# P2 — Phase B planning brief (read-only). Planner: Claude Fable 5.1 / medium. Risk tier: HIGH.
READ-ONLY: do not edit code/tests. Write exactly one file: docs/plan/scale_stage46/plan_v3.md. Then worker_done.
Base: HEAD 7bcb07a (feat/template-export). Phase A (stage 2 at real scale) is done: read docs/plan/scale_compare/README.md, plan_v2.md
(sections B3/B4 = the Phase B brief), T5_benchmark.md, RA2/RA3/RA4_findings.md for how guards/estimators now work.

## Goal
Stages 3–6 of the ETX runners (bundles/etx/simcompare_job_[3-6]_*.py; JOBS blocks show the commands: import-electrode-assets,
candidates, confirm-pairs, compare-windows (+report)) must run at real scale with IDENTICAL outputs to today, within ~16 GB RAM and
reasonable disk/time, or fail fast with clear messages. Real scale per model: ~1,682 cases, 32 diameters, crops 1000–1500 px
(1262 typical), electrode PNGs crop+1000 px, two models; layer split unknown (one-layer worst case). Python 3.8 / numpy>=1.16 (tested 1.21.6).

## Known eager paths (from Astra PC1 and Phase A guards — verify, don't trust)
- stage 4: electrode_map.py ~359-408 representative record per case, one bool mask per distinct electrode, np.stack, regions with full-frame
  masks and deep-copied metadata; candidates.py ~451 retains first side's maps; header-only validation missing (walks 32 records per case).
- stage 6: local_delta_report.py ~161-169,284 eager layout masks for both models; local_delta.py ~282-284,382-401,478-485 repeated full-mask
  access for windows/core/jitter (millions of full unpacks at scale); named-case reload per window size (:462); writer builds nested payload +
  full indented JSON string (:700,:728-734); both AreaResults retained (:421-422).
- stage 3: adapters/electrode_png.py (~262 configured_dataset; electrode PNG decode crop+1000 px) — check its footprint.
- stage 5: confirm_pairs.py — check whether it loads masks at all.
- Phase A added admission guards (real_hotspot.py guard_stage_map*, stage-6 guards) — they must stay correct after streaming changes.

## Plan must contain
1. Per stage 3–6: measured-or-derived time/RAM/disk at real scale today, root causes with file:line, and whether a fix is needed.
2. Designs preserving byte-identical outputs (JSON/CSV/PNG/HTML) — oracle strategy like Phase A (frozen copy of 7bcb07a code, randomized
   equivalence), including stage-6 window/jitter numerics and stage-4 region/stable-ID outputs.
3. ≤ 5 serial Tasks with Target, Change, Constraints, Ownership, Acceptance, risk tier; writers publish-on-success like Phase A.
4. A real-scale benchmark extending scripts/benchmark_scale_compare.py through stages 3–6 (synthetic electrodes with distinct pairs,
   many components, correspondences for stage 6, differing frame sizes between models), with targets.
5. Whether stage-1/2 outputs stay valid (user is running stage 2 on ETX now), config/runner changes users need.
6. Risks, open questions for the user (e.g. which stage-6 outputs/windows are actually needed), out of scope.
Keep it ≤ ~250 lines; follow pointers, don't read the whole repository.
