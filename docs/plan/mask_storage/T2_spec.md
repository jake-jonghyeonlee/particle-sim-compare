# T2 — Fix R1 advisories on compressed mask storage (plan v1.1, 2026-10-22). Risk tier: HIGH.

Context: T1 (docs/plan/mask_storage/T1_spec.md, read it) is implemented and uncommitted in this worktree.
Review R1 (Codex Astra xhigh) gave PASS-WITH-ADVISORIES; fix these four items.

## Target / Ownership (allowed writes)
- src/simcompare/adapters/real_hotspot.py (only `_load_mask`, `load_real_manifest` mask loop, and tests-related bits)
- tests/test_real_hotspot.py
- bundles/simcompare_job.py, bundles/etx/simcompare_job_*.py — regenerate ONLY via the repo build scripts.
Do not touch template_export.py, cli.py, docs/template_export.md, tests/test_template_export.py, tests/test_retired_paths.py.

## Change
1. Validator memory (real_hotspot.py ~load_real_manifest, ~line 484): at most one mask alive at a time — drop the reference
   (e.g. `del mask` / reuse pattern) before the next load. Restore `mmap_mode='r'` for the legacy `.npy` path in the
   validator only (add a parameter to `_load_mask`, e.g. `mmap=False`; configured_dataset keeps full loads as today).
2. `_load_mask`: after reading the npz member, require `isinstance(value, np.ndarray)`; otherwise raise
   NumericContractError('real_mask_encoding_invalid', mask_path). Add a test: one-member archive whose `mask.npy` holds
   non-NPY bytes (hashes updated so the hash check passes) -> real_mask_encoding_invalid (not AttributeError).
3. ENOSPC test: simulate failure AFTER partial output — first asset written completely, second save writes some bytes then
   raises OSError(errno.ENOSPC). Assert the exception propagates, no `<out>.tmp-*` staging dir and no `<out>` remain.
   Also (separate test, using the stage-1 runner or its _main/_Tee path if practical in-process, otherwise subprocess on a tiny
   fixture with a monkeypatched failure) assert the error text appears in the runner log file. If the runner log test is
   impractical, say why in worker_done instead of faking it.
4. Legacy round trip test must go through configured_dataset as well as load_real_manifest, comparing masks with the
   new-format import (identical arrays).

## Constraints
Same as T1: Python 3.8 syntax, numpy >= 1.16 APIs only, no schema bump, no new deps, no commit.

## Acceptance
- `.venv/bin/python -m pytest -p no:warnings` full suite: report counts; the only allowed failure is
  tests/test_handoff.py::test_offline_full_candidate_in_temporary_git_archive (sibling repo dirty, pre-existing).
- Bundles regenerated; tests/test_single_file.py and tests/test_etx_examples.py pass.
- worker_done lists files modified, new test names, counts.

## Baseline
HEAD f30111c + T1 uncommitted changes (diff fingerprint of real_hotspot.py+test_real_hotspot.py before T2: see README checkpoint)
+ unrelated template-export uncommitted work.
