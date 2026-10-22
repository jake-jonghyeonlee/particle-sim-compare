# Scale stages 4–6 (Phase B) — checkpoint (2026-10-23)

Orca Run run_c9825fe14b68. Risk tier HIGH for every Task. Base HEAD 7bcb07a; everything below is **uncommitted** (no commit/push yet).
Plan: `plan_v3_1.md` (Fable draft `plan_v3.md` + Astra xhigh plan check `PC2_findings.md` ACCEPT-WITH-CHANGES, blockers B1–B6 resolved).

| Task | Worker | Result | Report |
|---|---|---|---|
| T1 oracle (frozen 7bcb07a sources, generators, equivalence suites) | Sonnet medium | done | `T1_spec.md` |
| T2 stage 4 streaming + stage-5 gate, slim `review_rois` (`observed_ref` + `region_coverages`, still `review_roi_v1`, DL2 read-only checked) | Sonnet high | done | `T2_report.md`, `T2_benchmark.md` |
| T3 stage 6 compute, bounded caches, lazy assets, `compare_masks` artefact (Q3) | Sonnet high | done | `T3_report.md` |
| T4 stage 6 streamed writers (A3) + `SetPublication` transaction (B2), cli 2 lines | Sonnet high | done | `T4_report.md` |
| T5 compare CLI keeps previous generation, real-scale 1–6 benchmark, py3.8 pipeline, runners/docs | Sonnet medium | done | `T5_benchmark.md`, `T5_bench/` |

## T5 summary
- `pipeline.run_numeric_compare` sets `exc.keeps_previous_outputs` from `stream.begin()` on; `cli.py` handler already honours it (T4). `tests/test_compare_publication_cli.py` (8).
- `scripts/benchmark_scale_compare.py --stages 1-6` (profiles centered / components / scattered, distinct / shared electrodes, asymmetric target, P list, stage-5 and labelled fixtures,
  guard bracket, per-process RSS/IO counters, assertions on counts/rows/branches). Runs A (1262² centered distinct), B (1262×1134 target, components, shared), C (1500² centered distinct), N=1,682/model.
- Peak RSS: import 0.85 GiB, validate-data 1.1, compare 3.4–3.5 (1.7 shared), import-electrode-assets 0.57, candidates 1.2–1.3, confirm-pairs 0.9–1.25, compare-windows 0.8–1.25 GiB with the
  artefact / 3.3–3.4 GiB (1.5 shared) without. Walls (inflated ~1.3× by three concurrent chains): compare 10–17 min, candidates 5–6.5, confirm-pairs 3.5–4.6, compare-windows P=200 6–9.5 min
  with artefact, 14–23 without. Disk peak 3.5–4.7 GB per chain.
- py3.8.20 (numpy 1.21.6): stage 3–6 test files + equivalence green; N=300 1262² stage 1–6 pipeline with the rebuilt runners green (50 pairs, 4,800 rows).
- Found: ETX stage-1 JOBS have no `--expected-cases`, so stage-4 ROIs are all `blocked` (`map_coverage_incomplete`) unless the operator adds it; documented in `docs/usage.md` and runner notes. Not changed in code.
- Found: `confirm-pairs` collapses mirrored accepted candidates (50 → 25 pairs); its output has no endpoint bindings, so the named-pair branch needs a labelled correspondence.
- Runners (`bundles/simcompare_job.py`, `bundles/etx/*`) rebuilt from the final sources; docs: `docs/usage.md` ETX section, `configs/etx/*`.

## Validation
Full pytest (3.11, minus `test_handoff`) green; `test_etx_examples` stale-bundle test green after the rebuild. Pre-existing flakes noted in T4 (`test_stage5_accepts_slim_and_old_form_exports_alike`
wall-clock `labelled_at`) not touched.

## Next
Astra xhigh code review of the whole Phase B diff (user-specified reviewer) → fixes → re-review until PASS → commit/push (user pre-authorized push after PASS).
Open for the user: real ETX run feedback (RAM, timing, whether the 8 GiB default is enough for P=200 recompute), `--expected-cases` for the real inventory.
- 2026-10-23: Codex Astra review launch failed twice (401 workspace routing discovery unauthorized); user chose Opus 5.5 high for RB.

## Review and acceptance (2026-10-23)
- RB: Claude Opus 5.5 high (Codex Astra unavailable: 401 auth failure twice; user chose Opus) — PASS-WITH-ADVISORIES, no blockers, on
  PhaseB_candidate.diff (sha256 8ba5dc42eef20624…). Findings: RB_findings.md.
- Coordinator doc fixes after review: A1 (usage.md unpack cache 32 entries), A2 (T5_benchmark guard-bracket labelling), A9 (usage.md:
  move/rename the old --out folder before re-running stage 4/5).
- Open follow-ups (advisories, not fixed): A3 pre-begin CLI errors still delete the previous generation (+ orphan comparison_masks.zip),
  A4 dropped-masks mixed generation, A5 artefact read before stage-6 admission, A7/A8 minor admission under-pricing, A10 stale temp zip,
  A12 shared coverage lists. ETX caveat: stage-1 runner has no --expected-cases, so stage-4 ROIs are blocked (map_coverage_incomplete).
