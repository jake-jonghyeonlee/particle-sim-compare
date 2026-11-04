"""stretch-align/T2: ``candidates-change --apply-align stretch`` (타일 field), synthetic end to end.

Drift world: frame 1300 x 1300 px, 4 units of 1150 px, the target unit is ``EXTRA`` = 5 px larger per axis (ΔU = 5). Inside each target unit the
layout (CNT, three Layer files, hotspot features) is stretched by ``RATE`` (a column or row is repeated every ~1/RATE px), so a feature at unit
coordinate u sits ``ix * EXTRA + floor(u * RATE)`` px right of its reference position. One offset per unit cannot follow this drift
(``--apply-align unit`` leaves false changes at the feature positions); one offset per 150 px tile can. Unit (1, 1) also has a real change:
the feature at unit coordinate (560, 560) is 8 px wider in the target (case AB only).
"""
import csv
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

import test_change_candidates as tcc
from simcompare import change_candidates, change_map, layer_align
from simcompare.adapters import electrode_png
from simcompare.adapters.real_hotspot import import_hotspots
from simcompare.cli import main

PITCH = 0.1
U, EXTRA, RATE = 1150, 5, 0.009
LAYERS = ('L1', 'L2', 'L3')
CNTS = ('CNT1', 'CNT2', 'CNT3')
FEATURES = [(y, x) for y in (150, 560, 1000) for x in (150, 560, 1000)]       # unit 좌표 (y, x); 20 x 30 px
FEATURE_H, FEATURE_W = 20, 30
CHANGE_UNIT, CHANGE_AT, CHANGE_COLS = (1, 1), (560, 560), 8
RUN = dict(change_tol=0.1, window=5, top_k=40, layer_tol=0.5)
STRETCH = ('--apply-align', 'stretch', '--unit-align-um', '1.5', '--stretch-tile-um', '15', '--stretch-radius-um', '0.8')
UNIT = ('--apply-align', 'unit', '--unit-align-um', '1.5')


def tile(seed, n=4000):
    rng = np.random.default_rng(seed)
    out = np.zeros((U, U), bool)
    for _ in range(n):
        y, x = int(rng.integers(0, U - 10)), int(rng.integers(0, U - 10))
        out[y:y + int(rng.integers(2, 9)), x:x + int(rng.integers(2, 9))] = True
    return out


def warp(unit, size, rate=RATE):
    """unit(U x U) 를 size x size 로 늘린다: 원본 j - floor(j * rate) 번째 줄을 가져와 줄이 반복(늘어남)되고, 범위 밖은 비운다."""
    j = np.arange(size)
    src = j - np.floor(j * rate).astype(int)
    ok = (src >= 0) & (src < unit.shape[0])
    out = unit[np.ix_(np.clip(src, 0, unit.shape[0] - 1), np.clip(src, 0, unit.shape[0] - 1))]
    return out & ok[:, None] & ok[None, :]


def canvas(side, make_unit):
    ut = U if side == 'reference' else U + EXTRA
    full = np.zeros((2 * ut, 2 * ut), bool)
    for iy in (0, 1):
        for ix in (0, 1):
            full[iy * ut:(iy + 1) * ut, ix * ut:(ix + 1) * ut] = make_unit(iy, ix, ut)
    return full


def layout_image(side, base):
    return canvas(side, lambda iy, ix, ut: base if side == 'reference' else warp(base, ut))


def feature_unit(positions):
    out = np.zeros((U, U), bool)
    for y, x in positions:
        out[y:y + FEATURE_H, x:x + FEATURE_W] = True
    return out


def hotspot_mask(side, case):
    def make(iy, ix, ut):
        base = feature_unit(FEATURES)
        if side == 'reference':
            return base
        out = warp(base, ut)
        if case == tcc.CASE_AB and (iy, ix) == CHANGE_UNIT:
            moved = warp(feature_unit([CHANGE_AT]), ut)
            rows, cols = np.flatnonzero(moved.any(axis=1)), np.flatnonzero(moved.any(axis=0))
            out[rows[0]:rows[-1] + 1, cols[-1] + 1:cols[-1] + 1 + CHANGE_COLS] = True
        return out
    size = 1300 if side == 'reference' else 1300 + 2 * EXTRA
    return canvas(side, make)[500:500 + size, 500:500 + size]


def pattern(seed, shape):
    arr = np.zeros(shape, bool)
    arr[500 + seed:503 + seed, 502:508] = True
    arr[10:12, 10:12] = True
    return arr


def build_side(base, side):
    product = tcc.PRODUCTS[side]
    masks = {case: hotspot_mask(side, case) for case in (tcc.CASE_AB, tcc.CASE_AC)}
    size = next(iter(masks.values())).shape[0]
    full = (size + 1000, size + 1000)
    root = base / f'hotspot_{side}'
    for case, mask in masks.items():
        folder = root / 'images' / case
        (folder / 'result').mkdir(parents=True)
        tcc.save_black(folder / '1.png', pattern(1, full))
        tcc.save_black(folder / '2.png', pattern({tcc.CASE_AB: 2, tcc.CASE_AC: 3}[case], full))
        defect = case.split('-')[0]
        Image.fromarray(mask.astype(np.uint8) * 255).save(folder / f'hotspot_{defect}05.png')
        with (folder / f'result/{defect}.csv').open('w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.writer(handle)
            writer.writerow(['defect_size(um)', 'Area(um2)'])
            writer.writerow(['5', f'{int(mask.sum()) * PITCH * PITCH:.2f}'])
    expected = base / f'expected_{side}.txt'
    expected.write_text(''.join(f'{case}\n' for case in masks))
    imported = base / f'imported_{side}'
    import_hotspots(root, imported, expected_cases=expected)
    (imported / 'pairs.csv').write_text('pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\nAB,A,B,line,pair-v1\nAC,A,C,line,pair-v1\n')
    assignment = {'product_id': product, 'design_version': 'DESIGN', 'source_id': 'SOURCE', 'process_step_id': 'STEP',
                  'exposure_id': 'EXPOSURE', 'unit_id': 'UNIT', 'pair_table_path': 'pairs.csv',
                  'layer_stack': [{'actual_layer': 'L1', 'stack_order': 1, 'density_process_layer': 'P1'}],
                  'electrode_roles': tcc.ROLES}
    assignment_path = base / f'assignment_{side}.json'
    assignment_path.write_text(json.dumps(assignment))
    dl1 = base / 'dl1'
    for number, (name, seed) in enumerate(zip(CNTS, (21, 22, 23)), start=1):
        tcc.save_black(dl1 / product / 'CNO' / f'{number}_{name}.png', layout_image(side, tile(seed)))
    adapter = base / f'adapter_{side}'
    assert main(['import-electrode-assets', '--source-kind', 'hotspot_case_electrode', '--root', str(root),
                 '--numeric-data-dir', str(imported), '--condition-assignment', str(assignment_path),
                 '--cnt-from-dl1-root', str(dl1), '--source-version', 'layout_v1', '--max-asset-memory-mb', '512',
                 '--out', str(adapter)]) == 0
    for electrode in ('A', 'B', 'C'):           # 이 폴더 이름만 candidates-change 가 본다
        tcc.save_black(dl1 / product / 'Merged_Layer' / electrode / '1-L1.png', np.pad(np.ones((2, 2), bool), 4))
    for number, (layer, seed) in enumerate(zip(LAYERS, (31, 32, 33)), start=1):
        tcc.save_black(dl1 / product / 'Layer' / f'{number}-{layer}.png', layout_image(side, tile(seed)))
    data = base / f'data_{side}'
    shutil.copytree(imported, data)
    (data / 'assets').mkdir(exist_ok=True)
    for name in ('review_assets.json', 'electrode_asset_import_record.json'):
        shutil.copyfile(adapter / name, data / name)
    for path in (adapter / 'assets').iterdir():
        shutil.copyfile(path, data / 'assets' / path.name)
    return data, assignment


@pytest.fixture(scope='module')
def drift(tmp_path_factory):
    base = tmp_path_factory.mktemp('drift_world')
    datas, assignments = {}, {}
    for side in ('reference', 'target'):
        datas[side], assignments[side] = build_side(base, side)
    config = tcc.write_config(base, datas, assignments, map_spec={'diameter_um': 5})
    return SimpleNamespace(base=base, config=config, dl1=base / 'dl1', tmp_path_factory=tmp_path_factory)


def run(world, out, *extra, dl1=None, **kw):
    args = tcc.cli_args(world.config, out, dl1 or world.dl1, **{**RUN, **kw})
    return main(args[:-2] + list(extra) + args[-2:])


def make(world, *extra, **kw):
    out = world.tmp_path_factory.mktemp('out') / 'out'
    code = run(world, out, *extra, **kw)
    return SimpleNamespace(code=code, out=out, payload=tcc.load(out), alignment=tcc.load(out, 'alignment.json'),
                           execution=tcc.load(out, 'execution.json'))


def files(out):
    return {p.relative_to(out).as_posix(): p for p in sorted(Path(out).rglob('*')) if p.is_file()}


@pytest.fixture(scope='module')
def unit_run(drift):
    return make(drift, *UNIT)


@pytest.fixture(scope='module')
def stretch_run(drift):
    return make(drift, *STRETCH)


@pytest.fixture(scope='module')
def stretch_twice(drift):
    return make(drift, *STRETCH)


def queries(run_, direction='reference_to_target'):
    return tcc.queries(run_.payload, direction)


def near(q, y, x, tol_px=40):
    return abs(q['center_xy_um'][0] - (50.0 + x * PITCH)) <= tol_px * PITCH and abs(q['center_xy_um'][1] - (50.0 + y * PITCH)) <= tol_px * PITCH


def frame_centre(uy, ux, iy, ix):
    """unit (iy, ix) 안 unit 좌표 feature (uy, ux) 의 중심(reference frame 픽셀 (y, x))."""
    return iy * U + uy - 500 + FEATURE_H / 2.0, ix * U + ux - 500 + FEATURE_W / 2.0


CHANGE_CENTRE = frame_centre(*CHANGE_AT, *CHANGE_UNIT)


def is_change(q):
    return near(q, *CHANGE_CENTRE, tol_px=40)


def false_windows(run_):
    """real change 가 아닌 feature 위치의 점수 > 0 창."""
    return [q for q in queries(run_) if q['score_um2'] > 0 and not is_change(q)]


# --------------------------------------------------------------------------------------------------------------------
# (a) 핵심: 밀림 창이 stretch 에서 0, 실제 변경이 1위

def test_unit_mode_leaves_false_changes_at_the_drifting_features(unit_run):
    """이 world 가 의미가 있는지: unit 모드의 top-k 에 밀림 창이 있고, 실제 변경도 들어 있다."""
    assert len(false_windows(unit_run)) >= 3
    assert any(is_change(q) and q['score_um2'] > 0 for q in queries(unit_run))


def at_drift_windows(unit_run, stretch_run_):
    return [q for w in false_windows(unit_run) for q in queries(stretch_run_)
            if abs(q['center_xy_um'][0] - w['center_xy_um'][0]) <= 25 * PITCH and abs(q['center_xy_um'][1] - w['center_xy_um'][1]) <= 25 * PITCH]


def drift_windows_vanish(unit_run, stretch_run_, strict=False):
    """unit 모드 top-k 의 밀림 창마다, stretch 의 같은 자리 창은 점수 0 이거나 없다(strict 면 없어야 한다). 실제 변경 창은 stretch 1위."""
    here = at_drift_windows(unit_run, stretch_run_)
    zero = all(q['score_um2'] == 0 for q in here) and (not here or not strict)
    top = queries(stretch_run_)[0]
    return zero and is_change(top) and top['score_um2'] > 0 and not false_windows(stretch_run_)


def test_stretch_removes_the_drift_windows_and_ranks_the_real_change_first(unit_run, stretch_run):
    assert stretch_run.code in (0, 3)
    assert drift_windows_vanish(unit_run, stretch_run)
    top = queries(stretch_run)[0]
    assert top['score_um2'] == pytest.approx(CHANGE_COLS * FEATURE_H * PITCH * PITCH)      # 8 x 20 px 만큼 넓어진 면적, 밀림 잔차가 섞이지 않는다
    assert top['offset_source'] in ('stretch', 'unit') and queries(unit_run)[0]['score_um2'] > top['score_um2']


def unit_field_of(unit_run):
    regions = layer_align.UnitGeometry((1300, 1300)).unit_regions()
    field = []
    for u in unit_run.alignment['unit_offsets']:
        ix, iy = u['unit_index_xy']
        y0, y1, x0, x1 = regions[(iy, ix)]
        field.append((y0, y1, x0, x1, u['offset_xy_px'][0], u['offset_xy_px'][1]))
    return field


def test_the_stretch_run_has_no_window_at_the_drift_positions(unit_run, stretch_run):
    assert at_drift_windows(unit_run, stretch_run) == [] and len(queries(stretch_run)) < len(queries(unit_run))
    assert drift_windows_vanish(unit_run, stretch_run, strict=True)


def test_mutant_unit_field_in_change_maps_brings_the_drift_windows_back(drift, unit_run, monkeypatch):
    """변형 1: change_maps 에 타일 field 대신 unit field 를 넘기면 밀림 위치에 창이 후보로 다시 나온다(2차 패스가 점수를 0 으로 되돌려도 strict 단정이 깨진다)."""
    unit_field, real = unit_field_of(unit_run), change_map.change_maps

    def swapped(*args, **kwargs):
        kwargs['offset_field'] = unit_field
        return real(*args, **kwargs)
    monkeypatch.setattr(change_map, 'change_maps', swapped)
    mutant = make(drift, *STRETCH)
    assert at_drift_windows(unit_run, mutant)
    assert not drift_windows_vanish(unit_run, mutant, strict=True)


def test_mutant_unit_field_in_both_passes_fails_the_score_zero_assertion(drift, unit_run, monkeypatch):
    """변형 2: 변경 지도와 2차 패스 모두 unit field 면 밀림 창이 점수 > 0 으로 남아 기본 단정(점수 0)이 깨진다."""
    unit_field = unit_field_of(unit_run)
    real_maps, real_cases = change_map.change_maps, change_map.case_contributions

    def maps(*args, **kwargs):
        kwargs['offset_field'] = unit_field
        return real_maps(*args, **kwargs)

    def cases(*args, **kwargs):
        if 'window_offsets' in kwargs:
            kwargs['window_offsets'] = [unit_field] * len(kwargs['window_offsets'])
        return real_cases(*args, **kwargs)
    monkeypatch.setattr(change_map, 'change_maps', maps)
    monkeypatch.setattr(change_map, 'case_contributions', cases)
    mutant = make(drift, *STRETCH)
    assert false_windows(mutant) and not drift_windows_vanish(unit_run, mutant)


# --------------------------------------------------------------------------------------------------------------------
# (b) 모든 타일이 대체되면 unit 모드와 같다

def capture(monkeypatch):
    seen = {}
    real_maps, real_windows = change_map.change_maps, change_map.window_candidates

    def maps(*args, **kwargs):
        seen['maps'] = real_maps(*args, **kwargs)
        return seen['maps']

    def windows(*args, **kwargs):
        seen['windows'] = real_windows(*args, **kwargs)
        return seen['windows']
    monkeypatch.setattr(change_map, 'change_maps', maps)
    monkeypatch.setattr(change_map, 'window_candidates', windows)
    return seen


def test_all_tiles_replaced_equals_unit_mode(drift, monkeypatch):
    seen = capture(monkeypatch)
    unit_got = make(drift, *UNIT)
    unit_seen = dict(seen)
    real = layer_align.choose_dice_first
    calls = []

    def fake(*args, **kwargs):
        calls.append(1)
        result = real(*args, **kwargs)
        return {**result, 'status': 'low_score', 'apply': False} if len(calls) > 5 else result      # 1 = 전역, 2..5 = unit 4개, 그 뒤 = 타일
    monkeypatch.setattr(layer_align, 'choose_dice_first', fake)
    stretch_got = make(drift, *STRETCH)
    assert len(calls) > 5 + 16
    summary = stretch_got.alignment['stretch']
    assert summary['n_applied'] == 0 and summary['offset_source_counts']['stretch'] == 0
    a, b = unit_seen['maps'], seen['maps']
    assert sorted(a['maps']) == sorted(b['maps'])
    for layer in a['maps']:
        assert np.array_equal(a['maps'][layer], b['maps'][layer])
        assert np.array_equal(a['union_change'][layer], b['union_change'][layer])
        assert np.array_equal(a['union_ref'][layer], b['union_ref'][layer])
        assert np.array_equal(a['union_tgt'][layer], b['union_tgt'][layer])
    assert np.array_equal(a['D'], b['D'])
    assert unit_seen['windows']['candidates'] == seen['windows']['candidates']
    rank = lambda got: [(q['region_id'], q['score_um2'], q['center_xy_um']) for q in queries(got)]      # noqa: E731
    assert rank(unit_got) == rank(stretch_got) and len(rank(unit_got)) > 3


# --------------------------------------------------------------------------------------------------------------------
# (c) 오류 코드: 종료 2, --out 미생성

def expect_error(drift, tmp_path, capsys, code, *extra, dl1=None, **kw):
    out = tmp_path / 'out'
    assert run(drift, out, *extra, dl1=dl1, **kw) == 2
    assert code in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize('code,extra,kw', [
    ('apply_align_requires_unit_align', ('--apply-align', 'stretch', '--stretch-radius-um', '0.8'), {}),
    ('refine_pool_requires_local', STRETCH + ('--refine-pool', '3'), {}),
    ('stretch_excludes_local_align', STRETCH + ('--local-align-um', '1'), {}),
    ('stretch_tile_um_invalid', ('--apply-align', 'stretch', '--unit-align-um', '1.5', '--stretch-radius-um', '0.8', '--stretch-tile-um', '5'), {}),
    ('stretch_tile_um_invalid', ('--apply-align', 'stretch', '--unit-align-um', '1.5', '--stretch-radius-um', '0.8', '--stretch-tile-um', '200'), {}),
    ('stretch_radius_um_too_small_for_unit_delta', ('--apply-align', 'stretch', '--unit-align-um', '1.5', '--stretch-radius-um', '0.6',
                                                    '--stretch-tile-um', '15'), {}),
    ('stretch_radius_um_invalid', ('--apply-align', 'stretch', '--unit-align-um', '1.5', '--stretch-radius-um', '6.1', '--stretch-tile-um', '15'), {}),
    ('stretch_radius_um_invalid', ('--apply-align', 'stretch', '--unit-align-um', '1.5', '--stretch-radius-um', '0', '--stretch-tile-um', '15'), {}),
    ('stretch_radius_um_invalid', ('--apply-align', 'stretch', '--unit-align-um', '1.5'), {}),
    ('stretch_step_exceeds_tolerance', STRETCH, {'change_tol': 0.0}),
    ('stretch_step_exceeds_tolerance', ('--apply-align', 'stretch', '--unit-align-um', '1.5', '--stretch-radius-um', '0.8'), {}),     # 타일 기본 40 µm
    ('invalid_candidates_change_argument', ('--stretch-tile-um', '15'), {}),
    ('invalid_candidates_change_argument', UNIT + ('--stretch-radius-um', '0.8'), {}),
])
def test_error_codes_exit_2_without_creating_the_output(drift, tmp_path, capsys, code, extra, kw):
    expect_error(drift, tmp_path, capsys, code, *extra, **kw)


def test_layer_folder_is_required(drift, tmp_path, capsys):
    root = tmp_path / 'dl1_nolayer'
    shutil.copytree(drift.dl1, root)
    for folder in root.glob('*/Layer'):
        shutil.rmtree(folder)
    expect_error(drift, tmp_path, capsys, 'layer_folder_required', *STRETCH, dl1=root)


# --------------------------------------------------------------------------------------------------------------------
# (d) admission: 큰 배열(Layer 읽기) 할당 전 거부

def test_admission_rejects_before_any_layer_is_read(drift, tmp_path, capsys, monkeypatch):
    reads = []
    real_load = electrode_png.load_layer_png
    monkeypatch.setattr(electrode_png, 'load_layer_png', lambda *a, **k: reads.append(1) or real_load(*a, **k))
    monkeypatch.setattr(layer_align, 'estimate_stretch_bytes', lambda *a, **k: 10 ** 15)
    monkeypatch.setattr(layer_align, 'choose_tile_offsets', lambda *a, **k: pytest.fail('choose_tile_offsets must not run'))
    out = tmp_path / 'stretch_out'
    assert run(drift, out, *STRETCH) == 2
    assert 'oversized_real_operation' in capsys.readouterr().err
    assert not out.exists()
    assert len(reads) == 6      # unit 정렬이 읽은 두 모델 x Layer 3층뿐: stretch 단계는 Layer 를 읽기 전에 거부했다(읽었다면 12)


# --------------------------------------------------------------------------------------------------------------------
# (e) 기본·unit·local·unit-fold 실행에는 새 키·새 파일이 없다

def test_other_modes_have_no_stretch_keys_or_files(drift, unit_run):
    plain = make(drift)
    local = make(drift, '--apply-align', 'local', '--local-align-um', '1', '--unit-align-um', '1.5')
    folded = make(drift, *UNIT, '--unit-fold')
    for got in (plain, unit_run, local, folded):
        names = files(got.out)
        assert 'stretch_map.json' not in names and 'alignment.html' in names
        for name, path in names.items():
            assert b'stretch' not in path.read_bytes().lower(), name
        assert 'stretch' not in got.alignment
        for direction in ('reference_to_target', 'target_to_reference'):
            for q in queries(got, direction):
                assert not {'crosses_tile_boundary', 'seam_excluded_pixels', 'unit_index_xy', 'unit_coord_xy_um'} & set(q)
        assert 'stretch' not in json.dumps(got.execution)


# --------------------------------------------------------------------------------------------------------------------
# (f) stretch 모드의 출력 필드

def tile_at(stretch_map, y, x):
    for rec in stretch_map['tiles']:
        y0, y1, x0, x1 = rec['region_px']
        if y0 <= y < y1 and x0 <= x < x1:
            return rec
    raise AssertionError((y, x))


def test_stretch_outputs(stretch_run, drift):
    got = stretch_run
    sm = tcc.load(got.out, 'stretch_map.json')
    assert sm['schema_version'] == 'stretch_map_v1' and sm['tile_px'] == 150 and sm['radius_px'] == 8 and sm['s_max_px'] == 2
    assert sm['tolerance_px'] == 1 and sm['tile_um'] == 15 and sm['radius_um'] == 0.8
    assert sm['n_tiles'] == len(sm['tiles']) == 64 and len(sm['units']) == 4
    assert sm['n_applied'] > 32 and set(sm['offset_source_counts']) == {'stretch', 'unit', 'global'}
    for rec in sm['tiles']:
        assert {'unit_index_xy', 'tile_index_xy', 'region_px', 'start_offset_xy_px', 'offset_xy_px', 'delta_xy_px',
                'delta_from_global_xy_px', 'status', 'flags', 'dice', 'decided_by_layer', 'omega_pixels', 'per_layer',
                'offset_source'} <= set(rec)
        assert rec['offset_source'] == ('stretch' if rec['applied'] else 'unit')
    for unit in sm['units']:
        assert {'unit_index_xy', 'n_tiles', 'n_applied', 'status_counts', 'max_abs_delta_xy_px', 'linear_fit'} <= set(unit)
    # tiles cover the frame exactly
    cover = np.zeros((1300, 1300), int)
    for rec in sm['tiles']:
        y0, y1, x0, x1 = rec['region_px']
        cover[y0:y1, x0:x1] += 1
    assert cover.min() == cover.max() == 1
    summary = got.alignment['stretch']
    assert summary['n_tiles'] == 64 and summary['path'] == 'stretch_map.json' and summary['seam_excluded_pixels'] >= 0
    assert got.payload['policy']['apply_align'] == change_candidates.STRETCH_APPLY_POLICY
    args = got.payload['change_args']
    assert args['apply_align'] == 'stretch' and args['stretch_tile_um'] == 15 and args['stretch_radius_um'] == 0.8
    assert args['stretch_tile_px'] == 150 and args['stretch_radius_px'] == 8
    assert got.execution['args']['stretch_tile_px'] == 150
    assert set(got.execution['timings_s']) >= {'unit_align', 'layer_read', 'stretch_align', 'stretch_layer_read'}
    assert 'stretch_map.json' in got.execution['outputs']
    for direction in ('reference_to_target', 'target_to_reference'):
        qs = queries(got, direction)
        assert qs
        own = {'reference_to_target': 'reference', 'target_to_reference': 'target'}[direction]
        geometry = layer_align.UnitGeometry((1300, 1300) if own == 'reference' else (1310, 1310))
        for q in qs:
            assert q['offset_source'] in ('stretch', 'unit', 'global')
            assert isinstance(q['crosses_tile_boundary'], bool) and q['seam_excluded_pixels'] >= 0
            cx, cy = round((q['center_xy_um'][0] - 50.0) / PITCH - 0.5), round((q['center_xy_um'][1] - 50.0) / PITCH - 0.5)
            idx, coord = geometry.unit_index((cy, cx)), geometry.unit_coord((cy, cx))
            assert q['unit_index_xy'] == [idx[1], idx[0]]
            assert q['unit_coord_xy_um'] == pytest.approx([coord[1] * PITCH, coord[0] * PITCH], abs=2 * PITCH)
    for q in queries(got):      # 창이 offset 이 다른 타일을 걸치지 않으면 그 자리 타일의 offset 이 적용 offset 이다
        cx, cy = round((q['center_xy_um'][0] - 50.0) / PITCH), round((q['center_xy_um'][1] - 50.0) / PITCH)
        if not q['crosses_tile_boundary']:
            assert q['offset_xy_px_applied'] == tile_at(sm, cy, cx)['offset_xy_px']
            assert q['offset_source'] == tile_at(sm, cy, cx)['offset_source']


def test_window_fields_crossing_flag_and_seam_pixels():
    field = [(0, 10, 0, 10, 0, 0), (0, 10, 10, 20, 2, 0)]
    seam = change_map._field_seam(change_map._parse_field(field, (10, 20)), (10, 20))      # 경계 x=10 양쪽 폭 2
    inside = {'window_origin_px': (0, 0), 'window_px': 8}
    across = {'window_origin_px': (2, 6), 'window_px': 8}
    assert change_candidates._stretch_window_fields(inside, field, seam) == {'crosses_tile_boundary': False, 'seam_excluded_pixels': 0}
    assert change_candidates._stretch_window_fields(across, field, seam) == {'crosses_tile_boundary': True, 'seam_excluded_pixels': 4 * 8}
    same = [(0, 10, 0, 10, 1, 1), (0, 10, 10, 20, 1, 1)]
    assert change_candidates._stretch_window_fields(across, same, np.zeros((10, 20), bool)) == {
        'crosses_tile_boundary': False, 'seam_excluded_pixels': 0}


# --------------------------------------------------------------------------------------------------------------------
# (g) 결정성과 허용 차이

def test_two_stretch_runs_differ_only_in_timings(stretch_run, stretch_twice):
    a, b = files(stretch_run.out), files(stretch_twice.out)
    assert sorted(a) == sorted(b) and 'stretch_map.json' in a
    for name in a:
        if name == 'execution.json':
            strip = lambda doc: {k: v for k, v in doc.items() if k != 'timings_s'}      # noqa: E731
            assert strip(json.loads(a[name].read_text(encoding='utf-8'))) == strip(json.loads(b[name].read_text(encoding='utf-8')))
        else:
            assert a[name].read_bytes() == b[name].read_bytes(), name


def test_stretch_with_unit_fold_and_merge_scope_all_runs(drift):
    got = make(drift, *STRETCH, '--unit-fold', '--merge-scope', 'all')
    assert got.code in (0, 3) and queries(got)
    assert all('unit_fold' in q for q in queries(got))
    assert got.payload['policy']['attributes'] == change_candidates.ATTRIBUTES_POLICY_ALL_WINDOW_OFFSET
