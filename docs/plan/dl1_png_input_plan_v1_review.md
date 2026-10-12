# DL1 PNG input plan v1 — coordinator review

Date: 2026-10-12. Reviewer: Opus 5.5 coordinator (effort high, set by the user with `/effort high`
before this review), Run `run_3b14694cfbbe`. Reviewed file: `dl1_png_input_plan_v1.md`
(Task `task_2d6ce4aa5fe4`, Dispatch `ctx_a621591eb41c`).

Verdict: **REVISE — not adopted as written.** The route (strict v1 manifest + NPY export, versioned
sidecar, explicit metadata/grid for export, four sequential Tasks) is accepted. Six blocking findings
and five notes below must be folded into v1.1 before implementation is dispatched.

Checked: requirement coverage against `../png_input_notes_2026-10-12.md`; read/write scope; dependency
order; counterexamples for each acceptance test; interruption/retry recovery; DL1 code at `03bf3e3`
(`data.py`, `schema.py`, `train.py:66-106`, `checkpoint.py:32-33`, `model.py:99`, `requirements.lock`).

## Blocking findings

**B1 — Upper endpoint with a skipped layer is held unresolved; the source says report it, not drop it.**
Plan §4.4 step 3 and S07 hold a relation whenever the nearest *overlapping* upper layer is not the next
modeled stack layer. The source (§2 of the final draft) selects "CNT 순번보다 작은 순번 중 가장 가까운
겹침 layer" and says a skipped intermediate layer is to be "검증 보고서에 드러내고 확인한다". Its
unresolved list is: several electrodes on the selected layer, missing endpoint, same element on both
sides, undivided multi-connection CNT. A skipped layer is not in that list. A layer that has no pattern
at the CNT location is the normal case in a multi-layer stack, so holding it would leave most real
relations unresolved and make the pilot uninformative. The lower side already uses nearest overlapping
(user-confirmed), which also skips non-overlapping layers.
Required: emit the relation when the nearest overlapping upper layer is unique and all other rules
hold; record `skipped_upper_layers` (and `skipped_lower_layers` symmetrically) per relation in the
report, count them in the summary, and mark them distinctly in the preview as "needs confirmation".
Rewrite S07 accordingly. Intervening `cno` layers are listed separately from intervening electrode
layers. This remains an implementation interpretation in the decision ledger, flagged for the user.

**B2 — No bound on per-component cost; the v1 contract makes export O(K·H·W).**
`schema.py:202-204` requires every connection mask at full frame shape and `binary_mask` returns
float32, so K CNT components cost K full-frame NPY files on disk and K·H·W·4 bytes in `load_manifest`.
The plan leaves this as "resource limits pending" and specifies nothing that would catch a naive
per-component full-frame loop. Real frame size and CNT count are unknown, which is exactly why the
importer must report them before the user commits to an export.
Required: (a) labelling returns a label array once per CNT file; candidate tests, intersections and the
representative pixel use each component's bounding-box slice, never a full-frame boolean operation per
component per electrode; (b) exported NPY masks are `bool` dtype (accepted by `binary_mask`); (c) the
import report carries a `resource_estimate` per model — frame pixels, electrode asset count, CNT
component count, projected export bytes, projected `load_manifest` float32 bytes — computed in inspect
mode before any export; (d) a named synthetic test with a frame of at least 2000×2000 and at least 300
CNT components that asserts correctness and a generous wall-clock bound as a regression guard, stated
in the docs as not a real-size acceptance. No numeric limit is invented; the docs say the user decides
from the inspect report.

**B3 — Vocabulary consequence of "electrode folder name = role" is not planned or tested.**
`data.py:176-180` raises when a role or layer is outside the checkpoint vocabulary, and `build_vocab`
is taken from the training products. With exact electrode/layer names as symbols, a query or candidate
model whose names never appeared in training cannot be embedded, and two models that reuse a name share
an embedding without any claim that the electrodes are equivalent. The plan calls this "exact symbols"
and stops.
Required: the inspect report lists per-model role and layer name sets plus the cross-model union and
the names unique to one model; a named test shows search with an out-of-vocabulary product failing with
the existing clear error (not silently dropped); USAGE states the limitation and that no alias mapping
is applied. An optional explicit alias mapping is out of scope and stays pending.

**B4 — Four independent reviews are not justified by risk; the gates need consolidating.**
Plan §5 requires a separate fresh Opus review after every Task. Policy asks for review by risk. Two
gates cover the risk: **R-A** on the T1+T2 candidate (source contract, decoder strictness, core
extraction algorithm) before export is built on it, and **R-B** on the final T3+T4 candidate covering
the full diff from baseline (publication safety, report semantics, transform refactor, compatibility).
Required: restate the review gates this way; T3 may start only after R-A findings are resolved.

**B5 — Branch and commit handling is undefined, and the baseline is the default branch.**
DL1 is on `main`. The plan says "record commit or diff fingerprint; do not assume a commit was made",
which leaves the exact review candidate ambiguous.
Required: T1 starts by creating branch `feat/png-input` from `03bf3e3`; each Task ends with one local
commit (plus fix commits after review); nothing is pushed, merged, rebased or amended; review
candidates are commit hashes on that branch.

**B6 — S20 does not say how the baseline snapshot is obtained without touching Git state.**
"Temporary read-only source snapshot" could be read as `git worktree add` or a checkout.
Required: `git archive 03bf3e3 | tar -x -C <tmp>` (or equivalent that writes nothing into the repository
and creates no worktree/branch), run with the installed `.venv` and `PYTHONPATH=<tmp>/src`.

## Notes (fold in; not blocking on their own)

- **N1** S07 "equal-stack-only contact" is unreachable: an electrode sharing a CNT's stack number with a
  different layer name is already an inventory error under S03. Either drop the case or define the one
  reachable variant (same number and same layer name) and test that.
- **N2** Preview: state that it is produced with Pillow only (no matplotlib in `requirements.lock`),
  that the overview is downscaled for display, and that each component gets an original-resolution crop
  for inspection. A display crop is not an input crop; say so in the legend.
- **N3** Add an optional inspect flag that evaluates user-supplied candidate grids (no default) with the
  existing `grid_check` evidence and the transform record, so the user can choose `--candidate-grid`
  from evidence. Without it the user must guess a grid to reach export.
- **N4** Document that `connectivity_observation` feeds a model embedding (`model.py:99`,
  `train.py:106`), so "complete" versus "partial" from automatic extraction changes model input; the
  sidecar wording "extraction coverage only" must appear in USAGE next to that fact.
- **N5** Pillow's decompression-bomb limit is a real constraint for large frames. State the behaviour
  (error surfaced per file, limit value reported, user-controlled explicit option to raise it, never
  raised silently) and test the error path with a patched low limit.

## Accepted as written

Strict v1 manifest and NPY export with a hash-bound sidecar; explicit metadata and grid for export with
inspection always available; all-or-nothing export and staged publication with no overwrite; full
frame extent in the legacy `unit_size_um` with the sidecar recording the 2×2 core as reference only;
4-neighbour components declared in the report; nearest-overlap selection before the common-intersection
test; representative pixel inside the common intersection; no electrical verification status; the
decision ledger's separation of confirmed facts, proposals and pending questions; recovery notes.

## v1.1 adoption — 2026-10-12T05:07Z

Reviewed `dl1_png_input_plan_v1_1.md` (Task `task_3c4d16b36593`, Dispatch `ctx_5a600a5a3ae3`) at high
effort. B1–B6 and N1–N5 are each resolved in the sections named by its revision map; the source's
required synthetic checks still map to named tests (S01–S27). Planner objections: none.

**Verdict: ADOPTED as plan v1.1.** Implementation order T1 → T2 → R-A → T3 → T4 → R-B → coordinator
final verification, one Sol medium editor, branch `feat/png-input` from `03bf3e3`, local commits only.

Coordinator conditions carried into the Task contracts (clarifications, not plan changes):

- C1 The skipped-layer rule (B1) and 4-neighbour components are implementation interpretations. They
  are reported to the user as open questions; no report or doc may call them user-confirmed.
- C2 `SCALE_REGRESSION_SECONDS` is set at no less than ten times the measured fixture time on this
  machine, so the guard cannot flake on a loaded machine; the measurement is recorded.
- C3 Any need to touch a file outside a Task's allowed list is raised with `ask` before editing.
