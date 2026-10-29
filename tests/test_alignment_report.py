"""change-roi/T8: `alignment.html` of `candidates-change` (self-contained report of the CNT alignment and the change map).

Cases: aligned / aligned_uncertain / alignment failed (synthetic real_hotspot_v1 world of test_change_candidates), the size
steps (half resolution, then crops dropped from the lowest rank), the memory-skip path, determinism, and the helpers.
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from simcompare import alignment_report, cnt_align
from test_change_candidates import (  # noqa: F401  (world is a module-scoped fixture of the other test module)
    OFFSET, PITCH, load, queries, run_change, world)

IMG = re.compile(r'<img src="data:image/png;base64,([A-Za-z0-9+/=]+)" alt="([^"]*)"')


def read(out):
    return (Path(out)/'alignment.html').read_text(encoding='utf-8')


def captions(html):
    return [alt for _, alt in IMG.findall(html)]


def assert_self_contained(html):
    assert '<script' not in html.lower()
    assert 'http:' not in html and 'https:' not in html
    refs = re.findall(r'(?:src|href)\s*=\s*"([^"]*)"', html) + re.findall(r'url\(([^)]*)\)', html)
    assert all(r.startswith('data:image/png;base64,') or r.startswith('#') for r in refs), refs      # v3: in-page anchors (ROI list)
    assert html.startswith('<!doctype html>') and '<meta charset="utf-8">' in html


def expected_images(payload, alignment, with_cnt=True):
    n_cnt = len(set(f['layer'] for f in alignment['cnt_inputs']['reference']['files'])) if with_cnt else 0
    rois = len(queries(payload, 'reference_to_target'))
    heat_layers = {q['score_layer'] for q in queries(payload, 'reference_to_target')}
    # v3: 2a two pictures + 2b two per layer + 3 one map + 6 two heat maps per layer + per ROI (position + T3b layer bundle of 3; one layer per ROI here)
    return (2 + 2*n_cnt if n_cnt else 0) + 1 + 2*len(heat_layers) + rois*(1 + 3)


@pytest.fixture(scope='module')
def aligned(world, tmp_path_factory):
    out = tmp_path_factory.mktemp('report_aligned')/'out'
    code = run_change(world, out)
    sample = os.environ.get('ALIGNMENT_SAMPLE_DIR')
    if sample:
        Path(sample).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(out/'alignment.html', Path(sample)/'alignment.html')
    return SimpleNamespace(out=out, code=code, payload=load(out), alignment=load(out, 'alignment.json'), html=read(out))


# --- aligned ---------------------------------------------------------------------------------------------------------

def test_aligned_report_is_self_contained_and_has_the_expected_figures(aligned):
    html, payload, alignment = aligned.html, aligned.payload, aligned.alignment
    assert_self_contained(html)
    assert 'alignment.html' in load(aligned.out, 'execution.json')['outputs']
    caps = captions(html)
    rois = len(queries(payload, 'reference_to_target'))
    assert len(caps) == html.count('data:image/png;base64,') == expected_images(payload, alignment) == 11 + 4*rois
    assert sum(c.startswith('전체 CNT 층 · 정렬 ') for c in caps) == 2                    # 2a: all layers, before and after
    for name in ('CNT1', 'CNT2', 'CNT3'):          # 2b: one before and one after picture per CNT layer
        assert sum(c.startswith(f'{name} · ') for c in caps) == 2
        assert f'<h4>CNT {name}</h4>' in html
        assert sum(c == f'{name} (정렬 후)' for c in caps) == 0       # V2: no CNT crop in the ROI detail
    assert sum(c.startswith('위치') for c in caps) == rois and sum(c.startswith('변경 강조 지도') for c in caps) == 1
    assert sum(c.startswith('층 L1 · ') for c in caps) == 2                                # RU + count_map heat maps
    assert sum(c.startswith('ROI 층 L1 · ') for c in caps) == 3*rois                       # T3b: one layer bundle (compare, reference, target) per ROI
    assert sum('합집합' in c for c in caps) == 1 and all('CNT' not in c for c in caps if '합집합' in c)   # RU only
    first = IMG.search(html).group(1)
    image = Image.open(io.BytesIO(base64.b64decode(first)))
    assert image.format == 'PNG' and image.mode == 'RGB' and max(image.size) <= alignment_report.OVERVIEW_MAX_PX
    assert 'class="warn"' not in html


def test_summary_table_matches_alignment_json(aligned):
    html, a = aligned.html, aligned.alignment
    assert a['status'] == 'aligned' and 'alignment_uncertain' not in html
    from html import escape
    for key in ('offset_xy_um', 'offset_xy_px', 'dice_best', 'dice_identity', 'peak_ratio', 'pattern_period_vectors_um',
                'layers_used', 'layers_excluded', 'overlap_area_um2', 'search_um', 'pitch_um'):
        assert f'<td>{key}</td><td>{escape(json.dumps(a[key], ensure_ascii=False))}</td>' in html, key
    assert 'p_target = p_reference + offset' in html
    plateau = {'best_plateau_px': a['best_plateau_px'], 'best_plateau_extent_px': a['best_plateau_extent_px']}
    assert escape(json.dumps(plateau)) in html


def test_roi_section_lists_ranking_attributes_and_contributions(aligned):
    html, top = aligned.html, queries(aligned.payload, 'reference_to_target')[0]
    assert '순위 1' in html and top['region_id'] in html
    for key in ('score_um2', 'sum_residual_um2', 'changed_case_count', 'union_delta_um2', 'observed_fraction', 'flags'):
        assert f'<td>{key}' in html
    for case in top['contributing_cases']:
        assert case['case_key'] in html
    assert 'union_unchanged_pair_changed' in html
    assert 'union_basis: paired_and_one_sided_cases' in html and 'Δ_union' in html
    assert '합집합에 포함한 조합' in html and '<td>one_sided_included_target</td>' in html      # 2b → 2c in v3
    assert '<th>있는 모델</th>' in html and '<th>층 바뀜</th>' in html                           # Korean columns replace present_in/layer_changed


def test_payload_records_the_report_and_union_basis(aligned):
    report = aligned.payload['alignment_report']
    assert report == {'path': 'alignment.html', 'bytes': (aligned.out/'alignment.html').stat().st_size,
                      'downscaled': False, 'crops_omitted': []}
    assert aligned.payload['union_basis'] == 'paired_and_one_sided_cases'


def test_same_input_twice_gives_the_same_html(world, tmp_path, aligned):
    out = tmp_path/'again'
    assert run_change(world, out) == aligned.code
    assert hashlib.sha256((out/'alignment.html').read_bytes()).hexdigest() == \
        hashlib.sha256((aligned.out/'alignment.html').read_bytes()).hexdigest()
    assert str(out) not in read(out) and str(aligned.out) not in aligned.html


# --- uncertain and failed ---------------------------------------------------------------------------------------------

def test_uncertain_alignment_gets_a_warning_and_the_period_text(world, tmp_path, monkeypatch):
    real = cnt_align.align_products

    def ambiguous(*args, **kwargs):
        result = real(*args, **kwargs)
        return {**result, 'status': 'aligned_uncertain', 'reasons': ['peak_ambiguous']}
    monkeypatch.setattr(cnt_align, 'align_products', ambiguous)
    out = tmp_path/'out'
    assert run_change(world, out, top_k=1) == 3
    html = read(out)
    assert_self_contained(html)
    assert html.count('class="warn"') == 2                    # status warning + peak_ambiguous warning
    assert 'alignment_uncertain' in html and '반복 주기만큼 틀렸을 수 있습니다' in html and 'aligned_uncertain' in html
    alignment = load(out, 'alignment.json')
    assert len(captions(html)) == expected_images(load(out), alignment)


def test_failed_alignment_still_writes_a_report_with_a_warning_and_no_cnt_figures(world, tmp_path):
    empty = tmp_path/'no_cnt'
    empty.mkdir()
    out = tmp_path/'out'
    assert run_change(world, out, dl1=empty, top_k=1) == 3
    html, payload = read(out), load(out)
    assert_self_contained(html)
    assert 'class="warn"' in html and 'alignment_failed_identity_used' in html
    assert 'cnt_missing_reference' in html and 'CNT 층 영상이 없어' in html
    caps = captions(html)
    assert not any('정렬 전' in c or '정렬 후' in c for c in caps)
    heat = [c for c in caps if c.startswith('층 ')]
    rois = len(queries(payload, 'reference_to_target'))
    assert len(heat) == 2 and len(caps) == 1 + 2 + 4*rois     # change map + heat maps + (position + 3 bundle pictures) per ROI; no CNT pictures
    assert '<h3>2a.' in html and 'CNT 층 영상이 없어 겹침 그림이 없습니다' in html
    assert payload['alignment_report']['bytes'] == (out/'alignment.html').stat().st_size


# --- size steps and memory ---------------------------------------------------------------------------------------------

def test_cap_lowers_resolution_then_drops_crops_from_the_lowest_rank(world, tmp_path, monkeypatch):
    out = tmp_path/'full'
    assert run_change(world, out, top_k=1) == 0
    full = (out/'alignment.html').stat().st_size
    assert load(out)['alignment_report']['downscaled'] is False
    monkeypatch.setattr(alignment_report, 'MAX_HTML_BYTES', full-1)
    half = tmp_path/'half'
    assert run_change(world, half, top_k=1) == 0
    report = load(half)['alignment_report']
    assert report['downscaled'] is True and report['crops_omitted'] == [] and report['bytes'] < full
    assert len(captions(read(half))) == len(captions(read(out)))
    monkeypatch.setattr(alignment_report, 'MAX_HTML_BYTES', report['bytes']-1)
    gone = tmp_path/'gone'
    assert run_change(world, gone, top_k=1) == 0
    report = load(gone)['alignment_report']
    assert report['downscaled'] is True and report['crops_omitted'] == [1]
    html = read(gone)
    assert '그림 생략' in html and len(captions(html)) == 12       # 2a 2 + 2b 6 + map 1 + heat 2 + the position picture; no crop
    assert 'cnt_inputs' not in html and report['bytes'] == (gone/'alignment.html').stat().st_size


def synthetic_source(n_rois=3, shape=(300, 320), seed=0):
    rng = np.random.default_rng(seed)
    valid = np.ones(shape, bool)
    layers = {'CNT1': rng.random(shape) > 0.7, 'CNT2': rng.random(shape) > 0.7}
    other = {k: np.roll(v, (2, 3), axis=(0, 1)) for k, v in layers.items()}
    windows = [{'layer': 'L1', 'window_origin_px': [20 + 70*i, 30 + 60*i], 'window_px': 50} for i in range(n_rois)]
    maps = {'maps': {'L1': rng.integers(0, 4, shape).astype(np.uint32)}, 'D': valid,
            'union_change': {'L1': rng.random(shape) > 0.9}, 'union_ref': {'L1': rng.random(shape) > 0.5},
            'union_tgt': {'L1': rng.random(shape) > 0.5}}
    source = alignment_report.ImageSource(layers, other, valid, valid, (3, 2))
    source.collect(maps, windows)
    query = {'score_um2': 1.0, 'score_layer': 'L1', 'sum_residual_um2': 2.0, 'changed_case_count': 1, 'union_delta_um2': 0.5,
             'union_area_reference_um2': 3.0, 'union_area_target_um2': 3.5, 'observed_fraction': 1.0, 'flags': [],
             'cnt_change_um2_by_layer': {}, 'center_xy_um': [1.0, 2.0], 'region_id': 'chg_x',
             'contributing_cases': [{'case_key': 'k', 'change_kind': 'shifted', 'residual_um2': 1.0, 'iou_global': 0.1,
                                     'iou_local': 0.5}]}
    alignment = {'status': 'aligned', 'reasons': [], 'offset_xy_um': [0.3, 0.2], 'offset_xy_px': [3, 2], 'sign': 'p_target = p_reference + offset',
                 'dice_best': 0.9, 'dice_identity': 0.1, 'peak_ratio': 2.0, 'best_plateau_px': 1, 'best_plateau_extent_px': [0, 0],
                 'pattern_period_vectors_um': [], 'layers_used': ['CNT1', 'CNT2'], 'layers_excluded': [], 'overlap_area_um2': 1.0,
                 'search_um': 1.0, 'pitch_um': 0.1, 'alternatives': []}
    return alignment, [dict(query) for _ in range(n_rois)], windows, source


def position_source(shape=(300, 320)):
    """조밀한 CNT 패턴(절반)과 한 곳(rows 100-109, cols 200-209)에만 변경이 있는 source."""
    rng = np.random.default_rng(1)
    valid = np.ones(shape, bool)
    layers = {'CNT1': rng.random(shape) > 0.5}
    change = np.zeros(shape, bool)
    change[100:110, 200:210] = True
    maps = {'maps': {'L1': change.astype(np.uint32)}, 'D': valid, 'union_change': {'L1': change},
            'union_ref': {'L1': rng.random(shape) > 0.5}, 'union_tgt': {'L1': rng.random(shape) > 0.5}}
    source = alignment_report.ImageSource(layers, layers, valid, valid, (0, 0))
    source.collect(maps, [])
    return source


def colour_mask(rgb, colour):
    return np.all(rgb == np.array(colour, dtype=np.uint8), axis=2)


def test_position_base_ignores_dense_cnt_pattern_and_marks_only_changed_blocks():
    source = position_source()
    base, total = alignment_report._position_base(source)
    assert total == 2 and base.shape[:2] == (150, 160)
    assert not colour_mask(base, (190, 190, 190)).any()          # the old pattern grey
    changed = colour_mask(base, alignment_report.POS_CHANGED)
    assert changed.any() and not changed[:50].any() and not changed[:, :100].any() and not changed[55:].any()
    assert changed[50:55, 100:105].all()                       # frame rows 100-109, cols 200-209 → 2 px blocks 50-54 / 100-104
    assert colour_mask(base, alignment_report.POS_BG).sum() > 0.9*base.shape[0]*base.shape[1]


def test_position_rect_has_a_minimum_size_and_quadrant_lines_cross_at_the_frame_centre():
    source = position_source()
    base, total = alignment_report._position_base(source)
    row, col = (300//2)//total, (320//2)//total
    assert colour_mask(base, alignment_report.POS_LINE)[row, 5] and colour_mask(base, alignment_report.POS_LINE)[5, col]
    assert (base[row, 0:5] == alignment_report.POS_LINE).all() and (base[0:5, col] == alignment_report.POS_LINE).all()
    window = {'window_origin_px': [140, 60], 'window_px': 2}                    # 1 px after reduction
    rgb = alignment_report._position_image(base, total, window)
    rect = colour_mask(rgb, alignment_report.POS_RECT)
    ys, xs = np.nonzero(rect)
    assert ys.max() - ys.min() + 1 >= alignment_report.POS_MIN_RECT_PX and xs.max() - xs.min() + 1 >= alignment_report.POS_MIN_RECT_PX
    assert rect[ys.min(), xs.min():xs.max() + 1].all() and rect[ys.min():ys.max() + 1, xs.min()].all()
    big = alignment_report._position_image(base, total, {'window_origin_px': [20, 20], 'window_px': 100})
    assert colour_mask(big, alignment_report.POS_RECT).any()


def test_size_steps_are_ordered_on_a_synthetic_source():
    alignment, qs, windows, source = synthetic_source()
    sizes = {}
    data, info = alignment_report.build_alignment_html(alignment, queries=qs, windows=windows, source=source)
    sizes['full'] = info['bytes']
    assert info == {'path': 'alignment.html', 'bytes': len(data), 'downscaled': False, 'crops_omitted': [], 'n_images': info['n_images']}
    assert info['n_images'] == 2 + 4 + 1 + 2 + 3*4          # 2a, 2b (2 layers), map, heat, per ROI (position + one layer bundle of 3)
    data, info = alignment_report.build_alignment_html(alignment, queries=qs, windows=windows, source=source, max_bytes=sizes['full']-1)
    assert info['downscaled'] and info['crops_omitted'] == [] and info['bytes'] < sizes['full']
    sizes['half'] = info['bytes']
    previous = sizes['half']
    seen = []
    for limit in (sizes['half']-1, ):
        while True:
            data, info = alignment_report.build_alignment_html(alignment, queries=qs, windows=windows, source=source, max_bytes=limit)
            assert info['downscaled'] and info['bytes'] == len(data)
            seen.append(info['crops_omitted'])
            if len(info['crops_omitted']) == 3:
                break
            assert info['bytes'] <= limit and info['bytes'] < previous
            previous, limit = info['bytes'], info['bytes']-1
    assert seen[0] == [3] and seen[-1] == [1, 2, 3] and all(a == sorted(a) for a in seen)
    assert all(set(x) < set(y) for x, y in zip(seen, seen[1:]))
    html = data.decode('utf-8')
    assert html.count('그림 생략') == 3 and len(captions(html)) == 2 + 4 + 1 + 2 + 3   # overview + map + heat + positions stay (the bundles go)
    for n in (1, 2, 3):
        assert f'순위 {n} ' in html                                                  # the ROI tables stay


def test_images_skipped_when_the_memory_estimate_does_not_fit(world, tmp_path, monkeypatch):
    monkeypatch.setattr(alignment_report, 'fits', lambda *args: False)
    out = tmp_path/'out'
    assert run_change(world, out, top_k=1) == 3
    html, payload = read(out), load(out)
    assert 'data:image' not in html and '<img' not in html and '<script' not in html
    assert 'alignment_report_images_skipped' in {w['code'] for w in payload['warnings']}
    assert payload['status'] == 'partial' and payload['alignment_report']['bytes'] == (out/'alignment.html').stat().st_size
    warning = next(w for w in payload['warnings'] if w['code'] == 'alignment_report_images_skipped')
    assert warning['estimated_bytes'] > 0 and warning['max_operation_bytes'] > 0
    assert '<td>offset_xy_um</td>' in html and '순위 1' in html          # the tables are still there
    # the calculation is untouched
    reference = tmp_path/'reference'
    monkeypatch.undo()
    assert run_change(world, reference, top_k=1) == 0
    assert load(out)['directions'] == load(reference)['directions']


# --- helpers ----------------------------------------------------------------------------------------------------------

def test_png_writer_makes_a_valid_deterministic_png():
    rng = np.random.default_rng(1)
    rgb = rng.integers(0, 256, (7, 11, 3)).astype(np.uint8)
    data = alignment_report._png(rgb)
    assert data == alignment_report._png(rgb)
    image = Image.open(io.BytesIO(data))
    image.load()
    assert image.size == (11, 7) and np.array_equal(np.asarray(image), rgb)


def test_block_reduce_matches_padded_reshape():
    rng = np.random.default_rng(2)
    array = rng.random((23, 17)) > 0.8
    counts = rng.integers(0, 9, (23, 17)).astype(np.uint32)
    for step in (1, 2, 5, 30):
        ph, pw = -23 % step, -17 % step
        padded = np.pad(array, ((0, ph), (0, pw)))
        shaped = padded.reshape((23+ph)//step, step, (17+pw)//step, step)
        assert np.array_equal(alignment_report._block(array, step, 'any'), shaped.any(axis=(1, 3)))
        assert np.array_equal(alignment_report._block(array, step, 'sum'), shaped.sum(axis=(1, 3)))
        pc = np.pad(counts, ((0, ph), (0, pw))).reshape((23+ph)//step, step, (17+pw)//step, step)
        assert np.array_equal(alignment_report._block(counts, step, 'max'), pc.max(axis=(1, 3)))


def test_overlay_colours_and_rank_labels():
    ref = np.array([[True, True, False, False]])
    tgt = np.array([[True, False, True, False]])
    gray = np.array([[False, False, False, True]])
    rgb = alignment_report._overlay(ref, tgt, gray)
    assert [tuple(p) for p in rgb[0]] == [alignment_report.BOTH_COLOUR, alignment_report.REF_COLOUR,
                                         alignment_report.TGT_COLOUR, alignment_report.GRAY]
    canvas = np.full((40, 40, 3), 128, np.uint8)
    alignment_report._label(canvas, '12', 3, 3)
    assert (canvas == alignment_report.LABEL_COLOUR).all(axis=2).any()
    assert alignment_report._label(canvas, '7', 100, 100) is None            # off-canvas positions are clamped, not an error


def test_estimate_and_fits_use_the_reserved_bytes():
    need = alignment_report.estimate_report_bytes((100, 200), (100, 200))
    heat = alignment_report.HEAT_LAYERS_ASSUMED * 8 * 20000
    assert need == 9*20000 + 20000 + 24*20000 + heat               # 9*hr + ht + 24*nb (step 1 here) + n_layers*8*nb
    # the position pictures are made one at a time: the term is one picture, whatever the ROI count
    assert alignment_report.estimate_report_bytes((100, 200), (100, 200), 3) == need + 4*alignment_report.POSITION_MAX_PX**2
    assert alignment_report.estimate_report_bytes((100, 200), (100, 200), 30) == need + 4*alignment_report.POSITION_MAX_PX**2
    assert alignment_report.estimate_report_bytes((100, 200), (100, 200), 0, n_layers=1) == need - heat + 8*20000
    crops = alignment_report.estimate_report_bytes((100, 200), (100, 200), 2, 3, 30) - alignment_report.estimate_report_bytes((100, 200), (100, 200), 2, 3)
    assert crops == 2*(60*60)*(2*3 + 1 + 2*alignment_report.MAX_BUNDLES)    # per ROI: box area x (hotspot layers x 2 + obs + bundle bases)
    assert alignment_report.fits((100, 200), (100, 200), need, 0)
    assert not alignment_report.fits((100, 200), (100, 200), need, 1)
    assert not alignment_report.fits((100, 200), (100, 200), need-1, 0)


def test_reason_text_covers_every_alignment_reason():
    for code in ('cnt_missing_reference', 'cnt_missing_target', 'cnt_layer_set_mismatch', 'no_usable_layers', 'no_overlap',
                 'peak_plateau', 'peak_ambiguous', 'layer_offsets_disagree', 'residual_nonuniform', 'dice_below_min',
                 'search_bound_hit'):
        assert alignment_report.reason_text(code) != code
    assert 'CNT2' in alignment_report.reason_text('cnt_empty_target_CNT2')
    assert alignment_report.reason_text('something_new') == 'something_new'


# --- change-roi-v2/T3a: v3 layout ------------------------------------------------------------------------------------

def h2_titles(html):
    return re.findall(r'<h2[^>]*>(.*?)</h2>', html)


def test_section_order_and_status_badge_colours():
    alignment, qs, windows, source = synthetic_source()
    classes = {}
    for status, css in (('aligned', 'ok'), ('aligned_uncertain', 'uncertain'), ('alignment_failed_identity_used', 'failed')):
        data, _ = alignment_report.build_alignment_html({**alignment, 'status': status}, queries=qs, windows=windows, source=source)
        html = data.decode('utf-8')
        titles = h2_titles(html)
        assert [t.split('.')[0] for t in titles] == ['1', '2', '3', '4', '5', '6', '7']
        assert titles[0].startswith('1. 요약') and titles[1].startswith('2. CNT 정렬') and titles[2].startswith('3. 변경 강조')
        assert titles[3].startswith('4. ROI 목록') and titles[4].startswith('5. ROI 상세') and titles[5].startswith('6. 변경 지도 열지도')
        assert titles[6].startswith('7. 각주')
        assert html.index('<h3>2a.') < html.index('<h3>2b.') < html.index('<h3>2c.')
        assert html.index('<h1>') < html.index('<h2')
        classes[status] = re.search(r'<span class="badge (\w+)">', html).group(1)
        assert css == classes[status]
    assert len(set(classes.values())) == 3


def test_quadrant_difference_over_tolerance_is_highlighted():
    alignment, qs, windows, source = synthetic_source()
    alignment = {**alignment, 'offset_xy_um': [0.3, 0.2], 'args': {'align_layer_tolerance_um': 0.3},
                 'quadrant_offsets_um': {'top_left': [0.3, 0.2], 'top_right': [0.8, 0.2], 'bottom_left': None,
                                         'bottom_right': [0.3, 0.4]}}
    html = alignment_report.build_alignment_html(alignment, queries=qs, windows=windows, source=source)[0].decode('utf-8')
    assert html.count('<td class="hi">') == 1 and html.count('⚠') == 1
    assert '<td class="hi">[0.8, 0.2] · 차이 0.50 µm ⚠</td>' in html
    assert '<td>[0.3, 0.4] · 차이 0.20 µm</td>' in html and '<td>—</td>' in html


def tiny_source(n_layers, shape=(24, 24)):
    valid = np.ones(shape, bool)
    layers, other = {}, {}
    for k in range(n_layers):
        a = np.zeros(shape, bool)
        a[2:6, 2 + k:3 + k] = True
        layers['CNT%d' % (k + 1)] = a
        other['CNT%d' % (k + 1)] = a.copy()
    return alignment_report.ImageSource(layers, other, valid, valid, (0, 0))


def test_aligned_overlap_is_grey_not_blue_or_orange():
    shape = (24, 24)
    valid = np.ones(shape, bool)
    tgt_valid = valid.copy()
    tgt_valid[:, -1] = False
    ref, tgt = np.zeros(shape, bool), np.zeros(shape, bool)
    ref[2:6, 2:6] = tgt[2:6, 2:6] = True            # in both
    ref[10:14, 2:6] = True                          # reference only
    tgt[2:6, 10:14] = True                          # target only
    source = alignment_report.ImageSource({'CNT1': ref, 'CNT2': np.zeros(shape, bool)}, {'CNT1': tgt, 'CNT2': np.zeros(shape, bool)},
                                          valid, tgt_valid, (0, 0))
    before, after, pattern = alignment_report._overview_all(source, ['CNT1', 'CNT2'])
    blue = {tuple(c) for c in alignment_report.REF_SHADES}
    orange = {tuple(c) for c in alignment_report.TGT_SHADES}
    assert tuple(after[3, 3]) == alignment_report.SAME_SHADES[0] and tuple(after[3, 3]) not in blue | orange
    assert tuple(after[11, 3]) == alignment_report.REF_SHADES[0] and tuple(after[3, 11]) == alignment_report.TGT_SHADES[0]
    assert tuple(after[20, 20]) == alignment_report.WHITE and set(map(tuple, after[:, -1])) == {alignment_report.OUTSIDE_GRAY}
    assert tuple(before[3, 3]) not in blue | orange | {alignment_report.SAME_SHADES[0]}       # pre-mixed colour, not a layer shade
    assert tuple(before[11, 3]) == alignment_report.REF_SHADES[0] and tuple(before[3, 11]) == alignment_report.TGT_SHADES[0]
    assert pattern[3, 3] and pattern[11, 3] and not pattern[3, 11]
    again = alignment_report._overview_all(source, ['CNT1', 'CNT2'])
    assert all(np.array_equal(a, b) for a, b in zip((before, after, pattern), again))


def test_more_than_six_layers_warn_about_repeating_shades():
    alignment, qs, windows, _ = synthetic_source()
    for n, expected in ((6, False), (7, True)):
        data, _ = alignment_report.build_alignment_html(alignment, queries=[], windows=[], source=tiny_source(n))
        html = data.decode('utf-8')
        assert ('명도 반복' in html) is expected
        assert all('CNT%d' % k in html for k in range(1, n + 1))                        # the legend names every layer
    assert alignment_report.natural_key('CNT2') < alignment_report.natural_key('CNT10')


def test_position_text_uses_a_three_by_three_grid():
    text = alignment_report.position_text
    shape = (300, 300)
    assert text(0, 0, shape) == '왼쪽 위 (x 0 %, y 0 %)'
    assert text(150, 150, shape) == '가운데 (x 50 %, y 50 %)'
    assert text(299, 299, shape).startswith('오른쪽 아래')
    assert text(99, 199, shape).startswith('가운데 위') and text(100, 200, shape).startswith('오른쪽 가운데')
    assert text(1, 1, None) == '—'


def test_roi_list_links_match_the_detail_ids():
    alignment, qs, windows, source = synthetic_source(3)
    qs[0] = {**qs[0], 'status': 'blocked',
             'contributing_cases': [{**qs[0]['contributing_cases'][0], 'layer': 'M3'}, {**qs[0]['contributing_cases'][0], 'layer': 'M2'}]}
    html = alignment_report.build_alignment_html(alignment, queries=qs, windows=windows, source=source)[0].decode('utf-8')
    table = html[html.index('<h2 id="list">'):html.index('<h2>5.')]
    assert table.count('<tr>') == 1 + len(qs)
    for rank in (1, 2, 3):
        assert f'<a href="#roi{rank}">{rank}</a>' in table and f'id="roi{rank}"' in html
    assert '<td>L1</td><td>M2, M3</td><td>[1.00, 2.00]</td>' in table and '<td>L1</td><td>L1</td>' in table
    assert '<td>blocked</td>' in table and 'ROI 목록</a>' in html
    assert '<td>왼쪽 위 (x ' in table or '<td>가운데' in table or '<td>오른쪽' in table or '<td>왼쪽' in table


def contribution_case(i, kind='shifted', **extra):
    return {'case_key': 'lateral_short|A%d|L1|B|L1|L1' % i, 'change_kind': kind, 'residual_um2': 20.0 - i, 'area_reference_um2': 3.0,
            'area_target_um2': 4.5, 'iou_global': 0.1, 'iou_local': 0.5, 'local_shift_um': 0.3, 'present_in': 'both',
            'layer_changed': False, **extra}


def test_contribution_table_is_korean_with_row_classes_and_folds_after_ten_rows():
    alignment, qs, windows, source = synthetic_source(1)
    cases = [contribution_case(0, 'appeared', present_in='target', flags=['new_electrode'], area_reference_um2=0.0),
             contribution_case(1, 'disappeared', present_in='reference', flags=['removed_electrode'], layer_changed=True)]
    cases += [contribution_case(i) for i in range(2, 11)] + [{**contribution_case(11), 'case_key': 'odd-key', 'layer': 'M9'}]
    qs = [{**qs[0], 'contributing_cases': cases}]
    html = alignment_report.build_alignment_html(alignment, queries=qs, windows=windows[:1], source=source)[0].decode('utf-8')
    for column in ('불량 유형', '전극 쌍', '층', '변경 종류', '창 안 변경 면적 µm²', 'reference 면적 µm²', 'target 면적 µm²',
                   '있는 모델', '층 바뀜', '전극 표시'):
        assert f'<th>{column}</th>' in html
    assert 'iou_global' not in html and 'iou_local' not in html and 'local_shift_um' not in html
    for case in cases:
        assert case['case_key'] in html
    assert html.count('<tr class="app">') == 1 and html.count('<tr class="dis">') == 1
    assert '새로 생김' in html and '사라짐' in html and '형상 변화 <code>shifted</code>' in html
    assert '새 전극 <code>new_electrode</code>' in html and '없어진 전극 <code>removed_electrode</code>' in html
    assert 'target 만' in html and 'reference 만' in html and '<td>예</td>' in html
    assert '<td class="num">0.00</td><td class="num">4.50</td>' in html
    assert '<td>A0 – B</td><td>L1</td><td>창 안 새로 생김</td>' in html and '창 안 사라짐' in html
    assert html.count('나머지 2행 보기') == 1 and html.count('<summary>나머지') == 1
    assert 'odd-key' in html and '<td>M9</td>' in html                                  # undecomposable key stays verbatim
    short = [{**qs[0], 'contributing_cases': cases[:10]}]
    html = alignment_report.build_alignment_html(alignment, queries=short, windows=windows[:1], source=source)[0].decode('utf-8')
    assert '<summary>나머지' not in html


def test_tiny_operation_limit_skips_pictures_but_keeps_the_tables():
    alignment, qs, windows, _ = synthetic_source()
    assert not alignment_report.fits((300, 320), (300, 320), 1000, 0, 3)
    data, info = alignment_report.build_alignment_html(alignment, queries=qs, windows=windows, source=None,
                                                       skipped_reason='estimated too many bytes')
    html = data.decode('utf-8')
    assert '<img' not in html and 'data:image' not in html and info['n_images'] == 0
    assert '그림을 만들지 않았습니다' in html and 'estimated too many bytes' in html
    assert html.count('id="roi') == 3 and '<h2 id="list">' in html and '기여 조합' in html


# --- roi-detail/T3: position figure (T1 advice) -------------------------------------------------------------------------

def test_position_rect_in_a_frame_corner_keeps_its_seven_pixel_size_and_is_clipped_to_the_picture():
    source = position_source()
    base, total = alignment_report._position_base(source)
    assert total == 2 and base.shape[:2] == (150, 160)
    rect = colour_mask(alignment_report._position_image(base, total, {'window_origin_px': [0, 0], 'window_px': 2}), alignment_report.POS_RECT)
    # window = block (0, 0); the 7 px square is centred on it → rows/cols -3..3, so the visible part ends at index 3 (+1, +2 for the thick lines)
    assert rect[3, 0:4].all() and rect[0:4, 3].all() and rect[5, 0:6].all() and rect[0:6, 5].all()
    assert not rect[6:].any() and not rect[:, 6:].any()
    corner = colour_mask(alignment_report._position_image(base, total, {'window_origin_px': [298, 318], 'window_px': 2}),
                         alignment_report.POS_RECT)
    # window = block (149, 159) → rows 146..152 / cols 156..162; the visible top/left edges are at 146 and 156 (grown to 144 and 154)
    assert corner[146, 156:160].all() and corner[146:150, 156].all() and corner[144, 154:160].all() and corner[144:150, 154].all()
    assert not corner[:144].any() and not corner[:, :154].any()


def test_quadrant_lines_of_an_odd_sized_frame_sit_on_the_centre_block():
    source = position_source(shape=(303, 323))
    base, total = alignment_report._position_base(source)
    assert total == 2 and base.shape[:2] == (152, 162)
    line = colour_mask(base, alignment_report.POS_LINE)
    # independent expectation: the frame centre pixel is (303 // 2, 323 // 2) = (151, 161); with 2 px blocks that is block (75, 80).
    # Half the blocked observed size would give (152 // 2, 162 // 2) = (76, 81): the two differ, so a line placed that way fails here.
    rows, cols = np.nonzero(line)
    assert set(rows[line.sum(axis=1)[rows] == 162]) == {75} and set(cols[line.sum(axis=0)[cols] == 152]) == {80}
    assert line.sum() == 162 + 152 - 1


def test_position_caption_states_the_real_block_size(tmp_path):
    for pitch in (0.1, 0.25):
        alignment, qs, windows, source = synthetic_source()
        alignment['pitch_um'] = pitch
        extra = alignment_report._step_for(source.observed.shape, alignment_report.POSITION_MAX_PX)
        block = round(source.step * extra * pitch, 2)
        data, _ = alignment_report.build_alignment_html(alignment, queries=qs, windows=windows, source=source)
        html = data.decode('utf-8')
        text = '변경 위치는 약 %g µm 블록 단위로 칠한다(실제 면적보다 크게 보일 수 있음)' % block
        assert html.count(text) == 2 * len(qs)                   # figcaption and alt of every ROI


# --- roi-detail/T3: local alignment table -------------------------------------------------------------------------------

def local_doc(n):
    base = {'query_id': None, 'box_px': [0, 10, 0, 10], 'window_origin_px': [1, 1], 'window_px': 5, 'layers_used': [],
            'status': 'skipped', 'reason': None, 'flags': [], 'local_offset_xy_px': None, 'local_offset_xy_um': None,
            'delta_from_global_xy_um': None, 'delta_from_global_um': None, 'dice_global': None, 'dice_local': None,
            'ring_delta_xy_px': None, 'ring_dice': None, 'ring_reason': None, 'per_layer': {}}
    ok = dict(base, status='ok', flags=['local_offset_differs'], layers_used=[{'layer': 'L1', 'source': 'electrode'}, {'layer': 'CNT1', 'source': 'cnt'}],
              local_offset_xy_px=[5, 3], local_offset_xy_um=[0.5, 0.3], delta_from_global_xy_um=[0.2, 0.1], delta_from_global_um=0.223607,
              dice_global=0.5, dice_local=0.75, ring_delta_xy_px=[2, 1], ring_dice=0.7)
    biased = dict(ok, status='change_biased', reason='change_biased', flags=[])
    skipped = dict(base, reason='diagnostic_memory_limit')
    rois = [dict([ok, biased, skipped][i % 3], rank=i + 1) for i in range(n)]
    summary = {'n_rois': n, 'ok': 0, 'change_biased': 0, 'ring_inconclusive': 0, 'search_bound_hit': 0, 'ambiguous': 0, 'low_score': 0,
               'insufficient_support': 0, 'skipped': 0}
    for roi in rois:
        summary[roi['status']] += 1
    return {'schema': 'roi_local_alignment_v1', 'rois': rois, 'summary': summary, 'excluded_layers': [{'key': 'electrode:L2', 'reason': 'one_sided'}],
            'linear_fit': {'n': 4, 'scale_ppm_xy': [1200.5, -800.25], 'intercept_um_xy': [0, 0], 'rms_um': 0.01}, 'linear_fit_reason': None}


def test_local_alignment_table_summary_line_and_footnotes_appear_only_when_given():
    alignment, qs, windows, source = synthetic_source(n_rois=3)
    plain, _ = alignment_report.build_alignment_html(alignment, queries=qs, windows=windows, source=source)
    plain = plain.decode('utf-8')
    assert '국소 정렬' not in plain
    data, _ = alignment_report.build_alignment_html(alignment, queries=qs, windows=windows, source=source, local_alignment=local_doc(3))
    html = data.decode('utf-8')
    assert html.count('<h4>국소 정렬 진단</h4>') == 3
    assert 'ROI 국소 정렬 진단: ok 1 · 변경 편향 1 · 링 판정 불가 0 · 모호 0 · 점수 낮음 0 · 지지 부족 0 · 탐색 경계 0 · 진단 안 함 1' in html
    assert '배율 추정 1200.5 ppm, -800.25 ppm (4개 ROI)' in html
    assert '일치 확인 <code>ok</code>' in html and '변경이 정렬을 끌어당김(링과 다름) <code>change_biased</code>' in html
    assert '진단 안 함 <code>skipped</code>' in html and '<code>diagnostic_memory_limit</code>' in html
    assert '[5, 3] px = [0.5, 0.3] µm (x, y)' in html and '0.500 / 0.750' in html and '링 차이 [2, 1] px' in html
    assert 'electrode:L1, cnt:CNT1' in html and 'electrode:L2 (one_sided)' in html
    assert html.count('electrode:L2 (one_sided)') == 1          # document-wide excluded layers: once, in the summary line (L2 is no bundle layer here)
    assert '제외 층 electrode:L2 (one_sided)</p>' in html
    assert '링(창 둘레 띠)의 국소 offset 과 전체의 국소 offset 의 차이가 허용값(<code>--align-layer-tolerance-um</code>) 이하다' in html
    assert '<code>no_valid_region</code>' in html
    assert alignment_report.LOCAL_OPEN not in plain and alignment_report.LOCAL_CLOSE not in plain
    assert re.sub(r'<!--local-->.*?<!--/local-->', '', html, flags=re.S) == plain
    assert html.count('<td class="hi"><code>local_offset_differs</code></td>') == 1
    assert '계산(순위·기여 case·export)에 쓰이지 않는다' in html
    # the table of ROI 2 sits inside ROI 2's block
    block = html.split('id="roi2"')[1].split('id="roi3"')[0]
    assert 'change_biased' in block


def test_local_alignment_summary_gives_the_reason_without_a_fit():
    alignment, qs, windows, source = synthetic_source(n_rois=1)
    doc = local_doc(1)
    doc['linear_fit'], doc['linear_fit_reason'] = None, 'fewer_than_3_ok_rois'
    html = alignment_report.build_alignment_html(alignment, queries=qs, windows=windows, source=source, local_alignment=doc)[0].decode('utf-8')
    assert '배율 추정 없음: fewer_than_3_ok_rois' in html


def test_local_alignment_table_survives_the_size_cap():
    alignment, qs, windows, source = synthetic_source(n_rois=3)
    doc = local_doc(3)
    full, info_full = alignment_report.build_alignment_html(alignment, queries=qs, windows=windows, source=source, local_alignment=doc)
    data, info = alignment_report.build_alignment_html(alignment, queries=qs, windows=windows, source=source, local_alignment=doc,
                                                       max_bytes=len(full) // 4)
    assert info['crops_omitted'] and info['bytes'] < info_full['bytes']
    assert data.decode('utf-8').count('<h4>국소 정렬 진단</h4>') == 3


def test_local_alignment_without_a_picture_source_still_gets_the_table():
    alignment, qs, windows, source = synthetic_source(n_rois=2)
    html = alignment_report.build_alignment_html(alignment, queries=qs, windows=windows, source=None, skipped_reason='x',
                                                 local_alignment=local_doc(2))[0].decode('utf-8')
    assert html.count('<h4>국소 정렬 진단</h4>') == 2
