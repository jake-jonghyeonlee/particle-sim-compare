"""stretch-align/T1: 타일 정렬 순수 함수(tile_regions, choose_tile_offsets), 이음새 단차 제한 G1, seam n×m 일반화, 메모리 helper.
좌표: reference 픽셀 p ↔ target 픽셀 p + (dx, dy). 합성 frame 은 border_px=0 인 UnitGeometry(2×2 unit)를 쓴다. 기본 ΔU = 5 px."""
from __future__ import annotations

import numpy as np
import pytest

from simcompare import layer_align as la
from simcompare.change_map import MaskPair, _field_seam, change_maps
from simcompare.numeric_schema import NumericContractError

BIG = 10 ** 12
DELTA_U = 5


def geometry(h, w):
    return la.UnitGeometry((h, w), border_px=0)


def blocks(shape, seed, density=0.3, cell=4):
    rng = np.random.default_rng(seed)
    coarse = rng.random((shape[0] // cell, shape[1] // cell)) < density
    return np.kron(coarse, np.ones((cell, cell), dtype=bool)).astype(bool)


def rect(shape, y0, y1, x0, x1):
    m = np.zeros(shape, bool)
    m[y0:y1, x0:x1] = True
    return m


def warp(ref, pieces):
    """pieces: [(y0, y1, x0, x1, dx, dy)] reference 상자를 (dx, dy) 만큼 옮겨 target 에 놓는다: tgt[y + dy, x + dx] = ref[y, x]."""
    tgt = np.zeros_like(ref)
    h, w = ref.shape
    for y0, y1, x0, x1, dx, dy in pieces:
        for y in range(y0, y1):
            ty = y + dy
            if not 0 <= ty < h:
                continue
            xs = np.arange(x0, x1)
            tx = xs + dx
            ok = (tx >= 0) & (tx < w)
            tgt[ty, tx[ok]] = ref[y, xs[ok]]
    return tgt


def choose(ref, tgt, geom, tile, radius=6, *, s_max=None, unit_offsets=None, **kw):
    tiles = la.tile_regions(geom, tile)
    offsets = unit_offsets or {q: (0, 0) for q in geom.unit_regions()}
    if s_max is None:
        s_max = la.stretch_step_max_px(DELTA_U, tile, min(geom.unit_px))
    ones = np.ones(ref.shape, bool)
    kw.setdefault('min_support', 20)
    return la.choose_tile_offsets(tiles, offsets, {'layer:M1': (ref, tgt)}, ones, ones, radius, min_dice=0.5, s_max=s_max, **kw)


def maps(ref, tgt, field):
    ones = np.ones(ref.shape, bool)
    return change_maps([MaskPair('c', 'M1', ref, tgt)], ref_valid=ones, tgt_valid=ones, offset_field=field, pitch_um=0.1,
                       tolerance_um=0.0, max_operation_bytes=BIG, window_um=5.0)


def tile_by_index(result, ux, uy, tx, ty):
    for rec in result['tiles']:
        if rec['unit_index_xy'] == [ux, uy] and rec['tile_index_xy'] == [tx, ty]:
            return rec
    raise KeyError((ux, uy, tx, ty))


# --------------------------------------------------------------------------- tile_regions

def test_tile_regions_grid_remainder_merge_and_order():
    g = geometry(200, 200)                       # unit 100 × 100
    tiles = la.tile_regions(g, 40)               # 100 = 40 + 40 + 20: 나머지 20·2 = 40 은 T 미만이 아니므로 새 타일
    first = [t for t in tiles if t['unit_index'] == (0, 0)]
    assert [t['region'] for t in first[:3]] == [(0, 40, 0, 40), (0, 40, 40, 80), (0, 40, 80, 100)]
    assert len(first) == 9
    tiles = la.tile_regions(g, 45)               # 100 = 45 + 45 + 10 → 10·2 < 45: 앞 타일에 합친다
    first = [t for t in tiles if t['unit_index'] == (0, 0)]
    assert [t['region'] for t in first] == [(0, 45, 0, 45), (0, 45, 45, 100), (45, 100, 0, 45), (45, 100, 45, 100)]
    assert [t['unit_index'] for t in tiles] == sorted(t['unit_index'] for t in tiles)      # unit 순
    assert [t['tile_index'] for t in first] == [(0, 0), (0, 1), (1, 0), (1, 1)]            # 행, 열
    # 타일은 unit 경계를 넘지 않고 frame 을 정확히 분할한다
    cover = np.zeros((200, 200), int)
    regions = g.unit_regions()
    for t in tiles:
        y0, y1, x0, x1 = t['region']
        uy0, uy1, ux0, ux1 = regions[t['unit_index']]
        assert uy0 <= y0 < y1 <= uy1 and ux0 <= x0 < x1 <= ux1
        cover[y0:y1, x0:x1] += 1
    assert cover.min() == cover.max() == 1
    assert la.tile_regions(g, 40) == la.tile_regions(g, 40)
    assert len(la.tile_regions(g, 1000)) == 4      # tile 이 unit 보다 크면 unit 당 하나
    with pytest.raises(ValueError):
        la.tile_regions(g, 0)


def test_step_limit_and_tolerance_helpers():
    assert la.stretch_step_max_px(5, 400, 2000) == 2          # ceil(1.0) + 1
    assert la.stretch_step_max_px(5, 400, 800) == 4           # ceil(2.5) + 1
    assert la.stretch_step_max_px(0, 400, 800) == 1
    assert la.stretch_step_within_tolerance(10, 5) and not la.stretch_step_within_tolerance(11, 5)
    assert la.MAX_STRETCH_RADIUS_PX == 60


# --------------------------------------------------------------------------- 늘어난 target

def test_stretched_target_tile_delta_matches_cumulative_shift_without_residual():
    shape = (80, 320)                            # unit 40 × 160, 타일 40 → unit 당 1 × 4
    g = geometry(*shape)
    ref = blocks(shape, 1)
    pieces = [(0, 80, 40 * c, 40 * c + 40, c, 0) for c in range(8)]       # 열 타일 c 가 c px 밀림(선 간격 +1 px 누적)
    tgt = warp(ref, pieces)
    unit_offsets = {(0, 0): (0, 0), (1, 0): (0, 0), (0, 1): (4, 0), (1, 1): (4, 0)}      # unit 정렬이 이미 4 px 를 잡았다
    result = choose(ref, tgt, g, 40, 6, unit_offsets=unit_offsets)
    for rec in result['tiles']:
        c = rec['unit_index_xy'][0] * 4 + rec['tile_index_xy'][0]
        assert rec['status'] == 'ok' and rec['offset_source'] == 'stretch', rec
        assert rec['offset_xy_px'] == [c, 0]
        assert rec['delta_xy_px'] == [c - 4 * rec['unit_index_xy'][0], 0]
    assert result['step_replaced_tiles'] == 0
    out = maps(ref, tgt, result['field'])
    assert int(out['maps']['M1'].sum()) == 0                   # 잔차 없음
    assert out['stats']['seam_excluded_pixels'] > 0            # 이음새는 단차 1 px 폭으로 제외된다
    ones = np.ones(shape, bool)
    bare = change_maps([MaskPair('c', 'M1', ref, tgt)], ref_valid=ones, tgt_valid=ones, offset_px=(0, 0), pitch_um=0.1,
                       tolerance_um=0.0, max_operation_bytes=BIG, window_um=5.0)
    assert int(bare['maps']['M1'].sum()) > 1000                # 정렬이 없으면 밀림이 그대로 잔차


# --------------------------------------------------------------------------- 선폭 변화: 타일 내부·이음새·4 타일 모서리

def _line_scene(cd_box_ref, cd_box_tgt, shifts, shape=(80, 160), seed=3, clear=None):
    """배경 blocks 를 타일 열마다 shifts[tx] = (dx, dy) 만큼 옮겨 target 으로 만들고, 선(상자)을 ref/target 에 따로 그린다."""
    ref = blocks(shape, seed)
    if clear is not None:
        ref[clear[0]:clear[1], clear[2]:clear[3]] = False
    ref_bg = ref.copy()
    pieces = [(ty * 40, ty * 40 + 40, tx * 40, tx * 40 + 40, *shifts.get((ty, tx), (0, 0)))
              for ty in range(shape[0] // 40) for tx in range(shape[1] // 40)]
    tgt = warp(ref_bg, pieces)
    y0, y1, x0, x1 = cd_box_ref
    ref[y0:y1, x0:x1] = True
    y0, y1, x0, x1 = cd_box_tgt
    tgt[y0:y1, x0:x1] = True
    return ref, tgt


def test_line_width_change_inside_tile_is_not_erased():
    shape = (80, 160)
    g = geometry(*shape)
    ref = blocks(shape, 3)
    ref[8:32, 10:30] = False
    tgt = ref.copy()
    ref[10:30, 15:25] = True
    tgt[10:30, 14:26] = True
    result = choose(ref, tgt, g, 40, 6)
    assert all(rec['offset_xy_px'] == [0, 0] for rec in result['tiles'])
    res = maps(ref, tgt, result['field'])['maps']['M1']
    assert int(res[10:30].sum()) == 2 * 20                      # 행마다 양쪽 가장자리 1 px, 20 행: 지워지지 않는다


def test_line_width_change_on_seam_astra_counterexample_scale():
    """경계 x=400 에 걸친 선 [395,405) → [394,406), T=400, R=12: 왼 타일은 -1, 오른 타일은 +1 을 고른다(단차 2)."""
    shape = (400, 1600)                          # unit 200 × 800 → unit 당 1 × 2 타일(T=400)
    g = geometry(*shape)
    ref = blocks(shape, 7)
    ref[40:160, :] = False                       # 선이 지나가는 행에는 배경이 없다(배경은 위·아래 띠에만)
    pieces = [(0, 400, 0, 400, -1, 0), (0, 400, 400, 800, 1, 0), (0, 400, 800, 1200, 0, 0), (0, 400, 1200, 1600, 0, 0)]
    tgt = warp(ref, pieces)
    ref[50:150, 395:405] = True                  # 선(unit 0 행, 타일 경계 x=400 에 걸침)
    tgt[50:150, 394:406] = True
    s_max = la.stretch_step_max_px(DELTA_U, 400, 800)
    assert s_max == 4
    ok = choose(ref, tgt, g, 400, 12, s_max=s_max)
    left, right = tile_by_index(ok, 0, 0, 0, 0), tile_by_index(ok, 0, 0, 1, 0)
    assert left['offset_xy_px'] == [-1, 0] and right['offset_xy_px'] == [1, 0]
    step = max(abs(left['offset_xy_px'][0] - right['offset_xy_px'][0]), abs(left['offset_xy_px'][1] - right['offset_xy_px'][1]))
    assert step == 2 <= s_max and ok['step_replaced_tiles'] == 0
    visible = maps(ref, tgt, ok['field'])['maps']['M1'][50:150, :]
    erased = 2 - int(visible.sum()) // 100                     # 행마다 보이는 잔차 폭: CD 변화 2 에서 줄어든 만큼이 지워진 양
    assert erased == 2 <= s_max
    # G1: 같은 장면에서 s_max = 1 이면 두 타일이 unit offset 으로 대체되어 CD 변화가 그대로 남는다.
    tight = choose(ref, tgt, g, 400, 12, s_max=la.stretch_step_max_px(0, 400, 800))
    for rec in (tile_by_index(tight, 0, 0, 0, 0), tile_by_index(tight, 0, 0, 1, 0)):
        assert rec['offset_xy_px'] == [0, 0] and 'stretch_step_exceeded' in rec['flags'] and rec['offset_source'] == 'unit'
    visible = maps(ref, tgt, tight['field'])['maps']['M1'][50:150, :]
    assert 2 - int(visible.sum()) // 100 == 0 and int(visible.sum()) == 2 * 100


def test_line_width_change_on_seam_small_scale_and_g1_off_mutation():
    shape = (80, 160)
    g = geometry(*shape)
    shifts = {(ty, tx): ((-1, 0) if tx % 2 == 0 else (1, 0)) for ty in range(2) for tx in range(4)}
    ref, tgt = _line_scene((10, 30, 35, 45), (10, 30, 34, 46), shifts, clear=(8, 32, 30, 50))
    ok = choose(ref, tgt, g, 40, 6)                                           # s_max = 4 >= 단차 2
    assert tile_by_index(ok, 0, 0, 0, 0)['offset_xy_px'] == [-1, 0] and tile_by_index(ok, 0, 0, 1, 0)['offset_xy_px'] == [1, 0]
    assert 'stretch_step_exceeded' not in tile_by_index(ok, 0, 0, 0, 0)['flags']
    assert int(maps(ref, tgt, ok['field'])['maps']['M1'][10:30, 30:50].sum()) == 0   # 단차 2 <= s_max: 지워지는 양 2 <= s_max
    tight_max = la.stretch_step_max_px(0, 40, 80)
    tight = choose(ref, tgt, g, 40, 6, s_max=tight_max)                       # 단차 2 > 1
    assert tight['step_replaced_tiles'] >= 2
    assert tile_by_index(tight, 0, 0, 0, 0)['offset_xy_px'] == [0, 0] and 'stretch_step_exceeded' in tile_by_index(tight, 0, 0, 0, 0)['flags']
    assert int(maps(ref, tgt, tight['field'])['maps']['M1'][10:30, 30:50].sum()) == 2 * 20      # CD 변화 2 px × 20 행이 남는다
    # 변형: G1 을 끄면(s_max 를 크게) 같은 장면에서 이 단정이 깨진다.
    off = choose(ref, tgt, g, 40, 6, s_max=10 ** 6)
    assert off['step_replaced_tiles'] == 0 and int(maps(ref, tgt, off['field'])['maps']['M1'][10:30, 30:50].sum()) == 0


def test_line_width_change_at_four_tile_corner():
    shape = (160, 160)                           # unit 80 × 80, 타일 40 → unit 당 2 × 2
    g = geometry(*shape)
    shifts = {(ty, tx): ((-1 if tx % 2 == 0 else 1), (-1 if ty % 2 == 0 else 1)) for ty in range(4) for tx in range(4)}
    ref, tgt = _line_scene((35, 45, 35, 45), (34, 46, 34, 46), shifts, shape=shape, clear=(30, 50, 30, 50))
    ok = choose(ref, tgt, g, 40, 6)
    expected = {(0, 0): (-1, -1), (1, 0): (1, -1), (0, 1): (-1, 1), (1, 1): (1, 1)}       # (tx, ty) → (dx, dy)
    for (tx, ty), off in expected.items():
        assert tile_by_index(ok, 0, 0, tx, ty)['offset_xy_px'] == list(off)
    assert ok['step_replaced_tiles'] == 0
    assert int(maps(ref, tgt, ok['field'])['maps']['M1'][30:50, 30:50].sum()) == 0         # 모서리에서는 단차 <= s_max 만큼 지워진다
    tight = choose(ref, tgt, g, 40, 6, s_max=la.stretch_step_max_px(0, 40, 80))
    for (tx, ty) in expected:
        rec = tile_by_index(tight, 0, 0, tx, ty)
        assert rec['offset_xy_px'] == [0, 0] and 'stretch_step_exceeded' in rec['flags']
    assert int(maps(ref, tgt, tight['field'])['maps']['M1'][30:50, 30:50].sum()) == 12 * 12 - 10 * 10     # 가장자리 띠 44 칸


# --------------------------------------------------------------------------- 이음새 띠 안에만 있는 변경

def test_change_only_inside_the_seam_band_is_excluded_and_counted():
    shape = (80, 160)
    ref = np.zeros(shape, bool)
    tgt = np.zeros(shape, bool)
    ref[20:30, 38:42] = True                     # reference 에만 있는 덩어리가 이음새 띠 [38, 42) 안에 있다
    ref[20:30, 100:104] = True                   # 같은 모양을 띠 밖(타일 내부)에 두면 잔차로 센다
    field = [(0, 40, 0, 40, -1, 0), (0, 40, 40, 80, 1, 0), (0, 40, 80, 160, 0, 0),
             (40, 80, 0, 40, -1, 0), (40, 80, 40, 80, 1, 0), (40, 80, 80, 160, 0, 0)]
    out = maps(ref, tgt, field)
    res = out['maps']['M1']
    assert int(res[20:30, 36:44].sum()) == 0                   # 띠 안의 실제 변경은 잔차에 없다
    assert int(res[20:30, 98:106].sum()) == 40                 # 띠 밖 같은 변경은 남는다
    # 이음새 폭 2 인 [38, 42) × 80 행 + 폭 1 인 [79, 81) × 80 행 = 320 + 160 칸이 비교 영역에서 빠진다.
    assert _field_seam(tuple(sorted(field)), shape).sum() == 4 * 80 + 2 * 80
    assert out['stats']['seam_excluded_pixels'] == 4 * 80 + 2 * 80


# --------------------------------------------------------------------------- PXL 큰 사각형(L4)

def test_pxl_big_rectangle_width_growth_leaves_edge_residual():
    shape = (80, 160)
    g = geometry(*shape)
    ref = blocks(shape, 11, density=0.0)
    ref[10:70, 5:35] = True                       # 큰 사각형(타일 40 안에 두 가장자리)
    for x in (100, 112, 124, 136, 148):           # 선폭 고정 선(변하지 않음)
        ref[5:75, x:x + 3] = True
    tgt = ref.copy()
    tgt[10:70, 3:37] = True                       # 폭 30 → 34 (+4): 한 타일 안의 두 가장자리가 반대로 움직인다
    for tile in (40, 20):
        result = choose(ref, tgt, g, tile, 6)
        assert all(rec['offset_xy_px'] == [0, 0] for rec in result['tiles'])     # 폭 변화는 Dice 평탄부라 모호/대체 → unit offset
        res = maps(ref, tgt, result['field'])['maps']['M1'][10:70, :40]
        # 강체 타일 이동은 폭 차이 k=4 를 행마다 4 칸 미만으로 줄이지 못한다: 60 행 × 4 = 240 (T=40, T=20 모두; L4)
        assert int(res.sum()) == 4 * 60, tile


# --------------------------------------------------------------------------- 대체: 빈 타일, 모호, 거짓 peak, global

def _periodic(shape, pitch=8, missing=None):
    ref = np.zeros(shape, bool)
    for x in range(4, shape[1], pitch):
        ref[:, x:x + 3] = True
    for y in (10, 50):
        ref[y:y + 12, :] = True
    tgt = ref.copy()
    if missing is not None:
        tgt[0:40, missing:missing + 3] = False
    return ref, tgt


def test_empty_tile_falls_back_to_unit_offset_insufficient_support():
    shape = (80, 160)
    g = geometry(*shape)
    ref = blocks(shape, 2)
    ref[0:40, 0:40] = False                       # 타일 (unit(0,0), tx=0, ty=0) 이 비었다
    result = choose(ref, ref.copy(), g, 40, 6)
    rec = tile_by_index(result, 0, 0, 0, 0)
    assert rec['status'] == 'insufficient_support' and rec['applied'] is False
    assert rec['offset_xy_px'] == [0, 0] and 'stretch_tile_fallback' in rec['flags'] and rec['offset_source'] == 'unit'


def test_parallel_lines_with_pitch_below_radius_are_ambiguous_and_fall_back():
    shape = (80, 160)
    g = geometry(*shape)
    ref, tgt = _periodic(shape, pitch=8)
    result = choose(ref, tgt, g, 40, 10)           # R = 10 > p = 8
    assert {rec['status'] for rec in result['tiles']} == {'ambiguous'}
    assert all(rec['offset_xy_px'] == [0, 0] and 'stretch_tile_fallback' in rec['flags'] for rec in result['tiles'])


def test_false_periodic_peak_from_a_missing_line_is_blocked_by_g1():
    shape = (80, 160)
    g = geometry(*shape)
    ref, tgt = _periodic(shape, pitch=8, missing=44)     # tile (0, tx=1) 의 첫 선이 target 에 없다 → δx = +8 이 유일한 최고
    s_max = la.stretch_step_max_px(DELTA_U, 40, 40)
    assert s_max == 6
    result = choose(ref, tgt, g, 40, 10, s_max=s_max)
    rec = tile_by_index(result, 0, 0, 1, 0)
    assert rec['candidate_offset_xy_px'] == [8, 0] and rec['status'] == 'ok'     # 독립 선택은 거짓 peak 를 고른다
    assert rec['offset_xy_px'] == [0, 0] and 'stretch_step_exceeded' in rec['flags']      # 이웃(모호 → unit offset) 과 단차 8 > 6
    assert rec['offset_source'] == 'unit' and result['step_replaced_tiles'] == 1
    off = choose(ref, tgt, g, 40, 10, s_max=10 ** 6)                              # 변형: G1 끄기 → 거짓 peak 가 적용된다
    assert tile_by_index(off, 0, 0, 1, 0)['offset_xy_px'] == [8, 0]


def test_tile_offset_source_marks_global_for_failed_unit():
    shape = (80, 160)
    g = geometry(*shape)
    ref = blocks(shape, 2)
    ref[0:40, 0:40] = False                       # unit (0,0) 의 한 타일은 대체 대상
    tgt = warp(ref, [(0, 80, 80, 160, 1, 0), (0, 80, 0, 80, 0, 0)])             # unit (·,1) 은 +1 로 밀렸다
    sources = {(0, 0): 'global', (1, 0): 'unit', (0, 1): 'unit', (1, 1): 'unit'}
    result = choose(ref, tgt, g, 40, 6, unit_sources=sources, global_offset=(0, 0))
    fb = tile_by_index(result, 0, 0, 0, 0)
    assert fb['offset_source'] == 'global' and fb['offset_xy_px'] == [0, 0] and fb['delta_from_global_xy_px'] == [0, 0]
    stretched = tile_by_index(result, 1, 0, 0, 0)
    assert stretched['status'] == 'ok' and stretched['offset_source'] == 'stretch' and stretched['offset_xy_px'] == [1, 0]
    assert stretched['delta_from_global_xy_px'] == [1, 0] and stretched['delta_xy_px'] == [1, 0]


# --------------------------------------------------------------------------- seam n×m

def _legacy_field_seam(field, shape):
    """기준선 ba2c990 의 ``_field_seam``(1×1·1×2·2×1·2×2 전용)을 그대로 옮긴 사본. 일반화한 구현이 이와 배열 동일해야 한다."""
    rows = sorted({(f[0], f[1]) for f in field})
    cols = sorted({(f[2], f[3]) for f in field})
    assert len(rows) <= 2 and len(cols) <= 2 and len(field) == len(rows) * len(cols)
    height, width = shape
    pad_rows, pad_cols = len(rows) == 1, len(cols) == 1
    if pad_rows:
        rows.append((height, height))
    if pad_cols:
        cols.append((width, width))
    offsets = {}
    for ry0, ry1, rx0, rx1, dx, dy in field:
        offsets[(rows.index((ry0, ry1)), cols.index((rx0, rx1)))] = (dx, dy)
    for iy in (0, 1):
        for ix in (0, 1):
            if (iy, ix) not in offsets:
                offsets[(iy, ix)] = offsets[(0 if pad_rows else iy, 0 if pad_cols else ix)]
    regions = {(iy, ix): (rows[iy][0], rows[iy][1], cols[ix][0], cols[ix][1]) for iy in (0, 1) for ix in (0, 1)}
    h, wd = height, width
    mask = np.zeros((h, wd), dtype=bool)
    for (iy, ix), (y0, y1, x0, x1) in regions.items():
        if ix == 0:
            w = max(abs(offsets[(iy, 0)][0] - offsets[(iy, 1)][0]), abs(offsets[(iy, 0)][1] - offsets[(iy, 1)][1]))
            if w:
                mask[y0:y1, max(x1 - w, 0):min(x1 + w, wd)] = True
        if iy == 0:
            w = max(abs(offsets[(0, ix)][0] - offsets[(1, ix)][0]), abs(offsets[(0, ix)][1] - offsets[(1, ix)][1]))
            if w:
                mask[max(y1 - w, 0):min(y1 + w, h), x0:x1] = True
    return mask


def _grid_field(row_edges, col_edges, offsets):
    return [(ry0, ry1, cx0, cx1, offsets[(i, j)][0], offsets[(i, j)][1])
            for i, (ry0, ry1) in enumerate(row_edges) for j, (cx0, cx1) in enumerate(col_edges)]


def _expected_seam(field, shape):
    """독립 계산: 모든 인접 영역 쌍(변을 맞댄 쌍)마다 폭 = 체비쇼프 차 의 띠."""
    mask = np.zeros(shape, bool)
    for a in field:
        for b in field:
            if a[1] == b[0] and a[2] == b[2] and a[3] == b[3]:       # b 가 a 바로 아래
                w = max(abs(a[4] - b[4]), abs(a[5] - b[5]))
                if w:
                    mask[max(a[1] - w, 0):min(a[1] + w, shape[0]), a[2]:a[3]] = True
            if a[3] == b[2] and a[0] == b[0] and a[1] == b[1]:       # b 가 a 바로 오른쪽
                w = max(abs(a[4] - b[4]), abs(a[5] - b[5]))
                if w:
                    mask[a[0]:a[1], max(a[3] - w, 0):min(a[3] + w, shape[1])] = True
    return mask


def test_seam_small_grids_are_array_identical_to_the_baseline():
    shape = (60, 90)
    rng = np.random.default_rng(0)
    layouts = {'1x1': ([(0, 60)], [(0, 90)]), '1x2': ([(0, 60)], [(0, 30), (30, 90)]), '2x1': ([(0, 25), (25, 60)], [(0, 90)]),
               '2x2': ([(0, 25), (25, 60)], [(0, 30), (30, 90)])}
    for name, (rows, cols) in layouts.items():
        for _ in range(20):
            offsets = {(i, j): (int(rng.integers(-4, 5)), int(rng.integers(-4, 5))) for i in range(len(rows)) for j in range(len(cols))}
            field = tuple(sorted(_grid_field(rows, cols, offsets)))
            got = _field_seam(field, shape)
            assert got.dtype == bool and np.array_equal(got, _legacy_field_seam(field, shape)), (name, offsets)
        same = tuple(sorted(_grid_field(rows, cols, {k: (2, -1) for k in offsets})))
        assert not _field_seam(same, shape).any()


def test_seam_3x3_non_square_and_large_steps():
    shape = (60, 90)
    rng = np.random.default_rng(4)
    for rows, cols in (([(0, 20), (20, 40), (40, 60)], [(0, 30), (30, 60), (60, 90)]),                 # 3×3
                       ([(0, 20), (20, 60)], [(0, 15), (15, 40), (40, 70), (70, 90)]),                 # 2×4 비정방
                       ([(0, 10), (10, 30), (30, 45), (45, 52), (52, 60)], [(0, 90)])):                # 5×1
        for _ in range(15):
            offsets = {(i, j): (int(rng.integers(-3, 4)), int(rng.integers(-3, 4))) for i in range(len(rows)) for j in range(len(cols))}
            field = tuple(sorted(_grid_field(rows, cols, offsets)))
            assert np.array_equal(_field_seam(field, shape), _expected_seam(field, shape))
    rows, cols = [(0, 20), (20, 40), (40, 60)], [(0, 30), (30, 60), (60, 90)]
    offsets = {(i, j): (0, 0) for i in range(3) for j in range(3)}
    offsets[(1, 1)] = (25, -25)                                  # 큰 단차: 띠 폭 25 가 타일(20, 30)보다 커서 이웃 칸으로 번진다
    field = tuple(sorted(_grid_field(rows, cols, offsets)))
    seam = _field_seam(field, shape)
    assert np.array_equal(seam, _expected_seam(field, shape))
    assert seam[20:40, 5:55].all() and seam[20:40, 35:85].all()      # 가운데 칸 좌우 경계: 폭 25 가 양쪽 칸으로 번진다
    assert seam[0:45, 30:60].all() and seam[15:60, 30:60].all()      # 위아래 경계(프레임 밖은 잘림)
    assert not seam[0:20, 0:5].any()


def test_seam_from_remainder_merged_tiles_and_non_grid_rejected():
    g = geometry(100, 100)                                      # unit 50 × 50, 타일 22 → 22 + 28 (나머지 6 < 11 → 합침)
    tiles = la.tile_regions(g, 22)
    assert [t['region'] for t in tiles if t['unit_index'] == (0, 0)] == [(0, 22, 0, 22), (0, 22, 22, 50), (22, 50, 0, 22), (22, 50, 22, 50)]
    field = tuple(sorted((*t['region'], i % 3 - 1, 0) for i, t in enumerate(tiles)))
    seam = _field_seam(field, (100, 100))
    assert np.array_equal(seam, _expected_seam(field, (100, 100)))
    bad = ((0, 10, 0, 20, 0, 0), (10, 20, 0, 10, 0, 0), (0, 10, 20, 30, 0, 0), (10, 20, 10, 30, 0, 0), (20, 30, 0, 30, 0, 0))   # 영역 수 5 ≠ 행 3 × 열 4
    with pytest.raises(NumericContractError) as err:
        _field_seam(bad, (30, 30))
    assert err.value.code == 'invalid_offset_field'


# --------------------------------------------------------------------------- 실제 변경이 이음새 안: 영역 수 격자 일반화 경로

def test_change_maps_accepts_tile_grid_field_with_seam_count():
    shape = (80, 160)
    g = geometry(*shape)
    ref = blocks(shape, 5)
    field = [t['region'] + (0, 0) for t in la.tile_regions(g, 40)]
    field[1] = field[1][:4] + (3, 0)                              # 한 타일만 다른 offset → 이음새 폭 3
    tgt = ref.copy()
    out = change_maps([MaskPair('c', 'M1', ref, tgt)], ref_valid=np.ones(shape, bool), tgt_valid=np.ones(shape, bool),
                      offset_field=field, pitch_um=0.1, tolerance_um=0.0, max_operation_bytes=BIG, window_um=5.0)
    expected_seam = _expected_seam(tuple(sorted(field)), shape)
    assert out['stats']['seam_excluded_pixels'] <= int(expected_seam.sum())
    assert out['stats']['seam_excluded_pixels'] > 0


# --------------------------------------------------------------------------- 메모리 admission

def test_estimate_and_rejection_before_any_large_allocation(monkeypatch):
    small = la.estimate_stretch_bytes((40, 40), 6, 2)
    big = la.estimate_stretch_bytes((400, 400), 60, 20, n_tiles=64, frame_shape=(3000, 3000))
    assert 0 < small < big
    assert la.estimate_stretch_bytes((400, 400), 60, 20) > la.estimate_stretch_bytes((400, 400), 12, 20)      # R=60 이 R=12 보다 크다
    expected = (la._fast_len(400 + 121) ** 2) * 16 * 5 + 4 * 400 * 400 + 2 * 520 * 520 + 20 * 3 * 121 ** 2 * 8
    assert la.estimate_stretch_bytes((400, 400), 60, 20) == expected
    g = geometry(80, 160)
    ref = blocks((80, 160), 1)
    calls = []
    monkeypatch.setattr(la, 'fixed_omega', lambda *a, **k: calls.append('omega') or None)
    monkeypatch.setattr(la, 'region_window_scores', lambda *a, **k: calls.append('scores') or None)
    ones = np.ones(ref.shape, bool)
    tiles = la.tile_regions(g, 40)
    need = la.estimate_stretch_bytes((40, 40), 6, 1, n_tiles=len(tiles), frame_shape=ref.shape, n_starts=1)
    with pytest.raises(NumericContractError) as err:
        la.choose_tile_offsets(tiles, {q: (0, 0) for q in g.unit_regions()}, {'layer:M1': (ref, ref)}, ones, ones, 6,
                               min_dice=0.5, s_max=4, max_operation_bytes=need - 1)
    assert err.value.code == 'oversized_real_operation' and calls == []                    # 할당 전에 거부
    with pytest.raises(NumericContractError):
        la.choose_tile_offsets(tiles, {q: (0, 0) for q in g.unit_regions()}, {'layer:M1': (ref, ref)}, ones, ones, 6,
                               min_dice=0.5, s_max=4, max_operation_bytes=need + 10, reserved_bytes=11)
    assert calls == []
    with pytest.raises(ValueError):
        la.choose_tile_offsets(tiles, {q: (0, 0) for q in g.unit_regions()}, {'layer:M1': (ref, ref)}, ones, ones,
                               la.MAX_STRETCH_RADIUS_PX + 1, min_dice=0.5, s_max=4)


@pytest.mark.parametrize('radius', [7, 60])
def test_admission_estimate_bounds_the_real_peak(radius):
    import tracemalloc
    shape = (2000, 2000)
    g = geometry(*shape)                                   # unit 1000 × 1000, 타일 1000 → unit 당 1 타일
    ref = blocks(shape, 5, cell=8)
    tgt = np.roll(ref, 1, axis=1)
    ones = np.ones(shape, bool)
    starts = {(0, 0): (0, 0), (0, 1): (1, 0), (1, 0): (0, 1), (1, 1): (1, 1)}      # 서로 다른 시작점 4개 → Ω 캐시 4장
    tiles = la.tile_regions(g, 1000)
    need = la.estimate_stretch_bytes((1000, 1000), radius, 1, n_tiles=len(tiles), frame_shape=shape, n_starts=4)
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        base = tracemalloc.get_traced_memory()[0]
        la.choose_tile_offsets(tiles, starts, {'layer:M1': (ref, tgt)}, ones, ones, radius, min_dice=0.5, s_max=4)
        peak = tracemalloc.get_traced_memory()[1] - base
    finally:
        tracemalloc.stop()
    assert 0 < peak <= need, (peak, need)
    assert peak >= 4 * shape[0] * shape[1]                  # 실측이 Ω 캐시 4장 이상을 본다(추정이 터무니없이 크지 않음의 하한)


# --------------------------------------------------------------------------- G1 v1.2: unit 경계 쌍·다른 unit offset·모서리

def _boundary_scene(d):
    shape = (80, 160)                                      # unit 40 × 80, unit 경계 x = 80, 타일 40
    shifts = {(ty, tx): ((-d, 0) if tx < 2 else (d, 0)) for ty in range(2) for tx in range(4)}
    ref, tgt = _line_scene((10, 30, 75, 85), (10, 30, 75 - d, 85 + d), shifts, shape=shape, clear=(8, 32, 60, 100))
    return shape, ref, tgt


@pytest.mark.parametrize('d', [1, 3])
def test_g1_checks_pairs_across_the_unit_boundary(d):
    shape, ref, tgt = _boundary_scene(d)
    g = geometry(*shape)
    s_max = 1
    got = choose(ref, tgt, g, 40, 6, s_max=s_max)
    left, right = tile_by_index(got, 0, 0, 1, 0), tile_by_index(got, 1, 0, 0, 0)       # unit 경계 x=80 양쪽 타일
    resid = int(maps(ref, tgt, got['field'])['maps']['M1'][10:30, 60:100].sum())
    erased = (2 * d * 20 - resid) / 20                                                # 행마다 지워진 CD 변화
    assert erased <= s_max, (erased, left['offset_xy_px'], right['offset_xy_px'])
    # 단차 2d > s_max 이므로 stretch 로 적용된 쪽은 unit offset 으로 대체되고 CD 변화가 그대로 남는다.
    assert 'stretch_step_exceeded' in left['flags'] or 'stretch_step_exceeded' in right['flags']
    assert got['step_replaced_tiles'] >= 2 and resid == 2 * d * 20
    assert left['offset_xy_px'] == [0, 0] and right['offset_xy_px'] == [0, 0]


def test_g1_checks_unit_boundary_when_each_unit_has_a_single_tile():
    shape = (80, 160)
    g = geometry(*shape)                                   # 타일 80 → unit(40×80) 당 타일 1개
    d = 3
    bg = blocks(shape, 3)
    bg[8:32, 60:100] = False
    tgt = warp(bg, [(0, 80, 0, 80, -d, 0), (0, 80, 80, 160, d, 0)])
    ref = bg.copy()
    ref[10:30, 75:85] = True
    tgt[10:30, 75 - d:85 + d] = True
    assert len(la.tile_regions(g, 80)) == 4
    got = choose(ref, tgt, g, 80, 6, s_max=1)
    assert got['step_replaced_tiles'] >= 2
    assert all(rec['offset_xy_px'] == [0, 0] for rec in got['tiles'])
    assert int(maps(ref, tgt, got['field'])['maps']['M1'][10:30, 60:100].sum()) == 2 * d * 20


def test_g1_limit_across_units_adds_the_unit_offset_difference():
    shape = (80, 160)
    g = geometry(*shape)
    k = 4
    units = {q: ((0, 0) if q[1] == 0 else (k, 0)) for q in g.unit_regions()}
    shifts = {0: 0, 1: 1, 2: 1 + k, 3: 2 + k}              # 타일 열 → dx. 경계 단차 k, 같은 unit 단차 1
    bg = blocks(shape, 3)
    tgt = warp(bg, [(0, 80, 40 * tx, 40 * tx + 40, dx, 0) for tx, dx in shifts.items()])
    got = choose(bg, tgt, g, 40, 6, s_max=1, unit_offsets=units)
    assert got['step_replaced_tiles'] == 0
    assert [tile_by_index(got, ux, 0, tx, 0)['offset_xy_px'][0] for ux, tx in ((0, 0), (0, 1), (1, 0), (1, 1))] == [0, 1, 5, 6]
    # 한도는 k + s_max = 5: 단차 k = 4 는 통과하고, s_max=0 이면 한도 k 만 남아 같은 unit 단차 1 이 걸린다.
    strict = choose(bg, tgt, g, 40, 6, s_max=0, unit_offsets=units)
    assert strict['step_replaced_tiles'] > 0


def _opposed_step_scene():
    """R1b 반례: unit 0 offset (0,0), unit 1 offset (4,0). 타일 열 dx = [3, 3, 1, 1](unit 0 +3, unit 1 +1): 경계 단차 −2, unit 단차 +4."""
    shape = (80, 160)
    g = geometry(*shape)
    units = {q: ((0, 0) if q[1] == 0 else (4, 0)) for q in g.unit_regions()}
    shifts = {(ty, tx): ((3, 0) if tx < 2 else (1, 0)) for ty in range(2) for tx in range(4)}
    ref, tgt = _line_scene((10, 30, 75, 85), (10, 30, 78, 86), shifts, shape=shape, clear=(8, 32, 60, 100))
    return g, units, ref, tgt


def test_g1_box_rejects_a_step_opposite_to_the_unit_step():
    g, units, ref, tgt = _opposed_step_scene()
    got = choose(ref, tgt, g, 40, 6, s_max=1, unit_offsets=units)
    left, right = tile_by_index(got, 0, 0, 1, 0), tile_by_index(got, 1, 0, 0, 0)
    assert got['step_replaced_tiles'] >= 1
    assert 'stretch_step_exceeded' in left['flags'] or 'stretch_step_exceeded' in right['flags']
    resid = int(maps(ref, tgt, got['field'])['maps']['M1'][10:30, 60:100].sum())
    # unit 모드(타일 전체가 unit offset)의 잔차보다 지워진 양이 s_max 를 넘지 않는다.
    unit_field = [(y0, y1, x0, x1, dx, dy) for (y0, y1, x0, x1, dx, dy) in
                  [(0, 80, 0, 80, 0, 0), (0, 80, 80, 160, 4, 0)]]
    unit_resid = int(maps(ref, tgt, unit_field)['maps']['M1'][10:30, 60:100].sum())
    assert resid > 0 and unit_resid - resid <= 1 * 20 and resid - unit_resid <= 1 * 20


def test_g1_box_mutation_old_cheb_size_limit_would_pass_the_opposed_step():
    g, units, ref, tgt = _opposed_step_scene()
    a, b = (3, 0), (1, 0)                                   # 경계 타일 offset
    ua, ub = (0, 0), (4, 0)
    old_ok = max(abs(a[i] - b[i]) for i in (0, 1)) <= max(abs(ua[i] - ub[i]) for i in (0, 1)) + 1     # v1.2: 크기 한도
    du, do = ub[0] - ua[0], b[0] - a[0]
    box_ok = min(0, du) - 1 <= do <= max(0, du) + 1                                                    # v1.3: 상자
    assert old_ok and not box_ok


def _box_scene(axis, do):
    """unit 0 offset 0, unit 1 offset 4(axis 방향), 타일 이동량 [0, 1, 1 + do, 2 + do]: 경계 단차 do, unit 단차 4, 같은 unit 단차 1."""
    shape = (80, 160) if axis == 'x' else (160, 80)
    g = geometry(*shape)
    unit_axis = 1 if axis == 'x' else 0
    units = {q: ((0, 0) if q[unit_axis] == 0 else ((4, 0) if axis == 'x' else (0, 4))) for q in g.unit_regions()}
    steps = [0, 1, 1 + do, 2 + do]
    bg = blocks(shape, 3)
    pieces = []
    for k, d in enumerate(steps):
        pieces.append((0, 80, 40 * k, 40 * k + 40, d, 0) if axis == 'x' else (40 * k, 40 * k + 40, 0, 80, 0, d))
    return g, units, bg, warp(bg, pieces)


@pytest.mark.parametrize('axis', ['x', 'y'])
@pytest.mark.parametrize('do,replaced', [(4, False), (5, False), (3, False), (-1, False), (6, True), (-2, True)])
def test_g1_box_same_direction_inside_and_outside(axis, do, replaced):
    """s_max = 1, du = 4: 상자 min(0, 4) − 1 ≤ do ≤ max(0, 4) + 1 = [−1, 5]. 밖이면 stretch 타일이 unit offset 으로 대체된다."""
    g, units, bg, tgt = _box_scene(axis, do)
    got = choose(bg, tgt, g, 40, 6, s_max=1, unit_offsets=units)
    assert (got['step_replaced_tiles'] > 0) == replaced
    assert replaced == any('stretch_step_exceeded' in rec['flags'] for rec in got['tiles'])
    if not replaced:
        if axis == 'x':
            a, b = tile_by_index(got, 0, 0, 1, 0), tile_by_index(got, 1, 0, 0, 0)
        else:
            a, b = tile_by_index(got, 0, 0, 0, 1), tile_by_index(got, 0, 1, 0, 0)
        assert [a['offset_xy_px'][0 if axis == 'x' else 1], b['offset_xy_px'][0 if axis == 'x' else 1]] == [1, 1 + do]


def test_g1_four_tile_corner_reproduced_diagonal_step_equals_s_max():
    """재현된 모서리 대각 단차는 s_max(= 2)이다. 이론상 최악 2·s_max(≤ 4r)는 이웃 쌍 단차가 s_max 씩 같은 방향으로 이어질 때이며 문서 한계다(재현 사례 없음)."""
    shape = (160, 160)
    g = geometry(*shape)
    shifts = {(ty, tx): ((-1 if tx % 2 == 0 else 1), (-1 if ty % 2 == 0 else 1)) for ty in range(4) for tx in range(4)}
    ref, tgt = _line_scene((35, 45, 35, 45), (34, 46, 34, 46), shifts, shape=shape, clear=(30, 50, 30, 50))
    s_max = 2
    got = choose(ref, tgt, g, 40, 6, s_max=s_max)
    a, b = tile_by_index(got, 0, 0, 0, 0)['offset_xy_px'], tile_by_index(got, 0, 0, 1, 1)['offset_xy_px']
    diagonal = max(abs(a[0] - b[0]), abs(a[1] - b[1]))
    resid = int(maps(ref, tgt, got['field'])['maps']['M1'][30:50, 30:50].sum())
    erased = (12 * 12 - 10 * 10) - resid                    # 지워진 칸. 한 축 CD 변화 2 는 대각 단차(= 2) 만큼 지워진다.
    assert got['step_replaced_tiles'] == 0 and diagonal == s_max
    assert erased == 44 and resid == 0


# --------------------------------------------------------------------------- 질의별 seam_excluded_pixels (adv:2)

def test_window_seam_pixels_sum_to_the_global_count_with_partial_valid():
    from simcompare import change_candidates
    shape = (20, 80)
    field = [(0, 20, 0, 40, 0, 0), (0, 20, 40, 80, 2, 0)]            # 경계 x=40 양쪽 폭 2
    ref_valid = np.ones(shape, bool)
    ref_valid[0:5, :] = False                                         # 일부 영역은 비교 domain 이 아니다
    tgt_valid = np.ones(shape, bool)
    tgt_valid[:, 38:41] = False
    ref = np.zeros(shape, bool)
    out = change_maps([MaskPair('c', 'M1', ref, ref)], ref_valid=ref_valid, tgt_valid=tgt_valid, offset_field=field, pitch_um=0.1,
                      tolerance_um=0.0, max_operation_bytes=BIG, window_um=5.0)
    glob = out['stats']['seam_excluded_pixels']
    mask = change_candidates._stretch_seam_mask(field, ref_valid, tgt_valid)
    assert int(mask.sum()) == glob < int(_field_seam(tuple(sorted(field)), shape).sum())
    total = sum(change_candidates._stretch_window_fields({'window_origin_px': (0, x), 'window_px': 20}, field, mask)['seam_excluded_pixels']
                for x in range(0, 80, 20))
    assert total == glob
