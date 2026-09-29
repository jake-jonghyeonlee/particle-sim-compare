# merge.py contract (`simcompare merge-exports --out DIR SRC...`)

`merge_exports(sources, out_dir) -> dict` combines two or more particle-sim `export-simcompare`
outputs (base + resimulated variants, or several products) into one data_dir.

- Accepted entries only: bundles/layouts/hotspots/ca_records/layer_stack CSVs, geometry.json,
  provenance.json, export_warnings.json, arrays/. Anything else is an error (Fab and
  design_review_status.csv are added to the merged dir afterwards).
- Array files are copied to `arrays/<nn>_<source dir name>/` and CSV `path` values rewritten.
- Errors: duplicate sample_id / geometry key / sample_id_mapping entry, differing CSV headers,
  conflicting layer_stack rows, differing provenance mapping_contract_version or is_synthetic,
  missing provenance in some sources, non-empty output.
- provenance: calc_settings keeps keys equal across sources plus `merged_sources` (each source's
  original settings); calc_version kept when equal, else null; array_storage keys rebased.
