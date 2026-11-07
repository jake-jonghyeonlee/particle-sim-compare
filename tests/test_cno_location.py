"""compare2/T23: the location delta-vi of compare2-report includes CNO pairs (pixel family, x pi/4).

Hand-made folders (formula, one-sided pair, diameter set, identity, side-form mismatch, CNO-free bytes) and one real chain:
import --include-cno -> compare2 -> candidates-change -> compare-windows -> compare2-report with the link.
"""
import json
import math
from types import SimpleNamespace

import pytest
import yaml

import test_change_candidates as tcc
import test_cno_round6 as r6
from simcompare import compare2_link
from simcompare.cli import main
from simcompare.compare2_link import ROLES, build_link, compute_locations
from simcompare.compare2_report import build_report
from test_compare2 import RES, write_density
from test_compare2_link import (case, compare2_doc, loc_doc, named_row, side_doc, write_candidates, write_json,
                                write_windows)

PI4 = math.pi / 4
CNO_ID = 'cno:CNO@gcnt'
CNO_KEY = 'lateral_open|CNO|gcnt|gcnt'


# ---- hand-made folders ------------------------------------------------------------------------------------------------------

def cno_side(product, center, *, endpoints=True, failure=True, layer='gcnt'):
    side = side_doc(product, layer, None, center)
    if endpoints:
        side['endpoints'] = [{'electrode_id': 'CNO', 'role': None, 'actual_layer': layer}]
    if failure:
        side['failure_type'] = 'lateral_open'
    return side


def cno_cand(tmp, pairs, *, present_in='both'):
    """Candidates folder whose ROI 'roi:R3' holds one CNO case; pairs = [(pair_id, side a, side b, present_in)]."""
    def mutate(payload):
        query = payload['directions'][0]['queries'][1]
        query['contributing_cases'] = [case(CNO_KEY, present_in=present_in)]
        query['layers'] = ['gcnt']
    folder = write_candidates(tmp / 'cand', mutate=mutate)
    corr = {'schema_version': 'roi_correspondence_v1',
            'pairs': [{'pair_id': pid, 'a': a, 'b': b, 'source': {'kind': 'manual'}, 'status': 'ready', 'reasons': []}
                      for pid, a, b, _p in pairs]}
    sidecar = {'schema_version': 'direct_pairs_sidecar_v1',
               'pairs': {pid: {'region_id': 'R3', 'change_kind': 'appeared', 'residual_um2': 1.5, 'area_reference_um2': 2.0,
                               'area_target_um2': 3.5, 'present_in': p, 'layer_changed': False, 'flags': [], 'score_um2': 10.0}
                         for pid, a, b, p in pairs}}
    write_json(folder / 'direct/roi_correspondence.json', corr)
    write_json(folder / 'direct/direct_pairs_sidecar.json', sidecar)
    return folder


def cno_doc(rho_ref=None, rho_tgt=None, extra_sizes=()):
    doc = loc_doc()
    rho_ref = rho_ref or {3: 0.2, 5: 0.3}
    rho_tgt = rho_tgt or {3: 0.4, 5: 0.5}
    for role, rho in (('reference', rho_ref), ('target', rho_tgt)):
        doc['sides'][role]['cno'] = [{'case_name': 'lateralopen-CNO-gcnt', 'electrode_id': 'CNO', 'density_layer': 'gcnt',
                                      'lambda': 1.0, 'by_size': [{'diameter_um': float(d), 'density_pixel': v, 'area_csv_um2': 1.0,
                                                                  'lambda': 1.0} for d, v in sorted(rho.items())]}]
    doc['inputs']['constants'] = {'cno_factor': PI4}
    return doc


def cno_locations(tmp, pairs, rows, doc=None, **kw):
    cand = cno_cand(tmp, pairs, **kw)
    windows = write_windows(tmp / 'win', cand / 'direct/roi_correspondence.json', rows)
    doc = doc or cno_doc()
    link = build_link(doc, candidates_dir=cand, windows_dir=windows, map_window_um=10.0)
    return link, compute_locations(link, doc)


def pair_of(result, pid):
    return next(p for p in result['pairs'] if p['pair_id'] == pid)


TWO_SIDED = [('c1', cno_side('PR', [10.0, 20.0]), cno_side('PT', [11.0, 21.0]), 'both')]


def test_a_hand_computed_two_sided_cno_pair_with_two_diameters(tmp_path):
    rows = [named_row('c1', 10, 3, 1.0, 2.0), named_row('c1', 10, 5, 3.0, 4.0)]
    link, result = cno_locations(tmp_path, TWO_SIDED, rows)
    assert [w for w in link['warnings'] if w['code'] in ('case_key_unparsed', 'unmapped_condition')] == []
    p = pair_of(result, 'c1')
    want = PI4 * ((0.4 * 2 * 7.0 + 0.5 * 4 * 7.0) - (0.2 * 1 * 3.0 + 0.3 * 3 * 3.0))
    assert abs(p['delta_lambda']['pixel'] - want) < 1e-12
    assert abs(p['delta_vi']['pixel'] - 0.25 * want) < 1e-12
    assert p['delta_lambda']['line'] == 0.0 and p['delta_vi']['line'] == 0.0 and p['reasons'] == []
    roi = next(r for r in result['rois'] if r['roi_key'] == 'roi:R3')
    assert roi['condition_ids'] == [CNO_ID] and roi['class'] != 'uncomputable'
    assert abs(roi['delta_vi']['pixel'] - 0.25 * want) < 1e-12 and roi['delta_vi']['line'] == 0.0
    assert link['pairs'][0]['conditions'] == {'reference': {'condition_id': CNO_ID, 'reason': None},
                                              'target': {'condition_id': CNO_ID, 'reason': None}}
    assert 'CNO' in result['approximation_note'] and 'image area' in result['approximation_note']


def test_b_one_sided_appeared_cno_pair_has_zero_reference_area(tmp_path):
    pairs = [('c1', side_doc('PR', 'gcnt', None, [10.0, 20.0]), cno_side('PT', [11.0, 21.0], endpoints=False), 'target')]
    rows = [named_row('c1', 10, 3, 0.0, 2.0, metric='union'), named_row('c1', 10, 5, 0.0, 4.0, metric='union')]
    link, result = cno_locations(tmp_path, pairs, rows, present_in='target')
    p = pair_of(result, 'c1')
    want = PI4 * (0.4 * 2 * 7.0 + 0.5 * 4 * 7.0)
    assert abs(p['delta_lambda']['pixel'] - want) < 1e-12 and abs(p['delta_vi']['pixel'] - 0.25 * want) < 1e-12
    assert 'one_sided_pair_no_endpoints' not in p['reasons'] and p['reasons'] == []
    conditions = link['pairs'][0]['conditions']
    assert conditions['reference']['reason'] == 'cno_absent_on_side' and conditions['target']['condition_id'] == CNO_ID


def test_b2_disappeared_cno_pair_subtracts_the_reference_term(tmp_path):
    pairs = [('c1', cno_side('PR', [10.0, 20.0], endpoints=False), side_doc('PT', 'gcnt', None, [11.0, 21.0]), 'reference')]
    rows = [named_row('c1', 10, 3, 2.0, 0.0, metric='union'), named_row('c1', 10, 5, 4.0, 0.0, metric='union')]
    _link, result = cno_locations(tmp_path, pairs, rows, present_in='reference')
    want = -PI4 * (0.2 * 2 * 3.0 + 0.3 * 4 * 3.0)
    assert abs(pair_of(result, 'c1')['delta_lambda']['pixel'] - want) < 1e-12


def test_d_diameter_set_has_the_cno_sizes_and_the_filter_wins():
    doc = cno_doc(rho_ref={3: 0.2, 5: 0.3, 7: 0.1}, rho_tgt={3: 0.4, 5: 0.5, 7: 0.1})
    assert compare2_link._location_diameters(doc) == ['3', '5', '7']      # short layers 3, 5 and the CNO size 7
    doc['inputs']['diameter_filter'] = {'diameters_um': [5]}
    assert compare2_link._location_diameters(doc) == ['5']
    plain = loc_doc()
    assert compare2_link._location_diameters(plain) == ['3', '5']


def test_e_roi_sum_plus_residual_is_the_total(tmp_path):
    doc = cno_doc()
    doc['delta']['indicators']['delta_vi_pixel'] = 9.0
    rows = [named_row('c1', 10, 3, 1.0, 2.0), named_row('c1', 10, 5, 3.0, 4.0)]
    _link, result = cno_locations(tmp_path, TWO_SIDED, rows, doc)
    explained = result['explained']['pixel']
    assert abs(explained['location_sum'] + explained['residual_unexplained_by_location_sum'] - 9.0) < 1e-12
    p = pair_of(result, 'c1')
    assert abs(explained['location_sum'] - p['delta_vi']['pixel']) < 1e-12        # one ROI, replica factor 1


def test_replica_factor_applies_once_to_the_roi_sum(tmp_path):
    def mutate(payload):
        payload['directions'][0]['queries'][1]['unit_fold'] = {'n_replicas': 2, 'replicas': []}
    rows = [named_row('c1', 10, 3, 1.0, 2.0), named_row('c1', 10, 5, 3.0, 4.0)]
    cand = cno_cand(tmp_path, TWO_SIDED)
    payload = json.loads((cand / 'candidate_queries_v1.json').read_text(encoding='utf-8'))
    mutate(payload)
    write_json(cand / 'candidate_queries_v1.json', payload)
    windows = write_windows(tmp_path / 'win', cand / 'direct/roi_correspondence.json', rows)
    doc = cno_doc()
    result = compute_locations(build_link(doc, candidates_dir=cand, windows_dir=windows, map_window_um=10.0), doc)
    roi = next(r for r in result['rois'] if r['roi_key'] == 'roi:R3')
    assert roi['replica_factor'] == 3 and abs(roi['delta_vi']['pixel'] - 3 * pair_of(result, 'c1')['delta_vi']['pixel']) < 1e-12


@pytest.mark.parametrize('a_form,b_form', [
    (dict(endpoints=False), None),          # marked CNO on the reference, a short (two-endpoint) form on the target
    (None, dict(endpoints=False))])
def test_f_side_form_mismatch_is_null_with_a_reason(tmp_path, a_form, b_form):
    short_a = side_doc('PR', 'gcnt', ['A', 'B'], [10.0, 20.0])
    short_b = side_doc('PT', 'gcnt', ['A', 'B'], [11.0, 21.0])
    a = cno_side('PR', [10.0, 20.0], **a_form) if a_form is not None else short_a
    b = cno_side('PT', [11.0, 21.0], **b_form) if b_form is not None else short_b
    rows = [named_row('c1', 10, 3, 0.0, 2.0, metric='union'), named_row('c1', 10, 5, 0.0, 4.0, metric='union')]
    _link, result = cno_locations(tmp_path, [('c1', a, b, 'both')], rows)
    p = pair_of(result, 'c1')
    assert p['reasons'] == ['cno_pair_side_form_mismatch'] and p['delta_vi'] == {'line': None, 'pixel': None}


def test_marked_side_with_a_failure_type_less_absent_side_is_a_legal_one_sided_pair(tmp_path):
    # the absent side carries neither endpoints nor failure_type: it is not a mismatch
    pairs = [('c1', side_doc('PR', 'gcnt', None, [10.0, 20.0]), cno_side('PT', [11.0, 21.0], endpoints=False), 'target')]
    rows = [named_row('c1', 10, 3, 0.0, 1.0, metric='union'), named_row('c1', 10, 5, 0.0, 1.0, metric='union')]
    _link, result = cno_locations(tmp_path, pairs, rows, present_in='target')
    assert pair_of(result, 'c1')['delta_vi']['pixel'] is not None


def test_missing_cno_row_gives_unmapped_condition(tmp_path):
    doc = cno_doc()
    for role in ROLES:
        doc['sides'][role]['cno'] = []
    rows = [named_row('c1', 10, 3, 1.0, 2.0), named_row('c1', 10, 5, 3.0, 4.0)]
    link, result = cno_locations(tmp_path, TWO_SIDED, rows, doc)
    assert any(w['code'] == 'unmapped_condition' for w in link['warnings'])
    assert pair_of(result, 'c1')['delta_vi']['pixel'] is None


def test_zero_area_term_is_zero_before_a_missing_density(tmp_path):
    doc = cno_doc()
    doc['sides']['target']['cno'][0]['by_size'][0]['density_pixel'] = None
    rows = [named_row('c1', 10, 3, 1.0, 0.0), named_row('c1', 10, 5, 3.0, 4.0)]
    _link, result = cno_locations(tmp_path, TWO_SIDED, rows, doc)
    p = pair_of(result, 'c1')
    want = PI4 * (0.5 * 4 * 7.0 - (0.2 * 1 * 3.0 + 0.3 * 3 * 3.0))
    assert abs(p['delta_lambda']['pixel'] - want) < 1e-12 and 'density_missing_zero_area' in p['reasons']


def test_cno_free_input_keeps_the_old_note_and_has_no_cno_keys(tmp_path):
    doc = loc_doc()
    cand = write_candidates(tmp_path / 'cand')
    windows = write_windows(tmp_path / 'win', cand / 'direct/roi_correspondence.json', [named_row('direct_1', 10, 3, 1.0, 2.0)])
    link = build_link(doc, candidates_dir=cand, windows_dir=windows, map_window_um=10.0)
    assert not any('cno_case' in p for p in link['pairs'])
    assert compute_locations(link, doc)['approximation_note'] == compare2_link.APPROXIMATION_NOTE


def test_four_part_case_key_parses_without_a_warning():
    parsed = compare2_link._parse_case_key(CNO_KEY)
    assert parsed == {'failure': 'lateral_open', 'electrodes': ['CNO'], 'endpoint_layers': ['gcnt'], 'analysis_layer': 'gcnt'}
    assert compare2_link._parse_case_key('lateral_open|CNO|gcnt|other')['electrodes'] is None
    assert compare2_link._parse_case_key('lateral_short|A|L1|B')['electrodes'] is None


# ---- (c) the real chain -----------------------------------------------------------------------------------------------------

@pytest.fixture(scope='module')
def chain(tmp_path_factory):
    base = tmp_path_factory.mktemp('cno_loc')
    world = r6.make_world(base, layer=tcc.CNO_LAYER)
    corr = r6.candidates(world, base / 'cand')
    windows = r6.windows(world, corr, base / 'win')
    density = base / 'density'
    write_density(density, 'ga', {'P1': {5: 1.0}, 'P2': {5: 1.0}}, {'P1': {5: 3.0}, 'P2': {5: 1.0}, tcc.CNO_LAYER: {5: 2.0}})
    write_density(density, 'gb', {'P1': {5: 1.0}, 'P2': {5: 1.0}}, {'P1': {5: 3.0}, 'P2': {5: 1.0}, tcc.CNO_LAYER: {5: 4.0}})
    cfg = {'schema_version': 'compare2_config_v1', 'numeric_config': str(world.config), 'output_dir': str(base / 'c2'),
           'density_dir': str(density), 'lambda_diameters_um': [5], 'report': {'map_window_um': 6, 'diameter_um': 5},
           'model_groups': {g: {'glass_area': {'value': 50.0, 'unit': 'um2'}, 'weight_line': 1.0, 'weight_pixel': 1.0}
                            for g in ('ga', 'gb')},
           'products': {'reference': {'model_group': 'ga', 'resolution': RES, 'hotspot_root': str(base / 'hotspot_reference')},
                        'target': {'model_group': 'gb', 'resolution': RES, 'hotspot_root': str(base / 'hotspot_target')}}}
    config = base / 'compare2.yaml'
    config.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    assert main(['compare2', '--config', str(config)]) in (0, 3)
    doc = json.loads((base / 'c2/compare2.json').read_text(encoding='utf-8'))
    return SimpleNamespace(base=base, world=world, cand=base / 'cand', win=windows, corr=corr, config=config, c2=base / 'c2',
                           doc=doc)


def test_c_compare2_records_the_cno_density_per_diameter(chain):
    for role, rho in (('reference', 2.0), ('target', 4.0)):
        (row,) = chain.doc['sides'][role]['cno']
        assert row['density_layer'] == tcc.CNO_LAYER and row['lambda'] is not None
        (size,) = row['by_size']
        assert size['diameter_um'] == 5.0 and size['density_pixel'] == rho and size['area_csv_um2'] > 0
        assert abs(sum(s['lambda'] for s in row['by_size']) - row['lambda']) < 1e-12


def test_c_real_chain_computes_the_cno_roi_and_the_report_shows_the_chip(chain, tmp_path):
    files, record = build_report(chain.c2, chain.config, tmp_path / 'out', candidates_dir=chain.cand, windows_dir=chain.win)
    html = dict(files)['report.html']
    link = build_link(chain.doc, candidates_dir=chain.cand, windows_dir=chain.win, map_window_um=6.0)
    loc = compute_locations(link, chain.doc)
    assert not [w for w in link['warnings'] if w['code'] == 'case_key_unparsed'], link['warnings']
    assert not [w for w in record['warnings'] if w['code'] == 'case_key_unparsed']
    cno_rois = [r for r in loc['rois'] if any(c['failure'] == 'lateral_open' for c in r['design_change']['contributing_cases'])]
    assert cno_rois and all(r['class'] != 'uncomputable' and r['delta_vi']['pixel'] is not None for r in cno_rois)
    assert all(r['delta_vi']['line'] == 0.0 for r in cno_rois)
    expected = {key: loc[key] for key in ('counts', 'top_worse', 'top_improved', 'explained', 'diameters_um', 'approximation_note')}
    assert record['locations'] == json.loads(json.dumps(expected))
    assert '<span class="chip">CNO</span>' in html and 'CNO(GCNT) lateral open' in html
    assert 'CNO 는 위치 창 안의 이미지 면적으로 근사' in html
    # the pair value equals the formula on the round-6 rows
    rows = r6.csv_rows(chain.win)
    cno_pairs = [p for p in link['pairs'] if p.get('cno_case')]
    assert cno_pairs
    slope = chain.doc['delta']['slopes']['pixel']['value']
    f_ref, f_tgt = chain.doc['products']['reference']['F_pixel'], chain.doc['products']['target']['F_pixel']
    for pair in cno_pairs:
        row = r6.named_row(rows, pair['pair_id']) if pair['named_pair'] and any(
            r['metric'] == 'named_pair' and r['pair_id'] == pair['pair_id'] for r in rows) else r6.metric_row(
                rows, pair['pair_id'], 'union')
        a, b = float(row['a_value_um2']), float(row['b_value_um2'])
        want = PI4 * (4.0 * b * f_tgt - 2.0 * a * f_ref)
        got = pair_of(loc, pair['pair_id'])
        assert abs(got['delta_lambda']['pixel'] - want) < 1e-9 and abs(got['delta_vi']['pixel'] - slope * want) < 1e-9
        assert got['delta_vi']['line'] == 0.0


def test_c_one_sided_cno_in_the_real_chain_is_computed(tmp_path):
    # the CNO layer carries the short layer's name (L1): a CNO layer absent on one side would be blocked by round 4/6 as before
    world = r6.make_world(tmp_path, layer='L1', reference_cno=None)
    corr = r6.candidates(world, tmp_path / 'cand')
    windows = r6.windows(world, corr, tmp_path / 'win')
    density = tmp_path / 'density'
    write_density(density, 'ga', {'P1': {5: 1.0}}, {'P1': {5: 3.0}, 'L1': {5: 2.0}})
    cfg = {'schema_version': 'compare2_config_v1', 'numeric_config': str(world.config), 'output_dir': str(tmp_path / 'c2'),
           'density_dir': str(density), 'lambda_diameters_um': [5], 'report': {'map_window_um': 6, 'diameter_um': 5},
           'model_groups': {'ga': {'glass_area': {'value': 50.0, 'unit': 'um2'}, 'weight_line': 1.0, 'weight_pixel': 1.0}},
           'products': {side: {'model_group': 'ga', 'resolution': RES, 'hotspot_root': str(tmp_path / f'hotspot_{side}')}
                        for side in ROLES}}
    (tmp_path / 'compare2.yaml').write_text(yaml.safe_dump(cfg), encoding='utf-8')
    assert main(['compare2', '--config', str(tmp_path / 'compare2.yaml')]) in (0, 3)
    doc = json.loads((tmp_path / 'c2/compare2.json').read_text(encoding='utf-8'))
    link = build_link(doc, candidates_dir=tmp_path / 'cand', windows_dir=windows, map_window_um=6.0)
    loc = compute_locations(link, doc)
    cno_pairs = [p for p in link['pairs'] if p.get('cno_case')]
    assert cno_pairs
    for pair in cno_pairs:
        got = pair_of(loc, pair['pair_id'])
        assert got['reasons'] == [] and got['delta_vi']['pixel'] is not None, got
        assert pair['conditions']['reference']['reason'] == 'cno_absent_on_side'
