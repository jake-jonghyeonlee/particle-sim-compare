"""roi-detail/T2 fix: wiring of ``ImageSource.collect_bases(diag_margin=r)`` -> ``change_candidates._local_alignment_document``.

Non-zero global offset, reference/target frames of different size, one different local shift per ROI (positive and negative, x != y),
electrode + CNT layers. Asserts the (x, y) order and the values written to the document, and the linear fit of a position-proportional shift.
"""
import math

import numpy as np
import pytest

from simcompare import alignment_report, change_candidates as cc

REF_SHAPE, TGT_SHAPE = (400, 500), (440, 460)
DX, DY = 7, -5
W, R, PITCH = 40, 5, 0.1
PASTE = 12          # margin around the ROI box that carries the shifted content


def pattern(seed, n=2600, size=(2, 9)):
    rng = np.random.default_rng(seed)
    p = np.zeros(REF_SHAPE, bool)
    for _ in range(n):
        y, x = int(rng.integers(0, REF_SHAPE[0] - 10)), int(rng.integers(0, REF_SHAPE[1] - 10))
        p[y:y + int(rng.integers(*size)), x:x + int(rng.integers(*size))] = True
    return p


def target_of(pat, rois):
    """target[Y, X] = reference[Y - DY - ddy, X - DX - ddx] around each ROI box; shift None leaves the region empty."""
    tgt = np.zeros(TGT_SHAPE, bool)
    for origin, shift in rois:
        if shift is None:
            continue
        ddy, ddx = shift
        y0, y1, x0, x1 = alignment_report._crop_box(origin[0], origin[1], W, *REF_SHAPE)
        ra, rb, ca, cb = y0 - PASTE, y1 + PASTE, x0 - PASTE, x1 + PASTE
        tgt[ra + DY + ddy:rb + DY + ddy, ca + DX + ddx:cb + DX + ddx] = pat[ra:rb, ca:cb]
    return tgt


def document(monkeypatch, rois):
    monkeypatch.setattr(alignment_report, 'layer_bundles', lambda q, w: ([{'layer': 'E'}], []))
    cnt, el = pattern(1), pattern(2, n=1500, size=(3, 12))
    src = alignment_report.ImageSource({'C1': cnt}, {'C1': target_of(cnt, rois)}, np.ones(REF_SHAPE, bool),
                                       np.ones(TGT_SHAPE, bool), (DX, DY))
    windows = [{'window_origin_px': [y, x], 'window_px': W, 'layer': 'L'} for (y, x), _ in rois]
    src.hot = [{'box': alignment_report._crop_box(y, x, W, *REF_SHAPE)} for (y, x), _ in rois]
    el_tgt = target_of(el, rois)
    src.collect_bases({'E': list(range(len(rois)))}, lambda layer: el, lambda layer: el_tgt, diag_margin=R)
    queries = [{'query_id': 'q%d' % i} for i in range(len(rois))]
    args = {'align_layer_tolerance_um': 0.2, 'align_min_dice': 0.3, 'local_align_um': R * PITCH}
    return cc._local_alignment_document(src, {'r_px': R}, queries, windows, PITCH, args, (DX, DY), REF_SHAPE)


FIXED = [((40, 40), (2, -3)), ((60, 360), (-4, 1)), ((160, 200), (3, 3)), ((280, 60), (-1, -2)), ((290, 370), (0, 0))]


def test_each_roi_reports_its_own_local_offset_in_xy_order(monkeypatch):
    doc = document(monkeypatch, FIXED)
    assert doc['global_offset_xy_px'] == [DX, DY] and doc['search_px'] == R
    for item, (_, (ddy, ddx)) in zip(doc['rois'], FIXED):
        assert item['status'] == 'ok', item
        assert item['local_offset_xy_px'] == [DX + ddx, DY + ddy]
        assert item['local_offset_xy_um'] == [round((DX + ddx) * PITCH, 6), round((DY + ddy) * PITCH, 6)]
        assert item['delta_from_global_xy_um'] == [round(ddx * PITCH, 6), round(ddy * PITCH, 6)]
        assert item['delta_from_global_um'] == round(math.hypot(ddx * PITCH, ddy * PITCH), 6)
        assert item['ring_delta_xy_px'] == [ddx, ddy]
        assert {u['source'] for u in item['layers_used']} == {'electrode', 'cnt'}
        assert item['dice_local'] == 1.0
    assert doc['diagnostic_estimated_bytes'] if 'diagnostic_estimated_bytes' in doc else True


def test_position_proportional_shift_gives_the_signed_scale_and_drops_non_ok_rois(monkeypatch):
    origins = [(40, 40), (60, 360), (160, 200), (280, 60), (290, 370), (180, 330)]
    rois = []
    for oy, ox in origins:
        cx, cy = ox + W / 2.0, oy + W / 2.0
        rois.append(((oy, ox), (int(round(-0.015 * (cy - 200))), int(round(0.01 * (cx - 250))))))
    rois.append(((300, 230), None))               # empty target -> not ok
    doc = document(monkeypatch, rois)
    statuses = [r['status'] for r in doc['rois']]
    assert statuses[:-1] == ['ok'] * 6 and statuses[-1] == 'insufficient_support'
    fit = doc['linear_fit']
    assert fit is not None and fit['n'] == 6
    xs = np.array([ox + W / 2.0 for _, ox in origins])
    ys = np.array([oy + W / 2.0 for oy, _ in origins])
    px = np.array([s[1] for _, s in rois[:-1]]) + DX
    py = np.array([s[0] for _, s in rois[:-1]]) + DY
    assert fit['scale_ppm_xy'] == [pytest.approx(np.polyfit(xs, px, 1)[0] * 1e6, abs=1e-2),
                                   pytest.approx(np.polyfit(ys, py, 1)[0] * 1e6, abs=1e-2)]
    assert 7000 < fit['scale_ppm_xy'][0] < 13000 and -19000 < fit['scale_ppm_xy'][1] < -11000      # x +, y -, about 1.0 % and -1.5 %
    assert doc['linear_fit_reason'] is None
