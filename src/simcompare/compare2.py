"""compare2: line/pixel lambda, CAA and VI per side, their change decomposition and removal impacts.

Every value is computed from unrounded floats; `*_rounded` fields (6 digits) are for display only.
Formulas: docs/implementation_memory.json § compare2_calculation_2026_11_05 and the T3 contract.
lambda = density(count/glass) * CA(um^2, observed in the 2x2 crop) * factor, where factor = effective_resolution *
weight / glass (team formula 2026-11-10; effective = int(W*H/4) for the mobile group only; compare2_inputs.lambda_factor). A side with a missing input reports null instead of a guess.
Null propagation: the line series and the pixel series are independent. A missing line input (glass area, line
density, group status unavailable/partial) nulls only the line indicators and their deltas; a missing pixel input
(pixel density, invalid CNO CSV) nulls only the pixel ones. A not_applicable group (vertical size below the
interlayer distance) counts as 0 contribution, not as missing.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .compare2_inputs import (CNO_FACTOR, CRP_DEFAULT_OTHER, CRP_ONE_GROUPS, ROUND_DIGITS, VI_LINE_DIVISOR,
                              VI_PIXEL_DIVISOR, cno_lambda_factor, collect_cno, crp_for_group,
                              effective_resolution, lambda_factor, load_compare2_config, load_density, size_key)
from .adapters.real_hotspot import CASE_EXCLUSION_RULE
from .interlayer_distance import record_block
from .numeric_schema import NumericContractError, has_short_cases
from .pair_exclusion import PAIR_EXCLUSION_RULE, record_block as pair_exclusion_block

SCHEMA_VERSION = 'compare2_v1'
AREA_BASIS = 'crop_observed_area_legacy_team_factor_user_approved_2026_11_10'
CONSERVATION_TOL = 1e-12
SLOPE_LIMIT_EPS = 1e-12
RATIO_EPS = 1e-15
ROLES = ('reference', 'target')
KINDS = ('line', 'pixel')
OWNED_NAMES = {'compare2.json', 'compare2_summary.csv', 'compare2_layers.csv', 'compare2_conditions.csv',
               'compare2_cno.csv', 'execution.json', 'error.json'}
EXECUTION_COMMAND = 'compare2'


def stale_reason(doc):
    """Shared stale check of a compare2.json document (07 compare2-report and ledger).

    Returns None when area_basis and the case-exclusion rules match this code, otherwise (error code, message).
    Check order: (1) area_basis (compare2_stale_area_basis), (2) case_exclusions rules (compare2_stale_case_exclusion_rule),
    (3) the pair_exclusions rule of every manifests.<role> that carries a block (compare2_stale_pair_exclusion_rule).
    A role entry or block that is not a dict counts as a rule mismatch (error code, no traceback). A document without any
    pair_exclusions block (no case was left out) passes step 3."""
    inputs = doc.get('inputs') or {}
    basis = inputs.get('area_basis')
    if basis != AREA_BASIS:
        return ('compare2_stale_area_basis',
                f'compare2.json area_basis is {basis!r}, this code uses {AREA_BASIS!r}: 02(compare2)를 새 번들로 다시 실행')
    exclusions = inputs.get('case_exclusions')
    rules = {role: (exclusions.get(role) or {}).get('rule') if isinstance(exclusions, dict) else None
             for role in ROLES}
    if any(rule != CASE_EXCLUSION_RULE for rule in rules.values()):
        return ('compare2_stale_case_exclusion_rule',
                f'compare2.json case_exclusions rule is {rules!r}, this code uses {CASE_EXCLUSION_RULE!r}: '
                '02(compare2)를 새 번들로 다시 실행')
    manifests = inputs.get('manifests')
    recorded = {}
    for role in ROLES:
        entry = manifests.get(role) if isinstance(manifests, dict) else None
        if entry is not None and not isinstance(entry, dict):
            recorded[role] = None
            continue
        block = entry.get('pair_exclusions') if entry else None
        if block is not None:
            recorded[role] = block.get('rule') if isinstance(block, dict) else None
    if any(rule != PAIR_EXCLUSION_RULE for rule in recorded.values()):
        return ('compare2_stale_pair_exclusion_rule',
                f'compare2.json pair_exclusions rule is {recorded!r}, this code uses {PAIR_EXCLUSION_RULE!r}: '
                '02(compare2)를 새 번들로 다시 실행')
    return None


# ---- small numeric helpers -----------------------------------------------------------------------------------

def _g(x):
    """1 - exp(-x) without cancellation for small x."""
    return -math.expm1(-x)


def _round(x):
    return None if x is None else round(x, ROUND_DIGITS)


def _area(x):
    """Area (um^2) as written to JSON/CSV; calculations and conservation checks use the unrounded sums."""
    return round(x, ROUND_DIGITS)


def _add(*values):
    return None if any(v is None for v in values) else sum(values)


def _sub(b, a):
    return None if a is None or b is None else b - a


def _scale(s, x):
    return None if s is None or x is None else s * x


def _vi_line(x):
    return _g(x) / VI_LINE_DIVISOR


def _vi_pixel(x, crp):
    return _g(x) / crp / VI_PIXEL_DIVISOR


def _slope_g(x_a, x_b):
    """Secant slope of g(x) = 1 - exp(-x) between x_a and x_b; the limit value when the two are equal."""
    delta = x_b - x_a
    if abs(delta) < SLOPE_LIMIT_EPS:
        return math.exp(-(x_a + x_b) / 2), 'limit'
    return math.exp(-x_a) * -math.expm1(-delta) / delta, 'secant'


def _ratio(part, whole):
    if part is None or whole is None or abs(whole) < RATIO_EPS:
        return None
    return part / whole


def _within(error, reference):
    return abs(error) <= CONSERVATION_TOL * max(1.0, abs(reference))


def _diameter_json(key):
    return float(key)


# ---- extraction from the area result ----------------------------------------------------------------------------

def _new_cell():
    """Areas in um^2 (cells x pitch^2 per group)."""
    return {'ok': True, 'reasons': set(), 'has_input': False, 'line': 0.0, 'peff': 0.0, 'transfer': 0.0,
            'shared_l': 0.0, 'shared_p': 0.0, 'excl_l': defaultdict(float), 'excl_p': defaultdict(float),
            'tr': defaultdict(float), 'ledger': {}, 'ledger_checks': {}, 'ledger_overlap': {'line': 0, 'pixel': 0}}


ABSENT_REASONS = frozenset(('source_coverage_incomplete', 'expected_case_folder_missing'))
ABSENT_NOTE = 'absent_expected_case_counted_zero'


def _absent_sources(group):
    """The missing sources of a group whose only gap is expected cases without a folder, else None.

    Such a group counts the absent cases as 0 area (a folder that does not exist has no hotspot). Any other gap
    (a missing image of an existing case, an unmapped pair, a rejected mask, ...) leaves the group unavailable."""
    coverage = group.union.coverage
    if coverage.rejected or coverage.unmapped:
        return None
    for metric in (group.line.area, group.union):
        if metric.status in ('available', 'not_applicable'):
            continue
        if metric.status not in ('partial', 'unavailable'):
            return None
        reasons = set(metric.reasons or ())
        if not reasons <= ABSENT_REASONS or 'expected_case_folder_missing' not in reasons:
            return None
    missing = [source for source in group.source_cases if source.input_status == 'missing']
    if not missing:
        return None
    return missing


LEDGER_BASIS = 'pair_share_v1'
INTEGER_CHECKS = ('exclusive_ok', 'divisible_ok', 'shared_ok', 'sum_ok')


def _extract_ledger(cell, shares, pitch_area):
    """Add one group's ledger_shares (linepixel.GroupArea.ledger_shares) to the cell: item areas in um^2 under
    (kind, pair_id, failure_type, endpoints_key), the integer checks AND-ed, the multi-item overlap cells summed.
    An item exists only for an observed source, so a pair carried only by absent expected cases has none."""
    if shares is None:
        raise NumericContractError('compare2_ledger_shares_missing', 'compute_areas(condition_stats=True) is required')
    for kind in KINDS:
        block = shares[kind]
        for entry in block['items']:
            ekey = tuple((e['electrode_id'], e['actual_layer']) for e in entry['endpoints'])
            known = cell['ledger'].setdefault((kind, entry['pair_id'], entry['failure_type'], ekey), {
                'upper': entry['upper_layer'], 'lower': entry['lower_layer'],
                'endpoints': [dict(e) for e in entry['endpoints']], 'share': 0.0, 'own': 0.0, 'out': 0.0})
            known['share'] += entry['share_cells'] * pitch_area
            known['own'] += entry['own_cells'] * pitch_area
            known['out'] += entry['transferred_out_cells'] * pitch_area
        checks = cell['ledger_checks'].setdefault(kind, {name: True for name in INTEGER_CHECKS})
        for name in INTEGER_CHECKS:
            checks[name] = checks[name] and bool(block['checks'][name])
        cell['ledger_overlap'][kind] += block['multi_item_overlap_cells']


def _extract(areas, absent_zero=False):
    """Areas (um^2) per (analysis_layer, density_layer, diameter) and per condition; masks are dropped.

    absent_zero: with a confirmed expected-case inventory, a case whose folder is absent counts as 0 area instead of
    making its group unavailable (see _absent_sources). `absent_cases` lists those case names."""
    cells, classes, electrodes = {}, {}, {}
    absent_cases = set()
    for key in sorted(areas.groups):
        group = areas.groups[key]
        stats = group.condition_stats
        if stats is None:
            raise NumericContractError('compare2_condition_stats_missing', 'compute_areas(condition_stats=True) is required')
        ck = (key.analysis_layer, key.density_process_layer, size_key(key.diameter_um))
        pitch_area = group.frame.pixel_pitch_um ** 2  # each group adds cells x its own pitch^2 (area, um^2)
        item = cells.setdefault(ck, _new_cell())
        coverage = group.union.coverage  # an input image (or a true not-applicable) exists, whatever its area
        if coverage.observed + coverage.not_applicable > 0:
            item['has_input'] = True
        absent = _absent_sources(group) if absent_zero else None
        if absent is not None:
            item['absent'] = True
            names = {source.identity.case_id for source in absent}
            item.setdefault('absent_names', set()).update(names)
            absent_cases.update(names)
            seen_pids = {source.pair_id for source in group.source_cases if source.input_status != 'missing'}
        else:
            for status, reasons in ((group.line.area.status, group.line.area.reasons),
                                    (group.union.status, group.union.reasons)):
                # not_applicable (vertical size below the interlayer distance) is a true 0 contribution
                if status not in ('available', 'not_applicable'):
                    item['ok'] = False
                    item['reasons'].add('group_status_' + status)
                    item['reasons'].update(reasons or ())
        item['line'] += group.line_packed.count() * pitch_area
        item['peff'] += group.pixel_effective_packed.count() * pitch_area
        item['transfer'] += group.transfer_packed.count() * pitch_area
        item['shared_l'] += stats['line']['shared_cells'] * pitch_area
        item['shared_p'] += stats['pixel']['shared_cells'] * pitch_area
        for pid, count in stats['line']['exclusive_cells'].items():
            item['excl_l'][pid] += count * pitch_area
            classes[(pid, key.analysis_layer)] = 'line'
        for pid, count in stats['pixel']['exclusive_cells'].items():
            item['excl_p'][pid] += count * pitch_area
            classes[(pid, key.analysis_layer)] = 'pixel'
        for pid, count in stats['transfer_by_line_pair_cells'].items():
            item['tr'][pid] += count * pitch_area
        _extract_ledger(item, group.ledger_shares, pitch_area)
        for pid, pair in group.pair_membership.items():
            if absent is not None and pid not in seen_pids:
                continue  # only absent cases carry this pair here: not a condition of this side (0 area, no row)
            electrodes[(pid, key.analysis_layer)] = list(pair)
    return {'cells': cells, 'classes': classes, 'electrodes': electrodes, 'absent_cases': sorted(absent_cases)}


def _load_side(cfg, role, top_buckets, share_sink=None):
    """Load one side, measure it and return plain numbers; the dataset and the masks are released on return."""
    from .linepixel import compute_areas
    from .pipeline import _configured_real, _real_source
    section = cfg.get(role)
    real = _real_source(cfg, section)
    if real is None:
        raise NumericContractError('compare2_real_source_required', f'{role}: real_hotspot_v1 data_dir needed')
    detail = {'bucket_detail': 'summary', 'top_buckets': top_buckets}
    configured = _configured_real(cfg, section, real, detail, require_distance=True)
    if configured is None:
        raise NumericContractError('compare2_condition_assignment_required', f'{role}: condition_assignment needed')
    dataset = configured[1]
    if not has_short_cases(dataset.cases):
        left_out = pair_exclusion_block(dataset.provenance)
        note = ''
        if left_out is not None:
            count = left_out['excluded_cases'] + left_out['excluded_expected_cases']
            note = f'; {left_out["rule"]}: {count} cases left out'
        raise NumericContractError('compare2_side_without_cases', f'{role}: {CASE_EXCLUSION_RULE}{note}')
    manifest = real[1]
    meta = {'source_payload_hash': manifest['source_payload_hash'],
            'case_exclusions': manifest.get('case_exclusions'),
            'expected_case_coverage_status': manifest.get('expected_case_coverage_status'),
            'same_name_cases_unresolved': bool(dataset.provenance.get('unresolved_same_name_cases'))}
    result = compute_areas(dataset, **detail, condition_stats=True, share_sink=share_sink)
    extracted = _extract(result, absent_zero=meta['expected_case_coverage_status'] == 'confirmed')
    meta['pair_table_version'] = result.pair_table_version
    meta['pair_table_hash'] = result.pair_table_hash
    meta['input_hash'] = result.input_hash
    block = record_block(dataset.provenance)
    if block is not None:       # key absent = filter off = nothing written (the output bytes stay the baseline's)
        meta['distance_filter'] = block
    pair_block = pair_exclusion_block(dataset.provenance)
    if pair_block is not None:  # no left-out case = no key = the baseline's bytes
        meta['pair_exclusions'] = pair_block
    del result, dataset, configured, real
    return extracted, meta


# ---- per-side numbers -------------------------------------------------------------------------------------------

def _sum_opt(values):
    values = list(values)
    return None if any(v is None for v in values) else sum(values)


def _compute_side(role, product, group, tables, extracted, meta, diameters=None):
    """One side's numbers. `diameters` (a set of size keys) limits every cell, CNO row and delta input to those sizes."""
    cells, classes, electrodes = extracted['cells'], extracted['classes'], extracted['electrodes']
    # every analysed diameter has a (possibly empty) cell, so "observed" means an input image exists (hotspot
    # area may be 0) or a CNO row exists; a diameter whose records are all missing is not observed
    observed = {ck[2] for ck, item in cells.items() if item['has_input']}
    factors = {'line': lambda_factor(product, group, 'line'), 'pixel': lambda_factor(product, group, 'pixel'),
               'cno': cno_lambda_factor(product, group)}
    reasons = set()
    if factors['line'] is None:
        reasons.add(group.glass_area_reason or 'glass_area_pending')
    ok = {'line': factors['line'] is not None, 'pixel': factors['pixel'] is not None}
    if meta['expected_case_coverage_status'] != 'confirmed':
        reasons.add('expected_case_inventory_unprovided')
    if meta['same_name_cases_unresolved']:
        reasons.add('same_name_cases_unresolved')

    entries = {}
    for ck in sorted(cells):
        layer, dlayer, diameter = ck
        if diameters is not None and diameter not in diameters:
            continue
        item = cells[ck]
        density, notes = {}, []
        for kind in KINDS:
            value, why = tables[kind].lookup(dlayer, diameter)
            density[kind] = value
            if why is not None:
                needed = item['line'] if kind == 'line' else item['peff'] + item['transfer']
                if needed == 0:  # nothing to weight: contribution 0, informational only
                    notes.append(f'density_missing_zero_area:{kind}')
                    continue
                notes.append(why)
                reasons.add(f'{why}:{kind}:{dlayer}')
                ok[kind] = False
        if item.get('absent'):
            notes.append(ABSENT_NOTE)
        if not item['ok']:
            notes.extend(sorted(item['reasons']))
            reasons.add('group_not_available')
            ok['line'] = ok['pixel'] = False
        entries[ck] = {'item': item, 'density': density, 'notes': notes, 'ok': item['ok']}

    # cases counted as 0: only from cells that are ok and inside the selected diameters (entries is already filtered)
    absent_names = sorted({name for entry in entries.values() if entry['ok']
                           for name in entry['item'].get('absent_names', ())})

    def lam(kind, ck, count):
        entry = entries[ck]
        factor = factors[kind]
        if not entry['ok'] or factor is None:
            return None
        if entry['density'][kind] is None:
            return 0.0 if count == 0 else None
        return entry['density'][kind] * count * factor

    # layers and sizes
    layer_rows, layer_cells = {}, defaultdict(lambda: {'line': [], 'pixel': []})
    size_lam = {}
    for ck in sorted(entries):
        layer, dlayer, diameter = ck
        item, entry = entries[ck]['item'], entries[ck]
        lam_l, lam_p = lam('line', ck, item['line']), lam('pixel', ck, item['peff'])
        size_lam[ck] = (lam_l, lam_p)
        row = layer_rows.setdefault((layer, dlayer), {'layer': layer, 'density_layer': dlayer, 'by_size': []})
        row['by_size'].append({
            'diameter_um': _diameter_json(diameter), 'density_line': entry['density']['line'],
            'density_pixel': entry['density']['pixel'], 'line_area_um2': _area(item['line']),
            'pixel_eff_area_um2': _area(item['peff']), 'transfer_area_um2': _area(item['transfer']),
            'lambda_line': lam_l, 'lambda_pixel': lam_p,
            'status': 'available' if lam_l is not None and lam_p is not None else 'unavailable',
            'reasons': entry['notes']})
        layer_cells[(layer, dlayer)]['line'].append(lam_l)
        layer_cells[(layer, dlayer)]['pixel'].append(lam_p)
    total_line = _sum_opt(v for lams in size_lam.values() for v in (lams[0],)) if ok['line'] else None
    total_pixel = _sum_opt(v for lams in size_lam.values() for v in (lams[1],)) if ok['pixel'] else None
    for (layer, dlayer), row in layer_rows.items():
        row['lambda_line'] = _sum_opt(layer_cells[(layer, dlayer)]['line'])
        row['lambda_pixel'] = _sum_opt(layer_cells[(layer, dlayer)]['pixel'])
        row['share_line'] = _ratio(row['lambda_line'], total_line) if total_line else None
        row['share_pixel'] = _ratio(row['lambda_pixel'], total_pixel) if total_pixel else None

    # conditions
    conds = {}
    for ck in sorted(entries):
        layer = ck[0]
        item = entries[ck]['item']
        for pid in sorted(set(item['excl_l']) | set(item['excl_p']) | set(item['tr'])):
            cond = conds.setdefault((pid, layer), {'pid': pid, 'layer': layer, 'by_size': [], 'computable': True,
                                                   'class': classes.get((pid, layer)),
                                                   'electrodes': electrodes.get((pid, layer))})
            cond['by_size'].append((ck, item['excl_l'].get(pid, 0), item['excl_p'].get(pid, 0), item['tr'].get(pid, 0)))
    seen = set()  # conditions with any cell before the diameter filter
    if diameters is not None:
        for ck, item in cells.items():
            seen.update((pid, ck[0]) for pid in set(item['excl_l']) | set(item['excl_p']) | set(item['tr']))
    for ident, pair in electrodes.items():
        if ident not in conds:
            conds[ident] = {'pid': ident[0], 'layer': ident[1], 'by_size': [], 'computable': False,
                            'class': None, 'electrodes': pair}
            if ident in seen:  # the filter removed every cell: a true 0 contribution, not a missing input
                conds[ident]['filtered_out'] = True
            else:
                reasons.add('condition_not_computable')
    for ident, cond in conds.items():
        if cond.get('filtered_out'):
            cond['v'] = {'excl_l': 0.0, 'excl_p': 0.0, 'tr': 0.0}
            cond['area_excl'] = cond['area_tr'] = 0.0
            cond['rows'] = []
            cond['reasons'] = ['diameter_filter_removed_all']
            continue
        if not cond['computable']:
            cond['v'] = {'excl_l': None, 'excl_p': None, 'tr': None}
            cond['area_excl'] = cond['area_tr'] = 0.0
            cond['rows'] = []
            cond['reasons'] = ['condition_not_computable']
            continue
        rows, a_excl, a_tr = [], 0.0, 0.0
        parts = {'excl_l': [], 'excl_p': [], 'tr': []}
        for ck, n_l, n_p, n_t in cond['by_size']:
            v_l, v_p, v_t = lam('line', ck, n_l), lam('pixel', ck, n_p), lam('pixel', ck, n_t)
            parts['excl_l'].append(v_l)
            parts['excl_p'].append(v_p)
            parts['tr'].append(v_t)
            a_excl += n_l + n_p
            a_tr += n_t
            rows.append({'diameter_um': _diameter_json(ck[2]), 'density_layer': ck[1],
                         'area_excl_um2': _area(n_l + n_p), 'area_transfer_um2': _area(n_t),
                         'lambda_excl': _add(v_l, v_p), 'lambda_transfer': v_t})
        cond['v'] = {name: _sum_opt(values) for name, values in parts.items()}
        cond['area_excl'], cond['area_tr'], cond['rows'] = a_excl, a_tr, rows
        cond['reasons'] = sorted({note for ck, *_ in cond['by_size'] for note in entries[ck]['notes']})

    shared = {'line': _sum_opt(lam('line', ck, entries[ck]['item']['shared_l']) for ck in sorted(entries)),
              'pixel': _sum_opt(lam('pixel', ck, entries[ck]['item']['shared_p']) for ck in sorted(entries))}

    ledger_items, ledger_check, ledger_raw = _build_ledger(cells, entries, lam, conds, electrodes, shared,
                                                           {'line': total_line, 'pixel': total_pixel})

    # CNO
    cno_rows, cno_ok, cno_raw = {}, True, {}
    for case in collect_cno(product.hotspot_root):
        observed.update(size_key(diameter) for diameter in case.rows)
        row = {'case_name': case.case_name, 'electrode_id': case.electrode_id, 'density_layer': case.density_layer,
               'csv_status': case.status, 'csv_relative_path': case.csv_relative_path, 'lambda': None, 'reasons': []}
        if case.status != 'observed':
            row['reasons'].append(f'cno_csv_{case.status}')
            if case.reason:
                row['reasons'].append(case.reason)
            if case.detail:
                row['detail'] = case.detail
        elif factors['cno'] is None:
            row['reasons'].append(group.glass_area_reason or 'glass_area_pending')
        else:
            parts, by_size, raw_rows = [], [], []
            for diameter, area in sorted(case.rows.items()):
                if diameters is not None and size_key(diameter) not in diameters:
                    continue
                value, why = tables['pixel'].lookup(case.density_layer, diameter)
                if value is None:
                    row['reasons'].append(f'{why}:pixel:{case.density_layer}')
                    parts = None
                    break
                parts.append(value * area * factors['cno'])
                raw_rows.append((diameter, area))   # unrounded CSV area, read by the rebase calculation
                by_size.append({'diameter_um': _diameter_json(diameter), 'density_pixel': value, 'area_csv_um2': _area(area),
                                'lambda': value * area * factors['cno']})
            if parts is not None:
                row['lambda'] = sum(parts)
                row['by_size'] = by_size      # the per-diameter density the location formula of compare2-report reads
                cno_raw[case.case_name] = raw_rows
        row['reasons'] = sorted(set(row['reasons']))
        if row['lambda'] is None:
            cno_ok = False
            reasons.update(f'cno:{case.case_name}:{why}' for why in row['reasons'])
        cno_rows[case.case_name] = row
    total_cno = _sum_opt(row['lambda'] for row in cno_rows.values()) if cno_ok else None
    ok['pixel'] = ok['pixel'] and cno_ok

    crp = crp_for_group(product.model_group)
    x_pixel = _add(total_pixel, total_cno)
    side = {
        'role': role, 'product': product, 'group': group, 'crp': crp, 'factors': factors, 'ok': ok,
        'reasons': reasons, 'entries': entries, 'size_lam': size_lam, 'layers': layer_rows, 'conds': conds,
        'shared': shared, 'ledger_items': ledger_items, 'ledger_check': ledger_check, 'ledger_raw': ledger_raw,
        'cno_raw': cno_raw, 'cno': cno_rows, 'lam': {'line': total_line, 'pixel': total_pixel, 'cno': total_cno},
        'x_pixel': x_pixel,
        'caa': {'line': None if total_line is None else _g(total_line),
                'pixel': None if x_pixel is None or not ok['pixel'] else _g(x_pixel) / crp},
        'meta': meta, 'observed': observed, 'absent_cases': absent_names}
    side['vi'] = {'line': None if side['caa']['line'] is None else side['caa']['line'] / VI_LINE_DIVISOR,
                  'pixel': None if side['caa']['pixel'] is None else side['caa']['pixel'] / VI_PIXEL_DIVISOR}
    side['conservation'] = _conservation(side)
    return side


def _item_id(pid, failure_type, upper, lower, ekey):
    """Collision-free item id: the structural value [pair_id, failure_type, upper, lower, [[electrode, layer], ...]]
    as compact JSON (no separator character in a name can merge two items). `ledger` calls this same function."""
    return json.dumps([pid, failure_type, upper, lower, [[electrode, layer] for electrode, layer in ekey]],
                      ensure_ascii=False, separators=(',', ':'))


def _build_ledger(cells, entries, lam, conds, electrodes, shared, totals):
    """Per-item lambda (pair share of one failure type / electrode layers, split by size) and the cross-checks.

    Every item lambda goes through the same `lam` as the condition and layer lambdas (same density, factor, size
    filter, not-ok rule). `cells` holds every cell, `entries` the cells inside the diameter filter."""
    acc, raw = {}, {}
    for ck in sorted(cells):
        for (kind, pid, failure_type, ekey), led in cells[ck]['ledger'].items():
            rec = acc.setdefault((ck[0], pid, failure_type, ekey, kind), {'led': led, 'rows': [], 'out': 0.0,
                                                                          'notes': set()})
            if ck not in entries:
                continue
            rec['rows'].append((ck, led['share'], lam(kind, ck, led['share'])))
            rec['out'] += led['out']
            rec['notes'].update(entries[ck]['notes'])
    items = []
    for layer, pid, failure_type, ekey, kind in sorted(acc):
        rec = acc[(layer, pid, failure_type, ekey, kind)]
        led = rec['led']
        if rec['rows']:
            values = [value for _ck, _share, value in rec['rows']]
            total = None if any(v is None for v in values) else math.fsum(values)
            reasons = sorted(rec['notes'])
        else:  # the diameter filter removed every cell of this item: a true 0, not a missing input
            total, reasons = 0.0, ['diameter_filter_removed_all']
        item_id = _item_id(pid, failure_type, led['upper'], led['lower'], ekey)
        raw[(item_id, kind)] = list(rec['rows'])   # (cell key, unrounded share um2, lambda) in size order
        items.append({
            'item_id': item_id, 'pair_id': pid, 'class': kind,
            'failure_type': failure_type, 'analysis_layer': layer, 'upper_layer': led['upper'],
            'lower_layer': led['lower'], 'electrodes': electrodes.get((pid, layer)) or [e for e, _l in ekey],
            'endpoints': led['endpoints'], 'lambda': total,
            'area_share_um2': _area(sum(share for _ck, share, _v in rec['rows'])),
            'by_size': [{'diameter_um': _diameter_json(ck[2]), 'density_layer': ck[1], 'area_share_um2': _area(share),
                         'lambda': value} for ck, share, value in rec['rows']],
            'transferred_out_area_um2': _area(rec['out']), 'computable': total is not None, 'reasons': reasons})
    return items, _ledger_check(cells, items, conds, shared, totals), raw


def _ledger_check(cells, items, conds, shared, totals):
    integer = {kind: {name: True for name in INTEGER_CHECKS} for kind in KINDS}
    for kind in KINDS:
        integer[kind]['multi_item_overlap_cells'] = 0
    for cell in cells.values():
        for kind, checks in cell['ledger_checks'].items():
            for name in INTEGER_CHECKS:
                integer[kind][name] = integer[kind][name] and checks[name]
        for kind in KINDS:
            integer[kind]['multi_item_overlap_cells'] += cell['ledger_overlap'][kind]
    failed = any(not integer[kind][name] for kind in KINDS for name in INTEGER_CHECKS)
    integer['status'] = 'failed' if failed else 'passed'
    out = {'integer_checks': integer}
    empty = {'status': 'not_evaluated', 'sum': None, 'total': None, 'abs_error': None}
    for kind, excl_name in (('line', 'excl_l'), ('pixel', 'excl_p')):
        values = [item['lambda'] for item in items if item['class'] == kind]
        total = totals[kind]
        if total is None or any(v is None for v in values):
            out[f'lambda_{kind}'] = dict(empty, total=total)
            out[f'shared_{kind}'] = dict(empty, total=shared[kind])
            continue
        lhs = math.fsum(values)
        error = lhs - total
        out[f'lambda_{kind}'] = {'status': 'passed' if _within(error, total) else 'failed', 'sum': lhs,
                                 'total': total, 'abs_error': abs(error)}
        excl = [cond['v'][excl_name] for cond in conds.values() if cond['computable'] or cond.get('filtered_out')]
        if shared[kind] is None or any(v is None for v in excl):
            out[f'shared_{kind}'] = dict(empty, total=shared[kind])
            continue
        lhs = math.fsum(values + [-v for v in excl])
        error = lhs - shared[kind]
        out[f'shared_{kind}'] = {'status': 'passed' if _within(error, shared[kind]) else 'failed', 'sum': lhs,
                                 'total': shared[kind], 'abs_error': abs(error)}
    states = {out[name]['status'] for name in ('lambda_line', 'lambda_pixel', 'shared_line', 'shared_pixel')}
    states.add(integer['status'])
    out['status'] = 'failed' if 'failed' in states else ('not_evaluated' if 'not_evaluated' in states else 'passed')
    return out


def _conservation(side):
    result = {}
    for kind, excl_name in (('line', 'excl_l'), ('pixel', 'excl_p')):
        total = side['lam'][kind]
        shared = side['shared'][kind]
        values = [cond['v'][excl_name] for cond in side['conds'].values() if cond['computable']]
        if total is None or shared is None or any(v is None for v in values):
            result[kind] = {'status': 'not_evaluated', 'sum': None, 'total': total, 'abs_error': None}
            continue
        lhs = sum(values) + shared
        error = lhs - total
        result[kind] = {'status': 'passed' if _within(error, total) else 'failed', 'sum': lhs, 'total': total,
                        'abs_error': abs(error)}
    states = {item['status'] for item in result.values()}
    result['status'] = 'failed' if 'failed' in states else ('not_evaluated' if 'not_evaluated' in states else 'passed')
    return result


# ---- delta, decomposition, removal ------------------------------------------------------------------------------

def _cond_value(side, ident, name):
    cond = side['conds'].get(ident)
    return 0.0 if cond is None else cond['v'][name]


def _layer_size_lambdas(side, kind_index):
    """{(analysis_layer, diameter): lambda} summed over density layers; None when a part is null."""
    out = {}
    for (layer, _dlayer, diameter), lams in side['size_lam'].items():
        key = (layer, diameter)
        out[key] = _add(out.get(key, 0.0), lams[kind_index])
    return out


def _cno_by_layer(side):
    """A side's CNO rows keyed by (electrode_id, density_layer): the same electrode and layer under two folder-name
    forms (3-part, trailing hyphen) is one row, the lambdas summed."""
    merged = {}
    for name in sorted(side['cno']):
        row = side['cno'][name]
        key = (row['electrode_id'], row['density_layer'])
        if key not in merged:
            merged[key] = dict(row)
        else:
            merged[key]['lambda'] = _sum_opt([merged[key]['lambda'], row['lambda']])
    return merged


def _delta(a, b):
    ident_set = sorted(set(a['conds']) | set(b['conds']), key=lambda i: (i[1], i[0]))
    families = {kind: a['ok'][kind] and b['ok'][kind] for kind in KINDS}
    out = {'attribution': 'secant', 'indicators': {}, 'slopes': {}, 'conditions': [], 'cno': [], 'shared': {},
           'crp_difference': None, 'layers': [], 'check': {'status': 'not_evaluated'}}
    ind = out['indicators']
    for name in ('lam', 'caa', 'vi'):
        for kind in KINDS:
            ind[f'delta_{"lambda" if name == "lam" else name}_{kind}'] = (
                _sub(b[name][kind], a[name][kind]) if families[kind] else None)
    ind['delta_lambda_cno'] = _sub(b['lam']['cno'], a['lam']['cno']) if families['pixel'] else None
    for kind in KINDS:
        ind[f'delta_vi_{kind}_rounded'] = _round(ind[f'delta_vi_{kind}'])
        ind[f'delta_caa_{kind}_rounded'] = _round(ind[f'delta_caa_{kind}'])

    s_line = s_pixel = None
    if families['line']:
        slope, method = _slope_g(a['lam']['line'], b['lam']['line'])
        s_line = slope / VI_LINE_DIVISOR
        out['slopes']['line'] = {'value': s_line, 'method': method}
    if families['pixel']:
        slope, method = _slope_g(a['x_pixel'], b['x_pixel'])
        s_pixel = slope / (b['crp'] * VI_PIXEL_DIVISOR)
        out['slopes']['pixel'] = {'value': s_pixel, 'method': method}
        if a['crp'] != b['crp']:
            out['crp_difference'] = {
                'delta_vi_pixel': _g(a['x_pixel']) * (1 / b['crp'] - 1 / a['crp']) / VI_PIXEL_DIVISOR}
            out['crp_difference']['share_pixel'] = _ratio(out['crp_difference']['delta_vi_pixel'],
                                                             ind['delta_vi_pixel'])
    dvi_line, dvi_pixel = ind['delta_vi_line'], ind['delta_vi_pixel']

    sums = {'line': 0.0, 'pixel': 0.0}
    for ident in ident_set:
        ca, cb = a['conds'].get(ident), b['conds'].get(ident)
        base = cb or ca
        class_a, class_b = (ca or {}).get('class'), (cb or {}).get('class')
        cls = class_b if class_a in (None, class_b) else (class_a if class_b is None else f'{class_a}->{class_b}')
        d_l = _sub(_cond_value(b, ident, 'excl_l'), _cond_value(a, ident, 'excl_l')) if families['line'] else None
        d_p = _sub(_cond_value(b, ident, 'excl_p'), _cond_value(a, ident, 'excl_p')) if families['pixel'] else None
        d_t = (_sub(_cond_value(b, ident, 'tr'), _cond_value(a, ident, 'tr'))
               if families['pixel'] else None)
        v_line, v_pixel = _scale(s_line, d_l), _scale(s_pixel, d_p)
        if v_line is not None:
            sums['line'] += v_line
        if v_pixel is not None:
            sums['pixel'] += v_pixel
        removal = None
        if cb is not None:
            removal = {'delta_vi_line': None, 'delta_vi_pixel': None}
            if cb.get('filtered_out'):  # the filter removed every cell: removing it changes nothing
                removal = {'delta_vi_line': 0.0 if b['ok']['line'] else None,
                           'delta_vi_pixel': 0.0 if b['ok']['pixel'] else None}
            elif b['ok']['line'] and cb['computable']:
                removal['delta_vi_line'] = _vi_line(b['lam']['line'] - cb['v']['excl_l']) - b['vi']['line']
            if b['ok']['pixel'] and cb['computable']:
                removal['delta_vi_pixel'] = _vi_pixel(
                    b['lam']['pixel'] - cb['v']['excl_p'] + cb['v']['tr'] + b['lam']['cno'], b['crp']) - b['vi']['pixel']
        out['conditions'].append({
            'condition_id': f'{ident[0]}@{ident[1]}', 'pair_id': ident[0], 'electrodes': base['electrodes'],
            'layer': ident[1], 'class': cls,
            'presence': 'both' if ca is not None and cb is not None else ('new' if cb is not None else 'gone'),
            'delta_lambda_line': d_l, 'delta_lambda_pixel': d_p, 'delta_lambda_transfer': d_t,
            'delta_vi_line': v_line, 'delta_vi_pixel': v_pixel,
            'share_line': _ratio(v_line, dvi_line), 'share_pixel': _ratio(v_pixel, dvi_pixel),
            'removal_b': removal})

    cno_a, cno_b = _cno_by_layer(a), _cno_by_layer(b)
    for key in sorted(set(cno_a) | set(cno_b), key=lambda k: (((cno_b.get(k) or cno_a[k])['case_name']), str(k))):
        ra, rb = cno_a.get(key), cno_b.get(key)
        base = rb or ra
        name = base['case_name']
        va = 0.0 if ra is None else ra['lambda']
        vb = 0.0 if rb is None else rb['lambda']
        d_c = _sub(vb, va) if families['pixel'] else None
        v_pixel = _scale(s_pixel, d_c)
        if v_pixel is not None:
            sums['pixel'] += v_pixel
        removal = None
        if rb is not None:
            removal = {'delta_vi_line': 0.0 if b['ok']['line'] else None, 'delta_vi_pixel': None}
            if b['ok']['pixel']:
                removal['delta_vi_pixel'] = (_vi_pixel(b['lam']['pixel'] + b['lam']['cno'] - rb['lambda'], b['crp'])
                                             - b['vi']['pixel'])
        out['cno'].append({
            'case_name': name, 'electrode_id': base['electrode_id'], 'density_layer': base['density_layer'],
            'presence': 'both' if ra is not None and rb is not None else ('new' if rb is not None else 'gone'),
            'delta_lambda_pixel': d_c, 'delta_vi_pixel': v_pixel, 'share_pixel': _ratio(v_pixel, dvi_pixel),
            'removal_b': removal})

    d_shared_l = _sub(b['shared']['line'], a['shared']['line']) if families['line'] else None
    d_shared_p = _sub(b['shared']['pixel'], a['shared']['pixel']) if families['pixel'] else None
    shared_line, shared_pixel = _scale(s_line, d_shared_l), _scale(s_pixel, d_shared_p)
    if shared_line is not None:
        sums['line'] += shared_line
    if shared_pixel is not None:
        sums['pixel'] += shared_pixel
    out['shared'] = {'delta_lambda_line': d_shared_l, 'delta_lambda_pixel': d_shared_p,
                     'delta_vi_line': shared_line, 'delta_vi_pixel': shared_pixel,
                     'share_line': _ratio(shared_line, dvi_line), 'share_pixel': _ratio(shared_pixel, dvi_pixel)}
    if out['crp_difference'] is not None:
        sums['pixel'] += out['crp_difference']['delta_vi_pixel']

    # layers and sizes: the same slopes applied to the layer/size lambda change (shared parts included)
    layer_names = sorted({layer for side in (a, b) for (layer, _d) in _layer_size_lambdas(side, 0)})
    lam_maps = {kind: (_layer_size_lambdas(a, index), _layer_size_lambdas(b, index))
                for index, kind in enumerate(KINDS)}
    slope_of = {'line': s_line, 'pixel': s_pixel}
    for layer in layer_names:
        diameters = sorted({d for kind in KINDS for side_map in lam_maps[kind] for (l, d) in side_map if l == layer})
        row = {'layer': layer, 'delta_vi_line': None, 'delta_vi_pixel': None, 'by_size': []}
        totals = {kind: 0.0 for kind in KINDS}
        for diameter in diameters:
            entry = {'diameter_um': _diameter_json(diameter)}
            for kind in KINDS:
                map_a, map_b = lam_maps[kind]
                change = (_sub(map_b.get((layer, diameter), 0.0), map_a.get((layer, diameter), 0.0))
                          if families[kind] else None)
                value = _scale(slope_of[kind], change)
                entry[f'delta_vi_{kind}'] = value
                totals[kind] = None if value is None or totals[kind] is None else totals[kind] + value
            row['by_size'].append(entry)
        for kind in KINDS:
            row[f'delta_vi_{kind}'] = totals[kind] if families[kind] else None
        out['layers'].append(row)

    checks = {}
    for kind, total in (('line', dvi_line), ('pixel', dvi_pixel)):
        if total is None:
            checks[kind] = {'status': 'not_evaluated', 'sum': None, 'delta_vi': None, 'abs_error': None}
        else:
            error = sums[kind] - total
            checks[kind] = {'status': 'passed' if _within(error, total) else 'failed', 'sum': sums[kind],
                            'delta_vi': total, 'abs_error': abs(error)}
    states = {item['status'] for item in checks.values()}
    out['check'] = {**checks, 'status': 'failed' if 'failed' in states else (
        'not_evaluated' if 'not_evaluated' in states else 'passed')}
    return out


# ---- rebase: B's CA read with A's coefficients ------------------------------------------------------------------

REBASE_BASIS = 'target_ca_reference_coefficients_v1'
VI_DIVISOR = {'line': VI_LINE_DIVISOR, 'pixel': VI_PIXEL_DIVISOR}
SEGMENTS = (('ca', 'S0', 'S1'), ('f', 'S1', 'S2'), ('crp', 'S2', 'S3'), ('density', 'S3', 'S4'))


def _rebase_lam(table, factor, dlayer, diameter, count):
    """(lambda, why): density * count * factor in the order of compare2's `lam`; a missing density counts as 0 only
    for a zero area, otherwise the lambda is None and `why` is the density reason."""
    value, why = table.lookup(dlayer, diameter)
    if value is None:
        return (0.0, None) if count == 0 else (None, why)
    return value * count * factor, None


def _cmp(lhs, rhs):
    if lhs is None or rhs is None:
        return {'status': 'not_evaluated', 'sum': lhs, 'total': rhs, 'abs_error': None}
    error = lhs - rhs
    return {'status': 'passed' if _within(error, rhs) else 'failed', 'sum': lhs, 'total': rhs, 'abs_error': abs(error)}


def _rebase_cno_rows(side, table, factor):
    """Per CNO case: lambda with `table` and `factor` over the unrounded CSV areas of `side`. Returns
    ({case_name: (lambda, [row])}, missing[]); a case without a density for a non-zero area has lambda None."""
    out, missing = {}, []
    for name in side['cno']:
        row = side['cno'][name]
        raw = side['cno_raw'].get(name)
        if raw is None:
            out[name] = (None, [])
            continue
        parts, rows, bad = [], [], False
        for diameter, area in raw:
            value, why = _rebase_lam(table, factor, row['density_layer'], diameter, area)
            density = table.lookup(row['density_layer'], diameter)[0]
            rows.append({'diameter_um': _diameter_json(size_key(diameter)), 'density_a': density,
                         'area_csv_um2': _area(area), 'lambda': value})
            if value is None:
                bad = True
                missing.append({'scope': 'cno', 'kind': 'pixel', 'case_name': name, 'electrode_id': row['electrode_id'],
                                'density_layer': row['density_layer'], 'diameter_um': _diameter_json(size_key(diameter)),
                                'reason': why, 'area_b_um2': _area(area)})
            else:
                parts.append(value)
        out[name] = (None if bad else sum(parts), rows)
    return out, missing


def _merge_cno(side, per_case):
    """{(electrode, density_layer): {'case_name', 'electrode_id', 'density_layer', 'lambda', 'rows'}}: the
    folder-name forms of one electrode and layer are one row, as _cno_by_layer merges them."""
    merged = {}
    for name in sorted(per_case):
        row = side['cno'][name]
        value, rows = per_case[name]
        key = (row['electrode_id'], row['density_layer'])
        if key not in merged:
            merged[key] = {'case_name': name, 'electrode_id': row['electrode_id'], 'density_layer': row['density_layer'],
                           'lambda': value, 'rows': list(rows)}
        else:
            merged[key]['lambda'] = _sum_opt([merged[key]['lambda'], value])
            merged[key]['rows'].extend(rows)
    return merged


def _scale_area(value, ratio):
    """Lambda of state S1a: B's CA read at A's unit area. A ratio of exactly 1 keeps S1's value (bit identical)."""
    if value is None:
        return None
    return value if ratio == 1.0 else value * ratio


def _area_basis(a, b):
    """rebase.area_basis: U_area = unit_size_um[0] x unit_size_um[1] of each product (the value of `products.<role>`)."""
    areas = []
    for side in (a, b):
        unit = side['product'].unit_size_um
        areas.append(None if unit is None else unit[0] * unit[1])
    u_a, u_b = areas
    if u_a is None or u_b is None or u_a <= 0 or u_b <= 0:
        return {'status': 'unavailable', 'reasons': ['unit_size_missing'], 'u_area_a': u_a, 'u_area_b': u_b,
                'ratio': None}
    ratio = u_a / u_b
    ratio = 1.0 if math.isclose(ratio, 1.0, rel_tol=1e-15) else ratio     # R16:3: equal areas up to rounding keep S1a = S1
    return {'status': 'available', 'reasons': [], 'u_area_a': u_a, 'u_area_b': u_b, 'ratio': ratio}


def _rebase_kind(kind, a, b, tables, delta_vi, ratio=None):
    """One kind's rebase (S0..S4, plus S1a when `ratio` = U_area,A / U_area,B is given). Returns a dict: state,
    items, cno, missing, reasons, check."""
    ta = tables['reference'][kind]
    reasons = []
    for role, side in (('reference', a), ('target', b)):
        if side['factors'][kind] is None:
            reasons.append(f'factor_unavailable:{role}:{side["group"].glass_area_reason or "glass_area_pending"}')
        elif not side['ok'][kind]:
            reasons.append(f'side_not_available:{role}')
    result = {'items': [], 'cno': [], 'missing': [], 'reasons': reasons, 'check': {'status': 'not_evaluated'}}
    crp_a, crp_b = (1.0, 1.0) if kind == 'line' else (a['crp'], b['crp'])
    divisor = VI_DIVISOR[kind]
    if reasons:
        result['state'] = {'status': 'unavailable', 'reasons': sorted(reasons)}
        return result
    f_a, f_b = a['factors'][kind], b['factors'][kind]
    missing = result['missing']

    # item lambdas: density of A, area (share) of B, F of A (S1) or of B (S2)
    index = {role: {(i['item_id'], i['class']): i for i in side['ledger_items'] if i['class'] == kind}
             for role, side in (('reference', a), ('target', b))}
    recs, failed = [], False
    total_b = b['lam']['line'] if kind == 'line' else b['x_pixel']
    for key in sorted(set(index['reference']) | set(index['target'])):
        ia, ib = index['reference'].get(key), index['target'].get(key)
        base = ib or ia
        rec = {'item_id': key[0], 'class': kind, 'analysis_layer': base['analysis_layer'],
               'presence': 'both' if ia is not None and ib is not None else ('new' if ib is not None else 'gone'),
               'lambda_a': 0.0 if ia is None else ia['lambda'], 'lambda_b': 0.0 if ib is None else ib['lambda'],
               'by_size': []}
        l1, l2, bad = [], [], False
        for ck, share, value_b in (b['ledger_raw'].get(key, ()) if ib is not None else ()):
            v1, why = _rebase_lam(ta, f_a, ck[1], ck[2], share)
            v2 = _rebase_lam(ta, f_b, ck[1], ck[2], share)[0]
            rec['by_size'].append({'diameter_um': _diameter_json(ck[2]), 'density_layer': ck[1],
                                   'density_a': ta.lookup(ck[1], ck[2])[0], 'area_share_um2': _area(share),
                                   'lambda_s1': v1, 'lambda_s2': v2})
            if v1 is None:
                bad = True
                missing.append({'scope': 'item', 'kind': kind, 'item_id': key[0], 'analysis_layer': rec['analysis_layer'],
                                'density_layer': ck[1], 'diameter_um': _diameter_json(ck[2]), 'reason': why,
                                'area_b_um2': _area(share), 'lambda_b': value_b,
                                'lambda_b_share': _ratio(value_b, total_b)})
            else:
                l1.append(v1)
                l2.append(v2)
        rec['lambda_s1'] = None if bad else math.fsum(l1)
        rec['lambda_s2'] = None if bad else math.fsum(l2)
        if ratio is not None:
            rec['lambda_s1a'] = None if bad else math.fsum([_scale_area(v, ratio) for v in l1])
        if bad or rec['lambda_a'] is None or rec['lambda_b'] is None:
            failed = True
        recs.append(rec)

    def direct(factor):
        values = []
        for ck in sorted(b['entries']):
            entry = b['entries'][ck]
            count = entry['item']['line'] if kind == 'line' else entry['item']['peff']
            value, why = _rebase_lam(ta, factor, ck[1], ck[2], count)
            if value is None:
                if not any(m['density_layer'] == ck[1] and m['diameter_um'] == _diameter_json(ck[2])
                           and m['scope'] != 'cno' for m in missing):
                    missing.append({'scope': 'cell', 'kind': kind, 'analysis_layer': ck[0], 'density_layer': ck[1],
                                    'diameter_um': _diameter_json(ck[2]), 'reason': why, 'area_b_um2': _area(count)})
                return None
            values.append(value)
        return sum(values)

    d1, d2 = direct(f_a), direct(f_b)
    cno_recs, cno_tot = [], {'S1': None, 'S2': None}
    if kind == 'pixel':
        per1, miss1 = _rebase_cno_rows(b, ta, a['factors']['cno'])
        per2, _miss2 = _rebase_cno_rows(b, ta, b['factors']['cno'])
        missing.extend(miss1)
        merged_a, merged_b = _cno_by_layer(a), _cno_by_layer(b)
        m1, m2 = _merge_cno(b, per1), _merge_cno(b, per2)
        for key in sorted(set(merged_a) | set(merged_b), key=lambda k: ((merged_b.get(k) or merged_a[k])['case_name'], str(k))):
            ra, rb = merged_a.get(key), merged_b.get(key)
            base = rb or ra
            rec = {'case_name': base['case_name'], 'electrode_id': base['electrode_id'],
                   'density_layer': base['density_layer'],
                   'presence': 'both' if ra is not None and rb is not None else ('new' if rb is not None else 'gone'),
                   'lambda_a': 0.0 if ra is None else ra['lambda'], 'lambda_b': 0.0 if rb is None else rb['lambda'],
                   'lambda_s1': 0.0 if rb is None else m1[key]['lambda'],
                   'lambda_s2': 0.0 if rb is None else m2[key]['lambda'],
                   **({} if ratio is None else {'lambda_s1a': 0.0 if rb is None else _scale_area(m1[key]['lambda'], ratio)}),
                   'by_size': [] if rb is None else [{'diameter_um': r['diameter_um'], 'density_a': r['density_a'],
                                                      'area_csv_um2': r['area_csv_um2'], 'lambda_s1': r['lambda'],
                                                      'lambda_s2': s['lambda']}
                                                     for r, s in zip(m1[key]['rows'], m2[key]['rows'])]}
            if any(rec[name] is None for name in ('lambda_a', 'lambda_b', 'lambda_s1', 'lambda_s2')):
                failed = True
            cno_recs.append(rec)
        for name, per in (('S1', per1), ('S2', per2)):
            values = [per[row][0] for row in b['cno']]
            cno_tot[name] = None if any(v is None for v in values) else sum(values)
    if failed or d1 is None or d2 is None or (kind == 'pixel' and (cno_tot['S1'] is None or cno_tot['S2'] is None)):
        # a (kind, density layer, diameter) of B with area but no density in A: this kind is not converted
        for rec in recs + cno_recs:
            rec['ca_delta'] = rec['f_delta'] = rec['density_delta'] = None
            rec['design_area_delta'] = rec['unit_size_delta'] = None
        result.update(items=recs, cno=cno_recs)
        why = sorted({f'reference_{m["reason"]}:{kind}:{m["density_layer"]}' for m in missing})
        result['reasons'] = why or ['item_lambda_unavailable']
        result['state'] = {'status': 'unavailable', 'reasons': result['reasons']}
        return result

    lam_x = {'S0': a['lam']['line'] if kind == 'line' else a['x_pixel'],
             'S1': d1 if kind == 'line' else d1 + cno_tot['S1'],
             'S2': d2 if kind == 'line' else d2 + cno_tot['S2'],
             'S4': b['lam']['line'] if kind == 'line' else b['x_pixel']}
    lam_x['S3'] = lam_x['S2']
    crp_of = {'S0': crp_a, 'S1': crp_a, 'S2': crp_a, 'S3': crp_b, 'S4': crp_b}
    d1a = cno1a = None
    if ratio is not None:
        d1a = _scale_area(d1, ratio)
        cno1a = _scale_area(cno_tot['S1'], ratio) if kind == 'pixel' else None
        lam_x['S1a'] = d1a if kind == 'line' else d1a + cno1a
        crp_of['S1a'] = crp_a
    vi = {name: _g(lam_x[name]) / crp_of[name] / divisor for name in lam_x}
    vi['S0'], vi['S4'] = a['vi'][kind], b['vi'][kind]
    state = {'status': 'available', 'reasons': []}
    order = ('S0', 'S1a', 'S1', 'S2', 'S3', 'S4') if kind == 'pixel' else ('S0', 'S1a', 'S1', 'S2', 'S4')
    for name in order:
        if name == 'S1a' and ratio is None:
            continue
        entry = {'x': lam_x[name], 'vi': vi[name]}
        if kind == 'pixel':
            entry['crp'] = crp_of[name]
        if name == 'S1a':
            entry['lambda'] = d1a
            entry['lambda_cno'] = cno1a if kind == 'pixel' else None
        elif name == 'S1':
            entry['lambda'] = d1
            entry['lambda_cno'] = cno_tot['S1'] if kind == 'pixel' else None
        elif name in ('S2', 'S3'):
            entry['lambda'] = d2
            entry['lambda_cno'] = cno_tot['S2'] if kind == 'pixel' else None
        else:
            side, role = (a, 'S0') if name == 'S0' else (b, 'S4')
            entry['lambda'] = side['lam'][kind]
            entry['lambda_cno'] = side['lam']['cno'] if kind == 'pixel' else None
        if kind == 'line':
            del entry['lambda_cno']
        state[name] = entry

    # segments: item value = slope x change of lambda; the slope of every segment is its own secant
    sums, checks = {}, {}
    to_from = {'ca': ('lambda_a', 'lambda_s1'), 'f': ('lambda_s1', 'lambda_s2'), 'density': ('lambda_s2', 'lambda_b')}
    seg_state = {'ca': ('S0', 'S1'), 'f': ('S1', 'S2'), 'density': ('S3', 'S4')}   # S3 = S2 in x, B's CRP in vi
    seg_crp = {'ca': crp_a, 'f': crp_a, 'density': crp_b}
    segments = {}
    for name in ('ca', 'f', 'density'):
        src, dst = seg_state[name]
        slope, method = _slope_g(lam_x[src], lam_x[dst])
        value = slope / (seg_crp[name] * divisor)
        l_from, l_to = to_from[name]
        for rec in recs + cno_recs:
            rec[f'{name}_delta'] = value * (rec[l_to] - rec[l_from])
        item_values = [rec[f'{name}_delta'] for rec in recs]
        cno_values = [rec[f'{name}_delta'] for rec in cno_recs]
        total = math.fsum(item_values + cno_values)
        sums[name] = total
        segments[name] = {'slope': {'value': value, 'method': method}, 'items_sum': math.fsum(item_values),
                          'cno_sum': math.fsum(cno_values) if kind == 'pixel' else None, 'sum': total,
                          'delta_vi': vi[dst] - vi[src]}
        checks[name] = _cmp(total, vi[dst] - vi[src])
    if kind == 'pixel':
        crp_value = _g(lam_x['S2']) * (1 / crp_b - 1 / crp_a) / divisor
        segments['crp'] = {'sum': crp_value, 'delta_vi': vi['S3'] - vi['S2']}
        sums['crp'] = crp_value
        checks['crp'] = _cmp(crp_value, vi['S3'] - vi['S2'])
    area_sums = {}
    if ratio is not None:
        # K3: the old segment `ca` is kept; it is also split into design_area (S0 -> S1a) and unit_size (S1a -> S1).
        # These two are not part of sum_of_segments (that would count the CA change twice).
        area_seg = {'design_area': ('S0', 'S1a', 'lambda_a', 'lambda_s1a'),
                    'unit_size': ('S1a', 'S1', 'lambda_s1a', 'lambda_s1')}
        for name, (src, dst, l_from, l_to) in area_seg.items():
            slope, method = _slope_g(lam_x[src], lam_x[dst])
            value = slope / (crp_a * divisor)
            for rec in recs + cno_recs:
                rec[f'{name}_delta'] = value * (rec[l_to] - rec[l_from])
            item_values = [rec[f'{name}_delta'] for rec in recs]
            cno_values = [rec[f'{name}_delta'] for rec in cno_recs]
            total = math.fsum(item_values + cno_values)
            area_sums[name] = total
            segments[name] = {'slope': {'value': value, 'method': method}, 'items_sum': math.fsum(item_values),
                              'cno_sum': math.fsum(cno_values) if kind == 'pixel' else None, 'sum': total,
                              'delta_vi': vi[dst] - vi[src]}
            checks[name] = _cmp(total, vi[dst] - vi[src])
    else:
        for rec in recs + cno_recs:
            rec['design_area_delta'] = rec['unit_size_delta'] = None
    segments = {name: segments[name] for name in ('ca', 'design_area', 'unit_size', 'f', 'crp', 'density')
                if name in segments}
    state['segments'] = segments
    state['sum_of_segments'] = math.fsum(sums.values())
    state['delta'] = delta_vi
    check = {'s1_lambda': _cmp(math.fsum(r['lambda_s1'] for r in recs), d1),
             's2_lambda': _cmp(math.fsum(r['lambda_s2'] for r in recs), d2),
             'segments': checks, 'total': _cmp(state['sum_of_segments'], delta_vi)}
    if kind == 'pixel':
        check['s1_cno'] = _cmp(math.fsum(r['lambda_s1'] for r in cno_recs), cno_tot['S1'])
        check['s2_cno'] = _cmp(math.fsum(r['lambda_s2'] for r in cno_recs), cno_tot['S2'])
    if ratio is not None:
        check['s1a_lambda'] = _cmp(math.fsum(r['lambda_s1a'] for r in recs), d1a)
        if kind == 'pixel':
            check['s1a_cno'] = _cmp(math.fsum(r['lambda_s1a'] for r in cno_recs), cno1a)
        check['area_split'] = _cmp(area_sums['design_area'] + area_sums['unit_size'], sums['ca'])
    statuses = {check[n]['status'] for n in check if n != 'segments'} | {c['status'] for c in checks.values()}
    check['status'] = 'failed' if 'failed' in statuses else ('not_evaluated' if 'not_evaluated' in statuses else 'passed')
    result.update(state=state, items=recs, cno=cno_recs, check=check)
    return result


def _ratio_table(a, b, kind):
    """r_WH, r_rule, r_M, r_G, r_F of B over A; None when the denominator is 0 or a value is pending."""
    wh_a = a['product'].resolution[0] * a['product'].resolution[1]
    wh_b = b['product'].resolution[0] * b['product'].resolution[1]
    rule_a = None if wh_a == 0 else effective_resolution(a['product'], a['group']) / wh_a
    rule_b = None if wh_b == 0 else effective_resolution(b['product'], b['group']) / wh_b
    glass_a, glass_b = a['group'].glass_area_um2, b['group'].glass_area_um2
    return {'r_WH': _ratio(wh_b, wh_a), 'r_rule': _ratio(rule_b, rule_a),
            'r_M': _ratio(b['group'].weight(kind), a['group'].weight(kind)),
            'r_G': None if glass_a is None or glass_b is None else _ratio(glass_b, glass_a),
            'r_F': _ratio(b['factors'][kind], a['factors'][kind])}


def _rebase(a, b, tables, delta):
    """The `rebase` block of compare2.json: the change of each kind split along CA -> F -> CRP -> density."""
    coefficients = {}
    for role, side in (('reference', a), ('target', b)):
        product, group = side['product'], side['group']
        coefficients[role] = {
            'model_group': group.name, 'resolution': list(product.resolution),
            'effective_resolution': effective_resolution(product, group), 'glass_um2': group.glass_area_um2,
            'weights': {'line': group.weight_line, 'pixel': group.weight_pixel},
            'F': {'line': side['factors']['line'], 'pixel': side['factors']['pixel'], 'cno': side['factors']['cno']},
            'crp': side['crp'],
            'density_sha256': {kind: tables[role][kind].sha256 for kind in KINDS}}
    coefficients['ratios'] = {kind: _ratio_table(a, b, kind) for kind in KINDS}
    area_basis = _area_basis(a, b)
    states, items, cno, missing, reasons, checks = {}, [], [], [], [], {}
    for kind in KINDS:
        got = _rebase_kind(kind, a, b, tables, delta['indicators'][f'delta_vi_{kind}'], area_basis['ratio'])
        states[kind] = got['state']
        items.extend(got['items'])
        if kind == 'pixel':
            cno.extend(got['cno'])
        missing.extend(got['missing'])
        reasons.extend(f'{kind}:{why}' for why in got['reasons'])
        checks[kind] = got['check']
    available = [states[kind]['status'] == 'available' for kind in KINDS]
    status = 'available' if all(available) else ('partial' if any(available) else 'unavailable')
    check_states = {checks[kind]['status'] for kind in KINDS}
    checks['status'] = 'failed' if 'failed' in check_states else (
        'not_evaluated' if 'not_evaluated' in check_states else 'passed')
    missing.sort(key=lambda m: (m['scope'], m.get('kind', ''), str(m.get('item_id', m.get('case_name', ''))),
                                m['density_layer'], m['diameter_um']))
    return {'status': status, 'reasons': sorted(set(reasons)), 'missing': missing, 'coefficients': coefficients,
            'area_basis': area_basis, 'states': states, 'items': items, 'cno': cno, 'check': checks}


def _layer_order(cfg, sides):
    """Layer names in layer_stack order: stack_order ascending over both products (lowest order wins when a layer is
    listed twice, same order by name), then every other layer that appears in the results, by name."""
    order = {}
    for role in ROLES:
        section = cfg.get(role)
        assignment = section.get('condition_assignment') if isinstance(section, dict) else None
        stack = assignment.get('layer_stack') if isinstance(assignment, dict) else None
        for row in stack or ():
            if not isinstance(row, dict):
                continue
            name, number = row.get('actual_layer'), row.get('stack_order')
            if isinstance(name, str) and isinstance(number, (int, float)) and not isinstance(number, bool):
                order[name] = number if name not in order else min(order[name], number)
    seen = set()
    for side in sides:
        for layer, dlayer, _diameter in side['entries']:
            seen.update((layer, dlayer))
        for row in side['cno'].values():
            seen.add(row['density_layer'])
        for item in side['ledger_items']:
            seen.update((item['analysis_layer'], item['upper_layer'], item['lower_layer']))
            seen.update(endpoint['actual_layer'] for endpoint in item['endpoints'])
    seen.discard(None)
    inside = sorted(order, key=lambda name: (order[name], name))
    return inside + sorted(name for name in seen if name not in order)


# ---- output documents -------------------------------------------------------------------------------------------

def _side_json(side):
    group, product = side['group'], side['product']
    conds = []
    for ident in sorted(side['conds'], key=lambda i: (i[1], i[0])):
        cond = side['conds'][ident]
        v = cond['v']
        conds.append({
            'condition_id': f'{ident[0]}@{ident[1]}', 'pair_id': ident[0], 'electrodes': cond['electrodes'],
            'layer': ident[1], 'class': cond['class'],
            'lambda_excl': _add(v['excl_l'], v['excl_p']), 'lambda_transfer': v['tr'],
            'area_excl_um2': _area(cond['area_excl']), 'area_transfer_um2': _area(cond['area_tr']),
            'reasons': cond['reasons'], 'by_size': cond['rows']})
    status = 'available' if not side['reasons'] and side['conservation']['status'] == 'passed' else 'partial'
    reasons = sorted(side['reasons'])
    if side['conservation']['status'] == 'failed':
        reasons.append('conservation_check_failed')
    if side['ledger_check']['status'] == 'failed':
        reasons.append('ledger_check_failed')
        status = 'partial'
    caa, vi, lam = side['caa'], side['vi'], side['lam']
    doc = {
        'status': status, 'reasons': reasons,
        'lambda': {'line': lam['line'], 'pixel': lam['pixel'], 'cno': lam['cno']},
        'caa_line': caa['line'], 'caa_pixel': caa['pixel'], 'vi_line': vi['line'], 'vi_pixel': vi['pixel'],
        'caa_line_rounded': _round(caa['line']), 'caa_pixel_rounded': _round(caa['pixel']),
        'vi_line_rounded': _round(vi['line']), 'vi_pixel_rounded': _round(vi['pixel']),
        'layers': [side['layers'][key] for key in sorted(side['layers'])],
        'conditions': conds,
        'shared': {'lambda_line': side['shared']['line'], 'lambda_pixel': side['shared']['pixel']},
        'cno': [side['cno'][name] for name in sorted(side['cno'])],
        'conservation_check': side['conservation'],
        'ledger_items': side['ledger_items'], 'ledger_check': side['ledger_check']}
    _put_absent(doc, side)
    return doc, status, reasons


def _put_absent(doc, side):
    """Expected cases counted as 0 area (folder absent, confirmed inventory); the key exists only when there are any."""
    if side['absent_cases']:
        doc['absent_expected_cases'] = {'names': list(side['absent_cases']), 'count': len(side['absent_cases'])}


def _full_range_json(side_a, side_b):
    """Indicators over every observed diameter (no condition breakdown), next to a diameter-subset run."""
    docs = {}
    for side in (side_a, side_b):
        doc, status, reasons = _side_json(side)
        docs[side['role']] = {
            'lambda': doc['lambda'], 'caa_line': doc['caa_line'], 'caa_pixel': doc['caa_pixel'],
            'vi_line': doc['vi_line'], 'vi_pixel': doc['vi_pixel'],
            'caa_line_rounded': doc['caa_line_rounded'], 'caa_pixel_rounded': doc['caa_pixel_rounded'],
            'vi_line_rounded': doc['vi_line_rounded'], 'vi_pixel_rounded': doc['vi_pixel_rounded'],
            'status': status, 'reasons': reasons}
        _put_absent(docs[side['role']], side)
    ind = _delta(side_a, side_b)['indicators']
    delta = {key: ind[key] for key in sorted(ind)}
    pct = {}
    for name in ('lambda', 'caa', 'vi'):
        for kind in KINDS:
            base = side_a['lam' if name == 'lambda' else name][kind]
            change = ind[f'delta_{name}_{kind}']
            ratio = _ratio(change, base)
            pct[f'delta_{name}_{kind}_pct'] = None if ratio is None else ratio * 100
    delta.update(pct)
    return {'sides': docs, 'delta': delta}


def _product_json(side, density_tables):
    product, group = side['product'], side['group']
    return {'role': side['role'], 'model_group': group.name, 'crp': side['crp'],
            'resolution': list(product.resolution),
            'effective_resolution': effective_resolution(product, group), 'glass_um2': group.glass_area_um2,
            'glass_area_reason': group.glass_area_reason,
            'weights': {'line': group.weight_line, 'pixel': group.weight_pixel},
            'F_line': side['factors']['line'], 'F_pixel': side['factors']['pixel'], 'F_cno': side['factors']['cno'],
            'hotspot_root': str(product.hotspot_root),
            'unit_size_um': None if product.unit_size_um is None else list(product.unit_size_um),
            'density_files': {kind: {'path': str(table.path), 'file_name': table.path.name, 'sha256': table.sha256}
                              for kind, table in density_tables.items()},
            'source_payload_hash': side['meta']['source_payload_hash'],
            'pair_table_version': side['meta']['pair_table_version'],
            'pair_table_hash': side['meta']['pair_table_hash']}


def _fmt(value):
    return '' if value is None else value

def _csv_documents(doc):
    sides, delta = doc['sides'], doc['delta']
    summary_fields = ['scope', 'model_group', 'status', 'lambda_line', 'lambda_pixel', 'lambda_cno', 'caa_line',
                      'caa_pixel', 'vi_line', 'vi_pixel', 'vi_line_rounded', 'vi_pixel_rounded', 'slope_line',
                      'slope_pixel', 'conservation', 'reasons']
    summary = []
    for role in ROLES:
        side = sides[role]
        summary.append({'scope': role, 'model_group': doc['products'][role]['model_group'], 'status': side['status'],
                        'lambda_line': side['lambda']['line'], 'lambda_pixel': side['lambda']['pixel'],
                        'lambda_cno': side['lambda']['cno'], 'caa_line': side['caa_line'],
                        'caa_pixel': side['caa_pixel'], 'vi_line': side['vi_line'], 'vi_pixel': side['vi_pixel'],
                        'vi_line_rounded': side['vi_line_rounded'], 'vi_pixel_rounded': side['vi_pixel_rounded'],
                        'conservation': side['conservation_check']['status'], 'reasons': ';'.join(side['reasons'])})
    ind = delta['indicators']
    summary.append({'scope': 'delta', 'status': delta['check']['status'],
                    'lambda_line': ind['delta_lambda_line'], 'lambda_pixel': ind['delta_lambda_pixel'],
                    'lambda_cno': ind['delta_lambda_cno'], 'caa_line': ind['delta_caa_line'],
                    'caa_pixel': ind['delta_caa_pixel'], 'vi_line': ind['delta_vi_line'],
                    'vi_pixel': ind['delta_vi_pixel'], 'vi_line_rounded': ind['delta_vi_line_rounded'],
                    'vi_pixel_rounded': ind['delta_vi_pixel_rounded'],
                    'slope_line': (delta['slopes'].get('line') or {}).get('value'),
                    'slope_pixel': (delta['slopes'].get('pixel') or {}).get('value'),
                    'conservation': delta['check']['status']})
    if doc.get('formula_basis') == 'diameter_subset':  # only with lambda_diameters_um; else the CSV bytes stay as before
        summary_fields = summary_fields + ['formula_basis', 'diameters_um']
        listed = ';'.join(f'{size:g}' for size in doc['inputs']['diameter_filter']['diameters_um'])
        for row in summary:
            row['formula_basis'], row['diameters_um'] = 'diameter_subset', listed

    layer_fields = ['scope', 'layer', 'density_layer', 'diameter_um', 'density_line', 'density_pixel',
                    'line_area_um2', 'pixel_eff_area_um2', 'transfer_area_um2', 'lambda_line', 'lambda_pixel',
                    'share_line', 'share_pixel', 'delta_vi_line', 'delta_vi_pixel', 'status', 'reasons']
    layers = []
    for role in ROLES:
        for row in sides[role]['layers']:
            layers.append({'scope': role, 'layer': row['layer'], 'density_layer': row['density_layer'],
                           'lambda_line': row['lambda_line'], 'lambda_pixel': row['lambda_pixel'],
                           'share_line': row['share_line'], 'share_pixel': row['share_pixel']})
            for size in row['by_size']:
                layers.append({'scope': role, 'layer': row['layer'], 'density_layer': row['density_layer'],
                               **{k: size[k] for k in ('diameter_um', 'density_line', 'density_pixel',
                                                       'line_area_um2', 'pixel_eff_area_um2', 'transfer_area_um2',
                                                       'lambda_line', 'lambda_pixel', 'status')},
                               'reasons': ';'.join(size['reasons'])})
    for row in delta['layers']:
        layers.append({'scope': 'delta', 'layer': row['layer'], 'delta_vi_line': row['delta_vi_line'],
                       'delta_vi_pixel': row['delta_vi_pixel']})
        for size in row['by_size']:
            layers.append({'scope': 'delta', 'layer': row['layer'], 'diameter_um': size['diameter_um'],
                           'delta_vi_line': size['delta_vi_line'], 'delta_vi_pixel': size['delta_vi_pixel']})

    cond_fields = ['condition_id', 'pair_id', 'electrodes', 'layer', 'class', 'presence', 'reference_lambda_excl',
                   'reference_lambda_transfer', 'target_lambda_excl', 'target_lambda_transfer', 'reference_area_excl_um2',
                   'target_area_excl_um2', 'delta_lambda_line', 'delta_lambda_pixel', 'delta_lambda_transfer',
                   'delta_vi_line', 'delta_vi_pixel', 'share_line', 'share_pixel', 'removal_b_delta_vi_line',
                   'removal_b_delta_vi_pixel']
    by_side = {role: {c['condition_id']: c for c in sides[role]['conditions']} for role in ROLES}
    conds = []
    for row in delta['conditions']:
        out = {k: row[k] for k in ('condition_id', 'pair_id', 'layer', 'class', 'presence', 'delta_lambda_line',
                                   'delta_lambda_pixel', 'delta_lambda_transfer', 'delta_vi_line', 'delta_vi_pixel',
                                   'share_line', 'share_pixel')}
        out['electrodes'] = '-'.join(row['electrodes'] or ())
        for role in ROLES:
            cond = by_side[role].get(row['condition_id'])
            if cond is not None:
                out[f'{role}_lambda_excl'] = cond['lambda_excl']
                out[f'{role}_lambda_transfer'] = cond['lambda_transfer']
                if role == 'reference':
                    out['reference_area_excl_um2'] = cond['area_excl_um2']
                else:
                    out['target_area_excl_um2'] = cond['area_excl_um2']
        if row['removal_b'] is not None:
            out['removal_b_delta_vi_line'] = row['removal_b']['delta_vi_line']
            out['removal_b_delta_vi_pixel'] = row['removal_b']['delta_vi_pixel']
        conds.append(out)
    shared = delta['shared']
    conds.append({'condition_id': 'shared', 'class': 'shared', 'delta_lambda_line': shared['delta_lambda_line'],
                  'delta_lambda_pixel': shared['delta_lambda_pixel'], 'delta_vi_line': shared['delta_vi_line'],
                  'delta_vi_pixel': shared['delta_vi_pixel'], 'share_line': shared['share_line'],
                  'share_pixel': shared['share_pixel']})
    if delta['crp_difference'] is not None:
        conds.append({'condition_id': 'crp_difference', 'class': 'crp_difference',
                      'delta_vi_pixel': delta['crp_difference']['delta_vi_pixel'],
                      'share_pixel': delta['crp_difference']['share_pixel']})

    cno_fields = ['case_name', 'electrode_id', 'density_layer', 'presence', 'reference_csv_status', 'target_csv_status',
                  'reference_lambda', 'target_lambda', 'delta_lambda_pixel', 'delta_vi_pixel', 'share_pixel',
                  'removal_b_delta_vi_pixel', 'reasons']
    cno_by_side = {role: _cno_by_layer({'cno': {c['case_name']: c for c in sides[role]['cno']}}) for role in ROLES}
    cno = []
    for row in delta['cno']:
        out = {k: row[k] for k in ('case_name', 'electrode_id', 'density_layer', 'presence', 'delta_lambda_pixel',
                                   'delta_vi_pixel', 'share_pixel')}
        reasons = []
        for role in ROLES:
            item = cno_by_side[role].get((row['electrode_id'], row['density_layer']))
            if item is not None:
                out[f'{role}_csv_status'] = item['csv_status']
                out[f'{role}_lambda'] = item['lambda']
                reasons.extend(f'{role}:{why}' for why in item['reasons'])
        if row['removal_b'] is not None:
            out['removal_b_delta_vi_pixel'] = row['removal_b']['delta_vi_pixel']
        out['reasons'] = ';'.join(reasons)
        cno.append(out)
    return [('compare2_summary.csv', summary_fields, summary), ('compare2_layers.csv', layer_fields, layers),
            ('compare2_conditions.csv', cond_fields, conds), ('compare2_cno.csv', cno_fields, cno)]


def _json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def _owned_output(out: Path):
    """True for a missing/empty folder or one holding only compare2 files."""
    if not out.exists():
        return True
    if not out.is_dir():
        return False
    for path in sorted(out.iterdir()):
        if path.name.startswith('.compare2-'):
            continue
        if path.name not in OWNED_NAMES:
            return False
        if path.name in ('execution.json', 'error.json'):
            try:
                if json.loads(path.read_text(encoding='utf-8')).get('command') != EXECUTION_COMMAND:
                    return False
            except (OSError, ValueError, AttributeError):
                return False
    return True


def _publish(out: Path, files):
    """Write each file into a temporary folder inside out, then os.replace them one by one."""
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix='.compare2-', dir=str(out)))
    try:
        for name, text in files:
            with open(str(tmp / name), 'w', encoding='utf-8', newline='\n') as handle:  # write_text(newline=) is 3.10+
                handle.write(text)
        for name, _text in files:
            os.replace(str(tmp / name), str(out / name))
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


def _csv_text(fields, rows):
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({name: _fmt(row.get(name)) for name in fields})
    return '﻿' + buffer.getvalue()  # utf-8-sig


def build_compare2(config_path):
    """Load both sides and return (document, status, reasons); writes nothing."""
    from .pipeline import _compare_options, load_numeric_config
    cfg2 = load_compare2_config(config_path)
    cfg = load_numeric_config(cfg2.numeric_config)
    if cfg.get('mode') != 'real':
        raise NumericContractError('compare2_real_mode_required', 'numeric_config mode must be real')
    top = _compare_options(cfg)[1]
    sides, tables, extracted = {}, {}, {}
    for role in ROLES:
        product = getattr(cfg2, role)
        if not Path(product.hotspot_root).is_dir():
            raise NumericContractError('compare2_hotspot_root_missing', str(product.hotspot_root))
        tables[role] = load_density(cfg2.density_dir, product.model_group)
    metas = {}
    for role in ROLES:
        extracted[role], metas[role] = _load_side(cfg, role, top)
    listed = cfg2.lambda_diameters_um
    subset = None if listed is None else set(listed)
    full_sides = {}
    for role in ROLES:
        product = getattr(cfg2, role)
        args = (role, product, cfg2.model_groups[product.model_group], tables[role], extracted[role], metas[role])
        sides[role] = _compute_side(*args, diameters=subset)
        if subset is not None:  # same extraction, no filter: the all-diameter indicators next to the subset
            full_sides[role] = _compute_side(*args)
    delta = _delta(sides['reference'], sides['target'])
    side_docs, reasons, partial = {}, [], False
    for role in ROLES:
        side_docs[role], status, side_reasons = _side_json(sides[role])
        partial = partial or status != 'available'
        reasons.extend(f'{role}:{why}' for why in side_reasons)
    if delta['check']['status'] == 'failed':
        reasons.append('delta_check_failed')
    if delta['check']['status'] != 'passed':
        partial = True
    document = {
        'schema_version': SCHEMA_VERSION,
        'inputs': {
            'compare2_config': {'path': str(cfg2.config_path), 'sha256': cfg2.config_sha256},
            'numeric_config': {'path': str(cfg['_config_path']), 'sha256': cfg['_config_sha256']},
            'density_dir': str(cfg2.density_dir),
            'manifests': {role: {'source_payload_hash': metas[role]['source_payload_hash'],
                                 'input_hash': metas[role]['input_hash'],
                                 **({'distance_filter': metas[role]['distance_filter']}
                                    if 'distance_filter' in metas[role] else {}),
                                 **({'pair_exclusions': metas[role]['pair_exclusions']}
                                    if 'pair_exclusions' in metas[role] else {})} for role in ROLES},
            'case_exclusions': {role: metas[role].get('case_exclusions') for role in ROLES},
            'constants': {'vi_line_divisor': VI_LINE_DIVISOR, 'vi_pixel_divisor': VI_PIXEL_DIVISOR,
                          'cno_factor': CNO_FACTOR, 'crp_default_other': CRP_DEFAULT_OTHER,
                          'crp_one_groups': list(CRP_ONE_GROUPS), 'round_digits': ROUND_DIGITS,
                          'conservation_tolerance': CONSERVATION_TOL, 'slope_limit_eps': SLOPE_LIMIT_EPS,
                          'ratio_null_below': RATIO_EPS},
            'area_basis': AREA_BASIS, 'ledger_basis': LEDGER_BASIS, 'rebase_basis': REBASE_BASIS,
            'layer_order': _layer_order(cfg, [sides[role] for role in ROLES])},
        'products': {role: _product_json(sides[role], tables[role]) for role in ROLES},
        'sides': side_docs,
        'delta': delta,
        'rebase': _rebase(sides['reference'], sides['target'], tables, delta)}
    if subset is not None:
        observed = set().union(*(side['observed'] for side in full_sides.values()))
        not_observed = [key for key in listed if key not in observed]
        document['formula_basis'] = 'diameter_subset'
        document['inputs']['diameter_filter'] = {
            'mode': 'listed', 'diameters_um': [_diameter_json(key) for key in listed],
            'observed_diameters_um': [_diameter_json(key) for key in sorted(observed)],
            'not_observed_um': [_diameter_json(key) for key in not_observed]}
        document['full_range'] = _full_range_json(full_sides['reference'], full_sides['target'])
        warnings = [f'full_range_partial:{role}' for role in ROLES
                    if document['full_range']['sides'][role]['status'] != 'available']
        if warnings:  # informational: the top-level status and exit code do not change
            document['inputs']['diameter_filter']['warnings'] = warnings
        if not_observed:
            partial = True
            reasons.extend(f'diameter_filter_not_observed:{key}' for key in not_observed)
    return document, ('partial' if partial else 'available'), reasons


def run_compare2(config_path, out=None):
    """Compute and publish compare2 outputs; returns the execution record (exit_code 0, 3 or raises)."""
    started = time.time()
    cfg2 = load_compare2_config(config_path)
    output = Path(out) if out is not None else cfg2.output_dir
    if not _owned_output(output):
        raise NumericContractError('compare2_output_not_owned', f'{output}: use a new folder or a previous compare2 folder')
    document, status, reasons = build_compare2(config_path)
    texts = [(name, _csv_text(fields, rows)) for name, fields, rows in _csv_documents(document)]
    texts.append(('compare2.json', _json_text(document)))
    exit_code = 0 if status == 'available' else 3
    outputs = [str(output / name) for name in ('compare2.json', 'compare2_summary.csv', 'compare2_layers.csv',
                                               'compare2_conditions.csv', 'compare2_cno.csv')]
    record = {'command': EXECUTION_COMMAND, 'status': status, 'reasons': reasons, 'outputs': outputs,
              'config': str(cfg2.config_path), 'numeric_config': str(cfg2.numeric_config),
              'started_at': datetime.fromtimestamp(started, timezone.utc).isoformat(),
              'finished_at': datetime.now(timezone.utc).isoformat(),
              'elapsed_seconds': round(time.time() - started, 3), 'exit_code': exit_code}
    texts.append(('execution.json', _json_text(record)))  # every text exists before the first file is written
    _publish(output, texts)
    (output / 'error.json').unlink(missing_ok=True)
    return record


def main_compare2(config, out, print_result, error_stream=None):
    """CLI body: prints the record, or writes error.json (only) and returns 2."""
    error_stream = error_stream or sys.stderr
    output = None
    if out is not None:
        output = Path(out)
    try:
        if output is None:
            output = load_compare2_config(config).output_dir
        record = run_compare2(config, out)
    except (NumericContractError, OSError, ValueError, TypeError, KeyError) as exc:
        code = getattr(exc, 'code', 'input_or_config_error')
        print(f'오류: {exc}', file=error_stream)
        if output is not None and getattr(exc, 'code', None) != 'compare2_output_not_owned' and _owned_output(output):
            try:
                # a failed run leaves no stale results next to error.json
                for name in OWNED_NAMES - {'error.json'}:
                    (output / name).unlink(missing_ok=True)
                _publish(output, [('error.json', _json_text({'command': EXECUTION_COMMAND, 'code': code,
                                                            'detail': str(exc), 'exit_code': 2}))])
            except Exception as publish_error:  # still exit 2; say why error.json is missing
                print(f'오류: error.json 을 쓰지 못했습니다 (원래 오류 {code}): {publish_error}', file=error_stream)
        return 2
    print_result(record)
    return record['exit_code']
