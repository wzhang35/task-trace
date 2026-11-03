#!/usr/bin/env python3
"""Create a wholly synthetic two-turn research task; refuses to overwrite."""
import argparse
from pathlib import Path


def create(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    work = root / 'workspace'
    work.mkdir()
    (work / 'study.csv').write_text('stratum,arm,successes,total\nA,treatment,81,90\nA,control,234,270\nB,treatment,192,270\nB,control,55,80\n')
    (root / 'protocol.txt').write_text('Synthetic study. Assignment was observational, not randomized. Target population weights are A=0.5 and B=0.5. Counts are aggregate observations; no subject-level covariates are available.\n')
    (root / 'turn1.txt').write_text('This is a synthetic research task. Analyze study.csv using Python standard library only. First attempt to read protocol.txt once; if absent, record that limitation and continue, without searching outside this workspace. Create analysis.py, results.json and report.md. Calculate pooled success rates and the treatment-minus-control difference; then calculate the difference separately in A and B. Explain any sign reversal and distinguish an observed association from a causal effect. results.json must include pooled_difference and stratum_differences (A and B), as proportions rather than percentage points. Run your script and verify the arithmetic. Do not use network, external files or subagents. Give a short final response summarizing findings and files.\n')
    (root / 'turn2.txt').write_text('Follow-up from the researcher in this synthetic exercise: the newly supplied protocol.txt says assignment was observational and the target has equal A/B weights. Please update the existing analysis.py, results.json and report.md to include standardized_difference using those weights. Preserve pooled_difference and stratum_differences. Explain why the standardized result differs from the pooled result and why neither establishes causality. Re-run the code and check the result. Do not use network, external files or subagents. Give a short final response.\n')
    return root


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('destination')
    print(create(p.parse_args().destination))
