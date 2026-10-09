"""Versioned, file-only DL exchange. Similarity never changes numeric values.

Native search files have no result schema version. A separately generated envelope
pins their exact bytes and the producer's available model/index fingerprints.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import re
import os
import tempfile
import shutil
import uuid

SCORE_TOLERANCE = 1e-9

from .numeric_schema import CaseIdentity, NumericContractError

SCHEMA = 'dl_file_exchange_v1'
CONTEXT_SCHEMA = 'numeric_dl_context_v1'
IDENTITY = tuple(CaseIdentity.__dataclass_fields__)
FRAME = ('frame_id', 'length_unit', 'pixel_pitch_um', 'origin_xy_um',
         'shape_yx', 'axis_order', 'origin_convention')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise NumericContractError('duplicate_dl_json_key', key)
            result[key] = value
        return result
    def invalid(value):
        raise NumericContractError('nonfinite_dl_number', value)
    return json.loads(Path(path).read_text(), object_pairs_hook=unique, parse_constant=invalid)


def write(path, value, *, force=True):
    path = Path(path)
    if path.exists() and not force:
        raise NumericContractError('output_exists_use_force', str(path))
    encoded = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False,encoding='utf-8') as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
        os.replace(temporary,path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def identity(row):
    if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k] for k in IDENTITY):
        raise NumericContractError('complete_dl_source_identity_required')
    return tuple(row[k] for k in IDENTITY)


def frame(row):
    if not isinstance(row, dict) or any(k not in row for k in FRAME):
        raise NumericContractError('complete_dl_frame_required')
    return {k: row[k] for k in FRAME}


def _reference(root, path):
    path = Path(path).resolve()
    try:
        relative = path.relative_to(root.resolve())
    except ValueError as exc:
        raise NumericContractError('dl_file_outside_exchange_directory') from exc
    return {'path': str(relative), 'sha256': sha(path)}


def _file(root, reference):
    from .numeric_images import resolve_path
    if not isinstance(reference, dict) or set(reference) != {'path', 'sha256'}:
        raise NumericContractError('dl_file_reference_required')
    path = resolve_path(root, reference['path'])
    if sha(path) != reference['sha256']:
        raise NumericContractError('dl_native_file_hash_mismatch', reference['path'])
    return read(path)


def _score(row):
    score = row.get('score')
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not -1.000001 <= score <= 1.000001:
        raise NumericContractError('invalid_similarity_score')


def _dl1(raw, expected):
    required = {'fingerprint', 'grid', 'grid_selection', 'grid_gate', 'status', 'rankings', 'excluded', 'query_id'}
    if not required <= raw.keys() or raw.get('schema_version') is not None:
        raise NumericContractError('unsupported_dl1_native_result_profile')
    if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}',expected) or raw['fingerprint'] != expected:
        raise NumericContractError('dl1_fingerprint_mismatch')
    if type(raw['grid']) is not int or raw['grid'] <= 0 or raw['status'] not in ('available', 'unavailable'):
        raise NumericContractError('invalid_dl1_grid_or_status')
    seen = set()
    for row in raw['rankings']:
        _score(row)
        if row['product_id'] in seen or not isinstance(row.get('input_identity'), str) or len(row['input_identity']) != 64:
            raise NumericContractError('invalid_dl1_candidate_identity')
        seen.add(row['product_id'])


def _dl2(raw, index, expected):
    if index.get('schema_version') != 'psdl2_index_v2' or index.get('schema_version') != expected['schema_version']:
        raise NumericContractError('dl2_index_schema_mismatch')
    for key in ('checkpoint_fingerprint', 'view_fingerprint'):
        if not isinstance(expected[key],str) or not re.fullmatch('[0-9a-f]{64}',expected[key]) or index.get(key) != expected[key]:
            raise NumericContractError('dl2_'+key+'_mismatch')
    if 'rejected' not in raw:
        raise NumericContractError('dl2_rejected_field_required')
    required = {'query', 'primary', 'supplementary', 'status', 'no_match_reasons', 'primary_cohort', 'expanded', 'index_roi_ids'}
    if not required <= raw.keys() or raw.get('schema_version') is not None:
        raise NumericContractError('unsupported_dl2_native_result_profile')
    if raw['status'] not in ('available', 'unavailable'):
        raise NumericContractError('invalid_dl2_status')
    indexed = {}
    embeddings = {}
    import numpy as np
    for row in index['records']:
        if row.get('source_versions', {}).get('schema_version') != 'review_roi_v1':
            raise NumericContractError('dl2_source_schema_mismatch')
        key = (row['roi_id'], identity(row['source_identity']))
        if key in indexed:
            raise NumericContractError('duplicate_dl2_index_identity')
        indexed[key] = {k:v for k,v in row.items() if k != 'embedding'}
        vector = np.asarray(row.get('embedding'),dtype=np.float64)
        if vector.ndim!=1 or not vector.size or not np.isfinite(vector).all() or not np.isfinite(np.linalg.norm(vector)) or np.linalg.norm(vector)==0:
            raise NumericContractError('invalid_dl2_index_embedding')
        embeddings[key] = vector
    if raw['index_roi_ids'] != [r['roi_id'] for r in index['records']]:
        raise NumericContractError('dl2_index_membership_mismatch')
    query = raw['query']
    if indexed.get((query['roi_id'], identity(query['source_identity']))) != query:
        raise NumericContractError('dl2_query_index_mismatch')
    for scope in ('primary', 'supplementary'):
        for row in raw[scope]:
            _score(row)
            if row.get('search_scope') != scope or (scope == 'primary') != (row['product_id'] in raw['primary_cohort']):
                raise NumericContractError('dl2_search_scope_mismatch')
            original = {k:v for k,v in row.items() if k not in ('score', 'search_scope', 'same_physical_case')}
            if indexed.get((row['roi_id'], identity(row['source_identity']))) != original:
                raise NumericContractError('dl2_match_index_mismatch')
            a = embeddings[(query['roi_id'],identity(query['source_identity']))]
            b = embeddings[(row['roi_id'],identity(row['source_identity']))]
            if a.shape != b.shape:
                raise NumericContractError('invalid_dl2_index_embedding')
            cosine = round(float(np.clip(np.dot(a,b)/(np.linalg.norm(a)*np.linalg.norm(b)),-1,1)),6)
            if abs(cosine-row['score']) > SCORE_TOLERANCE:
                raise NumericContractError('dl2_scores_do_not_match_index')


def _cohort(cohort,raw):
    if not isinstance(cohort,list) or not cohort or any(not isinstance(v,str) or not v for v in cohort) or len(set(cohort))!=len(cohort):
        raise NumericContractError('explicit_nonempty_unique_product_cohort_required')
    if sorted(cohort)!=raw['primary_cohort']:
        raise NumericContractError('dl2_cohort_mismatch')


def create_exchange(out, *, context, dl1=None, dl1_fingerprint=None, dl1_bindings=None,
                    dl2=None, dl2_index=None, dl2_expected=None, dl2_cohort=None, pending_gates=None, force=False):
    """Generate, never manually author, the envelope. Expected fingerprints are explicit."""
    out = Path(out)
    root = out.parent
    if out.exists() and not force:
        raise NumericContractError('output_exists_use_force',str(out))
    context_doc = read(context)
    if context_doc.get('schema_version') != CONTEXT_SCHEMA:
        raise NumericContractError('numeric_context_schema_mismatch')
    if pending_gates is not None and (not isinstance(pending_gates,list) or any(not isinstance(g,str) or not g for g in pending_gates)):
        raise NumericContractError('pending_gate_list_required')
    pending = set(pending_gates or []) | {k for k,v in context_doc.get('gates',{}).items() if v.get('status')=='pending'}
    payload = {'schema_version':SCHEMA, 'numeric_context':_reference(root, context),
               'dl1':None, 'dl2':None, 'pending_gates':sorted(pending),
               'interpretation':'similarity_only_never_numeric_risk'}
    if dl1:
        if not dl1_fingerprint:
            raise NumericContractError('explicit_dl1_fingerprint_required')
        _dl1(read(dl1), dl1_fingerprint)
        payload['dl1'] = {'result':_reference(root, dl1), 'config_fingerprint':dl1_fingerprint,
                          'bindings':_reference(root, dl1_bindings) if dl1_bindings else None,
                          'native_profile':'dl1_search_7f9d659'}
    if dl2:
        if dl2_index is None or dl2_expected is None:
            raise NumericContractError('explicit_dl2_index_fingerprints_required')
        raw = read(dl2)
        _dl2(raw, read(dl2_index), dl2_expected)
        if dl2_cohort is None:
            raise NumericContractError('explicit_dl2_cohort_file_required')
        _cohort(read(dl2_cohort),raw)
        payload['dl2'] = {'result':_reference(root, dl2), 'index':_reference(root, dl2_index),
                          'expected':dl2_expected, 'cohort':_reference(root,dl2_cohort), 'native_profile':'dl2_search_e46a44e'}
    write(out, payload)
    return payload


def create_context(dataset_root, export, out, *, asset_manifest, force=False):
    """Compute original numeric values independently, then bind ready original ROIs."""
    from .adapters.numeric import load_dataset
    from .linepixel import compute_areas
    from .electrode_map import compute_electrode_maps
    from .roi_export import check_asset_manifest, _frame_payload
    from .report import map_report
    import numpy as np
    if Path(out).exists() and not force:
        raise NumericContractError('output_exists_use_force',str(out))
    dataset = load_dataset(Path(dataset_root))
    original = read(export)
    if original.get('schema_version') != 'review_roi_v1' or original['source_manifest_hash'] != dataset.provenance['input_hash']:
        raise NumericContractError('review_export_source_mismatch')
    assets, reasons = check_asset_manifest(dataset, Path(dataset_root), asset_manifest)
    if reasons:
        raise NumericContractError('original_asset_manifest_unavailable', str(reasons))
    areas = compute_areas(dataset)
    maps = compute_electrode_maps(dataset)
    original_map = map_report(maps)[0]
    map_files = {}
    pending_arrays = []
    prefix = 'numeric_map/'+Path(out).stem+'_'+uuid.uuid4().hex
    for kind in ('layers','summaries'):
        for entry,encoded in zip(getattr(maps,kind),original_map[kind]):
            if encoded['array_file'] is None:
                continue
            relative = prefix+'/'+encoded['array_file']
            arrays = {'valid':entry.frame.valid}
            if entry.counts is not None:
                arrays['counts'] = entry.counts
            encoded['array_file'] = relative
            pending_arrays.append((relative,arrays))
    context = {'schema_version':CONTEXT_SCHEMA, 'input_hash':dataset.provenance['input_hash'],
               'is_synthetic':areas.is_synthetic,
               'review_versions':{k:original[k] for k in ('schema_version','producer_commit','source_manifest_hash')},
               'review_export_sha256':sha(export), 'records':[],
               'blocked_rois':[r['roi_id'] for r in original['rois'] if r['status'] != 'ready'],
               'original_areas':areas.to_dict(), 'original_electrode_map':original_map,
               'original_map_files':map_files,
               'gates':deepcopy(original['gates'])}
    for row in original['rois']:
        if row['status'] != 'ready':
            continue
        key = identity(row['source_identity'])
        cases = [c for c in dataset.cases if identity(asdict(c.identity)) == key]
        if len(cases) != 1:
            raise NumericContractError('review_case_identity_mismatch')
        case = cases[0]
        info = assets[key]
        if frame(row['frame']) != frame(_frame_payload(dataset.frames[case.identity.frame_id])) or row['source_assets'] != info['assets'] or row['source_version'] != info['source_version']:
            raise NumericContractError('review_frame_or_assets_mismatch')
        diameter = row['provenance'].get('representative_diameter_um')
        # Manual positions have no diameter. Preserve all observations, never assign one.
        linked = []
        for group in areas.groups.values():
            if diameter is not None and group.key.diameter_um != diameter:
                continue
            for source in group.source_cases:
                if identity(asdict(source.identity)) == key:
                    linked.append({'diameter_um':group.key.diameter_um, 'original_case_ca':asdict(source),
                                   'line':asdict(group.line.area), 'pixel':asdict(group.pixel.area),
                                   'group':asdict(group.key)})
        context['records'].append({'roi_id':row['roi_id'], 'source_identity':row['source_identity'],
            'frame':row['frame'], 'source_assets':row['source_assets'], 'source_version':row['source_version'],
            'endpoints':row['endpoints'],'failure_type':row['failure_type'],'analysis_layer':row['analysis_layer'],
            'center_xy_um':row['center_xy_um'], 'original_values':linked,
            'numeric_status':'available' if linked else 'unavailable',
            'numeric_reasons':[] if linked else ['representative_diameter_group_unavailable' if diameter is not None else 'numeric_group_unavailable']})
    destination = Path(out).parent/prefix
    try:
        for relative,arrays in pending_arrays:
            path = Path(out).parent/relative
            path.parent.mkdir(parents=True,exist_ok=True)
            np.savez_compressed(path,**arrays)
            map_files[relative] = sha(path)
        write(out, context)
    except Exception:
        shutil.rmtree(destination,ignore_errors=True)
        raise
    return context


def read_results(exchange):
    root = Path(exchange).parent
    profile = read(exchange)
    if profile.get('schema_version') != SCHEMA:
        raise NumericContractError('dl_exchange_schema_mismatch')
    context = _file(root, profile['numeric_context'])
    if context.get('schema_version') != CONTEXT_SCHEMA:
        raise NumericContractError('numeric_context_schema_mismatch')
    from .numeric_images import resolve_path
    for path,expected in context['original_map_files'].items():
        if sha(resolve_path(root,path)) != expected:
            raise NumericContractError('original_numeric_map_hash_mismatch',path)
    numeric = {}
    for row in context['records']:
        key = (row['roi_id'], identity(row['source_identity']))
        if key in numeric:
            raise NumericContractError('duplicate_numeric_roi_identity')
        numeric[key] = row
    result = {'schema_version':'dl_numeric_join_v1', 'numeric':deepcopy(context), 'dl1':None, 'dl2':None,
              'pending_gates':sorted(set(profile['pending_gates']) | {k for k,v in context.get('gates',{}).items() if v.get('status')=='pending'}),
              'interpretation':'similarity_only_never_numeric_risk'}
    unavailable = {'status':'unavailable', 'value':None, 'reasons':['dl_result_missing'], 'matches':[]}
    result['dl1'] = deepcopy(unavailable)
    result['dl2'] = {k:v for k,v in deepcopy(unavailable).items() if k!='matches'}
    result['dl2'].update(primary=[],supplementary=[],primary_cohort=[],expanded=False,coverage=None)
    def join(row):
        if row.get('case_id') != row['source_identity']['case_id'] or row.get('product_id') != row['source_identity']['product_id']:
            raise NumericContractError('dl2_redundant_identity_mismatch')
        original = numeric.get((row['roi_id'], identity(row['source_identity'])))
        if original is None:
            raise NumericContractError('numeric_composite_identity_mismatch', row['roi_id'])
        if any(row['source_versions'].get(k) != v for k,v in context['review_versions'].items()):
            raise NumericContractError('dl2_numeric_producer_version_mismatch')
        signature=row['case_signature']
        if any(signature.get(k) != original[k] for k in ('endpoints','failure_type','analysis_layer')):
            raise NumericContractError('dl2_numeric_case_binding_mismatch')
        for key in ('frame', 'source_assets', 'source_version', 'center_xy_um'):
            a, b = row[key], original[key]
            if key == 'frame':
                a, b = frame(a), frame(b)
            if a != b:
                raise NumericContractError('dl2_numeric_'+key+'_mismatch', row['roi_id'])
        return {'original_values':deepcopy(original['original_values']),
                'numeric_status':original.get('numeric_status','available' if original['original_values'] else 'unavailable'),
                'numeric_reasons':original.get('numeric_reasons',[])}
    if profile['dl2']:
        p = profile['dl2']
        if p['native_profile'] != 'dl2_search_e46a44e':
            raise NumericContractError('unsupported_dl2_native_profile')
        raw, index = _file(root, p['result']), _file(root, p['index'])
        _dl2(raw, index, p['expected'])
        if 'cohort' not in p:
            raise NumericContractError('explicit_dl2_cohort_file_required')
        _cohort(_file(root,p['cohort']),raw)
        join(raw['query'])
        matches = []
        for scope in ('primary', 'supplementary'):
            for row in raw[scope]:
                matches.append({**deepcopy(row), **join(row), 'search_scope':scope})
        result['dl2'] = {'status':raw['status'], 'reasons':raw['no_match_reasons'],
                         'primary':[r for r in matches if r['search_scope']=='primary'],
                         'supplementary':[r for r in matches if r['search_scope']=='supplementary'],
                         'primary_cohort':raw['primary_cohort'],'expanded':raw['expanded'],
                         'coverage':{'query':raw['query']['valid_coverage'],
                                     'matches':{r['roi_id']:r['valid_coverage'] for r in matches}},
                         'query':deepcopy(raw['query']), 'rejected':raw['rejected']}
    if profile['dl1']:
        p = profile['dl1']
        if p['native_profile'] != 'dl1_search_7f9d659':
            raise NumericContractError('unsupported_dl1_native_profile')
        raw = _file(root, p['result'])
        _dl1(raw, p['config_fingerprint'])
        bindings = _file(root, p['bindings']) if p['bindings'] else []
        matches = []
        missing = False
        for row in raw['rankings']:
            associated = [b for b in bindings if b['product_id'] == row['product_id'] and b['input_identity'] == row['input_identity']]
            if not associated:
                missing = True
                matches.append({**deepcopy(row), 'status':'unavailable', 'original_values':None,
                                'reasons':['dl1_product_source_context_required']})
                continue
            linked = []
            for binding in associated:
                original = numeric.get((binding['roi_id'], identity(binding['source_identity'])))
                if original is None or original['source_identity']['product_id'] != row['product_id']:
                    raise NumericContractError('dl1_numeric_composite_identity_mismatch')
                for field in ('frame', 'source_assets', 'source_version'):
                    if binding[field] != original[field]:
                        raise NumericContractError('dl1_numeric_'+field+'_mismatch')
                linked.append(deepcopy(original))
            matches.append({**deepcopy(row), 'status':'available', 'original_values':linked, 'reasons':[]})
        result['dl1'] = {**deepcopy(raw), 'status':'partial' if missing else raw['status'], 'matches':matches}
    if result['pending_gates']:
        result['status'] = 'pending'
    else:
        result['status'] = 'available' if all(result[k]['status']=='available' for k in ('dl1','dl2')) else 'partial'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='파일 교환: DL similarity를 원본 수치 옆에 표시 (수치 변경 없음)')
    commands = parser.add_subparsers(dest='command', required=True)
    c = commands.add_parser('context'); c.add_argument('dataset'); c.add_argument('export'); c.add_argument('--asset-manifest', required=True); c.add_argument('--out', required=True)
    c = commands.add_parser('seal'); c.add_argument('--context', required=True); c.add_argument('--dl1'); c.add_argument('--dl1-fingerprint'); c.add_argument('--dl1-bindings'); c.add_argument('--dl2'); c.add_argument('--dl2-index'); c.add_argument('--dl2-cohort'); c.add_argument('--dl2-expected'); c.add_argument('--pending-gates'); c.add_argument('--out', required=True)
    c = commands.add_parser('read'); c.add_argument('exchange'); c.add_argument('--out', required=True)
    for command in commands.choices.values():
        command.add_argument('--force',action='store_true',help='기존 출력 교체를 명시적으로 허용')
    args = parser.parse_args(argv)
    try:
        if args.command == 'context':
            path = Path(args.dataset)/args.asset_manifest
            create_context(args.dataset, args.export, args.out, asset_manifest={'path':args.asset_manifest,'sha256':sha(path)},force=args.force)
        elif args.command == 'seal':
            create_exchange(args.out, context=args.context, dl1=args.dl1, dl1_fingerprint=args.dl1_fingerprint,
                            dl1_bindings=args.dl1_bindings, dl2=args.dl2, dl2_index=args.dl2_index,
                            dl2_expected=read(args.dl2_expected) if args.dl2_expected else None, dl2_cohort=args.dl2_cohort,force=args.force,
                            pending_gates=read(args.pending_gates) if args.pending_gates else None)
        else:
            write(args.out, read_results(args.exchange),force=args.force)
        return 0
    except (NumericContractError, OSError, ValueError, TypeError, KeyError) as exc:
        if args.command=='read':
            Path(args.out).unlink(missing_ok=True)
        print(json.dumps({'status':'unavailable','reason':str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
