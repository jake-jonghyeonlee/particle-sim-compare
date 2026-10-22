# T5 — real-scale benchmark, Python 3.8, runner rebuild (2026-10-22)

Baseline: HEAD 01989de + uncommitted T1–T4. Host: macOS arm64 (12 cores, 48 GiB), `.venv` Python 3.14 / numpy 2.4.6, one run at a time.
Script: `scripts/benchmark_scale_compare.py` (each CLI command is its own process under `/usr/bin/time -l`; RSS = that process's
maximum resident set size). Data spec is fixed in the script: per model N cases at crop S, 32 diameters, one layer `L1`,
distinct pair `A<i>/B<i>` per case, 70 % line / 30 % pixel, reference and target use different seeds.
`scattered` = fixture blobs (shared by 3 cases); `centered` = one rectangular band per case at the crop centre, ±50 px jitter,
half-height r = max(1, d·S/400), half-width r + 60 px (most cases overlap, like real crops).
All `validate-data`/`compare` exits were 3 (partial: `csv_mismatch` — the fixture CSV area is a constant 0.01 — and
`density_contract_unconfirmed, unit_area_basis_unconfirmed`); `import-hotspots` is always 3 (physical metadata unset).

## Results (wall s / peak RSS MiB)

| Run | import ref | import tgt | validate-data | compare | comparison.json | comparison.csv | disk (input / imported / outputs) |
|---|---|---|---|---|---|---|---|
| centered N=300 S=1262 | 41.6 / 198 | 41.6 / 191 | 11.3 / 241 | 79.6 / 471 | 72.2 MB | 9.4 MB | 339 MB / 74 MB / ~108 MB |
| scattered N=300 S=1262 | 42.4 / 194 | 42.6 / 196 | 11.3 / 245 | 68.7 / 406 | 67.4 MB (json+csv) | — | 359 MB / 78 MB / ~94 MB |
| centered N=1682 S=1262, **default limits** | 240.9 / 845 | 241.7 / 840 | 69.1 / 1111 | **77.4 / 1276, exit 2** | — | — | 1.90 GB / 413 MB / — |
| centered N=1682 S=1262, raised limits | (same data) | | 65.6 / 1111 | **573.8 / 3334** | 378.5 MB | 52.5 MB | + 0.58 GB outputs (≈ 2.9 GB total) |
| centered N=1682 S=1500, raised limits | 326.6 / 844 | 324.4 / 884 | 87.1 / 1131 | **732.2 / 3651** | 380.6 MB | 52.5 MB | 2.63 GB / 493 MB / 0.58 GB (4.0 GB total) |

Whole pipeline wall: N=300 ≈ 3 min; N=1682@1262 ≈ 10.7 min (limit failure) + 10.7 min (rerun); N=1682@1500 ≈ 24.8 min.
Data generation (12-way parallel) took 2–13 s; all generated data under `/tmp` was deleted afterwards.

### Limit error (recorded verbatim) and rerun
Default limits, centered N=1682, S=1262, `compare` (exit 2, `error.json` reason `oversized_real_bucket_output`):

    oversized_real_bucket_output: L1 d=3.0: 1669777 entries; limit 1000000

Rerun once with `max_bucket_output_entries: 10000000` and `max_operation_bytes: 34359738368` (32 GiB; required because the
estimate adds `max_bucket_output_entries × 1,100` B = 11 GB, above the 8 GiB default) in both `condition_assignment`s:
finished, exit 3, 573.8 s, 3,334 MiB. The 10 M value was a first guess, not a measured minimum; the failing group already
needed 1.67 M entries at d=3, and larger diameters were not probed for their exact peak. The S=1500 run used the raised
limits from the start (no second default-limit failure run; the same group-size limit is expected).

## Python 3.8 (`/tmp/py38`: Python 3.8.20, numpy 1.21.6, Pillow, PyYAML, pytest)
`PYTHONPATH=src /tmp/py38/bin/python -m pytest -p no:warnings tests/test_real_hotspot.py tests/test_linepixel_equivalence.py
tests/test_comparison_v2.py tests/test_summary_mode.py tests/test_scale_guards.py tests/test_linepixel.py tests/test_failure_pieces.py`
→ **180 passed, 0 skipped** (146.6 s; numpy 1.21.6 has `bitorder`, so the oracle tests ran). Only these files were run, not the whole suite.

Rebuilt runner `bundles/etx/simcompare_job_2_validate_compare.py` (copy with `BASE_DIR` edited, `real.yaml` = N=300 centered config,
one layer) under `/tmp/py38/bin/python`: validate-data exit 3 in 13 s, compare exit 3 in 176 s (3.14 took 11.8 s / 82.3 s; numpy 1.21 is
slower for compare, ~2.1×). Log tail:

    [simcompare_job] 2026-10-22 11:49:38 validate-data exit 3 in 13s
    [simcompare_job] 2026-10-22 11:49:38 start compare --config real.yaml
    {"status": "partial", "reasons": ["density_contract_unconfirmed", "unit_area_basis_unconfirmed"], ... "exit_code": 3}
    [simcompare_job] 2026-10-22 11:52:34 compare exit 3 in 176s

Observation: py3.8/numpy 1.21 and py3.14/numpy 2.4 `comparison.csv` differ at the last float digit of a summed area
(`87.20999999999998` vs `87.20999999999995`, pairwise-summation order); not bytewise identical across numpy versions, equal to ~1e-14 relative.

## Extrapolation notes (estimates, not measurements)
- compare wall: N=300 → 1682 (×5.6) took ×7.2 (exponent ≈ 1.15; grows from shared-bucket output). S=1262 → 1500 (cells ×1.41) took ×1.28.
- compare RSS: 471 MiB → 3.3 GiB (N ×5.6, includes the 10 M-entry budget). S has a minor effect (3.33 → 3.65 GiB).
- comparison.json (summary default) scales ~linearly in cases: 72 MB → 379 MB (×5.2); csv 9.4 → 52.5 MB.
- **Correction (T6):** cases are split across layers, so one layer holding all N=1682 cases is the worst case. An 11-layer run does NOT
  multiply the one-layer N=1682 time by 11; per-layer cost falls with the cases per layer (roughly linear-plus in N, exponent ≈ 1.15
  measured above), so total time is expected to be at or below the one-layer figure times a small factor. Retained packed results
  (`layers × 32 × 7 × cells/8` ≈ 0.5 GB at 11 layers) still add up. Unverified; no multi-layer run was made.
  Real bucket structure can have more distinct overlap patterns than this band model; that is what `max_label_pattern_entries` guards.
- import-hotspots ≈ 4–5.5 min per model at N=1682 (S=1262/1500), 0.85 GiB RSS; validate-data ≈ 1–1.5 min, 1.1 GiB.

## Not covered
stages 4 and 6 (guards only, Phase B), `full_bucket_detail: true` at N=1682 (would be GB-scale), multi-layer runs, the real-data bucket-count profile.

## T6 — separate label-pattern budget, default limits (2026-10-22)
Change: new `condition_assignment.max_label_pattern_entries` (default 24,000,000; error `oversized_real_label_patterns` with layer, diameter,
count, limit, remedy) bounds the exact partition's live patterns; `max_bucket_output_entries` (1,000,000) now bounds only emitted output.
Estimator: `max_label_entries × 256 B` replaces `max_bucket_output_entries × 1,100 B` (default total for N=1682, 1262²: ≈ 7.2 GB < 8 GiB).
Measured per-entry cost (`_partition_labels`, rectangle band model N=1682, 1262²): RSS 184–190 B per live-pattern entry (10.5 M and 16.9 M
entries), ~215 B per history node; 256 B budgets that. The check now also fires when the history passes the limit, so the history
stays at limit + one refinement step (was up to 4× limit).

Centered N=1682, **all limits DEFAULT** (isolated process, `/usr/bin/time -l`), Python 3.14:

| S | validate-data | compare | exit | comparison output |
|---|---|---|---|---|
| 1262 | 69.2 s / 1126 MiB | **584.4 s / 3497 MiB** | 3 (density/unit-area unconfirmed) | 431 MB folder |
| 1500 | 87.2 s / 1167 MiB | **746.6 s / 3735 MiB** | 3 | 433 MB folder |

Import: 245 s / 840 MiB (S=1262), 334 s / 841 MiB (S=1500) per model. /tmp data deleted afterwards.
Tests: full pytest green (test_handoff excluded); Py3.8 `/tmp/py38` run of test_scale_guards, test_summary_mode,
test_linepixel_equivalence, test_real_hotspot: all passed.
Not measured: the exact peak live-pattern count of the benchmark (RSS equals the earlier 10 M-entry run, so it is well under 24 M).

## T7 — Phase A review blockers (2026-10-22)
Fixes R1–R5, A1–A4 (details in the T7 worker_done). Regression found while re-benchmarking: after the R3 charge for retained
output, `validate-data` (and the stage-4/6 loaders) priced `compare.full_bucket_detail` output through `configured_dataset`'s
default and was refused with `oversized_real_operation` (9.84e9 B > 8 GiB already at N=300), which `validate-data` reports as
`invalid_configured_input` with exit 3 — indistinguishable from the usual partial exit. A first N=1682 run "finished" with that
hidden refusal (validate 65 s / 764 MiB = nothing read). Fixed: `configured_dataset(top_buckets=DEFAULT_TOP_BUCKETS)` admits summary
mode by default (`None` = full), `validate-data` uses the compare section's detail; the benchmark now flags a refused input in
`warnings` and stops. Tests `test_configured_dataset_admits_summary_by_default_and_full_only_when_asked`,
`test_validate_data_admits_what_the_summary_compare_keeps` (both fail without the fix).

Centered N=1682, S=1262, **all limits DEFAULT**, Python 3.11 (.venv, numpy 2.x), macOS, `/usr/bin/time -l`, one layer:

| command | wall | peak RSS | exit | note |
|---|---|---|---|---|
| import-hotspots reference | 244.0 s | 843.6 MiB | 3 | 58,870 files / 292 MB hashed, 0 mask decodes |
| import-hotspots target | 242.0 s | 837.7 MiB | 3 | same counts |
| validate-data | 68.9 s | 1123.7 MiB | 3 (`csv_mismatch`, fixture CSV area constant) | 107,648 mask decodes (every observed mask once: manifest validation only; no area compute here) |
| compare | **586.6 s** | **3923.3 MiB** | 3 (`density_contract_unconfirmed`, `unit_area_basis_unconfirmed`) | 215,296 mask decodes (2 models); output 431 MB |

Hash/decode evidence (A4, env `SIMCOMPARE_IO_COUNTERS`, per process): compare hashed 107,650 files / 263 MB and decoded
215,296 masks = 2 × 107,648. Per model there are 1682 cases × 32 diameters = 53,824 observed masks, 107,648 across both models, so compare decodes every observed mask **twice**: once in `load_real_manifest` validation and once in `compute_areas` (a mask is decoded once per pass and never re-decoded within a pass; `test_io_counters_count_hashes_and_decodes_when_enabled` asserts the two passes). Compare RSS is ~0.4 GiB above
the T6 figure (3497 MiB): a py3.8 pytest run overlapped part of this benchmark and the interpreter differs (T6: 3.14) — not attributed
to a code change; wall 586.6 s vs T6 584.4 s. /tmp data deleted by the script.
Tests: full pytest green (test_handoff excluded); `/tmp/py38` (PYTHONPATH=src) run of test_scale_guards, test_summary_mode,
test_linepixel_equivalence, test_real_hotspot, test_comparison_v2: all passed. Bundles rebuilt after the last source change.
