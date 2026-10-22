#!/usr/bin/env python3
"""Seeded real_hotspot_v1 fixture for configured `compare` (payload and RSS measurements). Generator only, no data is committed.

    python scripts/make_compare_fixture.py --out /tmp/cmp_fx --cases 50 --size 200 [--seed 1] [--shared 3]

Builds <out>/imported (import-hotspots result + pairs.csv) and <out>/config.yaml (reference == target, output_dir reports).
Each case is a pair `A<i>`/`B<i>` with seeded blobs that grow with the diameter; `--shared K` cases share one centre
cluster so that overlapping (shared) buckets and long lineage lists occur. 70 % of pairs are `line`, 30 % `pixel`.
"""
import argparse
import csv
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

DIAMETERS = (*range(1, 31), 40, 50)


def build(out, cases, size, seed=1, shared=3):
    from simcompare.adapters.real_hotspot import import_hotspots
    out = Path(out)
    rng = np.random.RandomState(seed)
    root, imported = out / 'input', out / 'imported'
    names = [f'lateral_short-A{i:04d}-L1-B{i:04d}-L1' for i in range(cases)]
    (out).mkdir(parents=True, exist_ok=True)
    (out / 'expected_cases.txt').write_text('\n'.join(names) + '\n', encoding='utf-8')
    yy, xx = np.ogrid[:size, :size]
    centres = None
    for index, name in enumerate(names):
        case = root / name
        (case / 'result').mkdir(parents=True)
        for label in ('1', '2'):
            Image.new('L', (size + 1000, size + 1000), 0).save(case / f'{label}.png')
        if index % shared == 0:
            centres = [(rng.randint(0, size), rng.randint(0, size)) for _ in range(rng.randint(1, 4))]
        cluster = [(cy + rng.randint(-6, 7), cx + rng.randint(-6, 7)) for cy, cx in centres]
        with (case / 'result' / 'lateral_short.csv').open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(('defect_size(um)', 'Area(um2)'))
            for diameter in DIAMETERS:
                writer.writerow((diameter, '0.01'))
        for diameter in DIAMETERS:
            radius = max(1.0, diameter * size / 400.0)
            mask = np.zeros((size, size), bool)
            for cy, cx in cluster:
                mask |= (yy - cy) ** 2 + (xx - cx) ** 2 <= radius ** 2
            Image.fromarray((mask * 255).astype(np.uint8)).save(case / f'hotspot_lateral_short{diameter:02d}.png')
    import_hotspots(root, imported, expected_cases=out / 'expected_cases.txt')
    with (imported / 'pairs.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(('pair_id', 'electrode_a_id', 'electrode_b_id', 'class', 'pair_table_version'))
        for index in range(cases):
            writer.writerow((f'P{index:04d}', f'A{index:04d}', f'B{index:04d}',
                             'line' if rng.rand() < .7 else 'pixel', 'fixture-pairs-v1'))
    assignment = {'product_id': 'FX', 'design_version': 'FX', 'source_id': 'FX', 'process_step_id': 'STEP',
                  'exposure_id': 'EXP', 'unit_id': 'UNIT', 'pair_table_path': 'pairs.csv',
                  'layer_stack': [{'actual_layer': 'L1', 'stack_order': 1, 'density_process_layer': 'P1'}]}
    cfg = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'reports',
           'reference': {'data_dir': str(imported), 'condition_assignment': assignment},
           'target': {'data_dir': str(imported), 'condition_assignment': assignment}}
    (out / 'config.yaml').write_text(yaml.safe_dump(cfg), encoding='utf-8')
    return out / 'config.yaml'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--cases', type=int, default=50)
    parser.add_argument('--size', type=int, default=200)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--shared', type=int, default=3)
    args = parser.parse_args()
    print(json.dumps({'config': str(build(args.out, args.cases, args.size, args.seed, args.shared))}))


if __name__ == '__main__':
    main()
