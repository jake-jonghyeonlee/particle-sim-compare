"""stretch-align/T4: consensus label (``unit_fold.consensus``) and target-side coordinates of the folded representative / replicas.

Only ``--apply-align stretch --unit-fold`` adds the fields (``consensus``, ``offset_xy_px_applied``, ``offset_source``, ``target_center_xy_um``,
``target_unit_index_xy``, ``target_unit_coord_xy_um``); the label never changes rank, fold decisions or top-k (L8).

* pure tests call ``change_candidates._fold_merged`` with a hand-made ranked list, a tile lookup and two frames.
* integration tests run the drift world of ``test_stretch_cli`` (change in unit (1, 1) only) and a copy whose change sits in all four units.
"""
from types import SimpleNamespace

import numpy as np
import pytest

import test_change_candidates as tcc
import test_stretch_cli as tsc
from simcompare import change_candidates, layer_align
from test_stretch_cli import drift  # noqa: F401  (module-scoped fixture of the T2 test module)

PITCH = 0.1
ORIGIN = 50.0
NEW_KEYS = {'consensus', 'offset_xy_px_applied', 'offset_source', 'target_center_xy_um', 'target_unit_index_xy', 'target_unit_coord_xy_um'}


# --------------------------------------------------------------------------------------------------------------------
# pure: _fold_merged

def frame(origin):
    return SimpleNamespace(origin_xy_um=origin, pixel_pitch_um=PITCH)


def entry(rid, y, x):
    """window centre at reference pixel (y, x)."""
    center = (ORIGIN + x * PITCH + PITCH / 2, ORIGIN + y * PITCH + PITCH / 2)
    return {'representative': SimpleNamespace(region_id=rid, center_xy_um=center, score=1.0, layer='L1'),
            'members': [SimpleNamespace(layer='L1')]}, {rid: {'window_px': 50, 'window_origin_px': (y - 25, x - 25)}}


def ranked(*specs):
    merged, by_id = [], {}
    for spec in specs:
        item, window = entry(*spec)
        merged.append(item)
        by_id.update(window)
    return merged, by_id


def ctx(table, tgt_origin=(ORIGIN, ORIGIN)):
    """table: [(x_end, (dx, dy), source)] 열 경계 순; 타일은 x 만으로 정해진다."""
    def lookup(y, x):
        for i, (x_end, offset, source) in enumerate(table):
            if x < x_end:
                return offset, source, i
        raise ValueError((y, x))
    return {'lookup': lookup, 'ref_frame': frame((ORIGIN, ORIGIN)), 'tgt_frame': frame(tgt_origin),
            'tgt_geometry': layer_align.UnitGeometry((1310, 1310))}


REF = layer_align.UnitGeometry((1300, 1300))
TABLE = [(300, (2, 1), 'stretch'), (700, (6, -1), 'unit'), (2000, (9, 3), 'global')]
Y = 200      # unit (0, *) in both products


def fold(specs, table=TABLE, tol_um=1.5, **kw):
    merged, by_id = ranked(*specs)
    return change_candidates._fold_merged(merged, by_id, REF, tol_um, PITCH, ctx(table, **kw))


def test_four_units_fold_into_one_with_k4_and_each_replica_has_its_own_offset():
    # 같은 unit 좌표 (Y, 100): unit 번호 (0,0) x=100(→ +400 = 500), unit (0,1) x=1250-500... 좌표는 reference frame 픽셀
    xs = [100, 100 + 1150, 100, 100 + 1150]
    ys = [Y, Y, Y + 1150, Y + 1150]
    kept, info = fold([(f'r{i}', y, x) for i, (y, x) in enumerate(zip(ys, xs))])
    assert [e['representative'].region_id for e in kept] == ['r0']
    got = info['r0']
    assert got['n_replicas'] == 3 and got['consensus'] == {'k': 4, 'n': 4, 'units_present_xy': [[0, 0], [0, 1], [1, 0], [1, 1]]}
    assert got['offset_xy_px_applied'] == [2, 1] and got['offset_source'] == 'stretch'
    by_region = {r['region_id']: r for r in got['replicas']}
    assert by_region['r1']['offset_xy_px_applied'] == [9, 3] and by_region['r1']['offset_source'] == 'global'       # x = 1250
    assert by_region['r2']['offset_xy_px_applied'] == [2, 1]
    assert by_region['r1']['offset_xy_px_applied'] != got['offset_xy_px_applied']      # 대표와 replica 의 적용 offset 은 서로 다르다


def test_two_replicas_of_the_same_unit_count_one_unit():
    """X2 반례: 같은 unit 의 replica 둘 → k=2(고유 unit 수), 1 + n_replicas = 3 이 아니다. 어떤 경우에도 k ≤ 4."""
    kept, info = fold([('rep', Y, 100), ('a', Y, 1150 + 100), ('b', Y + 3, 1150 + 104)])
    got = info['rep']
    assert [e['representative'].region_id for e in kept] == ['rep'] and got['n_replicas'] == 2
    assert got['consensus'] == {'k': 2, 'n': 4, 'units_present_xy': [[0, 0], [1, 0]]}      # [ix, iy]
    assert got['consensus']['k'] != 1 + got['n_replicas']
    assert 1 <= got['consensus']['k'] <= got['consensus']['n']


def test_single_unit_keeps_the_roi_with_k1():
    kept, info = fold([('only', 1150 + Y, 1150 + 100)])
    assert [e['representative'].region_id for e in kept] == ['only']
    assert info['only']['n_replicas'] == 0 and info['only']['consensus'] == {'k': 1, 'n': 4, 'units_present_xy': [[1, 1]]}


def test_target_side_coordinates_use_each_rois_own_offset_and_the_target_geometry():
    kept, info = fold([('rep', Y, 100), ('rep2', Y, 1150 + 100)])
    got = info['rep']
    # 대표: reference 중심 + 자기 offset (2, 1) px. target frame 원점이 같다.
    assert got['target_center_xy_um'] == pytest.approx([ORIGIN + 100 * PITCH + PITCH / 2 + 2 * PITCH, ORIGIN + Y * PITCH + PITCH / 2 + PITCH], abs=1e-6)
    tgt = layer_align.UnitGeometry((1310, 1310))
    assert got['target_unit_index_xy'] == [tgt.unit_index((Y + 1, 102))[1], tgt.unit_index((Y + 1, 102))[0]]
    coord = tgt.unit_coord((Y + 1, 102))
    assert got['target_unit_coord_xy_um'] == [round(coord[1] * PITCH, 6), round(coord[0] * PITCH, 6)]
    rep2 = got['replicas'][0]      # x = 1250 → offset (9, 3)
    assert rep2['target_center_xy_um'] == pytest.approx([ORIGIN + 1250 * PITCH + PITCH / 2 + 9 * PITCH, ORIGIN + Y * PITCH + PITCH / 2 + 3 * PITCH], abs=1e-6)
    assert rep2['target_center_xy_um'] != got['target_center_xy_um']
    # 자기 제품 좌표(reference 측)는 그대로 있고 target 좌표와 별개 키다
    assert rep2['unit_index_xy'] == [1, 0] and rep2['target_unit_index_xy'] == [1, 0]
    assert NEW_KEYS - {'consensus'} <= set(rep2) and NEW_KEYS <= set(got)


def test_target_unit_boundary_crossing_and_different_frame_origin():
    """ref x=645 는 reference unit 0(경계 650), offset +10 이면 target x=655 로 target 경계(655)를 넘어 unit 1. ΔU = 5 px, frame 원점이 다르다."""
    table = [(2000, (10, 0), 'stretch')]
    kept, info = fold([('edge', Y, 645)], table=table, tgt_origin=(ORIGIN + 0.7, ORIGIN - 0.4))
    got = info['edge']
    assert got['unit_index_xy'] == [0, 0] and got['target_unit_index_xy'] == [1, 0]
    assert got['target_center_xy_um'] == pytest.approx([ORIGIN + 645 * PITCH + PITCH / 2 + 10 * PITCH + 0.7, ORIGIN + Y * PITCH + PITCH / 2 - 0.4], abs=1e-6)
    # target frame 안의 칸: 원점 차이는 상쇄되고 칸 = reference 칸 + offset 이다
    assert got['target_unit_coord_xy_um'] == [round(0 * PITCH, 6), round(((Y + 500) % 1155) * PITCH, 6)]
    assert got['offset_xy_px_applied'] == [10, 0]
    # ΔU = 0 이었다면 같은 칸은 unit 0 의 마지막 칸(650 경계의 바로 앞)이다: 경계 통과가 target 기하 때문임을 확인
    same = layer_align.UnitGeometry((1300, 1300))
    assert same.unit_index((Y, 655))[1] == 1 and layer_align.UnitGeometry((1310, 1310)).unit_index((Y, 654))[1] == 0


def test_fold_decision_is_the_same_with_and_without_stretch_fields():
    specs = [('r0', Y, 100), ('r1', Y, 1150 + 104), ('r2', Y + 2000 - 1000, 900), ('r3', Y, 100 + 1150 + 3)]
    merged, by_id = ranked(*specs)
    plain_kept, plain_info = change_candidates._fold_merged(merged, by_id, REF, 1.5, PITCH)
    kept, info = change_candidates._fold_merged(merged, by_id, REF, 1.5, PITCH, ctx(TABLE))
    assert [e['representative'].region_id for e in kept] == [e['representative'].region_id for e in plain_kept]
    for rid in plain_info:
        assert set(info[rid]) - set(plain_info[rid]) == {'consensus', 'offset_xy_px_applied', 'offset_source', 'target_center_xy_um',
                                                          'target_unit_index_xy', 'target_unit_coord_xy_um'}
        assert {k: v for k, v in info[rid].items() if k in plain_info[rid] and k != 'replicas'} == {k: v for k, v in plain_info[rid].items() if k != 'replicas'}
        assert [r['region_id'] for r in info[rid]['replicas']] == [r['region_id'] for r in plain_info[rid]['replicas']]
    assert not NEW_KEYS & {k for rid in plain_info for k in plain_info[rid]}
    assert not NEW_KEYS & {k for rid in plain_info for r in plain_info[rid]['replicas'] for k in r}


def test_euclidean_tolerance_and_the_stride_grid_a3():
    """A3: 창 후보는 stride(=window//4)격자라 같은 위치의 ROI 가 unit 마다 두 축으로 양자화된다. fold 판정은 유클리드 거리라
    축별 차이 d 가 tolerance/√2 보다 크면 접히지 않는다(tolerance = window/4 = stride 와 같은 값이면 한 축에 stride 미만씩 허용된다)."""
    stride = 12           # window 5 µm = 50 px → stride 12 px, 기본 fold tolerance = window/4 = 12.5 px
    tol = 12.5 * PITCH
    items = lambda d: [('a', Y, 100), ('b', Y + d, 1150 + 100 + d)]      # noqa: E731
    assert fold(items(8), tol_um=tol)[1]['a']['n_replicas'] == 1           # √2 · 8 = 11.3 ≤ 12.5
    assert fold(items(stride - 1), tol_um=tol)[1]['a']['n_replicas'] == 0  # √2 · 11 = 15.6 > 12.5: 양쪽 축에서 stride-1 어긋나면 접히지 않는다


# --------------------------------------------------------------------------------------------------------------------
# integration: real pipeline

FOLD = ('--unit-fold',)
STRETCH_FOLD = (*tsc.STRETCH, *FOLD)


def all_unit_mask(side, case):
    def make(iy, ix, ut):
        base = tsc.feature_unit(tsc.FEATURES)
        if side == 'reference':
            return base
        out = tsc.warp(base, ut)
        if case == tcc.CASE_AB:
            moved = tsc.warp(tsc.feature_unit([tsc.CHANGE_AT]), ut)
            rows, cols = np.flatnonzero(moved.any(axis=1)), np.flatnonzero(moved.any(axis=0))
            out[rows[0]:rows[-1] + 1, cols[-1] + 1:cols[-1] + 1 + tsc.CHANGE_COLS] = True
        return out
    size = 1300 if side == 'reference' else 1300 + 2 * tsc.EXTRA
    return tsc.canvas(side, make)[500:500 + size, 500:500 + size]


@pytest.fixture(scope='module')
def four(tmp_path_factory):
    base = tmp_path_factory.mktemp('four_world')
    datas, assignments = {}, {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(tsc, 'hotspot_mask', all_unit_mask)
        for side in ('reference', 'target'):
            datas[side], assignments[side] = tsc.build_side(base, side)
    config = tcc.write_config(base, datas, assignments, map_spec={'diameter_um': 5})
    return SimpleNamespace(base=base, config=config, dl1=base / 'dl1', tmp_path_factory=tmp_path_factory)


@pytest.fixture(scope='module')
def four_fold(four):
    return tsc.make(four, *STRETCH_FOLD)


@pytest.fixture(scope='module')
def drift_fold(drift):
    return tsc.make(drift, *STRETCH_FOLD)


def folded(run_, direction='reference_to_target'):
    return [q for q in tsc.queries(run_, direction) if q.get('unit_fold')]


def test_same_change_in_all_four_units_folds_into_one_roi_with_k4(four_fold):
    assert four_fold.code in (0, 3)
    qs = folded(four_fold)
    top = [q for q in qs if q['unit_fold']['n_replicas'] == 3]
    assert len(top) == 1, [(q['unit_fold']['n_replicas'], q['score_um2']) for q in qs]
    fold = top[0]['unit_fold']
    assert fold['consensus'] == {'k': 4, 'n': 4, 'units_present_xy': [[0, 0], [0, 1], [1, 0], [1, 1]]}
    assert {tuple(r['unit_index_xy']) for r in fold['replicas']} | {tuple(fold['unit_index_xy'])} == {(0, 0), (0, 1), (1, 0), (1, 1)}
    # 같은 unit 좌표의 변경은 하나만 남는다(ROI 가 네 개로 남지 않는다)
    assert not [q for q in qs if q is not top[0] and q['score_um2'] > 0 and q['unit_fold']['n_replicas'] == 0
                and abs(q['unit_fold']['folded_center_xy_um'][0] - fold['folded_center_xy_um'][0]) < 1.25
                and abs(q['unit_fold']['folded_center_xy_um'][1] - fold['folded_center_xy_um'][1]) < 1.25]
    for q in qs:
        assert 1 <= q['unit_fold']['consensus']['k'] <= 4


def test_fold_survives_stride_quantisation_on_both_axes(four_fold):
    """A3 확인: 이 world 의 unit 크기 1150 = 12 × 95 + 10 이라 unit 마다 창 격자가 두 축으로 -2 px 씩 어긋난다(√2·2 = 2.8 px ≪ 12.5 px)."""
    fold = [q for q in folded(four_fold) if q['unit_fold']['n_replicas'] == 3][0]['unit_fold']
    assert max(r['distance_um'] for r in fold['replicas']) <= four_fold.payload['change_args']['unit_fold_tolerance_um'] + 1e-9
    assert layer_align.UnitGeometry((1300, 1300)).unit_px[0] % 12 == 10      # stride 12


def test_one_unit_change_stays_with_k1(drift_fold):
    top = tsc.queries(drift_fold)[0]
    assert tsc.is_change(top) and top['score_um2'] > 0
    assert top['unit_fold']['n_replicas'] == 0
    assert top['unit_fold']['consensus'] == {'k': 1, 'n': 4, 'units_present_xy': [[1, 1]]}


def test_both_directions_target_side_coordinates(four_fold):
    fwd, back = tsc.queries(four_fold), tsc.queries(four_fold, 'target_to_reference')
    by_region = {q['region_id']: q for q in back}
    assert len(fwd) == len(back) and fwd
    D = None
    for q in fwd:
        fold, mate = q['unit_fold'], by_region[q['region_id']]
        # reference→target 질의: 중심은 reference, unit_fold 의 target_* 이 target 측. target→reference 질의: 중심·자기 제품 좌표가 target 측이다.
        assert fold['target_center_xy_um'] == mate['center_xy_um']
        assert fold['target_unit_index_xy'] == mate['unit_index_xy'] and fold['target_unit_coord_xy_um'] == mate['unit_coord_xy_um']
        assert fold['offset_xy_px_applied'] == q['offset_xy_px_applied'] == mate['offset_xy_px_applied']
        assert fold['offset_source'] == q['offset_source']
        # 자기 제품 좌표(reference 측)는 target_* 과 따로 있다
        assert q['unit_index_xy'] == fold['unit_index_xy']
        rois = [(q['center_xy_um'], fold['offset_xy_px_applied'], fold['target_center_xy_um'])]
        rois += [(r['center_xy_um'], r['offset_xy_px_applied'], r['target_center_xy_um']) for r in fold['replicas']]
        for center, offset, target in rois:      # frame 원점 차이는 모든 ROI 에서 같고, 나머지는 자기 offset 이다
            shift = [target[0] - center[0] - offset[0] * PITCH, target[1] - center[1] - offset[1] * PITCH]
            D = D or shift
            assert shift == pytest.approx(D, abs=1e-5)
        assert q['status'] in ('ready', 'blocked')


def test_offsets_differ_between_representative_and_replicas(four_fold):
    pairs = [(q['unit_fold']['offset_xy_px_applied'], r['offset_xy_px_applied']) for q in folded(four_fold) for r in q['unit_fold']['replicas']]
    assert pairs and any(a != b for a, b in pairs)
    for q in folded(four_fold):
        assert q['unit_fold']['offset_source'] in ('stretch', 'unit', 'global')
        assert all(r['offset_source'] in ('stretch', 'unit', 'global') for r in q['unit_fold']['replicas'])


def strip_consensus(real):
    def wrapped(*args, **kwargs):
        kept, info = real(*args, **kwargs)
        for record in info.values():
            record.pop('consensus')
        return kept, info
    return wrapped


def rank_view(run_):
    return {d: [(q['region_id'], q['score_um2'], q['center_xy_um'], q['unit_fold']['n_replicas'], q['merged_from'])
                for q in tsc.queries(run_, d)] for d in ('reference_to_target', 'target_to_reference')}, \
        [{k: v for k, v in f.items() if k != 'path'} for f in run_.payload.get('funnels', [])] or run_.payload.get('funnel')


def test_the_label_does_not_change_rank_fold_or_top_k(four, four_fold, monkeypatch):
    monkeypatch.setattr(change_candidates, '_fold_merged', strip_consensus(change_candidates._fold_merged))
    without = tsc.make(four, *STRETCH_FOLD)
    assert all('consensus' not in q['unit_fold'] for q in folded(without))
    assert rank_view(without) == rank_view(four_fold)
    monkeypatch.undo()
    for k in (1, 2):      # 라벨이 있는 실행의 top-k 는 라벨 없는 전체 순위의 앞 k 개다
        top_k = tsc.queries(tsc.make(four, *STRETCH_FOLD, top_k=k))
        assert [(q['region_id'], q['score_um2']) for q in top_k] == [(q['region_id'], q['score_um2']) for q in tsc.queries(four_fold)[:k]]


def test_no_new_keys_without_stretch(four):
    """stretch 없는 --unit-fold(unit 정렬 적용·미적용)의 unit_fold 에는 새 키가 없다."""
    for extra in (FOLD, (*tsc.UNIT, *FOLD)):
        got = tsc.make(four, *extra)
        qs = folded(got)
        assert qs and any(q['unit_fold']['n_replicas'] for q in qs)
        for q in qs:
            assert not NEW_KEYS & set(q['unit_fold']) and not {'crosses_tile_boundary', 'seam_excluded_pixels'} & set(q)
            assert all(not NEW_KEYS & set(r) for r in q['unit_fold']['replicas'])
