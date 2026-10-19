"""numeric_v2 실행 경로. 학습이나 legacy loader를 호출하지 않는다."""
from __future__ import annotations
import json
from pathlib import Path


def _real_source(cfg, section):
    """Return a validated real_hotspot_v1 manifest, or None for numeric_v2."""
    from simcompare.adapters.real_hotspot import CONTRACT_VERSION, load_real_manifest
    from simcompare.numeric_schema import NumericContractError
    from simcompare.numeric_images import resolve_path
    if not isinstance(section, dict) or not isinstance(section.get('data_dir'), str):
        raise NumericContractError('numeric_input_required')
    root = _numeric_path(cfg, section['data_dir'])
    manifest_path = section.get('manifest_path', 'manifest.json')
    candidate = resolve_path(root, manifest_path)
    try:
        header = json.loads(candidate.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if header.get('schema_version') != CONTRACT_VERSION:
        return None
    if cfg.get('mode') != 'real':
        raise NumericContractError('real_source_in_synthetic_mode')
    return root, load_real_manifest(root, manifest_path)


def _configured_real(cfg, section, real):
    from simcompare.adapters.real_hotspot import configured_dataset
    root, manifest = real
    assignment = section.get('condition_assignment') if isinstance(section, dict) else None
    if assignment is None:
        return None
    dataset = configured_dataset(root, manifest, assignment)
    return root, dataset


def _write_inspection(output, prefix, payload, rows):
    import csv
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / f'{prefix}.json'
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    csv_path = output / f'{prefix}.csv'
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with csv_path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or ['status'])
        writer.writeheader()
        writer.writerows(rows or [{'status': 'no_records'}])
    return [str(json_path), str(csv_path)]


def _real_validation(cfg, section, output):
    _, manifest = section
    rows, reasons = [], set()
    for case in manifest['cases']:
        for record in case['records']:
            status = record['input_status']
            reason = record.get('reason')
            value = record.get('png_area_um2') if status == 'observed' else None
            if reason: reasons.add(reason)
            if record.get('csv_status') not in (None, 'consistent'):
                reasons.add('csv_'+record['csv_status'])
            rows.append({'case_id': case['case_name'], 'diameter_um': record['diameter_um'],
                         'failure_type': case['failure_type'], 'input_status': status,
                         'area_um2': value, 'csv_area_um2': (record.get('csv_area') or {}).get('value'),
                         'area_difference_um2': record.get('area_difference_um2'),
                         'area_tolerance_um2': record.get('area_tolerance_um2'),
                         'classification_status': case['classification_status'], 'reason': reason})
    reasons.update(d.get('status', '') for d in manifest.get('diagnostics', []))
    payload = {'schema_version': 'real_hotspot_validation_v1', 'source_contract_version': manifest['schema_version'],
               'is_synthetic': False, 'status': 'partial', 'reasons': sorted(filter(None, reasons)),
               'raw_observations_status': 'available' if any(r['area_um2'] is not None for r in rows) else 'unavailable',
               'classified_comparison_status': 'unavailable', 'map_status': 'requires_explicit_condition_assignment',
               'physical_metadata': manifest['physical_metadata'], 'expected_case_coverage_status': manifest['expected_case_coverage_status'],
               'diagnostics': manifest['diagnostics'], 'records': rows,
               'source_payload_hash': manifest['source_payload_hash']}
    paths = _write_inspection(output, 'validation', payload, rows)
    return {'status': 'partial', 'reasons': payload['reasons'], 'outputs': paths, 'exit_code': 3}


def load_numeric_config(path):
    import yaml
    from simcompare.numeric_schema import NumericContractError, text
    import hashlib
    path = Path(path).resolve()
    raw = path.read_bytes()
    try:
        cfg = yaml.safe_load(raw.decode('utf-8'))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise NumericContractError('invalid_numeric_config',str(exc)) from exc
    if not isinstance(cfg,dict) or cfg.get('schema_version') != 'numeric_config_v1':
        raise NumericContractError('unsupported_numeric_config', 'numeric_config_v1 설정을 사용하세요; legacy export의 numeric 변환은 G2 pending입니다')
    allowed = {'schema_version','mode','output_dir','reference','target','map','review_rois'}
    if set(cfg)-allowed or cfg.get('mode') not in ('synthetic','real') or not isinstance(cfg.get('output_dir'),str):
        raise NumericContractError('invalid_numeric_config')
    text(cfg['output_dir'],'output_dir')
    cfg['_config_dir'] = path.parent
    cfg['_config_path'] = path
    cfg['_config_sha256'] = hashlib.sha256(raw).hexdigest()
    return cfg


def _numeric_path(cfg, value):
    from simcompare.numeric_schema import text
    path = Path(text(value,'config path'))
    return path if path.is_absolute() else cfg['_config_dir']/path


def _numeric_input(cfg, section):
    from simcompare.adapters.numeric import load_dataset as load_numeric
    from simcompare.numeric_schema import NumericContractError
    if not isinstance(section,dict):
        raise NumericContractError('numeric_input_required')
    allowed = {'data_dir','manifest_path','product_id','asset_manifest','density_contracts','unit_bases','panel'}
    if set(section)-allowed:
        raise NumericContractError('unknown_numeric_input_fields')
    root = _numeric_path(cfg,section.get('data_dir'))
    dataset = load_numeric(root,manifest_path=section.get('manifest_path','manifest.json'),mode=cfg['mode'])
    products = {c.identity.product_id for c in dataset.cases}
    if len(products) != 1 or section.get('product_id') not in products:
        raise NumericContractError('single_declared_product_required')
    return root,dataset


def _numeric_rates(dataset, section):
    from simcompare.density import DensityContract, adapt_density
    from simcompare.linepixel import PanelSpec, UnitAreaBasis, compute_areas, compute_rates
    from simcompare.numeric_schema import NumericContractError
    def declarations(name):
        values=section.get(name,[])
        if not isinstance(values,list) or any(not isinstance(v,dict) for v in values):
            raise NumericContractError('invalid_numeric_config', name+'는 객체 목록이어야 합니다')
        return values
    contracts={}
    for raw in declarations('density_contracts'):
        try:contract=DensityContract(**raw)
        except TypeError as exc:raise NumericContractError('invalid_numeric_config','density_contracts 필드를 확인하세요') from exc
        if contract.distribution_id in contracts:raise NumericContractError('duplicate_density_contract')
        contracts[contract.distribution_id]=contract
    distributions={}
    for selected in sorted({r.distribution_id for r in dataset.density}):
        distribution=adapt_density([r for r in dataset.density if r.distribution_id==selected],contract=contracts.get(selected))
        if distribution.process_layer in distributions:raise NumericContractError('ambiguous_layer_density_distribution')
        distributions[distribution.process_layer]=distribution
    if set(contracts)-{r.distribution_id for r in dataset.density}:raise NumericContractError('unused_density_contract')
    bases={}
    domain=dataset.unit_domain
    for raw in declarations('unit_bases'):
        try:basis=UnitAreaBasis(**{**raw,'unit_size_um':tuple(raw['unit_size_um'])})
        except (KeyError,TypeError) as exc:raise NumericContractError('invalid_numeric_config','unit_bases의 frame/unit_size_um/evidence를 확인하세요') from exc
        key=(section['product_id'],basis.frame_id)
        if key in bases:raise NumericContractError('duplicate_unit_basis')
        declared=(domain['unit_width_um'],domain['unit_height_um'])
        measured=(domain['measured_width_um'],domain['measured_height_um'])
        if (domain['frame_id']!=basis.frame_id or declared!=tuple(basis.unit_size_um) or declared!=measured
                or domain.get('domain_shape_units_yx',[1,1])!=[1,1]):
            from dataclasses import replace
            basis=replace(basis,basis='manifest_unit_domain_mismatch')
        bases[key]=basis
    raw_panel=section.get('panel')
    if raw_panel is not None and not isinstance(raw_panel,dict):
        raise NumericContractError('invalid_numeric_config','panel은 치수와 length_unit을 가진 객체여야 합니다')
    try:panels={} if raw_panel is None else {section['product_id']:PanelSpec(**raw_panel)}
    except TypeError as exc:raise NumericContractError('invalid_numeric_config','panel 필드를 확인하세요') from exc
    return compute_rates(compute_areas(dataset),distributions,unit_bases=bases,panels=panels)


def run_numeric_compare(cfg, out=None):
    from simcompare.linepixel import compare_rates
    from simcompare.report import comparison_report, write_numeric_report
    reference_real = _real_source(cfg, cfg.get('reference'))
    target_real = _real_source(cfg, cfg.get('target'))
    if reference_real is not None or target_real is not None:
        if reference_real is None or target_real is None:
            from simcompare.numeric_schema import NumericContractError
            raise NumericContractError('mixed_real_and_numeric_source_formats')
        if (cfg['reference'].get('condition_assignment') is not None and
                cfg['target'].get('condition_assignment') is not None):
            from simcompare.linepixel import compute_areas
            def area_rows(side, result):
                rows = []
                for group in result.groups.values():
                    row = {'side': side, 'group': group.key.__dict__, 'status': group.union.status,
                           'union_um2': group.union.value, 'partial_union_um2': group.union.partial_value,
                           'line_um2': group.line.area.value, 'pixel_effective_um2': group.pixel.area.value,
                           'transfer_um2': group.transfer.value, 'unmapped_pair_count': group.coverage.unmapped,
                           'missing_count': group.coverage.missing, 'is_synthetic': False}
                    for source in group.source_cases:
                        raw_records = source.raw.get('records', []) if isinstance(source.raw, dict) else []
                        raw_record = next((r for r in raw_records if r.get('diameter_um') == source.diameter_um), {})
                        csv_area = raw_record.get('csv_area') or {}
                        rows.append({**row, 'case_id': source.identity.case_id, 'diameter_um': source.diameter_um,
                                     'case_area_um2': source.measurement.ca_um2,
                                     'case_partial_area_um2': source.measurement.partial_value,
                                     'input_status': source.input_status,
                                     'csv_status': raw_record.get('csv_status'),
                                     'csv_area_um2': csv_area.get('value'),
                                     'csv_raw_value': csv_area.get('raw_area'),
                                     'area_difference_um2': raw_record.get('area_difference_um2'),
                                     'area_tolerance_um2': raw_record.get('area_tolerance_um2'),
                                     'reasons': list(source.measurement.reasons)})
                return rows
            reference_configured = _configured_real(cfg, cfg['reference'], reference_real)
            left = compute_areas(reference_configured[1]) if reference_configured[1].cases else None
            left_payload = left.to_dict() if left is not None else None
            left_rows = area_rows('reference', left) if left is not None else []
            left_unresolved = reference_configured[1].provenance.get('unresolved_same_name_cases', [])
            del reference_configured, left
            target_configured = _configured_real(cfg, cfg['target'], target_real)
            right = compute_areas(target_configured[1]) if target_configured[1].cases else None
            right_payload = right.to_dict() if right is not None else None
            right_rows = area_rows('target', right) if right is not None else []
            right_unresolved = target_configured[1].provenance.get('unresolved_same_name_cases', [])
            del target_configured, right
            if left_payload is not None or right_payload is not None:
                rows = left_rows+right_rows
                unresolved_same = left_unresolved or right_unresolved
                payload = {'schema_version': 'real_hotspot_comparison_v1', 'source_contract_version': 'real_hotspot_v1',
                           'is_synthetic': False, 'status': 'partial',
                           'reasons': ['density_contract_unconfirmed', 'unit_area_basis_unconfirmed',
                                       'same_name_cases_unresolved' if unresolved_same else ''],
                           'raw_case_areas_status': 'available', 'classified_line_pixel_areas_status': 'partial' if rows else 'unavailable',
                           'weighted_rates_status': 'unavailable', 'deltas_status': 'unavailable',
                           'reference': left_payload, 'target': right_payload, 'records': rows}
                payload['reasons'] = sorted(filter(None, payload['reasons']))
                output = Path(out) if out is not None else _numeric_path(cfg, cfg['output_dir'])
                paths = _write_inspection(output, 'comparison', payload, rows)
                return {'status': 'partial', 'reasons': payload['reasons'], 'outputs': paths, 'exit_code': 3}
        output = Path(out) if out is not None else _numeric_path(cfg, cfg['output_dir'])
        rows = []
        for side, imported in (('reference', reference_real[1]), ('target', target_real[1])):
            for case in imported['cases']:
                for record in case['records']:
                    rows.append({'side': side, 'case_id': case['case_name'], 'diameter_um': record['diameter_um'],
                                 'input_status': record['input_status'], 'raw_area_um2': record['png_area_um2'],
                                 'csv_area_um2': (record.get('csv_area') or {}).get('value'),
                                 'csv_raw_value': (record.get('csv_area') or {}).get('raw_area'),
                                 'csv_raw_diameter': (record.get('csv_area') or {}).get('raw_diameter'),
                                 'csv_status': record.get('csv_status'),
                                 'area_difference_um2': record.get('area_difference_um2'),
                                 'area_tolerance_um2': record.get('area_tolerance_um2'),
                                 'classification_status': case['classification_status'],
                                 'reason': record.get('reason') or record.get('csv_status')})
        payload = {'schema_version': 'real_hotspot_comparison_v1', 'source_contract_version': 'real_hotspot_v1',
                   'is_synthetic': False, 'status': 'partial', 'reasons': ['explicit_condition_assignments_required',
                   'pair_classification_table_required', 'density_and_unit_metadata_missing'],
                   'raw_case_areas_status': 'available', 'classified_comparison_status': 'unavailable',
                   'line_pixel_rates_status': 'unavailable',
                   'deltas_status': 'unavailable', 'records': rows,
                   'reference_source_hash': reference_real[1]['source_payload_hash'],
                   'target_source_hash': target_real[1]['source_payload_hash']}
        if any(case['diagnostics'] for source in (reference_real[1], target_real[1]) for case in source['cases']):
            payload['reasons'] = sorted(set((*payload['reasons'], 'same_name_endpoints_unresolved')))
        paths = _write_inspection(output, 'comparison', payload, rows)
        return {'status': 'partial', 'reasons': payload['reasons'], 'outputs': paths, 'exit_code': 3}
    _,reference = _numeric_input(cfg,cfg.get('reference'))
    _,target = _numeric_input(cfg,cfg.get('target'))
    rr = _numeric_rates(reference,cfg['reference'])
    tr = _numeric_rates(target,cfg['target'])
    deltas = compare_rates(rr,tr,reference_product_id=cfg['reference']['product_id'],
                           target_product_id=cfg['target']['product_id'])
    payload,tables = comparison_report(rr,tr,deltas)
    output = Path(out) if out is not None else _numeric_path(cfg,cfg['output_dir'])
    paths = write_numeric_report(output,'comparison',payload,tables)
    return {'status':payload['status'],'reasons':payload['reasons'],'outputs':paths,'exit_code':0 if payload['status']=='available' else 3}


def _map_spec(cfg):
    from simcompare.numeric_schema import NumericContractError
    spec = cfg.get('map')
    if not isinstance(spec,dict) or set(spec)-{'input','diameter_um','min_count','per_cell_text'}:
        raise NumericContractError('numeric_map_config_required')
    if type(spec.get('per_cell_text',False)) is not bool:
        raise NumericContractError('invalid_numeric_config','map.per_cell_text는 boolean이어야 합니다')
    from .numeric_schema import DIAMETERS_UM
    if spec.get('diameter_um',5) not in DIAMETERS_UM:
        raise NumericContractError('invalid_representative_diameter', '지름은 1..30, 40, 50 um 중 하나여야 합니다')
    return spec


def _numeric_map(cfg):
    from simcompare.electrode_map import compute_electrode_maps
    spec = _map_spec(cfg)
    root,dataset = _numeric_input(cfg,spec.get('input'))
    maps = compute_electrode_maps(dataset,diameter_um=spec.get('diameter_um',5),min_count=spec.get('min_count'))
    return root,dataset,maps


def run_numeric_map(cfg, out=None):
    from simcompare.report import map_report, write_numeric_report
    spec = cfg.get('map')
    if isinstance(spec, dict) and isinstance(spec.get('input'), dict):
        real = _real_source(cfg, spec['input'])
        if real is not None:
            configured = _configured_real(cfg, spec['input'], real)
            if configured is not None:
                from simcompare.electrode_map import compute_electrode_maps
                from simcompare.report import map_report, write_map_arrays, write_numeric_report
                dataset = configured[1]
                if not dataset.cases:
                    rows = [{'case_id': name, 'status': 'unresolved_same_name_endpoints'}
                            for name in dataset.provenance.get('unresolved_same_name_cases', [])]
                    payload = {'schema_version': 'real_hotspot_electrode_map_v1', 'is_synthetic': False,
                               'status': 'unavailable', 'reasons': ['same_name_endpoints_unresolved'],
                               'maps': [], 'source_cases': rows,
                               'source_payload_hash': dataset.provenance.get('source_payload_hash')}
                    paths = _write_inspection(Path(out) if out is not None else _numeric_path(cfg, cfg['output_dir']),
                                              'electrode_map', payload, rows)
                    return {'status': 'unavailable', 'reasons': payload['reasons'], 'outputs': paths, 'exit_code': 3}
                maps = compute_electrode_maps(dataset, diameter_um=spec.get('diameter_um', 5),
                                              min_count=spec.get('min_count'))
                payload, tables = map_report(maps, per_cell=spec.get('per_cell_text', False))
                payload['is_synthetic'] = False
                if dataset.provenance.get('unresolved_same_name_cases'):
                    payload['reasons'] = sorted(set((*payload.get('reasons', ()), 'same_name_cases_unresolved')))
                    payload['status'] = 'partial'
                output = Path(out) if out is not None else _numeric_path(cfg, cfg['output_dir'])
                output.mkdir(parents=True, exist_ok=True)
                for old in output.glob('electrode_map*'):
                    if old.is_file() and old.suffix in ('.json', '.html', '.csv', '.npz', '.png'):
                        old.unlink()
                from simcompare.heatmap import write_heatmaps
                payload['heatmaps'] = write_heatmaps(output, maps)
                paths = write_map_arrays(output, maps, payload)
                paths += write_numeric_report(output, 'electrode_map', payload, tables)
                paths += [str(output/ref[k]) for ref in payload['heatmaps'] for k in ('png', 'sidecar') if ref[k] is not None]
                return {'status': payload['status'], 'reasons': payload.get('reasons', []), 'outputs': paths, 'exit_code': 3}
            output = Path(out) if out is not None else _numeric_path(cfg, cfg['output_dir'])
            manifest = real[1]
            rows = [{'case_id': case['case_name'], 'electrode_a': case['endpoints'][0]['electrode_id'],
                     'electrode_b': case['endpoints'][1]['electrode_id'], 'status': case['map_status'],
                     'reason': 'explicit_condition_assignment_required'} for case in manifest['cases']]
            payload = {'schema_version': 'real_hotspot_electrode_map_v1', 'is_synthetic': False,
                       'status': 'unavailable', 'reasons': ['explicit_condition_assignment_required'], 'maps': [], 'source_cases': rows,
                       'source_payload_hash': manifest['source_payload_hash']}
            paths = _write_inspection(output, 'electrode_map', payload, rows)
            return {'status': 'unavailable', 'reasons': payload['reasons'], 'outputs': paths, 'exit_code': 3}
    _,dataset,maps = _numeric_map(cfg)
    if any(m.status!='available' for m in (*maps.layers,*maps.summaries)):
        from .numeric_schema import NumericContractError
        raise NumericContractError('electrode_map_incomplete', ', '.join(sorted({r for m in (*maps.layers,*maps.summaries) for r in m.reasons})))
    payload,tables = map_report(maps,per_cell=cfg['map'].get('per_cell_text',False))
    output = Path(out) if out is not None else _numeric_path(cfg,cfg['output_dir'])
    for old in output.glob('electrode_map*'):
        if old.is_file() and old.suffix in ('.json','.html','.csv','.npz','.png'):old.unlink()
    from simcompare.heatmap import write_heatmaps
    heatmaps = write_heatmaps(output, maps)
    payload['heatmaps'] = heatmaps
    payload['is_synthetic']=bool(dataset.provenance.get('is_synthetic',False))
    from .report import write_map_arrays
    paths=write_map_arrays(output,maps,payload)
    paths += write_numeric_report(output,'electrode_map',payload,tables)
    paths += [str(output/ref[k]) for ref in heatmaps for k in ('png','sidecar') if ref[k] is not None]
    usable = any(m.coverage['source_coverage_status'] in ('available','partial') for m in maps.layers)
    return {'status':payload['status'],'reasons':payload['reasons'],'outputs':paths,'exit_code':0 if payload['status']=='available' else 3 if usable else 2}


def run_numeric_validate(cfg, out=None):
    from simcompare.adapters.real_hotspot import configured_dataset
    from simcompare.measurement import measure_full_mask
    from simcompare.numeric_schema import current_gate_decisions
    from simcompare.report import write_numeric_report
    sections = [(name, cfg[name]) for name in ('reference','target') if name in cfg]
    if 'map' in cfg:
        sections.append(('map.input', cfg['map']['input']))
    if not sections:
        from simcompare.numeric_schema import NumericContractError
        raise NumericContractError('numeric_input_required')
    records,seen,reasons,diagnostics = [],set(),set(),[]
    input_aliases = {}
    saw_real = False
    real_physical_metadata = None
    expected_coverage = None
    for label, section in sections:
        try:
            real = _real_source(cfg, section)
            if real is not None:
                saw_real = True
                root, manifest = real
                if real_physical_metadata is None:
                    real_physical_metadata = manifest['physical_metadata']
                    expected_coverage = manifest['expected_case_coverage_status']
                assignment = section.get('condition_assignment')
                if assignment is not None:
                    configured_dataset(root, manifest, assignment)
                source_key = str(root.resolve()) + '::' + str(section.get('manifest_path', 'manifest.json'))
                input_aliases.setdefault(source_key, []).append(label)
                if source_key not in seen:
                    seen.add(source_key)
                    # Raw manifest rows remain visible once per physical input; labels
                    # preserve which config sections refer to each source.
                    for case in manifest['cases']:
                        for record in case['records']:
                            records.append({'input': label, 'case_id': case['case_name'],
                                'diameter_um': record['diameter_um'], 'failure_type': case['failure_type'],
                                'input_status': record['input_status'], 'area_um2': record.get('png_area_um2'),
                                'csv_area_um2': (record.get('csv_area') or {}).get('value'),
                                'csv_raw_value': (record.get('csv_area') or {}).get('raw_area'),
                                'csv_raw_diameter': (record.get('csv_area') or {}).get('raw_diameter'),
                                'csv_status': record.get('csv_status'),
                                'area_difference_um2': record.get('area_difference_um2'),
                                'area_tolerance_um2': record.get('area_tolerance_um2'),
                                'geometry_status': case.get('geometry_status'),
                                'status': 'partial' if record.get('csv_status') == 'mismatch' or case.get('geometry_status') != 'confirmed' else record['input_status'],
                                'reason': record.get('reason') or record.get('csv_status')})
                            if record.get('reason'):
                                reasons.add(record['reason'])
                            if record.get('csv_status') not in (None, 'consistent'):
                                reasons.add('csv_'+record['csv_status'])
                    reasons.update(d.get('status', '') for d in manifest.get('diagnostics', []))
                continue
            root,dataset = _numeric_input(cfg,section)
            source_key = str(root.resolve()) + '::' + str(section.get('manifest_path', 'manifest.json'))
            input_aliases.setdefault(source_key, []).append(label)
            if source_key in seen:
                continue
            seen.add(source_key)
            for case in dataset.cases:
                for d,record in sorted(case.records.items()):
                    measured = measure_full_mask(record)
                    record_reasons = sorted(set((*measured.reasons,*case.reasons)))
                    reasons.update(record_reasons)
                    records.append({'input': label,'product_id':case.identity.product_id,'case_id':case.identity.case_id,
                                    'diameter_um':d,'failure_type':case.failure_type,'input_status':record.input_status,
                                    'value':measured.ca_um2,'partial_value':measured.partial_value,'unit':'um^2','status':measured.status,
                                    'reasons':record_reasons,'measurement':measured})
            reasons.update(dataset.reasons)
        except Exception as exc:
            if cfg.get('mode') != 'real':
                raise
            message = f'{label}: {type(exc).__name__}: {exc}'
            diagnostics.append({'input': label, 'status': 'invalid', 'error': str(exc)})
            reasons.add('invalid_configured_input')
            records.append({'input': label, 'status': 'unavailable', 'reason': message})
    payload = {'schema_version':'numeric_validation_v1','status':'partial','reasons':sorted(reasons),'records':records,
               'diagnostics':diagnostics,'gates':current_gate_decisions(),
               'input_aliases': {key: values for key, values in sorted(input_aliases.items())}}
    if saw_real:
        payload['is_synthetic'] = False
        payload['physical_metadata'] = real_physical_metadata
        payload['expected_case_coverage_status'] = expected_coverage
        payload['raw_observations_status'] = 'available' if any(r.get('area_um2') is not None for r in records) else 'unavailable'
        payload['classified_comparison_status'] = 'unavailable'
        payload['map_status'] = 'requires_explicit_condition_assignment'
    output = Path(out) if out is not None else _numeric_path(cfg,cfg['output_dir'])
    paths = write_numeric_report(output,'validation',payload,{'records':records})
    return {'status':'partial','reasons':sorted(reasons),'outputs':paths,'exit_code':3}


_ELECTRODE_SEMANTICS = ('binary', 'binary_zero_is_marked')


def _json_unique(path):
    from simcompare.roi_export import _json_load
    return _json_load(path)


def _electrode_evidence(root, section):
    """real_hotspot_v1 전극 asset 수용 증거를 검사한다. 극성은 추측하지 않는다.

    반환: None(증거 없음 → 기존 unavailable 단락), ('blocked', reasons), 또는 ('accepted', manifest).
    hash·binding 불일치는 NumericContractError(이름 있는 오류)다.
    """
    from simcompare.adapters.electrode_png import SOURCE_KIND_CONTRACT
    from simcompare.numeric_images import check_hash, resolve_path
    from simcompare.numeric_schema import NumericContractError
    asset_ref, record_ref = section.get('asset_manifest'), section.get('electrode_import_record')
    if asset_ref is None or record_ref is None or section.get('condition_assignment') is None:
        return None
    for name, ref in (('asset_manifest', asset_ref), ('electrode_import_record', record_ref)):
        if (not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}
                or not all(isinstance(ref[k], str) and ref[k] for k in ref)):
            raise NumericContractError('invalid_numeric_config', name + '는 {path, sha256} 객체여야 합니다')
    record_path = resolve_path(root, record_ref['path'])
    check_hash(record_path, record_ref['sha256'])
    manifest_path = resolve_path(root, asset_ref['path'])
    check_hash(manifest_path, asset_ref['sha256'])
    try:
        record, manifest = _json_unique(record_path), _json_unique(manifest_path)
    except (OSError, ValueError) as exc:
        raise NumericContractError('malformed_asset_manifest', str(exc)) from exc
    if (not isinstance(record, dict) or record.get('schema_version') != 'electrode_asset_import_record_v1'
            or not isinstance(manifest, dict) or manifest.get('schema_version') != 'review_assets_v1'):
        raise NumericContractError('electrode_import_record_invalid')
    import_info = manifest.get('electrode_asset_import')
    if not isinstance(import_info, dict):
        return None
    if record.get('polarity_basis') is None or import_info.get('polarity_basis') is None:
        return None
    if (record.get('manifest_sha256') != asset_ref['sha256'] or import_info.get('record') != record_path.name
            or record.get('dataset_input_hash') != manifest.get('source_manifest_hash')
            or record.get('source_kind') != import_info.get('source_kind')):
        raise NumericContractError('electrode_import_record_mismatch')
    contract = SOURCE_KIND_CONTRACT.get(record['source_kind']) if isinstance(record['source_kind'], str) else None
    if contract is None or record['polarity_basis'] != contract['polarity_basis'] \
            or import_info['polarity_basis'] != contract['polarity_basis']:
        return 'blocked', ['electrode_polarity_unspecified']
    refs = []
    try:
        for entry in manifest['cases']:
            assets = entry['assets']
            refs += [item['asset'] for item in assets['layouts']] + [assets['valid'], assets['cnt'], *assets.get('via', [])]
    except (KeyError, TypeError):
        raise NumericContractError('source_asset_manifest_invalid') from None
    if not refs or any(not isinstance(r, dict) or r.get('value_semantics') not in _ELECTRODE_SEMANTICS for r in refs):
        return 'blocked', ['electrode_polarity_unspecified']
    return 'accepted', manifest


def _real_roi_export(cfg, out, real, spec):
    """실자료 export: configured_dataset과 electrode-map 영역, 수용된 review_assets_v1만 쓴다."""
    from simcompare.electrode_map import compute_electrode_maps
    from simcompare.numeric_schema import NumericContractError
    from simcompare.roi_export import export_review_rois
    evidence = _electrode_evidence(real[0], spec['input'])
    if evidence is None:
        return None
    if evidence[0] == 'blocked':
        payload = {'schema_version': 'review_roi_v1', 'producer_commit': None,
                   'source_manifest_hash': real[1]['source_payload_hash'], 'assets_root': str(real[0]),
                   'rois': [], 'excluded_records': [], 'status': 'blocked', 'reasons': evidence[1]}
        paths = _write_inspection(Path(out), 'review_rois', payload, [])
        return {'status': 'blocked', 'reasons': evidence[1], 'outputs': paths, 'exit_code': 3}
    _map_spec(cfg)
    root, dataset = _configured_real(cfg, spec['input'], real)
    if evidence[1].get('source_manifest_hash') != dataset.provenance['input_hash']:
        raise NumericContractError('source_asset_manifest_identity_mismatch')
    if not dataset.cases:
        payload = {'schema_version': 'review_roi_v1', 'producer_commit': None,
                   'source_manifest_hash': dataset.provenance.get('source_payload_hash'), 'assets_root': str(root),
                   'rois': [], 'excluded_records': [], 'status': 'unavailable',
                   'reasons': ['same_name_endpoints_unresolved']}
        paths = _write_inspection(Path(out), 'review_rois', payload, [])
        return {'status': 'unavailable', 'reasons': payload['reasons'], 'outputs': paths, 'exit_code': 3}
    maps = compute_electrode_maps(dataset, diameter_um=spec.get('diameter_um', 5), min_count=spec.get('min_count'))
    return export_review_rois(dataset, root, out, maps=maps, selections=cfg.get('review_rois', {}),
                              asset_manifest=spec['input'].get('asset_manifest'))


def run_numeric_roi_export(cfg, out):
    if isinstance(cfg.get('map'), dict) and isinstance(cfg['map'].get('input'), dict):
        real = _real_source(cfg, cfg['map']['input'])
        if real is not None:
            accepted = _real_roi_export(cfg, out, real, cfg['map'])
            if accepted is not None:
                return accepted
            from simcompare.numeric_schema import NumericContractError
            payload = {'schema_version': 'review_roi_v1', 'producer_commit': None,
                       'source_manifest_hash': real[1]['source_payload_hash'], 'assets_root': str(real[0]),
                       'rois': [], 'excluded_records': [], 'status': 'unavailable',
                       'reasons': ['electrode_polarity_unknown', 'source_layout_assets_unconfirmed']}
            paths = _write_inspection(Path(out), 'review_rois', payload, [])
            return {'status': 'unavailable', 'reasons': payload['reasons'], 'outputs': paths, 'exit_code': 3}
    from simcompare.roi_export import export_review_rois
    root,dataset,maps = _numeric_map(cfg)
    return export_review_rois(dataset,root,out,maps=maps,selections=cfg.get('review_rois',{}),
                              asset_manifest=cfg['map']['input'].get('asset_manifest'))


def _dataset_for(cfg, section, label):
    """numeric_v2 dataset, or the explicitly configured real_hotspot_v1 dataset."""
    from simcompare.numeric_schema import NumericContractError
    real = _real_source(cfg, section)
    if real is not None:
        configured = _configured_real(cfg, section, real)
        if configured is None:
            raise NumericContractError('explicit_condition_assignments_required', label)
        return configured[1]
    return _numeric_input(cfg, section)[1]


def run_numeric_local_delta(cfg, out, *, correspondence, window_sizes_um, jitter_um=None, sort_diameter_um=None,
                            compact_json=False, config_path=None):
    """compare-windows: same physical window around corresponding positions, b − a."""
    from simcompare.correspondence import bind_pairs, load_correspondence
    from simcompare.local_delta import compute_local_delta, correspondence_hash, write_local_delta
    from simcompare.local_delta_report import write_local_delta_html
    from simcompare.numeric_schema import NumericContractError
    real = [_real_source(cfg, cfg.get(name)) is not None for name in ('reference', 'target')]
    if any(real) and not all(real):
        raise NumericContractError('mixed_real_and_numeric_source_formats')
    reference = _dataset_for(cfg, cfg.get('reference'), 'reference')
    target = _dataset_for(cfg, cfg.get('target'), 'target')
    payload = load_correspondence(correspondence)
    pairs = bind_pairs(payload, reference, target)
    result = compute_local_delta(pairs, reference, target, window_sizes_um=window_sizes_um, jitter_um=jitter_um,
                                 sort_diameter_um=sort_diameter_um,
                                 provenance={'correspondence_path': str(Path(correspondence).resolve()),
                                             'correspondence_sha256': correspondence_hash(correspondence),
                                             'correspondence_producer': payload['producer'],
                                             'correspondence_warnings': payload['warnings'],
                                             'config_path': str(cfg.get('_config_path', config_path or '')) or None,
                                             'config_sha256': cfg.get('_config_sha256')})
    output = Path(out) if out is not None else _numeric_path(cfg, cfg['output_dir'])
    paths = write_local_delta(output, result, compact=compact_json)
    paths += write_local_delta_html(output, result, cfg=cfg, reference=reference, target=target)
    return {'status': result.status, 'reasons': list(result.reasons), 'outputs': paths,
            'exit_code': 0 if result.status == 'available' else 3}

