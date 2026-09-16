# baseline.py contract (explicit features + standardisation + PCA)

Imports: numpy, sklearn.decomposition.PCA, preprocess.Normalizer, dataset.EncodedSet.

- `explicit_features(enc, *, include_risk: bool) -> tuple[np.ndarray, np.ndarray, list[str]]`
  returns (X (N,F) float64 with NaN for missing, mask (N,F) bool, names).
  Shape features (always), for layout slots s in (0, 1), only where present:
  `layout{s}_fill` = mean of layout over valid pixels; `layout{s}_edge` = fraction of
  valid horizontally/vertically adjacent pixel pairs (both valid) with different
  values; `layout{s}_aspect` = foreground bbox height/width (masked if no foreground).
  Then geometry columns `geom:<name>` from enc.geom/geom_mask.
  Risk features (include_risk): per hotspot channel k where present: `hot{k}_mean`,
  `hot{k}_max`, `hot{k}_frac50` (fraction of valid pixels > 0.5); `ca_log:{j}` from
  enc.ca_log/ca_mask; `ca_per_area_log:{j}` from enc.ca_area_log/ca_area_mask;
  `fab_shape:{j}`, `fab_total_log`.
- `class BaselinePCA`: `__init__(self, n_components=8)`;
  `fit(self, X, mask, train_pos) -> self`: Normalizer.fit on train rows; standardised
  matrix with masked entries = 0 (train mean); append missing-indicator columns
  (1.0 where masked) for features that are missing in any train row; features with no
  observed train value are dropped; PCA(n_components=min(n_components, n_train-1,
  n_features), random_state=0) fit on train rows only (if that minimum < 1 -> ValueError).
  `transform(self, X, mask) -> np.ndarray` (N, n_components_).
  `explained_variance_ratio_` property passthrough.
Missing values are imputed only inside this baseline and the imputation is reported
by the indicator columns; the raw feature table keeps NaN.
