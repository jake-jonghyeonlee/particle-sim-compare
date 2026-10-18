"""수치 결과의 JSON/CSV/HTML. null·상태·사유를 보존하고 공유 bucket은 한 번만 센다."""
from __future__ import annotations

import csv
from dataclasses import asdict, is_dataclass
from html import escape
import json
from pathlib import Path

import numpy as np

from .numeric_schema import NumericContractError, current_gate_decisions
from .linepixel import FAILURE_PIECES, FAILURE_PIECE_POLICY, FRACTION_NAMES, compare_fractions

FRACTION_NOTE = ('frame 면적 비율은 각 제품의 측정 2×2 frame 면적으로 나눈 단위 없는 값입니다. '
                 '유닛 크기가 달라도 비교할 수 있으며 G2의 한 unit 환산이 필요 없습니다. '
                 '밀도·unit·panel 가중값이나 확률·수율이 아니며 유한 2×2 경계 효과는 남아 있습니다.')


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
        metric = (row['value'], row.get('partial_value'), row['status'], tuple(row.get('reasons', ())))
        if key in unique and unique[key] != metric:
            raise NumericContractError('conflicting_bucket_link', key)
        unique[key] = metric
    if any(value is None or status != 'available' for value,_,status,_ in unique.values()):
        known = [value if status=='available' else partial for value,partial,status,_ in unique.values()
                 if (value if status=='available' else partial) is not None]
        return {'value':None,'partial_value':sum(known) if known else None,
                'status':'partial' if known else 'unavailable','reasons':['bucket_coverage_incomplete']}
    return {'value':sum(value for value,_,_,_ in unique.values()),'status':'available','reasons':[]}


def _metric_row(side, product, layer, classification, domain, metric, *, context=None):
    return {'side':side,'product_id':product,'layer':layer,'class':classification,'domain':domain,
            'value':metric['value'],'unit':metric['unit'],'status':metric['status'],
            'reasons':metric['reasons'],'distribution_ids':metric['distribution_ids'],
            'area_basis':metric['area_basis'],'rounding':metric['rounding'],
            'partial_value':metric.get('partial_value'),'coverage':metric['coverage'],'context':context}


def comparison_report(reference, target, deltas):
    """계산은 T3/T4에 맡기고 별도 line/pixel 및 진단 transfer를 표시한다."""
    payload = {'schema_version':'numeric_comparison_report_v1','is_synthetic':reference.areas.is_synthetic and target.areas.is_synthetic,
               'metric_semantics':'weighted_short_index','direction':'target_minus_reference',
               'coordinate_policy':'independent_product_frames',
               'applied_gate_decisions':current_gate_decisions(),
               'reference':reference.to_dict(),'target':target.to_dict(),
               'deltas':[v.to_dict() for _,v in sorted(deltas.items())],
               'failure_fragment_policy':dict(FAILURE_PIECE_POLICY),
               'failure_piece_axis_note':'불량 유형 조각은 line / pixel과 다른 축입니다. 두 축의 값을 서로 더하거나 빼지 않습니다.',
               'fraction_of_frame_note':FRACTION_NOTE}
    summary, areas, buckets, cases = [], [], [], []
    weighted_buckets, delta_buckets = [], []
    bucket_checks, pieces, fractions = [], [], []
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
            for name in FAILURE_PIECES:
                for domain in ('unit','panel'):
                    pieces.append({**_metric_row(side,key.product_id,key.analysis_layer,name,domain,
                                                asdict(getattr(layer.failure_pieces[name],domain)),context=asdict(key)),
                                   'axis':'failure_type','piece':name})
        for key, group in sorted(result.areas.groups.items()):
            group_id = json.dumps(asdict(key), sort_keys=True, separators=(',',':'),ensure_ascii=False)
            for classification, metric in (('line',group.line.area),('pixel',group.pixel.area),
                                            ('transfer',group.transfer),('union',group.union)):
                areas.append({'side':side,'group_id':group_id,'product_id':key.product_id,'layer':key.analysis_layer,
                              'diameter_um':key.diameter_um,'class':classification,**asdict(metric),
                              'frame':group.to_dict()['frame']})
            for name,metric in group.failure_pieces.items():
                pieces.append({'side':side,'group_id':group_id,'product_id':key.product_id,
                               'layer':key.analysis_layer,'diameter_um':key.diameter_um,
                               'axis':'failure_type','piece':name,'domain':'full_mask',
                               **asdict(metric),'frame':group.to_dict()['frame']})
            for name in FRACTION_NAMES:
                fractions.append({'side':side,'group_id':group_id,'product_id':key.product_id,
                                  'layer':key.analysis_layer,'diameter_um':key.diameter_um,'metric':name,
                                  'domain':'frame_fraction',**asdict(group.fractions[name])})
            for classification in ('line','pixel'):
                share = getattr(group,classification)
                links = []
                for bucket in (*share.exclusive,*share.shared):
                    for pair in bucket.pair_ids:
                        row = {'side':side,'group_id':group_id,'product_id':key.product_id,
                               'layer':key.analysis_layer,'diameter_um':key.diameter_um,'class':classification,
                               'bucket_id':bucket.bucket_id,'kind':bucket.kind,'pair_id':pair,
                               'pair_ids':bucket.pair_ids,'pair_lineage_case_keys':bucket.pair_lineage_case_keys,
                               'contributing_source_case_keys':bucket.contributing_source_case_keys,**asdict(bucket.area)}
                        buckets.append(row)
                        links.append(row)
                total = unique_bucket_total(links)
                if share.area.status != 'available':
                    total = {'value':None,'partial_value':share.area.partial_value,
                             'status':share.area.status,'reasons':list(share.area.reasons)}
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
                              'value':measured['ca_um2'],'partial_value':measured['partial_value'],
                              'unit':'um^2','status':measured['status'],
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
        for name in FAILURE_PIECES:
            pieces.append({**_metric_row('delta',delta.target_product_id,key.analysis_layer,name,'panel',
                                        asdict(delta.failure_pieces[name]),context=asdict(key)),
                           'axis':'failure_type','piece':name})
    fraction_deltas = compare_fractions(reference.areas, target.areas)
    payload['fraction_deltas'] = [{'comparison_key':asdict(ck),'diameter_um':d,
                                   'metrics':{name:asdict(m) for name,m in metrics.items()}}
                                  for (ck,d),metrics in sorted(fraction_deltas.items())]
    for (ck,d),metrics in sorted(fraction_deltas.items()):
        for name,metric in metrics.items():
            fractions.append({'side':'delta','group_id':None,'product_id':None,'layer':ck.analysis_layer,
                              'diameter_um':d,'metric':name,'domain':'frame_fraction',**asdict(metric),
                              'comparison_key':asdict(ck)})
    payload['bucket_totals'] = bucket_checks
    payload['failure_pieces_table'] = pieces
    complete = bool(deltas) and all(getattr(d,c).status == 'available'
                                   for d in deltas.values() for c in ('line','pixel','transfer'))
    payload['status'] = 'available' if complete else 'partial'
    payload['reasons'] = sorted({r for d in deltas.values() for c in ('line','pixel','transfer')
                                 for r in getattr(d,c).reasons})
    return json_value(payload), {'summary':summary,'areas':areas,'buckets':buckets,'case_ca':cases,
                                  'weighted_buckets':weighted_buckets,'delta_buckets':delta_buckets,'failure_pieces':pieces,
                                  'fractions':fractions}


def map_report(result, *, per_cell=False):
    """Default text is bounded by layers/counts, never by grid cells."""
    if per_cell and any(m.frame is not None and m.frame.valid.size > 4096
                        for m in (*result.layers,*result.summaries)):
        raise NumericContractError('per_cell_text_grid_too_large', '최대 4096 cells')
    payload = result.to_dict(include_arrays=per_cell)
    statuses = [m.status for m in (*result.layers,*result.summaries)]
    status = ('available' if statuses and all(s=='available' for s in statuses) else
              'partial' if any(s in ('available','partial') for s in statuses) else 'unavailable')
    payload.update(schema_version='numeric_electrode_map_report_v2',status=status,
                   array_format='numpy_npz',unit='distinct_electrodes',
                   reasons=sorted({r for m in (*result.layers,*result.summaries) for r in m.reasons}))
    rows=[]
    for kind,entries in (('layers',result.layers),('summaries',result.summaries)):
        for index,(entry,encoded) in enumerate(zip(entries,payload[kind])):
            path=f'electrode_map_{kind}_{index}.npz'
            counts=entry.counts
            encoded['array_file']=path if entry.frame is not None else None
            encoded['histogram']={} if counts is None else {
                str(int(n)):int(freq) for n,freq in zip(*np.unique(counts[entry.frame.valid],return_counts=True))}
            if not per_cell:
                encoded['counts']=None if counts is None else {'path':path,'key':'counts'}
                if encoded['frame'] is not None:
                    encoded['frame']['valid']={'path':path,'key':'valid'}
            if per_cell and kind=='layers':
                for r,c in np.ndindex(entry.frame.shape_yx):
                    valid=bool(entry.frame.valid[r,c])
                    rows.append({'layer':entry.key.layer,'row':r,'col':c,'valid':valid,
                                 'value':int(counts[r,c]) if valid and entry.status=='available' else None,
                                 'partial_value':int(counts[r,c]) if valid and counts is not None and entry.status=='partial' else None,
                                 'status':entry.status if valid else 'unavailable',
                                 'reasons':entry.reasons if valid else ('outside_valid_domain',)})
    return json_value(payload), {'map':rows} if per_cell else {}


def write_map_arrays(out, maps, payload):
    paths=[]
    for kind,entries in (('layers',maps.layers),('summaries',maps.summaries)):
        for entry,encoded in zip(entries,payload[kind]):
            if encoded['array_file'] is None:continue
            path=Path(out)/encoded['array_file']
            arrays={'valid':entry.frame.valid}
            if entry.counts is not None:arrays['counts']=entry.counts
            np.savez_compressed(path,**arrays)
            import hashlib
            encoded['array_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            paths.append(str(path))
    return paths


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
    table_html = ''
    if 'failure_pieces' in tables:
        table_html += '<p>'+escape(payload['failure_piece_axis_note'])+'</p>'
        for table,title,label in (('summary','Line / pixel 및 transfer','class'),
                                  ('failure_pieces','불량 유형 조각','piece')):
            columns=['side','product_id','layer','domain',label,'value','partial_value','unit','status','reasons']
            table_html += '<h2>'+escape(title)+'</h2><table><tr>'+''.join('<th>'+escape(k)+'</th>' for k in columns)+'</tr>'
            for row in tables[table]:
                table_html += '<tr>'+''.join('<td>'+escape(json.dumps(json_value(row.get(k)),ensure_ascii=False,allow_nan=False))+'</td>' for k in columns)+'</tr>'
            table_html += '</table>'
    if 'fractions' in tables:
        table_html += '<h2>frame 면적 비율 (fraction_of_frame)</h2><p>'+escape(payload.get('fraction_of_frame_note',FRACTION_NOTE))+'</p>'
        columns=['side','product_id','layer','diameter_um','metric','value','partial_value','status','reasons']
        table_html += '<table><tr>'+''.join('<th>'+escape(k)+'</th>' for k in columns)+'</tr>'
        for row in tables['fractions']:
            table_html += '<tr>'+''.join('<td>'+escape(json.dumps(json_value(row.get(k)),ensure_ascii=False,allow_nan=False))+'</td>' for k in columns)+'</tr>'
        table_html += '</table>'
    # JSON을 script에 삽입하지 않고 모든 텍스트를 escape하여 정적으로 표시한다.
    html = ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>'+escape(name)+
            '</title><style>body{font-family:system-ui;max-width:1100px;margin:2rem auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'+
            '<h1>'+escape(name)+'</h1><p>상태: '+escape(payload.get('status','partial'))+
            (' · 합성 동작 검증.' if payload.get('is_synthetic') else ' · 입력 자료 결과.')+' 원본 좌표와 관측 범위를 함께 확인하세요.</p>'+table_html+'<pre>'+escape(encoded)+'</pre></html>')
    html_path = out/(name+'.html')
    html_path.write_text(html,encoding='utf-8')
    paths.append(str(html_path))
    return paths
