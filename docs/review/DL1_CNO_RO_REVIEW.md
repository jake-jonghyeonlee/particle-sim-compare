# DL1 cno order / image_only — independent review R-O

Date: 2026-10-13. Reviewer: fresh Claude Opus 5.5, effort high, read-only (Task `task_a256d95569ef`,
Dispatch `ctx_6302bed5e9be`). I wrote neither the code nor the plan.

Verdict: PASS-WITH-NOTES for 60de205a1b971274a34964baa938067c9629703f

Scope: `git diff e67a8b2a3719d7141472a32503d00f52bbdf625c 60de205a1b971274a34964baa938067c9629703f`
(O1 `6f9a167`, O1b `f907749`, O1c `b9103a4`, O2 `60de205`) in `particle-sim-dl1`, branch
`feat/png-input`. Contract: plan v1, its review (C1–C6, addenda A1 and A2 overriding the plan) and the
user facts in `png_input_notes_2026-10-12.md` from "2026-10-13 정정" to the end.
The verdict applies to this commit only. All evidence is synthetic and from macOS.

## Blocking findings

None.

## Non-blocking notes

Ordered by how likely they are to matter on the first real image_only run.

**N1. Two same-name CNT files with identical masks are rejected in image_only too (decision requested by the Task).**
`src/psdl1/png_source.py:444-451` (and the generic identity claim at `:433`) run unconditionally.
Input: `R1/cno/1-OCNT.png` and `R1/cno/5-OCNT.png`, both all-white (or both the same shape), no
`--stack-order`. Result: exit 2, `ambiguous_cnt_asset_identity` plus `duplicate/colliding emitted identity`,
model invalid, and because export is all-or-nothing the whole pool is blocked.
My judgement: not blocking. C6 makes this an error and A2.2 says CNT files are "validated as before"; the
failure is loud, names both files, and is documented (USAGE.md:103, :505, :537). But image_only does not
need it: the union is the same whichever file is which, so the rule protects nothing on this path.
The realistic trigger is two empty same-name files in one cno folder. Recommended: ask the user whether
that can occur; if yes, relax the rule for image_only in a small contract revision (keep it in relations).

**N2. An order-named error can fire without `--stack-order`: `stack_order_case_collision`.**
`src/psdl1/png_source.py:465-471`. Input: `R1/VDD/1-PXL.png` and `R1/DATA/9-pxl.png` (also electrode
`ocnt` vs cno `OCNT`) with no order file → exit 2. This is what A1.4 requires and USAGE.md:105/:504 say
so in the main path, so it is not a contract violation. Two things to know: the code name suggests an
order-file problem the user does not have, and the real filename case is still an open question in the
notes. The same check does **not** run across models: model A with `PXL` and model B with `pxl` both
pass and become two separate layer vocabulary entries (A1.4 keeps source spelling). Look at
`inventory.electrode_layer_union` in the first real report for case twins.

**N3. `.PNG` (upper-case extension) is rejected.** `parse_png_name` (`png_source.py:72`) needs lower-case
`.png`; `1-OCNT.PNG` gives "expected positive numeric prefix…" and exit 2. Unchanged from `e67a8b2`, so
outside this diff, but the target host is Windows. `1 - OCNT.png`, `OCNT.png` and `0-PXL.png` are
rejected as documented. `Thumbs.db`/`desktop.ini` inside a folder only warn.

**N4. Test gap: the union in `to_product` is not pinned.** Mutant "last CNT file instead of union" at
`png_connections.py:370` survived the six PNG test files I ran. That function feeds only
`--inspect-grids` evidence (the `global_CNT` grid check); the export union at `import_png.py:330` is
tested and its mutant was killed. Production code is correct on both lines.

**N5. Manifest element order differs between modes.** image_only sorts elements by layer name, relations by
order index (`import_png.py:322`). `data.input_identity` is order-independent and identities matched for
models without relations, so the stated rule "modes differ only through connections" holds for identity.
I did not check whether the encoder is insensitive to element order (see "could not verify").

**N6. A CNT name change does not change the Product input identity in image_only.** Renaming
`2-GCNT.png` to `2-XCNT.png` changes the CNT asset, component and source-model identities but not the
Product identity, because the product carries only the union. Consistent with the contract; stated so
nobody expects otherwise.

**N7. Resume across this change is refused.** A checkpoint trained with `e67a8b2` code searches
byte-identically under the candidate, but `--resume` fails with `resume code hash differs`: the code hash
covers every `psdl1/*.py`, including the importer. Pre-existing mechanism, documented (USAGE.md:758).

**N8. Stale number in the guide.** USAGE.md:76 quotes the O1c run (`551 passed, 1 skipped`); this commit
gives 565 passed, 1 skipped. It is labelled as a record of that time, so not false.

**N9. Naming in the JSON summary.** In image_only `extraction[].summary.unresolved_component_count` equals
the component count and components carry `unresolved_reasons: ["cnt_image_only_no_relations"]`. The preview
shows "geometry only" and USAGE.md:204 says this is not a failed contact; only the field name reads oddly.

**N10. Same-electrode gap is real and documented.** With the user's 13-name order, electrode `X/1-SD1`,
electrode `Y/1-GAT3` and an OCNT pad over both produce a relation X→Y (`same_electrode_required: false`).
Stated in USAGE.md §9 and §11, README.md:121-123 and data_contract.md "Known gap".

## What I verified and how

All runs used `PYTHONDONTWRITEBYTECODE=1`, the real CLI in subprocesses, and synthetic PNG trees under
`/tmp/dl1-ro-review.IpsqCH` (scripts in its `scripts/`). Frames 1001×1101 or 1001×1001.

**A. image_only (no `--stack-order`)**

- Real-style tree: every electrode folder numbered from 1, `01_BML.png`, cno `1-OCNT`, `2-GCNT`,
  `3-OCNT`, `04_PCNT` (all-white), numbers colliding between folders and cno, both separators.
  Inspect, `--inspect-grids 64 128`, export, `--verify-bundle`, `python -m psdl1.data` all exit 0.
  The report contains none of `conflicting stack`, `stack_order_required`,
  `explicit_stack_order_missing`, `equal_stack`, `electrode_stack`.
- Exported `cnt` equals my own pixel union of the four CNT files (3466 pixels, exact array equality);
  `connections: []`, `connectivity_observation: "unavailable"`; manifest keys unchanged; exported
  electrode masks equal the source masks. Unchanged `load_manifest` loads it; `prepare_product` gives a
  CNT channel equal to the normalised union for every element, non-zero, with zero connections;
  report identities equal `input_identity` of the loaded products.
- Four models are told apart: with CNT (`cnt_image_only_no_relations`), no cno folder
  (`missing_cno_directory`, no `cnt` key), empty cno (`present_cno_without_png_assets`, no `cnt` key),
  all-white cno (`observed_cnt_empty_foreground`, `cnt` present with 0 pixels).
- Renumbering every file (new numbers, zero padding, swapped separators): no change to asset, element,
  component, frame or model identities, generated asset names and hashes, or Product input identities.
  One CNT pixel changed, an electrode layer renamed, an electrode folder renamed: identities change.
- Report and preview: `cnt_mode image_only` at top, model and extraction level and in the completed
  event; `not_run_image_only`; order `not_used`; no candidates, no relation, `cnt_order_index: null`;
  `all_components_resolved: null`, `not_applicable_image_only`, mask count 9 = 7 electrodes + valid + cnt.
  Preview HTML: 0 occurrences of "Endpoint", "position", "red X"; components titled "geometry only".
- Safety spot-checks: out inside root, out via symlink into root, out as parent of root, existing out all
  refused with nothing created; a bundle produced by `e67a8b2` code (`dl1_png_bundle_v2`) is rejected;
  tampered asset, tampered report, extra file rejected.

**B. relations mode**

- Order file handling (15 invalid files, each exit 2, no manifest, no assets, `cnt_mode relations`):
  missing CNT name, missing electrode name, duplicate entry, case-only duplicate, unknown field, wrong
  version, duplicate key, UTF-16, non-UTF-8, double BOM, empty file, comment, NaN, empty list, trailing
  text; also missing file and a directory. Valid LF, BOM, CRLF (under a Korean path with spaces),
  all-lower-case names and extra unused names give identical Product identities and order digests;
  `file_sha256` equals the SHA-256 of the exact bytes; no scratch path, `/private` or Korean folder name
  appears in the report. `load_stack_order` is called once in `run_import` and the parsed snapshot is
  passed on.
- Independent brute force (own flood fill, exact-fraction centroid, order positions from the list):
  28 random models, 213 components, 59 relations, several CNT files per name, absent layers, lower-case
  models, mixed-case order list. Endpoints, coordinates, reasons, skipped layers by kind, order index
  and counts all match; never more than one relation per component; export, verify and `psdl1.data`
  exit 0; global `cnt` equals the union in this mode too.
- Numbers: permuting every filename number in those trees leaves manifests, asset hashes, component and
  connection IDs, order indexes and Product identities identical. Grep finds `filename_number` only as
  a stored/printed provenance field.

**C. Mode separation**

- No invalid order produced an export or an image_only report. Reader checks after re-sealing reject:
  top/model/extraction `cnt_mode` changed either way, missing `cnt_mode`, `same_electrode_required: true`,
  wrong digest, reordered entries, status relabelled, report v2 label, algorithm "1" at top or per model,
  and a connection added to an image_only manifest.
- image_only vs relations for the same source: products identical except `connections`,
  `connectivity_observation` and element order; identities differ only for the model with relations.

**D. Legacy and core**

- `data.py`, `train.py`, `schema.py`, `model.py`, `checkpoint.py`, `search.py`, `evaluate.py`,
  `split.py`, `losses.py`, `requirements.lock` are byte-identical to a `git archive e67a8b2` extract;
  the decoder part of `png_source.py` differs only in one error-message word.
- In separate subprocesses with base and candidate `src`: the same NPY manifest gives identical schema
  and model-input versions, identities and prepared tensors; a checkpoint trained by base code gives a
  byte-identical search result.
- Full default suite twice: 565 passed, 1 skipped, both runs. Both repositories clean afterwards.

**E. Docs**

- The USAGE.md POSIX commands of §4.1, §4.7, §5.2, §5.3, §6.3 (with `--epochs 2`/`3` instead of
  100/150), §6.4 and §6.5 ran as written on a six-model synthetic tree with no order file: all succeeded.
- `configs/stack-order.json` holds exactly the user's 13 names in order and is described as the
  user-supplied reference of 2026-10-13, not as verified physics. No remaining statement that numbers
  are positions, that export needs an order file, or that an equal-stack case exists, other than text
  marked superseded. Windows is stated as never executed; no real-data, electrical or retrieval claim.

**F. Tests** — the named O-tests and the A1/A2 tests exist and assert literal expectations. Mutants in
scratch copies of the commit (six PNG test files, `-x`): 12 of 13 killed by an assertion; a 14th died by a crash and is not counted.

| Mutant | Killed by |
|---|---|
| export union → last CNT file | `test_image_only_export_carries_global_cnt_and_no_relations` |
| `to_product` union → last CNT file | **survived** (N4) |
| number in CNT asset identity | `test_cno_123_numbers_do_not_conflict_with_electrode_stacks` |
| number in component identity | `test_extracted_component_hash_and_id_describe_actual_mask` |
| number in electrode identity | `test_explicit_metadata_and_exact_names` |
| invalid order → image_only fallback | `test_stack_order_strict_json_diagnostics` |
| casefold removed from matching | `test_stack_order_exact_identifiers_and_read_errors` |
| same-name files unioned in relations | `test_same_name_cnt_assets_remain_separate_and_ambiguities_fail` |
| missing names tolerated | `test_stack_order_requires_all_present_names` |
| filename number used as position | `test_cno_123_numbers_do_not_conflict_with_electrode_stacks` |
| identical-mask error removed | `test_emitted_identity_collision_fails_and_repeated_cnt_layer_is_preserved` |
| source case-collision check removed | `test_order_names_match_case_insensitively` |
| verifier ignores image_only relations | `test_image_only_reader_rejects_inferred_evidence[manifest_relation]` |
| CNT identity tuple reshaped (crash) | killed by a TypeError, not by an assertion; not counted as evidence |

## What I could not verify

- Anything on Windows or Linux, and anything on real data: file-name case, `.PNG` extensions, image
  modes and sizes, whether identical same-name CNT files occur.
- The preview visually; I checked its text only.
- Whether the encoder is insensitive to element order (N5); I checked identity, not model output.
- Disconnected common intersections did not occur in my random models (0 cases); that branch rests on
  the existing tests. One `empty_common_intersection` case matched.
- The opt-in `--run-training` test (skipped by default) and retrieval quality.
- For the surviving mutant, `tests/test_png_compatibility.py` errored at setup inside the scratch copy,
  so I cannot say whether that file would have killed it.
