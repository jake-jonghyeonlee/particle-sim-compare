# F-R2 re-review after F3. Reviewer: Claude Opus 5.5 / high. Read-only.
Candidate: working tree vs HEAD c7a2829 (feat/template-export), 2026-10-23. The tracked parts of F_candidate_v2.diff match `git diff HEAD` exactly.

## Verdict: FAIL. B1 is fixed, but F3 adds one blocking regression (B2), which has a small fix.

## Blocking

**B2: the F3 wrapper also keeps a half-written result from compare's non-transactional writers in real mode.**
`src/simcompare/pipeline.py:606-614` marks *every* exception `keeps_previous_outputs` whenever `mode: real`. Two real-mode branches of
`_run_numeric_compare` write straight into `output_dir`, with no publication transaction:
- real_hotspot_v1 without `condition_assignment` (the inspection branch): `_write_inspection` at `pipeline.py:689`, `pipeline.py:45-57`.
  It writes comparison.json first, then comparison.csv.
- numeric_v2 data in `mode: real`: `write_numeric_report` at `pipeline.py:699`, `report.py:231-251`. It writes comparison.json, then
  each comparison_*.csv, then the HTML. Any error between those writes (for example ENOSPC) leaves a mix. I traced this branch in the
  code only; the repro below covers the inspection branch.

If one of these writers fails partway, `cli.py:297` now skips `_remove_reports`. The folder is left holding new and old files together,
and error.json is written beside them. Before F3 (F1 state), the same error removed every `comparison*` report, so the folder had no
mixed state.

Repro (`/tmp/r2/repro/test_r2_torn.py`, using the fixtures from tests/test_compare_publication_cli.py):
1. Publish a normal stage-2 generation.
2. Remove `condition_assignment` from both sides.
3. Make `csv.DictWriter.writerows` raise ENOSPC.
4. Run `compare`.
- Current tree: exit 2; comparison.json CHANGED (new inspection payload), comparison.csv CHANGED (header only, truncated),
  comparison_masks.zip is the old one, error.json is present.
- Same tree with only the wrapper's flag removed (`/tmp/r2/src_pre`): exit 2; all three files removed, error.json present.

This contradicts usage.md:137, "새 파일을 전부 임시 위치에 쓴 뒤 한 번에 바꾸므로, 실패하거나 … 디스크가 차도 이전 결과가 그대로 남는다 …
실자료 compare … 모든 오류에서 … 이전 결과는 지우지 않는다". On these branches the previous result is overwritten, not left as it was.

The documented ETX flow is not affected, because real.stage2 always has `condition_assignment`. The failure is also flagged by exit 2 and
error.json. Even so, this is the torn state that the publication work exists to prevent, and F3 introduced it. The fault class is real:
ETX has already hit ENOSPC.

Fix (small):
- Do not mark failures raised by the non-transactional writers. For example, wrap the `_write_inspection(...)` and
  `write_numeric_report(...)` calls in `except Exception as exc: exc.keeps_previous_outputs = False; raise`, and have the wrapper set
  the flag only when it is still unset (`if cfg.get('mode') == 'real' and getattr(exc, 'keeps_previous_outputs', None) is None`).
- Alternatively, mark only `_real_source` / `mixed_real_and_numeric_source_formats` (plus the existing assignment-branch try). That
  still covers both B1 repros, because the moved-folder `missing_or_escaping_path` is raised inside `_real_source`.
- Add a regression test: the inspection branch hits ENOSPC during the CSV write, and the result is that comparison.* is removed and
  error.json is written.
- Docs: one clause in usage.md:137 / usage_reference.md:229/:355 / F1_report item 1. For example: "a write failure on the
  non-transactional branches (no condition_assignment, or numeric_v2 data) still removes comparison.*, as before".

(Recording this for the record: the original R_findings fix (a), "every exception … when mode == real", is too broad for the same
reason. That was a defect in my previous recommendation.)

## B1: fixed (verified)
- My earlier repros (`/tmp/fr_repro/test_fr_repro.py`) on the current tree:
  - Tampered reference manifest: exit 2 `real_manifest_hash_mismatch`. comparison.json/.csv/masks.zip are kept, error.json is written.
  - Moved `data/imported/target`: exit 2 `missing_or_escaping_path`, files kept.
  - `invalid_compare_options`: unchanged. Nothing is deleted, and no error.json is written in the compare folder.
- New tests `test_a_tampered_reference_manifest_…`, `test_a_moved_import_folder_…`, `test_synthetic_compare_errors_still_use_the_old_removal_rule`
  all FAIL on the tree with the flag removed and PASS on the candidate. I ran them with `-c /dev/null`, because pyproject's
  `pythonpath=["src"]` otherwise shadows `PYTHONPATH`.
- Synthetic compare: the flag stays off, so the old removal rule applies. An unreadable config is unchanged
  (`test_numeric_cli.py::test_r2_error_cleanup_and_success_clears_marker`). The synthetic check is a unit-level assertion only.
  The CLI removal code path for an unflagged exception is unchanged and is exercised by other tests.

## A2: done
`publication.py:51-69` uses a private `WinDLL('kernel32', use_last_error=True)`, `get_last_error()`, and explicit restype/argtypes
(HANDLE / DWORD / BOOL / POINTER(DWORD)). Every failure or doubt means alive. The mocked-kernel32 test covers: 87 → gone, 5 → alive,
STILL_ACTIVE → alive, exit code 0 → gone, GetExitCodeProcess failure → alive, WinDLL failure → alive. It passes on py3.12 venv and on py3.8.
Residual (accepted): a process that exited with code 259 reads as alive, which is only a leak. The branch is still not run on real
Windows here.

## A1, A3, A4: done
Docs match the code and the findings: the A1 note in usage_reference, the 3.3~3.8 GiB peak, `--out` new-path rule limited to stages
1/3/4/5, and WORK_DIR added.

## Other checks
- Bundles: rebuilt both builders into /tmp/r2/b. All 7 files are byte-identical to bundles/.
- tests/test_compare_publication_cli.py: 14 passed (venv) and 14 passed (/tmp/py38).
- Full suite (`.venv`, `-x --ignore=tests/test_handoff.py`): green, exit 0.
- The F3 delta touches only pipeline.py (wrapper), publication.py (pid_alive), the test file, and docs. No F1 item regresses, apart
  from B2 above. compare-windows still never removes anything, and the in-flight guard and the masks-transaction tests pass.
