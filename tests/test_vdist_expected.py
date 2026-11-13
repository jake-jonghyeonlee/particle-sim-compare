"""vdist-expected/T1: an expected case without a folder may lack a distance row (only discovered cases need one)."""
import pytest
import yaml

from simcompare.adapters.real_hotspot import configured_dataset, configured_input_hash, load_real_manifest
from simcompare.cli import main
from simcompare.interlayer_distance import build_table, record_block
from simcompare.numeric_schema import NumericContractError
import test_compare2 as tc
from test_compare2_expected_zero import run_inventory
from test_compare2_report import TGT_CASES, report
from test_interlayer_distance import HEAD, PAIRS, VERT, build

STACK3 = {**tc.STACK, 'L3': (3, 'P3')}
THREE = ['L1', 'L2', 'L3']
ONLY_L2L3 = 'vertical_short-M-L2-N-L3'          # expected only, pair L2+L3
ONLY_L2L3_B = 'vertical_short-O-L2-P-L3'        # a second expected-only case sharing the same pair
ONLY_L1L2 = 'vertical_short-M-L1-N-L2'          # expected only, pair L1+L2
DISCOVERED_L2L3 = ('vertical_short', 'Z', 'L2', 'W', 'L3', {1: {0}})
NEW_KEYS = {'unfiltered_expected_pairs', 'unfiltered_expected_cases'}


def assignment3():
    return {**tc.assignment(), 'layer_stack': [{'actual_layer': layer, 'stack_order': order, 'density_process_layer': p}
                                                for layer, (order, p) in STACK3.items()]}


def world(tmp_path, rows, expected=(), cases=None):
    """cases (default base + the L1/L2 vertical case) with folders; `expected` = extra expected-only case names;
    rows = thk lines after the header (None = no table)."""
    imported = build(tmp_path, cases if cases is not None else tc.base_cases() + [VERT], PAIRS)
    manifest = load_real_manifest(imported)
    manifest['expected_cases'] = list(manifest['expected_cases']) + list(expected)
    manifest['expected_case_coverage_status'] = 'confirmed'
    t = None if rows is None else build_table((HEAD + rows).encode(), THREE, 'thk_info.txt')
    return imported, manifest, t


def make(tmp_path, rows, expected=(), cases=None):
    imported, manifest, t = world(tmp_path, rows, expected, cases)
    return configured_dataset(imported, manifest, assignment3(), distance_table=t)


def case_of(ds, case_id):
    return next(c for c in ds.cases if c.identity.case_id == case_id)


def statuses(case):
    return [(d, r.input_status, tuple(r.reasons)) for d, r in sorted(case.records.items())]


# ---- (a) an expected-only pair that the table lacks is allowed -----------------------------------------------------------

def test_a_expected_only_pair_without_a_row_is_allowed_and_stays_missing(tmp_path):
    ds = make(tmp_path / 'on', 'L1L2\t3\n', [ONLY_L2L3])
    case = case_of(ds, ONLY_L2L3)
    assert all(status == 'missing' and 'expected_case_folder_missing' in reasons for _d, status, reasons in statuses(case))
    assert 'distance_filter' not in case.metadata
    twin = case_of(make(tmp_path / 'off', None, [ONLY_L2L3]), ONLY_L2L3)
    assert statuses(case) == statuses(twin) and case.reasons == twin.reasons
    block = ds.provenance['distance_filter']
    assert block['unfiltered_expected_cases'] == 1 and block['unfiltered_expected_pairs'] == ['L2+L3']


# ---- (b) a discovered case without a row is still refused; the message does not name expected-only pairs ------------------

def test_b_discovered_pair_without_a_row_is_refused_and_expected_only_pairs_are_not_named(tmp_path):
    cases = tc.base_cases() + [VERT, DISCOVERED_L2L3]
    imported, manifest, t = world(tmp_path / 'one', 'XXYY\t1\n', [ONLY_L1L2], cases)
    with pytest.raises(NumericContractError) as caught:
        configured_dataset(imported, manifest, assignment3(), distance_table=t)
    assert caught.value.code == 'interlayer_distance_missing_pairs'
    assert 'L1+L2' in str(caught.value) and 'L2+L3' in str(caught.value)
    imported, manifest, t = world(tmp_path / 'two', 'L1L2\t3\n', [ONLY_L2L3_B, 'vertical_short-Q-L1-R-L3'], cases)
    with pytest.raises(NumericContractError) as caught:
        configured_dataset(imported, manifest, assignment3(), distance_table=t)
    assert str(caught.value).endswith('1 layer pairs without a distance: L2+L3')  # not L1+L3, which only an expected case needs


# ---- (c) the same pair is discovered and expected ------------------------------------------------------------------------

def test_c_shared_pair_missing_from_the_table_is_refused(tmp_path):
    imported, manifest, t = world(tmp_path, 'L1L2\t3\n', [ONLY_L2L3], tc.base_cases() + [DISCOVERED_L2L3])
    with pytest.raises(NumericContractError) as caught:
        configured_dataset(imported, manifest, assignment3(), distance_table=t)
    assert caught.value.code == 'interlayer_distance_missing_pairs' and 'L2+L3' in str(caught.value)


def test_c_shared_pair_in_the_table_filters_the_expected_case_too_and_m_is_zero(tmp_path):
    ds = make(tmp_path, 'L2L3\t2\n', [ONLY_L2L3], tc.base_cases() + [DISCOVERED_L2L3])
    block = ds.provenance['distance_filter']
    assert NEW_KEYS.isdisjoint(block)
    assert block['filtered_cases'] == 2 and block['filtered_records'] == 4      # d = 1, 2 of each of the two cases
    assert sorted(int(key) for key in case_of(ds, ONLY_L2L3).metadata['distance_filter']) == [1, 2]


# ---- (d) every pair has a row: nothing changes (an expected-only pair is among them) --------------------------------------
# Baseline: the same block f3edc88 wrote, derived by hand from its rule (looked_up_pairs = every vertical pair, filtered
# counts per diameter <= distance) and pinned here; the new keys must be absent and the input hash must be the helper's.

def test_d_all_pairs_present_matches_the_old_behaviour(tmp_path):
    imported, manifest, t = world(tmp_path, 'L1L2\t3\nL2L3\t2\n', [ONLY_L2L3, ONLY_L1L2])
    ds = configured_dataset(imported, manifest, assignment3(), distance_table=t)
    block = ds.provenance['distance_filter']
    assert NEW_KEYS.isdisjoint(block)
    assert block['looked_up_pairs'] == ['L1+L2', 'L2+L3']                       # the expected-only pair L2+L3 is looked up
    assert block['filtered_cases'] == 3 and block['filtered_records'] == 3 + 3 + 2    # VERT, ONLY_L1L2 (<= 3), ONLY_L2L3 (<= 2)
    record = record_block(ds.provenance)
    assert NEW_KEYS.isdisjoint(record) and record['layer_pair_distances'] == {'L1+L2': '3', 'L2+L3': '2'}
    assert ds.provenance['input_hash'] == configured_input_hash(imported, manifest, assignment3(), distance_table=t)


# ---- (e) M > 0: both keys, no null pair distance ---------------------------------------------------------------------------

def test_e_unfiltered_expected_cases_are_counted_and_the_pair_is_not_looked_up(tmp_path):
    imported, manifest, t = world(tmp_path, 'L1L2\t3\n', [ONLY_L2L3, ONLY_L2L3_B, ONLY_L1L2])
    ds = configured_dataset(imported, manifest, assignment3(), distance_table=t)
    block = ds.provenance['distance_filter']
    assert block['unfiltered_expected_cases'] == 2                              # two cases share the one missing pair
    assert block['unfiltered_expected_pairs'] == ['L2+L3']
    assert block['looked_up_pairs'] == ['L1+L2']
    assert block['filtered_cases'] == 2                                         # VERT and the expected L1/L2 case
    record = record_block(ds.provenance)
    assert record['unfiltered_expected_pairs'] == ['L2+L3'] and record['unfiltered_expected_cases'] == 2
    assert record['layer_pair_distances'] == {'L1+L2': '3'}                     # no L2+L3, no null
    assert list(record)[-2:] == ['unfiltered_expected_pairs', 'unfiltered_expected_cases']
    assert ds.provenance['input_hash'] == configured_input_hash(imported, manifest, assignment3(), distance_table=t)


def test_e_pairs_are_sorted(tmp_path):
    ds = make(tmp_path, 'L1L2\t3\n', [ONLY_L2L3, 'vertical_short-Q-L1-R-L3'])
    block = ds.provenance['distance_filter']
    assert block['unfiltered_expected_pairs'] == ['L1+L3', 'L2+L3'] and block['unfiltered_expected_cases'] == 2


# ---- 02 / 07 on a real run: the reference has the vertical case, the target only expects it --------------------------------

def run_02(tmp_path, monkeypatch, target_rows):
    """The target thk (own file) decides whether the expected vertical case has a distance row.
    Both sides expect the union of the cases (a confirmed inventory), so the target expects the vertical case."""
    ref = (tc.base_cases() + [VERT], PAIRS)
    tgt = (tc.base_cases(), PAIRS)
    code, out, _doc = run_inventory(tmp_path, monkeypatch, ref, tgt, thk=HEAD + 'L1L2\t3\n')
    assert code == 0
    (tmp_path / 'thk_target.txt').write_text(target_rows, encoding='utf-8')
    numeric = tmp_path / 'numeric.yaml'
    cfg = yaml.safe_load(numeric.read_text(encoding='utf-8'))
    cfg['target']['condition_assignment']['interlayer_distance_path'] = 'thk_target.txt'
    numeric.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    return main(['compare2', '--config', str(tmp_path / 'compare2.yaml')]), out


def test_f_02_records_the_keys_and_07_shows_the_count(tmp_path, monkeypatch):
    code, out = run_02(tmp_path, monkeypatch, HEAD + 'L1L1\t1\n')
    assert code == 0
    manifests = tc.load(out)['inputs']['manifests']
    assert NEW_KEYS.isdisjoint(manifests['reference']['distance_filter'])
    block = manifests['target']['distance_filter']
    assert block['unfiltered_expected_cases'] == 1 and block['unfiltered_expected_pairs'] == ['L1+L2']
    assert block['layer_pair_distances'] == {}
    exit_code, folder = report(tmp_path, out)
    html = (folder / 'report.html').read_text(encoding='utf-8')
    assert 'B: 켜짐 · thk_target.txt · 필터된 case 0개 · 거리 없는 기대 case 1개' in html
    assert 'A: 켜짐 · thk_info.txt · 필터된 case 1개' in html and 'A: 켜짐 · thk_info.txt · 필터된 case 1개 ·' not in html


def test_f_07_line_is_unchanged_without_the_keys(tmp_path):
    code, out = tc.run(tmp_path, (tc.base_cases() + [VERT], PAIRS), (TGT_CASES + [VERT], PAIRS), thk=HEAD + 'L1L2\t3\n')
    exit_code, folder = report(tmp_path, out)
    html = (folder / 'report.html').read_text(encoding='utf-8')
    assert 'A: 켜짐 · thk_info.txt · 필터된 case 1개' in html and '거리 없는' not in html


# ---- (g) D1: a vertical case that is only expected still needs the key -----------------------------------------------------

def test_g_expected_only_vertical_case_still_requires_the_distance_path(tmp_path, monkeypatch, capsys):
    code, out = run_02(tmp_path, monkeypatch, HEAD + 'L1L1\t1\n')
    assert code == 0
    numeric = tmp_path / 'numeric.yaml'
    cfg = yaml.safe_load(numeric.read_text(encoding='utf-8'))
    del cfg['target']['condition_assignment']['interlayer_distance_path']      # the target only expects the vertical case
    numeric.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    capsys.readouterr()
    assert main(['compare2', '--config', str(tmp_path / 'compare2.yaml')]) == 2
    shown = capsys.readouterr()
    assert 'interlayer_distance_path_required' in shown.out + shown.err


# ---- (h) A3: copying the row versus omitting it ----------------------------------------------------------------------------

def test_h_copied_row_and_omitted_row_differ_only_in_presence_and_conditions(tmp_path, monkeypatch):
    (tmp_path / 'omit').mkdir()
    (tmp_path / 'copy').mkdir()
    omit_code, omit_out = run_02(tmp_path / 'omit', monkeypatch, HEAD + 'L1L1\t1\n')
    copy_code, copy_out = run_02(tmp_path / 'copy', monkeypatch, HEAD + 'L1L2\t3\n')
    assert omit_code == 0 and copy_code == 0
    omitted, copied = tc.load(omit_out), tc.load(copy_out)
    for role in ('reference', 'target'):
        for x, y in zip(tc_numbers(omitted, role), tc_numbers(copied, role)):
            assert tc.close(x, y)                                              # lambda, vi: the same either way
    for key in ('delta_vi_line', 'delta_vi_pixel', 'delta_lambda_line', 'delta_lambda_pixel'):
        assert omitted['delta']['indicators'][key] == copied['delta']['indicators'][key] == 0.0
    # pinned differences: the condition's presence, and nothing else (the absent case is counted either way)
    presence = lambda doc: {k: v['presence'] for k, v in tc.cond_by_id(doc['delta']['conditions']).items()}
    assert presence(omitted)['XY@L2'] == 'gone' and presence(copied)['XY@L2'] == 'both'
    assert {k: v for k, v in presence(omitted).items() if k != 'XY@L2'} == {k: v for k, v in presence(copied).items() if k != 'XY@L2'}
    for doc in (omitted, copied):
        assert doc['sides']['target']['absent_expected_cases'] == {'names': ['vertical_short-X-L1-Y-L2'], 'count': 1}
        assert 'absent_expected_cases' not in doc['sides']['reference']


def tc_numbers(doc, role):
    side = doc['sides'][role]
    return side['lambda']['line'], side['lambda']['pixel'], side['vi_line'], side['vi_pixel']
