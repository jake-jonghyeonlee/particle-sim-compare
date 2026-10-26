"""change-roi/T6: `candidates-change` (CNT alignment + hotspot change windows -> DL2 export), synthetic real_hotspot_v1 end to end.

World (reference pixel p <-> target pixel p + OFFSET, 0.1 um pitch, 5 um hotspot masks):
- two cases, AB and AC, each with an unchanged hotspot blob that only moved with the whole layout,
- at CHANGE (reference rows 300..320, cols 300..330): AB's hotspot disappears in the target, AC's appears,
- three DL1 CNT layers whose target copy is the reference shifted by OFFSET, plus one extra CNT rectangle near CHANGE in layer CNT1.
"""
from dataclasses import replace
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest
import yaml

import stage46_generators as gen
from simcompare import candidates, change_candidates
from simcompare.adapters.real_hotspot import import_hotspots
from simcompare.cli import main
from test_candidates import demo
from test_etx_stage1_expected_cases import PYTHONS, _make_runner

ROOT = Path(__file__).resolve().parents[1]
SHAPE = (400, 420)
FULL = (SHAPE[0]+1000, SHAPE[1]+1000)
OFFSET = (7, -4)                      # (dx, dy) px: p_target = p_reference + OFFSET
PITCH = 0.1
CASE_AB, CASE_AC = 'lateral_short-A-L1-B-L1', 'lateral_short-A-L1-C-L1'
ROLES = {'A': 'gate', 'B': 'data', 'C': 'data'}
CHANGE = (slice(300, 320), slice(300, 330))
CHANGE_CENTRE_UM = (50.0+315*PITCH, 50.0+310*PITCH)      # centre of CHANGE in reference coordinates
PRODUCTS = {'reference': 'PROD_R', 'target': 'PROD_T'}
ARGS = dict(search=3, min_dice=0.3, layer_tol=0.5, change_tol=0.2, window=10, overlap=0.5, top_k=3)


def rect(y, x, h, w, shape=SHAPE):
    mask = np.zeros(shape, bool)
    mask[y:y+h, x:x+w] = True
    return mask


def save_black(path, black):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.where(black, 0, 255).astype(np.uint8)).save(path)


def pattern(seed):
    arr = np.zeros(FULL, bool)
    arr[500+seed:503+seed, 502:508] = True
    arr[10:12, 10:12] = True
    return arr


def cnt_world(seed):
    """Random CNT rectangles in the crop area (full-image coordinates), the CHANGE neighbourhood kept empty."""
    rng = np.random.default_rng(seed)
    world = np.zeros(FULL, bool)
    for _ in range(260):
        y, x = int(rng.integers(560, 840)), int(rng.integers(560, 860))
        world[y:y+int(rng.integers(2, 9)), x:x+int(rng.integers(2, 9))] = True
    world[500+285:500+335, 500+285:500+345] = False
    return world


def shifted(world, dx, dy):
    return np.roll(world, (dy, dx), axis=(0, 1))


CASE_AD = 'lateral_short-A-L1-D-L1'     # only in the "extra electrode" target: electrode D does not exist in the reference


def hotspot_masks(side, extra_target_case=False):
    """case -> hotspot mask in the frame of `side`."""
    dx, dy = OFFSET
    unchanged = {CASE_AB: rect(100, 100, 20, 40), CASE_AC: rect(200, 50, 15, 40)}
    masks = {}
    for case, mask in unchanged.items():
        masks[case] = mask if side == 'reference' else np.roll(mask, (dy, dx), axis=(0, 1))
    if side == 'reference':
        masks[CASE_AB] = masks[CASE_AB] | rect(CHANGE[0].start, CHANGE[1].start, 20, 30)
    else:
        masks[CASE_AC] = masks[CASE_AC] | rect(CHANGE[0].start+dy, CHANGE[1].start+dx, 20, 30)
        if extra_target_case:
            masks[CASE_AD] = rect(60, 250, 15, 30)
    return masks


def build_product(base, side, extra_cnt_layer=True, roles=ROLES, extra_target_case=False, dl1_electrodes=None):
    """hotspot tree -> import-hotspots -> import-electrode-assets -> one data_dir; returns (data_dir, assignment).

    The DL1 root also gets one electrode folder per electrode name (default: A, B, C and D when the target has the extra
    case); candidates-change compares those folder names to decide whether the electrode set differs."""
    product = PRODUCTS[side]
    root = base/f'hotspot_{side}'
    masks = hotspot_masks(side, extra_target_case)
    for case, mask in masks.items():
        folder = root/'images'/case
        (folder/'result').mkdir(parents=True)
        save_black(folder/'1.png', pattern(1))
        save_black(folder/'2.png', pattern({CASE_AB: 2, CASE_AC: 3, CASE_AD: 4}[case]))
        Image.fromarray(mask.astype(np.uint8)*255).save(folder/'hotspot_lateral_short05.png')
        with (folder/'result/lateral_short.csv').open('w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.writer(handle)
            writer.writerow(['defect_size(um)', 'Area(um2)'])
            writer.writerow(['5', f'{int(mask.sum())*PITCH*PITCH:.2f}'])
    expected = base/f'expected_{side}.txt'
    expected.write_text(''.join(f'{case}\n' for case in masks))
    imported = base/f'imported_{side}'
    import_hotspots(root, imported, expected_cases=expected)
    pairs = 'AB,A,B,line,pair-v1\nAC,A,C,line,pair-v1\n' + ('AD,A,D,line,pair-v1\n' if CASE_AD in masks else '')
    (imported/'pairs.csv').write_text('pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\n' + pairs)
    assignment = {'product_id': product, 'design_version': 'DESIGN', 'source_id': 'SOURCE', 'process_step_id': 'STEP',
                  'exposure_id': 'EXPOSURE', 'unit_id': 'UNIT', 'pair_table_path': 'pairs.csv',
                  'layer_stack': [{'actual_layer': 'L1', 'stack_order': 1, 'density_process_layer': 'P1'}]}
    if roles is not None:
        assignment['electrode_roles'] = {**roles, **({'D': 'data'} if CASE_AD in masks else {})}
    assignment_path = base/f'assignment_{side}.json'
    assignment_path.write_text(json.dumps(assignment))
    dl1 = base/'dl1'
    dx, dy = OFFSET
    for number, (name, seed) in enumerate((('CNT1', 21), ('CNT2', 22), ('CNT3', 23)), start=1):
        world = cnt_world(seed)
        if side == 'target':
            world = shifted(world, dx, dy)
            if name == 'CNT1' and extra_cnt_layer:   # a CNT rectangle that only the target has, near CHANGE (10 x 10 px)
                world[500+305+dy:500+315+dy, 500+305+dx:500+315+dx] = True
        save_black(dl1/product/'cno'/f'{number}_{name}.png', world)
    adapter = base/f'adapter_{side}'
    code = main(['import-electrode-assets', '--source-kind', 'hotspot_case_electrode', '--root', str(root),
                 '--numeric-data-dir', str(imported), '--condition-assignment', str(assignment_path),
                 '--cnt-from-dl1-root', str(dl1), '--source-version', 'layout_v1', '--max-asset-memory-mb', '512',
                 '--out', str(adapter)])
    assert code == 0
    if dl1_electrodes is None:
        dl1_electrodes = ['A', 'B', 'C'] + (['D'] if CASE_AD in masks else [])
    for electrode in dl1_electrodes:      # written after the import: only the folder names matter to candidates-change
        save_black(dl1/product/electrode/'1-L1.png', rect(0, 0, 4, 4))
    data = base/f'data_{side}'
    shutil.copytree(imported, data)
    (data/'assets').mkdir(exist_ok=True)
    for name in ('review_assets.json', 'electrode_asset_import_record.json'):
        shutil.copyfile(adapter/name, data/name)
    for path in (adapter/'assets').iterdir():
        shutil.copyfile(path, data/'assets'/path.name)
    return data, assignment


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_config(base, datas, assignments, *, name='real.yaml', map_spec=None, record=True):
    sections = {}
    for side in ('reference', 'target'):
        data = datas[side]
        spec = {'data_dir': str(data), 'product_id': PRODUCTS[side], 'condition_assignment': assignments[side],
                'asset_manifest': {'path': 'review_assets.json', 'sha256': sha(data/'review_assets.json')}}
        if record:
            spec['electrode_import_record'] = {'path': 'electrode_asset_import_record.json',
                                               'sha256': sha(data/'electrode_asset_import_record.json')}
        sections[side] = spec
    cfg = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'unused', **sections}
    if map_spec is not None:
        cfg['map'] = map_spec
    path = base/name
    path.write_text(yaml.safe_dump(cfg))
    return path


@pytest.fixture(scope='module')
def world(tmp_path_factory):
    base = tmp_path_factory.mktemp('change_world')
    datas, assignments = {}, {}
    for side in ('reference', 'target'):
        datas[side], assignments[side] = build_product(base, side)
    config = write_config(base, datas, assignments, map_spec={'diameter_um': 5})
    return SimpleNamespace(base=base, config=config, dl1=base/'dl1', datas=datas, assignments=assignments)


def cli_args(config, out, dl1, **kw):
    a = {**ARGS, **kw}
    return ['candidates-change', '--config', str(config), '--cnt-from-dl1-root', str(dl1),
            '--align-search-um', str(a['search']), '--align-min-dice', str(a['min_dice']),
            '--align-layer-tolerance-um', str(a['layer_tol']), '--change-tolerance-um', str(a['change_tol']),
            '--window-um', str(a['window']), '--merge-overlap', str(a['overlap']), '--top-k', str(a['top_k']),
            '--out', str(out)]


def run_change(world, out, **kw):
    return main(cli_args(kw.pop('config', world.config), out, kw.pop('dl1', world.dl1), **kw))


def load(out, name='candidate_queries_v1.json'):
    return json.loads((Path(out)/name).read_text(encoding='utf-8'))


def digests(folder):
    return {p.relative_to(folder).as_posix(): sha(p) for p in sorted(Path(folder).rglob('*')) if p.is_file()}


@pytest.fixture(scope='module')
def first_run(world, tmp_path_factory):
    out = tmp_path_factory.mktemp('change_out')/'out'
    code = run_change(world, out)
    return SimpleNamespace(out=out, code=code, payload=load(out))


def queries(payload, direction):
    return next(d for d in payload['directions'] if d['direction'] == direction)['queries']


# --- the planted change is found, the layout shift is removed ---------------------------------------------------------

def test_alignment_finds_the_known_offset_and_planted_change_is_the_top_query(first_run):
    payload = first_run.payload
    assert payload['alignment']['status'] == 'aligned', payload['alignment']
    assert payload['alignment']['offset_xy_um'] == [round(OFFSET[0]*PITCH, 6), round(OFFSET[1]*PITCH, 6)]
    assert payload['roi_source'] == 'cnt_change' and payload['diameter_um'] == 5.0 and payload['schema_version'] == 'candidate_queries_v1'
    top = queries(payload, 'reference_to_target')[0]
    half = 5.0     # the window is 10 um; the planted change must lie inside it
    assert abs(top['center_xy_um'][0]-CHANGE_CENTRE_UM[0]) <= half and abs(top['center_xy_um'][1]-CHANGE_CENTRE_UM[1]) <= half
    # the unchanged blobs moved with the layout; nothing else is reported as a change
    assert len(queries(payload, 'reference_to_target')) == 1
    assert first_run.code == 3                           # top_k (3) is not filled: only one place changed
    assert [w['code'] for w in payload['warnings']] == ['top_k_not_filled', 'top_k_not_filled']
    # The planted change moves a shape from electrode B's case to C's case at the same place: the per-layer union of
    # hotspots is unchanged (score 0) while the per-pair residual is not.
    assert top['score_layer'] == 'L1' and top['observed_fraction'] == 1.0
    assert top['score_basis'] == 'union_change' and top['score_um2'] == 0
    assert top['sum_residual_um2'] > 0 and top['changed_case_count'] == 2
    assert top['union_delta_um2'] == top['union_area_target_um2']-top['union_area_reference_um2']
    assert 'union_unchanged_pair_changed' in top['flags']


def test_target_direction_centre_is_reference_centre_plus_offset(first_run):
    r2t = queries(first_run.payload, 'reference_to_target')
    t2r = queries(first_run.payload, 'target_to_reference')
    assert len(r2t) == len(t2r) == 1
    want = [r2t[0]['center_xy_um'][0]+OFFSET[0]*PITCH, r2t[0]['center_xy_um'][1]+OFFSET[1]*PITCH]
    assert all(abs(g-w) <= PITCH/2 for g, w in zip(t2r[0]['center_xy_um'], want))
    assert t2r[0]['product_id'] == 'PROD_T' and r2t[0]['product_id'] == 'PROD_R'
    assert t2r[0]['region_id'] == r2t[0]['region_id'] and t2r[0]['region_id'].startswith('chg_')
    assert r2t[0]['max_count'] is None and t2r[0]['max_count'] is None


def test_contributing_cases_context_and_roi_links_match(first_run):
    out, payload = first_run.out, first_run.payload
    for direction, side, product in (('reference_to_target', 'reference', 'PROD_R'),
                                     ('target_to_reference', 'target', 'PROD_T')):
        query = queries(payload, direction)[0]
        cases = query['contributing_cases']
        assert {c['case_key'] for c in cases} == {'lateral_short|A|L1|B|L1|L1', 'lateral_short|A|L1|C|L1|L1'}
        kinds = {c['case_key'].split('|')[3]: c['change_kind'] for c in cases}
        assert kinds == {'B': 'disappeared', 'C': 'appeared'}
        assert all(c['residual_um2'] > 0 and c['flags'] == [] for c in cases)
        assert query['n_rois'] == query['n_rois_ready'] == len(query['roi_ids']) == len(cases) == 2
        rows = {r['roi_id']: r for r in load(out/f'{side}_export', 'review_rois.json')['rois']}
        assert set(query['roi_ids']) == set(rows) and all(r['status'] == 'ready' for r in rows.values())
        assert {r['case_id'] for r in rows.values()} == {CASE_AB, CASE_AC}
        for row in rows.values():       # the query ROI sits at the query centre of this direction, in this product
            assert row['product_id'] == product and row['source_kind'] == 'manual'
            assert row['center_xy_um'] == query['center_xy_um']
        endpoints = {r['case_id']: [e['electrode_id'] for e in r['endpoints']] for r in rows.values()}
        assert endpoints == {CASE_AB: ['A', 'B'], CASE_AC: ['A', 'C']}
    assert queries(payload, 'reference_to_target')[0]['query_id'] != queries(payload, 'target_to_reference')[0]['query_id']


def test_query_attributes_cnt_change_and_extra_keys(first_run):
    payload = first_run.payload
    top = queries(payload, 'reference_to_target')[0]
    assert top['cnt_change_um2_by_layer'] == {'CNT1': 1.0, 'CNT2': 0.0, 'CNT3': 0.0}   # the one extra 10 x 10 px rectangle
    assert 'wiring_change_um2' not in top and top['merged_from'] and top['region_id'] in top['merged_from']
    assert set(top) >= {'score_um2', 'score_layer', 'observed_fraction', 'flags', 'contributing_cases',
                        'cnt_change_um2_by_layer', 'query_id', 'product_id', 'design_version', 'frame_id', 'center_xy_um',
                        'window_um', 'region_id', 'merged_from', 'max_count', 'area_um2', 'layers', 'status', 'reasons',
                        'roi_ids', 'n_rois', 'n_rois_ready'}
    assert payload['change_args'] == {'align_search_um': 3.0, 'align_min_dice': 0.3, 'align_layer_tolerance_um': 0.5,
                                      'change_tolerance_um': 0.2, 'window_um': 10.0, 'merge_overlap': 0.5, 'top_k': 3}
    assert set(payload['alignment']) == {'status', 'reasons', 'offset_xy_um', 'dice_best', 'peak_ratio'}
    stages = {s['stage']: s for s in payload['funnel']['directions'][0]['stages']}
    assert list(stages) == ['change_candidates', 'merged', 'top_k', 'exported_ready']
    assert stages['top_k']['count'] == 1 and stages['top_k']['reasons'] == {'top_k_not_filled': 2}
    assert payload['funnel']['directions'][0]['top_k_not_filled'] == 2
    alignment = load(first_run.out, 'alignment.json')
    assert alignment['status'] == 'aligned' and alignment['sign'] == 'p_target = p_reference + offset'
    text = (first_run.out/'alignment.json').read_text(encoding='utf-8')
    assert text == json.dumps(alignment, ensure_ascii=False, indent=2, sort_keys=True)+'\n'    # sorted keys, indent 2
    assert alignment['schema_version'] == 'cnt_alignment_v1' and alignment['pitch_um'] == PITCH
    assert sorted(alignment['cnt_inputs']) == ['reference', 'target']
    assert {f['layer'] for f in alignment['cnt_inputs']['target']['files']} == {'CNT1', 'CNT2', 'CNT3'}


# --- exit codes, determinism, DL2 hand-over -----------------------------------------------------------------------------

def test_filled_top_k_and_complete_inputs_exit_zero(world, tmp_path, capsys):
    out = tmp_path/'out'
    assert run_change(world, out, top_k=1) == 0
    printed = json.loads(capsys.readouterr().out)
    payload = load(out)
    assert printed['exit_code'] == 0 and payload['status'] == 'available' and payload['warnings'] == []
    assert load(out, 'execution.json')['exit_code'] == 0
    assert all(q['status'] == 'ready' and q['n_rois'] == q['n_rois_ready'] == 2 for d in payload['directions'] for q in d['queries'])
    for side in ('reference', 'target'):
        cov = payload['funnel']['index_coverage'][side]
        assert cov['complete'] and cov['electrodes_covered'] == cov['electrodes_total'] == 3
        assert load(out/f'{side}_index_export', 'review_rois.json')['rois']


def test_same_input_twice_gives_identical_bytes(world, tmp_path, first_run):
    out = tmp_path/'again'
    assert run_change(world, out) == first_run.code
    assert digests(out) == digests(first_run.out)
    assert sorted(digests(out)) == sorted(set(digests(out))) and 'alignment.json' in digests(out)
    execution = load(out, 'execution.json')
    assert sorted(execution['outputs'] + ['execution.json']) == sorted(digests(out))
    assert execution['command'] == 'candidates-change' and execution['inputs']['config_sha256']
    assert all(not str(out) in p.read_text(encoding='utf-8', errors='ignore') for p in out.rglob('*.json'))   # paths stay relative


def test_confirm_pairs_list_reads_the_folder_without_changing_it(world, tmp_path, first_run, capsys):
    before = digests(first_run.out)
    models = {side: SimpleNamespace(spec=SimpleNamespace(shape=SHAPE, product_id=PRODUCTS[side]))
              for side in ('reference', 'target')}
    matches_dir = tmp_path/'matches'
    matches_dir.mkdir()
    matches = gen.fake_matches(first_run.out, models, matches_dir, seed=3)
    capsys.readouterr()
    code = main(['confirm-pairs', '--list', '--config', str(world.config), '--candidates', str(first_run.out),
                 *[a for d, p in matches.items() for a in ('--match', f'{d}={p}')]])
    assert code == 0
    listing = json.loads(capsys.readouterr().out)
    assert {c['direction'] for c in listing['candidates']} == {'reference_to_target', 'target_to_reference'}
    assert listing['n_candidates'] == 12 and listing['unavailable_queries'] == []   # 2 ROIs x top 3 x 2 directions
    query_ids = {r for d in first_run.payload['directions'] for q in d['queries'] for r in q['roi_ids']}
    assert {c['query_roi_id'] for c in listing['candidates']} == query_ids
    assert digests(first_run.out) == before


def test_alignment_failure_still_exports_and_exits_three(world, tmp_path):
    empty_dl1 = tmp_path/'no_cnt'
    empty_dl1.mkdir()
    out = tmp_path/'out'
    assert run_change(world, out, dl1=empty_dl1) == 3
    alignment = load(out, 'alignment.json')
    assert alignment['status'] == 'alignment_failed_identity_used' and alignment['offset_xy_px'] == [0, 0]
    assert alignment['reasons'] == ['cnt_missing_reference', 'cnt_missing_target']
    assert [w['status'] for w in alignment['cnt_inputs']['reference']['warnings']] == ['product_folder_missing']
    payload = load(out)
    assert payload['alignment']['status'] == 'alignment_failed_identity_used'
    assert payload['status'] == 'partial' and 'alignment_failed' in {w['code'] for w in payload['warnings']}
    for side in ('reference', 'target'):
        assert load(out/f'{side}_export', 'review_rois.json')['status'] in ('available', 'partial')
        assert (out/f'{side}_index_export'/'review_rois.json').is_file()
    assert queries(payload, 'reference_to_target') and all(q['cnt_change_um2_by_layer'] == {}
                                                         for q in queries(payload, 'reference_to_target'))


def test_missing_argument_exits_two_and_writes_nothing(world, tmp_path, capsys):
    full = cli_args(world.config, tmp_path/'out', world.dl1)
    for flag in [a for a in full if a.startswith('--')]:
        argv = list(full)
        index = argv.index(flag)
        del argv[index:index+2]
        with pytest.raises(SystemExit) as caught:
            main(argv)
        assert caught.value.code == 2, flag
    assert not (tmp_path/'out').exists()
    for bad in ({'top_k': 0}, {'overlap': 0}, {'overlap': 1.5}, {'window': -1}, {'min_dice': 2}, {'change_tol': -0.1},
                {'layer_tol': -1}, {'search': 0.1}):
        assert run_change(world, tmp_path/'out', **bad) == 2, bad
        assert not (tmp_path/'out').exists(), bad
    with pytest.raises(SystemExit) as caught:
        run_change(world, tmp_path/'out', top_k='abc')
    assert caught.value.code == 2 and not (tmp_path/'out').exists()
    capsys.readouterr()
    assert run_change(world, tmp_path/'out', search=0.1) == 2
    assert 'invalid_alignment_input' in capsys.readouterr().err


def test_existing_output_folder_is_refused_and_untouched(world, tmp_path, capsys):
    out = tmp_path/'out'
    (out/'keep').mkdir(parents=True)
    (out/'keep'/'note.txt').write_text('mine')
    before = digests(out)
    assert run_change(world, out) == 2
    assert 'output_dir_exists' in capsys.readouterr().err
    assert digests(out) == before


def test_multiple_frames_is_refused_before_anything_is_written(world, tmp_path, monkeypatch, capsys):
    original = candidates.prepare_side

    def two_frames(cfg, name, diameter_um, reserved_bytes=0):
        side = original(cfg, name, diameter_um, reserved_bytes)
        if name == 'target':
            side.dataset.cases[0].identity = replace(side.dataset.cases[0].identity, frame_id='OTHER_FRAME')
        return side
    monkeypatch.setattr(candidates, 'prepare_side', two_frames)
    out = tmp_path/'out'
    assert run_change(world, out) == 2
    err = capsys.readouterr().err
    assert 'multiple_frames_unsupported' in err and 'PROD_T' in err and 'OTHER_FRAME' in err
    assert not out.exists()


def test_numeric_config_and_fixed_diameter_exit_two(world, tmp_path, capsys):
    numeric = demo(tmp_path)/'config.yaml'
    out = tmp_path/'out'
    assert run_change(world, out, config=numeric) == 2
    assert 'candidates_change_requires_real_hotspot' in capsys.readouterr().err and not out.exists()
    three = write_config(world.base, world.datas, world.assignments, name='three.yaml', map_spec={'diameter_um': 3})
    assert run_change(world, out, config=three) == 2
    assert 'candidates_change_diameter_fixed_5um' in capsys.readouterr().err and not out.exists()
    without_map = write_config(world.base, world.datas, world.assignments, name='nomap.yaml')
    assert run_change(world, out, config=without_map) == 3 and out.exists()      # no map section: 5 um is implied


def test_failure_after_writing_removes_only_what_this_run_made(world, tmp_path, monkeypatch):
    out = tmp_path/'out'

    def boom(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(candidates, 'export_index', boom)
    assert run_change(world, out) == 2
    assert not out.exists()


def test_operation_limit_is_one_explicit_value_passed_to_every_stage(world, tmp_path, monkeypatch):
    from simcompare import change_map, cnt_align
    seen = {}

    def spy(module, name):
        original = getattr(module, name)

        def wrapper(*args, **kwargs):
            seen[name] = kwargs.get('max_operation_bytes', 'missing')
            return original(*args, **kwargs)
        monkeypatch.setattr(module, name, wrapper)
    for module, name in ((cnt_align, 'align_products'), (change_map, 'change_maps'),
                         (change_map, 'window_candidates'), (change_map, 'case_contributions')):
        spy(module, name)
    assert run_change(world, tmp_path/'out') == 3
    limits = set(seen.values())
    assert set(seen) == {'align_products', 'change_maps', 'window_candidates', 'case_contributions'}
    assert len(limits) == 1 and isinstance(next(iter(limits)), int) and next(iter(limits)) > 0
    from simcompare import pipeline
    cfg = pipeline.load_numeric_config(world.config)
    expected = min(candidates.prepare_side(cfg, name, 5).dataset.provenance['resource_estimate']['max_operation_bytes']
                   for name in ('reference', 'target'))
    assert next(iter(limits)) == expected == load(tmp_path/'out', 'execution.json')['args']['max_operation_bytes']
    monkeypatch.setattr(change_candidates, '_operation_limit', lambda sides: 1000)
    failed = tmp_path/'tiny'
    assert run_change(world, failed) == 2 and not failed.exists()


def test_roi_rows_are_deduplicated_by_request_key(world, tmp_path, monkeypatch):
    from simcompare import change_map
    real = change_map.case_contributions

    def doubled(*args, **kwargs):
        result = real(*args, **kwargs)
        for window in result['windows']:
            window['contributing_cases'] = window['contributing_cases'] * 2
        return result
    monkeypatch.setattr(change_map, 'case_contributions', doubled)
    sent = []
    export = candidates.export_manual_rows

    def spy(cfg, side, requests, *args, **kwargs):
        sent.append((side.name, list(requests)))
        return export(cfg, side, requests, *args, **kwargs)
    monkeypatch.setattr(candidates, 'export_manual_rows', spy)
    out = tmp_path/'out'
    run_change(world, out, top_k=1)
    assert [(name, len(requests)) for name, requests in sent[:2]] == [('reference', 2), ('target', 2)]   # 4 contributions, 2 keys
    for direction in load(out)['directions']:
        for query in direction['queries']:
            assert len(query['roi_ids']) == query['n_rois'] == 2 and len(query['contributing_cases']) == 4
    for side in ('reference', 'target'):
        assert len(load(out/f'{side}_export', 'review_rois.json')['rois']) == 2


def test_blocked_rows_stay_linked_and_make_a_partial_run(tmp_path):
    datas, assignments = {}, {}
    for side in ('reference', 'target'):
        datas[side], assignments[side] = build_product(tmp_path, side, roles=None)
    config = write_config(tmp_path, datas, assignments, map_spec={'diameter_um': 5})
    out = tmp_path/'out'
    assert main(cli_args(config, out, tmp_path/'dl1', top_k=1)) == 3
    payload = load(out)
    for direction in payload['directions']:
        query = direction['queries'][0]
        assert query['status'] == 'blocked' and query['n_rois'] == 2 and query['n_rois_ready'] == 0
        assert len(query['roi_ids']) == 2 and 'electrode_role_required_for_matching' in query['reasons']
    assert 'electrode_role_required_for_matching' in {w['code'] for w in payload['warnings']}
    assert payload['funnel']['directions'][0]['exported_ready'] == 0


def test_electrode_set_difference_warns_lists_the_one_sided_case_and_exits_three(tmp_path, capsys):
    """Same-role electrodes keep their names across models; a new or removed electrode means the circuit changed.
    The run continues (the planted change is still found), warns, lists the case that only one side has, exits 3."""
    datas, assignments = {}, {}
    for side in ('reference', 'target'):
        datas[side], assignments[side] = build_product(tmp_path, side, extra_target_case=True)
    config = write_config(tmp_path, datas, assignments, map_spec={'diameter_um': 5})
    out = tmp_path/'out'
    capsys.readouterr()
    assert main(cli_args(config, out, tmp_path/'dl1', top_k=1)) == 3
    printed = json.loads(capsys.readouterr().out)
    payload = load(out)
    differs = [w for w in payload['warnings'] if w['code'] == 'electrode_set_differs']
    assert differs == [{'code': 'electrode_set_differs', 'electrode_set_basis': 'dl1_electrode_folders',
                        'reference_only': [], 'target_only': ['D']}]
    assert payload['electrode_set_basis'] == 'dl1_electrode_folders'
    assert load(out, 'execution.json')['warnings'] == payload['warnings'] and 'electrode_set_differs' in printed['reasons']
    assert [w['code'] for w in payload['warnings']] == ['electrode_set_differs']       # nothing else is partial
    assert payload['status'] == 'partial' and payload['alignment']['status'] == 'aligned'
    only_target = payload['change_map']['cases_only_in_target']
    assert [c['case_key'] for c in only_target] == ['lateral_short|A|L1|D|L1|L1'] and payload['change_map']['cases_only_in_reference'] == []
    for direction in payload['directions']:                                      # the planted change is still reported
        query = direction['queries'][0]
        assert {c['case_key'] for c in query['contributing_cases']} == {'lateral_short|A|L1|B|L1|L1', 'lateral_short|A|L1|C|L1|L1'}
        assert query['status'] == 'ready' and query['n_rois'] == query['n_rois_ready'] == 2
    same = json.loads((out/'candidate_queries_v1.json').read_text())['funnel']['index_coverage']
    assert same['target']['dataset_cases'] == 3 and same['reference']['dataset_cases'] == 2


# --- T6 review follow-ups (adv:1..4) ---------------------------------------------------------------------------------

def test_held_bytes_are_reserved_from_every_stage_limit(world, tmp_path, monkeypatch, capsys):
    """Each stage gets reserved_bytes (datasets + CNT layers + valid). A limit that only the unreserved need fits is refused;
    need + reserved per stage is accepted."""
    from simcompare import change_map, cnt_align
    seen = {}

    def spy(module, name, need_of):
        original = getattr(module, name)

        def wrapper(*args, **kwargs):
            result = original(*args, **kwargs)
            seen[name] = (need_of(result, kwargs), kwargs['reserved_bytes'])
            return result
        monkeypatch.setattr(module, name, wrapper)
    spy(cnt_align, 'align_products', lambda r, kw: r['estimated_bytes'])
    spy(change_map, 'change_maps', lambda r, kw: r['stats']['estimated_bytes'])
    spy(change_map, 'window_candidates',
        lambda r, kw: change_map.estimate_change_map_bytes((SHAPE[0], SHAPE[1]), (SHAPE[0], SHAPE[1]), 3, r['window_px'], 0))
    spy(change_map, 'case_contributions', lambda r, kw: r['stats']['estimated_bytes'])
    assert run_change(world, tmp_path/'first') == 3
    assert set(seen) == {'align_products', 'change_maps', 'window_candidates', 'case_contributions'}
    assert all(reserved > 0 for _, reserved in seen.values())
    assert len({reserved for _, reserved in seen.values()}) == 1
    unreserved = max(need for need, _ in seen.values())
    exact = max(need + reserved for need, reserved in seen.values())
    capsys.readouterr()
    monkeypatch.setattr(change_candidates, '_operation_limit', lambda sides: unreserved)
    refused = tmp_path/'refused'
    assert run_change(world, refused) == 2 and not refused.exists()
    assert 'oversized_real_operation' in capsys.readouterr().err
    monkeypatch.setattr(change_candidates, '_operation_limit', lambda sides: exact)
    assert run_change(world, tmp_path/'accepted') == 3


def test_reserved_bytes_default_keeps_the_old_admission_and_rejects_bad_values():
    from simcompare import change_map, cnt_align
    from simcompare.numeric_schema import NumericContractError
    valid = np.ones((60, 60), bool)
    need = change_map.estimate_change_map_bytes(valid.shape, valid.shape, 0, 10, 0)
    maps = {'L1': np.zeros(valid.shape, np.uint32)}
    kwargs = dict(pitch_um=0.1, window_um=1.0)
    none = dict(union_change={}, union_ref={}, union_tgt={})
    change_map.window_candidates({}, valid, max_operation_bytes=need, **none, **kwargs)
    change_map.window_candidates({}, valid, max_operation_bytes=need, reserved_bytes=0, **none, **kwargs)
    with pytest.raises(NumericContractError) as caught:
        change_map.window_candidates({}, valid, max_operation_bytes=need, reserved_bytes=1, **none, **kwargs)
    assert caught.value.code == 'oversized_real_operation'
    for bad in (-1, 1.5, True):
        with pytest.raises(NumericContractError):
            change_map.window_candidates(maps, valid, max_operation_bytes=need * 10, reserved_bytes=bad,
                                         union_change={'L1': np.zeros(valid.shape, bool)},
                                         union_ref={'L1': np.zeros(valid.shape, bool)},
                                         union_tgt={'L1': np.zeros(valid.shape, bool)}, **kwargs)
    layers = {'CNT1': rect(10, 10, 5, 5, (60, 60))}
    side = {'valid': valid, 'layers': layers}
    align = dict(pitch_um=0.1, search_um=0.5, min_dice=0.1, layer_tolerance_um=0.1)
    probe = cnt_align.align_products(side, side, max_operation_bytes=10**12, **align)
    cnt_align.align_products(side, side, max_operation_bytes=probe['estimated_bytes'], **align)
    with pytest.raises(NumericContractError) as caught:
        cnt_align.align_products(side, side, max_operation_bytes=probe['estimated_bytes'], reserved_bytes=1, **align)
    assert caught.value.code == 'oversized_real_operation'


def test_one_sided_cnt_gives_no_cnt_change_and_a_flag(world, tmp_path):
    only_reference = tmp_path/'dl1_ref_only'
    shutil.copytree(world.dl1/PRODUCTS['reference'], only_reference/PRODUCTS['reference'])
    out = tmp_path/'out'
    assert run_change(world, out, dl1=only_reference) == 3
    payload = load(out)
    assert payload['alignment']['status'] == 'alignment_failed_identity_used'
    for direction in payload['directions']:
        assert direction['queries']
        for query in direction['queries']:
            assert query['cnt_change_um2_by_layer'] == {} and 'cnt_change_unavailable' in query['flags']


def test_electrode_set_compares_dl1_folders_not_case_endpoints(tmp_path_factory, capsys):
    # (a) a folder only the target has, identical cases: warns and exits 3
    base = tmp_path_factory.mktemp('folder_only')
    datas, assignments = {}, {}
    for side in ('reference', 'target'):
        datas[side], assignments[side] = build_product(base, side, dl1_electrodes=['A', 'B', 'C'] + (['E'] if side == 'target' else []))
    config = write_config(base, datas, assignments, map_spec={'diameter_um': 5})
    capsys.readouterr()
    assert main(cli_args(config, base/'out', base/'dl1', top_k=1)) == 3
    payload = load(base/'out')
    assert [w for w in payload['warnings'] if w['code'] == 'electrode_set_differs'] == [
        {'code': 'electrode_set_differs', 'electrode_set_basis': 'dl1_electrode_folders', 'reference_only': [], 'target_only': ['E']}]
    assert payload['change_map']['cases_only_in_target'] == [] and payload['change_map']['cases_only_in_reference'] == []
    # (b) a case only the target has, but the folders match: no warning, exit 0
    base = tmp_path_factory.mktemp('case_only')
    datas, assignments = {}, {}
    for side in ('reference', 'target'):
        datas[side], assignments[side] = build_product(base, side, extra_target_case=True, dl1_electrodes=['A', 'B', 'C', 'D'])
    config = write_config(base, datas, assignments, map_spec={'diameter_um': 5})
    capsys.readouterr()
    assert main(cli_args(config, base/'out', base/'dl1', top_k=1)) == 0
    payload = load(base/'out')
    assert payload['warnings'] == [] and [c['case_key'] for c in payload['change_map']['cases_only_in_target']] == [
        'lateral_short|A|L1|D|L1|L1']


def test_list_dl1_electrodes_names_only(tmp_path):
    from simcompare.adapters.electrode_png import list_dl1_electrodes
    root = tmp_path/'dl1'
    for folder in ('B', 'A', 'cno'):
        save_black(root/'M'/folder/('1_CNT1.png' if folder == 'cno' else '1-L1.png'), rect(0, 0, 2, 2))
    assert list_dl1_electrodes(root, 'M') == ['A', 'B'] and list_dl1_electrodes(root, 'missing') == []


def test_window_checks_run_before_any_output_is_started(world, tmp_path, monkeypatch, capsys):
    def boom():
        raise AssertionError('output tracking started before the argument checks')
    monkeypatch.setattr(candidates, 'output_tracker', boom)
    for window in (PITCH/2, 100.0):          # smaller than a pixel / larger than the 40 x 42 um frame
        out = tmp_path/f'out_{window}'
        capsys.readouterr()
        assert run_change(world, out, window=window) == 2
        assert 'invalid_candidates_change_argument' in capsys.readouterr().err and not out.exists()


# --- T7/T7b review follow-up (adv:2): the ranking, at command level -------------------------------------------------------

def test_a_union_changing_move_outranks_a_union_unchanged_move_at_the_command_level(tmp_path, monkeypatch):
    """Two planted changes: (1) B's hotspot moves to C at CHANGE (the per-layer union is unchanged: score 0) and (2) the
    target alone gets a new hotspot blob of B at another place (the union changes: score = the blob area). --top-k 1 must
    select (2); --top-k 3 lists (2) first and (1) second, flagged union_unchanged_pair_changed."""
    import test_change_candidates as me
    original = me.hotspot_masks
    blob = rect(250, 200, 15, 20)                  # target frame; reference frame = minus OFFSET -> rows 254.., cols 193..

    def with_blob(side, extra_target_case=False):
        masks = original(side, extra_target_case)
        if side == 'target':
            masks[CASE_AB] = masks[CASE_AB] | blob
        return masks
    monkeypatch.setattr(me, 'hotspot_masks', with_blob)
    datas, assignments = {}, {}
    for side in ('reference', 'target'):
        datas[side], assignments[side] = build_product(tmp_path, side)
    config = write_config(tmp_path, datas, assignments, map_spec={'diameter_um': 5})
    blob_centre = (50.0+(193+10)*PITCH, 50.0+(254+7.5)*PITCH)
    one = tmp_path/'one'
    main(cli_args(config, one, tmp_path/'dl1', top_k=1))
    top = queries(load(one), 'reference_to_target')
    assert len(top) == 1 and top[0]['score_um2'] == pytest.approx(3.0) and top[0]['score_basis'] == 'union_change'   # 15 x 20 px x 0.01 um2
    assert abs(top[0]['center_xy_um'][0]-blob_centre[0]) <= 5.0 and abs(top[0]['center_xy_um'][1]-blob_centre[1]) <= 5.0
    assert top[0]['sum_residual_um2'] == pytest.approx(3.0) and top[0]['changed_case_count'] == 1
    assert top[0]['union_delta_um2'] == pytest.approx(3.0)
    assert [c['change_kind'] for c in top[0]['contributing_cases']] == ['appeared']
    assert 'union_unchanged_pair_changed' not in top[0]['flags']
    assert len(load(one, 'direct/roi_correspondence.json')['pairs']) == 1
    three = tmp_path/'three'
    main(cli_args(config, three, tmp_path/'dl1', top_k=3))
    both = queries(load(three), 'reference_to_target')
    assert [round(q['score_um2'], 6) for q in both] == [3.0, 0]
    assert both[0]['region_id'] == top[0]['region_id'] and 'union_unchanged_pair_changed' in both[1]['flags']
    assert abs(both[1]['center_xy_um'][0]-CHANGE_CENTRE_UM[0]) <= 5.0 and both[1]['changed_case_count'] == 2
    assert len(load(three, 'direct/roi_correspondence.json')['pairs']) == 1 + 2        # one case + two cases


# --- the single-file runner: same bytes on the ETX interpreter (Python 3.9) --------------------------------------------

# execution.json and the other files hold no interpreter, host or timestamp field, so every output file is compared;
# a field of that kind would be listed here and removed from both sides before the comparison.
ENVIRONMENT_FIELDS = ()


@pytest.mark.skipif(not os.environ.get('SIMCOMPARE_ETX_PY'), reason='SIMCOMPARE_ETX_PY is not set (path of the ETX Python 3.9)')
def test_runner_output_on_python39_equals_the_current_interpreter(world, tmp_path):
    outputs = {}
    for label, python in (('current', sys.executable), ('etx39', os.path.expanduser(os.environ['SIMCOMPARE_ETX_PY']))):
        out = tmp_path/label/'out'
        jobs = [('candidates-change', cli_args(world.config, out, world.dl1)[1:])]
        script = _make_runner(tmp_path/label/'runner', jobs, log_file=str(tmp_path/label/'job.log'))
        done = subprocess.run([python, '-I', str(script)], capture_output=True, text=True, cwd=tmp_path)
        text = (tmp_path/label/'job.log').read_text(encoding='utf-8')
        assert 'candidates-change exit 3' in text, (done.stderr, text[-2000:])
        outputs[label] = digests(out)
    assert outputs['current'] == outputs['etx39'] and len(outputs['current']) == 12
    for name in ('direct/roi_correspondence.json', 'direct/direct_pairs_sidecar.json', 'alignment.html'):   # same bytes on 3.9 and 3.11
        assert name in outputs['current']
        assert outputs['current'][name] == outputs['etx39'][name]
    assert not ENVIRONMENT_FIELDS
    in_tree = tmp_path/'in_tree'
    assert run_change(world, in_tree) == 3
    differs = {name for name in digests(in_tree) if digests(in_tree)[name] != outputs['current'].get(name)}
    assert differs <= {'reference_export/review_rois.json', 'target_export/review_rois.json',
                       'reference_index_export/review_rois.json', 'target_index_export/review_rois.json',
                       'reference_export/review_rois.csv', 'target_export/review_rois.csv',
                       'reference_index_export/review_rois.csv', 'target_index_export/review_rois.csv',
                       'direct/roi_correspondence.json',
                       'candidate_queries_v1.json', 'execution.json'}     # only the exporter's producer_commit differs
