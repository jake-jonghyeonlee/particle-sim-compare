"""Canonical numeric_v2 loader; real source format/density approval stays gated.

Supports the accepted T1 document fixture and explicit root-relative binary
assets. It does not import or reinterpret AnalysisBundle, CAA or AE inputs.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
from pathlib import Path

from ..numeric_images import check_hash, read_binary, resolve_path
from ..numeric_schema import (
    DIAMETERS_UM, CaseIdentity, DensityRow, Endpoint, Frame, MaskRecord,
    NumericCase, NumericContractError, NumericDataset, Pair, SourceCA,
    binary_mask, convert_area, convert_length, diameter_key, finite, text,
)

SOURCE_PAYLOAD_KEYS = (
    'source_format', 'source_contract_version', 'is_synthetic', 'diameters_um',
    'frames', 'layer_stack', 'layer_process_mapping_status', 'cases',
    'unit_domain', 'panel', 'pair_table_version', 'files',
)
HASH_RECIPE = ('sha256(canonical source_payload UTF-8 JSON sort_keys=True,separators=(comma,colon),'
               'ensure_ascii=False); source_payload keys documented in README; file hashes are exact bytes')


def source_payload_hash(manifest: dict) -> str:
    payload = {key: manifest[key] for key in SOURCE_PAYLOAD_KEYS}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def _reject_constant(value):
    raise NumericContractError('nonfinite_json_number', value)


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise NumericContractError('duplicate_json_key', key)
        obj[key] = value
    return obj


def _csv(root, relative, required):
    path = resolve_path(root, relative)
    with path.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        names = reader.fieldnames or []
        if len(names) != len(set(names)) or not set(required) <= set(names):
            raise NumericContractError('invalid_csv_header', relative)
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise NumericContractError('invalid_csv_row', relative)
    return rows


def _number(value, field, *, positive=False):
    if value is None or value == '':
        return None
    try:
        result = float(value) if isinstance(value, str) else value
    except ValueError as exc:
        raise NumericContractError('invalid_number', field) from exc
    return finite(result, field, positive=positive, nonnegative=not positive)


def _boolean(value):
    if value not in ('true', 'false'):
        raise NumericContractError('invalid_csv_boolean', str(value))
    return value == 'true'


def _asset(root, entry, *, synthetic):
    if isinstance(entry, dict):
        return read_binary(root, entry['path'], sha256=entry['sha256'],
                           value_semantics=entry.get('value_semantics'), synthetic=synthetic)
    if not synthetic:
        raise NumericContractError('inline_real_mask_unsupported')
    return binary_mask(entry)  # inline synthetic masks are always nonzero-is-marked


def _frames(root, entries, synthetic):
    frames = {}
    for entry in entries:
        fid = text(entry['frame_id'], 'frame_id')
        if fid in frames:
            raise NumericContractError('duplicate_frame', fid)
        if entry.get('valid_status') != 'observed':
            raise NumericContractError('valid_observation_required', fid)
        unit = entry['length_unit']
        # *_um fields are already canonical, never reinterpret them as nm/mm.
        if 'pixel_pitch_um' in entry or 'origin_xy_um' in entry:
            if unit != 'um':
                raise NumericContractError('canonical_frame_unit_mismatch')
            pitch, origin = entry['pixel_pitch_um'], entry['origin_xy_um']
        else:
            pitch = convert_length(entry['pixel_pitch'], unit)
            origin = convert_length(entry['origin_xy'], unit)
        frame = Frame(fid, pitch, tuple(origin), tuple(entry['shape_yx']),
                      _asset(root, entry['valid'], synthetic=synthetic),
                      entry['axis_order'], entry['origin_convention'])
        expected = entry.get('expected_domain_bbox_um')
        if expected is not None and tuple(expected) != frame.bbox_um:
            raise NumericContractError('expected_domain_mismatch', fid)
        frames[fid] = frame
    if not frames:
        raise NumericContractError('missing_frames')
    return frames


def _pairs(root, manifest):
    rows = _csv(root, manifest.get('pairs_path', 'pairs.csv'),
                ['pair_id', 'electrode_a_id', 'electrode_b_id', 'class', 'pair_table_version'])
    pairs, ids = {}, set()
    for row in rows:
        pair = Pair(row['pair_id'], frozenset((row['electrode_a_id'], row['electrode_b_id'])),
                    row['class'], row['pair_table_version'])
        if pair.table_version != manifest['pair_table_version']:
            raise NumericContractError('pair_table_version_mismatch')
        if pair.electrode_ids in pairs or pair.pair_id in ids:
            raise NumericContractError('duplicate_or_conflicting_pair')
        pairs[pair.electrode_ids] = pair
        ids.add(pair.pair_id)
    return pairs


def _density(root, manifest):
    rows = _csv(root, manifest['density']['path'], ['distribution_id', 'fab_id', 'product_family',
                'process_layer', 'period', 'source_id', 'diameter_um', 'raw_value', 'raw_value_unit',
                'observed', 'surveyed_area_um2', 'averaging_basis', 'bin_semantics',
                'bin_lower_um', 'bin_upper_um', 'density_per_um2', 'canonical_unit', 'status', 'reasons'])
    result, seen, metadata, grids = [], set(), {}, {}
    for raw in rows:
        distribution = text(raw['distribution_id'], 'distribution_id')
        key = diameter_key(_number(raw['diameter_um'], 'diameter', positive=True), raw.get('diameter_unit') or 'um')
        if key not in DIAMETERS_UM or (distribution, key) in seen:
            raise NumericContractError('invalid_or_duplicate_density_diameter')
        seen.add((distribution, key))
        grids.setdefault(distribution, set()).add(key)
        columns = ('fab_id', 'product_family', 'process_layer', 'period', 'source_id',
                   'raw_value_unit', 'averaging_basis', 'bin_semantics', 'surveyed_area_um2')
        signature = tuple(raw[name] for name in columns)
        for name in ('fab_id', 'process_layer', 'period', 'source_id', 'raw_value_unit'):
            text(raw[name], name)
        if distribution in metadata and metadata[distribution] != signature:
            raise NumericContractError('inconsistent_density_distribution', distribution)
        metadata[distribution] = signature
        observed = _boolean(raw['observed'])
        value = _number(raw['raw_value'], 'raw density')
        if observed != (value is not None):
            raise NumericContractError('density_observation_mismatch')
        area = _number(raw['surveyed_area_um2'], 'surveyed area', positive=True)
        lower, upper = _number(raw['bin_lower_um'], 'bin lower'), _number(raw['bin_upper_um'], 'bin upper')
        if (lower is None) != (upper is None) or (lower is not None and lower >= upper):
            raise NumericContractError('invalid_density_bin')
        canonical = _number(raw['density_per_um2'], 'canonical density')
        # T2 deliberately reads raw measurements only. G1 conversion belongs to T4.
        if canonical is not None or raw['canonical_unit']:
            raise NumericContractError('density_conversion_not_supported_in_t2')
        if raw['status'] not in ('available', 'partial', 'unavailable', 'not_applicable'):
            raise NumericContractError('invalid_density_status')
        reasons = tuple(filter(None, raw['reasons'].split(';')))
        reasons = tuple(dict.fromkeys((*reasons, 'density_contract_unconfirmed',
                                     *(() if observed else ('missing_density_input',)))))
        result.append(DensityRow(distribution, float(key), value, raw['raw_value_unit'], observed,
                                 area, lower, upper, None, 'unavailable', reasons, copy.deepcopy(raw)))
    if not grids or any(grid != set(DIAMETERS_UM) for grid in grids.values()):
        raise NumericContractError('incomplete_density_coverage')
    if manifest['density']['distribution_id'] not in grids:
        raise NumericContractError('unknown_density_distribution')
    return result


def _cases(root, manifest, frames, pairs, synthetic):
    cases, identities = [], set()
    stack = {}
    orders = set()
    for entry in manifest['layer_stack']:
        layer = text(entry['actual_layer'], 'actual_layer')
        order = finite(entry['stack_order'], 'stack_order')
        if not order.is_integer() or layer in stack or order in orders:
            raise NumericContractError('invalid_or_duplicate_layer_stack')
        text(entry['density_process_layer'], 'density_process_layer')
        stack[layer] = entry
        orders.add(order)
    for raw_case in manifest['cases']:
        identity = CaseIdentity(**{name: raw_case[name] for name in CaseIdentity.__dataclass_fields__})
        if identity in identities:
            raise NumericContractError('duplicate_case_identity')
        identities.add(identity)
        if identity.frame_id not in frames:
            raise NumericContractError('unknown_frame')
        frame = frames[identity.frame_id]
        if any(not isinstance(ep.get('role'), str) or not ep['role'].strip()
               for ep in raw_case['endpoints']):
            raise NumericContractError('legacy_endpoint_role_required')
        endpoints = tuple(Endpoint(**ep) for ep in raw_case['endpoints'])
        if len(endpoints) != 2 or len({ep.electrode_id for ep in endpoints}) != 2:
            raise NumericContractError('invalid_case_endpoints')
        raw = raw_case['raw']
        if raw['case_id'] != identity.case_id or raw['ordered_endpoints'] != raw_case['endpoints']:
            raise NumericContractError('raw_identity_mismatch')
        text(raw['fab_pair_key'], 'raw fab_pair_key')
        text(raw['assigned_process_layer'], 'raw assigned_process_layer')
        text(raw_case['unit_id'], 'unit_id')
        failure_type = raw_case['failure_type']
        if failure_type not in ('lateral_short', 'vertical_short'):
            raise NumericContractError('unsupported_numeric_failure_type')
        pair = pairs.get(frozenset(ep.electrode_id for ep in endpoints))
        if pair is not None and raw_case.get('pair_id') != pair.pair_id:
            raise NumericContractError('pair_identity_mismatch')
        analysis_layer = text(raw_case['analysis_layer'], 'analysis_layer')
        density_layer = text(raw_case['density_process_layer'], 'density_process_layer')
        if any(ep.actual_layer not in stack for ep in endpoints):
            raise NumericContractError('unknown_actual_layer')
        height = None
        if failure_type == 'vertical_short':
            if endpoints[0].actual_layer == endpoints[1].actual_layer:
                raise NumericContractError('vertical_same_layer')
            upper = max(endpoints, key=lambda ep: stack[ep.actual_layer]['stack_order']).actual_layer
            if raw_case['upper_actual_layer'] != upper or analysis_layer != upper or density_layer != stack[upper]['density_process_layer']:
                raise NumericContractError('upper_layer_assignment_mismatch')
            height = finite(raw_case['height_um'], 'height_um', positive=True)
            if height >= 5 or not isinstance(raw_case.get('height_evidence'), str) or not raw_case['height_evidence'].strip():
                raise NumericContractError('height_evidence_required')
        elif endpoints[0].actual_layer != endpoints[1].actual_layer or analysis_layer != endpoints[0].actual_layer:
            raise NumericContractError('lateral_layer_mismatch')
        elif density_layer != stack[analysis_layer]['density_process_layer']:
            raise NumericContractError('density_layer_assignment_mismatch')
        if identity.source_contract_version == '1.2':
            if raw['assigned_process_layer'] != density_layer:
                raise NumericContractError('migration_assignment_conflict')
            if not raw_case.get('migration_evidence'):
                raise NumericContractError('migration_evidence_required')
        records = {}
        for entry in raw_case['records']:
            if 'diameter_um' in entry:
                if entry.get('diameter_unit', 'um') != 'um':
                    raise NumericContractError('canonical_diameter_unit_mismatch')
                key = diameter_key(entry['diameter_um'])
            else:
                key = diameter_key(entry['diameter'], entry['diameter_unit'])
            if key in records:
                raise NumericContractError('duplicate_case_diameter', identity.case_id)
            if key not in DIAMETERS_UM:
                raise NumericContractError('unsupported_diameter_grid')
            status = entry['input_status']
            # Polarity is declared on the asset reference; the record itself stays 'binary'.
            if entry.get('value_semantics', 'binary') != 'binary':
                raise NumericContractError('unsupported_mask_semantics')
            reasons = tuple(entry.get('reasons', ()))
            below = height is not None and key < height
            if status == 'not_applicable':
                evidence = entry.get('applicability_evidence')
                if not below or not isinstance(evidence, dict) or not isinstance(evidence.get('source'), str) or not evidence['source'].strip() or evidence.get('height_um') != height or evidence.get('comparison') != 'diameter_um < height_um':
                    raise NumericContractError('not_applicable_evidence_required')
                if 'below_height_vertical_short' not in reasons:
                    raise NumericContractError('not_applicable_reason_required')
            elif below:
                raise NumericContractError('below_height_status_mismatch')
            if status == 'observed':
                if (entry.get('mask') is None) == (entry.get('asset') is None):
                    raise NumericContractError('exactly_one_mask_source_required')
                mask = _asset(root, entry.get('asset') if entry.get('asset') is not None else entry['mask'], synthetic=synthetic)
            else:
                if entry.get('mask') is not None or entry.get('asset') is not None:
                    raise NumericContractError('unobserved_mask_supplied')
                mask = None
                if status == 'missing' and 'missing_input' not in reasons:
                    raise NumericContractError('missing_input_reason_required')
            source_ca = None
            if entry.get('source_ca') is not None:
                ca = entry['source_ca']
                source_ca = SourceCA(convert_area(ca['value'], ca['unit']), ca['frame_id'], ca['valid_hash'], ca['basis'])
            records[float(key)] = MaskRecord(float(key), frame, mask, status, reasons, source_ca, identity, analysis_layer)
        if set(records) != set(DIAMETERS_UM):
            raise NumericContractError('incomplete_case_coverage', identity.case_id)
        cases.append(NumericCase(identity, endpoints, failure_type, None if pair is None else pair.pair_id,
                                 None if pair is None else pair.classification, analysis_layer, density_layer,
                                 records, copy.deepcopy(raw), copy.deepcopy(raw_case),
                                 () if pair else ('unmapped_pair',)))
    if not cases:
        raise NumericContractError('missing_expected_cases')
    return cases


def _dimensions(entry, prefix):
    result = copy.deepcopy(entry)
    unit = entry.get('length_unit')
    if unit != 'um':
        raise NumericContractError('canonical_dimensions_unit_mismatch', prefix)
    names = ('unit_width_um', 'unit_height_um', 'measured_width_um', 'measured_height_um') if prefix == 'unit' else ('panel_width_um', 'panel_height_um')
    for name in names:
        result[name] = finite(entry[name], name, positive=True)
    return result


def load_dataset(data_dir: Path, *, manifest_path: str = 'manifest.json', mode: str = 'synthetic') -> NumericDataset:
    """Load validated canonical synthetic data; never silently approve G1/G2."""
    if mode not in ('synthetic', 'real'):
        raise NumericContractError('invalid_mode')
    root = Path(data_dir)
    try:
        manifest = json.loads(resolve_path(root, manifest_path).read_text(encoding='utf-8'),
                              parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        if manifest['schema_version'] != 'numeric_v2':
            raise NumericContractError('unsupported_source_contract')
        if type(manifest['is_synthetic']) is not bool:
            raise NumericContractError('invalid_synthetic_marker')
        synthetic = manifest['is_synthetic']
        if mode == 'real':
            if synthetic:
                raise NumericContractError('synthetic_input_in_real_mode')
            raise NumericContractError('real_source_format_unconfirmed', 'G2 adapter acceptance remains pending')
        if not synthetic:
            raise NumericContractError('real_input_requires_approved_adapter')
        if manifest['hash_recipe'] != HASH_RECIPE:
            raise NumericContractError('unsupported_hash_recipe')
        if source_payload_hash(manifest) != manifest['input_hash'] or manifest['source_manifest_hash'] != manifest['input_hash']:
            raise NumericContractError('input_hash_mismatch', 'manifest source payload')
        for relative, info in manifest['files'].items():
            check_hash(resolve_path(root, relative), info['sha256'])
        pairs_path = manifest.get('pairs_path', 'pairs.csv')
        if pairs_path not in manifest['files'] or manifest['density']['path'] not in manifest['files']:
            raise NumericContractError('table_hash_required')
        if manifest['pair_table_hash'] != manifest['files'][pairs_path]['sha256']:
            raise NumericContractError('pair_table_hash_mismatch')
        if manifest['diameters_um'] != list(DIAMETERS_UM):
            raise NumericContractError('unsupported_diameter_grid')
        for gate in ('G1', 'G2', 'G7a', 'G7b'):
            state = manifest['gates'][gate]
            if (state['status'] not in ('pending', 'answered')
                    or (state['status'] == 'pending' and state['answer'] is not None)
                    or (state['status'] == 'answered' and state['answer'] is None)):
                raise NumericContractError('invalid_gate_state', gate)
        frames = _frames(root, manifest['frames'], synthetic)
        pairs = _pairs(root, manifest)
        density = _density(root, manifest)
        cases = _cases(root, manifest, frames, pairs, synthetic)
        unit_domain = _dimensions(manifest['unit_domain'], 'unit')
        panel = _dimensions(manifest['panel'], 'panel')
        if unit_domain['frame_id'] not in frames:
            raise NumericContractError('unknown_unit_frame')
        unit_frame = frames[unit_domain['frame_id']]
        if (unit_domain['measured_width_um'], unit_domain['measured_height_um']) != (
            unit_frame.shape_yx[1] * unit_frame.pixel_pitch_um, unit_frame.shape_yx[0] * unit_frame.pixel_pitch_um):
            raise NumericContractError('measured_domain_mismatch')
        if panel.get('rounding_policy') not in ('floor', 'half_up_round'):
            raise NumericContractError('invalid_panel_rounding_policy')
        if manifest['gates']['G2']['status'] == 'pending' and any(
            unit_domain.get(name) is not None for name in ('unit_area_basis', 'conversion_policy', 'divisor')
        ):
            raise NumericContractError('unconfirmed_unit_conversion')
        # No unit conversion in T2; an explicitly confirmed policy is retained as metadata only.
        unit_domain.update(status='pending', reason='unit_area_basis_unconfirmed')
        panel['unit_count'] = None
        panel['reason'] = 'unit_area_basis_unconfirmed'
        return NumericDataset(cases, pairs, frames, density, unit_domain, panel,
                              copy.deepcopy(manifest['gates']), copy.deepcopy(manifest),
                              ('density_contract_unconfirmed', 'unit_area_basis_unconfirmed'))
    except NumericContractError:
        raise
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise NumericContractError('malformed_numeric_input', str(exc)) from exc


load_numeric_dataset = load_dataset
