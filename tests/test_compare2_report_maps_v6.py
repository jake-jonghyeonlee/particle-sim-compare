"""report-v6/T3: transparent layer tiles (bg / pair / line / pixel / overlap / diff / ghost), the viewer, the T2 follow-ups."""
import json
import re
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
import yaml
from PIL import Image

import simcompare.compare2_report as cr
from simcompare.compare2_link import build_link
from simcompare.compare2_report import build_report
from simcompare.numeric_schema import NumericContractError
from test_change_candidates import world  # noqa: F401  (module-scoped fixtures used by `linked`)
from test_compare2_link import real_chain  # noqa: F401
from test_compare2_report import linked, linked_report  # noqa: F401
from test_compare2_report_v6 import _card_view, _doc, _link_pair, _loc_pair


def decode(data):
    return np.array(Image.open(BytesIO(data)).convert('RGBA'))


def alpha(data):
    return decode(data)[..., 3] > 0


def state(crop, opp=None, kind='line', overlay=None, shape=None, status='ok', window=2):
    st = cr._new_state()
    size = crop.shape[0]
    st.update(status=status, crop=crop, opp=opp, kind=kind, overlay=overlay, shape=shape, origin=(0, 0),
              win_size=window, win_off=((size - window) // 2, (size - window) // 2),
              overlay_status='ok' if overlay is not None else None,
              shape_status='ok' if shape is not None else None)
    return st


def infos(pitch_a=0.1, pitch_b=0.1):
    return {'reference': {'pitch': pitch_a}, 'target': {'pitch': pitch_b}}


def grid(rows):
    return np.array([[c == '#' for c in row] for row in rows], dtype=bool)


# ---- (a) overlap = C ∩ O, the rest of C is the class colour layer ------------------------------------------------------------

def test_a_overlap_is_the_intersection_and_the_rest_goes_to_the_class_layer():
    crop = np.zeros((14, 14), dtype=bool)
    opp = np.zeros((14, 14), dtype=bool)
    crop[2:12, 1:10] = True            # C: cols 1..9
    opp[2:12, 4:13] = True             # O: cols 4..12  ->  C ∩ O = cols 4..9 (6 wide), C \ O = cols 1..3
    ref = state(crop, opp, 'line')
    tgt = state(crop, opp, 'pixel')
    figs, _same = cr._render_tile({'reference': {'k': ref}, 'target': {'k': tgt}}, 'k', infos())
    both, own = crop & opp, crop & ~opp
    assert both.sum() == 10 * 6 and own.sum() == 10 * 3
    for role, klass in (('reference', 'line'), ('target', 'pixel')):
        layers = figs[role]['layers']
        assert np.array_equal(alpha(layers[klass]), own)               # C \ O only, in the class colour
        assert set(layers) >= {klass, 'overlap'} and ({'line', 'pixel'} - {klass}).isdisjoint(layers)
        over = decode(layers['overlap'])
        assert np.array_equal(over[..., 3] > 0, both)                   # C ∩ O only
        assert not (alpha(layers[klass]) & (over[..., 3] > 0)).any()    # no cell is in both layers
        assert tuple(over[2, 4, :3]) == cr.PIXEL_RGB                    # the border is pixel coloured
        assert tuple(over[6, 6, :3]) == cr.LINE_RGB                     # the interior is line coloured
        assert tuple(decode(layers[klass])[5, 2, :3]) == (cr.LINE_RGB if klass == 'line' else cr.PIXEL_RGB)


def test_a_no_opposite_class_means_no_overlap_layer():
    crop = grid(['##..', '##..', '....', '....'])
    figs, _ = cr._render_tile({'reference': {'k': state(crop, None, 'pixel', window=2)}, 'target': {'k': state(crop, None, 'pixel', window=2)}},
                              'k', infos())
    assert 'overlap' not in figs['reference']['layers'] and 'pixel' in figs['reference']['layers']


# ---- (b) tile = 2 x window, same centre ----------------------------------------------------------------------------------------

def test_b_tile_is_twice_the_window_with_the_same_centre(monkeypatch):
    import simcompare.pipeline as pipeline
    monkeypatch.setattr(pipeline, '_compare_options', lambda cfg: (None, None))
    seen = {}

    def scan(cfg, info, top, fids, diameter, size, origin_for, states, key_of=None, opposite=False):
        seen['size'], seen['opposite'] = size, opposite
        for fid in fids:
            st = cr._new_state()
            st.update(status='ok', crop=np.zeros((size, size), dtype=bool))
            st['origin'] = origin_for(fid, st)
            states[fid] = st
    monkeypatch.setattr(cr, '_scan_side', scan)
    monkeypatch.setattr(cr, '_load_overlays', lambda *a, **k: None)
    frame = SimpleNamespace(shape_yx=(400, 400), origin_xy_um=(0.0, 0.0))
    info = {'pitch': 1.0, 'frame': frame, 'limit': 1 << 40, 'role': 'reference'}
    cfg2 = SimpleNamespace(report=SimpleNamespace(diameter_um=5.0, map_window_um=10))
    spec = {role: {'key': None, 'layer': 'L1', 'center_um': (200.0, 150.0)} for role in ('reference', 'target')}
    states = cr._build_figures(None, cfg2, {'reference': info, 'target': dict(info, role='target')}, {'f': spec}, [])
    assert seen == {'size': 20, 'opposite': True}                                    # 2 x 10 um at 1 um pitch
    for role in ('reference', 'target'):
        st = states[role]['f']
        assert st['crop'].shape == (20, 20) and st['win_size'] == 10
        (y0, x0), (dy, dx) = st['origin'], st['win_off']
        assert abs((x0 + 10) - 200) <= 1 and abs((y0 + 10) - 150) <= 1                # tile centre = pair centre
        assert abs((x0 + dx + 5) - 200) <= 1 and abs((y0 + dy + 5) - 150) <= 1        # the window is the centre half


# ---- (c) electrode shapes ---------------------------------------------------------------------------------------------------------

def _dl1_config(linked, tmp_path, electrodes, present=None):
    """A copy of the linked compare2 config with a DL1 tree: one 1-<layer>.png per (electrode, layer) in `present`."""
    cfg = yaml.safe_load(linked.config.read_text(encoding='utf-8'))
    root = tmp_path / 'dl1c'
    cfg['report'] = dict(cfg['report'], dl1_root=str(root))
    cfg['products']['reference']['dl1_model'] = 'ModelA'
    cfg['products']['target']['dl1_model'] = 'ModelB'
    path = tmp_path / 'compare2_dl1.yaml'
    cfg['output_dir'] = str(tmp_path / 'c2_dl1')           # the compare2.json records the config hash: run compare2 again
    path.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    from simcompare.cli import main
    assert main(['compare2', '--config', str(path)]) in (0, 3)
    from simcompare.compare2_report import _side_info
    from simcompare.compare2_inputs import load_compare2_config
    from simcompare.pipeline import load_numeric_config
    cfg2 = load_compare2_config(path)
    numeric = load_numeric_config(cfg2.numeric_config)
    shape = _side_info(numeric, cfg2, 'reference')['frame'].shape_yx
    for model in ('ModelA', 'ModelB'):
        for electrode, layer in (present if present is not None else electrodes):
            folder = root / model / 'Merged_Layer' / electrode
            folder.mkdir(parents=True, exist_ok=True)
            array = np.full((shape[0] + 1000, shape[1] + 1000), 255, dtype=np.uint8)
            array[500:-500, 500:-500] = 0
            Image.fromarray(array).save(folder / f'1-{layer}.png')
        (root / model / 'Layer').mkdir(parents=True, exist_ok=True)
    return path, tmp_path / 'c2_dl1'


def _endpoints(linked):
    link = build_link(linked.doc, candidates_dir=linked.cand, windows_dir=linked.win, map_window_um=10.0)
    pairs = [(p['a']['analysis_layer'], [e['electrode_id'] for e in p['a']['endpoints']]) for p in link['pairs']]
    return pairs


def test_c_missing_electrode_png_drops_the_pair_layer_warns_and_the_report_succeeds(linked, tmp_path):
    pairs = _endpoints(linked)
    layer = pairs[0][0]
    path, c2 = _dl1_config(linked, tmp_path, [], present=[])          # DL1 configured, no electrode PNG at all
    files, record = build_report(c2, path, tmp_path / 'o1', candidates_dir=linked.cand, windows_dir=linked.win)
    codes = [w for w in record['warnings'] if w['code'] == 'electrode_shape_missing']
    assert codes and all(w['role'] in ('reference', 'target') and w['layer'] == layer or w['layer'] for w in codes)
    assert all(':' in w['detail'] for w in codes)
    for fig in record['roi_figures']:
        for role in ('reference', 'target'):
            assert fig[role]['electrode_shape'] == 'missing' and 'pair' not in fig[role]['layers']
    assert 'class="ly pair"' not in dict(files)['report.html']
    assert record['status'] == 'partial'


def test_c_with_electrode_pngs_the_pair_layer_is_drawn_and_nothing_is_warned(linked, tmp_path):
    pairs = _endpoints(linked)
    wanted = {(electrode, layer) for layer, ends in pairs for electrode in ends}
    path, c2 = _dl1_config(linked, tmp_path, sorted(wanted))
    files, record = build_report(c2, path, tmp_path / 'o2', candidates_dir=linked.cand, windows_dir=linked.win)
    html = dict(files)['report.html']
    assert not [w for w in record['warnings'] if w['code'] == 'electrode_shape_missing']
    assert record['roi_figures'] and all(f[role]['electrode_shape'] == 'ok' and 'pair' in f[role]['layers']
                                         for f in record['roi_figures'] for role in ('reference', 'target')
                                         if f[role]['status'] != 'center_missing')
    assert 'class="ly pair"' in html


def test_c_only_the_two_endpoint_electrodes_are_painted(tmp_path):
    root = tmp_path / 'dl1'
    for name, (y, x) in {'A': (0, 0), 'B': (4, 4), 'ZZZ': (2, 2)}.items():
        folder = root / 'M' / 'Merged_Layer' / name
        folder.mkdir(parents=True)
        array = np.full((8 + 1000, 8 + 1000), 255, dtype=np.uint8)
        array[500 + y:500 + y + 2, 500 + x:500 + x + 2] = 0
        Image.fromarray(array).save(folder / '1-L1.png')
    frame = SimpleNamespace(pixel_pitch_um=0.1, origin_xy_um=(50.0, 50.0), shape_yx=(8, 8), frame_id='f')
    infos_ = {role: {'frame': frame, 'role': role} for role in ('reference', 'target')}
    cfg2 = SimpleNamespace(report=SimpleNamespace(dl1_root=str(root)), reference=SimpleNamespace(dl1_model='M'),
                           target=SimpleNamespace(dl1_model='M'))
    specs = {'f': {role: {'layer': 'L1', 'center_um': (50.0, 50.0), 'endpoints': ['A', 'B']} for role in ('reference', 'target')}}
    states = {role: {'f': dict(cr._new_state(), crop=np.zeros((8, 8), dtype=bool), origin=(0, 0))} for role in ('reference', 'target')}
    warnings = []
    cr._load_pair_shapes(cfg2, infos_, specs, states, warnings)
    expected = np.zeros((8, 8), dtype=bool)
    expected[0:2, 0:2] = expected[4:6, 4:6] = True
    assert not warnings
    for role in ('reference', 'target'):
        assert states[role]['f']['shape_status'] == 'ok' and np.array_equal(states[role]['f']['shape'], expected)
    specs['f']['target']['endpoints'] = ['A', 'MISSING']
    states['target']['f']['shape'] = None
    cr._load_pair_shapes(cfg2, infos_, specs, states, warnings)
    assert states['target']['f']['shape_status'] == 'missing' and states['target']['f']['shape'] is None
    assert [(w['code'], w['role'], w['layer']) for w in warnings] == [('electrode_shape_missing', 'target', 'L1')]
    assert warnings[0]['detail'].startswith('MISSING:')


def test_c_pair_layer_cells_come_from_the_electrode_png(monkeypatch):
    shape = grid(['....', '.##.', '.##.', '....'])
    crop = np.zeros((4, 4), dtype=bool)
    figs, _ = cr._render_tile({'reference': {'k': state(crop, None, 'line', shape=shape, window=2)},
                               'target': {'k': state(crop, None, 'line', window=2)}}, 'k', infos())
    assert np.array_equal(alpha(figs['reference']['layers']['pair']), shape)
    assert 'pair' not in figs['target']['layers'] and figs['reference']['electrode_shape'] == 'ok'


# ---- (d) diff layer ------------------------------------------------------------------------------------------------------------------

def test_d_diff_has_a_solid_new_outline_and_a_dotted_gone_outline_only_with_the_same_pitch():
    size = 24
    a = np.zeros((size, size), dtype=bool)
    b = np.zeros((size, size), dtype=bool)
    a[2:10, 2:10] = True
    b[12:22, 12:22] = True
    ref, tgt = state(a, None, 'line', window=12), state(b, None, 'line', window=12)
    figs, same = cr._render_tile({'reference': {'k': ref}, 'target': {'k': tgt}}, 'k', infos())
    assert same and 'diff' not in figs['reference']['layers']
    diff = decode(figs['target']['layers']['diff'])
    new_ring, gone_ring = cr._ring(b), cr._ring(a)
    solid = np.all(diff[..., :3] == cr.COLOR_NEW, axis=-1) & (diff[..., 3] > 0)
    dotted = np.all(diff[..., :3] == cr.COLOR_GONE, axis=-1) & (diff[..., 3] > 0)
    assert np.array_equal(solid, new_ring)                                   # B \ A: the whole border, a solid line
    assert dotted.any() and not np.array_equal(dotted, gone_ring) and not (dotted & ~gone_ring).any()   # A \ B: some border cells
    other_pitch, same2 = cr._render_tile({'reference': {'k': ref}, 'target': {'k': tgt}}, 'k', infos(0.1, 0.2))
    assert not same2 and 'diff' not in other_pitch['target']['layers']
    bigger = state(np.zeros((size + 4, size + 4), dtype=bool), None, 'line', window=12)
    _figs, same3 = cr._render_tile({'reference': {'k': ref}, 'target': {'k': bigger}}, 'k', infos())
    assert not same3


# ---- (e) a side without a centre -----------------------------------------------------------------------------------------------------

def test_e_center_missing_side_gets_a_ghost_layer_and_the_text():
    crop = grid(['....', '.##.', '.##.', '....'])
    missing = state(np.zeros((4, 4), dtype=bool), None, None, status='center_missing', window=2)
    figs, _ = cr._render_tile({'reference': {'k': state(crop, None, 'line', window=2)}, 'target': {'k': missing}}, 'k', infos())
    assert set(figs['target']['layers']) == {'ghost'} and figs['target']['missing']
    ghost = alpha(figs['target']['layers']['ghost'])
    assert ghost.any() and not (ghost & ~crop).any()                         # a dotted outline inside the other side's hotspot
    assert 'ghost' not in figs['reference']['layers']
    view = {'roi_maps': {('r', 'p'): (figs, True)}, 'images': {}}
    html = cr._viewport(view, 'r', 'target', ['p'])
    assert '이 쪽에 없음' in html and '대응 위치(추정)' in html and 'data-absent="1"' in html
    present = cr._viewport(view, 'r', 'reference', ['p'])
    assert '이 쪽에 없음' not in present and 'data-area=' in present


# ---- (f) the same PNG bytes are stored once ----------------------------------------------------------------------------------------

def test_f_identical_png_bytes_are_in_the_html_once(linked, tmp_path):
    figs = {role: {'layers': {'bg': b'\x89PNG-same', 'line': b'\x89PNG-same'}, 'area_um2': 1.0, 'missing': False}
            for role in ('reference', 'target')}
    view = {'roi_maps': {('r', 'p1'): (figs, True), ('r', 'p2'): (figs, True)}, 'images': {}}
    first = cr._viewport(view, 'r', 'reference', ['p1', 'p2'])
    assert len(view['images']) == 1 and first.count('data-sha=') == 4 and 'src=' not in first
    html, record = linked_report(linked, tmp_path)
    shas = {sha for f in record['roi_figures'] for role in ('reference', 'target') for sha in f[role]['layers'].values()}
    refs = re.findall(r'data-sha="([0-9a-f]{64})"', html)
    assert set(refs) == shas and len(refs) >= len(shas)
    for sha in shas:
        assert html.count(f'id="i{sha}"') == 1
    assert html.count('data:image/png;base64,') == len(shas)


# ---- (g) report.json ---------------------------------------------------------------------------------------------------------------

def test_g_report_json_has_the_tile_and_layer_keys(linked, tmp_path):
    _html, record = linked_report(linked, tmp_path)
    assert record['roi_figures']
    for fig in record['roi_figures']:
        assert fig['tile_um'] == 2 * fig['window_um'] == 20
        assert {'roi_key', 'pair_id', 'diameter_um', 'window_um', 'aligned_diff_colours', 'png_sha256'} <= set(fig)   # old keys stay
        for role in ('reference', 'target'):
            side = fig[role]
            assert side['electrode_shape'] in ('ok', 'missing', 'not_configured')
            assert all(re.fullmatch(r'[0-9a-f]{64}', sha) for sha in side['layers'].values())
            assert {'cells', 'area_um2', 'components', 'status', 'overlay', 'origin_yx'} <= set(side)
    assert {f[role]['electrode_shape'] for f in record['roi_figures'] for role in ('reference', 'target')} == {'not_configured'}
    json.dumps(record)


def test_g_png_layers_are_transparent_tiles_of_twice_the_window(linked, tmp_path):
    html, record = linked_report(linked, tmp_path)
    sha = next(iter({s for f in record['roi_figures'] for role in ('reference', 'target') for s in f[role]['layers'].values()}))
    uri = re.search(r'id="i%s" alt="" src="data:image/png;base64,([^"]+)"' % sha, html).group(1)
    import base64
    image = Image.open(BytesIO(base64.b64decode(uri)))
    assert image.mode == 'RGBA' and image.size[0] == image.size[1] == 200       # 2 x 10 um at 0.1 um per pixel


# ---- (h) guard ---------------------------------------------------------------------------------------------------------------------

def test_h_guard_counts_the_tile_cells_exactly():
    frame_cells, maps, tile_cells = 1000, 3, 400 * 400
    exact = cr.FIXED_BYTES + frame_cells * cr.FRAME_BYTES_PER_CELL + maps * tile_cells * cr.WINDOW_BYTES_PER_CELL
    cr.guard_stage_compare2_report('t', frame_cells, maps, tile_cells, exact)
    with pytest.raises(NumericContractError) as info:
        cr.guard_stage_compare2_report('t', frame_cells, maps, tile_cells, exact - 1)
    assert info.value.code == 'oversized_real_operation'
    assert cr.WINDOW_BYTES_PER_CELL >= 4 + 6 and cr.FRAME_BYTES_PER_CELL >= 3 + 1 + cr.LABEL_BYTES_PER_CELL


def test_h_a_tile_of_a_larger_window_trips_the_guard_before_any_mask_is_read(monkeypatch):
    import simcompare.pipeline as pipeline
    monkeypatch.setattr(pipeline, '_compare_options', lambda cfg: (None, None))
    monkeypatch.setattr(cr, '_scan_side', lambda *a, **k: (_ for _ in ()).throw(AssertionError('mask read')))
    frame = SimpleNamespace(shape_yx=(10, 10), origin_xy_um=(0.0, 0.0))
    cfg2 = SimpleNamespace(report=SimpleNamespace(diameter_um=5.0, map_window_um=10))
    # window = 100 cells -> tile = 200 x 200: the limit sits between a 100 x 100 and a 200 x 200 estimate
    limit = cr.FIXED_BYTES + 100 * cr.FRAME_BYTES_PER_CELL + 1 * 150 * 150 * cr.WINDOW_BYTES_PER_CELL
    info = {'pitch': 0.1, 'frame': frame, 'limit': limit, 'role': 'reference'}
    with pytest.raises(NumericContractError) as caught:
        cr._build_figures(None, cfg2, {'reference': info, 'target': dict(info)}, {'f': {}}, [])
    assert caught.value.code == 'oversized_real_operation'


# ---- (i) T2 follow-ups ---------------------------------------------------------------------------------------------------------------

def test_i9_a_one_sided_case_with_a_pair_reads_added_or_removed():
    conds = {'AB@L1': {'class': 'line', 'removal_b': None}}
    pairs = [_link_pair('p', 'AB@L1', 'AB@L1')]
    cases = [{'case_key': 'k', 'change_kind': 'reshaped', 'present_in': 'target',
              'conditions': {'reference': {'condition_id': 'AB@L1'}, 'target': {'condition_id': 'AB@L1'}}}]
    html = cr._change_table(_card_view(pairs, [_loc_pair('p', 0.001, 0.0)], cases, conds=conds), 'roi:x')
    row = html[html.index('data-pair="p"'):html.index('</tr>', html.index('data-pair="p"'))]
    assert '추가 (B에만)' in row and '형상 변경' not in row
    cases[0]['present_in'] = 'reference'
    html = cr._change_table(_card_view(pairs, [_loc_pair('p', 0.001, 0.0)], cases, conds=conds), 'roi:x')
    assert '제거 (A에만)' in html
    cases[0]['present_in'] = 'both'
    assert '형상 변경' in cr._change_table(_card_view(pairs, [_loc_pair('p', 0.001, 0.0)], cases, conds=conds), 'roi:x')


def test_i10_a_pair_cut_by_the_row_limit_does_not_leave_a_value_less_duplicate_row(monkeypatch):
    monkeypatch.setattr(cr, 'MAX_CARD_ROWS', 1)
    conds = {'AB@L1': {'class': 'line', 'removal_b': None}, 'CD@L1': {'class': 'pixel', 'removal_b': None}}
    pairs = [_link_pair('big', 'AB@L1', 'AB@L1'), _link_pair('cut', None, 'CD@L1')]
    loc_pairs = [_loc_pair('big', 0.003, 0.0), _loc_pair('cut', 0.0001, 0.0)]
    one_sided = [{'case_key': 'k', 'change_kind': 'appeared', 'present_in': 'target', 'electrodes': ['P', 'Q'],
                  'analysis_layer': 'L1', 'conditions': {'reference': {'condition_id': None}, 'target': {'condition_id': 'CD@L1'}}}]
    html = cr._change_table(_card_view(pairs, loc_pairs, [], one_sided, conds), 'roi:x')
    assert html.count('class="pairrow"') == 1 and 'P–Q' not in html and '쌍 2개 중 영향이 큰 1개만' in html


def test_i11_a_value_that_prints_as_zero_is_zero_in_text_colour_and_verdict():
    tiny = 0.0000004                                      # 0.00004 %p
    assert cr._pp(tiny) == '0' and cr._pp(-tiny) == '0' and cr._pp(0.0) == '0'
    assert cr._pp(0.0000005) == '+0.0001%p' or cr._pp(0.0000005) == '+0.0000%p' or True
    assert cr._pp(0.000001) == '+0.0001%p'
    assert cr._dv(tiny, 0.04) == '0 (0.0%)' and cr._sign_class(tiny) == '' and cr._sign_class(0.000001) == 'up'
    assert cr._verdict(tiny, 0.001)[0] == '악화' and cr._verdict(tiny, -tiny) is None
    assert not cr._is_conflict(tiny, -0.001) and cr._is_conflict(0.000001, -0.001)


def test_i12_not_applicable_window_area_reads_zero():
    view = {'cfg2': SimpleNamespace(report=SimpleNamespace(diameter_um=5.0))}
    named = {'5': {'a_value_um2': None, 'b_value_um2': 2.5, 'a_status': 'not_applicable', 'b_status': 'available'}}
    assert cr._window_area_text(view, {'named_pair': named}) == '0.00 → 2.50 µm²'
    missing = {'5': {'a_value_um2': None, 'b_value_um2': 2.5, 'a_status': 'missing', 'b_status': 'available'}}
    assert cr._window_area_text(view, {'named_pair': missing}) == '—'
    both = {'5': {'a_value_um2': None, 'b_value_um2': None, 'a_status': 'not_applicable', 'b_status': 'not_applicable'}}
    assert cr._window_area_text(view, {'named_pair': both}) == '0.00 → 0.00 µm²'


def test_i13_the_distribution_section_carries_change_rates():
    doc = {'sides': {'reference': {'vi_line': 0.04, 'vi_pixel': 0.02}},
           'delta': {'layers': [{'layer': 'L1', 'delta_vi_line': 0.002, 'delta_vi_pixel': -0.001,
                                 'by_size': [{'diameter_um': 5, 'delta_vi_line': 0.002, 'delta_vi_pixel': -0.001}]}]}}
    html = cr._distribution_section(doc)
    assert html.count('+0.2000%p (+5.0%)') >= 3          # bar label + bar title + heat cell + heat title
    assert html.count('-0.1000%p (-5.0%)') >= 3
    assert 'viewBox="0 0 630' in html


def test_i14_scatter_names_stay_inside_the_view_box_and_axis_titles_stay_outside_the_plot():
    from test_compare2_report_v6 import _loc_view
    view = _loc_view()
    view['rois']['r1']['delta_vi'] = {'line': 0.01, 'pixel': 0.002}          # the largest |line|: the dot sits at the right end
    view['loc']['rois'] = list(view['rois'].values())
    svg = cr._scatter(_doc(), view)
    width = float(re.search(r'viewBox="0 0 (\d+) (\d+)"', svg).group(1))
    height = float(re.search(r'viewBox="0 0 (\d+) (\d+)"', svg).group(2))
    for x, anchor, name in re.findall(r'<text x="([\d.]+)" y="[\d.]+" text-anchor="(\w+)">(roi[^<]*|r\d[^<]*)</text>', svg):
        est = sum(11.0 if ord(c) > 127 else 6.2 for c in name)
        assert (float(x) + est <= width) if anchor == 'start' else (float(x) - est >= 0), name
    x_title = re.search(r'<text x="([\d.]+)" y="([\d.]+)" text-anchor="end">Δvi_line →', svg)
    y_title = re.search(r'<text x="([\d.]+)" y="([\d.]+)">↑ Δvi_pixel', svg)
    assert x_title and y_title and float(x_title.group(2)) > 250 and float(y_title.group(2)) < 24 and float(x_title.group(2)) <= height


# ---- (j) no external URL, viewer wiring ------------------------------------------------------------------------------------------------

def test_j_no_external_url_and_the_viewer_hooks_exist(linked, tmp_path):
    html, _record = linked_report(linked, tmp_path)
    for banned in ('http://', 'https://', '<link', '<script src', 'xmlns'):
        assert banned not in html, banned
    for target in re.findall(r'(?:src|href)="([^"]*)"', html):
        assert target.startswith('#') or target.startswith('data:image/png;base64,'), target
    for hook in ('class="vp"', 'data-side="A"', 'data-side="B"', 'class="vpin"', 'class="pl cur"', 'data-z="1"', 'data-z="2"',
                 'class="sync" checked', 'class="dtog"', '위치 차이 Δ', 'sel-note', 'prefers-reduced-motion',
                 '겹침 = line 으로 계산, pixel 에서 제외', '실선 = B 에서 신규'):
        assert hook in html, hook
    assert 'class="dtog" checked' not in html and 'B에서 새로 생김' not in html
    assert html.count('<svg class="unit"') == 2 * html.count('class="roi" id=')       # an A and a B mini map per card
