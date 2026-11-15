"""ledger/T2: GroupArea.ledger_shares (1/n pair shares split by item, integer checks) under condition_stats."""
import json
import math
import random

import pytest

from simcompare.linepixel import _count_dtype, compute_areas
from test_linepixel import group, make_case


def shares_of(cases, **kw):
    return group(compute_areas(cases, condition_stats=True, **kw)).ledger_shares


def item_of(ledger, kind, pid, failure_type='lateral_short', upper='M2', lower='M2'):
    found = [i for i in ledger[kind]['items']
             if (i['pair_id'], i['failure_type'], i['upper_layer'], i['lower_layer']) == (pid, failure_type, upper, lower)]
    assert len(found) == 1
    return found[0]


def assert_checks(ledger):
    for kind in ('line', 'pixel'):
        checks = ledger[kind]['checks']
        assert checks['exclusive_ok'] and checks['divisible_ok'] and checks['shared_ok'] and checks['sum_ok']


# ---------- independent per-cell reference ----------

def reference(specs, width, valid):
    """specs: [(pid, failure_type, kind, set(columns))]. Returns per kind {item key: share}, pair hist, overlap."""
    valid_cells = {c for c in range(width) if valid[c]}
    pair_cells = {'line': {}, 'pixel': {}}
    item_cells = {'line': {}, 'pixel': {}}
    for pid, ftype, kind, cols in specs:
        upper, lower = ('M2', 'M1') if ftype == 'vertical_short' else ('M2', 'M2')
        pair_cells[kind].setdefault(pid, set()).update(cols)
        item_cells[kind].setdefault((pid, ftype, upper, lower), set()).update(cols)
    line = set().union(*pair_cells['line'].values()) & valid_cells if pair_cells['line'] else set()
    keeps = {'line': valid_cells, 'pixel': valid_cells - line}
    out = {}
    for kind in ('line', 'pixel'):
        keep = keeps[kind]
        share = {ikey: 0.0 for ikey in item_cells[kind]}
        hist = {pid: {} for pid in pair_cells[kind]}
        overlap = 0
        for cell in keep:
            covering = [pid for pid, cells in pair_cells[kind].items() if cell in cells]
            n = len(covering)
            for pid in covering:
                hist[pid][n] = hist[pid].get(n, 0) + 1
                items = [ikey for ikey, cells in item_cells[kind].items() if ikey[0] == pid and cell in cells]
                overlap += len(items) >= 2
                for ikey in items:
                    share[ikey] += 1.0 / (n * len(items))
        out[kind] = (share, hist, overlap)
    return out


def build_random(rng):
    width = rng.choice([5, 7, 9, 13, 16, 21, 33, 37])
    valid = [1 if rng.random() > 0.15 else 0 for _ in range(width)]
    cases, specs = [], []
    for p in range(rng.randint(1, 80)):
        kind = rng.choice(['line', 'pixel'])
        pid = f'A{p:03}-B{p:03}'
        for s in range(rng.choice([1, 1, 2, 3])):
            ftype = rng.choice(['lateral_short', 'vertical_short'])
            cols = {c for c in range(width) if rng.random() < rng.choice([0.1, 0.3, 0.6])}
            cases.append(make_case(f'c{p}-{s}', f'A{p:03}', f'B{p:03}', cols, kind, width=width, valid=[valid],
                                   failure_type=ftype))
            specs.append((pid, ftype, kind, cols))
    return cases, specs, width, valid


@pytest.mark.parametrize('seed', range(300))
def test_random_against_reference_and_integer_checks(seed):
    rng = random.Random(seed)
    cases, specs, width, valid = build_random(rng)
    ledger = shares_of(cases)
    assert_checks(ledger)
    expected = reference(specs, width, valid)
    for kind in ('line', 'pixel'):
        share, hist, overlap = expected[kind]
        got = {(i['pair_id'], i['failure_type'], i['upper_layer'], i['lower_layer']): i['share_cells']
               for i in ledger[kind]['items']}
        assert set(got) == set(share)
        for ikey, value in share.items():
            assert got[ikey] == pytest.approx(value, rel=1e-9, abs=1e-12)
        assert ledger[kind]['pair_hist'] == hist
        assert ledger[kind]['multi_item_overlap_cells'] == overlap
        keys = [(i['pair_id'], i['failure_type'], i['upper_layer'], i['lower_layer']) for i in ledger[kind]['items']]
        assert keys == sorted(keys)


# ---------- counterexamples ----------

def test_a_same_pair_lateral_and_vertical_same_cells():
    cases = [make_case('lat', 'A', 'B', {0, 1}), make_case('ver', 'A', 'B', {0, 1}, failure_type='vertical_short'),
             make_case('cd', 'C', 'D', {0})]
    ledger = shares_of(cases)
    assert_checks(ledger)
    lat = item_of(ledger, 'line', 'A-B')
    ver = item_of(ledger, 'line', 'A-B', 'vertical_short', 'M2', 'M1')
    # cell 0: n=2 -> pair 1/2, 2 items -> 1/4 each; cell 1: n=1 -> 1/2 each
    assert lat['share_cells'] == pytest.approx(0.75) and ver['share_cells'] == pytest.approx(0.75)
    assert item_of(ledger, 'line', 'C-D')['share_cells'] == pytest.approx(0.5)
    assert ledger['line']['multi_item_overlap_cells'] == 2
    assert ledger['line']['pair_hist']['A-B'] == {1: 1, 2: 1}
    assert ver['endpoints'] == [{'electrode_id': 'A', 'actual_layer': 'M1'}, {'electrode_id': 'B', 'actual_layer': 'M2'}]


def test_b_pixel_pair_fully_covered_by_line():
    ledger = shares_of([make_case('l', 'A', 'B', {0, 1, 2}), make_case('p', 'C', 'D', {1, 2}, 'pixel')])
    assert_checks(ledger)
    item = item_of(ledger, 'pixel', 'C-D')
    assert item['share_cells'] == 0 and item['own_cells'] == 0 and item['transferred_out_cells'] == 2
    assert ledger['pixel']['pair_hist']['C-D'] == {}
    assert item_of(ledger, 'line', 'A-B')['transferred_out_cells'] == 0


def test_c_two_line_and_two_pixel_pairs_one_cell():
    cases = [make_case('l1', 'A', 'B', {0}), make_case('l2', 'C', 'D', {0}),
             make_case('p1', 'E', 'F', {0}, 'pixel'), make_case('p2', 'G', 'H', {0}, 'pixel')]
    ledger = shares_of(cases)
    assert_checks(ledger)
    assert item_of(ledger, 'line', 'A-B')['share_cells'] == 0.5
    assert item_of(ledger, 'line', 'C-D')['share_cells'] == 0.5
    for pid in ('E-F', 'G-H'):
        item = item_of(ledger, 'pixel', pid)
        assert item['share_cells'] == 0 and item['transferred_out_cells'] == 1


def test_d_cells_outside_valid_are_excluded():
    cases = [make_case('l', 'A', 'B', {1, 2, 3}, width=6, valid=[[1, 1, 0, 0, 1, 1]])]
    ledger = shares_of(cases)
    assert_checks(ledger)
    item = item_of(ledger, 'line', 'A-B')
    assert item['share_cells'] == 1 and item['own_cells'] == 1
    assert ledger['line']['pair_hist']['A-B'] == {1: 1}


def test_e_pair_with_no_observed_cells_stays_listed():
    ledger = shares_of([make_case('l', 'A', 'B', set()), make_case('m', 'C', 'D', {1})])
    assert_checks(ledger)
    item = item_of(ledger, 'line', 'A-B')
    assert item['share_cells'] == 0 and item['own_cells'] == 0
    assert ledger['line']['pair_hist']['A-B'] == {}


def test_f_kind_without_pairs_is_empty():
    ledger = shares_of([make_case('l', 'A', 'B', {0, 1})])
    assert_checks(ledger)
    assert ledger['pixel']['items'] == [] and ledger['pixel']['pair_hist'] == {}
    assert ledger['pixel']['multi_item_overlap_cells'] == 0
    assert ledger['pixel']['checks']['expected_cells'] == 0


def test_g_two_sources_with_the_same_item_key_are_or_ed():
    ledger = shares_of([make_case('s1', 'A', 'B', {0, 1}), make_case('s2', 'A', 'B', {1, 2})])
    assert_checks(ledger)
    assert len(ledger['line']['items']) == 1
    item = ledger['line']['items'][0]
    assert item['own_cells'] == 3 and item['share_cells'] == 3


def test_h_m_differs_per_cell():
    cases = [make_case('lat', 'A', 'B', {0, 1, 2}), make_case('ver', 'A', 'B', {2, 3}, failure_type='vertical_short')]
    ledger = shares_of(cases)
    assert_checks(ledger)
    assert item_of(ledger, 'line', 'A-B')['share_cells'] == pytest.approx(2.5)
    assert item_of(ledger, 'line', 'A-B', 'vertical_short', 'M2', 'M1')['share_cells'] == pytest.approx(1.5)
    assert ledger['line']['multi_item_overlap_cells'] == 1


def test_i_counter_dtype_boundary():
    import numpy as np
    assert _count_dtype(0) is np.uint16 and _count_dtype(65535) is np.uint16
    assert _count_dtype(65536) is np.int32


def test_checks_hist_integer_rules():
    cases = [make_case(f'c{i}', f'A{i}', f'B{i}', set(range(i + 1))) for i in range(5)]
    ledger = shares_of(cases)
    assert_checks(ledger)
    hist = ledger['line']['checks']['n_hist']
    assert all(cells % n == 0 for n, cells in hist.items() if n >= 2)
    assert math.isclose(ledger['line']['checks']['share_sum'], ledger['line']['checks']['expected_cells'])


# ---------- default path ----------

def test_condition_stats_false_has_no_ledger_and_serialization_is_unchanged():
    cases = [make_case('lat', 'A', 'B', {0, 1}), make_case('ver', 'A', 'B', {1, 2}, failure_type='vertical_short'),
             make_case('p', 'C', 'D', {2, 3}, 'pixel')]
    off = group(compute_areas(cases))
    on = group(compute_areas(cases, condition_stats=True))
    assert off.ledger_shares is None and off.condition_stats is None
    assert on.ledger_shares is not None
    dumped = on.to_dict()
    assert 'ledger_shares' not in dumped and 'ledger_shares' not in json.dumps(dumped)
    dumped.pop('condition_stats')
    assert json.dumps(dumped, sort_keys=True) == json.dumps(off.to_dict(), sort_keys=True)
