#!/usr/bin/env bash
set -euo pipefail
SCRIPT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python3 - "$SCRIPT_ROOT" "$@" <<'PY'
import argparse,json,os,pathlib,subprocess,tempfile
p=argparse.ArgumentParser(description='No downloads; Python inference runs under a socket-denying sitecustomize')
p.add_argument('tools',type=pathlib.Path);p.add_argument('bundle',type=pathlib.Path)
p.add_argument('--expect-manifest-sha256');p.add_argument('--out',type=pathlib.Path);p.add_argument('--allow-dirty-for-test',action='store_true')
a=p.parse_args();a.bundle=a.bundle.resolve();out=(a.out or a.bundle.parent/(a.bundle.name+'-offline-validation')).resolve()
if out.is_relative_to(a.bundle):p.exit(2,'validation output must be outside verified bundle\n')
if out.exists() and any(out.iterdir()):p.exit(2,'offline output directory must be empty\n')
out.mkdir(parents=True,exist_ok=True)
steps=[]
runtime_versions={}
def record(name,command,returncode,stdout='',stderr=''):
    steps.append({'step':name,'command':command,'returncode':returncode,'stdout':stdout,'stderr':stderr})
    (out/'validation.json').write_text(json.dumps({'status':'running','steps':steps},indent=2)+'\n')
def run(name,command,env=None,cwd=None,allowed=(0,)):
    result=subprocess.run([str(v) for v in command],capture_output=True,text=True,env=env,cwd=cwd,timeout=180)
    record(name,[str(v) for v in command],result.returncode,result.stdout,result.stderr)
    if result.returncode not in allowed:raise RuntimeError(name+': exit '+str(result.returncode))
    return result
try:
    run('bundle_integrity',['bash',a.tools/'scripts/check_handoff.sh',a.bundle,*(['--allow-dirty-for-test'] if a.allow_dirty_for_test else []),*(['--expect-manifest-sha256',a.expect_manifest_sha256] if a.expect_manifest_sha256 else [])])
    with tempfile.TemporaryDirectory(prefix='simcompare-network-deny-') as tmp:
        guard=pathlib.Path(tmp)
        (guard/'sitecustomize.py').write_text('import socket\ndef deny(*a,**k): raise OSError("offline_network_blocked")\nsocket.socket.connect=deny\nsocket.socket.connect_ex=deny\nsocket.create_connection=deny\nsocket.getaddrinfo=deny\n')
        names={'compare':'particle-sim-compare','dl1':'particle-sim-dl1','dl2':'particle-sim-dl2'}
        environments={};interpreters={};runtime_versions={}
        for key,name in names.items():
            python=pathlib.Path(os.environ.get(key.upper()+'_PYTHON',str(a.bundle.parent/'handoff-envs'/key/'bin/python'))).absolute()
            if not python.is_file():raise RuntimeError('interpreter_missing: '+key+'; install declared dependencies offline and set '+key.upper()+'_PYTHON')
            env=os.environ.copy();env['PYTHONPATH']=str(guard)+os.pathsep+str(a.bundle/name/'src');env['PYTHONNOUSERSITE']='1';env['PYTHONDONTWRITEBYTECODE']='1'
            env['PIP_NO_INDEX']='1';env['HF_HUB_OFFLINE']='1';env['TRANSFORMERS_OFFLINE']='1';env['TORCH_HOME']=str(out/'empty-torch-cache')
            environments[key]=env;interpreters[key]=python
            version=run(key+'_runtime_version',[python,'-c','import platform; print(platform.python_version())'],env)
            runtime_versions[key]={'interpreter':str(python),'python_version':version.stdout.strip()}
            package={'compare':'simcompare','dl1':'psdl1','dl2':'psdl2'}[key]
            probe='import '+package+'; import socket\ntry: socket.create_connection(("127.0.0.1",9))\nexcept OSError as e: assert str(e)=="offline_network_blocked"\nelse: raise AssertionError("network guard missing")\nprint('+package+'.__file__)'
            run(key+'_import_network_denied',[python,'-c',probe],env)
            modules=['simcompare'] if key=='compare' else [package+'.data',package+'.train',package+'.search',package+'.evaluate']+(['psdl2.index'] if key=='dl2' else [])
            for module in modules:run(module+'_help',[python,'-m',module,'--help'],env)
        c=interpreters['compare'];env=environments['compare'];demo=out/'numeric-demo'
        run('numeric_demo',[c,'-m','simcompare','demo-data','--out',demo],env)
        validation=run('numeric_validate',[c,'-m','simcompare','validate-data','--config',demo/'config.yaml'],env,allowed=(0,3))
        if validation.returncode==3 and json.loads(validation.stdout).get('status')!='partial':raise RuntimeError('numeric_validate did not declare expected pending/partial state')
        run('numeric_compare',[c,'-m','simcompare','compare','--config',demo/'config.yaml','--out',out/'comparison'],env)
        run('numeric_map',[c,'-m','simcompare','electrode-map','--config',demo/'config.yaml','--out',out/'map'],env)
        generator=a.bundle/'particle-sim-compare/tests/fixtures/handoff/generate.py'
        if not generator.is_file():raise RuntimeError('fixture_runner_missing_in_committed_compare_archive; commit T12 before a production build')
        run('simcompare.dl_results_help',[c,'-m','simcompare.dl_results','--help'],env)
        native=out/'native'
        for mode,key in [('prepare','compare'),('dl1','dl1'),('dl2','dl2')]:
            run('fixture_'+mode,[interpreters[key],generator,mode,native,'--repo',a.bundle/names[key]],environments[key])
        run('fixture_seal',[c,'-m','simcompare.dl_results','seal','--context',native/'numeric_context.json','--dl1',native/'dl1_search.json','--dl1-fingerprint',json.loads((native/'dl1_search.json').read_text())['fingerprint'],'--dl1-bindings',native/'dl1_bindings.json','--dl2',native/'dl2_search.json','--dl2-cohort',native/'cohort.json','--dl2-index',native/'dl2_index.json','--dl2-expected',native/'dl2_expected.json','--out',native/'exchange.json'],env)
        run('fixture_read',[c,'-m','simcompare.dl_results','read',native/'exchange.json','--out',native/'joined.json'],env)
        result={'status':'available','network_policy':'Python socket.connect/connect_ex/create_connection/getaddrinfo patched in inheriting non-isolated Python children only; UDP sendto, gethostbyname, direct _socket, python -I and non-Python children are not blocked; no dependency installation','runtime_environments':runtime_versions,'weights':'local random fixture tensors only; no public pretrained weights','steps':steps}
except (RuntimeError,OSError,subprocess.TimeoutExpired) as exc:
    result={'status':'unavailable','reason':str(exc),'runtime_environments':runtime_versions,'steps':steps}
(out/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'status':result['status'],'reason':result.get('reason'),'record':str(out/'validation.json')}))
raise SystemExit(0 if result['status']=='available' else 2)
PY
