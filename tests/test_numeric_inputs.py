import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from simcompare.adapters.numeric import load_dataset, source_payload_hash
from simcompare.measurement import measure_full_mask
from simcompare.numeric_schema import NumericContractError

FIXTURE=Path(__file__).parent/'fixtures/numeric_v2'


@pytest.fixture
def package(tmp_path):
    root=tmp_path/'input'
    shutil.copytree(FIXTURE,root)
    return root


def rewrite(root, mutate):
    path=root/'manifest.json'
    manifest=json.loads(path.read_text())
    mutate(manifest)
    for name in manifest['files']:
        manifest['files'][name]['sha256']=hashlib.sha256((root/name).read_bytes()).hexdigest()
    manifest['pair_table_hash']=manifest['files']['pairs.csv']['sha256']
    manifest['input_hash']=source_payload_hash(manifest)
    manifest['source_manifest_hash']=manifest['input_hash']
    path.write_text(json.dumps(manifest))


def modify_csv(root, name, mutate):
    path=root/name
    with path.open() as f:
        reader=csv.DictReader(f);headers=reader.fieldnames;rows=list(reader)
    mutate(rows)
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,headers);writer.writeheader();writer.writerows(rows)
    rewrite(root,lambda m:None)


def test_accepted_t1_fixture_preserves_types_bindings_and_coverage():
    dataset=load_dataset(FIXTURE)
    lateral,forward,reverse=dataset.cases
    assert [c.failure_type for c in dataset.cases]==['lateral_short','vertical_short','vertical_short']
    assert lateral.pair_id==forward.pair_id==reverse.pair_id=='EXAMPLE_PAIR_AB'
    assert lateral.identity!=forward.identity
    assert forward.endpoints==tuple(reversed(reverse.endpoints))
    assert forward.endpoints[0].actual_layer=='EXAMPLE_M1'
    assert forward.endpoints[1].actual_layer=='EXAMPLE_M2'
    assert forward.raw['fab_pair_key']=='EXAMPLE_UNIT:M1-M2'
    assert reverse.raw['fab_pair_key']=='EXAMPLE_UNIT:M2-M1'
    assert forward.raw['assigned_process_layer']=='EXAMPLE_STEP_M2'
    assert len(lateral.records)==len(forward.records)==len(reverse.records)==32
    assert measure_full_mask(lateral.records[1]).ca_um2==0
    assert measure_full_mask(lateral.records[40]).status=='unavailable'
    assert measure_full_mask(forward.records[1]).status=='not_applicable'
    assert forward.records[3].input_status=='observed'
    assert all(g['status']=='pending' and g['answer'] is None for g in dataset.gates.values())
    assert dataset.unit_domain['divisor'] is None and dataset.unit_domain['conversion_policy'] is None
    assert dataset.panel['unit_count'] is None
    assert dataset.density[0].raw_value==0 and dataset.density[0].observed
    assert dataset.density[30].raw_value is None and not dataset.density[30].observed
    assert all(r.density_per_um2 is None and r.surveyed_area_um2 is None for r in dataset.density)
    assert dataset.provenance['input_hash']==json.loads((FIXTURE/'manifest.json').read_text())['input_hash']


@pytest.mark.parametrize('converted',[False,True])
def test_duplicate_case_diameters_rejected_even_after_conversion(package,converted):
    def change(m):
        entry=copy.deepcopy(m['cases'][0]['records'][4])
        if converted:
            del entry['diameter_um'];entry.update(diameter=5000,diameter_unit='nm')
        m['cases'][0]['records'].append(entry)
    rewrite(package,change)
    with pytest.raises(NumericContractError,match='duplicate_case_diameter'):
        load_dataset(package)


@pytest.mark.parametrize('change,code',[
    (lambda m:m['cases'].append(copy.deepcopy(m['cases'][0])),'duplicate_case_identity'),
    (lambda m:m['cases'][1]['records'][0].update(applicability_evidence=None),'not_applicable_evidence_required'),
    (lambda m:m['cases'][1].update(height_evidence=None),'height_evidence_required'),
    (lambda m:m['cases'][1].update(analysis_layer='EXAMPLE_M1'),'upper_layer_assignment_mismatch'),
    (lambda m:m['cases'][0]['records'].pop(),'incomplete_case_coverage'),
    (lambda m:m['cases'][0]['records'][4].update(value_semantics='probability'),'unsupported_mask_semantics'),
    (lambda m:m['cases'][0]['records'][4].update(mask=[[0.5,0,0,0],[0,0,0,0]]),'nonbinary_nominal_mask'),
    (lambda m:m['cases'][1].update(source_contract_version='1.2',raw={**m['cases'][1]['raw'],'assigned_process_layer':'SOURCE_STEP_RAW'}),'migration_assignment_conflict'),
    (lambda m:m['cases'][0].update(failure_type='lateral_open'),'unsupported_numeric_failure_type'),
    (lambda m:m['frames'][0].update(length_unit='nm'),'canonical_frame_unit_mismatch'),
    (lambda m:m['panel'].update(panel_height_um=-1),'invalid_number'),
    (lambda m:m['unit_domain'].update(measured_width_um=3),'measured_domain_mismatch'),
])
def test_invalid_case_frame_or_domain_reports_specific_error(package,change,code):
    rewrite(package,change)
    with pytest.raises(NumericContractError,match=code):
        load_dataset(package)


def test_external_asset_same_ca_and_source_conservation_report(package):
    digest=hashlib.sha256((package/'mask_255.npy').read_bytes()).hexdigest()
    def change(m):
        frame=m['frames'][0];frame.update(shape_yx=[2,2],pixel_pitch_um=0.5,origin_xy_um=[0,0],valid=[[1,1],[1,1]],expected_domain_bbox_um=[0,0,1,1])
        m['unit_domain'].update(measured_width_um=1,measured_height_um=1)
        for c in m['cases']:
            for r in c['records']:
                if r['input_status']=='observed':
                    r.update(mask=None,asset={'path':'mask_255.npy','sha256':digest})
        entry=m['cases'][0]['records'][4]
        entry['source_ca']={'value':1,'unit':'um^2','frame_id':'EXAMPLE_FRAME',
                            'valid_hash':hashlib.sha256(bytes([1,1,1,1])).hexdigest(),'basis':'full_mask_valid'}
    rewrite(package,change)
    dataset=load_dataset(package);record=dataset.cases[0].records[5]
    result=measure_full_mask(record)
    assert result.status=='unavailable' and result.reasons==('conservation_failed',)
    assert result.residual_um2==-0.5 and result.source_ca_um2==1
    assert record.mask.tolist()==[[True,False],[False,True]]
    assert measure_full_mask(dataset.cases[0].records[6]).ca_um2==0.5


def test_unmapped_pair_remains_in_expected_cases(package):
    modify_csv(package,'pairs.csv',lambda rows:rows.pop(0))
    dataset=load_dataset(package)
    assert len(dataset.cases)==3
    assert all(c.classification is None and c.reasons==('unmapped_pair',) for c in dataset.cases)


@pytest.mark.parametrize('name,change,code',[
    ('pairs.csv',lambda rows:rows.append(rows[0].copy()),'duplicate_or_conflicting_pair'),
    ('pairs.csv',lambda rows:rows[0].update(pair_table_version='other'),'pair_table_version_mismatch'),
    ('density.csv',lambda rows:rows.append(rows[0].copy()),'invalid_or_duplicate_density_diameter'),
    ('density.csv',lambda rows:rows[1].update(raw_value='-1'),'invalid_number'),
    ('density.csv',lambda rows:rows[1].update(raw_value='nan'),'invalid_number'),
    ('density.csv',lambda rows:rows[1].update(raw_value_unit='per_um2'),'inconsistent_density_distribution'),
    ('density.csv',lambda rows:rows[1].update(density_per_um2='10',canonical_unit='um^-2'),'density_conversion_not_supported_in_t2'),
    ('density.csv',lambda rows:rows[1].update(bin_lower_um='5',bin_upper_um='4'),'invalid_density_bin'),
    ('density.csv',lambda rows:rows.pop(),'incomplete_density_coverage'),
])
def test_table_errors_without_density_or_bin_guessing(package,name,change,code):
    modify_csv(package,name,change)
    with pytest.raises(NumericContractError,match=code):
        load_dataset(package)


def test_hash_and_json_integrity(package):
    with (package/'pairs.csv').open('a') as f:f.write('tampered\n')
    with pytest.raises(NumericContractError,match='input_hash_mismatch'):
        load_dataset(package)
    (package/'manifest.json').write_text('{"schema_version":"numeric_v2","schema_version":"1.2"}')
    with pytest.raises(NumericContractError,match='duplicate_json_key'):
        load_dataset(package)
    (package/'manifest.json').write_text('{"schema_version":"numeric_v2","value":NaN}')
    with pytest.raises(NumericContractError,match='nonfinite_json_number'):
        load_dataset(package)


def test_real_and_legacy_inputs_are_explicitly_blocked(package):
    with pytest.raises(NumericContractError,match='synthetic_input_in_real_mode'):
        load_dataset(package,mode='real')
    rewrite(package,lambda m:m.update(is_synthetic=False))
    with pytest.raises(NumericContractError,match='real_source_format_unconfirmed'):
        load_dataset(package,mode='real')
    rewrite(package,lambda m:m.update(schema_version='1.2'))
    with pytest.raises(NumericContractError,match='unsupported_source_contract'):
        load_dataset(package)


def test_imports_and_loader_do_not_touch_legacy_modules_in_fresh_interpreter():
    # tests/conftest.py already imports legacy code; only a fresh process proves isolation.
    script='''
import importlib.abc, sys
class BlockLegacy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'simcompare.schema','simcompare.preprocess','simcompare.adapters.example'}:
            raise AssertionError('legacy runtime import: '+fullname)
sys.meta_path.insert(0,BlockLegacy())
import simcompare.numeric_schema, simcompare.numeric_images, simcompare.measurement
from simcompare.adapters.numeric import load_dataset
assert len(load_dataset(sys.argv[1]).cases)==3
assert not {'simcompare.schema','simcompare.preprocess','simcompare.adapters.example'} & sys.modules.keys()
'''
    env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')}
    result=subprocess.run([sys.executable,'-c',script,str(FIXTURE.resolve())],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_pending_gate_rejects_supplied_2x2_divisor(package):
    rewrite(package,lambda m:m['unit_domain'].update(divisor=4,conversion_policy='divide_by_units'))
    with pytest.raises(NumericContractError,match='unconfirmed_unit_conversion'):
        load_dataset(package)


def test_raw_frame_units_are_converted_without_resizing(package):
    def change(m):
        frame=m['frames'][0]
        del frame['origin_xy_um'];del frame['pixel_pitch_um']
        frame.update(length_unit='nm',pixel_pitch=1000,origin_xy=[100000,200000])
    rewrite(package,change)
    source=load_dataset(package).cases[0].records[5]
    assert source.frame.pixel_pitch_um==1 and source.frame.origin_xy_um==(100,200)
    assert source.mask.shape==(2,4)
    assert measure_full_mask(source).ca_um2==2


def test_blank_or_extra_csv_cells_rejected(package):
    with (package/'pairs.csv').open('a') as f:f.write('bad,row\n')
    rewrite(package,lambda m:None)
    with pytest.raises(NumericContractError,match='invalid_csv_row'):
        load_dataset(package)
