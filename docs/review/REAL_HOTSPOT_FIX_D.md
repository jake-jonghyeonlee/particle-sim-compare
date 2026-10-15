# Real hotspot R2 follow-up fixes

Candidate: current worktree based on `40a4d6d3982de2d0beb0939909418eb25f2c2379`, including accepted A–C changes. This closes the five R2 findings in `REAL_HOTSPOT_REVIEW_R2.md` without editing the plan or review reports.

## Changes

- Operation preflight counts every expected-but-absent case in its assigned analysis-layer groups before estimating source, retained output, and transient arrays. It rejects budgets above 8 GiB, rejects the 100-missing-layer/2,000-byte reproducer, and now creates the frame validity array only after the budget passes. The estimate is eager and conservative; it does not claim streaming.
- CSV precision is recomputed from each preserved `raw_area` Decimal string. The loader checks the case-local CSV index and per-diameter copy, numeric value, precision half-unit, measurement difference, tolerance, and status against the binary mask area. The resealed `100.00`/half-unit `100` counterexample is rejected; the rounded `0.014` to `0.01` match remains accepted.
- Electrode maps report partial source coverage and an explicit inventory coverage summary when expected inventory was not supplied, while retaining observed counts. Pair-table absence remains independent of map availability.
- The configured PowerShell smoke checks `weighted_rates_status`. Its exit assertion uses a fixed nonzero assertion-failure code, so expected exit 3 with actual 0 or 2 fails and actual 3 passes.

## Validation

- `PYTHONPATH="$PWD/src" .venv/bin/python -m pytest tests/test_real_hotspot.py tests/test_windows_bundle.py` — **24 passed** (2.62 s).
- `PYTHONPATH="$PWD/src" .venv/bin/python scripts/benchmark_real_hotspot_compare_map.py` — eight cases, 32 observed diameters each (256 total 1270×1270 PNGs), explicit inventory and pair rows; import 0.696 s, configured compare 4.933 s / 1,209.8 MiB peak process RSS, map 1.641 s / 576.2 MiB. Verified diameter-1 union **0.08000000000000002 µm²** and **8** map cells at count 2. This is a synthetic Mac arm64 / Python 3.11.15 measurement, not a Windows memory acceptance result.
- `unzip -t outputs/windows_cp312_win_amd64.zip` — archive CRC passed. ` .venv/bin/python scripts/verify_windows_bundle.py outputs/windows_cp312_win_amd64` — 16-wheel inventory/hash verification passed. The final `simcompare-0.1.0-py3-none-any.whl` was installed into a temporary target outside the checkout; installed-wheel `import-hotspots`, `validate-data`, `compare`, and `electrode-map` all returned documented exit 3. Configured comparison returned `weighted_rates_status=unavailable`, an available diameter-1 union of **0.010000000000000002 µm²**, and map status available with **1** cell at count 2.
- PowerShell (`pwsh`/`powershell`) is not installed in this environment. The producer-consumer contract test checks the configured report field against the shipped script and its expected-exit cases `(3,0)`, `(3,2)`, `(3,3)` map to assertion exits `9`, `9`, `0`; actual Windows script execution and Windows CPython 3.12 offline installation remain pending.
- `git diff --check` passed.

## Final artifact and source fingerprints

- Archive: `outputs/windows_cp312_win_amd64.zip`, **60,631,291 bytes**, SHA-256 `cb9469376c2894d07092cd9d58f63599d34f5f69b74682cdf8b332a7d6598e62`.
- Application wheel: `simcompare-0.1.0-py3-none-any.whl`, SHA-256 `cff119dd79846ab6bda525e4d06881b07306be1f6d32eb27487fe94d64c00ee4`.
- Relevant source hashes: `real_hotspot.py` `10d97a50aad540edf0bb74faab37f6538a003ee0090933d34f1490f6964f2e2f`; `electrode_map.py` `bc6b1e844c4b13c4c4d6cc868924d6755a60a92b71fb86a223c255d435cf44f1`; `install_and_smoke.ps1` `af357621d4584596fb234ceecefc4688b340f5fb3c2923bc2f02d48a820bd6bb`; `test_real_hotspot.py` `5fb223deb87ab40ccc09433b3e83427c2c465b5c1623314ef7d37403265064ff`.

No Windows execution, 16 GB Windows resource result, real-data execution, or independent review PASS is claimed.
