"""Micro-benchmark of one linepixel group: single-pass labelling vs the pre-T1 reference (tests/reference_linepixel.py).

    python scripts/benchmark_linepixel.py [--pairs 100] [--size 1262] [--seed 1]

Each implementation runs in its own subprocess so peak RSS is not shared; the inputs (seeded random blobs, 1-2
sources per pair, 70 % line / 30 % pixel pairs, one layer, one diameter) are rebuilt identically in both. Not part of
the default test run. Reports wall time, peak RSS, RSS growth over the built inputs, and whether outputs are equal.
"""
import argparse
import json
from pathlib import Path
import resource
import subprocess
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]

from simcompare.numeric_schema import CaseIdentity, Endpoint, Frame, MaskRecord, NumericCase  # noqa: E402


def blob_mask(rng, size):
    mask = np.zeros((size, size), bool)
    yy, xx = np.ogrid[:size, :size]
    for _ in range(rng.randint(3, 7)):
        cy, cx, ry, rx = rng.randint(0, size), rng.randint(0, size), rng.randint(15, 90), rng.randint(15, 90)
        mask |= ((yy - cy) / ry) ** 2 + ((xx - cx) / rx) ** 2 <= 1
    return mask


def build_entries(pairs, size, seed):
    rng = np.random.RandomState(seed)
    frame = Frame('frame', 0.1, (0, 0), (size, size), np.ones((size, size), bool))
    entries = []
    for p in range(pairs):
        kind = 'line' if rng.rand() < .7 else 'pixel'
        for s in range(rng.randint(1, 3)):
            identity = CaseIdentity('product', 'design', 'source', 'step', 'exposure', f'case{p}_{s}', 'frame', 'bench')
            record = MaskRecord(5, frame, blob_mask(rng, size), 'observed', (), identity=identity, analysis_layer='M2')
            case = NumericCase(identity, (Endpoint(f'A{p}', 'gate', 'M2'), Endpoint(f'B{p}', 'data', 'M2')),
                               'lateral_short', f'P{p:03d}', kind, 'M2', 'density-M2', {5: record},
                               {'case_id': f'case{p}_{s}', 'records': []}, {'unit_id': 'unit', 'records': []})
            entries.append((case, record))
    return entries


def run(impl, pairs, size, seed):
    from simcompare.linepixel import GroupKey, _group
    entries = build_entries(pairs, size, seed)
    if impl == 'ref':
        import reference_linepixel
        function = reference_linepixel._group
    else:
        function = _group
    case = entries[0][0]
    i = case.identity
    key = GroupKey(i.product_id, i.design_version, 'unit', i.source_id, i.process_step_id, i.exposure_id, 'M2',
                   'density-M2', i.source_contract_version, i.frame_id, 5.0)
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    start = time.perf_counter()
    group = function(key, entries, 65536, 'bench-v1')
    wall = time.perf_counter() - start
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    scale = 1 if sys.platform == 'darwin' else 1024   # bytes on macOS, KiB on Linux
    digest = json.dumps(group.to_dict(), sort_keys=True)
    import hashlib
    return {'impl': impl, 'wall_s': round(wall, 2), 'peak_rss_mb': round(peak * scale / 2**20),
            'rss_before_mb': round(before * scale / 2**20), 'rss_growth_mb': round((peak - before) * scale / 2**20),
            'buckets': len(group.line.exclusive) + len(group.line.shared) + len(group.pixel.exclusive) + len(group.pixel.shared),
            'union_um2': group.union.value, 'to_dict_sha256': hashlib.sha256(digest.encode()).hexdigest()[:16]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pairs', type=int, default=100)
    parser.add_argument('--size', type=int, default=1262)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--impl', choices=('new', 'ref'))
    args = parser.parse_args()
    if args.impl:
        print(json.dumps(run(args.impl, args.pairs, args.size, args.seed)))
        return
    results = {}
    for impl in ('new', 'ref'):
        out = subprocess.run([sys.executable, __file__, '--impl', impl, '--pairs', str(args.pairs),
                              '--size', str(args.size), '--seed', str(args.seed)], capture_output=True, text=True, check=True)
        results[impl] = json.loads(out.stdout.strip().splitlines()[-1])
        print(json.dumps(results[impl]))
    print(f"speedup {results['ref']['wall_s'] / results['new']['wall_s']:.1f}x; "
          f"outputs equal: {results['new']['to_dict_sha256'] == results['ref']['to_dict_sha256']}")


if __name__ == '__main__':
    main()
