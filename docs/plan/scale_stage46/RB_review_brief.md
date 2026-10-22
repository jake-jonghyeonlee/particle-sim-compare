# RB — Phase B independent code review (read-only). Reviewer: Codex gpt-6-astra / xhigh. Risk tier HIGH.
READ-ONLY: do not edit/create/delete files; do not commit. Report findings only, then worker_done.
Candidate: working tree on feat/template-export vs HEAD 7bcb07a; diff excluding generated bundles, the frozen oracle copy
(tests/reference_stage46/simcompare_7bcb07a — verify it with tests/reference_stage46/DIGESTS.sha256 against `git show 7bcb07a:src/simcompare/...`)
and *.diff notes: docs/plan/scale_stage46/PhaseB_candidate.diff (sha256 prefix 8ba5dc42eef20624). Bundles rebuilt — verify embedded src.
Contracts: docs/plan/scale_stage46/plan_v3_1.md (authoritative), plan_v3.md (+§7 user decisions), T1–T5 specs, your PC2_findings.md.
Reports/evidence: T2_report.md, T3_report.md, T4_report.md, T5_benchmark.md, README.md (checkpoint). Full pytest green except tests/test_handoff.py.
Accepted out-of-scope edits: cli.py keeps_previous_outputs (compare + compare-windows), compare_masks.py fixed zip timestamps.
Known/decided: review_rois slim format kept as review_roi_v1 (DL2 validator accepted it); post-authentication layout damage now fails only that
case (T3 decision — judge whether it violates plan_v3_1 B5 "preserve failure order exactly"); stage-6 guard conservative (7.84 GB est vs 1.6 GB).
Focus: (1) byte-identical outputs vs frozen 7bcb07a for stages 4–6 (and stage 2 unchanged apart from the extra comparison_masks.zip), oracle
strength/independence; (2) stage-2 artefact reuse == recompute, staleness detection, transaction membership; (3) both publication transactions
(Phase A compare incl. new zip, Phase B set publication) incl. CLI handler interplay and restart recovery; (4) admission guards fire before
allocation and price real peaks (stage 4/5/6); (5) ETX: Python 3.8/numpy>=1.16, runners, configs/docs, stage-1/2 compatibility with outputs users
already produced on ETX. Verdict PASS / PASS-WITH-ADVISORIES / FAIL; blocking vs advisory with file:line and a concrete failure scenario.
