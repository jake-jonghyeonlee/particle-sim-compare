"""Tests for ``simcompare.preprocess``.

Covers the contract in ``docs/arch/preprocess.md``: aspect-ratio preserving
letterboxing with a valid-area mask, explicit-legend colour-to-value conversion
(including the no-legend / unmapped-colour errors), extrapolation-free
CA-curve alignment and a train-rows-only ``Normalizer`` that never propagates
NaN from masked-out entries.

All cases run on small synthetic arrays on CPU in seconds.
"""

from __future__ import annotations

import numpy as np
import pytest

from simcompare.preprocess import (
    Normalizer,
    align_curve,
    legend_to_values,
    letterbox,
    resized_pixel_size,
    safe_log10,
)


# --------------------------------------------------------------------------- #
# letterbox
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "src, out_hw, exp_scale, exp_off, exp_new",
    [
        # (H, W) source, (outH, outW) canvas, scale, (offset_y, offset_x), (new_h, new_w)
        ((2, 4), (8, 8), 2.0, (2, 0), (4, 8)),
        ((4, 2), (8, 8), 2.0, (0, 2), (8, 4)),
        ((3, 5), (10, 10), 2.0, (2, 0), (6, 10)),
        ((5, 3), (10, 10), 2.0, (0, 2), (10, 6)),
        ((1, 1), (5, 5), 5.0, (0, 0), (5, 5)),
        ((4, 8), (8, 8), 1.0, (2, 0), (4, 8)),
    ],
)
def test_letterbox_geometry(src, out_hw, exp_scale, exp_off, exp_new):
    image = np.zeros(src, dtype=np.float32)
    canvas, mask, info = letterbox(image, out_hw, binary=False)

    assert canvas.shape == out_hw
    assert mask.shape == out_hw
    assert canvas.dtype == np.float32
    assert mask.dtype == np.float32

    assert info["src_hw"] == list(src)
    assert info["offset_y"] == exp_off[0]
    assert info["offset_x"] == exp_off[1]
    assert info["scale"] == pytest.approx(exp_scale)

    ny, nx = exp_new
    oy, ox = exp_off
    assert np.all(mask[oy : oy + ny, ox : ox + nx] == 1.0)
    assert mask.sum() == ny * nx
    assert np.all(canvas[oy : oy + ny, ox : ox + nx] == 0.0)
    assert np.all(canvas[mask == 0.0] == 0.0)


def test_letterbox_binary_thresholding():
    image = np.array([[0.0, 0.4], [0.6, 1.0]], dtype=np.float32)
    canvas, mask, _ = letterbox(image, (2, 2), binary=True)
    assert np.array_equal(canvas, np.array([[0.0, 0.0], [1.0, 1.0]], dtype=np.float32))
    assert np.all(mask == 1.0)


def test_letterbox_rejects_bad_input():
    with pytest.raises(ValueError):
        letterbox(np.zeros((2, 2, 3), dtype=np.float32), (4, 4), binary=False)
    with pytest.raises(ValueError):
        letterbox(np.zeros((0, 4), dtype=np.float32), (4, 4), binary=False)
    with pytest.raises(ValueError):
        letterbox(np.zeros((2, 2), dtype=np.float32), (0, 4), binary=False)


def test_resized_pixel_size():
    info = {"scale": 2.0}
    assert resized_pixel_size(4.0, info) == pytest.approx(2.0)
    assert resized_pixel_size(None, info) is None


# --------------------------------------------------------------------------- #
# legend_to_values
# --------------------------------------------------------------------------- #
def _legend(entries, tolerance=0):
    return {"legend_id": "test", "entries": entries, "tolerance": tolerance}


def test_legend_to_values_maps_pixels():
    rgb = np.zeros((2, 2, 3), dtype=np.uint8)
    rgb[0, 0] = [255, 0, 0]
    rgb[0, 1] = [0, 255, 0]
    rgb[1, 0] = [0, 0, 255]
    rgb[1, 1] = [255, 255, 255]
    legend = _legend(
        [
            {"rgb": [255, 0, 0], "value": 0.0},
            {"rgb": [0, 255, 0], "value": 0.5},
            {"rgb": [0, 0, 255], "value": 1.0},
            {"rgb": [255, 255, 255], "value": 2.0},
        ]
    )
    out = legend_to_values(rgb, legend)
    assert out.shape == (2, 2)
    assert out.dtype == np.float32
    assert np.allclose(out, [[0.0, 0.5], [1.0, 2.0]])


def test_legend_to_values_nearest_within_tolerance():
    rgb = np.zeros((1, 1, 3), dtype=np.uint8)
    rgb[0, 0] = [250, 0, 0]  # 5 away from red
    legend = _legend([{"rgb": [255, 0, 0], "value": 1.0}], tolerance=10)
    assert legend_to_values(rgb, legend)[0, 0] == pytest.approx(1.0)


def test_legend_to_values_rejects_unmapped():
    rgb = np.zeros((1, 2, 3), dtype=np.uint8)
    rgb[0, 0] = [255, 0, 0]
    rgb[0, 1] = [128, 128, 128]  # far from red
    legend = _legend([{"rgb": [255, 0, 0], "value": 1.0}], tolerance=0)
    with pytest.raises(ValueError, match="1 pixels"):
        legend_to_values(rgb, legend)


@pytest.mark.parametrize(
    "rgb, legend",
    [
        (np.zeros((2, 2, 3), dtype=np.uint8), None),  # no legend
        (np.zeros((2, 2, 3), dtype=np.uint8), _legend([])),  # empty entries
        (np.zeros((2, 2), dtype=np.uint8), _legend([{"rgb": [0, 0, 0], "value": 0.0}])),  # rgb not HxWx3
        (np.zeros((2, 2, 3), dtype=np.uint8), "not-a-dict"),  # legend not a dict
    ],
)
def test_legend_to_values_rejects_bad_shape_or_legend(rgb, legend):
    with pytest.raises(ValueError):
        legend_to_values(rgb, legend)


# --------------------------------------------------------------------------- #
# align_curve
# --------------------------------------------------------------------------- #
def test_align_curve_exact():
    sizes = np.array([1.0, 2.0, 4.0])
    values = np.array([10.0, 20.0, 40.0])
    observed = np.array([True, True, True])
    grid = np.array([2.0, 8.0, 4.0])
    out, mask = align_curve(sizes, values, observed, grid, method="exact")
    assert np.allclose(out, [20.0, np.nan, 40.0], equal_nan=True)
    assert np.array_equal(mask, [True, False, True])
    assert np.isnan(out[1])


def test_align_curve_exact_ignores_unobserved():
    sizes = np.array([1.0, 2.0])
    values = np.array([10.0, 20.0])
    observed = np.array([False, True])
    grid = np.array([1.0, 2.0])
    out, mask = align_curve(sizes, values, observed, grid, method="exact")
    assert np.isnan(out[0])
    assert out[1] == pytest.approx(20.0)
    assert np.array_equal(mask, [False, True])


def test_align_curve_loglinear_within_range():
    sizes = np.array([1.0, 10.0])
    values = np.array([0.0, 1.0])
    observed = np.array([True, True])
    grid = np.array([1.0, 10.0, 100.0, 0.5])
    out, mask = align_curve(sizes, values, observed, grid, method="loglinear_within_range")
    assert out[0] == pytest.approx(0.0)
    assert out[1] == pytest.approx(1.0)
    assert np.isnan(out[2])  # 100 > max observed -> no extrapolation
    assert np.isnan(out[3])  # 0.5 < min observed -> no extrapolation
    assert np.array_equal(mask, [True, True, False, False])


def test_align_curve_loglinear_interpolates_in_log_space():
    # log10(10) = 1 is the midpoint between log10(1)=0 and log10(100)=2
    sizes = np.array([1.0, 100.0])
    values = np.array([0.0, 2.0])
    observed = np.array([True, True])
    grid = np.array([10.0])
    out, mask = align_curve(sizes, values, observed, grid, method="loglinear_within_range")
    assert out[0] == pytest.approx(1.0)
    assert mask[0]


def test_align_curve_rejects_bad_method_or_shape():
    sizes = np.array([1.0, 2.0])
    values = np.array([1.0, 2.0])
    observed = np.array([True, True])
    grid = np.array([1.0])
    with pytest.raises(ValueError):
        align_curve(sizes, values, observed, grid, method="nope")
    with pytest.raises(ValueError):
        align_curve(sizes, values[:1], observed, grid, method="exact")


def test_safe_log10():
    values = np.array([1.0, 100.0, 0.0, -5.0])
    mask = np.array([True, True, True, False])
    out = safe_log10(values, mask)
    assert out[0] == pytest.approx(0.0)
    assert out[1] == pytest.approx(2.0)
    assert out[2] == pytest.approx(-12.0)  # floored at 1e-12
    assert np.isnan(out[3])


# --------------------------------------------------------------------------- #
# Normalizer
# --------------------------------------------------------------------------- #
def test_normalizer_uses_only_train_rows():
    x = np.array([[1.0, 10.0], [2.0, 20.0], [100.0, 100.0]])
    mask = np.ones((3, 2), dtype=bool)
    norm = Normalizer.fit(x, mask, rows=np.array([0, 1]))
    assert np.allclose(norm.mean, [1.5, 15.0])
    assert np.allclose(norm.std, [0.5, 5.0])
    out = norm.transform(x, mask)
    assert out[0, 0] == pytest.approx(-1.0)
    assert out[1, 0] == pytest.approx(1.0)
    assert out[2, 0] == pytest.approx(197.0)  # test row transformed, not used in fit
    assert out[2, 1] == pytest.approx(17.0)


def test_normalizer_masked_out_entries_ignored_in_stats():
    x = np.array([[1.0, 5.0], [2.0, 6.0], [3.0, 7.0]])
    mask = np.array([[True, False], [True, False], [True, True]])
    norm = Normalizer.fit(x, mask, rows=np.array([0, 1, 2]))
    assert norm.mean[0] == pytest.approx(2.0)
    assert norm.std[0] == pytest.approx(np.std([1.0, 2.0, 3.0]))
    assert norm.mean[1] == pytest.approx(7.0)  # only row 2 observed in col 1
    assert norm.std[1] == pytest.approx(1.0)  # single value -> std 0 -> clamped to 1


def test_normalizer_no_observed_column_defaults():
    x = np.array([[1.0, 2.0], [3.0, 4.0]])
    mask = np.array([[True, False], [True, False]])
    norm = Normalizer.fit(x, mask, rows=np.array([0, 1]))
    assert norm.mean[1] == 0.0
    assert norm.std[1] == 1.0


def test_normalizer_transform_zero_outside_mask_and_no_nan_propagation():
    x = np.array([[1.0, np.nan], [2.0, np.nan]])
    mask = np.array([[True, False], [True, False]])
    norm = Normalizer.fit(x, mask, rows=np.array([0, 1]))
    out = norm.transform(x, mask)
    assert np.all(np.isfinite(out))  # NaN in masked-out column must not propagate
    assert np.all(out[:, 1] == 0.0)
    assert out[0, 0] == pytest.approx(-1.0)
    assert out[1, 0] == pytest.approx(1.0)


def test_normalizer_roundtrip_dict():
    x = np.array([[1.0, 2.0], [3.0, 4.0]])
    mask = np.ones((2, 2), dtype=bool)
    norm = Normalizer.fit(x, mask, rows=np.array([0, 1]))
    d = norm.to_dict()
    assert isinstance(d["mean"], list) and isinstance(d["std"], list)
    restored = Normalizer.from_dict(d)
    assert np.allclose(restored.mean, norm.mean)
    assert np.allclose(restored.std, norm.std)
    assert np.allclose(restored.transform(x, mask), norm.transform(x, mask))
