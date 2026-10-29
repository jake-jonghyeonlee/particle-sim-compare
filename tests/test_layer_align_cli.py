"""layer-align/T4a: ``candidates-change --unit-align-um / --apply-align unit / --unit-fold``, synthetic end to end.

Two worlds, both in the new folder structure (``<model>/{CNO, Merged_Layer/<electrode>, Layer}``):

* small world (``two_layer_world`` of test_change_roi_v2_merge, converted): ΔU = 0, one global shift for the whole layout. Every unit offset
  equals the global one, so ``--apply-align unit`` must reproduce the diagnostic output (error codes, off-by-default checks, merge_scope=all).
* unit world (this file): frame 1300 x 1300 px, 4 units of 1150 px, the target frame is ``delta_u`` px larger per unit, so the unit k = (iy, ix)
  of the target sits at ``C + delta_u * (ix, iy)`` px from the reference one. Features inside the overlap band (unit coordinates 500..650) show
  up in all four units. The layout (CNT, three Layer files) is a random tile copied to the four units; one unit has a design change.
"""
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

import test_change_candidates as tcc
from simcompare import change_candidates, layer_align
from simcompare.adapters.real_hotspot import import_hotspots
from simcompare.cli import main
from test_change_roi_v2_merge import build_world, two_layer_world  # noqa: F401  (module-scoped fixture of the other module)

LAYERS = ('L1', 'L2', 'L3')
CNTS = ('CNT1', 'CNT2', 'CNT3')
NEW_ALIGNMENT_KEYS = ('layer_global_check', 'unit_offsets', 'unit_geometry', 'layer_inputs')
PITCH = 0.1


def tile(seed, shape, n=3000):
    rng = np.random.default_rng(seed)
    out = np.zeros(shape, bool)
    for _ in range(n):
        y, x = int(rng.integers(0, shape[0] - 10)), int(rng.integers(0, shape[1] - 10))
        out[y:y + int(rng.integers(2, 9)), x:x + int(rng.integers(2, 9))] = True
    return out


def run(world, out, *extra, dl1=None, **kw):
    args = tcc.cli_args(world.config, out, dl1 or world.dl1, **kw)
    return main(args[:-2] + list(extra) + args[-2:])


def make(world, tmp_path_factory, *extra, dl1=None, **kw):
    out = tmp_path_factory.mktemp('out') / 'out'
    code = run(world, out, *extra, dl1=dl1, **kw)
    return SimpleNamespace(code=code, out=out, payload=tcc.load(out), alignment=tcc.load(out, 'alignment.json'))


def files(out):
    return {p.relative_to(out).as_posix(): p for p in sorted(Path(out).rglob('*')) if p.is_file()}


def strip(doc, *paths):
    """doc 의 복사본에서 (키, 하위 키, ...) 경로를 지운다."""
    doc = json.loads(json.dumps(doc))
    for path in paths:
        node = doc
        for key in path[:-1]:
            node = node[key]
        node.pop(path[-1], None)
    return doc


# --------------------------------------------------------------------------------------------------------------------
# 작은 월드: ΔU = 0 (two_layer_world 를 새 구조 + Layer 로 바꾼 복사본)

def to_v2(world, tmp, name, with_layer=True):
    """world.dl1 복사본: 전극 폴더를 Merged_Layer/ 아래로, cno → CNO, Layer/ 에 3층(reference 무작위, target 은 같은 영상을 OFFSET 만큼 이동)."""
    root = tmp / name
    shutil.copytree(world.dl1, root)
    for side, product in tcc.PRODUCTS.items():
        folder = root / product
        (folder / 'Merged_Layer').mkdir()
        for entry in sorted(folder.iterdir()):
            if entry.is_dir() and entry.name == 'cno':
                entry.rename(folder / 'CNO')
            elif entry.is_dir() and entry.name not in ('Merged_Layer',):
                entry.rename(folder / 'Merged_Layer' / entry.name)
        if with_layer:
            for number, (layer, seed) in enumerate(zip(LAYERS, (31, 32, 33)), start=1):
                image = np.tile(tile(seed, (tcc.FULL[0] // 2, tcc.FULL[1] // 2)), (2, 2))
                if side == 'target':
                    image = tcc.shifted(image, *tcc.OFFSET)
                tcc.save_black(folder / 'Layer' / f'{number}-{layer}.png', image)
    return root


@pytest.fixture(scope='module')
def small(two_layer_world, tmp_path_factory):
    tmp = tmp_path_factory.mktemp('small_roots')
    v2 = to_v2(two_layer_world, tmp, 'dl1_v2')
    nolayer = to_v2(two_layer_world, tmp, 'dl1_v2_nolayer', with_layer=False)
    legacy = tmp / 'dl1_legacy'
    shutil.copytree(two_layer_world.dl1, legacy)
    return SimpleNamespace(world=two_layer_world, v2=v2, nolayer=nolayer, legacy=legacy, tmp_path_factory=tmp_path_factory)


@pytest.fixture(scope='module')
def small_base(small):
    return make(small.world, small.tmp_path_factory, dl1=small.v2)


@pytest.fixture(scope='module')
def small_nolayer(small):
    return make(small.world, small.tmp_path_factory, dl1=small.nolayer)


@pytest.fixture(scope='module')
def small_unit_align(small):
    return make(small.world, small.tmp_path_factory, '--unit-align-um', '1', dl1=small.v2)


@pytest.fixture(scope='module')
def small_apply(small):
    return make(small.world, small.tmp_path_factory, '--unit-align-um', '1', '--apply-align', 'unit', dl1=small.v2)


def test_a_layer_folder_without_new_arguments_changes_nothing(small_base, small_nolayer):
    """(a) 인수 생략 + Layer 있음 → 16개 출력 파일이 Layer 없는 같은 입력과 바이트까지 같다(허용 차이 목록 = 비어 있음)."""
    a, b = files(small_base.out), files(small_nolayer.out)
    assert sorted(a) == sorted(b) and len(a) >= 16
    differing = [name for name in sorted(a) if a[name].read_bytes() != b[name].read_bytes()]
    assert differing == []
    assert small_base.code == small_nolayer.code


def test_b_unit_align_only_adds_alignment_keys_and_changes_nothing_else(small_base, small_unit_align):
    a, b = files(small_base.out), files(small_unit_align.out)
    assert sorted(a) == sorted(b)
    allowed = {'alignment.json', 'candidate_queries_v1.json', 'execution.json'}
    assert [n for n in sorted(a) if n not in allowed and a[n].read_bytes() != b[n].read_bytes()] == []   # alignment.html 포함
    assert small_unit_align.code == small_base.code
    alignment = small_unit_align.alignment
    assert all(k in alignment for k in NEW_ALIGNMENT_KEYS) and not any(k in small_base.alignment for k in NEW_ALIGNMENT_KEYS)
    assert strip(alignment, *[(k,) for k in NEW_ALIGNMENT_KEYS]) == small_base.alignment
    # 전체 queries·export 는 같고, 바뀌는 곳은 change_args / inputs.layers / notes 뿐이다
    payload, base = small_unit_align.payload, small_base.payload
    assert payload['change_args'].pop('unit_align_um') == 1.0
    layers = payload['inputs'].pop('layers')
    assert sorted(layers) == ['reference', 'target'] and all(v['n_files'] == 3 for v in layers.values())
    payload['notes'] = [n for n in payload['notes'] if n['code'] != 'layer_global_check_differs']
    assert payload == base
    execution, base_execution = tcc.load(small_unit_align.out, 'execution.json'), tcc.load(small_base.out, 'execution.json')
    assert sorted(execution.pop('timings_s')) == ['layer_read', 'unit_align']
    assert execution['args'].pop('unit_align_um') == 1.0
    execution['notes'] = [n for n in execution['notes'] if n['code'] != 'layer_global_check_differs']
    assert execution == base_execution


def test_b_unit_offsets_and_global_check_record(small_unit_align):
    alignment = small_unit_align.alignment
    g = small_unit_align.alignment['offset_xy_px']
    assert g == list(tcc.OFFSET)
    units = alignment['unit_offsets']
    assert [u['unit_index_xy'] for u in units] == [[0, 0], [1, 0], [0, 1], [1, 1]]
    for u in units:
        assert u['status'] == 'ok' and u['applied'] is True and u['offset_xy_px'] == list(tcc.OFFSET) and u['delta_xy_px'] == [0, 0]
        assert u['offset_xy_um'] == [round(tcc.OFFSET[0] * PITCH, 6), round(tcc.OFFSET[1] * PITCH, 6)]
        assert u['decided_by_layer'] is not None and u['omega_pixels'] > 0
        assert set(u['per_layer']) == {f'layer:{n}' for n in LAYERS} | {f'cnt:{n}' for n in CNTS}
    check = alignment['layer_global_check']
    assert check['status'] == 'ok' and check['applied'] is False and check['offset_xy_px'] == list(tcc.OFFSET)
    assert check['delta_from_cnt_um'] == 0.0 and check['search_px'] == 10
    geometry = alignment['unit_geometry']
    assert geometry['unit_px_reference_yx'] == [700, 710] and geometry['delta_u_yx_px'] == [0, 0]
    assert geometry['boundary_px_reference_yx'] == [200, 210] and geometry['required_radius_px'] == 2
    inputs = alignment['layer_inputs']
    assert sorted(inputs) == ['reference', 'target']
    for side, item in inputs.items():
        assert [f['layer'] for f in item['files']] == list(LAYERS) and all(len(f['sha256']) == 64 for f in item['files'])
        assert item['files'][0]['relative_path'] == f"{tcc.PRODUCTS[side]}/Layer/1-L1.png"


def test_c_unit_apply_with_delta_u_zero_reproduces_the_diagnostic_run(small_base, small_apply):
    """모든 unit offset 이 전역 offset 과 같으면 창 점수·중심·CNT 변경은 진단 결과와 같다. 기여 case 는 모든 층에서 모으므로(A2) 진단 결과를 포함한다."""
    assert small_apply.code == small_base.code
    same = ('query_id', 'region_id', 'product_id', 'center_xy_um', 'window_um', 'layers', 'merged_from', 'area_um2', 'score_um2',
            'score_basis', 'score_layer', 'sum_residual_um2', 'union_area_reference_um2', 'union_area_target_um2', 'union_delta_um2',
            'observed_fraction', 'cnt_change_um2_by_layer')
    base_q = {d['direction']: d['queries'] for d in small_base.payload['directions']}
    for direction in base_q:
        got = [d for d in small_apply.payload['directions'] if d['direction'] == direction][0]['queries']
        assert len(got) == len(base_q[direction]) > 0
        for q, want in zip(got, base_q[direction]):
            assert q['offset_source'] == 'unit' and q['offset_xy_px_applied'] == list(tcc.OFFSET)
            assert {k: q[k] for k in same} == {k: want[k] for k in same}
            assert [f for f in q['flags'] if f != 'window_clipped'] == [f for f in want['flags'] if f != 'window_clipped']
            assert all('layer' in c for c in q['contributing_cases'])            # 모든 층 기여(layer 키)
            assert {c['case_key'] for c in want['contributing_cases']} <= {c['case_key'] for c in q['contributing_cases']}


def test_g_review_rois_row_keys_do_not_change(small_base, small_apply):
    for name in ('reference_export', 'target_export', 'reference_index_export', 'target_index_export'):
        base = json.loads((small_base.out / name / 'review_rois.json').read_text(encoding='utf-8'))
        got = json.loads((small_apply.out / name / 'review_rois.json').read_text(encoding='utf-8'))
        assert [sorted(r) for r in got['rois']] == [sorted(r) for r in base['rois']]


def test_h_merge_scope_all_has_no_duplicate_case_layer_pairs(small):
    run_all = make(small.world, small.tmp_path_factory, '--merge-scope', 'all', '--unit-align-um', '1', '--apply-align', 'unit',
                   dl1=small.v2)
    for d in run_all.payload['directions']:
        assert d['queries']
        for q in d['queries']:
            keys = [(c['case_key'], c['layer']) for c in q['contributing_cases']]
            assert keys and len(keys) == len(set(keys))
            assert {c['layer'] for c in q['contributing_cases']} == {'L1', 'L2'}
    q = run_all.payload['directions'][0]['queries'][0]
    assert q['layers'] == ['L1', 'L2'] and q['offset_source'] == 'unit'
    assert run_all.payload['policy']['merge'].startswith('all_layers_')


def test_i_a_unit_that_is_not_ok_falls_back_to_global_and_is_not_applied(small, monkeypatch):
    real = layer_align.choose_dice_first
    calls = []

    def fake(*args, **kwargs):
        result = real(*args, **kwargs)
        calls.append(1)
        if len(calls) == 4:             # 1: 전역 교차 확인, 2..5: unit (0,0) (0,1)... — 4번째가 unit (iy=1, ix=0)
            result = {**result, 'status': 'ambiguous', 'apply': False, 'offset_xy_px': None, 'delta_xy_px': None, 'decided_by_layer': None}
        return result
    monkeypatch.setattr(layer_align, 'choose_dice_first', fake)
    got = make(small.world, small.tmp_path_factory, '--unit-align-um', '1', '--apply-align', 'unit', dl1=small.v2)
    units = got.alignment['unit_offsets']
    assert [u['applied'] for u in units] == [True, True, False, True] and units[2]['unit_index_xy'] == [0, 1]
    bad = units[2]
    assert bad['status'] == 'ambiguous' and bad['offset_xy_px'] == got.alignment['offset_xy_px'] and bad['delta_xy_px'] == [0, 0]
    assert bad['decided_by_layer'] is None and bad['candidate_offset_xy_px'] is None
    sources = {q['offset_source'] for d in got.payload['directions'] for q in d['queries']}
    assert sources <= {'unit', 'global'}


# --- (e) 오류 코드: 쓰기 전 종료 2, 출력 폴더 미생성 ------------------------------------------------------------------

def expect_error(small, tmp_path, capsys, code, *extra, dl1=None, **kw):
    out = tmp_path / 'out'
    assert run(small.world, out, *extra, dl1=dl1 or small.v2, **kw) == 2
    assert code in capsys.readouterr().err
    assert not out.exists()


def test_e_layer_folder_required(small, tmp_path, capsys):
    expect_error(small, tmp_path, capsys, 'layer_folder_required', '--unit-align-um', '1', dl1=small.nolayer)
    expect_error(small, tmp_path, capsys, 'layer_folder_required', '--unit-align-um', '1', dl1=small.legacy)


def test_e_unit_align_um_too_small_and_too_large(small, tmp_path, capsys):
    expect_error(small, tmp_path, capsys, 'unit_align_um_too_small', '--unit-align-um', '0.1')       # 1 px < 2 px
    expect_error(small, tmp_path, capsys, 'unit_align_um_too_large', '--unit-align-um', '31')        # 310 px > 300


def test_e_apply_align_argument_errors(small, tmp_path, capsys):
    expect_error(small, tmp_path, capsys, 'apply_align_requires_unit_align', '--apply-align', 'unit')
    expect_error(small, tmp_path, capsys, 'apply_align_local_not_available', '--apply-align', 'local', '--unit-align-um', '1')
    expect_error(small, tmp_path, capsys, 'invalid_candidates_change_argument', '--unit-fold-tolerance-um', '3')


def test_e_oversized_real_operation(small, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(layer_align, 'estimate_layer_bytes', lambda *args, **kwargs: 1 << 62)
    expect_error(small, tmp_path, capsys, 'oversized_real_operation', '--unit-align-um', '1')


def test_e_legacy_structure_with_default_arguments_still_works(small, tmp_path):
    assert run(small.world, tmp_path / 'out', dl1=small.legacy) in (0, 3)


# --- (f) suppress 0 ---------------------------------------------------------------------------------------------------

def test_f_suppress_zero_records_the_any_overlap_policy(small, small_base, tmp_path):
    out = tmp_path / 'out'
    run(small.world, out, '--suppress-overlap', '0', dl1=small.v2)
    assert tcc.load(out)['policy']['suppress'] == change_candidates.SUPPRESS_POLICY_ANY
    out2 = tmp_path / 'out2'
    run(small.world, out2, '--suppress-overlap', '0.3', dl1=small.v2)
    assert tcc.load(out2)['policy']['suppress'] == change_candidates.SUPPRESS_POLICY
    assert 'suppress' not in small_base.payload['policy']


# --------------------------------------------------------------------------------------------------------------------
# unit 월드: delta_u = 2 (target frame 이 축마다 4 px 큼)

U = 1150
C = (4, 3)                                      # (dx, dy) of unit (0, 0)
BAND = {'R1': (520, 520, 20, 40), 'R2': (520, 600, 15, 30), 'R3': (600, 520, 15, 25)}   # tile 좌표 (y, x, h, w), 겹침 띠 500..650
CASE_OF = {'R1': tcc.CASE_AB, 'R2': tcc.CASE_AC, 'R3': tcc.CASE_AB}
ONLY_REF = {'C1': (750, 750, 20, 30), 'RA': (300, 300, 20, 20), 'RB': (300, 400, 20, 20)}   # frame 좌표 (y, x, h, w), reference 에만 있다
CLEAR = (100, 100, 40, 60)                      # L2 설계 변경: unit (1, 1) 의 tile (100..140, 100..160) 를 target 에서 지운다


def sector_positions(y, x):
    return [(y - 500 + iy * U, x - 500 + ix * U, iy, ix) for iy in (0, 1) for ix in (0, 1)]


def full_image(base_tile, extra, shift, clear=None):
    ut = base_tile.shape[0] + extra
    unit = np.zeros((ut, ut), bool)
    unit[:base_tile.shape[0], :base_tile.shape[1]] = base_tile
    full = np.zeros((2 * ut, 2 * ut), bool)
    for iy in (0, 1):
        for ix in (0, 1):
            block = unit
            if clear is not None and (iy, ix) == (1, 1):
                block = unit.copy()
                block[clear[0]:clear[0] + clear[2], clear[1]:clear[1] + clear[3]] = False
            full[iy * ut:(iy + 1) * ut, ix * ut:(ix + 1) * ut] = block
    return np.roll(full, (shift[1], shift[0]), axis=(0, 1))


def hotspot_masks(side, extra):
    size = 1300 if side == 'reference' else 1300 + 2 * extra
    masks = {tcc.CASE_AB: np.zeros((size, size), bool), tcc.CASE_AC: np.zeros((size, size), bool)}
    for name, (ty, tx, h, w) in BAND.items():
        for py, px, iy, ix in sector_positions(ty, tx):
            if side == 'reference':
                masks[CASE_OF[name]][py:py + h, px:px + w] = True
            elif name != 'R3':            # R3 은 target 에서 모든 unit 에서 사라진다(4중 복제된 실제 변경)
                y, x = py + C[1] + extra * iy, px + C[0] + extra * ix
                masks[CASE_OF[name]][y:y + h, x:x + w] = True
    if side == 'reference':
        for y, x, h, w in ONLY_REF.values():
            masks[tcc.CASE_AB][y:y + h, x:x + w] = True
    return masks


def pattern(seed, shape):
    arr = np.zeros(shape, bool)
    arr[500 + seed:503 + seed, 502:508] = True
    arr[10:12, 10:12] = True
    return arr


def build_side(base, side, extra):
    import csv
    product = tcc.PRODUCTS[side]
    masks = hotspot_masks(side, extra)
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
    shift, ext = ((0, 0), 0) if side == 'reference' else (C, extra)
    for number, (name, seed) in enumerate(zip(CNTS, (21, 22, 23)), start=1):
        tcc.save_black(dl1 / product / 'CNO' / f'{number}_{name}.png', full_image(tile(seed, (U, U)), ext, shift))
    adapter = base / f'adapter_{side}'
    code = main(['import-electrode-assets', '--source-kind', 'hotspot_case_electrode', '--root', str(root),
                 '--numeric-data-dir', str(imported), '--condition-assignment', str(assignment_path),
                 '--cnt-from-dl1-root', str(dl1), '--source-version', 'layout_v1', '--max-asset-memory-mb', '512',
                 '--out', str(adapter)])
    assert code == 0
    for electrode in ('A', 'B', 'C'):           # 이 폴더 이름만 candidates-change 가 본다
        tcc.save_black(dl1 / product / 'Merged_Layer' / electrode / '1-L1.png', np.pad(np.ones((2, 2), bool), 4))
    for number, (layer, seed) in enumerate(zip(LAYERS, (31, 32, 33)), start=1):
        clear = CLEAR if (side == 'target' and layer == 'L2') else None
        tcc.save_black(dl1 / product / 'Layer' / f'{number}-{layer}.png', full_image(tile(seed, (U, U)), ext, shift, clear))
    data = base / f'data_{side}'
    shutil.copytree(imported, data)
    (data / 'assets').mkdir(exist_ok=True)
    for name in ('review_assets.json', 'electrode_asset_import_record.json'):
        shutil.copyfile(adapter / name, data / name)
    for path in (adapter / 'assets').iterdir():
        shutil.copyfile(path, data / 'assets' / path.name)
    return data, assignment


@pytest.fixture(scope='module')
def big(tmp_path_factory):
    base = tmp_path_factory.mktemp('unit_world')
    datas, assignments = {}, {}
    for side in ('reference', 'target'):
        datas[side], assignments[side] = build_side(base, side, 2)
    config = tcc.write_config(base, datas, assignments, map_spec={'diameter_um': 5})
    return SimpleNamespace(base=base, config=config, dl1=base / 'dl1', tmp_path_factory=tmp_path_factory)


BIG = dict(change_tol=0.0, window=5, top_k=12, layer_tol=0.5)
FOLD_UM = 12.0


@pytest.fixture(scope='module')
def big_diag(big):
    return make(big, big.tmp_path_factory, **BIG)


@pytest.fixture(scope='module')
def big_apply(big):
    return make(big, big.tmp_path_factory, '--unit-align-um', '1', '--apply-align', 'unit', **BIG)


@pytest.fixture(scope='module')
def big_fold(big):
    return make(big, big.tmp_path_factory, '--unit-align-um', '1', '--apply-align', 'unit', '--unit-fold',
                '--unit-fold-tolerance-um', str(FOLD_UM), **BIG)


@pytest.fixture(scope='module')
def big_nofold(big):
    return make(big, big.tmp_path_factory, '--unit-fold', '--unit-fold-tolerance-um', '0.0001', '--unit-align-um', '1',
                '--apply-align', 'unit', **BIG)


def queries(run_, direction='reference_to_target'):
    return tcc.queries(run_.payload, direction)


def near(q, y, x, tol_px=40):
    return abs(q['center_xy_um'][0] - (50.0 + x * PITCH)) <= tol_px * PITCH and abs(q['center_xy_um'][1] - (50.0 + y * PITCH)) <= tol_px * PITCH


def band_centres(name):
    ty, tx, h, w = BAND[name]
    return [(py + h / 2.0, px + w / 2.0) for py, px, _, _ in sector_positions(ty, tx)]


def unit_of(q):
    px_x, px_y = (q['center_xy_um'][0] - 50.0) / PITCH, (q['center_xy_um'][1] - 50.0) / PITCH
    return (int(px_y + 500) // U, int(px_x + 500) // U)


def test_c_unit_world_geometry_and_unit_offsets(big_apply):
    geometry = big_apply.alignment['unit_geometry']
    assert geometry['unit_px_reference_yx'] == [1150, 1150] and geometry['unit_px_target_yx'] == [1152, 1152]
    assert geometry['delta_u_yx_px'] == [2, 2] and geometry['required_radius_px'] == 6 and geometry['search_radius_px'] == 10
    for u in big_apply.alignment['unit_offsets']:
        ix, iy = u['unit_index_xy']
        assert u['status'] == 'ok' and u['applied'] is True, u
        assert u['offset_xy_px'] == [C[0] + 2 * ix, C[1] + 2 * iy]


def test_c_diagnostic_run_reports_false_changes_in_the_replicated_units(big_diag):
    found = [q for q in queries(big_diag) if any(near(q, y, x) for name in ('R1', 'R2') for y, x in band_centres(name))]
    assert found, 'the global offset must leave false changes at the replicated features (otherwise this world proves nothing)'


def test_c_apply_unit_removes_false_changes_and_ranks_the_real_change_first(big_apply):
    qs = queries(big_apply)
    assert not [q for q in qs if any(near(q, y, x) for name in ('R1', 'R2') for y, x in band_centres(name))]
    top = qs[0]
    assert near(top, 760, 765) and unit_of(top) == (1, 1)
    assert top['score_um2'] == pytest.approx(20 * 30 * PITCH * PITCH) and top['offset_source'] == 'unit'
    r3 = [q for q in qs if any(near(q, y, x) for y, x in band_centres('R3'))]
    assert len(r3) == 4 and {unit_of(q) for q in r3} == {(0, 0), (0, 1), (1, 0), (1, 1)}


def test_c_applied_offsets_match_the_unit_offsets_and_target_centres_follow(big_apply):
    offsets = {tuple(u['unit_index_xy'][::-1]): u['offset_xy_px'] for u in big_apply.alignment['unit_offsets']}
    r2t, t2r = queries(big_apply), queries(big_apply, 'target_to_reference')
    assert len(r2t) == len(t2r) > 4
    by_id = {q['region_id']: q for q in t2r}
    for q in r2t:
        assert q['offset_xy_px_applied'] == offsets[unit_of(q)] and q['offset_source'] == 'unit'
        other = by_id[q['region_id']]
        assert other['offset_xy_px_applied'] == q['offset_xy_px_applied']
        assert other['center_xy_um'][0] == pytest.approx(q['center_xy_um'][0] + q['offset_xy_px_applied'][0] * PITCH, abs=1e-6)
        assert other['center_xy_um'][1] == pytest.approx(q['center_xy_um'][1] + q['offset_xy_px_applied'][1] * PITCH, abs=1e-6)
    sidecar = tcc.load(big_apply.out, 'direct/direct_pairs_sidecar.json')
    assert sidecar['pairs'] and all(p['offset_source'] == 'unit' and len(p['offset_xy_px_applied']) == 2 for p in sidecar['pairs'].values())
    correspondence = tcc.load(big_apply.out, 'direct/roi_correspondence.json')
    for pair in correspondence['pairs']:
        dx = pair['b']['center_xy_um'][0] - pair['a']['center_xy_um'][0]
        dy = pair['b']['center_xy_um'][1] - pair['a']['center_xy_um'][1]
        applied = sidecar['pairs'][pair['pair_id']]['offset_xy_px_applied']
        assert (dx, dy) == pytest.approx((applied[0] * PITCH, applied[1] * PITCH), abs=1e-6)


def test_c_recomputed_window_values_equal_the_map_values_for_the_same_field(big_apply):
    for q in queries(big_apply):
        assert q['union_delta_um2'] == pytest.approx(q['union_area_target_um2'] - q['union_area_reference_um2'], abs=1e-9)
        assert 0 < q['observed_fraction'] <= 1


def test_g_row_keys_of_review_rois_do_not_change_in_the_unit_world(big_diag, big_apply):
    for name in ('reference_export', 'target_export'):
        base = json.loads((big_diag.out / name / 'review_rois.json').read_text(encoding='utf-8'))
        got = json.loads((big_apply.out / name / 'review_rois.json').read_text(encoding='utf-8'))
        assert got['rois'] and {tuple(sorted(r)) for r in got['rois']} == {tuple(sorted(r)) for r in base['rois']}


def test_d_unit_fold_keeps_one_representative_and_three_replicas(big_apply, big_fold):
    before, after = queries(big_apply), queries(big_fold)
    r3 = [q for q in after if any(near(q, y, x) for y, x in band_centres('R3'))]
    assert len(r3) == 1 and r3[0]['unit_fold']['n_replicas'] == 3
    fold = r3[0]['unit_fold']
    units = [fold['unit_index_xy']] + [r['unit_index_xy'] for r in fold['replicas']]
    assert sorted(units) == [[0, 0], [0, 1], [1, 0], [1, 1]]            # 대표 1 + 다른 unit 의 복제 3
    for replica in fold['replicas']:
        assert replica['distance_um'] <= FOLD_UM and replica['score_um2'] > 0 and replica['region_id'] and replica['layers'] == ['L1']
        assert len(replica['center_xy_um']) == 2 and len(replica['folded_center_xy_um']) == 2 and replica['rank_before_fold'] > 0
    # 같은 unit 안의 가까운 ROI(RA, RB: 100 px 간격, 허용값 120 px 이내)는 접히지 않는다
    near_pair = [q for q in after if near(q, 310, 310, 70) or near(q, 310, 410, 70)]
    assert len(near_pair) == 2 and all('unit_fold' in q and q['unit_fold']['n_replicas'] == 0 for q in near_pair)
    assert len(before) - len(after) == 3
    # funnel: 단계 unit_folded 가 접은 수를 센다
    for direction in big_fold.payload['funnel']['directions']:
        stages = {s['stage']: s for s in direction['stages']}
        assert stages['unit_folded']['dropped'] == 3 and stages['unit_folded']['reasons'] == {'unit_folded': 3}
        assert stages['merged']['count'] == stages['unit_folded']['count'] + 3
        assert stages['top_k']['count'] == stages['unit_folded']['count']
        assert [s['stage'] for s in direction['stages']].index('unit_folded') + 1 == [s['stage'] for s in direction['stages']].index('top_k')
    assert big_fold.payload['policy']['unit_fold'] == change_candidates.UNIT_FOLD_POLICY
    assert big_fold.payload['change_args']['unit_fold'] is True and big_fold.payload['change_args']['unit_fold_tolerance_um'] == FOLD_UM


def test_d_tiny_tolerance_folds_nothing(big_apply, big_nofold):
    assert len(queries(big_nofold)) == len(queries(big_apply))
    stages = {s['stage']: s for s in big_nofold.payload['funnel']['directions'][0]['stages']}
    assert stages['unit_folded']['dropped'] == 0 and stages['unit_folded']['reasons'] == {}


def test_unit_fold_default_tolerance_is_a_quarter_window(big):
    # 검증만: 허용값 기본값 = window_um / 4 (인수 기록)
    out = big.tmp_path_factory.mktemp('out') / 'out'
    assert run(big, out, '--unit-fold', '--merge-scope', 'all', **BIG) in (0, 3)
    assert tcc.load(out)['change_args']['unit_fold_tolerance_um'] == 1.25


def test_i_windows_of_a_unit_that_is_not_ok_use_the_global_offset(big, monkeypatch):
    real = layer_align.choose_dice_first
    calls = []

    def fake(*args, **kwargs):
        result = real(*args, **kwargs)
        calls.append(1)
        if len(calls) == 5:             # 1 = 전역 교차 확인, 2..5 = unit (0,0) (0,1) (1,0) (1,1)
            result = {**result, 'status': 'search_bound_hit', 'apply': False}
        return result
    monkeypatch.setattr(layer_align, 'choose_dice_first', fake)
    got = make(big, big.tmp_path_factory, '--unit-align-um', '1', '--apply-align', 'unit', **BIG)
    g = got.alignment['offset_xy_px']
    last = got.alignment['unit_offsets'][3]
    assert last['unit_index_xy'] == [1, 1] and last['applied'] is False and last['offset_xy_px'] == g and last['status'] == 'search_bound_hit'
    for q in queries(got):
        if unit_of(q) == (1, 1):
            assert q['offset_source'] == 'global' and q['offset_xy_px_applied'] == g
        else:
            assert q['offset_source'] == 'unit'
    assert any(unit_of(q) == (1, 1) for q in queries(got))
