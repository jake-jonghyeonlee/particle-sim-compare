"""PLAN_v4 T3: 사용자가 확정한 후보 쌍만 roi_correspondence_v1 로 바꾼다.

입력은 candidates 출력 폴더, DL2 baseline_ncc(all_ready) 결과 JSON(방향별), 사용자의 decisions.csv 이다.
decisions.csv 에 accept 로 적힌 후보만 쌍이 되고, 나머지(reject·미결정)는 어떤 출력의 쌍에도 들어가지 않는다.
점수·순위·플래그·mutual/one_way 는 사이드카에만 적으며 어떤 판정·정렬·병합에도 쓰지 않는다(S4).
roi_correspondence_v1 schema 와 validate_correspondence 의미는 바꾸지 않는다. 입력·정합성 문제는 모두 모아
NumericContractError 로 알리고 아무것도 쓰지 않는다.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re

from .numeric_schema import NumericContractError

DIRECTIONS = (('reference_to_target', 'reference', 'target'), ('target_to_reference', 'target', 'reference'))
DIRECTION_NAMES = tuple(d[0] for d in DIRECTIONS)
NCC_SCHEMA = 'psdl2_baseline_ncc_v1'
BATCH_SCHEMA = 'psdl2_search_batch_v1'
SEARCH_SCOPES = ('primary', 'supplementary')
SIDECAR_SCHEMA = 'confirmed_pairs_sidecar_v1'
FUNNEL_SCHEMA = 'confirm_funnel_v1'
DECISION_COLUMNS = ('candidate_id', 'decision', 'note')
DECISIONS = ('accept', 'reject')
ID_PATTERN = re.compile(r'^(reference_to_target|target_to_reference):(.+):r([1-9][0-9]*)$')
CENTER_EPS = 1e-9
NOTE = ('사용자가 확정한 쌍만 담는다. NCC/DL2 출처·점수·순위·mutual/one_way 는 confirmed_pairs_sidecar.json 에만 있다')


def utc_now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def candidate_id(direction, query_roi_id, rank):
    return f'{direction}:{query_roi_id}:r{rank}'


def pair_id_for(direction, query_roi_id, rank, centre):
    """방향·질의 roi_id·순위·후보 중심의 해시. 시각·점수·사용자 ID 는 넣지 않는다."""
    raw = '|'.join([direction, query_roi_id, str(rank), repr(float(centre[0])), repr(float(centre[1]))])
    return 'confirmed_' + hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


class _Problems:
    def __init__(self):
        self.items = []

    def add(self, code, detail=''):
        self.items.append(f'{code}: {detail}' if detail else code)

    def raise_if_any(self):
        if self.items:
            raise NumericContractError('confirm_pairs_input_problems',
                                       f'{len(self.items)}건\n' + '\n'.join('  - ' + item for item in self.items))


def _read_json_bytes(path, problems, label):
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        problems.add(f'{label}_unreadable', f'{path}: {exc}')
        return None, None
    digest = _sha256_bytes(data)
    try:
        text = data.decode('utf-8')
        del data   # the raw bytes are not needed while the parser builds the objects
        return json.loads(text), digest
    except ValueError as exc:
        problems.add(f'{label}_not_json', f'{path}: {exc}')
        return None, None


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _centre(value):
    if (isinstance(value, (list, tuple)) and len(value) == 2 and all(_is_number(v) for v in value)):
        return [float(value[0]), float(value[1])]
    return None


# ---- inputs -------------------------------------------------------------------------------

def _export_sizes(directory, payload):
    """On-disk bytes of the review_rois.json files candidates_payload names (files that cannot be statted are skipped:
    reading them reports the problem)."""
    sizes = {}
    for entry in payload.get('directions', []):
        if not isinstance(entry, dict):
            continue
        for field in ('query_export', 'index_export'):
            relative = entry.get(field)
            if isinstance(relative, str) and relative not in sizes:
                try:
                    sizes[relative] = (directory/relative).stat().st_size
                except OSError:
                    pass
    return sizes


def _match_sizes(matches):
    """On-disk bytes of the DL2 match JSONs given with --match (unreadable ones are skipped: reading reports them)."""
    sizes = []
    for path in (matches or {}).values():
        try:
            sizes.append(Path(path).stat().st_size)
        except (OSError, TypeError, ValueError):
            pass
    return sizes


def _load_candidates(candidates_dir, cfg, problems, datasets=None, matches=None):
    directory = Path(candidates_dir)
    payload, payload_sha = _read_json_bytes(directory/'candidate_queries_v1.json', problems, 'candidates_payload')
    if payload is None:
        return None
    if not isinstance(payload, dict) or payload.get('schema_version') != 'candidate_queries_v1':
        problems.add('unsupported_candidates_schema', str(directory/'candidate_queries_v1.json'))
        return None
    recorded = (payload.get('inputs') or {}).get('config_sha256')
    if recorded is not None and cfg.get('_config_sha256') is not None and recorded != cfg['_config_sha256']:
        problems.add('config_changed_since_candidates', f'{recorded} != {cfg["_config_sha256"]}')
    info = {'dir': directory, 'payload': payload, 'payload_sha256': payload_sha, 'directions': {}}
    if datasets:   # real datasets carry the operation limit: refuse before any export is read or parsed
        from .adapters.real_hotspot import guard_stage_confirm_read
        guard_stage_confirm_read('confirm-pairs', [item['dataset'] for item in datasets.values()],
                                 list(_export_sizes(directory, payload).values()) + _match_sizes(matches))
    match_inputs = payload.get('match_inputs') or {}
    if 'match_files' in payload:
        files = payload['match_files']
        for name, item in (match_inputs.items() if isinstance(match_inputs, dict) else []):
            got = files.get(name) if isinstance(files, dict) else None
            want = {key: f'{folder}/review_rois.json' for key, folder in item.items()} if isinstance(item, dict) else None
            if got != want:
                problems.add('candidates_match_files_inconsistent', f'{name}: match_files={got!r} match_inputs={item!r}')
        if not isinstance(files, dict) or set(files) != set(match_inputs if isinstance(match_inputs, dict) else ()):
            problems.add('candidates_match_files_inconsistent', f'directions {sorted(files) if isinstance(files, dict) else files!r}')
    for entry in payload.get('directions', []):
        name = entry.get('direction')
        if name not in DIRECTION_NAMES:
            continue
        expected = match_inputs.get(name) or {}
        paths = {}
        for key, field in (('query', 'query_export'), ('index', 'index_export')):
            relative = entry.get(field)
            if relative is None or relative != f'{expected.get(key)}/review_rois.json':
                problems.add('candidates_match_inputs_inconsistent', f'{name}.{field}={relative!r} match_inputs={expected!r}')
                paths[key] = None
                continue
            path = directory/relative
            export, sha = _read_json_bytes(path, problems, f'{name}_{key}_export')
            side_name = next((q if key == 'query' else i) for d, q, i in DIRECTIONS if d == name)
            recorded_sha = ((payload.get('inputs') or {}).get(side_name) or {}).get('export_sha256' if key == 'query' else 'index_export_sha256')
            if sha is not None and sha != recorded_sha:
                problems.add('confirm_export_changed_since_candidates',
                             f'{name} {key} export {relative}: on disk {sha} != candidates record {recorded_sha!r}')
            rows = _rows_by_id(export, problems, f'{name} {key} export') if export is not None else {}
            if isinstance(export, dict):
                export.pop('region_coverages', None)   # the shared identity lists (one per layer) are not read by any step here
            paths[key] = {'path': str(path), 'sha256': sha, 'export': export, 'rows': rows, 'dataset_name': side_name}
        info['directions'][name] = {'query_product': entry.get('query_product'), 'index_product': entry.get('index_product'), **paths}
    for name in DIRECTION_NAMES:
        if name not in info['directions']:
            problems.add('candidates_direction_missing', name)
    return info


def _rows_by_id(export, problems, label):
    rows = {}
    if not isinstance(export, dict) or not isinstance(export.get('rois'), list):
        problems.add('unsupported_review_roi_export', label)
        return rows
    for row in export['rois']:
        if isinstance(row, dict) and isinstance(row.get('roi_id'), str):
            if row['roi_id'] in rows:
                problems.add('duplicate_roi_id_in_export', f'{label}: {row["roi_id"]}')
                continue
            rows[row['roi_id']] = row
    return rows


def _load_match(direction, path, cands, datasets, problems, models=None):
    """한 방향의 DL2 결과를 검사하고 후보 표를 만든다. 반환: dict 또는 None."""
    query_name, index_name = next((q, i) for d, q, i in DIRECTIONS if d == direction)
    result, sha = _read_json_bytes(path, problems, f'match_{direction}')
    if result is None:
        return None
    label = f'{direction}({path})'
    if isinstance(result, dict) and result.get('schema_version') == BATCH_SCHEMA:
        return _load_batch_match(direction, path, result, sha, cands, datasets, problems, models)
    if not isinstance(result, dict) or result.get('schema_version') != NCC_SCHEMA:
        problems.add('match_schema_version_unsupported', f'{label}: {result.get("schema_version") if isinstance(result, dict) else None!r}')
        return None
    if result.get('mode') != 'all_ready' or not isinstance(result.get('results'), list):
        problems.add('match_not_all_ready_batch_output', label)
        return None
    if (result.get('arguments') or {}).get('self_check') is True:
        problems.add('match_is_a_self_check_run', label)
    check = result.get('self_check')
    if check is not None and (not isinstance(check, dict) or check.get('passed') is not True):
        problems.add('dl2_self_check_not_passed', label)
    side = cands['directions'][direction]
    inputs = result.get('inputs') if isinstance(result.get('inputs'), dict) else {}
    for key, field in (('query', 'query_export_sha256'), ('index', 'index_export_sha256')):
        recorded, expected = inputs.get(field), (side[key] or {}).get('sha256')
        if recorded is None:
            problems.add('match_input_hash_missing', f'{label}: inputs.{field}')
        elif expected is not None and recorded != expected:
            problems.add('match_input_hash_mismatch', f'{label}: inputs.{field} does not equal the candidates {key} export for {direction}')
    query_product = datasets[query_name]['product']
    index_product = datasets[index_name]['product']
    if result.get('candidate_product_id') != index_product:
        problems.add('match_candidate_product_mismatch', f'{label}: {result.get("candidate_product_id")!r} != {index_product!r}')
    query_rows = (side['query'] or {}).get('rows', {})
    index_rows = (side['index'] or {}).get('rows', {})
    table, unusable, seen_queries = {}, {}, set()
    for position, row in enumerate(result['results']):
        where = f'{label} results[{position}]'
        query = row.get('query') if isinstance(row, dict) else None
        roi_id = query.get('roi_id') if isinstance(query, dict) else None
        if not isinstance(roi_id, str) or not roi_id:
            problems.add('match_result_without_query_roi_id', where)
            continue
        if roi_id in seen_queries:
            problems.add('match_duplicate_query_roi_id', f'{where}: {roi_id}')
            continue
        seen_queries.add(roi_id)
        qrow = query_rows.get(roi_id)
        if qrow is None or qrow.get('status') != 'ready':
            problems.add('query_roi_not_ready_in_export', f'{where}: {roi_id}')
            continue
        centre = _centre(query.get('center_xy_um'))
        export_centre = _centre(qrow.get('center_xy_um'))
        if (centre is None or export_centre is None or query.get('product_id') != query_product
                or any(abs(a-b) > CENTER_EPS for a, b in zip(centre, export_centre))):
            problems.add('match_query_does_not_match_export', f'{where}: {roi_id}')
            continue
        if row.get('status') != 'available' or not row.get('primary'):
            unusable[roi_id] = sorted(str(r) for r in row.get('reasons', [])) or ['no_candidates']
            continue
        for entry in row['primary']:
            rank = entry.get('rank') if isinstance(entry, dict) else None
            cand_centre = _centre(entry.get('center_xy_um')) if isinstance(entry, dict) else None
            if type(rank) is not int or rank < 1 or cand_centre is None or not isinstance(entry.get('frame_id'), str):
                problems.add('match_candidate_malformed', f'{where}: rank={rank!r}')
                continue
            if entry.get('product_id') != index_product:
                problems.add('match_candidate_product_mismatch', f'{where} r{rank}: {entry.get("product_id")!r} != {index_product!r}')
                continue
            cid = candidate_id(direction, roi_id, rank)
            if cid in table:
                problems.add('match_duplicate_candidate_id', cid)
                continue
            flags = row.get('flags') if isinstance(row.get('flags'), dict) else {}
            table[cid] = {'candidate_id': cid, 'direction': direction, 'query_roi_id': roi_id, 'rank': rank,
                          'score': entry.get('score'), 'center_xy_um': cand_centre, 'frame_id': entry['frame_id'],
                          'product_id': entry['product_id'], 'query_center_xy_um': centre,
                          'query_frame_id': qrow.get('frame_id'), 'flags': flags, 'score_gap': flags.get('score_gap'),
                          'shared_with': list(row.get('shared_with') or []), 'template_sha256': row.get('template_sha256'),
                          'reasons': list(row.get('reasons') or []), 'query_row': qrow}
    return {'direction': direction, 'path': str(path), 'sha256': sha, 'table': table, 'unusable': unusable,
            'n_queries': len(result['results']), 'index_rows': index_rows,
            'implementation_revision': result.get('implementation_revision'), 'window_um': result.get('window_um'),
            'topk': result.get('topk'), 'self_check': check,
            'inputs': {'query_export_sha256': inputs.get('query_export_sha256'), 'index_export_sha256': inputs.get('index_export_sha256')}}


def _parse_index_sources(result, label, problems):
    """psdl2_search_batch_v1 의 index_sources 를 검사한다. 반환: 'single_export' 또는 source 목록(형식 오류면 빈 목록)."""
    sources = result.get('index_sources')
    if sources == 'single_export':
        return 'single_export'
    if not isinstance(sources, list) or not sources:
        problems.add('match_index_sources_invalid', f'{label}: index_sources must be "single_export" or a non-empty list')
        return []
    parsed = []
    for position, source in enumerate(sources):
        product_ids = source.get('product_ids', 0) if isinstance(source, dict) else 0
        if (not isinstance(source, dict) or not isinstance(source.get('export_sha256'), str)
                or not isinstance(source.get('patch_set_sha256s'), list)
                or not (product_ids is None or (isinstance(product_ids, list) and all(isinstance(p, str) for p in product_ids)))):
            problems.add('match_index_sources_invalid', f'{label}: index_sources[{position}]')
            continue
        parsed.append({'export_sha256': source['export_sha256'], 'patch_set_sha256s': list(source['patch_set_sha256s']),
                       'product_ids': None if product_ids is None else list(product_ids)})
    return parsed


def _index_grade(parsed, product, index_sha):
    """index 제품의 검증 등급과 그 제품을 담은 source 들. 등급 None 이면 제품을 담은 source 가 없다(후보 unusable).

    해시 대조는 등급으로만 남긴다: DL2 문서 흐름의 DB 는 제품별 template export 이므로 candidates 의 *_index_export 와
    해시가 같지 않아도 정상이다."""
    if parsed == 'single_export':
        return 'unverifiable_single_export', []
    used = [s for s in parsed if s['product_ids'] is not None and product in s['product_ids']]
    if not used:
        return None, []
    same = any(s['export_sha256'] == index_sha for s in used)
    return ('candidates_index_export' if same else 'product_ids_only'), used


def _batch_candidate_problem(entry, direction_products, grade, model=None):
    """후보 하나가 쓸 수 없는 사유 코드(쓸 수 있으면 None).

    model: models.yaml 에 있는 후보 제품이면 {'direction', 'grade'}. 모델 후보는 target 질의(target_to_reference)에서만 쓴다."""
    query_product, index_product = direction_products
    if entry['search_scope'] not in SEARCH_SCOPES:
        return 'unsupported_search_scope'
    if entry['product_id'] == query_product:
        return 'candidate_same_product'
    if model is not None:
        if model['direction'] != 'target_to_reference':
            return 'candidate_model_query_not_target'
        return 'candidate_product_unverifiable' if model['grade'] is None else None
    if entry['product_id'] != index_product:
        return 'candidate_product_not_configured'
    if grade is None:
        return 'candidate_product_unverifiable'
    return None


def _load_batch_match(direction, path, result, sha, cands, datasets, problems, models=None):
    """psdl2_search_batch_v1 한 방향. 반환 형태는 NCC 경로(_load_match)와 같고 unusable_candidates 가 더해진다.

    models(load_models 결과)가 있으면 모델 제품 후보는 table/unusable_candidates 가 아니라 model_table/model_unusable 에 담는다
    (reference↔target 의 깔때기·목록 항목이 모델 후보로 달라지지 않게 한다)."""
    query_name, index_name = next((q, i) for d, q, i in DIRECTIONS if d == direction)
    label = f'{direction}({path})'
    if not isinstance(result.get('rankings'), list):
        problems.add('match_rankings_required', label)
        return None
    side = cands['directions'][direction]
    query_sha, index_sha = (side['query'] or {}).get('sha256'), (side['index'] or {}).get('sha256')
    inputs = result.get('inputs') if isinstance(result.get('inputs'), dict) else {}
    parsed_sources = _parse_index_sources(result, label, problems)
    products = (datasets[query_name]['product'], datasets[index_name]['product'])
    grade, used_sources = _index_grade(parsed_sources, products[1], index_sha)
    other_side = cands['directions'][next(d for d in DIRECTION_NAMES if d != direction)]
    other_query_sha = (other_side['query'] or {}).get('sha256')
    query_rows = (side['query'] or {}).get('rows', {})
    index_rows = (side['index'] or {}).get('rows', {})
    table, unusable, unusable_candidates, seen_queries = {}, {}, {}, set()
    model_table, model_unusable, model_grades = {}, {}, {}
    model_entries = models['models'] if models else {}
    skipped_other_direction = 0
    for position, ranking in enumerate(result['rankings']):
        where = f'{label} rankings[{position}]'
        roi_id = ranking.get('query_roi_id') if isinstance(ranking, dict) else None
        if not isinstance(roi_id, str) or not roi_id or not isinstance(ranking.get('candidates'), list):
            problems.add('match_result_without_query_roi_id', where)
            continue
        recorded = ranking.get('query_export_sha256')      # 데이터셋 질의에서는 inputs.query_export_sha256 이 null 이다
        if recorded is None:
            recorded = inputs.get('query_export_sha256')
        if recorded is None:
            problems.add('match_input_hash_missing', f'{where}: query_export_sha256')
        elif query_sha is not None and recorded != query_sha:
            if other_query_sha is not None and recorded == other_query_sha:
                skipped_other_direction += 1             # DL2 실행기는 두 방향 질의를 한 봉투에 쓴다: 반대 방향 ranking
                continue
            problems.add('match_input_hash_mismatch', f'{where}: query_export_sha256 does not equal the candidates query export for {direction}')
            continue
        if roi_id in seen_queries:
            problems.add('match_duplicate_query_roi_id', f'{where}: {roi_id}')
            continue
        seen_queries.add(roi_id)
        qrow = query_rows.get(roi_id)
        if qrow is None or qrow.get('status') != 'ready':
            problems.add('query_roi_not_ready_in_export', f'{where}: {roi_id}')
            continue
        centre = _centre(qrow.get('center_xy_um'))
        if centre is None or ranking.get('query_product_id') != products[0]:
            problems.add('match_query_does_not_match_export', f'{where}: {roi_id}')
            continue
        if not ranking['candidates']:
            unusable[roi_id] = ['no_candidates']
            continue
        for entry in ranking['candidates']:
            rank = entry.get('rank') if isinstance(entry, dict) else None
            cand_centre = _centre(entry.get('center_xy_um')) if isinstance(entry, dict) else None
            if (type(rank) is not int or rank < 1 or cand_centre is None or not isinstance(entry.get('frame_id'), str)
                    or not isinstance(entry.get('product_id'), str) or not isinstance(entry.get('roi_id'), str)
                    or not isinstance(entry.get('search_scope'), str)):
                problems.add('match_candidate_malformed', f'{where}: rank={rank!r}')
                continue
            cid = candidate_id(direction, roi_id, rank)
            if cid in table or cid in unusable_candidates or cid in model_table or cid in model_unusable:
                problems.add('match_duplicate_candidate_id', cid)
                continue
            model_entry = model_entries.get(entry['product_id'])
            model_check = None
            if model_entry is not None:
                if entry['product_id'] not in model_grades:
                    model_grades[entry['product_id']] = _index_grade(parsed_sources, entry['product_id'], None)[0]
                model_check = {'direction': direction, 'grade': model_grades[entry['product_id']]}
            reason = _batch_candidate_problem(entry, products, grade, model_check)
            if reason is not None:
                (model_unusable if model_entry is not None else unusable_candidates)[cid] = {
                    'candidate_id': cid, 'direction': direction, 'query_roi_id': roi_id, 'rank': rank,
                    'candidate_product_id': entry['product_id'], 'search_scope': entry['search_scope'], 'reason': reason}
                continue
            target_table = table if model_entry is None else model_table
            target_table[cid] = {'candidate_id': cid, 'direction': direction, 'query_roi_id': roi_id, 'rank': rank,
                          'score': None, 'center_xy_um': cand_centre, 'frame_id': entry['frame_id'],
                          'product_id': entry['product_id'], 'query_center_xy_um': centre,
                          'query_frame_id': qrow.get('frame_id'),
                          'flags': {'supplementary': True} if entry['search_scope'] == 'supplementary' else {},
                          'score_gap': None, 'shared_with': [], 'template_sha256': None, 'reasons': [], 'query_row': qrow,
                          'match_schema': BATCH_SCHEMA, 'search_scope': entry['search_scope'], 'candidate_roi_id': entry['roi_id'],
                          'index_export_verification': grade}
            if model_entry is not None:
                target_table[cid].update(index_export_verification=model_grades[entry['product_id']], is_model=True,
                                         model_status=model_entry['status'])
    arguments = result.get('arguments') if isinstance(result.get('arguments'), dict) else {}
    warnings = [] if seen_queries else ['no_rankings_for_direction']
    extra = {'match_schema': BATCH_SCHEMA, 'checkpoint_fingerprint': result.get('checkpoint_fingerprint'),
             'view_fingerprint': result.get('view_fingerprint'), 'arguments': arguments,
             'index_sources_kind': 'single_export' if parsed_sources == 'single_export' else 'sources',
             'index_sources_used': used_sources, 'index_export_verification': grade or 'no_source_for_index_product',
             'index_export_sha256': index_sha, 'rankings_skipped_other_direction': skipped_other_direction,
             'warnings': warnings, 'index_sha256': inputs.get('index_sha256')}
    return {'direction': direction, 'path': str(path), 'sha256': sha, 'table': table, 'unusable': unusable,
            'unusable_candidates': unusable_candidates, 'n_queries': len(seen_queries), 'index_rows': index_rows,
            'implementation_revision': None, 'window_um': None, 'topk': arguments.get('topk'), 'self_check': None,
            'inputs': {'query_export_sha256': query_sha, 'index_export_sha256': index_sha}, 'match_schema': BATCH_SCHEMA,
            'sidecar_extra': extra, 'model_table': model_table, 'model_unusable': model_unusable}


def _read_decisions(path, problems):
    try:
        data = Path(path).read_bytes()
        text = data.decode('utf-8-sig')
    except (OSError, UnicodeDecodeError) as exc:
        problems.add('decisions_unreadable', f'{path}: {exc}')
        return None
    rows = list(csv.reader(io.StringIO(text, newline='')))
    if not rows:
        problems.add('decisions_header_required', str(path))
        return None
    header = [cell.strip() for cell in rows[0]]
    if sorted(header) != sorted(DECISION_COLUMNS):
        problems.add('decisions_columns_invalid', f'expected exactly {list(DECISION_COLUMNS)}, got {header}')
        return None
    index = {name: header.index(name) for name in DECISION_COLUMNS}
    parsed = []
    for line, cells in enumerate(rows[1:], 2):
        if not any(cell.strip() for cell in cells):
            continue
        if len(cells) != len(header):
            problems.add('decisions_row_malformed', f'line {line}: {len(cells)} cells')
            continue
        parsed.append({'line': line, 'candidate_id': cells[index['candidate_id']].strip(),
                       'decision': cells[index['decision']].strip(), 'note': cells[index['note']]})
    return {'rows': parsed, 'sha256': _sha256_bytes(data)}


def _classify_decisions(decisions, matches, problems):
    """{candidate_id: {'decision', 'note', 'line'}}; 모든 문제를 problems 에 모은다."""
    chosen, first_line = {}, {}
    for row in decisions['rows']:
        cid, where = row['candidate_id'], f'line {row["line"]}'
        if row['decision'] not in DECISIONS:
            problems.add('decision_value_invalid', f'{where}: {cid!r} {row["decision"]!r} (accept 또는 reject)')
            continue
        parsed = ID_PATTERN.match(cid)
        if parsed is None:
            problems.add('unknown_candidate_id', f'{where}: {cid!r}')
            continue
        direction, roi_id = parsed.group(1), parsed.group(2)
        if direction not in matches:
            problems.add('candidate_match_file_not_given', f'{where}: {cid} ({direction})')
            continue
        match = matches[direction]
        if cid not in match['table'] and cid not in match.get('model_table', {}):
            if cid in match.get('unusable_candidates', {}):
                problems.add('candidate_match_unavailable', f'{where}: {cid} ({match["unusable_candidates"][cid]["reason"]})')
            elif cid in match.get('model_unusable', {}):
                problems.add('candidate_match_unavailable', f'{where}: {cid} ({match["model_unusable"][cid]["reason"]})')
            elif roi_id in match['unusable']:
                problems.add('candidate_match_unavailable', f'{where}: {cid} ({", ".join(match["unusable"][roi_id])})')
            else:
                problems.add('unknown_candidate_id', f'{where}: {cid}')
            continue
        if cid in chosen:
            same = chosen[cid]['decision'] == row['decision']
            problems.add('duplicate_candidate_id' if same else 'conflicting_decisions',
                         f'{cid} (line {first_line[cid]} and {row["line"]})')
            continue
        chosen[cid] = {'decision': row['decision'], 'note': row['note'], 'line': row['line']}
        first_line[cid] = row['line']
    return chosen


# ---- pairs ----------------------------------------------------------------------------------

def _side_from_row(row):
    from .correspondence import _side_from_roi_row
    return _side_from_roi_row(row, 'query')


def _candidate_side(item, design_version, analysis_layer):
    return {'product_id': item['product_id'], 'design_version': design_version, 'frame_id': item['frame_id'],
            'center_xy_um': list(item['center_xy_um']), 'analysis_layer': analysis_layer, 'endpoints': None,
            'failure_type': None, 'roi_id': None}


def _build_pairs(accepted, matches, datasets, labelled_by, labelled_at, problems):
    from .correspondence import bind_pairs
    pairs = []
    reference, target = datasets['reference']['dataset'], datasets['target']['dataset']
    for item in accepted:
        direction = item['direction']
        index_rows = matches[direction]['index_rows']
        designs = sorted({r['source_identity']['design_version'] for r in index_rows.values()
                          if isinstance(r.get('source_identity'), dict) and r['source_identity'].get('frame_id') == item['frame_id']})
        if len(designs) != 1:
            problems.add('candidate_design_version_unresolved',
                         f'{item["candidate_id"]}: frame {item["frame_id"]!r} has design versions {designs} in the index export')
            continue
        query_side = _side_from_row(item['query_row'])
        cand_side = _candidate_side(item, designs[0], query_side['analysis_layer'])
        a, b = (query_side, cand_side) if direction == 'reference_to_target' else (cand_side, query_side)
        pair = {'pair_id': pair_id_for(direction, item['query_roi_id'], item['rank'], item['center_xy_um']),
                'a': a, 'b': b, 'repeats_per_frame': None,
                'source': {'kind': 'manual', 'labelled_by': labelled_by, 'labelled_at': labelled_at},
                'status': 'ready', 'reasons': []}
        try:
            bound = bind_pairs(_wrap([pair]), reference, target)[0]
        except NumericContractError as exc:
            problems.add(exc.code, f'{item["candidate_id"]}: {exc}')
            continue
        reasons = set(bound.reasons)
        frame_a, frame_b = reference.frames[a['frame_id']], target.frames[b['frame_id']]
        if frame_a.pixel_pitch_um != frame_b.pixel_pitch_um:
            reasons.add('pixel_pitch_mismatch')
        pair['reasons'] = sorted(reasons)
        pair['status'] = 'blocked' if reasons else 'ready'
        pairs.append((item, pair))
    return pairs


def _candidate_item(match, cid):
    item = match['table'].get(cid)
    return item if item is not None else match['model_table'][cid]


def _build_model_pairs(accepted, models, datasets, labelled_by, labelled_at, problems):
    """모델 후보 accept → {모델 key: [(item, pair)]}. a = target 질의 행, b = 모델 후보, 결속은 (target, X) 데이터셋.

    assets_pending 모델의 accept 는 쌍이 되지 않고 'deferred' 항목([item])으로만 돌려준다(키 'deferred')."""
    from .correspondence import bind_pairs
    from .models import CONFIGURED, model_pair_id
    target = datasets['target']['dataset']
    built = {key: {'pairs': [], 'deferred': []} for key in models['models']}
    for item in accepted:
        key = item['product_id']
        entry = models['models'][key]
        if entry['status'] != CONFIGURED:
            built[key]['deferred'].append(item)
            continue
        model_dataset = entry['dataset']
        designs = sorted({case.identity.design_version for case in model_dataset.cases})
        if len(designs) != 1:
            problems.add('candidate_design_version_unresolved',
                         f'{item["candidate_id"]}: model {key!r} dataset has design versions {designs}')
            continue
        query_side = _side_from_row(item['query_row'])
        cand_side = _candidate_side(item, designs[0], query_side['analysis_layer'])
        pair = {'pair_id': model_pair_id(item['direction'], item['query_roi_id'], item['rank'], key, item['center_xy_um']),
                'a': query_side, 'b': cand_side, 'repeats_per_frame': None,
                'source': {'kind': 'manual', 'labelled_by': labelled_by, 'labelled_at': labelled_at},
                'status': 'ready', 'reasons': []}
        try:
            bound = bind_pairs(_wrap([pair]), target, model_dataset)[0]
        except NumericContractError as exc:
            problems.add(exc.code, f'{item["candidate_id"]}: {exc}')
            continue
        reasons = set(bound.reasons)
        if target.frames[query_side['frame_id']].pixel_pitch_um != model_dataset.frames[cand_side['frame_id']].pixel_pitch_um:
            reasons.add('pixel_pitch_mismatch')
        pair['reasons'] = sorted(reasons)
        pair['status'] = 'blocked' if reasons else 'ready'
        built[key]['pairs'].append((item, pair))
    return built


def _wrap(pairs):
    return {'schema_version': 'roi_correspondence_v1', 'producer': {'kind': 'manual', 'tool': 'simcompare confirm-pairs'},
            'pairs': pairs}


def _oriented(item):
    """(a 중심, a frame, b 중심, b frame): a=reference, b=target 로 뒤집은 값."""
    if item['direction'] == 'reference_to_target':
        return item['query_center_xy_um'], item['query_frame_id'], item['center_xy_um'], item['frame_id']
    return item['center_xy_um'], item['frame_id'], item['query_center_xy_um'], item['query_frame_id']


def _distance(p, q):
    return math.hypot(p[0]-q[0], p[1]-q[1])


def _closeness(first, second, tolerance):
    """두 후보(서로 다른 방향)의 a·b 중심이 모두 tolerance 이내면 max 거리, 아니면 None."""
    ca, fa, cb, fb = _oriented(first)
    da, fa2, db, fb2 = _oriented(second)
    if (fa, fb) != (fa2, fb2):
        return None
    near = max(_distance(ca, da), _distance(cb, db))
    return near if near <= tolerance else None


def _context_compatible(first_pair, second_pair):
    """같은 분석층(a·b 양쪽), 양쪽 쌍이 failure_type 을 갖고 있으면 같은 failure_type 일 때만 같은 측정으로 본다.
    후보 쪽은 failure_type 이 없으므로(질의 행만 가짐) 쌍마다 a·b 에 적힌 값을 모아 비교한다."""
    for side in ('a', 'b'):
        if first_pair[side]['analysis_layer'] != second_pair[side]['analysis_layer']:
            return False
    failure = [{pair[side].get('failure_type') for side in ('a', 'b')} - {None} for pair in (first_pair, second_pair)]
    return not (failure[0] and failure[1] and failure[0] != failure[1])


def _mutual(entries, tolerance):
    """entries: [(item, pair)] 중 ready 인 것. mutual 표시와 1:1 병합 짝을 정한다(쌍을 더하거나 버리지 않는다)."""
    ready = [(item, pair) for item, pair in entries if pair['status'] == 'ready']
    near = {}
    candidates = []
    for item, pair in ready:
        near.setdefault(pair['pair_id'], [])
    for i, (first_item, first_pair) in enumerate(ready):
        for second_item, second_pair in ready[i+1:]:
            if first_item['direction'] == second_item['direction']:
                continue
            gap = _closeness(first_item, second_item, tolerance)
            if gap is None:
                continue
            near[first_pair['pair_id']].append(second_pair['pair_id'])
            near[second_pair['pair_id']].append(first_pair['pair_id'])
            candidates.append((gap, *sorted((first_pair['pair_id'], second_pair['pair_id']))))
    taken, merged = set(), {}
    differs = set()
    by_pair = {pair['pair_id']: pair for _, pair in ready}
    for gap, low, high in list(candidates):
        if not _context_compatible(by_pair[low], by_pair[high]):
            candidates.remove((gap, low, high))
            differs.update((low, high))
    for gap, low, high in sorted(candidates):
        if low in taken or high in taken:
            continue
        taken.update((low, high))
        merged[high] = low
    return {k: sorted(v) for k, v in near.items()}, merged, differs


def _provenance(item, decision, matches):
    match = matches[item['direction']]
    return {'candidate_id': item['candidate_id'], 'direction': item['direction'], 'query_roi_id': item['query_roi_id'],
            'rank': item['rank'], 'score': item['score'], 'score_gap': item['score_gap'], 'flags': item['flags'],
            'shared_with': item['shared_with'], 'template_sha256': item['template_sha256'],
            'candidate_center_xy_um': item['center_xy_um'], 'candidate_frame_id': item['frame_id'],
            'query_center_xy_um': item['query_center_xy_um'], 'match_reasons': item['reasons'],
            'decision': decision['decision'], 'decision_note': decision['note'],
            'match_file_sha256': match['sha256'], 'implementation_revision': match['implementation_revision'],
            **({k: item[k] for k in ('match_schema', 'search_scope', 'candidate_roi_id', 'index_export_verification')} if item.get('match_schema') else {})}


def _write_text(path, text, track):
    track(path)
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:  # Path.write_text(newline=) is 3.10+
        handle.write(text)


def _json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'


def _load_datasets(cfg):
    from . import pipeline
    from .correspondence import _dataset_product
    real = [pipeline._real_source(cfg, cfg.get(name)) is not None for name in ('reference', 'target')]
    if any(real) and not all(real):
        raise NumericContractError('mixed_real_and_numeric_source_formats')
    datasets = {}
    for name in ('reference', 'target'):
        if not isinstance(cfg.get(name), dict):
            raise NumericContractError('numeric_input_required', name)
        section = cfg[name]
        real_source = pipeline._real_source(cfg, section)
        if real_source is not None:
            configured = pipeline._configured_real(cfg, section, real_source)
            if configured is None:
                raise NumericContractError('explicit_condition_assignments_required', name)
            root, dataset = configured
        else:
            root, dataset = pipeline._numeric_input(cfg, section)
        datasets[name] = {'dataset': dataset, 'product': _dataset_product(dataset, name), 'root': root,
                          'manifest_path': section.get('manifest_path', 'manifest.json'), 'real': real_source is not None}
    return datasets


def _check_chain(cands, datasets, problems):
    """candidates -> export -> 현재 데이터셋 사슬: 원본 지문과 export 행의 frame 서술이 candidates 때와 같아야 한다."""
    from .numeric_images import resolve_path
    from .roi_export import _frame_payload
    recorded_inputs = cands['payload'].get('inputs') or {}
    for name in ('reference', 'target'):
        recorded = recorded_inputs.get(name) or {}
        info = datasets[name]
        try:
            manifest_sha = _sha256_bytes(resolve_path(Path(info['root']), info['manifest_path']).read_bytes())
        except (OSError, NumericContractError) as exc:
            problems.add('confirm_source_changed_since_candidates', f'{name} manifest unreadable: {exc}')
            continue
        current_hash = info['dataset'].provenance.get('input_hash')
        for field, current in (('manifest_sha256', manifest_sha), ('dataset_input_hash', current_hash)):
            if not isinstance(current, str) or recorded.get(field) != current:
                problems.add('confirm_source_changed_since_candidates',
                             f'{name}.{field}: current {current!r} != candidates record {recorded.get(field)!r}')
    seen = set()
    for direction, side in cands['directions'].items():
        for key in ('query', 'index'):
            part = side.get(key)
            if not part or part.get('export') is None or (part['path'] in seen):
                continue
            seen.add(part['path'])
            frames = datasets[part['dataset_name']]['dataset'].frames
            for roi_id, row in sorted(part['rows'].items()):
                descriptor = row.get('frame')
                if descriptor is None and row.get('status') != 'ready':
                    continue
                current = frames.get(row.get('frame_id'))
                if current is None or descriptor != _frame_payload(current):
                    problems.add('confirm_frame_changed_since_candidates',
                                 f'{direction} {key} export {roi_id}: frame {row.get("frame_id")!r} differs from the current {part["dataset_name"]} dataset')


def _check_args(labelled_by, tolerance):
    if not isinstance(labelled_by, str) or not labelled_by.strip():
        raise NumericContractError('labelled_by_required', '--labelled-by 는 비어 있지 않은 사용자 ID 여야 합니다')
    if isinstance(tolerance, bool) or not _is_number(tolerance) or tolerance < 0:
        raise NumericContractError('invalid_mutual_tolerance', '--mutual-tolerance-um 은 0 이상의 유한한 수여야 합니다')


def _prepare(cfg, candidates_dir, matches, problems, models_path=None):
    """반환: (datasets, cands, loaded, models). models 는 --models 가 없으면 None."""
    if not matches:
        raise NumericContractError('match_file_required', '--match 를 하나 이상 지정해야 합니다')
    for direction in matches:
        if direction not in DIRECTION_NAMES:
            raise NumericContractError('unknown_match_direction', direction)
    datasets = _load_datasets(cfg)
    models, guarded = None, datasets
    if models_path is not None:
        from .models import CONFIGURED, load_models
        models = load_models(models_path, cfg, config_real=all(item['real'] for item in datasets.values()))
        guarded = {**datasets, **{'model:' + key: {'dataset': entry['dataset']}
                                  for key, entry in models['models'].items() if entry['status'] == CONFIGURED}}
    cands = _load_candidates(candidates_dir, cfg, problems, guarded, matches)
    loaded = {}
    if cands is not None:
        _check_chain(cands, datasets, problems)
        for direction, path in matches.items():
            if (cands['directions'][direction]['query'] is None or cands['directions'][direction]['index'] is None):
                continue
            match = _load_match(direction, path, cands, datasets, problems, models)
            if match is not None:
                loaded[direction] = match
    return datasets, cands, loaded, models


def list_candidates(cfg, candidates_dir, matches, models_path=None):
    """DL2 검토 시트에 보이는 (질의 roi_id, 순위)에 해당하는 candidate_id 목록. 점수는 싣지 않는다."""
    problems = _Problems()
    _, _, loaded, models = _prepare(cfg, candidates_dir, matches, problems, models_path)
    problems.raise_if_any()
    listing = {'candidate_id_format': '<direction>:<query_roi_id>:r<rank>', 'candidates': [], 'unavailable_queries': []}
    unavailable_candidates = []
    for direction in DIRECTION_NAMES:
        match = loaded.get(direction)
        if match is None:
            continue
        usable = {**match['table'], **match.get('model_table', {})}
        for cid in sorted(usable):
            item = usable[cid]
            entry = {'candidate_id': cid, 'direction': direction, 'query_roi_id': item['query_roi_id'],
                     'rank': item['rank'], 'candidate_product_id': item['product_id'],
                     'candidate_frame_id': item['frame_id'], 'candidate_center_xy_um': item['center_xy_um'],
                     'flags': {k: v for k, v in sorted(item['flags'].items()) if k != 'score_gap'}}
            if item.get('match_schema'):
                entry['search_scope'] = item['search_scope']
                entry['index_export_verification'] = item['index_export_verification']
            if item.get('is_model'):
                entry['model_status'] = item['model_status']
            listing['candidates'].append(entry)
        for roi_id, reasons in sorted(match['unusable'].items()):
            listing['unavailable_queries'].append({'direction': direction, 'query_roi_id': roi_id, 'reasons': reasons})
        for cid, item in sorted({**match.get('unusable_candidates', {}), **match.get('model_unusable', {})}.items()):
            unavailable_candidates.append(item)
    if any(m.get('match_schema') for m in loaded.values()):
        listing['unavailable_candidates'] = unavailable_candidates
        listing['batch_directions'] = {
            name: {key: m['sidecar_extra'][key] for key in ('index_sources_kind', 'index_export_verification',
                                                            'rankings_skipped_other_direction', 'warnings')}
            for name, m in sorted(loaded.items()) if m.get('match_schema')}
    listing['n_candidates'] = len(listing['candidates'])
    return listing


def run_confirm_pairs(cfg, out, *, candidates_dir, matches, decisions_path, labelled_by, mutual_tolerance_um, now=None,
                      models_path=None):
    """새 출력 폴더에 roi_correspondence.json, confirmed_pairs_sidecar.json, confirm_funnel.json/.csv, execution.json 을 쓴다.

    입력·정합성 문제는 출력 폴더를 만들기 전에 모두 모아 NumericContractError(종료 코드 2)로 알린다.
    쓰는 도중 실패하면 이 실행이 만든 파일과 폴더만 지운다(접두 패턴·재귀 삭제 없음).
    models_path(--models)가 있으면 models/ 아래에 모델별 쌍 파일·유예·결정 현황·색인을 더 쓴다(기존 파일은 reference↔target 그대로).
    """
    from .correspondence import load_correspondence, validate_correspondence, write_correspondence
    _check_args(labelled_by, mutual_tolerance_um)
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise NumericContractError('output_dir_exists', str(out))
    problems = _Problems()
    datasets, cands, matches_loaded, models = _prepare(cfg, candidates_dir, matches, problems, models_path)
    decisions = _read_decisions(decisions_path, problems)
    chosen = _classify_decisions(decisions, matches_loaded, problems) if decisions is not None else {}
    problems.raise_if_any()
    labelled_at = (now or utc_now_iso)()
    labelled_by = labelled_by.strip()
    accepted_all = sorted((_candidate_item(matches_loaded[m.group(1)], cid) for cid in chosen
                           if chosen[cid]['decision'] == 'accept' for m in [ID_PATTERN.match(cid)]),
                          key=lambda item: item['candidate_id'])
    accepted = [item for item in accepted_all if not item.get('is_model')]
    model_accepted = [item for item in accepted_all if item.get('is_model')]
    entries = _build_pairs(accepted, matches_loaded, datasets, labelled_by, labelled_at, problems)
    model_entries = _build_model_pairs(model_accepted, models, datasets, labelled_by, labelled_at, problems) if models else {}
    problems.raise_if_any()
    near, merged, differs = _mutual(entries, mutual_tolerance_um)
    kept = [(item, pair) for item, pair in entries if pair['pair_id'] not in merged]
    kept.sort(key=lambda entry: entry[1]['pair_id'])
    by_id = {pair['pair_id']: item for item, pair in entries}
    payload = {'schema_version': 'roi_correspondence_v1',
               'producer': {'kind': 'manual', 'tool': 'simcompare confirm-pairs', 'version': '1',
                            'commit': cands['payload'].get('producer_commit')},
               'pairs': [pair for _, pair in kept], 'notes': NOTE}
    normalised = validate_correspondence(payload)

    pair_records, opposite_all = {}, {name: matches_loaded[name]['table'] for name in matches_loaded}
    for item, pair in kept:
        pid = pair['pair_id']
        sources = [_provenance(item, chosen[item['candidate_id']], matches_loaded)]
        away = sorted(high for high, low in merged.items() if low == pid)
        for dropped in away:
            dropped_item = by_id[dropped]
            sources.append(_provenance(dropped_item, chosen[dropped_item['candidate_id']], matches_loaded))
        record = {'pair_id': pid, 'status': pair['status'], 'reasons': pair['reasons'], 'direction': item['direction'],
                  'candidates': sources, 'merged_with': away}
        if pair['status'] == 'ready':
            record['mutual_status'] = 'mutual' if near.get(pid) else 'one_way'
            record['mutual_with'] = sorted(near.get(pid, []))
            record['mutual_context_differs'] = pid in differs
            record['opposite_match_given'] = any(name != item['direction'] for name in matches_loaded)
            opposite = []
            for name, table in opposite_all.items():
                if name == item['direction']:
                    continue
                for other in sorted(table):
                    if _closeness(item, table[other], mutual_tolerance_um) is not None:
                        opposite.append({'candidate_id': other, 'decision': chosen.get(other, {}).get('decision', 'undecided')})
            record['opposite_candidates_within_tolerance'] = opposite
        else:
            record['mutual_status'] = None
        pair_records[pid] = record
    merged_away = {high: {'kept_pair_id': low, 'candidate_id': by_id[high]['candidate_id']} for high, low in sorted(merged.items())}

    funnel = _funnel(matches_loaded, chosen, entries, kept, merged)
    ready_count = sum(pair['status'] == 'ready' for _, pair in kept)
    blocked_count = len(kept)-ready_count
    model_ready = sum(pair['status'] == 'ready' for built in model_entries.values() for _, pair in built['pairs'])
    model_blocked = sum(len(built['pairs']) for built in model_entries.values())-model_ready
    model_deferred = sum(len(built['deferred']) for built in model_entries.values())
    complete = ready_count+model_ready > 0 and blocked_count+model_blocked == 0 and model_deferred == 0
    exit_code = 0 if complete else 3
    policy = {'scores': 'display_and_provenance_only_never_used_for_selection_ordering_or_merging',
              'mutual_one_way': 'marking_only_merge_keeps_lower_pair_id_only_for_same_analysis_layer_and_failure_type_otherwise_both_kept_with_mutual_context_differs',
              'undecided_and_rejected': 'never_in_correspondence'}
    inputs_record = {'config_sha256': cfg.get('_config_sha256'),
                     'candidates_payload_sha256': cands['payload_sha256'], 'decisions_sha256': decisions['sha256'],
                     'matches': {name: {**{k: m[k] for k in ('path', 'sha256', 'implementation_revision', 'window_um', 'topk',
                                                              'self_check', 'inputs')}, **m.get('sidecar_extra', {})}
                                 for name, m in sorted(matches_loaded.items())}}
    sidecar = {'schema_version': SIDECAR_SCHEMA, 'labelled_by': labelled_by, 'labelled_at': labelled_at,
               'mutual_tolerance_um': float(mutual_tolerance_um), 'policy': policy, 'inputs': inputs_record,
               'pairs': pair_records, 'merged_away': merged_away, 'warnings': normalised['warnings']}
    if models:
        funnel['models'] = _models_funnel(models, matches_loaded, chosen, model_entries)
    created_files, created_dirs = [], []

    def track(path, is_dir=False):
        (created_dirs if is_dir else created_files).append(Path(path))

    out.mkdir()
    track(out, is_dir=True)
    try:
        track(out/'roi_correspondence.json')
        written = write_correspondence(out/'roi_correspondence.json', payload)
        reloaded = load_correspondence(written)  # round trip through the real loader
        if reloaded != normalised:
            raise NumericContractError('correspondence_round_trip_mismatch', written)
        _write_text(out/'confirmed_pairs_sidecar.json', _json_text(sidecar), track)
        _write_text(out/'confirm_funnel.json', _json_text(funnel), track)
        csv_path = out/'confirm_funnel.csv'
        track(csv_path)
        with csv_path.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.writer(handle, lineterminator='\n')
            writer.writerow(['direction', 'stage', 'count', 'reasons'])
            for row in funnel['directions']:
                for stage in row['stages']:
                    writer.writerow([row['direction'], stage['stage'], stage['count'],
                                     ';'.join(f'{k}:{v}' for k, v in stage['reasons'].items())])
            for stage in funnel['totals']['stages']:
                writer.writerow(['total', stage['stage'], stage['count'],
                                 ';'.join(f'{k}:{v}' for k, v in stage['reasons'].items())])
        outputs = ['confirm_funnel.csv', 'confirm_funnel.json', 'confirmed_pairs_sidecar.json', 'roi_correspondence.json']
        model_summary = None
        if models:
            written_models, model_summary = _write_models(
                out, models, model_entries, matches_loaded, chosen, datasets, cands, cfg, payload['producer'],
                {'labelled_by': labelled_by, 'labelled_at': labelled_at, 'tolerance': float(mutual_tolerance_um),
                 'policy': policy, 'inputs': inputs_record}, track)
            outputs += written_models
        reasons = []
        if ready_count+model_ready == 0:
            reasons.append('no_emitted_ready_pairs')
        if blocked_count+model_blocked:
            reasons.append('blocked_pairs_present')
        if model_deferred:
            reasons.append('model_assets_pending')
        result = {'status': 'available' if complete else 'partial', 'reasons': reasons, 'outputs': sorted(outputs+['execution.json']),
                  'exit_code': exit_code, 'labelled_by': labelled_by, 'labelled_at': labelled_at,
                  'n_accepted': len(accepted), 'n_emitted_ready': ready_count, 'n_emitted_blocked': blocked_count,
                  'n_merged_away': len(merged), 'funnel': funnel['totals']['counts']}
        if model_summary is not None:
            result['models'] = model_summary
        _write_text(out/'execution.json', _json_text({'command': 'confirm-pairs', **result}), track)
        return result
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


MODEL_DECISIONS_SCHEMA = 'confirm_model_decisions_v1'
MODELS_INDEX_SCHEMA = 'confirm_models_index_v1'
MODEL_DEFERRED_SCHEMA = 'confirm_model_deferred_v1'


def _model_items(matches, product):
    """(usable model candidates, unusable model candidates) of one model product over all given match files."""
    usable, unusable = {}, {}
    for match in matches.values():
        usable.update({cid: item for cid, item in match.get('model_table', {}).items() if item['product_id'] == product})
        unusable.update({cid: item for cid, item in match.get('model_unusable', {}).items()
                         if item['candidate_product_id'] == product})
    return usable, unusable


def _models_funnel(models, matches, chosen, built):
    """funnel['models']: 모델별 깔때기. 기존 directions/totals 는 reference↔target 만 센다."""
    result = {}
    for key in sorted(models['models']):
        usable, unusable = _model_items(matches, key)
        decision = {cid: chosen.get(cid, {}).get('decision') for cid in usable}
        accept = sum(1 for value in decision.values() if value == 'accept')
        reject = sum(1 for value in decision.values() if value == 'reject')
        pairs = [pair for _, pair in built[key]['pairs']]
        blocked = [pair for pair in pairs if pair['status'] == 'blocked']
        reasons = {}
        for pair in blocked:
            for code in pair['reasons']:
                reasons[code] = reasons.get(code, 0)+1
        unusable_reasons = {}
        for item in unusable.values():
            unusable_reasons[item['reason']] = unusable_reasons.get(item['reason'], 0)+1
        result[key] = {'asset_status': models['models'][key]['status'], 'stages': [
            {'stage': 'candidates_in_match_file', 'count': len(usable), 'reasons': dict(sorted(unusable_reasons.items()))},
            {'stage': 'decided_accept', 'count': accept, 'reasons': {}},
            {'stage': 'decided_reject', 'count': reject, 'reasons': {}},
            {'stage': 'undecided', 'count': len(usable)-accept-reject, 'reasons': {}},
            {'stage': 'deferred_assets_pending', 'count': len(built[key]['deferred']), 'reasons': {}},
            {'stage': 'blocked', 'count': len(blocked), 'reasons': dict(sorted(reasons.items()))},
            {'stage': 'emitted_ready_pairs', 'count': len(pairs)-len(blocked), 'reasons': {}}]}
    return result


def _model_decisions(models, matches, chosen, cands, datasets):
    """models/decisions.json: target 질의 ROI × 제품(reference 포함)마다 후보·결정·사유별 사용 불가·유예 수.

    결정(accepted/rejected/undecided)과 자산 상태(asset_status)는 따로 적는다. 후보 없음(candidates=0)과 모두 reject
    (candidates>0, rejected=candidates)가 구별된다."""
    reference_product = datasets['reference']['product']
    status = {reference_product: 'config_side'}
    status.update({key: models['models'][key]['status'] for key in models['models']})
    products = [reference_product]+sorted(models['models'])
    match = matches.get('target_to_reference')
    rows = ((cands['directions'].get('target_to_reference') or {}).get('query') or {}).get('rows', {})
    roi_ids = sorted(roi for roi, row in rows.items() if row.get('status') == 'ready')
    cells = {(roi, product): {'candidates': 0, 'accepted': 0, 'rejected': 0, 'undecided': 0, 'unavailable': {}, 'deferred': 0,
                              'asset_status': status[product]} for roi in roi_ids for product in products}
    ranked = set()
    if match is not None:
        ranked.update(match['unusable'])
        usable = list(match['table'].values())+list(match.get('model_table', {}).values())
        for item in usable:
            ranked.add(item['query_roi_id'])
            cell = cells.get((item['query_roi_id'], item['product_id']))
            if cell is None:
                continue
            cell['candidates'] += 1
            decision = chosen.get(item['candidate_id'], {}).get('decision')
            if decision == 'accept':
                cell['accepted'] += 1
                if item.get('is_model') and item['model_status'] != 'configured':
                    cell['deferred'] += 1
            elif decision == 'reject':
                cell['rejected'] += 1
            else:
                cell['undecided'] += 1
        for item in list(match.get('unusable_candidates', {}).values())+list(match.get('model_unusable', {}).values()):
            ranked.add(item['query_roi_id'])
            cell = cells.get((item['query_roi_id'], item['candidate_product_id']))
            if cell is None:
                continue
            cell['candidates'] += 1
            cell['unavailable'][item['reason']] = cell['unavailable'].get(item['reason'], 0)+1
    for cell in cells.values():
        cell['unavailable'] = dict(sorted(cell['unavailable'].items()))
    other = matches.get('reference_to_target')
    return {'schema_version': MODEL_DECISIONS_SCHEMA, 'target_product_id': models['target_product_id'],
            'reference_product_id': reference_product, 'target_to_reference_match_given': match is not None,
            'asset_status': dict(sorted(status.items())),
            'reference_to_target_model_candidates_unusable': len(other.get('model_unusable', {})) if other else 0,
            'rois': [{'query_roi_id': roi, 'ranked': roi in ranked,
                      'query_unavailable_reasons': list(match['unusable'].get(roi, [])) if match is not None else [],
                      'products': {product: cells[(roi, product)] for product in products}} for roi in roi_ids]}


def _ensure_dir(path, track):
    if not path.is_dir():
        path.mkdir()
        track(path, is_dir=True)


def _write_models(out, models, built, matches, chosen, datasets, cands, cfg, producer, common, track):
    """models/ 아래 파일을 쓴다. 반환: (out 기준 상대 경로 목록, 실행 요약)."""
    from .correspondence import load_correspondence, validate_correspondence, write_correspondence
    from .models import CONFIGURED
    root = out/'models'
    _ensure_dir(root, track)
    written, index, summary = [], {}, {}
    for key in sorted(models['models']):
        entry, data = models['models'][key], built[key]
        folder = root/key
        pairs = sorted(data['pairs'], key=lambda pair_entry: pair_entry[1]['pair_id'])
        n_ready = sum(pair['status'] == 'ready' for _, pair in pairs)
        info = {'product_id': key, 'status': entry['status'], 'n_ready': n_ready, 'n_blocked': len(pairs)-n_ready,
                'n_deferred': len(data['deferred']), 'folder': f'models/{key}', 'correspondence': None}
        if entry['status'] == CONFIGURED:
            _ensure_dir(folder, track)
            payload = {'schema_version': 'roi_correspondence_v1', 'producer': dict(producer),
                       'pairs': [pair for _, pair in pairs], 'notes': f'a=target(query), b={key}. {NOTE}'}
            normalised = validate_correspondence(payload)
            track(folder/'roi_correspondence.json')
            path = write_correspondence(folder/'roi_correspondence.json', payload)
            if load_correspondence(path) != normalised:
                raise NumericContractError('correspondence_round_trip_mismatch', path)
            records = {}
            for item, pair in pairs:
                record = {'pair_id': pair['pair_id'], 'status': pair['status'], 'reasons': pair['reasons'],
                          'direction': item['direction'], 'candidates': [_provenance(item, chosen[item['candidate_id']], matches)],
                          'merged_with': []}
                if pair['status'] == 'ready':
                    record.update(mutual_status='one_way', mutual_with=[], mutual_context_differs=False,
                                  opposite_match_given=False, opposite_candidates_within_tolerance=[])
                else:
                    record['mutual_status'] = None
                records[pair['pair_id']] = record
            sidecar = {'schema_version': SIDECAR_SCHEMA, 'labelled_by': common['labelled_by'], 'labelled_at': common['labelled_at'],
                       'mutual_tolerance_um': common['tolerance'], 'policy': dict(common['policy']), 'inputs': common['inputs'],
                       'pairs': records, 'merged_away': {}, 'warnings': normalised['warnings'],
                       'model': {'product_id': key, 'status': entry['status'], 'models_sha256': models['sha256'],
                                 'data_dir': entry['data_dir'], 'manifest_sha256': entry['manifest_sha256'],
                                 'asset_manifest_sha256': entry['asset_manifest_sha256'],
                                 'electrode_import_record_sha256': entry['electrode_import_record_sha256']}}
            _write_text(folder/'confirmed_pairs_sidecar.json', _json_text(sidecar), track)
            info['correspondence'] = f'models/{key}/roi_correspondence.json'
            written += [f'models/{key}/roi_correspondence.json', f'models/{key}/confirmed_pairs_sidecar.json']
        if data['deferred']:
            _ensure_dir(folder, track)
            deferred = {'schema_version': MODEL_DEFERRED_SCHEMA, 'product_id': key, 'reason': 'model_assets_pending',
                        'models_sha256': models['sha256'],
                        'candidate_ids': sorted(item['candidate_id'] for item in data['deferred'])}
            _write_text(folder/'deferred.json', _json_text(deferred), track)
            written.append(f'models/{key}/deferred.json')
        index[key] = info
        summary[key] = {'status': entry['status'], 'n_ready': info['n_ready'], 'n_blocked': info['n_blocked'],
                        'n_deferred': info['n_deferred']}
    _write_text(root/'decisions.json', _json_text(_model_decisions(models, matches, chosen, cands, datasets)), track)
    _write_text(root/'index.json', _json_text({'schema_version': MODELS_INDEX_SCHEMA, 'target_product_id': models['target_product_id'],
                                                'models': index}), track)
    return written+['models/decisions.json', 'models/index.json'], summary


def _funnel(matches, chosen, entries, kept, merged):
    """S1: match 파일 후보 → 결정(accept/reject/미결정) → 확정 → blocked(사유) → 병합 → 출력 쌍."""
    rows = []
    totals = {'candidates': 0, 'accept': 0, 'reject': 0, 'undecided': 0, 'blocked': 0, 'merged_away': 0, 'emitted_ready': 0}
    blocked_total = {}
    for direction in DIRECTION_NAMES:
        match = matches.get(direction)
        if match is None:
            continue
        ids = set(match['table'])
        accept = sum(1 for cid in ids if chosen.get(cid, {}).get('decision') == 'accept')
        reject = sum(1 for cid in ids if chosen.get(cid, {}).get('decision') == 'reject')
        built = [(item, pair) for item, pair in entries if item['direction'] == direction]
        blocked = [pair for _, pair in built if pair['status'] == 'blocked']
        reasons = {}
        for pair in blocked:
            for code in pair['reasons']:
                reasons[code] = reasons.get(code, 0)+1
                blocked_total[code] = blocked_total.get(code, 0)+1
        dropped = sum(1 for _, pair in built if pair['pair_id'] in merged)
        ready = sum(1 for item, pair in kept if item['direction'] == direction and pair['status'] == 'ready')
        stage_reasons = {'queries_without_candidates': len(match['unusable'])} if match['unusable'] else {}
        if match.get('unusable_candidates'):
            stage_reasons['unusable_candidates'] = len(match['unusable_candidates'])
        stages = [{'stage': 'candidates_in_match_file', 'count': len(ids), 'reasons': stage_reasons},
                  {'stage': 'decided_accept', 'count': accept, 'reasons': {}},
                  {'stage': 'decided_reject', 'count': reject, 'reasons': {}},
                  {'stage': 'undecided', 'count': len(ids)-accept-reject, 'reasons': {}},
                  {'stage': 'blocked', 'count': len(blocked), 'reasons': dict(sorted(reasons.items()))},
                  {'stage': 'merged_away_mutual', 'count': dropped, 'reasons': {}},
                  {'stage': 'emitted_ready_pairs', 'count': ready, 'reasons': {}}]
        rows.append({'direction': direction, 'n_queries_in_match_file': match['n_queries'], 'stages': stages})
        for key, value in (('candidates', len(ids)), ('accept', accept), ('reject', reject), ('undecided', len(ids)-accept-reject),
                           ('blocked', len(blocked)), ('merged_away', dropped), ('emitted_ready', ready)):
            totals[key] += value
    total_stages = [{'stage': name, 'count': totals[key], 'reasons': dict(sorted(blocked_total.items())) if key == 'blocked' else {}}
                    for name, key in (('candidates_in_match_files', 'candidates'), ('decided_accept', 'accept'),
                                      ('decided_reject', 'reject'), ('undecided', 'undecided'), ('blocked', 'blocked'),
                                      ('merged_away_mutual', 'merged_away'), ('emitted_ready_pairs', 'emitted_ready'))]
    return {'schema_version': FUNNEL_SCHEMA, 'directions': rows,
            'totals': {'counts': totals, 'stages': total_stages},
            'note': 'blocked 는 확정됐지만 S5 검사에 걸린 쌍이며 roi_correspondence.json 에 status=blocked 로도 들어간다. 병합 전 blocked 는 병합 대상이 아니다'}
