"""change-roi/T7: `candidates-change` writes direct/roi_correspondence.json (+ sidecar) that compare-windows reads as is.

Same synthetic real_hotspot_v1 world as test_change_candidates (OFFSET (7, -4) px at 0.1 um): the planted change has two
contributing cases (AB disappeared, AC appeared), so one selected ROI gives two pairs; both directions give the same pairs.
"""
from dataclasses import replace
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import numpy as np
import pytest

from simcompare import candidates, change_candidates, cnt_align
from simcompare.cli import main
from simcompare.correspondence import load_correspondence, validate_correspondence
from test_change_candidates import (  # noqa: F401  (world is a module-scoped fixture of the other test module)
    CASE_AB, CASE_AC, OFFSET, PITCH, PRODUCTS, build_product, cli_args, digests, load, rect, run_change, sha, world, write_config)

DIRECTIONS = ['reference_to_target', 'target_to_reference']
SOURCE = {'kind': 'template_match', 'method': 'cnt_alignment_v1', 'version': '1'}
FILTERS = {'source_id': 'SOURCE', 'process_step_id': 'STEP', 'exposure_id': 'EXPOSURE', 'unit_id': 'UNIT'}


@pytest.fixture(scope='module')
def run(world, tmp_path_factory):
    out = tmp_path_factory.mktemp('direct_out')/'out'
    code = run_change(world, out, top_k=1)
    return SimpleNamespace(out=out, code=code, corr=load(out, 'direct/roi_correspondence.json'),
                           sidecar=load(out, 'direct/direct_pairs_sidecar.json'), payload=load(out))


def compare_windows(world, correspondence, out, sizes=('10', '20')):
    return main(['compare-windows', '--config', str(world.config), '--correspondence', str(correspondence),
                 '--window-sizes', *sizes, '--out', str(out)])


# --- the file itself ----------------------------------------------------------------------------------------------------

def test_correspondence_is_a_plain_roi_correspondence_v1_for_the_planted_change(run):
    assert run.code == 0
    corr = run.corr
    assert corr['schema_version'] == 'roi_correspondence_v1'
    assert set(corr['producer']) == {'kind', 'version', 'commit', 'created_at'}
    assert corr['producer']['kind'] == 'template_match' and corr['producer']['version'] == '1'
    assert corr['producer']['created_at'] is None and corr['producer']['commit'] == run.payload['producer_commit']
    loaded = load_correspondence(run.out/'direct'/'roi_correspondence.json')     # the real loader accepts it
    assert loaded['warnings'] and {w['warning'] for w in loaded['warnings']} == {'duplicate_centres'}   # two cases, one ROI
    pairs = corr['pairs']
    assert len(pairs) == 2                                                          # (ROI, contributing case); not 4
    assert [p['status'] for p in pairs] == ['ready', 'ready'] and all(p['reasons'] == [] for p in pairs)
    assert all(p['source'] == SOURCE and p['pair_id'].startswith('direct_') and len(p['pair_id']) == 7+16 for p in pairs)
    cases = {}
    for pair in pairs:
        a, b = pair['a'], pair['b']
        assert (a['product_id'], b['product_id']) == (PRODUCTS['reference'], PRODUCTS['target'])
        assert a['analysis_layer'] == b['analysis_layer'] == 'L1' and a['failure_type'] == b['failure_type'] == 'lateral_short'
        assert a['frame_id'] == b['frame_id'] == 'real_hotspot_crop_v1'
        for side in (a, b):
            assert {k: side[k] for k in FILTERS} == FILTERS            # group selection cannot be ambiguous
        assert a['endpoints'] == b['endpoints'] and a['roi_id'] and b['roi_id'] and a['roi_id'] != b['roi_id']
        # b centre = a centre + offset (target coordinates), well inside half a pixel
        want = [a['center_xy_um'][0]+OFFSET[0]*PITCH, a['center_xy_um'][1]+OFFSET[1]*PITCH]
        assert all(abs(g-w) <= PITCH/2 for g, w in zip(b['center_xy_um'], want))
        cases[tuple(e['electrode_id'] for e in a['endpoints'])] = pair['pair_id']
    assert sorted(cases) == [('A', 'B'), ('A', 'C')]


def test_roi_ids_point_at_the_query_export_rows_of_the_same_centre_and_case(run):
    exports = {side: {r['roi_id']: r for r in load(run.out/f'{side}_export', 'review_rois.json')['rois']}
               for side in ('reference', 'target')}
    for pair in run.corr['pairs']:
        for key, side in (('a', 'reference'), ('b', 'target')):
            row = exports[side][pair[key]['roi_id']]
            assert row['center_xy_um'] == pair[key]['center_xy_um'] and row['status'] == 'ready'
            assert row['endpoints'] == pair[key]['endpoints']


def test_sidecar_holds_the_uncertainty_the_pairs_must_not_carry(run):
    sidecar, corr = run.sidecar, run.corr
    assert sidecar['schema_version'] == 'direct_pairs_sidecar_v1' and sidecar['limitations'] == [
        'compare-windows does not verify these input hashes']
    assert list(sidecar['pairs']) == [p['pair_id'] for p in corr['pairs']]
    assert sidecar['alignment']['status'] == 'aligned' and sidecar['alignment']['offset_xy_px'] == list(OFFSET)
    assert set(sidecar['inputs']) == {'config_sha256', 'reference', 'target', 'cnt'}
    assert sidecar['inputs']['config_sha256'] and sidecar['inputs']['cnt']['reference']['files_sha256']
    kinds = {}
    for pair_id, item in sidecar['pairs'].items():
        assert item['directions'] == DIRECTIONS                 # the same physical pair arrives from both directions: one entry
        assert item['offset_xy_um'] == [round(OFFSET[0]*PITCH, 6), round(OFFSET[1]*PITCH, 6)]
        assert item['alignment_status'] == 'aligned' and item['alignment_reasons'] == [] and item['status'] == 'ready'
        assert item['score_um2'] == 0 and item['score_basis'] == 'union_change'    # union unchanged, pair changed
        assert item['sum_residual_um2'] > 0 and item['changed_case_count'] == 2
        assert item['score_layer'] == 'L1' and item['observed_fraction'] == 1.0
        assert {'union_area_reference_um2', 'union_area_target_um2', 'union_delta_um2'} <= set(item)
        assert {'iou_global', 'iou_local', 'local_shift_um', 'flags', 'residual_um2', 'change_kind'} <= set(item)
        kinds[item['change_kind']] = pair_id
    assert sorted(kinds) == ['appeared', 'disappeared']


def test_summary_funnel_and_execution_list_the_direct_files(run):
    summary = {'correspondence': 'direct/roi_correspondence.json', 'sidecar': 'direct/direct_pairs_sidecar.json',
               'pairs_ready': 2, 'pairs_blocked': 0}
    assert run.payload['direct'] == summary
    assert run.payload['funnel']['direct_pairs_ready'] == 2 and run.payload['funnel']['direct_pairs_blocked'] == 0
    execution = load(run.out, 'execution.json')
    assert execution['direct'] == summary
    assert {'direct/roi_correspondence.json', 'direct/direct_pairs_sidecar.json'} <= set(execution['outputs'])
    assert sorted(execution['outputs'] + ['execution.json']) == sorted(digests(run.out))


def test_two_runs_give_identical_direct_bytes(world, tmp_path, run):
    again = tmp_path/'again'
    assert run_change(world, again, top_k=1) == run.code
    for name in ('roi_correspondence.json', 'direct_pairs_sidecar.json'):
        assert sha(again/'direct'/name) == sha(run.out/'direct'/name)
    text = (run.out/'direct'/'roi_correspondence.json').read_text(encoding='utf-8')
    assert str(run.out) not in text and 'created_at": null' in text


# --- compare-windows reads it unchanged --------------------------------------------------------------------------------

def test_compare_windows_runs_on_the_direct_file_without_any_code_change(world, run, tmp_path):
    windows = tmp_path/'windows'
    assert compare_windows(world, run.out/'direct'/'roi_correspondence.json', windows) in (0, 3)
    delta = load(windows, 'local_delta.json')
    ready = {p['pair_id'] for p in run.corr['pairs'] if p['status'] == 'ready'}
    assert {r['pair_id'] for r in delta['rows']} == ready
    text = json.dumps(delta)
    assert 'ambiguous_source_group' not in text and 'no_source_group' not in text
    five = [r for r in delta['rows'] if r['diameter_um'] == 5.0]       # the only diameter the synthetic hotspots carry
    assert {r['window_size_um'] for r in delta['rows']} == {10.0, 20.0} and len(five) == len(ready) * 2
    small = [r for r in five if r['window_size_um'] == 10.0]
    assert len(small) == len(ready) and all(r['status'] == 'available' for r in small)
    for row in small:                                                   # both sides found their one source group
        assert row['window']['a']['union']['status'] == row['window']['b']['union']['status'] == 'available'
    for row in five:
        if row['window_size_um'] == 20.0:   # a 20 um window is half the 40 x 42 um frame
            # The representative window of the merged region (the one with the smallest centre among the tied windows)
            # lies inside the 40 x 42 um frame, so there is no geometry reason at all on either side.
            assert row['window']['a']['union']['reasons'] == [] and row['window']['b']['union']['reasons'] == []


@pytest.mark.parametrize('only, sign', [('target', 1), ('reference', -1)])
def test_compare_windows_reads_a_one_sided_pair_without_endpoints_and_gives_the_group_union_delta(tmp_path, monkeypatch, only, sign):
    """A combination only one model has: the direct pair has no endpoints / failure_type, so there is no named pair;
    compare-windows (unchanged) finds both source groups and reports the group-union delta (target minus reference)."""
    from test_change_candidates import one_sided_world
    config = one_sided_world(tmp_path, monkeypatch, only)
    out = tmp_path/'out'
    assert main(cli_args(config, out, tmp_path/'dl1', top_k=1)) == 0
    corr_path = out/'direct'/'roi_correspondence.json'
    assert len(load(out, 'direct/roi_correspondence.json')['pairs']) == 1
    windows = tmp_path/'windows'
    code = compare_windows(SimpleNamespace(config=config), corr_path, windows)
    delta = load(windows, 'local_delta.json')
    # C7: the exit code is fixed. 3 comes from incomplete rows only (other diameters / the 20 um window), not from the pair
    assert code == 3 and delta['reasons'] == ['incomplete_local_delta'], (code, delta['reasons'])
    # the cause: the synthetic hotspots carry only the 5 um diameter, so every other diameter row is unavailable
    assert {r['diameter_um'] for r in delta['rows'] if r['status'] != 'available'} and \
        all(r['diameter_um'] != 5.0 for r in delta['rows'] if r['status'] != 'available')
    text = json.dumps(delta)
    assert 'ambiguous_source_group' not in text and 'no_source_group' not in text
    row = next(r for r in delta['rows'] if r['diameter_um'] == 5.0 and r['window_size_um'] == 10.0)
    assert row['named_pair'] is None and row['group_a'] and row['group_b']
    assert row['window']['a']['union']['status'] == row['window']['b']['union']['status'] == 'available'
    value = row['window']['delta']['union']['value']
    assert row['window']['delta']['union']['status'] == 'available' and value*sign > 0, value


# --- blocked and uncertain ---------------------------------------------------------------------------------------------

def test_failed_alignment_blocks_every_direct_pair(world, tmp_path):
    empty_dl1 = tmp_path/'no_cnt'
    empty_dl1.mkdir()
    out = tmp_path/'out'
    assert run_change(world, out, dl1=empty_dl1, top_k=1) == 3
    corr = load(out, 'direct/roi_correspondence.json')
    # Without alignment the layout shift itself is a change; which ROI ranks first (and how many cases it has) follows
    # the union-change score, so only the invariants are asserted.
    # One pair per (selected ROI, contributing case); the same physical pair arrives from both directions as one entry.
    queries = load(out)['directions'][0]['queries']
    n = len(corr['pairs'])
    assert n == sum(len(q['contributing_cases']) for q in queries) >= 1
    assert all(p['status'] == 'blocked' and p['reasons'] == ['alignment_failed'] for p in corr['pairs'])
    payload = load(out)
    assert payload['direct']['pairs_ready'] == 0 and payload['direct']['pairs_blocked'] == n
    warning = next(w for w in payload['warnings'] if w['code'] == 'direct_pairs_blocked')
    assert warning['count'] == n and warning['reasons'] == {'alignment_failed': n}
    windows = tmp_path/'windows'
    assert compare_windows(world, out/'direct'/'roi_correspondence.json', windows) == 3
    delta = load(windows, 'local_delta.json')
    assert 'blocked_pairs_present' in delta['reasons'] and not delta['rows']


def test_uncertain_alignment_keeps_pairs_ready_and_marks_only_the_sidecar(world, tmp_path, monkeypatch):
    real = cnt_align.align_products

    def ambiguous(*args, **kwargs):
        result = real(*args, **kwargs)
        assert result['status'] == 'aligned'
        return {**result, 'status': 'aligned_uncertain', 'reasons': ['peak_ambiguous']}
    monkeypatch.setattr(cnt_align, 'align_products', ambiguous)
    out = tmp_path/'out'
    assert run_change(world, out, top_k=1) == 3
    corr = load(out, 'direct/roi_correspondence.json')
    assert [p['status'] for p in corr['pairs']] == ['ready', 'ready'] and all(p['reasons'] == [] for p in corr['pairs'])
    sidecar = load(out, 'direct/direct_pairs_sidecar.json')
    assert sidecar['alignment']['status'] == 'aligned_uncertain' and sidecar['alignment']['reasons'] == ['peak_ambiguous']
    assert all(item['alignment_status'] == 'aligned_uncertain' and item['alignment_reasons'] == ['peak_ambiguous']
               for item in sidecar['pairs'].values())
    payload = load(out)
    assert payload['status'] == 'partial' and 'alignment_uncertain' in {w['code'] for w in payload['warnings']}
    assert payload['direct']['pairs_ready'] == 2
    windows = tmp_path/'windows'
    assert compare_windows(world, out/'direct'/'roi_correspondence.json', windows) in (0, 3)
    assert len({r['pair_id'] for r in load(windows, 'local_delta.json')['rows']}) == 2


def test_centre_outside_the_target_valid_domain_blocks_only_that_side(world, tmp_path, monkeypatch):
    run_once = tmp_path/'probe'
    assert run_change(world, run_once, top_k=1) == 0
    b_centre = load(run_once, 'direct/roi_correspondence.json')['pairs'][0]['b']['center_xy_um']
    original = candidates.prepare_side

    def with_hole(cfg, name, diameter_um, reserved_bytes=0):
        side = original(cfg, name, diameter_um, reserved_bytes)
        if name == 'target':
            frame = side.dataset.frames[next(iter(side.dataset.frames))]
            valid = np.array(frame.valid, copy=True)
            ix = int(np.floor((b_centre[0]-frame.origin_xy_um[0])/frame.pixel_pitch_um + 1e-9))
            iy = int(np.floor((b_centre[1]-frame.origin_xy_um[1])/frame.pixel_pitch_um + 1e-9))
            valid[iy-1:iy+2, ix-1:ix+2] = False
            side.dataset.frames[frame.frame_id] = replace(frame, valid=valid)
        return side
    monkeypatch.setattr(candidates, 'prepare_side', with_hole)
    out = tmp_path/'out'
    assert run_change(world, out, top_k=1) == 3
    pairs = load(out, 'direct/roi_correspondence.json')['pairs']
    assert pairs and all(p['status'] == 'blocked' and p['reasons'] == ['position_outside_valid_domain_b'] for p in pairs)
    assert load(out)['direct']['pairs_blocked'] == len(pairs)


# --- T9 C5/C6/C8: layer change, absent layer on the opposite model, unresolved group filter ------------------------------

LAYER_CASE = 'vertical_short-A-L1-B-L2'
STACKS = {'reference': [{'actual_layer': 'L1', 'stack_order': 1, 'density_process_layer': 'P1'},
                        {'actual_layer': 'L2', 'stack_order': 2, 'density_process_layer': 'P2'}],
          'target': [{'actual_layer': 'L1', 'stack_order': 2, 'density_process_layer': 'P1'},      # swapped stack order:
                     {'actual_layer': 'L2', 'stack_order': 1, 'density_process_layer': 'P2'}]}     # analysis layer L2 -> L1


def layer_change_world(base, monkeypatch):
    """One vertical short whose analysis layer is L2 in the reference and L1 in the target (the stack order differs).
    Neither model has a case in the other model's layer, so the layer change is two one-sided combinations (L2/L2 only in
    the reference, L1/L1 only in the target; C8) and no union is built between layers."""
    import test_change_candidates as me

    def patched(side, extra_target_case=False):
        # the two hotspots sit at different places so that the two combinations are two separate change windows
        return {LAYER_CASE: rect(100, 100, 20, 40) if side == 'reference' else rect(250, 250, 20, 40)}
    monkeypatch.setattr(me, 'hotspot_masks', patched)
    datas, assignments = {}, {}
    for side in ('reference', 'target'):
        datas[side], assignments[side] = build_product(base, side, layer_stack=STACKS[side], dl1_electrodes=['A', 'B'])
    return write_config(base, datas, assignments, map_spec={'diameter_um': 5})


def test_layer_change_is_two_one_sided_combinations_and_both_direct_pairs_are_blocked_by_the_absent_layer(tmp_path, monkeypatch):
    config = layer_change_world(tmp_path, monkeypatch)
    out = tmp_path/'out'
    assert main(cli_args(config, out, tmp_path/'dl1', top_k=2)) == 3
    payload = load(out)
    stats = payload['change_map']['pairing']
    assert stats['layer_changed_included'] == 2 and stats['one_sided_included_reference'] == stats['one_sided_included_target'] == 1
    pairs = load(out, 'direct/roi_correspondence.json')['pairs']
    assert len(pairs) == 2, [(d['direction'], [(q['score_um2'], q['score_layer'], q['reasons'], q['center_xy_um'],
                                                [c['case_key'] for c in q['contributing_cases']]) for q in d['queries']])
                             for d in payload['directions']]
    by_layer = {p['a']['analysis_layer']: p for p in pairs}
    assert sorted(by_layer) == ['L1', 'L2']
    # reference-only combination (layer L2): the target has no case in L2 -> side b is absent
    assert by_layer['L2']['status'] == 'blocked' and by_layer['L2']['reasons'] == ['one_sided_layer_absent_b']
    assert by_layer['L1']['status'] == 'blocked' and by_layer['L1']['reasons'] == ['one_sided_layer_absent_a']
    assert payload['direct']['pairs_ready'] == 0 and payload['direct']['pairs_blocked'] == 2
    warning = next(w for w in payload['warnings'] if w['code'] == 'direct_pairs_blocked')
    assert warning['reasons'] == {'one_sided_layer_absent_a': 1, 'one_sided_layer_absent_b': 1}
    sidecar = load(out, 'direct/direct_pairs_sidecar.json')['pairs']
    assert {item['layer_changed'] for item in sidecar.values()} == {True} and {item['present_in'] for item in sidecar.values()} == {
        'reference', 'target'}
    windows = tmp_path/'windows'                    # compare-windows refuses blocked pairs; it is not left to analysis_layer_not_in_source_*
    assert compare_windows(SimpleNamespace(config=config), out/'direct'/'roi_correspondence.json', windows) == 3
    delta = load(windows, 'local_delta.json')
    assert 'blocked_pairs_present' in delta['reasons'] and not delta['rows']


def _fake_dataset(*cases):
    return SimpleNamespace(cases=[SimpleNamespace(analysis_layer=layer, identity=SimpleNamespace(source_id='S', process_step_id=step,
                                                                                               exposure_id='E'),
                                                  metadata={'unit_id': 'U'}) for layer, step in cases])


def test_opposite_filter_with_several_values_uses_the_present_cases_value_else_stays_empty_and_is_flagged():
    dataset = _fake_dataset(('L1', 'STEP1'), ('L1', 'STEP2'), ('L2', 'STEP9'))
    present = SimpleNamespace(identity=SimpleNamespace(source_id='S', process_step_id='STEP2', exposure_id='E'), metadata={'unit_id': 'U'})
    filters, unresolved = change_candidates._dataset_filters(dataset, 'L1', present)
    assert filters == {'source_id': 'S', 'process_step_id': 'STEP2', 'exposure_id': 'E', 'unit_id': 'U'} and not unresolved
    other = SimpleNamespace(identity=SimpleNamespace(source_id='S', process_step_id='STEP3', exposure_id='E'), metadata={'unit_id': 'U'})
    for hint in (None, other):                      # not in the opposite set, or no case on the present side: left empty
        filters, unresolved = change_candidates._dataset_filters(dataset, 'L1', hint)
        assert filters == {} and unresolved         # the whole filter is left empty, not field by field
    filters, unresolved = change_candidates._dataset_filters(dataset, 'L2', None)      # a single value needs no hint
    assert filters['process_step_id'] == 'STEP9' and not unresolved


def _grouped_dataset(*groups):
    return SimpleNamespace(cases=[SimpleNamespace(analysis_layer='L1', identity=SimpleNamespace(source_id=s, process_step_id=p,
                                                                                               exposure_id='E'),
                                                  metadata={'unit_id': 'U'}) for s, p in groups])


def test_opposite_filter_takes_a_whole_group_tuple_or_nothing():
    """Groups (S1,P1) and (S2,P2) with the hint (S1,P2): each field value exists in the set, but the tuple does not."""
    dataset = _grouped_dataset(('S1', 'P1'), ('S2', 'P2'))
    mixed = SimpleNamespace(identity=SimpleNamespace(source_id='S1', process_step_id='P2', exposure_id='E'), metadata={'unit_id': 'U'})
    assert change_candidates._dataset_filters(dataset, 'L1', mixed) == ({}, True)
    exact = SimpleNamespace(identity=SimpleNamespace(source_id='S2', process_step_id='P2', exposure_id='E'), metadata={'unit_id': 'U'})
    assert change_candidates._dataset_filters(dataset, 'L1', exact) == (
        {'source_id': 'S2', 'process_step_id': 'P2', 'exposure_id': 'E', 'unit_id': 'U'}, False)


def test_unresolved_filter_state_reaches_the_direct_pair_builder():
    """_direct_side_one_sided hands the unresolved state to _direct_pairs, which writes filter_unresolved_<side> into the flags."""
    item = SimpleNamespace(product_id='P', design_version='D', frame_id='F',
                           side=SimpleNamespace(dataset=_fake_dataset(('L1', 'STEP1'), ('L1', 'STEP2'))), frame=None)
    side, identity, unresolved = change_candidates._direct_side_one_sided(None, 'L1', [1.0, 2.0], {}, item, None)
    assert unresolved and 'process_step_id' not in side and side['analysis_layer'] == 'L1'
