"""mrep/T3: `ledger_maps_v2` cards — three tabs, composition-change pairs matched on their common items, item values per row
(check 6), atomic card budget with reference-card eviction, per-stage reservation peaks (docs/plan/ledger_report/README.md 28)."""
import json
import math

import pytest

from simcompare import ledger_maps as lm
import test_ledger_maps as base
from test_ledger_maps_cards import (LINE_PAIRS, MAP_OPTS, TWO_PAIRS, big, bar, card, correspondence_chain, line_case,  # noqa: F401
                                    rows_of, run_cards, tiles_px, write_candidates)


# ---- tab selection (pure) -------------------------------------------------------------------------------------------------

def pair(pid, delta, *, kind='line', change='same_pair_change', items=None, brk='auto'):
    items = items if items is not None else [{'item_id': pid + '1', 'presence': 'both'}]
    if brk == 'auto':
        n_both = sum(1 for i in items if i['presence'] == 'both')
        brk = {'same_delta': delta if n_both == len(items) else 0.0, 'new_value': 0.0, 'gone_value': 0.0,
               'status': 'ok', 'n_both': n_both}
    return {'pair_id': pid, 'kind': kind, 'a': 10.0, 'b': 10.0 + delta, 'delta': delta, 'change_kind': change,
            'present_a': True, 'present_b': True, 'items': items, 'breakdown': brk}


def test_three_tabs_make_one_card_per_pair_with_the_tabs_and_ranks_of_each():
    top = pair('T', 9.0)                                                 # first on same and on all
    comp = pair('C', 0.1, change='layer_change', brk={'same_delta': 0.0, 'new_value': 100.0, 'gone_value': 100.0,
                                                      'status': 'ok', 'n_both': 1},
                items=[{'item_id': 'c1', 'presence': 'both'}, {'item_id': 'c2', 'presence': 'new'},
                       {'item_id': 'c3', 'presence': 'gone'}])
    filler = [pair(f'F{i}', 5.0 - i) for i in range(4)]
    plans = lm._select_cards({'pairs': [top, comp] + filler}, 2)
    by_id = {p['pair_id']: p for p in plans}
    # |delta|: T, F0 ; same: T, F0 ; comp: only C (the only layer_change pair, score 200 although delta is 0.1)
    assert sorted(by_id) == ['C', 'F0', 'T']
    assert by_id['T']['tabs'] == ['same', 'all'] and by_id['T']['tab_ranks'] == {'same': 1, 'all': 1}
    assert by_id['C']['tabs'] == ['comp'] and by_id['C']['tab_ranks'] == {'comp': 1}
    assert by_id['F0']['tabs'] == ['same', 'all'] and by_id['F0']['tab_ranks'] == {'same': 2, 'all': 2}
    # `rank` = rank by |delta| among all candidates; C is 6th by |delta| but a card because of its tab
    assert [(p['pair_id'], p['rank']) for p in plans] == [('T', 1), ('F0', 2), ('C', 6)]


def test_a_pair_on_all_three_tabs_is_one_card_with_three_tabs():
    both = pair('X', 4.0, change='layer_change',
                brk={'same_delta': 4.0, 'new_value': 1.0, 'gone_value': 1.0, 'status': 'ok', 'n_both': 1},
                items=[{'item_id': 'x1', 'presence': 'both'}, {'item_id': 'x2', 'presence': 'new'}])
    plans = lm._select_cards({'pairs': [both]}, 5)
    assert len(plans) == 1 and plans[0]['tabs'] == ['same', 'comp', 'all']
    assert plans[0]['tab_ranks'] == {'same': 1, 'comp': 1, 'all': 1}


def test_a_pair_without_a_breakdown_or_an_item_on_both_products_is_only_on_the_all_tab():
    waiting = pair('W', 3.0, brk=None)
    only_new = pair('N', 2.0, items=[{'item_id': 'n1', 'presence': 'new'}], brk={
        'same_delta': 0.0, 'new_value': 2.0, 'gone_value': 0.0, 'status': 'ok', 'n_both': 0})
    plans = {p['pair_id']: p for p in lm._select_cards({'pairs': [waiting, only_new]}, 5)}
    assert plans['W']['tabs'] == ['all'] and plans['N']['tabs'] == ['all']
    comp_null = pair('K', 1.0, change='layer_change', brk=None)
    assert lm._select_cards({'pairs': [comp_null]}, 5)[0]['tabs'] == ['all']


def test_new_100_and_gone_100_with_a_zero_delta_is_first_on_the_comp_tab():
    swap = pair('S', 0.0, change='layer_change',
                brk={'same_delta': 0.0, 'new_value': 100.0, 'gone_value': 100.0, 'status': 'ok', 'n_both': 1},
                items=[{'item_id': 's1', 'presence': 'both'}, {'item_id': 's2', 'presence': 'new'},
                       {'item_id': 's3', 'presence': 'gone'}])
    small = pair('M', 1.0, change='layer_change',
                 brk={'same_delta': 0.0, 'new_value': 1.0, 'gone_value': 0.5, 'status': 'ok', 'n_both': 1},
                 items=[{'item_id': 'm1', 'presence': 'both'}, {'item_id': 'm2', 'presence': 'new'}])
    plans = {p['pair_id']: p for p in lm._select_cards({'pairs': [swap, small]}, 1)}
    assert plans['S']['tab_ranks']['comp'] == 1 and 'comp' not in plans['M']['tab_ranks']
    assert plans['M']['tab_ranks']['all'] == 1


# ---- composition-change pair on a real chain ---------------------------------------------------------------------------------

def composition_chain(tmp, big_fixture, *, b_only_cells, b_common_cells, a_cells=None):
    """A has one item (L1). B has that item (L1) plus a B-only item (L2); the B-only cells may overlap the common ones."""
    big_fixture(100, 200)
    a_cells = a_cells or tiles_px(6, 9, 6, 9)
    ref = ([line_case(a_cells)], LINE_PAIRS)
    tgt = ([line_case(b_common_cells), line_case(b_only_cells, layer='L2')], LINE_PAIRS)
    base.chain(tmp, ref=ref, tgt=tgt, unit=(20.0, 10.0))
    return (100, 200)


def test_composition_change_pair_is_matched_on_its_common_item_and_rows_list_both_and_b_only_values(tmp_path, big):
    moved = tiles_px(16, 19, 10, 13)
    shape = composition_chain(tmp_path, big, b_only_cells=moved, b_common_cells=moved)       # the L2 item sits on the same tiles
    write_candidates(tmp_path, shape, shape, cells=[(0, 100, 0, 200, 50, 20)])
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'))
    assert code == 0 and doc['bands'] == [] and doc['schema_version'] == 'ledger_maps_v2'
    led = json.loads((tmp_path / 'led' / 'ledger.json').read_text())
    (led_pair,) = [p for p in led['pairs'] if p['pair_id'] == 'AB']
    assert led_pair['change_kind'] == 'layer_change'
    c = card(doc, 'AB')
    assert c['match']['status'] == 'matched' and c['match']['matched_rows'] == 1
    assert c['common_items'] == 1 and c['items_total'] == 2 and c['tabs'] == ['same', 'comp', 'all']
    (a1,) = rows_of(c, 'reference')
    b_rows = rows_of(c, 'target')
    assert [i['presence'] for i in a1['items']] == ['both'] and a1['match']['counterpart'] == 'B1'
    b1 = b_rows[0]
    presence = {i['item_id']: i['presence'] for i in b1['items']}
    assert sorted(presence.values()) == ['b_only', 'both']                       # both together in one row
    by_presence = {i['presence']: i['value'] for i in b1['items']}
    assert by_presence['b_only'] > 0 and by_presence['both'] > 0
    # check 6: the item values of a row add up to the row's value of the pair
    for row in c['rows']:
        assert math.fsum(i['value'] for i in row['items']) == pytest.approx(row['pair'], rel=1e-12), row['id']
    assert b1['pair'] == pytest.approx(c['b']) and a1['pair'] == pytest.approx(c['a'])
    assert doc['checks']['row_items']['status'] == 'passed' and doc['checks']['row_items']['checked'] == 2
    assert doc['checks']['partition']['status'] == 'passed'
    # the pair value is the ledger value; the b_only item is the ledger `new` item
    (new_item,) = [i for i in led_pair['items'] if i['presence'] == 'new']
    assert by_presence['b_only'] == pytest.approx(new_item['b'], rel=1e-12)


def test_the_correspondence_of_a_composition_change_pair_uses_the_common_items_only(tmp_path, big):
    """A's row lands (offset 50, 20 px) where only the B-only item has shape; the common item sits elsewhere in B. Measured
    on the whole pair the row would match, on the common item it must not (low_membership_overlap)."""
    landing = tiles_px(16, 19, 10, 13)
    shape = composition_chain(tmp_path, big, b_only_cells=landing, b_common_cells=tiles_px(30, 33, 2, 5))
    write_candidates(tmp_path, shape, shape, cells=[(0, 100, 0, 200, 50, 20)])
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'))
    c = card(doc, 'AB')
    assert code == 0 and c['match']['status'] == 'not_compared'
    assert rows_of(c, 'reference')[0]['match']['reason'] == 'low_membership_overlap'
    # the B rows are listed on their own and still carry their item values
    assert {i['presence'] for r in rows_of(c, 'target') for i in r['items']} == {'both', 'b_only'}
    assert doc['checks']['row_items']['status'] == 'passed'


def test_a_composition_change_pair_without_a_common_item_is_not_compared(tmp_path, big):
    shape = correspondence_chain(tmp_path, big, tiles_px(6, 9, 6, 9), tiles_px(16, 19, 10, 13), tgt_layer='L2')
    write_candidates(tmp_path, shape, shape, cells=[(0, 100, 0, 200, 50, 20)])
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'))
    c = card(doc, 'AB')
    assert code == 0 and c['match']['status'] == 'not_compared' and c['match']['reason'] == 'no_common_item'
    assert c['common_items'] == 0 and c['tabs'] == ['comp', 'all']
    (a1,), (b1,) = rows_of(c, 'reference'), rows_of(c, 'target')
    assert [i['presence'] for i in a1['items']] == ['a_only'] and [i['presence'] for i in b1['items']] == ['b_only']
    assert doc['checks']['row_items']['status'] == 'passed'


def test_a_pair_whose_items_are_all_on_both_products_keeps_one_item_per_row_with_the_row_value(tmp_path, big):
    shape = correspondence_chain(tmp_path, big, tiles_px(6, 9, 6, 9), tiles_px(16, 19, 10, 13))
    write_candidates(tmp_path, shape, shape, cells=[(0, 100, 0, 200, 50, 20)])
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'))
    c = card(doc, 'AB')
    assert code == 0 and c['match']['status'] == 'matched' and c['common_items'] == c['items_total'] == 1
    for row in c['rows']:
        (item,) = row['items']
        assert item['presence'] == 'both' and item['value'] == pytest.approx(row['pair'], rel=1e-12)
    assert doc['checks']['row_items']['checked'] == 2


def test_an_item_sum_that_does_not_add_up_fails_check_6_and_exits_3(tmp_path, big, monkeypatch):
    shape = correspondence_chain(tmp_path, big, tiles_px(6, 9, 6, 9), tiles_px(6, 9, 6, 9))
    real_fill = lm._fill_values

    def broken(ctx, plan, rows):
        real_fill(ctx, plan, rows)
        for row in rows:
            if row.get('items'):
                row['items'][0]['value'] *= 2.0
    monkeypatch.setattr(lm, '_fill_values', broken)
    code, doc = run_cards(tmp_path)
    assert code == 3 and doc['checks']['row_items']['status'] == 'failed' and doc['checks']['status'] == 'failed'
    assert doc['reasons'] == ['ledger_maps_value_check_failed'] and shape


# ---- atomic cards and the memory budget --------------------------------------------------------------------------------------

@pytest.fixture
def capture(monkeypatch):
    seen = {'budgets': [], 'cards': []}
    real_budget, real_card = lm._Budget.__init__, lm._Card.__init__

    def budget_init(self, *args, **kwargs):
        real_budget(self, *args, **kwargs)
        seen['budgets'].append(self)

    def card_init(self, *args, **kwargs):
        real_card(self, *args, **kwargs)
        seen['cards'].append(self)
    monkeypatch.setattr(lm._Budget, '__init__', budget_init)
    monkeypatch.setattr(lm._Card, '__init__', card_init)
    return seen


def inject(monkeypatch, prefix):
    """A reservation that starts with `prefix` fails as if the budget were exhausted (the run goes on from there)."""
    real = lm._Budget.reserve

    def reserve(self, nbytes, what, **kwargs):
        if what.startswith(prefix):
            raise lm._BudgetExceeded('oversized_real_operation', f'injected: {what}')
        return real(self, nbytes, what, **kwargs)
    monkeypatch.setattr(lm._Budget, 'reserve', reserve)
    return lambda: monkeypatch.setattr(lm._Budget, 'reserve', real)


def two_card_chain(tmp, big_fixture):
    big_fixture(100, 200)
    ab_a, ab_b = line_case(tiles_px(6, 9, 6, 9)), line_case(tiles_px(16, 20, 10, 14))
    gh_a = line_case(tiles_px(30, 33, 6, 9), layer='L2', ea='G', eb='H')
    gh_b = line_case(tiles_px(33, 36, 10, 13), layer='L2', ea='G', eb='H')
    base.chain(tmp, ref=([ab_a, gh_a], TWO_PAIRS), tgt=([ab_b, gh_b], TWO_PAIRS), unit=(20.0, 10.0))


def checks_of_survivors(doc):
    """What the card checks must have counted when only the cards in doc['cards'] were finished."""
    cards = doc['cards']
    live = sum(1 for c in cards for r in c['rows'] if r['edge'] != 'excluded')
    return {'range_sum': live, 'row_items': live, 'partition': len(cards), 'pair_bound': 2 * len(cards)}


def assert_no_card_memory_left(seen, run_used, base_used):
    assert all(card.hold_a.bytes == 0 for card in seen['cards'])
    assert run_used == base_used                            # only the reservations that are not owned by a card stay


def test_baseline_two_cards_free_every_card_reservation(tmp_path, big, capture):
    two_card_chain(tmp_path, big)
    code, doc = run_cards(tmp_path)
    assert code == 0 and [c['pair_id'] for c in doc['cards']] == ['AB', 'GH'] and doc['cards_dropped_for_budget'] == []
    assert all(c.hold_a.bytes == 0 for c in capture['cards'])
    for name, checked in checks_of_survivors(doc).items():
        assert doc['checks'][name]['checked'] == checked and doc['checks'][name]['status'] == 'passed'


def test_a_reserve_that_fails_on_the_second_reference_card_leaves_that_card_out_and_exits_3(tmp_path, big, capture,
                                                                                            monkeypatch):
    two_card_chain(tmp_path, big)
    code, doc = run_cards(tmp_path)
    base_used = capture['budgets'][-1].used
    capture['cards'].clear()
    inject(monkeypatch, 'card rows carried reference GH')          # after the card's pair arrays were reserved: it must roll back
    code, doc = run_cards(tmp_path, out='m2')
    assert code == 3 and doc['status'] == 'partial' and doc['reasons'] == ['cards_budget_reduced']
    assert [c['pair_id'] for c in doc['cards']] == ['AB']
    (dropped,) = doc['cards_dropped_for_budget']
    assert (dropped['pair_id'], dropped['stage'], dropped['reason']) == ('GH', 'reference', 'card_reserve_failed')
    assert [b for b in doc['bands'] if b['code'] == 'cards_budget_reduced'][0]['count'] == 1
    assert doc['checks']['item_sum']['reference']['status'] == 'passed' and doc['checks']['kind_sum']['reference']['line'][
        'status'] == 'passed'                                       # maps and checks 1-2 stay
    assert (tmp_path / 'm2' / 'map_reference_001_it.png').is_file()
    for name, checked in checks_of_survivors(doc).items():          # the dropped card is in no check count
        assert doc['checks'][name]['checked'] == checked and doc['checks'][name]['status'] == 'passed'
    assert_no_card_memory_left(capture, capture['budgets'][-1].used, base_used)


def test_a_reserve_that_fails_on_the_second_target_card_leaves_that_card_out_and_frees_its_reference_part(
        tmp_path, big, capture, monkeypatch):
    two_card_chain(tmp_path, big)
    code, doc = run_cards(tmp_path)
    base_used = capture['budgets'][-1].used
    capture['cards'].clear()
    inject(monkeypatch, 'card matching target GH')                 # the reference part of GH is done and still held
    code, doc = run_cards(tmp_path, out='m2')
    assert code == 3 and [c['pair_id'] for c in doc['cards']] == ['AB']
    (dropped,) = doc['cards_dropped_for_budget']
    assert (dropped['pair_id'], dropped['stage'], dropped['reason']) == ('GH', 'target', 'card_reserve_failed')
    assert any(c.plan['pair_id'] == 'GH' for c in capture['cards'])
    for name, checked in checks_of_survivors(doc).items():          # GH's reference checks (done earlier) are not counted
        assert doc['checks'][name]['checked'] == checked
    assert_no_card_memory_left(capture, capture['budgets'][-1].used, base_used)


def test_reference_cards_are_evicted_worst_rank_first_when_the_second_product_does_not_fit(tmp_path, big, capture):
    two_card_chain(tmp_path, big)
    code, doc = run_cards(tmp_path)
    budget = capture['budgets'][-1]
    peak, base_used = budget.peak, budget.used
    execution = json.loads((tmp_path / 'maps' / 'execution.json').read_text())
    stages = execution['max_reserved_by_stage']
    assert stages['target']['read'] == peak                           # the peak of this chain is the target read stage
    capture['cards'].clear()
    code = lm.main_ledger_maps(str(tmp_path / 'out'), str(tmp_path / 'compare2.yaml'), str(tmp_path / 'led'),
                               str(tmp_path / 'm2'), lambda record: None, options={
                                   'tile_um': 0.5, 'range_um': 0.5, 'linear_min_length_um': 5, 'segment_um': 7,
                                   'coord_tolerance_um': 1.0}, max_operation_bytes=peak - 1)
    doc = base.load_maps(tmp_path / 'm2')
    assert code == 3 and doc['reasons'] == ['cards_budget_reduced']
    (dropped,) = doc['cards_dropped_for_budget']
    assert (dropped['pair_id'], dropped['stage'], dropped['reason']) == ('GH', 'reference_held', 'evicted_for_budget')
    assert [c['pair_id'] for c in doc['cards']] == ['AB']
    for name, checked in checks_of_survivors(doc).items():
        assert doc['checks'][name]['checked'] == checked
    assert_no_card_memory_left(capture, capture['budgets'][-1].used, base_used)


def fake_run(budget, *held):
    """A _CardRun with only what eviction needs: finished reference cards that hold `held` bytes each (best rank first)."""
    run = lm._CardRun.__new__(lm._CardRun)
    run.budget, run.carried, run.dropped = budget, {}, []
    for rank, nbytes in enumerate(held, start=1):
        plan = {'pair_id': f'P{rank}', 'kind': 'line', 'rank': rank, 'tabs': ['all'], 'tab_ranks': {'all': rank},
                'drop_key': (rank, -1, 0, f'P{rank}')}
        card = lm._Card(plan)
        budget.reserve(nbytes, f'card {rank}', evict=False)
        card.hold_a.bytes = nbytes
        run.carried[plan['pair_id'], 'line'] = card
    budget.evictor = run.evict_one
    return run


def test_budget_100_with_a_card_of_20_and_a_picture_of_10_drops_the_card_for_a_target_estimate_of_75():
    budget = lm._Budget(100)
    budget.reserve(10, 'A item map PNG')
    run = fake_run(budget, 20)
    assert budget.used == 30
    budget.reserve(75, 'compute_areas holding (target)')              # 30 + 75 > 100: the card goes, 10 + 75 fits
    assert budget.used == 85
    assert run.carried == {} and [(d['pair_id'], d['reason']) for d in run.dropped] == [('P1', 'evicted_for_budget')]
    assert budget.used - 75 == 10                                       # only the non-card reservation (the picture) is left


def test_eviction_drops_the_worst_rank_first_and_stops_as_soon_as_the_request_fits():
    budget = lm._Budget(100)
    budget.reserve(10, 'A item map PNG')
    run = fake_run(budget, 20, 20, 20)                                  # P1 best ... P3 worst
    budget.reserve(45, 'next stage')                                    # 70 + 45 > 100: one card (P3) is enough
    assert [d['pair_id'] for d in run.dropped] == ['P3'] and sorted(c[0] for c in run.carried) == ['P1', 'P2']
    budget.reserve(20, 'next stage')                                    # 115 > 100: P2 goes, 95 fits
    assert [d['pair_id'] for d in run.dropped] == ['P3', 'P2'] and budget.used == 95


def test_when_dropping_every_card_is_not_enough_the_reserve_raises_for_exit_2():
    budget = lm._Budget(100)
    budget.reserve(30, 'A item map PNG')
    run = fake_run(budget, 20, 10)
    with pytest.raises(lm.NumericContractError) as error:
        budget.reserve(75, 'compute_areas holding (target)')          # 30 + 75 > 100 even with both cards gone
    assert error.value.code == 'oversized_real_operation' and 'compute_areas holding (target)' in str(error.value)
    assert run.carried == {} and len(run.dropped) == 2
    assert budget.used == 30                                            # the non-card reservation is kept


def test_a_card_that_fails_inside_its_scope_does_not_evict_other_cards():
    budget = lm._Budget(100)
    run = fake_run(budget, 40)
    other = lm._Hold()
    budget.begin(other)
    with pytest.raises(lm._BudgetExceeded):
        budget.reserve(80, 'card matching target X')                    # inside a card: the card fails, nobody is evicted
    budget.end()
    assert len(run.carried) == 1 and run.dropped == [] and budget.used == 40 and other.bytes == 0


def test_all_cards_dropped_and_still_no_room_for_the_second_product_is_exit_2(tmp_path, big, capture, monkeypatch):
    two_card_chain(tmp_path, big)
    real = lm._Budget.reserve

    def reserve(self, nbytes, what, **kwargs):
        if what.startswith('compute_areas holding (target)'):
            nbytes = 10 ** 12
        return real(self, nbytes, what, **kwargs)
    monkeypatch.setattr(lm._Budget, 'reserve', reserve)
    code = lm.main_ledger_maps(str(tmp_path / 'out'), str(tmp_path / 'compare2.yaml'), str(tmp_path / 'led'),
                               str(tmp_path / 'm2'), lambda record: None,
                               options={'tile_um': 0.5, 'range_um': 0.5, 'linear_min_length_um': 5, 'segment_um': 7,
                                        'coord_tolerance_um': 1.0})
    assert code == 2
    error = json.loads((tmp_path / 'm2' / 'error.json').read_text())
    assert error['code'] == 'oversized_real_operation' and 'compute_areas holding (target)' in error['detail']
    assert not (tmp_path / 'm2' / 'ledger_maps.json').exists()
    assert capture['cards'] and all(c.hold_a.bytes == 0 for c in capture['cards'])          # every card was dropped first


def test_execution_json_records_the_peak_reservation_of_every_stage_of_every_product(tmp_path, big):
    two_card_chain(tmp_path, big)
    code, doc = run_cards(tmp_path)
    execution = json.loads((tmp_path / 'maps' / 'execution.json').read_text())
    stages = execution['max_reserved_by_stage']
    assert sorted(stages) == ['reference', 'target']
    for role in ('reference', 'target'):
        assert sorted(stages[role]) == ['cards', 'maps', 'merge', 'read']
        assert all(isinstance(v, int) and v >= 0 for v in stages[role].values())
        assert stages[role]['read'] > 0 and stages[role]['cards'] > 0
    assert max(v for role in stages.values() for v in role.values()) == execution['max_reserved_bytes']


# ---- astra D1: a tile electrode grid whose build stops is not left reserved ----------------------------------------------------

def grid_world(monkeypatch, budget):
    """A 10 x 10 px frame, tile 1 px: the grid is 100 tiles * 2 B = 200 B, one PNG decode is 10 * 10 * 3 = 300 B."""
    from types import SimpleNamespace
    from simcompare.adapters import electrode_png
    import numpy as np
    monkeypatch.setattr(electrode_png, 'scan_electrode_shapes', lambda root, model: {'E1': {'L1': 'e1.png'}})
    monkeypatch.setattr(electrode_png, 'load_electrode_layer',
                        lambda root, model, frame, rels, errors=None: np.ones((10, 10), dtype=bool))
    side = SimpleNamespace(dl1_model='M')
    cfg2 = SimpleNamespace(report=SimpleNamespace(dl1_root='root'), reference=side, target=side)
    ctx = SimpleNamespace(tile_el={}, role='reference', info={'frame': SimpleNamespace(shape_yx=(10, 10))}, tile_px=1,
                          ntx=10, nty=10, el_bytes=0)
    return ctx, SimpleNamespace(cfg2=cfg2, budget=budget)


def run_card(ctx, run, work):
    """One card: its own hold, `work` bytes reserved first, then the grid; a failed card gives its hold back (as the card loop)."""
    hold = lm._Hold()
    run.budget.begin(hold)
    try:
        run.budget.reserve(work, 'card work')
        return lm._tile_electrodes(ctx, run, 'L1')
    except lm._BudgetExceeded:
        return 'failed'
    finally:
        run.budget.end()
        run.budget.free(hold)


def test_a_card_that_fails_on_the_png_decode_gives_the_grid_reservation_back(monkeypatch):
    budget = lm._Budget(1000)
    budget.reserve(400, 'not a card')                           # e.g. the other product's pictures
    ctx, run = grid_world(monkeypatch, budget)
    assert run_card(ctx, run, 300) == 'failed'                  # 400 + 300 + grid 200 = 900, the decode 300 does not fit
    assert budget.used == 400 and ctx.el_bytes == 0 and ctx.tile_el.get('L1') is None     # nothing of the card is left
    grid = run_card(ctx, run, 100)                              # 400 + 100 + 200 + 300 = 1000 fits (it failed before the fix)
    assert grid is not None and grid.shape == (10, 10) and int(grid[0, 0]) == 1
    grids = [g for key, g in ctx.tile_el.items() if key != '_names']
    assert budget.used == 400 + len(grids) * 200 and ctx.el_bytes == len(grids) * 200     # the cache and the reservation agree
    assert run_card(ctx, run, 500) is not None                  # the cached grid is reused: no second grid, no decode
    assert budget.used == 600


def test_a_grid_reservation_that_does_not_fit_leaves_nothing_behind(monkeypatch):
    budget = lm._Budget(1000)
    budget.reserve(900, 'not a card')
    ctx, run = grid_world(monkeypatch, budget)
    assert run_card(ctx, run, 0) == 'failed' and budget.used == 900 and ctx.el_bytes == 0
