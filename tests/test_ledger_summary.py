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
from test_ledger import go, item, lateral, make_world, pair, result, vertical

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

def test_a_a_layer_added_to_a_common_electrode_is_one_layer_change_bundle(tmp_path):
    names = ['Data', 'X1', 'X2', 'X3']
    dl1 = dl1_all(names, ('GAT2', 'SD1'))
    dl1['reference']['Data'] = ['SD1']                # Data is on GAT2 in B only: a common electrode, layer added
    pairs = [pair(f'N{i}', ['Data', f'X{i}'], 'line', None, [lateral(['Data', f'X{i}'], .01, 'GAT2')])
             for i in (1, 2, 3)]
    doc, _ = run(tmp_path, pairs, dl1=dl1)
    got = bundles(doc)
    assert list(got) == [('lyr', 'Data')]
    bundle = got[('lyr', 'Data')]
    assert bundle['type'] == 'layer_change' and bundle['atoms']['count'] == 3
    assert bundle['from'] == [] and bundle['to'] == ['GAT2']
    all_passed(doc)


def test_a2_a_new_electrode_on_three_layers_is_one_bundle_with_its_cno(tmp_path):
    """K2 / L4: every layer of a new electrode and its CNO line go to one `ele` bundle (K6)."""
    names = ['Data', 'X1', 'X2', 'X3']
    layers = ('GAT2', 'SD1', 'SD2')
    dl1 = dl1_all(names, layers)
    del dl1['reference']['Data']                      # Data exists in B only
    pairs = [pair(f'N{i}', ['Data', f'X{i}'], 'line', None, [lateral(['Data', f'X{i}'], .01, layer)])
             for i, layer in zip((1, 2, 3), layers)]
    doc, _ = run(tmp_path, pairs, dl1=dl1, cno=[('Data', 'SD1', None, .004)])
    got = bundles(doc)
    assert list(got) == [('ele', 'Data')]
    bundle = got[('ele', 'Data')]
    assert bundle['type'] == 'electrode_new' and bundle['atoms']['count'] == 4 and bundle['layers'] == list(layers)
    assert {row['layer']: row['cno'] for row in bundle['layer_rows']}['SD1'] == pytest.approx(doc['cno'][0]['delta'])
    assert bundle['kinds']['pixel']['net'] == pytest.approx(doc['cno'][0]['delta'])
    assert doc['summary']['bundles']['types']['pixel']['electrode_new']['count'] == 1
    assert doc['summary']['bundles']['types']['pixel']['cno']['count'] == 0       # no independent CNO bundle
    all_passed(doc)


def test_a3_a_removed_electrode_is_one_bundle(tmp_path):
    names = ['Data', 'X1', 'X2']
    dl1 = dl1_all(names, ('SD1', 'SD2'))
    del dl1['target']['Data']
    pairs = [pair(f'N{i}', ['Data', f'X{i}'], 'line', [lateral(['Data', f'X{i}'], .01, layer)], None)
             for i, layer in zip((1, 2), ('SD1', 'SD2'))]
    doc, _ = run(tmp_path, pairs, dl1=dl1)
    got = bundles(doc)
    assert list(got) == [('ele', 'Data')] and got[('ele', 'Data')]['type'] == 'electrode_removed'
    all_passed(doc)


def test_a4_two_new_electrodes_make_a_pair_bundle(tmp_path):
    names = ['N1', 'N2']
    dl1 = dl1_all(names + ['Z'])                      # Z keeps the reference inventory complete
    del dl1['reference']['N1'], dl1['reference']['N2']
    pairs = [pair('B1', names, 'line', None, [lateral(names, .01, 'SD1')])]
    doc, _ = run(tmp_path, pairs, dl1=dl1)
    got = bundles(doc)
    assert list(got) == [('p', 'B1', 'SD1')] and got[('p', 'B1', 'SD1')]['why'] == ['both_new_removed']


def test_b_hub_takes_its_partners_and_the_larger_hub_wins(tmp_path):
    partners = ['P0', 'P1', 'P2', 'P3', 'P4']
    names = ['Scan3'] + partners + ['Q1', 'Q2']
    pairs = hub_pairs('Scan3', partners, 'H') + hub_pairs('P0', ['Q1', 'Q2'], 'K')
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(names))
    got = bundles(doc)
    hub = got[('hub', 'Scan3', 'SD1')]
    assert hub['type'] == 'hub' and hub['partner_count'] == 5          # P0 (3 changed partners) is a hub, Scan3 has 5
    assert ('p', 'H0', 'SD1') not in got and hub['partners_elsewhere'] == []
    assert ('hub', 'P0', 'SD1') in got                                 # P0-Q1, P0-Q2: P0 hub, Q not
    all_passed(doc)


def test_b2_both_ends_hubs_with_the_same_number_of_changed_partners_give_the_pair(tmp_path):
    names = ['S', 'T'] + [f'S{i}' for i in range(3)] + [f'T{i}' for i in range(3)]
    pairs = (hub_pairs('S', ['T'] + names[2:5], 'H') + hub_pairs('T', names[5:], 'K'))     # S: T,S0,S1,S2   T: S,T0..T2
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(names))
    got = bundles(doc)
    assert got[('p', 'H0', 'SD1')]['why'] == ['hubs']                  # 4 vs 4
    assert ('hub', 'S', 'SD1') in got and ('hub', 'T', 'SD1') in got


def test_b3_both_ends_hubs_the_one_with_more_changed_partners_takes_the_pair(tmp_path):
    names = ['S', 'T'] + [f'S{i}' for i in range(4)] + [f'T{i}' for i in range(3)]
    pairs = hub_pairs('S', ['T'] + names[2:6], 'H') + hub_pairs('T', names[6:], 'K')   # S: 5, T: 4
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(names))
    got = bundles(doc)
    assert ('p', 'H0', 'SD1') not in got and got[('hub', 'S', 'SD1')]['partner_count'] == 5


def test_b4_a_hub_does_not_need_one_sign_two_increases_and_two_decreases(tmp_path):
    names = ['S', 'U0', 'U1', 'D0', 'D1']
    pairs = hub_pairs('S', ['U0', 'U1'], 'U') + hub_pairs('S', ['D0', 'D1'], 'D', lam_a=.020, lam_b=.010)
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(names))
    hub = bundles(doc)[('hub', 'S', 'SD1')]
    assert hub['atoms'] == {'increase': 2, 'decrease': 2, 'count': 4}


def test_b5_partners_inside_the_negligible_limit_are_not_counted_for_a_hub(tmp_path):
    names = ['S', 'A', 'B', 'C']
    pairs = hub_pairs('S', ['A', 'B'], 'U') + [up('Z', ('S', 'C'), None, .010, .010 * (1 + 1e-12))]
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(names))
    got = bundles(doc)
    assert ('hub', 'S', 'SD1') not in got and got[('p', 'Z', 'SD1')]['why'] == ['nohub']


def test_b6_the_fraction_condition_keeps_a_partly_changed_electrode_from_being_a_hub(tmp_path, monkeypatch):
    names = ['Data'] + [f'C{i}' for i in range(3)] + [f'U{i}' for i in range(17)]
    pairs = (hub_pairs('Data', names[1:4], 'H') +                                       # 3 changed partners
             [up(f'Q{i}', ('Data', f'U{i}'), None, .010, .010) for i in range(17)])     # 17 unchanged partners
    (tmp_path / 'default').mkdir()
    doc, _ = run(tmp_path / 'default', pairs, dl1=dl1_all(names))
    got = bundles(doc)                                    # default 0.5: 3 of 20 changed -> not a hub, three pairs
    assert ('hub', 'Data', 'SD1') not in got and {k for k in got} == {('p', f'H{i}', 'SD1') for i in range(3)}
    assert doc['summary']['bundles']['hub_min_fraction'] == 0.5 and doc['summary']['rules']['hub_min_fraction'] == 0.5
    assert doc['summary']['bundles']['hub_min_share'] == 0.0 and doc['summary']['rules']['hub_min_share'] == 0.0
    monkeypatch.setattr(ledger, 'HUB_MIN_FRACTION', 0.0)
    (tmp_path / 'off').mkdir()
    off, _ = run(tmp_path / 'off', pairs, dl1=dl1_all(names))
    assert ('hub', 'Data', 'SD1') in bundles(off) and off['summary']['rules']['hub_min_fraction'] == 0.0
    hub = bundles(off)[('hub', 'Data', 'SD1')]                  # R17:1: 17 unchanged partners are inside, not changed
    assert hub['partner_count'] == 20 and hub['hub']['changed_count'] == 3
    assert hub['hub']['changed_partners'] == ['C0', 'C1', 'C2'] and hub['hub']['partner_total'] == 20
    assert hub['hub']['fraction'] == pytest.approx(0.15)
    assert hub['hub']['increase'] == 3 and hub['hub']['decrease'] == 0
    assert 'hub_sensitivity_atoms' in off['summary']['rules']
    assert all(b['hub'] is None for b in bundles(doc).values())    # the default run has no hub bundle


def test_b6b_the_share_condition_drops_tiny_partner_changes(tmp_path, monkeypatch):
    names = ['S', 'A', 'B', 'C', 'D', 'E', 'F']
    pairs = hub_pairs('S', ['A', 'B', 'C'], 'T', .010, .010 * (1 + 1e-6)) + hub_pairs('S', ['D', 'E', 'F'], 'B', .010, .020)
    monkeypatch.setattr(ledger, 'HUB_MIN_FRACTION', 0.0)
    (tmp_path / 'a').mkdir()
    doc, _ = run(tmp_path / 'a', pairs, dl1=dl1_all(names))
    assert ('hub', 'S', 'SD1') in bundles(doc)                     # share off: six changed partners
    monkeypatch.setattr(ledger, 'HUB_MIN_SHARE', 0.01)
    (tmp_path / 'b').mkdir()
    doc, _ = run(tmp_path / 'b', pairs, dl1=dl1_all(names))        # the three tiny changes are < 1 % of G
    got = bundles(doc)
    assert ('hub', 'S', 'SD1') in got and got[('hub', 'S', 'SD1')]['partner_count'] == 6   # D, E, F + the tiny ones that
    hub = got[('hub', 'S', 'SD1')]['hub']                          # R17:1: the tiny partners are not changed partners
    assert hub['changed_partners'] == ['D', 'E', 'F'] and hub['changed_count'] == 3 and hub['partner_total'] == 6
    assert hub['fraction'] == pytest.approx(0.5) and hub['increase'] == 3 and hub['decrease'] == 0
    only_tiny = hub_pairs('S', ['A', 'B', 'C'], 'T', .010, .010 * (1 + 1e-6)) + [up('Big', ('X', 'Y'), None, .010, .5)]
    (tmp_path / 'c').mkdir()
    doc, _ = run(tmp_path / 'c', only_tiny, dl1=dl1_all(['S', 'A', 'B', 'C', 'X', 'Y']))
    assert ('hub', 'S', 'SD1') not in bundles(doc)                 # 3 tiny changes only: not a hub with share 0.01
    assert doc['summary']['rules']['hub_min_share'] == 0.01


def test_b6d_hub_with_mixed_signs_counts_changed_partners_by_direction(tmp_path):
    names = ['S', 'U0', 'U1', 'D0', 'D1', 'Z0']
    pairs = (hub_pairs('S', ['U0', 'U1'], 'U') + hub_pairs('S', ['D0', 'D1'], 'D', lam_a=.020, lam_b=.010) +
             [up('Q', ('S', 'Z0'), None, .010, .010)])
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(names))
    hub = bundles(doc)[('hub', 'S', 'SD1')]
    assert hub['hub'] == {'changed_partners': ['D0', 'D1', 'U0', 'U1'], 'changed_count': 4, 'partner_total': 5,
                          'fraction': pytest.approx(0.8), 'increase': 2, 'decrease': 2}
    assert hub['partner_count'] == 5


def test_b6e_unverifiable_electrodes_still_judge_a_hub_as_unchanged_state(tmp_path):
    names = ['S', 'P0', 'P1', 'P2']
    pairs = hub_pairs('S', ['P0', 'P1', 'P2'], 'H')
    doc, _ = run(tmp_path, pairs)                                  # no DL1 inventory: every electrode is unverifiable
    hub = bundles(doc)[('hub', 'S', 'SD1')]
    assert hub['layer_state_unverified'] is True
    assert hub['hub']['changed_count'] == 3 and hub['hub']['partner_total'] == 3 and hub['hub']['increase'] == 3


def test_b6c_hub_sensitivity_has_six_settings_and_leaves_the_real_assignment_alone(tmp_path):
    names = ['Data'] + [f'C{i}' for i in range(3)] + [f'U{i}' for i in range(17)]
    pairs = (hub_pairs('Data', names[1:4], 'H') + [up(f'Q{i}', ('Data', f'U{i}'), None, .010, .010) for i in range(17)])
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(names))
    rows = doc['summary']['bundles']['hub_sensitivity']
    assert [(r['hub_min_fraction'], r['hub_min_share']) for r in rows] == [
        (0.0, 0.0), (0.0, 0.01), (0.3, 0.0), (0.3, 0.01), (0.5, 0.0), (0.5, 0.01)]
    by = {(r['hub_min_fraction'], r['hub_min_share']): r['kinds']['line'] for r in rows}
    # 3 changed of 20 partners: 0.15 < 0.3, so only fraction 0 gives the hub; each change is 1/3 of G (>= 1 %)
    # the hub takes all 20 atoms of its partners (17 unchanged ones too); they carry all of G
    assert by[(0.0, 0.0)] == {'hubs': 1, 'atoms': 20, 'share': pytest.approx(1.0)}
    assert by[(0.0, 0.01)] == {'hubs': 1, 'atoms': 20, 'share': pytest.approx(1.0)}
    for key in ((0.3, 0.0), (0.3, 0.01), (0.5, 0.0), (0.5, 0.01)):
        assert by[key] == {'hubs': 0, 'atoms': 0, 'share': 0.0}
    assert all(r['kinds']['pixel'] == {'hubs': 0, 'atoms': 0, 'share': None} for r in rows)
    assert ('hub', 'Data', 'SD1') not in bundles(doc)                 # the real assignment used the default only


def test_b7_layer_composition_change_with_add_remove_and_move_is_one_bundle(tmp_path):
    dl1 = dl1_all(['E', 'F', 'G', 'H'], ('SD1', 'SD2', 'GAT2'))
    dl1['reference']['E'], dl1['target']['E'] = ['SD1', 'SD2'], ['GAT2', 'SD2']      # SD1 removed, GAT2 added
    pairs = [pair('M1', ['E', 'F'], 'line', [lateral(['E', 'F'], .01, 'SD1')], None),
             pair('M2', ['E', 'G'], 'line', None, [lateral(['E', 'G'], .02, 'GAT2')]),
             pair('M3', ['E', 'H'], 'line', [lateral(['E', 'H'], .01, 'SD2')], [lateral(['E', 'H'], .02, 'SD2')])]
    doc, _ = run(tmp_path, pairs, dl1=dl1)
    got = bundles(doc)
    assert sorted(got) == [('lyr', 'E'), ('p', 'M3', 'SD2')]               # SD2 did not change: the unchanged layer is a pair
    assert got[('lyr', 'E')]['from'] == ['SD1'] and got[('lyr', 'E')]['to'] == ['GAT2']
    assert got[('lyr', 'E')]['atoms']['count'] == 2 and got[('lyr', 'E')]['layers'] == ['GAT2', 'SD1']
    all_passed(doc)


def test_b8_both_electrodes_with_a_layer_change_make_a_pair(tmp_path):
    dl1 = dl1_all(['E', 'F'], ('SD1', 'GAT2'))
    dl1['reference']['E'], dl1['reference']['F'] = ['SD1'], ['SD1']
    pairs = [pair('B1', ['E', 'F'], 'line', None, [lateral(['E', 'F'], .01, 'GAT2')])]
    doc, _ = run(tmp_path, pairs, dl1=dl1)
    assert list(bundles(doc)) == [('p', 'B1', 'GAT2')]


def test_b9_one_electrode_atoms_are_self_unless_new_or_layer_changed(tmp_path):
    pairs = [pair('S', ['X', 'X'], 'line', [vertical([('X', 'SD1'), ('X', 'SD2')], .01, 'SD1', 'SD2')],
                  [vertical([('X', 'SD1'), ('X', 'SD2')], .02, 'SD1', 'SD2')])]
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(['X'], ('SD1', 'SD2')))
    got = bundles(doc)
    assert list(got) == [('self', 'X', 'SD1')] and got[('self', 'X', 'SD1')]['type'] == 'self'
    assert doc['summary']['bundles']['types']['line']['self']['count'] == 1


def test_b10_three_or_more_electrodes_make_a_pair_whatever_their_attributes(tmp_path):
    dl1 = dl1_all(['X', 'Y', 'Z'], ('SD1', 'GAT2'))
    del dl1['reference']['X']                                              # X is new
    pairs = [pair('M', ['X', 'Y', 'Z'], 'line', None,
                  [item('lateral_short', [('X', 'SD1'), ('Y', 'SD1'), ('Z', 'SD1')], .01, layer='SD1')])]
    doc, _ = run(tmp_path, pairs, dl1=dl1)
    got = bundles(doc)
    assert list(got) == [('p', 'M', 'SD1')] and got[('p', 'M', 'SD1')]['why'] == ['multi']


def test_c_two_partners_are_not_a_hub(tmp_path):
    pairs = hub_pairs('E', ['F', 'G'], 'T')
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(['E', 'F', 'G']))
    got = bundles(doc)
    assert set(got) == {('p', 'T0', 'SD1'), ('p', 'T1', 'SD1')}
    assert all(b['why'] == ['nohub'] for b in got.values())


def test_d_a_moved_electrode_is_one_layer_change_bundle_with_from_and_to(tmp_path):
    dl1 = dl1_all(['E', 'F', 'G'], ('SD1', 'SD2'))
    dl1['reference']['E'], dl1['target']['E'] = ['SD1'], ['SD2']
    pairs = [pair('M1', ['E', 'F'], 'line', [lateral(['E', 'F'], .01, 'SD1')], None),
             pair('M2', ['E', 'G'], 'line', None, [lateral(['E', 'G'], .02, 'SD2')])]
    doc, _ = run(tmp_path, pairs, dl1=dl1)
    got = bundles(doc)
    assert list(got) == [('lyr', 'E')]
    move = got[('lyr', 'E')]
    assert move['type'] == 'layer_change' and move['from'] == ['SD1'] and move['to'] == ['SD2']
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


def test_f_an_independent_cno_is_a_cno_bundle_and_is_not_counted_twice(tmp_path):
    pairs = [up('P1', ('E', 'F'), None)]
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(['E', 'F', 'Z']), cno=[('Z', 'SD1', .005, .009)])
    got = bundles(doc)
    z = got[('cno', 'Z', 'SD1')]
    assert z['type'] == 'cno' and z['kinds']['pixel']['net'] == pytest.approx(doc['cno'][0]['delta'])
    assert z['rows'][0]['cno'] is True and z['rows'][0]['pair_id'] is None
    types = doc['summary']['bundles']['types']['pixel']
    assert types['cno']['net'] == pytest.approx(doc['cno'][0]['delta'])
    assert types['cno_all']['net'] == pytest.approx(doc['cno'][0]['delta']) and types['cno_all']['reference'] is True
    assert sum(types[name]['net'] for name in ledger.TYPES) + types['crp']['net'] == pytest.approx(
        doc['totals']['pixel']['delta'])
    all_passed(doc)


def test_f2_a_cno_of_an_electrode_with_a_layer_change_goes_to_its_lyr_bundle(tmp_path):
    """B stage: the electrode attributes decide, whether or not another atom made the bundle first."""
    dl1 = dl1_all(['E', 'F', 'Z'], ('SD1', 'GAT2'))
    dl1['reference']['Z'] = ['SD1']                                       # Z gains GAT2
    pairs = [up('P1', ('E', 'F'), None)]
    doc, _ = run(tmp_path, pairs, dl1=dl1, cno=[('Z', 'SD1', .005, .009)])
    got = bundles(doc)
    assert ('lyr', 'Z') in got and ('cno', 'Z', 'SD1') not in got
    types = doc['summary']['bundles']['types']['pixel']
    assert types['layer_change']['net'] == pytest.approx(doc['cno'][0]['delta'])
    assert types['cno']['count'] == 0 and types['cno_all']['net'] == pytest.approx(doc['cno'][0]['delta'])
    all_passed(doc)


def test_g_mixed_sign_hub_keeps_both_signs_in_one_bundle(tmp_path):
    names = ['S'] + [f'U{i}' for i in range(3)] + [f'D{i}' for i in range(3)]
    pairs = (hub_pairs('S', names[1:4], 'U') + hub_pairs('S', names[4:], 'D', lam_a=.020, lam_b=.010))
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(names))
    hub = bundles(doc)[('hub', 'S', 'SD1')]
    assert hub['atoms'] == {'increase': 3, 'decrease': 3, 'count': 6}
    assert hub['kinds']['line']['increase'] == 3 and hub['kinds']['line']['decrease'] == 3


def test_h_atoms_inside_the_tolerance_do_not_count_toward_a_hub(tmp_path):
    names = ['S', 'A', 'B', 'C']
    pairs = hub_pairs('S', ['A', 'B'], 'U') + [up('Z', ('S', 'C'), None, .010, .010 * (1 + 1e-12))]
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(names))
    got = bundles(doc)
    assert ('hub', 'S', 'SD1') not in got                            # two counted partners only
    assert got[('p', 'Z', 'SD1')]['why'] == ['nohub']


def test_h5_tolerance_edge_is_judged_on_the_summed_values(tmp_path):
    names = ['S', 'A', 'B', 'C']
    for lam_b, hub in ((.010 * (1 + 1e-12), False), (.010 * (1 + 1e-6), True)):
        sub = tmp_path / str(hub)
        sub.mkdir()
        pairs = hub_pairs('S', ['A', 'B'], 'U') + [up('Z', ('S', 'C'), None, .010, lam_b)]
        doc, _ = run(sub, pairs, dl1=dl1_all(names))
        assert (('hub', 'S', 'SD1') in bundles(doc)) is hub


def test_i_a_kind_that_cannot_be_computed_is_not_evaluated(tmp_path):
    pairs = [up('P1', ('E', 'F'), None, cls='pixel')]
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(['E', 'F']), pixel_null=True)
    status = doc['summary']['bundles']['status']
    assert status['pixel'] == 'not_evaluated' and doc['summary']['bundles']['reasons']['pixel'] == ['kind_not_computable']
    ranking = doc['summary']['bundles']['ranking']['pixel']
    assert ranking['direction'] is None and ranking['zero'] == []
    for name in ('increase', 'decrease'):
        assert ranking[name] == {'total': None, 'k80': None, 'cards': [], 'rest': [], 'cover': None}
    assert doc['summary']['bundles']['types']['pixel'] is None
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
    assert ranking['direction'] == ranking['main'] == 'increase' and ranking['decrease']['cards'] == []
    main = ranking['increase']
    assert len(main['cards']) == 6 and len(main['rest']) == 2 and ranking['zero'] == []
    shares = {b['id']: b['kinds']['line']['direction_share'] for b in bundles_doc['list']}
    assert math.fsum(shares[i] for i in main['cards'] + main['rest']) == pytest.approx(1.0)
    assert main['cover'] == pytest.approx(math.fsum(shares[i] for i in main['cards']))
    assert main['k80'] == 6 or main['k80'] == 7
    assert main['total'] == pytest.approx(doc['totals']['line']['delta'])
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


def mixed_world():
    """Pair, hub, mixed why (['hubs', 'nohub']), CNO, moved electrode, 3-electrode (multi) atoms in one list."""
    atoms, pair_rows = [], {}

    def add(aid, kind, pid, ends, a, b, layer='SD1', cno=False):
        atoms.append({'id': aid, 'kind': kind, 'pair_id': None if cno else pid, 'cno': cno, 'layer': layer,
                      'ends': list(ends), 'a': a, 'b': b, 'd': b - a, 'presence': 'both'})
        if not cno:
            row = pair_rows.setdefault(pid, {'delta': 0.0, 'electrodes': sorted({n for n, _l in ends})})
            row['delta'] += b - a

    for i, partner in enumerate('ABC'):                       # S: hub with three partners at sign +
        add(f'hs{i}', 'line', f'S{partner}', [('S', 'SD1'), (partner, 'SD1')], .010, .020 + .001 * i)
    for i, partner in enumerate('DEF'):                       # T: hub with three partners at sign +
        add(f'ht{i}', 'line', f'T{partner}', [('T', 'SD1'), (partner, 'SD1')], .010, .030 + .001 * i)
    add('st0', 'line', 'ST', [('S', 'SD1'), ('T', 'SD1')], .010, .025)      # both ends hubs -> 'hubs'
    add('st1', 'line', 'ST', [('S', 'SD1'), ('T', 'SD1')], .010, .010 + 1e-15)   # inside tolerance -> 'nohub'
    add('mv0', 'pixel', 'MN', [('M', 'SD1'), ('N', 'SD1')], .004, .009)     # M moved: its layer state changed
    add('cn0', 'pixel', None, [('N', 'SD1')], .001, .003, cno=True)         # CNO line of N
    add('mu0', 'line', 'XYZ', [('X', 'SD1'), ('Y', 'SD1'), ('Z', 'SD1')], .002, .006)   # three electrodes
    names = ['S', 'T', 'M', 'N', 'X', 'Y', 'Z'] + list('ABCDEF')
    erows = {n: {'electrode': n, 'layer_state_status': 'verified', 'layer_state': {'SD1': 'both'}, 'moved': False}
             for n in names}
    erows['M'] = {'electrode': 'M', 'layer_state_status': 'verified', 'moved': True,
                  'layer_state': {'SD1': 'a_only', 'GAT2': 'b_only'}}
    g = {k: math.fsum(abs(a['d']) for a in atoms if a['kind'] == k) for k in ('line', 'pixel')}
    kstat = {k: ('ok', [], g[k]) for k in g}
    totals = {k: {'delta': math.fsum(a['d'] for a in atoms if a['kind'] == k)} for k in g}
    return atoms, kstat, erows, pair_rows, totals


def test_r10_2_a_mixed_atom_list_gives_identical_bytes_and_the_expected_reasons():
    atoms, kstat, erows, pair_rows, totals = mixed_world()
    outs, docs = [], []
    for seed in range(6):
        shuffled = [dict(a) for a in atoms]
        random.Random(seed).shuffle(shuffled)
        got, _ = ledger._bundle_summary(shuffled, kstat, erows, pair_rows, 0.0, totals, str, {'line': 1.0, 'pixel': 1.0})
        outs.append(json.dumps(got, sort_keys=False))
        docs.append(got)
    assert len(set(outs)) == 1
    by_key = {tuple(b['key']): b for b in docs[0]['list']}
    assert by_key[('p', 'ST', 'SD1')]['why'] == ['hubs']                       # 4 changed partners each: the pair
    assert by_key[('p', 'XYZ', 'SD1')]['why'] == ['multi']                     # (c) three electrodes
    assert ('lyr', 'M') in by_key and ('cno', 'N', 'SD1') in by_key            # a layer change and an independent CNO
    cno = by_key[('cno', 'N', 'SD1')]
    assert any(row['cno'] for row in cno['rows'])


def test_l1_a_bundle_inside_the_limit_keeps_its_sign_its_direction_and_its_place_in_the_sums():
    atoms = []
    for i, d in enumerate((.05, .04, 1e-13)):
        atoms.append({'id': f'i{i}', 'kind': 'line', 'pair_id': f'P{i}', 'cno': False, 'layer': 'SD1',
                      'ends': [(f'S{i}', 'SD1'), (f'T{i}', 'SD1')], 'a': .01, 'b': .01 + d, 'd': d, 'presence': 'both'})
    names = [n for a in atoms for n, _l in a['ends']]
    erows = {n: {'electrode': n, 'layer_state_status': 'verified', 'layer_state': {'SD1': 'both'}, 'moved': False}
             for n in names}
    pair_rows = {f'P{i}': {'delta': a['d'], 'electrodes': [a['ends'][0][0], a['ends'][1][0]]}
                 for i, a in enumerate(atoms)}
    g = math.fsum(a['d'] for a in atoms)
    kstat = {'line': ('ok', [], g), 'pixel': ('no_change', [], 0.0)}
    totals = {'line': {'delta': g}, 'pixel': {'delta': 0.0}}
    got, checks = ledger._bundle_summary(atoms, kstat, erows, pair_rows, 0.0, totals, str, {'line': 1.0, 'pixel': 0.0})
    ranking = got['ranking']['line']
    assert ranking['direction'] == 'increase' and len(ranking['increase']['cards']) == 3      # L1: raw sign, one card
    assert ranking['increase']['rest'] == [] and ranking['increase']['k80'] <= 2 and ranking['zero'] == []
    tiny = [b for b in got['list'] if b['kinds']['line']['abs'] < 1e-12][0]['kinds']['line']
    assert tiny['sign'] == 'increase' and tiny['cancelling'] is False
    assert checks['assigned'] == 3 and checks['duplicates'] == 0 and checks['kinds']['line']['each_once']['status'] == 'passed'
    assert all(c['status'] in ('passed', 'not_evaluated') for c in checks['kinds']['line'].values())
    assert ledger.SUMMARY_RULES['negligible']['abs'] == ledger.NO_CHANGE_ABS


def _design_args(design_deltas, vi_a=.25):
    pair_rows = {f'P{i}': {'pair_id': f'P{i}', 'electrodes': [f'E{i}', f'F{i}'],
                           'items': [{'class': 'line', 'ca_delta': d, 'delta': .001}]}
                 for i, d in enumerate(design_deltas)}
    segments = {'ca': math.fsum(design_deltas), 'f': .05, 'crp': 0.0, 'density': 0.0}
    rebase = {'missing': [], 'coefficients': {'ratios': {}}, 'check': {'line': {'status': 'passed'}}}
    basis = {'status': 'available', 'reasons': [], 'segments': segments}
    return rebase, basis, pair_rows, vi_a


def test_r10_1_a_design_difference_at_zero_noise_has_no_sign_and_is_marked_negligible():
    rebase, basis, pair_rows, vi_a = _design_args([7.8e-18, -3.5e-18])      # same CA: only float residue
    out = ledger._design_summary('line', rebase, basis, pair_rows, [], vi_a, {'line': {'delta': .05}})
    assert out['top_increase'] == [] and out['top_decrease'] == []
    assert out['negligible'] == ['design', 'crp', 'density']
    assert out['a_relative']['design'] == pytest.approx(100.0 * out['segments']['design'] / vi_a)   # raw value kept
    assert 'resolution' not in out['negligible']


def test_r10_1_the_limit_is_abs_plus_rel_times_vi_a_pairs_just_inside_and_outside():
    vi_a = .25
    limit = ledger.NO_CHANGE_ABS + ledger.NO_CHANGE_REL * vi_a
    rebase, basis, pair_rows, _ = _design_args([.5 * limit, -.5 * limit, 2 * limit, -2 * limit])
    out = ledger._design_summary('line', rebase, basis, pair_rows, [], vi_a, {'line': {'delta': .05}})
    assert [e['key'] for e in out['top_increase']] == ['P2'] and [e['key'] for e in out['top_decrease']] == ['P3']
    assert 'design' in out['negligible']                    # the summed design segment is 0 here


def test_r10_3_missing_cells_are_split_by_scope_and_a_null_share_is_flagged(tmp_path):
    def put(doc):
        miss = [{'scope': 'item', 'kind': 'pixel', 'item_id': 'i1', 'density_layer': 'SD1', 'diameter_um': 3.0,
                 'area_b_um2': 1.0, 'lambda_b_share': .25},
                {'scope': 'cno', 'kind': 'pixel', 'electrode_id': 'E1', 'density_layer': 'SD1', 'diameter_um': 3.0,
                 'area_b_um2': 2.0, 'lambda_b_share': None},
                {'scope': 'cno', 'kind': 'pixel', 'electrode_id': 'E2', 'density_layer': 'SD1', 'diameter_um': 3.0,
                 'area_b_um2': 3.0, 'lambda_b_share': .5},
                {'scope': 'cell', 'kind': 'pixel', 'density_layer': 'SD1', 'diameter_um': 3.0,
                 'area_b_um2': 4.0, 'lambda_b_share': .125}]
        doc['rebase'] = {'status': 'partial', 'reasons': [], 'missing': miss, 'coefficients': {'ratios': {}},
                         'states': {'line': {'status': 'unavailable', 'reasons': ['r']},
                                    'pixel': {'status': 'unavailable', 'reasons': ['r']}},
                         'items': [], 'cno': [], 'check': {}}
    doc, _ = run(tmp_path,[up('P1', ('E', 'F'), None)], mutate=put)
    cells = {c['scope']: c for c in doc['summary']['design']['pixel']['missing_cells']}
    assert sorted(cells) == ['cell', 'cno', 'item']
    assert cells['item']['item_count'] == 1 and cells['item']['cno_count'] == 0 and cells['item']['share_partial'] is False
    assert cells['cno']['cno_count'] == 2 and cells['cno']['item_count'] == 0
    assert cells['cno']['lambda_b_share'] == .5 and cells['cno']['share_partial'] is True
    assert cells['cno']['area_b_um2'] == 5.0


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
    assert ('lyr', 'Y') in got and got[('lyr', 'Y')]['type'] == 'layer_change'     # the B-side end point on SD2
    assert got[('lyr', 'Y')]['rows'][0]['layer'] == 'SD1'             # the line shows the item's analysis layer
    assert ('p', 'V', 'SD1') in got                                   # the A-side item has no changed end point


def test_h1_the_opposite_direction_item_follows_its_own_end_points(tmp_path):
    dl1 = dl1_all(['X', 'Y'], ('SD1', 'SD2'))
    dl1['reference']['X'] = ['SD1']                                   # X is on SD2 in B only
    pairs = [pair('V', ['X', 'Y'], 'line', None, [vertical([('X', 'SD2'), ('Y', 'SD1')], .02, 'SD2', 'SD1')])]
    doc, _ = run(tmp_path, pairs, dl1=dl1)
    assert list(bundles(doc)) == [('lyr', 'X')]


def test_h1_one_electrode_at_both_ends_goes_to_that_electrode(tmp_path):
    dl1 = dl1_all(['X'], ('SD1', 'SD2'))
    pairs = [pair('S', ['X', 'X'], 'line', [vertical([('X', 'SD1'), ('X', 'SD2')], .01, 'SD1', 'SD2')],
                  [vertical([('X', 'SD1'), ('X', 'SD2')], .02, 'SD1', 'SD2')])]
    doc, _ = run(tmp_path, pairs, dl1=dl1)
    got = bundles(doc)
    assert list(got) == [('self', 'X', 'SD1')] and got[('self', 'X', 'SD1')]['type'] == 'self'


def test_h1_a_missing_state_is_unknown_and_a_cno_without_state_is_cno(tmp_path):
    pairs = [up('P1', ('E', 'F'), None)]
    doc, _ = run(tmp_path, pairs, dl1=dl1_all(['E', 'F', 'Z']), cno=[('Z', 'GAT9', .005, .009)])
    z = bundles(doc)[('cno', 'Z', 'GAT9')]
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


# ---- rnarr/T11 K1 / L1 / L2 / L3: direction based ranking, types ------------------------------------------------------

def synth(deltas, crp=0.0, kind='line', vi=1.0):
    """One pair atom per delta (its own electrodes, no hub): `_bundle_summary` of a hand-made world."""
    atoms = []
    for i, d in enumerate(deltas):
        atoms.append({'id': f'i{i:04d}', 'kind': kind, 'pair_id': f'P{i:04d}', 'cno': False, 'layer': 'SD1',
                      'ends': [(f'S{i}', 'SD1'), (f'T{i}', 'SD1')], 'a': .01, 'b': .01 + d, 'd': d, 'presence': 'both'})
    names = [n for a in atoms for n, _l in a['ends']]
    erows = {n: {'electrode': n, 'status': 'common', 'layer_state_status': 'verified',
                 'layer_state': {'SD1': 'both'}, 'moved': False} for n in names}
    pair_rows = {a['pair_id']: {'delta': a['d'], 'electrodes': [a['ends'][0][0], a['ends'][1][0]]} for a in atoms}
    other = 'pixel' if kind == 'line' else 'line'
    g = math.fsum(abs(d) for d in deltas)
    kstat = {kind: ('ok', [], g), other: ('no_change', [], 0.0)}
    delta = math.fsum(deltas) + (crp if kind == 'pixel' else 0.0)
    totals = {kind: {'delta': delta}, other: {'delta': 0.0}}
    vi_a = {kind: vi, other: 0.0}
    return atoms, kstat, erows, pair_rows, crp if kind == 'pixel' else 0.0, totals, vi_a


def bundle_doc(deltas, **kw):
    atoms, kstat, erows, pair_rows, crp, totals, vi_a = synth(deltas, **kw)
    return ledger._bundle_summary(atoms, kstat, erows, pair_rows, crp, totals, str, vi_a)


def by_net(got, kind='line'):
    return {round(b['kinds'][kind]['net'], 9): b['id'] for b in got['list']}


def check_values(checks, kind='line'):
    return {name: c['status'] for name, c in checks['kinds'][kind].items()}


def test_k1_net_negative_but_the_largest_net_is_positive_main_cards_follow_the_direction():
    got, checks = bundle_doc([-.05, -.04, .06, .01])             # sum -0.02
    ranking, ids = got['ranking']['line'], by_net(got)
    assert ranking['direction'] == ranking['main'] == 'decrease'
    assert ranking['decrease']['cards'] == [ids[-.05], ids[-.04]]
    assert ranking['increase']['cards'] == [ids[.06], ids[.01]]          # the opposite direction: "상쇄 요인"
    assert ranking['decrease']['total'] == pytest.approx(-.09) and ranking['increase']['total'] == pytest.approx(.07)
    assert ranking['decrease']['k80'] == 2 and ranking['increase']['k80'] == 1       # .06 / .07 = 86 %
    shares = {b['id']: b['kinds']['line']['direction_share'] for b in got['list']}
    assert shares[ids[-.05]] == pytest.approx(.05 / .09) and shares[ids[.06]] == pytest.approx(.06 / .07)
    assert ranking['cancel_ratio'] == pytest.approx(1 - .02 / .16)
    assert all(status == 'passed' for status in check_values(checks).values()), checks['kinds']['line']


def test_k1_the_gross_share_keeps_its_name_and_meaning():
    got, _ = bundle_doc([-.05, .06])
    for b in got['list']:
        kind = b['kinds']['line']
        assert kind['share'] == pytest.approx(abs(kind['net']) / .11) and kind['abs'] == pytest.approx(abs(kind['net']))
    assert got['version'] == 2 and got['rule'] == 'bundle_assign_v2' and got['hub_min_fraction'] == 0.5
    assert got['hub_min_share'] == 0.0


def test_k1_a_balanced_kind_has_both_directions_with_six_cards_and_a_main_by_the_larger_total():
    got, checks = bundle_doc([.03, -.03])
    ranking = got['ranking']['line']
    assert ranking['direction'] == 'balanced' and ranking['main'] == 'decrease'      # equal totals: decrease
    got, _ = bundle_doc([.04, -.03])
    assert got['ranking']['line']['direction'] == 'increase'
    values = [.01 * (i + 1) for i in range(7)]
    got, checks = bundle_doc(values + [-v for v in values])
    ranking = got['ranking']['line']
    assert ranking['direction'] == 'balanced'
    assert len(ranking['increase']['cards']) == 6 and len(ranking['decrease']['cards']) == 6      # six on both sides
    assert len(ranking['increase']['rest']) == 1 and len(ranking['decrease']['rest']) == 1
    assert set(check_values(checks).values()) == {'passed'}


def test_k1_crp_can_dominate_the_kind_while_the_bundles_have_their_own_direction():
    got, checks = bundle_doc([.01, .02], crp=-.05, kind='pixel')      # sum +0.03, delta -0.02
    ranking = got['ranking']['pixel']
    assert ranking['direction'] == 'increase' and got['crp']['pixel'] == -.05
    assert checks['kinds']['pixel']['direction_identity']['status'] == 'passed'
    assert checks['kinds']['pixel']['direction_identity']['target'] == pytest.approx(-.02)
    assert got['types']['pixel']['crp']['net'] == -.05
    assert checks['kinds']['pixel']['types_plus_crp']['status'] == 'passed'


def test_l1_a_thousand_bundles_at_half_the_limit_keep_the_identity():
    limit = ledger._negligible_limit(1.0)
    got, checks = bundle_doc([.5 * limit] * 1000)
    ranking = got['ranking']['line']
    assert len(got['list']) == 1000 and ranking['direction'] == 'increase'          # |sum| = 500 x limit
    assert ranking['increase']['total'] == pytest.approx(500 * limit) and len(ranking['increase']['cards']) == 6
    assert len(ranking['increase']['rest']) == 994
    assert all(b['kinds']['line']['cancelling'] is False for b in got['list'])     # abs is not above the limit either
    assert set(check_values(checks).values()) == {'passed'}
    assert checks['kinds']['line']['each_once']['listed'] == 1000


def test_l1_a_cancelling_bundle_is_marked_and_ordered_last():
    atoms, kstat, erows, pair_rows, crp, totals, vi_a = synth([.05, .02, .02])
    # one bundle with two opposite atoms: net 0.0 exactly in its group, abs above the limit
    for i, d in enumerate((.03, -.03)):
        atoms.append({'id': f'z{i}', 'kind': 'line', 'pair_id': 'PZ', 'cno': False, 'layer': 'SD1',
                      'ends': [('SZ', 'SD1'), ('TZ', 'SD1')], 'a': .01, 'b': .01 + d, 'd': d, 'presence': 'both'})
    erows.update({n: dict(erows['S0']) for n in ('SZ', 'TZ')})
    pair_rows['PZ'] = {'delta': 0.0, 'electrodes': ['SZ', 'TZ']}
    kstat['line'] = ('ok', [], .09 + .06)
    totals['line'] = {'delta': .09}
    got, checks = ledger._bundle_summary(atoms, kstat, erows, pair_rows, 0.0, totals, str, vi_a)
    zero = [b for b in got['list'] if b['key'] == ['p', 'PZ', 'SD1']][0]
    assert zero['kinds']['line']['sign'] == 'zero' and zero['kinds']['line']['cancelling'] is True
    assert zero['kinds']['line']['internal_cancel'] == pytest.approx(.06)
    assert got['ranking']['line']['zero'] == [zero['id']] and zero['kinds']['line']['direction_share'] is None
    assert set(check_values(checks).values()) == {'passed'}


def test_l2_the_opposite_direction_below_the_third_card_is_in_its_rest():
    got, checks = bundle_doc([-.20] + [.01 * (i + 1) for i in range(5)])        # one decrease, five increases
    ranking = got['ranking']['line']
    assert ranking['direction'] == 'decrease'
    assert len(ranking['increase']['cards']) == 3 and len(ranking['increase']['rest']) == 2
    assert ranking['decrease']['cards'] and ranking['decrease']['rest'] == []
    assert ranking['increase']['cover'] + math.fsum(
        b['kinds']['line']['direction_share'] for b in got['list'] if b['id'] in ranking['increase']['rest']
    ) == pytest.approx(1.0)
    assert checks['kinds']['line']['each_once']['status'] == 'passed'


def test_l2_a_kind_with_no_change_keeps_a_complete_empty_ranking():
    atoms, kstat, erows, pair_rows, crp, totals, vi_a = synth([0.0, 0.0])
    kstat['line'] = ('no_change', [], 0.0)
    got, checks = ledger._bundle_summary(atoms, kstat, erows, pair_rows, 0.0, totals, str, vi_a)
    assert got['list'] == []
    ranking = got['ranking']['line']
    assert ranking['status'] == 'no_change' and ranking['increase']['cards'] == [] and ranking['zero'] == []


def test_l2_the_same_input_in_any_order_gives_identical_direction_bytes():
    atoms, kstat, erows, pair_rows, crp, totals, vi_a = synth([-.05, -.04, .06, .01, -.03, .02])
    outs = []
    for seed in range(5):
        shuffled = [dict(a) for a in atoms]
        random.Random(seed).shuffle(shuffled)
        got, _ = ledger._bundle_summary(shuffled, kstat, erows, pair_rows, crp, totals, str, vi_a)
        outs.append(json.dumps(got))
    assert len(set(outs)) == 1


def test_l3_types_hold_each_bundle_once_and_the_sum_with_crp_is_the_kind_delta(tmp_path):
    dl1 = dl1_all(['Data', 'X1', 'X2', 'S', 'A', 'B', 'C'], ('GAT2', 'SD1'))
    del dl1['reference']['Data']
    pairs = ([pair('N1', ['Data', 'X1'], 'line', None, [lateral(['Data', 'X1'], .01, 'GAT2')]),
              pair('N2', ['Data', 'X2'], 'line', None, [lateral(['Data', 'X2'], .02, 'SD1')])] +
             hub_pairs('S', ['A', 'B', 'C'], 'H', .02, .01))
    doc, _ = run(tmp_path, pairs, dl1=dl1, cno=[('Data', 'SD1', None, .004), ('S', 'SD1', .001, .002)], crp=(1.0, 2.92))
    types = doc['summary']['bundles']['types']
    for kind in ('line', 'pixel'):
        total = sum(types[kind][name]['net'] for name in ledger.TYPES) + types[kind]['crp']['net']
        assert total == pytest.approx(doc['totals'][kind]['delta'])
        assert doc['checks']['bundles']['kinds'][kind]['types_plus_crp']['status'] == 'passed'
        for name in ledger.TYPES:
            row = types[kind][name]
            assert row['net'] == pytest.approx(row['increase'] + row['decrease']) and row['gross'] >= abs(row['net'])
    assert types['line']['electrode_new']['count'] == 1 and types['line']['hub']['count'] == 1
    assert types['pixel']['electrode_new']['net'] == pytest.approx(doc['cno'][0]['delta'] if doc['cno'][0]['electrode_id'] == 'Data' else doc['cno'][1]['delta'])
    assert types['pixel']['cno_all']['net'] == pytest.approx(math.fsum(r['delta'] for r in doc['cno']))
    assert types['pixel']['cno']['net'] == pytest.approx(
        [r['delta'] for r in doc['cno'] if r['electrode_id'] == 'S'][0])      # S hub exists only for line atoms
    all_passed(doc)


# ---- rnarr/T11 K3: area basis in the ledger --------------------------------------------------------------------------

OTHER = dict(group='auto', res=[7, 9], glass=40.0)


def test_k3_area_basis_splits_the_design_segment_and_the_top_pairs_use_it(tmp_path):
    doc, c2 = real_ledger(tmp_path, a=dict(unit=[10, 20]), b=dict(OTHER, unit=[30, 10]))
    assert doc['checks']['rebase']['status'] == 'passed', json.dumps(doc['checks']['rebase'], indent=1)
    items = {(row['pair_id'], item['item_id']): item for row in doc['pairs'] for item in row['items']}
    for kind in ('line', 'pixel'):
        design = doc['summary']['design'][kind]
        assert design['basis'] == 'area' and design['status'] == 'available'
        seg = design['segments']
        assert list(seg) == ['design', 'resolution', 'crp', 'density', 'design_area', 'unit_size']
        assert seg['design_area'] + seg['unit_size'] == pytest.approx(seg['design'], abs=1e-12)
        # L6: the report adds the area pair, or the old design segment: never both
        area_total = seg['design_area'] + seg['unit_size'] + seg['resolution'] + seg['crp'] + seg['density']
        assert area_total == pytest.approx(doc['totals'][kind]['delta'], abs=1e-12)
        assert design['a_relative']['design_area'] == pytest.approx(
            100 * seg['design_area'] / design['vi_a'])
        for entry in design['top_increase'] + design['top_decrease']:
            if entry['pair_id'] is None:
                continue
            row = [r for r in doc['pairs'] if r['pair_id'] == entry['pair_id']][0]
            mine = [i for i in row['items'] if i['class'] == kind]
            assert entry['design_delta'] == pytest.approx(math.fsum(i['design_area_delta'] for i in mine), abs=1e-15)
    for kind in ('line', 'pixel'):                      # R17:4
        split = doc['checks']['rebase'][kind]['area_split']
        assert split['status'] == 'passed' and split['sum'] == pytest.approx(split['target'], abs=1e-12)
    some = next(i for i in items.values() if i['design_area_delta'] is not None)
    assert some['ca_delta'] is not None and some['unit_size_delta'] is not None   # the old ca_delta stays
    assert doc['summary']['rebase']['line']['states']['S1a']['x'] > 0


def test_k3_equal_unit_area_gives_the_old_design_values_bit_for_bit(tmp_path):
    doc, _ = real_ledger(tmp_path, a=dict(unit=[10, 20]), b=dict(OTHER, unit=[20, 10]))
    for kind in ('line', 'pixel'):
        seg = doc['summary']['design'][kind]['segments']
        assert doc['summary']['design'][kind]['basis'] == 'area'
        assert seg['design_area'] == seg['design'] and seg['unit_size'] == 0.0


def test_k3_missing_unit_size_falls_back_to_unit_and_adds_no_area_segments(tmp_path):
    doc, _ = real_ledger(tmp_path, b=OTHER)
    assert doc['summary']['rebase']['status'] == 'available'
    for kind in ('line', 'pixel'):
        design = doc['summary']['design'][kind]
        assert design['basis'] == 'unit' and list(design['segments']) == ['design', 'resolution', 'crp', 'density']
        assert 'S1a' not in doc['summary']['rebase'][kind]['states']
    item = doc['pairs'][0]['items'][0]
    assert item['design_area_delta'] is None and item['ca_delta'] is not None


def test_k3_an_old_compare2_without_area_basis_reads_as_unit_and_adds_no_item_keys(tmp_path):
    ref, tgt = two_sides()
    code, c2, out = rebase_go(tmp_path, ref, tgt, b=OTHER)
    assert code == 0
    for key in ('area_basis',):
        c2['rebase'].pop(key)
    path = out / 'compare2.json'
    path.write_text(json.dumps(c2), encoding='utf-8')
    ledger_out = tmp_path / 'ledger_out'
    assert ledger.main_ledger(str(out), str(tmp_path / 'compare2.yaml'), str(ledger_out), lambda record: None) == 0
    doc = json.loads((ledger_out / 'ledger.json').read_text(encoding='utf-8'))
    assert doc['summary']['design']['line']['basis'] == 'unit'
    item = doc['pairs'][0]['items'][0]
    assert 'design_area_delta' not in item and 'unit_size_delta' not in item and item['ca_delta'] is not None


def test_k3_one_kind_that_cannot_be_converted_does_not_stop_the_other(tmp_path):
    doc, _ = real_ledger(tmp_path, a=dict(unit=[10, 20]), b=dict(OTHER, unit=[30, 10]))

    def drop(c2):
        c2['rebase']['states']['line'] = {'status': 'unavailable', 'reasons': ['x']}
        c2['rebase']['check']['line'] = {'status': 'not_evaluated'}
    ref, tgt = two_sides()
    tmp = tmp_path / 'again'
    tmp.mkdir()
    code, c2, out = rebase_go(tmp, ref, tgt, a=dict(unit=[10, 20]), b=dict(OTHER, unit=[30, 10]))
    drop(c2)
    (out / 'compare2.json').write_text(json.dumps(c2), encoding='utf-8')
    ledger_out = tmp / 'ledger_out'
    ledger.main_ledger(str(out), str(tmp / 'compare2.yaml'), str(ledger_out), lambda record: None)
    again = json.loads((ledger_out / 'ledger.json').read_text(encoding='utf-8'))
    assert again['summary']['design']['line']['basis'] == 'unit' and again['summary']['design']['line']['status'] != 'available'
    assert again['summary']['design']['pixel']['basis'] == 'area'
