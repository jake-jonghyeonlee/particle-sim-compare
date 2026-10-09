"""Native file integrity and composite joins, with hand-written negative cases."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from simcompare.dl_results import create_exchange, read, read_results, sha, write
from simcompare.numeric_schema import NumericContractError

FIXTURE=Path(__file__).parent/'fixtures/handoff'


def copied(tmp_path):
    shutil.copytree(FIXTURE,tmp_path/'exchange')
    return tmp_path/'exchange'


def reseal(root):
    return create_exchange(root/'exchange.json',context=root/'numeric_context.json',dl1=root/'dl1_search.json',
        dl1_fingerprint=read(root/'dl1_search.json')['fingerprint'],dl1_bindings=root/'dl1_bindings.json',
        dl2=root/'dl2_search.json',dl2_index=root/'dl2_index.json',dl2_expected=read(root/'dl2_expected.json'),dl2_cohort=root/'cohort.json',force=True)


def test_native_results_scores_are_adjacent_and_never_numeric_inputs():
    context=read(FIXTURE/'numeric_context.json')
    result=read_results(FIXTURE/'exchange.json')
    assert result['numeric']==context
    assert result['dl1']['status']=='available' and result['dl2']['status']=='available'
    assert result['status']=='pending' and {'G1','G2','G4'} <= set(result['pending_gates'])
    assert {r['search_scope'] for r in [*result['dl2']['primary'],*result['dl2']['supplementary']]}=={'primary','supplementary'}
    assert result['interpretation']=='similarity_only_never_numeric_risk'
    by_id={r['roi_id']:r for r in context['records']}
    for row in [*result['dl2']['primary'],*result['dl2']['supplementary']]:
        assert row['original_values']==by_id[row['roi_id']]['original_values']
        assert row['source_identity']['product_id']==row['product_id']
        if row['source_identity']['product_id']=='PRODUCT' and len(row['original_values'])==1:
            # Both case masks cover a 4x4 block, at pitch 0.5: 16 * 0.25 = 4.
            original=row['original_values'][0]
            assert original['diameter_um']==5
            assert original['original_case_ca']['measurement']['ca_um2']==4
            assert original['line']['value']==4 and original['pixel']['value']==0
    before=deepcopy(context)
    assert read_results(FIXTURE/'exchange.json')['numeric']==before


def test_original_count_map_files_preserve_hand_checked_values():
    import numpy as np
    context=read_results(FIXTURE/'exchange.json')['numeric']
    layer=next(r for r in context['original_electrode_map']['layers'] if r['key']['product_id']=='PRODUCT')
    with np.load(FIXTURE/layer['array_file'],allow_pickle=False) as arrays:
        # AB and AC cover the same 4x4 region; distinct electrodes are A, B, C.
        assert arrays['counts'].shape==(8,10)
        assert arrays['counts'][2,4]==3 and arrays['counts'][0,0]==0


def test_numeric_map_binary_tamper_is_rejected(tmp_path):
    root=copied(tmp_path);context=read(root/'numeric_context.json')
    path=root/next(iter(context['original_map_files']))
    path.write_bytes(path.read_bytes()+b'tampered')
    with pytest.raises(NumericContractError,match='original_numeric_map_hash_mismatch'):
        read_results(root/'exchange.json')


def test_native_hash_tamper_rejected_even_same_shape(tmp_path):
    root=copied(tmp_path)
    native=read(root/'dl2_search.json');native['primary'][0]['score']=0.125
    write(root/'dl2_search.json',native)
    with pytest.raises(NumericContractError,match='dl_native_file_hash_mismatch'):
        read_results(root/'exchange.json')


@pytest.mark.parametrize('field,value,reason',[
    ('schema_version','psdl2_index_v999','schema_mismatch'),
    ('checkpoint_fingerprint','0'*64,'checkpoint_fingerprint_mismatch'),
    ('view_fingerprint','1'*64,'view_fingerprint_mismatch')])
def test_model_schema_and_transform_pin_not_shape(field,value,reason,tmp_path):
    root=copied(tmp_path); index=read(root/'dl2_index.json');index[field]=value
    write(root/'dl2_index.json',index)
    with pytest.raises(NumericContractError,match=reason):reseal(root)


def test_dl1_config_pin(tmp_path):
    root=copied(tmp_path); profile=read(root/'exchange.json');profile['dl1']['config_fingerprint']='f'*64
    write(root/'exchange.json',profile)
    with pytest.raises(NumericContractError,match='dl1_fingerprint_mismatch'):read_results(root/'exchange.json')


def test_repeated_case_id_does_not_join_other_product(tmp_path):
    root=copied(tmp_path); context=read(root/'numeric_context.json')
    assert {r['source_identity']['product_id'] for r in context['records'] if r['source_identity']['case_id']=='A'}=={'PRODUCT','OTHER'}
    # Remove OTHER's record. PRODUCT/A must never serve as fallback for OTHER/A.
    context['records']=[r for r in context['records'] if r['source_identity']['product_id']!='OTHER']
    write(root/'numeric_context.json',context);reseal(root)
    with pytest.raises(NumericContractError,match='numeric_composite_identity_mismatch'):
        read_results(root/'exchange.json')


@pytest.mark.parametrize('field',['design_version','source_id','process_step_id','exposure_id','source_contract_version','frame_id'])
def test_every_source_identity_component_is_required(field,tmp_path):
    root=copied(tmp_path);context=read(root/'numeric_context.json')
    for row in context['records']:row['source_identity'][field]='different_context'
    write(root/'numeric_context.json',context);reseal(root)
    with pytest.raises(NumericContractError,match='numeric_composite_identity_mismatch'):
        read_results(root/'exchange.json')


def test_origin_changed_after_export_never_joins(tmp_path):
    root=copied(tmp_path); context=read(root/'numeric_context.json')
    for row in context['records']:row['frame']['origin_xy_um']=[101,200]
    write(root/'numeric_context.json',context);reseal(root)
    with pytest.raises(NumericContractError,match='numeric_frame_mismatch'):read_results(root/'exchange.json')


def test_native_origin_changed_after_export_rejected_even_with_new_file_seal(tmp_path):
    root=copied(tmp_path)
    index=read(root/'dl2_index.json');native=read(root/'dl2_search.json')
    for row in index['records']:row['frame']['origin_xy_um']=[101,200]
    for row in [native['query'],*native['primary'],*native['supplementary']]:
        row['frame']['origin_xy_um']=[101,200]
    write(root/'negative_dl2_index_origin.json',index)
    write(root/'negative_dl2_search_origin.json',native)
    create_exchange(root/'negative_exchange.json',context=root/'numeric_context.json',
                    dl2=root/'negative_dl2_search_origin.json',dl2_index=root/'negative_dl2_index_origin.json',
                    dl2_expected=read(root/'dl2_expected.json'),dl2_cohort=root/'cohort.json',force=True)
    with pytest.raises(NumericContractError,match='numeric_frame_mismatch'):
        read_results(root/'negative_exchange.json')


@pytest.mark.parametrize('field', ['source_version','source_assets'])
def test_source_version_and_asset_hash_are_not_case_only(field,tmp_path):
    root=copied(tmp_path); context=read(root/'numeric_context.json')
    for row in context['records']:
        if field=='source_version':row[field]='wrong_original_version'
        else:row[field]['cnt']['sha256']='0'*64
    write(root/'numeric_context.json',context);reseal(root)
    with pytest.raises(NumericContractError,match='numeric_'+field+'_mismatch'):read_results(root/'exchange.json')


def test_missing_dl_and_pending_gates_keep_original_values(tmp_path):
    root=copied(tmp_path)
    create_exchange(root/'pending.json',context=root/'numeric_context.json',pending_gates=['G4_real_geometry'])
    result=read_results(root/'pending.json')
    assert result['status']=='pending'
    assert result['dl1']['status']=='unavailable' and result['dl1']['value'] is None
    assert result['dl2']['reasons']==['dl_result_missing']
    assert result['numeric']==read(root/'numeric_context.json')


def test_unbound_product_is_unavailable_not_guessed(tmp_path):
    root=copied(tmp_path);p=read(root/'exchange.json');p['dl1']['bindings']=None;write(root/'exchange.json',p)
    result=read_results(root/'exchange.json')
    assert result['dl1']['status']=='partial'
    assert all(r['original_values'] is None and r['reasons']==['dl1_product_source_context_required'] for r in result['dl1']['matches'])


def test_multicase_region_both_original_rows_survive():
    export=read(FIXTURE/'review_rois.json'); result=read_results(FIXTURE/'exchange.json')
    regions=[r for r in export['rois'] if r['source_kind']=='electrode_count_region']
    assert len(regions)==2 and {r['case_id'] for r in regions}=={'A','B'}
    assert len({r['roi_id'] for r in regions})==2
    assert len({r['source_region_id'] for r in regions})==1
    preserved={r['roi_id']:r for r in result['numeric']['records']}
    for r in regions:
        assert preserved[r['roi_id']]['source_identity']==r['source_identity']
        assert preserved[r['roi_id']]['frame']['origin_xy_um']==[100,200]
        assert preserved[r['roi_id']]['source_assets']==r['source_assets']


def test_cli_failure_does_not_leave_join_result(tmp_path):
    root=copied(tmp_path); out=root/'joined.json';out.write_text('stale')
    p=read(root/'exchange.json');p['schema_version']='wrong';write(root/'exchange.json',p)
    result=subprocess.run([sys.executable,'-m','simcompare.dl_results','read',str(root/'exchange.json'),'--out',str(out)],capture_output=True,text=True)
    assert result.returncode==2 and 'schema_mismatch' in result.stdout and not out.exists()


def test_score_not_from_approved_index_rejected_in_seal_and_read(tmp_path):
    root=copied(tmp_path);native=read(root/'dl2_search.json')
    native['primary'][0]['score']=0.25
    write(root/'dl2_search.json',native)
    with pytest.raises(NumericContractError,match='dl2_scores_do_not_match_index'):reseal(root)
    profile=read(root/'exchange.json');profile['dl2']['result']['sha256']=sha(root/'dl2_search.json')
    write(root/'exchange.json',profile)
    with pytest.raises(NumericContractError,match='dl2_scores_do_not_match_index'):read_results(root/'exchange.json')


def test_cohort_and_joined_scope_coverage_are_preserved(tmp_path):
    root=copied(tmp_path);r=read_results(root/'exchange.json')['dl2']
    assert r['primary_cohort']==['PRODUCT'] and r['expanded'] is True
    assert len(r['primary'])==2 and len(r['supplementary'])==1 and 'matches' not in r
    assert r['coverage']['query']['near']['valid_fraction']==1
    write(root/'cohort.json',['OTHER'])
    with pytest.raises(NumericContractError,match='hash_mismatch'):read_results(root/'exchange.json')
    with pytest.raises(NumericContractError,match='cohort_mismatch'):reseal(root)


def test_missing_rejected_has_named_error(tmp_path):
    root=copied(tmp_path);native=read(root/'dl2_search.json');native.pop('rejected');write(root/'dl2_search.json',native)
    with pytest.raises(NumericContractError,match='dl2_rejected_field_required'):reseal(root)


@pytest.mark.parametrize('command',['seal','context'])
def test_failed_creation_preserves_preexisting_output_and_assets(command,tmp_path):
    out=tmp_path/'valuable.json';out.write_text('preserve exactly')
    args=['--context',str(tmp_path/'missing')] if command=='seal' else [str(tmp_path/'missing'),str(tmp_path/'missing'),'--asset-manifest','missing.json']
    for extra in ([],['--force']):
        r=subprocess.run([sys.executable,'-m','simcompare.dl_results',command,*args,'--out',str(out),*extra],capture_output=True,text=True)
        assert r.returncode==2 and out.read_text()=='preserve exactly'
        assert not (tmp_path/'numeric_map').exists()


def test_unknown_map_diameter_is_explicit_unavailable(tmp_path):
    from simcompare.dl_results import create_context
    root=copied(tmp_path);roi=read(root/'review_rois.json')
    for row in roi['rois']:
        if row['source_kind']=='electrode_count_region':row['provenance']['representative_diameter_um']=999
    write(root/'negative_roi_diameter.json',roi)
    context=create_context(root/'numeric',root/'negative_roi_diameter.json',root/'new_context.json',
                           asset_manifest={'path':'review_assets.json','sha256':sha(root/'numeric/review_assets.json')})
    rows=[r for r in context['records'] if not r['original_values']]
    assert len(rows)==2
    assert all(r['numeric_status']=='unavailable' and r['numeric_reasons']==['representative_diameter_group_unavailable'] for r in rows)


def test_dl1_same_product_binding_is_user_statement_documented_limitation(tmp_path):
    root=copied(tmp_path);context=read(root/'numeric_context.json')
    b=next(r for r in context['records'] if r['source_identity']['product_id']=='PRODUCT' and r['source_identity']['case_id']=='B')
    bindings=read(root/'dl1_bindings.json')
    for binding in bindings:
        if binding['product_id']=='PRODUCT':
            for field in ('roi_id','source_identity','frame','source_assets','source_version'):binding[field]=deepcopy(b[field])
    write(root/'dl1_bindings.json',bindings);reseal(root)
    result=read_results(root/'exchange.json')
    # N7 is documentation only: C cannot prove the DL1 hash-to-asset correspondence.
    assert result['dl1']['status']=='available'
    product=next(r for r in result['dl1']['matches'] if r['product_id']=='PRODUCT')
    assert all(r['source_identity']['case_id']=='B' for r in product['original_values'])
