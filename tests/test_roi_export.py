"""손계산 물리 좌표/두 source, 원본 asset 경계 및 stable ROI identity."""
import copy
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import pytest

from simcompare.adapters.numeric import load_dataset
from simcompare.electrode_map import compute_electrode_maps
from simcompare.roi_export import check_asset_manifest, export_review_rois

FIXTURE=Path(__file__).parent/'fixtures/review_roi_v1'
EXPECTED=json.loads((FIXTURE/'expected.json').read_text())


def asset_reference(root):
    return {'path':'review_assets.json','sha256':hashlib.sha256((root/'review_assets.json').read_bytes()).hexdigest()}


def selections():
    common={'product_id':'PRODUCT','design_version':'DESIGN','frame_id':'FRAME'}
    return {'map_region_ids':'all','manual':[
        {**common,'case_id':'A','center_xy_um':[102.5,201.5]},
        {**common,'center_xy_um':[102.5,201.5]}]}


def exported(tmp_path,root=FIXTURE,*,selection=None,dataset=None):
    dataset=load_dataset(root) if dataset is None else dataset
    maps=compute_electrode_maps(dataset, min_count=3)
    result=export_review_rois(dataset,root,tmp_path,maps=maps,selections=selections() if selection is None else selection,
                              asset_manifest=asset_reference(root),producer_commit='hand_fixture_commit')
    return result,json.loads((tmp_path/'review_rois.json').read_text())


def test_both_sources_match_hand_fixture_and_multi_case_rows(tmp_path):
    result,payload=exported(tmp_path)
    assert result['exit_code']==3 and result['n_rois']==4 and result['n_ready']==1
    assert payload['schema_version']=='review_roi_v1'
    assert payload['producer_commit']=='hand_fixture_commit'
    assert payload['source_manifest_hash']==load_dataset(FIXTURE).provenance['input_hash']
    maps=[r for r in payload['rois'] if r['source_kind']=='electrode_count_region']
    assert sorted(r['case_id'] for r in maps)==EXPECTED['map_cases']
    assert len({r['roi_id'] for r in maps})==2
    assert len({r['source_region_id'] for r in maps})==1
    for row in maps:
        assert row['region_bbox_um']==EXPECTED['region_bbox_um']
        assert row['center_xy_um']==EXPECTED['center_xy_um']
        assert row['status']==EXPECTED['map_status'] and row['reasons']==EXPECTED['map_reasons']
        assert row['frame']['origin_xy_um']==EXPECTED['origin_xy_um']
        assert row['frame']['pixel_pitch_um']==EXPECTED['pixel_pitch_um']
        assert row['frame']['shape_yx']==EXPECTED['shape_yx']
        assert row['provenance']['coverage']['valid_area_um2']==EXPECTED['valid_area_um2']
        assert row['provenance']['coverage']['region_valid_area_um2']==EXPECTED['region_area_um2']
    manual=[r for r in payload['rois'] if r['source_kind']=='manual']
    ready=next(r for r in manual if r['status']=='ready')
    assert ready['case_id']==EXPECTED['manual_case']
    assert ready['center_xy_um']==EXPECTED['manual_center_xy_um'] and ready['source_region_id'] is None
    assert ready['region_bbox_um'] is None
    blocked=next(r for r in manual if r['status']=='blocked')
    assert blocked['case_id'] is None and blocked['reasons']==EXPECTED['missing_manual_reasons']
    assert len(payload['excluded_records'])==3
    assert all(row['crop_recipe']['value'] is None for row in payload['rois'])
    assert payload['gates']['G4']['answer'] is None


def test_original_relative_assets_hash_valid_and_no_hotspot_ca_tensor_input(tmp_path):
    before={p:p.read_bytes() for p in FIXTURE.rglob('*') if p.is_file()}
    _,payload=exported(tmp_path)
    row=next(r for r in payload['rois'] if r['status']=='ready')
    assert set(row['source_assets'])=={'layouts','cnt','valid','via'}
    refs=[e['asset'] for e in row['source_assets']['layouts']]+[row['source_assets']['cnt'],row['source_assets']['valid']]
    for ref in refs:
        assert not Path(ref['path']).is_absolute() and '..' not in Path(ref['path']).parts
        assert ref['sha256']==hashlib.sha256((FIXTURE/ref['path']).read_bytes()).hexdigest()
        assert ref['observed'] is True
    valid=np.load(FIXTURE/row['source_assets']['valid']['path'])
    assert valid[0,0]==0 and valid[2,4]==1
    assert row['source_assets']['layouts'][0]['endpoint']==row['endpoints'][0]
    assert row['source_version']=='fixture_original_v1'
    encoded=json.dumps(row['source_assets'])
    assert not any(word in encoded for word in ('hotspot','ca_um2','density','rate'))
    assert before=={p:p.read_bytes() for p in before}


def test_ids_and_rows_stable_when_source_and_selection_order_permuted(tmp_path):
    dataset=load_dataset(FIXTURE)
    _,a=exported(tmp_path/'a',dataset=dataset)
    dataset.cases.reverse()
    selection=selections();selection['manual'].reverse()
    _,b=exported(tmp_path/'b',dataset=dataset,selection=selection)
    assert a==b


def test_manual_explicit_endpoint_context_and_missing_frame_are_not_guessed(tmp_path):
    dataset=load_dataset(FIXTURE)
    eps=[vars(e) for e in dataset.cases[1].endpoints]
    _,payload=exported(tmp_path,selection={'manual':[
        {'product_id':'PRODUCT','design_version':'DESIGN','frame_id':'FRAME','endpoints':eps,'center_xy_um':[103,202]},
        {'product_id':'PRODUCT','design_version':'DESIGN','case_id':'A','center_xy_um':[103,202]}]})
    ready=next(r for r in payload['rois'] if r['status']=='ready')
    assert ready['case_id']=='B'
    missing=next(r for r in payload['rois'] if r['status']=='blocked')
    assert 'source_frame_required' in missing['reasons'] and missing['case_id']=='A'
    assert missing['frame'] is None


@pytest.mark.parametrize('change,reason',[
    ('parent','relative_path_escape'),('absolute','absolute_or_nonportable_path'),
    ('symlink','missing_or_escaping_path'),('hash','input_hash_mismatch'),
    ('shape','source_asset_frame_mismatch'),('valid','source_valid_mismatch'),
    ('endpoint','source_asset_endpoint_binding_mismatch'),('missing_shape','source_shape_required'),
    ('hotspot','unsupported_review_asset_kind'),('frame','source_asset_frame_mismatch'),
])
def test_asset_boundary_failures_block_case_instead_of_guessing(tmp_path,change,reason):
    root=tmp_path/'source';shutil.copytree(FIXTURE,root)
    path=root/'review_assets.json';manifest=json.loads(path.read_text())
    entry=manifest['cases'][0];ref=entry['assets']['layouts'][0]['asset']
    if change=='parent':ref['path']='../escape.npy'
    elif change=='absolute':ref['path']=str((root/'assets/layout_a.npy').resolve())
    elif change=='symlink':
        outside=tmp_path/'outside.npy';shutil.copyfile(root/'assets/layout_a.npy',outside)
        (root/'assets/layout_a.npy').unlink();(root/'assets/layout_a.npy').symlink_to(outside)
    elif change=='hash':ref['sha256']='0'*64
    elif change=='shape':
        np.save(root/'assets/layout_a.npy',np.ones((1,1),np.uint8))
        ref['sha256']=hashlib.sha256((root/'assets/layout_a.npy').read_bytes()).hexdigest()
    elif change=='valid':
        np.save(root/'assets/other_valid.npy',np.ones((8,10),np.uint8))
        entry['assets']['valid']={**entry['assets']['valid'],'path':'assets/other_valid.npy',
                                 'sha256':hashlib.sha256((root/'assets/other_valid.npy').read_bytes()).hexdigest()}
    elif change=='endpoint':entry['assets']['layouts'][0]['endpoint']['role']='wrong'
    elif change=='missing_shape':entry['assets']['layouts']=[]
    elif change=='hotspot':entry['assets']['hotspot']=ref
    elif change=='frame':entry['frame']['origin_xy_um']=[0,0]
    path.write_text(json.dumps(manifest))
    _,payload=exported(tmp_path/'out',root=root)
    manual=next(r for r in payload['rois'] if r['source_kind']=='manual' and r['case_id']=='A')
    assert manual['status']=='blocked' and reason in manual['reasons']
    assert manual['source_assets'] is None


def test_missing_asset_manifest_and_case_never_become_ready(tmp_path):
    dataset=load_dataset(FIXTURE)
    maps=compute_electrode_maps(dataset, min_count=3)
    export_review_rois(dataset,FIXTURE,tmp_path,maps=maps,selections=selections(),producer_commit='test')
    payload=json.loads((tmp_path/'review_rois.json').read_text())
    assert all(row['status']=='blocked' for row in payload['rois'])
    assert all('source_asset_manifest_required' in row['reasons'] for row in payload['rois'])


@pytest.mark.parametrize('center,reason',[([99,200],'position_outside_source_frame'),
                                          ([100.1,200.1],'position_outside_valid_domain'),
                                          ([float('nan'),202],'invalid_number'),([], 'physical_center_required')])
def test_manual_coordinates_require_observed_original_position(tmp_path,center,reason):
    _,payload=exported(tmp_path,selection={'manual':[{'product_id':'PRODUCT','design_version':'DESIGN',
        'frame_id':'FRAME','case_id':'A','center_xy_um':center}]})
    row=payload['rois'][0]
    assert row['status']=='blocked' and reason in row['reasons']


def test_unknown_case_and_invalid_center_are_blocked_together(tmp_path):
    _,payload=exported(tmp_path,selection={'manual':[{'product_id':'PRODUCT','design_version':'DESIGN',
        'frame_id':'FRAME','center_xy_um':[float('nan'),202]}]})
    row=payload['rois'][0]
    assert row['status']=='blocked' and row['case_id'] is None
    assert {'case_context_required','invalid_number'}<=set(row['reasons'])
    assert row['center_xy_um'] is None


def test_stale_map_case_and_frame_bindings_are_blocked(tmp_path):
    from dataclasses import replace
    from simcompare.numeric_schema import Frame
    dataset=load_dataset(FIXTURE)
    maps=compute_electrode_maps(dataset, min_count=3)
    frame=dataset.frames['FRAME']
    stale=Frame('FRAME',.5,(99,200),frame.shape_yx,frame.valid)
    layer=replace(maps.layers[0],regions=tuple(replace(r,frame=stale) for r in maps.layers[0].regions))
    maps=replace(maps,layers=(layer,))
    export_review_rois(dataset,FIXTURE,tmp_path/'frame',maps=maps,selections={'map_region_ids':'all'},
                       asset_manifest=asset_reference(FIXTURE),producer_commit='test')
    payload=json.loads((tmp_path/'frame/review_rois.json').read_text())
    assert all('region_source_frame_mismatch' in row['reasons'] for row in payload['rois'])
    dataset.cases.pop()
    export_review_rois(dataset,FIXTURE,tmp_path/'case',maps=maps,selections={'map_region_ids':'all'},
                       asset_manifest=asset_reference(FIXTURE),producer_commit='test')
    payload=json.loads((tmp_path/'case/review_rois.json').read_text())
    row=next(row for row in payload['rois'] if row['case_id']=='B')
    assert 'case_context_required' in row['reasons'] and row['status']=='blocked'


def test_asset_frame_bool_cannot_impersonate_unit_pitch_or_one_row(tmp_path):
    from simcompare.synthetic import generate_numeric_demo
    generate_numeric_demo(tmp_path/'demo')
    root=tmp_path/'demo/reference'
    dataset=load_dataset(root)
    path=root/'review_assets.json'
    manifest=json.loads(path.read_text())
    manifest['cases'][0]['frame']['pixel_pitch_um']=True  # True == 1, 물리 길이로는 허용 불가
    path.write_text(json.dumps(manifest))
    checked,reasons=check_asset_manifest(dataset,root,asset_reference(root))
    assert reasons==[]
    info=checked[tuple(vars(dataset.cases[0].identity).values())]
    assert info['assets'] is None and 'source_asset_frame_mismatch' in info['reasons']


def test_manual_default_and_map_requires_explicit_threshold(tmp_path):
    from simcompare.synthetic import generate_numeric_demo
    generate_numeric_demo(tmp_path/'demo')
    root=tmp_path/'demo/reference';dataset=load_dataset(root)
    case=dataset.cases[0]
    manual={**vars(case.identity),'center_xy_um':[100.5,200.5]}
    maps=compute_electrode_maps(dataset)
    assert maps.layers[0].regions==()
    result=export_review_rois(dataset,root,tmp_path/'manual',maps=maps,selections={'manual':[manual]},
                             asset_manifest=asset_reference(root),producer_commit='test')
    assert result['n_ready']==1 and result['exit_code']==0
    payload=json.loads((tmp_path/'manual/review_rois.json').read_text())
    assert payload['rois'][0]['source_kind']=='manual' and payload['rois'][0]['crop_recipe']['value'] is None
    blocked=export_review_rois(dataset,root,tmp_path/'no_threshold',maps=maps,selections={'map_region_ids':'all'},
                              asset_manifest=asset_reference(root),producer_commit='test')
    assert blocked['n_rois']==0 and blocked['exit_code']==3
    payload=json.loads((tmp_path/'no_threshold/review_rois.json').read_text())
    assert payload['excluded_records'][0]['reasons']==['map_threshold_required']
    maps=compute_electrode_maps(dataset,min_count=3)
    ready=export_review_rois(dataset,root,tmp_path/'threshold',maps=maps,selections={'map_region_ids':'all'},
                            asset_manifest=asset_reference(root),producer_commit='test')
    assert ready['n_ready']==4 and ready['exit_code']==0


def test_r2_commit_outside_checkout_has_reason_and_does_not_capture_parent_git(tmp_path,monkeypatch):
    import subprocess
    import simcompare.roi_export as producer
    foreign=tmp_path/'foreign';foreign.mkdir()
    subprocess.run(['git','init',str(foreign)],check=True,capture_output=True)
    (foreign/'package').mkdir()
    subprocess.run(['git','-C',str(foreign),'-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-m','fixture'],check=True,capture_output=True)
    monkeypatch.setattr(producer,'__file__',str(foreign/'package/src/simcompare/roi_export.py'))
    dataset=load_dataset(FIXTURE)
    producer.export_review_rois(dataset,FIXTURE,tmp_path/'out',selections={'manual':[]},asset_manifest=asset_reference(FIXTURE))
    payload=json.loads((tmp_path/'out/review_rois.json').read_text())
    assert payload['producer_commit'] is None
    assert payload['producer_commit_reason']=='producer_not_in_git_checkout'
