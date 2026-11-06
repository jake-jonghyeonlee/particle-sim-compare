"""compare2/V2: compare2_link loads and checks rounds 4/5/6 and DL1 search results against a compare2 document.

Two kinds of input: (a) one real chain (candidates-change + compare2 + compare-windows on the test_change_candidates world),
(b) small hand-made folders so every check and ambiguity can be reached without the heavy producers.
"""
import copy
import csv
import hashlib
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simcompare import compare2_link
from simcompare.cli import main
from simcompare.compare2_link import build_link
from simcompare.numeric_schema import NumericContractError
from test_change_candidates import PRODUCTS, run_change, world  # noqa: F401  (world is a module-scoped fixture there)
from test_compare2 import RES, write_density

REF_HASH, TGT_HASH = 'hash-reference', 'hash-target'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2), encoding='utf-8')
    return path


def dump(link):
    return json.dumps(link, sort_keys=True, ensure_ascii=False, allow_nan=False)


def code_of(exc_info):
    return exc_info.value.code


# ---- hand-made folders ------------------------------------------------------------------------------------------------------

def compare2_doc(ref_extra=(), tgt_extra=(), ref_hash=REF_HASH, tgt_hash=TGT_HASH):
    def side(extra):
        conditions = [{'condition_id': 'AB@L1', 'electrodes': ['A', 'B'], 'layer': 'L1'},
                      {'condition_id': 'AC@L1', 'electrodes': ['A', 'C'], 'layer': 'L1'},
                      {'condition_id': 'DE@L2', 'electrodes': ['D', 'E'], 'layer': 'L2'}]
        return {'conditions': conditions + list(extra)}
    return {'schema_version': 'compare2_v1',
            'inputs': {'manifests': {'reference': {'input_hash': ref_hash}, 'target': {'input_hash': tgt_hash}}},
            'sides': {'reference': side(ref_extra), 'target': side(tgt_extra)}}


def case(key, kind='appeared', **extra):
    return {'case_key': key, 'change_kind': kind, 'residual_um2': 1.5, 'area_reference_um2': 2.0, 'area_target_um2': 3.5,
            'flags': [], 'present_in': 'both', 'layer_changed': False, **extra}


def query(direction, region, merged, score, roi_ids, cases=(), one_sided=()):
    return {'query_id': f'{direction}:{region}', 'region_id': region, 'merged_from': list(merged), 'score_um2': score,
            'layers': ['L1'], 'status': 'ready', 'center_xy_um': [1.0, 2.0], 'window_um': 10.0, 'roi_ids': list(roi_ids),
            'contributing_cases': list(cases), 'one_sided_cases': list(one_sided)}


def side_doc(product, layer, electrodes, center):
    return {'product_id': product, 'design_version': 'D', 'frame_id': 'F', 'center_xy_um': center, 'analysis_layer': layer,
            'endpoints': None if electrodes is None else [{'electrode_id': e, 'role': 'gate', 'actual_layer': layer} for e in electrodes]}


def corr_pair(pid, a_electrodes=('A', 'B'), b_electrodes=('B', 'A'), layer='L1', status='ready'):
    return {'pair_id': pid, 'a': side_doc('PR', layer, a_electrodes, [10.0, 20.0]),
            'b': side_doc('PT', layer, b_electrodes, [11.0, 21.0]), 'source': {'kind': 'manual'}, 'status': status, 'reasons': []}


def write_candidates(folder, *, direct_pairs=None, ref_hash=REF_HASH, tgt_hash=TGT_HASH, mutate=None):
    """Two ROIs: ROI1 = regions R1+R2 (score 10), ROI2 = region R3 (score 20); the target_to_reference queries repeat them."""
    folder = Path(folder)
    payload = {'schema_version': 'candidate_queries_v1',
               'inputs': {'reference': {'dataset_input_hash': ref_hash, 'product_ids': ['PR']},
                          'target': {'dataset_input_hash': tgt_hash, 'product_ids': ['PT']}},
               'directions': [
                   {'direction': 'reference_to_target', 'queries': [
                       query('reference_to_target', 'R1', ['R1', 'R2'], 10.0, ['q1'],
                             cases=[case('lateral_short|A|L1|B|L1'), case('lateral_short|C|L1|A|L1|L1', 'shifted')],
                             one_sided=[case('lateral_short|C|L1|A|L1|L1')]),
                       query('reference_to_target', 'R3', ['R3'], 20.0, ['q3'], cases=[case('lateral_short|D|L2|E|L2')])]},
                   {'direction': 'target_to_reference', 'queries': [
                       query('target_to_reference', 'R1', ['R1'], 10.0, ['t1']),
                       query('target_to_reference', 'R3', ['R3'], 20.0, ['t3'])]}]}
    if mutate:
        mutate(payload)
    write_json(folder / 'candidate_queries_v1.json', payload)
    pairs = direct_pairs if direct_pairs is not None else [('direct_1', 'R2', ('A', 'B'), ('B', 'A')), ('direct_2', 'R3', ('D', 'E'), ('E', 'D'))]
    corr = {'schema_version': 'roi_correspondence_v1',
            'pairs': [corr_pair(pid, a, b, layer='L2' if a == ('D', 'E') else 'L1') for pid, region, a, b in pairs]}
    sidecar = {'schema_version': 'direct_pairs_sidecar_v1',
               'pairs': {pid: {'region_id': region, 'change_kind': 'appeared', 'residual_um2': 1.5, 'area_reference_um2': 2.0,
                               'area_target_um2': 3.5, 'present_in': 'both', 'layer_changed': False, 'flags': [], 'score_um2': 10.0}
                         for pid, region, a, b in pairs}}
    write_json(folder / 'direct/roi_correspondence.json', corr)
    write_json(folder / 'direct/direct_pairs_sidecar.json', sidecar)
    return folder


def write_confirm(folder, candidates, *, pairs=None, recorded_sha=None):
    """c1: target_to_reference query t1 -> ROI1; c2: names q1 and q3 -> both ROIs (multi_roi, ROI2 wins on score); c3: no endpoints."""
    folder = Path(folder)
    pairs = pairs if pairs is not None else {
        'c1': ([{'direction': 'target_to_reference', 'query_roi_id': 't1'}], ('A', 'B')),
        'c2': ([{'direction': 'reference_to_target', 'query_roi_id': 'q1'},
                {'direction': 'reference_to_target', 'query_roi_id': 'q3'}], ('A', 'C')),
        'c3': ([{'direction': 'reference_to_target', 'query_roi_id': 'q3'}], None)}
    corr = {'schema_version': 'roi_correspondence_v1',
            'pairs': [{**corr_pair(pid, a_electrodes=e, b_electrodes=e), 'status': 'ready'} for pid, (cands, e) in pairs.items()]}
    sidecar = {'schema_version': 'confirmed_pairs_sidecar_v1',
               'inputs': {'candidates_payload_sha256': recorded_sha or sha(Path(candidates) / 'candidate_queries_v1.json')},
               'pairs': {pid: {'candidates': cands, 'merged_with': []} for pid, (cands, e) in pairs.items()}}
    write_json(folder / 'roi_correspondence.json', corr)
    write_json(folder / 'confirmed_pairs_sidecar.json', sidecar)
    return folder


ROW_FIELDS = ['pair_id', 'window_size_um', 'diameter_um', 'kind', 'side_um', 'metric', 'a_value_um2', 'a_partial_value_um2',
              'a_status', 'a_fraction_of_window', 'b_value_um2', 'b_partial_value_um2', 'b_status', 'b_fraction_of_window',
              'delta_um2', 'delta_fraction', 'delta_status', 'delta_reasons']


def named_row(pid, window, diameter, a=1.0, b=2.0, metric='named_pair'):
    return {'pair_id': pid, 'window_size_um': window, 'diameter_um': diameter, 'kind': 'window', 'side_um': window,
            'metric': metric, 'a_value_um2': a, 'a_status': 'available', 'b_value_um2': b, 'b_status': 'available'}


def write_windows(folder, correspondence_file, rows, *, ref_hash=REF_HASH, tgt_hash=TGT_HASH, correspondence_sha=None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / 'local_delta.json', {
        'schema_version': 'local_delta_v1', 'products': {'a': {'product_id': 'PR'}, 'b': {'product_id': 'PT'}},
        'provenance': {'reference_input_hash': ref_hash, 'target_input_hash': tgt_hash,
                       'correspondence_sha256': correspondence_sha or sha(correspondence_file)}})
    with (folder / 'local_delta_rows.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=ROW_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return folder


@pytest.fixture
def hand(tmp_path):
    cand = write_candidates(tmp_path / 'cand')
    conf = write_confirm(tmp_path / 'conf', cand)
    return SimpleNamespace(tmp=tmp_path, cand=cand, conf=conf, doc=compare2_doc())


def link_of(hand, **kw):
    kw.setdefault('map_window_um', 10.0)
    return build_link(hand.doc, **kw)


# ---- (a) real producers -----------------------------------------------------------------------------------------------------

@pytest.fixture(scope='module')
def real_chain(world, tmp_path_factory):    # noqa: F811
    base = tmp_path_factory.mktemp('link_real')
    cand = base / 'cand'
    code = run_change(world, cand, top_k=1)
    assert code in (0, 3)
    density = base / 'density'
    write_density(density, 'grp', {'P1': {5: 1.0}}, {'P1': {5: 1.0}})
    cfg = {'schema_version': 'compare2_config_v1', 'numeric_config': str(world.config), 'output_dir': str(base / 'c2'),
           'density_dir': str(density),
           'model_groups': {'grp': {'glass_area': {'value': 50.0, 'unit': 'um2'}, 'weight_line': 1.0, 'weight_pixel': 1.0}},
           'products': {side: {'model_group': 'grp', 'resolution': RES, 'hotspot_root': str(world.base / f'hotspot_{side}')}
                        for side in ('reference', 'target')}}
    (base / 'compare2.yaml').write_text(yaml.safe_dump(cfg), encoding='utf-8')
    assert main(['compare2', '--config', str(base / 'compare2.yaml')]) in (0, 3)
    windows = base / 'win'
    assert main(['compare-windows', '--config', str(world.config), '--correspondence', str(cand / 'direct/roi_correspondence.json'),
                 '--window-sizes', '10', '20', '--out', str(windows)]) in (0, 3)
    keep = os.environ.get('C2_LINK_KEEP')       # optional: leave the producer outputs for a run under another interpreter
    if keep:
        shutil.copytree(str(cand), str(Path(keep) / 'cand'))
        shutil.copytree(str(windows), str(Path(keep) / 'win'))
        shutil.copyfile(str(base / 'c2/compare2.json'), str(Path(keep) / 'compare2.json'))
    return SimpleNamespace(cand=cand, windows=windows, doc=json.loads((base / 'c2/compare2.json').read_text(encoding='utf-8')),
                           base=base)


def test_real_producer_outputs_link_without_error(real_chain):
    link = build_link(real_chain.doc, candidates_dir=real_chain.cand, windows_dir=real_chain.windows, map_window_um=10.0)
    assert link['schema_version'] == 'compare2_link_v1'
    assert len(link['rois']) == 1 and len(link['pairs']) == 2
    roi = link['rois'][0]
    assert roi['roi_key'].startswith('roi:') and roi['contributing_cases']
    parsed = {tuple(c['electrodes']) for c in roi['contributing_cases']}
    assert parsed == {('A', 'B'), ('A', 'C')}
    for pair in link['pairs']:
        assert pair['path'] == 'direct' and pair['roi_key'] == roi['roi_key']
        assert pair['named_pair'], 'named_pair rows of compare-windows are attached'
        for row in pair['named_pair'].values():
            assert set(row) == {'a_value_um2', 'b_value_um2', 'a_status', 'b_status'}
        assert {role: pair['conditions'][role]['condition_id'] is not None for role in ('reference', 'target')} == {
            'reference': True, 'target': True}
    assert not [w for w in link['warnings'] if w['code'].startswith(('unmapped', 'ambiguous', 'multi_roi', 'unassigned'))]
    assert PRODUCTS['reference'] == link['pairs'][0]['a']['product_id']


# ---- (b) nothing optional ---------------------------------------------------------------------------------------------------

def test_no_optional_input_gives_none(hand):
    assert link_of(hand) is None
    assert link_of(hand, dl1_search=[]) is None


def test_confirm_needs_candidates_and_windows_need_a_correspondence(hand):
    with pytest.raises(NumericContractError) as exc:
        link_of(hand, confirm_dir=hand.conf)
    assert code_of(exc) == 'compare2_link_input_missing'
    windows = write_windows(hand.tmp / 'win', hand.cand / 'direct/roi_correspondence.json', [named_row('direct_1', 10, 5)])
    with pytest.raises(NumericContractError) as exc:
        link_of(hand, windows_dir=windows)
    assert code_of(exc) == 'compare2_link_input_missing'


def test_missing_file_and_wrong_schema(hand):
    (hand.cand / 'direct/direct_pairs_sidecar.json').unlink()
    with pytest.raises(NumericContractError) as exc:
        link_of(hand, candidates_dir=hand.cand)
    assert code_of(exc) == 'compare2_link_input_missing'
    write_candidates(hand.cand, mutate=lambda p: p.update(schema_version='candidate_queries_v0'))
    with pytest.raises(NumericContractError) as exc:
        link_of(hand, candidates_dir=hand.cand)
    assert code_of(exc) == 'compare2_link_schema_mismatch'


# ---- (c) input checks -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('role', ['reference', 'target'])
def test_candidates_dataset_hash_must_match_compare2(hand, role):
    hand.doc['inputs']['manifests'][role]['input_hash'] = 'other'
    with pytest.raises(NumericContractError) as exc:
        link_of(hand, candidates_dir=hand.cand)
    assert code_of(exc) == 'compare2_link_input_mismatch' and 'candidates_dataset_input_hash' in str(exc.value) and role in str(exc.value)


@pytest.mark.parametrize('field', ['ref_hash', 'tgt_hash'])
def test_windows_input_hashes_must_match_compare2(hand, field):
    corr = hand.cand / 'direct/roi_correspondence.json'
    windows = write_windows(hand.tmp / 'win', corr, [named_row('direct_1', 10, 5)], **{field: 'other'})
    with pytest.raises(NumericContractError) as exc:
        link_of(hand, candidates_dir=hand.cand, windows_dir=windows)
    assert code_of(exc) == 'compare2_link_input_mismatch' and 'windows_input_hash' in str(exc.value)


def test_confirm_must_record_the_candidates_file_sha(hand):
    write_confirm(hand.conf, hand.cand, recorded_sha='0' * 64)
    with pytest.raises(NumericContractError) as exc:
        link_of(hand, candidates_dir=hand.cand, confirm_dir=hand.conf)
    assert code_of(exc) == 'compare2_link_input_mismatch' and 'confirm_candidates_payload_sha256' in str(exc.value)


def test_windows_must_hash_the_correspondence_file_actually_used(hand):
    direct = hand.cand / 'direct/roi_correspondence.json'
    windows = write_windows(hand.tmp / 'win', direct, [named_row('direct_1', 10, 5)])
    assert link_of(hand, candidates_dir=hand.cand, windows_dir=windows)['pairs']        # direct file: accepted
    with pytest.raises(NumericContractError) as exc:                                    # confirm given: the confirm file is the one
        link_of(hand, candidates_dir=hand.cand, confirm_dir=hand.conf, windows_dir=windows)
    assert code_of(exc) == 'compare2_link_input_mismatch' and 'windows_correspondence_sha256' in str(exc.value)
    windows = write_windows(hand.tmp / 'win2', hand.conf / 'roi_correspondence.json', [named_row('c1', 10, 5)])
    assert link_of(hand, candidates_dir=hand.cand, confirm_dir=hand.conf, windows_dir=windows)


def test_pair_product_ids_must_belong_to_the_compare2_products(hand):
    hand.doc['products'] = {'reference': {'product_id': 'OTHER'}, 'target': {'product_id': 'PT'}}
    with pytest.raises(NumericContractError) as exc:
        link_of(hand, candidates_dir=hand.cand)
    assert code_of(exc) == 'compare2_link_input_mismatch' and 'pair_product_id' in str(exc.value)


def test_window_size_without_a_row_is_an_error(hand):
    windows = write_windows(hand.tmp / 'win', hand.cand / 'direct/roi_correspondence.json', [named_row('direct_1', 20, 5)])
    with pytest.raises(NumericContractError) as exc:
        link_of(hand, candidates_dir=hand.cand, windows_dir=windows)
    assert code_of(exc) == 'compare2_link_window_missing'


# ---- (d) named_pair rows ----------------------------------------------------------------------------------------------------

def test_duplicate_named_pair_row_is_refused_and_other_windows_are_ignored(hand):
    corr = hand.cand / 'direct/roi_correspondence.json'
    rows = [named_row('direct_1', 10, 5), named_row('direct_1', 10, 5.0)]
    with pytest.raises(NumericContractError) as exc:
        link_of(hand, candidates_dir=hand.cand, windows_dir=write_windows(hand.tmp / 'w1', corr, rows))
    assert code_of(exc) == 'compare2_report_duplicate_row'
    rows = [named_row('direct_1', 10, 5, a=1.0), named_row('direct_1', 20, 5, a=9.0), named_row('direct_1', 10, 3, a=2.0),
            named_row('direct_1', 10, 5, metric='union'), named_row('unknown_pair', 10, 5)]
    link = link_of(hand, candidates_dir=hand.cand, windows_dir=write_windows(hand.tmp / 'w2', corr, rows))
    pair = next(p for p in link['pairs'] if p['pair_id'] == 'direct_1')
    assert list(pair['named_pair']) == ['3', '5'] and pair['named_pair']['5']['a_value_um2'] == 1.0
    assert pair['named_pair']['5'] == {'a_value_um2': 1.0, 'b_value_um2': 2.0, 'a_status': 'available', 'b_status': 'available'}
    assert next(p for p in link['pairs'] if p['pair_id'] == 'direct_2')['named_pair'] == {}


# ---- (e) ROIs and pair assignment -------------------------------------------------------------------------------------------

def test_roi_bundle_drops_the_opposite_direction_duplicates(hand):
    link = link_of(hand, candidates_dir=hand.cand)
    assert [r['roi_key'] for r in link['rois']] == ['roi:R1+R2', 'roi:R3']
    roi = link['rois'][0]
    assert roi['region_ids'] == ['R1', 'R2'] and roi['score_um2'] == 10.0 and roi['change_kinds'] == ['appeared', 'shifted']
    first, second = roi['contributing_cases']
    assert (first['failure'], first['electrodes'], first['endpoint_layers'], first['analysis_layer']) == (
        'lateral_short', ['A', 'B'], ['L1', 'L1'], None)
    assert (second['electrodes'], second['analysis_layer']) == (['A', 'C'], 'L1')           # key order C|A sorted, analysis layer from the key
    assert [c['case_key'] for c in roi['one_sided_cases']] == ['lateral_short|C|L1|A|L1|L1']
    assert {p['pair_id']: p['roi_key'] for p in link['pairs']} == {'direct_1': 'roi:R1+R2', 'direct_2': 'roi:R3'}


def test_unassigned_direct_pair_is_a_warning(hand):
    write_candidates(hand.cand, direct_pairs=[('direct_1', 'R2', ('A', 'B'), ('B', 'A')), ('direct_9', 'R_unknown', ('D', 'E'), ('E', 'D'))])
    link = link_of(hand, candidates_dir=hand.cand)
    assert next(p for p in link['pairs'] if p['pair_id'] == 'direct_9')['roi_key'] is None
    assert 'unassigned_pair:direct_9' in [w['code'] for w in link['warnings']]


# ---- (f) confirm path -------------------------------------------------------------------------------------------------------

def test_confirm_pairs_follow_query_roi_ids_and_multi_roi_takes_the_larger_score(hand):
    link = link_of(hand, candidates_dir=hand.cand, confirm_dir=hand.conf)
    pairs = {p['pair_id']: p for p in link['pairs']}
    assert pairs['c1']['roi_key'] == 'roi:R1+R2' and pairs['c1']['path'] == 'confirm'     # t1 is a target_to_reference query
    assert pairs['c2']['roi_key'] == 'roi:R3'                                             # q1 (10) and q3 (20): the larger wins
    assert pairs['c3']['roi_key'] == 'roi:R3'
    assert 'multi_roi:c2' in [w['code'] for w in link['warnings']] and 'multi_roi:c1' not in [w['code'] for w in link['warnings']]
    assert pairs['c3']['conditions']['reference'] == {'condition_id': None, 'reason': 'confirm_named_pair_unavailable'}
    assert pairs['c3']['conditions']['target'] == {'condition_id': None, 'reason': 'confirm_named_pair_unavailable'}
    assert pairs['c1']['conditions']['reference']['condition_id'] == 'AB@L1'
    assert sorted(pairs) == ['c1', 'c2', 'c3']          # confirm pairs replace the direct pairs
    assert len(link['rois']) == 2                         # ROIs still come from candidate_queries


def test_multi_roi_tie_goes_to_the_smaller_roi_key(hand):
    def equal_scores(payload):
        for direction in payload['directions']:
            for q in direction['queries']:
                q['score_um2'] = 5.0
    write_candidates(hand.cand, mutate=equal_scores)
    write_confirm(hand.conf, hand.cand)
    link = link_of(hand, candidates_dir=hand.cand, confirm_dir=hand.conf)
    assert next(p for p in link['pairs'] if p['pair_id'] == 'c2')['roi_key'] == 'roi:R1+R2'


# ---- (g) condition mapping --------------------------------------------------------------------------------------------------

def test_condition_mapping_is_exact_and_ignores_electrode_order(hand):
    link = link_of(hand, candidates_dir=hand.cand)
    pair = next(p for p in link['pairs'] if p['pair_id'] == 'direct_1')           # b electrodes are ('B', 'A')
    assert pair['conditions'] == {'reference': {'condition_id': 'AB@L1', 'reason': None},
                                  'target': {'condition_id': 'AB@L1', 'reason': None}}
    roi_case = link['rois'][0]['contributing_cases'][1]
    assert roi_case['conditions']['reference']['condition_id'] == 'AC@L1'


def test_condition_mapping_case_differences_are_unmapped_and_duplicates_ambiguous(hand):
    write_candidates(hand.cand, direct_pairs=[('direct_1', 'R2', ('a', 'B'), ('A', 'B')), ('direct_2', 'R3', ('D', 'E'), ('D', 'E'))])
    hand.doc = compare2_doc(tgt_extra=[{'condition_id': 'DE2@L2', 'electrodes': ['E', 'D'], 'layer': 'L2'}])
    link = link_of(hand, candidates_dir=hand.cand)
    pairs = {p['pair_id']: p for p in link['pairs']}
    assert pairs['direct_1']['conditions']['reference'] == {'condition_id': None, 'reason': 'unmapped_condition'}
    assert pairs['direct_1']['conditions']['target']['condition_id'] == 'AB@L1'
    assert pairs['direct_2']['conditions']['reference']['condition_id'] == 'DE@L2'
    assert pairs['direct_2']['conditions']['target'] == {'condition_id': None, 'reason': 'ambiguous_condition_key'}
    codes = [w['code'] for w in link['warnings']]
    assert 'unmapped_condition:reference:L1|B+a' in codes and 'ambiguous_condition_key:target:L2|D+E' in codes


# ---- (h) DL1 ----------------------------------------------------------------------------------------------------------------

def dl1_doc(**override):
    doc = {'fingerprint': 'f' * 64, 'grid': 10, 'grid_selection': 'auto', 'grid_gate': 'ok', 'status': 'available',
           'query_id': 'PT', 'reason': {'code': 'fine', 'detail': 'dict reason'},
           'rankings': [{'product_id': 'PR', 'score': 0.9, 'input_identity': '1' * 64},
                        {'product_id': 'OTHER', 'score': 0.5, 'input_identity': '2' * 64}],
           'excluded': [{'product_id': 'X', 'reason': 'no_assets'}]}
    doc.update(override)
    return doc


def test_dl1_result_selected_and_dict_reason(hand):
    path = write_json(hand.tmp / 'dl1' / 'a.json', dl1_doc())
    link = link_of(hand, candidates_dir=hand.cand, dl1_search=[path])
    item = link['dl1'][0]
    assert item['path_name'] == 'a.json' and item['query_id'] == 'PT' and item['status'] == 'available'
    assert item['reason'] == {'code': 'fine', 'detail': 'dict reason'}
    assert item['rankings'] == [{'product_id': 'PR', 'score': 0.9}, {'product_id': 'OTHER', 'score': 0.5}]
    assert item['excluded'] == [{'product_id': 'X', 'reason': 'no_assets'}]
    assert item['selected'] == ['PR'] and not link['warnings']
    assert link['inputs']['dl1']['files']['a.json'] == sha(path)


def test_dl1_alone_is_enough_and_a_foreign_query_id_warns(hand):
    path = write_json(hand.tmp / 'dl1.json', dl1_doc(query_id='NOT_THE_TARGET'))
    link = link_of(hand, dl1_search=[path])
    assert link['rois'] == [] and link['pairs'] == []
    assert link['dl1'][0]['selected'] == [] and not link['warnings']        # no pair names a target product: nothing to compare against
    path = write_json(hand.tmp / 'dl1b.json', dl1_doc(query_id='NOT_THE_TARGET'))
    link = link_of(hand, candidates_dir=hand.cand, dl1_search=[path])
    assert [w['code'] for w in link['warnings']] == ['dl1_query_mismatch']


def test_dl1_invalid_file_is_an_error(hand):
    for name, doc in (('bad1', {'schema_version': 'x'}), ('bad2', dl1_doc(rankings=[{'product_id': 'PR', 'score': 9, 'input_identity': '1' * 64}])),
                      ('bad3', dl1_doc(fingerprint=None))):
        with pytest.raises(NumericContractError) as exc:
            link_of(hand, dl1_search=[write_json(hand.tmp / f'{name}.json', doc)])
        assert code_of(exc) == 'compare2_link_dl1_invalid'
    broken = hand.tmp / 'broken.json'
    broken.write_text('{', encoding='utf-8')
    with pytest.raises(NumericContractError) as exc:
        link_of(hand, dl1_search=[broken])
    assert code_of(exc) == 'compare2_link_dl1_invalid'


# ---- (i) determinism --------------------------------------------------------------------------------------------------------

def test_same_input_gives_the_same_bytes(hand):
    corr = hand.conf / 'roi_correspondence.json'
    windows = write_windows(hand.tmp / 'win', corr, [named_row('c1', 10, 5), named_row('c1', 10, 3)])
    dl1 = write_json(hand.tmp / 'dl1.json', dl1_doc())
    first = dump(link_of(hand, candidates_dir=hand.cand, confirm_dir=hand.conf, windows_dir=windows, dl1_search=[dl1]))
    again = dump(link_of(hand, candidates_dir=hand.cand, confirm_dir=hand.conf, windows_dir=windows, dl1_search=[dl1]))
    assert first == again


def test_real_chain_is_deterministic(real_chain):
    kw = dict(candidates_dir=real_chain.cand, windows_dir=real_chain.windows, map_window_um=10.0)
    assert dump(build_link(real_chain.doc, **kw)) == dump(build_link(real_chain.doc, **kw))
