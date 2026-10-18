"""roi_correspondence_v1: 검증, dataset binding, 네 변환기와 왕복."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from simcompare.adapters.numeric import load_dataset
from simcompare.correspondence import (
    SCHEMA_VERSION, attach_roi_ids, bind_pairs, from_dl2_search, load_correspondence,
    to_dl1_relations, to_dl2_relations, to_review_roi_config, validate_correspondence,
    write_correspondence,
)
from simcompare.numeric_schema import NumericContractError
from simcompare.pipeline import load_numeric_config, run_numeric_roi_export
from simcompare.synthetic import generate_numeric_demo


def endpoints(a='A', b='B', layer_a='M2'):
    return [{'electrode_id': a, 'role': 'gate', 'actual_layer': layer_a},
            {'electrode_id': b, 'role': 'data', 'actual_layer': 'M2'}]


def side(product, frame, center, *, analysis_layer='M2', eps=None, failure_type=None, roi_id=None):
    data = {'product_id': product, 'design_version': 'synthetic_design', 'frame_id': frame,
            'center_xy_um': list(center), 'analysis_layer': analysis_layer}
    if eps is not None:
        data['endpoints'] = eps
    if failure_type is not None:
        data['failure_type'] = failure_type
    if roi_id is not None:
        data['roi_id'] = roi_id
    return data


def manual_source():
    return {'kind': 'manual', 'labelled_by': 'designer-1', 'labelled_at': '2026-10-20', 'note': ''}


def payload(pairs=None):
    pairs = [{'pair_id': 'P1', 'a': side('reference', 'reference_frame', [102.5, 200.5], eps=endpoints(), failure_type='lateral_short'),
              'b': side('target', 'target_frame', [101.5, 200.5], eps=endpoints(), failure_type='lateral_short'),
              'repeats_per_frame': None, 'source': manual_source(), 'status': 'ready', 'reasons': []},
             {'pair_id': 'P2', 'a': side('reference', 'reference_frame', [104.5, 200.5]),
              'b': side('target', 'target_frame', [100.5, 201.5]),
              'source': manual_source(), 'status': 'ready'}] if pairs is None else pairs
    return {'schema_version': SCHEMA_VERSION, 'producer': {'kind': 'manual', 'version': '1', 'commit': None,
                                                           'created_at': '2026-10-18'}, 'pairs': pairs}


@pytest.fixture
def demo(tmp_path):
    generate_numeric_demo(tmp_path / 'demo')
    cfg = load_numeric_config(tmp_path / 'demo' / 'config.yaml')
    reference = load_dataset(tmp_path / 'demo' / 'reference')
    target = load_dataset(tmp_path / 'demo' / 'target')
    return cfg, reference, target


def test_validate_normalises_and_warns_on_duplicate_centres():
    doubled = payload()
    doubled['pairs'].append({**copy.deepcopy(doubled['pairs'][0]), 'pair_id': 'P3'})
    result = validate_correspondence(doubled)
    assert [p['pair_id'] for p in result['pairs']] == ['P1', 'P2', 'P3']
    assert result['pairs'][1]['repeats_per_frame'] is None and result['pairs'][1]['reasons'] == []
    assert result['pairs'][1]['a']['endpoints'] is None and result['pairs'][1]['a']['failure_type'] is None
    assert result['warnings'] == [{'pair_id': 'P3', 'duplicate_of': 'P1', 'warning': 'duplicate_centres'}]


@pytest.mark.parametrize('mutate,code', [
    (lambda p: p.update(schema_version='roi_correspondence_v0'), 'unsupported_correspondence_schema'),
    (lambda p: p['pairs'].append({**copy.deepcopy(p['pairs'][0])}), 'duplicate_correspondence_pair_id'),
    (lambda p: p['pairs'][0]['a'].pop('analysis_layer'), 'invalid_correspondence_side'),
    (lambda p: p['pairs'][0]['a'].update(center_xy_um=[1.0]), 'invalid_correspondence_center'),
    (lambda p: p['pairs'][0]['a'].update(center_xy_um=[float('nan'), 1.0]), 'invalid_number'),
    (lambda p: p['pairs'][0]['a'].update(endpoints=endpoints('A', 'A')), 'invalid_correspondence_endpoints'),
    (lambda p: p['pairs'][0]['a'].update(failure_type='open'), 'invalid_correspondence_failure_type'),
    (lambda p: p['pairs'][0].update(repeats_per_frame={'a': 0, 'b': 1}), 'invalid_correspondence_repeats'),
    (lambda p: p['pairs'][0].update(source={'kind': 'dl2_search', 'score': 0.9, 'rank': 1}), 'invalid_correspondence_source'),
    (lambda p: p['pairs'][0].update(source={'kind': 'manual', 'labelled_by': 'x'}), 'invalid_correspondence_source'),
    (lambda p: p['pairs'][0].update(status='blocked', reasons=[]), 'invalid_correspondence_status'),
    (lambda p: p.update(producer={'kind': 'oracle'}), 'invalid_correspondence_producer'),
])
def test_contract_violations_are_rejected(mutate, code):
    broken = payload()
    mutate(broken)
    with pytest.raises(NumericContractError) as info:
        validate_correspondence(broken)
    assert info.value.code == code


def test_duplicate_json_keys_rejected(tmp_path):
    path = tmp_path / 'c.json'
    path.write_text('{"schema_version": "roi_correspondence_v1", "schema_version": "x", "producer": {}, "pairs": []}')
    with pytest.raises(NumericContractError, match='duplicate_correspondence_key'):
        load_correspondence(path)


def test_bind_requires_matching_products_and_blocks_only_bad_positions(demo):
    cfg, reference, target = demo
    bound = bind_pairs(payload(), reference, target)
    assert [p.status for p in bound] == ['ready', 'ready']
    assert bound[0].a.frame.frame_id == 'reference_frame' and bound[0].b.frame.frame_id == 'target_frame'
    assert bound[0].a.endpoints[0].electrode_id == 'A' and bound[1].a.endpoints is None
    outside = payload()
    outside['pairs'][0]['a']['center_xy_um'] = [99.0, 200.5]
    outside['pairs'][1]['b']['analysis_layer'] = 'M9'
    bound = bind_pairs(outside, reference, target)
    assert bound[0].status == 'blocked' and bound[0].reasons == ('position_outside_source_frame_a',)
    assert bound[1].status == 'blocked' and bound[1].reasons == ('analysis_layer_not_in_source_b',)
    swapped = payload()
    swapped['pairs'][0]['a']['product_id'] = 'target'
    with pytest.raises(NumericContractError, match='correspondence_product_mismatch'):
        bind_pairs(swapped, reference, target)
    wrong_frame = payload()
    wrong_frame['pairs'][0]['b']['frame_id'] = 'reference_frame'
    with pytest.raises(NumericContractError, match='correspondence_frame_mismatch'):
        bind_pairs(wrong_frame, reference, target)


def test_review_roi_round_trip_attaches_ids_and_builds_dl_relations(tmp_path, demo):
    cfg, reference, target = demo
    source = payload()
    exports = {}
    for side_name, key in (('reference', 'a'), ('target', 'b')):
        derived, report = to_review_roi_config(cfg, source, side_name)
        assert report['n_requests'] == 2 and report['pairs_without_endpoints'] == ['P2']
        assert derived['map']['input']['product_id'] == side_name
        assert Path(derived['reference']['data_dir']).is_absolute()
        assert derived['review_rois']['manual'][0]['endpoints'] == endpoints()
        path = tmp_path / f'{side_name}.yaml'
        path.write_text(yaml.safe_dump(derived, sort_keys=False, allow_unicode=True), encoding='utf-8')
        result = run_numeric_roi_export(load_numeric_config(path), tmp_path / ('rois_' + side_name))
        exports[key] = json.loads((tmp_path / ('rois_' + side_name) / 'review_rois.json').read_text())
        assert result['n_rois'] == 2 and result['n_ready'] == 1  # P2 lacks endpoints → case_context_required
    attached, report = attach_roi_ids(source, exports['a'], exports['b'])
    # P2 lacks endpoints → its ROI rows are blocked → no roi_id attached (DL2 never indexes blocked rows)
    assert report['attached'] == 2 and report['not_found'] == [] and report['ambiguous'] == []
    assert [(e['pair_id'], e['side']) for e in report['blocked_roi']] == [('P2', 'a'), ('P2', 'b')]
    ids = {(p['pair_id'], k): p[k]['roi_id'] for p in attached['pairs'] for k in ('a', 'b')}
    assert ids[('P1', 'a')].startswith('roi_') and ids[('P1', 'b')].startswith('roi_')
    assert ids[('P2', 'a')] is None and ids[('P2', 'b')] is None
    relations, rel_report = to_dl2_relations(attached)
    assert relations['schema_version'] == 'roi_relations_v1'
    assert relations['relations'] == [{'a': ids[('P1', 'a')], 'b': ids[('P1', 'b')], 'relation': 'similar'}]
    assert rel_report['skipped'] == [{'pair_id': 'P2', 'reason': 'roi_id_missing'}]
    rows, dl1_report = to_dl1_relations(attached)
    assert rows == [('reference', 'target', 'similar')] and dl1_report['skipped'] == []
    written = write_correspondence(tmp_path / 'attached.json', attached)
    reloaded = load_correspondence(written)
    assert reloaded['pairs'][0]['a']['roi_id'] == ids[('P1', 'a')]
    # DL2 reader contract: exactly the keys a/b/relation and no self relation.
    for row in relations['relations']:
        assert set(row) == {'a', 'b', 'relation'} and row['a'] != row['b']


def test_blocked_pairs_and_missing_ids_are_skipped_not_invented():
    source = payload()
    source['pairs'][0].update(status='blocked', reasons=['designer_unsure'])
    relations, report = to_dl2_relations(source)
    assert relations['relations'] == []
    assert [s['reason'] for s in report['skipped']] == ['blocked_pair', 'roi_id_missing']
    rows, dl1 = to_dl1_relations(source)
    assert rows == [('reference', 'target', 'similar')] and dl1['skipped'] == [{'pair_id': 'P1', 'reason': 'blocked_pair'}]


def test_from_dl2_search_uses_export_context_and_index_fingerprints():
    row_a = {'roi_id': 'roi_q', 'status': 'ready', 'center_xy_um': [102.5, 200.5], 'analysis_layer': 'M2',
             'endpoints': endpoints(), 'failure_type': 'lateral_short',
             'source_identity': {'product_id': 'reference', 'design_version': 'synthetic_design', 'frame_id': 'reference_frame',
                                 'source_id': 's', 'process_step_id': 'step', 'exposure_id': 'e', 'case_id': 'AB',
                                 'source_contract_version': 'v2'}}
    row_b = {**copy.deepcopy(row_a), 'roi_id': 'roi_c', 'center_xy_um': [101.5, 200.5],
             'source_identity': {**row_a['source_identity'], 'product_id': 'target', 'frame_id': 'target_frame'}}
    export_a = {'schema_version': 'review_roi_v1', 'rois': [row_a]}
    export_b = {'schema_version': 'review_roi_v1', 'rois': [row_b]}
    index = {'schema_version': 'psdl2_index_v2', 'checkpoint_fingerprint': 'ckpt', 'view_fingerprint': 'view', 'records': []}
    result = {'query': {'roi_id': 'roi_q'}, 'status': 'available',
              'primary': [{'roi_id': 'roi_c', 'product_id': 'target', 'score': 0.93, 'same_physical_case': False}],
              'supplementary': []}
    converted = from_dl2_search([result], index, export_a, export_b)
    pair = converted['pairs'][0]
    assert pair['pair_id'] == 'dl2:roi_q:roi_c' and pair['status'] == 'ready'
    assert pair['a']['roi_id'] == 'roi_q' and pair['b']['roi_id'] == 'roi_c'
    assert pair['b']['center_xy_um'] == [101.5, 200.5] and pair['b']['process_step_id'] == 'step'
    assert pair['source'] == {'kind': 'dl2_search', 'score': 0.93, 'rank': 1, 'scope': 'primary',
                              'checkpoint_fingerprint': 'ckpt', 'index_fingerprint': 'view',
                              'index_schema_version': 'psdl2_index_v2', 'same_physical_case': False}
    with pytest.raises(NumericContractError, match='unsupported_dl2_index'):
        from_dl2_search([result], {**index, 'schema_version': 'psdl2_index_v1'}, export_a, export_b)
    with pytest.raises(NumericContractError, match='dl2_candidate_roi_not_in_export'):
        from_dl2_search([result], index, export_a, {'schema_version': 'review_roi_v1', 'rois': []})


def test_cli_validate_and_relations(tmp_path):
    path = tmp_path / 'c.json'
    path.write_text(json.dumps(payload()), encoding='utf-8')
    run = subprocess.run([sys.executable, '-m', 'simcompare.correspondence', 'validate', '--correspondence', path],
                         capture_output=True, text=True)
    assert run.returncode == 0 and json.loads(run.stdout)['n_ready'] == 2
    run = subprocess.run([sys.executable, '-m', 'simcompare.correspondence', 'to-dl1-relations',
                          '--correspondence', path, '--out', tmp_path / 'dl1.csv'], capture_output=True, text=True)
    assert run.returncode == 0 and (tmp_path / 'dl1.csv').read_text() == 'reference,target,similar\n'
    run = subprocess.run([sys.executable, '-m', 'simcompare.correspondence', 'to-dl2-relations',
                          '--correspondence', path, '--out', tmp_path / 'dl2.json'], capture_output=True, text=True)
    assert run.returncode == 3  # no roi_id yet → skipped, partial
    broken = tmp_path / 'broken.json'
    broken.write_text('{"schema_version": "nope"}', encoding='utf-8')
    run = subprocess.run([sys.executable, '-m', 'simcompare.correspondence', 'validate', '--correspondence', broken],
                         capture_output=True, text=True)
    assert run.returncode == 2 and 'unsupported_correspondence_schema' in run.stderr
