"""ledger: the pair / electrode ledger of one compare2 run (`ledger_v1`).

Input: compare2.json (+ the compare2 config it was made with, + the DL1 electrode folder *names*). No mask and no PNG is
read. Every number comes from the compare2 values: item lambda x the secant slope `delta.slopes.<kind>.value`
(docs/plan/ledger_report/README.md sections 5-8, 10, 11).

Null propagation (README 6): a kind (line / pixel) whose two vi or whose slope is missing has no value at all (every A, B,
delta and % of that kind is null, its checks are `not_evaluated`); presence (present / absent) is kept apart from the value.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .compare2 import (LEDGER_BASIS, SCHEMA_VERSION as COMPARE2_SCHEMA, _csv_text, _item_id, _json_text,
                       _within, stale_reason)
from .compare2_inputs import load_compare2_config
from .numeric_schema import NumericContractError

SCHEMA_VERSION = 'ledger_v1'
COMMAND = 'ledger'
KINDS = ('line', 'pixel')
ROLES = ('reference', 'target')
OWNED_NAMES = {'ledger.json', 'ledger_pairs.csv', 'ledger_electrodes.csv', 'execution.json', 'error.json'}
TMP_PREFIX = '.ledger-'
NO_CHANGE_ABS, NO_CHANGE_REL = 1e-12, 1e-9
TOP_CONTRIBUTORS = 2

# change kinds (README 8): code -> label; the order is the display order
CHANGE_KINDS = (
    ('same_pair_change', '같은 쌍 증감'), ('layer_change', '레이어 변경'), ('new_pair', '새 쌍'),
    ('gone_pair', '사라진 쌍'), ('electrode_added_removed', '전극 추가·제거'), ('no_change', '변화 없음'),
    ('cno', 'CNO'), ('value_pending', '값 대기'))
STATUS_LABELS = (('common', '공통'), ('new', '신규'), ('removed', '제거'), ('unverifiable', '확인 불가'))


# ---- small helpers -------------------------------------------------------------------------------------------------

def _osum(values):
    """Exact (math.fsum) sum; null as soon as one part is null."""
    values = list(values)
    return None if any(v is None for v in values) else math.fsum(values)


def _sub(b, a):
    return None if a is None or b is None else b - a


def _times(s, x):
    return None if s is None or x is None else s * x


def _pct(value, vi_a):
    """100 x value / vi_a. Null only for a null part or an exactly 0 denominator; a small positive vi_a is computed."""
    if value is None or vi_a is None or vi_a == 0:
        return None
    return 100.0 * (value / vi_a)


def _sha256_json(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


class _Acc(object):
    """An exact (math.fsum) sum that stays null once one part is null."""
    __slots__ = ('parts', 'null')

    def __init__(self):
        self.parts, self.null = [], False

    def add(self, value):
        if value is None:
            self.null = True
        else:
            self.parts.append(value)

    def value(self, computable=True):
        return None if self.null or not computable else math.fsum(self.parts)


class _Tri(object):
    """A, B and delta accumulators."""
    __slots__ = ('a', 'b', 'd')

    def __init__(self):
        self.a, self.b, self.d = _Acc(), _Acc(), _Acc()

    def add(self, a, b, d):
        self.a.add(a)
        self.b.add(b)
        self.d.add(d)

    def out(self, computable=True):
        return {'a': self.a.value(computable), 'b': self.b.value(computable), 'delta': self.d.value(computable)}


def _check(total, target):
    """One equation: sum = target within the compare2 tolerance; not_evaluated when a side is null."""
    if total is None or target is None:
        return {'status': 'not_evaluated', 'sum': total, 'target': target, 'abs_error': None}
    error = total - target
    return {'status': 'passed' if _within(error, target) else 'failed', 'sum': total, 'target': target,
            'abs_error': abs(error)}


def _worst(statuses):
    statuses = set(statuses)
    return 'failed' if 'failed' in statuses else ('not_evaluated' if 'not_evaluated' in statuses else 'passed')


# ---- reading compare2.json -----------------------------------------------------------------------------------------

def _read_compare2(compare2_dir, cfg2):
    folder = Path(compare2_dir)
    if (folder / 'error.json').exists():
        raise NumericContractError('compare2_execution_error', f'{folder}: error.json exists, compare2 did not finish')
    execution = folder / 'execution.json'
    if execution.exists():
        try:
            record = json.loads(execution.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise NumericContractError('compare2_execution_error', f'{execution}: {exc}') from exc
        if isinstance(record, dict) and (record.get('status') == 'error' or record.get('exit_code') == 2):
            raise NumericContractError('compare2_execution_error', f'{execution}: compare2 ended with an error')
    path = folder / 'compare2.json'
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise NumericContractError('compare2_json_missing', f'{path}: {exc}') from exc
    try:
        doc = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise NumericContractError('compare2_json_invalid', f'{path}: {exc}') from exc
    if not isinstance(doc, dict) or doc.get('schema_version') != COMPARE2_SCHEMA:
        raise NumericContractError('unsupported_compare2_json', f'{path}: schema_version must be {COMPARE2_SCHEMA}')
    inputs = doc.get('inputs') or {}
    if inputs.get('ledger_basis') != LEDGER_BASIS:
        raise NumericContractError(
            'ledger_compare2_stale',
            f'compare2.json ledger_basis is {inputs.get("ledger_basis")!r}, this code needs {LEDGER_BASIS!r}: '
            '02(compare2)를 새 번들로 다시 실행')
    # the same stale checks as 07 (compare2_report._read_compare2): one shared function
    stale = stale_reason(doc)
    if stale is not None:
        raise NumericContractError(stale[0], stale[1])
    recorded = (inputs.get('compare2_config') or {}).get('sha256')
    if recorded != cfg2.config_sha256:
        raise NumericContractError(
            'compare2_config_mismatch',
            f'compare2.json was made with config sha256 {recorded}, this config is {cfg2.config_sha256}')
    products = doc.get('products') or {}
    hashes = {role: (products.get(role) or {}).get('pair_table_hash') for role in ROLES}
    if hashes['reference'] is None or hashes['target'] is None or hashes['reference'] != hashes['target']:
        raise NumericContractError(
            'ledger_pair_table_mismatch',
            f'pair table hash differs between the products ({hashes}): 두 모델이 같은 pairs.csv 를 쓰도록 real.yaml 을 '
            '고친 뒤 02 부터 다시')
    return doc, hashlib.sha256(raw).hexdigest()


class _Side(object):
    """One product of compare2.json, reduced to what the ledger reads."""

    def __init__(self, role, side):
        self.role = role
        self.vi = {kind: side.get(f'vi_{kind}') for kind in KINDS}
        lam = side.get('lambda') or {}
        self.lam = {'line': lam.get('line'), 'pixel': lam.get('pixel'), 'cno': lam.get('cno')}
        self.pairs = {}
        for cond in side.get('conditions') or []:
            rec = self._pair(cond['pair_id'])
            rec['electrodes'] = list(cond.get('electrodes') or rec['electrodes'])
            if cond.get('class'):
                rec['classes'].add(cond['class'])
            if 'condition_not_computable' in (cond.get('reasons') or ()):
                rec['noncomputable'] = True
        for item in side.get('ledger_items') or []:
            rec = self._pair(item['pair_id'])
            if item.get('electrodes') and not rec['electrodes']:
                rec['electrodes'] = list(item['electrodes'])
            rec['classes'].add(item['class'])
            item_id = item['item_id']
            expected = _item_id(item['pair_id'], item['failure_type'], item['upper_layer'], item['lower_layer'],
                                [(e['electrode_id'], e['actual_layer']) for e in item['endpoints']])
            if item_id != expected:
                raise NumericContractError(
                    'ledger_item_id_mismatch',
                    f'{role}: item_id {item_id!r} does not match its structure ({expected!r}): '
                    '02(compare2)를 새 번들로 다시 실행')
            if item_id in rec['items']:  # an item id is unique per side; two equal ids are never merged
                raise NumericContractError('ledger_duplicate_item_id', f'{role}: item_id {item_id!r} appears twice')
            stored = dict(item)
            # the distance filter removed every cell of this item: a true 0 that no longer has a layer key (README 8)
            stored['filtered_out'] = set(item.get('reasons') or ()) == {'diameter_filter_removed_all'}
            rec['items'][item_id] = stored
        self.cno = {}
        for row in sorted(side.get('cno') or [], key=lambda r: str(r.get('case_name'))):
            key = (row['electrode_id'], row['density_layer'])
            known = self.cno.get(key)
            sizes = {}
            for entry in row.get('by_size') or ():
                sizes[entry['diameter_um']] = entry['lambda']
            if known is None:
                self.cno[key] = {'lambda': row.get('lambda'), 'sizes': sizes, 'reasons': list(row.get('reasons') or ())}
            else:  # two folder-name forms of one electrode and layer: the lambdas add
                known['lambda'] = _osum([known['lambda'], row.get('lambda')])
                for diameter, value in sizes.items():
                    known['sizes'][diameter] = (known['sizes'][diameter] + value) if diameter in known['sizes'] else value
                known['reasons'] = sorted(set(known['reasons']) | set(row.get('reasons') or ()))
        self.observed = set()
        for rec in self.pairs.values():
            self.observed.update(rec['electrodes'])
            for item in rec['items'].values():
                self.observed.update(item.get('electrodes') or ())
                self.observed.update(e['electrode_id'] for e in item.get('endpoints') or ())
        self.observed.update(electrode for electrode, _layer in self.cno)

    def _pair(self, pid):
        return self.pairs.setdefault(pid, {'electrodes': [], 'classes': set(), 'items': {}, 'noncomputable': False})

    def electrode_layers(self):
        """{electrode: {layer}} from the item endpoints; an electrode seen only in CNO takes its CNO density layers."""
        out = {}
        for rec in self.pairs.values():
            for item in rec['items'].values():
                for end in item.get('endpoints') or ():
                    out.setdefault(end['electrode_id'], set()).add(end['actual_layer'])
        from_items = set(out)
        for electrode, layer in self.cno:
            if electrode not in from_items:
                out.setdefault(electrode, set()).add(layer)
        return out


def _pair_kinds(sides):
    """{pair_id: 'line' | 'pixel' | None}; one pair id with two classes is an input error (README 8, X1)."""
    kinds = {}
    for pid in sorted({pid for side in sides.values() for pid in side.pairs}):
        classes = set()
        for side in sides.values():
            classes.update(side.pairs.get(pid, {'classes': ()})['classes'])
        classes.discard(None)
        if len(classes) > 1:
            raise NumericContractError('ledger_pair_class_mismatch', f'pair {pid}: classes {sorted(classes)}')
        kinds[pid] = next(iter(classes)) if classes else None
    return kinds


# ---- electrode inventory (README 8, X3) ------------------------------------------------------------------------------

def _inventory(cfg2, role, side):
    from .adapters.electrode_png import scan_electrode_shapes
    product = getattr(cfg2, role)
    shapes = {}
    if cfg2.report.dl1_root is not None and product.dl1_model is not None:
        shapes = scan_electrode_shapes(cfg2.report.dl1_root, product.dl1_model)
    if shapes:
        names = sorted(shapes)
        layers = {name: sorted(shapes[name]) for name in names}
        return {'electrodes': names, 'layers': layers, 'source': 'dl1', 'status': 'complete',
                'sha256': _sha256_json({'electrodes': names, 'layers': layers})}
    names = sorted(side.observed)
    return {'electrodes': names, 'layers': {}, 'source': 'observed', 'status': 'incomplete',
            'sha256': _sha256_json({'electrodes': names, 'layers': {}})}


# ---- the computation ------------------------------------------------------------------------------------------------

def _no_change(a, b):
    return abs(b - a) <= NO_CHANGE_ABS + NO_CHANGE_REL * max(abs(a), abs(b))


def build_ledger(doc, doc_sha256, cfg2):
    """The ledger document (a dict) and its two CSV row lists. Reads only compare2.json, the config and DL1 names."""
    sides = {role: _Side(role, doc['sides'][role]) for role in ROLES}
    side_a, side_b = sides['reference'], sides['target']
    delta = doc.get('delta') or {}
    slopes = delta.get('slopes') or {}
    indicators = delta.get('indicators') or {}
    pair_kind = _pair_kinds(sides)

    # kinds: computable only when both vi and the slope exist (X2)
    fam, slope = {}, {}
    for kind in KINDS:
        entry = slopes.get(kind)
        fam[kind] = (isinstance(side_a.vi[kind], (int, float)) and isinstance(side_b.vi[kind], (int, float))
                     and entry is not None and entry.get('value') is not None)
        slope[kind] = entry['value'] if fam[kind] else None

    def value(kind, lam):
        return _times(slope[kind], lam)

    # electrode inventories, presence, conflicts
    inventory = {role: _inventory(cfg2, role, sides[role]) for role in ROLES}
    warnings, present = [], {}
    for role in ROLES:
        inv, side = inventory[role], sides[role]
        if inv['status'] == 'incomplete':
            warnings.append({'code': 'electrode_inventory_from_hotspots', 'role': role})
            present[role] = set(side.observed)
        else:
            outside = sorted(side.observed - set(inv['electrodes']))
            if outside:
                warnings.append({'code': 'electrode_inventory_conflict', 'role': role, 'electrodes': outside})
            present[role] = set(inv['electrodes']) | side.observed
    complete = all(inventory[role]['status'] == 'complete' for role in ROLES)

    # pairs and their items
    pair_rows, unavailable = {}, []
    totals_items = {kind: _Tri() for kind in KINDS}
    pair_sum = {kind: _Tri() for kind in KINDS}            # sum of the pair values (README 5, check source)
    electrodes = {}
    layer_acc = {kind: {} for kind in KINDS}                # kind -> layer -> _Tri
    size_acc = {kind: {} for kind in KINDS}                 # kind -> (layer, diameter) -> _Tri
    pair_layer = {kind: {} for kind in KINDS}               # kind -> (layer, pair_id) -> _Tri

    def electrode(name):
        return electrodes.setdefault(name, {'line': _Tri(), 'pixel': _Tri(), 'cno': _Tri(), 'layers': {},
                                            'partners': {'reference': set(), 'target': set()}})

    def electrode_layer(name, layer, kind):
        return electrode(name)['layers'].setdefault(layer, {'line': _Tri(), 'pixel': _Tri()})[kind]

    for pid in sorted({pid for side in sides.values() for pid in side.pairs}):
        recs = {role: sides[role].pairs.get(pid) for role in ROLES}
        kind = pair_kind[pid]
        names = (recs['target'] or recs['reference'])['electrodes'] or (recs['reference'] or recs['target'])['electrodes']
        names = list(names)
        item_ids = sorted(set(recs['reference']['items'] if recs['reference'] else ()) |
                          set(recs['target']['items'] if recs['target'] else ()))
        item_rows, sums = [], {'a': [], 'b': [], 'd': []}
        for item_id in item_ids:
            ia = recs['reference']['items'].get(item_id) if recs['reference'] else None
            ib = recs['target']['items'].get(item_id) if recs['target'] else None
            base = ib or ia
            ikind = base['class']
            filtered = all(item['filtered_out'] for item in (ia, ib) if item is not None)
            lam_a = None if ia is None else ia.get('lambda')
            lam_b = None if ib is None else ib.get('lambda')
            a = None if slope[ikind] is None else (0.0 if ia is None else value(ikind, lam_a))
            b = None if slope[ikind] is None else (0.0 if ib is None else value(ikind, lam_b))
            d = _sub(b, a)
            sums['a'].append(a)
            sums['b'].append(b)
            sums['d'].append(d)
            item_rows.append({
                'item_id': item_id, 'failure_type': base['failure_type'], 'analysis_layer': base['analysis_layer'],
                'upper_layer': base['upper_layer'], 'lower_layer': base['lower_layer'],
                'endpoints': base.get('endpoints') or [], 'class': ikind,
                'presence': 'both' if ia is not None and ib is not None else ('new' if ib is not None else 'gone'),
                'filtered_out': filtered,
                'a_filtered': bool(ia is not None and ia['filtered_out']),
                'b_filtered': bool(ib is not None and ib['filtered_out']),
                'lambda_a': lam_a, 'lambda_b': lam_b, 'a': a, 'b': b, 'delta': d})
            for role, item in (('reference', ia), ('target', ib)):
                if item is not None and item.get('lambda') is None:
                    unavailable.append({'scope': 'item', 'id': item_id, 'side': role, 'pair_id': pid,
                                        'reasons': sorted(item.get('reasons') or ['lambda_null'])})
            # kind totals, electrode and layer ledgers (item values split over the two endpoints)
            totals_items[ikind].add(a, b, d)
            ends = base.get('endpoints') or [{'electrode_id': e, 'actual_layer': base['analysis_layer']} for e in names]
            for end in ends:
                share = 1.0 / len(ends)
                parts = [None if v is None else v * share for v in (a, b, d)]
                electrode(end['electrode_id'])[ikind].add(*parts)
                electrode_layer(end['electrode_id'], end['actual_layer'], ikind).add(*parts)
            layer_acc[ikind].setdefault(base['analysis_layer'], _Tri()).add(a, b, d)
            pair_layer[ikind].setdefault((base['analysis_layer'], pid), _Tri()).add(a, b, d)
            sizes_a, sizes_b = _size_map(ia), _size_map(ib)
            for size_key in sorted(set(sizes_a) | set(sizes_b), key=lambda k: (k[0], str(k[1]))):
                # key = (diameter, density_layer): one item at one diameter in two density layers never overwrites
                va = _size_value(slope[ikind], ia, sizes_a, size_key)
                vb = _size_value(slope[ikind], ib, sizes_b, size_key)
                size_acc[ikind].setdefault((base['analysis_layer'], size_key[0]), _Tri()).add(va, vb, _sub(vb, va))
        for a_name in names:
            electrode(a_name)
            for b_name in names:
                for role in ROLES:
                    if recs[role] is not None and a_name != b_name:
                        electrode(a_name)['partners'][role].add(b_name)
        sum_a, sum_b, sum_d = _osum(sums['a']), _osum(sums['b']), _osum(sums['d'])
        if kind is not None:
            pair_sum[kind].add(sum_a, sum_b, sum_d)
        present_a, present_b = recs['reference'] is not None, recs['target'] is not None
        for role in ROLES:
            if recs[role] is not None and recs[role]['noncomputable']:
                unavailable.append({'scope': 'pair', 'id': pid, 'side': role, 'reasons': ['condition_not_computable']})

        def public(role, total):
            rec = recs[role]
            if kind is None or slope[kind] is None:
                return None
            if rec is None:
                return 0.0
            return None if rec['noncomputable'] else total

        other_ids = {'reference': set(recs['target']['items']) if recs['target'] else set(),
                     'target': set(recs['reference']['items']) if recs['reference'] else set()}
        a_pub, b_pub = public('reference', sum_a), public('target', sum_b)
        d_pub = _sub(b_pub, a_pub)
        pair_rows[pid] = {
            'pair_id': pid, 'electrodes': names, 'kind': kind, 'present_a': present_a, 'present_b': present_b,
            'computable_a': a_pub is not None, 'computable_b': b_pub is not None,
            'a': a_pub, 'b': b_pub, 'delta': d_pub,
            'layers_a': sorted({i['analysis_layer'] for i in (recs['reference'] or {'items': {}})['items'].values()}),
            'layers_b': sorted({i['analysis_layer'] for i in (recs['target'] or {'items': {}})['items'].values()}),
            'items': item_rows,
            '_keys_known': not any(recs[role] is not None and recs[role]['noncomputable'] for role in ROLES),
            # the layer-change key set leaves out a filter-removed item (a true 0) only when the other product
            # has no item with the same id at all (README 8)
            '_keys': {role: {item_id for item_id, item in recs[role]['items'].items()
                             if not item['filtered_out'] or item_id in other_ids[role]}
                      if recs[role] is not None else set() for role in ROLES}}

    # change kinds (README 8)
    for pid in sorted(pair_rows):
        row = pair_rows[pid]
        if (complete and any(name not in present['reference'] or name not in present['target']
                             for name in row['electrodes'])):
            code = 'electrode_added_removed'
        elif row['present_b'] and not row['present_a']:
            code = 'new_pair'
        elif row['present_a'] and not row['present_b']:
            code = 'gone_pair'
        elif row['_keys_known'] and row['_keys']['reference'] != row['_keys']['target']:
            code = 'layer_change'
        elif row['a'] is None or row['b'] is None:
            code = 'value_pending'
        elif _no_change(row['a'], row['b']):
            code = 'no_change'
        else:
            code = 'same_pair_change'
        row['change_kind'] = code

    # per-kind vi, percentages, ranks
    for kind in KINDS:
        vi_a = side_a.vi[kind]
        for row in pair_rows.values():
            if row['kind'] == kind:
                row['pct_a'], row['pct_b'], row['pct_delta'] = (_pct(row[name], vi_a) for name in ('a', 'b', 'delta'))
    for row in pair_rows.values():
        if row['kind'] is None:
            row['pct_a'] = row['pct_b'] = row['pct_delta'] = None
    ranks = {}
    for kind, letter in (('line', 'L'), ('pixel', 'P')):
        members = [row for row in pair_rows.values() if row['kind'] == kind]

        def order(row):
            ranking = row['b'] if row['present_b'] and row['b'] is not None else row['a']
            return (0 if ranking is not None else 1, -(ranking or 0.0), row['pair_id'])

        for index, row in enumerate(sorted(members, key=order), 1):
            row['rank'] = f'{letter}{index}'
            ranks[row['pair_id']] = index
    for row in pair_rows.values():
        row.setdefault('rank', None)

    # CNO rows (kind pixel, unit (electrode_id, density_layer))
    cno_rows, cno_total = [], _Tri()
    cno_layer, cno_size = {}, {}
    for key in sorted(set(side_a.cno) | set(side_b.cno)):
        ra, rb = side_a.cno.get(key), side_b.cno.get(key)
        base = rb or ra
        lam_a = None if ra is None else ra['lambda']
        lam_b = None if rb is None else rb['lambda']
        a = None if slope['pixel'] is None else (0.0 if ra is None else value('pixel', lam_a))
        b = None if slope['pixel'] is None else (0.0 if rb is None else value('pixel', lam_b))
        d = _sub(b, a)
        cno_total.add(a, b, d)
        for role, rec in (('reference', ra), ('target', rb)):
            if rec is not None and rec['lambda'] is None:
                unavailable.append({'scope': 'cno', 'id': f'{key[0]}@{key[1]}', 'side': role,
                                    'reasons': sorted(rec['reasons'] or ['lambda_null'])})
        cno_rows.append({
            'electrode_id': key[0], 'density_layer': key[1], 'change_kind': 'cno',
            'presence': 'both' if ra is not None and rb is not None else ('new' if rb is not None else 'gone'),
            'lambda_a': lam_a, 'lambda_b': lam_b, 'a': a, 'b': b, 'delta': d,
            'pct_a': _pct(a, side_a.vi['pixel']), 'pct_b': _pct(b, side_a.vi['pixel']),
            'pct_delta': _pct(d, side_a.vi['pixel'])})
        for target in (electrode(key[0])['cno'], electrode(key[0])['pixel'],
                       electrode_layer(key[0], key[1], 'pixel'), cno_layer.setdefault(key[1], _Tri())):
            target.add(a, b, d)
        sizes_a, sizes_b = (ra or {'sizes': {}})['sizes'], (rb or {'sizes': {}})['sizes']
        for diameter in sorted(set(sizes_a) | set(sizes_b)):
            va = _size_value(slope['pixel'], ra, sizes_a, diameter)
            vb = _size_value(slope['pixel'], rb, sizes_b, diameter)
            cno_size.setdefault((key[1], diameter), _Tri()).add(va, vb, _sub(vb, va))

    # intercept and CRP rows (README 5)
    lam_line_a = side_a.lam['line']
    x_b = _osum([side_b.lam['pixel'], side_b.lam['cno']])
    intercept = {
        'line': (None if not fam['line'] or lam_line_a is None else side_a.vi['line'] - slope['line'] * lam_line_a),
        'pixel': (None if not fam['pixel'] or x_b is None else side_b.vi['pixel'] - slope['pixel'] * x_b)}
    crp = delta.get('crp_difference')
    crp_d = None if not fam['pixel'] else (0.0 if crp is None else crp['delta_vi_pixel'])

    totals, kinds_doc, equations = {}, {}, {}
    for kind in KINDS:
        items = totals_items[kind].out(fam[kind])
        c = intercept[kind]
        cno = cno_total.out(fam['pixel']) if kind == 'pixel' else None
        if kind == 'line':
            tot_a, tot_b = _osum([items['a'], c]), _osum([items['b'], c])
            tot_d = items['delta']
            row_c = {'a': c, 'b': c, 'delta': None if c is None else 0.0}
            extra = {}
        else:
            minus_d = None if crp_d is None else -crp_d
            tot_a = _osum([items['a'], cno['a'], c, minus_d])
            tot_b = _osum([items['b'], cno['b'], c])
            tot_d = _osum([items['delta'], cno['delta'], crp_d])
            row_c = {'a': c, 'b': c, 'delta': None if c is None else 0.0}
            extra = {'cno': dict(cno, pct_delta=_pct(cno['delta'], side_a.vi['pixel'])),
                     'crp': {'present': crp is not None, 'a': minus_d, 'b': None if crp_d is None else 0.0,
                             'delta': crp_d}}
        totals[kind] = dict({'items': items, 'intercept': row_c, 'a': tot_a, 'b': tot_b, 'delta': tot_d,
                             'vi_a': side_a.vi[kind], 'vi_b': side_b.vi[kind]}, **extra)
        number = 1 if kind == 'line' else 4
        equations[f'eq{number}_{kind}_a'] = _check(tot_a, side_a.vi[kind] if fam[kind] else None)
        equations[f'eq{number + 1}_{kind}_b'] = _check(tot_b, side_b.vi[kind] if fam[kind] else None)
        equations[f'eq{number + 2}_{kind}_delta'] = _check(
            tot_d, indicators.get(f'delta_vi_{kind}') if fam[kind] else None)

    # electrode ledger
    electrode_names = sorted(set(electrodes) | set(inventory['reference']['electrodes']) |
                             set(inventory['target']['electrodes']))
    elec_layers = {role: sides[role].electrode_layers() for role in ROLES}
    electrode_rows = []
    for name in electrode_names:
        acc = electrode(name)
        in_a, in_b = name in present['reference'], name in present['target']
        if not complete:
            status = 'unverifiable'
        elif in_a and in_b:
            status = 'common'
        else:
            status = 'new' if in_b else 'removed'
        layers_ab = {}
        for role in ROLES:
            inv = inventory[role]
            if inv['status'] == 'complete':
                layers_ab[role] = list(inv['layers'].get(name, []))
            else:
                layers_ab[role] = sorted(elec_layers[role].get(name, ()))
        row = {'electrode': name, 'status': status, 'present_a': in_a, 'present_b': in_b,
               'layers_a': layers_ab['reference'], 'layers_b': layers_ab['target']}
        for kind in KINDS:
            out = acc[kind].out(fam[kind])
            out.update(pct_a=_pct(out['a'], side_a.vi[kind]), pct_b=_pct(out['b'], side_a.vi[kind]),
                       pct_delta=_pct(out['delta'], side_a.vi[kind]))
            row[kind] = out
        row['cno'] = acc['cno'].out(fam['pixel'])
        row['partners_a'] = sorted(acc['partners']['reference'])
        row['partners_b'] = sorted(acc['partners']['target'])
        row['layers'] = {layer: {kind: acc['layers'][layer][kind].out(fam[kind]) for kind in KINDS}
                         for layer in sorted(acc['layers'])}
        electrode_rows.append(row)

    # checks: electrode sums, pair delta against compare2
    electrode_sum = {}
    for kind in KINDS:
        entry = {}
        for field, name in (('a', 'a'), ('b', 'b'), ('delta', 'd')):
            parts = [row[kind][field] for row in electrode_rows]
            lhs = None if not fam[kind] or any(v is None for v in parts) else math.fsum(parts)
            rhs = getattr(pair_sum[kind], name).value(fam[kind])
            if kind == 'pixel':
                rhs = _osum([rhs, getattr(cno_total, name).value(fam[kind])])
            entry[field] = _check(lhs, rhs)
        electrode_sum[kind] = entry
    pair_vs_compare2 = {}
    for kind in KINDS:
        lhs = pair_sum[kind].d.value(fam[kind])
        conds = [c.get(f'delta_vi_{kind}') for c in delta.get('conditions') or []]
        shared = (delta.get('shared') or {}).get(f'delta_vi_{kind}')
        # a condition whose delta is null (not computable) has no value to compare: the relation cannot be evaluated
        rhs = (None if not fam[kind] or shared is None or any(v is None for v in conds)
               else math.fsum(conds + [shared]))
        pair_vs_compare2[kind] = _check(lhs, rhs)
    checks = {'equations': equations, 'electrode_sum': electrode_sum, 'pair_delta_vs_compare2': pair_vs_compare2}

    # kinds block
    ordered = {}
    for kind in KINDS:
        members = sorted((r for r in pair_rows.values() if r['kind'] == kind), key=lambda r: ranks[r['pair_id']])
        ordered[kind] = members
        counts = {code: 0 for code, _label in CHANGE_KINDS}
        for row in members:
            counts[row['change_kind']] += 1
        rows_delta = [r['delta'] for r in members]
        if kind == 'pixel':
            counts['cno'] = len(cno_rows)
            rows_delta += [r['delta'] for r in cno_rows]
            if crp_d is not None:
                rows_delta.append(crp_d)
        known = [v for v in rows_delta if v is not None]
        kinds_doc[kind] = {
            'computable': fam[kind], 'vi_a': side_a.vi[kind], 'vi_b': side_b.vi[kind], 'slope': slope[kind],
            'slope_method': (slopes.get(kind) or {}).get('method'),
            'lambda_a': side_a.lam[kind], 'lambda_b': side_b.lam[kind], 'pair_count': len(members),
            'change_kinds': counts,
            'increase_sum': math.fsum(v for v in known if v > 0) if fam[kind] else None,
            'decrease_sum': math.fsum(v for v in known if v < 0) if fam[kind] else None,
            'value_pending_rows': len(rows_delta) - len(known)}
        if not fam[kind]:
            unavailable.append({'scope': 'kind', 'id': kind, 'reasons': ['kind_not_computable']})
    unknown = sorted((r for r in pair_rows.values() if r['kind'] is None), key=lambda r: r['pair_id'])
    all_counts = {code: 0 for code, _label in CHANGE_KINDS}
    for row in pair_rows.values():
        all_counts[row['change_kind']] += 1
    all_counts['cno'] = len(cno_rows)

    # layers
    layers = {}
    for kind in KINDS:
        rows = []
        for layer in sorted(layer_acc[kind]):
            contrib = sorted(((pid, tri) for (lay, pid), tri in pair_layer[kind].items() if lay == layer),
                             key=lambda x: (-abs(x[1].d.value(fam[kind]) or 0.0), x[0]))
            sizes = [dict({'diameter_um': diameter}, **tri.out(fam[kind]))
                     for (lay, diameter), tri in sorted(size_acc[kind].items()) if lay == layer]
            rows.append(dict({'layer': layer}, **layer_acc[kind][layer].out(fam[kind]), by_size=sizes,
                             top_pairs=[{'pair_id': pid, 'electrodes': pair_rows[pid]['electrodes'],
                                         'delta': tri.d.value(fam[kind])} for pid, tri in contrib
                                        if tri.d.value(fam[kind]) is not None][:TOP_CONTRIBUTORS]))
        layers[kind] = rows
    layers['cno'] = [dict({'layer': layer}, **cno_layer[layer].out(fam['pixel']),
                          by_size=[dict({'diameter_um': diameter}, **tri.out(fam['pixel']))
                                   for (lay, diameter), tri in sorted(cno_size.items()) if lay == layer])
                     for layer in sorted(cno_layer)]

    # layer x size table: the sizes of one layer add up to the layer value (a size row is never overwritten or lost)
    layer_size_sum = {}
    for kind in ('line', 'pixel', 'cno'):
        entry = {}
        for field in ('a', 'b', 'delta'):
            per_layer = [_check(_osum(s[field] for s in row['by_size']), row[field]) for row in layers[kind]]
            entry[field] = {'status': _worst(c['status'] for c in per_layer) if per_layer else 'passed',
                            'layers': len(per_layer),
                            'max_abs_error': max([c['abs_error'] for c in per_layer if c['abs_error'] is not None] or [0.0])}
        layer_size_sum[kind] = entry
    checks['layer_size_sum'] = layer_size_sum
    states = [e['status'] for e in equations.values()]
    states += [e['status'] for kind in KINDS for e in electrode_sum[kind].values()]
    states += [e['status'] for e in pair_vs_compare2.values()]
    states += [e['status'] for kind in layer_size_sum for e in layer_size_sum[kind].values()]
    checks['status'] = _worst(states)

    status, reasons = 'ok', []
    if checks['status'] == 'failed':
        status, reasons = 'partial', ['ledger_totals_check_failed']
    pairs_out = [_public_pair(r) for kind in KINDS for r in ordered[kind]] + [_public_pair(r) for r in unknown]
    document = {
        'schema_version': SCHEMA_VERSION, 'status': status, 'reasons': reasons,
        'change_kinds': [{'code': code, 'label': label} for code, label in CHANGE_KINDS],
        'electrode_status': [{'code': code, 'label': label} for code, label in STATUS_LABELS],
        'inputs': {
            'compare2_json_sha256': doc_sha256, 'config_sha256': cfg2.config_sha256,
            'ledger_basis': doc['inputs']['ledger_basis'],
            'products': {role: {'model_group': (doc.get('products') or {}).get(role, {}).get('model_group'),
                                'pair_table_hash': (doc.get('products') or {}).get(role, {}).get('pair_table_hash'),
                                'crp': (doc.get('products') or {}).get(role, {}).get('crp')} for role in ROLES},
            'electrode_inventory': inventory},
        'totals': totals,
        'kinds': dict(kinds_doc, change_kind_counts=all_counts),
        'pairs': pairs_out, 'electrodes': electrode_rows, 'cno': cno_rows, 'layers': layers,
        'checks': checks, 'warnings': warnings,
        'unavailable': sorted(unavailable, key=lambda u: (u['scope'], str(u['id']), str(u.get('side', '')))),
    }
    # pexcl/T3: the pair-exclusion blocks of compare2.json, copied as they are; no key when no product left a pair out
    manifests = (doc.get('inputs') or {}).get('manifests') or {}
    blocks = {role: manifests[role]['pair_exclusions'] for role in ROLES
              if isinstance(manifests.get(role), dict) and manifests[role].get('pair_exclusions')}
    if blocks:
        document['inputs']['pair_exclusions'] = blocks
    return document, status, reasons


def _public_pair(row):
    return {key: value for key, value in row.items() if not key.startswith('_')}


def _size_map(item):
    """{(diameter_um, density_layer): lambda} of one item. One item can reach two density layers at one diameter, so
    the density layer is part of the key (an equal key adds up, null stays null)."""
    if item is None:
        return {}
    parts = {}
    for entry in item.get('by_size') or ():
        parts.setdefault((entry['diameter_um'], entry.get('density_layer')), []).append(entry['lambda'])
    return {key: _osum(values) for key, values in parts.items()}


def _size_value(s, item, sizes, diameter):
    """Value of one item at one size: no slope = null; absent item or size = 0; null lambda = null."""
    if s is None:
        return None
    if item is None or diameter not in sizes:
        return 0.0
    return _times(s, sizes[diameter])


# ---- CSV --------------------------------------------------------------------------------------------------------------

def _csv_documents(document):
    pair_fields = ['rank', 'pair_id', 'electrode_1', 'electrode_2', 'kind', 'change_kind', 'present_a', 'present_b',
                   'a', 'b', 'delta', 'pct_a', 'pct_b', 'pct_delta', 'item_count']
    pairs = []
    for row in document['pairs']:
        names = list(row['electrodes']) + ['', '']
        pairs.append({'rank': row['rank'], 'pair_id': row['pair_id'], 'electrode_1': names[0], 'electrode_2': names[1],
                      'kind': row['kind'], 'change_kind': row['change_kind'],
                      'present_a': 'true' if row['present_a'] else 'false',
                      'present_b': 'true' if row['present_b'] else 'false',
                      'a': row['a'], 'b': row['b'], 'delta': row['delta'], 'pct_a': row['pct_a'],
                      'pct_b': row['pct_b'], 'pct_delta': row['pct_delta'], 'item_count': len(row['items'])})
    elec_fields = ['electrode', 'status', 'present_a', 'present_b', 'layers_a', 'layers_b']
    for kind in KINDS:
        elec_fields += [f'{kind}_a', f'{kind}_b', f'{kind}_delta', f'{kind}_pct_delta']
    elec_fields += ['cno_a', 'cno_b', 'cno_delta', 'partners_a', 'partners_b']
    electrodes = []
    for row in document['electrodes']:
        out = {'electrode': row['electrode'], 'status': row['status'],
               'present_a': 'true' if row['present_a'] else 'false',
               'present_b': 'true' if row['present_b'] else 'false',
               'layers_a': ';'.join(row['layers_a']), 'layers_b': ';'.join(row['layers_b']),
               'cno_a': row['cno']['a'], 'cno_b': row['cno']['b'], 'cno_delta': row['cno']['delta'],
               'partners_a': ';'.join(row['partners_a']), 'partners_b': ';'.join(row['partners_b'])}
        for kind in KINDS:
            out.update({f'{kind}_a': row[kind]['a'], f'{kind}_b': row[kind]['b'], f'{kind}_delta': row[kind]['delta'],
                        f'{kind}_pct_delta': row[kind]['pct_delta']})
        electrodes.append(out)
    return [('ledger_pairs.csv', pair_fields, pairs), ('ledger_electrodes.csv', elec_fields, electrodes)]


# ---- output folder (same convention as compare2) ----------------------------------------------------------------------

def _owned_output(out):
    """True for a missing/empty folder or one holding only files `ledger` makes."""
    if not out.exists():
        return True
    if not out.is_dir():
        return False
    for path in sorted(out.iterdir()):
        if path.name.startswith(TMP_PREFIX):
            continue
        if path.name not in OWNED_NAMES:
            return False
        if path.name in ('execution.json', 'error.json'):
            try:
                if json.loads(path.read_text(encoding='utf-8')).get('command') != COMMAND:
                    return False
            except (OSError, ValueError, AttributeError):
                return False
    return True


def _publish(out, files):
    """Write each file into a temporary folder inside out, then os.replace them one by one."""
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=TMP_PREFIX, dir=str(out)))
    try:
        for name, text in files:
            with open(str(tmp / name), 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(text)
        for name, _text in files:
            os.replace(str(tmp / name), str(out / name))
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


def run_ledger(compare2_dir, config, out):
    """Compute and publish the ledger; returns the execution record (exit_code 0 or 3) or raises."""
    started = time.time()
    output = Path(out)
    if not _owned_output(output):
        raise NumericContractError('ledger_output_not_owned', f'{output}: use a new folder or a previous ledger folder')
    cfg2 = load_compare2_config(config)
    doc, doc_sha = _read_compare2(compare2_dir, cfg2)
    document, status, reasons = build_ledger(doc, doc_sha, cfg2)
    texts = [(name, _csv_text(fields, rows)) for name, fields, rows in _csv_documents(document)]
    texts.append(('ledger.json', _json_text(document)))
    exit_code = 0 if status == 'ok' else 3
    record = {'command': COMMAND, 'status': status, 'reasons': reasons,
              'outputs': [str(output / name) for name in ('ledger.json', 'ledger_pairs.csv', 'ledger_electrodes.csv')],
              'compare2_dir': str(compare2_dir), 'config': str(cfg2.config_path),
              'started_at': datetime.fromtimestamp(started, timezone.utc).isoformat(),
              'finished_at': datetime.now(timezone.utc).isoformat(),
              'elapsed_seconds': round(time.time() - started, 3), 'exit_code': exit_code}
    texts.append(('execution.json', _json_text(record)))  # every text exists before the first file is written
    _publish(output, texts)
    error = output / 'error.json'
    if error.exists():
        error.unlink()
    return record


def main_ledger(compare2_dir, config, out, print_result, error_stream=None):
    """CLI body: prints the record, or leaves error.json + execution.json only and returns 2."""
    error_stream = error_stream or sys.stderr
    output = Path(out)
    try:
        record = run_ledger(compare2_dir, config, out)
    except (NumericContractError, OSError, ValueError, TypeError, KeyError) as exc:
        code = getattr(exc, 'code', 'input_or_config_error')
        print(f'오류: {exc}', file=error_stream)
        if code != 'ledger_output_not_owned' and _owned_output(output):
            try:
                for name in OWNED_NAMES - {'error.json', 'execution.json'}:  # a failed run leaves no stale results
                    path = output / name
                    if path.exists():
                        path.unlink()
                now = datetime.now(timezone.utc).isoformat()
                _publish(output, [
                    ('error.json', _json_text({'command': COMMAND, 'code': code, 'detail': str(exc), 'exit_code': 2})),
                    ('execution.json', _json_text({'command': COMMAND, 'status': 'error', 'reasons': [code],
                                                   'outputs': [], 'compare2_dir': str(compare2_dir),
                                                   'config': str(config), 'finished_at': now, 'exit_code': 2}))])
            except Exception as publish_error:  # still exit 2; say why error.json is missing
                print(f'오류: error.json 을 쓰지 못했습니다 (원래 오류 {code}): {publish_error}', file=error_stream)
        return 2
    print_result(record)
    return record['exit_code']
