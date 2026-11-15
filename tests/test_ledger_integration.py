"""ledger/T7 (opus adv:1): the real `compare2` command feeds the real `ledger` command.

test_ledger.py builds compare2.json by hand; these tests run `compare2` on the small real_hotspot_v1 fixtures of
test_compare2.py and then `ledger` on its output folder, so a drift between the two modules (item ids, null rules,
stale checks) fails here."""
import json

import pytest

from simcompare import ledger
from test_compare2 import CNO_CSV, CNO_NAME, PAIRS, base_cases, run, two_sides

EQUATIONS = {'eq1_line_a', 'eq2_line_b', 'eq3_line_delta', 'eq4_pixel_a', 'eq5_pixel_b', 'eq6_pixel_delta'}
THK = 'layers\tdistance\nL1L2\t0.5\n'
VERTICAL = ('vertical_short', 'X', 'L1', 'Y', 'L2', {1: set(), 2: {3, 4}, 3: {3, 4, 5}})
CNO_B = 'defect_size(um),Area(um2)\n1,0.5\n2,1.5\n3,5.0\n'


def ledger_of(tmp_path, out):
    ledger_out = tmp_path / 'ledger_out'
    code = ledger.main_ledger(str(out), str(tmp_path / 'compare2.yaml'), str(ledger_out), lambda record: None)
    assert code == 0, (ledger_out / 'error.json').read_text(encoding='utf-8') if (ledger_out / 'error.json').exists() else code
    return json.loads((ledger_out / 'ledger.json').read_text(encoding='utf-8'))


def assert_sums_passed(doc):
    checks = doc['checks']
    assert checks['status'] == 'passed', json.dumps(checks, indent=1)
    assert set(checks['equations']) == EQUATIONS
    assert [name for name, e in checks['equations'].items() if e['status'] != 'passed'] == []
    for kind in ('line', 'pixel'):
        # sum over electrodes = sum over pairs (+ CNO for pixel); sum of the pair deltas = delta.conditions + delta.shared
        assert {f: e['status'] for f, e in checks['electrode_sum'][kind].items()} == {
            'a': 'passed', 'b': 'passed', 'delta': 'passed'}
        assert checks['pair_delta_vs_compare2'][kind]['status'] == 'passed'
    assert all(e['status'] == 'passed' for kind in checks['layer_size_sum'].values() for e in kind.values())
    assert doc['status'] == 'ok'


def test_basic_variant_compare2_then_ledger_passes_every_sum(tmp_path):
    ref, tgt = two_sides()
    code, out = run(tmp_path, ref, tgt, ref_cno={CNO_NAME: CNO_CSV}, tgt_cno={CNO_NAME: CNO_B})
    assert code == 0
    doc = ledger_of(tmp_path, out)
    assert_sums_passed(doc)
    assert doc['kinds']['line']['computable'] and doc['kinds']['pixel']['computable']
    assert len(doc['cno']) == 1 and len(doc['pairs']) == len(PAIRS)
    assert doc['totals']['pixel']['crp']['present'] is False


def test_different_crp_variant_mobile_vs_other_passes_every_sum(tmp_path):
    ref, tgt = two_sides()
    code, out = run(tmp_path, ref, tgt, ref_group='mobile', tgt_group='auto',
                    ref_cno={CNO_NAME: CNO_CSV}, tgt_cno={CNO_NAME: CNO_B})
    assert code == 0
    doc = ledger_of(tmp_path, out)
    assert_sums_passed(doc)
    assert doc['inputs']['products']['reference']['crp'] == 1.0 and doc['inputs']['products']['target']['crp'] == 2.92
    crp = doc['totals']['pixel']['crp']
    assert crp['present'] is True and crp['a'] == -crp['delta'] and crp['b'] == 0.0
    # the column totals equal the real vi (the CRP row carries the difference)
    assert doc['totals']['pixel']['a'] == pytest.approx(doc['totals']['pixel']['vi_a'])
    assert doc['totals']['pixel']['b'] == pytest.approx(doc['totals']['pixel']['vi_b'])


def test_vertical_short_with_thk_distance_filter_variant_passes_every_sum(tmp_path):
    pairs = PAIRS + [('XY', 'X', 'Y', 'line')]
    ref = (base_cases() + [VERTICAL], pairs)
    tgt_vertical = ('vertical_short', 'X', 'L1', 'Y', 'L2', {1: set(), 2: {3, 4, 5}, 3: {3, 4, 5, 6}})
    tgt = (base_cases(AB={1: {0, 1}, 2: {0}, 3: {0}}) + [tgt_vertical], pairs)
    code, out = run(tmp_path, ref, tgt, thk=THK, ref_cno={CNO_NAME: CNO_CSV}, tgt_cno={CNO_NAME: CNO_B})
    assert code == 0
    doc = ledger_of(tmp_path, out)
    assert_sums_passed(doc)
    xy = {row['pair_id']: row for row in doc['pairs']}['XY']
    assert [item['endpoints'] for item in xy['items']] == [[{'electrode_id': 'X', 'actual_layer': 'L1'},
                                                            {'electrode_id': 'Y', 'actual_layer': 'L2'}]]
    assert xy['items'][0]['upper_layer'] == 'L2' and xy['items'][0]['lower_layer'] == 'L1'


def test_vertical_short_variant_with_the_diameter_filter_passes_every_sum(tmp_path):
    pairs = PAIRS + [('XY', 'X', 'Y', 'line')]
    ref = (base_cases() + [VERTICAL], pairs)
    code, out = run(tmp_path, ref, ref, thk=THK, diameters=[3])
    assert code == 0
    assert_sums_passed(ledger_of(tmp_path, out))


def test_diameter_filter_that_removes_every_cell_of_an_item_reaches_the_ledger_filter_path(tmp_path):
    # no 3 um input exists at all, so the filter (3 um only) removes every cell of every item: a true 0 per item
    # (T8 adv:2). compare2 exits 3 (nothing left to compute); the ledger still runs and marks each item filtered.
    pairs = PAIRS + [('XY', 'X', 'Y', 'line')]
    ref = (base_cases() + [VERTICAL], pairs)
    code, out = run(tmp_path, ref, ref, thk=THK, diameters=[3], skip=(3,))
    assert code == 3
    raw = json.loads((out / 'compare2.json').read_text(encoding='utf-8'))['sides']['reference']['ledger_items']
    assert raw and all(item['reasons'] == ['diameter_filter_removed_all'] for item in raw)
    doc = ledger_of(tmp_path, out)
    items = [item for row in doc['pairs'] for item in row['items']]
    assert len(items) == len(raw)
    assert all(item['filtered_out'] and item['a_filtered'] and item['b_filtered'] for item in items)
    # same item ids on both sides, so the layer keys agree; the values are not computable here (nothing left)
    assert all(row['change_kind'] == 'value_pending' for row in doc['pairs'])
