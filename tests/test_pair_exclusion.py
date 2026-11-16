"""pexcl/T1: pairs.csv class=excluded + built-in GH / N_NODE / VBIAS-VINT1 rule, applied before the merge; conditional input hash."""
import copy
import hashlib
import json

import pytest

from simcompare.adapters.real_hotspot import (CASE_EXCLUSION_RULE, configured_dataset, configured_input_hash,
                                              load_real_manifest)
from simcompare.interlayer_distance import build_table
from simcompare.linepixel import compute_areas
from simcompare.numeric_schema import NumericContractError
from simcompare.pair_exclusion import (BUILTIN_EXCLUDED_ELECTRODES, BUILTIN_EXCLUDED_PAIRS, PAIR_EXCLUSION_RULE,
                                       exclusion_reason, record_block)
import test_compare2 as tc

AB = ('lateral_short', 'A', 'L1', 'B', 'L1', {1: {0, 1, 2}, 2: {0, 1}, 3: {0}})
CD = ('lateral_short', 'C', 'L1', 'D', 'L1', {1: {5, 6}, 2: {5}, 3: {5}})
PAIRS = [('AB', 'A', 'B', 'line'), ('CD', 'C', 'D', 'pixel')]
NO_SETS = (frozenset(), frozenset())


def lat(ea, eb, masks, layer='L1', defect='lateral_short'):
    return (defect, ea, layer, eb, layer, masks)


def imported_of(tmp_path, name, cases, pairs):
    return tc.write_side(tmp_path, name, cases, pairs)[1]


def ds(imported, manifest=None, **kw):
    manifest = manifest if manifest is not None else load_real_manifest(imported)
    return configured_dataset(imported, manifest, tc.assignment(), **kw)


def code_of(call):
    with pytest.raises(NumericContractError) as caught:
        call()
    return caught.value.code


def names(dataset):
    return sorted(c.identity.case_id for c in dataset.cases)


def reasons_of(dataset):
    return {c.identity.case_id: c.reasons for c in dataset.cases}


def old_hash(manifest, provenance):
    """The input_hash formula before the pair rule (no distance table)."""
    text = (manifest['source_payload_hash'] + provenance['configured_assignment_hash']
            + (provenance['pair_table_hash'] or '') + CASE_EXCLUSION_RULE)
    return hashlib.sha256(text.encode('ascii')).hexdigest()


def snapshot(result):
    out = {}
    for key, g in result.groups.items():
        out[key] = (json.dumps(g.ledger_shares, sort_keys=True, default=str), g.union_mask.tobytes(),
                    g.line_mask.tobytes(), g.pixel_effective_mask.tobytes(), repr(g.coverage), g.union.status,
                    g.union.observed_area_um2)
    return out


# ---- the rule itself ---------------------------------------------------------------------------------------------

def test_rule_constants():
    assert BUILTIN_EXCLUDED_ELECTRODES == ('GH', 'N_NODE') and BUILTIN_EXCLUDED_PAIRS == (('VBIAS', 'VINT1'),)
    assert PAIR_EXCLUSION_RULE == 'pair_excluded_gh_n_node_vbias_vint1_2026_11_16'


@pytest.mark.parametrize('a,b', [('gh', 'X'), ('GH ', 'X'), ('GH1', 'X'), ('XGH', 'X'), ('N_NODE2', 'X'), ('n_node', 'X'),
                                 ('VBIAS', 'VINT2'), ('VBIAS', 'VINT1 '), ('VBIAS', 'X'), ('VINT1', 'X')])
def test_near_misses_are_not_excluded(a, b):
    assert exclusion_reason(a, b, *NO_SETS) is None
    assert exclusion_reason(b, a, *NO_SETS) is None


@pytest.mark.parametrize('a,b,reason', [('GH', 'X', 'builtin:GH'), ('X', 'GH', 'builtin:GH'),
                                        ('N_NODE', 'X', 'builtin:N_NODE'), ('GH', 'N_NODE', 'builtin:GH'),
                                        ('N_NODE', 'GH', 'builtin:GH'), ('GH', 'GH', 'builtin:GH'),
                                        ('N_NODE', 'N_NODE', 'builtin:N_NODE'),
                                        ('VBIAS', 'VINT1', 'builtin:VBIAS-VINT1'),
                                        ('VINT1', 'VBIAS', 'builtin:VBIAS-VINT1')])
def test_builtin_reasons_first_match_both_directions(a, b, reason):
    assert exclusion_reason(a, b, *NO_SETS) == reason


def test_registered_pair_wins_and_table_excluded_beats_builtin():
    key = frozenset(('GH', 'X'))
    assert exclusion_reason('GH', 'X', frozenset({key}), frozenset()) is None
    assert exclusion_reason('X', 'GH', frozenset(), frozenset({key})) == 'pairs_csv:excluded'
    assert exclusion_reason('S', 'D', frozenset(), frozenset({frozenset(('S', 'D'))})) == 'pairs_csv:excluded'


def test_near_miss_cases_stay_in_the_dataset_as_unmapped(tmp_path):
    cases = [AB] + [lat(n, 'X', {1: {7}}) for n in ('gh', 'GH1', 'XGH', 'N_NODE2')] + [lat('VBIAS', 'VINT2', {1: {8}})]
    dataset = ds(imported_of(tmp_path, 's', cases, PAIRS[:1]))
    assert len(dataset.cases) == 6 and 'pair_exclusions' not in dataset.provenance
    unmapped = [c for c in dataset.cases if 'unmapped_pair' in c.reasons]
    assert len(unmapped) == 5
    # the reverse order of the built-in pair is left out, VBIAS-VINT2 is not
    reverse = ds(imported_of(tmp_path, 'r', [AB, lat('VINT1', 'VBIAS', {1: {8}})], PAIRS[:1]))
    assert names(reverse) == ['lateral_short-A-L1-B-L1']
    assert reverse.provenance['pair_exclusions']['by_reason'] == {'builtin:VBIAS-VINT1': 1}


# ---- mixed fixture (C1), same-name, CNO, expected-only ---------------------------------------------------------------

def mixed(tmp_path):
    cases = [AB, lat('GH', 'X', {1: {1, 2}, 2: {1}}), lat('GH', 'Y', {1: {2, 3}, 2: {2}})]
    pairs = PAIRS[:1] + [('GX', 'GH', 'X', 'line')]
    return imported_of(tmp_path, 'mix', cases, pairs)


def with_cno(manifest):
    manifest = copy.deepcopy(manifest)
    manifest['cno_cases'] = [{'case_name': 'GH-L1-', 'endpoints': [{'electrode_id': 'GH', 'actual_layer': 'L1'}],
                              'records': [], 'csv': {}, 'geometry_status': 'confirmed'}]
    return manifest


def test_mixed_registered_gh_is_computed_unregistered_is_left_out_cno_stays(tmp_path):
    imported = mixed(tmp_path)
    dataset = ds(imported, with_cno(load_real_manifest(imported)))
    assert names(dataset) == ['GH-L1-', 'lateral_short-A-L1-B-L1', 'lateral_short-GH-L1-X-L1']
    registered = next(c for c in dataset.cases if c.identity.case_id == 'lateral_short-GH-L1-X-L1')
    assert registered.pair_id == 'GX' and registered.classification == 'line'
    assert 'unmapped_pair' not in registered.reasons
    block = dataset.provenance['pair_exclusions']
    assert block['rule'] == PAIR_EXCLUSION_RULE
    assert block['excluded_cases'] == 1 and block['excluded_expected_cases'] == 0
    assert block['by_reason'] == {'builtin:GH': 1}
    assert [c['case'] for c in block['cases']] == ['lateral_short-GH-L1-Y-L1']
    assert block['cases'][0] == {'case': 'lateral_short-GH-L1-Y-L1', 'electrodes': ['GH', 'Y'],
                                 'failure_type': 'lateral_short', 'analysis_layer': 'L1', 'presence': 'discovered',
                                 'observed_diameters': 32, 'reason': 'builtin:GH'}
    assert block['pairs'] == [{'electrodes': ['GH', 'Y'], 'pair_id': None, 'reason': 'builtin:GH', 'cases': 1,
                               'analysis_layers': ['L1']}]
    assert block['builtin_overridden_by_table'] == [{'electrodes': ['GH', 'X'], 'pair_id': 'GX', 'class': 'line',
                                                     'reason': 'builtin:GH'}]
    assert dataset.provenance['expected_cases'] == ['lateral_short-A-L1-B-L1', 'lateral_short-GH-L1-X-L1']
    assert [c['case_id'] for c in dataset.provenance['cases']] == [
        'lateral_short-A-L1-B-L1', 'lateral_short-GH-L1-X-L1', 'GH-L1-']
    assert record_block(dataset.provenance) == block and record_block(dataset.provenance) is not block
    assert record_block({}) is None


def test_same_name_gh_case_is_left_out_other_same_name_stays_unresolved(tmp_path):
    cases = [AB, lat('GH', 'GH', {1: {7}}), lat('N_NODE', 'N_NODE', {1: {8}}), lat('Z', 'Z', {1: {9}})]
    dataset = ds(imported_of(tmp_path, 's', cases, PAIRS[:1]))
    assert names(dataset) == ['lateral_short-A-L1-B-L1', 'lateral_short-Z-L1-Z-L1']
    assert dataset.provenance['unresolved_same_name_cases'] == ['lateral_short-Z-L1-Z-L1']
    assert dataset.provenance['pair_exclusions']['by_reason'] == {'builtin:GH': 1, 'builtin:N_NODE': 1}


def test_expected_only_gh_case_is_counted_and_never_missing(tmp_path):
    imported = imported_of(tmp_path, 's', [AB], PAIRS[:1])
    manifest = load_real_manifest(imported)
    manifest['expected_cases'] = list(manifest['expected_cases']) + ['lateral_short-GH-L1-X-L1',
                                                                      'lateral_short-C-L1-D-L1']
    dataset = ds(imported, manifest)
    assert names(dataset) == ['lateral_short-A-L1-B-L1', 'lateral_short-C-L1-D-L1']
    assert all('expected_case_folder_missing' not in c.reasons
               for c in dataset.cases if c.identity.case_id.startswith('lateral_short-GH'))
    assert 'expected_case_folder_missing' in reasons_of(dataset)['lateral_short-C-L1-D-L1']
    block = dataset.provenance['pair_exclusions']
    assert block['excluded_cases'] == 0 and block['excluded_expected_cases'] == 1
    assert sum(block['by_reason'].values()) == 1
    assert block['cases'] == [{'case': 'lateral_short-GH-L1-X-L1', 'electrodes': ['GH', 'X'],
                               'failure_type': 'lateral_short', 'analysis_layer': 'L1', 'presence': 'expected_only',
                               'observed_diameters': 0, 'reason': 'builtin:GH'}]
    assert 'lateral_short-GH-L1-X-L1' not in dataset.provenance['expected_cases']
    assert dataset.provenance['input_hash'] != configured_input_hash(imported, load_real_manifest(imported), tc.assignment())


def test_vertical_gh_case_needs_no_distance_row(tmp_path):
    vertical = ('vertical_short', 'GH', 'L1', 'X', 'L2', {1: {3}, 2: {3}, 3: {3}})
    table = build_table(b'layers\tdistance\nL1L1\t0\n', ['L1', 'L2'], 'thk_info-X.txt')   # no L1L2 row
    imported = imported_of(tmp_path, 's', [AB, vertical], PAIRS[:1])
    dataset = ds(imported, distance_table=table)
    assert names(dataset) == ['lateral_short-A-L1-B-L1']
    assert dataset.provenance['distance_filter']['looked_up_pairs'] == []
    assert dataset.provenance['distance_filter']['filtered_records'] == 0
    registered_vertical = ('vertical_short', 'A', 'L1', 'X', 'L2', {1: {3}})
    other = imported_of(tmp_path, 'o', [AB, registered_vertical], PAIRS[:1] + [('AX', 'A', 'X', 'line')])
    assert code_of(lambda: ds(other, distance_table=table)) == 'interlayer_distance_missing_pairs'


def test_left_out_case_layer_outside_the_stack_is_not_an_error(tmp_path):
    outside = lat('GH', 'X', {1: {3}}, layer='ZZ')
    imported = imported_of(tmp_path, 's', [AB, outside], PAIRS[:1])
    dataset = ds(imported)
    assert names(dataset) == ['lateral_short-A-L1-B-L1']
    (record,) = dataset.provenance['pair_exclusions']['cases']
    assert record['analysis_layer'] is None
    kept = imported_of(tmp_path, 'k', [AB, lat('A', 'X', {1: {3}}, layer='ZZ')], PAIRS[:1] + [('AX', 'A', 'X', 'line')])
    assert code_of(lambda: ds(kept)) == 'real_actual_layer_unassigned'


def test_all_cases_left_out_keeps_the_existing_error(tmp_path):
    imported = imported_of(tmp_path, 's', [lat('GH', 'X', {1: {3}}), lat('N_NODE', 'Y', {1: {4}})], PAIRS[:1])
    dataset = ds(imported)
    assert dataset.cases == [] and dataset.provenance['pair_exclusions']['excluded_cases'] == 2
    assert code_of(lambda: compute_areas(dataset)) == 'missing_expected_cases'


# ---- pairs.csv ---------------------------------------------------------------------------------------------------------

def table_case(tmp_path, pairs, extra=()):
    return imported_of(tmp_path, 't', [AB, lat('S', 'D', {1: {7}})] + list(extra), pairs)


def test_excluded_row_leaves_the_case_out_and_keeps_pair_id(tmp_path):
    dataset = ds(table_case(tmp_path, PAIRS[:1] + [('XSD', 'S', 'D', 'excluded')]))
    assert names(dataset) == ['lateral_short-A-L1-B-L1']
    assert set(dataset.pairs) == {frozenset(('A', 'B'))}      # an excluded row is not a Pair
    block = dataset.provenance['pair_exclusions']
    assert block['by_reason'] == {'pairs_csv:excluded': 1}
    assert block['pairs'][0]['pair_id'] == 'XSD' and block['pairs'][0]['reason'] == 'pairs_csv:excluded'


def test_duplicate_excluded_row_keeps_the_first_and_reserves_the_second_id(tmp_path):
    pairs = PAIRS[:1] + [('X1', 'S', 'D', 'excluded'), ('X2', 'D', 'S', 'excluded')]
    dataset = ds(table_case(tmp_path, pairs))
    assert dataset.provenance['pair_exclusions']['pairs'][0]['pair_id'] == 'X1'
    again = table_case(tmp_path.joinpath('b'), pairs + [('X2', 'Q', 'R', 'line')])
    assert code_of(lambda: ds(again)) == 'duplicate_or_conflicting_pair'


def test_class_conflicts_and_unknown_class(tmp_path):
    crossed = table_case(tmp_path, [('AB', 'A', 'B', 'line'), ('BA', 'B', 'A', 'excluded')])
    assert code_of(lambda: ds(crossed)) == 'duplicate_or_conflicting_pair'
    crossed2 = table_case(tmp_path.joinpath('c'), [('AB', 'A', 'B', 'excluded'), ('BA', 'B', 'A', 'pixel')])
    assert code_of(lambda: ds(crossed2)) == 'duplicate_or_conflicting_pair'
    odd = table_case(tmp_path.joinpath('d'), PAIRS[:1] + [('XSD', 'S', 'D', 'Excluded')])
    assert code_of(lambda: ds(odd)) == 'invalid_pair'


def test_excluded_row_gets_the_identifier_version_and_electrode_checks(tmp_path):
    empty_id = table_case(tmp_path, PAIRS[:1] + [('', 'S', 'D', 'excluded')])
    assert code_of(lambda: ds(empty_id)) == 'missing_identifier'
    empty_version = table_case(tmp_path.joinpath('v'), PAIRS[:1] + [('X1', 'S', 'D', 'excluded')])
    path = empty_version / 'pairs.csv'
    path.write_text(path.read_text(encoding='utf-8').replace('X1,S,D,excluded,v1', 'X1,S,D,excluded,'), encoding='utf-8')
    assert code_of(lambda: ds(empty_version)) == 'missing_identifier'
    same = table_case(tmp_path.joinpath('s'), PAIRS[:1] + [('X1', 'S', 'S', 'excluded')])
    assert code_of(lambda: ds(same)) == 'invalid_pair_table_row'


# ---- the left-out case does not reach the merge (C6, B3) ------------------------------------------------------------

def test_class_excluded_case_is_not_in_the_union_shares_or_coverage(tmp_path):
    overlap = lat('S1', 'D1', {1: {1, 2, 3}, 2: {1}, 3: {0}})
    pairs = PAIRS + [('XSD', 'S1', 'D1', 'excluded')]
    with_case = ds(imported_of(tmp_path, 'with', [AB, CD, overlap], pairs))
    control = ds(imported_of(tmp_path, 'ctl', [AB, CD], pairs))     # the manifest without that case
    got, want = (compute_areas(d, condition_stats=True) for d in (with_case, control))
    assert snapshot(got) == snapshot(want) and len(got.groups) == len(want.groups) == 32
    assert all(g.union.status == 'available' for g in got.groups.values())
    assert with_case.provenance['input_hash'] != control.provenance['input_hash']
    assert with_case.provenance['pair_exclusions']['excluded_cases'] == 1 and 'pair_exclusions' not in control.provenance


def test_builtin_gh_overlap_does_not_change_the_shares(tmp_path):
    overlap = lat('GH', 'Y', {1: {1, 2, 3}, 2: {1}, 3: {0}})
    with_case = ds(imported_of(tmp_path, 'with', [AB, CD, overlap], PAIRS))
    control = ds(imported_of(tmp_path, 'ctl', [AB, CD], PAIRS))
    got, want = (compute_areas(d, condition_stats=True) for d in (with_case, control))
    assert snapshot(got) == snapshot(want)
    assert all(g.union.status == 'available' and g.coverage.unmapped == 0 for g in got.groups.values())
    # without the rule the same input is not computable (the GH case is unmapped)
    kept = ds(imported_of(tmp_path, 'reg', [AB, CD, overlap], PAIRS + [('GY', 'GH', 'Y', 'line')]))
    assert 'pair_exclusions' not in kept.provenance
    assert snapshot(compute_areas(kept, condition_stats=True)) != snapshot(got)


# ---- hash contract (Q5, C4) -------------------------------------------------------------------------------------------

def test_no_exclusion_means_no_key_and_the_old_hash(tmp_path):
    imported = imported_of(tmp_path, 's', [AB, CD], PAIRS)
    manifest = load_real_manifest(imported)
    dataset = ds(imported, manifest)
    assert 'pair_exclusions' not in dataset.provenance
    assert dataset.provenance['input_hash'] == old_hash(manifest, dataset.provenance)
    assert configured_input_hash(imported, manifest, tc.assignment()) == dataset.provenance['input_hash']
    # a registered GH pair overrides the rule: still no exclusion, still the old hash
    reg = imported_of(tmp_path, 'g', [AB, lat('GH', 'X', {1: {1}})], PAIRS[:1] + [('GX', 'GH', 'X', 'line')])
    got = ds(reg)
    assert 'pair_exclusions' not in got.provenance and got.provenance['input_hash'] == old_hash(
        load_real_manifest(reg), got.provenance)


def test_hash_contract_with_exclusions(tmp_path):
    imported = mixed(tmp_path)
    manifest = load_real_manifest(imported)
    before = copy.deepcopy(manifest)
    h_first = configured_input_hash(imported, manifest, tc.assignment())
    assert manifest == before
    dataset = ds(imported, manifest)
    assert manifest == before
    assert dataset.provenance['input_hash'] == h_first
    # the other order, and a manifest read fresh from the files
    dataset2 = ds(imported)
    assert configured_input_hash(imported, load_real_manifest(imported), tc.assignment()) == dataset2.provenance['input_hash'] == h_first
    assert h_first == hashlib.sha256((manifest['source_payload_hash'] + dataset.provenance['configured_assignment_hash']
                                      + dataset.provenance['pair_table_hash'] + CASE_EXCLUSION_RULE
                                      + PAIR_EXCLUSION_RULE).encode('ascii')).hexdigest()
    assert h_first != old_hash(manifest, dataset.provenance)


def test_expected_only_exclusion_alone_adds_the_rule(tmp_path):
    imported = imported_of(tmp_path, 's', [AB], PAIRS[:1])
    plain = load_real_manifest(imported)
    extra = copy.deepcopy(plain)
    extra['expected_cases'] = list(extra['expected_cases']) + ['lateral_short-GH-L1-X-L1']
    base_hash = configured_input_hash(imported, plain, tc.assignment())
    ruled = configured_input_hash(imported, extra, tc.assignment())
    assert ruled != base_hash and ds(imported, extra).provenance['input_hash'] == ruled
    assert extra['expected_cases'][-1] == 'lateral_short-GH-L1-X-L1'     # the manifest was not changed
    assert base_hash == old_hash(plain, ds(imported, plain).provenance)


def test_excluded_row_changes_the_pair_table_hash(tmp_path):
    a = ds(table_case(tmp_path, PAIRS[:1]))
    b = ds(table_case(tmp_path.joinpath('b'), PAIRS[:1] + [('XSD', 'S', 'D', 'excluded')]))
    assert a.provenance['pair_table_hash'] != b.provenance['pair_table_hash']
