import os, sys, time, json, tracemalloc, hashlib
from pathlib import Path
REPO = Path(__file__).resolve().parents[4]
BENCH = Path(os.environ.get('T2_BENCH_ROOT', '/tmp/t2work/bench'))
sys.path.insert(0, str(REPO/'src'))
import simcompare.adapters.real_hotspot as rh
from simcompare.adapters.real_hotspot import configured_dataset, load_real_manifest
from simcompare.electrode_map import compute_electrode_maps
profile = sys.argv[1]
model = json.loads((BENCH/profile/'model.json').read_text())
data = Path(model['data_dir'])
a = dict(model['assignment']); a['max_operation_bytes'] = 1 << 35
ds = configured_dataset(data, load_real_manifest(data), a)
cells = next(iter(ds.frames.values())).valid.size
seen = {}
for name, est in (('guard_stage_map_counting', rh.estimate_stage_map_counting), ('guard_stage_map_regions', rh.estimate_stage_map_regions), ('guard_stage_map_links', rh.estimate_stage_map_regions)):
    def spy(stage, dataset, _n=name, _e=est, _o=getattr(rh, name), **kw):
        seen[_n] = max(seen.get(_n, 0), _e(cells, **kw)); return _o(stage, dataset, **kw)
    setattr(rh, name, spy)
layers = {}
for c in ds.cases: layers[c.analysis_layer] = layers.get(c.analysis_layer, 0) + 1
seen['admission'] = rh.estimate_stage_map_admission(cells, list(layers.values()), len(layers))
tracemalloc.start(); t = time.time()
maps = compute_electrode_maps(ds, diameter_um=5, min_count=3)
peak = tracemalloc.get_traced_memory()[1]; retained = tracemalloc.get_traced_memory()[0]; tracemalloc.stop()
regions = [r for l in maps.layers for r in l.regions]; links = sum(len(r.cases) for r in regions)
print(json.dumps({'profile': profile, 'cells': cells, 'cases': len(ds.cases), 'regions': len(regions), 'links': links, 'seconds': round(time.time()-t, 1),
  'traced_peak_mb': round(peak/2**20, 1), 'retained_mb': round(retained/2**20, 1), 'estimates_mb': {k: round(v/2**20, 1) for k, v in seen.items()}}))
