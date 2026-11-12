"""vdist-filter/T2: thk_info parser, interlayer-distance filter in configured_dataset, input hash, compare2 / 04 / 06 chain."""
import json
from decimal import Decimal

import pytest

from simcompare.adapters.real_hotspot import configured_dataset, load_real_manifest
from simcompare.interlayer_distance import build_table, canonical, DISTANCE_FILTER_RULE
from simcompare.linepixel import compute_areas
from simcompare.numeric_schema import NumericContractError, diameter_key
import test_compare2 as tc

STACK2 = ['L1', 'L2']
HEAD = 'layers\tdistance\n'


def table(text, stack=STACK2):
    return build_table(text.encode('utf-8'), stack, 'thk_info-X.txt')


def code_of(text, stack=STACK2):
    with pytest.raises(NumericContractError) as caught:
        table(text, stack)
    return caught.value.code


# ---- parser ----------------------------------------------------------------------------------------------------------

def test_header_case_insensitive_blank_lines_and_crlf():
    t = table('\n LAYERS \t Distance \r\n\r\nL1L2\t3\r\n\n')
    assert t.pairs == {('L1', 'L2'): Decimal(3)}


@pytest.mark.parametrize('text', [
    HEAD + 'L1L2\t\n',                 # empty field
    HEAD + 'L1L2\t3\t\n',              # trailing tab = 3 fields
    HEAD + 'L1\tL2\t3\n',              # 3 fields
    'layers,distance\nL1L2,3\n',       # comma separated
    'layers\tdist\nL1L2\t3\n',         # wrong header
    HEAD + 'L1L2\t-1\n', HEAD + 'L1L2\tNaN\n', HEAD + 'L1L2\tinf\n', HEAD + 'L1L2\tabc\n', HEAD + 'L1L2\t1_0\n',
    '',                                 # no header
])
def test_invalid_files_are_refused(text):
    assert code_of(text) == 'invalid_interlayer_distance_file'


def test_error_names_the_line():
    with pytest.raises(NumericContractError) as caught:
        table(HEAD + 'L1L2\t3\nL2L1\t3\t\n')
    assert 'line 3' in str(caught.value)


def test_same_layer_row_and_zero_distance_are_accepted_and_counted():
    t = table(HEAD + 'L1L1\t0\nL1L2\t0\nXXYY\t5\n')
    assert t.pairs == {('L1', 'L2'): Decimal(0)} and t.ignored_rows == 2


def test_both_orders_equal_ok_different_refused_one_side_ok_duplicates():
    assert table(HEAD + 'L1L2\t3\nL2L1\t3.0\n').pairs == {('L1', 'L2'): Decimal(3)}
    assert table(HEAD + 'L2L1\t4\n').pairs == {('L1', 'L2'): Decimal(4)}
    assert table(HEAD + 'L1L2\t3\nL1L2\t3.0\n').pairs == {('L1', 'L2'): Decimal(3)}
    assert code_of(HEAD + 'L1L2\t3\nL2L1\t4\n') == 'invalid_interlayer_distance_file'
    assert code_of(HEAD + 'L1L2\t3\nL1L2\t4\n') == 'invalid_interlayer_distance_file'


def test_ambiguous_key_between_different_pairs_only_when_present_in_the_file():
    stack = ['A', 'AA', 'AAA']   # AA+A and A+AA are one pair (not ambiguous); 'AAAA' = A+AAA only
    assert table(HEAD + 'AAA\t2\n', stack).pairs == {('A', 'AA'): Decimal(2)}
    stack = ['AB', 'C', 'A', 'BC']      # AB+C = A+BC = 'ABC'
    assert code_of(HEAD + 'ABC\t2\n', stack) == 'interlayer_distance_key_ambiguous'
    assert table(HEAD + 'ABCX\t2\n', stack).pairs == {}   # the ambiguous string is not in the file


def test_equal_values_give_one_table_sha_and_order_or_direction_does_not_matter():
    a = table(HEAD + 'L1L2\t3\n')
    for text in (HEAD + 'L1L2\t3.0\n', HEAD + 'L1L2\t3e0\n', HEAD + '\n  L2L1 \t 3 \n', HEAD + 'L1L2\t3\nQQ\t1\n'):
        assert table(text).table_sha256 == a.table_sha256
    assert table(HEAD + 'L1L2\t3.5\n').table_sha256 != a.table_sha256
    assert canonical(Decimal('3e1')) == '30' and canonical(Decimal('-0')) == '0'
    two = table(HEAD + 'L1L2\t3\nL3L2\t1\n', ['L1', 'L2', 'L3'])
    assert two.table_sha256 == table(HEAD + 'L2L3\t1\nL2L1\t3\n', ['L1', 'L2', 'L3']).table_sha256


# ---- filter ----------------------------------------------------------------------------------------------------------

VERT = ('vertical_short', 'X', 'L1', 'Y', 'L2', {1: {12, 13}, 2: {14}, 3: {10, 11}, 4: {12, 13, 14}})   # cells no other L2 case uses
PAIRS = tc.PAIRS + [('XY', 'X', 'Y', 'line')]


def build(tmp_path, cases, pairs=PAIRS, name='s'):
    root, imported = tc.write_side(tmp_path, name, cases, pairs)
    return imported


def dataset(imported, distance=None, assignment=None, **kw):
    t = None if distance is None else table(HEAD + f'L1L2\t{distance}\n')
    return configured_dataset(imported, load_real_manifest(imported), assignment or tc.assignment(),
                              distance_table=t, **kw)


def vertical_case(ds):
    return next(c for c in ds.cases if c.failure_type == 'vertical_short')


def cells(case, d):
    return int(case.records[float(d)].mask.sum())


def test_diameters_up_to_the_distance_are_dropped_boundary_included(tmp_path):
    imported = build(tmp_path, tc.base_cases() + [VERT])
    ds = dataset(imported, 3)
    v = vertical_case(ds)
    assert [cells(v, d) for d in (1, 2, 3, 4)] == [0, 0, 0, 3]
    assert v.records[3.0].input_status == 'observed' and v.records[3.0].source_ca is None
    assert ds.provenance['distance_filter']['filtered_records'] == 3
    assert ds.provenance['distance_filter']['rule'] == DISTANCE_FILTER_RULE
    plain = vertical_case(dataset(imported))
    assert [cells(plain, d) for d in (1, 2, 3, 4)] == [2, 1, 2, 3]


def test_decimal_comparison_keeps_diameter_three_for_a_distance_just_below(tmp_path):
    imported = build(tmp_path, tc.base_cases() + [VERT])
    v = vertical_case(dataset(imported, '2.9999999999999999999'))
    assert [cells(v, d) for d in (1, 2, 3, 4)] == [0, 0, 2, 3]
    assert diameter_key(3.0) > Decimal('2.9999999999999999999')


def test_lateral_cases_are_unchanged(tmp_path):
    imported = build(tmp_path, tc.base_cases() + [VERT])
    on, off = dataset(imported, 50), dataset(imported)
    for a, b in zip(on.cases, off.cases):
        if a.failure_type == 'lateral_short':
            assert all((a.records[d].mask == b.records[d].mask).all() for d in a.records)


def test_filtered_union_equals_the_union_without_that_case(tmp_path):
    with_v = build(tmp_path, tc.base_cases() + [VERT], name='a')
    without = build(tmp_path, tc.base_cases(), name='b')
    on = compute_areas(dataset(with_v, 50))
    base = compute_areas(dataset(without))
    for key, group in on.groups.items():
        twin = next(g for k, g in base.groups.items() if (k.diameter_um, k.analysis_layer) == (key.diameter_um, key.analysis_layer))
        if key.diameter_um <= 4:
            assert group.union.status == twin.union.status == 'available'
            assert group.union.observed_area_um2 == twin.union.observed_area_um2


def test_missing_combinations_are_all_listed(tmp_path):
    stack3 = {**tc.STACK, 'L3': (3, 'P3')}
    cases = tc.base_cases() + [VERT, ('vertical_short', 'Z', 'L2', 'W', 'L3', {1: {0}})]
    pairs = PAIRS + [('ZW', 'Z', 'W', 'line')]
    imported = build(tmp_path, cases, pairs)
    assignment = {**tc.assignment(), 'layer_stack': [{'actual_layer': l, 'stack_order': o, 'density_process_layer': p}
                                                      for l, (o, p) in stack3.items()]}
    t = build_table((HEAD + 'XXYY\t1\n').encode(), ['L1', 'L2', 'L3'])
    with pytest.raises(NumericContractError) as caught:
        configured_dataset(imported, load_real_manifest(imported), assignment, distance_table=t)
    assert caught.value.code == 'interlayer_distance_missing_pairs'
    assert 'L1+L2' in str(caught.value) and 'L2+L3' in str(caught.value)


def test_each_side_uses_its_own_table(tmp_path):
    imported = build(tmp_path, tc.base_cases() + [VERT])
    a, b = vertical_case(dataset(imported, 1)), vertical_case(dataset(imported, 4))
    assert cells(a, 2) == 1 and cells(b, 2) == 0


def test_expected_case_without_folder_is_dropped_per_diameter(tmp_path):
    imported = build(tmp_path, tc.base_cases(), PAIRS)
    manifest = load_real_manifest(imported)
    manifest['expected_cases'] = list(manifest['expected_cases']) + ['vertical_short-X-L1-Y-L2']
    manifest['expected_case_coverage_status'] = 'confirmed'
    ds = configured_dataset(imported, manifest, tc.assignment(), distance_table=table(HEAD + 'L1L2\t3\n'))
    result = compute_areas(ds)
    status = {k.diameter_um: g.union.status for k, g in result.groups.items() if k.analysis_layer == 'L2'}
    assert status[3.0] == 'available' and status[2.0] == 'available'
    assert status[4.0] == 'partial'
    group3 = next(g for k, g in result.groups.items() if k.analysis_layer == 'L2' and k.diameter_um == 3.0)
    assert group3.union.reasons == ()


def test_hash_follows_rule_and_table_content_only(tmp_path):
    imported = build(tmp_path, tc.base_cases() + [VERT])
    h = lambda d: dataset(imported, d).provenance['input_hash']
    base = dataset(imported).provenance['input_hash']
    assert h(3) != h(2) and h(3) != base
    spaced = configured_dataset(imported, load_real_manifest(imported), tc.assignment(),
                                distance_table=table(HEAD + '\n  L2L1 \t 3.0 \n'))
    assert spaced.provenance['input_hash'] == h(3)


def test_key_absent_is_the_baseline_even_with_a_config_folder_and_vertical_cases(tmp_path):
    imported = build(tmp_path, tc.base_cases() + [VERT])
    plain = dataset(imported)
    with_folder = dataset(imported, config_dir=tmp_path)
    assert with_folder.provenance['input_hash'] == plain.provenance['input_hash']
    assert 'distance_filter' not in with_folder.provenance
    assert cells(vertical_case(with_folder), 1) == 2


def test_key_from_the_config_folder_is_read_and_its_path_is_not_hashed(tmp_path):
    imported = build(tmp_path, tc.base_cases() + [VERT])
    cfg = tmp_path / 'cfg'
    (cfg / 'sub').mkdir(parents=True)
    (cfg / 'thk_info.txt').write_text(HEAD + 'L1L2\t3\n')
    (cfg / 'sub' / 'other.txt').write_text(HEAD + 'L1L2\t3.0\n')
    one = dataset(imported, assignment={**tc.assignment(), 'interlayer_distance_path': 'thk_info.txt'}, config_dir=cfg)
    two = dataset(imported, assignment={**tc.assignment(), 'interlayer_distance_path': 'sub/other.txt'}, config_dir=cfg)
    assert one.provenance['input_hash'] == two.provenance['input_hash']
    assert cells(vertical_case(one), 3) == 0
    with pytest.raises(NumericContractError) as caught:
        dataset(imported, assignment={**tc.assignment(), 'interlayer_distance_path': 'thk_info.txt'})
    assert caught.value.code == 'interlayer_distance_config_folder_required'


# ---- chain: compare2 (02), 03 -> 04, 06 --------------------------------------------------------------------------------

def test_compare2_requires_the_key_when_a_vertical_short_remains(tmp_path, capsys):
    code, _ = tc.run(tmp_path, (tc.base_cases() + [VERT], PAIRS), (tc.base_cases() + [VERT], PAIRS))
    assert code == 2 and 'interlayer_distance_path_required' in capsys.readouterr().err


def test_compare2_without_vertical_or_key_is_unchanged(tmp_path):
    ref, tgt = tc.two_sides()
    code, out = tc.run(tmp_path, ref, tgt)
    assert code == 0


def test_compare2_vi_changes_when_the_vertical_hotspots_are_dropped(tmp_path):
    ref = (tc.base_cases() + [VERT], PAIRS)
    folder_a, folder_b = tmp_path / 'a', tmp_path / 'b'
    folder_a.mkdir()
    folder_b.mkdir()
    code_a, out_a = tc.run(folder_a, ref, ref, thk=HEAD + 'L1L2\t0.5\n')
    code_b, out_b = tc.run(folder_b, ref, ref, thk=HEAD + 'L1L2\t3\n')
    assert code_a == code_b == 0
    row = lambda out: tc.cond_by_id(tc.load(out)['sides']['reference']['conditions'])['XY@L2']
    sizes = lambda out: {s['diameter_um']: s['area_excl_um2'] for s in row(out)['by_size']}
    a, b = sizes(out_a), sizes(out_b)
    assert a[1.0] > 0 and b[1.0] == b[2.0] == b[3.0] == 0.0
    assert b[4.0] == a[4.0] > 0
    assert row(out_b)['lambda_excl'] < row(out_a)['lambda_excl']
