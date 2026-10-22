# Expected-case inventory from the union of model roots — checkpoint (2026-10-23)
Orca Run run_c9825fe14b68. Risk tier HIGH. Base d8f6dbb.
- EC-T1 (Sonnet 5.5 medium, task_0e520c10377d): --expected-cases-from-roots, manifest expected_case_source, stage-1 runner JOBS, rebuilt runners.
- Coordinator: rebuilt bundles/simcompare_job.py; after review fixed restart notes (A1) in configs/etx/README.md, docs/usage.md,
  scripts/build_etx_stage_runners.py and rebuilt the six ETX runners (docs/comment text only).
- EC-R: Claude Opus 5.5 high (Codex unavailable, 401) — PASS-WITH-ADVISORIES on EC_candidate.diff (sha256 a629fefbe35a1470…). R_findings.md.
- Open: A2 reject empty/self/duplicate extra roots (degenerate union gives 'confirmed'); A3 stage 4 unblocks per layer only when both models
  have the same case set; A4 load_real_manifest does not cross-check status vs source (hash-protected only).
