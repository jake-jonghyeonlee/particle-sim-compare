# F4 — fix F-R2 blocker B2. Risk tier HIGH. Worker: Sonnet 5.5 medium. FOREGROUND validation.
Base: HEAD c7a2829 + uncommitted F1–F3 (keep). Findings: docs/plan/followups/R2_findings.md (read B2 fully, incl. repro idea).
Problem: F3's wrapper (src/simcompare/pipeline.py:606-614) marks EVERY real-mode compare exception keeps_previous_outputs, including failures
inside the two NON-transactional real-mode writers (_write_inspection when no condition_assignment, ~:689; write_numeric_report for numeric_v2
data in mode real, ~:699). ENOSPC there now leaves new comparison.json + truncated comparison.csv + old comparison_masks.zip beside error.json.
Fix: flag keeps_previous_outputs only for errors raised BEFORE any output is written (source loading/validation: _real_source,
load_real_manifest, mixed_real_and_numeric_source_formats, missing import folder) and inside the existing transactional publication; let errors
from the non-transactional writers propagate unflagged so cli removes the managed reports as before F3.
Ownership: src/simcompare/pipeline.py, tests/test_compare_publication_cli.py (+ regressions), docs/usage.md (~:137) + docs/usage_reference.md
(matching clause: non-transactional real outputs — no condition_assignment / numeric_v2 in real mode — are removed on error), bundles rebuild.
Acceptance: new test ENOSPC inside the inspection CSV writer (no condition_assignment) → no managed comparison files left (fails on current
tree, passes after); B1 tests still pass; full pytest green except tests/test_handoff.py; /tmp/py38 run of test_compare_publication_cli;
stale-bundle test green. No commit. worker_done.
