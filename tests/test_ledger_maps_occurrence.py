"""rnarr/T6: the observed connected-region metric (`occurrence`) of ledger-maps and the layer order (PLAN-v3 2.3, PLAN-v3.1
E3 E5 E6 E7 E10, E11 Z1 Z2)."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from simcompare import ledger_maps as lm
from test_compare2 import PAIRS, base_cases
from test_ledger_maps import chain, load_maps, maps_direct

ROLE_KEYS = ('reference', 'target')


def occ_doc(tmp_path, out='maps', **kwargs):
    code, folder = maps_direct(tmp_path, out, **kwargs)
    assert code in (0, 3)
    return load_maps(folder)


def rows(doc):
    return {(row['pair_id'], row['layer']): row for row in doc['occurrence']['items']}


# ---- the metric on cell sets (no files) ------------------------------------------------------------------------------

def fake_occ(budget, shape=(8, 8), names=('x', 'y'), counts=None):
    ctx = SimpleNamespace(info={'frame': SimpleNamespace(shape_yx=shape)}, role='reference',
                          by_id={name: {'number': index + 1} for index, name in enumerate(names)},
                          items=[{'item_id': name} for name in names])
    return lm._Occurrence(ctx, budget, 3.0, counts), SimpleNamespace(valid=np.ones(shape, dtype=bool))


def flat(cells, width=8):
    return np.array(sorted(y * width + x for y, x in cells), dtype=np.int64)


def measure(*pieces, shape=(8, 8)):
    budget = lm._Budget(10 ** 9)
    occ, frame = fake_occ(budget, shape)
    for piece in pieces:
        occ.arrive('x', frame, flat(piece, shape[1]))
    occ.finish()
    result = occ.result()['x']
    occ.close()
    assert budget.used == 0 and budget.occ_hold.bytes == 0
    return result


def test_two_overlapping_groups_make_one_union():
    result = measure({(3, 3), (3, 4)}, {(3, 4), (3, 5), (4, 5)})
    assert result['state'] == 'observed' and (result['n'], result['cells'], result['largest_cells']) == (1, 4, 4)


def test_two_disjoint_groups_stay_two_regions():
    result = measure({(2, 2), (2, 3)}, {(5, 5)})
    assert (result['n'], result['cells'], result['largest_cells']) == (2, 3, 2)


def test_a_one_cell_wide_link_connects_and_a_diagonal_does_not():
    linked = measure({(2, 2), (2, 3), (3, 3), (4, 3), (4, 4)})
    assert (linked['n'], linked['cells']) == (1, 5)
    diagonal = measure({(2, 2), (3, 3)})
    assert (diagonal['n'], diagonal['cells'], diagonal['largest_cells']) == (2, 2, 1)
    # the pieces of two groups that only touch diagonally are two regions as well
    assert measure({(2, 2)}, {(3, 3)})['n'] == 2


def test_frame_boundary_contact_counts_regions_not_cells():
    interior = measure({(3, 3), (3, 4)})
    assert interior['boundary'] == 0
    edges = measure({(0, 3)}, {(7, 7)}, {(3, 0), (4, 0)}, {(3, 7)}, {(4, 4)})
    assert edges['n'] == 5 and edges['boundary'] == 4          # top, corner, left (two cells, one region), right
    # a region that touches two edges counts once
    assert measure({(0, 0), (0, 1), (1, 0)})['boundary'] == 1


def test_no_cells_is_observed_empty_and_a_missing_arrival_is_not_observed():
    budget = lm._Budget(10 ** 9)
    occ, frame = fake_occ(budget)
    occ.arrive('x', frame, np.zeros(0, dtype=np.int64))        # the sink call of an empty mask (Z1)
    occ.finish()
    result = occ.result()
    assert result['x'] == {'state': 'observed_empty', 'n': 0, 'cells': 0, 'largest_cells': 0, 'boundary': 0}
    assert result['y'] == {'state': 'not_observed'}
    occ.close()


def block(state, n=None, mean=None):
    return {'state': state, 'n': n, 'mean_area_um2': mean}


def test_comparison_wording_follows_the_two_states():
    compare = lm._occ_compare
    assert compare(block('observed', 2, 1.0), block('observed', 3, 0.5))['kind'] == 'both_observed'
    assert compare(block('observed_empty', 0), block('observed', 3, 0.5))['kind'] == 'from_empty'
    assert compare(block('observed', 3, 0.5), block('observed_empty', 0))['kind'] == 'to_empty'
    assert compare(block('observed_empty', 0), block('observed_empty', 0))['kind'] == 'both_empty'
    # new / gone only for a structural absence
    assert compare(block('absent', 0), block('observed', 3, 0.5))['kind'] == 'new'
    assert compare(block('observed', 3, 0.5), block('absent', 0))['kind'] == 'gone'
    assert compare(block('absent', 0), block('observed_empty', 0))['kind'] == 'new'
    # an unobserved or pending side silences the sentence (one side, both sides)
    for state in ('not_observed', 'pending'):
        for other in ('observed', 'observed_empty', 'absent'):
            assert compare(block(state), block(other, 1, 1.0)) == {'kind': 'suppressed', 'reason': state}
            assert compare(block(other, 1, 1.0), block(state)) == {'kind': 'suppressed', 'reason': state}
        assert compare(block(state), block(state)) == {'kind': 'suppressed', 'reason': state}
    assert compare(block('pending'), block('not_observed'))['reason'] == 'pending'


def test_values_use_the_observed_area_and_the_unit_only_when_given():
    state = {'state': 'observed', 'n': 2, 'cells': 30, 'largest_cells': 20, 'boundary': 1}
    value = lm._occ_value(state, 0.1, 0.16, 0.04)
    assert value['n'] == 2 and value['area_um2'] == pytest.approx(0.3) and value['mean_area_um2'] == pytest.approx(0.15)
    assert value['largest_area_um2'] == pytest.approx(0.2) and value['boundary_regions'] == 1
    assert value['n_per_mm2'] == pytest.approx(2 * 1e6 / 0.16) and value['area_fraction'] == pytest.approx(0.3 / 0.16)
    assert value['n_per_unit'] == pytest.approx(2 * 0.04 / 0.16)
    assert lm._occ_value(state, 0.1, 0.16, None)['n_per_unit'] is None
    zero = lm._occ_value(state, 0.1, 0, 0.04)                    # A_obs = 0: no density, no fraction
    assert zero['n_per_mm2'] is None and zero['area_fraction'] is None and zero['n_per_unit'] is None
    empty = lm._occ_value({'state': 'observed_empty', 'n': 0, 'cells': 0, 'largest_cells': 0, 'boundary': 0},
                          0.1, 0.16, 0.04)
    assert empty['n'] == 0 and empty['mean_area_um2'] is None and empty['n_per_mm2'] == 0
    hole = lm._occ_value({'state': 'not_observed'}, 0.1, 0.16, 0.04)
    assert hole['n'] is None and hole['area_um2'] is None and hole['n_per_mm2'] is None


# ---- the budget (E3, Z2) ----------------------------------------------------------------------------------------------

def test_an_existing_reserve_takes_the_occurrence_bytes_back_first():
    """E3 counter-example: limit 100, existing 70, occurrence 20, existing asks 15."""
    budget = lm._Budget(100)
    budget.reserve(70, 'existing')
    occ, frame = fake_occ(budget)
    occ.arrive('x', frame, flat({(1, 1), (1, 2), (1, 3), (1, 4), (1, 5)}))   # 5 cells x 4 bytes = 20
    assert budget.occ_hold.bytes == 20 and budget.used == 90
    budget.reserve(15, 'existing again')
    assert budget.used == 85 and budget.occ_hold.bytes == 0
    occ.finish()
    assert occ.result()['x'] == {'state': 'pending', 'reason': 'budget'}
    # nothing new is held afterwards: a later cell piece makes its item pending, an empty arrival does not
    occ.arrive('y', frame, flat({(2, 2)}))
    assert budget.occ_hold.bytes == 0
    assert occ.result()['y']['state'] == 'pending'
    occ.close()
    assert budget.used == 85 and budget.yielder is None


def test_the_yield_runs_before_the_card_scope_check_and_never_evicts_cards():
    budget = lm._Budget(100)
    budget.reserve(70, 'existing')
    occ, frame = fake_occ(budget)
    occ.arrive('x', frame, flat({(1, 1), (1, 2), (1, 3), (1, 4), (1, 5)}))
    evicted = []
    budget.evictor = lambda: evicted.append(1) or False
    card = lm._Hold()
    budget.begin(card)                                            # a card part is being processed
    budget.reserve(15, 'card array')                              # would raise (no eviction inside a scope) without the yield
    assert budget.used == 85 and card.bytes == 15 and budget.occ_hold.bytes == 0 and not evicted
    budget.end()
    with pytest.raises(lm._BudgetExceeded):                       # nothing held any more: a real overflow still raises
        budget.reserve(30, 'too much')
    occ.close()


def test_occurrence_never_evicts_and_never_enters_a_card_hold():
    budget = lm._Budget(100)
    card = lm._Hold()
    budget.evictor = lambda: pytest.fail('occurrence must not evict')
    budget.begin(card)
    budget.reserve_occ(10, 'cells')
    assert card.bytes == 0 and budget.occ_hold.bytes == 10
    with pytest.raises(lm._BudgetExceeded):
        budget.reserve_occ(95, 'cells')
    budget.release_occ(10)
    assert budget.used == 0 and budget.occ_hold.bytes == 0


def test_an_own_reservation_that_does_not_fit_makes_that_item_pending_only():
    budget = lm._Budget(520)
    occ, frame = fake_occ(budget, shape=(16, 16))
    occ.arrive('x', frame, flat({(1, c) for c in range(1, 8)}, 16))   # 28 bytes: fits
    assert budget.occ_hold.bytes == 28
    occ.arrive('y', frame, flat({(r, c) for r in range(2, 10) for c in range(16)}, 16))   # 512 more: does not fit
    assert budget.occ_hold.bytes == 28 and not occ.reduced
    occ.finish()
    result = occ.result()
    assert result['y'] == {'state': 'pending', 'reason': 'budget'} and result['x']['state'] == 'observed'
    occ.close()
    assert budget.used == 0


def test_one_missing_piece_of_a_multi_group_item_keeps_it_pending_to_the_end():
    budget = lm._Budget(30)
    occ, frame = fake_occ(budget)
    occ.arrive('x', frame, flat({(1, 1), (1, 2), (1, 3)}))        # 12 bytes
    assert budget.occ_hold.bytes == 12
    occ.arrive('x', frame, flat({(3, c) for c in range(1, 6)}))   # 20 more: 32 > 30, the first piece is given back too
    assert budget.occ_hold.bytes == 0
    occ.arrive('x', frame, flat({(5, 5)}))                        # fits, but the item has no partial value
    assert budget.occ_hold.bytes == 0
    occ.finish()
    assert occ.result()['x'] == {'state': 'pending', 'reason': 'budget'}
    occ.close()
    assert budget.used == 0


def test_the_pieces_are_released_after_the_values_and_close_gives_back_the_rest():
    budget = lm._Budget(10 ** 6)
    occ, frame = fake_occ(budget)
    occ.arrive('x', frame, flat({(1, 1), (1, 2)}))
    occ.arrive('y', frame, flat({(4, 4)}))
    assert budget.occ_hold.bytes == 12
    occ.close()                                                   # an error path: nothing stays reserved
    assert budget.used == 0 and budget.occ_hold.bytes == 0 and budget.yielder is None


# ---- the command ------------------------------------------------------------------------------------------------------

def test_the_document_has_the_occurrence_block_with_both_products(tmp_path):
    chain(tmp_path)
    doc = occ_doc(tmp_path)
    occ = doc['occurrence']
    assert occ['diameter_um'] == 3.0 and occ['connectivity'] == '4_neighbour_cells' and occ['status'] == 'ok'
    for role in ROLE_KEYS:
        assert occ['observed_area'][role]['cells'] == 16 and occ['observed_area'][role]['basis'] == 'valid_mask'
        assert occ['observed_area'][role]['um2'] == pytest.approx(0.16)
        assert sum(occ['counts'][role].values()) == len(occ['items'])
    by_item = rows(doc)
    ab = by_item[('AB', 'L1')]
    assert ab['reference']['state'] == 'observed' and ab['reference']['n'] == 1
    assert ab['reference']['area_um2'] == pytest.approx(0.01) and ab['reference']['boundary_regions'] == 1
    assert ab['reference']['n_per_mm2'] == pytest.approx(1e6 / 0.16) and ab['reference']['area_fraction'] == pytest.approx(0.0625)
    assert ab['compare']['kind'] == 'both_observed'
    # AC has no cell at the report diameter: the sink came with an empty mask (observed, no region), not "not observed"
    ac = by_item[('AC', 'L1')]
    assert ac['reference']['state'] == 'observed_empty' and ac['reference']['n'] == 0
    assert ac['reference']['mean_area_um2'] is None and ac['compare']['kind'] == 'both_empty'
    assert occ['counts']['reference']['not_observed'] == 0 and occ['counts']['reference']['observed_empty'] == 3


def test_a_unit_from_the_config_gives_the_unit_count_and_a_derived_unit_does_not(tmp_path):
    chain(tmp_path)
    doc = occ_doc(tmp_path)
    assert doc['occurrence']['unit_conversion'] == {'applies': True, 'reason': None}
    assert rows(doc)[('AB', 'L1')]['reference']['n_per_unit'] == pytest.approx(1 * 0.2 * 0.2 / 0.16)
    other = tmp_path / 'derived'
    other.mkdir()
    chain(other, unit=None)                                       # the unit window is derived from the frame
    doc = occ_doc(other)
    assert doc['window']['reference']['unit_source'] == 'derived'
    assert doc['occurrence']['unit_conversion']['applies'] is False
    for row in doc['occurrence']['items']:
        assert row['reference']['n_per_unit'] is None and row['target']['n_per_unit'] is None
    assert rows(doc)[('AB', 'L1')]['reference']['n_per_mm2'] == pytest.approx(1e6 / 0.16)


def test_absent_items_and_the_other_side_are_told_apart(tmp_path):
    chain(tmp_path, tgt=(base_cases(AC=None), PAIRS))              # AC exists on the reference only
    doc = occ_doc(tmp_path)
    ac = rows(doc)[('AC', 'L1')]
    assert ac['presence'] == 'gone' and ac['target']['state'] == 'absent' and ac['target']['n'] == 0
    assert ac['reference']['state'] == 'observed_empty' and ac['compare']['kind'] == 'gone'
    assert doc['occurrence']['counts']['target']['absent'] == 1


def test_the_map_options_do_not_change_the_metric(tmp_path):
    chain(tmp_path)
    first = occ_doc(tmp_path)
    second = occ_doc(tmp_path, 'm2', options={'tile_um': 0.5, 'map_px_um': 0.4, 'range_um': 0.2})
    assert second['options']['tile_um'] == 0.5
    assert second['occurrence'] == first['occurrence']


def test_the_occurrence_hold_is_zero_at_the_end_and_the_run_is_clean(tmp_path, monkeypatch):
    budgets = []
    original = lm._Budget.__init__
    monkeypatch.setattr(lm._Budget, '__init__', lambda self, limit: (original(self, limit), budgets.append(self))[0])
    chain(tmp_path)
    doc = occ_doc(tmp_path)
    assert doc['status'] == 'ok' and 'occurrence_budget_reduced' not in doc['reasons']
    (budget,) = budgets
    assert budget.occ_hold.bytes == 0 and budget.yielder is None and budget.occ_peak > 0


def png_bytes(folder):
    return {path.name: path.read_bytes() for path in sorted(folder.iterdir()) if path.suffix == '.png'}


def test_a_tight_budget_gives_the_occurrence_up_and_leaves_maps_and_cards_unchanged(tmp_path, monkeypatch):
    """E3: what fits without the occurrence metric gives the same maps, cards and checks with it."""
    chain(tmp_path)
    monkeypatch.setattr(lm, 'OCCURRENCE_ENABLED', False)
    code, base = maps_direct(tmp_path, 'base')
    assert code == 0
    limit = json.loads((base / 'execution.json').read_text())['max_reserved_bytes']
    base_doc = load_maps(base)
    assert 'occurrence' not in base_doc
    monkeypatch.setattr(lm, 'OCCURRENCE_ENABLED', True)
    # the existing operations need exactly `limit`: every occurrence byte still held at their peak has to go
    code, tight = maps_direct(tmp_path, 'tight', max_operation_bytes=limit)
    assert code == 3
    doc = load_maps(tight)
    assert doc['status'] == 'partial' and doc['reasons'] == ['occurrence_budget_reduced']
    assert any(band['code'] == 'occurrence_budget_reduced' for band in doc['bands'])
    assert doc['occurrence']['status'] == 'partial' and doc['occurrence']['counts']['target']['pending'] > 0
    pending = [row for row in doc['occurrence']['items'] if row['compare']['kind'] == 'suppressed']
    assert pending and all(row['compare']['reason'] == 'pending' for row in pending)
    assert png_bytes(tight) == png_bytes(base)
    for key in ('cards', 'sides', 'checks', 'window', 'cards_dropped_for_budget', 'changes', 'overlay'):
        assert doc[key] == base_doc[key], key
    # a roomy budget keeps every value
    code, roomy = maps_direct(tmp_path, 'roomy')
    assert code == 0 and load_maps(roomy)['occurrence']['status'] == 'ok'
    assert png_bytes(roomy) == png_bytes(base)
    # rnarr/T6b: the record says what the metric needed and why items are missing
    record = doc['occurrence']['budget']
    assert record['occ_peak_bytes'] > 0 and record['hold_mode'] == 'immediate' and record['yielded_at_bytes'] is None
    band = [b for b in doc['bands'] if b['code'] == 'occurrence_budget_reduced'][0]
    assert 'more is needed for the connected-region metric' in band['detail']
    assert load_maps(roomy)['occurrence']['budget']['occ_peak_bytes'] == record['occ_peak_bytes']


# ---- rnarr/T6b: single pieces are never held, the last piece completes, the yield is run-wide ------------------------

def test_a_single_piece_item_is_measured_at_once_and_never_held():
    budget = lm._Budget(10 ** 9)
    occ, frame = fake_occ(budget, counts={'x': 1})
    occ.arrive('x', frame, flat({(3, 3), (3, 4)}))
    assert budget.occ_hold.bytes == 0 and budget.used == 0 and budget.occ_peak > 0     # temporaries only
    assert occ.pieces == {} and occ.values['x'][:3] == (1, 2, 2)
    assert not budget.occ_deferred
    occ.finish()
    assert occ.result()['x']['state'] == 'observed'
    occ.close()


def test_a_multi_piece_item_is_measured_when_its_last_piece_comes():
    budget = lm._Budget(10 ** 9)
    occ, frame = fake_occ(budget, counts={'x': 2})
    occ.arrive('x', frame, flat({(3, 3), (3, 4)}))
    assert budget.occ_hold.bytes > 0 and 'x' not in occ.values
    occ.arrive('x', frame, flat({(3, 4), (3, 5), (4, 5)}))
    assert budget.occ_hold.bytes == 0 and budget.used == 0                             # given back before finish
    assert occ.values['x'][:3] == (1, 4, 4) and not budget.occ_deferred
    occ.close()


def test_an_unknown_piece_count_is_held_to_the_end_and_recorded_as_deferred():
    budget = lm._Budget(10 ** 9)
    occ, frame = fake_occ(budget)
    occ.arrive('x', frame, flat({(3, 3)}))
    occ.arrive('x', frame, flat({(5, 5)}))
    assert budget.occ_hold.bytes > 0 and 'x' not in occ.values and budget.occ_deferred
    occ.finish()
    assert occ.values['x'][0] == 2 and budget.occ_hold.bytes == 0
    occ.close()
    # fewer pieces than counted: held to `finish`, still deferred
    budget = lm._Budget(10 ** 9)
    occ, frame = fake_occ(budget, counts={'x': 3})
    occ.arrive('x', frame, flat({(3, 3)}))
    assert not budget.occ_deferred
    occ.finish()
    assert budget.occ_deferred and occ.values['x'][0] == 1
    occ.close()


def test_a_piece_after_the_counted_ones_leaves_the_item_pending_not_wrong():
    budget = lm._Budget(10 ** 9)
    occ, frame = fake_occ(budget, counts={'x': 1})
    occ.arrive('x', frame, flat({(3, 3)}))
    occ.arrive('x', frame, flat({(5, 5)}))
    occ.finish()
    assert occ.result()['x'] == {'state': 'pending', 'reason': 'piece_count_mismatch'}
    assert budget.occ_mismatch == 1
    occ.close()


def test_a_yield_in_one_product_stops_holding_in_the_next():
    budget = lm._Budget(100)
    budget.reserve(70, 'existing')
    first, frame = fake_occ(budget)
    first.arrive('x', frame, flat({(1, 1), (1, 2), (1, 3), (1, 4), (1, 5)}))
    budget.reserve(15, 'existing again')                        # takes the 20 held bytes back
    assert budget.occ_reduced and budget.occ_yielded_at == 20
    first.close()
    second, frame = fake_occ(budget, counts={'x': 2})           # the next product, same budget
    second.arrive('x', frame, flat({(2, 2)}))
    assert budget.occ_hold.bytes == 0 and second.result()['x'] == {'state': 'pending', 'reason': 'budget'}
    budget.release(60, 'existing')                              # room for the temporaries of one measurement
    single, frame = fake_occ(budget, counts={'x': 1})           # a single piece holds nothing, so it still gets a value
    single.arrive('x', frame, flat({(2, 2)}))
    assert single.result()['x']['state'] == 'observed'
    second.close()
    single.close()


def test_a_yield_that_gives_back_no_byte_returns_false_and_the_reserve_does_not_loop():
    budget = lm._Budget(100)
    occ, frame = fake_occ(budget)
    budget.reserve_occ(10, 'temporary of somebody else')       # occurrence bytes that are not held pieces
    assert occ.give_back() is False and not budget.occ_reduced
    budget.reserve(80, 'existing')
    with pytest.raises(lm._BudgetExceeded):
        budget.reserve(30, 'too much')                          # would loop for ever if the yield kept answering True
    occ.close()


def test_the_coordinate_arrays_are_reserved_before_they_are_built():
    budget = lm._Budget(79)
    cells = flat({(1, 1), (1, 2), (1, 3), (1, 4), (1, 5)})      # 5 cells: y/x arrays need 80 bytes
    with pytest.raises(lm._BudgetExceeded, match='coordinates'):
        lm._measure_regions(cells, (8, 8), budget, 'item')
    assert budget.used == 0 and budget.occ_hold.bytes == 0
    budget = lm._Budget(10 ** 6)
    assert lm._measure_regions(cells, (8, 8), budget, 'item')[:3] == (1, 5, 5)
    assert budget.used == 0


def test_piece_counts_come_from_the_by_size_rows_of_the_map_diameter():
    def row(diameter, area_a, area_b, layer='D1'):
        return {'diameter_um': diameter, 'density_layer': layer, 'area_a': area_a, 'area_b': area_b,
                'lambda_a': 0.0, 'lambda_b': 0.0}
    led = {'pairs': [{'pair_id': 'P', 'items': [
        {'item_id': 'one', 'presence': 'both', 'by_size': [row(3.0, 1.0, 2.0), row(5.0, 1.0, 1.0)]},
        {'item_id': 'two', 'presence': 'both', 'by_size': [row(3.0, 1.0, 2.0), row(3.0, 1.0, 2.0, 'D2')]},
        {'item_id': 'only_b', 'presence': 'new', 'by_size': [row(3.0, 0.0, 2.0)]},
        {'item_id': 'none', 'presence': 'both', 'by_size': [row(5.0, 1.0, 1.0)]}]}]}
    assert lm._occ_piece_counts(led, 'reference', 3.0) == {'one': 1, 'two': 2}
    assert lm._occ_piece_counts(led, 'target', 3.0) == {'one': 1, 'two': 2, 'only_b': 1}


# ---- layer order (E10) ------------------------------------------------------------------------------------------------

def test_the_layer_key_follows_the_order_then_the_name():
    ledger = {'inputs': {'layer_order': ['SD1', 'SD2', 'PXL']}}
    assert sorted(['PXL', 'SD2', 'ZZ', 'SD1', 'AA'], key=lm._layer_key(ledger)) == ['SD1', 'SD2', 'PXL', 'AA', 'ZZ']
    assert sorted(['PXL', 'SD2', 'SD1'], key=lm._layer_key({'inputs': {}})) == ['PXL', 'SD1', 'SD2']
    assert lm._layer_order({'inputs': {'layer_order': 'SD1'}}) is None


def set_layer_order(tmp_path, order):
    path = tmp_path / 'led' / 'ledger.json'
    doc = json.loads(path.read_text(encoding='utf-8'))
    doc['inputs']['layer_order'] = order
    path.write_text(json.dumps(doc), encoding='utf-8')


def test_the_map_layers_follow_the_ledger_layer_order(tmp_path):
    chain(tmp_path)
    doc = occ_doc(tmp_path)
    assert doc['inputs']['layer_order'] == ['L1', 'L2', 'P1', 'P2']       # copied from ledger.json (T5)
    assert [m['layer'] for m in doc['sides']['reference']['maps'] if m['kind'] == 'it'] == ['L1', 'L2']
    set_layer_order(tmp_path, ['L2', 'L1'])
    doc = occ_doc(tmp_path, 'm2')
    assert doc['inputs']['layer_order'] == ['L2', 'L1']
    for role in ROLE_KEYS:
        maps = sorted((m for m in doc['sides'][role]['maps'] if m['kind'] == 'it'), key=lambda m: m['file'])
        assert [m['layer'] for m in maps] == ['L2', 'L1']
