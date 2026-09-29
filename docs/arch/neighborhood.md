# neighborhood.py contract (comparison inside the similar-product neighbourhood)

Risk metric: area-normalized CA (`ca_area@<size>` columns of `ranking_comparison`). Members
without any area CA (no physical scale) are listed but excluded from the common sizes. Within a
neighbourhood (query + neighbours of one `space`), only sizes observed by every remaining member
are compared, and each member's risk is the maximum over those sizes (no filling/extrapolation).

- `neighborhood_comparison(ranked, neighbors, *, space)` -> one row per query of that space:
  `query_id, product_id, comparison_group_id, failure_type, space, reuses_risk_inputs,
  neighbor_ids (';'), n_neighborhood, query_risk, neighbor_risk_median, risk_ratio_to_median,
  query_rank (1 = riskiest), compared_sizes (';'), reference_id (lowest-risk neighbour; ties by
  distance then id), reference_product_id, reference_risk, reference_distance, status`.
  status: `ok | no_neighbors | query_risk_missing | no_common_sizes | no_reference |
  query_is_lowest`.
- `geometry_deltas(comparison, features)` -> for `status == "ok"` rows and the failure type's
  `RELEVANT_FEATURES`: `query_id, reference_id, comparison_group_id, failure_type, feature,
  query_value, reference_value, delta (= reference − query), review_direction (increase |
  decrease)`; equal or missing values produce no row.

No pass/fail thresholds: other-failure tolerance and design rules are the designer's decision.
