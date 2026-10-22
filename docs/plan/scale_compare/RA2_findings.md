RA2 VERDICT: FAIL (read-only review complete; HEAD 01989dee95b88ea8c34a074256afeb93c3650e74).

BLOCKERS

B1 / R4 incomplete — publication cleanup can permanently tear the report pair.
Evidence: src/simcompare/pipeline.py:218,235-245,256-260. _clear_journal deletes .comparison.json.prev, then .comparison.csv.prev, then the journal, and runs outside the BaseException/rollback try. Interrupt after the first backup deletion leaves NEW_JSON + NEW_CSV, a journal claiming both previous files exist, and only OLD_CSV backup. The next begin() calls _rollback(), skips the missing JSON backup, restores OLD_CSV, and removes the journal: final NEW_JSON + OLD_CSV with no recovery material. Reproduced with the actual _publish/_rollback methods and a dictionary-backed filesystem (no disk writes), injecting KeyboardInterrupt before deleting .comparison.csv.prev. Existing tests/test_comparison_v2.py:245-278 cover replacement failures and a kill between replacements, but never interruption during commit-marker/backup cleanup. Make the commit decision durable before destroying rollback material; cover interruption/failure at every cleanup boundary, including restart recovery. docs/usage.md:330 currently promises stronger behavior than the implementation provides.

B2 / R2 incomplete — the component-count prepass and component construction are not bounded by the stage-4 estimate.
Evidence: src/simcompare/electrode_map.py:54-56,408-412 and src/simcompare/adapters/real_hotspot.py:659-660,678-680. _component_sizes materializes argwhere(...).tolist() plus a Python set of coordinate tuples BEFORE guard_stage_map_regions runs; the estimate does not charge that scratch. A dense 400x400 one-case/two-electrode dataset has area admission 14,879,216 B and initial stage-4 admission 5,920,000 B. At max_operation_bytes=16 MiB the actual map path reaches a traced peak of 39,778,878 B before finally raising oversized_real_operation (region estimate 31,552,768 B). At 32 MiB, both guards pass and compute_electrode_maps succeeds with a traced peak of 60,556,186 B. _component_sizes alone peaks at 37,851,256 B. These are incremental traced allocations, not total process RSS. Thus a job limited to its declared budget can OOM before the intended fail-fast error, or pass despite materially exceeding the estimate. Count with bounded storage or price counting/construction scratch before allocating it; add dense-mask/low-budget coverage in addition to the >32-fragment test.

ADVISORIES

A4 evidence interpretation: docs/plan/scale_compare/T5_benchmark.md:106-107 says each observed mask was decoded exactly once/no re-decode. There are 1682*32=53,824 masks per model, 107,648 across both models, but compare records 215,296 decodes: twice per observed mask (load_real_manifest validation + compute_areas). Instrumentation and counts are useful and correct; fix the interpretation. tests/test_scale_guards.py:473-476 itself establishes the two passes.

New public-API default mismatch: src/simcompare/adapters/real_hotspot.py:755 defaults admission to top-20 summary, while src/simcompare/linepixel.py:704 defaults compute_areas to full. Direct configured_dataset(...); compute_areas(dataset) therefore admits summary and executes full. An in-memory 12-case/24x24 manifest produced summary estimate 7,899,840 B, full estimate 73,506,240 B, limit 40,703,040 B: default loading and default full computation succeed, while explicit full admission rejects. This is an API consistency/admission caveat; the ETX CLI full branch is correctly wired (pipeline.py:438-442 passes detail={} -> top_buckets=None). Preserve safe direct-call semantics or explicitly bind/check the execution mode against admission.

FIX VERIFICATION

R1 fixed: one shared DEFAULT_MAX_LABEL_PATTERN_ENTRIES=24,000,000 in numeric_schema.py:21, used by admission/provenance/execution. test_default_label_budget_is_one_value_everywhere (test_scale_guards.py:401) would fail on RA's 50,000,000 execution default; reconstructed the old candidate in memory and confirmed that old constant.

R2 original full-frame region-count issue is addressed: sizes are counted and cumulative regions checked before _components, and test_stage4_region_count_is_bounded_by_what_is_allocated (test_scale_guards.py:438) covers >32 actual regions. It does not cover B2.

R3 fixed for CLI/stage 6: retained output is 32*sum(per_layer)*100 B (real_hotspot.py:606-617); stage 6 sums both retained results and uses top-1 execution (local_delta.py:378-382). Tests test_estimator_arithmetic_at_real_scale, test_estimator_scales_with_layers_and_bucket_budget, test_stage6_guard_charges_retained_output_of_both_models, and test_stage6_models_retain_only_what_the_guard_charged cover the change. Reconstructed RA full estimate 7,225,579,172 B vs current 10,525,579,172 B at N=1682/S=1262; summary now 7,811,835,172 B.

R4 ordinary replacement-failure rollback is covered by the added tests, but B1 remains.

R5 fixed: prospective live-entry pricing occurs before appending history nodes (linepixel.py:403-408), and the threshold respects small limits. New production-threshold tests at limits 1/50/500/5000 pass; the reconstructed RA function reached peak_history=59,556 for every one, so those tests fail on the old code. The exact-boundary test also passes.

A1 fixed by frame-bound cache validation (measurement.py:133-141); test_measurement_cache_is_bound_to_the_frame covers equal window bounds with changed validity.
A2 fixed: frozen oracle _validate_case is independent; AST comparison confirms every locally defined oracle function/class matches HEAD 01989de, including validation and measurement.
A3 fixed: docs/usage.md:232 arithmetic matches the current estimator (7.8118 GB summary, 10.5256 GB full).
A4 counters and regression coverage are present; documentation caveat above.

Extra validate-data/default-loader fix is correct for CLI: pipeline.py:623-625 follows compare detail, and _configured_real maps full {} to explicit None. New summary/full admission and validation regressions cover this; an in-memory binding probe accepted summary and rejected explicit full at the same intermediate limit. No weakening found in the CLI full path (direct API caveat above).

INDEPENDENT VALIDATION / ETX

- .venv: 53 selected equivalence/summary tests passed in 94.78 s; six additional pure estimator/stage-6 tests passed.
- /tmp/py38: same 53 selected tests passed in 121.62 s.
- Selection excluded temporary-file fixture tests to honor strict read-only ownership; full integration/full-suite/benchmark results remain supplied evidence, not rerun claims.
- Actual full-v2 side serializer, decoded in memory, exactly matched the frozen oracle; actual _write_csv output in memory was identical for summary/full (49,114 UTF-8 bytes on oracle_cases).
- Decoded every runner payload, compared all src/simcompare Python files byte-for-byte, and verified every embedded file hash plus aggregate source hash. All seven match current src; common payload source hash 9afc15cec1ab976a58d8a822efb3de5087cc54bece325cb8fda1c09704b0fc66.
- Reviewed stage-2 config, usage/restart instructions, stage runner generation, legacy-mask coverage, and stage-4/6 routes. No separate runner/config numeric blocker found beyond B1/B2.
- git diff --check passes; HEAD unchanged; no files created, edited, deleted, or committed.