"""Phase B T4: stage-6 writers (streamed JSON/CSV/HTML) and the compare-windows publication as one transaction.

The byte contract is the frozen 7bcb07a writer (tests/test_stage46_equivalence.py already runs the whole CLI both ways, full and
compact, empty / all-blocked / one-size / rank cases); this file pins the NEW machinery:
  * `_write_json` / `_write_csv` against `json.dumps(json_value(..))` and the frozen `_write_json_csv` on randomised tables,
  * streaming really avoids the second copy of the result (tracemalloc),
  * the whole compare-windows file set (json, csv, html, thumbs.json, png) is replaced as one generation: previous generation
    removed, unrelated files untouched, failures anywhere before the commit leave the previous generation, through the CLI too.
The filesystem protocol itself (fault at every step + restart recovery) is tests/test_set_publication.py.
"""
import dataclasses
import errno
import json
from pathlib import Path
import shutil
import tracemalloc

import numpy as np
import pytest

import test_stage46_equivalence as eq
from simcompare import local_delta, local_delta_report, pipeline, publication
from simcompare.numeric_schema import NumericContractError
from simcompare.report import json_value

gen = eq.gen
PKG = eq.PKG
CLEAN = eq.CLEAN
frozen_local_delta = eq.module(PKG, 'local_delta')


@pytest.fixture(scope='module')
def stage46(tmp_path_factory):
    class Factory:
        def scenario(self, name):
            return gen.scenario(tmp_path_factory, name)

        def run_dir(self, label):
            return Path(tmp_path_factory.mktemp(label))
    return Factory()


@pytest.fixture(autouse=True)
def pinned_clock(monkeypatch):
    for prefix in eq.PREFIXES:
        monkeypatch.setattr(eq.module(prefix, 'confirm_pairs'), 'utc_now_iso', lambda: eq.FIXED_CLOCK)


# --- 1. the streamed JSON / CSV writers ----------------------------------------------------------------------------------

@dataclasses.dataclass
class Leaf:
    value: float
    status: str
    reasons: tuple = ()
    arr: object = None


def random_value(rng, depth=0):
    kinds = ['int', 'float', 'str', 'none', 'bool', 'np_float', 'np_int', 'unicode', 'dataclass']
    if depth < 3:
        kinds += ['list', 'dict', 'tuple', 'long_list', 'empty']
    kind = kinds[rng.randint(len(kinds))]
    if kind == 'int':
        return int(rng.randint(-10 ** 9, 10 ** 9))
    if kind == 'float':
        return float(rng.choice([0.1, -0.0, 1e-300, 1e22, 3.5, rng.rand(), -rng.rand() * 1e5]))
    if kind == 'str':
        return 'a "quoted", \\ line\nbreak\ttab'
    if kind == 'unicode':
        return 'Δ 한글 ‰   emoji 😀'
    if kind == 'none':
        return None
    if kind == 'bool':
        return bool(rng.randint(2))
    if kind == 'np_float':
        return np.float64(rng.rand())
    if kind == 'np_int':
        return np.int64(rng.randint(100))
    if kind == 'dataclass':
        return Leaf(float(rng.rand()), 'available', ('r1', 'r2')[:rng.randint(3)], np.arange(rng.randint(4)))
    if kind == 'empty':
        return [{}, [], ()][rng.randint(3)]
    if kind == 'list':
        return [random_value(rng, depth + 1) for _ in range(rng.randint(0, 5))]
    if kind == 'tuple':
        return tuple(random_value(rng, depth + 1) for _ in range(rng.randint(0, 4)))
    if kind == 'long_list':
        return [random_value(rng, depth + 2) for _ in range(rng.randint(33, 80))]
    return {rng.choice(['k', 'Δ', 'é', 3, 4.5, 'x y']) if i % 2 else f'key{i}': random_value(rng, depth + 1)
            for i in range(rng.randint(0, 5))}


def expected_json(payload):
    return (json.dumps(json_value(payload), ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


@pytest.mark.parametrize('seed', range(12))
def test_streamed_json_equals_dumps_of_json_value(tmp_path, seed):
    rng = np.random.RandomState(seed)
    payload = {'schema_version': 'x', 'rows': [random_value(rng) for _ in range(rng.randint(0, 120))],
               'nested': random_value(rng), 'none': None, 'other': random_value(rng), 'unicode': 'Δ한글',
               'short': [random_value(rng, 2) for _ in range(3)]}
    path = tmp_path / 'p.json'
    local_delta._write_json(path, payload)
    assert path.read_bytes() == expected_json(payload)
    assert [p.name for p in tmp_path.iterdir()] == ['p.json']             # the temp sibling is gone


def test_streamed_json_edge_documents(tmp_path):
    for payload in ({}, {'rows': []}, {'rows': [None] * 40}, {'rows': [[]] * 40}, {'a': {'b': {'c': list(range(100))}}}, []):
        path = tmp_path / 'e.json'
        local_delta._write_json(path, payload)
        assert path.read_bytes() == expected_json(payload), payload


def test_streamed_json_rejects_nan_without_leaving_a_file(tmp_path):
    path = tmp_path / 'p.json'
    with pytest.raises(ValueError):
        local_delta._write_json(path, {'rows': [1.0] * 40 + [float('nan')]})
    assert list(tmp_path.iterdir()) == []                                  # neither the file nor its temp sibling
    path.write_bytes(b'previous')
    with pytest.raises(ValueError):
        local_delta._write_json(path, {'rows': [float('inf')] * 40})
    assert path.read_bytes() == b'previous' and [p.name for p in tmp_path.iterdir()] == ['p.json']


def test_lazy_list_is_empty_storage_so_only_the_python_encoder_may_walk_it():
    """The C encoder (json.dumps without indent) reads list storage directly: it would emit [] for a lazy list. _write_json
    always goes through iterencode (Python encoder); this pins why."""
    lazy = local_delta._lazy_json({'rows': list(range(100))})
    assert len(lazy['rows']) == 100 and list.__len__(lazy['rows']) == 0
    encoder = json.JSONEncoder(indent=2)
    assert json.loads(''.join(encoder.iterencode(lazy)))['rows'] == list(range(100))


def random_table(rng):
    keys = ['pair_id', 'Δ', 'a', 'b', 'status', 'reasons', 'x y', 'ünï']
    rows = []
    for _ in range(rng.randint(0, 40)):
        picked = [k for k in keys if rng.rand() < 0.7] or ['pair_id']
        rng.shuffle(picked)
        rows.append({k: random_value(rng, 2) if rng.rand() < 0.5 else [None, True, False, {'a': None}, ['x', 'y,z'], 'a,"b"\nc', 1.5, 7][rng.randint(8)]
                     for k in picked})
    return rows


@pytest.mark.parametrize('seed', range(12))
def test_streamed_csv_equals_frozen_writer(tmp_path, seed):
    rng = np.random.RandomState(100 + seed)
    tables = {'rows': random_table(rng), 'other': random_table(rng), 'empty': []}
    frozen_dir, new_dir = tmp_path / 'frozen', tmp_path / 'new'
    frozen_dir.mkdir(), new_dir.mkdir()
    frozen_local_delta._write_json_csv(frozen_dir, 'p', {'x': 1}, tables)
    for table, rows in tables.items():
        local_delta._write_csv(new_dir / f'p_{table}.csv', rows)
    for table in tables:
        assert (new_dir / f'p_{table}.csv').read_bytes() == (frozen_dir / f'p_{table}.csv').read_bytes(), table
    assert sorted(p.name for p in new_dir.iterdir()) == sorted(f'p_{t}.csv' for t in tables)


def test_streamed_csv_with_fixed_columns_streams_and_checks_every_row(tmp_path):
    columns = ('a', 'b', 'c')
    rows = [{'a': 1, 'b': None, 'c': [1, 'x']}, {'a': True, 'b': {'k': 1}, 'c': 'é,"q"'}]

    def produced():
        yield from rows
    path = tmp_path / 't.csv'
    local_delta._write_csv(path, produced(), columns)
    reference = tmp_path / 'ref'
    reference.mkdir()
    frozen_local_delta._write_json_csv(reference, 'ref', {}, {'t': rows})
    assert path.read_bytes() == (reference / 'ref_t.csv').read_bytes()
    local_delta._write_csv(path, iter([]), columns)                          # empty table: the placeholder header, not the columns
    assert path.read_text() == 'value,status,reasons\n'
    with pytest.raises(ValueError):
        local_delta._write_csv(path, iter([{'a': 1, 'b': 2, 'c': 3}, {'a': 1, 'c': 3, 'b': 2}]), columns)
    assert path.read_text() == 'value,status,reasons\n'                      # the failed write replaced nothing


def _result_for(stage46, **kwargs):
    corr = dict(seed=3, n_pairs=8, repeats='uniform', **CLEAN)
    sizes = kwargs.pop('sizes', (0.3, 0.8))
    cfg_path, correspondence = eq.make_windows_inputs(stage46, 'shared', corr, {}, sizes, dict(jitter=0.1))
    _, reference, target, pairs = eq._load_inputs('simcompare', cfg_path, correspondence)
    result = local_delta.compute_local_delta(pairs, reference, target, window_sizes_um=list(sizes), jitter_um=0.1,
                                             sort_diameter_um=1)
    return cfg_path, correspondence, reference, target, result


def test_numeric_writers_equal_the_frozen_writers_on_a_real_result_and_a_multiplied_one(stage46, tmp_path):
    _, _, _, _, result = _result_for(stage46)
    many = dataclasses.replace(result, rows=result.rows * 5)
    for label, res in (('real', result), ('many', many)):
        for compact in (False, True):
            new_dir, old_dir = tmp_path / f'{label}{compact}_new', tmp_path / f'{label}{compact}_old'
            new_paths = local_delta.write_local_delta(new_dir, res, compact=compact)
            old_paths = frozen_local_delta.write_local_delta(old_dir, res, compact=compact)
            assert [Path(p).name for p in new_paths] == [Path(p).name for p in old_paths]
            assert eq.tree(new_dir) == eq.tree(old_dir), (label, compact)


@pytest.mark.parametrize('compact', [False, True])
def test_empty_result_writers_equal_the_frozen_writers(tmp_path, compact):
    results = {}
    for prefix in eq.PREFIXES:
        mod = eq.module(prefix, 'local_delta')
        sensitivity = mod.window_sensitivity([], (0.5,), None)
        results[prefix] = mod.LocalDeltaResult(
            pairs=[], window_sizes_um=(0.5,), jitter_um=None, sort_diameter_um=None, rows=[], ranking={}, sensitivity=sensitivity,
            products={'a': {'product_id': 'Δ제품'}, 'b': {'product_id': 'B'}}, provenance={'is_synthetic': True, 'k': 'é'},
            status='unavailable', reasons=('no_rows',), explained=None)
    for prefix, name in (('simcompare', 'new'), (PKG, 'old')):
        out = tmp_path / name
        eq.module(prefix, 'local_delta').write_local_delta(out, results[prefix], compact=compact)
        eq.module(prefix, 'local_delta_report').write_local_delta_html(out, results[prefix])
    assert eq.tree(tmp_path / 'new') == eq.tree(tmp_path / 'old')
    assert (tmp_path / 'new' / 'local_delta_rows.csv').read_text() == 'value,status,reasons\n'


def test_streaming_writers_do_not_build_a_second_copy_of_the_rows(stage46, tmp_path):
    _, _, _, _, result = _result_for(stage46)
    many = dataclasses.replace(result, rows=result.rows * 12)

    def peak(function, out):
        tracemalloc.start()
        try:
            function(out, many)
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
    old = peak(frozen_local_delta.write_local_delta, tmp_path / 'old')
    new = peak(local_delta.write_local_delta, tmp_path / 'new')
    assert eq.tree(tmp_path / 'new') == eq.tree(tmp_path / 'old')
    assert new * 4 < old, (new, old)


def test_html_page_is_written_row_by_row_and_a_failure_leaves_no_page(stage46, tmp_path, monkeypatch):
    cfg_path, _, reference, target, result = _result_for(stage46)
    cfg = pipeline.load_numeric_config(cfg_path)
    real_row = local_delta_report._row_html
    out = tmp_path / 'page'
    open_pages = []

    def row(*args, **kwargs):
        open_pages.append(len([p for p in out.iterdir() if p.name.startswith('.local_delta.html.') and p.name.endswith('.tmp')]))
        if len(open_pages) == 3:
            raise OSError(errno.ENOSPC, 'disk full')
        return real_row(*args, **kwargs)
    monkeypatch.setattr(local_delta_report, '_row_html', row)
    with pytest.raises(OSError):
        local_delta_report.write_local_delta_html(out, result, cfg=cfg, reference=reference, target=target)
    assert open_pages == [1, 1, 1]                                            # the page is a file being written, not a string
    assert not (out / 'local_delta.html').exists()
    assert not [p for p in out.iterdir() if p.name.endswith('.tmp')]


# --- 2. compare-windows publishes the whole file set as one generation --------------------------------------------------

def run_windows(stage46, out, seed, *, compact=False, name='shared', sizes=(0.3, 0.8)):
    corr = dict(seed=seed, n_pairs=6, repeats='uniform', **CLEAN)
    cfg_path, correspondence = eq.make_windows_inputs(stage46, name, corr, {}, sizes, dict(jitter=0.1))
    code, stdout, stderr = eq.run_cli('simcompare', eq.windows_argv(cfg_path, correspondence, out, sizes, jitter=0.1, sort=1,
                                                                  compact=compact))
    return code, stdout, stderr


def cli_free(tree):
    return {k: v for k, v in tree.items() if k not in ('execution.json', 'error.json')}


UNRELATED = {'notes.txt': b'my notes', 'local_delta_notes.csv': b'user csv', 'window_sensitivity_extra.json': b'{}',
             'comparison.json': b'{"x":1}', 'sub/inner.txt': b'inner', 'local_delta_thumb_XYZ.png': b'png-ish'}


def add_unrelated(out):
    for name, data in UNRELATED.items():
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_bytes(data)


def test_a_new_run_replaces_the_previous_generation_as_a_whole(stage46, tmp_path):
    out, fresh = tmp_path / 'out', tmp_path / 'fresh'
    assert run_windows(stage46, out, 3)[0] in (0, 3)
    first = cli_free(eq.tree(out))
    assert any(name.endswith('.png') for name in first)
    add_unrelated(out)
    assert run_windows(stage46, out, 7)[0] in (0, 3)
    assert run_windows(stage46, fresh, 7)[0] in (0, 3)
    after = cli_free(eq.tree(out))
    expected = cli_free(eq.tree(fresh))
    for name, data in UNRELATED.items():
        assert after.pop(name) == data                                         # untouched, even the look-alikes
    assert {k: eq._normalise(v, out) for k, v in after.items()} == {k: eq._normalise(v, fresh) for k, v in expected.items()}
    gone = set(first) - set(expected)
    assert gone and not (gone & set(after))                                    # the previous generation's own files do not linger
    assert not [n for n in after if n.startswith('.')]                         # no control files, no scratch


def test_returned_outputs_are_the_published_files(stage46, tmp_path):
    cfg_path, correspondence = eq.make_windows_inputs(stage46, 'shared', dict(seed=3, n_pairs=6, repeats='uniform', **CLEAN), {},
                                                      (0.3, 0.8), dict(jitter=0.1))
    cfg = pipeline.load_numeric_config(cfg_path)
    out = tmp_path / 'out'
    result = pipeline.run_numeric_local_delta(cfg, out, correspondence=correspondence, window_sizes_um=[0.3, 0.8], jitter_um=0.1,
                                              sort_diameter_um=1)
    published = {p.name for p in out.iterdir()}
    assert published and all(publication.LOCAL_DELTA_FILES.fullmatch(name) for name in published)
    assert {Path(p).name for p in result['outputs']} == published
    assert all(Path(p).parent == out and Path(p).is_file() for p in result['outputs'])


FAULTS = [
    ('png_enospc', 'local_delta_report', '_png_file', 3, OSError(errno.ENOSPC, 'disk full')),
    ('row_html', 'local_delta_report', '_row_html', 2, OSError(errno.EIO, 'io')),
    ('json_enospc', 'local_delta', '_write_json', 2, OSError(errno.ENOSPC, 'disk full')),
    ('csv_cell', 'local_delta', '_csv_cell', 60, OSError(errno.EIO, 'io')),
    ('csv_value', 'local_delta', '_write_csv', 3, ValueError('bad cell')),
    ('overview_interrupt', 'local_delta_report', '_overview_png', 1, KeyboardInterrupt()),
    ('compute_interrupt', 'local_delta', 'compute_local_delta', 1, KeyboardInterrupt()),
    ('compute_error', 'local_delta', 'compute_local_delta', 1, NumericContractError('boom', 'compute failed')),
]


def failing(monkeypatch, module_name, attr, at, exc):
    module = eq.module('simcompare', module_name)
    real = getattr(module, attr)
    state = {'n': 0}

    def call(*args, **kwargs):
        state['n'] += 1
        if state['n'] == at:
            raise exc
        return real(*args, **kwargs)
    monkeypatch.setattr(module, attr, call)
    return state


@pytest.mark.parametrize('previous', [True, False], ids=['has_previous', 'first_run'])
@pytest.mark.parametrize('fault', FAULTS, ids=[f[0] for f in FAULTS])
def test_any_failure_before_the_commit_keeps_the_previous_generation(stage46, tmp_path, monkeypatch, fault, previous):
    label, module_name, attr, at, exc = fault
    out = tmp_path / 'out'
    if previous:
        assert run_windows(stage46, out, 3)[0] in (0, 3)
        add_unrelated(out)
    before = cli_free(eq.tree(out)) if previous else {}
    state = failing(monkeypatch, module_name, attr, at, exc)
    if isinstance(exc, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            run_windows(stage46, out, 7)
        assert state['n'] >= at
    else:
        code, _, stderr = run_windows(stage46, out, 7)
        assert state['n'] >= at and code == 2, stderr
        assert (out / 'error.json').is_file()                                  # the failure marker is written next to the kept set
    monkeypatch.undo()
    after = eq.tree(out) if out.exists() else {}
    assert cli_free(after) == before                                           # bytes, names and unrelated files unchanged
    assert not [n for n in after if n.startswith('.')]                          # scratch is gone
    # and the directory is not wedged: the next run publishes
    assert run_windows(stage46, out, 7)[0] in (0, 3)
    assert not (out / 'error.json').exists()


def test_report_phase_admission_failure_keeps_the_previous_generation_whole(stage46, tmp_path, monkeypatch):
    """Before this change the numeric files were already replaced when the page guard refused."""
    import simcompare.adapters.real_hotspot as real_hotspot
    out = tmp_path / 'out'
    assert run_windows(stage46, out, 3)[0] in (0, 3)
    before = cli_free(eq.tree(out))
    real = real_hotspot.guard_stage_compare_windows

    def guard(*args, **kwargs):
        if kwargs.get('phase') == 'report':
            raise NumericContractError('oversized_real_operation', 'compare-windows report: estimated 9 bytes exceed limit 1')
        return real(*args, **kwargs)
    monkeypatch.setattr(real_hotspot, 'guard_stage_compare_windows', guard)
    code, _, stderr = run_windows(stage46, out, 7)
    assert code == 2 and 'oversized_real_operation' in stderr
    monkeypatch.undo()
    assert cli_free(eq.tree(out)) == before


def test_publication_failure_after_the_journal_rolls_back_and_the_cli_keeps_the_reports(stage46, tmp_path, monkeypatch):
    out = tmp_path / 'out'
    assert run_windows(stage46, out, 3)[0] in (0, 3)
    add_unrelated(out)
    before = cli_free(eq.tree(out))
    real = publication.SetPublication._replace
    state = {'n': 0}

    def replace(source, destination):
        if Path(str(source)).parent.name.startswith('.local_delta.staging.'):
            state['n'] += 1
            if state['n'] == 4:                                                 # the 4th new file, after the journal and backups
                raise OSError(errno.ENOSPC, 'disk full')
        return real(source, destination)
    monkeypatch.setattr(publication.SetPublication, '_replace', staticmethod(replace))
    code, _, stderr = run_windows(stage46, out, 7)
    assert code == 2 and state['n'] >= 4, stderr
    monkeypatch.undo()
    after = eq.tree(out)
    assert cli_free(after) == before and not [n for n in after if n.startswith('.')]


def test_unreadable_recovery_state_is_an_error_and_the_cli_leaves_everything_as_found(stage46, tmp_path):
    out = tmp_path / 'out'
    assert run_windows(stage46, out, 3)[0] in (0, 3)
    (out / '.local_delta.publishing').write_text('{ not json')
    before = cli_free(eq.tree(out))
    code, _, stderr = run_windows(stage46, out, 7)
    assert code == 2 and 'local_delta_recovery_unreadable' in stderr
    assert cli_free(eq.tree(out)) == before                                     # reports AND the rollback material kept
    err = json.loads((out / 'error.json').read_text(encoding='utf-8'))
    assert err['reasons'] == ['local_delta_recovery_unreadable']


def test_input_errors_before_the_publication_keep_the_previous_generation_too(stage46, tmp_path):
    """RB-A3: an error before the transaction opened (here: the correspondence file is missing) leaves the last good generation;
    cli.py writes error.json / execution.json only."""
    out = tmp_path / 'out'
    assert run_windows(stage46, out, 3)[0] in (0, 3)
    before = cli_free(eq.tree(out))
    cfg_path, _ = eq.make_windows_inputs(stage46, 'shared', dict(seed=3, n_pairs=6, repeats='uniform', **CLEAN), {}, (0.3, 0.8),
                                         dict(jitter=0.1))
    code, _, _ = eq.run_cli('simcompare', eq.windows_argv(cfg_path, tmp_path / 'missing.json', out, (0.3, 0.8)))
    assert code == 2
    assert cli_free(eq.tree(out)) == before
    assert json.loads((out / 'error.json').read_text(encoding='utf-8'))['exit_code'] == 2


def test_crash_between_files_is_repaired_by_the_next_run(stage46, tmp_path, monkeypatch):
    """A process that dies right after the 4th new file replaced leaves a mixed directory; the next compare-windows
    restores the previous generation first (nothing was committed) and then publishes its own."""
    out = tmp_path / 'out'
    assert run_windows(stage46, out, 3)[0] in (0, 3)
    before = cli_free(eq.tree(out))

    class Died(BaseException):
        pass
    real = {name: getattr(publication.SetPublication, name) for name in ('_replace', '_unlink', '_rmtree', '_rmdir', '_present')}
    state = {'n': 0, 'dead': False}

    def replace(source, destination):
        if state['dead']:
            raise Died()
        if Path(str(source)).parent.name.startswith('.local_delta.staging.'):
            state['n'] += 1
            if state['n'] == 4:
                state['dead'] = True
                raise Died()
        return real['_replace'](source, destination)

    def dead(name):
        def call(*args):
            if state['dead']:
                raise Died()
            return real[name](*args)
        return call
    monkeypatch.setattr(publication.SetPublication, '_replace', staticmethod(replace))
    for name in ('_unlink', '_rmtree', '_rmdir', '_present'):
        monkeypatch.setattr(publication.SetPublication, name, staticmethod(dead(name)))
    with pytest.raises(Died):
        run_windows(stage46, out, 7)
    monkeypatch.undo()
    mixed = eq.tree(out)
    assert cli_free(mixed) != before and '.local_delta.publishing' in mixed       # torn, and visibly so
    assert run_windows(stage46, out, 7)[0] in (0, 3)
    fresh = tmp_path / 'fresh'
    assert run_windows(stage46, fresh, 7)[0] in (0, 3)
    got, want = cli_free(eq.tree(out)), cli_free(eq.tree(fresh))
    assert {k: eq._normalise(v, out) for k, v in got.items()} == {k: eq._normalise(v, fresh) for k, v in want.items()}
    shutil.rmtree(fresh)
