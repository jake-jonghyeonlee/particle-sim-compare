# Mask storage (.npz) — checkpoint 2026-10-22

- Orca Run run_0ed896be3d48. Risk tier HIGH (import-hotspots asset/manifest format). Base f30111c (+ uncommitted template-export work).
- T1 task_5a613904e3f0 / ctx_53774fcd516e: Sonnet 5.5 medium (effective verified) — succeeded, released. Spec: T1_spec.md.
- R1 task_22b56f987d98 / ctx_bee55f745a7d: Codex gpt-6-astra xhigh (user-specified; screen showed "GPT-6-Astra xhigh") — PASS-WITH-ADVISORIES
  on candidate diff T1_candidate.diff (sha256 prefix cb5d0fc85e65cdf3). Policy-required Opus 5.5 high review NOT run (user chose Astra).
- Validation: worker full suite 1100 passed / 3 skipped / 1 unrelated fail (test_handoff, sibling repo dirty); coordinator reran
  test_real_hotspot, test_single_file, test_etx_examples, test_candidates, test_real_roi_export: all pass.
- Open advisories: (1) validator now loads full heap arrays (legacy lost mmap); drop previous mask before next load.
  (2) npz member with non-npy bytes -> AttributeError instead of NumericContractError. (3) ENOSPC test does not cover partial write.
  (4) legacy test does not go through configured_dataset. Not verified: Python 3.8 / old numpy runtime on ETX.
- Pre-existing ETX limits found by review (not changed): configured_dataset output budget (8 GiB default) vs 10000x10000x32 diameters;
  Pillow pixel ceiling may reject >~89 MP PNGs.

## Update 2026-10-22 (T2 + R2)
- T2 task_008185eac759 / ctx_203977fe3d20 (Sonnet 5.5 medium, effective verified): fixed R1 advisories 1-4; suite 1103 passed / 3 skipped / 1 allowed fail. Released.
- Candidate T2_candidate.diff sha256 8bdd98b4f6606743…
- R2 Opus 5.5 high (task_f149795f213f / ctx_e7f8971256cd): PASS-WITH-ADVISORIES, no blockers. Ran test_real_hotspot under Python 3.8.20 + numpy 1.21.6 + Pillow<10: 40 passed.
- R2 Astra xhigh (task_aacde4504fd0 / ctx_6a6fab5a240f): PASS-WITH-ADVISORIES, no blockers. Bundles verified byte-for-byte.
- Open advisories: _load_mask `except Exception` maps MemoryError/OSError to real_mask_encoding_invalid (misdiagnosis on OOM);
  npz decompressed before shape check (forged-but-hash-consistent asset can allocate big memory); masks decompressed twice per stage;
  data_contract.md lacks mask_encoding; KeyboardInterrupt/SIGTERM leaves <out>.tmp-* (pre-existing); failed import leaves
  error.json/execution.json in <out>, so rerun needs <out> deleted (pre-existing). Not verified: numpy 1.16-1.20, real ETX Linux.
