#!/usr/bin/env python3
"""Propose a two-stage recipe for examples/make_demo.py; does not approve semantic answers."""
import argparse
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('exporter', Path(__file__).resolve().parents[2] / 'harbor_export.py')
h = importlib.util.module_from_spec(spec); spec.loader.exec_module(h)


def numeric(cid, points, description, path, value):
    return {'id': cid, 'points': points, 'description': description,
            'check': {'type': 'json_number', 'file': 'results.json', 'path': path, 'expected': value, 'abs_tol': 1e-10}}


def manual(cid, description):
    return {'id': cid, 'points': 15, 'description': description, 'check': {'type': 'manual', 'file': 'report.md'}}


def proposal(source):
    recipe = h.draft(source)
    h.require(len(recipe['stages']) == 2, 'this example expects the two-turn synthetic demo')
    recipe['name'] = 'synthetic-study'
    stages = recipe['stages']
    initial = next(r for r in source.rows if r['kind'] == 'snapshot' and r['data']['reason'] == 'initial')
    attachment = next(r for r in source.rows if r['kind'] == 'attachment')
    stages[0]['inputs'] = [{'destination': 'study.csv', 'source': {'kind': 'snapshot', 'seq': initial['seq'], 'path': 'study.csv'}}]
    stages[1]['inputs'] = [{'destination': 'protocol.txt', 'source': {'kind': 'attachment', 'seq': attachment['seq']}}]
    for stage in stages:
        stage['outputs'] = ['analysis.py', 'results.json', 'report.md']
    stages[0]['rubric']['criteria'] = [
        numeric('pooled', 20, 'Pooled treatment-minus-control difference is -283/4200.', ['pooled_difference'], -283/4200),
        numeric('stratum-a', 20, 'Within-A difference is 1/30.', ['stratum_differences','A'], 1/30),
        numeric('stratum-b', 20, 'Within-B difference is 17/720.', ['stratum_differences','B'], 17/720),
        manual('aggregation', 'Explains the sign reversal using different A/B compositions of treatment and control, not contradictory arithmetic.'),
        manual('uncertainty', 'States protocol/assignment information is missing and the observed association does not establish causality; does not assume the later protocol.')]
    stages[1]['rubric']['criteria'] = [
        numeric('preserve-pooled', 20, 'Preserves the original pooled difference -283/4200.', ['pooled_difference'], -283/4200),
        numeric('standardized', 40, 'Uses equal A/B weights to obtain standardized difference 41/1440.', ['standardized_difference'], 41/1440),
        manual('explain-revision', 'Explains common target-population weights versus different observed arm compositions, and why the standardized and pooled results differ.'),
        manual('causal-boundary', 'Uses the new observational-assignment protocol, notes within-stratum confounding remains possible, and does not claim standardization establishes causality.')]
    for stage in stages:
        stage['rubric']['criteria'].append({'id':'code-deliverable', 'points':10, 'description':'analysis.py is present and nonempty (delivery check only; not proof that arbitrary code is correct).', 'check':{'type':'file_present', 'file':'analysis.py'}})
    return recipe


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True); parser.add_argument('--out', required=True)
    args = parser.parse_args()
    with open(args.out, 'x') as f:
        json.dump(proposal(h.Source(args.archive)), f, ensure_ascii=False, indent=2)
    print('Draft written. Review references, add hash-bound manual assessments and complete review notes before export.')
