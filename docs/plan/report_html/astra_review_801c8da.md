VERDICT: FAIL

Independent read-only review of commit `801c8da` against parent `1ac6441`, restricted to `src`, `tests`, `scripts`, and `docs/usage.md`; contracts read from the candidate's `docs/plan/report_html_plan_v1.md` and T1/T2 specifications. All line numbers below refer to `801c8da`. No repository files or Git state were changed. Test execution and reproductions used an archive in `/tmp/astra_review_801c8da`.

The review task completed successfully; the implementation does not pass acceptance. Six correctness/safety findings require fixes. A separate scaling finding and an overview-selection limitation are noted afterward.

## Findings requiring fixes, highest priority first

1. **[P1] An error in any existing command can now delete unrelated `report*` user files.**
   Location: `src/simcompare/cli.py:95` (deletion at lines 96–98; shared error-handler call at line 174).

   Adding the generic `report` prefix expands cleanup for every command, including `compare`, `validate-data`, and `import-hotspots`. Reproduction: put `report_notes.json` containing user data in an output directory, then run `compare --config /tmp/nonexistent.yaml --out <that-directory>`. The command exits 2 and deletes `report_notes.json`. The same applies to `report_backup.csv`, `report_customer.png`, and other matching extensions. Before this commit these names were outside this cleanup prefix. The report command's same-input-directory guard does not protect other commands or unrelated files in an output directory.

   Suggested fix: remove `report` from the wildcard prefixes; clean only the exact owned `report.html` and `report.json` paths when appropriate, preferably using command-specific output ownership. Add regression coverage asserting unrelated `report*` files survive both an old-command error and a report error while a stale owned report is removed.

2. **[P1] Unescaped provenance counts allow executable HTML in the generated report.**
   Location: `src/simcompare/final_report.py:908`.

   `n_ready` and `n_blocked` are interpolated directly into `_Html`, bypassing `_cell` and `html.escape`. `_load` checks the schema version, but does not validate these fields' types. Setting `local_delta.json.provenance.n_ready` to `<script>alert(1)</script>` produces exit 0, and `report.html` contains that exact active script element. This violates the explicit escaping requirement; the current escaping test only covers pair IDs. Numeric-looking metadata must not be implicitly trusted just because it came from JSON. Other direct SVG geometry interpolation should receive the same validation audit.

   Suggested fix: validate counts as nonnegative integers and escape any value placed in an HTML fragment; reject invalid geometry types before interpolating SVG attributes. Add hostile provenance and attribute-content tests alongside the pair-ID test.

3. **[P1] A1 and B2 silently discard distinct process/exposure contexts sharing a layer.**
   Locations: `src/simcompare/final_report.py:479`–480 and `:523`–528.

   Summary lookups use only `(layer, class)` or `(side, layer, class)`, and failure-piece lookups use only `(side, layer, piece)`. The source tables carry a `context`; two legitimate contexts on M2 therefore overwrite each other instead of producing separately identified values. The underlying producer explicitly supports these contexts (`linepixel.py:551`–576 and `report.py:84`–101, 153–167). In a fixture with two M2 process contexts, appending a second context whose values are 999 leaves just one M2 line KPI with delta 999 and one B2 group; the first context disappears. Independently chosen final a/b rows can also be associated with the wrong delta context when sides have different coverage. B1 preserves its comparison key, so the sections can disagree about how many groups exist.

   Suggested fix: normalize the CSV `context` into the full comparison identity, retain product-side distinctions where necessary, and group/label A1 and B2 consistently with B1. Do not silently sum independent contexts. Test two process/exposure contexts on the same layer and unequal side coverage, checking displayed a, b, and delta together.

4. **[P2] A valid all-blocked windows result is rejected as an input error instead of reported as unavailable.**
   Location: `src/simcompare/final_report.py:378`–382.

   The representative diameter is required to occur in CSV rows before the report processes pair statuses. A real `compare-windows` run with its sole correspondence marked `blocked` legitimately produces exit 3, a valid `local_delta_v1` result, and an empty rows table. Feeding those outputs to `report --rank-window-um 1` returns exit 2 with `diameter_not_available` and no report. The user loses the blocked-pair list and reasons that A2/B7 are required to show. This is not a malformed-input fixture; it was reproduced through the candidate CLI producer.

   Suggested fix: distinguish an empty legitimate unavailable/all-blocked result from an unsupported requested diameter. Allow blocked-only results through classification and produce a report with exit 3. Keep diameter rejection for genuinely incompatible nonempty input. Add an end-to-end all-blocked test.

5. **[P2] Missing crops are displayed but B3 and the command still claim completion.**
   Location: `src/simcompare/final_report.py:754`–756; missing-crop branch at `:726`–731.

   B3's partial predicate checks only whether the thumbs manifest and overview images exist. It never checks each required crop. A normal producer case with P1 having endpoints and P2 omitting endpoints yields valid overviews from P1 and null layout crops for P2; `compare-windows` succeeds, and `report` then exits 0 with B3 `available` and an empty reasons list, despite displaying missing layout panes. Separately setting one manifest crop to `file: null` reproduces the same behavior. This conflicts with the documented exit-3 handling for crop omission and hides missing material from automation and section warnings.

   Suggested fix: accumulate missing crop records while building panes and include them in B3's partial status/reasons, preserving the existing explanatory placeholders. Distinguish a known null/unavailable crop from an inconsistent reference to a nonexistent file. Test mixed complete/incomplete pairs with valid overviews.

6. **[P2] Overview painting overwrites the required gray invalid domain.**
   Location: `src/simcompare/local_delta_report.py:114`–122.

   The function paints invalid blocks gray, then paints the entire layout mask over them without intersecting with `frame.valid`. Original layout assets are allowed to contain shapes outside the observed domain; the asset validator does not require layouts to be zero there. A 2×2 frame with `valid[0,0] = False` and a layout mask set at `[0,0]` produces electrode blue `[40,90,220]` instead of required gray `[200,200,200]`. At larger downsample steps, invalid-only layout occupancy can also contaminate a mixed block's displayed electrode color.

   Suggested fix: mask electrode occupancy by the original valid domain before block reduction, and apply invalid gray after painting. Define the mixed valid/invalid block convention explicitly. Add tests with masks outside valid, wholly invalid blocks, and mixed blocks; the current size/thin-line test has an all-valid frame.

## Additional findings and limitations

7. **[P2, scaling note] Every locator repeats every pair rectangle, producing quadratic HTML growth even without overlaps.**
   Location: `src/simcompare/final_report.py:732`–735 (and the non-highlight branch at `:287`–290).

   Each pair card emits two locator SVGs containing all pairs in the frame. With nonoverlapping windows, no PNGs, and top-N still 10, measured HTML sizes were:

   | Ready pairs | HTML bytes | `<rect>` elements |
   | --- | ---: | ---: |
   | 100 | 3,225,529 | 20,616 |
   | 300 | 26,955,949 | 181,816 |

   The growth is approximately quadratic; top-N does not bound B3. This creates unnecessarily large transferable artifacts and a large SVG DOM before crop images are added. There is no numerical file-size limit in the contract, so this is separated from the six acceptance blockers above.

   Suggested fix: define the shared neutral window overlay once per frame and reference it with SVG `<use>`, then draw only the highlighted pair in each locator, or show only that pair in its small locator. Add a size/element-count scaling check with nonoverlapping windows so real overlap-list growth does not obscure the issue.

8. **Overview selection limitation to resolve with the intended meaning of “whole-frame layout.”**
   Location: `src/simcompare/local_delta_report.py:325`–326 and `:370`–377.

   Only the first available named pair's two layout masks are retained for each side/frame (`setdefault`); later pairs with different electrodes do not contribute. B3 then presents this as the frame-wide location background for every pair and does not disclose `overview_source_pair_id`. Thus a second pair's electrodes can be absent from its locator even when its own layout crop exists. Selection also follows the ranking traversal. The contract does not explicitly define how to compose more than two electrode masks into the electrode-1/electrode-2 palette, so this is recorded as a limitation rather than an additional unqualified blocker. Either build an explicitly defined frame-wide layout, or visibly identify the selected case/pair and the limited background coverage.

## Verification and test gaps

Executed entirely outside the repository:

- `git archive 801c8da | tar -x -C /tmp/astra_review_801c8da`.
- `uv run --directory /tmp/astra_review_801c8da pytest -q tests/test_final_report.py tests/test_local_delta_report.py`: **21 tests passed**.
- `/tmp/astra_review_checks.py`: focused reproductions for cleanup data loss, actual-producer all-blocked handling, missing crop status, HTML injection, invalid-domain coloring, and multiple CSV contexts.
- `/tmp/astra_review_more.py`: legacy-output equivalence, actual-producer omitted-endpoint crop handling, numeric key formatting, overlap grouping, and size scaling.
- The legacy T1 renderer from `1ac6441` and candidate renderer were run against the same demo config/correspondence with windows 1 and 2 µm. All six pre-existing files were **byte-identical**: `local_delta.html`, `local_delta.json`, `local_delta_rows.csv`, `window_sensitivity.json`, `window_sensitivity_rows.csv`, and `window_sensitivity_rank_agreement.csv`.
- Changing window and diameter spellings from decimal strings to integer spellings, including the ranking window key, still produced exit 0. Classification reads numeric CSV keys, so `20` versus `20.0` is not a string-key lookup problem.
- Overlap grouping controls returned two overlaps for two identical windows (one on each side), then zero when the second a-side frame and b-side layer differed. Existing tests cover intersection area, touching boundaries, top-N order, positive/negative splitting, and zero exclusion.
- Existing tests cover product mismatch, mixed synthetic flags for windows, unexpected comparison schema, required-file omission, removal of stale report files on exit 2, and the same-directory output guard. Code uses resolved paths for that guard, including symlink aliases.
- Crop files reuse the embedded PNG bytes; bounds derive from clipped cell edges and frame origin/pitch. The generated sample matched those coordinates. Content-derived filenames intentionally deduplicate identical images; no deterministic filename collision was found. The overview step calculation caps the long side, though the gray-domain issue above remains.
- The standard demo report has embedded PNGs/inline SVG and no external resources. General self-containment/escaping is not satisfied for hostile input because of finding 2.

The new acceptance tests mostly compare values in `report.json`, not the rendered A1/B1 HTML or chart coordinates (`tests/test_final_report.py:61`–82). The single-context/one-zero-pair demo cannot catch context overwrites, and `test_text_is_escaped` at line 259 only varies a pair ID. There is no all-blocked case, mixed crop-availability case, invalid overview-domain test, or scale test. The T1 tests compare new sidecars with new embedded bytes but do not themselves lock byte equivalence to the parent renderer; this review supplied that missing verification for the demo case. Add regression tests with the fixes rather than treating the current green suite as acceptance of these scenarios.

Artifacts: reproduction inputs and outputs are under `/tmp/astra_review_evidence`; reproduction scripts are `/tmp/astra_review_checks.py` and `/tmp/astra_review_more.py`. The full repository suite and browser rendering were not run; these findings are based on the exact archived code, targeted tests, produced HTML, decoded PNG pixels, and focused executable reproductions.
