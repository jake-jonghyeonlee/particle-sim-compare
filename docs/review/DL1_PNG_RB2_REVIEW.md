# DL1 PNG input — independent review R-B2 (refresh of the final gate R-B)

Date: 2026-10-12/13. Reviewer: fresh independent Claude Opus 5.5 session, effort high, read-only
(Task `task_55a8afbd720c`, Dispatch `ctx_80d64f70a78b`). I did not write this code or its plan.

Verdict: PASS-WITH-NOTES for 1f9d005c32399df6fe31728fb55103b4d54f17c6

Candidate: `particle-sim-dl1`, branch `feat/png-input`, fix diff
`01240f9f61541fa328976a5e543c60276cc1fff7..1f9d005c32399df6fe31728fb55103b4d54f17c6` (11 files, +732/−36),
read in the context of the full diff from `03bf3e33c7107af75297aa298fa323e24018f28d` (23 files, +5591/−16).
Contract: plan v1.1 and adoption conditions C1–C3. Review being refreshed: `DL1_PNG_RB_REVIEW.md` (unchanged).
This verdict applies only to that commit. As the final gate it covers the full diff `03bf3e3..1f9d005`,
relying on R-A3 and R-B for the parts the fix commit does not touch.

**Summary.** B1 is fixed: every row of the R-B table and 29 further path spellings are refused with
exit 2 and nothing created, and seven legitimate sibling destinations still work. The new fallback
publication never replaced or modified an existing destination in any race I built, and never left a
`manifest.json` at the destination or in staging after any Python-level failure or Ctrl-C. Report and
integrity v2 are handled consistently, bundles are byte-deterministic, all eleven N4 mutants are now
killed, and no core file changed. I found no blocking issue. There are 7 new notes. The two worth
acting on are R1 (SIGTERM is not covered by the staging clean-up, and two documents word that
guarantee more broadly than the code delivers) and R5 (older sections of `data_contract.md` still
state the v1 names).

## Status of B1 and N1–N12

| Finding | Status | Evidence |
|---|---|---|
| B1 case/NFC/NFD `--out` inside the input tree | Fixed | `import_png.py:276-285` compares `os.path.samefile` for every existing ancestor of `out` before any directory is made. Real CLI, APFS: all five R-B rows plus the reversed NFD/NFC row give exit 2, 0 new entries, source bytes and mtimes unchanged. See the table below for the further attempts. Removing the check fails `test_rb_B1_review_table_refused_without_any_creation[case-model]`. |
| N1 old glibc / Windows publish | Fixed as far as it can be shown on macOS | A missing symbol, `ENOSYS` or `EINVAL` selects the exclusive-claim fallback (`:250-267`); other errors (`EACCES`, `EEXIST`) do not. The method is written into the sealed report. Real Linux and Windows hosts remain unrun, and the documents say so. |
| N2 loadable manifest in a failed staging folder | Fixed for exceptions and Ctrl-C; not for SIGTERM | 33 injected faults (`OSError`, `KeyboardInterrupt`, `SystemExit` at 11 points) and 6 real SIGINT deliveries: staging never holds `manifest.json`, the report says `failed` or `interrupted`. SIGTERM and SIGKILL after the rename leave it in place. See note R1. |
| N3 directories before validation; masked error | Fixed | Missing root, root that is a file, and `--candidate-grid` without `--export` create nothing. With an abandoned `import-report.json.writing` file the original error is reported and the report says `failed`. |
| N4 eleven test gaps | Closed | All eleven mutants killed by the tests the worker named (table below). The S16 fixture is asymmetric: bbox `[130,710,137,719]`. |
| N5 extra directories; traceback on malformed report | Fixed, one residual | Extra file, hidden file, symlink, empty and nested directory are all detected. 55 of 56 malformed inputs give a clean exit 2. Residual in note R6. |
| N6 `cno` names mixed into layer vocabulary lists | Fixed | Report has `electrode_layer_union`/`unique_electrode_layers` and `cno_layer_union`/`unique_cno_layers`; combined lists are renamed `all_source_*`. Fixture: electrode `L,N,U,U5`, cno `C4,C6`. |
| N7 raw `extraction_status: "complete"` | Fixed | No key `extraction_status` in the report. `extractor_run_status` is `ran` at top level and per model, beside `connectivity_observation: partial`. No field has the value `complete` for a run. |
| N8 absolute path in the invalid-metadata diagnostic | Fixed | Malformed, missing and directory metadata: diagnostic path is `metadata`, no absolute path anywhere in the bundle, exit 2, no manifest, no assets, bundle verifies as `blocked`. |
| N9 `transform` key in `grid_check` | Unchanged | `data.py` and `train.py` have no diff against `01240f9`. Additive only, as before. |
| N10 export with grid warnings | Documented | README and USAGE now state "Exported does not mean the grid is accepted". |
| N11 runtime | Improved | 484 passed, 1 skipped in 77.3 s (was 446 tests in 78.7 s). `imported` is module-scoped; slowest test still 15.8 s. |
| N12 `.pyc` refresh by the reviewer | Not repeated | Every Python call here used `PYTHONDONTWRITEBYTECODE=1`. |

## New blocking findings

None.

## New notes (non-blocking)

- R1 — SIGTERM is not handled, so N2 is only partly closed. Python's default SIGTERM action ends the
  process without running the handler at `import_png.py:473`. Measured with real signals: SIGTERM
  after `:460` leaves the staging folder with `manifest.json` and a report saying `exported`; once the
  seal has run, that folder passes `--verify-bundle`. SIGKILL does the same and cannot be prevented.
  Nothing is published, the folder keeps its hidden `.NAME.staging-*` name, and its content has
  already passed loader and validator, so no wrong data results; this is why it stays a note, as N2 was.
  Two sentences are broader than the behaviour: `docs/data_contract.md:572` ("After any
  failure/interruption…") and README ("disabled on any failure/interruption"). USAGE is accurate
  ("실패/KeyboardInterrupt 후"). Suggested: a SIGTERM handler that raises `SystemExit` for the duration of
  the import, and "Python exceptions and Ctrl-C" in those two sentences.
- R2 — After a successful fallback publication the staging folder is kept. It is a second complete
  bundle with `manifest.json` that verifies, next to the published one, and it doubles disk use. The
  documents only mention kept staging for failures. Native publication leaves no staging folder.
- R3 — The fallback copies `manifest.json` straight to its final name (`:239`). A hard kill or power
  loss during that write can leave a truncated `manifest.json` at the destination; a kill after it and
  before `:240` leaves a complete but unverified bundle. Both are loud or correct on use (invalid JSON,
  or exclusively copied files), and USAGE tells users to run `--verify-bundle` first. Copying to a
  temporary name and renaming inside the claimed folder would close it. Not run; from code reading.
- R4 — If the final `completed` event cannot be written (stdout closed, `BrokenPipeError`), the run
  prints "import failed; unpublished staging evidence retained at …" and exits 2 although the bundle
  is published and verifies. With the fallback, the kept staging copy is then marked `failed`.
- R5 — Older sections of `docs/data_contract.md` were not updated and now contradict the new section
  in the same file: `:166` (`dl1_png_import_v1`), `:436-437` (atomic rename including Windows), `:441`
  (`dl1_png_bundle_v1`), `:463` ("Legacy per-model `extraction_status: complete`"). USAGE, README and
  the example metadata file are consistent with v2. A script written from `:463` would fail on a
  missing key, not read wrong data. A "superseded by the R-B section" line on each would do.
- R6 — Verification residue. An empty `.npy` in a bundle that is otherwise consistently re-sealed ends
  in an `EOFError` traceback, exit 1 (`:137`; `EOFError` is not in the list at `:156`). An old v1
  integrity file is rejected as "invalid bundle integrity fields", not as an unsupported version.
  A `.DS_Store` created by Finder makes a bundle fail verification, which is correct but worth one line in USAGE.
- R7 — Test gap: changing `:262` so that every `OSError` falls back passes all 249 targeted tests.
  Production behaviour is correct (`EACCES` and `EEXIST` do not fall back).

## What I verified and how

Tests ran from `/Users/jonghyeonlee/git/particle-sim-dl1` with
`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider`. Scratch scripts, trees and
mutant copies are only in `/tmp/dl1-rb2-review.rYOXCv`. Python 3.12.13, Pillow 12.3.0, macOS arm64, APFS.

| Focus | Method | Result |
|---|---|---|
| 1. B1 table | Real CLI, full snapshot (mode, mtime, SHA-256) of the surrounding tree before and after | 5 R-B rows and the reversed NFD-root/NFC-out row: exit 2, 0 new, 0 changed |
| 1. New attempts | Same method, 29 spellings | All refused, nothing created: exact-case forms; `Src/../Src`; relative out with cwd inside root; relative root `.`; `out == root` in another case and with a trailing separator; doubled trailing separators; three missing intermediate folders under a case variant; `nonexist/../SRC/…`; `..` zig-zag; out an existing ancestor or grand-ancestor of root; root inside out; out through symlinks to root, to a model folder, with a case-variant target, relative, and reached by a case-variant link name; root through a symlink and through a case-variant symlink; out a dangling symlink; out itself a symlink into root; the `/System/Volumes/Data/…` firmlink alias for out, for root and for both |
| 1. No false refusal | Seven sibling destinations | All exit 0: plain sibling, `Src2/o`, `SRC_x`, `Sr`, two new parent levels, relative `../rel_ok` from inside root, trailing slash. No staging left |
| 2. Native publish | Normal run; six processes to one destination, three rounds; destination (empty folder, non-empty folder) created just before the rename | One winner, five exit 2; winner verifies; raced folders untouched; losers' staging has no `manifest.json`, status `failed`; method `darwin_rename_excl` |
| 2. Fallback publish | Native call replaced by a missing symbol; six real processes, six rounds (three with slowed copying); destination folder, file and dangling symlink appearing before the claim | One winner each round; existing content never changed; symlink target not created; method `exclusive_directory_claim_manifest_last`; manifest and all assets byte-equal to the native bundle |
| 2. Fallback faults | `OSError` and `KeyboardInterrupt` at copy 1, 2, 17, 34, 35 (the manifest) of 35 and in the destination verify | Destination never keeps `manifest.json`; staging never keeps it; statuses `failed`/`interrupted`; the original exception type is raised. A fault before the report is copied leaves a claimed folder without a report |
| 2. Staging faults | `KeyboardInterrupt`, `OSError`, `SystemExit` in `_write_assets`, `load_manifest`, `validate_product`, hashing, seal (before and after), both verifies, publication record (before and after), native rename; abandoned `.writing` file | 34 cases: no `manifest.json` in staging, nothing published, original error kept |
| 2. Real signals | SIGINT, SIGTERM, SIGKILL to a CLI process paused at six points | SIGINT: clean in all six. SIGTERM and SIGKILL: note R1 |
| 3. Versions | Verify CLI on v1 version strings, v1-shaped report and integrity, v2 label with v1 field names; `require_report_version` on five bad values | All rejected with exit 2 and a one-line message; none mis-read. Preview reader uses the same check |
| 3. Unchanged core | `git diff --stat` | No diff `01240f9..1f9d005` in `data.py`, `train.py`, `schema.py`, `model.py`, `checkpoint.py`, `search.py`, `evaluate.py`, `split.py`, `losses.py`, `requirements.lock`, `png_preview.py`, `png_connections.py`, `configs/`, `pyproject.toml`; no diff `03bf3e3..1f9d005` in the eight core files and the lock. Manifest keys are exactly `schema_version`, `candidate_grid`, `products` with `dl1_shape_v1_draft`. Legacy bit-identity was not re-measured; it rests on R-B plus these empty diffs |
| 3. Determinism | Two runs; a copy of the tree under a path with a space and Korean, relative arguments, repeated inspect grids | 37 entries byte-identical in each pair, native and fallback; stdout events equal apart from the output path |
| 3. Report content | Walked every value of a real report; searched JSON and HTML for absolute paths | See N6–N8 rows. No absolute path |
| 4. verify-bundle | 56 tampered copies and one untouched copy through the CLI | Untouched verifies. Detected cleanly: extra file (root, assets, hidden), symlink, empty and nested directory, missing NPY/preview/manifest/report/integrity, renamed NPY and manifest, folder replaced by file, edited manifest/report/preview, flipped NPY bit, 9 malformed integrity files, 17 re-sealed malformed or old reports, 7 re-sealed malformed manifests, junk NPY. One traceback: R6 |
| 5. N4 mutants | Each applied alone in a copy outside the repository, import path asserted, the four targeted test files | 11 of 11 killed, plus a second crosshair variant. 12 further mutants of the fix: 11 killed, 1 survived (R7) |
| 5. Suite | Full default suite three times | 484 passed, 1 skipped in 77.3 s on the counted run; the two earlier runs exited 0 (their summary line was suppressed by my `-q`). No failure in any of the three runs, nor in the three full targeted runs (249 tests) made on mutant copies |
| 6. Documents | Read the four changed documents against behaviour | USAGE states only macOS APFS was run and Linux/Windows are untested on real hosts; "Exported does not mean the grid is accepted" is in README, USAGE and data_contract; skipped-layer and 4-neighbour rules are still called implementation interpretations; no real-data, retrieval-quality or electrical claim. Exceptions: R1 wording, R5 |
| Repository state | `git status --short`, `git rev-parse HEAD` after all runs | `particle-sim-dl1` clean, HEAD `1f9d005c32399df6fe31728fb55103b4d54f17c6` |

N4 mutants and the test that fails:

| Mutant | Killed by |
|---|---|
| Unlisted-files check removed | `test_rb_S14_…[extra-file]` |
| Manifest/report hash link removed | `test_rb_S14_…[manifest-report-link]` |
| Manifest under its final name before validation | `test_rb_S15_multi_stage_failure_and_pending_manifest[validation]` |
| Crop offset x/y swapped; overview marker swapped; crop endpoint swapped; crosshair +10 px | `test_rb_S16_asymmetric_offsets_marker_endpoint_and_crosshair` |
| Escape removed from invalid-model heading; selected-candidate line; stack line | `test_rb_S16_escapes_invalid_model_candidates_and_stack` |
| Folder name written as `lineage_id` | `test_auto_manifest_export_and_pending_prerequisites` |

## What I could not verify

- Linux and Windows on real hosts: `renameat2`, the fallback on a real old glibc, Windows `samefile` and rename behaviour. The fallback ran only on macOS with the native call replaced.
- A second mount of the same directory; the firmlink alias is the closest I could build without privileges.
- Note R3 (kill during the manifest copy) is from code reading; I did not time a kill inside that write.
- Legacy numerics, export correctness against hand-drawn masks, preview placement and search integration were not re-measured; they rest on R-B and on the unchanged files listed above.
- Real source PNGs, real frame sizes, Pillow other than 12.3.0, offline `pip install .[png]`, the opt-in training smoke.
- Physical correctness of the nearest-overlap, skipped-layer and 4-neighbour rules, retrieval quality and electrical truth. These remain open user questions.
