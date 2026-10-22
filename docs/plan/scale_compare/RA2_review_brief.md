# RA2 — Phase A re-review after fixes (read-only). Reviewer: Codex gpt-6-astra / xhigh. Risk tier HIGH.
READ-ONLY: do not edit/create/delete files; do not commit. Report findings only, then worker_done.
Your previous review RA (task_f5e007a54570) gave FAIL with R1–R5 and advisories A1–A4. Fix task T7 (spec docs/plan/scale_compare/T7_spec.md)
claims all are fixed with regression tests, plus one extra regression it found (validate-data/configured_dataset priced full-bucket output
by default -> invalid_configured_input; now summary default, validate-data follows the compare section).
Candidate: working tree vs HEAD 01989de; diff excluding generated bundles and old *.diff notes: docs/plan/scale_compare/PhaseA_candidate_v2.diff.
Bundles regenerated after the last src change — verify they embed current src (decode payload).
Evidence: full pytest green except tests/test_handoff.py (sibling repo dirty); /tmp/py38 (3.8.20, numpy 1.21.6) scale/summary/equivalence/
real_hotspot/comparison_v2 tests pass; benchmark centered N=1682 S=1262 DEFAULT limits: import ~244 s/0.84 GiB per model, validate 69 s/1.1 GiB,
compare 587 s/3.9 GiB exit 3 (T5_benchmark.md, last section).
Check: (1) each of R1–R5, A1–A4 actually fixed, with a test that would fail on the old code; (2) the extra regression fix is correct and does not
weaken full-mode admission; (3) no new numeric change (equivalence/oracle, summary==full areas/CSV); (4) any new blocker for ETX users restarting
from stage 2 (configs/etx, docs/usage.md, runners). Verdict PASS / PASS-WITH-ADVISORIES / FAIL with file:line evidence.
