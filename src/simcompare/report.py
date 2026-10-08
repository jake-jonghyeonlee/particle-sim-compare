"""수치 결과의 JSON/CSV/HTML. null·상태·사유를 보존하고 공유 bucket은 한 번만 센다."""
from __future__ import annotations

import csv
from dataclasses import asdict, is_dataclass
from html import escape
import json
from pathlib import Path

import numpy as np

from .numeric_schema import NumericContractError


def json_value(value):
    if is_dataclass(value):
        return json_value(asdict(value))
    if isinstance(value, np.ndarray):
        return json_value(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k):json_value(v) for k,v in value.items()}
    if isinstance(value, (tuple,list)):
        return [json_value(v) for v in value]
    return value


def unique_bucket_total(rows):
    """한 class/domain/group의 pair별 반복 링크를 bucket ID로 중복 제거한다."""
    unique = {}
    for row in rows:
        key = row['bucket_id']
        metric = (row['value'], row['status'], tuple(row.get('reasons', ())))
        if key in unique and unique[key] != metric:
            raise NumericContractError('conflicting_bucket_link', key)
        unique[key] = metric
    if any(value is None or status != 'available' for value,status,_ in unique.values()):
        return {'value':None,'status':'unavailable','reasons':['bucket_coverage_incomplete']}
    return {'value':sum(value for value,_,_ in unique.values()),'status':'available','reasons':[]}


def _metric_row(side, product, layer, classification, domain, metric, *, context=None):
    return {'side':side,'product_id':product,'layer':layer,'class':classification,'domain':domain,
            'value':metric['value'],'unit':metric['unit'],'status':metric['status'],
            'reasons':metric['reasons'],'distribution_ids':metric['distribution_ids'],
            'area_basis':metric['area_basis'],'rounding':metric['rounding'],
            'partial_value':metric.get('partial_value'),'coverage':metric['coverage'],'context':context}


def comparison_report(reference, target, deltas):
    """계산은 T3/T4에 맡기고 별도 line/pixel 및 진단 transfer를 표시한다."""
    payload = {'schema_version':'numeric_comparison_report_v1','is_synthetic':True,
               'metric_semantics':'weighted_short_index','direction':'target_minus_reference',
               'coordinate_policy':'independent_product_frames',
               'reference':reference.to_dict(),'target':target.to_dict(),
               'deltas':[v.to_dict() for _,v in sorted(deltas.items())],
               'failure_fragment_policy':{'status':'pending','value':None,'reasons':['failure_fragment_scope_unconfirmed']}}
    summary, areas, buckets, cases = [], [], [], []
    weighted_buckets, delta_buckets = [], []
    bucket_checks = []
    for side, result in (('reference',reference), ('target',target)):
        for key, layer in sorted(result.layers.items()):
            for classification in ('line','pixel'):
                share = getattr(layer,classification)
                for domain in ('unit','panel'):
                    summary.append(_metric_row(side,key.product_id,key.analysis_layer,classification,domain,
                                               asdict(getattr(share,domain)),context=asdict(key)))
                    for bucket in (*share.exclusive,*share.shared):
                        for pair in bucket.pair_ids:
                            weighted_buckets.append({**_metric_row(side,key.product_id,key.analysis_layer,classification,domain,
                                                                  asdict(getattr(bucket,domain)),context=asdict(key)),
                                'bucket_id':bucket.bucket_id,'kind':bucket.kind,'pair_id':pair,
                                'pair_ids':bucket.pair_ids,'source_bucket_ids':bucket.source_bucket_ids})
            for domain in ('unit','panel'):
                summary.append(_metric_row(side,key.product_id,key.analysis_layer,'transfer',domain,
                                           asdict(getattr(layer,'transfer_'+domain)),context=asdict(key)))
        for key, group in sorted(result.areas.groups.items()):
            group_id = json.dumps(asdict(key), sort_keys=True, separators=(',',':'),ensure_ascii=False)
            for classification, metric in (('line',group.line.area),('pixel',group.pixel.area),
                                            ('transfer',group.transfer),('union',group.union)):
                areas.append({'side':side,'group_id':group_id,'product_id':key.product_id,'layer':key.analysis_layer,
                              'diameter_um':key.diameter_um,'class':classification,**asdict(metric),
                              'frame':group.to_dict()['frame']})
            for classification in ('line','pixel'):
                share = getattr(group,classification)
                links = []
                for bucket in (*share.exclusive,*share.shared):
                    for pair in bucket.pair_ids:
                        row = {'side':side,'group_id':group_id,'product_id':key.product_id,
                               'layer':key.analysis_layer,'diameter_um':key.diameter_um,'class':classification,
                               'bucket_id':bucket.bucket_id,'kind':bucket.kind,'pair_id':pair,
                               'pair_ids':bucket.pair_ids,'source_case_keys':bucket.source_case_keys,**asdict(bucket.area)}
                        buckets.append(row)
                        links.append(row)
                total = unique_bucket_total(links)
                if share.area.status != 'available':
                    total = {'value':None,'status':'unavailable','reasons':list(share.area.reasons)}
                elif total['value'] != share.area.value:
                    raise NumericContractError('report_bucket_conservation_failed')
                bucket_checks.append({'side':side,'group_id':group_id,'class':classification,**total,'unit':'um^2'})
            for case in group.source_cases:
                measured = asdict(case.measurement)
                cases.append({'side':side,'source_key':case.source_key,'product_id':key.product_id,
                              'layer':key.analysis_layer,'diameter_um':case.diameter_um,
                              'case_id':case.identity.case_id,'failure_type':case.failure_type,
                              'pair_id':case.pair_id,'identity':asdict(case.identity), 'endpoints':[asdict(e) for e in case.endpoints],
                              'raw':case.raw,'input_status':case.input_status,'frame':group.to_dict()['frame'],
                              'value':measured['ca_um2'],'unit':'um^2','status':measured['status'],
                              'reasons':measured['reasons'],'measurement':measured})
    for key, delta in sorted(deltas.items()):
        for classification in ('line','pixel','transfer'):
            summary.append(_metric_row('delta',delta.target_product_id,key.analysis_layer,classification,'panel',
                                       asdict(getattr(delta,classification)),context=asdict(key)))
        for classification in ('line','pixel'):
            for kind in ('exclusive','shared'):
                for members, metric in sorted(getattr(delta,classification+'_'+kind).items()):
                    delta_buckets.append({**_metric_row('delta',delta.target_product_id,key.analysis_layer,classification,
                                                      'panel',asdict(metric),context=asdict(key)),
                                          'comparison_key':asdict(key),'kind':kind,'pair_ids':json.loads(members)})
    payload['bucket_totals'] = bucket_checks
    complete = bool(deltas) and all(getattr(d,c).status == 'available'
                                   for d in deltas.values() for c in ('line','pixel','transfer'))
    payload['status'] = 'available' if complete else 'partial'
    payload['reasons'] = sorted({r for d in deltas.values() for c in ('line','pixel','transfer')
                                 for r in getattr(d,c).reasons})
    return json_value(payload), {'summary':summary,'areas':areas,'buckets':buckets,'case_ca':cases,
                                  'weighted_buckets':weighted_buckets,'delta_buckets':delta_buckets}


def map_report(result):
    payload = result.to_dict()
    usable = any(layer.status == 'partial' for layer in result.layers)
    payload.update(schema_version='numeric_electrode_map_report_v1',status='partial' if usable else 'unavailable',
                   reasons=sorted({'map_scope_unconfirmed',*(reason for layer in result.layers for reason in layer.reasons)}))
    for layer, encoded in zip(result.layers,payload['layers']):
        encoded['observed_lower_bound_counts'] = encoded['counts']
        encoded['counts'] = [[int(layer.counts[r,c]) if layer.frame.valid[r,c] and layer.status!='unavailable' else None
                              for c in range(layer.counts.shape[1])] for r in range(layer.counts.shape[0])]
    for summary, encoded in zip(result.summaries,payload['summaries']):
        if summary.counts is not None:
            encoded['observed_lower_bound_counts'] = encoded['counts']
            encoded['counts'] = [[int(summary.counts[r,c]) if summary.frame.valid[r,c] and summary.status!='unavailable' else None
                                  for c in range(summary.counts.shape[1])] for r in range(summary.counts.shape[0])]
    rows = []
    for layer in result.layers:
        x,y = layer.frame.origin_xy_um
        p = layer.frame.pixel_pitch_um
        for r,c in np.ndindex(layer.counts.shape):
            valid = bool(layer.frame.valid[r,c])
            rows.append({'product_id':layer.key.product_id,'layer':layer.key.layer,'frame_id':layer.frame.frame_id,
                         'row':r,'col':c,'x0_um':x+c*p,'y0_um':y+r*p,
                         'value':int(layer.counts[r,c]) if valid and layer.status!='unavailable' else None,'unit':'distinct_electrodes',
                         'observed_lower_bound':int(layer.counts[r,c]),
                         'status':layer.status if valid else 'unavailable',
                         'reasons':layer.reasons if valid else ('outside_valid_domain',),'valid':valid,
                         'coverage':layer.coverage})
    return json_value(payload), {'map':rows}


def write_numeric_report(out, name, payload, tables):
    """전 계산이 끝난 후 출력. 오류에는 별도 error.json만 작성한다."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    payload = json_value(payload)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    paths = []
    json_path = out/(name+'.json')
    json_path.write_text(encoded+'\n',encoding='utf-8')
    paths.append(str(json_path))
    for table, rows in tables.items():
        rows = [json_value(row) for row in rows]
        path = out/(name+'_'+table+'.csv')
        columns = list(dict.fromkeys(k for row in rows for k in row)) or ['value','status','reasons']
        with path.open('w',encoding='utf-8',newline='') as handle:
            writer = csv.DictWriter(handle,columns,lineterminator='\n')
            writer.writeheader()
            for row in rows:
                writer.writerow({k:json.dumps(v,ensure_ascii=False,allow_nan=False,separators=(',',':'))
                                 if v is None or isinstance(v,(dict,list,bool)) else v for k,v in row.items()})
        paths.append(str(path))
    # JSON을 script에 삽입하지 않고 모든 텍스트를 escape하여 정적으로 표시한다.
    html = ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>'+escape(name)+
            '</title><style>body{font-family:system-ui;max-width:1100px;margin:2rem auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'+
            '<h1>'+escape(name)+'</h1><p>상태: '+escape(payload.get('status','partial'))+
            ' · 합성 동작 검증. 원본 좌표와 관측 범위를 함께 확인하세요.</p><pre>'+escape(encoded)+'</pre></html>')
    html_path = out/(name+'.html')
    html_path.write_text(html,encoding='utf-8')
    paths.append(str(html_path))
    return paths
