# Plan request — simcompare rewrite and DL repositories

Requested by the user on 2026-10-07: "구현 전에 계획부터 진행해야지. orca task로 진행해."
Planner: Codex gpt-6-astra, effort high, read-only advisory. Coordinator and plan reviewer: Claude Opus 5.5.

## Objective

Produce a versioned implementation plan (`docs/plan/PLAN_v1.md`) for the work below. Do not implement anything.

1. `particle-sim-compare` (this repo, branch `rewrite/line-pixel-rate`): numeric comparison rebuilt around the
   team's real yield practice.
   - per-layer line / pixel defect values with electrode-pair shares, and target-minus-reference change
   - overlap union handling
   - electrode-count map for vulnerable locations (representative diameter 5 um)
   - input reading (hotspot masks per defect case and diameter, electrode-pair to line/pixel table,
     fab defect density, panel size and unit-pixel size), a report, and a CLI
   - retirement of old modules that no longer match the goal
2. `particle-sim-dl1` (`~/git/particle-sim-dl1`, local, not pushed): DL1 product similarity across unit sizes.
3. `particle-sim-dl2` (to be created as a separate repo): DL2 local-structure retrieval from one review ROI.
4. A hand-off package so the user can run training and validation in the closed network.

## Evidence to read first

- `docs/implementation_memory.json` — authoritative record of user decisions. Read every section dated
  2026-10-06 and 2026-10-07: `overlap_union_metric_2026_10_06`, `sim_output_definitions_2026_10_07`,
  `electrode_count_map_2026_10_07`, `current_yield_practice_2026_10_07`,
  `line_pixel_rate_extension_2026_10_07`, `repo_split_and_dl1_input_2026_10_07`. Earlier sections give the
  DL1 / DL2 design history.
- Code written today WITHOUT a plan (treat as a candidate, not as accepted):
  `src/simcompare/linepixel.py`, `tests/test_linepixel.py`, `src/simcompare/electrode_map.py`,
  `tests/test_electrode_map.py` (commits 8f6eef9, d7eb11e), and the whole `~/git/particle-sim-dl1` repo
  (commit 44a13a6).
- Branch `archive/pre-rewrite-20261007` (commit 5bde9f3): the previous working tree. Candidates to carry
  over: hotspot image reading (0/255 to 0/1 fix), input validation (path escape, duplicate diameters, unit
  mixing), window area measurement, fab measurement reading, missing-versus-zero reasons, synthetic data
  generator. Candidates to drop: autoencoder model / training / retrieval, avoidance variants, old cards
  and plots, the CAA placeholder.
- `docs/data_contract.md`, `README.md` on the current branch for the existing input formats.

## Decisions already made (do not reopen)

- Hotspot = particle-centre locus; CA in um^2; combination hotspots share scale and coordinates.
- Vertical short: same 2-D method as lateral, belongs to the upper layer of the pair, not simulated below
  the height; no height is 5 um or more.
- Yield practice: shorts only (open excluded), pair table (electrode, electrode, line|pixel) independent of
  layer and of lateral/vertical, per-layer per-diameter union, line/pixel overlap counted as line, same fab
  density for line and pixel, line and pixel kept as two values, panel-level comparison, unit count derived
  from panel size and unit-pixel size (floor or round), simulation on a 2x2 expansion.
- DL models live in separate repos; everything else stays here.
- DL input is the original shape (no hotspot, no CA). DL1 goal: similar shape and electrode connectivity
  even when unit size differs.
- Closed network: general PyPI packages available; no in-package network downloads; pretrained weights can
  be loaded from a file; real layouts and correspondence labels exist only there, and the user runs the
  validation.

## On hold (plan must not schedule these)

- Unit-boundary handling in the 2x2 result; conversion of the summed values to yield.
- Per-layout-change attribution (changed-zone split, onset-diameter shift, single-change revert).
- Open defects; wiring reinforcement; slide decks.

## Unknowns that need the user (plan must mark them as explicit gates, not guess)

- Exact format and unit of the fab defect density data (per layer, per diameter).
- How hotspot files arrive: one mask per defect case and diameter with electrode names? whether the image
  is the 2x2 expansion; how to obtain one unit's area from it.
- Whether DL1 and DL2 are two repos (assumed) and their names.
- DL2 window sizes (near / wide, in um); whether the intermediate layer is included for non-adjacent
  vertical pairs.

## What the plan must contain

- A version line and a short statement of scope and non-goals.
- Tasks, each with: Target (repo, branch, files), Change (observable result), Constraints (compatibility,
  do-not-touch), Ownership (allowed writes), Acceptance (commands, tests, evidence — meaningful, not
  "tests pass"), Baseline (expected branch / commit, known existing changes).
- Real dependencies between Tasks only. At most one editing worker per repository at a time.
- For the code already written without a plan: an explicit verdict per file — accept as is, accept with
  listed changes, or redo — with reasons from reading the code against the decisions above.
- Which Tasks require an independent Claude Opus 5.5 high review (core calculations, schemas, public
  inputs) and what the reviewer must be given.
- For every Task blocked by a user gate: what can be done before the answer and what cannot.
- Acceptance-test counterexamples: for each numeric Task, at least one input that a wrong implementation
  would pass unless the test is written carefully.
- Interruption and retry notes: how a half-finished Task is resumed without losing work.
- Risks and open questions, ranked.

## Constraints on the planner

- Read-only. The only file you may create is `docs/plan/PLAN_v1.md` in this repo.
- Do not run training, do not create repositories, do not push.
- Do not invent facts about the real data. Where the record is silent, say so and make it a gate.
- Write the plan in Korean (identifiers and commands as they are).
