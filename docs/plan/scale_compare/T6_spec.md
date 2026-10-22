# T6 — default limits that finish real-scale summary compare (Phase A). Risk tier HIGH (resource limits). Worker: Sonnet 5.5 medium.
Baseline: HEAD 01989de + uncommitted T1–T5 (incl. rebuilt bundles). Read docs/plan/scale_compare/T5_benchmark.md first.
Problem (T5 evidence): centered N=1682 S=1262 one layer with DEFAULT limits stops in compare with
'oversized_real_bucket_output: L1 d=3.0: 1669777 entries; limit 1000000' although summary mode emits only top-N buckets.
Cause: the label partition's live-pattern limit reuses max_bucket_output_entries (linepixel.py ~l.676-681,739) and the estimator
charges ~1,100 B per budget entry (real_hotspot.py ~l.591, 767) — that is the cost of emitted Python output objects, not of
predecessor-encoded live patterns. With 10M entries the run used only 3.3 GiB RSS.
Ownership: src/simcompare/linepixel.py, src/simcompare/adapters/real_hotspot.py (estimator/limits), tests (new + adapt
tests/test_scale_guards.py, tests/test_summary_mode.py, tests/test_linepixel_equivalence.py only if limit names change),
configs/etx/*.yaml comments, configs/etx/README.md, docs/usage.md, scripts/build_etx_stage_runners.py text if needed, bundles/ (rebuild only).
Change:
1. Separate the two budgets: keep `max_bucket_output_entries` for emitted output (full mode lists / summary top-N), add
   `max_label_pattern_entries` (new condition_assignment key, validated like the others, documented) for the live-pattern
   bound of the exact partition. Measure bytes per live-pattern entry (predecessor arrays + memo) and use that measured
   constant in the estimator. Choose defaults so that the T5 centered N=1682 S=1262 AND S=1500 runs finish with all limits at
   DEFAULT values within the default max_operation_bytes (8 GiB) — measure, don't guess; keep a clear error for genuinely
   larger inputs ('oversized_real_label_patterns' with layer, diameter, count, limit, remedy).
2. Full mode behaviour and all numeric outputs unchanged (equivalence + decoder + summary tests green).
3. Rebuild bundles (scripts/build_single_file.py, scripts/build_etx_stage_runners.py).
Acceptance: full pytest green except test_handoff; scripts/benchmark_scale_compare.py centered N=1682 at S=1262 and S=1500 with
DEFAULT limits: exit 3, wall and isolated RSS reported (delete /tmp data after); Py3.8 /tmp/py38 run of tests/test_scale_guards.py
tests/test_summary_mode.py tests/test_linepixel_equivalence.py tests/test_real_hotspot.py green. Append results to T5_benchmark.md.
Also correct the T5 extrapolation note: cases are split across layers, so one-layer is the worst case (11 layers does NOT
multiply the one-layer N=1682 time by 11). No commit.
