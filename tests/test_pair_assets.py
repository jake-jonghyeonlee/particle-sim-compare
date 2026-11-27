"""pasx/T12: `pair-step` compose / ledger / maps / report on product assets, compose staleness, refusals, E4 equivalence
with the classic pair flow, and a product-mode 08 that has no 03 output."""
import json
import os
import re
from pathlib import Path

import pytest
import yaml

import test_compare2 as tc
from simcompare import cli
from simcompare import compare2_side as cs
from simcompare import pair_assets as pr
from simcompare import products_assets as pa
from simcompare import products_config as pc
from simcompare.adapters import real_hotspot as rh
from simcompare.numeric_schema import NumericContractError
from test_products_assets import ALLOWED_DIFFERENCES, World, json_differences, make_world, step   # noqa: F401


@pytest.fixture
def world(tmp_path):
    w = make_world(tmp_path)
    w.doc['common']['report'] = {'diameter_um': 3}
    for name in w.doc['products']:
        w.doc['products'][name]['unit_size_um'] = [0.2, 0.2]
    w.write_doc()
    return w


@pytest.fixture
def built(world):
    for product in ('P1', 'P2', 'P3'):
        assert step(world, product, '01', 'T0')[0] == 0
    for product in ('P1', 'P2', 'P3'):
        assert step(world, product, '02', 'T0')[0] == 0
    return world


def pstep(world, first, second, name, tag='T1', cards=False):
    outs, errs = [], []
    code = pr.run_pair_step(world.path, first, second, name, run_tag=tag, cards=cards, out=outs.append, err=errs.append)
    return code, outs, errs


def pair_dir(world, pair='P1__P2'):
    return world.base / 'data/compare' / pair


def pair_log(world, pair='P1__P2'):
    path = pair_dir(world, pair) / 'pair_log.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def run_all(world, tag='T1', cards=False):
    for name in pr.STEPS:
        code, outs, errs = pstep(world, 'P1', 'P2', name, tag, cards)
        assert code in (0, 3), (name, outs, errs)


# ---- acceptance 1: the four steps -----------------------------------------------------------------------------------

def test_four_steps_make_the_pair_folder_and_the_log(built):
    world = built
    code, outs, errs = pstep(world, 'P1', 'P2', 'compose')
    assert code == 0 and outs == ['built compose P1__P2 exit 0'] and not errs
    folder = pair_dir(world)
    assert {p.name for p in (folder / 'compare2').iterdir()} >= {'compare2.json', 'compose.json', 'execution.json'}
    for name in ('ledger', 'maps', 'report'):
        code, outs, errs = pstep(world, 'P1', 'P2', name)
        assert code in (0, 3) and outs[-1].startswith('built %s P1__P2' % name), (name, outs, errs)
    assert (folder / 'ledger/ledger.json').is_file() and (folder / 'ledger_maps/ledger_maps.json').is_file()
    assert (folder / 'ledger_report/ledger_report.html').is_file()
    maps = json.loads((folder / 'ledger_maps/ledger_maps.json').read_text(encoding='utf-8'))
    assert maps['options']['cards'] is False and maps['cards'] == []              # product mode: cards off by default
    log = pair_log(world)
    assert [(l['step'], l['action']) for l in log] == [('compose', 'build'), ('ledger', 'build'), ('maps', 'build'),
                                                       ('report', 'build')]
    first = log[0]
    assert set(first) == {'at', 'run_tag', 'step', 'action', 'exit_code', 'error_code', 'elapsed_s', 'reasons', 'inputs'}
    assert first['run_tag'] == 'T1' and first['error_code'] is None and first['elapsed_s'] >= 0
    assert set(first['inputs']) >= {'products_yaml_sha256', 'side_sha256', 'side_generation', 'hotspots_generation'}
    assert len(first['inputs']['side_sha256']['P1']) == 64
    assert 'compare2_json_sha256' not in first['inputs'] or len(first['inputs']['compare2_json_sha256']) == 64
    assert len(log[1]['inputs']['compare2_json_sha256']) == 64
    # the generated settings exist and the old outputs were not moved (the folders were new)
    assert (world.base / 'gen.pair.P1__P2.compare2.yaml').is_file() and not (world.base / 'data/_runs/T1/compare').exists()


def test_cli_pair_step_and_cards_flag(built, capsys):
    argv = ['pair-step', '--config', str(built.path), '--pair', 'P1', 'P2', '--run-tag', 'C1']
    assert cli.main(argv + ['--step', 'compose']) == 0
    assert cli.main(argv + ['--step', 'ledger']) == 0
    assert cli.main(argv + ['--step', 'maps', '--cards']) in (0, 3)
    maps = json.loads((pair_dir(built) / 'ledger_maps/ledger_maps.json').read_text(encoding='utf-8'))
    assert 'cards' not in maps['options']                                         # cards on = the old options record
    with pytest.raises(SystemExit):
        cli.main(argv + ['--step', 'maps', '--cards', '--no-cards'])
    assert cli.main(argv + ['--step', 'maps', '--no-cards']) in (0, 3)


# ---- acceptance 2: compose skip and staleness -----------------------------------------------------------------------------

def test_second_compose_skips_and_a_density_change_makes_later_steps_refuse(built):
    world = built
    assert pstep(world, 'P1', 'P2', 'compose')[0] == 0
    before = (pair_dir(world) / 'compare2/compare2.json').read_bytes()
    code, outs, _errs = pstep(world, 'P1', 'P2', 'compose', 'T2')
    assert code == 0 and outs == ['skip fresh compose P1__P2'] and not (world.base / 'data/_runs/T2').exists()
    assert (pair_dir(world) / 'compare2/compare2.json').read_bytes() == before
    assert pstep(world, 'P1', 'P2', 'ledger', 'T2')[0] == 0                       # fresh compose: the later step runs
    assert [(l['step'], l['action']) for l in pair_log(world)][-3:] == [('compose', 'build'), ('compose', 'skip'),
                                                                        ('ledger', 'build')]
    density = world.base / 'data/input/density/mobile_line.csv'
    density.write_text(density.read_text(encoding='utf-8').replace('5.0', '5.5'), encoding='utf-8')
    for name in ('ledger', 'maps', 'report'):
        code, outs, errs = pstep(world, 'P1', 'P2', name, 'T3')
        assert code == 2 and any('pair_compose_stale' in e and 'density_changed' in e and 'COMPARE_STEPS' in e for e in errs), errs
    assert (pair_dir(world) / 'ledger/ledger.json').is_file()                    # a refusal moves nothing
    assert not (world.base / 'data/_runs/T3').exists()
    assert pair_log(world)[-1]['error_code'] == 'pair_compose_stale' and pair_log(world)[-1]['action'] == 'fail'
    code, outs, _errs = pstep(world, 'P1', 'P2', 'compose', 'T4')                 # the density is not part of the side
    assert code == 0 and outs[0].startswith('archived data/compare/P1__P2/compare2') and outs[-1].startswith('built compose')
    assert (world.base / 'data/_runs/T4/compare/P1__P2/compare2/compose.json').is_file()
    assert pstep(world, 'P1', 'P2', 'ledger', 'T4')[0] == 0


def test_the_computed_compose_deps_equal_the_ones_compose_json_records(built):
    world = built
    assert pstep(world, 'P1', 'P2', 'compose')[0] == 0
    config = pc.load_products_config(world.path)
    ctx = pr.PairContext(config, 'P1', 'P2', 'T1', print, print)
    pr.check_pair_assets(ctx)
    recorded = json.loads((pair_dir(world) / 'compare2/compose.json').read_text(encoding='utf-8'))
    assert pr.current_compose_deps(ctx) == pa._json_normal(recorded['deps']) and pr.compose_reasons(ctx) == []


def test_each_changed_input_has_its_own_reason(built):
    world = built
    assert pstep(world, 'P1', 'P2', 'compose')[0] == 0

    def reasons():
        ctx = pr.PairContext(pc.load_products_config(world.path), 'P1', 'P2', 'T1', print, print)
        pr.check_pair_assets(ctx)
        return pr.compose_reasons(ctx)
    assert reasons() == []
    world.doc['common']['lambda_diameters_um'] = [1, 3]                            # a product setting: 02 first (summary only)
    world.write_doc()
    code, _outs, errs = pstep(world, 'P1', 'P2', 'compose', 'T2')
    assert code == 2 and any('asset_stale' in e and 'product_settings_changed' in e for e in errs)
    for product in ('P1', 'P2'):
        assert step(world, product, '02', 'T2')[0] == 0
    assert reasons() == ['pair_settings_changed']
    code, outs, _errs = pstep(world, 'P1', 'P2', 'ledger', 'T2')
    assert code == 2                                                               # the settings are only written by compose
    assert pstep(world, 'P1', 'P2', 'compose', 'T2')[0] in (0, 3)
    assert reasons() == []
    (pair_dir(world) / 'compare2/compare2.json').write_bytes(b'{}\n')
    assert reasons() == ['compare2_json_changed', 'pair_config_changed']     # `{}` also names no config sha
    (pair_dir(world) / 'compare2/compose.json').unlink()
    assert reasons() == ['compose_missing']


# ---- acceptance 3: refusals ------------------------------------------------------------------------------------------------

def test_missing_and_stale_assets_are_named_with_product_and_reason(world):
    code, _outs, errs = pstep(world, 'P1', 'P2', 'compose')
    assert code == 2 and any('asset_missing' in e and 'P1 hotspots' in e and 'P2 hotspots' in e for e in errs)
    for product in ('P1', 'P2'):
        assert step(world, product, '01', 'T0')[0] == 0
    code, _outs, errs = pstep(world, 'P1', 'P2', 'compose')
    assert code == 2 and any('asset_missing' in e and 'P1 side' in e for e in errs)
    for product in ('P1', 'P2'):
        assert step(world, product, '02', 'T0')[0] == 0
    path = world.root('P2') / 'lateral_short-A-L1-B-L1' / 'result' / 'lateral_short.csv'
    path.write_text(path.read_text(encoding='utf-8') + '\n', encoding='utf-8')
    code, _outs, errs = pstep(world, 'P1', 'P2', 'compose')
    assert code == 2 and any('asset_stale' in e and 'P2 hotspots' in e and 'source_changed' in e for e in errs)
    assert not (pair_dir(world) / 'compare2').exists() and pair_log(world)[-1]['error_code'] == 'asset_stale'


def test_cross_group_same_product_and_unknown_pairs_are_refused(built):
    code, _outs, errs = pstep(built, 'P1', 'P3', 'compose')
    assert code == 2 and any('compare_cross_group' in e for e in errs)
    code, _outs, errs = pstep(built, 'P1', 'P1', 'compose')
    assert code == 2 and any('pair_same_product' in e for e in errs)
    code, _outs, errs = pstep(built, 'P1', 'NOPE', 'compose')
    assert code == 2 and any('pair_unknown_product' in e for e in errs)
    assert pstep(built, 'P1', 'P2', 'nope')[0] == 2
    assert pstep(built, 'P1', 'P2', 'compose', tag='../x')[0] == 2
    assert not (built.base / 'data/compare').exists()


def test_symlinked_pair_folder_is_refused(built):
    elsewhere = built.base.parent / 'elsewhere'
    elsewhere.mkdir()
    (built.base / 'data/compare').mkdir(parents=True)
    os.symlink(str(elsewhere), str(built.base / 'data/compare/P1__P2'))
    code, _outs, errs = pstep(built, 'P1', 'P2', 'compose')
    assert code == 2 and any('output_path_symlink' in e for e in errs) and not list(elsewhere.iterdir())


def test_later_steps_need_the_earlier_outputs(built):
    assert pstep(built, 'P1', 'P2', 'compose')[0] == 0
    code, _outs, errs = pstep(built, 'P1', 'P2', 'maps')
    assert code == 2 and any('pair_step_order' in e for e in errs)
    assert pstep(built, 'P1', 'P2', 'ledger')[0] == 0
    code, _outs, errs = pstep(built, 'P1', 'P2', 'report')
    assert code == 2 and any('pair_step_order' in e for e in errs)


def classic_flow(world, cards):
    """The old pair flow: 01 imports (expected cases from the other product's root), 02 compare2, ledger, ledger-maps,
    ledger-report, all from classic.* settings made of the generated pair settings."""
    base = world.base
    roots = {p: base / 'data/input' / p for p in ('P1', 'P2')}
    for product, other in (('P1', 'P2'), ('P2', 'P1')):
        rh.import_hotspots(roots[product], base / 'classic' / product, expected_case_roots=[roots[other]])
    real = yaml.safe_load((base / 'gen.pair.P1__P2.real.yaml').read_text(encoding='utf-8').split('\n', 1)[1])
    real['reference']['data_dir'], real['target']['data_dir'] = 'classic/P1', 'classic/P2'
    real['map']['input'], real['output_dir'] = real['reference'], 'classic/numeric'
    (base / 'classic.real.yaml').write_text(yaml.safe_dump(real), encoding='utf-8')
    compare2 = yaml.safe_load((base / 'gen.pair.P1__P2.compare2.yaml').read_text(encoding='utf-8').split('\n', 1)[1])
    compare2['numeric_config'], compare2['output_dir'] = 'classic.real.yaml', 'classic/compare2'
    (base / 'classic.compare2.yaml').write_text(yaml.safe_dump(compare2), encoding='utf-8')
    config = str(base / 'classic.compare2.yaml')
    assert cli.main(['compare2', '--config', config]) == 0
    assert cli.main(['ledger', '--compare2-dir', str(base / 'classic/compare2'), '--config', config,
                     '--out', str(base / 'classic/ledger')]) == 0
    extra = [] if cards else ['--no-cards']
    assert cli.main(['ledger-maps', '--compare2-dir', str(base / 'classic/compare2'), '--config', config,
                     '--ledger-dir', str(base / 'classic/ledger'), '--out', str(base / 'classic/ledger_maps')] + extra) in (0, 3)
    assert cli.main(['ledger-report', '--ledger-dir', str(base / 'classic/ledger'),
                     '--maps-dir', str(base / 'classic/ledger_maps'), '--out', str(base / 'classic/ledger_report')]) in (0, 3)


def jload(path):
    return json.loads(path.read_text(encoding='utf-8'))


SHA = re.compile(r'[0-9a-f]{64}')
LEDGER_ALLOWED = {'/inputs/compare2_json_sha256', '/inputs/config_sha256'}
MAPS_ALLOWED = {'/inputs/compare2_json_sha256', '/inputs/config_sha256', '/inputs/ledger_json_sha256',
                '/inputs/real_input_hash/reference', '/inputs/real_input_hash/target'}
CARD_PREFIXES = ('/cards', '/options/cards', '/checks/range_sum', '/checks/pair_bound', '/checks/partition',
                 '/checks/row_items')


def equivalence(world):
    """Differences of the product-mode outputs against the classic flow, per document."""
    base = world.base
    found = {}
    for new, name in (('compare2', 'compare2.json'), ('ledger', 'ledger.json'), ('ledger_maps', 'ledger_maps.json')):
        found[name] = set(json_differences(jload(pair_dir(world) / new / name), jload(base / 'classic' / new / name)))
    return found


def test_product_pair_flow_equals_the_classic_flow_with_cards_on(built):
    """E4 with `--cards`: only the paths and shas that name the other inputs differ; cards, maps and report are equal."""
    world = built
    run_all(world, 'T1', cards=True)
    classic_flow(world, cards=True)
    found = equivalence(world)
    assert found['compare2.json'] == ALLOWED_DIFFERENCES
    assert found['ledger.json'] == LEDGER_ALLOWED
    assert found['ledger_maps.json'] == MAPS_ALLOWED
    classic = world.base / 'classic'
    new_pngs = {p.name for p in (pair_dir(world) / 'ledger_maps').glob('*.png')}
    assert new_pngs and new_pngs == {p.name for p in (classic / 'ledger_maps').glob('*.png')}
    for name in sorted(new_pngs):
        assert (pair_dir(world) / 'ledger_maps' / name).read_bytes() == (classic / 'ledger_maps' / name).read_bytes(), name
    for name in ('ledger_pairs.csv', 'ledger_electrodes.csv'):
        assert (pair_dir(world) / 'ledger' / name).read_bytes() == (classic / 'ledger' / name).read_bytes(), name
    assert jload(pair_dir(world) / 'ledger_maps/ledger_maps.json')['cards']                    # the cards exist
    html = lambda folder: SHA.sub('SHA', (folder / 'ledger_report/ledger_report.html').read_text(encoding='utf-8'))
    assert html(pair_dir(world)) == html(classic)                                              # the sha display cells only


def test_product_pair_flow_without_cards_differs_only_in_the_card_fields(built):
    """E4 with the product default (cards off) against the classic flow with cards on: the allowed list plus the cards."""
    world = built
    run_all(world, 'T1')
    classic_flow(world, cards=True)
    found = equivalence(world)
    assert found['compare2.json'] == ALLOWED_DIFFERENCES and found['ledger.json'] == LEDGER_ALLOWED
    extra = found['ledger_maps.json'] - MAPS_ALLOWED
    assert extra and all(any(item.startswith(prefix) for prefix in CARD_PREFIXES) for item in extra), sorted(extra)
    assert MAPS_ALLOWED <= found['ledger_maps.json']
    classic = world.base / 'classic'
    for png in sorted((pair_dir(world) / 'ledger_maps').glob('*.png')):
        assert png.read_bytes() == (classic / 'ledger_maps' / png.name).read_bytes(), png.name
    assert (pair_dir(world) / 'ledger_report/ledger_report.html').read_text(encoding='utf-8')
    assert (pair_dir(world) / 'ledger_report/execution.json').is_file()


def test_08_in_product_mode_runs_to_the_end_without_03_outputs(built):
    """R3:7: no asset_manifest / electrode_import_record exists and nothing asks for one."""
    world = built
    run_all(world, 'T1')
    names = {p.name for p in world.base.rglob('*')}
    assert 'asset_manifest.json' not in names and 'electrode_import_record.json' not in names
    real = yaml.safe_load((world.base / 'gen.pair.P1__P2.real.yaml').read_text(encoding='utf-8').split('\n', 1)[1])
    for role in ('reference', 'target'):
        assert 'asset_manifest' not in real[role] and 'electrode_import_record' not in real[role]
    maps = jload(pair_dir(world) / 'ledger_maps/ledger_maps.json')
    ledger = jload(pair_dir(world) / 'ledger/ledger.json')
    report = jload(pair_dir(world) / 'ledger_report/execution.json')
    assert maps['status'] == 'ok' and ledger['status'] in ('ok', 'partial') and report['exit_code'] in (0, 3)
    # without DL1 (report.dl1_root is not set here) the maps have no electrode pictures; nothing else is missing
    assert maps['sides']['reference']['electrodes'] == [] and maps['sides']['reference']['items']


def test_rerunning_a_step_moves_the_old_folder_to_runs(built):
    run_all(built, 'T1')
    code, outs, _errs = pstep(built, 'P1', 'P2', 'ledger', 'T2')
    assert code == 0 and outs[0].startswith('archived data/compare/P1__P2/ledger to data/_runs/T2/compare/P1__P2/ledger')
    assert (built.base / 'data/_runs/T2/compare/P1__P2/ledger/ledger.json').is_file()
    code, outs, _errs = pstep(built, 'P1', 'P2', 'ledger', 'T2')                   # the same tag again: a free name
    assert code == 0 and (built.base / 'data/_runs/T2/compare/P1__P2/ledger-2/ledger.json').is_file()


# ---- T12b (R12:1-6) -------------------------------------------------------------------------------------------------------

def test_a_pair_config_change_makes_compose_build_again_and_later_steps_work(built):
    """R12:1: a products.yaml change that only changes the generated pair settings used to leave a fresh compose whose
    compare2.json named the old config sha; the ledger then refused it with compare2_config_mismatch (a dead end)."""
    world = built
    run_all(world, 'T1')
    world.doc['common']['report'] = {'diameter_um': 3, 'map_window_um': 40}
    world.write_doc()
    for product in ('P1', 'P2'):
        assert step(world, product, '02', 'T2')[0] == 0
    yaml_before = (world.base / 'gen.pair.P1__P2.compare2.yaml').read_bytes()
    ctx = pr.PairContext(pc.load_products_config(world.path), 'P1', 'P2', 'T2', print, print)
    pr.check_pair_assets(ctx)
    assert pr.compose_reasons(ctx) == ['pair_settings_changed']                   # before compose writes the settings
    code, outs, errs = pstep(world, 'P1', 'P2', 'compose', 'T2')
    assert code in (0, 3) and outs[-1].startswith('built compose') and not errs, (outs, errs)
    assert (world.base / 'gen.pair.P1__P2.compare2.yaml').read_bytes() != yaml_before
    assert pr.compose_reasons(ctx) == []
    for name in ('ledger', 'maps', 'report'):
        assert pstep(world, 'P1', 'P2', name, 'T2')[0] in (0, 3), name
    assert pstep(world, 'P1', 'P2', 'compose', 'T3')[1] == ['skip fresh compose P1__P2']


def test_compose_reasons_names_pair_config_changed_when_the_yaml_bytes_differ_from_compare2_json(built):
    """R12:1 with the settings file edited after compose (the file equals what compose would write, compare2.json differs)."""
    world = built
    assert pstep(world, 'P1', 'P2', 'compose')[0] == 0
    path = pair_dir(world) / 'compare2/compare2.json'
    doc = json.loads(path.read_text(encoding='utf-8'))
    doc['inputs']['compare2_config']['sha256'] = '0' * 64
    text = json.dumps(doc)
    path.write_text(text, encoding='utf-8')
    compose = pair_dir(world) / 'compare2/compose.json'
    recorded = json.loads(compose.read_text(encoding='utf-8'))
    recorded['compare2_json_sha256'] = pa.sha256_file(path)                         # only the config sha is wrong
    compose.write_text(json.dumps(recorded), encoding='utf-8')
    ctx = pr.PairContext(pc.load_products_config(world.path), 'P1', 'P2', 'T1', print, print)
    pr.check_pair_assets(ctx)
    assert pr.compose_reasons(ctx) == ['pair_config_changed']
    code, outs, _errs = pstep(world, 'P1', 'P2', 'ledger', 'T2')
    assert code == 2 and _errs and any('pair_compose_stale' in e and 'pair_config_changed' in e for e in _errs)


def test_compose_force_builds_a_fresh_compose_again_after_archiving(built, capsys):
    assert pstep(built, 'P1', 'P2', 'compose')[0] == 0
    code, outs, _errs = pstep(built, 'P1', 'P2', 'compose', 'T2')
    assert outs == ['skip fresh compose P1__P2']
    argv = ['pair-step', '--config', str(built.path), '--pair', 'P1', 'P2', '--step', 'compose', '--run-tag', 'F1']
    assert cli.main(argv) == 0 and 'skip fresh compose' in capsys.readouterr().out
    assert cli.main(argv + ['--force']) == 0
    out = capsys.readouterr().out
    assert 'archived data/compare/P1__P2/compare2' in out and 'built compose P1__P2' in out
    assert (built.base / 'data/_runs/F1/compare/P1__P2/compare2/compose.json').is_file()
    assert (pair_dir(built) / 'compare2/compose.json').is_file()
    assert pair_log(built)[-1]['reasons'] == ['force']
    with pytest.raises(SystemExit) as exc:
        cli.main(['pair-step', '--help'])
    assert exc.value.code == 0 and '--force' in capsys.readouterr().out


def test_an_order_refusal_archives_nothing(built):
    """R12:2: maps / report refused for a missing earlier output must leave the old maps and report folders in place."""
    world = built
    run_all(world, 'T1')
    ledger_json = pair_dir(world) / 'ledger/ledger.json'
    ledger_json.rename(ledger_json.with_name('ledger.json.moved'))
    code, _outs, errs = pstep(world, 'P1', 'P2', 'maps', 'T2')
    assert code == 2 and any('pair_step_order' in e for e in errs)
    code, _outs, errs = pstep(world, 'P1', 'P2', 'report', 'T2')
    assert code == 2 and any('pair_step_order' in e for e in errs)
    assert (pair_dir(world) / 'ledger_maps/ledger_maps.json').is_file() and (pair_dir(world) / 'ledger_report').is_dir()
    assert not (world.base / 'data/_runs/T2').exists()
    ledger_json.with_name('ledger.json.moved').rename(ledger_json)
    maps_json = pair_dir(world) / 'ledger_maps/ledger_maps.json'
    maps_json.rename(maps_json.with_name('x.moved'))
    code, _outs, errs = pstep(world, 'P1', 'P2', 'report', 'T2')
    assert code == 2 and any('pair_step_order' in e and 'maps' in e for e in errs)
    assert (pair_dir(world) / 'ledger_report/ledger_report.html').is_file() and not (world.base / 'data/_runs/T2').exists()


def test_the_archive_move_is_a_rename_that_retries_a_permission_error(built, monkeypatch):
    """R12:3: no shutil.move (no copy fallback): os.rename, PermissionError retried, nothing nested in the target."""
    run_all(built, 'T1')
    monkeypatch.setattr(pa, 'RETRY_WAIT_S', 0)
    real_rename, left = os.rename, {'n': 2}
    seen = []

    def flaky(src, dst):
        seen.append((str(src), str(dst)))
        if left['n'] > 0:
            left['n'] -= 1
            raise PermissionError('held open')
        return real_rename(src, dst)
    monkeypatch.setattr(pr.os, 'rename', flaky)
    code, outs, _errs = pstep(built, 'P1', 'P2', 'ledger', 'T2')
    assert code == 0 and left['n'] == 0 and len(seen) == 3 and outs[0].startswith('archived data/compare/P1__P2/ledger')
    target = built.base / 'data/_runs/T2/compare/P1__P2/ledger'
    assert (target / 'ledger.json').is_file() and not (target / 'ledger').exists()
    left['n'] = 99                                                                   # never works: the error, nothing moved
    code, _outs, errs = pstep(built, 'P1', 'P2', 'ledger', 'T3')
    assert code == 2 and any('pair_internal_error' in e and 'PermissionError' in e for e in errs)
    assert (pair_dir(built) / 'ledger/ledger.json').is_file()


def test_products_runs_move_is_a_rename_that_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, 'RETRY_WAIT_S', 0)
    source = tmp_path / 'g0001'
    source.mkdir()
    (source / 'f.txt').write_text('x', encoding='utf-8')
    real_rename, left = os.rename, {'n': 2}

    def flaky(src, dst):
        if left['n'] > 0:
            left['n'] -= 1
            raise PermissionError('held open')
        return real_rename(src, dst)
    monkeypatch.setattr(pa.os, 'rename', flaky)
    target = pa._move_to_runs(tmp_path, source, 'T1', 'P1', 'hotspots')
    assert left['n'] == 0 and (target / 'f.txt').is_file() and not (target / 'g0001').exists() and not source.exists()


@pytest.mark.parametrize('name', ['pair_log.jsonl', 'compare2', 'ledger', 'ledger_maps', 'ledger_report'])
def test_a_symlinked_log_or_step_folder_is_refused(built, name):
    """R12:4"""
    elsewhere = built.base.parent / 'elsewhere'
    elsewhere.mkdir()
    folder = pair_dir(built)
    folder.mkdir(parents=True)
    os.symlink(str(elsewhere), str(folder / name))
    code, _outs, errs = pstep(built, 'P1', 'P2', 'compose')
    assert code == 2 and any('output_path_symlink' in e for e in errs) and not list(elsewhere.iterdir())


def test_a_fresh_compose_with_a_partial_status_is_skipped_with_a_notice(built):
    """R12:5"""
    world = built
    assert pstep(world, 'P1', 'P2', 'compose')[0] == 0
    execution = pair_dir(world) / 'compare2/execution.json'
    doc = json.loads(execution.read_text(encoding='utf-8'))
    doc['status'] = 'partial'
    execution.write_text(json.dumps(doc), encoding='utf-8')
    code, outs, _errs = pstep(world, 'P1', 'P2', 'compose', 'T2')
    assert code == 0 and outs == ['skip fresh compose P1__P2 (compare2 status: partial)']
    last = pair_log(world)[-1]
    assert last['action'] == 'skip' and last['inputs']['compare2_status'] == 'partial'
    code, outs, _errs = pstep(world, 'P1', 'P2', 'compose', 'T3')
    assert outs == ['skip fresh compose P1__P2 (compare2 status: partial)']


def _fresh_ctx(world):
    ctx = pr.PairContext(pc.load_products_config(world.path), 'P1', 'P2', 'T1', print, print)
    pr.check_pair_assets(ctx)
    return ctx


def test_a_compare2_json_of_other_code_makes_compose_build_again(built, monkeypatch):
    """R12b:2: ledger_basis / stale_reason of compare2.json, the same checks the ledger makes."""
    assert pstep(built, 'P1', 'P2', 'compose')[0] == 0
    assert pr.compose_reasons(_fresh_ctx(built)) == []
    monkeypatch.setattr(pr.compare2_mod, 'LEDGER_BASIS', 'pair_share_v999')
    assert pr.compose_reasons(_fresh_ctx(built)) == ['compare2_code_stale']
    code, _outs, errs = pstep(built, 'P1', 'P2', 'ledger', 'T2')
    assert code == 2 and any('pair_compose_stale' in e and 'compare2_code_stale' in e and '--force' in e for e in errs)
    code, outs, errs = pstep(built, 'P1', 'P2', 'compose', 'T3')
    assert code == 0 and outs[0].startswith('archived data/compare/P1__P2/compare2') and outs[-1].startswith('built compose'), errs
    assert pair_log(built)[-1]['reasons'] == ['compare2_code_stale']       # (the side module still writes the real basis)
    monkeypatch.undo()                                                       # the rebuilt file names the patched basis
    assert pr.compose_reasons(_fresh_ctx(built)) == ['compare2_code_stale']
    assert pstep(built, 'P1', 'P2', 'compose', 'T4')[0] == 0
    assert pr.compose_reasons(_fresh_ctx(built)) == []
    # an area_basis of other code is stale too (compare2.stale_reason)
    monkeypatch.setattr(pr.compare2_mod, 'AREA_BASIS', 'area_v999')
    assert pr.compose_reasons(_fresh_ctx(built)) == ['compare2_code_stale']


def test_force_with_a_step_other_than_compose_is_refused(built, capsys):
    """R12b:3"""
    assert pstep(built, 'P1', 'P2', 'compose')[0] == 0
    for name in ('ledger', 'maps', 'report'):
        argv = ['pair-step', '--config', str(built.path), '--pair', 'P1', 'P2', '--step', name, '--run-tag', 'F1', '--force']
        assert cli.main(argv) == 2
        assert 'pair_force_compose_only' in capsys.readouterr().err
    assert not (built.base / 'data/_runs/F1').exists() and not (pair_dir(built) / 'ledger').exists()
    assert pr.run_pair_step(built.path, 'P1', 'P2', 'ledger', run_tag='F2', force=True, err=lambda t: None) == 2


def test_a_symlinked_runs_folder_is_refused_before_anything_is_archived(built):
    """R12b:4: pair archive"""
    assert pstep(built, 'P1', 'P2', 'compose')[0] == 0
    elsewhere = built.base.parent / 'elsewhere'
    elsewhere.mkdir()
    runs = built.base / 'data/_runs'
    runs.mkdir(parents=True, exist_ok=True)
    os.symlink(str(elsewhere), str(runs / 'T9'))
    errs = []
    code = pr.run_pair_step(built.path, 'P1', 'P2', 'compose', run_tag='T9', force=True, out=lambda t: None, err=errs.append)
    assert code == 2 and any('output_path_symlink' in e for e in errs)
    assert (pair_dir(built) / 'compare2/compose.json').is_file() and not list(elsewhere.iterdir())


def test_a_symlinked_runs_folder_is_refused_for_products_archive(tmp_path):
    """R12b:4: products _move_to_runs"""
    source = tmp_path / 'g0001'
    source.mkdir()
    elsewhere = tmp_path.parent / ('elsewhere_%s' % tmp_path.name)
    elsewhere.mkdir()
    (tmp_path / 'data').mkdir()
    os.symlink(str(elsewhere), str(tmp_path / 'data/_runs'))
    with pytest.raises(NumericContractError) as exc:
        pa._move_to_runs(tmp_path, source, 'T1', 'P1', 'hotspots')
    assert exc.value.code == 'output_path_symlink'
    assert source.is_dir() and not list(elsewhere.iterdir())


def test_report_with_and_without_cards_differs_only_in_the_card_area(built):
    """R12:6: the report of a cards-on and a cards-off maps run, shas masked."""
    world = built
    run_all(world, 'T1', cards=True)
    html_on = (pair_dir(world) / 'ledger_report/ledger_report.html').read_text(encoding='utf-8')
    assert pstep(world, 'P1', 'P2', 'maps', 'T2', cards=False)[0] in (0, 3)
    assert pstep(world, 'P1', 'P2', 'report', 'T2')[0] in (0, 3)
    html_off = (pair_dir(world) / 'ledger_report/ledger_report.html').read_text(encoding='utf-8')
    on, off = SHA.sub('SHA', html_on), SHA.sub('SHA', html_off)
    marker = '"options": {"coord_min_overlap"'
    assert marker in on and '"options": {"cards": false, "coord_min_overlap"' in off
    # the one difference (measured): the embedded maps options record names `"cards": false`; nothing else moves
    assert on == off.replace('"options": {"cards": false, "coord_min_overlap"', marker, 1)
    for needle in ('예산 부족', 'budget', 'Budget'):
        assert needle not in off or needle in on, needle
