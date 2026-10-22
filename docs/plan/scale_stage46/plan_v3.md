# Scale plan v3 — Phase B: stages 3–6 at real ETX scale with identical outputs (2026-10-22)

Planner: Claude Fable 5.1 / medium (read-only draft from P2_planning_brief.md). Base: 7bcb07a (feat/template-export).
Risk tier: HIGH for every Task (core calculations, resource guards, publish/recovery paths). Phase A (stage 2) is the
pattern: frozen-oracle equivalence, lazy masks, admission guards, publish-on-success writers (docs/plan/scale_compare/plan_v2.md).
Numbers below are DERIVED from code and the Phase A measurements (T5_benchmark.md), not measured for stages 3–6; T5 measures.
Scale unit: N = 1,682 cases/model, D = 32 diameters, cells = 1262² = 1.59 M (1500² = 2.25 M), one layer worst case, E = distinct
electrodes per layer ≤ 2N = 3,364, R = regions (unknown), P = confirmed pairs (unknown; see Q1).

## 1. Today, per stage

### Stage 3 — import-electrode-assets (adapters/electrode_png.py:330-512). No fix needed.
- Already one file at a time: `read()` :398-416 decodes one PNG, keeps only metadata in `decoded`; `_Writer.put` :223-236 writes
  one content-addressed asset. Peak RSS ≈ file bytes + 2262² × 21 B (RGBA worst, :271-277) + 3 × cells ≈ 0.15 GB; the dataset
  is lazy (Phase A) so `_dataset_bytes` :280 is only frame.valid. `--max-asset-memory-mb 16384` in the runner is far above need.
- Time (derived): per case 2 reads + sha + strict decode of 2262² + `_mask_hash` twice + PNG write at compress_level=9 (:232)
  ≈ 0.5–1 s → 15–30 min per model; CNT union once per model. Disk: ≤ 3,364 asset PNGs + review_assets.json (~5–10 MB) per model.
- Compress level is part of the asset bytes (sha256 recorded in the manifest, :234) → must not change. Only T5 measures it.

### Stage 4 — candidates (candidates.py:450 → electrode_map.py:358-541, roi_export.py). Fix needed: refused today, >16 GB if allowed.
- Lazy records are walked eagerly: `_validate_case` :306-318 iterates `case.records.items()` (LazyRecords.__getitem__ loads the
  .npz) → 53,824 decodes per side; `frames = [r.frame for ... c.records.values()]` :409 → another 53,824; the representative
  lookup :422 loads records until the diameter matches. ≈ 2 × 2.5 min per side, all for metadata.
- One full bool mask per distinct electrode (`touched`, :442-445): E × cells = 5.4 GB per side (benchmark data: all distinct);
  `observed` :446 keeps the loaded representative mask per case: N × cells = 2.7 GB; `np.sum(list(touched.values()))` :464
  stacks E masks into one (E, H, W) temp = another 5.4 GB. Both sides' maps are retained for export (`sides` dict, :451-467).
  Phase A guard_stage_map (real_hotspot.py:648-669) prices (N + 2E) × cells = 13.4 GB > 8 GiB default → `oversized_real_operation`
  now; raising the limit cannot fit two sides in 16 GB.
- Region phase: per region `names` ANDs all E masks :489 and links AND all N masks :495 → R × (E + N) × cells of traffic;
  `measure_union` per linked case recomputes `_cell_areas` (no `area_cache`) ≈ 30 ms each; `copy.deepcopy(case.raw/metadata)`
  per link (:495, ~0.2 MB each: `metadata.source_case_metadata` holds the 32 raw records, real_hotspot.py:1004-1007) and one
  full-frame `mask` per ElectrodeRegion (:509). Phase A's MAP_REGION_PAYLOAD_BYTES (16 KiB/region) does not price links.
- Export: `check_asset_manifest` (roi_export.py:58-130, `_asset` :45-54) decodes every layout/valid/cnt PNG of every case
  (4 × N) once per call; called 2× per side (region + index export) → ~27 k decodes ≈ 7 min, transient.
- Output size: each region-export row embeds `region.coverage` (roi_export.py:218-220) whose `observed` list has all N identities
  (≈ 0.6 MB per row at indent=2). Rows = Σ links; a central region linking all cases → ~1 GB review_rois.json per side, built
  as one `json.dumps` string (:313) and read back (candidates.py:241, `_persist_role_gate` :157-167 rewrites it). See Q2/R3.

### Stage 5 — confirm-pairs (confirm_pairs.py). No mask access; fix depends on Q2.
- `_load_datasets` :436-458 builds both lazy datasets; `_load_candidates` :90-137 parses candidate_queries + all four
  review_rois.json fully (`_rows_by_id` :140); `_check_chain` :460-493 hashes manifests and compares frame payloads. RAM ≈ 3–5×
  the export JSON size as Python objects (GB if stage-4 exports are GB-scale); otherwise seconds and < 1 GB.

### Stage 6 — compare-windows (+ HTML) (pipeline.py:954-983, local_delta.py, local_delta_report.py). Fix needed: refused today.
- Guard :967 with `asset_manifest` configured charges layouts 2 × N × (h+1000)(w+1000) = 17.2 GB (real_hotspot.py:745-756) →
  refused at any limit ≤ 16 GB. That is faithful: `_Assets` (local_delta_report.py:141-170) decodes and keeps 2 layouts per case
  for both sides. Note the charge uses the +1000 border, but assets are the exact crop (electrode_png.py:238-242, roi_export.py:49)
  → actual 2 × N × cells = 5.4 GB per side; still infeasible.
- compute_local_delta :361-429 recomputes `compute_areas` for BOTH models (summary, top-1) = two stage-2 compares
  (T7: 587 s / 3.9 GB each on py3.11, ~2× slower on py3.8) before any window work. Both AreaResults retained (:428, needed by HTML).
- Per row (P × sizes × 32): `measure_group_metric` :205-228 unpacks a FULL frame mask per metric (`union_mask` /
  `failure_piece_masks[name]`, linepixel.py:245-250 fresh unpack each access): window 8 + core 8 + jitter 8 (:484-500) = 24 full
  unpacks ≈ 25–40 ms; `_named_pair` :454-481 rescans all N cases (`_named_case` :261-279) and loads `case.records[diameter]`
  from disk per size (named-case reload, :462). P = 100 → 9,600 rows ≈ 6 min; P = 1,000 → 1 h; `explained_fraction` :587-670
  unpacks again per overlapping component.
- Writer :722-748: `result.to_dict()` copies all rows, one indented `json.dumps` string (~15–20 KB/row) plus `_csv_rows` (13 dict
  rows per row) → P = 1,000: ~2 GB JSON string + ~1 GB CSV dicts + ~1 GB row objects. Old outputs are deleted before writing
  (:731-734): a failure leaves nothing. HTML :281-398: 4 base64 thumbs per pair per size concatenated into one string.
- `report` (final_report.py:378-387) only parses the stage-6 files; it is not in the ETX JOBS (configs/etx/README.md) → out of scope.

## 2. Designs (byte-identical outputs)

### 2.1 Oracle strategy (T1)
- tests/reference_stage46/: frozen copies of 7bcb07a electrode_map.py, candidates.py, roi_export.py, local_delta.py,
  local_delta_report.py (imports rewired; a test hashes them against `git show 7bcb07a:<path>`, skipped without git).
- Randomized generators (seeded, in-memory real-style datasets with LazyRecords on tmp folders): 24–96 px frames, 5–60 cases,
  shared and distinct electrodes, 1–3 layers, partial `valid`, nested/identical/touching regions, comb regions, cases whose mask
  touches a region only outside valid, min_count hits exactly at threshold, two models with different frame sizes, blocked and
  ready pairs, windows flush with cell edges / clipped / outside the frame, jitter crossing an edge, core ≤ 0, named case
  missing/ambiguous, not_applicable vertical records, missing records, duplicate correspondence positions.
- Stage 4 equality: ElectrodeMaps vs frozen (asdict of every field incl. region_id, links' Measurements, coverage, provenance;
  masks via array_equal) and the whole candidates output tree byte-for-byte (same checkout → same producer_commit).
- Stage 6 equality: LocalDeltaResult rows/ranking/sensitivity/explained equal with `==` on floats (same code path on the same
  crops → bit-identical), then every written file (local_delta.json/.csv, window_sensitivity.*, .html, _thumbs.json, PNGs)
  byte-identical to the frozen writers on the same result. Run on py3.11 and /tmp/py38 (plan_v2 B6).

### 2.2 Stage 4 (T2) — electrode_map.py, candidates.py, roi_export.py, real_hotspot.py guards
- Header-only metadata: `_validate_case`, `frames` and the representative lookup use `records.header(d)` (real_hotspot.py:782)
  when records is a LazyRecords; plain dicts unchanged. Only the representative record is loaded (once per case). Note: a corrupt
  non-representative .npz is no longer detected here (it was validated at stage-1 import); documented, test added.
- Electrode accumulation without E masks: per case load the representative mask once, compute `active`, store PACKED `active`
  (cells/8) + packed original mask + `outside_valid` count; `counts` (int64, kept) is accumulated per ELECTRODE as Σ over
  electrodes of the OR of its cases' actives — same values as :464 (count of distinct electrodes touching a cell), computed one
  electrode at a time (one full bool temp). Packed cache per side ≈ N × 2 × cells/8 ≈ 0.67 GB (1262²) / 0.95 GB (1500²).
- Regions: `inside ⊆ active ⊆ valid`, so a case links iff its active ∩ inside ≠ ∅ (identical to `record.mask & inside` :495),
  and `names` = electrodes of linked cases (identical to :489 since touched[e] = OR of its cases' actives). Bbox prefilter on
  the packed actives, then unpack only candidates. `measure_union` keeps receiving the FULL-frame original mask (unpacked from the
  packed copy — same array, same gather order; cropping would change pairwise-summation blocks) with one `area_cache` dict per
  region (measurement.py:135-141; A3 showed identical values). `ElectrodeRegion.mask` becomes a property unpacking a packed field
  (array_equal; verify report.py/heatmap.py consumers — grep found none). CaseLink raw/metadata: one deepcopy per case shared by
  all its links (nothing mutates them; outputs identical); `np.argwhere(inside).tolist()` for the stable id stays as is.
- candidates.py: per side compute → region export → index export → drop maps (keep RegionInfo/rows); file contents do not depend
  on the write order. Asset validation memo: `check_asset_manifest` decodes each (path, sha256) once per process.
  review_rois.json is written through `JSONEncoder(indent=2, ensure_ascii=False, allow_nan=False).iterencode` to a temp file and
  renamed (byte-identical to dumps; test asserts) — the dict is small because rows share `region.coverage`; `_persist_role_gate`
  uses the same writer. New-output-dir cleanup on failure stays (candidates.py:403-442).
- Guards: re-derive `guard_stage_map` / `_stage_map_live` / counting / regions (real_hotspot.py:648-727) for the new peaks
  (packed cache + per-case shared payload + one full temp + counts + region packed masks + links), both sides counted, with
  tracemalloc boundary tests in the RA4 style (succeeds at the estimate, fails one byte below; dense, comb, many-electrode cases).
  Error code/remedy text unchanged except dropping "streaming support … pending".
- Target: both sides ≤ 3 GB RSS at N=1,682 (1500²), stage-4 wall ≤ 30 min/side (report actual).

### 2.3 Stage 6 compute (T3) — local_delta.py, local_delta_report.py, real_hotspot.py guard
- Unpacked-mask LRU keyed by (id(group), metric): bounded to 2 sides × 4 metrics × 32 diameters entries (≤ 0.6 GB at 1500²);
  `measure_group_metric`, `explained_fraction` and the HTML `_side_thumbnails` take masks from it. Same PackedMask → identical
  arrays; crop/measure code untouched → bit-identical numerics. Row order, loop order and reasons unchanged.
- Named case: index dataset.cases once by (product, design, frame, layer, frozenset(endpoints)) preserving case order; the
  remaining filters run in the original order so `matches` is identical. Per-pair cache of the named case's loaded records
  (≤ 2 × 32 masks), dropped when the pair changes.
- `_Assets`: keep refs only; decode layouts on demand with a small LRU (e.g. 8 cases); overview keeps the first pair's two arrays
  per (side, frame) only. Thumbs/overview PNG bytes identical (same arrays, same `_render`).
- Guard: layouts term → cache bound × asset cells (asset shape = frame shape) + one decode transient; add a rows term
  P_ready × |sizes| × 32 × ROW_BYTES (measured in T3 with tracemalloc, ~10 KB) + writer transient; reuse
  `oversized_real_operation` with the stage name, the P/sizes behind the estimate and the remedy "fewer pairs/sizes, --compact-json".
- compute_areas for both models stays (identical to today); see Q3 for the optional stage-2 mask artefact.
- Target: P = 200, 3 sizes, jitter: RSS ≤ 2 × compare retained + 1 GB; wall ≤ 2 × compare + 15 min (report actual).

### 2.4 Stage 6 writers (T4) — local_delta.py:700-748, local_delta_report.py:281-398
- local_delta.json / window_sensitivity.json: chunked `iterencode` (indent=2) to temp files; rows converted with `json_value` one
  at a time through a list proxy (`__len__`/`__iter__`) so no second copy of all rows exists; CSV rows generated per row and
  written as they are produced (all rows share the fixed key set, so the header is known from the first row; empty-table header
  fallback :711 preserved). Byte-equality tests vs `json.dumps`/today's writer on randomized results.
- HTML: write sections to the temp file progressively instead of one growing string; thumbs/overview PNG files are written as
  today (sha-named). Publish-on-success for the set: everything is written into `<out>/.local_delta.tmp-<pid>/`, then the old
  `local_delta*` / `window_sensitivity*` files are removed and the new ones renamed in; on any failure the temp dir is removed and
  the old outputs stay. The non-atomic rename window is documented like docs/usage.md:330. `--compact-json` behaviour unchanged.

## 3. Tasks (serial, one Sonnet 5.5 medium editor; each leaves changes uncommitted; Codex Astra review after T4 and T5)
- T1 oracle (HIGH). Target: tests/reference_stage46/*, tests/test_stage46_equivalence.py, generators in tests/ (no production
  change). Constraints: frozen files must hash-match 7bcb07a; py3.8 + numpy 1.21.6 run. Acceptance: equivalence suites pass against
  current HEAD (trivially) on both runtimes; coverage of every §2.1 case; run time of the suite ≤ 3 min.
- T2 stage 4 (HIGH). Target: electrode_map.py, candidates.py, roi_export.py (writer + memo only), real_hotspot.py (stage-4 guards),
  tests/test_scale_guards.py stage-4 tests, tests/test_electrode_map.py, tests/test_candidates.py. Constraints: no change to
  measurement.py/linepixel.py numerics; ElectrodeMaps/ElectrodeRegion/CaseLink public fields keep names and values; electrode-map
  CLI outputs (json/csv/npz/png) identical. Acceptance: T1 stage-4 suite green; guard boundary tests; decode counter shows one mask
  load per case (SIMCOMPARE_IO_COUNTERS); in-process micro-benchmark N=300/1262² RSS ≤ 1 GB per side; full pytest green.
- T3 stage 6 compute (HIGH). Target: local_delta.py, local_delta_report.py (`_Assets`, thumbnails), real_hotspot.py
  (`guard_stage_compare_windows`), tests. Constraints: rows/ranking/sensitivity/explained identical; no writer change.
  Acceptance: T1 stage-6 result suite green on both runtimes; unpack/decode counters bounded (test); guard tests incl. rows term.
- T4 stage 6 writers (HIGH). Target: local_delta.py:700-748, local_delta_report.py:281-398, docs/usage.md compare-windows section.
  Acceptance: byte-identical files vs frozen writers (T1); recovery tests (exception mid-HTML, ENOSPC, interrupt: old outputs kept,
  temp dir removed); py3.8 run of the writer tests.
- T5 benchmark + runners + docs (HIGH: ships the runners). Target: scripts/benchmark_scale_compare.py, configs/etx/*.yaml comments,
  configs/etx/README.md, docs/usage.md ETX section, bundles rebuilt via scripts/build_etx_stage_runners.py --overwrite,
  docs/plan/scale_stage46/README.md checkpoint. Acceptance: §4 runs recorded with actuals; N=50 HEAD-vs-candidate full-pipeline
  output-tree diff empty (excluding timestamps in confirm-pairs); py3.8 isolated run of the stage 3–6 tests listed by file.

## 4. Real-scale benchmark (T5): `benchmark_scale_compare.py --stages 1-6`
- Data: today's generator plus electrode PNGs per case (crop+1000, mode L, one distinct rectangle per electrode id so layouts
  differ; `--shared-electrodes 0.3` reuses ids across cases so regions reach min_count 3 with several electrodes), a DL1 folder
  with one CNT PNG per model (`_scan_dl1_model` naming, electrode_png.py:164-191), `--size-target` for a different target frame,
  assignment_<side>.json + stage-4 real.yaml with computed sha256s, synthetic DL2 match JSON for both directions built from the
  exports (format of confirm_pairs._load_match / tests/test_confirm_pairs.py) and decisions.csv accepting `--pairs` candidates,
  stage 6 with `--window-sizes 10 20 30 --jitter-um 0.5 --sort-diameter-um 5` and a second run with `--compact-json`.
- Each command isolated under /usr/bin/time with io counters; report wall, RSS, exit, error code, output bytes, #regions,
  #rows, review_rois.json size. Targets (report actual, never promise): stage 3 ≤ 1 GB, ≤ 30 min/model; stage 4 (both sides)
  ≤ 3 GB at N=1,682/1500², ≤ 60 min; stage 5 ≤ 2 GB; stage 6 P=200 ≤ 6 GB, ≤ 2 × compare + 15 min; 1262² and 1500²; py3.8 timing
  for the N=300 point. Deviations from targets go to the user with the measured value, not a silent re-target.

## 5. Stage 1/2 outputs, config and runner changes
- Stage-1 imports and the stage-2 run in progress on ETX stay valid: Phase B touches no stage-1/2 code path (linepixel.py,
  measurement.py, pipeline compare/validate, `estimate_area_bytes`, `resource_estimate` provenance unchanged → comparison.json
  identical). Stage-3 outputs produced with 7bcb07a stay valid (stage 3 unchanged).
- Users copy the six rebuilt runners (bundle code is shared; stages 1/2 behave the same). No new config keys; `max_operation_bytes`
  should no longer need raising for stages 4/6 at the default 8 GiB (verified by T5). Comments saying stages 4/6 are "not yet
  streaming" (real.stage4.example.yaml:40-43, README) are updated. `--max-asset-memory-mb` stays required.

## 6. Risks, open questions, out of scope
- R1 Numeric identity hinges on feeding measure_union full-frame arrays and never cropping before summation; the oracle must keep
  `==` on floats (no tolerance). R2 numpy-version float drift (T5 note: last-digit CSV differences across numpy) is pre-existing.
- R3 Stage-4/5 output size: review_rois.json duplicates coverage per row; streaming removes the RAM spike but not the file size,
  and stage 5 still parses it (3–5× in RAM). R4 P unknown: stage-6 rows/HTML scale linearly with P × sizes; a 300 MB HTML page
  is not openable even if written. R5 Guard re-derivation errors: boundary tests with tracemalloc on both runtimes mitigate.
- Q1 Expected number of confirmed pairs P and whether all three window sizes, jitter and the named-pair metric are needed on
  real data (each removal cuts stage-6 time ~linearly; `--compact-json` drops rows from the JSON only).
- Q2 May review_rois.json rows reference the region coverage once (schema change, not byte-identical) if stage 5 cannot parse
  the real-scale export? Default: keep identical bytes.
- Q3 Optional new artefact: stage 2 persists packed masks/AreaResults so stage 6 skips the two recomputes (~2 × compare time);
  new output, separate Task after Phase B if wanted.
- Out of scope: `report` on real data, electrode-map CLI timing (same engine; gains follow), MMO batches, changing PNG compression,
  multi-layer measurements beyond the benchmark's one-layer worst case, numpy < 1.21.6 certification.

## 7. User decisions (2026-10-22, coordinator)
- Q1: expected confirmed pairs P ≈ tens to 200 per model → stage-6 benchmark target P=200 (also report P=50); HTML at P=200 must stay openable.
- Q2: review_rois.json may switch to a reference format (coverage written once, rows reference it; content-identical, not byte-identical)
  IF the T5/T2 measurement shows stage 5 or the export is too large. Then version the schema and update every reader (confirm_pairs,
  DL2 handoff docs/tests) in the same Task; keep the old reader for old files.
- Q3: INCLUDE a stage-2 artefact: compare additionally writes packed per-group masks needed by stage 6 (content-addressed, with the
  comparison input hash); stage 6 reuses it when present and hash-matching, and FALLS BACK to recomputing compute_areas when absent or
  stale (so the stage-2 output the user is producing now on ETX stays usable). Numeric identity between reuse and recompute is an
  acceptance test. Publish-on-success for the artefact together with comparison.json/csv.
