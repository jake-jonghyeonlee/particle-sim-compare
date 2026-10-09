"""Cross-repo tests run only producer interpreters; runtime simcompare stays file-only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import io
import tarfile

import pytest

from simcompare.dl_results import create_exchange, read, read_results

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT/'tests/fixtures/handoff'


@pytest.mark.cross_repo
def test_actual_producer_round_trip(tmp_path):
    dl1 = ROOT.parent/'particle-sim-dl1'; dl2 = ROOT.parent/'particle-sim-dl2'
    for repo in (dl1,dl2):
        if not (repo/'.venv/bin/python').is_file():
            pytest.skip(f'cross-repo inference requires sibling repository and its .venv: {repo}')
    root = Path(os.environ.get('SIMCOMPARE_HANDOFF_GENERATE', tmp_path/'native'))
    generator = FIXTURE/'generate.py'
    for mode,repo,python in [('prepare',ROOT,sys.executable),('dl1',dl1,dl1/'.venv/bin/python'),('dl2',dl2,dl2/'.venv/bin/python')]:
        result = subprocess.run([str(python),str(generator),mode,str(root),'--repo',str(repo)],cwd=repo,text=True,capture_output=True,timeout=90)
        assert result.returncode == 0, result.stdout+result.stderr
    raw1=read(root/'dl1_search.json'); expected=read(root/'dl2_expected.json')
    create_exchange(root/'exchange.json',context=root/'numeric_context.json',dl1=root/'dl1_search.json',
        dl1_fingerprint=raw1['fingerprint'],dl1_bindings=root/'dl1_bindings.json',
        dl2=root/'dl2_search.json',dl2_index=root/'dl2_index.json',dl2_expected=expected,dl2_cohort=root/'cohort.json',force=True)
    result=read_results(root/'exchange.json')
    assert result['dl2']['status']=='available'
    exported=read(root/'review_rois.json')
    region_rows=[r for r in exported['rois'] if r['source_kind']=='electrode_count_region']
    assert {r['case_id'] for r in region_rows}=={'A','B'}
    assert len(region_rows)==2 and all(r['status']=='ready' for r in region_rows)
    indexed=read(root/'dl2_index.json')
    indexed_by_id={r['roi_id']:r for r in indexed['records']}
    for row in exported['rois']:
        if row['status']=='blocked':
            assert row['roi_id'] not in indexed_by_id
            continue
        actual=indexed_by_id[row['roi_id']]
        assert actual['source_identity']==row['source_identity']
        assert actual['frame']==row['frame']
        assert actual['source_assets']==row['source_assets']
    assert len(indexed['blocked_export_records'])==1
    assert any(r['search_scope']=='supplementary' for r in result['dl2']['supplementary'])


def test_no_runtime_sibling_import():
    import ast
    tree=ast.parse((ROOT/'src/simcompare/dl_results.py').read_text())
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):
            assert all(not a.name.startswith(('psdl1','psdl2')) for a in node.names)
        if isinstance(node,ast.ImportFrom):
            assert not (node.module or '').startswith(('psdl1','psdl2'))


def temporary_repos(tmp_path):
    repos={}
    for key,name in [('compare','particle-sim-compare'),('dl1','particle-sim-dl1'),('dl2','particle-sim-dl2')]:
        root=tmp_path/name;root.mkdir()
        (root/'pyproject.toml').write_text('[project]\nname="test"\nversion="0"\nrequires-python=">=3.12"\n')
        (root/'uv.lock').write_text('version = 1\n')
        (root/'README.md').write_text('synthetic archive test only\n')
        subprocess.run(['git','init','-q',str(root)],check=True)
        subprocess.run(['git','-C',str(root),'add','.'],check=True)
        subprocess.run(['git','-C',str(root),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','synthetic'],check=True)
        repos[key]=root
    return repos


def build_command(out,repos,extra=()):
    args=['bash',str(ROOT/'scripts/build_handoff.sh'),str(out)]
    for key,repo in repos.items():args += ['--'+key+'-repo',str(repo)]
    return args+list(extra)


def test_exact_archive_schema_hashes_and_tamper(tmp_path):
    repos=temporary_repos(tmp_path);out=tmp_path/'bundle'
    result=subprocess.run(build_command(out,repos),capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    manifest=read(out/'manifest.json'); schema=read(ROOT/'docs/handoff/manifest.schema.json')
    assert set(manifest)==set(schema['required'])
    assert manifest['weights']==[] and len(manifest['repositories'])==3
    for name,row in manifest['repositories'].items():
        assert set(row)==set(schema['$defs']['repo']['required'])
        actual=subprocess.check_output(['git','-C',str(tmp_path/name),'rev-parse','HEAD'],text=True).strip()
        assert row['commit']==actual and row['dirty'] is False
        assert 'uv.lock' in row['files']
    check=['bash',str(ROOT/'scripts/check_handoff.sh'),str(out)]
    result=subprocess.run(check,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    (out/'particle-sim-dl2/README.md').write_text('tampered\n')
    result=subprocess.run(check,capture_output=True,text=True)
    assert result.returncode!=0 and 'file hash mismatch' in result.stderr


def test_dirty_tree_refused_and_explicit_test_flag_recorded(tmp_path):
    repos=temporary_repos(tmp_path);out=tmp_path/'bundle'
    (repos['dl1']/'untracked.txt').write_text('not archived')
    result=subprocess.run(build_command(out,repos),capture_output=True,text=True)
    assert result.returncode!=0 and 'dirty working tree refused: particle-sim-dl1' in result.stderr
    result=subprocess.run(build_command(out,repos,['--allow-dirty-for-test']),capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    manifest=read(out/'manifest.json')
    assert manifest['repositories']['particle-sim-dl1']['dirty'] is True
    assert not (out/'particle-sim-dl1/untracked.txt').exists()
    command=['bash',str(ROOT/'scripts/check_handoff.sh'),str(out)]
    result=subprocess.run(command,capture_output=True,text=True)
    assert result.returncode!=0 and 'dirty test archive refused' in result.stderr
    assert subprocess.run(command+['--allow-dirty-for-test'],capture_output=True).returncode==0


def test_empty_validation_record_has_no_success_labels_or_numbers():
    record=read(ROOT/'docs/handoff/validation-template.json')
    assert all(value is None or value==[] for value in record.values())


def test_offline_missing_environment_is_explicit_not_success(tmp_path):
    repos=temporary_repos(tmp_path);out=tmp_path/'bundle'
    assert subprocess.run(build_command(out,repos),capture_output=True).returncode==0
    env=os.environ.copy();env['COMPARE_PYTHON']=str(tmp_path/'missing/python')
    result=subprocess.run(['bash',str(ROOT/'scripts/validate_offline.sh'),str(out)],env=env,capture_output=True,text=True)
    assert result.returncode!=0 and 'interpreter_missing' in result.stdout
    record=read(tmp_path/'bundle-offline-validation/validation.json')
    assert record['status']=='unavailable' and record['steps'][0]['returncode']==0


@pytest.mark.cross_repo
def test_offline_full_candidate_in_temporary_git_archive(tmp_path):
    """Test-only commit outside the working repo; never label it the approved release."""
    repos={'compare':tmp_path/'candidate','dl1':ROOT.parent/'particle-sim-dl1','dl2':ROOT.parent/'particle-sim-dl2'}
    for key in ('dl1','dl2'):
        if not (repos[key]/'.venv/bin/python').is_file():
            pytest.skip('offline cross-repo fixture inference needs sibling .venv: '+key)
    source=repos['compare'];source.mkdir()
    archive=subprocess.check_output(['git','-C',str(ROOT),'archive','HEAD'])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(source,filter='data')
    for path in ['src/simcompare/dl_results.py','tests/test_dl_results.py','tests/test_handoff.py',
                 'docs/handoff','tests/fixtures/handoff','scripts/build_handoff.sh','scripts/check_handoff.sh','scripts/validate_offline.sh']:
        src=ROOT/path;dst=source/path
        if src.is_dir():shutil.copytree(src,dst,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
        else:dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
    subprocess.run(['git','init','-q',str(source)],check=True)
    subprocess.run(['git','-C',str(source),'add','.'],check=True)
    subprocess.run(['git','-C',str(source),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','test-only candidate snapshot'],check=True)
    bundle=tmp_path/'bundle'
    result=subprocess.run(build_command(bundle,repos),capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    env=os.environ.copy();env['COMPARE_PYTHON']=sys.executable
    for key in ('dl1','dl2'):env[key.upper()+'_PYTHON']=str(repos[key]/'.venv/bin/python')
    result=subprocess.run(['bash',str(ROOT/'scripts/validate_offline.sh'),str(bundle)],env=env,capture_output=True,text=True,timeout=180)
    assert result.returncode==0,result.stdout+result.stderr
    record=read(tmp_path/'bundle-offline-validation/validation.json')
    assert record['status']=='available'
    assert set(record['runtime_environments'])=={'compare','dl1','dl2'}
    assert all(v['python_version'] for v in record['runtime_environments'].values())
    assert any(r['step']=='fixture_dl2' and r['returncode']==0 for r in record['steps'])
    assert subprocess.run(['bash',str(ROOT/'scripts/check_handoff.sh'),str(bundle)],capture_output=True).returncode==0


@pytest.mark.parametrize('tamper',['commit','file_and_hash'])
def test_external_manifest_pin_rejects_review_reproductions(tamper,tmp_path):
    from simcompare.dl_results import sha,write
    repos=temporary_repos(tmp_path);out=tmp_path/'bundle'
    r=subprocess.run(build_command(out,repos),capture_output=True,text=True);assert r.returncode==0
    expected=json.loads(r.stdout)['manifest_sha256'];assert expected==sha(out/'manifest.json')
    check=['bash',str(ROOT/'scripts/check_handoff.sh'),str(out)]
    assert subprocess.run(check+['--expect-manifest-sha256',expected],capture_output=True).returncode==0
    m=read(out/'manifest.json')
    if tamper=='commit':m['repositories']['particle-sim-dl1']['commit']='0'*40
    else:
        path=out/'particle-sim-dl1/README.md';path.write_text('modified');m['repositories']['particle-sim-dl1']['files']['README.md']=sha(path)
    write(out/'manifest.json',m)
    r=subprocess.run(check+['--expect-manifest-sha256',expected],capture_output=True,text=True)
    assert r.returncode==2 and 'manifest_sha256_mismatch' in r.stderr


@pytest.mark.parametrize('directory',['.venv','unexpected/nested'])
def test_empty_unexpected_directory_rejected(directory,tmp_path):
    repos=temporary_repos(tmp_path);out=tmp_path/'bundle';assert subprocess.run(build_command(out,repos),capture_output=True).returncode==0
    (out/'particle-sim-compare'/directory).mkdir(parents=True)
    r=subprocess.run(['bash',str(ROOT/'scripts/check_handoff.sh'),str(out)],capture_output=True,text=True)
    assert r.returncode==2 and 'directory inventory mismatch' in r.stderr


def test_offline_preserves_existing_nonempty_output(tmp_path):
    out=tmp_path/'old';out.mkdir();(out/'validation.json').write_text('old run')
    r=subprocess.run(['bash',str(ROOT/'scripts/validate_offline.sh'),str(tmp_path/'missing-bundle'),'--out',str(out)],capture_output=True,text=True)
    assert r.returncode==2 and 'must be empty' in r.stderr
    assert (out/'validation.json').read_text()=='old run'


def test_unsupported_builder_python_has_no_partial_output(tmp_path):
    old=Path('/usr/bin/python3')
    if not old.exists() or subprocess.check_output([str(old),'-c','import sys; print(sys.version_info<(3,11,4))'],text=True).strip()!='True':
        pytest.skip('Python below 3.11.4 unavailable for version guard reproduction')
    shim=tmp_path/'bin';shim.mkdir();(shim/'python3').symlink_to(old)
    env=os.environ.copy();env['PATH']=str(shim)+os.pathsep+env['PATH']
    out=tmp_path/'bundle'
    r=subprocess.run(['bash',str(ROOT/'scripts/build_handoff.sh'),str(out)],env=env,capture_output=True,text=True)
    assert r.returncode==2 and 'requires Python >= 3.11.4' in r.stderr and 'Traceback' not in r.stderr
    assert not out.exists()


@pytest.mark.cross_repo
def test_real_dl2_checkpoint_b_search_cannot_be_sealed_against_index_a(tmp_path):
    from simcompare.dl_results import write
    from simcompare.numeric_schema import NumericContractError
    repo=ROOT.parent/'particle-sim-dl2';python=repo/'.venv/bin/python'
    if not python.is_file():pytest.skip('real DL2 CLI regression requires sibling particle-sim-dl2 and its .venv')
    root=tmp_path/'exchange';shutil.copytree(FIXTURE,root)
    export=read(root/'review_rois.json');ready=[r for r in export['rois'] if r['status']=='ready']
    write(root/'lineages.json',{r['product_id']:r['product_id'] for r in ready})
    write(root/'split.json',{r['roi_id']:'train' for r in ready})
    query=next(r['roi_id'] for r in ready if r['product_id']=='PRODUCT' and r['source_kind']=='electrode_count_region' and r['case_id']=='A')
    env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1';env['HF_HUB_OFFLINE']='1'
    for name,seed in [('a',7),('b',11)]:
        config=read(root/'dl2_fixture_config.json');config['seed']=seed
        write(root/f'config_{name}.json',{'config':config} if name=='a' else config)
        calls=[['train',str(root/'review_rois.json'),'--assets-root',str(root/'numeric'),'--config',str(root/f'config_{name}.json'),'--lineages',str(root/'lineages.json'),'--split',str(root/'split.json'),'--out',str(root/f'{name}.pt')],
               ['index',str(root/'review_rois.json'),'--assets-root',str(root/'numeric'),'--checkpoint',str(root/f'{name}.pt'),'--out',str(root/f'index_{name}.json')],
               ['search',str(root/'review_rois.json'),'--assets-root',str(root/'numeric'),'--checkpoint',str(root/f'{name}.pt'),'--index',str(root/f'index_{name}.json'),'--roi-id',query,'--cohort',str(root/'cohort.json'),'--topk','10','--expand','--out',str(root/f'search_{name}.json')]]
        for module,*args in calls:
            result=subprocess.run([str(python),'-m','psdl2.'+module,*args],cwd=repo,env=env,capture_output=True,text=True,timeout=90)
            assert result.returncode==0,result.stdout+result.stderr
            (root/f'{module}_{name}_stdout.json').write_text(result.stdout)
    a=read(root/'index_a.json');b=read(root/'index_b.json')
    assert a['checkpoint_fingerprint']!=b['checkpoint_fingerprint'] and a['view_fingerprint']==b['view_fingerprint']
    sa=read(root/'search_a.json');sb=read(root/'search_b.json')
    assert [r['score'] for r in sa['primary']] != [r['score'] for r in sb['primary']]
    expected={k:a[k] for k in ('schema_version','checkpoint_fingerprint','view_fingerprint')}
    with pytest.raises(NumericContractError,match='dl2_scores_do_not_match_index'):
        create_exchange(root/'negative_b_search_a_index.json',context=root/'numeric_context.json',
                        dl2=root/'search_b.json',dl2_index=root/'index_a.json',dl2_expected=expected,dl2_cohort=root/'cohort.json',force=True)
