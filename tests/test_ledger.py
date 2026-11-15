"""ledger/T4: the `ledger` command on synthetic compare2.json documents (no mask, no real hotspot data).

The builder below makes a compare2.json whose numbers obey the compare2 identities (lambda sums, secant slope, delta
conditions + shared), so the ledger sums must pass; a test tampers one number to see the check fail."""
import copy
import hashlib
import json
from collections import namedtuple

import pytest
import yaml

from simcompare import ledger
from simcompare.cli import main
from simcompare.compare2 import VI_LINE_DIVISOR, VI_PIXEL_DIVISOR, _g, _slope_g
from simcompare.compare2_inputs import load_compare2_config

ROLES = ('reference', 'target')


# ---- builder ---------------------------------------------------------------------------------------------------------

def item(ftype, ends, lam, layer=None, upper=None, lower=None, sizes=None):
    """One item definition. Lateral: give layer. Vertical: give upper and lower."""
    upper = upper or layer
    return {'ftype': ftype, 'ends': ends, 'lam': lam, 'upper': upper, 'lower': lower or upper, 'sizes': sizes}


def lateral(ends, lam, layer, **kw):
    return item('lateral_short', [(e, layer) for e in ends], lam, layer=layer, **kw)


def vertical(ends, lam, upper, lower, **kw):
    return item('vertical_short', ends, lam, upper=upper, lower=lower, **kw)


def pair(pid, electrodes, cls, a, b, **kw):
    """a / b: list of item definitions (an empty list = present without items), None = the pair is absent."""
    return dict({'pid': pid, 'electrodes': list(electrodes), 'cls': cls, 'a': a, 'b': b}, **kw)


def _item_doc(p, d, kind):
    ends = sorted(d['ends'])
    item_id = '|'.join((p['pid'], d['ftype'], d['upper'], d['lower'], ','.join(f'{e}@{l}' for e, l in ends)))
    sizes = d['sizes'] or [d['lam']]
    return {'item_id': item_id, 'pair_id': p['pid'], 'class': kind, 'failure_type': d['ftype'],
            'analysis_layer': d['upper'], 'upper_layer': d['upper'], 'lower_layer': d['lower'],
            'electrodes': list(p['electrodes']), 'endpoints': [{'electrode_id': e, 'actual_layer': l} for e, l in ends],
            'lambda': d['lam'], 'area_share_um2': 1.0,
            'by_size': [{'diameter_um': float(3 + i), 'density_layer': d['upper'], 'area_share_um2': 1.0, 'lambda': lam}
                        for i, lam in enumerate(sizes)],
            'transferred_out_area_um2': 0.0, 'computable': d['lam'] is not None, 'reasons': []}


def build_doc(pairs, cno=(), crp=(1.0, 1.0), pixel_null=False, line_null=False, cfg_sha='0' * 64):
    """cno: [(electrode, density_layer, lambda_a or None = absent, lambda_b or None = absent)]."""
    sides, lam_all, excl, shared = {}, {}, {}, {}
    for index, role in enumerate(ROLES):
        items, conds = [], []
        lam = {'line': 0.0, 'pixel': 0.0}
        shared[role] = {'line': 0.0, 'pixel': 0.0}
        for p in pairs:
            defs = p['a' if index == 0 else 'b']
            noncomp = bool(p.get('noncomp_' + 'ab'[index]))
            excl[(role, p['pid'])] = {'line': 0.0, 'pixel': 0.0}
            if defs is None:
                continue
            conds.append({'condition_id': p['pid'], 'pair_id': p['pid'], 'electrodes': p['electrodes'],
                          'layer': defs[0]['upper'] if defs else 'L0', 'class': None if noncomp else p['cls'],
                          'reasons': ['condition_not_computable'] if noncomp else []})
            total = sum(d['lam'] for d in defs)
            lam[p['cls']] += total
            frac = p.get('shared_frac', 0.0)
            excl[(role, p['pid'])][p['cls']] = total * (1.0 - frac)
            shared[role][p['cls']] += total * frac
            items.extend(_item_doc(p, d, p['cls']) for d in defs)
        cno_rows = []
        for electrode, layer, la, lb in cno:
            value = la if index == 0 else lb
            if value is not None:
                cno_rows.append({'case_name': f'{electrode}_{layer}', 'electrode_id': electrode, 'density_layer': layer,
                                 'csv_status': 'observed', 'lambda': None if pixel_null else value,
                                 'by_size': [{'diameter_um': 3.0, 'lambda': value}], 'reasons': []})
        lam['cno'] = sum(r['lambda'] for r in cno_rows) if not pixel_null else None
        lam_all[role] = lam
        x = lam['pixel'] + sum(r['by_size'][0]['lambda'] for r in cno_rows)
        lam['x'] = x
        sides[role] = {'status': 'available', 'reasons': [], 'lambda': {k: lam[k] for k in ('line', 'pixel', 'cno')},
                       'vi_line': None if line_null else _g(lam['line']) / VI_LINE_DIVISOR,
                       'vi_pixel': None if pixel_null else _g(x) / crp[index] / VI_PIXEL_DIVISOR,
                       'conditions': conds, 'ledger_items': items, 'cno': cno_rows}
    la, lb = lam_all['reference'], lam_all['target']
    slopes, indicators, crp_diff = {}, {}, None
    s = {'line': None, 'pixel': None}
    if not line_null:
        slope, method = _slope_g(la['line'], lb['line'])
        s['line'] = slope / VI_LINE_DIVISOR
        slopes['line'] = {'value': s['line'], 'method': method}
    if not pixel_null:
        slope, method = _slope_g(la['x'], lb['x'])
        s['pixel'] = slope / (crp[1] * VI_PIXEL_DIVISOR)
        slopes['pixel'] = {'value': s['pixel'], 'method': method}
        if crp[0] != crp[1]:
            crp_diff = {'delta_vi_pixel': _g(la['x']) * (1 / crp[1] - 1 / crp[0]) / VI_PIXEL_DIVISOR}
    for kind in ('line', 'pixel'):
        a, b = sides['reference'][f'vi_{kind}'], sides['target'][f'vi_{kind}']
        indicators[f'delta_vi_{kind}'] = None if a is None or b is None else b - a

    def scaled(kind, change):
        return None if s[kind] is None or change is None else s[kind] * change

    conditions = []
    for p in pairs:
        row = {'condition_id': p['pid'], 'pair_id': p['pid'], 'layer': 'L', 'class': p['cls']}
        noncomp = p.get('noncomp_a') or p.get('noncomp_b')
        for kind in ('line', 'pixel'):
            change = excl[('target', p['pid'])][kind] - excl[('reference', p['pid'])][kind]
            row[f'delta_vi_{kind}'] = None if noncomp else scaled(kind, change)
        conditions.append(row)
    delta = {'attribution': 'secant', 'indicators': indicators, 'slopes': slopes, 'conditions': conditions,
             'shared': {f'delta_vi_{kind}': scaled(kind, shared['target'][kind] - shared['reference'][kind])
                        for kind in ('line', 'pixel')}, 'crp_difference': crp_diff}
    return {'schema_version': 'compare2_v1',
            'inputs': {'compare2_config': {'sha256': cfg_sha}, 'ledger_basis': 'pair_share_v1'},
            'products': {role: {'model_group': 'mobile', 'pair_table_hash': 'h' * 64, 'crp': crp[i]}
                         for i, role in enumerate(ROLES)},
            'sides': sides, 'delta': delta}


World = namedtuple('World', 'base c2 config out doc')


def make_world(tmp_path, pairs, dl1=None, mutate=None, config_mutate=None, **kw):
    """dl1: {'reference': {electrode: [layers]}, 'target': {...}} writes the DL1 folders and sets the config."""
    base = tmp_path
    cfg = {'schema_version': 'compare2_config_v1', 'numeric_config': 'numeric.yaml', 'output_dir': 'out',
           'density_dir': 'density',
           'model_groups': {'mobile': {'glass_area': {'value': 50.0, 'unit': 'um2'}, 'weight_line': 1.0,
                                       'weight_pixel': 1.0}},
           'products': {role: {'model_group': 'mobile', 'resolution': [100, 100], 'hotspot_root': str(base / 'hot' / role)}
                        for role in ROLES}}
    if dl1 is not None:
        cfg['report'] = {'dl1_root': str(base / 'dl1')}
        for role in ROLES:
            cfg['products'][role]['dl1_model'] = role
            (base / 'dl1' / role).mkdir(parents=True, exist_ok=True)
            for electrode, layers in dl1[role].items():
                folder = base / 'dl1' / role / electrode
                folder.mkdir(parents=True, exist_ok=True)
                for number, layer in enumerate(layers, 1):
                    (folder / f'{number}-{layer}.png').write_bytes(b'')
    if config_mutate:
        config_mutate(cfg)
    config = base / 'compare2.yaml'
    config.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    doc = build_doc(pairs, cfg_sha=load_compare2_config(config).config_sha256, **kw)
    if mutate:
        mutate(doc)
    c2 = base / 'c2'
    c2.mkdir()
    (c2 / 'compare2.json').write_text(json.dumps(doc), encoding='utf-8')
    return World(base, c2, config, base / 'ledger_out', doc)


def go(world, out=None):
    return ledger.main_ledger(str(world.c2), str(world.config), str(out or world.out), lambda record: None)


def result(world):
    return json.loads((world.out / 'ledger.json').read_text(encoding='utf-8'))


def by_pair(doc):
    return {row['pair_id']: row for row in doc['pairs']}


def error_code(out):
    return json.loads((out / 'error.json').read_text(encoding='utf-8'))['code']


# ---- the mockup v8 data (README section 14) --------------------------------------------------------------------------

ELECTRODES_A = {'Data': ['SD1'], 'ELVDD': ['SD1'], 'Scan1': ['GAT1'], 'EM': ['GAT1'], 'Anode': ['PXL'], 'Vint': ['SD1'],
                'Gnode': ['SD1'], 'Scan2': ['GAT1'], 'Vinit2': ['GAT1']}
ELECTRODES_B = {'Data': ['SD1'], 'ELVDD': ['SD1'], 'Scan1': ['GAT2'], 'EM': ['GAT2'], 'Anode': ['PXL'], 'Vint': ['SD1'],
                'Gnode': ['SD2'], 'Scan2': ['SD1'], 'Scan3': ['GAT2'], 'Vref': ['SD2']}


def mockup_pairs():
    return [
        pair('P1', ['Data', 'ELVDD'], 'line', [lateral(['Data', 'ELVDD'], .0120, 'SD1')],
             [lateral(['Data', 'ELVDD'], .0150, 'SD1')]),
        pair('P2', ['Scan1', 'EM'], 'line', [lateral(['Scan1', 'EM'], .0060, 'GAT1')],
             [lateral(['Scan1', 'EM'], .0078, 'GAT2')]),
        pair('P3', ['Anode', 'Vint'], 'pixel', [lateral(['Anode', 'Vint'], .0200, 'PXL')],
             [lateral(['Anode', 'Vint'], .0200, 'PXL')]),
        pair('P4', ['Gnode', 'Anode'], 'pixel', [vertical([('Gnode', 'SD1'), ('Anode', 'PXL')], .0090, 'SD1', 'PXL')],
             [vertical([('Gnode', 'SD2'), ('Anode', 'PXL')], .0050, 'SD2', 'PXL')]),
        pair('P5', ['Scan2', 'Vint'], 'line', None, [lateral(['Scan2', 'Vint'], .0065, 'SD1')]),
        pair('P6', ['Scan3', 'Data'], 'line', None, [vertical([('Scan3', 'GAT2'), ('Data', 'SD1')], .0040, 'GAT2', 'SD1')]),
        pair('P7', ['Scan3', 'Vref'], 'line', None, [vertical([('Scan3', 'GAT2'), ('Vref', 'SD2')], .0025, 'GAT2', 'SD2')]),
        pair('P8', ['Scan2', 'Vinit2'], 'line', [lateral(['Scan2', 'Vinit2'], .0050, 'GAT1')], None)]


MOCKUP_CNO = [('Anode', 'PXL', .0050, .0060), ('Gnode', 'SD1', .0020, .0020), ('Vref', 'SD2', None, .0015)]


def mockup(tmp_path, **kw):
    return make_world(tmp_path, mockup_pairs(), dl1={'reference': ELECTRODES_A, 'target': ELECTRODES_B},
                      cno=MOCKUP_CNO, **kw)


# ---- 1. mockup v8 change kinds -----------------------------------------------------------------------------------------

def test_mockup_change_kinds_and_status(tmp_path):
    world = mockup(tmp_path)
    assert go(world) == 0
    doc = result(world)
    assert doc['schema_version'] == 'ledger_v1' and doc['status'] == 'ok' and doc['reasons'] == []
    kinds = {row['pair_id']: row['change_kind'] for row in doc['pairs']}
    assert kinds == {'P1': 'same_pair_change', 'P2': 'layer_change', 'P3': 'no_change', 'P4': 'layer_change',
                     'P5': 'new_pair', 'P6': 'electrode_added_removed', 'P7': 'electrode_added_removed',
                     'P8': 'electrode_added_removed'}
    counts = doc['kinds']['change_kind_counts']
    assert counts['same_pair_change'] == 1 and counts['layer_change'] == 2 and counts['new_pair'] == 1
    assert counts['electrode_added_removed'] == 3 and counts['no_change'] == 1 and counts['cno'] == 3
    assert counts['gone_pair'] == 0 and counts['value_pending'] == 0
    assert sum(counts.values()) == 11
    assert {row['change_kind'] for row in doc['cno']} == {'cno'} and len(doc['cno']) == 3
    status = {row['electrode']: row['status'] for row in doc['electrodes']}
    assert status['Scan3'] == 'new' and status['Vref'] == 'new' and status['Vinit2'] == 'removed'
    assert status['Data'] == 'common' and status['Gnode'] == 'common'
    assert doc['warnings'] == []
    assert doc['checks']['status'] == 'passed'
    assert all(e['status'] == 'passed' for e in doc['checks']['equations'].values())
    inventory = doc['inputs']['electrode_inventory']
    assert inventory['reference']['source'] == 'dl1' and inventory['reference']['status'] == 'complete'
    assert inventory['reference']['electrodes'] == sorted(ELECTRODES_A)
    assert inventory['reference']['sha256'] and doc['inputs']['ledger_basis'] == 'pair_share_v1'
    assert (world.out / 'ledger_pairs.csv').exists() and (world.out / 'ledger_electrodes.csv').exists()
    exe = json.loads((world.out / 'execution.json').read_text(encoding='utf-8'))
    assert exe['command'] == 'ledger' and exe['exit_code'] == 0


def test_mockup_values_ranks_and_electrodes(tmp_path):
    world = mockup(tmp_path)
    assert go(world) == 0
    doc = result(world)
    s_line, s_pixel = doc['kinds']['line']['slope'], doc['kinds']['pixel']['slope']
    pairs = by_pair(doc)
    assert pairs['P1']['a'] == pytest.approx(s_line * .0120) and pairs['P1']['b'] == pytest.approx(s_line * .0150)
    assert pairs['P1']['delta'] == pytest.approx(s_line * .0030)
    assert pairs['P5']['a'] == 0.0 and pairs['P5']['present_a'] is False and pairs['P5']['present_b'] is True
    assert pairs['P3']['delta'] == 0.0
    # ranks: B value descending per kind, gone pairs by A
    assert [r['pair_id'] for r in doc['pairs'] if r['kind'] == 'line'] == ['P1', 'P2', 'P5', 'P8', 'P6', 'P7']
    assert pairs['P1']['rank'] == 'L1' and pairs['P2']['rank'] == 'L2' and pairs['P8']['rank'] == 'L4'
    assert pairs['P3']['rank'] == 'P1' and pairs['P4']['rank'] == 'P2'
    assert pairs['P1']['pct_a'] == pytest.approx(100 * pairs['P1']['a'] / doc['totals']['line']['vi_a'])
    # electrode ledger: a pair value splits 1/2, 1/2; CNO goes whole to its electrode
    elec = {row['electrode']: row for row in doc['electrodes']}
    assert elec['Data']['line']['b'] == pytest.approx(s_line * (.0150 + .0040) / 2)
    assert elec['Anode']['cno']['a'] == pytest.approx(s_pixel * .0050)
    assert elec['Anode']['pixel']['a'] == pytest.approx(s_pixel * (.0200 / 2 + .0090 / 2 + .0050))
    assert elec['Gnode']['layers_a'] == ['SD1'] and elec['Gnode']['layers_b'] == ['SD2']
    assert elec['Scan2']['partners_a'] == ['Vinit2'] and elec['Scan2']['partners_b'] == ['Vint']
    assert elec['Vref']['present_a'] is False and elec['Vref']['cno']['b'] == pytest.approx(s_pixel * .0015)
    cno = {(r['electrode_id'], r['density_layer']): r for r in doc['cno']}
    assert cno[('Vref', 'SD2')]['presence'] == 'new' and cno[('Vref', 'SD2')]['a'] == 0.0
    assert doc['kinds']['line']['pair_count'] == 6 and doc['kinds']['pixel']['pair_count'] == 2


def test_mockup_layers_sizes_and_top_pairs(tmp_path):
    world = mockup(tmp_path)
    assert go(world) == 0
    doc = result(world)
    layers = {row['layer']: row for row in doc['layers']['line']}
    sd1 = layers['SD1']
    assert [e['diameter_um'] for e in sd1['by_size']] == [3.0]
    assert sd1['by_size'][0]['delta'] == pytest.approx(sd1['delta'])
    assert [t['pair_id'] for t in sd1['top_pairs']] == ['P5', 'P1']        # |delta| .0065 (new pair), then .0030
    assert sd1['top_pairs'][0]['delta'] == pytest.approx(doc['kinds']['line']['slope'] * .0065)
    assert [row['layer'] for row in doc['layers']['cno']] == ['PXL', 'SD1', 'SD2']


# ---- 2. the six equations --------------------------------------------------------------------------------------------

def assert_all_passed(doc):
    assert doc['checks']['status'] == 'passed'
    assert set(doc['checks']['equations']) == {'eq1_line_a', 'eq2_line_b', 'eq3_line_delta', 'eq4_pixel_a',
                                               'eq5_pixel_b', 'eq6_pixel_delta'}
    assert all(e['status'] == 'passed' for e in doc['checks']['equations'].values())
    for kind in ('line', 'pixel'):
        assert all(e['status'] == 'passed' for e in doc['checks']['electrode_sum'][kind].values())
        assert doc['checks']['pair_delta_vs_compare2'][kind]['status'] == 'passed'


def test_equations_with_crp_difference(tmp_path):
    world = mockup(tmp_path, crp=(1.0, 2.92))
    assert go(world) == 0
    doc = result(world)
    assert_all_passed(doc)
    crp = doc['totals']['pixel']['crp']
    d = world.doc['delta']['crp_difference']['delta_vi_pixel']
    assert crp['present'] is True and crp['delta'] == d and crp['a'] == -d and crp['b'] == 0.0
    assert doc['totals']['pixel']['a'] == pytest.approx(doc['totals']['pixel']['vi_a'])
    assert doc['totals']['pixel']['b'] == pytest.approx(doc['totals']['pixel']['vi_b'])


def test_equations_with_equal_crp(tmp_path):
    world = mockup(tmp_path)
    assert go(world) == 0
    doc = result(world)
    assert_all_passed(doc)
    assert doc['totals']['pixel']['crp']['present'] is False and doc['totals']['pixel']['crp']['delta'] == 0.0
    assert doc['totals']['line']['intercept']['delta'] == 0.0
    assert doc['totals']['line']['intercept']['a'] == doc['totals']['line']['intercept']['b']


def test_equations_with_limit_slope_when_lambda_equal(tmp_path):
    pairs = [pair('P1', ['A', 'B'], 'line', [lateral(['A', 'B'], .01, 'SD1')], [lateral(['A', 'B'], .01, 'SD1')]),
             pair('P2', ['B', 'C'], 'pixel', [lateral(['B', 'C'], .02, 'PXL')], [lateral(['B', 'C'], .02, 'PXL')])]
    world = make_world(tmp_path, pairs, cno=[('A', 'PXL', .005, .005)])
    assert go(world) == 0
    doc = result(world)
    assert doc['kinds']['line']['slope_method'] == 'limit' and doc['kinds']['pixel']['slope_method'] == 'limit'
    assert_all_passed(doc)
    assert all(row['change_kind'] == 'no_change' for row in doc['pairs'])


def test_equations_with_shared_lambda(tmp_path):
    pairs = [pair('P1', ['A', 'B'], 'line', [lateral(['A', 'B'], .01, 'SD1')], [lateral(['A', 'B'], .016, 'SD1')],
                  shared_frac=.25),
             pair('P2', ['B', 'C'], 'pixel', [lateral(['B', 'C'], .02, 'PXL')], [lateral(['B', 'C'], .03, 'PXL')],
                  shared_frac=.5)]
    world = make_world(tmp_path, pairs, cno=[('A', 'PXL', .005, .007)])
    assert go(world) == 0
    doc = result(world)
    assert_all_passed(doc)
    # the pair delta is not the compare2 condition delta (the shared part is added): ledger = conditions + shared
    cond = world.doc['delta']['conditions'][0]['delta_vi_line']
    assert by_pair(doc)['P1']['delta'] > cond


def test_failed_check_is_partial_exit_3(tmp_path):
    def tamper(doc):
        doc['delta']['indicators']['delta_vi_line'] += 0.01
    world = mockup(tmp_path, mutate=tamper)
    assert go(world) == 3
    doc = result(world)
    assert doc['status'] == 'partial' and doc['reasons'] == ['ledger_totals_check_failed']
    assert doc['checks']['equations']['eq3_line_delta']['status'] == 'failed'
    exe = json.loads((world.out / 'execution.json').read_text(encoding='utf-8'))
    assert exe['exit_code'] == 3 and exe['status'] == 'partial'


# ---- 3. null propagation (X2) ----------------------------------------------------------------------------------------

def test_pixel_unavailable_leaves_line_normal(tmp_path):
    world = mockup(tmp_path, pixel_null=True)
    assert go(world) == 0
    doc = result(world)
    pairs = by_pair(doc)
    assert doc['kinds']['pixel']['computable'] is False and doc['kinds']['line']['computable'] is True
    for pid in ('P3', 'P4'):
        assert pairs[pid]['a'] is None and pairs[pid]['b'] is None and pairs[pid]['delta'] is None
        assert pairs[pid]['pct_delta'] is None
    assert pairs['P3']['change_kind'] == 'value_pending'      # same keys, value needed
    assert pairs['P4']['change_kind'] == 'layer_change'       # observed fact, no value needed
    assert pairs['P1']['delta'] is not None and pairs['P1']['change_kind'] == 'same_pair_change'
    assert all(r['a'] is None and r['delta'] is None for r in doc['cno'])
    assert doc['checks']['equations']['eq4_pixel_a']['status'] == 'not_evaluated'
    assert doc['checks']['equations']['eq6_pixel_delta']['status'] == 'not_evaluated'
    assert doc['checks']['equations']['eq1_line_a']['status'] == 'passed'
    assert doc['checks']['status'] == 'not_evaluated' and doc['status'] == 'ok'
    assert doc['totals']['pixel']['a'] is None and doc['kinds']['pixel']['increase_sum'] is None
    assert doc['kinds']['change_kind_counts']['value_pending'] == 1
    assert {'scope': 'kind', 'id': 'pixel', 'reasons': ['kind_not_computable']} in doc['unavailable']
    elec = {r['electrode']: r for r in doc['electrodes']}
    assert elec['Anode']['pixel']['a'] is None and elec['Data']['line']['a'] is not None


def test_line_unavailable_leaves_pixel_normal(tmp_path):
    world = mockup(tmp_path, line_null=True)
    assert go(world) == 0
    doc = result(world)
    pairs = by_pair(doc)
    assert doc['kinds']['line']['computable'] is False
    assert pairs['P1']['delta'] is None and pairs['P1']['change_kind'] == 'value_pending'
    assert pairs['P5']['a'] is None and pairs['P5']['change_kind'] == 'new_pair'
    assert pairs['P3']['delta'] == 0.0 and pairs['P3']['change_kind'] == 'no_change'
    assert doc['checks']['equations']['eq4_pixel_a']['status'] == 'passed'
    assert doc['checks']['equations']['eq1_line_a']['status'] == 'not_evaluated'


def test_one_noncomputable_pair_waits_for_value_only(tmp_path):
    pairs = mockup_pairs()
    pairs[0] = pair('P1', ['Data', 'ELVDD'], 'line', [lateral(['Data', 'ELVDD'], .0120, 'SD1')], [], noncomp_b=True)
    world = make_world(tmp_path, pairs, dl1={'reference': ELECTRODES_A, 'target': ELECTRODES_B}, cno=MOCKUP_CNO)
    assert go(world) == 0
    doc = result(world)
    row = by_pair(doc)['P1']
    assert row['present_a'] is True and row['present_b'] is True
    assert row['a'] is not None and row['b'] is None and row['delta'] is None
    assert row['computable_a'] is True and row['computable_b'] is False
    assert row['change_kind'] == 'value_pending'
    assert by_pair(doc)['P3']['change_kind'] == 'no_change' and by_pair(doc)['P2']['delta'] is not None
    assert {'scope': 'pair', 'id': 'P1', 'side': 'target', 'reasons': ['condition_not_computable']} in doc['unavailable']
    assert doc['checks']['equations']['eq1_line_a']['status'] == 'passed'
    assert doc['checks']['equations']['eq3_line_delta']['status'] == 'passed'
    assert doc['checks']['pair_delta_vs_compare2']['line']['status'] == 'not_evaluated'   # a null condition delta
    assert doc['kinds']['change_kind_counts']['value_pending'] == 1


def test_real_zero_is_no_change_not_pending(tmp_path):
    pairs = [pair('P1', ['A', 'B'], 'line', [lateral(['A', 'B'], 0.0, 'SD1')], [lateral(['A', 'B'], 0.0, 'SD1')]),
             pair('P2', ['B', 'C'], 'line', [lateral(['B', 'C'], .01, 'SD1')], [lateral(['B', 'C'], .02, 'SD1')])]
    world = make_world(tmp_path, pairs)
    assert go(world) == 0
    row = by_pair(result(world))['P1']
    assert row['a'] == 0.0 and row['b'] == 0.0 and row['delta'] == 0.0 and row['change_kind'] == 'no_change'
    assert row['computable_a'] and row['computable_b']


def test_present_pair_without_items_is_zero_not_absent(tmp_path):
    pairs = [pair('P1', ['A', 'B'], 'line', [], [lateral(['A', 'B'], .02, 'SD1')]),
             pair('P2', ['B', 'C'], 'line', [lateral(['B', 'C'], .01, 'SD1')], [lateral(['B', 'C'], .01, 'SD1')])]
    world = make_world(tmp_path, pairs)
    assert go(world) == 0
    row = by_pair(result(world))['P1']
    assert row['present_a'] is True and row['a'] == 0.0 and row['change_kind'] == 'layer_change'


def test_vi_a_zero_gives_null_percent(tmp_path):
    pairs = [pair('P1', ['A', 'B'], 'line', [lateral(['A', 'B'], 0.0, 'SD1')], [lateral(['A', 'B'], .01, 'SD1')])]
    world = make_world(tmp_path, pairs)
    assert go(world) == 0
    doc = result(world)
    row = by_pair(doc)['P1']
    assert doc['totals']['line']['vi_a'] == 0.0
    assert row['a'] == 0.0 and row['b'] > 0
    assert row['pct_a'] is None and row['pct_b'] is None and row['pct_delta'] is None


# ---- 4. electrodes ----------------------------------------------------------------------------------------------------

def test_same_name_electrode_pair_goes_whole_to_one_electrode(tmp_path):
    pairs = [pair('P1', ['X', 'X'], 'line', [vertical([('X', 'SD1'), ('X', 'GAT1')], .01, 'SD1', 'GAT1')],
                  [vertical([('X', 'SD1'), ('X', 'GAT1')], .02, 'SD1', 'GAT1')]),
             pair('P2', ['X', 'Y'], 'line', [lateral(['X', 'Y'], .01, 'SD1')], [lateral(['X', 'Y'], .01, 'SD1')])]
    world = make_world(tmp_path, pairs)
    assert go(world) == 0
    doc = result(world)
    s = doc['kinds']['line']['slope']
    elec = {r['electrode']: r for r in doc['electrodes']}
    assert elec['X']['line']['a'] == pytest.approx(s * (.01 + .01 / 2))
    assert elec['X']['line']['delta'] == pytest.approx(s * .01)
    assert elec['X']['partners_a'] == ['Y'] and elec['Y']['line']['a'] == pytest.approx(s * .01 / 2)
    assert set(elec['X']['layers']) == {'SD1', 'GAT1'}
    assert_all_passed(doc)


def test_no_inventory_uses_observed_and_does_not_judge_added_or_removed(tmp_path):
    # E3 has no defect in A and shows up first in B: observed lists cannot call that an added electrode
    pairs = [pair('P1', ['E1', 'E2'], 'line', [lateral(['E1', 'E2'], .01, 'SD1')], [lateral(['E1', 'E2'], .02, 'SD1')]),
             pair('P2', ['E1', 'E3'], 'line', None, [lateral(['E1', 'E3'], .01, 'SD1')])]
    world = make_world(tmp_path, pairs)
    assert go(world) == 0
    doc = result(world)
    assert by_pair(doc)['P2']['change_kind'] == 'new_pair'
    assert {w['code'] for w in doc['warnings']} == {'electrode_inventory_from_hotspots'}
    assert {w['role'] for w in doc['warnings']} == {'reference', 'target'}
    inventory = doc['inputs']['electrode_inventory']
    assert inventory['reference']['source'] == 'observed' and inventory['reference']['status'] == 'incomplete'
    assert inventory['target']['electrodes'] == ['E1', 'E2', 'E3']
    assert {r['electrode']: r['status'] for r in doc['electrodes']} == {
        'E1': 'unverifiable', 'E2': 'unverifiable', 'E3': 'unverifiable'}
    elec = {r['electrode']: r for r in doc['electrodes']}
    assert elec['E3']['present_a'] is False and elec['E3']['present_b'] is True


def test_complete_inventories_judge_real_addition(tmp_path):
    same = {'E1': ['SD1'], 'E2': ['SD1']}
    pairs = [pair('P1', ['E1', 'E2'], 'line', [lateral(['E1', 'E2'], .01, 'SD1')], [lateral(['E1', 'E2'], .02, 'SD1')]),
             pair('P2', ['E1', 'E3'], 'line', None, [lateral(['E1', 'E3'], .01, 'SD1')])]
    world = make_world(tmp_path, pairs, dl1={'reference': same, 'target': dict(same, E3=['SD1'])})
    assert go(world) == 0
    doc = result(world)
    assert by_pair(doc)['P2']['change_kind'] == 'electrode_added_removed'
    assert {r['electrode']: r['status'] for r in doc['electrodes']} == {'E1': 'common', 'E2': 'common', 'E3': 'new'}
    assert doc['warnings'] == []


def test_inventory_conflict_warns_and_counts_electrode_as_present(tmp_path):
    inv = {'E1': ['SD1'], 'E2': ['SD1']}
    pairs = [pair('P1', ['E1', 'Ghost'], 'line', [lateral(['E1', 'Ghost'], .01, 'SD1')],
                  [lateral(['E1', 'Ghost'], .02, 'SD1')])]
    world = make_world(tmp_path, pairs, dl1={'reference': inv, 'target': inv})
    assert go(world) == 0
    doc = result(world)
    conflicts = [w for w in doc['warnings'] if w['code'] == 'electrode_inventory_conflict']
    assert {(w['role'], tuple(w['electrodes'])) for w in conflicts} == {('reference', ('Ghost',)), ('target', ('Ghost',))}
    assert by_pair(doc)['P1']['change_kind'] == 'same_pair_change'
    assert {r['electrode']: r['status'] for r in doc['electrodes']}['Ghost'] == 'common'


def test_opposite_direction_items_split_over_electrode_layers(tmp_path):
    item1 = ('vertical_short', [('E1', 'SD1'), ('E2', 'SD2')])
    item2 = ('vertical_short', [('E1', 'SD2'), ('E2', 'SD1')])
    pairs = [pair('P1', ['E1', 'E2'], 'line',
                  [vertical(item1[1], .01, 'SD2', 'SD1'), vertical(item2[1], .03, 'SD2', 'SD1')],
                  [vertical(item1[1], .02, 'SD2', 'SD1'), vertical(item2[1], .03, 'SD2', 'SD1')])]
    world = make_world(tmp_path, pairs)
    assert go(world) == 0
    doc = result(world)
    s = doc['kinds']['line']['slope']
    row = by_pair(doc)['P1']
    assert len(row['items']) == 2 and len({i['item_id'] for i in row['items']}) == 2
    assert row['change_kind'] == 'same_pair_change'
    elec = {r['electrode']: r for r in doc['electrodes']}
    assert elec['E1']['layers']['SD1']['line']['a'] == pytest.approx(s * .01 / 2)
    assert elec['E1']['layers']['SD2']['line']['a'] == pytest.approx(s * .03 / 2)
    assert elec['E2']['layers']['SD2']['line']['a'] == pytest.approx(s * .01 / 2)
    assert elec['E2']['layers']['SD1']['line']['a'] == pytest.approx(s * .03 / 2)
    assert elec['E1']['layers']['SD1']['line']['delta'] == pytest.approx(s * .01 / 2)
    assert elec['E1']['layers']['SD2']['line']['delta'] == 0.0
    assert elec['E1']['line']['a'] == pytest.approx(s * .02)
    assert_all_passed(doc)


def test_size_split_sums_to_layer_value(tmp_path):
    pairs = [pair('P1', ['A', 'B'], 'line', [lateral(['A', 'B'], .01, 'SD1', sizes=[.004, .006])],
                  [lateral(['A', 'B'], .02, 'SD1', sizes=[.012, .008])])]
    world = make_world(tmp_path, pairs)
    assert go(world) == 0
    layer = result(world)['layers']['line'][0]
    assert [e['diameter_um'] for e in layer['by_size']] == [3.0, 4.0]
    assert sum(e['delta'] for e in layer['by_size']) == pytest.approx(layer['delta'])


# ---- 5. input errors ---------------------------------------------------------------------------------------------------

def test_pair_table_hash_mismatch_exits_2(tmp_path):
    def tamper(doc):
        doc['products']['target']['pair_table_hash'] = 'x' * 64
    world = mockup(tmp_path, mutate=tamper)
    assert go(world) == 2
    assert error_code(world.out) == 'ledger_pair_table_mismatch'
    assert not (world.out / 'ledger.json').exists() and (world.out / 'execution.json').exists()


def test_pair_class_mismatch_exits_2(tmp_path):
    def tamper(doc):
        for cond in doc['sides']['target']['conditions']:
            if cond['pair_id'] == 'P1':
                cond['class'] = 'pixel'
    world = mockup(tmp_path, mutate=tamper)
    assert go(world) == 2
    assert error_code(world.out) == 'ledger_pair_class_mismatch'


def test_missing_ledger_basis_exits_2(tmp_path):
    def tamper(doc):
        del doc['inputs']['ledger_basis']
    world = mockup(tmp_path, mutate=tamper)
    assert go(world) == 2
    assert error_code(world.out) == 'ledger_compare2_stale'


def test_config_sha_mismatch_exits_2(tmp_path):
    world = mockup(tmp_path)
    world.config.write_text(world.config.read_text(encoding='utf-8') + '\n# edited\n', encoding='utf-8')
    assert go(world) == 2
    assert error_code(world.out) == 'compare2_config_mismatch'


def test_compare2_execution_error_is_refused(tmp_path):
    world = mockup(tmp_path)
    (world.c2 / 'execution.json').write_text(json.dumps({'command': 'compare2', 'status': 'error', 'exit_code': 2}),
                                             encoding='utf-8')
    assert go(world) == 2
    assert error_code(world.out) == 'compare2_execution_error'


def test_missing_compare2_json_exits_2(tmp_path):
    world = mockup(tmp_path)
    (world.c2 / 'compare2.json').unlink()
    assert go(world) == 2
    assert error_code(world.out) == 'compare2_json_missing'


# ---- 6. output folder -------------------------------------------------------------------------------------------------

def test_rerun_into_own_folder_is_allowed(tmp_path):
    world = mockup(tmp_path)
    assert go(world) == 0
    assert go(world) == 0
    assert sorted(p.name for p in world.out.iterdir()) == ['execution.json', 'ledger.json', 'ledger_electrodes.csv',
                                                          'ledger_pairs.csv']


def test_folder_with_foreign_file_is_refused_and_untouched(tmp_path):
    world = mockup(tmp_path)
    world.out.mkdir()
    (world.out / 'notes.txt').write_text('mine', encoding='utf-8')
    assert go(world) == 2
    assert sorted(p.name for p in world.out.iterdir()) == ['notes.txt']
    assert (world.out / 'notes.txt').read_text(encoding='utf-8') == 'mine'


def test_foreign_execution_json_is_not_owned(tmp_path):
    world = mockup(tmp_path)
    world.out.mkdir()
    (world.out / 'execution.json').write_text(json.dumps({'command': 'compare2'}), encoding='utf-8')
    assert go(world) == 2
    assert json.loads((world.out / 'execution.json').read_text(encoding='utf-8')) == {'command': 'compare2'}
    assert not (world.out / 'error.json').exists()


def test_error_after_good_run_clears_results(tmp_path):
    world = mockup(tmp_path)
    assert go(world) == 0
    world.config.write_text(world.config.read_text(encoding='utf-8') + '\n# edited\n', encoding='utf-8')
    assert go(world) == 2
    assert sorted(p.name for p in world.out.iterdir()) == ['error.json', 'execution.json']
    assert json.loads((world.out / 'execution.json').read_text(encoding='utf-8'))['status'] == 'error'
    # a later good run replaces both again
    world.config.write_text(world.config.read_text(encoding='utf-8').replace('\n# edited\n', ''), encoding='utf-8')
    assert go(world) == 0
    assert sorted(p.name for p in world.out.iterdir()) == ['execution.json', 'ledger.json', 'ledger_electrodes.csv',
                                                          'ledger_pairs.csv']


# ---- 7. determinism, CLI -----------------------------------------------------------------------------------------------

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_same_input_twice_is_byte_identical(tmp_path):
    world = mockup(tmp_path)
    second = tmp_path / 'ledger_out2'
    assert go(world) == 0 and go(world, second) == 0
    for name in ('ledger.json', 'ledger_pairs.csv', 'ledger_electrodes.csv'):
        assert digest(world.out / name) == digest(second / name)


def test_ledger_csv_shapes(tmp_path):
    world = mockup(tmp_path)
    assert go(world) == 0
    pairs = (world.out / 'ledger_pairs.csv').read_text(encoding='utf-8-sig').splitlines()
    assert pairs[0].startswith('rank,pair_id,electrode_1,electrode_2,kind,change_kind')
    assert len(pairs) == 9
    rows = {line.split(',')[1]: line.split(',') for line in pairs[1:]}
    assert rows['P5'][5] == 'new_pair' and rows['P5'][6] == 'false' and rows['P5'][7] == 'true'
    electrodes = (world.out / 'ledger_electrodes.csv').read_text(encoding='utf-8-sig').splitlines()
    assert electrodes[0].startswith('electrode,status,present_a,present_b,layers_a,layers_b')
    assert len(electrodes) == 1 + 11


def test_cli_lists_ledger_and_runs(tmp_path, capsys):
    with pytest.raises(SystemExit):
        main(['--help'])
    assert 'ledger' in capsys.readouterr().out
    world = mockup(tmp_path)
    code = main(['ledger', '--compare2-dir', str(world.c2), '--config', str(world.config), '--out', str(world.out)])
    assert code == 0 and (world.out / 'ledger.json').exists()


def test_input_document_is_not_changed(tmp_path):
    world = mockup(tmp_path)
    before = copy.deepcopy(json.loads((world.c2 / 'compare2.json').read_text(encoding='utf-8')))
    assert go(world) == 0
    assert json.loads((world.c2 / 'compare2.json').read_text(encoding='utf-8')) == before
