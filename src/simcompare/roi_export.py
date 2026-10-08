"""C → DL2 review_roi_v1 위치 내보내기. 원본 asset은 읽기 검증만 한다.

hotspot/CA/density/rate는 asset 또는 tensor 입력으로 내보내지 않는다.
G4 pending에는 crop 크기를 만들지 않고 G7b 지도 선택은 blocked로 남긴다.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np

from .numeric_images import check_hash, read_binary, resolve_path
from .numeric_schema import CaseIdentity, Frame, NumericContractError, finite, text, require_same_frame

IDENTITY_FIELDS = tuple(CaseIdentity.__dataclass_fields__)


def _identity(raw):
    return tuple(raw.get(k) for k in IDENTITY_FIELDS)


def _json_load(path):
    def unique(pairs):
        result = {}
        for k,v in pairs:
            if k in result:
                raise NumericContractError('duplicate_asset_manifest_key',k)
            result[k] = v
        return result
    def constant(value):
        raise NumericContractError('nonfinite_asset_manifest_number',value)
    return json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=unique,parse_constant=constant)


def _frame_payload(frame):
    return {'frame_id':frame.frame_id,'length_unit':'um','origin_xy_um':list(frame.origin_xy_um),
            'pixel_pitch_um':frame.pixel_pitch_um,'shape_yx':list(frame.shape_yx),
            'axis_order':frame.axis_order,'origin_convention':frame.origin_convention}


def _asset(root, ref, frame, *, valid=False):
    if not isinstance(ref,dict) or not {'path','sha256','value_semantics','observed'} <= set(ref):
        raise NumericContractError('source_asset_reference_required')
    if ref['observed'] is not True:
        raise NumericContractError('source_asset_unobserved')
    arr = read_binary(root,ref['path'],sha256=ref['sha256'],value_semantics=ref['value_semantics'])
    if arr.shape != frame.shape_yx:
        raise NumericContractError('source_asset_frame_mismatch')
    if valid and not np.array_equal(arr,frame.valid):
        raise NumericContractError('source_valid_mismatch')
    return {k:ref[k] for k in ('path','sha256','value_semantics','observed')}


def check_asset_manifest(dataset, root, reference):
    """최소 합성 원본 asset manifest 검증; 실제 source 형식 승인이 아니다.

    반환 mapping에는 원본 identity/frame/binding과 검증한 asset만 있다.
    한 case의 누락/불일치는 그 case의 blocked 사유로 보존한다.
    """
    if reference is None:
        return {}, ['source_asset_manifest_required']
    try:
        if not isinstance(reference,dict) or not {'path','sha256'} <= set(reference):
            raise NumericContractError('source_asset_manifest_reference_required')
        path = resolve_path(root,reference['path'])
        check_hash(path,reference['sha256'])
        manifest = _json_load(path)
        if manifest.get('schema_version') != 'review_assets_v1':
            raise NumericContractError('unsupported_asset_manifest')
        if manifest.get('source_manifest_hash') != dataset.provenance['input_hash']:
            raise NumericContractError('source_asset_manifest_identity_mismatch')
        entries = {}
        for entry in manifest['cases']:
            identity = _identity(entry['identity'])
            CaseIdentity(**entry['identity'])
            if identity in entries:
                raise NumericContractError('duplicate_asset_case_identity')
            entries[identity] = entry
    except (NumericContractError,OSError,ValueError,TypeError,KeyError) as exc:
        return {}, ['source_asset_manifest_invalid',getattr(exc,'code','malformed_asset_manifest')]
    result = {}
    for case in dataset.cases:
        key = _identity(asdict(case.identity))
        raw = entries.get(key)
        if raw is None:
            result[key] = {'assets':None,'reasons':['source_shape_required'],'source_version':None}
            continue
        try:
            frame = dataset.frames[case.identity.frame_id]
            if raw['frame'] != _frame_payload(frame):
                raise NumericContractError('source_asset_frame_mismatch')
            descriptor = raw['frame']
            # Python의 bool == 1 비교가 물리 metadata 검증을 우회하지 않도록 T2 타입 검사도 한다.
            try:
                declared = Frame(descriptor['frame_id'],descriptor['pixel_pitch_um'],
                                 tuple(descriptor['origin_xy_um']),tuple(descriptor['shape_yx']),frame.valid,
                                 descriptor['axis_order'],descriptor['origin_convention'])
                require_same_frame(declared,frame)
            except NumericContractError as exc:
                raise NumericContractError('source_asset_frame_mismatch') from exc
            eps = [asdict(e) for e in case.endpoints]
            if raw['endpoints'] != eps or raw['failure_type'] != case.failure_type:
                raise NumericContractError('source_asset_case_binding_mismatch')
            version = text(raw['source_version'],'source_version')
            assets = raw['assets']
            if set(assets)-{'layouts','valid','cnt','via'}:
                raise NumericContractError('unsupported_review_asset_kind')
            layouts = assets.get('layouts')
            if not isinstance(layouts,list) or len(layouts) != len(eps):
                raise NumericContractError('source_shape_required')
            bound = []
            for endpoint,entry in zip(eps,layouts):
                if entry['endpoint'] != endpoint:
                    raise NumericContractError('source_asset_endpoint_binding_mismatch')
                bound.append({'endpoint':endpoint,'asset':_asset(root,entry['asset'],frame)})
            validated = {'layouts':bound,'valid':_asset(root,assets.get('valid'),frame,valid=True),
                         'cnt':_asset(root,assets.get('cnt'),frame),
                         'via':[_asset(root,entry,frame) for entry in assets.get('via',[])]}
            result[key] = {'assets':validated,'reasons':[],'source_version':version}
        except (NumericContractError,OSError,ValueError,TypeError,KeyError) as exc:
            result[key] = {'assets':None,'reasons':['source_shape_required',getattr(exc,'code','malformed_source_asset')],
                           'source_version':None}
    return result, []


def _stable_id(payload):
    return 'roi_'+hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),
                                          ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _manual_case(dataset, request):
    reasons = []
    if not request.get('product_id') or not request.get('design_version'):
        reasons.append('source_identity_required')
    if not request.get('frame_id'):
        reasons.append('source_frame_required')
    if not request.get('case_id') and not request.get('endpoints'):
        return None, sorted(set(reasons+['case_context_required']))
    matches = []
    for case in dataset.cases:
        identity = asdict(case.identity)
        if any(identity[k] != request.get(k) for k in ('product_id','design_version','frame_id')):
            continue
        if request.get('case_id') and identity['case_id'] != request['case_id']:
            continue
        if any(k in request and identity[k] != request[k] for k in IDENTITY_FIELDS):
            continue
        if 'endpoints' in request and request['endpoints'] != [asdict(e) for e in case.endpoints]:
            continue
        if 'failure_type' in request and request['failure_type'] != case.failure_type:
            continue
        matches.append(case)
    if len(matches) != 1:
        return None, sorted(set(reasons+['case_context_required' if not matches else 'case_context_ambiguous']))
    return matches[0], reasons


def export_review_rois(dataset, source_root, out, *, maps=None, selections=None,
                       asset_manifest=None, producer_commit=None):
    """설정에 명시한 layer region 및 manual 위치만 내보낸다. 학습/검색 호출 없음."""
    root = Path(source_root)
    selections = {} if selections is None else selections
    if not isinstance(selections,dict) or set(selections)-{'map_region_ids','manual'}:
        raise NumericContractError('invalid_roi_selection')
    assets, manifest_reasons = check_asset_manifest(dataset,root,asset_manifest)
    rows, excluded = [], []
    def add(kind, case, center, region=None, request=None, reasons=()):
        reasons = set(reasons) | set(manifest_reasons)
        identity = asdict(case.identity) if case else {k:(request or {}).get(k) for k in IDENTITY_FIELDS}
        frame = dataset.frames.get(identity['frame_id'])
        if frame is None:
            reasons.add('source_frame_required')
        asset_info = assets.get(_identity(identity),{'assets':None,'reasons':['source_shape_required'],'source_version':None})
        reasons.update(asset_info['reasons'])
        if kind == 'electrode_count_region':
            reasons.add('map_scope_unconfirmed')
            if frame is not None:
                try:
                    require_same_frame(region.frame,frame)
                except NumericContractError:
                    reasons.add('region_source_frame_mismatch')
            if region.coverage['source_coverage_status'] != 'available':
                reasons.add('map_coverage_incomplete')
        try:
            if not isinstance(center,(list,tuple)) or len(center) != 2:
                raise NumericContractError('physical_center_required')
            center = [finite(v,'center_xy_um') for v in center]
            if frame is not None:
                x0,y0,x1,y1 = frame.bbox_um
                if not (x0 <= center[0] < x1 and y0 <= center[1] < y1):
                    reasons.add('position_outside_source_frame')
                else:
                    col = int((center[0]-x0)/frame.pixel_pitch_um)
                    row = int((center[1]-y0)/frame.pixel_pitch_um)
                    if not frame.valid[row,col]:
                        reasons.add('position_outside_valid_domain')
        except NumericContractError as exc:
            reasons.add(exc.code)
            center = None
        provenance = ({'representative_diameter_um':region.provenance['representative_diameter_um'],
                       'threshold':region.provenance['threshold'],'connectivity':4,
                       'coverage':region.coverage,'G7b':region.provenance['G7b'],
                       'position_policy':'region_bbox_center'} if region else {'position_policy':'explicit_manual_point'})
        row = {'product_id':identity['product_id'],'design_version':identity['design_version'],
               'case_id':identity['case_id'],'source_identity':identity,
               'source_kind':kind,'source_region_id':None if region is None else region.region_id,
               'selection_policy_version':'review_roi_selection_v1',
               'frame':None if frame is None else _frame_payload(frame),'frame_id':identity['frame_id'],
               'center_xy_um':center,'region_bbox_um':None if region is None else list(region.region_bbox_um),
               'endpoints':None if case is None else [asdict(e) for e in case.endpoints],
               'failure_type':None if case is None else case.failure_type,
               'analysis_layer':None if case is None else case.analysis_layer,
               'source_assets':asset_info['assets'],'source_version':asset_info['source_version'],
               'provenance':provenance,'crop_recipe':{'status':'pending','value':None,'reasons':['crop_recipe_unconfirmed']},
               'status':'blocked' if reasons else 'ready','reasons':sorted(reasons)}
        # asset/hash는 provenance이며 ROI identity는 위치와 복합 source 문맥으로 안정화한다.
        row['roi_id'] = _stable_id({'source_kind':kind,'source_region_id':row['source_region_id'],
                                   'source_identity':identity,'center_xy_um':center,
                                   'request':{**request,'center_xy_um':center} if case is None and request is not None else None,
                                   'selection_policy_version':row['selection_policy_version']})
        rows.append(row)
        if reasons:
            excluded.append({'roi_id':row['roi_id'],'source_identity':identity,'source_kind':kind,
                             'reasons':row['reasons']})
    selected = selections.get('map_region_ids',[])
    if not (selected == 'all' or isinstance(selected,list) and all(isinstance(s,str) for s in selected)):
        raise NumericContractError('invalid_region_selection')
    regions = [] if maps is None else [r for layer in maps.layers for r in layer.regions]
    by_identity = {_identity(asdict(c.identity)):c for c in dataset.cases}
    found = set()
    for region in regions:
        if selected != 'all' and region.region_id not in selected:
            continue
        found.add(region.region_id)
        for link in region.cases:
            case = by_identity.get(_identity(link.identity))
            reasons = []
            if case is None:
                reasons.append('case_context_required')
            elif (tuple(asdict(e) for e in case.endpoints) != link.endpoints
                  or case.failure_type != link.failure_type):
                reasons.append('region_source_case_binding_mismatch')
            add('electrode_count_region',case,region.center_xy_um,region,
                request=link.identity if case is None else None,reasons=reasons)
    if selected != 'all':
        for missing in sorted(set(selected)-found):
            excluded.append({'source_kind':'electrode_count_region','source_region_id':missing,
                             'reasons':['source_region_missing']})
    manual = selections.get('manual',[])
    if not isinstance(manual,list) or any(not isinstance(r,dict) for r in manual):
        raise NumericContractError('invalid_manual_roi_selection')
    allowed = set(IDENTITY_FIELDS) | {'center_xy_um','endpoints','failure_type'}
    for request in manual:
        if set(request)-allowed:
            raise NumericContractError('unsupported_manual_roi_fields')
        case,reasons = _manual_case(dataset,request)
        add('manual',case,request.get('center_xy_um'),request=request,reasons=reasons)
    # 중복 지정은 같은 ROI 한 행으로 묶고 입력 순서와 무관하게 정렬한다.
    rows = sorted({row['roi_id']:row for row in rows}.values(),key=lambda r:r['roi_id'])
    excluded = sorted(excluded,key=lambda r:json.dumps(r,sort_keys=True,ensure_ascii=False))
    if producer_commit is None:
        result = subprocess.run(['git','rev-parse','HEAD'],cwd=Path(__file__).resolve().parents[2],
                                capture_output=True,text=True)
        producer_commit = result.stdout.strip() if result.returncode == 0 else None
    payload = {'schema_version':'review_roi_v1','producer_commit':producer_commit,
               'source_manifest_hash':dataset.provenance['input_hash'],'assets_root':str(root.resolve()),
               'rois':rows,'excluded_records':excluded,
               'gates':{g:{'status':'pending','answer':None} for g in ('G1','G2','G4','G7a','G7b')},
               'tensor_input_policy':'original_layout_CNT_VIA_valid_only',
               'status':'partial' if excluded else 'available'}
    folder = Path(out)
    folder.mkdir(parents=True,exist_ok=True)
    path = folder/'review_rois.json'
    path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return {'status':payload['status'],'outputs':[str(path)],'n_rois':len(rows),
            'n_ready':sum(r['status']=='ready' for r in rows),'n_blocked':sum(r['status']=='blocked' for r in rows),
            'exit_code':3 if excluded else 0}
