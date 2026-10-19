VERDICT: PASS-WITH-NOTES

# REVIEW2: independent review of FIX1 (High-risk: destructive cleanup path)

Reviewer: fresh Claude Opus 5.5 / high, read-only. Task task_391c198646c8.
Candidate: uncommitted `git diff -- src tests` on branch rewrite/line-pixel-rate at HEAD c6c3081. The sha256 of the diff is
e9b36af8a9209678cce917b170aaecf5fe1d7e80a9ae53d00db1ca3d61f5a4cd. I checked it at the start and the end of the review, and it matches /tmp/orca-specs/fix1.diff.
Baseline for "fails on 801c8da": the c6c3081 tree without the diff. `git diff 801c8da c6c3081` touches only the electrode_png adapter, the
import-electrode-assets CLI branch and test_retired_paths. final_report.py, local_delta_report.py and `_remove_reports` are identical to 801c8da.

I did not write anything in the repo. Copies of the base and candidate trees are in /tmp/rv2/{base,cand}. My probe tests are
/tmp/rv2/cand/tests/test_rv_probe.py and test_rv_scale.py, and the outputs are under /tmp/rv2/eq.

## Blocking findings

None.

## Check 1: are all 8 findings fixed, and do the regression tests fail on 801c8da?

I copied the new tests onto the baseline tree and ran them (`/tmp/rv2/base`). 12 of the 14 new tests fail there:

| Item | Test(s) | Result on 801c8da | Result on candidate |
|---|---|---|---|
| 1 cleanup | test_item1_compare_error_keeps_unrelated_report_files, test_item1_report_error_keeps_unrelated_files_and_removes_only_owned_report | FAIL (report_notes.json deleted) | pass |
| 2 escaping | test_item2_hostile_provenance_count_is_rejected_not_rendered, test_item2_hostile_geometry_values_are_rejected | FAIL | pass |
| 2 (guard) | test_item2_hostile_strings_in_other_metadata_never_reach_html_raw | passes on base (complementary guard, not the regression) | pass |
| 3 contexts | test_item3_two_contexts_on_one_layer_are_never_merged, test_item3_duplicate_context_rows_are_an_error_not_an_overwrite | FAIL (KeyError on the context label / no exit 2) | pass |
| 4 all-blocked | test_item4_all_blocked_windows_result_gives_report_with_exit3 | FAIL (diameter_not_available) | pass |
| 4 (preserve) | test_item4_diameter_not_available_still_exit2_when_rows_exist | passes on base (preservation test) | pass |
| 5 crops | test_item5_missing_crops_make_b3_partial_with_reasons, test_item5_null_file_with_note_versus_nonexistent_referenced_file | FAIL | pass |
| 6 overview | tests/test_local_delta_report.py::test_overview_masks_electrodes_by_valid_before_block_reduction | FAIL | pass |
| 7 scaling | test_item7_b3_html_grows_linearly_with_pair_count | FAIL | pass |
| 8 disclosure | test_item8_overview_source_pair_and_limit_are_disclosed | FAIL | pass |

I also reproduced the original failure scenarios directly against the candidate:
- **Item 1.** Each of compare, electrode-map, validate-data, export-review-rois and compare-windows was run with a config that does not exist; all exit 2. The files report_notes.json, report_backup.csv, report_customer.png, report.html.bak, reports.json, `report` and my_report.html survive in each case, and so does an existing report.html.
- **Item 2.** A hostile n_ready or n_blocked (a string, an object, -1, true or 1.0) gives exit 2 with `invalid_count_value` and no report.html. Hostile centre, origin, pitch or shape values give exit 2 with `invalid_geometry_value`.
  - I audited every remaining f-string interpolation into HTML/SVG in final_report.py. They all use escape, `_cell`, `_p` or `_fmt` inside escape, or they are internal ints, or numbers validated by `_finite`, `_bounds4` or `_frame_geometry`. The anchor ids are `pair-{i}`, which is safe.
- **Item 3.** Two contexts on M2 now produce two A1 KPIs, two B1 groups and two B2 groups with the same labels.
  - I added a probe where the second context exists only on the reference side (target rows removed). It shows `a` taken from the source and `b=None` in A1 and B2, and Δ taken from the delta row. Nothing is borrowed from the other context.
- **Item 4.** A real compare-windows output where every pair is blocked (exit 3, no rows) now gives a report with exit 3. A2 and B7 list P1 as blocked with reason `position_outside_source_frame_a`.
- **Item 5.** Two pairs, where P1 has endpoints and P2 has none, give B3 partial with exit 3. The missing crops are listed as P2/a/layout and P2/b/layout.
- **Item 6.** For a layout cell outside `valid`: gray at step 1. For a wholly invalid block: gray. For a mixed block, the electrode is shown only if it sits on a valid cell. The convention is documented in the docstring (local_delta_report.py:110).
- **Item 7.** With 100 and 300 non-overlapping pairs and no PNGs, the report is 373,146 B with 816 `<rect>` elements, and 968,618 B with 2,416. That is linear at 8 rects per pair. 801c8da measured 3.2 MB and 27 MB.
- **Item 8.** The caption names the source pair and states the limitation. When the source is absent it falls back to "—".

## Check 2: the cleanup change in cli.py (High-risk)

Code: cli.py:109-119, called from the error path at cli.py:191-214.

What I verified:
- **Non-report commands use exactly the 1ac6441 prefix list.** That is the state before the report feature was added, so the 801c8da regression (the generic `report` prefix) is removed.
  - The probe parametrised over the five numeric commands passes. Report files and user report_* files survive, and stale owned outputs (comparison.json, local_delta.json) are still removed. error.json is written.
- **report on exit 2 removes only report.html and report.json.**
  - Probe test_report_exit2_cleans_only_owned: a stale report.html and report.json are removed. The user files report_* survive. Numeric outputs that happen to sit in the report `--out` also survive (comparison.json, local_delta.json, local_delta_thumb_x.png, window_sensitivity.csv), because report never owned them. execution.json has exit_code 2 and error.json is written.
- **`--out` equal to an input dir** (compare, win or rois, each a copy):
  - Exit 2, and the folder content is byte-identical before and after, including a user-planted report.html.
  - The same holds when `--out` is a symlink alias of the compare dir.
  - Two independent guards cover this: run_report raises `output_is_input` before any write (final_report.py:1069-1071), and the CLI sets `output=None` so no cleanup and no marker happen (cli.py:200-203).
- **The success path (exit 0/3)** overwrites report.html and report.json, writes execution.json and removes a stale error.json. This is unchanged.
- **`unlink(missing_ok=True)` on a symlink** removes the link, not its target. On a directory it raises OSError, which the existing `except OSError: pass` swallows. No collateral deletion is possible.

Conclusion: no command deletes user report* files any more, and owned stale outputs are still cleaned for every command. See notes N1 and N2 for the residual exposure, both pre-existing.

## Check 3: are the new behaviour decisions sound?

- **Invalid count or geometry → exit 2.** Sound. The producer always writes int counts (local_delta.py:420 `len(...)`) and numeric geometry, so a legitimate output never triggers this. The demo passes.
- **Duplicate rows for one context → exit 2 (`_panel_values`, final_report.py:476).** A legitimate producer cannot trigger it:
  - Each side has a single declared product (pipeline.py:120-122, `single_declared_product_required`).
  - `compare_rates` and `compare_fractions` raise `ambiguous_comparison_layer` when one product has two LayerKeys with the same ComparisonKey (linepixel.py:788, 912).
  - So each panel row in summary and pieces is unique per (side, ComparisonKey, class/piece). Unit-domain and full_mask rows are filtered out by `domain == 'panel'`.
- **A referenced crop file that is missing → exit 2; a null file → exit 3.** Sound and consistent with the contract's "필수 파일 없음" (required file missing) exit 2 (docs/usage.md:122).
  - The exit 2 for a missing referenced file is behaviour 801c8da already had via `inputs.png`; FIX1 only added the comment.
  - A null file, or a crop record that is absent while thumbs exist, becomes a listed B3 partial with exit 3.
  - Only ready pairs get cards, so blocked pairs without crops do not make B3 partial.

## Check 4: does context keying in A1/B2 match B1 and the producer?

- `CONTEXT_FIELDS` plus the layer column is exactly `ComparisonKey` (linepixel.py:570-576).
- Side rows carry `asdict(LayerKey)` as their context (report.py:84-99). `_context_key` projects it onto the same tuple, so the extra fields (product_id, frame_id, unit_id, design_version) are dropped. My probe test_side_context_maps_to_delta_key passes.
- Delta rows carry `asdict(ComparisonKey)`, and the fraction `comparison_key` is the same.
- All three sections use one shared label map built from the union of keys.
- Labels are readable:
  - When a layer has a single context, the label is the layer.
  - Otherwise the label is `layer · process_step/exposure`, and any field that varies is appended. With a difference in source_id only, the label is `M2 · s/srcA/e` versus `M2 · s/srcB/e`.
  - Labels are escaped in h3, KPI titles and chart titles.

## Check 5: no regression

- **Full suite in the repo:** `uv run pytest -p no:cacheprovider` gave **930 passed, 3 skipped** (the log is /tmp/rv2/full_suite.log). The handoff test did not fail in this run.
- **compare-windows outputs, base renderer versus candidate renderer**, demo data, windows 1 and 2 µm:
  - These files are byte-identical: local_delta.html, local_delta.json, local_delta_rows.csv, all 8 thumb PNGs, both overview PNGs (the demo frames are all valid), local_delta_thumbs.json and the window_sensitivity files.
  - execution.json is identical apart from the output path.
  - The overview PNG is only a sidecar and is not embedded in local_delta.html, so T1 byte-identity of local_delta.html holds even for frames with invalid cells.
- **report.json, base versus candidate, same inputs:** same exit code (3), same reasons, same section statuses. The differences are additions only:
  - A1 kpi gains `a` and `b` on the panel KPIs.
  - B2 gains `group` and `context`.
  - B3 gains `missing_crops`.
  - All values common to both are equal.
- The repo `git status` is unchanged and the diff hash is unchanged after the review.

## Non-blocking notes (ranked)

**N1 (advisory; pre-existing since before 801c8da, out of FIX1 scope).** Non-report commands still delete user files that share an owned prefix (cli.py:115-118).
- Failure scenario: `validate-data --config missing.yaml --out D` deletes D/local_delta_notes.csv or D/comparison_mine.json. The cleanup is not command-specific, so validate-data also removes local_delta* and comparison* files.
- This matches 1ac6441 exactly. The new test deliberately excludes comparison_mine.json for compare.
- The same prefix pattern exists on success paths: local_delta.py:725 and pipeline.py:315/340.
- Suggested follow-up: give each command its own list of owned outputs (exact names, or the prefix plus a known manifest).

**N2 (advisory; pre-existing).** A report that fails with an exception outside the caught tuple exits 1 with a traceback and leaves a stale report.html and report.json.
- Verified: set `local_delta.json.provenance = [1, 2]`. `_load` (final_report.py:384) hits AttributeError, the exit code is 1, and the stale report.html remains.
- Suggested fix: validate that `provenance` is a dict (raise ReportInputError otherwise), or have the CLI clean report outputs on any exception for the report command.

**N3 (consistency).** `_fraction_groups` (final_report.py:463) still silently overwrites a duplicate delta fraction row (same context, diameter and metric), unlike `_panel_values`.
- A legitimate producer cannot emit such a row, because `compare_fractions` raises `ambiguous_comparison_layer`.
- Suggested fix: apply the same `duplicate_context` check there.

**N4 (docs).** docs/usage.md:122 does not list the new exit-2 reason codes `invalid_count_value`, `invalid_geometry_value` and `duplicate_context`. Suggest adding them in a docs change; FIX1 did not own usage.md.

**N5 (operational).** A crop file that is referenced but missing gives exit 2 with no report at all. For example, the windows JSON is copied to the closed network without the PNGs.
- This is consistent with the contract and with 801c8da.
- If the closed-network transfer often drops PNGs, consider degrading to a B3 partial instead.
