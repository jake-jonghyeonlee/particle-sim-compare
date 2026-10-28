"""compare-multiproduct/T3 fix (review blk:1): model-report on a real candidates-change output.

The ROI rows of candidates-change exports carry no source_region_id, so the reference_to_target pairs and the mutual-merged
pairs must join their ROI through the candidates payload (region_id) or the sidecar candidates. The world and the
candidates-change run are the repository's own test world (test_change_candidates); only the DL2 envelope is built by hand.
"""
import csv
import json
from pathlib import Path

import pytest
import yaml

from simcompare import model_report
from simcompare.cli import main
from simcompare.confirm_pairs import candidate_id, pair_id_for

from test_change_candidates import PRODUCTS, run_change, world   # noqa: F401  (world is a module-scoped fixture)
from test_confirm_pairs import decisions, load, query_rows, sha

REFERENCE, TARGET = PRODUCTS['reference'], PRODUCTS['target']


def frame_id(cand, export):
    return load(Path(cand)/export/'review_rois.json')['rois'][0]['frame_id']


def equal_target_row(cand, reference_row):
    key = model_report._endpoint_key(reference_row)
    (row,) = [r for r in query_rows({'cand': cand}, 'target_to_reference') if model_report._endpoint_key(r) == key]
    return row


def envelope(tmp, cand, centres):
    """psdl2_search_batch_v1; centres[(direction, query_roi_id)] = [rank-1 candidate centre] (one candidate per ranking)."""
    index_frame = {'reference_to_target': frame_id(cand, 'target_index_export'), 'target_to_reference': frame_id(cand, 'reference_index_export')}
    index_product = {'reference_to_target': TARGET, 'target_to_reference': REFERENCE}
    rankings = []
    for direction, query_name in (('reference_to_target', 'reference'), ('target_to_reference', 'target')):
        export_sha = sha(Path(cand)/f'{query_name}_export'/'review_rois.json')
        for row in query_rows({'cand': cand}, direction):
            centre = centres.get((direction, row['roi_id']))
            if centre is None:
                continue
            rankings.append({'query_roi_id': row['roi_id'], 'query_product_id': row['product_id'], 'query_export_sha256': export_sha,
                             'candidates': [{'rank': 1, 'roi_id': 'idx_1', 'product_id': index_product[direction],
                                             'frame_id': index_frame[direction], 'center_xy_um': list(centre),
                                             'search_scope': 'primary'}]})
    payload = {'schema_version': 'psdl2_search_batch_v1', 'checkpoint_fingerprint': 'c'*64, 'view_fingerprint': 'd'*64,
               'index_sources': [{'export_sha256': 'e'*64, 'patch_set_sha256s': [], 'product_ids': [REFERENCE, TARGET, 'P']}],
               'inputs': {'query_export_sha256': None, 'query_dataset_sha256': None, 'index_sha256': 'f'*64},
               'arguments': {'topk': 1}, 'results': [], 'rankings': rankings}
    path = Path(tmp)/'batch.json'
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    return path


def models_file(world, assignments_target):
    section = {'data_dir': 'models/P', 'product_id': 'P', 'condition_assignment': {**assignments_target, 'product_id': 'P'}}
    path = world.base/'models_mr.yaml'
    path.write_text(yaml.safe_dump({'schema_version': 'compare_models_v1', 'target_product_id': TARGET, 'models': {'P': section}}),
                    encoding='utf-8')
    return path


def flow(world, tmp_path, accepted, centres):
    """candidates-change -> confirm-pairs --models (P pending) -> reference compare-windows -> model-report. Returns (conf, rep dir)."""
    cand = tmp_path/'chg'
    assert run_change(world, cand, top_k=2) in (0, 3)
    env = {'tmp': tmp_path, 'cand': cand, 'config': world.config}
    batch = envelope(tmp_path, cand, centres(cand))
    models = models_file(world, world.assignments['target'])
    rows = [[cid, 'accept', ''] for cid in accepted(cand)]
    args = ['confirm-pairs', '--config', str(world.config), '--candidates', str(cand), '--models', str(models),
            '--decisions', str(decisions(env, rows)), '--labelled-by', 'designer1', '--mutual-tolerance-um', '5',
            '--match', f'reference_to_target={batch}', '--match', f'target_to_reference={batch}', '--out', str(tmp_path/'conf')]
    assert main(args) in (0, 3)
    conf = tmp_path/'conf'
    assert main(['compare-windows', '--config', str(world.config), '--correspondence', str(conf/'roi_correspondence.json'),
                 '--window-sizes', '10', '--out', str(tmp_path/'win_ref')]) in (0, 3)
    code = main(['model-report', '--candidates', str(cand), '--confirm', str(conf), '--windows', f'{REFERENCE}={tmp_path/"win_ref"}',
                 '--diameter-um', '5', '--window-um', '10', '--out', str(tmp_path/'rep')])
    assert code == 0
    with (tmp_path/'rep'/'model_report.csv').open(encoding='utf-8-sig', newline='') as handle:
        return conf, list(csv.DictReader(handle))


def first_reference_row(cand):
    return query_rows({'cand': cand}, 'reference_to_target')[0]


def test_candidates_change_has_no_source_region_id_but_shared_region_ids(world, tmp_path):
    cand = tmp_path/'chg'
    assert run_change(world, cand, top_k=2) in (0, 3)
    rois = load(cand/'reference_export'/'review_rois.json')['rois']+load(cand/'target_export'/'review_rois.json')['rois']
    assert all(row.get('source_region_id') is None for row in rois)
    payload = load(cand/'candidate_queries_v1.json')
    regions = {d['direction']: {q['region_id'] for q in d['queries']} for d in payload['directions']}
    assert regions['reference_to_target'] == regions['target_to_reference'] and regions['target_to_reference']


def test_reference_to_target_pair_joins_the_roi_of_its_region(world, tmp_path):
    def accepted(cand):
        return [candidate_id('reference_to_target', first_reference_row(cand)['roi_id'], 1)]

    def centres(cand):
        return {('reference_to_target', first_reference_row(cand)['roi_id']): first_reference_row(cand)['center_xy_um']}
    conf, rows = flow(world, tmp_path, accepted, centres)
    expected = equal_target_row(tmp_path/'chg', first_reference_row(tmp_path/'chg'))['roi_id']
    (pair,) = [r for r in rows if r['model'] == REFERENCE and r['pair_id']]
    assert pair['direction'] == 'reference_to_target' and pair['roi_linked'] == 'True' and pair['roi_id'] == expected
    assert not [r for r in rows if r['roi_linked'] == 'False' or r['cell_status'] == 'accepted_pair_missing']
    assert pair['score_um2'] != ''          # the change attributes of the ROI's query are joined too


def test_mutual_merged_pair_kept_as_reference_to_target_stays_on_its_roi(world, tmp_path):
    cand = tmp_path/'chg'
    assert run_change(world, cand, top_k=2) in (0, 3)
    rrow = first_reference_row(cand)
    trow = equal_target_row(cand, rrow)
    r_id, t_id = rrow['roi_id'], trow['roi_id']
    # target ROI's candidate sits at the reference ROI centre, the reference ROI's candidate at the target ROI centre (+ a jitter
    # inside the tolerance chosen so that the reference_to_target pair has the lower pair id and survives the merge)
    t_centre = rrow['center_xy_um']
    for step in range(1, 200):
        r_centre = [trow['center_xy_um'][0]+step*0.01, trow['center_xy_um'][1]]
        if pair_id_for('reference_to_target', r_id, 1, r_centre) < pair_id_for('target_to_reference', t_id, 1, t_centre):
            break
    else:
        pytest.fail('no jitter found')
    tmp_path_cand = tmp_path

    def accepted(_):
        return [candidate_id('reference_to_target', r_id, 1), candidate_id('target_to_reference', t_id, 1)]

    def centres(_):
        return {('reference_to_target', r_id): r_centre, ('target_to_reference', t_id): t_centre}
    # run_change inside flow rewrites the same output folder name: use a fresh tmp for the flow
    sub = tmp_path_cand/'flow'
    sub.mkdir()
    conf, rows = flow(world, sub, accepted, centres)
    (pair,) = load(conf/'roi_correspondence.json')['pairs']
    assert pair['a']['roi_id'] == r_id and pair['b']['roi_id'] is None          # the surviving pair is the reference_to_target one
    record = list(load(conf/'confirmed_pairs_sidecar.json')['pairs'].values())[0]
    assert record['direction'] == 'reference_to_target' and len(record['merged_with']) == 1
    (row,) = [r for r in rows if r['model'] == REFERENCE and r['pair_id']]
    assert row['roi_linked'] == 'True' and row['roi_id'] == t_id and row['cell_status'] == 'ready'
    assert not [r for r in rows if r['cell_status'] == 'accepted_pair_missing' or r['roi_linked'] == 'False']


class _Files:
    pass


def test_two_candidate_target_rois_make_an_unlinked_row(monkeypatch):
    key = ('lateral_short', 'L1', ('A', 'B'))
    reference = {'r1': {'failure_type': 'lateral_short', 'analysis_layer': 'L1', 'endpoints': [{'electrode_id': 'A'}, {'electrode_id': 'B'}]}}
    twin = {'failure_type': 'lateral_short', 'analysis_layer': 'L1', 'endpoints': [{'electrode_id': 'B'}, {'electrode_id': 'A'}]}
    payload = {'directions': [{'direction': 'reference_to_target', 'queries': [{'region_id': 'g', 'roi_ids': ['r1']}]},
                              {'direction': 'target_to_reference', 'queries': [{'region_id': 'g', 'roi_ids': ['t1', 't2']}]}]}
    monkeypatch.setattr(model_report, '_export_rows', lambda d, p, direction, f: reference if direction == 'reference_to_target' else {'t1': twin, 't2': twin})
    linker = model_report._Linker('x', payload, ['t1', 't2'], None)
    assert linker.link('reference_to_target', [{'direction': 'reference_to_target', 'query_roi_id': 'r1'}]) == (None, 'several_target_rois_for_region')
    single = model_report._Linker('x', payload, ['t1'], None)
    assert single.link('reference_to_target', [{'direction': 'reference_to_target', 'query_roi_id': 'r1'}]) == ('t1', None)
    assert key  # silence


def test_duplicate_key_is_refused():
    rows = [{'roi_id': 'a', 'model': 'm', 'pair_id': 'p'}, {'roi_id': 'a', 'model': 'm', 'pair_id': 'p'}]
    with pytest.raises(Exception, match='model_report_duplicate_key'):
        model_report.check_unique(rows)
    model_report.check_unique(rows[:1])
