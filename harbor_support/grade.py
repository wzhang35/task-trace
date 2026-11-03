#!/usr/bin/env python3
"""Binary, weighted artifact grading. Never execute or import agent outputs."""
import argparse
import hashlib
import json
import math
from pathlib import Path

MAX_BYTES = 8 * 1024 * 1024


def read_artifact(root, name):
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise ValueError('artifact missing or not a regular file: ' + name)
    if root.resolve() not in path.resolve().parents:
        raise ValueError('artifact leaves workspace')
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('artifact exceeds grading limit')
    return path.read_bytes()


def grade(rubric, root):
    root = Path(root)
    results = []
    for criterion in rubric['criteria']:
        check = criterion['check']
        passed = None
        reason = ''
        try:
            raw = read_artifact(root, check['file'])
            if check['type'] in ('json_number', 'json_equal'):
                value = json.loads(raw, parse_constant=lambda s: (_ for _ in ()).throw(ValueError('non-finite JSON')))
                for key in check['path']:
                    value = value[key]
                if check['type'] == 'json_number':
                    passed = type(value) in (int, float) and math.isfinite(value) and math.isclose(value, check['expected'], rel_tol=0, abs_tol=check['abs_tol'])
                else:
                    passed = type(value) is type(check['expected']) and value == check['expected']
                reason = 'value matches the declared check' if passed else 'value does not match the declared check'
            elif check['type'] == 'file_present':
                passed = bool(raw)
                reason = 'nonempty deliverable present; content correctness is not assessed by this item' if passed else 'empty deliverable'
            elif check['type'] == 'manual':
                h = hashlib.sha256(raw).hexdigest()
                matches = [a for a in rubric.get('assessments', []) if a['criterion_id'] == criterion['id'] and a['artifact_sha256'] == h]
                if matches:
                    assessment = matches[0]
                    passed = assessment['passed']
                    reason = assessment['reason']
                else:
                    reason = 'requires a new assessment of this exact artifact; not scored as failure'
            else:
                raise ValueError('unsupported check type')
        except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError, RecursionError) as exc:
            passed, reason = False, str(exc)
        results.append({'id': criterion['id'], 'description': criterion['description'],
                        'max_points': criterion['points'], 'passed': passed,
                        'points': None if passed is None else criterion['points'] if passed else 0,
                        'reason': reason})
    pending = any(r['passed'] is None for r in results)
    known = sum(r['points'] or 0 for r in results)
    return {'status': 'pending_review' if pending else 'scored', 'total_points': rubric['total_points'],
            'score': None if pending else known, 'known_points': known,
            'reward': None if pending else known / rubric['total_points'], 'criteria': results,
            'note': 'Mechanical checks and artifact-bound human/curator assessments are distinct. No expert certification is inferred.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rubric', default='/tests/rubric.json')
    parser.add_argument('--workspace', default='/app')
    parser.add_argument('--logs', default='/logs/verifier')
    args = parser.parse_args()
    logs = Path(args.logs); logs.mkdir(parents=True, exist_ok=True)
    for name in ('reward.txt', 'reward.json'):
        (logs / name).unlink(missing_ok=True)
    result = grade(json.loads(Path(args.rubric).read_text()), args.workspace)
    (logs / 'scorecard.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    if result['reward'] is None:
        print('Pending artifact review; no numerical reward emitted.')
        return 2
    (logs / 'reward.txt').write_text(str(result['reward']) + '\n')
    print(json.dumps({'score': result['score'], 'total_points': result['total_points']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
