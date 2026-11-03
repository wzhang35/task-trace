import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('task_trace', ROOT / 'task_trace.py')
tt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tt)


def native_bytes(call=False):
    records = [{'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'text': 'synthetic question'}]}},
               {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'text': 'synthetic answer'}]}}]
    if call:
        records.append({'type': 'response_item', 'payload': {'type': 'function_call', 'call_id': 'missing'}})
    return b''.join(tt.encoded(r) + b'\n' for r in records)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.work = self.root / 'workspace'; self.work.mkdir()
        (self.work / 'data.csv').write_text('value\n1\n')
        self.a = tt.start(self.work, self.root / 'archives', 'Synthetic <study>', 1)
        self.native = self.root / 'native.jsonl'
        self.native.write_bytes(native_bytes())

    def tearDown(self):
        self.tmp.cleanup()

    def hook(self, name, **extra):
        tt.hook(self.work, dict(cwd=str(self.work), session_id='s1', transcript_path=str(self.native), hook_event_name=name, **extra))

    def complete(self):
        for name in ('SessionStart', 'UserPromptSubmit', 'Stop'):
            self.hook(name)
        return tt.finish(self.a)

    def codes(self, report):
        return {g['code'] for g in report['gaps']}

    def test_file_versions_deletion_and_exact_native_bytes(self):
        first = self.a.rows()[1]['data']['files']['data.csv']['sha256']
        (self.work / 'data.csv').write_text('value\n2\n')
        self.hook('PreToolUse', tool_use_id='t')
        (self.work / 'data.csv').unlink()
        self.hook('PostToolUse', tool_use_id='t', tool_response='exit 1: expected synthetic failure')
        report = self.complete()
        self.assertEqual(report['capture_status'], 'closed_for_declared_scope')
        versions = [r['data']['files'] for r in self.a.rows() if r['kind']=='snapshot']
        self.assertTrue(any('data.csv' in v and v['data.csv']['sha256'] != first for v in versions))
        self.assertNotIn('data.csv', versions[-1])
        self.assertEqual((self.a.path/'objects'/first).read_bytes(), b'value\n1\n')
        saved = [r for r in self.a.rows() if r['kind']=='transcript'][-1]['data']
        self.assertEqual((self.a.path/'objects'/saved['sha256']).read_bytes(), self.native.read_bytes())

    def test_missing_return_and_subagent_are_gaps(self):
        self.hook('PreToolUse', tool_use_id='t')
        self.hook('SubagentStart', agent_id='child')
        report = self.complete()
        self.assertTrue({'tool_return_missing','subagent_stop_missing','session_transcript_missing'} <= self.codes(report))

    def test_child_transcript_is_preserved(self):
        child = self.root/'child.jsonl'; child.write_bytes(native_bytes())
        self.hook('SubagentStart', agent_id='child')
        self.hook('SubagentStop', agent_id='child', agent_transcript_path=str(child))
        report = self.complete()
        self.assertEqual(report['native_transcripts'], 2)
        self.assertFalse(report['gaps'])

    def test_native_open_call_and_torn_line(self):
        self.native.write_bytes(native_bytes(call=True))
        self.assertIn('native_tool_return_missing', self.codes(self.complete()))
        self.native.write_bytes(b'{')
        self.assertIn('incomplete_or_unknown_native_jsonl', self.codes(tt.finish(self.a)))

    def test_partial_interactive_capture_cannot_pass(self):
        self.hook('PreToolUse', tool_use_id='t')
        self.hook('PostToolUse', tool_use_id='t')
        self.assertIn('interactive_session_boundaries_missing', self.codes(tt.finish(self.a)))

    def test_sensitive_files_symlinks_and_oversize(self):
        (self.work/'.env').write_text('SYNTHETIC_SECRET=not-real')
        (self.work/'external').symlink_to(self.native)
        (self.work/'large').write_bytes(b'x'*(1024**2+1))
        report=self.complete()
        snaps=[r for r in self.a.rows() if r['kind']=='snapshot']
        files=snaps[-1]['data']['files']
        self.assertNotIn('.env',files);self.assertNotIn('external',files);self.assertNotIn('large',files)
        self.assertIn('file_not_captured',self.codes(report))
        self.assertTrue(any(x['path']=='.env' for x in report['exclusions']))

    def test_integrity_detects_object_and_log_corruption(self):
        self.complete()
        h=self.a.rows()[1]['data']['files']['data.csv']['sha256']
        (self.a.path/'objects'/h).write_bytes(b'changed')
        self.assertEqual(tt.verify(self.a)['integrity'],'failed')
        p=self.a.path/'events.jsonl'
        p.write_bytes(p.read_bytes()+b'{')
        self.assertEqual(tt.verify(self.a)['capture_status'],'invalid_archive')

    def test_activity_invalidates_stale_html_and_report(self):
        self.complete()
        self.assertTrue((self.a.path/'index.html').exists())
        self.hook('UserPromptSubmit')
        self.assertFalse((self.a.path/'index.html').exists())
        report=tt.verify(self.a)
        self.assertEqual(report['capture_status'],'recording_or_needs_finish')
        self.assertIn('interactive_turn_unfinished',self.codes(report))

    def test_concurrent_hooks_do_not_lose_events(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda i:self.hook('PreCompact', test_index=i),range(12)))
        recorded=[r['data']['test_index'] for r in self.a.rows() if r['kind']=='hook']
        self.assertEqual(sorted(recorded),list(range(12)))
        self.assertEqual(tt.verify(self.a)['integrity'],'passed')

    def test_hook_install_is_idempotent_and_preserves_existing(self):
        d=self.work/'.codex';d.mkdir()
        original={'hooks':{'Stop':[{'hooks':[{'type':'command','command':'echo existing'}]}]}}
        (d/'hooks.json').write_text(json.dumps(original))
        tt.install_hooks(self.work)
        first=tt.load(d/'hooks.json')
        tt.install_hooks(self.work)
        self.assertEqual(first,tt.load(d/'hooks.json'))
        self.assertEqual(first['hooks']['Stop'][0],original['hooks']['Stop'][0])
        self.assertEqual(len(first['hooks']['Stop']),2)

    def test_html_escapes_untrusted_content(self):
        self.hook('UserPromptSubmit', prompt='<script>alert(1)</script>')
        self.complete()
        html=(self.a.path/'index.html').read_text()
        self.assertNotIn('<script>',html)
        self.assertIn('&lt;script&gt;',html)

    def test_archive_cannot_be_inside_workspace(self):
        other=self.root/'other';other.mkdir()
        with self.assertRaises(ValueError):tt.start(other,other/'archive','test',1)

    def test_unrelated_resume_refused(self):
        with self.assertRaisesRegex(ValueError,'already captured'):
            tt.capture_run(self.a,'test','unavailable-codex',resume='unrelated')

    def test_exec_two_turns_with_fake_transport(self):
        # Explicitly synthetic protocol fixture, not proof of live Codex compatibility.
        home=self.root/'codex-home';sessions=home/'sessions';sessions.mkdir(parents=True)
        (sessions/'rollout-fixture-session.jsonl').write_bytes(native_bytes())
        fake=self.root/'fake-codex'
        fake.write_text('#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\nif "--version" in sys.argv:\n print("synthetic-test-fixture");sys.exit(0)\nprompt=sys.stdin.read()\nPath("answer.txt").write_text(prompt)\nfor x in [{"type":"thread.started","thread_id":"fixture-session"},{"type":"item.completed","item":{"type":"agent_message","text":"Synthetic answer"}},{"type":"turn.completed"}]:print(json.dumps(x),flush=True)\n')
        fake.chmod(0o700)
        with patch.dict(os.environ,{'CODEX_HOME':str(home)}):
            r1=tt.capture_run(self.a,'first',str(fake),timeout=10)
            r2=tt.capture_run(self.a,'second',str(fake),resume=r1['session_id'],timeout=10)
        self.assertEqual(r2['exit_code'],0)
        report=tt.finish(self.a)
        self.assertFalse(report['gaps'])
        self.assertEqual(report['runs'],2)
        prompts=[r['data']['prompt'] for r in self.a.rows() if r['kind']=='run_start']
        self.assertEqual(prompts,['first','second'])
        self.assertEqual((self.work/'answer.txt').read_text(),'second')

    def test_timeout_is_reported_not_success(self):
        fake=self.root/'fake-codex'
        fake.write_text('#!/usr/bin/env python3\nimport sys,time\nif "--version" in sys.argv:\n print("synthetic-test-fixture");sys.exit(0)\nsys.stdin.read()\ntime.sleep(10)\n')
        fake.chmod(0o700)
        result=tt.capture_run(self.a,'test',str(fake),timeout=0.1)
        self.assertTrue(result['interrupted'])
        report=tt.finish(self.a)
        self.assertIn('run_failed_or_interrupted',self.codes(report))
        self.assertEqual(report['capture_status'],'closed_with_gaps')

    def test_timeout_applies_after_stdout_closes(self):
        fake=self.root/'fake-codex'
        fake.write_text('#!/usr/bin/env python3\nimport os,sys,time\nif "--version" in sys.argv:\n print("synthetic-test-fixture");sys.exit(0)\nsys.stdin.read()\nos.close(1)\ntime.sleep(10)\n')
        fake.chmod(0o700)
        result=tt.capture_run(self.a,'test',str(fake),timeout=0.1)
        self.assertTrue(result['interrupted'])

    def test_invalid_event_shape_is_reported(self):
        (self.a.path/'events.jsonl').write_text('[]\n')
        self.assertEqual(tt.verify(self.a)['capture_status'],'invalid_archive')


if __name__ == '__main__':
    unittest.main()
