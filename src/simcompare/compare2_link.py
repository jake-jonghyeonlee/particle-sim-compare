"""compare2_link: load and check the round 4/5/6 outputs and DL1 search files against a compare2 result.

Pure functions, no files written, no numbers computed. ``build_link`` returns one JSON-able dict that holds
the ROIs of the candidates-change round (round 4), the pairs of its direct file and of the confirm-pairs file
(round 5), the named_pair window rows of compare-windows (round 6) and the DL1 search results, each mapped to the
compare2 conditions. Delta formulas, classification and rendering belong to later tasks.

Errors stop the link (NumericContractError); warnings travel in the result and the caller decides the exit code.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from .compare2_inputs import size_key
from .numeric_schema import NumericContractError

SCHEMA_VERSION = 'compare2_link_v1'
ROLES = ('reference', 'target')
CANDIDATES_FILE = 'candidate_queries_v1.json'
DIRECT_CORRESPONDENCE = 'direct/roi_correspondence.json'
DIRECT_SIDECAR = 'direct/direct_pairs_sidecar.json'
CONFIRM_CORRESPONDENCE = 'roi_correspondence.json'
CONFIRM_SIDECAR = 'confirmed_pairs_sidecar.json'
WINDOWS_JSON = 'local_delta.json'
WINDOWS_ROWS = 'local_delta_rows.csv'
SIDECAR_ATTRIBUTES = ('region_id', 'change_kind', 'residual_um2', 'area_reference_um2', 'area_target_um2', 'present_in',
                      'layer_changed', 'flags', 'score_um2')
NAMED_VALUES = ('a_value_um2', 'b_value_um2', 'a_status', 'b_status')
WINDOW_TOLERANCE = 1e-9


def _error(code, detail=''):
    return NumericContractError(code, detail)


class _Files:
    """Reads each file once as bytes; keeps name -> sha256 for the inputs record."""

    def __init__(self, root):
        self.root = Path(root)
        self.sha = {}

    def raw(self, name):
        path = self.root / name
        if not path.is_file():
            raise _error('compare2_link_input_missing', str(path))
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise _error('compare2_link_input_missing', f'{path}: {exc}')
        self.sha[name] = hashlib.sha256(data).hexdigest()
        return data

    def json(self, name, schema):
        data = self.raw(name)
        try:
            doc = json.loads(data.decode('utf-8'))
        except ValueError as exc:
            raise _error('compare2_link_schema_mismatch', f'{self.root / name}: not JSON ({exc})')
        if not isinstance(doc, dict) or doc.get('schema_version') != schema:
            got = doc.get('schema_version') if isinstance(doc, dict) else None
            raise _error('compare2_link_schema_mismatch', f'{self.root / name}: schema_version {got!r}, expected {schema!r}')
        return doc

    def rows(self, name):
        data = self.raw(name)
        try:
            text = data.decode('utf-8-sig')
        except ValueError as exc:
            raise _error('compare2_link_schema_mismatch', f'{self.root / name}: not UTF-8 ({exc})')
        return list(csv.DictReader(text.splitlines()))

    def record(self, kind):
        return {'kind': kind, 'path': str(self.root), 'files': dict(sorted(self.sha.items()))}


def _mismatch(check, detail=''):
    return _error('compare2_link_input_mismatch', f'{check}: {detail}' if detail else check)


# ---- compare2 document ------------------------------------------------------------------------------------------------------

def _manifest_hash(compare2_doc, role):
    manifests = (compare2_doc.get('inputs') or {}).get('manifests') or {}
    value = (manifests.get(role) or {}).get('input_hash')
    if not isinstance(value, str) or not value:
        raise _mismatch('compare2_input_hash_missing', role)
    return value


def _known_product_ids(compare2_doc, role):
    """Product ids the compare2 document names for a role (none in compare2_v1 today; read when present)."""
    found = set()
    for container in ((compare2_doc.get('products') or {}).get(role), ((compare2_doc.get('inputs') or {}).get('manifests') or {}).get(role)):
        if isinstance(container, dict):
            for key in ('product_id', 'dl1_model'):
                if isinstance(container.get(key), str) and container[key]:
                    found.add(container[key])
    return found


def _condition_key(layer, electrodes):
    return (layer, frozenset(electrodes))


def _condition_index(compare2_doc):
    """role -> {(layer, frozenset(electrode ids)): [condition_id, ...]} over compare2 sides[role].conditions."""
    index = {}
    for role in ROLES:
        table = {}
        conditions = (((compare2_doc.get('sides') or {}).get(role)) or {}).get('conditions') or []
        for cond in conditions:
            electrodes = cond.get('electrodes')
            if not isinstance(electrodes, (list, tuple)) or len(electrodes) != 2 or not isinstance(cond.get('layer'), str):
                continue
            table.setdefault(_condition_key(cond['layer'], electrodes), []).append(cond['condition_id'])
        index[role] = {key: sorted(ids) for key, ids in table.items()}
    return index


def _key_text(layer, electrodes):
    return f'{layer}|{"+".join(sorted(electrodes))}'


def _map_condition(index, role, layer, electrodes, warnings):
    """-> {'condition_id': id|None, 'reason': None|str}; exact string match, electrode order ignored."""
    if layer is None or not electrodes or len(electrodes) != 2:
        return {'condition_id': None, 'reason': 'condition_key_unavailable'}
    ids = index[role].get(_condition_key(layer, electrodes), [])
    text = _key_text(layer, electrodes)
    if len(ids) == 1:
        return {'condition_id': ids[0], 'reason': None}
    if len(ids) > 1:
        warnings.add(f'ambiguous_condition_key:{role}:{text}', ','.join(ids))
        return {'condition_id': None, 'reason': 'ambiguous_condition_key'}
    warnings.add(f'unmapped_condition:{role}:{text}', 'no compare2 condition with this analysis layer and electrode pair')
    return {'condition_id': None, 'reason': 'unmapped_condition'}


class _Warnings:
    def __init__(self):
        self.items = {}

    def add(self, code, detail=''):
        self.items[(code, detail)] = {'code': code, 'detail': detail}

    def result(self):
        return [self.items[key] for key in sorted(self.items)]


# ---- round 4: ROIs ----------------------------------------------------------------------------------------------------------

def _parse_case_key(case_key, layer=None):
    """'failure|e1|l1|e2|l2[|analysis_layer]' -> dict; unparsable keys give None fields."""
    parts = case_key.split('|') if isinstance(case_key, str) else []
    if len(parts) not in (5, 6) or not all(parts):
        return {'failure': None, 'electrodes': None, 'endpoint_layers': None, 'analysis_layer': layer}
    endpoints = sorted([(parts[1], parts[2]), (parts[3], parts[4])])
    return {'failure': parts[0], 'electrodes': [endpoints[0][0], endpoints[1][0]],
            'endpoint_layers': [endpoints[0][1], endpoints[1][1]],
            'analysis_layer': parts[5] if len(parts) == 6 else layer}


def _case_record(raw, index, warnings):
    record = {key: raw[key] for key in sorted(raw)}
    parsed = _parse_case_key(raw.get('case_key'), raw.get('layer'))
    if parsed['electrodes'] is None:
        warnings.add(f'case_key_unparsed:{raw.get("case_key")}', 'expected failure|e1|l1|e2|l2[|analysis_layer]')
    record.update(parsed)
    record['conditions'] = {role: _map_condition(index, role, parsed['analysis_layer'], parsed['electrodes'], warnings)
                            for role in ROLES} if parsed['electrodes'] is not None else {
        role: {'condition_id': None, 'reason': 'condition_key_unavailable'} for role in ROLES}
    return record


def _query_regions(query):
    return sorted(({query.get('region_id')} | set(query.get('merged_from') or ())) - {None})


def _build_rois(payload, index, warnings):
    """ROIs = reference_to_target queries (region_id ∪ merged_from); opposite-direction queries on the same region are duplicates."""
    rois, by_region = [], {}
    for direction in payload.get('directions') or []:
        if direction.get('direction') != 'reference_to_target':
            continue
        for query in direction.get('queries') or []:
            regions = _query_regions(query)
            key = 'roi:' + '+'.join(regions)
            roi = {'roi_key': key, 'query_id': query.get('query_id'), 'region_ids': regions,
                   'layers': sorted(query.get('layers') or []), 'score_um2': query.get('score_um2'),
                   'status': query.get('status'), 'center_xy_um': query.get('center_xy_um'),
                   'window_um': query.get('window_um'), 'roi_ids': sorted(query.get('roi_ids') or []),
                   'contributing_cases': [_case_record(c, index, warnings) for c in query.get('contributing_cases') or []],
                   'one_sided_cases': [_case_record(c, index, warnings) for c in query.get('one_sided_cases') or []]}
            roi['change_kinds'] = sorted({c.get('change_kind') for c in roi['contributing_cases'] if c.get('change_kind')})
            rois.append(roi)
            for region in regions:
                by_region.setdefault(region, key)
    rois.sort(key=lambda r: r['roi_key'])
    return rois, by_region


def _query_to_rois(payload, by_region):
    """(direction, roi id) -> set of ROI keys, for the confirm path (a query roi id lives in queries[].roi_ids)."""
    table = {}
    for direction in payload.get('directions') or []:
        for query in direction.get('queries') or []:
            keys = {by_region[region] for region in _query_regions(query) if region in by_region}
            for roi_id in query.get('roi_ids') or []:
                table.setdefault((direction.get('direction'), roi_id), set()).update(keys)
    return table


# ---- rounds 4/5: pairs ------------------------------------------------------------------------------------------------------

def _side_record(side):
    return {'product_id': side.get('product_id'), 'center_xy_um': side.get('center_xy_um'),
            'analysis_layer': side.get('analysis_layer'), 'endpoints': side.get('endpoints'), 'roi_id': side.get('roi_id')}


def _pair_records(correspondence, sidecar, path_kind, assign, index, scores, warnings):
    pairs = {}
    sidecar_pairs = sidecar.get('pairs') or {}
    ids = [p.get('pair_id') for p in correspondence.get('pairs') or []]
    if set(sidecar_pairs) != set(ids) or len(set(ids)) != len(ids):
        raise _mismatch('sidecar_pair_set', f'{path_kind}: sidecar pairs differ from the correspondence file')
    for pair in correspondence['pairs']:
        pid = pair['pair_id']
        side_data = sidecar_pairs[pid]
        keys = sorted(assign(side_data))
        roi_key = None
        if len(keys) > 1:
            roi_key = sorted(keys, key=lambda k: (-(scores.get(k) if scores.get(k) is not None else float('-inf')), k))[0]
            warnings.add(f'multi_roi:{pid}', ','.join(keys))
        elif keys:
            roi_key = keys[0]
        else:
            warnings.add(f'unassigned_pair:{pid}', f'{path_kind} pair is in no ROI')
        record = {'pair_id': pid, 'path': path_kind, 'roi_key': roi_key, 'status': pair.get('status'),
                  'reasons': list(pair.get('reasons') or []),
                  'a': _side_record(pair['a']), 'b': _side_record(pair['b']),
                  'attributes': {key: side_data.get(key) for key in SIDECAR_ATTRIBUTES}, 'named_pair': {}}
        conditions = {}
        for role, side in (('reference', pair['a']), ('target', pair['b'])):
            endpoints = side.get('endpoints')
            if not endpoints:
                reason = 'confirm_named_pair_unavailable' if path_kind == 'confirm' else 'endpoints_missing'
                conditions[role] = {'condition_id': None, 'reason': reason}
            else:
                conditions[role] = _map_condition(index, role, side.get('analysis_layer'),
                                                  [e.get('electrode_id') for e in endpoints], warnings)
        record['conditions'] = conditions
        pairs[pid] = record
    return pairs


# ---- round 6: named_pair rows -----------------------------------------------------------------------------------------------

def _number(text):
    if text is None or str(text).strip() == '':
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _same_window(value, window):
    return value is not None and abs(value - window) <= WINDOW_TOLERANCE * max(1.0, abs(window))


def _named_rows(rows, map_window_um):
    """pair_id -> {diameter text: {a_value_um2, b_value_um2, a_status, b_status}}; exactly one row per (pair, diameter)."""
    if not any(_same_window(_number(row.get('window_size_um')), map_window_um) for row in rows):
        raise _error('compare2_link_window_missing', f'local_delta_rows.csv has no row with window_size_um == {map_window_um:g}')
    found = {}
    for row in rows:
        if row.get('metric') != 'named_pair' or not _same_window(_number(row.get('window_size_um')), map_window_um):
            continue
        diameter = _number(row.get('diameter_um'))
        if diameter is None or diameter <= 0:
            raise _error('compare2_link_schema_mismatch', f'{WINDOWS_ROWS}: named_pair row without a diameter ({row.get("pair_id")})')
        key = (row.get('pair_id'), format(size_key(diameter), 'f'))
        if key in found:
            raise _error('compare2_report_duplicate_row', f'{WINDOWS_ROWS}: pair {key[0]}, diameter {key[1]}, window {map_window_um:g}')
        found[key] = {'a_value_um2': _number(row.get('a_value_um2')), 'b_value_um2': _number(row.get('b_value_um2')),
                      'a_status': row.get('a_status') or None, 'b_status': row.get('b_status') or None}
    table = {}
    for (pid, diameter), value in found.items():
        table.setdefault(pid, {})[diameter] = value
    return {pid: {d: table[pid][d] for d in sorted(table[pid], key=float)} for pid in sorted(table)}


# ---- DL1 --------------------------------------------------------------------------------------------------------------------

def _load_dl1(paths, known_ids, target_ids, warnings):
    from .dl_results import _dl1
    out = []
    for path in sorted((Path(p) for p in paths), key=lambda p: (p.name, str(p))):
        if not path.is_file():
            raise _error('compare2_link_input_missing', str(path))
        data = path.read_bytes()
        try:
            raw = json.loads(data.decode('utf-8'))
        except ValueError as exc:
            raise _error('compare2_link_dl1_invalid', f'{path}: not JSON ({exc})')
        try:
            _dl1(raw, raw.get('fingerprint') if isinstance(raw, dict) else None)
        except NumericContractError as exc:
            raise _error('compare2_link_dl1_invalid', f'{path}: {exc}')
        rankings = [{'product_id': row['product_id'], 'score': row['score']} for row in raw['rankings']]
        excluded = []
        for item in raw['excluded'] or []:
            if isinstance(item, dict):
                excluded.append({'product_id': item.get('product_id'), 'reason': item.get('reason')})
            else:
                excluded.append({'product_id': str(item), 'reason': None})
        if target_ids and raw['query_id'] not in target_ids:
            warnings.add('dl1_query_mismatch', f'{path.name}: query_id {raw["query_id"]!r} is not the target product {sorted(target_ids)}')
        out.append({'path_name': path.name, 'sha256': hashlib.sha256(data).hexdigest(), 'query_id': raw['query_id'],
                    'status': raw['status'], 'reason': raw.get('reason'), 'rankings': rankings, 'excluded': excluded,
                    'selected': sorted({row['product_id'] for row in rankings} & known_ids)})
    return out


# ---- entry point ------------------------------------------------------------------------------------------------------------

def build_link(compare2_doc, *, candidates_dir=None, confirm_dir=None, windows_dir=None, dl1_search=(), map_window_um):
    """Load and check the optional inputs against ``compare2_doc`` and return the link dict (None when nothing is given)."""
    dl1_search = list(dl1_search or ())
    if candidates_dir is None and confirm_dir is None and windows_dir is None and not dl1_search:
        return None
    if confirm_dir is not None and candidates_dir is None:
        raise _error('compare2_link_input_missing', '--confirm needs --candidates')
    if windows_dir is not None and candidates_dir is None and confirm_dir is None:
        raise _error('compare2_link_input_missing', '--windows needs --candidates or --confirm')
    warnings = _Warnings()
    inputs, rois, pairs = {}, [], {}
    reference_hash = _manifest_hash(compare2_doc, 'reference') if (candidates_dir or windows_dir) else None
    target_hash = _manifest_hash(compare2_doc, 'target') if (candidates_dir or windows_dir) else None
    index = _condition_index(compare2_doc)
    pair_products = {}
    used_correspondence = None

    if candidates_dir is not None:
        cand_files = _Files(candidates_dir)
        payload = cand_files.json(CANDIDATES_FILE, 'candidate_queries_v1')
        direct_corr = cand_files.json(DIRECT_CORRESPONDENCE, 'roi_correspondence_v1')
        direct_sidecar = cand_files.json(DIRECT_SIDECAR, 'direct_pairs_sidecar_v1')
        for role, want in (('reference', reference_hash), ('target', target_hash)):
            got = ((payload.get('inputs') or {}).get(role) or {}).get('dataset_input_hash')
            if got != want:
                raise _mismatch('candidates_dataset_input_hash', f'{role}: {got!r} != compare2 {want!r}')
        rois, by_region = _build_rois(payload, index, warnings)
        scores = {roi['roi_key']: roi['score_um2'] for roi in rois}
        known = {roi['roi_key'] for roi in rois}
        if confirm_dir is None:     # confirm pairs replace the direct pairs (D3): direct files are still required and read
            pairs.update(_pair_records(direct_corr, direct_sidecar, 'direct',
                                       lambda side: {by_region[side.get('region_id')]} if side.get('region_id') in by_region else set(),
                                       index, scores, warnings))
            used_correspondence = ('candidates', DIRECT_CORRESPONDENCE, cand_files)
            pair_products['direct'] = direct_corr
        inputs['candidates'] = cand_files.record('candidates')

        if confirm_dir is not None:
            conf_files = _Files(confirm_dir)
            confirm_corr = conf_files.json(CONFIRM_CORRESPONDENCE, 'roi_correspondence_v1')
            confirm_sidecar = conf_files.json(CONFIRM_SIDECAR, 'confirmed_pairs_sidecar_v1')
            recorded = (confirm_sidecar.get('inputs') or {}).get('candidates_payload_sha256')
            if recorded != cand_files.sha[CANDIDATES_FILE]:
                raise _mismatch('confirm_candidates_payload_sha256',
                                f'{recorded!r} != sha256 of {CANDIDATES_FILE} {cand_files.sha[CANDIDATES_FILE]}')
            lookup = _query_to_rois(payload, by_region)

            def assign_confirm(side):
                keys = set()
                for candidate in side.get('candidates') or []:
                    keys |= lookup.get((candidate.get('direction'), candidate.get('query_roi_id')), set())
                return keys & known
            confirm_pairs = _pair_records(confirm_corr, confirm_sidecar, 'confirm', assign_confirm, index, scores, warnings)
            pairs.update(confirm_pairs)
            used_correspondence = ('confirm', CONFIRM_CORRESPONDENCE, conf_files)
            pair_products['confirm'] = confirm_corr
            inputs['confirm'] = conf_files.record('confirm')

    ids = {'reference': set(), 'target': set()}
    if candidates_dir is not None:
        for role in ROLES:
            ids[role].update(((payload.get('inputs') or {}).get(role) or {}).get('product_ids') or [])
    for role, side in (('reference', 'a'), ('target', 'b')):
        for corr in pair_products.values():
            found = {p[side].get('product_id') for p in corr.get('pairs') or []}
            if len(found) > 1:
                raise _mismatch('pair_product_id', f'{role}: pairs name several products {sorted(map(str, found))}')
            want = _known_product_ids(compare2_doc, role) or ids[role]
            if found and want and not found <= want:
                raise _mismatch('pair_product_id', f'{role}: pair product {sorted(map(str, found))} not in {sorted(want)}')

    if windows_dir is not None:
        win_files = _Files(windows_dir)
        delta = win_files.json(WINDOWS_JSON, 'local_delta_v1')
        rows = win_files.rows(WINDOWS_ROWS)
        provenance = delta.get('provenance') or {}
        for role, want in (('reference', reference_hash), ('target', target_hash)):
            got = provenance.get(f'{role}_input_hash')
            if got != want:
                raise _mismatch('windows_input_hash', f'{role}: {got!r} != compare2 {want!r}')
        kind, name, owner = used_correspondence
        if provenance.get('correspondence_sha256') != owner.sha[name]:
            raise _mismatch('windows_correspondence_sha256',
                            f'{provenance.get("correspondence_sha256")!r} != sha256 of the {kind} {name} {owner.sha[name]}')
        products = delta.get('products') or {}
        for role, side in (('reference', 'a'), ('target', 'b')):
            got = (products.get(side) or {}).get('product_id')
            want = _known_product_ids(compare2_doc, role) or ids[role]
            if got and want and got not in want:
                raise _mismatch('windows_product_id', f'{role}: {got!r} not in {sorted(want)}')
        named = _named_rows(rows, float(map_window_um))
        for pid, by_diameter in named.items():
            if pid in pairs:
                pairs[pid]['named_pair'] = by_diameter
        inputs['windows'] = win_files.record('windows')

    known_ids = {i for role in ROLES for i in _known_product_ids(compare2_doc, role) | ids[role]}
    target_ids = _known_product_ids(compare2_doc, 'target') | ids['target']
    for corr in pair_products.values():
        for pair in corr.get('pairs') or []:
            known_ids.update(p for p in (pair['a'].get('product_id'), pair['b'].get('product_id')) if p)
            if pair['b'].get('product_id'):
                target_ids.add(pair['b']['product_id'])
    dl1 = _load_dl1(dl1_search, known_ids, target_ids, warnings) if dl1_search else []
    if dl1:
        inputs['dl1'] = {'kind': 'dl1', 'files': {item['path_name']: item['sha256'] for item in dl1}}

    return {'schema_version': SCHEMA_VERSION, 'map_window_um': float(map_window_um), 'inputs': inputs,
            'rois': rois, 'pairs': [pairs[pid] for pid in sorted(pairs)], 'dl1': dl1, 'warnings': warnings.result()}
