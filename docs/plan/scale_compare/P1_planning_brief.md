# P1 — Planning brief (read-only). Planner: Claude Fable 5.1 / medium. Risk tier: HIGH (core calculation + resource limits).
READ-ONLY: do not edit code/tests. Write exactly one file: docs/plan/scale_compare/plan_v1.md. Then worker_done.

## Goal
Make stages 2–6 of the ETX runners (bundles/etx/simcompare_job_[2-6]_*.py) feasible on real data, with IDENTICAL numeric
results to today. Real scale per model: ~1,682 cases (58,862 files / 35), 32 diameters (DIAMETERS_UM), hotspot crop
1000–1500 px square (example 1262x1262; varies per model, constant within a model), 2 models (reference, target).
Layer distribution unknown (depends on electrodes) — plan for the worst case "all cases in one layer".
ETX: Linux server, Python possibly 3.8, numpy >= 1.16, Pillow; RAM unknown (assume 32–64 GB; must not require more than
~16 GB at the scale above with default limits, or must fail fast with a clear, actionable message).

## Measured evidence (coordinator, Mac M-series, synthetic data, 1270x1270 crops, all cases one layer; per model N cases)
- stage 1 import: linear, 0.15 s/case, ~170 KB disk/case after .npz change (done, committed 7a43d30).
- stage 2 compare: N=50 -> 197 s, 4.8 GB RSS, comparison.json 290 MB; N=100 -> 1,124 s, 10.3 GB RSS, comparison.json 1.1 GB.
  N=100 exceeds default max_operation_bytes 8 GiB (oversized_real_operation) unless raised.
- cProfile N=50: 83% in src/simcompare/linepixel.py `_group.shares` (l.388-401): for each distinct membership pattern
  it rebuilds `inside` by AND-ing every pair mask over the full frame (O(patterns x pairs x cells)), plus contributor
  checks per pattern; _membership_counts np.unique sort 15%; JSON encoding of to_dict ~7%.
- configured_dataset (src/simcompare/adapters/real_hotspot.py ~l.590-760) eagerly loads every observed mask of every
  case into RAM (bool, 1 B/px) -> ~170 GB at real scale; its resource estimator (max_materialized_source_cells,
  max_group_mask_cells, max_operation_bytes) rejects accordingly.
- GroupArea retains ~10 full-frame bool masks per group (line/effective/transfer/union/piece masks).
- comparison.json (pipeline.run_numeric_compare + _write_inspection, indent=2) grows ~quadratically; per-source `raw`
  deep copies and bucket pair lineage/contributors are suspects — measure, don't guess.

## Consumers to inspect (narrowly)
compute_areas callers: pipeline.py (l.129-167 rates, l.170-230 real compare, l.361-390 validate-data), local_delta.py l.364-376,
dl_results.py l.323-338, adapters/electrode_png.py l.262 (configured_dataset). comparison.json readers: final_report.py,
tests (test_final_report, test_cli_e2e, test_numeric_cli, test_numeric_report, test_real_hotspot), tests/golden/*.
Also: which stage runner calls what (bundles/etx JOBS blocks; configs/etx/*.yaml).

## What the plan must contain
1. Root-cause per bottleneck (time, RAM, disk) with file:line.
2. Design for each fix, preserving exact numeric results and existing JSON fields unless a change is unavoidable;
   if comparison.json schema must change, propose a versioned, backward-readable change and list every reader to update.
   Candidate ideas (evaluate, don't assume): single-pass pattern labelling for shares/contributors (O((pairs+sources) x cells));
   lazy per-group mask loading in configured_dataset with per-group release; packed-bit storage for retained masks;
   resource estimator matching the streaming model; compact JSON / no per-diameter raw duplication.
3. Task split (<= 4 Tasks, serial, one editor), each with Target files, Change, Constraints, Ownership, Acceptance, per-Task
   risk tier. Equality oracle: keep the current implementation as a test-only reference and assert identical to_dict()/
   CSV outputs on existing fixtures + randomized overlapping-mask property tests.
4. ETX acceptance: a scale benchmark script (extend scripts/benchmark_real_hotspots.py or new) that reproduces the
   coordinator measurement and states target numbers (time and peak RSS) for N=100 and an extrapolation to N=1,682;
   Python 3.8 + old numpy compatibility (uv python 3.8 with numpy 1.21 available locally); rebuilt runners.
5. Whether already-imported stage-1 outputs stay valid (so the user restarts from stage 2), and any config changes users need
   (configs/etx/*.yaml limits).
6. Risks, open questions for the user, and what is explicitly out of scope.
Keep the plan concise (<= ~250 lines). Do not read the whole repository; follow the pointers.
