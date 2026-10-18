"""roi_correspondence_v1: corresponding positions between two products.

Producers are manual labels, template matching and DL2 search. Consumers are
compare-windows (local ΔCA), review_roi_v1 manual requests, DL2 roi_relations_v1
and DL1 relation rows. Coordinates stay in each product's own frame; nothing is
registered, inferred or rescaled, and DL scores are display context only.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys

from .numeric_schema import (
    DIAMETERS_UM, Endpoint, Frame, NumericContractError, NumericDataset, finite, text,
)

SCHEMA_VERSION = 'roi_correspondence_v1'
PRODUCER_KINDS = ('manual', 'template_match', 'dl2_search')
SIDE_REQUIRED = ('product_id', 'design_version', 'frame_id', 'center_xy_um', 'analysis_layer')
SIDE_OPTIONAL = ('endpoints', 'failure_type', 'roi_id', 'source_id', 'process_step_id',
                 'exposure_id', 'unit_id')
SIDE_FILTERS = ('source_id', 'process_step_id', 'exposure_id', 'unit_id')
PAIR_FIELDS = {'pair_id', 'a', 'b', 'repeats_per_unit', 'source', 'status', 'reasons'}
SOURCE_REQUIRED = {'manual': ('labelled_by', 'labelled_at'),
                   'template_match': ('method', 'version'),
                   'dl2_search': ('score', 'rank', 'checkpoint_fingerprint', 'index_fingerprint')}
FAILURE_TYPES = ('lateral_short', 'vertical_short')


def _json_load(path: Path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise NumericContractError('duplicate_correspondence_key', key)
            result[key] = value
        return result

    def constant(value):
        raise NumericContractError('nonfinite_correspondence_number', value)
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique, parse_constant=constant)
    except (OSError, ValueError) as exc:
        if isinstance(exc, NumericContractError):
            raise
        raise NumericContractError('invalid_correspondence_file', str(exc)) from exc


def _endpoints(value, where):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 2 or any(not isinstance(e, dict) for e in value):
        raise NumericContractError('invalid_correspondence_endpoints', where)
    result = []
    for entry in value:
        if set(entry) != {'electrode_id', 'role', 'actual_layer'}:
            raise NumericContractError('invalid_correspondence_endpoints', where)
        try:
            result.append(Endpoint(entry['electrode_id'], entry['role'], entry['actual_layer']))
        except NumericContractError as exc:
            raise NumericContractError('invalid_correspondence_endpoints', where) from exc
    if result[0].electrode_id == result[1].electrode_id:
        raise NumericContractError('invalid_correspondence_endpoints', where + ': two distinct electrodes required')
    return tuple(result)


def _side(raw, where):
    if not isinstance(raw, dict):
        raise NumericContractError('invalid_correspondence_side', where)
    unknown = set(raw) - set(SIDE_REQUIRED) - set(SIDE_OPTIONAL)
    if unknown or any(field not in raw for field in SIDE_REQUIRED):
        raise NumericContractError('invalid_correspondence_side', where + ': ' + ', '.join(sorted(unknown)) if unknown else where)
    for field in ('product_id', 'design_version', 'frame_id', 'analysis_layer'):
        text(raw[field], where + '.' + field)
    center = raw['center_xy_um']
    if not isinstance(center, (list, tuple)) or len(center) != 2:
        raise NumericContractError('invalid_correspondence_center', where)
    center = tuple(finite(v, where + '.center_xy_um') for v in center)
    failure_type = raw.get('failure_type')
    if failure_type is not None and failure_type not in FAILURE_TYPES:
        raise NumericContractError('invalid_correspondence_failure_type', where)
    roi_id = raw.get('roi_id')
    if roi_id is not None:
        text(roi_id, where + '.roi_id')
    filters = {}
    for field in SIDE_FILTERS:
        if raw.get(field) is not None:
            filters[field] = text(raw[field], where + '.' + field)
    endpoints = _endpoints(raw.get('endpoints'), where)
    return {'product_id': raw['product_id'], 'design_version': raw['design_version'], 'frame_id': raw['frame_id'],
            'center_xy_um': list(center), 'analysis_layer': raw['analysis_layer'],
            'endpoints': None if endpoints is None else [asdict(e) for e in endpoints],
            'failure_type': failure_type, 'roi_id': roi_id, **filters}


def _source(raw, where):
    if not isinstance(raw, dict) or raw.get('kind') not in PRODUCER_KINDS:
        raise NumericContractError('invalid_correspondence_source', where)
    kind = raw['kind']
    for field in SOURCE_REQUIRED[kind]:
        if field not in raw or raw[field] is None:
            raise NumericContractError('invalid_correspondence_source', f'{where}: {kind} requires {field}')
    if kind == 'dl2_search':
        finite(raw['score'], where + '.score')
        if isinstance(raw['rank'], bool) or not isinstance(raw['rank'], int) or raw['rank'] < 1:
            raise NumericContractError('invalid_correspondence_source', where + ': rank')
        for field in ('checkpoint_fingerprint', 'index_fingerprint'):
            text(raw[field], where + '.' + field)
    else:
        for field in SOURCE_REQUIRED[kind]:
            text(raw[field], where + '.' + field)
    return copy.deepcopy(raw)


def _repeats(raw, where):
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {'a', 'b'}:
        raise NumericContractError('invalid_correspondence_repeats', where)
    for side in ('a', 'b'):
        value = raw[side]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise NumericContractError('invalid_correspondence_repeats', where)
    return {'a': raw['a'], 'b': raw['b']}


def validate_correspondence(payload) -> dict:
    """Return a normalised deep copy; raise NumericContractError on contract violations."""
    if not isinstance(payload, dict) or payload.get('schema_version') != SCHEMA_VERSION:
        raise NumericContractError('unsupported_correspondence_schema')
    allowed = {'schema_version', 'producer', 'pairs', 'notes', 'warnings'}  # warnings are recomputed
    if set(payload) - allowed or not isinstance(payload.get('pairs'), list):
        raise NumericContractError('invalid_correspondence_file')
    producer = payload.get('producer')
    if not isinstance(producer, dict) or producer.get('kind') not in PRODUCER_KINDS:
        raise NumericContractError('invalid_correspondence_producer')
    pairs, seen, centres, warnings = [], set(), {}, []
    for index, raw in enumerate(payload['pairs']):
        where = f'pairs[{index}]'
        if not isinstance(raw, dict) or set(raw) - PAIR_FIELDS or not {'pair_id', 'a', 'b', 'source', 'status'} <= set(raw):
            raise NumericContractError('invalid_correspondence_pair', where)
        pair_id = text(raw['pair_id'], where + '.pair_id')
        if pair_id in seen:
            raise NumericContractError('duplicate_correspondence_pair_id', pair_id)
        seen.add(pair_id)
        status = raw['status']
        reasons = raw.get('reasons', [])
        if status not in ('ready', 'blocked') or not isinstance(reasons, list) or any(not isinstance(r, str) or not r for r in reasons):
            raise NumericContractError('invalid_correspondence_status', pair_id)
        if status == 'blocked' and not reasons:
            raise NumericContractError('invalid_correspondence_status', pair_id + ': blocked requires reasons')
        pair = {'pair_id': pair_id, 'a': _side(raw['a'], where + '.a'), 'b': _side(raw['b'], where + '.b'),
                'repeats_per_unit': _repeats(raw.get('repeats_per_unit'), where),
                'source': _source(raw['source'], where + '.source'), 'status': status, 'reasons': sorted(set(reasons))}
        key = tuple((pair[s]['product_id'], pair[s]['design_version'], pair[s]['frame_id'], tuple(pair[s]['center_xy_um']),
                     pair[s]['analysis_layer']) for s in ('a', 'b'))
        if key in centres:
            warnings.append({'pair_id': pair_id, 'duplicate_of': centres[key], 'warning': 'duplicate_centres'})
        else:
            centres[key] = pair_id
        pairs.append(pair)
    return {'schema_version': SCHEMA_VERSION, 'producer': copy.deepcopy(producer), 'pairs': pairs,
            'notes': copy.deepcopy(payload.get('notes')), 'warnings': warnings}


def load_correspondence(path) -> dict:
    return validate_correspondence(_json_load(Path(path)))


def write_correspondence(path, payload) -> str:
    payload = validate_correspondence({k: v for k, v in payload.items() if k != 'warnings'})
    payload.pop('warnings', None)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return str(path)


@dataclass(frozen=True)
class BoundSide:
    product_id: str
    design_version: str
    frame: Frame
    center_xy_um: tuple[float, float]
    analysis_layer: str
    endpoints: tuple[Endpoint, Endpoint] | None
    failure_type: str | None
    roi_id: str | None
    filters: dict

    def to_dict(self):
        return {'product_id': self.product_id, 'design_version': self.design_version,
                'frame_id': self.frame.frame_id, 'center_xy_um': list(self.center_xy_um),
                'analysis_layer': self.analysis_layer,
                'endpoints': None if self.endpoints is None else [asdict(e) for e in self.endpoints],
                'failure_type': self.failure_type, 'roi_id': self.roi_id, 'filters': dict(self.filters)}


@dataclass(frozen=True)
class BoundPair:
    pair_id: str
    a: BoundSide
    b: BoundSide
    repeats_per_unit: dict | None
    source: dict
    status: str
    reasons: tuple[str, ...]

    def to_dict(self):
        return {'pair_id': self.pair_id, 'a': self.a.to_dict(), 'b': self.b.to_dict(),
                'repeats_per_unit': self.repeats_per_unit, 'source': copy.deepcopy(self.source),
                'status': self.status, 'reasons': list(self.reasons)}


def _dataset_product(dataset: NumericDataset, which: str) -> str:
    products = {case.identity.product_id for case in dataset.cases}
    if len(products) != 1:
        raise NumericContractError('single_declared_product_required', which)
    return next(iter(products))


def _bind_side(raw, dataset: NumericDataset, which: str):
    product = _dataset_product(dataset, which)
    if raw['product_id'] != product:
        raise NumericContractError('correspondence_product_mismatch', f"{which}: {raw['product_id']} != {product}")
    designs = {case.identity.design_version for case in dataset.cases}
    if raw['design_version'] not in designs:
        raise NumericContractError('correspondence_design_mismatch', f"{which}: {raw['design_version']}")
    frame = dataset.frames.get(raw['frame_id'])
    if frame is None:
        raise NumericContractError('correspondence_frame_mismatch', f"{which}: {raw['frame_id']}")
    reasons = []
    x, y = raw['center_xy_um']
    x0, y0, x1, y1 = frame.bbox_um
    if not (x0 <= x < x1 and y0 <= y < y1):
        reasons.append(f'position_outside_source_frame_{which}')
    layers = {case.analysis_layer for case in dataset.cases if case.identity.frame_id == raw['frame_id']}
    if raw['analysis_layer'] not in layers:
        reasons.append(f'analysis_layer_not_in_source_{which}')
    endpoints = None if raw.get('endpoints') is None else tuple(Endpoint(**e) for e in raw['endpoints'])
    filters = {k: raw[k] for k in SIDE_FILTERS if raw.get(k) is not None}
    side = BoundSide(raw['product_id'], raw['design_version'], frame, (x, y), raw['analysis_layer'],
                     endpoints, raw.get('failure_type'), raw.get('roi_id'), filters)
    return side, reasons


def bind_pairs(payload, reference: NumericDataset, target: NumericDataset) -> list[BoundPair]:
    """Side a binds to the reference dataset and side b to the target dataset.

    Product, design and frame mismatches reject the file; positions outside the
    frame or layers absent from the source block only that pair.
    """
    payload = validate_correspondence(payload)
    bound = []
    for pair in payload['pairs']:
        a, reasons_a = _bind_side(pair['a'], reference, 'a')
        b, reasons_b = _bind_side(pair['b'], target, 'b')
        reasons = sorted(set(pair['reasons']) | set(reasons_a) | set(reasons_b))
        status = 'blocked' if reasons else 'ready'
        bound.append(BoundPair(pair['pair_id'], a, b, pair['repeats_per_unit'], pair['source'], status, tuple(reasons)))
    return bound


# ---- converters -------------------------------------------------------------

def _manual_request(side):
    request = {'product_id': side['product_id'], 'design_version': side['design_version'],
               'frame_id': side['frame_id'], 'center_xy_um': list(side['center_xy_um'])}
    for field in SIDE_FILTERS:
        if side.get(field) is not None and field != 'unit_id':
            request[field] = side[field]
    if side.get('endpoints') is not None:
        request['endpoints'] = copy.deepcopy(side['endpoints'])
    if side.get('failure_type') is not None:
        request['failure_type'] = side['failure_type']
    return request


def to_review_roi_config(cfg, payload, side: str) -> tuple[dict, dict]:
    """Derive a numeric_config_v1 whose map.input is the chosen side and whose
    review_rois.manual lists the pair positions. Relative paths become absolute."""
    if side not in ('reference', 'target'):
        raise NumericContractError('invalid_correspondence_side_selection', side)
    payload = validate_correspondence(payload)
    key = 'a' if side == 'reference' else 'b'
    if not isinstance(cfg, dict) or not isinstance(cfg.get(side), dict):
        raise NumericContractError('numeric_input_required', side)
    base = cfg.get('_config_dir')
    derived = {k: copy.deepcopy(v) for k, v in cfg.items() if k != '_config_dir'}

    def absolute(section):
        if isinstance(section, dict) and isinstance(section.get('data_dir'), str) and base is not None:
            path = Path(section['data_dir'])
            section['data_dir'] = str(path if path.is_absolute() else (Path(base) / path).resolve())
        return section
    for name in ('reference', 'target'):
        if isinstance(derived.get(name), dict):
            absolute(derived[name])
    existing_map = derived.get('map') if isinstance(derived.get('map'), dict) else {}
    derived['map'] = {'input': copy.deepcopy(derived[side]), 'diameter_um': existing_map.get('diameter_um', 5)}
    requests, missing_context = [], []
    for pair in payload['pairs']:
        if pair['status'] != 'ready':
            continue
        requests.append(_manual_request(pair[key]))
        if pair[key].get('endpoints') is None:
            missing_context.append(pair['pair_id'])
    derived['review_rois'] = {'manual': requests}
    report = {'side': side, 'n_requests': len(requests), 'pairs_without_endpoints': missing_context,
              'note': 'endpoints 없는 요청은 export-review-rois에서 case_context_required로 blocked가 된다'}
    return derived, report


def _export_rows(export):
    if not isinstance(export, dict) or export.get('schema_version') != 'review_roi_v1' or not isinstance(export.get('rois'), list):
        raise NumericContractError('unsupported_review_roi_export')
    return export['rois']


def _match_roi(side, rows):
    matches = []
    for row in rows:
        if row.get('source_kind') != 'manual' or row.get('center_xy_um') is None:
            continue
        identity = row.get('source_identity') or {}
        if (identity.get('product_id'), identity.get('design_version'), identity.get('frame_id')) != (
                side['product_id'], side['design_version'], side['frame_id']):
            continue
        if [float(v) for v in row['center_xy_um']] != [float(v) for v in side['center_xy_um']]:
            continue
        if row.get('analysis_layer') is not None and row['analysis_layer'] != side['analysis_layer']:
            continue
        if side.get('endpoints') is not None and row.get('endpoints') != side['endpoints']:
            continue
        if side.get('failure_type') is not None and row.get('failure_type') != side['failure_type']:
            continue
        if any(side.get(f) is not None and identity.get(f) != side[f] for f in SIDE_FILTERS if f != 'unit_id'):
            continue
        matches.append(row)
    return matches


def attach_roi_ids(payload, export_a, export_b) -> tuple[dict, dict]:
    """Fill roi_id from review_roi_v1 exports by identity, centre and context; never by shape."""
    payload = validate_correspondence(payload)
    rows = {'a': _export_rows(export_a), 'b': _export_rows(export_b)}
    report = {'attached': 0, 'not_found': [], 'ambiguous': [], 'blocked_roi': []}
    for pair in payload['pairs']:
        for key in ('a', 'b'):
            side = pair[key]
            matches = _match_roi(side, rows[key])
            if len(matches) == 1:
                row = matches[0]
                if row.get('status') != 'ready':
                    report['blocked_roi'].append({'pair_id': pair['pair_id'], 'side': key, 'roi_id': row.get('roi_id'),
                                                  'reasons': row.get('reasons', [])})
                side['roi_id'] = row['roi_id']
                report['attached'] += 1
            elif matches:
                report['ambiguous'].append({'pair_id': pair['pair_id'], 'side': key})
            else:
                report['not_found'].append({'pair_id': pair['pair_id'], 'side': key})
    return payload, report


def to_dl2_relations(payload) -> tuple[dict, dict]:
    """roi_relations_v1 rows for pairs whose both sides carry roi_id; always 'similar'."""
    payload = validate_correspondence(payload)
    relations, seen, skipped = [], set(), []
    for pair in payload['pairs']:
        a, b = pair['a'].get('roi_id'), pair['b'].get('roi_id')
        if pair['status'] != 'ready' or a is None or b is None:
            skipped.append({'pair_id': pair['pair_id'], 'reason': 'blocked_pair' if pair['status'] != 'ready' else 'roi_id_missing'})
            continue
        if a == b:
            skipped.append({'pair_id': pair['pair_id'], 'reason': 'self_relation_not_allowed'})
            continue
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        seen.add(key)
        relations.append({'a': a, 'b': b, 'relation': 'similar'})
    return {'schema_version': 'roi_relations_v1', 'relations': relations}, {'n_relations': len(relations), 'skipped': skipped}


def to_dl1_relations(payload) -> tuple[list[tuple[str, str, str]], dict]:
    """DL1 header-less rows product_a,product_b,similar for distinct product pairs."""
    payload = validate_correspondence(payload)
    rows, seen, skipped = [], set(), []
    for pair in payload['pairs']:
        a, b = pair['a']['product_id'], pair['b']['product_id']
        if pair['status'] != 'ready':
            skipped.append({'pair_id': pair['pair_id'], 'reason': 'blocked_pair'})
            continue
        if a == b:
            skipped.append({'pair_id': pair['pair_id'], 'reason': 'same_product'})
            continue
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        seen.add(key)
        rows.append((key[0], key[1], 'similar'))
    return rows, {'n_rows': len(rows), 'skipped': skipped}


def _side_from_roi_row(row, where):
    if row.get('status') != 'ready':
        raise NumericContractError('correspondence_source_roi_blocked', f"{where}: {row.get('roi_id')}")
    identity = row['source_identity']
    side = {'product_id': identity['product_id'], 'design_version': identity['design_version'],
            'frame_id': identity['frame_id'], 'center_xy_um': list(row['center_xy_um']),
            'analysis_layer': row['analysis_layer'], 'endpoints': copy.deepcopy(row.get('endpoints')),
            'failure_type': row.get('failure_type'), 'roi_id': row['roi_id']}
    for field in ('source_id', 'process_step_id', 'exposure_id'):
        if identity.get(field) is not None:
            side[field] = identity[field]
    return side


def from_dl2_search(results, index, query_export, index_export, *, include_supplementary=False,
                    producer_commit=None, created_at=None) -> dict:
    """Build pairs from DL2 search JSON files; ROI context comes from the exports, never from scores."""
    if not isinstance(index, dict) or index.get('schema_version') != 'psdl2_index_v2':
        raise NumericContractError('unsupported_dl2_index')
    for field in ('checkpoint_fingerprint', 'view_fingerprint'):
        text(index.get(field), 'index.' + field)
    query_rows = {row['roi_id']: row for row in _export_rows(query_export)}
    index_rows = {row['roi_id']: row for row in _export_rows(index_export)}
    pairs, seen = [], set()
    for result in results:
        if not isinstance(result, dict) or 'query' not in result or 'primary' not in result:
            raise NumericContractError('unsupported_dl2_search_result')
        query_id = result['query'].get('roi_id')
        if query_id not in query_rows:
            raise NumericContractError('dl2_query_roi_not_in_export', str(query_id))
        candidates = [('primary', row) for row in result.get('primary', [])]
        if include_supplementary:
            candidates += [('supplementary', row) for row in result.get('supplementary', [])]
        for scope, candidate in candidates:
            rank = int(candidate.get('rank', 0)) if isinstance(candidate.get('rank'), int) else None
            if rank is None:
                rank = [c for _, c in candidates if _ == scope].index(candidate) + 1
            cid = candidate.get('roi_id')
            if cid not in index_rows:
                raise NumericContractError('dl2_candidate_roi_not_in_export', str(cid))
            pair_id = f'dl2:{query_id}:{cid}'
            if pair_id in seen:
                continue
            seen.add(pair_id)
            pairs.append({'pair_id': pair_id, 'a': _side_from_roi_row(query_rows[query_id], 'query'),
                          'b': _side_from_roi_row(index_rows[cid], 'candidate'), 'repeats_per_unit': None,
                          'source': {'kind': 'dl2_search', 'score': float(candidate['score']), 'rank': rank,
                                     'scope': scope, 'checkpoint_fingerprint': index['checkpoint_fingerprint'],
                                     'index_fingerprint': index['view_fingerprint'],
                                     'index_schema_version': index['schema_version'],
                                     'same_physical_case': candidate.get('same_physical_case')},
                          'status': 'ready', 'reasons': []})
    return validate_correspondence({'schema_version': SCHEMA_VERSION,
                                    'producer': {'kind': 'dl2_search', 'version': '1', 'commit': producer_commit,
                                                 'created_at': created_at},
                                    'pairs': pairs})


# ---- CLI --------------------------------------------------------------------

def _print(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise NumericContractError('invalid_json_input', f'{path}: {exc}') from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='python -m simcompare.correspondence',
                                     description='roi_correspondence_v1 검증과 변환. 측정·학습은 하지 않는다.')
    sub = parser.add_subparsers(dest='command', required=True)
    validate = sub.add_parser('validate', help='형식 검증과 경고 출력')
    validate.add_argument('--correspondence', required=True)
    review = sub.add_parser('to-review-rois', help='한쪽 제품의 review_rois.manual 요청을 가진 numeric_config_v1 생성')
    review.add_argument('--config', required=True)
    review.add_argument('--correspondence', required=True)
    review.add_argument('--side', required=True, choices=('reference', 'target'))
    review.add_argument('--out', required=True, help='생성할 YAML 경로')
    attach = sub.add_parser('attach-roi-ids', help='review_roi_v1 export에서 roi_id를 채움')
    attach.add_argument('--correspondence', required=True)
    attach.add_argument('--export-a', required=True)
    attach.add_argument('--export-b', required=True)
    attach.add_argument('--out', required=True)
    dl2 = sub.add_parser('to-dl2-relations', help='roi_relations_v1 생성 (similar만)')
    dl2.add_argument('--correspondence', required=True)
    dl2.add_argument('--out', required=True)
    dl1 = sub.add_parser('to-dl1-relations', help='DL1 relations CSV 생성 (product_a,product_b,similar)')
    dl1.add_argument('--correspondence', required=True)
    dl1.add_argument('--out', required=True)
    search = sub.add_parser('from-dl2-search', help='DL2 search JSON → roi_correspondence_v1')
    search.add_argument('--search', required=True, nargs='+', help='psdl2.search 출력 JSON들')
    search.add_argument('--index', required=True, help='psdl2.index 출력 JSON')
    search.add_argument('--query-export', required=True, help='질의 제품의 review_rois.json')
    search.add_argument('--index-export', required=True, help='색인 제품의 review_rois.json')
    search.add_argument('--include-supplementary', action='store_true')
    search.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'validate':
            payload = load_correspondence(args.correspondence)
            _print({'status': 'available', 'n_pairs': len(payload['pairs']),
                    'n_ready': sum(p['status'] == 'ready' for p in payload['pairs']), 'warnings': payload['warnings']})
            return 0
        if args.command == 'to-review-rois':
            import yaml
            from .pipeline import load_numeric_config
            cfg = load_numeric_config(args.config)
            derived, report = to_review_roi_config(cfg, load_correspondence(args.correspondence), args.side)
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(yaml.safe_dump(derived, sort_keys=False, allow_unicode=True), encoding='utf-8')
            _print({'status': 'available', 'outputs': [str(out)], **report})
            return 0
        if args.command == 'attach-roi-ids':
            payload, report = attach_roi_ids(load_correspondence(args.correspondence),
                                             _read_json(args.export_a), _read_json(args.export_b))
            path = write_correspondence(args.out, payload)
            complete = not report['not_found'] and not report['ambiguous']
            _print({'status': 'available' if complete else 'partial', 'outputs': [path], **report})
            return 0 if complete else 3
        if args.command == 'to-dl2-relations':
            relations, report = to_dl2_relations(load_correspondence(args.correspondence))
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(relations, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            _print({'status': 'available' if not report['skipped'] else 'partial', 'outputs': [str(out)], **report})
            return 0 if not report['skipped'] else 3
        if args.command == 'to-dl1-relations':
            rows, report = to_dl1_relations(load_correspondence(args.correspondence))
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(''.join(','.join(row) + '\n' for row in rows), encoding='utf-8')
            _print({'status': 'available' if not report['skipped'] else 'partial', 'outputs': [str(out)], **report})
            return 0 if not report['skipped'] else 3
        if args.command == 'from-dl2-search':
            payload = from_dl2_search([_read_json(p) for p in args.search], _read_json(args.index),
                                      _read_json(args.query_export), _read_json(args.index_export),
                                      include_supplementary=args.include_supplementary)
            path = write_correspondence(args.out, payload)
            _print({'status': 'available', 'outputs': [path], 'n_pairs': len(payload['pairs'])})
            return 0
        raise NumericContractError('unknown_command', args.command)
    except (NumericContractError, OSError, ValueError, TypeError, KeyError) as exc:
        print(f'오류: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
