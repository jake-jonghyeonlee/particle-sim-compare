# Independent R2 review — FAIL

Review date: 2026-10-15. Task `task_07938783c098`, Dispatch `ctx_6c7ef74fbf61`. Independent read-only follow-up using R1 context and the user-approved Astra review substitution. No implementation or coordination authority was taken, no nested worker was launched, and no repository file was edited.

## Exact reviewed candidate

- Repository/base: `/Users/jonghyeonlee/git/particle-sim-compare`, `40a4d6d3982de2d0beb0939909418eb25f2c2379`, plus uncommitted final C work.
- Content inventory: `/tmp/real-hotspot-review-r2-hashes.json`, **32 files**, SHA-256 **`a60e9f53c354711dca1285a476e1cf0c92d6de964c1a3bb4ce7f2affc97522e3`**. Scope is all 24 `src/simcompare/**/*.py` files, the Windows builder/verifier/PowerShell/constraint/runtime inputs, configured comparison benchmark script, and the two focused real-hotspot/bundle test files. This is the initial fingerprint; final equality is recorded below.
- Archive: `outputs/windows_cp312_win_amd64.zip`, **60,630,632 bytes**, SHA-256 **`84befb4df6da86804c2b0f9741758cf64f8a272160e473ee9b413fd107eb9ba7`**.
- App wheel SHA-256: **`e0eee26a2b8c804cf07dc84155f5cbe285e281d6d60e99c13583ed16114992e0`**, `simcompare-0.1.0-py3-none-any.whl`.
- Read `docs/review/REAL_HOTSPOT_FIX_C.md`, the prior R1 report and relevant changed implementation/tests. The final wheel's Python sources exactly match the frozen checkout sources, and archived bundle contents exactly match the extracted bundle.

## Remaining blockers

### R2-1 — P1: Missing-inventory groups bypass the operation byte guard (R1 F5 remains open)

**Locations:** `src/simcompare/adapters/real_hotspot.py:505–534`, then `602–633`; retained group output allocation at `src/simcompare/linepixel.py:431`.

The byte estimate counts only discovered `manifest['cases']`. Expected-but-absent cases are added afterward and may introduce arbitrarily many additional layer/diameter groups. Those groups still allocate seven full-size output masks each, including when every observation in the group is missing. Neither group nor operation guard accounts for them.

**Executed reproduction:** one discovered 2×2 case with two observed masks; inventory adds 100 absent cases on distinct explicitly assigned layers; set `max_operation_bytes=2000`. `configured_dataset` accepts an estimate of **1,880 bytes**, creates 101 cases, and `compute_areas` retains **90,496 bytes of output arrays alone** across 3,232 groups. At the intended 1270×1270 size the same supported configuration has a **758,063,000-byte estimate**, while retained output arrays alone require **36,490,249,600 bytes (~34 GiB)**. The scale calculation is structural, not a claim that this large case was allocated during review.

**Required fix:** determine the complete expected group population before resource accounting, including missing-only groups, and guard all output allocations; alternatively emit scalars for missing-only groups without full arrays. Test that this counterexample is rejected or stays within its budget. The successful 8-case benchmark below does not close this gap.

### R2-2 — P1: Resealed CSV precision can still override the physical mismatch check (R1 F1 partially open)

**Location:** `src/simcompare/adapters/real_hotspot.py:395–414`.

The loader validates that rounding tolerance is finite/nonnegative and matches other editable tolerance fields, but never derives it again from `raw_area`. Thus semantic validity still depends on a self-consistent resealed claim for this part of the contract.

**Executed reproduction:** import a one-pixel PNG (0.01 µm²) with CSV raw text/value `100.00`. Change `area_rounding_half_unit` from 0.005 to 100 in both stored CSV copies, set `area_tolerance_um2=100.005` and `csv_status='consistent'`, and recompute the manifest hash. Keep the raw decimal/value and actual PNG intact. The loader and configured calculation accept it and produce an **available** union with no mismatch reasons. The proper precision-derived tolerance would be 0.01 µm², not 100.005.

**Required fix:** recompute decimal recording precision from raw text and validate both CSV representations and derived difference/status/tolerance against it. Rehashing must not turn a 99.99 µm² discrepancy into a consistent measurement.

### R2-3 — P2: A map with no expected inventory still reports available source coverage

**Location:** `src/simcompare/electrode_map.py:342–368` (case reasons collected at 343, but excluded from the source-coverage decision at 361).

**Executed reproduction:** ordinary import without `--expected-cases`, explicit valid assignments and AB classification. The line/pixel union is correctly partial. The map layer carries `expected_case_inventory_unprovided` in its reasons but simultaneously reports **`status='available'` and `coverage.source_coverage_status='available'`**. This contradicts the C fix claim that unprovided expected inventory makes results partial and still certifies coverage that has not been established.

**Required fix:** propagate inventory uncertainty into map layer and summary coverage status while retaining usable observed count arrays. Classification absence alone need not block the classification-independent map; expected-source uncertainty does.

### R2-4 — P1: The shipped PowerShell smoke cannot reach its numerical assertions

**Locations:** `scripts/install_and_smoke.ps1:80`; producer `src/simcompare/pipeline.py:215–220`. The archive contains the same script.

The smoke config supplies condition assignments, so comparison takes the configured branch and writes **`weighted_rates_status`**. Line 80 instead accesses **`$comparison.line_pixel_rates_status`**, a property emitted only by the unconfigured branch. With `Set-StrictMode -Version Latest`, this absent-property access fails; without strict mode its null value still fails the comparison to `"unavailable"`. The union/map assertions at lines 83–86 are never reached by a correct configured fixture run.

**Executed evidence:** final archived wheel was verified against source, exercised from a Unicode/spaced `/tmp` path, and separately installed offline into a `/tmp` target. Its configured fixture commands return documented exit 3, union is available at 0.01 µm², and map has one cell with count 2. The resulting JSON has `weighted_rates_status='unavailable'` and no `line_pixel_rates_status`. No Windows or PowerShell execution is claimed; the broken producer/consumer field contract is established by actual wheel output and the shipped script.

**Required fix:** assert the configured report field, then rebuild the archive and run the smoke against that exact artifact. Ensure the numerical assertions execute.

### R2-5 — P2: Unexpected successful CLI exit is returned as smoke success

**Locations:** `scripts/install_and_smoke.ps1:43,75`, `Fail` at `8–10`.

Each expected-exit-3 check calls `Fail ... $LASTEXITCODE`. If a CLI returns **0** instead of the expected incomplete status 3, the failure branch calls `Fail` with code 0 and exits the installer **successfully**, before the result assertions. Other nonzero unexpected exits propagate correctly.

**Static counterexample:** for either check, substitute a CLI that exits 0; `0 -ne 3` is true, and `Fail(message, 0)` executes `exit 0`. This is not a Windows execution claim.

**Required fix:** use a dedicated nonzero assertion-failure exit code for unexpected status 0 (or for all status mismatches), retaining the received status in the message. Add status-mismatch tests including expected 3/actual 0, 2 and 3.

## R1 disposition

| R1 finding | R2 evidence and disposition |
|---|---|
| F1 semantic schema | Fixed crop-origin/policy, nonbinary-mask, endpoint-binding and missing-record examples; remaining precision bypass R2-2 prevents closure. |
| F2 CSV mismatch propagation | Original 100.00-versus-0.01 mismatch now makes measurement, union and map partial; CSV raw value/difference/tolerance survive comparison output. Rounded-consistent input still works. Original finding closed, subject to R2-2 validation bypass. |
| F3 explicit inventory gaps | Known absent cases are now explicit missing records and make group/map coverage partial. Missing inventory itself still leaves map status available (R2-3). |
| F4 per-case geometry | Verified both sort orders: unconfirmed case remains unpositioned/missing in configured calculation; confirmed case retains shared frame; map partial. Closed for original counterexample. |
| F5 memory lifetime/guard | Incremental union replaces stacked reduction; explicit eager byte guard and benchmark added. Guard misses added expected groups (R2-1), so not closed. |
| F6 legacy null role | Rehashed numeric_v2 null-role fixture rejected with `legacy_endpoint_role_required`; real nullable roles retained. Closed. |
| F7 all inputs validated | Invalid target and bad map assignment now produce side-labelled invalid diagnostics; physical-input aliases retained. Closed for original counterexample. |

Full 32-diameter input/CSV persistence now round-trips its manifest hash, including string-key canonicalization; verified independently and by the 256-PNG benchmark.

## Validation performed

- **Focused repository tests:** 20 passed in 2.26 s (`tests/test_real_hotspot.py tests/test_windows_bundle.py`). The first invocation omitted `PYTHONPATH` and had five subprocess import failures, `No module named simcompare`; rerunning with the candidate `src` path passed. Subsequent adversarial work uses absolute `PYTHONPATH=/Users/jonghyeonlee/git/particle-sim-compare/src`. These environment failures are not attributed to candidate code.
- **Independent R2 checks:** `/tmp/real_hotspot_review_r2_repros.py`, 9 passed in 0.45 s. Five tests check prior corrections/full32 persistence; four intentionally assert current defects (precision, inventory-map status, missing-group byte guard, smoke field). A reused R1 helper prints the obsolete phrase "target ignored", but the R2 wrapper explicitly checks the new target-labelled invalid diagnostic and passes; the old print text is not the R2 conclusion.
- **Bundle verifier:** valid 16-wheel inventory accepted; focused tests exercised tampered/missing wheel failures. Archive CRC and byte-for-byte extracted content comparison passed. Every wheel has CPython 3.12 Windows x64-compatible tags, its version/hash appears in the target lock, and active Windows/Python-3.12 `Requires-Dist` dependencies form a closed set. All six declared direct dependencies are retained.
- **Wheel independence:** `/tmp/real_hotspot_r2_artifact_check.py` executed final wheel contents outside the checkout, then `pip3 install --no-index --no-deps --target '/tmp/real-hotspot-r2-installed-wheel 한글' <final wheel>` succeeded. Candidate source was not selected by those runs. Installed-wheel compare/map reproduced expected numerical outputs and the missing smoke property.
- **Offline installer review:** uses venv Python, `--no-index --only-binary=:all: --require-hashes --find-links`, bundled-wheel integrity check, `pip check`, runtime imports, platform/interpreter checks and explicit native exit checks. It does not need uv or source builds on the offline machine. The two smoke defects above remain.
- **Independent 256-PNG benchmark:** `scripts/benchmark_real_hotspot_compare_map.py`, 8 cases × 32 observed diameters at 1270×1270, explicit inventory/eight line pairs/one assigned layer. macOS 26.5.2 arm64, Python 3.11.15. Import **0.663 s**; configured compare **4.450 s / 1,366.9 MiB peak process RSS**; map **1.163 s / 586.1 MiB**. Asserted union **0.08000000000000002 µm²** and **8** cells at map count 2. Output: `/tmp/real-hotspot-r2-benchmark.json`. Process RSS, not Python heap, was measured. These values support the representative local run only; they do not prove the byte bound for arbitrary supported inputs or Windows 16 GB acceptance.

No duplicate full-suite run. Coordinator/worker broader evidence is acknowledged; the reported cross-repository handoff gate remains blocked by unrelated dirty sibling work and was not bypassed.

## Limits and next action

**FAIL for this exact source and archive candidate.** Fix the five remaining findings, rebuild the artifact after source/script changes, and review the new fingerprints. The previous candidate must not inherit a PASS from successful fixture calculations alone.

Windows CPython 3.12 x64 execution, actual offline installation, representative Windows 16 GB performance, and actual user-data validation remain pending. The benchmark and fixture are synthetic observations exercised through the real adapter, not real-data acceptance. No physical metadata or missing observations were invented.

Final verification (2026-10-15T04:02:04.534422+00:00): all 32/32 frozen file hashes match the initial inventory; fingerprint SHA-256 remains `a60e9f53c354711dca1285a476e1cf0c92d6de964c1a3bb4ce7f2affc97522e3`; archive size and SHA-256 remain 60630632 bytes / `84befb4df6da86804c2b0f9741758cf64f8a272160e473ee9b413fd107eb9ba7`. Exact wheel versions also match all 16 `==` lock pins. No candidate changes detected during review.
