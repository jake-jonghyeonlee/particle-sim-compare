"""numeric_v2 실행 경로. 학습이나 legacy loader를 호출하지 않는다."""
from __future__ import annotations
import json
from pathlib import Path


def load_numeric_config(path):
    import yaml
    from simcompare.numeric_schema import NumericContractError, text
    path = Path(path).resolve()
    try:
        cfg = yaml.safe_load(path.read_text(encoding='utf-8'))
    except yaml.YAMLError as exc:
        raise NumericContractError('invalid_numeric_config',str(exc)) from exc
    if not isinstance(cfg,dict) or cfg.get('schema_version') != 'numeric_config_v1':
        raise NumericContractError('unsupported_numeric_config', 'numeric_config_v1 설정을 사용하세요; legacy export의 numeric 변환은 G2 pending입니다')
    allowed = {'schema_version','mode','output_dir','reference','target','map','review_rois'}
    if set(cfg)-allowed or cfg.get('mode') not in ('synthetic','real') or not isinstance(cfg.get('output_dir'),str):
        raise NumericContractError('invalid_numeric_config')
    text(cfg['output_dir'],'output_dir')
    cfg['_config_dir'] = path.parent
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
    contracts = {}
    for raw in section.get('density_contracts',[]):
        contract = DensityContract(**raw)
        if contract.distribution_id in contracts:
            raise NumericContractError('duplicate_density_contract')
        contracts[contract.distribution_id] = contract
    distributions = {}
    selected = dataset.provenance['density']['distribution_id']
    # 원천이 지정한 distribution을 사용한다. layer별 다중 분포를 임의 합산하지 않는다.
    rows = [r for r in dataset.density if r.distribution_id == selected]
    distribution = adapt_density(rows,contract=contracts.get(selected))
    distributions[distribution.process_layer] = distribution
    if set(contracts)-{selected}:
        raise NumericContractError('unused_density_contract')
    bases = {}
    for raw in section.get('unit_bases',[]):
        basis = UnitAreaBasis(**{**raw,'unit_size_um':tuple(raw['unit_size_um'])})
        key = (section['product_id'],basis.frame_id)
        if key in bases:
            raise NumericContractError('duplicate_unit_basis')
        bases[key] = basis
    panels = {} if section.get('panel') is None else {section['product_id']:PanelSpec(**section['panel'])}
    return compute_rates(compute_areas(dataset),distributions,unit_bases=bases,panels=panels)


def run_numeric_compare(cfg, out=None):
    from simcompare.linepixel import compare_rates
    from simcompare.report import comparison_report, write_numeric_report
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


def _numeric_map(cfg):
    from simcompare.electrode_map import compute_electrode_maps
    from simcompare.numeric_schema import NumericContractError
    spec = cfg.get('map')
    if not isinstance(spec,dict) or set(spec)-{'input','diameter_um','min_count'}:
        raise NumericContractError('numeric_map_config_required')
    root,dataset = _numeric_input(cfg,spec.get('input'))
    maps = compute_electrode_maps(dataset,diameter_um=spec.get('diameter_um',5),min_count=spec.get('min_count'))
    return root,dataset,maps


def run_numeric_map(cfg, out=None):
    from simcompare.report import map_report, write_numeric_report
    _,_,maps = _numeric_map(cfg)
    payload,tables = map_report(maps)
    output = Path(out) if out is not None else _numeric_path(cfg,cfg['output_dir'])
    from simcompare.heatmap import write_heatmaps
    heatmaps = write_heatmaps(output, maps)
    payload['heatmaps'] = heatmaps
    paths = write_numeric_report(output,'electrode_map',payload,tables)
    paths += [str(output/ref[k]) for ref in heatmaps for k in ('png','sidecar') if ref[k] is not None]
    usable = any(m.coverage['source_coverage_status'] in ('available','partial') for m in maps.layers)
    return {'status':payload['status'],'reasons':payload['reasons'],'outputs':paths,'exit_code':0 if payload['status']=='available' else 3 if usable else 2}


def run_numeric_validate(cfg, out=None):
    from simcompare.measurement import measure_full_mask
    from simcompare.numeric_schema import current_gate_decisions
    from simcompare.report import write_numeric_report
    sections = [cfg[name] for name in ('reference','target') if name in cfg]
    if 'map' in cfg:
        sections.append(cfg['map']['input'])
    if not sections:
        from simcompare.numeric_schema import NumericContractError
        raise NumericContractError('numeric_input_required')
    records,seen,reasons = [],set(),set()
    for section in sections:
        root,dataset = _numeric_input(cfg,section)
        if str(root.resolve()) in seen:
            continue
        seen.add(str(root.resolve()))
        for case in dataset.cases:
            for d,record in sorted(case.records.items()):
                measured = measure_full_mask(record)
                record_reasons = sorted(set((*measured.reasons,*case.reasons)))
                reasons.update(record_reasons)
                records.append({'product_id':case.identity.product_id,'case_id':case.identity.case_id,
                                'diameter_um':d,'failure_type':case.failure_type,'input_status':record.input_status,
                                'value':measured.ca_um2,'partial_value':measured.partial_value,'unit':'um^2','status':measured.status,
                                'reasons':record_reasons,'measurement':measured})
        reasons.update(dataset.reasons)
    payload = {'schema_version':'numeric_validation_v1','status':'partial','reasons':sorted(reasons),'records':records,
               'gates':current_gate_decisions()}
    output = Path(out) if out is not None else _numeric_path(cfg,cfg['output_dir'])
    paths = write_numeric_report(output,'validation',payload,{'records':records})
    return {'status':'partial','reasons':sorted(reasons),'outputs':paths,'exit_code':3}


def run_numeric_roi_export(cfg, out):
    from simcompare.roi_export import export_review_rois
    root,dataset,maps = _numeric_map(cfg)
    return export_review_rois(dataset,root,out,maps=maps,selections=cfg.get('review_rois',{}),
                              asset_manifest=cfg['map']['input'].get('asset_manifest'))
