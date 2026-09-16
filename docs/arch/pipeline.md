# pipeline.py contract (command implementations; all outputs under cfg.output_dir)

Imports the modules by their public contracts. JSON written with indent=2,
ensure_ascii=False, NaN -> null. Every function returns dict {"outputs": [paths], ...}.

- `prepare(cfg) -> tuple[LoadedDataset, EncodedSet]`: load_dataset(data_dir, mode,
  hotspot_legend) + encode_bundles.
- `make_split(cfg, enc) -> tuple[np.ndarray, np.ndarray]`: group_split with
  cfg.split; assert_no_leakage.
- `run_validate(cfg)`: prepare; run_checks; write `validation.json`.
- `run_train(cfg)`: prepare; split; normalizers fit on train only; arrays;
  model_config_from_arrays(enabled=cfg.model.enabled_inputs ...); train_model;
  save `model.pt` (extra: seed, config_to_dict(cfg), train/test sample ids, loss
  kinds, torch version, device, is_synthetic, SHA-256 `training_fingerprint` over
  training input files and data/CAA/model/train/split settings; live-only
  design_review_status.csv excluded), `train_history.csv`, `split.json`
  (train/test sample ids and group labels), `run_record.json`.
- `compute_spaces(cfg, enc, train_pos, checkpoint) -> dict[str, np.ndarray]`:
  "shape_only": BaselinePCA on explicit_features(include_risk=False);
  "with_risk_baseline": BaselinePCA on include_risk=True; "with_risk_ae": AE z;
  "ae_struct" / "ae_fab" when present. Baseline fits use train rows only.
- `run_embed(cfg)`: needs model.pt (FileNotFoundError message tells to run train);
  rejects a missing/mismatched training fingerprint and requires retraining;
  writes `embeddings.npz` (space name -> array) and `embedding_meta.csv`.
- `run_analyze(cfg)`: checks the fingerprint; risk_table + comparison-group ranking
  + rank_agreement; neighbors
  via search_all for every space with cfg.analysis constraints -> `neighbors.csv`;
  clusters on "with_risk_ae" (train rows) -> `clusters.json`; loo stability using
  the shape_only baseline refit per held-out product -> `loo_stability.csv`;
  review cards including broad geometry candidates and external design-review status
  -> `review_cards.json`; `risk_table.csv`, `ranking.csv`,
  `rank_agreement.json`; `figures/rank_shift.svg`, `figures/ca_curves.svg`,
  `figures/loo_stability.svg` via `plots.write_analysis_figures(out, ranked, loo)` (dependency-free
  SVG, Korean text, per comparison group); `cluster_map.png` (Pillow scatter of the first two PCA
  components of with_risk_ae, colour by cluster, train filled / test outlined).
- `run_evaluate(cfg)`: checks the fingerprint; `evaluation.json` with: split summary
  + leakage check; held-out test products used only as shape-space queries against
  train candidates, with comparison-group CA/unit-area trend agreement (not accuracy);
  test-row per-modality reconstruction losses of the trained model;
  reconstruction error does not guarantee interpretation quality; ablation over
  {"image_only": [layout, hotspot], "numeric_only": [ca, fab, geometry], "full":
  enabled} trained with the same seed/epochs, reporting test losses and mean
  neighbor-set Jaccard vs shape_only baseline; leave-one-group-out for fab_id and
  product_family with confounding_report status ("not evaluable" when
  insufficient_groups, "effects not separable" when fully_confounded); synthetic flag
  and disclaimer that synthetic results are not research evidence.
