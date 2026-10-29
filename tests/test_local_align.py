"""roi-detail/T2: ``local_align`` 순수 함수(고정 Ω 점수, 상태 사다리, 선형 적합, 반경 검증)."""
import math

import numpy as np
import pytest

from simcompare import local_align as la

PITCH = 0.1
H, W = 100, 100        # reference 상자
R = 5
WIN = (20, 80, 20, 80)  # 상자 좌표 창
CANVAS = 240           # 세계 한 변. 상자는 (70, 70) 에서 시작해 어떤 shift 에서도 세계 안에 남는다
ORIGIN = 70


def world(seed, n=220, size=(2, 9)):
    rng = np.random.default_rng(seed)
    w = np.zeros((CANVAS, CANVAS), dtype=bool)
    for _ in range(n):
        y, x = int(rng.integers(ORIGIN - 15, ORIGIN + H + 15)), int(rng.integers(ORIGIN - 15, ORIGIN + W + 15))
        w[y:y + int(rng.integers(*size)), x:x + int(rng.integers(*size))] = True
    return w


def ref_of(w):
    return w[ORIGIN:ORIGIN + H, ORIGIN:ORIGIN + W].copy()


def wide_of(w, dd, r=R):
    """wide[r+ddy+j, r+ddx+i] = ref[j, i] 가 되게 세계에서 자른 (H+2r, W+2r) target crop."""
    ddy, ddx = dd
    return w[ORIGIN - r - ddy:ORIGIN + H + r - ddy, ORIGIN - r - ddx:ORIGIN + W + r - ddx].copy()


def valid_pair(r=R):
    return np.ones((H, W), bool), np.ones((H + 2 * r, W + 2 * r), bool)


def diagnose(ref, wide, *, r=R, tol=0.2, min_dice=0.3, window=WIN, valid=None, **kw):
    rv, tv = valid or valid_pair(r)
    return la.diagnose_roi(ref, wide, rv, tv, window, r, pitch_um=PITCH, tolerance_um=tol, min_dice=min_dice, **kw)


# --- 반경 -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('value,expected', [(1, 10), (0.1, 1), (3.0, 30), (0.26, 3)])
def test_radius_is_rounded_to_pixels(value, expected):
    assert la.search_radius_px(value, PITCH) == expected


@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf'), 0.04, 3.2, True, '1'])
def test_bad_radius_is_rejected_with_the_value(value):
    with pytest.raises(la.RadiusError) as info:
        la.search_radius_px(value, PITCH)
    assert str(value) in str(info.value) or repr(value) in str(info.value)


# --- 알려진 shift 복원 --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('dd', [(0, 0), (2, -3), (-4, 1), (3, 3), (-1, -2)])
@pytest.mark.parametrize('kinds', [('electrode:E',), ('cnt:C',), ('electrode:E', 'cnt:C')])
def test_known_shift_is_recovered_for_electrode_cnt_and_both(dd, kinds):
    worlds = {k: world(i + 1) for i, k in enumerate(kinds)}
    got = diagnose({k: ref_of(w) for k, w in worlds.items()}, {k: wide_of(w, dd) for k, w in worlds.items()})
    assert got['status'] == 'ok'
    assert got['d_yx'] == dd
    assert got['dice_local'] == 1.0
    assert got['ring_d_yx'] == dd
    assert set(got['per_layer']) == set(kinds)
    assert all(v['dice_local'] == 1.0 for v in got['per_layer'].values())
    assert (got['dice_global'] == 1.0) == (dd == (0, 0))


def test_ties_prefer_d_zero():
    # 같은 영상이 어디서나 같게 맞는 가짜가 아니라, 진짜로 d=0 과 d=(0,1) 이 같은 점수일 때 |d| 작은 쪽
    inter = np.zeros((11, 11), dtype=np.int64)
    sum_b = np.full((11, 11), 50, dtype=np.int64)
    inter[5, 5] = inter[5, 6] = inter[6, 5] = 30
    pick = la.choose(50, inter, sum_b, 5, min_support=1, min_dice=0.0)
    assert pick['d'] == (0, 0)


def test_known_shift_with_a_nonzero_dice_below_one():
    w = world(7)
    noisy = w.copy()
    noisy[ORIGIN + 10:ORIGIN + 20, ORIGIN + 10:ORIGIN + 90] ^= True        # 일부가 다른 영상
    got = diagnose({'cnt:C': ref_of(w)}, {'cnt:C': wide_of(noisy, (1, -2))})
    assert got['d_yx'] == (1, -2) and 0.5 < got['dice_local'] < 1.0


# --- Ω 와 가짜 최적 ---------------------------------------------------------------------------------------------------

def test_omega_is_the_square_erosion_of_target_valid_inside_reference_valid():
    rv, tv = valid_pair()
    tv[:, :3] = False                       # target 의 왼쪽 3 열이 관측 불가
    tv[40:42, 50:52] = False                # 구멍
    omega = la.valid_domain(rv, tv, R)
    assert omega.shape == (H, W)
    # reference (y, x) 는 wide 의 [y, y+2r] x [x, x+2r] 가 모두 유효할 때만 Ω 에 든다
    assert not omega[:, 0:3].any() and omega[0, 3]
    assert not omega[40 - 2 * R:42, 50 - 2 * R:52].any()
    assert omega[40 - 2 * R - 1, 60] and omega[60, 60]


def test_omega_is_empty_when_target_valid_is_empty():
    rv, tv = valid_pair()
    got = diagnose({'cnt:C': ref_of(world(1))}, {'cnt:C': wide_of(world(1), (0, 0))}, valid=(rv, np.zeros_like(tv)))
    assert got['status'] == 'skipped' and got['reason'] == 'no_valid_region'


def test_astra_1d_counterexample_does_not_pick_a_fake_interior_optimum():
    # reference [1,1,1,1,1,0] 이고 target 은 전부 1. d 마다 Ω 를 따로 잡으면 가장자리 d 가 거짓으로 좋아 보인다.
    # 고정 Ω 에서는 모든 d 의 Dice 가 같다(10/11) → 평탄 → ambiguous.
    r = 2
    ref = np.zeros((1, 6), bool)
    ref[0, :5] = True
    wide = np.ones((1 + 2 * r, 6 + 2 * r), bool)
    got = la.diagnose_roi({'cnt:C': ref}, {'cnt:C': wide}, np.ones((1, 6), bool), np.ones_like(wide), (0, 1, 0, 6), r,
                          pitch_um=PITCH, tolerance_um=0.2, min_dice=0.3, min_support=1, min_ring_support=1)
    assert got['status'] == 'ambiguous'
    assert got['dice_local'] == round(10 / 11, 6) == got['dice_global']


def test_frame_edge_does_not_create_a_fake_optimum():
    # target 가 왼쪽 가장자리에서 잘려(valid False) 있어도 true shift 가 선택된다: Ω 는 모든 d 에서 같다
    w = world(3)
    dd = (2, 1)
    rv, tv = valid_pair()
    tv[:, :4] = False
    wide = wide_of(w, dd)
    wide[:, :4] = False
    got = diagnose({'cnt:C': ref_of(w)}, {'cnt:C': wide}, valid=(rv, tv))
    assert got['d_yx'] == dd and got['status'] == 'ok'


# --- 상태 사다리 ------------------------------------------------------------------------------------------------------

def test_insufficient_support():
    w = np.zeros((CANVAS, CANVAS), bool)
    w[ORIGIN + 40:ORIGIN + 42, ORIGIN + 40:ORIGIN + 45] = True            # 전경 10 px
    got = diagnose({'cnt:C': ref_of(w)}, {'cnt:C': wide_of(w, (0, 0))})
    assert got['status'] == 'insufficient_support' and got['d_yx'] is None and got['dice_global'] is not None


def test_insufficient_support_when_target_has_no_foreground_at_zero():
    w = world(2)
    got = diagnose({'cnt:C': ref_of(w)}, {'cnt:C': np.zeros((H + 2 * R, W + 2 * R), bool)})
    assert got['status'] == 'insufficient_support'


def test_low_score():
    got = diagnose({'cnt:C': ref_of(world(1))}, {'cnt:C': wide_of(world(99), (0, 0))}, min_dice=0.6)
    assert got['status'] == 'low_score' and got['reason'] == 'low_score' and got['d_yx'] is not None


def test_ambiguous_for_a_periodic_pattern():
    w = np.zeros((CANVAS, CANVAS), bool)
    for x in range(0, CANVAS, 4):
        w[:, x:x + 2] = True                     # 주기 4 세로 줄무늬
    got = diagnose({'cnt:C': ref_of(w)}, {'cnt:C': wide_of(w, (0, 0))})
    assert got['status'] == 'ambiguous'


def test_flat_all_zero_surface_is_ambiguous_or_unsupported():
    zeros = np.zeros((H, W), bool)
    got = diagnose({'cnt:C': zeros}, {'cnt:C': np.zeros((H + 2 * R, W + 2 * R), bool)}, min_dice=0.0, min_support=0, min_ring_support=0)
    assert got['status'] in ('ambiguous', 'insufficient_support')
    pick = la.choose(0, np.zeros((11, 11), np.int64), np.full((11, 11), 5, np.int64), 5, min_support=0, min_dice=0.0)
    assert pick['status'] == 'ambiguous'      # 분모 > 0 이고 전부 0 점: 평탄
    none = la.choose(0, np.zeros((11, 11), np.int64), np.zeros((11, 11), np.int64), 5, min_support=0, min_dice=0.0)
    assert none['status'] == 'insufficient_support'


def test_search_bound_hit():
    w = world(5)
    got = diagnose({'cnt:C': ref_of(w)}, {'cnt:C': wide_of(w, (0, R))})
    assert got['status'] == 'search_bound_hit' and got['d_yx'] == (0, R)


def test_ring_inconclusive_when_the_window_fills_the_box():
    w = world(6)
    got = diagnose({'cnt:C': ref_of(w)}, {'cnt:C': wide_of(w, (1, 1))}, window=(0, H, 0, W))
    assert got['status'] == 'ring_inconclusive' and got['ring_reason'] == 'insufficient_support' and got['d_yx'] == (1, 1)


def _change_world():
    """창 안에는 빽빽한 패턴, 링에는 성긴 패턴. target 의 창 내용만 (3, 2) 밀려 있고 링 내용은 그대로."""
    rng = np.random.default_rng(11)
    ref = np.zeros((H, W), bool)
    for _ in range(260):                               # 창(20..80) 안 빽빽
        y, x = int(rng.integers(24, 70)), int(rng.integers(24, 70))
        ref[y:y + int(rng.integers(2, 8)), x:x + int(rng.integers(2, 8))] = True
    for _ in range(14):                                # 링 성김
        y, x = int(rng.integers(2, 90)), int(rng.integers(2, 12))
        ref[y:y + 3, x:x + 4] = True
        y, x = int(rng.integers(2, 90)), int(rng.integers(86, 96))
        ref[y:y + 3, x:x + 4] = True
    base = np.zeros((CANVAS, CANVAS), bool)
    base[ORIGIN:ORIGIN + H, ORIGIN:ORIGIN + W] = ref
    inner = np.zeros_like(base)
    inner[ORIGIN + 20:ORIGIN + 80, ORIGIN + 20:ORIGIN + 80] = ref[20:80, 20:80]
    outer = base & ~inner
    # target 세계: 링은 그대로, 창 내용은 d=(3, 2) 로 보이게 둔다(wide[r+ddy+j, r+ddx+i] = ref[j, i] 이므로 세계에서는 반대로 민다)
    moved = np.roll(inner, (-3, -2), axis=(0, 1))
    return ref, outer | moved


def test_change_biased_when_only_the_window_content_moved():
    ref, target_world = _change_world()
    wide = target_world[ORIGIN - R:ORIGIN + H + R, ORIGIN - R:ORIGIN + W + R]
    got = diagnose({'cnt:C': ref}, {'cnt:C': wide}, tol=0.2, min_dice=0.2)
    assert got['d_yx'] != (0, 0)
    assert got['ring_d_yx'] is not None
    assert got['status'] == 'change_biased'


def test_ok_and_local_offset_differs_flag():
    w = world(8)
    ref, wide = {'cnt:C': ref_of(w)}, {'cnt:C': wide_of(w, (3, 2))}
    differs = diagnose(ref, wide, tol=0.2)
    assert differs['status'] == 'ok' and differs['flags'] == ['local_offset_differs']
    quiet = diagnose(ref, wide, tol=1.0)
    assert quiet['status'] == 'ok' and quiet['flags'] == []


def test_scoring_is_exact_integer_arithmetic():
    w = world(4)
    omega = np.ones((H, W), bool)
    sum_a, inter, sum_b = la.layer_scores(ref_of(w), wide_of(w, (1, 1)), omega, R)
    assert inter.dtype == np.int64 and sum_b.dtype == np.int64
    assert inter[R + 1, R + 1] == sum_a == sum_b[R + 1, R + 1]


# --- 선형 적합 --------------------------------------------------------------------------------------------------------

def _points(scale_x_ppm, scale_y_ppm, a=(0.7, -0.4), shape=(4000, 4000), n=5):
    pts = []
    for k in range(n):
        x, y = 600 + 700 * k, 500 + 650 * ((k * 3) % n)
        ux, uy = (x - shape[1] / 2) * PITCH, (y - shape[0] / 2) * PITCH
        pts.append((x, y, a[0] + scale_x_ppm * 1e-6 * ux, a[1] + scale_y_ppm * 1e-6 * uy))
    return pts


def test_linear_fit_recovers_scale_and_intercept_with_sign():
    fit, reason = la.linear_fit(_points(50, -30), (4000, 4000), PITCH)
    assert reason is None and fit['n'] == 5
    assert fit['scale_ppm_xy'] == [pytest.approx(50, abs=1e-3), pytest.approx(-30, abs=1e-3)]
    assert fit['intercept_um_xy'] == [pytest.approx(0.7, abs=1e-5), pytest.approx(-0.4, abs=1e-5)]
    assert fit['rms_um'] == pytest.approx(0, abs=1e-5)


def test_linear_fit_is_null_with_a_reason_when_conditions_fail():
    assert la.linear_fit(_points(50, -30)[:2], (4000, 4000), PITCH) == (None, 'fewer_than_3_ok_rois')
    close = [(1000 + k, 1000 + k, 0.0, 0.0) for k in range(5)]
    assert la.linear_fit(close, (4000, 4000), PITCH) == (None, 'roi_centres_span_too_small')
    same_x = [(1000, 500 + 900 * k, 0.0, 0.0) for k in range(4)]
    assert la.linear_fit(same_x, (4000, 4000), PITCH) == (None, 'roi_centres_span_too_small')


def test_estimate_bytes_grows_with_rois_layers_and_radius():
    small = la.estimate_bytes([(200, 200)], 1, 3, 5)
    assert la.estimate_bytes([(200, 200)] * 3, 1, 3, 5) > small
    assert la.estimate_bytes([(200, 200)], 4, 3, 5) > small
    assert la.estimate_bytes([(200, 200)], 1, 3, 20) > small
    assert math.isfinite(small)
