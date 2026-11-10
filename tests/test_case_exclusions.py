"""legacy-calc-align/T2: vertical_short cases with SD2, SD3 or PXL as an endpoint layer are excluded in every stage."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_compare2 as tc
import test_compare2_expected_zero as ez
import test_compare2_report as tcr
import test_correspondence as tcor
import test_dv_grade as tdg
import test_dv_template as tdt
import test_template_export as tte
from simcompare import compare_masks, dv_grade
from simcompare.adapters.real_hotspot import (
    CASE_EXCLUSION_RULE, EXCLUDED_VERTICAL_LAYERS, is_excluded_case, is_excluded_case_name, load_real_manifest)
from simcompare.cli import main
from simcompare.compare2_report import build_report
from simcompare.correspondence import bind_pairs, to_dl1_relations, validate_correspondence
from simcompare.numeric_schema import NumericContractError

M = {1: {0}, 2: {0, 1}, 3: {0}}
CASES = [('lateral_short', 'A', 'L1', 'B', 'L1', M),
         ('vertical_short', 'A', 'SD2', 'C', 'L1', M),     # excluded
         ('vertical_short', 'D', 'L1', 'E', 'PXL', M),     # excluded
         ('vertical_short', 'F', 'SD3', 'G', 'SD2', M),    # excluded
         ('vertical_short', 'H', 'SD2', 'I', 'SD2', M),    # excluded (same layer)
         ('vertical_short', 'J', 'L2', 'K', 'L1', M),
         ('lateral_short', 'L', 'SD2', 'M', 'SD2', M),
         ('vertical_short', 'N', 'sd2', 'O', 'L1', M)]
KEPT = ['lateral_short-A-L1-B-L1', 'vertical_short-J-L2-K-L1', 'lateral_short-L-SD2-M-SD2', 'vertical_short-N-sd2-O-L1']


def name_of(case):
    return '{}-{}-{}-{}-{}'.format(*case[:5])


def test_rule_helpers():
    assert EXCLUDED_VERTICAL_LAYERS == {'SD2', 'SD3', 'PXL'}
    assert is_excluded_case('vertical_short', 'L1', 'PXL') and is_excluded_case('vertical_short', 'SD2', 'SD2')
    assert not is_excluded_case('lateral_short', 'SD2', 'SD2') and not is_excluded_case('vertical_short', 'sd2', 'L1')
    assert is_excluded_case_name('vertical_short-A-SD2-B-L1') and is_excluded_case_name('verticalshort-A-L1-B-SD3')
    assert not is_excluded_case_name('lateralopen-CNO-L1')          # does not parse: kept, never an error
    assert not is_excluded_case_name('not a case')


def test_manifest_drops_excluded_cases_and_counts_them(tmp_path):
    _root, imported = tc.write_side(tmp_path, 'm', CASES, tc.PAIRS)
    manifest = load_real_manifest(imported)           # source_payload_hash and inventory checks pass on the full import
    assert sorted(c['case_name'] for c in manifest['cases']) == sorted(KEPT)
    assert manifest['case_exclusions'] == {'rule': CASE_EXCLUSION_RULE, 'layers': ['PXL', 'SD2', 'SD3'],
                                           'excluded_cases': 4, 'excluded_expected_cases': 4}
    assert sorted(manifest['expected_cases']) == sorted(KEPT)
    assert manifest['expected_case_coverage_status'] == 'confirmed'
    stored = json.loads((imported / 'manifest.json').read_text(encoding='utf-8'))
    assert len(stored['cases']) == len(CASES) and 'case_exclusions' not in stored   # the 01 import file is untouched


def test_expected_only_vertical_sd2_is_not_synthesized_nor_reported(tmp_path, monkeypatch):
    ghost = 'vertical_short-Z-SD2-Y-L1'
    ref, tgt = (tc.base_cases(), tc.PAIRS), (tc.base_cases(), tc.PAIRS)
    code, out, doc = ez.run_pair_in(tmp_path, monkeypatch, 'ghost', ref, tgt, extra_expected=[ghost])
    assert code == 0
    imported = tmp_path / 'ghost' / 'ref' / 'imp'
    raw = json.loads((imported / 'manifest.json').read_text(encoding='utf-8'))
    assert ghost in raw['expected_cases'] and {'case': ghost, 'status': 'expected_case_folder_missing'} in raw['diagnostics']
    manifest = load_real_manifest(imported)
    assert ghost not in manifest['expected_cases'] and manifest['expected_case_coverage_status'] == 'confirmed'
    assert manifest['case_exclusions']['excluded_expected_cases'] == 1
    assert not [d for d in manifest['diagnostics'] if d.get('case') == ghost]
    for role in ('reference', 'target'):
        assert doc['sides'][role]['reasons'] == [] and 'absent_expected_cases' not in doc
        assert not ez.notes_of(doc['sides'][role]) & {ez.NOTE}
    _code, _out, plain = ez.run_pair_in(tmp_path, monkeypatch, 'plain', ref, tgt)
    ez.assert_same_numbers(doc, plain)
    assert ghost not in json.dumps(doc)


def test_side_with_only_excluded_cases_names_the_rule(tmp_path):
    only_excluded = [CASES[1], CASES[2]]
    code, out = tc.run(tmp_path, (only_excluded, tc.PAIRS), (tc.base_cases(), tc.PAIRS))
    assert code == 2
    error = json.loads((out / 'error.json').read_text(encoding='utf-8'))
    assert error['code'] == 'compare2_side_without_cases' and CASE_EXCLUSION_RULE in json.dumps(error)


def test_compare2_json_records_exclusions_and_report_shows_the_rule_once(tmp_path):
    cases = tc.base_cases() + [CASES[1], CASES[3]]
    code, out = tc.run(tmp_path, (cases, tc.PAIRS), (tc.base_cases(), tc.PAIRS))
    assert code == 0
    doc = tc.load(out)
    assert doc['inputs']['case_exclusions']['reference'] == {
        'rule': CASE_EXCLUSION_RULE, 'layers': ['PXL', 'SD2', 'SD3'], 'excluded_cases': 2, 'excluded_expected_cases': 2}
    assert doc['inputs']['case_exclusions']['target']['excluded_cases'] == 0
    exit_code, folder = tcr.report(tmp_path, out)
    html = tcr.text(folder)
    assert html.count(CASE_EXCLUSION_RULE) == 1 and 'SD2·SD3·PXL 이 낀 수직 단락' in html
    assert name_of(CASES[1]) not in html and name_of(CASES[3]) not in html


@pytest.mark.parametrize('mutate', [lambda inputs: inputs.pop('case_exclusions'),
                                    lambda inputs: inputs['case_exclusions']['target'].update(rule='old_rule')])
def test_report_refuses_a_compare2_json_without_the_current_rule(tmp_path, mutate):
    code, out = tcr.build(tmp_path)
    assert code == 0
    path = out / 'compare2.json'
    doc = json.loads(path.read_text(encoding='utf-8'))
    mutate(doc['inputs'])
    path.write_text(json.dumps(doc), encoding='utf-8')
    with pytest.raises(NumericContractError) as info:
        build_report(out, tmp_path / 'compare2.yaml', tmp_path / 'stale')
    assert info.value.code == 'compare2_stale_case_exclusion_rule' and '다시 실행' in str(info.value)


def test_input_hash_includes_the_rule(tmp_path):
    code, out = tc.run(tmp_path, (tc.base_cases(), tc.PAIRS), (tc.base_cases(), tc.PAIRS))
    assert code == 0
    got = tc.load(out)['inputs']['manifests']['reference']
    assignment = hashlib.sha256(json.dumps(tc.assignment(), sort_keys=True, separators=(',', ':'),
                                           ensure_ascii=False).encode('utf-8')).hexdigest()
    pairs = hashlib.sha256((tmp_path / 'ref' / 'imp' / 'pairs.csv').read_bytes()).hexdigest()
    without = hashlib.sha256((got['source_payload_hash'] + assignment + pairs).encode('ascii')).hexdigest()
    with_rule = hashlib.sha256((got['source_payload_hash'] + assignment + pairs + CASE_EXCLUSION_RULE).encode('ascii')).hexdigest()
    assert got['input_hash'] == with_rule != without


def test_compare_masks_records_the_rule_and_refuses_files_without_it():
    writer = compare_masks.MasksWriter(__import__('io').BytesIO())
    writer.add_side('reference', SimpleNamespace(groups={}, input_hash='h', is_synthetic=False))
    assert writer._sides['reference']['case_exclusion_rule'] == CASE_EXCLUSION_RULE
    dataset = SimpleNamespace(cases=[object()], provenance={'input_hash': 'h', 'is_synthetic': False}, frames={})
    side = dict(writer._sides['reference'])
    compare_masks._side_result(None, 'reference', side, dataset, 1 << 20)       # the current rule passes
    for stale in ({k: v for k, v in side.items() if k != 'case_exclusion_rule'}, {**side, 'case_exclusion_rule': 'old'}):
        with pytest.raises(ValueError, match='case_exclusion_rule_mismatch'):
            compare_masks._side_result(None, 'reference', stale, dataset, 1 << 20)


def test_correspondence_drops_an_excluded_pair(tmp_path):
    from simcompare.adapters.numeric import load_dataset
    from simcompare.synthetic import generate_numeric_demo
    generate_numeric_demo(tmp_path / 'demo')
    reference, target = load_dataset(tmp_path / 'demo' / 'reference'), load_dataset(tmp_path / 'demo' / 'target')

    def eps(layer):
        return [{'electrode_id': 'A', 'role': 'gate', 'actual_layer': layer},
                {'electrode_id': 'B', 'role': 'data', 'actual_layer': 'M2'}]
    good = tcor.payload()['pairs'][0]
    bad = json.loads(json.dumps(good))
    bad['pair_id'] = 'PX'
    bad['a'].update(failure_type='vertical_short', endpoints=eps('SD2'))
    bad['b'].update(failure_type='vertical_short', endpoints=eps('M1'))
    ok_pair = json.loads(json.dumps(good))
    ok_pair['a'].update(failure_type='vertical_short', endpoints=eps('M1'))
    ok_pair['b'].update(failure_type='vertical_short', endpoints=eps('M1'))
    ok_pair['pair_id'] = 'PY'
    payload = tcor.payload([bad, ok_pair])
    assert [p['pair_id'] for p in validate_correspondence(payload)['pairs']] == ['PY']
    assert validate_correspondence(payload)['warnings'] == [{'warning': 'case_excluded_pairs_dropped', 'count': 1}]
    bound = {p.pair_id: p for p in bind_pairs(payload, reference, target)}
    assert list(bound) == ['PY'] and bound['PY'].status == 'ready'
    _rows, report = to_dl1_relations(payload)           # converters validate first: PX is neither converted nor skipped
    assert 'PX' not in json.dumps(report)


def test_compare_windows_has_no_row_for_an_excluded_pair(tmp_path):
    import test_cno_round6 as r6
    world = r6.make_world(tmp_path, layer='L1')
    corr = r6.candidates(world, tmp_path / 'cand')
    payload = json.loads(corr.read_text(encoding='utf-8'))
    ready = [p for p in payload['pairs'] if p['status'] == 'ready' and p['a'].get('endpoints') and len(p['a']['endpoints']) == 2]
    assert ready
    bad = json.loads(json.dumps(ready[0]))
    bad['pair_id'] = 'EXCLUDED_PAIR_ID'
    bad['a'].update(failure_type='vertical_short', endpoints=[{'electrode_id': 'X', 'role': None, 'actual_layer': 'SD2'},
                                                              {'electrode_id': 'Y', 'role': None, 'actual_layer': 'L1'}])
    payload['pairs'].append(bad)
    mixed = tmp_path / 'mixed.json'
    mixed.write_text(json.dumps(payload), encoding='utf-8')
    plain = r6.windows(world, corr, tmp_path / 'win_plain')
    out = r6.windows(world, mixed, tmp_path / 'win_mixed')
    for path in out.iterdir():
        if path.is_file() and path.suffix in ('.json', '.csv', '.html'):
            assert 'EXCLUDED_PAIR_ID' not in path.read_text(encoding='utf-8'), path.name
    doc = json.loads((out / 'local_delta.json').read_text(encoding='utf-8'))
    base = json.loads((plain / 'local_delta.json').read_text(encoding='utf-8'))
    assert doc['status'] == base['status'] and doc['provenance']['correspondence_warnings'] == (
        base['provenance']['correspondence_warnings'] + [{'warning': 'case_excluded_pairs_dropped', 'count': 1}])
    assert (out / 'local_delta_rows.csv').read_text(encoding='utf-8') == (plain / 'local_delta_rows.csv').read_text(encoding='utf-8')


def test_dv_template_and_dv_grade_skip_the_excluded_cases(tmp_path, capsys):
    root = tdt._tree(tmp_path)
    for name in ('vertical_short-Q-SD2-R-L1', 'verticalshort-S-L1-T-PXL'):
        tdt._case(root, name)
    out = tmp_path / 'out'
    assert main(['dv-template', '--root', str(root), '--timings', '2', '--out', str(out)]) == 0
    manifest = json.loads((out / 'dv_template_manifest.json').read_text(encoding='utf-8'))
    assert manifest['excluded_vertical_cases'] == 2 and manifest['electrode_count'] == 3
    assert 'excluded_vertical_cases' in capsys.readouterr().out
    plain = dv_grade.collect_short_pairs(tdg._tree(tmp_path / 'plain'))
    grade_root = tdg._tree(tmp_path / 'g')
    for name in ('vertical_short-Q-SD2-R-L1', 'vertical_short-U-SD3-U-SD3'):
        tdg._case(grade_root, name)
    found = dv_grade.collect_short_pairs(grade_root)
    assert found['excluded_vertical_cases'] == 2 and found['pairs'].keys() == plain['pairs'].keys()
    assert found['short_case_count'] == plain['short_case_count']


def test_template_export_layer_check_ignores_layers_used_only_by_excluded_cases(tmp_path):
    cases = tte.CASES + ['vertical_short-A-L1-B-SD2']
    root, dl1 = tte.make_tree(tmp_path, cases=cases)
    out = tmp_path / 'out'
    config = tte.write_config(tmp_path, root, dl1, cases=tte.CASES)    # layer_stack has no SD2
    assert tte.run(config, out) == 0
