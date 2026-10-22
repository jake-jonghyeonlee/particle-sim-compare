# T2 — comparison.json v2 streaming writer with publish-on-success (Phase A). Risk tier HIGH. Worker: Sonnet 5.5 medium.
Read first: docs/plan/scale_compare/plan_v2.md (authoritative: B5, A2) and plan_v1.md §2.5.
Baseline: HEAD 01989de + uncommitted T1 (src/simcompare/linepixel.py, measurement.py, tests/reference_linepixel.py,
tests/test_linepixel_equivalence.py, scripts/benchmark_linepixel.py) — do not modify T1 files except linepixel.py if a
streaming accessor for to_dict pieces is strictly needed (explain).
Ownership: src/simcompare/pipeline.py (real compare path ~l.170-230 and a new writer near _write_inspection),
tests that read real comparison.json (tests/test_real_hotspot.py, tests/test_cli_e2e.py), new tests, docs/usage.md (real
comparison.json section ~l.301), scripts/benchmark_real_hotspot_compare_map.py if it reads changed keys, docs/plan/scale_compare/ notes.
Do not touch adapters/, electrode_map.py, local_delta*, bundles/, configs/.
Change:
1. First measure the v1 payload breakdown (bytes in raw, pair_lineage_case_keys, contributing_source_case_keys, rest) on a
   saved, seeded fixture (commit the generator, not the data) at two sizes; record numbers in docs/plan/scale_compare/T2_payload.md.
2. Real configured compare writes schema 'real_hotspot_comparison_v2': compact separators, per-side namespace,
   source_cases[*].raw -> raw_ref + side-level cases_raw written once; integer-index encoding of lineage/contributors
   (with per-group source_index) only if step 1 shows they exceed 30% of bytes — if used, round-trip pair_ids, lineage
   and contributors exactly. Never build the full v1 dict or asdict(raw) first; stream per group/per source with json
   encoding of small pieces. Reference side is fully computed+serialised to the temp file and freed before target compute.
3. Publish-on-success: write to a sibling temp file, fsync, os.replace only after both sides succeed; on any exception
   (target compute error, OSError incl. ENOSPC, KeyboardInterrupt) remove the temp file and keep any existing
   comparison.json. comparison.csv rows unchanged (same publish rule). exit_code 3 semantics unchanged.
4. Unconfigured real branch (pipeline.py ~l.246) and synthetic path unchanged; say so in docs.
5. Test-only decoder v2 -> v1 and a test asserting decode(v2) == reference v1 payload (only the outer schema_version differs)
   on fixtures; document the mapping in docs/usage.md.
Constraints: Python 3.8, numpy>=1.16 APIs, no new deps, no commit.
Acceptance: full pytest green except test_handoff (sibling dirty) and test_etx_examples::test_stage_runners_are_current_and_parse
(stale bundles, fixed in T4); recovery tests (target failure, simulated ENOSPC mid-write, interrupt, existing report preserved);
size reduction on the step-1 fixture reported (target ≤ 10% of v1 at the larger size; report actual); peak RSS of compare on
the step-1 larger fixture before/after reported (isolated per-process measurement).
worker_done: files, tests, measurements, deviations.
