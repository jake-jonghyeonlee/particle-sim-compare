# F1 report — review advisories fixed (base c7a2829, uncommitted)

Notes for F2 (docs/usage.md rewrite). docs/usage.md itself was not edited.

## User-facing behaviour changes

1. **compare / compare-windows keep the previous generation on every error (RB-A3).**
   - `compare-windows`: any error (invalid window sizes, correspondence/size/admission errors before the publication
     opens, config errors) writes only `error.json` / `execution.json`; `local_delta*` / `window_sensitivity*` are no
     longer deleted. This replaces usage.md:94's "그 전의 입력·설정·admission 오류는 … 관리 리포트를 지운다".
   - `compare` (cfg `mode: real`): EVERY exception from `run_numeric_compare` (manifest validation, mixed source formats, a moved
     import folder → `missing_or_escaping_path`, dataset/pair-table/admission errors, before `begin()` as well) keeps
     `comparison.json`, `comparison.csv`, `comparison_masks.zip` (F3/B1). Only error.json/execution.json are written.
     Not covered: a config file that cannot even be parsed, and the synthetic (`mode: synthetic`) compare, still remove the old
     `comparison*` files (documented "R2" rule, test_r2_error_cleanup_and_success_clears_marker).
   - The cleanup (`_remove_reports`) now also removes `comparison_masks.zip` (exact name) together with comparison.json/csv, so no
     orphan artefact remains, and it never deletes managed files while a publication control file
     (`.comparison.publishing|committed`, `.local_delta.publishing|committed`) exists; this prevents the
     `local_delta_recovery_unreadable` wedge after a process killed mid-publication.
2. **One generation when the mask artefact is dropped (RB-A4).** If `comparison_masks.zip` cannot be built (non-I/O error, message
   "comparison_masks.zip not written …"), the previous `comparison_masks.zip` is journalled and removed in the same transaction
   as the json/csv swap (rolled back with it on failure). Old text "the old artefact stays untouched" no longer holds.
3. **Stage 6 reads `comparison_masks.zip` after admission (RB-A5).** The admission (with the reuse estimate, which already prices the
   packed masks) runs on the file size only; the artefact is read afterwards. If it turns out unusable the recompute
   estimate is admitted again before `compute_areas`. No change to estimates, outputs or error codes.
4. **Stage-4 second product (RB-A7):** `candidates` counts the first product's still-alive configured dataset
   (`dataset_held_bytes`: 5 KiB per case/diameter record + 2 MiB) in the second product's admission (`reserved`). Slightly
   stricter for the second product only; the usual restart advice (smaller scope / more memory) is unchanged.
5. **Stage-5 gate (RB-A8):** `confirm-pairs` prices the `--match` DL2 JSON files (on-disk sizes) together with the four exports in
   the pre-read gate (same multiplier). Message text unchanged ("stage-5 export read: <bytes> bytes in <n> files …"; n now includes
   the match files).
6. **Stale temps (RB-A10):** `compare`'s `begin()` removes `.comparison.json|.comparison.csv|.comparison_masks.zip.<pid>.<hex>.tmp` of
   *dead* runs (never this process, never a live pid, never other names). `compare-windows`'s `begin()` already removed the
   `.local_delta.staging.<pid>.<hex>/` scratch; it now skips one whose pid is alive (and not this process). Pid liveness
   uses `os.kill(pid, 0)` on POSIX and `OpenProcess/GetExitCodeProcess` on Windows (never `os.kill` there); unknown = alive.
   The "killed compare leaves a 50–72 MB temp" note (RB-A10) can be dropped from usage.md.
7. **`--expected-cases-from-roots` validation (EC-A2):** rejects an empty list (`expected_case_roots_empty`), a root equal to `--root`
   after `resolve()` (`expected_case_root_is_import_root`) and duplicates after resolve (`duplicate_expected_case_root`), before anything
   is written. A non-existing root still raises `FileNotFoundError`.
8. **Manifest cross-check (EC-A4):** `load_real_manifest` raises `invalid_real_expected_case_coverage_status` unless
   `expected_case_coverage_status` ∈ {confirmed, unconfirmed} and `confirmed ⇔ expected_cases present`; manifests that record
   `expected_case_source` additionally need `source present ⇔ expected_cases present`. Manifests without the
   `expected_case_source` key (legacy) and without a status key load as before.

No change: numeric outputs, published bytes of comparison.json/csv/local_delta*, error codes of existing paths.

## Per item: change → regression test (all fail before the fix)

| Item | Change | Test |
| --- | --- | --- |
| RB-A3 compare | `pipeline.run_numeric_compare`: `keeps_previous_outputs` on every error of the real branch; `cli._remove_reports` removes `comparison_masks.zip`, skips in-flight sets | `test_compare_publication_cli.py::test_input_errors_before_the_publication_keep_the_previous_generation_too` (was `…still_remove…`, inverted), `test_f1_followups.py::test_cleanup_removes_the_orphan_masks_zip_but_not_user_files_or_in_flight_sets` |
| RB-A3 compare-windows | `cli.main`: compare-windows never removes managed reports | `test_f1_followups.py::test_compare_windows_error_before_the_publication_keeps_the_previous_generation` |
| RB-A3 wedge | in-flight guard in `_remove_reports` + keep rule | `test_f1_followups.py::test_pre_begin_failure_after_a_killed_publication_does_not_wedge_the_next_run` |
| RB-A4 | `_ComparisonStream._publish` journals + removes the previous zip when masks were dropped | `test_compare_publication_cli.py::test_a_masks_writer_failure_leaves_one_generation_without_the_previous_artefact`, `::test_a_dropped_artefact_is_restored_when_the_swap_is_interrupted`; `test_stage6_scale.py::test_an_artefact_that_cannot_be_built_never_costs_the_compare` (updated: zip removed) |
| RB-A5 | `pipeline`: `_stage6_artefact_candidate` (stat only) → guard → `_load_stage6_artefact` | `test_f1_followups.py::test_the_artefact_is_not_read_when_the_stage6_admission_refuses` |
| RB-A7 | `real_hotspot.dataset_held_bytes`; `candidates._run` adds it to `reserved` | `test_stage4_streaming.py::test_second_product_admission_counts_the_first_products_configured_dataset` |
| RB-A8 | `confirm_pairs._match_sizes` added to the stage-5 gate | `test_stage4_streaming.py::test_stage5_gate_also_prices_the_dl2_match_files` |
| RB-A10 | `_ComparisonStream._sweep_stale_temps`, `publication.pid_alive/owner_is_gone`, `SetPublication._owner_gone` | `test_f1_followups.py::test_begin_of_the_compare_transaction_sweeps_temps_of_dead_runs_only`, `::test_begin_of_the_set_publication_keeps_the_scratch_of_a_live_run_and_sweeps_a_dead_one` |
| RB-A12 | `electrode_map._frozen_coverage`: one detached copy per layer, lists → tuples, shared by the layer's regions | `test_electrode_map.py::test_region_coverage_does_not_alias_the_layer_coverage` |
| EC-A2 | `import_hotspots` validates the extra roots | `test_expected_case_union.py::test_extra_roots_reject_empty_self_and_duplicates` |
| EC-A4 | `load_real_manifest` cross-check | `test_expected_case_union.py::test_manifest_status_is_cross_checked_with_the_inventory_and_its_source` |

## Points the reviewer should look at

- **Oracle harness relaxation (RB-A12).** The frozen 7bcb07a `ElectrodeRegion.coverage` holds lists; production now holds tuples
  (serialised identically; `roi_export` dedupes coverage by list identity, which still works because the tuples are shared per
  layer). `tests/test_stage46_equivalence.py::deep_diff` compares types strictly, so it now tolerates exactly
  `tuple` (production) vs `list` (frozen) at `….coverage['<name>']`. The frozen files in `tests/reference_stage46` and their
  digests are untouched; the byte-level CLI equivalence suites pass unchanged. If that relaxation is not acceptable, the fallback
  is to share per-layer *list* snapshots (detached from the LayerMap but not immutable).
- Regions of one layer still share the (tuple) snapshot and the dict elements inside it; only the LayerMap coverage and the list
  containers are protected.
- `compare` with an unparseable config cannot be classified (mode unknown) and still uses the old removal rule.
- A1 (accepted, documented in usage_reference.md): admission may run with `reuse=True` when a stage-2 artefact exists; if it then
  proves unusable and reuse estimate > recompute estimate (tiny frames, ~22k cells or fewer), the run can be refused.


## Correction after F3/F4 (2026-10-23)
Items above that say compare (real) keeps the previous generation on EVERY error are superseded: since F4, errors before any output is written and inside the publication transaction keep it; failures in the two non-transactional real writers (_write_inspection without condition_assignment; write_numeric_report for numeric_v2 in mode real — currently unreachable, load stops with real_source_format_unconfirmed) remove the managed reports. Open (pre-existing, F-R3 A4): a successful inspection run leaves an older comparison_masks.zip in place.
