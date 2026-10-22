# RA4 — Phase A re-review after T9 (read-only). Reviewer: Codex gpt-6-astra / xhigh. Risk tier HIGH.
READ-ONLY: do not edit/create/delete files; do not commit. Report findings only, then worker_done.
Your RA3 findings: docs/plan/scale_compare/RA3_findings.md (B1 recovery fail-closed on unreadable journal; B2 stage-4 comb-region underpricing;
advisory publication wording). Fix task: docs/plan/scale_compare/T9_spec.md.
Candidate: working tree vs HEAD 01989de; diff excluding bundles and *.diff: docs/plan/scale_compare/PhaseA_candidate_v4.diff. Bundles rebuilt —
verify embedded src. Evidence: full pytest green except tests/test_handoff.py (sibling dirty); /tmp/py38 targeted tests green.
Verify B1/B2/advisory with your original repros, confirm no regression of earlier fixes (RA, RA2) and no numeric change.
Scope note from the coordinator: Phase A's contract is stage 2 at real scale plus fail-fast guards (not full streaming) for stages 4/6.
Report only defects that violate that contract or numeric identity; label anything else advisory.
Verdict PASS / PASS-WITH-ADVISORIES / FAIL with file:line evidence.
