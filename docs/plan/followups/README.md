# Follow-ups after Phase B + expected cases — checkpoint (2026-10-23)
(ΔV 가중 취약점 ROI 의 남은 항목은 [../deltav/README.md](../deltav/README.md), 2026-10-28.)
(층간 거리 필터·입력 경로 통일·통합 ETX 실행기의 남은 항목은 [vdist.md](vdist.md), 2026-11-13.)
(장부 보고서 Run 20261115-ledger-report 의 남은 일은 아래 "Ledger report Run" 절, 2026-11-15.)
Orca Run run_c9825fe14b68. Base c7a2829. Reviews by Claude Opus 5.5 high (Codex unavailable: 401).
- F1 (Sonnet high): RB-A3/A4/A5/A7/A8/A10/A12 + EC-A2/A4 fixed with regression tests. F2 (Sonnet medium): docs/usage.md rewritten
  (385 → ~155 lines: flow table, ETX, Windows CMD, common), full detail moved to docs/usage_reference.md (old tokens missing = 0).
- F-R FAIL (B1: real compare pre-transaction errors still deleted the previous generation) → F3 (Sonnet medium) fixed + A1-A4.
- F-R2 FAIL (B2: F3 flagged non-transactional writers too, torn set on ENOSPC) → F4 (Sonnet medium) narrowed the flag.
- F-R3 PASS-WITH-ADVISORIES. Coordinator fixed A1 (cli.py comment) and A2 (F1_report correction); bundles rebuilt.
- Open: F-R3 A4 (pre-existing) successful inspection-only compare leaves an older comparison_masks.zip; F-R A5/A6 notes.

## Ledger report Run (20261115-ledger-report, 2026-11-15)

ETX 08(`ledger` + `ledger-report`)까지 구현했다. 남은 일:
- 장부 2단계: unit 지도(보고서 2절), 5절 위치 카드·범위 합계. 다음 Run. 규칙은 [../ledger_report/README.md](../ledger_report/README.md).
- 성능: linepixel ledger 분담의 bincount 개선(리뷰 R1 adv:5). ETX 실측: 02 시간과 `compare2.json` 크기(리뷰 R2 adv:10).
- 완료(ledger/T10): 07 과 ledger 의 stale 검사를 공통 함수 `compare2.stale_reason(doc)` 로 합쳤다(R2b adv:1). 규칙이 늘면 이 함수만 고친다.
- 완료(ledger/T10): 타일 ② 대기 처리(R4 adv:1), 전극·CNO 기타 줄 대기 표시와 "있음·값 0 = 0%" 테스트(R4 adv:2·3).
- 실자료 CNO case 는 전극 이름 자리가 "CNO" 라 장부에서 CNO 값이 "CNO" 한 전극에 모인다. 실제 CNT 층 전극으로 귀속할지 결정이 필요하다.
- 08 상위 N 옵션(`--top-pairs`, `--top-electrodes`)은 실행기 상수가 없다(기본 30). 필요하면 상수를 더한다.
