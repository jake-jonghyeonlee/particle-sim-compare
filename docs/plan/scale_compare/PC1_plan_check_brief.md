# PC1 — Cross-model plan check (read-only). Reviewer: Codex gpt-6-astra / xhigh.
READ-ONLY: do not edit/create/delete files; do not commit. Report findings only, then worker_done.
Plan: docs/plan/scale_compare/plan_v1.md (incl. §6 coordinator notes). Brief it answered: docs/plan/scale_compare/P1_planning_brief.md.
Base commit 01989de (branch feat/template-export).
Check, with file:line evidence, narrowly following the plan's pointers:
1. §2.1 partition refinement: exactly equivalent to today's shares/_membership_counts (bucket set, order, counts, lineage, contributors), incl.
   masks outside valid, duplicate pairs, zero-area sources, pixel keep = valid & ~line. Counterexamples if not.
2. §2.2/§2.3: any consumer that breaks with packed-mask properties or lazy records (local_delta, electrode_map, candidates, roi_export,
   dl_results, electrode_png, stage-6 both sides), mutation of returned arrays, identity/caching assumptions, error-code/order changes.
3. §2.4 estimator: does it still fail fast with clear messages on truly infeasible inputs; are the formulas right for ETX scale
   (1,682 cases x 32 diameters, 1000–1500 px crops, one layer worst case, two models in stage 6)?
4. §2.5 v2 JSON: any production reader missed; is the change lossless.
5. ETX: Python 3.8 / numpy 1.16 compatibility gaps (e.g. bitorder elsewhere), Linux memory/FS issues, runner rebuild, whether stage-1 outputs truly stay valid.
6. Task split/acceptance: missing acceptance counterexamples, unrealistic targets, ordering/rollback risks.
Verdict: ACCEPT / ACCEPT-WITH-CHANGES / REJECT; blocking vs advisory findings.
