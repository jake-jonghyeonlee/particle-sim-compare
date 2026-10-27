"""candidates-dv: ΔV 등급으로 가중한 취약점 ROI(두 제품, real_hotspot_v1 전용).

설계: PLAN v2.2 > v2.1 > v2. 한 번의 지도 패스에서 count_all 과 등급 지도(count_high, count_visible, grade_max)를 만든다
(electrode_map.compute_graded_electrode_maps). ROI 영역은 선정 지도(--roi-grade high 면 count_high, low_or_high 면 count_visible)가
--min-count 이상인 4-neighbour 연결 성분이며, 병합은 지도 키(source/process/exposure/layer) 안에서만 한다.
count_all >= K 영역(비가중)은 비교용 목록으로만 계산하고 export 하지 않는다.

재사용(candidates.py 는 수정하지 않는다. 비공개 이름에 의존하는 곳은 아래 목록이다):
  candidates.prepare_side, merge_regions, RegionInfo, window_bounds, window_clipped, build_funnel, output_tracker, write_text,
  write_funnel_csv, index_coverage, export_index, DIRECTIONS 와 비공개 _export_side·_query_id·_query_export_note·_sha256_file·_validate_args.
  export 는 roi_export.export_review_rois 가 side.maps 에서 대표 region_id 를 찾는 경로를 그대로 쓴다(roi_export.py 불변).
"""
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

import numpy as np

from . import candidates
from .electrode_map import (ROI_GRADES, ElectrodeMaps, _components, _run_counts, compute_graded_electrode_maps,
                            graded_extra_bytes, stable_region_id)
from .heatmap import write_heatmap
from .numeric_schema import NumericContractError

VULNERABILITY_SCHEMA = 'vulnerability_v1'
SCORE_RULE_VERSION = 'dv_roi_rank_v1'
GRADE_CODE = {'None': 1, 'Low': 2, 'High': 3}
GRADE_NAME = {0: 'ungraded', 1: 'None', 2: 'Low', 3: 'High'}
GRADE_COLORS = {0: (255, 255, 255), 1: (255, 236, 160), 2: (255, 152, 0), 3: (198, 40, 40)}
GRADE_LABELS = {0: '0 not covered / ungraded only', 1: '1 None', 2: '2 Low', 3: '3 High'}
UNGRADED_SAMPLE = 20


def _bad(code, detail=''):
    return NumericContractError(code, detail)


def _validate_dv_args(roi_grade):
    if roi_grade not in ROI_GRADES:
        raise _bad('invalid_candidates_dv_argument', f'--roi-grade 는 {" | ".join(ROI_GRADES)} 중 하나여야 합니다(기본값 없음): {roi_grade!r}')


def _grade_color(value):
    return GRADE_COLORS[int(value)]


def _grade_label(value):
    return GRADE_LABELS[int(value)]


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'


def _key_fields(key):
    return {'source_id': key.source_id, 'process_step_id': key.process_step_id, 'exposure_id': key.exposure_id,
            'layer': key.layer, 'unit_id': key.unit_id}


# ---- dv-grade 입력 -----------------------------------------------------------------------------------------------

def _load_grades(path, label):
    from .dv_grade import DvGradeLoadError, MANIFEST_NAME, load_dv_grades
    if path is None or not str(path).strip():
        raise _bad('invalid_candidates_dv_argument', f'--dv-grades-{label} 가 필요합니다(기본값 없음)')
    folder = Path(path)
    try:
        loaded = load_dv_grades(folder)
    except DvGradeLoadError as exc:
        raise _bad(exc.code, f'--dv-grades-{label} {path}: {exc}') from exc
    loaded['dir'] = str(folder.resolve())
    loaded['manifest_sha256'] = candidates._sha256_file(folder / MANIFEST_NAME)
    return loaded


def _link_side(name, side, dv):
    """dv-grade 출력 ↔ 이 제품의 데이터셋. 쓰기 전에 모두 검사한다. (case_id -> grade, 등급 없는 case_id 목록)."""
    manifest = dv['manifest']
    cases = {case.identity.case_id: case for case in side.dataset.cases}
    if cases and side.product_ids != [manifest['product_id']]:
        raise _bad('dv_grade_product_mismatch',
                   f'{name}: 제품 {side.product_ids} 인데 dv-grade manifest 의 product_id 는 {manifest["product_id"]!r} 입니다')
    grade_of = {}
    for row in dv['by_case']:
        case = cases.get(row['case_name'])
        if case is None:
            raise _bad('dv_grade_product_mismatch', f'{name}: dv-grade case {row["case_name"]!r} 가 이 제품의 데이터셋에 없습니다')
        want = {(row['electrode_a'], row['layer_a']), (row['electrode_b'], row['layer_b'])}
        got = {(endpoint.electrode_id, endpoint.actual_layer) for endpoint in case.endpoints}
        if want != got or row['defect'] != case.failure_type:
            raise _bad('dv_grade_case_endpoint_mismatch',
                       f'{name}: {row["case_name"]}: dv-grade {sorted(want)} {row["defect"]} / 데이터셋 {sorted(got)} {case.failure_type}')
        if row['grade'] not in GRADE_CODE:
            raise _bad('dv_grade_invalid_value', f'{name}: {row["case_name"]}: grade {row["grade"]!r}')
        grade_of[row['case_name']] = row['grade']
    return grade_of, sorted(case_id for case_id in cases if case_id not in grade_of)


def _dv_record(dv):
    manifest = dv['manifest']
    return {'dir': dv['dir'], 'manifest_sha256': dv['manifest_sha256'], 'sheet_sha256': (manifest.get('sheet') or {}).get('sha256'),
            'threshold_v': manifest.get('threshold_v'), 'grade_rule_version': manifest.get('grade_rule_version'),
            'product_id': manifest.get('product_id'), 'pair_count': manifest.get('pair_count')}


# ---- 영역 속성·순위 ----------------------------------------------------------------------------------------------

def _first_cell(mask):
    rows, cols = np.nonzero(mask)
    return int(rows[0]), int(cols[0])


def _layer_attributes(layer, graded, select_name, threshold, diameter_um, guard):
    """한 layer 의 (등급 영역 속성 dict 목록, 비비가중 영역 속성 dict 목록). 영역 마스크는 한 번에 하나씩 푼다."""
    counts = layer.counts
    sel_map = graded.count_high if select_name == 'high' else graded.count_visible
    frame, pitch = layer.frame, layer.frame.pixel_pitch_um
    graded_rows = []
    for region in layer.regions:
        mask = region.mask
        gmax = int(graded.grade_max[mask].max())
        graded_rows.append({
            'region': region, 'first_cell': _first_cell(mask), 'sel_max_count': int(sel_map[mask].max()),
            'high_max_count': int(graded.count_high[mask].max()), 'visible_max_count': int(graded.count_visible[mask].max()),
            'all_max_in_mask': int(counts[mask].max()), 'grade_max_code': gmax})
    unweighted = []
    active = (counts >= threshold) & frame.valid
    if active.any():
        guard(int(_run_counts(active).sum()))
        for inside, bbox in _components(active):
            r0, c0, r1, c1 = bbox
            x0, y0 = frame.origin_xy_um
            physical = (x0 + c0 * pitch, y0 + r0 * pitch, x0 + c1 * pitch, y0 + r1 * pitch)
            chosen = inside & (sel_map >= threshold)
            sel_centre = None
            if chosen.any():
                rows, cols = np.nonzero(chosen)
                sel_centre = [x0 + (int(cols.min()) + int(cols.max()) + 1) / 2.0 * pitch, y0 + (int(rows.min()) + int(rows.max()) + 1) / 2.0 * pitch]
            sel_max = int(sel_map[inside].max())
            unweighted.append({
                'region_id': stable_region_id(layer.key, float(diameter_um), threshold, frame, inside),
                'key': layer.key, 'bbox_um': list(map(float, physical)),
                'center_xy_um': [float((physical[0] + physical[2]) / 2), float((physical[1] + physical[3]) / 2)],
                'area_um2': float(inside.sum()) * pitch * pitch, 'max_count': int(counts[inside].max()),
                'sel_max_count': sel_max, 'high_max_count': int(graded.count_high[inside].max()),
                'visible_max_count': int(graded.count_visible[inside].max()), 'grade_max_code': int(graded.grade_max[inside].max()),
                'sel_area_um2': float(chosen.sum()) * pitch * pitch, 'sel_centre_xy_um': sel_centre,
                'flags': [] if sel_max >= threshold else ['below_graded_threshold'],
                'contains': [index for index, row in enumerate(graded_rows) if inside[row['first_cell']]]})
    return graded_rows, unweighted


def _rank_score(row):
    region = row['region']
    return (-row['sel_max_count'], -row['grade_max_code'], -row['all_max_in_mask'], -float(region.area_um2), region.region_id)


def _union_regions(regions):
    """합성 ElectrodeRegion: 대표(첫 region) 의 id·중심·bbox·속성, member 들의 선정 마스크 합집합, CaseLink 합집합(case identity 중복 제거, 순위 순)."""
    representative = regions[0]
    if len(regions) == 1:
        return representative
    mask = representative.mask
    for region in regions[1:]:
        mask = mask | region.mask
    links, seen = [], set()
    for region in regions:
        for link in region.cases:
            identity = tuple(sorted(link.identity.items()))
            if identity not in seen:
                seen.add(identity)
                links.append(link)
    electrodes = tuple(sorted({name for region in regions for name in region.electrodes}))
    return replace(representative, mask=mask, cases=tuple(links), electrodes=electrodes)


def _window_cells(frame, mask, center, window_um):
    """(전체 셀 수, 창 안 셀 수): 셀 중심이 창 [x0, x1) × [y0, y1) 안에 있는 셀. mask 는 이미 frame.valid 안이다."""
    rows, cols = np.nonzero(mask)
    pitch = frame.pixel_pitch_um
    ox, oy = frame.origin_xy_um
    x = ox + (cols + 0.5) * pitch
    y = oy + (rows + 0.5) * pitch
    a, b, c, d = candidates.window_bounds(center, window_um)
    return int(rows.size), int(np.count_nonzero((x >= a) & (x < c) & (y >= b) & (y < d)))


def _centre_invalid(frame, center):
    x0, y0, x1, y1 = frame.bbox_um
    if not (x0 <= center[0] < x1 and y0 <= center[1] < y1):
        return True
    col = int((center[0] - x0) / frame.pixel_pitch_um)
    row = int((center[1] - y0) / frame.pixel_pitch_um)
    return not bool(frame.valid[row, col])


# ---- 등급 지도 파일 ----------------------------------------------------------------------------------------------

def _write_grade_maps(out, side_name, layer, graded, track, args):
    """layer 하나의 count_high·count_visible·grade_max PNG + sidecar. 상대 경로 표를 돌려준다."""
    if graded.count_high is None:
        return None
    folder = out / 'grade_maps'
    if not folder.exists():
        track(folder, is_dir=True)
    token = hashlib.sha256(json.dumps(asdict(layer.key), sort_keys=True).encode()).hexdigest()[:20]
    provenance = {'map_kind': 'grade_map', 'side': side_name, 'key': asdict(layer.key), 'min_count': args['min_count'],
                  'roi_grade': args['roi_grade'], 'grade_rule': 'dv_grade_rule_v1'}
    entry = {'key': asdict(layer.key)}
    for kind, array, kwargs in (
            ('count_high', graded.count_high, {'unit': 'distinct_electrodes_high_grade', 'title': 'Distinct electrodes (High)'}),
            ('count_visible', graded.count_visible, {'unit': 'distinct_electrodes_low_or_high_grade', 'title': 'Distinct electrodes (Low or High)'}),
            ('grade_max', graded.grade_max, {'unit': 'grade_max_0_uncovered_1_none_2_low_3_high', 'title': 'Max grade',
                                             'palette': _grade_color, 'labels': _grade_label})):
        base = f'{side_name}_{token}_{kind}'
        track(folder / f'{base}.png')
        track(folder / f'{base}.json')
        result = write_heatmap(folder, base, array, layer.frame, status=layer.status, reasons=layer.reasons,
                               provenance={**provenance, 'grade_map': kind}, **kwargs)
        entry[kind] = {'png': f'grade_maps/{result["png"]}', 'sidecar': f'grade_maps/{result["sidecar"]}'}
    return entry


# ---- 실행 --------------------------------------------------------------------------------------------------------

def run_candidates_dv(cfg, out, *, dv_grades_reference, dv_grades_target, min_count, roi_grade, window_um, merge_overlap,
                      max_candidates=None):
    """새 출력 폴더에 candidate_queries_v1.json, vulnerability_v1.json, grade_maps/, funnel.json/.csv, {reference,target}_export/,
    {reference,target}_index_export/, execution.json 을 쓴다. 입력 연결 오류는 아무것도 쓰기 전에 종료 코드 2 로 끝난다.
    실패하면 이 실행이 만든 파일과 폴더만 지운다."""
    from simcompare import pipeline
    candidates._validate_args(min_count, window_um, merge_overlap, max_candidates)
    _validate_dv_args(roi_grade)
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise _bad('output_dir_exists', str(out))
    diameter_um = 5 if 'map' not in cfg else pipeline._map_spec(cfg).get('diameter_um', 5)
    for name in ('reference', 'target'):
        if not isinstance(cfg.get(name), dict):
            raise _bad('numeric_input_required', name)
    kinds = [pipeline._real_source(cfg, cfg[name]) is not None for name in ('reference', 'target')]
    if not all(kinds):
        raise _bad('dv_candidates_requires_real_hotspot', 'candidates-dv 는 reference·target 모두 real_hotspot_v1 이어야 합니다')
    dv = {'reference': _load_grades(dv_grades_reference, 'reference'), 'target': _load_grades(dv_grades_target, 'target')}
    if dv['reference']['dir'] == dv['target']['dir']:
        raise _bad('dv_grades_dir_shared', f'두 제품이 같은 dv-grade 폴더를 가리킵니다: {dv["reference"]["dir"]}')
    sides = {name: candidates.prepare_side(cfg, name, diameter_um) for name in ('reference', 'target')}
    notes, links = [], {}
    for name, side in sides.items():
        links[name] = _link_side(name, side, dv[name])
        recorded_root = Path(str(dv[name]['manifest'].get('root', ''))).name
        source_root = (side.real[1] or {}).get('source_root_name') if side.real else None
        if source_root and recorded_root and recorded_root != source_root:
            notes.append({'code': 'dv_grade_root_name_differs', 'product': name, 'severity': 'warning',
                          'dv_grade_root': recorded_root, 'import_source_root': source_root})
    if dv['reference']['manifest'].get('outputs') == dv['target']['manifest'].get('outputs'):
        notes.append({'code': 'dv_grades_identical_outputs', 'product': 'both', 'severity': 'warning',
                      'detail': '두 제품의 dv-grade 출력 파일 해시가 모두 같습니다(복사본일 수 있음). 제품이 달라도 전극·전압이 같으면 같을 수 있어 오류로 보지 않습니다.'})
    track, cleanup = candidates.output_tracker()
    out.mkdir()
    track(out, is_dir=True)
    try:
        args = {'min_count': min_count, 'window_um': window_um, 'merge_overlap': merge_overlap, 'max_candidates': max_candidates,
                'representative_diameter_um': diameter_um, 'roi_grade': roi_grade}
        return _run(cfg, out, track, sides, dv, links, notes, args, diameter_um)
    except BaseException:
        cleanup()
        raise


def _plan_side(cfg, out, name, side, dv, grade_of, ungraded, args, diameter_um, reserved, track, memo):
    """한 제품: 등급 지도 계산 → 속성·순위·병합 → 대표만 합성 영역으로 바꾼 maps → export. 계획과 속성 표를 돌려준다."""
    from simcompare.adapters.real_hotspot import guard_stage_map_counting
    threshold, window_um, roi_grade = args['min_count'], args['window_um'], args['roi_grade']
    plan = {'graded_rows': [], 'unweighted': [], 'infos': [], 'merged': [], 'kept': [], 'cut': [], 'grade_maps': [], 'by_id': {},
            'pairs_by_region': {}, 'rank_unweighted': {}}
    if side.status != 'available':
        return plan
    maps, graded = compute_graded_electrode_maps(side.dataset, lambda case: grade_of.get(case.identity.case_id),
                                                 diameter_um=diameter_um, min_count=threshold,
                                                 roi_grade=roi_grade, reserved_bytes=reserved)
    incomplete = sorted({r for layer in maps.layers if layer.status != 'available' for r in layer.reasons})
    if any(layer.status != 'available' for layer in maps.layers):
        side.warnings.append('map_coverage_incomplete')
        side.input_record['map_coverage_reasons'] = incomplete
    layer_cases = {}
    for case in side.dataset.cases:
        layer_cases.setdefault(case.analysis_layer, []).append(case)
    held = graded_extra_bytes(maps.layers[0].frame.valid.size, len(maps.layers)) if maps.layers else 0   # 등급 배열이 아직 살아 있다
    regions_done = 0
    for index, (layer, gl) in enumerate(zip(maps.layers, graded.layers)):
        entry = _write_grade_maps(out, name, layer, gl, track, args)
        if entry is not None:
            plan['grade_maps'].append(entry)
        if layer.counts is None:
            continue
        cases = layer_cases.get(layer.key.layer, [])

        def guard(runs, _cases=cases, _index=index):
            guard_stage_map_counting('candidates-dv', side.dataset, regions_done=regions_done, runs=runs, layers_done=_index + 1,
                                     layer_cases=len(_cases), layer_electrodes=len({e.electrode_id for c in _cases for e in c.endpoints}),
                                     reserved=reserved + held)
        rows, unweighted = _layer_attributes(layer, gl, roi_grade, threshold, diameter_um, guard)
        regions_done += len(rows) + len(unweighted)
        base = len(plan['graded_rows'])
        for item in unweighted:
            item['contains'] = [base + position for position in item['contains']]
        plan['graded_rows'].extend(rows)
        plan['unweighted'].extend(unweighted)
    graded = None   # 등급 배열은 여기까지만 쓴다

    rows = plan['graded_rows']
    for row in rows:
        region = row['region']
        plan['by_id'][region.region_id] = row
        frame = region.frame
        key = region.key
        plan['infos'].append(candidates.RegionInfo(
            region.region_id, (key.product_id, key.design_version, frame.frame_id, key.source_id, key.process_step_id, key.exposure_id,
                               key.source_contract_version, key.unit_id, key.layer),
            (float(region.center_xy_um[0]), float(region.center_xy_um[1])), int(region.max_count), float(region.area_um2), key.layer,
            tuple(float(v) for v in frame.bbox_um), score=_rank_score(row)))
    merged = candidates.merge_regions(plan['infos'], window_um, args['merge_overlap'], rank_key=lambda info: info.score)
    cut = merged[args['max_candidates']:] if args['max_candidates'] is not None else []
    kept = merged[:args['max_candidates']] if args['max_candidates'] is not None else merged
    unweighted_order = sorted(range(len(merged)), key=lambda i: (-merged[i]['representative'].max_count,
                                                                 -merged[i]['representative'].area_um2, merged[i]['representative'].region_id))
    rank_unweighted = {merged[i]['representative'].region_id: position + 1 for position, i in enumerate(unweighted_order)}
    plan.update(merged=merged, kept=kept, cut=cut, rank_unweighted=rank_unweighted)
    by_region = {row['region'].region_id: row['region'] for row in rows}

    # 질의마다: 창 포함 비율, 합성 영역
    synthetic, ids = {}, []
    kept_ids = {id(merge) for merge in kept}
    for position, merge in enumerate(merged):
        representative = merge['representative']
        ranked = sorted(merge['members'], key=lambda info: info.score)
        members = [by_region[info.region_id] for info in ranked]
        merge['rank_graded'] = position + 1
        merge['ranked_members'] = [info.region_id for info in ranked]
        merge['kept'] = id(merge) in kept_ids
        if not merge['kept']:
            continue
        union = _union_regions(members)
        frame = union.frame
        total, inside_all = _window_cells(frame, union.mask, representative.center_xy_um, window_um)
        own_total, own_inside = _window_cells(frame, by_region[representative.region_id].mask, representative.center_xy_um, window_um)
        merge['window'] = {'sel_cells_total': total, 'sel_cells_in_window': inside_all,
                           'sel_cells_in_window_ratio': (inside_all / total) if total else None,
                           'rep_cells_outside': own_total - own_inside, 'member_cells_outside': (total - inside_all) - (own_total - own_inside),
                           'centre_invalid': _centre_invalid(frame, representative.center_xy_um)}
        synthetic[representative.region_id] = union
        ids.append(representative.region_id)

    # 제품·영역 표(표의 원자료는 병합 전 영역의 CaseLink)
    by_case = {row['case_name']: row for row in dv['by_case']}
    for row in rows:
        plan['pairs_by_region'][row['region'].region_id] = _pairs_of(row['region'], by_case)

    layers = []
    for layer in maps.layers:
        layers.append(replace(layer, regions=tuple(synthetic[r.region_id] for r in layer.regions if r.region_id in synthetic)))
    side.maps = ElectrodeMaps(tuple(layers), maps.summaries, maps.representative_diameter_um, maps.provenance)
    side.regions = plan['infos']
    maps = layers = None
    candidates._export_side(cfg, side, ids, out / f'{name}_export', threshold, diameter_um, track, memo, reserved)
    if side.status != 'unavailable':
        candidates._export_index(cfg, side, out / f'{name}_index_export', threshold, diameter_um, track, memo, reserved)
    side.maps = None
    return plan


def _pairs_of(region, by_case):
    """영역이 덮는 쌍: pair_key 마다 case 와 원본 CA·영역 안 CA. 등급 없는 case 는 따로."""
    pairs, ungraded = {}, []
    for link in region.cases:
        case_id = link.identity['case_id']
        item = {'case_name': case_id, 'failure_type': link.failure_type,
                'original_ca_um2': link.original_case_ca.ca_um2, 'original_ca_status': link.original_case_ca.status,
                'ca_in_region_um2': link.case_ca_in_region.ca_um2, 'ca_in_region_status': link.case_ca_in_region.status}
        row = by_case.get(case_id)
        if row is None:
            ungraded.append(item)
            continue
        pair = pairs.setdefault(row['pair_key'], {'pair_key': row['pair_key'], 'cases': []})
        pair['cases'].append(item)
    return {'pairs': [pairs[key] for key in sorted(pairs)], 'ungraded_cases': sorted(ungraded, key=lambda i: i['case_name'])}


def _run(cfg, out, track, sides, dv, links, notes, args, diameter_um):
    from simcompare.adapters.real_hotspot import MAP_SLIM_ROW_BYTES, MAP_REGION_INFO_BYTES, dataset_held_bytes
    from simcompare.roi_export import AssetMemo
    window_um, threshold = args['window_um'], args['min_count']
    memo = AssetMemo()
    plans = {}
    # 둘 다 먼저 읽었으므로 첫 제품을 계산하는 동안 둘째 제품의 데이터셋도 들고 있다
    reserved = dataset_held_bytes(sides['target'].dataset)
    for name in ('reference', 'target'):
        side = sides[name]
        grade_of, ungraded = links[name]
        plans[name] = _plan_side(cfg, out, name, side, dv[name], grade_of, ungraded, args, diameter_um, reserved, track, memo)
        plan = plans[name]
        reserved += MAP_SLIM_ROW_BYTES * (len(side.rows) + (len(side.index['rows']) if side.index else 0))
        reserved += MAP_REGION_INFO_BYTES * (len(plan['graded_rows']) + len(plan['unweighted']))
        if name == 'reference':
            reserved += dataset_held_bytes(side.dataset) - dataset_held_bytes(sides['target'].dataset)
    memo = None

    warnings = []
    directions, funnels = [], []
    for direction, query_name, index_name in candidates.DIRECTIONS:
        side, plan = sides[query_name], plans[query_name]
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
            row = plan['by_id'][rep.region_id]
            rows = rows_by_region.get(rep.region_id, [])
            ready = [r for r in rows if r['status'] == 'ready']
            reasons = set(excluded.get(rep.region_id, ()))
            for item in rows:
                reasons.update(item['reasons'])
            if side.status in ('blocked', 'unavailable'):
                reasons.update(side.reasons)
            if not rows and not reasons:
                reasons.add('no_roi_rows')
            flags = []
            if candidates.window_clipped(rep, window_um):
                flags.append('window_clipped')
            elif any(candidates.window_clipped(m, window_um) for m in members):
                flags.append('merged_member_window_clipped')
            if len(rows) > len(ready) and ready:
                flags.append('roi_rows_partially_blocked')
            window = merge['window']
            if window['sel_cells_total'] and window['sel_cells_in_window'] < window['sel_cells_total']:
                flags.append('selected_cells_outside_window')
            if window['member_cells_outside'] > 0:
                flags.append('merged_member_cells_outside_window')
            if window['centre_invalid']:
                flags.append('query_centre_invalid')
            queries.append({
                'query_id': candidates._query_id(direction, rep.region_id), 'product_id': rep.group[0],
                'design_version': rep.group[1], 'frame_id': rep.group[2],
                'center_xy_um': list(rep.center_xy_um), 'window_um': window_um, 'region_id': rep.region_id,
                'merged_from': [m.region_id for m in members], 'max_count': rep.max_count,
                'area_um2': rep.area_um2, 'layers': sorted({m.layer for m in members}), 'flags': flags,
                'status': 'ready' if ready else 'blocked', 'reasons': sorted(reasons),
                'roi_ids': sorted(r['roi_id'] for r in rows), 'n_rois': len(rows), 'n_rois_ready': len(ready),
                'map_key': _key_fields(row['region'].key), 'grade_max': GRADE_NAME[row['grade_max_code']],
                'grade_max_code': row['grade_max_code'], 'sel_max_count': row['sel_max_count'],
                'high_max_count': row['high_max_count'], 'visible_max_count': row['visible_max_count'],
                'rank_graded': merge['rank_graded'], 'rank_unweighted': plan['rank_unweighted'][rep.region_id],
                'score_rule_version': SCORE_RULE_VERSION,
                'sel_cells_total': window['sel_cells_total'], 'sel_cells_in_window': window['sel_cells_in_window'],
                'sel_cells_in_window_ratio': window['sel_cells_in_window_ratio']})
        funnels.append(_funnel(direction, plan, queries, args['max_candidates']))
        directions.append({'direction': direction, 'query_product': query_name, 'index_product': index_name,
                           'query_export': f'{query_name}_export/review_rois.json' if sides[query_name].export_path else None,
                           'index_export': (f'{index_name}_index_export/review_rois.json'
                                            if sides[index_name].index and sides[index_name].index['path'] else None),
                           'queries': queries})
    for name, side in sides.items():
        for code in side.warnings:
            warnings.append({'code': code, 'product': name})
        if side.role_blocked or side.role_blocked_index:
            warnings.append({'code': candidates.ROLE_REQUIRED_REASON, 'product': name, 'detail': candidates.ROLE_REQUIRED_MESSAGE,
                             'query_rows_blocked': side.role_blocked, 'index_rows_blocked': side.role_blocked_index})
        if side.status in ('blocked', 'unavailable'):
            warnings.append({'code': 'side_' + side.status, 'product': name, 'reasons': side.reasons})
        ungraded = links[name][1]
        if ungraded:
            warnings.append({'code': 'dv_ungraded_cases', 'product': name, 'count': len(ungraded), 'cases': ungraded[:UNGRADED_SAMPLE],
                             'detail': 'dv-grade 에 없는 case 입니다. count_all 에는 들어가지만 등급 지도에는 들어가지 않습니다(vulnerability_v1.json 에 전체 목록).'})
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

    inputs = {'config_sha256': cfg.get('_config_sha256'),
              'reference': {**sides['reference'].input_record, 'product_ids': sides['reference'].product_ids},
              'target': {**sides['target'].input_record, 'product_ids': sides['target'].product_ids}}
    for name, side in sides.items():
        inputs[name]['dv_grades'] = _dv_record(dv[name])
        if side.export_path is not None:
            inputs[name]['export_sha256'] = candidates._sha256_file(side.export_path)
        if side.index is not None and side.index['path'] is not None:
            inputs[name]['index_export_sha256'] = candidates._sha256_file(side.index['path'])
    commit = next((s.export_payload.get('producer_commit') for s in sides.values() if s.export_payload), None)
    n_cut = sum(len(plans[n]['cut']) for n in plans)
    all_queries = [q for d in directions for q in d['queries']]
    complete = (not warnings and all(q['status'] == 'ready' and q['n_rois'] == q['n_rois_ready'] for q in all_queries)
                and all(s.status == 'available' for s in sides.values()))
    funnel_doc = {'schema_version': 'candidate_funnel_v1', 'directions': funnels, 'cut_by_max_candidates': n_cut,
                  'max_candidates': args['max_candidates'], 'max_candidates_scope': 'per_direction', 'index_coverage': coverage}
    match_inputs = {direction: {'query': f'{query_name}_export', 'index': f'{index_name}_index_export'}
                    for direction, query_name, index_name in candidates.DIRECTIONS}
    roles = set()
    for side in sides.values():
        dataset = getattr(side, 'dataset', None)
        if dataset is None:
            continue
        roles.update((dataset.provenance or {}).get('electrode_roles_effective', {}).values())
        roles.update(ep.role for case in dataset.cases for ep in case.endpoints if ep.role)
    dl2_model_roles = sorted(roles)
    payload = {'schema_version': candidates.SCHEMA_VERSION, 'roi_source': 'candidates_dv_v1', 'producer_commit': commit,
               'args': args, 'inputs': inputs, 'directions': directions, 'funnel': funnel_doc, 'warnings': warnings, 'notes': notes,
               'match_inputs': match_inputs, 'dl2_model_roles': dl2_model_roles,
               'match_files': {d: {k: f'{v}/review_rois.json' for k, v in item.items()} for d, item in match_inputs.items()},
               'status': 'available' if complete else 'partial',
               'policy': {'merge': 'window_overlap_intersection_over_min_area_union_find_region_id_order_within_map_key',
                          'window': 'square_centered_at_region_center_unclipped_for_overlap',
                          'representative': SCORE_RULE_VERSION,
                          'rank_key_graded': 'sel_max_count_desc_grade_max_desc_all_max_in_mask_desc_area_desc_region_id_asc',
                          'rank_key_unweighted': 'all_max_in_mask_desc_area_desc_region_id_asc',
                          'regions': 'connected_components_of_selected_map_ge_min_count_per_map_key',
                          'selected_map': 'count_high' if args['roi_grade'] == 'high' else 'count_visible',
                          'mutual_one_way': 'not_computed_here',
                          'index_export': 'one_manual_roi_per_dataset_case_index_only_not_queries_no_region_provenance'}}
    vulnerability = _vulnerability(sides, plans, dv, links, args, notes)
    outputs = []

    def emit(file_name, text):
        candidates.write_text(out / file_name, text, track)
        outputs.append(file_name)

    emit('candidate_queries_v1.json', _json(payload))
    emit('vulnerability_v1.json', _json(vulnerability))
    emit('funnel.json', _json(funnel_doc))
    candidates.write_funnel_csv(out / 'funnel.csv', funnels, coverage, track)
    outputs.append('funnel.csv')
    for name, plan in plans.items():
        for entry in plan['grade_maps']:
            for kind in ('count_high', 'count_visible', 'grade_max'):
                outputs.extend([entry[kind]['png'], entry[kind]['sidecar']])
    for name, side in sides.items():
        if side.export_path is not None:
            for item in sorted(Path(p).name for p in side.exported.get('outputs', [])):
                outputs.append(f'{name}_export/{item}')
        if side.index is not None and side.index['path'] is not None:
            for item in sorted(Path(p).name for p in side.index['exported'].get('outputs', [])):
                outputs.append(f'{name}_index_export/{item}')
    result = {'status': payload['status'], 'reasons': sorted({w['code'] for w in warnings}), 'outputs': sorted(outputs),
              'exit_code': 0 if complete else 3, 'n_queries': {d['direction']: len(d['queries']) for d in directions},
              'funnel': {f['direction']: {s['stage']: s['count'] for s in f['stages']} for f in funnels},
              'index_coverage': {n: {k: c[k] for k in ('dataset_cases', 'index_rois_ready', 'index_rois_blocked',
                                                      'electrodes_covered', 'electrodes_total')} for n, c in coverage.items()},
              'match_inputs': match_inputs, 'match_files': payload['match_files'], 'dl2_model_roles': dl2_model_roles}
    candidates.write_text(out / 'execution.json', _json({'command': 'candidates-dv', **result}), track)
    result['outputs'] = sorted(result['outputs'] + ['execution.json'])
    return result


def _funnel(direction, plan, queries, max_candidates):
    """candidates.build_funnel 의 단계에 등급 단계를 더한다: unweighted_regions → graded_regions → merged_queries → …"""
    n_graded, n_unweighted = len(plan['graded_rows']), len(plan['unweighted'])
    below = sum(1 for item in plan['unweighted'] if 'below_graded_threshold' in item['flags'])
    funnel = candidates.build_funnel(direction, n_graded, len(plan['merged']), len(plan['cut']), queries, max_candidates)
    stages = funnel['stages']
    stages[0] = {'stage': 'graded_regions', 'count': n_graded, 'dropped': below,
                 'reasons': {'below_graded_threshold': below} if below else {}}
    stages.insert(0, {'stage': 'unweighted_regions', 'count': n_unweighted, 'dropped': 0, 'reasons': {}})
    funnel['unweighted_regions'] = n_unweighted
    funnel['graded_regions'] = n_graded
    funnel['below_graded_threshold'] = below
    funnel['note'] += '. graded_regions 의 dropped 는 선정 지도가 K 미만이라 영역이 없는 count_all 영역 수(below_graded_threshold)이며 count 는 선정 지도의 영역 수다'
    return funnel


def _vulnerability(sides, plans, dv, links, args, notes):
    products = {}
    for name, side in sides.items():
        plan, (grade_of, ungraded) = plans[name], links[name]
        merged_into = {}
        rank = {}
        for merge in plan['merged']:
            rep = merge['representative'].region_id
            for member in merge['members']:
                merged_into[member.region_id] = rep
            rank[rep] = (merge['rank_graded'], plan['rank_unweighted'][rep], merge['kept'])
        exported = {merge['representative'].region_id for merge in plan['kept']}
        regions = []
        for row in plan['graded_rows']:
            region = row['region']
            rep = merged_into.get(region.region_id, region.region_id)
            pairs = plan['pairs_by_region'][region.region_id]
            grade_count = {'None': 0, 'Low': 0, 'High': 0, 'ungraded': len(pairs['ungraded_cases'])}
            by_pair = {item['pair_key']: item for item in dv[name]['pairs']}
            for pair in pairs['pairs']:
                grade_count[by_pair[pair['pair_key']]['grade']] += 1
            regions.append({
                'region_id': region.region_id, 'map_key': _key_fields(region.key), 'center_xy_um': list(map(float, region.center_xy_um)),
                'region_bbox_um': list(map(float, region.region_bbox_um)), 'area_um2': float(region.area_um2),
                'max_count': int(region.max_count), 'sel_max_count': row['sel_max_count'], 'high_max_count': row['high_max_count'],
                'visible_max_count': row['visible_max_count'], 'grade_max': GRADE_NAME[row['grade_max_code']],
                'grade_max_code': row['grade_max_code'], 'sel_area_um2': float(region.area_um2),
                'sel_centre_xy_um': list(map(float, region.center_xy_um)), 'n_pairs_by_grade': grade_count,
                'merged_into': rep, 'is_representative': rep == region.region_id,
                'rank_graded': rank[rep][0] if rep == region.region_id else None,
                'rank_unweighted': rank[rep][1] if rep == region.region_id else None,
                'exported': region.region_id in exported, 'electrodes': list(region.electrodes),
                'pairs': pairs['pairs'], 'ungraded_cases': pairs['ungraded_cases']})
        unweighted = []
        order = sorted(range(len(plan['unweighted'])), key=lambda i: (-plan['unweighted'][i]['max_count'], -plan['unweighted'][i]['area_um2'],
                                                                      plan['unweighted'][i]['region_id']))
        count_all_rank = {index: position + 1 for position, index in enumerate(order)}
        for index, item in enumerate(plan['unweighted']):
            unweighted.append({
                'region_id': item['region_id'], 'map_key': _key_fields(item['key']), 'center_xy_um': item['center_xy_um'],
                'region_bbox_um': item['bbox_um'], 'area_um2': item['area_um2'], 'max_count': item['max_count'],
                'rank_count_all': count_all_rank[index], 'sel_max_count': item['sel_max_count'], 'high_max_count': item['high_max_count'],
                'visible_max_count': item['visible_max_count'], 'grade_max': GRADE_NAME[item['grade_max_code']],
                'grade_max_code': item['grade_max_code'], 'sel_area_um2': item['sel_area_um2'], 'sel_centre_xy_um': item['sel_centre_xy_um'],
                'flags': item['flags'], 'graded_region_ids': [plan['graded_rows'][i]['region'].region_id for i in item['contains']]})
        by_case = {row['case_name']: row for row in dv[name]['by_case']}
        products[name] = {
            'product_id': dv[name]['manifest']['product_id'], 'dv_provenance': _dv_record(dv[name]),
            'regions': regions, 'unweighted_regions': unweighted, 'grade_maps': plan['grade_maps'],
            'pairs': [dict(row) for row in dv[name]['pairs']],
            'cases': [{**dict(row), 'in_dataset': True} for row in dv[name]['by_case']],
            'ungraded_cases': ungraded, 'n_dataset_cases': len(side.dataset.cases),
            'n_graded_cases': len(grade_of), 'case_count_by_grade': {g: sum(1 for r in by_case.values() if r['grade'] == g) for g in GRADE_CODE}}
    return {'schema_version': VULNERABILITY_SCHEMA, 'score_rule_version': SCORE_RULE_VERSION, 'args': args, 'products': products,
            'notes': notes}
