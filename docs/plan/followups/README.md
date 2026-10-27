# Follow-ups after Phase B + expected cases — checkpoint (2026-10-23)
(ΔV 가중 취약점 ROI 의 남은 항목은 [../deltav/README.md](../deltav/README.md), 2026-10-28.)
Orca Run run_c9825fe14b68. Base c7a2829. Reviews by Claude Opus 5.5 high (Codex unavailable: 401).
- F1 (Sonnet high): RB-A3/A4/A5/A7/A8/A10/A12 + EC-A2/A4 fixed with regression tests. F2 (Sonnet medium): docs/usage.md rewritten
  (385 → ~155 lines: flow table, ETX, Windows CMD, common), full detail moved to docs/usage_reference.md (old tokens missing = 0).
- F-R FAIL (B1: real compare pre-transaction errors still deleted the previous generation) → F3 (Sonnet medium) fixed + A1-A4.
- F-R2 FAIL (B2: F3 flagged non-transactional writers too, torn set on ENOSPC) → F4 (Sonnet medium) narrowed the flag.
- F-R3 PASS-WITH-ADVISORIES. Coordinator fixed A1 (cli.py comment) and A2 (F1_report correction); bundles rebuilt.
- Open: F-R3 A4 (pre-existing) successful inspection-only compare leaves an older comparison_masks.zip; F-R A5/A6 notes.
