#!/usr/bin/env python3
"""Local, task-scoped Codex capture. Python standard library; macOS/Linux."""
import argparse
import contextlib
import datetime as dt
import fcntl
import fnmatch
import hashlib
import html
import json
import os
from pathlib import Path
import selectors
import shlex
import signal
import subprocess
import sys
import time
import uuid

VERSION = '0.1.0'
BINDING = '.task-trace.json'
EVENTS = ['SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse',
          'PreCompact', 'PostCompact', 'SubagentStart', 'SubagentStop', 'Stop',
          'Interrupt', 'SessionEnd']
SKIP_DIRS = {'.git', '.venv', 'node_modules', '__pycache__', '.ssh', '.aws', '.task-trace', 'archives'}
SKIP_FILES = {BINDING, '.DS_Store', 'auth.json', 'config.toml'}
SKIP_PATTERNS = ['.env*', '*.pem', '*.key', 'credentials*', '*.pyc']
LIMITS = [
    'Only the selected workspace and explicitly attached files are covered; URLs are not downloaded.',
    'Snapshots preserve observed boundaries, not every transient filesystem write between boundaries.',
    'Provider-hidden reasoning, unexposed system context and remote tool environments are not recoverable.',
    'Raw transcripts and tool output may contain secrets. Archives are private, not publication-ready.',
    'Hooks require Codex trust and version support; hosted tools may bypass local tool hooks.',
    'Filesystem hashes prove local consistency, not provider identity or expert acceptance.',
]

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def encoded(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()

def digest(b):
    return hashlib.sha256(b).hexdigest()

def inside(path, root):
    return path == root or root in path.parents

def write_json(p, x):
    p = Path(p)
    tmp = p.with_name(p.name + '.' + uuid.uuid4().hex + '.tmp')
    with open(tmp, 'xb') as f:
        os.chmod(tmp, 0o600)
        f.write(encoded(x) + b'\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)

def load(p):
    return json.loads(Path(p).read_text())

class Archive:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.config = load(self.path / 'task.json')
        self.workspace = Path(self.config['workspace'])

    @contextlib.contextmanager
    def lock(self):
        with open(self.path / '.lock', 'a') as f:
            os.chmod(f.name, 0o600)
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def rows(self):
        p = self.path / 'events.jsonl'
        rows = [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
        if any(not isinstance(r, dict) or not isinstance(r.get('data'), dict) or not isinstance(r.get('kind'), str) for r in rows):
            raise ValueError('invalid event record shape')
        return rows

    def append(self, kind, data):
        # Caller holds the lock; a torn write is surfaced by check, never silently repaired.
        rows = self.rows()
        for name in ('report.json', 'index.html'):
            (self.path / name).unlink(missing_ok=True)
        row = {'seq': len(rows), 'time': now(), 'kind': kind, 'data': data,
               'previous': rows[-1]['hash'] if rows else None}
        row['hash'] = digest(encoded(row))
        with open(self.path / 'events.jsonl', 'ab') as f:
            os.chmod(f.name, 0o600)
            f.write(encoded(row) + b'\n')
            f.flush()
            os.fsync(f.fileno())
        return row

    def blob(self, data):
        h = digest(data)
        p = self.path / 'objects' / h
        if not p.exists():
            with open(p, 'xb') as f:
                os.chmod(p, 0o600)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        return {'sha256': h, 'bytes': len(data)}

    def read_file(self, path, limit=True):
        path = Path(path)
        # Do not follow symlinks, including a final component replaced during the read.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            import stat
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError('not_regular_file')
            max_bytes = self.config['max_file_bytes'] if limit else 1024**3
            if before.st_size > max_bytes:
                raise ValueError('file_over_size_limit')
            with os.fdopen(fd, 'rb', closefd=False) as f:
                data = f.read(max_bytes + 1)
            after = os.fstat(fd)
            if len(data) > max_bytes:
                raise ValueError('file_over_size_limit')
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('file_changed_during_capture')
            return self.blob(data)
        finally:
            os.close(fd)

    def snapshot(self, reason):
        files, omitted = {}, []
        def walk_error(error):
            omitted.append({'path': str(error.filename), 'reason': str(error)})
        for base, dirs, names in os.walk(self.workspace, followlinks=False, onerror=walk_error):
            base = Path(base)
            for name in list(dirs):
                p = base / name
                if name in SKIP_DIRS or p.is_symlink():
                    dirs.remove(name)
                    omitted.append({'path': str(p.relative_to(self.workspace)),
                                    'reason': 'symlink' if p.is_symlink() else 'excluded_directory'})
            for name in sorted(names):
                p = base / name
                rel = str(p.relative_to(self.workspace))
                if name in SKIP_FILES or any(fnmatch.fnmatch(name, x) for x in SKIP_PATTERNS):
                    omitted.append({'path': rel, 'reason': 'excluded_file'})
                    continue
                try:
                    files[rel] = self.read_file(p)
                except (OSError, ValueError) as e:
                    omitted.append({'path': rel, 'reason': str(e)})
        return self.append('snapshot', {'reason': reason, 'files': files, 'omitted': omitted})

    def transcript(self, path, session_id=None, relation='main'):
        try:
            ref = self.read_file(path, limit=False)
            # Preserve original bytes even when the live transcript ends mid-record.
            self.append('transcript', {'path': str(path), 'session_id': session_id,
                                       'relation': relation, **ref})
        except (OSError, ValueError) as e:
            self.append('gap', {'code': 'transcript_unavailable', 'path': str(path), 'detail': str(e)})


def bound(workspace):
    return Archive(load(Path(workspace).resolve() / BINDING)['archive'])

def start(workspace, store, title, max_mb):
    workspace, store = Path(workspace).resolve(), Path(store).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError('workspace must exist')
    if inside(store, workspace):
        raise ValueError('archive store must be outside the captured workspace')
    if (workspace / BINDING).exists():
        raise ValueError('workspace already bound; use its current task or an independent workspace')
    task_id = uuid.uuid4().hex
    p = store / task_id
    p.mkdir(parents=True, mode=0o700)
    os.chmod(p, 0o700)
    (p / 'objects').mkdir(mode=0o700)
    (p / 'raw').mkdir(mode=0o700)
    write_json(p / 'task.json', {'schema': 1, 'id': task_id, 'title': title,
                               'workspace': str(workspace), 'created': now(),
                               'collector_version': VERSION, 'max_file_bytes': int(max_mb * 1024**2),
                               'platform': sys.platform, 'python': sys.version.split()[0]})
    write_json(workspace / BINDING, {'archive': str(p)})
    a = Archive(p)
    with a.lock():
        a.append('start', {'declared_scope': 'workspace plus explicit attachments', 'limitations': LIMITS})
        a.snapshot('initial')
    return a


def install_hooks(workspace):
    a = bound(workspace)
    target = a.workspace / '.codex' / 'hooks.json'
    target.parent.mkdir(exist_ok=True)
    data = load(target) if target.exists() else {}
    if not isinstance(data, dict) or not isinstance(data.get('hooks', {}), dict):
        raise ValueError('existing hook configuration has unexpected shape')
    command = shlex.join([sys.executable, str(Path(__file__).resolve()), 'hook', '--workspace', str(a.workspace)])
    hooks = data.setdefault('hooks', {})
    for name in EVENTS:
        groups = hooks.setdefault(name, [])
        if not isinstance(groups, list):
            raise ValueError('existing hook event is not a list')
        if not any(h.get('command') == command for g in groups for h in g.get('hooks', [])):
            groups.append({'hooks': [{'type': 'command', 'command': command,
                                      'timeout': 3 if name in ('Interrupt', 'SessionEnd') else 60}]})
    with a.lock():
        if target.exists():
            a.append('hook_config_backup', {'path': str(target), **a.read_file(target)})
        write_json(target, data)
        a.append('hooks_installed', {'path': str(target), 'command': command,
                                     'trust': 'Review in Codex /hooks or Settings > Hooks before use'})
    return target


def hook(workspace, data):
    a = bound(workspace)
    cwd = Path(data.get('cwd', '')).resolve()
    if not inside(cwd, a.workspace):
        raise ValueError('hook cwd is outside the bound workspace')
    event = data.get('hook_event_name')
    with a.lock():
        a.append('hook', data)
        for field, relation in [('transcript_path', 'main'), ('agent_transcript_path', 'subagent')]:
            if data.get(field):
                a.transcript(data[field], data.get('agent_id') if relation == 'subagent' else data.get('session_id'), relation)
        # Short shutdown hooks leave file collection to explicit finish; never delay termination.
        if event not in ('SessionEnd', 'Interrupt'):
            a.snapshot(event or 'unknown_hook')


def capture_run(a, prompt, codex, model=None, resume=None, timeout=600):
    run_id = uuid.uuid4().hex
    run_dir = a.path / 'raw' / run_id
    run_dir.mkdir(mode=0o700)
    if resume and resume not in {r['data'].get('session_id') for r in a.rows() if r['kind'] == 'run_end'}:
        raise ValueError('resume must reference a session already captured in this task')
    command = [codex, 'exec', '--sandbox', 'workspace-write', '--skip-git-repo-check',
               '--json', '--ignore-user-config']
    if model:
        command += ['--model', model]
    if resume:
        command += ['resume', resume, '--skip-git-repo-check']
    command += ['-']
    version = subprocess.run([codex, '--version'], capture_output=True, text=True, timeout=10)
    with a.lock():
        a.append('run_start', {'run_id': run_id, 'prompt': prompt, 'command': command,
                               'codex_version': version.stdout.strip(), 'resume': resume,
                               'note': 'User config ignored; CLI sandbox and provider defaults still apply.'})
        a.snapshot('before_run')
    stderr_path = run_dir / 'stderr.log'
    stdout_path = run_dir / 'events.jsonl'
    sid = None
    interrupted = False
    runner_error = None
    with open(stderr_path, 'wb') as err, open(stdout_path, 'wb') as out:
        os.chmod(stderr_path, 0o600); os.chmod(stdout_path, 0o600)
        proc = subprocess.Popen(command, cwd=a.workspace, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=err, start_new_session=True)
        selector = selectors.DefaultSelector(); selector.register(proc.stdout, selectors.EVENT_READ)
        buffer = b''; deadline = time.monotonic() + timeout
        try:
            proc.stdin.write(prompt.encode()); proc.stdin.close()
            while selector.get_map():
                if time.monotonic() > deadline:
                    interrupted = True
                    break
                for key, _ in selector.select(timeout=.25):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        break
                    out.write(chunk); out.flush()
                    buffer += chunk
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        try:
                            event = json.loads(line)
                            if not isinstance(event, dict) or not isinstance(event.get('type'), str):
                                raise ValueError('unexpected event shape')
                        except ValueError:
                            with a.lock():a.append('gap', {'code': 'invalid_exec_event', 'run_id': run_id})
                            continue
                        if event.get('type') == 'thread.started':sid = event.get('thread_id')
                        with a.lock():
                            a.append('exec_event', {'run_id': run_id, 'event': event})
                            if event.get('type') in ('item.completed', 'turn.completed', 'turn.failed'):
                                a.snapshot(event['type'])
        except KeyboardInterrupt:
            interrupted = True
        except Exception as e:
            interrupted = True
            runner_error = str(e)
        finally:
            selector.close()
            if not interrupted:
                try:
                    proc.wait(timeout=max(0, deadline-time.monotonic()))
                except subprocess.TimeoutExpired:
                    interrupted = True
            if proc.poll() is None and interrupted:
                os.killpg(proc.pid, signal.SIGTERM)
                try:proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL);proc.wait()
            remainder = proc.stdout.read()
            proc.stdout.close()
            if not proc.stdin.closed:proc.stdin.close()
            out.write(remainder);out.flush();os.fsync(out.fileno())
            if buffer or remainder:
                with a.lock():a.append('gap', {'code': 'unparsed_exec_tail', 'run_id': run_id})
    with a.lock():
        if runner_error:a.append('gap', {'code':'runner_error', 'detail':runner_error, 'run_id':run_id})
        a.append('run_end', {'run_id': run_id, 'session_id': sid or resume,
                             'exit_code': proc.returncode, 'interrupted': interrupted,
                             'stdout': a.read_file(stdout_path, False), 'stderr': a.read_file(stderr_path, False)})
        if sid or resume:
            home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
            found = list((home / 'sessions').rglob('rollout-*' + (sid or resume) + '.jsonl'))
            if found:
                for p in found:a.transcript(p, sid or resume)
            else:a.append('gap', {'code': 'native_transcript_not_found', 'session_id': sid or resume})
        a.snapshot('after_run')
    return {'archive': str(a.path), 'session_id': sid or resume, 'exit_code': proc.returncode, 'interrupted': interrupted}


def verify(a):
    problems, rows = [], []
    try:rows = a.rows()
    except (ValueError, OSError) as e:problems.append('invalid_event_log: ' + str(e))
    previous = None
    for i, row in enumerate(rows):
        r = dict(row); h = r.pop('hash', None)
        if h != digest(encoded(r)) or r.get('previous') != previous or r.get('seq') != i:
            problems.append('event_chain_mismatch:' + str(i))
        previous = h
    refs = set()
    def visit(x):
        if isinstance(x, dict):
            if 'sha256' in x and 'bytes' in x and isinstance(x['sha256'], str):refs.add(x['sha256'])
            for v in x.values():visit(v)
        elif isinstance(x, list):
            for v in x:visit(v)
    for row in rows:
        d=row['data']
        if row['kind']=='snapshot':visit(d['files'])
        elif row['kind'] in ('transcript','attachment','hook_config_backup'):visit(d)
        elif row['kind']=='run_end':
            visit(d.get('stdout',{}));visit(d.get('stderr',{}))
    for h in sorted(refs):
        if len(h) != 64 or any(c not in '0123456789abcdef' for c in h):
            problems.append('invalid_object_reference');continue
        p = a.path / 'objects' / h
        if not p.is_file() or digest(p.read_bytes()) != h:problems.append('object_missing_or_changed:' + h)
    gaps = [r['data'] for r in rows if r['kind'] == 'gap']
    snapshots = [r for r in rows if r['kind'] == 'snapshot']
    excluded = [x for r in snapshots for x in r['data']['omitted']]
    for x in excluded:
        if x['reason'] not in ('excluded_directory', 'excluded_file'):
            gaps.append({'code': 'file_not_captured', **x})
    starts = {r['data']['run_id'] for r in rows if r['kind'] == 'run_start'}
    ends = {r['data']['run_id']:r['data'] for r in rows if r['kind'] == 'run_end'}
    if starts - ends.keys():gaps.append({'code': 'unfinished_run'})
    for rid, e in ends.items():
        if e['exit_code'] or e['interrupted']:gaps.append({'code': 'run_failed_or_interrupted', 'run_id': rid})
        ev = [r['data']['event']['type'] for r in rows if r['kind']=='exec_event' and r['data']['run_id']==rid]
        if 'turn.completed' not in ev:gaps.append({'code': 'turn_completion_missing', 'run_id': rid})
    pre, post, substart, substop = set(), set(), set(), set()
    hooks = [r['data'] for r in rows if r['kind']=='hook']
    for d in hooks:
        key=(d.get('session_id'), d.get('tool_use_id'))
        if d.get('hook_event_name')=='PreToolUse':pre.add(key)
        if d.get('hook_event_name')=='PostToolUse':post.add(key)
        if d.get('hook_event_name')=='SubagentStart':substart.add(d.get('agent_id'))
        if d.get('hook_event_name')=='SubagentStop':substop.add(d.get('agent_id'))
    for k in pre-post:gaps.append({'code':'tool_return_missing','call':list(k)})
    for k in substart-substop:gaps.append({'code':'subagent_stop_missing','agent_id':k})
    if hooks and not starts:
        for sid in {d.get('session_id') for d in hooks}:
            session_hooks=[d for d in hooks if d.get('session_id')==sid]
            names=[d.get('hook_event_name') for d in session_hooks]
            if 'SessionStart' not in names or 'UserPromptSubmit' not in names or 'Stop' not in names:
                gaps.append({'code':'interactive_session_boundaries_missing','session_id':sid})
            elif names.index('UserPromptSubmit') < names.index('SessionStart') or max(i for i,n in enumerate(names) if n=='UserPromptSubmit') > max(i for i,n in enumerate(names) if n=='Stop'):
                gaps.append({'code':'interactive_turn_unfinished','session_id':sid})
    transcripts={r['data']['path']:r['data'] for r in rows if r['kind']=='transcript'}
    captured_sids={t.get('session_id') for t in transcripts.values()}
    for sid in {e.get('session_id') for e in ends.values()} | {d.get('session_id') for d in hooks} | substart:
        if sid and sid not in captured_sids:gaps.append({'code':'session_transcript_missing','session_id':sid})
    for t in transcripts.values():
        p=a.path/'objects'/t['sha256']
        if not p.is_file():continue
        try:
            native=[json.loads(line) for line in p.read_bytes().splitlines()]
            if not native:raise ValueError('empty transcript')
            calls, returns=set(), set()
            user_seen=assistant_seen=False
            for record in native:
                payload=record.get('payload', {})
                if record.get('type')=='response_item':
                    typ=payload.get('type')
                    if typ in ('function_call','custom_tool_call'):calls.add(payload.get('call_id'))
                    if typ in ('function_call_output','custom_tool_call_output'):returns.add(payload.get('call_id'))
                    if typ=='message':
                        user_seen |= payload.get('role')=='user'
                        assistant_seen |= payload.get('role')=='assistant'
            if calls-returns:gaps.append({'code':'native_tool_return_missing','path':t['path'],'calls':sorted(str(x) for x in calls-returns)})
            if not user_seen or not assistant_seen:gaps.append({'code':'native_conversation_not_recognized','path':t['path']})
        except (ValueError, AttributeError):gaps.append({'code':'incomplete_or_unknown_native_jsonl','path':t['path']})
    if not transcripts:gaps.append({'code':'no_native_transcript'})
    if not starts and not hooks:gaps.append({'code':'no_codex_activity'})
    finished = bool(rows and rows[-1]['kind']=='finish')
    report={'schema':1, 'task_id':a.config['id'], 'title':a.config['title'],
            'integrity':'passed' if not problems else 'failed', 'integrity_errors':problems,
            'capture_status':'invalid_archive' if problems else 'closed_with_gaps' if finished and gaps else 'closed_for_declared_scope' if finished else 'recording_or_needs_finish',
            'semantic_success':'not_assessed', 'events':len(rows), 'objects_checked':len(refs),
            'snapshots':len(snapshots), 'runs':len(starts), 'native_transcripts':len(transcripts),
            'gaps':gaps, 'exclusions':list({encoded(x):x for x in excluded}.values()),
            'limitations':LIMITS, 'verified_through_hash':previous}
    return report


def render(a, report):
    esc = html.escape
    parts=['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Task Trace</title><style>body{max-width:960px;margin:40px auto;padding:0 20px;font:16px/1.7 system-ui;color:#20362d;background:#f7f8f5}pre{white-space:pre-wrap;overflow-wrap:anywhere;padding:14px;background:#edf1eb;max-height:500px;overflow:auto}details{background:white;padding:14px;margin:12px 0;border:1px solid #dbe3d9;border-radius:6px}summary{cursor:pointer}small{color:#637366}h2{margin-top:36px}a{color:#246149}</style><h1>'+esc(a.config['title'])+'</h1><p>Private task archive · '+esc(report['capture_status'])+' · integrity '+report['integrity']+'</p><p>Capture coverage is not a judgment of task success. Open report.json for gaps and limitations.</p><h2>Task timeline</h2>']
    for row in a.rows():
        kind,d=row['kind'],row['data']
        if kind=='snapshot':
            content={'files':d['files'],'omitted':d['omitted']};label='Files · '+d['reason']+' · '+str(len(d['files']))+' captured'
        elif kind=='run_start':label='User prompt';content=d['prompt']
        elif kind=='exec_event':
            ev=d['event'];item=ev.get('item',{});label=ev.get('type','event')+' · '+item.get('type','');content=ev
        elif kind=='hook':label='Codex hook · '+d.get('hook_event_name','unknown');content=d
        else:label=kind;content=d
        text=content if isinstance(content,str) else json.dumps(content,ensure_ascii=False,indent=2)
        links=''
        if kind=='snapshot':
            links='<ul>'+''.join('<li><a href="objects/'+esc(ref['sha256'],quote=True)+'" download="'+esc(Path(name).name,quote=True)+'">'+esc(name)+'</a></li>' for name,ref in d['files'].items())+'</ul>'
        elif kind in ('attachment','transcript'):
            links='<p><a href="objects/'+esc(d['sha256'],quote=True)+'" download="'+esc(Path(d.get('source',d.get('path','original'))).name,quote=True)+'">Download original bytes</a></p>'
        parts.append('<details><summary>'+str(row['seq'])+' · '+esc(label)+' <small>'+esc(row['time'])+'</small></summary>'+links+'<pre>'+esc(text)+'</pre></details>')
    parts.append('<h2>Coverage report</h2><pre>'+esc(json.dumps(report,ensure_ascii=False,indent=2))+'</pre></html>')
    p=a.path/'index.html';p.write_text(''.join(parts));os.chmod(p,0o600)


def finish(a):
    with a.lock():
        latest={r['data']['path']:r['data'] for r in a.rows() if r['kind']=='transcript'}
        for path,d in latest.items():a.transcript(path,d.get('session_id'),d.get('relation','main'))
        a.snapshot('finish')
        a.append('finish', {'note':'Subsequent captured activity invalidates this closed status; finish again after the last turn.'})
        report=verify(a)
        write_json(a.path/'report.json', report)
        render(a,report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=VERSION)
    sub=parser.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('start');p.add_argument('--workspace',required=True);p.add_argument('--store',required=True);p.add_argument('--title',required=True);p.add_argument('--max-file-mb',type=float,default=50)
    for cmd in ['install-hooks','hook','checkpoint','attach','run','finish','check']:
        p=sub.add_parser(cmd);p.add_argument('--workspace',default='.')
        if cmd=='checkpoint':p.add_argument('--label',default='manual')
        if cmd=='attach':p.add_argument('file');p.add_argument('--label',required=True)
        if cmd=='run':
            p.add_argument('--prompt-file',required=True);p.add_argument('--codex',default='codex');p.add_argument('--model');p.add_argument('--resume');p.add_argument('--timeout',type=int,default=600)
    args=parser.parse_args();os.umask(0o077)
    if args.cmd=='hook':
        try:hook(args.workspace,json.load(sys.stdin));print('{}')
        except Exception as e:
            # Hooks are observers; do not block, rewrite tools, or inject model instructions.
            print(json.dumps({'systemMessage':'Task Trace capture failed: '+str(e)}))
            return 1
        return 0
    if args.cmd=='start':
        if args.max_file_mb<=0:raise ValueError('max-file-mb must be positive')
        a=start(args.workspace,args.store,args.title,args.max_file_mb);result={'task_id':a.config['id'],'archive':str(a.path)}
    elif args.cmd=='install-hooks':result={'hooks':str(install_hooks(args.workspace)),'next':'Review and trust these hooks in Codex; restart or resume the session.'}
    else:
        a=bound(args.workspace)
        if args.cmd=='run':
            result=capture_run(a,Path(args.prompt_file).read_text(),args.codex,args.model,args.resume,args.timeout)
        elif args.cmd=='checkpoint':
            with a.lock():result=a.snapshot(args.label)
        elif args.cmd=='attach':
            p=Path(args.file).absolute()
            with a.lock():result=a.append('attachment',{'label':args.label,'source':str(p),**a.read_file(p)})
        elif args.cmd=='finish':result=finish(a)
        elif args.cmd=='check':
            with a.lock():result=verify(a)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if args.cmd=='check' and (result['integrity']!='passed' or result['gaps'] or result['capture_status']!='closed_for_declared_scope'):return 1
    if args.cmd=='run' and (result['exit_code'] or result['interrupted']):return 1
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except (OSError,ValueError,KeyError) as exc:
        print('Task Trace: '+str(exc),file=sys.stderr);sys.exit(1)
