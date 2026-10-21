import csv
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest
import yaml

from simcompare.adapters.real_hotspot import import_hotspots, load_real_manifest
from simcompare.numeric_schema import NumericContractError
from simcompare.png_strict import read_gray_hotspot


def _source(root, case='lateral_short-A-L1-B-L1', *, shape=(2270, 2270),
            gray1=255, gray2=0, csv_area='0.01'):
    folder = root / 'images' / case
    (folder / 'result').mkdir(parents=True)
    Image.new('L', (shape[1], shape[0]), 0).save(folder/'1.png')
    Image.new('L', (shape[1], shape[0]), 0).save(folder/'2.png')
    corner = np.zeros((shape[0]-1000, shape[1]-1000), dtype=np.uint8)
    corner[0, 0] = gray1
    Image.fromarray(corner).save(folder/'hotspot_lateral_short01.png')
    Image.fromarray(np.zeros_like(corner) + gray2).save(folder/'hotspot_lateral_short02.png')
    with (folder/'result/lateral_short.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['defect_size(um)', 'Area(um2)'])
        writer.writerow(['1', csv_area])
        writer.writerow(['2', '0'])
    return folder


def _run(*args):
    return subprocess.run([sys.executable, '-m', 'simcompare', *map(str, args)],
                          capture_output=True, text=True)


def _assignment(pair_table=True):
    return {'product_id': 'PRODUCT', 'design_version': 'DESIGN', 'source_id': 'SOURCE',
            'process_step_id': 'STEP', 'exposure_id': 'EXPOSURE', 'unit_id': 'UNIT',
            'layer_stack': [{'actual_layer': 'L1', 'stack_order': 1, 'density_process_layer': 'P1'}],
            **({'pair_table_path': 'pairs.csv'} if pair_table else {})}


def _write_pairs(folder):
    (folder/'pairs.csv').write_text('pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\n'
                                    'AB,A,B,line,pair-v1\n', encoding='utf-8')


def test_import_validate_compare_map_observation_first_cli(tmp_path):
    roots, imported = [], []
    for name in ('reference', 'target'):
        root = tmp_path/name
        _source(root, csv_area='0.014')  # pixel area and source rounding agree within stated tolerance
        roots.append(root)
        out = tmp_path/f'{name}_import'
        assert _run('import-hotspots', '--root', root, '--out', out).returncode == 3
        _write_pairs(out)
        imported.append(out)
    cfg = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'out',
           'reference': {'data_dir': str(imported[0]), 'condition_assignment': _assignment()},
           'target': {'data_dir': str(imported[1]), 'condition_assignment': _assignment()},
           'map': {'input': {'data_dir': str(imported[0]), 'condition_assignment': _assignment(pair_table=False)},
                   'diameter_um': 1}}
    config = tmp_path/'config.yaml'
    config.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    assert _run('validate-data', '--config', config).returncode == 3
    validation = json.loads((tmp_path/'out/validation.json').read_text())
    assert validation['is_synthetic'] is False
    assert validation['physical_metadata']['height_um'] is None
    assert validation['expected_case_coverage_status'] == 'unconfirmed'
    assert 'expected_case_inventory_unprovided' in validation['reasons']
    assert validation['raw_observations_status'] == 'available'
    one = next(r for r in validation['records'] if r['diameter_um'] == 1)
    zero = next(r for r in validation['records'] if r['diameter_um'] == 2)
    missing = next(r for r in validation['records'] if r['diameter_um'] == 3)
    assert one['area_um2'] == 0.01 and one['csv_area_um2'] == 0.014
    assert one['area_difference_um2'] == pytest.approx(-0.004)
    assert zero['input_status'] == 'observed' and zero['area_um2'] == 0.0
    assert missing['input_status'] == 'missing' and missing['area_um2'] is None
    assert _run('compare', '--config', config).returncode == 3
    comparison = json.loads((tmp_path/'out/comparison.json').read_text())
    assert comparison['is_synthetic'] is False
    assert comparison['raw_case_areas_status'] == 'available'
    assert comparison['weighted_rates_status'] == 'unavailable'
    assert comparison['reference']['groups']
    assert not any('conservation_failed' in source['measurement']['reasons']
                   for group in comparison['reference']['groups'] for source in group['source_cases'])
    assert _run('electrode-map', '--config', config).returncode == 3
    mapped = json.loads((tmp_path/'out/electrode_map.json').read_text())
    assert mapped['is_synthetic'] is False
    assert mapped['status'] in ('partial', 'available')
    assert 'counts' in mapped['layers'][0]
    assert 'electrode_polarity_unknown' not in mapped.get('reasons', [])
    # No pair table keeps source CA and map observations while classification stays incomplete.
    cfg['reference']['condition_assignment'] = _assignment(pair_table=False)
    cfg['target']['condition_assignment'] = _assignment(pair_table=False)
    config.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    assert _run('compare', '--config', config).returncode == 3
    partial = json.loads((tmp_path/'out/comparison.json').read_text())
    group = next(g for g in partial['reference']['groups'] if g['group']['diameter_um'] == 1)
    assert group['union']['coverage']['unmapped'] == 1
    assert group['union']['value'] is None
    assert group['source_cases'][0]['measurement']['ca_um2'] == pytest.approx(0.01)


def test_crop_frame_corner_and_non_square_shape(tmp_path):
    root = tmp_path/'src'
    _source(root, shape=(2270, 2280))
    out = tmp_path/'imported'
    import_hotspots(root, out)
    manifest = load_real_manifest(out)
    assert manifest['frame']['shape_yx'] == [1270, 1280]
    assert manifest['frame']['origin_xy_um'] == [50.0, 50.0]
    record = manifest['cases'][0]['records'][0]
    with np.load(out/record['mask_path']) as arc:
        mask = arc['mask']
    assert mask.shape == (1270, 1280) and mask[0, 0]
    assert record['png_area_um2'] == 0.01


@pytest.mark.parametrize('mode', ['RGB', 'RGBA'])
def test_gray_reader_rejects_color_or_alpha(tmp_path, mode):
    path = tmp_path/'bad.png'
    Image.new(mode, (2, 2), (255, 0, 0) if mode == 'RGB' else (255, 255, 255, 0)).save(path)
    with pytest.raises(NumericContractError):
        read_gray_hotspot(path, 'bad.png')


def test_gray_reader_accepts_equal_channel_rgb_as_occupancy(tmp_path):
    path = tmp_path/'gray-rgb.png'
    Image.new('RGB', (2, 1), (128, 128, 128)).save(path)
    assert read_gray_hotspot(path, 'gray-rgb.png').tolist() == [[True, True]]


def test_gray_reader_rejects_crc_damage(tmp_path):
    path = tmp_path/'crc.png'
    Image.new('L', (2, 2), 128).save(path)
    data = bytearray(path.read_bytes())
    data[-1] ^= 0x01
    path.write_bytes(data)
    with pytest.raises(NumericContractError, match='png_crc_mismatch'):
        read_gray_hotspot(path, 'crc.png')


def test_case_local_csv_and_leading_zero_duplicate_are_auditable(tmp_path):
    root = tmp_path/'src'
    folder = _source(root)
    # A matching CSV elsewhere must not satisfy the case-local requirement.
    (folder/'result/lateral_short.csv').unlink()
    (root/'result').mkdir()
    (root/'result/lateral_short.csv').write_text('defect_size(um),Area(um2)\n1,0.01\n')
    imported = tmp_path/'imported'
    import_hotspots(root, imported)
    manifest = load_real_manifest(imported)
    assert manifest['cases'][0]['csv']['status'] == 'missing'
    Image.new('L', (1270, 1270), 0).save(folder/'hotspot_lateral_short03.png')
    Image.new('L', (1270, 1270), 0).save(folder/'hotspot_lateral_short3.png')
    with pytest.raises(NumericContractError, match='duplicate_real_hotspot_diameter'):
        import_hotspots(root, tmp_path/'duplicate')


def test_missing_grouping_assignments_only_allow_inspection(tmp_path):
    root = tmp_path/'src'
    _source(root)
    imported = tmp_path/'imported'
    import_hotspots(root, imported)
    config = tmp_path/'config.yaml'
    config.write_text(yaml.safe_dump({'schema_version': 'numeric_config_v1', 'mode': 'real',
                                     'output_dir': 'out',
                                     'reference': {'data_dir': str(imported)},
                                     'target': {'data_dir': str(imported)}}))
    assert _run('compare', '--config', config).returncode == 3
    report = json.loads((tmp_path/'out/comparison.json').read_text())
    assert report['classified_comparison_status'] == 'unavailable'


def test_vertical_black_is_observed_without_invented_height(tmp_path):
    root = tmp_path/'src'
    folder = _source(root)
    vertical = folder.with_name('vertical_short-A-L1-B-L2')
    folder.rename(vertical)
    for old in list(vertical.glob('hotspot_*.png')):
        old.rename(vertical/old.name.replace('lateral_short', 'vertical_short'))
    (vertical/'result/lateral_short.csv').rename(vertical/'result/vertical_short.csv')
    out = tmp_path/'imported'
    import_hotspots(root, out)
    manifest = load_real_manifest(out)
    case = manifest['cases'][0]
    assert case['height_um'] is None and case['height_evidence'] is None
    assert case['records'][1]['input_status'] == 'observed'
    assert case['records'][1]['png_area_um2'] == 0.0


def test_configured_aggregation_has_explicit_oversized_group_guard(tmp_path):
    root = tmp_path/'src'
    _source(root)
    imported = tmp_path/'imported'
    import_hotspots(root, imported)
    config = tmp_path/'config.yaml'
    assignment = _assignment(pair_table=False)
    assignment['max_group_mask_cells'] = 100
    cfg = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'out',
           'reference': {'data_dir': str(imported), 'condition_assignment': assignment},
           'target': {'data_dir': str(imported), 'condition_assignment': assignment}}
    config.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    result = _run('compare', '--config', config)
    assert result.returncode == 2
    error = json.loads((tmp_path/'out/error.json').read_text())
    assert 'oversized_real_mask_group' in error['reasons']


def test_same_name_endpoints_remain_auditable_and_unresolved(tmp_path):
    root = tmp_path/'src'
    _source(root, case='lateral_short-A-L1-A-L1')
    imported = tmp_path/'imported'
    import_hotspots(root, imported)
    config = tmp_path/'config.yaml'
    assignment = _assignment(pair_table=False)
    cfg = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'out',
           'reference': {'data_dir': str(imported), 'condition_assignment': assignment},
           'target': {'data_dir': str(imported), 'condition_assignment': assignment}}
    config.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    assert _run('compare', '--config', config).returncode == 3
    report = json.loads((tmp_path/'out/comparison.json').read_text())
    assert 'same_name_cases_unresolved' in report['reasons']
    assert any('same_electrode_name_both_endpoints_short_unresolved' in group['union']['reasons']
               for group in report['reference']['groups'])
    assert report['records']
    assert report['records'][0]['case_id'] == 'lateral_short-A-L1-A-L1'


def test_resealed_source_semantics_and_nonbinary_assets_rejected(tmp_path):
    import hashlib
    import json
    out = tmp_path/'imported'
    root = tmp_path/'source'
    _source(root, shape=(1002, 1002))
    import_hotspots(root, out)
    manifest_path = out/'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['frame']['origin_xy_um'] = [0, 0]
    payload = {k:v for k,v in manifest.items() if k not in ('source_payload_hash','manifest_hash_recipe')}
    manifest['source_payload_hash'] = hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(NumericContractError, match='real_frame_contract_mismatch'):
        load_real_manifest(out)

    case_binding = tmp_path/'case_binding'
    import_hotspots(root, case_binding)
    manifest_path = case_binding/'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['cases'][0]['endpoints'][0]['electrode_id'] = 'Z'
    payload = {k:v for k,v in manifest.items() if k not in ('source_payload_hash','manifest_hash_recipe')}
    manifest['source_payload_hash'] = hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(NumericContractError, match='invalid_real_case_endpoints'):
        load_real_manifest(case_binding)

    status_binding = tmp_path/'status_binding'
    import_hotspots(root, status_binding)
    manifest_path = status_binding/'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['cases'][0]['records'][0]['input_status'] = 'missing'
    payload = {k:v for k,v in manifest.items() if k not in ('source_payload_hash','manifest_hash_recipe')}
    manifest['source_payload_hash'] = hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(NumericContractError, match='invalid_missing_real_record'):
        load_real_manifest(status_binding)

    valid = tmp_path/'valid_import'
    import_hotspots(root, valid)
    manifest_path = valid/'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    record = manifest['cases'][0]['records'][0]
    asset = valid/record['mask_path']
    np.savez_compressed(asset, mask=np.array([[-7., 0.], [0., 0.]]))
    record['mask_sha256'] = hashlib.sha256(asset.read_bytes()).hexdigest()
    payload = {k:v for k,v in manifest.items() if k not in ('source_payload_hash','manifest_hash_recipe')}
    manifest['source_payload_hash'] = hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(NumericContractError, match='real_mask_binary_contract_mismatch'):
        load_real_manifest(valid)


def test_csv_mismatch_is_partial_and_rounded_match_is_accepted(tmp_path):
    from simcompare.adapters.real_hotspot import configured_dataset
    from simcompare.linepixel import compute_areas
    bad_root, bad = tmp_path/'bad_source', tmp_path/'bad_import'
    _source(bad_root, shape=(1002,1002), csv_area='100.00')
    expected=tmp_path/'expected.txt'; expected.write_text('lateral_short-A-L1-B-L1\n')
    import_hotspots(bad_root,bad,expected_cases=expected); _write_pairs(bad)
    group = next(g for k,g in compute_areas(configured_dataset(bad,load_real_manifest(bad),_assignment())).groups.items() if k.diameter_um==1)
    source = group.source_cases[0]
    assert group.union.status == 'partial' and group.union.value is None
    assert 'csv_area_mismatch' in group.union.reasons
    assert source.raw['records'][0]['csv_area']['raw_area'] == '100.00'
    assert source.measurement.status == 'partial' and source.measurement.partial_value == pytest.approx(.01)
    from simcompare.electrode_map import compute_electrode_maps
    mapped = compute_electrode_maps(configured_dataset(bad,load_real_manifest(bad),_assignment()),diameter_um=1).layers[0]
    assert mapped.status == 'partial' and 'csv_area_mismatch' in mapped.reasons
    assert any('csv_area_mismatch' in item['reasons'] for item in mapped.coverage['measurement_errors'])

    cfg={'schema_version':'numeric_config_v1','mode':'real','output_dir':'reports',
         'reference':{'data_dir':str(bad),'condition_assignment':_assignment()},
         'target':{'data_dir':str(bad),'condition_assignment':_assignment()}}
    config=tmp_path/'compare.yaml'; config.write_text(yaml.safe_dump(cfg),encoding='utf-8')
    assert _run('compare','--config',config).returncode == 3
    report=json.loads((tmp_path/'reports/comparison.json').read_text())
    row=next(r for r in report['records'] if r.get('side')=='reference' and r.get('diameter_um')==1)
    assert row['csv_status']=='mismatch' and row['csv_raw_value']=='100.00'
    assert row['area_difference_um2']==pytest.approx(-99.99) and row['area_tolerance_um2']==pytest.approx(.01)

    good_root, good = tmp_path/'good_source', tmp_path/'good_import'
    _source(good_root, shape=(1002,1002), csv_area='0.014')
    import_hotspots(good_root,good,expected_cases=expected); _write_pairs(good)
    rounded = next(g for k,g in compute_areas(configured_dataset(good,load_real_manifest(good),_assignment())).groups.items() if k.diameter_um==1)
    assert rounded.union.status == 'available' and rounded.union.value == pytest.approx(.01)


def test_expected_missing_case_materialized_into_compare_and_map_coverage(tmp_path):
    from simcompare.adapters.real_hotspot import configured_dataset
    from simcompare.electrode_map import compute_electrode_maps
    from simcompare.linepixel import compute_areas
    root, out = tmp_path/'source', tmp_path/'imported'
    _source(root, shape=(1002,1002))
    expected = tmp_path/'expected.txt'
    expected.write_text('lateral_short-A-L1-B-L1\nlateral_short-A-L1-C-L1\n')
    import_hotspots(root,out,expected_cases=expected); _write_pairs(out)
    ds = configured_dataset(out,load_real_manifest(out),_assignment())
    group = next(g for k,g in compute_areas(ds).groups.items() if k.diameter_um==1)
    assert group.coverage.expected == 2 and group.coverage.missing == 1
    assert group.union.status == 'partial' and 'expected_case_folder_missing' in group.union.reasons
    mapped = compute_electrode_maps(ds,diameter_um=1).layers[0]
    assert mapped.status == 'partial' and mapped.coverage['missing']


def test_per_case_geometry_is_not_promoted_by_another_case(tmp_path):
    from simcompare.adapters.real_hotspot import configured_dataset
    from simcompare.electrode_map import compute_electrode_maps
    root,out = tmp_path/'source',tmp_path/'imported'
    first = _source(root, shape=(1002,1002))
    (first/'2.png').unlink()
    _source(root,case='lateral_short-A-L1-C-L1',shape=(1002,1002))
    import_hotspots(root,out); _write_pairs(out)
    ds = configured_dataset(out,load_real_manifest(out),_assignment())
    unconfirmed = next(c for c in ds.cases if c.identity.case_id.endswith('B-L1'))
    assert 'case_geometry_unconfirmed' in unconfirmed.records[1.0].reasons
    mapped = compute_electrode_maps(ds,diameter_um=1).layers[0]
    assert mapped.status == 'partial' and mapped.coverage['missing']

    no_geometry_root,no_geometry_out=tmp_path/'no_geometry_source',tmp_path/'no_geometry_import'
    only = _source(no_geometry_root,shape=(1002,1002))
    (only/'1.png').unlink(); (only/'2.png').unlink()
    import_hotspots(no_geometry_root,no_geometry_out)
    raw_manifest=load_real_manifest(no_geometry_out)
    assert raw_manifest['frame']['shape_yx'] is None
    assert raw_manifest['cases'][0]['records'][0]['input_status']=='observed'
    with pytest.raises(NumericContractError,match='real_common_frame_shape_required'):
        configured_dataset(no_geometry_out,raw_manifest,_assignment(pair_table=False))


def test_real_operation_byte_budget_and_legacy_role_required(tmp_path):
    import json
    import shutil
    from simcompare.adapters.real_hotspot import configured_dataset
    from simcompare.adapters.numeric import load_dataset, source_payload_hash
    root,out = tmp_path/'source',tmp_path/'imported'
    _source(root,shape=(1002,1002)); import_hotspots(root,out)
    assignment = _assignment(pair_table=False); assignment['max_operation_bytes'] = 1
    with pytest.raises(NumericContractError,match='oversized_real_operation'):
        configured_dataset(out,load_real_manifest(out),assignment)
    legacy=tmp_path/'legacy'
    shutil.copytree(Path(__file__).parent/'fixtures/numeric_v2',legacy)
    path=legacy/'manifest.json'; manifest=json.loads(path.read_text())
    manifest['cases'][0]['endpoints'][0]['role']=None
    manifest['cases'][0]['raw']['ordered_endpoints'][0]['role']=None
    manifest['input_hash']=source_payload_hash(manifest);manifest['source_manifest_hash']=manifest['input_hash']
    path.write_text(json.dumps(manifest))
    with pytest.raises(NumericContractError,match='legacy_endpoint_role_required'):
        load_dataset(legacy)


def test_validate_reports_invalid_target_and_bad_assignment_by_identity(tmp_path):
    import json
    from simcompare.pipeline import run_numeric_validate
    root,out=tmp_path/'source',tmp_path/'imported'
    _source(root,shape=(1002,1002)); import_hotspots(root,out)
    cfg={'mode':'real','reference':{'data_dir':str(out)},
         'target':{'data_dir':str(tmp_path/'missing-target')},
         'map':{'input':{'data_dir':str(out),'condition_assignment':{'broken':True}}},
         'output_dir':str(tmp_path/'reports')}
    result=run_numeric_validate(cfg,out=tmp_path/'reports')
    payload=json.loads((tmp_path/'reports/validation.json').read_text())
    assert result['exit_code']==3
    assert {d['input'] for d in payload['diagnostics']} == {'target','map.input'}
    assert all(d['status']=='invalid' for d in payload['diagnostics'])


def _reseal_real_manifest(folder, change):
    import hashlib
    manifest_path = folder/'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    change(manifest)
    payload = {k:v for k,v in manifest.items() if k not in ('source_payload_hash','manifest_hash_recipe')}
    manifest['source_payload_hash'] = hashlib.sha256(json.dumps(
        payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))


def test_resealed_csv_precision_cannot_override_raw_decimal_contract(tmp_path):
    root,out = tmp_path/'source',tmp_path/'imported'
    _source(root,shape=(1002,1002),csv_area='100.00')
    import_hotspots(root,out)
    def tamper(manifest):
        record=manifest['cases'][0]['records'][0]
        record['csv_area']['area_rounding_half_unit']=100
        manifest['cases'][0]['csv']['rows']['1']['area_rounding_half_unit']=100
        record['area_tolerance_um2']=100.005
        record['csv_status']='consistent'
        manifest['expected_cases']=[manifest['cases'][0]['case_name']]
        manifest['expected_case_coverage_status']='confirmed'
    _reseal_real_manifest(out,tamper)
    with pytest.raises(NumericContractError,match='real_csv_raw_value_mismatch'):
        load_real_manifest(out)


def test_missing_inventory_groups_are_guarded_before_materialization(tmp_path):
    from simcompare.adapters.real_hotspot import configured_dataset
    root,out = tmp_path/'source',tmp_path/'imported'
    _source(root,shape=(1002,1002))
    expected=tmp_path/'expected.txt'
    expected.write_text('lateral_short-A-L1-B-L1\n'+''.join(
        f'lateral_short-A-L{i}-B-L{i}\n' for i in range(2,102)))
    import_hotspots(root,out,expected_cases=expected)
    _write_pairs(out)
    assignment=_assignment()
    assignment['layer_stack']=[{'actual_layer':f'L{i}','stack_order':i,
                                'density_process_layer':f'P{i}'} for i in range(1,102)]
    assignment['max_operation_bytes']=2000
    with pytest.raises(NumericContractError,match='oversized_real_operation'):
        configured_dataset(out,load_real_manifest(out),assignment)
    import simcompare.adapters.real_hotspot as real_hotspot
    real_hotspot_memory = real_hotspot._physical_memory_bytes
    try:
        real_hotspot._physical_memory_bytes = lambda: 64*1024**3
        assignment['max_operation_bytes']=64*1024**3+1
        with pytest.raises(NumericContractError,match='invalid_resource_limit'):
            configured_dataset(out,load_real_manifest(out),assignment)
        assignment['max_operation_bytes']=0
        with pytest.raises(NumericContractError,match='invalid_resource_limit'):
            configured_dataset(out,load_real_manifest(out),assignment)
        # 8 GiB를 넘는 명시 값도 물리 메모리 안이면 받는다.
        assignment['max_operation_bytes']=16*1024**3
        configured_dataset(out,load_real_manifest(out),assignment)
    finally:
        real_hotspot._physical_memory_bytes = real_hotspot_memory


def test_map_inventory_uncertainty_is_partial_but_pair_table_is_not_required(tmp_path):
    from simcompare.adapters.real_hotspot import configured_dataset
    from simcompare.electrode_map import compute_electrode_maps
    root,out = tmp_path/'source',tmp_path/'imported'
    _source(root,shape=(1002,1002))
    import_hotspots(root,out)
    layer=compute_electrode_maps(configured_dataset(out,load_real_manifest(out),
        _assignment(pair_table=False)),diameter_um=1).layers[0]
    assert layer.status=='partial' and layer.coverage['source_coverage_status']=='partial'
    assert layer.coverage['source_coverage_summary']['expected_case_inventory_status']=='unconfirmed'
    assert layer.coverage['source_coverage_summary']['observed_case_count']==1
    assert layer.counts is not None and int(layer.counts.sum())==2
    # Once inventory is explicit, missing pair classification does not block this map.
    expected=tmp_path/'expected_confirmed.txt'
    expected.write_text('lateral_short-A-L1-B-L1\n')
    confirmed=tmp_path/'confirmed'
    import_hotspots(root,confirmed,expected_cases=expected)
    mapped=compute_electrode_maps(configured_dataset(confirmed,load_real_manifest(confirmed),
        _assignment(pair_table=False)),diameter_um=1).layers[0]
    assert mapped.status=='available' and mapped.coverage['source_coverage_status']=='available'


def _nest_images(folder):
    images = folder / 'images'
    images.mkdir()
    for path in list(folder.glob('*.png')):
        path.rename(images / path.name)
    return images


def test_case_images_subfolder_and_unspaced_defect_alias(tmp_path):
    flat, nested = tmp_path / 'flat', tmp_path / 'nested'
    _source(flat)
    folder = _source(nested, case='lateralshort-A-L1-B-L1')
    _nest_images(folder)
    out_flat, out_nested = tmp_path / 'flat_out', tmp_path / 'nested_out'
    import_hotspots(flat, out_flat)
    result = import_hotspots(nested, out_nested)
    assert result['cases'] == 1
    case = json.loads((out_nested / 'manifest.json').read_text(encoding='utf-8'))['cases'][0]
    assert case['case_name'] == 'lateralshort-A-L1-B-L1' and case['failure_type'] == 'lateral_short'
    assert case['image_subdir'] == 'images' and case['csv']['status'] == 'observed'
    assert [r['input_status'] for r in case['records'][:2]] == ['observed', 'observed']
    assert case['records'][0]['source_path'].endswith('/images/hotspot_lateral_short01.png')
    assert case['geometry_status'] == 'confirmed' and case['crop_shape_yx'] == [1270, 1270]
    assert 'image_subdir' not in json.loads((out_flat / 'manifest.json').read_text(encoding='utf-8'))['cases'][0]
    loaded = load_real_manifest(out_nested)
    assert loaded is not None


def test_case_with_pngs_in_both_case_and_images_folder_is_ambiguous(tmp_path):
    folder = _source(tmp_path / 'src')
    _nest_images(folder)
    Image.new('L', (2270, 2270), 0).save(folder / '1.png')
    with pytest.raises(NumericContractError, match='ambiguous_real_images_location'):
        import_hotspots(tmp_path / 'src', tmp_path / 'out')


def test_real_csv_header_variant_is_accepted_and_others_rejected(tmp_path):
    root = tmp_path/'src'
    folder = _source(root)
    path = folder/'result/lateral_short.csv'
    path.write_text('DefectSize (um), Area(um^2)\n1.00, 0.01\n2.00, 0.00\n', encoding='utf-8')
    imported = tmp_path/'imported'
    import_hotspots(root, imported)
    csv_record = load_real_manifest(imported)['cases'][0]['csv']
    assert csv_record['status'] == 'observed'
    assert csv_record['rows'], csv_record
    path.write_text('DefectSize(um), Area(um^2)\n1.00, 0.01\n2.00, 0.00\n', encoding='utf-8')
    import_hotspots(root, tmp_path/'imported3')
    csv_record = load_real_manifest(tmp_path/'imported3')['cases'][0]['csv']
    assert csv_record['status'] == 'observed'
    path.write_text('Size,Area\n1,0.01\n', encoding='utf-8')
    with pytest.raises(NumericContractError, match='invalid_real_csv_header'):
        import_hotspots(root, tmp_path/'imported2')


def _pair_rows(tmp_path, rows):
    from simcompare.adapters.real_hotspot import configured_dataset
    root, imported = tmp_path/'src', tmp_path/'imp'
    _source(root)
    import_hotspots(root, imported)
    header = 'pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\n'
    (imported/'pairs.csv').write_text(header + ''.join(rows), encoding='utf-8')
    return configured_dataset(imported, load_real_manifest(imported), _assignment())


def test_reversed_pair_with_same_class_is_collapsed_and_conflicts_are_rejected(tmp_path):
    ok = ['AB,A,B,pixel,v1\n', 'BA,B,A,pixel,v1\n']
    _pair_rows(tmp_path/'ok', ok)
    for name, rows in (('class', ['AB,A,B,pixel,v1\n', 'BA,B,A,line,v1\n']),
                       ('version', ['AB,A,B,pixel,v1\n', 'BA,B,A,pixel,v2\n']),
                       ('id', ['AB,A,B,pixel,v1\n', 'AB,C,D,pixel,v1\n'])):
        with pytest.raises(NumericContractError, match='duplicate_or_conflicting_pair'):
            _pair_rows(tmp_path/name, rows)


def test_role_policy_covers_pair_table_electrodes_not_seen_in_cases(tmp_path):
    rows = ['AB,A,B,line,v1\n', 'CD,C,D,pixel,v1\n']
    from simcompare.adapters.real_hotspot import configured_dataset
    root, imported = tmp_path/'src', tmp_path/'imp'
    _source(root)
    import_hotspots(root, imported)
    (imported/'pairs.csv').write_text('pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\n' + ''.join(rows),
                                      encoding='utf-8')
    assignment = {**_assignment(), 'electrode_role_policy': 'electrode_id'}
    dataset = configured_dataset(imported, load_real_manifest(imported), assignment)
    assert {(e.electrode_id, e.role) for c in dataset.cases for e in c.endpoints} == {('A', 'A'), ('B', 'B')}
    assert dataset.provenance['electrode_roles_effective'] == {'A': 'A', 'B': 'B', 'C': 'C', 'D': 'D'}
    for bad, code in (({'electrode_role_policy': 'guess'}, 'invalid_real_electrode_role_policy'),
                      ({'electrode_role_policy': 'electrode_id', 'electrode_roles': {'A': 'x'}},
                       'electrode_roles_conflict_with_role_policy')):
        with pytest.raises(NumericContractError, match=code):
            configured_dataset(imported, load_real_manifest(imported), {**_assignment(), **bad})
    no_table = {k: v for k, v in assignment.items() if k != 'pair_table_path'}
    with pytest.raises(NumericContractError, match='electrode_role_policy_requires_pair_table'):
        configured_dataset(imported, load_real_manifest(imported), no_table)


def _first_record(manifest):
    return next(r for c in manifest['cases'] for r in c['records'] if r['input_status'] == 'observed')


def _lateral_import(tmp_path, name='imp'):
    root = tmp_path/'source'
    if not root.exists():
        _source(root, shape=(1002, 1002))
    out = tmp_path/name
    import_hotspots(root, out)
    return root, out


def test_import_writes_compressed_npz_and_roundtrips(tmp_path):
    root, out = _lateral_import(tmp_path)
    assert not list((out/'assets').glob('*.npy'))
    manifest = json.loads((out/'manifest.json').read_text())
    observed = [r for c in manifest['cases'] for r in c['records'] if r['input_status'] == 'observed']
    assert observed and all(r['mask_encoding'] == 'npz_deflate_bool_v1' and r['mask_path'].endswith('.npz') for r in observed)
    assert all('mask_encoding' not in r for c in manifest['cases'] for r in c['records'] if r['input_status'] == 'missing')
    loaded = load_real_manifest(out)
    assert loaded['cases'][0]['records'][0]['png_area_um2'] == observed[0]['png_area_um2']
    rec = _first_record(manifest)
    with np.load(out/rec['mask_path'], allow_pickle=False) as arc:
        stored = arc['mask']
    assert stored.dtype == bool and stored.shape == (2, 2) and stored[0, 0] and np.count_nonzero(stored) == 1


def test_legacy_npy_manifest_still_validates(tmp_path):
    import hashlib
    _, out = _lateral_import(tmp_path)
    manifest = json.loads((out/'manifest.json').read_text())
    for case in manifest['cases']:
        for rec in case['records']:
            if rec['input_status'] != 'observed':
                continue
            with np.load(out/rec['mask_path']) as arc:
                mask = arc['mask']
            old = out/rec['mask_path']
            new = old.with_suffix('.npy')
            np.save(new, mask)
            old.unlink()
            rec['mask_path'] = new.relative_to(out).as_posix()
            rec['mask_sha256'] = hashlib.sha256(new.read_bytes()).hexdigest()
            del rec['mask_encoding']
    (out/'manifest.json').write_text(json.dumps(manifest))
    _reseal_real_manifest(out, lambda m: None)
    from simcompare.adapters.real_hotspot import configured_dataset
    pairs_csv = 'pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\nAB,A,B,line,v1\nCD,C,D,pixel,v1\n'
    (out/'pairs.csv').write_text(pairs_csv, encoding='utf-8')
    legacy = configured_dataset(out, load_real_manifest(out), _assignment())
    assert legacy.cases
    fresh = tmp_path/'fresh'
    import_hotspots(tmp_path/'source', fresh)
    (fresh/'pairs.csv').write_text(pairs_csv, encoding='utf-8')
    current = configured_dataset(fresh, load_real_manifest(fresh), _assignment())
    pairs = 0
    for case_old, case_new in zip(legacy.cases, current.cases):
        assert set(case_old.records) == set(case_new.records)
        for diameter, rec_old in case_old.records.items():
            old_mask, new_mask = rec_old.mask, case_new.records[diameter].mask
            assert (old_mask is None) == (new_mask is None)
            if old_mask is not None:
                assert np.array_equal(old_mask, new_mask)
                pairs += 1
    assert pairs


def _tamper_asset(tmp_path, mutate_asset=None, mutate_record=None, rename_to=None):
    import hashlib
    _, out = _lateral_import(tmp_path)
    holder = {}

    def change(manifest):
        rec = _first_record(manifest)
        asset = out/rec['mask_path']
        if mutate_asset:
            mutate_asset(asset)
        if rename_to:
            new = asset.with_suffix(rename_to)
            asset.rename(new)
            asset = new
            rec['mask_path'] = new.relative_to(out).as_posix()
        if mutate_record:
            mutate_record(rec)
        rec['mask_sha256'] = hashlib.sha256(asset.read_bytes()).hexdigest()
    _reseal_real_manifest(out, change)
    return out


@pytest.mark.parametrize('kind,code', [
    ('bad_encoding', 'real_mask_encoding_invalid'),
    ('null_encoding', 'real_mask_encoding_invalid'),
    ('suffix_mismatch', 'real_mask_encoding_invalid'),
    ('extra_member', 'real_mask_encoding_invalid'),
    ('missing_member', 'real_mask_encoding_invalid'),
    ('non_bool', 'real_mask_binary_contract_mismatch'),
])
def test_tampered_mask_assets_rejected(tmp_path, kind, code):
    if kind == 'bad_encoding':
        out = _tamper_asset(tmp_path, mutate_record=lambda r: r.update(mask_encoding='gzip'))
    elif kind == 'null_encoding':
        out = _tamper_asset(tmp_path, mutate_record=lambda r: r.update(mask_encoding=None))
    elif kind == 'suffix_mismatch':
        out = _tamper_asset(tmp_path, rename_to='.npy')
    elif kind == 'extra_member':
        def extra(asset):
            with np.load(asset) as arc:
                mask = arc['mask']
            np.savez_compressed(asset, mask=mask, other=mask)
        out = _tamper_asset(tmp_path, mutate_asset=extra)
    elif kind == 'missing_member':
        def wrong(asset):
            with np.load(asset) as arc:
                mask = arc['mask']
            np.savez_compressed(asset, notmask=mask)
        out = _tamper_asset(tmp_path, mutate_asset=wrong)
    else:
        out = _tamper_asset(tmp_path, mutate_asset=lambda a: np.savez_compressed(a, mask=np.ones((2, 2), dtype=np.uint8)))
    with pytest.raises(NumericContractError, match=code):
        load_real_manifest(out)


def test_npz_member_with_non_npy_bytes_rejected(tmp_path):
    import zipfile
    def corrupt(asset):
        with zipfile.ZipFile(asset, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('mask.npy', b'this is not an npy payload')
    out = _tamper_asset(tmp_path, mutate_asset=corrupt)
    with pytest.raises(NumericContractError, match='real_mask_encoding_invalid'):
        load_real_manifest(out)


def test_npz_hash_mismatch_rejected(tmp_path):
    _, out = _lateral_import(tmp_path)
    rec = _first_record(json.loads((out/'manifest.json').read_text()))
    with (out/rec['mask_path']).open('ab') as f:
        f.write(b'x')
    with pytest.raises(NumericContractError, match='real_mask_hash_mismatch'):
        load_real_manifest(out)


def test_missing_record_must_not_carry_mask_encoding(tmp_path):
    _, out = _lateral_import(tmp_path)
    def change(m):
        rec = next(r for c in m['cases'] for r in c['records'] if r['input_status'] == 'missing')
        rec['mask_encoding'] = 'npz_deflate_bool_v1'
    _reseal_real_manifest(out, change)
    with pytest.raises(NumericContractError, match='invalid_missing_real_record'):
        load_real_manifest(out)


def test_large_sparse_mask_compresses_below_one_percent(tmp_path):
    root = tmp_path/'source'
    folder = _source(root, shape=(5000, 5000))
    big = np.zeros((4000, 4000), dtype=np.uint8)
    big[1000:1100, 2000:2100] = 255
    Image.fromarray(big).save(folder/'hotspot_lateral_short01.png')
    out = tmp_path/'imp'
    import_hotspots(root, out)
    sizes = [p.stat().st_size for p in (out/'assets').glob('*.npz')]
    assert sizes and max(sizes) < 0.01 * 16_000_000


def test_enospc_during_save_cleans_staging_and_surfaces_error(tmp_path, monkeypatch):
    import errno
    root = tmp_path/'source'
    _source(root, shape=(1002, 1002))
    def boom(*args, **kwargs):
        raise OSError(errno.ENOSPC, 'No space left on device')
    monkeypatch.setattr(np, 'savez_compressed', boom)
    out = tmp_path/'imp'
    with pytest.raises(OSError) as info:
        import_hotspots(root, out)
    assert info.value.errno == errno.ENOSPC
    assert not out.exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith('imp.tmp-')]


def test_enospc_after_partial_second_asset_cleans_staging(tmp_path, monkeypatch):
    import errno
    root = tmp_path/'source'
    _source(root, shape=(1002, 1002))
    real, calls = np.savez_compressed, []

    def flaky(file, *args, **kwargs):
        calls.append(str(file))
        if len(calls) == 1:
            return real(file, *args, **kwargs)
        Path(file).write_bytes(b'PK partial')
        raise OSError(errno.ENOSPC, 'No space left on device')
    monkeypatch.setattr(np, 'savez_compressed', flaky)
    out = tmp_path/'imp'
    with pytest.raises(OSError) as info:
        import_hotspots(root, out)
    assert info.value.errno == errno.ENOSPC
    assert len(calls) == 2
    assert not out.exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith('imp.tmp-')]


def test_stage1_runner_logs_import_error(tmp_path):
    runner_src = Path(__file__).resolve().parents[1]/'bundles/etx/simcompare_job_1_import_hotspots.py'
    text = runner_src.read_text(encoding='utf-8')
    root = tmp_path/'work'/'src'
    _source(root, shape=(1002, 1002))
    work = tmp_path/'work'
    start, end = text.index('JOBS = ['), text.index('LOG_FILE = ')
    jobs = "JOBS = [('import-hotspots', ['--root', 'src', '--out', 'imp'])]\n"
    runner = work/'runner.py'
    runner.write_text(text[:start] + jobs + text[end:], encoding='utf-8')
    hook = tmp_path/'hook'
    hook.mkdir()
    (hook/'sitecustomize.py').write_text(
        "import errno, numpy as np\n"
        "def boom(*a, **k):\n    raise OSError(errno.ENOSPC, 'No space left on device')\n"
        "np.savez_compressed = boom\n", encoding='utf-8')
    import os
    env = {**os.environ, 'PYTHONPATH': str(hook)}
    result = subprocess.run([sys.executable, str(runner)], capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert result.returncode != 0
    log = (work/'runs/simcompare_1_import_hotspots.log').read_text(encoding='utf-8')
    assert 'No space left on device' in log and 'No space left on device' in result.stderr
    assert not (work/'imp'/'manifest.json').exists() and not (work/'imp'/'assets').exists()
    assert not [p for p in work.iterdir() if p.name.startswith('imp.tmp-')]
