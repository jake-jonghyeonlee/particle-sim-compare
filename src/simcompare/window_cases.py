"""compare-multiproduct T2b: window_cases_v1, the hotspot case composition of a model pair inside one window.

For every ready pair of a compare-windows --model run (a = target, b = model) and one chosen window size and diameter,
each side's cases (same product / design version / frame / analysis layer, side filters applied; endpoints and failure
type do NOT filter) are measured one at a time inside the pair's square window. A case is `present` (available, area
> 0), `absent` (available, area 0) or `unknown` (partial, unavailable, no record; the reasons are kept, never counted as
absent). Cases are named by failure type plus the sorted endpoint electrode ids and compared per name:
both / target_only / model_only / unknown. The result is a list and a count, not an area: no new quantity is defined and
no cause is claimed.
"""
from __future__ import annotations

import csv
from pathlib import Path

from .local_delta import _csv_cell, _measure_cropped, _product, _square, _write_json
from .publication import staged_file
from .numeric_schema import DIAMETERS_UM, NumericContractError, diameter_key, finite

SCHEMA_VERSION = 'window_cases_v1'
SEMANTICS = 'observed hotspot case composition in the window; not a causal attribution'
CASE_KEY_RULE = ('key = failure_type + sorted endpoint electrode_id list ("|"-joined); the analysis layer is the pair\'s own '
                 'layer on both sides; the same key twice on one side is ambiguous (listed, compared as unknown); a key with '
                 'no case in one dataset is absent there, a key whose measurement failed is unknown')
CLASS_RULE = {'both': 'present in a and b', 'target_only': 'present in a (target), absent in b (model)',
              'model_only': 'absent in a, present in b', 'unknown': 'a or b unknown or ambiguous',
              'not_listed': 'absent on both sides'}
CLASS_ORDER = {'target_only': 0, 'model_only': 1, 'unknown': 2, 'both': 3}
MAX_CANDIDATES_PER_SIDE = 5000
MAX_LISTED_CASES = 500
JSON_NAME, CSV_NAME = 'window_cases.json', 'window_cases.csv'
CSV_COLUMNS = ['pair_id', 'key', 'failure_type', 'endpoints', 'a_status', 'b_status', 'class', 'a_area_um2', 'b_area_um2']


def writes_nothing(exc):
    """Mark a refusal that must end the command with exit 2 before any file is written (cli.py honours the mark)."""
    exc.writes_nothing = True
    return exc


def check_window(window_um, window_sizes_um):
    """The case window must be one of the compare-windows sizes."""
    window = finite(window_um, 'case window', positive=True)
    if window not in [float(size) for size in window_sizes_um]:
        raise writes_nothing(NumericContractError('case_window_not_in_sizes', f'--case-window-um {window_um} 는 --window-sizes 안의 값이어야 합니다'))
    return window


def resolve_diameter(reference, target, diameter_um):
    """The canonical diameter (a DIAMETERS_UM value) when both datasets have a record for it; else a no-write refusal."""
    value = finite(diameter_um, 'case diameter', positive=True)
    matches = [d for d in DIAMETERS_UM if diameter_key(d) == diameter_key(value)]
    if matches:
        for dataset in (reference, target):
            if not any(matches[0] in case.records for case in dataset.cases):
                matches = []
                break
    if not matches:
        raise writes_nothing(NumericContractError('case_diameter_missing', f'--case-diameter-um {diameter_um} 는 두 데이터셋 모두에 있는 지름이어야 합니다'))
    return matches[0]


def _filters_match(case, side):
    for name, value in side.filters.items():
        actual = getattr(case.identity, name, None) if name != 'unit_id' else case.metadata.get('unit_id')
        if actual != value:
            return False
    return True


class _Index:
    """dataset.cases bucketed once by (product, design, frame, analysis layer)."""

    def __init__(self, dataset):
        self._buckets = {}
        for case in dataset.cases:
            identity = case.identity
            self._buckets.setdefault((identity.product_id, identity.design_version, identity.frame_id, case.analysis_layer), []).append(case)

    def candidates(self, side):
        bucket = self._buckets.get((side.product_id, side.design_version, side.frame.frame_id, side.analysis_layer), ())
        return [case for case in bucket if _filters_match(case, side)]


def _key(case):
    return (case.failure_type, tuple(sorted(e.electrode_id for e in case.endpoints)))


def _key_text(key):
    return '|'.join((key[0], *key[1]))


def _entry(status, area=None, reasons=()):
    return {'status': status, 'area_um2': area, 'reasons': sorted(set(reasons))}


def _measure_case(case, diameter, window):
    """present / absent / unknown for one case inside the window. The mask is read here and dropped on return."""
    if diameter not in case.records:
        return _entry('unknown', reasons=('no_record_for_diameter',))
    record = case.records[diameter]
    measured = _measure_cropped(record.frame, record.mask, window, diameter=diameter, analysis_layer=record.analysis_layer,
                                identity=record.identity, input_status=record.input_status, reasons=record.reasons)
    if measured.status != 'available':
        return _entry('unknown', reasons=(f'measurement_{measured.status}', *measured.reasons))
    area = measured.ca_um2
    return _entry('present' if area > 0 else 'absent', area, measured.reasons)


def _side_states(candidates, diameter, window):
    """{key: entry} for one side; a key held by more than one case is ambiguous and is not measured."""
    by_key = {}
    for case in candidates:
        by_key.setdefault(_key(case), []).append(case)
    states = {}
    for key, cases in by_key.items():
        states[key] = _entry('unknown', reasons=('ambiguous_case_key',)) if len(cases) > 1 else _measure_case(cases[0], diameter, window)
    return states


def _classify(a, b):
    if a['status'] == 'unknown' or b['status'] == 'unknown':
        return 'unknown'
    if a['status'] == 'present':
        return 'both' if b['status'] == 'present' else 'target_only'
    return 'model_only' if b['status'] == 'present' else 'not_listed'


def _pair_entry(pair, window_um, diameter, indexes):
    entry = {'pair_id': pair.pair_id, 'window_um': window_um, 'diameter_um': float(diameter),
             'center_a': list(pair.a.center_xy_um), 'center_b': list(pair.b.center_xy_um),
             'analysis_layer': pair.a.analysis_layer, 'status': 'available',
             'counts': {'both': 0, 'target_only': 0, 'model_only': 0, 'unknown': 0}, 'cases': [], 'truncated': 0}
    candidates = {label: indexes[label].candidates(side) for label, side in (('a', pair.a), ('b', pair.b))}
    entry['n_candidates'] = {label: len(cases) for label, cases in candidates.items()}
    if any(len(cases) > MAX_CANDIDATES_PER_SIDE for cases in candidates.values()):
        entry['status'] = 'case_limit_exceeded'
        return entry
    states = {'a': _side_states(candidates['a'], diameter, _square(pair.a.center_xy_um, window_um)),
              'b': _side_states(candidates['b'], diameter, _square(pair.b.center_xy_um, window_um))}
    missing = _entry('absent', reasons=('no_case_in_dataset',))
    listed = []
    for key in set(states['a']) | set(states['b']):
        a, b = states['a'].get(key, missing), states['b'].get(key, missing)
        kind = _classify(a, b)
        if kind == 'not_listed':
            continue
        entry['counts'][kind] += 1
        listed.append((CLASS_ORDER[kind], key, {'key': _key_text(key), 'failure_type': key[0], 'endpoints': list(key[1]),
                                                'a': a, 'b': b, 'class': kind}))
    listed.sort(key=lambda item: item[:2])
    entry['cases'] = [item[2] for item in listed[:MAX_LISTED_CASES]]
    entry['truncated'] = max(0, len(listed) - MAX_LISTED_CASES)
    return entry


def build_window_cases(pairs, reference, target, *, window_um, diameter_um, correspondence_sha256):
    """The window_cases_v1 payload. reference = a (the target product), target = b (the model)."""
    window = finite(window_um, 'case window', positive=True)
    diameter = resolve_diameter(reference, target, diameter_um)
    indexes = {'a': _Index(reference), 'b': _Index(target)}
    ready = [pair for pair in pairs if pair.status == 'ready']
    return {'schema_version': SCHEMA_VERSION, 'semantics': SEMANTICS, 'window_um': window, 'diameter_um': float(diameter),
            'products': {'a': {'product_id': _product(reference)}, 'b': {'product_id': _product(target)}},
            'case_key_rule': CASE_KEY_RULE, 'class_rule': CLASS_RULE,
            'limits': {'max_candidates_per_side': MAX_CANDIDATES_PER_SIDE, 'max_listed_cases_per_pair': MAX_LISTED_CASES,
                       'list_order': 'target_only, model_only, unknown, both; then key'},
            'inputs': {'correspondence_sha256': correspondence_sha256},
            'n_pairs': len(pairs), 'n_ready': len(ready), 'n_blocked': len(pairs) - len(ready),
            'pairs': [_pair_entry(pair, window, diameter, indexes) for pair in ready]}


def _csv_rows(payload):
    for pair in payload['pairs']:
        for case in pair['cases']:
            yield {'pair_id': pair['pair_id'], 'key': case['key'], 'failure_type': case['failure_type'],
                   'endpoints': ';'.join(case['endpoints']), 'a_status': case['a']['status'], 'b_status': case['b']['status'],
                   'class': case['class'], 'a_area_um2': case['a']['area_um2'], 'b_area_um2': case['b']['area_um2']}


def write_window_cases(out, payload):
    """window_cases.json and window_cases.csv into the staging folder (publication.py publishes them with the rest)."""
    out = Path(out)
    json_path, csv_path = out/JSON_NAME, out/CSV_NAME
    _write_json(json_path, payload)
    with staged_file(csv_path, newline='') as handle:   # own writer: an empty list still gets the real header
        writer = csv.DictWriter(handle, CSV_COLUMNS, lineterminator='\n')
        writer.writeheader()
        for row in _csv_rows(payload):
            writer.writerow({k: _csv_cell(v) for k, v in row.items()})
    return [str(json_path), str(csv_path)]
