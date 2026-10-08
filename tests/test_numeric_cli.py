"""새 numeric CLI의 subprocess 검산. 기대 숫자는 수작업 fixture에 둔다."""
import csv
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from simcompare.adapters.numeric import source_payload_hash
from simcompare.cli import main
from simcompare.synthetic import generate_numeric_demo

EXPECTED = Path(__file__).parent/'fixtures/numeric_v2/expected'


def run_cli(*args):
    return subprocess.run([sys.executable,'-m','simcompare',*map(str,args)],capture_output=True,text=True)


def projection(payload):
    result = {'status':payload['status'],'direction':payload['direction']}
    for side in ('reference','target'):
        group = next(g for g in payload[side]['areas']['groups'] if g['group']['diameter_um']==5)
        areas = {c:group[c]['area']['value'] for c in ('line','pixel')}
        areas.update({c:group[c]['value'] for c in ('transfer','union')})
        for c in ('line','pixel'):
            for kind in ('exclusive','shared'):
                areas[c+'_'+kind] = {'|'.join(b['pair_ids']):b['area']['value'] for b in group[c][kind]}
        result[side+'_d5'] = areas
        layer = payload[side]['layers'][0]
        result[side+'_panel'] = {c:layer[c]['panel']['value'] for c in ('line','pixel')}
        result[side+'_panel'].update(transfer=layer['transfer_panel']['value'],N=layer['line']['panel']['rounding']['unit_count'])
        result[side+'_case_ca_d5'] = {c['identity']['case_id']:c['measurement']['ca_um2'] for c in group['source_cases']}
    result['delta_panel'] = {c:payload['deltas'][0][c]['value'] for c in ('line','pixel','transfer')}
    return result


def test_subprocess_demo_then_compare_matches_hand_authored_json_and_csv(tmp_path):
    data = tmp_path/'demo'
    assert run_cli('demo-data','--out',data).returncode == 0
    assert (data/'reference/manifest.json').exists()
    assert not (data/'bundles.csv').exists()  # demo는 numeric_v2만 생성
    compared = run_cli('compare','--config',data/'config.yaml')
    assert compared.returncode == 0, compared.stderr
    out = data/'numeric_outputs'
    payload = json.loads((out/'comparison.json').read_text())
    assert projection(payload) == json.loads((EXPECTED/'comparison.json').read_text())
    with (EXPECTED/'summary.csv').open() as handle:
        expected = list(csv.DictReader(handle))
    with (out/'comparison_summary.csv').open() as handle:
        actual = [{k:row[k] for k in expected[0]} for row in csv.DictReader(handle)]
    assert actual == expected
    frames = [payload[s]['areas']['groups'][0]['frame']['shape_yx'] for s in ('reference','target')]
    assert frames == [[1,6],[2,3]]
    assert payload['coordinate_policy'] == 'independent_product_frames'
    for side in ('reference','target'):
        assert all(state['status']=='pending' and state['answer'] is None for state in payload[side]['gates'].values())
    assert (out/'comparison.html').exists()
    assert not (out/'model.pt').exists()


def test_single_product_map_validation_roi_and_gate_pending(tmp_path):
    generate_numeric_demo(tmp_path/'demo')
    data = tmp_path/'demo'
    mapped = run_cli('electrode-map','--config',data/'config.yaml')
    assert mapped.returncode == 0, mapped.stderr
    map_payload = json.loads((data/'numeric_outputs/electrode_map.json').read_text())
    assert {r['key']['product_id'] for r in map_payload['layers']} == {'reference'}
    assert map_payload['status'] == 'available'
    assert map_payload['layers'][0]['counts'] == [[2,4,3,4,3,2]]
    assert run_cli('validate-data','--config',data/'pending.yaml').returncode == 3
    pending = run_cli('compare','--config',data/'pending.yaml')
    assert pending.returncode == 3, pending.stderr
    payload = json.loads((data/'numeric_outputs/comparison.json').read_text())
    assert payload['reference']['areas']['groups'][4]['line']['area']['value'] == 4
    for c in ('line','pixel','transfer'):
        metric = payload['deltas'][0][c]
        assert metric['value'] is None and metric['status'] == 'unavailable'
        assert {'density_contract_unconfirmed','unit_area_basis_unconfirmed'} <= set(metric['reasons'])
    with (data/'numeric_outputs/comparison_summary.csv').open() as handle:
        assert all(r['value']=='null' and r['status']=='unavailable' and r['reasons']!='[]' for r in csv.DictReader(handle))
    exported = run_cli('export-review-rois','--config',data/'config.yaml','--out',data/'roi')
    assert exported.returncode == 0, exported.stderr
    rois = json.loads((data/'roi/review_rois.json').read_text())
    assert rois['rois'] == []  # 사용자가 위치/threshold를 지정하기 전 자동 ROI 없음
    assert map_payload['layers'][0]['regions'] == []
    assert len(map_payload['heatmaps']) == 2
    for image in map_payload['heatmaps']:
        assert (data/'numeric_outputs'/image['png']).exists()
        assert (data/'numeric_outputs'/image['sidecar']).exists()


def test_unmapped_target_final_delta_is_unavailable_not_partial_difference(tmp_path):
    generate_numeric_demo(tmp_path/'demo')
    data = tmp_path/'demo'
    path = data/'target/manifest.json'
    manifest = json.loads(path.read_text())
    extra = json.loads(json.dumps(manifest['cases'][0]))
    extra['case_id'] = extra['raw']['case_id'] = 'unmapped'
    extra['pair_id'] = None
    extra['endpoints'][1]['electrode_id'] = 'UNMAPPED_X'
    extra['raw']['ordered_endpoints'] = extra['endpoints']
    manifest['cases'].append(extra)
    manifest['input_hash'] = manifest['source_manifest_hash'] = source_payload_hash(manifest)
    path.write_text(json.dumps(manifest))
    assert run_cli('compare','--config',data/'config.yaml').returncode == 3
    payload = json.loads((data/'numeric_outputs/comparison.json').read_text())
    assert payload['reference']['layers'][0]['line']['panel']['value'] == 400
    for c in ('line','pixel','transfer'):
        assert payload['deltas'][0][c]['value'] is None
        assert 'unmapped_pair' in payload['deltas'][0][c]['reasons']
    assert payload['target']['layers'][0]['line']['panel']['partial_value'] == 150


@pytest.mark.parametrize('mode',['bad_hash','missing_config','malformed_yaml','real_mode'])
def test_bad_input_nonzero_and_no_complete_report(tmp_path,mode):
    generate_numeric_demo(tmp_path/'demo')
    data = tmp_path/'demo'
    config = data/'config.yaml'
    if mode == 'bad_hash':
        (data/'target/pairs.csv').write_text('bad input\n')
    elif mode == 'missing_config':
        config = data/'missing.yaml'
    elif mode == 'malformed_yaml':
        config.write_text('bad: [\n')
    else:
        cfg = yaml.safe_load(config.read_text());cfg['mode']='real';config.write_text(yaml.safe_dump(cfg))
    out = data/'bad_output'
    result = run_cli('compare','--config',config,'--out',out)
    assert result.returncode == 2
    assert '오류:' in result.stderr
    assert not (out/'comparison.json').exists()
    error = json.loads((out/'error.json').read_text())
    assert error['status']=='unavailable' and error['outputs']==[]


def test_new_commands_never_call_old_training_or_prepare(tmp_path,monkeypatch):
    from simcompare import pipeline
    generate_numeric_demo(tmp_path/'demo')
    def forbidden(*args,**kwargs):
        raise AssertionError('old training path called')
    for name in ('prepare','run_train','train_model','run_embed','run_analyze','run_evaluate'):
        assert not hasattr(pipeline,name)
        monkeypatch.setattr(pipeline,name,forbidden,raising=False)
    cfg = tmp_path/'demo/config.yaml'
    assert main(['compare','--config',str(cfg)]) == 0
    assert main(['electrode-map','--config',str(cfg)]) == 0
    assert main(['validate-data','--config',str(cfg)]) == 3
    assert main(['export-review-rois','--config',str(cfg),'--out',str(tmp_path/'roi')]) == 0


def test_absent_representative_diameter_is_unavailable_null_not_zero(tmp_path):
    generate_numeric_demo(tmp_path/'demo')
    config=tmp_path/'demo/config.yaml'
    cfg=yaml.safe_load(config.read_text());cfg['map']['diameter_um']=100
    config.write_text(yaml.safe_dump(cfg))
    result=run_cli('electrode-map','--config',config)
    assert result.returncode==2
    out=tmp_path/'demo/numeric_outputs'
    payload=json.loads((out/'electrode_map.json').read_text())
    assert payload['status']=='unavailable'
    assert payload['layers'][0]['counts'] is None
    with (out/'electrode_map_map.csv').open() as f:
        assert all(r['value']=='null' and r['status']=='unavailable' for r in csv.DictReader(f))


def test_invalid_empty_output_config_has_nonzero_without_traceback(tmp_path):
    generate_numeric_demo(tmp_path/'demo')
    path=tmp_path/'demo/config.yaml'
    cfg=yaml.safe_load(path.read_text());cfg['output_dir']=''
    path.write_text(yaml.safe_dump(cfg))
    result=run_cli('compare','--config',path)
    assert result.returncode==2 and '오류:' in result.stderr
    assert 'Traceback' not in result.stderr


def test_execution_marker_identifies_failed_rerun_and_recovery(tmp_path):
    generate_numeric_demo(tmp_path/'demo')
    config=tmp_path/'demo/config.yaml'
    out=tmp_path/'out'
    assert main(['compare','--config',str(config),'--out',str(out)])==0
    bad=tmp_path/'bad.yaml';bad.write_text('schema_version: wrong\n')
    assert main(['compare','--config',str(bad),'--out',str(out)])==2
    state=json.loads((out/'execution.json').read_text())
    assert state['status']=='unavailable' and state['outputs']==[]
    assert main(['compare','--config',str(config),'--out',str(out)])==0
    assert json.loads((out/'execution.json').read_text())['status']=='available'


def test_explicit_cli_and_config_threshold_create_regions(tmp_path):
    generate_numeric_demo(tmp_path/'demo')
    path=tmp_path/'demo/config.yaml'
    result=run_cli('electrode-map','--config',path,'--min-count','3')
    assert result.returncode==0,result.stderr
    out=tmp_path/'demo/numeric_outputs'
    payload=json.loads((out/'electrode_map.json').read_text())
    assert payload['provenance']['threshold']==3
    assert len(payload['layers'][0]['regions'])==1
    assert payload['layers'][0]['regions'][0]['bbox_yx']==[0,1,1,5]
    cfg=yaml.safe_load(path.read_text());cfg['map']['min_count']=3
    cfg['review_rois']={'map_region_ids':'all'}
    path.write_text(yaml.safe_dump(cfg))
    result=run_cli('export-review-rois','--config',path,'--out',tmp_path/'roi')
    assert result.returncode==0,result.stderr
    rois=json.loads((tmp_path/'roi/review_rois.json').read_text())
    assert len(rois['rois'])==4
    assert all(r['status']=='ready' and 'map_scope_unconfirmed' not in r['reasons'] for r in rois['rois'])


def test_subprocess_failure_pieces_match_hand_json_and_csv_existing_values_unchanged(tmp_path):
    data=tmp_path/'demo'
    assert run_cli('demo-data','--numeric-only','--out',data).returncode==0
    assert run_cli('compare','--config',data/'config.yaml').returncode==0
    out=data/'numeric_outputs';payload=json.loads((out/'comparison.json').read_text())
    expected=json.loads((EXPECTED/'failure_pieces.json').read_text())
    actual={}
    for side in ('reference','target'):
        g=next(g for g in payload[side]['areas']['groups'] if g['group']['diameter_um']==5)
        actual[side+'_d5']={n:m['value'] for n,m in g['failure_pieces'].items()}
        actual[side+'_panel']={n:m['panel']['value'] for n,m in payload[side]['layers'][0]['failure_pieces'].items()}
    actual['delta_panel']={n:m['value'] for n,m in payload['deltas'][0]['failure_pieces'].items()}
    assert actual==expected
    assert projection(payload)==json.loads((EXPECTED/'comparison.json').read_text())
    with (EXPECTED/'failure_pieces_panel.csv').open() as f:expected_csv=list(csv.DictReader(f))
    with (out/'comparison_failure_pieces.csv').open() as f:
        actual_csv=[{k:r[k] for k in expected_csv[0]} for r in csv.DictReader(f) if r['domain']=='panel']
    assert actual_csv==expected_csv
    html=(out/'comparison.html').read_text()
    assert '<h2>불량 유형 조각</h2><table>' in html
    assert 'line / pixel과 다른 축' in html
    assert payload['applied_gate_decisions']['G7a']['implementation_status']=='implemented'
