# RA3 — Phase A re-review after T8 (read-only). Reviewer: Codex gpt-6-astra / xhigh. Risk tier HIGH.
READ-ONLY: do not edit/create/delete files; do not commit. Report findings only, then worker_done.
Your RA2 (task_361f9e4fffb0, findings in docs/plan/scale_compare/RA2_findings.md) gave FAIL: B1 publication cleanup, B2 stage-4 counting scratch,
plus advisories (decode-count text, direct-API default binding). Fix task: docs/plan/scale_compare/T8_spec.md.
Candidate: working tree vs HEAD 01989de; diff excluding bundles and *.diff notes: docs/plan/scale_compare/PhaseA_candidate_v3.diff.
Bundles rebuilt after the last src change — verify embedded src (decode payload).
T8 claims: atomic .comparison.committed marker, roll-forward/back recovery, fault injection at every step incl. restart; bounded run-length
union-find component labelling identical to the old flood fill, counting/build priced before allocation (dense 400x400 test); T5 text fixed;
compute_areas default bound to admitted detail (bucket_detail_exceeds_admission). T8 also edited tests/reference_comparison_v1.py and
tests/test_linepixel_equivalence.py::test_real_hotspot_temp_dataset for the new default — confirm these edits do NOT weaken the oracle.
Evidence: full pytest green except tests/test_handoff.py (sibling repo dirty); /tmp/py38 targeted tests green.
Verify B1, B2, advisories, no numeric change, no new ETX blocker. Verdict PASS / PASS-WITH-ADVISORIES / FAIL with file:line evidence.
