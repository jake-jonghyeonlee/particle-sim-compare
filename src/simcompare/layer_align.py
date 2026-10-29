"""layer-align/T2: unit 기하, Dice 우선 선택, unit 점수 창, ROI 접기, 이음새, 탐색 범위 요구(순수 함수, 파일 I/O 없음).

좌표 약속
- 영상 배열 색인은 (y, x). offset·delta 의 공개 표기는 기존 local_align 출력 관례대로 (x, y) 다(`offset_xy_px`, `delta_xy_px`).
- 후보는 상대 이동 δ(체비쇼프 |δ| <= R), 절대 offset o = s + δ (s = 시작 offset). FFT lag, crop, 저장 offset 모두 절대 o 를 쓴다.
- 점수 창 색인은 [R + dy, R + dx]. I(δ) = Σ_{x∈Ω} A(x) B(x + s + δ), b(δ) = Σ_{x∈Ω} B(x + s + δ), a = Σ_Ω A. 모두 정수.
- Dice(δ) = 2 I(δ) / (a + b(δ)) 는 Fraction 으로 비교한다(평균·중앙값 없음).

알려진 한계(X7): 탐색 범위 밖의 진짜 peak 와 안쪽 부차 peak 가 함께 있으면 원리상 완전히 막지 못한다. 막는 장치는 둘뿐이다:
선택 Dice 가 min_dice 미만이면 low_score, 선택 δ 가 경계 1 px 안이면 search_bound_hit(적용 안 함).
"""
from __future__ import annotations

import math
import re
from fractions import Fraction

import numpy as np

from .cnt_align import _fast_len
from .local_align import AMBIGUITY_DELTA, MIN_SUPPORT_PX

BORDER_PX = 500
MAX_UNIT_SEARCH_PX = 300
MAX_LOCAL_APPLY_PX = 300
STATUSES = ('ok', 'ambiguous', 'low_score', 'search_bound_hit', 'insufficient_support')


def _r6(value):
    return round(float(value), 6)


def _natural_key(text):
    return [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', str(text))]


# --------------------------------------------------------------------------- unit 기하

class UnitGeometry:
    """hotspot frame (H, W) 는 2×2 unit 영상에서 사방 border_px 를 자른 중앙 영역이다."""

    def __init__(self, frame_shape_yx, border_px=BORDER_PX):
        self.frame_shape = (int(frame_shape_yx[0]), int(frame_shape_yx[1]))
        self.border_px = int(border_px)
        h, w = self.frame_shape
        self.unit_px = ((h + 2 * self.border_px) // 2, (w + 2 * self.border_px) // 2)
        self.unit_size_odd_px = bool((h + 2 * self.border_px) % 2 or (w + 2 * self.border_px) % 2)

    def unit_index(self, p):
        """frame 픽셀 p=(y, x) → unit 번호 (iy, ix)."""
        return ((int(p[0]) + self.border_px) // self.unit_px[0], (int(p[1]) + self.border_px) // self.unit_px[1])

    def unit_coord(self, p):
        """frame 픽셀 p=(y, x) → unit 안 좌표 (p + border) mod unit_px."""
        return ((int(p[0]) + self.border_px) % self.unit_px[0], (int(p[1]) + self.border_px) % self.unit_px[1])

    def boundary_px(self):
        """frame 안 unit 경계(끝 미포함 경계) = unit_px - border, 축별 (y, x)."""
        return (self.unit_px[0] - self.border_px, self.unit_px[1] - self.border_px)

    def unit_regions(self):
        """{(iy, ix): (y0, y1, x0, x1)} reference 픽셀, 끝 미포함. 네 영역이 frame 을 정확히 분할한다."""
        h, w = self.frame_shape
        by, bx = self.boundary_px()
        ys = {0: (0, by), 1: (by, h)}
        xs = {0: (0, bx), 1: (bx, w)}
        return {(iy, ix): (ys[iy][0], ys[iy][1], xs[ix][0], xs[ix][1]) for iy in (0, 1) for ix in (0, 1)}


def delta_u(ref_geometry, tgt_geometry):
    """축별 정수 (dy, dx) = unit_px_target − unit_px_reference."""
    return (tgt_geometry.unit_px[0] - ref_geometry.unit_px[0], tgt_geometry.unit_px[1] - ref_geometry.unit_px[1])


def delta_u_max(ref_geometry, tgt_geometry):
    d = delta_u(ref_geometry, tgt_geometry)
    return max(abs(d[0]), abs(d[1]))


def required_unit_radius_px(delta_u_max_px):
    """unit 탐색 반경 하한: unit 번호 이동 |ΔU| + unit 내부 고정점 이동 최대 |ΔU| + 여유 2."""
    return 2 * int(delta_u_max_px) + 2


def required_local_radius_px(delta_u_max_px):
    return int(delta_u_max_px) + 2


# --------------------------------------------------------------------------- Dice 우선 선택

def _dice_frac(inter, a, b):
    den = int(a) + int(b)
    return None if den == 0 else Fraction(2 * int(inter), den)


def _cheb(p, q):
    return max(abs(p[0] - q[0]), abs(p[1] - q[1]))


def _tie_key(d):
    return (d[0] * d[0] + d[1] * d[1], d[0], d[1])


def choose_dice_first(per_layer_scores, s_xy, radius, *, min_dice, min_support=MIN_SUPPORT_PX, epsilon=AMBIGUITY_DELTA, excluded=None):
    """v1.2 X1: 층 Dice 우선 선택.

    per_layer_scores: {층 키: {'a': int, 'inter': (2R+1,2R+1) int, 'b': (2R+1,2R+1) int}}. 키는 `layer:<이름>`/`cnt:<이름>`.
    excluded: {층 키: 사유} 호출자가 이미 뺀 층(one_sided 등). 반환 dict 키는 모듈 docstring·A3 기록 참조."""
    r = int(radius)
    if r < 1:
        raise ValueError(f'radius {radius!r}: 1 이상이어야 합니다')
    n = 2 * r + 1
    sx, sy = int(s_xy[0]), int(s_xy[1])
    result = {'status': 'insufficient_support', 'flags': [], 'apply': False, 'offset_xy_px': None, 'delta_xy_px': None,
              'decided_by_layer': None, 'layer_order': [], 'per_layer': {}, 'start_xy_px': [sx, sy], 'radius_px': r,
              'best_dice': None, 'dice': None, 'ambiguity_set_size_initial': None, 'ambiguity_set_size': None,
              'layers_disagreeing': []}
    excluded = dict(excluded or {})
    dice = {}       # 층 키 → {(dy, dx): Fraction}
    info = {}
    for key in sorted(per_layer_scores, key=_natural_key):
        sc = per_layer_scores[key]
        a, inter, b = int(sc['a']), np.asarray(sc['inter']), np.asarray(sc['b'])
        if inter.shape != (n, n) or b.shape != (n, n):
            raise ValueError(f'{key}: score window shape {inter.shape}/{b.shape} != {(n, n)}')
        rec = {'support_a_px': a, 'support_b_px': int(b[r, r]), 'best_dice': None, 'argmax_delta_xy_px': None,
               'dice_at_chosen': None, 'excluded_reason': None}
        info[key] = rec
        if key in excluded:
            rec['excluded_reason'] = excluded[key]
            continue
        if a < min_support or int(b[r, r]) < min_support:
            rec['excluded_reason'] = 'insufficient_support'
            continue
        table = {}
        for iy in range(n):
            for ix in range(n):
                f = _dice_frac(inter[iy, ix], a, b[iy, ix])
                if f is not None:
                    table[(iy - r, ix - r)] = f
        if not table:
            rec['excluded_reason'] = 'insufficient_support'
            continue
        best = max(table.values())
        argmax = min((d for d, f in table.items() if f == best), key=_tie_key)
        rec['best_dice'], rec['argmax_delta_xy_px'] = _r6(best), [argmax[1], argmax[0]]
        rec['_best'], rec['_argmax'] = best, argmax
        dice[key] = table
    for key in excluded:
        if key not in info:
            info[key] = {'support_a_px': None, 'support_b_px': None, 'best_dice': None, 'argmax_delta_xy_px': None,
                         'dice_at_chosen': None, 'excluded_reason': excluded[key]}
    order = sorted(dice, key=lambda k: (-info[k]['_best'], _natural_key(k)))
    result['layer_order'] = order

    def finish():
        for rec in info.values():
            rec.pop('_best', None)
            rec.pop('_argmax', None)
        result['per_layer'] = info
        return result

    if not order:
        return finish()
    l1 = order[0]
    best1 = info[l1]['_best']
    result['best_dice'] = _r6(best1)
    if float(best1) < float(min_dice):
        result['status'] = 'low_score'
        return finish()
    s_set = [d for d, f in dice[l1].items() if f + epsilon >= best1]
    result['ambiguity_set_size_initial'] = len(s_set)
    decided = l1
    for k in order[1:]:
        if len(s_set) == 1:
            break
        table = dice[k]
        floor = Fraction(-1)
        m = max(table.get(d, floor) for d in s_set)
        s_set = [d for d in s_set if table.get(d, floor) + epsilon >= m]
        decided = k
    result['ambiguity_set_size'] = len(s_set)
    if len(s_set) == 1:
        chosen = s_set[0]
    else:
        diameter = max(_cheb(p, q) for p in s_set for q in s_set)
        if diameter >= 2:
            result['status'] = 'ambiguous'
            return finish()
        floor = Fraction(-1)
        chosen = min(s_set, key=lambda d: (tuple(-dice[k].get(d, floor) for k in order),) + _tie_key(d))
        result['flags'].append('subpixel_tie')
    result['decided_by_layer'] = decided
    result['delta_xy_px'] = [chosen[1], chosen[0]]
    result['offset_xy_px'] = [sx + chosen[1], sy + chosen[0]]
    result['dice'] = _r6(dice[l1][chosen])
    for k in order:
        info[k]['dice_at_chosen'] = _r6(dice[k][chosen]) if chosen in dice[k] else None
        if info[k]['_argmax'] != chosen:
            result['layers_disagreeing'].append(k)
    if float(dice[l1][chosen]) < float(min_dice):      # R1 adv:2 / X7: 선택 δ 에서의 L1 Dice 로 판정한다(best 가 아니다)
        result['status'] = 'low_score'
    elif _cheb(chosen, (0, 0)) >= r - 1:
        result['status'] = 'search_bound_hit'
    else:
        result['status'] = 'ok'
        result['apply'] = True
    return finish()


# --------------------------------------------------------------------------- Ω 와 점수 창

def _wide_shifted(arr, s_xy, radius, out_shape):
    """Bw[j, i] = arr[j − R + sy, i − R + sx] (범위 밖 False/0), 모양 (h + 2R, w + 2R). 색인 (y, x)."""
    arr = np.asarray(arr)
    h, w = out_shape
    r = int(radius)
    sx, sy = int(s_xy[0]), int(s_xy[1])
    out = np.zeros((h + 2 * r, w + 2 * r), dtype=arr.dtype)
    # 출력 (j, i) ← 입력 (j − r + sy, i − r + sx)
    j0, j1 = max(0, r - sy), min(h + 2 * r, arr.shape[0] + r - sy)
    i0, i1 = max(0, r - sx), min(w + 2 * r, arr.shape[1] + r - sx)
    if j0 < j1 and i0 < i1:
        out[j0:j1, i0:i1] = arr[j0 - r + sy:j1 - r + sy, i0 - r + sx:i1 - r + sx]
    return out


def _square_all(mask, radius):
    """모든 점이 (2r+1)² 이웃(체비쇼프 r)에서 True. 영상 밖은 False."""
    r = int(radius)
    if r <= 0:
        return np.asarray(mask, dtype=bool).copy()
    m = np.asarray(mask, dtype=bool)
    h, w = m.shape
    pad = np.zeros((h + 2 * r, w + 2 * r), dtype=bool)
    pad[r:r + h, r:r + w] = m
    rows = np.ones((h + 2 * r, w), dtype=bool)
    for dx in range(2 * r + 1):
        rows &= pad[:, dx:dx + w]
    out = np.ones((h, w), dtype=bool)
    for dy in range(2 * r + 1):
        out &= rows[dy:dy + h, :]
    return out


def fixed_omega(ref_valid, tgt_valid, s_xy, radius, erode):
    """v1.2 X2: Ω = reference valid ∧ ⋀_{|δ|<=R} target valid(x + s + δ), 그 뒤 정사각 침식(영상 밖 False).

    Ω 는 δ 와 무관하다(target valid 를 모든 후보에서 요구)."""
    ref_valid = np.asarray(ref_valid, dtype=bool)
    wide = _wide_shifted(np.asarray(tgt_valid, dtype=bool), s_xy, radius, ref_valid.shape)
    h, w = ref_valid.shape
    k = 2 * int(radius) + 1
    rows = np.ones((h + 2 * int(radius), w), dtype=bool)
    for dx in range(k):
        rows &= wide[:, dx:dx + w]
    every = np.ones((h, w), dtype=bool)
    for dy in range(k):
        every &= rows[dy:dy + h, :]
    return _square_all(ref_valid & every, erode)


def unit_window_scores(ref_layer, tgt_layer, omega, s_xy, radius):
    """한 층 bool 두 장 → (a, I, b). I, b 는 (2R+1, 2R+1) int64, 색인 [R+dy, R+dx]. omega 는 reference 모양 영역 마스크(예: 한 unit 영역).

    FFT 상관(rfft2 zero-pad, wrap 없음, 반올림). 직접 합과 정수로 일치해야 한다."""
    ref_layer = np.asarray(ref_layer, dtype=bool)
    omega = np.asarray(omega, dtype=bool)
    if omega.shape != ref_layer.shape:
        raise ValueError(f'omega shape {omega.shape} != reference shape {ref_layer.shape}')
    r = int(radius)
    h, w = ref_layer.shape
    a_om = ref_layer & omega
    a = int(np.count_nonzero(a_om))
    bw = _wide_shifted(np.asarray(tgt_layer, dtype=bool), s_xy, r, (h, w))
    size = (_fast_len(h + 2 * r + 1), _fast_len(w + 2 * r + 1))
    spec_b = np.fft.rfft2(bw.astype(np.float64), s=size)
    spec_a = np.fft.rfft2(a_om.astype(np.float64), s=size)
    spec_o = np.fft.rfft2(omega.astype(np.float64), s=size)
    n = 2 * r
    inter = np.rint(np.fft.irfft2(np.conj(spec_a) * spec_b, s=size)[:n + 1, :n + 1]).astype(np.int64)
    sum_b = np.rint(np.fft.irfft2(np.conj(spec_o) * spec_b, s=size)[:n + 1, :n + 1]).astype(np.int64)
    return a, inter, sum_b


def region_window_scores(ref_layer, tgt_layer, omega, region, s_xy, radius):
    """``unit_window_scores`` 와 같은 값(a, I, b)을 reference 영역 ``region`` = (y0, y1, x0, x1) 상자 안에서만 계산한다.

    omega 는 frame 모양 마스크이고 영역 밖 값은 쓰지 않는다. 영역 하나의 FFT 는 상자 크기로 작아서 4 영역 합이 frame 한 번 값이다.
    target 은 상자를 s 만큼 옮기고 R 만큼 넓힌 부분만 잘라 쓴다(frame 밖 False)."""
    y0, y1, x0, x1 = (int(v) for v in region)
    r = int(radius)
    sx, sy = int(s_xy[0]), int(s_xy[1])
    ref_layer = np.asarray(ref_layer, dtype=bool)
    tgt_layer = np.asarray(tgt_layer, dtype=bool)
    h, w = y1 - y0, x1 - x0
    sub = np.zeros((h + 2 * r, w + 2 * r), dtype=bool)
    ty0, ty1, tx0, tx1 = y0 + sy - r, y1 + sy + r, x0 + sx - r, x1 + sx + r
    cy0, cy1, cx0, cx1 = max(ty0, 0), min(ty1, tgt_layer.shape[0]), max(tx0, 0), min(tx1, tgt_layer.shape[1])
    if cy0 < cy1 and cx0 < cx1:
        sub[cy0 - ty0:cy1 - ty0, cx0 - tx0:cx1 - tx0] = tgt_layer[cy0:cy1, cx0:cx1]
    return unit_window_scores(ref_layer[y0:y1, x0:x1], sub, np.asarray(omega, dtype=bool)[y0:y1, x0:x1], (r, r), r)


def estimate_layer_bytes(ref_shape, tgt_shape, radius, n_regions=4):
    """unit 점수 창 계산의 정점 바이트(층 1개씩 처리, 영역 4개 점수 창만 남김). cnt_align.estimate_alignment_bytes 와 같은 방식."""
    r = int(radius)
    ny = _fast_len(ref_shape[0] + 2 * r + 1)
    nx = _fast_len(ref_shape[1] + 2 * r + 1)
    real = ny * nx * 8
    cplx = ny * (nx // 2 + 1) * 16
    ref_px = int(ref_shape[0]) * int(ref_shape[1])
    tgt_px = int(tgt_shape[0]) * int(tgt_shape[1])
    wide_px = (int(ref_shape[0]) + 2 * r) * (int(ref_shape[1]) + 2 * r)
    boolean = 4 * ref_px + 2 * tgt_px + 2 * wide_px
    window = (2 * r + 1) ** 2 * 8 * 2 * int(n_regions)
    return int(boolean + 2 * real + 4 * cplx + window)


# --------------------------------------------------------------------------- ROI 접기

def fold_rois(items, geometry, tolerance_px):
    """순위 순 greedy 접기(A1). items: 순위 순 dict 목록, 키 `region_id`, `center_yx_px`(reference 픽셀 (y, x)), 선택 `rank`.

    축별 원환 거리 min(|Δ|, U−|Δ|) 의 유클리드 거리 <= tolerance_px(포함) 이고 unit 번호가 서로 다를 때만 대표의 replicas 가 된다.
    반환 (남은 목록(순서 유지), {대표 region_id: [replica 기록]}). 기록은 region_id, rank, center_yx_px, unit_index, folded_yx_px, distance_px."""
    tol = float(tolerance_px)
    tol2 = tol * tol
    kept = []
    replicas = {}
    folded = {}
    for pos, item in enumerate(items):
        c = (int(item['center_yx_px'][0]), int(item['center_yx_px'][1]))
        u = geometry.unit_coord(c)
        idx = geometry.unit_index(c)
        folded[item['region_id']] = (u, idx)
        target = None
        for rep in kept:
            ru, ridx = folded[rep['region_id']]
            if ridx == idx:
                continue
            dy = abs(u[0] - ru[0])
            dx = abs(u[1] - ru[1])
            dy = min(dy, geometry.unit_px[0] - dy)
            dx = min(dx, geometry.unit_px[1] - dx)
            d2 = dy * dy + dx * dx
            if d2 <= tol2 + 1e-9:
                target = (rep, math.sqrt(d2))
                break
        if target is None:
            kept.append(item)
            replicas[item['region_id']] = []
            continue
        replicas[target[0]['region_id']].append({
            'region_id': item['region_id'], 'rank': item.get('rank', pos + 1), 'center_yx_px': [c[0], c[1]],
            'unit_index': list(idx), 'folded_yx_px': [u[0], u[1]], 'distance_px': _r6(target[1])})
    return kept, replicas


# --------------------------------------------------------------------------- 이음새

def seam_band(geometry, unit_offsets):
    """unit 내부 경계 양쪽에 인접 unit offset 체비쇼프 차이 폭 w 의 제외 마스크(True = 제외). 전부 같으면 빈 마스크.

    unit_offsets: {(iy, ix): (ox, oy)}. 경계 b 에서 [b − w, b + w) 를 두 unit 이 맞닿은 구간에서만 뺀다."""
    h, wd = geometry.frame_shape
    mask = np.zeros((h, wd), dtype=bool)
    regions = geometry.unit_regions()
    for (iy, ix), (y0, y1, x0, x1) in regions.items():
        if ix == 0:
            width = _cheb_xy(unit_offsets[(iy, 0)], unit_offsets[(iy, 1)])
            if width:
                mask[y0:y1, max(x1 - width, 0):min(x1 + width, wd)] = True
        if iy == 0:
            width = _cheb_xy(unit_offsets[(0, ix)], unit_offsets[(1, ix)])
            if width:
                mask[max(y1 - width, 0):min(y1 + width, h), x0:x1] = True
    return mask


def _cheb_xy(p, q):
    return max(abs(int(p[0]) - int(q[0])), abs(int(p[1]) - int(q[1])))
