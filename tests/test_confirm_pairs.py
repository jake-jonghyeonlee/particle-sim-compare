"""PLAN_v4 T3: simcompare confirm-pairs (user-confirmed NCC/DL2 candidates -> roi_correspondence_v1 + sidecar).

DL2 match files are built by hand from the candidates exports; DL2 itself is never run.
"""
import csv
import hashlib
import json
from pathlib import Path

import pytest

from simcompare import confirm_pairs
from simcompare.cli import main
from simcompare.pipeline import load_numeric_config
from simcompare.correspondence import load_correspondence, main as correspondence_main, validate_correspondence
from simcompare.confirm_pairs import candidate_id, pair_id_for

# candidate centres inside each opposite frame (reference x[100,106) y[200,201), target x[100,103) y[200,202))
R2T = [(101.5, 201.0), (102.5, 200.5), (100.5, 201.5)]
T2R = [(103.0, 200.5), (104.5, 200.5), (101.5, 200.5)]
DIRECTIONS = {'reference_to_target': ('reference', 'target'), 'target_to_reference': ('target', 'reference')}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


@pytest.fixture
def env(tmp_path):
    demo = tmp_path/'demo'
    assert main(['demo-data', '--out', str(demo)]) == 0
    cand = tmp_path/'cand'
    assert main(['candidates', '--config', str(demo/'config.yaml'), '--min-count', '1', '--window-um', '4',
                 '--merge-overlap', '0.5', '--out', str(cand)]) == 0
    return {'tmp': tmp_path, 'config': demo/'config.yaml', 'cand': cand}


def query_rows(env, direction):
    query_name = DIRECTIONS[direction][0]
    rows = load(env['cand']/f'{query_name}_export'/'review_rois.json')['rois']
    return sorted((r for r in rows if r['status'] == 'ready'), key=lambda r: r['roi_id'])


def fake_match(env, direction, name=None, *, centres=None, per_roi=None, scores=(0.91, 0.83, 0.52), **overrides):
    """psdl2_baseline_ncc_v1 all_ready output as DL2 writes it (only the fields confirm-pairs reads and records)."""
    query_name, index_name = DIRECTIONS[direction]
    centres = centres or (R2T if direction == 'reference_to_target' else T2R)
    index_frame = {'reference': 'reference_frame', 'target': 'target_frame'}[index_name]
    results = []
    for row in query_rows(env, direction):
        own = (per_roi or {}).get(row['roi_id'], centres)
        results.append({
            'query': {'roi_id': row['roi_id'], 'product_id': row['product_id'], 'frame_id': row['frame_id'],
                      'center_xy_um': row['center_xy_um']},
            'status': 'available', 'reasons': [],
            'primary': [{'rank': rank, 'score': scores[rank-1], 'center_xy_um': list(c), 'product_id': index_name,
                         'frame_id': index_frame, 'template_top_left_px_yx': [0, 0]} for rank, c in enumerate(own, 1)],
            'endpoints_used': [], 'template_sha256': 'f'*64, 'shared_with': [],
            'flags': {'low_structure': False, 'window_clipped': False, 'query_valid_hole': False,
                      'candidate_valid_hole': False, 'score_gap': scores[0]-scores[1], 'near_equal_peaks': True},
            'query_invalid_pixel_count': 0})
    payload = {'schema_version': 'psdl2_baseline_ncc_v1', 'method': 'zncc_fft_role_layer_union',
               'implementation_revision': 'baseline_ncc_batch_v1.3', 'mode': 'all_ready', 'window_um': 4.0, 'topk': 3,
               'candidate_product_id': index_name, 'n_queries': len(results), 'status': 'available', 'results': results,
               'inputs': {'query_export_sha256': sha(env['cand']/f'{query_name}_export'/'review_rois.json'),
                          'index_export_sha256': sha(env['cand']/f'{index_name}_index_export'/'review_rois.json')},
               'arguments': {'window_um': 4.0, 'topk': 3, 'self_check': False}}
    payload.update(overrides)
    path = env['tmp']/(name or f'{direction}.json')
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    return path


def resync_candidates(env):
    """Pretend candidates had been run on the exports as they are now on disk."""
    path = env['cand']/'candidate_queries_v1.json'
    payload = load(path)
    for name in ('reference', 'target'):
        payload['inputs'][name]['export_sha256'] = sha(env['cand']/f'{name}_export'/'review_rois.json')
        payload['inputs'][name]['index_export_sha256'] = sha(env['cand']/f'{name}_index_export'/'review_rois.json')
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def decisions(env, rows, name='decisions.csv', header=('candidate_id', 'decision', 'note')):
    path = env['tmp']/name
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def cid(env, direction, rank, index=0):
    return candidate_id(direction, query_rows(env, direction)[index]['roi_id'], rank)


def run(env, matches, decisions_path, out='out', *, tolerance=0.5, labelled_by='designer1', extra=()):
    args = ['confirm-pairs', '--config', str(env['config']), '--candidates', str(env['cand']),
            '--decisions', str(decisions_path), '--labelled-by', labelled_by, '--mutual-tolerance-um', str(tolerance),
            '--out', str(env['tmp']/out), *extra]
    for direction, path in matches.items():
        args += ['--match', f'{direction}={path}']
    return main(args)


def both(env, **kwargs):
    return {d: fake_match(env, d, **kwargs) for d in DIRECTIONS}


def pairs_of(out):
    return load(Path(out)/'roi_correspondence.json')['pairs']


def walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


# (a)(b)(i) end to end; undecided and rejected never reach the numbers --------------------------------------

def test_end_to_end_only_accepted_reach_correspondence_and_local_delta(env, capsys):
    matches = fake_match(env, 'reference_to_target')
    accepted = [cid(env, 'reference_to_target', 1, 0), cid(env, 'reference_to_target', 2, 1)]
    rejected = cid(env, 'reference_to_target', 1, 2)
    path = decisions(env, [[accepted[0], 'accept', '같은 형상'], [accepted[1], 'accept', ''], [rejected, 'reject', '다른 곳']])
    assert run(env, {'reference_to_target': matches}, path) == 0
    out = env['tmp']/'out'
    assert sorted(p.name for p in out.iterdir()) == ['confirm_funnel.csv', 'confirm_funnel.json', 'confirmed_pairs_sidecar.json',
                                                      'execution.json', 'roi_correspondence.json']
    pairs = pairs_of(out)
    assert len(pairs) == 2 and all(p['status'] == 'ready' for p in pairs)
    expected = {pair_id_for('reference_to_target', query_rows(env, 'reference_to_target')[i]['roi_id'], rank, centre)
                for i, rank, centre in ((0, 1, R2T[0]), (1, 2, R2T[1]))}
    assert {p['pair_id'] for p in pairs} == expected
    for pair in pairs:
        assert pair['source']['kind'] == 'manual' and pair['source']['labelled_by'] == 'designer1'
        assert set(pair['source']) == {'kind', 'labelled_by', 'labelled_at'}
        assert load_correspondence(out/'roi_correspondence.json')  # round trip via the real loader
    # (i) existing validators accept the file
    assert validate_correspondence(load(out/'roi_correspondence.json'))['pairs']
    capsys.readouterr()
    assert correspondence_main(['validate', '--correspondence', str(out/'roi_correspondence.json')]) == 0
    # compare-windows on the produced file: local delta rows only for accepted pairs
    windows = env['tmp']/'windows'
    code = main(['compare-windows', '--config', str(env['config']), '--correspondence', str(out/'roi_correspondence.json'),
                 '--window-sizes', '2', '--out', str(windows)])
    assert code in (0, 3)
    delta = load(windows/'local_delta.json')
    row_ids = {row['pair_id'] for row in delta['rows']}
    assert row_ids == expected
    # (b) counterexample: the rejected and every undecided candidate id of the same files are absent everywhere
    forbidden = set()
    for direction in DIRECTIONS:
        for index, row in enumerate(query_rows(env, direction)):
            for rank, centre in enumerate(R2T if direction == 'reference_to_target' else T2R, 1):
                forbidden.add(pair_id_for(direction, row['roi_id'], rank, centre))
    forbidden -= expected
    text = (out/'roi_correspondence.json').read_text(encoding='utf-8')+(windows/'local_delta.json').read_text(encoding='utf-8')
    assert not any(pair_id in text for pair_id in forbidden)
    funnel = load(out/'confirm_funnel.json')['totals']['counts']
    assert funnel == {'candidates': 15, 'accept': 2, 'reject': 1, 'undecided': 12, 'blocked': 0, 'merged_away': 0,
                      'emitted_ready': 2}
    assert load(out/'execution.json')['exit_code'] == 0


def test_nothing_accepted_writes_empty_correspondence_and_exits_3(env):
    matches = fake_match(env, 'reference_to_target')
    path = decisions(env, [[cid(env, 'reference_to_target', 1), 'reject', 'x']])
    assert run(env, {'reference_to_target': matches}, path) == 3
    assert pairs_of(env['tmp']/'out') == []
    assert load(env['tmp']/'out'/'execution.json')['reasons'] == ['no_emitted_ready_pairs']
    empty = decisions(env, [], name='empty.csv')
    assert run(env, {'reference_to_target': matches}, empty, out='out2') == 3
    assert pairs_of(env['tmp']/'out2') == []


# (c) direction flip ---------------------------------------------------------------------------------------

def test_target_to_reference_is_flipped_so_a_is_reference(env):
    matches = fake_match(env, 'target_to_reference')
    qrow = query_rows(env, 'target_to_reference')[0]
    path = decisions(env, [[cid(env, 'target_to_reference', 2), 'accept', 'flip']])
    assert run(env, {'target_to_reference': matches}, path) == 0
    (pair,) = pairs_of(env['tmp']/'out')
    a, b = pair['a'], pair['b']
    assert (a['product_id'], a['frame_id'], a['center_xy_um']) == ('reference', 'reference_frame', list(T2R[1]))
    assert (b['product_id'], b['frame_id'], b['center_xy_um']) == ('target', 'target_frame', qrow['center_xy_um'])
    # the query ROI row supplies side b exactly as exported; the candidate side invents nothing
    assert b['roi_id'] == qrow['roi_id'] and b['endpoints'] == qrow['endpoints'] and b['failure_type'] == qrow['failure_type']
    assert a['roi_id'] is None and a['endpoints'] is None and a['failure_type'] is None
    assert a['analysis_layer'] == b['analysis_layer'] == qrow['analysis_layer']
    assert a['design_version'] == 'synthetic_design'
    side = load(env['tmp']/'out'/'confirmed_pairs_sidecar.json')['pairs'][pair['pair_id']]
    assert side['direction'] == 'target_to_reference'


def test_reference_to_target_query_row_is_side_a(env):
    matches = fake_match(env, 'reference_to_target')
    qrow = query_rows(env, 'reference_to_target')[0]
    path = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', '']])
    assert run(env, {'reference_to_target': matches}, path) == 0
    (pair,) = pairs_of(env['tmp']/'out')
    assert pair['a']['roi_id'] == qrow['roi_id'] and pair['a']['endpoints'] == qrow['endpoints']
    assert pair['b']['center_xy_um'] == list(R2T[0]) and pair['b']['roi_id'] is None and pair['b']['endpoints'] is None


# (d)(j) scores stay in the sidecar ---------------------------------------------------------------------------

def test_scores_only_in_sidecar_and_no_ca_values_anywhere(env):
    matches = both(env)
    path = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', '메모 있음'],
                           [cid(env, 'target_to_reference', 2, 1), 'accept', '']])
    assert run(env, matches, path) == 0
    out = env['tmp']/'out'
    for name in ('roi_correspondence.json', 'confirm_funnel.json', 'execution.json'):
        keys = set(walk(load(out/name)))
        assert not keys & {'score', 'rank', 'score_gap', 'flags', 'template_sha256', 'mutual_status'}, name
    text = (out/'roi_correspondence.json').read_text(encoding='utf-8')
    assert '0.91' not in text and '0.83' not in text
    sidecar = load(out/'confirmed_pairs_sidecar.json')
    assert sidecar['schema_version'] == 'confirmed_pairs_sidecar_v1'
    by_candidate = {c['candidate_id']: (pid, c) for pid, rec in sidecar['pairs'].items() for c in rec['candidates']}
    first = by_candidate[cid(env, 'reference_to_target', 1)][1]
    assert first['score'] == 0.91 and first['rank'] == 1 and first['decision_note'] == '메모 있음'
    assert first['template_sha256'] == 'f'*64 and first['implementation_revision'] == 'baseline_ncc_batch_v1.3'
    assert first['flags']['near_equal_peaks'] is True and first['score_gap'] == pytest.approx(0.08)
    assert by_candidate[cid(env, 'target_to_reference', 2, 1)][1]['score'] == 0.83
    assert sidecar['inputs']['matches']['reference_to_target']['sha256'] == sha(matches['reference_to_target'])
    assert sidecar['inputs']['decisions_sha256'] == sha(path)
    assert sidecar['inputs']['candidates_payload_sha256'] == sha(env['cand']/'candidate_queries_v1.json')
    assert sidecar['labelled_by'] == 'designer1'
    # (j) no hotspot / CA / density numbers in any output
    forbidden = {'original_case_ca', 'case_ca_in_region', 'ca_um2', 'hotspot', 'mask', 'records', 'density', 'rate', 'delta'}
    for name in sorted(p.name for p in out.iterdir() if p.suffix == '.json'):
        assert not forbidden & set(walk(load(out/name))), name


# (e) input errors: exit 2, nothing written, every problem listed -------------------------------------------------

def assert_rejected(env, code, capsys, *needles):
    assert code == 2
    assert not (env['tmp']/'out').exists()
    err = capsys.readouterr().err
    for needle in needles:
        assert needle in err, err
    return err


def test_unknown_duplicate_conflicting_ids_listed_together(env, capsys):
    matches = fake_match(env, 'reference_to_target')
    good = cid(env, 'reference_to_target', 1)
    other = cid(env, 'reference_to_target', 2)
    path = decisions(env, [[good, 'accept', ''], [good, 'accept', ''], [other, 'accept', ''], [other, 'reject', ''],
                           ['reference_to_target:no_such_roi:r1', 'accept', ''], ['garbage', 'accept', ''],
                           [cid(env, 'reference_to_target', 3), 'maybe', '']])
    err = assert_rejected(env, run(env, {'reference_to_target': matches}, path), capsys,
                          'duplicate_candidate_id', 'conflicting_decisions', 'unknown_candidate_id', 'decision_value_invalid')
    assert err.count('unknown_candidate_id') == 2


def test_extra_or_missing_decision_columns_rejected(env, capsys):
    matches = fake_match(env, 'reference_to_target')
    extra = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', '', 'x']], header=('candidate_id', 'decision', 'note', 'score'))
    assert_rejected(env, run(env, {'reference_to_target': matches}, extra), capsys, 'decisions_columns_invalid')
    short = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept']], header=('candidate_id', 'decision'))
    assert_rejected(env, run(env, {'reference_to_target': matches}, short), capsys, 'decisions_columns_invalid')


def test_non_utf8_decisions_rejected(env, capsys):
    matches = fake_match(env, 'reference_to_target')
    path = env['tmp']/'bad.csv'
    path.write_bytes(b'candidate_id,decision,note\n\xff\xfe,accept,x\n')
    assert_rejected(env, run(env, {'reference_to_target': matches}, path), capsys, 'decisions_unreadable')


def test_wrong_schema_version_and_failed_self_check(env, capsys):
    path = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', '']])
    bad = fake_match(env, 'reference_to_target', schema_version='psdl2_baseline_ncc_v0')
    assert_rejected(env, run(env, {'reference_to_target': bad}, path), capsys, 'match_schema_version_unsupported')
    failed = fake_match(env, 'reference_to_target', self_check={'passed': False})
    assert_rejected(env, run(env, {'reference_to_target': failed}, path), capsys, 'dl2_self_check_not_passed')
    odd = fake_match(env, 'reference_to_target', self_check={})
    assert_rejected(env, run(env, {'reference_to_target': odd}, path), capsys, 'dl2_self_check_not_passed')
    passed = fake_match(env, 'reference_to_target', self_check={'passed': True})
    assert run(env, {'reference_to_target': passed}, path) == 0  # passed (or absent) is fine


def test_match_for_other_exports_or_product_is_rejected(env, capsys):
    path = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', '']])
    swapped = fake_match(env, 'reference_to_target')
    payload = load(swapped)
    payload['inputs']['index_export_sha256'] = sha(env['cand']/'target_export'/'review_rois.json')  # the query export, not the index export
    swapped.write_text(json.dumps(payload), encoding='utf-8')
    assert_rejected(env, run(env, {'reference_to_target': swapped}, path), capsys, 'match_input_hash_mismatch')
    missing = fake_match(env, 'reference_to_target')
    payload = load(missing)
    del payload['inputs']
    missing.write_text(json.dumps(payload), encoding='utf-8')
    assert_rejected(env, run(env, {'reference_to_target': missing}, path), capsys, 'match_input_hash_missing')
    product = fake_match(env, 'reference_to_target', candidate_product_id='reference')
    assert_rejected(env, run(env, {'reference_to_target': product}, path), capsys, 'match_candidate_product_mismatch')


def test_unavailable_result_and_missing_match_file_ids_are_errors(env, capsys):
    matches = fake_match(env, 'reference_to_target')
    payload = load(matches)
    victim = payload['results'][0]
    victim.update(status='unavailable', primary=[], reasons=['template_without_structure:gate/M2'])
    matches.write_text(json.dumps(payload), encoding='utf-8')
    path = decisions(env, [[candidate_id('reference_to_target', victim['query']['roi_id'], 1), 'accept', ''],
                           [cid(env, 'target_to_reference', 1), 'accept', '']])
    assert_rejected(env, run(env, {'reference_to_target': matches}, path), capsys, 'candidate_match_unavailable',
                    'template_without_structure', 'candidate_match_file_not_given')


def test_required_arguments_and_existing_output(env, capsys):
    matches = fake_match(env, 'reference_to_target')
    path = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', '']])
    base = ['confirm-pairs', '--config', str(env['config']), '--candidates', str(env['cand']), '--match',
            f'reference_to_target={matches}', '--decisions', str(path), '--out', str(env['tmp']/'out')]
    assert main(base + ['--mutual-tolerance-um', '0.5']) == 2  # --labelled-by has no default
    assert main(base + ['--labelled-by', 'u']) == 2            # --mutual-tolerance-um has no default
    assert main(['confirm-pairs', '--config', str(env['config']), '--candidates', str(env['cand']), '--decisions', str(path),
                 '--labelled-by', 'u', '--mutual-tolerance-um', '0.5', '--out', str(env['tmp']/'out')]) == 2  # no --match
    assert not (env['tmp']/'out').exists()
    capsys.readouterr()
    (env['tmp']/'out').mkdir()
    (env['tmp']/'out'/'keep.txt').write_text('mine')
    assert run(env, {'reference_to_target': matches}, path) == 2
    assert (env['tmp']/'out'/'keep.txt').read_text() == 'mine' and len(list((env['tmp']/'out').iterdir())) == 1
    assert main(base[:-2] + ['--out', str(env['tmp']/'o2'), '--labelled-by', ' ', '--mutual-tolerance-um', '0.5']) == 2
    assert main(base[:-2] + ['--out', str(env['tmp']/'o3'), '--labelled-by', 'u', '--mutual-tolerance-um', '-1']) == 2
    assert not (env['tmp']/'o2').exists() and not (env['tmp']/'o3').exists()


# (f) per-pair blocked reasons --------------------------------------------------------------------------------------

def test_centre_outside_frame_blocks_pair_and_compare_windows_reports_it(env):
    outside = [(150.0, 201.0), R2T[1], R2T[2]]
    matches = fake_match(env, 'reference_to_target', centres=outside)
    path = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', ''], [cid(env, 'reference_to_target', 2), 'accept', '']])
    assert run(env, {'reference_to_target': matches}, path) == 3
    out = env['tmp']/'out'
    pairs = {p['pair_id']: p for p in pairs_of(out)}
    blocked = [p for p in pairs.values() if p['status'] == 'blocked']
    assert len(pairs) == 2 and len(blocked) == 1 and blocked[0]['reasons'] == ['position_outside_source_frame_b']
    sidecar = load(out/'confirmed_pairs_sidecar.json')['pairs']
    assert sidecar[blocked[0]['pair_id']]['status'] == 'blocked'
    assert sidecar[blocked[0]['pair_id']]['reasons'] == ['position_outside_source_frame_b']
    funnel = load(out/'confirm_funnel.json')['totals']['stages']
    assert {s['stage']: s for s in funnel}['blocked']['reasons'] == {'position_outside_source_frame_b': 1}
    assert load(out/'execution.json')['exit_code'] == 3
    windows = env['tmp']/'windows'
    assert main(['compare-windows', '--config', str(env['config']), '--correspondence', str(out/'roi_correspondence.json'),
                 '--window-sizes', '2', '--out', str(windows)]) == 3
    delta = load(windows/'local_delta.json')
    assert 'blocked_pairs_present' in delta['reasons']
    assert {r['pair_id'] for r in delta['rows']} == set(pairs)-{blocked[0]['pair_id']}


def test_missing_analysis_layer_blocks_pair(env):
    for name in ('reference', 'target'):
        export = env['cand']/f'{name}_export'/'review_rois.json'
        payload = load(export)
        for row in payload['rois']:
            row['analysis_layer'] = 'M9'
        export.write_text(json.dumps(payload), encoding='utf-8')
    resync_candidates(env)
    matches = fake_match(env, 'reference_to_target')
    path = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', '']])
    assert run(env, {'reference_to_target': matches}, path) == 3
    (pair,) = pairs_of(env['tmp']/'out')
    assert pair['status'] == 'blocked' and 'analysis_layer_not_in_source_b' in pair['reasons']


def test_pixel_pitch_mismatch_blocks_pair(env, monkeypatch):
    from dataclasses import replace
    real = confirm_pairs._load_datasets

    def patched(cfg):
        datasets = real(cfg)
        dataset = datasets['target']['dataset']
        frames = {k: replace(v, pixel_pitch_um=0.5) for k, v in dataset.frames.items()}
        datasets['target']['dataset'] = type(dataset)(dataset.cases, dataset.pairs, frames, *[
            getattr(dataset, f) for f in dataset.__dataclass_fields__ if f not in ('cases', 'pairs', 'frames')])
        return datasets
    monkeypatch.setattr(confirm_pairs, '_load_datasets', patched)
    monkeypatch.setattr(confirm_pairs, '_check_chain', lambda *args: None)  # the exports still describe the unpatched pitch
    matches = fake_match(env, 'reference_to_target')
    path = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', '']])
    assert run(env, {'reference_to_target': matches}, path) == 3
    (pair,) = pairs_of(env['tmp']/'out')
    assert pair['status'] == 'blocked' and 'pixel_pitch_mismatch' in pair['reasons']


def test_frame_or_product_identity_mismatch_rejects_whole_run(env, capsys):
    path = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', '']])
    payload = load(fake_match(env, 'reference_to_target'))
    for row in payload['results']:
        for entry in row['primary']:
            entry['frame_id'] = 'some_other_frame'
    bad = env['tmp']/'frame.json'
    bad.write_text(json.dumps(payload), encoding='utf-8')
    assert_rejected(env, run(env, {'reference_to_target': bad}, path), capsys, 'candidate_design_version_unresolved')
    payload = load(fake_match(env, 'reference_to_target'))
    for row in payload['results']:
        for entry in row['primary']:
            entry['product_id'] = 'reference'
    bad.write_text(json.dumps(payload), encoding='utf-8')
    assert_rejected(env, run(env, {'reference_to_target': bad}, path), capsys, 'match_candidate_product_mismatch')


def test_duplicate_same_centres_is_a_warning_not_a_block(env):
    matches = fake_match(env, 'reference_to_target', centres=[R2T[0], R2T[0], R2T[2]])
    path = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', ''], [cid(env, 'reference_to_target', 2), 'accept', '']])
    assert run(env, {'reference_to_target': matches}, path) == 0
    sidecar = load(env['tmp']/'out'/'confirmed_pairs_sidecar.json')
    assert len(sidecar['warnings']) == 1 and sidecar['warnings'][0]['warning'] == 'duplicate_centres'
    assert all(p['status'] == 'ready' for p in pairs_of(env['tmp']/'out'))


# (g) mutual / one_way ----------------------------------------------------------------------------------------------

def mutual_env(env, gap):
    near = [(103.0+gap, 200.5), T2R[1], T2R[2]]
    matches = {'reference_to_target': fake_match(env, 'reference_to_target'),
               'target_to_reference': fake_match(env, 'target_to_reference', centres=near)}
    path = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', ''], [cid(env, 'target_to_reference', 1), 'accept', '']])
    return matches, path


def test_mutual_double_accept_emits_one_pair_with_lower_id(env):
    matches, path = mutual_env(env, 0.3)
    assert run(env, matches, path, tolerance=0.5) == 0
    out = env['tmp']/'out'
    (pair,) = pairs_of(out)
    forward = pair_id_for('reference_to_target', query_rows(env, 'reference_to_target')[0]['roi_id'], 1, R2T[0])
    backward = pair_id_for('target_to_reference', query_rows(env, 'target_to_reference')[0]['roi_id'], 1, (103.3, 200.5))
    assert pair['pair_id'] == min(forward, backward)
    sidecar = load(out/'confirmed_pairs_sidecar.json')
    record = sidecar['pairs'][pair['pair_id']]
    dropped = max(forward, backward)
    assert record['mutual_status'] == 'mutual' and record['merged_with'] == [dropped] and record['mutual_with'] == [dropped]
    assert {c['candidate_id'] for c in record['candidates']} == {cid(env, 'reference_to_target', 1), cid(env, 'target_to_reference', 1)}
    assert sidecar['merged_away'][dropped]['kept_pair_id'] == pair['pair_id']
    counts = load(out/'confirm_funnel.json')['totals']['counts']
    assert counts['accept'] == 2 and counts['merged_away'] == 1 and counts['emitted_ready'] == 1


def test_beyond_tolerance_is_one_way_and_nothing_is_merged_or_dropped(env):
    matches, path = mutual_env(env, 0.3)
    assert run(env, matches, path, tolerance=0.2) == 0
    out = env['tmp']/'out'
    pairs = pairs_of(out)
    assert len(pairs) == 2
    sidecar = load(out/'confirmed_pairs_sidecar.json')
    assert {r['mutual_status'] for r in sidecar['pairs'].values()} == {'one_way'}
    assert all(r['merged_with'] == [] and r['mutual_with'] == [] for r in sidecar['pairs'].values())
    assert sidecar['merged_away'] == {}


def test_unaccepted_opposite_candidates_are_information_only(env):
    matches, _ = mutual_env(env, 0.3)
    path = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', ''], [cid(env, 'target_to_reference', 1), 'reject', '']])
    assert run(env, matches, path, tolerance=0.5) == 0
    out = env['tmp']/'out'
    (pair,) = pairs_of(out)
    record = load(out/'confirmed_pairs_sidecar.json')['pairs'][pair['pair_id']]
    assert record['mutual_status'] == 'one_way'  # a rejected opposite candidate never makes a pair mutual
    near = {c['candidate_id']: c['decision'] for c in record['opposite_candidates_within_tolerance']}
    assert near[cid(env, 'target_to_reference', 1)] == 'reject'
    assert any(decision == 'undecided' for decision in near.values())


def test_single_direction_marks_one_way_and_records_opposite_not_given(env):
    matches = {'reference_to_target': fake_match(env, 'reference_to_target')}
    path = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', '']])
    assert run(env, matches, path) == 0
    (pair,) = pairs_of(env['tmp']/'out')
    record = load(env['tmp']/'out'/'confirmed_pairs_sidecar.json')['pairs'][pair['pair_id']]
    assert record['mutual_status'] == 'one_way' and record['opposite_match_given'] is False


# (h) determinism ---------------------------------------------------------------------------------------------------

def test_rerun_is_byte_identical_except_labelled_at(env):
    matches, path = mutual_env(env, 0.3)
    stamps = iter(['2026-10-20T01:00:00Z', '2026-10-21T09:30:00Z'])
    results = []
    for out in ('run1', 'run2'):
        stamp = next(stamps)
        result = confirm_pairs.run_confirm_pairs(
            load_numeric_config(env['config']), env['tmp']/out,
            candidates_dir=env['cand'], matches={k: str(v) for k, v in matches.items()}, decisions_path=path,
            labelled_by='designer1', mutual_tolerance_um=0.5, now=lambda s=stamp: s)
        results.append((env['tmp']/out, stamp))
    (one, first), (two, second) = results
    for name in sorted(p.name for p in one.iterdir()):
        a, b = (one/name).read_bytes().decode('utf-8'), (two/name).read_bytes().decode('utf-8')
        assert a.replace(first, 'T') == b.replace(second, 'T'), name
    assert first in (one/'roi_correspondence.json').read_text() and second in (two/'roi_correspondence.json').read_text()
    # labelled_at is not part of pair_id: ids are equal while the timestamps differ
    assert [p['pair_id'] for p in pairs_of(one)] == [p['pair_id'] for p in pairs_of(two)]
    assert pairs_of(one)[0]['source']['labelled_at'] != pairs_of(two)[0]['source']['labelled_at']


def test_pair_id_depends_on_direction_roi_rank_and_centre_only():
    base = pair_id_for('reference_to_target', 'roi_x', 1, (1.0, 2.0))
    assert base == pair_id_for('reference_to_target', 'roi_x', 1, (1.0, 2.0))
    assert len({base, pair_id_for('target_to_reference', 'roi_x', 1, (1.0, 2.0)), pair_id_for('reference_to_target', 'roi_y', 1, (1.0, 2.0)),
                pair_id_for('reference_to_target', 'roi_x', 2, (1.0, 2.0)), pair_id_for('reference_to_target', 'roi_x', 1, (1.0, 2.5))}) == 5


# list helper ---------------------------------------------------------------------------------------------------------

def test_list_prints_candidate_ids_without_scores(env, capsys):
    matches = fake_match(env, 'reference_to_target')
    capsys.readouterr()
    assert main(['confirm-pairs', '--list', '--config', str(env['config']), '--candidates', str(env['cand']),
                 '--match', f'reference_to_target={matches}']) == 0
    listing = json.loads(capsys.readouterr().out)
    ids = [c['candidate_id'] for c in listing['candidates']]
    assert listing['candidate_id_format'] == '<direction>:<query_roi_id>:r<rank>' and len(ids) == 15
    assert cid(env, 'reference_to_target', 2, 3) in ids
    assert not {'score', 'score_gap'} & set(walk(listing))


# cleanup --------------------------------------------------------------------------------------------------------------

def test_failure_after_output_dir_creation_removes_only_own_files(env, monkeypatch, capsys):
    import simcompare.correspondence as correspondence
    matches = fake_match(env, 'reference_to_target')
    path = decisions(env, [[cid(env, 'reference_to_target', 1), 'accept', '']])

    def boom(_path):
        raise OSError('disk went away')
    monkeypatch.setattr(correspondence, 'load_correspondence', boom)
    assert run(env, {'reference_to_target': matches}, path) == 2
    assert not (env['tmp']/'out').exists()
    assert 'disk went away' in capsys.readouterr().err


# B1: the whole chain candidates -> exports -> matcher -> current datasets ---------------------------------------------------

def accept_first(env, direction='reference_to_target'):
    return fake_match(env, direction), decisions(env, [[cid(env, direction, 1), 'accept', '']])


def assert_exit2_with(env, matches, path, code, capsys, direction='reference_to_target'):
    capsys.readouterr()
    assert run(env, {direction: matches}, path) == 2
    assert code in capsys.readouterr().err
    assert not (env['tmp']/'out').exists()


def edit_numeric_manifests(env, edit):
    from simcompare.adapters.numeric import source_payload_hash
    for path in (env['config'].parent).rglob('manifest.json'):
        doc = load(path)
        if doc.get('schema_version') != 'numeric_v2':
            continue
        edit(doc)
        doc['input_hash'] = doc['source_manifest_hash'] = source_payload_hash(doc)
        path.write_text(json.dumps(doc), encoding='utf-8')


def test_stale_geometry_with_rehashed_manifests_is_rejected(env, capsys):
    matches, path = accept_first(env)

    def move(doc):
        doc['frames'][0]['origin_xy_um'] = [doc['frames'][0]['origin_xy_um'][0]-0.1, doc['frames'][0]['origin_xy_um'][1]-0.1]
    edit_numeric_manifests(env, move)
    assert_exit2_with(env, matches, path, 'confirm_source_changed_since_candidates', capsys)


def test_manifest_bytes_changed_is_rejected_even_if_content_hash_is_equal(env, capsys):
    matches, path = accept_first(env)
    manifest = env['config'].parent/'reference'/'manifest.json'
    manifest.write_text(json.dumps(load(manifest), indent=4), encoding='utf-8')
    assert_exit2_with(env, matches, path, 'confirm_source_changed_since_candidates', capsys)


def test_export_changed_after_candidates_is_rejected_for_query_and_index(env, capsys):
    for export in ('reference_export', 'target_index_export'):
        file = env['cand']/export/'review_rois.json'
        original = file.read_text(encoding='utf-8')
        file.write_text(original.replace('"status"', '"status" ', 1), encoding='utf-8')
        matches, path = accept_first(env)  # the DL2 hashes follow the changed bytes, so only the candidates record can object
        assert_exit2_with(env, matches, path, 'confirm_export_changed_since_candidates', capsys)
        file.write_text(original, encoding='utf-8')


def test_export_frame_descriptor_changed_is_rejected(env, capsys):
    file = env['cand']/'target_index_export'/'review_rois.json'
    payload = load(file)
    for row in payload['rois']:
        if row.get('frame'):
            row['frame']['origin_xy_um'] = [row['frame']['origin_xy_um'][0]+0.1, row['frame']['origin_xy_um'][1]]
    file.write_text(json.dumps(payload), encoding='utf-8')
    resync_candidates(env)
    matches, path = accept_first(env)
    assert_exit2_with(env, matches, path, 'confirm_frame_changed_since_candidates', capsys)


def test_duplicate_roi_id_in_export_is_rejected(env, capsys):
    file = env['cand']/'target_index_export'/'review_rois.json'
    payload = load(file)
    twin = json.loads(json.dumps(payload['rois'][0]))
    twin['center_xy_um'] = [twin['center_xy_um'][0]+0.1, twin['center_xy_um'][1]]
    payload['rois'].append(twin)
    file.write_text(json.dumps(payload), encoding='utf-8')
    resync_candidates(env)
    matches, path = accept_first(env)
    assert_exit2_with(env, matches, path, 'duplicate_roi_id_in_export', capsys)
    assert_exit2_with_list = main(['confirm-pairs', '--list', '--config', str(env['config']), '--candidates', str(env['cand']),
                                   '--match', f'reference_to_target={matches}'])
    assert assert_exit2_with_list == 2


def test_rows_by_id_never_overwrites():
    problems = confirm_pairs._Problems()
    rows = confirm_pairs._rows_by_id({'rois': [{'roi_id': 'x', 'v': 1}, {'roi_id': 'x', 'v': 2}]}, problems, 'e')
    assert rows == {'x': {'roi_id': 'x', 'v': 1}} and 'duplicate_roi_id_in_export' in problems.items[0]


# B2: mutual merge needs compatible measurement contexts --------------------------------------------------------------------

def test_reciprocal_pairs_with_different_failure_type_are_both_kept(env):
    ref = query_rows(env, 'reference_to_target')
    tgt = query_rows(env, 'target_to_reference')
    qr = next(r for r in ref if r['failure_type'] == 'lateral_short')
    qt = next(r for r in tgt if r['failure_type'] == 'vertical_short')
    matches = {'reference_to_target': fake_match(env, 'reference_to_target', per_roi={qr['roi_id']: [tuple(qt['center_xy_um'])]*3}),
               'target_to_reference': fake_match(env, 'target_to_reference', per_roi={qt['roi_id']: [tuple(qr['center_xy_um'])]*3})}
    path = decisions(env, [[candidate_id('reference_to_target', qr['roi_id'], 1), 'accept', ''],
                           [candidate_id('target_to_reference', qt['roi_id'], 1), 'accept', '']])
    assert run(env, matches, path, tolerance=0) == 0
    out = env['tmp']/'out'
    assert len(pairs_of(out)) == 2 and all(p['status'] == 'ready' for p in pairs_of(out))
    assert load(out/'confirm_funnel.json')['totals']['counts']['merged_away'] == 0
    sidecar = load(out/'confirmed_pairs_sidecar.json')
    assert sidecar['merged_away'] == {}
    assert all(r['mutual_status'] == 'mutual' and r['mutual_context_differs'] is True for r in sidecar['pairs'].values())


def test_same_context_still_merges_and_unit_layer_difference_blocks_merge(env):
    matches, path = mutual_env(env, 0.3)
    assert run(env, matches, path) == 0
    sidecar = load(env['tmp']/'out'/'confirmed_pairs_sidecar.json')
    assert all(r['mutual_context_differs'] is False for r in sidecar['pairs'].values()) and len(sidecar['merged_away']) == 1

    def entry(direction, layer, pid):
        centre = [1.0, 1.0]
        item = {'direction': direction, 'query_center_xy_um': centre, 'center_xy_um': centre,
                'query_frame_id': 'FA' if direction == 'reference_to_target' else 'FB',
                'frame_id': 'FB' if direction == 'reference_to_target' else 'FA'}
        side = {'analysis_layer': layer, 'failure_type': None}
        return item, {'pair_id': pid, 'status': 'ready', 'a': dict(side), 'b': dict(side)}
    near, merged, differs = confirm_pairs._mutual([entry('reference_to_target', 'M1', 'p1'), entry('target_to_reference', 'M2', 'p2')], 0)
    assert merged == {} and differs == {'p1', 'p2'} and near == {'p1': ['p2'], 'p2': ['p1']}
    near, merged, differs = confirm_pairs._mutual([entry('reference_to_target', 'M1', 'p1'), entry('target_to_reference', 'M1', 'p2')], 0)
    assert merged == {'p2': 'p1'} and differs == set()


def test_match_files_inconsistent_with_match_inputs_is_rejected(env, capsys):
    matches, path = accept_first(env)
    cand = env['cand']/'candidate_queries_v1.json'
    original = cand.read_text(encoding='utf-8')
    payload = load(cand)
    payload['match_files']['reference_to_target']['query'] = 'missing/review_rois.json'
    cand.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    assert_exit2_with(env, matches, path, 'candidates_match_files_inconsistent', capsys)
    payload = load(cand)
    del payload['match_files']['target_to_reference']                # direction missing
    payload['match_files']['reference_to_target'] = json.loads(original)['match_files']['reference_to_target']
    cand.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    assert_exit2_with(env, matches, path, 'candidates_match_files_inconsistent', capsys)


def test_missing_match_files_field_stays_accepted(env):
    matches, path = accept_first(env)
    cand = env['cand']/'candidate_queries_v1.json'
    payload = load(cand)
    assert 'match_files' in payload and payload['match_files']
    del payload['match_files']
    cand.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    assert run(env, {'reference_to_target': matches}, path) == 0


# compare-multiproduct/T1: byte-identity golden of the output without --models (digests taken from the 1f48e0d code) ----

GOLDEN_BASELINE = {
    'confirm_funnel.csv': '1842855e37fe79c898760a9520fae2b76c35f2d7ce2ede34c9fcb2866128ae4e',
    'confirm_funnel.json': '0c0e8c8a76326407f89b1ea33d240911df4fba5b588b13ffef075893fbb2d58d',
    'confirmed_pairs_sidecar.json': '1d005b104d5a9918000aa11c1450e3c43e78fe920b92fad09e5be5182bd03672',
    'execution.json': '3de1dd7de82bf8bb93177bc7462b18b9f4c5eb846793adaf65ee03fa19385b9d',
    'roi_correspondence.json': 'b0707b3f5b4a4845e0b10b6450406233bd934a6ae48948fa437bcafce844ed83',
}


def _normalised_output_digests(env, out):
    """sha256 per output file with the variable fields (labelled_at, producer commit, the temp path) removed."""
    import re

    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in ('labelled_at', 'commit')}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    digests = {}
    for path in sorted(Path(out).iterdir()):
        text = path.read_bytes().decode('utf-8')
        text = text.replace(str(env['tmp']), '<TMP>')
        if path.name == 'confirmed_pairs_sidecar.json':   # input hashes cover exports that embed the temp path
            text = re.sub(r'[0-9a-f]{64}', 'H', text)
        if path.suffix == '.json':
            text = json.dumps(clean(json.loads(text)), sort_keys=True, ensure_ascii=False)
        else:
            text = re.sub(r'\r\n', '\n', text)
        digests[path.name] = hashlib.sha256(text.encode('utf-8')).hexdigest()
    return digests


def test_without_models_outputs_are_byte_identical_to_the_baseline(env):
    matches, path = mutual_env(env, 0.3)
    assert run(env, matches, path, tolerance=0.5) == 0
    digests = _normalised_output_digests(env, env['tmp']/'out')
    assert not (env['tmp']/'out'/'models').exists()
    assert 'models' not in load(env['tmp']/'out'/'confirm_funnel.json')
    assert digests == GOLDEN_BASELINE, json.dumps(digests, indent=1)
