import json
import sys
from pathlib import Path

import pytest
import yaml

from simcompare.adapters.real_hotspot import import_hotspots
from simcompare.cli import main
from test_real_hotspot import _source

ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path, name):
    cfg = yaml.safe_load((ROOT / 'configs/etx' / name).read_text(encoding='utf-8'))
    for side, model in (('reference', 'QA4_R00'), ('target', 'QA4_T00')):
        _source(tmp_path / 'src' / model, case='lateral_short-A-SD1-B-SD1')
        import_hotspots(tmp_path / 'src' / model, tmp_path / side)
        (tmp_path / side / 'pairs.csv').write_text(
            'pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\nAB,A,B,line,v1\nBA,B,A,line,v1\n',
            encoding='utf-8')
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
        assert extra == {'asset_manifest', 'electrode_import_record'}
        assert stage4[side]['condition_assignment'] == stage2[side]['condition_assignment']


NEW_STAGES = ('models_01_import', 'models_02_import_assets', 'models_03_confirm_pairs', 'models_04_compare_windows',
              'models_05_report')
BASE_FLOW = ('01_import_hotspots', '02_vi_compute', '03_import_assets', '04_change_candidates', '05_confirm_pairs_dl2',
             '06_compare_windows', '07_vi_report')
DV_FLOW = ('01_import_hotspots', '02_import_assets', '03_dv_template', '04_dv_grade', '05_candidates_dv', '06_vulnerability_report')
OLD_NAMES = ('0_dv_template', '0b_dv_grade', '1_import_hotspots', '1m_import_models', '2_validate_compare', '2b_compare2',
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


def test_every_runner_job_parses_with_the_real_cli_parser():
    from simcompare.cli import _parser
    parser = _parser()
    stages = _stages()
    assert set(NEW_STAGES) <= set(stages)
    for flow, table in (('base', stages), ('dv', _module().DV_STAGES)):
        for stage, (_, jobs) in table.items():
            assert jobs, (flow, stage)
            for command, args, _ in jobs:
                parsed = parser.parse_args([command, *args])
                assert parsed.command == command, (flow, stage)


def test_new_runner_stages_carry_the_multiproduct_arguments():
    stages = _stages()
    jobs = {stage: stages[stage][1] for stage in NEW_STAGES}
    assert [c for c, _, _ in jobs['models_01_import']] == ['import-hotspots']
    assert [c for c, _, _ in jobs['models_02_import_assets']] == ['import-electrode-assets']
    assert [c for c, _, _ in jobs['models_03_confirm_pairs']] == ['confirm-pairs', 'confirm-pairs']
    assert all('--models' in args for _, args, _ in jobs['models_03_confirm_pairs'])
    ((_, six, _),) = jobs['models_04_compare_windows']
    for flag in ('--models', '--model', '--case-window-um', '--case-diameter-um'):
        assert flag in six
    assert six[six.index('--out') + 1] == 'data/local/models/QA4_C00'
    ((_, seven, _),) = jobs['models_05_report']
    assert [seven[i + 1] for i, a in enumerate(seven) if a == '--windows'] == ['QA4_R00=data/local', 'QA4_C00=data/local/models/QA4_C00']


def test_compare2_example_config_loads_and_stages_are_registered():
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
    stages = _stages()
    assert [c for c, _, _ in stages['02_vi_compute'][1]] == ['compare2']
    assert [c for c, _, _ in stages['07_vi_report'][1]] == ['compare2-report']
    for stage in ('02_vi_compute', '07_vi_report'):
        assert (ROOT / 'bundles/etx' / f'simcompare_job_{stage}.py').is_file()


def test_stage_runners_are_current_and_parse(tmp_path):
    _module().main(['--out-dir', str(tmp_path)])
    built = sorted(tmp_path.rglob('*.py'))
    assert len(built) == len(_stages()) + len(_module().DV_STAGES)
    for path in built:
        committed = ROOT / 'bundles/etx' / path.relative_to(tmp_path)
        assert committed.read_bytes() == path.read_bytes(), f'{committed} is stale; rebuild with --overwrite'
        compile(path.read_text(encoding='utf-8'), str(path), 'exec')


def test_runner_files_use_the_run_order_names_and_no_old_name_is_left():
    module = _module()
    assert set(BASE_FLOW) | {'diag_validate_compare'} | set(NEW_STAGES) == set(module.STAGES)
    assert tuple(module.DV_STAGES) == DV_FLOW
    on_disk = {p.relative_to(ROOT / 'bundles/etx').as_posix() for p in (ROOT / 'bundles/etx').rglob('*.py')}
    expected = ({f'simcompare_job_{k}.py' for k in module.STAGES} | {f'dv/simcompare_job_{k}.py' for k in module.DV_STAGES})
    assert on_disk == expected
    assert not any(f'simcompare_job_{old}.py' in on_disk for old in OLD_NAMES)
    assert (ROOT / 'bundles/simcompare_job.py').is_file()          # the single-file runner keeps its name


def test_dv_common_steps_have_the_same_jobs_as_the_base_flow():
    module = _module()
    assert module.DV_STAGES['01_import_hotspots'][1] == module.STAGES['01_import_hotspots'][1]
    assert module.DV_STAGES['02_import_assets'][1] == module.STAGES['03_import_assets'][1]
    for stage in ('01_import_hotspots', '02_import_assets'):      # the bundled EDIT block carries the same JOBS text
        text = (ROOT / 'bundles/etx/dv' / f'simcompare_job_{stage}.py').read_text(encoding='utf-8')
        other = 'simcompare_job_01_import_hotspots.py' if stage.startswith('01') else 'simcompare_job_03_import_assets.py'
        base = (ROOT / 'bundles/etx' / other).read_text(encoding='utf-8')
        jobs = lambda t: t[t.index('JOBS = ['):t.index('LOG_FILE = ')]
        assert jobs(text) == jobs(base)
    names = [k for k in module.DV_STAGES]
    assert [c for c, _, _ in module.DV_STAGES['03_dv_template'][1]] == ['dv-template'] * 2 and names[:2] == ['01_import_hotspots', '02_import_assets']


def test_t24_only_the_two_01_import_jobs_turn_include_cno_on():
    """T24: ETX 01 (and the dv copy) import with --include-cno; no other stage's JOBS changed (models_01 stays off)."""
    module = _module()
    for table in (module.STAGES, module.DV_STAGES):
        for stage, (_, jobs) in table.items():
            flagged = [command for command, args, _ in jobs if '--include-cno' in args]
            if stage == '01_import_hotspots':
                assert flagged == ['import-hotspots', 'import-hotspots'], stage
                assert all('--include-cno' in args for _, args, _ in jobs)
            else:
                assert not flagged, stage
    for path in (ROOT / 'bundles/etx/simcompare_job_01_import_hotspots.py', ROOT / 'bundles/etx/dv/simcompare_job_01_import_hotspots.py'):
        text = path.read_text(encoding='utf-8')
        jobs = text[text.index('JOBS = ['):text.index('LOG_FILE = ')]
        assert jobs.count("'--include-cno'") == 2, path
    for path in sorted((ROOT / 'bundles/etx').rglob('*.py')):
        if path.name != 'simcompare_job_01_import_hotspots.py':
            text = path.read_text(encoding='utf-8')
            assert "'--include-cno'" not in text[text.index('JOBS = ['):text.index('LOG_FILE = ')], path


def test_t24_dv_01_header_is_defined_directly_with_the_dv_folders_and_the_cno_note():
    module = _module()
    base, dv = module.STAGES['01_import_hotspots'][0], module.DV_STAGES['01_import_hotspots'][0]
    assert any('--include-cno' in line and '01회차부터 다시' in line for line in base)
    assert any('--include-cno' in line and 'dv_01회차부터 다시' in line for line in dv)
    text = '\n'.join(dv)
    assert 'data/dv_template, data/dv_grade, data/changes_dv, data/vulnerability_report.' in text
    assert '예전 실행기로 이미 import했다면' not in text and 'data/confirmed' not in text      # the base-flow wording is not left over
    assert any(line.startswith('dv_01회차: hotspot import.') for line in dv)


def test_compare_windows_runner_includes_the_report_map_window_and_vi_report_links_rounds_4_and_6():
    from simcompare.compare2_inputs import load_compare2_config
    stages = _stages()
    ((_, six, _),) = stages['06_compare_windows'][1]
    sizes = six[six.index('--window-sizes') + 1:six.index('--jitter-um')]
    cfg = load_compare2_config(ROOT / 'configs/etx/compare2.example.yaml')
    assert '10' in sizes and f'{cfg.report.map_window_um:g}' in sizes
    ((command, seven, _),) = stages['07_vi_report'][1]
    assert command == 'compare2-report'
    assert seven == ['--compare2-dir', 'data/compare2', '--config', 'compare2.yaml', '--candidates', 'data/changes',
                     '--windows', 'data/local', '--out', 'data/compare2_report']
