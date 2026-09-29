# pair_geometry.py contract (electrode-pair dimensions from layout masks)

Adjustable dimensions measured on binary layout masks (`array > 0.5`) with pixel-centre
distances (sklearn `KDTree`). Values are pixel-level approximations (about ±1 pixel; exact for
horizontal, vertical and 45° edges) and do not replace design dimensions.

`PAIR_FEATURES = ("pair:min_gap", "pair:near_gap_len", "pair:overlap_area", "pair:a_min_width",
"pair:b_min_width")`; lengths use the canonical length unit, area its square.

- `boundary(mask)`: mask pixels 4-adjacent to in-image background; the image edge is not a boundary.
- `min_width_px(mask)`: ridge pixels of the distance-to-background field (not smaller than any
  8-neighbour) measured by the shortest of the horizontal, vertical and two diagonal runs through
  them; runs cut by the image edge are ignored. Empty mask or no background -> NaN.
- `pair_metrics_px(a, b | None, near_margin_px)`: `min_gap` = nearest boundary centre distance − 1
  (0 when the projections overlap); `near_gap_len` = count of a-boundary pixels whose nearest
  b-boundary distance ≤ nearest + margin (NaN when overlapping); `overlap_area` = pixels in a ∧ b;
  widths per electrode. `b=None` (open) fills only `a_min_width`.
- `bundle_pixel_size(bundle, length_unit)`: the single layout `pixel_size` converted from
  `physical_length_unit`; None when missing or different between electrodes.
- `bundle_pair_metrics(bundle, *, length_unit="um", near_margin=1.0)`: scaled metrics; all NaN
  without a physical pixel size, arrays, or equal shapes (never estimated).
- `pair_metrics_table(bundles, *, length_unit, near_margin)` -> DataFrame `sample_id` + features.
- `RELEVANT_FEATURES[failure_type]`: lateral_short = gap, near-gap length, widths;
  vertical_short = overlap area, gap, widths; lateral_open = a width.
