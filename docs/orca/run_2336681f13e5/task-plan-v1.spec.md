PLANNING TASK (read-only advisory) — particle-sim-compare ROI CA comparison + location-centered integrated report

You are the planner (Codex gpt-6-astra, effort high). You advise the Opus coordinator; you do NOT gain coordination authority, do not launch nested workers/subagents, and do not implement.

TARGET
- Repository /Users/jonghyeonlee/git/particle-sim-compare, branch record-cno-open-fab-decisions, HEAD b507da96554baa658171dcae61a99342c9ec36e3.
- Read FIRST and in full: docs/orca/run_2336681f13e5/request.txt (the user-authorized feature contract: confirmed domain decisions, calculation/comparison invariants, maintainability requirement, acceptance counterexamples). It is binding.
- Also read docs/orca/run_2336681f13e5/checkpoint.md (baseline) and docs/open_questions.md (includes the 2026-10-01 decisions).
- Sibling /Users/jonghyeonlee/git/particle-sim may be READ for CA / hotspot / export producer semantics. Never edit it.

CHANGE (the single deliverable)
Write one versioned plan file: docs/orca/run_2336681f13e5/plan-v1.md (Korean prose, English identifiers). It must contain:
1. Evidence: what the code actually does today, with file:line citations you verified yourself. At minimum: schema.py (ImageInput/origin_xy, AnalysisBundle, CA unit contract), adapters/particle_sim_run.py (how hotspot planes and CA rows are built, nominal vs mean, origin handling), neighborhood.py, variants.py _roi, retrieval.py candidate_mask, pair_geometry.py, pipeline.py run_analyze, plots.py, cards.py, config.py, and the particle-sim producer (what arrays/tables the exporter emits; whether any per-pixel/per-location quantitative CA-generating area exists whose full-domain sum equals the simulator CA for a given source mode, or whether exported hotspot arrays are only footprint/display masks).
2. A truthful decision on the quantitative ROI CA source: which typed source(s) can be measured now, the full-domain conservation check that gates them, and the explicit unavailable-with-reason path otherwise. Do not invent rescaling, do not split a global CA among pixels, do not treat footprint union area as CA. State any upstream data dependency plainly.
3. Design: small cohesive modules with names fitting this project (e.g. regional contracts/measurement, ROI selection + common windows, comparison, report rendering), thin orchestration in pipeline.py, pure calculations separated from IO/adapters/plotting, configuration keys (with defaults and meaning) instead of magic constants, correspondence-mapping input interface (file format, unknown != absent/zero), registration/common-frame contract (origin, pixel size, anisotropy support or explicit rejection), deterministic region IDs, output file layout (standalone HTML + JSON/CSV + local static figures, no network/external assets) following existing pipeline conventions, and documented extension boundaries for mapping, Fab weighting and a future Tree (NOT implemented, no placeholder model).
4. Tasks: an ordered list of implementation Tasks for ONE editing worker at a time (Codex gpt-6.1-sol medium). For each Task give the full contract: Target (files/component), Change (observable result), Constraints (compat rules, do-not-touch), Ownership (allowed writes), Acceptance (concrete tests/commands/evidence), Baseline. State real dependencies only. Prefer few well-bounded Tasks over many tiny ones; say which could be merged.
5. Acceptance counterexamples mapped to concrete named tests (every counterexample in the request's "Meaningful acceptance counterexamples" and "Calculation/comparison invariants" sections must map to at least one test, or be explicitly marked not-applicable with reason).
6. Risks, open questions for the coordinator, and interruption/retry recovery notes (what a retried worker must check before continuing).

CONSTRAINTS
- Read-only for all source, tests, configs and existing docs in both repositories. The ONLY file you may create or modify is docs/orca/run_2336681f13e5/plan-v1.md.
- No git mutations (no add/commit/stash/reset/checkout/branch), no dependency installs, no network.
- Three files have pre-existing uncommitted changes that must be preserved untouched: docs/open_questions.md, src/simcompare/adapters/particle_sim_run.py, tests/test_particle_sim_export_integration.py (baseline `git diff` sha256 a00a5706a34852ba3bd4c72397d87268b510ed5cce666a586aeeeb1dfc8a28b3). The plan must tell implementers how to build on them without reverting or claiming them.
- Honour the request's exclusions: CNO stays excluded; open metal reinforcement unsupported; no Fab weighted formula / CAA / yield conversion; no Tree/SHAP implementation; no automatic layout edits; existing AE/search pipeline and legacy edit-candidate behaviour preserved; no accuracy/yield claims.
- You may run read-only commands, including the test suite: .venv/bin/python -m pytest (baseline 209 passed, 56 skipped).
- If a decision genuinely needs the coordinator, use the ask command from your preamble; otherwise record the question in the plan and choose the conservative option.

OWNERSHIP
- Writes: docs/orca/run_2336681f13e5/plan-v1.md only. Coordinator (Opus) reviews and adopts or returns the plan.

OBSERVABLE ACCEPTANCE
- plan-v1.md exists with sections 1–6 above, file:line evidence, and a clear statement on quantitative ROI CA source availability.
- `git status --short` shows no changes other than the three pre-existing files and the untracked docs/orca/ directory; `git diff | shasum -a 256` still equals a00a5706…a8b3.
- worker_done sent once with outcome and --report-path docs/orca/run_2336681f13e5/plan-v1.md.
