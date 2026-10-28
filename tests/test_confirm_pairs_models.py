"""compare-multiproduct/T1: confirm-pairs --models (model candidates -> models/<key>/roi_correspondence.json).

The DL2 envelope is built by hand (psdl2_search_batch_v1); DL2 itself is never run. Model datasets are copies of the demo target
dataset under another product_id.
"""
import json
from pathlib import Path

import pytest

from simcompare.cli import main
from simcompare.confirm_pairs import candidate_id
from simcompare.correspondence import validate_correspondence

from test_confirm_pairs import T2R, decisions, env, load, query_rows, sha   # noqa: F401  (env is a fixture)
from test_models_config import make_model, model_section, write_models

BATCH = 'psdl2_search_batch_v1'
MODEL_FRAME_POINTS = [(101.5, 201.0), (102.5, 200.5), (100.5, 201.5)]   # inside the target-shaped model frame


def demo_dir(env):
    return env['config'].parent


def envelope(env, *, products=('reference', 'target', 'C', 'D'), sources='sources', name='batch.json'):
    """One envelope with the rankings of both directions (as the DL2 runner writes them).

    target ROI ranking:   r1 reference, r2 C, r3 C, r4 D, r5 reference
    reference ROI ranking: r1 target, r2 C (a model candidate of a reference query: unusable)
    """
    target_row = query_rows(env, 'target_to_reference')[0]
    reference_row = query_rows(env, 'reference_to_target')[0]
    t_sha = sha(env['cand']/'target_export'/'review_rois.json')
    r_sha = sha(env['cand']/'reference_export'/'review_rois.json')

    def cand(rank, product, frame, centre, scope='primary'):
        return {'rank': rank, 'roi_id': f'idx_{rank}', 'product_id': product, 'frame_id': frame, 'center_xy_um': list(centre),
                'search_scope': scope}
    target_candidates = [cand(1, 'reference', 'reference_frame', T2R[0]), cand(2, 'C', 'target_frame', MODEL_FRAME_POINTS[0]),
                         cand(3, 'C', 'target_frame', MODEL_FRAME_POINTS[1]), cand(4, 'D', 'target_frame', MODEL_FRAME_POINTS[2]),
                         cand(5, 'reference', 'reference_frame', T2R[1])]
    reference_candidates = [cand(1, 'target', 'target_frame', MODEL_FRAME_POINTS[0]),
                            cand(2, 'C', 'target_frame', MODEL_FRAME_POINTS[1])]
    index_sources = ('single_export' if sources == 'single_export'
                     else [{'export_sha256': 'e'*64, 'patch_set_sha256s': [], 'product_ids': list(products)}])
    payload = {'schema_version': BATCH, 'checkpoint_fingerprint': 'c'*64, 'view_fingerprint': 'd'*64, 'index_sources': index_sources,
               'inputs': {'query_export_sha256': None, 'query_dataset_sha256': None, 'index_sha256': 'f'*64},
               'arguments': {'topk': 5}, 'results': [],
               'rankings': [{'query_roi_id': target_row['roi_id'], 'query_product_id': 'target', 'query_export_sha256': t_sha,
                             'candidates': target_candidates},
                            {'query_roi_id': reference_row['roi_id'], 'query_product_id': 'reference',
                             'query_export_sha256': r_sha, 'candidates': reference_candidates}]}
    path = env['tmp']/name
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    return path


def tid(env, rank):
    return candidate_id('target_to_reference', query_rows(env, 'target_to_reference')[0]['roi_id'], rank)


def rid(env, rank):
    return candidate_id('reference_to_target', query_rows(env, 'reference_to_target')[0]['roi_id'], rank)


def scene(env, *, c=None, d=False, extra=(), both_matches=True):
    """C configured (optionally altered), D pending (no folder) unless d=True (then D configured too)."""
    demo = demo_dir(env)
    make_model(demo, 'C', **(c or {}))
    sections = {'C': model_section(demo, 'C'), 'D': model_section(demo, 'D')}
    if d:
        make_model(demo, 'D')
        sections['D'] = model_section(demo, 'D')
    for key in extra:
        make_model(demo, key)
        sections[key] = model_section(demo, key)
    models = write_models(demo, sections)
    batch = envelope(env)
    matches = {'target_to_reference': batch, **({'reference_to_target': batch} if both_matches else {})}
    return models, matches


def run_models(env, models, matches, rows, out='out', *, capsys=None):
    path = decisions(env, rows)
    args = ['confirm-pairs', '--config', str(env['config']), '--candidates', str(env['cand']), '--models', str(models),
            '--decisions', str(path), '--labelled-by', 'designer1', '--mutual-tolerance-um', '0.5', '--out', str(env['tmp']/out)]
    for direction, match in matches.items():
        args += ['--match', f'{direction}={match}']
    return main(args)


def test_configured_and_pending_models_one_pair_file_and_one_deferred(env):
    models, matches = scene(env)
    assert run_models(env, models, matches, [[tid(env, 2), 'accept', ''], [tid(env, 4), 'accept', 'pending model']]) == 3
    out = env['tmp']/'out'
    corr = load(out/'models'/'C'/'roi_correspondence.json')
    assert validate_correspondence(corr)['pairs']
    (pair,) = corr['pairs']
    row = query_rows(env, 'target_to_reference')[0]
    assert pair['a']['product_id'] == 'target' and pair['a']['roi_id'] == row['roi_id']
    assert pair['b']['product_id'] == 'C' and pair['b']['roi_id'] is None and pair['b']['design_version'] == 'synthetic_design'
    assert pair['b']['center_xy_um'] == list(MODEL_FRAME_POINTS[0]) and pair['status'] == 'ready'
    assert corr['producer']['kind'] == 'manual' and 'a=target(query), b=C' in corr['notes']
    assert not (out/'models'/'D'/'roi_correspondence.json').exists()
    deferred = load(out/'models'/'D'/'deferred.json')
    assert deferred['candidate_ids'] == [tid(env, 4)] and deferred['reason'] == 'model_assets_pending'
    assert not (out/'models'/'C'/'deferred.json').exists()
    assert load(out/'execution.json')['exit_code'] == 3 and 'model_assets_pending' in load(out/'execution.json')['reasons']
    index = load(out/'models'/'index.json')
    assert index['schema_version'] == 'confirm_models_index_v1' and index['target_product_id'] == 'target'
    assert index['models']['C'] == {'product_id': 'C', 'status': 'configured', 'n_ready': 1, 'n_blocked': 0, 'n_deferred': 0,
                                    'folder': 'models/C', 'correspondence': 'models/C/roi_correspondence.json'}
    assert index['models']['D']['status'] == 'assets_pending' and index['models']['D']['n_deferred'] == 1
    assert index['models']['D']['correspondence'] is None
    # the reference<->target files are the ordinary ones and carry no model pair
    assert load(out/'roi_correspondence.json')['pairs'] == []
    funnel = load(out/'confirm_funnel.json')
    assert funnel['models']['D']['asset_status'] == 'assets_pending'
    assert {s['stage']: s['count'] for s in funnel['models']['D']['stages']}['deferred_assets_pending'] == 1
    assert funnel['totals']['counts']['candidates'] == 3   # reference<->target candidates only: r1, r5 of the target ROI and r1 of the reference ROI


def test_sidecar_of_a_model_records_the_model_inputs(env):
    models, matches = scene(env)
    assert run_models(env, models, matches, [[tid(env, 2), 'accept', '']]) == 0
    out = env['tmp']/'out'
    sidecar = load(out/'models'/'C'/'confirmed_pairs_sidecar.json')
    demo = demo_dir(env)
    assert sidecar['schema_version'] == 'confirmed_pairs_sidecar_v1'
    assert sidecar['model'] == {'product_id': 'C', 'status': 'configured', 'models_sha256': sha(models), 'data_dir': 'models/C',
                                'manifest_sha256': sha(demo/'models'/'C'/'manifest.json'),
                                'asset_manifest_sha256': sha(demo/'models'/'C'/'review_assets.json'),
                                'electrode_import_record_sha256': None}
    (record,) = sidecar['pairs'].values()
    assert record['direction'] == 'target_to_reference' and record['mutual_status'] == 'one_way'
    assert record['opposite_match_given'] is False and record['merged_with'] == []
    assert record['candidates'][0]['candidate_id'] == tid(env, 2) and record['candidates'][0]['search_scope'] == 'primary'
    assert load(out/'execution.json')['exit_code'] == 0


def test_two_accepts_of_one_model_on_one_roi_give_two_pairs(env):
    models, matches = scene(env)
    assert run_models(env, models, matches, [[tid(env, 2), 'accept', ''], [tid(env, 3), 'accept', '']]) == 0
    pairs = load(env['tmp']/'out'/'models'/'C'/'roi_correspondence.json')['pairs']
    assert len(pairs) == 2 and len({p['pair_id'] for p in pairs}) == 2
    assert {tuple(p['b']['center_xy_um']) for p in pairs} == set(MODEL_FRAME_POINTS[:2])
    assert {p['a']['roi_id'] for p in pairs} == {query_rows(env, 'target_to_reference')[0]['roi_id']}


def test_reference_and_model_accepts_together_exit_0_when_nothing_is_deferred(env):
    models, matches = scene(env, d=True)
    rows = [[tid(env, 1), 'accept', 'reference candidate of the target ROI'], [tid(env, 2), 'accept', ''], [tid(env, 4), 'accept', '']]
    assert run_models(env, models, matches, rows) == 0
    out = env['tmp']/'out'
    (ref_pair,) = load(out/'roi_correspondence.json')['pairs']
    assert ref_pair['a']['product_id'] == 'reference' and ref_pair['b']['product_id'] == 'target'
    assert load(out/'models'/'D'/'roi_correspondence.json')['pairs'][0]['b']['product_id'] == 'D'
    assert load(out/'execution.json')['n_emitted_ready'] == 1


def test_only_deferred_accepts_exit_3_without_any_correspondence_pair(env):
    models, matches = scene(env)
    assert run_models(env, models, matches, [[tid(env, 4), 'accept', '']]) == 3
    out = env['tmp']/'out'
    assert load(out/'roi_correspondence.json')['pairs'] == []
    assert load(out/'models'/'C'/'roi_correspondence.json')['pairs'] == []
    assert (out/'models'/'D'/'deferred.json').exists()
    assert set(load(out/'execution.json')['reasons']) == {'no_emitted_ready_pairs', 'model_assets_pending'}


def test_model_candidate_of_a_reference_query_is_unusable_and_accepting_it_is_exit_2(env, capsys):
    models, matches = scene(env)
    assert run_models(env, models, matches, [[rid(env, 2), 'accept', '']]) == 2
    err = capsys.readouterr().err
    assert 'candidate_match_unavailable' in err and 'candidate_model_query_not_target' in err
    assert not (env['tmp']/'out').exists()


def test_list_shows_model_status_and_counts_a_shared_envelope_once(env, capsys):
    models, matches = scene(env)
    args = ['confirm-pairs', '--list', '--config', str(env['config']), '--candidates', str(env['cand']), '--models', str(models)]
    for direction, match in matches.items():                       # the same file under both --match
        args += ['--match', f'{direction}={match}']
    capsys.readouterr()
    assert main(args) == 0
    shown = json.loads(capsys.readouterr().out)
    by_id = {c['candidate_id']: c for c in shown['candidates']}
    assert by_id[tid(env, 2)]['model_status'] == 'configured' and by_id[tid(env, 4)]['model_status'] == 'assets_pending'
    assert 'model_status' not in by_id[tid(env, 1)] and 'model_status' not in by_id[rid(env, 1)]
    assert [c['candidate_product_id'] for c in shown['candidates']].count('C') == 2          # not 4
    assert [c['candidate_product_id'] for c in shown['candidates']].count('D') == 1
    unavailable = {u['candidate_id']: u['reason'] for u in shown['unavailable_candidates']}
    assert unavailable[rid(env, 2)] == 'candidate_model_query_not_target'


def test_without_models_a_model_candidate_stays_not_configured(env, capsys):
    _, matches = scene(env)
    args = ['confirm-pairs', '--list', '--config', str(env['config']), '--candidates', str(env['cand']),
            '--match', f'target_to_reference={matches["target_to_reference"]}']
    capsys.readouterr()
    assert main(args) == 0
    shown = json.loads(capsys.readouterr().out)
    assert {u['reason'] for u in shown['unavailable_candidates']} == {'candidate_product_not_configured'}
    assert not any('model_status' in c for c in shown['candidates'])


def test_decisions_json_tells_no_candidate_from_all_rejected(env):
    models, matches = scene(env, extra=('E',))
    rows = [[tid(env, 2), 'reject', ''], [tid(env, 3), 'reject', ''], [tid(env, 1), 'accept', '']]
    assert run_models(env, models, matches, rows) == 0
    data = load(env['tmp']/'out'/'models'/'decisions.json')
    assert data['schema_version'] == 'confirm_model_decisions_v1' and data['target_product_id'] == 'target'
    assert data['asset_status'] == {'C': 'configured', 'D': 'assets_pending', 'E': 'configured', 'reference': 'config_side'}
    by_roi = {r['query_roi_id']: r for r in data['rois']}
    roi = by_roi[query_rows(env, 'target_to_reference')[0]['roi_id']]
    assert roi['ranked'] is True and len(by_roi) == len(query_rows(env, 'target_to_reference'))
    others = [r for r in data['rois'] if r is not roi]                  # ROIs the envelope has no ranking for
    assert others and all(not r['ranked'] and r['products']['C']['candidates'] == 0 for r in others)
    products = roi['products']
    assert products['C'] == {'candidates': 2, 'accepted': 0, 'rejected': 2, 'undecided': 0, 'unavailable': {}, 'deferred': 0,
                             'asset_status': 'configured'}
    assert products['E'] == {'candidates': 0, 'accepted': 0, 'rejected': 0, 'undecided': 0, 'unavailable': {}, 'deferred': 0,
                             'asset_status': 'configured'}
    assert products['D']['candidates'] == 1 and products['D']['undecided'] == 1 and products['D']['asset_status'] == 'assets_pending'
    assert products['reference'] == {'candidates': 2, 'accepted': 1, 'rejected': 0, 'undecided': 1, 'unavailable': {}, 'deferred': 0,
                                     'asset_status': 'config_side'}
    assert data['reference_to_target_model_candidates_unusable'] == 1


def test_decisions_json_counts_unavailable_reasons_and_deferred(env):
    models, matches = scene(env)
    batch = envelope(env, products=('reference', 'target', 'D'), name='no_c.json')       # no source lists C
    matches = {'target_to_reference': batch}
    assert run_models(env, models, matches, [[tid(env, 4), 'accept', '']]) == 3
    data = load(env['tmp']/'out'/'models'/'decisions.json')
    products = {r['query_roi_id']: r for r in data['rois']}[query_rows(env, 'target_to_reference')[0]['roi_id']]['products']
    assert products['C'] == {'candidates': 2, 'accepted': 0, 'rejected': 0, 'undecided': 0,
                             'unavailable': {'candidate_product_unverifiable': 2}, 'deferred': 0, 'asset_status': 'configured'}
    assert products['D']['accepted'] == 1 and products['D']['deferred'] == 1
    assert data['reference_to_target_model_candidates_unusable'] == 0


@pytest.mark.parametrize('mutate, code', [
    (lambda data: data.update(target_product_id='reference'), 'models_target_mismatch'),
    (lambda data: data['models'].update(reference=data['models']['C']), 'models_product_is_config_side'),
    (lambda data: data['models'].update(target=data['models']['C']), 'models_product_is_config_side'),
    (lambda data: data['models'].update({'../C': data['models']['C']}), 'models_product_id_unsafe'),
    (lambda data: data['models'].update({'/abs': data['models']['C']}), 'models_product_id_unsafe'),
    (lambda data: data['models'].update({'c': data['models']['C']}), 'models_product_id_unsafe'),
])
def test_models_errors_exit_2_before_any_output(env, capsys, mutate, code):
    models, matches = scene(env)
    import yaml
    data = yaml.safe_load(models.read_text(encoding='utf-8'))
    mutate(data)
    models.write_text(yaml.safe_dump(data), encoding='utf-8')
    capsys.readouterr()
    assert run_models(env, models, matches, [[tid(env, 2), 'accept', '']]) == 2
    assert code in capsys.readouterr().err
    assert not (env['tmp']/'out').exists()


def test_models_yaml_in_another_folder_is_exit_2(env, capsys):
    models, matches = scene(env)
    moved = models.parent/'sub'/'models.yaml'
    moved.parent.mkdir()
    moved.write_bytes(models.read_bytes())
    capsys.readouterr()
    assert run_models(env, moved, matches, [[tid(env, 2), 'accept', '']]) == 2
    assert 'models_config_dir_mismatch' in capsys.readouterr().err
    assert not (env['tmp']/'out').exists()


def test_two_design_versions_in_the_model_dataset_is_unresolved(env, capsys):
    models, matches = scene(env, c={'other_design': True})
    assert run_models(env, models, matches, [[tid(env, 2), 'accept', '']]) == 2
    assert 'candidate_design_version_unresolved' in capsys.readouterr().err
    assert not (env['tmp']/'out').exists()


def test_model_pixel_pitch_different_from_target_blocks_the_pair(env):
    models, matches = scene(env, c={'pitch': 2})
    assert run_models(env, models, matches, [[tid(env, 2), 'accept', '']]) == 3
    out = env['tmp']/'out'
    (pair,) = load(out/'models'/'C'/'roi_correspondence.json')['pairs']
    assert pair['status'] == 'blocked' and 'pixel_pitch_mismatch' in pair['reasons']
    assert load(out/'models'/'index.json')['models']['C']['n_blocked'] == 1
    assert 'blocked_pairs_present' in load(out/'execution.json')['reasons']


def test_candidate_frame_missing_in_the_model_dataset_rejects_the_run(env, capsys):
    models, matches = scene(env, c={'frame_id': 'x_frame'})
    assert run_models(env, models, matches, [[tid(env, 2), 'accept', '']]) == 2
    assert 'correspondence_frame_mismatch' in capsys.readouterr().err
    assert not (env['tmp']/'out').exists()


def test_candidate_centre_outside_the_model_frame_blocks_only_that_pair(env):
    models, matches = scene(env)
    batch = json.loads(matches['target_to_reference'].read_text(encoding='utf-8'))
    batch['rankings'][0]['candidates'][2]['center_xy_um'] = [500.0, 500.0]        # C r3 outside
    matches['target_to_reference'].write_text(json.dumps(batch), encoding='utf-8')
    assert run_models(env, models, matches, [[tid(env, 2), 'accept', ''], [tid(env, 3), 'accept', '']]) == 3
    pairs = {tuple(p['b']['center_xy_um']): p for p in load(env['tmp']/'out'/'models'/'C'/'roi_correspondence.json')['pairs']}
    assert pairs[MODEL_FRAME_POINTS[0]]['status'] == 'ready' and pairs[(500.0, 500.0)]['status'] == 'blocked'


def test_model_assets_resolve_from_another_working_directory(env, monkeypatch, tmp_path):
    models, matches = scene(env)
    elsewhere = tmp_path/'elsewhere'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert run_models(env, models, matches, [[tid(env, 2), 'accept', '']]) == 0
    sidecar = load(env['tmp']/'out'/'models'/'C'/'confirmed_pairs_sidecar.json')
    assert sidecar['model']['asset_manifest_sha256'] == sha(demo_dir(env)/'models'/'C'/'review_assets.json')


def test_rerun_is_byte_identical_except_labelled_at(env):
    models, matches = scene(env)
    rows = [[tid(env, 2), 'accept', ''], [tid(env, 3), 'accept', ''], [tid(env, 4), 'accept', '']]
    assert run_models(env, models, matches, rows, 'run1') == 3
    assert run_models(env, models, matches, rows, 'run2') == 3
    one, two = env['tmp']/'run1', env['tmp']/'run2'
    names = sorted(str(p.relative_to(one)) for p in one.rglob('*') if p.is_file())
    assert names == sorted(str(p.relative_to(two)) for p in two.rglob('*') if p.is_file())
    assert 'models/C/roi_correspondence.json' in names and 'models/D/deferred.json' in names
    import re
    stamp = re.compile(r'"labelled_at": "[^"]+"')
    for name in names:
        a, b = (one/name).read_text(encoding='utf-8'), (two/name).read_text(encoding='utf-8')
        assert stamp.sub('', a) == stamp.sub('', b), name


def test_failure_while_writing_removes_only_own_files_including_models(env, monkeypatch):
    from simcompare import confirm_pairs
    models, matches = scene(env)

    def boom(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(confirm_pairs, '_model_decisions', boom)
    assert run_models(env, models, matches, [[tid(env, 2), 'accept', '']]) == 2
    assert not (env['tmp']/'out').exists()
