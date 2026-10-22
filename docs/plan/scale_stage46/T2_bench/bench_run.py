"""python bench_run.py <profile> <new|old> [min_count] : one `candidates` run in a fresh process, phases timed, peak RSS sampled."""
import sys, time, json, resource, io, contextlib, threading, os
from pathlib import Path
import yaml
REPO = Path(__file__).resolve().parents[4]
BENCH = Path(os.environ.get('T2_BENCH_ROOT', '/tmp/t2work/bench'))
sys.path.insert(0, str(REPO/'tests'/'reference_stage46')); sys.path.insert(0, str(REPO/'src'))
profile, variant = sys.argv[1], sys.argv[2]
min_count = int(sys.argv[3]) if len(sys.argv) > 3 else 3
root = BENCH/profile
model = json.loads((root/'model.json').read_text())
import hashlib
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
data = Path(model['data_dir'])
side = {'data_dir': str(data), 'product_id': 'BENCH', 'condition_assignment': model['assignment'],
        'asset_manifest': {'path': 'review_assets.json', 'sha256': sha(data/'review_assets.json')},
        'electrode_import_record': {'path': 'electrode_asset_import_record.json', 'sha256': sha(data/'electrode_asset_import_record.json')}}
cfg_path = root/'cfg.yaml'
cfg_path.write_text(yaml.safe_dump({'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'unused',
                    'map': {'diameter_um': 5}, 'reference': side, 'target': side}, sort_keys=True))
if variant == 'old':
    import loader; loader.load(); prefix = 'simcompare_7bcb07a'
else:
    prefix = 'simcompare'
import importlib
cli = importlib.import_module(prefix + '.cli'); cand = importlib.import_module(prefix + '.candidates')
rh = importlib.import_module(prefix + '.adapters.real_hotspot')
counters = rh.enable_io_counters()
samples = {'peak_rss_mb': 0}
def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)   # macOS: bytes
phases = []
def timed(name, func):
    def wrapped(*a, **k):
        t = time.time(); r0 = rss_mb()
        try: return func(*a, **k)
        finally: phases.append({'phase': name, 'seconds': round(time.time() - t, 1), 'maxrss_after_mb': round(rss_mb(), 0)})
    return wrapped
for name in ('_prepare', '_export_side', '_export_index'):
    setattr(cand, name, timed(name, getattr(cand, name)))
out = root/f'cand_{variant}_{min_count}'
import shutil; shutil.rmtree(out, ignore_errors=True)
t0 = time.time()
buf = io.StringIO()
with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
    code = cli.main(['candidates', '--config', str(cfg_path), '--min-count', str(min_count), '--window-um', '20', '--merge-overlap', '0.5', '--out', str(out)])
wall = time.time() - t0
sizes = {p.relative_to(out).as_posix(): p.stat().st_size for p in sorted(out.rglob('*.json'))}
payload = json.loads((out/'candidate_queries_v1.json').read_text(encoding='utf-8')) if (out/'candidate_queries_v1.json').exists() else {}
rows = {}
for folder in ('reference_export', 'reference_index_export'):
    f = out/folder/'review_rois.json'
    if f.exists():
        d = json.loads(f.read_text(encoding='utf-8')); rows[folder] = {'rois': len(d['rois']), 'ready': sum(r['status'] == 'ready' for r in d['rois']), 'blocked': sum(r['status'] != 'ready' for r in d['rois'])}
print(json.dumps({'profile': profile, 'variant': variant, 'min_count': min_count, 'exit': code, 'wall_s': round(wall, 1), 'peak_rss_mb': round(rss_mb(), 0),
                  'phases': phases, 'io_counters': counters, 'export_bytes': {k: v for k, v in sizes.items() if k.endswith('review_rois.json')},
                  'rows': rows, 'status': payload.get('status'), 'n_queries': {d['direction']: len(d['queries']) for d in payload.get('directions', [])},
                  'stderr_tail': buf.getvalue()[-300:]}))
