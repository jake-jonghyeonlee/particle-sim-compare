# variants.py contract (local-edit candidates and variant re-comparison)

## `edit_candidates(comparison, deltas, bundles, *, length_unit="um") -> (spec: dict, table: DataFrame)`

`spec` is a particle-sim `make-variant` edit spec (`schema_version "1.0"`, `length_unit "um"`,
`note`, `candidates`); each candidate has exactly `candidate_id, sample_id, change_description,
edits, basis, info`, and each edit `op, slot, target_slot, roi_um, amount_um`. Only local edits
are proposed (no global CD bias / overlay). For `neighborhood_comparison` rows with `status == ok`:

- lateral_short with `pair:min_gap` delta > 0: `trim_near` for slot b then a, amounts
  `delta × TRIM_FRACTIONS (0.5, 1.0)` in um.
- vertical_short with `pair:overlap_area` delta < 0: `remove_overlap` for slot b then a, amount 0.
- lateral_open: skipped (widening/additive edits are not supported); other cases skipped with reason.

ROI (um, upper-left origin): the 4-connected region ≥ 50% of the peak of the query's hotspot at the
largest compared size containing the peak, bounding box padded by that particle size; fallback
`closest_zone` (overlap or nearest boundary pixels of both electrodes) when no usable hotspot or
the edited electrode is outside. `candidate_id = <sample_id>-e<n>` numbered per product (particle-sim
turns the last token into the design version). `table` lists proposed and skipped rows
(`CANDIDATE_COLUMNS`). Decisions are always `designer_judgement`.

## `variant_comparison(ranked, bundles) -> DataFrame`

For every bundle with `parent_design_id`, the parent is the bundle whose `<product_id>-<design_version>`
equals it and whose `comparison_group_id` and `assigned_process_layer` match. Compares the maximum
area-normalized CA on sizes observed by both: `parent_ca_area, variant_ca_area, delta, ratio,
direction (decrease|increase|same)`; status `ok | no_parent_match | ambiguous_parent |
no_common_sizes`. No pass/fail judgement.
