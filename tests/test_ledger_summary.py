"""rnarr/T5: `ledger.json summary` (rebase split, design difference, cause-candidate bundles, concentration), layer_order.

Bundle tests build compare2.json by hand (test_ledger helpers); the rebase tests run the real `compare2` command."""
import copy
import json
import math
import random
import re

import pytest

from simcompare import ledger
from test_compare2 import two_sides
from test_compare2_rebase import go as rebase_go
from test_ledger import go, lateral, make_world, pair, result, vertical

ID_RE = re.compile(r'^bd-[0-9a-f]{16,24}$')


def dl1_all(names, layers=('SD1',)):
    return {role: {name: list(layers) for name in names} for role in ('reference', 'target')}


def run(tmp_path, pairs, dl1=None, **kw):
    world = make_world(tmp_path, pairs, dl1=dl1, **kw)
    assert go(world) == 0
    return result(world), world


def bundles(doc):
    return {tuple(b['key']): b for b in doc['summary']['bundles']['list']}


def up(pid, a, b, lam_a=.010, lam_b=.020, layer='SD1', cls='line'):
    return pair(pid, list(a), cls, [lateral(list(a), lam_a, layer)], [lateral(list(a), lam_b, layer)])


def hub_pairs(center, partners, prefix, lam_a=.010, lam_b=.020, layer='SD1'):
    return [up(f'{prefix}{i}', (center, p), None, lam_a, lam_b, layer) for i, p in enumerate(partners)]


def all_passed(doc):
    checks = doc['checks']['bundles']
    assert checks['duplicates'] == 0 and checks['assigned'] == checks['atoms'] and checks['id_unique']
    for kind in ('line', 'pixel'):
        for name, check in checks['kinds'][kind].items():
            assert check['status'] in ('passed', 'not_evaluated'), (kind, name, check)
    assert checks['status'] != 'failed', checks
    assert checks['kinds']['line']['net_plus_crp']['status'] == 'passed', checks


# ---- G5 (a)-(n) -------------------------------------------------------------------------------------------------

def test_a_new_electrode_on_a_layer_takes_all_its_atoms(tmp_path):
    names = ['Data', 'X1', 'X2', 'X3']
    dl1 = dl1_all(names, ('GAT2', 'SD1'))
    dl1['reference']['Data'] = ['SD1']                # Data is on GAT2 in B only
    pairs = [pair(f'N{i}', ['Data', f'X{i}'], 'line', None, [lateral(['Data', f'X{i}'], .01, 'GAT2')])
             for i in (1, 2, 3)]
    doc, _ = run(tmp_path, pairs, dl1=dl1)
    got = bundles(doc)
    assert list(got) == [('e', 'Data', 'GAT2')]
    assert got[('e', 'Data', 'GAT2')]['type'] == 'new' and got[('e', 'Data', 'GAT2')]['atoms']['count'] == 3
    all_passed(doc)


def test_b_hub_takes_its_partners_and_a_hub_partner_keeps_its_pair(tmp_path):
    partners = ['P0', 'P1', 'P2', 'P3', 'P4']
    names = ['Scan3'] + partners + ['Q1', 'Q2']
    pairs = hub_pairs('Scan3', partners, 'H') + hub_pairs('P0', ['Q1', 'Q2'], 'K')
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(names))
    got = bundles(doc)
    hub = got[('e', 'Scan3', 'SD1')]
    assert hub['type'] == 'hub' and hub['partner_count'] == 4         # P0 is a hub too: its pair has its own bundle
    pair_b = got[('p', 'H0', 'SD1')]
    assert pair_b['type'] == 'pair' and pair_b['why'] == ['hubs']
    assert [e['partner'] for e in hub['partners_elsewhere']] == ['P0']
    assert hub['partners_elsewhere'][0]['bundle_id'] == pair_b['id']
    assert ('e', 'P0', 'SD1') in got                                  # P0-Q1, P0-Q2: P0 hub, Q not
    all_passed(doc)


def test_c_two_partners_are_not_a_hub(tmp_path):
    pairs = hub_pairs('E', ['F', 'G'], 'T')
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(['E', 'F', 'G']))
    got = bundles(doc)
    assert set(got) == {('p', 'T0', 'SD1'), ('p', 'T1', 'SD1')}
    assert all(b['why'] == ['nohub'] for b in got.values())


def test_d_a_moved_electrode_is_one_bundle_with_from_and_to(tmp_path):
    dl1 = dl1_all(['E', 'F', 'G'], ('SD1', 'SD2'))
    dl1['reference']['E'], dl1['target']['E'] = ['SD1'], ['SD2']
    pairs = [pair('M1', ['E', 'F'], 'line', [lateral(['E', 'F'], .01, 'SD1')], None),
             pair('M2', ['E', 'G'], 'line', None, [lateral(['E', 'G'], .02, 'SD2')])]
    doc, _ = run(tmp_path, pairs, dl1=dl1)
    got = bundles(doc)
    assert list(got) == [('mv', 'E')]
    move = got[('mv', 'E')]
    assert move['type'] == 'move' and move['from'] == ['SD1'] and move['to'] == ['SD2']
    assert move['atoms']['count'] == 2 and move['kinds']['line']['net'] == pytest.approx(
        doc['totals']['line']['delta'])
    all_passed(doc)


def test_e_both_electrodes_changed_gives_a_pair_bundle(tmp_path):
    dl1 = dl1_all(['E', 'F'], ('SD1', 'GAT2'))
    dl1['reference']['E'], dl1['reference']['F'] = ['SD1'], ['SD1']
    pairs = [pair('B1', ['E', 'F'], 'line', None, [lateral(['E', 'F'], .01, 'GAT2')])]
    doc, _ = run(tmp_path, pairs, dl1=dl1)
    got = bundles(doc)
    assert list(got) == [('p', 'B1', 'GAT2')] and got[('p', 'B1', 'GAT2')]['why'] == ['both']


def test_f_cno_goes_to_its_electrode_bundle(tmp_path):
    pairs = [up('P1', ('E', 'F'), None)]
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(['E', 'F', 'Z']), cno=[('Z', 'SD1', .005, .009)])
    got = bundles(doc)
    z = got[('e', 'Z', 'SD1')]
    assert z['type'] == 'cno' and z['kinds']['pixel']['net'] == pytest.approx(doc['cno'][0]['delta'])
    assert z['rows'][0]['cno'] is True and z['rows'][0]['pair_id'] is None
    all_passed(doc)


def test_g_mixed_sign_hub_keeps_both_signs_in_one_bundle(tmp_path):
    names = ['S'] + [f'U{i}' for i in range(3)] + [f'D{i}' for i in range(3)]
    pairs = (hub_pairs('S', names[1:4], 'U') + hub_pairs('S', names[4:], 'D', lam_a=.020, lam_b=.010))
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(names))
    hub = bundles(doc)[('e', 'S', 'SD1')]
    assert hub['atoms'] == {'increase': 3, 'decrease': 3, 'count': 6}
    assert hub['kinds']['line']['increase'] == 3 and hub['kinds']['line']['decrease'] == 3


def test_h_atoms_inside_the_tolerance_do_not_count_toward_a_hub(tmp_path):
    names = ['S', 'A', 'B', 'C']
    pairs = hub_pairs('S', ['A', 'B'], 'U') + [up('Z', ('S', 'C'), None, .010, .010 * (1 + 1e-12))]
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(names))
    got = bundles(doc)
    assert ('e', 'S', 'SD1') not in got                              # two counted partners only
    assert got[('p', 'Z', 'SD1')]['why'] == ['nohub']


def test_h5_tolerance_edge_is_judged_on_the_summed_values(tmp_path):
    names = ['S', 'A', 'B', 'C']
    for lam_b, hub in ((.010 * (1 + 1e-12), False), (.010 * (1 + 1e-6), True)):
        sub = tmp_path / str(hub)
        sub.mkdir()
        pairs = hub_pairs('S', ['A', 'B'], 'U') + [up('Z', ('S', 'C'), None, .010, lam_b)]
        doc, _ = run(sub, pairs, dl1=dl1_all(names))
        assert (('e', 'S', 'SD1') in bundles(doc)) is hub


def test_i_a_kind_that_cannot_be_computed_is_not_evaluated(tmp_path):
    pairs = [up('P1', ('E', 'F'), None, cls='pixel')]
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(['E', 'F']), pixel_null=True)
    status = doc['summary']['bundles']['status']
    assert status['pixel'] == 'not_evaluated' and doc['summary']['bundles']['reasons']['pixel'] == ['kind_not_computable']
    ranking = doc['summary']['bundles']['ranking']['pixel']
    assert ranking['k80'] is None and ranking['cards'] == [] and ranking['cover'] is None
    assert doc['summary']['concentration']['pixel']['actual']['pair']['status'] == 'not_evaluated'
    assert doc['summary']['concentration']['pixel']['actual']['pair']['verdict'] == 'omitted'
    assert doc['checks']['bundles']['kinds']['pixel']['net_plus_crp']['status'] == 'not_evaluated'


def test_j_only_the_crp_changes(tmp_path):
    pairs = [up('P1', ('E', 'F'), None, .01, .01, cls='pixel'), up('P2', ('G', 'H'), None, .02, .02, cls='pixel')]
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(['E', 'F', 'G', 'H']), crp=(1.0, 2.92))
    summary = doc['summary']
    assert summary['bundles']['status']['pixel'] == 'no_change'
    assert summary['bundles']['crp']['pixel'] == pytest.approx(doc['totals']['pixel']['delta'])
    assert summary['bundles']['crp']['pixel'] != 0.0
    assert summary['bundles']['list'] == []                         # nothing changed in any bundle
    conc = summary['concentration']['pixel']['actual']
    for stage in ('division', 'layer', 'electrode', 'pair', 'item'):
        groups = [g for g in conc[stage]['groups'] if g['abs'] > 0]
        assert conc[stage]['status'] == 'ok' and len(groups) == 1 and groups[0]['share'] == 1.0
    assert conc['division']['groups'][0]['key'] == 'crp' and conc['layer']['groups'][0]['key'] == 'CRP'
    assert doc['checks']['concentration']['pixel']['pair']['shares_sum']['status'] == 'passed'
    all_passed(doc)


def test_k_sums_and_no_duplicates_on_a_mixed_world(tmp_path):
    names = ['S', 'A', 'B', 'C', 'D', 'X', 'Y']
    pairs = (hub_pairs('S', ['A', 'B', 'C', 'D'], 'H') +
             [up('V', ('X', 'Y'), None, .004, .001, cls='pixel')])
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(names), cno=[('X', 'SD1', .002, .003)], crp=(1.0, 2.92))
    all_passed(doc)
    for kind in ('line', 'pixel'):
        net = sum(b['kinds'][kind]['net'] for b in doc['summary']['bundles']['list'])
        crp = doc['summary']['bundles']['crp'][kind]
        assert net + crp == pytest.approx(doc['totals'][kind]['delta'])


def test_l_k80_cards_and_the_rest(tmp_path):
    deltas = [.100, .090, .080, .070, .060, .050, .040, .030]
    pairs = [up(f'P{i}', (f'E{i}', f'F{i}'), None, .01, .01 + d) for i, d in enumerate(deltas)]
    pairs.append(up('Zero', ('Ez', 'Fz'), None, .02, .02))                  # unchanged: counted, not listed
    names = [n for p in pairs for n in p['electrodes']]
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(names))
    bundles_doc = doc['summary']['bundles']
    ranking = bundles_doc['ranking']['line']
    assert len(ranking['cards']) == 6 and len(ranking['rest']) == 2
    assert ranking['cover'] + ranking['rest_share'] == pytest.approx(1.0)
    assert ranking['k80'] == 6 or ranking['k80'] == 7
    assert len(bundles_doc['list']) == 8 and bundles_doc['bundle_count'] == 9
    assert bundles_doc['atoms']['line'] == 9
    assert doc['checks']['bundles']['assigned'] == 9
    all_passed(doc)


def test_m_the_same_input_twice_and_a_shuffled_input_give_identical_bytes(tmp_path):
    names = ['S', 'A', 'B', 'C', 'D', 'X', 'Y']
    pairs = hub_pairs('S', ['A', 'B', 'C', 'D'], 'H') + [up('V', ('X', 'Y'), None, .004, .001, cls='pixel')]
    cno = [('X', 'SD1', .002, .003), ('Y', 'SD1', .001, .002)]
    texts = []
    for index in range(3):
        sub = tmp_path / str(index)
        sub.mkdir()

        def shuffle(doc, seed=index):
            rng = random.Random(seed)
            for side in doc['sides'].values():
                if seed:
                    rng.shuffle(side['ledger_items'])
                    rng.shuffle(side['cno'])
                    rng.shuffle(side['conditions'])
        doc, world = run(sub, pairs, dl1=dl1_all(names), cno=cno, mutate=shuffle)
        summary = {k: v for k, v in doc.items() if k in ('summary',)}
        texts.append(json.dumps(summary, ensure_ascii=False, sort_keys=False))
    assert texts[0] == texts[1] == texts[2]


def test_m2_atom_order_does_not_matter_to_the_bundle_function():
    atoms = []
    for i in range(6):
        atoms.append({'id': f'i{i}', 'kind': 'line', 'pair_id': f'P{i}', 'cno': False, 'layer': 'SD1',
                      'ends': [('S', 'SD1'), (f'T{i}', 'SD1')], 'a': .01, 'b': .01 + .001 * (i + 1),
                      'd': .001 * (i + 1), 'presence': 'both'})
    erows = {n: {'electrode': n, 'layer_state_status': 'verified', 'layer_state': {'SD1': 'both'}, 'moved': False}
             for n in ['S'] + [f'T{i}' for i in range(6)]}
    pair_rows = {f'P{i}': {'delta': .001, 'electrodes': ['S', f'T{i}']} for i in range(6)}
    kstat = {'line': ('ok', [], math.fsum(a['d'] for a in atoms)), 'pixel': ('no_change', [], 0.0)}
    totals = {'line': {'delta': math.fsum(a['d'] for a in atoms)}, 'pixel': {'delta': 0.0}}
    outs = []
    for seed in range(4):
        shuffled = [dict(a) for a in atoms]
        random.Random(seed).shuffle(shuffled)
        got, _ = ledger._bundle_summary(shuffled, kstat, erows, pair_rows, 0.0, totals, str)
        outs.append(json.dumps(got))
    assert len(set(outs)) == 1


def test_n_old_compare2_without_rebase_has_no_design_but_has_bundles(tmp_path):
    pairs = hub_pairs('S', ['A', 'B', 'C'], 'H')
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(['S', 'A', 'B', 'C']))
    summary = doc['summary']
    assert summary['rebase'] == {'status': 'not_available', 'reasons': ['compare2_without_rebase']}
    assert summary['design']['line']['status'] == 'not_available' and summary['design']['line']['top_increase'] == []
    assert summary['bundles']['list'] and summary['concentration']['line']['actual']['pair']['n'] == 3
    assert doc['checks']['rebase'] == {'status': 'not_evaluated'}
    assert 'layer_order' not in doc['inputs']
    assert summary['rules']['hub_min'] == 3
    first = doc['pairs'][0]['items'][0]
    assert first['ca_delta'] is None and first['by_size'][0]['lambda_b_at_a'] is None


# ---- H1: end-point layers ---------------------------------------------------------------------------------------

def test_h1_a_vertical_short_uses_the_state_of_each_end_point_layer(tmp_path):
    dl1 = dl1_all(['X', 'Y'], ('SD1', 'SD2'))
    dl1['reference']['Y'] = ['SD1']                                   # Y is on SD2 in B only
    pairs = [pair('V', ['X', 'Y'], 'line', [vertical([('X', 'SD1'), ('Y', 'SD1')], .01, 'SD1', 'SD1')],
                  [vertical([('X', 'SD1'), ('Y', 'SD2')], .02, 'SD1', 'SD2')])]
    doc, _ = run(tmp_path, pairs, dl1=dl1)
    got = bundles(doc)
    assert ('e', 'Y', 'SD2') in got and got[('e', 'Y', 'SD2')]['type'] == 'new'
    assert got[('e', 'Y', 'SD2')]['rows'][0]['layer'] == 'SD1'        # the line shows the item's analysis layer


def test_h1_the_opposite_direction_item_follows_its_own_end_points(tmp_path):
    dl1 = dl1_all(['X', 'Y'], ('SD1', 'SD2'))
    dl1['reference']['X'] = ['SD1']                                   # X is on SD2 in B only
    pairs = [pair('V', ['X', 'Y'], 'line', None, [vertical([('X', 'SD2'), ('Y', 'SD1')], .02, 'SD2', 'SD1')])]
    doc, _ = run(tmp_path, pairs, dl1=dl1)
    assert list(bundles(doc)) == [('e', 'X', 'SD2')]


def test_h1_one_electrode_at_both_ends_goes_to_that_electrode(tmp_path):
    dl1 = dl1_all(['X'], ('SD1', 'SD2'))
    pairs = [pair('S', ['X', 'X'], 'line', [vertical([('X', 'SD1'), ('X', 'SD2')], .01, 'SD1', 'SD2')],
                  [vertical([('X', 'SD1'), ('X', 'SD2')], .02, 'SD1', 'SD2')])]
    doc, _ = run(tmp_path, pairs, dl1=dl1)
    got = bundles(doc)
    assert list(got) == [('e', 'X', 'SD1')] and got[('e', 'X', 'SD1')]['type'] == 'hub'


def test_h1_a_missing_state_is_unknown_and_a_cno_without_state_is_cno(tmp_path):
    pairs = [up('P1', ('E', 'F'), None)]
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(['E', 'F', 'Z']), cno=[('Z', 'GAT9', .005, .009)])
    z = bundles(doc)[('e', 'Z', 'GAT9')]
    assert z['type'] == 'cno' and z['layer_state_unknown'] is True and z['layer_state_unverified'] is False


def test_h1_without_a_dl1_inventory_every_state_is_unverified(tmp_path):
    doc, _ = run(tmp_path, [up('P1', ('E', 'F'), None)])
    got = bundles(doc)
    assert list(got) == [('p', 'P1', 'SD1')] and got[('p', 'P1', 'SD1')]['layer_state_unverified'] is True


# ---- H2: ids ------------------------------------------------------------------------------------------------------

def test_h2_ids_are_unique_and_whitelisted_for_odd_names(tmp_path):
    names = ['A|B', 'Q"uote', '한글', 'a-b', "it's"]
    pairs = [up(f'P{i}', (names[i], names[(i + 1) % len(names)]), None) for i in range(len(names))]
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(names))
    lst = doc['summary']['bundles']['list']
    ids = [b['id'] for b in lst]
    assert len(ids) == len(set(ids)) == len(lst) and all(ID_RE.match(i) for i in ids)
    assert doc['checks']['bundles']['id_unique'] is True
    assert all(isinstance(b['key'], list) for b in lst)
    keys = {tuple(b['key']) for b in lst}
    assert len(keys) == len(lst)


def test_h2_the_id_is_a_hash_of_the_json_key():
    first, second = ledger._bundle_id(('e', 'A|B', 'SD1'), 16), ledger._bundle_id(('e', 'A', 'B|SD1'), 16)
    assert first != second and ID_RE.match(first) and len(ledger._bundle_id(('mv', 'E'), 24)) == 27


# ---- E4 / Z3 -------------------------------------------------------------------------------------------------------

def stage(values, delta=None):
    parts = {f'g{i:02d}': ([abs(v)], [v]) for i, v in enumerate(values)}
    total = sum(abs(v) for v in values)
    return ledger._concentration_stage(parts, total, 'ok' if total > 0 else 'no_change', [],
                                       sum(values) if delta is None else delta, len(values))[0]


def test_e4_three_groups_with_80_percent_are_concentrated():
    block = stage([.30, .26, .24, .04, .04, .04, .04, .04])
    assert block['k80'] == 3 and block['verdict'] == 'concentrated' and block['tie_at_cut'] is False


def test_e4_eight_equal_groups_are_a_tie_at_the_cut_and_omitted():
    block = stage([.125] * 8)
    assert block['verdict'] == 'omitted' and 'tie_at_cut' in block['omit_reasons'] and block['tie_count'] == 8


def test_e4_a_spread_without_ties_is_dispersed():
    # the plan's example [.15 .14 .13 .12 .12 .12 .11 .11] reaches 80% only at 7 and ties at the cut (omitted);
    # this one has no tie at the cut
    block = stage([.16, .14, .13, .125, .12, .115, .11, .10])
    assert block['k80'] == 7 and block['verdict'] == 'dispersed'
    assert stage([.15, .14, .13, .12, .12, .12, .11, .11])['verdict'] == 'omitted'


def test_e4_a_small_population_is_omitted_but_numbers_stay():
    block = stage([.5, .3, .2])
    assert block['verdict'] == 'omitted' and block['omit_reasons'] == ['n_lt_8'] and block['k80'] == 2


def test_z3_g_zero_or_null_has_no_shares():
    zero = stage([0.0] * 8)
    assert zero['status'] == 'no_change' and zero['k80'] is None and zero['top1'] is None
    assert all(g['share'] is None for g in zero['groups'])
    null_block, checks = ledger._concentration_stage({}, None, 'not_evaluated', ['atom_null'], None, 3)
    assert null_block['n'] is None and null_block['k80'] is None and null_block['verdict'] == 'omitted'
    assert checks['shares_sum']['status'] == 'not_evaluated' and checks['net_sum']['status'] == 'not_evaluated'


def test_e4_stage_sums_pass_on_a_real_world(tmp_path):
    names = ['S', 'A', 'B', 'C', 'D', 'X', 'Y']
    pairs = hub_pairs('S', ['A', 'B', 'C', 'D'], 'H') + [up('V', ('X', 'Y'), None, .004, .001, cls='pixel')]
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(names), cno=[('X', 'SD1', .002, .003)], crp=(1.0, 2.92))
    for kind in ('line', 'pixel'):
        for name, checks in doc['checks']['concentration'][kind].items():
            assert checks['shares_sum']['status'] == 'passed' and checks['net_sum']['status'] == 'passed', (kind, name)
    pixel = doc['summary']['concentration']['pixel']['actual']
    assert {g['key'] for g in pixel['layer']['groups']} == {'SD1', 'CNO@SD1', 'CRP'}
    assert {g['key'] for g in pixel['electrode']['groups']} == {'X', 'Y', 'CRP(귀속 불가)'}
    assert {g['key'] for g in pixel['pair']['groups']} == {'V', 'CNO:X@SD1', 'CRP'}
    assert {g['key'] for g in pixel['division']['groups']} <= {'same', 'new', 'gone', 'cno', 'crp'}


# ---- layer_order (E10, R9:4) ---------------------------------------------------------------------------------------

def test_e10_layer_lists_follow_layer_order_and_unknown_layers_follow_by_name(tmp_path):
    order = ['SD1', 'SD2', 'PXL']

    def put(doc):
        doc['inputs']['layer_order'] = order
    pairs = [pair('P1', ['E', 'F'], 'line',
                  [lateral(['E', 'F'], .01, 'PXL'), lateral(['E', 'F'], .01, 'SD1'), lateral(['E', 'F'], .01, 'ZZ'),
                   lateral(['E', 'F'], .01, 'AA')],
                  [lateral(['E', 'F'], .02, 'SD2')])]
    doc, _ = run(tmp_path, pairs, mutate=put)
    assert doc['inputs']['layer_order'] == order
    assert doc['pairs'][0]['layers_a'] == ['SD1', 'PXL', 'AA', 'ZZ'] and doc['pairs'][0]['layers_b'] == ['SD2']
    assert [row['layer'] for row in doc['layers']['line']] == ['SD1', 'SD2', 'PXL', 'AA', 'ZZ']
    (tmp_path / 'p').mkdir()
    plain, _ = run(tmp_path / 'p', pairs)
    assert [row['layer'] for row in plain['layers']['line']] == ['AA', 'PXL', 'SD1', 'SD2', 'ZZ']


# ---- the real compare2 -> ledger flow (rebase) -----------------------------------------------------------------------

def real_ledger(tmp_path, **kw):
    ref, tgt = two_sides()
    code, doc, out = rebase_go(tmp_path, ref, tgt, **kw)
    assert code == 0
    ledger_out = tmp_path / 'ledger_out'
    assert ledger.main_ledger(str(out), str(tmp_path / 'compare2.yaml'), str(ledger_out), lambda record: None) == 0
    return json.loads((ledger_out / 'ledger.json').read_text(encoding='utf-8')), doc


def test_h6_g1_and_g2_checks_pass_when_ca_f_and_crp_all_change(tmp_path):
    doc, c2 = real_ledger(tmp_path, b=dict(group='auto', res=[7, 9], glass=40.0))
    assert doc['totals']['pixel']['crp']['present'] is True
    assert doc['summary']['rebase']['status'] == 'available'
    assert doc['checks']['rebase']['status'] == 'passed', json.dumps(doc['checks']['rebase'], indent=1)
    assert doc['checks']['bundles']['status'] == 'passed', json.dumps(doc['checks']['bundles'], indent=1)
    for kind in ('line', 'pixel'):
        design = doc['summary']['design'][kind]
        assert design['status'] == 'available'
        assert sum(design['segments'].values()) == pytest.approx(doc['totals'][kind]['delta'], abs=1e-12)
        assert doc['summary']['bundles']['crp'][kind] == pytest.approx(doc['totals'][kind].get('crp', {'delta': 0.0})['delta'])
    assert doc['summary']['design']['line']['segments']['crp'] == 0.0           # line has no CRP
    assert doc['summary']['design']['pixel']['segments']['crp'] != 0.0
    assert doc['inputs']['layer_order'] == c2['inputs']['layer_order']
    ref = doc['inputs']['products']['reference']
    assert ref['F'] == {'line': c2['products']['reference']['F_line'], 'pixel': c2['products']['reference']['F_pixel'],
                        'cno': c2['products']['reference']['F_cno']}
    assert 'crp' in ref and len(ref['density_sha256']['line']) == 64
    item = doc['pairs'][0]['items'][0]
    assert item['ca_delta'] is not None and 'lambda_b_at_a' in item['by_size'][0]


def test_design_top_pairs_are_ordered_and_carry_both_deltas(tmp_path):
    doc, _ = real_ledger(tmp_path, b=dict(group='auto', res=[7, 9], glass=40.0))
    for kind in ('line', 'pixel'):
        design = doc['summary']['design'][kind]
        assert design['names']['resolution'] in ('해상도 차이', '해상도·조건 차이')
        up_values = [e['design_delta'] for e in design['top_increase']]
        down_values = [e['design_delta'] for e in design['top_decrease']]
        assert up_values == sorted(up_values, reverse=True) and down_values == sorted(down_values)
        assert all(v > 0 for v in up_values) and all(v < 0 for v in down_values) and len(up_values) <= 5
        for entry in design['top_increase'] + design['top_decrease']:
            assert entry['actual_delta'] is not None and 'design_a_relative' in entry


def test_r9_1_only_the_state_of_each_kind_decides(tmp_path):
    def put(doc):
        doc['rebase'] = {'status': 'partial', 'reasons': ['line:x'], 'missing': [], 'coefficients': {'ratios': {}},
                         'states': {'line': {'status': 'unavailable', 'reasons': ['x']},
                                    'pixel': {'status': 'unavailable', 'reasons': ['y']}},
                         'items': [], 'cno': [], 'check': {}}
        doc['rebase']['items'] = [{'item_id': it['item_id'], 'class': 'line', 'ca_delta': 5.0, 'f_delta': 5.0,
                                   'density_delta': 5.0, 'by_size': []}
                                  for it in doc['sides']['target']['ledger_items']]
    doc, _ = run(tmp_path, [up('P1', ('E', 'F'), None)], mutate=put)
    item = doc['pairs'][0]['items'][0]
    assert item['ca_delta'] is None and item['f_delta'] is None and item['by_size'][0]['lambda_b_at_a'] is None
    assert doc['summary']['design']['line']['status'] == 'unavailable'
    assert doc['summary']['rebase']['status'] == 'partial'


def test_r9_3_a_failed_rebase_check_leaves_the_design_not_evaluated(tmp_path):
    def put(doc):
        doc['rebase'] = {'status': 'available', 'reasons': [], 'missing': [], 'coefficients': {'ratios': {}},
                         'states': {'line': {'status': 'available', 'reasons': [],
                                             'segments': {name: {'sum': 0.0, 'delta_vi': 0.0}
                                                          for name in ('ca', 'f', 'density')}},
                                    'pixel': {'status': 'unavailable', 'reasons': ['y']}},
                         'items': [], 'cno': [], 'check': {'line': {'status': 'failed'}}}
    world = make_world(tmp_path, [up('P1', ('E', 'F'), None)], mutate=put)
    assert go(world) == 3                       # the empty fixture's own totals do not close: a failed check is exit 3
    doc = result(world)
    assert doc['reasons'] == ['ledger_summary_check_failed'] and doc['checks']['status'] != 'failed'
    design = doc['summary']['design']['line']
    assert design['status'] == 'not_evaluated' and design['reasons'] == ['rebase_check_failed']
    assert design['top_increase'] == [] and design['segments'] is None


def test_r9_2_missing_cells_are_summed_per_density_layer_and_size(tmp_path):
    def put(doc):
        miss = [{'scope': 'item', 'kind': 'line', 'item_id': 'i1', 'density_layer': 'PXL', 'diameter_um': 3.0,
                 'area_b_um2': 1.0, 'lambda_b_share': .25},
                {'scope': 'item', 'kind': 'line', 'item_id': 'i2', 'density_layer': 'PXL', 'diameter_um': 3.0,
                 'area_b_um2': 2.0, 'lambda_b_share': .5},
                {'scope': 'item', 'kind': 'line', 'item_id': 'i2', 'density_layer': 'SD1', 'diameter_um': 5.0,
                 'area_b_um2': 4.0, 'lambda_b_share': .125},
                {'scope': 'item', 'kind': 'line', 'item_id': 'i3', 'density_layer': 'SD1', 'diameter_um': 3.0,
                 'area_b_um2': 8.0, 'lambda_b_share': .0625},
                {'scope': 'item', 'kind': 'pixel', 'item_id': 'i9', 'density_layer': 'SD1', 'diameter_um': 3.0,
                 'area_b_um2': 9.0, 'lambda_b_share': .5}]
        doc['inputs']['layer_order'] = ['SD1', 'PXL']
        doc['rebase'] = {'status': 'partial', 'reasons': [], 'missing': miss, 'coefficients': {'ratios': {}},
                         'states': {'line': {'status': 'unavailable', 'reasons': ['r']},
                                    'pixel': {'status': 'unavailable', 'reasons': ['r']}},
                         'items': [], 'cno': [], 'check': {}}
    doc, _ = run(tmp_path, [up('P1', ('E', 'F'), None)], mutate=put)
    cells = doc['summary']['design']['line']['missing_cells']
    assert [(c['density_layer'], c['diameter_um']) for c in cells] == [('SD1', 3.0), ('SD1', 5.0), ('PXL', 3.0)]
    pxl = cells[2]
    assert pxl['area_b_um2'] == 3.0 and pxl['lambda_b_share'] == .75 and pxl['item_count'] == 2
    assert [c['item_count'] for c in doc['summary']['design']['pixel']['missing_cells']] == [1]
