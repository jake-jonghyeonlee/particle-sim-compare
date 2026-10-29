"""layer-align/T5: alignment.html 의 Layer 그림 — 위치 그림 바탕(i), ROI 주변 crop(ii), 층 묶음 바탕, 각주·요약 문구.

단위 시험은 합성 ``ImageSource`` 로(계산 없음), 끝에서 끝 시험은 test_layer_align_cli 의 작은 월드(두 모델 모두 새 구조 + Layer 3층)로 한다.
"""
import base64
import io
import json
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

import test_change_candidates as tcc
from simcompare import alignment_report as ar
from simcompare import change_candidates
from simcompare.numeric_schema import NumericContractError
from test_change_roi_v2_merge import two_layer_world  # noqa: F401  (module-scoped fixture of the other module)
from test_layer_align_cli import (  # noqa: F401  (module-scoped fixtures of the other module)
    files, make, small, small_apply, small_base, small_nolayer)
from test_alignment_report import IMG, captions

SHAPE = (300, 320)


def png_sizes(html, prefix):
    """캡션이 prefix 로 시작하는 그림들의 (너비, 높이) 목록."""
    out = []
    for data, alt in IMG.findall(html):
        if alt.startswith(prefix):
            out.append(Image.open(io.BytesIO(base64.b64decode(data))).size)
    return out


def layered_source(n_rois=3, shape=SHAPE, w=100, seed=0, layer_names=('L1', 'L2'), offsets=None, fill=None):
    """synthetic_source 와 같은 합성 source + collect_layers. fill(side, layer) → frame bool 로 Layer 를 바꿀 수 있다."""
    rng = np.random.default_rng(seed)
    valid = np.ones(shape, bool)
    cnt = {'CNT1': rng.random(shape) > 0.7}
    windows = [{'layer': 'L1', 'window_origin_px': [20 + 70*i, 30 + 60*i], 'window_px': w} for i in range(n_rois)]
    maps = {'maps': {'L1': rng.integers(0, 4, shape).astype(np.uint32)}, 'D': valid,
            'union_change': {'L1': rng.random(shape) > 0.9}, 'union_ref': {'L1': rng.random(shape) > 0.5},
            'union_tgt': {'L1': rng.random(shape) > 0.5}}
    source = ar.ImageSource(cnt, cnt, valid, valid, (0, 0))
    source.collect(maps, windows)
    frames = {(side, name): (rng.random(shape) > 0.5) for side in ('ref', 'tgt') for name in layer_names}

    def load(side, layer):
        return fill(side, layer) if fill is not None else frames.get((side, layer))
    style = ar.stack_style({name: k + 1 for k, name in enumerate(layer_names)})
    source.collect_layers(style, load, offsets or [((0, 0), 'global')] * n_rois)
    query = {'score_um2': 1.0, 'score_layer': 'L1', 'sum_residual_um2': 2.0, 'changed_case_count': 1, 'union_delta_um2': 0.5,
             'union_area_reference_um2': 3.0, 'union_area_target_um2': 3.5, 'observed_fraction': 1.0, 'flags': [],
             'cnt_change_um2_by_layer': {}, 'center_xy_um': [1.0, 2.0], 'region_id': 'chg_x',
             'contributing_cases': [{'case_key': 'k', 'change_kind': 'shifted', 'residual_um2': 1.0, 'iou_global': 0.1, 'iou_local': 0.5}]}
    alignment = {'status': 'aligned', 'reasons': [], 'offset_xy_um': [0.3, 0.2], 'offset_xy_px': [0, 0], 'sign': 'p_target = p_reference + offset',
                 'dice_best': 0.9, 'dice_identity': 0.1, 'peak_ratio': 2.0, 'best_plateau_px': 1, 'best_plateau_extent_px': [0, 0],
                 'pattern_period_vectors_um': [], 'layers_used': ['CNT1'], 'layers_excluded': [], 'overlap_area_um2': 1.0,
                 'search_um': 1.0, 'pitch_um': 0.1, 'alternatives': []}
    return alignment, [dict(query) for _ in range(n_rois)], windows, source


def blank_source(shape, windows, offset=(0, 0)):
    valid = np.ones(shape, bool)
    zero = np.zeros(shape, bool)
    maps = {'maps': {'L1': np.zeros(shape, np.uint32)}, 'D': valid, 'union_change': {'L1': zero}, 'union_ref': {'L1': zero}, 'union_tgt': {'L1': zero}}
    source = ar.ImageSource({}, {}, valid, valid, offset)
    source.collect(maps, windows)
    return source


# --- (b) 위치 그림 바탕 ----------------------------------------------------------------------------------------------

def test_b_dense_pattern_at_half_fill_is_alpha_times_fraction_not_saturated_grey():
    shape = (500, 500)
    pattern = np.zeros(shape, bool)
    pattern[:, ::2] = True                                           # 50% 채움 조밀 패턴
    source = blank_source(shape, [])
    style = ar.stack_style({'L1': 1})
    source.collect_layers(style, lambda side, layer: pattern if side == 'ref' else None, [])
    base, total = ar._position_base(source)
    assert total == 2 and base.shape[:2] == (250, 250)
    fraction = int(source.layer_fraction['L1'][0, 0])
    assert fraction == 128                                           # round(0.5 * 255)
    alpha = ar.LAYER_ALPHA * fraction / 255.0
    expected = np.rint(255 * (1 - alpha) + np.array(style['colour']['L1'], dtype=float) * alpha).astype(int)
    assert np.abs(base[3, 3].astype(int) - expected).max() <= 1
    assert base[3, 3].min() > 120 and not (base[3, 3] == base[3, 3][0]).all()      # 층 색을 띤다(회백색으로 포화하지 않음)
    full = blank_source(shape, [])
    full.collect_layers(style, lambda side, layer: np.ones(shape, bool) if side == 'ref' else None, [])
    base_full, _ = ar._position_base(full)
    assert np.abs(base_full[3, 3].astype(int) - np.rint(255 * (1 - ar.LAYER_ALPHA) + np.array(style['colour']['L1']) * ar.LAYER_ALPHA)).max() <= 1
    empty = blank_source(shape, [])
    empty.collect_layers(style, lambda side, layer: np.zeros(shape, bool) if side == 'ref' else None, [])
    assert (ar._position_base(empty)[0][3, 3] == 255).all()


def test_b_layers_are_blended_in_stack_order_bottom_to_top():
    shape = (200, 200)
    ones = np.ones(shape, bool)
    source = blank_source(shape, [])
    style = ar.stack_style({'A': 2, 'B': 1}, {'A': 5, 'B': 9})        # stack order: A(5) 아래, B(9) 위
    assert style['order'] == ['A', 'B'] and style['source'] == 'layer_stack'
    source.collect_layers(style, lambda side, layer: ones if side == 'ref' else None, [])
    base, _ = ar._position_base(source)
    px = np.array([255.0] * 3)
    for name in style['order']:
        px = px * (1 - ar.LAYER_ALPHA) + np.array(style['colour'][name]) * ar.LAYER_ALPHA
    assert np.abs(base[2, 2].astype(int) - np.rint(px)).max() <= 2


def test_fraction_uses_the_real_area_of_edge_blocks():
    frame = np.ones((5, 7), bool)
    assert (ar._fraction(frame, 3) == 255).all()
    half = np.zeros((5, 7), bool)
    half[:, :2] = True
    assert ar._fraction(half, 4).tolist() == [[128, 0], [128, 0]]


# --- (c) ROI 사각형 색 ---------------------------------------------------------------------------------------------------

def test_c_roi_rectangle_colour_differs_from_the_change_colour_and_every_layer_colour():
    assert ar.POS_RECT_LAYER != ar.POS_CHANGED_LAYER
    assert ar.POS_RECT_LAYER not in ar.LAYER_HUES and ar.POS_CHANGED_LAYER not in ar.LAYER_HUES
    shape = (300, 320)
    window = {'layer': 'L1', 'window_origin_px': [100, 120], 'window_px': 40}
    source = blank_source(shape, [window])
    change = np.zeros(shape, bool)
    change[200:210, 40:50] = True
    source.heat['L1']['union'] = ar._block(change, source.step, 'sum')
    ones = np.ones(shape, bool)
    source.collect_layers(ar.stack_style({'L1': 1, 'L2': 2}), lambda side, layer: ones if side == 'ref' else None, [((0, 0), 'global')])
    base, total = ar._position_base(source)
    changed = np.all(base == np.array(ar.POS_CHANGED_LAYER, dtype=np.uint8), axis=2)
    assert changed.any() and not np.all(base == np.array(ar.POS_RECT_LAYER, dtype=np.uint8), axis=2).any()
    rgb = ar._position_image(base, total, window, True)
    rect = np.all(rgb == np.array(ar.POS_RECT_LAYER, dtype=np.uint8), axis=2)
    ys, xs = np.nonzero(rect)
    assert rect.any() and not (rect & changed).any()
    top, left = int(ys.min()), int(xs.min())
    assert (rgb[top - 1, left:left + 4] == 255).all()               # 바깥 흰 테
    # 모든 층 색은 혼합이라 사각형 색과 같은 픽셀이 바탕에 없다
    for hue in ar.LAYER_HUES:
        assert not np.all(base == np.array(hue, dtype=np.uint8), axis=2).any()


# --- (d) crop 크기·offset ---------------------------------------------------------------------------------------------------

def test_d_crop_side_is_four_windows_and_clips_at_the_frame_edge():
    windows = [{'layer': 'L1', 'window_origin_px': [140, 150], 'window_px': 20},     # 가운데
               {'layer': 'L1', 'window_origin_px': [0, 0], 'window_px': 20}]          # frame 왼쪽 위 모서리
    source = blank_source(SHAPE, windows)
    ones = np.ones(SHAPE, bool)
    source.collect_layers(ar.stack_style({'L1': 1}), lambda side, layer: ones, [((0, 0), 'global')] * 2)
    assert source.layer_crop[(0, 'ref')].shape == (80, 80, 3) and source.layer_crop[(0, 'tgt')].shape == (80, 80, 3)
    assert source.layer_crop[(1, 'ref')].shape == (50, 50, 3)                         # 중심 기준 -30..50 → 0..50
    assert source.crop_boxes[1] == (0, 50, 0, 50)
    for i, side in ((0, 80), (1, 50)):
        for crop_side in ('ref', 'tgt'):
            assert ar._crop_image(source, i, windows[i], crop_side, ar.CROP_MAX_PX).shape[:2] == (side, side)


def test_d_a_crop_wider_than_the_limit_is_block_averaged():
    windows = [{'layer': 'L1', 'window_origin_px': [140, 150], 'window_px': 100}]
    source = blank_source(SHAPE, windows)
    ones = np.ones(SHAPE, bool)
    source.collect_layers(ar.stack_style({'L1': 1}), lambda side, layer: ones, [((0, 0), 'global')])
    assert source.layer_crop[(0, 'ref')].shape == (300, 320, 3)       # 4 x 100 px 를 frame (300, 320) 으로 자름
    assert ar._crop_image(source, 0, windows[0], 'ref', 200).shape[:2] == (150, 160)
    assert ar._crop_image(source, 0, windows[0], 'ref', ar.CROP_MAX_PX).shape[:2] == (300, 320)
    ramp = np.arange(30 * 30).reshape(30, 30) % 255
    rgb = np.stack([ramp] * 3, axis=2).astype(np.uint8)
    small_rgb = ar._downscale_rgb(rgb, 3)
    assert small_rgb.shape == (10, 10, 3)
    assert abs(int(small_rgb[0, 0, 0]) - int(round(rgb[:3, :3, 0].mean()))) <= 1


def test_d_target_crop_is_moved_by_the_applied_offset_of_each_roi():
    windows = [{'layer': 'L1', 'window_origin_px': [40, 50], 'window_px': 20},
               {'layer': 'L1', 'window_origin_px': [40, 50], 'window_px': 20}]
    marker = np.zeros(SHAPE, bool)
    marker[50:54, 60:64] = True                                       # target 좌표
    offsets = [((0, 0), 'global'), ((5, 3), 'unit')]                  # (dx, dy)
    source = blank_source(SHAPE, windows)
    source.collect_layers(ar.stack_style({'L1': 1}), lambda side, layer: marker if side == 'tgt' else np.zeros(SHAPE, bool), offsets)
    colour = np.rint(255 * (1 - ar.LAYER_ALPHA) + np.array(ar.LAYER_HUES[0]) * ar.LAYER_ALPHA).astype(np.uint8)
    y0, _, x0, _ = source.crop_boxes[0]
    assert source.crop_boxes[1] == source.crop_boxes[0] and (y0, x0) == (10, 20)

    def painted(i):
        ys, xs = np.nonzero(np.all(source.layer_crop[(i, 'tgt')] == colour, axis=2))
        return int(ys.min()), int(xs.min()), int(ys.max()), int(xs.max())
    assert painted(0) == (50 - y0, 60 - x0, 53 - y0, 63 - x0)         # offset 0: 그대로
    assert painted(1) == (50 - 3 - y0, 60 - 5 - x0, 53 - 3 - y0, 63 - 5 - x0)    # p_target = p_ref + offset → ref 좌표는 target − offset
    assert not np.all(source.layer_crop[(0, 'ref')] == colour, axis=2).any()
    assert 'offset [5, 3] px (x, y), 출처 unit offset' in ar._crop_caption(source, 1, 'tgt')
    assert 'offset [0, 0] px (x, y), 출처 전역 offset' in ar._crop_caption(source, 0, 'tgt')


# --- (e) 30 MiB 사다리 ---------------------------------------------------------------------------------------------------------

def test_e_size_ladder_shrinks_the_crops_then_drops_them_and_keeps_the_position_pictures():
    alignment, qs, windows, source = layered_source(n_rois=3)
    full, info = ar.build_alignment_html(alignment, queries=qs, windows=windows, source=source)
    html = full.decode('utf-8')
    assert source.layered() and 'Layer 그림 범례' in html
    crops = png_sizes(html, 'ROI 주변')
    assert len(crops) == 6 and all(max(size) <= ar.CROP_MAX_PX for size in crops)
    assert max(max(size) for size in crops) > ar.CROP_HALF_PX               # 반 해상도 단계가 줄일 크기
    n_full = info['n_images']
    assert n_full == len(captions(html))
    assert sum(c.startswith('위치') for c in captions(html)) == 3
    half, info_half = ar.build_alignment_html(alignment, queries=qs, windows=windows, source=source, max_bytes=info['bytes'] - 1)
    half_html = half.decode('utf-8')
    assert info_half['downscaled'] and info_half['crops_omitted'] == [] and info_half['bytes'] < info['bytes']
    assert all(max(size) <= ar.CROP_HALF_PX for size in png_sizes(half_html, 'ROI 주변')) and len(png_sizes(half_html, 'ROI 주변')) == 6
    assert info_half['n_images'] == len(captions(half_html))
    limit, seen = info_half['bytes'] - 1, []
    while True:
        data, current = ar.build_alignment_html(alignment, queries=qs, windows=windows, source=source, max_bytes=limit)
        seen.append(current['crops_omitted'])
        assert current['n_images'] == len(captions(data.decode('utf-8')))
        if len(current['crops_omitted']) == 3:
            break
        limit = current['bytes'] - 1
    assert seen[0] == [3] and seen[-1] == [1, 2, 3]
    last = data.decode('utf-8')
    assert last.count('그림 생략') == 3 and not png_sizes(last, 'ROI 주변')
    assert sum(c.startswith('위치') for c in captions(last)) == 3             # 위치 그림은 유지
    assert 'Layer 그림 범례' in last


def test_e_the_estimate_adds_the_fractions_and_two_crops_per_roi_only_with_layer_png():
    ref, tgt = (3000, 3000), (3000, 3000)
    base = ar.estimate_report_bytes(ref, tgt, 5, 4, 100)
    assert ar.estimate_report_bytes(ref, tgt, 5, 4, 100, 0) == base
    nb = 3000 * 3000 // 9                                              # step 3 → 1000 x 1000
    assert nb == 1000 * 1000
    assert ar.estimate_report_bytes(ref, tgt, 5, 4, 100, 7) - base == 7 * nb + 5 * 2 * 400 * 400 * (3 + 7)     # T5b: 층별 bool crop 2개 x 층 수
    assert ar.estimate_report_bytes(ref, tgt, 0, 4, 100, 7) - ar.estimate_report_bytes(ref, tgt, 0, 4, 100) == 7 * nb
    assert ar.fits(ref, tgt, base + 1, 0, 5, 4, 100) and not ar.fits(ref, tgt, base + 1, 0, 5, 4, 100, 7)


# --- (f) 층 묶음 바탕 ----------------------------------------------------------------------------------------------------------

def test_f_bundle_captions_name_the_base_only_when_layer_figures_are_on():
    alignment, qs, windows, source = layered_source(n_rois=1, layer_names=('L1',))
    box = source.hot[0]['box']
    shape = (box[1] - box[0], box[3] - box[2])
    shape_bool = np.ones(shape, bool)
    source.base[(0, 'L1', 'ref')], source.base[(0, 'L1', 'tgt')] = shape_bool, shape_bool
    plain = [c for c, _ in ar._bundle_images(source, 0, windows[0], 'L1', 400)]
    assert not any('바탕:' in c for c in plain)                          # base_kind 가 비어 있으면 예전 캡션 그대로
    source.base_kind[('L1', 'ref')], source.base_kind[('L1', 'tgt')] = 'Layer', 'Layer'
    caps = [c for c, _ in ar._bundle_images(source, 0, windows[0], 'L1', 400)]
    assert all(c.endswith(' · 바탕: Layer') for c in caps)
    source.base_kind[('L1', 'tgt')] = 'Merged'
    caps = [c for c, _ in ar._bundle_images(source, 0, windows[0], 'L1', 400)]
    assert caps[0].endswith(' · 바탕: reference Layer / target Merged 전극 합집합')
    assert caps[1].endswith(' · 바탕: Layer') and caps[2].endswith(' · 바탕: Merged 전극 합집합')
    source.base[(0, 'L1', 'tgt')] = None
    caps = [c for c, _ in ar._bundle_images(source, 0, windows[0], 'L1', 400)]
    assert caps[0].endswith(' · 바탕: Layer') and '전극 형상 없음' in caps[0] and '바탕:' not in caps[2]


# --- T5b: 층별 비교 (reference | target | 차이) ----------------------------------------------------------------------------------

def decode(png):
    image = Image.open(io.BytesIO(png))
    assert image.mode == 'P'                                           # 팔레트 PNG(작게 저장)
    return np.array(image.convert('RGB'))


def compare_world(layer_names=('A', 'B', 'C'), changed=('B',), tgt_missing=(), n_rois=1):
    """층마다 고정 무늬. changed 층은 target 에서 창 안 한 곳이 다르다. tgt_missing 층은 target 에 Layer 가 없다."""
    rng = np.random.default_rng(7)
    base = {name: rng.random(SHAPE) > 0.6 for name in layer_names}

    def fill(side, layer):
        if side == 'tgt' and layer in tgt_missing:
            return None
        frame = base[layer].copy()
        if side == 'tgt' and layer in changed:
            frame[40:46, 60:70] = ~frame[40:46, 60:70]
        return frame
    return layered_source(n_rois=n_rois, layer_names=layer_names, fill=fill, w=100)


def test_t5b_only_the_changed_layer_is_spread_and_the_rest_are_folded():
    alignment, qs, windows, source = compare_world()
    plan = ar.compare_layers(source, 0, windows[0])
    assert [item['layer'] for item in plan['spread']] == ['B'] and [item['layer'] for item in plan['folded']] == ['A', 'C']
    assert plan['spread'][0]['n_diff'] == 60 and plan['spread'][0]['ref_only'] + plan['spread'][0]['tgt_only'] == 60
    html = ar.build_alignment_html(alignment, queries=qs, windows=windows, source=source)[0].decode('utf-8')
    block = html.split('<h4>층별 비교</h4>')[1].split('</details>')[0]
    opened, folded = block.split('<details>')
    assert '층 B · 창 안 차이 60 픽셀' in opened and 'µm²' in opened and '층 A ·' not in opened and '층 C ·' not in opened
    assert '<summary>차이 없는 층 2개</summary>' in folded and '층 A ·' in folded and '층 C ·' in folded
    caps = [c for c in captions(html) if c.startswith('층별 비교')]
    assert len(caps) == 9 and caps[0] == '층별 비교 · B · reference (검정 = 형상)'
    assert len(png_sizes(html, '층별 비교')) == 9


def test_t5b_spread_layers_are_ordered_by_difference_pixels_then_stack_order():
    rng = np.random.default_rng(3)
    base = {name: rng.random(SHAPE) > 0.6 for name in ('A', 'B', 'C', 'D')}
    rows = {'A': 4, 'B': 9, 'C': 4, 'D': 0}

    def fill(side, layer):
        frame = base[layer].copy()
        if side == 'tgt':
            frame[40:40 + rows[layer], 60] = ~frame[40:40 + rows[layer], 60]
        return frame
    alignment, qs, windows, source = layered_source(n_rois=1, layer_names=('A', 'B', 'C', 'D'), fill=fill)
    plan = ar.compare_layers(source, 0, windows[0])
    assert [item['layer'] for item in plan['spread']] == ['B', 'A', 'C']          # 9, 그다음 동률 4 는 stack order
    assert [item['layer'] for item in plan['folded']] == ['D']


def test_t5b_difference_picture_has_four_distinct_colours():
    window = {'layer': 'L1', 'window_origin_px': [40, 50], 'window_px': 20}
    source = blank_source(SHAPE, [window])
    ref, tgt = np.zeros(SHAPE, bool), np.zeros(SHAPE, bool)
    ref[45:50, 60:65] = True                                          # 공통 + reference 만
    tgt[47:52, 62:67] = True                                          # 공통 + target 만
    source.collect_layers(ar.stack_style({'L1': 1}), lambda side, layer: ref if side == 'ref' else tgt, [((0, 0), 'global')])
    y0, _, x0, _ = source.crop_boxes[0]
    source.crop_obs[0][:, :5] = False                                 # 왼쪽 몇 열은 비교 영역 밖
    ref_png, tgt_png, diff_png = ar._compare_pictures(source, 0, window, 'L1', ar.CROP_MAX_PX)
    rgb_ref, rgb_tgt, diff = decode(ref_png), decode(tgt_png), decode(diff_png)
    assert rgb_ref.shape == (80, 80, 3) == diff.shape
    pal = ar.COMPARE_PALETTE

    def at(image, y, x):
        return tuple(int(v) for v in image[y - y0, x - x0])
    assert at(diff, 45, 60) == pal[ar.COMPARE_REF_ONLY]               # reference 만
    assert at(diff, 51, 66) == pal[ar.COMPARE_TGT_ONLY]               # target 만
    assert at(diff, 48, 63) == pal[ar.COMPARE_COMMON]                 # 공통
    assert at(diff, 41, 52) == pal[0]                                 # 둘 다 없음(흰색)
    assert at(rgb_ref, 45, 60) == (0, 0, 0) and at(rgb_ref, 51, 66) == (255, 255, 255) and at(rgb_tgt, 51, 66) == (0, 0, 0)
    assert tuple(int(v) for v in diff[10, 0]) == pal[ar.COMPARE_OUTSIDE]       # 비교 영역 밖
    assert at(diff, 40, 55) == pal[ar.COMPARE_RECT]                   # ROI 창 테두리
    assert len({pal[ar.COMPARE_REF_ONLY], pal[ar.COMPARE_TGT_ONLY], pal[ar.COMPARE_COMMON], pal[0], pal[ar.COMPARE_RECT]}) == 5
    assert pal[ar.COMPARE_RECT] not in (ar.POS_RECT_LAYER, ar.POS_CHANGED_LAYER, pal[ar.COMPARE_TGT_ONLY], pal[ar.COMPARE_REF_ONLY])


def test_t5b_target_layer_is_moved_by_the_applied_offset_of_each_roi():
    windows = [{'layer': 'L1', 'window_origin_px': [40, 50], 'window_px': 20}] * 2
    marker = np.zeros(SHAPE, bool)
    marker[50:54, 60:64] = True
    offsets = [((0, 0), 'global'), ((5, 3), 'unit')]
    source = blank_source(SHAPE, windows)
    source.collect_layers(ar.stack_style({'L1': 1}), lambda side, layer: marker if side == 'tgt' else np.zeros(SHAPE, bool), offsets)
    y0, _, x0, _ = source.crop_boxes[0]

    def black(i):
        image = decode(ar._compare_pictures(source, i, windows[i], 'L1', ar.CROP_MAX_PX)[1])
        ys, xs = np.nonzero(np.all(image == 0, axis=2))
        return int(ys.min()) + y0, int(xs.min()) + x0, int(ys.max()) + y0, int(xs.max()) + x0
    assert black(0) == (50, 60, 53, 63)
    assert black(1) == (47, 55, 50, 58)                                # target − offset (dy 3, dx 5)


def test_t5b_a_layer_on_one_model_only_is_spread_with_a_one_sided_caption():
    alignment, qs, windows, source = compare_world(changed=(), tgt_missing=('C',))
    plan = ar.compare_layers(source, 0, windows[0])
    assert [item['layer'] for item in plan['spread']] == ['C'] and plan['spread'][0]['only'] == 'ref'
    assert plan['spread'][0]['tgt_only'] == 0 and plan['spread'][0]['ref_only'] > 0
    html = ar.build_alignment_html(alignment, queries=qs, windows=windows, source=source)[0].decode('utf-8')
    assert '층 C · 창 안 차이' in html and 'reference 에만 있는 층' in html and 'target 에만 있는 층' not in html
    only_tgt = layered_source(n_rois=1, layer_names=('A', 'B'), fill=lambda side, layer: np.ones(SHAPE, bool) if (side, layer) != ('ref', 'B') else None)
    html = ar.build_alignment_html(only_tgt[0], queries=only_tgt[1], windows=only_tgt[2], source=only_tgt[3])[0].decode('utf-8')
    assert 'target 에만 있는 층' in html


def test_t5b_the_size_ladder_halves_the_comparison_pictures_then_omits_them_with_a_sentence():
    alignment, qs, windows, source = compare_world(n_rois=3)
    full, info = ar.build_alignment_html(alignment, queries=qs, windows=windows, source=source)
    html = full.decode('utf-8')
    assert info['n_images'] == len(captions(html)) and len(png_sizes(html, '층별 비교')) == 27
    assert max(max(size) for size in png_sizes(html, '층별 비교')) > ar.CROP_HALF_PX
    half, info_half = ar.build_alignment_html(alignment, queries=qs, windows=windows, source=source, max_bytes=info['bytes'] - 1)
    half_html = half.decode('utf-8')
    assert info_half['downscaled'] and len(png_sizes(half_html, '층별 비교')) == 27
    assert all(max(size) <= ar.CROP_HALF_PX for size in png_sizes(half_html, '층별 비교'))
    assert info_half['n_images'] == len(captions(half_html))
    none, info_none = ar.build_alignment_html(alignment, queries=qs, windows=windows, source=source, max_bytes=1)
    none_html = none.decode('utf-8')
    assert none_html.count('층별 비교 생략(크기 상한)') == 3 and not png_sizes(none_html, '층별 비교')
    assert info_none['n_images'] == len(captions(none_html))


def test_t5b_the_masks_are_kept_for_the_comparison_and_counted_in_the_held_bytes():
    alignment, qs, windows, source = compare_world()
    assert source.layer_masks and all(mask.dtype == bool for mask in source.layer_masks.values())
    held = source.held_bytes()
    skipped = blank_source(SHAPE, windows)
    skipped.collect_layers(ar.stack_style({'A': 1}), lambda side, layer: np.ones(SHAPE, bool), [((0, 0), 'global')], keep_masks=False)
    assert not skipped.layer_masks and held > skipped.held_bytes()
    skipped.compare_skipped = '메모리 한도'
    html = ar.build_alignment_html(alignment, queries=qs, windows=windows, source=skipped)[0].decode('utf-8')
    assert '층별 비교 생략(메모리 한도)' in html and not png_sizes(html, '층별 비교')


def test_t5b_without_layer_data_there_is_no_comparison():
    from test_alignment_report import synthetic_source
    alignment, qs, windows, source = synthetic_source(n_rois=2)
    html = ar.build_alignment_html(alignment, queries=qs, windows=windows, source=source)[0].decode('utf-8')
    assert '층별 비교' not in html and not source.layer_masks and ar.compare_layers(source, 0, windows[0]) == {'spread': [], 'folded': []}


# --- (g) stack order 출처 ------------------------------------------------------------------------------------------------------

def test_g_stack_order_comes_from_the_config_then_from_the_file_numbers():
    numbers = {'L1': 3, 'L2': 1, 'L3': 2}
    from_config = ar.stack_style(numbers, {'L1': 1, 'L2': 2, 'L3': 3})
    assert from_config['order'] == ['L1', 'L2', 'L3'] and from_config['source'] == 'layer_stack' and from_config['not_in_stack'] == []
    from_files = ar.stack_style(numbers, None)
    assert from_files['order'] == ['L2', 'L3', 'L1'] and from_files['source'] == '파일 번호' and from_files['not_in_stack'] == []
    assert ar.stack_style(numbers, {})['source'] == '파일 번호'
    partial = ar.stack_style(numbers, {'L1': 1, 'L2': 2})                # L3 은 stack order 에 없다
    assert partial['order'] == ['L1', 'L2', 'L3'] and partial['not_in_stack'] == ['L3'] and partial['source'] == 'layer_stack(+파일 번호 n)'
    shifted = ar.stack_style({'L1': 3, 'L2': 1}, {'L1': 1, 'L2': 2, 'EXTRA': 0})   # 그림에 없는 층 이름은 무시
    assert shifted['order'] == ['L1', 'L2']
    many = ar.stack_style({'L%d' % k: k for k in range(1, 14)})
    assert many['colour']['L13'] == many['colour']['L1'] == ar.LAYER_HUES[0] and many['colour']['L2'] == ar.LAYER_HUES[1]


def test_g_legend_names_the_source_and_the_layers_missing_from_the_stack_order():
    alignment, qs, windows, source = layered_source(n_rois=1)
    source.layer_style = ar.stack_style({'L1': 1, 'L2': 2}, {'L1': 1})
    html = ar.build_alignment_html(alignment, queries=qs, windows=windows, source=source)[0].decode('utf-8')
    assert 'stack order 출처: <code>layer_stack(+파일 번호 n)</code>' in html
    assert ar.LAYER_NOT_IN_STACK in html and 'L2' in html.split(ar.LAYER_NOT_IN_STACK)[1][:80]
    assert '(아래 → 위)' in html


# --- (h) 각주·요약 ----------------------------------------------------------------------------------------------------------

def local_apply_doc(n):
    from test_alignment_report import local_doc
    doc = local_doc(n)
    doc['layers_basis'] = 'layer_png_per_layer_dice_first'
    return doc


def build(apply_mode=None, local=None, queries_update=None, alignment_update=None, source_on=True):
    alignment, qs, windows, source = layered_source(n_rois=2)
    if alignment_update:
        alignment.update(alignment_update)
    for q in qs:
        q.update(queries_update or {})
    html = ar.build_alignment_html(alignment, queries=qs, windows=windows, source=source if source_on else None,
                                   skipped_reason='x', local_alignment=local, apply_mode=apply_mode)[0].decode('utf-8')
    return html


def test_h_apply_modes_get_their_own_sentences_and_the_diagnostic_keeps_the_old_one():
    diag = build(local=local_apply_doc(2))
    assert '계산(순위·기여 case·export)에 쓰이지 않는다' in diag and 'ROI 국소 정렬 진단(<code>--local-align-um</code>) 상태:' in diag
    assert '변경 지도·열지도는 unit offset 기준' not in diag and 'zero_after_refine' not in diag and '적용 모드' not in diag
    local = build('local', local_apply_doc(2))
    assert '계산(순위·기여 case·export)에 쓰이지 않는다' not in local
    assert '국소 정렬은 계산에 쓰인다' in local and 'ROI 국소 정렬(<code>--apply-align local</code>) 상태:' in local
    assert '변경 지도·열지도는 unit offset 기준이고, ROI 점수·기여 case·중심은 ROI 적용 offset 기준이다' in local
    assert '경계 창' in local and '창 중심이 속한 unit 의 offset 기준' in local
    assert '<code>zero_after_refine</code>' in local and 'outside_refine_pool' in local
    unit = build('unit')
    assert '변경 지도·열지도는 unit offset 기준' in unit and 'zero_after_refine' not in unit and '진단 표는' not in unit
    unit_diag = build('unit', local_apply_doc(2))
    assert '계산(순위·기여 case·export)에 쓰이지 않는다' in unit_diag and '전역 offset g 를 시작점으로' in unit_diag
    assert '전역 offset g 를 시작점으로' not in local


def test_h_summary_shows_the_apply_mode_unit_offsets_and_folded_roi_count_only_when_applying():
    units = [{'unit_index_xy': [x, y], 'offset_xy_px': [x + 1, y + 2], 'applied': (x + y) % 2 == 0} for y in (0, 1) for x in (0, 1)]
    plain = build(alignment_update={'unit_offsets': units}, queries_update={'unit_fold': {'n_replicas': 2}})
    assert '적용 모드' not in plain and '접힌 ROI 수' not in plain and 'unit offset 4개' not in plain
    applying = build('unit', alignment_update={'unit_offsets': units}, queries_update={'unit_fold': {'n_replicas': 2}})
    assert '<td>적용 모드</td>' in applying and '--apply-align unit' in applying
    assert 'unit [0, 0]: <code>[1, 2]</code> 적용(unit)' in applying and 'unit [1, 0]: <code>[2, 2]</code> 전역 offset 사용' in applying
    assert '<td>접힌 ROI 수</td><td>4</td>' in applying                      # ROI 2개 x 접힌 2개
    no_fold = build('local')
    assert '<td>적용 모드</td>' in no_fold and '접힌 ROI 수' not in no_fold and 'unit offset 4개' not in no_fold


# --- 그림이 없는 입력은 예전 그대로 --------------------------------------------------------------------------------------------------

def test_without_layer_data_the_html_has_no_layer_text_or_pictures():
    from test_alignment_report import synthetic_source
    alignment, qs, windows, source = synthetic_source(n_rois=2)
    assert not source.layered()
    html = ar.build_alignment_html(alignment, queries=qs, windows=windows, source=source)[0].decode('utf-8')
    for text in ('Layer 그림', 'ROI 주변 ·', 'Layer stack', '바탕:', '진자홍'):
        assert text not in html
    assert '연빨강 = 변경 위치' in html and not png_sizes(html, 'ROI 주변')


# --- 끝에서 끝 (작은 월드: 두 모델 모두 새 구조 + Layer 3층) ----------------------------------------------------------------------------------

def test_end_to_end_default_run_draws_layer_figures_and_records_the_inputs(small_base, small_nolayer):
    html = (small_base.out / 'alignment.html').read_text(encoding='utf-8')
    plain = (small_nolayer.out / 'alignment.html').read_text(encoding='utf-8')
    rois = len(tcc.queries(small_base.payload, 'reference_to_target'))
    caps = captions(html)
    assert sum(c.startswith('ROI 주변 · reference Layer stack') for c in caps) == rois
    assert sum(c.startswith('ROI 주변 · target Layer stack') for c in caps) == rois
    assert all('reference 좌표로 옮김: offset' in c and '전역 offset' in c for c in caps if c.startswith('ROI 주변 · target'))
    assert sum(c.startswith('위치: frame 전체 · 바탕 = reference 모델 Layer') for c in caps) == rois
    assert 'Layer 그림 범례' in html and 'stack order 출처' in html
    assert any(c.endswith(' · 바탕: Layer') for c in caps if c.startswith('ROI 층 '))
    assert 'ROI 주변 ·' not in plain and 'Layer 그림' not in plain and '바탕: Layer' not in plain
    notes = [n for n in small_base.payload['notes'] if n['code'] == ar.LAYER_NOT_IN_STACK]
    assert notes and set(notes[0]['layers']) <= {'L1', 'L2', 'L3'}
    assert not any(n['code'] == ar.LAYER_NOT_IN_STACK for n in small_nolayer.payload['notes'])
    layers = small_base.payload['inputs']['layers']
    assert all(len(v['files_sha256']) == 64 and v['n_files'] == 3 for v in layers.values())
    assert [f['layer'] for f in small_base.alignment['layer_inputs']['reference']['files']] == ['L1', 'L2', 'L3']
    assert not [w for w in small_base.payload['warnings'] if w['code'].startswith('alignment_report_layer')]
    assert small_base.alignment['layer_inputs']['reference']['files'][0]['sha256'] != ''


def test_end_to_end_without_a_stack_order_in_the_config_uses_the_file_numbers(small, monkeypatch, tmp_path_factory):
    monkeypatch.setattr(change_candidates, '_figure_stack_order', lambda sides: None)      # 설정에 layer_stack 이 없는 경우
    run = make(small.world, tmp_path_factory, dl1=small.v2)
    html = (run.out / 'alignment.html').read_text(encoding='utf-8')
    assert 'stack order 출처: <code>파일 번호</code>' in html
    assert not any(n['code'] == ar.LAYER_NOT_IN_STACK for n in run.payload['notes'])


def test_end_to_end_apply_unit_run_shows_the_apply_text_and_unit_offsets(small_apply):
    html = (small_apply.out / 'alignment.html').read_text(encoding='utf-8')
    assert '<td>적용 모드</td>' in html and '--apply-align unit' in html
    assert 'unit offset 4개' in html and html.count('unit [') >= 4
    assert '변경 지도·열지도는 unit offset 기준' in html
    assert any('reference 좌표로 옮김: offset' in c and ('unit offset' in c or '전역 offset' in c) for c in captions(html))


def test_end_to_end_layer_figures_are_skipped_with_a_warning_when_the_layer_read_fails(small, monkeypatch, tmp_path_factory):
    from simcompare.adapters import electrode_png
    real = electrode_png.load_layer_png

    def broken(root, model, frame, layer_fs_rel):
        raise NumericContractError('png_decode_failed', layer_fs_rel)
    monkeypatch.setattr(electrode_png, 'load_layer_png', broken)
    run = make(small.world, tmp_path_factory, dl1=small.v2)
    assert run.code == 3
    html = (run.out / 'alignment.html').read_text(encoding='utf-8')
    assert 'ROI 주변 ·' not in html and 'Layer 그림' not in html
    warning = next(w for w in run.payload['warnings'] if w['code'] == 'alignment_report_layer_figures_skipped')
    assert warning['reason'] == 'png_decode_failed'
    monkeypatch.setattr(electrode_png, 'load_layer_png', real)
