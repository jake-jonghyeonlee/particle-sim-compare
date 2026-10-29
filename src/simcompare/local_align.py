"""roi-detail/T2: ROI 국소 정렬 진단(진단 전용, 계산·export 에 반영하지 않는다).

전역 CNT 정렬 offset 이 ROI 상자 안에서는 정확히 맞지 않을 수 있다. ROI 마다 묶음 층의 전극 형상과 CNT 층을 쓰는 pooled Dice 로
전역 offset 둘레 ±r px 를 다시 찾아, 국소 offset 이 전역과 얼마나 다른지 표로 알린다(plan v1.1 + v1.2).

- 비교 영역 Ω 는 후보 d 와 무관하게 고정한다: Ω = reference valid(상자) ∧ ⋀_d target valid(global + d). 모든 d 의 점수를 같은 Ω 에서 센다.
  I(d) = Σ_L Σ_{x∈Ω} A_L(x) B_L(x+d), Dice(d) = 2 I(d) / Σ_L (Σ_Ω A_L + Σ_Ω B_L(x+d)). 모두 정수 합이다.
- 좌표: d = (ddy, ddx). target 은 상자를 사방 r 넓힌 crop B(r+ddy+j, r+ddx+i) = (reference 상자 (j, i) 의 target 픽셀 at global + d).
  국소 offset(px, (x, y) 순서) = 전역 offset + (ddx, ddy).
- 상태 사다리(앞에서 걸리면 그 상태): insufficient_support, low_score, ambiguous, search_bound_hit, ring_inconclusive, change_biased, ok.
- 정수 비교는 파이썬 int 교차곱으로 한다. 부동소수는 6자리로 반올림해 기록한다.
"""
from __future__ import annotations

import math
from fractions import Fraction

import numpy as np

SCHEMA = 'roi_local_alignment_v1'
LAYERS_BASIS = 'bundle_electrode_layers_plus_cnt'
MAX_LOCAL_SEARCH_PX = 30        # 3 µm @ 0.1 µm. 점수를 직접 합(후보 (2r+1)^2 개)으로 세므로 상한을 둔다.
# 시험값. 근거: ROI 상자 한 변은 보통 200 px(10 µm 창 + 50% 여백, 0.1 µm pitch)이고 전극 선폭은 수 px 다.
# 전경이 이 값 미만이면 Dice 의 분모가 작아 한 픽셀 차이로 최적이 뒤바뀐다(전극 선 하나가 길이 ~100 px 면 ~300 px).
MIN_SUPPORT_PX = 200
# 링(상자 ∖ 창)은 창 둘레 폭 w/4 인 띠라 상자의 3/4 쯤이지만 전경이 창 안에 몰릴 수 있다. 전체의 절반으로 낮춘 시험값.
MIN_RING_SUPPORT_PX = 100
# 최적과 체비쇼프 거리 2 이상 떨어진 d 의 Dice 가 최적 − 0.01 이내면 최적이 뚜렷하지 않다(평탄·주기 구조). Dice 0.01 = 1 %p.
AMBIGUITY_DELTA = Fraction(1, 100)
MIN_FIT_ROIS = 3
MIN_FIT_SPAN_FRACTION = 0.25
STATUSES = ('ok', 'change_biased', 'ring_inconclusive', 'search_bound_hit', 'ambiguous', 'low_score',
            'insufficient_support', 'skipped')


def _r6(value):
    return round(float(value), 6)


class RadiusError(ValueError):
    """--local-align-um 값 오류. 메시지에 값과 허용 범위를 담는다."""


def search_radius_px(local_align_um, pitch_um):
    """R(µm) → r_px = round(R / pitch) ∈ [1, MAX_LOCAL_SEARCH_PX]. 범위를 벗어나면 RadiusError."""
    if isinstance(local_align_um, bool) or not isinstance(local_align_um, (int, float)) or not math.isfinite(local_align_um) \
            or local_align_um <= 0:
        raise RadiusError(f'--local-align-um {local_align_um!r}: 유한한 양수여야 합니다')
    r = int(round(float(local_align_um) / float(pitch_um)))
    if r < 1 or r > MAX_LOCAL_SEARCH_PX:
        raise RadiusError(f'--local-align-um {local_align_um}: round(R / pitch {pitch_um}) = {r} px, 허용 범위는 1..{MAX_LOCAL_SEARCH_PX} px '
                          f'({_r6(pitch_um)} .. {_r6(MAX_LOCAL_SEARCH_PX * pitch_um)} µm 근처)')
    return r


def estimate_bytes(boxes, n_electrode_layers, n_cnt_layers, r):
    """진단이 보유할 바이트 추정(전 ROI 의 넓은 target 전극 crop + 가장 큰 ROI 하나의 점수 임시 배열).

    boxes: ROI 상자 (h, w) 목록. 전극 crop 은 (모델 쪽) 넓은 target bool (h+2r)(w+2r) 를 ROI·층마다 보유한다(reference 쪽은 기존 바탕 crop).
    임시: CNT 층마다 reference 상자 + 넓은 target 사본, Ω·침식 중간·valid 사본·AND 임시 6개 상자 크기 + 후보 (2r+1)^2 정수 배열 2개(int64) x 층 수."""
    held = 0
    biggest = 0
    n = 2 * int(r) + 1
    layers = int(n_electrode_layers) + int(n_cnt_layers)
    for h, w in boxes:
        wide = (int(h) + 2 * r) * (int(w) + 2 * r)
        held += wide * int(n_electrode_layers)
        temp = int(n_cnt_layers) * (int(h) * int(w) + wide) + 6 * wide + 16 * n * n * max(layers, 1)
        biggest = max(biggest, temp)
    return held + biggest


# --------------------------------------------------------------------------- 점수

def valid_domain(ref_valid_box, tgt_valid_wide, r):
    """Ω = reference valid(상자) ∧ 모든 d∈[-r, r]² 에서 target valid. tgt_valid_wide 는 (h+2r, w+2r)."""
    ref_valid_box = np.asarray(ref_valid_box, dtype=bool)
    tgt_valid_wide = np.asarray(tgt_valid_wide, dtype=bool)
    h, w = ref_valid_box.shape
    k = 2 * r + 1
    if tgt_valid_wide.shape != (h + 2 * r, w + 2 * r):
        raise ValueError(f'target valid shape {tgt_valid_wide.shape} != {(h + 2 * r, w + 2 * r)}')
    rows = np.ones((h, w + 2 * r), dtype=bool)
    for dy in range(k):
        rows &= tgt_valid_wide[dy:dy + h, :]
    out = ref_valid_box.copy()
    for dx in range(k):
        out &= rows[:, dx:dx + w]
    return out


def ring_mask(shape, window_box):
    """링 = 상자 ∖ 창. window_box = 상자 좌표의 (y0, y1, x0, x1)."""
    ring = np.ones(shape, dtype=bool)
    y0, y1, x0, x1 = window_box
    ring[max(y0, 0):max(y1, 0), max(x0, 0):max(x1, 0)] = False
    return ring


def layer_scores(a, b_wide, omega, r):
    """한 층: (Σ_Ω A, I(d) (2r+1,2r+1) int64, Σ_Ω B(x+d) (2r+1,2r+1) int64). 색인 [r+ddy, r+ddx]."""
    a = np.asarray(a, dtype=bool)
    b_wide = np.asarray(b_wide, dtype=bool)
    h, w = a.shape
    n = 2 * r + 1
    a_om = a & omega
    inter = np.zeros((n, n), dtype=np.int64)
    sum_b = np.zeros((n, n), dtype=np.int64)
    for iy in range(n):
        for ix in range(n):
            win = b_wide[iy:iy + h, ix:ix + w]
            inter[iy, ix] = np.count_nonzero(a_om & win)
            sum_b[iy, ix] = np.count_nonzero(omega & win)
    return int(np.count_nonzero(a_om)), inter, sum_b


def pooled(scores):
    """층별 layer_scores 결과 목록 → (Σ A, Σ I, Σ B)."""
    sum_a = sum(s[0] for s in scores)
    inter = sum(s[1] for s in scores)
    sum_b = sum(s[2] for s in scores)
    return sum_a, inter, sum_b


def _dice(inter, sum_a, sum_b):
    """(분자 2I, 분모) 정수 쌍. 분모가 0 이면 None."""
    den = int(sum_a) + int(sum_b)
    return None if den == 0 else (2 * int(inter), den)


def choose(sum_a, inter, sum_b, r, *, min_support, min_dice):
    """한 점수 면에서 최적 d 와 상태(1~5단). 반환 {status, d: (ddy, ddx)|None, dice, dice_zero}.

    insufficient_support: Σ_Ω A 또는 d=0 의 Σ_Ω B 가 min_support 미만, 또는 분모 0 인 d 만 있음.
    low_score: 최적 Dice < min_dice. ambiguous: 체비쇼프 거리 ≥ 2 인 d 의 Dice ≥ 최적 − AMBIGUITY_DELTA. search_bound_hit: 최적이 체비쇼프 r."""
    n = 2 * r + 1
    cand = []
    for iy in range(n):
        for ix in range(n):
            frac = _dice(inter[iy, ix], sum_a, sum_b[iy, ix])
            if frac is not None:
                cand.append((iy - r, ix - r, frac))
    zero = _dice(inter[r, r], sum_a, sum_b[r, r])
    dice_zero = None if zero is None else _r6(zero[0] / zero[1])
    if not cand or int(sum_a) < min_support or int(sum_b[r, r]) < min_support:
        return {'status': 'insufficient_support', 'd': None, 'dice': None, 'dice_zero': dice_zero}

    def key(item):      # 최대 Dice(정수 교차곱), 동률이면 |d|² 작은 쪽, 그다음 (ddy, ddx) 사전순
        return Fraction(item[2][0], item[2][1])
    best_frac = max(key(c) for c in cand)
    tied = [c for c in cand if key(c) == best_frac]
    tied.sort(key=lambda c: (c[0] * c[0] + c[1] * c[1], c[0], c[1]))
    by, bx, (bn, bd) = tied[0]
    dice = _r6(bn / bd)
    out = {'status': 'ok', 'd': (by, bx), 'dice': dice, 'dice_zero': dice_zero}
    if float(bn) / bd < float(min_dice):
        out['status'] = 'low_score'
        return out
    delta = AMBIGUITY_DELTA
    for dy, dx, (cn, cd) in cand:
        if max(abs(dy - by), abs(dx - bx)) >= 2 and Fraction(cn, cd) + delta >= best_frac:
            out['status'] = 'ambiguous'
            return out
    if max(abs(by), abs(bx)) == r:
        out['status'] = 'search_bound_hit'
    return out


def _dice_at(scores_one, d, r):
    sum_a, inter, sum_b = scores_one
    frac = _dice(inter[r + d[0], r + d[1]], sum_a, sum_b[r + d[0], r + d[1]])
    return None if frac is None else _r6(frac[0] / frac[1])


def diagnose_roi(ref_layers, tgt_wide_layers, ref_valid_box, tgt_valid_wide, window_box, r, *, pitch_um, tolerance_um, min_dice,
                 min_support=MIN_SUPPORT_PX, min_ring_support=MIN_RING_SUPPORT_PX):
    """한 ROI 의 진단 결과(상태 사다리 1~8단; 1단 skipped 는 호출자 몫).

    ref_layers: {층 키: bool (h, w)}, tgt_wide_layers: {층 키: bool (h+2r, w+2r)} 같은 키 순서. window_box: 상자 좌표 창 (y0, y1, x0, x1).
    반환 키: status, reason, flags, d_yx (정수 쌍 또는 None), dice_global, dice_local, ring_d_yx, ring_dice, ring_reason, per_layer, omega_pixels."""
    omega = valid_domain(ref_valid_box, tgt_valid_wide, r)
    result = {'status': 'insufficient_support', 'reason': None, 'flags': [], 'd_yx': None, 'dice_global': None, 'dice_local': None,
              'ring_d_yx': None, 'ring_dice': None, 'ring_reason': None, 'per_layer': {}, 'omega_pixels': int(np.count_nonzero(omega))}
    if not result['omega_pixels']:
        result['status'], result['reason'] = 'skipped', 'no_valid_region'
        return result
    keys = list(ref_layers)
    scores = [layer_scores(ref_layers[k], tgt_wide_layers[k], omega, r) for k in keys]
    sum_a, inter, sum_b = pooled(scores)
    pick = choose(sum_a, inter, sum_b, r, min_support=min_support, min_dice=min_dice)
    result['dice_global'] = pick['dice_zero']
    result['status'] = pick['status']
    if pick['d'] is not None:
        d = pick['d']
        result['d_yx'], result['dice_local'] = d, pick['dice']
        result['per_layer'] = {k: {'dice_global': _dice_at(s, (0, 0), r), 'dice_local': _dice_at(s, d, r)} for k, s in zip(keys, scores)}
    if pick['status'] != 'ok':
        result['reason'] = pick['status']
        return result
    ring = omega & ring_mask(omega.shape, window_box)
    ring_scores = pooled([layer_scores(ref_layers[k], tgt_wide_layers[k], ring, r) for k in keys])
    ring_pick = choose(*ring_scores, r, min_support=min_ring_support, min_dice=min_dice)
    result['ring_dice'] = ring_pick['dice']
    if ring_pick['status'] != 'ok':
        result['status'], result['reason'], result['ring_reason'] = 'ring_inconclusive', 'ring_inconclusive', ring_pick['status']
        result['ring_d_yx'] = ring_pick['d']
        return result
    result['ring_d_yx'] = ring_pick['d']
    d, dr = result['d_yx'], ring_pick['d']
    gap_um = math.hypot((d[0] - dr[0]) * pitch_um, (d[1] - dr[1]) * pitch_um)
    if gap_um > tolerance_um:
        result['status'], result['reason'] = 'change_biased', 'change_biased'
        return result
    result['status'] = 'ok'
    if math.hypot(d[0] * pitch_um, d[1] * pitch_um) > tolerance_um:
        result['flags'].append('local_offset_differs')
    return result


# --------------------------------------------------------------------------- 선형 적합

def linear_fit(points, frame_shape, pitch_um):
    """ok ROI 들의 국소 offset(µm) = a + s·(중심 − frame 중심) 을 x, y 따로 최소제곱으로 구한다.

    points: [(center_x_px, center_y_px, offset_x_um, offset_y_um)]. 반환 (결과 dict 또는 None, 사유 또는 None).
    조건: 점 3개 이상, x 범위와 y 범위가 각각 frame 크기의 1/4 이상(px)."""
    n = len(points)
    if n < MIN_FIT_ROIS:
        return None, 'fewer_than_%d_ok_rois' % MIN_FIT_ROIS
    height, width = int(frame_shape[0]), int(frame_shape[1])
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    if max(xs) - min(xs) < MIN_FIT_SPAN_FRACTION * width or max(ys) - min(ys) < MIN_FIT_SPAN_FRACTION * height:
        return None, 'roi_centres_span_too_small'
    cx, cy = width / 2.0, height / 2.0
    slopes, intercepts, sq = [], [], 0.0
    for pos, centre, idx in ((xs, cx, 2), (ys, cy, 3)):
        u = [(p - centre) * pitch_um for p in pos]
        v = [pt[idx] for pt in points]
        mu, mv = sum(u) / n, sum(v) / n
        suu = sum((a - mu) ** 2 for a in u)
        if suu <= 0:
            return None, 'roi_centres_span_too_small'
        s = sum((a - mu) * (b - mv) for a, b in zip(u, v)) / suu
        a0 = mv - s * mu
        slopes.append(s)
        intercepts.append(a0)
        sq += sum((b - (a0 + s * a)) ** 2 for a, b in zip(u, v))
    return {'n': n, 'scale_ppm_xy': [_r6(s * 1e6) for s in slopes], 'intercept_um_xy': [_r6(a) for a in intercepts],
            'rms_um': _r6(math.sqrt(sq / (2.0 * n)))}, None
