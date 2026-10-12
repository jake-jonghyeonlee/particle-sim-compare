# DL1 PNG input — independent review R-A2 (refresh of R-A after the fix commit)

Date: 2026-10-12. Reviewer: independent Claude Opus 5.5 session, effort high, read-only
(Task `task_9b4eb09aa995`, Dispatch `ctx_3733418af780`). I did not write this code or its plan.

Verdict: FAIL for f12ab47d99184eb265a9be5bbe8168db48481d10

Candidate: `particle-sim-dl1`, branch `feat/png-input`, fix diff
`105acea9c475678cc160b85f398d68928729f90d..f12ab47d99184eb265a9be5bbe8168db48481d10` (8 files, +609/−18),
read in the context of the full diff from `03bf3e33c7107af75297aa298fa323e24018f28d`.
Previous review: `DL1_PNG_RA_REVIEW.md` (unchanged). This verdict applies only to that commit.

**Summary.** Everything the fix Task asked for is done and holds up: B1, B2, B3 and all N5 cases
now reject, N1/N2 mutants are killed, N7/N8/N10/N11 behave as documented, and IDs and hashes are
byte-identical to 105acea. The new structure validator gave no false rejection on 891 Pillow-written
files and 2,904 hand-built files, and no false acceptance on 6,621 corrupted ones.
The verdict is FAIL for one reason: I found one more input of the same class as B1/B3, where Pillow and
the validator disagree about which bytes are the image and Pillow fabricates black foreground (B4).
It was already present in 105acea and I missed it in R-A; the fix did not introduce it.
The fix is a few lines in `png_source.py`. There are 6 new notes, none blocking.

## Status of the earlier findings

| Finding | Status | Evidence |
|---|---|---|
| B1 short complete zlib stream | Fixed | My R-A inputs (64×64 1-bit with 63/32/1/0 rows; 4×4 8-bit with 3 rows) all reject with "IDAT scanline byte count is short". Dropping the last byte of the scanline stream rejects in all 2,904 matrix cases (every colour type and depth, interlaced and not). |
| B2 tRNS on 2/4-bit greyscale | Fixed | tRNS = white and tRNS = black reject at depths 1/2/4/8; unused tRNS on grey and RGB also rejects (documented as intentional). |
| B3 palette index without PLTE entry | Fixed | Index 3 with a 2-entry PLTE rejects at depths 2/4/8; missing PLTE rejects. 813 further cases with one out-of-range index at a random pixel, random row filters, depths 1–8, Adam7 and not, all reject with "palette pixel index has no PLTE entry". |
| N1 S23 misses full-frame `labels == c` | Fixed | Mutant `local = (labels == box.label)[window]` and mutant `component_slice(labels, box.label)[window]` both fail `test_png_scale.py`. |
| N2 one diagonal direction only | Fixed | Mutant `previous[k][0] <= x1` fails `test_component_rule_and_multicontact_pending`. |
| N5 lenient acceptances | Fixed | Bytes after IEND, concatenated PNG, missing IDAT CRC/IEND, wrong IDAT CRC, interlace 2, compression 1, filter method 1 and over-long stream all reject. Every byte prefix of two files and bit flips at every byte of a 1096-byte file: none accepted. |
| N7 empty electrode folder | Fixed | `M/B/` empty gives warning "electrode folder contains no PNG assets"; model stays valid. Empty `cno` gives no warning, which is right because extraction reports it. |
| N8 case variant of `cno` | Fixed | `CNO`, `Cno`, `cnO` are errors, their PNGs are not decoded, `cno_present` stays False, and other models are unaffected. |
| N10 non-model root directories | Fixed (documentation) | `docs/data_contract.md` states it; behaviour unchanged (`.hidden` invalidates the inventory, root files warn). |
| N11 NFC/NFD names | Fixed | Non-NFC model, folder and layer names each warn with their path; names and IDs are not normalised. |
| N12 representative pixel cost, double hashing | Partially fixed | Results are exact on both paths and the mask is hashed once. The fast path only covers `count × max coordinate ≤ about 2.1e9`; larger intersections use a fallback that is slower than the old code. See note A3. |
| N9, N13, N14 | Not fixed, deferred to T3 by the Task | No change, as agreed. |
| N3, N4, N6 | No action was requested | N6 (decode memory about 17–18 bytes per pixel, all masks held at once) still applies. |

## New blocking finding

### B4 — An invalid APNG `fcTL` region makes Pillow decode only part of the frame; the rest becomes foreground

- Where: `src/psdl1/png_source.py:275` (only `n_frames != 1` is checked) and
  `src/psdl1/png_source.py:218` (the validator treats `acTL`/`fcTL` as ordinary ancillary chunks).
- Input: 8×8 greyscale 8-bit PNG, every pixel white, correct CRCs, IDAT inflating to exactly 72 bytes.
  Between IHDR and IDAT: `acTL` (num_frames = 1) and one `fcTL` (sequence 0) whose region is smaller
  than the image. Without the two chunks the file decodes to 0 foreground pixels.

  | `fcTL` region (w×h @ x,y) | Result on f12ab47 |
  |---|---|
  | 8×8 @ 0,0 (valid) | accepted, 0 foreground pixels |
  | 8×4 @ 0,0 | **accepted, 32 foreground pixels** (rows 4–7) |
  | 8×7 @ 0,1 | **accepted, 8 foreground pixels** (row 0; the image is also shifted down one row) |
  | 1×1 @ 7,7 | **accepted, 63 foreground pixels** |
  | 4×4 @ 2,2 | rejected by Pillow (row length mismatch) |

- Why it happens: Pillow uses the `fcTL` region as the decode tile for frame 0, reads only that many
  rows from IDAT and leaves the rest of the frame zero, which this adapter maps to foreground. The
  validator checks IDAT against the IHDR size, so it confirms a stream that Pillow then reads differently.
- Why it blocks: the file is invalid (APNG requires the first frame's `fcTL` to cover the full image at
  offset 0), it is accepted without error or warning, and the fabricated value is foreground. That is the
  bar I applied to B1 and B3, and it is the "validator and Pillow disagree about the image bytes" case
  this refresh was asked to look for. Practical likelihood is very low, as with B3.
- Not a regression: the 105acea snapshot gives the same four results.
- Suggested fix: reject `acTL`, `fcTL` and `fdAT` in `_validate_png_structure` ("APNG is unsupported"),
  or at minimum require any `fcTL` before IDAT to equal the IHDR size at offset 0. Rejecting the three
  chunk types also closes note A1. Add the 8×4 and 8×7 fixtures.

## New notes (non-blocking)

- A1 — Other malformed APNG shapes are accepted with the IDAT (default image) pixels, so the mask is
  right but extra frames go unreported: `acTL` with num_frames = 0 followed by `fcTL`/`fdAT`; two `acTL`
  chunks; `acTL` placed after IDAT. Pillow only emits a `UserWarning` ("Invalid APNG, will use default
  PNG image") for the first two. A valid single-frame APNG (`acTL` = 1, full-size `fcTL`) is accepted correctly.
- A2 — Palette validation is a pure-Python loop of about 80 ns per pixel
  (`png_source.py:128-151`). Measured validator time per file: 2000×2000 0.31–0.34 s and 6000×6000
  2.9–3.3 s for 1-bit and 4-bit palettes, for 8-bit palettes with a non-zero row filter, and for a
  Pillow-written `P` file. The same sizes in greyscale take under 0.01 s, and 8-bit palette with filter 0
  takes 0.33 s at 6000×6000. Correct, but a source tree of indexed PNGs pays seconds per file. Skipping
  the index scan when PLTE has `2**depth` entries, and using NumPy for the rest, would remove most of it.
- A3 — N12 is only partly faster (`png_connections.py:118-129`). Timings for a full square intersection:

  | Side | Pixels | Path | New | Old (105acea) |
  |---|---|---|---|---|
  | 1000 | 1,000,000 | int64 | 0.01 s | 0.27 s |
  | 1280 | 1,638,400 | int64 | 0.01 s | 0.45 s |
  | 1300 | 1,690,000 | Python | 0.70 s | 0.47 s |
  | 2000 | 4,000,000 | Python | 1.68 s | 1.19 s |

  The fallback no longer builds a tuple list, so it uses less memory, but it is about 40 % slower than
  before. Subtracting the bbox-local minimum is not needed (coordinates are already local); centring on
  the integer mean before scaling would widen the int64 range considerably.
- A4 — Test gaps found by mutation (production code is correct in each case, verified by my matrix):
  - Adam7 fixtures are 3×3 only, so passes 2 and 3 are never non-empty; changing pass 2's start column survives.
  - The packed-index fixtures are bit-symmetric; reversing the shift order at `png_source.py:150` survives.
  - Swapping the Paeth tie order at `png_source.py:142` survives.
  - `test_ra_b3_missing_palette` matches `"PLTE"` against a message that contains the file name
    `missing-PLTE.png`; with the PLTE requirement removed it still passes on an unrelated `TypeError`.
  - No test ties an extracted component's `mask_sha256` or `component_id` to its mask; hashing `~local`
    at `png_connections.py:171` survives. This matters more now that the ID takes a precomputed hash.
  - The int64 bound is not exercised near overflow; replacing it with `True` survives.
- A5 — Pillow fully decodes the file before the validator runs: `image.getexif()` at
  `png_source.py:277` triggers `load()` for PNG, so the comment at line 283 is inaccurate and many
  malformed files are reported with Pillow's message rather than the validator's. Pixels are still only
  used after validation, so this is not a correctness problem. It does mean tests that assert only
  `PNGSourceError` (the N5 and critical-order groups) do not prove the validator caught the case; five
  of my validator mutants survived for that reason. Moving `_validate_png_structure` before `getexif()`
  would make the ordering match the documentation.
- A6 — Strictness that will reject some real files, all documented and intentional: any tRNS on
  greyscale or RGB even if unused, any non-255 palette alpha even if unused, bytes after IEND, a bad CRC
  on an ancillary chunk, and XMP orientation other than 1. Worth one line in USAGE so users know to re-save.

## What I verified and how

Commands were run from `/Users/jonghyeonlee/git/particle-sim-dl1` with `PYTHONDONTWRITEBYTECODE=1`;
scratch scripts and inputs live only in `/tmp/dl1-ra2-review.8wmfN9`. Pillow 12.3.0, macOS arm64, Python 3.12.

| Check | Method | Result |
|---|---|---|
| Suites | `.venv/bin/python -m pytest -p no:cacheprovider -p no:warnings` on the three PNG test files, and the full default suite | 155 passed; 350 passed, 1 skipped |
| Scope | `git diff --name-only 105acea f12ab47`; `git diff --stat 03bf3e3 f12ab47` on `schema.py`, `model.py`, `data.py`, `requirements.lock`, `pyproject.toml`; `git diff --check` | Exactly the eight listed files; versions, NPY loader and lock untouched (`pyproject.toml` has only the earlier one-line extra); no whitespace errors |
| R-A reproductions | My R-A scripts `dec.py` and `dec2.py` unchanged against the new commit | B1, B2, B3 and every N5 case reject. 24 inputs accepted, all valid, all with correct polarity. No truncated prefix and no bit flip accepted |
| Validator: valid files | Hand-built encoder, independent of Pillow and of `tests/png_helpers.py`: colour types 0/2/3/4/6 at every supported depth, with and without Adam7, 132 sizes including 1×1, every width 1–10 and 15/16/17/31/33/64/65, random row filters 0–4, random zlib level, IDAT split at random points or one byte per chunk, zero-length IDAT chunks | 2,904 files, 0 rejected, every mask equal to the source pattern |
| Validator: corrupted files | Same matrix with one scanline byte removed, one added, or one palette index out of range | 2,904 short, 2,904 long, 813 out-of-range: 0 accepted |
| Padding bits | Rows at depths 1/2/4 with all padding bits set | 1,152 files accepted with unchanged masks; a 1-entry PLTE with only a padding bit set is accepted, with index 1 used it is rejected |
| Pillow-written files | Modes `1`, `L`, `RGB`, `LA`, `RGBA`, `P` at bits 1/2/4/8, adaptive and web palettes, 9 sizes from 1×1 to 300×301, with `optimize`, compress levels 0/1/9, `dpi`, tEXt/zTXt/iTXt, ICC profile and EXIF | 891 files, 0 failures. 1500×1700 files with 6–41 IDAT chunks decode correctly |
| Ancillary chunks | 22 chunk types (gAMA, pHYs, tEXt, zTXt, iTXt, iCCP, tIME, sRGB, cHRM, bKGD, sBIT, hIST, sPLT, eXIf, private and vendor chunks, a 200 KB tEXt, an empty tEXt) before and after IDAT | All accepted with correct pixels |
| Validator/Pillow disagreement | 36 probes: `fdAT`/`DDAT` after IDAT, APNG variants, PLTE on RGB and grey, `CgBI`, tRNS order and length, PLTE longer than `2**depth`, second zlib stream, junk IDAT, preset dictionary, raw deflate, gzip, small zlib window, bad chunk names, XMP orientation | Consistent except B4 and A1 |
| Memory and time, large files | `tracemalloc` around `_validate_png_structure`: 9000×9000 grey8 all white (99 KB compressed, 81 MB inflated) as one IDAT, 64 KB IDATs, 7-byte and 1-byte IDATs; 9000×9000 1-bit noise; 4000×4000 RGBA | Peak 0.09–0.45 MB in every case; 0.05–0.09 s, or 1.4 s with 99,358 one-byte IDAT chunks. No pending-output false rejection |
| N12 arithmetic | Read the bound: `|dy| ≤ count·max(y)`, `|dx| ≤ count·max(x)`, guard on the sum of squares. 4,000 random masks against `fractions.Fraction`; tie cases; seven large cases on both sides of the bound (up to 47,000×47,000 sparse and 2000×2000 dense) against exact Python or object-dtype integers | 0 mismatches; path selection matches the bound; `argmin` first-minimum gives the y-then-x tie rule |
| IDs and hashes unchanged | Six generated models (1010×1040, files in modes `1`/`L`/`RGB`/`RGBA`/`P`), inventory + extraction + products serialised with array hashes, on an archived 105acea snapshot and on the candidate | Byte-identical output (353 components, 5 relations); each `mask_sha256` and `component_id` recomputed from the mask matches |
| Extraction | My R-A brute-force comparison `alg.py` unchanged | 0 mismatches on 3,000 labellings, 3,000 representative pixels, 1,500 models (12,664 components, 1,552 resolved); endpoint float checks 0 failures |
| Mutants | 53 single-change mutants in scratch copies through the unchanged tests | 41 killed, including N1, N1b, N2 and all three inventory changes. 6 survive only because another check also rejects. 6 are real test gaps (A4) |
| Inventory | 9 synthetic trees for N7, N8, N10, N11 | As documented |
| S23 | `tests/test_png_scale.py --durations=1`, three runs | 0.31–0.32 s for the whole test including decoding, against the 2.0 s guard on the extraction section |
| Repository state | `git status --short`, `git rev-parse HEAD` after all runs | `particle-sim-dl1` clean, HEAD `f12ab47d99184eb265a9be5bbe8168db48481d10`; `DL1_PNG_RA_REVIEW.md` untouched |

## What I could not verify

- Real source PNGs: their modes, sizes, writers, and whether any carry tRNS, trailing bytes or APNG chunks.
- Files from writers other than Pillow and my own encoder (libpng tools, Photoshop, ImageMagick, optimisers); none are installed and nothing was fetched.
- Any Pillow other than 12.3.0, or Linux/Windows; B4 depends on Pillow's APNG handling and may differ by version.
- The worker's 0.043 s figure for the S23 timed section; I measured only the whole test.
- The int64 fast path on real intersections; my timings are synthetic full squares.
- Peak process memory for a full `decode_png` at very large frames beyond R-A's N6 measurement; only the validator was measured here.
- N9, N13, N14 and all T3/T4 scope, which were not part of this candidate.
