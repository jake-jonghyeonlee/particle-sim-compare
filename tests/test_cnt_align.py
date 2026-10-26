"""change-roi/T4: CNT 정렬 모듈(합성 입력). 부호: p_target = p_reference + offset."""
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from simcompare import cnt_align
from simcompare.adapters.electrode_png import load_cnt_layers
from simcompare.cnt_align import align_products, estimate_alignment_bytes
from simcompare.numeric_schema import NumericContractError

SRC = str(Path(__file__).resolve().parents[1] / 'src')
BIG = 10 ** 11
KW = dict(pitch_um=0.1, search_um=10.0, min_dice=0.3, layer_tolerance_um=0.5, max_operation_bytes=BIG)

# 3.9 인터프리터 subprocess 에서도 그대로 쓰는 자기완결 사례 생성기(numpy 와 simcompare 만 쓴다).
CASE_SRC = '''
import numpy as np


def random_world(seed, shape=(900, 900), density=0.012, layers=3):
    rng = np.random.default_rng(seed)
    return [rng.random(shape) < density for _ in range(layers)]


def cut(world, y0, x0, h, w):
    return [layer[y0:y0 + h, x0:x0 + w].copy() for layer in world]


def identifiable_case():
    world = random_world(7)
    dx, dy = 13, -7
    ref0, shape_r, shape_t = (150, 160), (400, 360), (380, 420)
    tgt0 = (ref0[0] - dy, ref0[1] - dx)
    ref = {'layers': dict(zip(('M1', 'M2', 'V'), cut(world, ref0[0], ref0[1], *shape_r))),
           'valid': np.ones(shape_r, bool)}
    tgt = {'layers': dict(zip(('M1', 'M2', 'V'), cut(world, tgt0[0], tgt0[1], *shape_t))),
           'valid': np.ones(shape_t, bool)}
    return ref, tgt, dict(pitch_um=0.1, search_um=10.0, min_dice=0.3, layer_tolerance_um=0.5,
                          max_operation_bytes=10 ** 11), (dx, dy)


def ambiguous_case():
    ref_a = np.zeros((25, 25), bool)
    tgt_a = np.zeros((25, 25), bool)
    for y in (5, 15):
        for x in (8, 18):
            ref_a[y, x] = True
        for x in (4, 14, 24):
            tgt_a[y, x] = True
    ref = {'layers': {'M1': ref_a}, 'valid': np.ones((25, 25), bool)}
    tgt = {'layers': {'M1': tgt_a}, 'valid': np.ones((25, 25), bool)}
    return ref, tgt, dict(pitch_um=1.0, search_um=8.0, min_dice=0.0, layer_tolerance_um=0.5,
                          max_operation_bytes=10 ** 11), (6, 0)


def plateau_case():
    """ref 는 한 칸 점, target 은 같은 점을 3x3 으로 키운 것(점 크기만 다름) → 최댓값 동률 plateau."""
    rng = np.random.default_rng(5)
    ref = np.zeros((300, 300), bool)
    for gy in range(1, 36):
        for gx in range(1, 36):
            if rng.random() < 0.25:
                ref[gy * 8, gx * 8] = True
    tgt = np.zeros((320, 330), bool)
    ys, xs = np.nonzero(ref)
    for oy in (-1, 0, 1):
        for ox in (-1, 0, 1):
            tgt[ys + 3 + oy, xs + 23 + ox] = True
    return ({'layers': {'M1': ref}, 'valid': np.ones((300, 300), bool)},
            {'layers': {'M1': tgt}, 'valid': np.ones((320, 330), bool)},
            dict(pitch_um=0.1, search_um=10.0, min_dice=0.0, layer_tolerance_um=0.5,
                 max_operation_bytes=10 ** 11), (23, 3))
'''
_ns = {}
exec(CASE_SRC, _ns)
random_world, cut = _ns['random_world'], _ns['cut']
identifiable_case, ambiguous_case = _ns['identifiable_case'], _ns['ambiguous_case']
plateau_case = _ns['plateau_case']


def pair(world, ref0, tgt0, shape_r, shape_t, names=('M1', 'M2', 'V')):
    ref = {'layers': dict(zip(names, cut(world, ref0[0], ref0[1], *shape_r))), 'valid': np.ones(shape_r, bool)}
    tgt = {'layers': dict(zip(names, cut(world, tgt0[0], tgt0[1], *shape_t))), 'valid': np.ones(shape_t, bool)}
    return ref, tgt


def tgt_origin(ref0, dx, dy):
    """target = reference + offset 이므로 tgt 원점 = ref 원점 - offset."""
    return ref0[0] - dy, ref0[1] - dx


@pytest.mark.parametrize('dx,dy', [(13, -7), (0, 0), (-40, 25), (-98, 3), (5, 97), (99, 0), (-60, -60)])
def test_identifiable_shifts_are_recovered_exactly(dx, dy):
    world = random_world(11)
    ref0 = (200, 200)
    ref, tgt = pair(world, ref0, tgt_origin(ref0, dx, dy), (400, 360), (380, 420))
    out = align_products(ref, tgt, **KW)
    assert out['offset_xy_px'] == [dx, dy]
    assert out['offset_xy_um'] == [round(dx * 0.1, 6), round(dy * 0.1, 6)]
    assert out['status'] == 'aligned', out['reasons']
    assert out['sign'] == 'p_target = p_reference + offset'
    assert out['layers_used'] == ['M1', 'M2', 'V']
    assert 0.3 < out['dice_best'] < 1.0
    json.dumps(out, sort_keys=True, allow_nan=False)


def test_two_by_two_repeat_with_boundary_information_gives_true_offset_not_period():
    rng = np.random.default_rng(3)
    tile = rng.random((100, 100)) < 0.03
    world = np.tile(tile, (4, 4))
    world[60:66, 70:76] = True                 # 주기를 깨는 경계 정보
    world[230:238, 310:316] = True
    dx, dy = 20, -10
    ref0 = (50, 50)
    tgt0 = tgt_origin(ref0, dx, dy)
    ref = {'layers': {'M1': world[50:350, 50:350]}, 'valid': np.ones((300, 300), bool)}
    tgt = {'layers': {'M1': world[tgt0[0]:tgt0[0] + 300, tgt0[1]:tgt0[1] + 300]}, 'valid': np.ones((300, 300), bool)}
    out = align_products(ref, tgt, **{**KW, 'search_um': 15.0})
    assert out['offset_xy_px'] == [dx, dy]
    periods = [tuple(v['vector_xy_um']) for v in out['pattern_period_vectors_um']]
    assert (10.0, 0.0) in periods and (0.0, 10.0) in periods


def test_unidentifiable_pattern_is_deterministic_ambiguous_and_lists_truth():
    ref, tgt, kw, truth = ambiguous_case()
    first = align_products(ref, tgt, **kw)
    second = align_products(ref, tgt, **kw)
    assert first == second
    assert 'peak_ambiguous' in first['reasons'] and first['status'] == 'aligned_uncertain'
    assert first['peak_ratio'] == 1.0
    assert first['offset_xy_px'] == [-4, 0]          # 동점이면 |d| 최소
    assert list(truth) in [a['offset_xy_px'] for a in first['alternatives']]
    assert len(first['alternatives']) <= 5


def test_partial_cnt_change_keeps_true_offset_with_dice_below_one():
    world = random_world(5)
    rng = np.random.default_rng(99)
    dx, dy = -22, 17
    ref0 = (180, 190)
    ref, tgt = pair(world, ref0, tgt_origin(ref0, dx, dy), (400, 400), (400, 400))
    for name, layer in tgt['layers'].items():
        ys, xs = np.nonzero(layer)
        pick = rng.permutation(len(ys))[: int(0.2 * len(ys))]
        layer[ys[pick], xs[pick]] = False           # 삭제
        layer[rng.integers(0, 400, len(pick)), rng.integers(0, 400, len(pick))] = True   # 생성
    out = align_products(ref, tgt, **KW)
    assert out['offset_xy_px'] == [dx, dy]
    assert out['dice_best'] < 1.0


def test_layer_moving_alone_is_flagged_but_result_is_best_effort():
    world = random_world(21)
    dx, dy = 10, 6
    ref0 = (200, 200)
    ref, tgt = pair(world, ref0, tgt_origin(ref0, dx, dy), (400, 400), (400, 400))
    odd = tgt_origin(ref0, dx + 30, dy - 12)
    tgt['layers']['V'] = cut(world, odd[0], odd[1], 400, 400)[2]
    out = align_products(ref, tgt, **KW)
    assert out['offset_xy_px'] == [dx, dy]
    assert 'layer_offsets_disagree' in out['reasons']
    assert out['status'] == 'aligned_uncertain'
    assert out['per_layer']['V']['offset_xy_um'] == [round((dx + 30) * 0.1, 6), round((dy - 12) * 0.1, 6)]


def brute_best(a, b, r, min_support=0.5):
    """전수 탐색 기대값: 지지(두 valid=전부 True 인 겹침) >= min_support * 면적인 |d| <= r 중 교집합 최대, 동률은 |d| 최소 → (dy, dx)."""
    best = None
    h, w = a.shape
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dy * dy + dx * dx > r * r:
                continue
            ys, ye = max(0, -dy), min(h, b.shape[0] - dy)
            xs, xe = max(0, -dx), min(w, b.shape[1] - dx)
            if ys >= ye or xs >= xe:
                continue
            support = (ye - ys) * (xe - xs)
            if support < min_support * min(a.size, b.size):
                continue
            inter = int((a[ys:ye, xs:xe] & b[ys + dy:ye + dy, xs + dx:xe + dx]).sum())
            key = (-inter, dy * dy + dx * dx, dy, dx)
            if best is None or key < best[0]:
                best = (key, [dx, dy])
    return best[1]


def test_valid_hole_and_support_exclusion_and_search_bound():
    world = random_world(31)
    dx, dy = 8, -5
    ref0 = (200, 200)
    ref, tgt = pair(world, ref0, tgt_origin(ref0, dx, dy), (300, 300), (300, 300))
    ref['valid'][100:200, 100:200] = False
    tgt['valid'][50:120, 200:260] = False
    out = align_products(ref, tgt, **KW)
    assert out['offset_xy_px'] == [dx, dy]
    # 지지 면적: 40 % 만 겹치는 참 이동(60 px)은 후보에서 빠진다
    base = random_world(32, shape=(160, 260), layers=1)
    ref = {'layers': {'M1': base[0][:, 0:100]}, 'valid': np.ones((160, 100), bool)}
    tgt = {'layers': {'M1': base[0][:, 60:160]}, 'valid': np.ones((160, 100), bool)}
    wide = align_products(ref, tgt, pitch_um=1.0, search_um=80.0, min_dice=0.0, layer_tolerance_um=5.0,
                          max_operation_bytes=BIG)
    assert wide['offset_xy_px'] != [-60, 0]
    assert wide['offset_xy_px'] == brute_best(base[0][:, 0:100], base[0][:, 60:160], 80)
    assert wide['overlap_area_um2'] >= 0.5 * 160 * 100
    # 탐색 경계
    ref0 = (300, 300)
    ref, tgt = pair(random_world(33), ref0, tgt_origin(ref0, 100, 0), (300, 300), (300, 300))
    edge = align_products(ref, tgt, **KW)
    assert edge['offset_xy_px'] == [100, 0]
    assert 'search_bound_hit' in edge['reasons']


def test_correlation_has_no_wrap_against_brute_force():
    rng = np.random.default_rng(4)
    a, b = rng.random((50, 60)) < 0.2, rng.random((40, 70)) < 0.2
    r = 80
    size = (cnt_align._fft_size(50, 40, r), cnt_align._fft_size(60, 70, r))
    win = cnt_align._lags(cnt_align._spectrum(a, size), cnt_align._spectrum(b, size), size, r)
    assert size[0] >= 50 + r and size[1] >= 70 + r
    for dy, dx in [(0, 0), (-40, 55), (30, -60), (-79, 79), (79, -79), (12, 12), (-10, 20), (r, 0), (0, -r)]:
        expect = 0
        for y in range(50):
            for x in range(60):
                if a[y, x] and 0 <= y + dy < 40 and 0 <= x + dx < 70:
                    expect += int(b[y + dy, x + dx])
        assert win[dy + r, dx + r] == expect, (dy, dx)


def test_reversing_direction_negates_the_offset_and_keeps_physical_correspondence():
    world = random_world(41)
    dx, dy = 31, -18
    ref0 = (200, 200)
    ref, tgt = pair(world, ref0, tgt_origin(ref0, dx, dy), (400, 360), (380, 420))
    forward = align_products(ref, tgt, **KW)
    backward = align_products(tgt, ref, **KW)
    assert forward['offset_xy_px'] == [dx, dy]
    assert backward['offset_xy_px'] == [-dx, -dy]
    assert backward['offset_xy_um'] == [-forward['offset_xy_um'][0], -forward['offset_xy_um'][1]]
    assert forward['dice_best'] == backward['dice_best']
    # 물리 대응: ref 의 한 점이 가리키는 target 점은 양방향에서 같은 세계 좌표다
    p = np.array([120, 90])
    q = p + np.array([forward['offset_xy_px'][1], forward['offset_xy_px'][0]])
    back = q + np.array([backward['offset_xy_px'][1], backward['offset_xy_px'][0]])
    assert tuple(back) == tuple(p)


def test_failures_return_identity_with_reasons():
    ones = np.ones((60, 60), bool)
    some = np.zeros((60, 60), bool)
    some[10:14, 10:14] = True
    good = {'layers': {'M1': some}, 'valid': ones}
    kw = {**KW, 'search_um': 3.0}
    out = align_products({'layers': {}, 'valid': ones}, good, **kw)
    assert out['status'] == 'alignment_failed_identity_used' and out['reasons'] == ['cnt_missing_reference']
    assert out['offset_xy_px'] == [0, 0] and out['offset_xy_um'] == [0.0, 0.0]
    out = align_products(good, {'layers': {}, 'valid': ones}, **kw)
    assert out['reasons'] == ['cnt_missing_target']
    out = align_products({'layers': {'M1': np.zeros_like(some)}, 'valid': ones}, good, **kw)
    assert out['status'] == 'alignment_failed_identity_used'
    assert 'cnt_empty_reference_M1' in out['reasons'] and 'no_usable_layers' in out['reasons']
    out = align_products(good, {'layers': {'M9': some}, 'valid': ones}, **kw)
    assert {'cnt_layer_set_mismatch', 'no_usable_layers'} <= set(out['reasons'])
    assert out['status'] == 'alignment_failed_identity_used'
    # 일부 층만 빠지면 사용 층으로 계속한다
    both = {'layers': {'M1': some, 'M2': some}, 'valid': ones}
    part = align_products(both, {'layers': {'M1': some}, 'valid': ones}, **kw)
    assert part['layers_used'] == ['M1'] and 'cnt_layer_set_mismatch' in part['reasons']
    assert part['status'] == 'aligned_uncertain'
    # 겹침 없음
    top, bottom = np.zeros((100, 100), bool), np.zeros((100, 100), bool)
    top[:10] = True
    bottom[90:] = True
    pts_top, pts_bottom = np.zeros((100, 100), bool), np.zeros((100, 100), bool)
    pts_top[3, 3:60:7] = True
    pts_bottom[95, 3:60:7] = True
    out = align_products({'layers': {'M1': pts_top}, 'valid': top}, {'layers': {'M1': pts_bottom}, 'valid': bottom},
                         **{**KW, 'search_um': 2.0})
    assert out['status'] == 'alignment_failed_identity_used' and 'no_overlap' in out['reasons']
    assert out['offset_xy_px'] == [0, 0]


def test_admission_passes_at_the_limit_and_refuses_one_byte_below():
    world = random_world(51, shape=(200, 200))
    ref, tgt = pair(world, (0, 0), (0, 0), (150, 150), (150, 150))
    need = estimate_alignment_bytes((150, 150), (150, 150), 100, 3)
    ok = align_products(ref, tgt, **{**KW, 'max_operation_bytes': need})
    assert ok['estimated_bytes'] == need and ok['offset_xy_px'] == [0, 0]
    with pytest.raises(NumericContractError) as caught:
        align_products(ref, tgt, **{**KW, 'max_operation_bytes': need - 1})
    assert caught.value.code == 'oversized_real_operation'
    assert str(need) in str(caught.value) and str(need - 1) in str(caught.value)
    assert estimate_alignment_bytes((2400, 2400), (2400, 2400), 300, 3) > 1e9 * 0.3


def test_admission_includes_quadrant_temporaries():
    ref_shape, tgt_shape, r, layers = (150, 140), (130, 160), 100, 3
    ny, nx = cnt_align._fft_size(ref_shape[0], tgt_shape[0], r), cnt_align._fft_size(ref_shape[1], tgt_shape[1], r)
    real, cplx, window = ny * nx * 8, ny * (nx // 2 + 1) * 16, (2 * r + 1) ** 2
    boolean = (2 * (layers + 1) * (ref_shape[0] * ref_shape[1] + tgt_shape[0] * tgt_shape[1])
               + (layers + 2) * ref_shape[0] * ref_shape[1])
    expect = boolean + 2 * real + (2 * layers + 2) * cplx + 4 * cplx + (layers + 6) * window * 8
    assert estimate_alignment_bytes(ref_shape, tgt_shape, r, layers) == expect


def _tie_case():
    """참 offset (60, 0). M1 은 유일하게 식별하고, V 층은 -40 과 60 에서 정확히 같은 교집합을 낸다(주기 100 px 같은 동률)."""
    rng = np.random.default_rng(12)
    ref_m1 = rng.random((300, 300)) < 0.012
    ref_m1[150:, 150:] = False                       # 오른쪽 아래 분면에는 V 층만 있다
    tgt_m1 = np.zeros((300, 400), bool)
    tgt_m1[:, 60:360] = ref_m1
    ref_v, tgt_v = np.zeros((300, 300), bool), np.zeros((300, 400), bool)
    for y in range(160, 290, 7):
        ref_v[y, 200] = True
        tgt_v[y, 260] = True                         # 200 + 60
        tgt_v[y, 160] = True                         # 200 - 40
    return ({'layers': {'M1': ref_m1, 'V': ref_v}, 'valid': np.ones((300, 300), bool)},
            {'layers': {'M1': tgt_m1, 'V': tgt_v}, 'valid': np.ones((300, 400), bool)})


def test_periodic_ties_in_layer_and_quadrant_diagnostics_do_not_raise_false_reasons():
    ref, tgt = _tie_case()
    out = align_products(ref, tgt, **KW)
    assert out['offset_xy_px'] == [60, 0]
    assert out['status'] == 'aligned', out['reasons']
    assert out['per_layer']['V']['offset_xy_um'] == [6.0, 0.0]             # 동률이면 전역 best 를 고른다
    assert out['quadrant_offsets_um']['bottom_right'] == [6.0, 0.0]
    assert 'layer_offsets_disagree' not in out['reasons'] and 'residual_nonuniform' not in out['reasons']


def test_residual_nonuniform_positive_when_one_quadrant_moves_differently():
    rng = np.random.default_rng(21)
    ref = rng.random((300, 300)) < 0.012
    tgt = np.zeros((330, 360), bool)
    dx, dy = 10, 5
    for (y0, y1, x0, x1, extra) in [(0, 150, 0, 150, 0), (0, 150, 150, 300, 0), (150, 300, 0, 150, 0),
                                    (150, 300, 150, 300, 20)]:
        tgt[y0 + dy:y1 + dy, x0 + dx + extra:x1 + dx + extra] |= ref[y0:y1, x0:x1]
    out = align_products({'layers': {'M1': ref}, 'valid': np.ones((300, 300), bool)},
                         {'layers': {'M1': tgt}, 'valid': np.ones((330, 360), bool)}, **KW)
    assert out['offset_xy_px'] == [dx, dy]
    assert out['status'] == 'aligned_uncertain' and 'residual_nonuniform' in out['reasons']
    assert out['quadrant_offsets_um']['bottom_right'] == [round((dx + 20) * 0.1, 6), round(dy * 0.1, 6)]
    assert out['quadrant_offsets_um']['top_left'] == [round(dx * 0.1, 6), round(dy * 0.1, 6)]


def test_dice_below_min_positive():
    world = random_world(5)
    rng = np.random.default_rng(99)
    ref0 = (180, 190)
    ref, tgt = pair(world, ref0, tgt_origin(ref0, -22, 17), (400, 400), (400, 400))
    for layer in tgt['layers'].values():
        ys, xs = np.nonzero(layer)
        pick = rng.permutation(len(ys))[: int(0.4 * len(ys))]
        layer[ys[pick], xs[pick]] = False
        layer[rng.integers(0, 400, len(pick)), rng.integers(0, 400, len(pick))] = True
    out = align_products(ref, tgt, **{**KW, 'min_dice': 0.95})
    assert out['offset_xy_px'] == [-22, 17] and out['dice_best'] < 0.95
    assert 'dice_below_min' in out['reasons'] and out['status'] == 'aligned_uncertain'
    assert 'dice_below_min' not in align_products(ref, tgt, **{**KW, 'min_dice': 0.1})['reasons']


def test_dot_size_difference_gives_a_recorded_plateau():
    ref, tgt, kw, truth = plateau_case()
    out = align_products(ref, tgt, **kw)
    assert out['best_plateau_px'] == 9 and out['best_plateau_extent_px'] == [2, 2]
    assert out['reasons'] == ['peak_plateau'] and out['status'] == 'aligned_uncertain'
    assert max(abs(out['offset_xy_px'][0] - truth[0]), abs(out['offset_xy_px'][1] - truth[1])) <= 1
    exact = align_products(*identifiable_case()[:2], **identifiable_case()[2])
    assert exact['best_plateau_px'] == 1 and exact['best_plateau_extent_px'] == [0, 0]
    assert 'peak_plateau' not in exact['reasons']


def test_alternatives_only_list_peaks_with_nonzero_intersection():
    a, b = np.zeros((60, 60), bool), np.zeros((60, 60), bool)
    a[20, 20], b[25, 28] = True, True
    out = align_products({'layers': {'M1': a}, 'valid': np.ones((60, 60), bool)},
                         {'layers': {'M1': b}, 'valid': np.ones((60, 60), bool)}, **{**KW, 'search_um': 3.0})
    assert out['offset_xy_px'] == [8, 5]
    assert out['alternatives'] == [] and out['peak_ratio'] is None
    ref, tgt, kw, _ = ambiguous_case()
    assert all(alt['dice'] > 0 for alt in align_products(ref, tgt, **kw)['alternatives'])


def test_search_edge_peaks_are_marked():
    ref, tgt, kw, truth = ambiguous_case()
    out = align_products(ref, tgt, **{**kw, 'search_um': 6.0})       # 참 offset (6, 0) 이 탐색 원 위에 있다
    edge = [alt for alt in out['alternatives'] if alt['offset_xy_px'] == [6, 0]]
    assert edge and edge[0]['at_search_edge'] is True
    inner = align_products(ref, tgt, **kw)
    assert all(alt['at_search_edge'] is False for alt in inner['alternatives'] if alt['offset_xy_px'] == [6, 0])
    assert all('at_search_edge' in v for v in align_products(*identifiable_case()[:2], **identifiable_case()[2])
               ['pattern_period_vectors_um'])
    circle = cnt_align._grid(3)[2] <= 9
    edge_mask = cnt_align._edge_mask(circle)
    assert not edge_mask[3, 3] and edge_mask[3, 6] and edge_mask[0, 3] and not edge_mask[0, 0]


def _assert_identifiability(ref, tgt, kw, truth, expect_reasons, truth_is_best):
    first = align_products(ref, tgt, **kw)
    assert first == align_products(ref, tgt, **kw)                      # 결정적
    assert 'peak_ambiguous' in first['reasons'] and first['status'] == 'aligned_uncertain'
    for reason in expect_reasons:
        assert reason in first['reasons'], first['reasons']
    listed = [first['offset_xy_px']] + [alt['offset_xy_px'] for alt in first['alternatives']]
    assert list(truth) in listed
    assert (first['offset_xy_px'] == list(truth)) == truth_is_best
    json.dumps(first, sort_keys=True, allow_nan=False)
    return first


def test_identifiability_non_integer_period_crop():
    # 점 간격 33.5 px 를 정수로 반올림한 열 → 67 px 주기. 세계는 x = 0 에서 시작하므로 target crop 의 왼쪽 30 px 는 비어 있다.
    rng = np.random.default_rng(8)
    rows, width, dx, dy = 260, 700, 50, 3
    bits = rng.random((rows + 80, 2)) < 0.5
    world = np.zeros((rows + 80, width + 200), bool)
    for k in range(27):
        world[:, int(np.round(33.5 * k))] = bits[:, k % 2]
    ref = world[10:10 + rows, 20:20 + width]
    padded = np.pad(world, ((20, 0), (80, 0)))
    tgt = padded[10 - dy + 20:10 - dy + 20 + rows, 20 - dx + 80:20 - dx + 80 + width]
    out = _assert_identifiability({'layers': {'M1': ref}, 'valid': np.ones(ref.shape, bool)},
                                  {'layers': {'M1': tgt}, 'valid': np.ones(tgt.shape, bool)}, KW, (dx, dy), [], True)
    assert out['peak_ratio'] < 1.2
    assert [-84, 3] in [alt['offset_xy_px'] for alt in out['alternatives']]


def test_identifiability_valid_hole_hides_the_distinguishing_dots():
    ref, tgt = np.zeros((100, 150), bool), np.zeros((100, 150), bool)
    for y in range(10, 95, 8):
        ref[y, 50] = ref[y, 80] = True
        tgt[y, 70] = tgt[y, 100] = True
    tgt_valid = np.ones((100, 150), bool)
    tgt_valid[:, 95:] = False                                           # target 의 두 번째 점은 valid 구멍 안
    kw = dict(pitch_um=1.0, search_um=40.0, min_dice=0.0, layer_tolerance_um=5.0, max_operation_bytes=BIG)
    out = _assert_identifiability({'layers': {'M1': ref}, 'valid': np.ones((100, 150), bool)},
                                  {'layers': {'M1': tgt}, 'valid': tgt_valid}, kw, (20, 0), [], False)
    assert out['offset_xy_px'] == [-10, 0] and out['peak_ratio'] == 1.0   # 동점이면 |d| 최소
    # 주기 때문에 멀리 떨어진 동률은 plateau 가 아니다: best 를 포함하는 연결 성분만 본다(T5 후속 adv:9)
    assert 'peak_plateau' not in out['reasons'] and out['best_plateau_extent_px'] == [0, 0] and out['best_plateau_px'] == 1


def test_identifiability_period_shorter_than_search_radius():
    rng = np.random.default_rng(9)
    bits = rng.random((300, 10)) < 0.3
    ref = np.tile(bits, (1, 30))                                        # x 주기 10 px, 탐색 반경 40 px
    tgt = np.roll(ref, 13, axis=1)                                      # 참 offset (13, 0)
    kw = dict(pitch_um=1.0, search_um=40.0, min_dice=0.0, layer_tolerance_um=5.0, max_operation_bytes=BIG)
    out = _assert_identifiability({'layers': {'M1': ref}, 'valid': np.ones(ref.shape, bool)},
                                  {'layers': {'M1': tgt}, 'valid': np.ones(tgt.shape, bool)}, kw, (13, 0), [], False)
    assert out['offset_xy_px'] == [3, 0]                                # 겹침이 가장 큰 주기 동치 offset


@pytest.mark.parametrize('override', [
    {'search_um': 0.1}, {'search_um': 0.0}, {'pitch_um': 0.0}, {'pitch_um': -0.1}, {'search_um': -1.0}])
def test_invalid_alignment_input_is_an_explicit_error(override):
    ref, tgt = pair(random_world(3), (0, 0), (0, 0), (60, 60), (60, 60))
    with pytest.raises(NumericContractError) as caught:
        align_products(ref, tgt, **{**KW, **override})
    assert caught.value.code == 'invalid_alignment_input'


def test_layer_and_valid_shape_mismatch_raise():
    ref, tgt = pair(random_world(3), (0, 0), (0, 0), (60, 60), (60, 60))
    bad = {'layers': {**tgt['layers'], 'M1': tgt['layers']['M1'][:50]}, 'valid': tgt['valid']}
    with pytest.raises(NumericContractError) as caught:
        align_products(ref, bad, **KW)
    assert caught.value.code == 'invalid_alignment_input' and 'M1' in str(caught.value)
    with pytest.raises(NumericContractError) as caught:
        align_products(ref, {'layers': tgt['layers'], 'valid': np.ones((60,), bool)}, **KW)
    assert caught.value.code == 'invalid_alignment_input'
    edge = align_products(ref, tgt, **{**KW, 'search_um': 0.2})          # r = 2 px 는 허용
    assert edge['search_px'] == 2


def _png(path, shape, pixels, extra=()):
    full = np.zeros(shape, dtype=bool)
    for y, x in tuple(pixels) + tuple(extra):
        full[y, x] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.where(full, 0, 255).astype(np.uint8)).save(path)


def test_load_cnt_layers_crops_each_layer_without_cross_layer_union(tmp_path):
    frame = SimpleNamespace(shape_yx=(40, 50), pixel_pitch_um=0.1, origin_xy_um=(50.0, 50.0), frame_id='f1')
    full = (40 + 1000, 50 + 1000)
    cno = tmp_path / 'PRODUCT' / 'cno'
    _png(cno / '1_M1.png', full, [(503, 504)])
    _png(cno / '2_V.png', full, [(510, 520)])
    _png(cno / '3_CNT.png', full, [(530, 540), (10, 10)])      # (10,10) 은 crop 밖
    _png(cno / '4_M1.png', full, [(505, 506)])                 # 같은 layer 이름 중복
    out = load_cnt_layers(tmp_path, 'PRODUCT', frame)
    assert sorted(out['layers']) == ['CNT', 'M1', 'V']
    assert all(arr.shape == (40, 50) and arr.dtype == bool for arr in out['layers'].values())
    assert {tuple(p) for p in np.argwhere(out['layers']['M1'])} == {(3, 4), (5, 6)}
    assert {tuple(p) for p in np.argwhere(out['layers']['V'])} == {(10, 20)}
    assert {tuple(p) for p in np.argwhere(out['layers']['CNT'])} == {(30, 40)}
    assert [w['status'] for w in out['warnings']] == ['duplicate_cnt_layer_name']
    assert out['warnings'][0]['layer'] == 'M1'
    assert len(out['files']) == 4 and all(len(f['sha256']) == 64 for f in out['files'])
    (tmp_path / 'EMPTY' / 'cno').mkdir(parents=True)
    empty = load_cnt_layers(tmp_path, 'EMPTY', frame)
    assert empty['layers'] == {} and empty['warnings'][0]['status'] == 'cnt_not_provided'
    missing = load_cnt_layers(tmp_path, 'NOPE', frame)
    assert missing['layers'] == {} and missing['warnings'][0]['status'] == 'product_folder_missing'


def test_missing_layers_give_a_result_before_valid_shapes_are_checked():
    """change-roi/T6 (T5 adv:8): the T4 behaviour — an input without CNT layers is a result, whatever its valid looks like."""
    odd_valid = np.ones((5,), bool)                                     # 2-D 가 아니어도 층이 없으면 결과다
    other = {'layers': {'M1': np.ones((9, 9), bool)}, 'valid': np.ones((9, 9), bool)}
    for ref, tgt, reason in (({'layers': {}, 'valid': odd_valid}, other, 'cnt_missing_reference'),
                             (other, {'valid': odd_valid}, 'cnt_missing_target')):
        out = align_products(ref, tgt, **KW)
        assert out['status'] == 'alignment_failed_identity_used' and reason in out['reasons']
        assert out['offset_xy_px'] == [0, 0]
    both = align_products({'layers': {}, 'valid': odd_valid}, {'layers': {}, 'valid': odd_valid}, **KW)
    assert both['reasons'] == ['cnt_missing_reference', 'cnt_missing_target']
    with pytest.raises(NumericContractError):                           # 층이 있으면 모양 검사는 그대로
        align_products({'layers': {'M1': np.ones((9, 9), bool)}, 'valid': odd_valid}, other, **KW)


def test_plateau_is_judged_on_the_component_that_contains_the_best_offset():
    """change-roi/T6 (T5 adv:9): periodic ties far from the best are not a plateau; a real flat top still is."""
    ref, tgt, kw, truth = plateau_case()
    flat = align_products(ref, tgt, **kw)
    assert max(abs(flat['offset_xy_px'][0] - truth[0]), abs(flat['offset_xy_px'][1] - truth[1])) <= 1   # 평탄한 꼭대기 안
    assert 'peak_plateau' in flat['reasons'] and max(flat['best_plateau_extent_px']) >= cnt_align.PLATEAU_MIN_EXTENT_PX
    assert flat['best_plateau_px'] > 1
    row = np.zeros((40, 120), bool)                                     # x 주기 10 인 점들 → 같은 값이 멀리서 반복된다
    row[::7, 5::10] = True
    shifted = np.roll(row, 20, axis=1)
    periodic = align_products({'layers': {'M1': row}, 'valid': np.ones(row.shape, bool)},
                              {'layers': {'M1': shifted}, 'valid': np.ones(row.shape, bool)},
                              pitch_um=1.0, search_um=30.0, min_dice=0.0, layer_tolerance_um=5.0,
                              max_operation_bytes=10 ** 11)
    assert 'peak_plateau' not in periodic['reasons']
    assert periodic['best_plateau_extent_px'] == [0, 0] and periodic['best_plateau_px'] == 1


@pytest.mark.skipif(not os.environ.get('SIMCOMPARE_ETX_PY'), reason='SIMCOMPARE_ETX_PY not set')
@pytest.mark.parametrize('name', ['identifiable_case', 'ambiguous_case', 'plateau_case'])
def test_python39_result_is_identical(name):
    py = os.path.expanduser(os.environ['SIMCOMPARE_ETX_PY'])
    code = (CASE_SRC + '\nimport json, sys\nsys.path.insert(0, %r)\n' % SRC +
            'from simcompare.cnt_align import align_products\n'
            'ref, tgt, kw, _ = %s()\n' % name +
            'print(json.dumps(align_products(ref, tgt, **kw), sort_keys=True))\n')
    done = subprocess.run([py, '-c', code], capture_output=True, text=True, check=True, timeout=600)
    ref, tgt, kw, _ = {'identifiable_case': identifiable_case, 'ambiguous_case': ambiguous_case,
                       'plateau_case': plateau_case}[name]()
    here = json.dumps(align_products(ref, tgt, **kw), sort_keys=True)
    assert done.stdout.strip() == here
    assert math.isfinite(json.loads(here)['dice_best'])
