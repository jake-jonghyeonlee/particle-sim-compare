"""mrep/T4: the 04 change locations on the ledger maps (`ledger_maps.json` changes / overlay).

(1) Pure geometry: `roi_map_rects`, `overlay_state` on hand-made product geometry (C7: pA = (x_um - g_A_um) / pitch,
pB = pA + offset_px, display = (p - map_origin_px) / block_px; the field is chosen before the periodic display shift).
(2) `ledger-maps` runs on the small real_hotspot fixtures of test_ledger_maps_cards with a hand-written 04 folder."""
from types import SimpleNamespace

import pytest

from simcompare import ledger_maps as lm
from test_ledger_maps_cards import big, correspondence_chain, run_cards, tiles_px, write_candidates  # noqa: F401

SPREAD_PX = 10.0


def side(origin_um=(0.0, 0.0), shape=(100, 200), window=((0, 0), (200, 100)), map_origin=(0, 0), block=1):
    return {'origin_um': origin_um, 'pitch': 0.1, 'shape_yx': shape,
            'window': {'origin_px': list(window[0]), 'size_px': list(window[1])}, 'map_origin_px': map_origin,
            'block_px': block}


def corr_ok(*cells):
    return SimpleNamespace(ok=True, band=None, cells=list(cells) or [(0, 100, 0, 200, 0, 0)])


def corr_band(band):
    return SimpleNamespace(ok=False, band=band, cells=None)


def roi(center=(5.0, 5.0), window_um=2.0):
    return {'roi_key': 'roi:R1', 'center_xy_um': list(center), 'window_um': window_um}


# ---- (1) geometry ----------------------------------------------------------------------------------------------------------

def test_b_frame_origin_is_not_added_again_when_the_offset_is_zero():
    """G6 fixture: A origin 0 um, B origin 10 um, offset 0 -> the B rectangle is the A rectangle (no 10 um = 100 px move)."""
    geo = {'reference': side(), 'target': side(origin_um=(10.0, 10.0))}
    out = lm.roi_map_rects(roi(), geo, corr_ok(), SPREAD_PX)
    assert out['status'] == 'ok' and out['offset_px'] == [0, 0]
    assert out['reference']['rect_px'] == [40.0, 40.0, 60.0, 60.0]
    assert out['target']['rect_px'] == out['reference']['rect_px']
    assert out['target']['rect_map'] == out['reference']['rect_map'] == [40.0, 40.0, 60.0, 60.0]
    moved = lm.roi_map_rects(roi(), geo, corr_ok((0, 100, 0, 200, 7, -3)), SPREAD_PX)     # the field offset moves it once
    assert moved['target']['rect_px'] == [47.0, 37.0, 67.0, 57.0]


def test_a_um_origin_and_the_map_origin_and_block_enter_the_display_once():
    geo = {'reference': side(origin_um=(2.0, 1.0), map_origin=(10, 20), block=4), 'target': side(map_origin=(0, 0), block=2)}
    out = lm.roi_map_rects(roi(center=(5.0, 5.0)), geo, corr_ok((0, 100, 0, 200, 8, 4)), SPREAD_PX)
    assert out['reference']['rect_px'] == [20.0, 30.0, 40.0, 50.0]        # ((5 - 2)/0.1 = 30, (5 - 1)/0.1 = 40) +- 10
    assert out['reference']['rect_map'] == [2.5, 2.5, 7.5, 7.5]            # (p - (10, 20)) / 4
    assert out['target']['rect_px'] == [28.0, 34.0, 48.0, 54.0]
    assert out['target']['rect_map'] == [14.0, 17.0, 24.0, 27.0]           # p / 2


def test_roi_outside_the_unit_window_is_shifted_by_whole_periods_and_the_field_is_chosen_before_the_shift():
    window = ((50, 25), (100, 50))                       # a 100 x 50 px unit window inside the 200 x 100 frame
    geo = {'reference': side(window=window), 'target': side(window=((50, 25), (100, 50)))}
    cells = [(0, 100, 0, 100, 5, 0), (0, 100, 100, 200, 9, 0)]      # left half +5, right half +9
    out = lm.roi_map_rects(roi(center=(2.0, 1.0)), geo, corr_ok(*cells), SPREAD_PX)     # centre px (20, 10), x 10..30
    assert out['status'] == 'ok' and out['shifted'] and out['shift_px'] == [100, 50]
    assert out['offset_px'] == [5, 0]                              # the original place (left half), not the shifted one
    assert out['reference']['rect_px'] == [110.0, 50.0, 130.0, 70.0] and out['clipped'] is False
    assert out['target']['rect_px'] == [115.0, 50.0, 135.0, 70.0]
    inside = lm.roi_map_rects(roi(center=(10.0, 5.0)), geo, corr_ok(*cells), SPREAD_PX)
    assert inside['shifted'] is False and inside['shift_px'] == [0, 0]


def test_roi_outside_a_window_that_is_the_whole_frame_is_only_listed():
    geo = {'reference': side(), 'target': side()}
    out = lm.roi_map_rects(roi(center=(50.0, 5.0)), geo, corr_ok(), SPREAD_PX)        # px x = 500 > 200
    assert out['status'] == 'outside_map' and out['reference'] is None and out['target'] is None


def test_roi_cut_by_the_window_border_is_drawn_clipped_and_one_larger_than_the_window_is_cut_at_the_border():
    geo = {'reference': side(), 'target': side()}
    crossing = lm.roi_map_rects(roi(center=(0.5, 5.0)), geo, corr_ok(), SPREAD_PX)     # x -5 .. 15
    assert crossing['status'] == 'ok' and crossing['clipped'] is True and crossing['larger_than_window'] is False
    assert crossing['reference']['rect_px'] == [0.0, 40.0, 15.0, 60.0] and crossing['reference']['clipped'] is True
    big = lm.roi_map_rects(roi(center=(10.0, 5.0), window_um=40.0), geo, corr_ok(), SPREAD_PX)   # 400 px square
    assert big['larger_than_window'] is True and big['clipped'] is True
    assert big['reference']['rect_px'] == [0.0, 0.0, 200.0, 100.0]


def test_without_a_usable_field_the_a_rectangle_stays_and_the_b_rectangle_is_left_out():
    geo = {'reference': side(), 'target': side()}
    out = lm.roi_map_rects(roi(), geo, corr_band('coords_not_applied'), SPREAD_PX)
    assert out['reference']['rect_px'] == [40.0, 40.0, 60.0, 60.0] and out['offset_px'] is None
    assert out['target']['status'] == 'no_offset' and out['target']['reason'] == 'coords_not_applied'
    assert out['target']['rect_px'] is None
    wide =lm.roi_map_rects(roi(center=(10.0, 5.0), window_um=12.0),            # x 40..160 touches both cells
                            geo, corr_ok((0, 100, 0, 100, 0, 0), (0, 100, 100, 200, 50, 0)), SPREAD_PX)
    assert wide['target']['reason'] == 'offset_spread' and wide['reference']['rect_px'] is not None


def test_b_rectangle_outside_the_b_window_is_not_drawn():
    geo = {'reference': side(), 'target': side(window=((100, 0), (100, 100)))}
    out = lm.roi_map_rects(roi(), geo, corr_ok(), SPREAD_PX)        # B x 40..60 is left of the B window 100..200
    assert out['reference']['rect_px'] is not None and out['target']['status'] == 'outside_b_window'


def test_overlay_needs_one_offset_over_the_whole_region():
    box = (0, 0, 200, 100)
    assert lm.overlay_state(corr_ok((0, 100, 0, 200, 4, 2)), box, SPREAD_PX) == {
        'available': True, 'offset_px': [4, 2], 'reason': None}
    two = lm.overlay_state(corr_ok((0, 100, 0, 100, 0, 0), (0, 100, 100, 200, 50, 0)), box, SPREAD_PX)
    assert two == {'available': False, 'offset_px': None, 'reason': 'offset_spread'}
    assert lm.overlay_state(corr_ok((0, 100, 0, 100, 0, 0), (0, 100, 100, 200, 5, 0)), box, SPREAD_PX)['available']
    assert lm.overlay_state(corr_band('coords_stale'), box, SPREAD_PX) == {
        'available': False, 'offset_px': None, 'reason': 'coords_stale'}


# ---- (2) ledger-maps runs --------------------------------------------------------------------------------------------------

def query_changes(center, window_um=2.0, ref_hash=None, case_key='lateral_short|A|L1|B|L1|L1'):
    def change(queries, align):
        if ref_hash is not None:
            queries['inputs']['reference']['dataset_input_hash'] = ref_hash
        queries['directions'] = [{'direction': 'reference_to_target', 'queries': [{
            'query_id': 'q1', 'region_id': 'R1', 'merged_from': ['R1'], 'score_um2': 3.0, 'layers': ['L1'],
            'status': 'ready', 'center_xy_um': list(center), 'window_um': window_um, 'roi_ids': [],
            'contributing_cases': [{'case_key': case_key, 'change_kind': 'appeared', 'present_in': 'both',
                                    'residual_um2': 1.0, 'layer_changed': False}],
            'one_sided_cases': []}]}]
    return change


def origin_of(doc):
    return doc['sides']['reference']['frame']['origin_um']


def test_run_without_candidates_has_no_candidates_and_no_overlay(tmp_path, big):
    correspondence_chain(tmp_path, big, tiles_px(6, 9, 6, 9), tiles_px(16, 19, 10, 13))
    code, doc = run_cards(tmp_path)
    assert code == 0
    assert doc['changes']['link_status'] == 'no_candidates' and doc['changes']['rois'] == []
    assert doc['changes']['coords_status'] == 'coords_no_candidates'
    assert doc['overlay'] == {'available': False, 'offset_px': None, 'reason': 'coords_no_candidates'}


def candidates_run(tmp_path, big, center, mode='stretch', **kw):
    shape = correspondence_chain(tmp_path, big, tiles_px(6, 9, 6, 9), tiles_px(16, 19, 10, 13))
    code, doc0 = run_cards(tmp_path, out='plain')
    ox, oy = origin_of(doc0)
    write_candidates(tmp_path, shape, shape, mode=mode, cells=[(0, 100, 0, 200, 50, 20)],
                     changes=query_changes((ox + center[0], oy + center[1]), **kw))
    return run_cards(tmp_path, '--candidates', str(tmp_path / 'cand'))


def test_run_with_04_gives_roi_rectangles_on_both_maps_and_an_overlay(tmp_path, big):
    code, doc = candidates_run(tmp_path, big, (3.0, 4.0))               # 30 x 40 px, 2 um = 20 px window
    ch = doc['changes']
    assert code == 0 and ch['link_status'] == 'ok' and ch['coords_status'] == 'ok'
    (roi,) = ch['rois']
    assert roi['roi_key'] == 'roi:R1'
    m = roi['map']
    assert m['status'] == 'ok' and m['offset_px'] == [50, 20] and m['clipped'] is False and m['shifted'] is False
    ox, oy = doc['sides']['reference']['region']['origin_px']
    block = doc['sides']['reference']['region']['block_px']
    assert m['reference']['rect_px'] == [20.0, 30.0, 40.0, 50.0]
    assert m['reference']['rect_map'] == [pytest.approx((20.0 - ox) / block), pytest.approx((30.0 - oy) / block),
                                          pytest.approx((40.0 - ox) / block), pytest.approx((50.0 - oy) / block)]
    assert m['target']['rect_px'] == [70.0, 50.0, 90.0, 70.0]            # A + the field offset (50, 20) px, once
    assert doc['overlay'] == {'available': True, 'offset_px': [50, 20], 'reason': None}
    assert ch['outside_map'] == [] and ch['summary']['roi_count'] == 1 and ch['summary']['rois_outside_map'] == 0
    (case,) = roi['cases']
    assert (case['status'], case['pair_id']) == ('linked', 'AB') and ch['pairs']['AB']['roi_keys'] == ['roi:R1']
    assert ch['summary']['linked_pairs'] + ch['summary']['unlinked_pairs'] == ch['summary']['pair_denominator']


def test_roi_beyond_the_frame_is_listed_as_outside_map(tmp_path, big):
    code, doc = candidates_run(tmp_path, big, (500.0, 4.0))
    ch = doc['changes']
    assert code == 0 and ch['link_status'] == 'ok' and ch['outside_map'] == ['roi:R1']
    assert ch['rois'][0]['map']['status'] == 'outside_map' and ch['rois'][0]['map']['reference'] is None
    assert ch['summary']['rois_outside_map'] == 1


def test_roi_on_the_window_border_is_clipped(tmp_path, big):
    code, doc = candidates_run(tmp_path, big, (0.5, 4.0))                # x -5 .. 15 px
    m = doc['changes']['rois'][0]['map']
    assert code == 0 and m['status'] == 'ok' and m['clipped'] is True and m['reference']['clipped'] is True
    assert m['reference']['rect_px'][0] == 0.0 and m['reference']['rect_px'][2] == 15.0


def test_diagnostic_04_keeps_the_links_but_has_no_b_rectangle(tmp_path, big):
    code, doc = candidates_run(tmp_path, big, (3.0, 4.0), mode='diagnostic')
    ch = doc['changes']
    assert code == 0 and ch['link_status'] == 'ok' and ch['coords_status'] == 'coords_not_applied'
    m = ch['rois'][0]['map']
    assert m['reference']['rect_px'] == [20.0, 30.0, 40.0, 50.0]
    assert m['target']['status'] == 'no_offset' and m['target']['rect_px'] is None
    assert doc['overlay'] == {'available': False, 'offset_px': None, 'reason': 'coords_not_applied'}


def test_dataset_hash_mismatch_is_stale_and_links_nothing(tmp_path, big):
    code, doc = candidates_run(tmp_path, big, (3.0, 4.0), ref_hash='0' * 64)
    ch = doc['changes']
    assert ch['link_status'] == 'stale' and ch['rois'] == [] and ch['pairs'] == {} and ch['items'] == {}
    assert ch['summary']['roi_count'] == 0 and ch['outside_map'] == []
    assert 'reference' in ch['detail']
