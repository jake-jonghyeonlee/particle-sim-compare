# T8 — Fix RA2 blockers (Phase A). Risk tier HIGH. Worker: Sonnet 5.5 high.
Baseline: HEAD 01989de + uncommitted T1–T7 (keep everything). Full reviewer text: docs/plan/scale_compare/RA2_findings.md (read it).
Run all validation in the FOREGROUND (no background waits).
Ownership: src/simcompare/pipeline.py (publication/journal), src/simcompare/electrode_map.py (_component_sizes/_components),
src/simcompare/adapters/real_hotspot.py (stage-4 guard pricing; configured_dataset default), src/simcompare/linepixel.py (compute_areas
default binding only), tests/test_comparison_v2.py, tests/test_scale_guards.py (+ new tests), docs/usage.md (~l.330 publication promise),
docs/plan/scale_compare/T5_benchmark.md (decode-count text ~l.106-107), bundles/ (rebuild at end).
Fix:
B1 Publication cleanup (pipeline.py:218,235-245,256-260): make the commit decision durable BEFORE deleting rollback material (e.g. write
   a 'committed' marker/journal state first; cleanup after commit must never trigger rollback; recovery on next begin() must roll forward
   when committed, back when not). Test interruption/failure at EVERY boundary (each replace, marker write, each backup deletion, journal
   removal) including restart recovery: final state is always (OLD_JSON,OLD_CSV) or (NEW_JSON,NEW_CSV). Make docs/usage.md match exactly.
B2 Stage-4 component counting (electrode_map.py:54-56,408-412; real_hotspot.py:659-660,678-680): _component_sizes must count with bounded
   memory (e.g. scipy-free union-find/label on the bool array with an int32 label array, or scanline labelling — no argwhere().tolist()/
   Python set of tuples), and the stage-4 estimate must charge the counting scratch and component-construction peak before allocating.
   Add the reviewer's dense 400x400 one-case/two-electrode case: at a budget below the true peak it must fail fast with
   oversized_real_operation BEFORE the large allocation (assert traced peak stays within budget via tracemalloc); at a budget above the
   estimate it succeeds and the traced peak ≤ estimate.
Advisories: (a) T5_benchmark.md: masks are decoded twice per observed mask (validation + compute_areas) — fix the text; (b) direct API:
   configured_dataset default admission (summary top-20) vs compute_areas default (full) — bind them (e.g. dataset carries the admitted
   detail and compute_areas defaults to it, or refuses a mode costlier than admitted) and test it; CLI behaviour unchanged.
Constraints: no numeric change (equivalence/summary tests green); Python 3.8; numpy>=1.16; no commit.
Acceptance: full pytest green except test_handoff; /tmp/py38 run of tests/test_scale_guards.py tests/test_comparison_v2.py
tests/test_summary_mode.py tests/test_linepixel_equivalence.py tests/test_real_hotspot.py tests/test_electrode_map.py; bundles rebuilt;
quick re-benchmark NOT required unless src in linepixel/real_hotspot compute path changed (say which). worker_done per finding + tests.
