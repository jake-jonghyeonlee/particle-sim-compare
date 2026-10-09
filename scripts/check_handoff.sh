#!/usr/bin/env bash
set -euo pipefail
python3 - "$@" <<'PY'
import argparse,hashlib,json,pathlib,re,sys
p=argparse.ArgumentParser(description='Verify exact archive inventories and sha256; reject dirty test packages by default')
p.add_argument('bundle',type=pathlib.Path);p.add_argument('--allow-dirty-for-test',action='store_true');a=p.parse_args()
def fail(reason):raise ValueError(reason)
def unique(items):
    result={}
    for k,v in items:
        if k in result:fail('duplicate manifest key: '+k)
        result[k]=v
    return result
def safe(root,name):
    path=pathlib.Path(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name:fail('unsafe path: '+name)
    target=root/path
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):fail('unsafe symlink: '+name)
    return target
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
try:
    m=json.loads((a.bundle/'manifest.json').read_text(),object_pairs_hook=unique)
    if set(m)!={'schema_version','id_versions','python_version','weights','pending_gates','repositories','metadata_files'}:fail('manifest fields mismatch')
    if not isinstance(m['python_version'],str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',m['python_version']):fail('invalid Python version')
    if m['schema_version']!='simcompare_handoff_v1' or m['weights']!=[]:fail('unsupported handoff schema or weights policy')
    if set(m['repositories'])!={'particle-sim-compare','particle-sim-dl1','particle-sim-dl2'}:fail('three repositories required')
    if m['id_versions']!={'numeric':'numeric_v2','roi':'review_roi_v1','exchange':'dl_file_exchange_v1','dl1':'dl1_shape_v1_draft','dl2':'original_roi_bundle_v1'}:fail('schema/id versions mismatch')
    if set(m['pending_gates'])!={'G1_density_unit_inspected_area','G2_one_unit_from_2x2_image_container_bit_depth_layout_polarity','G4_real_DL2_geometry_layer_policy_max_pair_attempts','G5_real_DL1_grid_minimum_line_width_CNT_VIA_source','G6_labels_acceptance_targets','DL2_connectivity_geometric_proxy','public_ConvNeXt_V2_weights_license_unverified'} or len(m['pending_gates'])!=7:fail('pending gates missing')
    if set(m['metadata_files'])!={'manifest.schema.json','validation-template.json'}:fail('metadata fields mismatch')
    for name,r in m['repositories'].items():
        if set(r)!={'commit','dirty','dependency_lock','files','required_python'}:fail('repository fields mismatch: '+name)
        if not isinstance(r['required_python'],str) or not r['required_python']:fail('Python requirement missing: '+name)
        if not re.fullmatch('[0-9a-f]{40}',r['commit']) or type(r['dirty']) is not bool:fail('invalid commit/dirty field')
        if r['dirty'] and not a.allow_dirty_for_test:fail('dirty test archive refused: '+name)
        root=a.bundle/name
        actual={str(f.relative_to(root)) for f in root.rglob('*') if f.is_file() or f.is_symlink()}
        if actual!=set(r['files']):fail('archive inventory mismatch: '+name)
        for path,expected in r['files'].items():
            if not re.fullmatch('[0-9a-f]{64}',expected) or digest(safe(root,path))!=expected:fail('file hash mismatch: '+name+'/'+path)
        lock=r['dependency_lock']
        if set(lock)!={'path','sha256','kind'}:fail('dependency lock fields mismatch: '+name)
        if lock['kind']!=lock['path'] or lock['path'] not in ('uv.lock','requirements.lock') or lock['sha256']!=r['files'][lock['path']]:fail('dependency lock mismatch: '+name)
    for path,expected in m['metadata_files'].items():
        if digest(safe(a.bundle,path))!=expected:fail('metadata hash mismatch: '+path)
    root_files={f.name for f in a.bundle.iterdir()}
    if root_files!=set(m['repositories'])|set(m['metadata_files'])|{'manifest.json'}:fail('unexpected bundle root entries')
    print(json.dumps({'status':'available','verified_repositories':3,'weights':[],'dirty_test_only':a.allow_dirty_for_test}))
except (ValueError,KeyError,TypeError,OSError) as exc:p.exit(2,str(exc)+'\n')
PY
