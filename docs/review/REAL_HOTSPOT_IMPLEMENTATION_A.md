# Real hotspot implementation A — evidence

Date: 2026-10-15. Baseline: `rewrite/line-pixel-rate@40a4d6d3982de2d0beb0939909418eb25f2c2379`.

## Implemented

- Added `real_hotspot_v1` import and version/hash validation. The CLI discovers case folders beneath the supplied root, parses the five case-name fields, reads only each case's `result/<defect>.csv`, and records missing diameters, CSV mismatches, unexpected diameters, absent expected folders, duplicate/nested case folders, and same-name endpoints.
- Hotspot decoding uses the existing strict PNG structure, CRC, inflate, orientation, and pixel-limit checks, hashes the validated byte buffer, then accepts grayscale PNG and RGB only when its channels are identical. Any nonzero gray value is occupied. Black is observed area zero; missing files retain null area. The existing numeric_v2 binary decoder is unchanged.
- The importer records the centered crop frame at `(50, 50)` µm with `0.1` µm pitch, checks the per-axis 1000 px size difference and shared crop shape, writes occupancy assets sequentially, and hashes source and derived files. CSV strings, their rounding precision, pixel area, difference, and tolerance remain separate.
- Real `validate-data`, `compare`, and `electrode-map` paths consume the versioned manifest. Without explicit condition/layer assignments, inspection and raw area remain available while grouped/classified output is unavailable. With explicit assignments, masks use the existing line/pixel union, coverage, failure-piece, and map calculations. Missing pair classification remains incomplete; density/unit rates and deltas remain unavailable. ROI export is blocked while electrode polarity/layout semantics are unknown.
- Configured aggregation enforces `max_group_mask_cells` (default 500,000,000) and `max_materialized_source_cells` (default 6,000,000,000); reference and target masks are materialized sequentially. Import enforces a per-PNG `max_input_pixels` limit (default 100,000,000). These are resource guards, not a representative 16 GB workload benchmark.

## Verification

Command:

```text
.venv/bin/python -m pytest tests/test_real_hotspot.py tests/test_numeric_images.py tests/test_numeric_inputs.py tests/test_numeric_cli.py tests/test_linepixel.py tests/test_electrode_map.py tests/test_measurement.py tests/test_numeric_schema.py -q
```

Result: passed. Coverage includes CLI import/validate/compare/map, `is_synthetic=false`, null physical metadata, 1270 px crop corner and non-square geometry, equal-channel RGB grayscale acceptance, CRC/color/alpha rejection, case-local CSV, leading-zero duplicate suffix, CSV rounding through measurement, observed black versus missing, vertical zero with unknown height, unclassified pair output, same-name audit retention, resource guard rejection, and existing numeric_v2 image/schema/measurement/linepixel/map regressions.

`git diff --check` passed. A system `python3` was 3.14 without pytest; validation used the repository `.venv` Python 3.11 environment.

## Remaining acceptance work

- No representative real-data RSS/time run or Windows Python 3.12 offline installation was performed; those belong to the next packaging task or actual user data run.
- The physical layer/process, product/source/exposure/unit assignments and pair table must be provided in `condition_assignment` to enable grouping. Unknown values remain null. Electrode image polarity/layout mapping remains unconfirmed, so ROI output is blocked.
- The importer cannot prove missing case folders without `--expected-cases`; discovered cases and per-diameter gaps remain individually auditable.
- No independent implementation review PASS is claimed.
