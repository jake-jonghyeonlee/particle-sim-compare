# Phase B T5 — compare CLI publication fix, real-scale stages 1–6 benchmark, runners, docs. Risk tier HIGH (ships runners). Worker: Sonnet 5.5 medium.
All validation in the FOREGROUND. Read: docs/plan/scale_stage46/plan_v3_1.md (B4, A4, A5), T2/T3/T4_report.md, docs/plan/scale_compare/T5_benchmark.md.
Baseline: HEAD 7bcb07a + uncommitted Phase B T1–T4 — keep all.
Ownership: src/simcompare/cli.py + src/simcompare/pipeline.py ONLY for item 1; scripts/benchmark_scale_compare.py (extend to --stages 1-6),
tests (new + compare publication tests), configs/etx/* comments + README, docs/usage.md ETX section, bundles/ (rebuild via scripts/build_single_file.py
and scripts/build_etx_stage_runners.py), docs/plan/scale_stage46/README.md (checkpoint) and T5_benchmark.md (results).
1. Compare CLI fix: T4 found cli.py's failure handler deletes reports on any exit-2 error, destroying the previous comparison generation that
   the Phase A _ComparisonStream preserved. Apply the same keeps_previous_outputs contract to stage-2 compare (errors after its publication
   opened must not delete comparison.json/csv/comparison_masks.zip). Fault tests at CLI level for compare like T4's test_stage6_writers.
2. Benchmark per plan_v3_1 B4 through the CLI, isolated per-command /usr/bin/time RSS + IO counters: N=1682/model, crops 1262² and 1500²,
   centered/high-link profile AND many-separated-components profile, shared/distinct electrodes, asymmetric target frame, P=50 and P=200
   (stage-5-produced fixture + labelled correspondence fixture with both endpoint bindings and repeats), 3 window sizes + jitter; assert ready
   ROI/query/confirmed-pair counts, exact row counts, named-pair/jitter/explained branches ran; a guard-refusal boundary run. Stage 6 with and
   without the stage-2 artefact. Report per stage wall/RSS/output bytes. Keep data under /tmp and delete afterwards (report disk peak).
   If the full matrix exceeds ~3 h wall, run 1262² fully and 1500² for centered P=200 only; say what was skipped.
3. /tmp/py38 (PYTHONPATH=src) run of stage 3–6 test files + equivalence; N=300 1262² stage 1–6 pipeline once under py38 with the rebuilt runners
   (copy runners to the work dir, edit JOBS/BASE_DIR in the copies).
4. Rebuild all runners; update configs/etx comments ('not yet streaming' etc.), docs/usage.md ETX section (restart instructions, new outputs
   comparison_masks.zip, slim review_rois, expected time/RAM), checkpoint README.
Acceptance: full pytest green except test_handoff; stale-bundle test now green; results in T5_benchmark.md. No commit. worker_done with numbers.
