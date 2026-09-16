# checks.py contract (validate-data report)

Imports: numpy, schema, split (group_ids, confounding_report), caa, dataset.EncodedSet,
adapters.example.LoadedDataset.

`run_checks(loaded, enc, cfg) -> dict` (JSON-serialisable; numpy -> python types):
- "counts": n_bundles, n_products, n_lineages, n_families, n_fabs, by_failure_type.
- "missing_inputs": {"layout_a": n, "layout_b_short": n (shorts lacking slot b),
  "open_b_not_applicable": n (opens; explicitly not missing), "hotspot@<size>": n,
  "ca@<g>": n, "fab": n, "geometry": n}.
- "physical_scale": {"missing_sample_ids": [...], "message": "physical length
  comparison impossible for these samples; only pixel-relative comparison"}.
- "hotspot_layout_overlap": list of {"sample_id", "corr"} where corr = Pearson
  correlation over valid pixels between mean of present hotspot channels and the max
  of present layout slots (skip if either absent or zero variance); "flagged" =
  corr > 0.8; plus "message": processed hotspot images can duplicate layout content,
  so image features may double count geometry.
- "confounding": confounding_report(enc.meta, "fab_id", "product_family").
- "independent_groups": number of unique group_ids(enc.meta); "split_feasible": >= 2.
- "caa_applicability": list of {"sample_id", "reason"} where select_distribution /
  interpret_fab fails (reason = error message).
- "grid_coverage": {"ca@<g>": fraction observed}.
- "synthetic": True if any bundle.provenance.is_synthetic.
- "warnings": loaded.warnings + enc.notes.
