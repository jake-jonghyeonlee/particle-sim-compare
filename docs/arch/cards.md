# cards.py contract (review cards; evidence only, no automatic factor judgement)

`build_review_cards(ranked: pd.DataFrame, neighbors: pd.DataFrame, clusters: dict,
features: pd.DataFrame, *, min_products: int = 2) -> list[dict]`
- ranked: analysis.ranking_comparison output (has role_key, layer_key, product_id,
  sample_id, ca_max, ca_area_max, caa_actual, caa_reference, reasons, rank_* columns).
- neighbors: retrieval.search_all output for spaces "shape_only" and "with_risk".
- clusters: {"labels": {sample_id: int}, "representatives": {int: sample_id},
  "counter_examples": {int: sample_id | None}}.
- features: explicit shape features with a sample_id column.
One card per (failure_type, role_key, layer_key) group, sorted:
{"card_id": "card-<n>", "failure_type", "electrode_roles", "layers",
 "products": sorted ids, "n_comparable_products": int,
 "status": "comparable" if n >= min_products else "insufficient_products",
 "representative_cases": group sample_ids that are cluster representatives, else
   the top-2 by caa_actual (fallback ca_max),
 "counter_cases": the lowest-2 by the same value, plus counter_examples in group,
 "repeated_shape_features": up to 3 feature names with the lowest coefficient of
   variation among the group's upper-half-by-ca_max rows (descriptive only),
 "risk_comparison": per row {sample_id, product_id, ca_max, ca_area_max, caa_actual,
   caa_reference, rank_shift_fab},
 "size_correction": rows whose rank_shift_area != 0: {sample_id, rank_ca_max,
   rank_ca_area_max},
 "retrieval": {"shape_only": {query: [neighbors]}, "with_risk": {...},
   "note": "with_risk neighbors reuse CA/hotspot inputs; not independent findings"},
 "available_conditions": list of strings, "unavailable_conditions": strings for
   samples without physical scale, CAA reasons, single product / single fab,
 "expert_review_required": always includes "confirm electrode role / layer
   correspondence across products", "confirm CAA weighting rule and Fab units",
   plus "recompute CA at missing sizes: <ids>" when any ca@ column NaN,
 "disclaimer": "Evidence for expert review only. No causal importance, automatic
   factor ranking, or yield/defect-rate claim."}
NaN -> None in output (JSON safe).
