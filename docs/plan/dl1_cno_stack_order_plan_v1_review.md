# DL1 cno stack order plan v1 — coordinator review and adoption

Date: 2026-10-13. Reviewer: Opus 5.5 coordinator (effort high), Run `run_84882a6a9a35`. Reviewed file:
`dl1_cno_stack_order_plan_v1.md` (Task `task_719a63b53eae`, Dispatch `ctx_397ecfdf57dd`).

Checked: coverage of the 2026-10-13 user statements (cno numbers meaningless, CNT identified by name,
several files per CNT name kept separate, one reference order for all models with absent layers
allowed, order supplied as a separate input file, no overlap inference); read/write scope; dependency
order; counterexamples of O01–O18; migration and recovery; DL1 code at `e67a8b2`.

**Verdict: ADOPTED as plan v1** with the conditions below. No blocking finding. The plan already folds
in the answers the user gave while it was being written.

Why it is acceptable:

- The order contract is one strict JSON list, validated against each model's electrode numbers, so a
  wrong list is caught where the data can contradict it and a missing name is an error, not a guess.
- cno numbers leave every identity; CNT identity is name plus mask content, so renumbering files does
  not change results, and same-name files stay separate without a union.
- Without the order file nothing positional is extracted and export is blocked for models with cno;
  models without cno keep working as before.
- Report, bundle and algorithm versions change, and old sidecars are rejected by the readers.

Conditions carried into the Task contracts:

- C1 Each Task ends with exactly one local commit on `feat/png-input`; no push, merge, rebase or amend.
- C2 `docs/USAGE.md` keeps the step-by-step structure written on 2026-10-13 (commit `e67a8b2`); the order
  file becomes its own preparation step, Windows command forms stay next to the POSIX ones, and the
  phrases asserted by `tests/test_png_integration.py::test_png_offline_documents_preserve_pending_quality`
  stay present.
- C3 `same_electrode_required` stays false and inactive. The coordinator asked the user whether a CNT
  always joins two layers of the same electrode folder; no answer yet. A confirmed rule is a new
  contract revision with its own review, not a change slipped into these Tasks.
- C4 Every order example in docs, help text and tests is labelled as syntax only. Nothing may state
  or imply where OCNT, GCNT or PCNT really sit.
- C5 T2 additionally owns a new example file `configs/stack-order.example.json`, with a comment-free
  JSON body and a conspicuous note in USAGE that its order is a placeholder to be replaced.
- C6 Remaining plan decisions are accepted at their conservative defaults: any CNT name listed in the
  order file is accepted (no whitelist); two same-name files with identical masks are an error naming
  both files.
