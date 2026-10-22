# Phase B T2 — stage 4 streaming + stage-5 gate. Risk tier HIGH. Worker: Sonnet 5.5 high. All validation in the FOREGROUND.
Read: docs/plan/scale_stage46/plan_v3_1.md (authoritative: B1, B3 stage-4 part, B5, B6, A1, A5), plan_v3.md §1 stage 4/5 and §2.2, PC2_findings.md.
Baseline: HEAD 7bcb07a + uncommitted Phase B T1 (tests/reference_stage46, tests/stage46_generators.py, tests/test_stage46_equivalence.py) — keep.
Ownership: src/simcompare/electrode_map.py, candidates.py, roi_export.py, confirm_pairs.py, adapters/real_hotspot.py (stage-4/5 guards only;
do NOT change load_real_manifest, configured_dataset defaults, estimate_area_bytes, assignment hashes, resource_estimate provenance),
tests (new + stage-4/5 tests; you may extend T1 generators but not weaken the frozen oracle or its digests), docs/usage.md stage-4/5 sections.
Change:
1. Stage 4 per plan_v3_1 A1/B6: header-only metadata for lazy records; one representative load per case; packed actives; per-electrode OR then
   count accumulation; names/links from the same eligible links; measure_union with full-frame arrays + one area_cache per region; share one
   read-only coverage/raw/metadata copy instead of per-region/per-link deepcopies; drop full masks after packing; no new fields in to_dict.
2. B1 export: rows keep scalar coverage fields and `observed_ref`; full identity list written once in top-level `region_coverages`. FIRST check
   DL2 acceptance read-only: run particle-sim-dl2's validator (PYTHONPATH=/Users/jonghyeonlee/git/particle-sim-dl2/src, import only, do not edit
   that repo) on a slim export from a generator. If accepted under review_roi_v1 keep the version; else bump to review_roi_v2 and add a
   `--review-roi-format v1` compatibility switch, and report the DL2 follow-up. candidates must not re-read its own exports; stream file hashes.
   All readers in this repo (candidates, confirm_pairs, roi_export.check_*) accept both shapes. The T1 oracle compares content after expanding
   observed_ref (document this as the only intended deviation, with a test that expansion reproduces the old bytes exactly).
3. B3/B5: stage-4 admission prices packed cache, region build, links/coverage growth BEFORE allocation (both sides); invocation-scoped asset
   validation memo per B5; stage-5 pre-read gate on export file sizes with a measured parse multiplier.
4. Benchmarks in-process: N=300, 1262², one layer, shared/distinct electrodes, centered + many-component profiles: report RSS/time per side,
   export sizes old vs new, I/O counters (authentication passes + map-only loads).
Acceptance: T1 stage-4/5 equivalence green (with the documented observed_ref expansion), guard boundary tests (tracemalloc peak ≤ estimate;
one byte below refuses with zero expensive calls) on 3.11 and /tmp/py38, full pytest green except tests/test_handoff.py, sealed stage-1/2
regression unaffected. No bundle rebuild (T5). No commit. worker_done: per item what changed, DL2 check result, measurements, deviations.
