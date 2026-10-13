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


def test_read_refusal_without_force_keeps_the_existing_output(tmp_path):
    out=tmp_path/'joined.json';out.write_text('preserve exactly')
    r=subprocess.run([sys.executable,'-m','simcompare.dl_results','read',str(FIXTURE/'exchange.json'),'--out',str(out)],capture_output=True,text=True)
    assert r.returncode==2 and 'output_exists_use_force' in r.stdout
    assert out.read_text()=='preserve exactly'
    # With --force the same valid exchange replaces it.
    r=subprocess.run([sys.executable,'-m','simcompare.dl_results','read',str(FIXTURE/'exchange.json'),'--out',str(out),'--force'],capture_output=True,text=True)
    assert r.returncode==0 and out.read_text()!='preserve exactly'


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


@pytest.mark.parametrize('producer', ['dl1', 'dl2'])
@pytest.mark.parametrize('edit,reason', [
    ('reverse', 'matches_not_sorted'), ('tie', 'matches_not_sorted'),
    ('duplicate', 'duplicate_candidate'), ('self', 'query_self_match'),
    ('empty', 'status_matches_mismatch'), ('unavailable', 'status_matches_mismatch'),
])
def test_native_match_contract_seal_and_read(producer, edit, reason, tmp_path):
    root = copied(tmp_path)
    path = root/(producer+'_search.json')
    native = read(path)
    field = 'rankings' if producer == 'dl1' else 'primary'
    rows = native[field]
    if edit == 'reverse':
        rows.reverse()
    elif edit == 'tie':
        # Keep legitimate scores; edit the IDs/index consistently to expose tie order.
        if producer == 'dl1':
            for row in rows:
                row['score'] = 1.0
            rows.sort(key=lambda row: row['product_id'], reverse=True)
        else:
            index = read(root/'dl2_index.json')
            query_embedding = next(r['embedding'] for r in index['records'] if r['roi_id'] == native['query']['roi_id'])
            for row in rows:
                row['score'] = 1.0
                next(r for r in index['records'] if r['roi_id'] == row['roi_id'])['embedding'] = query_embedding
            rows.sort(key=lambda row: row['roi_id'], reverse=True)
            write(root/'dl2_index.json', index)
    elif edit == 'duplicate':
        rows.insert(1, deepcopy(rows[0]))
    elif edit == 'self':
        if producer == 'dl1':
            rows[0]['product_id'] = native['query_id']
        else:
            rows[0] = {**deepcopy(native['query']), 'score': 1.0, 'search_scope': 'primary', 'same_physical_case': True}
    elif edit == 'empty':
        native[field] = []
    else:
        native['status'] = 'unavailable'
    write(path, native)
    with pytest.raises(NumericContractError, match=producer+'_'+reason):
        reseal(root)
    # Rehash by hand so read reaches the contract check rather than byte integrity.
    profile = read(root/'exchange.json')
    profile[producer]['result']['sha256'] = sha(path)
    if producer == 'dl2':
        profile[producer]['index']['sha256'] = sha(root/'dl2_index.json')
    write(root/'exchange.json', profile)
    with pytest.raises(NumericContractError, match=producer+'_'+reason):
        read_results(root/'exchange.json')


def test_dl2_unavailable_can_have_supplementary_matches(tmp_path):
    root = copied(tmp_path)
    native = read(root/'dl2_search.json')
    native['primary'] = []
    native['status'] = 'unavailable'
    write(root/'dl2_search.json', native)
    reseal(root)
    result = read_results(root/'exchange.json')['dl2']
    assert result['status'] == 'unavailable' and result['supplementary']


@pytest.mark.parametrize('embedding', ['invalid embedding', '0.5'])
def test_string_embedding_has_named_contract_error(embedding, tmp_path):
    root = copied(tmp_path)
    index = read(root/'dl2_index.json')
    index['records'][0]['embedding'] = embedding
    write(root/'dl2_index.json', index)
    with pytest.raises(NumericContractError, match='invalid_dl2_index_embedding'):
        reseal(root)
    profile = read(root/'exchange.json')
    profile['dl2']['index']['sha256'] = sha(root/'dl2_index.json')
    write(root/'exchange.json', profile)
    with pytest.raises(NumericContractError, match='invalid_dl2_index_embedding'):
        read_results(root/'exchange.json')


def context_at(root, out, **kwargs):
    from simcompare.dl_results import create_context
    return create_context(root/'numeric', root/'review_rois.json', out,
                          asset_manifest={'path': 'review_assets.json', 'sha256': sha(root/'numeric/review_assets.json')}, **kwargs)


def test_context_force_removes_only_replaced_map_directory(tmp_path):
    root = copied(tmp_path)
    out = root/'new_context.json'
    context_at(root, out)
    unrelated = root/'numeric_map/unrelated'
    unrelated.mkdir(); (unrelated/'keep').write_text('keep')
    fixture_directories = {p for p in (root/'numeric_map').iterdir() if p.name != 'unrelated' and not p.name.startswith('new_context_')}
    context_at(root, out, force=True)
    assert len(list((root/'numeric_map').glob('new_context_*'))) == 1
    assert (unrelated/'keep').read_text() == 'keep'
    assert all(p.exists() for p in fixture_directories)
    assert all((root/path).is_file() for path in read(out)['original_map_files'])


def test_context_failure_after_npz_creation_cleans_only_new_assets(tmp_path, monkeypatch):
    import simcompare.dl_results as module
    root = copied(tmp_path)
    out = root/'new_context.json'
    context_at(root, out)
    before = out.read_bytes()
    directories = set((root/'numeric_map').iterdir())
    def fail_write(path, value, **kwargs):
        assert any(p not in directories and list(p.glob('*.npz')) for p in (root/'numeric_map').iterdir())
        raise OSError('injected failure after NPZ creation')
    monkeypatch.setattr(module, 'write', fail_write)
    with pytest.raises(OSError, match='after NPZ'):
        context_at(root, out, force=True)
    assert out.read_bytes() == before
    assert set((root/'numeric_map').iterdir()) == directories


def test_context_failure_during_npz_write_cleans_partial_assets(tmp_path, monkeypatch):
    import numpy as np
    root = copied(tmp_path)
    directories = set((root/'numeric_map').iterdir())
    save = np.savez_compressed
    def fail_save(path, **arrays):
        save(path, **arrays)
        assert Path(path).is_file()
        raise OSError('injected failure after NPZ creation')
    monkeypatch.setattr(np, 'savez_compressed', fail_save)
    with pytest.raises(OSError, match='after NPZ'):
        context_at(root, root/'failed_context.json')
    assert not (root/'failed_context.json').exists()
    assert set((root/'numeric_map').iterdir()) == directories


@pytest.mark.parametrize('reference', ['outside', 'symlink', 'unrelated'])
def test_context_force_never_reclaims_unowned_directory(reference, tmp_path):
    root = copied(tmp_path)
    out = root/'new_context.json'
    context_at(root, out)
    previous = read(out)
    protected = tmp_path/'protected' if reference != 'unrelated' else root/'numeric_map/unrelated'
    protected.mkdir(); (protected/'keep').write_text('preserve')
    if reference == 'outside':
        path = '../protected/keep'
    elif reference == 'symlink':
        link = root/('numeric_map/new_context_'+'a'*32)
        link.symlink_to(protected, target_is_directory=True)
        path = str(link.relative_to(root))+'/keep'
    else:
        path = 'numeric_map/unrelated/keep'
    previous['original_map_files'] = {path: sha(protected/'keep')}
    write(out, previous)
    context_at(root, out, force=True)
    assert (protected/'keep').read_text() == 'preserve'


def test_context_force_can_replace_corrupt_output_without_deleting_assets(tmp_path):
    root = copied(tmp_path)
    out = root/'new_context.json'; out.write_text('broken json')
    before = set((root/'numeric_map').iterdir())
    context_at(root, out, force=True)
    assert before <= set((root/'numeric_map').iterdir())
    assert read(out)['schema_version'] == 'numeric_dl_context_v1'


@pytest.mark.parametrize('producer,field', [('dl1', 'rankings'), ('dl2', 'primary'), ('dl2', 'supplementary')])
@pytest.mark.parametrize('kind,reason', [
    ('not_list', 'candidate_list_required'), ('not_dict', 'candidate_row_required'),
    ('missing_product', 'candidate_identifier_required'), ('mixed_product', 'candidate_identifier_required'),
    ('missing_score', 'invalid_similarity_score'), ('absurd_score', 'invalid_similarity_score'),
])
def test_malformed_native_candidates_have_named_errors_in_seal_and_read(tmp_path, producer, field, kind, reason):
    root = copied(tmp_path)
    path = root/(producer+'_search.json'); native = read(path)
    if kind == 'not_list': native[field] = {'not': 'a list'}
    elif kind == 'not_dict': native[field] = [None]
    elif kind == 'missing_product': native[field][0].pop('product_id')
    elif kind == 'mixed_product':
        native[field][0]['product_id'] = 7
        for row in native[field]: row['score'] = 1.0
    elif kind == 'missing_score': native[field][0].pop('score')
    else: native[field][0]['score'] = 10**400
    write(path, native)
    expected = reason if reason == 'invalid_similarity_score' else producer+'_'+reason
    with pytest.raises(NumericContractError, match=expected): reseal(root)
    profile = read(root/'exchange.json'); profile[producer]['result']['sha256'] = sha(path)
    write(root/'exchange.json', profile)
    with pytest.raises(NumericContractError, match=expected): read_results(root/'exchange.json')


@pytest.mark.parametrize('field', ['primary', 'supplementary', 'query'])
def test_missing_roi_identifier_has_named_contract_error(tmp_path, field):
    root = copied(tmp_path); path = root/'dl2_search.json'; native = read(path)
    (native[field] if field == 'query' else native[field][0]).pop('roi_id')
    write(path, native)
    reason = 'dl2_query_candidate_identifier_required' if field == 'query' else 'dl2_candidate_identifier_required'
    with pytest.raises(NumericContractError, match=reason): reseal(root)
    profile = read(root/'exchange.json'); profile['dl2']['result']['sha256'] = sha(path); write(root/'exchange.json', profile)
    with pytest.raises(NumericContractError, match=reason): read_results(root/'exchange.json')


@pytest.mark.parametrize('number', [10**400, float('inf'), float('nan'), 1e308])
def test_absurd_or_nonfinite_embeddings_have_named_errors(tmp_path, number):
    root = copied(tmp_path); path = root/'dl2_index.json'; index = read(path)
    index['records'][0]['embedding'] = [number, 1]
    # JSON's NaN/Infinity spellings are deliberately hand-written invalid fixtures.
    path.write_text(json.dumps(index))
    reason = 'nonfinite_dl_number' if isinstance(number, float) and not __import__('math').isfinite(number) else 'invalid_dl2_index_embedding'
    with pytest.raises(NumericContractError, match=reason): reseal(root)
    profile = read(root/'exchange.json'); profile['dl2']['index']['sha256'] = sha(path); write(root/'exchange.json', profile)
    with pytest.raises(NumericContractError, match=reason): read_results(root/'exchange.json')


def test_supplementary_self_match_is_rejected_without_primary_cohort(tmp_path):
    root = copied(tmp_path); native = read(root/'dl2_search.json')
    native.update(primary=[], primary_cohort=['OTHER'], status='unavailable',
                  supplementary=[{**deepcopy(native['query']), 'score': 1.0,
                                  'search_scope': 'supplementary', 'same_physical_case': True}])
    write(root/'dl2_search.json', native); write(root/'cohort.json', ['OTHER'])
    with pytest.raises(NumericContractError, match='dl2_query_self_match'): reseal(root)
    profile = read(root/'exchange.json')
    profile['dl2']['result']['sha256'] = sha(root/'dl2_search.json')
    profile['dl2']['cohort']['sha256'] = sha(root/'cohort.json')
    write(root/'exchange.json', profile)
    with pytest.raises(NumericContractError, match='dl2_query_self_match'): read_results(root/'exchange.json')


def old_context(out, references, schema='numeric_dl_context_v1'):
    write(out, {'schema_version': schema, 'original_map_files': {path: '0'*64 for path in references}})


def test_cleanup_stem_is_literal_even_with_regex_characters(tmp_path):
    from simcompare.dl_results import _replaced_map_directory
    out = tmp_path/'a.+.json'
    owned = 'numeric_map/a.+_'+'a'*32
    other = 'numeric_map/aXYZ_'+'b'*32
    (tmp_path/owned).mkdir(parents=True); (tmp_path/other).mkdir()
    old_context(out, [other+'/map.npz'])
    assert _replaced_map_directory(out) is None
    old_context(out, [owned+'/map.npz'])
    assert _replaced_map_directory(out) == tmp_path/owned


def test_cleanup_requires_one_directory_and_previous_context_schema(tmp_path):
    from simcompare.dl_results import _replaced_map_directory
    out = tmp_path/'c.json'
    first = 'numeric_map/c_'+'a'*32; second = 'numeric_map/c_'+'b'*32
    (tmp_path/first).mkdir(parents=True); (tmp_path/second).mkdir()
    old_context(out, [first+'/map.npz', second+'/map.npz'])
    assert _replaced_map_directory(out) is None
    old_context(out, [first+'/map.npz'], schema='other_schema')
    assert _replaced_map_directory(out) is None
    old_context(out, [first+'/map.npz', first+'/another.npz'])
    assert _replaced_map_directory(out) == tmp_path/first


def test_cleanup_never_follows_numeric_map_symlink_outside_root(tmp_path, monkeypatch):
    from simcompare.dl_results import _replaced_map_directory
    root = tmp_path/'output'; root.mkdir()
    outside = tmp_path/'outside'; outside.mkdir()
    victim = outside/('c_'+'a'*32); victim.mkdir(); (victim/'keep').write_text('keep')
    link = root/'numeric_map'; link.symlink_to(outside, target_is_directory=True)
    out = root/'c.json'; reference = 'numeric_map/'+victim.name+'/keep'
    old_context(out, [reference])
    resolve = Path.resolve
    def no_follow(path, *args, **kwargs):
        if path.parent == link:
            pytest.fail('cleanup followed an outside numeric_map symlink')
        return resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'resolve', no_follow)
    assert _replaced_map_directory(out) is None
    assert (victim/'keep').read_text() == 'keep' and link.is_symlink()


def test_cleanup_reference_comparison_is_independent_of_windows_separator(tmp_path, monkeypatch):
    import simcompare.dl_results as module
    from pathlib import PureWindowsPath
    out = tmp_path/'c.json'; directory = 'numeric_map/c_'+'a'*32
    (tmp_path/directory).mkdir(parents=True)
    old_context(out, [directory+'/map.npz'])
    # Real filesystem operations stay on the host. Only Path's string spelling
    # is simulated as Windows; stored references still use portable '/' strings.
    class WindowsSpelling:
        def __init__(self, path): self.path = Path(path)
        def __fspath__(self): return str(self.path)
        def __str__(self): return str(PureWindowsPath(self.path))
        @property
        def parent(self): return WindowsSpelling(self.path.parent)
        def __getattr__(self, name): return getattr(self.path, name)
    monkeypatch.setattr(module, 'Path', WindowsSpelling)
    assert module._replaced_map_directory(out) == tmp_path/directory
