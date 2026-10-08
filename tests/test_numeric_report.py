"""리포트의 공유 링크 중복 제거, null, HTML text 처리 및 단위/provenance."""
import csv
import json
from pathlib import Path

import pytest

from simcompare.adapters.numeric import source_payload_hash
from simcompare.numeric_schema import NumericContractError
from simcompare.pipeline import load_numeric_config, run_numeric_compare
from simcompare.report import unique_bucket_total, write_numeric_report
from simcompare.synthetic import generate_numeric_demo


def test_shared_bucket_repeated_pair_links_are_counted_once(tmp_path):
    generate_numeric_demo(tmp_path/'demo')
    run_numeric_compare(load_numeric_config(tmp_path/'demo/config.yaml'))
    out = tmp_path/'demo/numeric_outputs'
    with (out/'comparison_buckets.csv').open() as f:
        rows = list(csv.DictReader(f))
    rows = [r for r in rows if r['side']=='reference' and r['diameter_um']=='5.0' and r['class']=='line']
    shared = [r for r in rows if r['kind']=='shared']
    assert len(shared)==2 and shared[0]['bucket_id']==shared[1]['bucket_id']
    assert sum(float(r['value']) for r in rows)==5  # 반복 링크를 잘못 합치면 5
    metrics = [{**r,'value':float(r['value']),'reasons':json.loads(r['reasons'])} for r in rows]
    assert unique_bucket_total(metrics)=={'value':4,'status':'available','reasons':[]}
    payload = json.loads((out/'comparison.json').read_text())
    checks = [r for r in payload['bucket_totals'] if r['side']=='reference' and r['class']=='line'
              and json.loads(r['group_id'])['diameter_um']==5]
    assert checks[0]['value']==4


def test_conflicting_bucket_links_and_incomplete_totals():
    with pytest.raises(NumericContractError,match='conflicting_bucket_link'):
        unique_bucket_total([{'bucket_id':'B','value':1,'status':'available'},
                             {'bucket_id':'B','value':2,'status':'available'}])
    assert unique_bucket_total([{'bucket_id':'B','value':None,'status':'unavailable'}])['value'] is None


def test_every_weighted_value_preserves_units_basis_distribution_and_rounding(tmp_path):
    generate_numeric_demo(tmp_path/'demo')
    run_numeric_compare(load_numeric_config(tmp_path/'demo/config.yaml'))
    out = tmp_path/'demo/numeric_outputs'
    with (out/'comparison_summary.csv').open() as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        assert row['unit']=='1' and json.loads(row['distribution_ids'])==['SYNTHETIC_D']
        basis,rounding = json.loads(row['area_basis']),json.loads(row['rounding'])
        if row['side']=='delta':
            assert basis['reference']['basis']==basis['target']['basis']=='one_unit'
            assert rounding['reference']['unit_count']==100 and rounding['target']['unit_count']==50
        else:
            assert basis['basis']=='one_unit' and rounding['policy']=='floor'
            assert rounding['unit_count']==(100 if row['side']=='reference' else 50)
    payload=json.loads((out/'comparison.json').read_text())
    assert payload['reference']['areas']['groups'][0]['frame']['origin_xy_um']==[100,200]
    assert payload['reference']['areas']['pair_table_version']=='synthetic_pairs_v1'
    assert payload['failure_fragment_policy']['value']==['lateral_only','vertical_only','simultaneous']
    assert payload['failure_fragment_policy']['reasons']==[]
    assert payload['failure_fragment_policy']['status']=='implemented'
    assert payload['applied_gate_decisions']['G7b']['answer']=='short_only'
    assert payload['applied_gate_decisions']['G1']['answer'] is None
    assert payload['applied_gate_decisions']['G1']['answered_parts']['bin_semantics']=='per_bin'
    assert payload['reference']['areas']['groups'][4]['source_cases'][4]['failure_type']=='vertical_short'


def test_html_escapes_script_ending_ids_in_actual_numeric_report(tmp_path):
    generate_numeric_demo(tmp_path/'demo')
    path=tmp_path/'demo/reference/manifest.json'
    manifest=json.loads(path.read_text())
    injected='ID</script><script>alert(1)</script>'
    manifest['cases'][0]['case_id']=manifest['cases'][0]['raw']['case_id']=injected
    manifest['input_hash']=manifest['source_manifest_hash']=source_payload_hash(manifest)
    path.write_text(json.dumps(manifest))
    run_numeric_compare(load_numeric_config(tmp_path/'demo/config.yaml'))
    out=tmp_path/'demo/numeric_outputs'
    html=(out/'comparison.html').read_text()
    assert '</script>' not in html and '<script>' not in html
    assert 'ID&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;' in html
    assert injected in (out/'comparison.json').read_text()


def test_csv_null_and_html_null_are_explicit(tmp_path):
    payload={'status':'partial','value':None,'reasons':['missing_input'],'id':'<b>x</b>'}
    write_numeric_report(tmp_path,'partial',payload,{'values':[{'value':None,'status':'unavailable','reasons':['missing_input']}]})
    with (tmp_path/'partial_values.csv').open() as f:
        row=next(csv.DictReader(f))
    assert row['value']=='null' and row['status']=='unavailable' and row['reasons']=='["missing_input"]'
    assert '&lt;b&gt;x&lt;/b&gt;' in (tmp_path/'partial.html').read_text()


def test_weighted_and_delta_bucket_csv_preserve_shared_and_conservation(tmp_path):
    generate_numeric_demo(tmp_path/'demo')
    run_numeric_compare(load_numeric_config(tmp_path/'demo/config.yaml'))
    out=tmp_path/'demo/numeric_outputs'
    with (out/'comparison_weighted_buckets.csv').open() as f:
        rows=[r for r in csv.DictReader(f) if r['side']=='reference' and r['class']=='line' and r['domain']=='panel']
    assert len([r for r in rows if r['kind']=='shared'])==2
    metrics=[{**r,'value':float(r['value']),'reasons':json.loads(r['reasons'])} for r in rows]
    assert unique_bucket_total(metrics)['value']==400
    with (out/'comparison_delta_buckets.csv').open() as f:
        rows=[r for r in csv.DictReader(f) if r['class']=='line']
    assert sum(float(r['value']) for r in rows)==-250
    assert any(r['kind']=='shared' for r in rows)
    assert all('simultaneous' not in r['kind'] for r in rows)


def test_partial_area_and_case_ca_json_csv_html_never_show_complete_values(tmp_path):
    from html import unescape
    generate_numeric_demo(tmp_path/'demo')
    root=tmp_path/'demo'
    path=root/'target/manifest.json'
    manifest=json.loads(path.read_text())
    record=next(r for r in manifest['cases'][0]['records'] if r['diameter_um']==5)
    record.pop('mask');record.update(input_status='missing',reasons=['missing_input'])
    manifest['input_hash']=manifest['source_manifest_hash']=source_payload_hash(manifest)
    path.write_text(json.dumps(manifest))
    run_numeric_compare(load_numeric_config(root/'config.yaml'))
    out=root/'numeric_outputs'
    document=json.loads((out/'comparison.json').read_text())
    group=next(g for g in document['target']['areas']['groups'] if g['group']['diameter_um']==5)
    assert group['line']['area']['status']=='partial'
    assert group['line']['area']['value'] is None and group['line']['area']['partial_value']==3
    with (out/'comparison_areas.csv').open() as f:
        row=next(r for r in csv.DictReader(f) if r['side']=='target' and r['class']=='line' and r['diameter_um']=='5.0')
    assert row['value']=='null' and row['partial_value']=='3.0' and row['status']=='partial'
    escaped=(out/'comparison.html').read_text().split('<pre>',1)[1].split('</pre>',1)[0]
    html_document=json.loads(unescape(escaped))
    html_area=next(g for g in html_document['target']['areas']['groups'] if g['group']['diameter_um']==5)['line']['area']
    assert html_area['value'] is None and html_area['partial_value']==3
    with (out/'comparison_buckets.csv').open() as f:
        buckets=[r for r in csv.DictReader(f) if r['side']=='target' and r['class']=='line' and r['diameter_um']=='5.0' and r['pair_id']=='AB']
    assert len(buckets)==1
    row=buckets[0]
    lineage=json.loads(row['pair_lineage_case_keys'])
    contributors=json.loads(row['contributing_source_case_keys'])
    source={s['source_key']:s['identity']['case_id'] for s in group['source_cases']}
    assert {source[k] for k in lineage}=={'AB','vertical_AB'}
    assert {source[k] for k in contributors}=={'vertical_AB'}
    assert 'source_case_keys' not in row  # 계보를 기여자 이름으로 표시하지 않는다.


def test_bucket_links_keep_partial_value_separate():
    rows=[{'bucket_id':'B','value':None,'partial_value':1,'status':'partial'},
          {'bucket_id':'B','value':None,'partial_value':1,'status':'partial'},
          {'bucket_id':'C','value':None,'partial_value':2,'status':'partial'}]
    assert unique_bucket_total(rows)=={'value':None,'partial_value':3,'status':'partial','reasons':['bucket_coverage_incomplete']}


def test_available_map_report_csv_has_counts_and_no_pending_scope(tmp_path):
    from simcompare.pipeline import run_numeric_map
    generate_numeric_demo(tmp_path/'demo')
    result=run_numeric_map(load_numeric_config(tmp_path/'demo/config.yaml'))
    assert result['status']=='available' and result['exit_code']==0
    out=tmp_path/'demo/numeric_outputs'
    document=json.loads((out/'electrode_map.json').read_text())
    import numpy as np
    for entry in (document['layers'][0],document['summaries'][0]):
        with np.load(out/entry['array_file']) as arrays:
            assert arrays['counts'].tolist()==[[2,4,3,4,3,2]]
    assert document['layers'][0]['regions']==[]
    assert 'map_scope_unconfirmed' not in document['reasons']
    assert not (out/'electrode_map_map.csv').exists()
    config=load_numeric_config(tmp_path/'demo/config.yaml');config['map']['per_cell_text']=True
    run_numeric_map(config)
    with (out/'electrode_map_map.csv').open() as f: rows=list(csv.DictReader(f))
    assert [int(row['value']) for row in rows]==[2,4,3,4,3,2]
    assert all(row['partial_value']=='null' and row['status']=='available' for row in rows)
    assert document['provenance']['G7b']['decided_on']=='2026-10-08'


def test_r2_synthetic_label_and_consistent_gate_status_follow_input(tmp_path):
    from simcompare.adapters.numeric import load_dataset
    from simcompare.pipeline import _numeric_rates
    from simcompare.report import comparison_report
    from simcompare.linepixel import compare_rates
    from simcompare.numeric_schema import current_gate_decisions
    generate_numeric_demo(tmp_path/'demo');cfg=load_numeric_config(tmp_path/'demo/config.yaml')
    ref=load_dataset(tmp_path/'demo/reference');tgt=load_dataset(tmp_path/'demo/target')
    ref.provenance['is_synthetic']=False;tgt.provenance['is_synthetic']=False
    rr=_numeric_rates(ref,cfg['reference']);tr=_numeric_rates(tgt,cfg['target'])
    payload,tables=comparison_report(rr,tr,compare_rates(rr,tr,reference_product_id='reference',target_product_id='target'))
    assert payload['is_synthetic'] is False
    assert payload['reference']['gates']==payload['target']['gates']==payload['applied_gate_decisions']==current_gate_decisions()
    write_numeric_report(tmp_path/'out','input',payload,tables)
    assert '합성 동작 검증' not in (tmp_path/'out/input.html').read_text()


def test_r2_retired_arch_pages_label_themselves_legacy():
    root=Path(__file__).resolve().parents[1]
    for name in ('config','checks'):
        assert (root/f'docs/arch/{name}.md').read_text().startswith('> **legacy / archived**')
