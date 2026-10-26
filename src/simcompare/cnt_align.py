"""CNT 정렬: 두 제품(reference / target)의 CNT 층 영상을 같은 격자 위에서 정수 픽셀 이동으로 맞춘다.

입력은 CNT 층과 valid 영역뿐이다(hotspot 은 쓰지 않는다). 부호는 ``p_target = p_reference + offset`` 이다.
점수는 두 valid 영역 전체 기준 pooled Dice 이며 분모가 offset 과 무관해서, 최대화는 교집합 수 최대화(=상관 peak)와 같다.
교집합 수는 zero-padding rfft2 상관으로 모든 |d| <= r 에 대해 한 번에 정확히 구한다(wrap 없음, 반올림 후 정수).
"""
from __future__ import annotations

import math
import numbers

import numpy as np

from .numeric_schema import NumericContractError

MIN_SUPPORT_FRACTION = 0.5
PEAK_SEPARATION_UM = 2.0
PEAK_AMBIGUOUS_RATIO = 1.2
PERIOD_MIN_FRACTION = 0.5
MAX_ALTERNATIVES = 5
MAX_PERIOD_VECTORS = 8
PLATEAU_MIN_EXTENT_PX = 2
MIN_SEARCH_PX = 2
SIGN = 'p_target = p_reference + offset'
QUADRANTS = ('top_left', 'top_right', 'bottom_left', 'bottom_right')

_FAIL_REASONS = {'no_usable_layers', 'no_overlap'}


def _fast_len(n):
    """n 이상인 가장 작은 2·3·5 합성수(FFT 가 빠른 크기)."""
    n = max(int(n), 1)
    while True:
        m = n
        for p in (2, 3, 5):
            while m % p == 0:
                m //= p
        if m == 1:
            return n
        n += 1


def _search_px(search_um, pitch_um):
    return max(int(math.floor(float(search_um) / float(pitch_um) + 1e-9)), 0)


def _fft_size(ref_len, tgt_len, search_px):
    """wrap 이 없으려면 N >= max(길이) + r (+1 여유), 창 색인이 겹치지 않으려면 N >= 2r + 1."""
    return _fast_len(max(max(ref_len, tgt_len) + search_px + 1, 2 * search_px + 1))


def estimate_alignment_bytes(ref_shape, tgt_shape, search_px, n_layers):
    """동시에 보유하는 배열의 바이트 수(할당 전 admission 용).

    N_y, N_x = 축별 FFT 크기, R = N_y*N_x*8 (패딩 float64), C = N_y*(N_x//2+1)*16 (rfft complex128),
    W = (2r+1)^2, L = n_layers.
        bool 입력        = 2 * (L+1) * (H_ref*W_ref + H_tgt*W_tgt)      (층 ∧ valid 사본 포함)
        4분면 bool 임시  = (L+2) * H_ref*W_ref                           (분면 마스크, valid ∧ 분면, 층별 ∧ 분면)
        패딩 실수 배열   = 2 * R                                         (패딩 입력 + irfft 결과)
        보관 스펙트럼    = (2*L + 2) * C                                 (ref·tgt 층 L 쌍 + valid 2장)
        작업 복소 배열   = 4 * C                                         (곱, 누적 상관, 지지 상관, 자기상관/분면)
        상관 창(int64+bool) = (L + 6) * W * 8
    """
    ny = _fft_size(ref_shape[0], tgt_shape[0], search_px)
    nx = _fft_size(ref_shape[1], tgt_shape[1], search_px)
    layers = max(int(n_layers), 1)
    real = ny * nx * 8
    cplx = ny * (nx // 2 + 1) * 16
    window = (2 * search_px + 1) ** 2
    boolean = 2 * (layers + 1) * (ref_shape[0] * ref_shape[1] + tgt_shape[0] * tgt_shape[1])
    boolean += (layers + 2) * ref_shape[0] * ref_shape[1]
    return int(boolean + 2 * real + (2 * layers + 2) * cplx + 4 * cplx + (layers + 6) * window * 8)


def _spectrum(arr, size):
    return np.fft.rfft2(arr.astype(np.float64), s=size)


def _lags(spec_a, spec_b, size, r):
    """I(d) = sum_p a[p] * b[p + d] for |dy|,|dx| <= r as int64 window [dy + r, dx + r]."""
    corr = np.fft.irfft2(np.conj(spec_a) * spec_b, s=size)
    return _window(corr, size, r)


def _window(corr, size, r):
    ys = np.arange(-r, r + 1) % size[0]
    xs = np.arange(-r, r + 1) % size[1]
    return np.rint(corr[np.ix_(ys, xs)]).astype(np.int64)


def _grid(r):
    axis = np.arange(-r, r + 1)
    dy, dx = np.meshgrid(axis, axis, indexing='ij')
    return dy, dx, dy * dy + dx * dx


def _pick_best(values, cand, dy, dx, d2):
    """Max value, then smallest |d|, then (dy, dx) lexicographic. Returns (dy, dx) or None."""
    if not cand.any():
        return None
    top = values[cand].max()
    sel = cand & (values == top)
    iy, ix = np.nonzero(sel)
    order = np.lexsort((dx[iy, ix], dy[iy, ix], d2[iy, ix]))
    k = order[0]
    return int(dy[iy[k], ix[k]]), int(dx[iy[k], ix[k]])


def _pick_best_prefer(values, cand, dy, dx, d2, prefer, r):
    """_pick_best, but the global best ``prefer`` = (dy, dx) wins when it also reaches the maximum (periodic ties)."""
    if not cand.any():
        return None
    if cand[prefer[0] + r, prefer[1] + r] and values[prefer[0] + r, prefer[1] + r] == values[cand].max():
        return prefer
    return _pick_best(values, cand, dy, dx, d2)


def _edge_mask(circle):
    """Points of the search circle that have a neighbour outside it (their local maximum is cut by the circle)."""
    padded = np.pad(circle, 1, constant_values=False)
    h, w = circle.shape
    outside = np.zeros(circle.shape, dtype=bool)
    for oy in (0, 1, 2):
        for ox in (0, 1, 2):
            if oy == 1 and ox == 1:
                continue
            outside |= ~padded[oy:oy + h, ox:ox + w]
    return circle & outside


def _component(mask, seed):
    """``seed``(mask 안의 한 칸)를 포함하는 8-연결 성분의 bool 배열."""
    out = np.zeros(mask.shape, dtype=bool)
    out[seed] = True
    stack = [seed]
    height, width = mask.shape
    while stack:
        y, x = stack.pop()
        for ny in range(max(y - 1, 0), min(y + 2, height)):
            for nx in range(max(x - 1, 0), min(x + 2, width)):
                if mask[ny, nx] and not out[ny, nx]:
                    out[ny, nx] = True
                    stack.append((ny, nx))
    return out


def _local_max(values, cand):
    work = np.where(cand, values, -1)
    padded = np.pad(work, 1, constant_values=-1)
    h, w = work.shape
    is_max = cand.copy()
    for oy in (0, 1, 2):
        for ox in (0, 1, 2):
            if oy == 1 and ox == 1:
                continue
            is_max &= work >= padded[oy:oy + h, ox:ox + w]
    return is_max


def _r6(value):
    return None if value is None else round(float(value), 6)


def _um(dy, dx, pitch):
    return [_r6(dx * pitch), _r6(dy * pitch)]


def _empty_result(pitch_um, search_um, search_px):
    return {
        'status': 'alignment_failed_identity_used', 'reasons': [], 'sign': SIGN,
        'offset_xy_um': [0.0, 0.0], 'offset_xy_px': [0, 0],
        'dice_best': None, 'dice_identity': None, 'peak_ratio': None,
        'best_plateau_px': None, 'best_plateau_extent_px': None,
        'alternatives': [], 'pattern_period_vectors_um': [], 'per_layer': {},
        'quadrant_offsets_um': {}, 'layers_used': [], 'layers_excluded': [],
        'overlap_area_um2': None, 'search_um': _r6(search_um), 'search_px': search_px,
        'constants': {'MIN_SUPPORT_FRACTION': MIN_SUPPORT_FRACTION, 'PEAK_SEPARATION_UM': PEAK_SEPARATION_UM,
                      'PEAK_AMBIGUOUS_RATIO': PEAK_AMBIGUOUS_RATIO, 'PERIOD_MIN_FRACTION': PERIOD_MIN_FRACTION,
                      'PLATEAU_MIN_EXTENT_PX': PLATEAU_MIN_EXTENT_PX},
        'estimated_bytes': 0,
    }


def _finish(result, reasons):
    result['reasons'] = sorted(set(reasons))
    if any(r in _FAIL_REASONS or r.startswith('cnt_missing_') for r in result['reasons']):
        result['status'] = 'alignment_failed_identity_used'
        result['offset_xy_um'], result['offset_xy_px'] = [0.0, 0.0], [0, 0]
    elif result['reasons']:
        result['status'] = 'aligned_uncertain'
    else:
        result['status'] = 'aligned'
    return result


def align_products(ref, tgt, *, pitch_um, search_um, min_dice, layer_tolerance_um, max_operation_bytes):
    """Align ``tgt`` to ``ref``. Failure is a result (identity offset), never an exception; admission and invalid input raise."""
    if not isinstance(pitch_um, numbers.Real) or not math.isfinite(pitch_um) or pitch_um <= 0:
        raise NumericContractError('invalid_alignment_input', f'pitch_um must be positive, got {pitch_um!r}')
    if not isinstance(search_um, numbers.Real) or not math.isfinite(search_um) or search_um < 0:
        raise NumericContractError('invalid_alignment_input', f'search_um must be finite and >= 0, got {search_um!r}')
    search_px = _search_px(search_um, pitch_um)
    if search_px < MIN_SEARCH_PX:
        raise NumericContractError(
            'invalid_alignment_input',
            f'search radius {search_px} px < {MIN_SEARCH_PX} px (search_um={search_um}, pitch_um={pitch_um}); raise --search-um')
    result = _empty_result(pitch_um, search_um, search_px)
    reasons = []
    ref_layers, tgt_layers = dict(ref.get('layers') or {}), dict(tgt.get('layers') or {})
    if not ref_layers:
        reasons.append('cnt_missing_reference')
    if not tgt_layers:
        reasons.append('cnt_missing_target')
    if reasons:   # 층이 없는 입력은 valid 모양을 검사하기 전에 결과로 돌려준다(실패는 예외가 아니라 결과)
        return _finish(result, reasons)
    for side, product in (('reference', ref), ('target', tgt)):
        valid_shape = np.shape(product['valid'])
        if len(valid_shape) != 2:
            raise NumericContractError('invalid_alignment_input', f'{side} valid must be 2-D, got shape {valid_shape}')
        for name, layer in (product.get('layers') or {}).items():
            if np.shape(layer) != valid_shape:
                raise NumericContractError(
                    'invalid_alignment_input',
                    f'{side} layer {name} shape {np.shape(layer)} != valid shape {valid_shape}')

    valid_ref, valid_tgt = np.asarray(ref['valid'], dtype=bool), np.asarray(tgt['valid'], dtype=bool)
    excluded, used, a_masks, b_masks = [], [], {}, {}
    for name in sorted(set(ref_layers) | set(tgt_layers)):
        if name not in ref_layers or name not in tgt_layers:
            excluded.append({'layer': name, 'reason': 'cnt_layer_set_mismatch'})
            reasons.append('cnt_layer_set_mismatch')
            continue
        a, b = np.asarray(ref_layers[name], dtype=bool) & valid_ref, np.asarray(tgt_layers[name], dtype=bool) & valid_tgt
        empty = [side for side, m in (('reference', a), ('target', b)) if not m.any()]
        if empty:
            for side in empty:
                excluded.append({'layer': name, 'reason': f'cnt_empty_{side}_{name}'})
                reasons.append(f'cnt_empty_{side}_{name}')
            continue
        used.append(name)
        a_masks[name], b_masks[name] = a, b
    result['layers_used'], result['layers_excluded'] = used, excluded
    if not used:
        reasons.append('no_usable_layers')
        return _finish(result, reasons)

    need = estimate_alignment_bytes(valid_ref.shape, valid_tgt.shape, search_px, len(used))
    result['estimated_bytes'] = need
    if need > max_operation_bytes:
        raise NumericContractError(
            'oversized_real_operation',
            f'{need} > {max_operation_bytes}; lower --search-um, align a smaller frame, or raise the operation limit')

    r = search_px
    size = (_fft_size(valid_ref.shape[0], valid_tgt.shape[0], r), _fft_size(valid_ref.shape[1], valid_tgt.shape[1], r))
    dy, dx, d2 = _grid(r)
    circle = d2 <= r * r

    spec_vr, spec_vt = _spectrum(valid_ref, size), _spectrum(valid_tgt, size)
    support = _lags(spec_vr, spec_vt, size, r)
    n_vr, n_vt = int(valid_ref.sum()), int(valid_tgt.sum())
    cand = circle & (support >= MIN_SUPPORT_FRACTION * min(n_vr, n_vt))
    if not cand.any():
        reasons.append('no_overlap')
        return _finish(result, reasons)

    spec_a = {n: _spectrum(a_masks[n], size) for n in used}
    spec_b = {n: _spectrum(b_masks[n], size) for n in used}
    area_a = {n: int(a_masks[n].sum()) for n in used}
    area_b = {n: int(b_masks[n].sum()) for n in used}
    denom = sum(area_a.values()) + sum(area_b.values())

    summed = sum(np.conj(spec_a[n]) * spec_b[n] for n in used)
    inter = _window(np.fft.irfft2(summed, s=size), size, r)

    best = _pick_best(inter, cand, dy, dx, d2)
    by, bx = best
    best_i = int(inter[by + r, bx + r])
    dice_best = 2.0 * best_i / denom
    result['offset_xy_px'], result['offset_xy_um'] = [bx, by], _um(by, bx, pitch_um)
    result['dice_best'] = _r6(dice_best)
    result['dice_identity'] = _r6(2.0 * int(inter[r, r]) / denom)
    result['overlap_area_um2'] = _r6(int(support[by + r, bx + r]) * pitch_um * pitch_um)

    # 최댓값 동률 집합: 크기와 퍼짐(px). 주기 동률·점 크기 차이로 생기는 plateau 를 드러낸다.
    # best 를 포함하는 8-연결 성분만 본다(주기 때문에 멀리 떨어진 동률은 plateau 가 아니다).
    tied = cand & (inter == best_i)
    component = _component(tied, (by + r, bx + r))
    ty, tx = dy[component], dx[component]
    extent = [int(tx.max() - tx.min()), int(ty.max() - ty.min())]
    result['best_plateau_px'] = int(component.sum())
    result['best_plateau_extent_px'] = extent
    if max(extent) >= PLATEAU_MIN_EXTENT_PX:
        reasons.append('peak_plateau')

    # 별개 peak: 3x3 국소 최대, 교집합 > 0, 최적에서 PEAK_SEPARATION_UM 이상 떨어진 것.
    # 탐색 원 가장자리 점은 원에 잘린 국소 최대일 수 있어 지우지 않고 at_search_edge 로 표시한다(참 offset 이 사라지지 않게).
    edge = _edge_mask(circle)
    peak_mask = _local_max(inter, cand)
    far = np.hypot(dy - by, dx - bx) * pitch_um >= PEAK_SEPARATION_UM - 1e-9
    iy, ix = np.nonzero(peak_mask & far & (inter > 0))
    order = np.lexsort((dx[iy, ix], dy[iy, ix], d2[iy, ix], -inter[iy, ix]))[:MAX_ALTERNATIVES]
    result['alternatives'] = [
        {'offset_xy_um': _um(int(dy[iy[k], ix[k]]), int(dx[iy[k], ix[k]]), pitch_um),
         'offset_xy_px': [int(dx[iy[k], ix[k]]), int(dy[iy[k], ix[k]])],
         'dice': _r6(2.0 * int(inter[iy[k], ix[k]]) / denom),
         'at_search_edge': bool(edge[iy[k], ix[k]])} for k in order]
    if len(order):
        second_i = int(inter[iy[order[0]], ix[order[0]]])
        ratio = best_i / second_i
        result['peak_ratio'] = _r6(ratio)
        if ratio < PEAK_AMBIGUOUS_RATIO:
            reasons.append('peak_ambiguous')

    # 주기: 층 합산 자기상관의 국소 최대
    auto = _window(np.fft.irfft2(sum(np.abs(spec_a[n]) ** 2 for n in used), s=size), size, r)
    zero_lag = int(auto[r, r])
    sep = (np.hypot(dy, dx) * pitch_um >= PEAK_SEPARATION_UM - 1e-9) & circle
    period_cand = sep & (auto >= PERIOD_MIN_FRACTION * zero_lag)
    pm = _local_max(auto, circle) & period_cand
    iy, ix = np.nonzero(pm)
    order = np.lexsort((dx[iy, ix], dy[iy, ix], d2[iy, ix], -auto[iy, ix]))[:MAX_PERIOD_VECTORS]
    result['pattern_period_vectors_um'] = [
        {'vector_xy_um': _um(int(dy[iy[k], ix[k]]), int(dx[iy[k], ix[k]]), pitch_um),
         'autocorr_fraction': _r6(int(auto[iy[k], ix[k]]) / zero_lag) if zero_lag else None,
         'at_search_edge': bool(edge[iy[k], ix[k]])} for k in order]

    # 층별 진단
    per_layer, disagree = {}, False
    for n in used:
        win = _lags(spec_a[n], spec_b[n], size, r)
        own = _pick_best_prefer(win, cand, dy, dx, d2, (by, bx), r)
        den_l = area_a[n] + area_b[n]
        per_layer[n] = {
            'offset_xy_um': _um(own[0], own[1], pitch_um),
            'dice_at_best': _r6(2.0 * int(win[by + r, bx + r]) / den_l),
            'dice_at_own_best': _r6(2.0 * int(win[own[0] + r, own[1] + r]) / den_l)}
        if math.hypot(own[0] - by, own[1] - bx) * pitch_um > layer_tolerance_um + 1e-9:
            disagree = True
    result['per_layer'] = per_layer
    if disagree:
        reasons.append('layer_offsets_disagree')

    # 4분면 진단
    rows, cols = np.nonzero(valid_ref.any(axis=1))[0], np.nonzero(valid_ref.any(axis=0))[0]
    y0, y1, x0, x1 = int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
    ym, xm = (y0 + y1) // 2, (x0 + x1) // 2
    bounds = {'top_left': (y0, ym, x0, xm), 'top_right': (y0, ym, xm, x1),
              'bottom_left': (ym, y1, x0, xm), 'bottom_right': (ym, y1, xm, x1)}
    quadrants, nonuniform = {}, False
    for name in QUADRANTS:
        qy0, qy1, qx0, qx1 = bounds[name]
        quad = np.zeros(valid_ref.shape, dtype=bool)
        quad[qy0:qy1, qx0:qx1] = True
        vq = valid_ref & quad
        n_vq = int(vq.sum())
        aq = {n: a_masks[n] & quad for n in used}
        if n_vq == 0 or not any(m.any() for m in aq.values()):
            quadrants[name] = None
            continue
        sup_q = _lags(_spectrum(vq, size), spec_vt, size, r)
        cand_q = circle & (sup_q >= MIN_SUPPORT_FRACTION * min(n_vq, n_vt))
        acc = sum(np.conj(_spectrum(aq[n], size)) * spec_b[n] for n in used if aq[n].any())
        win_q = _window(np.fft.irfft2(acc, s=size), size, r)
        if not cand_q[by + r, bx + r]:   # 전역 최적에서 분면이 target 에 충분히 겹치지 않으면 진단 불가
            quadrants[name] = None
            continue
        best_q = _pick_best_prefer(win_q, cand_q, dy, dx, d2, (by, bx), r)
        quadrants[name] = _um(best_q[0], best_q[1], pitch_um)
        if math.hypot(best_q[0] - by, best_q[1] - bx) * pitch_um > layer_tolerance_um + 1e-9:
            nonuniform = True
    result['quadrant_offsets_um'] = quadrants
    if nonuniform:
        reasons.append('residual_nonuniform')

    if dice_best < min_dice:
        reasons.append('dice_below_min')
    if math.hypot(by, bx) > r - 1:
        reasons.append('search_bound_hit')
    return _finish(result, reasons)
