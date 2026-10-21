"""export-template-rois: 한 제품의 template ROI를 hotspot·CA를 열지 않고 내보낸다.

입력 트리에는 case 폴더의 1.png/2.png(처리 마스크)만 둔다. hotspot 파일명 패턴이나 result/ 폴더가 보이면
열지 않고 template_hotspot_input_present로 거부한다. 기존 import-hotspots, import-electrode-assets,
export-review-rois 경로를 같은 함수로 호출하고, 그 위에 ROI별 mask_derivation만 더한다. 기존 모듈은 바꾸지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import yaml

from simcompare.numeric_schema import NumericContractError

CONFIG_VERSION = 'template_export_config_v1'
EXECUTION_VERSION = 'template_export_execution_v1'
DERIVATION_VERSION = 'psdl2_mask_derivation_v1'
RULE_VERSION = 'between_stack_order_v1'
# import-electrode-assets가 요구하는 값이다. 이 명령은 density를 쓰지 않으며 출력에도 남지 않는다.
_DENSITY_SENTINEL = 'template_export_unused'
_ID_FIELDS = ('product_id', 'design_version', 'source_id', 'process_step_id', 'exposure_id', 'unit_id')
_ALLOWED = {'schema_version', 'data_dir', 'cnt_from_dl1_root', 'layer_stack', 'electrode_roles', 'electrode_role_policy',
            'pair_table_path', 'source_version', 'max_asset_memory_mb', 'rois', 'mask_derivation_rule', *_ID_FIELDS}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(payload) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def _fail(code, detail=''):
    return NumericContractError(code, detail)


def _text(config, key):
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _fail('invalid_template_config', f'{key}는 비어 있지 않은 문자열이어야 합니다')
    return value


def _load_config(config_path: Path):
    raw = config_path.read_bytes()
    config = yaml.safe_load(raw.decode('utf-8-sig'))
    if not isinstance(config, dict) or config.get('schema_version') != CONFIG_VERSION:
        raise _fail('invalid_template_config', f'schema_version은 {CONFIG_VERSION}이어야 합니다')
    unknown = sorted(set(config) - _ALLOWED)
    if unknown:
        raise _fail('invalid_template_config', '알 수 없는 필드: ' + ', '.join(str(k) for k in unknown))
    rule = config.get('mask_derivation_rule')
    if rule is None:
        raise _fail('mask_derivation_rule_required', f'{RULE_VERSION}만 허용되며 기본값이 없습니다')
    if rule != RULE_VERSION:
        raise _fail('mask_derivation_rule_unknown', f'{rule!r}: {RULE_VERSION}만 허용')
    for key in ('data_dir', 'cnt_from_dl1_root', 'source_version', *_ID_FIELDS):
        _text(config, key)
    limit = config.get('max_asset_memory_mb')
    if isinstance(limit, bool) or not isinstance(limit, (int, float)) or limit <= 0:
        raise _fail('invalid_template_config', 'max_asset_memory_mb는 양수여야 합니다(기본값 없음)')
    stack = config.get('layer_stack')
    if not isinstance(stack, list) or not stack:
        raise _fail('invalid_template_config', 'layer_stack은 비어 있지 않은 목록이어야 합니다')
    layers, orders = set(), set()
    for row in stack:
        if (not isinstance(row, dict) or set(row) != {'actual_layer', 'stack_order'}
                or not isinstance(row['actual_layer'], str) or not row['actual_layer']
                or type(row['stack_order']) not in (int, float) or row['stack_order'] in orders
                or row['actual_layer'] in layers):
            raise _fail('invalid_template_config', 'layer_stack 행은 중복 없는 {actual_layer, stack_order}만 받습니다')
        layers.add(row['actual_layer'])
        orders.add(row['stack_order'])
    roles, policy = config.get('electrode_roles'), config.get('electrode_role_policy')
    if (roles is None) == (policy is None):
        raise _fail('invalid_template_config', 'electrode_roles 또는 electrode_role_policy 중 정확히 하나가 필요합니다')
    if policy is not None and (policy != 'electrode_id' or not config.get('pair_table_path')):
        raise _fail('invalid_template_config', 'electrode_role_policy는 electrode_id이며 pair_table_path가 필요합니다')
    rois = config.get('rois')
    if not isinstance(rois, list) or not rois:
        raise _fail('invalid_template_config', 'rois는 비어 있지 않은 목록이어야 합니다')
    for roi in rois:
        center = roi.get('center_xy_um') if isinstance(roi, dict) else None
        if (not isinstance(roi, dict) or set(roi) != {'case_id', 'center_xy_um'} or not isinstance(roi['case_id'], str)
                or not isinstance(center, list) or len(center) != 2):
            raise _fail('invalid_template_config', 'rois 행은 {case_id, center_xy_um: [x, y]}만 받습니다')
    return config, _sha(raw)


def _resolve(base: Path, value) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path


def _scan_tree_for_hotspots(root: Path):
    """파일 이름만 훑는다(열지 않음). hotspot 파일명 패턴이나 result/ 폴더가 있으면 거부한다."""
    from simcompare.adapters.real_hotspot import HOTSPOT_RE
    found = []
    for directory, dirs, files in os.walk(root):
        for name in dirs:
            if name.lower() == 'result':
                found.append(os.path.relpath(os.path.join(directory, name), root) + '/')
        for name in files:
            if HOTSPOT_RE.fullmatch(name):
                found.append(os.path.relpath(os.path.join(directory, name), root))
    if found:
        found.sort()
        raise _fail('template_hotspot_input_present',
                    f'{len(found)}개 발견(열지 않음): ' + ', '.join(found[:5]) + (' ...' if len(found) > 5 else ''))


def _check_layer_stack(manifest, order):
    for case in manifest['cases']:
        for endpoint in case['endpoints']:
            if endpoint['actual_layer'] not in order:
                raise _fail('template_layer_missing_from_stack',
                            f"{case['case_name']}: {endpoint['actual_layer']!r}가 layer_stack에 없습니다")


def _derivation_entry(row, order):
    endpoints = [{'electrode_id': e['electrode_id'], 'role': e['role'], 'actual_layer': e['actual_layer']}
                 for e in row['endpoints']]
    endpoints.sort(key=lambda e: (e['electrode_id'], e['actual_layer']))   # 순서만 다른 case는 같은 항목이다
    entry = {'case': {'failure_type': row['failure_type'], 'endpoints': endpoints},
             'processed_endpoint': None, 'removed_layers': []}
    if row['failure_type'] != 'vertical_short':
        return entry
    for endpoint in endpoints:
        if endpoint['actual_layer'] not in order:
            raise _fail('template_layer_missing_from_stack', f"{row['case_id']}: {endpoint['actual_layer']!r}")
    upper = [e for e in endpoints if e['actual_layer'] == row['analysis_layer']]
    if len(upper) != 1:
        raise _fail('template_processed_endpoint_ambiguous', row['case_id'])
    low, high = sorted(order[e['actual_layer']] for e in endpoints)
    entry['processed_endpoint'] = upper[0]['electrode_id']
    entry['removed_layers'] = sorted(layer for layer, value in order.items() if low < value < high)
    return entry


def _mask_derivation(rois, order):
    entries = {}
    for row in rois:
        if row['status'] != 'ready':
            continue
        entry = _derivation_entry(row, order)
        key = json.dumps(entry['case'], sort_keys=True, ensure_ascii=False)
        # 같은 case의 ROI가 여럿이어도 항목은 case마다 하나다.
        entries[key] = entry
    return {'schema_version': DERIVATION_VERSION, 'rule_version': RULE_VERSION,
            'cases': [entries[k] for k in sorted(entries)]}


def _data_files(folder: Path):
    return {p.relative_to(folder).as_posix(): _sha(p.read_bytes()) for p in sorted(folder.rglob('*')) if p.is_file()}


def _build(config, config_sha, base: Path, config_path: Path, stage: Path, final: Path):
    from simcompare import pipeline
    from simcompare.adapters.electrode_png import import_electrode_assets
    from simcompare.adapters.real_hotspot import import_hotspots
    root = _resolve(base, config['data_dir'])
    cnt_root = _resolve(base, config['cnt_from_dl1_root'])
    if not root.is_dir():
        raise _fail('template_data_dir_required', str(root))
    _scan_tree_for_hotspots(root)
    work, data, roi_dir = stage / '_work', stage / 'data', stage / 'roi'
    work.mkdir()
    import_hotspots(root, data)
    manifest = json.loads((data / 'manifest.json').read_text(encoding='utf-8'))
    order = {row['actual_layer']: row['stack_order'] for row in config['layer_stack']}
    _check_layer_stack(manifest, order)
    assignment = {k: config[k] for k in _ID_FIELDS}
    assignment['layer_stack'] = [{**row, 'density_process_layer': _DENSITY_SENTINEL} for row in config['layer_stack']]
    pair_sha = None
    if config.get('electrode_roles') is not None:
        assignment['electrode_roles'] = config['electrode_roles']
    if config.get('pair_table_path'):
        pair_bytes = _resolve(base, config['pair_table_path']).read_bytes()
        (data / 'pairs.csv').write_bytes(pair_bytes)
        pair_sha = _sha(pair_bytes)
        assignment['pair_table_path'] = 'pairs.csv'
    if config.get('electrode_role_policy') is not None:
        assignment['electrode_role_policy'] = config['electrode_role_policy']
    assignment_path = work / 'assignment.json'
    assignment_path.write_text(json.dumps(assignment), encoding='utf-8')
    adapter = import_electrode_assets(
        'hotspot_case_electrode', root, work / 'adapter', numeric_data_dir=data, condition_assignment=str(assignment_path),
        cnt_from_dl1_root=str(cnt_root), source_version=config['source_version'],
        max_asset_memory_mb=config['max_asset_memory_mb'])
    # import 결과를 import된 폴더에 합친다(export가 asset을 data_dir 아래에서 읽는다).
    (data / 'assets').mkdir(exist_ok=True)
    for name in ('review_assets.json', 'electrode_asset_import_record.json'):
        shutil.copyfile(work / 'adapter' / name, data / name)
    for path in sorted((work / 'adapter' / 'assets').iterdir()):
        shutil.copyfile(path, data / 'assets' / path.name)
    frame_id = manifest['frame']['frame_id']
    requests = [{**{k: config[k] for k in ('product_id', 'design_version', 'source_id', 'process_step_id', 'exposure_id')},
                 'frame_id': frame_id, 'case_id': roi['case_id'], 'center_xy_um': roi['center_xy_um']}
                for roi in config['rois']]
    cfg = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'unused', '_config_dir': base,
           'map': {'diameter_um': 1, 'input': {
               'data_dir': str(data), 'condition_assignment': assignment,
               'asset_manifest': {'path': 'review_assets.json', 'sha256': _sha((data / 'review_assets.json').read_bytes())},
               'electrode_import_record': {'path': 'electrode_asset_import_record.json',
                                           'sha256': _sha((data / 'electrode_asset_import_record.json').read_bytes())}}},
           'review_rois': {'manual': requests}}
    exported = pipeline.run_numeric_roi_export(cfg, roi_dir)
    roi_path = roi_dir / 'review_rois.json'
    payload = json.loads(roi_path.read_text(encoding='utf-8'))
    # export는 asset을 data/ 아래 참조만 한다. 임시 폴더 경로를 게시 후 경로로 바꾼다.
    payload['assets_root'] = str((final / 'data').resolve())
    roi_path.write_bytes(_json_bytes(payload))
    (roi_dir / 'review_rois.csv').unlink(missing_ok=True)
    derivation = _mask_derivation(payload['rois'], order)
    (roi_dir / 'mask_derivation.json').write_bytes(_json_bytes(derivation))
    record = json.loads((data / 'electrode_asset_import_record.json').read_text(encoding='utf-8'))
    shutil.rmtree(work)
    n_ready = sum(r['status'] == 'ready' for r in payload['rois'])
    exit_code = 3 if exported['exit_code'] else 0
    execution = {
        'schema_version': EXECUTION_VERSION, 'command': 'export-template-rois', 'exit_code': exit_code,
        'status': payload['status'], 'mask_derivation_rule': RULE_VERSION,
        'inputs': {'config_sha256': config_sha, 'pair_table_sha256': pair_sha,
                   'layer_stack': config['layer_stack'], 'source_version': config['source_version'],
                   'input_tree_files_sha256': manifest['source_files'],
                   'dl1_files_sha256': {k: v for k, v in record['files'].items() if k.startswith('cnt_from_dl1_root:')}},
        'hotspot_inputs': {'scanned_names_only': True, 'found': 0, 'hotspot_or_ca_arrays_opened': False},
        'files_read': {'input_tree': sorted(manifest['source_files']),
                       'dl1': sorted(k for k in record['files'] if k.startswith('cnt_from_dl1_root:')),
                       'config': config_path.name, 'pair_table': 'data/pairs.csv' if pair_sha else None,
                       'generated_data_files': sorted(_data_files(data))},
        'outputs': {'data_files_sha256': _data_files(data),
                    'review_rois_sha256': _sha(roi_path.read_bytes()),
                    'mask_derivation_sha256': _sha((roi_dir / 'mask_derivation.json').read_bytes())},
        'counts': {'rois': len(payload['rois']), 'ready': n_ready, 'blocked': len(payload['rois']) - n_ready,
                   'derivation_cases': len(derivation['cases']), 'electrode_import_status': adapter['status']}}
    (stage / 'template_export_execution.json').write_bytes(_json_bytes(execution))
    return execution, exit_code


def run_template_export(config_path, out):
    """종료 코드 0 완료, 3 일부 ROI blocked, 입력·설정 오류는 NumericContractError(CLI가 종료 코드 2로 바꾼다)."""
    config_path = Path(config_path).resolve(strict=True)
    if os.path.lexists(Path(out).absolute()):      # 끊어진 심볼릭 링크도 존재로 본다(resolve 전에 검사)
        raise _fail('template_output_exists', str(Path(out).absolute()))
    final = Path(out).resolve()
    if os.path.lexists(final):
        raise _fail('template_output_exists', str(final))
    config, config_sha = _load_config(config_path)
    base = config_path.parent
    made = []
    for parent in reversed(final.parents):
        if not parent.exists():
            made.append(parent)
    final.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=final.name + '.tmp-', dir=final.parent))
    try:
        execution, exit_code = _build(config, config_sha, base, config_path, stage, final)
        os.replace(stage, final)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        for parent in reversed(made):   # 이 실행이 만든 빈 상위 폴더만 지운다
            try:
                parent.rmdir()
            except OSError:
                break
        raise
    return {'status': execution['status'], 'outputs': [str(final / 'roi' / 'review_rois.json'),
                                                       str(final / 'roi' / 'mask_derivation.json'),
                                                       str(final / 'template_export_execution.json')],
            'data_dir': str(final / 'data'), **execution['counts'], 'exit_code': exit_code}
