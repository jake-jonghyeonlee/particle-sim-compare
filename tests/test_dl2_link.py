"""change-roi/T10: the two DL2 links.

R1  ``candidates-change`` writes ``mask_derivation.json`` into its four export folders (same writer and rule as
    ``export-template-rois``); the DL2 ``parse_mask_derivation`` rules are re-implemented here (DL2 is never imported).
R3  ``confirm-pairs`` reads ``psdl2_search_batch_v1`` envelopes (synthetic, every key of the DL2 table) next to the unchanged
    ``psdl2_baseline_ncc_v1`` path.
Also: a missing ``--cnt-from-dl1-root`` is an input error (exit 2, nothing written).
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from simcompare import change_candidates, template_export
from simcompare.cli import main
from simcompare.confirm_pairs import candidate_id, pair_id_for
from simcompare.correspondence import load_correspondence
from simcompare.numeric_schema import CaseIdentity, Endpoint, NumericContractError

from test_change_candidates import cli_args, digests, load, run_change, world   # noqa: F401  (world is a fixture)
from test_confirm_pairs import (DIRECTIONS, R2T, T2R, cid, decisions, env, load as load_json, pairs_of,   # noqa: F401
                                query_rows, run, sha)

RULE = 'between_stack_order_v1'
FOLDERS = ('reference_export', 'target_export', 'reference_index_export', 'target_index_export')


# ---- R1: the DL2 parse_mask_derivation rules, re-implemented -------------------------------------------------------------

def dl2_check_mask_derivation(value, rows):
    """Raises AssertionError with the DL2 error code. `rows` are the full rows of the export folder the file sits in."""
    assert isinstance(value, dict) and set(value) == {'schema_version', 'rule_version', 'cases'}, 'mask_derivation_envelope_invalid'
    assert value['schema_version'] == 'psdl2_mask_derivation_v1', 'unknown_mask_derivation_schema'
    assert isinstance(value['rule_version'], str) and value['rule_version'].strip()
    assert isinstance(value['cases'], list), 'mask_derivation_cases_list_required'
    lookup = {}
    for entry in value['cases']:
        assert isinstance(entry, dict) and set(entry) == {'case', 'processed_endpoint', 'removed_layers'}, 'mask_derivation_entry_invalid'
        case = entry['case']
        assert isinstance(case, dict) and set(case) == {'failure_type', 'endpoints'}, 'mask_derivation_case_invalid'
        assert case['failure_type'] in ('lateral_short', 'vertical_short'), 'mask_derivation_failure_type_invalid'
        assert isinstance(case['endpoints'], list) and len(case['endpoints']) == 2, 'mask_derivation_two_endpoints_required'
        endpoints = [(e['electrode_id'], e['role'], e['actual_layer']) for e in case['endpoints']]
        assert all(set(e) == {'electrode_id', 'role', 'actual_layer'} for e in case['endpoints'])
        assert len(set(endpoints)) == 2, 'mask_derivation_duplicate_endpoint'
        removed = entry['removed_layers']
        assert isinstance(removed, list), 'mask_derivation_removed_layers_list_required'
        assert len(set(removed)) == len(removed), 'mask_derivation_removed_layers_duplicate'
        assert not set(removed) & {e[2] for e in endpoints}, 'mask_derivation_removed_layer_is_endpoint_layer'
        assert removed == sorted(removed)                       # stored sorted and unique
        processed = entry['processed_endpoint']
        if case['failure_type'] == 'lateral_short':
            assert processed is None and not removed, 'mask_derivation_lateral_must_be_empty'
        else:
            assert isinstance(processed, str), 'mask_derivation_processed_endpoint_required'
            assert len([e for e in endpoints if e[0] == processed]) == 1, 'mask_derivation_processed_endpoint_invalid'
        key = (case['failure_type'], tuple(sorted(endpoints)))
        assert key not in lookup, 'mask_derivation_duplicate_case'
        lookup[key] = entry
    used = set()
    for row in rows:                                            # load_dataset: every ready row needs an entry, none may dangle
        key = (row['failure_type'], tuple(sorted((e['electrode_id'], e['role'], e['actual_layer']) for e in row['endpoints'])))
        if row['status'] == 'ready':
            assert key in lookup, 'mask_derivation_case_missing: ' + row['roi_id']
        if key in lookup:
            used.add(key)
    assert not set(lookup) - used, 'mask_derivation_case_unreferenced'
    return lookup


@pytest.fixture(scope='module')
def run_out(world, tmp_path_factory):                           # noqa: F811
    out = tmp_path_factory.mktemp('dl2_link_out') / 'out'
    code = run_change(world, out, top_k=1)
    return SimpleNamespace(out=out, code=code, payload=load(out))


def test_four_export_folders_carry_a_dl2_valid_mask_derivation(run_out):
    assert run_out.code == 0
    for folder in FOLDERS:
        rows = load(run_out.out / folder, 'review_rois.json')['rois']
        derivation = load(run_out.out / folder, 'mask_derivation.json')
        lookup = dl2_check_mask_derivation(derivation, rows)
        assert derivation['rule_version'] == RULE and len(lookup) == 2              # the AB and the AC case
        for entry in derivation['cases']:                                           # the world is lateral only: a known empty value
            assert entry['case']['failure_type'] == 'lateral_short'
            assert entry['processed_endpoint'] is None and entry['removed_layers'] == []
        assert (run_out.out / folder / 'mask_derivation.json').read_bytes() == template_export._json_bytes(derivation)


def test_payload_and_execution_list_the_derivation_files_and_rule(run_out):
    expected = {'reference_to_target': {'query': 'reference_export/mask_derivation.json', 'index': 'target_index_export/mask_derivation.json'},
                'target_to_reference': {'query': 'target_export/mask_derivation.json', 'index': 'reference_index_export/mask_derivation.json'}}
    payload, execution = run_out.payload, load(run_out.out, 'execution.json')
    assert payload['mask_derivation_files'] == expected == execution['mask_derivation_files']
    assert payload['mask_derivation_rule'] == RULE == execution['mask_derivation_rule'] == execution['args']['mask_derivation_rule']
    assert {f'{folder}/mask_derivation.json' for folder in FOLDERS} <= set(execution['outputs'])
    assert payload['match_files']['reference_to_target']['query'] == 'reference_export/review_rois.json'      # unchanged
    for side, folder, field in (('reference', 'reference_export', 'mask_derivation_sha256'),
                                ('target', 'target_export', 'mask_derivation_sha256'),
                                ('reference', 'reference_index_export', 'index_mask_derivation_sha256'),
                                ('target', 'target_index_export', 'index_mask_derivation_sha256')):
        assert payload['inputs'][side][field] == sha(run_out.out / folder / 'mask_derivation.json')
    for paths in expected.values():                                                  # relative to the output folder
        assert all((run_out.out / path).is_file() for path in paths.values())


def test_same_input_gives_the_same_bytes_including_mask_derivation(world, run_out, tmp_path):   # noqa: F811
    again = tmp_path / 'again'
    assert run_change(world, again, top_k=1) == run_out.code
    assert digests(again) == digests(run_out.out)
    assert 'reference_export/mask_derivation.json' in digests(again)


def test_vertical_and_lateral_values_follow_the_template_export_rule():
    identity = dict(product_id='P', design_version='D', source_id='S', process_step_id='STEP', exposure_id='E',
                    frame_id='F', source_contract_version='v1')
    cases = [SimpleNamespace(identity=CaseIdentity(case_id='V', **identity), failure_type='vertical_short', analysis_layer='L3',
                             endpoints=(Endpoint('B', 'data', 'L3'), Endpoint('A', 'gate', 'L1'))),
             SimpleNamespace(identity=CaseIdentity(case_id='H', **identity), failure_type='lateral_short', analysis_layer='L2',
                             endpoints=(Endpoint('A', 'gate', 'L2'), Endpoint('C', 'data', 'L2')))]
    side = SimpleNamespace(name='reference', dataset=SimpleNamespace(cases=cases))
    from dataclasses import asdict
    rows = [{'roi_id': c.identity.case_id, 'status': 'ready', 'source_identity': asdict(c.identity)} for c in cases]
    rows.append({'roi_id': 'blocked', 'status': 'blocked', 'source_identity': asdict(cases[0].identity)})
    order = {'L1': 1, 'L2': 2, 'L3': 3, 'L4': 4}
    document = template_export._mask_derivation(change_candidates._derivation_rows(side, rows), order)
    full = [{'roi_id': c.identity.case_id, 'status': 'ready', 'failure_type': c.failure_type,
             'endpoints': [asdict(e) for e in c.endpoints]} for c in cases]
    dl2_check_mask_derivation(document, full)
    by_type = {e['case']['failure_type']: e for e in document['cases']}
    assert by_type['vertical_short']['processed_endpoint'] == 'B' and by_type['vertical_short']['removed_layers'] == ['L2']
    assert by_type['lateral_short']['processed_endpoint'] is None and by_type['lateral_short']['removed_layers'] == []
    assert len(document['cases']) == 2                          # the blocked duplicate adds no entry
    # the same rule as export-template-rois: identical entries from its own entry function
    template_rows = [{'case_id': 'V', 'failure_type': 'vertical_short', 'analysis_layer': 'L3',
                      'endpoints': [asdict(e) for e in cases[0].endpoints]}]
    assert template_export._derivation_entry(template_rows[0], order) == by_type['vertical_short']


def test_rule_value_is_required_and_only_one_rule_is_known(world, tmp_path, capsys):          # noqa: F811
    out = tmp_path / 'out'
    for rule, code in (('other_rule_v1', 'mask_derivation_rule_unknown'), ('', 'mask_derivation_rule_unknown')):
        capsys.readouterr()
        assert run_change(world, out, rule=rule) == 2
        assert code in capsys.readouterr().err and not out.exists()
    with pytest.raises(NumericContractError) as caught:
        change_candidates.run_candidates_change(
            {}, out, cnt_from_dl1_root=str(world.dl1), align_search_um=3, align_min_dice=0.3, align_layer_tolerance_um=0.5,
            change_tolerance_um=0.2, window_um=10, merge_overlap=0.5, top_k=1, mask_derivation_rule=None)
    assert caught.value.code == 'mask_derivation_rule_required' and not out.exists()
    with pytest.raises(SystemExit):                              # the CLI flag itself is required (no default)
        main(cli_args(world.config, out, world.dl1, rule=None))
    assert not out.exists()


def test_missing_dl1_root_is_an_input_error_before_anything_is_written(world, tmp_path, capsys):   # noqa: F811
    out = tmp_path / 'out'
    missing = tmp_path / 'no_such_dl1'
    capsys.readouterr()
    assert run_change(world, out, dl1=missing) == 2
    err = capsys.readouterr().err
    assert f'오류: cnt_dl1_root_missing: --cnt-from-dl1-root 경로가 없습니다: {missing} (resolved: {missing.resolve()})' in err
    assert not out.exists()
    plain_file = tmp_path / 'a_file'
    plain_file.write_text('x')
    assert run_change(world, out, dl1=plain_file) == 2 and not out.exists()


def test_existing_root_without_model_folders_still_fails_the_alignment_with_exit_3(world, tmp_path):   # noqa: F811
    empty = tmp_path / 'empty_root'
    empty.mkdir()
    out = tmp_path / 'out'
    assert run_change(world, out, dl1=empty, top_k=1) == 3
    assert load(out)['alignment']['status'] == 'alignment_failed_identity_used'


# ---- R3: psdl2_search_batch_v1 -------------------------------------------------------------------------------------------

BATCH = 'psdl2_search_batch_v1'
UNUSABLE_TAIL = (('other_product', 'primary', 'candidate_product_not_configured'),
                 ('QUERY', 'primary', 'candidate_same_product'),
                 ('INDEX', 'weird_scope', 'unsupported_search_scope'))


def index_sources(env, index_name, kind):
    path = env['cand'] / f'{index_name}_index_export' / 'review_rois.json'
    if kind == 'single_export':
        return 'single_export'
    product_ids = None if kind == 'null_products' else [index_name]
    other = {'export_sha256': 'e' * 64, 'patch_set_sha256s': ['a' * 64], 'product_ids': ['elsewhere']}
    return [{'export_sha256': sha(path), 'patch_set_sha256s': [], 'product_ids': product_ids}, other]


def fake_batch(env, direction, name=None, *, sources='sources', tail=True, **overrides):
    """psdl2_search_batch_v1 as ``psdl2.search --all-ready`` writes it: every key of the DL2 table."""
    query_name, index_name = DIRECTIONS[direction]
    centres = R2T if direction == 'reference_to_target' else T2R
    frame = {'reference': 'reference_frame', 'target': 'target_frame'}[index_name]
    query_sha = sha(env['cand'] / f'{query_name}_export' / 'review_rois.json')
    rankings = []
    for row in query_rows(env, direction):
        candidates = [{'rank': rank, 'roi_id': f'idx_{rank}', 'product_id': index_name, 'frame_id': frame,
                       'center_xy_um': list(centre), 'search_scope': 'primary'} for rank, centre in enumerate(centres, 1)]
        candidates.append({'rank': 4, 'roi_id': 'idx_4', 'product_id': index_name, 'frame_id': frame,
                           'center_xy_um': list(centres[1]), 'search_scope': 'supplementary'})
        if tail:
            for rank, (product, scope, _) in enumerate(UNUSABLE_TAIL, 5):
                product = {'QUERY': query_name, 'INDEX': index_name}.get(product, product)
                candidates.append({'rank': rank, 'roi_id': f'idx_{rank}', 'product_id': product, 'frame_id': frame,
                                   'center_xy_um': list(centres[0]), 'search_scope': scope})
        rankings.append({'query_roi_id': row['roi_id'], 'query_product_id': row['product_id'], 'query_export_sha256': query_sha,
                         'candidates': candidates})
    payload = {'schema_version': BATCH, 'checkpoint_fingerprint': 'c' * 64, 'view_fingerprint': 'd' * 64,
               'index_sources': index_sources(env, index_name, sources),
               'inputs': {'query_export_sha256': query_sha, 'query_dataset_sha256': None, 'index_sha256': 'f' * 64},
               'arguments': {'topk': 3, 'expand': False, 'per_product_topk': None, 'exclude_query_product': False,
                             'ignore_mask_derivation': False, 'cohort': [index_name, 'other_product']},
               'results': [], 'rankings': rankings}
    payload.update(overrides)
    path = env['tmp'] / (name or f'batch_{direction}.json')
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    return path


def listing(env, matches, capsys):
    capsys.readouterr()
    args = ['confirm-pairs', '--list', '--config', str(env['config']), '--candidates', str(env['cand'])]
    for direction, path in matches.items():
        args += ['--match', f'{direction}={path}']
    code = main(args)
    return code, capsys.readouterr()


@pytest.mark.parametrize('sources', ['sources', 'single_export'])
def test_list_shows_candidates_and_unusable_reasons_from_a_batch_envelope(env, capsys, sources):
    path = fake_batch(env, 'reference_to_target', sources=sources)
    code, captured = listing(env, {'reference_to_target': path}, capsys)
    assert code == 0
    shown = json.loads(captured.out)
    n_queries = len(query_rows(env, 'reference_to_target'))
    assert shown['n_candidates'] == 4 * n_queries
    first = query_rows(env, 'reference_to_target')[0]['roi_id']
    by_id = {c['candidate_id']: c for c in shown['candidates']}
    assert set(by_id) >= {candidate_id('reference_to_target', first, rank) for rank in (1, 2, 3, 4)}
    assert by_id[candidate_id('reference_to_target', first, 4)]['flags'] == {'supplementary': True}
    assert by_id[candidate_id('reference_to_target', first, 4)]['search_scope'] == 'supplementary'
    assert by_id[candidate_id('reference_to_target', first, 1)]['search_scope'] == 'primary'
    reasons = {(u['query_roi_id'], u['rank']): u['reason'] for u in shown['unavailable_candidates']}
    assert [reasons[(first, rank)] for rank in (5, 6, 7)] == [reason for _, _, reason in UNUSABLE_TAIL]
    assert 'score' not in set(json.dumps(shown))              # no score is invented for a batch candidate


def test_ncc_listing_is_unchanged_by_the_batch_support(env, capsys):
    from test_confirm_pairs import fake_match
    code, captured = listing(env, {'reference_to_target': fake_match(env, 'reference_to_target')}, capsys)
    shown = json.loads(captured.out)
    assert code == 0 and set(shown) == {'candidate_id_format', 'candidates', 'unavailable_queries', 'n_candidates'}
    assert all('search_scope' not in c for c in shown['candidates'])


@pytest.mark.parametrize('sources', ['sources', 'single_export'])
def test_accept_writes_pair_level_correspondence_and_sidecar_that_compare_windows_reads(env, capsys, sources):
    path = fake_batch(env, 'reference_to_target', sources=sources)
    accepted = [cid(env, 'reference_to_target', 1, 0), cid(env, 'reference_to_target', 4, 1)]
    rows = decisions(env, [[accepted[0], 'accept', 'primary'], [accepted[1], 'accept', 'supplementary']])
    assert run(env, {'reference_to_target': path}, rows) == 0
    out = env['tmp'] / 'out'
    pairs = pairs_of(out)
    assert len(pairs) == 2 and all(p['status'] == 'ready' for p in pairs)
    expected = {pair_id_for('reference_to_target', query_rows(env, 'reference_to_target')[i]['roi_id'], rank, centre)
                for i, rank, centre in ((0, 1, R2T[0]), (1, 4, R2T[1]))}
    assert {p['pair_id'] for p in pairs} == expected
    qrow = query_rows(env, 'reference_to_target')[0]
    first = next(p for p in pairs if p['pair_id'] == pair_id_for('reference_to_target', qrow['roi_id'], 1, R2T[0]))
    assert first['a']['roi_id'] == qrow['roi_id'] and first['b']['center_xy_um'] == list(R2T[0])       # a = reference
    assert first['b']['product_id'] == 'target' and first['source']['kind'] == 'manual'
    assert load_correspondence(out / 'roi_correspondence.json')['pairs']
    sidecar = load_json(out / 'confirmed_pairs_sidecar.json')
    match = sidecar['inputs']['matches']['reference_to_target']
    assert match['match_schema'] == BATCH and match['sha256'] == sha(path)
    assert match['index_export_verification'] == ('unverifiable_single_export' if sources == 'single_export' else 'index_sources')
    if sources == 'sources':
        assert match['matched_sources'][0]['export_sha256'] == sha(env['cand'] / 'target_index_export' / 'review_rois.json')
        assert match['matched_sources'][0]['product_ids'] == ['target']
    else:
        assert match['index_sources'] == 'single_export'
    record = sidecar['pairs'][first['pair_id']]['candidates'][0]
    assert record['match_schema'] == BATCH and record['rank'] == 1 and record['search_scope'] == 'primary'
    assert record['candidate_roi_id'] == 'idx_1' and record['score'] is None
    supp = sidecar['pairs'][next(p['pair_id'] for p in pairs if p['pair_id'] in expected and p is not first)]['candidates'][0]
    assert supp['search_scope'] == 'supplementary' and supp['rank'] == 4
    windows = env['tmp'] / 'windows'
    assert main(['compare-windows', '--config', str(env['config']), '--correspondence', str(out / 'roi_correspondence.json'),
                 '--window-sizes', '2', '--out', str(windows)]) in (0, 3)
    assert {row['pair_id'] for row in load_json(windows / 'local_delta.json')['rows']} == expected


def test_target_to_reference_batch_is_flipped_so_a_is_reference(env):
    path = fake_batch(env, 'target_to_reference')
    qrow = query_rows(env, 'target_to_reference')[0]
    assert run(env, {'target_to_reference': path}, decisions(env, [[cid(env, 'target_to_reference', 2), 'accept', '']])) == 0
    (pair,) = pairs_of(env['tmp'] / 'out')
    assert (pair['a']['product_id'], pair['a']['center_xy_um']) == ('reference', list(T2R[1]))
    assert (pair['b']['product_id'], pair['b']['roi_id']) == ('target', qrow['roi_id'])


def test_dataset_query_uses_the_per_ranking_query_hash(env):
    path = fake_batch(env, 'reference_to_target')
    payload = load_json(path)
    payload['inputs']['query_export_sha256'] = None                      # --query-dataset runs: the ranking carries the hash
    payload['inputs']['query_dataset_sha256'] = '9' * 64
    path.write_text(json.dumps(payload), encoding='utf-8')
    assert run(env, {'reference_to_target': path}, decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', '']])) == 0


def assert_rejected(env, matches, rows, capsys, *needles):
    capsys.readouterr()
    assert run(env, matches, rows) == 2
    assert not (env['tmp'] / 'out').exists()                              # nothing is written
    err = capsys.readouterr().err
    for needle in needles:
        assert needle in err, err


def test_hash_mismatches_exit_two_and_write_nothing(env, capsys):
    rows = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', '']])
    path = fake_batch(env, 'reference_to_target')
    payload = load_json(path)
    payload['rankings'][0]['query_export_sha256'] = '0' * 64               # a ranking from another query export
    path.write_text(json.dumps(payload), encoding='utf-8')
    assert_rejected(env, {'reference_to_target': path}, rows, capsys, 'match_input_hash_mismatch', 'query_export_sha256')
    stale_index = fake_batch(env, 'reference_to_target', name='stale.json')
    payload = load_json(stale_index)
    payload['index_sources'][0]['export_sha256'] = '1' * 64                # no source equals the candidates index export
    stale_index.write_text(json.dumps(payload), encoding='utf-8')
    assert_rejected(env, {'reference_to_target': stale_index}, rows, capsys, 'match_input_hash_mismatch', 'index_sources')
    no_hash = fake_batch(env, 'reference_to_target', name='nohash.json')
    payload = load_json(no_hash)
    payload['inputs']['query_export_sha256'] = None
    payload['rankings'][0]['query_export_sha256'] = None
    no_hash.write_text(json.dumps(payload), encoding='utf-8')
    assert_rejected(env, {'reference_to_target': no_hash}, rows, capsys, 'match_input_hash_missing')


def test_null_product_ids_make_the_index_products_candidates_unverifiable(env, capsys):
    path = fake_batch(env, 'reference_to_target', sources='null_products')
    code, captured = listing(env, {'reference_to_target': path}, capsys)
    shown = json.loads(captured.out)
    assert code == 0 and shown['n_candidates'] == 0
    assert {u['reason'] for u in shown['unavailable_candidates'] if u['rank'] <= 4} == {'candidate_product_unverifiable'}
    rows = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', '']])
    assert_rejected(env, {'reference_to_target': path}, rows, capsys, 'candidate_match_unavailable', 'candidate_product_unverifiable')


def test_unusable_candidates_cannot_be_accepted(env, capsys):
    path = fake_batch(env, 'reference_to_target')
    for rank, (_, _, reason) in enumerate(UNUSABLE_TAIL, 5):
        rows = decisions(env, [[cid(env, 'reference_to_target', rank), 'accept', '']])
        assert_rejected(env, {'reference_to_target': path}, rows, capsys, 'candidate_match_unavailable', reason)


def test_malformed_envelopes_are_input_errors(env, capsys):
    rows = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', '']])
    path = fake_batch(env, 'reference_to_target')
    payload = load_json(path)
    del payload['rankings']
    path.write_text(json.dumps(payload), encoding='utf-8')
    assert_rejected(env, {'reference_to_target': path}, rows, capsys, 'match_rankings_required')
    bad = fake_batch(env, 'reference_to_target', name='bad.json', index_sources=[{'export_sha256': 3}])
    assert_rejected(env, {'reference_to_target': bad}, rows, capsys, 'match_index_sources_invalid')
    unknown = fake_batch(env, 'reference_to_target', name='unknown.json', schema_version='psdl2_search_batch_v0')
    assert_rejected(env, {'reference_to_target': unknown}, rows, capsys, 'match_schema_version_unsupported')


def test_both_schemas_can_be_mixed_between_directions(env):
    from test_confirm_pairs import fake_match
    matches = {'reference_to_target': fake_batch(env, 'reference_to_target'),
               'target_to_reference': fake_match(env, 'target_to_reference')}
    rows = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', ''], [cid(env, 'target_to_reference', 1), 'accept', '']])
    assert run(env, matches, rows) == 0
    sidecar = load_json(env['tmp'] / 'out' / 'confirmed_pairs_sidecar.json')['inputs']['matches']
    assert sidecar['reference_to_target']['match_schema'] == BATCH and 'match_schema' not in sidecar['target_to_reference']
