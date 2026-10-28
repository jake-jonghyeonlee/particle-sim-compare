import json
import sys
from pathlib import Path

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


NEW_STAGES = ('1m_import_models', '3m_import_model_assets', '5m_confirm_pairs_models', '6m_compare_windows_models', '7_model_report')


def _stages():
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        import build_etx_stage_runners as stages
    finally:
        sys.path.pop(0)
    return stages.STAGES


def test_every_runner_job_parses_with_the_real_cli_parser():
    from simcompare.cli import _parser
    parser = _parser()
    stages = _stages()
    assert set(NEW_STAGES) <= set(stages)
    for stage, (_, jobs) in stages.items():
        assert jobs, stage
        for command, args, _ in jobs:
            parsed = parser.parse_args([command, *args])
            assert parsed.command == command, stage


def test_new_runner_stages_carry_the_multiproduct_arguments():
    stages = _stages()
    jobs = {stage: stages[stage][1] for stage in NEW_STAGES}
    assert [c for c, _, _ in jobs['1m_import_models']] == ['import-hotspots']
    assert [c for c, _, _ in jobs['3m_import_model_assets']] == ['import-electrode-assets']
    assert [c for c, _, _ in jobs['5m_confirm_pairs_models']] == ['confirm-pairs', 'confirm-pairs']
    assert all('--models' in args for _, args, _ in jobs['5m_confirm_pairs_models'])
    ((_, six, _),) = jobs['6m_compare_windows_models']
    for flag in ('--models', '--model', '--case-window-um', '--case-diameter-um'):
        assert flag in six
    assert six[six.index('--out') + 1] == 'data/local/models/QA4_C00'
    ((_, seven, _),) = jobs['7_model_report']
    assert [seven[i + 1] for i, a in enumerate(seven) if a == '--windows'] == ['QA4_R00=data/local', 'QA4_C00=data/local/models/QA4_C00']


def test_stage_runners_are_current_and_parse(tmp_path):
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        import build_etx_stage_runners as stages
    finally:
        sys.path.pop(0)
    stages.main(['--out-dir', str(tmp_path)])
    for path in sorted(tmp_path.glob('*.py')):
        committed = ROOT / 'bundles/etx' / path.name
        assert committed.read_bytes() == path.read_bytes(), f'{committed} is stale; rebuild with --overwrite'
