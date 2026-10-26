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
    assert all(r.startswith('data:image/png;base64,') for r in refs), refs
    assert html.startswith('<!doctype html>') and '<meta charset="utf-8">' in html


def expected_images(payload, alignment, with_cnt=True):
    n_cnt = len(set(f['layer'] for f in alignment['cnt_inputs']['reference']['files'])) if with_cnt else 0
    rois = len(queries(payload, 'reference_to_target'))
    heat_layers = {q['score_layer'] for q in queries(payload, 'reference_to_target')}
    return 2*n_cnt + 2*len(heat_layers) + rois*(n_cnt + 1)


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
    assert len(caps) == html.count('data:image/png;base64,') == expected_images(payload, alignment) == 12
    for name in ('CNT1', 'CNT2', 'CNT3'):          # one before and one after picture per CNT layer
        assert sum(c.startswith(f'{name} · ') for c in caps) == 2
        assert f'<h3>CNT {name}</h3>' in html
        assert sum(c == f'{name} (정렬 후)' for c in caps) == 1       # the ROI crop
    assert sum(c.startswith('층 L1 · ') for c in caps) == 2                                # RU + count_map heat maps
    assert sum('합집합' in c for c in caps) == 2 and all('CNT' not in c for c in caps if '합집합' in c)   # RU + hotspot only
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
        assert case['case_key'] in html and case['change_kind'] in html
    assert 'union_unchanged_pair_changed' in html
    assert 'union_basis: paired_cases_only' in html and 'Δ_union' in html


def test_payload_records_the_report_and_union_basis(aligned):
    report = aligned.payload['alignment_report']
    assert report == {'path': 'alignment.html', 'bytes': (aligned.out/'alignment.html').stat().st_size,
                      'downscaled': False, 'crops_omitted': []}
    assert aligned.payload['union_basis'] == 'paired_cases_only'


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
    assert len(heat) == 2 and len(caps) == 2 + rois           # heat maps + one hotspot crop per ROI, no CNT crops
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
    assert '그림 생략' in html and len(captions(html)) == 8        # overview 6 + heat 2; the overview and heat maps stay
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


def test_size_steps_are_ordered_on_a_synthetic_source():
    alignment, qs, windows, source = synthetic_source()
    sizes = {}
    data, info = alignment_report.build_alignment_html(alignment, queries=qs, windows=windows, source=source)
    sizes['full'] = info['bytes']
    assert info == {'path': 'alignment.html', 'bytes': len(data), 'downscaled': False, 'crops_omitted': [], 'n_images': info['n_images']}
    assert info['n_images'] == 4 + 2 + 3*3
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
    assert html.count('그림 생략') == 3 and len(captions(html)) == 4 + 2           # overview + heat stay
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
    assert need == 6*20000 + 20000
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
