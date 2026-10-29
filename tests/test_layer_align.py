"""layer-align/T2: layer_align 순수 함수 테스트(unit 기하, Dice 우선 선택, 점수 창, 접기, 이음새, 범위 요구)."""
from __future__ import annotations

import numpy as np
import pytest

from simcompare import layer_align as la


# --------------------------------------------------------------------------- 도우미

def _scores(r, a, table_inter, b=None, default=100):
    """(2r+1)² 점수 창. table_inter: {(dy, dx): I}. b 는 상수 a. Dice = 2I/(a+b) = I/a."""
    n = 2 * r + 1
    inter = np.full((n, n), default, dtype=np.int64)
    for (dy, dx), v in table_inter.items():
        inter[r + dy, r + dx] = v
    return {'a': a, 'inter': inter, 'b': np.full((n, n), a if b is None else b, dtype=np.int64)}


def _choose(scores, r=3, s=(0, 0), min_dice=0.0, **kw):
    return la.choose_dice_first(scores, s, r, min_dice=min_dice, min_support=1, **kw)


def _shift(arr, ty, tx):
    """B[p + t] = A[p] (영상 밖은 버리고 빈 곳은 False)."""
    out = np.zeros_like(arr)
    h, w = arr.shape
    ys, xs = slice(max(0, -ty), min(h, h - ty)), slice(max(0, -tx), min(w, w - tx))
    out[max(0, ty):max(0, ty) + (ys.stop - ys.start), max(0, tx):max(0, tx) + (xs.stop - xs.start)] = arr[ys, xs]
    return out


def _direct(a_img, b_img, omega, s_xy, r):
    sx, sy = s_xy
    n = 2 * r + 1
    inter = np.zeros((n, n), dtype=np.int64)
    sum_b = np.zeros((n, n), dtype=np.int64)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            i = b = 0
            for y, x in zip(*np.nonzero(omega)):
                yy, xx = y + sy + dy, x + sx + dx
                if 0 <= yy < b_img.shape[0] and 0 <= xx < b_img.shape[1] and b_img[yy, xx]:
                    b += 1
                    i += int(a_img[y, x])
            inter[r + dy, r + dx], sum_b[r + dy, r + dx] = i, b
    return int(np.count_nonzero(a_img & omega)), inter, sum_b


def _direct_omega(ref_valid, tgt_valid, s_xy, r, erode):
    sx, sy = s_xy
    h, w = ref_valid.shape
    base = np.zeros((h, w), dtype=bool)
    for y in range(h):
        for x in range(w):
            ok = bool(ref_valid[y, x])
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    yy, xx = y + sy + dy, x + sx + dx
                    ok = ok and 0 <= yy < tgt_valid.shape[0] and 0 <= xx < tgt_valid.shape[1] and bool(tgt_valid[yy, xx])
            base[y, x] = ok
    out = np.zeros((h, w), dtype=bool)
    for y in range(h):
        for x in range(w):
            ok = True
            for dy in range(-erode, erode + 1):
                for dx in range(-erode, erode + 1):
                    yy, xx = y + dy, x + dx
                    ok = ok and 0 <= yy < h and 0 <= xx < w and bool(base[yy, xx])
            out[y, x] = ok
    return out


# --------------------------------------------------------------------------- unit 기하

def test_unit_geometry_even_frame_regions_and_coords():
    g = la.UnitGeometry((2000, 2400))
    assert g.unit_px == (1500, 1700) and not g.unit_size_odd_px
    assert g.boundary_px() == (1000, 1200)
    regions = g.unit_regions()
    assert regions[(0, 0)] == (0, 1000, 0, 1200) and regions[(1, 1)] == (1000, 2000, 1200, 2400)
    covered = np.zeros((2000, 2400), dtype=np.int8)
    for y0, y1, x0, x1 in regions.values():
        covered[y0:y1, x0:x1] += 1
    assert (covered == 1).all()
    assert g.unit_index((999, 1199)) == (0, 0) and g.unit_index((1000, 1200)) == (1, 1)
    assert g.unit_coord((0, 0)) == (500, 500) and g.unit_coord((1000, 1200)) == (0, 0)


def test_unit_geometry_odd_frame_flag_and_partition():
    g = la.UnitGeometry((2001, 2000))
    assert g.unit_px == (1500, 1500) and g.unit_size_odd_px
    assert g.boundary_px() == (1000, 1000)
    regions = g.unit_regions()
    assert regions[(1, 0)] == (1000, 2001, 0, 1000)
    covered = np.zeros((2001, 2000), dtype=np.int8)
    for y0, y1, x0, x1 in regions.values():
        covered[y0:y1, x0:x1] += 1
    assert (covered == 1).all()
    assert g.unit_index((2000, 1999)) == (1, 1)


def test_delta_u_three_pixels_requires_radius_8_and_5():
    ref, tgt = la.UnitGeometry((2000, 2000)), la.UnitGeometry((2006, 2002))
    assert la.delta_u(ref, tgt) == (3, 1) and la.delta_u_max(ref, tgt) == 3
    assert la.required_unit_radius_px(3) == 8
    assert la.required_local_radius_px(3) == 5
    odd = la.UnitGeometry((2001, 2000))
    assert la.delta_u_max(odd, la.UnitGeometry((2007, 2000))) == 3
    assert la.required_unit_radius_px(0) == 2 and la.required_local_radius_px(0) == 2
    assert la.MAX_UNIT_SEARCH_PX == 300 and la.MAX_LOCAL_APPLY_PX == 300


# --------------------------------------------------------------------------- X2: Ω 와 점수 창

@pytest.mark.parametrize('s_xy', [(0, 0), (4, -3), (-5, 2), (-6, -4), (3, 6)])
def test_fixed_omega_matches_brute_force_with_holes_and_edges(s_xy):
    ref_valid = np.ones((24, 30), dtype=bool)
    ref_valid[5:8, 10:14] = False
    tgt_valid = np.ones((28, 34), dtype=bool)
    tgt_valid[12:15, 3:6] = False
    tgt_valid[:, 33] = False
    omega = la.fixed_omega(ref_valid, tgt_valid, s_xy, 2, 1)
    assert (omega == _direct_omega(ref_valid, tgt_valid, s_xy, 2, 1)).all()
    assert omega.any()


@pytest.mark.parametrize('s_xy', [(0, 0), (4, -3), (-5, 2), (-6, -4), (3, 6)])
def test_unit_window_scores_equal_direct_sum_for_nonzero_start(s_xy):
    rng = np.random.RandomState(11)
    a_img = rng.rand(24, 30) < 0.4
    b_img = rng.rand(28, 34) < 0.4
    ref_valid = np.ones((24, 30), dtype=bool)
    ref_valid[5:8, 10:14] = False
    tgt_valid = np.ones((28, 34), dtype=bool)
    tgt_valid[12:15, 3:6] = False
    r = 3
    omega = la.fixed_omega(ref_valid, tgt_valid, s_xy, r, 1)
    a, inter, sum_b = la.unit_window_scores(a_img, b_img, omega, s_xy, r)
    da, dinter, dsum_b = _direct(a_img, b_img, omega, s_xy, r)
    assert a == da
    assert (inter == dinter).all() and (sum_b == dsum_b).all()
    assert inter.dtype == np.int64


def test_omega_does_not_depend_on_delta_and_region_mask_restricts_scores():
    rng = np.random.RandomState(2)
    a_img = rng.rand(30, 30) < 0.5
    b_img = rng.rand(30, 30) < 0.5
    valid = np.ones((30, 30), dtype=bool)
    r, s = 2, (1, -2)
    omega = la.fixed_omega(valid, valid, s, r, 0)
    region = omega.copy()
    region[15:, :] = False
    a, inter, sum_b = la.unit_window_scores(a_img, b_img, region, s, r)
    da, dinter, dsum_b = _direct(a_img, b_img, region, s, r)
    assert a == da and (inter == dinter).all() and (sum_b == dsum_b).all()
    # Ω 는 모든 δ 에서 같은 마스크로 센다: b(δ) 는 Ω 픽셀 수를 넘지 않는다.
    assert int(sum_b.max()) <= int(np.count_nonzero(region))


def test_omega_empty_when_target_cannot_cover_all_candidates():
    valid = np.ones((10, 10), dtype=bool)
    omega = la.fixed_omega(valid, np.ones((10, 10), dtype=bool), (9, 0), 2, 0)
    assert not omega.any()


# --------------------------------------------------------------------------- X1: Dice 우선 선택

def test_x1_tied_first_layer_is_decided_by_next_layer_even_at_distance_one():
    scores = {'layer:L1': _scores(3, 1000, {(0, 0): 900, (0, 1): 900}),
              'layer:L2': _scores(3, 1000, {(0, 0): 500, (0, 1): 800})}
    out = _choose(scores)
    assert out['layer_order'] == ['layer:L1', 'layer:L2']
    assert out['status'] == 'ok' and out['apply'] is True and out['flags'] == []
    assert out['delta_xy_px'] == [1, 0] and out['offset_xy_px'] == [1, 0]
    assert out['decided_by_layer'] == 'layer:L2'
    assert out['ambiguity_set_size_initial'] == 2 and out['ambiguity_set_size'] == 1
    assert out['layers_disagreeing'] == ['layer:L1']
    assert out['per_layer']['layer:L1']['argmax_delta_xy_px'] == [0, 0]
    assert out['per_layer']['layer:L2']['dice_at_chosen'] == 0.8


def test_x1_offset_is_absolute_start_plus_delta_with_xy_order():
    scores = {'layer:L1': _scores(3, 1000, {(-1, 2): 900})}
    out = la.choose_dice_first(scores, (10, -7), 3, min_dice=0.0, min_support=1)
    assert out['delta_xy_px'] == [2, -1]
    assert out['offset_xy_px'] == [12, -8]
    assert out['start_xy_px'] == [10, -7]


def test_x1_subpixel_tie_uses_lexicographic_dice_representative():
    # L1 은 (0,0),(0,1) 동률. L2 는 (0,1) 이 0.005 높지만 ε(0.01) 안 → S 지름 1 로 남는다.
    scores = {'layer:L1': _scores(3, 1000, {(0, 0): 900, (0, 1): 900}),
              'layer:L2': _scores(3, 1000, {(0, 0): 800, (0, 1): 805})}
    out = _choose(scores)
    assert out['status'] == 'ok' and out['flags'] == ['subpixel_tie'] and out['apply'] is True
    assert out['ambiguity_set_size'] == 2
    assert out['delta_xy_px'] == [1, 0]        # (Dice_L1, Dice_L2) 사전순 최대


def test_x1_subpixel_tie_all_equal_prefers_smallest_distance_then_dy_dx():
    scores = {'layer:L1': _scores(3, 1000, {(0, 0): 900, (1, 0): 900, (0, 1): 900}),
              'layer:L2': _scores(3, 1000, {(0, 0): 800, (1, 0): 800, (0, 1): 800})}
    out = _choose(scores)
    # (0,0) 과 (1,0),(0,1): 지름 1. |δ|² 최소 = (0,0).
    assert out['status'] == 'ok' and out['flags'] == ['subpixel_tie']
    assert out['delta_xy_px'] == [0, 0]
    scores = {'layer:L1': _scores(3, 1000, {(1, 0): 900, (0, 1): 900}),
              'layer:L2': _scores(3, 1000, {(1, 0): 800, (0, 1): 800})}
    out = _choose(scores)
    # (1,0) 과 (0,1) 은 체비쇼프 1, |δ|² 동률 → (dy, dx) 사전순 = (0, 1).
    assert out['status'] == 'ok' and out['delta_xy_px'] == [1, 0]


def test_x1_diameter_two_remains_ambiguous_and_is_not_applied():
    scores = {'layer:L1': _scores(3, 1000, {(0, 0): 900, (0, 2): 900}),
              'layer:L2': _scores(3, 1000, {(0, 0): 800, (0, 2): 800})}
    out = _choose(scores)
    assert out['status'] == 'ambiguous' and out['apply'] is False
    assert out['delta_xy_px'] is None and out['offset_xy_px'] is None and out['decided_by_layer'] is None
    assert out['ambiguity_set_size'] == 2


def test_x1_later_layer_resolves_wide_tie():
    scores = {'layer:L1': _scores(4, 1000, {(0, 0): 900, (0, 2): 900}),
              'layer:L2': _scores(4, 1000, {(0, 0): 500, (0, 2): 800})}
    out = _choose(scores, r=4)
    assert out['status'] == 'ok' and out['delta_xy_px'] == [2, 0] and out['decided_by_layer'] == 'layer:L2'


def test_x1_is_deterministic_when_all_layers_tie_regardless_of_input_order():
    table = {(0, 0): 900, (1, 1): 700}
    keys = ['layer:M2', 'layer:M1', 'cnt:M1']
    a = _choose({k: _scores(3, 1000, table) for k in keys})
    b = _choose({k: _scores(3, 1000, table) for k in reversed(keys)})
    assert a == b
    assert a['layer_order'] == ['cnt:M1', 'layer:M1', 'layer:M2']
    assert a['status'] == 'ok' and a['delta_xy_px'] == [0, 0]
    assert a['decided_by_layer'] == 'cnt:M1'


def test_x1_layer_order_is_best_dice_descending_then_key():
    scores = {'layer:A': _scores(3, 1000, {(0, 0): 600}), 'layer:B': _scores(3, 1000, {(0, 0): 900}),
              'cnt:C': _scores(3, 1000, {(0, 0): 600})}
    assert _choose(scores)['layer_order'] == ['layer:B', 'cnt:C', 'layer:A']


def test_x1_low_score_and_insufficient_support_and_excluded_layers():
    low = _choose({'layer:L1': _scores(3, 1000, {(0, 0): 300})}, min_dice=0.5)
    assert low['status'] == 'low_score' and low['apply'] is False and low['delta_xy_px'] is None and low['best_dice'] == 0.3
    thin = la.choose_dice_first({'layer:L1': _scores(3, 50, {(0, 0): 40})}, (0, 0), 3, min_dice=0.0)
    assert thin['status'] == 'insufficient_support' and thin['per_layer']['layer:L1']['excluded_reason'] == 'insufficient_support'
    both = _choose({'layer:L1': _scores(3, 1000, {(0, 0): 900}), 'cnt:X': _scores(3, 1000, {(0, 0): 100})},
                   excluded={'cnt:X': 'one_sided', 'layer:Z': 'png_errors:bad'})
    assert both['status'] == 'ok' and both['layer_order'] == ['layer:L1']
    assert both['per_layer']['cnt:X']['excluded_reason'] == 'one_sided'
    assert both['per_layer']['layer:Z']['excluded_reason'] == 'png_errors:bad'


def test_x1_search_bound_hit_within_one_pixel_of_edge_is_not_applied():
    for table in ({(0, 3): 900}, {(0, 2): 900}, {(-2, -1): 900}):
        out = _choose({'layer:L1': _scores(3, 1000, table)})
        assert out['status'] == 'search_bound_hit' and out['apply'] is False
        assert out['delta_xy_px'] is not None
    inside = _choose({'layer:L1': _scores(3, 1000, {(1, 1): 900})})
    assert inside['status'] == 'ok' and inside['apply'] is True


def test_x1_changed_layer_is_not_excluded_it_simply_ranks_last():
    scores = {'layer:ok1': _scores(3, 1000, {(1, 0): 900}), 'layer:changed': _scores(3, 1000, {(-1, 0): 400})}
    out = _choose(scores)
    assert out['layer_order'] == ['layer:ok1', 'layer:changed']
    assert out['delta_xy_px'] == [0, 1] and out['layers_disagreeing'] == ['layer:changed']
    assert out['per_layer']['layer:changed']['excluded_reason'] is None


# --------------------------------------------------------------------------- X7: 탐색 범위

def _pattern_pair(true_shift_yx, extra=None, seed=3, size=90):
    rng = np.random.RandomState(seed)
    a = rng.rand(size, size) < 0.35
    b = _shift(a, *true_shift_yx)
    if extra is not None:
        b = b | extra(a)
    return a, b


def _run_real(a, b, s_xy, r, min_dice):
    valid = np.ones(a.shape, dtype=bool)
    omega = la.fixed_omega(valid, valid, s_xy, r, 0)
    a_sum, inter, sum_b = la.unit_window_scores(a, b, omega, s_xy, r)
    return la.choose_dice_first({'layer:L1': {'a': a_sum, 'inter': inter, 'b': sum_b}}, s_xy, r, min_dice=min_dice)


def test_x7_true_peak_outside_range_is_caught_by_low_score():
    a, b = _pattern_pair((0, 12))
    out = _run_real(a, b, (0, 0), 3, 0.6)
    assert out['status'] == 'low_score' and out['apply'] is False


def test_x7_true_peak_at_range_edge_is_caught_by_search_bound_hit_with_noncentral_start():
    a, b = _pattern_pair((0, 8))
    out = _run_real(a, b, (5, 0), 3, 0.6)         # 진짜 offset 8 = s + 3 → 경계
    assert out['status'] == 'search_bound_hit' and out['apply'] is False
    assert out['offset_xy_px'] == [8, 0]


def test_x7_inner_peak_inside_range_is_found_with_noncentral_start():
    a, b = _pattern_pair((-2, 6))
    out = _run_real(a, b, (4, -1), 4, 0.6)
    assert out['status'] == 'ok' and out['offset_xy_px'] == [6, -2] and out['delta_xy_px'] == [2, -1]


def test_x7_documented_limit_outside_true_peak_plus_inner_partial_peak_is_not_blocked():
    # 진짜 peak 는 범위 밖(x+12). 안쪽 부차 peak(x+1, 절반 행)가 min_dice 를 넘으면 원리상 막지 못한다(문서화된 한계).
    def half(a):
        part = a.copy()
        part[::2, :] = False
        return _shift(part, 0, 1)
    a, b = _pattern_pair((0, 12), extra=half)
    out = _run_real(a, b, (0, 0), 3, 0.2)
    assert out['status'] == 'ok' and out['offset_xy_px'] == [1, 0]     # 틀린 offset 이 적용된다 = 알려진 한계
    # 같은 입력도 min_dice 를 올리면 low_score 로 막힌다.
    assert _run_real(a, b, (0, 0), 3, 0.8)['status'] == 'low_score'


# --------------------------------------------------------------------------- A1: 접기

def _item(region_id, y, x, rank=None):
    item = {'region_id': region_id, 'center_yx_px': (y, x)}
    if rank is not None:
        item['rank'] = rank
    return item


def test_fold_four_replicas_become_one_representative_with_three_replicas():
    g = la.UnitGeometry((2000, 2000))           # unit 1500
    items = [_item('r1', 200, 300), _item('r2', 1701, 300), _item('r3', 200, 1799), _item('r4', 1700, 1800), _item('far', 400, 500)]
    kept, replicas = la.fold_rois(items, g, 2.0)
    assert [i['region_id'] for i in kept] == ['r1', 'far']
    assert [r['region_id'] for r in replicas['r1']] == ['r2', 'r3', 'r4']
    assert replicas['far'] == []
    rep = replicas['r1'][0]
    assert rep['rank'] == 2 and rep['unit_index'] == [1, 0] and rep['center_yx_px'] == [1701, 300]
    assert rep['folded_yx_px'] == [701, 800] and rep['distance_px'] == 1.0


def test_fold_does_not_fold_close_rois_inside_one_unit():
    g = la.UnitGeometry((2000, 2000))
    kept, replicas = la.fold_rois([_item('a', 200, 300), _item('b', 203, 300), _item('c', 200, 305)], g, 50.0)
    assert [i['region_id'] for i in kept] == ['a', 'b', 'c']
    assert all(v == [] for v in replicas.values())


def test_fold_toroidal_distance_across_unit_wrap_and_tolerance_is_inclusive():
    g = la.UnitGeometry((2000, 2000))
    # p=999 → coord 1499 (unit 0), p=1000 → coord 0 (unit 1). 원환 거리 1.
    items = [_item('left', 999, 300), _item('right', 1000, 300)]
    kept, replicas = la.fold_rois(items, g, 1.0)
    assert [i['region_id'] for i in kept] == ['left'] and replicas['left'][0]['distance_px'] == 1.0
    kept, replicas = la.fold_rois(items, g, 0.999)
    assert [i['region_id'] for i in kept] == ['left', 'right']
    kept, _ = la.fold_rois([_item('a', 999, 299), _item('b', 1000, 301)], g, 5 ** 0.5)       # (1, 2) → √5 포함
    assert len(kept) == 1
    kept, _ = la.fold_rois([_item('a', 999, 299), _item('b', 1000, 301)], g, 2.2)
    assert len(kept) == 2


def test_fold_assigns_to_first_representative_in_rank_order():
    g = la.UnitGeometry((2000, 2000))
    items = [_item('top', 200, 300), _item('mid', 203, 304), _item('copy', 1700, 300)]
    kept, replicas = la.fold_rois(items, g, 2.0)
    assert [i['region_id'] for i in kept] == ['top', 'mid']
    assert [r['region_id'] for r in replicas['top']] == ['copy'] and replicas['mid'] == []


# --------------------------------------------------------------------------- 이음새

def test_seam_band_empty_when_all_offsets_equal_and_widths_otherwise():
    g = la.UnitGeometry((2000, 2000))
    same = {(iy, ix): (3, -2) for iy in (0, 1) for ix in (0, 1)}
    assert not la.seam_band(g, same).any()
    offsets = {(0, 0): (0, 0), (0, 1): (4, 0), (1, 0): (0, 2), (1, 1): (0, 0)}
    band = la.seam_band(g, offsets)
    # 세로 경계 x=1000: 위 행(unit 행 0)은 폭 4, 아래 행(unit 행 1)은 폭 2.
    assert band[10, 996:1004].all() and not band[10, 995] and not band[10, 1004]
    assert band[1990, 998:1002].all() and not band[1990, 997] and not band[1990, 1002]
    # 가로 경계 y=1000: 왼쪽 열(unit 열 0)은 폭 2, 오른쪽 열(unit 열 1)은 폭 4.
    assert band[998:1002, 10].all() and not band[997, 10] and not band[1002, 10]
    assert band[996:1004, 1990].all() and not band[995, 1990] and not band[1004, 1990]
    assert not band[10, 10] and not band[1990, 10]


# --------------------------------------------------------------------------- 바이트 추정

def test_estimate_layer_bytes_grows_with_radius_and_frame():
    small = la.estimate_layer_bytes((1000, 1000), (1000, 1000), 8)
    assert small > 0
    assert la.estimate_layer_bytes((1000, 1000), (1000, 1000), 80) > small
    assert la.estimate_layer_bytes((2000, 2000), (2000, 2000), 8) > small
