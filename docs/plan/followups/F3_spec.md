# F3 — fix F-R review B1 (+ small advisories). Risk tier HIGH (publication/CLI). Worker: Sonnet 5.5 medium. FOREGROUND validation.
Base: HEAD c7a2829 + uncommitted F1 + F2 (keep all). Findings: docs/plan/followups/R_findings.md (read B1, A1-A4).
Ownership: src/simcompare/pipeline.py (run_numeric_compare entry), src/simcompare/cli.py only if needed, src/simcompare/publication.py (A2),
tests/test_compare_publication_cli.py (+ new regressions), docs/usage.md, docs/usage_reference.md, docs/plan/followups/F1_report.md,
bundles/ (rebuild at end: scripts/build_single_file.py --out bundles/simcompare_job.py --overwrite; scripts/build_etx_stage_runners.py --overwrite).
- B1 (option a): when cfg mode is 'real', EVERY exception from run_numeric_compare (incl. _real_source/load_real_manifest validation errors,
  mixed_real_and_numeric_source_formats, missing/moved import folder → missing_or_escaping_path) is marked keeps_previous_outputs, so
  comparison.json/csv/comparison_masks.zip of the previous run are kept and only error.json/execution.json are written. Regression tests
  (fail before): tampered reference manifest; moved data/imported/target. Synthetic compare and an unreadable config keep today's behaviour.
  Make usage.md:137 and usage_reference.md:229/296/355 + F1_report.md state exactly that.
- A2: Windows pid-liveness branch in publication.py uses ctypes.WinDLL('kernel32', use_last_error=True), ctypes.get_last_error(), explicit
  OpenProcess.restype/argtypes (HANDLE); keep "unknown ⇒ alive" (never delete on doubt). Unit-test the branch with a mocked kernel32.
- A3: usage.md:60 compare peak → "3.3~3.8 GiB". A4: usage.md:34 restrict "--out must be a new path" to stages 1/3/4/5 (stage 2 output_dir and
  stage 6 data/local are replaced as one generation); mention WORK_DIR next to BASE_DIR/JOBS/LOG_FILE.
- A1: document in usage_reference.md (admission charges artefact reuse when an artefact exists; tiny frames only).
Acceptance: full pytest green except tests/test_handoff.py; /tmp/py38 run of touched test files; stale-bundle test green. No commit. worker_done.
