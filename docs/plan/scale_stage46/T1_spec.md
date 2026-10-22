# Phase B T1 — frozen 7bcb07a oracle for stages 4 and 6. Risk tier HIGH. Worker: Sonnet 5.5 medium.
Read: docs/plan/scale_stage46/plan_v3_1.md (authoritative; esp. A2, B5, B6), plan_v3.md §2.1, PC2_findings.md (A2).
Baseline: HEAD 7bcb07a, clean except untracked docs/plan/scale_stage46/. NO production code change in this Task.
Ownership: tests/reference_stage46/ (new), tests/test_stage46_equivalence.py (new), tests/stage46_generators.py (new), tests/conftest.py only if a
fixture must be registered. Run everything in the FOREGROUND.
Change:
1. Freeze byte-exact copies of 7bcb07a: src/simcompare/electrode_map.py, candidates.py, roi_export.py, confirm_pairs.py, local_delta.py,
   local_delta_report.py, plus every production helper they call that later Tasks may change (check pipeline.py helpers used by candidates and
   stage 6, measurement.py, numeric_images.py usage). Store bytes unmodified; a loader rewires their imports to the frozen package at import
   time (importlib), and a checked-in sha256 digest file verifies the frozen bytes (works without git).
2. Seeded generators (real-style datasets on tmp folders with LazyRecords, assets, assignments, pair tables, correspondences) covering plan_v3
   §2.1 cases + PC2 additions: same paths in different roots, same bytes opposite value_semantics, missing/invalid/mutated assets, multi-layer,
   asymmetric frames, comb/dense/many-component masks, shared/distinct electrodes, correspondences with both endpoint bindings and repeats,
   empty/all-blocked tables, non-ASCII reasons, compact/full writers.
3. Equivalence harness: run production vs frozen on the same inputs; compare ElectrodeMaps (asdict + array_equal), candidates output tree
   byte-for-byte, confirm-pairs outputs, LocalDeltaResult (== on floats) and every stage-6 written file byte-for-byte (normalise only
   documented timestamp/producer fields, list them).
4. Perturbation test: monkeypatch a production function with a tiny numeric change and assert the harness fails.
Acceptance: suites green against current HEAD on .venv (3.11) and /tmp/py38 (PYTHONPATH=src); runtime ≤ 3 min each; full pytest green except
tests/test_handoff.py (sibling dirty). worker_done: files, case counts, runtimes, any 7bcb07a behaviour that could not be frozen (why).
