# Scale plan v1 — ETX stages 2–6 on real data, identical numerics

Planner: Claude Fable 5.1 / medium (read-only draft). Base: `01989de` on `feat/template-export`. Risk tier: HIGH.
Scale assumptions (from brief): 1,682 cases × 32 diameters = 53,824 observed records per model, frame 1262² ≈ 1.6 M cells
(bool 1.6 MB, packed 0.2 MB, int32 6.4 MB, float64 12.7 MB), worst case all cases in one layer, two models.

## 1. Root causes

| # | Symptom | Cause | Where |
|---|---------|-------|-------|
| T1 | compare time superlinear (50→100 cases: 197 s→1,124 s) | `shares` rebuilds `inside` by AND/AND-NOT over **every** pair mask for **every** membership pattern, then `np.any(mask & inside)` per lineage source per pattern: O(patterns × (pairs + sources) × cells) | `src/simcompare/linepixel.py:388-401` |
| T2 | `_membership_counts` 15 % | stacks all pair masks per chunk, `np.packbits` + `np.unique(axis=0)` sort; also needs numpy ≥ 1.17 (`bitorder=`) | `linepixel.py:220-239` |
| T3 | per-record measurement | `measure_full_mask` → `measure_union` → `_cell_areas` allocates a fresh float64 frame (12.7 MB) and `areas * valid` for every record, although frame and window are identical for all 53,824 records of a model | `src/simcompare/measurement.py:59-76, 110-116, 139-141` |
| R1 | RAM ~86 GB/model (172 GB in stages 4/6 which hold both sides) | `configured_dataset` loads every observed mask eagerly as bool `MaskRecord.mask` | `src/simcompare/adapters/real_hotspot.py:702-720` |
| R2 | estimator rejects real scale (`oversized_real_materialization`, `oversized_real_operation`) | estimator models the eager design: `observed_count×cells` (86e9 > 20e9 in `configs/etx/*.yaml`), `transient = max_layer_count×20×cells` (54 GB), output `14×cells` per group | `real_hotspot.py:650-676` |
| R3 | ~10 full bool masks retained per group (line/effective/transfer/union/3 pieces + pair masks during the call); 11 layers × 32 d × 10 × 1.6 MB = 560 MB/model, ×2 in stage 6; plus `contributor_records` keeps every source mask of the group alive until `shares` ends | `linepixel.py:316-424` |
| R4 | `SourceCaseArea.raw` is `copy.deepcopy(case.raw)` **per diameter** → 32 copies of a 32-record list per case (1.7 M small dicts ≈ 1 GB of Python objects at scale) | `linepixel.py:331-334` |
| D1 | comparison.json 290 MB → 1.1 GB (×3.8 for ×2 cases); json.dumps builds the whole string in RAM | `pipeline.py:44` (`indent=2`), payload = two full `AreaResult.to_dict()` (`pipeline.py:206-230`). Linear part: per-diameter `raw` duplication (R4) → ~N×1024 record dicts. Superlinear suspect: `pair_lineage_case_keys`/`contributing_source_case_keys` (80-char keys) summed over all shared buckets — count grows with overlap density. Must be measured (Task 3 first step), not assumed. |

Consumers checked: `compute_areas` ← `pipeline.py:167` (synthetic rates), `:206/212` (real compare), `local_delta.py:375-376` (stage 6, both sides at once),
`dl_results.py:338` (synthetic). `configured_dataset` ← `pipeline.py:35` (`_configured_real`: compare, map, candidates, stage 6 via `_dataset_for` l.608-616),
`pipeline.py:387` (validate-data, load-only), `adapters/electrode_png.py:262` (stage 3). Retained masks are read by `local_delta.py:210, 637`,
`local_delta_report.py:227`, tests `test_linepixel.py:322-324`, `test_failure_pieces.py:37-40,136` (expects read-only arrays).
Real `comparison.json` (`real_hotspot_comparison_v1`) readers: **only tests** (`tests/test_real_hotspot.py:84-106,176,227-232`, `test_cli_e2e.py:13`);
`final_report.py:325-331` requires the synthetic `layers` key and already stops with `missing_input_file` on real data (`docs/usage.md:301`).
`test_numeric_cli.py:35-49,225` compares the **synthetic** payload (built from the same `GroupArea.to_dict()`) against `tests/fixtures/numeric_v2/expected` — so `to_dict()` must not change.

## 2. Design (numerics identical by construction)

### 2.1 Single-pass membership labelling (T1, T2)
Replace `_membership_counts` + `shares` with partition refinement, per class (`keep` = `valid` for line, `valid & ~line` for pixel):
```
label = zeros(cells, int32); patterns = [()]            # id 0 = no member
for pid in sorted(pair_masks):                           # same order as today's `pairs = sorted(masks)`
    sel = pair_masks[pid] & keep
    old, inv = np.unique(label[sel], return_inverse=True)
    new = [new_id(patterns[o] + (pid,)) for o in old]    # patterns stay sorted tuples
    label[sel] = np.asarray(new, int32)[inv]
counts = np.bincount(label, minlength=len(patterns))     # pattern → cell count (count*pixel_area unchanged)
```
Contributors: `touched[source] = set(np.unique(label[source_mask]))` in one pass per source; bucket contributors = lineage sources with `pattern_id ∈ touched`.
Equivalence: a cell is in today's `inside(members)` iff its final label pattern == members (AND of members, AND-NOT of non-members); `np.any(mask & inside)` iff
the source touches a cell with that label. Bucket order is `sorted(counts.items())` on the same tuples → identical buckets, keys, lineage, contributors.
Cost O((pairs + sources) × cells) per class; label 6.4 MB. Only one source mask is unpacked at a time.

### 2.2 `_group` streaming loop and packed retention (R3, R4, T3)
- Iterate `entries` in today's sorted order; load each record once (lazy map, §2.3); do measurement, `type_masks |=`, pair-union OR (incremental, replaces
  `union_masks` but keeps its compatibility checks via a `check_union_compatible(first, record)` helper raising the same codes), then keep only
  `np.packbits(mask)` per source (0.2 MB) for the contributor pass. Pair masks: packed too (unpack once per labelling pass). Worst case 2 × 1,682 × 0.2 MB = 670 MB per group, released after the group.
- `GroupArea` stores retained masks packed (`PackedMask(shape, bits)`); `line_mask`, `pixel_effective_mask`, `transfer_mask`, `union_mask`, `failure_piece_masks[name]`
  become properties returning an unpacked read-only bool array (fresh array per access; `local_delta` crops immediately, so no behaviour change). Dataclass fields renamed with a `_packed` suffix; constructor is only called in `_group`.
- `raw` deep-copied **once per case** inside `compute_areas`, shared by the 32 `SourceCaseArea`s (never mutated; `to_dict()` output identical).
- Keep `chunk_cells` parameter and its validation (`invalid_membership_chunk_size`) for API/test compatibility; it no longer influences work.
- New code uses only numpy ≥ 1.16 APIs (`packbits/unpackbits` without `bitorder`, `unique(return_inverse)`, `bincount`).

### 2.3 Lazy per-record loading in `configured_dataset` (R1)
- New `LazyRecords(Mapping[float, MaskRecord])` in `real_hotspot.py`: stores per-diameter header (status, reasons, `SourceCA`, tolerance, asset path, source record);
  `__getitem__` runs today's `_load_mask` + dtype/shape checks (`real_mask_shape_mismatch`, l.706-708) and returns a full `MaskRecord`; no cache.
  `header(d)` returns `RecordHeader(diameter_um, identity, analysis_layer, input_status, reasons)` (new NamedTuple in `numeric_schema.py`).
- `linepixel._validate_case` and the grouping loop in `compute_areas` (l.451-475) use `records.header(d)` when available, else the `MaskRecord` itself; `_group` loads via `case.records[d]`. Plain `dict` records (synthetic, tests, `local_delta`) keep working unchanged.
- `electrode_map._validate_case`/`compute_electrode_maps` (l.228-239, 319) iterate `.items()/.values()` → still correct (sequential loads, nothing retained); optional header use is a follow-up.
- `validate-data` (`pipeline.py:387`) must keep failing fast on a bad asset: add a streaming touch pass (load every observed record once, discard) — ~5–10 min at scale; the message stays `real_mask_shape_mismatch`/`real_mask_encoding_invalid` with the case name.

### 2.4 Resource estimator matching the streaming model (R2)
`estimated_peak_bytes = retained_packed + group_transient + payload_estimate` with
retained_packed = layers × 32 × 10 × cells/8; group_transient = 24 × cells + 2 × max_layer_count × cells/8; payload ≈ observed records × 2 KB.
Real scale ≈ 0.7 + 0.7 + 0.1 GB ≈ 1.5 GB per model → passes the 8 GiB default. Keys stay accepted and validated (`invalid_resource_limit` unchanged):
`max_operation_bytes` compares against the new estimate; `max_group_mask_cells` keeps meaning "cells of one layer at one diameter" (2.7e9 at scale; raise the default
500 M → 5 G since the packed set is cells/8 bytes); `max_materialized_source_cells` is applied to `max_layer_count × cells` (what is materialised per group), not to the whole model, and documented as such. `provenance.resource_estimate.method` describes the streaming model. Physical-memory cap logic (l.642-646) unchanged.

### 2.5 comparison.json (D1) — `real_hotspot_comparison_v2`, written only by the real path of `run_numeric_compare`
Unavoidable: v1 at N=1,682 is ≥ 18 GB even if linear, and `json.dumps` needs the full string in RAM. Change (lossless, reversible by a reader):
1. `source_cases[*].raw` → `raw_ref` (case_id); each side gains `cases_raw: {case_id: raw}` written once.
2. Written with `json.dump(fp, separators=(',', ':'))` (no indent) side by side — reference side serialised and freed before target is computed (today both payload dicts coexist, `pipeline.py:206-216`).
3. If Task 3's measurement shows lineage/contributor lists dominate, add per-group `source_index: [source_key…]` and encode bucket lists as integer indices (`*_idx`) — same information, decided from numbers.
In-memory `AreaResult.to_dict()` and the synthetic path stay v1 (goldens untouched). `comparison.csv` rows unchanged.
Readers to update: `tests/test_real_hotspot.py:84-106,176,227-232` (only if they touch `raw`; keys they use survive), `tests/test_cli_e2e.py:13` (status only),
`docs/usage.md:301`, `tests/fixtures/handoff/numeric_context.json` (check for the version string), `scripts/benchmark_real_hotspot_compare_map.py` (check). `final_report.py` unchanged (still unsupported for real).

### 2.6 Shared cell-area cache (T3)
`measure_full_mask` passes a per-frame cache `(cell_areas, areas=cell_areas*valid, valid_area)` keyed by `id(frame)`+`frame.valid_hash` (computed once per group); the identical arrays feed `areas[record.mask].sum()`, so float results are bit-identical. `measure_union` signature unchanged (optional kwarg).

## 3. Tasks (serial, one editor; each starts from the previous Task's commit)

**Equality oracle (used by T1–T3):** copy today's `_membership_counts`, `_group`, `shares` and `measure_full_mask` verbatim from `01989de` into
`tests/reference_linepixel.py` (test-only, never imported by `src`). `tests/test_linepixel_equivalence.py` asserts `AreaResult.to_dict()` equality, mask equality
(`line/pixel_effective/transfer/union/pieces`) and `area_rows` CSV equality on: `oracle_cases()` permutations, `test_failure_pieces` fixtures, `tests/fixtures/numeric_v2`,
the real-hotspot temp datasets of `test_real_hotspot.py`, and a seeded randomized generator (200 iterations: frames 1–7×1–9 with `valid` holes, 1–8 pairs, 1–3 sources/pair,
line/pixel mix, missing/not_applicable/zero/duplicate-pair sources, overlapping random rectangles, same-net cases). Also assert `NumericContractError` codes match on the generator's invalid variants.

### T1 — linepixel single pass + packed retention + measurement cache (risk HIGH: core calculation)
- Target: `src/simcompare/linepixel.py` (`_group`, `_membership_counts`, `GroupArea`), `src/simcompare/measurement.py` (cache kwarg), `tests/reference_linepixel.py`, `tests/test_linepixel_equivalence.py`.
- Change: §2.1, §2.2 (packed masks, raw copy once), §2.6. Plain-dict records only (lazy map arrives in T2 but `_group` already loads via `records[d]` once per record).
- Constraints: `to_dict()` bytes identical; public names (`compute_areas`, `GroupArea.*_mask`, `Bucket`, `chunk_cells`) kept; no numpy > 1.16 API; no Python > 3.8 syntax.
- Acceptance: `pytest tests/` green; equivalence suite green; micro-benchmark (in-process, 100 pairs × 1270², random overlaps) ≥ 10× faster than reference and ≤ 1 GB RSS; `uv run --python 3.8 --with numpy==1.21 pytest tests/test_linepixel.py tests/test_linepixel_equivalence.py tests/test_failure_pieces.py` green.
- Ownership: linepixel.py, measurement.py, new test files only.

### T2 — lazy records + streaming estimator + validate touch pass (risk HIGH: resource limits, recovery messages)
- Target: `src/simcompare/adapters/real_hotspot.py:590-760`, `src/simcompare/numeric_schema.py` (RecordHeader), `src/simcompare/linepixel.py` (header use in `_validate_case`/grouping), `src/simcompare/pipeline.py:387` (touch pass), `tests/test_real_hotspot.py:200-215,365-375,436-460`, `configs/etx/real.stage2.example.yaml`, `real.stage4.example.yaml`, `configs/etx/README.md`.
- Change: §2.3, §2.4. Limit tests keep their error codes but with inputs that trigger the new estimate (document the arithmetic in the test).
- Constraints: `MaskRecord`/`Frame` unchanged; synthetic `load_dataset` untouched; same error codes and case names in messages; both stage-4 sides may coexist (two lazy datasets ≈ metadata only).
- Acceptance: `pytest tests/` green (including equivalence against a real-hotspot temp dataset); RSS test: 300 cases × 32 d × 1270² via `compare` ≤ 2 GB peak (new test, marked slow); `validate-data` on a corrupted .npz fails with the existing code; Python 3.8 run of `tests/test_real_hotspot.py`.
- Ownership: files above only.

### T3 — comparison.json v2 streaming writer (risk NORMAL; schema change, but real v1 has no production reader)
- Target: `src/simcompare/pipeline.py:170-230` (+ a `_write_comparison_v2` next to `_write_inspection`), readers listed in §2.5, `docs/usage.md:301`, `docs/plan/scale_compare/` measurement note.
- Change: first measure the v1 payload breakdown at N=50/100 (bytes per `raw`, lineage, contributors, rest) with a throwaway script and record it; then §2.5 items 1–2, item 3 only if lineage/contributors > 30 % of bytes.
- Constraints: `comparison.csv`, synthetic path, `AreaResult.to_dict()` unchanged; `exit_code 3` semantics unchanged.
- Acceptance: tests green; at N=100 file ≤ 10 % of today's 1.1 GB; a reader helper in tests reconstructs v1 `source_cases[*].raw` from v2 and matches the reference writer byte-for-byte after `json.loads`.

### T4 — scale benchmark, 3.8 compatibility run, rebuilt runners (risk NORMAL)
- Target: `scripts/benchmark_scale_compare.py` (new; `benchmark_real_hotspots.py` builds identical masks and only covers import), `bundles/etx/` via `scripts/build_etx_stage_runners.py`, `docs/usage.md` ETX section, checkpoint in `docs/plan/scale_compare/`.
- Change: generator = N cases, crop size S (default 1262), seeded random overlapping blobs with one layer (`--layers 1`) and per-case pair ids; runs `import-hotspots` → `validate-data` → `compare` (→ optional `compare-windows` with a synthetic correspondence) through the CLI as subprocesses; reports wall time per command, `ru_maxrss` of children, output sizes; `--json` summary; `--cases 50,100` reproduces the coordinator run.
- Acceptance targets (Mac M-series, S=1262, one layer): N=100 compare ≤ 90 s wall and ≤ 2 GB peak RSS (today 1,124 s / 10.3 GB); N=50→100 time ratio ≤ 2.3; N=300 ≤ 5 min, ≤ 3 GB. Extrapolation to N=1,682 written into the checkpoint from the measured per-record load (~54k loads × ~10 ms ≈ 9 min), labelling (~32 groups × 1,682 pairs × ~1 ms ≈ 1 min) and measurement (~54k × ~5 ms ≈ 5 min): expected ≤ 30 min and ≤ 6 GB per model, stage 6 ≤ 10 GB for both sides. Numbers above are targets for the benchmark to confirm or refute, not promises.
- Also: full `pytest` under `uv run --python 3.8 --with numpy==1.21 --with pillow`; rebuild runners 2–6 and run the bundled runner once on the benchmark folder.

## 4. ETX acceptance and user-facing changes
- Stage-1 outputs stay valid: mask encoding `npz_deflate_bool_v1`, manifest and legacy `.npy` reading are untouched; the user restarts from stage 2 with the new runners.
- Config: no required change. Remove the now-misleading comment in `configs/etx/real.stage2.example.yaml:33-35` (`max_operation_bytes` guidance) and document the streaming meaning of the three limits; the 20e9/5e9 example values remain valid.
- Runners 2–6 must be rebuilt (`scripts/build_etx_stage_runners.py`); stage 3 (`import-electrode-assets`) and stage 5 (`confirm-pairs`) do not call `compute_areas` but share the bundle, so they are rebuilt too.

## 5. Risks, open questions, out of scope
Risks: (a) the superlinear JSON growth may be in bucket lists — T3's measurement decides item 3; (b) `electrode_map` validation loads all 32 records per case sequentially (stage 4, ~10–20 min for two sides) — acceptable but not optimised; (c) lazy loading reads each .npz once per use: `validate-data` + `compare` = 2 passes, stage 6 = 1 pass per side; a slow network filesystem on ETX would dominate (benchmark reports load time separately); (d) `local_delta` `_as_dataset`/`bind_pairs` must not iterate `records.items()` on both sides (check in T2; sequential loads would only cost time); (e) tests that construct `GroupArea` or rely on mask fields being dataclass fields (none found outside `linepixel.py`).
Open questions for the user: (1) ETX numpy/Python exact versions (code today already needs numpy ≥ 1.17 for `bitorder`; new code targets 1.16); (2) ETX RAM and whether the server filesystem is local; (3) is the full real `comparison.json` detail needed at all, or would a per-group summary plus `comparison.csv` be enough (would make v2 much smaller); (4) expected number of layers actually populated.
Out of scope: changing any numeric definition, bucket semantics or `to_dict()` layout; `final_report` support for real data; parallelism/multiprocessing; stage 1 import changes; `electrode_map` header optimisation; DL2 repository.

## 6. Coordinator acceptance notes (Opus 5.5, 2026-10-22)
- Accepted as v1 subject to Astra xhigh plan check (user-specified reviewer; Opus high review not requested this round).
- User facts: hotspot crop 1262x1262 in one model; 1000–1500 px per model, constant within a model; layer distribution unknown
  (depends on electrodes) — keep the one-layer worst case as the acceptance basis.
- §2.1 must keep pattern 0 (no member) out of buckets, and contributor touch sets must use the record's raw mask exactly as the old
  `np.any(record.mask & inside)` (inside ⊆ keep ⊆ valid), proven by the oracle on masks that extend outside `valid`.
- Remove every `bitorder=` use reachable from stages 2–6 (numpy 1.16 target), not only in new code.
- Open question (3) is left as default: lossless v2; do not drop detail without a user decision.
