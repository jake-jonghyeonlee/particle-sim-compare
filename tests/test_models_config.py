"""compare-multiproduct/T1: compare_models_v1 (models.yaml) loader.

Model datasets are copies of the synthetic demo target dataset with another product_id (and the manifest hash recomputed).
"""
import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml

from simcompare.cli import main
from simcompare.models import ASSETS_PENDING, CONFIGURED, check_product_keys, load_models, model_pair_id
from simcompare.numeric_schema import NumericContractError
from simcompare.pipeline import load_numeric_config


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def demo(tmp_path):
    root = tmp_path/'demo'
    assert main(['demo-data', '--out', str(root)]) == 0
    return root


def make_model(demo, key, *, frame_id=None, pitch=None, other_design=False, unit=None):
    """Copy demo/target to demo/models/<key> as product <key>; optional frame rename, pixel pitch or second design version."""
    from simcompare.adapters.numeric import source_payload_hash
    root = demo/'models'/key
    shutil.copytree(demo/'target', root)
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    for case in manifest['cases']:
        case['product_id'] = key
        if frame_id is not None:
            case['frame_id'] = frame_id
    if other_design:
        manifest['cases'][0]['design_version'] = 'other_design'
    for frame in manifest['frames']:
        if frame_id is not None:
            frame['frame_id'] = frame_id
        if pitch is not None:
            frame['pixel_pitch_um'] = pitch
    domain = manifest['unit_domain']
    if frame_id is not None:
        domain['frame_id'] = frame_id
    if pitch is not None:   # keep the measured domain equal to the (larger) frame extent: 3x2 pixels at `pitch` um
        domain.update(unit_width_um=3*pitch, unit_height_um=2*pitch, measured_width_um=3*pitch, measured_height_um=2*pitch)
    manifest['input_hash'] = manifest['source_manifest_hash'] = source_payload_hash(manifest)
    (root/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return root


def model_section(demo, key, *, with_assets=True):
    config = yaml.safe_load((demo/'config.yaml').read_text(encoding='utf-8'))
    section = copy.deepcopy(config['target'])
    section['data_dir'] = f'models/{key}'
    section['product_id'] = key
    if with_assets and (demo/'models'/key/'review_assets.json').exists():
        section['asset_manifest'] = {'path': 'review_assets.json', 'sha256': sha(demo/'models'/key/'review_assets.json')}
    else:
        section.pop('asset_manifest', None)
    return section


def write_models(demo, sections, *, target='target', name='models.yaml', folder=None):
    path = (folder or demo)/name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({'schema_version': 'compare_models_v1', 'target_product_id': target, 'models': sections},
                                   allow_unicode=True), encoding='utf-8')
    return path


def cfg_of(demo):
    return load_numeric_config(demo/'config.yaml')


def test_configured_and_pending_models_load_without_rewriting_paths(demo):
    make_model(demo, 'C')
    path = write_models(demo, {'C': model_section(demo, 'C'), 'D': model_section(demo, 'D')})   # D: no folder yet
    loaded = load_models(path, cfg_of(demo))
    assert loaded['target_product_id'] == 'target' and loaded['sha256'] == sha(path)
    c, d = loaded['models']['C'], loaded['models']['D']
    assert (c['status'], d['status']) == (CONFIGURED, ASSETS_PENDING)
    assert c['data_dir'] == 'models/C' and c['product'] == 'C'                   # the written string, not a rewritten path
    assert c['manifest_sha256'] == sha(demo/'models'/'C'/'manifest.json')
    assert c['asset_manifest_sha256'] == sha(demo/'models'/'C'/'review_assets.json')
    assert c['electrode_import_record_sha256'] is None
    assert 'dataset' not in d and d['section']['data_dir'] == 'models/D'


def test_relative_paths_resolve_against_the_config_folder_not_the_cwd(demo, tmp_path, monkeypatch):
    make_model(demo, 'C')
    path = write_models(demo, {'C': model_section(demo, 'C')})
    cfg = cfg_of(demo)
    elsewhere = tmp_path/'elsewhere'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    loaded = load_models(path, cfg)
    assert loaded['models']['C']['status'] == CONFIGURED
    assert Path(loaded['models']['C']['root']) == demo/'models'/'C'


@pytest.mark.parametrize('mutate, code', [
    (lambda data: data.update(schema_version='compare_models_v2'), 'unsupported_models_config'),
    (lambda data: data.update(target_product_id='reference'), 'models_target_mismatch'),
    (lambda data: data.update(unknown=1), 'invalid_models_config'),
    (lambda data: data.update(models={}), 'invalid_models_config'),
    (lambda data: data['models'].update(reference=data['models']['C']), 'models_product_is_config_side'),
    (lambda data: data['models'].update(target=data['models']['C']), 'models_product_is_config_side'),
    (lambda data: data['models'].update({'../C': data['models']['C']}), 'models_product_id_unsafe'),
    (lambda data: data['models'].update({'/abs': data['models']['C']}), 'models_product_id_unsafe'),
    (lambda data: data['models'].update({'c': data['models']['C']}), 'models_product_id_unsafe'),
    (lambda data: data['models'].update({'..': data['models']['C']}), 'models_product_id_unsafe'),
    (lambda data: data['models'].update({'a'*65: data['models']['C']}), 'models_product_id_unsafe'),
    (lambda data: data['models']['C'].update(product_id='Z'), 'models_section_product_mismatch'),
])
def test_loader_rejects_with_a_code(demo, mutate, code):
    make_model(demo, 'C')
    data = {'schema_version': 'compare_models_v1', 'target_product_id': 'target', 'models': {'C': model_section(demo, 'C')}}
    mutate(data)
    path = demo/'models.yaml'
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding='utf-8')
    with pytest.raises(NumericContractError) as caught:
        load_models(path, cfg_of(demo))
    assert caught.value.code == code


def test_models_yaml_must_sit_next_to_the_config(demo):
    make_model(demo, 'C')
    path = write_models(demo, {'C': model_section(demo, 'C')}, folder=demo/'sub')
    with pytest.raises(NumericContractError) as caught:
        load_models(path, cfg_of(demo))
    assert caught.value.code == 'models_config_dir_mismatch'


def test_dataset_product_must_equal_the_key(demo):
    make_model(demo, 'C')
    section = model_section(demo, 'C')
    section['data_dir'] = 'target'                      # a folder that holds product "target", declared as model C
    section.pop('asset_manifest')
    path = write_models(demo, {'C': section})
    with pytest.raises(NumericContractError) as caught:
        load_models(path, cfg_of(demo))
    assert caught.value.code in ('single_declared_product_required', 'models_dataset_product_mismatch')


def test_manifest_present_but_broken_is_an_error_not_pending(demo):
    root = make_model(demo, 'C')
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    manifest['input_hash'] = '0'*64                                        # contract violation
    (root/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    path = write_models(demo, {'C': model_section(demo, 'C')})
    with pytest.raises(NumericContractError):
        load_models(path, cfg_of(demo))


def test_declared_asset_file_that_is_missing_is_an_error(demo):
    make_model(demo, 'C')
    section = model_section(demo, 'C')
    section['asset_manifest'] = {'path': 'no_such.json', 'sha256': '0'*64}
    path = write_models(demo, {'C': section})
    with pytest.raises(NumericContractError) as caught:
        load_models(path, cfg_of(demo))
    assert caught.value.code == 'models_asset_file_unreadable'


def test_config_real_mismatch_is_refused(demo):
    make_model(demo, 'C')
    path = write_models(demo, {'C': model_section(demo, 'C')})
    with pytest.raises(NumericContractError) as caught:
        load_models(path, cfg_of(demo), config_real=True)
    assert caught.value.code == 'mixed_real_and_numeric_source_formats'


def test_key_rule_accepts_ordinary_ids():
    check_product_keys(['QA4_C00', 'a', 'A.b-c_9', 'x'*64])
    for bad in ('', '-a', '.a', 'a b', 'ä', '한글', 'a/b', 'a'*65, None, 7):
        with pytest.raises(NumericContractError):
            check_product_keys([bad])


def test_model_pair_id_depends_on_product_and_inputs_only():
    base = model_pair_id('target_to_reference', 'roi1', 1, 'C', (1.0, 2.0))
    assert base == model_pair_id('target_to_reference', 'roi1', 1, 'C', (1.0, 2.0))
    assert base.startswith('confirmed_model_')
    others = {model_pair_id('target_to_reference', 'roi1', 2, 'C', (1.0, 2.0)),
              model_pair_id('target_to_reference', 'roi1', 1, 'D', (1.0, 2.0)),
              model_pair_id('target_to_reference', 'roi2', 1, 'C', (1.0, 2.0)),
              model_pair_id('target_to_reference', 'roi1', 1, 'C', (1.0, 2.5))}
    assert base not in others and len(others) == 4


def test_example_models_yaml_parses_and_keys_pass_the_rule():
    root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((root/'configs/etx/models.example.yaml').read_text(encoding='utf-8'))
    assert data['schema_version'] == 'compare_models_v1' and data['target_product_id'] == 'QA4_T00'
    check_product_keys(list(data['models']))
    assert all(section['data_dir'] == f'data/models/{key}' for key, section in data['models'].items())
