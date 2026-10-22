# T4 — comparison summary mode (Phase A, v2.1). Risk tier HIGH. Worker: Sonnet 5.5 medium.
Read first: docs/plan/scale_compare/plan_v2.md (incl. v2.1) and docs/plan/scale_compare/T2_payload.md.
Baseline: HEAD 01989de + uncommitted T1–T3 (see git status; do not revert anything).
User decision: real compare writes a SUMMARY by default; full bucket lists only if the config sets `full_bucket_detail: true`.
Ownership: src/simcompare/linepixel.py (compute_areas/_group/shares option), src/simcompare/pipeline.py (real compare path + v2 writer),
src/simcompare/adapters/real_hotspot.py (estimator/budget wiring only), config schema validation wherever top-level/compare
options are validated, tests (new + adapt tests/test_comparison_v2.py and real compare tests), configs/etx/real.stage2.example.yaml
(+ stage4 example if it shares the key), docs/usage.md. Do not touch bundles/ (T5), electrode_map/local_delta algorithms.
Change:
1. Config key (choose the natural place, e.g. top-level `compare: {full_bucket_detail: false, top_buckets: 20}` — validate types,
   reject unknown keys like the rest of the config). Default false / 20.
2. compute_areas gains a bucket-detail option. Summary mode: still computes every group area exactly as today (line, pixel,
   transfer, union, pieces, fractions, coverage, source_cases measurement) and bucket counts (number of exclusive/shared buckets,
   their total area — must equal the share area), but builds pair_lineage/contributors ONLY for the top-N buckets per class by
   area (ties broken by the current bucket sort order), so time/memory no longer scale with the full bucket list.
   The bucket output budget applies to what is actually emitted. Full mode == current T1–T3 behaviour, byte-identical.
3. v2 writer: summary mode writes schema 'real_hotspot_comparison_v2' with `bucket_detail: "summary"` (+ top_buckets value) and,
   per class, `bucket_count_exclusive`, `bucket_count_shared`, `top_buckets: [...]` instead of full exclusive/shared lists;
   full mode writes `bucket_detail: "full"` exactly as T2. comparison.csv unchanged in both modes.
   The v2->v1 test decoder must refuse summary files with a clear error.
4. Estimator/budget: in summary mode the budget formula counts only top-N output; label/pattern memory stays bounded as in T1.
Constraints: no numeric change to any area; Python 3.8; numpy>=1.16; publish-on-success preserved; no commit.
Acceptance:
- Full pytest green except test_handoff (sibling dirty) and test_stage_runners_are_current_and_parse (stale bundles).
- Full mode: T1 equivalence + T2 decoder tests still pass unchanged. Summary mode: group areas/CSV identical to full mode on
  fixtures and on ≥ 50 randomized cases; top-N lists equal the first N of the full ordering by area.
- scripts/make_compare_fixture.py --size 1262 one layer, N=300 summary mode with DEFAULT limits: completes; report wall, isolated peak
  RSS, comparison.json size (target ≤ 50 MB, ≤ 2 GB RSS; report actual).
worker_done: files, tests, measurements, deviations.
