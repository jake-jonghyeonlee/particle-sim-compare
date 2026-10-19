"""PLAN_v4 T1: simcompare candidates (regions of both products -> merged queries -> exports + funnel)."""
import json
from pathlib import Path

import pytest
import yaml

from simcompare.candidates import RegionInfo, merge_regions, window_overlap_ratio
from simcompare.cli import main
from test_real_roi_export import build, sha


def demo(tmp_path):
    out = tmp_path/'demo'
    assert main(['demo-data', '--out', str(out)]) == 0
    return out


def edit_config(demo_dir, change, name='edited.yaml'):
    cfg = yaml.safe_load((demo_dir/'config.yaml').read_text())
    change(cfg)
    path = demo_dir/name
    path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
    return path


def run(config, out, *, k=4, window=4, overlap=0.5, extra=()):
    return main(['candidates', '--config', str(config), '--min-count', str(k), '--window-um', str(window),
                 '--merge-overlap', str(overlap), '--out', str(out), *extra])


def load(out, name='candidate_queries_v1.json'):
    return json.loads((Path(out)/name).read_text(encoding='utf-8'))


def queries(payload, direction):
    return next(d for d in payload['directions'] if d['direction'] == direction)['queries']


def stages(payload, direction):
    funnel = next(f for f in payload['funnel']['directions'] if f['direction'] == direction)
    return {s['stage']: s for s in funnel['stages']}


def info(region_id, cx, cy=0.0, *, count=4, area=1.0, group=('P', 'D', 'F'), layer='M2', bbox=(-100, -100, 100, 100)):
    return RegionInfo(region_id, group, (cx, cy), count, area, layer, bbox)


# (a) synthetic numeric_v2, two products -------------------------------------------------

def test_synthetic_two_products_exports_and_consistent_funnel(tmp_path, capsys):
    config = demo(tmp_path)/'config.yaml'
    out = tmp_path/'out'
    code = run(config, out, k=4, window=4, overlap=0.5)
    assert code == 3  # index_union_partial (see test_index_union_partial)
    payload = load(out)
    assert payload['schema_version'] == 'candidate_queries_v1'
    for side in ('reference', 'target'):
        assert (out/f'{side}_export'/'review_rois.json').is_file()
        assert payload['inputs'][side]['export_sha256']
    for direction in ('reference_to_target', 'target_to_reference'):
        s = stages(payload, direction)
        assert s['regions']['count'] >= s['merged_queries']['count'] >= s['after_max_candidates']['count'] >= s['exported_ready']['count']
        assert s['merged_queries']['dropped'] == s['regions']['count']-s['merged_queries']['count']
        assert s['merged_queries']['reasons'] == ({'merged_overlap': s['merged_queries']['dropped']} if s['merged_queries']['dropped'] else {})
        assert s['exported_ready']['dropped'] == s['after_max_candidates']['count']-s['exported_ready']['count']
        assert s['exported_ready']['count'] == len(queries(payload, direction)) > 0
    # the reference demo has two K=4 regions (centres 101.5 and 103.5); 4 um windows overlap by exactly 0.5 and merge
    assert stages(payload, 'reference_to_target')['regions']['count'] == 2
    assert stages(payload, 'reference_to_target')['merged_queries']['count'] == 1
    merged = [q for q in queries(payload, 'reference_to_target') if len(q['merged_from']) > 1]
    assert len(merged) == 1 and merged[0]['region_id'] in merged[0]['merged_from']
    # exports carry only the representatives
    exported = {r['source_region_id'] for r in load(out/'reference_export', 'review_rois.json')['rois']}
    assert exported == {q['region_id'] for q in queries(payload, 'reference_to_target')}
    assert (out/'funnel.csv').read_text(encoding='utf-8-sig').splitlines()[0] == 'direction,stage,count,dropped,reasons'
    assert json.loads((out/'execution.json').read_text())['exit_code'] == 3
    assert payload['inputs']['config_sha256'] and payload['inputs']['reference']['manifest_sha256']
    assert payload['args'] == {'min_count': 4, 'window_um': 4, 'merge_overlap': 0.5, 'max_candidates': None,
                               'representative_diameter_um': 5}


def test_complete_run_exits_zero(tmp_path):
    config = demo(tmp_path)/'config.yaml'
    out = tmp_path/'out'
    assert run(config, out, k=1) == 0
    payload = load(out)
    assert payload['status'] == 'available' and payload['warnings'] == []
    assert all(q['status'] == 'ready' and q['reasons'] == [] for d in payload['directions'] for q in d['queries'])


def test_blocked_query_reasons_in_funnel(tmp_path):
    demo_dir = demo(tmp_path)
    config = edit_config(demo_dir, lambda c: c['reference'].pop('asset_manifest'))
    out = tmp_path/'out'
    assert run(config, out, k=4) == 3
    payload = load(out)
    s = stages(payload, 'reference_to_target')['exported_ready']
    assert s['count'] == 0 and s['dropped'] > 0
    assert 'source_asset_manifest_required' in s['reasons']
    assert all(q['status'] == 'blocked' for q in queries(payload, 'reference_to_target'))
    assert stages(payload, 'target_to_reference')['exported_ready']['count'] > 0


# (b) real_hotspot_v1 route ----------------------------------------------------------------

def real_config(tmp_path, *, record=True):
    sections = {}
    for name in ('reference', 'target'):
        base = tmp_path/name
        base.mkdir()
        data, assignment = build(base)
        spec = {'data_dir': str(data), 'product_id': 'PRODUCT', 'condition_assignment': assignment,
                'asset_manifest': {'path': 'review_assets.json', 'sha256': sha(data/'review_assets.json')}}
        if record:
            spec['electrode_import_record'] = {'path': 'electrode_asset_import_record.json',
                                               'sha256': sha(data/'electrode_asset_import_record.json')}
        sections[name] = spec
    cfg = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'unused', 'map': {'diameter_um': 1},
           **sections}
    path = tmp_path/'real.yaml'
    path.write_text(yaml.safe_dump(cfg))
    return path


def test_real_route_two_products(tmp_path):
    config = real_config(tmp_path)
    out = tmp_path/'out'
    code = run(config, out, k=1, window=10, overlap=0.5)
    payload = load(out)
    assert code == 0, (payload['warnings'], [q['reasons'] for d in payload['directions'] for q in d['queries']])
    for direction in ('reference_to_target', 'target_to_reference'):
        s = stages(payload, direction)
        assert s['regions']['count'] >= s['merged_queries']['count'] >= s['exported_ready']['count'] >= 1
    assert payload['inputs']['reference']['source_kind'] == 'real_hotspot_v1'
    assert payload['inputs']['reference']['electrode_import_record']['sha256']
    rows = load(out/'reference_export', 'review_rois.json')['rois']
    assert rows and all(r['status'] == 'ready' for r in rows)
    assert all(e['asset']['value_semantics'] == 'binary_zero_is_marked' for r in rows for e in r['source_assets']['layouts'])


def test_real_route_without_evidence_is_blocked_not_exported(tmp_path):
    config = real_config(tmp_path, record=False)
    out = tmp_path/'out'
    assert run(config, out, k=1, window=10, overlap=0.5) == 3
    payload = load(out)
    assert not (out/'reference_export').exists()
    s = stages(payload, 'reference_to_target')['exported_ready']
    assert s['count'] == 0 and s['reasons'] == {'electrode_polarity_unknown': s['dropped'],
                                                'source_layout_assets_unconfirmed': s['dropped']}


def test_mixed_real_and_numeric_is_input_error(tmp_path, capsys):
    demo_dir = demo(tmp_path)
    cfg = yaml.safe_load(real_config(tmp_path).read_text())
    mixed = edit_config(demo_dir, lambda c: c.update(target=cfg['target'], mode='real'))
    out = tmp_path/'out'
    assert run(mixed, out) == 2 and not out.exists()


# (c) merge ---------------------------------------------------------------------------------

def test_overlap_ratio_is_intersection_over_window_area():
    assert window_overlap_ratio((0, 0), (0, 0), 10) == 1.0
    assert window_overlap_ratio((0, 0), (5, 0), 10) == 0.5
    assert window_overlap_ratio((0, 0), (5, 5), 10) == 0.25
    assert window_overlap_ratio((0, 0), (10, 0), 10) == 0.0


def test_merge_picks_highest_max_count_and_keeps_far_region():
    near_low, near_high, far = info('r1', 0, count=3), info('r2', 4, count=7), info('r3', 100, count=9)
    merged = merge_regions([near_low, near_high, far], 10, 0.5)
    assert [m['representative'].region_id for m in merged] == ['r3', 'r2']
    group = next(m for m in merged if m['representative'].region_id == 'r2')
    assert [r.region_id for r in group['members']] == ['r1', 'r2']


def test_merge_ratio_one_vs_lower_changes_count():
    regions = [info('r1', 0), info('r2', 4)]
    assert len(merge_regions(regions, 10, 1.0)) == 2
    assert len(merge_regions(regions, 10, 0.6)) == 1
    assert len(merge_regions(regions, 10, 0.7)) == 2  # ratio is 0.6; a stricter bound must not merge
    assert len(merge_regions(regions, 10, 0.61)) == 2


def test_merge_is_transitive_over_a_chain():
    # r1~r2 and r2~r3 overlap, r1 and r3 do not
    regions = [info('r1', 0), info('r2', 6), info('r3', 12)]
    assert window_overlap_ratio((0, 0), (12, 0), 10) == 0.0
    merged = merge_regions(regions, 10, 0.3)
    assert len(merged) == 1 and [r.region_id for r in merged[0]['members']] == ['r1', 'r2', 'r3']


def test_merge_tie_break_and_input_order_independence():
    # equal max_count -> larger area -> smaller region_id
    a, b = info('rb', 0, area=2.0), info('ra', 1, area=3.0)
    assert merge_regions([a, b], 10, 0.5)[0]['representative'].region_id == 'ra'
    c, d = info('rb', 0, area=3.0), info('ra', 1, area=3.0)
    assert merge_regions([c, d], 10, 0.5)[0]['representative'].region_id == 'ra'
    import itertools
    regions = [info('r1', 0), info('r2', 3, count=5), info('r3', 6), info('r4', 50)]
    results = {json.dumps([[m['representative'].region_id, [r.region_id for r in m['members']]]
                           for m in merge_regions(list(p), 10, 0.5)]) for p in itertools.permutations(regions)}
    assert len(results) == 1


def test_merge_never_crosses_product_or_frame_groups():
    regions = [info('r1', 0, group=('P', 'D', 'F1')), info('r2', 0, group=('P', 'D', 'F2'))]
    assert len(merge_regions(regions, 10, 1.0)) == 2


def test_cli_merge_ratio_changes_query_count(tmp_path):
    config = demo(tmp_path)/'config.yaml'
    counts = {}
    for overlap in (1.0, 0.5):
        out = tmp_path/f'out_{overlap}'
        run(config, out, k=4, window=4, overlap=overlap)
        counts[overlap] = len(queries(load(out), 'reference_to_target'))
    assert counts == {1.0: 2, 0.5: 1}


# (d) --max-candidates ------------------------------------------------------------------------

def test_max_candidates_cut_is_recorded(tmp_path):
    config = demo(tmp_path)/'config.yaml'
    out = tmp_path/'out'
    run(config, out, k=4, window=2, overlap=1.0, extra=('--max-candidates', '1'))
    payload = load(out)
    s = stages(payload, 'reference_to_target')
    assert s['merged_queries']['count'] == 2 and s['after_max_candidates']['count'] == 1
    assert s['after_max_candidates']['dropped'] == 1 and s['after_max_candidates']['reasons'] == {'cut_by_max_candidates': 1}
    assert len(queries(payload, 'reference_to_target')) == 1
    assert payload['funnel']['cut_by_max_candidates'] == sum(
        f['cut_by_max_candidates'] for f in payload['funnel']['directions']) == 1
    assert payload['args']['max_candidates'] == 1
    # the kept one is the highest-ranked (max_count desc, area desc, region_id)
    assert (out/'funnel.csv').read_text(encoding='utf-8-sig').count('cut_by_max_candidates:1') == 1
    # without the argument nothing is cut and nothing is claimed
    plain = tmp_path/'plain'
    run(config, plain, k=4, window=2, overlap=1.0)
    assert load(plain)['funnel']['cut_by_max_candidates'] == 0 and load(plain)['funnel']['max_candidates'] is None


# (e) required args -----------------------------------------------------------------------------

@pytest.mark.parametrize('missing', ['--min-count', '--window-um', '--merge-overlap', '--out', '--config'])
def test_required_arguments_missing_exit_2(tmp_path, missing, capsys):
    config = demo(tmp_path)/'config.yaml'
    full = {'--config': str(config), '--min-count': '4', '--window-um': '2', '--merge-overlap': '0.5',
            '--out': str(tmp_path/'out')}
    argv = ['candidates'] + [x for k, v in full.items() if k != missing for x in (k, v)]
    with pytest.raises(SystemExit) as raised:
        main(argv)
    assert raised.value.code == 2
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('kwargs', [{'k': 0}, {'window': 0}, {'window': -1}, {'overlap': 0}, {'overlap': 1.5},
                                    {'extra': ('--max-candidates', '0')}])
def test_invalid_values_exit_2_and_leave_no_output(tmp_path, kwargs):
    config = demo(tmp_path)/'config.yaml'
    out = tmp_path/'out'
    assert run(config, out, **kwargs) == 2
    assert not out.exists()


# (f) existing output ------------------------------------------------------------------------------

def test_existing_output_dir_is_rejected_and_untouched(tmp_path, capsys):
    config = demo(tmp_path)/'config.yaml'
    out = tmp_path/'out'
    (out/'reference_export').mkdir(parents=True)
    keep = {out/'notes.txt': 'mine', out/'review_rois.json': '{}', out/'reference_export'/'review_rois.json': 'x',
            out/'candidate_queries_v1.json': 'old', out/'execution.json': 'old'}
    for path, text in keep.items():
        path.write_text(text)
    assert run(config, out) == 2
    assert {p: p.read_text() for p in out.rglob('*') if p.is_file()} == keep
    assert 'output_dir_exists' in capsys.readouterr().err


# (g) determinism ------------------------------------------------------------------------------------

def snapshot(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(Path(folder).rglob('*')) if p.is_file()}


def test_rerun_is_byte_identical(tmp_path):
    config = demo(tmp_path)/'config.yaml'
    first, second = tmp_path/'one', tmp_path/'two'
    assert run(config, first) == run(config, second)
    assert snapshot(first) == snapshot(second) and snapshot(first)


# (h) index union ---------------------------------------------------------------------------------------

def test_index_union_partial_is_named_warning_and_exit_3(tmp_path):
    config = demo(tmp_path)/'config.yaml'
    out = tmp_path/'out'
    assert run(config, out, k=4) == 3
    payload = load(out)
    warning = next(w for w in payload['warnings'] if w['code'] == 'index_union_partial' and w['product'] == 'reference')
    assert warning['electrodes_missing'] == warning['electrodes_in_dataset']-warning['electrodes_in_export'] > 0
    assert payload['status'] == 'partial'
    assert 'index_union_partial' in json.loads((out/'execution.json').read_text())['reasons']


# (i) frame-clipped windows ------------------------------------------------------------------------------

def test_window_clipped_by_frame_is_flagged_not_dropped(tmp_path):
    config = demo(tmp_path)/'config.yaml'
    clipped, inside = tmp_path/'clipped', tmp_path/'inside'
    run(config, clipped, k=3, window=2, overlap=1.0)
    run(config, inside, k=3, window=0.5, overlap=1.0)
    flagged = queries(load(clipped), 'reference_to_target')
    assert any('window_clipped' in q['flags'] for q in flagged)
    assert len(flagged) == len(queries(load(inside), 'reference_to_target'))  # kept either way
    assert all(q['flags'] == [] for d in load(inside)['directions'] for q in d['queries'])


# (j) no CA / hotspot values in exports -----------------------------------------------------------------------

def walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_keys(item)


def test_exports_have_no_ca_or_hotspot_values(tmp_path):
    demo_dir = demo(tmp_path)
    out = tmp_path/'out'
    run(demo_dir/'config.yaml', out, k=4)
    for side in ('reference', 'target'):
        export = load(out/f'{side}_export', 'review_rois.json')
        forbidden = {'original_case_ca', 'case_ca_in_region', 'ca_um2', 'hotspot', 'mask', 'records', 'density', 'rate'}
        assert not forbidden & set(walk_keys(export))
        assert export['tensor_input_policy'] == 'original_layout_CNT_VIA_valid_only'
        manifest = json.loads((demo_dir/side/'review_assets.json').read_text())
        allowed = set()
        for entry in manifest['cases']:
            allowed |= {a['asset']['path'] for a in entry['assets']['layouts']} | {entry['assets']['valid']['path'],
                                                                                 entry['assets']['cnt']['path']}
        used = {a['asset']['path'] for r in export['rois'] for a in r['source_assets']['layouts']}
        used |= {r['source_assets'][k]['path'] for r in export['rois'] for k in ('valid', 'cnt')}
        assert used and used <= allowed
    payload = load(out)
    assert not {'original_case_ca', 'case_ca_in_region', 'ca_um2', 'score'} & set(walk_keys(payload))


# failure cleanup ---------------------------------------------------------------------------------------------

def test_failure_removes_only_files_created_by_this_run(tmp_path, monkeypatch, capsys):
    config = demo(tmp_path)/'config.yaml'
    bystander = tmp_path/'bystander.json'
    bystander.write_text('keep')
    import simcompare.roi_export as roi_export
    real_export = roi_export.export_review_rois
    calls = []

    def failing(dataset, root, folder, **kwargs):
        real_export(dataset, root, folder, **kwargs)
        calls.append(folder)
        if len(calls) == 2:
            raise ValueError('boom')
    monkeypatch.setattr(roi_export, 'export_review_rois', failing)
    out = tmp_path/'out'
    assert run(config, out, k=4) == 2
    assert len(calls) == 2 and not out.exists()
    assert bystander.read_text() == 'keep' and 'boom' in capsys.readouterr().err
