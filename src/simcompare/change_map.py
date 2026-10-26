"""변경 지도: 두 제품(reference / target)의 같은 case 쌍에서 5 µm hotspot mask 의 차이를 모아 변경 위치를 찾는다.

계산만 한다(CLI·출력 파일·HTML 은 이 모듈 밖). 좌표는 T4 정렬과 같다: reference 픽셀 p 에 target 픽셀 ``p + offset`` 이
대응한다(``offset_px = (dx, dy)``, ``cnt_align`` 결과의 ``offset_xy_px``). target mask 를 reference 좌표로 옮긴 것이 B'이다.

흐름
- ``pair_cases``: 두 dataset 에서 결합 키로 case 쌍을 만든다(mask 는 읽지 않는다. 헤더만 본다).
- ``change_maps``: 쌍마다 mask 를 한 장씩 읽어 잔차 R 을 analysis_layer 별 ``uint32`` 지도에 더한다.
- ``window_candidates``: 층별 적분 영상으로 모든 W×W 창 합을 구하고 8-이웃 극대 창을 후보로 만든다.
- ``case_contributions``: 선정된 창에 대해 case 를 한 번 더 돌며 기여 case 와 변경 종류를 구한다.
분류(shifted/reshaped/…)는 설명용이며 선정·점수에 쓰지 않는다.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import math
import numbers

import numpy as np

from .cnt_align import _fft_size, _grid, _lags, _pick_best, _spectrum
from .numeric_schema import NumericContractError, diameter_key

CLASS_IOU_DELTA = 0.2
CLASS_IOU_MIN = 0.2
COUNTER_LIMIT = 2 ** 32
INTEGRAL_LIMIT = 2 ** 64
CANDIDATE_BYTES = 1024          # 후보 dict 하나(파이썬 객체)의 보수적 추정
FLAG_PARTIAL = 'window_partially_unobserved'
CLASSES = ('shifted', 'reshaped', 'appeared', 'disappeared', 'mixed_undetermined')

_INVALID = 'invalid_change_map_input'


def _bad(detail):
    return NumericContractError(_INVALID, detail)


# --------------------------------------------------------------------------- A1. 쌍 만들기

class CasePair:
    """한 case 의 reference·target 대표 record. mask 는 ``load()`` 에서만 읽는다(캐시하지 않는다).

    ``flags`` = 두 대표 record 헤더 reasons(예 ``csv_area_mismatch``)를 ``reference:``/``target:`` 접두로 모은 정렬 튜플.
    쌍을 제외하지 않고 기여 case 에 표시만 한다."""
    __slots__ = ('case_key', 'layer', 'ref_case', 'tgt_case', 'ref_diameter', 'tgt_diameter', 'flags')

    def __init__(self, case_key, layer, ref_case, tgt_case, ref_diameter, tgt_diameter, flags=()):
        self.case_key, self.layer = case_key, layer
        self.ref_case, self.tgt_case = ref_case, tgt_case
        self.ref_diameter, self.tgt_diameter = ref_diameter, tgt_diameter
        self.flags = tuple(flags)

    def load(self):
        a = self.ref_case.records[self.ref_diameter].mask
        b = self.tgt_case.records[self.tgt_diameter].mask
        if a is None or b is None:
            raise NumericContractError('change_map_mask_unavailable', self.case_key)
        return a, b


class MaskPair:
    """이미 메모리에 있는 mask 쌍(합성 입력·테스트용). ``CasePair`` 와 같은 인터페이스."""
    __slots__ = ('case_key', 'layer', 'a', 'b', 'flags')

    def __init__(self, case_key, layer, a, b, flags=()):
        self.case_key, self.layer, self.a, self.b = case_key, layer, a, b
        self.flags = tuple(flags)

    def load(self):
        return self.a, self.b


def _endpoints(case):
    return tuple(sorted((e.electrode_id, e.actual_layer) for e in case.endpoints))


def _key_text(failure_type, endpoints, layer=None):
    fields = [failure_type] + [part for endpoint in endpoints for part in endpoint]
    if layer is not None:
        fields.append(layer)
    return '|'.join(fields)


def _header(records, diameter):
    header = getattr(records, 'header', None)
    return header(diameter) if header is not None else records[diameter]


def _observation(case, diameter_um):
    """(대표 지름 key, 제외 사유 또는 None, 헤더 reasons). 누락(missing)과 관측 면적 0(observed)을 구분한다."""
    wanted = diameter_key(diameter_um)
    found = next((d for d in case.records if diameter_key(d) == wanted), None)
    if found is None:
        return None, 'representative_diameter_missing', ()
    head = _header(case.records, found)
    reasons = tuple(head.reasons)
    if head.input_status == 'observed':
        return found, None, reasons
    if head.input_status == 'not_applicable':
        return found, 'not_applicable', reasons
    if 'case_geometry_unconfirmed' in head.reasons:
        return found, 'geometry_unconfirmed', reasons
    return found, 'mask_missing', reasons


def _product(dataset):
    ids = sorted({case.identity.product_id for case in dataset.cases})
    return ids[0] if len(ids) == 1 else ','.join(ids)


SHORT_FAILURE_TYPES = ('lateral_short', 'vertical_short')   # electrode_map 의 short-only 관례(G7b)
CONTEXT_FIELDS = ('process_step_id', 'exposure_id', 'unit_id', 'source_contract_version')


def _group(dataset):
    groups = {}
    for case in dataset.cases:
        groups.setdefault((case.failure_type, _endpoints(case)), {}).setdefault(case.analysis_layer, []).append(case)
    return groups


def _context(dataset):
    out = {}
    for field in CONTEXT_FIELDS:
        values = {getattr(case.identity, field, None) for case in dataset.cases}
        out[field] = sorted(v for v in values if v is not None)
    return out


def pair_cases(ref_dataset, tgt_dataset, *, diameter_um):
    """결합 키 = (불량명, 정렬된 (전극, 층) 두 개) 로 두 dataset 의 case 를 짝짓는다. product_id·design_version 등은 키가 아니다.

    같은 키인데 analysis_layer 가 다르면 ``case_layer_mismatch``(공통 layer 가 하나도 없을 때), 한쪽에만 있으면
    ``cases_only_in_*``. 같은 (키, analysis_layer) 가 한 dataset 에 둘 이상이면 ``duplicate_case_key`` 로 둘 다 제외한다.
    양쪽 대표 지름 record 가 관측(면적 0 포함)일 때만 쌍을 만든다. mask 는 읽지 않는다.
    한쪽에 중복이 있으면 반대편의 단일 case 도 ``duplicate_case_key_on_other_side`` 로 제외 목록에 남긴다(둘 다 쌍에서 빠진다).
    lateral_short·vertical_short 가 아닌 case 는 ``map_requires_short_only`` 로 제외한다(electrode_map 관례).
    두 dataset 의 process_step_id·exposure_id·unit_id·source_contract_version 은 ``stats['dataset_context']`` 에 적고
    다르면 ``warnings`` 에 ``change_map_dataset_context_differs`` 를 넣는다.
    반환: ``{'pairs': [CasePair], 'cases_excluded': [{case_key, side, product, reason}], 'cases_only_in_reference': [...],
    'cases_only_in_target': [...], 'warnings': [...], 'stats': {...}}``(목록은 case_key 순 정렬).
    """
    ref_groups, tgt_groups = _group(ref_dataset), _group(tgt_dataset)
    ref_product, tgt_product = _product(ref_dataset), _product(tgt_dataset)
    pairs, excluded, only_ref, only_tgt = [], [], [], []

    def exclude(case_key, side, reason):
        excluded.append({'case_key': case_key, 'side': side,
                         'product': ref_product if side == 'reference' else tgt_product, 'reason': reason})

    def only(target, case_key, product):
        target.append({'case_key': case_key, 'product': product})

    for key in sorted(set(ref_groups) | set(tgt_groups)):
        failure_type, endpoints = key
        ref_layers, tgt_layers = ref_groups.get(key, {}), tgt_groups.get(key, {})
        if failure_type not in SHORT_FAILURE_TYPES:
            for side, layers in (('reference', ref_layers), ('target', tgt_layers)):
                for layer in sorted(layers):
                    for _ in layers[layer]:
                        exclude(_key_text(failure_type, endpoints, layer), side, 'map_requires_short_only')
            continue
        if not ref_layers:
            for layer in sorted(tgt_layers):
                for _ in tgt_layers[layer]:
                    only(only_tgt, _key_text(failure_type, endpoints, layer), tgt_product)
            continue
        if not tgt_layers:
            for layer in sorted(ref_layers):
                for _ in ref_layers[layer]:
                    only(only_ref, _key_text(failure_type, endpoints, layer), ref_product)
            continue
        common = sorted(set(ref_layers) & set(tgt_layers))
        for layer in sorted(set(ref_layers) | set(tgt_layers)):
            text = _key_text(failure_type, endpoints, layer)
            in_ref, in_tgt = ref_layers.get(layer, []), tgt_layers.get(layer, [])
            if layer in common:
                if len(in_ref) > 1 or len(in_tgt) > 1:
                    for side, mine, other in (('reference', in_ref, in_tgt), ('target', in_tgt, in_ref)):
                        if len(mine) > 1:
                            exclude(text, side, 'duplicate_case_key')
                        elif len(other) > 1:
                            exclude(text, side, 'duplicate_case_key_on_other_side')
                    continue
                ref_d, ref_reason, ref_head = _observation(in_ref[0], diameter_um)
                tgt_d, tgt_reason, tgt_head = _observation(in_tgt[0], diameter_um)
                if ref_reason:
                    exclude(text, 'reference', ref_reason)
                if tgt_reason:
                    exclude(text, 'target', tgt_reason)
                if not ref_reason and not tgt_reason:
                    flags = sorted({f'reference:{r}' for r in ref_head} | {f'target:{r}' for r in tgt_head})
                    pairs.append(CasePair(text, layer, in_ref[0], in_tgt[0], ref_d, tgt_d, flags))
            elif not common:
                if in_ref:
                    exclude(text, 'reference', 'case_layer_mismatch')
                if in_tgt:
                    exclude(text, 'target', 'case_layer_mismatch')
            else:
                if in_ref:
                    only(only_ref, text, ref_product)
                if in_tgt:
                    only(only_tgt, text, tgt_product)
    pairs.sort(key=lambda p: p.case_key)
    excluded.sort(key=lambda e: (e['case_key'], e['side'], e['reason']))
    only_ref.sort(key=lambda e: e['case_key'])
    only_tgt.sort(key=lambda e: e['case_key'])
    reasons = {}
    for item in excluded:
        reasons[item['reason']] = reasons.get(item['reason'], 0) + 1
    context = {'reference': _context(ref_dataset), 'target': _context(tgt_dataset)}
    warnings = ['change_map_dataset_context_differs'] if context['reference'] != context['target'] else []
    return {'pairs': pairs, 'cases_excluded': excluded, 'cases_only_in_reference': only_ref,
            'cases_only_in_target': only_tgt, 'warnings': warnings,
            'stats': {'pairs': len(pairs), 'excluded_entries': len(excluded), 'excluded_by_reason': reasons,
                      'only_in_reference': len(only_ref), 'only_in_target': len(only_tgt),
                      'diameter_um': float(diameter_um), 'dataset_context': context}}


# --------------------------------------------------------------------------- A5/A6. admission·검증

def estimate_change_map_bytes(ref_shape, tgt_shape, n_layers, window_px, tolerance_px):
    """동시에 보유하는 배열의 바이트 수(할당 전 admission 용). 두 단계 중 큰 쪽이다.

    hr = H_ref*W_ref, ht = H_tgt*W_tgt, L = n_layers, w = window_px, r = tolerance_px.
    1단계(change_maps, case 한 장씩):
        valid 2장 + 이동한 target valid + D            = 3*hr + ht                  (bool)
        현재 case: target 원본 + A + B' + R             = ht + 3*hr                  (bool)
        팽창(r > 0): 팽창 2장 + 단계 임시 2장            = 4*hr                       (bool)
        층별 uint32 지도                                = 4*L*hr
    2단계(window_candidates, w > 0 일 때): ny = H_ref-w+1, nx = W_ref-w+1, nwin = ny*nx, stride = max(1, w//4),
        ng = ceil(ny/stride)*ceil(nx/stride)
        지도 L장 + D                                    = 4*L*hr + hr
        적분 uint64 1장                                 = (H_ref+1)*(W_ref+1)*8
        창 합 + 덧셈 임시 2장 + 관측 수 1장              = 4*nwin*8
        격자 복사·패딩·bool 임시                         = 32*ng
        후보 버퍼(층마다 최대 ng 개)                     = CANDIDATE_BYTES*ng*L
    """
    hr = int(ref_shape[0]) * int(ref_shape[1])
    ht = int(tgt_shape[0]) * int(tgt_shape[1])
    layers = max(int(n_layers), 0)
    dilation = 4 * hr if tolerance_px > 0 else 0
    first = 3 * hr + ht + ht + 3 * hr + dilation + 4 * layers * hr
    if int(window_px) <= 0:
        return int(first)
    w = int(window_px)
    ny, nx = max(int(ref_shape[0]) - w + 1, 0), max(int(ref_shape[1]) - w + 1, 0)
    stride = max(1, w // 4)
    grid = -(-ny // stride) * -(-nx // stride)
    second = (4 * layers * hr + hr + (int(ref_shape[0]) + 1) * (int(ref_shape[1]) + 1) * 8 + 4 * ny * nx * 8
              + 32 * grid + CANDIDATE_BYTES * grid * layers)
    return int(max(first, second))


def _check_admission(need, limit):
    if limit is not None and need > limit:
        raise NumericContractError(
            'oversized_real_operation',
            f'{need} > {limit}; use a smaller frame or window, lower --tolerance-um, or raise the operation limit')


def _round_px(value_um, pitch_um):
    return int(math.floor(float(value_um) / float(pitch_um) + 0.5 + 1e-9))


def _check_scalar(pitch_um, tolerance_um=None, window_um=None):
    if not isinstance(pitch_um, numbers.Real) or not math.isfinite(pitch_um) or pitch_um <= 0:
        raise _bad(f'pitch_um must be positive, got {pitch_um!r}')
    if tolerance_um is not None and (not isinstance(tolerance_um, numbers.Real) or not math.isfinite(tolerance_um)
                                     or tolerance_um < 0):
        raise _bad(f'tolerance_um must be finite and >= 0, got {tolerance_um!r}')
    if window_um is not None and (not isinstance(window_um, numbers.Real) or not math.isfinite(window_um)
                                  or window_um < pitch_um):
        raise _bad(f'window_um must be finite and >= pitch_um ({pitch_um}), got {window_um!r}')


def _valid_array(value, name):
    arr = np.asarray(value)
    if arr.ndim != 2 or arr.size == 0:
        raise _bad(f'{name} must be a non-empty 2-D array, got shape {arr.shape}')
    return arr if arr.dtype == bool else arr.astype(bool)


def _offset(offset_px):
    try:
        dx, dy = offset_px
    except (TypeError, ValueError):
        raise _bad(f'offset_px must be (dx, dy), got {offset_px!r}') from None
    for value in (dx, dy):
        if isinstance(value, bool) or not isinstance(value, numbers.Integral):
            raise _bad(f'offset_px must be integers, got {offset_px!r}')
    return int(dx), int(dy)


# --------------------------------------------------------------------------- A2. 잔차·지도

def _shift_crop(src, dy, dx, y0, y1, x0, x1):
    """out[y - y0, x - x0] = src[y + dy, x + dx] (y0<=y<y1, x0<=x<x1). src 밖은 False(관측 불가)."""
    out = np.zeros((y1 - y0, x1 - x0), dtype=bool)
    sy0, sy1 = max(y0 + dy, 0), min(y1 + dy, src.shape[0])
    sx0, sx1 = max(x0 + dx, 0), min(x1 + dx, src.shape[1])
    if sy0 < sy1 and sx0 < sx1:
        out[sy0 - dy - y0:sy1 - dy - y0, sx0 - dx - x0:sx1 - dx - x0] = src[sy0:sy1, sx0:sx1]
    return out


def _dilate(mask, r):
    """8-이웃 팽창 r 회(= 반지름 r 인 정사각 팽창; 가로 r 회 뒤 세로 r 회와 같다). 경계 밖은 빈 칸이다."""
    out = mask
    for _ in range(r):
        nxt = out.copy()
        nxt[:, 1:] |= out[:, :-1]
        nxt[:, :-1] |= out[:, 1:]
        out = nxt
    for _ in range(r):
        nxt = out.copy()
        nxt[1:] |= out[:-1]
        nxt[:-1] |= out[1:]
        out = nxt
    return out


def _erode(mask, r):
    """8-이웃 침식 r 회(반지름 r 인 정사각 침식). frame 밖은 관측 불가(False)로 본다."""
    out = mask
    for _ in range(r):
        nxt = out.copy()
        nxt[:, 1:] &= out[:, :-1]
        nxt[:, :-1] &= out[:, 1:]
        nxt[:, 0] = False
        nxt[:, -1] = False
        out = nxt
    for _ in range(r):
        nxt = out.copy()
        nxt[1:] &= out[:-1]
        nxt[:-1] &= out[1:]
        nxt[0] = False
        nxt[-1] = False
        out = nxt
    return out


def _residual(a, b, domain, r):
    """R = ((A \\ dilate(B', r)) ∪ (B' \\ dilate(A, r))) ∩ domain. r = 0 이면 (A XOR B') ∩ domain.

    ``domain`` 은 호출자가 ``erode(D, r)`` 로 넘긴다: D 경계 r px 띠에서는 상대 쪽 형상이 관측 밖이라 거짓 잔차가 생긴다."""
    if r == 0:
        res = a ^ b
    else:
        res = (a & ~_dilate(b, r)) | (b & ~_dilate(a, r))
    res &= domain
    return res


def _comparison_domain(ref_valid, tgt_valid, dy, dx):
    shifted = _shift_crop(tgt_valid, dy, dx, 0, ref_valid.shape[0], 0, ref_valid.shape[1])
    return ref_valid & shifted


def _extent(mask, shift):
    """mask(행 또는 열 any 결과)가 True 인 칸을 덮는 [시작, 끝) 구간에 ``shift`` 를 더한 값. 없으면 None."""
    index = np.flatnonzero(mask)
    return None if index.size == 0 else (int(index[0]) + shift, int(index[-1]) + 1 + shift)


def _pair_bbox(a, b, dy, dx, height, width, margin):
    """A ∪ B'(= target b 를 reference 좌표로 옮긴 것)의 bbox 를 ``margin`` 만큼 넓혀 frame 으로 자른 (y0, y1, x0, x1). 둘 다 비면 None."""
    boxes = []
    ya, xa = _extent(a.any(axis=1), 0), _extent(a.any(axis=0), 0)
    if ya is not None:
        boxes.append((ya[0], ya[1], xa[0], xa[1]))
    yb, xb = _extent(b.any(axis=1), -dy), _extent(b.any(axis=0), -dx)
    if yb is not None:
        y0, y1, x0, x1 = max(yb[0], 0), min(yb[1], height), max(xb[0], 0), min(xb[1], width)
        if y0 < y1 and x0 < x1:
            boxes.append((y0, y1, x0, x1))
    if not boxes:
        return None
    return (max(min(b[0] for b in boxes) - margin, 0), min(max(b[1] for b in boxes) + margin, height),
            max(min(b[2] for b in boxes) - margin, 0), min(max(b[3] for b in boxes) + margin, width))


def _load_checked(pair, ref_shape, tgt_shape):
    a, b = pair.load()
    a, b = np.asarray(a), np.asarray(b)
    if a.dtype != bool or b.dtype != bool:
        raise _bad(f'case {pair.case_key}: masks must be bool')
    if a.shape != ref_shape or b.shape != tgt_shape:
        raise _bad(f'case {pair.case_key}: mask shapes {a.shape}/{b.shape} != valid shapes {ref_shape}/{tgt_shape}')
    return a, b


def change_maps(pairs_iter, *, ref_valid, tgt_valid, offset_px, pitch_um, tolerance_um, max_operation_bytes,
                window_um=None):
    """analysis_layer 별 ``uint32`` 변경 지도(R 누적), 비교 영역 D, 통계를 돌려준다.

    pairs_iter: ``layer``·``case_key``·``load()`` 를 가진 쌍(``CasePair``/``MaskPair``)의 iterable. 쌍마다 한 번만 읽는다.
    r = round(tolerance_um / pitch_um) px(반올림은 .5 올림). 팽창은 frame 전체에서 하고 D 로 자른다.
    window_um 을 주면 admission 이 창 후보 단계(적분 영상)까지 포함해 검사한다. 한도를 넘으면 할당 전에
    ``oversized_real_operation``. 층의 쌍 수가 2^32 이상이면 ``change_map_counter_overflow``.
    반환: ``{'maps': {layer: uint32 지도}, 'D': bool, 'stats': {...}}``.
    """
    _check_scalar(pitch_um, tolerance_um, window_um)
    ref_valid, tgt_valid = _valid_array(ref_valid, 'ref_valid'), _valid_array(tgt_valid, 'tgt_valid')
    dx, dy = _offset(offset_px)
    pairs = list(pairs_iter)
    layers = sorted({pair.layer for pair in pairs})
    r = _round_px(tolerance_um, pitch_um)
    window_px = 0 if window_um is None else _round_px(window_um, pitch_um)
    need = estimate_change_map_bytes(ref_valid.shape, tgt_valid.shape, len(layers), window_px, r)
    _check_admission(need, max_operation_bytes)
    per_layer = {layer: 0 for layer in layers}
    for pair in pairs:
        per_layer[pair.layer] += 1
    for layer, count in per_layer.items():
        if count >= COUNTER_LIMIT:
            raise NumericContractError('change_map_counter_overflow', f'layer {layer}: {count} cases >= 2^32')
    full_domain = _comparison_domain(ref_valid, tgt_valid, dy, dx)
    domain_before = int(np.count_nonzero(full_domain))
    domain = _erode(full_domain, r)
    del full_domain
    maps = {layer: np.zeros(ref_valid.shape, dtype=np.uint32) for layer in layers}
    changed = {layer: 0 for layer in layers}
    residual_pixels = 0
    height, width = ref_valid.shape
    for pair in pairs:
        a, b = _load_checked(pair, ref_valid.shape, tgt_valid.shape)
        # R 는 A ∪ B' 위에서만 0 이 아니다: 그 bbox 를 r(+1) 넓힌 영역에서 계산하면 frame 전체 계산과 비트 단위로 같다.
        box = _pair_bbox(a, b, dy, dx, height, width, r + 1)
        if box is not None:
            y0, y1, x0, x1 = box
            res = _residual(a[y0:y1, x0:x1], _shift_crop(b, dy, dx, y0, y1, x0, x1), domain[y0:y1, x0:x1], r)
            count = int(np.count_nonzero(res))
            if count:
                target = maps[pair.layer][y0:y1, x0:x1]
                np.add(target, res, out=target, casting='unsafe')
                changed[pair.layer] += 1
                residual_pixels += count
            del res
        a = b = None   # 다음 쌍을 읽기 전에 이전 쌍 배열을 놓는다
    return {'maps': maps, 'D': domain,
            'stats': {'pairs': len(pairs), 'pairs_by_layer': per_layer, 'pairs_with_change_by_layer': changed,
                      'residual_pixels': residual_pixels, 'tolerance_px': r, 'offset_px': [dx, dy],
                      'domain_pixels': int(np.count_nonzero(domain)),
                      'domain_pixels_before_erosion': domain_before,
                      'frame_pixels': int(domain.size),
                      'estimated_bytes': need, 'pitch_um': float(pitch_um), 'tolerance_um': float(tolerance_um)}}


# --------------------------------------------------------------------------- A3. 창 점수와 후보

def _integral(arr):
    """(H+1, W+1) uint64 적분 영상. 합이 2^64 이상이 될 수 있으면 오류."""
    top = int(arr.max()) if arr.size else 0
    if top * arr.size >= INTEGRAL_LIMIT:
        raise NumericContractError('change_map_counter_overflow', f'integral image bound {top} x {arr.size} >= 2^64')
    out = np.zeros((arr.shape[0] + 1, arr.shape[1] + 1), dtype=np.uint64)
    np.cumsum(arr, axis=0, dtype=np.uint64, out=out[1:, 1:])
    np.cumsum(out[1:, 1:], axis=1, dtype=np.uint64, out=out[1:, 1:])
    return out


def _window_sums(arr, w):
    """모든 W×W 창(왼쪽 위 칸 기준)의 합, shape (H-W+1, W_-W+1), uint64(모듈러 산술이라 참값 < 2^64 이면 정확)."""
    ii = _integral(arr)
    return (ii[w:, w:] + ii[:-w, :-w]) - (ii[:-w, w:] + ii[w:, :-w])


def _local_max_grid(grid):
    """8-이웃 격자점보다 크거나 같고(경계 밖은 0) 값 > 0 인 점."""
    padded = np.pad(grid, 1)
    h, w = grid.shape
    keep = grid > 0
    for oy in (0, 1, 2):
        for ox in (0, 1, 2):
            if oy == 1 and ox == 1:
                continue
            keep &= grid >= padded[oy:oy + h, ox:ox + w]
    return keep


def window_candidates(maps, D, *, pitch_um, window_um, max_operation_bytes, origin_xy_um=(0.0, 0.0)):
    """층별 W×W 창 합(``score_um2 = 창 합 × pitch²``)과 극대 창 후보.

    W = round(window_um / pitch_um) px, stride = max(1, W // 4). 후보 = stride 격자에서 8-이웃 격자점보다 크거나 같고
    점수 > 0 인 점. ``observed_fraction = |D ∩ 창| / W²`` 가 1 미만이면 플래그 ``window_partially_unobserved``.
    중심 = ``origin + (왼쪽 위 칸 + W/2) * pitch``(candidates.window_bounds 가 쓰는 창 중심과 같은 물리 좌표, reference frame).
    D 는 ``change_maps`` 가 돌려준 erode(D, r) 이다(관측 불가 띠를 뺀 영역). ``max_operation_bytes`` 는 필수다(한도 없음은 None 을 명시).
    층 사이 합집합 점수는 만들지 않는다. 정렬: score 내림차순 → layer → center x → center y.
    반환: ``{'candidates': [...], 'window_px', 'stride_px', 'window_um'}``.
    """
    _check_scalar(pitch_um, window_um=window_um)
    domain = _valid_array(D, 'D')
    w = _round_px(window_um, pitch_um)
    height, width = domain.shape
    if w < 1 or w > height or w > width:
        raise _bad(f'window {w} px does not fit the frame {domain.shape}')
    for layer, arr in maps.items():
        if np.shape(arr) != domain.shape:
            raise _bad(f'map {layer} shape {np.shape(arr)} != D shape {domain.shape}')
    _check_admission(estimate_change_map_bytes(domain.shape, domain.shape, len(maps), w, 0), max_operation_bytes)
    stride = max(1, w // 4)
    ox, oy = float(origin_xy_um[0]), float(origin_xy_um[1])
    observed = _window_sums(domain, w)[::stride, ::stride].copy()
    out = []
    for layer in sorted(maps):
        sums = _window_sums(maps[layer], w)
        grid = sums[::stride, ::stride]
        del sums
        for gy, gx in np.argwhere(_local_max_grid(grid)):
            y, x = int(gy) * stride, int(gx) * stride
            count = int(observed[gy, gx])
            out.append({
                'layer': layer,
                'center_xy_um': [round(ox + (x + w / 2.0) * pitch_um, 6), round(oy + (y + w / 2.0) * pitch_um, 6)],
                'score_um2': int(grid[gy, gx]) * pitch_um * pitch_um,
                'observed_fraction': count / float(w * w),
                'flags': [] if count == w * w else [FLAG_PARTIAL],
                'window_origin_px': [y, x], 'window_px': w})
    out.sort(key=lambda c: (-c['score_um2'], c['layer'], c['center_xy_um'][0], c['center_xy_um'][1]))
    return {'candidates': out, 'window_px': w, 'stride_px': stride, 'window_um': float(window_um)}


def region_id_for(layer, center_xy_um):
    text = f'{layer}|{center_xy_um[0]:.6f}|{center_xy_um[1]:.6f}'
    return 'chg_' + hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def region_rank_key(info):
    """``candidates.merge_regions(..., rank_key=...)`` 용: 점수 내림차순 → layer → region_id."""
    return -info.score, info.layer, info.region_id


def to_region_infos(candidates, product_id, design_version, frame_id, frame_bbox_um=None):
    """후보를 ``candidates.RegionInfo`` 로 바꾼다. ``score`` 에는 스칼라(score_um2)만 싣고, area_um2 = score_um2.

    frame_bbox_um 을 주지 않으면 frame 가장자리 판정이 꺼진다(무한 bbox).
    """
    from .candidates import RegionInfo
    bbox = tuple(frame_bbox_um) if frame_bbox_um is not None else (-math.inf, -math.inf, math.inf, math.inf)
    return [RegionInfo(region_id_for(c['layer'], c['center_xy_um']), (product_id, design_version, frame_id),
                       (c['center_xy_um'][0], c['center_xy_um'][1]), 0, c['score_um2'], c['layer'], bbox,
                       c['score_um2']) for c in candidates]


# --------------------------------------------------------------------------- A4. 기여 case 와 분류

def _classify(a_w, b_w, w_px, pitch_um):
    na, nb = int(np.count_nonzero(a_w)), int(np.count_nonzero(b_w))
    if na == 0 and nb == 0:
        return None
    if na == 0:
        return {'classification': 'appeared', 'iou_global': 0.0, 'iou_local': 0.0, 'local_shift_um': [0.0, 0.0]}
    if nb == 0:
        return {'classification': 'disappeared', 'iou_global': 0.0, 'iou_local': 0.0, 'local_shift_um': [0.0, 0.0]}
    s_max = w_px // 4
    size = (_fft_size(a_w.shape[0], b_w.shape[0], s_max), _fft_size(a_w.shape[1], b_w.shape[1], s_max))
    inter = _lags(_spectrum(a_w, size), _spectrum(b_w, size), size, s_max)
    dy, dx, d2 = _grid(s_max)
    circle = d2 <= s_max * s_max
    by, bx = _pick_best(inter, circle, dy, dx, d2)
    i_global, i_local = int(inter[s_max, s_max]), int(inter[by + s_max, bx + s_max])
    iou_global = Fraction(i_global, na + nb - i_global)
    iou_local = Fraction(i_local, na + nb - i_local)
    if iou_local - iou_global >= Fraction(str(CLASS_IOU_DELTA)):
        kind = 'shifted'
    elif iou_global >= Fraction(str(CLASS_IOU_MIN)):
        kind = 'reshaped'
    else:
        kind = 'mixed_undetermined'
    return {'classification': kind, 'iou_global': round(float(iou_global), 6), 'iou_local': round(float(iou_local), 6),
            'local_shift_um': [round(bx * pitch_um, 6), round(by * pitch_um, 6)]}


def case_contributions(pairs_iter, windows, *, ref_valid, tgt_valid, offset_px, pitch_um, tolerance_um,
                       max_operation_bytes):
    """선정된 창(``window_candidates`` 후보 dict 목록)마다, 창 안 R > 0 인 case 를 기여 case 로 모으고 변경 종류를 분류한다.

    case 를 한 장씩 다시 읽는다(두 번째 패스). 창 둘레 r 픽셀을 포함한 잘라낸 영역에서 잔차를 계산하므로 frame 전체
    계산과 창 안에서 같은 값이다. 같은 analysis_layer 의 창에만 기여한다.
    분류: A_w 비고 B'_w 있음 → appeared, 반대 → disappeared. 둘 다 있으면 IoU_global 과, |s| <= W//4 정수 이동 중 교집합 수
    최대(동률: |s| 최소 → (dy, dx))인 IoU_local 로 ``iou_local - iou_global >= CLASS_IOU_DELTA`` → shifted,
    아니면 ``iou_global >= CLASS_IOU_MIN`` → reshaped, 그 외 mixed_undetermined. 이동한 B' 가 창 밖으로 나간 부분도 합집합에 남긴다.
    s 는 ``B'[p + s]`` 가 ``A[p]`` 와 맞는 이동(``local_shift_um = [dx, dy]``)이다.
    반환: ``{'windows': [{'window_index', 'layer', 'contributing_cases': [...]}], 'constants': {...}, 'stats': {...}}``.
    """
    _check_scalar(pitch_um, tolerance_um)
    ref_valid, tgt_valid = _valid_array(ref_valid, 'ref_valid'), _valid_array(tgt_valid, 'tgt_valid')
    dx, dy = _offset(offset_px)
    windows = list(windows)
    pairs = list(pairs_iter)
    r = _round_px(tolerance_um, pitch_um)
    height, width = ref_valid.shape
    for window in windows:
        y0, x0 = window['window_origin_px']
        w = window['window_px']
        if y0 < 0 or x0 < 0 or y0 + w > height or x0 + w > width:
            raise _bad(f'window {window.get("center_xy_um")} lies outside the frame {ref_valid.shape}')
    layers = sorted({window['layer'] for window in windows})
    # 이 패스는 층별 uint32 지도와 적분 영상을 만들지 않는다: 1단계 추정(window_px=0)만 검사한다.
    need = estimate_change_map_bytes(ref_valid.shape, tgt_valid.shape, len(layers), 0, r)
    _check_admission(need, max_operation_bytes)
    domain = _erode(_comparison_domain(ref_valid, tgt_valid, dy, dx), r)
    by_layer = {}
    for index, window in enumerate(windows):
        by_layer.setdefault(window['layer'], []).append(index)
    found = [[] for _ in windows]
    for pair in pairs:
        indexes = by_layer.get(pair.layer)
        if not indexes:
            continue
        a, b = _load_checked(pair, ref_valid.shape, tgt_valid.shape)
        for index in indexes:
            window = windows[index]
            y0, x0 = window['window_origin_px']
            w = window['window_px']
            cy0, cy1, cx0, cx1 = max(y0 - r, 0), min(y0 + w + r, height), max(x0 - r, 0), min(x0 + w + r, width)
            a_c = a[cy0:cy1, cx0:cx1]
            b_c = _shift_crop(b, dy, dx, cy0, cy1, cx0, cx1)
            d_c = domain[cy0:cy1, cx0:cx1]
            inner = (slice(y0 - cy0, y0 - cy0 + w), slice(x0 - cx0, x0 - cx0 + w))
            count = int(np.count_nonzero(_residual(a_c, b_c, d_c, r)[inner]))
            if not count:
                continue
            entry = {'case_key': pair.case_key, 'residual_um2': count * pitch_um * pitch_um}
            entry.update(_classify(a_c[inner] & d_c[inner], b_c[inner] & d_c[inner], w, pitch_um))
            entry['flags'] = list(getattr(pair, 'flags', ()))
            found[index].append(entry)
        a = b = a_c = b_c = None   # 다음 쌍을 읽기 전에 이전 쌍 배열(과 그 view)을 놓는다
    out = []
    for index, window in enumerate(windows):
        found[index].sort(key=lambda e: (-e['residual_um2'], e['case_key']))
        out.append({'window_index': index, 'layer': window['layer'], 'center_xy_um': list(window['center_xy_um']),
                    'contributing_cases': found[index]})
    return {'windows': out,
            'constants': {'CLASS_IOU_DELTA': CLASS_IOU_DELTA, 'CLASS_IOU_MIN': CLASS_IOU_MIN,
                          'local_search': 'integer shifts with |s| <= W // 4 (Euclidean), ties: smallest |s| then (dy, dx)'},
            'stats': {'pairs': len(pairs), 'windows': len(windows), 'tolerance_px': r, 'estimated_bytes': need}}
