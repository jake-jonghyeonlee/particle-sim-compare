# DL1 review: f0a5598 (base d4dd896)

- **Reviewer:** fresh read-only Claude Opus 5.5 session (effort high), separate from the coordinator and implementers. 2026-10-18.
- **Risk tier:** HIGH.
- **Candidate:** `f0a559815b8f538c8e8b8b599a67c18f6209dc87` (branch `feat/png-input`, clean tree).
- **Range:** `d4dd896..f0a5598`, 13 commits.
- **Coordinator evidence:** DL1 full suite at f0a5598 → 862 passed, 1 skipped (191 s).

## Verdict

**PASS-WITH-NOTES** for f0a559815b8f538c8e8b8b599a67c18f6209dc87. It may be pinned as the DL1 commit in the C/DL1/DL2 handoff bundle.

## Blocking findings

None. No concrete failure scenario was found.

## Non-blocking notes

1. **The full Windows ZIP ships the older psdl1 code, so the update wheel is required.**
   - The ZIP's `manifest.json` `application_sources` match `78a2f91:src/psdl1/*` exactly.
   - They differ from f74a718 and f0a5598 only in `psdl1/_png_diagnostic_render.py`.
   - To run f0a5598 code, users must also install `bundles/updates/grid-diagnostics-f74a718` with `--force-reinstall`. This is documented; the version number stays 0.1.0 in both cases.
   - The build manifest records a dirty tree at build time (`git_status_at_build`). The dirty files were scripts and docs only; the `src` hashes still match 78a2f91.

2. **After the update, the bundle's own checks will fail closed.** From reading the code; not run on Windows.
   - `verify_windows_bundle.py --installed-prefix` and `install_windows_bundle.py --reuse-venv` compare installed sources against the ZIP manifest. They will report `installed psdl1 source differs: psdl1/_png_diagnostic_render.py` (scripts/verify_windows_bundle.py:208-212).
   - The update README says to copy `grid-diagnostics-f74a718/` into the folder that holds `.venv-cmd`, which is the bundle folder. Later full-bundle verification will then fail its strict inventory check with `inventory differs` (scripts/verify_windows_bundle.py:166-169).
   - Both failures are loud, not silent. Consider documenting this in the update README.

3. **The update folder is not protected from line-ending conversion.**
   - `.gitattributes` marks only `bundles/windows_cp312_win_amd64.parts/*` as `-text`.
   - On a Windows Git checkout with `core.autocrlf=true`, the text files in `bundles/updates/grid-diagnostics-f74a718/` (README.md, BUILD.json, VALIDATION.json, requirements-update.txt) could be rewritten to CRLF. A manual check against `SHA256SUMS.txt` would then fail for those files.
   - The wheel itself is still bound by pip `--require-hashes`, so installation is unaffected. Inferred; not reproduced on Windows.

4. **The transfer self-check catches accidental corruption, not tampering.**
   - `parts.json`, which holds `reassemble_sha256` and the part and archive hashes, sits in the same folder as `reassemble.py`. The script checks its own hash, so this only detects corruption in transit.
   - The trust anchor is the Git commit. Pin the commit hash in the handoff record.

5. **The tests do not check the committed artifacts.**
   - `tests/test_windows_bundle.py` uses synthetic bundles. It does not check the committed parts, the ZIP, or the update wheel; these were checked by hand (see below).
   - The update's own `BUILD.json` says `windows_execution_tested: false`.

6. **Source-change detection matches the eager path, with one documented limit.**
   - A reload checks every recorded file, including every merged CNT provenance file. It compares the file SHA, decoded mode, shape and mask hash, then compares replayed extraction and resource evidence (png_source.py `_reload_model`, png_connections.py `_reload_extraction`).
   - Files added after discovery are not picked up. This is documented snapshot behaviour and matches the eager path.
   - If there are no grids, preview or export, there is no reload and no check, because nothing is re-read.

7. **Two new failure behaviours.**
   - Diagnostic render or ledger I/O errors count as grid failures. Combined with `--export`, they block export (`inspect_grid_evaluation_failed`). This matches the old grid-failure behaviour but adds more ways to fail.
   - A progress or event transport failure after publication raises `RuntimeError` even though the bundle is already published and verified (import_png.py:646-653). This is documented and tested.

8. **Older verifiers still accept new bundles.** The d4dd896 `verify_bundle` returns `verified` on f0a5598 export bundles both with and without `--inspect-grids` diagnostics. The old verifier only checks the diagnostic files' hashes, not their content.

## What was checked

**Streaming import compared with the old eager import**
- Full diffs of `png_source.py`, `import_png.py`, `png_connections.py`, `png_contract.py`, `data.py` and `png_preview.py`.
- Stack-order resolution: `resolve_stack_order` was already a per-model map, so applying `resolve_model_order` per model during the scan is equivalent.
- Extraction: `extract_inventory` was already a per-model map, so streaming `extract_model` is equivalent.
- Global identity claims: `emitted_ids` and traversal order are preserved.
- Merged image-only CNT duplicates: the first-decoded file keeps its mask and becomes the representative. `binding` includes the mask SHA, so different geometries never merge.
- Late manifest errors: duplicate product IDs and pitch differences are raised when the iterator is exhausted, inside the `for` loop and before `manifest.pending.json` is renamed.
- No partial publication: source-change and progress-sink errors are raised outside the recoverable grid `try`, so the stage is never published.
- Grid evidence: `binary_mask` returns float32 0/1, so the cached scalars equal the old `check_resampling` recomputation.

**Tests and reproductions run**
- `tests/test_png_streaming_equivalence.py` and `tests/test_png_streaming.py`: 69 passed. The equivalence test builds the real d4dd896 code with `git archive` and compares bundle bytes (manifest, NPY, preview, report minus only the documented `overhead_note` and `diagnostic` additions) for image_only and relations, inspect and export, invalid, duplicate and partial inputs, and `--no-preview`.
- `tests/test_windows_bundle.py` and `tests/test_png_streaming_memory.py`: all passed.
- Fuzz check: 300 random masks comparing `label_components` with the old `_components`, plus 120 random Products (valid holes, missing masks, optional CNT, grids 2-80 including upsampling). The `grid_check` from `inspect_grid_diagnostics` equals the old `data.grid_check` in every case: **0 mismatches**.

**Grid diagnostics**
- Support intervals in `correspondence`: `floor(x*nw/W)` to `ceil((x+1)*nw/W)` is the exact inverse of Torch's adaptive area bins, so the `target component has no source support` RuntimeError cannot be triggered by valid input.
- Nothing in the code chooses a grid (no candidate or recommendation logic).
- Memory per sink is bounded: fixed VIEW-sized buffers, at most 3 insets and at most YELLOW_DETAILS examples, and the ledger streams to disk.
- `verify_diagnostic_artifacts` ties each artifact's namespace, hash, PNG header, ledger and directory set to the report.

**Windows bundle**
- Parts: the SHA256 of each of the 4 parts and of `reassemble.py` matches `parts.json`. The concatenated parts hash to `9d677ad8…abdf`, as recorded. `reassemble.py` is byte-identical to `scripts/transfer_windows_bundle.py`.
- Join failure: reassembly with one changed byte in part004 fails with `part integrity failed` and creates no output. A CRLF copy of the helper is accepted only through its canonical-LF hash.
- Reassembled ZIP: extracted and verified with `verify_windows_bundle.py`: `OK (13 wheels)`. Appending one byte to a wheel gives rc=1 `integrity check failed`. Each third-party wheel's hash equals its recorded PyPI `published_sha256`.
- Bundle contents: the bundled scripts are identical to the repo's scripts.
- No network: the installer uses pip `--no-index --only-binary=:all: --require-hashes --find-links=wheelhouse`. There is no download path.
- Update wheel: `SHA256SUMS.txt` verifies OK. The wheel's `psdl1/*` files are byte-identical to `f74a718:src/psdl1/*`, with no missing or extra files, and RECORD hashes are consistent. `git diff f74a718 f0a5598` touches no `src`, so the wheel also equals the f0a5598 sources. The `_join_seam` import check correctly tells it apart from 78a2f91.

## What was not checked

- Windows execution of the installer, reassembly, update or smoke steps. Everything Windows-specific is from reading the code or running it on macOS.
- PyPI provenance of third-party wheels beyond the hashes recorded in the manifest (offline review).
- The large binary wheels' internal contents, beyond the metadata checks the verifier runs.
- Line-by-line correctness of diagnostic PNG rendering and ledger semantics (event and site witness selection, yellow aggregation, insets). Only grid_check equivalence, memory bounds and verifier binding were checked.
- The full test suite (coordinator evidence used); `tests/test_png_diagnostic.py` was only skimmed, not run.
- Production-scale memory or RSS behaviour.
