# T1 — linepixel single-pass labelling (Phase A). Risk tier HIGH. Worker: Sonnet 5.5 medium.
Read first: docs/plan/scale_compare/plan_v2.md (authoritative) and plan_v1.md §2.1, §2.2, §2.6, §3 (oracle), §5.
Target: repo /Users/jonghyeonlee/git/particle-sim-compare, current worktree, branch feat/template-export, HEAD 01989de
(clean except untracked docs/plan/scale_compare/).
Ownership (allowed writes): src/simcompare/linepixel.py, src/simcompare/measurement.py (optional per-group cache kwarg),
src/simcompare/numeric_schema.py ONLY if a new error code/limit constant must be registered, tests/reference_linepixel.py (new),
tests/test_linepixel_equivalence.py (new), existing tests ONLY where they must adapt to packed-mask properties (explain each).
Do not touch adapters/, pipeline.py, local_delta*, electrode_map.py, bundles/, configs/ (later Tasks).
Change: replace shares/_membership_counts with bounded partition-refinement labelling (plan_v2 B1, B2), packed retained masks
behind read-only properties, raw deep-copied once per case, per-group cell-area cache with bit-identical floats, no bitorder.
`max_bucket_output_entries`: implement the budget check in linepixel with a module default and a keyword parameter on
compute_areas (wiring from YAML assignment is T3's job); error 'oversized_real_bucket_output'.
Constraints: AreaResult.to_dict() and all CSV rows byte-identical to the reference on every oracle case; public names kept;
Python 3.8 syntax; numpy >= 1.16 APIs only in src; no commit.
Acceptance:
- `.venv/bin/python -m pytest -p no:warnings` full suite green except the known tests/test_handoff.py sibling-dirty failure.
- Equivalence suite per plan_v1 §3 + plan_v2 B1/B2 cases (≥ 200 seeded randomized iterations).
- Micro-benchmark script output (tests or scripts/, your choice, not in the default test run): 100 pairs × 1262², seeded blobs,
  new vs reference wall time and peak memory; report actual numbers.
- Isolated Python 3.8 run per plan_v2 B6 of tests/test_linepixel.py, tests/test_linepixel_equivalence.py, tests/test_failure_pieces.py.
- worker_done: files modified, test counts, benchmark numbers, any deviation from the plan with reason.
