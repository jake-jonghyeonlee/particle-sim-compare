"""vdist-filter/T4: the integrated ETX runner (simcompare.etx_flow + bundles/etx/simcompare_job_etx.py).

JOBS come from the two config files (real.yaml, compare2.yaml) and the EDIT block. The previous output folders of the chosen steps
are moved to data/_runs/<RUN_TAG>/ (never deleted, never copied).
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from simcompare import etx_flow
from simcompare.etx_flow import FlowError

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / 'configs/etx/templates'


def _builder():
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        import build_etx_stage_runners as module
    finally:
        sys.path.pop(0)
    return module


def edit_settings(**overrides):
    """The EDIT-block values of the generated integrated runner (taken from the generator, so the test follows the file)."""
    namespace = {}
    exec(compile(_builder().FLOW_EDIT, 'FLOW_EDIT', 'exec'), namespace)
    keys = {'steps': 'STEPS', 'run_tag': 'RUN_TAG', 'real_config': 'REAL_CONFIG', 'compare2_config': 'COMPARE2_CONFIG',
            'include_cno': 'INCLUDE_CNO', 'max_asset_memory_mb': 'MAX_ASSET_MEMORY_MB', 'align_args': 'ALIGN_ARGS',
            'extra_window_sizes_um': 'EXTRA_WINDOWS_UM', 'jitter_um': 'JITTER_UM', 'dl2_match_args': 'DL2_MATCH_ARGS',
            'dl2_decision_args': 'DL2_DECISION_ARGS', 'changes_dir': 'CHANGES_DIR', 'confirmed_dir': 'CONFIRMED_DIR',
            'local_dir': 'LOCAL_DIR', 'report_dir': 'REPORT_DIR'}
    settings = {name: namespace[constant] for name, constant in keys.items()}
    settings.update(overrides)
    return settings


def template_base(tmp_path, name='base'):
    """A BASE_DIR holding the two templates as real.yaml and compare2.yaml (the files a user copies and edits)."""
    base = tmp_path / name
    base.mkdir()
    for file in ('real.yaml', 'compare2.yaml'):
        shutil.copyfile(str(TEMPLATES / file), str(base / file))
    return base


def edit_yaml(path, mutate):
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    mutate(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding='utf-8')


def flow_runner(folder, base, overrides=None):
    """The generated integrated runner with BASE_DIR set and the given EDIT-block constants overridden (appended after the block)."""
    module = _builder()
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / 'job.py'
    module.build(out, header=module.FLOW_HEADER, flow=(module.FLOW_EDIT, module.FLOW_FUNC))
    text = out.read_text(encoding='utf-8').replace('BASE_DIR = None', f'BASE_DIR = {str(base)!r}', 1)
    marker = '# ============================== EDIT ABOVE'
    extra = ''.join(f'{key} = {value!r}\n' for key, value in (overrides or {}).items())
    out.write_text(text.replace(marker, extra + marker, 1), encoding='utf-8')
    return out


def run_runner(python, script, cwd):
    return subprocess.run([python, '-I', str(script)], capture_output=True, text=True, cwd=str(cwd))


def code_of(excinfo):
    return excinfo.value.code


# ---- JOBS from the templates ----------------------------------------------------------------------------------------------

def expected_default_jobs(align):
    kinds = [('reference', 'QA4_R00', 'QA4_T00'), ('target', 'QA4_T00', 'QA4_R00')]
    jobs = []
    for side, model, other in kinds:
        jobs.append(('import-hotspots', ['--root', f'data/input/{model}', '--expected-cases-from-roots', f'data/input/{other}',
                                         '--include-cno', '--out', f'data/imported/{side}']))
    for side, model, _ in kinds:
        jobs.append(('import-electrode-assets', [
            '--source-kind', 'hotspot_case_electrode', '--root', f'data/input/{model}', '--config', 'real.yaml', '--side', side,
            '--cnt-from-dl1-root', 'data/input/dl1', '--source-version', 'LAYOUT_V1', '--max-asset-memory-mb', '16384',
            '--out', f'data/assets/{side}']))
    jobs.append(('compare2', ['--config', 'compare2.yaml', '--out', 'data/compare2']))
    jobs.append(('validate-data', ['--config', 'real.yaml']))
    jobs.append(('compare', ['--config', 'real.yaml']))
    jobs.append(('candidates-change', ['--config', 'real.yaml', '--cnt-from-dl1-root', 'data/input/dl1'] + list(align) + [
        '--electrode-png-root', 'data/input/dl1', '--window-um', '10', '--diameter-um', '3', '--out', 'data/changes']))
    jobs.append(('compare-windows', ['--config', 'real.yaml', '--correspondence', 'data/changes/direct/roi_correspondence.json',
                                     '--window-sizes', '10', '--jitter-um', '0.5', '--sort-diameter-um', '3', '--out', 'data/local']))
    jobs.append(('compare2-report', ['--compare2-dir', 'data/compare2', '--config', 'compare2.yaml', '--candidates', 'data/changes',
                                     '--windows', 'data/local', '--out', 'data/compare2_report']))
    return jobs


def test_template_configs_give_the_expected_jobs_in_the_fixed_order(tmp_path):
    base = template_base(tmp_path)
    settings = edit_settings()
    assert list(settings['steps']) == ['01', '03', '02', 'diag', '04', '06', '07']      # 05 stays a step but is not a default
    jobs = etx_flow.plan_jobs(base, settings)
    assert [(c, a) for c, a in jobs] == expected_default_jobs(settings['align_args'])
    reordered = etx_flow.plan_jobs(base, dict(settings, steps=['07', 'diag', '04', '01', '06', '02', '03']))
    assert reordered == jobs            # the order of STEPS does not matter


def test_one_edit_in_the_config_files_changes_every_stage_argument(tmp_path):
    """model name / hotspot root, DL1 root, diameter and window are read once, from compare2.yaml; every stage follows."""
    base = template_base(tmp_path)
    settings = edit_settings(extra_window_sizes_um=[20, 10])
    before = etx_flow.plan_jobs(base, dict(settings, steps=list(etx_flow.STEP_ORDER), dl2_match_args=['--match', 'a=b'],
                                           dl2_decision_args=['--decisions', 'd.csv']))

    def mutate(data):
        data['products']['reference']['hotspot_root'] = 'data/input/QA9_R'
        data['report'].update(dl1_root='data/input/dl1b', diameter_um=4, map_window_um=20)
    edit_yaml(base / 'compare2.yaml', mutate)
    after = etx_flow.plan_jobs(base, dict(settings, steps=list(etx_flow.STEP_ORDER), dl2_match_args=['--match', 'a=b'],
                                          dl2_decision_args=['--decisions', 'd.csv']))
    flat_before = ' '.join(arg for _, args in before for arg in args)
    flat_after = ' '.join(arg for _, args in after for arg in args)
    assert 'data/input/QA4_R00' in flat_before and 'data/input/QA9_R' not in flat_before
    assert 'data/input/QA4_R00' not in flat_after.split() and 'data/input/dl1' not in flat_after.split()
    by = {}
    for command, args in after:
        by.setdefault(command, []).append(args)
    ref_import, tgt_import = by['import-hotspots']
    assert ref_import[ref_import.index('--root') + 1] == 'data/input/QA9_R'
    assert tgt_import[tgt_import.index('--expected-cases-from-roots') + 1] == 'data/input/QA9_R'
    for args in by['import-electrode-assets']:
        assert args[args.index('--cnt-from-dl1-root') + 1] == 'data/input/dl1b'
    assert by['import-electrode-assets'][0][by['import-electrode-assets'][0].index('--root') + 1] == 'data/input/QA9_R'
    (change,) = by['candidates-change']
    assert change[change.index('--cnt-from-dl1-root') + 1] == change[change.index('--electrode-png-root') + 1] == 'data/input/dl1b'
    assert change[change.index('--window-um') + 1] == '20' and change[change.index('--diameter-um') + 1] == '4'
    (windows,) = by['compare-windows']
    assert windows[windows.index('--window-sizes') + 1:windows.index('--jitter-um')] == ['20', '10']      # map window first, extra after
    assert windows[windows.index('--sort-diameter-um') + 1] == '4'


def test_step_05_switches_06_and_07_to_the_confirmed_correspondence(tmp_path):
    base = template_base(tmp_path)
    settings = edit_settings(dl2_match_args=['--match', 'reference_to_target=data/dl2/r2t.json'],
                             dl2_decision_args=['--decisions', 'data/dl2/decisions.csv', '--labelled-by', 'ME'])
    jobs = etx_flow.plan_jobs(base, dict(settings, steps=['06', '07', '04', '05']))
    assert [c for c, _ in jobs] == ['candidates-change', 'confirm-pairs', 'confirm-pairs', 'compare-windows', 'compare2-report']
    listing, confirm = jobs[1][1], jobs[2][1]
    assert listing[:5] == ['--list', '--config', 'real.yaml', '--candidates', 'data/changes'] and '--decisions' not in listing
    assert confirm[-2:] == ['--out', 'data/confirmed'] and '--decisions' in confirm and '--list' not in confirm
    windows, report = jobs[3][1], jobs[4][1]
    assert windows[windows.index('--correspondence') + 1] == 'data/confirmed/roi_correspondence.json'
    assert report[report.index('--confirm') + 1] == 'data/confirmed'
    # without 05: the direct correspondence and no --confirm
    plain = etx_flow.plan_jobs(base, dict(settings, steps=['04', '06', '07']))
    assert '--confirm' not in plain[-1][1]
    assert plain[1][1][plain[1][1].index('--correspondence') + 1] == 'data/changes/direct/roi_correspondence.json'


def test_step_05_needs_its_dl2_arguments(tmp_path):
    base = template_base(tmp_path)
    with pytest.raises(FlowError) as caught:
        etx_flow.plan_jobs(base, edit_settings(steps=['05'], dl2_match_args=[], dl2_decision_args=[]))
    assert code_of(caught) == 'etx_dl2_args_required'


def test_07_links_04_and_06_only_when_they_are_chosen_or_their_output_exists(tmp_path):
    base = template_base(tmp_path)
    settings = edit_settings(steps=['07'])
    (_, args), = etx_flow.plan_jobs(base, settings)
    assert args == ['--compare2-dir', 'data/compare2', '--config', 'compare2.yaml', '--out', 'data/compare2_report']
    (base / 'data/changes').mkdir(parents=True)
    (_, args), = etx_flow.plan_jobs(base, settings)
    assert '--candidates' in args and '--windows' not in args
    (base / 'data/local').mkdir()
    (_, args), = etx_flow.plan_jobs(base, settings)
    assert args[args.index('--candidates') + 1] == 'data/changes' and args[args.index('--windows') + 1] == 'data/local'
    # chosen steps count even when nothing exists yet
    jobs = etx_flow.plan_jobs(template_base(tmp_path, 'fresh'), edit_settings(steps=['04', '06', '07']))
    assert '--candidates' in jobs[-1][1] and '--windows' in jobs[-1][1]


def test_include_cno_false_drops_the_flag_from_both_01_jobs(tmp_path):
    base = template_base(tmp_path)
    on = etx_flow.plan_jobs(base, edit_settings(steps=['01'], include_cno=True))
    off = etx_flow.plan_jobs(base, edit_settings(steps=['01'], include_cno=False))
    assert [('--include-cno' in args) for _, args in on] == [True, True]
    assert [('--include-cno' in args) for _, args in off] == [False, False]


# ---- errors before anything runs ------------------------------------------------------------------------------------------

@pytest.mark.parametrize('steps, code', [(['01', '08'], 'etx_unknown_step'), (['1'], 'etx_unknown_step'), ([], 'etx_steps_invalid'),
                                        ('01', 'etx_steps_invalid')])
def test_unknown_or_empty_steps_are_an_error_before_anything_is_planned(tmp_path, steps, code):
    base = template_base(tmp_path)
    with pytest.raises(FlowError) as caught:
        etx_flow.plan_jobs(base, edit_settings(steps=steps))
    assert code_of(caught) == code


def test_unknown_setting_names_are_refused(tmp_path):
    with pytest.raises(FlowError) as caught:
        etx_flow.plan_jobs(template_base(tmp_path), dict(edit_settings(), stepz=['01']))
    assert code_of(caught) == 'etx_unknown_setting'


def test_03_output_folders_that_do_not_match_are_an_error(tmp_path):
    base = template_base(tmp_path)
    edit_yaml(base / 'real.yaml', lambda d: d['target']['electrode_import_record'].update(
        path='data/assets/elsewhere/electrode_asset_import_record.json'))
    with pytest.raises(FlowError) as caught:
        etx_flow.plan_jobs(base, edit_settings(steps=['03']))
    assert code_of(caught) == 'etx_asset_folder_mismatch'
    etx_flow.plan_jobs(base, edit_settings(steps=['01']))        # the other steps do not look at the 03 folders
    base2 = template_base(tmp_path, 'wrongname')
    edit_yaml(base2 / 'real.yaml', lambda d: d['reference'].update(asset_manifest={'path': 'data/assets/reference/assets.json'}))
    with pytest.raises(FlowError) as caught:
        etx_flow.plan_jobs(base2, edit_settings(steps=['03']))
    assert code_of(caught) == 'etx_asset_folder_mismatch'
    base3 = template_base(tmp_path, 'nokeys')
    edit_yaml(base3 / 'real.yaml', lambda d: d['reference'].pop('asset_manifest'))
    with pytest.raises(FlowError) as caught:
        etx_flow.plan_jobs(base3, edit_settings(steps=['03']))
    assert code_of(caught) == 'etx_asset_path_missing'


def test_dl1_root_is_required_for_03_and_04(tmp_path):
    base = template_base(tmp_path)
    edit_yaml(base / 'compare2.yaml', lambda d: d['report'].pop('dl1_root'))
    for step in ('03', '04'):
        with pytest.raises(FlowError) as caught:
            etx_flow.plan_jobs(base, edit_settings(steps=[step]))
        assert code_of(caught) == 'etx_dl1_root_required'
    assert etx_flow.plan_jobs(base, edit_settings(steps=['01', '02', 'diag', '06']))     # the other steps do not need it


def test_align_args_must_not_repeat_what_the_config_files_supply(tmp_path):
    base = template_base(tmp_path)
    for flag in etx_flow.MANAGED_04_FLAGS:
        with pytest.raises(FlowError) as caught:
            etx_flow.plan_jobs(base, edit_settings(steps=['04'], align_args=['--top-k', '3', flag, 'x']))
        assert code_of(caught) == 'etx_align_args_managed', flag


def test_compare2_config_must_point_at_the_same_real_config(tmp_path):
    base = template_base(tmp_path)
    edit_yaml(base / 'compare2.yaml', lambda d: d.update(numeric_config='other.yaml'))
    with pytest.raises(FlowError) as caught:
        etx_flow.plan_jobs(base, edit_settings())
    assert code_of(caught) == 'etx_numeric_config_mismatch'


def test_missing_or_invalid_config_files_are_errors(tmp_path):
    base = template_base(tmp_path)
    with pytest.raises(FlowError) as caught:
        etx_flow.plan_jobs(base, edit_settings(real_config='nope.yaml'))
    assert code_of(caught) == 'etx_config_unreadable'
    (base / 'real.yaml').write_text('schema_version: other\n', encoding='utf-8')
    with pytest.raises(FlowError) as caught:
        etx_flow.plan_jobs(base, edit_settings())
    assert code_of(caught) == 'etx_config_invalid'


# ---- previous results: moved, never deleted ----------------------------------------------------------------------------------

OUTPUT_FOLDERS = ['data/imported/reference', 'data/imported/target', 'data/assets/reference', 'data/assets/target', 'data/compare2',
                  'outputs/numeric_real', 'data/changes', 'data/local', 'data/compare2_report']


def populate(base, folders=OUTPUT_FOLDERS):
    for folder in folders:
        (base / folder).mkdir(parents=True, exist_ok=True)
        (base / folder / 'file.txt').write_text(f'content of {folder}\n', encoding='utf-8')
        (base / folder / 'sub').mkdir()
        (base / folder / 'sub/deep.bin').write_bytes(b'\x00\x01' + folder.encode())


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None) for p in sorted(Path(root).rglob('*'))}


def test_archive_moves_only_the_folders_that_exist_and_the_chosen_steps_make(tmp_path):
    base = template_base(tmp_path)
    populate(base, ['data/imported/reference', 'data/compare2', 'data/changes', 'data/local'])      # target, assets, diag... are absent
    settings = edit_settings(run_tag='T1')
    moves = etx_flow.plan_archive(base, settings)
    resolved = base.resolve()
    assert [(s.relative_to(resolved).as_posix(), d.relative_to(resolved).as_posix()) for s, d in moves] == [
        ('data/imported/reference', 'data/_runs/T1/data/imported/reference'), ('data/compare2', 'data/_runs/T1/data/compare2'),
        ('data/changes', 'data/_runs/T1/data/changes'), ('data/local', 'data/_runs/T1/data/local')]
    assert (base / 'data/compare2').is_dir() and not (base / 'data/_runs').exists()       # planning moves nothing
    before = snapshot(base / 'data')
    expected_files = {name: data for name, data in before.items() if name.startswith(('compare2/', 'changes/')) and data is not None}
    done = etx_flow.archive_outputs(base, edit_settings(run_tag='T1', steps=['02', '04']))
    assert [d.relative_to(resolved).as_posix() for _, d in done] == ['data/_runs/T1/data/compare2', 'data/_runs/T1/data/changes']
    assert (base / 'data/imported/reference/file.txt').is_file() and (base / 'data/local/file.txt').is_file()       # not chosen: untouched
    assert not (base / 'data/compare2').exists() and not (base / 'data/changes').exists()
    assert (base / 'data/_runs/T1/data/compare2/file.txt').read_text(encoding='utf-8') == 'content of data/compare2\n'
    assert (base / 'data/_runs/T1/data/changes/sub/deep.bin').read_bytes() == b'\x00\x01data/changes'
    # nothing was deleted or copied: every file of the moved folders is at the archive path with the same bytes, and the file count is the same
    after = snapshot(base / 'data')
    moved = {name[len('_runs/T1/data/'):]: data for name, data in after.items() if name.startswith('_runs/T1/data/') and data is not None}
    assert moved == expected_files
    assert sum(1 for data in after.values() if data is not None) == sum(1 for data in before.values() if data is not None)


def test_archive_refuses_when_a_target_exists_and_moves_nothing(tmp_path):
    base = template_base(tmp_path)
    populate(base, ['data/compare2', 'data/changes'])
    (base / 'data/_runs/T1/data/changes').mkdir(parents=True)
    with pytest.raises(FlowError) as caught:
        etx_flow.archive_outputs(base, edit_settings(run_tag='T1', steps=['02', '04']))
    assert code_of(caught) == 'etx_archive_target_exists'
    assert (base / 'data/compare2/file.txt').is_file() and (base / 'data/changes/file.txt').is_file()
    assert not (base / 'data/_runs/T1/data/compare2').exists()                 # not even the first one that would have fit
    assert (base / 'data/_runs/T1/data/changes').is_dir() and not list((base / 'data/_runs/T1/data/changes').iterdir())


@pytest.mark.parametrize('tag', ['', '..', 'a/b', 'a\\b', ' x', '.'])
def test_run_tag_must_be_a_plain_folder_name(tmp_path, tag):
    base = template_base(tmp_path)
    with pytest.raises(FlowError) as caught:
        etx_flow.plan_archive(base, edit_settings(run_tag=tag))
    assert code_of(caught) == 'etx_run_tag_invalid'


def test_default_run_tag_is_a_utc_time_stamp(tmp_path):
    base = template_base(tmp_path)
    populate(base, ['data/changes'])
    ((_, dst),) = etx_flow.plan_archive(base, edit_settings(steps=['04']))
    assert re.fullmatch(r'\d{8}-\d{6}', dst.relative_to(base.resolve() / 'data/_runs').parts[0])


def test_output_folder_rules(tmp_path):
    base = template_base(tmp_path)
    edit_yaml(base / 'compare2.yaml', lambda d: d.update(output_dir='/tmp/elsewhere_vdist_t4/compare2'))
    with pytest.raises(FlowError) as caught:
        etx_flow.plan_archive(base, edit_settings(steps=['02']))
    assert code_of(caught) == 'etx_output_outside_base'
    other = template_base(tmp_path, 'nested')
    with pytest.raises(FlowError) as caught:        # one output folder inside another: the move order would be ambiguous
        populate(other, ['data/changes', 'data/changes/inner'])
        etx_flow.plan_archive(other, edit_settings(steps=['04', '06'], local_dir='data/changes/inner'))
    assert code_of(caught) == 'etx_output_nested'
    inside = template_base(tmp_path, 'inside')
    populate(inside, ['data'])
    with pytest.raises(FlowError) as caught:        # the archive folder would sit inside a folder that is being moved
        etx_flow.plan_archive(inside, edit_settings(steps=['04'], changes_dir='data'))
    assert code_of(caught) == 'etx_archive_inside_output'
    plain_file = template_base(tmp_path, 'file')
    (plain_file / 'data').mkdir()
    (plain_file / 'data/changes').write_text('not a folder', encoding='utf-8')
    with pytest.raises(FlowError) as caught:
        etx_flow.plan_archive(plain_file, edit_settings(steps=['04']))
    assert code_of(caught) == 'etx_output_not_a_folder'


def test_the_archive_paths_follow_the_config_files(tmp_path):
    """01 = both data_dirs, 03 = both asset folders, 02 = compare2 output_dir, diag = real output_dir: all from the config files."""
    base = template_base(tmp_path)

    def mutate(data):
        data['output_dir'] = 'out/diag'
        data['reference']['data_dir'] = 'imp/ref'
        data['target']['asset_manifest'] = {'path': 'ast/tgt/review_assets.json'}
        data['target']['electrode_import_record'] = {'path': 'ast/tgt/electrode_asset_import_record.json'}
    edit_yaml(base / 'real.yaml', mutate)
    edit_yaml(base / 'compare2.yaml', lambda d: d.update(output_dir='cmp'))
    folders = ['imp/ref', 'data/imported/target', 'data/assets/reference', 'ast/tgt', 'cmp', 'out/diag', 'data/changes']
    populate(base, folders)
    moves = etx_flow.plan_archive(base, edit_settings(run_tag='T', steps=['01', '03', '02', 'diag', '04']))
    assert sorted(s.relative_to(base.resolve()).as_posix() for s, _ in moves) == sorted(folders)


def test_prepare_dry_run_logs_the_plan_and_moves_nothing(tmp_path):
    base = template_base(tmp_path)
    populate(base)
    before = snapshot(base)
    lines = []
    assert etx_flow.prepare(base, edit_settings(run_tag='DRY'), dry_run=True, echo=lines.append) == []
    assert snapshot(base) == before and not (base / 'data/_runs').exists()
    text = '\n'.join(lines)
    assert text.count('  job ') == len(expected_default_jobs(edit_settings()['align_args']))
    assert text.count('  move ') == len(OUTPUT_FOLDERS) and 'DRY_RUN = True' in text
    assert 'job candidates-change --config real.yaml' in text


def test_prepare_moves_then_returns_the_jobs(tmp_path):
    base = template_base(tmp_path)
    populate(base, ['data/imported/reference', 'data/changes'])
    lines = []
    jobs = etx_flow.prepare(base, edit_settings(run_tag='RUN1'), echo=lines.append)
    assert jobs == expected_default_jobs(edit_settings()['align_args'])
    assert not (base / 'data/changes').exists() and (base / 'data/_runs/RUN1/data/changes/file.txt').is_file()
    assert (base / 'data/_runs/RUN1/data/imported/reference/sub/deep.bin').is_file()
    assert any('previous outputs to move' in line for line in lines)


def test_prepare_warns_when_a_product_id_differs_from_the_hotspot_folder(tmp_path):
    base = template_base(tmp_path)
    edit_yaml(base / 'real.yaml', lambda d: d['reference'].update(product_id='OTHER'))
    lines = []
    etx_flow.prepare(base, edit_settings(steps=['02']), dry_run=True, echo=lines.append)
    assert any('warning' in line and 'OTHER' in line for line in lines)


# ---- the generated runner file ----------------------------------------------------------------------------------------------

def test_runner_file_dry_run_prints_the_jobs_and_moves_nothing(tmp_path):
    base = template_base(tmp_path)
    populate(base, ['data/changes', 'data/compare2'])
    before = snapshot(base)
    script = flow_runner(tmp_path / 'runner', base, {'DRY_RUN': True, 'RUN_TAG': 'DRYRUN'})
    result = run_runner(sys.executable, script, tmp_path)
    log = (base / 'runs/simcompare_job_etx.log').read_text(encoding='utf-8')
    assert result.returncode == 0, result.stderr
    assert {k: v for k, v in snapshot(base).items() if not k.startswith('runs')} == before
    assert 'flow: DRY_RUN = True' in log and 'start ' not in log and 'package extracted to' in log
    assert re.search(r'payload_sha256=[0-9a-f]{64}', log)
    for command in ('import-hotspots', 'import-electrode-assets', 'compare2 ', 'validate-data', 'compare ', 'candidates-change',
                    'compare-windows', 'compare2-report'):
        assert f'  job {command}' in log, command
    assert 'move' in log and 'data/_runs/DRYRUN' in log
    print('\n--- DRY_RUN log of the template configs ---')
    print('\n'.join(line for line in log.splitlines() if 'flow:' in line or '  job ' in line or '  move ' in line))


def test_runner_file_stops_with_exit_2_on_a_plan_error_before_running_or_moving_anything(tmp_path):
    base = template_base(tmp_path)
    populate(base, ['data/changes'])
    before = snapshot(base)
    script = flow_runner(tmp_path / 'runner', base, {'STEPS': ['01', 'nine']})
    result = run_runner(sys.executable, script, tmp_path)
    log = (base / 'runs/simcompare_job_etx.log').read_text(encoding='utf-8')
    assert result.returncode == 2
    assert '오류: etx_unknown_step' in log and 'start ' not in log
    assert {k: v for k, v in snapshot(base).items() if not k.startswith('runs')} == before


def test_runner_file_edit_block_carries_the_agreed_names_and_no_jobs_list():
    text = _builder().FLOW_EDIT
    for name in ('STEPS', 'RUN_TAG', 'DRY_RUN', 'REAL_CONFIG', 'COMPARE2_CONFIG', 'INCLUDE_CNO', 'MAX_ASSET_MEMORY_MB', 'ALIGN_ARGS',
                 'EXTRA_WINDOWS_UM', 'JITTER_UM', 'DL2_MATCH_ARGS', 'DL2_DECISION_ARGS', 'CHANGES_DIR', 'CONFIRMED_DIR', 'LOCAL_DIR',
                 'REPORT_DIR', 'LOG_FILE', 'WORK_DIR', 'BASE_DIR'):
        assert re.search(rf'^{name} = ', text, re.M), name
    assert 'JOBS = [' not in text
    committed = (ROOT / 'bundles/etx/simcompare_job_etx.py').read_text(encoding='utf-8')
    assert committed.count('PAYLOAD_SHA256 = ') == 1 and 'run_jobs(_flow_jobs)' in committed       # the package payload is in the file once
