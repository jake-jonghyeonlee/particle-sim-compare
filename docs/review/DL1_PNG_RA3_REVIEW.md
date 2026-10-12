# DL1 PNG input — independent review R-A3 (gate for the T1 + T2 scope)

Date: 2026-10-12. Reviewer: fresh independent Claude Opus 5.5 session, effort high, read-only
(Task `task_3a43893fef9d`, Dispatch `ctx_d85ab5a2ba44`). I did not write this code or its plan.

Verdict: PASS-WITH-NOTES for 6f82f47b0190521b95677f4f0fe9dae3b6617a6a

Candidate: `particle-sim-dl1`, branch `feat/png-input`, fix diff
`f12ab47d99184eb265a9be5bbe8168db48481d10..6f82f47b0190521b95677f4f0fe9dae3b6617a6a` (6 files, +324/−28),
read in the context of the full diff from `03bf3e33c7107af75297aa298fa323e24018f28d` (9 files, +2856).
Earlier reviews: `DL1_PNG_RA_REVIEW.md`, `DL1_PNG_RA2_REVIEW.md` (both unchanged).
This verdict applies only to that commit.

**Summary.** B4 is fixed and I found no new blocking input. Every APNG chunk is now rejected by the
validator before Pillow opens the file, the validator and the pixel guard run before any Pillow
decoding, and the remaining notes A1–A6 are closed or documented. Across 3,505 corpus files and
about 19,600 palette files there was no false acceptance, no false rejection and no wrong mask.
IDs and hashes are byte-identical to 105acea. The full diff `03bf3e3..6f82f47` is acceptable as the
base for T3. There are 6 new notes; the one worth acting on in T3 is C1 (the file is read several
times, so validated bytes and decoded bytes are only the same if the file does not change).

## Status of B4 and A1–A6

| Finding | Status | Evidence |
|---|---|---|
| B4 invalid `fcTL` region fabricates foreground | Fixed | `png_source.py:195-196` rejects `acTL`, `fcTL`, `fdAT` on the chunk header, before CRC or payload. My R-A2 inputs (8×4, 8×7@0,1, 1×1@7,7, 4×4@2,2, full 8×8) all give "APNG is unsupported". 110 further APNG files (10 shapes × 11 colour type/depth combinations, chunks before and after IDAT) all reject; f12ab47 accepted 78 of them. |
| A1 other malformed APNG shapes | Fixed | `acTL` = 0 with `fcTL`/`fdAT`, duplicate `acTL`, `acTL` after IDAT, lone `fcTL`, lone `fdAT` all reject with the same reason. A valid single-frame APNG is now also rejected; that is intentional and documented. |
| A2 palette validation cost | Fixed for the common case | Index scan is skipped when PLTE has `2**depth` entries and uses NumPy otherwise. 6000×6000: 1-bit 0.00 s, 4-bit 0.12 s, 8-bit 0.06 s (was 2.9–3.3 s). Residual: note C2. |
| A3 representative pixel | Fixed | New arithmetic is exact and the int64 path now covers every realistic intersection (a full square stays on it up to about 65,000 pixels a side). 2000×2000 full square: 0.036 s (was 1.68 s). |
| A4 test gaps | Closed | The six mutants are killed by the unchanged test suite (table below). The R-A2 Paeth mutant turned out to be an equivalent mutant; see note C4. |
| A5 Pillow decoded before the validator | Fixed | `_validate_png_structure` is called at `png_source.py:279`, before `Image.open`. Removing the call fails 55 tests; moving it after `image.load()` fails 17. For an over-limit or APNG file `Image.open` is never called (counted: 0 calls). |
| A6 intentional strictness undocumented | Documented | `docs/data_contract.md` lists the rejected features and the remedy (re-save as plain opaque black/white PNG). USAGE is not changed; it was outside this fix's file scope. See note C5. |

## New blocking findings

None.

## New notes (non-blocking)

- C1 — The file is opened several times and nothing ties the reads together. One `decode_png` opens
  the path three times (`_header_shape` at `png_source.py:78`, the validator at `:187`, `Image.open`
  at `:280`), and `discover_source` reads it a fourth time for the source SHA-256 (`:423`).
  Demonstration: a hook that replaces the file right after validation with an APNG-subregion file
  (the B4 shape) is accepted with a fabricated foreground row. This needs the source tree to be
  modified during the import, in a window of milliseconds, so it is below the blocking bar, and
  f12ab47 had the same structure. It does mean the recorded file hash, the validated bytes and the
  decoded mask are not guaranteed to describe the same bytes. Suggested fix: read the bytes once,
  then validate, decode from `io.BytesIO` and hash that same buffer (cost: the compressed size in memory).
- C2 — A palette smaller than `2**depth` combined with non-zero row filters still unfilters in a
  Python loop per byte (`png_source.py:128-143`): 8-bit, filter Up, 2000×2000 0.30 s and 6000×6000
  2.74 s. Pillow writes palette images with filter 0, so this only affects files from other writers.
- C3 — Remaining test gaps (production code is correct in each case, shown by my matrices):
  - Changing the packed-depth comparison to `samples > entries` (`png_source.py:150`) survives. No
    test uses an index equal to the PLTE entry count at depth 1, 2 or 4; the fixtures use index 3 with 2 entries.
  - Removing the validator's pixel pre-check (`png_source.py:244-251`) survives, because Pillow's own
    guard at `Image.open` raises the same exception classes. Nothing asserts that the limit is applied
    before IDAT inflation. Behaviour is correct: a 100000×100000 header with a junk IDAT reports
    `DecompressionBombError` at the default limit, not a zlib error.
- C4 — The R-A2 mutant "above before left on a Paeth tie" still survives, and I now believe it is an
  equivalent mutant: left and above can only tie for the minimum when they are the same value. The
  new left versus upper-left test is the meaningful one and kills that variant. No action needed.
- C5 — The strictness list is in `docs/data_contract.md` only. When T3/T4 touch the Korean USAGE guide,
  add the same one-line remedy there, including that APNG files (even single-frame) must be re-saved.
- C6 — The pre-check's message is shorter than Pillow's (it omits ", could be decompression bomb DOS
  attack."). `exception_class`, `pixels` and `effective_max_image_pixels` in the issue are identical
  to f12ab47, so only code that matches the full Pillow sentence would notice.

Still open from earlier reviews, deferred to T3 as agreed: N6 (decode memory about 17–18 bytes per
pixel, all masks held at once), N9 (in-root symlink aliases), N13 (three meanings of "complete"),
N14 (exact Pillow pin not enforced at run time).

## What I verified and how

Commands were run from `/Users/jonghyeonlee/git/particle-sim-dl1` with `PYTHONDONTWRITEBYTECODE=1`;
scratch scripts, snapshots and inputs live only in `/tmp/dl1-ra3-review.r0tUgY`.
Pillow 12.3.0, NumPy 2.5.3, Python 3.12, macOS arm64. Snapshots of 105acea and f12ab47 were taken
with `git archive` into scratch for comparison.

| Check | Method | Result |
|---|---|---|
| Suites | `.venv/bin/python -m pytest -p no:cacheprovider -p no:warnings` on the three PNG test files, and the full default suite | 212 passed; 407 passed, 1 skipped |
| Scope | `git diff --name-only f12ab47 6f82f47`; `git diff --check`; `git diff --stat 03bf3e3 6f82f47` on `pyproject.toml`, `requirements.lock`, `schema.py`, `model.py`, `data.py` | Exactly the six listed files; no whitespace errors; versions, NPY loader and lock untouched; `pyproject.toml` has only the earlier `png = ["Pillow==12.3.0"]` line |
| Validator ordering (A5) | Code reading; counting `Image.open` calls; mutants X3, X4 | Validator and pixel pre-check run first; Pillow is not opened for rejected structure |
| Pillow chunk handlers | Read installed `PngImagePlugin.py`: every `chunk_*` handler, `_open`, `load_read`, `load_end`, `getexif` | Only `acTL`/`fcTL`/`fdAT` change the decode region or frame; they are matched case-sensitively, as the validator does. `PLTE` and `tRNS` affect pixels only through paths the validator constrains. Other handlers only fill `info` |
| Corpus, candidate vs f12ab47 | 3,505 hand-built and Pillow-written files run through both commits: 11 colour type/depth bases; 44 ancillary chunk variants before and after IDAT (gAMA incl. 0 and huge, cHRM, sRGB, iCCP valid and corrupt, bKGD, sBIT, pHYs, hIST, sPLT, cICP, mDCV, cLLI, oFFs, text chunks, eXIf/XMP/raw-profile orientation 1, case variants `acTl`/`fcTl`/`fdAt`, private chunks); orientation 2/3/6/8 by three carriers; PLTE on RGB/RGBA/grey; critical and reserved-bit name variants; every byte truncation and one bit flip per byte of each base; 275 Pillow files in modes `1`/`L`/`RGB`/`LA`/`RGBA`/`P` at bits 1/2/4/8, interlaced and not | 1,256 valid files all accepted with the exact intended mask. 2,249 invalid, APNG, orientation, truncated or bit-flipped files all rejected. Only verdict change against f12ab47: 78 APNG files accept → reject. No accepted mask differs between the two commits |
| My R-A and R-A2 scripts | `dec.py`, `dec2.py`, `v1.py`–`v5.py` unchanged | B1–B3 and N5 still reject; 2,904 valid files 0 rejected; 2,904 short, 2,904 long, 813 out-of-range 0 accepted; 1,152 padding-bit files unchanged; 891 Pillow-written files 0 failures; 1500×1700 files with 6–41 IDAT chunks correct; validator peak memory 0.09–0.45 MB at 9000×9000 |
| A2 palette validation | New independent encoder: depths 1/2/4/8; PLTE sizes 1–17, half, half+1, 100, 200, 255, full; 19 sizes with widths 1, 2, 3, 5, 7, 8, 9, 13, 15, 16, 17, 31, 33, 64, 65, 127; Adam7 and not; filter 0, 1, 2, 3, 4 fixed and random per row; random padding bits; random IDAT splits. Each case also with one pixel set to the entry count, to `2**depth − 1`, or a random undefined index | 9,348 valid and 912 full-palette files accepted with exact masks; 9,348 out-of-range files all rejected with "palette pixel index has no PLTE entry". 30 wide configurations (rows up to 300,007 pixels, crossing 65,536-byte inflate blocks, padding bits all set) correct, and an undefined index at each corner rejected |
| A3 arithmetic | Derivation: with `sum = n·q + r`, `(n·y − sum)² = n²·dy² − 2n·r·dy + r²`; dropping `r²` and dividing by `n` keeps the order and stays integral. Bound: each term and every partial sum is at most `n(by²+bx²) + 2·ry·by + 2·rx·bx`; `r > 0` implies `b ≥ 1`, so the scalar `2r` is covered | Sound |
| A3 against exact reference | `fractions.Fraction` reference: 5,000 random masks; 24,000 injected coordinate sets (bound straddling int64 max, elongated up to 1e12, offsets up to int64 max − 1e6, four-way ties); 8,000 sets sweeping √(int64 max); 4,000 sets auditing every intermediate with Python integers; dense 2000×2000, 1×3,000,000, 3,000,000×1, 3000×1500 and an L-shaped far-offset mask | 0 mismatches; path taken equals the bound rule in every case (16,862 int64, 7,138 exact); no intermediate above int64 when the bound says safe; tie rule y then x on both paths |
| IDs and hashes unchanged | Six generated models (1010×1040, modes `1`/`L`/`RGB`/`RGBA`/`P`), inventory + extraction + products serialised with array hashes, on the 105acea snapshot and on the candidate | Byte-identical (353 components, 5 relations); each `mask_sha256` and `component_id` recomputed from its mask matches |
| Extraction | My R-A brute-force comparison `alg.py`, run once | 0 mismatches on 3,000 labellings, 3,000 representative pixels, 1,500 models (12,664 components, 1,552 resolved); endpoint float checks 0 failures |
| Pixel limit | 64×64 file with override 4096, 4095, 2048, 2047, 1, 1e9; invalid overrides `0`, `-1`, `1.5`, `True`, `"9"`; global set to `None` with and without override; global checked after each of 3,505 decodes; all compared with f12ab47 | Accept at limit; `DecompressionBombWarning` above limit; `DecompressionBombError` above twice the limit; invalid overrides and a disabled global raise `ValueError`; override works with a `None` global and restores `None`; global and `LOAD_TRUNCATED_IMAGES` always restored. Identical to f12ab47 except the message text (C6) |
| Mutants | 24 single-change mutants in scratch copies of `src`, import path verified, unchanged tests | 20 killed, including all six A4 mutants (Adam7 pass-2 column 5 failures, reversed shifts 3, Paeth upper-left tie 1, PLTE requirement 1, hash of `~local` 1, bound replaced by `True` 1), APNG check removed (10), `fcTL` dropped from the set (1), shortcut widened (4), padding counted (3), remainder terms dropped or sign-flipped, tie kept last. 4 survive: one equivalent (C4), two masked by Pillow's guard and one real gap (C3) |
| Timings | `v5.py`, `a3.py` on this machine | Consistent with the worker's figures: validator 6000×6000 1-bit palette under 0.01 s; representative pixel 2000×2000 0.036 s |
| Documentation | Read the new `docs/data_contract.md` section against the code and tests | Accurate, including the fixture sizes and the stated bound margin 1,505,827,713 (recomputed) |
| Repository state | `git status --short`, `git rev-parse HEAD` after all runs | `particle-sim-dl1` clean, HEAD `6f82f47b0190521b95677f4f0fe9dae3b6617a6a`; existing `__pycache__` directories predate this review and are git-ignored |

## What I could not verify

- Real source PNGs: their modes, sizes, writers, and whether any carry tRNS, trailing bytes, APNG chunks or filtered small palettes.
- Files from writers other than Pillow and my own encoders (libpng tools, Photoshop, ImageMagick, optimisers); none are installed and nothing was fetched.
- Any Pillow other than 12.3.0, or Linux/Windows. The structural checks no longer depend on Pillow, but pixel expansion and the chunk-handler reading above are for 12.3.0 only.
- The worker's "before" timings (3.38 s and 1.72 s) and its own mutant and identity runs; I re-ran equivalents instead.
- Concurrent modification of a source tree in practice (C1); I only showed the gap with an injected swap.
- Peak process memory for a full `decode_png` at very large frames beyond R-A's N6 measurement.
- N9, N13, N14 and all T3/T4 scope, which were not part of this candidate.
