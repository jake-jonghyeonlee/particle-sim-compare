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
from simcompare.compare2_link import ROLES, _classify, build_link, compute_locations
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


def write_candidates(folder, *, direct_pairs=None, ref_hash=REF_HASH, tgt_hash=TGT_HASH, mutate=None, present_in=None):
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
                               'area_target_um2': 3.5, 'present_in': (present_in or {}).get(pid, 'both'), 'layer_changed': False,
                               'flags': [], 'score_um2': 10.0}
                         for pid, region, a, b in pairs}}
    write_json(folder / 'direct/roi_correspondence.json', corr)
    write_json(folder / 'direct/direct_pairs_sidecar.json', sidecar)
    return folder


def write_confirm(folder, candidates, *, pairs=None, recorded_sha=None):
    """c1: target_to_reference query t1 -> ROI1; c2: names q1 and q3 -> both ROIs (multi_roi, ROI2 wins on score); c3: no endpoints.

    Like confirm_pairs.py:513-516,534: only the query side has endpoints (reference for a reference_to_target query, target
    for a target_to_reference query); the candidate side has endpoints=None.
    """
    folder = Path(folder)
    pairs = pairs if pairs is not None else {
        'c1': ([{'direction': 'target_to_reference', 'query_roi_id': 't1'}], ('A', 'B')),
        'c2': ([{'direction': 'reference_to_target', 'query_roi_id': 'q1'},
                {'direction': 'reference_to_target', 'query_roi_id': 'q3'}], ('A', 'C')),
        'c3': ([{'direction': 'reference_to_target', 'query_roi_id': 'q3'}], None)}
    def sides(cands, e):
        if e is None:
            return None, None
        return (e, None) if cands[0]['direction'] == 'reference_to_target' else (None, e)
    corr = {'schema_version': 'roi_correspondence_v1',
            'pairs': [{**corr_pair(pid, a_electrodes=sides(cands, e)[0], b_electrodes=sides(cands, e)[1]), 'status': 'ready'}
                      for pid, (cands, e) in pairs.items()]}
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
    assert [w for w in link['warnings'] if w['code'] == 'unassigned_pair' and w['detail'].startswith('direct_9:')]


# ---- (f) confirm path -------------------------------------------------------------------------------------------------------

def test_confirm_pairs_follow_query_roi_ids_and_multi_roi_takes_the_larger_score(hand):
    link = link_of(hand, candidates_dir=hand.cand, confirm_dir=hand.conf)
    pairs = {p['pair_id']: p for p in link['pairs']}
    assert pairs['c1']['roi_key'] == 'roi:R1+R2' and pairs['c1']['path'] == 'confirm'     # t1 is a target_to_reference query
    assert pairs['c2']['roi_key'] == 'roi:R3'                                             # q1 (10) and q3 (20): the larger wins
    assert pairs['c3']['roi_key'] == 'roi:R3'
    multi = [w['detail'].split(':')[0] for w in link['warnings'] if w['code'] == 'multi_roi']
    assert multi == ['c2']
    unavailable = {'condition_id': None, 'reason': 'confirm_named_pair_unavailable'}
    assert pairs['c3']['conditions'] == {'reference': unavailable, 'target': unavailable}
    # adv3: only the query side has endpoints; the candidate side is unavailable
    assert pairs['c1']['conditions'] == {'reference': unavailable, 'target': {'condition_id': 'AB@L1', 'reason': None}}
    assert pairs['c2']['conditions'] == {'reference': {'condition_id': 'AC@L1', 'reason': None}, 'target': unavailable}
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
    found = {(w['code'], w['detail'].split(':')[0] + ':' + w['detail'].split(':')[1]) for w in link['warnings']}
    assert ('unmapped_condition', 'reference:L1|B+a') in found and ('ambiguous_condition_key', 'target:L2|D+E') in found
    assert all(w['code'] in ('unmapped_condition', 'ambiguous_condition_key') for w in link['warnings'])     # adv10: fixed codes


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
    assert link['inputs']['dl1']['files'] == {'0:a.json': sha(path)}


def test_dl1_alone_is_enough_and_a_foreign_query_id_warns(hand):
    path = write_json(hand.tmp / 'dl1.json', dl1_doc(query_id='NOT_THE_TARGET'))
    link = link_of(hand, dl1_search=[path])
    assert link['rois'] == [] and link['pairs'] == []
    assert link['dl1'][0]['selected'] == []
    assert [w['code'] for w in link['warnings']] == ['dl1_query_unverified']       # adv6: no target product id to check against
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


# ---- V3 review follow-ups (adv1/2/5/6/7/8/9) -------------------------------------------------------------------------------

def test_adv1_case_absent_on_a_side_has_no_mapping_and_no_warning(hand):
    def one_side(payload):
        payload['directions'][0]['queries'][0]['contributing_cases'][1]['present_in'] = 'reference'
    write_candidates(hand.cand, mutate=one_side)
    link = link_of(hand, candidates_dir=hand.cand)
    case_ac = link['rois'][0]['contributing_cases'][1]
    assert case_ac['conditions']['reference']['condition_id'] == 'AC@L1'
    assert case_ac['conditions']['target'] == {'condition_id': None, 'reason': 'case_absent_on_side'}
    assert not link['warnings']


def test_adv2_one_sided_direct_pair_has_no_endpoints_on_either_side(hand):
    # real producer shape (change_candidates.py:419-430): present_in != both -> no endpoints on either side
    write_candidates(hand.cand, direct_pairs=[('direct_1', 'R2', None, None), ('direct_2', 'R3', ('D', 'E'), ('E', 'D')),
                                              ('direct_3', 'R3', None, None)],
                     present_in={'direct_1': 'reference', 'direct_3': 'both'})
    link = link_of(hand, candidates_dir=hand.cand)
    pairs = {p['pair_id']: p for p in link['pairs']}
    one_sided = {'condition_id': None, 'reason': 'one_sided_pair_no_endpoints'}
    assert pairs['direct_1']['conditions'] == {'reference': one_sided, 'target': one_sided}
    missing = {'condition_id': None, 'reason': 'endpoints_missing'}                 # present_in == both but no endpoints
    assert pairs['direct_3']['conditions'] == {'reference': missing, 'target': missing}
    assert not link['warnings']
    windows = write_windows(hand.tmp / 'win', hand.cand / 'direct/roi_correspondence.json', [named_row('direct_2', 10, 5)])
    loc = compute_locations(link_of(hand, candidates_dir=hand.cand, windows_dir=windows), loc_doc())
    assert next(p for p in loc['pairs'] if p['pair_id'] == 'direct_1')['reasons'] == ['one_sided_pair_no_endpoints']


def test_adv5_excluded_must_be_a_list_of_objects(hand):
    for excluded in (['X'], {'X': 1}, 'X'):
        with pytest.raises(NumericContractError) as exc:
            link_of(hand, dl1_search=[write_json(hand.tmp / 'dl1x.json', dl1_doc(excluded=excluded))])
        assert code_of(exc) == 'compare2_link_dl1_invalid'


def test_adv6_dl1_without_a_known_target_product_warns_unverified(hand):
    path = write_json(hand.tmp / 'dl1.json', dl1_doc())
    link = link_of(hand, dl1_search=[path])
    assert [w['code'] for w in link['warnings']] == ['dl1_query_unverified'] and 'dl1.json' in link['warnings'][0]['detail']


def test_adv7_same_file_names_in_two_folders_do_not_collide(hand):
    first = write_json(hand.tmp / 'one' / 'dl1.json', dl1_doc())
    second = write_json(hand.tmp / 'two' / 'dl1.json', dl1_doc(grid=20))
    link = link_of(hand, candidates_dir=hand.cand, dl1_search=[first, second])
    files = link['inputs']['dl1']['files']
    assert len(files) == 2 and set(files) == {'0:dl1.json', '1:dl1.json'}
    assert files['0:dl1.json'] == sha(first) and files['1:dl1.json'] == sha(second)


def test_adv8_quoted_newline_in_a_csv_field_does_not_break_rows(hand):
    corr = hand.cand / 'direct/roi_correspondence.json'
    first = {**named_row('direct_1', 10, 5, a=1.0, b=2.0), 'delta_reasons': 'line one\nline two'}
    link = link_of(hand, candidates_dir=hand.cand,
                   windows_dir=write_windows(hand.tmp / 'win', corr, [first, named_row('direct_1', 10, 3, a=4.0, b=5.0)]))
    pair = next(p for p in link['pairs'] if p['pair_id'] == 'direct_1')
    assert pair['named_pair']['5']['b_value_um2'] == 2.0 and pair['named_pair']['3']['a_value_um2'] == 4.0


def test_adv9_bad_named_pair_diameter_is_a_schema_mismatch_with_the_original_code(hand):
    corr = hand.cand / 'direct/roi_correspondence.json'
    for bad in ('inf', 'nan'):
        windows = write_windows(hand.tmp / 'win', corr, [named_row('direct_1', 10, bad)])
        with pytest.raises(NumericContractError) as exc:
            link_of(hand, candidates_dir=hand.cand, windows_dir=windows)
        assert code_of(exc) == 'compare2_link_schema_mismatch' and 'diameter' in str(exc.value)


# ---- V3 locations -----------------------------------------------------------------------------------------------------------

DENS = {('reference', 'L1'): {3: (0.1, 0.9), 5: (0.2, 0.9)}, ('target', 'L1'): {3: (0.3, 0.9), 5: (0.4, 0.9)},
        ('reference', 'L2'): {3: (0.9, 0.5), 5: (0.9, 0.6)}, ('target', 'L2'): {3: (0.9, 0.7), 5: (0.9, 0.8)}}


def loc_doc(classes=None, totals=(6.0, 5.0)):
    """compare2 document for the hand folders: F line/pixel ref 2/3, target 5/7; slopes 0.5/0.25; AB,AC line on L1, DE pixel on L2."""
    doc = compare2_doc()
    classes = classes or {}
    for role in ROLES:
        side = doc['sides'][role]
        for cond in side['conditions']:
            cond['class'] = classes.get((role, cond['condition_id']), 'pixel' if cond['layer'] == 'L2' else 'line')
            cond['by_size'] = []
        side['layers'] = [{'layer': layer, 'density_layer': 'D' + layer[1],
                           'by_size': [{'diameter_um': float(d), 'density_line': v[0], 'density_pixel': v[1]}
                                       for d, v in DENS[(role, layer)].items()]} for layer in ('L1', 'L2')]
    doc['products'] = {'reference': {'F_line': 2.0, 'F_pixel': 3.0}, 'target': {'F_line': 5.0, 'F_pixel': 7.0}}
    doc['delta'] = {'slopes': {'line': {'value': 0.5}, 'pixel': {'value': 0.25}},
                    'indicators': {'delta_vi_line': totals[0], 'delta_vi_pixel': totals[1]}}
    return doc


BASE_ROWS = [named_row('direct_1', 10, 3, 1.0, 2.0), named_row('direct_1', 10, 5, 3.0, 4.0),
             named_row('direct_2', 10, 3, 2.0, 1.0), named_row('direct_2', 10, 5, 1.0, 3.0)]


def locs(hand, doc=None, rows=None, **kw):
    doc = doc or loc_doc()
    windows = write_windows(hand.tmp / 'win', hand.cand / 'direct/roi_correspondence.json', BASE_ROWS if rows is None else rows)
    link = link_of(hand, candidates_dir=hand.cand, windows_dir=windows)
    return compute_locations(link, doc, **kw)


def by_id(result, key, field='pairs'):
    return next(item for item in result[field] if item.get('pair_id', item.get('roi_key')) == key)


def test_a_hand_computed_two_diameters_line_and_pixel(hand):
    result = locs(hand)
    p1, p2 = by_id(result, 'direct_1'), by_id(result, 'direct_2')
    assert result['diameters_um'] == [3.0, 5.0]
    # line: (0.3*2*5 + 0.4*4*5) - (0.1*1*2 + 0.2*3*2) = 11 - 1.4
    assert abs(p1['delta_lambda']['line'] - 9.6) < 1e-12 and abs(p1['delta_vi']['line'] - 4.8) < 1e-12
    assert p1['delta_lambda']['pixel'] == 0.0 and p1['delta_vi']['pixel'] == 0.0
    # pixel: (0.7*1*7 + 0.8*3*7) - (0.5*2*3 + 0.6*1*3) = 21.7 - 4.8
    assert abs(p2['delta_lambda']['pixel'] - 16.9) < 1e-12 and abs(p2['delta_vi']['pixel'] - 4.225) < 1e-12
    assert p2['delta_lambda']['line'] == 0.0
    roi1, roi2 = by_id(result, 'roi:R1+R2', 'rois'), by_id(result, 'roi:R3', 'rois')
    assert abs(roi1['delta_vi']['line'] - 4.8) < 1e-12 and abs(roi2['delta_vi']['pixel'] - 4.225) < 1e-12
    assert roi1['condition_ids'] == ['AB@L1', 'AC@L1'] and roi2['condition_ids'] == ['DE@L2']
    assert roi1['design_change']['change_kinds'] == ['appeared', 'shifted']


def test_b_line_to_pixel_transition_splits_by_class(hand):
    doc = loc_doc({('target', 'AB@L1'): 'pixel'})
    p1 = by_id(locs(hand, doc), 'direct_1')
    assert abs(p1['delta_lambda']['line'] - (-1.4)) < 1e-12                       # only the reference side is a line condition
    assert abs(p1['delta_lambda']['pixel'] - (0.9 * 2 * 7 + 0.9 * 4 * 7)) < 1e-12  # only the target side is a pixel condition
    assert abs(p1['delta_vi']['line'] - (-0.7)) < 1e-12 and abs(p1['delta_vi']['pixel'] - 37.8 * 0.25) < 1e-12


def test_c_evaluation_order_zero_area_before_missing_density(hand):
    doc = loc_doc()
    doc['sides']['target']['layers'][0]['by_size'][0]['density_line'] = None         # target L1 line density at 3 is missing
    rows = [named_row('direct_1', 10, 3, 1.0, 0.0), named_row('direct_1', 10, 5, 3.0, 4.0)]
    p1 = by_id(locs(hand, doc, rows), 'direct_1')
    assert abs(p1['delta_lambda']['line'] - (0.4 * 4 * 5 - 1.4)) < 1e-12              # zero target area at d=3: that term is 0
    assert 'density_missing_zero_area' in p1['reasons'] and not [r for r in p1['reasons'] if r.startswith('density_missing:')]
    rows = [named_row('direct_1', 10, 3, 1.0, 2.0), named_row('direct_1', 10, 5, 3.0, 4.0)]
    p1 = by_id(locs(hand, doc, rows), 'direct_1')
    assert p1['delta_lambda']['line'] is None and p1['delta_vi']['line'] is None and 'density_missing:target:3' in p1['reasons']
    assert p1['delta_lambda']['pixel'] == 0.0                                          # the pixel family is unaffected (class line)
    doc['products']['reference']['F_line'] = None
    p1 = by_id(locs(hand, doc, rows), 'direct_1')
    assert 'factor_missing:reference' in p1['reasons']


def test_d_confirm_path_is_not_computable(hand):
    conf = write_confirm(hand.tmp / 'conf2', hand.cand)
    windows = write_windows(hand.tmp / 'win', conf / 'roi_correspondence.json', [named_row('c1', 10, 3, 1.0, 2.0), named_row('c1', 10, 5, 1.0, 2.0)])
    link = link_of(hand, candidates_dir=hand.cand, confirm_dir=conf, windows_dir=windows)
    result = compute_locations(link, loc_doc())
    assert all(p['delta_vi'] == {'line': None, 'pixel': None} and p['reasons'] == ['confirm_named_pair_unavailable']
               for p in result['pairs'])
    assert result['counts'] == {'worse': 0, 'improved': 0, 'negligible': 0, 'uncomputable': 2}
    assert all(r['class'] == 'uncomputable' and r['net'] is None for r in result['rois'])


def test_e_diameter_set_follows_the_compare2_selection(hand):
    doc = loc_doc()
    doc['inputs']['diameter_filter'] = {'mode': 'listed', 'diameters_um': [3.0]}
    result = locs(hand, doc)
    assert result['diameters_um'] == [3.0]
    assert abs(by_id(result, 'direct_1')['delta_lambda']['line'] - (0.3 * 2 * 5 - 0.1 * 1 * 2)) < 1e-12
    doc['inputs']['diameter_filter']['diameters_um'] = [3.0, 7.0]
    p1 = by_id(locs(hand, doc), 'direct_1')
    assert 'window_row_missing:7' in p1['reasons'] and p1['delta_lambda'] == {'line': None, 'pixel': None}


def test_f_classification_boundaries_and_both_denominator_branches(hand):
    assert [_classify(v) for v in (0.005, 0.0050001, -0.005, -0.0050001, 0.0, None)] == [
        'negligible', 'worse', 'negligible', 'improved', 'negligible', 'uncomputable']
    big = locs(hand, loc_doc(totals=(60.0, 50.0)))              # |dVI| above the location sums: they divide by |dVI|
    assert abs(by_id(big, 'roi:R1+R2', 'rois')['net'] - 4.8 / 60.0) < 1e-12
    small = locs(hand, loc_doc(totals=(1.0, 0.0)))             # location sums above |dVI|: they divide by the sums
    assert abs(by_id(small, 'roi:R1+R2', 'rois')['net'] - 1.0) < 1e-12 and abs(by_id(small, 'roi:R3', 'rois')['net'] - 1.0) < 1e-12
    zero_rows = [named_row(p, 10, d, 0.0, 0.0) for p in ('direct_1', 'direct_2') for d in (3, 5)]
    zero = locs(hand, loc_doc(totals=(0.0, 0.0)), zero_rows)    # both zero: the term is 0
    assert [r['net'] for r in zero['rois']] == [0.0, 0.0] and zero['counts']['negligible'] == 2
    gone = locs(hand, loc_doc(totals=(1.0, 0.0)), BASE_ROWS[:2])    # direct_2 has no rows: ROI R3 leaves the denominators
    assert gone['counts'] == {'worse': 1, 'improved': 0, 'negligible': 0, 'uncomputable': 1}
    assert abs(by_id(gone, 'roi:R1+R2', 'rois')['net'] - 1.0) < 1e-12


def synthetic(specs, totals=(100.0, 0.0), folds=None):
    """specs: roi -> {pair_id: (a, b) or None}; every pair is AB@L1 (line) at d=3 with Δλ = 1.5 b - 0.2 a.

    folds: roi -> n_replicas of its unit_fold record."""
    rois, pairs = [], []
    for roi_key, members in specs.items():
        rois.append({'roi_key': roi_key, 'contributing_cases': [], 'one_sided_cases': [], 'change_kinds': [],
                     'unit_fold': {'n_replicas': (folds or {}).get(roi_key, 0), 'replicas': []}})
        for pid, area in members.items():
            named = {} if area is None else {'3': {'a_value_um2': area[0], 'b_value_um2': area[1], 'a_status': 'available',
                                                    'b_status': 'available'}}
            pairs.append({'pair_id': pid, 'path': 'direct', 'roi_key': roi_key, 'named_pair': named,
                          'conditions': {role: {'condition_id': 'AB@L1', 'reason': None} for role in ROLES}})
    doc = loc_doc(totals=totals)
    doc['inputs']['diameter_filter'] = {'mode': 'listed', 'diameters_um': [3.0]}
    return {'schema_version': 'compare2_link_v1', 'rois': rois, 'pairs': pairs}, doc


def test_g_ranking_top_n_map_pairs_and_ties():
    specs = {'roi:w1': {'w1': (0, 40)}, 'roi:w2': {'w2': (0, 20)}, 'roi:w3': {'w3': (0, 20)}, 'roi:w4': {'w4': (0, 8)},
             'roi:w5': {'w5': (0, 4)}, 'roi:w6': {'w6': (0, 2)}, 'roi:n1': {'n1': (0, 0.2)},
             'roi:i1': {'i1': (100, 0)}, 'roi:i2': {'i2': (50, 0)}, 'roi:i3': {'i3': (50, 0)}, 'roi:i4': {'i4': (10, 0)},
             'roi:m': {'m1': (0, 4), 'm2': (20, 0), 'm3': (0, 8), 'm4': None, 'm5': (0, 4)}}
    link, doc = synthetic(specs)
    result = compute_locations(link, doc)
    assert result['top_worse'] == ['roi:w1', 'roi:w2', 'roi:w3', 'roi:w4', 'roi:w5']        # tie w2/w3 by roi_key; w6 cut off
    assert result['top_improved'] == ['roi:i1', 'roi:i2', 'roi:i3']                         # most negative first, tie by roi_key
    assert result['counts'] == {'worse': 6, 'improved': 4, 'negligible': 1, 'uncomputable': 1}
    only = compute_locations(link, doc, top_regions=2, top_improved=1)
    assert only['top_worse'] == ['roi:w1', 'roi:w2'] and only['top_improved'] == ['roi:i1']
    roi_m = by_id(result, 'roi:m', 'rois')
    # |dvi|: m3 = 6, m1 = m5 = 3, m2 = 2 (a=20 -> -2); m4 is null and goes last; at most 3 pairs, ties by pair_id
    assert roi_m['map_pairs'] == ['m3', 'm1', 'm5'] and roi_m['class'] == 'uncomputable'
    assert roi_m['reasons'] == ['pair_null:line:1', 'pair_null:pixel:1']
    wide = by_id(compute_locations(link, doc, max_pairs_per_condition=5), 'roi:m', 'rois')
    assert wide['map_pairs'] == ['m3', 'm1', 'm5', 'm2', 'm4']


def test_unit_fold_replica_factor_scales_the_roi_and_changes_the_ranking():
    specs = {'roi:a': {'a1': (0, 40), 'a2': (0, 40)}, 'roi:b': {'b1': (0, 100)}}          # vi: a pairs 30 + 30 = 60, b 75
    link, doc = synthetic(specs, totals=(1000.0, 0.0))
    plain = compute_locations(link, doc)
    assert plain['top_worse'] == ['roi:b', 'roi:a'] and by_id(plain, 'roi:a', 'rois')['replica_factor'] == 1
    link, doc = synthetic(specs, totals=(1000.0, 0.0), folds={'roi:a': 3})
    folded = compute_locations(link, doc)
    roi_a = by_id(folded, 'roi:a', 'rois')
    assert roi_a['replica_factor'] == 4 and abs(roi_a['delta_vi']['line'] - 4 * 60.0) < 1e-9
    assert [p['delta_vi']['line'] for p in folded['pairs'] if p['roi_key'] == 'roi:a'] == [30.0, 30.0]    # pairs keep their own values
    assert folded['top_worse'] == ['roi:a', 'roi:b']
    assert abs(folded['explained']['line']['ratio_top_worse'] - (240.0 + 75.0) / 1000.0) < 1e-12
    assert abs(folded['explained']['line']['residual_unexplained_by_location_sum'] - (1000.0 - 315.0)) < 1e-9
    assert roi_a['pairs_total'] == 2 and roi_a['pairs_null_by_kind'] == {'line': 0, 'pixel': 0} and roi_a['pairs_one_sided'] == 0
    assert 'Unit-fold' in folded['approximation_note'] and '--top-k' in folded['approximation_note']


def test_build_rois_carries_unit_fold_from_the_query(hand):
    def fold(payload):
        payload['directions'][0]['queries'][0]['unit_fold'] = {
            'n_replicas': 3, 'replicas': [{'region_id': f'X{i}', 'unit_index_xy': [i, 0], 'distance_um': 1.0, 'score_um2': 2.0,
                                           'center_xy_um': [0, 0]} for i in range(3)]}
    write_candidates(hand.cand, mutate=fold)
    link = link_of(hand, candidates_dir=hand.cand)
    assert link['rois'][0]['unit_fold']['n_replicas'] == 3 and len(link['rois'][0]['unit_fold']['replicas']) == 3
    assert 'center_xy_um' not in link['rois'][0]['unit_fold']['replicas'][0]
    assert link['rois'][1]['unit_fold'] == {'n_replicas': 0, 'replicas': []}
    windows = write_windows(hand.tmp / 'win', hand.cand / 'direct/roi_correspondence.json', BASE_ROWS)
    result = compute_locations(link_of(hand, candidates_dir=hand.cand, windows_dir=windows), loc_doc())
    assert abs(by_id(result, 'roi:R1+R2', 'rois')['delta_vi']['line'] - 4 * 4.8) < 1e-12


def test_not_applicable_area_is_zero_and_missing_stays_null(hand):
    rows = [{**named_row('direct_1', 10, 3, '', 2.0), 'a_status': 'not_applicable'}, named_row('direct_1', 10, 5, 3.0, 4.0)]
    p1 = by_id(locs(hand, rows=rows), 'direct_1')
    assert abs(p1['delta_lambda']['line'] - (0.3 * 2 * 5 + 0.4 * 4 * 5 - 0.2 * 3 * 2)) < 1e-12
    rows = [{**named_row('direct_1', 10, 3, '', 2.0), 'a_status': 'missing'}, named_row('direct_1', 10, 5, 3.0, 4.0)]
    p1 = by_id(locs(hand, rows=rows), 'direct_1')
    assert p1['delta_lambda']['line'] is None and 'area_missing:reference:3' in p1['reasons']


def test_null_ratio_floor_uses_the_default():
    link, doc = synthetic({'roi:noise': {'p': (0, 1e-24)}}, totals=(0.0, 0.0))
    doc['inputs']['constants'] = {'ratio_null_below': None}
    assert compute_locations(link, doc)['rois'][0]['class'] == 'negligible'


def test_roi_counts_null_and_one_sided_pairs():
    link, doc = synthetic({'roi:m': {'m1': (0, 4), 'm2': None}})
    link['pairs'][1]['conditions'] = {role: {'condition_id': None, 'reason': 'one_sided_pair_no_endpoints'} for role in ROLES}
    roi = compute_locations(link, doc)['rois'][0]
    assert roi['pairs_total'] == 2 and roi['pairs_null_by_kind'] == {'line': 1, 'pixel': 1} and roi['pairs_one_sided'] == 1
    assert roi['class'] == 'uncomputable'


def test_f_denominator_floor_turns_noise_into_negligible():
    link, doc = synthetic({'roi:noise': {'p': (0, 1e-24)}}, totals=(0.0, 0.0))        # dVI = 0 and a 1e-24 location sum
    result = compute_locations(link, doc)
    assert result['rois'][0]['net'] == 0.0 and result['counts']['negligible'] == 1
    above, doc = synthetic({'roi:edge': {'p': (0, 4e-15)}}, totals=(0.0, 0.0))        # vi = 3e-15, just above RATIO_EPS 1e-15
    edge = compute_locations(above, doc)['rois'][0]
    assert abs(edge['net'] - 1.0) < 1e-12 and edge['class'] == 'worse'
    doc['inputs']['constants'] = {'ratio_null_below': 1e-10}                           # the floor comes from the compare2 document
    assert compute_locations(above, doc)['rois'][0]['class'] == 'negligible'


def test_h_explained_ratio_and_residual(hand):
    result = locs(hand)
    line, pixel = result['explained']['line'], result['explained']['pixel']
    assert abs(line['ratio_top_worse'] - 4.8 / 6.0) < 1e-12 and line['ratio_top_improved'] == 0.0
    assert abs(line['residual_unexplained_by_location_sum'] - 1.2) < 1e-12
    assert abs(pixel['ratio_top_worse'] - 4.225 / 5.0) < 1e-12 and abs(pixel['residual_unexplained_by_location_sum'] - 0.775) < 1e-12
    near_zero = locs(hand, loc_doc(totals=(0.0, 5e-16)))['explained']
    for kind in ('line', 'pixel'):
        assert near_zero[kind]['ratio_top_worse'] is None and near_zero[kind]['ratio_top_improved'] is None
        assert near_zero[kind]['reasons'] == ['ratio_undefined']
    assert abs(near_zero['line']['residual_unexplained_by_location_sum'] - (0.0 - 4.8)) < 1e-12


@pytest.fixture(scope='module')
def real_chain_d5(real_chain):
    """The same producer outputs, compare2 limited to diameter 5 (the only diameter that is fully available in the world)."""
    base = real_chain.base
    cfg = yaml.safe_load((base / 'compare2.yaml').read_text(encoding='utf-8'))
    cfg.update(output_dir=str(base / 'c2_d5'), lambda_diameters_um=[5])
    (base / 'compare2_d5.yaml').write_text(yaml.safe_dump(cfg), encoding='utf-8')
    assert main(['compare2', '--config', str(base / 'compare2_d5.yaml')]) in (0, 3)
    keep = os.environ.get('C2_LINK_KEEP')
    if keep:
        shutil.copyfile(str(base / 'c2_d5/compare2.json'), str(Path(keep) / 'compare2_d5.json'))
    return json.loads((base / 'c2_d5/compare2.json').read_text(encoding='utf-8'))


def test_i_real_producer_chain_runs_and_sums_close(real_chain, real_chain_d5):
    for doc in (real_chain.doc, real_chain_d5):
        link = build_link(doc, candidates_dir=real_chain.cand, windows_dir=real_chain.windows, map_window_um=10.0)
        result = compute_locations(link, doc)
        assert result['schema_version'] == 'compare2_locations_v1' and len(result['pairs']) == 2 and len(result['rois']) == 1
        json.dumps(result, allow_nan=False)
    total = real_chain_d5['delta']['indicators']
    assert result['diameters_um'] == [5.0]
    for kind in ('line', 'pixel'):
        assert total[f'delta_vi_{kind}'] is not None, 'the diameter-5 compare2 has a delta-vi to explain'
        assert all(p['delta_vi'][kind] is not None for p in result['pairs'])
        valid = [r['delta_vi'][kind] for r in result['rois']]
        assert None not in valid
        explained = result['explained'][kind]
        assert abs(sum(valid) + explained['residual_unexplained_by_location_sum'] - total[f'delta_vi_{kind}']) < 1e-12


def test_k_locations_are_deterministic(hand):
    assert dump(locs(hand)) == dump(locs(hand))


def test_v4_adv1_replica_count_mismatch_is_an_information_reason():
    link, doc = synthetic({'roi:a': {'a1': (0, 40)}, 'roi:b': {'b1': (0, 40)}}, totals=(1000.0, 0.0), folds={'roi:a': 3})
    link['rois'][1]['unit_fold'] = {'n_replicas': 1, 'replicas': [{'region_id': 'X'}]}      # roi:b is consistent
    result = compute_locations(link, doc)
    roi_a, roi_b = by_id(result, 'roi:a', 'rois'), by_id(result, 'roi:b', 'rois')
    assert 'unit_fold_count_mismatch' in roi_a['reasons'] and 'unit_fold_count_mismatch' not in roi_b['reasons']
    assert roi_a['replica_factor'] == 4 and roi_a['class'] == 'worse'                      # the value still follows n_replicas
    assert roi_b['replica_factor'] == 2


def test_v4_adv2_replica_summary_keeps_the_producer_layers(hand):
    def fold(payload):
        payload['directions'][0]['queries'][0]['unit_fold'] = {
            'n_replicas': 1, 'replicas': [{'region_id': 'X0', 'unit_index_xy': [1, 0], 'distance_um': 1.0, 'score_um2': 2.0,
                                           'layers': ['L1', 'L2'], 'center_xy_um': [0, 0]}]}
    write_candidates(hand.cand, mutate=fold)
    replica = link_of(hand, candidates_dir=hand.cand)['rois'][0]['unit_fold']['replicas'][0]
    assert replica['layers'] == ['L1', 'L2'] and 'center_xy_um' not in replica


@pytest.mark.parametrize('bad', [-1, -3, 'many', [2]])
def test_v4_adv3_negative_or_non_numeric_n_replicas_is_a_schema_mismatch(hand, bad):
    def fold(payload):
        payload['directions'][0]['queries'][0]['unit_fold'] = {'n_replicas': bad, 'replicas': []}
    write_candidates(hand.cand, mutate=fold)
    with pytest.raises(NumericContractError) as info:
        link_of(hand, candidates_dir=hand.cand)
    assert code_of(info) == 'compare2_link_schema_mismatch'
