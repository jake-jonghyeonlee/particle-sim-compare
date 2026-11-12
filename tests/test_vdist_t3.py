"""vdist-filter/T3: distance_filter records (02/04/06), 07 input table + input_hash check, stale rule, T1/T2 advisories."""
import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import simcompare.local_delta as local_delta
from simcompare.adapters.real_hotspot import load_real_manifest
from simcompare.cli import main
from simcompare.compare2_report import build_report
from simcompare.local_delta import _as_dataset, _cno_pair, _named_pair, _near_case_ids, _square, compute_local_delta
from simcompare.numeric_schema import Endpoint
import test_change_candidates as tcc
import test_compare2 as tc
from test_compare2_link import real_chain  # noqa: F401  (fixture used by `linked`)
from test_compare2_report import TGT_CASES, linked, linked_report, report  # noqa: F401  (linked is a fixture)
from test_change_candidates import world  # noqa: F401  (fixture used by real_chain)
from test_interlayer_distance import HEAD, PAIRS, VERT
from test_local_delta import make_case2d, pair, rect, row_at
from test_named_pair_layers import vcase

BLOCK_KEYS = {'rule', 'file_name', 'source_sha256', 'table_sha256', 'unit_assumption', 'filtered_records',
              'filtered_cases', 'layer_pair_distances'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---- 02: compare2.json ---------------------------------------------------------------------------------------------------

def test_02_records_the_block_for_the_sides_whose_filter_is_on(tmp_path):
    ref = (tc.base_cases() + [VERT], PAIRS)
    code, out = tc.run(tmp_path, ref, ref, thk=HEAD + 'L1L2\t3\n')
    assert code == 0
    manifests = tc.load(out)['inputs']['manifests']
    for role in ('reference', 'target'):
        block = manifests[role]['distance_filter']
        assert set(block) == BLOCK_KEYS and block['unit_assumption'] == 'um' and block['file_name'] == 'thk_info.txt'
        assert block['source_sha256'] == sha(tmp_path / 'thk_info.txt')
        assert block['layer_pair_distances'] == {'L1+L2': '3'}     # only the pair a vertical case looked up
        assert block['filtered_cases'] == 1 and block['filtered_records'] == 3     # d = 1, 2, 3 of the one vertical case


def test_02_without_the_key_writes_no_block_and_the_rest_is_the_baseline(tmp_path):
    ref, tgt = tc.two_sides()
    code, out = tc.run(tmp_path, ref, tgt)
    assert code == 0
    assert 'distance_filter' not in (out / 'compare2.json').read_text(encoding='utf-8')


def test_02_one_side_on_one_side_off(tmp_path):
    ref = (tc.base_cases() + [VERT], PAIRS)
    tgt = (tc.base_cases(), PAIRS)
    code, out = tc.run(tmp_path, ref, tgt, thk=HEAD + 'L1L2\t3\n')
    assert code == 0
    numeric = tmp_path / 'numeric.yaml'
    cfg = yaml.safe_load(numeric.read_text(encoding='utf-8'))
    del cfg['target']['condition_assignment']['interlayer_distance_path']
    numeric.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    assert main(['compare2', '--config', str(tmp_path / 'compare2.yaml')]) == 0
    manifests = tc.load(out)['inputs']['manifests']
    assert 'distance_filter' in manifests['reference'] and 'distance_filter' not in manifests['target']


# ---- 07: input table, input_hash check, stale rule ------------------------------------------------------------------------

def changed_run(tmp_path, thk):
    return tc.run(tmp_path, (tc.base_cases() + [VERT], PAIRS), (TGT_CASES + [VERT], PAIRS), thk=thk)


def test_07_input_table_line_on_and_off(tmp_path):
    code, out = changed_run(tmp_path, HEAD + 'L1L2\t3\n')
    exit_code, folder = report(tmp_path, out)
    html = (folder / 'report.html').read_text(encoding='utf-8')
    assert '층간 거리 필터' in html and 'A: 켜짐 · thk_info.txt · 필터된 case 1개' in html and 'B: 켜짐' in html
    plain = tmp_path / 'plain'
    plain.mkdir()
    code, out = tc.run(plain, (tc.base_cases(), PAIRS), (TGT_CASES, PAIRS))
    exit_code, folder = report(plain, out)
    html = (folder / 'report.html').read_text(encoding='utf-8')
    assert 'A: 꺼짐' in html and 'B: 꺼짐' in html


def test_07_thk_content_change_is_refused_without_links(tmp_path):
    code, out = changed_run(tmp_path, HEAD + 'L1L2\t3\n')
    (tmp_path / 'thk_info.txt').write_text(HEAD + 'L1L2\t0.5\n', encoding='utf-8')
    exit_code, folder = report(tmp_path, out, name='changed')
    assert exit_code == 2
    error = json.loads((folder / 'error.json').read_text(encoding='utf-8'))
    assert error['code'] == 'compare2_input_hash_mismatch'
    assert 'reference: compare2.json used input_hash' in error['detail'] or 'target: compare2.json used input_hash' in error['detail']
    assert not (folder / 'report.html').exists()
    (tmp_path / 'thk_info.txt').write_text(HEAD + 'L1L2\t3\n', encoding='utf-8')     # the same content again passes
    assert report(tmp_path, out, name='same')[0] in (0, 3)


def test_07_stale_distance_filter_rule_is_refused(tmp_path):
    code, out = changed_run(tmp_path, HEAD + 'L1L2\t3\n')
    path = out / 'compare2.json'
    doc = json.loads(path.read_text(encoding='utf-8'))
    for role in ('reference', 'target'):
        del doc['inputs']['manifests'][role]['distance_filter']['rule']
    path.write_text(json.dumps(doc), encoding='utf-8')
    exit_code, folder = report(tmp_path, out)
    assert exit_code == 2
    assert json.loads((folder / 'error.json').read_text(encoding='utf-8'))['code'] == 'compare2_stale_distance_filter_rule'


def test_07_no_key_and_no_block_is_the_old_behaviour(tmp_path):
    code, out = tc.run(tmp_path, (tc.base_cases(), PAIRS), (TGT_CASES, PAIRS))
    assert report(tmp_path, out)[0] in (0, 3)


# ---- 04 / 06 / 07 on a real chain with the key (CNO layer gives a second layer in the stack) ---------------------------

@pytest.fixture(scope='module')
def keyed(tmp_path_factory):
    base = tmp_path_factory.mktemp('vdist_t3_keyed')
    datas, assignments = {}, {}
    thk = base / 'thk_info.txt'
    thk.write_text(HEAD + 'L1GCNT\t3\n', encoding='utf-8')
    for side in ('reference', 'target'):
        datas[side], assignments[side] = tcc.build_product(base, side, cno='apart')
        assignments[side] = {**assignments[side], 'interlayer_distance_path': 'thk_info.txt'}
    config = tcc.write_config(base, datas, assignments, map_spec={'diameter_um': 5})
    for side in ('reference', 'target'):
        # the electrode asset manifest is bound to the dataset input_hash, which now holds the rule and the table:
        # import the assets again through the keyed config (the thk file sits in its folder), then write the config again
        adapter = base / f'adapter_{side}_keyed'
        assert main(['import-electrode-assets', '--source-kind', 'hotspot_case_electrode', '--root', str(base / f'hotspot_{side}'),
                     '--config', str(config), '--side', side,
                     '--cnt-from-dl1-root', str(base / 'dl1'), '--source-version', 'layout_v1', '--max-asset-memory-mb', '512',
                     '--out', str(adapter)]) == 0
        for name in ('review_assets.json', 'electrode_asset_import_record.json'):
            shutil.copyfile(adapter / name, datas[side] / name)
        for path in (adapter / 'assets').iterdir():
            shutil.copyfile(path, datas[side] / 'assets' / path.name)
    config = tcc.write_config(base, datas, assignments, map_spec={'diameter_um': 5})
    cand = base / 'cand'
    assert main(tcc.cli_args(config, cand, base / 'dl1', top_k=5)) in (0, 3)
    density = base / 'density'
    tc.write_density(density, 'grp', {'P1': {5: 1.0}}, {'P1': {5: 1.0}})
    tc.write_density(density, 'grp2', {'P1': {5: 3.0}}, {'P1': {5: 2.0}})
    group = {'glass_area': {'value': 200.0, 'unit': 'um2'}, 'weight_line': 1.0, 'weight_pixel': 1.0}
    cfg = {'schema_version': 'compare2_config_v1', 'numeric_config': str(config), 'output_dir': str(base / 'c2'),
           'density_dir': str(density), 'model_groups': {'grp': dict(group), 'grp2': dict(group)},
           'lambda_diameters_um': [5], 'report': {'map_window_um': 10, 'diameter_um': 5},
           'products': {'reference': {'model_group': 'grp', 'resolution': tc.RES, 'hotspot_root': str(base / 'hotspot_reference')},
                        'target': {'model_group': 'grp2', 'resolution': tc.RES, 'hotspot_root': str(base / 'hotspot_target')}}}
    compare2_config = base / 'compare2.yaml'
    compare2_config.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    assert main(['compare2', '--config', str(compare2_config)]) in (0, 3)
    windows = base / 'win'
    assert main(['compare-windows', '--config', str(config), '--correspondence', str(cand / 'direct/roi_correspondence.json'),
                 '--window-sizes', '10', '--out', str(windows)]) in (0, 3)
    return SimpleNamespace(base=base, thk=thk, config=config, cand=cand, windows=windows, c2=base / 'c2',
                           compare2_config=compare2_config)


def test_04_and_06_and_02_record_the_same_block(keyed):
    queries = json.loads((keyed.cand / 'candidate_queries_v1.json').read_text(encoding='utf-8'))
    local = json.loads((keyed.windows / 'local_delta.json').read_text(encoding='utf-8'))
    compare2 = json.loads((keyed.c2 / 'compare2.json').read_text(encoding='utf-8'))
    for role in ('reference', 'target'):
        block = queries['inputs'][role]['distance_filter']
        assert set(block) == BLOCK_KEYS and block['file_name'] == 'thk_info.txt'
        assert block['source_sha256'] == sha(keyed.thk) and block['layer_pair_distances'] == {}
        assert local['provenance']['distance_filter'][role] == block
        assert compare2['inputs']['manifests'][role]['distance_filter'] == block


def test_04_and_06_without_the_key_write_no_block(tmp_path):
    base = tmp_path
    datas, assignments = {}, {}
    for side in ('reference', 'target'):
        datas[side], assignments[side] = tcc.build_product(base, side)
    config = tcc.write_config(base, datas, assignments, map_spec={'diameter_um': 5})
    cand = base / 'cand'
    assert main(tcc.cli_args(config, cand, base / 'dl1', top_k=5)) in (0, 3)
    windows = base / 'win'
    assert main(['compare-windows', '--config', str(config), '--correspondence', str(cand / 'direct/roi_correspondence.json'),
                 '--window-sizes', '10', '--out', str(windows)]) in (0, 3)
    for path in list(cand.rglob('*.json')) + list(windows.rglob('*.json')):
        assert 'distance_filter' not in path.read_text(encoding='utf-8'), path


def test_07_with_links_a_changed_input_is_refused_before_anything_is_written(linked, tmp_path, monkeypatch):
    """The linked real chain has no thk key (one layer), so the input_hash is changed through the condition assignment:
    the check is the same one (current input_hash vs compare2.json), reached through the ROI figures."""
    import simcompare.compare2_report as report_module
    opened = []
    original = report_module._scan_side
    monkeypatch.setattr(report_module, '_scan_side', lambda *a, **k: opened.append(1) or original(*a, **k))
    html, record = linked_report(linked, tmp_path / 'ok')
    assert opened and record['roi_figures'], 'the linked report must open the datasets for its maps'
    numeric = Path(yaml.safe_load(linked.config.read_text(encoding='utf-8'))['numeric_config'])
    original_text = numeric.read_text(encoding='utf-8')
    cfg = yaml.safe_load(original_text)
    cfg['reference']['condition_assignment']['unit_id'] = 'UNIT_CHANGED'
    numeric.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    try:
        with pytest.raises(Exception) as caught:
            linked_report(linked, tmp_path / 'bad')
        assert getattr(caught.value, 'code', None) == 'compare2_input_hash_mismatch', caught.value
        assert not (tmp_path / 'bad' / 'out').exists()
    finally:
        numeric.write_text(original_text, encoding='utf-8')


# ---- R1: 06 CLI end to end with a vertical case that the rule drops -------------------------------------------------------

def test_r1_compare_windows_cli_keeps_the_filtered_case_available_with_zero_area(tmp_path):
    ref = (tc.base_cases() + [VERT], PAIRS)
    code, out = tc.run(tmp_path, ref, ref, thk=HEAD + 'L1L2\t3\n')
    assert code == 0
    imported = tmp_path / 'ref' / 'imp'
    frame = load_real_manifest(imported)['frame']
    pitch, (ox, oy), (ny, nx) = frame['pixel_pitch_um'], frame['origin_xy_um'], frame['shape_yx']
    centre = [ox + nx * pitch / 2, oy + ny * pitch / 2]
    side = {'product_id': 'P', 'design_version': 'D', 'frame_id': frame['frame_id'], 'center_xy_um': centre,
            'analysis_layer': 'L2', 'failure_type': 'vertical_short',
            'endpoints': [{'electrode_id': 'X', 'role': 'gate', 'actual_layer': 'L1'},
                          {'electrode_id': 'Y', 'role': 'data', 'actual_layer': 'L2'}]}
    corr = tmp_path / 'corr.json'
    corr.write_text(json.dumps({'schema_version': 'roi_correspondence_v1', 'producer': {'kind': 'manual'}, 'pairs': [
        {'pair_id': 'v1', 'a': side, 'b': side, 'status': 'ready',
         'source': {'kind': 'manual', 'labelled_by': 'test', 'labelled_at': '2026-11-13'}}]}), encoding='utf-8')
    windows = tmp_path / 'win'
    code = main(['compare-windows', '--config', str(tmp_path / 'numeric.yaml'), '--correspondence', str(corr),
                 '--window-sizes', '0.4', '--out', str(windows)])      # the frame is 0.4 um wide: the window is not clipped
    assert code in (0, 3)
    local = json.loads((windows / 'local_delta.json').read_text(encoding='utf-8'))
    assert set(local['provenance']['distance_filter']['reference']) == BLOCK_KEYS
    rows = {row['diameter_um']: row for row in local['rows'] if row['pair_id'] == 'v1'}
    for diameter in (1.0, 2.0, 3.0):      # dropped: area 0, but observed (not missing)
        named = rows[diameter]['named_pair']
        assert named['a']['status'] != 'missing' and named['a']['value'] == 0.0
    assert rows[4.0]['named_pair']['a']['value'] > 0


# ---- A1 / A2: _near_case_ids ----------------------------------------------------------------------------------------------

def near_cases(n):
    return [vcase(f'c{i:02d}', ('L1', 'L2'), rect(range(4), [1])) for i in range(n)]


def test_a1_near_index_is_built_once_per_case_list_and_gives_the_same_ids(monkeypatch):
    cases = near_cases(4)
    bound = SimpleNamespace(product_id='A', design_version='design', frame=cases[0].records[5].frame,
                            endpoints=(Endpoint('E1', 'gate', 'L9'), Endpoint('E2', 'data', 'L9')))
    calls = []
    original = local_delta._electrode_set_key
    monkeypatch.setattr(local_delta, '_electrode_set_key', lambda *a: calls.append(1) or original(*a))
    first = _near_case_ids(cases, bound)
    built = len(calls)
    assert _near_case_ids(cases, bound) == first and len(calls) == built      # cached: no new key computation
    assert first == (['c00', 'c01', 'c02', 'c03'], False)
    other = near_cases(2)
    assert _near_case_ids(other, bound) == (['c00', 'c01'], False) and len(calls) > built    # a new list rebuilds


def test_a2_candidates_are_sorted_capped_and_marked_truncated():
    ordered = [vcase('b', ('L2', 'L1'), rect(range(4), [1])), vcase('a', ('L1', 'L2'), rect(range(4), [1])),
               vcase('c', ('L1', 'L2'), rect(range(4), [2]))]
    bound = SimpleNamespace(product_id='A', design_version='design', frame=ordered[0].records[5].frame,
                            endpoints=(Endpoint('E1', 'gate', 'L9'), Endpoint('E2', 'data', 'L9')))
    assert _near_case_ids(ordered, bound)[0] == ['a', 'c', 'b']      # (electrodes, layers, case_id): layers L1,L2 before L1,L2 reversed
    assert _near_case_ids(near_cases(12), bound) == ([f'c{i:02d}' for i in range(10)], True)
    assert _near_case_ids(near_cases(10), bound)[1] is False


def test_a2_named_pair_marks_truncation_only_when_it_happens():
    many, few = near_cases(12), [vcase('c00', ('L1', 'L2'), rect(range(4), [1]), product='B')]
    bound = pair('p', many, few, (1.0, 1.0), (1.0, 1.0),
                 eps=(Endpoint('E1', 'gate', 'L9'), Endpoint('E2', 'data', 'L9')))
    named = row_at(compute_local_delta([bound], many, few, window_sizes_um=[1.0]), 'p', 1.0)['named_pair']
    assert named['reasons'] == ['named_pair_case_not_found_a', 'named_pair_case_not_found_b']
    assert named['candidates_truncated'] is True and len(named['candidates']['a']) == 10 and named['candidates']['b'] == ['c00']
    small = row_at(compute_local_delta([pair('q', few, few, (1.0, 1.0), (1.0, 1.0),
                                             eps=(Endpoint('E1', 'gate', 'L9'), Endpoint('E2', 'data', 'L9')))],
                                        few, few, window_sizes_um=[1.0]), 'q', 1.0)['named_pair']
    assert 'candidates_truncated' not in small


# ---- A5: the CNO route and a case that exists on one side only -----------------------------------------------------------

def cno_case(case_id, product, cells):
    case = make_case2d(case_id, 'A', 'C', cells, product=product)
    return replace(case, failure_type='lateral_open', endpoints=(Endpoint('A', 'gate', 'M2'),),
                   metadata={**case.metadata, 'case_kind': 'cno'})


def test_a5_cno_named_route_matches_only_cno_cases():
    short = make_case2d('short', 'A', 'C', rect(range(4), range(4)))
    cno_a = cno_case('cno', 'A', rect(range(4), range(2)))
    cno_b = cno_case('cno', 'B', rect(range(4), range(2)))
    from test_local_delta import side as bound_side
    from simcompare.local_delta import BoundPair
    eps = (Endpoint('A', 'gate', 'M2'),)
    source = {'kind': 'manual', 'labelled_by': 'test', 'labelled_at': '2026-11-13'}
    bound = BoundPair('cp', bound_side([cno_a, short], (1.0, 1.0), product='A', eps=eps, failure_type='lateral_open'),
                      bound_side([cno_b], (1.0, 1.0), product='B', eps=eps, failure_type='lateral_open'),
                      None, source, 'ready', ())
    result = _cno_pair(_as_dataset([cno_a, short]), _as_dataset([cno_b]), bound, 5, _square((1.0, 1.0), 1.0),
                       _square((1.0, 1.0), 1.0), None)
    assert result['reasons'] == [] and result['cases']['a']['case_id'] == 'cno' and result['delta'].value == 0


def test_a5_case_on_one_side_only_reports_not_found_with_empty_candidates():
    a = [vcase('x1', ('L1', 'L2'), rect(range(4), [1]))]
    b = [vcase('other', ('L1', 'L2'), rect(range(4), [1]), product='B', a='Z1', b='Z2')]
    bound = pair('p', a, b, (1.0, 1.0), (1.0, 1.0), eps=(Endpoint('E1', 'gate', 'L1'), Endpoint('E2', 'data', 'L2')))
    named = row_at(compute_local_delta([bound], a, b, window_sizes_um=[1.0]), 'p', 1.0)['named_pair']
    assert named['reasons'] == ['named_pair_case_not_found_b'] and named['cases']['a']['case_id'] == 'x1'
    assert named['candidates'] == {'b': []} and 'candidates_truncated' not in named


# ---- R3 / R4 -------------------------------------------------------------------------------------------------------------

def test_r3_expected_only_vertical_case_with_unassigned_layer_fails_before_the_missing_pair_check(tmp_path):
    from simcompare.adapters.real_hotspot import configured_dataset
    from simcompare.numeric_schema import NumericContractError
    from test_interlayer_distance import build, table
    imported = build(tmp_path, tc.base_cases() + [VERT], PAIRS)
    manifest = load_real_manifest(imported)
    manifest['expected_cases'] = list(manifest['expected_cases']) + ['vertical_short-M-L1-N-L9']
    with pytest.raises(NumericContractError) as caught:
        configured_dataset(imported, manifest, tc.assignment(), distance_table=table(HEAD + 'XXYY\t1\n'))
    assert caught.value.code == 'real_actual_layer_unassigned'


def test_r4_electrode_map_keeps_unmapped_pair_and_inventory_reasons_for_a_dropped_diameter(tmp_path):
    from simcompare.adapters.real_hotspot import configured_dataset
    from test_interlayer_distance import _layer_map, build, table
    imported = build(tmp_path, tc.base_cases() + [VERT], tc.PAIRS)      # no XY pair row: the vertical case is unmapped
    manifest = load_real_manifest(imported)
    manifest['expected_case_coverage_status'] = 'unconfirmed'
    ds = configured_dataset(imported, manifest, tc.assignment(), distance_table=table(HEAD + 'L1L2\t3\n'))
    vertical = next(c for c in ds.cases if c.failure_type == 'vertical_short')
    assert 'unmapped_pair' in vertical.reasons
    for diameter in (3, 4):      # 3 is dropped by the rule, 4 is kept: the pair / inventory facts are in both
        reasons = _layer_map(ds, diameter).reasons
        assert 'unmapped_pair' in reasons and 'expected_case_inventory_unprovided' in reasons, (diameter, reasons)
