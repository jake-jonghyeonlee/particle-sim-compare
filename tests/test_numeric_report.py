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
    assert payload['failure_fragment_policy']['value'] is None
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
