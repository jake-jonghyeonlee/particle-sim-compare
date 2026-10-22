# T7 — Fix RA review blockers (Phase A). Risk tier HIGH. Worker: Sonnet 5.5 high (estimator/memory-model design across modules).
Baseline: HEAD 01989de + uncommitted T1–T6 (do not revert anything). Review RA (Codex Astra xhigh) verdict FAIL; findings verbatim below.
Ownership: src/simcompare/linepixel.py, src/simcompare/measurement.py, src/simcompare/adapters/real_hotspot.py, src/simcompare/pipeline.py,
src/simcompare/electrode_map.py (only region-count bound/guard, no algorithm change), tests/*, scripts/benchmark_scale_compare.py,
docs/usage.md, configs/etx/*, docs/plan/scale_compare/T5_benchmark.md, bundles/ (rebuild only via build scripts at the end).
Fix every blocker; each needs a regression test reproducing the reviewer's scenario (fails before, passes after):
R1 Default label budget differs between admission (24M in adapter) and execution (50M in linepixel.py:35,719-721). Resolve the default
   once; same effective value in provenance, admission, execution, docs.
R2 Stage-4 admission assumes ≤32 connected regions (real_hotspot.py:548,617-619) but electrode_map.py:386-408 allocates a full-frame
   mask per component. Enforce a real bound on actual region allocation (count components before allocating full masks, e.g. via
   label counts, and fail fast with oversized_real_operation naming stage-4 regions) or a conservative bound — no fixed guess.
R3 Bucket output retained across all groups (and both models in stage 6) is charged once as transient (real_hotspot.py:569-577,634-637;
   linepixel.py:766-770). Account for retained per-group output over the whole model (×2 for stage 6) incl. serialization expansion,
   or release/serialize groups during computation. Summary mode retains only top-N, so its estimate must reflect that.
R4 pipeline.py:176-179 publishes JSON then CSV with two renames; a failure/interrupt between them leaves NEW_JSON+OLD_CSV. Make
   publication all-or-nothing for the pair (e.g. back up previous generation and restore on failure, or a single generation switch)
   and test failure/interrupt at each replacement boundary.
R5 linepixel.py:360,376-385,403-407: initial/floored check threshold 65,536 ignores smaller live limits; make the check honour small
   limits and budget the one-step overshoot; test with production-default threshold (no monkeypatch).
Advisories (do them, they are small):
A1 measurement.py:126-132 area_cache keyed only by window bounds — include frame identity/valid hash or bind the cache to one frame.
A2 tests/reference_linepixel.py imports src _validate_case — freeze the 01989de _validate_case in the oracle.
A3 docs/usage.md:232 stale "≈2.2 GB" arithmetic — correct using the final formula (state full vs summary).
A4 benchmark lacks hash/decode counts — add counters (e.g. env-gated instrumentation) or record the evidence gap explicitly.
After fixes: rerun scripts/benchmark_scale_compare.py centered N=1682 S=1262 with DEFAULT limits (must finish; report wall/RSS; delete
/tmp data), full pytest green except test_handoff, /tmp/py38 run of the scale/summary/equivalence/real_hotspot/comparison_v2 tests,
rebuild bundles; append results to T5_benchmark.md. No commit.
worker_done: per finding what changed + test name, measurements, deviations.
