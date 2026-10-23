# F-R2 — re-review after F3 (read-only). Reviewer: Claude Opus 5.5 / high (Codex unavailable). Risk tier HIGH.
READ-ONLY: no edits, no commit. Report findings, then worker_done.
Your/previous F-R findings: docs/plan/followups/R_findings.md (B1 blocker, A1-A6). Fix task: docs/plan/followups/F3_spec.md.
Candidate: working tree vs HEAD c7a2829; diff excluding bundles: docs/plan/followups/F_candidate_v2.diff. Focus on the F3 delta:
src/simcompare/pipeline.py (run_numeric_compare wrapper), src/simcompare/publication.py (Windows liveness), tests/test_compare_publication_cli.py,
docs/usage.md, docs/usage_reference.md, docs/plan/followups/F1_report.md; verify bundles embed current src.
Verify B1 with the reviewer's two repros (tampered manifest; moved data/imported/target) and that synthetic compare / unreadable config keep
today's behaviour; no regression of F1 items; docs now state exactly what the code does. Verdict PASS / PASS-WITH-ADVISORIES / FAIL.
