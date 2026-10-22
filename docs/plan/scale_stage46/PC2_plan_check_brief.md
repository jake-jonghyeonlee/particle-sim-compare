# PC2 — Cross-model plan check for Phase B (read-only). Reviewer: Codex gpt-6-astra / xhigh.
READ-ONLY: no edits, no commit. Report findings, then worker_done.
Plan: docs/plan/scale_stage46/plan_v3.md (brief: P2_planning_brief.md). Base 7bcb07a. Phase A context: docs/plan/scale_compare/README.md,
plan_v2.md, RA*_findings.md (your earlier reviews — apply the same rigor).
Check with file:line evidence: (1) each stage-3..6 cost claim and root cause; anything missed (e.g. stage 3/5 footprints, report path,
electrode_png, confirm_pairs parse of large exports); (2) byte-identical equivalence of each design (§2.2 header-only validation & corrupt-npz
detection change, packed actives & count accumulation, names/links derivation, measure_union full-frame inputs & area_cache, LRU unpack,
named-case index, lazy _Assets, iterencode/streamed CSV/HTML byte equality, publish-on-success dir swap); (3) guard re-derivation feasibility
and fail-fast ordering; (4) task split/acceptance gaps (e.g. oracle strength, py3.8, benchmark realism: distinct pairs, many components,
differing frame sizes, P scale); (5) ETX: stage-1/2 outputs truly unaffected, runner/config changes.
Verdict ACCEPT / ACCEPT-WITH-CHANGES / REJECT with blocking vs advisory.
