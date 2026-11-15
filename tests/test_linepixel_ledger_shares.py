"""ledger/T2: GroupArea.ledger_shares (1/n pair shares split by item, integer checks) under condition_stats."""
import json
import math
import random

import pytest

import numpy as np

from simcompare.linepixel import _count_dtype, compute_areas
from simcompare.numeric_schema import DIAMETERS_UM, CaseIdentity, Endpoint, Frame, MaskRecord, NumericCase
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

def make_case_nd(case_id, a, b, cells, kind, *, height, width, valid, failure_type, layers):
    """Like test_linepixel.make_case with a 2D frame; cells are flat indices, layers = (layer of a, layer of b)."""
    frame = Frame('frame', 1, (0, 0), (height, width), valid)
    identity = CaseIdentity('product', 'design', 'source', 'step', 'exposure', case_id, 'frame', 'synthetic_v2')
    endpoints = (Endpoint(a, 'gate', layers[0]), Endpoint(b, 'data', layers[1]))
    raw = {'case_id': case_id, 'fab_pair_key': f'RAW:{a}-{b}', 'assigned_process_layer': 'step',
           'ordered_endpoints': [vars(ep) for ep in endpoints]}
    vertical = failure_type == 'vertical_short'
    records, metadata = {}, {'unit_id': 'unit', 'height_um': 3 if vertical else None,
                             'height_evidence': 'synthetic height' if vertical else None, 'records': []}
    for d in DIAMETERS_UM:
        mask = np.zeros(height * width, bool)
        if d == 5:
            mask[list(cells)] = True
        na = vertical and d < 3
        records[d] = MaskRecord(d, frame, None if na else mask.reshape(height, width),
                                'not_applicable' if na else 'observed',
                                ('below_height_vertical_short',) if na else (), identity=identity,
                                analysis_layer='M2')
        metadata['records'].append({'diameter_um': d, 'applicability_evidence': {
            'height_um': 3, 'comparison': 'diameter_um < height_um', 'source': 'synthetic'} if na else None})
    return NumericCase(identity, endpoints, failure_type, '-'.join(sorted((a, b))), kind, 'M2', 'density-M2',
                       records, raw, metadata)


def endpoints_key(a, b, layers):
    return tuple(sorted(((a, layers[0]), (b, layers[1]))))


def reference(specs, valid_cells):
    """specs: [(pid, failure_type, kind, set(cells), endpoints_key, (upper, lower))]. Per kind: shares, hist, overlap."""
    pair_cells = {'line': {}, 'pixel': {}}
    item_cells = {'line': {}, 'pixel': {}}
    for pid, ftype, kind, cols, ekey, _layers in specs:
        pair_cells[kind].setdefault(pid, set()).update(cols)
        item_cells[kind].setdefault((pid, ftype, ekey), set()).update(cols)
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
    height = rng.choice([1, 2, 3, 4])
    width = rng.choice([5, 7, 9, 13, 16])
    total = height * width
    valid = [[1 if rng.random() > 0.15 else 0 for _ in range(width)] for _ in range(height)]
    valid_cells = {r * width + c for r in range(height) for c in range(width) if valid[r][c]}
    cases, specs = [], []
    for p in range(rng.randint(1, 40)):
        kind = rng.choice(['line', 'pixel'])
        a, b = f'A{p:03}', f'B{p:03}'
        pid = f'{a}-{b}'
        for s in range(rng.choice([1, 1, 2, 3, 4])):
            ftype = rng.choice(['lateral_short', 'vertical_short'])
            if ftype == 'vertical_short':
                lower = rng.choice(['M1', 'M0'])
                layers = ('M2', lower) if rng.random() < 0.5 else (lower, 'M2')   # direction mixed
                upper_lower = ('M2', lower)
            else:
                layers, upper_lower = ('M2', 'M2'), ('M2', 'M2')
            cols = {c for c in range(total) if rng.random() < rng.choice([0.1, 0.3, 0.6])}
            cases.append(make_case_nd(f'c{p}-{s}', a, b, cols, kind, height=height, width=width, valid=valid,
                                      failure_type=ftype, layers=layers))
            specs.append((pid, ftype, kind, cols, endpoints_key(a, b, layers), upper_lower))
    return cases, specs, valid_cells


def ikey_of(item):
    return item['pair_id'], item['failure_type'], tuple((e['electrode_id'], e['actual_layer']) for e in item['endpoints'])


@pytest.mark.parametrize('seed', range(300))
def test_random_against_reference_and_integer_checks(seed):
    rng = random.Random(seed)
    cases, specs, valid_cells = build_random(rng)
    ledger = shares_of(cases)
    assert_checks(ledger)
    expected = reference(specs, valid_cells)
    layer_of = {(pid, ftype, ekey): ul for pid, ftype, _kind, _cols, ekey, ul in specs}
    for kind in ('line', 'pixel'):
        share, hist, overlap = expected[kind]
        got = {ikey_of(i): i['share_cells'] for i in ledger[kind]['items']}
        assert set(got) == set(share)
        for ikey, value in share.items():
            assert got[ikey] == pytest.approx(value, rel=1e-9, abs=1e-12)
        for i in ledger[kind]['items']:
            assert (i['upper_layer'], i['lower_layer']) == layer_of[ikey_of(i)]
        assert ledger[kind]['pair_hist'] == hist
        assert ledger[kind]['multi_item_overlap_cells'] == overlap
        keys = [ikey_of(i) for i in ledger[kind]['items']]
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


def test_j_swapped_direction_is_two_items():
    # A@M1,B@M2 and A@M2,B@M1 are different items of one pair; they share cell 0 (n=2 pairs -> 1/(2*2) each)
    cases = [make_case('d1', 'A', 'B', {0, 1}, failure_type='vertical_short'),
             Case_swapped('d2', {0, 2}), make_case('cd', 'C', 'D', {0})]
    ledger = shares_of(cases)
    assert_checks(ledger)
    items = [i for i in ledger['line']['items'] if i['pair_id'] == 'A-B']
    assert len(items) == 2
    assert {tuple((e['electrode_id'], e['actual_layer']) for e in i['endpoints']) for i in items} == {
        (('A', 'M1'), ('B', 'M2')), (('A', 'M2'), ('B', 'M1'))}
    assert all((i['upper_layer'], i['lower_layer']) == ('M2', 'M1') for i in items)
    # cell 0: n=2, m=2 -> 1/4 each; cells 1 / 2: n=1, m=1 -> 1/2... pair has n=1 so item gets 1
    assert sorted(i['share_cells'] for i in items) == pytest.approx([1.25, 1.25])
    assert ledger['line']['multi_item_overlap_cells'] == 1
    assert ledger['line']['pair_hist']['A-B'] == {1: 2, 2: 1}


def Case_swapped(case_id, cells):
    return make_case_nd(case_id, 'A', 'B', cells, 'line', height=1, width=6, valid=[[1] * 6],
                        failure_type='vertical_short', layers=('M2', 'M1'))


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
