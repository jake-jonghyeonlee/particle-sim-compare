"""vdist-filter/T5: input locations. New mode = relative to the config folder, old mode = relative to data_dir (baseline).

Unit tests of the resolver (numeric_images.locate_file / locate_assets) plus runs of candidates-dv, 03 --config,
vulnerability-report and the models loader on the synthetic dv world, with configs in the new form.
"""
import copy
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from simcompare.cli import main
from simcompare.numeric_images import locate_assets, locate_file
from simcompare.numeric_schema import NumericContractError
from simcompare.pipeline import load_numeric_config
from test_dv_candidates import World, standard_cases, TWIST, build_product, sha


# ---- resolver ------------------------------------------------------------------------------------------------

def _tree(tmp_path):
    config, data = tmp_path / 'cfg', tmp_path / 'cfg' / 'data'
    data.mkdir(parents=True)
    return config, data


def test_new_location_only_is_new_mode_and_png_base_is_the_written_manifest_parent(tmp_path):
    config, data = _tree(tmp_path)
    (config / 'a').mkdir()
    (config / 'a' / 'm.json').write_text('{}')
    (config / 'a' / 'r.json').write_text('{}')
    found = locate_assets(config, data, 'a/m.json', 'a/r.json')
    assert found.mode == 'new' and found.manifest_path == (config / 'a' / 'm.json').resolve()
    assert found.asset_root == config / 'a' and found.record_path == (config / 'a' / 'r.json').resolve()


def test_old_location_only_is_old_mode_even_with_a_subfolder_manifest(tmp_path):
    config, data = _tree(tmp_path)
    (data / 'meta').mkdir()
    (data / 'meta' / 'm.json').write_text('{}')
    (data / 'r.json').write_text('{}')
    found = locate_assets(config, data, 'meta/m.json', 'r.json')
    assert found.mode == 'old' and found.asset_root == data          # the baseline: PNG paths are relative to data_dir
    assert found.record_path == (data / 'r.json').resolve()


def test_both_locations_conflict_even_with_equal_content(tmp_path):
    config, data = _tree(tmp_path)
    for folder in (config, data):
        (folder / 'm.json').write_text('same')
    with pytest.raises(NumericContractError) as caught:
        locate_file(config, data, 'm.json', 'asset_manifest.path')
    assert caught.value.code == 'input_location_conflict'
    # the config folder being the data folder is one location, not a conflict
    assert locate_file(config, config, 'm.json', 'k').mode == 'new'


def test_symlinked_manifest_keeps_the_link_parent_as_png_base(tmp_path):
    config, data = _tree(tmp_path)
    (config / 'real').mkdir()
    (config / 'real' / 'm.json').write_text('{}')
    (config / 'link').mkdir()
    os.symlink(config / 'real' / 'm.json', config / 'link' / 'm.json')
    (config / 'r.json').write_text('{}')
    found = locate_assets(config, data, 'link/m.json', 'r.json')
    assert found.mode == 'new' and found.asset_root == config / 'link'
    assert found.manifest_path == (config / 'real' / 'm.json').resolve()


def test_broken_symlink_directory_and_escape_never_fall_back(tmp_path):
    config, data = _tree(tmp_path)
    os.symlink(tmp_path / 'nowhere', config / 'x.json')
    (config / 'd').mkdir()
    for value, code in (('x.json', 'missing_or_escaping_path'), ('d', 'asset_not_file')):
        with pytest.raises(NumericContractError) as caught:
            locate_file(config, data, value, 'k')
        assert caught.value.code == code, value
    (tmp_path / 'outside.json').write_text('{}')
    (data / 'up.json').write_text('{}')
    for value in ('../outside.json', str(tmp_path / 'outside.json'), 'a\\b.json'):
        with pytest.raises(NumericContractError):
            locate_file(config, data, value, 'k')
    os.symlink(tmp_path / 'outside.json', config / 'out.json')      # a symlink out of the config folder
    with pytest.raises(NumericContractError):
        locate_file(config, data, 'out.json', 'k')


def test_interlayer_distance_path_is_config_folder_only(tmp_path):
    config, data = _tree(tmp_path)
    (data / 'thk.txt').write_text('x')
    with pytest.raises(NumericContractError):       # no fallback for the new key
        locate_file(config, None, 'thk.txt', 'interlayer_distance_path')
    (config / 'thk.txt').write_text('x')
    assert locate_file(config, None, 'thk.txt', 'interlayer_distance_path').path == (config / 'thk.txt').resolve()
    with pytest.raises(NumericContractError):
        locate_file(config, None, '../thk.txt', 'interlayer_distance_path')


# ---- runs ----------------------------------------------------------------------------------------------------

@pytest.fixture(scope='module')
def world(tmp_path_factory):
    return World(tmp_path_factory.mktemp('inpaths'), standard_cases(), standard_cases(), tgt_types=TWIST)


@pytest.fixture(scope='module')
def baseline(world):
    assert world.run('baseline') == 0
    return world.base / 'baseline'


def run_with(world, config, out, **kwargs):
    saved = world.config
    world.config = config
    try:
        return world.run(out, **kwargs)
    finally:
        world.config = saved


def new_config(world, name, refs, aliases=True, interlayer=None):
    """A copy of the world's config whose asset refs are given per side as {'asset_manifest': .., 'electrode_import_record': ..}."""
    cfg = yaml.safe_load(world.config.read_text(encoding='utf-8'))
    for side in ('reference', 'target'):
        for key in ('asset_manifest', 'electrode_import_record'):
            if refs[side].get(key) is None:
                cfg[side].pop(key, None)
            else:
                cfg[side][key] = refs[side][key]
        if interlayer:
            cfg[side]['condition_assignment']['interlayer_distance_path'] = interlayer
    if aliases:     # yaml.safe_dump writes the shared layer_stack as an anchor and an alias
        cfg['target']['condition_assignment']['layer_stack'] = cfg['reference']['condition_assignment']['layer_stack']
    path = world.base / name
    path.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    return path


@pytest.fixture(scope='module')
def regenerated(world):
    """03 --config real.yaml --side X for both sides; the outputs stay where 03 wrote them (no copy)."""
    for side in ('reference', 'target'):
        code = main(['import-electrode-assets', '--source-kind', 'hotspot_case_electrode', '--root', str(world.base / f'src_{side}'),
                     '--config', str(world.config), '--side', side, '--cnt-from-dl1-root', str(world.base / f'cnt_{side}'),
                     '--source-version', 'layout_v1', '--max-asset-memory-mb', '256', '--out', str(world.base / f'new_{side}')])
        assert code == 0
    return {side: world.base / f'new_{side}' for side in ('reference', 'target')}


def refs_for(world, folders, *, manifest_sha=False, record_sha=False):
    result = {}
    for side, folder in folders.items():
        rel = os.path.relpath(folder, world.base)
        result[side] = {'asset_manifest': {'path': f'{rel}/review_assets.json'},
                        'electrode_import_record': {'path': f'{rel}/electrode_asset_import_record.json'}}
        if manifest_sha:
            result[side]['asset_manifest']['sha256'] = sha(folder / 'review_assets.json')
        if record_sha:
            result[side]['electrode_import_record']['sha256'] = sha(folder / 'electrode_asset_import_record.json')
    return result


def test_03_with_config_gives_the_same_dataset_hash_as_the_json_assignment(world, regenerated):
    for side in ('reference', 'target'):
        old = json.loads((world.base / f'data_{side}' / 'electrode_asset_import_record.json').read_text(encoding='utf-8'))
        new = json.loads((regenerated[side] / 'electrode_asset_import_record.json').read_text(encoding='utf-8'))
        assert new['dataset_input_hash'] == old['dataset_input_hash']


def test_new_mode_without_sha_runs_candidates_dv_and_the_report(world, regenerated):
    config = new_config(world, 'real_nosha.yaml', refs_for(world, regenerated))
    assert '*id001' in config.read_text(encoding='utf-8')           # the YAML alias is part of this test
    assert run_with(world, config, 'nosha') == 0
    out = world.base / 'report_nosha'
    assert main(['vulnerability-report', '--candidates-dv-dir', str(world.base / 'nosha'), '--out', str(out)]) == 0
    texts = ' '.join(p.read_text(encoding='utf-8', errors='ignore') for p in (world.base / 'nosha').rglob('*.json'))
    assert '"resolved_path"' in texts and '"asset_root"' in texts       # where the verified files are, for the report


@pytest.mark.parametrize('manifest_sha,record_sha', [(True, True), (True, False), (False, True)])
def test_sha_combinations_all_succeed(world, regenerated, manifest_sha, record_sha):
    config = new_config(world, 'real_combo.yaml', refs_for(world, regenerated, manifest_sha=manifest_sha, record_sha=record_sha))
    assert run_with(world, config, f'combo_{int(manifest_sha)}{int(record_sha)}') == 0


def test_stated_sha_is_compared_strictly(world, regenerated, capsys):
    refs = refs_for(world, regenerated, manifest_sha=True)
    refs['reference']['asset_manifest']['sha256'] = '0' * 64
    assert run_with(world, new_config(world, 'real_bad.yaml', refs), 'bad_sha') == 2
    assert 'input_hash_mismatch' in capsys.readouterr().err


def test_a_manifest_from_another_run_fails_the_chain(world, regenerated):
    swapped = world.base / 'swapped_reference'
    shutil.copytree(regenerated['reference'], swapped)
    tampered = json.loads((swapped / 'review_assets.json').read_text(encoding='utf-8'))
    tampered['note'] = 'another run'
    (swapped / 'review_assets.json').write_text(json.dumps(tampered), encoding='utf-8')
    folders = {**regenerated, 'reference': swapped}
    assert run_with(world, new_config(world, 'real_swap.yaml', refs_for(world, folders)), 'swap') == 2


def test_old_mode_with_a_subfolder_manifest_equals_the_baseline_output(world, baseline):
    data = world.base / 'data_reference'
    (data / 'meta').mkdir(exist_ok=True)
    shutil.copyfile(data / 'review_assets.json', data / 'meta' / 'review_assets.json')
    cfg = yaml.safe_load(world.config.read_text(encoding='utf-8'))
    cfg['reference']['asset_manifest'] = {'path': 'meta/review_assets.json', 'sha256': sha(data / 'meta' / 'review_assets.json')}
    path = world.base / 'real_meta.yaml'
    path.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    assert run_with(world, path, 'meta_out') == 0
    out = world.base / 'meta_out'
    names = sorted(str(p.relative_to(out)) for p in out.rglob('*') if p.is_file())
    assert names == sorted(str(p.relative_to(baseline)) for p in baseline.rglob('*') if p.is_file())
    for name in names:
        if name == 'execution.json':
            continue
        new, old = (out / name).read_bytes(), (baseline / name).read_bytes()
        if b'meta/review_assets' not in new:        # only the recorded asset path may differ
            assert new == old, name


def test_old_form_config_equals_the_baseline_output(world, baseline):
    assert world.run('again') == 0
    again = world.base / 'again'
    for path in baseline.rglob('*'):
        if path.is_file() and path.name != 'execution.json':
            assert (again / path.relative_to(baseline)).read_bytes() == path.read_bytes(), path.name


def test_both_locations_present_is_a_conflict_exit_2(world, capsys):
    stray = world.base / 'review_assets.json'
    shutil.copyfile(world.base / 'data_reference' / 'review_assets.json', stray)
    try:
        assert world.run('conflict') == 2
    finally:
        stray.unlink()
    assert 'input_location_conflict' in capsys.readouterr().err


def test_escape_in_an_asset_path_is_refused(world):
    refs = {side: {'asset_manifest': {'path': '../review_assets.json'},
                   'electrode_import_record': {'path': 'electrode_asset_import_record.json'}} for side in ('reference', 'target')}
    assert run_with(world, new_config(world, 'real_escape.yaml', refs), 'escape') == 2


def test_interlayer_distance_path_is_allowed_and_must_stay_in_the_config_folder(world):
    (world.base / 'thk_info.txt').write_text('unused here\n')
    # the key is part of the assignment (and its hash), so 03 reads the same config to make matching assets
    plain = {side: {} for side in ('reference', 'target')}
    config03 = new_config(world, 'real_thk03.yaml', plain, interlayer='thk_info.txt')
    folders = {}
    for side in ('reference', 'target'):
        folders[side] = world.base / f'thk_{side}'
        assert main(['import-electrode-assets', '--source-kind', 'hotspot_case_electrode', '--root', str(world.base / f'src_{side}'),
                     '--config', str(config03), '--side', side, '--cnt-from-dl1-root', str(world.base / f'cnt_{side}'),
                     '--source-version', 'layout_v1', '--max-asset-memory-mb', '256', '--out', str(folders[side])]) == 0
    refs = refs_for(world, folders)
    assert run_with(world, new_config(world, 'real_thk.yaml', refs, interlayer='thk_info.txt'), 'thk') == 0
    assert run_with(world, new_config(world, 'real_thk_bad.yaml', refs, interlayer='../thk_info.txt'), 'thk_bad') == 2


def test_03_config_and_condition_assignment_together_are_an_error(world):
    with pytest.raises(SystemExit) as caught:
        main(['import-electrode-assets', '--source-kind', 'hotspot_case_electrode', '--root', str(world.base / 'src_reference'),
              '--config', str(world.config), '--side', 'reference', '--condition-assignment', str(world.base / 'assignment_reference.json'),
              '--source-version', 'v', '--max-asset-memory-mb', '256', '--out', str(world.base / 'never')])
    assert caught.value.code == 2 and not (world.base / 'never').exists()


def test_models_follow_up_with_new_paths_and_no_sha(world):
    from simcompare.models import CONFIGURED, load_models
    data, assignment = build_product(world.base, 'model', 'PM', standard_cases())
    section = {'product_id': 'PM', 'data_dir': str(data), 'condition_assignment': assignment}
    models = world.base / 'models.yaml'
    models.write_text(yaml.safe_dump({'schema_version': 'compare_models_v1', 'target_product_id': 'PB', 'models': {'PM': section}}),
                      encoding='utf-8')
    code = main(['import-electrode-assets', '--source-kind', 'hotspot_case_electrode', '--root', str(world.base / 'src_model'),
                 '--config', str(models), '--model', 'PM', '--cnt-from-dl1-root', str(world.base / 'cnt_model'),
                 '--source-version', 'layout_v1', '--max-asset-memory-mb', '256', '--out', str(world.base / 'new_model')])
    assert code == 0
    section = copy.deepcopy(section)
    section['asset_manifest'] = {'path': 'new_model/review_assets.json'}
    section['electrode_import_record'] = {'path': 'new_model/electrode_asset_import_record.json'}
    models.write_text(yaml.safe_dump({'schema_version': 'compare_models_v1', 'target_product_id': 'PB', 'models': {'PM': section}}),
                      encoding='utf-8')
    loaded = load_models(models, load_numeric_config(world.config))
    entry = loaded['models']['PM']
    assert entry['status'] == CONFIGURED
    assert entry['asset_manifest_sha256'] == sha(world.base / 'new_model' / 'review_assets.json')
    assert entry['electrode_import_record_sha256'] == sha(world.base / 'new_model' / 'electrode_asset_import_record.json')
