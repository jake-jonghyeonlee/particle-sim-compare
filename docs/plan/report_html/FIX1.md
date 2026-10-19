FIX1 — fix Astra review findings on the integrated HTML report (risk tier: HIGH — finding 1 is a destructive data path; review after fix: fresh Opus 5.5 high)

Target: repo /Users/jonghyeonlee/git/particle-sim-compare, branch rewrite/line-pixel-rate. Candidate under fix: commit 801c8da (report feature). Read the full review first: docs/plan/report_html/astra_review_801c8da.md (line numbers refer to 801c8da). Contract: docs/plan/report_html_plan_v1.md §2–§7. Before starting, run `git status --short` and `git log --oneline -3`: other sessions' files (PLAN_v4, adapters/electrode_png.py, etc.) may be present — do not touch them; if cli.py or docs/usage.md has uncommitted changes from someone else, stop and ask the coordinator.

Files you own: src/simcompare/cli.py (only _remove_reports / report error path), src/simcompare/final_report.py, src/simcompare/local_delta_report.py (only _overview_png and the overview selection note), tests/test_final_report.py, tests/test_local_delta_report.py, tests for cli cleanup (add to an existing cli test file or tests/test_final_report.py).

Change (each with a regression test that fails on 801c8da):
1. P1 cleanup: remove the generic 'report' prefix from _remove_reports. The report command removes only its own exact files (report.html, report.json; error.json/execution.json as other commands do). Test: unrelated report_notes.json / report_backup.csv survive an error in `compare` and in `report`; a stale owned report.html is removed on report exit 2.
2. P1 escaping: every value interpolated into HTML/SVG goes through escape or is validated as a finite number (counts: non-negative int; geometry: finite float). Test hostile provenance n_ready/n_blocked and a hostile string in another metadata field → no raw <script in report.html (exit 2 on invalid type is acceptable; say which).
3. P1 contexts: A1 and B2 must key by the full comparison context (as B1 does via comparison_key), never overwrite. Show one KPI/group per context with a readable label (layer + process_step/exposure). Test two contexts on the same layer with different values and unequal side coverage; a, b, Δ all match the source.
4. P2 all-blocked: a valid local_delta result with no rows (all pairs blocked) produces a report with exit 3, listing blocked pairs and reasons in A2/B7; diameter_not_available stays exit 2 only when rows exist but lack the diameter. End-to-end test via compare-windows with a blocked pair.
5. P2 missing crops: any required crop that is null/missing makes B3 partial with reasons (and exit 3); distinguish null-with-note from a referenced file that does not exist. Test mixed pairs (one with endpoints, one without).
6. P2 overview: electrode colours are masked by frame.valid before block reduction; invalid gray applied last; document the mixed-block convention in a comment. Tests: mask outside valid, wholly invalid block, mixed block.
7. Scaling: each B3 card's small locator draws only its own window (highlighted) on the overview; the full all-pairs overlay appears once per frame in the "전체 frame 위치도". Test: 100 non-overlapping synthetic pairs → report.html size grows ~linearly (assert < 1.5 MB without PNGs, or rect count ≤ c·N).
8. Overview limitation disclosure: show overview_source_pair_id and a sentence that the background shows only that case's two electrodes (window positions are exact). Do not attempt a full-layout composite.

Constraints: no change to numeric outputs or local_delta.html (keep T1 byte-identity); no new dependencies; keep style. Do not commit.

Acceptance: full `uv run pytest -q` passes; each of the 8 items has a named test; demo flow (/tmp only, not scripts/2_demo.sh into the repo) report exit 0. worker_done lists tests added per item and the commands run.
