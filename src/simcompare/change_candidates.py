"""change-roi/T6: ``candidates-change`` — CNT 정렬 + hotspot 변경 지도로 DL2 에 넣을 변경 위치 질의 ROI 를 만든다.

real_hotspot_v1 두 제품(reference / target)만 지원하고, 제품마다 frame 은 정확히 하나여야 한다. 지름은 5 µm 로 고정이다.

흐름: 두 side 준비(``candidates.prepare_side``) → 층별 CNT 로 정렬(``cnt_align``) → 변경 지도·창 후보(``change_map``) →
층마다 병합·방향별 상위 top-k → 기여 case → manual ROI 질의 export(``candidates.export_manual_rows``) + index export
(``candidates.export_index``) → candidate_queries_v1.json(기존 스키마 + 추가 키), funnel, execution.

출력은 결정적이다: 같은 입력·인수는 같은 바이트를 만든다(경로는 출력 폴더 상대).
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import math
import numbers
from pathlib import Path
import sys

import numpy as np

from . import alignment_report, candidates, change_map, layer_align, local_align, template_export
from .numeric_schema import NumericContractError

DIAMETER_UM = 5
ROI_SOURCE = 'cnt_change'
ALIGNMENT_SCHEMA = 'cnt_alignment_v1'
ELECTRODE_SET_BASIS = 'dl1_electrode_folders'
UNION_BASIS = 'paired_and_one_sided_cases'   # 합집합·순위 = 짝지어진 조합 + 한쪽 조합(그쪽 합집합에만). 전극이 한쪽에만 있어도 포함한다
NO_CASE_REASON = 'no_case_in_this_model'      # 이 방향 모델에는 기여 case 가 없다(전부 반대 모델에만 있는 조합)
DIRECT_SOURCE = {'kind': 'template_match', 'method': 'cnt_alignment_v1', 'version': '1'}
DIRECT_SIDECAR_SCHEMA = 'direct_pairs_sidecar_v1'
DIRECT_LIMITATION = 'compare-windows does not verify these input hashes'


def _bad(code, detail=''):
    return NumericContractError(code, detail)


# --------------------------------------------------------------------------- 인수·입력 검사

def _finite(label, value, *, minimum=None, positive=False, unit_interval=False):
    if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(value):
        raise _bad('invalid_candidates_change_argument', f'{label}는 유한한 수여야 합니다')
    if positive and value <= 0:
        raise _bad('invalid_candidates_change_argument', f'{label}는 0보다 커야 합니다')
    if minimum is not None and value < minimum:
        raise _bad('invalid_candidates_change_argument', f'{label}는 {minimum} 이상이어야 합니다')
    if unit_interval and not 0 < value <= 1:
        raise _bad('invalid_candidates_change_argument', f'{label}는 (0, 1] 범위여야 합니다')


MERGE_SCOPES = ('layer', 'all')
MERGE_POLICY = {'layer': 'per_layer_window_overlap_intersection_over_min_area_union_find_region_id_order',
                'all': 'all_layers_window_overlap_intersection_over_min_area_union_find_region_id_order'}
SUPPRESS_POLICY = 'greedy_score_order_window_overlap_ge_x_all_layers'
SUPPRESS_POLICY_ANY = 'greedy_score_order_any_window_overlap_all_layers'
ATTRIBUTES_POLICY_ALL_WINDOW_OFFSET = 'contributing_cases_all_layers_window_offset'
ATTRIBUTES_POLICY_ALL ='score_union_sum_residual_representative_layer_window_changed_case_count_all_member_layers'


APPLY_ALIGN_MODES = ('diagnostic', 'unit', 'local')
UNIT_FOLD_POLICY = 'greedy_rank_order_toroidal_unit_distance_le_tolerance_reference_unit_all_layers'


def _validate_args(cnt_root, search, min_dice, layer_tolerance, change_tolerance, window, overlap, top_k,
                   mask_derivation_rule, merge_scope=None, suppress_overlap=None, electrode_png_root=None, local_align_um=None,
                   unit_align_um=None, apply_align=None, unit_fold=False, unit_fold_tolerance_um=None, refine_pool=None):
    if not isinstance(cnt_root, (str, Path)) or not str(cnt_root):
        raise _bad('invalid_candidates_change_argument', '--cnt-from-dl1-root 경로가 필요합니다')
    if not Path(cnt_root).is_dir():
        raise _bad('cnt_dl1_root_missing',
                   f'--cnt-from-dl1-root 경로가 없습니다: {cnt_root} (resolved: {Path(cnt_root).resolve()})')
    if electrode_png_root is not None and (not isinstance(electrode_png_root, (str, Path)) or not str(electrode_png_root)
                                           or not Path(electrode_png_root).is_dir()):
        raise _bad('electrode_png_root_missing',
                   f'--electrode-png-root 경로가 폴더가 아닙니다: {electrode_png_root} (resolved: {Path(str(electrode_png_root)).resolve()})')
    if mask_derivation_rule is None:
        raise _bad('mask_derivation_rule_required', f'--mask-derivation-rule: {template_export.RULE_VERSION}만 허용되며 기본값이 없습니다')
    if mask_derivation_rule != template_export.RULE_VERSION:
        raise _bad('mask_derivation_rule_unknown', f'{mask_derivation_rule!r}: {template_export.RULE_VERSION}만 허용')
    _finite('--align-search-um', search, positive=True)
    _finite('--align-min-dice', min_dice, minimum=0)
    if min_dice > 1:
        raise _bad('invalid_candidates_change_argument', '--align-min-dice는 [0, 1] 범위여야 합니다')
    _finite('--align-layer-tolerance-um', layer_tolerance, minimum=0)
    _finite('--change-tolerance-um', change_tolerance, minimum=0)
    _finite('--window-um', window, positive=True)
    _finite('--merge-overlap', overlap, unit_interval=True)
    if type(top_k) is not int or top_k < 1:
        raise _bad('invalid_candidates_change_argument', '--top-k는 1 이상의 정수여야 합니다')
    if merge_scope is not None and merge_scope not in MERGE_SCOPES:
        raise _bad('invalid_candidates_change_argument', f'--merge-scope는 {"|".join(MERGE_SCOPES)} 중 하나여야 합니다')
    if suppress_overlap is not None:
        _finite('--suppress-overlap', suppress_overlap)
        if not 0 <= suppress_overlap <= 1:
            raise _bad('invalid_candidates_change_argument', '--suppress-overlap는 [0, 1] 범위여야 합니다')
    if local_align_um is not None and (isinstance(local_align_um, bool) or not isinstance(local_align_um, (int, float))
                                       or not math.isfinite(local_align_um) or local_align_um <= 0):
        raise _bad('local_align_um_invalid', f'--local-align-um {local_align_um!r}: 유한한 양수여야 합니다')
    if unit_align_um is not None:
        _finite('--unit-align-um', unit_align_um, positive=True)
    if apply_align is not None and apply_align not in APPLY_ALIGN_MODES:
        raise _bad('invalid_candidates_change_argument', f'--apply-align는 {"|".join(APPLY_ALIGN_MODES)} 중 하나여야 합니다')
    if apply_align == 'local' and local_align_um is None:
        raise _bad('apply_align_requires_local_align', '--apply-align local 은 --local-align-um 이 필요합니다')
    if apply_align == 'unit' and unit_align_um is None:
        raise _bad('apply_align_requires_unit_align', '--apply-align unit 은 --unit-align-um 이 필요합니다')
    if refine_pool is not None:
        if apply_align != 'local':
            raise _bad('refine_pool_requires_local', '--refine-pool 은 --apply-align local 과 함께만 줄 수 있습니다')
        if type(refine_pool) is not int or refine_pool < 1:
            raise _bad('invalid_candidates_change_argument', '--refine-pool 은 1 이상의 정수여야 합니다')
    if unit_fold_tolerance_um is not None:
        if not unit_fold:
            raise _bad('invalid_candidates_change_argument', '--unit-fold-tolerance-um 은 --unit-fold 와 함께만 줄 수 있습니다')
        _finite('--unit-fold-tolerance-um', unit_fold_tolerance_um, minimum=0)


def _require_layer_folder(products, layer_root):
    """두 모델 모두 새 구조(Merged_Layer) + Layer PNG 가 있어야 한다(``layer_folder_required``)."""
    from .adapters.electrode_png import layout_structure, scan_layer_pngs
    missing = [name for name in ('reference', 'target')
               if layout_structure(layer_root, products[name].product_id) != 'merged_layer_v2'
               or not scan_layer_pngs(layer_root, products[name].product_id)]
    if missing:
        raise _bad('layer_folder_required',
                   f'{",".join(missing)}: 새 구조(Merged_Layer) + Layer/ PNG 가 있어야 합니다 (root {layer_root})')


def _check_local_apply(local_align_um, pitch, products, layer_root, unit_aligned=True):
    """``--apply-align local`` 의 쓰기 전 검사. 국소 탐색 반경(px)을 돌려준다.

    Layer 새 구조가 필요하고(``layer_folder_required``), 반경이 ``[필요 px, MAX_LOCAL_APPLY_PX]`` 안이어야 한다
    (진단 전용 경로의 2..30 px 규칙은 이 경로에 적용하지 않는다). 필요 px 는 시작점이 unit offset 이면 |ΔU|max + 2,
    ``--unit-align-um`` 이 없어 시작점이 전역 g 이면 unit 탐색과 같은 2·|ΔU|max + 2 다.
    µm → px 반올림은 진단 경로(``local_align.search_radius_px``)와 같은 규칙이다."""
    _require_layer_folder(products, layer_root)
    ref_geo = layer_align.UnitGeometry(products['reference'].frame.valid.shape)
    tgt_geo = layer_align.UnitGeometry(products['target'].frame.valid.shape)
    radius = int(round(float(local_align_um) / float(pitch)))
    delta = layer_align.delta_u_max(ref_geo, tgt_geo)
    if unit_aligned:
        required, why = layer_align.required_local_radius_px(delta), f'|ΔU|max {delta} px + 2 px'
    else:
        required, why = layer_align.required_unit_radius_px(delta), f'unit 정렬 없음: 시작점이 전역 offset 이므로 |ΔU|max {delta} px 의 2배 + 2 px'
    required = max(required, local_align.MIN_LOCAL_SEARCH_PX)
    if radius < required:
        raise _bad('local_align_um_too_small_for_unit_delta',
                   f'--local-align-um {local_align_um} ({radius} px) < 필요 {required} px = {round(required * pitch, 6)} µm ({why})')
    if radius > layer_align.MAX_LOCAL_APPLY_PX:
        raise _bad('local_align_um_invalid',
                   f'--local-align-um {local_align_um} ({radius} px) > 상한 {layer_align.MAX_LOCAL_APPLY_PX} px')
    return radius


def _check_unit_align(unit_align_um, pitch, products, layer_root):
    """``--unit-align-um`` 가 필요한 실행의 쓰기 전 검사. 탐색 반경(px)을 돌려준다.

    두 모델 모두 새 구조(Merged_Layer) + Layer PNG 가 있어야 하고(``layer_folder_required``), 반경이 요구 범위
    ``[2·|ΔU|max + 2, MAX_UNIT_SEARCH_PX]`` 안이어야 한다."""
    _require_layer_folder(products, layer_root)
    ref_geo = layer_align.UnitGeometry(products['reference'].frame.valid.shape)
    tgt_geo = layer_align.UnitGeometry(products['target'].frame.valid.shape)
    radius = change_map._round_px(unit_align_um, pitch)
    delta = layer_align.delta_u_max(ref_geo, tgt_geo)
    required = layer_align.required_unit_radius_px(delta)
    if radius < required:
        raise _bad('unit_align_um_too_small',
                   f'--unit-align-um {unit_align_um} ({radius} px) < 필요 {required} px = {round(required * pitch, 6)} µm '
                   f'(|ΔU|max {delta} px 의 2배 + 2 px)')
    if radius > layer_align.MAX_UNIT_SEARCH_PX:
        raise _bad('unit_align_um_too_large',
                   f'--unit-align-um {unit_align_um} ({radius} px) > 상한 {layer_align.MAX_UNIT_SEARCH_PX} px = '
                   f'{round(layer_align.MAX_UNIT_SEARCH_PX * pitch, 6)} µm')
    return radius


class _Product:
    """한 side 의 (제품, design_version, frame) 하나."""

    def __init__(self, name, side):
        self.name, self.side = name, side
        cases = side.dataset.cases
        frames = {}
        for case in cases:
            frames.setdefault(case.identity.product_id, set()).add(case.identity.frame_id)
        self.frames_by_product = {p: sorted(f) for p, f in sorted(frames.items())}
        self.all_frames = sorted({f for fs in frames.values() for f in fs})
        self.products = sorted(frames)
        self.designs = sorted({c.identity.design_version for c in cases})

    def check_single_frame(self):
        if any(len(f) != 1 for f in self.frames_by_product.values()) or len(self.all_frames) != 1:
            raise _bad('multiple_frames_unsupported',
                       f'{self.name}: 제품별 frame_id 목록 {json.dumps(self.frames_by_product, ensure_ascii=False)}. '
                       'candidates-change 는 제품마다 frame 이 정확히 하나일 때만 지원한다')

    def bind(self):
        if len(self.products) != 1 or len(self.designs) != 1:
            raise _bad('candidates_change_single_product_required',
                       f'{self.name}: products={self.products} design_versions={self.designs}')
        self.product_id, self.design_version, self.frame_id = self.products[0], self.designs[0], self.all_frames[0]
        self.frame = self.side.dataset.frames[self.frame_id]


def _operation_limit(sides):
    """stage 4 admission 과 같은 출처: configured_dataset 이 provenance 에 남긴 max_operation_bytes(기본 8 GiB, 물리 메모리 이하)."""
    limits = []
    for name, side in sides.items():
        estimate = side.dataset.provenance.get('resource_estimate')
        limit = estimate.get('max_operation_bytes') if isinstance(estimate, dict) else None
        if type(limit) is not int or limit <= 0:
            raise _bad('invalid_resource_limit', f'{name}: dataset has no max_operation_bytes (real_hotspot_v1 required)')
        limits.append(limit)
    return min(limits)


# --------------------------------------------------------------------------- 좌표·키

def _cell(center, frame):
    pitch = frame.pixel_pitch_um
    return (int(math.floor((center[0] - frame.origin_xy_um[0]) / pitch + 1e-9)),
            int(math.floor((center[1] - frame.origin_xy_um[1]) / pitch + 1e-9)))


def _row_key(identity, failure_type, analysis_layer, center, frame):
    return (tuple(sorted(identity.items())), failure_type, analysis_layer, _cell(center, frame))


def _clipped(center, bbox, window_um):
    x0, y0, x1, y1 = bbox
    a, b, c, d = candidates.window_bounds(center, window_um)
    return a < x0 - 1e-9 or b < y0 - 1e-9 or c > x1 + 1e-9 or d > y1 + 1e-9


def _shift_um(offset_px, pitch, ref_frame, tgt_frame):
    """reference 물리 좌표 → target 물리 좌표 이동(µm): p_target = p_reference + offset, 두 frame 의 원점 차이 포함."""
    dx, dy = offset_px
    return (tgt_frame.origin_xy_um[0] - ref_frame.origin_xy_um[0] + dx * pitch,
            tgt_frame.origin_xy_um[1] - ref_frame.origin_xy_um[1] + dy * pitch)


def _sha_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


# --------------------------------------------------------------------------- CNT 변경 면적

def _cnt_change_by_layer(window, ref_layers, tgt_layers, ref_valid, tgt_valid, offset_px, pitch):
    """창 안, 정렬 후 CNT 층별 XOR 면적(µm²). 두 valid 가 겹치는 칸에서만 센다.

    한쪽 CNT 층이 비면 비교할 수 없으므로 {} 이다(호출자가 질의 flag ``cnt_change_unavailable`` 을 단다)."""
    if not ref_layers or not tgt_layers:
        return {}
    names = sorted(set(ref_layers) | set(tgt_layers))
    dx, dy = offset_px
    y0, x0 = window['window_origin_px']
    w = window['window_px']
    y1, x1 = y0 + w, x0 + w
    domain = (ref_valid[y0:y1, x0:x1] &
              change_map._shift_crop(tgt_valid, dy, dx, y0, y1, x0, x1))
    out = {}
    for name in names:
        a = (ref_layers[name][y0:y1, x0:x1] & ref_valid[y0:y1, x0:x1]) if name in ref_layers else np.zeros((w, w), bool)
        if name in tgt_layers:
            b = change_map._shift_crop(tgt_layers[name] & tgt_valid, dy, dx, y0, y1, x0, x1)
        else:
            b = np.zeros((w, w), bool)
        out[name] = round(int(np.count_nonzero((a ^ b) & domain)) * pitch * pitch, 6)
    return out


# --------------------------------------------------------------------------- 순위 속성

APPLIED_KEYS = ('offset_xy_px_applied', 'offset_source')      # --apply-align unit 일 때만 창·쌍에 단다
ATTRIBUTE_KEYS = ('score_basis','sum_residual_um2', 'changed_case_count', 'union_area_reference_um2',
                  'union_area_target_um2', 'union_delta_um2')


def _window_attributes(window, n_cases):
    """질의·사이드카에 같이 싣는 순위 속성. 순위 점수 score_um2 는 합집합 변화 면적, 나머지는 속성이다."""
    return {'score_basis': window['score_basis'], 'sum_residual_um2': window['sum_residual_um2'],
            'changed_case_count': n_cases, 'union_area_reference_um2': window['union_area_reference_um2'],
            'union_area_target_um2': window['union_area_target_um2'], 'union_delta_um2': window['union_delta_um2']}


# --------------------------------------------------------------------------- 직접 대응 (DL2 건너뛰기)

def _valid_at(frame, center):
    ix, iy = _cell(center, frame)
    height, width = frame.valid.shape
    return 0 <= ix < width and 0 <= iy < height and bool(frame.valid[iy, ix])


def _direct_side(case, center, rows_by_key, frame):
    """roi_correspondence_v1 의 한 side. source/process_step/exposure/unit 은 기여 case 값으로 채워 group 모호성을 막는다."""
    identity = asdict(case.identity)
    side = {'product_id': identity['product_id'], 'design_version': identity['design_version'],
            'frame_id': identity['frame_id'], 'center_xy_um': list(center), 'analysis_layer': case.analysis_layer,
            'failure_type': case.failure_type, 'endpoints': [asdict(e) for e in case.endpoints]}
    for field in ('source_id', 'process_step_id', 'exposure_id'):
        side[field] = identity[field]
    unit_id = case.metadata.get('unit_id')
    if unit_id:
        side['unit_id'] = unit_id
    row = rows_by_key.get(_row_key(identity, case.failure_type, case.analysis_layer, center, frame))
    if row is not None and row.get('status') == 'ready':       # DL2 는 blocked ROI 를 색인하지 않으므로 ready 행만 잇는다
        side['roi_id'] = row['roi_id']
    return side, identity


def _layer_cases(dataset, layer):
    return [c for c in dataset.cases if c.analysis_layer == layer]


def _dataset_filters(dataset, layer, present_case=None):
    """한쪽 조합에서 case 가 없는 쪽 side 의 group 필터. (필터, 정해지지 못한 필드가 있는지)를 돌려준다.

    그 쪽 dataset 의 (같은 층) case 들의 group 튜플(source_id, process_step_id, exposure_id, unit_id) 집합이 하나뿐이면 그 튜플을 쓴다.
    여럿이면 case 가 있는 쪽(``present_case``)의 튜플과 같은 튜플이 정확히 하나일 때만 그 튜플을 쓴다. 필드마다 따로 정하지 않는다.
    그래도 정해지지 않으면 필터를 비우고 unresolved 로 알린다."""
    cases = _layer_cases(dataset, layer) or list(dataset.cases)
    names = ('source_id', 'process_step_id', 'exposure_id', 'unit_id')

    def group(case):
        return (getattr(case.identity, 'source_id', None), getattr(case.identity, 'process_step_id', None),
                getattr(case.identity, 'exposure_id', None), case.metadata.get('unit_id'))

    groups = sorted({group(c) for c in cases}, key=lambda g: tuple(str(v) for v in g))
    if len(groups) > 1:
        hint = group(present_case) if present_case is not None else None
        groups = [g for g in groups if g == hint]
        if len(groups) != 1:
            return {}, True
    return {name: value for name, value in zip(names, groups[0]) if value is not None} if groups else {}, False


def _direct_side_one_sided(case, layer, center, rows_by_key, item, other_case=None):
    """한쪽 조합의 side: endpoints·failure_type 을 쓰지 않는다(반대 모델에 그 case 가 없어 named_pair 를 찾을 수 없다).
    analysis_layer 는 그 조합의 층, 필터는 case 가 있는 쪽은 그 case identity, 없는 쪽은 dataset 값이다.
    (side, identity, 필터 미결정 여부)를 돌려준다."""
    side = {'product_id': item.product_id, 'design_version': item.design_version, 'frame_id': item.frame_id,
            'center_xy_um': list(center), 'analysis_layer': layer}
    if case is None:
        filters, unresolved = _dataset_filters(item.side.dataset, layer, other_case)
        identity = {'product_id': item.product_id, 'design_version': item.design_version, 'frame_id': item.frame_id,
                    **filters}
        side.update({k: v for k, v in identity.items() if k not in ('product_id', 'design_version', 'frame_id')})
        return side, identity, unresolved
    identity = asdict(case.identity)
    for field in ('source_id', 'process_step_id', 'exposure_id'):
        side[field] = identity[field]
    unit_id = case.metadata.get('unit_id')
    if unit_id:
        side['unit_id'] = unit_id
    row = rows_by_key.get(_row_key(identity, case.failure_type, case.analysis_layer, center, item.frame))
    if row is not None and row.get('status') == 'ready':
        side['roi_id'] = row['roi_id']
    return side, identity, False


def _direct_pairs(plan, contrib, pair_by_key, ref, tgt, shift, alignment, rows_index):
    """(선정 ROI, 기여 case) 하나당 쌍 하나. 두 방향의 ROI 는 a=reference / b=target 으로 정규화한 뒤 같은 키를 하나로 합친다.

    a 중심 = reference 쪽 ROI 중심, b 중심 = a + (원점 차이 + offset)(``_shift_um``, T6 의 target 방향 중심과 같은 식).
    ready 쌍의 reasons 는 비어 있다: 정렬 불확실성은 사이드카에만 쓴다(bind_pairs 는 reasons 가 있으면 blocked 로 바꾼다).
    """
    failed = alignment['status'] == 'alignment_failed_identity_used'
    found = {}
    for query_name in ('reference', 'target'):
        direction = plan[query_name]['direction']
        for entry in plan[query_name]['entries']:
            rep, window, i = entry['merge']['representative'], entry['window'], entry['index']
            a_center = entry['ref_center']
            shift_i = entry.get('shift', shift)         # 창별 이동(apply=unit 은 창 중심이 속한 unit 의 offset)
            b_center = [round(a_center[0] + shift_i[0], 6), round(a_center[1] + shift_i[1], 6)]
            for contribution in contrib[i]['contributing_cases']:
                pair = pair_by_key[contribution['case_key']]
                present_in = pair.present_in
                absent, unresolved = [], []
                if present_in == 'both':
                    a, a_identity = _direct_side(pair.ref_case, a_center, rows_index['reference'], ref.frame)
                    b, b_identity = _direct_side(pair.tgt_case, b_center, rows_index['target'], tgt.frame)
                else:
                    a, a_identity, a_unresolved = _direct_side_one_sided(
                        pair.ref_case, pair.layer, a_center, rows_index['reference'], ref, pair.tgt_case)
                    b, b_identity, b_unresolved = _direct_side_one_sided(
                        pair.tgt_case, pair.layer, b_center, rows_index['target'], tgt, pair.ref_case)
                    unresolved = [name for name, flag in (('a', a_unresolved), ('b', b_unresolved)) if flag]
                    # 반대 모델에 그 층의 case 가 하나도 없으면 compare-windows 에 맡기지 않고 여기서 blocked 로 쓴다
                    absent = [name for name, case, item in (('a', pair.ref_case, ref), ('b', pair.tgt_case, tgt))
                              if case is None and not _layer_cases(item.side.dataset, pair.layer)]
                digest = _sha_json({
                    'a': [sorted(a_identity.items()), a['analysis_layer'], a.get('failure_type'), _cell(a_center, ref.frame)],
                    'b': [sorted(b_identity.items()), b['analysis_layer'], b.get('failure_type'), _cell(b_center, tgt.frame)]})
                pair_id = 'direct_' + digest[:16]
                if pair_id in found:
                    found[pair_id]['directions'].add(direction)
                    continue
                flags = sorted(set(window['flags']) | set(contribution['flags'])
                               | {'filter_unresolved_' + name for name in unresolved})
                found[pair_id] = {'pair_id': pair_id, 'a': a, 'b': b, 'directions': {direction}, 'layer_absent': absent,
                                  'score_um2': window['score_um2'], 'score_layer': rep.layer, 'region_id': rep.region_id,
                                  **_window_attributes(window, len(contrib[i]['contributing_cases'])),
                                  **entry.get('applied', {}),
                                  'observed_fraction': window['observed_fraction'], 'flags': flags,
                                  'present_in': present_in, 'one_sided_case': present_in != 'both',
                                  'layer_changed': pair.layer_changed,
                                  'change_kind': contribution['classification'],
                                  'residual_um2': contribution['residual_um2'],
                                  'area_reference_um2': contribution['area_reference_um2'],
                                  'area_target_um2': contribution['area_target_um2'],
                                  'iou_global': contribution['iou_global'],
                                  'iou_local': contribution['iou_local'],
                                  'local_shift_um': contribution['local_shift_um'], **_layer_key(contribution)}
    for record in found.values():
        if failed:
            reasons = ['alignment_failed']
        else:
            reasons = [name for name, frame, side in (('a', ref.frame, record['a']), ('b', tgt.frame, record['b']))
                       if not _valid_at(frame, side['center_xy_um'])]
            reasons = ['position_outside_valid_domain_' + name for name in reasons]
        reasons += ['one_sided_layer_absent_' + name for name in record['layer_absent']]
        record['status'], record['reasons'] = ('blocked' if reasons else 'ready'), reasons
    return sorted(found.values(), key=lambda r: (-r['score_um2'], -r['sum_residual_um2'], r['a']['analysis_layer'],
                                                  r['a']['center_xy_um'][0], r['a']['center_xy_um'][1], r['pair_id']))


def _direct_documents(records, alignment, commit, inputs):
    """(roi_correspondence_v1 문서, direct_pairs_sidecar_v1 문서). 시각·경로·호스트를 넣지 않아 출력이 결정적이다."""
    from . import correspondence
    payload = correspondence.validate_correspondence({
        'schema_version': correspondence.SCHEMA_VERSION,
        'producer': {'kind': 'template_match', 'version': '1', 'commit': commit, 'created_at': None},
        'pairs': [{'pair_id': r['pair_id'], 'a': r['a'], 'b': r['b'], 'source': dict(DIRECT_SOURCE),
                   'status': r['status'], 'reasons': r['reasons']} for r in records],
        'notes': ['candidates-change direct pairs: b centre = a centre + alignment offset (see direct_pairs_sidecar.json)']})
    payload.pop('warnings')
    summary = {k: alignment[k] for k in ('status', 'reasons', 'offset_xy_um', 'offset_xy_px', 'dice_best', 'peak_ratio',
                                         'pattern_period_vectors_um')}
    summary['alternatives'] = list(alignment['alternatives'])[:5]
    pairs = {}
    for r in records:
        pairs[r['pair_id']] = {
            'status': r['status'], 'reasons': r['reasons'], 'offset_xy_um': list(alignment['offset_xy_um']),
            'alignment_status': alignment['status'], 'alignment_reasons': list(alignment['reasons']),
            'peak_ratio': alignment['peak_ratio'], 'score_um2': r['score_um2'], 'score_layer': r['score_layer'],
            **{k: r[k] for k in ATTRIBUTE_KEYS},
            'region_id': r['region_id'], 'one_sided_case': r['one_sided_case'], 'present_in': r['present_in'],
            'layer_changed': r['layer_changed'], 'change_kind': r['change_kind'], 'residual_um2': r['residual_um2'],
            'area_reference_um2': r['area_reference_um2'], 'area_target_um2': r['area_target_um2'],
            'iou_global': r['iou_global'], 'iou_local': r['iou_local'], 'local_shift_um': r['local_shift_um'],
            'observed_fraction': r['observed_fraction'], 'flags': r['flags'], 'directions': sorted(r['directions']),
            **{k: r[k] for k in APPLIED_KEYS if k in r}, **_layer_key(r)}
    sidecar = {'schema_version': DIRECT_SIDECAR_SCHEMA, 'method': DIRECT_SOURCE['method'],
               'sign': 'p_target = p_reference + offset', 'alignment': summary, 'inputs': inputs,
               'limitations': [DIRECT_LIMITATION], 'pairs': pairs}
    return payload, sidecar


# --------------------------------------------------------------------------- funnel

def _derivation_rows(side, rows):
    """export 행(슬림 행은 endpoints 를 갖지 않는다)을 template_export 의 writer 가 읽는 형태로 바꾼다.

    endpoints·failure_type·analysis_layer 는 export 가 행에 쓴 값과 같은 출처인 dataset case(행의 source_identity 로 찾는다)에서 읽는다."""
    by_identity = {tuple(sorted(asdict(c.identity).items())): c for c in side.dataset.cases}
    out = []
    for row in rows:
        case = by_identity.get(tuple(sorted((row.get('source_identity') or {}).items())))
        if case is None:
            if row['status'] == 'ready':
                raise _bad('mask_derivation_case_unresolved', f"{side.name}: {row['roi_id']}")
            continue
        out.append({'case_id': case.identity.case_id, 'status': row['status'], 'failure_type': case.failure_type,
                    'analysis_layer': case.analysis_layer, 'endpoints': [asdict(e) for e in case.endpoints]})
    return out


def _stack_order(side):
    """제품의 {actual_layer: stack_order}. condition_assignment.layer_stack 에서 읽는다(export-template-rois 의 layer_stack 과 같은 값)."""
    assignment = side.section.get('condition_assignment') if isinstance(side.section, dict) else None
    stack = assignment.get('layer_stack') if isinstance(assignment, dict) else None
    if not isinstance(stack, list) or not stack:
        raise _bad('mask_derivation_layer_stack_missing', f'{side.name}: condition_assignment.layer_stack')
    return {row['actual_layer']: row['stack_order'] for row in stack}


def _layer_key(contribution):
    """merge_scope=all 에서만 기여 case 에 ``layer`` 가 있다(case 가 속한 층). layer 범위면 키를 만들지 않는다."""
    return {'layer': contribution['layer']} if 'layer' in contribution else {}


def _funnel(direction, n_candidates, n_merged, queries, top_k, n_suppressed=None, n_unit_folded=None, refine=None):
    """``refine`` (--apply-align local 만): {'pool': 풀 크기, 'survivors': 재점수 뒤 변경이 남은 창 수}. 단계 ``refine_pool`` 이 ``change_candidates`` 바로 뒤에 들어가고
    ``merged`` 는 survivors 에서 센다(풀 밖 창과 재점수 뒤 0 이 된 창은 병합 전에 이미 빠졌다)."""
    kept = len(queries)
    if refine is not None:
        n_before_merge = refine['survivors']
    else:
        n_before_merge = n_candidates
    survivors = (n_merged if n_suppressed is None else n_suppressed) if n_unit_folded is None else n_unit_folded
    ready = sum(q['status'] == 'ready' for q in queries)
    blocked_reasons = {}
    for q in queries:
        if q['status'] != 'ready':
            for code in q['reasons']:
                blocked_reasons[code] = blocked_reasons.get(code, 0) + 1
    shortfall = max(top_k - kept, 0)
    stages = [
        {'stage': 'change_candidates', 'count': n_candidates, 'dropped': 0, 'reasons': {}},
        {'stage': 'merged', 'count': n_merged, 'dropped': n_before_merge - n_merged,
         'reasons': {'merged_overlap': n_before_merge - n_merged} if n_before_merge > n_merged else {}},
        {'stage': 'top_k', 'count': kept, 'dropped': survivors - kept,
         'reasons': ({'cut_by_top_k': survivors - kept} if survivors > kept else
                     ({'top_k_not_filled': shortfall} if shortfall else {}))},
        {'stage': 'exported_ready', 'count': ready, 'dropped': kept - ready, 'reasons': dict(sorted(blocked_reasons.items()))}]
    if refine is not None:
        outside = n_candidates - refine['pool']
        zero = refine['pool'] - refine['survivors']
        reasons = {}
        if outside:
            reasons['outside_refine_pool'] = outside
        if zero:
            reasons['zero_after_refine'] = zero
        stages.insert(1, {'stage': 'refine_pool', 'count': refine['survivors'], 'dropped': n_candidates - refine['survivors'],
                          'reasons': reasons})
    if n_suppressed is not None:
        stages.insert(3 if refine is not None else 2, {'stage': 'suppressed', 'count': n_suppressed, 'dropped': n_merged - n_suppressed,
                          'reasons': {'suppressed_overlap': n_merged - n_suppressed} if n_merged > n_suppressed else {}})
    if n_unit_folded is not None:
        before = n_merged if n_suppressed is None else n_suppressed
        stages.insert(len(stages) - 2, {'stage': 'unit_folded', 'count': n_unit_folded, 'dropped': before - n_unit_folded,
                                        'reasons': {'unit_folded': before - n_unit_folded} if before > n_unit_folded else {}})
    doc = {'direction': direction, 'stages': stages, 'change_candidates': n_candidates, 'merged': n_merged,
            'top_k': top_k, 'top_k_not_filled': shortfall, 'exported_ready': ready, 'blocked': kept - ready,
            'note': 'reasons 값은 사유 코드별 질의 수이며 한 질의가 여러 사유를 가질 수 있어 dropped와 합이 다를 수 있다'}
    if refine is not None:
        doc['refine_pool'] = refine['requested']
    if n_suppressed is not None:
        doc['suppressed'] = n_suppressed
    if n_unit_folded is not None:
        doc['unit_folded'] = n_unit_folded
    return doc


# --------------------------------------------------------------------------- 실행

def run_candidates_change(cfg, out, *, cnt_from_dl1_root, align_search_um, align_min_dice, align_layer_tolerance_um,
                          change_tolerance_um, window_um, merge_overlap, top_k, mask_derivation_rule,
                          merge_scope=None, suppress_overlap=None, electrode_png_root=None, local_align_um=None,
                          unit_align_um=None, apply_align=None, unit_fold=False, unit_fold_tolerance_um=None,
                          refine_pool=None):
    """새 출력 폴더에 alignment.json, candidate_queries_v1.json, funnel.json/.csv, reference_export/, target_export/,
    reference_index_export/, target_index_export/(각 폴더에 review_rois.json 과 mask_derivation.json),
    direct/(roi_correspondence.json, direct_pairs_sidecar.json), execution.json 을 쓴다.

    ``electrode_png_root`` (선택): alignment.html 층 묶음의 전극 형상 바탕을 읽는 PNG root. 생략하면 ``cnt_from_dl1_root`` 다.
    전극 목록 비교(``electrode_set_differs``, 조합 짝짓기)는 이 값과 무관하게 ``cnt_from_dl1_root`` 를 쓴다.

    ``local_align_um`` (선택, roi-detail/T2): ROI 국소 정렬 진단의 탐색 반경(µm). 주면 ``roi_local_alignment.json`` 을 더 쓴다.
    진단 전용이다: 경고·종료 코드·다른 산출 파일은 바꾸지 않는다. round(R / pitch) 가 2..30 px 가 아니면(2 px 이상이어야 거리 2 이상 후보가 있어 모호(ambiguous) 판정이 가능하다) 쓰기 전에 종료 2(``local_align_um_invalid``).

    ``unit_align_um`` (선택, layer-align/T4a): Layer 전역 교차 확인과 unit 4개 offset 의 탐색 반경(µm). 주면 alignment.json 에
    ``layer_global_check``·``unit_offsets``·``unit_geometry``·``layer_inputs`` 를 더한다(계산·순위·export 는 그대로).
    ``apply_align`` (선택, ``diagnostic`` 기본 | ``unit`` | ``local``): ``unit`` 이면 unit offset 장을 변경 지도·창 재계산·target 방향 중심·직접 쌍·CNT 변경 면적에 쓴다.
    ``local`` (layer-align/T4b) 이면 ROI 마다 Layer 층 + CNT 층 Dice 우선 국소 보정을 하고(``--local-align-um`` 필요, ``--unit-align-um`` 은 시작점용으로 선택),
    창 후보 → 점수 상위 ``refine_pool``(기본 10 × top_k) 원시 창 → 창마다 국소 보정 + 재점수화 → 재점수로 병합 → 억제 → 접기 → top_k 순서로 진행한다.
    ``unit_fold`` (선택): 억제 뒤·top_k 앞에서 서로 다른 unit 의 같은 위치 ROI 를 접는다
    (``unit_fold_tolerance_um`` 생략 시 window_um / 4).
    쓰기 전 종료 2: ``layer_folder_required``(두 모델 중 하나라도 새 구조 + Layer PNG 가 없음), ``unit_align_um_too_small``, ``unit_align_um_too_large``,
    ``apply_align_requires_unit_align``, ``apply_align_requires_local_align``, ``local_align_um_too_small_for_unit_delta``, ``refine_pool_requires_local``.

    폴더를 만들기 전에 NumericContractError(종료 코드 2)로 알리는 것: 인수 값(없는 DL1 root, 폴더가 아닌 전극 PNG root, 규칙 값 포함),
    이미 있는 출력 경로, config 종류·지름,
    side 준비 결과, 제품·frame 개수, pitch 불일치, 정렬 탐색 반경(>= 2 x pitch), 창 크기(>= pitch, frame 보다 크지 않음).
    CNT 읽기·정렬·메모리 한도(admission) 오류는 폴더를 만든 뒤에 나므로, 쓰는 도중 실패한 경우와 같이 이 실행이 만든
    파일과 폴더만 지운다. 이미 있는 출력 경로는 거부하고 건드리지 않는다.
    """
    from . import pipeline
    _validate_args(cnt_from_dl1_root, align_search_um, align_min_dice, align_layer_tolerance_um, change_tolerance_um,
                   window_um, merge_overlap, top_k, mask_derivation_rule, merge_scope, suppress_overlap, electrode_png_root,
                   local_align_um, unit_align_um, apply_align, unit_fold, unit_fold_tolerance_um, refine_pool)
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise _bad('output_dir_exists', str(out))
    for name in ('reference', 'target'):
        if not isinstance(cfg.get(name), dict):
            raise _bad('numeric_input_required', name)
    if 'map' in cfg and pipeline._map_spec(cfg).get('diameter_um', DIAMETER_UM) != DIAMETER_UM:
        raise _bad('candidates_change_diameter_fixed_5um', f"map.diameter_um={cfg['map'].get('diameter_um')!r}")
    if not all(pipeline._real_source(cfg, cfg[name]) is not None for name in ('reference', 'target')):
        raise _bad('candidates_change_requires_real_hotspot', 'reference·target 모두 real_hotspot_v1 이어야 한다')
    sides = {name: candidates.prepare_side(cfg, name, DIAMETER_UM) for name in ('reference', 'target')}
    for name, side in sides.items():
        if side.status == 'unavailable':
            raise _bad('candidates_change_side_unavailable', f'{name}: {side.reasons}')
    products = {name: _Product(name, side) for name, side in sides.items()}
    for item in products.values():
        item.check_single_frame()
    for item in products.values():
        item.bind()
    ref, tgt = products['reference'], products['target']
    pitch = ref.frame.pixel_pitch_um
    if tgt.frame.pixel_pitch_um != pitch:
        raise _bad('candidates_change_pitch_mismatch', f'{pitch} != {tgt.frame.pixel_pitch_um}')
    if float(align_search_um) < 2 * pitch - 1e-9:
        raise _bad('invalid_alignment_input', f'--align-search-um {align_search_um} < 2 x pitch ({2 * pitch})')
    window_px = change_map._round_px(window_um, pitch)
    height, width = ref.frame.valid.shape
    if window_um < pitch - 1e-9 or window_px < 1:
        raise _bad('invalid_candidates_change_argument', f'--window-um {window_um} < pitch ({pitch})')
    if window_px > height or window_px > width:
        raise _bad('invalid_candidates_change_argument',
                   f'--window-um {window_um} ({window_px} px) does not fit the reference frame ({height} x {width} px)')
    local_radius_px = None
    if local_align_um is not None and apply_align == 'local':
        local_radius_px = _check_local_apply(local_align_um, pitch, products, electrode_png_root or cnt_from_dl1_root,
                                             unit_aligned=unit_align_um is not None)
    elif local_align_um is not None:
        try:
            local_align.search_radius_px(local_align_um, pitch)
        except local_align.RadiusError as exc:
            raise _bad('local_align_um_invalid', str(exc))
    unit_radius_px = None
    if unit_align_um is not None or apply_align == 'unit':
        unit_radius_px = _check_unit_align(unit_align_um, pitch, products, electrode_png_root or cnt_from_dl1_root)
    limit = _operation_limit(sides)
    args = {'cnt_from_dl1_root': str(cnt_from_dl1_root), 'align_search_um': align_search_um,
            'align_min_dice': align_min_dice, 'align_layer_tolerance_um': align_layer_tolerance_um,
            'change_tolerance_um': change_tolerance_um, 'window_um': window_um, 'merge_overlap': merge_overlap,
            'top_k': top_k, 'max_operation_bytes': limit, 'mask_derivation_rule': mask_derivation_rule}
    if merge_scope is not None:
        args['merge_scope'] = merge_scope
    if suppress_overlap is not None:
        args['suppress_overlap'] = suppress_overlap
    if electrode_png_root is not None:
        args['electrode_png_root'] = str(electrode_png_root)
    if local_align_um is not None:
        args['local_align_um'] = local_align_um
    if unit_align_um is not None:
        args['unit_align_um'] = unit_align_um
        args['unit_align_px'] = unit_radius_px
    if apply_align is not None:
        args['apply_align'] = apply_align
    if apply_align == 'local':
        args['local_align_px'] = local_radius_px
        args['refine_pool'] = refine_pool if refine_pool is not None else 10 * top_k
    if unit_fold:
        args['unit_fold'] = True
        args['unit_fold_tolerance_um'] = window_um / 4.0 if unit_fold_tolerance_um is None else unit_fold_tolerance_um
    track, cleanup = candidates.output_tracker()
    out.mkdir()
    track(out, is_dir=True)
    try:
        return _run(cfg, out, track, sides, products, pitch, limit, args)
    except BaseException:
        cleanup()
        raise


def _layer_figure_files(root, products):
    """layer-align/T5: 두 모델 모두 새 구조(Merged_Layer) + Layer PNG 가 있으면 ``{'reference'|'target': {층: fs_rel}}``, 아니면 None(예전 그림)."""
    from .adapters.electrode_png import layout_structure, scan_layer_pngs
    files = {}
    for name in ('reference', 'target'):
        product = products[name].product_id
        if layout_structure(root, product) != 'merged_layer_v2':
            return None
        files[name] = scan_layer_pngs(root, product)
        if not files[name]:
            return None
    return files


def _layer_fingerprints(root, products, files):
    """그림에만 쓰는 Layer 의 입력 지문. ``alignment.json.layer_inputs`` 와 같은 모양 ``{모델: {'product_id', 'files': [{layer, relative_path, sha256}]}}``."""
    from .numeric_images import resolve_path
    out = {}
    for name in ('reference', 'target'):
        records = [{'layer': layer, 'relative_path': rel,
                    'sha256': hashlib.sha256(resolve_path(Path(root), rel).read_bytes()).hexdigest()}
                   for layer, rel in sorted(files[name].items(), key=lambda item: alignment_report.natural_key(item[0]))]
        out[name] = {'product_id': products[name].product_id, 'files': records}
    return out


def _figure_stack_order(sides):
    """그림용 층 쌓는 순서: 설정의 ``condition_assignment.layer_stack`` ({층: stack order}). 없으면 None(Layer 파일 번호 순)."""
    for name in ('reference', 'target'):
        try:
            return _stack_order(sides[name])
        except (NumericContractError, KeyError, TypeError):
            continue
    return None


def _collect_layer_figures(source, queries, windows, root, files, products, stack, offset_px, limit, reserved, warnings, notes):
    """layer-align/T5: 5절 위치 그림 바탕(reference Layer 블록 분율)과 ROI 주변 Layer stack crop 두 장을 모은다. 성공하면 True.

    층은 stack order 순으로 (모델, 층) 하나씩 읽어 분율·crop 에 섞고 바로 놓는다. 읽기 admission(``electrode_fits``)과 보유할 crop 바이트가
    한도를 넘거나 Layer PNG 를 읽을 수 없으면 Layer 그림만 빼고(예전 그림) 경고 ``alignment_report_layer_figures_skipped`` 를 남긴다(계산·JSON 은 그대로).
    target crop 은 그 ROI 의 적용 offset(``offset_xy_px_applied``, 없으면 전역 offset)으로 reference 좌표에 옮긴다."""
    import re
    from .adapters.electrode_png import load_layer_png
    if not source.crop_boxes or source.observed is None:
        return False
    ref_shape, tgt_shape = products['reference'].frame.valid.shape, products['target'].frame.valid.shape
    crop_bytes = sum(6 * (y1 - y0) * (x1 - x0) for y0, y1, x0, x1 in source.crop_boxes)
    need = alignment_report.electrode_read_bytes(ref_shape, tgt_shape) + crop_bytes
    if need > limit - reserved - source.held_bytes():
        warnings.append({'code': 'alignment_report_layer_figures_skipped', 'estimated_bytes': need, 'reserved_bytes': reserved,
                         'max_operation_bytes': limit})
        return False
    numbers = {}
    for name in ('target', 'reference'):      # 두 모델의 파일 번호가 다르면 reference 를 따른다
        for layer, rel in files[name].items():
            match = re.match(r'(\d+)-', Path(rel).name)
            numbers[layer] = int(match.group(1)) if match else 0
    style = alignment_report.stack_style(numbers, stack)
    if style['not_in_stack']:
        notes.append({'code': alignment_report.LAYER_NOT_IN_STACK, 'severity': 'info', 'layers': list(style['not_in_stack']),
                      'detail': 'figure only: these Layer files are stacked on top by file number'})
    offsets = []
    for i in range(len(source.crop_boxes)):
        query = queries[i] if i < len(queries) else {}
        applied = query.get('offset_xy_px_applied')
        offsets.append(((int(applied[0]), int(applied[1])), query.get('offset_source', 'global')) if applied is not None
                       else ((int(offset_px[0]), int(offset_px[1])), 'global'))

    def load(side, layer):
        name = 'reference' if side == 'ref' else 'target'
        rel = files[name].get(layer)
        if rel is None:
            return None
        return load_layer_png(root, products[name].product_id, products[name].frame, rel)
    try:
        source.collect_layers(style, load, offsets)
    except NumericContractError as exc:
        source.layer_fraction.clear()
        source.layer_crop.clear()
        source.layer_style = None
        warnings.append({'code': 'alignment_report_layer_figures_skipped', 'reason': getattr(exc, 'code', str(exc))})
        return False
    return True


def _collect_layer_bases(source, queries, windows, root, files, products, limit, reserved, warnings, diag=None, layer_files=None):
    """alignment.html 5절 층 묶음의 전극 형상 바탕(DDDDDD)을 읽어 ROI 상자 crop 으로 남긴다.

    그리는 층(``alignment_report.layer_bundles``)만, (모델, 층) 하나씩 frame bool 로 읽고 바로 놓는다. 메모리 추정(``electrode_fits``)이
    한도를 넘으면 바탕만 빼고 경고 ``alignment_report_layer_base_skipped`` 를 남긴다(계산·JSON 은 그대로, 종료 코드 3).
    읽은 파일의 sha256 은 기록하지 않는다: 이 PNG 는 보고서 그림에만 쓰이고 계산·export·JSON 에 들어가지 않으므로 입력 지문이 아니다.

    ``diag`` (국소 정렬 진단, 선택): ``{'r_px': r}`` 를 주면, 바탕 admission 이 통과한 경우에 한해 별도 admission(``local_align.estimate_bytes``)을 해서
    통과하면 같은 읽기에서 넓은 target crop 도 남긴다. 못 하면 ``diag['skip']`` 에 사유를 적는다. 바탕 admission·경고는 diag 와 무관하다."""
    from .adapters.electrode_png import load_electrode_layer, load_layer_png
    wanted = {}
    for i, (query, window) in enumerate(zip(queries, windows)):
        if i >= len(source.hot):
            break
        for bundle in alignment_report.layer_bundles(query, window)[0]:
            wanted.setdefault(bundle['layer'], []).append(i)
    if not wanted:
        return
    ref_shape, tgt_shape = products['reference'].frame.valid.shape, products['target'].frame.valid.shape
    held = source.held_bytes()
    if not alignment_report.electrode_fits(ref_shape, tgt_shape, limit, reserved, held):
        warnings.append({'code': 'alignment_report_layer_base_skipped',
                         'estimated_bytes': alignment_report.electrode_read_bytes(ref_shape, tgt_shape),
                         'reserved_bytes': reserved, 'max_operation_bytes': limit})
        for layer in wanted:
            for side in ('ref', 'tgt'):
                source.base_reason[(layer, side)] = '읽기 생략: 메모리 한도'
        if diag is not None:
            diag['skip'] = 'electrode_bases_skipped'
        return
    margin = 0
    if diag is not None:
        r = diag['r_px']
        boxes = []
        base_bytes = 0
        for layer, rois in wanted.items():
            for i in rois:
                y0, y1, x0, x1 = source.hot[i]['box']
                base_bytes += 2 * (y1 - y0) * (x1 - x0)
        for i in sorted({i for rois in wanted.values() for i in rois}):
            y0, y1, x0, x1 = source.hot[i]['box']
            boxes.append((y1 - y0, x1 - x0))
        n_cnt = len(set(source.ref_layers) & set(source.tgt_layers))
        need = local_align.estimate_bytes(boxes, len(wanted), n_cnt, r)
        room = limit - reserved - held - base_bytes - alignment_report.electrode_read_bytes(ref_shape, tgt_shape)
        if need > room:
            diag['skip'] = 'diagnostic_memory_limit'
            diag['estimated_bytes'] = need
            diag['available_bytes'] = max(int(room), 0)
        else:
            margin = r

    def loader(name, side):
        item = products[name]

        def load(layer):
            layer_rel = layer_files[name].get(layer) if layer_files is not None and diag is None else None
            if layer_rel is not None:      # layer-align/T5: 그 층의 Layer PNG 가 있으면 Merged 합집합 대신 Layer 를 바탕으로 쓴다(진단 실행은 예전 바탕 그대로)
                try:
                    union = load_layer_png(root, item.product_id, item.frame, layer_rel)
                except NumericContractError as exc:
                    source.base_reason[(layer, side)] = '읽기 실패: ' + exc.code
                    return None
                source.base_kind[(layer, side)] = 'Layer'
                return union
            names = files[name].get(layer)
            if layer_files is not None and diag is None:
                source.base_kind[(layer, side)] = 'Merged'
            if not names:
                folder = (Path(root) / item.product_id).is_dir()
                source.base_reason[(layer, side)] = '파일 없음' if folder else '제품 폴더 없음'
                return None
            errors = []
            union = load_electrode_layer(root, item.product_id, item.frame, names, errors)
            if diag is not None:
                source.base_errors[(layer, side)] = sorted(set(errors))
            if union is None:
                source.base_reason[(layer, side)] = '읽기 실패: ' + (errors[0] if errors else 'unreadable')
            return union
        return load
    source.collect_bases(wanted, loader('reference', 'ref'), loader('target', 'tgt'), diag_margin=margin)


def _pad_crop(array, y0, y1, x0, x1):
    """frame 배열의 상자 crop. 배열이 상자보다 작으면 모자란 쪽은 False."""
    crop = np.array(array[y0:y1, x0:x1], dtype=bool)
    if crop.shape != (y1 - y0, x1 - x0):
        full = np.zeros((y1 - y0, x1 - x0), dtype=bool)
        full[:crop.shape[0], :crop.shape[1]] = crop
        crop = full
    return crop


def _local_alignment_document(source, state, queries, windows, pitch, args, offset_px, frame_shape):
    """``roi_local_alignment.json`` 내용(roi-detail/T2, 진단 전용). source 가 None 이면(그림 생략) 전 ROI 를 skipped 로 쓴다.

    ROI 하나씩 crop 을 만들어 ``local_align.diagnose_roi`` 에 넘기고 바로 놓는다. 층 키는 출처 접두(``electrode:<층>``, ``cnt:<층>``)에 자연 정렬이다.
    전극 PNG 오류가 있거나 한쪽 모델에만 있는 층은 진단에서 빼고 ``excluded_layers`` 에 적는다. 바탕 그림·바탕 admission 은 바꾸지 않는다."""
    r = state['r_px']
    tolerance, min_dice = float(args['align_layer_tolerance_um']), float(args['align_min_dice'])
    dx, dy = offset_px
    doc = {'schema': local_align.SCHEMA, 'search_um': args['local_align_um'], 'search_px': r, 'tolerance_um': tolerance,
           'min_dice': min_dice, 'global_offset_xy_px': [dx, dy], 'layers_basis': local_align.LAYERS_BASIS,
           'min_support_px': local_align.MIN_SUPPORT_PX, 'min_ring_support_px': local_align.MIN_RING_SUPPORT_PX,
           'ambiguity_delta': float(local_align.AMBIGUITY_DELTA), 'excluded_layers': [], 'rois': []}
    global_skip = 'images_skipped' if source is None else state.get('skip')
    if global_skip == 'diagnostic_memory_limit':
        doc['diagnostic_estimated_bytes'] = int(state['estimated_bytes'])
        doc['diagnostic_available_bytes'] = int(state['available_bytes'])
    bundle_layers = []
    for i, (query, window) in enumerate(zip(queries, windows)):
        bundle_layers.append([b['layer'] for b in alignment_report.layer_bundles(query, window)[0]])
    excluded, cnt_names = {}, []
    if source is not None and global_skip is None:
        for layer in sorted({layer for layers in bundle_layers for layer in layers}, key=alignment_report.natural_key):
            sides = {side: any(source.base.get((i, layer, side)) is not None for i, layers in enumerate(bundle_layers) if layer in layers)
                     for side in ('ref', 'tgt')}
            errors = sorted(set(source.base_errors.get((layer, 'ref'), [])) | set(source.base_errors.get((layer, 'tgt'), [])))
            if errors:
                excluded['electrode:' + layer] = 'png_errors:' + ','.join(errors)
            elif not (sides['ref'] and sides['tgt']):
                excluded['electrode:' + layer] = 'one_sided' if (sides['ref'] or sides['tgt']) else 'missing_both'
        for layer in sorted(set(source.ref_layers) | set(source.tgt_layers), key=alignment_report.natural_key):
            if layer in source.ref_layers and layer in source.tgt_layers:
                cnt_names.append(layer)
            else:
                excluded['cnt:' + layer] = 'one_sided'
        doc['excluded_layers'] = [{'key': k, 'reason': excluded[k]} for k in sorted(excluded, key=alignment_report.natural_key)]
    ok_points = []
    for i, window in enumerate(windows):
        y, x, w = window['window_origin_px'][0], window['window_origin_px'][1], window['window_px']
        item = {'rank': i + 1, 'query_id': queries[i]['query_id'] if i < len(queries) else None,
                'box_px': None, 'window_origin_px': [int(y), int(x)], 'window_px': int(w), 'layers_used': [],
                'status': 'skipped', 'reason': global_skip, 'flags': [], 'local_offset_xy_px': None, 'local_offset_xy_um': None,
                'delta_from_global_xy_um': None, 'delta_from_global_um': None, 'dice_global': None, 'dice_local': None,
                'ring_delta_xy_px': None, 'ring_dice': None, 'ring_reason': None, 'per_layer': {}}
        doc['rois'].append(item)
        if global_skip is not None:
            continue
        y0, y1, x0, x1 = source.hot[i]['box']
        item['box_px'] = [int(y0), int(y1), int(x0), int(x1)]
        el_layers = [layer for layer in bundle_layers[i] if 'electrode:' + layer not in excluded
                     and source.diag_wide.get((i, layer)) is not None and source.base.get((i, layer, 'ref')) is not None]
        if not bundle_layers[i]:
            item['reason'] = 'no_bundle_layers'
            continue
        keys = sorted(['electrode:' + layer for layer in el_layers] + ['cnt:' + layer for layer in cnt_names],
                      key=alignment_report.natural_key)
        if not keys:
            item['reason'] = 'no_layers'
            continue
        ref_layers, wide_layers = {}, {}
        for key in keys:
            kind, layer = key.split(':', 1)
            if kind == 'electrode':
                ref_layers[key], wide_layers[key] = source.base[(i, layer, 'ref')], source.diag_wide[(i, layer)]
            else:
                ref_layers[key] = _pad_crop(source.ref_layers[layer], y0, y1, x0, x1)
                wide_layers[key] = change_map._shift_crop(source.tgt_layers[layer], dy, dx, y0 - r, y1 + r, x0 - r, x1 + r)
        ref_valid = _pad_crop(source.ref_valid, y0, y1, x0, x1)
        tgt_valid = change_map._shift_crop(source.tgt_valid, dy, dx, y0 - r, y1 + r, x0 - r, x1 + r)
        item['layers_used'] = [{'layer': key.split(':', 1)[1], 'source': key.split(':', 1)[0]} for key in keys]
        got = local_align.diagnose_roi(ref_layers, wide_layers, ref_valid, tgt_valid, (y - y0, y + w - y0, x - x0, x + w - x0), r,
                                       pitch_um=pitch, tolerance_um=tolerance, min_dice=min_dice)
        item.update({'status': got['status'], 'reason': got['reason'], 'flags': got['flags'], 'dice_global': got['dice_global'],
                     'dice_local': got['dice_local'], 'ring_dice': got['ring_dice'], 'ring_reason': got['ring_reason'],
                     'per_layer': got['per_layer']})
        if got['d_yx'] is not None:
            ddy, ddx = got['d_yx']
            item['local_offset_xy_px'] = [dx + ddx, dy + ddy]
            item['local_offset_xy_um'] = [local_align._r6((dx + ddx) * pitch), local_align._r6((dy + ddy) * pitch)]
            item['delta_from_global_xy_um'] = [local_align._r6(ddx * pitch), local_align._r6(ddy * pitch)]
            item['delta_from_global_um'] = local_align._r6(math.hypot(ddx * pitch, ddy * pitch))
        if got['ring_d_yx'] is not None:
            item['ring_delta_xy_px'] = [got['ring_d_yx'][1], got['ring_d_yx'][0]]
        if got['status'] == 'ok':
            ok_points.append((x + w / 2.0, y + w / 2.0, item['local_offset_xy_um'][0], item['local_offset_xy_um'][1]))
    fit, fit_reason = local_align.linear_fit(ok_points, frame_shape, pitch)
    if global_skip is not None or not doc['rois']:
        fit, fit_reason = None, 'no_diagnosed_rois'
    doc['linear_fit'], doc['linear_fit_reason'] = fit, fit_reason
    counts = {status: 0 for status in local_align.STATUSES}
    for item in doc['rois']:
        counts[item['status']] += 1
    doc['summary'] = {'n_rois': len(doc['rois']), **counts}
    differs = [item['rank'] for item in doc['rois'] if 'local_offset_differs' in item['flags']]
    doc['notes'] = ([{'code': 'roi_local_offset_differs', 'severity': 'info', 'tolerance_um': tolerance, 'ranks': differs}]
                    if differs else [])
    return doc


def _unit_alignment(products, cnt, g, radius, args, layer_root, limit, reserved, pitch):
    """layer-align/T4a: Layer 층 + CNT 층 Dice 로 (a) 전역 교차 확인과 (b) unit 4개 offset 을 구한다. 파일은 읽기만 한다.

    층은 하나씩 읽어 (frame 전체 점수 창, unit 영역별 점수 창)만 남기고 바로 놓는다. 키는 ``layer:<이름>`` / ``cnt:<이름>``.
    한쪽 모델에만 있는 층은 읽지 않고 ``one_sided`` 로 뺀다. 반환: ``doc``(alignment.json 에 더할 키), ``field``(offset 장),
    ``effective``/``source``({(iy, ix): ...}), ``notes``, ``timings_s``."""
    import time
    from .adapters.electrode_png import load_layer_png, scan_layer_pngs
    from .numeric_images import resolve_path
    started = time.perf_counter()
    read_s = 0.0
    ref, tgt = products['reference'], products['target']
    ref_shape, tgt_shape = tuple(ref.frame.valid.shape), tuple(tgt.frame.valid.shape)
    geometry = {'reference': layer_align.UnitGeometry(ref_shape), 'target': layer_align.UnitGeometry(tgt_shape)}
    regions = geometry['reference'].unit_regions()
    order = sorted(regions)
    files = {name: scan_layer_pngs(layer_root, products[name].product_id) for name in ('reference', 'target')}
    layer_names = sorted(set(files['reference']) & set(files['target']), key=alignment_report.natural_key)
    cnt_names = sorted(set(cnt['reference']['layers']) & set(cnt['target']['layers']), key=alignment_report.natural_key)
    excluded = {}
    for name in sorted(set(files['reference']) ^ set(files['target'])):
        excluded['layer:' + name] = 'one_sided'
    for name in sorted(set(cnt['reference']['layers']) ^ set(cnt['target']['layers'])):
        excluded['cnt:' + name] = 'one_sided'
    # admission: 층 1개의 FFT 작업 공간 + 남기는 점수 창(층마다 frame 전체 1 + unit 4, 배열 3개)
    # estimate_layer_bytes 는 실측(3000x3000, R=20: 추정 501 MiB, 실제 층 작업 +806 MiB)보다 작아서 2배로 잡는다(float64 변환·패딩 사본).
    kept_windows = (len(layer_names) + len(cnt_names)) * (1 + len(order)) * 3 * (2 * radius + 1) ** 2 * 8
    need = 2 * int(layer_align.estimate_layer_bytes(ref_shape, tgt_shape, radius)) + kept_windows
    change_map._check_admission(need, limit, reserved)
    omega = layer_align.fixed_omega(ref.frame.valid, tgt.frame.valid, g, radius, radius)
    global_scores, unit_scores = {}, {q: {} for q in order}
    inputs = {name: {'product_id': products[name].product_id, 'files': []} for name in ('reference', 'target')}

    def add(key, ref_array, tgt_array):
        a, inter, b = layer_align.unit_window_scores(ref_array, tgt_array, omega, g, radius)
        global_scores[key] = {'a': a, 'inter': inter, 'b': b}
        for q in order:
            a, inter, b = layer_align.region_window_scores(ref_array, tgt_array, omega, regions[q], g, radius)
            unit_scores[q][key] = {'a': a, 'inter': inter, 'b': b}

    for name in layer_names:
        loaded = {}
        for side in ('reference', 'target'):
            began = time.perf_counter()
            rel = files[side][name]
            loaded[side] = load_layer_png(layer_root, products[side].product_id, products[side].frame, rel)
            digest = hashlib.sha256(resolve_path(Path(layer_root), rel).read_bytes()).hexdigest()
            read_s += time.perf_counter() - began
            inputs[side]['files'].append({'layer': name, 'relative_path': rel, 'sha256': digest})
        add('layer:' + name, loaded['reference'], loaded['target'])
        loaded = None
    for name in cnt_names:
        add('cnt:' + name, cnt['reference']['layers'][name], cnt['target']['layers'][name])
    min_dice = float(args['align_min_dice'])
    check = layer_align.choose_dice_first(global_scores, g, radius, min_dice=min_dice, excluded=excluded)
    notes = []
    delta_um = None
    if check['delta_xy_px'] is not None:
        delta_um = round(math.hypot(check['delta_xy_px'][0] * pitch, check['delta_xy_px'][1] * pitch), 6)
    tolerance = float(args['align_layer_tolerance_um'])
    if check['status'] == 'ok' and delta_um is not None and delta_um > tolerance + 1e-9:
        notes.append({'code': 'layer_global_check_differs', 'severity': 'info', 'tolerance_um': tolerance,
                      'delta_from_cnt_um': delta_um, 'layer_offset_xy_px': check['offset_xy_px'], 'cnt_offset_xy_px': [g[0], g[1]]})
    global_doc = {k: check[k] for k in ('status', 'flags', 'offset_xy_px', 'delta_xy_px', 'decided_by_layer', 'layer_order',
                                         'layers_disagreeing', 'best_dice', 'dice', 'ambiguity_set_size_initial',
                                         'ambiguity_set_size', 'per_layer')}
    global_doc.update({'applied': False, 'search_px': radius, 'search_um': round(radius * pitch, 6),
                       'cnt_offset_xy_px': [g[0], g[1]], 'delta_from_cnt_um': delta_um,
                       'omega_pixels': int(np.count_nonzero(omega)),
                       'layers_excluded': {k: excluded[k] for k in sorted(excluded, key=alignment_report.natural_key)}})
    units, field, effective, source = [], [], {}, {}
    for q in order:
        res = layer_align.choose_dice_first(unit_scores[q], g, radius, min_dice=min_dice, excluded=excluded)
        applied = bool(res['apply'])
        offset = (int(res['offset_xy_px'][0]), int(res['offset_xy_px'][1])) if applied else (int(g[0]), int(g[1]))
        y0, y1, x0, x1 = regions[q]
        effective[q], source[q] = offset, 'unit' if applied else 'global'
        field.append((y0, y1, x0, x1, offset[0], offset[1]))
        record = {k: res[k] for k in ('status', 'flags', 'decided_by_layer', 'layer_order', 'layers_disagreeing', 'best_dice', 'dice',
                                      'ambiguity_set_size_initial', 'ambiguity_set_size', 'per_layer')}
        record.update({'unit_index_xy': [q[1], q[0]], 'region_px': [y0, y1, x0, x1], 'offset_xy_px': list(offset),
                       'offset_xy_um': [round(offset[0] * pitch, 6), round(offset[1] * pitch, 6)],
                       'delta_xy_px': [offset[0] - g[0], offset[1] - g[1]], 'applied': applied,
                       'candidate_offset_xy_px': res['offset_xy_px'], 'omega_pixels': int(np.count_nonzero(omega[y0:y1, x0:x1]))})
        units.append(record)
    delta = layer_align.delta_u(geometry['reference'], geometry['target'])
    doc = {'layer_global_check': global_doc, 'unit_offsets': units,
           'unit_geometry': {'border_px': layer_align.BORDER_PX, 'unit_px_reference_yx': list(geometry['reference'].unit_px),
                             'unit_px_target_yx': list(geometry['target'].unit_px), 'delta_u_yx_px': list(delta),
                             'boundary_px_reference_yx': list(geometry['reference'].boundary_px()),
                             'boundary_px_target_yx': list(geometry['target'].boundary_px()),
                             'unit_size_odd_px': bool(geometry['reference'].unit_size_odd_px or geometry['target'].unit_size_odd_px),
                             'search_radius_px': radius, 'required_radius_px': layer_align.required_unit_radius_px(max(abs(delta[0]), abs(delta[1]))),
                             'frame_anchor': 'frame_shape_midpoint'},
           'layer_inputs': inputs}
    return {'doc': doc, 'field': field, 'effective': effective, 'source': source, 'notes': notes, 'geometry': geometry['reference'],
            'timings_s': {'unit_align': round(time.perf_counter() - started, 3), 'layer_read': round(read_s, 3)}}


def _fold_merged(merged, by_id, geometry, tolerance_um, pitch):
    """억제 뒤 순위 목록을 reference unit 으로 접는다(``layer_align.fold_rois``). (남은 목록, {대표 region_id: unit_fold 기록})."""
    items = []
    for rank, entry in enumerate(merged, start=1):
        rep = entry['representative']
        window = by_id[rep.region_id]
        half = window['window_px'] // 2
        items.append({'region_id': rep.region_id, 'rank': rank,
                      'center_yx_px': (window['window_origin_px'][0] + half, window['window_origin_px'][1] + half)})
    kept, replicas = layer_align.fold_rois(items, geometry, tolerance_um / pitch)
    by_region = {entry['representative'].region_id: entry for entry in merged}

    def folded_um(yx):
        return [round(yx[1] * pitch, 6), round(yx[0] * pitch, 6)]

    out, info = [], {}
    for item in kept:
        entry = by_region[item['region_id']]
        center = (int(item['center_yx_px'][0]), int(item['center_yx_px'][1]))
        records = []
        for rep in replicas[item['region_id']]:
            other = by_region[rep['region_id']]
            records.append({'region_id': rep['region_id'], 'rank_before_fold': rep['rank'],
                            'center_xy_um': [float(v) for v in other['representative'].center_xy_um],
                            'unit_index_xy': [rep['unit_index'][1], rep['unit_index'][0]],
                            'folded_center_xy_um': folded_um(rep['folded_yx_px']),
                            'distance_um': round(rep['distance_px'] * pitch, 6),
                            'score_um2': other['representative'].score,
                            'layers': sorted({m.layer for m in other['members']})})
        idx = geometry.unit_index(center)
        info[item['region_id']] = {'folded_center_xy_um': folded_um(geometry.unit_coord(center)),
                                   'unit_index_xy': [idx[1], idx[0]], 'n_replicas': len(records), 'replicas': records}
        out.append(entry)
    return out, info


def _recomputed_windows(windows, results, pitch):
    """``case_contributions(window_offsets=...)`` 가 다시 센 창 값(합집합·sum_residual·관측 비율·flags)으로 창 dict 를 새로 만든다.

    ``window_candidates`` 와 같은 정의·같은 곱셈(셀 수 × pitch²)이다."""
    area = pitch * pitch
    out = []
    for window, result in zip(windows, results):
        cells, n_ref, n_tgt = result['union_change_cells'], result['union_ref_cells'], result['union_tgt_cells']
        flags = list(result['flags']) + ([change_map.FLAG_UNION_UNCHANGED] if cells == 0 else [])
        out.append({**window, 'score_um2': cells * area, 'sum_residual_cells': result['sum_residual_cells'],
                    'sum_residual_um2': result['sum_residual_cells'] * area, 'union_area_reference_um2': n_ref * area,
                    'union_area_target_um2': n_tgt * area, 'union_delta_um2': (n_tgt - n_ref) * area,
                    'observed_fraction': result['observed_fraction'], 'flags': flags})
    return out


LOCAL_LAYERS_BASIS = 'layer_png_per_layer_dice_first'
LOCAL_APPLY_POLICY = 'top_n_pool_local_dice_first_rescore_then_merge_suppress_fold_top_k'
CROSSES_BOUNDARY_FLAG = 'crosses_unit_boundary_field_used'


def _maps_bytes(maps):
    """``change_maps`` 결과가 보유한 배열 바이트(국소 보정 동안 admission 한도에서 뺀다)."""
    total = int(np.asarray(maps['D']).nbytes)
    for key in ('maps', 'union_change', 'union_ref', 'union_tgt'):
        total += sum(int(np.asarray(a).nbytes) for a in maps[key].values())
    return total


def _crosses_unit(window, geometry):
    """창(정사각)이 reference unit 경계를 넘는가: 왼쪽 위 칸과 오른쪽 아래 칸의 unit 번호가 다르다."""
    y, x = window['window_origin_px']
    w = window['window_px']
    return geometry.unit_index((y, x)) != geometry.unit_index((y + w - 1, x + w - 1))


def _local_corrections(windows, products, cnt, layer_root, g, unit, radius, args, pitch, limit, reserved):
    """layer-align/T4b: 원시 창 풀(``windows``)의 창마다 국소 offset 을 구한다. 파일은 읽기만 한다.

    창마다 상자(``alignment_report._crop_box``) 안에서, 시작점 s(창 중심 unit 의 적용 offset, unit 정렬이 없으면 전역 g)에서 반경 ``radius`` 안의 상대 이동 δ 를
    Layer 층 + CNT 층(키 ``layer:`` / ``cnt:``)의 Dice 우선 규칙(``layer_align.choose_dice_first``)으로 고른다. 점수는 상자 crop FFT 다.
    Ω = 상자 안 reference valid ∧ 모든 후보 δ 에서 target valid(``local_align.valid_domain``), unit 정렬이 있으면 창 중심 unit 영역으로 한정한다.
    선택이 ok 이면 링(창 둘레 띠) 검사를 한다: 링 판정 불가면 적용하지 않고, 링과 어긋나면 ``change_biased`` 플래그를 달되 적용한다.
    적용하지 않는 창은 unit offset(없으면 전역 g)을 그대로 쓴다. unit 경계를 넘는 창은 국소 보정을 하지 않는다(field 사용).

    반환: ``{'records': [...], 'excluded': {...}, 'layer_files': {...}, 'timings_s': {...}}``. 층 파일은 하나씩 읽고 상자 crop 만 남긴다."""
    import time
    from .adapters.electrode_png import load_layer_png, scan_layer_pngs
    from .numeric_images import resolve_path
    started = time.perf_counter()
    read_s = 0.0
    ref, tgt = products['reference'], products['target']
    height, width = ref.frame.valid.shape
    r = int(radius)
    geometry = unit['geometry'] if unit is not None else None
    regions = geometry.unit_regions() if geometry is not None else None
    min_dice = float(args['align_min_dice'])
    tolerance = float(args['align_layer_tolerance_um'])
    files = {name: scan_layer_pngs(layer_root, products[name].product_id) for name in ('reference', 'target')}
    layer_names = sorted(set(files['reference']) & set(files['target']), key=alignment_report.natural_key)
    cnt_names = sorted(set(cnt['reference']['layers']) & set(cnt['target']['layers']), key=alignment_report.natural_key)
    excluded = {}
    for name in sorted(set(files['reference']) ^ set(files['target'])):
        excluded['layer:' + name] = 'one_sided'
    for name in sorted(set(cnt['reference']['layers']) ^ set(cnt['target']['layers'])):
        excluded['cnt:' + name] = 'one_sided'
    keys = ['layer:' + n for n in layer_names] + ['cnt:' + n for n in cnt_names]
    plan = []
    for window in windows:
        y, x = window['window_origin_px']
        w = window['window_px']
        box = alignment_report._crop_box(y, x, w, height, width)
        if geometry is not None:
            idx = geometry.unit_index((y + w // 2, x + w // 2))
            start, source = unit['effective'][idx], unit['source'][idx]
        else:
            idx, start, source = None, (int(g[0]), int(g[1])), 'global'
        plan.append({'box': box, 'idx': idx, 'start': (int(start[0]), int(start[1])), 'start_source': source,
                     'crossing': geometry is not None and _crosses_unit(window, geometry), 'window': (int(y), int(x), int(w))})
    active = [i for i, p in enumerate(plan) if not p['crossing']]
    # admission: 보유 crop(창·층마다 상자 + 넓은 상자, bool) + 가장 큰 창 하나의 FFT 작업 공간
    held = 0
    biggest = (0, 0)
    for i in active:
        y0, y1, x0, x1 = plan[i]['box']
        held += len(keys) * ((y1 - y0) * (x1 - x0) + (y1 - y0 + 2 * r) * (x1 - x0 + 2 * r))
        biggest = max(biggest, (y1 - y0, x1 - x0))
    if active and keys:
        change_map._check_admission(held + int(layer_align.estimate_layer_bytes(biggest, biggest, r, n_regions=2)), limit, reserved)
    ref_crops = {i: {} for i in active}
    wide_crops = {i: {} for i in active}
    layer_files = {name: {'product_id': products[name].product_id, 'files': []} for name in ('reference', 'target')}

    def put(key, side, array):
        for i in active:
            y0, y1, x0, x1 = plan[i]['box']
            if side == 'reference':
                ref_crops[i][key] = _pad_crop(array, y0, y1, x0, x1)
            else:
                sx, sy = plan[i]['start']
                wide_crops[i][key] = change_map._shift_crop(array, sy, sx, y0 - r, y1 + r, x0 - r, x1 + r)

    for name in layer_names:
        for side in ('reference', 'target'):
            began = time.perf_counter()
            rel = files[side][name]
            frame = load_layer_png(layer_root, products[side].product_id, products[side].frame, rel)
            digest = hashlib.sha256(resolve_path(Path(layer_root), rel).read_bytes()).hexdigest()
            read_s += time.perf_counter() - began
            layer_files[side]['files'].append({'layer': name, 'relative_path': rel, 'sha256': digest})
            put('layer:' + name, side, frame)
            frame = None
    for name in cnt_names:
        put('cnt:' + name, 'reference', cnt['reference']['layers'][name])
        put('cnt:' + name, 'target', cnt['target']['layers'][name])
    records = []
    for i, p in enumerate(plan):
        y, x, w = p['window']
        y0, y1, x0, x1 = p['box']
        sx, sy = p['start']
        rec = {'box': [int(y0), int(y1), int(x0), int(x1)], 'start': [sx, sy], 'start_source': p['start_source'],
               'unit_index_xy': None if p['idx'] is None else [p['idx'][1], p['idx'][0]], 'crossing': p['crossing'],
               'status': 'skipped', 'reason': None, 'flags': [], 'applied': False, 'keys': [], 'choice': None, 'omega_pixels': None,
               'dice_start': None, 'ring': None}
        records.append(rec)
        if p['crossing']:
            rec['reason'] = CROSSES_BOUNDARY_FLAG
            rec['flags'] = [CROSSES_BOUNDARY_FLAG]
            continue
        if not keys:
            rec['reason'] = 'no_layers'
            continue
        valid_ref = _pad_crop(ref.frame.valid, y0, y1, x0, x1)
        valid_tgt = change_map._shift_crop(tgt.frame.valid, sy, sx, y0 - r, y1 + r, x0 - r, x1 + r)
        omega = local_align.valid_domain(valid_ref, valid_tgt, r)
        if regions is not None:      # 창 중심 unit 영역 밖(옆 unit 의 구조)은 점수에 넣지 않는다
            ry0, ry1, rx0, rx1 = regions[p['idx']]
            # unit 내부 경계 쪽만 r px 침식한다: 거기서는 target 표본 x+s+δ 가 이웃 unit 의 형상(다른 offset)을 읽는다. frame 가장자리는 그대로.
            if ry0 > 0:
                ry0 += r
            if ry1 < height:
                ry1 -= r
            if rx0 > 0:
                rx0 += r
            if rx1 < width:
                rx1 -= r
            inside =np.zeros(omega.shape, dtype=bool)
            inside[max(ry0 - y0, 0):max(ry1 - y0, 0), max(rx0 - x0, 0):max(rx1 - x0, 0)] = True
            omega &= inside
        rec['omega_pixels'] = int(np.count_nonzero(omega))
        rec['keys'] = list(keys)
        scores = {}
        for key in keys:
            a, inter, b = layer_align.unit_window_scores(ref_crops[i][key], wide_crops[i][key], omega, (r, r), r)
            scores[key] = {'a': a, 'inter': inter, 'b': b}
        choice = layer_align.choose_dice_first(scores, (sx, sy), r, min_dice=min_dice)
        rec['choice'] = choice
        rec['flags'] = list(choice['flags'])
        status = choice['status']
        if choice['layer_order']:
            first = scores[choice['layer_order'][0]]
            frac = layer_align._dice_frac(first['inter'][r, r], first['a'], first['b'][r, r])
            rec['dice_start'] = None if frac is None else layer_align._r6(frac)
        if status == 'ok':
            ring = omega & local_align.ring_mask(omega.shape, (y - y0, y + w - y0, x - x0, x + w - x0))
            ring_scores = [layer_align.unit_window_scores(ref_crops[i][key], wide_crops[i][key], ring, (r, r), r) for key in keys]
            pooled = local_align.pooled(ring_scores)
            ring_pick = local_align.choose(*pooled, r, min_support=local_align.MIN_RING_SUPPORT_PX, min_dice=min_dice)
            rec['ring'] = {'status': ring_pick['status'], 'dice': ring_pick['dice'],
                           'delta_xy_px': None if ring_pick['d'] is None else [ring_pick['d'][1], ring_pick['d'][0]]}
            if ring_pick['status'] != 'ok':
                status = 'ring_inconclusive'
            else:
                chosen = choice['delta_xy_px']
                gap_um = math.hypot((chosen[0] - ring_pick['d'][1]) * pitch, (chosen[1] - ring_pick['d'][0]) * pitch)
                if gap_um > tolerance:
                    status = 'change_biased'
                    rec['flags'].append('change_biased')
            if math.hypot(choice['delta_xy_px'][0] * pitch, choice['delta_xy_px'][1] * pitch) > tolerance:
                rec['flags'].append('local_offset_differs')
        rec['status'], rec['reason'] = status, (None if status in ('ok', 'change_biased') else status)
        rec['applied'] = status in ('ok', 'change_biased')
        ref_crops[i] = wide_crops[i] = None      # 이 창의 crop 은 더 쓰지 않는다
    return {'records': records, 'excluded': excluded, 'layer_files': layer_files,
            'timings_s': {'local_refine': round(time.perf_counter() - started, 3), 'local_layer_read': round(read_s, 3)}}


def _local_decisions(records, unit, g):
    """창마다 (재점수용 offset 사양, 적용 offset, 출처)를 정한다. 사양: 국소 적용이면 스칼라 (dx, dy), 아니면 unit field(없으면 전역 스칼라).

    출처: ``local`` | ``unit``(unit 정렬이 적용된 unit, 또는 field 를 쓴 경계 창) | ``global``."""
    out = []
    for rec in records:
        start = (rec['start'][0], rec['start'][1])
        if rec['applied']:
            offset = (rec['choice']['offset_xy_px'][0], rec['choice']['offset_xy_px'][1])
            out.append({'spec': offset, 'offset': offset, 'source': 'local'})
        elif unit is not None:
            out.append({'spec': unit['field'], 'offset': start, 'source': 'unit' if rec['crossing'] else rec['start_source']})
        else:
            out.append({'spec': (int(g[0]), int(g[1])), 'offset': start, 'source': 'global'})
    return out


def _local_apply_document(records, decisions, queries, windows, pitch, args, offset_px, frame_shape, excluded):
    """``roi_local_alignment.json`` (``--apply-align local``): 최종 ROI(순위 순)마다 국소 보정 기록과 적용한 offset."""
    r = int(args['local_align_px'])
    tolerance, min_dice = float(args['align_layer_tolerance_um']), float(args['align_min_dice'])
    dx, dy = offset_px
    doc = {'schema': local_align.SCHEMA, 'search_um': args['local_align_um'], 'search_px': r, 'tolerance_um': tolerance,
           'min_dice': min_dice, 'global_offset_xy_px': [dx, dy], 'layers_basis': LOCAL_LAYERS_BASIS,
           'min_support_px': local_align.MIN_SUPPORT_PX, 'min_ring_support_px': local_align.MIN_RING_SUPPORT_PX,
           'ambiguity_delta': float(local_align.AMBIGUITY_DELTA), 'refine_pool': args['refine_pool'],
           'excluded_layers': [{'key': k, 'reason': excluded[k]} for k in sorted(excluded, key=alignment_report.natural_key)],
           'rois': []}
    ok_points = []
    for i, (rec, decision, window) in enumerate(zip(records, decisions, windows)):
        y, x = window['window_origin_px']
        w = window['window_px']
        choice = rec['choice']
        item = {'rank': i + 1, 'query_id': queries[i]['query_id'] if i < len(queries) else None, 'box_px': rec['box'],
                'window_origin_px': [int(y), int(x)], 'window_px': int(w),
                'layers_used': [{'layer': key.split(':', 1)[1], 'source': key.split(':', 1)[0]} for key in rec['keys']],
                'status': rec['status'], 'reason': rec['reason'], 'flags': list(rec['flags']),
                'start_offset_xy_px': rec['start'], 'start_source': rec['start_source'], 'unit_index_xy': rec['unit_index_xy'],
                'applied_offset_xy_px': [int(decision['offset'][0]), int(decision['offset'][1])], 'applied_source': decision['source'],
                'local_offset_xy_px': None, 'local_offset_xy_um': None, 'delta_from_global_xy_um': None, 'delta_from_global_um': None,
                'delta_from_start_xy_px': None, 'dice_global': rec['dice_start'], 'dice_local': None, 'ring_delta_xy_px': None,
                'ring_dice': None, 'ring_reason': None, 'omega_pixels': rec['omega_pixels'], 'per_layer': {}}
        if choice is not None:
            item.update({'per_layer': choice['per_layer'], 'decided_by_layer': choice['decided_by_layer'],
                         'layer_order': choice['layer_order'], 'layers_disagreeing': choice['layers_disagreeing'],
                         'best_dice': choice['best_dice'], 'dice_local': choice['dice'],
                         'ambiguity_set_size_initial': choice['ambiguity_set_size_initial'],
                         'ambiguity_set_size': choice['ambiguity_set_size']})
            if choice['offset_xy_px'] is not None:
                lx, ly = choice['offset_xy_px']
                item['local_offset_xy_px'] = [lx, ly]
                item['local_offset_xy_um'] = [local_align._r6(lx * pitch), local_align._r6(ly * pitch)]
                item['delta_from_global_xy_um'] = [local_align._r6((lx - dx) * pitch), local_align._r6((ly - dy) * pitch)]
                item['delta_from_global_um'] = local_align._r6(math.hypot((lx - dx) * pitch, (ly - dy) * pitch))
                item['delta_from_start_xy_px'] = choice['delta_xy_px']
        if rec['ring'] is not None:
            item['ring_delta_xy_px'], item['ring_dice'] = rec['ring']['delta_xy_px'], rec['ring']['dice']
            if rec['ring']['status'] != 'ok':
                item['ring_reason'] = rec['ring']['status']
        doc['rois'].append(item)
        if rec['status'] == 'ok':
            ok_points.append((x + w / 2.0, y + w / 2.0, item['local_offset_xy_um'][0], item['local_offset_xy_um'][1]))
    fit, fit_reason = local_align.linear_fit(ok_points, frame_shape, pitch)
    if not doc['rois']:
        fit, fit_reason = None, 'no_diagnosed_rois'
    doc['linear_fit'], doc['linear_fit_reason'] = fit, fit_reason
    counts = {status: 0 for status in local_align.STATUSES}
    sources = {'local': 0, 'unit': 0, 'global': 0}
    for item in doc['rois']:
        counts[item['status']] += 1
        sources[item['applied_source']] += 1
    doc['summary'] = {'n_rois': len(doc['rois']), **counts, 'applied_source_counts': sources}
    differs = [item['rank'] for item in doc['rois'] if 'local_offset_differs' in item['flags']]
    doc['notes'] = ([{'code': 'roi_local_offset_differs', 'severity': 'info', 'tolerance_um': tolerance, 'ranks': differs}]
                    if differs else [])
    return doc


def _frame_record(ref_shape, tgt_shape, stats, bbox):
    """alignment.json 에 더하는 frame 크기와 비교 영역. 수치는 change_maps 가 이미 센 값을 쓴다(새 frame 크기 할당 없음)."""
    ref_pixels, tgt_pixels = int(ref_shape[0]) * int(ref_shape[1]), int(tgt_shape[0]) * int(tgt_shape[1])
    before = int(stats['domain_pixels_before_erosion'])
    return {'frame_shape_reference_yx': list(ref_shape), 'frame_shape_target_yx': list(tgt_shape),
            'frame_shape_delta_px': [tgt_shape[0] - ref_shape[0], tgt_shape[1] - ref_shape[1]],
            'frame_anchor': 'top_left_index',
            'comparison_domain': {'pixels_before_erosion': before, 'pixels': int(stats['domain_pixels']),
                                  'reference_frame_pixels': ref_pixels, 'target_frame_pixels': tgt_pixels,
                                  'fraction_of_reference': round(before / ref_pixels, 6) if ref_pixels else 0.0,
                                  'fraction_of_target': round(before / tgt_pixels, 6) if tgt_pixels else 0.0,
                                  'bbox_px': None if bbox is None else [int(v) for v in bbox]}}


def _run(cfg, out, track, sides, products, pitch, limit, args):
    from .adapters.electrode_png import list_dl1_electrodes, load_cnt_layers, scan_electrode_layers
    from .adapters.real_hotspot import MAP_SLIM_ROW_BYTES, dataset_held_bytes
    from .roi_export import AssetMemo
    from . import cnt_align
    ref, tgt = products['reference'], products['target']
    window_um, top_k = args['window_um'], args['top_k']
    warnings, notes = [], []

    # --- 2. CNT 정렬 (실패해도 진행: offset (0, 0))
    cnt = {name: load_cnt_layers(args['cnt_from_dl1_root'], item.product_id, item.frame) for name, item in products.items()}
    # 이미 보유 중인 것(두 dataset, 양쪽 CNT 층 bool, 양쪽 valid)은 단계별 admission 한도에서 뺀다.
    held = (sum(dataset_held_bytes(side.dataset) for side in sides.values())
            + sum(int(layer.nbytes) for name in cnt for layer in cnt[name]['layers'].values())
            + int(ref.frame.valid.nbytes) + int(tgt.frame.valid.nbytes))
    reserved = int(held)
    alignment = cnt_align.align_products(
        {'valid': ref.frame.valid, 'layers': cnt['reference']['layers']},
        {'valid': tgt.frame.valid, 'layers': cnt['target']['layers']},
        pitch_um=pitch, search_um=args['align_search_um'], min_dice=args['align_min_dice'],
        layer_tolerance_um=args['align_layer_tolerance_um'], max_operation_bytes=limit, reserved_bytes=reserved)
    cnt_inputs = {name: {'product_id': products[name].product_id, 'files': cnt[name]['files'],
                         'warnings': cnt[name]['warnings']} for name in products}
    alignment_doc = {**alignment, 'schema_version': ALIGNMENT_SCHEMA, 'pitch_um': pitch, 'cnt_inputs': cnt_inputs,
                     'args': {k: args[k] for k in ('align_search_um', 'align_min_dice', 'align_layer_tolerance_um')}}
    outputs = []

    def emit(name, text):
        candidates.write_text(out / name, text, track)
        outputs.append(name)

    def emit_bytes(name, data):
        track(out / name)
        (out / name).write_bytes(data)
        outputs.append(name)

    outputs.append('alignment.json')      # 파일은 변경 지도 뒤에 쓴다(비교 영역 픽셀 수를 그 계산에서 재사용)
    offset_px = (int(alignment['offset_xy_px'][0]), int(alignment['offset_xy_px'][1]))
    # alignment.html 의 그림: 만들기 전에 메모리 추정으로 허용 여부를 정한다(넘으면 표만 쓰고 경고, 계산은 그대로).
    window_px = change_map._round_px(window_um, pitch)
    # hotspot 층 수: 두 dataset case 의 analysis_layer 합집합(변경 지도 층의 상한). 알 수 없으면 보수적 상수.
    hot_layers = len({case.analysis_layer for side in sides.values() for case in getattr(side.dataset, 'cases', ())})
    n_hot_layers = hot_layers or alignment_report.HEAT_LAYERS_ASSUMED
    electrode_root = args.get('electrode_png_root', args['cnt_from_dl1_root'])
    # layer-align/T5: 두 모델 모두 새 구조 + Layer PNG 가 있으면 5절 그림이 Layer stack 이다(인수와 무관; 이름만 스캔).
    layer_figure_files = None
    if alignment_report.fits(ref.frame.valid.shape, tgt.frame.valid.shape, limit, reserved, top_k, n_hot_layers, window_px):
        layer_figure_files = _layer_figure_files(electrode_root, products)
    n_layer_png = len(set(layer_figure_files['reference']) | set(layer_figure_files['target'])) if layer_figure_files else 0
    images_ok = alignment_report.fits(ref.frame.valid.shape, tgt.frame.valid.shape, limit, reserved, top_k,
                                      n_hot_layers, window_px, n_layer_png)
    if not images_ok:
        layer_figure_files = None
    report_source = (alignment_report.ImageSource(cnt['reference']['layers'], cnt['target']['layers'], ref.frame.valid,
                                                  tgt.frame.valid, offset_px) if images_ok else None)
    # 층 묶음 바탕용 전극 PNG 이름 목록(읽지 않는다). 이름 규칙 위반은 기존 loader 와 같은 오류다.
    electrode_files = ({name: scan_electrode_layers(electrode_root, products[name].product_id) for name in products}
                       if report_source is not None else {})
    if alignment['status'] != 'aligned':
        warnings.append({'code': 'alignment_' + ('failed' if alignment['status'] == 'alignment_failed_identity_used'
                                                 else 'uncertain'),
                         'status': alignment['status'], 'reasons': list(alignment['reasons'])})
    for name in sorted(cnt):
        for item in cnt[name]['warnings']:
            notes.append({'code': 'cnt_input_note', 'product': name, **item})
    # --- 2b. Layer 전역 교차 확인 + unit offset (--unit-align-um 을 줄 때만; 읽기 전용, apply=unit 일 때만 아래에서 쓴다)
    unit = None
    apply_unit = args.get('apply_align') == 'unit'
    apply_local = args.get('apply_align') == 'local'
    if args.get('unit_align_px') is not None:
        unit = _unit_alignment(products, cnt, offset_px, args['unit_align_px'], args, electrode_root, limit, reserved, pitch)
        alignment_doc.update(unit['doc'])
        notes.extend(unit['notes'])
    figure_layer_inputs = None
    if layer_figure_files is not None and unit is None:      # 그림에만 쓰는 Layer 의 입력 지문(정렬에 쓰면 위 unit['doc']['layer_inputs'] 를 그대로 쓴다)
        figure_layer_inputs = _layer_fingerprints(electrode_root, products, layer_figure_files)
        alignment_doc['layer_inputs'] = figure_layer_inputs

    # --- 3. 변경 지도 → 창 후보 → 층마다 병합
    # 같은 역할의 전극은 두 모델에서 같은 이름이다. 전극이 한쪽 모델에만 있어도 비교 대상이다: 그 전극을 쓰는 한쪽 조합은
    # 설계 변경으로 새로 생긴(사라진) 불량 조합과 같이 그쪽 합집합에 들어가고(pair_cases) flag ``new_electrode`` /
    # ``removed_electrode`` 만 붙는다. 모델 전체의 전극 목록 차이는 정보(note)다.
    # 기준은 case endpoints 가 아니라 DL1 root 의 모델별 전극 폴더 이름이다(기대 case 는 두 모델의 합집합이라
    # case 목록으로는 전극 추가·삭제가 가려지거나 거짓 차이가 난다).
    names = {name: set(list_dl1_electrodes(args['cnt_from_dl1_root'], products[name].product_id)) for name in products}
    pairing = change_map.pair_cases(sides['reference'].dataset, sides['target'].dataset, diameter_um=DIAMETER_UM,
                                    ref_electrodes=sorted(names['reference']), tgt_electrodes=sorted(names['target']))
    pairs = pairing['pairs']
    pair_by_key = {p.case_key: p for p in pairs}
    for code in pairing['warnings']:
        warnings.append({'code': code})
    if names['reference'] != names['target']:        # 정보다: 경고도 종료 코드 3 도 아니다(전극 추가·삭제도 비교 대상)
        notes.append({'code': 'electrode_set_differs', 'severity': 'info', 'electrode_set_basis': ELECTRODE_SET_BASIS,
                      'reference_only': sorted(names['reference'] - names['target']),
                      'target_only': sorted(names['target'] - names['reference'])})
    use_field = unit is not None and (apply_unit or apply_local)       # local 에서 --unit-align-um 이 없으면 시작점은 전역 g
    map_offset = {'offset_field': unit['field']} if use_field else {'offset_px': offset_px}
    maps = change_map.change_maps(pairs, ref_valid=ref.frame.valid, tgt_valid=tgt.frame.valid, **map_offset,
                                  pitch_um=pitch, tolerance_um=args['change_tolerance_um'], max_operation_bytes=limit,
                                  window_um=window_um, reserved_bytes=reserved)
    found = change_map.window_candidates(
        maps['maps'], maps['D'], union_change=maps['union_change'], union_ref=maps['union_ref'],
        union_tgt=maps['union_tgt'], pitch_um=pitch, window_um=window_um, max_operation_bytes=limit,
        origin_xy_um=(ref.frame.origin_xy_um[0], ref.frame.origin_xy_um[1]), reserved_bytes=reserved)
    map_stats = maps['stats']
    ref_shape, tgt_shape = tuple(int(v) for v in ref.frame.valid.shape), tuple(int(v) for v in tgt.frame.valid.shape)
    alignment_doc.update(_frame_record(ref_shape, tgt_shape, map_stats, maps['domain_bbox_px']))
    if ref_shape != tgt_shape:      # 정보다: 경고도 종료 코드 3 도 아니고 정렬 불확실 사유도 아니다
        notes.append({'code': 'frame_shape_differs', 'severity': 'info', 'reference_yx': list(ref_shape),
                      'target_yx': list(tgt_shape), 'delta_px': alignment_doc['frame_shape_delta_px']})
    candidates.write_text(out / 'alignment.json', json.dumps(alignment_doc, ensure_ascii=False, indent=2, sort_keys=True,
                                                             allow_nan=False) + '\n', track)
    # --- 3b. (--apply-align local) 풀 = 창 후보 점수 상위 N → 창마다 국소 보정 + 재점수화 → 이후 병합·억제·접기는 재점수로 한다(X3)
    candidate_list = found['candidates']
    refine, local_run, local_by_window = None, None, {}
    if apply_local:
        pool = found['candidates'][:args['refine_pool']]
        local_run = _local_corrections(pool, products, cnt, electrode_root, offset_px, unit, args['local_align_px'], args, pitch, limit,
                                       reserved + _maps_bytes(maps))
        decisions = _local_decisions(local_run['records'], unit, offset_px)
        candidate_list = []
        if pool:
            scored = change_map.case_contributions(
                pairs, pool, ref_valid=ref.frame.valid, tgt_valid=tgt.frame.valid, offset_px=offset_px, pitch_um=pitch,
                tolerance_um=args['change_tolerance_um'], max_operation_bytes=limit, reserved_bytes=reserved + _maps_bytes(maps),
                window_offsets=[d['spec'] for d in decisions])['windows']
            for window, result, rec, decision in zip(_recomputed_windows(pool, scored, pitch), scored, local_run['records'], decisions):
                if window['score_um2'] == 0 and window['sum_residual_cells'] == 0:      # 재점수 뒤 변경이 없다: 거짓 변경이었다
                    continue
                if rec['crossing']:
                    window['flags'] = list(window['flags']) + [CROSSES_BOUNDARY_FLAG]
                candidate_list.append(window)
                local_by_window[id(window)] = {'contrib': result, 'record': rec, 'decision': decision}
            candidate_list.sort(key=lambda c: (-c['score_um2'], -c['sum_residual_cells'], c['layer'], c['center_xy_um'][0],
                                               c['center_xy_um'][1]))
        refine = {'pool': len(pool), 'requested': args['refine_pool'], 'survivors': len(candidate_list)}
    by_id, merged, all_infos = {}, [], []
    ref_bbox = tuple(float(v) for v in ref.frame.bbox_um)
    merge_all = args.get('merge_scope') == 'all'
    suppress = args.get('suppress_overlap')
    for layer in sorted({c['layer'] for c in candidate_list}):
        layer_candidates = [c for c in candidate_list if c['layer'] == layer]
        infos = change_map.to_region_infos(layer_candidates, ref.product_id, ref.design_version, ref.frame_id, ref_bbox)
        for candidate, info in zip(layer_candidates, infos):
            by_id[info.region_id] = candidate
        if merge_all:
            all_infos.extend(infos)
        else:
            merged.extend(candidates.merge_regions(infos, window_um, args['merge_overlap'],
                                                   rank_key=change_map.region_rank_key))
    if merge_all:      # group 이 (제품, design, frame) 이라 한 번 호출하면 층을 넘어 병합된다
        merged = candidates.merge_regions(all_infos, window_um, args['merge_overlap'], rank_key=change_map.region_rank_key)
    merged.sort(key=lambda m: (-m['representative'].score, -m['representative'].max_count, m['representative'].layer,
                               m['representative'].center_xy_um[0], m['representative'].center_xy_um[1]))
    n_merged_total = len(merged)
    n_suppressed = None
    if suppress is not None:
        merged = change_map.suppress_overlap(merged, window_um, suppress)
        n_suppressed = len(merged)
    n_unit_folded, fold_info = None, {}
    if args.get('unit_fold'):      # 억제 뒤·top_k 앞: 서로 다른 unit 의 같은 위치 ROI 를 대표 하나로 접는다
        merged, fold_info = _fold_merged(merged, by_id, layer_align.UnitGeometry(ref_shape), args['unit_fold_tolerance_um'], pitch)
        n_unit_folded = len(merged)
    kept = merged[:top_k]
    windows = [by_id[m['representative'].region_id] for m in kept]
    if report_source is not None:      # alignment.html 용 열지도 블록·창 crop 만 남기고 큰 지도는 놓는다
        report_source.collect(maps, windows)
    del maps

    # --- 5. 기여 case (두 번째 패스) + CNT 변경 면적
    window_offsets, window_sources = [offset_px] * len(windows), [None] * len(windows)
    kept_local = []
    if apply_local:
        # 재점수 패스(3b) 값 그대로: 대표 창 1개 + 모든 층 기여(merge_scope=all 구성원 층 확장 없음)
        contrib = [local_by_window[id(w)]['contrib'] for w in windows]
        kept_local = [local_by_window[id(w)] for w in windows]
        window_offsets = [item['decision']['offset'] for item in kept_local]
        window_sources = [item['decision']['source'] for item in kept_local]
    elif apply_unit:
        # 모든 층의 기여 case 를 창마다 한 번씩(대표 창만): 창의 점수·합집합·관측 비율도 이 패스 값으로 낸다(merge_scope=all 구성원 층 확장 없음)
        contrib = change_map.case_contributions(
            pairs, windows, ref_valid=ref.frame.valid, tgt_valid=tgt.frame.valid, offset_px=offset_px, pitch_um=pitch,
            tolerance_um=args['change_tolerance_um'], max_operation_bytes=limit, reserved_bytes=reserved,
            window_offsets=[unit['field']] * len(windows))['windows']
        windows = _recomputed_windows(windows, contrib, pitch)
        for i, window in enumerate(windows):
            half = window['window_px'] // 2
            idx = unit['geometry'].unit_index((window['window_origin_px'][0] + half, window['window_origin_px'][1] + half))
            window_offsets[i], window_sources[i] = unit['effective'][idx], unit['source'][idx]
    elif merge_all:
        # 병합 구성원 층마다 대표 창과 같은 위치·크기의 창을 만들어 그 층의 case 도 센다(전체 층 합친 뒤 창별로 다시 모은다).
        expanded, owner = [], []
        for i, (m, window) in enumerate(zip(kept, windows)):
            for layer in sorted({member.layer for member in m['members']}):
                expanded.append({**window, 'layer': layer})
                owner.append((i, layer))
        scored = change_map.case_contributions(
            pairs, expanded, ref_valid=ref.frame.valid, tgt_valid=tgt.frame.valid, offset_px=offset_px, pitch_um=pitch,
            tolerance_um=args['change_tolerance_um'], max_operation_bytes=limit, reserved_bytes=reserved)['windows']
        contrib = [{'window_index': i, 'layer': window['layer'], 'center_xy_um': list(window['center_xy_um']),
                    'contributing_cases': []} for i, window in enumerate(windows)]
        for (i, layer), result in zip(owner, scored):
            contrib[i]['contributing_cases'].extend({**c, 'layer': layer} for c in result['contributing_cases'])
        for item in contrib:
            item['contributing_cases'].sort(key=lambda e: (-e['residual_um2'], e['case_key']))
    else:
        contrib = change_map.case_contributions(
            pairs, windows, ref_valid=ref.frame.valid, tgt_valid=tgt.frame.valid, offset_px=offset_px, pitch_um=pitch,
            tolerance_um=args['change_tolerance_um'], max_operation_bytes=limit, reserved_bytes=reserved)['windows']
    cnt_change = [_cnt_change_by_layer(w, cnt['reference']['layers'], cnt['target']['layers'], ref.frame.valid,
                                       tgt.frame.valid, window_offsets[i], pitch) for i, w in enumerate(windows)]

    # --- 4·6. 방향별 중심과 manual 요청 (ROI × 기여 case 하나당 요청 하나, 같은 키는 1회)
    shift = _shift_um(offset_px, pitch, ref.frame, tgt.frame)
    window_shifts = [_shift_um(o, pitch, ref.frame, tgt.frame) for o in window_offsets]
    applied = [({'offset_xy_px_applied': [int(o[0]), int(o[1])], 'offset_source': s} if (apply_unit or apply_local) else {})
               for o, s in zip(window_offsets, window_sources)]
    plan = {}
    for direction, query_name, index_name in candidates.DIRECTIONS:
        item = products[query_name]
        entries, requests = [], {}
        for i, (merge, window) in enumerate(zip(kept, windows)):
            rep = merge['representative']
            shift_here = (0.0, 0.0) if query_name == 'reference' else window_shifts[i]
            center = [round(rep.center_xy_um[0] + shift_here[0], 6), round(rep.center_xy_um[1] + shift_here[1], 6)]
            keys = []
            for entry in contrib[i]['contributing_cases']:
                pair = pair_by_key[entry['case_key']]
                case = pair.ref_case if query_name == 'reference' else pair.tgt_case
                if case is None:       # 한쪽 조합: 반대 방향 질의 제품에는 case 문맥이 없어 manual 행을 만들지 않는다
                    continue
                identity = asdict(case.identity)
                key = _row_key(identity, case.failure_type, case.analysis_layer, center, item.frame)
                requests.setdefault(key, {**identity, 'center_xy_um': center,
                                          'endpoints': [asdict(e) for e in case.endpoints],
                                          'failure_type': case.failure_type})
                if key not in keys:
                    keys.append(key)
            entries.append({'merge': merge, 'window': window, 'center': center, 'keys': keys, 'index': i,
                            'ref_center': [float(rep.center_xy_um[0]), float(rep.center_xy_um[1])],
                            'shift': window_shifts[i], 'shift_here': shift_here, 'applied': applied[i]})
        plan[query_name] = {'direction': direction, 'index_name': index_name, 'entries': entries,
                            'requests': [requests[k] for k in sorted(requests)]}

    # --- 6·7. export (질의 export 두 개, index export 두 개). 두 dataset 이 모두 메모리에 있으므로 서로의 바이트를 예약한다.
    memo = AssetMemo()

    def reserved_for(name):
        other = sides['target' if name == 'reference' else 'reference']
        rows = sum(len(s.rows) + (len(s.index['rows']) if s.index else 0) for s in sides.values())
        return dataset_held_bytes(other.dataset) + MAP_SLIM_ROW_BYTES * rows

    for name, side in sides.items():
        done = candidates.export_manual_rows(cfg, side, plan[name]['requests'], out / f'{name}_export', DIAMETER_UM,
                                             track, memo, reserved_for(name))
        if done is None:
            side.status = 'blocked'
            side.reasons = list(candidates.SHORT_CIRCUIT_REASONS)
            continue
        side.exported, side.export_path, payload, side.role_blocked = done
        side.rows = payload['rois']
        side.export_payload = payload
        if payload.get('status') in ('blocked', 'unavailable'):
            side.status = payload['status']
            side.reasons = list(payload.get('reasons', []))
    for name, side in sides.items():
        if side.status != 'unavailable':
            candidates.export_index(cfg, side, out / f'{name}_index_export', DIAMETER_UM, track, memo, reserved_for(name))
    memo = None

    # --- 7b. DL2 질의·index export 폴더마다 mask_derivation.json (export-template-rois 와 같은 writer·규칙, ready 행의 case 만)
    derivation_files = {}
    for name, side in sides.items():
        order = _stack_order(side)
        for key, folder, rows, written in (
                ('query', f'{name}_export', side.rows, side.export_path is not None),
                ('index', f'{name}_index_export', side.index['rows'] if side.index else [],
                 side.index is not None and side.index['path'] is not None)):
            if not written:
                derivation_files[(name, key)] = None
                continue
            document = template_export._mask_derivation(_derivation_rows(side, rows), order)
            emit_bytes(f'{folder}/mask_derivation.json', template_export._json_bytes(document))
            derivation_files[(name, key)] = f'{folder}/mask_derivation.json'

    # --- 8. 연결: 요청 키 → 반환 행
    directions, funnels, rows_index = [], [], {}
    n_candidates, n_merged = len(found['candidates']), n_merged_total
    for direction, query_name, index_name in candidates.DIRECTIONS:
        side, item = sides[query_name], products[query_name]
        rows_by_key = {}
        for row in side.rows:
            center = row.get('center_xy_um')
            if center is None or not isinstance(row.get('source_identity'), dict):
                continue
            rows_by_key[_row_key(row['source_identity'], row.get('failure_type'), row.get('analysis_layer'), center,
                                 item.frame)] = row
        rows_index[query_name] = rows_by_key
        queries = []
        for entry in plan[query_name]['entries']:
            rep, members, window = entry['merge']['representative'], entry['merge']['members'], entry['window']
            i = entry['index']
            rows = []
            for key in entry['keys']:
                row = rows_by_key.get(key)
                if row is not None:
                    rows.append(row)
            ready = [r for r in rows if r['status'] == 'ready']
            reasons = set()
            for row in rows:
                reasons.update(row['reasons'])
            if side.status in ('blocked', 'unavailable'):
                reasons.update(side.reasons)
            if len(rows) < len(entry['keys']):
                reasons.add('roi_row_missing')
            if not rows and not reasons:
                reasons.add(NO_CASE_REASON if contrib[i]['contributing_cases'] and not entry['keys'] else 'no_roi_rows')
            flags = list(window['flags'])
            shift_here = entry['shift_here']
            if _clipped(entry['center'], tuple(item.frame.bbox_um), window_um):
                flags.append('window_clipped')
            elif any(_clipped((m.center_xy_um[0] + shift_here[0], m.center_xy_um[1] + shift_here[1]),
                              tuple(item.frame.bbox_um), window_um) for m in members):
                flags.append('merged_member_window_clipped')
            if len(rows) > len(ready) and ready:
                flags.append('roi_rows_partially_blocked')
            if not cnt_change[i]:
                flags.append('cnt_change_unavailable')
            queries.append({
                'query_id': candidates._query_id(direction, rep.region_id), 'product_id': item.product_id,
                'design_version': item.design_version, 'frame_id': item.frame_id,
                'center_xy_um': list(entry['center']), 'window_um': window_um, 'region_id': rep.region_id,
                'merged_from': [m.region_id for m in members],
                # RegionInfo.max_count 는 내부적으로 sum_residual 셀 수다. 취약점 max_count 와 뜻이 달라 payload 에 쓰지 않는다.
                'max_count': None, 'area_um2': rep.area_um2,
                'layers': sorted({m.layer for m in members}), 'flags': flags,
                'status': 'ready' if ready else 'blocked', 'reasons': sorted(reasons),
                'roi_ids': sorted(r['roi_id'] for r in rows), 'n_rois': len(rows), 'n_rois_ready': len(ready),
                'score_um2': window['score_um2'], 'score_basis': window['score_basis'], 'score_layer': rep.layer,
                **_window_attributes(window, len(contrib[i]['contributing_cases'])),
                'observed_fraction': window['observed_fraction'],
                **entry['applied'],
                **({'unit_fold': fold_info[rep.region_id]} if fold_info else {}),
                'contributing_cases': [
                    {'case_key': c['case_key'], 'residual_um2': c['residual_um2'], 'change_kind': c['classification'],
                     'area_reference_um2': c['area_reference_um2'], 'area_target_um2': c['area_target_um2'],
                     'iou_global': c['iou_global'], 'iou_local': c['iou_local'],
                     'local_shift_um': c['local_shift_um'], 'flags': c['flags'],
                     'present_in': pair_by_key[c['case_key']].present_in,
                     'layer_changed': pair_by_key[c['case_key']].layer_changed, **_layer_key(c)}
                    for c in contrib[i]['contributing_cases']],
                'one_sided_cases': [
                    {'case_key': c['case_key'], 'present_in': pair_by_key[c['case_key']].present_in,
                     'layer_changed': pair_by_key[c['case_key']].layer_changed, **_layer_key(c)}
                    for c in contrib[i]['contributing_cases'] if pair_by_key[c['case_key']].present_in != 'both'],
                'cnt_change_um2_by_layer': cnt_change[i]})
        funnel = _funnel(direction, n_candidates, n_merged, queries, top_k, n_suppressed, n_unit_folded, refine)
        funnels.append(funnel)
        if funnel['top_k_not_filled']:
            warning = {'code': 'top_k_not_filled', 'direction': direction, 'requested': top_k, 'found': len(queries)}
            if apply_local:
                warning['detail'] = '풀 안 창만 순위에 들어간다: --refine-pool 을 키우세요'
            warnings.append(warning)
        directions.append({'direction': direction, 'query_product': query_name, 'index_product': index_name,
                           'query_export': f'{query_name}_export/review_rois.json' if side.export_path else None,
                           'index_export': (f'{index_name}_index_export/review_rois.json'
                                            if sides[index_name].index and sides[index_name].index['path'] else None),
                           'queries': queries})
    for name, side in sides.items():
        for code in side.warnings:
            warnings.append({'code': code, 'product': name})
        if side.role_blocked or side.role_blocked_index:
            warnings.append({'code': candidates.ROLE_REQUIRED_REASON, 'product': name,
                             'detail': candidates.ROLE_REQUIRED_MESSAGE, 'query_rows_blocked': side.role_blocked,
                             'index_rows_blocked': side.role_blocked_index})
        if side.status in ('blocked', 'unavailable'):
            warnings.append({'code': 'side_' + side.status, 'product': name, 'reasons': side.reasons})
        if side.export_path is not None:
            partial = candidates._query_export_note(side)
            if partial is not None:
                notes.append(partial)
    coverage = {}
    for name, side in sides.items():
        if side.index is None:
            continue
        coverage[name] = candidates.index_coverage(side)
        if not coverage[name]['complete']:
            warnings.append({'code': 'index_coverage_incomplete', 'product': name,
                             'electrodes_covered': coverage[name]['electrodes_covered'],
                             'electrodes_total': coverage[name]['electrodes_total'],
                             'index_rois_blocked': coverage[name]['index_rois_blocked'],
                             'reasons': sorted(coverage[name]['blocked_reasons']),
                             'missing_by_role_layer': coverage[name]['missing_by_role_layer']})

    # --- 직접 대응 쌍 (DL2 건너뛰기): 파일은 아래 문서 단계에서 쓴다. blocked 쌍은 경고로 남겨 종료 코드 3 이 된다.
    direct_records = _direct_pairs(plan, contrib, pair_by_key, ref, tgt, shift, alignment, rows_index)
    direct_ready = sum(r['status'] == 'ready' for r in direct_records)
    direct_blocked = len(direct_records) - direct_ready
    if direct_blocked:
        counts = {}
        for r in direct_records:
            for code in r['reasons']:
                counts[code] = counts.get(code, 0) + 1
        warnings.append({'code': 'direct_pairs_blocked', 'count': direct_blocked, 'reasons': dict(sorted(counts.items())),
                         'detail': 'these pairs are blocked; use the DL2 route (reference_export / target_export) for them'})

    # --- alignment.html: 정렬이 실패·불확실해도 항상 쓴다. 그림 생성을 허용하지 않았으면 표만 쓰고 경고(exit 3)
    skipped = None
    local_um = args.get('local_align_um')
    diagnose = local_um is not None and not apply_local
    local_state = {'r_px': local_align.search_radius_px(local_um, pitch)} if diagnose else None
    layer_bases = None
    if report_source is not None and layer_figure_files is not None:
        if _collect_layer_figures(report_source, directions[0]['queries'], windows, electrode_root, layer_figure_files, products,
                                  _figure_stack_order(sides), offset_px, limit, reserved, warnings, notes):
            layer_bases = layer_figure_files
    if report_source is not None:
        _collect_layer_bases(report_source, directions[0]['queries'], windows, electrode_root, electrode_files, products,
                             limit, reserved, warnings, diag=local_state, layer_files=layer_bases)
    if apply_local:
        local_doc = _local_apply_document([item['record'] for item in kept_local], [item['decision'] for item in kept_local],
                                          directions[0]['queries'], windows, pitch, args, offset_px, ref_shape, local_run['excluded'])
    else:
        local_doc = (_local_alignment_document(report_source, local_state, directions[0]['queries'], windows, pitch, args, offset_px,
                                               ref_shape) if diagnose else None)
    if report_source is not None:      # 진단용 넓은 crop 은 문서를 만든 직후 놓는다(HTML 렌더 전에 메모리 반환)
        report_source.diag_wide.clear()
    if report_source is None:
        need = alignment_report.estimate_report_bytes(ref.frame.valid.shape, tgt.frame.valid.shape, top_k,
                                                      n_hot_layers, window_px)
        skipped = f'estimated {need} bytes + {reserved} reserved > operation limit {limit}'
        warnings.append({'code': 'alignment_report_images_skipped', 'estimated_bytes': need, 'reserved_bytes': reserved,
                         'max_operation_bytes': limit})
    report_html, report_info = alignment_report.build_alignment_html(
        alignment_doc, queries=directions[0]['queries'], windows=windows, source=report_source, skipped_reason=skipped,
        pairing_stats=pairing['stats'], local_alignment=local_doc,
        apply_mode='unit' if apply_unit else 'local' if apply_local else None)
    report_source = None
    emit_bytes('alignment.html', report_html)
    if local_doc is not None:
        emit('roi_local_alignment.json', json.dumps(local_doc, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        summary = local_doc['summary']
        print('roi_local_alignment: n_rois=%d ok=%d skipped=%d' % (summary['n_rois'], summary['ok'], summary['skipped']),
              file=sys.stderr)
    alignment_report_doc = {k: report_info[k] for k in ('path', 'bytes', 'downscaled', 'crops_omitted')}

    # --- 9~12. 문서
    inputs = {'config_sha256': cfg.get('_config_sha256'),
              'reference': {**sides['reference'].input_record, 'product_ids': sides['reference'].product_ids},
              'target': {**sides['target'].input_record, 'product_ids': sides['target'].product_ids},
              'cnt': {name: {'product_id': products[name].product_id, 'files_sha256': _sha_json(cnt[name]['files']),
                             'n_files': len(cnt[name]['files'])} for name in products}}
    for name, side in sides.items():
        if side.export_path is not None:
            inputs[name]['export_sha256'] = candidates._sha256_file(side.export_path)
        if side.index is not None and side.index['path'] is not None:
            inputs[name]['index_export_sha256'] = candidates._sha256_file(side.index['path'])
        for key, field in (('query', 'mask_derivation_sha256'), ('index', 'index_mask_derivation_sha256')):
            if derivation_files[(name, key)] is not None:
                inputs[name][field] = candidates._sha256_file(out / derivation_files[(name, key)])
    mask_derivation_files = {direction: {'query': derivation_files[(query_name, 'query')],
                                         'index': derivation_files[(index_name, 'index')]}
                             for direction, query_name, index_name in candidates.DIRECTIONS}
    commit = next((s.export_payload.get('producer_commit') for s in sides.values() if s.export_payload), None)
    all_queries = [q for d in directions for q in d['queries']]
    # 반대 모델에만 있는 조합뿐인 방향 질의(NO_CASE_REASON)는 내보낼 ROI 가 원래 없으므로 완전성을 깨지 않는다.
    complete = (not warnings and all((q['status'] == 'ready' and q['n_rois'] == q['n_rois_ready'])
                                     or q['reasons'] == [NO_CASE_REASON] for q in all_queries)
                and all(s.status == 'available' for s in sides.values()))
    funnel_doc = {'schema_version': 'candidate_funnel_v1', 'directions': funnels, 'top_k': top_k,
                  'top_k_scope': 'per_direction', 'index_coverage': coverage,
                  'direct_pairs_ready': direct_ready, 'direct_pairs_blocked': direct_blocked}
    if suppress is not None:
        funnel_doc['suppressed'] = n_suppressed
    exec_inputs = {'config_sha256': inputs['config_sha256'],
                   'reference': {k: sides['reference'].input_record.get(k)
                                 for k in ('manifest_sha256', 'asset_manifest', 'electrode_import_record')},
                   'target': {k: sides['target'].input_record.get(k)
                              for k in ('manifest_sha256', 'asset_manifest', 'electrode_import_record')},
                   'cnt': inputs['cnt']}
    direct_doc, direct_sidecar = _direct_documents(direct_records, alignment, commit, exec_inputs)
    direct_summary = {'correspondence': 'direct/roi_correspondence.json', 'sidecar': 'direct/direct_pairs_sidecar.json',
                      'pairs_ready': direct_ready, 'pairs_blocked': direct_blocked}
    match_inputs = {direction: {'query': f'{query_name}_export', 'index': f'{index_name}_index_export'}
                    for direction, query_name, index_name in candidates.DIRECTIONS}
    roles = set()
    for side in sides.values():
        roles.update((side.dataset.provenance or {}).get('electrode_roles_effective', {}).values())
        roles.update(ep.role for case in side.dataset.cases for ep in case.endpoints if ep.role)
    dl2_model_roles = sorted(roles)
    alignment_summary = {k: alignment[k] for k in ('status', 'reasons', 'offset_xy_um', 'dice_best', 'peak_ratio')}
    change_args = {k: args[k] for k in ('align_search_um', 'align_min_dice', 'align_layer_tolerance_um',
                                        'change_tolerance_um', 'window_um', 'merge_overlap', 'top_k')}
    change_args.update({k: args[k] for k in ('merge_scope', 'suppress_overlap', 'electrode_png_root', 'local_align_um',
                                             'unit_align_um', 'apply_align', 'unit_fold', 'unit_fold_tolerance_um',
                                             'refine_pool') if k in args})
    layer_files = (unit['doc']['layer_inputs'] if unit is not None
                   else (local_run['layer_files'] if local_run is not None else figure_layer_inputs))
    if layer_files is not None:      # Layer 를 정렬이나 그림에 쓴 실행만: 입력 지문(alignment.json.layer_inputs 와 같은 파일 목록)
        inputs['layers'] = {name: {'product_id': item['product_id'], 'files_sha256': _sha_json(item['files']),
                                   'n_files': len(item['files'])} for name, item in layer_files.items()}
    payload = {'schema_version': candidates.SCHEMA_VERSION, 'roi_source': ROI_SOURCE, 'producer_commit': commit,
               'args': {'min_count': None, 'window_um': window_um, 'merge_overlap': args['merge_overlap'],
                        'max_candidates': top_k, 'representative_diameter_um': DIAMETER_UM},
               'diameter_um': float(DIAMETER_UM), 'change_args': change_args, 'alignment': alignment_summary,
               'change_map': {'stats': map_stats, 'pairing': pairing['stats'],
                              'cases_excluded': pairing['cases_excluded'],
                              'cases_only_in_reference': pairing['cases_only_in_reference'],
                              'cases_only_in_target': pairing['cases_only_in_target']},
               'electrode_set_basis': ELECTRODE_SET_BASIS, 'union_basis': UNION_BASIS,
               'alignment_report': alignment_report_doc,
               'inputs': inputs, 'directions': directions, 'funnel': funnel_doc, 'direct': direct_summary,
               'warnings': warnings, 'notes': notes, 'match_inputs': match_inputs, 'dl2_model_roles': dl2_model_roles,
               'match_files': {d: {k: f'{v}/review_rois.json' for k, v in item.items()}
                               for d, item in match_inputs.items()},
               'mask_derivation_rule': args['mask_derivation_rule'], 'mask_derivation_files': mask_derivation_files,
               'status': 'available' if complete else 'partial',
               'policy': {'roi_source': 'cnt_alignment_plus_hotspot_xor_residual_windows',
                          'merge': MERGE_POLICY['all' if merge_all else 'layer'],
                          'window': 'square_centered_at_window_center_in_reference_coordinates',
                          'representative': 'score_um2_desc_sum_residual_um2_desc_layer_asc_center_asc_region_id_asc',
                          'score_basis': change_map.SCORE_BASIS,
                          'roi_per_contributing_case': 'one_manual_roi_per_(window, contributing_case)',
                          'wiring_change_um2': 'not_computed_here',
                          'mutual_one_way': 'not_computed_here',
                          'index_export': 'one_manual_roi_per_dataset_case_index_only_not_queries_no_region_provenance'}}
    if suppress is not None:
        payload['policy']['suppress'] = SUPPRESS_POLICY_ANY if suppress <= candidates.OVERLAP_EPS else SUPPRESS_POLICY
    if apply_local:
        payload['policy']['apply_align'] = LOCAL_APPLY_POLICY
    if args.get('unit_fold'):
        payload['policy']['unit_fold'] = UNIT_FOLD_POLICY
    if merge_all:
        payload['policy']['attributes'] = ATTRIBUTES_POLICY_ALL_WINDOW_OFFSET if (apply_unit or apply_local) else ATTRIBUTES_POLICY_ALL
    emit('candidate_queries_v1.json', json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    emit('funnel.json', json.dumps(funnel_doc, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    candidates.write_funnel_csv(out / 'funnel.csv', funnels, coverage, track)
    outputs.append('funnel.csv')
    (out / 'direct').mkdir()
    track(out / 'direct', is_dir=True)
    emit('direct/roi_correspondence.json', json.dumps(direct_doc, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    emit('direct/direct_pairs_sidecar.json', json.dumps(direct_sidecar, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    for name, side in sides.items():
        if side.export_path is not None:
            for item in sorted(Path(p).name for p in side.exported.get('outputs', [])):
                outputs.append(f'{name}_export/{item}')
        if side.index is not None and side.index['path'] is not None:
            for item in sorted(Path(p).name for p in side.index['exported'].get('outputs', [])):
                outputs.append(f'{name}_index_export/{item}')
    result = {'status': payload['status'], 'reasons': sorted({w['code'] for w in warnings}),
              'outputs': sorted(outputs), 'exit_code': 0 if complete else 3,
              'alignment': alignment_summary,
              'n_queries': {d['direction']: len(d['queries']) for d in directions},
              'funnel': {f['direction']: {s['stage']: s['count'] for s in f['stages']} for f in funnels},
              'index_coverage': {n: {k: c[k] for k in ('dataset_cases', 'index_rois_ready', 'index_rois_blocked',
                                                      'electrodes_covered', 'electrodes_total')}
                                 for n, c in coverage.items()},
              'direct': direct_summary,
              'match_inputs': match_inputs, 'match_files': payload['match_files'],
              'mask_derivation_rule': args['mask_derivation_rule'], 'mask_derivation_files': mask_derivation_files,
              'dl2_model_roles': dl2_model_roles}
    execution = {'command': 'candidates-change',
                 'args': {**change_args, 'max_operation_bytes': limit, 'mask_derivation_rule': args['mask_derivation_rule']},
                 'inputs': exec_inputs, 'warnings': warnings, 'notes': notes, **result}
    if unit is not None or local_run is not None:      # 새 기능을 켰을 때만(고정 해시 테스트 밖): 단계 소요 시간
        execution['timings_s'] = {**(unit['timings_s'] if unit is not None else {}),
                                  **(local_run['timings_s'] if local_run is not None else {})}
    candidates.write_text(out / 'execution.json', json.dumps(execution, ensure_ascii=False, indent=2) + '\n', track)
    result['outputs'] = sorted(result['outputs'] + ['execution.json'])
    return result
