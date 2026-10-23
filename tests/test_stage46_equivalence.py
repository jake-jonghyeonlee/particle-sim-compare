"""Phase B T1: frozen-7bcb07a oracle for stages 4-6 (candidates, confirm-pairs, compare-windows).

Production (``simcompare``) and the byte-frozen 7bcb07a copy (``simcompare_7bcb07a``, tests/reference_stage46) are run
on the same generated inputs (tests/stage46_generators.py, built by the frozen package):

  * stage 4: ``compute_electrode_maps`` (every ElectrodeMaps field, arrays with array_equal, to_dict JSON) and the whole
    ``candidates`` output tree byte-for-byte;
  * stage 5: ``confirm-pairs`` output tree byte-for-byte, fed with the frozen candidates tree;
  * stage 6: ``compute_local_delta`` (LocalDeltaResult, floats compared with ==) and every file ``compare-windows``
    writes byte-for-byte (JSON/CSV/HTML/_thumbs.json/PNG), compact and full writers.

Documented normalisations (everything else is compared as raw bytes; stdout/stderr/exit code are compared too):
  * OUT_DIR: the absolute path of the per-run output directory (and of the scenario data/config directories, which are
    shared and identical for both runs) is NOT rewritten; only the two output directories differ, and each is replaced
    by ``<OUT>`` in file contents, stdout and stderr.
  * confirm-pairs ``labelled_at`` (wall clock, second resolution): the clock function ``utc_now_iso`` of BOTH packages is
    pinned to a constant instead of rewriting bytes.
  * roi_export producer_commit: the frozen ``roi_export.__file__`` is pointed at the production checkout (loader.py), so
    git ls-files/rev-parse resolve against the same repository as production. No other field is normalised.

Phase B T2 deviation (the only intended one): review_rois.json rows reference their layer's identity lists (`observed_ref`) and
the lists are written once in the top-level `region_coverages` (roi_export.expand_region_coverages is the inverse). The
harness expands the PRODUCTION side's slim exports back to the 7bcb07a form and re-derives the export sha256 values recorded in
candidate_queries_v1.json from the expanded bytes before comparing; ``test_slim_export_expansion_reproduces_7bcb07a_bytes``
proves the expansion on its own and everything else stays raw-byte equal.

Known limits of the freeze: none that change behaviour; the frozen copy is the whole package (adapters, pipeline,
measurement, numeric_images, ...) so no production helper leaks into the oracle call graph. ``tests/test_handoff.py`` is
unrelated (sibling checkout).
"""
import dataclasses
import hashlib
import io
import contextlib
import json
import math
import re
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent / 'reference_stage46'))
import loader  # noqa: E402
import stage46_generators as gen  # noqa: E402

PKG = loader.FROZEN_NAME
FROZEN = gen.frozen()
import simcompare  # noqa: E402,F401
import simcompare.cli  # noqa: E402,F401

ALL_SCENARIOS = ('shared', 'distinct', 'multilayer', 'components', 'incomplete', 'noroles', 'policy', 'unicode')
FIXED_CLOCK = '2026-10-22T00:00:00Z'


def module(prefix, name):
    import importlib
    return importlib.import_module(f'{prefix}.{name}')


PREFIXES = ('simcompare', PKG)


# --- harness ----------------------------------------------------------------------------------------------------

def run_cli(prefix, argv, monkeypatch=None):
    """Run ``<prefix>.cli.main(argv)``; return (exit code, stdout, stderr)."""
    main = module(prefix, 'cli').main
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


def tree(folder):
    folder = Path(folder)
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob('*')) if p.is_file()}


def _forms(path):
    return {str(path), str(Path(path).resolve())}


def _normalise(data, out_dir, roots=()):
    """Replace the per-run output directory by <OUT> and (mutation tests, where each package gets its own clone of the
    scenario) the clone root by <ROOT>. Works on str and bytes."""
    is_bytes = isinstance(data, bytes)
    for token, path in sorted([('<OUT>', out_dir), *[('<ROOT>', r) for r in roots]], key=lambda t: -len(str(t[1]))):
        for form in sorted(_forms(path), key=len, reverse=True):
            data = data.replace(form.encode(), token.encode()) if is_bytes else data.replace(form, token)
    return data


def expand_slim_exports(files):
    """Production (a) tree -> 7bcb07a form: slim review_rois.json expanded, recorded export hashes re-derived.

    Only files that really contain `region_coverages` are touched; their expansion must be byte-exact (else the comparison
    with the frozen bytes fails)."""
    from simcompare.roi_export import expand_region_coverages
    files = dict(files)
    swaps = {}
    for name, data in sorted(files.items()):
        if name.endswith('review_rois.json') and b'"region_coverages"' in data:
            expanded = (json.dumps(expand_region_coverages(json.loads(data.decode('utf-8'))), ensure_ascii=False, indent=2,
                                   allow_nan=False) + '\n').encode('utf-8')
            swaps[hashlib.sha256(data).hexdigest()] = hashlib.sha256(expanded).hexdigest()
            files[name] = expanded
    for name, data in sorted(files.items()):
        if name.endswith('candidate_queries_v1.json'):
            text = data.decode('utf-8')
            for old, new in swaps.items():
                text = text.replace(old, new)
            files[name] = text.encode('utf-8')
    return files


def compare_trees(a_dir, b_dir, roots_a=(), roots_b=()):
    """List of differences between two output trees (empty = byte-identical after the OUT_DIR/ROOT normalisation and the
    documented review_rois expansion of the production side)."""
    ta, tb = expand_slim_exports(tree(a_dir)), tree(b_dir)
    problems = []
    if set(ta) != set(tb):
        problems.append(f'file sets differ: only production {sorted(set(ta) - set(tb))}, only frozen {sorted(set(tb) - set(ta))}')
    for name in sorted(set(ta) & set(tb)):
        if _normalise(ta[name], a_dir, roots_a) != _normalise(tb[name], b_dir, roots_b):
            problems.append(f'bytes differ: {name} ({len(ta[name])} vs {len(tb[name])} bytes)')
    return problems


def compare_runs(production, frozen, a_dir, b_dir, roots_a=(), roots_b=()):
    problems = []
    if production[0] != frozen[0]:
        problems.append(f'exit code {production[0]} != {frozen[0]}')
    for label, x, y in (('stdout', production[1], frozen[1]), ('stderr', production[2], frozen[2])):
        if _normalise(x, a_dir, roots_a) != _normalise(y, b_dir, roots_b):
            problems.append(f'{label} differs')
    return problems + compare_trees(a_dir, b_dir, roots_a, roots_b)


def run_both(argv_for, out_root, label, *, setup=None):
    """argv_for(out_dir) -> argv list. Runs production then frozen; returns (problems, production_run, frozen_run).

    setup(prefix) may return a context manager (e.g. to install a fault/mutation hook for that package only).
    """
    outs, runs = {}, {}
    for prefix in PREFIXES:
        out = Path(out_root) / f'{label}_{"prod" if prefix == "simcompare" else "frozen"}'
        outs[prefix] = out
        ctx = setup(prefix) if setup else contextlib.nullcontext()
        with ctx:
            runs[prefix] = run_cli(prefix, argv_for(out))
    problems = compare_runs(runs['simcompare'], runs[PKG], outs['simcompare'], outs[PKG])
    return problems, runs['simcompare'], runs[PKG]


def assert_equal_runs(argv_for, out_root, label, **kwargs):
    problems, production, frozen = run_both(argv_for, out_root, label, **kwargs)
    assert not problems, '\n'.join(problems + ['--- production stderr ---', production[2][-1500:]])
    return production, frozen


# deep comparison of dataclass trees containing numpy arrays ------------------------------------------------------

def _key_form(key):
    """Dict keys may be frozen dataclasses of either package: compare them by class name + field values."""
    if dataclasses.is_dataclass(key) and not isinstance(key, type):
        return (type(key).__name__, tuple(_key_form(getattr(key, f.name)) for f in dataclasses.fields(key)))
    if isinstance(key, tuple):
        return tuple(_key_form(k) for k in key)
    return key


def deep_diff(a, b, path='$', limit=20):
    """Differences between two object trees; dataclasses compare by class name + fields, arrays by dtype/shape/content,
    floats with == (NaN equals NaN). Production and frozen classes are different objects, so types compare by name."""
    out = []

    def rec(x, y, where):
        if len(out) >= limit:
            return
        if dataclasses.is_dataclass(x) and not isinstance(x, type):
            if not (dataclasses.is_dataclass(y) and type(x).__name__ == type(y).__name__):
                out.append(f'{where}: {type(x).__name__} vs {type(y).__name__}')
                return
            for f in dataclasses.fields(x):
                rec(getattr(x, f.name), getattr(y, f.name), f'{where}.{f.name}')
        elif isinstance(x, np.ndarray):
            if not (isinstance(y, np.ndarray) and x.dtype == y.dtype and x.shape == y.shape and np.array_equal(x, y)):
                out.append(f'{where}: array differs')
        elif isinstance(x, dict):
            if not isinstance(y, dict) or [_key_form(k) for k in x] != [_key_form(k) for k in y]:
                out.append(f'{where}: dict keys differ')
                return
            for (kx, vx), (ky, vy) in zip(x.items(), y.items()):
                rec(vx, vy, f'{where}[{kx!r}]')
        elif isinstance(x, (list, tuple)):
            # F1 / RB-A12: ElectrodeRegion.coverage holds its identity lists as (immutable) tuples; the frozen 7bcb07a keeps
            # lists. Same elements, same JSON; this is the only type difference the comparison tolerates.
            frozen_coverage = (isinstance(x, tuple) and type(y) is list and re.search(r"\.coverage\['[a-z_]+'\]$", where))
            if (type(x) is not type(y) and not frozen_coverage) or len(x) != len(y):
                out.append(f'{where}: sequence {type(x).__name__}/{len(x)} vs {type(y).__name__}/{len(y) if hasattr(y, "__len__") else "?"}')
                return
            for i, (p, q) in enumerate(zip(x, y)):
                rec(p, q, f'{where}[{i}]')
        elif isinstance(x, (set, frozenset)):
            if x != y:
                out.append(f'{where}: set differs')
        elif isinstance(x, float):
            if not (isinstance(y, float) and (x == y or (math.isnan(x) and math.isnan(y)))):
                out.append(f'{where}: {x!r} != {y!r}')
        elif type(x).__module__ == 'builtins' or x is None:
            if type(x) is not type(y) or x != y:
                out.append(f'{where}: {x!r} != {y!r}')
        elif hasattr(x, '__dict__'):      # LazyRecords & friends: compare materialised mappings
            if type(x).__name__ != type(y).__name__:
                out.append(f'{where}: {type(x).__name__} vs {type(y).__name__}')
            elif hasattr(x, 'items'):
                rec(dict(x.items()), dict(y.items()), where)
        else:
            if x != y:
                out.append(f'{where}: {x!r} != {y!r}')

    rec(a, b, path)
    return out


# --- fixtures ---------------------------------------------------------------------------------------------------

@pytest.fixture(scope='session')
def stage46(tmp_path_factory):
    class Factory:
        def scenario(self, name):
            return gen.scenario(tmp_path_factory, name)

        def clone(self, name, label, dest=None):
            return gen.clone_scenario(tmp_path_factory, name, label, dest)

        def run_dir(self, label):
            return Path(tmp_path_factory.mktemp(label))
    return Factory()


@pytest.fixture(autouse=True)
def pinned_clock(monkeypatch):
    for prefix in PREFIXES:
        monkeypatch.setattr(module(prefix, 'confirm_pairs'), 'utc_now_iso', lambda: FIXED_CLOCK)


def config_for(stage46, name, **kwargs):
    base, ref, tgt = stage46.scenario(name)
    cfg = gen.write_config(base / f'cfg_{kwargs.get("diameter_um", 1)}_{int(kwargs.get("manifest", True))}.yaml', ref, tgt, **kwargs)
    return base, ref, tgt, cfg


# --- 0. the frozen oracle itself --------------------------------------------------------------------------------

def test_frozen_bytes_match_checked_in_digests_and_git():
    loader.verify_digests()
    assert len(loader.read_digests()) >= 30
    repo = Path(__file__).resolve().parents[1]
    git = shutil.which('git')
    if not git or not (repo / '.git').exists():
        pytest.skip('git checkout not available; the checked-in digests above were verified')
    probe = subprocess.run([git, 'cat-file', '-e', loader.BASE_COMMIT + '^{commit}'], cwd=repo, capture_output=True)
    if probe.returncode != 0:
        pytest.skip('base commit 7bcb07a not present in this checkout')
    for name in loader.read_digests():
        relative = name.split('/', 1)[1]
        shown = subprocess.run([git, 'show', f'{loader.BASE_COMMIT}:src/simcompare/{relative}'], cwd=repo,
                               capture_output=True, check=True).stdout
        assert hashlib.sha256(shown).hexdigest() == loader.read_digests()[name], name


def test_frozen_call_graph_never_reaches_production():
    """Every frozen module's globals/imports resolve inside the frozen package; no production simcompare module is used."""
    loader.load()
    frozen_modules = [m for n, m in sys.modules.items() if n == PKG or n.startswith(PKG + '.')]
    assert len(frozen_modules) >= 15
    for mod in frozen_modules:
        for value in vars(mod).values():
            owner = getattr(value, '__module__', None)
            assert not (isinstance(owner, str) and (owner == 'simcompare' or owner.startswith('simcompare.'))), (mod.__name__, value)
    for name in ('cli', 'pipeline', 'candidates', 'confirm_pairs', 'electrode_map', 'local_delta', 'local_delta_report',
                 'roi_export', 'measurement', 'numeric_images'):
        origin = Path(module(PKG, name).__spec__.origin).resolve()
        assert loader.FROZEN_DIR.resolve() in origin.parents, origin


# --- 1. stage 4: electrode maps (library level) -----------------------------------------------------------------

def datasets_for(prefix, model):
    rh = module(prefix, 'adapters.real_hotspot')
    manifest = rh.load_real_manifest(model.data_dir)
    return rh.configured_dataset(model.data_dir, manifest, model.assignment)


MAP_CASES = [(name, d, k) for name in ('shared', 'distinct', 'multilayer', 'components', 'incomplete', 'policy', 'unicode')
             for d, k in ((1, None), (1, 2), (5, 3))]


@pytest.mark.parametrize('name,diameter,min_count', MAP_CASES, ids=[f'{n}-d{d}-k{k}' for n, d, k in MAP_CASES])
def test_compute_electrode_maps_equals_frozen(stage46, name, diameter, min_count):
    _, ref, tgt = stage46.scenario(name)
    checked_regions = 0
    for model in (ref, tgt):
        results = {}
        for prefix in PREFIXES:
            dataset = datasets_for(prefix, model)
            maps = module(prefix, 'electrode_map').compute_electrode_maps(dataset, diameter_um=diameter, min_count=min_count)
            results[prefix] = maps
        diff = deep_diff(results['simcompare'], results[PKG])
        assert not diff, diff
        assert json.dumps(results['simcompare'].to_dict(), sort_keys=True, default=str) == \
            json.dumps(results[PKG].to_dict(), sort_keys=True, default=str)
        checked_regions += sum(len(layer.regions) for layer in results['simcompare'].layers)
    if min_count is not None and name in ('shared', 'distinct', 'components') and diameter == 1:
        assert checked_regions > 0         # the generators really produce regions to compare


# --- 2. stage 4: candidates output tree -------------------------------------------------------------------------

CAND_RUNS = [
    ('shared', dict(k=2, window=1, overlap=0.5)),
    ('shared', dict(k=3, window=0.6, overlap=0.2, extra=('--max-candidates', '3'))),
    ('shared', dict(k=2, window=1, overlap=0.5, diameter=5)),
    ('distinct', dict(k=2, window=1, overlap=0.5)),
    ('distinct', dict(k=2, window=0.4, overlap=1.0)),
    ('multilayer', dict(k=2, window=1, overlap=0.5)),
    ('multilayer', dict(k=3, window=2, overlap=0.1, diameter=3)),
    ('components', dict(k=2, window=1, overlap=0.5)),
    ('components', dict(k=3, window=0.8, overlap=0.3, diameter=2, extra=('--max-candidates', '10'))),
    ('incomplete', dict(k=2, window=1, overlap=0.5)),
    ('noroles', dict(k=2, window=1, overlap=0.5)),
    ('policy', dict(k=2, window=1, overlap=0.5)),
    ('unicode', dict(k=2, window=1, overlap=0.5)),
    ('shared', dict(k=2, window=1, overlap=0.5, record=False)),
    ('shared', dict(k=2, window=1, overlap=0.5, manifest=False)),
    ('shared', dict(k=9, window=1, overlap=0.5)),               # no region reaches the threshold: empty tables
]


def candidates_argv(cfg, out, k, window, overlap, extra=()):
    return ['candidates', '--config', cfg, '--min-count', k, '--window-um', window, '--merge-overlap', overlap,
            '--out', out, *extra]


@pytest.mark.parametrize('name,opts', CAND_RUNS, ids=[f'{n}-{i}' for i, (n, _) in enumerate(CAND_RUNS)])
def test_candidates_tree_equals_frozen(stage46, name, opts):
    opts = dict(opts)
    base, ref, tgt = stage46.scenario(name)
    cfg = gen.write_config(base / f'cand_{abs(hash(json.dumps(opts, sort_keys=True))) % 10**8}.yaml', ref, tgt,
                           diameter_um=opts.pop('diameter', 1), record=opts.pop('record', True),
                           manifest=opts.pop('manifest', True))
    k, window, overlap, extra = opts['k'], opts['window'], opts['overlap'], opts.get('extra', ())
    production, _ = assert_equal_runs(lambda out: candidates_argv(cfg, out, k, window, overlap, extra),
                                      stage46.run_dir('cand'), f'{name}')
    assert production[0] in (0, 3)


def test_slim_export_expansion_reproduces_7bcb07a_bytes(stage46, tmp_path):
    """The one intended deviation of Phase B: rows reference the identity lists (`observed_ref` + top-level
    `region_coverages`). Expanding the production file gives, byte for byte, the file 7bcb07a writes for the same input
    (library level: same maps, same selections, same fixed producer_commit)."""
    checked = slim = 0
    for name in ('shared', 'distinct', 'multilayer', 'components', 'incomplete', 'unicode'):
        _, ref, tgt = stage46.scenario(name)
        for label, model in (('ref', ref), ('tgt', tgt)):
            spec = gen.side_config(model)
            written = {}
            for prefix in PREFIXES:
                dataset = datasets_for(prefix, model)
                maps = module(prefix, 'electrode_map').compute_electrode_maps(dataset, diameter_um=1, min_count=2)
                out = tmp_path / f'{name}_{label}_{prefix}'
                module(prefix, 'roi_export').export_review_rois(dataset, model.data_dir, out, maps=maps,
                                                                selections={'map_region_ids': 'all', 'manual': []},
                                                                asset_manifest=spec['asset_manifest'], producer_commit='fixed')
                written[prefix] = (out / 'review_rois.json').read_bytes()
            production = json.loads(written['simcompare'].decode('utf-8'))
            from simcompare.roi_export import expand_region_coverages
            expanded = (json.dumps(expand_region_coverages(production), ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
            assert expanded == written[PKG], (name, label)
            checked += 1
            if 'region_coverages' in production:
                slim += 1
                assert len(written['simcompare']) < len(written[PKG])
                assert b'"observed_ref"' in written['simcompare'] and b'"observed_ref"' not in written[PKG]
    assert slim >= 6 and checked == 12            # most scenarios really produce region rows (the others stay byte-equal)


# --- 3. stage 5: confirm-pairs (fed with the frozen candidates tree) --------------------------------------------

_CHAINS = {}


def chain_for(stage46, name, k=2, window=1):
    """frozen ``candidates`` run + DL2-style match files for one scenario (cached for the session)."""
    key = (name, k, window)
    if key not in _CHAINS:
        base, ref, tgt = stage46.scenario(name)
        cfg = gen.write_config(base / f'chain_{k}_{window}.yaml', ref, tgt, diameter_um=1)
        out = base / f'chain_cand_{k}_{window}'
        code, _, err = run_cli(PKG, candidates_argv(cfg, out, k, window, 0.5))
        assert code in (0, 3), err
        match_dir = base / f'chain_match_{k}_{window}'
        match_dir.mkdir()
        matches = gen.fake_matches(out, {'reference': ref, 'target': tgt}, match_dir, seed=len(name) + k)
        _CHAINS[key] = {'cfg': cfg, 'cand': out, 'matches': matches, 'base': base, 'ref': ref, 'tgt': tgt}
    return _CHAINS[key]


def confirm_argv(chain, out, decisions, *, tolerance=0.5, labelled_by='designer1', directions=None):
    argv = ['confirm-pairs', '--config', chain['cfg'], '--candidates', chain['cand'], '--decisions', decisions,
            '--labelled-by', labelled_by, '--mutual-tolerance-um', tolerance, '--out', out]
    for direction, path in chain['matches'].items():
        if directions is None or direction in directions:
            argv += ['--match', f'{direction}={path}']
    return argv


CONFIRM_RUNS = [(name, dict(seed=s, tolerance=t))
                for name in ('shared', 'distinct', 'multilayer', 'components', 'unicode', 'policy')
                for s, t in ((1, 0.5), (2, 0.05), (3, 3.0))] + [
    ('shared', dict(seed=4, tolerance=0.5, directions=('reference_to_target',))),
    ('shared', dict(seed=5, tolerance=0.5, accept=0.0)),          # nothing accepted: empty correspondence
    ('distinct', dict(seed=6, tolerance=0.5, accept=1.0)),        # everything rank 1 accepted
    ('shared', dict(seed=7, tolerance=0.5, labelled_by='검토자')),  # non-ASCII label
]


@pytest.mark.parametrize('name,opts', CONFIRM_RUNS, ids=[f'{n}-{i}' for i, (n, _) in enumerate(CONFIRM_RUNS)])
def test_confirm_pairs_tree_equals_frozen(stage46, name, opts):
    chain = chain_for(stage46, name)
    work = stage46.run_dir('confirm')
    given = {d: p for d, p in chain['matches'].items() if opts.get('directions') is None or d in opts['directions']}
    decisions = gen.decisions_file(work / 'decisions.csv', given, seed=opts['seed'], accept=opts.get('accept', 0.7))
    production, _ = assert_equal_runs(
        lambda out: confirm_argv(chain, out, decisions, tolerance=opts['tolerance'], labelled_by=opts.get('labelled_by', 'designer1'),
                                 directions=opts.get('directions')),
        work, name)
    assert production[0] in (0, 3), production[2]


# --- 4. stage 6: compare-windows --------------------------------------------------------------------------------

def windows_argv(cfg, correspondence, out, sizes, *, jitter=None, sort=None, compact=False):
    argv = ['compare-windows', '--config', cfg, '--correspondence', correspondence, '--window-sizes', *sizes, '--out', out]
    if jitter is not None:
        argv += ['--jitter-um', jitter]
    if sort is not None:
        argv += ['--sort-diameter-um', sort]
    if compact:
        argv += ['--compact-json']
    return argv


CLEAN = dict(blocked=0, outside=0, edge=0, duplicate=0)     # every centre is interior: windows complete, rows available

WINDOW_RUNS = [
    # (scenario, correspondence kwargs, sizes, cli kwargs, config kwargs)
    ('shared', dict(seed=1, n_pairs=6, repeats='uniform', **CLEAN), (0.3, 0.8), dict(jitter=0.1, sort=1), {}),
    ('shared', dict(seed=2, n_pairs=6, repeats='some', **CLEAN), (0.4, 1.0), dict(jitter=0.05, sort=5), {}),
    ('shared', dict(seed=3, n_pairs=5, repeats='none', **CLEAN), (0.5,), {}, {}),
    ('shared', dict(seed=4, n_pairs=6, repeats='uniform', **CLEAN), (0.3, 0.8), dict(compact=True, jitter=0.1), {}),
    ('shared', dict(seed=5, n_pairs=6, **CLEAN), (0.6,), dict(sort=3), dict(manifest=False)),
    ('shared', dict(seed=6, n_pairs=6, endpoints='a_only', **CLEAN), (0.5, 1.0), {}, {}),
    ('shared', dict(seed=7, n_pairs=6, endpoints='none', repeats='uniform', **CLEAN), (0.5, 1.0), dict(jitter=0.2), {}),
    ('shared', dict(seed=8, n_pairs=8, duplicate=3, edge=2, outside=0, blocked=0), (0.7, 1.4), dict(jitter=0.1, sort=2), {}),
    ('distinct', dict(seed=9, n_pairs=6, repeats='uniform', **CLEAN), (0.5, 1.2), dict(jitter=0.1, sort=1), {}),
    ('distinct', dict(seed=10, n_pairs=6, unmatched_endpoints=2, **CLEAN), (0.5, 1.2), dict(sort=2), {}),
    ('distinct', dict(seed=11, n_pairs=4, edge=3, outside=0, blocked=0, duplicate=0, margin=0.0), (0.4, 0.8), dict(jitter=0.3), {}),
    ('distinct', dict(seed=23, n_pairs=6), (0.5, 1.5), dict(jitter=0.1, sort=1), {}),               # blocked + outside + edge mix
    ('multilayer', dict(seed=12, n_pairs=6, repeats='uniform', **CLEAN), (0.5, 1.0), dict(jitter=0.1, sort=1), {}),
    ('multilayer', dict(seed=13, n_pairs=6, repeats='some', **CLEAN), (0.5, 3.0), dict(sort=2), {}),
    ('components', dict(seed=14, n_pairs=5, repeats='uniform', **CLEAN), (0.4, 8.0), dict(jitter=0.1, sort=1), {}),
    ('components', dict(seed=15, n_pairs=6, failure_type=False, **CLEAN), (0.8,), dict(compact=True), {}),
    ('unicode', dict(seed=16, n_pairs=5, repeats='uniform', **CLEAN), (0.5, 1.0), dict(jitter=0.1, sort=1), {}),
    ('incomplete', dict(seed=18, n_pairs=5, **CLEAN), (0.5, 1.0), dict(sort=1), {}),
    ('noroles', dict(seed=19, n_pairs=4, endpoints='none', **CLEAN), (0.5,), {}, {}),
    ('shared', dict(seed=20, n_pairs=0), (0.5,), {}, {}),                                  # empty pair table
    ('shared', dict(seed=21, n_pairs=3, blocked=3, outside=0, edge=0, duplicate=0), (0.5,), {}, {}),  # all blocked
]


def make_windows_inputs(stage46, name, corr_kwargs, config_kwargs, sizes=(0.5,), cli=None):
    base, ref, tgt = stage46.scenario(name)
    corr_kwargs = dict(corr_kwargs)
    corr_kwargs.setdefault('margin', max(sizes) / 2 + (cli or {}).get('jitter', 0.0) + 0.05)
    tag = hashlib.sha256(json.dumps([name, corr_kwargs, config_kwargs], sort_keys=True).encode()).hexdigest()[:10]
    cfg = gen.write_config(base / f'w_{tag}.yaml', ref, tgt, diameter_um=1, **config_kwargs)
    corr = gen.correspondence(base / f'c_{tag}.json', ref, tgt, **corr_kwargs)
    return cfg, corr


@pytest.mark.parametrize('name,corr,sizes,cli,conf', WINDOW_RUNS, ids=[f'{r[0]}-{i}' for i, r in enumerate(WINDOW_RUNS)])
def test_compare_windows_tree_equals_frozen(stage46, name, corr, sizes, cli, conf):
    cfg, correspondence = make_windows_inputs(stage46, name, corr, conf, sizes, cli)
    production, _ = assert_equal_runs(lambda out: windows_argv(cfg, correspondence, out, sizes, **cli),
                                      stage46.run_dir('windows'), name)
    assert production[0] in (0, 3), production[2]


def test_compare_windows_on_stage5_produced_correspondence(stage46):
    """end-to-end: candidates -> confirm-pairs (frozen) -> compare-windows production vs frozen."""
    ran = 0
    for name in ('shared', 'multilayer'):
        chain = chain_for(stage46, name)
        work = stage46.run_dir('e2e')
        decisions = gen.decisions_file(work / 'decisions.csv', chain['matches'], seed=3, accept=0.9)
        out = work / 'confirm'
        code, _, err = run_cli(PKG, confirm_argv(chain, out, decisions, tolerance=3.0))
        assert code in (0, 3), err
        correspondence = out / 'roi_correspondence.json'
        assert correspondence.is_file()
        production, _ = assert_equal_runs(
            lambda o: windows_argv(chain['cfg'], correspondence, o, (0.5, 1.0), jitter=0.1, sort=1), work, name)
        ran += production[0] in (0, 3)
    assert ran == 2


def _load_inputs(prefix, cfg_path, correspondence):
    pipeline = module(prefix, 'pipeline')
    cfg = pipeline.load_numeric_config(cfg_path)
    reference = pipeline._dataset_for(cfg, cfg.get('reference'), 'reference')
    target = pipeline._dataset_for(cfg, cfg.get('target'), 'target')
    corr = module(prefix, 'correspondence')
    payload = corr.load_correspondence(correspondence)
    return cfg, reference, target, corr.bind_pairs(payload, reference, target)


LIB_RUNS = [r for i, r in enumerate(WINDOW_RUNS) if r[0] in ('shared', 'distinct', 'multilayer', 'components', 'unicode') and r[1].get('n_pairs') and i % 4 == 0]


@pytest.mark.parametrize('name,corr,sizes,cli,conf', LIB_RUNS, ids=[f'{r[0]}-{i}' for i, r in enumerate(LIB_RUNS)])
def test_local_delta_result_equals_frozen(stage46, name, corr, sizes, cli, conf):
    cfg_path, correspondence = make_windows_inputs(stage46, name, corr, conf, sizes, cli)
    results = {}
    for prefix in PREFIXES:
        cfg, reference, target, pairs = _load_inputs(prefix, cfg_path, correspondence)
        results[prefix] = module(prefix, 'local_delta').compute_local_delta(
            pairs, reference, target, window_sizes_um=list(sizes), jitter_um=cli.get('jitter'), sort_diameter_um=cli.get('sort'))
    diff = deep_diff(results['simcompare'], results[PKG])
    assert not diff, diff
    assert json.dumps(results['simcompare'].to_dict(), sort_keys=True) == json.dumps(results[PKG].to_dict(), sort_keys=True)


# --- 5. asset variants: missing / invalid / mutated assets, same path in different roots, opposite semantics ------

def _manifest(model):
    return json.loads((model.data_dir / 'review_assets.json').read_text(encoding='utf-8'))


def _save_manifest(model, manifest):
    (model.data_dir / 'review_assets.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')


def _layout(manifest, case, slot):
    return manifest['cases'][case]['assets']['layouts'][slot]['asset']


def mutate_assets(model, kind, case=0, slot=0):
    """Apply one asset mutation to a cloned data_dir. Returns the manifest path/reference mutated (for reporting)."""
    from PIL import Image
    manifest = _manifest(model)
    ref = _layout(manifest, case, slot)
    path = model.data_dir / ref['path']
    if kind == 'missing':
        path.unlink()
    elif kind == 'truncated':
        path.write_bytes(path.read_bytes()[:40])
    elif kind == 'garbage':
        path.write_bytes(b'not a png at all')
    elif kind == 'wrong_content':           # a valid PNG with other bytes: manifest sha256 no longer matches
        other = model.data_dir / _layout(manifest, case + 1, 0)['path']
        path.write_bytes(other.read_bytes())
    elif kind == 'rewritten':               # different valid content at the same path, manifest sha updated
        with Image.open(path) as image:
            arr = np.array(image)
        arr[0, 0] = 255 - arr[0, 0]
        Image.fromarray(arr).save(path)
        new = hashlib.sha256(path.read_bytes()).hexdigest()
        for entry in manifest['cases']:
            for item in entry['assets']['layouts']:
                if item['asset']['path'] == ref['path']:
                    item['asset']['sha256'] = new
        _save_manifest(model, manifest)
    elif kind == 'semantics':               # same bytes, opposite value_semantics for one use of the path
        ref['value_semantics'] = 'binary'
        _save_manifest(model, manifest)
    elif kind == 'shared_path':             # two cases reference one asset
        manifest['cases'][case + 1]['assets']['layouts'][0]['asset'] = dict(ref)
        _save_manifest(model, manifest)
    elif kind == 'unobserved':
        ref['observed'] = False
        _save_manifest(model, manifest)
    else:
        raise KeyError(kind)


ASSET_KINDS = ('missing', 'truncated', 'wrong_content', 'rewritten', 'semantics', 'shared_path')


ASSET_CASES = [('shared', k) for k in ASSET_KINDS] + [('multilayer', k) for k in ('missing', 'rewritten', 'semantics')]


@pytest.mark.parametrize('scenario,kind', ASSET_CASES)
def test_asset_variants_candidates_and_windows_equal_frozen(stage46, scenario, kind):
    out, ref, tgt = stage46.clone(scenario, f'assets_{kind}')
    mutate_assets(tgt if scenario == 'multilayer' else ref, kind, case=1)
    cfg = gen.write_config(out / 'cfg.yaml', ref, tgt, diameter_um=1)
    production, _ = assert_equal_runs(lambda o: candidates_argv(cfg, o, 2, 1, 0.5), out, 'cand')
    assert production[0] in (0, 2, 3)
    if kind not in ('missing', 'rewritten', 'semantics', 'wrong_content'):
        return
    correspondence = gen.correspondence(out / 'c.json', ref, tgt, seed=5, n_pairs=3, repeats='uniform', margin=0.6, **CLEAN)
    production, _ = assert_equal_runs(lambda o: windows_argv(cfg, correspondence, o, (0.5, 1.0), jitter=0.1, sort=1), out, 'win')
    assert production[0] in (0, 2, 3)


def patch_reads(prefix, hook):
    """Wrap ``read_binary`` of one package's roi_export / numeric_images / local_delta_report (call-time lookups)."""
    patches = contextlib.ExitStack()
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    patches.callback(mp.undo)
    for name in ('roi_export', 'numeric_images', 'local_delta_report'):
        mod = module(prefix, name)
        original = getattr(mod, 'read_binary', None)
        if original is not None:
            mp.setattr(mod, 'read_binary', hook(original))
    return patches


def mutate_after_first_read(target_relative):
    """Hook factory: after the first read of ``target_relative`` truncate that file, so the next read fails its sha256."""
    def factory(original):
        def wrapped(root, relative, **kwargs):
            result = original(root, relative, **kwargs)
            if relative == target_relative:
                path = Path(root) / relative
                if path.exists() and not getattr(wrapped, 'done', False):
                    wrapped.done = True
                    path.write_bytes(path.read_bytes()[:40])
            return result
        return wrapped
    return factory


@pytest.mark.parametrize('scenario,case', [('shared', 1), ('distinct', 2)])
def test_asset_mutated_between_validation_and_use_equals_frozen(stage46, scenario, case):
    """check_asset_manifest reads every layout once; the page later re-reads it. The first read succeeds, the file
    is damaged afterwards: 7bcb07a keeps earlier masks, suppresses the later ones and records one global reason."""
    outcomes = []
    work = stage46.run_dir('between')
    for prefix in PREFIXES:
        out, ref, tgt = stage46.clone(scenario, 'between', dest=work / 'root')
        reference_path = _layout(_manifest(ref), case, 0)['path']
        cfg = gen.write_config(out / 'cfg.yaml', ref, tgt, diameter_um=1)
        correspondence = gen.correspondence(out / 'c.json', ref, tgt, seed=6, n_pairs=4, repeats='uniform', margin=0.6, **CLEAN)
        target = work / f'win_{prefix}'
        with patch_reads(prefix, mutate_after_first_read(reference_path)):
            result = run_cli(prefix, windows_argv(cfg, correspondence, target, (0.5, 1.0), jitter=0.1, sort=1))
        outcomes.append((result, target))
    problems = compare_runs(outcomes[0][0], outcomes[1][0], outcomes[0][1], outcomes[1][1])
    assert not problems, problems
    html = (outcomes[0][1] / 'local_delta.html').read_text(encoding='utf-8')
    assert 'hash_mismatch' in html      # the damaged second read is recorded as the page's global asset reason


@pytest.mark.parametrize('scenario,case', [('shared', 1), ('distinct', 2)])
def test_candidates_asset_mutated_after_its_first_read_equals_frozen(stage46, scenario, case):
    """Production memoises decoded assets (re-hashing the file on every hit); a layout damaged after its first successful
    read must block the same rows, with the same reasons, as 7bcb07a's repeated full decode does."""
    outcomes = []
    work = stage46.run_dir('cand_between')
    for prefix in PREFIXES:
        out, ref, tgt = stage46.clone(scenario, 'cand_between', dest=work / 'root')
        reference_path = _layout(_manifest(ref), case, 0)['path']
        cfg = gen.write_config(out / 'cfg.yaml', ref, tgt, diameter_um=1)
        target = work / f'cand_{prefix}'
        with patch_reads(prefix, mutate_after_first_read(reference_path)):
            result = run_cli(prefix, candidates_argv(cfg, target, 2, 1, 0.5))
        outcomes.append((result, target))
    problems = compare_runs(outcomes[0][0], outcomes[1][0], outcomes[0][1], outcomes[1][1])
    problems = [p for p in problems if 'review_rois.json' not in p and 'candidate_queries_v1.json' not in p]
    assert not problems, problems
    production, frozen = (_expanded(target) for _, target in outcomes)
    assert production == frozen
    assert any('input_hash_mismatch' in text for text in production.values())      # the damaged second read really blocked rows


def _expanded(folder):
    """The folder's exports in the 7bcb07a form, plus candidate_queries_v1.json without the recorded export hashes."""
    files = expand_slim_exports(tree(folder))
    result = {}
    for name, data in files.items():
        if name.endswith('review_rois.json') or name == 'candidate_queries_v1.json':
            result[name] = data.decode('utf-8')
    return result


# --- 6. the oracle must fail when production changes numerically -------------------------------------------------

def _perturb(monkeypatch, prefix_module, attribute):
    original = getattr(prefix_module, attribute)

    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        if dataclasses.is_dataclass(result) and getattr(result, 'observed_ca_um2', 0):
            return dataclasses.replace(result, observed_ca_um2=result.observed_ca_um2 * (1 + 1e-9))
        return result
    monkeypatch.setattr(prefix_module, attribute, wrapped)


def test_perturbation_in_stage6_union_is_caught(stage46, monkeypatch):
    cfg, correspondence = make_windows_inputs(stage46, 'shared', dict(seed=1, n_pairs=6, repeats='uniform', **CLEAN), {}, (0.3, 0.8),
                                              dict(jitter=0.1, sort=1))
    clean, _, _ = run_both(lambda o: windows_argv(cfg, correspondence, o, (0.3, 0.8), jitter=0.1, sort=1), stage46.run_dir('p6a'), 'clean')
    assert clean == []
    _perturb(monkeypatch, simcompare.local_delta, '_measure_cropped')
    problems, production, frozen = run_both(lambda o: windows_argv(cfg, correspondence, o, (0.3, 0.8), jitter=0.1, sort=1),
                                            stage46.run_dir('p6b'), 'perturbed')
    assert any('local_delta.json' in p or 'bytes differ' in p for p in problems), problems
    # the library-level comparison catches it too
    results = {}
    for prefix in PREFIXES:
        _, reference, target, pairs = _load_inputs(prefix, cfg, correspondence)
        results[prefix] = module(prefix, 'local_delta').compute_local_delta(pairs, reference, target, window_sizes_um=[0.3, 0.8])
    assert deep_diff(results['simcompare'], results[PKG])


def test_perturbation_in_stage4_measurement_is_caught(stage46, monkeypatch):
    _, ref, tgt = stage46.scenario('shared')
    clean = {p: module(p, 'electrode_map').compute_electrode_maps(datasets_for(p, ref), diameter_um=1, min_count=2) for p in PREFIXES}
    assert deep_diff(clean['simcompare'], clean[PKG]) == []
    _perturb(monkeypatch, simcompare.electrode_map, 'measure_union')
    perturbed = module('simcompare', 'electrode_map').compute_electrode_maps(datasets_for('simcompare', ref), diameter_um=1, min_count=2)
    assert deep_diff(perturbed, clean[PKG])


def test_perturbation_in_stage5_pair_building_is_caught(stage46, monkeypatch):
    chain = chain_for(stage46, 'shared')
    work = stage46.run_dir('p5')
    decisions = gen.decisions_file(work / 'decisions.csv', chain['matches'], seed=1)
    clean, _, _ = run_both(lambda o: confirm_argv(chain, o, decisions), work, 'clean')
    assert clean == []
    original = simcompare.confirm_pairs.pair_id_for
    monkeypatch.setattr(simcompare.confirm_pairs, 'pair_id_for',
                        lambda direction, roi, rank, centre: original(direction, roi, rank, [centre[0] + 1e-9, centre[1]]))
    problems, _, _ = run_both(lambda o: confirm_argv(chain, o, decisions), work, 'perturbed')
    assert problems
