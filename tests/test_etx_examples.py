import json
import sys
from pathlib import Path

import pytest
import yaml

from simcompare.adapters.real_hotspot import import_hotspots
from simcompare.cli import main
from test_real_hotspot import _source

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / 'configs/etx/templates'
ASSET_KEYS = {'asset_manifest', 'electrode_import_record'}


def _config(tmp_path, name):
    """The example config on a tmp working folder in the new input layout: data/input/pairs.csv and thk_info-<model>.txt next to it."""
    cfg = yaml.safe_load((ROOT / 'configs/etx' / name).read_text(encoding='utf-8'))
    (tmp_path / 'data/input').mkdir(parents=True)
    (tmp_path / 'data/input/pairs.csv').write_text(
        'pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\nAB,A,B,line,v1\nBA,B,A,line,v1\n', encoding='utf-8')
    for side, model in (('reference', 'QA4_R00'), ('target', 'QA4_T00')):
        _source(tmp_path / 'src' / model, case='lateral_short-A-SD1-B-SD1')
        import_hotspots(tmp_path / 'src' / model, tmp_path / side)
        (tmp_path / f'data/input/thk_info-{model}.txt').write_text('layers\tdistance\nBMLBML\t0\n', encoding='utf-8')
        cfg[side]['data_dir'] = str(tmp_path / side)
    cfg['output_dir'] = str(tmp_path / 'out')
    path = tmp_path / 'real.yaml'
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding='utf-8')
    return path, cfg


def test_stage2_example_config_runs_validate_and_compare(tmp_path):
    path, cfg = _config(tmp_path, 'real.stage2.example.yaml')
    assert cfg['map']['input']['data_dir'] == cfg['reference']['data_dir']
    assert main(['validate-data', '--config', str(path)]) in (0, 3)
    assert main(['compare', '--config', str(path)]) in (0, 3)
    assert not (tmp_path / 'out' / 'error.json').exists()


def test_assignment_examples_match_stage2_condition_assignment():
    cfg = yaml.safe_load((ROOT / 'configs/etx/real.stage2.example.yaml').read_text(encoding='utf-8'))
    for side in ('reference', 'target'):
        example = json.loads((ROOT / f'configs/etx/assignment_{side}.example.json').read_text(encoding='utf-8'))
        assert {k: v for k, v in example.items() if k != 'electrode_roles'} == cfg[side]['condition_assignment']


def test_stage4_example_adds_only_asset_evidence_to_stage2():
    stage2 = yaml.safe_load((ROOT / 'configs/etx/real.stage2.example.yaml').read_text(encoding='utf-8'))
    stage4 = yaml.safe_load((ROOT / 'configs/etx/real.stage4.example.yaml').read_text(encoding='utf-8'))
    for side in ('reference', 'target'):
        extra = set(stage4[side]) - set(stage2[side])
        assert extra == ASSET_KEYS
        assert stage4[side]['condition_assignment'] == stage2[side]['condition_assignment']


def test_examples_and_templates_have_the_same_keys_and_path_rules():
    """The examples are the templates (configs/etx/templates/*.yaml) with other comments: same keys, same paths, same values."""
    template = yaml.safe_load((TEMPLATES / 'real.yaml').read_text(encoding='utf-8'))
    stage4 = yaml.safe_load((ROOT / 'configs/etx/real.stage4.example.yaml').read_text(encoding='utf-8'))
    stage2 = yaml.safe_load((ROOT / 'configs/etx/real.stage2.example.yaml').read_text(encoding='utf-8'))
    assert stage4 == template
    for side in ('reference', 'target'):
        assert {k: v for k, v in template[side].items() if k not in ASSET_KEYS} == stage2[side]
    skip = ('reference', 'target', 'map')       # map.input is the reference section (an alias), so it differs by the asset keys too
    assert {k: v for k, v in stage2.items() if k not in skip} == {k: v for k, v in template.items() if k not in skip}
    assert stage2['map']['input'] == stage2['reference'] and template['map']['input'] == template['reference']
    assert stage2['map']['diameter_um'] == template['map']['diameter_um']
    assert (yaml.safe_load((ROOT / 'configs/etx/compare2.example.yaml').read_text(encoding='utf-8'))
            == yaml.safe_load((TEMPLATES / 'compare2.yaml').read_text(encoding='utf-8')))
    for side, model in (('reference', 'QA4_R00'), ('target', 'QA4_T00')):      # the input layout of v2.2 / v3
        assignment = template[side]['condition_assignment']
        assert assignment['pair_table_path'] == 'data/input/pairs.csv'
        assert assignment['interlayer_distance_path'] == f'data/input/thk_info-{model}.txt'
        assert template[side]['asset_manifest'] == {'path': f'data/assets/{side}/review_assets.json'}      # no sha256
        assert template[side]['electrode_import_record'] == {'path': f'data/assets/{side}/electrode_asset_import_record.json'}
    compare2 = yaml.safe_load((TEMPLATES / 'compare2.yaml').read_text(encoding='utf-8'))
    assert compare2['report']['map_window_um'] == 10
    assert [compare2['products'][r]['hotspot_root'] for r in ('reference', 'target')] == ['data/input/QA4_R00', 'data/input/QA4_T00']


def test_models_example_uses_the_new_input_layout():
    models = yaml.safe_load((ROOT / 'configs/etx/models.example.yaml').read_text(encoding='utf-8'))
    for key, section in models['models'].items():
        assignment = section['condition_assignment']
        assert assignment['pair_table_path'] == 'data/input/pairs.csv'
        assert assignment['interlayer_distance_path'] == f'data/input/thk_info-{key}.txt'
        for asset in ASSET_KEYS & set(section):
            assert 'sha256' not in section[asset] and section[asset]['path'].startswith(f'data/assets/models/{key}/')


MODEL_STAGES = ('models_01_import', 'models_02_import_assets', 'models_03_confirm_pairs', 'models_04_compare_windows',
                'models_05_report')
DV_FLOW = ('01_import_hotspots', '02_import_assets', '03_dv_template', '04_dv_grade', '05_candidates_dv', '06_vulnerability_report')
FLOW_FILE = 'simcompare_job_etx.py'
OLD_NAMES = ('01_import_hotspots', '02_vi_compute', '03_import_assets', '04_change_candidates', '05_confirm_pairs_dl2',
             '06_compare_windows', '07_vi_report', 'diag_validate_compare',
             '0_dv_template', '0b_dv_grade', '1_import_hotspots', '1m_import_models', '2_validate_compare', '2b_compare2',
             '3_import_electrode_assets', '3m_import_model_assets', '4_candidates', '4b_candidates_dv', '4c_vulnerability_report',
             '5_confirm_pairs', '5m_confirm_pairs_models', '6_compare_windows', '6m_compare_windows_models', '7_model_report',
             '8_compare2_report')


def _module():
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        import build_etx_stage_runners as stages
    finally:
        sys.path.pop(0)
    return stages


def _stages():
    return _module().STAGES


def test_every_runner_job_parses_with_the_real_cli_parser(tmp_path):
    from simcompare import etx_flow
    from simcompare.cli import _parser
    from test_etx_flow import edit_settings, template_base
    parser = _parser()
    stages = _stages()
    assert set(MODEL_STAGES) == set(stages)
    for flow, table in (('models', stages), ('dv', _module().DV_STAGES)):
        for stage, (_, jobs) in table.items():
            assert jobs, (flow, stage)
            for command, args, _ in jobs:
                parsed = parser.parse_args([command, *args])
                assert parsed.command == command, (flow, stage)
    base = template_base(tmp_path)
    for step in etx_flow.STEP_ORDER:        # the integrated runner: every step's jobs parse (05 needs its DL2 arguments)
        jobs = etx_flow.plan_jobs(base, edit_settings(steps=[step]))
        assert jobs, step
        for command, args in jobs:
            assert parser.parse_args([command, *args]).command == command, step


def test_model_runner_stages_carry_the_multiproduct_arguments():
    stages = _stages()
    jobs = {stage: stages[stage][1] for stage in MODEL_STAGES}
    assert [c for c, _, _ in jobs['models_01_import']] == ['import-hotspots']
    assert jobs['models_01_import'][0][1][:2] == ['--root', 'data/input/QA4_C00']       # no hotspot folder level
    ((_, electrode, _),) = jobs['models_02_import_assets']
    assert electrode[electrode.index('--config') + 1] == 'models.yaml' and electrode[electrode.index('--model') + 1] == 'QA4_C00'
    assert '--numeric-data-dir' not in electrode and '--condition-assignment' not in electrode
    assert [c for c, _, _ in jobs['models_03_confirm_pairs']] == ['confirm-pairs', 'confirm-pairs']
    assert all('--models' in args for _, args, _ in jobs['models_03_confirm_pairs'])
    reference_windows, six = jobs['models_04_compare_windows'][0][1], jobs['models_04_compare_windows'][1][1]
    assert reference_windows[reference_windows.index('--correspondence') + 1] == 'data/confirmed/roi_correspondence.json'
    assert reference_windows[reference_windows.index('--out') + 1] == 'data/local' and '--model' not in reference_windows
    for flag in ('--models', '--model', '--case-window-um', '--case-diameter-um'):
        assert flag in six
    assert six[six.index('--out') + 1] == 'data/local/models/QA4_C00'
    ((_, seven, _),) = jobs['models_05_report']
    assert [seven[i + 1] for i, a in enumerate(seven) if a == '--windows'] == ['QA4_R00=data/local', 'QA4_C00=data/local/models/QA4_C00']


def test_compare2_example_config_loads_and_the_integrated_runner_is_registered():
    from simcompare.compare2_inputs import load_compare2_config
    cfg = load_compare2_config(ROOT / 'configs/etx/compare2.example.yaml')  # paths are not required to exist
    assert set(cfg.model_groups) == {'mobile', 'auto', 'watch', 'other'}
    for name in ('mobile', 'auto', 'watch'):
        group = cfg.model_groups[name]
        assert group.glass_area_um2 == 1.3875e12 and (group.weight_line, group.weight_pixel) == (2.5, 2.5)
        assert group.glass_area_reason is None
    other = cfg.model_groups['other']
    assert other.glass_area_um2 == 9.75e11 and other.weight_line == pytest.approx(10 / 7) and other.weight_pixel == 2.0
    assert cfg.report.dl1_root is not None
    assert (ROOT / 'bundles/etx' / FLOW_FILE).is_file()


def test_stage_runners_are_current_and_parse(tmp_path):
    _module().main(['--out-dir', str(tmp_path)])
    built = sorted(tmp_path.rglob('*.py'))
    assert len(built) == 1 + len(_stages()) + len(_module().DV_STAGES)
    for path in built:
        committed = ROOT / 'bundles/etx' / path.relative_to(tmp_path)
        assert committed.read_bytes() == path.read_bytes(), f'{committed} is stale; rebuild with --overwrite'
        compile(path.read_text(encoding='utf-8'), str(path), 'exec')


def test_runner_files_are_the_integrated_runner_and_the_model_and_dv_stages_only():
    module = _module()
    assert tuple(module.DV_STAGES) == DV_FLOW
    on_disk = {p.relative_to(ROOT / 'bundles/etx').as_posix() for p in (ROOT / 'bundles/etx').rglob('*.py')}
    expected = ({FLOW_FILE} | {f'simcompare_job_{k}.py' for k in module.STAGES} | {f'dv/simcompare_job_{k}.py' for k in module.DV_STAGES})
    assert on_disk == expected
    for old in OLD_NAMES:       # the eight base-flow runners (01..07, diag) are replaced by the integrated runner; no older name is left
        assert f'simcompare_job_{old}.py' not in on_disk, old
    assert (ROOT / 'bundles/simcompare_job.py').is_file()          # the single-file runner keeps its name


def test_dv_common_steps_have_the_same_jobs_as_the_integrated_runner_steps(tmp_path):
    """dv_01 / dv_02 carry the same arguments as the integrated runner's 01 and 03 steps for the template configs."""
    from simcompare import etx_flow
    from test_etx_flow import edit_settings, template_base
    module = _module()
    base = template_base(tmp_path)
    settings = edit_settings()
    one = etx_flow.plan_jobs(base, dict(settings, steps=['01']))
    three = etx_flow.plan_jobs(base, dict(settings, steps=['03']))
    assert [(c, a) for c, a, _ in module.DV_STAGES['01_import_hotspots'][1]] == one
    assert [(c, a) for c, a, _ in module.DV_STAGES['02_import_assets'][1]] == three
    names = list(module.DV_STAGES)
    assert [c for c, _, _ in module.DV_STAGES['03_dv_template'][1]] == ['dv-template'] * 2 and names[:2] == ['01_import_hotspots', '02_import_assets']
    assert not any('input/hotspot' in ' '.join(args) for table in (module.STAGES, module.DV_STAGES) for _, jobs in table.values()
                   for _, args, _ in jobs)


def test_t24_include_cno_is_on_in_the_integrated_runner_and_dv_01_only():
    """T24: the two 01 import jobs turn --include-cno on (integrated runner INCLUDE_CNO and dv_01); no other runner has it (models_01 stays off)."""
    module = _module()
    assert 'INCLUDE_CNO = True' in module.FLOW_EDIT
    for table in (module.STAGES, module.DV_STAGES):
        for stage, (_, jobs) in table.items():
            flagged = [command for command, args, _ in jobs if '--include-cno' in args]
            if table is module.DV_STAGES and stage == '01_import_hotspots':
                assert flagged == ['import-hotspots', 'import-hotspots'], stage
                assert all('--include-cno' in args for _, args, _ in jobs)
            else:
                assert not flagged, stage
    path = ROOT / 'bundles/etx/dv/simcompare_job_01_import_hotspots.py'
    text = path.read_text(encoding='utf-8')
    jobs = text[text.index('JOBS = ['):text.index('LOG_FILE = ')]
    assert jobs.count("'--include-cno'") == 2, path
    for path in sorted((ROOT / 'bundles/etx').rglob('*.py')):
        if path.name != FLOW_FILE and not (path.name == 'simcompare_job_01_import_hotspots.py' and path.parent.name == 'dv'):
            text = path.read_text(encoding='utf-8')
            assert "'--include-cno'" not in text[text.index('JOBS = ['):text.index('LOG_FILE = ')], path


def test_t24_dv_01_header_is_defined_directly_with_the_dv_folders_and_the_cno_note():
    module = _module()
    dv = module.DV_STAGES['01_import_hotspots'][0]
    assert any('--include-cno' in line and 'dv_01회차부터 다시' in line for line in dv)
    text = '\n'.join(dv)
    assert 'data/dv_template, data/dv_grade, data/changes_dv, data/vulnerability_report.' in text
    assert '예전 실행기로 이미 import했다면' not in text and 'data/confirmed' not in text      # the base-flow wording is not left over
    assert any(line.startswith('dv_01회차: hotspot import.') for line in dv)
