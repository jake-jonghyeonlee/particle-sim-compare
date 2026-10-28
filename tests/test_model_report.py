"""compare-multiproduct/T3: model-report (model_report_v1).

The flow is the T1/T2/T2b fixture chain: candidates -> confirm-pairs --models (C configured, D pending) -> compare-windows for the
reference pairs and for model C (with window_cases) -> model-report. DL2 is never run; the envelope is built by hand.
"""
import csv
import json
import os
from pathlib import Path

import pytest

from simcompare.cli import main
from simcompare.model_report import negate_text

from test_confirm_pairs import decisions, env, fake_match, load, query_rows, resync_candidates, sha   # noqa: F401
from test_confirm_pairs_models import demo_dir, rid, run_models, scene, tid
from test_compare_windows_models import rebind_assets, reference_run, windows, zero_masks
import test_compare_windows_models as tw

WINDOW, DIAMETER = 2, 5
CASE_ARGS = ['--case-window-um', '2', '--case-diameter-um', '5']


def write_json(path, payload):
    Path(path).write_text(json.dumps(payload, indent=2), encoding='utf-8')


def share_regions(env):
    """candidates-change gives both directions the same region ids; the plain candidates fixture does not. Make them equal."""
    reference = load(env['cand']/'reference_export'/'review_rois.json')
    target_path = env['cand']/'target_export'/'review_rois.json'
    target = load(target_path)
    for row in target['rois']:
        row['source_region_id'] = reference['rois'][0]['source_region_id']
    write_json(target_path, target)
    resync_candidates(env)


def build(env, accept=(), *, reject=(), mutate=None, edit_batch=None, case=True, run_windows=True, share=False):
    """confirm-pairs --models + the reference and model C compare-windows runs. accept/reject are candidate ids."""
    from test_models_config import model_section, write_models
    if share:
        share_regions(env)
    models, matches = scene(env)
    demo = demo_dir(env)
    if edit_batch is not None:
        path = matches['target_to_reference']
        payload = load(path)
        edit_batch(payload)
        write_json(path, payload)
    if mutate is not None:
        mutate(demo/'models'/'C')
    rebind_assets(demo/'models'/'C')
    models = write_models(demo, {'C': model_section(demo, 'C'), 'D': model_section(demo, 'D')})
    rows = [[cid, 'accept', ''] for cid in accept]+[[cid, 'reject', ''] for cid in reject]
    assert run_models(env, models, matches, rows, out='conf') in (0, 3)
    conf = env['tmp']/'conf'
    ctx = {'cand': env['cand'], 'conf': conf, 'models': models, 'ref': demo/'numeric_outputs', 'C': env['tmp']/'win_C'}
    if run_windows:
        reference_run(env, conf)
        assert windows(env, models, conf, 'C', 'win_C', extra=CASE_ARGS if case else ()) in (0, 3)
    return ctx


def report(env, ctx, out='rep', *, window=WINDOW, diameter=DIAMETER, mapping=None):
    mapping = mapping if mapping is not None else {'reference': ctx['ref'], 'C': ctx['C']}
    args = ['model-report', '--candidates', str(ctx['cand']), '--confirm', str(ctx['conf']), '--diameter-um', str(diameter),
            '--window-um', str(window), '--out', str(env['tmp']/out)]
    for key, folder in mapping.items():
        args += ['--windows', f'{key}={folder}']
    return main(args)


def rows_of(env, out='rep'):
    with (env['tmp']/out/'model_report.csv').open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def cell(rows, roi, model):
    return [r for r in rows if r['roi_id'] == roi and r['model'] == model]


def delta_rows(folder):
    with (Path(folder)/'local_delta_rows.csv').open(encoding='utf-8', newline='') as handle:
        return {r['pair_id']: r for r in csv.DictReader(handle) if r['kind'] == 'window' and r['metric'] == 'union'
                and float(r['side_um']) == WINDOW and float(r['diameter_um']) == DIAMETER}


def refused(env, code, capsys, out='rep', **kwargs):
    capsys.readouterr()
    assert report(env, kwargs.pop('ctx'), out, **kwargs) == 2
    assert code in capsys.readouterr().err
    assert not (env['tmp']/out).exists()
    assert not list(env['tmp'].glob(f'.{out}.staging-*'))


def edit(path, change):
    payload = load(path)
    change(payload)
    write_json(path, payload)


# ---- end to end ---------------------------------------------------------------------------------------------------------------

def test_end_to_end_copies_cells_and_counts(env):
    """Reference pair + C r2, r3 on one ROI; C has no hotspot, so its delta is -4.0 (b - a). Values are the CSV strings."""
    ctx = build(env, accept=[tid(env, 1), tid(env, 2), tid(env, 3)], mutate=zero_masks)
    assert report(env, ctx) == 0
    out = env['tmp']/'rep'
    assert sorted(p.name for p in out.iterdir()) == ['execution.json', 'model_report.csv', 'model_report.html', 'model_report.json']
    assert (out/'model_report.csv').read_bytes().startswith(b'\xef\xbb\xbf')
    rows = rows_of(env)
    roi = query_rows(env, 'target_to_reference')[0]['roi_id']
    assert len({(r['roi_id'], r['model'], r['pair_id']) for r in rows}) == len(rows)
    # 5 target ROIs x (reference, C, D) = 15 cells; the ROI of the envelope has one reference pair and two C pairs
    assert len(rows) == 5*3+1
    reference_pair = cell(rows, roi, 'reference')
    model_pairs = cell(rows, roi, 'C')
    assert len(reference_pair) == 1 and [r['rank'] for r in model_pairs] == ['2', '3']
    for key, folder in (('reference', ctx['ref']), ('C', ctx['C'])):
        source = delta_rows(folder)
        for row in cell(rows, roi, key):
            for name in ('a_value_um2', 'b_value_um2', 'delta_um2', 'delta_status', 'delta_reasons'):
                assert row[name] == source[row['pair_id']][name], (key, name)
    zero_model = [r for r in model_pairs if r['delta_um2'] == '-4.0']
    assert zero_model and all(r['delta_model_minus_target_um2'] == r['delta_um2'] for r in model_pairs)
    assert {r['delta_orientation'] for r in model_pairs} == {'model_minus_target'}
    assert reference_pair[0]['delta_orientation'] == 'target_minus_reference'
    assert reference_pair[0]['model_role'] == 'reference' and reference_pair[0]['asset_status'] == 'config_side'
    assert reference_pair[0]['window_cases_status'] == 'not_applicable' and reference_pair[0]['search_scope'] == 'primary'
    # window_cases counts equal the JSON
    cases = {e['pair_id']: e for e in load(ctx['C']/'window_cases.json')['pairs']}
    for row in model_pairs:
        entry = cases[row['pair_id']]
        assert row['window_cases_status'] == 'available'
        assert [row[f'window_cases_{n}'] for n in ('both', 'target_only', 'model_only', 'unknown')] == [
            str(entry['counts'][n]) for n in ('both', 'target_only', 'model_only', 'unknown')]
    # JSON mirrors the CSV
    payload = load(out/'model_report.json')
    assert payload['schema_version'] == 'model_report_v1' and payload['columns'] == ['reference', 'C', 'D']
    assert len(payload['rows']) == len(rows) and payload['inputs']['candidates candidate_queries_v1.json']['sha256'] == sha(ctx['cand']/'candidate_queries_v1.json')
    assert load(out/'execution.json')['exit_code'] == 0
    # two accepted pairs on one ROI keep both rows in rank order; the other ROIs are single cells
    assert [r['pair_id'] for r in model_pairs] == [r['pair_id'] for r in sorted(model_pairs, key=lambda r: int(r['rank']))]


def test_cell_states_and_asset_status_are_separate(env):
    """D pending: an accepted D candidate is deferred; undecided candidates are not_accepted; ranked-but-empty is no_candidate."""
    ctx = build(env, accept=[tid(env, 2), tid(env, 4)], reject=[tid(env, 1), tid(env, 5)])
    assert report(env, ctx) == 0
    rows = rows_of(env)
    roi = query_rows(env, 'target_to_reference')[0]['roi_id']
    reference, c_cell, d_cell = cell(rows, roi, 'reference')[0], cell(rows, roi, 'C'), cell(rows, roi, 'D')[0]
    assert reference['cell_status'] == 'not_accepted' and reference['n_rejected'] == '2' and reference['pair_id'] == ''
    assert c_cell[0]['cell_status'] == 'ready' and len(c_cell) == 1
    assert d_cell['cell_status'] == 'deferred' and d_cell['asset_status'] == 'assets_pending' and d_cell['n_deferred'] == '1'
    other = query_rows(env, 'target_to_reference')[1]['roi_id']
    assert {r['cell_status'] for r in rows if r['roi_id'] == other} == {'no_search'}


def test_no_candidate_is_not_confused_with_no_search_or_not_accepted(env):
    # a ranking with an empty candidate list for the second ROI -> ranked, zero candidates -> no_candidate
    second = query_rows(env, 'target_to_reference')[1]['roi_id']

    def add_empty(payload):
        payload['rankings'].append({**payload['rankings'][0], 'query_roi_id': second, 'candidates': []})
    ctx = build(env, accept=[tid(env, 2)], edit_batch=add_empty)
    assert report(env, ctx) == 0
    rows = rows_of(env)
    states = {(r['roi_id'], r['model']): r['cell_status'] for r in rows}
    assert {states[(second, m)] for m in ('reference', 'C', 'D')} == {'no_candidate'}
    third = query_rows(env, 'target_to_reference')[2]['roi_id']
    assert {states[(third, m)] for m in ('reference', 'C', 'D')} == {'no_search'}


def test_blocked_and_ready_pairs_of_one_cell(env):
    def outside(payload):
        payload['rankings'][0]['candidates'][2]['center_xy_um'] = [110.0, 201.0]   # C r3 outside the frame -> blocked
    ctx = build(env, accept=[tid(env, 2), tid(env, 3)], edit_batch=outside)
    assert report(env, ctx) == 0
    rows = cell(rows_of(env), query_rows(env, 'target_to_reference')[0]['roi_id'], 'C')
    assert [(r['rank'], r['pair_status']) for r in rows] == [('2', 'ready'), ('3', 'blocked')]
    assert rows[0]['cell_status'] == 'ready' and rows[1]['delta_um2'] == ''
    assert rows[1]['window_cases_status'] == 'pair_blocked'


def test_reference_to_target_pair_joins_the_roi_by_region_and_marks_a_moved_centre(env):
    def move(payload):
        payload['rankings'][1]['candidates'][0]['center_xy_um'] = [100.5, 201.5]   # the reference query's candidate in the target
    ctx = build(env, accept=[rid(env, 1)], share=True, edit_batch=move)
    assert report(env, ctx) == 0
    rows = rows_of(env)
    linked = [r for r in rows if r['model'] == 'reference' and r['pair_id']]
    assert len(linked) == 1 and linked[0]['roi_linked'] == 'True' and linked[0]['direction'] == 'reference_to_target'
    assert linked[0]['center_differs_from_query'] == 'True' and json.loads(linked[0]['target_center_xy_um']) == [100.5, 201.5]
    target_rois = {r['roi_id'] for r in query_rows(env, 'target_to_reference') for _ in [0]}
    assert linked[0]['roi_id'] in target_rois
    assert not [r for r in rows if r['roi_linked'] == 'False']


def test_unlinked_reference_pair_is_kept_as_roi_missing_row(env):
    ctx = build(env, accept=[rid(env, 1)])   # candidates fixture: the two directions have different region ids
    assert report(env, ctx) == 0
    rows = rows_of(env)
    (lost,) = [r for r in rows if r['roi_linked'] == 'False']
    assert lost['roi_id'] == '' and lost['model'] == 'reference' and lost['pair_id'] and lost['unlinked_reason'] == 'no_target_roi_for_region'
    assert 'ROI 없음' in (env['tmp']/'rep'/'model_report.html').read_text(encoding='utf-8')
    assert load(env['tmp']/'rep'/'model_report.json')['summary']['reference']['n_pairs_not_linked'] == 1


def test_reference_pairs_of_both_directions_on_one_roi_are_two_rows_unless_merged(env):
    ctx = build(env, accept=[tid(env, 1), rid(env, 1)], share=True)
    assert report(env, ctx) == 0
    pairs = load(ctx['conf']/'roi_correspondence.json')['pairs']
    reference_rows = [r for r in rows_of(env) if r['model'] == 'reference' and r['pair_id']]
    assert len(reference_rows) == len(pairs) and {r['pair_id'] for r in reference_rows} == {p['pair_id'] for p in pairs}


# ---- the sign rule --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('text,expected', [('1.2500', '-1.2500'), ('-0.5', '0.5'), ('0', '0'), ('0.0', '0.0'), ('-0.0', '0.0'),
                                           ('null', 'null'), ('', ''), ('+2', '-2'), ('1e-05', '-1e-05')])
def test_negate_text_changes_only_the_sign_character(text, expected):
    assert negate_text(text) == expected


def test_negate_text_refuses_non_numbers():
    with pytest.raises(Exception):
        negate_text('abc')


def test_reference_pair_unified_column_flips_the_copied_string(env):
    ctx = build(env, accept=[tid(env, 1)])
    path = ctx['ref']/'local_delta_rows.csv'
    lines = path.read_text(encoding='utf-8').splitlines()
    header = lines[0].split(',')
    changed = []
    for line in lines:
        cells = next(csv.reader([line]))
        if cells[3] == 'window' and cells[5] == 'union' and float(cells[2]) == DIAMETER and cells[0] != 'pair_id':
            cells[header.index('delta_um2')], cells[header.index('delta_status')] = '1.2500', 'available'
            line = ','.join('"%s"' % c.replace('"', '""') if ',' in c or '"' in c else c for c in cells)
        changed.append(line)
    path.write_text('\n'.join(changed)+'\n', encoding='utf-8')
    assert report(env, ctx) == 0
    (row,) = [r for r in rows_of(env) if r['model'] == 'reference' and r['pair_id']]
    assert row['delta_um2'] == '1.2500' and row['delta_model_minus_target_um2'] == '-1.2500'
    reported = [r for r in load(env['tmp']/'rep'/'model_report.json')['rows'] if r['pair_id'] == row['pair_id']][0]
    assert reported['delta_um2'] == '1.2500' and reported['delta_model_minus_target_um2'] == '-1.2500'


def test_null_delta_stays_null_not_zero(env):
    ctx = build(env, accept=[tid(env, 1), tid(env, 3)])
    assert report(env, ctx) == 0
    nulls = [r for r in rows_of(env) if r['delta_um2'] == 'null']
    assert nulls and all(r['delta_model_minus_target_um2'] == 'null' for r in nulls)
    reported = [r for r in load(env['tmp']/'rep'/'model_report.json')['rows'] if r['pair_id']]
    assert all(r['delta_model_minus_target_um2'] is None for r in reported if r['delta_um2'] is None)


# ---- window_cases display rules --------------------------------------------------------------------------------------------------

def test_models_without_window_cases_show_not_computed(env):
    ctx = build(env, accept=[tid(env, 2)], case=False)
    assert report(env, ctx) == 0
    (row,) = [r for r in rows_of(env) if r['model'] == 'C' and r['pair_id']]
    assert row['window_cases_status'] == 'not_computed' and row['window_cases_both'] == ''


def test_failed_status_and_empty_side_hide_the_counts(env):
    ctx = build(env, accept=[tid(env, 2), tid(env, 3)])
    pair_ids = [e['pair_id'] for e in load(ctx['C']/'window_cases.json')['pairs']]

    def change(payload):
        first, second = sorted(payload['pairs'], key=lambda e: e['pair_id'])
        first['status'] = 'case_limit_exceeded'
        second['n_candidates'] = {'a': 0, 'b': 3}
    edit(ctx['C']/'window_cases.json', change)
    assert report(env, ctx) == 0
    by_pair = {r['pair_id']: r for r in rows_of(env) if r['model'] == 'C' and r['pair_id']}
    first, second = sorted(pair_ids)
    assert by_pair[first]['window_cases_status'] == 'case_limit_exceeded' and by_pair[first]['window_cases_both'] == ''
    assert by_pair[second]['window_cases_status'] == 'no_candidates_a' and by_pair[second]['window_cases_unknown'] == ''


def test_window_cases_window_must_match_the_report_window(env, monkeypatch, capsys):
    monkeypatch.setattr(tw, 'SIZES', ['2', '4'])
    ctx = build(env, accept=[tid(env, 2)])
    refused(env, 'model_report_window_cases_mismatch', capsys, ctx=ctx, window=4)


# ---- input generation and consistency (X2) ----------------------------------------------------------------------------------------

def test_candidates_folder_of_another_generation(env, capsys):
    ctx = build(env, accept=[tid(env, 2)])
    edit(ctx['cand']/'candidate_queries_v1.json', lambda payload: payload.update(note_after_confirm=1))
    refused(env, 'model_report_candidates_mismatch', capsys, ctx=ctx)


def test_previous_generation_windows_folder(env, capsys):
    ctx = build(env, accept=[tid(env, 2)])
    path = ctx['conf']/'models'/'C'/'roi_correspondence.json'
    path.write_text(path.read_text(encoding='utf-8')+'\n', encoding='utf-8')   # same pairs, other bytes: the windows run used the old file
    refused(env, 'model_report_correspondence_mismatch', capsys, ctx=ctx)


def test_model_key_given_to_the_wrong_folder(env, capsys):
    ctx = build(env, accept=[tid(env, 2)])
    refused(env, 'model_report_correspondence_mismatch', capsys, ctx=ctx, mapping={'reference': ctx['C'], 'C': ctx['ref']})
    edit(ctx['C']/'local_delta.json', lambda payload: payload['products']['b'].update(product_id='X'))
    refused(env, 'model_report_products_mismatch', capsys, ctx=ctx)


def test_target_inputs_differ(env, capsys):
    ctx = build(env, accept=[tid(env, 2)])
    edit(ctx['C']/'local_delta.json', lambda payload: payload['provenance'].update(reference_input_hash='0'*64))
    refused(env, 'model_report_target_inputs_differ', capsys, ctx=ctx)


def test_pair_set_of_a_windows_folder_must_equal_the_ready_pairs(env, capsys):
    ctx = build(env, accept=[tid(env, 2), tid(env, 3)])
    edit(ctx['C']/'local_delta.json', lambda payload: payload.update(pairs=payload['pairs'][:1]))
    refused(env, 'model_report_pair_set_mismatch', capsys, ctx=ctx)


@pytest.mark.parametrize('how,code', [('missing', 'model_report_input_incomplete'), ('failed', 'model_report_input_failed'),
                                      ('error', 'model_report_input_failed')])
def test_completion_marker(env, capsys, how, code):
    ctx = build(env, accept=[tid(env, 2)])
    folder = ctx['C']
    if how == 'missing':
        (folder/'execution.json').unlink()
    elif how == 'failed':
        edit(folder/'execution.json', lambda payload: payload.update(exit_code=2))
    else:
        write_json(folder/'error.json', {'status': 'unavailable'})
    refused(env, code, capsys, ctx=ctx)


def test_confirm_folder_marker_and_kind(env, capsys):
    ctx = build(env, accept=[tid(env, 2)])
    edit(ctx['conf']/'execution.json', lambda payload: payload.update(exit_code=2))
    refused(env, 'model_report_input_failed', capsys, ctx=ctx)


def test_confirm_folder_without_models_is_refused(env, capsys):
    from test_confirm_pairs import both, decisions as make_decisions, cid, run as run_plain
    matches = both(env)
    assert run_plain(env, matches, make_decisions(env, [[cid(env, 'target_to_reference', 1), 'accept', '']])) in (0, 3)
    reference_run(env, env['tmp']/'out')
    ctx = {'cand': env['cand'], 'conf': env['tmp']/'out', 'ref': env['config'].parent/'numeric_outputs'}
    refused(env, 'model_report_confirm_not_models', capsys, ctx=ctx, mapping={'reference': ctx['ref']})


def test_window_and_diameter_must_be_in_the_local_delta(env, capsys):
    ctx = build(env, accept=[tid(env, 2)])
    refused(env, 'model_report_window_not_available', capsys, ctx=ctx, window=3)
    refused(env, 'model_report_diameter_not_available', capsys, ctx=ctx, diameter=0.5)


def test_windows_argument_errors(env, capsys):
    ctx = build(env, accept=[tid(env, 1), tid(env, 2)])
    refused(env, 'model_report_windows_missing', capsys, ctx=ctx, mapping={'reference': ctx['ref']})   # C has pairs, no folder
    refused(env, 'model_report_unknown_windows_key', capsys, ctx=ctx, mapping={'reference': ctx['ref'], 'C': ctx['C'], 'Z': ctx['C']})
    refused(env, 'model_report_invalid_windows_argument', capsys, ctx=ctx, mapping={})
    refused(env, 'model_report_windows_without_pairs', capsys, ctx=ctx, mapping={'reference': ctx['ref'], 'C': ctx['C'], 'D': ctx['C']})


def test_out_folder_protection(env, capsys):
    ctx = build(env, accept=[tid(env, 2)])
    capsys.readouterr()
    for out in (ctx['C']/'inside', ctx['conf']/'sub', ctx['cand']/'sub'):
        args = ['model-report', '--candidates', str(ctx['cand']), '--confirm', str(ctx['conf']), '--diameter-um', '5', '--window-um', '2',
                '--windows', f'reference={ctx["ref"]}', '--windows', f'C={ctx["C"]}', '--out', str(out)]
        before = sorted(p for p in out.parent.rglob('*')) if out.parent.exists() else []
        assert main(args) == 2
        assert 'output_is_input' in capsys.readouterr().err
        assert not out.exists()
        assert sorted(p for p in out.parent.rglob('*')) == before
    (env['tmp']/'taken').mkdir()
    assert report(env, ctx, 'taken') == 2 and 'output_dir_exists' in capsys.readouterr().err
    assert list((env['tmp']/'taken').iterdir()) == []


# ---- the HTML -------------------------------------------------------------------------------------------------------------------

def test_html_is_self_contained_and_shows_no_search_score(env):
    """An NCC match file carries scores 0.91 / 0.83 / 0.52 in the confirm sidecar; none of them may reach the report."""
    from test_confirm_pairs import both, cid, run as run_plain
    from test_confirm_pairs_models import scene as models_scene
    from test_models_config import model_section, write_models
    models, _ = models_scene(env)
    matches = both(env)
    path = decisions(env, [[cid(env, 'target_to_reference', 1), 'accept', '']])
    args = ['confirm-pairs', '--config', str(env['config']), '--candidates', str(env['cand']), '--models', str(models),
            '--decisions', str(path), '--labelled-by', 'designer1', '--mutual-tolerance-um', '0.5', '--out', str(env['tmp']/'conf')]
    for direction, match in matches.items():
        args += ['--match', f'{direction}={match}']
    assert main(args) in (0, 3)
    assert '0.91' in (env['tmp']/'conf'/'confirmed_pairs_sidecar.json').read_text(encoding='utf-8')
    reference_run(env, env['tmp']/'conf')
    ctx = {'cand': env['cand'], 'conf': env['tmp']/'conf', 'ref': env['config'].parent/'numeric_outputs'}
    assert report(env, ctx, mapping={'reference': ctx['ref']}) == 0
    out = env['tmp']/'rep'
    document = (out/'model_report.html').read_text(encoding='utf-8')
    assert 'http' not in document and '<script' not in document and 'src=' not in document and '<img' not in document
    for name in ('model_report.html', 'model_report.csv', 'model_report.json'):
        text = (out/name).read_text(encoding='utf-8-sig')
        assert not any(value in text for value in ('0.91', '0.83', '0.52')), name
        assert 'score' not in text.replace('score_um2', ''), name
    assert '인과를 뜻하지 않는다' in document and 'reference→target 변경 속성' in document and '<h1>' in document


def test_html_lists_pairs_and_links_the_window_cases_file(env):
    ctx = build(env, accept=[tid(env, 2), tid(env, 3)])
    assert report(env, ctx) == 0
    document = (env['tmp']/'rep'/'model_report.html').read_text(encoding='utf-8')
    for row in rows_of(env):
        if row['pair_id']:
            assert row['pair_id'] in document
    assert str(ctx['C']/'window_cases.json') in document and sha(ctx['conf']/'models'/'C'/'roi_correspondence.json') in document


def test_report_is_deterministic(env):
    ctx = build(env, accept=[tid(env, 1), tid(env, 2)])
    assert report(env, ctx, 'rep1') == 0 and report(env, ctx, 'rep2') == 0
    for name in ('model_report.csv', 'model_report.json'):
        first, second = (env['tmp']/d/name for d in ('rep1', 'rep2'))
        assert first.read_bytes() == second.read_bytes()
