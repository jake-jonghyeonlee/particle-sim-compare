# Scale plan v2 — supersedes v1 where they differ (2026-10-22)

Coordinator: Opus 5.5. Base for v1: 01989de. v1 (Fable draft) stays the design reference: docs/plan/scale_compare/plan_v1.md.
v2 resolves Astra xhigh plan check PC1 (task_414d334edd9a, ACCEPT-WITH-CHANGES, blockers B1–B6, advisories A1–A4).
Risk tier: HIGH for every Task below (core calculation, resource limits, output schema, recovery).
Reviewer (user-specified): Codex gpt-6-astra / xhigh after each phase.

## Phases
- Phase A — stages 2 (validate-data, compare) feasible at real scale; stages 4 and 6 fail fast with a clear,
  actionable resource error instead of OOM. User restarts from stage 2 after Phase A.
- Phase B — stages 4 (electrode-map/candidates) and 6 (compare-windows + report) streaming. Planned as v3 after
  Phase A evidence (B3, B4 details below are carried over as the Phase B brief).

## Changes to v1

### B1 (partition correctness)
Final buckets = tuple-keyed counter built only from labels with id != 0 AND final frequency > 0; sort by member tuple,
never by label id. Same for keep = valid and keep = valid & ~line. Duplicate-pair sources OR into one class/pair mask
first; missing/rejected/zero-area sources stay in lineage; only current computable sources can contribute.
Oracle cases add: identical and nested pairs (dead labels), all-false / all-invalid frames, line fully covering pixel,
>64 pairs, non-byte-aligned frame widths, masks extending outside valid.

### B2 (bounded labels and output)
- Patterns stored by predecessor encoding (parent_id, added_pid) — O(1) per new label; tuples reconstructed only for
  final live labels. Compact (remap to live labels via np.unique(return_inverse)) only when history exceeds a
  threshold (e.g. 4 × live-capacity or 2^30), so int32 can never overflow and history memory stays bounded.
- Output budget: count bucket entries (sum over buckets of len(pair_ids)+len(lineage)+len(contributors)) as they are
  produced; exceed `max_bucket_output_entries` (new assignment key, default documented, validated like other limits)
  -> NumericContractError('oversized_real_bucket_output', '<layer> d=<diameter>: <n> entries; limit <m>').
- Tests: dense distinct patterns (checkerboards, 100 independent 50% masks on 64x64), nested/identical masks,
  missing-only inventories; assert peak label-history size and no int32 overflow.
- Compatibility helper for union checks keeps metadata only, never a full first MaskRecord per pair.

### B3/B4 (stages 4 and 6) — Phase A guard, Phase B fix
Phase A: add operation-specific admission estimates that include the paths v1 missed, and fail fast before loading:
- stage 4 electrode-map/candidates: electrode_map.py:359-380 (representative record per case, one bool mask per
  distinct electrode, np.stack), regions :390-405, candidates.py:451 (first side retained) => ~5·N·cells per side.
- stage 6 compare-windows: local_delta_report.py:161-169,284 (layout masks of both models) => ~4·N·cells, plus both
  AreaResults (local_delta.py:421-422).
Error codes: reuse 'oversized_real_operation' with a detail naming the stage, the estimate, the limit, and the
remedy ("split cases / raise max_operation_bytes up to physical RAM / wait for streaming stage-N support").
Phase B (v3): lazy/bounded layout loading, bounded crop/unpack cache for local_delta windows/jitter, header-only
validation in electrode_map, streaming electrode accumulation, stage-6 writer like T2, full stage-4/6 scale acceptance
with distinct electrodes, many components, both sides, differing frame sizes.

### B5 (writer first, publish-on-success)
Task order is T1 linepixel -> T2 writer -> T3 lazy/estimator -> T4 benchmark/compat/runners. The compare RSS gates live
in T4 only. The v2 writer must never build the full v1 dict or asdict(raw) first; it streams per group/per source.
Output is written to a sibling temp file and atomically renamed only after both sides succeed; on any failure
(target compute error, OSError/ENOSPC, interrupt) the temp file is removed and an existing comparison.json is kept.
T2 is HIGH.

### B6 (compatibility commands)
Python 3.8 runs use an isolated no-project venv, e.g.
`uv venv --python 3.8 /tmp/py38 && uv pip install --python /tmp/py38 numpy==1.21.6 'pillow<10' pyyaml pytest`
then `PYTHONPATH=src /tmp/py38/bin/python -m pytest -p no:warnings <numeric/ETX-relevant tests>` (list the files run;
do not claim the whole suite). numpy floor: remove both bitorder uses (linepixel.py:251,254 — the only ones found);
supported floor is stated as "tested 1.21.6 / Python 3.8"; 1.16 is not certified (no wheels locally). The test-only
reference oracle keeps bitorder and is skipped under numpy < 1.17 (documented).

### Advisories adopted
- A1: no extra validate-data touch pass; keep current CLI error precedence (load_real_manifest authenticates first).
  Benchmark reports hash/decode counts per command. Document that physical-RAM detection is host RAM, not cgroup.
- A2: v2 keeps an explicit side namespace, exact raw values, ordering, missing/unresolved cases; if integer indices are
  used, round-trip pair_ids, lineage and contributors. Unconfigured real branch (pipeline.py:246) stays as is (state it).
  Test-only decoder v2->v1 equality; mapping documented in docs/usage.md.
- A3: retained-mask properties return read-only arrays; failure_piece_masks stays dict-style; geometry-unconfirmed
  records still become missing; raw sharing is documented as read-only. Measurement cache only per group full frame,
  same reduction order; tests with fractional pitch/origin, partial valid, distinct-but-equal Frame objects.
- A4: test existing sealed stage-1 imports (both .npz and legacy .npy) load unchanged; update
  scripts/build_etx_stage_runners.py:42-43 text and configs/etx/*.yaml comments; isolated per-command RSS
  (/usr/bin/time or psutil per subprocess, not RUSAGE_CHILDREN cumulative); benchmark data spec fixed in the script.

## Phase A Tasks (serial, one editor, Sonnet 5.5 medium; each leaves changes uncommitted, no commit)
- T1 linepixel: v1 §2.1, §2.2 (packed retention, raw once per case), §2.6, + B1, B2, B6 (bitorder). Oracle file
  tests/reference_linepixel.py + tests/test_linepixel_equivalence.py. Acceptance: full pytest green; equivalence suite;
  in-process micro-benchmark (100 pairs × 1262², seeded blobs) reported vs reference (target ≥ 10×, report actual);
  py3.8 isolated run of linepixel/equivalence/failure_pieces tests.
- T2 comparison v2 writer: v1 §2.5 + B5 + A2. Acceptance: tests green; recovery tests (target failure, ENOSPC
  mid-write, existing report preserved); payload breakdown measured on a saved fixture and recorded.
- T3 lazy records + estimator: v1 §2.3 (without the touch pass), §2.4, + stage-4/6 admission guards (B3/B4 Phase A),
  A1, A3. Acceptance: tests green; limit tests keep codes with documented arithmetic; stage-4/6 guard tests.
- T4 benchmark + compat + runners: scripts/benchmark_scale_compare.py (isolated per-command RSS, decode counts),
  N=50/100/300 at S=1262 one layer; py3.8 isolated run; rebuild all runners; docs/usage.md ETX section; checkpoint.
  Targets (report actual, not promises): N=100 compare ≤ 90 s, ≤ 2 GB; extrapolation to N=1,682 with method.

## v2.1 (2026-10-22) — user decision after T3 evidence
Evidence: T3 seeded fixture N=300 one layer needed max_bucket_output_entries=5M and wrote a 779 MB comparison.json; real crops
put every hotspot near its crop centre, so shared-bucket output can reach GB–tens of GB at N=1,682.
User decision: default output = summary; full bucket lists only when `full_bucket_detail: true` in the config.
New T4 (HIGH): summary mode. T5 (was T4): benchmark + py3.8 + runner rebuild.
