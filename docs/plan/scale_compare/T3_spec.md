# T3 — lazy records + streaming estimator + stage-4/6 admission guards (Phase A). Risk tier HIGH. Worker: Sonnet 5.5 medium.
Read first: docs/plan/scale_compare/plan_v2.md (authoritative: B3/B4 Phase A guard, A1, A3, A4) and plan_v1.md §2.3, §2.4.
Baseline: HEAD 01989de + uncommitted T1 (linepixel/measurement + tests) and T2 (pipeline.py v2 writer + tests/docs/scripts).
Ownership: src/simcompare/adapters/real_hotspot.py (configured_dataset ~l.590-760 and helpers), src/simcompare/numeric_schema.py
(RecordHeader if needed), src/simcompare/linepixel.py (only header use in _validate_case / compute_areas grouping and wiring
max_bucket_output_entries), src/simcompare/pipeline.py (only to pass assignment limits / stage guards), src/simcompare/electrode_map.py
and src/simcompare/candidates.py and src/simcompare/local_delta.py / local_delta_report.py ONLY to add the admission guard call
before heavy allocation (no algorithm change), tests (tests/test_real_hotspot.py limit tests ~l.200-215,365-375,436-460 + new),
configs/etx/real.stage2.example.yaml, configs/etx/real.stage4.example.yaml, configs/etx/README.md if present,
scripts/build_etx_stage_runners.py text at ~l.42-43 only (do NOT rebuild bundles; T4 does).
Change:
1. LazyRecords mapping per plan_v1 §2.3: header access without loading; __getitem__ loads via existing _load_mask + the same
   dtype/shape checks and error codes; no cache; geometry-unconfirmed records still become missing (A3). Plain dict records keep working.
2. compute_areas/_group consume records lazily: at most the masks of one layer×diameter group are loaded at a time, released after.
3. Estimator per plan_v1 §2.4 corrected by Astra PC1 B2: one-layer worst case formula, include label/patterns transient and
   output budget; per-model and for stage 6 two-model lifetime. Keep all config keys and error codes; document arithmetic in tests.
   Wire max_bucket_output_entries from condition_assignment (validated like other limits).
4. Phase A guards (plan_v2 B3/B4): before stage-4 electrode-map/candidates and stage-6 compare-windows/report allocate, estimate
   their eager footprint (electrode_map.py:359-380 representative records + per-electrode masks + stack; regions; candidates.py:451;
   local_delta_report.py:161-169,284 layouts of both models; both AreaResults) and raise
   NumericContractError('oversized_real_operation', '<stage>: estimated X bytes exceed limit Y; split cases or raise
   max_operation_bytes up to physical RAM; streaming support for this stage is pending') when above max_operation_bytes.
5. No extra validate-data touch pass (A1). Note in docs that physical-RAM detection is host RAM, not cgroup/job limit.
6. Existing stage-1 imports (.npz and legacy .npy) load unchanged — add a test using a sealed legacy import.
Constraints: identical numeric outputs (T1 equivalence suite + T2 v2 decoder tests must stay green); Python 3.8; numpy>=1.16; no commit.
Acceptance: full pytest green except test_handoff (sibling dirty) and test_stage_runners_are_current_and_parse (stale bundles);
seeded fixture (scripts/make_compare_fixture.py) at N=100 and N=300 with --size 1262, one layer: compare peak RSS and wall time
(isolated per-process) reported, target ≤ 2 GB at N=300; guard tests for stage 4 and stage 6; Python 3.8 isolated run
(plan_v2 B6) of tests/test_real_hotspot.py tests/test_linepixel_equivalence.py tests/test_comparison_v2.py.
worker_done: files, tests, measurements, deviations.
