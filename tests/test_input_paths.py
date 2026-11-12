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


def test_data_dir_equal_to_the_config_folder_is_old_mode_with_the_data_dir_png_base(tmp_path):
    (tmp_path / 'm.json').write_text('{}')
    (tmp_path / 'r.json').write_text('{}')
    assert locate_file(tmp_path, tmp_path, 'm.json', 'k').mode == 'old'      # one location, not a conflict
    found = locate_assets(tmp_path, tmp_path, 'm.json', 'r.json')
    assert found.mode == 'old' and found.asset_root == tmp_path
    link = tmp_path.parent / (tmp_path.name + '_link')
    os.symlink(tmp_path, link)                                               # data_dir reaching the same folder by a link
    assert locate_file(tmp_path, link, 'm.json', 'k').mode == 'old'


def test_only_absence_falls_back_other_os_errors_are_errors(tmp_path, monkeypatch):
    config, data = _tree(tmp_path)
    (data / 'm.json').write_text('{}')
    real_lstat = os.lstat

    def denied(path, *args, **kwargs):
        if str(path) == str(config / 'm.json'):
            raise PermissionError(13, 'denied')
        return real_lstat(path, *args, **kwargs)
    monkeypatch.setattr('simcompare.numeric_images.os.lstat', denied)
    with pytest.raises(NumericContractError) as caught:
        locate_file(config, data, 'm.json', 'k')
    assert caught.value.code == 'input_unreadable'


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


# ---- pair table in the config folder (the user layout: data/input/pairs.csv shared by both sides) -------------

def user_layout(world, tag):
    """Config in world.base; data/input/pairs.csv (one file, both sides); the data_dirs hold no pairs.csv."""
    pairs = world.base / 'data' / 'input' / 'pairs.csv'
    if not pairs.exists():
        pairs.parent.mkdir(parents=True, exist_ok=True)
        text = (world.base / 'data_reference' / 'pairs.csv').read_text(encoding='utf-8')
        assert text == (world.base / 'data_target' / 'pairs.csv').read_text(encoding='utf-8')     # the same table for both
        pairs.write_text(text, encoding='utf-8')
    cfg = yaml.safe_load(world.config.read_text(encoding='utf-8'))
    for side in ('reference', 'target'):
        copy_dir = world.base / f'u{tag}_{side}'
        if not copy_dir.exists():
            shutil.copytree(world.base / f'data_{side}', copy_dir, ignore=shutil.ignore_patterns(
                'pairs.csv', 'review_assets.json', 'electrode_asset_import_record.json'))
        assert not (copy_dir / 'pairs.csv').exists()
        cfg[side]['data_dir'] = copy_dir.name
        cfg[side]['condition_assignment']['pair_table_path'] = 'data/input/pairs.csv'
        cfg[side].pop('asset_manifest'), cfg[side].pop('electrode_import_record')
    cfg['map']['input'] = cfg['reference']        # validate-data / electrode-map read the reference side
    path = world.base / f'real_user_{tag}.yaml'
    path.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    return path


def test_pair_table_in_the_config_folder_runs_03_validate_04_and_the_hashes_match(world):
    config = user_layout(world, 'a')
    folders = {}
    for side in ('reference', 'target'):
        folders[side] = world.base / f'upair_{side}'
        assert main(['import-electrode-assets', '--source-kind', 'hotspot_case_electrode', '--root', str(world.base / f'src_{side}'),
                     '--config', str(config), '--side', side, '--cnt-from-dl1-root', str(world.base / f'cnt_{side}'),
                     '--source-version', 'layout_v1', '--max-asset-memory-mb', '256', '--out', str(folders[side])]) == 0
    assert main(['validate-data', '--config', str(config), '--out', str(world.base / 'upair_validate')]) in (0, 3)
    cfg = yaml.safe_load(config.read_text(encoding='utf-8'))
    refs = refs_for(world, folders)
    for side in refs:
        cfg[side].update(refs[side])
    final = world.base / 'real_user_a_assets.yaml'
    final.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    assert run_with(world, final, 'upair_04') == 0              # the 04 identity check passes: the dataset hashes are equal
    for side in folders:       # 03's hash is the one 04 computed from the same pair table content and the same strings
        record = json.loads((folders[side] / 'electrode_asset_import_record.json').read_text(encoding='utf-8'))
        manifest = json.loads((folders[side] / 'review_assets.json').read_text(encoding='utf-8'))
        assert record['dataset_input_hash'] == manifest['source_manifest_hash']


def test_the_same_pair_table_string_in_both_locations_is_a_conflict_equal_or_different(world):
    from simcompare import pipeline
    config = user_layout(world, 'b')
    cfg = load_numeric_config(config)
    section = cfg['reference']
    old = world.base / section['data_dir'] / 'data' / 'input' / 'pairs.csv'
    old.parent.mkdir(parents=True)
    real = pipeline._real_source(cfg, section)
    for content in ((world.base / 'data' / 'input' / 'pairs.csv').read_text(encoding='utf-8'), 'pair_id\nother\n'):
        old.write_text(content, encoding='utf-8')
        with pytest.raises(NumericContractError) as caught:
            pipeline._configured_real(cfg, section, real)
        assert caught.value.code == 'input_location_conflict'
    old.unlink()
    assert pipeline._configured_real(cfg, section, real)[1].cases       # the config-folder table alone: new mode


def test_pair_table_old_location_only_is_the_old_mode(world):
    cfg = yaml.safe_load(world.config.read_text(encoding='utf-8'))
    assert cfg['reference']['condition_assignment']['pair_table_path'] == 'pairs.csv'
    assert not (world.base / 'pairs.csv').exists() and (world.base / 'data_reference' / 'pairs.csv').is_file()
    cfg['map']['input'] = cfg['reference']
    old_form = world.base / 'real_oldform.yaml'
    old_form.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    assert main(['validate-data', '--config', str(old_form), '--out', str(world.base / 'uold_validate')]) in (0, 3)


# ---- 03 argument combinations ----------------------------------------------------------------------------------

def _03(world, *extra):
    return main(['import-electrode-assets', '--source-kind', 'hotspot_case_electrode', '--root', str(world.base / 'src_reference'),
                 '--source-version', 'v', '--max-asset-memory-mb', '256', '--out', str(world.base / 'never_written'), *extra])


def test_03_manifest_path_with_config_is_an_error(world):
    with pytest.raises(SystemExit) as caught:
        _03(world, '--config', str(world.config), '--side', 'reference', '--manifest-path', 'manifest.json')
    assert caught.value.code == 2 and not (world.base / 'never_written').exists()


def test_03_side_and_model_combinations(world, capsys):
    models = world.base / 'models_combo.yaml'
    section = yaml.safe_load(world.config.read_text(encoding='utf-8'))['target']
    section['product_id'] = 'PM'
    models.write_text(yaml.safe_dump({'schema_version': 'compare_models_v1', 'target_product_id': 'PB', 'models': {'PM': section}}),
                      encoding='utf-8')
    for args in (('--config', str(world.config), '--model', 'PM'),                          # real.yaml with --model
                 ('--config', str(world.config), '--side', 'reference', '--model', 'PM'),    # both
                 ('--config', str(world.config)),                                           # neither
                 ('--config', str(models), '--side', 'reference'),                          # models.yaml with --side
                 ('--config', str(models), '--side', 'reference', '--model', 'PM'),          # both
                 ('--config', str(models)),                                                 # neither
                 ('--side', 'reference'), ('--model', 'PM')):                               # without --config
        try:
            code = _03(world, *args)
        except SystemExit as exc:
            code = exc.code
        assert code == 2, args
        assert not (world.base / 'never_written').exists(), args
    capsys.readouterr()
