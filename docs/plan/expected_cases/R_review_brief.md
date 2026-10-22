# EC-R — independent review (read-only). Reviewer: Claude Opus 5.5 / high (Codex unavailable: 401). Risk tier HIGH.
READ-ONLY: no edits, no commit. Report findings, then worker_done.
Contract: docs/plan/expected_cases/T1_spec.md. Candidate: working tree vs HEAD d8f6dbb; diff excluding bundles:
docs/plan/expected_cases/EC_candidate.diff. Bundles (6 ETX runners + bundles/simcompare_job.py) rebuilt — verify embedded src.
Evidence: full pytest green except tests/test_handoff.py; /tmp/py38 test_real_hotspot + new tests green.
Check: discovery refactor is behaviour-identical (names, ordering, errors, images/ layout) for the imported root; union semantics and
diagnostics; mutual exclusion; manifest field + hash; legacy manifests without the key; stage-2/4 consumers (pipeline.py:469,784,845) and
stage-4 coverage now 'available' only with a real inventory source; runner JOBS correctness on ETX (relative paths, BASE_DIR); docs restart note.
Verdict PASS / PASS-WITH-ADVISORIES / FAIL with file:line and concrete failure scenarios.
