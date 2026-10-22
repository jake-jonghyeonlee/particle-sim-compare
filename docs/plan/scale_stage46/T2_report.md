# Phase B T2 report — stage 4 streaming + stage-5 gate (Sonnet 5.5 high, 2026-10-22, uncommitted on top of 7bcb07a + T1)

## Items
1. **Stage 4 (electrode_map.py).** Lazy records are read through `LazyRecords.header` (validation, frame comparison, representative lookup);
   exactly one representative mask load per case (new `map_loads` counter); the loaded record is measured (`measure_full_mask` with one
   full-window `area_cache` per layer), packed (`np.packbits`, `_Observed`: record fields + packed original mask + bbox of set cells) and
   released. `counts` = sum over electrodes of the OR of that electrode's actives (one bool temp). Regions link by bbox prefilter +
   `packed & inside`; `names` come from the same linked cases; `measure_union` receives the rebuilt full-frame `MaskRecord` and one
   `area_cache` per region. `ElectrodeRegion.mask` stays the same required dataclass field (name/position/ctor arg/to_dict key) but is stored
   packed (`_PackedMaskField` descriptor; every read returns a fresh read-only bool array). identity/endpoints/raw/metadata are built once per
   case and shared by all its links; region coverage = shallow `{**coverage, 3 scalars}` (lists shared with the layer map); one provenance copy
   for all regions. `to_dict` unchanged (test). Corrupt NON-representative `.npz` is no longer read by the map (authentication in
   `load_real_manifest` still reads every mask on every command, B6).
2. **Export (roi_export.py).** Rows keep every scalar coverage field and carry `observed_ref` in place of the four identity lists
   (`observed`, `missing`, `not_applicable`, `measurement_errors`); the lists are written once in top-level `region_coverages`
   (omitted when no region row exists, so manual-only exports are byte-identical). Writer streams (`JSONEncoder.iterencode`) to a temp file and
   `os.replace`s it. `expand_region_coverages()` restores the old form; expanding a parsed slim file and dumping with indent=2 gives the 7bcb07a
   bytes (test over 6 scenarios x 2 models). `export_hooks()` (roi_export) lets `candidates` apply the role gate BEFORE the single write, receive the
   rows/payload without re-reading, share an `AssetMemo` and reserve bytes — pipeline.py is untouched. `candidates` streams its file hashes
   (`file_sha256`), keeps only slim rows, and processes the products sequentially (reference query+index export, then target), dropping each
   product's maps after its exports; the second product's map is admitted with the first one's rows/regions reserved.
3. **Guards (real_hotspot.py, stage-4/5 only).** New formulas: `estimate_stage_map_admission` (packed cache + counts + scan temp), counting,
   regions, per-region `guard_stage_map_links` (links + the shared per-case copies, BEFORE windows/CaseLinks/measure_union), `guard_stage_map_export`
   (rows, before any row exists), all with `reserved`; stage-4 messages use `STAGE4_REMEDY` (no "streaming pending"; `OPERATION_REMEDY` for stages 2/6
   is unchanged). Stage 5: `guard_stage_confirm_read` (sizes x `STAGE5_PARSE_MULTIPLIER=5` + biggest x 3 vs the datasets' limit) before any export is
   read; the reader drops `region_coverages` after parsing. B5: `AssetMemo` (key = resolved path + sha256 + value_semantics; value = shape + mask
   digest; a hit re-resolves the path and re-hashes the file; shape and `valid` equality are re-checked per use; bounded; invocation-scoped).
4. **Benchmarks** (docs/plan/scale_stage46/T2_benchmark.md, scripts in T2_bench/): N=300, 1262², one layer, centered/grid x distinct/shared:
   RSS 264-341 MB vs 1.2-7.8 GB, 11-37 s vs 37-94 s, query export 1.5/24 MB vs 37/650 MB per side; mask decodes 600 map loads vs 6,000.

## DL2 check (read-only, particle-sim-dl2 not edited)
`psdl2.data.load_export` (import via sibling `src`, SCHEMA_VERSION `review_roi_v1`) loads the slim exports (reference/target/index) with the
same ready/blocked rows and reasons as the expanded old form. DL2 reads only `schema_version` and `provenance.coverage.source_coverage_status`.
=> version kept `review_roi_v1`; NO `--review-roi-format` switch, NO DL2 follow-up needed. A skip-if-absent test pins this.

## Validation
- 3.11 full pytest (minus test_handoff.py and the stale-bundle test below): green. py38 (`/tmp/py38`): equivalence (105 incl. new), streaming (32),
  scale guards, candidates, confirm-pairs, roi/real roi/template export green; the other files fail/err exactly as at HEAD 7bcb07a in that
  environment (pandas/tomllib missing; identical 70-line FAILED/ERROR list at baseline, compared by id).
- `tests/test_etx_examples.py::test_stage_runners_are_current_and_parse` FAILS by design: the committed `bundles/etx/*.py` embed the changed sources
  (T5 rebuilds them; not touched here).
- T1 oracle: frozen sources and digests untouched. Only the harness gained the documented expansion (`expand_slim_exports`) + three tests
  (expansion bytes, candidates with an asset mutated after its first read, which the memo must treat like the old repeated decode).

## Deviations / notes for the coordinator
- All four identity lists (not only `observed`) moved to `region_coverages`; row key is `observed_ref` as specified.
- candidates export order changed (reference query, reference index, target query, target index); contents are byte-equal after expansion; error
  precedence differs only when both products fail.
- The count-prepass guard cannot fire before admission in small one-layer cases (admission has the 32 B/cell scan temp); it is kept for run-heavy /
  link-heavy later layers and tested through its estimate function and `links_done`.
- Constants (`MAP_*`, `STAGE5_*`) are conservative (estimate = 1.55x traced peak at N=300/1262²); the per-case raw/metadata copy is priced at 128 KiB
  (generator: 46 KB, real 32-record cases are larger).
- measure_union's repeated `_slabs` requested-area reduction (measurement.py) is untouched (out of scope): per-link CPU of a window-heavy region
  is unchanged; grid profile 5,269 links = 14 s per side.
- Benchmarks use the same data directory for both sides and 4 of 32 diameters per case (generation time); authentication/map-load counts scale with
  records: report per-case numbers for 32 diameters (old ~69 map loads per case vs new 1).
