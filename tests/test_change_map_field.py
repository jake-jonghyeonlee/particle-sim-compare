"""layer-align/T3: change_maps 의 offset 장, case_contributions 의 창별 offset, suppress_overlap 0.
좌표: reference 픽셀 p ↔ target 픽셀 p + (dx, dy). 영역은 (y0, y1, x0, x1, dx, dy), 끝 미포함."""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from simcompare import change_candidates
from simcompare.change_map import (MaskPair, case_contributions, change_maps, suppress_overlap, window_candidates)
from simcompare.numeric_schema import NumericContractError

BIG = 10 ** 12
PITCH = 0.1
WINDOW_UM = 5.0
W = 50


def rect(shape, y, x, h, w):
    m = np.zeros(shape, bool)
    m[y:y + h, x:x + w] = True
    return m


def grid_field(shape, by, bx, offsets):
    """2×2 field. offsets = {(iy, ix): (dx, dy)}."""
    h, w = shape
    ys, xs = {0: (0, by), 1: (by, h)}, {0: (0, bx), 1: (bx, w)}
    return [(ys[iy][0], ys[iy][1], xs[ix][0], xs[ix][1], offsets[(iy, ix)][0], offsets[(iy, ix)][1])
            for iy in (0, 1) for ix in (0, 1)]


def maps_kw(**kw):
    base = dict(pitch_um=PITCH, tolerance_um=0.2, max_operation_bytes=BIG, window_um=WINDOW_UM)
    base.update(kw)
    return base


def window(layer, y, x):
    return {'layer': layer, 'window_origin_px': [y, x], 'window_px': W,
            'center_xy_um': [round((x + W / 2.0) * PITCH, 6), round((y + W / 2.0) * PITCH, 6)]}


def expected_shift(tgt, field):
    out = np.zeros_like(tgt)
    for y0, y1, x0, x1, dx, dy in field:
        for y in range(y0, y1):
            for x in range(x0, x1):
                if 0 <= y + dy < tgt.shape[0] and 0 <= x + dx < tgt.shape[1]:
                    out[y, x] = tgt[y + dy, x + dx]
    return out


# --------------------------------------------------------------------------- (a)

def test_uniform_field_equals_scalar_offset():
    shape = (60, 90)
    rng = np.random.default_rng(1)
    pairs = [MaskPair('c%d' % i, 'M1' if i % 2 else 'M2', rng.random(shape) < 0.1, rng.random(shape) < 0.1)
             for i in range(5)]
    ref_valid = np.ones(shape, bool)
    ref_valid[10:14, 20:30] = False
    tgt_valid = np.ones(shape, bool)
    for g in ((2, -1), (-3, 2), (0, 0)):
        field = grid_field(shape, 30, 45, {k: g for k in ((0, 0), (0, 1), (1, 0), (1, 1))})
        scalar = change_maps(pairs, ref_valid=ref_valid, tgt_valid=tgt_valid, offset_px=g, **maps_kw())
        fielded = change_maps(pairs, ref_valid=ref_valid, tgt_valid=tgt_valid, offset_field=field, **maps_kw())
        assert np.array_equal(scalar['D'], fielded['D'])
        for name in ('maps', 'union_change', 'union_ref', 'union_tgt'):
            assert scalar[name].keys() == fielded[name].keys()
            for layer in scalar[name]:
                assert np.array_equal(scalar[name][layer], fielded[name][layer]), (g, name, layer)
        assert scalar['domain_bbox_px'] == fielded['domain_bbox_px']
        extra = set(fielded['stats']) - set(scalar['stats'])
        assert extra == {'offset_field', 'seam_excluded_pixels'}
        assert fielded['stats']['seam_excluded_pixels'] == 0
        same = {k: v for k, v in fielded['stats'].items() if k not in extra}
        assert same.pop('estimated_bytes') > scalar['stats']['estimated_bytes']      # layer-align/T4a adv:3: field 임시 배열 4*hr
        assert same == {k: v for k, v in scalar['stats'].items() if k != 'estimated_bytes'}
    assert 'offset_field' not in scalar['stats'] and 'seam_excluded_pixels' not in scalar['stats']


# --------------------------------------------------------------------------- (b)

def test_regions_are_shifted_by_their_own_offset_and_seam_is_left_out_of_the_domain():
    shape = (40, 60)
    rng = np.random.default_rng(2)
    a, b = rng.random(shape) < 0.3, rng.random(shape) < 0.3
    offsets = {(0, 0): (0, 0), (0, 1): (3, 0), (1, 0): (0, 2), (1, 1): (-2, 1)}
    by, bx = 20, 30
    field = grid_field(shape, by, bx, offsets)
    out = change_maps([MaskPair('c', 'M1', a, b)], ref_valid=np.ones(shape, bool), tgt_valid=np.ones(shape, bool),
                      offset_field=field, **maps_kw(tolerance_um=0.0))

    def cheb(p, q):
        return max(abs(p[0] - q[0]), abs(p[1] - q[1]))

    seam = np.zeros(shape, bool)
    for iy, (ya, yb) in enumerate(((0, by), (by, 40))):
        width = cheb(offsets[(iy, 0)], offsets[(iy, 1)])
        seam[ya:yb, bx - width:bx + width] = True
    for ix, (xa, xb) in enumerate(((0, bx), (bx, 60))):
        width = cheb(offsets[(0, ix)], offsets[(1, ix)])
        seam[by - width:by + width, xa:xb] = True
    assert seam.any()
    shifted = expected_shift(b, field)
    shifted_valid = expected_shift(np.ones(shape, bool), field)
    domain = shifted_valid & ~seam
    assert np.array_equal(out['D'], domain)
    assert out['stats']['seam_excluded_pixels'] == int(np.count_nonzero(shifted_valid & seam))
    # 세로 띠 폭: 위 행 3, 아래 행 2
    assert not out['D'][:by, bx - 3:bx + 3].any()
    assert not out['D'][by:, bx - 2:bx + 2].any()
    assert np.array_equal(out['maps']['M1'], ((a ^ shifted) & domain).astype(np.uint32))
    assert out['stats']['offset_px'] is None
    assert out['stats']['offset_field'] == [list(f) for f in sorted(field)]


# --------------------------------------------------------------------------- (h)

@pytest.mark.parametrize('bad', [
    [(0, 20, 0, 60, 0, 0)],                                                     # 빈틈
    [(0, 40, 0, 60, 0, 0), (0, 10, 0, 10, 1, 1)],                               # 겹침
    [(0, 40, 0, 30, 0, 0), (0, 40, 30, 70, 0, 0)],                              # frame 밖
    [],                                                                         # 빈 목록
    [(0, 40, 0, 60, 0.5, 0)],                                                   # 정수 아님
])
def test_invalid_offset_field(bad):
    shape = (40, 60)
    with pytest.raises(NumericContractError) as err:
        change_maps([], ref_valid=np.ones(shape, bool), tgt_valid=np.ones(shape, bool), offset_field=bad, **maps_kw())
    assert err.value.code == 'invalid_offset_field'


def test_exactly_one_of_offset_px_and_offset_field():
    shape = (40, 60)
    ok = [(0, 40, 0, 60, 0, 0)]
    for kw in ({}, {'offset_px': (0, 0), 'offset_field': ok}):
        with pytest.raises(NumericContractError):
            change_maps([], ref_valid=np.ones(shape, bool), tgt_valid=np.ones(shape, bool), **maps_kw(), **kw)
    # 한 영역짜리 field 도 받는다
    out = change_maps([], ref_valid=np.ones(shape, bool), tgt_valid=np.ones(shape, bool), offset_field=ok, **maps_kw())
    assert out['stats']['offset_px'] == [0, 0]


# --------------------------------------------------------------------------- (c)

def test_window_offsets_scalar_matches_window_candidates_and_omission_is_unchanged():
    shape = (120, 160)
    rng = np.random.default_rng(3)
    pairs = [MaskPair('base%d' % i, 'M1' if i % 2 else 'M2', (m := rng.random(shape) < 0.02), m.copy())
             for i in range(4)]
    pairs.append(MaskPair('shift', 'M1', rect(shape, 40, 40, 20, 10), rect(shape, 40, 46, 20, 10)))
    pairs.append(MaskPair('appear', 'M2', np.zeros(shape, bool), rect(shape, 60, 100, 15, 15)))
    kw = dict(ref_valid=np.ones(shape, bool), tgt_valid=np.ones(shape, bool), pitch_um=PITCH, tolerance_um=0.2)
    maps = change_maps(pairs, offset_px=(0, 0), max_operation_bytes=BIG, window_um=WINDOW_UM, **kw)
    found = window_candidates(maps['maps'], maps['D'], **{k: maps[k] for k in ('union_change', 'union_ref', 'union_tgt')},
                              pitch_um=PITCH, window_um=WINDOW_UM, max_operation_bytes=BIG)['candidates']
    assert found
    plain = case_contributions(pairs, found, offset_px=(0, 0), max_operation_bytes=BIG, **kw)
    assert all(set(w) == {'window_index', 'layer', 'center_xy_um', 'contributing_cases'} for w in plain['windows'])
    fresh = case_contributions(pairs, found, offset_px=(0, 0), max_operation_bytes=BIG, window_offsets=None, **kw)
    assert plain == fresh
    new = case_contributions(pairs, found, offset_px=(0, 0), max_operation_bytes=BIG,
                             window_offsets=[(0, 0)] * len(found), **kw)
    area = PITCH * PITCH
    for cand, got in zip(found, new['windows']):
        assert got['union_change_cells'] == round(cand['score_um2'] / area)
        assert got['sum_residual_cells'] == cand['sum_residual_cells']
        assert got['union_ref_cells'] == round(cand['union_area_reference_um2'] / area)
        assert got['union_tgt_cells'] == round(cand['union_area_target_um2'] / area)
        assert got['observed_fraction'] == cand['observed_fraction']
    for old, got in zip(plain['windows'], new['windows']):
        assert [c['case_key'] for c in old['contributing_cases']] == [c['case_key'] for c in got['contributing_cases']]


# --------------------------------------------------------------------------- (d)

def two_piece_world():
    """ref 40×120, 경계 x=60. 왼쪽 조각 offset (0, 0), 오른쪽 조각 offset (3, 0). 각 조각은 그 offset 에서 정확히 맞는다."""
    ref_shape, tgt_shape = (80, 120), (80, 130)
    a = rect(ref_shape, 20, 50, 20, 20)                     # 경계 x=60 을 가로지른다
    a |= rect(ref_shape, 55, 20, 8, 8)
    a |= rect(ref_shape, 55, 90, 8, 8)
    b = np.zeros(tgt_shape, bool)
    b[:, :60] = a[:, :60]                                    # 왼쪽: offset 0
    b[:, 63:123] = a[:, 60:]                                 # 오른쪽: offset +3
    field = grid_field(ref_shape, 40, 60, {(0, 0): (0, 0), (0, 1): (3, 0), (1, 0): (0, 0), (1, 1): (3, 0)})
    return ref_shape, tgt_shape, a, b, field


def test_boundary_window_with_field_has_no_residual_and_no_contributing_case():
    ref_shape, tgt_shape, a, b, field = two_piece_world()
    # 이음새 안(target 열 57..59)에만 있는 접합 흔적: 왼쪽 조각이 읽는 열이고 reference 에는 없다.
    b[30:35, 57:60] = True
    pairs = [MaskPair('c', 'M1', a, b)]
    kw = dict(ref_valid=np.ones(ref_shape, bool), tgt_valid=np.ones(tgt_shape, bool), pitch_um=PITCH, tolerance_um=0.2)
    win = window('M1', 10, 35)    # x 35..85: 경계 60 을 넘는다
    out = case_contributions(pairs, [win], offset_px=(0, 0), max_operation_bytes=BIG, window_offsets=[field], **kw)
    got = out['windows'][0]
    assert got['contributing_cases'] == []
    assert got['union_change_cells'] == 0 and got['sum_residual_cells'] == 0
    # 반례: 모두 0 인 scalar 로 계산하면 같은 창에 잔차가 생긴다.
    zero = case_contributions(pairs, [win], offset_px=(0, 0), max_operation_bytes=BIG, window_offsets=[(0, 0)], **kw)
    assert zero['windows'][0]['contributing_cases']
    # change_maps 도 같은 결과: 이음새 안의 흔적은 지도에 안 나온다.
    maps = change_maps(pairs, offset_field=field, **kw, **{k: v for k, v in maps_kw().items() if k not in ('pitch_um', 'tolerance_um')})
    assert int(maps['maps']['M1'].sum()) == 0
    assert maps['stats']['seam_excluded_pixels'] > 0


# --------------------------------------------------------------------------- (e)

def test_non_member_layer_with_local_offset_residual_shows_up_in_contributions():
    shape = (100, 100)
    m1 = MaskPair('m1', 'M1', rect(shape, 30, 20, 15, 10), rect(shape, 30, 22, 15, 10))     # offset (2, 0) 에서 맞음
    m2 = MaskPair('m2', 'M2', rect(shape, 30, 20, 15, 10), rect(shape, 30, 20, 15, 10))     # offset (0, 0) 에서 맞음
    kw = dict(ref_valid=np.ones(shape, bool), tgt_valid=np.ones(shape, bool), pitch_um=PITCH, tolerance_um=0.0)
    win = window('M1', 10, 5)
    base = case_contributions([m1, m2], [win], offset_px=(0, 0), max_operation_bytes=BIG, **kw)
    assert [c['case_key'] for c in base['windows'][0]['contributing_cases']] == ['m1']
    local = case_contributions([m1, m2], [win], offset_px=(0, 0), max_operation_bytes=BIG,
                               window_offsets=[(2, 0)], **kw)
    cases = local['windows'][0]['contributing_cases']
    assert [(c['case_key'], c['layer']) for c in cases] == [('m2', 'M2')]
    assert cases[0]['residual_um2'] > 0
    # 합집합·sum_residual 은 창의 층(M1) 쌍으로만: M1 은 맞았으니 0
    assert local['windows'][0]['union_change_cells'] == 0
    # 잔차 0 인 case 도 합집합에 누적: M1 의 A 는 창 안에 있다
    assert local['windows'][0]['union_ref_cells'] == 15 * 10


# --------------------------------------------------------------------------- (f)

def test_observed_fraction_follows_the_actual_domain():
    shape = (80, 120)
    ref_valid = np.ones(shape, bool)
    ref_valid[20:30, 70:80] = False                      # 창 안 구멍 10×10
    tgt_valid = np.ones(shape, bool)
    pair = MaskPair('c', 'M1', np.zeros(shape, bool), np.zeros(shape, bool))
    win = window('M1', 10, 70)                           # x 70..120: 오른쪽 가장자리까지
    kw = dict(ref_valid=ref_valid, tgt_valid=tgt_valid, pitch_um=PITCH, tolerance_um=0.0)
    got = case_contributions([pair], [win], offset_px=(0, 0), max_operation_bytes=BIG,
                             window_offsets=[(3, 0)], **kw)['windows'][0]
    # offset dx=3: 오른쪽 3 열은 target 밖. 세로 y 10..60 · 가로 x 70..117 에서 구멍 100 칸을 뺀다.
    valid = ref_valid & expected_shift(tgt_valid, [(0, 80, 0, 120, 3, 0)])
    expected = int(np.count_nonzero(valid[10:60, 70:120])) / float(W * W)
    assert got['observed_fraction'] == expected == (50 * 47 - 100) / 2500.0
    assert got['flags'] == ['window_partially_unobserved']
    full = case_contributions([pair], [window('M1', 10, 5)], offset_px=(0, 0), max_operation_bytes=BIG,
                              window_offsets=[(0, 0)], **kw)['windows'][0]
    assert full['observed_fraction'] == 1.0 and full['flags'] == []


def test_window_offsets_length_and_field_errors():
    shape = (80, 120)
    kw = dict(ref_valid=np.ones(shape, bool), tgt_valid=np.ones(shape, bool), pitch_um=PITCH, tolerance_um=0.0)
    with pytest.raises(NumericContractError):
        case_contributions([], [window('M1', 0, 0)], offset_px=(0, 0), max_operation_bytes=BIG, window_offsets=[], **kw)
    with pytest.raises(NumericContractError) as err:
        case_contributions([], [window('M1', 0, 0)], offset_px=(0, 0), max_operation_bytes=BIG,
                           window_offsets=[[(0, 40, 0, 120, 0, 0)]], **kw)
    assert err.value.code == 'invalid_offset_field'


# --------------------------------------------------------------------------- (g)

def item(x, y):
    return {'representative': SimpleNamespace(center_xy_um=(x, y)), 'id': (x, y)}


def kept_ids(items, threshold):
    return [i['id'] for i in suppress_overlap(items, WINDOW_UM, threshold)]


def test_suppress_overlap_zero_means_any_overlap():
    items = [item(0.0, 0.0), item(4.9, 0.0), item(5.0, 0.0), item(10.0, 4.9), item(30.0, 30.0)]
    # 4.9 는 0.1 µm(1 px) 겹침 → 억제. 5.0 은 맞닿기만 → 유지. (10, 4.9) 는 (5, 0) 과 y 로 0.1 겹치지만 x 로 닿기만.
    assert kept_ids(items, 0) == [(0.0, 0.0), (5.0, 0.0), (10.0, 4.9), (30.0, 30.0)]
    assert kept_ids([item(0.0, 0.0), item(5.0, 4.9)], 0) == [(0.0, 0.0), (5.0, 4.9)]
    assert kept_ids([item(0.0, 0.0), item(4.9, 4.9)], 0) == [(0.0, 0.0)]


def test_suppress_overlap_positive_threshold_is_unchanged():
    # 겹침 비율: 4.9 → 0.02, 3.0 → 0.4
    assert kept_ids([item(0.0, 0.0), item(4.9, 0.0)], 0.3) == [(0.0, 0.0), (4.9, 0.0)]
    assert kept_ids([item(0.0, 0.0), item(3.0, 0.0)], 0.3) == [(0.0, 0.0)]
    assert kept_ids([item(0.0, 0.0), item(3.0, 0.0)], 0.4) == [(0.0, 0.0)]       # 경계값 포함
    assert kept_ids([item(0.0, 0.0), item(5.0, 0.0)], 1.0) == [(0.0, 0.0), (5.0, 0.0)]


def test_validate_args_accepts_zero_suppress_overlap(tmp_path):
    from simcompare import template_export

    def check(value):
        change_candidates._validate_args(tmp_path, 1.0, 0.5, 0.1, 0.1, 5.0, 0.3, 5, template_export.RULE_VERSION,
                                         suppress_overlap=value)

    check(0)
    check(0.0)
    check(1)
    check(0.3)
    for bad in (-0.1, 1.1, float('nan'), True):
        with pytest.raises(Exception):
            check(bad)
    assert change_candidates.SUPPRESS_POLICY == 'greedy_score_order_window_overlap_ge_x_all_layers'
    assert change_candidates.SUPPRESS_POLICY_ANY == 'greedy_score_order_any_window_overlap_all_layers'
