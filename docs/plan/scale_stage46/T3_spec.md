# Phase B T3 — stage 6 compute + stage-2 mask artefact (Q3). Risk tier HIGH. Worker: Sonnet 5.5 high. All validation in the FOREGROUND.
Read: docs/plan/scale_stage46/plan_v3_1.md (authoritative: B3, B5, B6, Q3, A4, A5), plan_v3.md §1 stage 6 and §2.3, PC2_findings.md,
T2_report.md (what T2 changed). Baseline: HEAD 7bcb07a + uncommitted Phase B T1 + T2 — keep all.
Ownership: src/simcompare/local_delta.py (compute paths; NOT the writers :700-748 — T4), src/simcompare/local_delta_report.py (_Assets, thumbs
data; not the HTML string writer — T4), src/simcompare/pipeline.py (stage-6 call sites :954-983 and the stage-2 compare publication to add the
artefact), src/simcompare/adapters/real_hotspot.py (stage-6 guard only), src/simcompare/linepixel.py ONLY for artefact (de)serialisation of
packed group masks (no numeric change), tests (new + stage-6 tests), docs/usage.md stage-2/6 sections.
Change:
1. B5/B6: invocation-scoped, owner-scoped unpack cache for group masks; named-case index (identical match order) + per-pair record cache;
   lazy _Assets with bounded layout LRU that reproduces today's failure order and reasons exactly; keep load_real_manifest authentication.
2. B3: stage-6 admission after correspondence loading/binding (P ready/blocked, validated sizes), before compute; phase-wise peak (max of phases)
   with all terms listed in plan_v3_1 B3, largest frame of both models; same guard in local_delta.py:375-377 and local_delta_report.py:284-286.
3. Q3 artefact: stage-2 compare additionally publishes packed per-group masks stage 6 needs (+ the comparison input hash and code/version
   tag) INSIDE the existing Phase A publication transaction (comparison.json/csv bytes unchanged); stage 6 reuses it when present and matching,
   else recomputes compute_areas (stage-2 outputs already produced on ETX without the artefact stay valid). Test reuse == recompute exactly;
   test stale/corrupt artefact → recompute (or fail closed if partially published — follow the transaction rules).
Acceptance: T1 stage-6 equivalence green (results and files, no intended deviation), guard boundary tests (tracemalloc peak ≤ estimate; one
byte below refuses with zero expensive calls; asymmetric frames; eviction; oversized P/sizes) on 3.11 and /tmp/py38; I/O counters show bounded
unpacks/decodes; in-process benchmark N=300, 1262², P=50 and 200, 3 sizes, jitter: RSS/time split into load-validation / compute-or-reuse /
windows / report; full pytest green except test_handoff and the stale-bundle test. No bundle rebuild, no commit. worker_done per item + numbers;
write docs/plan/scale_stage46/T3_report.md.
