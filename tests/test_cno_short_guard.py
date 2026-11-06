"""compare2/T20: CNO cases (--include-cno) are left out of every short-only consumer and of its coverage checks."""
import csv
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml
from PIL import Image

import test_compare2 as tc2
import test_real_hotspot as trh
from simcompare import candidates, change_candidates
from simcompare.adapters.electrode_png import import_electrode_assets
from simcompare.adapters.real_hotspot import configured_dataset, import_hotspots, load_real_manifest
from simcompare.cli import main
from simcompare.electrode_map import compute_electrode_maps
from simcompare.linepixel import compute_areas
from simcompare.numeric_schema import NumericContractError, is_cno_case

CNO_NAME = 'lateralopen-CNO-GCNT-'
CNO_CSV = 'defect_size(um),Area(um2)\n1,0.03\n2,0.02\n3,0.01\n'
HASH_KEYS = ('input_hash', 'source_payload_hash', 'source_manifest_hash', 'cno_cases_excluded')


def cno_assignment():
    value = tc2.assignment()
    value['layer_stack'] = value['layer_stack'] + [
        {'actual_layer': 'GCNT', 'stack_order': 3, 'density_process_layer': 'gcnt'}]
    return value


def build(tmp, tag, *, include_cno, skip=()):
    """One side tree: five-part cases + one CNO folder with images. Returns (hotspot root, imported folder)."""
    base = tmp / tag
    root, imported = tc2.write_side(base, 'side', tc2.base_cases(), tc2.PAIRS, cno={CNO_NAME: CNO_CSV}, skip=skip)
    folder = root / CNO_NAME / 'images'
    folder.mkdir(parents=True)
    Image.new('L', (1004, 1004), 0).save(folder / '1.png')
    mask = np.zeros((4, 4), dtype=np.uint8)
    mask[0, 0] = mask[1, 1] = 255
    Image.fromarray(mask).save(folder / 'hotspot_lateral_open01.png')
    if include_cno:
        shutil.rmtree(imported)
        import_hotspots(root, imported, expected_cases=base / 'side' / 'expected.txt', include_cno=True)
        (imported / 'pairs.csv').write_text(
            'pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\n'
            + ''.join(f'{pid},{a},{b},{cls},v1\n' for pid, a, b, cls in tc2.PAIRS), encoding='utf-8')
    return root, imported


def dataset_of(imported):
    return configured_dataset(imported, load_real_manifest(imported), cno_assignment())


def strip(value):
    if isinstance(value, dict):
        return {key: strip(item) for key, item in value.items() if key not in HASH_KEYS}
    if isinstance(value, list):
        return [strip(item) for item in value]
    return value


@pytest.fixture
def both(tmp_path):
    return build(tmp_path, 'on', include_cno=True), build(tmp_path, 'off', include_cno=False)


def test_on_dataset_holds_the_cno_case_and_off_does_not(both):
    (_, on_imp), (_, off_imp) = both
    on, off = dataset_of(on_imp), dataset_of(off_imp)
    assert [is_cno_case(c) for c in on.cases].count(True) == 1 and not any(is_cno_case(c) for c in off.cases)
    assert len(on.cases) == len(off.cases) + 1


def test_compute_areas_skips_cno_and_matches_the_dataset_without_it(both):
    (_, on_imp), (_, off_imp) = both
    on, off = compute_areas(dataset_of(on_imp)), compute_areas(dataset_of(off_imp))
    on_doc, off_doc = on.to_dict(), off.to_dict()
    assert on_doc['cno_cases_excluded'] == 1 and 'cno_cases_excluded' not in off_doc
    assert on_doc['input_hash'] != off_doc['input_hash']
    assert strip(on_doc) == strip(off_doc)


def test_electrode_map_skips_cno_and_matches_the_dataset_without_it(both):
    (_, on_imp), (_, off_imp) = both
    on = compute_electrode_maps(dataset_of(on_imp), diameter_um=1.0)
    off = compute_electrode_maps(dataset_of(off_imp), diameter_um=1.0)
    assert on.provenance['cno_cases_excluded'] == 1 and 'cno_cases_excluded' not in off.provenance
    assert on.provenance['missing_manifest_cases'] == off.provenance['missing_manifest_cases'] == []
    assert strip(on.to_dict()) == strip(off.to_dict())


def test_index_coverage_universe_ignores_cno(both):
    (_, on_imp), (_, off_imp) = both
    state = {'rows': [], 'own_blocked': {}, 'status': 'available', 'reasons': [], 'path': None}
    got = {tag: candidates.index_coverage(SimpleNamespace(dataset=dataset_of(imp), index=state, name='P'))
           for tag, imp in (('on', on_imp), ('off', off_imp))}
    for key in ('electrodes_total', 'by_role_layer', 'missing_by_role_layer'):
        assert got['on'][key] == got['off'][key]
    assert not any('CNO' in str(key) for key in got['on']['by_role_layer'])
    assert got['on']['dataset_cases'] == got['off']['dataset_cases'] + 1


def test_diameter_check_is_not_satisfied_by_a_cno_case(tmp_path):
    # the short cases miss diameter 1; the CNO hotspot has it
    _, imported = build(tmp_path, 'on', include_cno=True, skip=(1,))
    sides = {'reference': SimpleNamespace(dataset=dataset_of(imported))}
    with pytest.raises(NumericContractError) as info:
        change_candidates._check_diameter_observed(sides, 1)
    assert info.value.code == 'candidates_change_diameter_not_observed'
    change_candidates._check_diameter_observed(sides, 2)   # a short case observes diameter 2


def run_compare2(tmp, tag, imported, root):
    base = tmp / tag / 'cfg'
    base.mkdir(parents=True)
    numeric = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'unused',
               'reference': {'data_dir': str(imported), 'condition_assignment': cno_assignment()},
               'target': {'data_dir': str(imported), 'condition_assignment': cno_assignment()}}
    (base / 'numeric.yaml').write_text(yaml.safe_dump(numeric), encoding='utf-8')
    tc2.write_density(base / 'density', 'mobile', tc2.DENS_LINE, {**tc2.DENS_PIXEL, 'GCNT': tc2.DENS_PIXEL['gcnt']})
    glass = {'value': 50.0, 'unit': 'um2'}
    cfg = {'schema_version': 'compare2_config_v1', 'numeric_config': 'numeric.yaml', 'output_dir': 'out',
           'density_dir': 'density',
           'model_groups': {'mobile': {'glass_area': glass, 'weight_line': 1.0, 'weight_pixel': 1.0}},
           'products': {'reference': {'model_group': 'mobile', 'resolution': tc2.RES, 'hotspot_root': str(root)},
                        'target': {'model_group': 'mobile', 'resolution': tc2.RES, 'hotspot_root': str(root)}}}
    (base / 'compare2.yaml').write_text(yaml.safe_dump(cfg), encoding='utf-8')
    code = main(['compare2', '--config', str(base / 'compare2.yaml')])
    return code, base / 'out', base


def test_compare2_with_cno_dataset_equals_the_one_without(both, tmp_path):
    (on_root, on_imp), (off_root, off_imp) = both
    on_code, on_out, on_base = run_compare2(tmp_path, 'run_on', on_imp, on_root)
    off_code, off_out, off_base = run_compare2(tmp_path, 'run_off', off_imp, off_root)
    assert on_code == off_code == 0
    docs = {}
    for tag, out, base in (('on', on_out, on_base), ('off', off_out, off_base)):
        text = json.dumps(strip(tc2.load(out)))
        for path in (str(base), str(base.parent.parent)):
            text = text.replace(path, 'ROOT')
        docs[tag] = {key: value for key, value in json.loads(text).items() if key not in ('inputs', 'products')}
    assert docs['on'].keys() == docs['off'].keys()
    for key in docs['on']:
        assert docs['on'][key] == docs['off'][key], key
    assert docs['on']['delta']['cno']   # the CNO CSV rows still reach the lambda, from the CSV
    codes = [main(['validate-data', '--config', str(base / 'numeric.yaml')]) for base in (on_base, off_base)]
    assert codes[0] == codes[1]


# ---- 3rd round: electrode assets --------------------------------------------------------------------------------

def test_import_electrode_assets_skips_cno_case_with_reason(tmp_path):
    import test_electrode_png as tep
    root = tep.real_source(tmp_path)
    cno = root / CNO_NAME
    (cno / 'result').mkdir(parents=True)
    (cno / 'result/lateral_open.csv').write_text(CNO_CSV, encoding='utf-8')
    (cno / 'images').mkdir()
    Image.new('L', (tep.REAL_SHAPE[1], tep.REAL_SHAPE[0]), 0).save(cno / 'images/1.png')
    Image.new('L', (20, 10), 0).save(cno / 'images/hotspot_lateral_open01.png')
    results = {}
    for tag, flag in (('on', True), ('off', False)):
        imported = tmp_path / f'imp_{tag}'
        import_hotspots(root, imported, include_cno=flag)
        (imported / 'pairs.csv').write_text(
            'pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\nAB,A,B,line,pair-v1\n')
        assignment = {'product_id': 'PRODUCT', 'design_version': 'DESIGN', 'source_id': 'SOURCE',
                      'process_step_id': 'STEP', 'exposure_id': 'EXPOSURE', 'unit_id': 'UNIT',
                      'pair_table_path': 'pairs.csv',
                      'layer_stack': [{'actual_layer': 'L1', 'stack_order': 1, 'density_process_layer': 'P1'},
                                      {'actual_layer': 'GCNT', 'stack_order': 2, 'density_process_layer': 'PG'}]}
        path = tmp_path / f'assignment_{tag}.json'
        path.write_text(json.dumps(assignment))
        results[tag] = import_electrode_assets(
            'hotspot_case_electrode', root, tmp_path / f'out_{tag}', numeric_data_dir=imported,
            condition_assignment=path, source_version='v', max_asset_memory_mb=256,
            cnt_from_dl1_root=tep.cnt_root(tmp_path))
    assert results['on']['exit_code'] == results['off']['exit_code'] == 0
    assert results['on']['cases_converted'] == results['off']['cases_converted'] == 1
    record_on = json.loads((tmp_path / 'out_on' / 'electrode_asset_import_record.json').read_text())
    record_off = json.loads((tmp_path / 'out_off' / 'electrode_asset_import_record.json').read_text())
    assert record_on['skipped_cases'] == [{'case_id': CNO_NAME, 'product_id': 'PRODUCT',
                                           'reasons': ['cno_case_no_electrode_asset']}]
    assert 'skipped_cases' not in record_off and record_on['blocked_cases'] == record_off['blocked_cases'] == []
    assert record_on['counts'] == record_off['counts']
    manifest_on = json.loads((tmp_path / 'out_on' / 'review_assets.json').read_text())
    manifest_off = json.loads((tmp_path / 'out_off' / 'review_assets.json').read_text())
    assert manifest_on['cases'] == manifest_off['cases']


# ---- real_hotspot: T19 review follow-ups -------------------------------------------------------------------------

def mixed(tmp_path):
    return trh._mixed_root(tmp_path)


def test_adv1_admission_counts_a_confirmed_cno_in_its_layer_group(tmp_path):
    root = tmp_path / 'src'
    trh._source(root)
    trh._cno(root, 'lateralopen-CNO-L1-')   # same layer as the short case
    import_hotspots(root, tmp_path / 'on', include_cno=True)
    assignment = trh._cno_assignment(('L1',))
    assignment['max_group_mask_cells'] = 1270 * 1270 + 1   # one frame fits, two do not
    manifest = load_real_manifest(tmp_path / 'on')
    with pytest.raises(NumericContractError) as info:
        configured_dataset(tmp_path / 'on', manifest, assignment)
    assert info.value.code == 'oversized_real_mask_group'
    import_hotspots(root, tmp_path / 'off', include_cno=False)
    assert len(configured_dataset(tmp_path / 'off', load_real_manifest(tmp_path / 'off'), assignment).cases) == 1


def test_adv2_broken_cno_csv_has_root_relative_detail_and_a_top_level_diagnostic(tmp_path):
    root = mixed(tmp_path)
    (root / trh.CNO_GCNT / 'result/lateral_open.csv').write_text('wrong,header\n1,2\n', encoding='utf-8')
    import_hotspots(root, tmp_path / 'on', include_cno=True)
    manifest = trh._manifest_of(tmp_path / 'on')
    first = manifest['cno_cases'][0]
    assert first['csv']['status'] == 'invalid' and str(tmp_path) not in first['csv']['detail']
    assert first['csv']['detail'] == f'invalid_real_csv_header: {trh.CNO_GCNT}/result/lateral_open.csv'
    found = [d for d in manifest['diagnostics'] if d.get('status') == 'cno_csv_invalid']
    assert [d['case'] for d in found] == [trh.CNO_GCNT] and found[0]['reason'] == 'invalid_real_csv_header'
    assert str(tmp_path) not in json.dumps(manifest['diagnostics'])
    (case,) = [c for c in __import__('simcompare.compare2_inputs', fromlist=['x']).collect_cno(root)
               if c.case_name == trh.CNO_GCNT]
    assert str(tmp_path) not in case.detail


def test_adv3_cno_hotspot_outside_the_diameter_list_is_reported_and_may_leave_none(tmp_path):
    root = mixed(tmp_path)
    images = root / trh.CNO_PCNT / 'images'
    Image.new('L', (1270, 1270), 0).save(images / 'hotspot_lateral_open99.png')
    import_hotspots(root, tmp_path / 'on', include_cno=True)
    manifest = trh._manifest_of(tmp_path / 'on')
    unexpected = sorted(d['diameter_um'] for d in manifest['diagnostics']
                        if d.get('status') == 'unexpected_diameter' and d['case'] == trh.CNO_PCNT)
    assert unexpected == [99]
    ok = manifest['cno_cases'][1]
    assert ok['geometry_status'] == 'confirmed'
    (images / 'hotspot_lateral_open01.png').unlink()   # only the diameter outside the list is left
    import_hotspots(root, tmp_path / 'none', include_cno=True)
    manifest = trh._manifest_of(tmp_path / 'none')
    bad = manifest['cno_cases'][1]
    assert bad['geometry_status'] == 'unconfirmed' and 'no_hotspot_images' in bad['geometry_reasons']
    assert load_real_manifest(tmp_path / 'none') is not None


@pytest.mark.parametrize('broken', ['text', [1], [None], {'a': 1}, [{'case_name': 'x'}]])
def test_adv4_broken_cno_cases_structure_is_a_contract_error(tmp_path, broken):
    root = mixed(tmp_path)
    import_hotspots(root, tmp_path / 'on', include_cno=True)
    manifest = load_real_manifest(tmp_path / 'on')
    manifest['cno_cases'] = broken
    with pytest.raises(NumericContractError) as info:
        configured_dataset(tmp_path / 'on', manifest, trh._cno_assignment())
    assert info.value.code == 'invalid_real_cno_cases'


def test_adv5_hotspot_symlink_leaving_the_root_is_refused_before_it_is_opened(tmp_path):
    root = mixed(tmp_path)
    outside = tmp_path / 'outside.png'
    Image.new('L', (1270, 1270), 0).save(outside)
    link = root / trh.CNO_GCNT / 'images/hotspot_lateral_open01.png'
    link.unlink()
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip('symlinks are not available')
    with pytest.raises(NumericContractError) as info:
        import_hotspots(root, tmp_path / 'on', include_cno=True)
    assert info.value.code == 'missing_or_escaping_path'
