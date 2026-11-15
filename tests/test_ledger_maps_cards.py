"""lmap/T4: range totals cards of `ledger-maps` (lines, tile ownership, A-B correspondence via 04 fields, edge repeat,
checks 3-5).

Two layers of tests. (1) The pure line functions (`plan_rows`, `place_rows`, `pick_offset`, `_tile_shift`, ...) get hand-made
tile arrays, so the numbers of README 18.4 (300 um line, 70 um segments, ...) are tested exactly. (2) `ledger-maps` itself runs
on small real_hotspot_v1 fixtures with a bigger frame (0.1 um pixel pitch, 0.5 um tiles = 5 px) and hand-written 04 folders."""
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml
from PIL import Image

from simcompare import ledger_maps as lm
from simcompare.adapters.real_hotspot import import_hotspots
from simcompare.candidates import _canonical_sha
from simcompare.numeric_schema import DIAMETERS_UM
import test_ledger_maps as base
from test_compare2 import CELL


def opts(**kw):
    return lm.validate_options(kw)


def ids(points, ntx):
    """tile ids of (tx, ty) points, sorted."""
    return np.array(sorted(ty * ntx + tx for tx, ty in points), dtype=np.int64)


def block(x0, y0, x1, y1, ntx):
    return ids([(x, y) for x in range(x0, x1) for y in range(y0, y1)], ntx)


NO_EDGE = {'enabled': True, 'reason': 'coords_resolution_too_coarse', 'shift': None, 'min_match': 0.98,
           'equiv': lambda clipped, shift: None}


# ---- (1) the pure line functions ---------------------------------------------------------------------------------------

def test_linear_300um_line_with_70um_segments_gives_five_rows_60um_apart_and_no_duplicate_value():
    ntx, nty = 400, 40
    member = block(50, 20, 350, 26, ntx)                       # 300 x 6 tiles of 1 um
    vt, vv = member, np.ones(len(member))
    plan = lm.plan_rows(member, vt, vv, ntx, 1.0, (0, 0, ntx, nty), opts(segment_um=70.0))
    assert plan['linear'] and plan['others_n'] == 0
    rows = plan['rows']
    assert [r['type'] for r in rows] == ['linear'] * 5
    # cuts 50, 110, ... 350 (60 um apart); only the two ends of the component grow by the range (10 um)
    assert [r['box'][0] for r in rows] == [40, 110, 170, 230, 290]
    assert [r['box'][2] for r in rows] == [110, 170, 230, 290, 360]
    assert all(r['box'][1] == 10 and r['box'][3] == 36 for r in rows)      # short axis: 20 - 10 .. 26 + 10
    placed = lm.place_rows(rows, ntx, nty, NO_EDGE)
    assert [r['lost'] for r in placed] == [0] * 5 and all(r['edge'] == 'none' for r in placed)
    owner = lm._owner_index(placed)
    sums, _hits = zip(*[lm._row_sums(owner, vt, vv, 5)] * 1)
    per_row = sums[0]
    # a segment holds 60 x 6 tiles of value 1; the first/last also hold nothing extra (no component tile outside the cuts)
    assert per_row.tolist() == [360.0] * 5
    assert per_row.sum() == len(member)                         # the rows add up to the pair's tiles once


def test_eight_points_with_max_rows_6_give_six_rows_and_the_other_two_places():
    ntx, nty = 200, 60
    spots = [(20 + 25 * k, 30) for k in range(8)]              # 8 single tiles, 25 um apart
    member = ids(spots, ntx)
    values = {sp[0] + sp[1] * ntx: float(k + 1) for k, sp in enumerate(spots)}
    vv = np.array([values[t] for t in member.tolist()])
    plan = lm.plan_rows(member, member, vv, ntx, 1.0, (0, 0, ntx, nty), opts(max_rows=6))
    assert not plan['linear'] and [r['type'] for r in plan['rows']] == ['point'] * 6
    # the largest values first: 8, 7, ..., 3
    assert plan['others_n'] == 2 and plan['others_value'] == pytest.approx(1.0 + 2.0)
    assert [round(_box_sum(member, vv, r['box'], ntx)) for r in plan['rows']] == [8, 7, 6, 5, 4, 3]


def _box_sum(vt, vv, box, ntx):
    return lm._box_value(vt, vv, box, ntx)


def test_neighbouring_points_within_twice_the_range_become_one_place():
    ntx, nty = 100, 60
    member = ids([(20, 30), (35, 30), (80, 30)], ntx)        # 15 apart < 2 x 10 + 1 -> one place; 80 is far
    plan = lm.plan_rows(member, member, np.ones(3), ntx, 1.0, (0, 0, ntx, nty), opts())
    assert len(plan['rows']) == 2 and plan['rows'][0]['components'] == 2 and plan['rows'][1]['components'] == 1


def test_component_cut_by_the_window_keeps_its_range_outside_the_window_but_inside_the_frame():
    ntx, nty = 120, 60
    member = block(0, 30, 100, 33, ntx)                        # a bar that continues beyond the window
    win = (20, 0, 60, 60)
    plan = lm.plan_rows(member, member, np.ones(len(member)), ntx, 1.0, win, opts(linear_min_length_um=20.0))
    assert plan['linear'] and len(plan['rows']) == 1
    (row,) = plan['rows']
    assert row['box'][0] == 20 - 10 and row['box'][2] == 60 + 10         # the range grows past the window edge
    placed = lm.place_rows(plan['rows'], ntx, nty, NO_EDGE)
    tiles_x = sorted({int(t) % ntx for t in placed[0]['tiles'].tolist()})
    assert tiles_x[0] == 10 and tiles_x[-1] == 69                        # counted although outside the window


def test_window_box_rounds_outwards_to_whole_tiles():
    ctx = SimpleNamespace(window={'origin_px': [7, 12], 'size_px': [14, 11]}, tile_px=10)
    assert lm._window_box(ctx) == (0, 1, 3, 3)


def test_rows_that_overlap_give_each_tile_to_the_first_row_and_count_what_was_taken():
    rows = [{'type': 'linear', 'box': (0, 0, 10, 10)}, {'type': 'linear', 'box': (5, 0, 15, 10)}]
    placed = lm.place_rows(rows, 40, 40, NO_EDGE)
    assert [len(r['tiles']) for r in placed] == [100, 50] and [r['lost'] for r in placed] == [0, 50]
    assert len(np.intersect1d(placed[0]['tiles'], placed[1]['tiles'])) == 0


def test_a_range_that_leaves_the_frame_without_dl1_or_a_repeat_position_is_excluded():
    rows = [{'type': 'point', 'box': (-3, 10, 8, 20)}]
    placed = lm.place_rows(rows, 40, 40, dict(NO_EDGE, shift=(20, 40), equiv=lambda c, s: None))
    assert placed[0]['edge'] == 'excluded' and placed[0]['edge_reason'] == 'edge_no_dl1' and len(placed[0]['tiles']) == 0
    placed = lm.place_rows(rows, 40, 40, NO_EDGE)
    assert placed[0]['edge_reason'] == 'edge_no_repeat_position'
    placed = lm.place_rows(rows, 40, 40, dict(NO_EDGE, enabled=False))
    assert placed[0]['edge_reason'] == 'coords_resolution_too_coarse'


def test_edge_repeat_replaces_only_the_tiles_outside_the_frame_by_their_image_one_period_away():
    """R2:1: the 3 tiles left of the frame come back +20 tiles (x 37..39 of a 40 tile frame is NOT this case: ux = 20)."""
    rows = [{'type': 'point', 'box': (-3, 10, 8, 20)}]
    calls = []

    def equiv(strip, shift):
        calls.append((strip, shift))
        return 1.0, 1.0
    placed = lm.place_rows(rows, 40, 40, dict(NO_EDGE, shift=(20, 30), equiv=equiv))[0]
    assert calls == [((0, 10, 3, 20), (20, 0))]            # the neighbour strip: 3 tiles wide, the rows of the box
    assert placed['edge'] == 'repeat_used' and placed['shift_tiles'] == [20, 0]
    assert placed['fbox'] == (0, 10, 8, 20) and placed['image_tiles'] == 30      # the cut box, plus 3 x 10 image tiles
    assert len(placed['tiles']) == 8 * 10 + 30 and placed['lost'] == 0
    x = placed['tiles'] % 40
    assert sorted(set(x[(x >= 8)].tolist())) == [17, 18, 19]                       # x -3..-1 come back at 17..19
    other = dict(NO_EDGE, shift=(20, 30), equiv=lambda strip, shift: (0.5, 1.0))
    placed = lm.place_rows(rows, 40, 40, other)[0]
    assert placed['edge'] == 'excluded' and placed['edge_reason'] == 'edge_mismatch' and not len(placed['tiles'])
    low_dice = dict(NO_EDGE, shift=(20, 30), equiv=lambda strip, shift: (1.0, 0.5))
    assert lm.place_rows(rows, 40, 40, low_dice)[0]['edge_reason'] == 'edge_mismatch'


def test_edge_repeat_moves_both_axes_and_checks_the_strip_of_every_outside_part():
    rows = [{'type': 'point', 'box': (-2, -3, 5, 4)}]
    calls = []
    placed = lm.place_rows(rows, 40, 40, dict(NO_EDGE, shift=(20, 30),
                                              equiv=lambda strip, shift: calls.append((strip, shift)) or (1.0, 1.0)))[0]
    assert sorted(calls) == [((0, 0, 2, 3), (20, 30)), ((0, 0, 2, 4), (20, 0)), ((0, 0, 5, 3), (0, 30))]
    assert placed['edge'] == 'repeat_used' and sorted(placed['image_shifts']) == [[0, 30], [20, 0], [20, 30]]
    assert placed['image_tiles'] == 2 * 3 + 2 * 4 + 5 * 3                         # corner + left part + top part
    assert placed['fbox'] == (0, 0, 5, 4) and len(placed['tiles']) == 5 * 4 + 29
    placed = lm.place_rows([{'type': 'point', 'box': (35, 10, 45, 20)}], 40, 40,
                           dict(NO_EDGE, shift=(20, 30), equiv=lambda strip, shift: calls.append((strip, shift)) or (1.0, 1.0)))[0]
    assert calls[-1] == ((35, 10, 40, 20), (-20, 0)) and placed['image_tiles'] == 50     # right side: -U, 5 tiles wide


def test_edge_repeat_is_refused_when_the_image_or_the_strip_one_period_further_is_outside_the_frame():
    never = dict(NO_EDGE, shift=(20, 30), equiv=lambda strip, shift: (1.0, 1.0))
    # 25 tiles left of the frame: its image (x -5..-1) is still left of the frame
    assert lm.place_rows([{'type': 'point', 'box': (-25, 10, 8, 20)}], 40, 40, never)[0]['edge_reason'] == 'edge_no_repeat_position'
    # the box leaves the frame below, and this unit has no vertical period (unit >= frame in y)
    placed = lm.place_rows([{'type': 'point', 'box': (3, 30, 8, 45)}], 40, 40, dict(never, shift=(20, None)))[0]
    assert placed['edge'] == 'excluded' and placed['edge_reason'] == 'edge_no_repeat_position'
    # the strip + period leaves the frame: unit 38 of 40 tiles, strip 3 tiles wide -> 38..41
    tight = dict(NO_EDGE, shift=(38, None), equiv=lambda strip, shift: (1.0, 1.0))
    assert lm.place_rows([{'type': 'point', 'box': (-3, 10, 8, 20)}], 40, 40, tight)[0]['edge_reason'] == 'edge_no_repeat_position'
    assert lm.place_rows([{'type': 'point', 'box': (-2, 10, 8, 20)}], 40, 40, tight)[0]['edge'] == 'repeat_used'


def test_an_image_tile_that_an_earlier_row_owns_stays_with_that_row():
    rows = [{'type': 'point', 'box': (17, 10, 30, 20)}, {'type': 'point', 'box': (-3, 10, 8, 20)}]
    placed = lm.place_rows(rows, 40, 40, dict(NO_EDGE, shift=(20, 30), equiv=lambda c, s: (1.0, 1.0)))
    assert placed[0]['edge'] == 'none' and len(placed[0]['tiles']) == 13 * 10 and placed[0]['lost'] == 0
    assert placed[1]['edge'] == 'repeat_used' and placed[1]['image_tiles'] == 30
    assert placed[1]['lost'] == 30 and len(placed[1]['tiles']) == 80          # the 3 x 10 image tiles belong to row 1
    assert len(np.intersect1d(placed[0]['tiles'], placed[1]['tiles'])) == 0


def test_edge_that_cannot_be_checked_is_excluded_not_passed():
    rows = [{'type': 'point', 'box': (-3, 10, 8, 20)}]
    for verdict in ((None, 1.0), (1.0, None)):
        placed = lm.place_rows(rows, 40, 40, dict(NO_EDGE, shift=(20, 30), equiv=lambda strip, shift, v=verdict: v))[0]
        assert placed['edge'] == 'excluded' and placed['edge_reason'] == 'edge_unverifiable'


def test_pick_offset_takes_the_biggest_overlap_and_refuses_a_spread():
    cells = [(0, 100, 0, 100, 4, 0), (0, 100, 100, 200, 4, 5)]
    assert lm.pick_offset(cells, (80, 0, 130, 50), 10.0) == ((4, 5), None)            # 20 vs 30 px wide: cell 1 wins
    assert lm.pick_offset(cells, (80, 0, 130, 50), 3.0) == (None, 'offset_spread')       # 5 px apart > 3 px
    assert lm.pick_offset(cells, (70, 0, 120, 50), 10.0) == ((4, 0), None)             # 30 vs 20 px wide: cell 0 wins
    assert lm.pick_offset(cells, (150, 0, 190, 50), 3.0) == ((4, 5), None)               # only one cell is touched
    assert lm.pick_offset(cells, (300, 0, 400, 50), 3.0) == (None, 'no_field_cell')
    tie = [(0, 10, 0, 10, 1, 1), (0, 10, 10, 20, 1, 1)]
    assert lm.pick_offset(tie, (5, 0, 15, 10), 1.0) == ((1, 1), None)


def test_tile_shift_counts_the_frame_origin_difference_once():
    """G6: A origin 0 um, B origin 10 um, pixel offset 0 -> shift 0 (not 10 tiles, not -10 tiles)."""
    info_a = {'pitch': 0.1, 'frame': SimpleNamespace(origin_xy_um=(0.0, 0.0))}
    info_b = {'pitch': 0.1, 'frame': SimpleNamespace(origin_xy_um=(10.0, 10.0))}
    assert lm._tile_shift((0, 0), info_a, info_b, 1.0)[:2] == (0, 0)
    sx, sy, err = lm._tile_shift((50, -30), info_a, info_b, 1.0)                       # 5 um, -3 um
    assert (sx, sy) == (5, -3) and err == pytest.approx((0.0, 0.0))
    sx, sy, err = lm._tile_shift((55, 0), info_a, info_b, 1.0)                         # 5.5 um -> a tile of rounding
    assert sx in (5, 6) and abs(err[0]) == pytest.approx(0.5)


def _frame_info(gx, gy, pitch=0.1):
    return {'pitch': pitch, 'frame': SimpleNamespace(origin_xy_um=(gx, gy))}


def test_tile_shift_is_exact_where_a_float_sum_of_origins_rounds_the_wrong_way():
    """C3. 04's d = (g_B - g_A) + offset * pitch, so g_A + d - g_B is 1.4999999999999998 in float for g_A=0, g_B=0.8,
    offset 15 px (1.5 um): the old formula gave 1 tile, the exact value is a half tile and goes away from zero -> 2."""
    assert lm._tile_shift((15, 0), _frame_info(0.0, 0.0), _frame_info(0.8, 0.0), 1.0)[:2] == (2, 0)
    # the negative twin: g_A=0.1, g_B=0.2, offset -15 px; the old formula gave -1
    assert lm._tile_shift((-15, 0), _frame_info(0.1, 0.0), _frame_info(0.2, 0.0), 1.0)[:2] == (-2, 0)
    for offset, want in ((5, 1), (-5, -1), (15, 2), (-15, -2), (25, 3), (-25, -3), (4, 0), (-4, 0), (14, 1), (-14, -1),
                         (16, 2), (-16, -2), (0, 0)):
        sx, _sy, err = lm._tile_shift((offset, 0), _frame_info(0.3, 0.7), _frame_info(0.1, 0.9), 1.0)
        assert sx == want, offset
        assert err[0] == pytest.approx((sx * 10 - offset) * 0.1, abs=1e-12)          # the exact pixel difference
    sx, sy, err = lm._tile_shift((12.5, -7.5), _frame_info(0, 0), _frame_info(0, 0), 1.0)   # a non-integer pixel offset
    assert (sx, sy) == (1, -1) and err == pytest.approx((-0.25, -0.25))


def test_run_with_another_frame_origin_shifts_the_target_rows_by_the_exact_tile_count(tmp_path, big, monkeypatch):
    """R2:5: the origin of B is 0.8 um, the 04 offset 15 px = 1.5 um, tile 1 um -> 2 tiles (an origin counted twice or a
    float rounding to 1 would show as another shift)."""
    shape = correspondence_chain(tmp_path, big, bar(40, 60, 60, 90), bar(40, 60, 75, 105))
    write_candidates(tmp_path, shape, shape, cells=[(0, 100, 0, 200, 15, 0)])
    real_info = lm._side_info

    def info(cfg, cfg2, role):
        out = real_info(cfg, cfg2, role)
        if role == 'target':
            out['frame'].origin_xy_um = (0.8, 0.0)
        return out
    monkeypatch.setattr(lm, '_side_info', info)
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'), '--tile-um', '1.0')
    c = card(doc, 'AB')
    assert code == 0 and c['match']['status'] == 'matched'
    (a1,) = rows_of(c, 'reference')
    assert a1['match']['shift_tiles'] == [2, 0] and a1['match']['rounding_error_um'] == pytest.approx([0.5, 0.0])
    assert c['rounding_error_um'] == pytest.approx(0.5)
    b1 = [r for r in rows_of(c, 'target') if r['match']['status'] == 'matched'][0]
    assert b1['rect_tiles'] == [a1['rect_tiles'][0] + 2, a1['rect_tiles'][1], a1['rect_tiles'][2] + 2, a1['rect_tiles'][3]]
    assert doc['checks']['partition']['status'] == 'passed'


def test_box_values_equal_the_brute_force_sum_of_every_box_and_ranking_ignores_input_order():
    """R2:4: one searchsorted pair per box row gives the same exact sums as masking the whole array per box."""
    rng = np.random.RandomState(5)
    ntx, nty = 60, 40
    tiles = np.unique(rng.randint(0, ntx * nty, size=700)).astype(np.int64)
    values = rng.uniform(1e-6, 1e3, size=len(tiles)) * 10.0 ** rng.randint(-8, 8, size=len(tiles))
    boxes = [(0, 0, 60, 40), (-5, -5, 10, 10), (50, 30, 80, 60), (10, 10, 11, 11), (20, 5, 20, 30), (7, 12, 31, 29),
             (100, 100, 120, 120)]
    got = lm._box_values(tiles, values, boxes, ntx)
    ty, tx = tiles // ntx, tiles % ntx
    for box, value in zip(boxes, got):
        keep = (tx >= box[0]) & (tx < box[2]) & (ty >= box[1]) & (ty < box[3])
        assert value == math.fsum(values[keep].tolist())
    assert lm._box_values(np.zeros(0, np.int64), np.zeros(0), boxes, ntx) == [0.0] * len(boxes)


def test_not_listed_places_count_the_pair_value_once_on_the_tiles_no_listed_row_owns():
    """R2:3: the box of a place that is not listed overlaps a listed row; the value of the shared tiles belongs to the
    listed row, and two unlisted boxes that overlap each other count their common tiles once."""
    ntx, nty = 60, 40
    vt = ids([(10, 10), (14, 10), (30, 10), (31, 10), (50, 30)], ntx)
    vv = np.array([1.0, 2.0, 4.0, 8.0, 16.0])
    listed = lm.place_rows([{'type': 'point', 'box': (8, 8, 16, 14)}], ntx, nty, NO_EDGE)          # tiles (10,10), (14,10)
    boxes = [(12, 8, 20, 14), (29, 8, 40, 14), (35, 8, 45, 14), (45, 25, 55, 35)]
    # unlisted boxes hold (14,10)=2 [owned by the listed row], (30,10)=4 + (31,10)=8, and (50,30)=16; the two overlapping
    # boxes (29..40 / 35..45) share tiles 35..39, which hold no value here
    assert lm.others_free_value(boxes, listed, vt, vv, ntx, nty) == pytest.approx(4.0 + 8.0 + 16.0)
    plain = math.fsum(lm._box_values(vt, vv, boxes, ntx))
    assert plain == pytest.approx(2.0 + 4.0 + 8.0 + 16.0)                      # the old number counted tile (14,10) twice
    assert lm.others_free_value([], listed, vt, vv, ntx, nty) == 0.0
    # the same tile in two unlisted boxes
    both = lm.others_free_value([(29, 8, 33, 14), (30, 9, 32, 12)], [], vt, vv, ntx, nty)
    assert both == pytest.approx(4.0 + 8.0)


def test_membership_overlap_ratio_uses_the_tolerance_in_tiles():
    b = ids([(10, 10), (11, 10)], 30)
    a = ids([(10, 12), (11, 12), (20, 20)], 30)
    assert lm._ratio_overlap(a, b, 30, 30, 1) == 0.0
    assert lm._ratio_overlap(a, b, 30, 30, 2) == pytest.approx(2 / 3)


# ---- (2) ledger-maps on a bigger frame ---------------------------------------------------------------------------------

def make_writer(h, w):
    """write_side of test_compare2 for an h x w px frame: cases carry sets of (y, x) pixels."""
    def write_side(base_dir, name, cases, pairs, cno=None, skip=()):
        root = base_dir / name / 'src'
        names = []
        for defect, ea, la, eb, lb, masks in cases:
            case = f'{defect}-{ea}-{la}-{eb}-{lb}'
            names.append(case)
            folder = root / case
            (folder / 'result').mkdir(parents=True)
            for electrode in ('1.png', '2.png'):
                Image.new('L', (w + 1000, h + 1000), 0).save(folder / electrode)
            rows = ['defect_size(um),Area(um2)']
            for diameter in DIAMETERS_UM:
                cells = masks.get(diameter, ())
                array = np.zeros((h, w), dtype=np.uint8)
                for y, x in cells:
                    array[y, x] = 255
                Image.fromarray(array).save(folder / f'hotspot_{defect}{diameter:02d}.png')
                rows.append(f'{diameter},{len(cells) * CELL:.2f}')
            (folder / 'result' / f'{defect}.csv').write_text('\n'.join(rows) + '\n', encoding='utf-8')
        expected = base_dir / name / 'expected.txt'
        expected.write_text('\n'.join(names) + '\n', encoding='utf-8')
        imported = base_dir / name / 'imp'
        import_hotspots(root, imported, expected_cases=expected)
        (imported / 'pairs.csv').write_text(
            'pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\n'
            + ''.join(f'{pid},{a},{b},{cls},v1\n' for pid, a, b, cls in pairs), encoding='utf-8')
        return root, imported
    return write_side


def make_dl1_writer(h, w):
    def write_dl1(root, model, electrodes):
        """electrodes: {name: {layer: set of (y, x)}} (black = shape)."""
        for name, layers in electrodes.items():
            folder = root / model / name
            folder.mkdir(parents=True, exist_ok=True)
            for index, (layer, cells) in enumerate(sorted(layers.items()), start=1):
                array = np.full((h + 1000, w + 1000), 255, dtype=np.uint8)
                for y, x in cells:
                    array[500 + y, 500 + x] = 0
                Image.fromarray(array).save(folder / f'{index}-{layer}.png')
    return write_dl1


@pytest.fixture
def big(monkeypatch):
    def use(h, w):
        monkeypatch.setattr(base, 'write_side', make_writer(h, w))
        monkeypatch.setattr(base, 'write_dl1', make_dl1_writer(h, w))
    return use


def bar(y0, y1, x0, x1):
    return {(y, x) for y in range(y0, y1) for x in range(x0, x1)}


def line_case(cells, layer='L1', ea='A', eb='B', diameters=(3,)):
    return ('lateral_short', ea, layer, eb, layer, {d: cells for d in diameters})


LINE_PAIRS = [('AB', 'A', 'B', 'line')]
MAP_OPTS = ['--tile-um', '0.5', '--range-um', '0.5', '--linear-min-length-um', '5', '--segment-um', '7',
            '--coord-tolerance-um', '1.0']


def run_cards(tmp, *extra, out='maps'):
    code, folder = base.run_maps(tmp, out, *MAP_OPTS, *extra)
    assert code in (0, 3), (folder / 'error.json').read_text() if (folder / 'error.json').exists() else code
    return code, base.load_maps(folder)


def card(doc, pair_id):
    (found,) = [c for c in doc['cards'] if c['pair_id'] == pair_id]
    return found


def rows_of(c, side):
    return [r for r in c['rows'] if r['side'] == side]


def write_candidates(tmp, shape_a, shape_b, *, mode='stretch', cells=None, global_offset=(0, 0), changes=None):
    """A hand-written 04 folder: candidate_queries_v1.json + alignment.json (+ stretch_map.json).

    cells = [(y0, y1, x0, x1, dx, dy)] in reference px."""
    folder = tmp / 'cand'
    folder.mkdir(exist_ok=True)
    numeric = tmp / 'numeric.yaml'
    section = yaml.safe_load(numeric.read_text(encoding='utf-8'))
    queries = {'schema_version': 'candidate_queries_v1', 'change_args': {} if mode is None else {'apply_align': mode},
               'inputs': {'config_sha256': hashlib.sha256(numeric.read_bytes()).hexdigest(),
                          'cnt': {role: {'product_id': 'P'} for role in ('reference', 'target')}}}
    recorded_hash = json.loads((tmp / 'out' / 'compare2.json').read_text(encoding='utf-8'))['inputs']['manifests']
    for role in ('reference', 'target'):
        # what candidates.prepare_side records: the manifest file sha256 and the dataset input hash (C2)
        queries['inputs'][role] = {
            'condition_assignment_sha256': _canonical_sha(section[role]['condition_assignment']),
            'manifest_sha256': hashlib.sha256((Path(section[role]['data_dir']) / 'manifest.json').read_bytes()).hexdigest(),
            'dataset_input_hash': recorded_hash[role]['input_hash']}
    align = {'schema_version': 'alignment_v1', 'pitch_um': 0.1, 'frame_shape_reference_yx': list(shape_a),
             'frame_shape_target_yx': list(shape_b), 'offset_xy_px': list(global_offset)}
    cell_docs = [{'region_px': list(c[:4]), 'offset_xy_px': [c[4], c[5]], 'applied': True} for c in (cells or [])]
    if mode == 'stretch':
        (folder / 'stretch_map.json').write_text(json.dumps({'schema_version': 'stretch_map_v1', 'tiles': cell_docs}),
                                                 encoding='utf-8')
    elif mode in ('unit', 'local') and cells:
        align['unit_offsets'] = cell_docs
    if changes:
        changes(queries, align)
    (folder / 'candidate_queries_v1.json').write_text(json.dumps(queries), encoding='utf-8')
    (folder / 'alignment.json').write_text(json.dumps(align), encoding='utf-8')
    return folder


def test_linear_line_run_gives_five_segments_and_values_that_add_up_to_the_pair(tmp_path, big):
    h, w = 40, 400
    big(h, w)
    cells = bar(18, 21, 30, 330)                               # 30 um long, 0.3 um thick, 60 tiles of 0.5 um
    side = ([line_case(cells)], LINE_PAIRS)
    base.chain(tmp_path, ref=side, tgt=side, unit=(36.0, 4.0))
    code, doc = run_cards(tmp_path)
    assert code == 0 and doc['checks']['range_sum']['status'] == 'passed'
    (c,) = doc['cards']
    a_rows = rows_of(c, 'reference')
    assert [r['type'] for r in a_rows] == ['linear'] * 5 and c['match']['reason'] == 'coords_no_candidates'
    # 60 tiles / 5 segments = 12 tiles = 6 um apart; only the two ends of the line grow by 0.5 um (1 tile)
    assert a_rows[0]['rect_tiles'][0] == 6 - 1 and a_rows[-1]['rect_tiles'][2] == 66 + 1
    inner = [round(a_rows[i + 1]['rect'][0] - a_rows[i]['rect'][0], 6) for i in range(1, 4)]
    assert inner == [6.0, 6.0, 6.0]
    total_line = c['rows_sum']['reference']
    assert total_line == pytest.approx(c['a'])                              # the 5 rows together hold the whole pair
    assert sum(r['pair'] for r in a_rows) == pytest.approx(c['a'])
    # the same 300 pixel columns (60 px each of 12 tiles) hold the same value: 1/5 of the pair per segment
    for r in a_rows:
        assert r['pair'] == pytest.approx(c['a'] / 5) and r['tiles_lost'] == 0
        assert r['pair'] + (r['others']['others_value'] + sum(o['value'] for o in r['others']['top'])) == pytest.approx(r['range_total'])


def tiles_px(tx0, tx1, ty0, ty1):
    """pixels of whole 0.5 um tiles (5 px)."""
    return bar(ty0 * 5, ty1 * 5, tx0 * 5, tx1 * 5)


TWO_PAIRS = LINE_PAIRS + [('GH', 'G', 'H', 'line')]


def correspondence_chain(tmp, big, ref_cells, tgt_cells, *, pairs=LINE_PAIRS, h=100, w=200, ref_layer='L1',
                         tgt_layer='L1', **kw):
    big(h, w)
    ref = ([line_case(ref_cells, layer=ref_layer)] if ref_cells is not None else [], pairs)
    tgt = ([line_case(tgt_cells, layer=tgt_layer)] if tgt_cells is not None else [], pairs)
    base.chain(tmp, ref=ref, tgt=tgt, unit=(w * 0.1, h * 0.1), **kw)
    return (h, w)


def test_same_pair_offset_from_the_04_field_moves_the_row_and_keeps_the_value(tmp_path, big):
    shape = correspondence_chain(tmp_path, big, tiles_px(6, 9, 6, 9), tiles_px(16, 19, 10, 13))
    write_candidates(tmp_path, shape, shape, cells=[(0, 100, 0, 200, 50, 20)])      # B = A + (50, 20) px = (5, 2) um
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'))
    assert code == 0 and doc['bands'] == [] and doc['inputs']['candidates']['apply_align'] == 'stretch'
    c = card(doc, 'AB')
    assert c['match']['status'] == 'matched' and c['match']['matched_rows'] == 1
    (a1,), (b1,) = rows_of(c, 'reference'), rows_of(c, 'target')
    assert a1['match']['counterpart'] == 'B1' and b1['match']['counterpart'] == 'A1'
    assert [round(v, 6) for v in b1['rect']] == [round(a1['rect'][0] + 5.0, 6), round(a1['rect'][1] + 2.0, 6),
                                                  round(a1['rect'][2] + 5.0, 6), round(a1['rect'][3] + 2.0, 6)]
    assert a1['pair'] == pytest.approx(c['a']) and b1['pair'] == pytest.approx(c['b'])    # one blob each side
    assert c['rounding_error_um'] == 0.0 and c['match']['absorbed_b_rows'] == [1]
    assert doc['checks']['partition']['status'] == 'passed' and doc['checks']['pair_bound']['status'] == 'passed'


def test_second_row_that_lands_on_the_first_in_b_is_refused_not_split(tmp_path, big):
    """G1-10 (a): two A rows that do not touch, but their offsets put them on the same B tiles."""
    a = tiles_px(6, 9, 6, 9) | tiles_px(26, 29, 6, 9)
    shape = correspondence_chain(tmp_path, big, a, tiles_px(6, 9, 6, 9))
    write_candidates(tmp_path, shape, shape, cells=[(0, 100, 0, 100, 0, 0), (0, 100, 100, 200, -100, 0)])
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'))
    assert code == 0
    c = card(doc, 'AB')
    a1, a2 = rows_of(c, 'reference')
    assert a1['match']['status'] == 'matched' and a2['match'] == {'status': 'only', 'reason': 'b_conflict'}
    (b1,) = rows_of(c, 'target')
    assert b1['match']['with_a'] == 1 and c['match']['matched_rows'] == 1
    assert a2['pair'] == pytest.approx(c['a'] / 2) and b1['pair'] == pytest.approx(c['b'])
    assert doc['checks']['partition']['status'] == 'passed'


def test_one_shared_tile_does_not_swallow_the_b_row_the_other_99_stay_a_b_only_row(tmp_path, big):
    """G1-10 (b): the B row has 100 tiles, one of them lies in the matched set."""
    shape = correspondence_chain(tmp_path, big, tiles_px(6, 9, 6, 9), tiles_px(10, 18, 10, 18))
    write_candidates(tmp_path, shape, shape, cells=[(0, 100, 0, 200, 0, 0)])
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'), '--coord-tolerance-um', '2.0')
    assert code == 0
    c = card(doc, 'AB')
    b1, b2 = rows_of(c, 'target')
    assert b1['match']['with_a'] == 1 and b1['tiles'] == 25 and b1['pair'] == 0.0     # the moved A row: no B shape there
    assert b2['match']['status'] == 'only' and b2['tiles'] == 99 and b2['match']['partial_matched'] is True
    assert b2['pair'] == pytest.approx(c['b']) and c['match']['absorbed_b_rows'] == []
    assert doc['checks']['partition']['status'] == 'passed'


def test_a_row_whose_tiles_another_row_took_is_valued_on_its_final_tiles(tmp_path, big):
    """G1-10 (c) + (d): the blob row keeps 21 of its 42 tiles; its value counts only those, and the rows add up."""
    cells = tiles_px(6, 66, 20, 22) | tiles_px(30, 33, 23, 25)          # a 60 x 2 tile line and a 3 x 2 tile blob 1 tile above
    shape = correspondence_chain(tmp_path, big, cells, cells, h=160, w=400)
    code, doc = run_cards(tmp_path, '--range-um', '1.0')                   # two tiles of range
    assert code == 0
    c = card(doc, 'AB')
    rows = rows_of(c, 'reference')
    assert [r['type'] for r in rows] == ['linear'] * 5 + ['point'] and c['match']['reason'] == 'coords_no_candidates'
    blob = rows[-1]
    assert blob['tiles'] == 21 and blob['tiles_lost'] == 21 and blob['rect_tiles'] == [28, 21, 35, 27]
    unit = c['a'] / (25.0 * 126)                                           # every tile holds 25 px of 126 tiles
    assert blob['pair'] == pytest.approx(3 * 25 * unit)                    # only the 3 blob tiles of row 24, not the stolen row 23
    assert rows[2]['pair'] == pytest.approx((24 + 3) * 25 * unit)         # the line segment owns the blob tiles of row 23
    assert sum(r['pair'] for r in rows) == pytest.approx(c['a'])
    for name in ('range_sum', 'pair_bound', 'partition'):
        assert doc['checks'][name]['status'] == 'passed'


def test_without_a_04_folder_every_row_stays_on_its_own_side_and_a_band_says_why(tmp_path, big):
    shape = correspondence_chain(tmp_path, big, tiles_px(6, 9, 6, 9), tiles_px(16, 19, 10, 13))
    code, doc = run_cards(tmp_path)
    assert code == 0 and [b['code'] for b in doc['bands']] == ['coords_no_candidates']
    c = card(doc, 'AB')
    assert c['match']['status'] == 'not_compared' and c['match']['reason'] == 'coords_no_candidates'
    assert all(r['match']['status'] == 'only' and r['match']['reason'] == 'coords_no_candidates' for r in c['rows'])
    assert len(rows_of(c, 'reference')) == 1 and len(rows_of(c, 'target')) == 1


@pytest.mark.parametrize('mode, change, band', [
    ('diagnostic', None, 'coords_not_applied'),
    (None, None, 'coords_not_applied'),
    ('stretch', lambda q, a: q['inputs'].__setitem__('config_sha256', '0' * 64), 'coords_stale'),
    ('stretch', lambda q, a: a.__setitem__('frame_shape_target_yx', [1, 1]), 'coords_stale'),
    ('stretch', lambda q, a: q['inputs']['reference'].__setitem__('condition_assignment_sha256', 'x'), 'coords_stale'),
    ('stretch', lambda q, a: q['inputs']['cnt']['target'].__setitem__('product_id', 'Q'), 'coords_stale'),
    ('stretch', lambda q, a: a.__setitem__('pitch_um', 0.2), 'coords_stale'),
    ('stretch', lambda q, a: a.pop('pitch_um'), 'coords_unavailable'),
    # C2: the input files themselves
    ('stretch', lambda q, a: q['inputs']['reference'].__setitem__('manifest_sha256', '0' * 64), 'coords_stale'),
    ('stretch', lambda q, a: q['inputs']['target'].__setitem__('dataset_input_hash', '0' * 64), 'coords_stale'),
    ('stretch', lambda q, a: [q['inputs']['reference'].pop(k) for k in ('manifest_sha256', 'dataset_input_hash')],
     'coords_unavailable'),
    ('stretch', lambda q, a: [q['inputs']['target'].pop(k) for k in ('manifest_sha256', 'dataset_input_hash')],
     'coords_unavailable'),
])
def test_04_that_cannot_be_used_gives_a_band_and_no_correspondence(tmp_path, big, mode, change, band):
    shape = correspondence_chain(tmp_path, big, tiles_px(6, 9, 6, 9), tiles_px(16, 19, 10, 13))
    write_candidates(tmp_path, shape, shape, mode=mode, cells=[(0, 100, 0, 200, 50, 20)], changes=change)
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'))
    assert code == 0 and [b['code'] for b in doc['bands']] == [band]
    c = card(doc, 'AB')
    assert c['match']['status'] == 'not_compared' and c['match']['reason'] == band
    assert not [r for r in c['rows'] if r['match']['status'] == 'matched']


def test_04_made_from_another_manifest_file_is_stale_although_config_names_shapes_and_pitch_agree(tmp_path, big):
    """C2: every setting 04 recorded still matches; only the bytes of a file it read changed (a re-saved manifest)."""
    shape = correspondence_chain(tmp_path, big, tiles_px(6, 9, 6, 9), tiles_px(16, 19, 10, 13))
    write_candidates(tmp_path, shape, shape, cells=[(0, 100, 0, 200, 50, 20)])
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'))
    assert code == 0 and doc['bands'] == [] and card(doc, 'AB')['match']['status'] == 'matched'
    section = yaml.safe_load((tmp_path / 'numeric.yaml').read_text(encoding='utf-8'))
    manifest = Path(section['reference']['data_dir']) / 'manifest.json'
    manifest.write_text(json.dumps(json.loads(manifest.read_text(encoding='utf-8')), indent=2) + '\n', encoding='utf-8')
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'), out='m2')
    assert code == 0 and [b['code'] for b in doc['bands']] == ['coords_stale']
    assert 'reference manifest_sha256' in doc['bands'][0]['detail'] and 'dataset_input_hash' not in doc['bands'][0]['detail']
    assert card(doc, 'AB')['match']['status'] == 'not_compared'


def test_04_asset_fingerprints_are_compared_when_04_recorded_them(tmp_path, big, monkeypatch):
    from simcompare import pipeline
    shape = correspondence_chain(tmp_path, big, tiles_px(6, 9, 6, 9), tiles_px(16, 19, 10, 13))

    def recorded(sha_manifest, sha_record):
        def change(queries, align):
            queries['inputs']['reference']['asset_manifest'] = {'path': 'm.json', 'sha256': sha_manifest}
            queries['inputs']['reference']['electrode_import_record'] = {'path': 'r.json', 'sha256': sha_record}
        return change
    # only the reference carries recorded asset fingerprints, so only its evidence is asked for
    monkeypatch.setattr(pipeline, '_electrode_evidence', lambda root, section, config_dir=None:
                        ('accepted', {}, (('r.json', 'aa' * 32), ('m.json', 'bb' * 32)), None))
    write_candidates(tmp_path, shape, shape, cells=[(0, 100, 0, 200, 50, 20)], changes=recorded('bb' * 32, 'aa' * 32))
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'))
    assert doc['bands'] == [] and card(doc, 'AB')['match']['status'] == 'matched'
    write_candidates(tmp_path, shape, shape, cells=[(0, 100, 0, 200, 50, 20)], changes=recorded('cc' * 32, 'aa' * 32))
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'), out='m2')
    assert [b['code'] for b in doc['bands']] == ['coords_stale'] and 'asset_manifest.sha256' in doc['bands'][0]['detail']
    monkeypatch.setattr(pipeline, '_electrode_evidence', lambda root, section, config_dir=None: None)
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'), out='m3')
    assert [b['code'] for b in doc['bands']] == ['coords_unavailable']          # recorded by 04, cannot be recomputed now


def test_unit_and_local_modes_read_the_unit_field_and_local_without_it_the_global_offset(tmp_path, big):
    shape = correspondence_chain(tmp_path, big, tiles_px(6, 9, 6, 9), tiles_px(16, 19, 10, 13))
    for mode, cells, glob in (('unit', [(0, 100, 0, 200, 50, 20)], (0, 0)), ('local', [(0, 100, 0, 200, 50, 20)], (0, 0)),
                              ('local', None, (50, 20))):
        write_candidates(tmp_path, shape, shape, mode=mode, cells=cells, global_offset=glob)
        code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'))
        assert code == 0 and card(doc, 'AB')['match']['status'] == 'matched', (mode, cells)
    write_candidates(tmp_path, shape, shape, mode='unit', cells=None)               # unit mode without unit_offsets
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'))
    assert [b['code'] for b in doc['bands']] == ['coords_unavailable']


def test_offsets_that_differ_inside_the_range_are_not_used(tmp_path, big):
    shape = correspondence_chain(tmp_path, big, tiles_px(6, 9, 6, 9), tiles_px(16, 19, 10, 13))
    cells = [(0, 100, 0, 40, 50, 20), (0, 100, 40, 200, 80, 20)]                    # the row (px 25..50) touches both: 3 um apart
    write_candidates(tmp_path, shape, shape, cells=cells)
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'))
    c = card(doc, 'AB')
    assert code == 0 and c['match']['status'] == 'not_compared'
    assert rows_of(c, 'reference')[0]['match']['reason'] == 'offset_spread'
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'), '--coord-offset-spread-um', '3.0', out='m2')
    assert card(doc, 'AB')['match']['status'] == 'matched'                             # the biggest cell (x 40..50: 10 px vs 15) wins


def test_a_moved_row_must_overlap_the_b_shape_and_stay_inside_the_b_frame(tmp_path, big):
    shape = correspondence_chain(tmp_path, big, tiles_px(6, 9, 6, 9), tiles_px(30, 33, 10, 13))
    write_candidates(tmp_path, shape, shape, cells=[(0, 100, 0, 200, 50, 20)])      # lands at x 16..19: B has its shape at 30..33
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'))
    assert rows_of(card(doc, 'AB'), 'reference')[0]['match']['reason'] == 'low_membership_overlap'
    write_candidates(tmp_path, shape, shape, cells=[(0, 100, 0, 200, 400, 0)])
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'))
    assert rows_of(card(doc, 'AB'), 'reference')[0]['match']['reason'] == 'outside_b_frame'
    assert doc['checks']['partition']['status'] == 'passed'


def test_b_only_and_a_only_pairs_show_their_own_side_only(tmp_path, big):
    big(100, 200)
    gh = line_case(tiles_px(6, 9, 6, 9), layer='L2', ea='G', eb='H')
    ab = line_case(tiles_px(16, 19, 10, 13))
    base.chain(tmp_path, ref=([gh], TWO_PAIRS), tgt=([ab, gh], TWO_PAIRS), unit=(20.0, 10.0))
    write_candidates(tmp_path, (100, 200), (100, 200), cells=[(0, 100, 0, 200, 0, 0)])
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'))
    c = card(doc, 'AB')
    assert code == 0 and c['match']['status'] == 'b_only' and not rows_of(c, 'reference')
    (row,) = rows_of(c, 'target')
    assert row['pair'] == pytest.approx(c['b']) and c['a'] == 0.0
    assert row['rect_tiles'] == [15, 9, 20, 14]          # the blob (tiles 16..18 x 10..12) plus one tile of range
    # the same pair the other way round: only in the reference
    rev = tmp_path / 'rev'
    rev.mkdir()
    base.chain(rev, ref=([ab, gh], TWO_PAIRS), tgt=([gh], TWO_PAIRS), unit=(20.0, 10.0))
    write_candidates(rev, (100, 200), (100, 200), cells=[(0, 100, 0, 200, 0, 0)])
    code, doc = run_cards(rev, '--candidates', str(rev / 'cand'))
    c = card(doc, 'AB')
    assert c['match']['status'] == 'a_only' and not rows_of(c, 'target') and rows_of(c, 'reference')[0]['pair'] == pytest.approx(c['a'])
    assert doc['checks']['range_sum']['status'] == 'passed'


def test_layer_change_pair_shows_both_positions_and_values_without_a_comparison(tmp_path, big):
    shape = correspondence_chain(tmp_path, big, tiles_px(6, 9, 6, 9), tiles_px(16, 19, 10, 13), tgt_layer='L2')
    write_candidates(tmp_path, shape, shape, cells=[(0, 100, 0, 200, 50, 20)])
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'))
    c = card(doc, 'AB')
    assert code == 0 and c['match']['status'] == 'not_compared' and c['match']['reason'] == 'layer_change'
    (a1,), (b1,) = rows_of(c, 'reference'), rows_of(c, 'target')
    assert a1['pair'] == pytest.approx(c['a']) and b1['pair'] == pytest.approx(c['b']) and c['a'] != c['b']
    assert a1['rect_tiles'] == [5, 5, 10, 10] and b1['rect_tiles'] == [15, 9, 20, 14]


# ---- edge repeat with a synthetic electrode grid -----------------------------------------------------------------------

def edge_ctx(grid, w_tiles=40, ntx=100, nty=40, member=None, window_px=None, tolerance_um=2.0):
    ctx = SimpleNamespace(tile_px=5, ntx=ntx, nty=nty, role='reference', tile_el={'L1': grid}, el_bytes=0,
                          window={'origin_px': [0, 0], 'size_px': list(window_px or [w_tiles * 5, 20 * 5])},
                          info={'pitch': 0.1, 'frame': SimpleNamespace(shape_yx=(nty * 5, ntx * 5))})
    run = SimpleNamespace(opts=opts(tile_um=0.5, coord_tolerance_um=tolerance_um))
    return lm._edge_env(ctx, run, member if member is not None else np.zeros(0, np.int64), ['L1'])


def test_small_unit_repeat_is_used_when_electrodes_and_shape_match_and_the_value_is_the_moved_places(tmp_path):
    ntx, nty = 100, 40
    grid = np.zeros((nty, ntx), dtype=np.uint16)
    grid[10:20, 0:10] = 7
    grid[10:20, 40:50] = 7                                   # the same electrode one unit (40 tiles) to the right
    member = block(2, 12, 5, 15, ntx)
    member = np.union1d(member, block(42, 12, 45, 15, ntx))
    edge = edge_ctx(grid, member=member)
    assert edge['shift'] == (40, 20)
    rows = [{'type': 'point', 'box': (-3, 10, 8, 20)}]
    (row,) = lm.place_rows(rows, ntx, nty, edge)
    assert row['edge'] == 'repeat_used' and row['shift_tiles'] == [40, 0] and row['fbox'] == (0, 10, 8, 20)
    assert row['edge_match'] == 1.0 and row['edge_dice'] == 1.0 and row['image_tiles'] == 30
    # hand calculation: the value is the original tiles inside the frame + the image tiles (x -3..-1 -> 37..39);
    # 99 on an original tile, 2.0 on an image tile, 5.0 on tile 42 (one period right of the strip: in neither set)
    vt = np.array([15 * ntx + 3, 15 * ntx + 38, 15 * ntx + 42], dtype=np.int64)
    sums, _ = lm._row_sums(lm._owner_index([row]), vt, np.array([99.0, 2.0, 5.0]), 1)
    assert sums.tolist() == [101.0]


def test_small_unit_repeat_that_differs_in_electrodes_or_shape_is_excluded():
    ntx, nty = 100, 40
    member = np.union1d(block(2, 12, 5, 15, ntx), block(42, 12, 45, 15, ntx))
    grid = np.zeros((nty, ntx), dtype=np.uint16)
    grid[10:20, 0:10] = 7
    grid[10:20, 40:50] = 8                                   # another electrode there
    (row,) = lm.place_rows([{'type': 'point', 'box': (-3, 10, 8, 20)}], ntx, nty, edge_ctx(grid, member=member))
    assert row['edge'] == 'excluded' and row['edge_reason'] == 'edge_mismatch' and not len(row['tiles'])
    grid[10:20, 40:50] = 7
    other = np.union1d(block(2, 12, 5, 15, ntx), block(42, 12, 45, 14, ntx))         # the pair's shape differs
    (row,) = lm.place_rows([{'type': 'point', 'box': (-3, 10, 8, 20)}], ntx, nty, edge_ctx(grid, member=other))
    assert row['edge'] == 'excluded' and row['edge_reason'] == 'edge_mismatch'
    assert lm.place_rows([{'type': 'point', 'box': (-3, 10, 8, 20)}], ntx, nty,
                         edge_ctx(grid, member=other))[0]['lost'] == 0
    (row,) = lm.place_rows([{'type': 'point', 'box': (-3, 10, 8, 20)}], ntx, nty,
                           dict(edge_ctx(grid, member=member), min_match=0.9))
    assert row['edge'] == 'repeat_used'


def test_both_strips_empty_is_unverifiable_and_the_row_is_excluded():
    """Two empty membership strips give no Dice: "equal" is not shown, the row is excluded (not Dice = 1)."""
    ntx, nty = 100, 40
    grid = np.zeros((nty, ntx), dtype=np.uint16)
    grid[10:20, 0:10] = 7
    grid[10:20, 40:50] = 7
    member = block(5, 12, 7, 15, ntx)                      # inside the box but outside both 3 tile strips
    (row,) = lm.place_rows([{'type': 'point', 'box': (-3, 10, 8, 20)}], ntx, nty, edge_ctx(grid, member=member))
    assert row['edge'] == 'excluded' and row['edge_reason'] == 'edge_unverifiable' and not len(row['tiles'])
    # no electrode on either strip: the electrode match cannot be shown either
    empty = np.zeros((nty, ntx), dtype=np.uint16)
    member = np.union1d(block(2, 12, 5, 15, ntx), block(42, 12, 45, 15, ntx))
    (row,) = lm.place_rows([{'type': 'point', 'box': (-3, 10, 8, 20)}], ntx, nty, edge_ctx(empty, member=member))
    assert row['edge'] == 'excluded' and row['edge_reason'] == 'edge_unverifiable'


def test_a_unit_that_is_not_a_whole_number_of_tiles_records_the_rounding_and_is_excluded_beyond_the_tolerance():
    ntx, nty = 100, 40
    grid = np.zeros((nty, ntx), dtype=np.uint16)
    member = np.zeros(0, np.int64)
    ctx = edge_ctx(grid, w_tiles=40, member=member)
    assert ctx['shift'] == (40, 20) and ctx['rounding_um'] == (0.0, 0.0)
    # window 203 px, tile 5 px: 41 tiles (205 px), error 2 px = 0.2 um <= 2.0 um
    ctx = edge_ctx(grid, member=member, window_px=(203, 100))
    assert ctx['shift'][0] == 41 and ctx['rounding_um'][0] == pytest.approx(0.2)
    row = lm._edge_resolve((-3, 10, 8, 20), ntx, nty, dict(ctx, equiv=lambda strip, shift: (1.0, 1.0)))
    assert row['edge'] == 'repeat_used' and row['edge_rounding_um'] == pytest.approx(0.2) and row['shift_tiles'] == [41, 0]
    # the rounding is at most half a tile, and the edge rule only runs when a tile is <= --coord-tolerance-um, so with the
    # tolerance test of `period` the error can never exceed it in a real run; a tile coarser than the tolerance is G4
    ctx = edge_ctx(grid, member=member, window_px=(203, 100), tolerance_um=0.1)
    row = lm._edge_resolve((-3, 10, 8, 20), ntx, nty, dict(ctx, equiv=lambda s, v: (1.0, 1.0)))
    assert row['edge'] == 'excluded' and row['edge_reason'] == 'coords_resolution_too_coarse'


PERIOD_DL1 = {'A': {'L1': bar(0, 40, 0, 20) | bar(0, 40, 190, 200)}, 'B': {'L1': bar(0, 40, 100, 110)}}
PERIOD_CELLS = bar(15, 25, 0, 15) | bar(15, 25, 190, 200)          # the same layout at both ends of the 19 um unit


def period_chain(tmp, big, dl1):
    big(40, 200)                                                    # frame 20 um x 4 um, unit 19 um x 4 um (period 38 tiles)
    side = ([line_case(PERIOD_CELLS)], LINE_PAIRS)
    base.chain(tmp, ref=side, tgt=side, unit=(19.0, 4.0), dl1={'ref': dl1, 'tgt': dl1})


def test_run_with_a_small_unit_counts_the_image_tiles_and_the_range_checks_hold(tmp_path, big):
    """The window starts 1 tile inside the frame and the range is 2 tiles: each end of the layout leaves the frame by 1
    tile. The strip at that edge equals the strip one period (38 tiles) further, so the row takes its image tiles."""
    period_chain(tmp_path, big, PERIOD_DL1)
    code, doc = run_cards(tmp_path, '--range-um', '1.0')
    assert code == 0 and doc['status'] == 'ok'
    c = card(doc, 'AB')
    first, second = rows_of(c, 'reference')
    assert (first['edge'], second['edge']) == ('repeat_used', 'repeat_used')
    assert first['rect_tiles'] == [0, 1, 5, 7] and first['image_tiles'] == 6 and first['image_shifts'] == [[38, 0]]
    assert second['rect_tiles'] == [36, 1, 40, 7] and second['image_tiles'] == 6 and second['image_shifts'] == [[-38, 0]]
    # image column 37 (first row) lies inside the second row's box and image column 2 (second row) inside the first
    # row's: the first row owns both, the second row lost 6 + 6 tiles
    assert (first['tiles'], first['tiles_lost']) == (5 * 6 + 6, 0)                 # clipped box 5 x 6 + image column
    assert (second['tiles'], second['tiles_lost']) == (4 * 6 + 6 - 12, 12)         # clipped 4 x 6 + image column - 12 lost
    assert first['pair'] + second['pair'] == pytest.approx(c['a'])             # the whole pair stands on the final tiles
    for name in ('range_sum', 'pair_bound', 'partition'):
        assert doc['checks'][name]['status'] == 'passed', name


def test_run_with_a_small_unit_excludes_the_edge_row_when_the_strip_one_period_further_differs(tmp_path, big):
    other = {'A': {'L1': bar(0, 40, 0, 20)}, 'B': {'L1': bar(0, 40, 190, 200)}}          # another electrode at the far end
    period_chain(tmp_path, big, other)
    code, doc = run_cards(tmp_path, '--range-um', '1.0')
    rows = rows_of(card(doc, 'AB'), 'reference')
    assert code == 0 and [r['edge'] for r in rows] == ['excluded', 'excluded']
    assert {r['edge_reason'] for r in rows} == {'edge_mismatch'} and all(r['pair'] is None for r in rows)


# ---- G4, options, checks ------------------------------------------------------------------------------------------------

def test_tile_2_with_tolerance_1_gives_the_band_and_excludes_the_edge_rows(tmp_path):
    base.chain(tmp_path)
    code, out = base.run_maps(tmp_path, 'm', '--tile-um', '2', '--coord-tolerance-um', '1')
    doc = base.load_maps(out)
    assert code == 0 and [b['code'] for b in doc['bands']] == ['coords_no_candidates', 'coords_resolution_too_coarse']
    rows = [r for c in doc['cards'] for r in c['rows']]
    assert rows and all(r['edge'] == 'excluded' and r['edge_reason'] == 'coords_resolution_too_coarse' and r['pair'] is None
                        for r in rows)
    assert doc['checks']['range_sum']['status'] == 'not_evaluated'


def test_tile_coarser_than_the_tolerance_never_matches_even_with_a_04_folder(tmp_path, big):
    shape = correspondence_chain(tmp_path, big, tiles_px(6, 9, 6, 9), tiles_px(16, 19, 10, 13))
    write_candidates(tmp_path, shape, shape, cells=[(0, 100, 0, 200, 50, 20)])
    code, doc = run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'), '--tile-um', '1.0', '--coord-tolerance-um', '0.5')
    assert code == 0 and 'coords_resolution_too_coarse' in [b['code'] for b in doc['bands']]
    assert card(doc, 'AB')['match']['status'] == 'not_compared'


@pytest.mark.parametrize('flag, value', [('--top-cards', '0'), ('--segment-um', '0'), ('--linear-min-length-um', '-1'),
                                          ('--edge-repeat-min-match', '1.5'), ('--coord-offset-spread-um', '-0.1'),
                                          ('--coord-tolerance-um', '0'), ('--max-rows', '0')])
def test_card_options_out_of_range_exit_2(tmp_path, flag, value):
    base.chain(tmp_path)
    code, out = base.run_maps(tmp_path, 'm', flag, value)
    assert code == 2 and json.loads((out / 'error.json').read_text())['code'] == 'ledger_maps_option_invalid'


def test_top_cards_picks_the_largest_absolute_deltas_per_kind(tmp_path):
    base.chain(tmp_path)
    led = json.loads((tmp_path / 'led' / 'ledger.json').read_text())
    code, out = base.run_maps(tmp_path, 'm', '--top-cards', '2')
    doc = base.load_maps(out)
    for kind in ('line', 'pixel'):
        pairs = sorted((p for p in led['pairs'] if p['kind'] == kind), key=lambda p: (-abs(p['delta']), p['pair_id']))
        got = [c['pair_id'] for c in doc['cards'] if c['kind'] == kind]
        assert got == [p['pair_id'] for p in pairs[:2]] and [c['rank'] for c in doc['cards'] if c['kind'] == kind] == [1, 2]


def test_a_broken_range_total_partition_or_pair_bound_ends_with_exit_3_and_the_output(tmp_path, monkeypatch, big):
    shape = correspondence_chain(tmp_path, big, tiles_px(6, 9, 6, 9), tiles_px(6, 9, 6, 9))
    write_candidates(tmp_path, shape, shape, cells=[(0, 100, 0, 200, 0, 0)])
    args = ['--candidates', str(tmp_path / 'cand')]
    code, doc = run_cards(tmp_path, *args)
    assert code == 0 and doc['status'] == 'ok'
    real_fill = lm._fill_values

    def broken_fill(ctx, plan, rows):
        real_fill(ctx, plan, rows)
        for row in rows:
            if row['range_total'] is not None:
                row['range_total'] += 1.0
    monkeypatch.setattr(lm, '_fill_values', broken_fill)
    code, doc = run_cards(tmp_path, *args, out='m1')
    assert code == 3 and doc['status'] == 'partial' and doc['reasons'] == ['ledger_maps_value_check_failed']
    assert doc['checks']['range_sum']['status'] == 'failed' and doc['checks']['range_sum']['failed_count'] >= 1
    assert doc['checks']['status'] == 'failed' and doc['cards']
    monkeypatch.setattr(lm, '_fill_values', real_fill)
    monkeypatch.setattr(lm, '_pair_total', lambda ctx, pair_id, ids: 1e9)
    code, doc = run_cards(tmp_path, *args, out='m2')
    assert code == 3 and doc['checks']['pair_bound']['status'] == 'failed'
    monkeypatch.undo()
    real_place = lm.place_rows

    def doubled(rows, ntx, nty, edge):
        placed = real_place(rows, ntx, nty, edge)
        if len(placed) >= 1:
            placed.append(dict(placed[0]))                      # the same tiles twice: no longer a partition
        return placed
    monkeypatch.setattr(lm, 'place_rows', doubled)
    code, doc = run_cards(tmp_path, *args, out='m3')
    assert code == 3 and doc['checks']['partition']['status'] == 'failed'


def test_two_runs_with_cards_have_identical_bytes_except_execution_json(tmp_path, big):
    shape = correspondence_chain(tmp_path, big, tiles_px(6, 9, 6, 9) | tiles_px(26, 29, 6, 9), tiles_px(6, 9, 6, 9))
    write_candidates(tmp_path, shape, shape, cells=[(0, 100, 0, 100, 0, 0), (0, 100, 100, 200, -100, 0)])
    args = ['--candidates', str(tmp_path / 'cand')]
    code1, out1 = base.run_maps(tmp_path, 'r1', *MAP_OPTS, *args)
    code2, out2 = base.run_maps(tmp_path, 'r2', *MAP_OPTS, *args)
    assert code1 == 0 and code2 == 0 and base.results_hash(out1) == base.results_hash(out2)


def test_card_data_of_the_first_product_is_reserved_until_the_second_product_is_done(tmp_path, big, monkeypatch):
    shape = correspondence_chain(tmp_path, big, tiles_px(6, 9, 6, 9), tiles_px(6, 9, 6, 9))
    write_candidates(tmp_path, shape, shape, cells=[(0, 100, 0, 200, 0, 0)])
    calls, real_reserve, real_release = [], lm._Budget.reserve, lm._Budget.release

    def reserve(self, nbytes, what):
        calls.append(('reserve', what, nbytes, self.used))
        return real_reserve(self, nbytes, what)

    def release(self, nbytes, what):
        calls.append(('release', what, nbytes, self.used))
        return real_release(self, nbytes, what)

    monkeypatch.setattr(lm._Budget, 'reserve', reserve)
    monkeypatch.setattr(lm._Budget, 'release', release)
    code, out = base.run_maps(tmp_path, 'r1', *MAP_OPTS, '--candidates', str(tmp_path / 'cand'))
    assert code == 0
    carried = [c for c in calls if c[0] == 'reserve' and c[1] == 'cards carried to the next product (reference)']
    assert len(carried) == 1 and carried[0][2] > 0
    # released after the second product has used it
    freed = [c for c in calls if c[0] == 'release' and c[1] == 'cards carried from the first product']
    assert len(freed) == 1 and freed[0][2] == carried[0][2]
    assert calls.index(freed[0]) > calls.index(carried[0])
