# T5 — real-scale benchmark, Python 3.8 run, runner rebuild (Phase A close). Risk tier NORMAL (no src logic change). Worker: Sonnet 5.5 medium.
Read first: docs/plan/scale_compare/plan_v2.md (incl. v2.1), T2_payload.md. Baseline: HEAD 01989de + uncommitted T1–T4.
Ownership: scripts/benchmark_scale_compare.py (new; may reuse scripts/make_compare_fixture.py), bundles/ (ONLY via
scripts/build_single_file.py and scripts/build_etx_stage_runners.py), docs/usage.md ETX section, configs/etx/* comments,
docs/plan/scale_compare/T5_benchmark.md (results). Do not change src/ logic; if a src defect is found, stop and report it in
worker_done with evidence (outcome failed) instead of fixing.
Change / Acceptance:
1. Benchmark script: generates per model N cases at crop S (default 1262) with 32 diameters, one layer, TWO overlap profiles:
   (a) "scattered" (current make_compare_fixture behaviour) and (b) "centered": every case's hotspot band sits near its crop centre
   with random jitter ±50 px and diameter-growing size, so most cases overlap (models the real crops). Distinct electrode pair per case.
   Runs through the CLI as separate processes: import-hotspots (both models) -> validate-data -> compare (summary default).
   Reports per command: wall, isolated peak RSS (/usr/bin/time -l per process), output sizes, exit code, error code if any.
2. Run: centered N=300 and N=1682 at S=1262 (the real per-model scale), scattered N=300; also centered N=1682 at S=1500 if
   N=1682@1262 takes < 40 min total. Keep generated data under /tmp and delete it at the end (report disk used).
   If a run stops with a limit error (e.g. oversized_real_bucket_output from the live-pattern budget), record it verbatim, then
   rerun once with the limit raised enough to finish and record the raised value, time and RSS.
3. Python 3.8: isolated venv /tmp/py38 already exists (python 3.8.20, numpy 1.21.6, Pillow, PyYAML, pytest). Run
   `PYTHONPATH=src /tmp/py38/bin/python -m pytest -p no:warnings tests/test_real_hotspot.py tests/test_linepixel_equivalence.py
   tests/test_comparison_v2.py tests/test_summary_mode.py tests/test_scale_guards.py tests/test_linepixel.py tests/test_failure_pieces.py`
   and report counts (the oracle tests needing numpy>=1.17 bitorder are allowed to be skipped if they are marked so).
4. Rebuild all runners; tests/test_etx_examples.py and tests/test_single_file.py pass; run bundles/etx stage-2 runner once on the
   N=300 centered data under /tmp/py38/bin/python (copy runner to the work dir, edit JOBS/BASE_DIR in the copy) and report log tail.
5. Write docs/plan/scale_compare/T5_benchmark.md: table of results + extrapolation notes; update docs/usage.md ETX section
   (restart from stage 2, replace all 6 runners, new config keys, expected time/RAM).
Full pytest green except test_handoff (sibling dirty). No commit.
worker_done: files, numbers, deviations.
