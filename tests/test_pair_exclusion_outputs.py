"""pexcl/T2: pair_exclusions block in compare2.json / 04 / compare provenance, stale rule check, numeric_v2, version."""
import json
import re
import hashlib
from types import SimpleNamespace

import pytest

import test_compare2 as tc
import test_compare2_expected_zero as ez
import test_compare2_report as tcr
import test_ledger as tl
from simcompare import compare2, pipeline
from simcompare.adapters import numeric
from simcompare.adapters.real_hotspot import configured_dataset, load_real_manifest
from simcompare.cli import main
from simcompare.numeric_schema import NumericContractError
from simcompare.pair_exclusion import PAIR_EXCLUSION_RULE, record_block

GH_CASE = ('lateral_short', 'GH', 'L1', 'X', 'L1', {1: {1, 2}, 2: {1}, 3: {1}})


def sides_with_gh(ref=True, tgt=False):
    ref_cases = ez.pick('AB', 'DE', 'GH') + ([GH_CASE] if ref else [])
    tgt_cases = ez.pick('AB', 'DE', 'GH') + ([GH_CASE] if tgt else [])
    return (ref_cases, tc.PAIRS), (tgt_cases, tc.PAIRS)


# ---- compare2.json block (T2-1, C2) ---------------------------------------------------------------------------------

def test_gh_case_is_computed_around_and_recorded_per_role(tmp_path, monkeypatch):
    ref, tgt = sides_with_gh(ref=True, tgt=True)
    code, out, doc = ez.run_pair_in(tmp_path, monkeypatch, 'with', ref, tgt)
    assert code == 0
    code0, _, plain = ez.run_pair_in(tmp_path, monkeypatch, 'plain', sides_with_gh(False, False)[0],
                                     sides_with_gh(False, False)[1])
    assert code0 == 0
    for role in compare2.ROLES:
        manifest = doc['inputs']['manifests'][role]
        block = manifest['pair_exclusions']
        assert block['rule'] == PAIR_EXCLUSION_RULE and block['by_reason'] == {'builtin:GH': 1}
        assert block['excluded_cases'] == 1 and block['excluded_expected_cases'] == 0
        assert 'pair_exclusions' not in plain['inputs']['manifests'][role]
        assert doc['sides'][role]['status'] == 'available' and doc['sides'][role]['lambda'] == plain['sides'][role]['lambda']
        assert manifest['input_hash'] != plain['inputs']['manifests'][role]['input_hash']


def test_one_sided_gh_case_is_discovered_here_expected_only_there(tmp_path, monkeypatch):
    ref, tgt = sides_with_gh(ref=True, tgt=False)
    code, _, doc = ez.run_pair_in(tmp_path, monkeypatch, 'a', ref, tgt, inventory='union')
    assert code == 0
    blocks = {role: doc['inputs']['manifests'][role]['pair_exclusions'] for role in compare2.ROLES}
    assert (blocks['reference']['excluded_cases'], blocks['reference']['excluded_expected_cases']) == (1, 0)
    assert (blocks['target']['excluded_cases'], blocks['target']['excluded_expected_cases']) == (0, 1)
    assert blocks['reference']['cases'][0]['presence'] == 'discovered'
    assert blocks['target']['cases'][0]['presence'] == 'expected_only'
    plain = ez.run_pair_in(tmp_path, monkeypatch, 'p', *sides_with_gh(False, False))[2]
    for role in compare2.ROLES:      # both input hashes carry the rule
        assert doc['inputs']['manifests'][role]['input_hash'] != plain['inputs']['manifests'][role]['input_hash']
    # (b) the other side has neither the folder nor the expected name: no block there
    code, _, doc_b = ez.run_pair_in(tmp_path, monkeypatch, 'b', ref, tgt, inventory='own')
    assert code == 0
    assert 'pair_exclusions' in doc_b['inputs']['manifests']['reference']
    assert 'pair_exclusions' not in doc_b['inputs']['manifests']['target']
    assert doc_b['inputs']['manifests']['target']['input_hash'] == plain['inputs']['manifests']['target']['input_hash']


# ---- stale rule (T2-2) -----------------------------------------------------------------------------------------------

def test_stale_rule_unit():
    good = {'inputs': {'area_basis': compare2.AREA_BASIS,
                       'case_exclusions': {r: {'rule': compare2.CASE_EXCLUSION_RULE} for r in compare2.ROLES},
                       'manifests': {'reference': {'pair_exclusions': {'rule': PAIR_EXCLUSION_RULE}}, 'target': {}}}}
    assert compare2.stale_reason(good) is None
    good['inputs']['manifests']['target']['pair_exclusions'] = {'rule': 'older'}
    code, message = compare2.stale_reason(good)
    assert code == 'compare2_stale_pair_exclusion_rule' and '다시 실행' in message and PAIR_EXCLUSION_RULE in message
    good['inputs']['manifests']['target']['pair_exclusions'] = {}       # a block without a rule is stale as well
    assert compare2.stale_reason(good)[0] == 'compare2_stale_pair_exclusion_rule'


def test_07_refuses_a_stale_pair_rule(tmp_path, monkeypatch):
    ref, tgt = sides_with_gh(True, True)
    code, out, doc = ez.run_pair_in(tmp_path, monkeypatch, 'run', ref, tgt)
    assert code == 0
    folder = tmp_path / 'run'
    path = out / 'compare2.json'
    doc['inputs']['manifests']['reference']['pair_exclusions']['rule'] = 'older_rule'
    path.write_text(json.dumps(doc), encoding='utf-8')
    rep = folder / 'rep'
    assert main(['compare2-report', '--compare2-dir', str(out), '--config', str(folder / 'compare2.yaml'),
                 '--out', str(rep)]) == 2
    assert json.loads((rep / 'error.json').read_text(encoding='utf-8'))['code'] == 'compare2_stale_pair_exclusion_rule'


def test_ledger_refuses_a_stale_pair_rule(tmp_path):
    def tamper(doc):
        doc['inputs'].setdefault('manifests', {})['target'] = {'pair_exclusions': {'rule': 'older_rule'}}
    world = tl.mockup(tmp_path, mutate=tamper)
    assert tl.go(world) == 2
    assert tl.error_code(world.out) == 'compare2_stale_pair_exclusion_rule'


# ---- all cases left out (T2-3) ---------------------------------------------------------------------------------------

def test_side_without_cases_names_the_rule_and_count(tmp_path):
    left_out = [GH_CASE, ('lateral_short', 'N_NODE', 'L1', 'Y', 'L1', {1: {2}})]
    code, out = tc.run(tmp_path, (left_out, tc.PAIRS[:1]), (tc.base_cases(), tc.PAIRS))
    assert code == 2
    error = json.loads((out / 'error.json').read_text(encoding='utf-8'))
    assert error['code'] == 'compare2_side_without_cases'
    assert PAIR_EXCLUSION_RULE in error['detail'] and '2 cases left out' in error['detail']


# ---- numeric_v2 stays strict (T2-4) --------------------------------------------------------------------------------------

def test_numeric_v2_class_excluded_is_still_invalid(tmp_path):
    (tmp_path / 'pairs.csv').write_text('pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\n'
                                        'X1,S,D,excluded,v1\n', encoding='utf-8')
    with pytest.raises(NumericContractError) as caught:
        numeric._pairs(tmp_path, {'pair_table_version': 'v1'})
    assert caught.value.code == 'invalid_pair'


# ---- compare provenance and no-exclusion bytes (T2-1, T2-5) ---------------------------------------------------------------

def test_compare_provenance_helper_writes_a_key_only_for_blocks():
    block = {'rule': PAIR_EXCLUSION_RULE, 'excluded_cases': 1}
    with_block = SimpleNamespace(provenance={'pair_exclusions': block})
    without = SimpleNamespace(provenance={})
    assert pipeline._pair_exclusion_provenance(without, without) == {}
    got = pipeline._pair_exclusion_provenance(with_block, without)
    assert got == {'pair_exclusions': {'reference': block}} and got['pair_exclusions']['reference'] is not block


def test_no_exclusion_outputs_keep_the_baseline_bytes(tmp_path):
    ref, tgt = ez.two_one_sided()
    code, out = tc.run(tmp_path, (tc.base_cases(), tc.PAIRS), (tc.base_cases(), tc.PAIRS))
    assert code == 0
    text = (out / 'compare2.json').read_text(encoding='utf-8')
    assert 'pair_exclusions' not in text
    for name in ('compare2_summary.csv', 'compare2_layers.csv', 'compare2_conditions.csv', 'compare2_cno.csv'):
        assert 'pair_exclusions' not in (out / name).read_text(encoding='utf-8')


# ---- pair_table_version (T2-6) -----------------------------------------------------------------------------------------------

def test_pair_table_version_with_excluded_rows_only(tmp_path):
    _root, imported = tc.write_side(tmp_path, 's', [GH_CASE, tc.base_cases()[0]],
                                    [('XSD', 'S', 'D', 'excluded')])
    dataset = configured_dataset(imported, load_real_manifest(imported), tc.assignment())
    assert dataset.provenance['pair_table_version'] == 'v1'
    _root, mixed = tc.write_side(tmp_path, 'm', [tc.base_cases()[0]], [('XSD', 'S', 'D', 'excluded'), tc.PAIRS[0]])
    assert configured_dataset(mixed, load_real_manifest(mixed), tc.assignment()).provenance['pair_table_version'] == 'v1'
