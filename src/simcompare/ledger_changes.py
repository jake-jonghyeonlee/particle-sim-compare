"""ledger_changes: link the 04 change locations (candidate_queries_v1) to the ledger items of one compare2 run (mrep/T2).

Pure function, no file is written. ``build_changes(candidates, led_doc, c2_doc)`` returns a dict with the ROI list (same
``roi_key`` as the 07 report anchors, from ``compare2_link._build_rois``), the link of every 04 case to one ledger item and
the per-pair / per-item ROI lists.

Rule (case -> item), in this order; no guess is made when the key is not unique:
  1. exactly one ledger pair with the same electrode set, else ``no_pair`` / ``pair_excluded`` (0) or ``ambiguous`` (2+);
  2. in that pair exactly one item with the same failure_type, the same sorted endpoints (electrode, layer) and, when the
     case_key has six parts, the same analysis_layer -> ``linked``; none -> ``unlinked/no_item``; 2+ -> ``ambiguous``;
  3. a CNO case (``lateral_open|electrode|layer|layer``) goes to the ledger ``cno`` row (electrode, density_layer).
The case_key text is made by ``change_map._key_text``: failure_type, then (electrode_id, actual_layer) of the sorted
endpoints, then the analysis layer; the ledger item carries the same ``failure_type`` / ``endpoints[].actual_layer`` /
``analysis_layer`` (compare2._build_ledger), so the two keys are one system.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .compare2_link import (CANDIDATES_FILE, ROLES, _Warnings, _build_rois, _cno_index, _condition_index, _parse_case_key,
                            _is_cno_parsed, _query_regions)
from .compare2_report import KIND_TEXT
from .numeric_schema import NumericContractError

SCHEMA_VERSION = 'ledger_changes_v1'
CANDIDATES_SCHEMA = 'candidate_queries_v1'
KINDS = ('line', 'pixel')
NO_CHANGE = 'no_change'


def _empty(status, detail=None, apply_align=None):
    return {'schema_version': SCHEMA_VERSION, 'link_status': status, 'detail': detail, 'apply_align': apply_align,
            'rois': [], 'pairs': {}, 'items': {}, 'cno': {}, 'warnings': [],
            'summary': {'roi_count': 0, 'linked_pairs': 0, 'linked_no_change_pairs': 0, 'unlinked_pairs': 0,
                        'pair_denominator': 0, 'linked_cases': 0, 'unlinked_cases': 0, 'ambiguous_cases': 0, 'tag_pair_counts': {}}}


def _load(candidates):
    """-> (payload or None, status, detail). A folder without the 04 file, or None, is no_candidates."""
    if candidates is None:
        return None, 'no_candidates', 'no candidates input'
    if isinstance(candidates, dict):
        return candidates, 'ok', None
    path = Path(candidates)
    file = path / CANDIDATES_FILE if path.is_dir() else path
    if not file.is_file():
        return None, 'no_candidates', f'{file} does not exist'
    try:
        doc = json.loads(file.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return None, 'invalid', f'{file}: {exc}'
    return doc, 'ok', None


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _check_query(query, where):
    """C1: the structure _build_rois and the map rectangle need; -> a problem text or None."""
    # R2:1: the types are checked before `_query_regions` builds a set from them (a list region_id is unhashable, an
    # int merged_from is not iterable)
    region_id, merged = query.get('region_id'), query.get('merged_from')
    if region_id is not None and not isinstance(region_id, str):
        return f'{where}: region_id {region_id!r} is not a string (query_id {query.get("query_id")!r})'
    if merged is not None and not (isinstance(merged, (list, tuple)) and all(isinstance(r, str) for r in merged)):
        return f'{where}: merged_from {merged!r} is not a list of strings (query_id {query.get("query_id")!r})'
    regions = _query_regions(query)
    if not regions or not all(isinstance(r, str) and r for r in regions):
        return f'{where}: region_id / merged_from names no region (query_id {query.get("query_id")!r})'
    where = f'{where} (region {"+".join(regions)})'
    center = query.get('center_xy_um')
    if not (isinstance(center, (list, tuple)) and len(center) == 2 and all(_number(v) for v in center)):
        return f'{where}: center_xy_um {center!r} is not two numbers'
    window = query.get('window_um')
    if not (_number(window) and window > 0):
        return f'{where}: window_um {window!r} is not a positive number'
    for field in ('contributing_cases', 'one_sided_cases'):
        cases = query.get(field)
        if field == 'contributing_cases' and cases is None:
            return f'{where}: contributing_cases is missing'
        if cases is not None and not (isinstance(cases, list) and all(isinstance(c, dict) for c in cases)):
            return f'{where}: {field} is not a list of objects'
    for field in ('layers', 'roi_ids', 'merged_from'):
        if query.get(field) is not None and not isinstance(query[field], (list, tuple)):
            return f'{where}: {field} is not a list'
    if query.get('unit_fold') is not None and not isinstance(query['unit_fold'], dict):
        return f'{where}: unit_fold is not an object'
    return None


def _check_payload(payload):
    if not isinstance(payload, dict) or payload.get('schema_version') != CANDIDATES_SCHEMA:
        got = payload.get('schema_version') if isinstance(payload, dict) else None
        return f'schema_version {got!r}, expected {CANDIDATES_SCHEMA!r}'
    if not isinstance(payload.get('inputs'), dict):
        return 'inputs is not an object'
    for role in ROLES:
        if payload['inputs'].get(role) is not None and not isinstance(payload['inputs'][role], dict):
            return f'inputs.{role} is not an object'
    if payload.get('change_args') is not None and not isinstance(payload['change_args'], dict):
        return 'change_args is not an object'
    directions = payload.get('directions')
    if not isinstance(directions, list) or not all(isinstance(d, dict) for d in directions):
        return 'directions is not a list of objects'
    for d_index, direction in enumerate(directions):
        queries = direction.get('queries') or []
        if not isinstance(queries, list) or not all(isinstance(q, dict) for q in queries):
            return 'queries is not a list of objects'
        if direction.get('direction') != 'reference_to_target':        # only these make ROIs (_build_rois)
            continue
        for q_index, query in enumerate(queries):
            problem = _check_query(query, f'directions[{d_index}].queries[{q_index}]')
            if problem is not None:
                return problem
    return None


def _manifests(c2_doc):
    inputs = c2_doc.get('inputs') if isinstance(c2_doc, dict) else None
    manifests = inputs.get('manifests') if isinstance(inputs, dict) else None
    return manifests if isinstance(manifests, dict) else {}


def _hash_mismatch(payload, c2_doc):
    """C6: 04 inputs.<role>.dataset_input_hash against 02 inputs.manifests.<role>.input_hash; both must be non-empty."""
    manifests = _manifests(c2_doc)
    for role in ROLES:
        got = (payload['inputs'].get(role) or {}).get('dataset_input_hash')
        want = (manifests.get(role) or {}).get('input_hash') if isinstance(manifests.get(role), dict) else None
        if not isinstance(got, str) or not got or not isinstance(want, str) or not want:
            return f'{role}: hash missing (04 {got!r}, 02 {want!r})'
        if got != want:
            return f'{role}: 04 {got!r} != 02 {want!r}'
    return None


def _ends(endpoints):
    return tuple(sorted((e['electrode_id'], e['actual_layer']) for e in endpoints or ()))


def _excluded_sets(led_doc, c2_doc):
    """Electrode sets recorded as left out (compare2 manifests.<role>.pair_exclusions, then the ledger copy)."""
    found = set()
    manifests = _manifests(c2_doc)
    blocks = [manifests[role].get('pair_exclusions') for role in ROLES if isinstance(manifests.get(role), dict)]
    led_inputs = led_doc.get('inputs') if isinstance(led_doc.get('inputs'), dict) else {}
    led_blocks = led_inputs.get('pair_exclusions')
    blocks += list(led_blocks.values()) if isinstance(led_blocks, dict) else []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        for entry in list(block.get('pairs') or []) + list(block.get('cases') or []):
            if isinstance(entry, dict) and entry.get('electrodes'):
                found.add(tuple(sorted(entry['electrodes'])))
    return found


def _link_case(case, pairs_by_set, cno_rows, excluded):
    """-> dict(status, reason, pair_id, item_id, cno_id). status: linked / unlinked / ambiguous."""
    key = case.get('case_key')
    parts = key.split('|') if isinstance(key, str) else []
    parsed = _parse_case_key(key, case.get('layer'))
    out = {'status': 'unlinked', 'reason': None, 'pair_id': None, 'item_id': None, 'cno_id': None}
    if parsed['electrodes'] is None:
        out['reason'] = 'case_key_unparsed'
        return out
    if _is_cno_parsed(parsed):
        electrode, layer = parsed['electrodes'][0], parsed['analysis_layer']
        hits = [r for r in cno_rows if r.get('electrode_id') == electrode and r.get('density_layer') == layer]
        if len(hits) == 1:
            out.update(status='linked', cno_id=f'cno:{electrode}@{layer}')
        else:
            out['status'], out['reason'] = ('ambiguous', 'ambiguous_cno') if hits else ('unlinked', 'no_item')
        return out
    electrodes = tuple(sorted(parsed['electrodes']))
    candidates = pairs_by_set.get(electrodes, [])
    if len(candidates) > 1:
        out.update(status='ambiguous', reason='ambiguous_pair')
        return out
    if not candidates:
        out['reason'] = 'pair_excluded' if electrodes in excluded else 'no_pair'
        return out
    pair = candidates[0]
    out['pair_id'] = pair['pair_id']
    ends = tuple(zip(parsed['electrodes'], parsed['endpoint_layers']))
    ends = tuple(sorted(ends))
    analysis = parsed['analysis_layer'] if len(parts) == 6 else None
    hits = [i for i in pair.get('items') or []
            if i.get('failure_type') == parsed['failure'] and _ends(i.get('endpoints')) == ends
            and (analysis is None or i.get('analysis_layer') == analysis)]
    if len(hits) == 1:
        out.update(status='linked', item_id=hits[0]['item_id'])
    elif hits:
        out.update(status='ambiguous', reason='ambiguous_item', pair_id=None)
    else:
        out['reason'] = 'no_item'
    return out


def _merge_present(values):
    """present_in of one case seen in several lists: one value stays, different values mean both products."""
    values = {v for v in values if v}
    if len(values) > 1:
        return 'both'
    return next(iter(values)) if values else None


def _roi_cases(roi):
    """C2: the cases of one ROI, one entry per case_key (contributing_cases and one_sided_cases merged).

    -> [(case, change_kinds, one_sided, present_in)]. one_sided only when every sighting is one-sided; the shape tags
    (change_kind) of all sightings are kept; a case without a case_key is never merged."""
    merged, order = {}, []
    for one_sided, source in ((False, roi['contributing_cases']), (True, roi['one_sided_cases'])):
        for index, case in enumerate(source):
            key = case.get('case_key')
            slot = key if isinstance(key, str) else ('#', one_sided, index)
            if slot not in merged:
                merged[slot] = {'case': case, 'kinds': [], 'one_sided': [], 'present': []}
                order.append(slot)
            entry = merged[slot]
            if case.get('change_kind') and case['change_kind'] not in entry['kinds']:
                entry['kinds'].append(case['change_kind'])
            entry['one_sided'].append(one_sided)
            entry['present'].append(case.get('present_in'))
    return [(merged[s]['case'], merged[s]['kinds'], all(merged[s]['one_sided']), _merge_present(merged[s]['present']))
            for s in order]


def _case_row(case, kinds, one_sided, present_in, link):
    return {'case_key': case.get('case_key'), 'change_kind': kinds[0] if kinds else case.get('change_kind'),
            'change_kinds': sorted(kinds), 'present_in': present_in,
            'one_sided': one_sided, 'status': link['status'], 'reason': link['reason'], 'pair_id': link['pair_id'],
            'item_id': link['item_id'], 'cno_id': link['cno_id']}


def build_changes(candidates, led_doc, c2_doc):
    """The link of the 04 change locations to the ledger. ``candidates``: the 04 folder (or file) path, the parsed
    candidate_queries_v1 payload, or None. Never raises on bad input: the problem is ``link_status`` (R2:1: a shape
    the checks did not foresee is ``invalid`` with the exception in ``detail``)."""
    try:
        return _build_changes(candidates, led_doc, c2_doc)
    except Exception as exc:
        apply_align = None
        if isinstance(candidates, dict):
            change_args = candidates.get('change_args')
            apply_align = (change_args.get('apply_align') or 'diagnostic') if isinstance(change_args, dict) else 'diagnostic'
        return _empty('invalid', f'{type(exc).__name__}: {exc}', apply_align)


def _build_changes(candidates, led_doc, c2_doc):
    payload, status, detail = _load(candidates)
    if status != 'ok':
        return _empty(status, detail)
    problem = _check_payload(payload)
    if problem is None and not (isinstance(led_doc, dict) and isinstance(led_doc.get('pairs'), list)
                                and all(isinstance(p, dict) and isinstance(p.get('pair_id'), str) for p in led_doc['pairs'])):
        problem = 'ledger document has no pairs list of objects with a pair_id'
    if problem is None and not isinstance(led_doc.get('cno') or [], list):
        problem = 'ledger document cno is not a list'
    change_args = payload.get('change_args') if isinstance(payload, dict) else None
    apply_align = ((change_args.get('apply_align') or 'diagnostic') if isinstance(change_args, dict) else 'diagnostic') \
        if isinstance(payload, dict) else None
    if problem is not None:
        return _empty('invalid', problem, apply_align)
    mismatch = _hash_mismatch(payload, c2_doc)
    if mismatch is not None:
        return _empty('stale', mismatch, apply_align)       # diagnostic mode is checked the same way (C6)
    warnings = _Warnings()
    try:
        rois, _by_region = _build_rois(payload, _condition_index(c2_doc), _cno_index(c2_doc), warnings)
    except (NumericContractError, KeyError, TypeError, AttributeError, ValueError) as exc:
        return _empty('invalid', f'{type(exc).__name__}: {exc}', apply_align)

    pairs_by_set = {}
    for pair in led_doc['pairs']:
        pairs_by_set.setdefault(tuple(sorted(pair.get('electrodes') or ())), []).append(pair)
    ledger_pairs = {p['pair_id']: p for p in led_doc['pairs']}
    cno_rows = led_doc.get('cno') or []
    excluded = _excluded_sets(led_doc, c2_doc)

    pair_rois, pair_tags, item_rois, cno_rois = {}, {}, {}, {}
    roi_out, ambiguous_keys, linked_keys, unlinked_keys = [], set(), set(), set()
    for roi in rois:
        cases = []
        key = roi['roi_key']
        for case, kinds, one_sided, present_in in _roi_cases(roi):
            link = _link_case(case, pairs_by_set, cno_rows, excluded)
            cases.append(_case_row(case, kinds, one_sided, present_in, link))
            if link['status'] == 'linked':
                linked_keys.add(case.get('case_key'))
                if link['item_id'] is not None:
                    item_rois.setdefault(link['item_id'], set()).add(key)
                    pair_rois.setdefault(link['pair_id'], set()).add(key)
                    pair_tags.setdefault(link['pair_id'], set()).update(kinds)
                else:
                    cno_rois.setdefault(link['cno_id'], set()).add(key)
            elif link['status'] == 'ambiguous':
                ambiguous_keys.add(case.get('case_key'))
            else:
                unlinked_keys.add(case.get('case_key'))
        roi_out.append({'roi_key': roi['roi_key'], 'query_id': roi['query_id'], 'region_ids': roi['region_ids'],
                        'layers': roi['layers'], 'score_um2': roi['score_um2'], 'status': roi['status'],
                        'center_xy_um': roi['center_xy_um'], 'window_um': roi['window_um'],
                        'change_kinds': roi['change_kinds'],
                        'change_kind_text': [KIND_TEXT.get(k, '판정 불가') for k in roi['change_kinds']],
                        'cases': cases})
    pairs = {pid: {'roi_keys': sorted(keys), 'tags': sorted(pair_tags.get(pid, ()))} for pid, keys in sorted(pair_rois.items())}
    denominator = {p['pair_id'] for p in led_doc['pairs'] if p.get('change_kind') != NO_CHANGE}
    tag_counts = {}
    for pid, entry in pairs.items():
        kind = (ledger_pairs.get(pid) or {}).get('kind')
        for tag in entry['tags']:
            counts = tag_counts.setdefault(tag, {'line': 0, 'pixel': 0})
            if kind in KINDS:
                counts[kind] += 1
    return {'schema_version': SCHEMA_VERSION, 'link_status': 'ok', 'detail': None, 'apply_align': apply_align,
            'rois': roi_out, 'pairs': pairs,
            'items': {iid: {'roi_keys': sorted(keys)} for iid, keys in sorted(item_rois.items())},
            'cno': {cid: {'roi_keys': sorted(keys)} for cid, keys in sorted(cno_rois.items())},
            'warnings': warnings.result(),
            # R1:3: linked_pairs + unlinked_pairs = pair_denominator (no_change pairs are outside); the no_change
            # pairs that were linked anyway are counted apart
            'summary': {'roi_count': len(roi_out), 'linked_pairs': len(denominator & set(pairs)),
                        'linked_no_change_pairs': len(set(pairs) - denominator),
                        'unlinked_pairs': len(denominator - set(pairs)), 'pair_denominator': len(denominator),
                        'linked_cases': len(linked_keys), 'unlinked_cases': len(unlinked_keys - linked_keys),
                        'ambiguous_cases': len(ambiguous_keys),
                        'tag_pair_counts': {tag: tag_counts[tag] for tag in sorted(tag_counts)}}}
