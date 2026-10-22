# Phase B T3 report — stage 6 compute + stage-2 mask artefact (Sonnet 5.5 high, 2026-10-22, uncommitted on top of 7bcb07a + T1 + T2)

Risk tier HIGH. No commit, no bundle rebuild. Everything below was validated in the foreground.

## Items

1. **B5/B6 caches (local_delta.py, local_delta_report.py).**
   - `MaskCache` (local_delta.py): bounded LRU of unpacked group masks, created per `compute_local_delta` call and per page writer, never process-wide.
     Entries hold a strong reference to the `PackedMask` they came from, so an `id()` is never reused while its entry is alive (test with a weakref). The arrays
     are the read-only arrays `PackedMask.unpack` returns, so every number is bit-identical. Bound `UNPACK_CACHE_ENTRIES = 32` — see deviation 1.
   - `NamedCaseIndex` (case order preserved, same filters in the same order; `_named_case` is now the scan form of the same two helpers) and a per-pair record cache
     (`_Run.record`): a lazy `case.records[d]` is read once per pair and diameter instead of once per pair, size and diameter.
   - `_Assets` is lazy and bounded: the constructor validates the manifest and reads every layout once **in dataset order** (authentication pass, array dropped), so the
     first unreadable layout still stops the later cases and sets the same global reason as 7bcb07a; `layouts(case)` decodes again on demand with an 8-case LRU per side.
     `load_real_manifest` authentication is untouched (the counters below show the same 2 passes per side).
2. **B3 admission (real_hotspot.py, pipeline.py, local_delta.py, local_delta_report.py).** `estimate_stage_compare_windows` = MAX of the phases `areas` (both
   AreaResults or the artefact load + transient), `rows` (held + pairs + `ready × sizes × 32 × 32 KiB` + sensitivity entries + unpack cache + named-case records/index + one
   window's temporaries) and `report` (held + rows + page mask cache + 8 layout cases × 2 masks per side + pinned overview masks + one decode + refs + page text), with the
   LARGEST frame of both models. `pipeline.run_numeric_local_delta` now loads the correspondence, binds the pairs, validates sizes/jitter, probes the artefact (stat only,
   read only if its size is within the limit), THEN guards (`reuse=artefact usable`), then computes. The library guard (`compute_local_delta`, `phase='compute'`) and the page
   guard (`write_local_delta_html`, `phase='report'`) use the same function with their phase. Message: `oversized_real_operation: <stage>: estimated X bytes exceed limit Y
   (R ready and B blocked pairs x S window sizes x 32 diameters[, compare masks reused]); fewer pairs or window sizes ..., or raise max_operation_bytes ...`.
   `--compact-json` is not offered as a remedy.
3. **Q3 artefact (compare_masks.py new, pipeline.py).** `compare` (real path) streams `comparison_masks.zip` (stored zip, per group the union + 3 failure-piece packed
   masks, the AreaMetrics/FractionMetrics, `meta.json` last with input hashes, frame descriptors, sha256 per mask, schema + `AREA_ENGINE_TAG`) and publishes it with
   `comparison.json`/`comparison.csv` in the SAME Phase A transaction (`_NAMES` now has three names; the journal lists the names of that publication; the old two-name journal
   is still understood; an unknown name or non-bool value still fails closed). `comparison.json`/`.csv` bytes equal 7bcb07a (test + N=300 benchmark `cmp`). Stage 6 reuses the file
   only when schema, engine tag, both `input_hash`, `is_synthetic`, frame descriptor (incl. `valid_hash`), every mask length/sha256 and the zip CRC match and no
   `.comparison.publishing/.committed` exists; otherwise it silently recomputes `compute_areas` (older stage-2 outputs stay valid). If the artefact cannot be BUILT
   (serialisation error, not I/O) `compare` warns on stderr and publishes the pair without it. linepixel.py is untouched (serialisation lives in the new module).

## Acceptance

| criterion | result |
|---|---|
| T1 stage-6 equivalence (results + every written file) | green on 3.11 (tests/test_stage46_equivalence.py, ~2 min) and `/tmp/py38` (same file, 2:52). No intended deviation. |
| new tests (tests/test_stage6_scale.py, 96 tests) | green on 3.11 (~2:30) and 3.8 (~3:30 together with scale_guards/comparison_v2/local_delta) |
| guard boundary: exact estimate passes, one byte below refuses with zero expensive calls | 9 parametrised cases (3 scenarios × phase all/compute/report) + the CLI path (`compute_areas`, `PackedMask.unpack`, `read_binary`, `load_compare_masks` all forbidden; `_load_mask` is allowed there because authentication precedes the guard, B6) |
| measured peak ≤ estimate, three tracing sessions (py3.8 has no `reset_peak`): held, rows, report | 4 scenarios + a large asymmetric pair (240×300 vs 300×270): measured/estimate held 0.39–0.65, rows 0.30–0.50, report 0.72–0.80 |
| asymmetric frames; oversized P/sizes; eviction | covered (`distinct`, `multilayer`, large pair; 200 000 ready pairs × 5 sizes refused with the counts in the message; cache of 3 entries gives the frozen result and ≥3× more unpacks) |
| I/O counters bounded | below; `mask_unpacks`, `mask_unpack_hits`, `layout_auth_reads`, `layout_decodes` are new `SIMCOMPARE_IO_COUNTERS` keys |
| artefact: reuse == recompute | 4 scenarios, byte-for-byte on every output file (+ library-level `==` on rows/ranking/sensitivity/explained/products/provenance); `compute_areas` forbidden on the reuse run, counted twice on the recompute run |
| stale / corrupt → recompute | 13 edits of a valid artefact (engine tag, schema, either input hash, synthetic flag, frame valid hash/pitch, mask digest/shape, side missing, garbage meta, bit flip (CRC), member missing), 4 file damages, other model's artefact, 4 in-flight publication markers, over-budget size: all recomputed with identical output; reading never repairs or deletes |
| artefact inside the transaction | `test_every_step_of_the_three_file_publication_leaves_a_consistent_set`: fault (OSError / Ctrl-C / process death) at every filesystem step × restart recovery killed at every step × hardlink / copy fallback × with / without previous set; result is always (old, old, old) or (new, new, new), no leftovers; plus old-journal, unknown-name journal, abort, ENOSPC at artefact close, unbuildable artefact |
| full pytest 3.11 | green except `test_handoff.py` (ignored, sibling dirty) and `test_etx_examples.py::test_stage_runners_are_current_and_parse` (bundles embed old sources; T5 rebuilds) |

Existing tests changed: `tests/test_scale_guards.py` stage-6 tests (new message/remedy, the layout charge is bounded instead of per case, held formula). Nothing else edited.

## In-process benchmark (docs/plan/scale_stage46/T3_bench/, one fresh process per run, `compare-windows` CLI)

Data: N = 300 cases per model, 1262² (1,592,644 cells), ONE layer, all 32 diameters, 'grid' hotspots (25 blobs, 70 % present per case), distinct electrodes with real
layout PNGs (crop + 1000 border), stage-5-style correspondence (`gen.correspondence`, endpoints on both sides so the named-pair path runs, uniform repeats, every window
fully inside), `--window-sizes 10 20 30 --jitter-um 0.5 --sort-diameter-um 5`. Python 3.11, macOS arm64, 48 GB. RSS = `ru_maxrss` after the phase (monotone: later phases can
only show growth). `old` = frozen 7bcb07a. `json_csv` / `report` RSS is dominated by the stage-6 WRITERS (T4's task: `to_dict`, one indented `json.dumps` string, CSV dict rows).

| run | rows | wall | load+validate | compute_areas ×2 / artefact load | windows (rows) | write json/csv | report | RSS after load / compute / rows | peak RSS |
|---|---|---|---|---|---|---|---|---|---|
| P=50 old | 4,800 | 173.4 s | 21.6 s | 64.6 s | 61.2 s | 3.3 s | 12.0 s | 199 / 431 / n.a. | 2,687 MB (report) |
| P=50 new, recompute | 4,800 | 162.0 s | 21.6 s | 64.6 s | 48.1 s | 3.5 s | 13.4 s | 195 / 423 / 744 | 1,285 MB (json/csv) |
| P=50 new, artefact | 4,800 | 96.8 s | 21.5 s | 0.03 s | 47.8 s | 3.2 s | 13.4 s | 195 / – / 672 | 1,214 MB (json/csv) |
| P=200 old | 19,200 | 384.2 s | 21.6 s | 64.7 s | 259.7 s | 12.6 s | 14.7 s | 199 / 431 / 681 | 3,683 MB (report) |
| P=200 new, recompute | 19,200 | 337.5 s | 21.8 s | 64.8 s | 206.3 s | 12.5 s | 21.2 s | 197 / 432 / 924 | 3,078 MB (json/csv) |
| P=200 new, artefact | 19,200 | 273.6 s | 22.1 s | 0.03 s | 206.1 s | 13.3 s | 21.2 s | 194 / – / 840 | 2,999 MB (json/csv) |

- Output equality: every file of the P=50 and P=200 runs (local_delta.json/.csv, window_sensitivity.*, local_delta.html, _thumbs.json, 388 / 1,481 PNGs) is byte-identical
  between artefact reuse, recompute and the frozen 7bcb07a; the only difference old↔new is `provenance.config_path/config_sha256` (the benchmark used two config file names).
- The windows phase is 20–25 % faster (named records read once per pair + the unpack cache); it is dominated by `measure_union` on the window crops (~10 ms/row: `binary_mask`
  of the crop 2.2 s, `measure_union` 2.5 s, `crop_to_window`/`Frame.__post_init__`/Fractions the rest of a 10 s / 960-row profile), which is the numeric core and was not touched.
- The report phase is slower (P=50 12.0 → 13.4 s, P=200 14.7 → 21.2 s): layouts are decoded again on demand (588 / 2,358 extra decodes) because they are no longer kept; the
  old report phase added 1.65 GB (P=50) / 0.85 GB (P=200) of RSS because it kept every layout of both models — that is what the lazy `_Assets` removes.
- Artefact: 51.3 MB for 2 models × 32 groups × 4 masks (independent of N). `compare` with the artefact: 88.1 s / 378 MB vs frozen 86.9 s / 385 MB, `comparison.json`/`.csv` identical bytes.
  It saves exactly the two `compute_areas` runs (64.6 s here; ~2 × 587 s at N = 1,682 per the Phase A measurement) and the 19,200 `compute_areas` mask decodes.
- I/O counters (P=200): `sha256_files` 38,402 in all variants (authentication unchanged); `mask_decodes` old 96,000 = 38,400 authentication + 19,200 compute_areas + 38,400 named
  reloads; new recompute 70,400 (named reloads 12,800), new artefact 51,200. New: `mask_unpacks` 153,798 / `mask_unpack_hits` 248,394 (8 unpacks per row; the frozen code does
  one per use, up to 24 per row), `layout_auth_reads` 1,200, `layout_decodes` 2,358.
- Guard estimates for the same runs (`stage_compare_windows_estimate`): P=50 / artefact: rows 460 MB, report 478 MB; P=200 / artefact: rows 929 MB, report 1,060 MB; recompute adds the
  Phase A areas phase 6,302 MB (the `max_label_pattern_entries × 256 B` label budget). Observed RSS after the rows phase: 672 / 840 MB (RSS includes the ~190 MB interpreter + datasets).
  Production scale (N = 1,682, 1262² / 1500², P = 50 / 200 / 1000, artefact): peak 0.9 / 1.5 / 4.6 GB (1262²) and 1.0 / 1.6 / 4.7 GB (1500²); recompute path 7.5 / 7.8 GB (areas phase) at
  the default 8 GiB limit.

## Deviations and decisions for the coordinator

1. **Unpack cache bound is 32 entries, not the 256 of plan_v3 §2.3.** Measured: `PackedMask.unpack` costs 0.06 ms per 1262² mask, a window row ~10 ms. Sweep on 12 pairs × 3 sizes
   (rows, same machine): no cache 10.8 s, 8–64 entries 10.0 s, 256 entries 9.5 s; the 256-entry cache raised the rows-phase RSS by ~300 MB (P = 50: 967 vs 672 MB; P = 200: 1,131 vs 840 MB with the artefact,
   1,229 vs 924 MB recomputing) for ≈5 % time (P = 50: 45.4 vs 47.8 s; P = 200: 196.9 vs 206.1 s). 32 = both sides × 4 metrics × 4 diameters covers every use of a row (8 unpacks per row instead of up to 24). Two constants
   (`local_delta.UNPACK_CACHE_ENTRIES`, `real_hotspot.STAGE6_UNPACK_CACHE_ENTRIES`, checked equal by a test) change it back; the guard prices whichever is set.
2. **`_Assets` decodes each layout twice** (authentication pass in dataset order, then on demand). This is what keeps "first failure stops later layouts, same global reason" exact
   (including the T1 mutated-between-validation-and-use test) without keeping arrays. One documented difference: a layout damaged AFTER the authentication pass makes only that case
   unavailable with the real read error as the page reason (7bcb07a kept its construction-time copy); tested.
3. **Error precedence**: the guard now runs after `load_correspondence`/`bind_pairs`, so a malformed correspondence file is reported before an oversize error (B3 asks for this).
4. **Stage-6 `held` has its own per-record constants** (`STAGE6_DATASET_RECORD_BYTES` 5 KiB, `STAGE6_RESULT_RECORD_BYTES` 2 KiB, `STAGE6_FIXED_BYTES` 2 MiB per model). The Phase A
   `PAYLOAD_BYTES_PER_RECORD` (3 KiB for "dataset and result") understates what stage 6 holds: measured at N = 300 (tracemalloc, per model) dataset 41 MB = 4.4 KB/record,
   AreaResult 61 MB = 50 MB masks + output + 1.2 KB/record. Phase A's formula and stage 2's admission are unchanged.
5. **The artefact is looked up only in `cfg['output_dir']`** (the default `compare` output). A `compare --out X` run is not reused by a config whose `output_dir` differs; stage 6 then
   recomputes. No new config key or CLI option was added.
6. **Journal format**: a publication that includes the artefact writes a three-key journal; the Phase A code (two names) would call it `comparison_recovery_unreadable` (fail closed, nothing
   deleted). Only matters if someone downgrades the runner between a killed publication and the next run.
7. `local_delta.json` writer, CSV writer and the HTML string builder are untouched (T4). Stage-6 peak RSS in the benchmark is now set by those writers (1.2 GB at P = 50, 3.0 GB at
   P = 200), not by the compute; the guard's `rows`/`report` terms price the compute-side structures plus the page text only.

## Notes for T4 / T5

- T4 owns `write_local_delta` / the HTML writer and the stage-6 transaction in pipeline.py; the new `reuse=` flag of the page guard is derived from `result.areas[0].reused_from`.
  The compare artefact's names are in `_ComparisonStream._NAMES`; a stage-6 transaction should not touch them.
- T5: `compare_masks.py` is picked up by `build_single_file.py` (rglob over src/simcompare). Comments saying stage 6 is "not yet streaming" remain in `configs/etx/README.md` and
  `configs/etx/real.stage4.example.yaml`; `docs/usage.md` was updated for stages 2/6 (compare-windows "실자료 규모", admission, artefact, publication paragraph, ETX bullets).
- ETX: the stage-2 run already in progress needs no action; after the new runner exists a re-run of stage 2 produces the artefact, otherwise stage 6 recomputes (≈ 2 × the stage-2 compare time).
- New benchmark scripts: `T3_bench/bench_build.py` (builds the two models with the frozen package, ~2.5 min per model at N = 300) and `T3_bench/bench_run.py` (modes `compare`,
  `0` = recompute, `1` = artefact; `new`/`old`).

## Files

Production: `src/simcompare/local_delta.py` (compute paths only), `local_delta_report.py` (`_Assets`, thumbnails data, guard call), `pipeline.py` (stage-6 call site, `_ComparisonStream`
three-file transaction, `_compare_masks_for_stage6`), `adapters/real_hotspot.py` (stage-6 estimate/guard + constants), **new** `compare_masks.py`. Tests: **new** `tests/test_stage6_scale.py`
(96), `tests/test_scale_guards.py` (stage-6 tests updated). Docs: `docs/usage.md`. Bench: `docs/plan/scale_stage46/T3_bench/`.
