# T1 — Compressed hotspot mask storage (plan v1, 2026-10-22)

Risk tier: HIGH (on-disk manifest/asset format of import-hotspots; core data path read by compare/candidates).
Review route (user-specified): Codex gpt-6-astra / xhigh, read-only.

## Problem
`import-hotspots` decodes every hotspot PNG into an uncompressed bool `.npy` (1 byte/pixel,
up to 32 diameters per case) — src/simcompare/adapters/real_hotspot.py:266-267. On ETX real data this
fills the disk ("No space left on device"). Binary PNGs are tiny; the .npy copies are not.

## Target
- Repo: /Users/jonghyeonlee/git/particle-sim-compare, current worktree, branch feat/template-export.
- Writer: src/simcompare/adapters/real_hotspot.py `import_hotspots` (~line 266).
- Readers: same file, manifest validator (~line 451-470, uses np.load mmap_mode='r') and the
  case loader (~line 665). No other module reads these mask assets (verified by grep for
  mask_path / np.load in src/simcompare).
- Tests: tests/test_real_hotspot.py (+ any test that fails because of this change).
- Generated runners: bundles/simcompare_job.py and bundles/etx/simcompare_job_*.py must be rebuilt
  with the repo's own build scripts (scripts/build_single_file.py, scripts/build_etx_stage_runners.py —
  check which produces which) so they embed the new source.

## Change
1. New imports write each mask as a compressed archive: `assets/<index>_<diameter>.npz` via
   `np.savez_compressed(path, mask=<2-D bool array>)`. Record a new field
   `mask_encoding: "npz_deflate_bool_v1"` on every observed record. `mask_sha256` stays the sha256 of
   the file on disk. Do not write the uncompressed array anywhere (no staging .npy).
2. Readers accept both encodings:
   - `mask_encoding == "npz_deflate_bool_v1"`: path must end in `.npz`; load with
     `np.load(asset, allow_pickle=False)` inside a `with`; the archive must contain exactly one member
     `mask`; then apply the existing dtype/ndim/shape/area checks unchanged.
   - field absent (legacy imports already on disk): path must end in `.npy`; behave exactly as today.
   - any other value, or suffix/encoding mismatch: raise NumericContractError with a new specific code
     (e.g. 'real_mask_encoding_invalid').
   Put the load in one small helper used by both readers.
   Missing records must still have mask_path/mask_sha256 None and must not carry mask_encoding (or
   carry None) — keep the validator strict about that.
3. Do NOT bump CONTRACT_VERSION / schema_version (legacy manifests must keep validating). If you
   conclude a bump is required, stop and ask the coordinator instead.
4. Keep memory behaviour no worse than today: one mask in memory at a time during import.

## Constraints
- Python 3.8 compatible (see commit b937cec): no Path.is_relative_to, no write_text(newline=), lazy annotations ok.
- Do not touch: src/simcompare/template_export.py, docs/template_export.md, tests/test_template_export.py,
  src/simcompare/cli.py, tests/test_retired_paths.py (pre-existing uncommitted work of another task).
  The bundles are already modified in the worktree by that work; rebuilding them is expected and fine.
- No new dependencies. No changes to other stages' formats.
- Do not commit. Leave changes in the worktree.

## Acceptance
- New tests in tests/test_real_hotspot.py:
  a) import writes .npz assets with mask_encoding set, no .npy in assets, and validation + case loading
     round-trip gives identical masks/areas to the PNG input;
  b) a legacy manifest (hand-converted: .npy asset, no mask_encoding) still validates and loads;
  c) tampered cases rejected: wrong encoding value, .npz with extra/missing member, suffix mismatch,
     non-bool dtype inside the npz, hash mismatch.
  d) size check: a large mostly-empty mask (e.g. 4000x4000 with a small blob) produces an asset < 1% of
     the raw 16 MB.
- Full suite: `.venv/bin/python -m pytest -p no:warnings` passes (report counts; list any pre-existing failures
  with evidence they fail on the baseline too).
- Rebuilt bundles: tests that check bundle freshness (e.g. tests/test_single_file.py, tests/test_etx_examples.py)
  pass, and `python3 bundles/etx/simcompare_job_1_import_hotspots.py` style smoke on a tiny fixture is optional.
- worker_done summary lists files modified and the test counts.

## Baseline
HEAD f30111c on feat/template-export. Pre-existing uncommitted changes: bundles/etx/*.py, bundles/simcompare_job.py,
src/simcompare/cli.py, tests/test_retired_paths.py (modified); docs/template_export.md,
src/simcompare/template_export.py, tests/test_template_export.py (untracked).

## ETX compatibility (user requirement: must work without problems on ETX)
ETX runs the generated single-file runners in bundles/etx/ (Linux server, wrapper cannot pass argv,
Python may be 3.8, numpy/Pillow are whatever is installed there — the runner prints their versions).
- Use only numpy APIs available since numpy 1.16 (np.savez_compressed, np.load(allow_pickle=False),
  NpzFile as context manager). Do not rely on newer kwargs (e.g. no `max_header_size` usage that breaks old numpy).
- Write each .npz inside the staging folder (next to --out), never in TMPDIR; check whether the installed/old numpy
  savez path uses a temp file and, if so, note it (masks are small after compression so it is acceptable, but say so).
- Large single masks (e.g. 10000x10000) must not need more than ~2x the bool array in RAM while saving or loading.
- Partial failure (ENOSPC mid-import) must still leave no staging folder behind (existing rmtree path) and the
  error must surface in the runner log; add a test that simulates OSError(ENOSPC) during save and checks cleanup.
- Add a one-line progress/size summary to the import result or stderr (total bytes written to assets) so the ETX
  log shows disk usage. Keep stdout JSON contracts unchanged if tests rely on them.
- Rebuild ALL runners in bundles/ with the repo build scripts; the embedded source hash in their header must change,
  and existing runner tests (e.g. tests/test_etx_examples.py, tests/test_single_file.py) must pass.
- Run the stage-1 ETX runner end-to-end on a small fixture copied to a temp dir (edit JOBS there, not in the repo)
  under the oldest Python available locally (python3.8 if present, else report which), and report the log tail.
