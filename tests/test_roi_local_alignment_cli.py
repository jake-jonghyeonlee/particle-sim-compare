"""roi-detail/T2: ``candidates-change --local-align-um`` (ROI local alignment diagnostic), synthetic end to end.

World: the two-layer world of test_change_roi_v2_merge with electrode PNGs from test_change_roi_v2_layers (bars of electrode A on L1 and
L2, of B on L1; target bars at the reference bars + OFFSET). ``--merge-scope all`` gives one ROI. The baseline-off identity is pinned
in test_roi_local_alignment_baseline; here on/off runs are compared file by file.
"""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_change_candidates as tcc
import test_change_roi_v2_layers as layers
from simcompare import alignment_report, candidates, local_align
from simcompare.cli import main
from test_change_roi_v2_merge import build_world, two_layer_world  # noqa: F401  (module-scoped fixture of the other module)

ELECTRODES = {'A': {'L1': layers.BAR_A, 'L2': layers.BAR_A2}, 'B': {'L1': layers.BAR_B}}
EXTRA = ('--merge-scope', 'all')


@pytest.fixture(scope='module')
def clean_root(two_layer_world, tmp_path_factory):
    """DL1 root whose electrode PNGs are all readable (electrodes C and D removed: the shared world keeps a wrong-size PNG there)."""
    return layers.copy_root(two_layer_world, tmp_path_factory.mktemp('local_roots'), 'dl1_clean', ELECTRODES, rm=('C', 'D'))


def run(world, out, root, *extra):
    return layers.run(world, out, root, *EXTRA, *extra)


def doc(out):
    return json.loads((Path(out) / 'roi_local_alignment.json').read_text(encoding='utf-8'))


def files(out):
    return {p.relative_to(out).as_posix(): p for p in sorted(Path(out).rglob('*')) if p.is_file()}


@pytest.fixture(scope='module')
def off_run(two_layer_world, clean_root, tmp_path_factory):
    out = tmp_path_factory.mktemp('local_off') / 'out'
    return SimpleNamespace(out=out, code=run(two_layer_world, out, clean_root))


@pytest.fixture(scope='module')
def on_run(two_layer_world, clean_root, tmp_path_factory):
    out = tmp_path_factory.mktemp('local_on') / 'out'
    return SimpleNamespace(out=out, code=run(two_layer_world, out, clean_root, '--local-align-um', '1'))


# --- on / off ---------------------------------------------------------------------------------------------------------

def test_off_run_has_no_new_file_or_key(off_run):
    assert 'roi_local_alignment.json' not in files(off_run.out)
    assert 'local_align_um' not in tcc.load(off_run.out)['change_args']
    execution = tcc.load(off_run.out, 'execution.json')
    assert 'local_align_um' not in execution['args'] and 'roi_local_alignment.json' not in execution['outputs']


def test_on_run_differs_only_in_the_allowed_places(off_run, on_run):
    assert on_run.code == off_run.code
    off_files, on_files = files(off_run.out), files(on_run.out)
    assert sorted(set(on_files) - set(off_files)) == ['roi_local_alignment.json']
    assert sorted(set(off_files) - set(on_files)) == []
    for name in sorted(off_files):
        if name in ('execution.json', 'candidate_queries_v1.json'):
            continue
        assert on_files[name].read_bytes() == off_files[name].read_bytes(), name       # alignment.json/.html, exports, direct/*, funnel
    on_exec, off_exec = (tcc.load(r.out, 'execution.json') for r in (on_run, off_run))
    assert on_exec['args'].pop('local_align_um') == 1.0
    assert on_exec['outputs'] == sorted(off_exec['outputs'] + ['roi_local_alignment.json'])
    on_exec['outputs'] = off_exec['outputs']
    assert on_exec == off_exec
    on_q, off_q = (tcc.load(r.out) for r in (on_run, off_run))
    assert on_q['change_args'].pop('local_align_um') == 1.0
    assert on_q == off_q


def test_on_run_is_deterministic(two_layer_world, clean_root, on_run, tmp_path):
    out = tmp_path / 'out'
    run(two_layer_world, out, clean_root, '--local-align-um', '1')
    assert files(out)['roi_local_alignment.json'].read_bytes() == files(on_run.out)['roi_local_alignment.json'].read_bytes()


# --- 내용 -------------------------------------------------------------------------------------------------------------

def test_document_layout_and_recovered_zero_residual(on_run):
    d = doc(on_run.out)
    assert d['schema'] == 'roi_local_alignment_v1' and d['search_px'] == 10 and d['search_um'] == 1.0
    assert d['layers_basis'] == 'bundle_electrode_layers_plus_cnt' and d['excluded_layers'] == []
    assert d['global_offset_xy_px'] == list(tcc.OFFSET)
    payload = tcc.load(on_run.out)
    assert d['tolerance_um'] == payload['change_args']['align_layer_tolerance_um']
    assert [r['rank'] for r in d['rois']] == list(range(1, len(d['rois']) + 1))
    assert d['rois'][0]['query_id'] in {q['query_id'] for q in tcc.queries(payload, 'reference_to_target')}
    roi = d['rois'][0]
    assert roi['status'] == 'ok' and roi['reason'] is None
    keys = [(u['source'], u['layer']) for u in roi['layers_used']]
    assert ('electrode', 'L1') in keys and ('electrode', 'L2') in keys and ('cnt', 'CNT1') in keys
    assert roi['local_offset_xy_px'] == list(tcc.OFFSET) and roi['delta_from_global_um'] == 0.0
    assert roi['dice_local'] >= roi['dice_global'] and roi['ring_delta_xy_px'] == [0, 0]
    assert set(roi['per_layer']) == {'electrode:L1', 'electrode:L2', 'cnt:CNT1', 'cnt:CNT2', 'cnt:CNT3'}
    assert d['summary']['n_rois'] == len(d['rois']) and d['summary']['ok'] == 1 and d['summary']['skipped'] == 0
    assert d['linear_fit'] is None and d['linear_fit_reason'] == 'fewer_than_3_ok_rois'


def test_log_line_counts_rois(two_layer_world, clean_root, tmp_path, capsys):
    run(two_layer_world, tmp_path / 'out', clean_root, '--local-align-um', '1')
    assert 'roi_local_alignment: n_rois=1 ok=1 skipped=0' in capsys.readouterr().err


# --- 전극 층 제외 -------------------------------------------------------------------------------------------------------

def test_a_damaged_electrode_png_excludes_only_that_layer(two_layer_world, tmp_path):
    root = layers.copy_root(two_layer_world, tmp_path, 'dl1', ELECTRODES, rm=('C', 'D'))
    (root / tcc.PRODUCTS['target'] / 'A' / '1-L2.png').write_bytes(b'not a png')
    out = tmp_path / 'out'
    run(two_layer_world, out, root, '--local-align-um', '1')
    d = doc(out)
    assert [e['key'] for e in d['excluded_layers']] == ['electrode:L2'] and d['excluded_layers'][0]['reason'].startswith('png_errors:')
    assert ('electrode', 'L2') not in [(u['source'], u['layer']) for u in d['rois'][0]['layers_used']]
    assert ('electrode', 'L1') in [(u['source'], u['layer']) for u in d['rois'][0]['layers_used']]


def test_a_layer_in_one_model_only_is_excluded(two_layer_world, tmp_path):
    root = layers.copy_root(two_layer_world, tmp_path, 'dl1', ELECTRODES, rm=('C', 'D'))
    (root / tcc.PRODUCTS['target'] / 'A' / '1-L2.png').unlink()
    out = tmp_path / 'out'
    run(two_layer_world, out, root, '--local-align-um', '1')
    assert doc(out)['excluded_layers'] == [{'key': 'electrode:L2', 'reason': 'one_sided'}]


def test_unreadable_files_of_other_electrodes_exclude_the_layer(two_layer_world, roots_default, tmp_path):
    out = tmp_path / 'out'
    run(two_layer_world, out, roots_default, '--local-align-um', '1')
    d = doc(out)
    assert d['excluded_layers'] == [{'key': 'electrode:L1', 'reason': 'png_errors:electrode_frame_mismatch'}]
    assert d['rois'][0]['status'] == 'ok'


@pytest.fixture(scope='module')
def roots_default(two_layer_world, tmp_path_factory):
    return layers.copy_root(two_layer_world, tmp_path_factory.mktemp('local_default'), 'dl1', ELECTRODES)


# --- admission: 진단만 skipped -----------------------------------------------------------------------------------------

def _outcome(out):
    payload = tcc.load(out)
    return payload['warnings'], tcc.load(out, 'execution.json')['exit_code']


def test_diagnostic_memory_limit_skips_only_the_diagnostic(two_layer_world, clean_root, off_run, tmp_path, monkeypatch):
    monkeypatch.setattr(local_align, 'estimate_bytes', lambda *a, **k: 1 << 62)
    out = tmp_path / 'out'
    code = run(two_layer_world, out, clean_root, '--local-align-um', '1')
    d = doc(out)
    assert code == off_run.code and _outcome(out) == _outcome(off_run.out)
    assert [(r['status'], r['reason']) for r in d['rois']] == [('skipped', 'diagnostic_memory_limit')] * len(d['rois'])
    assert d['summary']['skipped'] == len(d['rois']) and d['linear_fit'] is None
    assert files(out)['alignment.html'].read_bytes() == files(off_run.out)['alignment.html'].read_bytes()


def test_base_admission_failure_skips_every_roi_and_keeps_the_base_warning(two_layer_world, clean_root, tmp_path, monkeypatch):
    monkeypatch.setattr(alignment_report, 'electrode_fits', lambda *a, **k: False)
    off, on = tmp_path / 'off', tmp_path / 'on'
    code_off = run(two_layer_world, off, clean_root)
    code_on = run(two_layer_world, on, clean_root, '--local-align-um', '1')
    assert code_on == code_off == 3
    assert _outcome(on) == _outcome(off)
    assert 'alignment_report_layer_base_skipped' in [w['code'] for w in _outcome(on)[0]]
    d = doc(on)
    assert {(r['status'], r['reason']) for r in d['rois']} == {('skipped', 'electrode_bases_skipped')}


def test_images_omitted_skips_every_roi_in_window_order(two_layer_world, clean_root, tmp_path, monkeypatch):
    monkeypatch.setattr(alignment_report, 'fits', lambda *a, **k: False)
    off, on = tmp_path / 'off', tmp_path / 'on'
    run(two_layer_world, off, clean_root)
    code = run(two_layer_world, on, clean_root, '--local-align-um', '1')
    assert code == 3 and _outcome(on) == _outcome(off)
    assert 'alignment_report_images_skipped' in [w['code'] for w in _outcome(on)[0]]
    d = doc(on)
    n = len(tcc.queries(tcc.load(on), 'reference_to_target'))
    assert [r['rank'] for r in d['rois']] == list(range(1, n + 1))
    assert {(r['status'], r['reason'], r['box_px']) for r in d['rois']} == {('skipped', 'images_skipped', None)}
    assert d['summary']['skipped'] == n


# --- R 검증 -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf', '0.04', '3.2'])
def test_bad_radius_exits_two_and_creates_no_folder(two_layer_world, clean_root, tmp_path, capsys, value):
    out = tmp_path / 'out'
    assert run(two_layer_world, out, clean_root, '--local-align-um', value) == 2
    assert not out.exists()
    assert value.replace('inf', 'inf') in capsys.readouterr().err


def test_radius_limits_are_inclusive_at_one_and_thirty_pixels(two_layer_world, clean_root, tmp_path):
    for value, px in (('0.1', 1), ('3.0', 30)):
        out = tmp_path / ('out' + value)
        assert run(two_layer_world, out, clean_root, '--local-align-um', value) in (0, 3)
        assert doc(out)['search_px'] == px


# --- 실패 주입 ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('fail_on', ['roi_local_alignment.json', 'execution.json'])
def test_failure_while_writing_removes_what_this_run_made(two_layer_world, clean_root, tmp_path, monkeypatch, fail_on):
    original = candidates.write_text

    def failing(path, text, track):
        original(path, text, track)
        if Path(path).name == fail_on:
            raise OSError('injected')
    monkeypatch.setattr(candidates, 'write_text', failing)
    out = tmp_path / 'out'
    assert run(two_layer_world, out, clean_root, '--local-align-um', '1') == 2      # the CLI reports the OSError and exits 2
    assert not out.exists()


def test_existing_output_folder_is_refused_and_untouched(two_layer_world, clean_root, tmp_path, capsys):
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'keep.txt').write_text('mine')
    assert run(two_layer_world, out, clean_root, '--local-align-um', '1') == 2
    assert [p.name for p in out.iterdir()] == ['keep.txt'] and (out / 'keep.txt').read_text() == 'mine'
    capsys.readouterr()
