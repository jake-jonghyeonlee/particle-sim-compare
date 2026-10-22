"""Build the two stage-6 benchmark models (grid hotspot profile, distinct electrodes, ONE layer, all 32 diameters).

    python bench_build.py <out_dir> <n_cases> <size> [reference|target]

Built by the FROZEN 7bcb07a package (tests/stage46_generators.py). Reference and target get different electrode names and
seeds; every case has a 1262-style hotspot crop (`size` px) and real electrode layout PNGs (crop + 1000 border)."""
import json, os, sys, time
from pathlib import Path
import numpy as np
REPO = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(REPO / 'tests'), str(REPO / 'tests' / 'reference_stage46'), str(REPO / 'src')]
import stage46_generators as gen

out, n, size = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
original = gen.base_mask


def base_mask(shape, kind, seed):
    if kind != 'grid':
        return original(shape, kind, seed)
    rng = np.random.RandomState(seed)
    h, w = shape
    mask = np.zeros(shape, bool)
    step = max(32, size // 5)
    for r in range(step // 2, h - 4, step):
        for c in range(step // 2, w - 4, step):
            if rng.rand() < 0.7:
                mask[r:r + 3, c:c + 3] = True
    return mask


gen.base_mask = base_mask
report = {}
for side, prefix, offset in (('reference', 'A', 0), ('target', 'B', 100000)):
    if len(sys.argv) > 4 and sys.argv[4] != side:
        continue
    cases = [gen.CaseSpec(f'{prefix}{i:04d}', f'{prefix}x{i:04d}', kind='grid', seed=offset + i) for i in range(n)]
    spec = gen.ModelSpec('PREF' if side == 'reference' else 'PTGT', (size, size), cases)
    started = time.time()
    model = gen.build_model(out, side, spec)
    report[side] = {'seconds': round(time.time() - started, 1), 'data_dir': str(model.data_dir)}
    (out / f'{side}.json').write_text(json.dumps({'data_dir': str(model.data_dir), 'assignment': model.assignment,
                                                  'product_id': spec.product_id, 'cases': [[c.ea, c.eb] for c in cases],
                                                  'size': size}))
    print(json.dumps({side: report[side]}), flush=True)
