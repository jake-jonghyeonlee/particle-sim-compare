"""Phase B T5: stage-2 `compare` through the CLI keeps the previous comparison generation on failures after its publication opened.

pipeline._ComparisonStream preserves comparison.json / comparison.csv / comparison_masks.zip until its commit; cli.py's failure handler
used to delete them on any exit-2 error. The contract (same as compare-windows): a failure from stream.begin() on keeps the managed
reports; input / config errors before that keep the documented "remove managed reports" rule."""
import errno
import json
from pathlib import Path

import pytest

import test_stage46_equivalence as eq
from test_stage6_scale import compare_config, stage46, pinned_clock, files  # noqa: F401  (fixtures)
from simcompare import linepixel, pipeline
from simcompare.numeric_schema import NumericContractError

GENERATION = ('comparison.csv', 'comparison.json', 'comparison_masks.zip')
UNRELATED = {'notes.txt': b'my notes', 'comparison_notes.zip': b'user zip', 'sub/inner.txt': b'inner'}


def generation(folder):
    return {n: (Path(folder) / n).read_bytes() for n in GENERATION}


def run(cfg):
    return eq.run_cli('simcompare', ['compare', '--config', cfg])


def published(stage46, label):
    out, cfg, corr, cmp_dir, ref, tgt = compare_config(stage46, 'shared', label)
    assert run(cfg)[0] == 3
    for name, data in UNRELATED.items():
        (cmp_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (cmp_dir / name).write_bytes(data)
    return cfg, cmp_dir, generation(cmp_dir)


def assert_kept(cmp_dir, before, stderr=''):
    assert generation(cmp_dir) == before, stderr
    assert not [p.name for p in cmp_dir.iterdir() if p.name.startswith('.')]          # no temp / control files left
    for name, data in UNRELATED.items():
        assert (cmp_dir / name).read_bytes() == data
    err = json.loads((cmp_dir / 'error.json').read_text(encoding='utf-8'))
    assert err['exit_code'] == 2 and err['command'] == 'compare'


class Counter:
    def __init__(self, monkeypatch, owner, attr, at, exc):
        self.n, self.at = 0, at
        real = getattr(owner, attr)

        def call(*args, **kwargs):
            self.n += 1
            if self.n == self.at:
                raise exc
            return real(*args, **kwargs)
        monkeypatch.setattr(owner, attr, call)


FAULTS = [
    ('target_compute_contract_error', linepixel, 'compute_areas', 2, NumericContractError('boom', 'target compute failed')),
    ('target_compute_value_error', linepixel, 'compute_areas', 2, ValueError('bad target')),
    ('target_side_write_enospc', pipeline._ComparisonStream, 'side', 2, OSError(errno.ENOSPC, 'disk full')),
    ('reference_side_write_enospc', pipeline._ComparisonStream, 'side', 1, OSError(errno.ENOSPC, 'disk full')),
    ('commit_enospc', pipeline._ComparisonStream, 'commit', 1, OSError(errno.ENOSPC, 'disk full')),
]


@pytest.mark.parametrize('fault', FAULTS, ids=[f[0] for f in FAULTS])
def test_failure_after_the_publication_opened_keeps_the_previous_generation(stage46, monkeypatch, fault):
    _, owner, attr, at, exc = fault
    cfg, cmp_dir, before = published(stage46, 'cmp_fault_' + fault[0])
    state = Counter(monkeypatch, owner, attr, at, exc)
    code, _, stderr = run(cfg)
    assert state.n >= at and code == 2, stderr
    monkeypatch.undo()
    assert_kept(cmp_dir, before, stderr)
    assert run(cfg)[0] == 3                                                            # not wedged: the next run publishes
    assert not (cmp_dir / 'error.json').exists()


def test_unreadable_recovery_state_is_an_error_and_everything_is_left_as_found(stage46):
    cfg, cmp_dir, before = published(stage46, 'cmp_unreadable')
    (cmp_dir / '.comparison.publishing').write_text('{ not json')
    code, _, stderr = run(cfg)
    assert code == 2 and 'comparison_recovery_unreadable' in stderr
    assert generation(cmp_dir) == before and (cmp_dir / '.comparison.publishing').read_text() == '{ not json'
    err = json.loads((cmp_dir / 'error.json').read_text(encoding='utf-8'))
    assert err['reasons'] == ['comparison_recovery_unreadable']


def test_publication_failure_after_the_journal_rolls_back_and_keeps_the_reports(stage46, monkeypatch):
    cfg, cmp_dir, before = published(stage46, 'cmp_midpublish')
    real = pipeline.os.replace
    state = {'n': 0}

    def replace(source, destination, *args, **kwargs):
        if Path(str(source)).name.startswith('.comparison') and str(source).endswith('.tmp'):
            state['n'] += 1
            if state['n'] == 2:                                                        # after the journal and the backups
                raise OSError(errno.ENOSPC, 'disk full')
        return real(source, destination, *args, **kwargs)
    monkeypatch.setattr(pipeline.os, 'replace', replace)
    code, _, stderr = run(cfg)
    monkeypatch.undo()
    assert code == 2 and state['n'] >= 2, stderr
    assert_kept(cmp_dir, before, stderr)


def test_input_errors_before_the_publication_keep_the_previous_generation_too(stage46):
    """RB-A3: an error before the publication opened (here: the pair table is missing) no longer deletes the last good
    comparison.json / .csv / comparison_masks.zip; error.json is written next to them."""
    cfg, cmp_dir, before = published(stage46, 'cmp_input_error')
    text = Path(cfg).read_text(encoding='utf-8').replace('pair_table_path: pairs.csv', 'pair_table_path: missing_pairs.csv', 1)
    broken = Path(cfg).with_name('broken.yaml')
    broken.write_text(text, encoding='utf-8')
    code, _, stderr = run(broken)
    assert code == 2, stderr
    assert_kept(cmp_dir, before, stderr)
    assert run(cfg)[0] == 3 and not (cmp_dir / 'error.json').exists()


def _data_dir(cfg, which):
    import re
    text = Path(cfg).read_text(encoding='utf-8')
    raw = re.findall(r'data_dir:\s*(\S+)', text)[which]
    return Path(raw) if Path(raw).is_absolute() else Path(cfg).parent / raw


def test_a_tampered_reference_manifest_keeps_the_previous_generation(stage46):
    """F3/B1: a manifest validation error is raised before the real branch's try; mode=real still keeps the old files."""
    cfg, cmp_dir, before = published(stage46, 'cmp_tampered_manifest')
    manifest = _data_dir(cfg, 0) / 'manifest.json'
    payload = json.loads(manifest.read_text(encoding='utf-8'))
    payload['expected_case_coverage_status'] = ('unconfirmed' if payload.get('expected_case_coverage_status') == 'confirmed'
                                                else 'confirmed')
    manifest.write_text(json.dumps(payload), encoding='utf-8')
    code, _, stderr = run(cfg)
    assert code == 2, stderr
    assert_kept(cmp_dir, before, stderr)


def test_a_moved_import_folder_keeps_the_previous_generation(stage46):
    """F3/B1: the restart step 'move data/imported/target away' then stage 2 -> missing_or_escaping_path, files kept."""
    import shutil
    cfg, cmp_dir, before = published(stage46, 'cmp_moved_import')
    target = _data_dir(cfg, 1)
    shutil.move(str(target), str(target) + '_old')
    code, _, stderr = run(cfg)
    assert code == 2, stderr
    assert_kept(cmp_dir, before, stderr)


def test_synthetic_compare_errors_still_use_the_old_removal_rule():
    for mode, kept in (('real', True), ('synthetic', False)):
        with pytest.raises(NumericContractError) as caught:
            pipeline.run_numeric_compare({'mode': mode, 'reference': None, 'target': None, 'output_dir': 'x'})
        assert getattr(caught.value, 'keeps_previous_outputs', False) is kept


def test_a_masks_writer_failure_leaves_one_generation_without_the_previous_artefact(stage46, monkeypatch):
    """RB-A4: when the artefact cannot be built (non-I/O error) the previous comparison_masks.zip is journalled and removed
    with the swap, so the published set is the new json/csv only."""
    from simcompare import compare_masks
    cfg, cmp_dir, before = published(stage46, 'cmp_masks_dropped')
    monkeypatch.setattr(compare_masks.MasksWriter, 'close', lambda self, *a, **k: (_ for _ in ()).throw(TypeError('numpy.float16')))
    code, _, stderr = run(cfg)
    monkeypatch.undo()
    assert code == 3 and 'comparison_masks.zip not written' in stderr, stderr
    assert not (cmp_dir / 'comparison_masks.zip').exists()
    assert (cmp_dir / 'comparison.json').read_bytes() == before['comparison.json']
    assert not [p.name for p in cmp_dir.iterdir() if p.name.startswith('.')]
    assert run(cfg)[0] == 3 and (cmp_dir / 'comparison_masks.zip').exists()


def test_a_dropped_artefact_is_restored_when_the_swap_is_interrupted(stage46, monkeypatch):
    """RB-A4: the removal of the previous artefact is inside the transaction: a failure before the commit restores it."""
    from simcompare import compare_masks
    cfg, cmp_dir, before = published(stage46, 'cmp_masks_dropped_rollback')
    monkeypatch.setattr(compare_masks.MasksWriter, 'close', lambda self, *a, **k: (_ for _ in ()).throw(TypeError('numpy.float16')))
    real = pipeline._ComparisonStream._unlink

    def failing(path):
        if Path(str(path)).name == 'comparison_masks.zip':
            raise OSError(errno.EIO, 'cannot remove')
        return real(path)
    monkeypatch.setattr(pipeline._ComparisonStream, '_unlink', staticmethod(failing))
    code, _, stderr = run(cfg)
    monkeypatch.undo()
    assert code == 2, stderr
    assert_kept(cmp_dir, before, stderr)


def test_windows_pid_liveness_uses_a_private_kernel32_and_unknown_means_alive(monkeypatch):
    """F3/A2: the nt branch uses WinDLL(use_last_error=True) + get_last_error; every doubt keeps the file."""
    import ctypes
    import types
    from simcompare import publication
    calls = {}

    class Fn:
        def __init__(self, fn):
            self.fn, self.restype, self.argtypes = fn, None, None

        def __call__(self, *args):
            return self.fn(*args)

    def install(handle, code=0, exit_ok=True, last_error=0):
        def exit_code(_, ref):
            ref._obj.value = code
            return exit_ok
        wrapped = types.SimpleNamespace(OpenProcess=Fn(lambda access, inherit, pid: handle),
                                        GetExitCodeProcess=Fn(exit_code), CloseHandle=Fn(lambda h: None))

        def windll(name, use_last_error=False):
            calls['args'] = (name, use_last_error)
            return wrapped
        monkeypatch.setattr(publication.os, 'name', 'nt')
        monkeypatch.setattr(ctypes, 'WinDLL', windll, raising=False)
        monkeypatch.setattr(ctypes, 'get_last_error', lambda: last_error, raising=False)
        return wrapped

    wrapped = install(0, last_error=87)
    assert publication.pid_alive(4242) is False                      # ERROR_INVALID_PARAMETER: no such process
    assert calls['args'] == ('kernel32', True) and wrapped.OpenProcess.restype is not None
    install(0, last_error=5)
    assert publication.pid_alive(4242) is True                       # access denied: alive
    install(7, code=259)
    assert publication.pid_alive(4242) is True                       # STILL_ACTIVE
    install(7, code=0)
    assert publication.pid_alive(4242) is False                      # exited
    install(7, exit_ok=False)
    assert publication.pid_alive(4242) is True                       # cannot tell: alive
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **k: (_ for _ in ()).throw(OSError('no kernel32')), raising=False)
    assert publication.pid_alive(4242) is True                       # any failure: alive


def _without_condition_assignment(cfg):
    import yaml
    data = yaml.safe_load(Path(cfg).read_text(encoding='utf-8'))
    for side in ('reference', 'target'):
        data[side].pop('condition_assignment')
    stripped = Path(cfg).with_name('inspection.yaml')
    stripped.write_text(yaml.safe_dump(data, sort_keys=True, allow_unicode=True), encoding='utf-8')
    return stripped


def test_a_failing_non_transactional_inspection_writer_leaves_no_managed_comparison_files(stage46, monkeypatch):
    """F4/B2: without condition_assignment compare writes comparison.json/.csv straight into output_dir (no publication
    transaction), so an ENOSPC midway must not leave new files beside the old masks zip: cli removes the reports as before."""
    import csv
    cfg, cmp_dir, before = published(stage46, 'cmp_inspection_enospc')
    inspection = _without_condition_assignment(cfg)

    def full_disk(self, rows):
        raise OSError(errno.ENOSPC, 'disk full')
    monkeypatch.setattr(csv.DictWriter, 'writerows', full_disk)
    code, _, stderr = run(inspection)
    monkeypatch.undo()
    assert code == 2, stderr
    assert not [n for n in GENERATION if (cmp_dir / n).exists()], sorted(p.name for p in cmp_dir.iterdir())
    for name, data in UNRELATED.items():
        assert (cmp_dir / name).read_bytes() == data
    err = json.loads((cmp_dir / 'error.json').read_text(encoding='utf-8'))
    assert err['exit_code'] == 2 and err['command'] == 'compare'


def test_numeric_v2_real_mode_writer_failure_is_not_flagged_as_keeping_outputs(monkeypatch, tmp_path):
    """F4/B2: the other non-transactional real-mode writer (write_numeric_report) must not set keeps_previous_outputs."""
    from simcompare import report
    monkeypatch.setattr(pipeline, '_real_source', lambda cfg, side: None)
    monkeypatch.setattr(pipeline, '_numeric_input', lambda cfg, side: (None, None))
    monkeypatch.setattr(pipeline, '_numeric_rates', lambda dataset, side: None)
    monkeypatch.setattr(linepixel, 'compare_rates', lambda *a, **k: None)
    monkeypatch.setattr(report, 'comparison_report', lambda *a: ({'status': 'available', 'reasons': []}, {}))

    def full_disk(*args, **kwargs):
        raise OSError(errno.ENOSPC, 'disk full')
    monkeypatch.setattr(report, 'write_numeric_report', full_disk)
    cfg = {'mode': 'real', 'reference': {'product_id': 'a'}, 'target': {'product_id': 'b'}, 'output_dir': str(tmp_path)}
    with pytest.raises(OSError) as caught:
        pipeline.run_numeric_compare(cfg)
    assert caught.value.keeps_previous_outputs is False
