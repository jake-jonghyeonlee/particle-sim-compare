"""T1b: real_hotspot_v1 route of export-review-rois (configured_dataset + electrode-map regions + T1a assets)."""
import hashlib
import json
from pathlib import Path
import shutil

import pytest
import yaml

from simcompare.cli import main
from simcompare.adapters.real_hotspot import import_hotspots
from test_electrode_png import CASE, cnt_root, real_source

SHORT_CIRCUIT = ['electrode_polarity_unknown', 'source_layout_assets_unconfirmed']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(tmp_path, roles=None):
    """hotspot import + import-electrode-assets output merged into one data_dir (export reads assets under data_dir)."""
    root = real_source(tmp_path)
    expected = tmp_path / 'expected.txt'
    expected.write_text(CASE + '\n')
    imported = tmp_path / 'imported'
    import_hotspots(root, imported, expected_cases=expected)
    (imported / 'pairs.csv').write_text('pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\nAB,A,B,line,pair-v1\n')
    assignment = {'product_id': 'PRODUCT', 'design_version': 'DESIGN', 'source_id': 'SOURCE', 'process_step_id': 'STEP',
                  'exposure_id': 'EXPOSURE', 'unit_id': 'UNIT', 'pair_table_path': 'pairs.csv',
                  'layer_stack': [{'actual_layer': 'L1', 'stack_order': 1, 'density_process_layer': 'P1'}]}
    if roles is not None:
        assignment['electrode_roles'] = dict(roles)
    assignment_path = tmp_path / 'assignment.json'
    assignment_path.write_text(json.dumps(assignment))
    adapter = tmp_path / 'adapter'
    code = main(['import-electrode-assets', '--source-kind', 'hotspot_case_electrode', '--root', str(root),
                 '--numeric-data-dir', str(imported), '--condition-assignment', str(assignment_path),
                 '--cnt-from-dl1-root', str(cnt_root(tmp_path)), '--source-version', 'layout_v1',
                 '--max-asset-memory-mb', '256', '--out', str(adapter)])
    assert code == 0
    data = tmp_path / 'data'
    shutil.copytree(imported, data)
    (data / 'assets').mkdir(exist_ok=True)
    for name in ('review_assets.json', 'electrode_asset_import_record.json'):
        shutil.copyfile(adapter / name, data / name)
    for path in (adapter / 'assets').iterdir():
        shutil.copyfile(path, data / 'assets' / path.name)
    return data, assignment


def config(tmp_path, data, assignment, *, record=True, selections=None, min_count=1):
    spec = {'data_dir': str(data), 'condition_assignment': assignment,
            'asset_manifest': {'path': 'review_assets.json', 'sha256': sha(data / 'review_assets.json')}}
    if record:
        spec['electrode_import_record'] = {'path': 'electrode_asset_import_record.json',
                                           'sha256': sha(data / 'electrode_asset_import_record.json')}
    cfg = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'unused',
           'map': {'input': spec, 'diameter_um': 1},
           'review_rois': selections if selections is not None else {'map_region_ids': 'all'}}
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(cfg))
    return path, cfg


def export(tmp_path, cfg_path, *extra):
    out = tmp_path / 'roi'
    code = main(['export-review-rois', '--config', str(cfg_path), '--out', str(out), *extra])
    return code, out


def rois(out):
    return json.loads((out / 'review_rois.json').read_text())


def rewrite(path, change):
    doc = json.loads(path.read_text())
    change(doc)
    path.write_text(json.dumps(doc, indent=2))


def test_end_to_end_ready_roi_from_map_region_with_both_provenances(tmp_path):
    data, assignment = build(tmp_path)
    cfg_path, _ = config(tmp_path, data, assignment)
    code, out = export(tmp_path, cfg_path, '--min-count', '1')
    payload = rois(out)
    assert code == 0, [r['reasons'] for r in payload['rois']]
    ready = [r for r in payload['rois'] if r['status'] == 'ready']
    assert ready and all(r['source_kind'] == 'electrode_count_region' and r['source_region_id'] for r in ready)
    row = ready[0]
    assert row['case_id'] == CASE and row['reasons'] == []
    # hotspot-derived region position + PNG-derived layout asset, both on the one row
    assert row['provenance']['position_policy'] == 'region_bbox_center' and row['region_bbox_um']
    assert [e['asset']['value_semantics'] for e in row['source_assets']['layouts']] == ['binary_zero_is_marked'] * 2
    assert not [r for r in payload['rois'] if r['source_kind'] != 'electrode_count_region']
    assert payload['source_manifest_hash'] == json.loads((data / 'review_assets.json').read_text())['source_manifest_hash']


def test_region_ids_selection_picks_only_named_region(tmp_path):
    data, assignment = build(tmp_path)
    cfg_path, _ = config(tmp_path, data, assignment)
    _, out = export(tmp_path, cfg_path, '--min-count', '1')
    region_id = rois(out)['rois'][0]['source_region_id']
    cfg_path, _ = config(tmp_path, data, assignment, selections={'map_region_ids': [region_id, 'nope']})
    shutil.rmtree(out)
    code, out = export(tmp_path, cfg_path, '--min-count', '1')
    payload = rois(out)
    assert {r['source_region_id'] for r in payload['rois']} == {region_id}
    assert code == 3 and payload['excluded_records'][-1]['reasons'] == ['source_region_missing']


def baseline_short_circuit(tmp_path, cfg_path):
    code, out = export(tmp_path, cfg_path)
    return code, (out / 'review_rois.json').read_bytes(), (out / 'review_rois.csv').read_bytes()


def test_without_record_keeps_unavailable_short_circuit(tmp_path):
    data, assignment = build(tmp_path)
    with_key, _ = config(tmp_path, data, assignment, record=False)
    code, out = export(tmp_path, with_key, '--min-count', '1')
    payload = rois(out)
    assert code == 3 and payload['status'] == 'unavailable' and payload['reasons'] == SHORT_CIRCUIT
    assert payload['rois'] == []
    # identical to a config that never mentions the new evidence (old behaviour)
    shutil.rmtree(out)
    _, cfg = config(tmp_path, data, assignment, record=False)
    cfg['map']['input'].pop('asset_manifest')
    cfg['map']['input'].pop('condition_assignment')
    plain = tmp_path / 'plain.yaml'
    plain.write_text(yaml.safe_dump(cfg))
    code2, out2 = export(tmp_path, plain, '--min-count', '1')
    assert code2 == 3 and (out2 / 'review_rois.json').read_bytes() == json.dumps(payload, ensure_ascii=False, indent=2).encode()


def test_record_without_polarity_basis_is_old_unavailable(tmp_path):
    data, assignment = build(tmp_path)
    rewrite(data / 'electrode_asset_import_record.json', lambda d: d.pop('polarity_basis'))
    cfg_path, _ = config(tmp_path, data, assignment)
    code, out = export(tmp_path, cfg_path, '--min-count', '1')
    payload = rois(out)
    assert code == 3 and payload['status'] == 'unavailable' and payload['reasons'] == SHORT_CIRCUIT and payload['rois'] == []


def test_assignment_missing_keeps_short_circuit(tmp_path):
    data, assignment = build(tmp_path)
    cfg_path, cfg = config(tmp_path, data, assignment)
    cfg['map']['input'].pop('condition_assignment')
    cfg_path.write_text(yaml.safe_dump(cfg))
    code, out = export(tmp_path, cfg_path, '--min-count', '1')
    assert code == 3 and rois(out)['reasons'] == SHORT_CIRCUIT


def test_manifest_hash_not_matching_dataset_is_named_error_and_no_rois(tmp_path):
    data, assignment = build(tmp_path)
    def change(doc):
        doc['source_manifest_hash'] = '0' * 64
    rewrite(data / 'review_assets.json', change)
    rewrite(data / 'electrode_asset_import_record.json',
            lambda d: d.update(manifest_sha256=sha(data / 'review_assets.json'), dataset_input_hash='0' * 64))
    cfg_path, _ = config(tmp_path, data, assignment)
    code, out = export(tmp_path, cfg_path, '--min-count', '1')
    assert code == 2
    error = json.loads((out / 'error.json').read_text())
    assert error['reasons'] == ['source_asset_manifest_identity_mismatch']
    assert not (out / 'review_rois.json').exists()


def test_record_not_bound_to_manifest_is_named_error(tmp_path):
    data, assignment = build(tmp_path)
    rewrite(data / 'electrode_asset_import_record.json', lambda d: d.update(manifest_sha256='1' * 64))
    cfg_path, _ = config(tmp_path, data, assignment)
    code, out = export(tmp_path, cfg_path, '--min-count', '1')
    assert code == 2 and json.loads((out / 'error.json').read_text())['reasons'] == ['electrode_import_record_mismatch']


def test_unaccepted_value_semantics_is_blocked(tmp_path):
    data, assignment = build(tmp_path)
    def change(doc):
        doc['cases'][0]['assets']['layouts'][0]['asset']['value_semantics'] = 'greyscale'
    rewrite(data / 'review_assets.json', change)
    rewrite(data / 'electrode_asset_import_record.json', lambda d: d.update(manifest_sha256=sha(data / 'review_assets.json')))
    cfg_path, _ = config(tmp_path, data, assignment)
    code, out = export(tmp_path, cfg_path, '--min-count', '1')
    payload = rois(out)
    assert code == 3 and payload['status'] == 'blocked' and payload['reasons'] == ['electrode_polarity_unspecified']
    assert payload['rois'] == []


@pytest.mark.parametrize('field,value', [('source_kind', 'mystery_folder'), ('polarity_basis', 'guess')])
def test_unknown_source_kind_or_basis_is_blocked(tmp_path, field, value):
    data, assignment = build(tmp_path)
    def manifest_change(doc):
        if field == 'source_kind':
            doc['electrode_asset_import']['source_kind'] = value
        else:
            doc['electrode_asset_import']['polarity_basis'] = value
    rewrite(data / 'review_assets.json', manifest_change)
    rewrite(data / 'electrode_asset_import_record.json',
            lambda d: d.update({'manifest_sha256': sha(data / 'review_assets.json'), field: value}))
    cfg_path, _ = config(tmp_path, data, assignment)
    code, out = export(tmp_path, cfg_path, '--min-count', '1')
    payload = rois(out)
    assert code == 3 and payload['status'] == 'blocked' and payload['reasons'] == ['electrode_polarity_unspecified']


@pytest.mark.parametrize('which', ['asset_manifest', 'electrode_import_record'])
def test_sha256_mismatch_is_named_error(tmp_path, which):
    data, assignment = build(tmp_path)
    cfg_path, cfg = config(tmp_path, data, assignment)
    cfg['map']['input'][which]['sha256'] = '2' * 64
    cfg_path.write_text(yaml.safe_dump(cfg))
    code, out = export(tmp_path, cfg_path, '--min-count', '1')
    assert code == 2 and json.loads((out / 'error.json').read_text())['reasons'] == ['input_hash_mismatch']


def test_record_reference_must_be_path_and_sha_only(tmp_path):
    data, assignment = build(tmp_path)
    cfg_path, cfg = config(tmp_path, data, assignment)
    cfg['map']['input']['electrode_import_record']['extra'] = 1
    cfg_path.write_text(yaml.safe_dump(cfg))
    code, out = export(tmp_path, cfg_path, '--min-count', '1')
    assert code == 2 and json.loads((out / 'error.json').read_text())['reasons'] == ['invalid_numeric_config']



def test_evidence_swapped_after_hash_check_is_not_accepted(tmp_path, monkeypatch):
    """B4: 해시를 검증한 바이트와 파싱한 바이트가 같아야 한다. 해시 읽기 직후 파일을 유효한 기록으로 바꿔 끼운다."""
    from simcompare import pipeline
    data, assignment = build(tmp_path)
    _, cfg = config(tmp_path, data, assignment)
    record = data / 'electrode_asset_import_record.json'
    good = record.read_bytes()
    bad = json.loads(good)
    bad['polarity_basis'] = None
    record.write_text(json.dumps(bad))
    section = cfg['map']['input']
    section['electrode_import_record']['sha256'] = sha(record)
    assert pipeline._electrode_evidence(data, section) is None          # 고정된 기록은 증거 없음(기존 단락)
    original, swapped = Path.read_bytes, []

    def read_then_swap(self):
        content = original(self)
        if self.resolve() == record.resolve() and not swapped:
            swapped.append(True)
            record.write_bytes(good)
        return content
    record.write_text(json.dumps(bad))
    monkeypatch.setattr(Path, 'read_bytes', read_then_swap)
    result = pipeline._electrode_evidence(data, section)
    monkeypatch.undo()
    assert swapped and sha(record) != section['electrode_import_record']['sha256']
    assert result is None, result                                          # 'accepted' 이면 안 된다


def test_manifest_changed_during_export_read_is_named_error(tmp_path, monkeypatch):
    from simcompare import pipeline, roi_export
    from simcompare.numeric_schema import NumericContractError
    data, assignment = build(tmp_path)
    _, cfg = config(tmp_path, data, assignment)
    real_export = roi_export.export_review_rois

    def swapping(*args, **kwargs):
        result = real_export(*args, **kwargs)
        with (data / 'review_assets.json').open('a', encoding='utf-8') as handle:
            handle.write(' ')                                              # export가 읽은 뒤 manifest 바이트가 바뀐다
        return result
    monkeypatch.setattr(roi_export, 'export_review_rois', swapping)
    with pytest.raises(NumericContractError) as caught:
        pipeline.run_numeric_roi_export(cfg, tmp_path / 'roi')
    assert caught.value.code == 'evidence_changed_during_read'


def test_duplicate_key_in_evidence_is_still_rejected_from_the_hashed_bytes(tmp_path):
    from simcompare import pipeline
    from simcompare.numeric_schema import NumericContractError
    data, assignment = build(tmp_path)
    _, cfg = config(tmp_path, data, assignment)
    manifest = data / 'review_assets.json'
    manifest.write_text(manifest.read_text().rstrip()[:-1] + ', "schema_version": "review_assets_v1"}')
    section = cfg['map']['input']
    section['asset_manifest']['sha256'] = sha(manifest)
    with pytest.raises(NumericContractError) as caught:
        pipeline._electrode_evidence(data, section)
    assert caught.value.code == 'malformed_asset_manifest' and 'duplicate_asset_manifest_key' in str(caught.value)
