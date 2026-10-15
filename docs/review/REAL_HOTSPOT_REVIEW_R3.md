# Independent R3 review — PASS

2026-10-15. Task `task_746598eaba69`, Dispatch `ctx_c5413aed3c65`. Read-only independent follow-up under the user-approved Astra substitution, using the R1/R2 evidence and `docs/review/REAL_HOTSPOT_FIX_D.md`.

**PASS for the reviewed implementation and rebuilt artifact scope. All five R2 findings are closed; no remaining concrete blocker was found in this follow-up.** This is not Windows execution, Windows 16 GB performance, real-data, or cross-repository handoff acceptance.

## Exact candidate

- Repository/base: `/Users/jonghyeonlee/git/particle-sim-compare`, `40a4d6d3982de2d0beb0939909418eb25f2c2379` plus frozen A–D changes.
- Fingerprint inventory: `/tmp/real-hotspot-review-r3-hashes.json`, **32 source/script/test files**, SHA-256 **`87859e1a9f40ac2518b49908a088e484f2114f07608d39aa63e843aac9aaf756`**.
- Same scope as R2: all 24 `src/simcompare/**/*.py` files; Windows builder, verifier, PowerShell, build constraints and runtime input; configured benchmark script; focused real-hotspot and bundle tests.
- Only four inventoried files changed since R2: `adapters/real_hotspot.py`, `electrode_map.py`, `install_and_smoke.ps1`, and `tests/test_real_hotspot.py`. Prior closure evidence remains applicable to unchanged code; relevant changed paths were rechecked.
- ZIP: `outputs/windows_cp312_win_amd64.zip`, **60,631,291 bytes**, SHA-256 **`cb9469376c2894d07092cd9d58f63599d34f5f69b74682cdf8b332a7d6598e62`**.
- App wheel: `simcompare-0.1.0-py3-none-any.whl`, SHA-256 **`cff119dd79846ab6bda525e4d06881b07306be1f6d32eb27487fe94d64c00ee4`**.
- Final before/after equality is recorded at the end of this report.

## Closure evidence

| R2 finding | Reviewed fix and independent evidence | Result |
|---|---|---|
| R2-1: missing-only groups evade byte guard | `real_hotspot.py:538–574` counts all absent expected cases in their assigned groups before output/transient estimates and before frame allocation. Original 100-missing-layer, 2,000-byte counterexample now raises `oversized_real_operation`. An allocation spy verified `np.ones` was not reached before rejection. With a 200,000-byte allowance, the small fixture reports **181,080 estimated bytes**, 3,232 groups and **90,496 retained output-array bytes**, rather than the old 1,880-byte estimate. | Closed |
| R2-2: resealed precision bypass | `real_hotspot.py:82–107` independently derives Decimal precision from raw text; `398–405` also binds record and case-local CSV copies. Original half-unit=100 tampering is rejected. Tested `100.00` and `1.0000e2`, with one or both stored copies changed: all reject with raw-value or representation mismatch. Rounded-consistent cases still pass focused tests; full 32-diameter CSV/hash persistence was separately rechecked after this change. | Closed |
| R2-3: unknown inventory map marked available | `electrode_map.py:332–378` explicitly tracks inventory status and makes source coverage partial when unconfirmed. Independent check verifies both layer and product summary are partial, with observed counts retained. Explicit inventory restores available layer/summary even with no pair classification table, preserving classification-independent mapping. | Closed |
| R2-4: smoke reads wrong report property | Shipped `install_and_smoke.ps1:85` reads `weighted_rates_status`. The independently installed final wheel actually emits that field with `unavailable` and yields the configured numerical fixture values. The shipped script exactly matches the checkout script; its union/map assertions remain present at 88–91. | Closed |
| R2-5: expected 3 / actual 0 exits successfully | `install_and_smoke.ps1:14–16` uses fixed failure code 9 for any expected/actual mismatch; both import and command-loop calls use this helper. `Fail` itself maps code 0 to 9 at line 10. Static control-flow and focused contract checks establish actual 0 or 2 fails and actual 3 continues. No PowerShell execution is claimed. | Closed |

The eager resource guard is now assessed against the complete expected group population for the reviewed counterexample. This remains an estimated resource guard, not streaming or an empirical guarantee for every input. The previously measured 256-PNG synthetic Mac workload and Task D's reported rerun remain supporting evidence, not Windows acceptance; no expensive benchmark was repeated in R3.

## Tests and artifact verification

1. `PYTHONPATH=/Users/jonghyeonlee/git/particle-sim-compare/src .venv/bin/python -m pytest -o addopts='' -q tests/test_real_hotspot.py tests/test_windows_bundle.py --basetemp=/tmp/real-hotspot-r3-tests` — **24 passed in 2.96 s**.
2. `/tmp/real_hotspot_review_r3_checks.py` — **8 independent closure checks passed in 0.45 s**, including direct reuse of the original R2 precision and byte-guard counterexamples. An additional full32 persistence check passed separately in **0.14 s** (eight other checks deselected). Total: **9 independent checks**; no full-suite rerun.
3. `/tmp/real_hotspot_r3_artifact_check.py` — ZIP CRC, exact archive/extracted-bundle equality, 16-wheel inventory/hash verification, target-compatible wheel tags and active Windows/Python-3.12 dependency closure passed. All six direct dependencies remain represented. Exact application `.py` bytes in the wheel match every inventoried checkout source file.
4. Final app wheel installed via `pip3 install --no-index --no-deps --target '/tmp/real-hotspot-r3-wheel-smoke 한글/wheel' <final wheel>`. The fixture subprocess environment selected that installed directory, not checkout code or an older installed package. Import location was explicitly checked in the output.
5. Installed-wheel `import-hotspots`, `validate-data`, `compare`, and `electrode-map` each returned expected exit **3**. Configured diameter-1 union was **available, 0.01 µm²** within 1e-9; map was **available with one cell at count 2**; `weighted_rates_status='unavailable'`. These actual outputs match the shipped smoke's producer/consumer contract.

Artifact verification log: `/tmp/real-hotspot-r3-artifact-check.log`. The previously reviewed offline-only venv/pip requirements, wheel hash failure handling, target selection and dependency inventory are preserved; the Windows script changes are limited to the reviewed smoke fixes.

## Remaining acceptance limits

- Actual Windows PowerShell execution and CPython 3.12 x64 offline installation remain pending; PowerShell is unavailable here. Static script checks plus local installed-wheel execution are not represented as a Windows run.
- Actual Windows 16 GB behavior and real user-data acceptance remain pending. Local tests use Python 3.11.15 and synthetic fixtures; unknown user metadata is not fabricated.
- The previously reported full cross-repository handoff test remains blocked by unrelated dirty sibling work. R3 did not modify that repository or bypass its gate.
- No repository files were edited, no nested workers were used, and no lifecycle ownership was changed. Review scripts, installed test package and outputs are under `/tmp`.

This PASS applies only to the fingerprints above and the stated review scope; substantive later changes require corresponding refreshed verification.

Final verification (2026-10-15T04:10:17.499211+00:00): **32/32 file hashes unchanged**; fingerprint `87859e1a9f40ac2518b49908a088e484f2114f07608d39aa63e843aac9aaf756`; ZIP **60631291 bytes**, SHA-256 `cb9469376c2894d07092cd9d58f63599d34f5f69b74682cdf8b332a7d6598e62` unchanged; application-wheel SHA unchanged; all 16 wheel versions match exact lock pins. No candidate drift during review.
