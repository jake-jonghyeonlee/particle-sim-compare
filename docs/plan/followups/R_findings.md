# F-R independent review — F1 (code, HIGH) + F2 (docs). Reviewer: Claude Opus 5.5 / high. Read-only.
Candidate: working tree vs HEAD c7a2829 (feat/template-export), 2026-10-23.

## Verdict: FAIL (one blocking finding, B1). Everything else passes; the advisories below are optional.
B1 is fixed either by a small code change plus a test, or by correcting four doc sentences.

## Blocking

**B1 — RB-A3 (compare) only partly fixed, and the docs promise more than the code does.**
`src/simcompare/pipeline.py:607-618`: some errors are raised *before* the real-branch `try` starts (`pipeline.py:620`), so they are
never marked `keeps_previous_outputs`. These are:
- `_real_source` → `load_real_manifest` validation errors (`real_manifest_hash_mismatch`, `real_manifest_version_mismatch`, the new
  EC-A4 `invalid_real_expected_case_coverage_status`, `invalid_real_expected_case_source`)
- `mixed_real_and_numeric_source_formats`
- a missing or unreadable manifest, which falls through to the numeric path and fails there with `missing_or_escaping_path`

For all of these, `cli.py:297-298` still calls `_remove_reports`, which deletes comparison.json, comparison.csv and comparison_masks.zip.

Reproduced with a scratch test outside the repo (`/tmp/fr_repro/test_fr_repro.py`, using the fixtures from
tests/test_compare_publication_cli.py):
1. Reference manifest tampered (status flipped) → exit 2 `real_manifest_hash_mismatch`; comparison.json, comparison.csv and
   comparison_masks.zip are deleted (only error.json, execution.json and user files remain).
2. `data/imported/target` moved away (this is exactly the restart step at usage.md:48), then stage 2 run → exit 2
   `missing_or_escaping_path`; all three files are deleted.

The four doc places that claim otherwise:
- usage.md:137 says every error keeps the previous result except "설정 파일 자체를 읽지 못하는 경우와 합성 compare".
- usage_reference.md:229, :296 and :355 say the same.
- F1_report.md item 1 lists only "an unparseable config".

Impact: the three files are deleted together, so there is no torn state, and they can be regenerated (about 10 minutes at ETX scale).
But this misses the F1 contract wording ("errors before a publication transaction opens must not delete the previous good
generation") and the F2 accuracy requirement.

Fix (pick one):
- (a) Mark `keeps_previous_outputs` on every exception from `run_numeric_compare` when `cfg.get('mode') == 'real'`, for example by
  moving the `_real_source` calls inside a try. Add a regression test (moved import folder, tampered manifest).
- (b) Correct the four doc sentences so they name the error classes that still delete.

Note: `invalid_compare_options` is raised in `load_numeric_config`. In that case cli knows no output folder, so nothing is deleted
and no error.json is written. This matches the docs' "cannot read the config" exception.

## Advisories (non-blocking)

- **A1 (RB-A5, low)** `pipeline.py:1095-1103`: admission now runs with `reuse=True` whenever an artefact of plausible size exists.
  If that artefact then turns out to be unusable (for example stale after a re-import), and the reuse estimate exceeds the limit
  while the recompute estimate fits, the run is now refused; before, it ran.
  - This needs reuse held > recompute held: `groups×(4·cells/8 + 8 KiB)` against `groups×7·cells/8 + output + records×2 KiB`.
  - That only happens for tiny frames (about 22k cells or fewer), so it cannot happen at ETX scale.
  - So the F1_report claim "no change to estimates/outputs/error codes" holds except for this corner. Accept, or document it.
- **A2 (RB-A10, low)** `publication.py:52-61` (Windows branch):
  - `ctypes.windll.kernel32.GetLastError()` can be overwritten between calls. The failure is on the safe side (any value other
    than 87 means alive, so the file is kept and only leaks).
  - Prefer `ctypes.WinDLL('kernel32', use_last_error=True)` with `ctypes.get_last_error()`, and an explicit
    `OpenProcess.restype` (HANDLE).
  - The Windows branch is not exercised by any test run here (macOS only).
  - If a pid is reused, the temp is kept (a leak, never a deletion). A live run on the same host is never swept.
  - A live run on *another host or PID namespace* writing into the same shared folder could have its temps swept. That only
    happens with concurrent runs in one folder, which usage.md:137 declares unsupported. Accept.
- **A3 (docs, low)** usage.md:60 gives the stage-2 `compare` peak as 3.3~3.7 GiB. The latest default-limits run measured
  3,923 MiB, about 3.8 GiB (docs/plan/scale_compare/T5_benchmark.md:104, with an overlapping py3.8 run). Suggest "3.3~3.8 GiB".
- **A4 (docs, low)** usage.md:34 says every `--out` must be a new path. That contradicts usage.md:48 (`data/local` can be reused
  because stage 6 replaces it as one generation) and stage 2's `output_dir` (also replaced as a generation). Limit the sentence
  to stages 1/3/4/5. Also, usage.md:30 says BASE_DIR governs "JOBS와 LOG_FILE"; the runner comment says WORK_DIR too. WORK_DIR was
  never documented in the old usage.md either, so nothing was lost.
- **A5 (RB-A12, note)** `electrode_map.py:228-233`: the regions of one layer share one frozen snapshot. Nested dicts
  (`source_coverage_summary`) and the identity dicts inside the tuples are still mutable and shared among that layer's regions,
  but no longer with the LayerMap. This is within the spec.
  - The oracle tolerance (`tests/test_stage46_equivalence.py:200-203`) works in one direction only (production tuple vs frozen
    list) and only at leaves named `.coverage['<key>']`.
  - LayerMap.coverage lists are still lists, and int-indexed or other paths do not match, so equivalence is as strict as before
    everywhere else.
- **A6 (EC-A2, note)** `real_hotspot.py:246-250`: the resolved-path equality and duplicate checks are case-sensitive on macOS
  (`Path.resolve` does not canonicalize case there; a strict resolve on Windows does). Minor.

## Verified as passing
- **RB-A4:** `_publish` journals the previous zip as `existed=True`, hard-links it to `.prev`, then unlinks the final file. A
  rollback restores it from `.prev`; a roll-forward deletes `.prev` and leaves no zip, which is correct. The journal names stay a
  subset of `_NAMES`, so any version can recover. Both the dropped-artefact case and the interrupted swap are tested.
- **RB-A5:** the stat-only candidate check runs before the guard, and the artefact is read only afterwards. An unusable artefact
  gets a second `reuse=False` admission before `compute_areas`.
- **RB-A7 / RB-A8:** the admission terms are correct (`dataset_held_bytes` is the same expression as stage-6 `dataset_bytes`; the
  match files are priced by on-disk size). They affect admission only, never output bytes.
- **RB-A10:** the regex matches exactly the `_open_temp` names (`.<name>.<pid>.<8 hex>.tmp`); this process's pid and live pids
  are excluded. In the SetPublication sweep, its own staging directory is created after the sweep. The in-flight guard in
  `_remove_reports` protects any set with a `.publishing` or `.committed` file.
- **RB-A12:** fixed. roi_export dedupes by the ids of the per-layer shared tuples and names each entry by a content hash, so the
  bytes are identical.
- **EC-A2 / EC-A4:** correct. Every manifest writer since bd45eda wrote a status consistent with its inventory, so legacy
  manifests still load.
- **compare-windows:** never removes managed reports on any error. The wedge test passes.
- **Bundles:** I rebuilt `scripts/build_single_file.py` and `scripts/build_etx_stage_runners.py` from the working tree into /tmp.
  All 7 files are byte-identical to `bundles/`.
- **Docs:**
  - No broken links or anchors (docs/*.md outside plan/, README.md, configs/etx/README.md).
  - Independent token check of the old usage.md (233 `--options` and snake_case codes): 0 missing from usage.md + usage_reference.md.
  - Runner JOBS, folder names, and the defaults (8 GiB, 5e9, 6e9, 1e6, 24e6, top_buckets 20, map 5 µm) match the code.
  - Error codes (`output_dir_exists`, `electrode_output_exists`, `real_hotspot_output_exists`) exist.
  - The usage.md assertion at tests/test_scale_guards.py:442 still holds.
  - The shape is right: usage.md is 153 lines (old 385) with a clean ETX / Windows / common split.
