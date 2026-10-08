"""numeric_v2 동작 검증을 위한 합성 binary 데이터."""
from __future__ import annotations
import csv
import json
from pathlib import Path
import numpy as np


def generate_numeric_demo(out_dir: str | Path) -> dict:
    """32 지름의 독립 binary 예제. 사용자 gate를 답하지 않는 합성 계약이다."""
    import hashlib
    import yaml
    from simcompare.adapters.numeric import HASH_RECIPE, source_payload_hash
    from simcompare.numeric_schema import DIAMETERS_UM, CaseIdentity

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    products = {}
    for product, shape, panel_size, cell_sets in (
        ('reference', (1, 6), (60, 10), ((0,1,2), (2,3), (1,3,4), (4,5))),
        ('target', (2, 3), (30, 10), ((0,1), (1,2), (0,3,4), (4,5))),
    ):
        root = out/product
        (root/'assets').mkdir(parents=True, exist_ok=True)
        frame_id = product+'_frame'
        def asset(name, array):
            relative = 'assets/'+name+'.npy'
            np.save(root/relative, np.asarray(array, dtype=np.uint8))
            return {'path': relative, 'sha256': hashlib.sha256((root/relative).read_bytes()).hexdigest(),
                    'value_semantics': 'binary', 'observed': True}
        valid = np.ones(shape, np.uint8)
        valid_ref = asset('valid', valid)
        frame = {'frame_id': frame_id, 'length_unit': 'um', 'pixel_pitch_um': 1,
                 'origin_xy_um': [100,200], 'shape_yx': list(shape), 'axis_order': 'yx',
                 'origin_convention': 'top_left_cell_edge', 'valid': valid_ref, 'valid_status': 'observed'}
        pairs = [('AB','A','B','line'), ('AC','A','C','line'), ('DE','D','E','pixel'), ('EF','E','F','pixel')]
        cases, asset_cases = [], []
        for n, (pair, a, b, kind) in enumerate(pairs + [('AB','A','B','line')]):
            vertical = n == 4
            case_id = 'vertical_AB' if vertical else pair
            eps = [{'electrode_id': a, 'role': 'gate', 'actual_layer': 'M1' if vertical else 'M2'},
                   {'electrode_id': b, 'role': 'data', 'actual_layer': 'M2'}]
            case = {'product_id': product, 'design_version': 'synthetic_design', 'unit_id': product+'_unit',
                    'source_id': 'synthetic_source', 'process_step_id': 'step_M2', 'exposure_id': 'synthetic_exposure',
                    'case_id': case_id, 'frame_id': frame_id, 'source_contract_version': 'synthetic_v2',
                    'endpoints': eps, 'pair_id': pair, 'analysis_layer': 'M2', 'density_process_layer': 'step_M2',
                    'failure_type': 'vertical_short' if vertical else 'lateral_short',
                    'raw': {'case_id': case_id, 'ordered_endpoints': eps,
                            'fab_pair_key': product+':'+case_id, 'assigned_process_layer': 'step_M2'}, 'records': []}
            if vertical:
                case.update(height_um=3, height_evidence='명시 합성 높이 3 um', upper_actual_layer='M2')
            for d in DIAMETERS_UM:
                below = vertical and d < 3
                arr = np.zeros(shape, np.uint8)
                if d == 5:
                    arr.flat[list((0,) if vertical else cell_sets[n])] = 1
                record = {'diameter_um': d, 'input_status': 'not_applicable' if below else 'observed',
                          'reasons': ['below_height_vertical_short'] if below else []}
                if below:
                    record['applicability_evidence'] = {'height_um': 3, 'comparison': 'diameter_um < height_um',
                                                       'source': '명시 합성 높이'}
                else:
                    record['mask'] = arr.tolist()
                case['records'].append(record)
            cases.append(case)
            layouts = []
            for slot, ep in enumerate(eps):
                arr = np.zeros(shape, np.uint8)
                arr.flat[slot] = 1
                layouts.append({'endpoint': ep, 'asset': asset(case_id+'_layout_'+str(slot), arr)})
            asset_cases.append({'identity': {k:case[k] for k in CaseIdentity.__dataclass_fields__},
                                'endpoints': eps, 'failure_type': case['failure_type'],
                                'frame': {k:v for k,v in frame.items() if k not in ('valid','valid_status')},
                                'source_version': 'synthetic_layout_v1',
                                'assets': {'layouts': layouts, 'valid': valid_ref,
                                           'cnt': asset(case_id+'_cnt', np.zeros(shape, np.uint8)), 'via': []}})
        with (root/'pairs.csv').open('w', newline='', encoding='utf-8') as handle:
            writer = csv.writer(handle, lineterminator='\n')
            writer.writerow(['pair_id','electrode_a_id','electrode_b_id','class','pair_table_version'])
            writer.writerows([(*p, 'synthetic_pairs_v1') for p in pairs])
        columns = ['distribution_id','fab_id','product_family','process_layer','period','source_id','diameter_um',
                   'raw_value','raw_value_unit','observed','surveyed_area_um2','averaging_basis','bin_semantics',
                   'bin_lower_um','bin_upper_um','density_per_um2','canonical_unit','status','reasons']
        with (root/'density.csv').open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, columns, lineterminator='\n')
            writer.writeheader()
            for d in DIAMETERS_UM:
                writer.writerow(dict(distribution_id='SYNTHETIC_D', fab_id='synthetic_fab', product_family='synthetic',
                    process_layer='step_M2', period='synthetic_period', source_id='synthetic_density', diameter_um=d,
                    raw_value=1 if d==5 else 0, raw_value_unit='um^-2', observed='true', bin_semantics='per_bin',
                    status='unavailable', reasons='density_contract_unconfirmed'))
        manifest = {'schema_version': 'numeric_v2', 'contract_status': 'synthetic_only',
                    'source_format': 'synthetic_binary_example', 'source_contract_version': 'synthetic_v2',
                    'adapter_version': 'numeric_v2', 'is_synthetic': True, 'diameters_um': list(DIAMETERS_UM),
                    'frames': [frame], 'cases': cases, 'layer_process_mapping_status': 'synthetic_only',
                    'layer_stack': [{'actual_layer':'M1','stack_order':1,'density_process_layer':'step_M1'},
                                    {'actual_layer':'M2','stack_order':2,'density_process_layer':'step_M2'}],
                    'pair_table_version': 'synthetic_pairs_v1',
                    'density': {'path':'density.csv','distribution_id':'SYNTHETIC_D'},
                    'unit_domain': {'frame_id': frame_id, 'length_unit':'um',
                        'unit_width_um':shape[1], 'unit_height_um':shape[0],
                        'measured_width_um':shape[1], 'measured_height_um':shape[0],
                        'unit_area_basis':None,'conversion_policy':None,'divisor':None},
                    'panel': {'length_unit':'um', 'panel_width_um':panel_size[0], 'panel_height_um':panel_size[1],
                              'rounding_policy':'floor'},
                    'gates': {g:{'status':'pending','answer':None,'reason':'사용자 답변 없음'}
                              for g in ('G1','G2','G4','G7a','G7b')}, 'hash_recipe': HASH_RECIPE,
                    'files': {name:{'sha256':hashlib.sha256((root/name).read_bytes()).hexdigest()}
                              for name in ('pairs.csv','density.csv')}}
        manifest['pair_table_hash'] = manifest['files']['pairs.csv']['sha256']
        manifest['input_hash'] = manifest['source_manifest_hash'] = source_payload_hash(manifest)
        (root/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        asset_manifest = {'schema_version':'review_assets_v1','source_manifest_hash':manifest['input_hash'],
                          'cases':asset_cases}
        (root/'review_assets.json').write_text(json.dumps(asset_manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        products[product] = {'data_dir':product,'product_id':product,
            'asset_manifest':{'path':'review_assets.json','sha256':hashlib.sha256((root/'review_assets.json').read_bytes()).hexdigest()},
            'density_contracts':[{'distribution_id':'SYNTHETIC_D','process_layer':'step_M2','period':'synthetic_period',
                                  'basis_version':'synthetic_bins_v1','is_synthetic':True,'bin_semantics':'per_bin','value_unit':'um^-2'}],
            'unit_bases':[{'basis':'one_unit','is_synthetic':True,'frame_id':frame_id,
                           'unit_size_um':[shape[1],shape[0]],'evidence':'명시 합성 full-mask 한 unit'}],
            'panel':{'panel_size':list(panel_size),'unit_size':[shape[1],shape[0]],'length_unit':'um','rounding':'floor'}}
    cfg = {'schema_version':'numeric_config_v1','mode':'synthetic','output_dir':'numeric_outputs',
           'reference':products['reference'],'target':products['target'],
           'map':{'input':products['reference'],'diameter_um':5},
           'review_rois':{'manual':[]}}
    (out/'config.yaml').write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding='utf-8')
    pending = json.loads(json.dumps(cfg))
    for section in (pending['reference'], pending['target'], pending['map']['input']):
        section.pop('density_contracts',None)
        section.pop('unit_bases',None)
    (out/'pending.yaml').write_text(yaml.safe_dump(pending, sort_keys=False, allow_unicode=True), encoding='utf-8')
    return {'is_synthetic':True,'config':str(out/'config.yaml'),'pending_config':str(out/'pending.yaml'),
            'note':'합성 동작 검증이며 gate 답변이나 실자료 승인 근거가 아닙니다'}
