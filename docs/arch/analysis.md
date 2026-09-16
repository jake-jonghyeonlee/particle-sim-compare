# analysis.py contract (comparison, ranking, clustering, stability)

Imports: numpy, pandas, sklearn.cluster.KMeans, schema, caa, preprocess.align_curve,
retrieval (role_key, layer_key, search_all).

- `cluster_embeddings(emb, train_pos, n_clusters, seed) -> dict`: KMeans(n_clusters =
  min(n_clusters, len(train_pos)), n_init=10, random_state=seed) fit on train rows
  only; labels = predict on all rows. representatives: {cluster: row position (any
  row) closest to that centroid}; counter_examples: {cluster: train row assigned to a
  DIFFERENT cluster that is closest to this centroid, or None}. Also "note":
  "cluster ids are arbitrary labels without physical meaning".
- `risk_table(bundles, distributions, cfg) -> pd.DataFrame` one row per bundle:
  sample_id, product_id, product_family, fab_id, failure_type, role_key (str
  "|".join), layer_key (str), has_physical_scale; for each ca_grid value g:
  `ca@{g}` and `ca_area@{g}` (NaN if missing, via align_curve with
  cfg.data.ca_alignment on ca values and on ca_per_area values); `ca_max`,
  `ca_area_max` (nanmax over observed, NaN if none); `caa_actual`,
  `caa_actual_reason`, `caa_reference`, `caa_reference_reason`, `caa_is_demo`.
  CAA actual: select_distribution -> interpret_fab(dist, cfg.caa.rule, mode=cfg.mode,
  confirmed=cfg.caa.confirmed) -> compute_caa(require_full_coverage from cfg). Reference:
  distributions[cfg.caa.reference_distribution_id] (missing id -> reason). Any CAAError
  or missing CA -> value NaN and reason text; never 0.
- `ranking_comparison(table) -> pd.DataFrame`: copy of table plus, within each
  failure_type group, rank columns (1 = highest, NaN values unranked, method "min"):
  rank_ca_max, rank_ca_area_max, rank_caa_actual, rank_caa_reference and
  rank_shift_fab = rank_caa_reference - rank_caa_actual, rank_shift_area =
  rank_ca_area_max - rank_ca_max.
- `rank_agreement(ranked) -> list[dict]`: per failure_type {"failure_type", "n",
  "spearman_actual_vs_reference", "spearman_raw_vs_area"} using rows where both
  values are finite; n < 3 -> None value.
- `neighbor_sets(neighbors: pd.DataFrame) -> dict[str, set[str]]` query_id -> ids.
- `jaccard(a: set, b: set) -> float | None` (None if both empty).
- `leave_one_product_out(ranked, meta, embed_fn, *, k, top_k, search_kwargs) -> pd.DataFrame`
  for each product p (sorted): keep = rows with product_id != p.
  top items: per failure_type the sample_ids of top_k rows by rank_caa_actual among
  keep in the full ranking vs recomputed ranking of keep-only rows -> mean Jaccard.
  retrieval: emb_full = embed_fn(all train positions), emb_loo = embed_fn(positions
  of keep); search_all over keep positions only with both embeddings (candidates
  never include p in both cases: pass filters excluding p via a meta copy) -> mean
  Jaccard of neighbor sets. Row: held_out_product, top_items_jaccard,
  neighbor_jaccard, n_queries. embed_fn(train_pos) -> (N, D) embeddings for all rows.
