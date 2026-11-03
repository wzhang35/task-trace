#!/usr/bin/env python3
"""Independent Task Trace v1 archive -> curated Harbor 0.25 multi-step task converter."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile

VERSION = '0.1.0'
SUPPORT = Path(__file__).resolve().parent / 'harbor_support'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(), parse_constant=lambda s: (_ for _ in ()).throw(ValueError('non-finite JSON: ' + s)))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def relative(value):
    require(isinstance(value, str) and value and '\\' not in value and '\x00' not in value, 'invalid relative path')
    p = PurePosixPath(value)
    require(not p.is_absolute() and all(x not in ('', '.', '..') for x in value.split('/')), 'unsafe relative path: ' + value)
    require(all(re.fullmatch(r'[A-Za-z0-9_.-]+', x) for x in p.parts), 'use portable path segments: ' + value)
    require(p.parts[0] not in ('.git', '.codex', '.ssh', '.aws', '.task-trace.json'), 'reserved path: ' + value)
    return value


class Source:
    """Reads only explicit archived objects; never follows original machine paths."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        for name in ('task.json', 'events.jsonl', 'report.json', 'objects'):
            require(not (self.root / name).is_symlink(), 'symlinked archive component: ' + name)
        self.task = read_json(self.root / 'task.json')
        require(self.task.get('schema') == 1, 'only Task Trace archive schema 1 is supported')
        self.rows = [json.loads(line) for line in (self.root / 'events.jsonl').read_text().splitlines()]
        require(self.rows, 'empty archive')
        previous = None
        for i, row in enumerate(self.rows):
            require(isinstance(row, dict), 'invalid event shape')
            unsigned = {k: v for k, v in row.items() if k != 'hash'}
            require(row.get('seq') == i and row.get('previous') == previous and sha(encoded(unsigned)) == row.get('hash'), 'archive event chain failed at ' + str(i))
            previous = row['hash']
        self.head = previous
        self.report = read_json(self.root / 'report.json')
        require(self.rows[-1]['kind'] == 'finish' and self.report.get('verified_through_hash') == previous, 'archive is active or report is stale; finish and check first')
        require(self.report.get('integrity') == 'passed' and self.report.get('capture_status') == 'closed_for_declared_scope' and not self.report.get('gaps'), 'archive reports capture gaps; resolve them before conversion')
        self.objects = {}
        for row in self.rows:
            d = row['data']; kind = row['kind']
            refs = list(d['files'].values()) if kind == 'snapshot' else [d] if kind in ('attachment', 'transcript', 'hook_config_backup') else [d['stdout'], d['stderr']] if kind == 'run_end' else []
            for ref in refs:
                self.verify_blob(ref)

    def verify_blob(self, ref):
        h = ref.get('sha256')
        require(isinstance(h, str) and re.fullmatch('[0-9a-f]{64}', h), 'invalid blob hash')
        if h not in self.objects:
            p = self.root / 'objects' / h
            require(not p.is_symlink() and p.is_file(), 'missing or symlinked object: ' + h)
            digest = hashlib.sha256(); size = 0
            with p.open('rb') as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b''):
                    digest.update(chunk); size += len(chunk)
            require(digest.hexdigest() == h and size == ref['bytes'], 'object integrity failed: ' + h)
            self.objects[h] = p
        require(self.objects[h].stat().st_size == ref['bytes'], 'inconsistent object size reference')
        return self.objects[h]

    def blob(self, ref):
        data = self.verify_blob(ref).read_bytes()
        require(sha(data) == ref['sha256'], 'object changed during export')
        return data

    def event(self, seq, kind):
        require(type(seq) is int and 0 <= seq < len(self.rows), 'invalid event sequence')
        row = self.rows[seq]
        require(row['kind'] == kind, 'expected ' + kind + ' at event ' + str(seq))
        return row

    def source_file(self, selection):
        kind = selection.get('kind')
        if kind == 'snapshot':
            row = self.event(selection['seq'], 'snapshot')
            ref = row['data']['files'][selection['path']]
        elif kind == 'attachment':
            row = self.event(selection['seq'], 'attachment')
            ref = row['data']
        else:
            raise ValueError('input source must be a snapshot or attachment')
        return self.blob(ref), {'source': selection, 'event_hash': row['hash'], 'sha256': ref['sha256']}


def draft(source):
    stages = []
    for row in source.rows:
        if row['kind'] != 'run_start':
            continue
        following = [r for r in source.rows[row['seq'] + 1:] if r['kind'] == 'snapshot' and r['data']['reason'] == 'after_run']
        stages.append({'id': 'turn-' + str(len(stages)+1), 'source_run': row['seq'],
                       'instruction': {'mode': 'historical'}, 'inputs': [], 'outputs': [],
                       'reference_snapshot': following[0]['seq'] if following else None,
                       'reference_review': '', 'rubric': {'total_points': 100, 'criteria': [], 'assessments': []}})
    require(stages, 'no captured CLI runs; hook-only archives need a future turn adapter')
    return {'schema': 1, 'archive_head': source.head, 'name': 'research-task', 'title': source.task['title'],
            'review': '', 'environment': {'profile': 'python-stdlib', 'image': 'python:3.12-slim', 'review': ''},
            'reward_strategy': 'mean', 'stages': stages,
            'draft_note': 'Review task boundaries, input timing, dependencies, references and binary scoring criteria. Empty review/rubric/outputs intentionally prevents export.'}


def rubric_check(rubric, outputs):
    total = rubric['total_points']
    require(type(total) is int and total > 0, 'total_points must be positive integer')
    criteria = rubric['criteria']
    require(criteria and sum(c['points'] for c in criteria) == total, 'rubric points must sum to total_points')
    ids = set()
    for c in criteria:
        require(isinstance(c['id'], str) and c['id'] not in ids, 'duplicate/invalid rubric criterion id')
        ids.add(c['id'])
        require(type(c['points']) is int and c['points'] > 0 and bool(c['description'].strip()), 'criterion needs positive integer points and a description')
        check = c['check']
        require(check['file'] in outputs, 'graded file must be a declared output')
        require(check['type'] in ('json_number', 'json_equal', 'file_present', 'manual'), 'unsupported check type')
        if check['type'] in ('json_number', 'json_equal'):
            require(isinstance(check['path'], list) and all(type(k) in (str, int) for k in check['path']), 'JSON path must be a key/index list')
        if check['type'] == 'json_number':
            require(all(type(check[k]) in (int, float) and math.isfinite(check[k]) for k in ('expected', 'abs_tol')) and check['abs_tol'] >= 0, 'numeric check needs finite expected and nonnegative abs_tol')
        if check['type'] == 'json_equal':
            require('expected' in check, 'missing expected value')
    seen = set()
    for a in rubric.get('assessments', []):
        c = next((c for c in criteria if c['id'] == a['criterion_id']), None)
        require(c is not None and c['check']['type'] == 'manual', 'assessment must name a manual criterion')
        require(re.fullmatch('[0-9a-f]{64}', a['artifact_sha256']) and type(a['passed']) is bool, 'assessment requires exact artifact hash and binary verdict')
        require(a.get('reviewer_role') in ('curator', 'expert') and bool(a.get('reviewer', '').strip()) and bool(a.get('reason', '').strip()), 'assessment requires reviewer identity, role and reason')
        key = (a['criterion_id'], a['artifact_sha256'])
        require(key not in seen, 'duplicate assessment for artifact/criterion')
        seen.add(key)


def prepare(source, recipe):
    require(recipe.get('schema') == 1 and recipe.get('archive_head') == source.head, 'recipe/archive version or head mismatch')
    require(isinstance(recipe.get('review'), str) and recipe['review'].strip(), 'recipe is still a draft: record the conversion review')
    require(re.fullmatch('[a-z0-9][a-z0-9-]{0,62}', recipe['name']), 'invalid task name')
    env = recipe['environment']
    require(env['profile'] == 'python-stdlib' and bool(env.get('review', '').strip()), 'MVP requires reviewed python-stdlib environment; do not pretend external tools are restored')
    require(isinstance(env['image'], str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_./:@-]+', env['image']), 'invalid container image reference')
    require(recipe['reward_strategy'] in ('mean', 'final'), 'reward strategy must be mean or final')
    require(recipe['stages'], 'at least one stage is required')
    files, mapping, ids, previous_run = {}, [], set(), -1
    all_outputs = {relative(p).casefold() for s in recipe['stages'] for p in s['outputs']}
    for stage in recipe['stages']:
        sid = stage['id']
        require(re.fullmatch('[a-z0-9][a-z0-9-]{0,62}', sid) and sid not in ids, 'duplicate/invalid stage id')
        ids.add(sid)
        row = source.event(stage['source_run'], 'run_start')
        require(row['seq'] > previous_run, 'stages must preserve source turn order')
        previous_run = row['seq']
        outputs = stage['outputs']
        require(outputs and len({p.casefold() for p in outputs}) == len(outputs), 'stage needs unique declared outputs')
        require(bool(stage.get('reference_review', '').strip()), 'reference output requires an explicit curator/expert review note')
        for name in outputs:
            relative(name)
        rubric_check(stage['rubric'], outputs)
        instruction = stage['instruction']
        if instruction['mode'] == 'historical':
            text = row['data']['prompt']
        else:
            require(instruction['mode'] == 'reconstructed' and instruction.get('reason') and instruction.get('text'), 'reconstructed instructions need text and reason')
            text = instruction['text']
        prefix = 'steps/' + sid + '/'
        files[prefix + 'instruction.md'] = text.encode()
        files[prefix + 'tests/rubric.json'] = encoded(stage['rubric']) + b'\n'
        files[prefix + 'tests/test.sh'] = b'#!/bin/sh\nset -eu\nexec python /tests/grade.py\n'
        occupied = set()
        for selected in stage['inputs']:
            destination = relative(selected['destination'])
            require(destination.casefold() not in all_outputs and destination.casefold() not in occupied, 'input/output collision or duplicate input: ' + destination)
            require(PurePosixPath(destination).name != 'setup.sh', 'executable step setup is outside this profile')
            occupied.add(destination.casefold())
            require(selected['source']['seq'] <= row['seq'], 'future source cannot be exposed to an earlier stage')
            data, evidence = source.source_file(selected['source'])
            files[prefix + 'workdir/' + destination] = data
            mapping.append({'stage': sid, 'role': 'input', 'destination': destination, **evidence})
        reference = source.event(stage['reference_snapshot'], 'snapshot')
        end = next((r for r in source.rows[row['seq']+1:] if r['kind'] == 'run_end' and r['data']['run_id'] == row['data']['run_id']), None)
        next_start = next((r['seq'] for r in source.rows[row['seq']+1:] if r['kind'] == 'run_start'), len(source.rows))
        require(end and not end['data']['exit_code'] and not end['data']['interrupted'] and end['seq'] <= reference['seq'] < next_start, 'reference snapshot must follow its completed run and precede the next run')
        for name in outputs:
            data, evidence = source.source_file({'kind': 'snapshot', 'seq': reference['seq'], 'path': name})
            files[prefix + 'solution/files/' + name] = data
            mapping.append({'stage': sid, 'role': 'reference', 'destination': name, **evidence})
        for criterion in stage['rubric']['criteria']:
            if criterion['check']['type'] == 'manual':
                ref_hash = reference['data']['files'][criterion['check']['file']]['sha256']
                require(any(a['criterion_id'] == criterion['id'] and a['artifact_sha256'] == ref_hash for a in stage['rubric'].get('assessments', [])), 'reference manual criterion is unassessed: ' + criterion['id'])
        # Paths are restricted portable relative paths; no shell interpolation from arbitrary text.
        files[prefix + 'solution/solve.sh'] = b'#!/bin/sh\nset -eu\ncp -R /solution/files/. /app/\n'
    config = ['schema_version = "1.4"', 'multi_step_reward_strategy = ' + json.dumps(recipe['reward_strategy']), '',
              '[metadata]', 'name = ' + json.dumps(recipe['name']), 'title = ' + json.dumps(recipe['title'], ensure_ascii=False),
              'status = "curated export; container validation must be run separately"', '',
              '[agent]', 'timeout_sec = 600.0', '', '[verifier]', 'timeout_sec = 60.0', 'environment_mode = "separate"', '',
              '[environment]', 'cpus = 1', 'memory_mb = 1024', 'storage_mb = 2048']
    for stage in recipe['stages']:
        config += ['', '[[steps]]', 'name = ' + json.dumps(stage['id']), 'artifacts = [']
        config += ['  { source = ' + json.dumps('/app/' + p) + ', destination = ' + json.dumps(p) + ' },' for p in stage['outputs']]
        config += [']']
    files['task.toml'] = ('\n'.join(config) + '\n').encode()
    files['environment/Dockerfile'] = ('FROM ' + env['image'] + '\nWORKDIR /app\n').encode()
    files['environment/docker-compose.yaml'] = b'services:\n  main:\n    network_mode: none\n'
    files['tests/grade.py'] = (SUPPORT / 'grade.py').read_bytes()
    return files, mapping


def export(source, recipe, destination, include_trace=False):
    files, mapping = prepare(source, recipe)
    destination = Path(destination).absolute()
    require(not destination.exists() and not destination.is_symlink(), 'destination already exists')
    require(source.root != destination and source.root not in destination.resolve().parents, 'destination must be outside source archive')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix='.' + destination.name + '-', dir=destination.parent))
    try:
        for name, data in files.items():
            p = temp / 'task' / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
            p.chmod(0o700 if p.suffix == '.sh' else 0o600)
        provenance = {'schema': 1, 'converter_version': VERSION, 'target': 'Harbor 0.25.0 / task schema 1.4',
                      'archive_head': source.head, 'archive_task_id': source.task['id'],
                      'task_files': {name: sha(data) for name, data in files.items()}, 'mapping': mapping,
                      'container_validation': 'not_run_by_exporter',
                      'limitations': ['Python standard-library local-file tasks only; no inference of MCP, remote services or missing dependencies.',
                                      'Oracle materializes reviewed reference artifacts; it does not rerun historical reasoning.',
                                      'Manual assessments apply only to exact artifact bytes; unfamiliar answers need new review.',
                                      'Historical trace is evidence, not automatically a successful training demonstration.']}
        (temp / 'provenance.json').write_bytes(encoded(provenance) + b'\n')
        (temp / 'recipe.json').write_bytes(encoded(recipe) + b'\n')
        if include_trace:
            trace = temp / 'evidence'; (trace / 'objects').mkdir(parents=True)
            for name in ('task.json', 'events.jsonl', 'report.json'):
                shutil.copyfile(source.root / name, trace / name)
            for h, path in source.objects.items():
                shutil.copyfile(path, trace / 'objects' / h)
        os.rename(temp, destination)
    except BaseException:
        shutil.rmtree(temp)
        raise
    return {'bundle': str(destination), 'task': str(destination / 'task'), 'stages': len(recipe['stages']),
            'task_files': len(files), 'included_trace': include_trace, 'validation': 'exported; run Harbor oracle and negative controls'}


def score_summary(recipe, trial):
    """Do not interpret Harbor's empty aggregate mean as a numerical score."""
    trial = Path(trial)
    stages = []
    for stage in recipe['stages']:
        path = trial / 'steps' / stage['id'] / 'verifier' / 'scorecard.json'
        if not path.is_file():
            stages.append({'id': stage['id'], 'status': 'not_scored', 'score': None})
            continue
        result = read_json(path)
        require(result['total_points'] == stage['rubric']['total_points'], 'scorecard/rubric total mismatch')
        require(result['status'] in ('scored', 'pending_review'), 'unknown scorecard status')
        if result['status'] == 'scored':
            require(type(result['score']) is int and 0 <= result['score'] <= result['total_points'], 'invalid stage score')
        else:
            require(result['score'] is None, 'pending review must not have a score')
        stages.append({'id': stage['id'], **{k: result[k] for k in ('status','score','total_points','known_points')}})
    pending = any(s['status'] == 'pending_review' for s in stages)
    complete = all(s['status'] == 'scored' for s in stages)
    status = 'pending_review' if pending else 'scored' if complete else 'incomplete'
    reward = None
    if complete:
        values = [s['score']/s['total_points'] for s in stages]
        reward = sum(values)/len(values) if recipe['reward_strategy'] == 'mean' else values[-1]
    return {'status': status, 'reward': reward, 'score_out_of_100': None if reward is None else 100*reward,
            'strategy': recipe['reward_strategy'], 'stages': stages,
            'note': 'Based on per-stage scorecards, not the Harbor job aggregate; missing review is not zero.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    for command in ('inspect', 'init', 'export'):
        p = sub.add_parser(command); p.add_argument('--archive', required=True)
        if command != 'inspect': p.add_argument('--out', required=True)
        if command == 'export':
            p.add_argument('--recipe', required=True); p.add_argument('--include-trace', action='store_true')
    p = sub.add_parser('scores'); p.add_argument('--bundle', required=True); p.add_argument('--trial', required=True)
    args = parser.parse_args(); os.umask(0o077)
    if args.cmd == 'scores':
        result = score_summary(read_json(Path(args.bundle)/'recipe.json'), args.trial)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    source = Source(args.archive)
    if args.cmd == 'inspect':
        result = {'archive_head': source.head, 'exclusions': source.report.get('exclusions', []),
                  'runs': [{'seq': r['seq'], 'prompt': r['data']['prompt']} for r in source.rows if r['kind'] == 'run_start'],
                  'snapshots': [{'seq': r['seq'], 'reason': r['data']['reason'], 'files': list(r['data']['files'])} for r in source.rows if r['kind'] == 'snapshot'],
                  'attachments': [{'seq': r['seq'], 'label': r['data']['label']} for r in source.rows if r['kind'] == 'attachment']}
    elif args.cmd == 'init':
        with open(args.out, 'xb') as f: f.write(encoded(draft(source)) + b'\n')
        result = {'recipe': args.out, 'status': 'draft; review before export'}
    else:
        result = export(source, read_json(args.recipe), args.out, args.include_trace)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit('Harbor export: ' + str(exc))
