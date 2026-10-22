# Phase B T4 report — stage-6 writers + full-set publication transaction (Sonnet 5.5 high, 2026-10-22, uncommitted on top of 7bcb07a + T1–T3)

Risk tier HIGH. No commit, no bundle rebuild. All validation ran in the foreground.

## Items

1. **A3 streaming (local_delta.py, local_delta_report.py).**
   - `LocalDeltaResult.payload()` (live objects) + `to_dict()` (= `json_value(payload())`, unchanged output). `_write_json` walks the payload with
     `json.JSONEncoder(ensure_ascii=False, indent=2, allow_nan=False).iterencode` and a `list` subclass (`_LazyList`: real items in `_items`, `__len__`/`__iter__`
     overridden, each item passes `json_value` only when the encoder reaches it). Lists longer than 32 become lazy at every depth, so neither `rows` nor
     `per_pair_diameter` nor the rankings ever exist as a converted copy. Written in 64 KiB pieces. `_LazyList` is only safe through `iterencode` (the C encoder of
     `dumps` without indent would read the empty storage); a test pins that.
   - `_csv_rows` is a generator; `_write_csv` converts rows with `json_value` one at a time. `local_delta_rows.csv` uses the static column tuple `CSV_ROW_COLUMNS`
     (every row is checked against it; an empty table keeps the `value,status,reasons` placeholder header); the two small sensitivity tables keep the exact
     first-seen-key rule (rows held). Cell rendering (`None`/bool/dict/list → compact JSON) unchanged.
   - `write_local_delta_html` writes `head`, then each window-size section and each row as it is produced, then blocked list + `</html>`; the page never exists as one
     string. Every file (json, csv, html, `_thumbs.json`) is written to a temp sibling (`staged_file`) and renamed when complete, so a library caller never sees a
     half-written file either.
   - Bytes equal the frozen 7bcb07a writers: T1 oracle (all 21 `compare-windows` runs incl. compact, empty pair table, all-blocked, one size, rank cases; whole output tree
     + stdout/stderr/exit code) green on 3.11 and `/tmp/py38`; plus direct tests against the frozen `_write_json_csv` / `write_local_delta` (randomised nested
     payloads and tables, multiplied real result, empty result, full and compact).
2. **B2 transaction (new `src/simcompare/publication.py`, pipeline.py, cli.py).**
   - `SetPublication` generalises the Phase A `_ComparisonStream` protocol (journal → backups (hard link, copy fallback) → `os.replace` → atomic commit marker → delete
     backups/journal/marker) from two fixed names to the allowlisted file set `LOCAL_DELTA_FILES` (json, csv ×3, html, `_thumbs.json`, `_thumb_<12 hex>.png`,
     `_overview_<a|b>_<12 hex>.png`). I did **not** refactor `_ComparisonStream` itself (reviewed three times; the new class copies the protocol and tests mirror
     `test_comparison_v2.py`). Everything else in the directory (look-alikes such as `local_delta_notes.csv`, `comparison*`, sub-folders, `execution.json`/`error.json`) is
     never read, moved or deleted. A staged name outside the allowlist, an empty stage, or an allowlisted name that is not a regular file refuses before the journal.
   - The writers write the whole new generation into `.local_delta.staging.<pid>.<rand>/`; `run_numeric_local_delta` opens the transaction (`begin()`: recover an
     earlier publication, clean stale scratch, create scratch) **after** admission and **before** the compute (so an unreadable earlier state is reported before hours of
     work; a refused/oversize run creates nothing), then compute → `write_local_delta` → `write_local_delta_html` → `commit()` → `abort()` (scratch removal; removes an output
     folder this run created if nothing was published). Returned `outputs` are the final paths.
   - Guarantees (docs/usage.md says exactly this): before the marker → previous generation as a whole (ENOSPC/OSError/Ctrl-C/compute error/page admission refusal); after
     the marker → new generation, cleanup errors never roll back; killed process → next run rolls forward/back; unreadable/corrupt journal or marker, journal naming a
     non-allowlisted file, lost file without backup → `local_delta_recovery_unreadable`, nothing changed; a marker/journal that cannot be removed blocks the next publication.
     Not guaranteed: the torn interval between a kill and the next run; concurrent writers; storage that lies after fsync.
   - Behaviour change worth knowing: the old writer deleted every `local_delta*`/`window_sensitivity*` `.json/.csv/.html`; now only the allowlisted names belong to the
     generation, and a previous run's thumbnail PNGs are replaced with the generation (the old code left them forever).
3. **Memory** (below): report-phase RSS no longer grows; the whole run is bounded by the rows phase.

## Needs your decision (outside the T4 ownership list, small, flagged)

- **cli.py (2 lines).** `cli.main`'s generic failure handler (`_remove_reports`) deletes every `local_delta*`/`window_sensitivity*` `.json/.csv/.html/.png/.npz` in `--out` on
  any `OSError/ValueError/...` (exit 2) — which would destroy exactly the previous generation (and, for `local_delta_recovery_unreadable`, the files the recovery needs)
  that the transaction preserves, so B2 would have held only for library calls. I made `run_numeric_local_delta` mark exceptions raised **after the transaction opened**
  (`exc.keeps_previous_outputs = True`) and the handler skips `_remove_reports` for them (it still writes `error.json`/`execution.json`). Input/config/correspondence/admission errors (before
  `begin()`) keep the documented "remove managed reports" rule (docs/usage.md:218; a test pins it). Phase A's `compare` has the same handler/transaction conflict
  (`comparison*` is deleted on a publication `OSError` through the CLI); I left `compare` alone. If you prefer not to touch cli.py, revert those two lines and B2 holds only below the CLI.
- **compare_masks.py (T3 file, 8 lines).** `ZipFile.writestr(name)` stamps the wall clock, so two identical `compare` runs could differ in artefact bytes when they straddle a
  2-second boundary: T3's `test_a_second_compare_replaces_the_artefact_with_the_pair` ("deterministic bytes") failed once in my full runs. Members now use `ZipInfo` with a fixed
  date (`_member`). Stage-6 reuse checks are unaffected (content hashes/CRC).
- **Pre-existing flake, not touched:** `tests/test_stage4_streaming.py::test_stage5_accepts_slim_and_old_form_exports_alike` compares two `confirm-pairs` outputs including
  `labelled_at` (wall clock, 1 s resolution) without pinning the clock; it failed once in a full run and passed 3/3 alone.
- **Guard is now conservative:** T3's `report`-phase estimate still charges the page text `ready × sizes × 256 KiB`; the page is no longer held (real_hotspot.py is not T4's file, so unchanged). Measured peaks are
  far below the estimates, so the guard only over-refuses slightly.

## Acceptance

| criterion | result |
|---|---|
| T1 stage-6 equivalence (files byte-identical) | `tests/test_stage46_equivalence.py` whole file green on 3.11 (2:07); stage-6/asset/perturbation subset green on `/tmp/py38` (42 tests, 2:08) |
| streamed JSON/CSV/HTML == frozen writers | `tests/test_stage6_writers.py` (56 tests, 2:00 on 3.11): 12 randomised nested payloads, 12 randomised CSV tables (+ empty), edge documents, NaN/inf rejection leaves no file, fixed-column CSV, real + multiplied result full/compact, empty result full/compact; page written row by row and failure leaves no page |
| streaming avoids the second copy | tracemalloc, 6,144 rows through `write_local_delta`: frozen writer 681.4 MB peak → 1.30 MB (523×); test asserts ≥ 4× |
| transaction faults (3.11 and 3.8) | `tests/test_set_publication.py` (41 tests incl. 3 skips for "copy fallback without a previous set", ~15 s on 3.11, in the 2:14 3.8 run): fault at **every** filesystem step (45–60 steps: reads, listings, stat, create, fsync, replace, unlink, link/copy, mkdir/rmdir/rmtree, dir sync) × {OSError, Ctrl-C, process death} × {with / without previous set} × {hard link / copy fallback} × unrelated + look-alike files present; after each fault, in-process result is old or new, and for process death the restart recovery is itself killed at each of its own steps and then run fault-free: state = old (before marker) or new (after), no leftovers, unrelated files byte-identical, directory not wedged. Plus read-fault fail-closed at every recovery read (rollback and roll-forward, EIO/EACCES), 11 corrupt-journal forms (incl. names outside the allowlist/path escape), lost file without backup, marker lstat error, cleanup interrupt after commit never rolls back, undeletable marker/journal blocks publication, stale scratch of a killed run, an AST test that every os/shutil mutation lives in a fault-injectable step. Mutation checks: discarding the backups before the marker, and not removing new-only files in rollback, both fail the suite |
| CLI level (`test_stage6_writers.py`, 23 tests) | 8 writer-boundary faults (ENOSPC at a PNG / row / JSON / CSV cell, bad CSV value, Ctrl-C in overview / compute, compute error) × {previous generation + unrelated files, first run}: previous generation byte-identical, no scratch, `error.json` written, next run publishes; page-guard refusal after numeric files; failure after the journal (`ENOSPC` at the 4th replace) rolled back; unreadable journal → exit 2, everything as found; input error before the transaction still removes reports; killed between files → next run repairs and equals a fresh run; successful run replaces a previous generation of different file names and keeps unrelated files |
| full pytest 3.11 | green except `test_handoff.py` (sibling dirty), `test_etx_examples.py::test_stage_runners_are_current_and_parse` (stale bundles, T5) and the one-off flake above; chunks: 3:00 (all other files), 2:07 (equivalence), 5:10 (stage6_scale + writers + publication) |
| 3.8 | `test_set_publication.py` + `test_stage6_writers.py` green (2:14); artefact/three-file tests of `test_stage6_scale.py` green after the zip fix |

## Benchmark (docs/plan/scale_stage46/T4_bench/, in-process `compare-windows` CLI, fresh process per run)

Same data as T3 (N = 300 cases per model, 1262², one layer, 32 diameters, grid hotspots, distinct electrodes with real layout PNGs, named-pair path active, window sizes 10/20/30,
jitter 0.5, Python 3.11, macOS arm64). RSS = `ru_maxrss` after the phase (monotone). T3 column = T3_report.md (before T4).

| run | rows | wall | write json/csv | report | commit | peak RSS | T3 peak RSS | T3 wall |
|---|---|---|---|---|---|---|---|---|
| P=50 recompute | 4,800 | 160.4 s | 2.4 s | 13.5 s | 0.0 s | **751 MB** | 1,285 MB | 162.0 s |
| P=50 artefact | 4,800 | 95.6 s | 2.4 s | 13.5 s | 0.0 s | **677 MB** | 1,214 MB | 96.8 s |
| P=200 recompute | 19,200 | 333.2 s | 9.8 s | 21.3 s | 0.1 s | **928 MB** | 3,078 MB | 337.5 s |
| P=200 artefact | 19,200 | 268.5 s | 9.7 s | 21.2 s | 0.1 s | **852 MB** | 2,999 MB | 273.6 s |
| P=50 frozen 7bcb07a | 4,800 | 173.2 s | 3.3 s | 12.0 s | – | 2,694 MB (report) | – | – |

- RSS after the phases (P=200 artefact): load 197 → artefact 211 → rows 852 → json/csv 852 → report 852 → commit 852; (P=50 recompute: rows 745 → json/csv 746 → report 751). The writers
  add 0–6 MB over the rows phase; the peak is now the compute (rows) phase, not the writers.
- Wall: json/csv 3.5/12.5 s → 2.4/9.8 s, report unchanged (13.5/21.3 s), commit (fsync of 388/1,481 PNGs, backups, replaces, marker) 0.0/0.1 s. Total wall −1 % to −2 % vs T3.
- Output equality: P=50 new (recompute and artefact) vs frozen 7bcb07a: 395 files byte-identical (388 PNG, json/csv/html/thumbs) except `provenance.config_path/config_sha256`
  (bench used `cfg_old.yaml`/`cfg_new.yaml`, as in T3); `compare_trees.py`. New recompute vs new artefact: identical. No hidden files left in any output folder.
- Output sizes P=200: `local_delta.json` 234 MB, `local_delta_rows.csv` 24.4 MB, `local_delta.html` 6.2 MB (openable), `window_sensitivity.json` 1.9 MB, `_thumbs.json` 1.2 MB, 1,481 PNG.
- Residual: the compute phase now sets the peak (852–928 MB at P=200, N=300; the production N=1,682 estimate from T3 stays 1.5 GB at P=200 with the artefact).

## Files

Production: **new** `src/simcompare/publication.py`; `src/simcompare/local_delta.py` (payload/lazy JSON/CSV writers), `local_delta_report.py` (progressive page, staged thumbs.json),
`pipeline.py` (`run_numeric_local_delta` transaction), `cli.py` (2 lines, see above), `compare_masks.py` (deterministic zip, see above). Tests: **new** `tests/test_set_publication.py`,
`tests/test_stage6_writers.py`. Docs: `docs/usage.md` (compare-windows "출력 쓰기와 공개"; the "writers still old" sentence in the Phase B paragraph). Bench: `docs/plan/scale_stage46/T4_bench/`
(`bench_build.py`, `bench_run.py`, `compare_trees.py`, `summarize.py`, `results.json`; data in `/tmp/t4bench`, 1.1 GB, not kept in the repo).

## Notes for T5

- `build_single_file.py` picks up `publication.py` (rglob over src/simcompare); the stale-bundle test stays red until the runners are rebuilt.
- `configs/etx/README.md` and `real.stage4.example.yaml` still say stage 6 writers are "not yet streaming" — T5 comment fix.
- ETX operators: a `compare-windows` run that is killed leaves `.local_delta.staging.*` (safe to delete) and, only if killed inside the publication window, `.local_delta.publishing`/`.prev/`; simply re-running repairs.
