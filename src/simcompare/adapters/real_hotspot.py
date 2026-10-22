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
import sys
import tempfile
from collections.abc import Mapping
from pathlib import PurePosixPath
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np
from PIL import Image

from ..numeric_schema import (DEFAULT_TOP_BUCKETS, DEFAULT_MAX_BUCKET_OUTPUT_ENTRIES, DEFAULT_MAX_LABEL_PATTERN_ENTRIES,
                              DIAMETERS_UM, NumericContractError)
from ..numeric_images import resolve_path
from ..png_strict import read_gray_hotspot

CONTRACT_VERSION = 'real_hotspot_v1'
DEFECTS = ('lateral_short', 'vertical_short', 'lateral_open', 'vertical_open')
# 실자료 폴더 이름의 붙여 쓴 표기(lateralshort)를 정식 이름으로 받는다. 파일·CSV 이름은 정식 이름만 쓴다.
DEFECT_ALIASES = {'lateralshort': 'lateral_short', 'verticalshort': 'vertical_short',
                  'lateralopen': 'lateral_open', 'verticalopen': 'vertical_open'}
IMAGES_DIRNAME = 'images'
# 허용하는 case-local CSV 헤더(공백만 무시): 계약 이름과 실자료 이름. 열 순서는 (지름, 면적) 고정이다.
CSV_HEADERS = (['defect_size(um)', 'Area(um2)'], ['DefectSize (um)', 'Area(um^2)'],
               ['DefectSize(um)', 'Area(um^2)'])
CASE_RE = re.compile(r'^([^-]+)-([^-]+)-([^-]+)-([^-]+)-([^-]+)$')
HOTSPOT_RE = re.compile(r'^hotspot_([a-z_]+?)(\d+)\.png$', re.IGNORECASE)


def _physical_memory_bytes():
    """물리 메모리 바이트(POSIX sysconf). 알 수 없으면 None이며 그때는 상한 비교를 하지 않는다."""
    try:
        pages, page_size = os.sysconf('SC_PHYS_PAGES'), os.sysconf('SC_PAGE_SIZE')
    except (AttributeError, ValueError, OSError):
        return None
    return pages * page_size if pages > 0 and page_size > 0 else None


# Benchmark instrumentation, off by default: with SIMCOMPARE_IO_COUNTERS=<file> the process counts the files it
# hashes and the masks it decodes and writes the totals as JSON at exit (scripts/benchmark_scale_compare.py).
IO_COUNTERS = None


def enable_io_counters(path=None):
    global IO_COUNTERS
    IO_COUNTERS = {'sha256_files': 0, 'sha256_bytes': 0, 'mask_decodes': 0}
    if path:
        import atexit
        atexit.register(lambda: Path(path).write_text(json.dumps(IO_COUNTERS), encoding='utf-8'))
    return IO_COUNTERS


if os.environ.get('SIMCOMPARE_IO_COUNTERS'):
    enable_io_counters(os.environ['SIMCOMPARE_IO_COUNTERS'])


def _sha(path: Path) -> str:
    data = path.read_bytes()
    if IO_COUNTERS is not None:
        IO_COUNTERS['sha256_files'] += 1
        IO_COUNTERS['sha256_bytes'] += len(data)
    return hashlib.sha256(data).hexdigest()


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
            if [(name or '').strip() for name in reader.fieldnames or ()] not in CSV_HEADERS:
                raise NumericContractError('invalid_real_csv_header', str(path))
            size_key, area_key = reader.fieldnames
            rows = {}
            for row in reader:
                d_raw, area_raw = (row[size_key] or '').strip(), (row[area_key] or '').strip()
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


MASK_ENCODING = 'npz_deflate_bool_v1'


def _load_mask(asset: Path, record: dict, mmap: bool = False):
    """Load one stored hotspot mask. Legacy imports (no mask_encoding) are .npy; new ones are one-member .npz."""
    if IO_COUNTERS is not None:
        IO_COUNTERS['mask_decodes'] += 1
    encoding, mask_path = record.get('mask_encoding'), str(record.get('mask_path'))
    if encoding is None and 'mask_encoding' not in record:
        if not mask_path.endswith('.npy'):
            raise NumericContractError('real_mask_encoding_invalid', mask_path)
        return np.load(asset, mmap_mode='r' if mmap else None, allow_pickle=False)
    if encoding != MASK_ENCODING or not mask_path.endswith('.npz'):
        raise NumericContractError('real_mask_encoding_invalid', mask_path)
    try:
        with np.load(asset, allow_pickle=False) as archive:
            if list(archive.files) != ['mask']:
                raise NumericContractError('real_mask_encoding_invalid', mask_path)
            value = archive['mask']
            if not isinstance(value, np.ndarray):
                raise NumericContractError('real_mask_encoding_invalid', mask_path)
            return value
    except NumericContractError:
        raise
    except Exception as exc:
        raise NumericContractError('real_mask_encoding_invalid', mask_path) from exc


def _read_gray(path: Path, relative_path: str):
    return read_gray_hotspot(path, relative_path, return_sha256=True)


def _discover_cases(root: Path):
    """Case folders under a hotspot root and their images/ layout directories."""
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
    return found, images_dirs


def import_hotspots(root: Path, out: Path, *, expected_cases: Path | None = None,
                    expected_case_roots: list | None = None, max_input_pixels: int = 100_000_000):
    """Import discovered cases one mask at a time into a versioned manifest."""
    root, out = Path(root).resolve(strict=True), Path(out).resolve()
    if not root.is_dir():
        raise NumericContractError('real_hotspot_root_required', str(root))
    if out.exists():
        raise NumericContractError('real_hotspot_output_exists', str(out))
    if not isinstance(max_input_pixels, int) or max_input_pixels <= 0:
        raise NumericContractError('invalid_resource_limit')
    if expected_cases is not None and expected_case_roots is not None:
        raise NumericContractError('expected_cases_sources_exclusive')
    expected = None
    if expected_cases is not None:
        expected = [line.strip() for line in Path(expected_cases).read_text(encoding='utf-8-sig').splitlines()
                    if line.strip() and not line.lstrip().startswith('#')]
        if len(expected) != len(set(expected)):
            raise NumericContractError('duplicate_expected_case')
        for name in expected:
            _case_name(name)
    found, images_dirs = _discover_cases(root)
    expected_source = None
    if expected_case_roots is not None:
        extra_roots = [Path(extra) for extra in expected_case_roots]
        union = set(p.name for p in found)
        for extra in extra_roots:
            extra_resolved = extra.resolve(strict=True)
            if not extra_resolved.is_dir():
                raise NumericContractError('real_hotspot_root_required', str(extra_resolved))
            union.update(p.name for p in _discover_cases(extra_resolved)[0])
        expected = sorted(union)
        expected_source = {'kind': 'union_of_discovered_roots',
                           'roots': [extra.as_posix() for extra in extra_roots],
                           'limitation': 'cases absent from every root are not detected'}
    elif expected is not None:
        expected_source = {'kind': 'file'}
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=out.name+'.tmp-', dir=out.parent))
    try:
        assets = staging / 'assets'
        assets.mkdir()
        cases, diagnostics, file_index = [], [], {}
        asset_bytes = 0
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
                    mask_path = Path('assets') / f'{index:05d}_{diameter:02d}.npz'
                    np.savez_compressed(staging / mask_path, mask=mask)
                    asset_bytes += (staging / mask_path).stat().st_size
                    count = int(np.count_nonzero(mask))
                    area = count * 0.01
                    record.update(input_status='observed', mask_path=mask_path.as_posix(), mask_encoding=MASK_ENCODING,
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
                    'expected_cases': expected, 'expected_case_source': expected_source,
                    'expected_case_coverage_status': 'confirmed' if expected is not None else 'unconfirmed',
                    'frame': frames, 'cases': cases, 'diagnostics': diagnostics, 'source_files': file_index}
        # Canonicalize through the JSON representation first so integer CSV row
        # keys and tuple shapes hash exactly as their persisted string/list forms.
        manifest = json.loads(json.dumps(manifest, ensure_ascii=False, allow_nan=False))
        canonical = json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        manifest['source_payload_hash'] = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        manifest['manifest_hash_recipe'] = 'sha256(canonical JSON before source_payload_hash; sort_keys, compact separators, UTF-8)'
        (staging / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(staging, out)
        print(f'import-hotspots: wrote {asset_bytes} bytes of compressed mask assets ({MASK_ENCODING})', file=sys.stderr)
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
                mask = None  # at most one mask alive at a time
                mask = _load_mask(asset, record, mmap=True)
                if mask.dtype != np.dtype(bool) or mask.ndim != 2 or (geometry_ok and list(mask.shape) != frame_shape):
                    raise NumericContractError('real_mask_binary_contract_mismatch', record['mask_path'])
                if record.get('mask_shape_yx') != list(mask.shape):
                    raise NumericContractError('real_mask_shape_metadata_mismatch', record['mask_path'])
                area = int(np.count_nonzero(mask)) * 0.01
                if not isinstance(record.get('png_area_um2'), (int, float)) or not math.isfinite(record['png_area_um2']) or not math.isclose(area, record['png_area_um2'], rel_tol=0, abs_tol=1e-12):
                    raise NumericContractError('real_mask_area_mismatch', record['mask_path'])
                del mask
                expected_csv_status = 'missing_diameter' if csv_row is None else derived_status
                if csv_row is not None:
                    _csv_row_contract(csv_row, record['diameter_um'], area,
                                      record.get('area_tolerance_um2'), record.get('area_difference_um2'))
                if record.get('csv_status') != expected_csv_status:
                    raise NumericContractError('real_csv_status_invariant_mismatch', record['mask_path'])
            elif (record.get('mask_path') is not None or record.get('mask_sha256') is not None
                  or record.get('mask_encoding') is not None):
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
    if manifest.get('expected_case_source') is not None:
        source = manifest['expected_case_source']
        kind = source.get('kind') if isinstance(source, dict) else None
        if manifest.get('expected_cases') is None or kind not in ('file', 'union_of_discovered_roots'):
            raise NumericContractError('invalid_real_expected_case_source')
        if kind == 'file' and set(source) != {'kind'}:
            raise NumericContractError('invalid_real_expected_case_source')
        if kind == 'union_of_discovered_roots' and (
                set(source) != {'kind', 'roots', 'limitation'} or not isinstance(source['roots'], list)
                or not all(isinstance(r, str) for r in source['roots']) or not isinstance(source['limitation'], str)):
            raise NumericContractError('invalid_real_expected_case_source')
    return manifest


# ---- resource model (streaming compare) -----------------------------------------------------------
# Unit costs are conservative worst cases measured/derived from linepixel._group; `cells` = frame cells.
RETAINED_MASKS_PER_GROUP = 7          # line, pixel_effective, transfer, union + 3 failure pieces, packed (cells/8 B each)
TRANSIENT_BYTES_PER_CELL = 64         # one group at a time: label 4 + flatnonzero 8 + gather 4 + unique inverse/sort 16
                                      # + lookup 4 + bool temporaries 3 + cell-area cache 16 + loaded mask 2 + touch pass 8
PAYLOAD_BYTES_PER_RECORD = 3 * 1024   # SourceCaseArea + shared raw copy (x2: dataset and result) per case/diameter
BUCKET_ENTRY_BYTES = 100              # one emitted bucket pair id / lineage / contributor entry (tuple slot + share of Bucket)
BUCKET_SERIALIZE_BYTES = 200          # the same entry while its group is serialized: asdict copy slot + ~90 B of JSON text
STAGE6_TOP_BUCKETS = 1                # compare-windows reads masks/areas only: its AreaResults keep one bucket per class
# Measured (T6, scripts: rectangles band model, RSS of _partition_labels incl. final tuples/dicts): 184-190 B per live
# pattern entry and ~215 B per history node at 10-17 M entries; the check in _partition_labels keeps the history
# at the limit (+ one refinement step), so one entry of max_label_pattern_entries is budgeted at 256 B.
LABEL_PATTERN_ENTRY_BYTES = 256
MAP_REGION_PAYLOAD_BYTES = 16 * 1024  # per retained stage-4 region: stable id, coverage dict, bbox/provenance tuples (its links are priced apart)
MAP_REGION_ID_BYTES_PER_CELL = 200    # transient per cell of the largest region: np.argwhere(...).tolist() + its json text
MAP_RUN_BYTES = 48                    # per horizontal run of the thresholded map (<= cells/2 runs): the 4 int32 label arrays
                                      # (16 B, kept until the regions are built) + <= 32 B of scratch (pointer doubling,
                                      # bincount, argsort of the roots)
MAP_REGION_RUN_BYTES = 400            # per horizontal run of the region being built: its PhysicalWindow plus the rectangle and
                                      # clipped-rectangle tuples measure_union makes of it. Measured on a connected comb mask
                                      # (200 x 200, 19,901 runs): 270-300 B per run beyond MAP_RUN_BYTES on Python 3.8/numpy 1.21
                                      # and 230-285 B on 3.11/numpy 2.4; 400 keeps >= 1.3x margin on both.
MAP_OBSERVED_BYTES = 2 * 1024         # per observed case the map scan keeps besides its packed mask: record fields, Measurement,
                                      # bbox, coverage identity entry, electrode index
MAP_CASE_COPY_BYTES = 128 * 1024      # per linked case: the ONE deep copy of case.raw / case.metadata shared by all its links
MAP_LINK_BYTES = 3 * 1024             # per retained CaseLink: its own Measurement (the identity/endpoints/raw are shared per case)
MAP_ROW_BYTES = 12 * 1024             # per ROI export row kept in memory until the export file is written (and, slimmed, after)
MAP_SLIM_ROW_BYTES = 2 * 1024         # per export row candidates keeps after the file is written (roi_id, status, reasons, endpoints)
MAP_REGION_INFO_BYTES = 1024          # per RegionInfo candidates keeps for the merge
MAP_ASSET_ENTRY_BYTES = 4 * 1024      # per case: the validated asset references check_asset_manifest holds for the export
MAP_SCAN_BYTES_PER_CELL = 32          # transient of measuring + packing one representative: loaded mask, its binary_mask copy,
                                      # the cell-area tables of the first window (cached afterwards, priced apart as 8 B/cell)
MAP_ACCUMULATE_BYTES_PER_CELL = 4     # one electrode's OR mask + the unpacked case mask + its valid-masked copy
MAP_REGION_MEASURE_BYTES_PER_CELL = 40  # one region: its full mask, counts[inside] gather, the cell-area tables of its window
                                      # set (one table cached per region), the rebuilt MaskRecord and measure_union temporaries
STAGE4_REMEDY = ('split cases, select fewer regions (--max-candidates, fewer map regions) or raise max_operation_bytes up '
                 'to physical RAM (host RAM, not a cgroup/job limit)')
LAYOUT_TRANSIENT_BYTES_PER_CELL = 20  # PNG decode of one electrode layout (see adapters/electrode_png.py)
STAGE6_UNPACK_CACHE_ENTRIES = 2 * 4 * 4    # local_delta.UNPACK_CACHE_ENTRIES: both sides x (union + 3 pieces) x 4 diameters
STAGE6_LAYOUT_CACHE_CASES = 8         # local_delta_report.LAYOUT_CACHE_CASES: decoded layout cases (2 masks each) per side
STAGE6_PAGE_MASK_ENTRIES = 24         # local_delta_report.PAGE_MASK_CACHE_ENTRIES
# Measured at N=300 / 1262^2 / 32 diameters (tracemalloc, per model): dataset 41 MB = 4.4 KB per record, AreaResult 61 MB = masks + output
# (50 MB) + 1.2 KB per record; the Phase A payload constant (3 KiB per record, "dataset and result") is below that, so stage 6 prices its own.
STAGE6_DATASET_RECORD_BYTES = 5 * 1024   # the configured dataset's share per case/diameter record (raw copies, LazyRecords entries)
STAGE6_RESULT_RECORD_BYTES = 2 * 1024    # the AreaResult's share per record (SourceCaseArea, Measurement, shared raw reference)
STAGE6_FIXED_BYTES = 2 * 1024 * 1024     # per model: manifest + provenance copies, frames, pair table, cfg
STAGE6_REUSED_GROUP_BYTES = 8 * 1024  # metadata of one group read back from the compare artefact (AreaMetrics, fractions, key)
STAGE6_PAIR_BYTES = 4 * 1024          # one bound pair (BoundPair, two BoundSides, source dict, repeats)
STAGE6_ROW_BYTES = 32 * 1024          # one (pair x size x diameter) row: measured 9-12 KB retained incl. its ranking entry, 2.7x margin
STAGE6_SENSITIVITY_ENTRY_BYTES = 4 * 1024   # one (pair x diameter) window_sensitivity entry
STAGE6_WINDOW_BYTES_PER_CELL = 64     # measure_union of one window: crop, MaskRecord, cell-area tables, label/run temporaries
STAGE6_NAMED_INDEX_BYTES = 256        # per case of the named-case index
STAGE6_PAGE_BYTES_PER_PAIR_SIZE = 256 * 1024   # the page: 4 base64 thumbnails + table text of one pair and size
STAGE6_REMEDY = ('fewer pairs or window sizes (split the correspondence file), or raise max_operation_bytes up to '
                 'physical RAM (host RAM, not a cgroup/job limit)')
OPERATION_REMEDY = ('split cases or raise max_operation_bytes up to physical RAM '
                    '(host RAM, not a cgroup/job limit); streaming support for this stage is pending')


def _max_patterns(cases, cells):
    """Most distinct pair-membership patterns one group can have: a cell carries one pattern; pairs <= cases."""
    return min(cells, 2 ** min(cases, 40))


def estimate_area_bytes(cells, group_counts, record_count, max_bucket_entries, top_buckets=None,
                        max_label_entries=DEFAULT_MAX_LABEL_PATTERN_ENTRIES):
    """Peak bytes of compute_areas for ONE model (one layer x diameter group is computed at a time).

    masks     = layers x 32 diameters x 7 packed masks (cells/8 each)          -- the AreaResult, packed
    output    = layers x 32 groups x emitted bucket entries x 100 B            -- RETAINED: every group of the
                AreaResult keeps its bucket lists until the result is written, so it is charged once per group
    group     = max_layer_count x 2 packed masks (sources + pair unions) + 64 B/cell transient
    history   = max_label_entries x 256 B (measured live-pattern cost) + serialization of one group's output
                (emitted x 200 B: asdict copies + JSON text)
    payload   = records x 3 KiB
    emitted per group = max_bucket_entries (full) or, in summary mode, min(max_bucket_entries, 2 classes x
    top_buckets x 3 lists x that layer's case count), never more than the group can structurally produce
    (buckets <= distinct patterns <= min(cells, 2^cases), each <= 3 x cases entries). The label partition
    stays exact and is bounded by max_label_entries, likewise capped at patterns x cases.
    retained = masks + output. Returns the components so callers can combine two models (stage 6): their
    retained+payload coexist, the group/history transient is live for one model at a time.
    """
    layers = len(group_counts)
    packed = (cells + 7) // 8
    masks = layers * len(DIAMETERS_UM) * RETAINED_MASKS_PER_GROUP * packed
    per_layer = [min(max_bucket_entries, _max_patterns(count, cells) * 3 * count) if top_buckets is None else
                 min(max_bucket_entries, 2 * top_buckets * 3 * count, _max_patterns(count, cells) * 3 * count)
                 for count in group_counts.values()]
    max_label_entries = min(max_label_entries, max((_max_patterns(count, cells) * count
                                                    for count in group_counts.values()), default=0))
    output = len(DIAMETERS_UM) * sum(per_layer) * BUCKET_ENTRY_BYTES
    group = max(group_counts.values(), default=0) * 2 * packed + TRANSIENT_BYTES_PER_CELL * cells
    history = max(per_layer, default=0) * BUCKET_SERIALIZE_BYTES + max_label_entries * LABEL_PATTERN_ENTRY_BYTES
    payload = record_count * PAYLOAD_BYTES_PER_RECORD
    retained = masks + output
    return {'retained': retained, 'output': output, 'payload': payload, 'transient': group + history,
            'total': retained + payload + group + history}


def _operation_error(stage, estimated, limit):
    return NumericContractError('oversized_real_operation',
                                f'{stage}: estimated {estimated} bytes exceed limit {limit}; {OPERATION_REMEDY}')


def _dataset_profile(dataset):
    """(cells, {layer: case count}, record count, limit, bucket entries) from metadata only; None if not a real dataset."""
    estimate = dataset.provenance.get('resource_estimate')
    if not isinstance(estimate, dict) or not dataset.frames:
        return None
    frame = next(iter(dataset.frames.values()))
    counts = {}
    for case in dataset.cases:
        counts[case.analysis_layer] = counts.get(case.analysis_layer, 0) + 1
    assignment = dataset.provenance.get('configured_assignments') or {}
    entries = assignment.get('max_bucket_output_entries', DEFAULT_MAX_BUCKET_OUTPUT_ENTRIES)
    label_entries = assignment.get('max_label_pattern_entries', DEFAULT_MAX_LABEL_PATTERN_ENTRIES)
    return (int(frame.valid.size), counts, len(dataset.cases) * len(DIAMETERS_UM),
            estimate['max_operation_bytes'], entries, frame.shape_yx, label_entries)


def _stage4_error(stage, estimated, limit):
    return NumericContractError('oversized_real_operation',
                                f'{stage}: estimated {estimated} bytes exceed limit {limit}; {STAGE4_REMEDY}')


def _packed_bytes(cells):
    return (cells + 7) // 8


def _stage_map_cache(cells, layer_cases):
    """The packed representative masks of one layer and the per-case records the scan keeps."""
    return layer_cases * (_packed_bytes(cells) + MAP_OBSERVED_BYTES)


def estimate_stage_map_admission(cells, layer_cases, layers, *, reserved=0):
    """Peak of the case scan and the electrode accumulation of the most populated layer.

    compute_electrode_maps loads ONE representative mask at a time and packs it (cells/8) next to a small record; the
    area table of the full-frame window is cached for the layer (8 B/cell) and the counts (int64, 8 B/cell) of every
    layer are kept. `reserved` is what the caller holds besides this map (the other product's rows and regions)."""
    retained = layers * 8 * cells
    scan = _stage_map_cache(cells, max(layer_cases, default=0)) + retained + 8 * cells + MAP_SCAN_BYTES_PER_CELL * cells
    accumulate = (_stage_map_cache(cells, max(layer_cases, default=0)) + retained
                  + MAP_ACCUMULATE_BYTES_PER_CELL * cells)
    return reserved + max(scan, accumulate)


def guard_stage_map(stage, dataset, *, reserved=0):
    """Stage 4 admission (electrode-map / candidates / ROI export), before any mask is loaded.

    Streaming scan: one representative mask is alive at a time; per case only the packed mask (cells/8) and a small
    record stay (estimate_stage_map_admission). The region masks are NOT guessed here: their number is only known
    after the counts exist, so compute_electrode_maps counts the connected components first (guard_stage_map_counting
    prices that prepass, guard_stage_map_regions the region construction and guard_stage_map_links every region's links)
    before it allocates the first full-frame region mask.
    """
    profile = _dataset_profile(dataset)
    if profile is None:
        return
    cells, counts, _, limit, _, _, _ = profile
    estimated = estimate_stage_map_admission(cells, list(counts.values()), len(counts), reserved=reserved)
    if estimated > limit:
        raise _stage4_error(stage, estimated, limit)


def _stage_map_live(cells, *, regions, layers_done, layer_cases, layer_electrodes, links_done=0, reserved=0):
    """Bytes alive in compute_electrode_maps while a layer is thresholded and its regions are built, excluding
    the run labels and the id payload: the layer's packed representatives (N x (cells/8 + record)), the int64 counts of
    every layer done, one PACKED mask + MAP_REGION_PAYLOAD_BYTES per region, MAP_LINK_BYTES per retained link, the
    shared per-case raw/metadata copy of every case that may be linked and the thresholded `active` mask (1 x cells).
    reserved = bytes the caller holds besides this map (the other product)."""
    return (reserved + _stage_map_cache(cells, layer_cases) + layers_done * 8 * cells
            + regions * (_packed_bytes(cells) + MAP_REGION_PAYLOAD_BYTES) + links_done * MAP_LINK_BYTES
            + layer_cases * MAP_CASE_COPY_BYTES + 512 * layer_electrodes + cells)


def estimate_stage_map_counting(cells, *, regions_done, runs, layers_done, layer_cases, layer_electrodes, links_done=0,
                                reserved=0):
    """Peak of the component-count prepass of one layer (run-length union-find, see electrode_map._label_runs)."""
    return (_stage_map_live(cells, regions=regions_done, layers_done=layers_done, layer_cases=layer_cases,
                            layer_electrodes=layer_electrodes, links_done=links_done, reserved=reserved)
            + cells + MAP_RUN_BYTES * runs)


def estimate_stage_map_regions(cells, *, regions, runs, largest_area, largest_runs, layers_done, layer_cases,
                               layer_electrodes, links_done=0, new_links=0, reserved=0):
    """Peak while the regions of a layer are built: _stage_map_live with every region counted, the run labels
    (kept until the last region exists), the id payload of the largest region (transient), the window objects plus
    measure_union rectangles of the region with the most runs (one region at a time, transient), the working set of
    measuring one region (MAP_REGION_MEASURE_BYTES_PER_CELL) and the links already built plus the `new_links` the region
    is about to build."""
    return (_stage_map_live(cells, regions=regions, layers_done=layers_done, layer_cases=layer_cases,
                            layer_electrodes=layer_electrodes, links_done=links_done + new_links, reserved=reserved)
            + MAP_RUN_BYTES * runs + MAP_REGION_ID_BYTES_PER_CELL * largest_area + MAP_REGION_RUN_BYTES * largest_runs
            + MAP_REGION_MEASURE_BYTES_PER_CELL * cells)


def guard_stage_map_counting(stage, dataset, *, regions_done, runs, layers_done, layer_cases, layer_electrodes,
                             links_done=0, reserved=0):
    """Stage 4 count prepass bound, called by electrode_map once a layer's counts exist and its horizontal runs
    are known (a row scan, O(width) memory), before the label arrays (MAP_RUN_BYTES per run) are allocated."""
    profile = _dataset_profile(dataset)
    if profile is None:
        return
    estimated = estimate_stage_map_counting(profile[0], regions_done=regions_done, runs=runs, layers_done=layers_done,
                                            layer_cases=layer_cases, layer_electrodes=layer_electrodes,
                                            links_done=links_done, reserved=reserved)
    if estimated > profile[3]:
        raise _stage4_error(f'{stage} (stage-4 component count: {runs} runs of {profile[0]} cells)', estimated, profile[3])


def guard_stage_map_regions(stage, dataset, *, regions, runs, largest_area, largest_runs, layers_done, layer_cases,
                            layer_electrodes, links_done=0, reserved=0):
    """Stage 4 region bound, called by electrode_map after the components of a layer are counted and before any
    region mask is allocated.

    regions = connected regions counted so far over all layers including the current one (from the label sizes,
    no mask allocated), largest_area = cells of the biggest of them, runs = horizontal runs of the current layer,
    largest_runs = horizontal runs of the region with the most (a connected comb has few cells per run).
    See estimate_stage_map_regions for what is alive.
    """
    profile = _dataset_profile(dataset)
    if profile is None:
        return
    cells, limit = profile[0], profile[3]
    estimated = estimate_stage_map_regions(cells, regions=regions, runs=runs, largest_area=largest_area,
                                           largest_runs=largest_runs, layers_done=layers_done, layer_cases=layer_cases,
                                           layer_electrodes=layer_electrodes, links_done=links_done, reserved=reserved)
    if estimated > limit:
        raise _stage4_error(f'{stage} (stage-4 regions: {regions} regions of {cells} cells)', estimated, limit)


STAGE5_PARSE_MULTIPLIER = 5           # retained Python objects per byte of a parsed review_rois.json (measured 1.9-2.5x on 3.8/3.11,
                                      # peak 3.5x with the text; 5 keeps 2x margin for number-heavy rows)
STAGE5_READ_TRANSIENT = 3             # per byte of the file being read: the raw bytes, its decoded text and parser working set
STAGE5_REMEDY = ('rerun candidates with --max-candidates (fewer exported regions) or raise max_operation_bytes up to physical '
                 'RAM (host RAM, not a cgroup/job limit)')


def estimate_stage_confirm_read(sizes):
    """Peak of confirm-pairs while it holds every parsed candidates export: all files stay parsed (their rows are looked
    up by roi_id), only the file being read adds the raw bytes and the decoded text."""
    return sum(sizes) * STAGE5_PARSE_MULTIPLIER + max(sizes, default=0) * STAGE5_READ_TRANSIENT


def guard_stage_confirm_read(stage, datasets, sizes):
    """Stage 5 pre-read gate on the export file sizes, called before any export is read or json-parsed."""
    profiles = [_dataset_profile(d) for d in datasets]
    if not profiles or any(p is None for p in profiles):
        return
    limit = min(p[3] for p in profiles)
    estimated = estimate_stage_confirm_read(sizes)
    if estimated > limit:
        raise NumericContractError(
            'oversized_real_operation',
            f'{stage} (stage-5 export read: {sum(sizes)} bytes in {len(sizes)} files x {STAGE5_PARSE_MULTIPLIER} parsed): '
            f'estimated {estimated} bytes exceed limit {limit}; {STAGE5_REMEDY}')


def estimate_stage_map_export(cells, *, layers, regions, links, rows, cases, reserved=0):
    """Peak while ROI rows are built and written: the map is still alive (counts of every layer, packed regions, links,
    the shared per-case raw/metadata copies), the rows exist in full until the file is written, one electrode layout is
    decoded at a time and check_asset_manifest holds the validated references of every case."""
    return (reserved + layers * 8 * cells + regions * (_packed_bytes(cells) + MAP_REGION_PAYLOAD_BYTES)
            + links * MAP_LINK_BYTES + cases * (MAP_CASE_COPY_BYTES + MAP_ASSET_ENTRY_BYTES) + rows * MAP_ROW_BYTES
            + LAYOUT_TRANSIENT_BYTES_PER_CELL * cells)


def guard_stage_map_export(stage, dataset, maps, rows, *, reserved=0):
    """Stage 4 export bound, called by roi_export once the number of rows to build is known and before any row exists."""
    profile = _dataset_profile(dataset)
    if profile is None:
        return
    layers = list(maps.layers) if maps is not None else []
    regions = [r for layer in layers for r in layer.regions]
    estimated = estimate_stage_map_export(profile[0], layers=len(layers), regions=len(regions),
                                          links=sum(len(r.cases) for r in regions), rows=rows,
                                          cases=len(dataset.cases), reserved=reserved)
    if estimated > profile[3]:
        raise _stage4_error(f'{stage} (stage-4 export: {rows} rows)', estimated, profile[3])


def guard_stage_map_links(stage, dataset, *, regions, runs, largest_area, largest_runs, layers_done, layer_cases,
                          layer_electrodes, links_done, new_links, reserved=0):
    """Stage 4 link bound, called by electrode_map for every region after its linked cases are known and before its
    windows, CaseLinks or measurements exist: the links built so far (links_done, all layers) plus this region's."""
    profile = _dataset_profile(dataset)
    if profile is None:
        return
    cells, limit = profile[0], profile[3]
    estimated = estimate_stage_map_regions(cells, regions=regions, runs=runs, largest_area=largest_area,
                                           largest_runs=largest_runs, layers_done=layers_done, layer_cases=layer_cases,
                                           layer_electrodes=layer_electrodes, links_done=links_done,
                                           new_links=new_links, reserved=reserved)
    if estimated > limit:
        raise _stage4_error(f'{stage} (stage-4 links: {links_done + new_links} links in {regions} regions of {cells} cells)',
                            estimated, limit)


def _stage6_frames(dataset):
    """[(cells, pixel pitch)] of every frame of a dataset (a real dataset has one)."""
    return [(int(frame.valid.size), float(frame.pixel_pitch_um)) for frame in dataset.frames.values()]


def estimate_stage_compare_windows(profiles, frames, *, ready, blocked, sizes, jitter_um=None, named=False,
                                   layouts=(False, False), reuse=False, phase='all'):
    """Peak bytes of compare-windows as the MAX of its phases (never their sum), for the largest frame of both models.

    profiles = _dataset_profile of reference and target; frames = _stage6_frames of each; layouts = per side: does the
    cfg configure an asset_manifest. Phases:
      areas   compute_areas of both models (or the artefact load): held + the one-group transient
      rows    all rows exist, the unpack cache (<= STAGE6_UNPACK_CACHE_ENTRIES masks) and the named-case records are
              live, windows are measured: held + pairs + rows + caches + one window's temporaries
      report  the unpack cache is dropped; the page keeps its own small cache, <= STAGE6_LAYOUT_CACHE_CASES decoded
              layout cases per side, the pinned overview masks, one decode, the page text
    held = both AreaResults (summary buckets: retained + payload) or, with the artefact, the packed masks of the
    groups compare-windows reads (4 per group) + their metadata; every term is an upper bound.
    Returns {'areas', 'rows', 'report', 'peak', terms...} (a phase that does not apply is 0)."""
    cells = [p[0] for p in profiles]
    max_cells = max(cells)
    groups = [len(p[1]) * len(DIAMETERS_UM) for p in profiles]
    parts = [estimate_area_bytes(p[0], p[1], p[2], p[4], STAGE6_TOP_BUCKETS, p[6]) for p in profiles]
    dataset_bytes = [p[2] * STAGE6_DATASET_RECORD_BYTES + STAGE6_FIXED_BYTES for p in profiles]
    if reuse:
        held = sum(g * (4 * _packed_bytes(c) + STAGE6_REUSED_GROUP_BYTES) + data
                   for g, c, data in zip(groups, cells, dataset_bytes))
        areas_transient = sum(g * STAGE6_REUSED_GROUP_BYTES for g in groups) + max(_packed_bytes(c) for c in cells)
    else:
        held = sum(part['retained'] + p[2] * STAGE6_RESULT_RECORD_BYTES + data
                   for part, p, data in zip(parts, profiles, dataset_bytes))
        areas_transient = max(part['transient'] for part in parts)
    n_sizes, n_diameters = len(sizes), len(DIAMETERS_UM)
    pairs_bytes = (ready + blocked) * STAGE6_PAIR_BYTES
    rows_bytes = ready * n_sizes * n_diameters * STAGE6_ROW_BYTES + ready * n_diameters * STAGE6_SENSITIVITY_ENTRY_BYTES
    side = max(sizes, default=0.0) + 2 * (jitter_um or 0.0)
    window_cells = 0
    for c, model_frames in zip(cells, frames):
        if model_frames:
            window_cells = max(window_cells, min(c, (math.ceil(side / model_frames[0][1]) + 4) ** 2))
    rows_phase = report = 0
    if ready:
        # every (pair, size, diameter) row touches <= 8 masks (4 per side), so the cache never needs more than that
        unpack_entries = min(STAGE6_UNPACK_CACHE_ENTRIES, 4 * sum(groups), 8 * n_diameters * ready)
        caches = (unpack_entries + 1) * max_cells
        if named:
            caches += (2 * n_diameters + 2) * max_cells + sum(p[2] for p in profiles) // n_diameters * STAGE6_NAMED_INDEX_BYTES
        rows_phase = held + pairs_bytes + rows_bytes + caches + STAGE6_WINDOW_BYTES_PER_CELL * window_cells
        report = (held + pairs_bytes + rows_bytes + (STAGE6_PAGE_MASK_ENTRIES + 1) * max_cells
                  + ready * n_sizes * STAGE6_PAGE_BYTES_PER_PAIR_SIZE + 4 * max_cells)
    else:
        caches = 0
    if any(layouts):
        asset_cells = [c for c, on in zip(cells, layouts) if on]
        widest = max(asset_cells)
        layout_cache = sum(STAGE6_LAYOUT_CACHE_CASES * 2 * c for c in asset_cells) if ready else 0
        pinned = sum(2 * c * max(1, len(f)) for c, f, on in zip(cells, frames, layouts) if on) if ready else 0
        refs = sum(p[2] // n_diameters * MAP_ASSET_ENTRY_BYTES for p, on in zip(profiles, layouts) if on)
        report = max(report, held + pairs_bytes + rows_bytes) + layout_cache + pinned + refs \
            + (LAYOUT_TRANSIENT_BYTES_PER_CELL + 4) * widest
    areas_phase = held + areas_transient
    wanted = {'all': (areas_phase, rows_phase, report), 'compute': (areas_phase, rows_phase, 0),
              'rows': (0, rows_phase, 0), 'report': (0, 0, report)}[phase]
    return {'areas': wanted[0], 'rows': wanted[1], 'report': wanted[2], 'peak': max(wanted), 'held': held,
            'rows_bytes': rows_bytes, 'caches': caches, 'pairs_bytes': pairs_bytes, 'window_cells': window_cells}


def stage_compare_windows_estimate(reference, target, *, layouts_cfg=None, pairs=None, sizes=(), jitter_um=None,
                                   reuse=False, phase='all'):
    """(estimate dict, limit) for the two datasets, or None when either is not a configured real dataset."""
    profiles = [_dataset_profile(d) for d in (reference, target)]
    if any(p is None for p in profiles):
        return None
    pairs = [] if pairs is None else list(pairs)
    ready = sum(1 for p in pairs if p.status == 'ready')
    named = any(p.status == 'ready' and p.a.endpoints is not None and p.b.endpoints is not None for p in pairs)
    layouts = tuple(isinstance(layouts_cfg, dict) and isinstance(layouts_cfg.get(name), dict)
                    and bool(layouts_cfg[name].get('asset_manifest')) for name in ('reference', 'target'))
    estimate = estimate_stage_compare_windows(
        profiles, [_stage6_frames(reference), _stage6_frames(target)], ready=ready, blocked=len(pairs) - ready,
        sizes=tuple(sizes), jitter_um=jitter_um, named=named, layouts=layouts, reuse=reuse, phase=phase)
    estimate['ready'], estimate['blocked'] = ready, len(pairs) - ready
    return estimate, min(p[3] for p in profiles)


def guard_stage_compare_windows(stage, reference, target, *, layouts_cfg=None, pairs=None, sizes=(), jitter_um=None,
                                reuse=False, phase='all'):
    """Stage 6 admission, after the correspondence is loaded and bound and before any compute or allocation.

    pairs = BoundPair list (ready / blocked are counted), sizes = the validated window sizes (µm). The estimate is the
    max over the phases (see estimate_stage_compare_windows); layouts_cfg = cfg prices the page's layout cache for the
    sides that have an asset_manifest; reuse = the compare artefact supplies the AreaResults (no compute_areas).
    phase selects the part that is still ahead of the caller ('compute' before the rows, 'report' before the page).
    A caller without pairs (None) prices zero pairs, as the stage-2-only checks always did."""
    found = stage_compare_windows_estimate(reference, target, layouts_cfg=layouts_cfg, pairs=pairs, sizes=sizes,
                                           jitter_um=jitter_um, reuse=reuse, phase=phase)
    if found is None:
        return
    estimate, limit = found
    if estimate['peak'] > limit:
        raise NumericContractError(
            'oversized_real_operation',
            f"{stage}: estimated {estimate['peak']} bytes exceed limit {limit} "
            f"({estimate['ready']} ready and {estimate['blocked']} blocked pairs x {len(tuple(sizes))} window sizes x "
            f"{len(DIAMETERS_UM)} diameters{', compare masks reused' if reuse else ''}); {STAGE6_REMEDY}")


class LazyRecords(Mapping):
    """Per-diameter MaskRecords of one real case; a mask is read from disk only on item access (no cache).

    header(d) returns RecordHeader without touching the file; records[d] loads the mask through _load_mask with
    the same dtype/shape check and error code as the eager path. Geometry-unconfirmed and missing records carry
    no mask and never read a file. Treat it as read-only: it holds metadata, never arrays.
    """

    def __init__(self, case_name, frame, shape, folder, identity, analysis_layer, entries):
        self._case_name, self._frame, self._shape, self._folder = case_name, frame, shape, folder
        self._identity, self._layer = identity, analysis_layer
        self._entries = entries  # float diameter -> (source_record dict, status, reasons, source_ca, tolerance, load)

    def __iter__(self):
        return iter(self._entries)

    def __len__(self):
        return len(self._entries)

    def __contains__(self, diameter):
        return diameter in self._entries

    def header(self, diameter):
        from ..numeric_schema import RecordHeader
        _, status, reasons, _, _, _ = self._entries[diameter]
        return RecordHeader(diameter, self._frame, self._identity, self._layer, status, reasons)

    def __getitem__(self, diameter):
        from ..numeric_schema import MaskRecord
        source_record, status, reasons, source_ca, tolerance, load = self._entries[diameter]
        mask = None
        if load:
            mask = _load_mask(resolve_path(self._folder, source_record['mask_path']), source_record)
            if mask.dtype != np.dtype(bool) or mask.shape != self._shape:
                raise NumericContractError('real_mask_shape_mismatch', self._case_name)
        return MaskRecord(float(source_record['diameter_um']), self._frame, mask, status, reasons,
                          source_ca, self._identity, self._layer,
                          conservation_atol_um2=tolerance)



def configured_dataset(folder: Path, manifest: dict, assignment: dict, top_buckets=DEFAULT_TOP_BUCKETS):
    """Bind explicit analysis assignments and pair table to real observations.

    top_buckets prices the compare admission estimate: an int N = summary mode (the default, like the compare
    default), None = full bucket detail (`compare.full_bucket_detail: true`), whose retained output is charged for
    every group. The choice is recorded as provenance['admitted_bucket_detail']; compute_areas defaults to it and refuses
    a costlier explicit mode. Stages that never compute buckets (validate-data, electrode-map, compare-windows) must not be
    refused for output they do not keep, so they use the summary default.
    """
    import copy
    from ..numeric_schema import (CaseIdentity, DensityRow, Endpoint, Frame, MaskRecord,
        NumericCase, NumericDataset, Pair, SourceCA)

    required = ('product_id', 'design_version', 'source_id', 'process_step_id', 'exposure_id', 'unit_id',
                'layer_stack')
    if not isinstance(assignment, dict) or any(not assignment.get(k) for k in required):
        raise NumericContractError('real_grouping_assignment_required')
    allowed = set(required) | {'pair_table_path', 'electrode_roles', 'electrode_role_policy', 'max_group_mask_cells',
                               'max_materialized_source_cells', 'max_operation_bytes',
                               'max_bucket_output_entries', 'max_label_pattern_entries'}
    if set(assignment) - allowed:
        raise NumericContractError('unknown_real_grouping_assignment_field')
    role_policy = assignment.get('electrode_role_policy')
    if role_policy is not None:
        # electrode_id: pair 표에 나오는 모든 전극이 자기 이름을 role로 받는다(아래 pair 표를 읽은 뒤 채운다).
        if role_policy != 'electrode_id':
            raise NumericContractError('invalid_real_electrode_role_policy', str(role_policy))
        if 'electrode_roles' in assignment:
            raise NumericContractError('electrode_roles_conflict_with_role_policy')
        if not assignment.get('pair_table_path'):
            raise NumericContractError('electrode_role_policy_requires_pair_table')
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
                earlier = pairs.get(pair.electrode_ids)
                if earlier is not None:
                    # A-B와 B-A처럼 같은 조합이 다시 나오면 분류와 표 버전이 같을 때만 첫 행을 유지한다.
                    if (earlier.classification, earlier.table_version) != (pair.classification, pair.table_version):
                        raise NumericContractError('duplicate_or_conflicting_pair')
                    if pair.pair_id in ids and pair.pair_id != earlier.pair_id:
                        raise NumericContractError('duplicate_or_conflicting_pair')
                    ids.add(pair.pair_id)
                    continue
                if pair.pair_id in ids:
                    raise NumericContractError('duplicate_or_conflicting_pair')
                pairs[pair.electrode_ids] = pair
                ids.add(pair.pair_id)
    if role_policy == 'electrode_id':
        role_map = {electrode: electrode for pair in pairs.values() for electrode in pair.electrode_ids}
    frame_spec = manifest['frame']
    if frame_spec.get('shape_yx') is None:
        raise NumericContractError('real_common_frame_shape_required')
    shape = tuple(frame_spec['shape_yx'])
    if len(shape) != 2 or any(type(v) is not int or v <= 0 for v in shape):
        raise NumericContractError('real_common_frame_shape_required')
    frame_id = frame_spec['frame_id']
    cases, unresolved = [], []
    max_group_cells = assignment.get('max_group_mask_cells', 5_000_000_000)
    max_source_cells = assignment.get('max_materialized_source_cells', 6_000_000_000)
    max_operation_bytes = assignment.get('max_operation_bytes', 8 * 1024**3)
    max_bucket_entries = assignment.get('max_bucket_output_entries', DEFAULT_MAX_BUCKET_OUTPUT_ENTRIES)
    if type(max_bucket_entries) is not int or max_bucket_entries <= 0:
        raise NumericContractError('invalid_resource_limit', 'max_bucket_output_entries must be a positive integer')
    max_label_entries = assignment.get('max_label_pattern_entries', DEFAULT_MAX_LABEL_PATTERN_ENTRIES)
    if type(max_label_entries) is not int or max_label_entries <= 0:
        raise NumericContractError('invalid_resource_limit', 'max_label_pattern_entries must be a positive integer')
    if type(max_group_cells) is not int or max_group_cells <= 0 or type(max_source_cells) is not int or max_source_cells <= 0:
        raise NumericContractError('invalid_resource_limit')
    physical = _physical_memory_bytes()
    if type(max_operation_bytes) is not int or max_operation_bytes <= 0:
        raise NumericContractError('invalid_resource_limit', 'max_operation_bytes must be a positive integer')
    # 기본은 8 GiB다. 큰 메모리 서버에서는 명시적으로 올릴 수 있지만, 물리 메모리를 알 수 있으면 그 값을 넘길 수 없다.
    if physical is not None and max_operation_bytes > physical:
        raise NumericContractError('invalid_resource_limit',
                                   f'max_operation_bytes {max_operation_bytes} exceeds physical memory {physical}')
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
    frame_cells = shape[0] * shape[1]
    # max_materialized_source_cells bounds what one layer x diameter group materialises: sources + pair masks.
    widest = max(group_counts.values(), default=0) * frame_cells
    if widest > max_source_cells:
        raise NumericContractError('oversized_real_materialization',
                                   f'{widest} source cells exceed limit {max_source_cells}')
    oversized = {layer: count*frame_cells for layer, count in group_counts.items()
                 if count*frame_cells > max_group_cells}
    if oversized:
        layer, cells = next(iter(sorted(oversized.items())))
        raise NumericContractError('oversized_real_mask_group', f'{layer} requires {cells} cells; limit {max_group_cells}')
    # Streaming model: masks are read one at a time, a group keeps packed bits, results keep packed masks.
    estimate = estimate_area_bytes(frame_cells, group_counts, sum(group_counts.values()) * len(DIAMETERS_UM),
                                   max_bucket_entries, top_buckets, max_label_entries)
    estimated_peak_bytes = estimate['total']
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
        entries = {}
        geometry_confirmed = raw_case.get('geometry_status') == 'confirmed'
        for source_record in raw_case['records']:
            source_ca = None
            load = source_record['input_status'] == 'observed' and geometry_confirmed
            if load:
                csv_area = source_record.get('csv_area')
                if csv_area is not None and source_record.get('csv_status') == 'consistent':
                    source_ca = SourceCA(csv_area['value'], frame_id, frame.valid_hash)
            reasons = () if source_record['input_status'] == 'observed' else ('missing_input',)
            if source_record.get('csv_status') == 'mismatch':
                reasons += ('csv_area_mismatch',)
            if not geometry_confirmed:
                reasons += ('case_geometry_unconfirmed',)
            tolerance = source_record.get('area_tolerance_um2') or 1e-9
            entries[float(source_record['diameter_um'])] = (
                source_record, 'missing' if 'case_geometry_unconfirmed' in reasons else source_record['input_status'],
                reasons, source_ca, tolerance, load)
        records = LazyRecords(raw_case['case_name'], frame, shape, folder, identity, analysis_layer, entries)
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
                  # the bucket detail the admission estimate above priced; linepixel.compute_areas binds to it
                  'admitted_bucket_detail': ({'bucket_detail': 'full'} if top_buckets is None else
                                             {'bucket_detail': 'summary', 'top_buckets': top_buckets}),
                  'electrode_roles_effective': dict(sorted(role_map.items())),
                  'resource_estimate': {'estimated_peak_bytes': estimated_peak_bytes,
                    'max_operation_bytes': max_operation_bytes,
                    'max_bucket_output_entries': max_bucket_entries,
                    'max_label_pattern_entries': max_label_entries,
                    'components': estimate,
                    'method': 'streaming: 7 packed masks + the bucket output of every layer/diameter group retained '
                              '+ one group transient (2 packed masks per source + 64 B/cell + label budget + '
                              'serialization of one group) + 3 KiB per record'}}
    return NumericDataset(cases, pairs, {frame_id: frame}, [],
                          {'frame_id': frame_id, 'unit_width_um': None, 'unit_height_um': None,
                           'measured_width_um': None, 'measured_height_um': None}, {},
                          {'G1': {'status': 'pending', 'answer': None}, 'G2': {'status': 'pending', 'answer': None},
                           'G7a': {'status': 'answered', 'answer': 'failure_pieces'}, 'G7b': {'status': 'answered', 'answer': 'short_only'}},
                          provenance, ('density_contract_unconfirmed', 'unit_area_basis_unconfirmed',
                                       'unresolved_same_name_cases' if unresolved else ''))
