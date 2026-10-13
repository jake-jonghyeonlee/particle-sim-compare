# DL1 cno order / image_only — independent review R-O2 (refresh of R-O)

Date: 2026-10-13. Reviewer: fresh Claude Opus 5.5, effort high, read-only (Task `task_78d365d1ea16`,
Dispatch `ctx_6228fc4cc3ef`). I wrote neither the code nor the plan.

Verdict: PASS-WITH-NOTES for d4dd896d315d99c7e72e68570a95f659b24aa6ea

Scope: only `git diff 60de205a1b971274a34964baa938067c9629703f d4dd896d315d99c7e72e68570a95f659b24aa6ea`
(9 files, +217/-30) in `particle-sim-dl1`, branch `feat/png-input`. Contract: addendum A3 of
`docs/plan/dl1_cno_stack_order_plan_v1_review.md` and the notes of `DL1_CNO_RO_REVIEW.md`.
The verdict applies to this commit only. All evidence is synthetic and from macOS.

## Blocking findings

None.

## Status of the R-O notes

| Note | Status | Evidence |
|---|---|---|
| N1 identical same-name CNT files | Fixed as A3.1 | image_only: exit 0, warning names every file, union exact; relations: still `ambiguous_cnt_asset_identity`, exit 2, no manifest (section A) |
| N2 order-named error without order file | Fixed as A3.2 | no order file: warnings only, no `stack_order_*` text anywhere in the report; with order file the report is byte-identical to `60de205` (section B) |
| N3 `.PNG` rejected | Fixed as A3.3 | `.PNG`/`.Png`/`.pNG` accepted for electrode and cno in both modes; other syntax unchanged (section C) |
| N4 `to_product` union not pinned | Fixed | mutant "last CNT file instead of union" now fails `test_to_product_cnt_union_is_all_files` |
| N8 stale suite count | Fixed | USAGE.md:76 says `573 passed, 1 skipped`; my run: 573 passed, 1 skipped (98.07 s) |
| N9 summary naming | Fixed | image_only summary has `geometry_only_component_count` = component count and `unresolved_component_count: 0`; reader checks both and still accepts `60de205` bundles |

## Non-blocking notes

**M1. Merged duplicate files are missing from two secondary listings.** With `R1/cno/1-OCNT.png`,
`5-OCNT.png`, `7-OCNT.png` identical, `duplicate_geometry[].source_paths` lists only
`R1/cno/1-OCNT.png` (`import_png.py:294` reads one path per asset), and the preview names `5-OCNT.png` and
`7-OCNT.png` once each (in the warning text) while every component points at `1-OCNT.png`. The warning
and `provenance_files` name all files, so nothing is lost; a reader of `duplicate_geometry` alone would
miss two files.

**M2. Which file represents a merged group depends on the filename.** `png_source.py:461` takes the
first file in path order. After renumbering, the asset's top-level `relative_path`, `filename_number`,
`file_sha256`, `decoded_mode` and each component's `source_path`/`cnt_filename_number` change
(`1-OCNT.png` → `09_OCNT.png` in my run). All identities, the manifest and every exported asset stay
byte-identical, so this is provenance only, as A1 requires. Stated so nobody reads the top-level
`file_sha256` as "the" file of that asset.

**M3. A tree with duplicates is indistinguishable, in the exported data, from the same tree without
them.** Manifest and all asset files were byte-identical between my tree with seven cno files and the
de-duplicated tree with four; only the report (`cnt_file_count` 7, warning, `provenance_files`) differs.
This is what "one content identity" means and matches A3.1.

**M4. `1-X.png.PNG` is now accepted** (layer `X.png`); at `60de205` it failed. It follows directly from
A3.3, and `1-X.png.png` was already accepted with the same layer name, so no new rule was loosened.
`1- X.PNG` is still rejected but with a different message (`layer: expected nonempty trimmed ID`).

**M5. USAGE.md has two rows for `extraction[].summary`** (lines 204 and 206). They do not contradict
each other; the older row was kept and the new one added below it.

**M6. Test gaps (production code is correct on each).** No test covers three identical files, two
duplicate groups in one model, renumbering of duplicate files, or a kind collision by case (electrode
`ocnt` vs cno `OCNT`). I checked all four through the CLI (sections A and B).

**M7. `--resume` across this commit is refused**, as in R-O N7: the code hash covers the changed
importer modules. Pre-existing mechanism; not re-tested here.

**M8. In relations mode, case twins across models are not reported** (unchanged from `60de205`;
A3.2 only covers the path without an order file).

## What I verified and how

All runs used `PYTHONDONTWRITEBYTECODE=1`, the real CLI in subprocesses, and synthetic PNG trees
(1001×1101) under `/tmp/dl1-ro2-review.6lF0Mv` (scripts in its `scripts/`). "Base" is
`git archive 60de205 | tar -x` used through `PYTHONPATH` in a separate subprocess.

**A. N1 — identical same-name CNT files** (`n1.py`, `n23.py`, `final.py`)

- Tree: model R1 with three identical `OCNT` files (three components each), a fourth different `OCNT`
  file, one `GCNT` overlapping them, two all-white `PCNT` files; R2 with two all-white `OCNT` files;
  R3 with one `OCNT` file. No order file: export exit 0, `--verify-bundle` 0, `python -m psdl1.data` 0,
  `--inspect-grids 64 128` 0.
- Three `duplicate_cnt_mask_same_name` warnings, each naming every file of its group. No
  `colliding`/`ambiguous` text in the report. 19 asset and component IDs, all unique; preview files are
  one per asset and three per component, no name collision.
- Exported `cnt` equals my own pixel union by exact array equality: R1 5425 pixels, R2 0, R3 4125.
  `connections: []`, `connectivity_observation: "unavailable"`.
- Counts: R1 has 4 CNT assets for 7 files; `cnt_file_count` 7; `generated_mask_asset_count` 4
  (2 electrodes + valid + cnt); 6 components — the three shapes of the merged group are counted once,
  not three times; `geometry_only_component_count` 6, `unresolved_component_count` 0.
- Renumbering every cno file (new numbers, zero padding, swapped separators): manifest, all asset
  files, asset/component/frame IDs identical. The reports differ only in filename, number, path and
  warning-text fields (M2). File enumeration is sorted in code (`png_source.py:408`), so directory
  order cannot matter.
- Duplicates saved in modes `RGB`, `L` and `1` and with `.png`/`.PNG`/`.Png`: one asset, three
  provenance entries with their own modes and hashes, 100 CNT pixels, bundle verified.
- De-duplicated tree: candidate manifest and assets byte-identical to the duplicate tree, and
  byte-identical to base output for the de-duplicated tree. Base rejects the duplicate tree (exit 2).
- Same tree with `--stack-order`: exit 2, `cnt_mode relations`, `ambiguous_cnt_asset_identity` for each
  group, no manifest, no assets, no `provenance_files`, no image_only warning; the whole report is
  identical to the base report.
- The base reader also verifies the candidate's duplicate bundle (exit 0).

**B. N2 — case variants** (`n23.py`)

- Tree: `WITHIN` (`PXL`, `Pxl`, `pxl`), `UP` (`PXL`, cno `OCNT`), `LOW` (`pxl`, cno `ocnt`), `KIND`
  (electrode `ocnt`, cno `OCNT`), `PLAIN`. No order file: exit 0, bundle verified, `psdl1.data` 0.
  Two per-model warnings (`WITHIN`, `KIND`) and two inventory warnings naming the variants and all
  models involved; no `stack_order_` text in the report. Base rejects the same tree (exit 2).
- Spellings kept: `electrode_layer_union` `[PXL, Pxl, SD1, ocnt, pxl]`, cno `[OCNT, ocnt]`; manifest
  `actual_layer` values exact; element IDs unique; the `UP` and `PLAIN` product entries equal those of a
  base run on a tree without twins.
- With an order file: exit 2, `stack_order_case_collision` for `WITHIN` and `KIND`, no manifest; report
  identical to base. The kind collision by case therefore warns in image_only (CNT stays an image, the
  electrode stays an element) and is an error in relations.

**C. N3 — extension** (`n23.py`, `final.py`)

- `1-PXL.PNG`, `2-SD1.Png`, `1_PXL.pNG`, cno `1-OCNT.PNG`: exit 0 in both modes, original filenames in
  the report, bundle verified; exported bytes identical to the same tree with lower-case extensions,
  which in turn is byte-identical to base output. Base rejects the upper-case tree.
- Still rejected (exit 2), same reason text as base except M4: `1 - OCNT.png`, `OCNT.png`, `0-PXL.png`,
  `1-.PNG`, `-1-X.PNG`, `1.5-X.PNG`, `1- X.PNG`. Still ignored with a warning, as at base: `1-X.jpg`,
  `1-X.pngx`, `1-Xpng`, `1-X.PNG ` (trailing space), `Thumbs.db`, `desktop.ini`, `1-PXL.png.bak`,
  `notes.PNG.txt`. Directories named `2-X.png`/`3-Y.PNG` and PNG files at root, model level or nested:
  `unsupported directory layout` / `PNG at unsupported layout/depth`, exit 2, as at base (that
  classification was already case-insensitive and is not in this diff).
- `1-PXL.png` next to `1-PXL.PNG`: the working volume is case-insensitive, so I ran this on a temporary
  case-sensitive APFS image mounted inside the scratch directory (detached afterwards). Electrode
  folder: exit 2, `duplicate normalized binding` naming both files, whether the masks are equal or not.
  cno folder, identical masks: exit 0, `duplicate_cnt_mask_same_name`, one asset with both files as
  provenance. cno folder, different masks: exit 0, two assets, union of both. `1-PXL.png` with
  `1-pxl.png` in one folder: exit 0, `layer_name_case_variants`, two elements. `CNO` beside `cno`:
  still the reserved-folder error.

**D. Compatibility** (`compat.py`)

- Eleven models (random layers and folders, several CNT files per name, no cno, empty cno, all-white
  cno, lower-case model names, one model with two real relations), base and candidate in separate
  subprocesses, both modes: manifest and all asset files byte-identical (77 files image_only, 79
  relations); Product input identities, asset and component IDs equal; `preview.html` identical.
- Relations mode: every bundle file and the whole report byte-identical to base.
- image_only: the report differs only in `summary.geometry_only_component_count` (new) and
  `summary.unresolved_component_count` (now 0), hence also `bundle-integrity.json`. Same for
  inspection without export.
- Candidate reader verifies base bundles of both modes (exit 0; 53 components under the legacy summary
  rule); candidate `psdl1.data` accepts base manifests; base reader verifies candidate bundles.
- `data.py`, `train.py`, `schema.py`, `model.py`, `checkpoint.py`, `search.py`, `evaluate.py`,
  `split.py`, `losses.py`, `import_png.py`, `png_preview.py`, `__init__.py`, `requirements.lock`,
  `pyproject.toml` byte-identical to the base archive. Report v3, bundle v3, algorithm "2", metadata,
  stack-order, schema and model-input versions unchanged.

**E. Tests** (`mutants.py`)

- Full suite on the candidate: 573 passed, 1 skipped (matches the coordinator).
- `test_to_product_cnt_union_is_all_files`, `test_image_only_accepts_identical_same_name_cnt_files`,
  `test_image_only_case_variants_warn_not_error`, `test_uppercase_png_extension_accepted` and
  `test_image_only_geometry_summary_and_duplicate_provenance_validated` use hand-written pixels, file
  names, counts and message fragments; none derives its expectation from importer code.
- Mutants in scratch copies of the commit (six PNG test files, `-x`): 11 of 11 counted mutants killed by
  a test expectation; a 12th died by a crash and is not counted. The unmutated control passed five
  files; `tests/test_png_compatibility.py` errors at setup in a scratch copy (it needs the git
  repository), as in R-O.

| Mutant | Killed by |
|---|---|
| `to_product` union → last CNT file | `test_to_product_cnt_union_is_all_files` |
| export union → last CNT file | `test_image_only_export_carries_global_cnt_and_no_relations` |
| merged group dropped instead of kept once | `test_image_only_accepts_identical_same_name_cnt_files[all_white]` |
| `cnt_file_count` counts assets, not files | same test (`1 == 2`) |
| duplicate warning names only one file | same test |
| relations error renamed to the image_only code | same test |
| extension accepts only `.png`/`.PNG` | `test_uppercase_png_extension_accepted` |
| cross-model case notice removed | `test_image_only_case_variants_warn_not_error` |
| order-file collision renamed to the warning code | same test |
| case variants an error without order file | same test |
| reader skips the geometry-summary check | `test_image_only_geometry_summary_and_duplicate_provenance_validated[count]` |
| discovery always treats the run as image_only | `RuntimeError` from the reader's provenance check, not a test expectation; not counted |

**F. Docs**

- README.md: only the two sentences about same-name CNT files changed (+3/-2 lines).
- USAGE.md:99, :103, :105, :206, :505–508, :540 and data_contract.md:113–124, :215–219 match what the
  CLI did in sections A–C, including "relations still rejects" and the reader accepting earlier v3
  image_only summaries.

## What I could not verify

- Anything on Windows or Linux and anything on real data: NTFS case behaviour, real file-name and
  extension case, whether identical same-name CNT files occur.
- The preview visually; I checked its text and file list only.
- `tests/test_png_compatibility.py` against the mutants (setup error in scratch copies); it passes in
  the repository as part of the full suite.
- Training, search and `--resume` behaviour were not re-run; the core modules are byte-identical to
  `60de205` and exported bytes are identical for every input both commits accept.
- A re-sealed bundle with `provenance_files` removed from an asset: by reading
  `png_contract.py:493-501` the reader would accept it, since a single-file asset has no such field.
