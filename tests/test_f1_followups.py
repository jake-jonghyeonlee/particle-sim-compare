"""F1: review advisories RB-A3 (compare-windows / cleanup), RB-A10 (stale temps), RB-A12, EC-A2, EC-A4."""
import json
import os
from pathlib import Path

import pytest

import test_stage46_equivalence as eq
from test_stage6_scale import compare_config, stage46, pinned_clock  # noqa: F401  (fixtures)
from simcompare import cli


def run_windows(cfg, corr, out, sizes=(0.3, 0.8)):
    return eq.run_cli('simcompare', eq.windows_argv(cfg, corr, out, sizes, jitter=0.1, sort=1))


def snapshot(folder):
    return {p.name: p.read_bytes() for p in sorted(Path(folder).iterdir()) if p.is_file() and p.name not in ('error.json', 'execution.json')}


# -- RB-A3 --------------------------------------------------------------------------------------------------------------
def test_compare_windows_error_before_the_publication_keeps_the_previous_generation(stage46):
    out, cfg, corr, _, _, _ = compare_config(stage46, 'shared', 'f1_windows_pre_begin')
    target = out / 'w'
    assert run_windows(cfg, corr, target)[0] in (0, 3)
    before = snapshot(target)
    assert 'local_delta.json' in before
    code, _, err = run_windows(cfg, corr, target, sizes=(-1.0,))               # refused before the transaction opens
    assert code == 2, err
    assert snapshot(target) == before
    assert json.loads((target / 'error.json').read_text(encoding='utf-8'))['exit_code'] == 2


def test_pre_begin_failure_after_a_killed_publication_does_not_wedge_the_next_run(stage46):
    """A journal left by a process killed during step 3 (no backups yet); a run that fails before begin() used to delete the
    finals, so the next begin() found neither file nor backup (local_delta_recovery_unreadable)."""
    out, cfg, corr, _, _, _ = compare_config(stage46, 'shared', 'f1_windows_wedge')
    target = out / 'w'
    assert run_windows(cfg, corr, target)[0] in (0, 3)
    before = snapshot(target)
    names = sorted(n for n in before if n.startswith(('local_delta', 'window_sensitivity')))
    (target / '.local_delta.publishing').write_text(json.dumps({'schema': 'set_publication_v1', 'old': names, 'new': names}))
    assert run_windows(cfg, corr, target, sizes=(-1.0,))[0] == 2
    assert {n: (target / n).read_bytes() for n in names} == {n: before[n] for n in names}
    code, _, err = run_windows(cfg, corr, target)                              # recovers (rolls back nothing to restore) and publishes
    assert code in (0, 3) and 'local_delta_recovery_unreadable' not in err, err


def test_cleanup_removes_the_orphan_masks_zip_but_not_user_files_or_in_flight_sets(tmp_path):
    folder = tmp_path / 'o'
    folder.mkdir()
    for name in ('comparison.json', 'comparison.csv', 'comparison_masks.zip', 'comparison_notes.zip', 'notes.txt', 'validation.json'):
        (folder / name).write_bytes(b'x')
    cli._remove_reports(folder, 'compare')
    assert sorted(p.name for p in folder.iterdir()) == ['comparison_notes.zip', 'notes.txt']
    for name in ('comparison.json', 'comparison.csv', 'comparison_masks.zip', 'local_delta.json', 'local_delta_notes.csv'):
        (folder / name).write_bytes(b'x')
    (folder / '.comparison.publishing').write_text('{}')
    cli._remove_reports(folder, 'electrode-map')                               # an interrupted publication is not ours to delete
    assert {'comparison.json', 'comparison.csv', 'comparison_masks.zip'} <= {p.name for p in folder.iterdir()}
    assert not (folder / 'local_delta.json').exists()


# -- RB-A5 --------------------------------------------------------------------------------------------------------------
def test_the_artefact_is_not_read_when_the_stage6_admission_refuses(stage46, monkeypatch, tmp_path):
    import simcompare.adapters.real_hotspot as real_hotspot
    from test_stage6_scale import make_fail, set_limit, run_compare
    out, cfg, corr, cmp_dir, _, _ = compare_config(stage46, 'shared', 'f1_artefact_after_admission')
    run_compare(cfg)
    assert (cmp_dir / 'comparison_masks.zip').exists()
    config = __import__('simcompare').pipeline.load_numeric_config(cfg)
    _, reference, target, pairs = eq._load_inputs('simcompare', cfg, corr)
    estimate, _ = real_hotspot.stage_compare_windows_estimate(reference, target, layouts_cfg=config, pairs=pairs, sizes=(0.3, 0.8),
                                                              jitter_um=0.1, reuse=True)
    make_fail(monkeypatch, loads=False)                                        # load_compare_masks fails the test when it runs
    real_load = real_hotspot.configured_dataset

    def tight(*args, **kwargs):
        dataset = real_load(*args, **kwargs)
        set_limit(dataset, limit=estimate['peak'] - 1)
        return dataset
    monkeypatch.setattr(real_hotspot, 'configured_dataset', tight)
    code, _, err = run_windows(cfg, corr, tmp_path / 'refused')
    assert code == 2 and 'oversized_real_operation' in (tmp_path / 'refused' / 'error.json').read_text(encoding='utf-8'), err


# -- RB-A10 -------------------------------------------------------------------------------------------------------------
def _dead_pid():
    import subprocess
    import sys
    process = subprocess.Popen([sys.executable, '-c', 'pass'])
    process.wait()
    return process.pid


def test_begin_of_the_compare_transaction_sweeps_temps_of_dead_runs_only(tmp_path):
    import subprocess
    import sys
    from simcompare.pipeline import _ComparisonStream
    live = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        dead = _dead_pid()
        names = ['.comparison_masks.zip.%d.0a1b2c3d.tmp', '.comparison.json.%d.0a1b2c3d.tmp', '.comparison.csv.%d.0a1b2c3d.tmp']
        out = tmp_path / 'o'
        out.mkdir()
        for name in names:
            (out / (name % dead)).write_bytes(b'stale')
            (out / (name % live.pid)).write_bytes(b'other run')
        (out / ('.comparison.json.%d.0a1b2c3d.tmp' % os.getpid())).write_bytes(b'mine')
        user = {'.notes.%d.tmp' % dead: b'user', 'comparison_masks.zip.%d.tmp' % dead: b'user', '.comparison.json.%d.zz.tmp' % dead: b'user'}
        for name, data in user.items():
            (out / name).write_bytes(data)
        stream = _ComparisonStream(out, None, masks=True)
        stream.begin()
        stream.abort()
        left = {p.name for p in out.iterdir()}
        assert not any(str(dead) in n and n not in user for n in left)
        assert {n % live.pid for n in names} <= left and set(user) <= left
        assert '.comparison.json.%d.0a1b2c3d.tmp' % os.getpid() in left
    finally:
        live.kill()
        live.wait()


def test_begin_of_the_set_publication_keeps_the_scratch_of_a_live_run_and_sweeps_a_dead_one(tmp_path):
    import subprocess
    import sys
    from simcompare.publication import SetPublication
    live = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        out = tmp_path / 'o'
        out.mkdir()
        dead_dir, live_dir = out / ('.local_delta.staging.%d.aabbccdd' % _dead_pid()), out / ('.local_delta.staging.%d.aabbccdd' % live.pid)
        for folder in (dead_dir, live_dir):
            folder.mkdir()
            (folder / 'local_delta.json').write_bytes(b'x')
        publication = SetPublication(out)
        publication.begin()
        publication.abort()
        assert not dead_dir.exists() and live_dir.exists()
    finally:
        live.kill()
        live.wait()
