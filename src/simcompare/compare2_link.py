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
import io
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
        return list(csv.DictReader(io.StringIO(text, newline='')))

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
        warnings.add('ambiguous_condition_key', f'{role}:{text}: {",".join(ids)}')
        return {'condition_id': None, 'reason': 'ambiguous_condition_key'}
    warnings.add('unmapped_condition', f'{role}:{text}: no compare2 condition with this analysis layer and electrode pair')
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
        warnings.add('case_key_unparsed', f'{raw.get("case_key")}: expected failure|e1|l1|e2|l2[|analysis_layer]')
    record.update(parsed)
    present = raw.get('present_in')
    conditions = {}
    for role in ROLES:
        if present in ROLES and role != present:      # the case does not exist on this side: no mapping, no warning
            conditions[role] = {'condition_id': None, 'reason': 'case_absent_on_side'}
        elif parsed['electrodes'] is None:
            conditions[role] = {'condition_id': None, 'reason': 'condition_key_unavailable'}
        else:
            conditions[role] = _map_condition(index, role, parsed['analysis_layer'], parsed['electrodes'], warnings)
    record['conditions'] = conditions
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
            warnings.add('multi_roi', f'{pid}: {",".join(keys)}')
        elif keys:
            roi_key = keys[0]
        else:
            warnings.add('unassigned_pair', f'{pid}: {path_kind} pair is in no ROI')
        record = {'pair_id': pid, 'path': path_kind, 'roi_key': roi_key, 'status': pair.get('status'),
                  'reasons': list(pair.get('reasons') or []),
                  'a': _side_record(pair['a']), 'b': _side_record(pair['b']),
                  'attributes': {key: side_data.get(key) for key in SIDECAR_ATTRIBUTES}, 'named_pair': {}}
        conditions = {}
        one_sided = bool(pair['a'].get('endpoints')) != bool(pair['b'].get('endpoints'))
        for role, side in (('reference', pair['a']), ('target', pair['b'])):
            endpoints = side.get('endpoints')
            if not endpoints:
                if path_kind == 'confirm':
                    reason = 'confirm_named_pair_unavailable'
                else:       # a direct pair that has endpoints on one side only has no named_pair rows either
                    reason = 'one_sided_pair_no_endpoints' if one_sided else 'endpoints_missing'
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
        try:
            canonical = format(size_key(diameter), 'f')
        except NumericContractError as exc:
            raise _error('compare2_link_schema_mismatch',
                         f'{WINDOWS_ROWS}: named_pair diameter {row.get("diameter_um")!r} ({row.get("pair_id")}): {exc.code}')
        key = (row.get('pair_id'), canonical)
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
    numbered = sorted(enumerate(Path(p) for p in paths), key=lambda item: (item[1].name, str(item[1]), item[0]))
    for position, path in numbered:      # position = index in the given order, so equal file names never collide
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
        listed = raw['excluded'] if raw['excluded'] is not None else []
        if not isinstance(listed, list) or not all(isinstance(item, dict) for item in listed):
            raise _error('compare2_link_dl1_invalid', f'{path}: excluded must be a list of objects')
        excluded = [{'product_id': item.get('product_id'), 'reason': item.get('reason')} for item in listed]
        if not target_ids:
            warnings.add('dl1_query_unverified', f'{path.name}: the target product id is unknown, query_id {raw["query_id"]!r} is not checked')
        elif raw['query_id'] not in target_ids:
            warnings.add('dl1_query_mismatch', f'{path.name}: query_id {raw["query_id"]!r} is not the target product {sorted(target_ids)}')
        out.append({'position': position, 'path_name': path.name, 'sha256': hashlib.sha256(data).hexdigest(), 'query_id': raw['query_id'],
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
        inputs['dl1'] = {'kind': 'dl1', 'files': {f'{item["position"]}:{item["path_name"]}': item['sha256'] for item in dl1}}

    return {'schema_version': SCHEMA_VERSION, 'map_window_um': float(map_window_um), 'inputs': inputs,
            'rois': rois, 'pairs': [pairs[pid] for pid in sorted(pairs)], 'dl1': dl1, 'warnings': warnings.result()}


# ---- locations: delta-vi per pair and ROI, classification, explained ratio ---------------------------------------------------

LOCATIONS_SCHEMA = 'compare2_locations_v1'
KINDS = ('line', 'pixel')
NET_THRESHOLD = 0.005
DEFAULT_RATIO_EPS = 1e-15       # compare2.RATIO_EPS; the document's inputs.constants.ratio_null_below wins when present
APPROXIMATION_NOTE = ('Approximation: each pair is valued with the window area of its own hotspot map and the side density and factor; '
                      'shared parts and transfer are not separated. CNO and CRP differences, changes outside the ROIs and '
                      'overlapping windows stay in the residual (residual_unexplained_by_location_sum).')


def _dkey(value):
    return format(size_key(value), 'f')


def _location_diameters(doc):
    flt = (doc.get('inputs') or {}).get('diameter_filter') or {}
    values = flt.get('diameters_um')
    if values is None:      # no filter: every diameter compare2 summed
        values = [size.get('diameter_um') for role in ROLES
                  for layer in (((doc.get('sides') or {}).get(role)) or {}).get('layers') or []
                  for size in layer.get('by_size') or []]
    return [format(key, 'f') for key in sorted({size_key(v) for v in values if v is not None})]


class _SideTables:
    """The compare2 numbers of one side that the location formula reads: F, per-condition class, density per layer and diameter."""

    def __init__(self, doc, role):
        side = (doc.get('sides') or {}).get(role) or {}
        product = (doc.get('products') or {}).get(role) or {}
        self.factor = {'line': product.get('F_line'), 'pixel': product.get('F_pixel')}
        self.conditions = {c.get('condition_id'): c for c in side.get('conditions') or []}
        self.density, self.dlayers = {}, {}
        for row in side.get('layers') or []:
            table = self.density.setdefault((row.get('layer'), row.get('density_layer')), {})
            for size in row.get('by_size') or []:
                table[_dkey(size['diameter_um'])] = {'line': size.get('density_line'), 'pixel': size.get('density_pixel')}
            self.dlayers.setdefault(row.get('layer'), set()).add(row.get('density_layer'))

    def density_of(self, condition, dkey, kind):
        layer, dlayer = condition.get('layer'), None
        for row in condition.get('by_size') or []:
            if _dkey(row['diameter_um']) == dkey:
                dlayer = row.get('density_layer')
                break
        if dlayer is None and len(self.dlayers.get(layer, ())) == 1:
            dlayer = next(iter(self.dlayers[layer]))
        return ((self.density.get((layer, dlayer)) or {}).get(dkey) or {}).get(kind)


def _pair_location(pair, diameters, tables, slopes):
    reasons = set()
    out = {'pair_id': pair['pair_id'], 'roi_key': pair.get('roi_key'), 'path': pair.get('path'),
           'delta_lambda': {k: None for k in KINDS}, 'delta_vi': {k: None for k in KINDS}, 'reasons': []}

    def done():
        out['reasons'] = sorted(reasons)
        return out
    conds = pair.get('conditions') or {}
    if pair.get('path') == 'confirm':
        reasons.add('confirm_named_pair_unavailable')
        return done()
    named = pair.get('named_pair') or {}
    if not named:
        one_sided = any((conds.get(role) or {}).get('reason') == 'one_sided_pair_no_endpoints' for role in ROLES)
        reasons.add('one_sided_pair_no_endpoints' if one_sided else 'named_pair_missing')
        return done()
    if not diameters:
        reasons.add('diameter_set_empty')
    reasons.update(f'window_row_missing:{d}' for d in diameters if d not in named)
    side_cond = {}
    for role in ROLES:
        cid = (conds.get(role) or {}).get('condition_id')
        condition = tables[role].conditions.get(cid) if cid is not None else None
        if condition is None:
            reasons.add(f'condition_unmapped:{role}')
        elif condition.get('class') not in KINDS:
            reasons.add(f'class_missing:{role}')
        else:
            side_cond[role] = condition
    if len(side_cond) < len(ROLES) or not diameters or any(r.startswith('window_row_missing') for r in reasons):
        return done()
    for kind in KINDS:
        total, failed = 0.0, False
        for d in diameters:
            terms = {}
            for role, area_key in (('reference', 'a_value_um2'), ('target', 'b_value_um2')):
                condition = side_cond[role]
                if condition['class'] != kind:
                    terms[role] = 0.0
                    continue
                area = named[d].get(area_key)
                if area is None:
                    reasons.add(f'area_missing:{role}:{d}')
                    failed = True
                    continue
                rho = tables[role].density_of(condition, d, kind)
                factor = tables[role].factor[kind]
                if area == 0:
                    terms[role] = 0.0
                    if rho is None or factor is None:
                        reasons.add('density_missing_zero_area')
                    continue
                if rho is None:
                    reasons.add(f'density_missing:{role}:{d}')
                    failed = True
                if factor is None:
                    reasons.add(f'factor_missing:{role}')
                    failed = True
                if rho is not None and factor is not None:
                    terms[role] = rho * area * factor
            if len(terms) == len(ROLES):
                total += terms['target'] - terms['reference']
        if not failed:
            out['delta_lambda'][kind] = total
            if slopes[kind] is None:
                reasons.add(f'slope_missing:{kind}')
            else:
                out['delta_vi'][kind] = slopes[kind] * total
    return done()


def _case_summary(case):
    return {key: case.get(key) for key in ('case_key', 'change_kind', 'present_in', 'layer_changed', 'failure', 'electrodes',
                                           'analysis_layer')}


def _classify(net):
    if net is None:
        return 'uncomputable'
    return 'worse' if net > NET_THRESHOLD else ('improved' if net < -NET_THRESHOLD else 'negligible')


def compute_locations(link, compare2_doc, *, top_regions=5, top_improved=3, max_pairs_per_condition=3):
    """Delta-vi per pair and ROI from the window areas, ROI classification and the explained ratio; values come from compare2 only."""
    if link is None:
        raise _error('compare2_link_input_missing', 'compute_locations needs a link')
    diameters = _location_diameters(compare2_doc)
    tables = {role: _SideTables(compare2_doc, role) for role in ROLES}
    slopes = {k: (((compare2_doc.get('delta') or {}).get('slopes') or {}).get(k) or {}).get('value') for k in KINDS}
    indicators = (compare2_doc.get('delta') or {}).get('indicators') or {}
    total_vi = {k: indicators.get(f'delta_vi_{k}') for k in KINDS}
    eps = ((compare2_doc.get('inputs') or {}).get('constants') or {}).get('ratio_null_below', DEFAULT_RATIO_EPS)
    pairs = sorted((_pair_location(p, diameters, tables, slopes) for p in link.get('pairs') or []), key=lambda p: p['pair_id'])
    by_roi = {}
    for pair in pairs:
        if pair['roi_key'] is not None:
            by_roi.setdefault(pair['roi_key'], []).append(pair)
    link_pairs = {p['pair_id']: p for p in link.get('pairs') or []}

    rois = []
    for roi in sorted(link.get('rois') or [], key=lambda r: r['roi_key']):
        members, reasons = by_roi.get(roi['roi_key'], []), set()
        values = {}
        for k in KINDS:
            nulls = sum(1 for p in members if p['delta_vi'][k] is None)
            if not members:
                values[k] = None
            elif nulls:
                values[k] = None
                reasons.add(f'pair_null:{k}:{nulls}')
            else:
                values[k] = sum(p['delta_vi'][k] for p in members)
        if not members:
            reasons.add('roi_without_pairs')
        ranked = sorted(members, key=lambda p: (any(p['delta_vi'][k] is None for k in KINDS),
                                                -sum(abs(p['delta_vi'][k]) for k in KINDS if p['delta_vi'][k] is not None),
                                                p['pair_id']))
        conditions = {c for case in roi['contributing_cases'] + roi['one_sided_cases'] for c in
                      (case['conditions'][role]['condition_id'] for role in ROLES)}
        for p in members:
            conditions.update(link_pairs[p['pair_id']]['conditions'][role]['condition_id'] for role in ROLES)
        rois.append({'roi_key': roi['roi_key'], 'delta_vi': values, 'net': None, 'class': 'uncomputable',
                     'reasons': sorted(reasons), 'map_pairs': [p['pair_id'] for p in ranked[:max_pairs_per_condition]],
                     'design_change': {'change_kinds': roi['change_kinds'],
                                       'contributing_cases': [_case_summary(c) for c in roi['contributing_cases']],
                                       'one_sided_cases': [_case_summary(c) for c in roi['one_sided_cases']]},
                     'condition_ids': sorted(c for c in conditions if c is not None)})

    denominator = {}
    for k in KINDS:
        valid = sum(abs(r['delta_vi'][k]) for r in rois if r['delta_vi'][k] is not None)
        denominator[k] = max(abs(total_vi[k]), valid) if total_vi[k] is not None else valid
    for roi in rois:
        if any(roi['delta_vi'][k] is None for k in KINDS):
            continue
        roi['net'] = sum(0.0 if denominator[k] == 0 else roi['delta_vi'][k] / denominator[k] for k in KINDS)
        roi['class'] = _classify(roi['net'])
    counts = {name: sum(1 for r in rois if r['class'] == name) for name in ('worse', 'improved', 'negligible', 'uncomputable')}
    worse = sorted((r for r in rois if r['class'] == 'worse'), key=lambda r: (-r['net'], r['roi_key']))
    improved = sorted((r for r in rois if r['class'] == 'improved'), key=lambda r: (r['net'], r['roi_key']))
    top_w, top_i = worse[:top_regions], improved[:top_improved]

    explained = {}
    for k in KINDS:
        valid_sum = sum(r['delta_vi'][k] for r in rois if r['delta_vi'][k] is not None)
        whole = total_vi[k]
        entry = {'delta_vi': whole, 'location_sum': valid_sum, 'residual_unexplained_by_location_sum': None,
                 'ratio_top_worse': None, 'ratio_top_improved': None, 'reasons': []}
        if whole is not None:
            entry['residual_unexplained_by_location_sum'] = whole - valid_sum
        if whole is None or abs(whole) < eps:
            entry['reasons'].append('ratio_undefined')
        else:
            entry['ratio_top_worse'] = sum(r['delta_vi'][k] for r in top_w if r['delta_vi'][k] is not None) / whole
            entry['ratio_top_improved'] = sum(r['delta_vi'][k] for r in top_i if r['delta_vi'][k] is not None) / whole
        explained[k] = entry

    warnings = []
    if not diameters:
        warnings.append({'code': 'diameter_set_empty', 'detail': 'compare2 names no diameter'})
    for k in KINDS:
        if slopes[k] is None:
            warnings.append({'code': 'slope_missing', 'detail': k})
    return {'schema_version': LOCATIONS_SCHEMA, 'diameters_um': [float(d) for d in diameters],
            'approximation_note': APPROXIMATION_NOTE, 'pairs': pairs, 'rois': rois, 'counts': counts,
            'top_worse': [r['roi_key'] for r in top_w], 'top_improved': [r['roi_key'] for r in top_i],
            'explained': explained, 'warnings': warnings}
