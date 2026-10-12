# DL1 PNG input — independent review R-B (final gate, T3 + T4 and the full diff)

Date: 2026-10-12. Reviewer: fresh independent Claude Opus 5.5 session, effort high, read-only
(Task `task_dc935f328aef`, Dispatch `ctx_16daf9c65eae`). I did not write this code or its plan.

Verdict: FAIL for 01240f9f61541fa328976a5e543c60276cc1fff7

Candidate: `particle-sim-dl1`, branch `feat/png-input`, full diff
`03bf3e33c7107af75297aa298fa323e24018f28d..01240f9f61541fa328976a5e543c60276cc1fff7` (23 files, +4895/−16),
primary scope `6f82f47b0190521b95677f4f0fe9dae3b6617a6a..01240f9` (T3 `e2d3293`, T4 `01240f9`).
Contract: plan v1.1 (all sections; T3, T4, S12–S27), adoption conditions C1–C3, and
`docs/png_input_notes_2026-10-12.md`. Earlier reviews: `DL1_PNG_RA_REVIEW.md`, `DL1_PNG_RA2_REVIEW.md`,
`DL1_PNG_RA3_REVIEW.md` (all unchanged). This verdict applies only to that commit.

**Summary.** The failure is narrow: one blocking finding. The "output must be outside the input tree"
refusal can be bypassed on this machine by spelling the path with different letter case or Unicode
normalisation, and the importer then publishes a bundle, including a model-ready `manifest.json`,
inside the source tree with exit code 0 (B1). The fix is a few lines in `import_png.py`.
Everything else I tried to break held. Legacy outputs are bit-identical to `03bf3e3`, exports are
correct against independently built masks, publication is atomic and never overwrites, bundle
verification detects every single-file change I made, reports are byte-deterministic, the preview is
offline and escaped, and the documents make no false quality, real-data or electrical claim.
There are 12 non-blocking notes; N1 (old-glibc Linux) and N4 (test gaps) are the ones worth acting on.

## Blocking finding

### B1 — A case-variant or NFD/NFC-variant `--out` path publishes the bundle inside the input tree

- Where: `src/psdl1/import_png.py:223-228`. The containment test compares resolved path strings
  (`out.is_relative_to(root)`). On a case-insensitive or normalisation-insensitive file system
  (default macOS APFS) two different strings name the same directory, and `Path.resolve()` does not
  canonicalise either difference. The staging directory is then also created inside the tree (`:232`).
- Reproduction with the real CLI. Source tree `…/Src/M1/{A,B,D,cno}/…`, one valid model:

  | Command (other arguments: `--export --metadata meta.json --candidate-grid 64`) | Exit | Result |
  |---|---|---|
  | `--root …/Src --out …/SRC/M1/o6` | **0** | 36 new entries under `Src/M1/o6`, including `manifest.json` and `assets/` |
  | `--root …/src --out …/Src/M1/o8` (root typed in lower case) | **0** | same, under `Src/M1/o8` |
  | `--root …/입력` (NFC) `--out …/입력/M1/o` (NFD spelling) | **0** | same, under the source model folder |
  | `--root …/Src --out …/src/o5` | 2 | diagnostic bundle published at `Src/o5`; the staging folder was itself read as an invalid model |
  | `--root …/Src --out …/SRC/M1/A/o7` | 2 | diagnostic bundle published inside the electrode folder |

  The exact-case forms (`Src/o1`, `Src/M1/A/o`, `Src/../Src/o2`, a relative path from inside the root, a
  path through a symlink to the root, root given through a symlink) are all refused correctly.
- Consequence: the source tree is modified. Re-importing the same root afterwards fails
  (`unsupported directory layout`, model invalid, exit 2) until the user deletes the folder by hand.
  The first run's own report also carries a warning naming its random staging folder as an empty
  electrode folder. No source PNG is changed, nothing is overwritten, and the later failure is loud,
  so no wrong data reaches a model.
- Violated clauses: plan §4.1 "Use output outside the input tree; never modify original PNGs";
  T3 constraints; the CLI help text ("new destination outside input tree"); `docs/data_contract.md`
  ("Output must be new and outside the source tree"). It is the case the review brief asked about
  (refusal "including via symlink or relative path tricks"), and the run reports success.
- Likelihood: needs a path typed in a different case or normalisation from the one on disk. With
  Korean folder names the NFC/NFD mismatch is a realistic way to hit it on macOS. Linux file systems are
  normally case-sensitive and not affected. Windows behaviour is not verified.
- Suggested fix: compare identities, not strings. Walk the existing ancestors of `out` and refuse if
  `os.path.samefile(ancestor, root)` is true for any of them, before creating the staging folder.
  Add a test that skips when the temporary file system is case-sensitive.
- `tests/test_png_import.py::test_publication_interruption_and_no_overwrite` only tries `root / "output"`.

## Non-blocking notes

**Publication**

- N1 — The Linux publish path is untested and depends on glibc 2.28 or later.
  `import_png.py:143` looks up `libc.renameat2`; on older glibc (for example RHEL/CentOS 7) the symbol
  is missing, `ctypes` raises `AttributeError`, and every import fails at the very last step after all
  the work, leaving only a staging folder. That is a safe failure, but it would make the importer
  unusable on such a closed-network host. Only the Darwin branch ran here. A fallback
  (`os.mkdir(destination)` as the exclusive claim, or `os.link`-style no-replace) and one line in USAGE
  would cover it.
- N2 — A staging folder left by a failure after `:323` (seal, verify or publish failing) contains a
  loadable `manifest.json` and all assets; its report says `export_status: failed` and
  `--verify-bundle` rejects it. After Ctrl-C between sealing and publishing, the hidden staging folder
  is a complete bundle that verifies. USAGE already tells users not to treat staging as a final bundle.
  Renaming the manifest back to `manifest.pending.json` in the exception handler would remove the trap.
  `KeyboardInterrupt` is not caught, so in that case the staged report keeps its earlier status.
- N3 — `out.parent` is created (`:231`) and the staging folder made before the source is read, so a
  failed run can leave new empty parent folders. The handler at `:336-340` writes the failed report with
  `_write_json`, which itself raises if a `.writing` file is left over, masking the original error.

**Bundle verification**

- N4 — Test gaps found by mutation; production code is correct in each case (checked by my own runs).
  - S14: removing the unlisted/missing-files check (`:97`) or the manifest/report hash link (`:102`) passes all tests.
  - S15: the only fault is injected inside `_write_assets`. Writing the manifest under its final name before validation passes all tests.
  - S16: every fixture contact has x = y, so swapping x and y in the crop offset record, the overview
    marker, or the crop endpoint pixel passes all tests, as does moving the context crosshair by 10 pixels.
    This is the "wrong coordinate overlays" counterexample S16 names. I checked the real placement on an asymmetric frame (below).
  - S16: the escaping fixture has no CNT and no invalid model, so removing `escape` from the
    invalid-model heading, the selected-candidate line or the layer-stack line passes all tests.
  - S13: no test asserts that the exported `lineage_id`/`design_version` equal the metadata file.
    Writing the model folder name as `lineage_id` passes `test_png_import.py`.
- N5 — `--verify-bundle` does not notice added empty directories. A re-sealed report that lacks
  `export_status` ends in an uncaught `KeyError` traceback with exit 1 (`:101`, `:373`), not a clean
  message. Verification reads NPY headers only and does not recompute Product input identities.
  The documents describe it accurately as corruption detection, not authenticity: I confirmed that
  editing a file and re-sealing the report and `bundle-integrity.json` consistently passes, as expected.

**Report and status wording**

- N6 — `layers`, `layer_union` and `unique_layers` include `cno` layer names
  (`png_source.py:464`). These never enter the model vocabulary, so a CNT layer name that occurs in one
  model appears in `unique_layers` next to names that do matter for a checkpoint. Split the lists or say so in USAGE.
- N7 — The per-model record still says `extraction_status: "complete"` for a model with unresolved
  components, beside `connectivity_observation: "partial"`; the top-level field is `"extractor_ran"`.
  `status_summary`, `extraction_run_meaning`, the preview and both documents explain the three terms,
  so nothing is misleading once read, but the raw field name remains a trap for anyone scripting against the report.
- N8 — When the metadata file is invalid, the diagnostic stores its absolute path, so that one report
  is not location-independent. Successful and blocked-for-missing-input reports contain no absolute path.

**Legacy surface**

- N9 — `grid_check` now returns a `transform` key. It therefore appears in the validator report, in
  each `grid_gate.products` row of search and evaluation JSON, and in `metadata.grid_checks` inside new
  checkpoints. It is not part of `input_identity`, `input_hash`, `contract_hash`, the config fingerprint
  or any gate comparison, and `simcompare.dl_results` does not read inside `grid_gate`. Additive only.
- N10 — Export succeeds when the candidate grid produces thin-line or component-loss warnings (the
  report marks the evidence `export_candidate_not_approved`, status `warning`). Train and search then
  block by default. This is what the plan and S21 specify; I mention it so nobody reads "exported" as "grid accepted".

**Tests and runtime**

- N11 — Runtime is spread out, not concentrated. Slowest: `test_imported_validator_structured_train_and_search`
  15.8 s (two 1401×1603 products at grid 128, one training step, three CLI subprocesses), then 4.7 s
  and below. The function-scoped `imported` fixture is rebuilt four times at 3.2–3.4 s each; module scope
  would save about 10 s. The 19 T3 tests take 36 s because each starts CLI subprocesses that import torch.
  No flakiness: two full runs, both targeted commands and about 55 mutant runs gave consistent results.

**Review process**

- N12 — I ran `python -m psdl1.import_png` twice from the DL1 directory without
  `PYTHONDONTWRITEBYTECODE=1`. That refreshed five git-ignored files in the existing
  `src/psdl1/__pycache__` (`data`, `import_png`, `png_connections`, `png_contract`, `png_source`).
  They match HEAD, `git status` is clean, and I left them in place.

## Status of deferred earlier notes

| Note | Status | Evidence |
|---|---|---|
| N6 decode memory, all masks held | Disclosed, not reduced | `overhead_note` in every `resource_estimate` states about 17–18 bytes/pixel, the compressed snapshot and retained masks; README and USAGE repeat it. Three 2000×2000 models (13 assets) peaked at 0.5 GB. |
| N9 in-root symlink aliases | Surfaced | `duplicate_geometry` lists identical assets and identical models; the same text is in `diagnostics` and the preview. An alias model is still exported as its own product (no merge, by design), so a pool can hold duplicates if the warning is ignored. |
| N13 three meanings of "complete" | Addressed in report, preview and docs; raw field name remains | See note N7. |
| N14 exact Pillow pin not enforced | Addressed as agreed | Runtime version is recorded; a version other than 12.3.0 adds a diagnostic shown in report and preview; USAGE states the wheel constraint. The pin itself stays exact and unenforced. |
| C1 file read several times | Fixed | One `read_bytes()` snapshot is validated, decoded and hashed (`png_source.py:288-316`). Mutant that re-opens the path is killed. |
| C2 Python unfilter loop for small filtered palettes | Unchanged | Performance only; affects non-Pillow writers. |
| C3 two test gaps | Closed | Mutants `samples > entries` and "pre-check removed" are both killed by the new tests. |
| C4 equivalent Paeth mutant | No action needed | — |
| C5 strictness list missing from USAGE | Fixed | USAGE §8 lists the rejected features, including single-frame APNG, and the re-save remedy. |
| C6 shorter pixel-limit message | Unchanged | Cosmetic. |

## What I verified and how

Tests were run from `/Users/jonghyeonlee/git/particle-sim-dl1` with `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider`.
Scratch scripts, trees, the baseline snapshot and mutant copies live only in `/tmp/dl1-rb-review.WmxSmB`.
Pillow 12.3.0, Python 3.12.13, macOS arm64 (APFS).

| Focus | Method | Result |
|---|---|---|
| Suites | Full default suite twice; plan T3 command; plan T4 command | 446 passed, 1 skipped (78.7 s); 19 passed (36 s); 46 passed (43 s) |
| Scope | `git diff --stat 03bf3e3 01240f9` on `schema.py`, `model.py`, `checkpoint.py`, `search.py`, `evaluate.py`, `split.py`, `losses.py`, `requirements.lock`, and on `tests/`; `git diff --check` | Core files and lock have no diff; only new `png_*` test files; the 195 baseline tests are unmodified and pass; no whitespace errors |
| 1. Legacy bit-identity | `git archive 03bf3e3 \| tar -x` snapshot; the same script run in separate subprocesses with `PYTHONPATH` set to each tree, module path asserted | `normalize_unit`: 570 shape/grid cases (19 shapes from 1×1 to 1001×1203, 15 grids, float and bool input). `prepare_product` images, valid, connection masks, identity, endpoint features, collated tensors and `grid_check` minus `transform`: 280 framed products (10 shapes, 4 pitch/origin pairs, 7 grids, CNT and VIA) and 84 frame-less products. All byte-identical |
| 1. Versions, checkpoints | Code reading of every `grid_check` consumer; S20 test driver | `SCHEMA_VERSION`, `MODEL_INPUT_VERSION`, `CHECKPOINT_VERSION` unchanged; a checkpoint saved by baseline code loads in the candidate and gives a byte-equal embedding; changed-code resume is rejected. See N9 for the new key |
| 2. Export correctness | Own trees (four models, 1010×1030, modes `L`/`1`/`RGB`, filenames with `-`, `_` and zero padding) compared with the arrays I drew | Strict v1 manifest; 32 NPY all bool with frame shape; electrode masks equal my arrays (black = 1); global `cnt` equals all CNT foreground including two unresolved components; each connection mask equals its component; endpoints inside the mask and equal on both sides; `unit_size_um` = full extent; lineage/design equal the metadata file; `validate_product` and `python -m psdl1.data` pass; projected bytes 33,293,696 = actual bytes |
| 2. Status aggregation | Seven edge models in one export | No `cno` → `unavailable`, no `cnt` key; empty `cno` folder → `unavailable`; all-white CNT → `unavailable` with an all-zero `cnt`; CNT touching nothing → `unavailable` with foreground kept; all resolved with skipped layers → `complete`, model status `available`, `needs_confirmation` 1; two CNT planes → union correct (1,225 pixels) |
| 2. Prerequisites | CLI without metadata, without grid, with neither, with an unknown model key, duplicate keys, an extra `hotspot` key, one invalid model | Exit 2, diagnostic bundle, no `manifest.json`, no `assets/` in every case |
| 3. Publication | 17 destination forms; 11 injected faults (preview, assets, loader, validator, seal, verify, publish, `KeyboardInterrupt` twice, `OSError` in `np.save`); a destination created during the build | Existing file, empty folder, dangling symlink, `out == root` refused; nothing ever published on a fault; a raced destination and its content left untouched; source bytes and mtimes unchanged. Exceptions: B1, N2 |
| 3. Path containment | Model, electrode and layer names with `<script>`, quotes, `&`, leading `-`, leading `..`, 200-character names, Korean | All written files are named by SHA-256 identifiers under `assets/` and `preview/`; nothing outside the bundle; no symlinks |
| 4. Bundle verification | 19 tampered copies and one untouched copy | Detected: one flipped NPY bit, NPY removed, added, renamed, two swapped, manifest edit, manifest whitespace, report edit, preview HTML edit, preview PNG edit, added preview file, missing integrity file, asset replaced by a symlink to an identical file, and manifest or NPY edits with only the integrity file re-sealed. Not detected: empty directory (N5); fully consistent re-sealing (documented limit) |
| 5. Report semantics | Reading the report from each run | `electrical_verification` and `real_data_validation` are `not_performed`; `resource_inventory` is printed and the staged report written before `to_product`; vocabulary union and unique sets correct; duplicates and source warnings present; no text calls the skipped-layer or 4-neighbour rule user-confirmed |
| 5. Determinism | Four models written in two different file and model orders, different root locations (one with a space and Korean), relative versus absolute arguments, inspect grids in different order with a repeat | All 132 bundle files byte-identical, stdout events identical apart from the output path; a re-run is identical |
| 6. Inspect grids | CLI with none, two, zero and with `--candidate-grid` but no `--export` | No grids → empty evidence, no recommendation; supplied grids → `inspect_only_not_approved` each; transform record equals `frame_transform(...).to_record()` and the embedded `grid_check.transform`; endpoint features from `structured_batch` equal my hand values for a 1100×1300 frame at grid 128 (content 108×128, top pad 10) |
| 7. Preview | Asymmetric 1100×1500 frame with one contact; 400-component model; a ring component spanning the frame; an invalid model; hostile names | Endpoint pixel at crop (3,4) as expected; context offset (1248,143) and crosshair centre (67,68) as expected; overview marker within one pixel of (126.3, 789.9); polarity correct. No raw injected markup; one inline script; every `src`/`href` is a file in the bundle; no URL, `fetch` or import. 400 + 1 components and an invalid model: 2.9 s, 0.5 GB peak, 1,220 files, 4.1 MB. Crops carry `model_input: false` |
| 8. Integration | Own five-model bundle through `psdl1.data`, `structured_batch`, a fixture checkpoint and the `psdl1.search` CLI | Rankings returned; out-of-vocabulary candidates excluded with the missing role or layer named; out-of-vocabulary queries `unavailable` with the name; connectivity tensor `[1,1,2]` for partial, partial, complete; a bundle exported at grid 64 against a grid-128 checkpoint is refused without `--allow-grid-override`; `hotspot`, `CA`, `fab` rejected by the loader; no crop, tiling or geometric augmentation path added |
| 9. Tests | 57 single-change mutants in scratch copies through the unchanged tests | 45 killed, including global CNT from resolved only, uint8 NPY, missing prerequisites, skipped hash check, replacing rename, removed containment check, dropped invalid model, default inspect grid, floor rounding, half-pixel and axis errors in the transform, core crop, coverage aggregation, electrical status, endpoint feature change. 1 equivalent. 11 real gaps (N4) |
| 9. Expected values | Reading of the six new test files | Literal pixels, coordinates, byte counts and transform values; production helpers are used only alongside literals |
| 10. Documents | Read README, USAGE, data_contract, training and validation diffs against behaviour; ran every documented command form | Commands run. 0.1 µm/pixel, frame-extent meaning of `unit_size_um`, no crop, optional Pillow and wheel constraint, vocabulary limits, the exact "complete means extraction coverage only; electrical verification not performed" sentence, and the 5–10 product pilot as integration/connection inspection only are all present. No real-data, retrieval-quality or electrical claim. `validation.md` only gains a section. The one inaccurate statement is the outside-the-tree guarantee (B1) |
| Worker evidence | `--verify-bundle` on `/tmp/dl1-png-preview-evidence/t4-inspection`; viewed `t4-visual-check.png` | Verified, 23 files; overview and context panels are consistent with the fixture |
| Repository state | `git status --short`, `git rev-parse HEAD` after all runs | `particle-sim-dl1` clean, HEAD `01240f9f61541fa328976a5e543c60276cc1fff7`; see N12 |

## What I could not verify

- Real source PNGs, real frame sizes and real CNT density; all inputs here are synthetic.
- Linux and Windows: the `renameat2` and Windows rename branches of `_publish_noreplace`, old glibc (N1), and whether B1 occurs on Windows. Only macOS arm64 on APFS was run.
- Any Pillow other than 12.3.0; the version warning was exercised only through the existing monkeypatched test.
- Peak memory and runtime above 2000×2000 frames or above 400 components, and export with hundreds of full-frame connection masks.
- Offline `pip install .[png]` against a closed-network wheelhouse, and the `psdl1-import-png` console script; no install was attempted.
- The opt-in 40-epoch training smoke (`--run-training`), which stays skipped.
- A baseline mutant for S20 through the test file itself (it resolves the candidate from the repository path); I relied on my own baseline comparison and on mutant m48 through the other tests.
- Physical correctness of the nearest-overlap, skipped-layer and 4-neighbour rules, retrieval quality, and electrical truth. These remain open user questions.
