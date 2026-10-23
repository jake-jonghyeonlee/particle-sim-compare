# F-R — independent review of F1 (code, HIGH) + F2 (docs) (read-only). Reviewer: Claude Opus 5.5 / high (Codex unavailable: 401).
READ-ONLY: no edits, no commit. Report findings, then worker_done.
Contracts: docs/plan/followups/F1_spec.md, F2_spec.md; source findings docs/plan/scale_stage46/RB_findings.md, docs/plan/expected_cases/R_findings.md.
Candidate: working tree vs HEAD c7a2829; diff excluding bundles: docs/plan/followups/F_candidate.diff. Bundles rebuilt — verify embedded src.
Evidence: F1 worker_done (all new tests fail before the fix), full pytest green except tests/test_handoff.py, /tmp/py38 touched files green;
F2 token check missing=0. Reports: docs/plan/followups/F1_report.md.
Check F1: each item truly fixed without changing published bytes/numerics; publication/recovery invariants still hold (no new torn states,
stale-temp sweep cannot delete a live run's files incl. on Windows pid reuse); admission changes fire before allocation; the oracle tolerance
in tests/test_stage46_equivalence.py (tuple vs list only at region coverage) does not weaken equivalence elsewhere.
Check F2: docs/usage.md accurate vs code/configs/runner JOBS (commands, folder names, manual steps, defaults, error codes, numbers), concise
ETX vs Windows split; nothing lost into nowhere (usage_reference.md), links resolve.
Verdict PASS / PASS-WITH-ADVISORIES / FAIL with file:line and concrete failure scenarios.
