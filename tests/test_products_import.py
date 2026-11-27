"""pasx/T6: `products-import` puts the two products of real.yaml + compare2.yaml into products.yaml."""
import copy
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from simcompare import cli
from simcompare import products_config as pc
from simcompare import products_import as pi
from test_etx_flow import TEMPLATES
from test_products_assets import make_world


def run(real, compare2, group='QA4', config=None, **options):
    outs, errs = [], []
    code = pi.run_products_import(real, compare2, group, config_path=config or Path(real).parent / 'products.yaml',
                                  out=outs.append, err=errs.append, **options)
    return code, outs, errs


@pytest.fixture
def template_dir(tmp_path):
    base = tmp_path / 'tpl'
    base.mkdir()
    for name in ('real.yaml', 'compare2.yaml'):
        shutil.copyfile(str(TEMPLATES / name), str(base / name))
    return base


@pytest.fixture
def no_group_check(monkeypatch):
    """The template inputs (data/input/...) do not exist here; the group check has its own tests with real inputs."""
    monkeypatch.setattr(pc, 'check_groups', lambda config: {})


def load(path):
    return pc.load_products_config(path)


def listing(folder):
    return sorted(p.name for p in folder.iterdir())


# ---- the templates give the T3 template -----------------------------------------------------------------------------------------

def test_the_two_templates_give_a_products_yaml_with_the_meaning_of_the_t3_template(template_dir, no_group_check):
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')
    assert code == 0, errs
    assert outs[-1]['status'] == 'written' and outs[-1]['products'] == ['QA4_R00', 'QA4_T00']
    ours, theirs = load(template_dir / 'products.yaml'), load(TEMPLATES / 'products.yaml')
    assert ours.groups == theirs.groups == {'QA4': ['QA4_R00', 'QA4_T00']}
    for name in theirs.products:
        left, right = copy.deepcopy(ours.products[name]), copy.deepcopy(theirs.products[name])
        right.pop('note', None)
        assert left == right, name
    for key in ('density_dir', 'report', 'lambda_diameters_um', 'numeric'):
        assert ours.common[key] == theirs.common[key], key
    for group in theirs.common['model_groups']:                              # compare2.yaml has two more product families
        assert ours.common['model_groups'][group] == theirs.common['model_groups'][group], group
    assert set(ours.common['model_groups']) == {'mobile', 'auto', 'watch', 'other'}
    print('\n--- products.yaml written from the two templates ---\n' + (template_dir / 'products.yaml').read_text(encoding='utf-8'))


def test_the_written_file_has_the_marker_line_fixed_key_order_and_is_deterministic(template_dir, tmp_path, no_group_check):
    assert run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')[0] == 0
    first = (template_dir / 'products.yaml').read_bytes()
    text = first.decode('utf-8')
    assert text.startswith('# products-import 가 고친 파일')
    assert (text.index('\ncommon:') < text.index('density_dir:') < text.index('model_groups:') < text.index('numeric:')
            < text.index('\ngroups:') < text.index('\nproducts:'))
    other = tmp_path / 'other'
    other.mkdir()
    for name in ('real.yaml', 'compare2.yaml'):
        shutil.copyfile(str(TEMPLATES / name), str(other / name))
    assert run(other / 'real.yaml', other / 'compare2.yaml')[0] == 0
    assert (other / 'products.yaml').read_bytes() == first
    assert listing(template_dir) == ['compare2.yaml', 'products.yaml', 'real.yaml']      # no temporary file is left


def test_include_cno_follows_the_argument(template_dir, no_group_check):
    assert run(template_dir / 'real.yaml', template_dir / 'compare2.yaml', include_cno=False)[0] == 0
    assert all(p['include_cno'] is False for p in load(template_dir / 'products.yaml').products.values())


# ---- second run, conflicts, update ----------------------------------------------------------------------------------------------

def test_a_second_run_with_the_same_values_writes_nothing(template_dir, no_group_check):
    assert run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')[0] == 0
    target = template_dir / 'products.yaml'
    before = (target.read_bytes(), target.stat().st_mtime_ns)
    names = listing(template_dir)
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')
    assert code == 0 and outs[-1]['status'] == 'unchanged' and not errs
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before and listing(template_dir) == names


def test_a_hand_written_file_with_the_same_meaning_is_left_alone(template_dir, no_group_check):
    shutil.copyfile(str(TEMPLATES / 'products.yaml'), str(template_dir / 'products.yaml'))
    # the template has two model families only; compare2.yaml has four, so this is a (real) difference ...
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')
    assert code == 2 and any('common.model_groups' in line for line in errs)
    # ... and once compare2.yaml says the same, the hand-written file (comments and all) is not touched
    data = yaml.safe_load((template_dir / 'compare2.yaml').read_text(encoding='utf-8'))
    for name in ('auto', 'watch'):
        del data['model_groups'][name]
    (template_dir / 'compare2.yaml').write_text(yaml.safe_dump(data, sort_keys=False), encoding='utf-8')
    before = ((template_dir / 'products.yaml').read_bytes(), (template_dir / 'products.yaml').stat().st_mtime_ns)
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')
    assert code == 0, errs
    assert outs[-1]['status'] == 'unchanged'
    assert ((template_dir / 'products.yaml').read_bytes(), (template_dir / 'products.yaml').stat().st_mtime_ns) == before


def test_a_different_value_is_a_conflict_listed_by_cell_and_update_overwrites_but_keeps_the_note(template_dir, no_group_check):
    assert run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')[0] == 0
    target = template_dir / 'products.yaml'
    doc = yaml.safe_load(target.read_text(encoding='utf-8'))
    doc['products']['QA4_R00']['note'] = '손으로 쓴 메모'
    target.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding='utf-8')
    c2 = yaml.safe_load((template_dir / 'compare2.yaml').read_text(encoding='utf-8'))
    c2['products']['reference']['resolution'] = [1600, 1850]
    c2['report']['top_n'] = 7
    (template_dir / 'compare2.yaml').write_text(yaml.safe_dump(c2, sort_keys=False), encoding='utf-8')
    before = (target.read_bytes(), target.stat().st_mtime_ns)
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')
    assert code == 2 and 'products_import_conflict' in errs[0]
    assert any('products.QA4_R00.resolution: [1500, 1850] -> [1600, 1850]' in line for line in errs)
    assert any('common.report.top_n: 4 -> 7' in line for line in errs)
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before                       # refused: nothing written
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml', update=True)
    assert code == 0, errs
    assert outs[-1]['status'] == 'written' and 'products.QA4_R00' in outs[-1]['updated'] and 'common.report' in outs[-1]['updated']
    config = load(target)
    assert config.products['QA4_R00']['resolution'] == [1600, 1850] and config.common['report']['top_n'] == 7
    assert config.products['QA4_R00']['note'] == '손으로 쓴 메모'
    assert listing(template_dir) == ['compare2.yaml', 'products.yaml', 'real.yaml']
    assert run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')[2] == []          # now equal again


def test_other_products_of_the_file_are_kept(template_dir, no_group_check):
    assert run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')[0] == 0
    target = template_dir / 'products.yaml'
    doc = yaml.safe_load(target.read_text(encoding='utf-8'))
    doc['products']['OTHER1'] = copy.deepcopy(doc['products']['QA4_R00'])
    doc['products']['OTHER1']['hotspot_root'] = 'data/input/OTHER1'
    doc['groups']['X'] = ['OTHER1']
    target.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding='utf-8')
    c2 = yaml.safe_load((template_dir / 'compare2.yaml').read_text(encoding='utf-8'))
    c2['products']['target']['resolution'] = [1500, 1800]
    (template_dir / 'compare2.yaml').write_text(yaml.safe_dump(c2, sort_keys=False), encoding='utf-8')
    assert run(template_dir / 'real.yaml', template_dir / 'compare2.yaml', update=True)[0] == 0
    config = load(target)
    assert set(config.products) == {'QA4_R00', 'QA4_T00', 'OTHER1'} and config.groups['X'] == ['OTHER1']
    assert config.products['QA4_T00']['resolution'] == [1500, 1800]


def test_a_product_that_is_in_another_group_is_refused(template_dir, no_group_check):
    assert run(template_dir / 'real.yaml', template_dir / 'compare2.yaml', group='QA4')[0] == 0
    target = template_dir / 'products.yaml'
    before = (target.read_bytes(), target.stat().st_mtime_ns)
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml', group='ELSE', update=True)
    assert code == 2 and 'products_import_group_conflict' in errs[0] and 'QA4' in errs[0]
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before


def test_a_new_group_is_created_next_to_the_existing_ones(template_dir, no_group_check):
    assert run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')[0] == 0
    # a second pair of products from other settings (files in the same folder) goes into its own group
    second = template_dir
    real = yaml.safe_load((template_dir / 'real.yaml').read_text(encoding='utf-8'))
    c2 = yaml.safe_load((template_dir / 'compare2.yaml').read_text(encoding='utf-8'))
    for role, name in (('reference', 'QB_R'), ('target', 'QB_T')):
        real[role]['product_id'] = name
        real[role]['condition_assignment']['product_id'] = name
        real[role]['condition_assignment']['source_id'] = name + '_hotspot'
        c2['products'][role]['hotspot_root'] = 'data/input/' + name
    c2['numeric_config'] = 'real_b.yaml'
    (second / 'real_b.yaml').write_text(yaml.safe_dump(real, sort_keys=False), encoding='utf-8')
    (second / 'compare2_b.yaml').write_text(yaml.safe_dump(c2, sort_keys=False), encoding='utf-8')
    config = template_dir / 'products.yaml'
    code, outs, errs = run(second / 'real_b.yaml', second / 'compare2_b.yaml', group='QB', config=config)
    assert code == 0, errs
    assert load(config).groups == {'QA4': ['QA4_R00', 'QA4_T00'], 'QB': ['QB_R', 'QB_T']}


# ---- --check, compatibility, bad input --------------------------------------------------------------------------------------------

def test_check_writes_nothing_to_the_target(template_dir, no_group_check):
    names = listing(template_dir)
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml', check=True)
    assert code == 0 and outs[-1]['status'] == 'would_write'
    assert listing(template_dir) == names and not (template_dir / 'products.yaml').exists()
    assert run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')[0] == 0
    target = template_dir / 'products.yaml'
    before = (target.read_bytes(), target.stat().st_mtime_ns)
    c2 = yaml.safe_load((template_dir / 'compare2.yaml').read_text(encoding='utf-8'))
    c2['products']['target']['resolution'] = [10, 10]
    (template_dir / 'compare2.yaml').write_text(yaml.safe_dump(c2, sort_keys=False), encoding='utf-8')
    assert run(template_dir / 'real.yaml', template_dir / 'compare2.yaml', check=True)[0] == 2        # the conflict is reported
    code, outs, _ = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml', check=True, update=True)
    assert code == 0 and outs[-1]['status'] == 'would_write'
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before and listing(template_dir) == ['compare2.yaml', 'products.yaml', 'real.yaml']


def test_a_failing_compatibility_check_rolls_the_write_back(template_dir, monkeypatch):
    violation = [{'code': 'group_pair_table_mismatch', 'group': 'QA4', 'product': 'QA4_T00', 'case': None}]

    def refuse(config):
        raise pc.GroupCompatibilityError(violation)
    monkeypatch.setattr(pc, 'check_groups', refuse)
    names = listing(template_dir)
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')
    assert code == 2 and 'group_incompatible' in errs[0] and outs[-1]['violations'] == violation
    assert listing(template_dir) == names and not (template_dir / 'products.yaml').exists()      # a new file is not left behind
    monkeypatch.setattr(pc, 'check_groups', lambda config: {})
    assert run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')[0] == 0
    target = template_dir / 'products.yaml'
    before = (target.read_bytes(), target.stat().st_mtime_ns)
    c2 = yaml.safe_load((template_dir / 'compare2.yaml').read_text(encoding='utf-8'))
    c2['products']['target']['resolution'] = [10, 10]
    (template_dir / 'compare2.yaml').write_text(yaml.safe_dump(c2, sort_keys=False), encoding='utf-8')
    monkeypatch.setattr(pc, 'check_groups', refuse)
    assert run(template_dir / 'real.yaml', template_dir / 'compare2.yaml', update=True)[0] == 2
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before                          # an existing file is not touched
    assert listing(template_dir) == ['compare2.yaml', 'products.yaml', 'real.yaml']


def make_real_world(tmp_path):
    """real.yaml + compare2.yaml for the products P1 and P2 of the T5 fixture world (real inputs, the real group check)."""
    world = make_world(tmp_path)
    base = world.base
    sections = {}
    for role, name in (('reference', 'P1'), ('target', 'P2')):
        assignment = copy.deepcopy(world.doc['products'][name]['condition_assignment'])
        assignment['product_id'] = name
        sections[role] = {'product_id': name, 'data_dir': 'data/imported/' + role, 'condition_assignment': assignment}
    real = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'outputs/numeric_real',
            'reference': sections['reference'], 'target': sections['target'],
            'map': {'input': sections['reference'], 'diameter_um': 5}, 'review_rois': {'map_region_ids': [], 'manual': []}}
    compare2 = {'schema_version': 'compare2_config_v1', 'numeric_config': 'real.yaml', 'output_dir': 'data/compare2',
                'density_dir': 'data/input/density', 'model_groups': copy.deepcopy(world.doc['common']['model_groups']),
                'products': {role: {'model_group': 'mobile', 'resolution': list(world.doc['products'][name]['resolution']),
                                    'hotspot_root': 'data/input/' + name}
                             for role, name in (('reference', 'P1'), ('target', 'P2'))},
                'report': {'diameter_um': 3}}
    (base / 'real.yaml').write_text(yaml.safe_dump(real, sort_keys=False), encoding='utf-8')
    (base / 'compare2.yaml').write_text(yaml.safe_dump(compare2, sort_keys=False), encoding='utf-8')
    return base


def test_the_real_group_check_accepts_compatible_products_and_the_file_is_usable_by_products_config(tmp_path):
    base = make_real_world(tmp_path)
    config = base / 'products-new.yaml'
    code, outs, errs = run(base / 'real.yaml', base / 'compare2.yaml', group='G', config=config)
    assert code == 0, errs
    assert load(config).groups == {'G': ['P1', 'P2']}
    assert pc.run_products_config(config, check=True)


def test_the_real_group_check_refuses_an_incompatible_product_and_leaves_the_file(tmp_path):
    base = make_real_world(tmp_path)
    real = yaml.safe_load((base / 'real.yaml').read_text(encoding='utf-8'))
    real['target']['condition_assignment']['layer_stack'] = real['target']['condition_assignment']['layer_stack'][:1]
    (base / 'real.yaml').write_text(yaml.safe_dump(real, sort_keys=False), encoding='utf-8')
    names = listing(base)
    code, outs, errs = run(base / 'real.yaml', base / 'compare2.yaml', group='G', config=base / 'products-new.yaml')
    assert code == 2 and 'group_incompatible' in errs[0], errs
    assert listing(base) == names


@pytest.mark.parametrize('mutate, code_text', [
    (lambda r, c: c.update(numeric_config='other.yaml'), 'products_import_config_mismatch'),
    (lambda r, c: r['target'].update(product_id=r['reference']['product_id']), 'products_import_invalid'),
    (lambda r, c: r['reference']['condition_assignment'].update(product_id='ELSE'), 'products_import_invalid'),
    (lambda r, c: r['reference'].update(product_id='bad name'), 'invalid_product_name'),
])
def test_bad_settings_are_refused_before_anything_is_written(template_dir, no_group_check, mutate, code_text):
    real = yaml.safe_load((template_dir / 'real.yaml').read_text(encoding='utf-8'))
    c2 = yaml.safe_load((template_dir / 'compare2.yaml').read_text(encoding='utf-8'))
    mutate(real, c2)
    (template_dir / 'real.yaml').write_text(yaml.safe_dump(real, sort_keys=False), encoding='utf-8')
    (template_dir / 'compare2.yaml').write_text(yaml.safe_dump(c2, sort_keys=False), encoding='utf-8')
    names = listing(template_dir)
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')
    assert code == 2 and code_text in errs[0], errs
    assert listing(template_dir) == names


def test_a_products_yaml_in_another_folder_rebases_the_paths_or_is_refused(template_dir, no_group_check):
    sub = template_dir / 'sub'
    sub.mkdir()
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml', config=sub / 'products.yaml')
    assert code == 2 and 'relative_path_escape' in errs[0]            # ../data/input/... is refused by products-config
    assert listing(sub) == []


def test_missing_or_unsupported_existing_file_is_an_error(template_dir, no_group_check):
    (template_dir / 'products.yaml').write_text('schema_version: other\n', encoding='utf-8')
    before = (template_dir / 'products.yaml').read_bytes()
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')
    assert code == 2 and 'unsupported_products_config' in errs[0]
    assert (template_dir / 'products.yaml').read_bytes() == before
    code, outs, errs = run(template_dir / 'missing.yaml', template_dir / 'compare2.yaml')
    assert code == 2 and errs


# ---- the command line ------------------------------------------------------------------------------------------------------------

def test_cli_products_import_prints_json_and_exit_codes(template_dir, no_group_check, capsys):
    argv = ['products-import', '--real', str(template_dir / 'real.yaml'), '--compare2', str(template_dir / 'compare2.yaml'),
            '--group', 'QA4', '--config', str(template_dir / 'products.yaml')]
    assert cli.main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    assert first['status'] == 'written' and first['group'] == 'QA4' and first['products'] == ['QA4_R00', 'QA4_T00']
    assert cli.main(argv) == 0 and json.loads(capsys.readouterr().out)['status'] == 'unchanged'
    assert cli.main(argv + ['--check']) == 0 and json.loads(capsys.readouterr().out)['status'] == 'unchanged'
    print('\n--- products-import output (first run) ---\n' + json.dumps(first, ensure_ascii=False, indent=2))


# ---- pasx/T6b (R6:1-3, 7) ------------------------------------------------------------------------------------------------------

def test_check_in_a_missing_folder_creates_no_folder_and_no_temporary_file(template_dir, no_group_check):
    config = template_dir / 'newdir' / 'products.yaml'
    names = listing(template_dir)
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml', config=config, check=True)
    # ../ paths are refused by products-config (the template inputs sit next to real.yaml); the point is that nothing is created
    assert code in (0, 2)
    assert listing(template_dir) == names and not (template_dir / 'newdir').exists()
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml', check=True)
    assert code == 0 and outs[-1]['status'] == 'would_write' and 'folder' not in outs[-1]
    assert listing(template_dir) == names


def test_check_leaves_no_temporary_file_when_the_group_check_or_an_exception_stops_it(template_dir, monkeypatch):
    names = listing(template_dir)
    violation = [{'code': 'group_pair_table_mismatch', 'group': 'QA4', 'product': 'QA4_T00', 'case': None}]

    def refuse(config):
        raise pc.GroupCompatibilityError(violation)
    monkeypatch.setattr(pc, 'check_groups', refuse)
    assert run(template_dir / 'real.yaml', template_dir / 'compare2.yaml', check=True)[0] == 2
    assert listing(template_dir) == names

    def explode(config):
        raise OSError('disk gone')
    monkeypatch.setattr(pc, 'check_groups', explode)
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml', check=True)
    assert code == 2 and 'products_import_io' in errs[0]
    assert listing(template_dir) == names


def test_a_new_product_that_does_not_load_with_the_file_common_is_a_listed_conflict(template_dir, no_group_check):
    assert run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')[0] == 0
    target = template_dir / 'products.yaml'
    before = (target.read_bytes(), target.stat().st_mtime_ns)
    c2 = yaml.safe_load((template_dir / 'compare2.yaml').read_text(encoding='utf-8'))
    c2['model_groups']['tablet'] = copy.deepcopy(c2['model_groups']['mobile'])
    c2['products']['target']['model_group'] = 'tablet'
    (template_dir / 'compare2.yaml').write_text(yaml.safe_dump(c2, sort_keys=False), encoding='utf-8')
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')
    assert code == 2 and 'products_import_conflict' in errs[0], errs
    assert any('common.model_groups' in line for line in errs)
    assert any('does not load with the file common values' in line and 'QA4_T00' in line for line in errs)
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml', update=True)
    assert code == 0, errs                                              # --update replaces the common values first, then it loads


def test_update_that_changes_common_lists_every_product_of_the_file(template_dir, no_group_check):
    assert run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')[0] == 0
    target = template_dir / 'products.yaml'
    doc = yaml.safe_load(target.read_text(encoding='utf-8'))
    doc['products']['OTHER'] = copy.deepcopy(doc['products']['QA4_R00'])
    doc['groups']['ELSE'] = ['OTHER']
    target.write_text(yaml.safe_dump(doc, sort_keys=False), encoding='utf-8')
    c2 = yaml.safe_load((template_dir / 'compare2.yaml').read_text(encoding='utf-8'))
    c2['report']['top_n'] = 9
    (template_dir / 'compare2.yaml').write_text(yaml.safe_dump(c2, sort_keys=False), encoding='utf-8')
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml', update=True)
    assert code == 0, errs
    assert sorted(outs[-1]['common_changed_products']) == ['OTHER', 'QA4_R00', 'QA4_T00']
    assert any('OTHER' in line and 'QA4_R00' in line for line in errs)
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml', update=True)      # nothing changes now
    assert 'common_changed_products' not in outs[-1] and not errs


def test_a_path_with_no_relative_form_is_products_import_invalid(template_dir, no_group_check, monkeypatch):
    def other_drive(path, start=None):
        raise ValueError('path is on mount "D:", start on mount "C:"')
    monkeypatch.setattr(os.path, 'relpath', other_drive)
    names = listing(template_dir)
    code, outs, errs = run(template_dir / 'real.yaml', template_dir / 'compare2.yaml')
    assert code == 2 and 'products_import_invalid' in errs[0], errs
    assert listing(template_dir) == names
