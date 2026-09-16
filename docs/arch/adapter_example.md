# adapters/example.py contract (example file adapter)

Replace this adapter to connect real data; downstream code only sees schema objects.
Imports: csv or pandas, json, numpy, PIL, pathlib, schema, preprocess.legend_to_values.

Directory format (paths inside CSV are relative to data_dir):
- `bundles.csv` (required). Required columns: sample_id, product_id, product_family,
  design_version, design_lineage_id, electrode_a_role, electrode_b_role, layer_a,
  layer_b, failure_type, fab_id. Optional columns: fab_distribution_id,
  assigned_process_layer, unit_width, unit_height, physical_length_unit,
  parent_design_id, change_description, calc_version.
- `layouts.csv` (optional): sample_id, slot ("a"|"b"), path, pixel_size, origin_x, origin_y
- `hotspots.csv` (optional): sample_id, particle_size, path, pixel_size, origin_x, origin_y
- `ca_records.csv` (optional): sample_id, particle_size, ca_value, ca_unit
  (one row per size; blank ca_value = not observed)
- `fab.json` (required): {"distributions": [{fab_distribution_id, fab_id,
  product_family, process_layer, bin_lower, bin_upper, raw_values (null = not
  observed), value_kind, value_unit, size_unit, surveyed_area, area_unit, period,
  is_reference}]}
- `geometry.json` (optional): {sample_id: {"thickness": {}, "shape_metrics": {}, "length_unit": str}}
- `provenance.json` (optional): {"calc_settings": {}, "calc_version": str, "is_synthetic": bool}

```python
@dataclass
class LoadedDataset:
    bundles: list[AnalysisBundle]
    distributions: dict[str, FabDistribution]
    warnings: list[str]

def load_dataset(data_dir, *, mode: str, hotspot_legend: dict | None = None) -> LoadedDataset
def read_image(path: Path, *, kind: str, legend: dict | None) -> tuple[np.ndarray, str, str | None]
```
read_image: ".npy" -> np.load(allow_pickle=False), must be 2-D; ".png" -> for kind
"hotspot" with legend: RGB -> legend_to_values, semantics "probability",
legend_id; otherwise grayscale "L"/255. Layout arrays -> float32 (>0.5) binary,
semantics "binary". Hotspot without legend -> "processed_intensity". Other suffix ->
DataContractError. Returns (float32 array, semantics, legend_id).

Rules (all violations raise DataContractError with sample ids in the message):
- blank CSV cells become None, never 0; numeric columns parsed with float().
- lateral_open: electrode_b_role/layer_b blank or "N/A" -> NOT_APPLICABLE; other
  value -> error. Short types: blank b fields -> error.
- missing bundles.csv/fab.json or required column; duplicate sample_id.
- rows in layouts/hotspots/ca_records/geometry referencing unknown sample_id.
- layout slot "b" for an open; duplicate slot; image file missing; absolute path
  or path resolving outside data_dir.
- CA grouping via schema.group_size_records (duplicate size, mixed units -> error).
- fab_distribution_id in bundles.csv not present in fab.json.
- every bundle: schema.validate_bundle(bundle, mode) (warnings collected);
  every distribution: schema.validate_fab.
- layouts sorted by slot (a then b); image role = electrode role of that slot;
  hotspots sorted by particle_size with role "hotspot".
- Provenance(adapter="example", source_files=[relative paths used], calc_settings,
  calc_version from bundle row or provenance.json, is_synthetic).
- missing optional inputs only add warnings (e.g. "S1: CA curve missing").
