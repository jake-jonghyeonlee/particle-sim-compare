# Real hotspot review fixes — Task C

## Result

Addressed findings F1–F7 from `REAL_HOTSPOT_REVIEW_R1.md` while preserving the prior A/B work and the coordinator's plan/checkpoint files. The real manifest loader now independently checks fixed source units, grayscale occupancy policy, frame/crop transform, case and endpoint bindings, record coverage, source-index coverage, finite area invariants, and exact boolean assets after hash verification. Per-case geometry stays local; unconfirmed observations remain inspectable but do not enter positioned calculations.

CSV mismatches retain the original decimal text, parsed value, difference, tolerance, and mismatch status in measurement/report/map provenance, and make dependent metrics partial. Rounded-consistent CSV values remain accepted. Expected-but-missing cases become explicit missing records in comparison and map coverage; absent expected inventory is marked unconfirmed and also makes results partial. The 8 GiB `max_operation_bytes` default estimates retained source masks, diameter outputs, and a transient margin for the eager operation; values above the default are rejected. Legacy numeric_v2 inputs again require nonempty endpoint roles, while real inputs keep nullable roles. `validate-data` validates every distinct configured input and each supplied real condition assignment, with side-labelled diagnostics.

Electrode polarity limits review ROI only. Configured electrode maps use observed hotspot masks and do not require polarity. The B handoff and PowerShell smoke wording now reflects this rule.

## Verification

- `PYTHONPATH=/Users/jonghyeonlee/git/particle-sim-compare/src .venv/bin/python -m pytest -q tests/test_real_hotspot.py tests/test_numeric_inputs.py tests/test_windows_bundle.py` — focused regressions passed, including resealed semantic/hash counterexamples, nonbinary arrays, CSV mismatch and rounding, expected-case gaps, unconfirmed geometry, resource guard, legacy null-role rejection, labelled target/map validation, and wheel tamper/missing checks.
- `PYTHONPATH=/Users/jonghyeonlee/git/particle-sim-compare/src .venv/bin/python -m pytest -q` — all other repository tests passed; the sole failure is `tests/test_handoff.py::test_offline_full_candidate_in_temporary_git_archive`, which refuses to run because sibling `particle-sim-dl1` already has unrelated uncommitted files. Two tests were skipped. No sibling repository files were changed.
- `PYTHONPATH=src .venv/bin/python scripts/benchmark_real_hotspot_compare_map.py` — macOS 26.5.2 arm64, Python 3.11.15; 8 cases × 32 observed diameters = 256 PNGs at 1270×1270, explicit inventory, eight explicit line pairs and one assigned layer. Import: 0.714 s. Configured compare subprocess: 4.512 s, peak RSS 1,154.5 MiB. Configured map subprocess: 1.202 s, peak RSS 576.2 MiB. Assertions verified diameter-1 union 0.08000000000000002 µm² and eight cells with map count 2.
- `python3 scripts/build_windows_bundle.py` — rebuilt the final ignored archive after source and smoke changes. Bundle verifier: 16 wheels; wheel tag check matched CPython 3.12 Windows x64; all six direct dependencies are pinned in the hashed lock. `unzip -t outputs/windows_cp312_win_amd64.zip`, `python scripts/verify_windows_bundle.py outputs/windows_cp312_win_amd64`, and `tests/test_windows_bundle.py` passed. Matching, tampered, and missing wheel inventory cases were checked.
- Installed the built `simcompare-0.1.0-py3-none-any.whl` locally into a target directory and ran the fixture's import, validate, compare and map commands from a Unicode/spaced path. All returned documented incomplete exit 3; configured comparison asserted an available 0.01 µm² diameter-1 union and map output asserted one cell with count 2.
- `git diff --check` passed.

## Bundle and limits

- Archive: `outputs/windows_cp312_win_amd64.zip`
- Size: 60,630,632 bytes
- SHA-256: `84befb4df6da86804c2b0f9741758cf64f8a272160e473ee9b413fd107eb9ba7`
- Windows run: pending. Extract to a Unicode/spaced directory on CPython 3.12 x64, then run `Set-ExecutionPolicy -Scope Process Bypass` and `./install_and_smoke.ps1`; the script validates architecture, bundle hashes, offline hash-checked wheel installation, imports, `pip check`, expected exit codes, configured union value, and map histogram.
- Real-data run: pending; benchmark inputs are synthetic. The Mac RSS/time run does not certify Windows 16 GB behavior.
- The cross-repository handoff test remains blocked by its dirty sibling checkout and was not bypassed by editing another repository.
