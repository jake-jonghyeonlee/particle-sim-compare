"""mrep/T2: ledger_changes links 04 change locations to ledger items (exact key, ambiguity, hash check)."""
import copy
import json

import pytest

from simcompare import compare2_link, ledger_changes
from simcompare.ledger_changes import build_changes

REF, TGT = 'hash-reference', 'hash-target'


def end(electrode, layer):
    return {'electrode_id': electrode, 'actual_layer': layer}


def item(item_id, failure, ends, layer):
    return {'item_id': item_id, 'failure_type': failure, 'analysis_layer': layer,
            'endpoints': [end(e, l) for e, l in ends], 'presence': 'both'}


def lpair(pid, electrodes, items, kind='line', change_kind='same_pair_change'):
    return {'pair_id': pid, 'electrodes': list(electrodes), 'kind': kind, 'change_kind': change_kind, 'items': items}


def led_doc(pairs=None, cno=(), exclusions=None):
    inputs = {'pair_exclusions': exclusions} if exclusions else {}
    return {'schema_version': 'ledger_v1', 'pairs': pairs if pairs is not None else default_pairs(), 'cno': list(cno),
            'inputs': inputs}


def default_pairs():
    return [
        lpair('P_AB', ['A', 'B'], [item('i_ab_sd1', 'lateral_short', [('A', 'SD1'), ('B', 'SD1')], 'SD1'),
                                   item('i_ab_v', 'vertical_short', [('A', 'GAT2'), ('B', 'SD2')], 'GAT2'),
                                   item('i_ab_v2', 'vertical_short', [('A', 'SD2'), ('B', 'GAT2')], 'GAT2')]),
        lpair('P_CD', ['C', 'D'], [item('i_cd_x', 'lateral_short', [('C', 'L1'), ('D', 'L1')], 'L1'),
                                   item('i_cd_y', 'lateral_short', [('C', 'L1'), ('D', 'L1')], 'L2')], kind='pixel'),
        lpair('P_EF', ['E', 'F'], [item('i_ef', 'lateral_short', [('E', 'L1'), ('F', 'L1')], 'L1')], change_kind='no_change'),
        lpair('P_GH', ['G', 'H'], [item('i_gh', 'lateral_short', [('G', 'L1'), ('H', 'L1')], 'L1')], kind='pixel')]


def c2_doc(ref=REF, tgt=TGT, exclusions=None):
    manifests = {'reference': {'input_hash': ref}, 'target': {'input_hash': tgt}}
    if exclusions:
        manifests['target']['pair_exclusions'] = exclusions
    sides = {role: {'conditions': [{'condition_id': 'AB@SD1', 'electrodes': ['A', 'B'], 'layer': 'SD1'}]}
             for role in ('reference', 'target')}
    return {'schema_version': 'compare2_v1', 'inputs': {'manifests': manifests}, 'sides': sides}


def case(key, kind='appeared', **extra):
    return dict({'case_key': key, 'change_kind': kind, 'residual_um2': 1.0, 'present_in': 'both', 'layer_changed': False}, **extra)


def query(region, cases, merged=None, one_sided=(), center=(1.0, 2.0)):
    return {'query_id': f'q:{region}', 'region_id': region, 'merged_from': list(merged or [region]), 'score_um2': 5.0,
            'layers': ['L1'], 'status': 'ready', 'center_xy_um': list(center), 'window_um': 10.0, 'roi_ids': [],
            'contributing_cases': list(cases), 'one_sided_cases': list(one_sided)}


def payload(queries, ref=REF, tgt=TGT, apply_align=None):
    doc = {'schema_version': 'candidate_queries_v1',
           'inputs': {'reference': {'dataset_input_hash': ref}, 'target': {'dataset_input_hash': tgt}},
           'directions': [{'direction': 'reference_to_target', 'queries': list(queries)}]}
    if apply_align:
        doc['change_args'] = {'apply_align': apply_align}
    return doc


def one(key, kind='appeared', **kw):
    return payload([query('R1', [case(key, kind)], **kw)])


def only_case(result):
    return result['rois'][0]['cases'][0]


def test_vertical_items_with_the_same_electrodes_are_told_apart_by_the_endpoint_layers():
    result = build_changes(one('vertical_short|A|GAT2|B|SD2|GAT2'), led_doc(), c2_doc())
    row = only_case(result)
    assert (row['status'], row['item_id'], row['pair_id']) == ('linked', 'i_ab_v', 'P_AB')
    assert result['items'] == {'i_ab_v': {'roi_keys': ['roi:R1']}}
    # the key names the endpoints sorted, so the opposite direction is a different item
    other = build_changes(one('vertical_short|A|SD2|B|GAT2|GAT2'), led_doc(), c2_doc())
    assert only_case(other)['item_id'] == 'i_ab_v2'


def test_two_items_with_one_key_are_ambiguous_and_not_linked():
    pairs = default_pairs()
    pairs[0]['items'].append(item('i_ab_dup', 'lateral_short', [('A', 'SD1'), ('B', 'SD1')], 'SD1'))
    result = build_changes(one('lateral_short|A|SD1|B|SD1|SD1'), led_doc(pairs), c2_doc())
    row = only_case(result)
    assert (row['status'], row['reason'], row['pair_id'], row['item_id']) == ('ambiguous', 'ambiguous_item', None, None)
    assert result['pairs'] == {} and result['items'] == {}
    assert result['summary']['ambiguous_cases'] == 1 and result['summary']['linked_pairs'] == 0


def test_excluded_pair_no_pair_and_no_item_are_told_apart():
    exclusions = {'pairs': [{'electrodes': ['X', 'Y'], 'pair_id': None, 'reason': 'builtin:GH', 'cases': 1,
                             'analysis_layers': ['L1']}]}
    doc = c2_doc(exclusions=exclusions)
    excluded = build_changes(one('lateral_short|X|L1|Y|L1|L1'), led_doc(), doc)
    assert (only_case(excluded)['status'], only_case(excluded)['reason']) == ('unlinked', 'pair_excluded')
    ledger_copy = build_changes(one('lateral_short|X|L1|Y|L1|L1'), led_doc(exclusions={'target': exclusions}), c2_doc())
    assert only_case(ledger_copy)['reason'] == 'pair_excluded'
    assert only_case(build_changes(one('lateral_short|P|L1|Q|L1|L1'), led_doc(), c2_doc()))['reason'] == 'no_pair'
    no_item = build_changes(one('lateral_short|A|SD1|B|SD1|OTHER'), led_doc(), c2_doc())
    assert (only_case(no_item)['status'], only_case(no_item)['reason']) == ('unlinked', 'no_item')


def test_five_part_key_links_only_when_one_candidate_exists():
    linked = build_changes(one('lateral_short|A|SD1|B|SD1'), led_doc(), c2_doc())
    assert only_case(linked)['item_id'] == 'i_ab_sd1'
    # P_CD has the same failure and endpoints in two analysis layers: the five-part key cannot choose
    ambiguous = build_changes(one('lateral_short|C|L1|D|L1'), led_doc(), c2_doc())
    assert only_case(ambiguous)['status'] == 'ambiguous'
    six = build_changes(one('lateral_short|C|L1|D|L1|L2'), led_doc(), c2_doc())
    assert only_case(six)['item_id'] == 'i_cd_y'
    # the case's own layer field must not turn a five-part key into a six-part one
    layered = build_changes(payload([query('R1', [case('lateral_short|C|L1|D|L1', layer='L2')])]), led_doc(), c2_doc())
    assert only_case(layered)['status'] == 'ambiguous'


def test_one_item_in_two_rois_counts_one_pair_and_lists_both_rois():
    doc = payload([query('R1', [case('lateral_short|A|SD1|B|SD1|SD1', 'appeared')], center=(1.0, 1.0)),
                   query('R2', [case('lateral_short|A|SD1|B|SD1|SD1', 'shifted'), case('lateral_short|G|L1|H|L1|L1', 'shifted')],
                         center=(9.0, 9.0))])
    result = build_changes(doc, led_doc(), c2_doc())
    assert result['pairs']['P_AB'] == {'roi_keys': ['roi:R1', 'roi:R2'], 'tags': ['appeared', 'shifted']}
    assert result['items']['i_ab_sd1']['roi_keys'] == ['roi:R1', 'roi:R2']
    summary = result['summary']
    assert (summary['roi_count'], summary['linked_pairs']) == (2, 2)
    # denominator = ledger pairs without 'no_change' (P_EF left out): 3, linked 2 -> unlinked 1
    assert (summary['pair_denominator'], summary['unlinked_pairs']) == (3, 1)
    assert summary['tag_pair_counts'] == {'appeared': {'line': 1, 'pixel': 0}, 'shifted': {'line': 1, 'pixel': 1}}


def test_cno_case_links_to_the_cno_row():
    cno = [{'electrode_id': 'CNO', 'density_layer': 'L1'}]
    result = build_changes(one('lateral_open|CNO|L1|L1'), led_doc(cno=cno), c2_doc())
    row = only_case(result)
    assert (row['status'], row['cno_id']) == ('linked', 'cno:CNO@L1')
    assert result['cno'] == {'cno:CNO@L1': {'roi_keys': ['roi:R1']}} and result['pairs'] == {}
    missing = build_changes(one('lateral_open|CNO|L9|L9'), led_doc(cno=cno), c2_doc())
    assert (only_case(missing)['status'], only_case(missing)['reason']) == ('unlinked', 'no_item')


def test_one_sided_cases_are_linked_and_marked():
    doc = payload([query('R1', [], one_sided=[case('lateral_short|G|L1|H|L1|L1', 'appeared', present_in='target')])])
    row = only_case(build_changes(doc, led_doc(), c2_doc()))
    assert (row['one_sided'], row['status'], row['pair_id']) == (True, 'linked', 'P_GH')


@pytest.mark.parametrize('ref, tgt', [('', TGT), (REF, None), ('', '')])
def test_missing_hash_is_stale_even_when_both_sides_are_empty(ref, tgt):
    result = build_changes(payload([query('R1', [])], ref=ref, tgt=tgt), led_doc(), c2_doc(ref=ref, tgt=tgt))
    assert result['link_status'] == 'stale' and result['rois'] == []


def test_missing_hash_on_one_side_only_is_stale():
    assert build_changes(payload([query('R1', [])]), led_doc(), c2_doc(ref=''))['link_status'] == 'stale'
    assert build_changes(payload([query('R1', [])], tgt=''), led_doc(), c2_doc())['link_status'] == 'stale'


def test_different_hash_is_stale_and_links_nothing():
    result = build_changes(payload([query('R1', [case('lateral_short|A|SD1|B|SD1|SD1')])]), led_doc(), c2_doc(tgt='other'))
    assert result['link_status'] == 'stale' and 'target' in result['detail']
    assert result['pairs'] == {} and result['summary']['roi_count'] == 0


def test_diagnostic_mode_still_checks_the_link():
    stale = build_changes(payload([query('R1', [])], ref='x', apply_align='diagnostic'), led_doc(), c2_doc())
    assert (stale['link_status'], stale['apply_align']) == ('stale', 'diagnostic')
    default = build_changes(payload([query('R1', [])]), led_doc(), c2_doc())
    assert (default['link_status'], default['apply_align']) == ('ok', 'diagnostic')
    unit = build_changes(payload([query('R1', [])], apply_align='unit'), led_doc(), c2_doc())
    assert unit['apply_align'] == 'unit'


def test_wrong_schema_and_structure_are_invalid():
    bad = payload([query('R1', [])])
    bad['schema_version'] = 'something_else'
    assert build_changes(bad, led_doc(), c2_doc())['link_status'] == 'invalid'
    no_inputs = payload([query('R1', [])])
    no_inputs['inputs'] = None
    assert build_changes(no_inputs, led_doc(), c2_doc())['link_status'] == 'invalid'
    assert build_changes(dict(payload([]), directions='x'), led_doc(), c2_doc())['link_status'] == 'invalid'
    assert build_changes(payload([query('R1', [])]), {'pairs': None}, c2_doc())['link_status'] == 'invalid'
    broken_fold = payload([dict(query('R1', []), unit_fold={'n_replicas': -1})])
    assert build_changes(broken_fold, led_doc(), c2_doc())['link_status'] == 'invalid'


def test_no_candidates_for_none_missing_folder_and_unreadable_file(tmp_path):
    assert build_changes(None, led_doc(), c2_doc())['link_status'] == 'no_candidates'
    assert build_changes(tmp_path / 'absent', led_doc(), c2_doc())['link_status'] == 'no_candidates'
    assert build_changes(tmp_path, led_doc(), c2_doc())['link_status'] == 'no_candidates'
    (tmp_path / 'candidate_queries_v1.json').write_text('{not json', encoding='utf-8')
    assert build_changes(tmp_path, led_doc(), c2_doc())['link_status'] == 'invalid'


def test_folder_input_equals_payload_input(tmp_path):
    doc = one('lateral_short|A|SD1|B|SD1|SD1')
    (tmp_path / 'candidate_queries_v1.json').write_text(json.dumps(doc), encoding='utf-8')
    assert build_changes(tmp_path, led_doc(), c2_doc()) == build_changes(doc, led_doc(), c2_doc())


def test_roi_keys_equal_the_07_build_rois_result_and_the_output_is_deterministic():
    doc = payload([query('R2', [case('lateral_short|G|L1|H|L1|L1', 'shifted')], merged=['R2', 'R3'], center=(3.0, 4.0)),
                   query('R1', [case('lateral_short|A|SD1|B|SD1|SD1')], center=(1.0, 1.0))])
    c2 = c2_doc()
    warnings = compare2_link._Warnings()
    rois, _ = compare2_link._build_rois(doc, compare2_link._condition_index(c2), compare2_link._cno_index(c2), warnings)
    result = build_changes(copy.deepcopy(doc), led_doc(), c2)
    assert [r['roi_key'] for r in result['rois']] == [r['roi_key'] for r in rois] == ['roi:R1', 'roi:R2+R3']
    for mine, theirs in zip(result['rois'], rois):
        assert mine['center_xy_um'] == theirs['center_xy_um'] and mine['window_um'] == theirs['window_um']
        assert mine['change_kinds'] == theirs['change_kinds']
    assert result['rois'][1]['change_kind_text'] == ['위치 이동']
    assert json.dumps(result, sort_keys=True) == json.dumps(build_changes(copy.deepcopy(doc), led_doc(), c2), sort_keys=True)
    assert ledger_changes.SCHEMA_VERSION == result['schema_version']


def test_unparsable_case_key_is_unlinked():
    row = only_case(build_changes(one('garbage'), led_doc(), c2_doc()))
    assert (row['status'], row['reason']) == ('unlinked', 'case_key_unparsed')
