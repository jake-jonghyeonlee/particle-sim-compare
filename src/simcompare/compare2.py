"""compare2: line/pixel lambda, CAA and VI per side, their change decomposition and removal impacts.

Every value is computed from unrounded floats; `*_rounded` fields (6 digits) are for display only.
Formulas: docs/implementation_memory.json § compare2_calculation_2026_11_05 and the T3 contract.
lambda = density(count/glass) * CA(um^2, observed in the 2x2 crop) * factor, where the factor holds /4 and the
panel scale (compare2_inputs.lambda_factor). A side with a missing input reports null instead of a guess.
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
                              lambda_factor, load_compare2_config, load_density, size_key)
from .numeric_schema import NumericContractError, has_short_cases

SCHEMA_VERSION = 'compare2_v1'
AREA_BASIS = 'crop_observed_area_div4_user_approved_2026_11_05'
CONSERVATION_TOL = 1e-12
SLOPE_LIMIT_EPS = 1e-12
RATIO_EPS = 1e-15
ROLES = ('reference', 'target')
KINDS = ('line', 'pixel')
OWNED_NAMES = {'compare2.json', 'compare2_summary.csv', 'compare2_layers.csv', 'compare2_conditions.csv',
               'compare2_cno.csv', 'execution.json', 'error.json'}
EXECUTION_COMMAND = 'compare2'


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
            'tr': defaultdict(float)}


def _extract(areas):
    """Areas (um^2) per (analysis_layer, density_layer, diameter) and per condition; masks are dropped."""
    cells, classes, electrodes = {}, {}, {}
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
        for pid, pair in group.pair_membership.items():
            electrodes[(pid, key.analysis_layer)] = list(pair)
    return {'cells': cells, 'classes': classes, 'electrodes': electrodes}


def _load_side(cfg, role, top_buckets):
    """Load one side, measure it and return plain numbers; the dataset and the masks are released on return."""
    from .linepixel import compute_areas
    from .pipeline import _configured_real, _real_source
    section = cfg.get(role)
    real = _real_source(cfg, section)
    if real is None:
        raise NumericContractError('compare2_real_source_required', f'{role}: real_hotspot_v1 data_dir needed')
    detail = {'bucket_detail': 'summary', 'top_buckets': top_buckets}
    configured = _configured_real(cfg, section, real, detail)
    if configured is None:
        raise NumericContractError('compare2_condition_assignment_required', f'{role}: condition_assignment needed')
    dataset = configured[1]
    if not has_short_cases(dataset.cases):
        raise NumericContractError('compare2_side_without_cases', role)
    manifest = real[1]
    meta = {'source_payload_hash': manifest['source_payload_hash'],
            'expected_case_coverage_status': manifest.get('expected_case_coverage_status'),
            'same_name_cases_unresolved': bool(dataset.provenance.get('unresolved_same_name_cases'))}
    result = compute_areas(dataset, **detail, condition_stats=True)
    extracted = _extract(result)
    meta['pair_table_version'] = result.pair_table_version
    meta['pair_table_hash'] = result.pair_table_hash
    meta['input_hash'] = result.input_hash
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
        if not item['ok']:
            notes.extend(sorted(item['reasons']))
            reasons.add('group_not_available')
            ok['line'] = ok['pixel'] = False
        entries[ck] = {'item': item, 'density': density, 'notes': notes, 'ok': item['ok']}

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

    # CNO
    cno_rows, cno_ok = {}, True
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
            parts, by_size = [], []
            for diameter, area in sorted(case.rows.items()):
                if diameters is not None and size_key(diameter) not in diameters:
                    continue
                value, why = tables['pixel'].lookup(case.density_layer, diameter)
                if value is None:
                    row['reasons'].append(f'{why}:pixel:{case.density_layer}')
                    parts = None
                    break
                parts.append(value * area * factors['cno'])
                by_size.append({'diameter_um': _diameter_json(diameter), 'density_pixel': value, 'area_csv_um2': _area(area),
                                'lambda': value * area * factors['cno']})
            if parts is not None:
                row['lambda'] = sum(parts)
                row['by_size'] = by_size      # the per-diameter density the location formula of compare2-report reads
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
        'shared': shared, 'cno': cno_rows, 'lam': {'line': total_line, 'pixel': total_pixel, 'cno': total_cno},
        'x_pixel': x_pixel,
        'caa': {'line': None if total_line is None else _g(total_line),
                'pixel': None if x_pixel is None or not ok['pixel'] else _g(x_pixel) / crp},
        'meta': meta, 'observed': observed}
    side['vi'] = {'line': None if side['caa']['line'] is None else side['caa']['line'] / VI_LINE_DIVISOR,
                  'pixel': None if side['caa']['pixel'] is None else side['caa']['pixel'] / VI_PIXEL_DIVISOR}
    side['conservation'] = _conservation(side)
    return side


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
    caa, vi, lam = side['caa'], side['vi'], side['lam']
    return {
        'status': status, 'reasons': reasons,
        'lambda': {'line': lam['line'], 'pixel': lam['pixel'], 'cno': lam['cno']},
        'caa_line': caa['line'], 'caa_pixel': caa['pixel'], 'vi_line': vi['line'], 'vi_pixel': vi['pixel'],
        'caa_line_rounded': _round(caa['line']), 'caa_pixel_rounded': _round(caa['pixel']),
        'vi_line_rounded': _round(vi['line']), 'vi_pixel_rounded': _round(vi['pixel']),
        'layers': [side['layers'][key] for key in sorted(side['layers'])],
        'conditions': conds,
        'shared': {'lambda_line': side['shared']['line'], 'lambda_pixel': side['shared']['pixel']},
        'cno': [side['cno'][name] for name in sorted(side['cno'])],
        'conservation_check': side['conservation']}, status, reasons


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
            'resolution': list(product.resolution), 'glass_um2': group.glass_area_um2,
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
                                 'input_hash': metas[role]['input_hash']} for role in ROLES},
            'constants': {'vi_line_divisor': VI_LINE_DIVISOR, 'vi_pixel_divisor': VI_PIXEL_DIVISOR,
                          'cno_factor': CNO_FACTOR, 'crp_default_other': CRP_DEFAULT_OTHER,
                          'crp_one_groups': list(CRP_ONE_GROUPS), 'round_digits': ROUND_DIGITS,
                          'conservation_tolerance': CONSERVATION_TOL, 'slope_limit_eps': SLOPE_LIMIT_EPS,
                          'ratio_null_below': RATIO_EPS},
            'area_basis': AREA_BASIS},
        'products': {role: _product_json(sides[role], tables[role]) for role in ROLES},
        'sides': side_docs,
        'delta': delta}
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
