"""mrep/T1: item breakdown, composition, electrode layer state, by_size and the '불량 구성 변경' label of ledger.json.

The new keys are added only: with them removed, the output equals the one of the baseline code (commit c194d0e)."""
import copy
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from simcompare import ledger
from test_ledger import (ELECTRODES_A, ELECTRODES_B, MOCKUP_CNO, by_pair, go, lateral, make_world, mockup,
                         mockup_pairs, pair, result, vertical)

BASELINE_COMMIT = 'c194d0e8bbd828e5addb2368dcb3b26bf5666460'
FILTERED = ['diameter_filter_removed_all']
REPO = Path(__file__).resolve().parents[1]


def _baseline_module():
    source = subprocess.run(['git', 'show', f'{BASELINE_COMMIT}:src/simcompare/ledger.py'], cwd=str(REPO),
                            stdout=subprocess.PIPE, check=True).stdout.decode('utf-8')
    module = types.ModuleType('simcompare._ledger_baseline')
    module.__package__ = 'simcompare'
    module.__file__ = 'ledger_baseline.py'
    sys.modules[module.__name__] = module
    exec(compile(source, 'ledger_baseline.py', 'exec'), module.__dict__)
    return module


def _strip_new_keys(doc):
    doc = copy.deepcopy(doc)
    for row in doc['pairs']:
        row.pop('breakdown')
        row.pop('composition')
        for item in row['items']:
            item.pop('by_size')
    for kind in ('line', 'pixel'):
        doc['kinds'][kind].pop('item_breakdown')
    doc['checks'].pop('item_breakdown')
    for row in doc['electrodes']:
        for key in ('layer_state', 'layer_state_status', 'moved'):
            row.pop(key)
    return doc


def _label_back(doc):
    for entry in doc['change_kinds']:
        if entry['code'] == 'layer_change':
            assert entry['label'] == '불량 구성 변경'
            entry['label'] = '레이어 변경'
    return doc


def _same_as_baseline(world, tmp_path):
    base = _baseline_module()
    old_out = tmp_path / 'old_out'
    assert base.main_ledger(str(world.c2), str(world.config), str(old_out), lambda record: None) == 0
    assert go(world) == 0
    old = json.loads((old_out / 'ledger.json').read_text(encoding='utf-8'))
    new = _label_back(_strip_new_keys(result(world)))
    assert new == old
    for name in ('ledger_pairs.csv', 'ledger_electrodes.csv'):          # the CSVs only use codes
        assert (world.out / name).read_bytes() == (old_out / name).read_bytes()


def test_without_the_new_keys_the_output_is_the_baseline_mockup(tmp_path):
    _same_as_baseline(mockup(tmp_path), tmp_path)


def test_without_the_new_keys_the_output_is_the_baseline_noncomputable_pair_and_no_dl1(tmp_path):
    pairs = mockup_pairs()
    pairs[0] = pair('P1', ['Data', 'ELVDD'], 'line', [lateral(['Data', 'ELVDD'], .0120, 'SD1')], [], noncomp_b=True)
    world = make_world(tmp_path, pairs, cno=MOCKUP_CNO, crp=(1.0, 2.92))
    _same_as_baseline(world, tmp_path)


# ---- pair breakdown ---------------------------------------------------------------------------------------------------

def test_pair_breakdown_of_a_composition_change_and_the_new_label(tmp_path):
    pairs = [pair('P1', ['A', 'B'], 'line',
                  [lateral(['A', 'B'], .0100, 'SD1'), vertical([('A', 'GAT2'), ('B', 'SD2')], .0100, 'GAT2', 'SD2')],
                  [lateral(['A', 'B'], .0150, 'SD1'), lateral(['A', 'B'], .0100, 'GAT2')]),
             pair('P2', ['B', 'C'], 'line', [lateral(['B', 'C'], .01, 'SD1')], [lateral(['B', 'C'], .02, 'SD1')])]
    world = make_world(tmp_path, pairs)
    assert go(world) == 0
    doc = result(world)
    s = doc['kinds']['line']['slope']
    row = by_pair(doc)['P1']
    assert row['change_kind'] == 'layer_change'
    brk = row['breakdown']
    assert brk['status'] == 'ok' and (brk['n_both'], brk['n_new'], brk['n_gone']) == (1, 1, 1)
    assert brk['same_delta'] == pytest.approx(s * .0050)
    assert brk['new_value'] == pytest.approx(s * .0100) and brk['gone_value'] == pytest.approx(s * .0100)
    assert brk['same_delta'] + brk['new_value'] - brk['gone_value'] == pytest.approx(row['delta'])
    assert [d['text'] for d in row['composition']['added']] == ['측면 GAT2']
    assert [d['text'] for d in row['composition']['removed']] == ['상하 GAT2-SD2']
    assert row['composition']['removed'][0]['layers'] == ['GAT2', 'SD2']
    assert by_pair(doc)['P2']['composition'] == {'added': [], 'removed': []}
    assert [e['label'] for e in doc['change_kinds'] if e['code'] == 'layer_change'] == ['불량 구성 변경']
    assert doc['kinds']['line']['item_breakdown']['new'] == pytest.approx(s * .0100)
    assert doc['checks']['item_breakdown']['pair'] == {'status': 'passed', 'checked': 2, 'failed': 0,
                                                       'not_evaluated': 0}
    assert doc['checks']['item_breakdown']['kind']['line']['status'] == 'passed'


def test_filtered_item_with_the_same_id_on_the_other_side_is_both(tmp_path):
    pairs = [pair('P1', ['A', 'B'], 'line', [lateral(['A', 'B'], 0.0, 'SD1', sizes=[], reasons=FILTERED)],
                  [lateral(['A', 'B'], .02, 'SD1')])]
    world = make_world(tmp_path, pairs)
    assert go(world) == 0
    row = by_pair(result(world))['P1']
    assert row['change_kind'] == 'same_pair_change'
    assert (row['breakdown']['n_both'], row['breakdown']['n_new'], row['breakdown']['n_gone']) == (1, 0, 0)
    assert row['breakdown']['same_delta'] == pytest.approx(row['delta'])
    assert row['composition'] == {'added': [], 'removed': []}


def test_noncomputable_pair_is_null_while_the_kind_breakdown_and_checks_stay_numbers(tmp_path):
    pairs = mockup_pairs()
    pairs[0] = pair('P1', ['Data', 'ELVDD'], 'line', [lateral(['Data', 'ELVDD'], .0120, 'SD1')], [], noncomp_b=True)
    world = make_world(tmp_path, pairs, dl1={'reference': ELECTRODES_A, 'target': ELECTRODES_B}, cno=MOCKUP_CNO)
    assert go(world) == 0
    doc = result(world)
    assert by_pair(doc)['P1']['delta'] is None
    assert by_pair(doc)['P1']['breakdown'] == {
        'same_delta': None, 'new_value': None, 'gone_value': None, 'n_both': None, 'n_new': None, 'n_gone': None,
        'status': 'not_evaluated'}
    line = doc['kinds']['line']['item_breakdown']
    assert all(isinstance(line[k], float) for k in ('same', 'new', 'gone', 'cno', 'crp'))
    # the kind sum still holds on the internal item values (E1), the null pair is only counted as not evaluated
    assert doc['checks']['item_breakdown']['kind']['line']['status'] == 'passed'
    assert doc['checks']['item_breakdown']['kind']['pixel']['status'] == 'passed'
    assert doc['checks']['item_breakdown']['pair']['not_evaluated'] == 1
    assert doc['checks']['item_breakdown']['pair']['status'] == 'not_evaluated'
    assert doc['checks']['item_breakdown']['pair']['failed'] == 0
    s = doc['kinds']['line']['slope']
    assert line['same'] + line['new'] - line['gone'] == pytest.approx(doc['totals']['line']['items']['delta'])
    assert s * .0120 == pytest.approx(by_pair(doc)['P1']['a'])


def test_waiting_pair_without_items_is_not_evaluated_not_zero(tmp_path):
    pairs = [pair('P1', ['A', 'B'], 'line', [], [], noncomp_a=True),
             pair('P2', ['B', 'C'], 'line', [lateral(['B', 'C'], .01, 'SD1')], [lateral(['B', 'C'], .02, 'SD1')])]
    world = make_world(tmp_path, pairs)
    assert go(world) == 0
    doc = result(world)
    assert by_pair(doc)['P1']['breakdown']['status'] == 'not_evaluated'
    assert by_pair(doc)['P1']['breakdown']['same_delta'] is None
    assert by_pair(doc)['P2']['breakdown']['status'] == 'ok'


def test_empty_kind_is_zero_and_passes(tmp_path):
    pairs = [pair('P1', ['A', 'B'], 'line', [lateral(['A', 'B'], .01, 'SD1')], [lateral(['A', 'B'], .02, 'SD1')])]
    world = make_world(tmp_path, pairs)
    assert go(world) == 0
    doc = result(world)
    assert doc['kinds']['pixel']['pair_count'] == 0
    assert doc['kinds']['pixel']['item_breakdown'] == {'same': 0.0, 'new': 0.0, 'gone': 0.0, 'cno': 0.0, 'crp': 0.0}
    assert doc['checks']['item_breakdown']['kind']['pixel']['status'] == 'passed'


def test_crp_difference_is_part_of_the_kind_check(tmp_path):
    world = mockup(tmp_path, crp=(1.0, 2.92))
    assert go(world) == 0
    doc = result(world)
    pixel = doc['kinds']['pixel']['item_breakdown']
    assert pixel['crp'] == pytest.approx(doc['totals']['pixel']['crp']['delta']) and pixel['crp'] != 0.0
    assert pixel['cno'] == pytest.approx(doc['totals']['pixel']['cno']['delta'])
    checks = doc['checks']['item_breakdown']['kind']['pixel']
    assert checks['status'] == 'passed' and checks['items']['status'] == 'passed'
    assert checks['total']['status'] == 'passed' and checks['total']['target'] == pytest.approx(
        doc['totals']['pixel']['delta'])
    assert doc['checks']['item_breakdown']['pair']['status'] == 'passed'


def test_pair_absent_on_one_side_splits_into_new_only(tmp_path):
    world = mockup(tmp_path)
    assert go(world) == 0
    row = by_pair(result(world))['P5']
    assert row['change_kind'] == 'new_pair'
    assert (row['breakdown']['n_both'], row['breakdown']['n_new'], row['breakdown']['n_gone']) == (0, 1, 0)
    assert row['breakdown']['new_value'] == pytest.approx(row['delta'])


def test_by_size_reuses_the_item_size_rows(tmp_path):
    pairs = [pair('P1', ['A', 'B'], 'line', [lateral(['A', 'B'], .03, 'SD1', sizes=[.01, .02])],
                  [lateral(['A', 'B'], .05, 'SD1', sizes=[.02, .03])])]
    world = make_world(tmp_path, pairs)
    assert go(world) == 0
    doc = result(world)
    s = doc['kinds']['line']['slope']
    sizes = by_pair(doc)['P1']['items'][0]['by_size']
    assert [e['diameter_um'] for e in sizes] == [3.0, 4.0]
    assert sizes[0]['density_layer'] == 'SD1'
    assert (sizes[0]['lambda_a'], sizes[0]['lambda_b']) == (.01, .02)
    assert (sizes[0]['area_a'], sizes[0]['area_b']) == (1.0, 1.0)
    assert sizes[1]['value_a'] == pytest.approx(s * .02) and sizes[1]['value_b'] == pytest.approx(s * .03)


# ---- electrode layer state ----------------------------------------------------------------------------------------------

def test_layer_state_and_moved_from_dl1(tmp_path):
    world = mockup(tmp_path)
    assert go(world) == 0
    elec = {row['electrode']: row for row in result(world)['electrodes']}
    assert elec['Data']['layer_state'] == {'SD1': 'both'} and elec['Data']['moved'] is False
    assert elec['Gnode']['layer_state'] == {'SD1': 'a_only', 'SD2': 'b_only'} and elec['Gnode']['moved'] is True
    assert elec['Scan1']['layer_state'] == {'GAT1': 'a_only', 'GAT2': 'b_only'} and elec['Scan1']['moved'] is True
    assert elec['Scan1']['layer_state_status'] == 'verified'
    assert elec['Vinit2']['layer_state'] == {'GAT1': 'a_only'} and elec['Vinit2']['moved'] is False   # removed
    assert elec['Scan3']['layer_state'] == {'GAT2': 'b_only'} and elec['Scan3']['moved'] is False     # added


def test_only_a_layers_is_a_only_not_moved(tmp_path):
    pairs = [pair('P1', ['A', 'B'], 'line', [lateral(['A', 'B'], .01, 'SD1')], [lateral(['A', 'B'], .02, 'SD1')])]
    dl1 = {'reference': {'A': ['SD1', 'GAT1'], 'B': ['SD1']}, 'target': {'A': ['SD1'], 'B': ['SD1']}}
    world = make_world(tmp_path, pairs, dl1=dl1)
    assert go(world) == 0
    elec = {row['electrode']: row for row in result(world)['electrodes']}
    assert elec['A']['layer_state'] == {'GAT1': 'a_only', 'SD1': 'both'} and elec['A']['moved'] is False


def test_without_dl1_the_layer_state_is_unverified(tmp_path):
    world = make_world(tmp_path, mockup_pairs(), cno=MOCKUP_CNO)
    assert go(world) == 0
    elec = result(world)['electrodes']
    assert elec and all(row['layer_state_status'] == 'unverified' for row in elec)
    assert all(row['moved'] is None for row in elec)
