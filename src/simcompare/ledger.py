"""ledger: the pair / electrode ledger of one compare2 run (`ledger_v1`).

Input: compare2.json (+ the compare2 config it was made with, + the DL1 electrode folder *names*). No mask and no PNG is
read. Every number comes from the compare2 values: item lambda x the secant slope `delta.slopes.<kind>.value`
(docs/plan/ledger_report/README.md sections 5-8, 10, 11).

Null propagation (README 6): a kind (line / pixel) whose two vi or whose slope is missing has no value at all (every A, B,
delta and % of that kind is null, its checks are `not_evaluated`); presence (present / absent) is kept apart from the value.
"""
from __future__ import annotations

import copy
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
_DELTA_NAMES = ('ca_delta', 'f_delta', 'density_delta', 'design_area_delta', 'unit_size_delta')

# change kinds (README 8): code -> label; the order is the display order
CHANGE_KINDS = (
    ('same_pair_change', '같은 쌍 증감'), ('layer_change', '불량 구성 변경'), ('new_pair', '새 쌍'),
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
    # E10: every layer list and layer row follows inputs.layer_order of compare2.json (none: by name, as before)
    raw_order = (doc.get('inputs') or {}).get('layer_order')
    layer_order = list(raw_order) if isinstance(raw_order, list) and all(isinstance(n, str) for n in raw_order) else None
    lk = _layer_key(layer_order or ())
    rebase, rb_items, rb_cno, rb_ok = _rebase_view(doc)

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
    # mrep/T1: the same internal item values split by presence (E1), summed per kind
    split_acc = {kind: {'same': _Acc(), 'new': _Acc(), 'gone': _Acc()} for kind in KINDS}
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
            presence = item_rows[-1]['presence']
            split_acc[ikind][{'both': 'same', 'new': 'new', 'gone': 'gone'}[presence]].add(
                d if presence == 'both' else (b if presence == 'new' else a))
            ends = base.get('endpoints') or [{'electrode_id': e, 'actual_layer': base['analysis_layer']} for e in names]
            for end in ends:
                share = 1.0 / len(ends)
                parts = [None if v is None else v * share for v in (a, b, d)]
                electrode(end['electrode_id'])[ikind].add(*parts)
                electrode_layer(end['electrode_id'], end['actual_layer'], ikind).add(*parts)
            layer_acc[ikind].setdefault(base['analysis_layer'], _Tri()).add(a, b, d)
            pair_layer[ikind].setdefault((base['analysis_layer'], pid), _Tri()).add(a, b, d)
            sizes_a, sizes_b = _size_map(ia), _size_map(ib)
            areas_a, areas_b = _size_area_map(ia), _size_area_map(ib)
            by_size = []
            for size_key in sorted(set(sizes_a) | set(sizes_b), key=lambda k: (k[0], str(k[1]))):
                # key = (diameter, density_layer): one item at one diameter in two density layers never overwrites
                va = _size_value(slope[ikind], ia, sizes_a, size_key)
                vb = _size_value(slope[ikind], ib, sizes_b, size_key)
                size_acc[ikind].setdefault((base['analysis_layer'], size_key[0]), _Tri()).add(va, vb, _sub(vb, va))
                by_size.append({
                    'diameter_um': size_key[0], 'density_layer': size_key[1],
                    'area_a': _size_pick(ia, areas_a, size_key), 'area_b': _size_pick(ib, areas_b, size_key),
                    'lambda_a': _size_pick(ia, sizes_a, size_key), 'lambda_b': _size_pick(ib, sizes_b, size_key),
                    'value_a': va, 'value_b': vb})
            item_rows[-1]['by_size'] = by_size
            # rnarr/T5: B's CA read with A's coefficients (compare2 `rebase`); null when it is not available
            rb_rec = rb_items.get((item_id, ikind)) if rb_ok[ikind] else None
            for name in _DELTA_NAMES if (rebase or {}).get('area_basis') is not None else _DELTA_NAMES[:3]:
                item_rows[-1][name] = None if rb_rec is None else rb_rec.get(name)
            for entry in by_size:
                entry['lambda_b_at_a'] = _lambda_b_at_a(rb_rec, ib is not None, entry['diameter_um'],
                                                        entry['density_layer'])
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
            'layers_a': sorted({i['analysis_layer'] for i in (recs['reference'] or {'items': {}})['items'].values()},
                               key=lk),
            'layers_b': sorted({i['analysis_layer'] for i in (recs['target'] or {'items': {}})['items'].values()},
                               key=lk),
            'items': item_rows, 'breakdown': _pair_breakdown(item_rows, d_pub),
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
        # mrep/T1: what a changed defect composition added / removed (same key sets as the layer_change rule above)
        row['composition'] = {'added': [], 'removed': []}
        if code == 'layer_change':
            by_id = {item['item_id']: item for item in row['items']}
            for field, ids in (('added', row['_keys']['target'] - row['_keys']['reference']),
                               ('removed', row['_keys']['reference'] - row['_keys']['target'])):
                row['composition'][field] = sorted(
                    (_item_description(by_id[i], lk) for i in ids if i in by_id),
                    key=lambda d: (d['failure_type'], d['layers'], d['item_id']))

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
        rb_rec = rb_cno.get(key) if rb_ok['pixel'] else None
        for name in _DELTA_NAMES if (rebase or {}).get('area_basis') is not None else _DELTA_NAMES[:3]:
            cno_rows[-1][name] = None if rb_rec is None else rb_rec.get(name)
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
                layers_ab[role] = sorted(inv['layers'].get(name, []), key=lk)
            else:
                layers_ab[role] = sorted(elec_layers[role].get(name, ()), key=lk)
        row = {'electrode': name, 'status': status, 'present_a': in_a, 'present_b': in_b,
               'layers_a': layers_ab['reference'], 'layers_b': layers_ab['target']}
        # mrep/T1: per-layer cell state from the same layer lists (DL1 inventory; observed = unverified)
        only_a = sorted(set(row['layers_a']) - set(row['layers_b']), key=lk)
        only_b = sorted(set(row['layers_b']) - set(row['layers_a']), key=lk)
        row['layer_state'] = {layer: ('both' if layer in row['layers_a'] and layer in row['layers_b']
                                      else ('a_only' if layer in row['layers_a'] else 'b_only'))
                              for layer in sorted(set(row['layers_a']) | set(row['layers_b']), key=lk)}
        row['layer_state_status'] = 'verified' if complete else 'unverified'
        row['moved'] = (bool(in_a and in_b and only_a and only_b)) if complete else None
        for kind in KINDS:
            out = acc[kind].out(fam[kind])
            out.update(pct_a=_pct(out['a'], side_a.vi[kind]), pct_b=_pct(out['b'], side_a.vi[kind]),
                       pct_delta=_pct(out['delta'], side_a.vi[kind]))
            row[kind] = out
        row['cno'] = acc['cno'].out(fam['pixel'])
        row['partners_a'] = sorted(acc['partners']['reference'])
        row['partners_b'] = sorted(acc['partners']['target'])
        row['layers'] = {layer: {kind: acc['layers'][layer][kind].out(fam[kind]) for kind in KINDS}
                         for layer in sorted(acc['layers'], key=lk)}
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

    # mrep/T1: item breakdown per kind (the internal item values behind totals.<kind>.items, E1) and its checks
    item_breakdown = {}
    for kind in KINDS:
        parts = {name: split_acc[kind][name].value(fam[kind]) for name in ('same', 'new', 'gone')}
        parts['cno'] = (cno_total.d.value(fam['pixel']) if kind == 'pixel' else (0.0 if fam[kind] else None))
        parts['crp'] = crp_d if kind == 'pixel' else (0.0 if fam[kind] else None)
        if totals[kind]['items']['delta'] is None:      # R1:1: all five null or none (never a part-null breakdown)
            parts = {name: None for name in parts}
        item_breakdown[kind] = parts
    kind_checks = {}
    for kind in KINDS:
        parts = item_breakdown[kind]
        core = _osum([parts['same'], parts['new'], None if parts['gone'] is None else -parts['gone']])
        items_check = _check(core, totals[kind]['items']['delta'])
        full_check = _check(_osum([core, parts['cno'], parts['crp']]), totals[kind]['delta'])
        kind_checks[kind] = {'status': _worst([items_check['status'], full_check['status']]),
                             'items': items_check, 'total': full_check}
    pair_states = []
    for row in pair_rows.values():
        brk = row['breakdown']
        if brk['status'] != 'ok':
            pair_states.append('not_evaluated')
        else:
            pair_states.append(_check(math.fsum([brk['same_delta'], brk['new_value'], -brk['gone_value']]),
                                      row['delta'])['status'])
    checks['item_breakdown'] = {
        'pair': {'status': _worst(pair_states), 'checked': sum(1 for s in pair_states if s != 'not_evaluated'),
                 'failed': sum(1 for s in pair_states if s == 'failed'),
                 'not_evaluated': sum(1 for s in pair_states if s == 'not_evaluated')},
        'kind': kind_checks}

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
            'value_pending_rows': len(rows_delta) - len(known), 'item_breakdown': item_breakdown[kind]}
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
        for layer in sorted(layer_acc[kind], key=lk):
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
                     for layer in sorted(cno_layer, key=lk)]

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

    # rnarr/T5: the narrative summary (rebase split, design difference, cause-candidate bundles, concentration)
    summary, summary_checks = _build_summary(
        doc, rebase, fam, {kind: side_a.vi[kind] for kind in KINDS}, totals, crp_d, pair_rows, cno_rows,
        electrode_rows, lk)
    checks.update({name: value for name, value in summary_checks.items() if name != 'status'})
    status, reasons = 'ok', []
    if checks['status'] == 'failed':
        status, reasons = 'partial', ['ledger_totals_check_failed']
    elif summary_checks['status'] == 'failed':   # its own status: the existing checks.status stays as it was
        status, reasons = 'partial', ['ledger_summary_check_failed']
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
            'electrode_inventory': inventory,
            'diameter_filter': _diameter_filter_block(doc)},
        'totals': totals,
        'kinds': dict(kinds_doc, change_kind_counts=all_counts),
        'pairs': pairs_out, 'electrodes': electrode_rows, 'cno': cno_rows, 'layers': layers,
        'checks': checks, 'warnings': warnings,
        'unavailable': sorted(unavailable, key=lambda u: (u['scope'], str(u['id']), str(u.get('side', '')))),
        'summary': summary,
    }
    if layer_order is not None:
        document['inputs']['layer_order'] = layer_order
    for role in ROLES:   # the coefficients of each product (copied from compare2.json `products`, nothing recalculated)
        document['inputs']['products'][role].update(_product_coefficients((doc.get('products') or {}).get(role) or {}))
    # pexcl/T3: the pair-exclusion blocks of compare2.json, copied as they are; no key when no product left a pair out
    manifests = (doc.get('inputs') or {}).get('manifests') or {}
    blocks = {role: manifests[role]['pair_exclusions'] for role in ROLES
              if isinstance(manifests.get(role), dict) and manifests[role].get('pair_exclusions')}
    if blocks:
        document['inputs']['pair_exclusions'] = blocks
    return document, status, reasons


def _diameter_filter_block(doc):
    """rtrim/T3: the particle size range of the 02 result, copied (nothing is recalculated). Without a filter in compare2.json
    the range is every size: {'mode': 'all'}."""
    flt = (doc.get('inputs') or {}).get('diameter_filter')
    if not isinstance(flt, dict):
        return {'mode': 'all'}
    block = copy.deepcopy(flt)
    if doc.get('formula_basis') is not None:
        block['formula_basis'] = doc['formula_basis']
    return block


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


def _size_area_map(item):
    """{(diameter_um, density_layer): area_share_um2} of one item, summed per key like `_size_map`."""
    if item is None:
        return {}
    parts = {}
    for entry in item.get('by_size') or ():
        parts.setdefault((entry['diameter_um'], entry.get('density_layer')), []).append(entry.get('area_share_um2'))
    return {key: _osum(values) for key, values in parts.items()}


def _size_pick(item, mapping, key):
    """One shown number of a size row: an absent item or size is a true 0 (same rule as `_size_value`)."""
    return 0.0 if item is None or key not in mapping else mapping[key]


def _pair_breakdown(item_rows, delta):
    """same_delta + new_value - gone_value = pair delta. No public pair value (waiting, no slope) = nothing to split."""
    if delta is None:
        return {'same_delta': None, 'new_value': None, 'gone_value': None, 'n_both': None, 'n_new': None,
                'n_gone': None, 'status': 'not_evaluated'}
    both = [r for r in item_rows if r['presence'] == 'both']
    new = [r for r in item_rows if r['presence'] == 'new']
    gone = [r for r in item_rows if r['presence'] == 'gone']
    return {'same_delta': _osum(r['delta'] for r in both), 'new_value': _osum(r['b'] for r in new),
            'gone_value': _osum(r['a'] for r in gone), 'n_both': len(both), 'n_new': len(new),
            'n_gone': len(gone), 'status': 'ok'}


def _item_description(item, layer_key=str):
    """Short description of one item for the composition lists: defect way + endpoint layers."""
    layers = sorted({e['actual_layer'] for e in item.get('endpoints') or ()}, key=layer_key) or [item['analysis_layer']]
    way = {'lateral_short': '측면', 'vertical_short': '상하'}.get(item['failure_type'], item['failure_type'])
    return {'item_id': item['item_id'], 'failure_type': item['failure_type'], 'layers': layers,
            'text': f'{way} {"-".join(layers)}'}


def _size_value(s, item, sizes, diameter):
    """Value of one item at one size: no slope = null; absent item or size = 0; null lambda = null."""
    if s is None:
        return None
    if item is None or diameter not in sizes:
        return 0.0
    return _times(s, sizes[diameter])


# ---- rnarr/T5: summary (rebase split, design difference, cause-candidate bundles, concentration) -------------------------
# Plan: docs of Run 20261118-report-narrative, PLAN-v3 3, v3.1 E1/E4/E10/E11, v3.2 G1-G3 and H1-H6.

BUNDLE_RULE = 'bundle_assign_v2'
BUNDLE_VERSION = 2       # L5: summary.bundles.version; a ledger without it is the gross (|delta|) reading
HUB_MIN = 3              # (electrode, layer) with this many partners whose pair changed (any sign) = a hub (provisional)
HUB_MIN_FRACTION = 0.5   # ... and at least this fraction of all its partners changed (user decision 2026-11-19)
HUB_MIN_SHARE = 0.0      # ... a partner counts only if its |delta| >= this x G of the kind (0.0 = off)
HUB_SENSITIVITY_FRACTIONS = (0.0, 0.3, 0.5)
HUB_SENSITIVITY_SHARES = (0.0, 0.01)
TYPES = ('electrode_new', 'electrode_removed', 'layer_change', 'hub', 'pair', 'self', 'cno')
CARD_COUNT = 6
OPPOSITE_CARD_COUNT = 3
CONFLICT_SHARE = 0.02
K_SHARE, K_EPS = 0.8, 1e-12
TOP_GROUPS = 10
TOP_DESIGN = 5
CONCENTRATED_K80, DISPERSED_RATIO, MIN_ATOMS = 3, 0.3, 8
CONC_STAGES = ('division', 'layer', 'electrode', 'pair', 'item')
DESIGN_SEGMENTS = (('design', 'ca'), ('resolution', 'f'), ('crp', 'crp'), ('density', 'density'))
SUMMARY_RULES = {
    'bundle_rule': BUNDLE_RULE, 'hub_min': HUB_MIN, 'hub_min_fraction': HUB_MIN_FRACTION, 'hub_min_share': HUB_MIN_SHARE,
    'card_count': CARD_COUNT,
    'opposite_card_count': OPPOSITE_CARD_COUNT, 'conflict_share': CONFLICT_SHARE,
    'k_share': K_SHARE, 'concentrated_k80_max': CONCENTRATED_K80, 'dispersed_k80_ratio': DISPERSED_RATIO,
    'min_atoms': MIN_ATOMS, 'top_groups': TOP_GROUPS, 'top_design': TOP_DESIGN,
    'no_change_abs': NO_CHANGE_ABS, 'no_change_rel': NO_CHANGE_REL,
    'negligible': {'abs': NO_CHANGE_ABS, 'rel': NO_CHANGE_REL, 'rule': '|x| <= abs + rel x |vi_A(kind)|'}}


def _negligible_limit(vi_a):
    """R10:1: a design difference or a bundle at or below this is read as 0 (kept in sums and checks)."""
    return NO_CHANGE_ABS + NO_CHANGE_REL * abs(vi_a or 0.0)


def _layer_key(order):
    """Sort key of a layer name: its place in `order`, then the other layers by name (E10)."""
    rank = {name: index for index, name in enumerate(order)}
    return lambda name: (rank.get(name, len(rank)), str(name))


def _rebase_view(doc):
    """(rebase block or None, {(item_id, class): rec}, {(electrode, density_layer): rec}, {kind: available})."""
    rebase = doc.get('rebase')
    if not isinstance(rebase, dict):
        return None, {}, {}, {kind: False for kind in KINDS}
    states = rebase.get('states') or {}
    ok = {kind: (states.get(kind) or {}).get('status') == 'available' for kind in KINDS}
    items = {(rec['item_id'], rec['class']): rec for rec in rebase.get('items') or ()}
    cno = {(rec['electrode_id'], rec['density_layer']): rec for rec in rebase.get('cno') or ()}
    return rebase, items, cno, ok


def _lambda_b_at_a(rb_rec, in_b, diameter, density_layer):
    """lambda of B's CA with A's coefficients for one size row (S1): an absent B item is a true 0."""
    if not in_b:
        return 0.0
    if rb_rec is None:
        return None
    values = [row.get('lambda_s1') for row in rb_rec.get('by_size') or ()
              if row.get('diameter_um') == diameter and row.get('density_layer') == density_layer]
    return _osum(values) if values else None


def _product_coefficients(product):
    out = {}
    for key in ('resolution', 'effective_resolution', 'glass_um2', 'weights', 'unit_size_um'):
        if key in product:
            out[key] = copy.deepcopy(product[key])
    if any(key in product for key in ('F_line', 'F_pixel', 'F_cno')):
        out['F'] = {'line': product.get('F_line'), 'pixel': product.get('F_pixel'), 'cno': product.get('F_cno')}
    files = product.get('density_files')
    if isinstance(files, dict):
        out['density_sha256'] = {kind: (files.get(kind) or {}).get('sha256') for kind in KINDS}
    return out


def _item_pairs(pair_rows, kind):
    """[(pair_row, [item row of this class])] in pair id order."""
    out = []
    for pid in sorted(pair_rows):
        rows = [item for item in pair_rows[pid]['items'] if item['class'] == kind]
        if rows:
            out.append((pair_rows[pid], rows))
    return out


def _rebase_summary(rebase, basis, pair_rows, cno_rows, totals):
    """summary.rebase and checks.rebase: the four segments (CA, F, CRP, density) of each kind, checked three ways."""
    if rebase is None:
        return ({'status': 'not_available', 'reasons': ['compare2_without_rebase']}, {'status': 'not_evaluated'})
    summary = {'status': rebase.get('status'), 'reasons': list(rebase.get('reasons') or ()), 'basis': basis,
               'ratios': copy.deepcopy((rebase.get('coefficients') or {}).get('ratios'))}
    checks, states = {}, []
    for kind in KINDS:
        state = (rebase.get('states') or {}).get(kind) or {'status': 'unavailable', 'reasons': []}
        entry = {'status': state.get('status'), 'reasons': list(state.get('reasons') or ()),
                 'delta': totals[kind]['delta'], 'segments': None}
        if state.get('status') != 'available':
            summary[kind] = entry
            checks[kind] = {'status': 'not_evaluated'}
            states.append('not_evaluated')
            continue
        segments = state.get('segments') or {}
        values = {name: (segments[name]['sum'] if name in segments else 0.0) for _label, name in DESIGN_SEGMENTS}
        entry['states'] = {name: copy.deepcopy(state[name]) for name in ('S0', 'S1a', 'S1', 'S2', 'S3', 'S4')
                           if name in state}
        extra = [name for name in ('design_area', 'unit_size') if name in segments]
        per = {}
        for name in extra:        # K3: the old `ca` segment is also split in two; these two never join `values`
            parts = [item[f'{name}_delta'] for _row, rows in _item_pairs(pair_rows, kind) for item in rows]
            if kind == 'pixel':
                parts += [row[f'{name}_delta'] for row in cno_rows]
            per[name] = _check(_osum(parts), segments[name]['delta_vi'])
        entry['segments'] = dict(values, **{name: segments[name]['sum'] for name in extra})
        for _label, name in DESIGN_SEGMENTS:
            if name == 'crp':
                per[name] = _check(values[name], segments[name]['delta_vi'] if name in segments else 0.0)
                continue
            parts = [item[f'{name}_delta'] for _row, rows in _item_pairs(pair_rows, kind) for item in rows]
            if kind == 'pixel':
                parts += [row[f'{name}_delta'] for row in cno_rows]
            per[name] = _check(_osum(parts), segments[name]['delta_vi'])
        total = _check(_osum(values.values()), totals[kind]['delta'])
        checks[kind] = {'status': _worst([c['status'] for c in per.values()] + [total['status']]),
                        'segments': per, 'total': total}
        states.append(checks[kind]['status'])
        summary[kind] = entry
    checks['status'] = _worst(states)
    return summary, checks


def _design_summary(kind, rebase, basis_status, pair_rows, cno_rows, vi_a, totals, lk=str):
    """G1: the four segments as design / resolution / CRP / density differences, as a share of vi_A, the top pairs."""
    if rebase is None:
        return {'status': 'not_available', 'basis': 'unit', 'reasons': ['compare2_without_rebase'], 'segments': None,
                'a_relative': None, 'top_increase': [], 'top_decrease': [], 'missing_cells': []}
    cells = {}
    for miss in rebase.get('missing') or ():     # R9:2: compare2 lists item x size rows; sum them per cell
        if miss.get('kind') != kind:             # R10:3: and per scope (item / cno / cell)
            continue
        scope = miss.get('scope')
        cell = cells.setdefault((miss.get('density_layer'), miss.get('diameter_um'), str(scope)),
                                {'scope': scope, 'area': [], 'share': [], 'items': set(), 'cno': set()})
        cell['area'].append(miss.get('area_b_um2'))
        cell['share'].append(miss.get('lambda_b_share'))
        if miss.get('item_id') is not None:
            cell['items'].add(miss['item_id'])
        if miss.get('electrode_id') is not None:
            cell['cno'].add(miss['electrode_id'])
    missing_cells = []
    for (layer, diameter, _s), c in sorted(cells.items(), key=lambda kv: (lk(kv[0][0]), kv[0][1] or 0.0, kv[0][2])):
        shares = [v for v in c['share'] if v is not None]
        missing_cells.append({'scope': c['scope'], 'density_layer': layer, 'diameter_um': diameter,
                              'area_b_um2': _osum(c['area']), 'lambda_b_share': math.fsum(shares) if shares else None,
                              'share_partial': len(shares) != len(c['share']),
                              'item_count': len(c['items']), 'cno_count': len(c['cno'])})
    if basis_status['status'] != 'available':
        return {'status': 'unavailable', 'basis': 'unit', 'reasons': list(basis_status['reasons']), 'segments': None,
                'a_relative': None, 'top_increase': [], 'top_decrease': [], 'missing_cells': missing_cells}
    if ((rebase.get('check') or {}).get(kind) or {}).get('status') != 'passed':   # R9:3
        return {'status': 'not_evaluated', 'basis': 'unit', 'reasons': ['rebase_check_failed'], 'segments': None,
                'a_relative': None, 'top_increase': [], 'top_decrease': [], 'missing_cells': missing_cells}
    values = basis_status['segments']
    segments = {label: values[name] for label, name in DESIGN_SEGMENTS}
    # K3 / L6: `area` when compare2 split the old design segment by unit area (both products have unit_size_um and this
    # kind converted); the report adds design_area + unit_size or ca, never both.
    area = ((rebase.get('area_basis') or {}).get('status') == 'available'
            and 'design_area' in values and 'unit_size' in values)
    basis = 'area' if area else 'unit'
    item_key = 'design_area_delta' if area else 'ca_delta'
    if area:
        segments['design_area'], segments['unit_size'] = values['design_area'], values['unit_size']
    ratios = ((rebase.get('coefficients') or {}).get('ratios') or {}).get(kind) or {}
    differs = [label for label, key in (('rule', 'r_rule'), ('glass', 'r_G'), ('weight', 'r_M'))
               if ratios.get(key) is not None and abs(ratios[key] - 1.0) > 1e-12]
    entries = []
    for row, rows in _item_pairs(pair_rows, kind):
        design, actual = _osum(item[item_key] for item in rows), _osum(item['delta'] for item in rows)
        entries.append({'key': row['pair_id'], 'pair_id': row['pair_id'], 'electrodes': list(row['electrodes']),
                        'design_delta': design, 'actual_delta': actual, 'design_a_relative': _pct(design, vi_a),
                        'actual_a_relative': _pct(actual, vi_a)})
    if kind == 'pixel':
        for row in cno_rows:
            key = f'CNO:{row["electrode_id"]}@{row["density_layer"]}'
            entries.append({'key': key, 'pair_id': None, 'electrodes': [row['electrode_id']],
                            'design_delta': row[item_key], 'actual_delta': row['delta'],
                            'design_a_relative': _pct(row[item_key], vi_a),
                            'actual_a_relative': _pct(row['delta'], vi_a)})
    limit = _negligible_limit(vi_a)       # R10:1: a design difference inside the limit is 0: no sign, not listed
    entries = [e for e in entries if e['design_delta'] is not None and abs(e['design_delta']) > limit]
    up = sorted((e for e in entries if e['design_delta'] > 0), key=lambda e: (-e['design_delta'], e['key']))
    down = sorted((e for e in entries if e['design_delta'] < 0), key=lambda e: (e['design_delta'], e['key']))
    negligible = [label for label in list(segments)
                  if segments[label] is not None and abs(segments[label]) <= limit]
    outside = not _no_change(0.0, segments['crp']) or not _no_change(0.0, segments['density'])
    return {'status': 'available', 'basis': basis, 'reasons': [], 'vi_a': vi_a, 'delta': totals[kind]['delta'],
            'delta_a_relative': _pct(totals[kind]['delta'], vi_a),
            'segments': segments, 'negligible': negligible,
            'a_relative': {label: _pct(value, vi_a) for label, value in segments.items()},
            'names': {'design': '설계 차이', 'resolution': '해상도·조건 차이' if differs else '해상도 차이',
                      'condition_differences': differs, 'show_outside_conditions': outside},
            'top_increase': up[:TOP_DESIGN], 'top_decrease': down[:TOP_DESIGN], 'missing_cells': missing_cells}


# ---- atoms ----------------------------------------------------------------------------------------------------------

def _atoms(pair_rows, cno_rows):
    """Every actual-value atom of both kinds: pair items (sizes added) and CNO lines (kind pixel)."""
    atoms = []
    for pid in sorted(pair_rows):
        row = pair_rows[pid]
        for item in row['items']:
            ends = ([(e['electrode_id'], e['actual_layer']) for e in item['endpoints']] or
                    [(name, item['analysis_layer']) for name in row['electrodes']])
            atoms.append({'id': item['item_id'], 'kind': item['class'], 'pair_id': pid, 'cno': False,
                          'layer': item['analysis_layer'], 'ends': ends, 'a': item['a'], 'b': item['b'],
                          'd': item['delta'], 'presence': item['presence']})
    for row in cno_rows:
        name, layer = row['electrode_id'], row['density_layer']
        atoms.append({'id': f'CNO:{name}@{layer}', 'kind': 'pixel', 'pair_id': None, 'cno': True, 'layer': layer,
                      'ends': [(name, layer)], 'a': row['a'], 'b': row['b'], 'd': row['delta'],
                      'presence': row['presence']})
    return atoms


def _kind_status(kind, atoms, computable, vi_a):
    """(status, reasons, G): not_evaluated (kind not computable / a null atom), no_change, ok."""
    mine = [a for a in atoms if a['kind'] == kind]
    if not computable:
        return 'not_evaluated', ['kind_not_computable'], None
    if any(a['d'] is None for a in mine):
        return 'not_evaluated', ['atom_null'], None
    g = math.fsum(abs(a['d']) for a in mine)
    return ('no_change' if g <= NO_CHANGE_ABS + NO_CHANGE_REL * abs(vi_a or 0.0) else 'ok'), [], g


# ---- G2: cause-candidate bundles ------------------------------------------------------------------------------------

def _bundle_id(key, length):
    text = json.dumps(list(key), ensure_ascii=False, separators=(',', ':'))
    return 'bd-' + hashlib.sha256(text.encode('utf-8')).hexdigest()[:length]


def _bundle_summary(atoms, kstat, erows, pair_rows, crp, totals, lk, vi_a=None):
    """summary.bundles and checks.bundles. `kstat[kind] = (status, reasons, G)`. Atom order never changes the result.

    rnarr/T11 (PLAN-v3.3 K1 K2 L1-L5): two assignment stages (non-CNO atoms, then CNO by electrode attributes), electrode
    bundles over all layers, a sign-free hub, direction based ranking and a per-type sum."""
    live = [a for a in atoms if kstat[a['kind']][0] != 'not_evaluated']
    vi_a = vi_a or {}
    limit = {kind: _negligible_limit(vi_a.get(kind)) for kind in KINDS}
    hub_min, hub_fraction, hub_share = HUB_MIN, HUB_MIN_FRACTION, HUB_MIN_SHARE
    changed_states = ('a_only', 'b_only')

    def status_of(electrode):
        row = erows.get(electrode)
        return 'unknown' if row is None else row.get('status', 'common')

    def state_of(electrode, layer):
        """(state, unknown, unverified): an unverified electrode or a missing key is read as 'both' (H1)."""
        row = erows.get(electrode)
        if row is None:
            return 'both', True, False
        if row.get('layer_state_status') != 'verified':
            return 'both', False, True
        state = (row.get('layer_state') or {}).get(layer)
        return ('both', True, False) if state is None else (state, False, False)

    def layer_changed(electrode, layer):
        return state_of(electrode, layer)[0] in changed_states

    def changed_layers(electrode):
        row = erows.get(electrode)
        if row is None or row.get('layer_state_status') != 'verified':
            return []
        return sorted((l for l, s in (row.get('layer_state') or {}).items() if s in changed_states), key=lk)

    for a in live:
        a['tol'] = _no_change(a['a'], a['b'])
        a['moved_enough'] = abs(a['d']) > limit[a['kind']]
        a['names'] = sorted({name for name, _layer in a['ends']})
        a['two'] = None
        if not a['cno'] and len(a['names']) == 2:
            layer_of = {}
            for name, layer in sorted(a['ends'], key=lambda end: (end[0], lk(end[1]))):
                layer_of.setdefault(name, layer)
            a['two'] = [(name, layer_of[name]) for name in a['names']]

    # fixed before any assignment (L4): per (electrode, endpoint layer) every partner of an eligible atom and the
    # partners whose atom really changed (not inside the tolerance, |delta| above the negligible limit)
    hubs = {}
    for a in live:
        two = a['two']
        if two is None or any(status_of(n) in ('new', 'removed') or state_of(n, l)[0] != 'both' for n, l in two):
            continue
        changed = not a['tol'] and a['moved_enough']
        for i in (0, 1):
            slot = hubs.setdefault((two[i][0], two[i][1]), {'all': set(), 'by': {}})
            slot['all'].add(two[1 - i][0])
            if changed:
                part = slot['by'].setdefault(two[1 - i][0], {})
                entry = part.setdefault(a['kind'], [])
                entry.append((abs(a['d']), a['id']))

    def judge(fraction, share):
        """{(electrode, layer): {partner: {atom id}}} of the changed partners (|delta| of the partner >= share x G of
        the kind), and the hub test of that setting. A partner counts when its summed |delta| of one kind reaches
        `share` x G(kind); share 0 keeps every partner with a changed atom."""
        changed = {}
        for end, slot in hubs.items():
            mine = {}
            for partner, per_kind in slot['by'].items():
                if any(math.fsum(v for v, _i in rows) >= share * (g[k] or 0.0) for k, rows in per_kind.items()):
                    mine[partner] = {i for rows in per_kind.values() for _v, i in rows}
            changed[end] = mine

        def count(end):
            return len(changed.get((end[0], end[1]), ()))

        def hub(end):
            slot = hubs.get((end[0], end[1]))
            if slot is None:
                return False
            number = count(end)
            return number >= hub_min and number / len(slot['all']) >= fraction
        return changed, count, hub

    g = {kind: kstat[kind][2] for kind in KINDS}
    judged, n_changed, is_hub = judge(hub_fraction, hub_share)

    def assign(a, n_changed=n_changed, is_hub=is_hub):
        """(bundle key, why) of one atom."""
        if a['cno']:      # stage B: the electrode attributes decide, never the bundles that exist
            name, layer = a['ends'][0]
            if status_of(name) in ('new', 'removed'):
                return ('ele', name), None
            if changed_layers(name):
                return ('lyr', name), None
            return ('cno', name, layer), None
        names = a['names']
        pair = ('p', a['pair_id'], a['layer'])
        if len(names) >= 3:
            return pair, 'multi'
        if len(names) == 1:
            name = names[0]
            if status_of(name) in ('new', 'removed'):
                return ('ele', name), None
            if any(layer_changed(name, layer) for _n, layer in a['ends']):
                return ('lyr', name), None
            return ('self', name, a['layer']), None
        two = a['two']
        new_removed = [i for i in (0, 1) if status_of(two[i][0]) in ('new', 'removed')]
        if len(new_removed) == 1:
            return ('ele', two[new_removed[0]][0]), None
        if len(new_removed) == 2:
            return pair, 'both_new_removed'
        flipped = [i for i in (0, 1) if layer_changed(*two[i])]
        if len(flipped) == 1:
            return ('lyr', two[flipped[0]][0]), None
        if len(flipped) == 2:
            return pair, 'both'
        h0, h1 = is_hub(two[0]), is_hub(two[1])
        if h0 and h1:
            n0, n1 = n_changed(two[0]), n_changed(two[1])
            if n0 == n1:
                return pair, 'hubs'
            h0, h1 = n0 > n1, n1 > n0
        if h0:
            return ('hub', two[0][0], two[0][1]), None
        if h1:
            return ('hub', two[1][0], two[1][1]), None
        return pair, 'nohub'

    buckets, assigned_to, assignments = {}, {}, 0
    for a in sorted(live, key=lambda x: x['id']):
        key, why = assign(a)
        assignments += 1
        assigned_to[a['id']] = key
        bucket = buckets.setdefault(key, {'key': key, 'atoms': [], 'why': set(), 'unknown': False, 'unverified': False})
        bucket['atoms'].append(a)
        if why:
            bucket['why'].add(why)
        owned = set(a['names']) if key[0] == 'p' else {key[1]}
        for end in a['ends']:
            if end[0] in owned:
                _state, unknown, unverified = state_of(*end)
                bucket['unknown'] = bucket['unknown'] or unknown
                bucket['unverified'] = bucket['unverified'] or unverified

    ids = {key: _bundle_id(key, 16) for key in buckets}
    if len(set(ids.values())) != len(ids):
        ids = {key: _bundle_id(key, 24) for key in buckets}
    id_unique = len(set(ids.values())) == len(ids)
    pair_delta = {pid: row['delta'] for pid, row in pair_rows.items()}

    # partners that took part in a hub judgement but went to another bundle (R8:7)
    elsewhere = {}
    for (name, layer) in hubs:
        if not is_hub((name, layer)):
            continue
        here = ('hub', name, layer)
        for partner, atom_ids in judged[(name, layer)].items():
            for atom_id in atom_ids:
                if assigned_to[atom_id] != here:
                    elsewhere.setdefault(here, set()).add((partner, ids[assigned_to[atom_id]]))

    # how much the hub setting matters: every setting of the grid assigns the atoms again, apart from the real
    # assignment above (default setting only)
    sensitivity = []
    for fraction in HUB_SENSITIVITY_FRACTIONS:
        for share in HUB_SENSITIVITY_SHARES:
            _c, count_s, hub_s = judge(fraction, share)
            per_kind = {kind: {'hubs': set(), 'atoms': 0, 'abs': []} for kind in KINDS}
            for a in sorted(live, key=lambda x: x['id']):
                key, _why = assign(a, count_s, hub_s)
                if key[0] == 'hub':
                    row = per_kind[a['kind']]
                    row['hubs'].add((key[1], key[2]))
                    row['atoms'] += 1
                    row['abs'].append(abs(a['d']))
            sensitivity.append({'hub_min_fraction': fraction, 'hub_min_share': share, 'kinds': {
                kind: (None if kstat[kind][0] == 'not_evaluated' else
                       {'hubs': len(per_kind[kind]['hubs']), 'atoms': per_kind[kind]['atoms'],
                        'share': (math.fsum(per_kind[kind]['abs']) / g[kind]) if g[kind] else None})
                for kind in KINDS}})

    g = {kind: kstat[kind][2] for kind in KINDS}
    blist, nets = [], {kind: [] for kind in KINDS}
    for key, bucket in buckets.items():
        out = {'id': ids[key], 'key': list(key), 'why': sorted(bucket['why']),
               'layer_state_unverified': bucket['unverified'], 'layer_state_unknown': bucket['unknown']}
        kinds = {}
        for kind in KINDS:
            status = kstat[kind][0]
            mine = [a for a in bucket['atoms'] if a['kind'] == kind]
            if status == 'not_evaluated':
                kinds[kind] = {'net': None, 'abs': None, 'share': None, 'increase': None, 'decrease': None,
                               'atoms': None, 'sign': None, 'cancelling': None, 'internal_cancel': None,
                               'direction_share': None}
                continue
            net, total = math.fsum(a['d'] for a in mine), math.fsum(abs(a['d']) for a in mine)
            nets[kind].append(net)
            kinds[kind] = {'net': net, 'abs': total, 'share': (total / g[kind]) if status == 'ok' else None,
                           'increase': sum(1 for a in mine if a['d'] > 0), 'decrease': sum(1 for a in mine if a['d'] < 0),
                           'atoms': len(mine),
                           'sign': ('none' if total == 0.0 else 'increase' if net > 0 else 'decrease' if net < 0
                                    else 'zero'),
                           'cancelling': abs(net) <= limit[kind] and total > limit[kind],
                           'internal_cancel': total - abs(net), 'direction_share': None}
        out['kinds'] = kinds
        out['_abs'] = sum(1 for kind in KINDS if (kinds[kind]['abs'] or 0.0) > 0.0)
        blist.append((key, bucket, out))

    final = []
    for key, bucket, out in blist:
        if not out.pop('_abs'):       # nothing changed in this bundle (every |delta| is exactly 0): counted, not listed
            continue
        kinds, atoms_here = out['kinds'], bucket['atoms']
        out['atoms'] = {'increase': sum(1 for a in atoms_here if a['d'] > 0),
                        'decrease': sum(1 for a in atoms_here if a['d'] < 0), 'count': len(atoms_here)}
        pair_atoms = [a for a in atoms_here if not a['cno']]
        out['independent_cno'] = key[0] == 'cno'
        if key[0] == 'p':
            out.update(type='pair', pair_id=key[1], layer=key[2], layers=[key[2]],
                       electrodes=list(pair_rows[key[1]]['electrodes']), electrode=None)
        else:
            out.update(electrode=key[1], pair_id=None, electrodes=None)
            if key[0] in ('ele', 'lyr'):
                # every layer the electrode's atoms sit on (its own end point), one row per layer (K2)
                per_layer = {}
                for a in atoms_here:
                    own = [layer for name, layer in a['ends'] if name == key[1]] or [a['layer']]
                    layer = sorted(own, key=lk)[0]
                    row = per_layer.setdefault(layer, {'line': [], 'pixel': [], 'cno': []})
                    row[a['kind']].append(a['d'])
                    if a['cno']:
                        row['cno'].append(a['d'])
                layer_rows = [{'layer': layer, 'state': state_of(key[1], layer)[0],
                               'line': math.fsum(per_layer[layer]['line']) if per_layer[layer]['line'] else None,
                               'pixel': math.fsum(per_layer[layer]['pixel']) if per_layer[layer]['pixel'] else None,
                               'cno': math.fsum(per_layer[layer]['cno']) if per_layer[layer]['cno'] else None}
                              for layer in sorted(per_layer, key=lk)]
                out.update(layer=None, layers=[r['layer'] for r in layer_rows], layer_rows=layer_rows)
                if key[0] == 'ele':
                    out['type'] = 'electrode_new' if status_of(key[1]) == 'new' else 'electrode_removed'
                else:
                    states = (erows.get(key[1]) or {}).get('layer_state') or {}
                    out.update(type='layer_change',
                               **{'from': sorted((l for l, s in states.items() if s == 'a_only'), key=lk),
                                  'to': sorted((l for l, s in states.items() if s == 'b_only'), key=lk)})
            else:
                out.update(layer=key[2], layers=[key[2]], type=key[0])      # hub | self | cno
        # partner lines (one per kind, pair or CNO, layer) and the largest partner per kind
        rows, partner_abs = {}, {kind: {} for kind in KINDS}
        for a in atoms_here:
            partner = None
            if key[0] != 'p' and not a['cno'] and len(a['names']) == 2:
                partner = a['names'][0] if a['names'][1] == key[1] else a['names'][1]
            rk = (a['kind'], 'CNO' if a['cno'] else a['pair_id'], a['layer'])
            row = rows.setdefault(rk, {'kind': a['kind'], 'pair_id': None if a['cno'] else a['pair_id'], 'cno': a['cno'],
                                       'layer': a['layer'], 'partner': partner, 'electrodes': None, 'net': [],
                                       'abs': [], 'presence': set(), 'item_ids': [],
                                       'pair_delta': None if a['cno'] else pair_delta[a['pair_id']]})
            row['net'].append(a['d'])
            row['abs'].append(abs(a['d']))
            row['presence'].add(a['presence'])
            row['item_ids'].append(a['id'])
            if key[0] == 'p':
                row['electrodes'] = list(pair_rows[a['pair_id']]['electrodes'])
            if partner is not None:
                partner_abs[a['kind']].setdefault(partner, []).append(abs(a['d']))
        lines = []
        for row in rows.values():
            lines.append(dict(row, net=math.fsum(row['net']), abs=math.fsum(row['abs']),
                              presence=sorted(row['presence']), item_ids=sorted(row['item_ids'])))
        lines.sort(key=lambda r: (-abs(r['net']), r['kind'], str(r['pair_id']), r['layer']))      # K1: |net| first
        out['rows'] = lines
        out['partners'] = sorted({a['names'][0] if a['names'][1] == key[1] else a['names'][1]
                                  for a in pair_atoms if key[0] != 'p' and len(a['names']) == 2})
        out['partner_count'] = len(out['partners'])
        top = {}
        for kind in KINDS:
            parts = {name: math.fsum(values) for name, values in partner_abs[kind].items()}
            total = kinds[kind]['abs']
            if parts and total:
                name = sorted(parts, key=lambda n: (-parts[n], n))[0]
                top[kind] = {'partner': name, 'abs': parts[name], 'fraction': parts[name] / total}
            else:
                top[kind] = None
        out['top_partner'] = top
        line, pixel = kinds['line'], kinds['pixel']
        out['conflict'] = bool(
            line['net'] is not None and pixel['net'] is not None and line['share'] is not None
            and pixel['share'] is not None and line['net'] * pixel['net'] < 0
            and line['share'] >= CONFLICT_SHARE and pixel['share'] >= CONFLICT_SHARE)
        out['partners_elsewhere'] = [{'partner': p, 'bundle_id': i} for p, i in sorted(elsewhere.get(key, ()))]
        final.append(out)
    final.sort(key=lambda b: (-max((b['kinds'][k]['share'] or 0.0) for k in KINDS), b['id']))

    crp_part = {'line': 0.0 if kstat['line'][0] != 'not_evaluated' else None, 'pixel': crp}
    ranking, types = {}, {}
    direction_checks = {}
    for kind in KINDS:
        status = kstat[kind][0]
        empty_dir = {'total': None, 'k80': None, 'cards': [], 'rest': [], 'cover': None}
        if status == 'not_evaluated':
            ranking[kind] = {'status': status, 'direction': None, 'main': None, 'increase': dict(empty_dir),
                             'decrease': dict(empty_dir), 'zero': [], 'cancel_ratio': None, 'net_total': None}
            types[kind] = None
            direction_checks[kind] = None
            continue
        ok = status == 'ok'
        members = [b for b in final if (b['kinds'][kind]['abs'] or 0.0) > 0.0]
        net_of = lambda b: b['kinds'][kind]['net']
        groups = {'increase': sorted((b for b in members if net_of(b) > 0), key=lambda b: (-net_of(b), b['id'])),
                  'decrease': sorted((b for b in members if net_of(b) < 0), key=lambda b: (net_of(b), b['id'])),
                  'zero': sorted((b for b in members if net_of(b) == 0), key=lambda b: b['id'])}
        totals_dir = {name: math.fsum(net_of(b) for b in groups[name]) for name in ('increase', 'decrease')}
        net_total = math.fsum([totals_dir['increase'], totals_dir['decrease']])
        if abs(net_total) <= limit[kind]:       # L2: the main direction of a balanced kind is the larger |total|
            direction = 'balanced'
            main = 'increase' if totals_dir['increase'] > -totals_dir['decrease'] else 'decrease'
        else:
            direction = main = 'increase' if net_total > 0 else 'decrease'
        packed = {}
        for name in ('increase', 'decrease'):
            members_dir, total = groups[name], totals_dir[name]
            shares = {}
            for b in members_dir:
                share = (net_of(b) / total) if ok and total != 0 else None
                b['kinds'][kind]['direction_share'] = share
                shares[b['id']] = share
            count = CARD_COUNT if direction == 'balanced' or name == direction else OPPOSITE_CARD_COUNT
            running, k80 = 0.0, None
            if ok and total != 0:
                for index, b in enumerate(members_dir, 1):
                    running += shares[b['id']]
                    if running >= K_SHARE - K_EPS:
                        k80 = index
                        break
                if k80 is None:
                    k80 = len(members_dir)
            cards, rest = members_dir[:count], members_dir[count:]
            packed[name] = {'total': total, 'k80': k80, 'cards': [b['id'] for b in cards],
                            'rest': [b['id'] for b in rest],
                            'cover': math.fsum(shares[b['id']] for b in cards) if ok and total != 0 and cards else None}
        gross = g[kind]
        ranking[kind] = dict({'status': status, 'direction': direction, 'main': main}, **packed,
                             zero=[b['id'] for b in groups['zero']],
                             cancel_ratio=(1.0 - abs(net_total) / gross) if gross else None, net_total=net_total)
        # L3: additive types hold every bundle once (a CNO atom inside an electrode bundle sits in that type);
        # `cno` is the independent CNO bundles only and `cno_all` (reference, never added) is every CNO atom
        types[kind] = {name: {'count': 0, 'increase': [], 'decrease': [], 'net': [], 'gross': []} for name in TYPES}
        for b in members:
            row = types[kind][b['type']]
            row['count'] += 1
            row['net'].append(net_of(b))
            row['gross'].append(b['kinds'][kind]['abs'])
            (row['increase'] if net_of(b) > 0 else row['decrease'] if net_of(b) < 0 else []).append(net_of(b))
        for name in TYPES:
            row = types[kind][name]
            types[kind][name] = {'count': row['count'], 'increase': math.fsum(row['increase']),
                                 'decrease': math.fsum(row['decrease']), 'net': math.fsum(row['net']),
                                 'gross': math.fsum(row['gross'])}
        cno_atoms = [a for a in live if a['kind'] == kind and a['cno']]
        types[kind]['crp'] = {'net': crp_part[kind]}
        types[kind]['cno_all'] = {'count': len(cno_atoms), 'net': math.fsum(a['d'] for a in cno_atoms),
                                  'gross': math.fsum(abs(a['d']) for a in cno_atoms), 'reference': True}
        direction_checks[kind] = (groups, packed, totals_dir, members)

    status_doc = {kind: kstat[kind][0] for kind in KINDS}
    summary = {'rule': BUNDLE_RULE, 'version': BUNDLE_VERSION, 'hub_min': hub_min, 'hub_min_fraction': hub_fraction,
               'hub_min_share': hub_share, 'hub_sensitivity': sensitivity, 'status': status_doc, 'reasons': {kind: list(kstat[kind][1]) for kind in KINDS}, 'g': g,
               'atoms': {kind: sum(1 for a in live if a['kind'] == kind) for kind in KINDS},
               'bundle_count': len(buckets), 'list': final, 'ranking': ranking, 'types': types, 'crp': crp_part}
    kind_checks = {}
    for kind in KINDS:
        delta = totals[kind]['delta']
        if direction_checks[kind] is None:
            none = _check(None, delta)
            kind_checks[kind] = {'net_plus_crp': none, 'direction_identity': none, 'share_sum': _check(None, 1.0),
                                 'direction_share_sum': _check(None, 1.0), 'cover_plus_rest': _check(None, 1.0),
                                 'each_once': {'status': 'not_evaluated'}, 'types_plus_crp': none}
            continue
        groups, packed, totals_dir, members = direction_checks[kind]
        ok = kstat[kind][0] == 'ok'
        filled = [name for name in ('increase', 'decrease') if groups[name] and totals_dir[name] != 0]
        listed = [i for name in ('increase', 'decrease') for i in packed[name]['cards'] + packed[name]['rest']]
        listed += ranking[kind]['zero']
        wanted = sorted(b['id'] for b in members)
        rest_ids = {n: set(packed[n]['rest']) for n in filled}
        covered = _check(math.fsum((packed[n]['cover'] or 0.0) + math.fsum(
            b['kinds'][kind]['direction_share'] for b in groups[n] if b['id'] in rest_ids[n])
            for n in filled), float(len(filled))) if ok else _check(None, 1.0)
        share_sum = (math.fsum(b['kinds'][kind]['direction_share'] for n in filled for b in groups[n])
                     if ok else None)
        type_net = math.fsum(row['net'] for name, row in types[kind].items() if name in TYPES)
        kind_checks[kind] = {
            'net_plus_crp': _check(_osum([math.fsum(nets[kind]), crp_part[kind]]), delta),
            'direction_identity': _check(_osum([totals_dir['increase'], totals_dir['decrease'], crp_part[kind]]),
                                         delta),
            'share_sum': _check(math.fsum(b['kinds'][kind]['share'] for b in final), 1.0) if ok
            else _check(None, 1.0),
            'direction_share_sum': _check(share_sum, float(len(filled))) if ok else _check(None, 1.0),
            'cover_plus_rest': covered,
            'each_once': {'status': 'passed' if sorted(listed) == wanted else 'failed', 'listed': len(listed),
                          'expected': len(wanted)},
            'types_plus_crp': _check(_osum([type_net, crp_part[kind]]), delta)}
    atom_ids = [a['id'] for a in live]
    structural = {'atoms': len(live), 'assigned': len(assigned_to), 'assignments': assignments,
                  'duplicates': assignments - len(set(atom_ids)), 'id_unique': id_unique,
                  'id_length': max([len(i) - 3 for i in ids.values()] or [16])}
    states = [c['status'] for k in KINDS for c in kind_checks[k].values()]
    structural_ok = (structural['duplicates'] == 0 and structural['assigned'] == structural['atoms']
                     and structural['assignments'] == structural['atoms'] and id_unique)
    states.append('passed' if structural_ok else 'failed')
    checks = dict(structural, kinds=kind_checks, status=_worst(states))
    return summary, checks


# ---- G3: concentration (actual values) -------------------------------------------------------------------------------

def _stage_parts(stage, atoms):
    """{group key: ([|value|...], [value...])} of one stage. The electrode stage splits an item over its endpoints."""
    parts = {}

    def add(key, value):
        entry = parts.setdefault(key, ([], []))
        entry[0].append(abs(value))
        entry[1].append(value)

    for a in atoms:
        d = a['d']
        crp, cno = a.get('crp', False), a['cno']
        if stage == 'division':
            add('crp' if crp else 'cno' if cno else {'both': 'same', 'new': 'new', 'gone': 'gone'}[a['presence']], d)
        elif stage == 'layer':
            add('CRP' if crp else f'CNO@{a["layer"]}' if cno else a['layer'], d)
        elif stage == 'electrode':
            if crp:
                add('CRP(귀속 불가)', d)
            elif cno:
                add(a['ends'][0][0], d)
            else:
                for name, _layer in a['ends']:
                    add(name, d / len(a['ends']))
        elif stage == 'pair':
            add('CRP' if crp else a['id'] if cno else a['pair_id'], d)
        else:
            add('CRP' if crp else a['id'], d)
    return parts


def _concentration_stage(parts, total_abs, status, reasons, kind_delta, n_atoms):
    """One stage block and its checks (E4, Z3). Shares and k50 / k80 are null for G = 0 or a null atom."""
    base = {'status': status, 'atoms': n_atoms, 'g': total_abs}
    if status == 'not_evaluated':
        base.update(verdict='omitted', omit_reasons=list(reasons), n=None, k50=None, k80=None, top1=None, top3=None,
                    top5=None, tie_at_cut=False, tie_count=0, groups=[], rest=None, net=None)
        return base, {'shares_sum': _check(None, 1.0), 'net_sum': _check(None, kind_delta)}
    rows = []
    for key, (abs_parts, net_parts) in parts.items():
        total, net = math.fsum(abs_parts), math.fsum(net_parts)
        rows.append({'key': key, 'abs': total, 'net': net, 'cancel': total - abs(net)})
    rows.sort(key=lambda r: (-r['abs'], r['key']))
    active = [r for r in rows if r['abs'] > 0]
    ok = status == 'ok'
    for r in rows:
        r['share'] = (r['abs'] / total_abs) if ok else None
    k50 = k80 = None
    top = {1: None, 3: None, 5: None}
    tie_at_cut, tie_count = False, 0
    if ok:
        running = 0.0
        for index, r in enumerate(active, 1):
            running += r['share']
            if k50 is None and running >= 0.5 - K_EPS:
                k50 = index
            if running >= K_SHARE - K_EPS:
                k80 = index
                break
        if k80 is None:
            k80 = len(active)
        cut = active[k80 - 1]['abs']
        tied = [i for i, r in enumerate(active) if _no_change(cut, r['abs'])]
        tie_count = len(tied)
        tie_at_cut = any(i >= k80 for i in tied)
        for number in top:
            top[number] = math.fsum(r['share'] for r in active[:number])
    n = len(active)
    omit = list(reasons)
    if not ok:
        omit.append('no_change')
    else:
        if n < MIN_ATOMS:
            omit.append('n_lt_8')
        if tie_at_cut:
            omit.append('tie_at_cut')
    if omit:
        verdict = 'omitted'
    elif k80 <= CONCENTRATED_K80:
        verdict = 'concentrated'
    elif k80 / n >= DISPERSED_RATIO:
        verdict = 'dispersed'
    else:
        verdict = 'middle'
    shown, left = rows[:TOP_GROUPS], rows[TOP_GROUPS:]
    rest = None
    if left:
        rest_abs, rest_net = math.fsum(r['abs'] for r in left), math.fsum(r['net'] for r in left)
        rest = {'count': len(left), 'abs': rest_abs, 'net': rest_net, 'cancel': rest_abs - abs(rest_net),
                'share': (rest_abs / total_abs) if ok else None}
    net_total = math.fsum(r['net'] for r in rows)
    base.update(verdict=verdict, omit_reasons=omit, n=n, k50=k50, k80=k80, top1=top[1], top3=top[3], top5=top[5],
                tie_at_cut=tie_at_cut, tie_count=tie_count, groups=shown, rest=rest, net=net_total)
    shares = _check(math.fsum(r['share'] for r in rows), 1.0) if ok else _check(None, 1.0)
    return base, {'shares_sum': shares, 'net_sum': _check(net_total, kind_delta)}


def _concentration(atoms, kstat, crp, totals, vi_a):
    """summary.concentration and checks.concentration: five stages, the actual basis only."""
    summary, checks, states = {}, {}, []
    for kind in KINDS:
        mine = [a for a in atoms if a['kind'] == kind]
        if kind == 'pixel':      # the CRP line is one more atom of the pixel kind; it belongs to no pair or electrode
            mine = mine + [{'id': 'CRP', 'kind': 'pixel', 'pair_id': None, 'cno': False, 'crp': True, 'layer': None,
                            'ends': [], 'a': None, 'b': None, 'd': crp, 'presence': 'both'}]
        status, reasons, g = kstat[kind]
        if status != 'not_evaluated' and any(a['d'] is None for a in mine):
            status, reasons, g = 'not_evaluated', ['atom_null'], None
        elif status != 'not_evaluated':
            g = math.fsum(abs(a['d']) for a in mine)
            status = 'no_change' if g <= NO_CHANGE_ABS + NO_CHANGE_REL * abs(vi_a[kind] or 0.0) else 'ok'
        summary[kind], checks[kind] = {}, {}
        stages, stage_checks = {}, {}
        for stage in CONC_STAGES:
            parts = _stage_parts(stage, mine) if status != 'not_evaluated' else {}
            stages[stage], stage_checks[stage] = _concentration_stage(parts, g, status, reasons, totals[kind]['delta'],
                                                                      len(mine))
            states += [c['status'] for c in stage_checks[stage].values()]
        summary[kind]['actual'] = stages
        checks[kind] = stage_checks
    checks['status'] = _worst(states)
    return summary, checks


# ---- the summary ----------------------------------------------------------------------------------------------------

def _build_summary(doc, rebase, fam, vi_a, totals, crp_d, pair_rows, cno_rows, electrode_rows, lk):
    basis = (doc.get('inputs') or {}).get('rebase_basis')
    rebase_doc, rebase_checks = _rebase_summary(rebase, basis, pair_rows, cno_rows, totals)
    design = {kind: _design_summary(kind, rebase, rebase_doc.get(kind) or {'status': 'unavailable', 'reasons': []},
                                    pair_rows, cno_rows, vi_a[kind], totals, lk) for kind in KINDS}
    atoms = _atoms(pair_rows, cno_rows)
    kstat = {}
    for kind in KINDS:
        # the G threshold of the plan: 1e-12 + 1e-9 x vi_A
        kstat[kind] = _kind_status(kind, atoms, fam[kind], vi_a[kind])
    erows = {row['electrode']: row for row in electrode_rows}
    # the bundle code writes into its atoms (sign, names); work on copies so the concentration atoms stay plain
    bundle_atoms = [dict(a) for a in atoms]
    bundles, bundle_checks = _bundle_summary(bundle_atoms, kstat, erows, pair_rows, crp_d if fam['pixel'] else None,
                                             totals, lk, vi_a)
    conc_status = {kind: _kind_status(kind, atoms, fam[kind], vi_a[kind]) for kind in KINDS}
    concentration, conc_checks = _concentration(atoms, conc_status, crp_d if fam['pixel'] else None, totals,
                                              vi_a)
    summary = {'rules': dict(SUMMARY_RULES, hub_min=HUB_MIN, hub_min_fraction=HUB_MIN_FRACTION,
                                                  hub_min_share=HUB_MIN_SHARE), 'rebase': rebase_doc, 'design': design, 'bundles': bundles,
               'concentration': concentration}
    new_checks = {'rebase': rebase_checks, 'bundles': bundle_checks, 'concentration': conc_checks}
    new_checks['status'] = _worst([rebase_checks['status'], bundle_checks['status'], conc_checks['status']])
    # 'status' of the new checks is theirs alone: a not_evaluated summary check is not a failure of the ledger
    new_checks['status'] = 'failed' if 'failed' in (rebase_checks['status'], bundle_checks['status'],
                                                    conc_checks['status']) else new_checks['status']
    return summary, new_checks


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
