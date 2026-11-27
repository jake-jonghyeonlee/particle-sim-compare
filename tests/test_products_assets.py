"""pasx/T5: generation store, staleness verdicts, products-step 01 / 02, interruption injection, open_generation."""
import copy
import json
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml
from PIL import Image

import test_compare2 as tc
from simcompare import cli
from simcompare import compare2_side as cs
from simcompare import products_assets as pa
from simcompare import products_config as pc
from simcompare.adapters import real_hotspot as rh
from simcompare.numeric_schema import NumericContractError

EXTRA_CASE = ('lateral_short', 'K', 'L1', 'M', 'L1', {1: {0, 1}, 2: {0}})      # only in P4; pair KM is in pairs.csv
PAIRS = tc.PAIRS + [('KM', 'K', 'M', 'line')]


# ---- fixtures ---------------------------------------------------------------------------------------------------

def write_source(root, cases):
    """The case folders of tc.write_side (electrode PNGs, hotspot PNGs, csv), without importing."""
    for defect, ea, la, eb, lb, masks in cases:
        folder = root / ('%s-%s-%s-%s-%s' % (defect, ea, la, eb, lb))
        (folder / 'result').mkdir(parents=True)
        for electrode in ('1.png', '2.png'):
            Image.new('L', (1004, 1004), 0).save(str(folder / electrode))
        rows = ['defect_size(um),Area(um2)']
        for diameter in tc.DIAMETERS_UM if hasattr(tc, 'DIAMETERS_UM') else (1, 2, 3):
            cells = masks.get(diameter, ())
            array = np.zeros((4, 4), dtype=np.uint8)
            for cell in cells:
                array[cell // 4, cell % 4] = 255
            Image.fromarray(array).save(str(folder / ('hotspot_%s%02d.png' % (defect, diameter))))
            rows.append('%d,%.2f' % (diameter, len(cells) * tc.CELL))
        (folder / 'result' / ('%s.csv' % defect)).write_text('\n'.join(rows) + '\n', encoding='utf-8')


class World(object):
    def __init__(self, base, path, doc):
        self.base, self.path, self.doc = base, path, doc

    def write_doc(self):
        self.path.write_text(yaml.safe_dump(self.doc, sort_keys=False), encoding='utf-8')

    def root(self, product):
        return self.base / self.doc['products'][product]['hotspot_root']

    def products_dir(self, product):
        return self.base / 'data/products' / product


def make_world(tmp_path):
    """Group G = {P1, P2} (P2 lacks the IJ case and has other AB masks), group H = {P3}; P4 sits ready (own folder)."""
    base = tmp_path / 'base'
    cases = {'P1': tc.base_cases(), 'P2': tc.base_cases(AB={1: {0, 1, 5}, 2: {0, 1}, 3: {0}}, IJ=None),
             'P3': tc.base_cases(), 'P4': tc.base_cases(IJ=None) + [EXTRA_CASE]}
    for product, case_list in cases.items():
        write_source(base / 'data/input' / product, case_list)
    tc.write_density(base / 'data/input/density', 'mobile', tc.DENS_LINE, tc.DENS_PIXEL)
    (base / 'data/input/pairs.csv').write_text(
        'pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\n'
        + ''.join('%s,%s,%s,%s,v1\n' % row for row in PAIRS), encoding='utf-8')
    stack = tc.assignment()['layer_stack']

    def product(name):
        return {'hotspot_root': 'data/input/%s' % name, 'model_group': 'mobile', 'resolution': list(tc.RES),
                'condition_assignment': {'layer_stack': copy.deepcopy(stack), 'pair_table_path': 'data/input/pairs.csv'}}
    doc = {'schema_version': 'simcompare_products_v1',
           'common': {'density_dir': 'data/input/density',
                      'model_groups': {'mobile': {'glass_area': {'value': 50.0, 'unit': 'um2'},
                                                  'weight_line': 1.0, 'weight_pixel': 1.0}}},
           'groups': {'G': ['P1', 'P2'], 'H': ['P3']},
           'products': {'P1': product('P1'), 'P2': product('P2'), 'P3': product('P3')}}
    world = World(base, base / 'products.yaml', doc)
    world.write_doc()
    return world


def add_p4(world):
    world.doc['products']['P4'] = {'hotspot_root': 'data/input/P4', 'model_group': 'mobile', 'resolution': list(tc.RES),
                                   'condition_assignment': copy.deepcopy(world.doc['products']['P1']['condition_assignment'])}
    world.doc['groups']['G'] = ['P1', 'P2', 'P4']
    world.write_doc()


def step(world, product, number, tag='T1', force=False):
    outs, errs = [], []
    code = pa.run_products_step(world.path, product, number, force=force, run_tag=tag, out=outs.append, err=errs.append)
    return code, outs, errs


def build_all(world, products=('P1', 'P2', 'P3'), tag='T0'):
    for product in products:
        assert step(world, product, '01', tag)[0] == 0
    for product in products:
        assert step(world, product, '02', tag)[0] == 0


@pytest.fixture
def world(tmp_path):
    return make_world(tmp_path)


@pytest.fixture
def built(world):
    build_all(world)
    return world


def status(world, product):
    return pa.check_assets(world.base, product, world.path)


def states(world, product):
    verdicts = status(world, product)
    return {key: verdict.as_tuple() for key, verdict in verdicts.items()}


def log_lines(world, product):
    path = world.products_dir(product) / 'asset_log.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def bump_mtime(path, ns=7_000_000_000):
    stat = os.stat(str(path))
    os.utime(str(path), ns=(stat.st_atime_ns, stat.st_mtime_ns + ns))


def rewrite_hotspot(world, product, case, diameter, cells):
    path = world.root(product) / case / ('hotspot_lateral_short%02d.png' % diameter)
    array = np.zeros((4, 4), dtype=np.uint8)
    for cell in cells:
        array[cell // 4, cell % 4] = 255
    Image.fromarray(array).save(str(path))
    return path


AB = 'lateral_short-A-L1-B-L1'


def write_settings(world, product):
    """The writer side of the generated settings (check_assets only reads them)."""
    config = pc.load_products_config(world.path)
    pa.ensure_product_settings(config, product, pa.open_generation(world.base, product, 'hotspots'))


# ---- acceptance 1: generations, pointer, complete.json, skip fresh ---------------------------------------------------

def test_01_then_02_build_assets_and_a_second_run_skips_everything(world):
    for product in ('P1', 'P2'):
        code, outs, _errs = step(world, product, '01')
        assert code == 0 and outs[-1].startswith('built 01 %s g0001-' % product)
    for product in ('P1', 'P2'):
        code, outs, _errs = step(world, product, '02')
        assert code == 0 and [o for o in outs if o.startswith('built 02')] == ['built 02 side %s g0001-%s' % (
            product, pa.open_generation(world.base, product, 'side').name.split('-')[1]), 'built 02 summary %s' % product]
    for product in ('P1', 'P2'):
        folder = world.products_dir(product)
        for kind in ('hotspots', 'side'):
            pointer = json.loads((folder / kind / 'CURRENT.json').read_text(encoding='utf-8'))
            generation = pa.open_generation(world.base, product, kind)
            assert pointer['generation'] == generation.name and pa.GEN_RE.fullmatch(generation.name)
            assert generation.complete['schema_version'] == 'asset_generation_v1'
            assert pointer['complete_sha256'] == pa.sha256_file(generation.path / 'complete.json')
        assert (folder / 'summary.json').is_file() and (folder / 'asset_log.jsonl').is_file()
        hot = pa.open_generation(world.base, product, 'hotspots')
        assert {e['path'] for e in hot.complete['files']} == {'manifest.json', 'expected_cases.txt', 'source_inventory.json'}
        inventory = json.loads((hot.path / 'source_inventory.json').read_text(encoding='utf-8'))
        assert inventory['folders'] and inventory['files'] and all(len(f['sha256']) == 64 for f in inventory['files'])
        assert hot.complete['verification'] == {'masks': 'full', 'manifest_sha256': hot.file_sha256('manifest.json')}
        assert hot.complete['masks'] and all(set(m) == {'path', 'size', 'mtime_ns'} for m in hot.complete['masks'])
        side = pa.open_generation(world.base, product, 'side')
        maps_src = side.complete['maps_src']                # RECORD_MAPS default: the 02 side is recorded (pasx/T8)
        assert maps_src['status'] == 'complete' and maps_src['reason'] is None and maps_src['calls'] > 0
        assert maps_src['raw_bytes'] > 0 and maps_src['stored_bytes'] > 0     # (a tiny array grows by the zlib header)
        assert maps_src['conditions']['maps_basis'] == 'maps_src_v1'
        assert {e['path'] for e in side.complete['files']} == {'side.json', 'maps_src/index.json'}
        assert side.file_sha256('maps_src/index.json') == pa.sha256_file(side.path / 'maps_src' / 'index.json')
        assert side.complete['depends'] == [{'kind': 'hotspots', 'generation': hot.name, 'complete_sha256': hot.complete_sha256}]
        assert len(side.complete['code_sha256']) == 64 and side.complete['elapsed_s'] >= 0
        assert {state for state, _r in states(world, product).values()} == {pa.OK}
    for product in ('P1', 'P2'):                     # second run: every step skips
        _, outs, _ = step(world, product, '01')
        assert outs == ['skip fresh 01 %s' % product]
        _, outs, _ = step(world, product, '02')
        assert outs == ['skip fresh 02 side %s' % product, 'skip fresh 02 summary %s' % product]
    assert not list((world.products_dir('P1')).rglob('g0002*'))
    log = log_lines(world, 'P1')
    assert [(l['step'], l['action'], l.get('part')) for l in log] == [
        ('01', 'build', None), ('02', 'build', 'side'), ('02', 'build', 'summary'), ('01', 'skip', None),
        ('02', 'skip', 'side'), ('02', 'skip', 'summary')]
    assert all({'at', 'run_tag', 'step', 'action', 'status', 'reasons', 'generation', 'elapsed_s', 'exit_code',
                'error_code'} <= set(l) for l in log)


def test_force_rebuilds_a_fresh_step_and_02_runs_without_03_outputs(built):
    world = built
    first = pa.open_generation(world.base, 'P1', 'hotspots').name
    code, outs, _ = step(world, 'P1', '01', force=True)
    assert code == 0 and outs[-1].startswith('built 01 P1 g0002-') and pa.open_generation(world.base, 'P1', 'hotspots').name != first
    # the side measured the old generation: it is stale now and 02 rebuilds it and the summary
    assert states(world, 'P1')['side'][1] == ['hotspots_generation_changed']
    code, outs, _ = step(world, 'P1', '02')
    assert code == 0 and [o.split(' ')[0:3] for o in outs if o.startswith('built')] == [['built', '02', 'side'], ['built', '02', 'summary']]
    # no step 03 output exists and 02 never asked for one (R3:7)
    names = {p.name for p in world.base.rglob('*')}
    assert 'asset_manifest.json' not in names and 'electrode_import_record.json' not in names
    real = yaml.safe_load((world.base / 'gen.product.P1.real.yaml').read_text(encoding='utf-8').split('\n', 1)[1])
    assert 'asset_manifest' not in real['reference'] and 'electrode_import_record' not in real['reference']
    assert real['reference']['data_dir'] == pa.open_generation(world.base, 'P1', 'hotspots').relative_path


def test_02_without_01_is_asset_missing_and_group_errors_exit_2(world):
    code, outs, errs = step(world, 'P1', '02')
    assert code == 2 and any('asset_missing' in e for e in errs)
    (world.root('P1') / 'lateral_short-Q-L1-R-L1').mkdir()      # a union case without a pair: group refused
    (world.root('P1') / 'lateral_short-Q-L1-R-L1' / '1.png').write_bytes(b'')
    code, outs, errs = step(world, 'P1', '01')
    assert code == 2 and any('group_case_pair_unmapped' in e for e in errs)
    assert not (world.products_dir('P1') / 'hotspots').exists()


def test_bad_arguments_exit_2(world):
    assert step(world, 'NOPE', '01')[0] == 2
    assert step(world, 'P1', '03')[0] == 2
    code, _outs, errs = step(world, 'P1', '01', tag='../x')
    assert code == 2 and any('invalid_run_tag' in e for e in errs)
    assert not (world.base / 'data/products/P1').exists()


def test_bad_run_tag_is_refused(world):
    with pytest.raises(NumericContractError):
        pa.check_run_tag('../x')
    with pytest.raises(NumericContractError):
        pa.check_run_tag('')


def test_symlinked_product_folder_is_refused(world):
    target = world.base / 'elsewhere'
    target.mkdir()
    (world.base / 'data/products').mkdir(parents=True)
    os.symlink(str(target), str(world.base / 'data/products/P1'))
    code, _outs, errs = step(world, 'P1', '01')
    assert code == 2 and any('output_path_symlink' in e for e in errs) and not list(target.iterdir())


def test_symlinked_runs_folder_is_refused(world):
    elsewhere = world.base.parent / 'elsewhere'
    elsewhere.mkdir()
    (world.base / 'data').mkdir(exist_ok=True)
    os.symlink(str(elsewhere), str(world.base / 'data/_runs'))
    code, _outs, errs = step(world, 'P1', '01')
    assert code == 2 and any('output_path_symlink' in e for e in errs) and not list(elsewhere.iterdir())


def test_cli_products_step(world, capsys):
    argv = ['products-step', '--config', str(world.path), '--product', 'P3', '--step', '01', '--run-tag', 'CLI']
    assert cli.main(argv) == 0
    assert 'built 01 P3' in capsys.readouterr().out
    assert cli.main(argv) == 0
    assert capsys.readouterr().out.strip() == 'skip fresh 01 P3'
    assert cli.main(argv + ['--force']) == 0 and 'built 01 P3 g0002-' in capsys.readouterr().out


# ---- acceptance 3: staleness counterexamples ---------------------------------------------------------------------------

def test_changed_source_png_makes_01_stale_and_01_rebuilds(built):
    world = built
    old = pa.open_generation(world.base, 'P1', 'hotspots').name
    rewrite_hotspot(world, 'P1', AB, 1, {5, 6, 7})
    assert states(world, 'P1')['hotspots'] == (pa.STALE, ['source_changed'])
    assert states(world, 'P2')['hotspots'] == (pa.OK, [])
    code, outs, _ = step(world, 'P1', '01', 'T2')
    assert code == 0 and outs[-1].startswith('built 01 P1 g0002-')
    assert pa.open_generation(world.base, 'P1', 'hotspots').name != old
    assert states(world, 'P1')['hotspots'] == (pa.OK, [])
    assert log_lines(world, 'P1')[-1]['reasons'] == ['source_changed']


def test_mtime_only_change_is_fresh_with_a_warning(built):
    world = built
    bump_mtime(world.root('P1') / AB / 'result' / 'lateral_short.csv')
    verdict = status(world, 'P1')['hotspots']
    assert verdict.as_tuple() == (pa.OK, []) and verdict.warnings == ['source_stat_changed_content_same']
    code, outs, errs = step(world, 'P1', '01', 'T2')
    assert code == 0 and outs == ['skip fresh 01 P1'] and any('source_stat_changed_content_same' in e for e in errs)


def test_added_empty_folder_and_added_or_removed_file_make_01_stale(built):
    world = built
    (world.root('P1') / 'EMPTY_FOLDER').mkdir()
    assert states(world, 'P1')['hotspots'] == (pa.STALE, ['source_changed'])
    (world.root('P1') / 'EMPTY_FOLDER').rmdir()
    assert states(world, 'P1')['hotspots'][0] == pa.OK
    (world.root('P1') / 'note.txt').write_text('x', encoding='utf-8')
    assert states(world, 'P1')['hotspots'] == (pa.STALE, ['source_changed'])
    (world.root('P1') / 'note.txt').unlink()
    (world.root('P1') / AB / '2.png').unlink()
    assert states(world, 'P1')['hotspots'] == (pa.STALE, ['source_changed'])


def test_adding_a_product_to_a_group_stales_only_that_group(built):
    world = built
    add_p4(world)
    assert states(world, 'P1')['hotspots'] == (pa.STALE, ['group_union_changed'])
    assert states(world, 'P2')['hotspots'] == (pa.STALE, ['group_union_changed'])
    assert states(world, 'P3')['hotspots'] == (pa.OK, [])
    assert step(world, 'P3', '01')[1] == ['skip fresh 01 P3']
    assert step(world, 'P1', '01', 'T2')[0] == 0
    assert states(world, 'P1')['hotspots'] == (pa.OK, [])
    union = (world.base / 'data/products/_groups/G/expected_cases.txt').read_text(encoding='utf-8')
    assert 'lateral_short-K-L1-M-L1' in union


def test_import_option_and_root_changes_have_their_own_reasons(built):
    world = built
    world.doc['products']['P3']['include_cno'] = True
    world.write_doc()
    assert states(world, 'P3')['hotspots'] == (pa.STALE, ['import_options_changed'])
    shutil.copytree(str(world.root('P3')), str(world.base / 'data/input/P3b'))
    world.doc['products']['P3']['hotspot_root'] = 'data/input/P3b'
    world.write_doc()
    assert states(world, 'P3')['hotspots'] == (pa.STALE, ['hotspot_root_changed', 'import_options_changed'])


def test_density_change_makes_only_the_summary_stale(built):
    world = built
    density = world.base / 'data/input/density/mobile_line.csv'
    density.write_text(density.read_text(encoding='utf-8').replace('5.0', '5.5'), encoding='utf-8')
    found = states(world, 'P1')
    assert found['hotspots'][0] == pa.OK and found['side'][0] == pa.OK and found['summary'] == (pa.STALE, ['density_changed'])
    side_before = pa.open_generation(world.base, 'P1', 'side').name
    before = (world.products_dir('P1') / 'summary.json').read_bytes()
    code, outs, _ = step(world, 'P1', '02', 'T2')
    assert code == 0 and outs == ['skip fresh 02 side P1', 'built 02 summary P1']
    assert pa.open_generation(world.base, 'P1', 'side').name == side_before
    assert (world.products_dir('P1') / 'summary.json').read_bytes() != before
    assert states(world, 'P1')['summary'] == (pa.OK, [])


def test_pair_table_change_makes_the_side_stale(built):
    world = built
    pairs = world.base / 'data/input/pairs.csv'
    pairs.write_text(pairs.read_text(encoding='utf-8') + 'ZZ,Y,Z,line,v1\n', encoding='utf-8')
    found = states(world, 'P1')
    assert found['hotspots'][0] == pa.OK and found['side'] == (pa.STALE, ['pair_table_changed'])
    code, outs, _ = step(world, 'P1', '02', 'T2')
    assert code == 0 and [o for o in outs if o.startswith('built')] == ['built 02 side P1 ' + pa.open_generation(
        world.base, 'P1', 'side').name, 'built 02 summary P1']
    assert states(world, 'P1')['side'] == (pa.OK, [])


def test_product_block_diameter_and_model_group_changes_move_the_summary(built):
    world = built
    world.doc['products']['P3']['resolution'] = [40, 10]
    world.write_doc()
    # the generated settings changed and the read-only check does not write them (R5:1): side and summary read 낡음
    found = states(world, 'P3')
    assert found['side'] == (pa.STALE, ['product_settings_changed']) and found['summary'][0] == pa.STALE
    code, outs, _errs = step(world, 'P3', '02', 'T2')          # 02 writes the settings; the side itself is still fresh
    assert code == 0 and outs == ['skip fresh 02 side P3', 'built 02 summary P3']
    assert states(world, 'P3')['summary'] == (pa.OK, [])
    world.doc['common']['lambda_diameters_um'] = [1, 3]
    world.write_doc()
    assert states(world, 'P3')['summary'][0] == pa.STALE
    write_settings(world, 'P3')                         # the writer (products-step does it) makes the reason visible
    assert states(world, 'P3')['summary'] == (pa.STALE, ['diameter_subset_changed'])
    code, _outs, _errs = step(world, 'P3', '02', 'T3')
    assert code == 0 and states(world, 'P3')['summary'] == (pa.OK, [])
    world.doc['common']['model_groups']['mobile']['weight_line'] = 2.0
    world.write_doc()
    write_settings(world, 'P3')
    assert 'model_group_changed' in states(world, 'P3')['summary'][1]


def test_asset_status_is_the_worst_state_and_missing_assets_are_reported(world):
    assert pa.asset_status(world.base, 'P1', world.path) == (pa.MISSING, ['asset_missing', 'hotspots_unavailable'])
    assert step(world, 'P1', '01')[0] == 0
    assert pa.asset_status(world.base, 'P1', world.path) == (pa.MISSING, ['asset_missing', 'side_unavailable'])
    assert step(world, 'P1', '02')[0] == 0
    assert pa.asset_status(world.base, 'P1', world.path) == (pa.OK, [])
    rewrite_hotspot(world, 'P1', AB, 1, {5, 6, 7})
    assert pa.asset_status(world.base, 'P1', world.path) == (pa.STALE, ['source_changed'])
    side = pa.open_generation(world.base, 'P1', 'side')
    with open(str(side.path / 'side.json'), 'ab') as handle:
        handle.write(b' ')
    state, reasons = pa.asset_status(world.base, 'P1', world.path)
    assert state == pa.CORRUPT and 'asset_corrupt:file_size_mismatch' in reasons


def test_asset_status_reads_no_mask(built, monkeypatch):
    calls = []
    monkeypatch.setattr(rh, '_load_mask', lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError('mask read')))
    assert pa.asset_status(built.base, 'P1', built.path) == (pa.OK, [])
    assert calls == []


# ---- acceptance 5: open_generation counterexamples ---------------------------------------------------------------------

def repoint(world, product, kind, generation, complete_sha256):
    pointer = world.products_dir(product) / kind / 'CURRENT.json'
    pointer.write_text(json.dumps({'generation': generation, 'complete_sha256': complete_sha256}), encoding='utf-8')


def corrupt_reason(world, product, kind):
    with pytest.raises(pa.AssetError) as caught:
        pa.open_generation(world.base, product, kind)
    assert caught.value.state == pa.CORRUPT
    return caught.value.code


def test_open_generation_refuses_a_changed_side_json(built):
    side = pa.open_generation(built.base, 'P1', 'side')
    path = side.path / 'side.json'
    raw = path.read_bytes()
    path.write_bytes(raw[:-1] + (b'}' if raw[-1:] != b'}' else b' '))        # same size, other bytes
    assert corrupt_reason(built, 'P1', 'side') == 'asset_corrupt:file_sha256_mismatch'
    path.write_bytes(raw + b'\n')
    assert corrupt_reason(built, 'P1', 'side') == 'asset_corrupt:file_size_mismatch'
    path.unlink()
    assert corrupt_reason(built, 'P1', 'side') == 'asset_corrupt:payload_file_missing'


def test_open_generation_refuses_a_complete_json_of_another_product_or_kind(built):
    for field, value, code in (('product', 'P2', 'product_mismatch'), ('kind', 'hotspots', 'kind_mismatch')):
        folder = built.products_dir('P3')
        side = pa.open_generation(built.base, 'P3', 'side')
        original = (side.path / 'complete.json').read_bytes()
        document = json.loads(original.decode('utf-8'))
        document[field] = value
        raw = pa._json_bytes(document)
        (side.path / 'complete.json').write_bytes(raw)
        repoint(built, 'P3', 'side', side.name, pa.sha256_bytes(raw))      # the pointer matches: only the content is wrong
        assert corrupt_reason(built, 'P3', 'side') == 'asset_corrupt:' + code
        (side.path / 'complete.json').write_bytes(original)
        repoint(built, 'P3', 'side', side.name, pa.sha256_bytes(original))
        assert pa.open_generation(built.base, 'P3', 'side').name == side.name
        assert folder.is_dir()


def test_open_generation_refuses_a_pointer_to_another_generation(built):
    assert step(built, 'P1', '01', 'T2', force=True)[0] == 0
    names = sorted(p.name for p in (built.products_dir('P1') / 'hotspots').iterdir() if pa.GEN_RE.fullmatch(p.name))
    assert len(names) == 2
    old, new = names
    new_sha = pa.sha256_file(built.products_dir('P1') / 'hotspots' / new / 'complete.json')
    repoint(built, 'P1', 'hotspots', old, new_sha)                 # the other generation with this pointer's sha
    assert corrupt_reason(built, 'P1', 'hotspots') == 'asset_corrupt:complete_sha256_mismatch'
    # a copy of a generation under another folder name: complete.generation differs from the folder
    copy_name = 'g0009-deadbeef'
    shutil.copytree(str(built.products_dir('P1') / 'hotspots' / new), str(built.products_dir('P1') / 'hotspots' / copy_name))
    repoint(built, 'P1', 'hotspots', copy_name, new_sha)
    assert corrupt_reason(built, 'P1', 'hotspots') == 'asset_corrupt:generation_name_mismatch'
    repoint(built, 'P1', 'hotspots', '../x', new_sha)
    assert corrupt_reason(built, 'P1', 'hotspots') == 'asset_corrupt:pointer_generation_name'
    (built.products_dir('P1') / 'hotspots' / 'CURRENT.json').write_text('{"generation": "' + new + '"', encoding='utf-8')
    assert corrupt_reason(built, 'P1', 'hotspots') == 'asset_corrupt:pointer_unreadable'


def test_a_corrupt_01_is_rebuilt_and_02_refuses_it(built):
    world = built
    manifest = pa.open_generation(world.base, 'P3', 'hotspots').path / 'manifest.json'
    manifest.write_bytes(manifest.read_bytes() + b'\n')
    assert states(world, 'P3')['hotspots'][0] == pa.CORRUPT
    code, _outs, errs = step(world, 'P3', '02', 'T2')
    assert code == 2 and any('asset_missing' in e and 'products-step 01' in e for e in errs)
    code, outs, _errs = step(world, 'P3', '01', 'T2')
    assert code == 0 and outs[-1].startswith('built 01 P3 g0002-')
    assert states(world, 'P3')['hotspots'] == (pa.OK, [])


def test_missing_pointer_without_generations_is_missing(world):
    with pytest.raises(pa.AssetError) as caught:
        pa.open_generation(world.base, 'P1', 'side')
    assert caught.value.state == pa.MISSING and caught.value.code == 'asset_missing'


def test_missing_pointer_with_generations_is_corrupt_and_nothing_moves(built):
    """R5:4"""
    kdir = built.products_dir('P1') / 'side'
    (kdir / 'CURRENT.json').unlink()
    assert corrupt_reason(built, 'P1', 'side') == 'asset_corrupt:pointer_missing_with_generations'
    assert pa.asset_status(built.base, 'P1', built.path)[0] == pa.CORRUPT
    assert pa.archive_names(kdir) == []
    code, _outs, _errs = step(built, 'P1', '02', 'T2')      # a rebuild is the way out; the old folder is archived after
    assert code == 0 and pa.asset_status(built.base, 'P1', built.path) == (pa.OK, [])


class MaskCounter(object):
    def __init__(self, monkeypatch):
        self.calls = 0
        original = rh._load_mask

        def counted(*args, **kwargs):
            self.calls += 1
            return original(*args, **kwargs)
        monkeypatch.setattr(rh, '_load_mask', counted)


def test_a_normal_seal_decodes_no_mask_and_the_full_check_decodes_all(built, monkeypatch, capsys):
    counter = MaskCounter(monkeypatch)
    generation, manifest = pa.load_hotspots_manifest(built.base, 'P1', verify='seal')
    assert counter.calls == 0 and capsys.readouterr().err == ''
    assert generation.seal()['schema'] == 'mask_seal_v1' and manifest['expected_cases']
    pa.load_hotspots_manifest(built.base, 'P1', verify='full')
    assert counter.calls == len(generation.complete['masks']) > 0


def test_seal_falls_back_to_the_full_check_when_a_mask_is_missing_or_added(built, monkeypatch, capsys):
    generation = pa.open_generation(built.base, 'P2', 'hotspots')
    seal = generation.seal()
    extra = dict(seal, masks=seal['masks'] + [{'path': 'assets/extra.npz', 'size': 1, 'mtime_ns': 1}])
    counter = MaskCounter(monkeypatch)
    rh.load_real_manifest(generation.path, verify='seal', seal=extra)             # a mask added to the seal
    assert 'mask_seal_mismatch:mask_set' in capsys.readouterr().err and counter.calls > 0
    short = dict(seal, masks=seal['masks'][1:])                                     # a mask left out of the seal
    counter.calls = 0
    rh.load_real_manifest(generation.path, verify='seal', seal=short)
    assert 'mask_seal_mismatch:mask_set' in capsys.readouterr().err and counter.calls > 0
    (generation.path / seal['masks'][0]['path']).unlink()                          # a mask file removed from the folder
    with pytest.raises(Exception):
        pa.load_hotspots_manifest(built.base, 'P2', verify='seal')
    assert 'mask_seal_mismatch' in capsys.readouterr().err


def test_seal_is_made_from_complete_json_only(built):
    generation = pa.open_generation(built.base, 'P1', 'hotspots')
    seal = generation.seal()
    assert seal['manifest_sha256'] == pa.sha256_file(generation.path / 'manifest.json')
    assert seal['masks'] == generation.complete['masks'] and seal['verification'] == 'full'
    side = pa.open_generation(built.base, 'P1', 'side')
    with pytest.raises(ValueError):
        side.seal()


# ---- acceptance 4: interruption injection ------------------------------------------------------------------------------

class Boom(RuntimeError):
    pass


def hotspot_names(world, product):
    return sorted(p.name for p in (world.products_dir(product) / 'hotspots').iterdir() if pa.GEN_RE.fullmatch(p.name))


def leftovers(world, product):
    return sorted(p.name for p in (world.products_dir(product) / 'hotspots').iterdir() if p.name.startswith('.'))


def runs_items(world, tag):
    root = world.base / 'data/_runs' / tag
    return sorted(p.relative_to(root).as_posix() for p in root.rglob('*')) if root.exists() else []


def stale_world(world):
    build_all(world, ('P1',), 'T0')
    rewrite_hotspot(world, 'P1', AB, 1, {5, 6, 7})        # P1's 01 is stale
    return pa.open_generation(world.base, 'P1', 'hotspots').name


def next_run_recovers(world, old, expect_new_published):
    code, outs, errs = step(world, 'P1', '01', 'T9')
    assert code == 0, errs
    assert leftovers(world, 'P1') == []                          # staging and temporary files are gone from the folder
    current = pa.open_generation(world.base, 'P1', 'hotspots')
    assert states(world, 'P1')['hotspots'] == (pa.OK, [])
    if expect_new_published:
        assert outs == ['skip fresh 01 P1'] and current.name != old
    else:
        assert outs[-1].startswith('built 01 P1') and current.name != old
    return current


def test_interrupted_while_writing_staging_keeps_the_old_generation(world, monkeypatch):
    old = stale_world(world)
    real_import = rh.import_hotspots

    def partial(root, out, **kwargs):
        Path(out).mkdir(parents=True)
        (Path(out) / 'half.bin').write_bytes(b'half')
        raise Boom('disk full')
    monkeypatch.setattr(rh, 'import_hotspots', partial)
    code, _outs, errs = step(world, 'P1', '01', 'T1')
    assert code == 2 and any('Boom' in e for e in errs)
    monkeypatch.setattr(rh, 'import_hotspots', real_import)
    assert pa.open_generation(world.base, 'P1', 'hotspots').name == old        # the pointer still names the old one
    assert len(leftovers(world, 'P1')) == 1 and log_lines(world, 'P1')[-1]['action'] == 'fail'
    next_run_recovers(world, old, expect_new_published=False)
    assert any(item.endswith('half.bin') for item in runs_items(world, 'T9'))


def test_interrupted_before_complete_json_is_written(world, monkeypatch):
    old = stale_world(world)
    monkeypatch.setattr(pa, '_write_complete', lambda *a, **k: (_ for _ in ()).throw(Boom('before complete')))
    assert step(world, 'P1', '01', 'T1')[0] == 2
    monkeypatch.undo()
    assert pa.open_generation(world.base, 'P1', 'hotspots').name == old
    assert hotspot_names(world, 'P1') == [old] and len(leftovers(world, 'P1')) == 1
    next_run_recovers(world, old, expect_new_published=False)
    assert any('.hotspots.staging-' in item for item in runs_items(world, 'T9'))


def test_interrupted_right_after_the_folder_rename(world, monkeypatch):
    old = stale_world(world)
    original = pa._rename_generation

    def rename_then_die(staging, target):
        original(staging, target)
        raise Boom('after rename')
    monkeypatch.setattr(pa, '_rename_generation', rename_then_die)
    assert step(world, 'P1', '01', 'T1')[0] == 2
    monkeypatch.undo()
    assert pa.open_generation(world.base, 'P1', 'hotspots').name == old          # never published
    orphan = [n for n in hotspot_names(world, 'P1') if n != old]
    assert len(orphan) == 1
    current = next_run_recovers(world, old, expect_new_published=False)
    assert current.name not in orphan and orphan[0] not in hotspot_names(world, 'P1')     # the orphan was archived
    assert any(item.endswith(orphan[0]) for item in runs_items(world, 'T9'))


def test_interrupted_right_after_the_pointer_replace_keeps_the_new_generation(world, monkeypatch):
    old = stale_world(world)
    original = pa._write_pointer

    def pointer_then_die(kdir, generation, sha):
        original(kdir, generation, sha)
        raise Boom('after pointer')
    monkeypatch.setattr(pa, '_write_pointer', pointer_then_die)
    assert step(world, 'P1', '01', 'T1')[0] == 2
    monkeypatch.undo()
    new = pa.open_generation(world.base, 'P1', 'hotspots').name
    assert new != old                                                            # published: the pointer is not reverted
    assert states(world, 'P1')['hotspots'] == (pa.OK, [])
    next_run_recovers(world, old, expect_new_published=True)
    assert pa.open_generation(world.base, 'P1', 'hotspots').name == new


def test_failed_archive_is_logged_as_pending_and_retried(world, monkeypatch):
    stale_world(world)
    assert step(world, 'P1', '01', 'T1', force=True)[0] == 0       # three generations: g0001 is due for the archive
    assert step(world, 'P1', '01', 'T1', force=True)[0] == 0
    names = hotspot_names(world, 'P1')
    assert len(names) == 2                                                        # current + previous
    # now make a new generation while every move fails
    monkeypatch.setattr(pa, '_move_to_runs', lambda *a, **k: (_ for _ in ()).throw(OSError('locked by another process')))
    code, outs, errs = step(world, 'P1', '01', 'T2', force=True)
    assert code == 0 and outs[-1].startswith('built 01 P1')                      # the publish is not undone
    assert any('보관 실패' in e for e in errs)
    assert len(hotspot_names(world, 'P1')) == 3
    pending = [l for l in log_lines(world, 'P1') if l['action'] == 'pending_archive']
    assert pending and pending[-1]['error_code'] == 'archive_failed' and pending[-1]['generation'] == names[0]
    current = pa.open_generation(world.base, 'P1', 'hotspots').name
    assert states(world, 'P1')['hotspots'] == (pa.OK, [])
    monkeypatch.undo()
    code, outs, _errs = step(world, 'P1', '01', 'T3')
    assert code == 0 and outs[0] == 'archived P1/hotspots/%s' % names[0]
    assert pa.open_generation(world.base, 'P1', 'hotspots').name == current and len(hotspot_names(world, 'P1')) == 2
    assert any(item.endswith(names[0]) for item in runs_items(world, 'T3'))


def test_interrupted_side_and_summary_publish_keep_the_old_assets(built, monkeypatch):
    world = built
    old_side = pa.open_generation(world.base, 'P1', 'side').name
    summary = (world.products_dir('P1') / 'summary.json').read_bytes()
    rewrite_hotspot(world, 'P1', AB, 1, {5, 6, 7})
    assert step(world, 'P1', '01', 'T1')[0] == 0
    original = pa._write_pointer

    def side_pointer_dies(kdir, generation, sha):
        if kdir.name == 'side':
            raise Boom('side pointer')
        original(kdir, generation, sha)
    monkeypatch.setattr(pa, '_write_pointer', side_pointer_dies)
    assert step(world, 'P1', '02', 'T2')[0] == 2
    monkeypatch.undo()
    assert pa.open_generation(world.base, 'P1', 'side').name == old_side
    assert (world.products_dir('P1') / 'summary.json').read_bytes() == summary
    code, outs, _ = step(world, 'P1', '02', 'T3')
    assert code == 0 and pa.open_generation(world.base, 'P1', 'side').name != old_side
    assert states(world, 'P1')['summary'] == (pa.OK, [])
    assert leftovers_of_product(world) == []


def leftovers_of_product(world):
    return sorted(p.name for p in world.products_dir('P1').iterdir() if p.name.startswith('.'))


def test_interrupted_summary_leaves_the_old_summary_and_staging_is_moved(built, monkeypatch):
    world = built
    summary = (world.products_dir('P1') / 'summary.json').read_bytes()
    density = world.base / 'data/input/density/mobile_line.csv'
    density.write_text(density.read_text(encoding='utf-8').replace('5.0', '5.5'), encoding='utf-8')
    monkeypatch.setattr(pa, '_write_atomic', lambda *a, **k: (_ for _ in ()).throw(Boom('summary replace')))
    assert step(world, 'P1', '02', 'T1')[0] == 2
    monkeypatch.undo()
    assert (world.products_dir('P1') / 'summary.json').read_bytes() == summary
    assert len(leftovers_of_product(world)) == 1
    code, _outs, _errs = step(world, 'P1', '02', 'T2')
    assert code == 0 and leftovers_of_product(world) == [] and states(world, 'P1')['summary'] == (pa.OK, [])
    assert any('.summary.staging-' in item for item in runs_items(world, 'T2'))


def test_a_taken_generation_name_gets_a_new_id(world, monkeypatch):
    ids = iter(['aaaaaaaa', 'bbbbbbbb', 'cccccccc', 'dddddddd', 'eeeeeeee', 'ffffffff'])
    assert step(world, 'P3', '01', 'T0')[0] == 0
    kdir = world.products_dir('P3') / 'hotspots'
    (kdir / 'g0002-aaaaaaaa').mkdir()                                  # exactly the name the next publish would pick
    monkeypatch.setattr(pa, '_new_id8', lambda: next(ids))
    assert step(world, 'P3', '01', 'T1', force=True)[0] == 0
    assert pa.open_generation(world.base, 'P3', 'hotspots').name.startswith('g0003-') or \
        pa.open_generation(world.base, 'P3', 'hotspots').name != 'g0002-aaaaaaaa'


# ---- publish_generation unit test: handles closed and complete.json last ------------------------------------------------

def test_publish_writes_complete_json_last_and_lists_payload_files(world):
    order = []
    original = pa._write_complete

    def spy(staging, document):
        order.append(sorted(p.name for p in staging.iterdir()))
        return original(staging, document)

    def build(staging):
        staging.mkdir()
        (staging / 'side.json').write_text('{}', encoding='utf-8')
        return {'maps_src': {'status': 'none'}}
    pa._write_complete = spy
    try:
        generation = pa.publish_generation(world.base, 'P3', 'side', build, depends=[], fingerprint={'x': 1})
    finally:
        pa._write_complete = original
    assert order == [['side.json']] and generation.name.startswith('g0001-')
    assert generation.complete['files'] == [{'path': 'side.json', 'size': 2, 'sha256': pa.sha256_bytes(b'{}')}]
    assert generation.complete['fingerprint'] == {'x': 1}


# ---- the fingerprint helpers --------------------------------------------------------------------------------------------

def test_current_summary_deps_equal_the_deps_written_by_compare2_summary(built):
    world = built
    summary = json.loads((world.products_dir('P1') / 'summary.json').read_text(encoding='utf-8'))
    side = pa.open_generation(world.base, 'P1', 'side')
    cfg_pair = cs.load_pair_config(world.base / 'gen.product.P1.compare2.yaml')
    assert pa.current_summary_deps(cfg_pair[0], side.file_sha256('side.json')) == summary['deps']


# ---- acceptance 2: the product flow equals the classic pair flow (E4 02 part, E3') -------------------------------------------

# compare2.json fields that may differ: where the configs live and their sha, and the manifest hashes (the product flow
# imports with a group expected-case FILE, the classic flow with --expected-cases-from-roots: other manifest bytes).
ALLOWED_DIFFERENCES = {
    '/inputs/compare2_config/path', '/inputs/compare2_config/sha256', '/inputs/numeric_config/path',
    '/inputs/numeric_config/sha256',
    '/inputs/manifests/reference/input_hash', '/inputs/manifests/reference/source_payload_hash',
    '/inputs/manifests/target/input_hash', '/inputs/manifests/target/source_payload_hash',
    '/products/reference/source_payload_hash', '/products/target/source_payload_hash'}


def json_differences(a, b, path=''):
    if type(a) is not type(b):
        return [path]
    if isinstance(a, dict):
        found = []
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                found.append('%s/%s' % (path, key))
            else:
                found += json_differences(a[key], b[key], '%s/%s' % (path, key))
        return found
    if isinstance(a, list):
        if len(a) != len(b):
            return [path]
        return [d for index, (x, y) in enumerate(zip(a, b)) for d in json_differences(x, y, '%s[%d]' % (path, index))]
    return [] if a == b else [path]


def test_product_flow_equals_the_classic_pair_flow(built):
    from simcompare.cli import main
    world = built
    base = world.base
    sides = {p: pa.open_generation(base, p, 'side').path for p in ('P1', 'P2')}
    pc.run_products_config(world.path, pairs=[('P1', 'P2')], data_dirs={
        p: pa.open_generation(base, p, 'hotspots').relative_path for p in ('P1', 'P2')})
    assert main(['compare2-compose', '--config', str(base / 'gen.pair.P1__P2.compare2.yaml'), '--side-reference', str(sides['P1']),
                 '--side-target', str(sides['P2']), '--out', str(base / 'composed')]) == 0
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
    assert main(['compare2', '--config', str(base / 'classic.compare2.yaml')]) == 0
    new = json.loads((base / 'composed/compare2.json').read_text(encoding='utf-8'))
    old = json.loads((base / 'classic/compare2/compare2.json').read_text(encoding='utf-8'))
    assert set(json_differences(new, old)) == ALLOWED_DIFFERENCES        # the indicators (lambda, CAA, VI, rows) are equal
    assert new['sides']['target']['absent_expected_cases'] and old['sides']['target']['absent_expected_cases']
    for name in ('compare2_summary.csv', 'compare2_layers.csv', 'compare2_conditions.csv', 'compare2_cno.csv'):
        assert (base / 'composed' / name).read_bytes() == (base / 'classic/compare2' / name).read_bytes(), name


def test_side_of_the_product_settings_equals_the_side_of_a_pair_settings(built):
    world = built
    pc.run_products_config(world.path, pairs=[('P1', 'P2')], data_dirs={
        name: pa.open_generation(world.base, name, 'hotspots').relative_path for name in ('P1', 'P2')})
    cfg_pair = cs.load_pair_config(world.base / 'gen.pair.P1__P2.compare2.yaml')
    side = pa.open_generation(world.base, 'P1', 'side')
    document, _sha = cs.read_side(side.path)
    assert cs.side_binding(cfg_pair[1], 'reference', cfg_pair[2]) == document['binding']


# ---- T5b (R5 follow-ups) -----------------------------------------------------------------------------------------

def tree_snapshot(root):
    return sorted((str(p.relative_to(root)), p.stat().st_mtime_ns) for p in root.rglob('*'))


def test_asset_status_writes_nothing_and_works_on_a_read_only_base(built):
    """R5:1: the check builds the settings in memory; a changed setting reads 낡음 without a write."""
    world = built
    before = tree_snapshot(world.base)
    assert pa.asset_status(world.base, 'P1', world.path) == (pa.OK, [])
    assert tree_snapshot(world.base) == before
    (world.base / 'gen.product.P1.compare2.yaml').unlink()                # a missing generated file is a changed setting
    assert states(world, 'P1')['side'] == (pa.STALE, ['product_settings_changed'])
    assert not (world.base / 'gen.product.P1.compare2.yaml').exists()
    step(world, 'P1', '02', 'T2')
    assert (world.base / 'gen.product.P1.compare2.yaml').exists() and states(world, 'P1')['side'] == (pa.OK, [])
    write_bits = {}
    for path in [world.base] + list(world.base.rglob('*')):
        if path.is_dir():
            write_bits[path] = path.stat().st_mode
            os.chmod(str(path), 0o555)
    try:
        assert pa.asset_status(world.base, 'P1', world.path) == (pa.OK, [])
    finally:
        for path, mode in write_bits.items():
            os.chmod(str(path), mode)


def test_changed_mask_stat_is_corrupt(built):
    """R5:2: a mask whose size or mtime moved (or that is gone) makes 01 손상; no mask is read."""
    world = built
    generation = pa.open_generation(world.base, 'P1', 'hotspots')
    masks = [entry['path'] for entry in generation.complete['masks']]
    assert len(masks) >= 2
    bump_mtime(generation.path / masks[0])
    state, reasons = pa.asset_status(world.base, 'P1', world.path)
    assert state == pa.CORRUPT and 'asset_corrupt:mask_stat_changed' in reasons
    os.utime(str(generation.path / masks[0]), ns=(1, [e['mtime_ns'] for e in generation.complete['masks']][0]))
    assert pa.asset_status(world.base, 'P1', world.path) == (pa.OK, [])
    (generation.path / masks[1]).unlink()
    state, reasons = pa.asset_status(world.base, 'P1', world.path)
    assert state == pa.CORRUPT and 'asset_corrupt:mask_stat_changed' in reasons
    assert step(world, 'P1', '01', 'T2')[0] == 0                           # a rebuild is the way out
    assert states(world, 'P1')['hotspots'] == (pa.OK, [])


def test_01_reports_diagnostic_counts_and_02_reports_a_partial_summary_on_skip(world):
    """R5:3"""
    code, outs, _errs = step(world, 'P1', '01')
    assert code == 0 and outs[-2].startswith('diagnostics 01 P1: ') and outs[-1].startswith('built 01 P1 g0001-')
    entry = log_lines(world, 'P1')[-1]
    assert isinstance(entry['diagnostics'], dict)
    assert outs[-2] == 'diagnostics 01 P1: ' + (', '.join('%s=%d' % kv for kv in entry['diagnostics'].items()) or 'none')
    assert step(world, 'P1', '02')[0] == 0
    summary_path = world.products_dir('P1') / 'summary.json'
    document = json.loads(summary_path.read_text(encoding='utf-8'))
    document['status'] = 'partial'
    summary_path.write_text(json.dumps(document), encoding='utf-8')
    code, outs, _errs = step(world, 'P1', '02', 'T2')
    assert code == 0 and outs[-1] == 'skip fresh 02 summary P1 (summary status: partial)'
    assert log_lines(world, 'P1')[-1]['summary_status'] == 'partial'


def test_a_permission_error_is_retried_a_few_times_and_nothing_else_is(built, monkeypatch):
    """R5:5"""
    monkeypatch.setattr(pa, 'RETRY_WAIT_S', 0)
    real_replace = os.replace
    failures = {'left': 3}

    def flaky(src, dst):
        if failures['left'] > 0 and 'hotspots' in str(dst) and '/g0' in str(dst):      # the generation rename only
            failures['left'] -= 1
            raise PermissionError('held open')
        return real_replace(src, dst)
    monkeypatch.setattr(pa.os, 'replace', flaky)
    code, outs, _errs = step(built, 'P1', '01', 'T2', force=True)
    assert code == 0 and failures['left'] == 0 and outs[-1].startswith('built 01 P1 g0002-')
    failures['left'] = 99                                                    # never works: 5 tries, then the error
    calls = []
    monkeypatch.setattr(pa.os, 'replace', lambda s, d: calls.append(1) or (_ for _ in ()).throw(PermissionError('x')))
    with pytest.raises(PermissionError):
        pa._rename_generation(Path('a'), Path('b'))
    assert len(calls) == pa.RETRY_COUNT
    calls.clear()
    monkeypatch.setattr(pa.os, 'replace', lambda s, d: calls.append(1) or (_ for _ in ()).throw(OSError('other')))
    with pytest.raises(OSError):
        pa._rename_generation(Path('a'), Path('b'))
    assert len(calls) == 1


def test_os_side_files_do_not_change_the_inventory(built):
    """R5:7"""
    world = built
    folder = world.root('P1') / AB
    for name in ('Thumbs.db', 'desktop.ini', '.DS_Store', '._1.png', 'THUMBS.DB'):
        (folder / name).write_bytes(b'junk')
    (world.root('P1') / '._top').write_bytes(b'junk')
    assert states(world, 'P1')['hotspots'] == (pa.OK, [])
    (folder / 'notes.txt').write_bytes(b'real file')
    assert states(world, 'P1')['hotspots'] == (pa.STALE, ['source_changed'])


# ---- pasx/T8: the 02 recording (maps_src_v1) ------------------------------------------------------------------------------

def side_bytes(world, product):
    return pa.open_generation(world.base, product, 'side')


def run02(world, product, tag='T9', **kwargs):
    outs, errs = [], []
    code = pa.run_products_step(world.path, product, '02', run_tag=tag, out=outs.append, err=errs.append, **kwargs)
    return code, outs, errs


def test_02_side_json_is_the_same_with_and_without_the_recording(built):
    world = built
    recorded = side_bytes(world, 'P1')
    assert recorded.complete['maps_src']['status'] == 'complete'
    assert run02(world, 'P1', force=True, record_maps=False)[0] == 0
    plain = side_bytes(world, 'P1')
    assert plain.complete['maps_src'] == {'status': 'none'} and plain.name != recorded.name
    assert [e['path'] for e in plain.complete['files']] == ['side.json']
    assert (plain.path / 'side.json').read_bytes() == (recorded.path / 'side.json').read_bytes()
    assert not (plain.path / 'maps_src').exists() or not list((plain.path / 'maps_src').iterdir())


def test_02_a_recording_failure_still_publishes_the_side(built, monkeypatch):
    world = built
    from simcompare import maps_source as ms
    import errno

    def enospc(self, handle, data):
        raise OSError(errno.ENOSPC, 'No space left on device')
    monkeypatch.setattr(ms.MapsRecorder, '_write', enospc)
    code, outs, errs = run02(world, 'P1', force=True)
    assert code == 0 and any('maps_src 02 P1: failed (disk_full)' == line for line in outs)
    assert any('지도 원자료 녹화가 실패' in e for e in errs)
    side = side_bytes(world, 'P1')
    assert side.complete['maps_src'] == {'status': 'failed', 'reason': 'disk_full'}
    assert [e['path'] for e in side.complete['files']] == ['side.json']
    assert states(world, 'P1')['side'] == (pa.OK, [])
    line = [json.loads(t) for t in (world.base / 'data/products/P1/asset_log.jsonl').read_text().splitlines()
            if '"part": "side"' in t][-1]
    assert line['status'] == pa.OK and line['maps_src'] == 'failed' and line['maps_src_reason'] == 'disk_full'


def test_02_the_size_cap_stops_only_the_recording(built):
    world = built
    code, outs, errs = run02(world, 'P1', force=True, maps_max_bytes=100)
    assert code == 0
    assert side_bytes(world, 'P1').complete['maps_src'] == {'status': 'failed', 'reason': 'cap_exceeded'}


def test_02_a_side_json_write_failure_is_an_asset_failure_not_a_recording_failure(built, monkeypatch):
    world = built
    before = side_bytes(world, 'P1').name

    def broken(out, files):
        raise OSError(28, 'side.json could not be written')
    monkeypatch.setattr(cs, '_publish', broken)
    code, _outs, errs = run02(world, 'P1', force=True)
    assert code == 2 and any('compare2_side_failed' in e or 'asset' in e for e in errs)
    assert side_bytes(world, 'P1').name == before                 # nothing was published
    log = [json.loads(t) for t in (world.base / 'data/products/P1/asset_log.jsonl').read_text().splitlines()][-1]
    assert log['action'] == 'fail' and log['error_code'] not in (None, 'maps_src')
