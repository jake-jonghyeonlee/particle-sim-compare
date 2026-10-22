# EC-T1 — auto-generated expected-case inventory (union of models). Risk tier HIGH (stage-1 manifest / coverage semantics).
Worker: Sonnet 5.5 medium. All validation in the FOREGROUND. Base: HEAD d8f6dbb (feat/template-export), clean.
User decision (2026-10-23): expected cases = UNION of case names discovered under the reference and target hotspot roots, so a case folder
missing in only one model is reported (expected_case_folder_missing); a case missing in both is not detectable — record that limitation.
Ownership: src/simcompare/adapters/real_hotspot.py (import_hotspots + a discovery helper; load_real_manifest validation of the new field),
src/simcompare/cli.py (import-hotspots option), scripts/build_etx_stage_runners.py (stage-1 JOBS) + rebuilt bundles/ via the build scripts,
configs/etx/README.md, docs/usage.md, docs/data_contract.md (real_hotspot_v1 section), tests (new + tests/test_real_hotspot.py).
Change:
1. Factor the existing case discovery in import_hotspots (os.walk, images/ layout, ambiguous/nested/duplicate checks, _case_name) into one
   helper used for both the imported root and the extra roots — identical names and errors.
2. New keyword `expected_case_roots: list[Path] | None` and CLI `--expected-cases-from-roots ROOT [ROOT ...]` (mutually exclusive with
   --expected-cases → clear error). expected = sorted(union of discovered names of the own root and every extra root). The own root's
   behaviour (assets, diagnostics, missing-case diagnostics for names only in other roots) is otherwise unchanged.
3. Manifest gains `expected_case_source`: {"kind": "file"} for --expected-cases, {"kind": "union_of_discovered_roots", "roots": [<root as given,
   posix>], "limitation": "cases absent from every root are not detected"} for the new option, null when none. expected_case_coverage_status
   stays 'confirmed' when either source is given. load_real_manifest accepts manifests WITHOUT the key (existing imports stay valid) and
   validates its shape when present. No change to stage 2+ code paths beyond tolerating the field; comparison outputs for an import made
   with an explicit identical file must equal those made with the union option except the provenance field.
4. Stage-1 ETX runner JOBS: each import-hotspots job passes the other model's root via --expected-cases-from-roots. Rebuild all runners.
5. Docs: ETX restart note — to unblock stage 4, re-run stage 1 (delete/rename data/imported/reference and target first; --out must not exist),
   then stages 2→6.
Acceptance: tests — union with a case only in the other root → that case is expected and diagnosed missing in this model; mutual exclusion;
legacy manifest without the key loads; manifest hash stable/recomputed correctly; stage-4 map coverage becomes 'available' (not blocked by
expected_case_inventory_unprovided) on a fixture imported with the new option; full pytest green except tests/test_handoff.py; /tmp/py38
(PYTHONPATH=src) run of tests/test_real_hotspot.py and the new tests; stale-bundle test green after rebuild. No commit. worker_done with files/tests.
