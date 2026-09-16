# retrieval.py contract (similar-case search)

Imports: numpy, pandas, schema.NOT_APPLICABLE.
meta columns used: sample_id, product_id, product_family, fab_id, failure_type,
electrode_a_role, electrode_b_role, layer_a, layer_b, unit_width, unit_height
(unit_* may be NaN/None).

- `role_key(row) -> tuple`: (failure_type, *sorted roles excluding NOT_APPLICABLE)
  (so ("lateral_short","data","gate") equals the swapped pair).
- `layer_key(row) -> tuple`: sorted layers excluding NOT_APPLICABLE.
- `candidate_mask(meta, query_pos: int, *, layer_policy="exact",
  other_products_only=False, filters: dict | None = None,
  size_tolerance: float | None = None) -> np.ndarray[bool]`
  same role_key always required; layer_policy "exact" -> same layer_key, "ignore" ->
  no layer constraint, other -> ValueError; exclude the query row; other_products_only
  -> product_id differs; filters {column: allowed values list} (unknown column ->
  ValueError); size_tolerance t -> both rows need finite unit_width/unit_height and
  |log(w/wq)| <= log(1+t) and |log(h/hq)| <= log(1+t); unknown sizes excluded.
- `search(embeddings: np.ndarray, meta, query_pos: int, *, k: int, space: str,
  metric="euclidean", **constraints) -> pd.DataFrame`
  columns exactly: query_id, neighbor_id, rank, distance, space, neighbor_product_id,
  neighbor_fab_id, n_candidates. rank starts at 1, ascending distance, ties by
  sample_id. metric "cosine" = 1 - cosine similarity. Non-finite embedding row
  used (query or candidate) -> ValueError. len(embeddings) != len(meta) -> ValueError.
  No candidates -> empty DataFrame with those columns.
- `search_all(embeddings, meta, *, k, space, positions=None, **constraints) -> pd.DataFrame`
  concatenation over positions (default all rows), index reset.

`space` is a label such as "shape_only" or "with_risk". Neighbors found in a
"with_risk" space reuse CA/hotspot inputs and must not be reported as independent
findings (the label keeps them distinguishable).
