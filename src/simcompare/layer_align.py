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
MAX_STRETCH_RADIUS_PX = 60      # stretch/X5: 타일 FFT 크기를 묶는 반경 상한(6 µm). ΔU <= 58 px 까지. 더 크면 unit/local 을 쓴다.
STRETCH_TILE_RECORD_BYTES = 4096
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
    """영역 격자의 내부 경계 양쪽에 인접 영역 offset 체비쇼프 차이 폭 w 의 제외 마스크(True = 제외). 전부 같으면 빈 마스크.

    unit_offsets: {(i, j): (ox, oy)}, 키는 ``geometry.unit_regions()`` 와 같다(행 i, 열 j; unit 기하는 2×2, stretch 타일 장은 n×m).
    경계 b 에서 [b − w, b + w) 를 두 영역이 맞닿은 구간에서만 뺀다. 오른쪽 이웃 (i, j+1) 과 아래쪽 이웃 (i+1, j) 쌍마다 한 번씩."""
    h, wd = geometry.frame_shape
    mask = np.zeros((h, wd), dtype=bool)
    regions = geometry.unit_regions()
    for (iy, ix), (y0, y1, x0, x1) in regions.items():
        if (iy, ix + 1) in regions:
            width = _cheb_xy(unit_offsets[(iy, ix)], unit_offsets[(iy, ix + 1)])
            if width:
                mask[y0:y1, max(x1 - width, 0):min(x1 + width, wd)] = True
        if (iy + 1, ix) in regions:
            width = _cheb_xy(unit_offsets[(iy, ix)], unit_offsets[(iy + 1, ix)])
            if width:
                mask[max(y1 - width, 0):min(y1 + width, h), x0:x1] = True
    return mask


def _cheb_xy(p, q):
    return max(abs(int(p[0]) - int(q[0])), abs(int(p[1]) - int(q[1])))


# --------------------------------------------------------------------------- stretch 타일 정렬 (stretch-align/T1)

def _split_axis(length, tile):
    """길이 ``length`` 를 ``tile`` 칸씩 자른 [시작, 끝) 목록. 끝 나머지가 tile/2 미만이면 앞 타일에 합친다. tile 보다 짧으면 하나."""
    n = length // tile
    rem = length - n * tile
    if n == 0:
        return [(0, length)]
    edges = [(k * tile, (k + 1) * tile) for k in range(n)]
    if rem == 0:
        return edges
    if 2 * rem < tile:
        edges[-1] = (edges[-1][0], length)
    else:
        edges.append((n * tile, length))
    return edges


def tile_regions(geometry, tile_px):
    """unit 영역마다 ``tile_px`` 격자 타일. 타일은 unit 경계를 넘지 않는다. 결정적 순서(unit 순, 행, 열).

    반환: dict 목록 ``{'unit_index': (iy, ix), 'tile_index': (ty, tx), 'region': (y0, y1, x0, x1)}`` (reference 픽셀, 끝 미포함).
    타일 행·열 경계는 unit 영역의 해당 축 구간만으로 정해서, 전체 타일이 frame 위의 n×m 격자를 이룬다."""
    tile = int(tile_px)
    if tile < 1:
        raise ValueError(f'tile_px {tile_px!r}: 1 이상이어야 합니다')
    out = []
    regions = geometry.unit_regions()
    for q in sorted(regions):
        y0, y1, x0, x1 = regions[q]
        for ty, (ra, rb) in enumerate(_split_axis(y1 - y0, tile)):
            for tx, (ca, cb) in enumerate(_split_axis(x1 - x0, tile)):
                out.append({'unit_index': q, 'tile_index': (ty, tx), 'region': (y0 + ra, y0 + rb, x0 + ca, x0 + cb)})
    return out


def stretch_step_max_px(delta_u_max_px, tile_px, unit_min_px):
    """G1 이웃 타일 단차 상한 s_max = ceil(|ΔU|max · T / U_min) + 1 (정수 계산). U_min = 두 제품 unit 크기 중 작은 축 값."""
    d, t, u = abs(int(delta_u_max_px)), int(tile_px), int(unit_min_px)
    if t < 1 or u < 1:
        raise ValueError('tile_px 와 unit_min_px 는 1 이상이어야 합니다')
    return -((-d * t) // u) + 1


def stretch_step_within_tolerance(s_max, tolerance_px):
    """한 이음새가 지울 수 있는 CD 변화 s_max 가 허용 폭 r(px) 이 이미 세지 않는 CD 변화 2r 이하인가."""
    return int(s_max) <= 2 * int(tolerance_px)


def estimate_stretch_bytes(max_tile_yx, radius, n_layers, *, n_tiles=0, frame_shape=None, n_starts=4, concurrent_fft=5):
    """타일 정렬의 정점 바이트(X5). 타일 점수는 하나씩 계산해 바로 버리므로 가장 큰 타일 하나만 센다.

    = FFT 임시(fast_len(T_y+2R+1)·fast_len(T_x+2R+1) 복소 × 동시 생존 수) + 그 타일의 bool 임시
      + 층 수 × 3 × (2R+1)² × 8 (층별 점수 창 a/I/b 를 선택 직전까지 들고 있음) + 타일 기록 + (frame_shape 를 주면) Ω 마스크 n_starts 장과 임시.
    호출자는 이 값과 ``reserved_bytes`` 를 ``max_operation_bytes`` 에 견줘 큰 배열을 할당하기 전에 거부한다."""
    r = int(radius)
    ty, tx = int(max_tile_yx[0]), int(max_tile_yx[1])
    ny, nx = _fast_len(ty + 2 * r + 1), _fast_len(tx + 2 * r + 1)
    fft = ny * nx * 16 * int(concurrent_fft)
    boolean = 4 * ty * tx + 2 * (ty + 2 * r) * (tx + 2 * r)
    windows = int(n_layers) * 3 * (2 * r + 1) ** 2 * 8
    records = int(n_tiles) * STRETCH_TILE_RECORD_BYTES
    omega = 0
    if frame_shape is not None:
        frame_px = int(frame_shape[0]) * int(frame_shape[1])
        # 동시 생존: 캐시된 Ω (n_starts − 1)장 + fixed_omega 안 최대 7장(wide·rows·every·ref&every·pad·rows·out); 끝나면 모두 해제
        omega = (int(n_starts) + 6) * frame_px
    return int(fft + boolean + windows + records + omega)


_TILE_KEYS = ('status', 'flags', 'decided_by_layer', 'layer_order', 'layers_disagreeing', 'best_dice', 'dice',
              'ambiguity_set_size_initial', 'ambiguity_set_size', 'per_layer')


def choose_tile_offsets(tiles, unit_offsets, layers, ref_valid, tgt_valid, radius, *, min_dice, s_max, unit_sources=None,
                        global_offset=None, excluded=None, min_support=MIN_SUPPORT_PX, epsilon=AMBIGUITY_DELTA,
                        erode=None, max_operation_bytes=None, reserved_bytes=0):
    """타일마다 층 공통 offset 하나를 Dice 우선으로 고른다(L3). 점수는 타일 하나씩 계산·선택하고 바로 버린다(X5).

    tiles: ``tile_regions`` 결과. unit_offsets: {(iy, ix): (ox, oy)} 타일 시작점(= unit 적용 offset). unit_sources: {(iy, ix): 'unit'|'global'}
    (기본 'unit'; unit 정렬이 실패해 global 이 적용된 unit 은 'global'). layers: {층 키: (reference bool, target bool)}.
    ref_valid/tgt_valid: Ω 용 관측 마스크. 상태 ``ok`` 만 적용하고 그 밖은 unit offset 으로 대체(``stretch_tile_fallback``).
    그 뒤 G1: frame 전체 4-이웃 타일 쌍의 offset 체비쇼프 차가 한도(같은 unit s_max, 다른 unit cheb(u_a − u_b) + s_max; u = 적용 unit offset)를
    넘으면 stretch 로 적용된 쪽을 unit offset 으로 대체(``stretch_step_exceeded``)하고 위반이 없을 때까지 반복한다.
    대체된 타일은 unit offset 으로 고정되어 반드시 끝난다.
    반환 ``{'tiles': [기록], 'field': [(y0, y1, x0, x1, dx, dy)], 'step_replaced_tiles': n, 's_max': s_max, 'radius_px': R}``.
    기록 키: unit_index_xy, tile_index_xy, region_px, start_offset_xy_px, offset_xy_px, delta_xy_px(unit 대비),
    delta_from_global_xy_px(global_offset 을 줄 때), candidate_offset_xy_px, offset_source(stretch|unit|global), applied, omega_pixels
    와 choose_dice_first 요약."""
    r = int(radius)
    if not 1 <= r <= MAX_STRETCH_RADIUS_PX:
        raise ValueError(f'radius {radius!r}: 1..{MAX_STRETCH_RADIUS_PX} 이어야 합니다')
    s_max = int(s_max)
    if s_max < 0:
        raise ValueError(f's_max {s_max!r}: 0 이상이어야 합니다')
    tiles = list(tiles)
    sources = dict(unit_sources or {})
    keys = sorted(layers, key=_natural_key)
    ref_valid = np.asarray(ref_valid, dtype=bool)
    tgt_valid = np.asarray(tgt_valid, dtype=bool)
    max_tile = (max((t['region'][1] - t['region'][0] for t in tiles), default=0),
                max((t['region'][3] - t['region'][2] for t in tiles), default=0))
    need = estimate_stretch_bytes(max_tile, r, len(keys), n_tiles=len(tiles), frame_shape=ref_valid.shape,
                                  n_starts=len({tuple(int(v) for v in o) for o in unit_offsets.values()}))
    from .change_map import _check_admission
    _check_admission(need, max_operation_bytes, reserved_bytes)       # 큰 배열을 할당하기 전에 거부
    omegas = {}
    records = []
    for t in tiles:
        q = tuple(t['unit_index'])
        s = (int(unit_offsets[q][0]), int(unit_offsets[q][1]))
        if s not in omegas:
            omegas[s] = fixed_omega(ref_valid, tgt_valid, s, r, r if erode is None else int(erode))
        omega = omegas[s]
        region = tuple(int(v) for v in t['region'])
        scores = {}
        for key in keys:
            ref_array, tgt_array = layers[key]
            a, inter, b = region_window_scores(ref_array, tgt_array, omega, region, s, r)
            scores[key] = {'a': a, 'inter': inter, 'b': b}
        res = choose_dice_first(scores, s, r, min_dice=min_dice, min_support=min_support, epsilon=epsilon, excluded=excluded)
        scores = None
        y0, y1, x0, x1 = region
        applied = bool(res['apply'])
        rec = {k: res[k] for k in _TILE_KEYS}
        rec['flags'] = list(res['flags'])
        rec.update({'unit_index_xy': [q[1], q[0]], 'tile_index_xy': [t['tile_index'][1], t['tile_index'][0]],
                    'region_px': [y0, y1, x0, x1], 'start_offset_xy_px': [s[0], s[1]],
                    'candidate_offset_xy_px': res['offset_xy_px'], 'applied': applied,
                    'omega_pixels': int(np.count_nonzero(omega[y0:y1, x0:x1])), '_unit': q, '_ty': t['tile_index'][0],
                    '_tx': t['tile_index'][1], '_s': s,
                    '_offset': (int(res['offset_xy_px'][0]), int(res['offset_xy_px'][1])) if applied else s})
        if not applied:
            rec['flags'].append('stretch_tile_fallback')
        records.append(rec)
    # G1: frame 전체 4-이웃 타일 쌍(unit 경계 쌍 포함). 한도 = (다른 unit 이면 cheb(u_a − u_b)) + s_max
    by_x0, by_y0 = {}, {}
    for i, rec in enumerate(records):
        y0, y1, x0, x1 = rec['region_px']
        by_x0.setdefault(x0, []).append(i)
        by_y0.setdefault(y0, []).append(i)
    pairs = []
    for i, rec in enumerate(records):
        y0, y1, x0, x1 = rec['region_px']
        for j in by_x0.get(x1, ()):
            if records[j]['region_px'][0] < y1 and y0 < records[j]['region_px'][1]:
                pairs.append((i, j))
        for j in by_y0.get(y1, ()):
            if records[j]['region_px'][2] < x1 and x0 < records[j]['region_px'][3]:
                pairs.append((i, j))
    limits = {}
    for i, j in pairs:
        a, b = records[i], records[j]
        limits[(i, j)] = s_max + (0 if a['_unit'] == b['_unit'] else _cheb_xy(a['_s'], b['_s']))
    replaced = 0
    while True:
        bad = set()
        for i, j in pairs:
            if _cheb_xy(records[i]['_offset'], records[j]['_offset']) > limits[(i, j)]:
                bad.update(k for k in (i, j) if records[k]['applied'])
        if not bad:
            break
        for k in bad:
            rec = records[k]
            rec['applied'] = False
            rec['_offset'] = rec['_s']
            rec['flags'].append('stretch_step_exceeded')
            replaced += 1
    field = []
    for rec in records:
        o, s, q = rec['_offset'], rec['_s'], rec['_unit']
        rec['offset_xy_px'] = [o[0], o[1]]
        rec['delta_xy_px'] = [o[0] - s[0], o[1] - s[1]]
        rec['delta_from_global_xy_px'] = None if global_offset is None else [o[0] - int(global_offset[0]), o[1] - int(global_offset[1])]
        rec['offset_source'] = 'stretch' if rec['applied'] else sources.get(q, 'unit')
        y0, y1, x0, x1 = rec['region_px']
        field.append((y0, y1, x0, x1, o[0], o[1]))
        for k in ('_unit', '_ty', '_tx', '_s', '_offset'):
            del rec[k]
    return {'tiles': records, 'field': field, 'step_replaced_tiles': replaced, 's_max': s_max, 'radius_px': r}
