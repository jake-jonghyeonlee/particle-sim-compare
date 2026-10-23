# F-R3 — re-review after F4 (read-only). Reviewer: Claude Opus 5.5 / high (Codex unavailable). Risk tier HIGH.
READ-ONLY: no edits, no commit. Report findings, then worker_done.
Previous findings: docs/plan/followups/R_findings.md (B1), R2_findings.md (B2). Fix task: docs/plan/followups/F4_spec.md.
Candidate: working tree vs HEAD c7a2829; diff excluding bundles: docs/plan/followups/F_candidate_v3.diff. F4 delta: src/simcompare/pipeline.py
(real-mode compare wrapper + flags on the two non-transactional writers), tests/test_compare_publication_cli.py, docs/usage.md, docs/usage_reference.md;
verify bundles embed current src.
Verify: B2 repro (ENOSPC in inspection CSV without condition_assignment; numeric_v2 writer in real mode) now removes managed reports (no torn
set); B1 repros still keep the previous generation; transactional path unchanged; docs exact. Keep scope to the F3/F4 delta unless you find
a regression elsewhere. Verdict PASS / PASS-WITH-ADVISORIES / FAIL.
