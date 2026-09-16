# preprocess.py contract

Imports: numpy, PIL.Image, dataclasses. No torch.

- `letterbox(image: np.ndarray, out_hw: tuple[int, int], *, binary: bool)
  -> tuple[np.ndarray, np.ndarray, dict]`
  image must be 2-D non-empty (else ValueError). scale = min(outH/H, outW/W);
  new_h = max(1, round(H*scale)), new_w = max(1, round(W*scale)). Resize with
  Pillow mode "F" (float32): NEAREST if binary else BILINEAR. Place centered:
  offset_y = (outH-new_h)//2, offset_x = (outW-new_w)//2. Output float32 zeros
  elsewhere; valid mask float32 1.0 on the placed region, 0.0 on padding.
  If binary, output values are thresholded to {0,1} (> 0.5).
  info = {"scale": float, "offset_y": int, "offset_x": int, "src_hw": [H, W]}.
- `resized_pixel_size(pixel_size: float | None, info: dict) -> float | None`:
  pixel_size / scale, or None when pixel_size is None.
- `legend_to_values(rgb: np.ndarray, legend: dict | None) -> np.ndarray`
  legend = {"legend_id": str, "entries": [{"rgb": [r,g,b], "value": float}, ...],
  "tolerance": int (default 0)}. None/empty entries/rgb not HxWx3 -> ValueError.
  Each pixel -> value of the entry with smallest max-abs channel difference if that
  difference <= tolerance; any unmapped pixel -> ValueError (count in message).
  Returns float32 HxW.
- `align_curve(sizes, values, observed, grid, method="exact")
  -> tuple[np.ndarray, np.ndarray]` returns (float64 values with NaN, bool mask).
  "exact": grid point g takes the observed value at size with
  np.isclose(size, g, rtol=1e-6, atol=0).
  "loglinear_within_range": exact match first; else if the nearest OBSERVED sizes
  s0 < g < s1 exist, linear interpolation of value in log(size); never outside the
  observed range. Unknown method or length mismatch -> ValueError. Never fill 0.
- `safe_log10(values: np.ndarray, mask: np.ndarray, floor: float = 1e-12) -> np.ndarray`:
  log10(max(v, floor)) where mask, NaN elsewhere.

```python
@dataclass
class Normalizer:
    mean: np.ndarray   # (D,)
    std: np.ndarray    # (D,)
    @classmethod
    def fit(cls, x, mask, rows) -> "Normalizer"
    def transform(self, x, mask) -> np.ndarray
    def to_dict(self) -> dict      # lists of floats
    @classmethod
    def from_dict(cls, d) -> "Normalizer"
```
fit: x (N,D) float, mask (N,D) bool, rows = integer index array; statistics use
ONLY rows[i] entries where mask is True. Column with no observed entry -> mean 0,
std 1; std < 1e-8 -> 1. transform: (x-mean)/std where mask, 0.0 elsewhere (0 is a
placeholder only; the mask remains the source of truth). NaN inputs where mask is
False must not produce NaN outputs.
