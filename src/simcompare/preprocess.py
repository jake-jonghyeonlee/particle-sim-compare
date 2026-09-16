"""Preprocessing helpers for simcompare.

Implements the contract in ``docs/arch/preprocess.md``: aspect-ratio preserving
letterboxing with a valid-area mask, explicit-legend colour-to-value conversion,
extrapolation-free CA-curve alignment and a train-rows-only ``Normalizer``.

Only numpy, Pillow and the standard library are used.  No torch.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
from PIL import Image


# --------------------------------------------------------------------------- #
# letterbox
# --------------------------------------------------------------------------- #
def letterbox(
    image: np.ndarray,
    out_hw: tuple[int, int],
    *,
    binary: bool,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Resize *image* preserving aspect ratio and centre it in an ``out_hw`` canvas.

    Returns ``(canvas, valid_mask, info)`` where the canvas is float32, the mask
    is float32 (1.0 on the placed region, 0.0 on padding) and *info* records the
    geometry.  When *binary* is true the canvas values are thresholded to {0, 1}.
    """
    if not isinstance(image, np.ndarray) or image.ndim != 2 or image.size == 0:
        raise ValueError("image must be a 2-D non-empty ndarray")

    out_h, out_w = out_hw
    if out_h <= 0 or out_w <= 0:
        raise ValueError("out_hw must contain positive dimensions")

    h, w = image.shape
    scale = min(out_h / h, out_w / w)
    new_h = max(1, round(h * scale))
    new_w = max(1, round(w * scale))

    src = Image.fromarray(image.astype(np.float32, copy=False), mode="F")
    resample = Image.NEAREST if binary else Image.BILINEAR
    resized = src.resize((new_w, new_h), resample)
    resized_arr = np.asarray(resized, dtype=np.float32)

    canvas = np.zeros((out_h, out_w), dtype=np.float32)
    mask = np.zeros((out_h, out_w), dtype=np.float32)

    offset_y = (out_h - new_h) // 2
    offset_x = (out_w - new_w) // 2
    canvas[offset_y : offset_y + new_h, offset_x : offset_x + new_w] = resized_arr
    mask[offset_y : offset_y + new_h, offset_x : offset_x + new_w] = 1.0

    if binary:
        canvas = (canvas > 0.5).astype(np.float32)

    info = {
        "scale": float(scale),
        "offset_y": int(offset_y),
        "offset_x": int(offset_x),
        "src_hw": [int(h), int(w)],
    }
    return canvas, mask, info


# --------------------------------------------------------------------------- #
# resized_pixel_size
# --------------------------------------------------------------------------- #
def resized_pixel_size(pixel_size: float | None, info: dict) -> float | None:
    """Map a pixel size from the resized frame back to the source frame."""
    if pixel_size is None:
        return None
    scale = info["scale"]
    if scale == 0:
        raise ValueError("scale must be non-zero")
    return float(pixel_size) / float(scale)


# --------------------------------------------------------------------------- #
# legend_to_values
# --------------------------------------------------------------------------- #
def legend_to_values(rgb: np.ndarray, legend: dict | None) -> np.ndarray:
    """Convert an HxWx3 RGB array to float32 values using an explicit legend.

    Each pixel is mapped to the legend entry with the smallest max-abs channel
    difference, provided that difference is within ``tolerance``.  Any unmapped
    pixel raises ``ValueError`` reporting the number of such pixels.
    """
    if not isinstance(rgb, np.ndarray) or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("rgb must be an HxWx3 ndarray")
    if legend is None or not isinstance(legend, dict):
        raise ValueError("legend must be a dict")
    entries = legend.get("entries")
    if not entries:
        raise ValueError("legend entries must not be empty")

    tolerance = float(legend.get("tolerance", 0))
    table = np.array([e["rgb"] for e in entries], dtype=np.float64)
    values = np.array([e["value"] for e in entries], dtype=np.float64)

    px = rgb.astype(np.float64)
    diff = np.abs(px[..., None, :] - table[None, None, :, :])
    maxdiff = diff.max(axis=-1)  # (H, W, E)
    best = maxdiff.argmin(axis=-1)  # (H, W)
    best_diff = np.take_along_axis(maxdiff, best[..., None], axis=-1)[..., 0]

    out = np.full(px.shape[:2], np.nan, dtype=np.float32)
    valid = best_diff <= tolerance
    out[valid] = values[best[valid]].astype(np.float32)

    if not np.all(valid):
        count = int((~valid).sum())
        raise ValueError(f"{count} pixels could not be mapped to the legend")

    return out


# --------------------------------------------------------------------------- #
# align_curve
# --------------------------------------------------------------------------- #
def align_curve(
    sizes,
    values,
    observed,
    grid,
    method: str = "exact",
) -> tuple[np.ndarray, np.ndarray]:
    """Align *values* (at *sizes*) onto *grid* without extrapolation.

    Returns ``(out, mask)`` where *out* is float64 (NaN where unknown) and *mask*
    is a bool array marking grid points that received a value.
    """
    sizes = np.asarray(sizes, dtype=np.float64)
    values = np.asarray(values, dtype=np.float64)
    observed = np.asarray(observed, dtype=bool)
    grid = np.asarray(grid, dtype=np.float64)

    if sizes.shape != values.shape or sizes.ndim != 1:
        raise ValueError("sizes and values must be 1-D and the same length")
    if observed.shape != sizes.shape:
        raise ValueError("observed must match the length of sizes")
    if grid.ndim != 1:
        raise ValueError("grid must be 1-D")

    if method == "exact":
        out = np.full(grid.shape, np.nan, dtype=np.float64)
        mask = np.zeros(grid.shape, dtype=bool)
        obs_idx = np.where(observed)[0]
        for j, g in enumerate(grid):
            for i in obs_idx:
                if np.isclose(sizes[i], g, rtol=1e-6, atol=0.0):
                    out[j] = values[i]
                    mask[j] = True
                    break
        return out, mask

    if method == "loglinear_within_range":
        out = np.full(grid.shape, np.nan, dtype=np.float64)
        mask = np.zeros(grid.shape, dtype=bool)
        obs_idx = np.where(observed)[0]
        obs_sizes = sizes[obs_idx]
        obs_values = values[obs_idx]
        order = np.argsort(obs_sizes)
        obs_sizes = obs_sizes[order]
        obs_values = obs_values[order]
        for j, g in enumerate(grid):
            exact = np.where(np.isclose(obs_sizes, g, rtol=1e-6, atol=0.0))[0]
            if exact.size:
                out[j] = obs_values[exact[0]]
                mask[j] = True
                continue
            if g <= obs_sizes[0] or g >= obs_sizes[-1]:
                continue  # never extrapolate outside the observed range
            k = np.searchsorted(obs_sizes, g)
            s0, s1 = obs_sizes[k - 1], obs_sizes[k]
            if s0 <= g <= s1:
                t = (math.log(g) - math.log(s0)) / (math.log(s1) - math.log(s0))
                out[j] = obs_values[k - 1] + t * (obs_values[k] - obs_values[k - 1])
                mask[j] = True
        return out, mask

    raise ValueError(f"unknown method: {method!r}")


# --------------------------------------------------------------------------- #
# safe_log10
# --------------------------------------------------------------------------- #
def safe_log10(values: np.ndarray, mask: np.ndarray, floor: float = 1e-12) -> np.ndarray:
    """``log10(max(v, floor))`` where *mask* is true, NaN elsewhere."""
    values = np.asarray(values, dtype=np.float64)
    mask = np.asarray(mask, dtype=bool)
    out = np.full(values.shape, np.nan, dtype=np.float64)
    out[mask] = np.log10(np.maximum(values[mask], floor))
    return out


# --------------------------------------------------------------------------- #
# Normalizer
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class Normalizer:
    """Standard-scaler fit on a subset of rows only.

    ``mean`` and ``std`` have shape ``(D,)``.  Statistics are computed from the
    entries of *rows* where *mask* is true; a column with no observed entry gets
    mean 0 / std 1, and a std below ``1e-8`` is clamped to 1.
    """

    mean: np.ndarray  # (D,)
    std: np.ndarray  # (D,)

    @classmethod
    def fit(cls, x, mask, rows) -> "Normalizer":
        x = np.asarray(x, dtype=np.float64)
        mask = np.asarray(mask, dtype=bool)
        rows = np.asarray(rows, dtype=np.intp)

        if x.ndim != 2:
            raise ValueError("x must be 2-D")
        if mask.shape != x.shape:
            raise ValueError("mask must match the shape of x")
        if rows.ndim != 1:
            raise ValueError("rows must be 1-D")

        n, d = x.shape
        if rows.size == 0:
            return cls(np.zeros(d, dtype=np.float64), np.ones(d, dtype=np.float64))

        sel = x[rows]
        sel_mask = mask[rows]

        mean = np.zeros(d, dtype=np.float64)
        std = np.ones(d, dtype=np.float64)
        for j in range(d):
            col = sel[:, j]
            m = sel_mask[:, j]
            if not np.any(m):
                continue
            vals = col[m]
            mu = float(np.mean(vals))
            sd = float(np.std(vals))
            mean[j] = mu
            std[j] = sd if sd >= 1e-8 else 1.0

        return cls(mean, std)

    def transform(self, x, mask) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        mask = np.asarray(mask, dtype=bool)
        if x.ndim != 2:
            raise ValueError("x must be 2-D")
        if mask.shape != x.shape:
            raise ValueError("mask must match the shape of x")

        out = np.zeros(x.shape, dtype=np.float64)
        out[mask] = (x[mask] - self.mean) / self.std
        return out

    def to_dict(self) -> dict:
        return {
            "mean": [float(v) for v in np.asarray(self.mean).ravel()],
            "std": [float(v) for v in np.asarray(self.std).ravel()],
        }

    @classmethod
    def from_dict(cls, d) -> "Normalizer":
        return cls(
            np.asarray(d["mean"], dtype=np.float64),
            np.asarray(d["std"], dtype=np.float64),
        )
