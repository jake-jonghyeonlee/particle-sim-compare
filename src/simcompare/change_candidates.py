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

import numpy as np

from . import candidates, change_map
from .numeric_schema import NumericContractError

DIAMETER_UM = 5
ROI_SOURCE = 'cnt_change'
ALIGNMENT_SCHEMA = 'cnt_alignment_v1'
ELECTRODE_SET_BASIS = 'dl1_electrode_folders'
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


def _validate_args(cnt_root, search, min_dice, layer_tolerance, change_tolerance, window, overlap, top_k):
    if not isinstance(cnt_root, (str, Path)) or not str(cnt_root):
        raise _bad('invalid_candidates_change_argument', '--cnt-from-dl1-root 경로가 필요합니다')
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
            b_center = [round(a_center[0] + shift[0], 6), round(a_center[1] + shift[1], 6)]
            for contribution in contrib[i]['contributing_cases']:
                pair = pair_by_key[contribution['case_key']]
                a, a_identity = _direct_side(pair.ref_case, a_center, rows_index['reference'], ref.frame)
                b, b_identity = _direct_side(pair.tgt_case, b_center, rows_index['target'], tgt.frame)
                digest = _sha_json({
                    'a': [sorted(a_identity.items()), a['analysis_layer'], a['failure_type'], _cell(a_center, ref.frame)],
                    'b': [sorted(b_identity.items()), b['analysis_layer'], b['failure_type'], _cell(b_center, tgt.frame)]})
                pair_id = 'direct_' + digest[:16]
                if pair_id in found:
                    found[pair_id]['directions'].add(direction)
                    continue
                flags = sorted(set(window['flags']) | set(contribution['flags']))
                found[pair_id] = {'pair_id': pair_id, 'a': a, 'b': b, 'directions': {direction},
                                  'score_um2': rep.score, 'score_layer': rep.layer, 'region_id': rep.region_id,
                                  'observed_fraction': window['observed_fraction'], 'flags': flags,
                                  'change_kind': contribution['classification'],
                                  'residual_um2': contribution['residual_um2'], 'iou_global': contribution['iou_global'],
                                  'iou_local': contribution['iou_local'],
                                  'local_shift_um': contribution['local_shift_um']}
    for record in found.values():
        if failed:
            reasons = ['alignment_failed']
        else:
            reasons = [name for name, frame, side in (('a', ref.frame, record['a']), ('b', tgt.frame, record['b']))
                       if not _valid_at(frame, side['center_xy_um'])]
            reasons = ['position_outside_valid_domain_' + name for name in reasons]
        record['status'], record['reasons'] = ('blocked' if reasons else 'ready'), reasons
    return sorted(found.values(), key=lambda r: (-r['score_um2'], r['a']['analysis_layer'],
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
            'region_id': r['region_id'], 'change_kind': r['change_kind'], 'residual_um2': r['residual_um2'],
            'iou_global': r['iou_global'], 'iou_local': r['iou_local'], 'local_shift_um': r['local_shift_um'],
            'observed_fraction': r['observed_fraction'], 'flags': r['flags'], 'directions': sorted(r['directions'])}
    sidecar = {'schema_version': DIRECT_SIDECAR_SCHEMA, 'method': DIRECT_SOURCE['method'],
               'sign': 'p_target = p_reference + offset', 'alignment': summary, 'inputs': inputs,
               'limitations': [DIRECT_LIMITATION], 'pairs': pairs}
    return payload, sidecar


# --------------------------------------------------------------------------- funnel

def _funnel(direction, n_candidates, n_merged, queries, top_k):
    kept = len(queries)
    ready = sum(q['status'] == 'ready' for q in queries)
    blocked_reasons = {}
    for q in queries:
        if q['status'] != 'ready':
            for code in q['reasons']:
                blocked_reasons[code] = blocked_reasons.get(code, 0) + 1
    shortfall = max(top_k - kept, 0)
    stages = [
        {'stage': 'change_candidates', 'count': n_candidates, 'dropped': 0, 'reasons': {}},
        {'stage': 'merged', 'count': n_merged, 'dropped': n_candidates - n_merged,
         'reasons': {'merged_overlap': n_candidates - n_merged} if n_candidates > n_merged else {}},
        {'stage': 'top_k', 'count': kept, 'dropped': n_merged - kept,
         'reasons': ({'cut_by_top_k': n_merged - kept} if n_merged > kept else
                     ({'top_k_not_filled': shortfall} if shortfall else {}))},
        {'stage': 'exported_ready', 'count': ready, 'dropped': kept - ready, 'reasons': dict(sorted(blocked_reasons.items()))}]
    return {'direction': direction, 'stages': stages, 'change_candidates': n_candidates, 'merged': n_merged,
            'top_k': top_k, 'top_k_not_filled': shortfall, 'exported_ready': ready, 'blocked': kept - ready,
            'note': 'reasons 값은 사유 코드별 질의 수이며 한 질의가 여러 사유를 가질 수 있어 dropped와 합이 다를 수 있다'}


# --------------------------------------------------------------------------- 실행

def run_candidates_change(cfg, out, *, cnt_from_dl1_root, align_search_um, align_min_dice, align_layer_tolerance_um,
                          change_tolerance_um, window_um, merge_overlap, top_k):
    """새 출력 폴더에 alignment.json, candidate_queries_v1.json, funnel.json/.csv, reference_export/, target_export/,
    reference_index_export/, target_index_export/, direct/(roi_correspondence.json, direct_pairs_sidecar.json),
    execution.json 을 쓴다.

    폴더를 만들기 전에 NumericContractError(종료 코드 2)로 알리는 것: 인수 값, 이미 있는 출력 경로, config 종류·지름,
    side 준비 결과, 제품·frame 개수, pitch 불일치, 정렬 탐색 반경(>= 2 x pitch), 창 크기(>= pitch, frame 보다 크지 않음).
    CNT 읽기·정렬·메모리 한도(admission) 오류는 폴더를 만든 뒤에 나므로, 쓰는 도중 실패한 경우와 같이 이 실행이 만든
    파일과 폴더만 지운다. 이미 있는 출력 경로는 거부하고 건드리지 않는다.
    """
    from . import pipeline
    _validate_args(cnt_from_dl1_root, align_search_um, align_min_dice, align_layer_tolerance_um, change_tolerance_um,
                   window_um, merge_overlap, top_k)
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
    limit = _operation_limit(sides)
    args = {'cnt_from_dl1_root': str(cnt_from_dl1_root), 'align_search_um': align_search_um,
            'align_min_dice': align_min_dice, 'align_layer_tolerance_um': align_layer_tolerance_um,
            'change_tolerance_um': change_tolerance_um, 'window_um': window_um, 'merge_overlap': merge_overlap,
            'top_k': top_k, 'max_operation_bytes': limit}
    track, cleanup = candidates.output_tracker()
    out.mkdir()
    track(out, is_dir=True)
    try:
        return _run(cfg, out, track, sides, products, pitch, limit, args)
    except BaseException:
        cleanup()
        raise


def _run(cfg, out, track, sides, products, pitch, limit, args):
    from .adapters.electrode_png import list_dl1_electrodes, load_cnt_layers
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

    emit('alignment.json', json.dumps(alignment_doc, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + '\n')
    offset_px = (int(alignment['offset_xy_px'][0]), int(alignment['offset_xy_px'][1]))
    if alignment['status'] != 'aligned':
        warnings.append({'code': 'alignment_' + ('failed' if alignment['status'] == 'alignment_failed_identity_used'
                                                 else 'uncertain'),
                         'status': alignment['status'], 'reasons': list(alignment['reasons'])})
    for name in sorted(cnt):
        for item in cnt[name]['warnings']:
            notes.append({'code': 'cnt_input_note', 'product': name, **item})

    # --- 3. 변경 지도 → 창 후보 → 층마다 병합
    pairing = change_map.pair_cases(sides['reference'].dataset, sides['target'].dataset, diameter_um=DIAMETER_UM)
    pairs = pairing['pairs']
    pair_by_key = {p.case_key: p for p in pairs}
    for code in pairing['warnings']:
        warnings.append({'code': code})
    # 같은 역할의 전극은 두 모델에서 같은 이름이다. 전극이 늘거나 줄었으면 회로 자체가 바뀐 것이라 비교 대상이 아니다:
    # 경고만 남기고 계산은 계속한다. 한쪽 전극만 관여하는 case 는 cases_only_in_* 에 남고 ROI 후보가 되지 않는다.
    # 기준은 case endpoints 가 아니라 DL1 root 의 모델별 전극 폴더 이름이다(기대 case 는 두 모델의 합집합이라
    # case 목록으로는 전극 추가·삭제가 가려지거나 거짓 경고가 난다).
    names = {name: set(list_dl1_electrodes(args['cnt_from_dl1_root'], products[name].product_id)) for name in products}
    if names['reference'] != names['target']:
        warnings.append({'code': 'electrode_set_differs', 'electrode_set_basis': ELECTRODE_SET_BASIS,
                         'reference_only': sorted(names['reference'] - names['target']),
                         'target_only': sorted(names['target'] - names['reference'])})
    maps = change_map.change_maps(pairs, ref_valid=ref.frame.valid, tgt_valid=tgt.frame.valid, offset_px=offset_px,
                                  pitch_um=pitch, tolerance_um=args['change_tolerance_um'], max_operation_bytes=limit,
                                  window_um=window_um, reserved_bytes=reserved)
    found = change_map.window_candidates(
        maps['maps'], maps['D'], pitch_um=pitch, window_um=window_um, max_operation_bytes=limit,
        origin_xy_um=(ref.frame.origin_xy_um[0], ref.frame.origin_xy_um[1]), reserved_bytes=reserved)
    map_stats = maps['stats']
    del maps
    by_id, merged = {}, []
    ref_bbox = tuple(float(v) for v in ref.frame.bbox_um)
    for layer in sorted({c['layer'] for c in found['candidates']}):
        layer_candidates = [c for c in found['candidates'] if c['layer'] == layer]
        infos = change_map.to_region_infos(layer_candidates, ref.product_id, ref.design_version, ref.frame_id, ref_bbox)
        for candidate, info in zip(layer_candidates, infos):
            by_id[info.region_id] = candidate
        merged.extend(candidates.merge_regions(infos, window_um, args['merge_overlap'],
                                               rank_key=change_map.region_rank_key))
    merged.sort(key=lambda m: (-m['representative'].score, m['representative'].layer,
                               m['representative'].center_xy_um[0], m['representative'].center_xy_um[1]))
    kept = merged[:top_k]
    windows = [by_id[m['representative'].region_id] for m in kept]

    # --- 5. 기여 case (두 번째 패스) + CNT 변경 면적
    contrib = change_map.case_contributions(
        pairs, windows, ref_valid=ref.frame.valid, tgt_valid=tgt.frame.valid, offset_px=offset_px, pitch_um=pitch,
        tolerance_um=args['change_tolerance_um'], max_operation_bytes=limit, reserved_bytes=reserved)['windows']
    cnt_change = [_cnt_change_by_layer(w, cnt['reference']['layers'], cnt['target']['layers'], ref.frame.valid,
                                       tgt.frame.valid, offset_px, pitch) for w in windows]

    # --- 4·6. 방향별 중심과 manual 요청 (ROI × 기여 case 하나당 요청 하나, 같은 키는 1회)
    shift = _shift_um(offset_px, pitch, ref.frame, tgt.frame)
    plan = {}
    for direction, query_name, index_name in candidates.DIRECTIONS:
        item = products[query_name]
        shift_here = (0.0, 0.0) if query_name == 'reference' else shift
        entries, requests = [], {}
        for i, (merge, window) in enumerate(zip(kept, windows)):
            rep = merge['representative']
            center = [round(rep.center_xy_um[0] + shift_here[0], 6), round(rep.center_xy_um[1] + shift_here[1], 6)]
            keys = []
            for entry in contrib[i]['contributing_cases']:
                pair = pair_by_key[entry['case_key']]
                case = pair.ref_case if query_name == 'reference' else pair.tgt_case
                identity = asdict(case.identity)
                key = _row_key(identity, case.failure_type, case.analysis_layer, center, item.frame)
                requests.setdefault(key, {**identity, 'center_xy_um': center,
                                          'endpoints': [asdict(e) for e in case.endpoints],
                                          'failure_type': case.failure_type})
                if key not in keys:
                    keys.append(key)
            entries.append({'merge': merge, 'window': window, 'center': center, 'keys': keys, 'index': i,
                            'ref_center': [float(rep.center_xy_um[0]), float(rep.center_xy_um[1])]})
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

    # --- 8. 연결: 요청 키 → 반환 행
    directions, funnels, rows_index = [], [], {}
    n_candidates, n_merged = len(found['candidates']), len(merged)
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
                reasons.add('no_roi_rows')
            flags = list(window['flags'])
            shift_here = (0.0, 0.0) if query_name == 'reference' else shift
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
                'merged_from': [m.region_id for m in members], 'max_count': None, 'area_um2': rep.area_um2,
                'layers': sorted({m.layer for m in members}), 'flags': flags,
                'status': 'ready' if ready else 'blocked', 'reasons': sorted(reasons),
                'roi_ids': sorted(r['roi_id'] for r in rows), 'n_rois': len(rows), 'n_rois_ready': len(ready),
                'score_um2': rep.score, 'score_layer': rep.layer, 'observed_fraction': window['observed_fraction'],
                'contributing_cases': [
                    {'case_key': c['case_key'], 'residual_um2': c['residual_um2'], 'change_kind': c['classification'],
                     'iou_global': c['iou_global'], 'iou_local': c['iou_local'],
                     'local_shift_um': c['local_shift_um'], 'flags': c['flags']}
                    for c in contrib[i]['contributing_cases']],
                'cnt_change_um2_by_layer': cnt_change[i]})
        funnel = _funnel(direction, n_candidates, n_merged, queries, top_k)
        funnels.append(funnel)
        if funnel['top_k_not_filled']:
            warnings.append({'code': 'top_k_not_filled', 'direction': direction, 'requested': top_k,
                             'found': len(queries)})
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
    commit = next((s.export_payload.get('producer_commit') for s in sides.values() if s.export_payload), None)
    all_queries = [q for d in directions for q in d['queries']]
    complete = (not warnings and all(q['status'] == 'ready' and q['n_rois'] == q['n_rois_ready'] for q in all_queries)
                and all(s.status == 'available' for s in sides.values()))
    funnel_doc = {'schema_version': 'candidate_funnel_v1', 'directions': funnels, 'top_k': top_k,
                  'top_k_scope': 'per_direction', 'index_coverage': coverage,
                  'direct_pairs_ready': direct_ready, 'direct_pairs_blocked': direct_blocked}
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
    payload = {'schema_version': candidates.SCHEMA_VERSION, 'roi_source': ROI_SOURCE, 'producer_commit': commit,
               'args': {'min_count': None, 'window_um': window_um, 'merge_overlap': args['merge_overlap'],
                        'max_candidates': top_k, 'representative_diameter_um': DIAMETER_UM},
               'diameter_um': float(DIAMETER_UM), 'change_args': change_args, 'alignment': alignment_summary,
               'change_map': {'stats': map_stats, 'pairing': pairing['stats'],
                              'cases_excluded': pairing['cases_excluded'],
                              'cases_only_in_reference': pairing['cases_only_in_reference'],
                              'cases_only_in_target': pairing['cases_only_in_target']},
               'electrode_set_basis': ELECTRODE_SET_BASIS,
               'inputs': inputs, 'directions': directions, 'funnel': funnel_doc, 'direct': direct_summary,
               'warnings': warnings, 'notes': notes, 'match_inputs': match_inputs, 'dl2_model_roles': dl2_model_roles,
               'match_files': {d: {k: f'{v}/review_rois.json' for k, v in item.items()}
                               for d, item in match_inputs.items()},
               'status': 'available' if complete else 'partial',
               'policy': {'roi_source': 'cnt_alignment_plus_hotspot_xor_residual_windows',
                          'merge': 'per_layer_window_overlap_intersection_over_min_area_union_find_region_id_order',
                          'window': 'square_centered_at_window_center_in_reference_coordinates',
                          'representative': 'score_um2_desc_layer_asc_region_id_asc',
                          'roi_per_contributing_case': 'one_manual_roi_per_(window, contributing_case)',
                          'wiring_change_um2': 'not_computed_here',
                          'mutual_one_way': 'not_computed_here',
                          'index_export': 'one_manual_roi_per_dataset_case_index_only_not_queries_no_region_provenance'}}
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
              'match_inputs': match_inputs, 'match_files': payload['match_files'], 'dl2_model_roles': dl2_model_roles}
    execution = {'command': 'candidates-change', 'args': {**change_args, 'max_operation_bytes': limit},
                 'inputs': exec_inputs, 'warnings': warnings, **result}
    candidates.write_text(out / 'execution.json', json.dumps(execution, ensure_ascii=False, indent=2) + '\n', track)
    result['outputs'] = sorted(result['outputs'] + ['execution.json'])
    return result
