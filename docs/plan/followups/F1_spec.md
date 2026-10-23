# F1 — fix remaining review advisories. Risk tier HIGH (recovery/publication, admission, manifest validation). Worker: Sonnet 5.5 high.
All validation in the FOREGROUND. Base: HEAD c7a2829 (feat/template-export), clean.
Sources (read the cited items only): docs/plan/scale_stage46/RB_findings.md (A3, A4, A5, A7, A8, A10, A12) and
docs/plan/expected_cases/R_findings.md (A2, A4 — the worker_done text is summarised in docs/plan/expected_cases/README.md).
Ownership: src/simcompare/cli.py, pipeline.py, publication.py, adapters/real_hotspot.py, candidates.py, confirm_pairs.py, electrode_map.py,
compare_masks.py (only as needed per item), tests (new + affected), bundles/ (rebuild at end via scripts/build_single_file.py --out
bundles/simcompare_job.py --overwrite and scripts/build_etx_stage_runners.py --overwrite). Do NOT edit docs/usage.md (F2 rewrites it);
put user-facing behaviour notes in docs/plan/followups/F1_report.md for F2 to fold in.
Fix (each with a regression test that fails before the fix):
- RB-A3: errors before a publication transaction opens must not delete the previous good generation of compare / compare-windows outputs
  (e.g. treat all compare and compare-windows errors as keeps_previous_outputs, or open the transaction before admission); also no orphan
  comparison_masks.zip next to deleted json/csv; and the 'killed during step 3 then pre-begin failure' wedge must not leave
  local_delta_recovery_unreadable.
- RB-A4: when the masks writer fails with a non-I/O error, do not leave the previous comparison_masks.zip beside the new json/csv
  (include it in the journal and remove it, so the published set is one generation).
- RB-A5: read comparison_masks.zip only after/within stage-6 admission (price it in the estimate; do not allocate before admission).
- RB-A7: stage-4 second-product admission must count the first product's still-alive configured dataset (or release it first).
- RB-A8: stage-5 gate must price the DL2 match JSON reads too.
- RB-A10: begin() of each transaction removes stale temp files it owns (e.g. .comparison_masks.zip.<pid>.<hex>.tmp, json/csv temps) from dead
  previous runs, without touching other processes' live temps (use pid liveness or age + ownership pattern).
- RB-A12: ElectrodeRegion.coverage must not alias the LayerMap coverage lists in a way a caller mutation leaks (share immutably — tuples or
  read-only views — with identical serialized output).
- EC-A2: --expected-cases-from-roots rejects an empty list, a root equal to --root (after resolve) and duplicates, with clear errors.
- EC-A4: load_real_manifest cross-checks expected_case_coverage_status with expected_case_source/expected_cases (confirmed ⇔ a source and a
  list are present; legacy manifests without expected_case_source keep loading as before).
Constraints: numeric outputs and published file bytes unchanged (stage-2/4/6 equivalence suites and frozen oracles green); Python 3.8;
numpy>=1.16. Acceptance: full pytest green except tests/test_handoff.py; /tmp/py38 (PYTHONPATH=src) run of every test file you touched;
bundles rebuilt and stale-bundle test green. No commit. worker_done: per item what changed + test name; F1_report.md written.
