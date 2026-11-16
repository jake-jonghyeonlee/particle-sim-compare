"""pexcl/T4: candidates-dv skips recorded excluded cases, 03 layer check after the pair rule, 04 pair_exclusions block."""
import json
from pathlib import Path

import pytest

import test_change_candidates as tcc
import test_dv_candidates as tdc
import test_template_export as tte
from simcompare.cli import main
from simcompare.pair_exclusion import PAIR_EXCLUSION_RULE

GH_NAME = 'lateral_short-GH-L1-N1-L1'
SD_NAME = 'lateral_short-S1-L1-D1-L1'


@pytest.fixture(autouse=True)
def _electrode_types(monkeypatch):
    for letter in 'GSD':
        monkeypatch.setitem(tdc.TYPES, letter, ('low', ('0', '0')))


def _extra_cases():
    island = tdc.island(10)
    return [(GH_NAME, island), (SD_NAME, island)]


def _edit_pairs(monkeypatch, edit):
    """World builds pairs.csv inside build_product, before the asset import hashes it: edit the rows in the same place.
    edit gets the list of csv lines (header first) and returns the lines to write."""
    original_write = Path.write_text

    def write_text(self, data, *args, **kwargs):
        if self.name == 'pairs.csv' and str(data).startswith('pair_id,electrode_a_id'):
            data = '\n'.join(edit(str(data).splitlines())) + '\n'
        return original_write(self, data, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', write_text)


def _case_names(path, product='reference'):
    vulnerability = json.loads((Path(path) / 'vulnerability_v1.json').read_text(encoding='utf-8'))
    return {row['case_name'] for row in vulnerability['products'][product]['cases']}, vulnerability


# ---- astra:C1 (dv-grade rows of left-out cases) ----------------------------------------------------------------------

def test_dv_candidates_skips_builtin_and_table_excluded_cases(tmp_path, monkeypatch):
    cases = tdc.standard_cases() + _extra_cases()
    # GH-N1 is not registered (built-in rule); S1-D1 is registered as class=excluded
    _edit_pairs(monkeypatch, lambda lines: [l.replace(',line,', ',excluded,') if l.startswith('D1S1,') or l.startswith('S1D1,') else l
                                            for l in lines if not l.startswith(('GHN1,', 'GH'))])
    world = tdc.World(tmp_path, cases, cases)
    assert world.run('skip') == 0
    names, vulnerability = _case_names(tmp_path / 'skip')
    assert GH_NAME not in names and SD_NAME not in names
    assert {c for c, _ in tdc.standard_cases()} <= names
    product = vulnerability['products']['reference']
    assert not any(p['pair_key'] in ('GH-N1', 'D1-S1', 'S1-D1') for p in product['pairs'])
    payload = json.loads((tmp_path / 'skip' / 'candidate_queries_v1.json').read_text(encoding='utf-8'))
    note = next(n for n in payload['notes'] if n['code'] == 'dv_grade_pair_excluded_cases_skipped' and n['product'] == 'reference')
    assert note['count'] == 2 and note['cases'] == sorted([GH_NAME, SD_NAME])


def test_dv_candidates_keeps_a_registered_gh_pair(tmp_path):
    cases = tdc.standard_cases() + [(GH_NAME, tdc.island(10))]
    world = tdc.World(tmp_path, cases, cases)   # build_product registers GH-N1 as line
    assert world.run('kept') == 0
    names, _ = _case_names(tmp_path / 'kept')
    assert GH_NAME in names
    payload = json.loads((tmp_path / 'kept' / 'candidate_queries_v1.json').read_text(encoding='utf-8'))
    assert not any(n['code'] == 'dv_grade_pair_excluded_cases_skipped' for n in payload['notes'])


def test_dv_candidates_still_refuses_an_unknown_case_when_exclusions_exist(tmp_path, capsys, monkeypatch):
    cases = tdc.standard_cases() + [(GH_NAME, tdc.island(10))]
    _edit_pairs(monkeypatch, lambda lines: [l for l in lines if not l.startswith('GH')])
    world = tdc.World(tmp_path, cases, cases)
    names = [c for c, _ in cases] + ['lateral_short-P9-L1-N9-L1']
    variant = world.dv_variant('extra', 'reference', names)
    tdc._exit2(world, capsys, 'dv_grade_product_mismatch', dv={'reference': variant, 'target': world.dv['target']})


# ---- R1:1 (03 layer check follows the pair rule) --------------------------------------------------------------------

def test_template_layer_outside_stack_of_a_left_out_case_passes(tmp_path):
    odd = 'lateral_short-GH-L9-X-L9'
    cases = tte.CASES + [odd]
    root, dl1 = tte.make_tree(tmp_path, cases)
    config = tte.write_config(tmp_path, root, dl1, cases=tte.CASES)
    out = tmp_path / 'out'
    assert tte.run(config, out) == 0


def test_template_layer_outside_stack_of_a_registered_case_still_fails(tmp_path, capsys):
    odd = 'lateral_short-A-L9-B-L9'
    cases = tte.CASES + [odd]
    root, dl1 = tte.make_tree(tmp_path, cases)
    config = tte.write_config(tmp_path, root, dl1, cases=tte.CASES)
    out = tmp_path / 'out'
    assert tte.run(config, out) == 2
    assert 'template_layer_missing_from_stack' in capsys.readouterr().err
    assert not out.exists()


# ---- R1:5 (stale check with a role entry that is not a dict) -----------------------------------------------------------

def test_stale_reason_non_dict_role_entry_is_an_error_code_not_a_traceback():
    from simcompare import compare2
    doc = {'inputs': {'area_basis': compare2.AREA_BASIS,
                      'case_exclusions': {r: {'rule': compare2.CASE_EXCLUSION_RULE} for r in compare2.ROLES},
                      'manifests': {'reference': ['not', 'a', 'dict'], 'target': {}}}}
    assert compare2.stale_reason(doc)[0] == 'compare2_stale_pair_exclusion_rule'
    doc['inputs']['manifests']['reference'] = {'pair_exclusions': 'text'}
    assert compare2.stale_reason(doc)[0] == 'compare2_stale_pair_exclusion_rule'
    doc['inputs']['manifests'] = {'reference': {}, 'target': {}}
    assert compare2.stale_reason(doc) is None


# ---- R1:2 (04 candidates-change records the block) -----------------------------------------------------------------------

def _run_change(base, monkeypatch, exclude_ac):
    base.mkdir()
    if exclude_ac:   # the A-C pair is registered as class=excluded: the case lateral_short-A-L1-C-L1 is left out
        _edit_pairs(monkeypatch, lambda lines: [l.replace(',line,', ',excluded,') if l.startswith('AC,') else l for l in lines])
    datas, assignments = {}, {}
    for side in ('reference', 'target'):
        datas[side], assignments[side] = tcc.build_product(base, side)
    config = tcc.write_config(base, datas, assignments, map_spec={'diameter_um': 5})
    out = base / 'out'
    assert main(tcc.cli_args(config, out, base / 'dl1')) in (0, 3)
    return tcc.load(out)


def test_candidates_change_records_the_block_only_when_a_case_is_left_out(tmp_path, monkeypatch):
    plain = _run_change(tmp_path / 'plain', monkeypatch, False)
    for side in ('reference', 'target'):
        assert 'pair_exclusions' not in plain['inputs'][side]
    with_block = _run_change(tmp_path / 'excl', monkeypatch, True)
    for side in ('reference', 'target'):
        block = with_block['inputs'][side]['pair_exclusions']
        assert block['rule'] == PAIR_EXCLUSION_RULE and block['excluded_cases'] == 1
        assert block['by_reason'] == {'pairs_csv:excluded': 1}
