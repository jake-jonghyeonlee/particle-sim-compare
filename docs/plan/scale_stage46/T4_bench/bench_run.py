"""One stage-6 `compare-windows` run in a fresh process, phases timed, peak RSS sampled after each phase.

    python bench_run.py <data_root> <new|old> <P> <reuse:0|1> [sizes=10,20,30] [jitter=0.5] [tag]

data_root = bench_build.py output (reference.json / target.json). `new` = production, `old` = the frozen 7bcb07a package.
reuse=1 first needs `python bench_run.py <data_root> new 0 compare` (stage-2 compare with the artefact)  -> mode 'compare'.
Phases (wall seconds + ru_maxrss after the phase):
  load_validation   _dataset_for x2 (load_real_manifest authenticates every mask + configured_dataset) + correspondence load/bind
  compute_or_reuse  compute_areas x2, or the artefact load
  rows              compute_local_delta minus the above (windows, jitter, named pair, ranking, sensitivity, explained)
  write_json_csv    write_local_delta (into the scratch directory since T4)
  report            write_local_delta_html (assets authentication + thumbnails + page)
  publish_commit    SetPublication.commit (T4: fsync + journal + backups + replaces + marker + cleanup)
"""
import contextlib, hashlib, importlib, io, json, os, resource, sys, time
from pathlib import Path
import yaml
REPO = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(REPO / 'tests'), str(REPO / 'tests' / 'reference_stage46'), str(REPO / 'src')]
import stage46_generators as gen

root, variant, P, mode = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]
sizes = [float(v) for v in (sys.argv[5] if len(sys.argv) > 5 else '10,20,30').split(',')]
jitter = float(sys.argv[6]) if len(sys.argv) > 6 else 0.5
tag = sys.argv[7] if len(sys.argv) > 7 else f'{variant}_P{P}_{mode}'
reuse = mode == '1' or mode == 'reuse'


def model(side):
    info = json.loads((root / f'{side}.json').read_text())
    cases = [gen.CaseSpec(a, b, kind='grid', seed=i) for i, (a, b) in enumerate(info['cases'])]
    spec = gen.ModelSpec(info['product_id'], (info['size'], info['size']), cases)
    return gen.ModelData(spec, Path(info['data_dir']), info['assignment'], root / side)


ref, tgt = model('reference'), model('target')
work = root / 'work'
work.mkdir(exist_ok=True)
cmp_dir = work / ('cmp_' + variant)
cfg_path = gen.write_config(work / f'cfg_{variant}.yaml', ref, tgt, diameter_um=5, extra={'output_dir': str(cmp_dir)})
corr_path = work / f'corr_P{P}.json'
if not corr_path.exists():
    gen.correspondence(corr_path, ref, tgt, seed=11, n_pairs=P, repeats='uniform', blocked=0, outside=0, edge=0, duplicate=0,
                       margin=max(sizes) / 2 + jitter + 0.05)

prefix = 'simcompare'
if variant == 'old':
    import loader
    loader.load()
    prefix = 'simcompare_7bcb07a'
mod = lambda name: importlib.import_module(f'{prefix}.{name}')
rh = mod('adapters.real_hotspot')
counters = rh.enable_io_counters()
rss = lambda: round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024) if sys.platform == 'darwin'
                    else resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 0)
phases, spent = {}, {}


def timed(owner, attr, bucket):
    real = getattr(owner, attr)

    def wrapped(*a, **k):
        t = time.time()
        try:
            return real(*a, **k)
        finally:
            spent[bucket] = spent.get(bucket, 0.0) + time.time() - t
            phases[bucket] = {'seconds': round(spent[bucket], 1), 'maxrss_mb': rss()}
    setattr(owner, attr, wrapped)
    return real


pipeline = mod('pipeline')
timed(pipeline, '_dataset_for', 'load_dataset')
timed(mod('correspondence'), 'load_correspondence', 'corr')
timed(mod('correspondence'), 'bind_pairs', 'corr')
timed(mod('linepixel'), 'compute_areas', 'compute_areas')
if prefix == 'simcompare':
    timed(mod('compare_masks'), 'load_compare_masks', 'reuse_load')
ld, rep = mod('local_delta'), mod('local_delta_report')
timed(ld, 'compute_local_delta', 'compute_local_delta_total')
timed(ld, 'write_local_delta', 'write_json_csv')
timed(rep, 'write_local_delta_html', 'report')
if prefix == 'simcompare':
    timed(mod('publication').SetPublication, 'commit', 'publish_commit')
cli = mod('cli')
out = work / f'out_{tag}'
import shutil
shutil.rmtree(out, ignore_errors=True)
if mode == 'compare':
    argv = ['compare', '--config', str(cfg_path)]
    shutil.rmtree(cmp_dir, ignore_errors=True)
else:
    argv = ['compare-windows', '--config', str(cfg_path), '--correspondence', str(corr_path), '--window-sizes',
            *[str(s) for s in sizes], '--jitter-um', str(jitter), '--sort-diameter-um', '5', '--out', str(out)]
    if variant == 'new' and not reuse and cmp_dir.exists():
        # stage 6 must recompute: hide the artefact of an earlier compare run
        for f in cmp_dir.glob('comparison_masks.zip'):
            f.rename(f.with_suffix('.zip.hidden'))
    if variant == 'new' and reuse:
        for f in cmp_dir.glob('comparison_masks.zip.hidden'):
            f.rename(f.with_name('comparison_masks.zip'))
t0 = time.time()
buf = io.StringIO()
with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
    code = cli.main(argv)
wall = time.time() - t0
if 'compute_local_delta_total' in spent:
    spent['rows'] = max(0.0, spent['compute_local_delta_total'] - spent.get('compute_areas', 0.0))
    phases['rows'] = {'seconds': round(spent['rows'], 1), 'maxrss_mb': phases['compute_local_delta_total']['maxrss_mb']}
    phases.pop('compute_local_delta_total', None)
if 'reuse_load' in spent:
    phases['compute_or_reuse'] = {'seconds': round(spent['reuse_load'], 1), 'what': 'artefact load'}
elif 'compute_areas' in spent:
    phases['compute_or_reuse'] = {'seconds': round(spent['compute_areas'], 1), 'what': 'compute_areas x2'}
result = {'tag': tag, 'variant': variant, 'P': P, 'sizes': sizes, 'jitter': jitter, 'mode': mode, 'exit': code, 'wall_s': round(wall, 1),
          'peak_rss_mb': rss(), 'phases': phases, 'io_counters': counters, 'tail': buf.getvalue()[-400:]}
if mode != 'compare' and (out / 'local_delta.json').exists():
    d = json.loads((out / 'local_delta.json').read_text(encoding='utf-8'))
    result['rows'] = d['n_rows']
    result['n_ready'] = d['provenance']['n_ready']
    result['status'] = d['status']
    result['out_bytes'] = {p.name: p.stat().st_size for p in out.iterdir() if p.is_file() and not p.name.endswith('.png')}
    result['hidden_files_left'] = sorted(p.name for p in out.iterdir() if p.name.startswith('.'))
    result['png_files'] = sum(1 for p in out.iterdir() if p.name.endswith('.png'))
print(json.dumps(result))
