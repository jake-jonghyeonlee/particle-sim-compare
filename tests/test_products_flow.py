"""pasx/T6: the integrated runner's product mode (MODE = "products"): JOBS, validation, DRY_RUN, and a real end-to-end run."""
import re
import sys
from pathlib import Path

import pytest

from simcompare import etx_flow
from simcompare.etx_flow import FlowError
from test_etx_flow import _builder, flow_runner, run_runner, snapshot
from test_products_assets import make_world

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def world(tmp_path):
    w = make_world(tmp_path)
    w.doc['common']['report'] = {'diameter_um': 3}
    for name in w.doc['products']:
        w.doc['products'][name]['unit_size_um'] = [0.2, 0.2]
    w.write_doc()
    return w


def settings(**overrides):
    found = {'mode': 'products', 'run_tag': 'T1'}
    found.update(overrides)
    return found


def jobs_of(world, **overrides):
    return [(job.step, job[0], list(job[1])) for job in etx_flow.plan_product_jobs(world.base, settings(**overrides))]


def asset(step, product, *extra):
    return ('P' + step, 'products-step', ['--config', 'products.yaml', '--product', product, '--step', step] + list(extra)
            + ['--run-tag', 'T1'])


def pair(step_id, step, first, second, *extra):
    return (step_id, 'pair-step', ['--config', 'products.yaml', '--pair', first, second, '--step', step, '--run-tag', 'T1'] + list(extra))


# ---- JOBS -------------------------------------------------------------------------------------------------------------------

def test_a_assets_only_by_default_for_every_registered_product_01_then_02(world):
    assert jobs_of(world) == [asset('01', 'P1'), asset('01', 'P2'), asset('01', 'P3'),
                              asset('02', 'P1'), asset('02', 'P2'), asset('02', 'P3')]
    assert jobs_of(world, products=['P2', 'P1'], asset_steps=['02', '01']) == [      # PRODUCTS order, fixed 01 -> 02 order
        asset('01', 'P2'), asset('01', 'P1'), asset('02', 'P2'), asset('02', 'P1')]


def test_b_pair_steps_only_when_asset_steps_is_empty(world):
    assert jobs_of(world, asset_steps=[], compare=[('P1', 'P2')]) == [
        pair('C02', 'compose', 'P1', 'P2'), pair('C08', 'ledger', 'P1', 'P2'),
        pair('C08', 'maps', 'P1', 'P2', '--no-cards'), pair('C08', 'report', 'P1', 'P2')]


def test_c_compare_steps_08_plans_ledger_maps_report_only_and_cards_follow_the_setting(world):
    jobs = jobs_of(world, asset_steps=[], compare=[('P1', 'P2')], compare_steps=['08'])
    assert [(step, args[args.index('--step') + 1]) for step, _, args in jobs] == [
        ('C08', 'ledger'), ('C08', 'maps'), ('C08', 'report')]
    assert '--no-cards' in jobs[1][2] and '--cards' not in jobs[1][2]
    with_cards = jobs_of(world, asset_steps=[], compare=[('P1', 'P2')], compare_steps=['08'], ledger_maps_cards=True)
    assert with_cards[1][2][-1] == '--cards'
    off = jobs_of(world, asset_steps=[], compare=[('P1', 'P2')], compare_steps=['08'], ledger_maps_cards=False)
    assert off[1][2][-1] == '--no-cards'


def test_pairs_run_one_after_the_other_and_every_job_shares_the_run_tag(world):
    jobs = jobs_of(world, products=['P1', 'P2'], compare=[('P1', 'P2'), ('P2', 'P1')])
    assert [(step, args[args.index('--pair') + 1:args.index('--pair') + 3] if '--pair' in args else None) for step, _, args in jobs[4:]] == [
        ('C02', ['P1', 'P2']), ('C02', ['P2', 'P1']),
        ('C08', ['P1', 'P2']), ('C08', ['P1', 'P2']), ('C08', ['P1', 'P2']),
        ('C08', ['P2', 'P1']), ('C08', ['P2', 'P1']), ('C08', ['P2', 'P1'])]
    assert all(args[args.index('--run-tag') + 1] == 'T1' for _, _, args in jobs)


def test_d_force_assets_add_force_to_that_products_steps_only(world):
    jobs = jobs_of(world, products=['P1', 'P2'], force_assets=['P2'])
    assert jobs == [asset('01', 'P1'), asset('01', 'P2', '--force'), asset('02', 'P1'), asset('02', 'P2', '--force')]


def test_record_maps_settings_only_touch_the_02_jobs(world):
    """pasx/T8: the defaults add nothing (the job lists above stay as they are); RECORD_MAPS = False and a smaller
    MAPS_SRC_MAX_BYTES become `products-step 02` arguments."""
    off = jobs_of(world, products=['P1'], record_maps=False)
    assert off == [asset('01', 'P1'), asset('02', 'P1', '--no-record-maps')]
    small = jobs_of(world, products=['P1'], maps_src_max_bytes=10 ** 9)
    assert small == [asset('01', 'P1'), asset('02', 'P1', '--maps-src-max-bytes', '1000000000')]
    both = jobs_of(world, products=['P1'], force_assets=['P1'], record_maps=False, maps_src_max_bytes=10 ** 9)
    assert both == [asset('01', 'P1', '--force'), asset('02', 'P1', '--force', '--no-record-maps')]
    assert jobs_of(world, products=['P1'], record_maps=True, maps_src_max_bytes=32 * 1024 ** 3) == jobs_of(world, products=['P1'])


@pytest.mark.parametrize('overrides', [{'record_maps': 1}, {'record_maps': None}, {'maps_src_max_bytes': 0},
                                       {'maps_src_max_bytes': -5}, {'maps_src_max_bytes': True},
                                       {'maps_src_max_bytes': '1'}])
def test_record_maps_settings_are_validated(world, overrides):
    with pytest.raises(etx_flow.FlowError) as caught:
        etx_flow.plan_product_jobs(world.base, settings(products=['P1'], **overrides))
    assert caught.value.code == 'etx_setting_invalid'


def test_jobs_carry_step_ids_and_labels(world):
    jobs = etx_flow.plan_product_jobs(world.base, settings(compare=[('P1', 'P2')]))
    labels = {job.step: job.label for job in jobs}
    assert labels == {'P01': 'hotspot 자산', 'P02': 'side 자산', 'C02': 'compare2 조합', 'C08': '장부·지도·보고서'}


def test_plan_jobs_dispatches_to_product_mode_without_real_yaml(world):
    assert not (world.base / 'real.yaml').exists()
    assert etx_flow.plan_jobs(world.base, settings(asset_steps=['01'], products=['P1'])) == [
        ('products-step', ['--config', 'products.yaml', '--product', 'P1', '--step', '01', '--run-tag', 'T1'])]


# ---- validation (FlowError = exit 2, nothing is written) -----------------------------------------------------------------------

@pytest.mark.parametrize('overrides, code', [
    ({'compare': [('P1', 'P3')]}, 'etx_compare_cross_group'),           # P3 is in group H
    ({'compare': [('P1', 'NOPE')]}, 'etx_product_unknown'),
    ({'products': ['P1', 'NOPE']}, 'etx_product_unknown'),
    ({'compare': [('P1', 'P1')]}, 'etx_compare_same_product'),
    ({'compare': [('P1', 'P2'), ('P1', 'P2')]}, 'etx_setting_invalid'),
    ({'compare': [('P1',)]}, 'etx_setting_invalid'),
    ({'products': ['P1', 'P1']}, 'etx_setting_invalid'),
    ({'asset_steps': ['03']}, 'etx_asset_steps_invalid'),
    ({'asset_steps': '01'}, 'etx_asset_steps_invalid'),
    ({'compare_steps': ['01']}, 'etx_compare_steps_invalid'),
    ({'force_assets': ['NOPE']}, 'etx_product_unknown'),
    ({'force_assets': ['P3'], 'products': ['P1']}, 'etx_force_not_selected'),
    ({'force_assets': ['P1'], 'asset_steps': []}, 'etx_force_not_selected'),
    ({'asset_steps': [], 'compare': []}, 'etx_no_jobs'),
    ({'asset_steps': [], 'compare': [('P1', 'P2')], 'compare_steps': []}, 'etx_no_jobs'),
    ({'run_tag': 'a/b'}, 'etx_run_tag_invalid'),
    ({'ledger_maps_cards': 'yes'}, 'etx_setting_invalid'),
    ({'mode': 'both'}, 'etx_mode_invalid'),
    ({'products_config': 'missing.yaml'}, 'etx_config_unreadable'),
])
def test_e_planning_errors_are_flow_errors_before_anything_is_written(world, overrides, code):
    before = snapshot(world.base)
    with pytest.raises(FlowError) as excinfo:
        etx_flow.plan_product_jobs(world.base, settings(**overrides))
    assert excinfo.value.code == code
    assert snapshot(world.base) == before


def test_an_invalid_products_yaml_is_a_flow_error_with_its_own_code(world):
    world.doc['groups']['G'].append('GHOST')
    world.write_doc()
    with pytest.raises(FlowError) as excinfo:
        etx_flow.plan_product_jobs(world.base, settings())
    assert excinfo.value.code == 'unknown_product_in_group'


def test_pair_mode_ignores_the_product_values(tmp_path):
    from test_etx_flow import edit_settings, template_base
    base = template_base(tmp_path)
    plain = etx_flow.plan_jobs(base, edit_settings(steps=['02', '08']))
    noisy = etx_flow.plan_jobs(base, edit_settings(steps=['02', '08'], products=['NOPE'], compare=[('X', 'X')], asset_steps=['zz'],
                                                   products_config='absent.yaml', force_assets=['Q']))
    assert noisy == plain


def test_pair_mode_cards_false_adds_only_no_cards_to_the_08_maps_job(tmp_path):
    from test_etx_flow import edit_settings, template_base
    base = template_base(tmp_path)
    plain = etx_flow.plan_jobs(base, edit_settings(steps=['02', '08']))
    off = etx_flow.plan_jobs(base, edit_settings(steps=['02', '08'], ledger_maps_cards=False))
    default = etx_flow.plan_jobs(base, edit_settings(steps=['02', '08'], ledger_maps_cards=True))
    assert default == plain
    changed = [(a, b) for a, b in zip(plain, off) if a != b]
    assert len(plain) == len(off) and len(changed) == 1 and changed[0][0][0] == 'ledger-maps'
    assert changed[0][1][1] == changed[0][0][1] + ['--no-cards'] and changed[0][0][1][-1] == changed[0][1][1][-2]


# ---- DRY_RUN -------------------------------------------------------------------------------------------------------------------

def test_f_prepare_dry_run_lists_the_jobs_and_the_asset_state_and_writes_nothing(world):
    before = snapshot(world.base)
    lines = []
    assert etx_flow.prepare(world.base, settings(compare=[('P1', 'P2')]), dry_run=True, echo=lines.append) == []
    assert snapshot(world.base) == before
    text = '\n'.join(lines)
    assert text.count('  job ') == 6 + 1 + 3
    assert 'job [P01] products-step --config products.yaml --product P1 --step 01 --run-tag T1' in text
    assert 'job [C08] pair-step --config products.yaml --pair P1 P2 --step maps --run-tag T1 --no-cards' in text
    assert '  asset P1: 없음' in text and '  asset P3: 없음' in text
    assert re.search(r'  pair P1__P2 compose: .*asset_missing|  pair P1__P2 compose: 판정 불가', text)
    assert 'DRY_RUN = True' in text and 'nothing was run or moved' in text
    print('\n--- product mode DRY_RUN ---\n' + text)


def test_prepare_in_product_mode_moves_no_output_folder_and_returns_flow_jobs(world):
    for folder in ('data/imported/reference', 'data/compare2', 'data/ledger'):
        (world.base / folder).mkdir(parents=True)
        (world.base / folder / 'keep.txt').write_text('x', encoding='utf-8')
    before = snapshot(world.base)
    jobs = etx_flow.prepare(world.base, settings(products=['P1']), echo=lambda line: None)
    assert isinstance(jobs, etx_flow.FlowJobs) and jobs.finalizers == [('products-sheet', ['--config', 'products.yaml'])]
    assert [job[0] for job in jobs] == ['products-step', 'products-step']
    assert snapshot(world.base) == before and not (world.base / 'data/_runs').exists()


def test_the_finishing_sheet_job_gets_no_record_maps_when_recording_is_off(world):
    """R8b:1: RECORD_MAPS = False passes --no-record-maps, so a side without a recording stays 정상 in the sheet."""
    off = etx_flow.prepare(world.base, settings(products=['P1'], record_maps=False), echo=lambda line: None)
    assert off.finalizers == [('products-sheet', ['--config', 'products.yaml', '--no-record-maps'])]
    assert etx_flow.product_finalizers('products.yaml') == [('products-sheet', ['--config', 'products.yaml'])]
    assert etx_flow.product_finalizers('p.yaml', False) == [('products-sheet', ['--config', 'p.yaml', '--no-record-maps'])]


def test_asset_state_lines_are_computed_and_printed_only_in_dry_run(world, monkeypatch):
    calls = []
    monkeypatch.setattr(etx_flow, '_product_status_lines', lambda plan: calls.append(1) or ['  asset X: 없음'])
    lines = []
    etx_flow.prepare(world.base, settings(products=['P1']), echo=lines.append)
    assert calls == [] and not any('asset state' in line for line in lines)
    lines = []
    etx_flow.prepare(world.base, settings(products=['P1']), dry_run=True, echo=lines.append)
    assert calls == [1] and any('asset state' in line for line in lines) and '  asset X: 없음' in lines


def test_a_fresh_product_has_a_state_in_dry_run_after_its_assets_exist(world):
    from test_products_assets import build_all
    build_all(world, products=('P1',))
    lines = []
    etx_flow.prepare(world.base, settings(products=['P1']), dry_run=True, echo=lines.append)
    assert '  asset P1: 정상' in lines, lines


# ---- the runner file, with real commands ------------------------------------------------------------------------------------


def _runner(tmp_path, world, name, **overrides):
    values = {'MODE': 'products', 'RUN_TAG': name}
    values.update(overrides)
    return flow_runner(tmp_path / ('runner-' + name), world.base, values)


def test_runner_edit_block_carries_the_product_names_and_pair_defaults():
    text = _builder().FLOW_EDIT
    assert re.search(r'^MODE = "pair"', text, re.M)
    for name in ('PRODUCTS_CONFIG', 'PRODUCTS', 'ASSET_STEPS', 'COMPARE', 'COMPARE_STEPS', 'FORCE_ASSETS', 'LEDGER_MAPS_CARDS',
                 'RECORD_MAPS', 'MAPS_SRC_MAX_BYTES'):
        assert re.search(rf'^{name} = ', text, re.M), name
    assert re.search(r'^RECORD_MAPS = True', text, re.M) and re.search(r'^MAPS_SRC_MAX_BYTES = 32 \* 1024\*\*3', text, re.M)
    assert re.search(r'^ASSET_STEPS = \["01", "02"\]', text, re.M) and re.search(r'^COMPARE_STEPS = \["02", "08"\]', text, re.M)
    assert re.search(r'^LEDGER_MAPS_CARDS = None', text, re.M)


def test_runner_dry_run_in_product_mode_prints_the_plan_without_real_yaml(world):
    script = _runner(world.base.parent, world, 'DRY', DRY_RUN=True, COMPARE=[('P1', 'P2')], PRODUCTS=['P1', 'P2'])
    result = run_runner(sys.executable, script, world.base.parent)
    log = (world.base / 'runs/simcompare_job_etx.log').read_text(encoding='utf-8')
    assert result.returncode == 0, result.stderr
    assert 'flow: DRY_RUN = True' in log and 'start ' not in log
    assert '  job [P01] products-step' in log and '  job [C08] pair-step' in log and '  asset P1: 없음' in log
    assert not any((world.base / 'data' / name).exists() for name in ('products', 'compare', '_runs'))


def test_runner_stops_with_exit_2_on_a_product_plan_error(world):
    script = _runner(world.base.parent, world, 'BAD', COMPARE=[('P1', 'P3')])
    result = run_runner(sys.executable, script, world.base.parent)
    log = (world.base / 'runs/simcompare_job_etx.log').read_text(encoding='utf-8')
    assert result.returncode == 2 and '오류: etx_compare_cross_group' in log and 'start ' not in log


def test_runner_runs_assets_then_pair_to_the_end_and_a_second_run_skips_everything(world):
    first = _runner(world.base.parent, world, 'R1', PRODUCTS=['P1', 'P2'], COMPARE=[('P1', 'P2')])
    result = run_runner(sys.executable, first, world.base.parent)
    log = (world.base / 'runs/simcompare_job_etx.log').read_text(encoding='utf-8')
    assert result.returncode == 0, result.stderr + log[-3000:]
    for marker in ('===== [P01] hotspot 자산 시작', '===== [P02] side 자산 시작', '===== [C02] compare2 조합 시작',
                   '===== [C08] 장부·지도·보고서 시작'):
        assert marker in log, marker
    pair_dir = world.base / 'data/compare/P1__P2'
    for folder, document in (('compare2', 'compare2.json'), ('ledger', 'ledger.json'), ('ledger_maps', 'ledger_maps.json'),
                             ('ledger_report', 'ledger_report.html')):
        assert (pair_dir / folder / document).is_file(), (folder, document)
    assert not (world.base / 'data/imported').exists() and not (world.base / 'data/compare2').exists()      # the pair folders are not used
    assert (world.base / 'data/products/P1').is_dir() and (world.base / 'data/products/P2').is_dir()
    assert not (world.base / 'data/products/P3').exists()
    assert 'skip fresh' not in log
    # the second run: assets and the composition are fresh -> skipped; the 08 steps are rebuilt (their old output is archived)
    second = _runner(world.base.parent, world, 'R2', PRODUCTS=['P1', 'P2'], COMPARE=[('P1', 'P2')])
    result = run_runner(sys.executable, second, world.base.parent)
    log = (world.base / 'runs/simcompare_job_etx.log').read_text(encoding='utf-8')
    tail = log[log.rindex('payload_sha256'):]
    assert result.returncode == 0, result.stderr + tail[-3000:]
    for skipped in ('skip fresh 01 P1', 'skip fresh 01 P2', 'skip fresh 02 side P1', 'skip fresh 02 side P2', 'skip fresh compose P1__P2'):
        assert skipped in tail, skipped
    assert 'build' not in [line for line in tail.splitlines() if 'products-step' in line and 'exit' in line]


def test_runner_updates_the_sheet_after_a_good_run_and_after_the_main_job_failed(world):
    good = _runner(world.base.parent, world, 'S1', PRODUCTS=['P1'], ASSET_STEPS=['01', '02'])
    result = run_runner(sys.executable, good, world.base.parent)
    log = (world.base / 'runs/simcompare_job_etx.log').read_text(encoding='utf-8')
    assert result.returncode == 0, result.stderr + log[-3000:]
    assert '마무리 job products-sheet exit 0' in log
    csv_path = world.base / 'data/products/products_sheet.csv'
    text = csv_path.read_bytes().decode('utf-8-sig')
    assert text.count('\r\n') == 1 + 3 and '\r\nP1,' in text           # header + three registered products
    assert (world.base / 'data/products/products_sheet.html').is_file()
    # a main job that stops with exit 2 (the pair has no assets: compose refuses): the sheet is still made again
    csv_path.unlink()
    (world.base / 'data/products/products_sheet.html').unlink()
    bad = _runner(world.base.parent, world, 'S2', ASSET_STEPS=[], COMPARE=[('P2', 'P1')], COMPARE_STEPS=['02'])
    result = run_runner(sys.executable, bad, world.base.parent)
    log = (world.base / 'runs/simcompare_job_etx.log').read_text(encoding='utf-8')
    tail = log[log.rindex('payload_sha256'):]
    assert result.returncode == 2, result.stderr + tail[-3000:]
    assert '마무리 job products-sheet exit 0' in tail and '경고' not in tail
    rows = csv_path.read_bytes().decode('utf-8-sig')
    assert '\r\nP1,G,' in rows and '\r\nP2,G,' in rows and '\r\nP3,H,' in rows and '없음' in rows


def test_runner_force_assets_rebuild_a_fresh_product(world):
    first = _runner(world.base.parent, world, 'F1', PRODUCTS=['P1'])
    assert run_runner(sys.executable, first, world.base.parent).returncode == 0
    forced = _runner(world.base.parent, world, 'F2', PRODUCTS=['P1'], FORCE_ASSETS=['P1'])
    result = run_runner(sys.executable, forced, world.base.parent)
    log = (world.base / 'runs/simcompare_job_etx.log').read_text(encoding='utf-8')
    tail = log[log.rindex('payload_sha256'):]
    assert result.returncode == 0, result.stderr + tail[-3000:]
    assert 'skip fresh 01 P1' not in tail and '--force' in tail
    assert 'built 01 P1' in tail and 'built 02 side P1' in tail            # a fresh product was rebuilt (current + previous generation kept)


# ---- pasx/T13: PRODUCTS_IMPORT runs products-import before the product plan ---------------------------------------------------

IMPORT = [('real.yaml', 'compare2.yaml', 'G')]


@pytest.fixture
def iworld(tmp_path):
    """real.yaml + compare2.yaml for P1/P2 and NO products.yaml (the import makes it)."""
    import yaml
    from test_products_import import make_real_world
    base = make_real_world(tmp_path)
    path = base / 'compare2.yaml'
    doc = yaml.safe_load(path.read_text(encoding='utf-8'))
    for role in ('reference', 'target'):
        doc['products'][role]['unit_size_um'] = [0.2, 0.2]
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding='utf-8')
    (base / 'products.yaml').unlink()
    return base


def import_settings(**overrides):
    found = {'products_import': IMPORT, 'compare': [('P1', 'P2')]}
    found.update(overrides)
    return settings(**found)


def edit_yaml(path, change):
    import yaml
    doc = yaml.safe_load(path.read_text(encoding='utf-8'))
    change(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding='utf-8')


def test_edit_block_carries_the_import_names():
    text = _builder().FLOW_EDIT
    assert re.search(r'^PRODUCTS_IMPORT = \[\]', text, re.M) and re.search(r'^PRODUCTS_IMPORT_UPDATE = False', text, re.M)


def test_import_makes_products_yaml_before_the_plan_and_a_second_prepare_says_unchanged(iworld):
    lines = []
    jobs = etx_flow.prepare(iworld, import_settings(), echo=lines.append)
    assert (iworld / 'products.yaml').is_file() and len(jobs) == 4 + 1 + 3
    assert any('products-import real.yaml compare2.yaml group G: added (P1, P2)' in line for line in lines), lines
    before = (iworld / 'products.yaml').read_bytes()
    lines = []
    etx_flow.prepare(iworld, import_settings(), echo=lines.append)
    assert any('group G: unchanged (P1, P2)' in line for line in lines) and (iworld / 'products.yaml').read_bytes() == before


def test_runner_imports_then_runs_to_the_end_and_a_second_run_skips_the_assets(iworld):
    first = _runner(iworld.parent, type('W', (), {'base': iworld}), 'I1', PRODUCTS_IMPORT=IMPORT, COMPARE=[('P1', 'P2')])
    result = run_runner(sys.executable, first, iworld.parent)
    log = (iworld / 'runs/simcompare_job_etx.log').read_text(encoding='utf-8')
    assert result.returncode == 0, result.stderr + log[-3000:]
    assert 'group G: added (P1, P2)' in log and (iworld / 'products.yaml').is_file()
    assert (iworld / 'data/compare/P1__P2/ledger_report/ledger_report.html').is_file()
    second = _runner(iworld.parent, type('W', (), {'base': iworld}), 'I2', PRODUCTS_IMPORT=IMPORT, COMPARE=[('P1', 'P2')])
    result = run_runner(sys.executable, second, iworld.parent)
    log = (iworld / 'runs/simcompare_job_etx.log').read_text(encoding='utf-8')
    tail = log[log.rindex('group G:'):]
    assert result.returncode == 0, result.stderr + tail[-3000:]
    assert 'group G: unchanged (P1, P2)' in tail and 'skip fresh 01 P1' in tail and 'skip fresh 02 side P2' in tail


def test_a_value_conflict_stops_the_run_and_update_overwrites_but_keeps_the_note(iworld):
    etx_flow.prepare(iworld, import_settings(), echo=lambda line: None)
    edit_yaml(iworld / 'products.yaml', lambda d: d['products']['P1'].update(note='by hand'))
    edit_yaml(iworld / 'compare2.yaml', lambda d: d['report'].update(diameter_um=4))
    before = (iworld / 'products.yaml').read_bytes()
    with pytest.raises(FlowError) as raised:
        etx_flow.prepare(iworld, import_settings(), echo=lambda line: None)
    assert raised.value.code == 'products_import_conflict' and 'report' in str(raised.value)
    assert (iworld / 'products.yaml').read_bytes() == before
    lines = []
    jobs = etx_flow.prepare(iworld, import_settings(products_import_update=True), echo=lines.append)
    assert len(jobs) == 8
    assert any('group G: updated' in line and 'affected products: P1, P2' in line for line in lines), lines
    import yaml
    doc = yaml.safe_load((iworld / 'products.yaml').read_text(encoding='utf-8'))
    assert doc['common']['report']['diameter_um'] == 4 and doc['products']['P1']['note'] == 'by hand'


def test_a_group_conflict_stops_the_run_and_leaves_the_file(iworld):
    etx_flow.prepare(iworld, import_settings(), echo=lambda line: None)
    before = (iworld / 'products.yaml').read_bytes()
    with pytest.raises(FlowError) as raised:
        etx_flow.prepare(iworld, import_settings(products_import=[('real.yaml', 'compare2.yaml', 'H')]), echo=lambda line: None)
    assert raised.value.code == 'products_import_group_conflict' and (iworld / 'products.yaml').read_bytes() == before


def test_an_incompatible_pair_stops_the_run_and_makes_no_file(iworld):
    edit_yaml(iworld / 'real.yaml', lambda d: d['target']['condition_assignment'].update(
        layer_stack=d['target']['condition_assignment']['layer_stack'][:1]))
    with pytest.raises(FlowError) as raised:
        etx_flow.prepare(iworld, import_settings(), echo=lambda line: None)
    assert raised.value.code == 'group_incompatible' and not (iworld / 'products.yaml').exists()


def test_a_missing_file_and_a_bad_entry_are_flow_errors(iworld):
    with pytest.raises(FlowError) as raised:
        etx_flow.prepare(iworld, import_settings(products_import=[('nope.yaml', 'compare2.yaml', 'G')]), echo=lambda line: None)
    assert raised.value.code.startswith('products_import_')
    for bad in ([('real.yaml', 'compare2.yaml')], 'real.yaml', [('real.yaml', 'compare2.yaml', 3)]):
        with pytest.raises(FlowError) as raised:
            etx_flow.prepare(iworld, import_settings(products_import=bad), echo=lambda line: None)
        assert raised.value.code == 'etx_setting_invalid'
    with pytest.raises(FlowError):
        etx_flow.prepare(iworld, import_settings(products_import_update='yes'), echo=lambda line: None)
    assert not (iworld / 'products.yaml').exists()


def test_dry_run_without_products_yaml_checks_the_import_and_skips_the_plan(iworld):
    before = snapshot(iworld)
    lines = []
    assert etx_flow.prepare(iworld, import_settings(), dry_run=True, echo=lines.append) == []
    text = '\n'.join(lines)
    assert 'group G --check: would add (P1, P2)' in text and '계획 생략' in text and '  job ' not in text
    assert snapshot(iworld) == before
    print('\n--- DRY_RUN without products.yaml ---\n' + text)


def test_dry_run_with_products_yaml_shows_the_check_and_the_existing_plan(iworld):
    etx_flow.prepare(iworld, import_settings(), echo=lambda line: None)
    edit_yaml(iworld / 'compare2.yaml', lambda d: d['report'].update(diameter_um=4))
    before = snapshot(iworld)
    lines = []
    etx_flow.prepare(iworld, import_settings(products_import_update=True), dry_run=True, echo=lines.append)
    text = '\n'.join(lines)
    assert 'group G --check: would update' in text and text.count('  job ') == 8 and '계획 생략' not in text
    assert snapshot(iworld) == before


def test_pair_mode_ignores_the_import_values(tmp_path):
    from test_etx_flow import edit_settings, template_base
    base = template_base(tmp_path)
    plain = etx_flow.plan_jobs(base, edit_settings(steps=['02', '08']))
    noisy = etx_flow.plan_jobs(base, edit_settings(steps=['02', '08'], products_import=[('x',)], products_import_update=7))
    assert noisy == plain
