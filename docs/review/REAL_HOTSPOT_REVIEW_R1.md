# Independent Task A review — FAIL

Review date: 2026-10-15. This is the fresh, read-only review requested with the user-approved Astra substitution; no code was authored or changed by this reviewer. The review task completed, but the implementation candidate does **not** pass acceptance.

## Exact candidate and scope

- Repository: `/Users/jonghyeonlee/git/particle-sim-compare`.
- Base: `40a4d6d3982de2d0beb0939909418eb25f2c2379` plus the frozen uncommitted Task A implementation.
- Candidate manifest: `/tmp/real-hotspot-review-a-hashes.json`, SHA-256 `0442fc01de6fbc912ac32ce08adb654f0e02808b46679688da70b2ebf033f621`.
- Scope: all 24 `src/simcompare/**/*.py` files and `tests/test_real_hotspot.py` enumerated in that manifest, especially the new real adapter and modified pipeline/schema/measurement/PNG/linepixel paths. Initial verification: 25/25 hashes match. Final verification is recorded below before delivery.
- Requirements read: `docs/plan/real_hotspot_windows_plan_v1.md`, accepted C1–C4 in `docs/plan/real_hotspot_astra_advisory.md`, and `docs/review/REAL_HOTSPOT_IMPLEMENTATION_A.md`.
- Concurrent Task B scripts/docs/packaging are outside the verdict. This is not an overall packaging, representative-memory, real-data, or Windows execution PASS.

## Findings

### F1 — P1: Recomputed hashes substitute for required semantic contract validation

Location: `src/simcompare/adapters/real_hotspot.py:260–285`, especially `283–285`; silent conversion at `394`.

`load_real_manifest` validates version/hash/diameter coverage and nonzero count, but does not validate the fixed real-source policy, crop transform, frame semantics, or actual binary asset values. Hashes are editable provenance, so a producer can recompute them without satisfying the accepted physical contract.

Two reproduced counterexamples:

1. Change an authentic import's crop origin from `[50,50]` to `[0,0]`, set `gray_nonzero_is_occupied=false`, and recompute the documented manifest hash. Loading and configured calculation both succeed, yielding an `available` group at bbox `(0,0,0.2,0.2)` instead of `(50,50,50.2,50.2)`.
2. Replace a binary occupancy array with `[[-7.0,0],[0,0]]`, refresh its SHA and manifest SHA. The loader accepts the equal nonzero count; configured loading casts `-7.0` to `True` and emits an `available` group.

Fix acceptance: independently validate the version's geometry/policy/units, endpoint/case bindings, missing-record invariants and exact finite binary assets; preserve hash checks as an additional integrity check. Both counterexamples must raise a contract error even with correctly recomputed hashes.

### F2 — P1: A CSV area mismatch disappears when calculations are configured

Location: `src/simcompare/adapters/real_hotspot.py:397–405`; emitted comparison at `src/simcompare/pipeline.py:180–212`.

The adapter only creates `SourceCA` when `csv_status == 'consistent'`; on a mismatch it removes the comparison source and gives an observed mask no reasons. The mismatch, original CSV area, difference and tolerance are absent from the configured comparison's source-case serialization. Consequently the configured unweighted union, failure pieces and map can claim available observations with no mismatch warning.

Reproduction: import a one-pixel mask (0.01 µm²) with case-local CSV `100.00`. The manifest correctly says `mismatch`, but `compute_areas` returns diameter-1 union `status='available'`, empty reasons, and ~0.01 source CA; `compute_electrode_maps` also returns an available layer with empty reasons.

Fix acceptance: preserve source CSV value, difference, tolerance and mismatch status through configured measurement/report/map paths; apply an explicit conservative mismatch policy rather than treating the CSV as absent. Rounded-consistent cases must remain accepted.

### F3 — P1: Explicitly missing expected cases are dropped from calculation coverage

Location: `src/simcompare/adapters/real_hotspot.py:414–427`.

`expected_raw` is rebuilt exclusively from discovered/calculated cases and replaces the manifest's case list used by downstream completeness checks. The expected-case inventory and its missing-folder diagnostic therefore have no effect on group or map coverage.

Reproduction: inventory lists `lateral_short-A-L1-B-L1` and `lateral_short-A-L1-C-L1`, but only AB exists. Import reports `expected_case_folder_missing`. Configured diameter-1 union reports `coverage.expected=1`, `status='available'`; the map layer is also available. The absent AC case is not counted as unknown/missing.

Fix acceptance: carry inventory gaps into expected-source coverage or block the affected grouping conservatively when its physical assignment cannot be established. The classified group and map must not certify complete source coverage. Unprovided inventory must remain distinguishable from confirmed coverage.

### F4 — P1: A case with unverified crop geometry can borrow another case's frame

Location: `src/simcompare/adapters/real_hotspot.py:163–174`, `194–195`, `228–229`, and `336–342`.

If either electrode image is absent, crop geometry is unknown and only an entry diagnostic is added. The global frame later uses the last case's `crop_shape`, and configured loading ignores the earlier case's geometry diagnostic. The earlier hotspot can thus become a fully positioned observation using another case's geometry. In the opposite sort order, the final missing electrode makes the global shape null and prevents otherwise valid cases from grouping; behavior depends on case order.

Reproduction: import AB with `2.png` removed, then valid AC, both small 1002×1002 electrode fixtures. AB retains `electrode_image_missing`, but configured mapping succeeds with layer `status='available'` and no reasons. Its crop-to-electrode correspondence was never verified.

Fix acceptance: preserve per-case geometry validity; retain raw observations but exclude/block or conservatively mark geometry-dependent outputs for that case, without contaminating the shared frame or making validity depend on discovery order.

### F5 — P1: The accepted bounded-lifetime design is not implemented; guards omit retained output masks

Location: `src/simcompare/adapters/real_hotspot.py:344–363`, `388–413`; call site `src/simcompare/pipeline.py:191–196`; retained outputs `src/simcompare/linepixel.py:137–143,431`.

Configured loading materializes every observed mask for every case/diameter, and `MaskRecord` copies the arrays. `compute_areas` then retains seven full-size output masks for every layer/diameter group until the whole `AreaResult` is serialized. Sequential reference/target processing helps, but does not implement C4's group/diameter-limited source and result lifetime. The source-cell and per-layer group-cell guards do not count this retained output population. `measurement.py:37` also still uses the array-stacking `logical_or.reduce` union.

Small reproducible lifetime evidence: two cases on two assigned layers, all 32 diameters, retain 64 distinct source arrays and 448 result arrays simultaneously. At 1270×1270, 116 single-case layers with 32 observed diameters pass both defaults: 5,987,084,800 source cells < 6,000,000,000 and 1,612,900 cells/group < 500,000,000. Those inputs plus the seven outputs alone require 47,896,678,400 bytes (~44.61 GiB), before transient allocations and Python objects. This is a structural lower-bound calculation, not an RSS benchmark or a claim that the user's real data has 116 layers.

Fix acceptance: implement bounded group/diameter materialization and scalar result emission/release, or a conservative whole-operation byte guard including retained output and transient arrays. A representative Task B benchmark remains required, but cannot repair the missing lifetime/guard accounting by itself.

### F6 — P2: Nullable real roles also relax the legacy numeric_v2 contract

Location: `src/simcompare/numeric_schema.py:145–152`; unchanged legacy call site `src/simcompare/adapters/numeric.py:199`.

`Endpoint` now accepts `role=None` for every source format. The legacy loader does not independently require a role, so the real-format exception escapes its intended boundary.

Reproduction: copy the existing numeric_v2 fixture, set the first endpoint role and its raw mirror to null, recompute the existing source hash. `load_dataset` now succeeds with `role is None`; before this diff `Endpoint.__post_init__` called `text` on role and rejected it.

Fix acceptance: require nonempty roles in the legacy source loader or version-specific validation while retaining null plus provenance for real_hotspot_v1. Legacy height/NA invariants must continue to pass.

### F7 — P2: validate-data stops after the first real input

Location: `src/simcompare/pipeline.py:348–352`.

The real path returns from inside the input-section loop. A valid real reference prevents validation of the target and optional map input, and supplied condition assignments are not validated by this path.

Reproduction: provide a valid imported reference and a target directory that does not exist. `run_numeric_validate` returns ordinary partial status/exit 3 and a reference-only report, with no invalid-target diagnostic.

Fix acceptance: validate every requested distinct input and supplied assignment, preserving side/source identity in the report; an invalid target must be reported rather than bypassed.

## Executed evidence

1. `.venv/bin/python -m pytest tests/test_real_hotspot.py tests/test_numeric_schema.py tests/test_measurement.py -q` — 55 tests passed. Includes current gray/RGB/alpha/CRC checks, non-square crop/corner, rounded CSV measurement, observed-zero versus missing, configured raw/line/map behavior and same-name retention.
2. Six additional selected tests passed (0.10 s):
   - `tests/test_numeric_inputs.py::test_accepted_t1_fixture_preserves_types_bindings_and_coverage`
   - `tests/test_numeric_inputs.py::test_converted_source_diameter_na_evidence_matches_area_and_map`
   - `tests/test_linepixel.py::test_priority_union_and_shares_hand_written_counterexample`
   - `tests/test_linepixel.py::test_three_way_shared_membership_is_one_unsplit_bucket`
   - `tests/test_failure_pieces.py::test_hand_written_mapped_short_union_pieces_and_line_pixel_unchanged`
   - `tests/test_electrode_map.py::test_canonical_duplicate_pairs_distinct_electrodes_and_layer_maximum`
3. `.venv/bin/python -m pytest /tmp/real_hotspot_review_repros.py -q -s --basetemp=/tmp/real-hotspot-review-repros-data` — 8 counterexample tests passed in 0.18 s. These tests intentionally assert the observed defects; passing them confirms reproduction, not implementation correctness. Their test names map directly to F1 (two tests) and F2–F7. The first draft had two repro-harness mistakes (exact floating-point equality and a wrong field name); both were corrected in the `/tmp` harness before the final successful run, with no candidate changes.

The strict decoder correctly reuses structure/CRC/inflate checks and rejects non-gray RGB/alpha. The ordinary unweighted priority/shared/failure-piece/map algorithms pass the selected existing oracles. Same-name sources remain present and unresolved in the tested comparison, without fabricated electrode names. These positive checks do not override the seven findings above.

## Limits and disposition

No repository modifications, nested workers, full-suite rerun, destructive recovery, real-data validation or Windows execution. Temporary reproductions/report only; the source candidate was read-only. The runtime was the repository virtualenv (Python 3.11), not Windows Python 3.12. No user metadata, layer order, role, height, density, unit dimensions or missing actual files were invented. Actual CSV encoding/domain decisions, source-condition assignments, expected inventory, classification data and electrode polarity still require explicit source evidence as planned.

Required next step: fix the findings in a separately owned implementation task, update the candidate fingerprint, and re-review the changed candidate. Task B packaging and representative RSS/time acceptance remain outside this verdict.

Final fingerprint verification (2026-10-15T03:29:24.063745+00:00): manifest SHA-256 unchanged; all 25/25 frozen file hashes match the initial candidate; no mismatches. Runtime: 3.11.15.
