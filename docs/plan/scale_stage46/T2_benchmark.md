# Phase B T2 — stage-4 benchmark (in-process, `candidates` command, 2026-10-22)

Scripts: `docs/plan/scale_stage46/T2_bench/` (`bench_build.py` builds the data with the frozen package, `bench_run.py` runs one
`candidates` in a fresh process for `new` = production or `old` = frozen 7bcb07a, `bench_trace.py` traces one side's map under
tracemalloc against the guard estimates, `parse_mult.py` measures the stage-5 parse multiplier). Python 3.11, macOS arm64, numpy 2.x.

Data: N = 300 cases, frame 1262 x 1262 (1,592,644 cells), ONE layer, 4 diameters per case kept (1, 5, 10, 30 µm; the representative is 5 µm),
real electrode layouts (crop + 1000 border PNGs), 600 distinct or 40 shared electrodes, `--min-count 3`, window 20 µm. Reference and target
are the same data directory (a product-id difference would change the dataset hash), so the two sides cost the same. Profiles:
`centered` = every case has the same central blob (one region linking all 300 cases: high link volume per region);
`grid` = blobs on a 5 x 5 grid, each present in 70 % of the cases (25 regions, 5,269 links = 5,269 query rows per side).

| profile | exit | wall new / old | peak RSS new / old | query export new / old (per side) | rows | regions |
|---|---|---|---|---|---|---|
| centered-distinct | 0 | 13.8 s / 38.5 s | 264 MB / 3,843 MB | 1.50 MB / 37.05 MB | 300 | 1 |
| centered-shared | 0 | 11.1 s / 37.3 s | 322 MB / 1,200 MB | 1.49 MB / 36.87 MB | 300 | 1 |
| grid-distinct | 0 | 36.6 s / 93.5 s | 327 MB / 7,760 MB | 24.23 MB / 650.43 MB | 5,269 | 25 |
| grid-shared | 0 | 33.9 s / 91.0 s | 341 MB / 7,228 MB | 24.19 MB / 647.23 MB | 5,269 | 25 |

(The index exports are manual rows without region provenance: 1.04 MB per side in both versions.) Peak RSS is `ru_maxrss` of the whole
process, i.e. both sides of the run (the second side adds only 40-70 MB over the first one). Per phase and side (new / old): map
`_prepare` 3.2-3.6 s / 9.9 s (centered), 14 s / 28 s (grid); query export 0.6-3.4 s / 4.4-13 s; index export 0.9 s / 4.1-4.5 s.
Funnel, queries and the expanded exports are equal to the old ones (centered profiles: `expand_region_coverages(new)` equals the 7bcb07a
bytes exactly; `candidate_queries_v1.json` equal except the recorded export hashes).

I/O counters (`SIMCOMPARE_IO_COUNTERS` mechanism, new counters `map_loads`, `asset_decodes`, `asset_memo_hits`):

| | new | old |
|---|---|---|
| `sha256_files` / `sha256_bytes` | 4,802 / 8.7-9.8 MB | 4,802 / same |
| `mask_decodes` | 5,400 = 4,800 authentication (4 `load_real_manifest` passes x 1,200 records) + 600 map loads (2 sides x 300 cases, ONE per case) | 10,800 = 4,800 authentication + 6,000 map loads (10 per case here: 4 in `_validate_case`, 4 for the frame list, 2 in the representative lookup; with 32 diameters ~69 per case) |
| asset PNG decodes | 602 (distinct) / 42 (shared) + 4,198 / 4,758 memo hits (each hit re-hashes the file) | every asset on every check (4 checks: 4,800 decodes) |

The authentication passes are unchanged (the plan's B6): `_real_source` still authenticates and decodes every observed mask on every pass.

## Guard estimates against the traced peak (one side, `bench_trace.py`, tracemalloc)

| profile | traced peak | admission | count prepass | regions | links (largest) | retained after the map |
|---|---|---|---|---|---|---|
| centered-distinct | 110.0 MB | 130.4 MB | 110.5 MB | 170.0 MB | 170.9 MB | 39.2 MB |
| grid-shared | 120.8 MB | 130.4 MB | 110.3 MB | 174.6 MB | 190.1 MB | 45.8 MB |

The estimate is 1.55-1.57x the traced peak; at N = 1,682 the admission figure is 0.39 GiB (1262²) / 0.54 GiB (1500²) (cache 1,682 x (cells/8 + 2 KB) + counts +
scan temporaries), far below the 8 GiB default. Real-scale peaks are dominated by `links x 3 KiB` + `rows x 12 KiB`, which is what the new
`stage-4 links` / `stage-4 export` guards price before the rows exist (5,269 rows here cost 24 MB on disk).

## Stage-5 parse multiplier (retained Python objects per file byte, `json.loads` of the written exports)

3.11: 2.07x (24 MB export), 2.24x (index export), 1.94x (old 37 MB export); 3.8: 2.35x, 2.52x, 2.17x. Peak with the decoded text 3.1-3.5x.
`STAGE5_PARSE_MULTIPLIER = 5` (2x margin over the worst measured value), `STAGE5_READ_TRANSIENT = 3`. A test asserts `retained <= 5 x size` on both
Python versions for the generated exports.
