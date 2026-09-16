# dataset.py contract (bundles -> masked arrays)

Imports: numpy, pandas, schema, preprocess (letterbox, align_curve, safe_log10,
Normalizer), caa (select_distribution, fab_shape_and_total, bin_midpoints, CAAError),
config.AppConfig.

```python
META_COLUMNS = ["sample_id", "product_id", "product_family", "design_version",
    "design_lineage_id", "electrode_a_role", "electrode_b_role", "layer_a", "layer_b",
    "failure_type", "fab_id", "fab_distribution_id", "assigned_process_layer",
    "unit_width", "unit_height", "physical_length_unit", "has_physical_scale", "is_open",
    "parent_design_id"]

@dataclass
class EncodedSet:
    meta: pd.DataFrame            # META_COLUMNS, one row per bundle, same order
    layout: np.ndarray            # (N, 2, H, W) float32, slot 0 = electrode a, slot 1 = b
    layout_valid: np.ndarray      # (N, 2, H, W) float32
    layout_present: np.ndarray    # (N, 2) float32
    hotspot: np.ndarray           # (N, K, H, W), K = len(cfg.data.hotspot_sizes)
    hotspot_valid: np.ndarray
    hotspot_present: np.ndarray   # (N, K)
    ca_log: np.ndarray            # (N, G) log10 raw CA, NaN when missing
    ca_mask: np.ndarray           # (N, G) bool
    ca_area_log: np.ndarray       # (N, G) log10 CA / unit area, NaN when missing
    ca_area_mask: np.ndarray      # (N, G) bool
    fab_shape: np.ndarray         # (N, B) B = len(cfg.data.fab_grid)
    fab_mask: np.ndarray          # (N, B) bool
    fab_total_log: np.ndarray     # (N, 1)
    fab_total_mask: np.ndarray    # (N, 1) bool
    geom: np.ndarray              # (N, D)
    geom_mask: np.ndarray         # (N, D) bool
    geom_names: list[str]
    notes: list[str]
```

- `encode_bundles(bundles, distributions, cfg) -> EncodedSet`
  H, W = cfg.data.image_size. Layout slot i = bundle.layouts[i] if exists (letterbox
  binary=True) else zeros/valid 0/present 0 (open slot b is absent by definition).
  Hotspot channel k = hotspot whose particle_size isclose(rtol=1e-6) to
  hotspot_sizes[k] (letterbox binary=False); absent otherwise.
  CA: align_curve(ca.particle_sizes, ca.values, ca.observed, cfg.data.ca_grid,
  cfg.data.ca_alignment) then safe_log10. Per area: values from bundle.ca_per_area();
  when None the whole row is masked and a note is added once per reason.
  Fab: select_distribution(bundle, distributions, cfg.caa.process_layer_overrides);
  CAAError -> row masked + note "<sample_id>: fab unavailable: <msg>". shape/total =
  fab_shape_and_total(dist); shape value at fab_grid[j] taken from the bin whose
  midpoint isclose(rtol=1e-6) and shape finite; total_log = log10(total) if total > 0.
  Geometry: geom_names = ["unit_width", "unit_height"] + sorted "thk:<k>" + sorted
  "shape:<k>" over all bundles; missing -> mask False, value NaN.
- `fit_normalizers(enc, train_pos) -> dict[str, Normalizer]` keys "ca", "fab_total",
  "geom", each Normalizer.fit on train rows only.
- `to_model_arrays(enc, norms) -> dict[str, np.ndarray]` float32 arrays with keys:
  layout, layout_valid, layout_present, hotspot, hotspot_valid, hotspot_present,
  ca (normalised ca_log), ca_mask, fab (fab_shape with NaN -> 0 where masked), fab_mask,
  fab_total (normalised), fab_total_mask, geom (normalised), geom_mask
  (masks as float32 0/1).
- `subset_arrays(arrays, pos) -> dict` slices every array by positions.
