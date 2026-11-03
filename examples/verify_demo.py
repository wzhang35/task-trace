#!/usr/bin/env python3
"""Verify the synthetic demo archive without executing captured code or reading live logs."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def verify(root):
    root = Path(root)
    rows = [json.loads(line) for line in (root / 'events.jsonl').read_text().splitlines()]
    report = json.loads((root / 'report.json').read_text())
    assert report['integrity'] == 'passed' and not report['gaps'], 'run collector check first'
    assert report['capture_status'] == 'closed_for_declared_scope', 'archive is not closed'
    assert rows[-1]['hash'] == report['verified_through_hash'], 'report is stale'
    starts = [r for r in rows if r['kind'] == 'run_start']
    ends = [r for r in rows if r['kind'] == 'run_end']
    assert len(starts) == len(ends) == 2, 'expected two turns'
    assert ends[0]['data']['session_id'] == ends[1]['data']['session_id'] == starts[1]['data']['resume'], 'resume linkage missing'
    assert all(not r['data']['exit_code'] and not r['data']['interrupted'] for r in ends)
    snapshots = [r for r in rows if r['kind'] == 'snapshot']
    initial, final = snapshots[0]['data']['files'], snapshots[-1]['data']['files']
    assert 'protocol.txt' not in initial and 'protocol.txt' in final, 'late input mislabeled'
    assert {'study.csv', 'protocol.txt', 'analysis.py', 'results.json', 'report.md'} <= final.keys()
    attachments = [r for r in rows if r['kind'] == 'attachment']
    assert len(attachments) == 1 and ends[0]['seq'] < attachments[0]['seq'] < starts[1]['seq']
    def blob(ref):
        data = (root / 'objects' / ref['sha256']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == ref['sha256']
        return data
    versions = {r['data']['files']['results.json']['sha256']: r['data']['files']['results.json'] for r in snapshots if 'results.json' in r['data']['files']}
    assert len(versions) >= 2, 'earlier result version missing'
    old = [json.loads(blob(ref)) for ref in versions.values() if 'standardized_difference' not in json.loads(blob(ref))]
    assert old, 'first-turn result is missing'
    result = json.loads(blob(final['results.json']))
    expected = {'pooled_difference': -283 / 4200, 'standardized_difference': 41 / 1440}
    for key, value in expected.items():
        assert math.isclose(result[key], value, abs_tol=1e-12), (key, result[key])
    for key, value in {'A': 1 / 30, 'B': 17 / 720}.items():
        assert math.isclose(result['stratum_differences'][key], value, abs_tol=1e-12)
    for name in ('analysis.py', 'report.md'):
        assert len({r['data']['files'][name]['sha256'] for r in snapshots if name in r['data']['files']}) >= 2, name + ' earlier version missing'
    native = [r['data'] for r in rows if r['kind'] == 'transcript'][-1]
    messages = [json.loads(line) for line in blob(native).splitlines()]
    user_text = '\n'.join(c.get('text', '') for r in messages if r.get('type') == 'response_item' and r.get('payload', {}).get('role') == 'user' for c in r['payload'].get('content', []) if isinstance(c, dict))
    for start in starts:
        assert start['data']['prompt'].strip() in user_text, 'original question missing from native transcript'
    commands = [r for r in rows if r['kind'] == 'exec_event' and r['data']['event'].get('type') == 'item.completed' and r['data']['event'].get('item', {}).get('type') == 'command_execution']
    assert commands, 'no tool results captured'
    return {'demo': 'passed', 'turns': 2, 'snapshots': len(snapshots), 'tool_results': len(commands), 'result_versions': len(versions), 'expected_results': expected}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive')
    print(json.dumps(verify(parser.parse_args().archive), indent=2))
