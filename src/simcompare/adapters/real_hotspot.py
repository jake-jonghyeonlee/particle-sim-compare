"""Observation-first importer for the confirmed real hotspot directory layout.

This format intentionally carries no invented product, process, unit, role,
height, or polarity metadata. It preserves raw observations for inspection and
leaves classified calculations gated until explicit assignments are supplied.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from pathlib import PurePosixPath
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np
from PIL import Image

from ..numeric_schema import DIAMETERS_UM, NumericContractError
from ..numeric_images import resolve_path
from ..png_strict import read_gray_hotspot

CONTRACT_VERSION = 'real_hotspot_v1'
DEFECTS = ('lateral_short', 'vertical_short', 'lateral_open', 'vertical_open')
# 실자료 폴더 이름의 붙여 쓴 표기(lateralshort)를 정식 이름으로 받는다. 파일·CSV 이름은 정식 이름만 쓴다.
DEFECT_ALIASES = {'lateralshort': 'lateral_short', 'verticalshort': 'vertical_short',
                  'lateralopen': 'lateral_open', 'verticalopen': 'vertical_open'}
IMAGES_DIRNAME = 'images'
CASE_RE = re.compile(r'^([^-]+)-([^-]+)-([^-]+)-([^-]+)-([^-]+)$')
HOTSPOT_RE = re.compile(r'^hotspot_([a-z_]+?)(\d+)\.png$', re.IGNORECASE)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case_name(name: str):
    match = CASE_RE.fullmatch(name)
    if not match:
        raise NumericContractError('invalid_real_case_name', name)
    defect, electrode_a, layer_a, electrode_b, layer_b = match.groups()
    if not all((electrode_a, layer_a, electrode_b, layer_b)):
        raise NumericContractError('invalid_real_case_name', name)
    defect = DEFECT_ALIASES.get(defect, defect)
    if defect not in DEFECTS:
        raise NumericContractError('unsupported_real_defect', defect)
    return defect, electrode_a, layer_a, electrode_b, layer_b


def _csv_areas(root: Path, case_dir: Path, defect: str):
    path = case_dir / 'result' / f'{defect}.csv'
    if not path.is_file():
        return {'status': 'missing', 'path': None, 'sha256': None, 'rows': {}, 'reason': 'case_local_csv_missing'}
    path = resolve_path(root, path.relative_to(root).as_posix())
    try:
        with path.open(encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != ['defect_size(um)', 'Area(um2)']:
                raise NumericContractError('invalid_real_csv_header', str(path))
            rows = {}
            for row in reader:
                d_raw, area_raw = row['defect_size(um)'], row['Area(um2)']
                d, area = float(d_raw), float(area_raw)
                if not math.isfinite(d) or d <= 0 or not math.isfinite(area) or area < 0:
                    raise NumericContractError('invalid_real_csv_value', str(path))
                key = int(d) if d.is_integer() else d
                if key in rows:
                    raise NumericContractError('duplicate_real_csv_diameter', f'{path}:{key}')
                try:
                    rounding_half_unit = float(Decimal(1).scaleb(Decimal(area_raw).as_tuple().exponent) / 2)
                except (InvalidOperation, ValueError, OverflowError) as exc:
                    raise NumericContractError('invalid_real_csv_precision', str(path)) from exc
                rows[key] = {'value': area, 'raw_diameter': d_raw, 'raw_area': area_raw,
                             'area_rounding_half_unit': rounding_half_unit}
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        if isinstance(exc, NumericContractError):
            raise
        raise NumericContractError('invalid_real_csv', str(path)) from exc
    return {'status': 'observed', 'path': path.name, 'sha256': _sha(path), 'rows': rows, 'reason': None}


def _csv_row_contract(row, diameter, mask_area, tolerance_value=None, difference_value=None):
    """Validate CSV values and all derived measurement fields from preserved text."""
    if row is None:
        return None, None, None
    try:
        raw_diameter = Decimal(str(row.get('raw_diameter')))
        raw_area = Decimal(str(row.get('raw_area')))
        half_unit = float(Decimal(1).scaleb(raw_area.as_tuple().exponent) / 2)
    except (InvalidOperation, ValueError, TypeError, OverflowError) as exc:
        raise NumericContractError('real_csv_raw_value_mismatch') from exc
    value = row.get('value')
    stored_half = row.get('area_rounding_half_unit')
    if (not raw_diameter.is_finite() or float(raw_diameter) != diameter
            or not raw_area.is_finite() or raw_area < 0
            or not isinstance(value, (int, float)) or not math.isfinite(value)
            or not math.isclose(float(raw_area), value, rel_tol=0, abs_tol=1e-12)
            or not isinstance(stored_half, (int, float)) or not math.isfinite(stored_half)
            or not math.isclose(stored_half, half_unit, rel_tol=0, abs_tol=1e-15)):
        raise NumericContractError('real_csv_raw_value_mismatch')
    tolerance = half_unit + 0.005
    difference = mask_area - value if mask_area is not None else None
    if tolerance_value is not None and not math.isclose(tolerance_value, tolerance, rel_tol=0, abs_tol=1e-12):
        raise NumericContractError('real_csv_measurement_invariant_mismatch')
    if difference_value is not None and not math.isclose(difference_value, difference, rel_tol=0, abs_tol=1e-12):
        raise NumericContractError('real_csv_measurement_invariant_mismatch')
    return tolerance, difference, ('consistent' if abs(difference) <= tolerance else 'mismatch') if difference is not None else None


def _read_gray(path: Path, relative_path: str):
    return read_gray_hotspot(path, relative_path, return_sha256=True)


def import_hotspots(root: Path, out: Path, *, expected_cases: Path | None = None,
                    max_input_pixels: int = 100_000_000):
    """Import discovered cases one mask at a time into a versioned manifest."""
    root, out = Path(root).resolve(strict=True), Path(out).resolve()
    if not root.is_dir():
        raise NumericContractError('real_hotspot_root_required', str(root))
    if out.exists():
        raise NumericContractError('real_hotspot_output_exists', str(out))
    if not isinstance(max_input_pixels, int) or max_input_pixels <= 0:
        raise NumericContractError('invalid_resource_limit')
    expected = None
    if expected_cases is not None:
        expected = [line.strip() for line in Path(expected_cases).read_text(encoding='utf-8-sig').splitlines()
                    if line.strip() and not line.lstrip().startswith('#')]
        if len(expected) != len(set(expected)):
            raise NumericContractError('duplicate_expected_case')
        for name in expected:
            _case_name(name)
    found, images_dirs = [], {}
    for directory, dirs, files in os.walk(root):
        if ('1.png' in files or '2.png' in files
                or any(HOTSPOT_RE.fullmatch(filename) for filename in files)):
            path = Path(directory)
            if path.name.lower() == IMAGES_DIRNAME and path != root:
                # <case>/images/ 배치: case 폴더는 그 상위이고 result/는 case 폴더 바로 아래에 있다.
                images_dirs[path.parent] = path
                path = path.parent
            found.append(path)
    for case_path in images_dirs:
        if any(child.is_file() and (child.name in ('1.png', '2.png') or HOTSPOT_RE.fullmatch(child.name))
               for child in case_path.iterdir()):
            raise NumericContractError('ambiguous_real_images_location', str(case_path))
    found = list(dict.fromkeys(found))
    if not found:
        raise NumericContractError('no_real_hotspot_cases_found')
    found.sort(key=lambda p: p.relative_to(root).as_posix())
    names = [_case_name(p.name) and p.name for p in found]
    if len(names) != len(set(names)):
        raise NumericContractError('duplicate_real_case_name')
    for case_dir in found:
        if any(parent != root and parent in found for parent in case_dir.parents):
            raise NumericContractError('nested_real_case_directory', str(case_dir))
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=out.name+'.tmp-', dir=out.parent))
    try:
        assets = staging / 'assets'
        assets.mkdir()
        cases, diagnostics, file_index = [], [], {}
        common_crop_shape = None
        expected_set = set(expected or [])
        for index, case_dir in enumerate(found):
            defect, ea, la, eb, lb = _case_name(case_dir.name)
            rel_case = case_dir.relative_to(root).as_posix()
            image_dir = images_dirs.get(case_dir, case_dir)
            entry = {'case_name': case_dir.name, 'source_relative_path': rel_case,
                     'failure_type': defect, 'endpoints': [
                         {'electrode_id': ea, 'actual_layer': la, 'role': None},
                         {'electrode_id': eb, 'actual_layer': lb, 'role': None}],
                     'product_id': None, 'design_version': None, 'source_id': None,
                     'process_step_id': None, 'exposure_id': None, 'unit_id': None,
                     'height_um': None, 'height_evidence': None,
                     'source_contract_version': CONTRACT_VERSION, 'frame_id': 'real_hotspot_crop_v1',
                     'csv': _csv_areas(root, case_dir, defect), 'records': [],
                     'geometry_status': 'unconfirmed', 'electrode_shapes_yx': None,
                     'crop_shape_yx': None, 'geometry_reasons': []}
            if image_dir != case_dir:
                entry['image_subdir'] = IMAGES_DIRNAME
            if entry['csv']['status'] == 'observed':
                csv_source = case_dir / 'result' / f'{defect}.csv'
                csv_key = csv_source.relative_to(root).as_posix()
                file_index[csv_key] = {'sha256': entry['csv']['sha256'], 'path': csv_key}
            if ea == eb:
                entry['diagnostics'] = ['same_electrode_name_both_endpoints_short_unresolved']
            else:
                entry['diagnostics'] = []
            csv_rows = entry['csv']['rows']
            for extra_diameter in sorted(set(csv_rows) - set(DIAMETERS_UM)):
                diagnostics.append({'case': case_dir.name, 'diameter_um': extra_diameter,
                                    'status': 'unexpected_csv_diameter'})
            image_names = {}
            for filename in os.listdir(image_dir):
                m = HOTSPOT_RE.fullmatch(filename)
                if not m:
                    continue
                prefix, suffix = m.groups()
                if prefix.lower() != defect.lower():
                    diagnostics.append({'case': case_dir.name, 'file': filename, 'status': 'invalid_defect_prefix'})
                    continue
                diameter = int(suffix)
                if diameter in image_names:
                    raise NumericContractError('duplicate_real_hotspot_diameter', f'{case_dir.name}:{diameter}')
                image_names[diameter] = image_dir / filename
            for electrode_file in ('1.png', '2.png'):
                p = image_dir / electrode_file
                if p.exists():
                    source_key = p.relative_to(root).as_posix()
                    p = resolve_path(root, source_key)
                    file_index[source_key] = {'sha256': _sha(p), 'path': source_key}
            if (image_dir / '1.png').exists() and (image_dir / '2.png').exists():
                with Image.open(image_dir / '1.png') as i1, Image.open(image_dir / '2.png') as i2:
                    shape1, shape2 = (i1.height, i1.width), (i2.height, i2.width)
                if shape1 != shape2 or min(shape1) <= 1000:
                    raise NumericContractError('electrode_shape_mismatch_or_too_small', case_dir.name)
                crop_shape = (shape1[0]-1000, shape1[1]-1000)
                if common_crop_shape is not None and crop_shape != common_crop_shape:
                    raise NumericContractError('real_cases_do_not_share_crop_frame', case_dir.name)
                common_crop_shape = crop_shape
                case_crop_shape = crop_shape
                entry.update(geometry_status='confirmed', electrode_shapes_yx=[list(shape1), list(shape2)],
                             crop_shape_yx=list(case_crop_shape))
            else:
                case_crop_shape = None
                entry['diagnostics'].append('electrode_image_missing')
                entry['geometry_reasons'].append('electrode_image_missing')
            for diameter in DIAMETERS_UM:
                image_path = image_names.get(diameter)
                csv_row = csv_rows.get(diameter)
                record = {'diameter_um': diameter, 'input_status': 'missing', 'mask_path': None,
                          'mask_sha256': None, 'png_area_um2': None, 'csv_area': csv_row,
                          'area_tolerance_um2': None, 'area_difference_um2': None,
                          'reason': 'hotspot_image_missing' if image_path is None else None}
                if image_path is not None:
                    raw_rel = image_path.relative_to(root).as_posix()
                    image_path = resolve_path(root, raw_rel)
                    try:
                        with Image.open(image_path) as header_image:
                            header_pixels = header_image.width * header_image.height
                    except (OSError, ValueError) as exc:
                        raise NumericContractError('png_decode_failed', raw_rel) from exc
                    if header_pixels > max_input_pixels:
                        raise NumericContractError('real_hotspot_pixel_limit', raw_rel)
                    mask, source_sha256 = _read_gray(image_path, raw_rel)
                    if case_crop_shape is not None and mask.shape != case_crop_shape:
                        raise NumericContractError('hotspot_crop_shape_mismatch', f'{raw_rel}:{mask.shape}!={case_crop_shape}')
                    mask_path = Path('assets') / f'{index:05d}_{diameter:02d}.npy'
                    np.save(staging / mask_path, mask)
                    count = int(np.count_nonzero(mask))
                    area = count * 0.01
                    record.update(input_status='observed', mask_path=mask_path.as_posix(),
                                  mask_sha256=_sha(staging / mask_path), source_path=raw_rel,
                                  source_sha256=source_sha256, occupied_pixels=count, png_area_um2=area,
                                  mask_shape_yx=list(mask.shape),
                                  reason=None)
                    file_index[raw_rel] = {'sha256': record['source_sha256'], 'path': raw_rel}
                    if csv_row is None:
                        record['csv_status'] = 'missing_diameter'
                    else:
                        tolerance = csv_row['area_rounding_half_unit'] + 0.005
                        difference = area - csv_row['value']
                        record.update(area_tolerance_um2=tolerance, area_difference_um2=difference,
                                      csv_status='consistent' if abs(difference) <= tolerance else 'mismatch')
                elif csv_row is not None:
                    record['csv_status'] = 'csv_only'
                else:
                    record['csv_status'] = 'missing_both'
                entry['records'].append(record)
            for diameter in sorted(set(image_names)-set(DIAMETERS_UM)):
                diagnostics.append({'case': case_dir.name, 'diameter_um': diameter, 'status': 'unexpected_diameter'})
            entry['classification_status'] = 'unassigned_pair_table'
            entry['map_status'] = 'unconfigured'
            cases.append(entry)
        discovered_names = {case['case_name'] for case in cases}
        for missing in sorted(expected_set-discovered_names):
            diagnostics.append({'case': missing, 'status': 'expected_case_folder_missing'})
        if expected is None:
            diagnostics.append({'status': 'expected_case_inventory_unprovided', 'scope': 'absent_case_coverage_unconfirmed'})
        csv_missing_cases = [c['case_name'] for c in cases if c['csv']['status'] == 'missing']
        diagnostics.extend({'case': name, 'status': 'case_local_csv_missing'} for name in csv_missing_cases)
        frames = {'frame_id': 'real_hotspot_crop_v1', 'length_unit': 'um', 'pixel_pitch_um': 0.1,
                  'origin_xy_um': [50.0, 50.0], 'axis_order': 'yx', 'shape_yx': common_crop_shape,
                  'crop_from_electrode_px_yx': [500, 500], 'crop_policy': 'centered_exact_1000px_border',
                  'frame_id_scope': 'shared condition frame; condition assignments required'}
        manifest = {'schema_version': CONTRACT_VERSION, 'source_contract_version': CONTRACT_VERSION,
                    'is_synthetic': False, 'source_root_name': root.name,
                    'source_policy': {'gray_nonzero_is_occupied': True, 'csv_area_um2_preserved': True,
                                      'area_formula': 'occupied_pixels * 0.01 um2',
                                      'classification_requires_explicit_pair_table': True,
                                      'electrode_polarity': None, 'max_input_pixels': max_input_pixels,
                                      'materialization': 'one source PNG and one crop mask at a time'},
                    'physical_metadata': {'product_id': None, 'design_version': None, 'source_id': None,
                                          'process_step_id': None, 'exposure_id': None, 'unit_id': None,
                                          'layer_process_mapping': None, 'height_um': None, 'electrode_roles': None},
                    'expected_cases': expected, 'expected_case_coverage_status': 'confirmed' if expected is not None else 'unconfirmed',
                    'frame': frames, 'cases': cases, 'diagnostics': diagnostics, 'source_files': file_index}
        # Canonicalize through the JSON representation first so integer CSV row
        # keys and tuple shapes hash exactly as their persisted string/list forms.
        manifest = json.loads(json.dumps(manifest, ensure_ascii=False, allow_nan=False))
        canonical = json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        manifest['source_payload_hash'] = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        manifest['manifest_hash_recipe'] = 'sha256(canonical JSON before source_payload_hash; sort_keys, compact separators, UTF-8)'
        (staging / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(staging, out)
        return {'status': 'partial', 'manifest': str(out/'manifest.json'), 'cases': len(cases),
                'diagnostics': len(diagnostics), 'is_synthetic': False, 'source_payload_hash': manifest['source_payload_hash']}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def load_real_manifest(folder: Path, manifest_path='manifest.json'):
    """Read and authenticate a real_hotspot_v1 import without relaxing numeric_v2."""
    folder = Path(folder)
    path = resolve_path(folder, manifest_path)
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('schema_version') != CONTRACT_VERSION or manifest.get('source_contract_version') != CONTRACT_VERSION:
        raise NumericContractError('real_manifest_version_mismatch')
    if manifest.get('is_synthetic') is not False:
        raise NumericContractError('invalid_synthetic_marker')
    expected_hash = manifest.get('source_payload_hash')
    payload = {k: v for k, v in manifest.items() if k not in ('source_payload_hash', 'manifest_hash_recipe')}
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    if hashlib.sha256(canonical.encode('utf-8')).hexdigest() != expected_hash:
        raise NumericContractError('real_manifest_hash_mismatch')
    # These values describe the importer itself, not user supplied metadata.
    # A correctly resealed manifest must not redefine the source semantics.
    frame = manifest.get('frame', {})
    if (frame.get('frame_id') != 'real_hotspot_crop_v1' or frame.get('length_unit') != 'um'
            or frame.get('pixel_pitch_um') != 0.1 or frame.get('origin_xy_um') != [50.0, 50.0]
            or frame.get('axis_order') != 'yx' or frame.get('crop_from_electrode_px_yx') != [500, 500]
            or frame.get('crop_policy') != 'centered_exact_1000px_border'):
        raise NumericContractError('real_frame_contract_mismatch')
    policy = manifest.get('source_policy', {})
    if (policy.get('gray_nonzero_is_occupied') is not True
            or policy.get('csv_area_um2_preserved') is not True
            or policy.get('area_formula') != 'occupied_pixels * 0.01 um2'):
        raise NumericContractError('real_source_policy_mismatch')
    physical = manifest.get('physical_metadata', {})
    if any(value is not None for value in physical.values()):
        raise NumericContractError('real_physical_metadata_must_remain_unassigned')
    frame_shape = frame.get('shape_yx')
    geometry_confirmed = any(case.get('geometry_status') == 'confirmed' for case in manifest.get('cases', []))
    if frame_shape is None and not geometry_confirmed:
        pass
    elif (not isinstance(frame_shape, list) or len(frame_shape) != 2
            or any(type(v) is not int or v <= 0 for v in frame_shape)):
        raise NumericContractError('real_common_frame_shape_required')
    names, identities, assets = set(), set(), set()
    source_files = manifest.get('source_files')
    if not isinstance(source_files, dict):
        raise NumericContractError('real_source_file_index_required')
    for source_key, indexed in source_files.items():
        if (not isinstance(indexed, dict) or indexed.get('path') != source_key
                or not re.fullmatch(r'[0-9a-f]{64}', str(indexed.get('sha256', '')))):
            raise NumericContractError('invalid_real_source_file_index', source_key)
        source_path = PurePosixPath(source_key)
        if source_path.is_absolute() or '..' in source_path.parts:
            raise NumericContractError('invalid_real_source_file_index', source_key)
    used_source_files = set()
    for case in manifest.get('cases', []):
        name = case.get('case_name')
        defect, endpoint_a, layer_a, endpoint_b, layer_b = _case_name(name)
        if name in names:
            raise NumericContractError('duplicate_real_case_name', name)
        names.add(name)
        if case.get('source_contract_version') != CONTRACT_VERSION or case.get('frame_id') != frame['frame_id']:
            raise NumericContractError('real_case_manifest_version_mismatch', name)
        if (case.get('classification_status') != 'unassigned_pair_table'
                or case.get('map_status') != 'unconfigured'
                or any(case.get(key) is not None for key in ('product_id','design_version','source_id',
                       'process_step_id','exposure_id','unit_id','height_um','height_evidence'))):
            raise NumericContractError('real_case_unassigned_metadata_invariant_mismatch', name)
        endpoints = case.get('endpoints')
        if (not isinstance(endpoints, list) or len(endpoints) != 2
                or any(not isinstance(ep, dict) or ep.get('role') is not None for ep in endpoints)
                or [(ep.get('electrode_id'), ep.get('actual_layer')) for ep in endpoints] !=
                   [(endpoint_a, layer_a), (endpoint_b, layer_b)]
                or case.get('failure_type') != defect):
            raise NumericContractError('invalid_real_case_endpoints', name)
        source_relative_path = case.get('source_relative_path')
        source_path = PurePosixPath(source_relative_path) if isinstance(source_relative_path, str) else None
        if (source_path is None or source_path.is_absolute() or '..' in source_path.parts
                or source_path.name != name):
            raise NumericContractError('invalid_real_case_source_binding', name)
        image_subdir = case.get('image_subdir')
        if image_subdir not in (None, IMAGES_DIRNAME):
            raise NumericContractError('invalid_real_case_source_binding', name)
        image_base = source_relative_path if image_subdir is None else f'{source_relative_path}/{image_subdir}'
        case_identity = (case.get('source_relative_path'), case.get('case_name'))
        if case_identity in identities:
            raise NumericContractError('duplicate_real_case_identity', name)
        identities.add(case_identity)
        geometry_ok = case.get('geometry_status') == 'confirmed'
        case_shape = case.get('crop_shape_yx')
        if geometry_ok and case_shape != frame_shape:
            raise NumericContractError('real_case_frame_mismatch', name)
        electrode_shapes = case.get('electrode_shapes_yx')
        if geometry_ok and (not isinstance(electrode_shapes, list) or len(electrode_shapes) != 2
                or electrode_shapes[0] != electrode_shapes[1]
                or any(shape != [frame_shape[0]+1000, frame_shape[1]+1000] for shape in electrode_shapes)):
            raise NumericContractError('real_electrode_crop_transform_mismatch', name)
        if not geometry_ok and case_shape is not None:
            raise NumericContractError('invalid_unconfirmed_case_geometry', name)
        if not geometry_ok and (case.get('geometry_status') != 'unconfirmed' or electrode_shapes is not None
                                or not case.get('geometry_reasons')):
            raise NumericContractError('invalid_unconfirmed_case_geometry', name)
        if case.get('source_contract_version') != manifest['schema_version']:
            raise NumericContractError('real_case_manifest_version_mismatch')
        records = case.get('records', [])
        if len(records) != len(DIAMETERS_UM) or {r.get('diameter_um') for r in records} != set(DIAMETERS_UM):
            raise NumericContractError('real_case_diameter_coverage_invalid', case.get('case_name', ''))
        case_csv_rows = case.get('csv', {}).get('rows', {})
        for record in records:
            if record.get('input_status') not in ('observed', 'missing'):
                raise NumericContractError('invalid_real_input_status')
            csv_row = record.get('csv_area')
            indexed_csv_row = case_csv_rows.get(str(record.get('diameter_um')))
            if csv_row != indexed_csv_row:
                raise NumericContractError('real_csv_representation_mismatch', name)
            csv_tolerance, csv_difference, derived_status = _csv_row_contract(
                csv_row, record['diameter_um'], record.get('png_area_um2'))
            if record.get('input_status') == 'observed':
                source_path = record.get('source_path')
                source_rel = PurePosixPath(source_path) if isinstance(source_path, str) else None
                if (source_rel is None or source_rel.is_absolute() or '..' in source_rel.parts
                        or source_rel.parent.as_posix() != image_base
                        or source_path not in source_files or
                        source_files[source_path].get('sha256') != record.get('source_sha256')):
                    raise NumericContractError('real_record_source_coverage_mismatch', name)
                used_source_files.add(source_path)
                asset = resolve_path(folder, record['mask_path'])
                if record['mask_path'] in assets:
                    raise NumericContractError('duplicate_real_mask_asset', record['mask_path'])
                assets.add(record['mask_path'])
                if _sha(asset) != record.get('mask_sha256'):
                    raise NumericContractError('real_mask_hash_mismatch', record['mask_path'])
                mask = np.load(asset, allow_pickle=False, mmap_mode='r')
                if mask.dtype != np.dtype(bool) or mask.ndim != 2 or (geometry_ok and list(mask.shape) != frame_shape):
                    raise NumericContractError('real_mask_binary_contract_mismatch', record['mask_path'])
                if record.get('mask_shape_yx') != list(mask.shape):
                    raise NumericContractError('real_mask_shape_metadata_mismatch', record['mask_path'])
                area = int(np.count_nonzero(mask)) * 0.01
                if not isinstance(record.get('png_area_um2'), (int, float)) or not math.isfinite(record['png_area_um2']) or not math.isclose(area, record['png_area_um2'], rel_tol=0, abs_tol=1e-12):
                    raise NumericContractError('real_mask_area_mismatch', record['mask_path'])
                expected_csv_status = 'missing_diameter' if csv_row is None else derived_status
                if csv_row is not None:
                    _csv_row_contract(csv_row, record['diameter_um'], area,
                                      record.get('area_tolerance_um2'), record.get('area_difference_um2'))
                if record.get('csv_status') != expected_csv_status:
                    raise NumericContractError('real_csv_status_invariant_mismatch', record['mask_path'])
            elif record.get('mask_path') is not None or record.get('mask_sha256') is not None:
                raise NumericContractError('invalid_missing_real_record', name)
            else:
                expected_status = 'csv_only' if csv_row is not None else 'missing_both'
                if (record.get('csv_status') != expected_status or record.get('png_area_um2') is not None
                        or record.get('area_difference_um2') is not None or record.get('area_tolerance_um2') is not None):
                    raise NumericContractError('real_missing_record_invariant_mismatch', name)
        csv_info = case.get('csv', {})
        if csv_info.get('status') == 'observed':
            csv_path = f"{case.get('source_relative_path')}/result/{case.get('failure_type')}.csv"
            if csv_path not in source_files or source_files[csv_path].get('sha256') != csv_info.get('sha256'):
                raise NumericContractError('real_csv_source_coverage_mismatch', name)
            used_source_files.add(csv_path)
        for image_name in ('1.png', '2.png'):
            key = f"{image_base}/{image_name}"
            if key in source_files:
                used_source_files.add(key)
            elif geometry_ok:
                raise NumericContractError('real_electrode_source_coverage_mismatch', name)
    if used_source_files != set(source_files):
        raise NumericContractError('unbound_real_source_file_index_entry')
    if manifest.get('expected_cases') is not None:
        expected = manifest['expected_cases']
        if not isinstance(expected, list) or len(expected) != len(set(expected)) or not set(names) <= set(expected):
            raise NumericContractError('invalid_real_expected_case_inventory')
        for expected_name in expected:
            _case_name(expected_name)
    return manifest


def configured_dataset(folder: Path, manifest: dict, assignment: dict):
    """Bind explicit analysis assignments and pair table to real observations."""
    import copy
    from ..numeric_schema import (CaseIdentity, DensityRow, Endpoint, Frame, MaskRecord,
        NumericCase, NumericDataset, Pair, SourceCA)

    required = ('product_id', 'design_version', 'source_id', 'process_step_id', 'exposure_id', 'unit_id',
                'layer_stack')
    if not isinstance(assignment, dict) or any(not assignment.get(k) for k in required):
        raise NumericContractError('real_grouping_assignment_required')
    allowed = set(required) | {'pair_table_path', 'electrode_roles', 'max_group_mask_cells',
                               'max_materialized_source_cells', 'max_operation_bytes'}
    if set(assignment) - allowed:
        raise NumericContractError('unknown_real_grouping_assignment_field')
    role_map = assignment.get('electrode_roles', {})
    if (not isinstance(role_map, dict) or
            any(not isinstance(k, str) or not isinstance(v, str) or not v.strip() for k, v in role_map.items())):
        raise NumericContractError('invalid_real_electrode_roles')
    stack_rows = assignment['layer_stack']
    if not isinstance(stack_rows, list) or not stack_rows:
        raise NumericContractError('real_layer_stack_required')
    stack, orders = {}, set()
    for row in stack_rows:
        layer, order = row.get('actual_layer'), row.get('stack_order')
        process = row.get('density_process_layer')
        if not isinstance(layer, str) or not layer or type(order) not in (int, float) or order in orders:
            raise NumericContractError('invalid_real_layer_assignment')
        stack[layer] = (order, process)
        orders.add(order)
    table_path = resolve_path(folder, assignment['pair_table_path']) if assignment.get('pair_table_path') else None
    pairs, ids = {}, set()
    if table_path is not None:
        with table_path.open(encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            required_columns = {'pair_id', 'electrode_a_id', 'electrode_b_id', 'class', 'pair_table_version'}
            if not required_columns <= set(reader.fieldnames or ()):
                raise NumericContractError('invalid_pair_table_header')
            for row in reader:
                names = (row['electrode_a_id'], row['electrode_b_id'])
                if not all(names) or names[0] == names[1]:
                    raise NumericContractError('invalid_pair_table_row')
                pair = Pair(row['pair_id'], frozenset(names), row['class'], row['pair_table_version'])
                if pair.electrode_ids in pairs or pair.pair_id in ids:
                    raise NumericContractError('duplicate_or_conflicting_pair')
                pairs[pair.electrode_ids] = pair
                ids.add(pair.pair_id)
    frame_spec = manifest['frame']
    if frame_spec.get('shape_yx') is None:
        raise NumericContractError('real_common_frame_shape_required')
    shape = tuple(frame_spec['shape_yx'])
    if len(shape) != 2 or any(type(v) is not int or v <= 0 for v in shape):
        raise NumericContractError('real_common_frame_shape_required')
    frame_id = frame_spec['frame_id']
    cases, unresolved = [], []
    max_group_cells = assignment.get('max_group_mask_cells', 500_000_000)
    max_source_cells = assignment.get('max_materialized_source_cells', 6_000_000_000)
    max_operation_bytes = assignment.get('max_operation_bytes', 8 * 1024**3)
    if type(max_group_cells) is not int or max_group_cells <= 0 or type(max_source_cells) is not int or max_source_cells <= 0:
        raise NumericContractError('invalid_resource_limit')
    if type(max_operation_bytes) is not int or max_operation_bytes <= 0 or max_operation_bytes > 8 * 1024**3:
        raise NumericContractError('invalid_resource_limit', 'max_operation_bytes must be in (0, 8 GiB]')
    group_counts, observed_count = {}, 0
    for raw_case in manifest['cases']:
        a, b = raw_case['endpoints']
        if any(ep['actual_layer'] not in stack for ep in (a, b)):
            raise NumericContractError('real_actual_layer_unassigned', raw_case['case_name'])
        layer = max((a, b), key=lambda ep: stack[ep['actual_layer']][0])['actual_layer'] if raw_case['failure_type'] == 'vertical_short' else a['actual_layer']
        group_counts[layer] = group_counts.get(layer, 0) + 1
        observed_count += sum(r['input_status'] == 'observed' for r in raw_case['records'])
    # Missing inventory cases still create full per-diameter result groups. Add
    # them before estimating output and transient arrays so absence cannot evade
    # the operation budget.
    discovered_names = {case['case_name'] for case in manifest['cases']}
    expected_names = set(manifest.get('expected_cases') or ())
    for missing_name in sorted(expected_names - discovered_names):
        defect, _, layer_a, _, layer_b = _case_name(missing_name)
        if layer_a not in stack or layer_b not in stack:
            raise NumericContractError('real_actual_layer_unassigned', missing_name)
        if defect == 'vertical_short':
            if layer_a == layer_b:
                raise NumericContractError('real_vertical_same_layer', missing_name)
            layer = layer_a if stack[layer_a][0] >= stack[layer_b][0] else layer_b
        else:
            if layer_a != layer_b:
                raise NumericContractError('real_lateral_layer_mismatch', missing_name)
            layer = layer_a
        if not isinstance(stack[layer][1], str) or not stack[layer][1]:
            raise NumericContractError('real_density_layer_assignment_required', layer)
        group_counts[layer] = group_counts.get(layer, 0) + 1
    if observed_count * shape[0] * shape[1] > max_source_cells:
        raise NumericContractError('oversized_real_materialization',
                                   f'{observed_count*shape[0]*shape[1]} source cells exceed limit {max_source_cells}')
    oversized = {layer: count*shape[0]*shape[1] for layer, count in group_counts.items()
                 if count*shape[0]*shape[1] > max_group_cells}
    if oversized:
        layer, cells = next(iter(sorted(oversized.items())))
        raise NumericContractError('oversized_real_mask_group', f'{layer} requires {cells} cells; limit {max_group_cells}')
    # Eager peak estimate: every loaded source mask, retained per-diameter output
    # and a conservative per-case transient margin. This does not claim streaming.
    source_bytes = observed_count * shape[0] * shape[1]
    frame_cells = shape[0] * shape[1]
    output_bytes = len(group_counts) * len(DIAMETERS_UM) * 14 * frame_cells
    transient_bytes = max(group_counts.values(), default=0) * 20 * frame_cells
    estimated_peak_bytes = source_bytes + output_bytes + transient_bytes
    if estimated_peak_bytes > max_operation_bytes:
        raise NumericContractError('oversized_real_operation',
            f'estimated {estimated_peak_bytes} bytes exceed {max_operation_bytes}; reduce input scope')
    frame = Frame(frame_id, frame_spec['pixel_pitch_um'], tuple(frame_spec['origin_xy_um']),
                  shape, np.ones(shape, dtype=bool))
    for raw_case in manifest['cases']:
        if any(ep['actual_layer'] not in stack for ep in raw_case['endpoints']):
            raise NumericContractError('real_actual_layer_unassigned', raw_case['case_name'])
        a, b = raw_case['endpoints']
        same_name = a['electrode_id'] == b['electrode_id']
        if same_name:
            unresolved.append(raw_case['case_name'])
        endpoints = tuple(Endpoint(ep['electrode_id'], role_map.get(ep['electrode_id']), ep['actual_layer'])
                          for ep in (a, b))
        if raw_case['failure_type'] == 'vertical_short':
            if endpoints[0].actual_layer == endpoints[1].actual_layer:
                raise NumericContractError('real_vertical_same_layer', raw_case['case_name'])
            analysis_layer = max(endpoints, key=lambda ep: stack[ep.actual_layer][0]).actual_layer
        else:
            if endpoints[0].actual_layer != endpoints[1].actual_layer:
                raise NumericContractError('real_lateral_layer_mismatch', raw_case['case_name'])
            analysis_layer = endpoints[0].actual_layer
        density_layer = stack[analysis_layer][1]
        if not isinstance(density_layer, str) or not density_layer:
            raise NumericContractError('real_density_layer_assignment_required', analysis_layer)
        identity = CaseIdentity(assignment['product_id'], assignment['design_version'], assignment['source_id'],
                                assignment['process_step_id'], assignment['exposure_id'], raw_case['case_name'],
                                frame_id, manifest['schema_version'])
        pair = None if same_name else pairs.get(frozenset((a['electrode_id'], b['electrode_id'])))
        records = {}
        for source_record in raw_case['records']:
            mask = None
            source_ca = None
            geometry_confirmed = raw_case.get('geometry_status') == 'confirmed'
            if source_record['input_status'] == 'observed' and geometry_confirmed:
                asset = resolve_path(folder, source_record['mask_path'])
                mask = np.load(asset, allow_pickle=False)
                if mask.dtype != np.dtype(bool) or mask.shape != shape:
                    raise NumericContractError('real_mask_shape_mismatch', raw_case['case_name'])
                csv_area = source_record.get('csv_area')
                if csv_area is not None and source_record.get('csv_status') == 'consistent':
                    source_ca = SourceCA(csv_area['value'], frame_id, frame.valid_hash)
            reasons = () if source_record['input_status'] == 'observed' else ('missing_input',)
            if source_record.get('csv_status') == 'mismatch':
                reasons += ('csv_area_mismatch',)
            if not geometry_confirmed:
                reasons += ('case_geometry_unconfirmed',)
                mask, source_ca = None, None
            tolerance = source_record.get('area_tolerance_um2') or 1e-9
            records[float(source_record['diameter_um'])] = MaskRecord(
                float(source_record['diameter_um']), frame, mask,
                'missing' if 'case_geometry_unconfirmed' in reasons else source_record['input_status'], reasons,
                source_ca, identity, analysis_layer, conservation_atol_um2=tolerance)
        case_reasons = []
        if same_name:
            case_reasons.append('same_electrode_name_both_endpoints_short_unresolved')
        if pair is None and not same_name:
            case_reasons.append('unmapped_pair')
        case_reasons.extend(raw_case.get('geometry_reasons', []))
        if manifest.get('expected_case_coverage_status') != 'confirmed':
            case_reasons.append('expected_case_inventory_unprovided')
        cases.append(NumericCase(identity, endpoints, raw_case['failure_type'],
                                 pair.pair_id if pair else None, pair.classification if pair else None,
                                 analysis_layer, density_layer, records,
                                 {'case_id': raw_case['case_name'], 'ordered_endpoints': raw_case['endpoints'],
                                  'records': copy.deepcopy(raw_case['records']),
                                  'csv': copy.deepcopy(raw_case['csv']),
                                  'geometry_status': raw_case.get('geometry_status'),
                                  'source_relative_path': raw_case.get('source_relative_path')},
                                 {'unit_id': assignment['unit_id'], 'height_um': None, 'height_evidence': None,
                                  'source_case_metadata': raw_case, 'max_group_mask_cells': max_group_cells},
                                 tuple(sorted(set(case_reasons)))))
    for missing_name in sorted(expected_names - discovered_names):
        defect, ea, la, eb, lb = _case_name(missing_name)
        if la not in stack or lb not in stack:
            raise NumericContractError('real_actual_layer_unassigned', missing_name)
        endpoints = (Endpoint(ea, role_map.get(ea), la), Endpoint(eb, role_map.get(eb), lb))
        if defect == 'vertical_short':
            if la == lb:
                raise NumericContractError('real_vertical_same_layer', missing_name)
            analysis_layer = max(endpoints, key=lambda ep: stack[ep.actual_layer][0]).actual_layer
        else:
            if la != lb:
                raise NumericContractError('real_lateral_layer_mismatch', missing_name)
            analysis_layer = la
        density_layer = stack[analysis_layer][1]
        if not isinstance(density_layer, str) or not density_layer:
            raise NumericContractError('real_density_layer_assignment_required', analysis_layer)
        identity = CaseIdentity(assignment['product_id'], assignment['design_version'], assignment['source_id'],
                                assignment['process_step_id'], assignment['exposure_id'], missing_name,
                                frame_id, manifest['schema_version'])
        pair = pairs.get(frozenset((ea, eb)))
        missing_records = {float(d): MaskRecord(float(d), frame, None, 'missing',
                           ('expected_case_folder_missing',), None, identity, analysis_layer)
                           for d in DIAMETERS_UM}
        cases.append(NumericCase(identity, endpoints, defect, pair.pair_id if pair else None,
                                 pair.classification if pair else None, analysis_layer, density_layer,
                                 missing_records, {'case_id': missing_name, 'ordered_endpoints': None},
                                 {'unit_id': assignment['unit_id'], 'height_um': None,
                                  'source_case_metadata': {'case_name': missing_name,
                                                           'geometry_status': 'missing_expected_case'}},
                                 ('expected_case_folder_missing',) if pair else
                                 ('expected_case_folder_missing', 'unmapped_pair')))
    expected_raw = [{'product_id': c.identity.product_id, 'design_version': c.identity.design_version,
                     'source_id': c.identity.source_id, 'process_step_id': c.identity.process_step_id,
                     'exposure_id': c.identity.exposure_id, 'case_id': c.identity.case_id,
                     'frame_id': c.identity.frame_id, 'source_contract_version': c.identity.source_contract_version}
                    for c in cases]
    assignment_json = json.dumps(assignment, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    assignment_hash = hashlib.sha256(assignment_json.encode('utf-8')).hexdigest()
    pair_hash = _sha(table_path) if table_path else None
    configured_hash = hashlib.sha256((manifest['source_payload_hash']+assignment_hash+(pair_hash or '')).encode('ascii')).hexdigest()
    provenance = {**copy.deepcopy(manifest), 'is_synthetic': False, 'cases': expected_raw,
                  'pair_table_version': next(iter(pairs.values())).table_version if pairs else None,
                  'pair_table_hash': pair_hash, 'configured_assignments': copy.deepcopy(assignment),
                  'configured_assignment_hash': assignment_hash, 'input_hash': configured_hash,
                  'unresolved_same_name_cases': unresolved,
                  'resource_estimate': {'estimated_peak_bytes': estimated_peak_bytes,
                    'max_operation_bytes': max_operation_bytes,
                    'method': 'retained_source + 14 masks per layer/diameter + 20 masks per case transient; eager, conservative'}}
    return NumericDataset(cases, pairs, {frame_id: frame}, [],
                          {'frame_id': frame_id, 'unit_width_um': None, 'unit_height_um': None,
                           'measured_width_um': None, 'measured_height_um': None}, {},
                          {'G1': {'status': 'pending', 'answer': None}, 'G2': {'status': 'pending', 'answer': None},
                           'G7a': {'status': 'answered', 'answer': 'failure_pieces'}, 'G7b': {'status': 'answered', 'answer': 'short_only'}},
                          provenance, ('density_contract_unconfirmed', 'unit_area_basis_unconfirmed',
                                       'unresolved_same_name_cases' if unresolved else ''))
