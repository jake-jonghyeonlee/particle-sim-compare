# Phase B T5 — real-scale stages 1–6 benchmark, py3.8 pipeline, compare CLI fix (2026-10-22/23, Sonnet 5.5 medium)

Baseline: HEAD 7bcb07a + uncommitted Phase B T1–T4 (+ this task's edits, no commit). Host: macOS arm64, 12 cores, 48 GiB, `.venv` Python 3.11 / numpy 2.x.
Script: `scripts/benchmark_scale_compare.py --stages 1-6` (plan_v3_1 B4). Raw per-command results: `T5_bench/run_{A,B,C}_*.json`; the
launch line is `T5_bench/launch.sh`. Every CLI command is its own process under `/usr/bin/time -l` (RSS = that process's maximum resident
set size; hashed-file / mask-decode counts from `SIMCOMPARE_IO_COUNTERS`). All data under `/tmp/bench_real`, deleted by the script afterwards.

**Caveat on wall times: the three chains ran concurrently** (A, B, C started together, 23:01 KST; a full pytest, two py3.8 pytest chunks and the
py3.8 pipeline overlapped parts of stages 1–5). RSS and output bytes are unaffected (per-process); wall times are inflated, compare/stage-1 by
~1.3–1.4× against the solo numbers in `docs/plan/scale_compare/T5_benchmark.md` (compare, 1262² centered: 587 s solo vs 812 s here; 1500²: 747 s vs 1,010 s).
Treat the walls as upper bounds. Limits: `max_operation_bytes: 34359738368` (32 GiB, below the 48 GiB host RAM) in every config; the default 8 GiB
and the stage-6 estimate are discussed under *Guard*.

## Matrix actually run, and what was skipped

| run | crop (ref / target) | profile | electrodes | N per model | P | stage-6 variants |
|---|---|---|---|---|---|---|
| A | 1262² / 1262² | centered (one band per case at the crop centre ±50 px: one big high-link region) | distinct (A/B, C/D) | 1,682 | 50, 200 | labelled+artefact P=50,200; stage-5+artefact P=50,200; labelled+recompute P=200 |
| B | 1262² / 1262×1134 (asymmetric) | components (3 of 196 lattice sites per case: ~196 separated regions) | shared (59-electrode pool, every electrode in ~57 cases) | 1,682 | 50, 200 | stage-5+artefact P=50,200; labelled+artefact P=200; stage-5+recompute P=200; guard bracket P=50 (labelled, recompute) |
| C | 1500² / 1500² | centered | distinct | 1,682 | 200 | labelled+artefact, labelled+recompute |

Window sizes 10/20/30 µm, jitter 0.5 µm, sort diameter 5 µm, 32 diameters, one layer, `--min-count 3 --window-um 6 --merge-overlap 0.5`.
Skipped (the full cross product would be ~6–8 h wall): centered×shared, components×distinct, components at 1500², P=50 recompute for A/C, a
second layer, `full_bucket_detail`, and real data. 1262² ran for both profiles and both electrode modes only along the A/B diagonal.

## Per-stage results (wall / peak RSS / output bytes)

| stage / command | A 1262² centered, distinct | B 1262² components, shared, asymmetric | C 1500² centered, distinct |
|---|---|---|---|
| 1 import-hotspots (per model) | 259 s / 862 MiB / 207 MB | 281 s / 835 MiB / 246 MB | 346 s / 849 MiB / 247 MB |
| 2 validate-data | 141 s / 1,110 MiB / 152 MB | 130 s / 1,097 MiB / 152 MB | 195 s / 1,170 MiB / 152 MB |
| 2 compare (incl. `comparison_masks.zip`) | 812 s / 3,492 MiB / 488 MB (zip 51 MB) | 582 s / 1,666 MiB / 420 MB (zip 49 MB) | 1,010 s / 3,381 MiB / 511 MB (zip 72 MB) |
| 3 import-electrode-assets (per model) | 108 s / 570 MiB / 23 MB (3,364 assets) | 71 s / 568 MiB / 12 MB (61 assets) | 142 s / 574 MiB / 26 MB (3,364 assets) |
| 4 candidates | 299 s / 1,216 MiB / 30 MB | 289 s / 1,247 MiB / 60 MB | 386 s / 1,321 MiB / 30 MB |
| 5 confirm-pairs P=50 | 213 s / 885 MiB / 22 MB | 197 s / 1,027 MiB / 0.2 MB | – |
| 5 confirm-pairs P=200 | 210 s / 1,247 MiB / 90 MB | 200 s / 1,005 MiB / 0.6 MB | 277 s / 1,252 MiB / 90 MB |
| 6 compare-windows P=50, labelled, artefact | 323 s / 1,056 MiB / 69 MB | – | – |
| 6 P=50, stage-5, artefact | 295 s / 817 MiB / 30 MB (25 pairs) | 294 s / 816 MiB / 63 MB | – |
| 6 P=200, labelled, artefact | 462 s / 1,226 MiB / 274 MB | 443 s / 1,221 MiB / 276 MB | 571 s / 1,064 MiB / 275 MB |
| 6 P=200, stage-5, artefact | 346 s / 871 MiB / 121 MB (100 pairs) | 405 s / 962 MiB / 252 MB (199 pairs) | – |
| 6 P=200, labelled, **recompute** (no artefact) | 1,100 s / 3,297 MiB / 274 MB | – | 1,382 s / 3,438 MiB / 275 MB |
| 6 P=200, stage-5, **recompute** | – | 823 s / 1,532 MiB / 252 MB | – |
| 6 P=50, labelled, recompute (guard run, see below) | – | 659 s / 1,620 MiB / 68 MB | – |

Data generation 47–56 s (4 workers); input 1.93 / 2.03 / 2.66 GB, imported 413 / 484 / 493 MB. **Disk peak** (folder size after any command, per chain):
A 3.84 GB, B 3.49 GB, C 4.75 GB — about 12.1 GB if the three coexist; each stage-6 output directory (≤ 276 MB + PNGs) was deleted after its checks.
Chain wall: A 5,137 s, B 5,352 s, C 4,977 s (sum of commands 4,937 / 5,106 / 4,801 s).

Observations:
- **Stage 6 artefact:** reuse saves one full area pass: recompute needs 335,744 (A, C labelled) / 322,944 (B stage-5) mask decodes against 228,096 / 215,296 with the
  artefact; wall 1,100 → 462 s (A), 1,382 → 571 s (C); RSS 3.3–3.4 GiB → 1.06–1.23 GiB. Reuse == recompute is pinned byte-for-byte by T3's tests, not re-checked here.
- **Stage 6 output size follows P·sizes·32** (rows), not N: P=200 → 19,200 rows, the output folder is 274–276 MB with its thumbnails (T4: `local_delta.json` 234 MB, `local_delta.html` 6 MB at P=200).
- **Stages 4/5 are bounded by authentication, not by N·cells:** candidates 1.2–1.3 GiB; stage 5 hashes 215,298 files and decodes 215,296 masks (every observed mask twice: `load_real_manifest` authenticates each side twice in 4/5/6, B6). Stage 5 is minutes, not seconds.
- RSS per stage is flat across the profiles; the heavy peak is stage 2 `compare` (3.4–3.5 GiB at one layer, 1.7 GiB with 62 shared electrodes — fewer distinct groups) and stage 6 recompute.

## Assertions (exit 3 alone is not success)

Checked per stage-6 run against its own correspondence file (all passed): ready pairs == `provenance.n_ready`; `n_rows == len(rows) == ready × 3 sizes × 32`
(4,800 / 2,400 / 19,200 / 9,600 / 19,104 rows seen); `jitter` present on every row; `explained_fraction` present; no leftover control files; named-pair rows ==
all rows for the labelled fixtures (both endpoint bindings + `repeats_per_frame`: 4,800 / 19,200 rows), 0 for the stage-5 fixtures (see below); artefact runs decode fewer masks
than recompute runs. Stage 4: ready ROI rows 1,682 per export (A, C: 1 region → 1 merged query per direction) and 5,018 / 5,023 (B: 196 regions → 196 queries per direction, each
linked case an export row); stage 5: match files hold 1,682 / 1,682 (A, C) and 5,018 / 5,023 (B) queries.

Findings that are not failures:
- **Stage-5 pairs ≠ accepted decisions.** `confirm-pairs` collapses accepted candidates that name the same position from both directions: 50 accepted → 25 pairs and 200 → 100 (A, C: one
  region, mirrored matches), 200 → 199 (B). The benchmark's first assertion (`pairs == accepted`) therefore reported a *benchmark* failure in all three runs; it is corrected in the script
  to `accepted/2 ≤ pairs ≤ accepted` (A, B and C ran with the old text and list this in `assertions_failed`; everything else in that list is empty).
- **The stage-5-produced correspondence has no endpoint bindings** (`both_endpoint_pairs: 0`), so the named-pair branch runs only for the labelled fixture. A stage-5 pair is a position pair.
- B's stage-5 fixture finishes `partial` (`incomplete_local_delta`): rows of windows that touch the frame edge or an unobserved area are `window_clipped`/`partial_observation`
  (576 + 448 of 4,800 rows in the N=200 dry run). A, which has a single central region, stays `available`.
- **`--expected-cases` is required for ready ROIs** (found in the py3.8 pipeline): without it `expected_case_inventory_status` is `unconfirmed`, the source coverage is
  `partial` and every stage-4 ROI is `blocked` (`map_coverage_incomplete`), so there are 0 queries downstream and stage 6 is `unavailable` with 0 rows. The ETX runners' stage-1 JOBS
  carry no such file; `docs/usage.md` and the runner notes now say so.

## Guard-refusal boundary (run B, labelled P=50, recompute, 32 GiB configured)

| `max_operation_bytes` | result | wall / RSS | mask decodes |
|---|---|---|---|
| 1 | exit 2 `oversized_real_operation: estimated 7,811,835,172 bytes exceed 1` | 101 s / 625 MiB | 161,472 (= load-validation; no compute) |
| 7,811,835,171 (estimate − 1) | exit 2, same estimate 7,811,835,172, nothing published | 99 s / 648 MiB | 161,472 |
| 7,811,835,172 | exit 2, next phase: `estimated 7,842,725,348 bytes exceed limit 7,811,835,172 (50 ready and 0 blocked pairs × 3 window sizes × 32 diameters)` | 196 s / 828 MiB | 215,296 |
| 7,842,725,348 | exit 0, 4,800 rows, 68 MB | 659 s / 1,620 MiB | 326,144 |

Correction (RB review A2, coordinator): the first two rows were refused by the stage-1/2 dataset-load admission in
`configured_dataset` (estimate 7,811,835,172 B, after 3 authentication passes), NOT by the stage-6 guard. Only the third row
(limit 7,811,835,172) is the stage-6 guard (estimate 7,842,725,348 B, refused after 4 authentication passes, before compute). The
"one byte below the stage-6 estimate" boundary was therefore not exercised at real scale; unit tests cover it.

The refusal costs ~100 s (the authentication that precedes pair binding), never starts compute and publishes nothing. **The estimate is 4.8× the measured peak** (7.84 GB vs 1.62 GB) and sits just
below the default 8 GiB (8.59 GB) for P=50 recompute at N=1682; a larger P or crop may exceed the default (`oversized_real_operation`, not measured at P=200) — the conservative
side; the ETX docs tell the operator to raise it (physical RAM at most).

## Python 3.8 (`/tmp/py38`: Python 3.8.20, numpy 1.21.6)

`PYTHONPATH=src /tmp/py38/bin/python -m pytest -p no:warnings -q`:
- chunk 1: `test_stage4_streaming test_stage6_scale test_stage6_writers test_set_publication test_compare_publication_cli test_scale_guards test_candidates test_confirm_pairs test_local_delta test_local_delta_report` → **≈387 passed, 3 skipped** (counted from the dots) (copy-fallback skips), exit 0.
- chunk 2: `test_stage46_equivalence test_electrode_map test_real_roi_export test_roi_export test_candidate_coverage test_real_hotspot test_comparison_v2` → **≈279 passed**, exit 0.
- Pipeline with the **rebuilt runners** (copies in a work dir, `BASE_DIR`/JOBS edited: `--expected-cases` added to stage 1, `--window-um 6`, `CHANGE_ME` → `bench`), N=300 per model, 1262², components, distinct, P=50
  (`T5_bench/py38_pipeline_N300.json`, driver `py38_pipeline.py`): stage 1 174 s, 2 (validate+compare) 234 s, 3 71 s, 4 92 s, 5 75 s, 6 183 s, all exit 0; 882 / 891 queries, 50 pairs ready, **4,800 rows = 50 × 3 × 32**, `comparison_masks.zip`
  present, status `partial` (window-clipped rows, as in run B).

## Compare CLI fix (item 1)

`pipeline.run_numeric_compare` marks exceptions raised from `stream.begin()` on (`keeps_previous_outputs = True`); `cli.main`'s failure handler (T4's two lines) then skips `_remove_reports`,
keeps `comparison.json/csv/comparison_masks.zip` and writes only `error.json`/`execution.json`. Input/config errors before the publication opened keep the documented "remove managed reports" rule.
`tests/test_compare_publication_cli.py` (8 tests): target compute error (contract and `ValueError`), reference/target side ENOSPC, commit ENOSPC, ENOSPC mid-publication (rolled back), unreadable
journal (`comparison_recovery_unreadable`, everything as found), input error before the publication — each with a previous generation + unrelated files, then "next run publishes". The five fault tests and the
two recovery tests fail without the fix (checked by disabling it).

## Validation

Full `pytest` (3.11, `.venv`, `--ignore=tests/test_handoff.py`): exit 0, ≈1,653 passed, 5 skipped (counted from the progress dots; `-q` printed no summary line), no failures, including `test_etx_examples.py::test_stage_runners_are_current_and_parse` (runners rebuilt).
Run before the final `build_etx_stage_runners` pass (see README checkpoint for the last re-run).
