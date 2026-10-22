# T9 — Fix RA3 blockers (Phase A). Risk tier HIGH. Worker: Sonnet 5.5 high. Run all validation in the FOREGROUND.
Baseline: HEAD 01989de + uncommitted T1–T8 (keep everything). Reviewer text: docs/plan/scale_compare/RA3_findings.md (read it fully).
Ownership: src/simcompare/pipeline.py (recovery), src/simcompare/adapters/real_hotspot.py + src/simcompare/electrode_map.py (stage-4 region
pricing/bounding only), src/simcompare/measurement.py only if needed to bound scratch without numeric change, tests/test_comparison_v2.py,
tests/test_scale_guards.py, tests/test_component_labels.py, docs/usage.md (~l.330), bundles/ (rebuild at end).
B1 Recovery must fail closed: in _recover/_rollback (pipeline.py:298-307) an unreadable/corrupt journal or marker (OSError, ValueError, partial
   JSON) must NOT discard backups/journal; raise a clear error (e.g. NumericContractError('comparison_recovery_unreadable', path + remedy))
   and leave all files untouched. Only a successfully read journal may drive roll-back/forward. Add fault injection on EVERY read in
   recovery (journal read, marker check, backup stat) in the torn state, plus "second fault-free recovery repairs it" tests.
B2 Stage-4 region construction underpriced for connected comb masks (reviewer repro: 200x200, active[:, ::2]=True; active[0,:]=True; one region,
   19,901 runs; estimate 6,528,016 B vs traced peak 11.25 MB). Price per-run window objects + measure_union rectangle scratch (measure bytes per
   run on 3.8/numpy1.21 and 3.11, use the larger with margin), or bound by building/measuring regions in a run-count-limited way; the guard must
   fire before construction. Add the comb case: at limit = estimate it succeeds with traced peak <= estimate; one byte below fails fast with
   oversized_real_operation before the large allocation. Keep the dense 400x400 test passing. No numeric change to regions/maps.
Advisory: docs/usage.md:330 and pipeline.py:99-103,193-195 — scope the "previous files untouched" promise to failures before the commit decision;
   describe the recovery interval accurately.
Acceptance: full pytest green except test_handoff; /tmp/py38 run of tests/test_comparison_v2.py tests/test_scale_guards.py tests/test_component_labels.py
tests/test_electrode_map.py tests/test_summary_mode.py tests/test_linepixel_equivalence.py; bundles rebuilt. worker_done per finding + test names. No commit.
