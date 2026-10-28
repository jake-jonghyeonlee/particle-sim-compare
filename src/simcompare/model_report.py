"""compare-multiproduct T3: model_report_v1, the one-page ROI x model table.

Rows are target query ROIs (the `target_to_reference` ROIs of `models/decisions.json`), columns are models (the reference
product included). Everything shown is copied from existing outputs: nothing is recomputed. A value string such as
`delta_um2` is the cell text of `local_delta_rows.csv`; only the unified column `delta_model_minus_target_um2` differs, and
only by the sign of a reference pair (text edit, no float re-formatting). The change columns are the reference -> target
change attributes of candidates-change plus, for a model pair, the `window_cases_v1` counts of compare-windows. DL2 / NCC
search scores are never read into the report.

Input generations are checked before any file is written (see `check_*` below): a refused input is a NumericContractError
(exit 2, no output folder).
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import os
import shutil
import tempfile
from pathlib import Path

from .numeric_schema import NumericContractError

SCHEMA_VERSION = 'model_report_v1'
HTML_NAME, CSV_NAME, JSON_NAME, EXECUTION_NAME = 'model_report.html', 'model_report.csv', 'model_report.json', 'execution.json'
SUCCESS_EXIT = (0, 3)
SIGN_RULE = ('raw delta_um2 is b - a of the local_delta run: model pair = model - target, reference pair = target - reference. '
             'delta_model_minus_target_um2 = raw for a model pair, raw with the sign flipped for a reference pair (null stays null, 0 stays 0)')
CAUSE_NOTE = 'case 구성 차이는 인과를 뜻하지 않는다'
NOT_COMPUTED, NOT_APPLICABLE = 'not_computed', 'not_applicable'
CLASS_NAMES = ('target_only', 'model_only', 'unknown', 'both')
TOP_NAMES = 3
NUMERIC_FIELDS = ('a_value_um2', 'b_value_um2', 'delta_um2', 'delta_model_minus_target_um2')
CSV_COLUMNS = [
    'roi_id', 'roi_linked', 'unlinked_reason', 'model', 'model_role', 'cell_status', 'asset_status', 'n_candidates', 'n_accepted',
    'n_rejected', 'n_undecided', 'n_deferred', 'unavailable_reasons', 'pair_id', 'pair_status', 'pair_reasons', 'direction',
    'rank', 'search_scope', 'center_differs_from_query', 'target_center_xy_um', 'roi_center_xy_um', 'roi_analysis_layer',
    'a_value_um2', 'b_value_um2', 'delta_um2', 'delta_status', 'delta_reasons', 'delta_orientation',
    'delta_model_minus_target_um2', 'window_cases_status', 'window_cases_both', 'window_cases_target_only',
    'window_cases_model_only', 'window_cases_unknown', 'window_cases_top',
    'score_um2', 'changed_case_count', 'union_delta_um2', 'change_kind_counts', 'cnt_change_um2_by_layer']


def _bad(code, detail=''):
    return NumericContractError(code, detail)


# ---- file reading with sha256 records ------------------------------------------------------------------------------------

class _Files:
    """Every input file is read once; its sha256 goes into the report."""

    def __init__(self):
        self.records = {}

    def read(self, path, label):
        path = Path(path)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise _bad('model_report_input_unreadable', f'{label}: {path}: {exc}') from exc
        self.records[label] = {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest()}
        return data

    def json(self, path, label, schema=None):
        data = self.read(path, label)
        try:
            payload = json.loads(data.decode('utf-8'))
        except ValueError as exc:
            raise _bad('model_report_input_unreadable', f'{label}: {path}: {exc}') from exc
        if not isinstance(payload, dict):
            raise _bad('model_report_input_malformed', f'{label}: {path}: JSON 객체가 아닙니다')
        if schema is not None and payload.get('schema_version') != schema:
            raise _bad('model_report_unexpected_schema', f'{label}: {path}: schema_version {payload.get("schema_version")!r}, 기대값 {schema!r}')
        return payload

    def csv(self, path, label):
        data = self.read(path, label)
        try:
            return list(csv.DictReader(data.decode('utf-8').splitlines(), skipinitialspace=False))
        except (ValueError, csv.Error) as exc:
            raise _bad('model_report_input_unreadable', f'{label}: {path}: {exc}') from exc

    def sha(self, label):
        return self.records[label]['sha256']


def _same(first, second):
    return abs(float(first)-float(second)) < 1e-9


def _text_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ---- sign handling on cell text --------------------------------------------------------------------------------------------

def negate_text(text):
    """The cell text with the sign of the number flipped (no float re-formatting). '', 'null' stay; zero loses any sign."""
    if text in ('', 'null'):
        return text
    number = _text_number(text)
    if number is None:
        raise _bad('model_report_value_unreadable', f'수치 문자열을 읽을 수 없습니다: {text!r}')
    if number == 0:
        return text.lstrip('+-')
    if text.startswith('-'):
        return text[1:]
    if text.startswith('+'):
        return '-'+text[1:]
    return '-'+text


# ---- argument and output-folder checks ---------------------------------------------------------------------------------------

def parse_windows(specs):
    """['KEY=folder', ...] -> {KEY: folder}. Each key once, folder non-empty."""
    result = {}
    for spec in specs or []:
        key, separator, folder = spec.partition('=')
        if not separator or not key or not folder or key in result:
            raise _bad('model_report_invalid_windows_argument', f'--windows {spec!r}: <product_id>=<폴더> 형식, 키는 한 번씩')
        result[key] = folder
    if not result:
        raise _bad('model_report_invalid_windows_argument', '--windows 가 하나 이상 필요합니다')
    return result


def _check_numbers(diameter_um, window_um):
    for name, value in (('--diameter-um', diameter_um), ('--window-um', window_um)):
        number = _text_number(value)
        if number is None or number <= 0 or isinstance(value, bool):
            raise _bad('model_report_invalid_argument', f'{name} 는 0 보다 큰 유한한 수여야 합니다: {value!r}')


def _check_out(out, folders):
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise _bad('output_dir_exists', str(out))
    resolved_out = out.resolve()
    for folder in folders:
        resolved = Path(folder).resolve()
        if resolved_out == resolved or resolved in resolved_out.parents:
            raise _bad('output_is_input', f'--out 은 입력 폴더 밖의 새 폴더여야 합니다: {folder}')


# ---- loading and checking the inputs -------------------------------------------------------------------------------------------

def _check_marker(folder, files, label):
    """A folder counts as complete only with a successful execution.json and no error.json (A3)."""
    folder = Path(folder)
    if (folder/'error.json').exists():
        raise _bad('model_report_input_failed', f'{label}: {folder/"error.json"} 가 있습니다(실패한 실행의 폴더)')
    if not (folder/'execution.json').is_file():
        raise _bad('model_report_input_incomplete', f'{label}: {folder/"execution.json"} 이 없습니다(끝나지 않은 실행의 폴더)')
    execution = files.json(folder/'execution.json', f'{label} execution.json')
    if execution.get('exit_code') not in SUCCESS_EXIT:
        raise _bad('model_report_input_failed', f'{label}: execution.json exit_code {execution.get("exit_code")!r} (성공 0 또는 3 만 허용)')
    return execution


def _load_confirm(confirm_dir, candidates_sha, files):
    """The confirm-pairs --models output: pair files, sidecars, decisions, index. Candidates sha256 chain checked."""
    confirm = Path(confirm_dir)
    _check_marker(confirm, files, 'confirm')
    for name in ('models/decisions.json', 'models/index.json'):
        if not (confirm/name).is_file():
            raise _bad('model_report_confirm_not_models', f'--confirm 에 {name} 이 없습니다(confirm-pairs --models 출력이 아님)')
    decisions = files.json(confirm/'models'/'decisions.json', 'confirm models/decisions.json', 'confirm_model_decisions_v1')
    index = files.json(confirm/'models'/'index.json', 'confirm models/index.json', 'confirm_models_index_v1')
    if decisions.get('target_product_id') != index.get('target_product_id'):
        raise _bad('model_report_confirm_inconsistent', 'decisions.json 과 index.json 의 target_product_id 가 다릅니다')
    reference = decisions.get('reference_product_id')
    products = {}

    def load_side(key, correspondence, sidecar):
        corr = files.json(confirm/correspondence, f'confirm {correspondence}', 'roi_correspondence_v1')
        side = files.json(confirm/sidecar, f'confirm {sidecar}', 'confirmed_pairs_sidecar_v1')
        recorded = (side.get('inputs') or {}).get('candidates_payload_sha256')
        if recorded != candidates_sha:
            raise _bad('model_report_candidates_mismatch', f'{sidecar} 가 기록한 candidates 파일 sha256 {recorded} 이 --candidates 의 실제 값 {candidates_sha} 과 다릅니다')
        corr_ids = [pair.get('pair_id') for pair in corr.get('pairs', [])]
        if set(side.get('pairs') or {}) != set(corr_ids) or len(set(corr_ids)) != len(corr_ids):
            raise _bad('model_report_confirm_inconsistent', f'{sidecar} 의 쌍 목록이 {correspondence} 와 다르거나 pair_id 가 겹칩니다')
        products[key] = {'role': 'reference' if key == reference else 'model', 'correspondence': corr, 'sidecar': side,
                         'correspondence_sha256': files.sha(f'confirm {correspondence}'),
                         'correspondence_file': correspondence}
    load_side(reference, 'roi_correspondence.json', 'confirmed_pairs_sidecar.json')
    for key, info in sorted((index.get('models') or {}).items()):
        if info.get('correspondence'):
            expected = f'models/{key}/roi_correspondence.json'
            if info['correspondence'] != expected:
                raise _bad('model_report_confirm_inconsistent', f'index.json 의 {key} 대응 파일 경로가 {expected} 가 아닙니다')
            load_side(key, expected, f'models/{key}/confirmed_pairs_sidecar.json')
        else:
            products[key] = {'role': 'model', 'correspondence': None, 'sidecar': None, 'correspondence_sha256': None,
                             'correspondence_file': None}
    return decisions, index, reference, products


def _row_text(row, name):
    return row.get(name) if row.get(name) is not None else ''


def _window_rows(rows, window_um, diameter_um):
    """pair_id -> the union window row at side_um == window_um and diameter == diameter_um (CSV strings). Duplicates refused."""
    selected = {}
    for row in rows:
        if row.get('kind') != 'window' or row.get('metric') != 'union':
            continue
        side, diameter = _text_number(row.get('side_um')), _text_number(row.get('diameter_um'))
        if side is None or diameter is None or not _same(side, window_um) or not _same(diameter, diameter_um):
            continue
        if row['pair_id'] in selected:
            raise _bad('model_report_duplicate_row', f'local_delta_rows.csv 에 같은 쌍·창·지름의 union 행이 둘 이상입니다: {row["pair_id"]}')
        selected[row['pair_id']] = row
    return selected


def _load_windows(key, folder, product, reference, target, window_um, diameter_um, files):
    """One --windows folder: marker, generation, direction, pair set, window/diameter and window_cases checks (X2)."""
    label = f'windows[{key}]'
    folder = Path(folder)
    _check_marker(folder, files, label)
    delta = files.json(folder/'local_delta.json', f'{label} local_delta.json', 'local_delta_v1')
    rows = files.csv(folder/'local_delta_rows.csv', f'{label} local_delta_rows.csv')
    provenance = delta.get('provenance') or {}
    corr = product['correspondence']
    if corr is None:
        raise _bad('model_report_windows_without_pairs', f'{label}: 제품 {key!r} 는 확정 대응 파일이 없습니다(자산 대기 모델)')
    if provenance.get('correspondence_sha256') != product['correspondence_sha256']:
        raise _bad('model_report_correspondence_mismatch',
                   f'{label}: local_delta.json 이 계산에 쓴 대응 파일 sha256 {provenance.get("correspondence_sha256")} 이 '
                   f'--confirm 의 {product["correspondence_file"]} 실제 값 {product["correspondence_sha256"]} 과 다릅니다(이전 세대 폴더)')
    products = delta.get('products') or {}
    got = (((products.get('a') or {}).get('product_id')), ((products.get('b') or {}).get('product_id')))
    want = (reference, target) if product['role'] == 'reference' else (target, key)
    if got != want:
        raise _bad('model_report_products_mismatch', f'{label}: local_delta.json 의 제품 (a, b) = {got}, 기대값 {want}')
    if product['role'] == 'model' and (provenance.get('model') or {}).get('product_id') != key:
        raise _bad('model_report_products_mismatch', f'{label}: provenance.model.product_id 가 {key!r} 가 아닙니다')
    corr_ready = {pair['pair_id'] for pair in corr['pairs'] if pair.get('status') == 'ready'}
    delta_ids = [pair.get('pair_id') for pair in delta.get('pairs', [])]
    delta_ready = {pair.get('pair_id') for pair in delta.get('pairs', []) if pair.get('status') == 'ready'}
    if delta_ready != corr_ready or not set(delta_ids) <= {pair['pair_id'] for pair in corr['pairs']}:
        raise _bad('model_report_pair_set_mismatch', f'{label}: local_delta.json 의 ready 쌍 목록이 대응 파일의 ready 쌍 목록과 다릅니다')
    sizes = delta.get('window_sizes_um') or []
    if not any(_same(size, window_um) for size in sizes):
        raise _bad('model_report_window_not_available', f'{label}: --window-um {window_um:g} 은 window_sizes_um 에 없습니다: {sizes}')
    diameters = sorted({_text_number(row.get('diameter_um')) for row in rows if _text_number(row.get('diameter_um')) is not None})
    if rows and not any(_same(value, diameter_um) for value in diameters):
        raise _bad('model_report_diameter_not_available', f'{label}: --diameter-um {diameter_um:g} 은 local_delta_rows.csv 에 없습니다: {diameters}')
    target_hash = provenance.get('target_input_hash' if product['role'] == 'reference' else 'reference_input_hash')
    if not isinstance(target_hash, str) or not target_hash:
        raise _bad('model_report_target_inputs_unreadable', f'{label}: local_delta.json provenance 에 target 쪽 입력 해시가 없습니다')
    cases = None
    wanted = provenance.get('window_cases')
    if product['role'] == 'model' and wanted:
        if not _same(wanted.get('window_um'), window_um) or not _same(wanted.get('diameter_um'), diameter_um):
            raise _bad('model_report_window_cases_mismatch',
                       f'{label}: window_cases 의 창·지름 ({wanted.get("window_um")}, {wanted.get("diameter_um")}) 이 '
                       f'--window-um {window_um:g}, --diameter-um {diameter_um:g} 와 다릅니다')
        if wanted.get('file') != 'window_cases.json':
            raise _bad('model_report_window_cases_mismatch', f'{label}: provenance.window_cases.file 이 window_cases.json 이 아닙니다')
        cases = files.json(folder/'window_cases.json', f'{label} window_cases.json', 'window_cases_v1')
        pair_ids = [entry.get('pair_id') for entry in cases.get('pairs', [])]
        if (not _same(cases.get('window_um'), window_um) or not _same(cases.get('diameter_um'), diameter_um)
                or (cases.get('inputs') or {}).get('correspondence_sha256') != product['correspondence_sha256']
                or set(pair_ids) != corr_ready or len(set(pair_ids)) != len(pair_ids)):
            raise _bad('model_report_window_cases_mismatch', f'{label}: window_cases.json 이 local_delta.json 의 창·지름·대응 파일·쌍 목록과 맞지 않습니다')
    return {'folder': folder, 'rows': _window_rows(rows, window_um, diameter_um), 'target_hash': target_hash, 'cases': cases,
            'delta': delta}


# ---- candidates payload: ROI rows and change attributes -----------------------------------------------------------------------

def _direction(payload, name):
    for item in payload.get('directions', []):
        if item.get('direction') == name:
            return item
    return {}


def _export_rows(candidates_dir, payload, direction, files):
    """roi_id -> export row of the query export of `direction` (read from the candidates folder)."""
    relative = _direction(payload, direction).get('query_export')
    if not relative:
        raise _bad('model_report_candidates_incomplete', f'candidate_queries_v1.json 에 {direction} 의 query_export 가 없습니다')
    path = Path(candidates_dir)/relative
    export = files.json(path, f'candidates {relative}')
    return {row['roi_id']: row for row in export.get('rois', []) if isinstance(row, dict) and row.get('roi_id')}


def _change_attributes(payload):
    """roi_id -> the candidates-change attributes of the target query that holds the ROI (copied; None when absent)."""
    result = {}
    for query in _direction(payload, 'target_to_reference').get('queries', []):
        kinds = {}
        for case in query.get('contributing_cases') or []:
            kind = case.get('change_kind')
            kinds[kind] = kinds.get(kind, 0)+1
        values = {'score_um2': query.get('score_um2'), 'changed_case_count': query.get('changed_case_count'),
                  'union_delta_um2': query.get('union_delta_um2'),
                  'change_kind_counts': dict(sorted(kinds.items())) if query.get('contributing_cases') is not None else None,
                  'cnt_change_um2_by_layer': query.get('cnt_change_um2_by_layer')}
        for roi in query.get('roi_ids') or []:
            result[roi] = values
    return result


def _endpoint_key(row):
    return (row.get('failure_type'), row.get('analysis_layer'),
            tuple(sorted(e.get('electrode_id') for e in (row.get('endpoints') or []))))


class _Linker:
    """Attaches a reference pair to a target ROI. `target_to_reference` pairs carry the target ROI id; `reference_to_target`
    pairs go through the shared region id (same window list in both directions, docs/data_contract.md candidates-change)."""

    def __init__(self, candidates_dir, payload, roi_ids, files):
        self.roi_ids = set(roi_ids)
        self._dir, self._payload, self._files = candidates_dir, payload, files
        self._reference_rows = self._target_rows = None

    def _rows(self):
        if self._reference_rows is None:
            self._reference_rows = _export_rows(self._dir, self._payload, 'reference_to_target', self._files)
            self._target_rows = _export_rows(self._dir, self._payload, 'target_to_reference', self._files)
        return self._reference_rows, self._target_rows

    def link(self, direction, candidates):
        """(target roi_id or None, reason when None). Order: a `target_to_reference` candidate of the pair (mutual merge or
        plain), then the candidates payload (reference query -> region_id -> target query), then export `source_region_id`."""
        for item in candidates:
            if item.get('direction') == 'target_to_reference':
                roi = item.get('query_roi_id')
                return (roi, None) if roi in self.roi_ids else (None, 'query_roi_not_in_decisions')
        query_roi_id = (candidates or [{}])[0].get('query_roi_id')
        reference_rows, target_rows = self._rows()
        row = reference_rows.get(query_roi_id)
        if row is None:
            return None, 'reference_query_roi_without_region'
        region = next((q.get('region_id') for q in _direction(self._payload, 'reference_to_target').get('queries', [])
                       if query_roi_id in (q.get('roi_ids') or [])), None)
        pool = set()
        if region is not None:
            pool = {roi for q in _direction(self._payload, 'target_to_reference').get('queries', [])
                    if q.get('region_id') == region for roi in q.get('roi_ids') or []}
        if not pool and row.get('source_region_id'):
            pool = {roi for roi, other in target_rows.items() if other.get('source_region_id') == row['source_region_id']}
        if region is None and not pool:
            return None, 'reference_query_roi_without_region'
        found = sorted(roi for roi in pool if roi in self.roi_ids and roi in target_rows
                       and _endpoint_key(target_rows[roi]) == _endpoint_key(row))
        if len(found) != 1:
            return None, 'no_target_roi_for_region' if not found else 'several_target_rois_for_region'
        return found[0], None


# ---- rows --------------------------------------------------------------------------------------------------------------------

def _window_cases_view(role, windows, pair_id, pair_status):
    """(status, counts or None, top names) for one pair; the T2b adv:1/adv:4 display rules."""
    empty = {name: None for name in CLASS_NAMES}
    if role == 'reference':
        return NOT_APPLICABLE, empty, {}
    if windows['cases'] is None:
        return NOT_COMPUTED, empty, {}
    entry = next((e for e in windows['cases']['pairs'] if e['pair_id'] == pair_id), None)
    if entry is None:
        return 'pair_blocked' if pair_status != 'ready' else NOT_COMPUTED, empty, {}
    if entry.get('status') != 'available':
        return entry.get('status'), empty, {}
    absent = [f'no_candidates_{side}' for side in ('a', 'b') if (entry.get('n_candidates') or {}).get(side) == 0]
    if absent:
        return '+'.join(absent), empty, {}
    top = {name: [case['key'] for case in entry['cases'] if case['class'] == name][:TOP_NAMES] for name in CLASS_NAMES}
    return 'available', dict(entry['counts']), top


def _pair_view(key, product, windows, pair, record, window_row):
    candidate = (record.get('candidates') or [{}])[0]
    values = {name: _row_text(window_row, name) if window_row else '' for name in ('a_value_um2', 'b_value_um2', 'delta_um2',
                                                                                   'delta_status', 'delta_reasons')}
    if window_row is None and pair.get('status') == 'ready':
        values['delta_reasons'] = '["delta_row_missing"]'   # a ready pair without its union window row: say so, never a blank
    reference = product['role'] == 'reference'
    values['delta_orientation'] = 'target_minus_reference' if reference else 'model_minus_target'
    values['delta_model_minus_target_um2'] = negate_text(values['delta_um2']) if reference else values['delta_um2']
    status, counts, top = _window_cases_view(product['role'], windows, pair['pair_id'], pair.get('status'))
    return {'pair_id': pair['pair_id'], 'pair_status': pair.get('status'), 'pair_reasons': list(pair.get('reasons') or []),
            'direction': record.get('direction'), 'rank': candidate.get('rank'), 'search_scope': candidate.get('search_scope'),
            **values, 'window_cases_status': status, 'window_cases_counts': counts, 'window_cases_top': top}


def _cell_status(cell, pairs, match_given):
    """The decision state of one ROI x model cell; asset status is a separate field."""
    if pairs:
        return 'ready' if any(p['pair_status'] == 'ready' for p in pairs) else 'blocked'
    if cell['deferred'] > 0:
        return 'deferred'
    if cell['candidates'] > 0:
        return 'not_accepted' if cell['accepted'] == 0 else 'accepted_pair_missing'
    return 'no_candidate' if match_given and cell['_ranked'] else 'no_search'


def _build(decisions, reference, products, windows_by_key, candidates_dir, payload, files, target):
    rois = decisions.get('rois') or []
    match_given = bool(decisions.get('target_to_reference_match_given'))
    roi_rows = _export_rows(candidates_dir, payload, 'target_to_reference', files)
    attributes = _change_attributes(payload)
    linker = _Linker(candidates_dir, payload, [r['query_roi_id'] for r in rois], files)
    order = [reference]+sorted(k for k in products if k != reference)
    by_cell, unlinked = {}, []
    for key in order:
        product, windows = products[key], windows_by_key.get(key)
        if product['correspondence'] is None:
            continue
        if windows is None and product['correspondence']['pairs']:
            raise _bad('model_report_windows_missing', f'제품 {key!r} 의 쌍이 있는데 --windows {key}=<폴더> 가 없습니다')
        for pair in product['correspondence']['pairs']:
            record = product['sidecar']['pairs'][pair['pair_id']]
            candidate = (record.get('candidates') or [{}])[0]
            view = _pair_view(key, product, windows, pair, record, windows['rows'].get(pair['pair_id']) if windows else None)
            roi, reason = linker.link(record.get('direction'), record.get('candidates') or [])
            view['target_center_xy_um'] = list(pair['b']['center_xy_um']) if product['role'] == 'reference' else list(pair['a']['center_xy_um'])
            query_center = (roi_rows.get(roi) or {}).get('center_xy_um') if roi else None
            view['center_differs_from_query'] = (None if query_center is None or product['role'] != 'reference' else
                                                 not all(_same(x, y) for x, y in zip(view['target_center_xy_um'], query_center)))
            if roi is None:
                unlinked.append((key, view, reason))
            else:
                by_cell.setdefault((roi, key), []).append(view)
    rows = []
    add = rows.append

    def base(roi, key, cell, status, linked, reason):
        export = roi_rows.get(roi) or {}
        attr = attributes.get(roi) or {}
        return {'roi_id': roi or '', 'roi_linked': linked, 'unlinked_reason': reason, 'model': key,
                'model_role': products[key]['role'], 'cell_status': status, 'asset_status': cell.get('asset_status'),
                'n_candidates': cell.get('candidates'), 'n_accepted': cell.get('accepted'), 'n_rejected': cell.get('rejected'),
                'n_undecided': cell.get('undecided'), 'n_deferred': cell.get('deferred'),
                'unavailable_reasons': cell.get('unavailable') or {}, 'roi_center_xy_um': export.get('center_xy_um'),
                'roi_analysis_layer': export.get('analysis_layer'), 'score_um2': attr.get('score_um2'),
                'changed_case_count': attr.get('changed_case_count'), 'union_delta_um2': attr.get('union_delta_um2'),
                'change_kind_counts': attr.get('change_kind_counts'), 'cnt_change_um2_by_layer': attr.get('cnt_change_um2_by_layer')}
    empty_pair = {'pair_id': '', 'pair_status': None, 'pair_reasons': [], 'direction': None, 'rank': None, 'search_scope': None,
                  'center_differs_from_query': None, 'target_center_xy_um': None, 'a_value_um2': '', 'b_value_um2': '', 'delta_um2': '',
                  'delta_status': '', 'delta_reasons': '', 'delta_orientation': None, 'delta_model_minus_target_um2': '',
                  'window_cases_status': None, 'window_cases_counts': {name: None for name in CLASS_NAMES}, 'window_cases_top': {}}
    for roi_entry in rois:
        roi = roi_entry['query_roi_id']
        for key in order:
            cell = {'candidates': 0, 'accepted': 0, 'rejected': 0, 'undecided': 0, 'deferred': 0, 'unavailable': {}, 'asset_status': None}
            cell.update((roi_entry.get('products') or {}).get(key) or {})
            cell['_ranked'] = roi_entry.get('ranked')
            pairs = sorted(by_cell.get((roi, key), []), key=lambda v: (v['rank'] is None, v['rank'] if v['rank'] is not None else 0, v['pair_id']))
            status = _cell_status(cell, pairs, match_given)
            for view in pairs or [empty_pair]:
                add({**base(roi, key, cell, status, True, None), **view})
    for key, view, reason in sorted(unlinked, key=lambda item: (item[0], item[1]['pair_id'])):
        add({**base(None, key, {}, view['pair_status'], False, reason), **view})
    check_unique(rows)
    return rows, order


def check_unique(rows):
    """(ROI, model, pair_id) must not repeat."""
    seen = set()
    for row in rows:
        identity = (row['roi_id'], row['model'], row['pair_id'])
        if identity in seen:
            raise _bad('model_report_duplicate_key', f'ROI·모델·pair_id 가 겹칩니다: {identity}')
        seen.add(identity)


# ---- writers -----------------------------------------------------------------------------------------------------------------

def _plain(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return '' if value is None else value


def _csv_row(row):
    counts = row['window_cases_counts']
    flat = {**row, 'window_cases_both': counts['both'], 'window_cases_target_only': counts['target_only'],
            'window_cases_model_only': counts['model_only'], 'window_cases_unknown': counts['unknown'],
            'window_cases_top': row['window_cases_top']}
    return {name: _plain(flat.get(name)) for name in CSV_COLUMNS}


def _json_row(row):
    result = {name: row[name] for name in row if name not in ('window_cases_counts', 'window_cases_top')}
    for name in NUMERIC_FIELDS:
        result[name] = None if result[name] in ('', 'null') else result[name]
    result['window_cases'] = {'status': row['window_cases_status'], 'counts': row['window_cases_counts'], 'top': row['window_cases_top']}
    return result


def _summary(rows, order):
    summary = {}
    for key in order:
        mine = [row for row in rows if row['model'] == key and row['roi_linked']]
        cells, per_status = {}, {}
        for row in mine:
            cells.setdefault(row['roi_id'], row['cell_status'])
        for status in cells.values():
            per_status[status] = per_status.get(status, 0)+1
        pairs = [row for row in rows if row['model'] == key and row['pair_id']]
        summary[key] = {'role': next((r['model_role'] for r in rows if r['model'] == key), None),
                        'n_rois': len(cells), 'cells_by_status': dict(sorted(per_status.items())), 'n_pairs': len(pairs),
                        'n_pairs_delta_available': sum(1 for row in pairs if row['delta_status'] == 'available'),
                        'n_pairs_not_linked': sum(1 for row in pairs if not row['roi_linked'])}
    return summary


CSS = ('body{font-family:sans-serif;margin:16px;color:#222}table{border-collapse:collapse;font-size:12px}'
       'th,td{border:1px solid #bbb;padding:4px 6px;vertical-align:top;text-align:left}th{background:#eee}'
       '.note{color:#555;font-size:12px;max-width:980px}.mono{font-family:monospace;font-size:11px}'
       '.st{display:inline-block;padding:1px 6px;border-radius:8px;background:#e0e0e0;font-size:11px}'
       '.ready{background:#c8e6c9}.blocked{background:#ffcdd2}.deferred,.not_accepted,.no_search{background:#fff3cd}'
       '.pair{margin:3px 0;padding:2px 0;border-top:1px dotted #ccc}.pair:first-child{border-top:0}')


def _e(value):
    return html.escape('' if value is None else str(value))


def _unavailable_note(row):
    reasons = row['unavailable_reasons'] or {}
    if not reasons:
        return ''
    return f' · 쓸 수 없음 {sum(reasons.values())}({", ".join(f"{k} {v}" for k, v in sorted(reasons.items()))})'


def _cell_html(cell_rows):
    first = cell_rows[0]
    parts = [f'<span class="st {_e(first["cell_status"])}">{_e(first["cell_status"])}</span>']
    if first['asset_status'] and first['asset_status'] != 'configured':
        parts.append(f' <span class="st">자산 {_e(first["asset_status"])}</span>')
    if first['n_candidates'] is not None and not first['pair_id']:
        parts.append(f'<div class="note">후보 {_e(first["n_candidates"])} · accept {_e(first["n_accepted"])} · reject {_e(first["n_rejected"])}'
                     f' · 미결정 {_e(first["n_undecided"])}{_unavailable_note(first)}</div>')
    for row in cell_rows:
        if not row['pair_id']:
            continue
        counts = row['window_cases_counts']
        change = (f'case: {_e(row["window_cases_status"])}' if counts['both'] is None else
                  f'case 구성: 둘 다 {counts["both"]} · target만 {counts["target_only"]} · 모델만 {counts["model_only"]} · 알 수 없음 {counts["unknown"]}')
        names = ' / '.join(f'{name}: {", ".join(keys)}' for name, keys in row['window_cases_top'].items() if keys)
        parts.append(
            '<div class="pair"><span class="mono">{pid}</span> {state} r{rank} {scope}<br>'
            'Δ(모델−target) <b>{unified}</b> <span class="note">(원값 {raw} = {orient}, {dstatus}; a {a} / b {b} µm²)</span><br>'
            '<span class="note">{change}{names}{center}</span></div>'.format(
                pid=_e(row['pair_id']), state=_e(row['pair_status']), rank=_e(row['rank']), scope=_e(row['search_scope']),
                unified=_e(row['delta_model_minus_target_um2']), raw=_e(row['delta_um2']), orient=_e(row['delta_orientation']),
                dstatus=_e(row['delta_status'] or ('delta_row_missing' if 'delta_row_missing' in row['delta_reasons'] else '')), a=_e(row['a_value_um2']), b=_e(row['b_value_um2']), change=change,
                names=f'<br>{_e(names)}' if names else '',
                center=' · target 중심이 질의 중심과 다름' if row['center_differs_from_query'] else ''))
    return ''.join(parts)


def render_html(args, rows, order, summary, inputs, window_cases_files):
    head = ('<p class="note">행은 target 질의 ROI, 열은 모델(reference 포함)이다. 셀은 쌍 목록(rank 순, 자동 선택·집계 없음)이며 '
            f'Δ는 창 {args["window_um"]:g} µm·지름 {args["diameter_um"]:g} µm의 union CA 변화다. '
            '부호: 모델 쌍의 원값은 모델−target(b−a), reference 쌍의 원값은 target−reference이므로, 굵은 통일 값은 reference 쌍만 부호를 뒤집어 '
            '모두 모델−target으로 맞춘 표시다(수치는 local_delta 출력에서 복사했고 새로 계산하지 않았다). '
            'ROI 열의 점수·case 수·union 변화는 reference→target 변경 속성이고, 모델별 case 구성 차이는 관측 목록이다. '
            f'{_e(CAUSE_NOTE)}.</p>')
    summary_html = '<ul>'+''.join(
        f'<li><b>{_e(key)}</b> ({_e(info["role"])}): ROI {info["n_rois"]}개 · 셀 상태 {_e(json.dumps(info["cells_by_status"], ensure_ascii=False))}'
        f' · 쌍 {info["n_pairs"]} (Δ 사용 가능 {info["n_pairs_delta_available"]}, ROI 없음 {info["n_pairs_not_linked"]})</li>'
        for key, info in summary.items())+'</ul>'
    header = ''.join(f'<th>{_e(key)}</th>' for key in order)
    body, by_roi = [], {}
    for row in rows:
        by_roi.setdefault((row['roi_linked'], row['roi_id'] or row['pair_id']), []).append(row)
    for (linked, name), group in by_roi.items():
        if linked:
            sample = group[0]
            attr = (f'점수 {_e(sample["score_um2"])} µm² · case {_e(sample["changed_case_count"])} · union 변화 {_e(sample["union_delta_um2"])}<br>'
                    f'변경 종류 {_e(sample["change_kind_counts"])}<br>CNT 변경 {_e(sample["cnt_change_um2_by_layer"])}')
            label = (f'<span class="mono">{_e(sample["roi_id"])}</span><br>중심 {_e(sample["roi_center_xy_um"])} · 층 {_e(sample["roi_analysis_layer"])}'
                     f'<br><span class="note">{attr}</span>')
            cells = ''.join('<td>{}</td>'.format(_cell_html([r for r in group if r['model'] == key])) for key in order)
        else:
            sample = group[0]
            label = (f'<b>ROI 없음</b> <span class="note">({_e(sample["unlinked_reason"])})</span><br>'
                     f'target 중심 {_e(sample["target_center_xy_um"])}')
            cells = ''.join('<td>{}</td>'.format(_cell_html([r for r in group if r['model'] == key]) if sample['model'] == key else '') for key in order)
        body.append(f'<tr><td>{label}</td>{cells}</tr>')
    table = f'<table><tr><th>ROI (reference→target 변경 속성)</th>{header}</tr>{"".join(body)}</table>'
    links = ''.join(f'<li class="mono">{_e(key)}: {_e(path)}</li>' for key, path in window_cases_files.items())
    links_html = f'<h2>모델별 case 구성 전체 목록</h2><ul>{links}</ul>' if links else ''
    sha_rows = ''.join(f'<tr><td>{_e(label)}</td><td class="mono">{_e(info["path"])}</td><td class="mono">{_e(info["sha256"])}</td></tr>'
                       for label, info in sorted(inputs.items()))
    return ('<!DOCTYPE html>\n<html lang="ko"><head><meta charset="utf-8"><title>모델별 변경·불량 변화 한 장</title>'
            f'<style>{CSS}</style></head><body><h1>모델별 변경·불량 변화 (model_report_v1)</h1>{head}<h2>모델별 요약</h2>{summary_html}'
            f'<h2>ROI × 모델</h2>{table}{links_html}<h2>입력 파일 sha256</h2><table><tr><th>입력</th><th>경로</th><th>sha256</th></tr>{sha_rows}</table>'
            '</body></html>\n')


def _write(path, text):
    with open(str(path), 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(text)


def run_model_report(candidates_dir, confirm_dir, windows, *, diameter_um, window_um, out):
    """model_report.html/.csv/.json and execution.json into the new folder `out`. Any refusal happens before a file is written."""
    _check_numbers(diameter_um, window_um)
    windows = dict(windows)
    out = Path(out)
    _check_out(out, [candidates_dir, confirm_dir, *windows.values()])
    candidates = Path(candidates_dir)
    files = _Files()
    payload = files.json(candidates/'candidate_queries_v1.json', 'candidates candidate_queries_v1.json', 'candidate_queries_v1')
    decisions, index, reference, products = _load_confirm(confirm_dir, files.sha('candidates candidate_queries_v1.json'), files)
    target = decisions.get('target_product_id')
    for key in windows:
        if key not in products:
            raise _bad('model_report_unknown_windows_key', f'--windows 의 키 {key!r} 가 reference({reference!r}) 도 confirm 의 모델도 아닙니다: {sorted(products)}')
    loaded = {key: _load_windows(key, folder, products[key], reference, target, float(window_um), float(diameter_um), files)
              for key, folder in sorted(windows.items())}
    hashes = {key: item['target_hash'] for key, item in loaded.items()}
    if len(set(hashes.values())) > 1:
        raise _bad('model_report_target_inputs_differ', f'windows 폴더들의 target 쪽 입력 해시가 다릅니다: {hashes}')
    rows, order = _build(decisions, reference, products, loaded, candidates, payload, files, target)
    summary = _summary(rows, order)
    args = {'diameter_um': float(diameter_um), 'window_um': float(window_um)}
    case_files = {key: str(Path(windows[key])/'window_cases.json') for key, item in loaded.items() if item['cases'] is not None}
    document = render_html(args, rows, order, summary, files.records, case_files)
    report = {'schema_version': SCHEMA_VERSION, 'target_product_id': target, 'reference_product_id': reference, 'columns': order,
              'args': args, 'sign_rule': SIGN_RULE, 'semantics': CAUSE_NOTE, 'inputs': dict(sorted(files.records.items())),
              'summary': summary, 'rows': [_json_row(row) for row in rows]}
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f'.{out.name}.staging-', dir=str(out.parent)))
    try:
        _write(stage/HTML_NAME, document)
        with open(str(stage/CSV_NAME), 'w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, CSV_COLUMNS, lineterminator='\n')
            writer.writeheader()
            for row in rows:
                writer.writerow(_csv_row(row))
        _write(stage/JSON_NAME, json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
        names = [CSV_NAME, EXECUTION_NAME, HTML_NAME, JSON_NAME]
        result = {'status': 'available', 'reasons': [], 'outputs': [str(out/name) for name in names], 'exit_code': 0, 'n_rows': len(rows)}
        _write(stage/EXECUTION_NAME, json.dumps({'command': 'model-report', **result}, ensure_ascii=False, indent=2)+'\n')
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(str(stage), 0o777 & ~umask)
        os.rename(str(stage), str(out))
    except BaseException:
        shutil.rmtree(str(stage), ignore_errors=True)
        raise
    return result
