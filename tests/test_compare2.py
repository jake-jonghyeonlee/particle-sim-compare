"""compare2 compute + CLI on small real_hotspot_v1 trees (4x4 cell frame, 0.01 um^2 per cell)."""
import csv
import json
import math
from pathlib import Path

import numpy as np
import pytest
import yaml
from PIL import Image

from simcompare.adapters.real_hotspot import import_hotspots
from simcompare.cli import main
from simcompare.numeric_schema import DIAMETERS_UM

CELL = 0.01
SIZES = (1, 2, 3)
STACK = {'L1': (1, 'P1'), 'L2': (2, 'P2')}
RES = [20, 10]


def close(x, y):
    return abs(x - y) <= 1e-12 * max(1.0, abs(y))


# ---- fixture builders -------------------------------------------------------------------------------------------

def write_side(base, name, cases, pairs, cno=None):
    """cases: (defect, ea, la, eb, lb, {diameter: cells}); pairs: (pair_id, ea, eb, class). Returns (root, imported)."""
    root = base / name / 'src'
    names = []
    for defect, ea, la, eb, lb, masks in cases:
        case = f'{defect}-{ea}-{la}-{eb}-{lb}'
        names.append(case)
        folder = root / case
        (folder / 'result').mkdir(parents=True)
        for electrode in ('1.png', '2.png'):
            Image.new('L', (1004, 1004), 0).save(folder / electrode)
        rows = ['defect_size(um),Area(um2)']
        for diameter in DIAMETERS_UM:
            cells = masks.get(diameter, ())
            array = np.zeros((4, 4), dtype=np.uint8)
            for cell in cells:
                array[cell // 4, cell % 4] = 255
            Image.fromarray(array).save(folder / f'hotspot_{defect}{diameter:02d}.png')
            rows.append(f'{diameter},{len(cells) * CELL:.2f}')
        (folder / 'result' / f'{defect}.csv').write_text('\n'.join(rows) + '\n', encoding='utf-8')
    for cno_name, text in (cno or {}).items():
        folder = root / cno_name / 'result'
        folder.mkdir(parents=True)
        (folder / 'lateral_open.csv').write_text(text, encoding='utf-8')
    expected = base / name / 'expected.txt'
    expected.write_text('\n'.join(names) + '\n', encoding='utf-8')
    imported = base / name / 'imp'
    import_hotspots(root, imported, expected_cases=expected)
    (imported / 'pairs.csv').write_text(
        'pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\n'
        + ''.join(f'{pid},{a},{b},{cls},v1\n' for pid, a, b, cls in pairs), encoding='utf-8')
    return root, imported


def assignment():
    return {'product_id': 'P', 'design_version': 'D', 'source_id': 'S', 'process_step_id': 'STEP',
            'exposure_id': 'E', 'unit_id': 'U', 'pair_table_path': 'pairs.csv',
            'layer_stack': [{'actual_layer': layer, 'stack_order': order, 'density_process_layer': dlayer}
                            for layer, (order, dlayer) in STACK.items()]}


def write_density(folder, group, line, pixel):
    """line/pixel: {density_layer: {diameter: value}}; every other size is 0.0."""
    folder.mkdir(parents=True, exist_ok=True)
    for kind, table in (('line', line), ('pixel', pixel)):
        header = ['layer'] + [f'{d}um' for d in DIAMETERS_UM]
        lines = [','.join(header)]
        for layer, values in table.items():
            lines.append(','.join([layer] + [repr(values.get(d, 0.0)) for d in DIAMETERS_UM]))
        (folder / f'{group}_{kind}.csv').write_text('\n'.join(lines) + '\n', encoding='utf-8')


DENS_LINE = {'P1': {1: 5.0, 2: 2.5, 3: 1.25}, 'P2': {1: 4.0, 2: 2.0, 3: 1.0}}
DENS_PIXEL = {'P1': {1: 3.0, 2: 1.5, 3: 0.75}, 'P2': {1: 6.0, 2: 3.0, 3: 1.5}, 'gcnt': {1: 2.0, 2: 1.0, 3: 0.5}}


def run(tmp, ref, tgt, *, ref_group='mobile', tgt_group='mobile', glass=50.0, line=None, pixel=None,
        ref_cno=None, tgt_cno=None, scale=1.0, out_name='out'):
    """Build both sides, write the configs, run `simcompare compare2`. Returns (exit code, output folder)."""
    ref_root, ref_imp = write_side(tmp, 'ref', *ref, cno=ref_cno)
    tgt_root, tgt_imp = write_side(tmp, 'tgt', *tgt, cno=tgt_cno)
    numeric = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'unused',
               'reference': {'data_dir': str(ref_imp), 'condition_assignment': assignment()},
               'target': {'data_dir': str(tgt_imp), 'condition_assignment': assignment()}}
    (tmp / 'numeric.yaml').write_text(yaml.safe_dump(numeric), encoding='utf-8')
    scaled = lambda table: {layer: {d: v * scale for d, v in values.items()} for layer, values in table.items()}
    for group in {ref_group, tgt_group}:
        write_density(tmp / 'density', group, scaled(DENS_LINE if line is None else line),
                      scaled(DENS_PIXEL if pixel is None else pixel))
    glass_cfg = {'value': glass, 'unit': 'um2'}
    cfg = {'schema_version': 'compare2_config_v1', 'numeric_config': 'numeric.yaml', 'output_dir': out_name,
           'density_dir': 'density',
           'model_groups': {group: {'glass_area': glass_cfg, 'weight_line': 1.0, 'weight_pixel': 1.0}
                            for group in {ref_group, tgt_group}},
           'products': {'reference': {'model_group': ref_group, 'resolution': RES, 'hotspot_root': str(ref_root)},
                        'target': {'model_group': tgt_group, 'resolution': RES, 'hotspot_root': str(tgt_root)}}}
    (tmp / 'compare2.yaml').write_text(yaml.safe_dump(cfg), encoding='utf-8')
    code = main(['compare2', '--config', str(tmp / 'compare2.yaml')])
    return code, tmp / out_name


def load(out):
    return json.loads((out / 'compare2.json').read_text(encoding='utf-8'))


# pairs (class) and cases of the base scenario: L1 line AB/AC, pixel DE/EF; L2 line GH, pixel IJ
PAIRS = [('AB', 'A', 'B', 'line'), ('AC', 'A', 'C', 'line'), ('DE', 'D', 'E', 'pixel'), ('EF', 'E', 'F', 'pixel'),
         ('GH', 'G', 'H', 'line'), ('IJ', 'I', 'J', 'pixel')]


def base_cases(**override):
    masks = {
        'AB': {1: {0, 1, 2}, 2: {0, 1}, 3: {0}}, 'AC': {1: {2, 3}, 2: {2}},
        'DE': {1: {1, 3, 4}, 2: {4, 5}, 3: {4}}, 'EF': {1: {4, 5}, 2: {5, 6}},
        'GH': {1: {0, 1}, 2: {0}}, 'IJ': {1: {1, 2}, 2: {8}, 3: {8, 9}}}
    masks.update(override)
    where = {'AB': ('A', 'B', 'L1'), 'AC': ('A', 'C', 'L1'), 'DE': ('D', 'E', 'L1'), 'EF': ('E', 'F', 'L1'),
             'GH': ('G', 'H', 'L2'), 'IJ': ('I', 'J', 'L2')}
    return [('lateral_short', ea, layer, eb, layer, masks[pid]) for pid, (ea, eb, layer) in where.items()
            if masks[pid] is not None]


# ---- independent oracle (set arithmetic) ------------------------------------------------------------------------

def oracle(cases, pairs, line_density, pixel_density, factor_line, factor_pixel):
    cls = {pid: c for pid, _a, _b, c in pairs}
    pid_of = {frozenset((a, b)): pid for pid, a, b, _c in pairs}
    sets = {}
    for defect, ea, la, eb, lb, masks in cases:
        layer = la if defect == 'lateral_short' else max((la, lb), key=lambda l: STACK[l][0])
        for d in SIZES:
            sets.setdefault((layer, d), {}).setdefault(pid_of[frozenset((ea, eb))], set()).update(masks.get(d, ()))
    result = {'lam_line': 0.0, 'lam_pixel': 0.0, 'cond': {}, 'shared_l': 0.0, 'shared_p': 0.0, 'groups': {}}
    for (layer, d), by_pair in sets.items():
        line = {p: c for p, c in by_pair.items() if cls[p] == 'line'}
        pix = {p: c for p, c in by_pair.items() if cls[p] == 'pixel'}
        l_all = set().union(*line.values()) if line else set()
        p_all = set().union(*pix.values()) if pix else set()
        peff = p_all - l_all
        owner_l = {c: {p for p, cs in line.items() if c in cs} for c in l_all}
        owner_p = {c: {p for p, cs in pix.items() if c in cs} for c in peff}
        dl, dp = line_density[STACK[layer][1]][d], pixel_density[STACK[layer][1]][d]
        lam_l = lambda n: dl * n * CELL * factor_line
        lam_p = lambda n: dp * n * CELL * factor_pixel
        result['lam_line'] += lam_l(len(l_all))
        result['lam_pixel'] += lam_p(len(peff))
        result['groups'][(layer, d)] = {'line': len(l_all), 'peff': len(peff), 'lam_l': lam_l(len(l_all)),
                                         'lam_p': lam_p(len(peff))}
        result['shared_l'] += lam_l(sum(1 for o in owner_l.values() if len(o) > 1))
        result['shared_p'] += lam_p(sum(1 for o in owner_p.values() if len(o) > 1))
        for pid in by_pair:
            row = result['cond'].setdefault((pid, layer), {'excl': 0.0, 'transfer': 0.0, 'class': cls[pid]})
            if cls[pid] == 'line':
                row['excl'] += lam_l(sum(1 for o in owner_l.values() if o == {pid}))
                row['transfer'] += lam_p(sum(1 for c, o in owner_l.items() if o == {pid} and c in p_all))
            else:
                row['excl'] += lam_p(sum(1 for o in owner_p.values() if o == {pid}))
    return result


FACTOR = RES[0] * RES[1] / (4 * 50.0)  # weight 1, glass 50 um^2 -> 1.0


def vi(lam_line, lam_pixel, crp=1.0):
    return (1 - math.exp(-lam_line)) / 1.2159, (1 - math.exp(-lam_pixel)) / crp / 1.5462


def cond_by_id(entries):
    return {entry['condition_id']: entry for entry in entries}


# ---- tests ------------------------------------------------------------------------------------------------------

def test_hand_calculation_single_pair(tmp_path):
    ref = ([('lateral_short', 'A', 'L1', 'B', 'L1', {1: {0, 1, 2}})], [('AB', 'A', 'B', 'line')])
    tgt = ([('lateral_short', 'A', 'L1', 'B', 'L1', {1: {0, 1, 2, 3, 4}})], [('AB', 'A', 'B', 'line')])
    code, out = run(tmp_path, ref, tgt)
    assert code == 0, (out / 'compare2.json').read_text()
    doc = load(out)
    assert FACTOR == 1.0
    lam_a, lam_b = 5.0 * 3 * 0.01, 5.0 * 5 * 0.01  # D(P1, 1 um) * cells * 0.01 um^2 * F
    side_a, side_b = doc['sides']['reference'], doc['sides']['target']
    assert close(side_a['lambda']['line'], lam_a) and close(side_b['lambda']['line'], lam_b)
    assert side_a['lambda']['pixel'] == 0.0 and side_a['lambda']['cno'] == 0.0
    assert close(side_a['caa_line'], 1 - math.exp(-lam_a))
    assert close(side_a['vi_line'], (1 - math.exp(-lam_a)) / 1.2159)
    assert side_a['vi_pixel'] == 0.0
    d_vi = (1 - math.exp(-lam_b)) / 1.2159 - (1 - math.exp(-lam_a)) / 1.2159
    delta = doc['delta']
    assert close(delta['indicators']['delta_vi_line'], d_vi)
    assert delta['indicators']['delta_vi_line_rounded'] == round(d_vi, 6)
    (row,) = delta['conditions']
    assert row['presence'] == 'both' and row['class'] == 'line'
    assert close(row['delta_vi_line'], d_vi) and close(row['share_line'], 1.0)
    assert delta['slopes']['line']['method'] == 'secant'
    assert doc['schema_version'] == 'compare2_v1'
    assert doc['inputs']['area_basis'] == 'crop_observed_area_div4_user_approved_2026_11_05'
    for name in ('compare2_summary.csv', 'compare2_layers.csv', 'compare2_conditions.csv', 'compare2_cno.csv',
                 'execution.json'):
        assert (out / name).is_file()
    execution = json.loads((out / 'execution.json').read_text())
    assert execution['exit_code'] == 0 and execution['command'] == 'compare2'
    with (out / 'compare2_summary.csv').open(encoding='utf-8-sig') as handle:
        rows = list(csv.DictReader(handle))
    assert [r['scope'] for r in rows] == ['reference', 'target', 'delta']


def test_lambda_caa_vi_match_oracle_and_conservation(tmp_path):
    ref = (base_cases(), PAIRS)
    tgt = (base_cases(AB={1: {0, 1}, 2: {0, 1, 2}, 3: {0}}, DE={1: {1, 4}, 2: {4, 5, 6}, 3: {4}}), PAIRS)
    code, out = run(tmp_path, ref, tgt)
    assert code == 0, (out / 'compare2.json').read_text()
    doc = load(out)
    for role, (cases, _pairs) in (('reference', ref), ('target', tgt)):
        expect = oracle(cases, PAIRS, DENS_LINE, DENS_PIXEL, FACTOR, FACTOR)
        side = doc['sides'][role]
        assert close(side['lambda']['line'], expect['lam_line']) and close(side['lambda']['pixel'], expect['lam_pixel'])
        vi_line, vi_pixel = vi(expect['lam_line'], expect['lam_pixel'])
        assert close(side['vi_line'], vi_line) and close(side['vi_pixel'], vi_pixel)
        assert close(side['caa_line'], 1 - math.exp(-expect['lam_line']))
        assert side['conservation_check']['status'] == 'passed'
        conds = cond_by_id(side['conditions'])
        for (pid, layer), row in expect['cond'].items():
            got = conds[f'{pid}@{layer}']
            assert got['class'] == row['class']
            assert close(got['lambda_excl'], row['excl']) and close(got['lambda_transfer'], row['transfer'])
        assert close(side['shared']['lambda_line'], expect['shared_l'])
        assert close(side['shared']['lambda_pixel'], expect['shared_p'])
        layers = {row['layer']: row for row in side['layers']}
        assert close(sum(r['share_line'] for r in layers.values()), 1.0)
        assert close(sum(r['share_pixel'] for r in layers.values()), 1.0)
        l1 = layers['L1']
        assert close(l1['lambda_line'], sum(g['lam_l'] for (layer, _d), g in expect['groups'].items() if layer == 'L1'))
    delta = doc['delta']
    ind = delta['indicators']
    assert close(ind['delta_vi_line'], doc['sides']['target']['vi_line'] - doc['sides']['reference']['vi_line'])
    assert delta['check']['status'] == 'passed'
    parts = sum(c['delta_vi_line'] for c in delta['conditions']) + delta['shared']['delta_vi_line']
    assert close(parts, ind['delta_vi_line'])
    parts = sum(c['delta_vi_pixel'] for c in delta['conditions']) + delta['shared']['delta_vi_pixel']
    assert close(parts, ind['delta_vi_pixel'])
    layer_sum = sum(r['delta_vi_line'] for r in delta['layers'])
    assert close(layer_sum, ind['delta_vi_line'])
    for row in delta['layers']:
        assert close(sum(s['delta_vi_line'] for s in row['by_size']), row['delta_vi_line'])
    removal = cond_by_id(delta['conditions'])['AB@L1']['removal_b']
    tgt_expect = oracle(tgt[0], PAIRS, DENS_LINE, DENS_PIXEL, FACTOR, FACTOR)
    cond = tgt_expect['cond'][('AB', 'L1')]
    vl_b, vp_b = vi(tgt_expect['lam_line'], tgt_expect['lam_pixel'])
    vl_x, vp_x = vi(tgt_expect['lam_line'] - cond['excl'], tgt_expect['lam_pixel'] + cond['transfer'])
    assert close(removal['delta_vi_line'], vl_x - vl_b) and close(removal['delta_vi_pixel'], vp_x - vp_b)


def test_line_covering_pixel_gives_transfer(tmp_path):
    covering = base_cases(AB={1: {0, 1, 2, 3, 4, 5}, 2: {0, 1}, 3: {0}}, AC={1: set()},
                          DE={1: {1, 3, 4}, 2: {4, 5}, 3: {4}}, EF={1: {4, 5}})
    code, out = run(tmp_path, (base_cases(), PAIRS), (covering, PAIRS))
    assert code == 0
    doc = load(out)
    group_b = [r for r in doc['sides']['target']['layers'] if r['layer'] == 'L1'][0]['by_size']
    one = [s for s in group_b if s['diameter_um'] == 1.0][0]
    assert one['pixel_eff_area_um2'] == 0.0 and one['transfer_area_um2'] > 0
    rows = cond_by_id(doc['delta']['conditions'])
    ab = rows['AB@L1']
    assert ab['removal_b']['delta_vi_pixel'] > 0
    assert ab['delta_vi_pixel'] == 0.0 and ab['delta_lambda_transfer'] is not None
    expect = oracle(covering, PAIRS, DENS_LINE, DENS_PIXEL, FACTOR, FACTOR)
    assert close(cond_by_id(doc['sides']['target']['conditions'])['AB@L1']['lambda_transfer'],
                 expect['cond'][('AB', 'L1')]['transfer'])


def test_presence_new_and_gone(tmp_path):
    new_pairs = PAIRS + [('KL', 'K', 'L', 'line')]
    tgt_cases = base_cases(AC=None) + [('lateral_short', 'K', 'L1', 'L', 'L1', {1: {8, 9}, 2: {10}})]
    code, out = run(tmp_path, (base_cases(), PAIRS), (tgt_cases, new_pairs))
    doc = load(out)
    assert code == 0, doc['sides']['target']['reasons']
    rows = cond_by_id(doc['delta']['conditions'])
    assert rows['AC@L1']['presence'] == 'gone' and rows['KL@L1']['presence'] == 'new' and rows['AB@L1']['presence'] == 'both'
    assert 'AC@L1' not in cond_by_id(doc['sides']['target']['conditions'])
    assert 'KL@L1' not in cond_by_id(doc['sides']['reference']['conditions'])
    assert rows['AC@L1']['removal_b'] is None and rows['KL@L1']['removal_b'] is not None
    gone, new = rows['AC@L1'], rows['KL@L1']
    ref_ac = cond_by_id(doc['sides']['reference']['conditions'])['AC@L1']['lambda_excl']
    tgt_kl = cond_by_id(doc['sides']['target']['conditions'])['KL@L1']['lambda_excl']
    assert close(gone['delta_lambda_line'], -ref_ac) and close(new['delta_lambda_line'], tgt_kl)
    assert doc['delta']['check']['status'] == 'passed'


def test_vertical_sizes_follow_the_layer_distance(tmp_path):
    vertical = ('vertical_short', 'X', 'L1', 'Y', 'L2', {1: set(), 2: {3, 4}, 3: {3, 4, 5}})
    pairs = PAIRS + [('XY', 'X', 'Y', 'line')]
    code, out = run(tmp_path, (base_cases() + [vertical], pairs), (base_cases() + [vertical], pairs))
    assert code == 0
    doc = load(out)
    row = cond_by_id(doc['sides']['reference']['conditions'])['XY@L2']  # analysis layer = upper layer
    sizes = {s['diameter_um']: s for s in row['by_size']}
    assert sizes[1.0]['area_excl_um2'] == 0.0 and sizes[1.0]['lambda_excl'] == 0.0
    assert sizes[2.0]['area_excl_um2'] > 0 and sizes[3.0]['area_excl_um2'] > sizes[2.0]['area_excl_um2']
    expect = oracle(base_cases() + [vertical], pairs, DENS_LINE, DENS_PIXEL, FACTOR, FACTOR)
    assert close(row['lambda_excl'], expect['cond'][('XY', 'L2')]['excl'])


def test_cancelling_conditions_use_the_slope_limit(tmp_path):
    pairs = [('AB', 'A', 'B', 'line'), ('AC', 'A', 'C', 'line')]
    ref = ([('lateral_short', 'A', 'L1', 'B', 'L1', {1: {0, 1}}), ('lateral_short', 'A', 'L1', 'C', 'L1', {1: {2}})], pairs)
    tgt = ([('lateral_short', 'A', 'L1', 'B', 'L1', {1: {0}}), ('lateral_short', 'A', 'L1', 'C', 'L1', {1: {2, 3}})], pairs)
    code, out = run(tmp_path, ref, tgt)
    assert code == 0
    doc = load(out)
    delta = doc['delta']
    assert delta['indicators']['delta_lambda_line'] == 0.0 and delta['indicators']['delta_vi_line'] == 0.0
    assert delta['slopes']['line']['method'] == 'limit'
    lam = doc['sides']['reference']['lambda']['line']
    assert close(delta['slopes']['line']['value'], math.exp(-lam) / 1.2159)
    rows = cond_by_id(delta['conditions'])
    unit = 5.0 * 1 * 0.01 * FACTOR * math.exp(-lam) / 1.2159
    assert close(rows['AB@L1']['delta_vi_line'], -unit) and close(rows['AC@L1']['delta_vi_line'], unit)
    assert rows['AB@L1']['share_line'] is None and rows['AC@L1']['share_line'] is None
    assert delta['check']['status'] == 'passed'


def test_crp_difference_row(tmp_path):
    code, out = run(tmp_path, (base_cases(), PAIRS),
                    (base_cases(AB={1: {0, 1}, 2: {0}, 3: {0}}, DE={1: {1, 3}, 2: {4, 5}, 3: {4}}), PAIRS),
                    ref_group='mobile', tgt_group='auto')
    assert code == 0
    doc = load(out)
    assert doc['products']['reference']['crp'] == 1.0 and doc['products']['target']['crp'] == 2.92
    delta = doc['delta']
    crp_row = delta['crp_difference']
    x_a = doc['sides']['reference']['lambda']['pixel'] + doc['sides']['reference']['lambda']['cno']
    expected = (1 - math.exp(-x_a)) * (1 / 2.92 - 1 / 1.0) / 1.5462
    assert close(crp_row['delta_vi_pixel'], expected)
    parts = (sum(c['delta_vi_pixel'] for c in delta['conditions']) + delta['shared']['delta_vi_pixel']
             + sum(c['delta_vi_pixel'] for c in delta['cno']) + crp_row['delta_vi_pixel'])
    assert close(parts, delta['indicators']['delta_vi_pixel'])
    assert delta['check']['status'] == 'passed'
    assert doc['sides']['target']['caa_pixel'] == pytest.approx(
        (1 - math.exp(-doc['sides']['target']['lambda']['pixel'])) / 2.92, rel=1e-12)


def test_glass_pending_nulls_metrics_but_keeps_areas(tmp_path):
    code, out = run(tmp_path, (base_cases(), PAIRS), (base_cases(), PAIRS), glass=None)
    assert code == 3
    doc = load(out)
    for role in ('reference', 'target'):
        side = doc['sides'][role]
        assert side['vi_line'] is None and side['vi_pixel'] is None and side['lambda']['line'] is None
        assert side['conservation_check']['status'] == 'not_evaluated'
        assert 'glass_area_pending' in side['reasons']
        area = [r for r in side['layers'] if r['layer'] == 'L1'][0]['by_size'][0]
        assert area['line_area_um2'] > 0
        assert side['conditions'][0]['area_excl_um2'] >= 0 and side['conditions'][0]['lambda_excl'] is None
    delta = doc['delta']
    assert delta['indicators']['delta_vi_line'] is None and delta['slopes'] == {}
    assert all(c['delta_vi_line'] is None and c['share_line'] is None for c in delta['conditions'])
    assert all(c['removal_b']['delta_vi_line'] is None for c in delta['conditions'])
    assert delta['check']['status'] == 'not_evaluated'


def test_missing_density_layer_nulls_that_family(tmp_path):
    pixel = {'P1': DENS_PIXEL['P1'], 'gcnt': DENS_PIXEL['gcnt']}  # no P2
    code, out = run(tmp_path, (base_cases(), PAIRS), (base_cases(), PAIRS), pixel=pixel)
    assert code == 3
    doc = load(out)
    side = doc['sides']['reference']
    assert side['vi_pixel'] is None and side['lambda']['pixel'] is None
    assert side['vi_line'] is not None and doc['delta']['indicators']['delta_vi_line'] is not None
    assert 'density_layer_missing:pixel:P2' in side['reasons']
    l2 = [r for r in side['layers'] if r['layer'] == 'L2'][0]
    assert l2['lambda_pixel'] is None and l2['lambda_line'] is not None
    assert doc['delta']['indicators']['delta_vi_pixel'] is None


CNO_NAME = 'lateralopen-CNO-gcnt'
CNO_CSV = 'defect_size(um),Area(um2)\n1,0.5\n2,1.5\n3,2.5\n'


def test_cno_adds_to_pixel_lambda_but_not_to_layer_shares(tmp_path):
    code, out = run(tmp_path, (base_cases(), PAIRS), (base_cases(), PAIRS), ref_cno={CNO_NAME: CNO_CSV},
                    tgt_cno={CNO_NAME: 'defect_size(um),Area(um2)\n1,0.5\n2,1.5\n3,5.0\n'})
    assert code == 0, load(out)['sides']['reference']['reasons']
    doc = load(out)
    lam_cno_a = (2.0 * 0.5 + 1.0 * 1.5 + 0.5 * 2.5) * FACTOR * math.pi / 4
    lam_cno_b = (2.0 * 0.5 + 1.0 * 1.5 + 0.5 * 5.0) * FACTOR * math.pi / 4
    ref, tgt = doc['sides']['reference'], doc['sides']['target']
    assert close(ref['lambda']['cno'], lam_cno_a) and close(tgt['lambda']['cno'], lam_cno_b)
    (row,) = ref['cno']
    assert row['case_name'] == CNO_NAME and row['density_layer'] == 'gcnt' and row['csv_status'] == 'observed'
    expect = oracle(base_cases(), PAIRS, DENS_LINE, DENS_PIXEL, FACTOR, FACTOR)
    x_a = expect['lam_pixel'] + lam_cno_a
    assert close(ref['caa_pixel'], 1 - math.exp(-x_a)) and close(ref['vi_pixel'], (1 - math.exp(-x_a)) / 1.5462)
    assert close(sum(r['share_pixel'] for r in ref['layers']), 1.0)  # CNO is not in the layer denominator
    delta = doc['delta']
    (cno,) = delta['cno']
    assert cno['presence'] == 'both' and close(cno['delta_lambda_pixel'], lam_cno_b - lam_cno_a)
    assert cno['removal_b']['delta_vi_pixel'] < 0
    assert delta['check']['status'] == 'passed'
    assert [r['case_name'] for r in csv.DictReader((out / 'compare2_cno.csv').open(encoding='utf-8-sig'))] == [CNO_NAME]


def test_invalid_cno_csv_nulls_the_pixel_family(tmp_path):
    code, out = run(tmp_path, (base_cases(), PAIRS), (base_cases(), PAIRS), ref_cno={CNO_NAME: 'bad,header\n1,2\n'},
                    tgt_cno={CNO_NAME: CNO_CSV})
    assert code == 3
    doc = load(out)
    ref = doc['sides']['reference']
    (row,) = ref['cno']
    assert row['lambda'] is None and row['csv_status'] == 'invalid' and row['csv_relative_path'].endswith('lateral_open.csv')
    assert 'detail' in row and ref['lambda']['cno'] is None and ref['vi_pixel'] is None
    assert ref['vi_line'] is not None
    assert doc['delta']['indicators']['delta_vi_pixel'] is None


def test_rounded_delta_does_not_change_ratios(tmp_path):
    code, out = run(tmp_path, (base_cases(), PAIRS),
                    (base_cases(AB={1: {0}, 2: {0, 1}, 3: {0}}), PAIRS), scale=1e-9)
    assert code == 0
    delta = load(out)['delta']
    ind = delta['indicators']
    assert ind['delta_vi_line_rounded'] == 0.0 and ind['delta_vi_line'] != 0.0
    assert close(sum(c['share_line'] for c in delta['conditions']) + delta['shared']['share_line'], 1.0)
    ab = cond_by_id(delta['conditions'])['AB@L1']
    assert ab['share_line'] == pytest.approx(ab['delta_vi_line'] / ind['delta_vi_line'], rel=1e-12) and ab['share_line'] > 0


def test_output_folder_rules_and_error_json(tmp_path):
    code, out = run(tmp_path, (base_cases(), PAIRS), (base_cases(), PAIRS))
    assert code == 0
    document = json.loads((out / 'compare2.json').read_text())
    assert main(['compare2', '--config', str(tmp_path / 'compare2.yaml')]) == 0  # compare2's own folder is reused
    assert json.loads((out / 'compare2.json').read_text()) == document
    stray = tmp_path / 'other'
    stray.mkdir()
    (stray / 'notes.txt').write_text('mine', encoding='utf-8')
    assert main(['compare2', '--config', str(tmp_path / 'compare2.yaml'), '--out', str(stray)]) == 2
    assert (stray / 'notes.txt').read_text() == 'mine' and not (stray / 'error.json').exists()
    cfg = yaml.safe_load((tmp_path / 'compare2.yaml').read_text())
    cfg['products']['target']['hotspot_root'] = str(tmp_path / 'missing')
    (tmp_path / 'bad.yaml').write_text(yaml.safe_dump(cfg), encoding='utf-8')
    assert main(['compare2', '--config', str(tmp_path / 'bad.yaml'), '--out', str(out)]) == 2
    error = json.loads((out / 'error.json').read_text())
    assert error['code'] == 'compare2_hotspot_root_missing' and error['exit_code'] == 2
    assert main(['compare2', '--config', str(tmp_path / 'compare2.yaml')]) == 0
    assert not (out / 'error.json').exists()
    assert not [p for p in out.iterdir() if p.name.startswith('.compare2-')]
