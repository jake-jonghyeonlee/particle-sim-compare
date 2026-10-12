# DL1 PNG input — independent review R-A (T1 + T2)

Date: 2026-10-12. Reviewer: fresh independent Claude Opus 5.5 session, effort high, read-only
(Task `task_c09a58664503`, Dispatch `ctx_e9137e1a3d52`). I did not write this code or its plan.

Verdict: FAIL for 105acea9c475678cc160b85f398d68928729f90d

Candidate: `particle-sim-dl1`, branch `feat/png-input`, full diff
`03bf3e33c7107af75297aa298fa323e24018f28d..105acea9c475678cc160b85f398d68928729f90d` (9 files, +1969).
Contract: plan v1.1 §3, §4.2–4.4, §4.6, T1, T2, S01–S12, S23–S25, adoption conditions C1–C3, and
`docs/png_input_notes_2026-10-12.md`. This verdict applies only to that commit.

**Summary.** The failure is narrow: three blocking findings, all in the strict decoder
`png_source._decode`, all fixable inside T1's owned files. Each is a malformed or transparent PNG that
plan §4.3 says must be rejected and that is accepted; two of them fabricate black = foreground pixels.
Everything else I tried to break held: inventory, the extraction algorithm (zero mismatches against an
independent brute-force implementation), status semantics, cost structure, resource formulas and scope.
There are 14 non-blocking notes. T3 should not start until B1–B3 have fix commits and this review is refreshed.

## Blocking findings

All three were reproduced with hand-built PNG bytes (stdlib `struct`/`zlib`) against installed
Pillow 12.3.0 through `psdl1.png_source.decode_png`.

### B1 — A PNG whose pixel stream ends early is accepted, and the missing rows become foreground

- Where: `src/psdl1/png_source.py:103-113` (`image.load()` is treated as proof the pixel stream is complete).
- Input: 64×64, bit depth 1, greyscale, one IDAT whose zlib stream is *complete and valid* but holds only
  k of 64 scanlines (`zlib.compress(raw[:k*9])`), correct CRCs, IEND present.

  | Rows present | Result |
  |---|---|
  | 63 / 64 | accepted; row 63 all `True` |
  | 32 / 64 | accepted; rows 32–63 all `True` |
  | 1 / 64 | accepted; rows 1–63 all `True` |
  | 0 / 64 | rejected (`image file is truncated`) |

  The same happens for 8-bit greyscale (4×4 with 3 rows: last row decoded `[1,1,1,1]`, true data `[0,0,1,1]`).
- Why it happens: Pillow's zlib decoder returns "done" on `Z_STREAM_END` without checking that every row
  was produced. The undecoded rows stay zero-initialised, which is black, which this adapter maps to foreground.
- Why it violates the contract: §4.3 requires rejecting "corrupt/truncated files" with per-file reasons, and
  `docs/data_contract.md` states "animated or corrupt PNG fail with file paths". Here the importer invents
  electrode/CNT foreground, in the direction that creates overlaps and therefore relations, with no error or warning.
- Scope of the hole: byte-truncated files *are* rejected (every prefix of a 1096-byte file up to the end of the
  IDAT payload; multi-IDAT files with a dropped last/middle chunk or cut mid-file). Bit flips inside the stream
  are caught by zlib's Adler-32. The hole is specifically a well-formed stream with too little data, which needs
  a writer that finalised early. That is uncommon, but when it happens it is silent.
- Suggested fix: validate independently of Pillow that the concatenated IDAT inflates to exactly the expected
  scanline byte count for the IHDR (per-pass sum for Adam7), and add fixtures for short and over-long streams.

### B2 — tRNS transparency on 2-bit and 4-bit greyscale is ignored and the file is accepted

- Where: `src/psdl1/png_source.py:105-107` (opacity is judged only from `image.convert("RGBA")`).
- Input: 4×1 greyscale, samples `[0,max,max,0]`, `tRNS` = the white sample value.

  | Bit depth | tRNS = white | tRNS = black (0) |
  |---|---|---|
  | 1 | rejected | rejected |
  | 2 (tRNS = 3) | **accepted**, mask `[1,0,0,1]` | rejected |
  | 4 (tRNS = 15) | **accepted**, mask `[1,0,0,1]` | rejected |
  | 8 (tRNS = 255) | rejected | rejected |

- Why it happens: Pillow scales 2/4-bit samples to 0–255 but keeps the tRNS value unscaled, so
  `convert("RGBA")` finds no matching pixel and reports every pixel opaque.
- Why it violates the contract: §4.3 "Reject … transparent/semitransparent pixels (including alpha=0)" and
  S01 "nonopaque alpha/palette transparency". The docs say "Palette and transparency expansion is exact"; for
  these depths it is not. The outcome also differs from the identical image at depth 1 or 8.
- Practical risk is low (the resulting mask is the plausible one, and optimisers normally reduce such an image
  to 1-bit), but it is exactly the "tRNS on L" case the contract lists, and the fix is one line.
- Suggested fix: for modes `1`/`L`/`RGB`, reject when a tRNS chunk names a sample value that occurs in the
  image, comparing in raw sample units (or, more simply, reject any tRNS on non-palette images). Add 1/2/4/8-bit fixtures.

### B3 — Palette pixels with no palette entry are accepted as black foreground

- Where: `src/psdl1/png_source.py:101-113` (mode `P` is expanded without checking indices against PLTE).
- Inputs:
  - Colour type 3, bit depth 2/4/8, PLTE = `[white, black]` (2 entries), indices `[1,0,3,1]` → accepted,
    mask `[1,0,1,1]`. Index 3 has no palette entry and is decoded as black.
  - Colour type 3, bit depth 8, **no PLTE chunk at all**, indices `[1,0,0,1]` → accepted, mask `[1,1,1,1]`.
- Why it violates the contract: both files are invalid PNGs (the specification makes an out-of-range index
  and a missing PLTE errors). §4.3 allows palette input "only when decoded pixels are exactly black/white";
  here the black is supplied by Pillow's zero padding, not by the file. As in B1, the fabricated value is foreground.
- Suggested fix: for mode `P`, require a PLTE chunk and require `max(index) < number of PLTE entries`
  before expansion. Add both fixtures.

## Non-blocking notes

**Tests**

- N1 — S23 does not catch the plan's named regression "full-frame `labels == c` per component"
  (`tests/test_png_scale.py:37-69`). Replacing `png_connections.py:157` with
  `local = (labels == box.label)[window]` passes all 87 PNG tests: the instrumentation only checks argument
  shapes of `overlap_pixels`/`common_intersection`/`representative_pixel`/`mask_hash`, and the mutant runs in
  0.187 s against the 2.0 s guard. Production code is correct; the guard for it is missing. Suggest wrapping the
  component-slicing step in an instrumented helper, or asserting on the array handed to `==`.
- N2 — The 4-neighbour test covers only one diagonal direction (`tests/test_png_connections.py:142-145`).
  Changing `previous[k][0] < x1` to `<=` at `png_connections.py:66` (merges up-right diagonals) passes all tests.
  Production is correct (see verification); add a mirrored fixture such as `[[0,1],[1,0]]`.
- N3 — The timing guard is non-flaky: I measured 0.0546–0.0554 s idle and 0.080–0.101 s with 14 busy-loop
  processes on this 12-core machine, against 2.0 s (C2 satisfied, about 36×). On its own it is a weak detector
  (see N1); it is only meaningful together with the structural counters.
- N4 — The coordinator brief quotes "targeted T2 command 87 passed". The plan's T2 command
  (`test_png_connections.py test_png_scale.py`) gives 28 passed; 87 is those plus `test_png_source.py` (59). No defect.

**Decoder (below the blocking bar)**

- N5 — Other lenient acceptances, all with pixels identical to the intended image: bytes after IEND, a second
  PNG concatenated, file cut after the IDAT payload (missing IDAT CRC/IEND), wrong IDAT CRC with a correct
  Adler-32, IHDR interlace method 2, IHDR compression method 1, over-long pixel stream. The docs sentence
  "corrupt PNG fail" is broader than the behaviour; a strict chunk walk added for B1 would close these too.
- N6 — Decoding peaks at about 17–18 bytes per pixel of transient memory (8000×8000: RSS 0.1 GB → 1.27 GB),
  from the RGBA expansion plus boolean temporaries. `discover_source` also keeps every mask of every model
  alive at once (1 byte/pixel × assets × models). Neither appears in `resource_estimate` beyond the generic
  overhead note. Pillow's default guard is 89,478,485 pixels, so frames above roughly 9459×9459 already need the override.

**Inventory**

- N7 — An electrode folder with no PNG disappears without error or warning (`png_source.py:182-199`):
  `M/A/1-X.png` plus empty `M/B/` gives a valid model with roles `('A',)`. Suggest a warning.
- N8 — `cno` matching is exact-case (`png_source.py:192`): a folder `CNO` is a valid electrode named `CNO`
  with `cno_present=False`, so CNT masks silently become an electrode. Exact names are the plan's rule; a
  warning for case variants of the reserved name would not alias anything.
- N9 — Symlinks that stay inside the root are followed. `N -> M` yields a second valid model with identical
  content, and `M/B -> M/A` a duplicate electrode. Containment is as specified; T3 should surface models or
  assets with identical mask hashes, since an alias is a duplicate product in a training pool.
- N10 — Any non-model directory at the root (`.hidden`, `__MACOSX`, `.ipynb_checkpoints`) becomes an invalid
  model and makes the whole inventory invalid, while stray files are warnings. Conservative and acceptable; document it.
- N11 — Names are compared byte-exactly, so NFC and NFD spellings of the same Korean name give different
  roles and asset IDs. Copying a tree between macOS and Linux can change identities. Document, or warn on
  non-NFC names; do not normalise silently.

**Extraction and cost**

- N12 — `representative_pixel` builds a Python tuple per intersection pixel (`png_connections.py:114-122`):
  a 4,000,000-pixel intersection took 10.3 s and 570 MB. `label_components` loops in Python per run:
  6000×6000 with 9 M runs took 7.3 s, 50 % noise 13.4 s. Both are fine for small contacts and are outside S23.
  Each component's local mask is also hashed twice (`component_id` and `mask_sha256`).
- N13 — Naming hazard for T3: `ModelExtraction.extraction_status == "complete"` means "the extractor ran",
  next to `connectivity_observation == "complete"` meaning coverage, and a model with unresolved components
  has the former. `Product.connectivity_status` then turns coverage-complete into `"available"`
  (`schema.py`). Nothing claims electrical verification, but these three words need careful wording in the report and preview.

**Dependency**

- N14 — `Pillow==12.3.0` exact pin: appropriate for now. B1–B3 are all Pillow behaviours, so the decoder's
  strictness is only established for the version the fixtures ran on, and the pin matches `requirements.lock`.
  Two costs to state in USAGE: the closed-network wheelhouse must hold exactly 12.3.0 for the target
  platform or `pip install .[png]` fails, and the pin is not enforced at run time (a different installed
  Pillow is used as-is; only `DecoderPolicy.pillow_version` records it). Once B1–B3 are fixed with checks that
  do not depend on Pillow internals, a range such as `>=12.3,<13` becomes defensible.

## What T3/T4 must respect

- Surface `ModelSource.warnings` and `SourceInventory.warnings` in the report; they are the only trace of ignored files.
- Call `to_product`/`iter_connections` only after the resource estimate is shown; consume one connection mask at a time.
- Keep `needs_confirmation` as sidecar/preview evidence only; never describe the skipped-layer rule or
  4-neighbour labelling as user-confirmed (C1). The current docs and code comply.
- `report_payload` drops only fields named `mask`; any new array-valued field must be excluded or encoded explicitly.
- Hold the decoder session for the whole import and do not decode in parallel; `warnings.catch_warnings` and
  `Image.MAX_IMAGE_PIXELS` are process-global and the lock serialises only this module.

## What I verified and how

Commands were run from `/Users/jonghyeonlee/git/particle-sim-dl1` with `PYTHONDONTWRITEBYTECODE=1`; scratch
scripts and inputs live only in `/tmp/dl1-ra-review.tibsS4`.

| Check | Method | Result |
|---|---|---|
| Suites | `.venv/bin/python -m pytest -p no:cacheprovider` on the T1 files, the T2 files, and the full default suite | 124 passed; 28 passed; 282 passed, 1 skipped |
| Scope | `git diff --name-only`, `git diff --check`, `git diff --stat` on `schema.py`, `model.py`, `data.py`, `requirements.lock` | Only the 9 listed files; no whitespace errors; versions, NPY loader and lock untouched |
| Decoder matrix | ~95 hand-built PNGs: depth 1/2/4/8 grey, RGB, LA, RGBA, palettes at every depth, tRNS variants, 16-bit, Adam7, APNG, eXIf before/after IDAT, non-PNG bytes | Polarity black=1 correct for every accepted mode and depth, including swapped palettes; grey, colour, alpha, 16-bit, multi-frame, orientation rejected; exceptions are B1–B3 and N5 |
| Truncation and corruption | Every byte prefix of two files; bit flips at every byte of a 1096-byte file; stored-deflate 1-bit image with flipped pixel bits; wrong CRC/Adler; dropped IDAT chunks | All rejected except the cases in B1 and N5 |
| Pixel guard | Read `decoder_session`; existing S25 test; mutants | Global and `LOAD_TRUNCATED_IMAGES` restored in `finally` on success, per-file failure and exceptions; `None` guard rejected unless an explicit override is given, then restored to `None`; warning and hard error both become per-file errors with the effective limit |
| Labelling | `label_components` against an independent BFS on 3000 random masks (labels, bbox, count, first pixel) | 0 mismatches |
| Representative pixel | Against a `fractions.Fraction` reference on 3000 random masks | 0 mismatches; always inside the intersection |
| Extraction | Independent brute-force implementation of plan §4.4 on 1500 random models (12,664 components, 1,552 resolved), assets shuffled | 0 mismatches in endpoints, pixel, reasons-vs-relation, status aggregation; every product passed `validate_product`; at most one relation per component |
| Algorithm reading | Code review of `_extract_component` | Nearest upper/lower chosen before the intersection test; no fall-through; same-layer multiples, equal-stack, empty and disconnected intersections unresolved; skipped layers emitted with separate electrode/cno evidence and `needs_confirmation` |
| Endpoint floats | `validate_product` arithmetic for every x in [0, 2,000,000) at pitch 0.1; last-pixel centre against the half-open extent for ~8,400 widths up to 1,999,999 | 0 failures; fractional part stays within 0.5 ± 2.4e-10 |
| Inventory | 11 synthetic trees: separators, zero padding, `.PNG`, duplicates, stack conflicts, repeated cno layers, symlinks, hidden files, NFC/NFD | Behaves as specified; observations in N7–N11 |
| Cost | Direct `ModelSource` models at 2000×2000 | One frame-spanning mesh component, E=10: 0.48 s. 150 nested frame-sized rings, E=10: 3.8 s. No full-frame work beyond each component's own bbox |
| Resource formulas | Code against plan §4.6; hand-specified S23 numbers; real `np.save` header | M = E+1+U+R, bool N·M, float32 4·N·M, 128-byte header, K scenario all correct |
| Status wording | grep and reading of the three modules and the docs diff | No electrical-verification claim; interpretations labelled as such |
| Test strength | 28 single-line mutants run through the unchanged tests via `-o pythonpath=<scratch copy>` | 26 killed, including wrong stack direction, fall-through, arbitrary same-layer pick, polarity, threshold, limit not restored; 2 survived (N1, N2) |
| Expected values | Reading of the three test files | Hand-specified pixels, IDs (independent SHA-256 or literals) and byte counts; not recomputed by production code |
| Repository state | `git status --short`, `git rev-parse HEAD` after all runs | Clean; HEAD still `105acea9c475678cc160b85f398d68928729f90d` |

## What I could not verify

- Real source PNGs: actual modes, bit depths, sizes, CNT density and whether real writers ever produce the B1–B3 shapes.
- Behaviour on any Pillow other than 12.3.0, or on Linux/Windows wheels; all results are macOS arm64, Python 3.12.13.
- Peak memory and runtime at real frame sizes; my measurements stop at 8000×8000 decode and 6000×6000 labelling.
- Case-sensitive or non-APFS filesystem behaviour (case-variant folders coexisting, NFD names on Linux).
- Offline `pip install .[png]` against a closed-network wheelhouse; no install was attempted.
- Physical correctness of the nearest-overlap/skipped-layer and 4-neighbour rules; these remain open user questions.
- T3/T4 scope (export, preview, CLI, train/search integration) was not reviewed.
