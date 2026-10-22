"""C → DL2 review_roi_v1 위치 내보내기. 원본 asset은 읽기 검증만 한다.

hotspot/CA/density/rate는 asset 또는 tensor 입력으로 내보내지 않는다.
G4 pending에는 crop 크기를 만들지 않는다. 기본 위치 출처는 사용자 지정이다.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess

import numpy as np

from .numeric_images import MASK_SEMANTICS, check_hash, read_binary, resolve_path
from .numeric_schema import CaseIdentity, Frame, NumericContractError, finite, text, require_same_frame, current_gate_decisions

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


class AssetMemo:
    """Invocation-scoped, bounded record of assets that were already decoded and checked in this run.

    Key: absolute resolved path + sha256 + value_semantics; value: (shape, sha256 of the decoded mask bytes), never an
    array. A hit still resolves the path and re-hashes the file (a changed or missing file fails exactly as before);
    only the PNG decode is skipped, which is deterministic for identical bytes. Every use re-checks the frame shape and,
    for `valid`, equality with the frame's valid mask, so one asset may serve cases of different frames or roots."""

    def __init__(self, max_entries=100_000):
        self.max_entries = max_entries
        self.entries = {}
        self.decodes = self.hits = 0


def _count_asset(kind):
    from .adapters import real_hotspot
    if real_hotspot.IO_COUNTERS is not None:     # benchmark counters: assets decoded / served from the memo
        real_hotspot.IO_COUNTERS[kind] = real_hotspot.IO_COUNTERS.get(kind, 0) + 1


def _mask_digest(mask):
    return hashlib.sha256(mask.astype('uint8').tobytes()).hexdigest()


def _read_asset(root, ref, memo):
    """(shape, digest-of-the-decoded-mask-bytes-or-None-when-not-needed, mask-or-None); the mask only on a miss."""
    semantics, expected = ref['value_semantics'], ref['sha256']
    if memo is None or semantics not in MASK_SEMANTICS or not isinstance(expected, str):
        mask = read_binary(root, ref['path'], sha256=expected, value_semantics=semantics)
        return mask.shape, None, mask
    path = resolve_path(root, ref['path'])
    key = (str(path), expected, semantics)
    entry = memo.entries.get(key)
    if entry is not None:
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise NumericContractError('input_hash_mismatch', ref['path'])
        memo.hits += 1
        _count_asset('asset_memo_hits')
        return entry[0], entry[1], None
    mask = read_binary(root, ref['path'], sha256=expected, value_semantics=semantics)
    memo.decodes += 1
    _count_asset('asset_decodes')
    if len(memo.entries) < memo.max_entries:
        memo.entries[key] = (mask.shape, _mask_digest(mask))
    return mask.shape, None, mask


def _asset(root, ref, frame, *, valid=False, memo=None):
    if not isinstance(ref,dict) or not {'path','sha256','value_semantics','observed'} <= set(ref):
        raise NumericContractError('source_asset_reference_required')
    if ref['observed'] is not True:
        raise NumericContractError('source_asset_unobserved')
    shape, digest, mask = _read_asset(root, ref, memo)
    if shape != frame.shape_yx:
        raise NumericContractError('source_asset_frame_mismatch')
    if valid:
        if mask is not None:
            same = np.array_equal(mask,frame.valid)
        else:
            same = digest == frame.valid_hash
        if not same:
            raise NumericContractError('source_valid_mismatch')
    return {k:ref[k] for k in ('path','sha256','value_semantics','observed')}


def check_asset_manifest(dataset, root, reference, memo=None):
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
                bound.append({'endpoint':endpoint,'asset':_asset(root,entry['asset'],frame,memo=memo)})
            validated = {'layouts':bound,'valid':_asset(root,assets.get('valid'),frame,valid=True,memo=memo),
                         'cnt':_asset(root,assets.get('cnt'),frame,memo=memo),
                         'via':[_asset(root,entry,frame,memo=memo) for entry in assets.get('via',[])]}
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


COVERAGE_LISTS = ('observed', 'missing', 'not_applicable', 'measurement_errors')
SCHEMA_VERSION = 'review_roi_v1'
_HOOKS = []


@contextmanager
def export_hooks(*, row_gate=None, asset_memo=None, reserved_bytes=0):
    """Caller hooks for the export_review_rois calls made inside the block (candidates uses them through
    pipeline._real_roi_export, which has no parameter for them).

    row_gate(rows) -> int may turn ready rows into blocked ones BEFORE the file is written (the single write then carries
    the gate's result; a non-zero return makes an 'available' export 'partial'); asset_memo is an AssetMemo shared by the
    asset checks. The yielded dict receives the last export's `rows` and `payload` (everything but rows and
    region_coverages), so the caller never has to re-read the file it just caused to be written.
    """
    state = {'row_gate': row_gate, 'asset_memo': asset_memo, 'reserved_bytes': reserved_bytes, 'rows': None, 'payload': None}
    _HOOKS.append(state)
    try:
        yield state
    finally:
        _HOOKS.remove(state)


def _json_stream(path, payload):
    """Write payload exactly as json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)+'\\n' would, chunk by chunk,
    to a temporary file that replaces `path` only when complete (no multi-GB string, no partial file on failure)."""
    path = Path(path)
    temporary = path.with_name(f'{path.name}.tmp-{os.getpid()}')
    try:
        with open(temporary, 'w', encoding='utf-8') as handle:
            for chunk in json.JSONEncoder(ensure_ascii=False, indent=2, allow_nan=False).iterencode(payload):
                handle.write(chunk)
            handle.write('\n')
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def file_sha256(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(chunk), b''):
            digest.update(block)
    return digest.hexdigest()


def expand_region_coverages(payload):
    """The full-identity (pre-reference) form of an export: every row's `provenance.coverage` carries the four identity lists
    in place of `observed_ref` and the top-level `region_coverages` is dropped. Applied to a parsed slim export and dumped
    with indent=2 it reproduces, byte for byte, what 7bcb07a wrote for the same run. Exports without references are returned
    unchanged."""
    shared = payload.get('region_coverages')
    if not isinstance(shared, dict):
        return payload
    rows = []
    for row in payload.get('rois', []):
        coverage = ((row.get('provenance') or {}).get('coverage')) if isinstance(row, dict) else None
        if isinstance(coverage, dict) and 'observed_ref' in coverage:
            lists = shared[coverage['observed_ref']]
            expanded = {k: lists[k] for k in COVERAGE_LISTS if k in lists}
            expanded.update((k, v) for k, v in coverage.items() if k != 'observed_ref')
            row = {**row, 'provenance': {**row['provenance'], 'coverage': expanded}}
        rows.append(row)
    return {k: (rows if k == 'rois' else v) for k, v in payload.items() if k != 'region_coverages'}


def export_review_rois(dataset, source_root, out, *, maps=None, selections=None,
                       asset_manifest=None, producer_commit=None):
    """설정에 명시한 layer region 및 manual 위치만 내보낸다. 학습/검색 호출 없음.

    region 행의 provenance.coverage는 case 식별 목록(observed/missing/not_applicable/measurement_errors) 대신
    `observed_ref`만 갖고, 목록은 최상위 `region_coverages`에 한 번만 쓴다(행 수 x case 수로 커지는 중복 제거).
    """
    hooks = _HOOKS[-1] if _HOOKS else {}
    root = Path(source_root)
    selections = {} if selections is None else selections
    if not isinstance(selections,dict) or set(selections)-{'map_region_ids','manual'}:
        raise NumericContractError('invalid_roi_selection')
    assets, manifest_reasons = check_asset_manifest(dataset,root,asset_manifest,memo=hooks.get('asset_memo'))
    rows, excluded = [], []
    shared_coverages, coverage_refs, slim_coverages = {}, {}, {}

    def region_coverage(region):
        """The row form of the region's coverage: its identity lists replaced by one reference to a shared copy."""
        coverage = region.coverage
        if not any(k in coverage for k in COVERAGE_LISTS):
            return coverage
        if id(coverage) not in slim_coverages:
            lists = {k: coverage[k] for k in COVERAGE_LISTS if k in coverage}
            lists_key = tuple(id(v) for v in lists.values())
            if lists_key not in coverage_refs:
                text_form = json.dumps(lists, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
                coverage_refs[lists_key] = 'coverage_' + hashlib.sha256(text_form.encode()).hexdigest()[:24]
                shared_coverages[coverage_refs[lists_key]] = lists
            slim, placed = {}, False
            for key, value in coverage.items():
                if key in COVERAGE_LISTS:
                    if not placed:
                        slim['observed_ref'], placed = coverage_refs[lists_key], True
                else:
                    slim[key] = value
            slim_coverages[id(coverage)] = slim
        return slim_coverages[id(coverage)]
    def add(kind, case, center, region=None, request=None, reasons=()):
        reasons = set(reasons) | set(manifest_reasons)
        identity = asdict(case.identity) if case else {k:(request or {}).get(k) for k in IDENTITY_FIELDS}
        frame = dataset.frames.get(identity['frame_id'])
        if frame is None:
            reasons.add('source_frame_required')
        asset_info = assets.get(_identity(identity),{'assets':None,'reasons':['source_shape_required'],'source_version':None})
        reasons.update(asset_info['reasons'])
        if kind == 'electrode_count_region':
            if (region.provenance.get('threshold') is None
                    or region.provenance.get('regions_requested') is not True):
                reasons.add('map_threshold_required')
            if region.provenance.get('G7b', {}).get('value') != 'short_only':
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
                       'coverage':region_coverage(region),'G7b':region.provenance['G7b'],
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
    manual = selections.get('manual',[])
    if isinstance(dataset.provenance.get('resource_estimate'), dict):
        from .adapters.real_hotspot import guard_stage_map_export
        planned = sum(len(r.cases) for r in regions if selected == 'all' or r.region_id in selected)
        guard_stage_map_export('roi-export',dataset,maps,planned+(len(manual) if isinstance(manual,list) else 0),
                               reserved=hooks.get('reserved_bytes',0))
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
    if selected == 'all' and (maps is None or maps.provenance.get('threshold') is None):
        excluded.append({'source_kind':'electrode_count_region','reasons':['map_threshold_required']})
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
    commit_reason=None
    if producer_commit is None:
        package_root=Path(__file__).resolve().parents[2]
        if not (package_root/'.git').exists():
            commit_reason='producer_not_in_git_checkout'
        else:
            try:
                tracked=subprocess.run(['git','ls-files','--error-unmatch','src/simcompare/roi_export.py'],cwd=package_root,capture_output=True,text=True)
                if tracked.returncode!=0:
                    commit_reason='producer_source_not_tracked'
                else:
                    result=subprocess.run(['git','rev-parse','HEAD'],cwd=package_root,capture_output=True,text=True)
                    producer_commit=result.stdout.strip() if result.returncode==0 else None
                    if producer_commit is None:commit_reason='producer_git_revision_unavailable'
            except OSError:
                commit_reason='producer_git_unavailable'
    gated = 0
    if hooks.get('row_gate') is not None:
        gated = hooks['row_gate'](rows)
    payload = {'schema_version':SCHEMA_VERSION,'producer_commit':producer_commit,'producer_commit_reason':commit_reason,
               'source_manifest_hash':dataset.provenance['input_hash'],'assets_root':str(root.resolve()),
               **({'region_coverages':dict(sorted(shared_coverages.items()))} if shared_coverages else {}),
               'rois':rows,'excluded_records':excluded,
               'gates':current_gate_decisions(),
               'tensor_input_policy':'original_layout_CNT_VIA_valid_only',
               'status':'partial' if excluded or gated else 'available'}
    folder = Path(out)
    folder.mkdir(parents=True,exist_ok=True)
    path = folder/'review_rois.json'
    _json_stream(path,payload)
    if hooks:
        hooks['rows'] = rows
        hooks['payload'] = {k:v for k,v in payload.items() if k not in ('rois','region_coverages')}
    return {'status':payload['status'],'outputs':[str(path)],'n_rois':len(rows),
            'n_ready':sum(r['status']=='ready' for r in rows),'n_blocked':sum(r['status']=='blocked' for r in rows),
            'exit_code':3 if excluded else 0}
