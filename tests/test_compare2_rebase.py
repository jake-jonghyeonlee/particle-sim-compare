"""rnarr/T4: compare2 `rebase` block (B's CA read with A's coefficients, S0..S4) and `inputs.layer_order`.

Same fixtures as test_compare2 (4x4 cell frame). A = reference, B = target. State path: CA -> F -> CRP -> density.
"""
import hashlib
import json
import math
import re

import pytest
import yaml

from simcompare import compare2
from simcompare.cli import main
from test_compare2 import (CNO_CSV, CNO_NAME, DENS_LINE, DENS_PIXEL, PAIRS, RES, STACK, assignment, base_cases,
                           load, only, run, two_sides, write_density, write_side)

KINDS = ('line', 'pixel')
SEGS = ('ca', 'f', 'crp', 'density')
SAME = dict(group='mobile', res=RES, glass=50.0, weights=(1.0, 1.0), line=DENS_LINE, pixel=DENS_PIXEL)


def run2(tmp, ref, tgt, a=None, b=None, ref_cno=None, tgt_cno=None, stack=None, diameters=None, name='out'):
    """Like test_compare2.run with its own product settings per side: dict(group, res, glass, weights, line, pixel)."""
    a, b = dict(SAME, **(a or {})), dict(SAME, **(b or {}))
    ref_root, ref_imp = write_side(tmp, 'ref', *ref, cno=ref_cno)
    tgt_root, tgt_imp = write_side(tmp, 'tgt', *tgt, cno=tgt_cno)
    assign = assignment()
    if stack is not None:
        assign['layer_stack'] = stack
    numeric = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'unused',
               'reference': {'data_dir': str(ref_imp), 'condition_assignment': assign},
               'target': {'data_dir': str(tgt_imp), 'condition_assignment': assign}}
    (tmp / 'numeric.yaml').write_text(yaml.safe_dump(numeric), encoding='utf-8')
    groups = {}
    for side in (a, b):
        if side['group'] in groups:
            assert groups[side['group']] == (side['line'], side['pixel'], side['glass'], side['weights'])
        groups[side['group']] = (side['line'], side['pixel'], side['glass'], side['weights'])
        write_density(tmp / 'density', side['group'], side['line'], side['pixel'])
    cfg = {'schema_version': 'compare2_config_v1', 'numeric_config': 'numeric.yaml', 'output_dir': name,
           'density_dir': 'density',
           'model_groups': {group: {'glass_area': {'value': glass, 'unit': 'um2'}, 'weight_line': weights[0],
                                    'weight_pixel': weights[1]}
                            for group, (_l, _p, glass, weights) in groups.items()},
           'products': {'reference': {'model_group': a['group'], 'resolution': a['res'], 'hotspot_root': str(ref_root),
                                      **({'unit_size_um': a['unit']} if a.get('unit') else {})},
                        'target': {'model_group': b['group'], 'resolution': b['res'], 'hotspot_root': str(tgt_root),
                                   **({'unit_size_um': b['unit']} if b.get('unit') else {})}}}
    if diameters is not None:
        cfg['lambda_diameters_um'] = diameters
    (tmp / 'compare2.yaml').write_text(yaml.safe_dump(cfg), encoding='utf-8')
    code = main(['compare2', '--config', str(tmp / 'compare2.yaml')])
    return code, tmp / name


def sub(tmp, name):
    folder = tmp / name
    folder.mkdir()
    return folder


def go(tmp, *args, **kwargs):
    code, out = run2(tmp, *args, **kwargs)
    return code, load(out), out


def segs(doc, kind):
    return doc['rebase']['states'][kind]['segments']


def seg_sums(doc, kind):
    return {name: seg['sum'] for name, seg in segs(doc, kind).items()}


def assert_closed(doc):
    """Every kind's checks pass: Σ item (+CNO) = S1/S2 lambda, each segment = its vi difference, segments = delta."""
    rebase = doc['rebase']
    assert rebase['status'] == 'available', rebase['reasons']
    assert rebase['check']['status'] == 'passed', rebase['check']
    for kind in KINDS:
        state = rebase['states'][kind]
        assert state['sum_of_segments'] == pytest.approx(doc['delta']['indicators'][f'delta_vi_{kind}'], abs=1e-12)
        assert state['delta'] == doc['delta']['indicators'][f'delta_vi_{kind}']
        for seg in state['segments'].values():
            assert seg['sum'] == pytest.approx(seg['delta_vi'], abs=1e-12)
        # S0 and S4 are the two documents' own vi
    assert rebase['states']['line']['S0']['vi'] == doc['sides']['reference']['vi_line']
    assert rebase['states']['pixel']['S4']['vi'] == doc['sides']['target']['vi_pixel']


def item_total(doc, kind, seg):
    return math.fsum(i[f'{seg}_delta'] for i in doc['rebase']['items'] if i['class'] == kind)


# ---- the equal / one-factor cases -----------------------------------------------------------------------------

def test_identical_sides_have_bit_identical_s1_and_zero_later_segments(tmp_path):
    ref, tgt = two_sides()
    code, doc, _out = go(tmp_path, ref, ref)  # the same cases, the same coefficients
    assert code == 0
    assert_closed(doc)
    for kind in KINDS:
        state = doc['rebase']['states'][kind]
        x_b = doc['sides']['target']['lambda']['line'] if kind == 'line' else (
            doc['sides']['target']['lambda']['pixel'] + doc['sides']['target']['lambda']['cno'])
        assert state['S1']['x'] == x_b and state['S2']['x'] == x_b          # bit identical, not approx
        assert state['S1']['vi'] == doc['sides']['target'][f'vi_{kind}']
        assert all(seg['sum'] == 0.0 for seg in state['segments'].values())
    by_id = {(i['item_id'], i['class']): i for i in doc['sides']['target']['ledger_items']}
    for item in doc['rebase']['items']:
        assert item['lambda_s1'] == by_id[(item['item_id'], item['class'])]['lambda'] == item['lambda_s2']
        assert item['ca_delta'] == item['f_delta'] == item['density_delta'] == 0.0
    assert doc['rebase']['missing'] == []


def test_only_the_coefficients_differ_gives_zero_ca_segment(tmp_path):
    ref, _tgt = two_sides()
    code, doc, _out = go(tmp_path, ref, ref, b=dict(group='watch'))  # watch: effective = W*H (4x the mobile one)
    assert code == 0
    assert_closed(doc)
    for kind in KINDS:
        assert segs(doc, kind)['ca']['sum'] == 0.0
        assert segs(doc, kind)['f']['sum'] != 0.0
        assert segs(doc, kind)['density']['sum'] == 0.0
    assert all(i['ca_delta'] == 0.0 for i in doc['rebase']['items'])
    ratios = doc['rebase']['coefficients']['ratios']['line']
    assert ratios['r_WH'] == 1.0 and ratios['r_rule'] == 4.0 and ratios['r_M'] == 1.0 and ratios['r_G'] == 1.0
    assert ratios['r_F'] == pytest.approx(4.0)
    assert ratios['r_F'] == pytest.approx(ratios['r_WH'] * ratios['r_rule'] * ratios['r_M'] / ratios['r_G'])


def test_only_the_cells_differ_gives_zero_later_segments(tmp_path):
    ref, tgt = two_sides()
    code, doc, _out = go(tmp_path, ref, tgt)
    assert code == 0
    assert_closed(doc)
    for kind in KINDS:
        sums = seg_sums(doc, kind)
        assert sums['ca'] == pytest.approx(doc['delta']['indicators'][f'delta_vi_{kind}'], abs=1e-12)
        assert sums['f'] == 0.0 and sums['density'] == 0.0 and sums.get('crp', 0.0) == 0.0
    assert any(i['ca_delta'] != 0.0 for i in doc['rebase']['items'])
    # the slope of segment 1 is its own secant: x_S0 -> x_S1 (= x_B here)
    line = segs(doc, 'line')['ca']['slope']
    assert line['value'] == doc['delta']['slopes']['line']['value'] and line['method'] == doc['delta']['slopes']['line']['method']


def test_same_group_resolution_only_gives_only_the_f_segment(tmp_path):
    ref, _tgt = two_sides()
    code, doc, _out = go(tmp_path, ref, ref, b=dict(res=[40, 10]))   # same group, same tables, same crp
    assert code == 0
    assert_closed(doc)
    for kind in KINDS:
        sums = seg_sums(doc, kind)
        assert sums['ca'] == 0.0 and sums['density'] == 0.0 and sums.get('crp', 0.0) == 0.0 and sums['f'] != 0.0
    ratios = doc['rebase']['coefficients']['ratios']['pixel']
    assert ratios['r_WH'] == 2.0 and ratios['r_rule'] == 1.0 and ratios['r_F'] == pytest.approx(2.0)


def test_only_crp_differs_gives_only_the_crp_line(tmp_path):
    ref, _tgt = two_sides()
    # mobile (effective 200//4 = 50, crp 1) vs auto (effective 10*5 = 50, crp 2.92): same F, same tables
    code, doc, _out = go(tmp_path, ref, ref, b=dict(group='auto', res=[10, 5]))
    assert code == 0
    assert_closed(doc)
    assert doc['sides']['reference']['vi_pixel'] != doc['sides']['target']['vi_pixel']
    sums = seg_sums(doc, 'pixel')
    assert sums['ca'] == 0.0 and sums['f'] == 0.0 and sums['density'] == 0.0 and sums['crp'] != 0.0
    assert sums['crp'] == pytest.approx(doc['delta']['indicators']['delta_vi_pixel'], abs=1e-12)
    assert doc['delta']['crp_difference']['delta_vi_pixel'] == pytest.approx(sums['crp'], abs=1e-15)
    # line has no CRP: no segment, no change
    assert 'crp' not in segs(doc, 'line')
    assert all(v == 0.0 for v in seg_sums(doc, 'line').values())
    assert 'crp' not in doc['rebase']['states']['line']['S1'] and 'crp' not in doc['rebase']['states']['line']['S4']
    assert doc['rebase']['states']['pixel']['S2']['crp'] == 1.0 and doc['rebase']['states']['pixel']['S3']['crp'] == 2.92


def test_line_does_not_depend_on_a_crp_not_equal_to_one(tmp_path):
    ref, tgt = two_sides()
    # both sides crp 2.92 (auto, effective 50)  vs  both sides crp 1 (mobile, effective 50): the line series are equal
    code, with_crp, _o = go(sub(tmp_path, 'a'), ref, tgt,
                            a=dict(group='auto', res=[10, 5]), b=dict(group='auto', res=[10, 5]))
    assert code == 0
    code, without, _o = go(sub(tmp_path, 'b'), ref, tgt)
    assert code == 0
    assert_closed(with_crp)
    assert_closed(without)
    assert with_crp['rebase']['states']['line'] == without['rebase']['states']['line']
    assert 'crp' not in segs(with_crp, 'line')
    assert segs(with_crp, 'pixel')['crp']['sum'] == 0.0     # equal CRP: the pixel CRP line is 0


def test_only_the_density_differs_gives_only_the_density_segment(tmp_path):
    ref, _tgt = two_sides()
    heavier = {layer: {d: v * 1.5 for d, v in row.items()} for layer, row in DENS_LINE.items()}
    heavier_p = {layer: {d: v * 2.0 for d, v in row.items()} for layer, row in DENS_PIXEL.items()}
    # watch res [10,5] -> effective 50 = mobile's 200//4; both crp 1
    code, doc, _out = go(tmp_path, ref, ref, b=dict(group='watch', res=[10, 5], line=heavier, pixel=heavier_p))
    assert code == 0
    assert_closed(doc)
    for kind in KINDS:
        sums = seg_sums(doc, kind)
        assert sums['ca'] == 0.0 and sums['f'] == 0.0 and sums.get('crp', 0.0) == 0.0 and sums['density'] != 0.0
    assert all(i['density_delta'] != 0.0 for i in doc['rebase']['items'] if i['lambda_b'] != 0.0)


def test_all_factors_change_at_once_close_to_delta(tmp_path):
    ref, tgt = two_sides()
    heavier = {layer: {d: v * 1.3 for d, v in row.items()} for layer, row in DENS_LINE.items()}
    heavier_p = {layer: {d: v * 1.7 for d, v in row.items()} for layer, row in DENS_PIXEL.items()}
    code, doc, _out = go(tmp_path, ref, tgt, b=dict(group='auto', res=[7, 9], line=heavier, pixel=heavier_p,
                                                    glass=40.0, weights=(2.0, 3.0)))
    assert code == 0
    assert_closed(doc)
    for kind in KINDS:
        assert all(seg['sum'] != 0.0 for seg in segs(doc, kind).values())
    # each segment has its own slope
    slopes = {seg['slope']['value'] for seg in segs(doc, 'line').values() if 'slope' in seg}
    assert len(slopes) == 3
    for kind in KINDS:
        for seg in ('ca', 'f', 'density'):
            seg_doc = segs(doc, kind)[seg]
            cno = 0.0 if seg_doc['cno_sum'] is None else seg_doc['cno_sum']
            assert math.fsum([item_total(doc, kind, seg), cno]) == pytest.approx(seg_doc['delta_vi'], abs=1e-12)
            assert seg_doc['items_sum'] == pytest.approx(item_total(doc, kind, seg), abs=1e-15)


def test_f_a_zero_is_defined(tmp_path):
    ref, tgt = two_sides()
    code, doc, _out = go(tmp_path, ref, tgt, a=dict(res=[1, 1]))   # mobile 1x1: int(1/4) = 0 -> F_A = 0
    assert doc['rebase']['coefficients']['reference']['F']['line'] == 0.0
    assert doc['rebase']['status'] == 'available'
    assert doc['rebase']['check']['status'] == 'passed'
    ratios = doc['rebase']['coefficients']['ratios']['line']
    assert ratios['r_F'] is None and ratios['r_WH'] == 200.0    # the F ratio has no denominator, the others do
    assert doc['rebase']['states']['line']['S1']['x'] == 0.0     # B's CA read with F_A = 0
    assert doc['rebase']['states']['line']['S0']['x'] == 0.0
    assert seg_sums(doc, 'line')['ca'] == 0.0 and seg_sums(doc, 'line')['f'] != 0.0
    assert doc['rebase']['states']['line']['sum_of_segments'] == pytest.approx(
        doc['delta']['indicators']['delta_vi_line'], abs=1e-12)


# ---- new / gone / CNO -------------------------------------------------------------------------------------------

def test_new_and_gone_items(tmp_path):
    full = (base_cases(), PAIRS)
    smaller = (base_cases(GH=None, IJ=None), PAIRS)       # L2 cases left out
    code, doc, _out = go(sub(tmp_path, 'x'), smaller, full)
    assert code in (0, 3)
    assert_closed(doc)
    new = [i for i in doc['rebase']['items'] if i['presence'] == 'new']
    assert new and all(i['lambda_a'] == 0.0 and i['lambda_s1'] > 0.0 and i['analysis_layer'] == 'L2' for i in new)
    assert all(i['ca_delta'] > 0 for i in new if i['class'] == 'line')
    code, doc2, _out = go(sub(tmp_path, 'y'), full, smaller)
    assert_closed(doc2)
    gone = [i for i in doc2['rebase']['items'] if i['presence'] == 'gone']
    assert gone and all(i['lambda_b'] == 0.0 and i['lambda_s1'] == 0.0 and i['lambda_s2'] == 0.0 and i['by_size'] == []
                        for i in gone)
    assert all(i['ca_delta'] < 0 for i in gone if i['class'] == 'line')


def test_cno_new_gone_and_changed(tmp_path):
    ref, tgt = two_sides()
    changed = 'defect_size(um),Area(um2)\n1,0.5\n2,1.5\n3,5.0\n'
    for name, a_cno, b_cno in (('new', None, {CNO_NAME: CNO_CSV}), ('gone', {CNO_NAME: CNO_CSV}, None),
                               ('both', {CNO_NAME: CNO_CSV}, {CNO_NAME: changed})):
        folder = tmp_path / name
        folder.mkdir()
        code, doc, _out = go(folder, ref, tgt, ref_cno=a_cno, tgt_cno=b_cno)
        assert code == 0, doc['sides']['reference']['reasons']
        assert_closed(doc)
        (row,) = doc['rebase']['cno']
        assert row['presence'] == name and row['case_name'] == CNO_NAME
        if name == 'new':
            assert row['lambda_a'] == 0.0 and row['lambda_s1'] == row['lambda_b'] and row['ca_delta'] > 0
        elif name == 'gone':
            assert row['lambda_b'] == 0.0 and row['lambda_s1'] == 0.0 and row['ca_delta'] < 0 and row['by_size'] == []
        else:
            assert row['lambda_s1'] == row['lambda_b'] != row['lambda_a']
            assert [s['area_csv_um2'] for s in row['by_size']] == [0.5, 1.5, 5.0]
        # the pixel segment sums include the CNO line
        assert segs(doc, 'pixel')['ca']['cno_sum'] == pytest.approx(row['ca_delta'], abs=1e-15)
        assert doc['rebase']['states']['line']['status'] == 'available'


def test_cno_f_segment_uses_the_cno_factor(tmp_path):
    ref, _tgt = two_sides()
    code, doc, _out = go(tmp_path, ref, ref, b=dict(res=[40, 10]), ref_cno={CNO_NAME: CNO_CSV}, tgt_cno={CNO_NAME: CNO_CSV})
    assert code == 0
    assert_closed(doc)
    (row,) = doc['rebase']['cno']
    assert row['lambda_s1'] == row['lambda_a']
    assert row['lambda_s2'] == pytest.approx(2.0 * row['lambda_a']) and row['lambda_s2'] == row['lambda_b']
    assert row['ca_delta'] == 0.0 and row['density_delta'] == 0.0 and row['f_delta'] != 0.0


# ---- waiting rules ----------------------------------------------------------------------------------------------

def test_a_density_missing_where_b_has_area_makes_that_kind_unavailable(tmp_path):
    only_l1 = (base_cases(GH=None, IJ=None), PAIRS)                   # A has no L2 cell at all
    full = (base_cases(), PAIRS)
    no_p2_line = {'P1': DENS_LINE['P1']}                              # A's line density table has no P2
    code, doc, _out = go(tmp_path, only_l1, full, a=dict(group='mobile', line=no_p2_line), b=dict(group='watch', res=[10, 5]))
    assert code in (0, 3)
    rebase = doc['rebase']
    assert doc['sides']['reference']['status'] in ('available', 'partial')
    assert rebase['status'] == 'partial'
    assert rebase['states']['line']['status'] == 'unavailable' and rebase['states']['pixel']['status'] == 'available'
    assert any(r.startswith('line:reference_density_layer_missing:line:P2') for r in rebase['reasons'])
    gaps = [m for m in rebase['missing'] if m['kind'] == 'line']
    assert gaps and all(m['density_layer'] == 'P2' and m['area_b_um2'] > 0 and m['reason'] == 'density_layer_missing'
                        for m in gaps)
    assert all(m['lambda_b'] is not None and 0 < m['lambda_b_share'] <= 1 for m in gaps)
    assert {m['diameter_um'] for m in gaps} <= {1.0, 2.0, 3.0}
    # no partial sum is written as a vi: the unavailable kind has no segments, its items no deltas
    assert 'segments' not in rebase['states']['line']
    assert all(i['ca_delta'] is None and i['f_delta'] is None for i in rebase['items'] if i['class'] == 'line')
    assert 'segments' in rebase['states']['pixel']
    assert rebase['check']['line']['status'] == 'not_evaluated'
    # 02 itself is unchanged by the waiting (exit code and side status do not come from rebase)
    assert doc['delta']['check']['status'] == 'passed'


def test_a_density_missing_for_a_zero_area_is_zero_not_unavailable(tmp_path):
    zero_b = (base_cases(GH={}, IJ={}), PAIRS)                         # L2 cases exist but have no hotspot cell
    only_l1 = (base_cases(GH=None, IJ=None), PAIRS)
    no_p2 = {'P1': DENS_LINE['P1']}
    code, doc, _out = go(tmp_path, only_l1, zero_b, a=dict(line=no_p2, pixel={'P1': DENS_PIXEL['P1'], 'gcnt': DENS_PIXEL['gcnt']}),
                         b=dict(group='watch', res=[10, 5]))
    assert doc['rebase']['status'] == 'available', doc['rebase']['reasons']
    assert doc['rebase']['missing'] == []
    for item in doc['rebase']['items']:
        if item['analysis_layer'] == 'L2':
            assert item['lambda_s1'] == 0.0 and item['ca_delta'] == 0.0


def test_a_density_missing_for_the_cno_layer_makes_pixel_unavailable(tmp_path):
    """R9:4 (a): B has a CNO area, A's pixel density table has no CNO layer: `_rebase_cno_rows` reports it in missing[]."""
    ref, tgt = two_sides()
    no_cno_layer = {'P1': DENS_PIXEL['P1'], 'P2': DENS_PIXEL['P2']}
    code, doc, _out = go(tmp_path, ref, tgt, a=dict(pixel=no_cno_layer), b=dict(group='watch', res=[10, 5]),
                         tgt_cno={CNO_NAME: CNO_CSV})
    rebase = doc['rebase']
    assert rebase['states']['pixel']['status'] == 'unavailable'
    assert rebase['states']['line']['status'] == 'available'
    gaps = [m for m in rebase['missing'] if m['scope'] == 'cno']
    assert gaps and all(m['kind'] == 'pixel' and m['case_name'] == CNO_NAME and m['density_layer'] == 'gcnt'
                        and m['area_b_um2'] > 0 for m in gaps)
    assert any(r.endswith(':pixel:gcnt') or ':pixel:gcnt' in r for r in rebase['reasons'])
    assert 'segments' not in rebase['states']['pixel']


def test_a_density_missing_only_outside_the_diameter_filter_keeps_the_kind_available(tmp_path):
    """R9:4 (c): the gap is at a diameter the filter removed, so nothing of B uses it."""
    ref, tgt = two_sides()
    gap_at_3 = {'P1': DENS_LINE['P1'], 'P2': {1: 4.0, 2: 2.0}}
    code, doc, _out = go(tmp_path, ref, tgt, a=dict(line=gap_at_3), b=dict(group='watch', res=[10, 5]), diameters=[1, 2])
    rebase = doc['rebase']
    assert rebase['states']['line']['status'] == 'available', rebase['reasons']
    assert [m for m in rebase['missing'] if m['kind'] == 'line'] == []
    for item in rebase['items']:
        assert {s['diameter_um'] for s in item['by_size']} <= {1.0, 2.0}


def test_glass_pending_makes_both_kinds_unavailable_and_keeps_02_status(tmp_path):
    ref, tgt = two_sides()
    code, doc, out = go(tmp_path, ref, tgt, a=dict(glass=None), b=dict(group='watch', res=[10, 5]))
    assert code == 3      # the existing partial exit of a pending glass area, not changed by rebase
    rebase = doc['rebase']
    assert rebase['status'] == 'unavailable'
    for kind in KINDS:
        assert rebase['states'][kind]['status'] == 'unavailable'
        assert any(r.startswith('factor_unavailable:reference:') for r in rebase['states'][kind]['reasons'])
    assert rebase['items'] == [] and rebase['cno'] == []
    assert rebase['coefficients']['reference']['F']['line'] is None
    assert rebase['coefficients']['ratios']['line']['r_G'] is None and rebase['coefficients']['ratios']['line']['r_F'] is None
    assert rebase['check']['status'] == 'not_evaluated'
    execution = json.loads((out / 'execution.json').read_text(encoding='utf-8'))
    assert execution['exit_code'] == 3


def test_lambda_diameters_subset(tmp_path):
    ref, tgt = two_sides()
    code, doc, _out = go(tmp_path, ref, tgt, b=dict(group='auto', res=[7, 9], glass=40.0), diameters=[3])
    assert code == 0
    assert_closed(doc)
    for item in doc['rebase']['items']:
        assert {s['diameter_um'] for s in item['by_size']} <= {3.0}
    side = doc['sides']['target']
    assert doc['rebase']['states']['line']['S4']['x'] == side['lambda']['line']
    # the subset result differs from the full range
    full = tmp_path / 'full'
    full.mkdir()
    code, doc_all, _out = go(full, ref, tgt, b=dict(group='auto', res=[7, 9], glass=40.0))
    assert doc_all['rebase']['states']['line']['S1']['x'] != doc['rebase']['states']['line']['S1']['x']


# ---- the new keys are the only change ----------------------------------------------------------------------------

def test_without_the_new_keys_the_outputs_are_the_old_bytes(tmp_path):
    # R9:4 (b): the golden bytes of the old keys are test_compare2.GOLDEN's job; this test only compares with the old helper run
    ref, tgt = two_sides()
    code, doc, out = go(tmp_path, ref, tgt)
    assert code == 0
    assert doc['inputs']['rebase_basis'] == 'target_ca_reference_coefficients_v1'
    stripped = json.loads(json.dumps(doc))
    stripped.pop('rebase')
    stripped['inputs'].pop('rebase_basis')
    stripped['inputs'].pop('layer_order')
    # an old key is never changed: every old value equals the value of a run through the old test helper
    code, out2 = run(sub(tmp_path, 'old'), ref, tgt)
    old = load(out2)
    mask = lambda value: json.loads(json.dumps(value).replace(str(tmp_path / 'old'), 'T').replace(str(tmp_path), 'T'))
    for key in ('sides', 'delta', 'products', 'schema_version'):
        assert mask(stripped[key]) == mask(old[key]), key
    for key in old['inputs']:
        if key not in ('compare2_config', 'numeric_config', 'density_dir', 'rebase_basis', 'layer_order'):
            assert stripped['inputs'][key] == old['inputs'][key], key
    for name in ('compare2_summary.csv', 'compare2_layers.csv', 'compare2_conditions.csv', 'compare2_cno.csv'):
        assert (out / name).read_bytes().replace(str(tmp_path).encode(), b'T') == \
            (out2 / name).read_bytes().replace(str(tmp_path / 'old').encode(), b'T'), name


def test_two_runs_write_the_same_bytes(tmp_path):
    ref, tgt = two_sides()
    texts = []
    for name in ('one', 'two'):
        folder = tmp_path / name
        folder.mkdir()
        code, doc, out = go(folder, ref, tgt, b=dict(group='auto', res=[7, 9], glass=40.0))
        text = (out / 'compare2.json').read_text(encoding='utf-8').replace(str(folder), 'T')
        texts.append(re.sub(r'[0-9a-f]{64}', 'H', text))
    assert texts[0] == texts[1]


# ---- layer_order ------------------------------------------------------------------------------------------------

def test_layer_order_follows_the_layer_stack(tmp_path):
    ref, tgt = two_sides()
    stack = [{'actual_layer': 'L2', 'stack_order': 1, 'density_process_layer': 'P2'},
             {'actual_layer': 'L1', 'stack_order': 2, 'density_process_layer': 'P1'}]
    code, doc, _out = go(tmp_path, ref, tgt, stack=stack, ref_cno={CNO_NAME: CNO_CSV}, tgt_cno={CNO_NAME: CNO_CSV})
    assert code == 0
    # the stack first (order ascending), then the other layers (density layers, CNO layer) by name
    assert doc['inputs']['layer_order'] == ['L2', 'L1', 'P1', 'P2', 'gcnt']
    code, doc, _out = go(sub(tmp_path, 'default'), ref, tgt)
    assert doc['inputs']['layer_order'][:2] == ['L1', 'L2']


def _fake_side(layers, density_layers=()):
    entries = {(layer, density, 3): None for layer, density in zip(layers, density_layers or layers)}
    return {'entries': entries, 'cno': {}, 'ledger_items': []}


def test_layer_order_sd1_sd2_pxl_and_layers_outside_the_stack():
    cfg = {'reference': {'condition_assignment': {'layer_stack': [
               {'actual_layer': 'PXL', 'stack_order': 3}, {'actual_layer': 'SD1', 'stack_order': 1},
               {'actual_layer': 'SD2', 'stack_order': 2}]}},
           'target': {'condition_assignment': {'layer_stack': [
               {'actual_layer': 'SD1', 'stack_order': 1}, {'actual_layer': 'ALT', 'stack_order': 2},
               {'actual_layer': 'PXL', 'stack_order': 3}]}}}
    sides = [_fake_side(['PXL', 'SD1'], ['ZZ', 'CNO_L']), _fake_side(['SD2'], ['AA'])]
    # same order (2): by name; layers outside the stack (AA, CNO_L, ZZ) behind, by name
    assert compare2._layer_order(cfg, sides) == ['SD1', 'ALT', 'SD2', 'PXL', 'AA', 'CNO_L', 'ZZ']
    assert compare2._layer_order({}, sides) == ['AA', 'CNO_L', 'PXL', 'SD1', 'SD2', 'ZZ']


def test_layer_order_ignores_layer_stack_rows_that_are_not_mappings():
    """R9:5: a stray string or null in layer_stack is skipped, the other rows still count."""
    cfg = {'reference': {'condition_assignment': {'layer_stack': ['SD1', None, 3, {'actual_layer': 'SD2', 'stack_order': 1}]}}}
    assert compare2._layer_order(cfg, [_fake_side(['SD1', 'SD2'])]) == ['SD2', 'SD1']


# ---- rnarr/T10: area basis (S1a, design_area, unit_size) ----------------------------------------------------------------

NEW_SEGMENTS = ('design_area', 'unit_size')
OTHER = dict(group='auto', res=[7, 9], glass=40.0)   # a different group: F, CRP and glass differ from SAME


def old_view(rebase):
    """The rebase block without any key added by T10."""
    out = json.loads(json.dumps(rebase))
    out.pop('area_basis', None)
    for state in out['states'].values():
        state.pop('S1a', None)
        for name in NEW_SEGMENTS:
            state.get('segments', {}).pop(name, None)
    for row in out['items'] + out['cno']:
        for key in ('lambda_s1a', 'design_area_delta', 'unit_size_delta'):
            row.pop(key, None)
    for check in out['check'].values():
        if isinstance(check, dict):
            for key in ('s1a_lambda', 's1a_cno', 'area_split'):
                check.pop(key, None)
            for name in NEW_SEGMENTS:
                check.get('segments', {}).pop(name, None)
    return out


def assert_area_closed(doc):
    assert_closed(doc)
    assert doc['rebase']['area_basis']['status'] == 'available'
    for kind in KINDS:
        state = doc['rebase']['states'][kind]
        seg = state['segments']
        assert seg['design_area']['sum'] + seg['unit_size']['sum'] == pytest.approx(seg['ca']['sum'], abs=1e-12)
        for name in NEW_SEGMENTS:
            cno = math.fsum(r[f'{name}_delta'] for r in doc['rebase']['cno']) if kind == 'pixel' else 0.0
            assert item_total(doc, kind, name) + cno == pytest.approx(seg[name]['delta_vi'], abs=1e-12)
        assert state['S1a']['vi'] - state['S0']['vi'] == pytest.approx(seg['design_area']['delta_vi'], abs=1e-15)
        assert doc['rebase']['check'][kind]['area_split']['status'] == 'passed'
        assert doc['rebase']['check'][kind]['s1a_lambda']['status'] == 'passed'


@pytest.mark.parametrize('unit_a, unit_b', [([10, 10], [10, 10]), ([10, 20], [20, 10]), ([5, 8], [8, 5])])
def test_equal_unit_area_makes_s1a_bit_identical_to_s1(tmp_path, unit_a, unit_b):
    ref, tgt = two_sides()
    code, doc, _out = go(tmp_path, ref, tgt, a=dict(unit=unit_a), b=dict(OTHER, unit=unit_b))
    assert code == 0
    assert_area_closed(doc)
    basis = doc['rebase']['area_basis']
    assert basis['ratio'] == 1.0 and basis['u_area_a'] == basis['u_area_b']
    for kind in KINDS:
        state = doc['rebase']['states'][kind]
        assert state['S1a'] == dict(state['S1'])           # x, vi, lambda, lambda_cno (and crp) bit identical
        seg = state['segments']
        assert seg['design_area'] == seg['ca']
        assert seg['unit_size']['sum'] == 0.0 and seg['unit_size']['items_sum'] == 0.0
    for row in doc['rebase']['items']:
        assert row['lambda_s1a'] == row['lambda_s1']
        assert row['design_area_delta'] == row['ca_delta'] and row['unit_size_delta'] == 0.0


def test_non_square_units_scale_s1a_by_the_area_ratio_and_keep_the_old_ca_segment(tmp_path):
    ref, tgt = two_sides()
    code, doc, _out = go(tmp_path, ref, tgt, a=dict(unit=[10, 20]), b=dict(OTHER, unit=[30, 10]),
                         ref_cno={CNO_NAME: CNO_CSV}, tgt_cno={CNO_NAME: CNO_CSV})
    assert code == 0
    assert_area_closed(doc)
    basis = doc['rebase']['area_basis']
    assert basis == {'status': 'available', 'reasons': [], 'u_area_a': 200, 'u_area_b': 300, 'ratio': 200 / 300}
    for kind in KINDS:
        state = doc['rebase']['states'][kind]
        assert state['S1a']['x'] == pytest.approx(state['S1']['x'] * basis['ratio'], rel=1e-12)
        assert state['S1a']['lambda'] == state['S1']['lambda'] * basis['ratio']
        assert list(state['segments']) == [n for n in ('ca', 'design_area', 'unit_size', 'f', 'crp', 'density')
                                           if n in state['segments']]
        assert state['segments']['unit_size']['sum'] != 0.0 and state['segments']['design_area']['sum'] != 0.0
        # the sum of segments still counts the CA change once
        assert state['sum_of_segments'] == pytest.approx(doc['delta']['indicators'][f'delta_vi_{kind}'], abs=1e-12)
    for row in doc['rebase']['items']:
        assert row['ca_delta'] is not None and row['design_area_delta'] is not None
    (cno,) = doc['rebase']['cno']
    assert cno['lambda_s1a'] == pytest.approx(cno['lambda_s1'] * basis['ratio'], rel=1e-12)
    assert segs(doc, 'pixel')['design_area']['cno_sum'] == pytest.approx(cno['design_area_delta'], abs=1e-15)
    assert segs(doc, 'pixel')['unit_size']['cno_sum'] == pytest.approx(cno['unit_size_delta'], abs=1e-15)
    assert segs(doc, 'line')['design_area']['cno_sum'] is None


def test_document_example_proportions(tmp_path):
    """A 50 um unit, N 1000k, B 60 um unit, N 700k: design per area follows CA x U_A/U_B, screen area is +0.8 %."""
    ref, tgt = two_sides()
    code, doc, _out = go(tmp_path, ref, tgt, a=dict(res=[100, 40], unit=[50, 50]), b=dict(res=[70, 40], unit=[60, 60]))
    assert code == 0
    assert_area_closed(doc)
    line = doc['rebase']['states']['line']
    ratio = 2500 / 3600
    assert doc['rebase']['area_basis']['ratio'] == ratio
    assert line['S1a']['x'] / line['S0']['x'] == pytest.approx(line['S1']['x'] / line['S0']['x'] * ratio, rel=1e-12)
    assert line['S4']['x'] / line['S1a']['x'] == pytest.approx(1.008, rel=1e-9)   # (U_B x N_B) / (U_A x N_A)
    assert line['S2']['x'] / line['S1a']['x'] == pytest.approx(1.008, rel=1e-9)


def test_pixel_crp_change_stays_after_the_area_segments_and_line_ignores_crp(tmp_path):
    ref, tgt = two_sides()
    code, doc, _out = go(sub(tmp_path, 'x'), ref, tgt, a=dict(unit=[10, 10]), b=dict(group='auto', res=[10, 5], unit=[12, 10]))
    assert code == 0
    assert_area_closed(doc)
    pixel = doc['rebase']['states']['pixel']
    assert pixel['S1a']['crp'] == 1.0 and pixel['segments']['crp']['sum'] != 0.0
    assert 'crp' not in doc['rebase']['states']['line']['S1a']
    # line: both sides crp 2.92 against both sides crp 1 gives the same line states, S1a included
    code, with_crp, _o = go(sub(tmp_path, 'a'), ref, tgt, a=dict(group='auto', res=[10, 5], unit=[10, 10]),
                            b=dict(group='auto', res=[10, 5], unit=[12, 10]))
    code, without, _o = go(sub(tmp_path, 'b'), ref, tgt, a=dict(unit=[10, 10]), b=dict(unit=[12, 10]))
    assert_area_closed(with_crp)
    assert with_crp['rebase']['states']['line'] == without['rebase']['states']['line']


def test_one_kind_not_converted_has_no_s1a_but_the_other_keeps_it(tmp_path):
    only_l1 = (base_cases(GH=None, IJ=None), PAIRS)
    full = (base_cases(), PAIRS)
    code, doc, _out = go(tmp_path, only_l1, full, a=dict(line={'P1': DENS_LINE['P1']}, unit=[10, 10]),
                         b=dict(group='watch', res=[10, 5], unit=[20, 10]))
    rebase = doc['rebase']
    assert rebase['area_basis']['status'] == 'available'
    assert rebase['states']['line']['status'] == 'unavailable' and 'S1a' not in rebase['states']['line']
    assert all(i['design_area_delta'] is None and i['unit_size_delta'] is None
               for i in rebase['items'] if i['class'] == 'line')
    assert 'S1a' in rebase['states']['pixel'] and 'design_area' in rebase['states']['pixel']['segments']
    assert rebase['check']['pixel']['area_split']['status'] == 'passed'


@pytest.mark.parametrize('units', [(None, None), ([10, 10], None), (None, [10, 10])])
def test_missing_unit_size_makes_area_basis_unavailable_and_changes_nothing_else(tmp_path, units):
    ref, tgt = two_sides()
    code, doc, _out = go(sub(tmp_path, 'x'), ref, tgt, a=dict(unit=units[0]), b=dict(OTHER, unit=units[1]),
                         ref_cno={CNO_NAME: CNO_CSV}, tgt_cno={CNO_NAME: CNO_CSV})
    code0, base, _o = go(sub(tmp_path, 'y'), ref, tgt, b=OTHER, ref_cno={CNO_NAME: CNO_CSV}, tgt_cno={CNO_NAME: CNO_CSV})
    assert code == code0 == 0
    basis = doc['rebase']['area_basis']
    assert basis['status'] == 'unavailable' and basis['reasons'] == ['unit_size_missing'] and basis['ratio'] is None
    for kind in KINDS:
        assert 'S1a' not in doc['rebase']['states'][kind]
        assert not set(NEW_SEGMENTS) & set(segs(doc, kind))
    assert all(r['design_area_delta'] is None and r['unit_size_delta'] is None and 'lambda_s1a' not in r
               for r in doc['rebase']['items'] + doc['rebase']['cno'])
    assert old_view(doc['rebase']) == old_view(base['rebase'])
    assert doc['rebase']['status'] == 'available' and doc['rebase']['check']['status'] == 'passed'


def test_new_keys_are_the_only_change_and_runs_are_reproducible(tmp_path):
    ref, tgt = two_sides()
    kwargs = dict(b=OTHER, ref_cno={CNO_NAME: CNO_CSV}, tgt_cno={CNO_NAME: CNO_CSV})
    code, plain, out_plain = go(sub(tmp_path, 'p'), ref, tgt, **kwargs)
    code2, doc, out = go(sub(tmp_path, 'u'), ref, tgt, a=dict(unit=[10, 20]), **dict(kwargs, b=dict(OTHER, unit=[30, 10])))
    assert code == code2 == 0
    assert old_view(doc['rebase']) == old_view(plain['rebase'])
    for name in ('compare2_summary.csv', 'compare2_layers.csv', 'compare2_conditions.csv', 'compare2_cno.csv'):
        assert (out / name).read_bytes().replace(str(tmp_path / 'u').encode(), b'T') == \
            (out_plain / name).read_bytes().replace(str(tmp_path / 'p').encode(), b'T'), name
    texts = []
    for name in ('one', 'two'):
        folder = sub(tmp_path, name)
        code3, _doc, out3 = go(folder, ref, tgt, a=dict(unit=[10, 20]), b=dict(OTHER, unit=[30, 10]))
        texts.append(re.sub(r'[0-9a-f]{64}', 'H', (out3 / 'compare2.json').read_text(encoding='utf-8').replace(str(folder), 'T')))
    assert texts[0] == texts[1]
