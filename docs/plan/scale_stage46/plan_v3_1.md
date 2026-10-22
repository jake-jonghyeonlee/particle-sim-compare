# Phase B plan v3.1 — supersedes plan_v3.md where they differ (2026-10-22)

Coordinator: Opus 5.5. Design reference: plan_v3.md (Fable draft) + §7 user decisions. Resolves Astra xhigh plan check PC2
(docs/plan/scale_stage46/PC2_findings.md: ACCEPT-WITH-CHANGES, blockers B1–B6, advisories A1–A5). Base 7bcb07a. Risk tier HIGH for all Tasks.
Reviewer (user-specified): Codex gpt-6-astra / xhigh. Workers: Sonnet 5.5 (high for T2/T3/T4 — cross-module memory/recovery design,
as Phase A showed). All validation in the FOREGROUND (no background waits — a Phase A worker stalled that way).

## Resolutions

B1 stage-4/5 export memory. review_rois.json rows duplicate the full `coverage.observed` identity list (O(R·N) rows × N).
- DL2 (sibling repo particle-sim-dl2, read-only check: src/psdl2/data.py:188-190,243-245) requires `schema_version == review_roi_v1` and reads
  only `row.provenance.coverage.source_coverage_status`. Design (user Q2 allowed a content-preserving change): rows keep every scalar coverage
  field (status, counts, reasons) and replace the identity list with `observed_ref: <region coverage id>`; the full list is written ONCE in a
  top-level `region_coverages` map. Before adopting, T2 must run the DL2 validator read-only against a slim export (import psdl2 from the sibling
  checkout via PYTHONPATH, no edits there). If DL2 accepts it under `review_roi_v1`, keep the version; otherwise bump to `review_roi_v2`, keep
  v1 writing behind a flag for DL2, and record the DL2 follow-up for the user. Either way readers in this repo (candidates reparse,
  confirm_pairs) accept both shapes.
- candidates.py must not re-read/reparse its own exports (keep the rows it wrote) and must hash files by streaming (chunks).
- electrode_map.py:502 per-region deepcopy of layer coverage → share one read-only copy.
- Stage 5 (confirm_pairs) parses the slim files; add a pre-read size/structure gate (file bytes vs max_operation_bytes with a measured
  parse multiplier) that refuses before json.loads with a clear error.

B2 stage-6 publication: full-set transaction owned by one coordinated writer in pipeline.py (T4 owns pipeline.py stage-6 orchestration):
explicit allowlist of produced files (local_delta.json/.csv, window_sensitivity.*, local_delta.html, _thumbs.json, thumb/overview PNGs),
old generation preserved as backups, durable commit marker, roll-forward/back recovery, fail-closed on unreadable state, unrelated files
untouched — reuse the Phase A _ComparisonStream protocol/tests pattern. Faults at every delete/replace/commit/cleanup/read boundary + restart.

B3 stage-6 admission: phase-wise peak = max(load/compute phase, rows/report phase), not sums. Price: retained AreaResults (or the Q3 artefact),
unpack cache (bounded, per-invocation, owner-scoped), named-case records, layout cache (per side × per case × 2 layouts × cache size,
largest frame of BOTH models), pinned overview masks, decode/eviction temporaries, row/reason structures with an UPPER bound per row,
sensitivity/ranking/explained structures. The CLI guard moves AFTER correspondence loading/binding (count ready/blocked pairs P and validated
sizes) but BEFORE any compute/allocation; T3 owns pipeline.py:954-983 stage-6 call sites, local_delta.py:375-377 and
local_delta_report.py:284-286 guards. T2 likewise prices link/coverage growth before allocation. Tests: measured tracemalloc peak ≤ priced
budget, and a one-byte-below refusal performs zero expensive calls; both runtimes; asymmetric frames; many groups/regions/links; oversized
P/sizes; cache eviction. --compact-json is not offered as a memory remedy.

B4 benchmark validity: generator writes consistent CSV areas, explicit role policy, complete inventory/assets; assert counts of ready ROIs,
queries, confirmed pairs, exact P·|sizes|·32 rows, and that named-pair, jitter and explained-union branches ran (counters). Keep an
end-to-end stage-5-produced fixture AND a separately labelled correspondence fixture with both endpoint bindings + repeats to exercise the
full T3 contract. Profiles: centered/high-link and many separated components; distinct/shared electrodes; equal/asymmetric frames;
overlapping/disjoint windows; P = 50, 200 and a guard-refusal boundary. Hotspot masks (not electrode rectangles) create stage-4 components.
Exit code 3 alone is not success: assert the outputs above.

B5 asset memo/lazy loading: invocation-scoped, bounded memo keyed by absolute root-resolved path + sha256 + value_semantics; every use
re-checks frame shape/valid equality and identity/endpoint binding (check_asset_manifest checks unchanged). Cache metadata/validation
results only, never all decoded arrays. _Assets failure order: preserve today's behaviour exactly (first failure stops later layouts and sets
the same global reason) — implement lazy loading in a way that reproduces that order, and test missing/invalid/mutated-between-validation
-and-use assets, same paths in different roots, same bytes with opposite semantics. Unpack cache scoped to the owning AreaResults/invocation.

B6 loader/I-O truth: pipeline._real_source → load_real_manifest authenticates and decodes every observed mask on every command
(stages 3–6, twice per side in 4/5/6). Keep that authentication (do not remove it to save time); corrupt-npz detection stays where it is.
Header-only map validation is an extra-load removal only. I/O acceptance = authentication passes (counted) + one representative load per
case (separate map-only counter). Stage-5 is not "seconds": it authenticates masks. Stage-6 time is split into load-validation,
both-model compute (or artefact reuse), windows, report. Stage-3 RSS is measured, not inferred from _dataset_bytes.

Advisories adopted: A1 (OR per electrode before counting; names from the same eligible links; preserve record metadata; drop full masks after
packing). A2 oracle: freeze source bytes + separate import-rewiring loader with a checked-in base digest; route the whole oracle call graph
(including pipeline helpers used by candidates) to frozen code; include a deliberate-perturbation test. A3 JSON streaming via a list subclass
adapter, bypass whole-result to_dict/json_value, preserve key order/newlines/CSV rendering. A4 "report" in Phase B means local_delta.html only;
integrated `report` stays out of scope; correct guard numbers (current layout charge 34.4 GB both models at 1262²). Measure O(R²) candidate
merging, O(P²) explained grouping, measure_union _slabs. A5 stage-1/2 untouched: no change to load_real_manifest, configured_dataset
defaults, estimate_area_bytes, assignment hashes, resource_estimate provenance; regression: sealed npz + legacy npy through stage 2 with
identical outputs; no new fields leaking into ElectrodeMaps.to_dict.

Q3 artefact (user: include): stage 2 additionally publishes `compare_masks/` (packed per-group masks stage 6 needs, keyed by comparison input
hash) inside the same Phase A publication transaction; stage 6 reuses when present and hash-matching, else recomputes. Reuse == recompute is
an acceptance test. comparison.json/csv bytes must stay identical (artefact is an extra file set).

## Tasks (serial, one editor)
- T1 oracle (Sonnet medium): tests/reference_stage46/* frozen 7bcb07a sources + rewiring loader + base digest; generators; equivalence suites
  for stage 4 (maps + full candidates output tree) and stage 6 (result + every written file); perturbation test; py3.8 + 3.11.
- T2 stage 4 + stage 5 gate (Sonnet high): electrode_map.py, candidates.py, roi_export.py, confirm_pairs.py (gate + slim reader),
  real_hotspot.py stage-4 guards; B1, B3(stage-4 part), B5(asset memo), B6, A1. DL2 validator check as in B1.
- T3 stage 6 compute + Q3 artefact (Sonnet high): local_delta.py, local_delta_report.py (_Assets, thumbs), pipeline.py stage-6 call sites +
  compare artefact publication, real_hotspot.py stage-6 guard; B3, B5, Q3.
- T4 stage 6 writers + transaction (Sonnet high): local_delta.py writers, local_delta_report.py HTML, pipeline.py stage-6 transaction; B2, A3.
- T5 benchmark + runners + docs (Sonnet medium): benchmark --stages 1-6 per B4 at N=1682, 1262² and 1500², P=50/200; py3.8 run; rebuild runners;
  docs/usage.md + configs/etx; checkpoint docs/plan/scale_stage46/README.md.
Astra xhigh code review after T5 (whole Phase B), fixes → re-review until PASS, then commit/push (user pre-authorized push after PASS).
