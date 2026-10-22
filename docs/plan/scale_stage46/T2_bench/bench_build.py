"""Build one stage-4 benchmark model (ModelData) for a profile: python bench_build.py <profile> <n> <size> <out_dir>
profiles: centered-distinct, centered-shared, grid-distinct, grid-shared. Data only; built by the FROZEN package."""
import os, sys, time, json, pickle
from pathlib import Path
import numpy as np
REPO = Path(__file__).resolve().parents[4]
BENCH = Path(os.environ.get('T2_BENCH_ROOT', '/tmp/t2work/bench'))
sys.path.insert(0, str(REPO/'tests')); sys.path.insert(0, str(REPO/'tests'/'reference_stage46')); sys.path.insert(0, str(REPO/'src'))
import stage46_generators as gen

profile, n, size, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), Path(sys.argv[4])
original = gen.base_mask
def base_mask(shape, kind, seed):
    if kind == 'grid':
        rng = np.random.RandomState(seed); h, w = shape; m = np.zeros(shape, bool)
        step = max(32, size // 5)
        for r in range(step // 2, h - 4, step):
            for c in range(step // 2, w - 4, step):
                if rng.rand() < 0.7:
                    m[r:r+3, c:c+3] = True
        return m
    return original(shape, kind, seed)
gen.base_mask = base_mask
kind = 'centered' if profile.startswith('centered') else 'grid'
shared = profile.endswith('shared')
KEEP = (1, 5, 10, 30)
drop = tuple(d for d in gen.DIAMETERS if d not in KEEP)
pool = 40
if shared:
    import itertools
    pairs = list(itertools.combinations([f'E{k:03d}' for k in range(pool)], 2))
    np.random.RandomState(1).shuffle(pairs)
    pairs = pairs[:n]
else:
    pairs = [(f'A{i:04d}', f'B{i:04d}') for i in range(n)]
unique = [gen.CaseSpec(ea, eb, kind=kind, seed=i, drop=drop) for i, (ea, eb) in enumerate(pairs)]
spec = gen.ModelSpec('BENCH', (size, size), unique)
t = time.time()
model = gen.build_model(out, 'model', spec)
print(json.dumps({'profile': profile, 'cases': len(unique), 'size': size, 'seconds': round(time.time() - t, 1), 'data_dir': str(model.data_dir)}))
(out/'model.json').write_text(json.dumps({'data_dir': str(model.data_dir), 'assignment': model.assignment, 'product_id': 'BENCH'}))
