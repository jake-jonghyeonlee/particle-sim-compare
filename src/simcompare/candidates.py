"""PLAN_v4 T1: 양방향 취약 영역 → 병합된 질의 ROI → 바로 매칭할 수 있는 export와 깔때기 표.

영역 계산·export는 기존 코드(compute_electrode_maps, export_review_rois)를 그대로 쓴다.
CA·delta·점수·상호(mutual/one_way) 표시는 하지 않는다(T3 이후). 확정되지 않은 후보는 수치에 들어가지 않는다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np

SCHEMA_VERSION = 'candidate_queries_v1'
OVERLAP_EPS = 1e-12
DIRECTIONS = (('reference_to_target', 'reference', 'target'), ('target_to_reference', 'target', 'reference'))
ROLE_REQUIRED_REASON = 'electrode_role_required_for_matching'
ROLE_REQUIRED_MESSAGE = ('DL2 매처는 모든 endpoint의 비어 있지 않은 role과 actual_layer를 요구한다. 역할을 추측하지 않는다. '
                         'config의 condition_assignment.electrode_roles에 전극별 role을 명시하라.')
SHORT_CIRCUIT_REASONS = ['electrode_polarity_unknown', 'source_layout_assets_unconfirmed']


@dataclass(frozen=True)
class RegionInfo:
    """병합 입력에 필요한 영역 요약(영역 계산 결과의 사본이 아니라 참조 값)."""
    region_id: str
    group: tuple          # (product_id, design_version, frame_id): 같은 제품·frame의 영역만 병합
    center_xy_um: tuple
    max_count: int
    area_um2: float
    layer: str
    frame_bbox_um: tuple


def window_bounds(center, window_um):
    half = window_um / 2.0
    return center[0]-half, center[1]-half, center[0]+half, center[1]+half


def window_overlap_ratio(first, second, window_um):
    """같은 크기 정사각 창 두 개의 intersection/min(area). 창은 frame으로 자르지 않은 공칭 창이다."""
    ox = max(0.0, window_um-abs(first[0]-second[0]))
    oy = max(0.0, window_um-abs(first[1]-second[1]))
    return ox*oy/(window_um*window_um)


def window_clipped(info, window_um, tolerance=1e-9):
    x0, y0, x1, y1 = info.frame_bbox_um
    a, b, c, d = window_bounds(info.center_xy_um, window_um)
    return a < x0-tolerance or b < y0-tolerance or c > x1+tolerance or d > y1+tolerance


def _rank_key(info):
    return -info.max_count, -info.area_um2, info.region_id


def merge_regions(regions, window_um, overlap):
    """R3: region_id 순서의 union-find(전이적). 대표 = max_count↑, area↑, region_id↑ 순으로 첫 번째.

    반환: [{'representative': RegionInfo, 'members': [RegionInfo...(region_id 순)]}], 대표 순위(max_count↓...) 정렬.
    """
    ordered = sorted(regions, key=lambda r: r.region_id)
    parent = list(range(len(ordered)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_group = {}
    for index, info in enumerate(ordered):
        by_group.setdefault(info.group, []).append(index)
    for indices in by_group.values():
        cx = np.array([ordered[i].center_xy_um[0] for i in indices], dtype=float)
        cy = np.array([ordered[i].center_xy_um[1] for i in indices], dtype=float)
        for a in range(len(indices)):
            if a+1 >= len(indices):
                break
            ox = np.maximum(0.0, window_um-np.abs(cx[a+1:]-cx[a]))
            oy = np.maximum(0.0, window_um-np.abs(cy[a+1:]-cy[a]))
            for offset in np.flatnonzero(ox*oy/(window_um*window_um) >= overlap-OVERLAP_EPS):
                ra, rb = find(indices[a]), find(indices[a+1+int(offset)])
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
    components = {}
    for index, info in enumerate(ordered):
        components.setdefault(find(index), []).append(info)
    merged = [{'representative': min(members, key=_rank_key), 'members': members} for members in components.values()]
    return sorted(merged, key=lambda m: _rank_key(m['representative']))


def _sha256_file(path):
    from simcompare.roi_export import file_sha256
    return file_sha256(path)   # streamed: an export can be gigabytes


def _canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def _endpoint_key(endpoint):
    return endpoint.get('electrode_id'), endpoint.get('role'), endpoint.get('actual_layer')


class _Side:
    """한 제품(config.reference 또는 config.target)의 입력·영역·export 상태."""

    def __init__(self, name):
        self.name = name
        self.real = None
        self.section = None
        self.root = None
        self.dataset = None
        self.maps = None
        self.evidence = None
        self.regions = []
        self.status = 'available'
        self.reasons = []
        self.warnings = []
        self.input_record = {}
        self.exported = None   # export 결과(dict) 또는 None
        self.rows = []
        self.export_path = None
        self.export_payload = None
        self.product_ids = []
        self.role_blocked = 0
        self.role_blocked_index = 0
        self.index = None      # index export 상태(_export_index가 채운다)


def _role_gate(side, rows):
    """실자료 ROI row 중 role/actual_layer가 비어 있는 endpoint가 있는 ready row를 매처 비대상(blocked)으로 돌린다.

    역할을 만들어 채우지 않는다. 반환은 막은 row 수. numeric_v2 합성 경로(side.real is None)는 건드리지 않는다.
    """
    if side.real is None:
        return 0

    def bad(ep):
        return not (isinstance(ep, dict) and all(isinstance(ep.get(k), str) and ep[k] for k in ('role', 'actual_layer')))
    n = 0
    for row in rows:
        if row['status'] != 'ready':
            continue
        endpoints = list(row.get('endpoints') or []) + [item.get('endpoint') for item in
                                                         (row.get('source_assets') or {}).get('layouts', [])]
        if any(bad(ep) for ep in endpoints):
            row['status'] = 'blocked'
            row['reasons'] = sorted(set(row['reasons']) | {ROLE_REQUIRED_REASON})
            n += 1
    return n


def _slim_rows(rows):
    """The fields of an export row the funnel and the coverage tables read, sharing the row's own objects: the full rows
    (assets, frame, provenance) are not kept once the file is written."""
    slim = []
    for row in rows:
        assets = row.get('source_assets')
        slim.append({'roi_id': row['roi_id'], 'status': row['status'], 'reasons': row['reasons'],
                     'source_region_id': row.get('source_region_id'),
                     'source_assets': None if assets is None else {'layouts': [{'endpoint': item.get('endpoint')}
                                                                               for item in assets.get('layouts', [])]}})
    return slim


def _prepare(cfg, name, min_count, diameter_um, reserved_bytes=0):
    from simcompare import pipeline
    from simcompare.electrode_map import compute_electrode_maps
    from simcompare.numeric_images import resolve_path
    from simcompare.numeric_schema import NumericContractError
    side = _Side(name)
    side.section = cfg.get(name)
    real = pipeline._real_source(cfg, side.section)
    side.real = real
    manifest_path = side.section.get('manifest_path', 'manifest.json')
    if real is not None:
        if side.section.get('condition_assignment') is None:
            raise NumericContractError('explicit_condition_assignments_required', name)
        side.evidence = pipeline._electrode_evidence(real[0], side.section)
        side.root, side.dataset = pipeline._configured_real(cfg, side.section, real)
        side.input_record['condition_assignment_sha256'] = _canonical_sha(side.section['condition_assignment'])
        for key in ('asset_manifest', 'electrode_import_record'):
            ref = side.section.get(key)
            side.input_record[key] = dict(ref) if isinstance(ref, dict) else None
        if not side.dataset.cases:
            side.status = 'unavailable'
            side.reasons = ['same_name_endpoints_unresolved']
        if side.dataset.provenance.get('unresolved_same_name_cases'):
            side.warnings.append('same_name_cases_unresolved')
    else:
        side.root, side.dataset = pipeline._numeric_input(cfg, side.section)
        ref = side.section.get('asset_manifest')
        side.input_record['asset_manifest'] = dict(ref) if isinstance(ref, dict) else None
    side.input_record.update(data_dir=str(Path(side.root).resolve()), manifest_path=manifest_path,
                             manifest_sha256=_sha256_file(resolve_path(Path(side.root), manifest_path)),
                             dataset_input_hash=side.dataset.provenance.get('input_hash'),
                             source_kind='real_hotspot_v1' if real is not None else 'numeric_v2')
    if side.status == 'available':
        side.maps = compute_electrode_maps(side.dataset, diameter_um=diameter_um, min_count=min_count,
                                           reserved_bytes=reserved_bytes)
        incomplete = sorted({r for layer in side.maps.layers if layer.status != 'available' for r in layer.reasons})
        if any(layer.status != 'available' for layer in side.maps.layers):
            side.warnings.append('map_coverage_incomplete')
            side.input_record['map_coverage_reasons'] = incomplete
        for layer in side.maps.layers:
            for region in layer.regions:
                frame = region.frame
                side.regions.append(RegionInfo(
                    region.region_id, (region.key.product_id, region.key.design_version, frame.frame_id),
                    (float(region.center_xy_um[0]), float(region.center_xy_um[1])), int(region.max_count),
                    float(region.area_um2), region.key.layer, tuple(float(v) for v in frame.bbox_um)))
    side.product_ids = sorted({r.group[0] for r in side.regions} | {c.identity.product_id for c in side.dataset.cases})
    return side


def _do_export(cfg, side, selections, folder, min_count, diameter_um, track, memo, reserved_bytes):
    """selections를 기존 export 경로(numeric 또는 real T1b)로 내보낸다. real인데 전극 증거가 없으면 None.

    파일은 한 번만 쓴다(role gate는 쓰기 전에 행에 적용). 쓴 파일을 다시 읽지 않고 exporter가 넘겨 준 행·payload를 쓴다.
    반환: (결과, export 경로, payload(rois 포함), role gate가 막은 행 수).
    """
    from simcompare import pipeline
    from simcompare.roi_export import export_hooks, export_review_rois
    blocked = [0]

    def gate(rows):
        blocked[0] = _role_gate(side, rows)
        return blocked[0]

    with export_hooks(row_gate=gate, asset_memo=memo, reserved_bytes=reserved_bytes) as captured:
        if side.real is not None:
            if side.evidence is None:
                return None
            folder.mkdir()
            track(folder, is_dir=True)
            for name in ('review_rois.json', 'review_rois.csv'):
                track(folder/name)
            derived = {**cfg, 'map': {'input': side.section, 'diameter_um': diameter_um, 'min_count': min_count},
                       'review_rois': selections}
            result = pipeline._real_roi_export(derived, folder, side.real, derived['map'],
                                               prepared=(side.root, side.dataset, side.maps))
        else:
            folder.mkdir()
            track(folder, is_dir=True)
            track(folder/'review_rois.json')
            result = export_review_rois(side.dataset, side.root, folder, maps=side.maps, selections=selections,
                                        asset_manifest=side.section.get('asset_manifest'))
    path = folder/'review_rois.json'
    if captured['payload'] is not None:
        payload = {**captured['payload'], 'rois': _slim_rows(captured['rows'])}
    else:   # pipeline wrote a small inspection file (blocked / unavailable): no rows, nothing worth re-reading
        payload = {'status': result['status'], 'reasons': list(result.get('reasons', [])), 'rois': [],
                   'excluded_records': [], 'producer_commit': None}
    return result, path, payload, blocked[0]


def _export_side(cfg, side, selected_ids, folder, min_count, diameter_um, track, memo, reserved_bytes):
    """선택한 대표 region만 기존 export 경로(numeric 또는 real T1b)로 내보낸다."""
    done = _do_export(cfg, side, {'map_region_ids': sorted(selected_ids)}, folder, min_count, diameter_um, track, memo,
                      reserved_bytes)
    if done is None:
        side.status = 'blocked'
        side.reasons = list(SHORT_CIRCUIT_REASONS)
        return
    side.exported, side.export_path, payload, side.role_blocked = done
    side.rows = payload['rois']
    side.export_payload = payload
    if payload.get('status') in ('blocked', 'unavailable'):
        side.status = payload['status']
        side.reasons = list(payload.get('reasons', []))


def _index_point(frame):
    """index ROI 위치: frame 중심이 valid면 그것, 아니면 row-major 첫 valid 셀 중심. valid 셀이 없으면 None."""
    x0, y0, x1, y1 = frame.bbox_um
    pitch = frame.pixel_pitch_um
    centre = [(x0+x1)/2.0, (y0+y1)/2.0]
    col, row = int((centre[0]-x0)/pitch), int((centre[1]-y0)/pitch)
    if 0 <= row < frame.shape_yx[0] and 0 <= col < frame.shape_yx[1] and frame.valid[row, col]:
        return centre
    cells = np.argwhere(frame.valid)
    if not len(cells):
        return None
    row, col = cells[0]
    return [float(x0+(int(col)+0.5)*pitch), float(y0+(int(row)+0.5)*pitch)]


def _export_index(cfg, side, folder, min_count, diameter_um, track, memo, reserved_bytes):
    """데이터셋 case마다 명시 manual ROI 하나를 내보내 role/layer union이 제품 전체 전극을 덮게 한다(index 전용)."""
    requests, own_blocked = [], {}
    for case in sorted(side.dataset.cases, key=lambda c: tuple(str(v) for v in asdict(c.identity).values())):
        identity = asdict(case.identity)
        point = _index_point(side.dataset.frames[identity['frame_id']])
        if point is None:
            own_blocked[tuple(str(v) for v in identity.values())] = ['index_frame_has_no_valid_cell']
            continue
        requests.append({**identity, 'center_xy_um': point, 'endpoints': [asdict(e) for e in case.endpoints],
                         'failure_type': case.failure_type})
    state = {'status': 'available', 'reasons': [], 'rows': [], 'path': None, 'exported': None, 'payload': None,
             'own_blocked': own_blocked, 'requested': len(requests)+len(own_blocked)}
    side.index = state
    if not requests:
        state['status'] = 'blocked'
        state['reasons'] = ['index_no_exportable_case']
        return
    done = _do_export(cfg, side, {'manual': requests}, folder, min_count, diameter_um, track, memo, reserved_bytes)
    if done is None:
        state['status'] = 'blocked'
        state['reasons'] = list(SHORT_CIRCUIT_REASONS)
        return
    state['exported'], state['path'], payload, side.role_blocked_index = done
    state['rows'] = payload['rois']
    state['payload'] = payload
    if payload.get('status') in ('blocked', 'unavailable'):
        state['status'] = payload['status']
        state['reasons'] = list(payload.get('reasons', []))


def _index_coverage(side):
    """index export가 데이터셋 전극 전체를 (role, layer)별로 덮는지 센다."""
    state = side.index
    universe = {_endpoint_key(asdict(e)) for case in side.dataset.cases for e in case.endpoints}
    ready = [r for r in state['rows'] if r['status'] == 'ready']
    covered = {_endpoint_key(item['endpoint']) for row in ready
               for item in (row.get('source_assets') or {}).get('layouts', [])} & universe
    reasons = {}
    for row in state['rows']:
        if row['status'] != 'ready':
            for code in row['reasons']:
                reasons[code] = reasons.get(code, 0)+1
    for codes in state['own_blocked'].values():
        for code in codes:
            reasons[code] = reasons.get(code, 0)+1
    if state['status'] != 'available':
        for code in state['reasons']:
            reasons[code] = reasons.get(code, 0)+1
    totals, hits = _count_by(universe), _count_by(covered)
    by_role_layer = {key: {'electrodes_covered': hits.get(key, 0), 'electrodes_total': total}
                     for key, total in totals.items()}
    n_blocked = len(state['rows'])-len(ready)+len(state['own_blocked'])
    complete = (state['status'] == 'available' and n_blocked == 0 and covered == universe)
    return {'stage': 'index_coverage', 'product': side.name, 'dataset_cases': len(side.dataset.cases),
            'index_rois_ready': len(ready), 'index_rois_blocked': n_blocked,
            'blocked_reasons': dict(sorted(reasons.items())),
            'electrodes_covered': len(covered), 'electrodes_total': len(universe),
            'by_role_layer': by_role_layer, 'complete': complete,
            'missing_by_role_layer': _count_by(universe-covered)}


def _query_export_note(side):
    """질의 export는 선택한 영역의 case 전극 layout만 참조한다. 데이터셋 전극 전체와 비교해 부족하면 알린다(정보)."""
    universe = {_endpoint_key(asdict(e)) for case in side.dataset.cases for e in case.endpoints}
    covered = {_endpoint_key(item['endpoint']) for row in side.rows if row['status'] == 'ready'
               for item in (row.get('source_assets') or {}).get('layouts', [])}
    missing = sorted(universe-covered, key=lambda k: tuple(str(v) for v in k))
    if not missing:
        return None
    return {'code': 'index_union_partial', 'product': side.name, 'severity': 'info',
            'electrodes_in_dataset': len(universe), 'electrodes_in_export': len(covered & universe),
            'electrodes_missing': len(missing),
            'missing_by_role_layer': _count_by(missing),
            'detail': f'{side.name}_export는 질의 전용이다. 선택한 영역의 case 전극 layout만 참조하므로 DL2 index로 쓰면 '
                      f'role_layer_unions가 데이터셋 전극 전체를 덮지 못한다. index에는 {side.name}_index_export를 쓴다.'}


def _count_by(missing):
    counts = {}
    for _, role, layer in missing:
        key = f'{role}|{layer}'
        counts[key] = counts.get(key, 0)+1
    return dict(sorted(counts.items()))


def _query_id(direction, region_id):
    return 'cand_'+hashlib.sha256(f'{direction}|{region_id}'.encode()).hexdigest()[:16]


def _funnel(direction, n_regions, n_merged, n_cut, queries, max_candidates):
    kept = len(queries)
    ready = sum(q['status'] == 'ready' for q in queries)
    blocked_reasons = {}
    for q in queries:
        if q['status'] != 'ready':
            for code in q['reasons']:
                blocked_reasons[code] = blocked_reasons.get(code, 0)+1
    stages = [
        {'stage': 'regions', 'count': n_regions, 'dropped': 0, 'reasons': {}},
        {'stage': 'merged_queries', 'count': n_merged, 'dropped': n_regions-n_merged,
         'reasons': {'merged_overlap': n_regions-n_merged} if n_regions > n_merged else {}},
        {'stage': 'after_max_candidates', 'count': kept, 'dropped': n_cut,
         'reasons': {'cut_by_max_candidates': n_cut} if n_cut else {}},
        {'stage': 'exported_ready', 'count': ready, 'dropped': kept-ready, 'reasons': dict(sorted(blocked_reasons.items()))}]
    return {'direction': direction, 'stages': stages, 'regions': n_regions, 'merged_queries': n_merged,
            'max_candidates': max_candidates, 'cut_by_max_candidates': n_cut, 'exported_ready': ready,
            'blocked': kept-ready,
            'note': 'reasons 값은 사유 코드별 질의 수이며 한 질의가 여러 사유를 가질 수 있어 dropped와 합이 다를 수 있다'}


def _validate_args(min_count, window_um, merge_overlap, max_candidates):
    from simcompare.numeric_schema import NumericContractError
    if type(min_count) is not int or min_count < 1:
        raise NumericContractError('invalid_count_threshold', '--min-count는 1 이상의 정수여야 합니다')
    for label, value in (('--window-um', window_um), ('--merge-overlap', merge_overlap)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise NumericContractError('invalid_candidate_argument', label+'는 유한한 수여야 합니다')
    if window_um <= 0:
        raise NumericContractError('invalid_candidate_argument', '--window-um는 0보다 커야 합니다')
    if not 0 < merge_overlap <= 1:
        raise NumericContractError('invalid_candidate_argument', '--merge-overlap은 (0, 1] 범위여야 합니다')
    if max_candidates is not None and (type(max_candidates) is not int or max_candidates < 1):
        raise NumericContractError('invalid_candidate_argument', '--max-candidates는 1 이상의 정수여야 합니다')


def run_candidates(cfg, out, *, min_count, window_um, merge_overlap, max_candidates=None):
    """새 출력 폴더에 candidate_queries_v1.json, funnel.json/.csv, reference_export/, target_export/, reference_index_export/, target_index_export/, execution.json을 쓴다.

    실패하면 이 실행이 만든 파일과 폴더만 지운다(접두 패턴·재귀 삭제 없음). 이미 있는 출력 경로는 거부하고 건드리지 않는다.
    """
    from simcompare import pipeline
    from simcompare.numeric_schema import NumericContractError
    _validate_args(min_count, window_um, merge_overlap, max_candidates)
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise NumericContractError('output_dir_exists', str(out))
    diameter_um = 5 if 'map' not in cfg else pipeline._map_spec(cfg).get('diameter_um', 5)
    for name in ('reference', 'target'):
        if not isinstance(cfg.get(name), dict):
            raise NumericContractError('numeric_input_required', name)
    kinds = [pipeline._real_source(cfg, cfg[name]) is not None for name in ('reference', 'target')]
    if any(kinds) and not all(kinds):
        raise NumericContractError('mixed_real_and_numeric_source_formats')
    created_files, created_dirs = [], []

    def track(path, is_dir=False):
        (created_dirs if is_dir else created_files).append(Path(path))

    out.mkdir()
    track(out, is_dir=True)
    try:
        return _run(cfg, out, track, min_count, window_um, merge_overlap, max_candidates, diameter_um)
    except BaseException:
        for path in created_files:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        for path in reversed(created_dirs):
            try:
                path.rmdir()
            except OSError:
                pass
        raise


def _write_text(path, text, track):
    track(path)
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:  # Path.write_text(newline=) is 3.10+
        handle.write(text)


def _run(cfg, out, track, min_count, window_um, merge_overlap, max_candidates, diameter_um):
    from simcompare.adapters.real_hotspot import MAP_SLIM_ROW_BYTES, MAP_REGION_INFO_BYTES
    from simcompare.roi_export import AssetMemo
    sides, plans = {}, {}
    memo = AssetMemo()   # asset PNGs decoded for one product's exports are not decoded again for its second export
    reserved = 0         # what the first product still holds (rows, regions) while the second one's map is built
    for name in ('reference', 'target'):
        side = sides[name] = _prepare(cfg, name, min_count, diameter_um, reserved)
        merged = merge_regions(side.regions, window_um, merge_overlap)
        cut = merged[max_candidates:] if max_candidates is not None else []
        kept = merged[:max_candidates] if max_candidates is not None else merged
        plans[name] = {'n_regions': len(side.regions), 'n_merged': len(merged), 'kept': kept, 'cut': cut}
        if side.status != 'unavailable':
            ids = [m['representative'].region_id for m in kept]
            _export_side(cfg, side, ids, out/f'{name}_export', min_count, diameter_um, track, memo, reserved)
            if side.status != 'unavailable':   # the query export may have found the product unavailable
                _export_index(cfg, side, out/f'{name}_index_export', min_count, diameter_um, track, memo, reserved)
        side.maps = None   # counts, region masks and links are only needed for the exports above
        reserved += MAP_SLIM_ROW_BYTES * (len(side.rows) + (len(side.index['rows']) if side.index else 0))
        reserved += MAP_REGION_INFO_BYTES * len(side.regions)
    memo = None

    warnings, notes = [], []
    directions, funnels = [], []
    for direction, query_name, index_name in DIRECTIONS:
        side = sides[query_name]
        plan = plans[query_name]
        rows_by_region = {}
        for row in side.rows:
            rows_by_region.setdefault(row.get('source_region_id'), []).append(row)
        excluded = {}
        if side.export_payload:
            for item in side.export_payload.get('excluded_records', []):
                if item.get('source_region_id'):
                    excluded.setdefault(item['source_region_id'], set()).update(item['reasons'])
        queries = []
        for merge in plan['kept']:
            rep, members = merge['representative'], merge['members']
            rows = rows_by_region.get(rep.region_id, [])
            ready = [r for r in rows if r['status'] == 'ready']
            reasons = set(excluded.get(rep.region_id, ()))
            for row in rows:
                reasons.update(row['reasons'])
            if side.status in ('blocked', 'unavailable'):
                reasons.update(side.reasons)
            if not rows and not reasons:
                reasons.add('no_roi_rows')
            flags = []
            if window_clipped(rep, window_um):
                flags.append('window_clipped')
            elif any(window_clipped(m, window_um) for m in members):
                flags.append('merged_member_window_clipped')
            if len(rows) > len(ready) and ready:
                flags.append('roi_rows_partially_blocked')
            queries.append({
                'query_id': _query_id(direction, rep.region_id), 'product_id': rep.group[0],
                'design_version': rep.group[1], 'frame_id': rep.group[2],
                'center_xy_um': list(rep.center_xy_um), 'window_um': window_um, 'region_id': rep.region_id,
                'merged_from': [m.region_id for m in members], 'max_count': rep.max_count,
                'area_um2': rep.area_um2, 'layers': sorted({m.layer for m in members}), 'flags': flags,
                'status': 'ready' if ready else 'blocked', 'reasons': sorted(reasons),
                'roi_ids': sorted(r['roi_id'] for r in rows), 'n_rois': len(rows), 'n_rois_ready': len(ready)})
        funnels.append(_funnel(direction, plan['n_regions'], plan['n_merged'], len(plan['cut']), queries, max_candidates))
        directions.append({'direction': direction, 'query_product': query_name, 'index_product': index_name,
                           'query_export': f'{query_name}_export/review_rois.json' if sides[query_name].export_path else None,
                           'index_export': (f'{index_name}_index_export/review_rois.json'
                                            if sides[index_name].index and sides[index_name].index['path'] else None),
                           'queries': queries})
    for name, side in sides.items():
        for code in side.warnings:
            warnings.append({'code': code, 'product': name})
        if side.role_blocked or side.role_blocked_index:
            warnings.append({'code': ROLE_REQUIRED_REASON, 'product': name, 'detail': ROLE_REQUIRED_MESSAGE,
                             'query_rows_blocked': side.role_blocked,
                             'index_rows_blocked': side.role_blocked_index})
        if side.status in ('blocked', 'unavailable'):
            warnings.append({'code': 'side_'+side.status, 'product': name, 'reasons': side.reasons})
        if side.export_path is not None:
            partial = _query_export_note(side)
            if partial is not None:
                notes.append(partial)
    coverage = {}
    for name, side in sides.items():
        if side.index is None:
            continue
        coverage[name] = _index_coverage(side)
        if not coverage[name]['complete']:
            warnings.append({'code': 'index_coverage_incomplete', 'product': name,
                             'electrodes_covered': coverage[name]['electrodes_covered'],
                             'electrodes_total': coverage[name]['electrodes_total'],
                             'index_rois_blocked': coverage[name]['index_rois_blocked'],
                             'reasons': sorted(coverage[name]['blocked_reasons']),
                             'missing_by_role_layer': coverage[name]['missing_by_role_layer']})

    inputs = {'config_sha256': cfg.get('_config_sha256'),
              'reference': {**sides['reference'].input_record, 'product_ids': sides['reference'].product_ids},
              'target': {**sides['target'].input_record, 'product_ids': sides['target'].product_ids}}
    for name, side in sides.items():
        if side.export_path is not None:
            inputs[name]['export_sha256'] = _sha256_file(side.export_path)
        if side.index is not None and side.index['path'] is not None:
            inputs[name]['index_export_sha256'] = _sha256_file(side.index['path'])
    commit = next((s.export_payload.get('producer_commit') for s in sides.values() if s.export_payload), None)
    n_cut = sum(len(plans[n]['cut']) for n in plans)
    all_queries = [q for d in directions for q in d['queries']]
    complete = (not warnings and all(q['status'] == 'ready' and q['n_rois'] == q['n_rois_ready'] for q in all_queries)
                and all(s.status == 'available' for s in sides.values()))
    funnel_doc = {'schema_version': 'candidate_funnel_v1', 'directions': funnels,
                  'cut_by_max_candidates': n_cut, 'max_candidates': max_candidates,
                  'max_candidates_scope': 'per_direction', 'index_coverage': coverage}
    match_inputs = {direction: {'query': f'{query_name}_export', 'index': f'{index_name}_index_export'}
                    for direction, query_name, index_name in DIRECTIONS}
    roles = set()
    for side in sides.values():
        dataset = getattr(side, 'dataset', None)
        if dataset is None:
            continue
        # DL2 model.roles에 넣을 목록: 설정된 role 전체(pair 표 기준 정책이면 표의 모든 전극)와 실제 endpoint role.
        roles.update((dataset.provenance or {}).get('electrode_roles_effective', {}).values())
        roles.update(ep.role for case in dataset.cases for ep in case.endpoints if ep.role)
    dl2_model_roles = sorted(roles)
    payload = {'schema_version': SCHEMA_VERSION, 'producer_commit': commit,
               'args': {'min_count': min_count, 'window_um': window_um, 'merge_overlap': merge_overlap,
                        'max_candidates': max_candidates, 'representative_diameter_um': diameter_um},
               'inputs': inputs, 'directions': directions, 'funnel': funnel_doc, 'warnings': warnings, 'notes': notes,
               'match_inputs': match_inputs, 'dl2_model_roles': dl2_model_roles,
               'match_files': {d: {k: f'{v}/review_rois.json' for k, v in item.items()}
                               for d, item in match_inputs.items()},
               'status': 'available' if complete else 'partial',
               'policy': {'merge': 'window_overlap_intersection_over_min_area_union_find_region_id_order',
                          'window': 'square_centered_at_region_center_unclipped_for_overlap',
                          'representative': 'max_count_desc_area_desc_region_id_asc',
                          'mutual_one_way': 'not_computed_here',
                          'index_export': 'one_manual_roi_per_dataset_case_index_only_not_queries_no_region_provenance'}}
    outputs = []

    def emit(name, text):
        _write_text(out/name, text, track)
        outputs.append(name)

    emit('candidate_queries_v1.json', json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    emit('funnel.json', json.dumps(funnel_doc, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    csv_path = out/'funnel.csv'
    track(csv_path)
    with csv_path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, lineterminator='\n')
        writer.writerow(['direction', 'stage', 'count', 'dropped', 'reasons'])
        for f in funnels:
            for s in f['stages']:
                writer.writerow([f['direction'], s['stage'], s['count'], s['dropped'],
                                 ';'.join(f'{k}:{v}' for k, v in s['reasons'].items())])
        for name, item in coverage.items():
            writer.writerow([f'index_{name}', 'index_coverage', item['index_rois_ready'], item['index_rois_blocked'],
                             ';'.join(f'{k}:{v}' for k, v in item['blocked_reasons'].items())])
    outputs.append('funnel.csv')
    for name, side in sides.items():
        if side.export_path is not None:
            for item in sorted(Path(p).name for p in side.exported.get('outputs', [])):
                outputs.append(f'{name}_export/{item}')
        if side.index is not None and side.index['path'] is not None:
            for item in sorted(Path(p).name for p in side.index['exported'].get('outputs', [])):
                outputs.append(f'{name}_index_export/{item}')
    result = {'status': payload['status'], 'reasons': sorted({w['code'] for w in warnings}),
              'outputs': sorted(outputs), 'exit_code': 0 if complete else 3,
              'n_queries': {d['direction']: len(d['queries']) for d in directions},
              'funnel': {f['direction']: {s['stage']: s['count'] for s in f['stages']} for f in funnels},
              'index_coverage': {n: {k: c[k] for k in ('dataset_cases', 'index_rois_ready', 'index_rois_blocked',
                                                      'electrodes_covered', 'electrodes_total')}
                                 for n, c in coverage.items()},
              'match_inputs': match_inputs, 'match_files': payload['match_files'], 'dl2_model_roles': dl2_model_roles}
    execution = {'command': 'candidates', **result}
    _write_text(out/'execution.json', json.dumps(execution, ensure_ascii=False, indent=2)+'\n', track)
    result['outputs'] = sorted(result['outputs']+['execution.json'])
    return result
