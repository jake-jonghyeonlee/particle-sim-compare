"""lmap/T3: `ledger-maps` on the small real_hotspot_v1 fixtures of test_compare2.py.

Every test runs the real chain 02 (compare2) -> ledger -> ledger-maps on a 4x4 cell frame (0.1 um pixel pitch, so one
1 um tile covers the whole frame). unit_size_um = [0.2, 0.2] makes the unit window 2x2 px at origin (1, 1)."""
import copy
import hashlib
import json
import math
import struct

import numpy as np
import pytest
import yaml
from PIL import Image

from simcompare import ledger, ledger_maps
from simcompare.cli import main
from simcompare.numeric_schema import DIAMETERS_UM, NumericContractError
from test_compare2 import (CNO_CSV, CNO_NAME, PAIRS, RES, assignment, base_cases, write_density, write_side, DENS_LINE,
                           DENS_PIXEL)

CNO_B = 'defect_size(um),Area(um2)\n1,0.5\n2,1.5\n3,5.0\n'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_dl1(root, model, electrodes):
    """electrodes: {name: {layer: {cell,...}}}; black pixel = shape (1004 x 1004 png = 4x4 frame + 500 px border)."""
    for name, layers in electrodes.items():
        folder = root / model / name
        folder.mkdir(parents=True, exist_ok=True)
        for index, (layer, cells) in enumerate(sorted(layers.items()), start=1):
            array = np.full((1004, 1004), 255, dtype=np.uint8)
            for cell in cells:
                array[500 + cell // 4, 500 + cell % 4] = 0
            Image.fromarray(array).save(folder / f'{index}-{layer}.png')


def chain(tmp, ref=None, tgt=None, *, ref_group='mobile', tgt_group='mobile', unit=(0.2, 0.2), dl1=None,
          report=None, glass=50.0, line=None, pixel=None, ref_cno=None, tgt_cno=None, diameters=None, skip=(),
          thk=None):
    """02 + ledger. Returns the folders. dl1 = {'ref': {...electrodes}, 'tgt': {...}} writes root tmp/'dl1'."""
    ref = ref or (base_cases(), PAIRS)
    tgt = tgt or (base_cases(), PAIRS)
    ref_root, ref_imp = write_side(tmp, 'ref', *ref, cno=ref_cno, skip=skip)
    tgt_root, tgt_imp = write_side(tmp, 'tgt', *tgt, cno=tgt_cno, skip=skip)
    extra = {}
    if thk is not None:
        (tmp / 'thk_info.txt').write_text(thk, encoding='utf-8')
        extra = {'interlayer_distance_path': 'thk_info.txt'}
    numeric = {'schema_version': 'numeric_config_v1', 'mode': 'real', 'output_dir': 'unused',
               'reference': {'data_dir': str(ref_imp), 'condition_assignment': {**assignment(), **extra}},
               'target': {'data_dir': str(tgt_imp), 'condition_assignment': {**assignment(), **extra}}}
    (tmp / 'numeric.yaml').write_text(yaml.safe_dump(numeric), encoding='utf-8')
    for group in {ref_group, tgt_group}:
        write_density(tmp / 'density', group, DENS_LINE if line is None else line,
                      DENS_PIXEL if pixel is None else pixel)
    glass_cfg = {'value': glass, 'unit': 'um2'}
    cfg = {'schema_version': 'compare2_config_v1', 'numeric_config': 'numeric.yaml', 'output_dir': 'out',
           'density_dir': 'density',
           'model_groups': {group: {'glass_area': glass_cfg, 'weight_line': 1.0, 'weight_pixel': 1.0}
                            for group in {ref_group, tgt_group}},
           'products': {'reference': {'model_group': ref_group, 'resolution': RES, 'hotspot_root': str(ref_root)},
                        'target': {'model_group': tgt_group, 'resolution': RES, 'hotspot_root': str(tgt_root)}}}
    if unit is not None:
        cfg['products']['reference']['unit_size_um'] = list(unit)
        cfg['products']['target']['unit_size_um'] = list(unit)
    cfg['report'] = dict(report or {'diameter_um': 3})
    if dl1 is not None:
        write_dl1(tmp / 'dl1', 'MA', dl1['ref'])
        write_dl1(tmp / 'dl1', 'MB', dl1['tgt'])
        cfg['report']['dl1_root'] = str(tmp / 'dl1')
        cfg['products']['reference']['dl1_model'] = 'MA'
        cfg['products']['target']['dl1_model'] = 'MB'
    if diameters is not None:
        cfg['lambda_diameters_um'] = diameters
    (tmp / 'compare2.yaml').write_text(yaml.safe_dump(cfg), encoding='utf-8')
    code = main(['compare2', '--config', str(tmp / 'compare2.yaml')])
    assert code in (0, 3)
    led_code = ledger.main_ledger(str(tmp / 'out'), str(tmp / 'compare2.yaml'), str(tmp / 'led'), lambda record: None)
    assert led_code in (0, 3)
    return {'compare2': tmp / 'out', 'ledger': tmp / 'led', 'config': tmp / 'compare2.yaml'}


def maps_args(tmp, out='maps', *extra):
    return ['ledger-maps', '--compare2-dir', str(tmp / 'out'), '--config', str(tmp / 'compare2.yaml'),
            '--ledger-dir', str(tmp / 'led'), '--out', str(tmp / out), *extra]


def run_maps(tmp, out='maps', *extra):
    return main(maps_args(tmp, out, *extra)), tmp / out


def load_maps(out):
    return json.loads((out / 'ledger_maps.json').read_text(encoding='utf-8'))


def items_by_pair(doc, role):
    return {item['pair_id']: item for item in doc['sides'][role]['items']}


def results_hash(out):
    return {path.name: sha(path) for path in sorted(out.iterdir()) if path.name != 'execution.json'}


def png_chunks(path):
    data = path.read_bytes()
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    names, offset = [], 8
    while offset < len(data):
        length, = struct.unpack('>I', data[offset:offset + 4])
        names.append(data[offset + 4:offset + 8].decode('ascii'))
        offset += 12 + length
    return names


# ---- the basic run ----------------------------------------------------------------------------------------------

def test_basic_run_passes_checks_and_records_window_tiles_and_files(tmp_path):
    chain(tmp_path, dl1={'ref': {'A': {'L1': {0, 1}}, 'B': {'L1': {1, 2}}}, 'tgt': {'A': {'L1': {0, 1}}}})
    hashes = {name: sha(tmp_path / 'out' / 'compare2.json') for name in ('c',)}
    led_sha = sha(tmp_path / 'led' / 'ledger.json')
    code, out = run_maps(tmp_path)
    assert code == 0, (out / 'error.json').read_text() if (out / 'error.json').exists() else code
    doc = load_maps(out)
    assert doc['schema_version'] == 'ledger_maps_v2' and doc['status'] == 'ok'     # mrep/T3: cards got tabs and item values
    assert doc['checks']['status'] == 'passed'
    for role in ('reference', 'target'):
        assert doc['checks']['item_sum'][role]['status'] == 'passed'
        assert doc['checks']['item_sum'][role]['checked'] == len(doc['sides'][role]['items'])
        assert {kind: row['status'] for kind, row in doc['checks']['kind_sum'][role].items()} == {
            'line': 'passed', 'pixel': 'passed'}
        # one 1 um tile covers the 0.4 um frame
        assert doc['sides'][role]['tiles']['count'] == 1 and doc['sides'][role]['tiles']['shape_yx'] == [1, 1]
        window = doc['window'][role]
        assert window['size_px'] == [2, 2] and window['origin_px'] == [1, 1]
        assert window['unit_source'] == 'config' and window['unit_um'] == [0.2, 0.2]
        assert window['tile_origin_um'] == [50.0, 50.0]
        assert window['origin_um'] == pytest.approx([50.1, 50.1])
    # lmap/T4: the cards and checks 3-5 exist now (their details are in test_ledger_maps_cards.py)
    assert doc['cards']
    for name in ('range_sum', 'pair_bound', 'partition'):
        # a one-tile frame: every range leaves the frame (edge: excluded), so nothing is left to check
        assert doc['checks'][name]['status'] in ('passed', 'not_evaluated'), doc['checks'][name]
    assert doc['options']['tile_um'] == 1.0 and doc['report']['diameter_um'] == 3.0
    execution = json.loads((out / 'execution.json').read_text())
    assert execution['command'] == 'ledger-maps' and execution['exit_code'] == 0
    assert 0 < execution['max_reserved_bytes'] <= execution['max_operation_bytes']
    # stage 1 outputs stay untouched
    assert sha(tmp_path / 'led' / 'ledger.json') == led_sha
    assert doc['inputs']['ledger_json_sha256'] == led_sha
    assert doc['inputs']['compare2_json_sha256'] == sha(tmp_path / 'out' / 'compare2.json')
    # AB: line item, three cells at 1 um, one at 3 um (the map diameter)
    ab = items_by_pair(doc, 'reference')['AB']
    assert ab['status'] == 'ok' and ab['kind'] == 'line' and ab['tile_sum'] == pytest.approx(ab['value'])
    names = sorted(path.name for path in out.iterdir())
    assert 'ledger_maps.json' in names and 'execution.json' in names
    assert 'map_reference_001_it.png' in names and 'map_reference_001_el.png' in names
    assert 'map_reference_002_it.png' in names     # layer L2 (GH, IJ)
    del hashes


def test_two_runs_have_identical_bytes_except_execution_json(tmp_path):
    chain(tmp_path, dl1={'ref': {'A': {'L1': {0, 1}}}, 'tgt': {'A': {'L1': {0, 1}}}})
    code1, out1 = run_maps(tmp_path, 'm1')
    code2, out2 = run_maps(tmp_path, 'm2')
    assert code1 == 0 and code2 == 0
    assert results_hash(out1) == results_hash(out2)
    code3, _ = run_maps(tmp_path, 'm1')       # a previous ledger-maps folder may be overwritten
    assert code3 == 0 and results_hash(out1) == results_hash(out2)


def test_png_roundtrip_matches_json_numbers_and_has_no_color_chunks(tmp_path):
    chain(tmp_path, dl1={'ref': {'A': {'L1': {0, 1}}, 'B': {'L1': {1, 2}}}, 'tgt': {'A': {'L1': {0, 1}}}})
    code, out = run_maps(tmp_path)
    assert code == 0
    doc = load_maps(out)
    seen = 0
    for role in ('reference', 'target'):
        for entry in doc['sides'][role]['maps']:
            path = out / entry['file']
            image = Image.open(path)
            assert image.mode == 'RGB'
            array = np.asarray(image)
            assert not array[..., 2].any()
            numbers = array[..., 0].astype(np.int64) | (array[..., 1].astype(np.int64) << 8)
            assert list(numbers.shape) == entry['shape_yx']
            counts = np.bincount(numbers.reshape(-1))
            assert {str(i): int(c) for i, c in enumerate(counts) if i and c} == entry['numbers']
            chunks = png_chunks(path)
            assert not {'gAMA', 'sRGB', 'iCCP', 'tIME', 'tEXt', 'zTXt', 'iTXt'} & set(chunks)
            seen += 1
    assert seen >= 4
    # electrode maps: names sort, the first name wins a shared pixel, the overlap is counted
    electrodes = {row['name']: row for row in doc['sides']['reference']['electrodes']}
    assert electrodes['A']['number'] == 1 and electrodes['B']['number'] == 2
    assert electrodes['B']['layers']['L1']['overlap_px'] >= 0
    assert doc['sides']['reference']['hidden']['electrode_overlap_px'] == sum(
        layer['overlap_px'] for row in electrodes.values() for layer in row['layers'].values())


def test_dl1_not_configured_warns_and_makes_no_electrode_picture(tmp_path):
    chain(tmp_path)
    code, out = run_maps(tmp_path)
    assert code == 0
    doc = load_maps(out)
    assert {w['code'] for w in doc['warnings']} == {'maps_dl1_not_configured'}
    assert not [p for p in out.iterdir() if p.name.endswith('_el.png')]
    assert doc['sides']['reference']['electrodes'] == []


# ---- counter examples ----------------------------------------------------------------------------------------------

def test_item_without_area_at_the_map_diameter_is_marked_no_map_area(tmp_path):
    chain(tmp_path)                         # AC has no 3 um cell; the map diameter is 3
    code, out = run_maps(tmp_path)
    assert code == 0
    doc = load_maps(out)
    ac = items_by_pair(doc, 'reference')['AC']
    assert ac['status'] == 'no_map_area' and ac['has_map_area'] is False and ac['membership_tiles'] == 0
    assert ac['tile_sum'] == pytest.approx(ac['value']) and ac['value'] > 0   # it still has a value at 1/2 um
    assert items_by_pair(doc, 'reference')['AB']['status'] == 'ok'


def test_electrode_only_in_one_product(tmp_path):
    ref = (base_cases(), PAIRS)
    tgt = (base_cases(EF=None), PAIRS)   # no E-F hotspot case in the target (the pair table is shared)
    chain(tmp_path, ref=ref, tgt=tgt)
    code, out = run_maps(tmp_path)
    assert code == 0
    doc = load_maps(out)
    assert 'EF' in items_by_pair(doc, 'reference') and 'EF' not in items_by_pair(doc, 'target')
    for role in ('reference', 'target'):
        assert doc['checks']['item_sum'][role]['status'] == 'passed'
    led = json.loads((tmp_path / 'led' / 'ledger.json').read_text())
    ef = [item for pair in led['pairs'] if pair['pair_id'] == 'EF' for item in pair['items']]
    assert ef and all(item['presence'] == 'gone' and item['b'] == 0.0 for item in ef)


def test_pending_values_are_not_evaluated_but_the_pictures_exist(tmp_path):
    # no line density for layer P2 -> the line value of L2 is pending -> every line value of the ledger is null
    line = {'P1': DENS_LINE['P1']}
    chain(tmp_path, line=line)
    code, out = run_maps(tmp_path)
    doc = load_maps(out)
    assert code == 0, doc['reasons']
    line_items = [i for i in doc['sides']['reference']['items'] if i['kind'] == 'line']
    assert line_items and all(i['status'] == 'pending' and i['value'] is None and i['tile_sum'] is None
                              for i in line_items)
    assert doc['checks']['item_sum']['reference']['not_evaluated'] == len(line_items)
    assert doc['checks']['kind_sum']['reference']['line']['status'] == 'not_evaluated'
    assert doc['checks']['kind_sum']['reference']['pixel']['status'] == 'passed'
    assert (out / 'map_reference_001_it.png').is_file()
    assert any(i['has_map_area'] for i in line_items)      # the membership is still there


def test_crp_intercept_and_cno_are_not_in_the_tiles(tmp_path):
    chain(tmp_path, ref_group='mobile', tgt_group='auto', ref_cno={CNO_NAME: CNO_CSV}, tgt_cno={CNO_NAME: CNO_B})
    led = json.loads((tmp_path / 'led' / 'ledger.json').read_text())
    assert led['inputs']['products']['reference']['crp'] != led['inputs']['products']['target']['crp']
    assert led['totals']['line']['intercept']['a'] not in (None, 0.0) or led['totals']['pixel']['intercept']['a'] not in (None, 0.0)
    assert led['totals']['pixel']['cno']['a'] not in (None, 0.0)
    assert led['totals']['pixel']['crp']['present'] is True
    code, out = run_maps(tmp_path)
    assert code == 0
    doc = load_maps(out)
    for role, key in (('reference', 'a'), ('target', 'b')):
        assert doc['checks']['item_sum'][role]['status'] == 'passed'
        for kind in ('line', 'pixel'):
            check = doc['checks']['kind_sum'][role][kind]
            assert check['status'] == 'passed'
            assert check['total'] == pytest.approx(led['totals'][kind]['items'][key])
            if kind == 'pixel':
                # the column total (items + intercept + CNO + CRP) is something else: the tiles hold the items only
                assert check['sum'] != pytest.approx(led['totals']['pixel'][key], rel=1e-9)


def test_two_pairs_with_identical_masks_keep_both_values_but_one_picture_number(tmp_path):
    cases = base_cases(AC={1: {0, 1, 2}, 2: {0, 1}, 3: {0}})        # AC = AB, both line pairs of L1
    chain(tmp_path, ref=(cases, PAIRS), tgt=(cases, PAIRS))
    code, out = run_maps(tmp_path)
    assert code == 0
    doc = load_maps(out)
    items = items_by_pair(doc, 'reference')
    ab, ac = items['AB'], items['AC']
    assert ab['number'] < ac['number']
    assert ab['has_map_area'] and ac['has_map_area'] and ab['value'] > 0 and ac['value'] > 0
    assert ab['membership_tiles'] == 1 and ac['membership_tiles'] == 1
    assert ab['hidden_px'] == 0 and ac['hidden_px'] > 0 and ac['shown_px'] == 0
    numbers = [m for m in doc['sides']['reference']['maps'] if m['layer'] == 'L1' and m['kind'] == 'it'][0]['numbers']
    assert str(ab['number']) in numbers and str(ac['number']) not in numbers
    assert doc['sides']['reference']['hidden']['item_px'] >= ac['hidden_px']
    assert doc['checks']['item_sum']['reference']['status'] == 'passed'


def test_line_wins_over_pixel_on_a_shared_picture_pixel(tmp_path):
    cases = base_cases(DE={1: {1}, 2: {4, 5}, 3: {0}})               # the pixel pair DE sits on the line AB cell 0
    chain(tmp_path, ref=(cases, PAIRS), tgt=(cases, PAIRS))
    code, out = run_maps(tmp_path)
    assert code == 0
    items = items_by_pair(load_maps(out), 'reference')
    assert items['AB']['hidden_px'] == 0 and items['DE']['hidden_px'] > 0 and items['DE']['kind'] == 'pixel'


def test_thin_component_survives_the_display_reduction_and_the_membership_is_unchanged(tmp_path):
    chain(tmp_path)
    code, narrow = run_maps(tmp_path, 'fine')
    code2, coarse = run_maps(tmp_path, 'coarse', '--map-px-um', '0.8')
    assert code == 0 and code2 == 0
    fine, rough = load_maps(narrow), load_maps(coarse)
    for role in ('reference', 'target'):
        for a, b in zip(fine['sides'][role]['items'], rough['sides'][role]['items']):
            assert a['membership_tiles'] == b['membership_tiles'] and a['tile_sum'] == b['tile_sum']
            assert (a['shown_px'] > 0) == (b['shown_px'] > 0)             # a block with one cell is kept
    assert rough['sides']['reference']['region']['block_px'] > fine['sides']['reference']['region']['block_px']


def test_max_map_px_doubles_the_scale_and_records_the_steps(tmp_path):
    chain(tmp_path)
    code, out = run_maps(tmp_path, 'm', '--max-map-px', '1')
    assert code == 0
    doc = load_maps(out)
    assert doc['scale_steps'] and all(step['reason'] == 'max_map_px' for step in doc['scale_steps'])
    for role in ('reference', 'target'):
        for entry in doc['sides'][role]['maps']:
            assert max(entry['shape_yx']) <= 1


def test_max_tile_entries_exceeded_stops_without_result_files(tmp_path):
    chain(tmp_path)
    code, out = run_maps(tmp_path, 'm', '--max-tile-entries', '1')
    assert code == 2
    assert sorted(p.name for p in out.iterdir()) == ['error.json', 'execution.json']
    assert json.loads((out / 'error.json').read_text())['code'] == 'oversized_real_operation'


def test_option_out_of_range_exits_2_with_error_json(tmp_path):
    chain(tmp_path)
    code, out = run_maps(tmp_path, 'm', '--tile-um', '3')
    assert code == 2
    assert json.loads((out / 'error.json').read_text())['code'] == 'ledger_maps_option_invalid'
    assert not (out / 'ledger_maps.json').exists()
    for flag, value in (('--range-um', '0'), ('--max-rows', '0'), ('--coord-min-overlap', '1.5'),
                        ('--linear-min-aspect', '0.5'), ('--max-map-bytes', '0')):
        code, out = run_maps(tmp_path, 'o' + flag.strip('-'), flag, value)
        assert code == 2 and json.loads((out / 'error.json').read_text())['code'] == 'ledger_maps_option_invalid'


def test_tile_that_is_not_a_whole_number_of_pixels_exits_2(tmp_path):
    chain(tmp_path)
    code, out = run_maps(tmp_path, 'm', '--tile-um', '0.55')
    assert code == 2 and json.loads((out / 'error.json').read_text())['code'] == 'ledger_maps_option_invalid'


def test_output_folder_with_foreign_files_exits_2_and_touches_nothing(tmp_path):
    chain(tmp_path)
    out = tmp_path / 'maps'
    out.mkdir()
    (out / 'notes.txt').write_text('mine', encoding='utf-8')
    code, _ = run_maps(tmp_path)
    assert code == 2
    assert sorted(p.name for p in out.iterdir()) == ['notes.txt']
    assert (out / 'notes.txt').read_text(encoding='utf-8') == 'mine'


def test_missing_ledger_or_stale_ledger_exits_2(tmp_path):
    chain(tmp_path)
    (tmp_path / 'led' / 'ledger.json').rename(tmp_path / 'ledger_moved.json')
    code, out = run_maps(tmp_path, 'm1')
    assert code == 2 and json.loads((out / 'error.json').read_text())['code'] == 'ledger_maps_input_missing'
    (tmp_path / 'ledger_moved.json').rename(tmp_path / 'led' / 'ledger.json')
    doc = json.loads((tmp_path / 'led' / 'ledger.json').read_text())
    doc['inputs']['compare2_json_sha256'] = '0' * 64
    (tmp_path / 'led' / 'ledger.json').write_text(json.dumps(doc), encoding='utf-8')
    code, out = run_maps(tmp_path, 'm2')
    assert code == 2 and json.loads((out / 'error.json').read_text())['code'] == 'ledger_maps_stale'


def test_missing_candidates_folder_exits_2_and_existing_one_is_only_checked(tmp_path):
    chain(tmp_path)
    code, out = run_maps(tmp_path, 'm1', '--candidates', str(tmp_path / 'nope'))
    assert code == 2 and json.loads((out / 'error.json').read_text())['code'] == 'ledger_maps_input_missing'
    (tmp_path / 'cand').mkdir()
    code, out = run_maps(tmp_path, 'm2', '--candidates', str(tmp_path / 'cand'))
    assert code == 0
    assert load_maps(out)['inputs']['candidates']['exists'] is True


def test_changed_thk_file_gives_the_07_input_hash_code(tmp_path):
    pairs = PAIRS + [('XY', 'X', 'Y', 'line')]
    vertical = ('vertical_short', 'X', 'L1', 'Y', 'L2', {1: set(), 2: {3, 4}, 3: {3, 4, 5}})
    side = (base_cases() + [vertical], pairs)
    chain(tmp_path, ref=side, tgt=side, thk='layers\tdistance\nL1L2\t0.5\n')
    (tmp_path / 'thk_info.txt').write_text('layers\tdistance\nL1L2\t0.1\n', encoding='utf-8')
    code, out = run_maps(tmp_path)
    assert code == 2
    assert json.loads((out / 'error.json').read_text())['code'] == 'compare2_input_hash_mismatch'
    assert not (out / 'ledger_maps.json').exists()


def test_every_diameter_outside_the_ledger_range_runs_with_pending_values(tmp_path):
    # R1:2 — no 3 um input at all while lambda_diameters_um lists only 3: compare2 exits 3, nothing is left to count
    chain(tmp_path, diameters=[3], skip=(3,))
    code, out = run_maps(tmp_path)
    doc = load_maps(out)
    assert code == 0, doc['reasons']
    items = doc['sides']['reference']['items']
    assert items and all(i['status'] in ('pending', 'no_map_area') for i in items)
    assert all(i['tile_sum'] is None or i['tile_sum'] == 0 for i in items)
    assert doc['checks']['status'] == 'passed'


def test_only_listed_diameters_enter_the_tiles(tmp_path):
    chain(tmp_path, diameters=[1, 3])
    code, out = run_maps(tmp_path)
    doc = load_maps(out)
    assert code == 0
    for role in ('reference', 'target'):
        assert doc['checks']['item_sum'][role]['status'] == 'passed'
        assert all(row['status'] == 'passed' for row in doc['checks']['kind_sum'][role].values())


def test_sink_does_not_change_the_frame_it_receives(tmp_path, monkeypatch):
    chain(tmp_path)
    real_load = ledger_maps._load_side
    calls = []

    def load(cfg, role, top, share_sink=None):
        def watching(**kw):
            before = kw['frame'].valid.copy()
            share_sink(**kw)
            assert np.array_equal(kw['frame'].valid, before)
            calls.append(1)
        return real_load(cfg, role, top, share_sink=watching)

    monkeypatch.setattr(ledger_maps, '_load_side', load)
    code, _ = run_maps(tmp_path)
    assert code == 0 and calls


def test_a_value_check_failure_writes_the_output_and_exits_3(tmp_path):
    chain(tmp_path)
    doc = json.loads((tmp_path / 'led' / 'ledger.json').read_text())
    item = doc['pairs'][0]['items'][0]
    item['a'] = item['a'] * 2 if item['a'] else 1.0           # the ledger no longer matches the tiles
    (tmp_path / 'led' / 'ledger.json').write_text(json.dumps(doc), encoding='utf-8')
    # ledger.json was edited, so its recorded compare2 hash is kept: the command accepts it and the check fails
    code, out = run_maps(tmp_path)
    assert code == 3
    result = load_maps(out)
    assert result['status'] == 'partial' and result['reasons'] == ['ledger_maps_value_check_failed']
    assert result['checks']['item_sum']['reference']['status'] == 'failed'
    assert (out / 'map_reference_001_it.png').is_file()


# ---- memory budget -------------------------------------------------------------------------------------------------

def maps_direct(tmp, out='maps', **kwargs):
    code = ledger_maps.main_ledger_maps(str(tmp / 'out'), str(tmp / 'compare2.yaml'), str(tmp / 'led'),
                                        str(tmp / out), lambda record: None, **kwargs)
    return code, tmp / out


def test_budget_exceeded_before_reading_exits_2_without_result_files(tmp_path):
    chain(tmp_path)
    code, out = maps_direct(tmp_path, max_operation_bytes=100)
    assert code == 2
    assert sorted(p.name for p in out.iterdir()) == ['error.json', 'execution.json']
    error = json.loads((out / 'error.json').read_text())
    assert error['code'] == 'oversized_real_operation' and 'max_operation_bytes' in error['detail']


def test_budget_in_range_records_the_peak_reservation(tmp_path, monkeypatch):
    # rnarr/T6: the exact-peak limit is the limit of the existing operations; the occurrence metric would give its
    # memory back there (exit 3, tests/test_ledger_maps_occurrence.py), so this test runs without it
    # rnarr/T6b (R11:5): still needed after the single-piece fix: the connected-region temporaries (138 bytes here)
    # do not fit on top of the existing peak, so that item stays pending (exit 3)
    monkeypatch.setattr(ledger_maps, 'OCCURRENCE_ENABLED', False)
    chain(tmp_path)
    code, out = maps_direct(tmp_path)
    assert code == 0
    peak = json.loads((out / 'execution.json').read_text())['max_reserved_bytes']
    # the same run with exactly that limit still passes, one byte less must fail somewhere
    code, out = maps_direct(tmp_path, 'm2', max_operation_bytes=peak)
    assert code == 0
    code, out = maps_direct(tmp_path, 'm3', max_operation_bytes=peak - 1)
    assert code == 2 and json.loads((out / 'error.json').read_text())['code'] == 'oversized_real_operation'
    assert not (out / 'ledger_maps.json').exists()


def test_a_large_sink_temporary_is_refused_before_it_is_allocated(tmp_path, monkeypatch):
    chain(tmp_path)
    code, out = maps_direct(tmp_path)
    limit = json.loads((out / 'execution.json').read_text())['max_reserved_bytes']
    monkeypatch.setattr(ledger_maps, 'SINK_BYTES_PER_CELL', 10 ** 9)
    reserved = []
    real_reserve = ledger_maps._Budget.reserve

    def reserve(self, nbytes, what, **kwargs):
        reserved.append(what)
        return real_reserve(self, nbytes, what, **kwargs)

    monkeypatch.setattr(ledger_maps._Budget, 'reserve', reserve)
    code, out = maps_direct(tmp_path, 'm2', max_operation_bytes=limit)
    assert code == 2
    error = json.loads((out / 'error.json').read_text())
    assert error['code'] == 'oversized_real_operation' and 'sink temporaries' in error['detail']
    # the sink refused its temporaries first: nothing item-sized was reserved (or allocated) before the stop
    assert reserved[-1].startswith('sink temporaries')
    assert not [what for what in reserved if what.startswith(('item sparse values', 'item membership'))]


def test_cards_of_the_first_product_stay_reserved_while_the_second_is_read(tmp_path, monkeypatch):
    chain(tmp_path)
    code, out = maps_direct(tmp_path)
    peak = json.loads((out / 'execution.json').read_text())['max_reserved_bytes']
    carried = []

    def cards(ctx):
        carried.append(ctx.role)
        return [], (peak // 2 if ctx.role == 'reference' else 0)

    monkeypatch.setattr(ledger_maps, '_cards', cards)
    code, out = maps_direct(tmp_path, 'm2', max_operation_bytes=peak + peak // 4)
    assert code == 2 and carried == ['reference']
    error = json.loads((out / 'error.json').read_text())
    assert error['code'] == 'oversized_real_operation' and 'compute_areas holding (target)' in error['detail']
    assert not (out / 'ledger_maps.json').exists()


def test_stage_one_files_are_not_written_by_ledger_maps(tmp_path):
    chain(tmp_path)
    before = {p.name: sha(p) for folder in ('out', 'led') for p in (tmp_path / folder).iterdir()
              if p.name != 'execution.json'}
    code, _ = run_maps(tmp_path)
    assert code == 0
    after = {p.name: sha(p) for folder in ('out', 'led') for p in (tmp_path / folder).iterdir()
             if p.name != 'execution.json'}
    assert before == after


def test_dense_grid_and_item_tiles_follow_the_hand_calculation(tmp_path):
    ref = ([('lateral_short', 'A', 'L1', 'B', 'L1', {1: {0, 1, 2}, 3: {0}})], [('AB', 'A', 'B', 'line')])
    tgt = ([('lateral_short', 'A', 'L1', 'B', 'L1', {1: {0, 1, 2, 3, 4}, 3: {0, 1}})], [('AB', 'A', 'B', 'line')])
    chain(tmp_path, ref=ref, tgt=tgt)
    code, out = run_maps(tmp_path)
    assert code == 0
    doc = load_maps(out)
    led = json.loads((tmp_path / 'led' / 'ledger.json').read_text())
    (ab,) = [item for pair in led['pairs'] for item in pair['items']]
    for role, key in (('reference', 'a'), ('target', 'b')):
        row = doc['sides'][role]['items'][0]
        assert row['value'] == ab[key] and row['tile_sum'] == pytest.approx(ab[key], rel=1e-12)
        assert doc['checks']['kind_sum'][role]['line']['sum'] == pytest.approx(ab[key], rel=1e-12)
        assert doc['checks']['kind_sum'][role]['pixel']['sum'] == 0.0
    assert doc['sides']['target']['items'][0]['membership_tiles'] == 1


# ---- lmap/T8: budgeted merges (C1), reservations of the card step (R2:2), R2:6 ------------------------------------------

def _chunks(n, overlap=0):
    """Two chunks of an item: n tile ids each (the second starts `overlap` tiles before the end of the first)."""
    first = np.arange(n, dtype=np.int32)
    second = np.arange(n - overlap, 2 * n - overlap, dtype=np.int32)
    return ([first, second], [np.ones(n), np.full(n, 2.0)], [first[:1000].copy(), second[:1000].copy()])


def test_merge_of_two_large_chunks_is_refused_before_anything_is_allocated(monkeypatch):
    """C1: the budget is original + merged result (the old code reserved only the size of the concatenated copy, so the
    np.unique / inverse / sort temporaries ran unreserved)."""
    n = 1000000
    chunks = _chunks(n)
    held = sum(a.nbytes for part in chunks for a in part)
    merged = 2 * n * 12 + 2000 * 4
    budget = ledger_maps._Budget(held + merged)
    budget.reserve(held, 'chunks')

    def boom(*args, **kwargs):
        raise AssertionError('allocated before the reservation')
    monkeypatch.setattr(ledger_maps.np, 'concatenate', boom)
    monkeypatch.setattr(ledger_maps.np, 'unique', boom)
    with pytest.raises(NumericContractError) as error:
        ledger_maps._merge_sparse(chunks, budget, 'big')
    assert error.value.code == 'oversized_real_operation' and 'merge copy big' in str(error.value)
    assert budget.used == held                                    # nothing stays reserved after the refusal


def test_merge_reservation_covers_the_real_peak_and_the_merge_is_exact():
    import tracemalloc
    n = 300000
    chunks = _chunks(n, overlap=n // 3)                            # overlapping tiles must be summed
    held = sum(a.nbytes for part in chunks for a in part)
    budget = ledger_maps._Budget(10 ** 10)
    budget.reserve(held, 'chunks')
    tracemalloc.start()
    try:
        tiles, values, member = ledger_maps._merge_sparse(chunks, budget, 'x')
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert budget.peak - held >= peak                              # reserved >= what the merge really held at once
    assert budget.peak - held >= ledger_maps.MERGE_BYTES_PER_ENTRY * 2 * n
    assert len(tiles) == 2 * n - n // 3 and tiles[0] == 0 and tiles[-1] == 2 * n - n // 3 - 1
    overlap = values[(tiles >= n - n // 3) & (tiles < n)]
    assert overlap.tolist() == [3.0] * (n // 3) and values[0] == 1.0 and values[-1] == 2.0
    assert member.tolist() == sorted(set(chunks[2][0].tolist()) | set(chunks[2][1].tolist()))
    assert budget.used == held + tiles.nbytes + values.nbytes + member.nbytes      # only the result stays reserved


def test_fsum_of_an_array_is_the_same_exact_value_without_a_list_of_the_whole_array(monkeypatch):
    rng = np.random.RandomState(3)
    array = rng.uniform(-1, 1, size=5003) * 10.0 ** rng.randint(-9, 9, size=5003)
    monkeypatch.setattr(ledger_maps, 'FSUM_CHUNK', 7)
    assert ledger_maps._fsum_array(array) == math.fsum(array.tolist())
    assert ledger_maps._fsum_array(np.zeros(0)) == 0.0


def test_card_step_arrays_are_reserved_before_they_are_built(tmp_path, monkeypatch):
    """R2:2: the component labelling (window picture + run arrays) and the window ids / observed-area arrays are reserved,
    so a limit that cannot hold them stops the run with exit 2 before they exist."""
    monkeypatch.setattr(ledger_maps, 'OCCURRENCE_ENABLED', False)     # rnarr/T6: exact-peak limit, see the test above
    chain(tmp_path)
    code, out = maps_direct(tmp_path)
    limit = json.loads((out / 'execution.json').read_text())['max_reserved_bytes']
    reserved = []
    real_reserve = ledger_maps._Budget.reserve
    monkeypatch.setattr(ledger_maps._Budget, 'reserve',
                        lambda self, nbytes, what, **kw: (reserved.append((what, nbytes)),
                                                          real_reserve(self, nbytes, what, **kw))[1])
    code, out = maps_direct(tmp_path, 'm2', max_operation_bytes=limit)
    assert code == 0
    labelling = [n for what, n in reserved if what.startswith('component labelling')]
    values = [n for what, n in reserved if what.startswith('card values')]
    assert labelling and values and all(n > 0 for n in labelling + values)
    for name in ('COMPONENT_BYTES_PER_MEMBER', 'IDS_UNIQUE_BYTES_PER_ID'):
        monkeypatch.undo()
        monkeypatch.setattr(ledger_maps, name, 10 ** 9)
        code, out = maps_direct(tmp_path, 'm3' + name[:3], max_operation_bytes=limit)
        # mrep/T3 (E3): a card step reservation that does not fit leaves that card out (exit 3) instead of ending the run
        # with exit 2; maps and values stay
        doc = load_maps(out)
        assert code == 3 and doc['status'] == 'partial' and doc['cards_dropped_for_budget']
        assert 'cards_budget_reduced' in doc['reasons'] and not (out / 'error.json').exists()
        word = 'component labelling' if name.startswith('COMP') else 'card values'
        assert all(word in row['detail'] and row['reason'] == 'card_reserve_failed'
                   for row in doc['cards_dropped_for_budget'])
        # a card is either finished or dropped, never both (cards with nothing to label still fit)
        left = {(c['kind'], c['pair_id']) for c in doc['cards']}
        assert not left & {(d['kind'], d['pair_id']) for d in doc['cards_dropped_for_budget']}
        assert (out / 'map_reference_001_it.png').is_file()


def test_without_the_resource_estimate_the_run_goes_on_and_warns(tmp_path, monkeypatch):
    """R2:6(b): no `resource_estimate` in the dataset provenance = nothing reserved for compute_areas, a warning says so."""
    from simcompare import pipeline
    chain(tmp_path)
    real = pipeline._configured_real

    def stripped(*args, **kwargs):
        result = real(*args, **kwargs)
        if result is not None:
            result[1].provenance.pop('resource_estimate', None)
        return result
    monkeypatch.setattr(pipeline, '_configured_real', stripped)
    code, out = run_maps(tmp_path)
    doc = load_maps(out)
    assert code == 0
    assert [w for w in doc['warnings'] if w['code'] == 'maps_resource_estimate_missing'] == [
        {'code': 'maps_resource_estimate_missing', 'role': 'reference'},
        {'code': 'maps_resource_estimate_missing', 'role': 'target'}]
    monkeypatch.undo()
    code, out = run_maps(tmp_path, 'm3')
    assert not [w for w in load_maps(out)['warnings'] if w['code'] == 'maps_resource_estimate_missing']


def test_a_pair_with_an_item_on_one_product_only_is_matched_on_the_items_both_products_have():
    """mrep/T3 (replaces R2:6(c) `items_not_on_both_sides`): the pair exists on both products but one of its items exists on
    one of them. The pair is matched through its common items, `comparable` (every item on both) stays False; no common
    item at all = `no_common_item`; a pair that is not a matchable change kind keeps its change kind as the reason."""
    pair = {'kind': 'line', 'pair_id': 'AB', 'a': 1.0, 'b': 2.0, 'delta': 1.0, 'change_kind': 'same_pair_change',
            'present_a': True, 'present_b': True,
            'items': [{'item_id': 'i1', 'presence': 'both'}, {'item_id': 'i2', 'presence': 'new'}]}
    (plan,) = ledger_maps._select_cards({'pairs': [pair]}, 5)
    assert plan['match'] == 'matched' and plan['reason'] is None and not plan['comparable']
    assert plan['common_ids'] == ['i1']
    pair['items'] = [{'item_id': 'i1', 'presence': 'both'}]
    assert ledger_maps._select_cards({'pairs': [pair]}, 5)[0]['comparable']
    pair['change_kind'] = 'layer_change'
    assert ledger_maps._select_cards({'pairs': [pair]}, 5)[0]['match'] == 'matched'
    pair['items'] = [{'item_id': 'i1', 'presence': 'gone'}, {'item_id': 'i2', 'presence': 'new'}]
    plan = ledger_maps._select_cards({'pairs': [pair]}, 5)[0]
    assert plan['match'] == 'not_compared' and plan['reason'] == 'no_common_item'
    pair['change_kind'] = 'electrode_added_removed'
    plan = ledger_maps._select_cards({'pairs': [pair]}, 5)[0]
    assert plan['match'] == 'not_compared' and plan['reason'] == 'electrode_added_removed'
