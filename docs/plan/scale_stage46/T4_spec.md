# Phase B T4 — stage-6 writers + full-set publication transaction. Risk tier HIGH. Worker: Sonnet 5.5 high. All validation in the FOREGROUND.
Read: docs/plan/scale_stage46/plan_v3_1.md (authoritative: B2, A3), plan_v3.md §2.4, PC2_findings.md (B2, A3), T3_report.md, and the Phase A
publication protocol in src/simcompare/pipeline.py (_ComparisonStream) + docs/plan/scale_compare/RA2/RA3/RA4_findings.md (lessons).
Baseline: HEAD 7bcb07a + uncommitted Phase B T1–T3 — keep all.
Ownership: src/simcompare/local_delta.py writers (~:700-748 and helpers), src/simcompare/local_delta_report.py HTML/thumb writers,
src/simcompare/pipeline.py stage-6 orchestration (transaction ownership spanning numeric + HTML writers), tests (new + stage-6 writer tests),
docs/usage.md compare-windows section.
Change:
1. A3 streaming: local_delta.json / window_sensitivity.json via chunked iterencode with a list-subclass adapter (no whole-result to_dict/json_value
   copy), CSV rows written as produced (preserve first-seen key order per table, empty-table header fallback, rendering of null/bool/containers),
   HTML written progressively. Byte-identical to the frozen 7bcb07a writers (T1 oracle) for full and --compact-json, empty/one-size/rank cases.
2. B2 transaction: one coordinated writer owns the whole produced-file set (explicit allowlist incl. thumb/overview PNGs and _thumbs.json);
   old generation preserved as backups; durable commit marker; roll-forward after commit, roll-back before; fail-closed on unreadable state;
   unrelated files untouched. Reuse/extract the Phase A protocol rather than inventing a new one if practical. Fault injection at EVERY
   write/delete/replace/commit/cleanup/read boundary + restart recovery, with and without a previous generation, with unrelated files present.
   Docs promise exactly what the code guarantees (before commit → OLD set; after commit → NEW set; unreadable recovery → error, untouched).
3. Peak RSS of the report phase (T3 measured 1.2/3.0 GB at P=50/200, N=300) should drop; measure again.
Acceptance: T1 stage-6 equivalence (files byte-identical) on 3.11 and /tmp/py38; transaction fault tests on both; full pytest green except
test_handoff and the stale-bundle test; in-process benchmark N=300, 1262², P=50/200, 3 sizes, jitter: report-phase RSS and wall. No bundle rebuild,
no commit. worker_done per item + numbers; write docs/plan/scale_stage46/T4_report.md.
