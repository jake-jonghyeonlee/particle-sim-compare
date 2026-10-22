# Scale compare — checkpoint (2026-10-22)

Orca Run run_c9825fe14b68. Risk tier HIGH. Base 01989de. Phase A accepted; Phase B (stages 4/6 streaming) not started.
- Plan: P1 Fable 5.1 medium draft (plan_v1.md) -> Astra xhigh plan check PC1 ACCEPT-WITH-CHANGES -> plan_v2.md (+v2.1 user decision: summary output default).
- Implementation (Sonnet 5.5): T1 linepixel single pass, T2 comparison v2 writer, T3 lazy records/estimator/stage-4/6 guards, T4 summary mode,
  T5 benchmark/py3.8/runners, T6 label budget, T7 (high; attempt 1 stalled idle and was stopped, retry ctx_b9ac3c69aa49) RA fixes,
  T8 (high) RA2 fixes, T9 (high) RA3 fixes.
- Reviews (Codex gpt-6-astra xhigh, user-specified): RA FAIL -> RA2 FAIL -> RA3 FAIL -> RA4 PASS on PhaseA_candidate_v4.diff
  (sha256 9818bec27224d11d…). Findings: RA2_findings.md, RA3_findings.md, RA4_findings.md. Opus 5.5 high review not run (user chose Astra).
- Evidence: T5_benchmark.md — centered N=1682/model, S=1262, one layer, DEFAULT limits: import ~4 min/0.84 GiB per model,
  validate-data 69 s/1.1 GiB, compare 587 s/3.9 GiB (Mac M-series, Python 3.11). Py3.8.20 + numpy 1.21.6 targeted tests green.
- User restart point on ETX: stage 2 with all 6 rebuilt runners; stage-1 imports stay valid.
- Open: Phase B (stage 4 electrode-map/candidates, stage 6 compare-windows/report streaming); real ETX run feedback (numpy/Python version, RAM, timing).
