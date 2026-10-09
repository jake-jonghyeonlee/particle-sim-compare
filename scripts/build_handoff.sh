#!/usr/bin/env bash
set -euo pipefail
SCRIPT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python3 - "$SCRIPT_ROOT" "$@" <<'PY'
import sys
if sys.version_info < (3,11,4):
    print("build_handoff requires Python >= 3.11.4",file=sys.stderr);sys.exit(2)
import argparse, hashlib, io, json, pathlib, platform, shutil, subprocess, tarfile
p=argparse.ArgumentParser(description='Exact-commit archives, no weights or dependency downloads')
p.add_argument('tool_root',type=pathlib.Path);p.add_argument('out',type=pathlib.Path)
p.add_argument('--allow-dirty-for-test',action='store_true')
for name in ('compare','dl1','dl2'):
    p.add_argument('--'+name+'-repo',type=pathlib.Path)
    p.add_argument('--'+name+'-ref',default='HEAD')
a=p.parse_args()
names={'compare':'particle-sim-compare','dl1':'particle-sim-dl1','dl2':'particle-sim-dl2'}
def git(repo,*args):return subprocess.check_output(['git','-C',str(repo),*args])
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
try:
    repos={}
    for key,name in names.items():
        repo=(getattr(a,key+'_repo') or (a.tool_root if key=='compare' else a.tool_root.parent/name)).resolve()
        if a.out.resolve().is_relative_to(repo):raise ValueError('bundle output must be outside repository: '+name)
        dirty=bool(git(repo,'status','--porcelain'))
        if dirty and not a.allow_dirty_for_test:raise ValueError('dirty working tree refused: '+name)
        ref=git(repo,'rev-parse',getattr(a,key+'_ref')+'^{commit}').decode().strip()
        repos[name]=(repo,ref,dirty)
    if a.out.exists() and any(a.out.iterdir()):raise ValueError('output directory must be empty')
    a.out.mkdir(parents=True,exist_ok=True)
    manifest={'schema_version':'simcompare_handoff_v1','id_versions':{'numeric':'numeric_v2','roi':'review_roi_v1','exchange':'dl_file_exchange_v1','dl1':'dl1_shape_v1_draft','dl2':'original_roi_bundle_v1'},
              'python_version':platform.python_version(),'weights':[],
              'pending_gates':['G1_density_unit_inspected_area','G2_one_unit_from_2x2_image_container_bit_depth_layout_polarity','G4_real_DL2_geometry_layer_policy_max_pair_attempts','G5_real_DL1_grid_minimum_line_width_CNT_VIA_source','G6_labels_acceptance_targets','DL2_connectivity_geometric_proxy','public_ConvNeXt_V2_weights_license_unverified'],
              'repositories':{},'metadata_files':{}}
    for name,(repo,commit,dirty) in repos.items():
        archive=git(repo,'archive','--format=tar',commit)
        target=a.out/name;target.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(target,filter='data')
        files={}
        for path in sorted(target.rglob('*')):
            if path.is_symlink():raise ValueError('symlink unsupported in bundle: '+str(path))
            if path.is_file():files[str(path.relative_to(target))]=sha(path)
        # Weights are opt-in only; refuse tracked checkpoint assets rather than silently omit them.
        if any(pathlib.Path(f).suffix in ('.pt','.pth','.safetensors','.ckpt') for f in files):raise ValueError('tracked weights require an explicitly reviewed package policy: '+name)
        lock=next((target/n for n in ('uv.lock','requirements.lock') if (target/n).is_file()),target/'uv.lock')
        if not lock.is_file():raise ValueError('dependency lock missing: '+name)
        manifest['repositories'][name]={'commit':commit,'dirty':dirty,'dependency_lock':{'path':lock.name,'sha256':sha(lock),'kind':lock.name},'files':files,
            'required_python':__import__('tomllib').loads((target/'pyproject.toml').read_text())['project']['requires-python']}
    for filename in ('manifest.schema.json','validation-template.json'):
        src=a.tool_root/'docs/handoff'/filename
        shutil.copyfile(src,a.out/filename);manifest['metadata_files'][filename]=sha(a.out/filename)
    (a.out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'status':'available','bundle':str(a.out),'commits':{n:r['commit'] for n,r in manifest['repositories'].items()},'dirty_test_only':any(r['dirty'] for r in manifest['repositories'].values()),'weights':[],'manifest_sha256':sha(a.out/'manifest.json')}))
except (ValueError,OSError,subprocess.CalledProcessError) as exc:
    p.exit(2,str(exc)+'\n')
PY
