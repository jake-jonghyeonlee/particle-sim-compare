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

## Addendum A1 — 2026-10-13: electrode numbers are provenance only (user decision)

After O1 was accepted (`6f9a167`), the user supplied the full top-to-bottom order
`PXL, SD2, SD1, OCNT, PCNT, GAT3, GCNT, OACT, GAT2, GATC, GAT1, ACT, BML` and said the order must be
taken from this list and no longer from filename numbers. Plan v1 kept electrode numbers as stack
positions and cross-checked the list against them; that part is superseded. The coordinator issues this
bounded amendment directly (a user decision with direct consequences; no new design choice):

- A1.1 Electrode filename numbers are provenance only, exactly like cno numbers: positive integer and
  filename syntax are the only requirements. They are never a stack position.
- A1.2 The vertical order comes only from the order file. `stack_order_electrode_order_conflict`,
  the electrode `conflicting stack` check and `stack_order_ambiguous_electrode_name` are removed: the same
  number may carry different layer names and one layer name may appear under different numbers in
  different electrode folders. A duplicate electrode × layer inside ONE electrode folder stays an error.
- A1.3 Electrode numbers leave every identity: electrode asset/element binding becomes
  `[model, "electrode", electrode, layer]`. Renumbering any file changes no asset, element, component,
  connection or model identity. Report keeps `filename_number` as provenance; `electrode_stack` and
  `electrode_stack_table` are removed or replaced by an explicit provenance table.
- A1.4 Order-file names are matched to source layer names case-insensitively (casefold), because the
  user wrote `PXL` while an earlier filename example is `1-pxl.png`. Two present names that differ only
  by case, or two order entries that differ only by case, are an error naming both. Everywhere else
  (element IDs, manifest role/layer, vocabulary) the source spelling is kept exactly; the report records
  the matched pairs.
- A1.5 Models without cno need no order file. Where no order is resolved, nothing may be ordered by
  filename numbers: use layer name and label the listing as unordered. When an order file is supplied it
  is validated for every model (all present names must be listed).
- A1.6 The user's order is committed as `configs/stack-order.json` (O2) and described as the user-supplied
  reference of 2026-10-13. Tests keep using their own synthetic lists; no test hard-codes this order as
  truth and no code hard-codes it.
- A1.7 `same_electrode_required` stays false and inactive.
Versions stay report v3 / bundle v3 / algorithm "2" because nothing built at `6f9a167` has been published.

## Addendum A2 — 2026-10-13: CNT as image only is the first path (user decision "일단 a부터 가보자")

The user confirmed that one CNT component joins layers of the same electrode and observed that the
electrode folder already groups those pieces, so relation extraction adds only the contact location.
The user chose to start with option A: use CNT as an image only. Relation extraction (option B) stays
available as an optional mode. This supersedes plan v1's rule that export is blocked for cno-present
models without an order file.

- A2.1 Two modes, chosen only by whether `--stack-order` is given, recorded as `cnt_mode` in the report
  (top level and per model) and shown in the CLI completion line and the preview:
  `image_only` (no order file) and `relations` (order file supplied).
- A2.2 `image_only`: every CNT file is decoded and validated as before; the exported product carries
  the global CNT mask (union of all CNT foreground of the model) as the existing `cnt` asset,
  `connections: []` and `connectivity_observation: "unavailable"`. No relation, no endpoint, no position
  is produced or implied. Export is allowed (metadata and grid are still required). The component
  inventory (count, bbox, per-file name) stays in the report and preview as geometry only, with reason
  `cnt_image_only_no_relations`; `stack_order.status` is `not_used`.
  The prerequisite `explicit_stack_order_missing` and the reason `stack_order_required` are removed.
- A2.3 `relations`: behaviour as implemented at O1b. A supplied but invalid order file is still an
  error (exit 2, no export) — it never falls back to `image_only`.
- A2.4 The confirmed same-electrode fact is NOT yet applied to `relations` mode (`same_electrode_required`
  stays false). Docs must say that `relations` mode is optional, not yet reviewed against the
  same-electrode rule, and may emit a relation between two different electrodes; this is a known gap
  to close before `relations` mode is relied on.
- A2.5 Docs (O2): the main step-by-step path is `image_only` with no order file. The order file and
  `relations` mode move to an optional section. `configs/stack-order.json` (the user's 13-name order) is
  still added for that optional mode.
- A2.6 Identities: a model exported in `image_only` and in `relations` mode has different Product input
  identities (connections differ); state this and that a checkpoint/search must use one mode consistently.
Versions stay report v3 / bundle v3 / algorithm "2" (nothing published yet).

## Addendum A3 — 2026-10-13: image_only must not reject real-style input for relations-only reasons

From independent review R-O (PASS-WITH-NOTES for `60de205`, notes N1–N3). Coordinator decisions; they
only relax rejections that protect nothing on the image_only path, and keep relations mode as reviewed.

- A3.1 (N1) In `image_only`, two or more cno files with the same CNT name AND identical decoded masks are
  accepted: the union is unaffected. Report them as a warning (`duplicate_cnt_mask_same_name`, naming all
  files). In `relations` mode the error `ambiguous_cnt_asset_identity` stays (condition C6).
- A3.2 (N2) Without an order file, layer names that differ only by letter case never produce an error and
  never an order-named code. Report a warning `layer_name_case_variants` per model and, at inventory
  level, for case twins across models (they become separate layer vocabulary entries; source spelling is
  kept). With an order file the collision error of A1.4 stays.
- A3.3 (N3) The `.png` extension is matched case-insensitively (`.PNG`, `.Png`). Everything else about
  filename syntax is unchanged. The original filename stays in provenance.
- A3.4 (N4, N8, N9) Pin the `to_product` CNT union with a test; correct the stale suite count in USAGE;
  in image_only name the summary field so it does not read as failed contacts (keep a compatible meaning
  documented, or add `geometry_only_component_count` and set the unresolved count to 0 — choose one and
  keep report v3 readers consistent).
