# 통합 HTML 보고서 계획 v1 (2026-10-19)

기준: branch `rewrite/line-pixel-rate`, commit `1ac6441`, 작업 트리 clean.
위험 등급: **Normal**. 계산 코드는 바꾸지 않고, 이미 만들어진 출력 파일만 읽어 보고서를 쓴다. T1에서 `compare-windows` 출력에 crop PNG 파일을 추가하지만 수치는 바꾸지 않는다.

## 1. 사용자 결정 (2026-10-19)

| 항목 | 결정 |
|---|---|
| 독자 | 둘 다. 앞에는 보고용 요약, 뒤에는 상세 위치 |
| 실행 방식 | 새 명령 `simcompare report`가 기존 출력 폴더를 읽어 HTML 한 파일을 만든다 |
| 대표 지름 | 5 µm |
| 요약에 올리는 쌍 | 증가(악화) 상위 10쌍, 감소(개선) 상위 10쌍을 따로 |
| 순위 기준 창 | 20 µm. 임시값이며 제품(모델)마다 다를 수 있어 인수로 바꿀 수 있게 하고, 보고서에 "임시 기준값"이라고 표시한다 |
| 넣지 않는 것 | 수율 환산, 밀도 단위 환산, layer 겹쳐보기, 뷰어 (보류 항목) |

## 2. 명령

```
simcompare report --compare-dir <compare 출력> [--map-dir <electrode-map 출력>]
                  [--windows-dir <compare-windows 출력>] [--rois-dir <export-review-rois 출력>]
                  --out <새 폴더> [--diameter-um 5] [--rank-window-um 20] [--top-n 10]
```

- 출력: `<out>/report.html` 한 파일(이미지·그래프 모두 내장, `file://`로 열림), `<out>/report.json`(보고서에 쓴 값과 입력 파일 SHA256), `execution.json`.
- `--compare-dir`만 필수다. 나머지 폴더가 없으면 해당 섹션에 "입력 없음"을 표시한다.
- 그래프는 외부 라이브러리 없이 inline SVG로 그린다(폐쇄망 대응).
- 종료 코드는 기존 규칙을 따른다: 0 = 완료, 3 = 일부 섹션이 partial/unavailable, 2 = 입력 오류.
- 입력 오류(exit 2)에 해당하는 경우:
  - 폴더 사이에 reference/target product_id가 다름
  - `is_synthetic`이 섞여 있음
  - `--rank-window-um` 값이 `local_delta.json`의 `window_sizes_um`에 없음 (이 경우 사용 가능한 창 목록을 오류 메시지에 함께 알린다)
  - schema_version이 기대값과 다름

## 3. 보고서 구성

### A. 요약 (보고용, 1~2 화면)

| # | 섹션 | 데이터 |
|---|---|---|
| A0 | 표지 | 제품 a/b, 실행 일시, 합성/실자료, `producer_commit`, 기준값(지름 5 µm, 창 20 µm 임시, N=10) |
| A1 | 핵심 지표 카드 | layer별 union frame 비율 Δ @5 µm (`comparison_fractions.csv`); line/pixel Δ (`comparison_summary.csv`, G1 미확정 표시); 쌍 수 ready/blocked; 문제 행 수 |
| A2 | 악화 상위 10 / 개선 상위 10 | `local_delta.ranking.by_window_size["20.0"]`에서 Δ_union > 0 내림차순, < 0 오름차순. Δ = 0 쌍은 개수만 표시. partial/unavailable 쌍은 별도 표시. 각 행은 상세 섹션 B3로 가는 앵커 링크 |
| A3 | 판정 조건과 주의 | `applied_gate_decisions` (G1·G2·G4 pending 등), `failure_piece_axis_note`, `fraction_of_frame_note`, 창 20 µm가 임시값이라는 문구 |

### B. 상세

| # | 섹션 | 데이터 | 표시 |
|---|---|---|---|
| B1 | frame 면적 비율 | `comparison_fractions.csv` | 지표별 지름에 따른 a/b 꺾은선(SVG), 5 µm 표 |
| B2 | line/pixel/transfer와 불량 유형 조각 | `comparison_summary.csv`, `comparison_failure_pieces.csv` | 막대 그래프 2개. 두 축은 서로 더하지 않는다는 문구 |
| B3 | 대응 위치 상세 | `local_delta_rows.csv`(창 20, 지름 5), T1의 crop PNG | 쌍별 카드: a/b layout·hotspot crop, a / b / Δ 표(union + 조각 3종, window/core). 순서는 A2와 같고, 나머지 쌍은 그 뒤에 |
| B4 | 창 크기 민감도 | `window_sensitivity_rows.csv`, `_rank_agreement.csv` | 표. 추천값은 계산하지 않음 |
| B5 | 취약 위치 지도 | `electrode_map.json`, heat map PNG | 이미지 내장, histogram 표 |
| B6 | 검토 ROI | `review_rois.json` | 표, 제외 기록 |
| B7 | 상태·누락 | 모든 입력에서 status ≠ available인 행과 reasons | 표 |
| B8 | 출처 | 입력 파일 경로와 SHA256, `config_sha256`, `correspondence_sha256`, `source_manifest_hash` | 표 |

## 4. Tasks

| Task | 내용 | 쓰기 범위 | 등급 |
|---|---|---|---|
| T1 | `write_local_delta_html`이 이미 만드는 crop 이미지를 `local_delta_thumb_<hash>.png`로도 저장하고, `local_delta_thumbs.json`(pair_id, 창, 지름, side, kind → 파일, 상태 note)을 쓴다. 기존 HTML과 수치는 변경 없음. 파일명이 `local_delta` 접두사라 `_remove_reports` 정리 대상에 자동 포함된다 | `src/simcompare/local_delta_report.py`, `tests/test_local_delta_report.py` | Normal |
| T2 | `src/simcompare/final_report.py`(읽기, 검증, 섹션 조립, SVG)와 CLI `report` 하위 명령 | `src/simcompare/final_report.py`(신규), `src/simcompare/cli.py` | Normal |
| T3 | 테스트와 문서: demo 4개 명령 → report 실행 end-to-end, 섹션별 값이 원본 CSV/JSON과 일치, 오류 케이스(제품 불일치, 창 20 없음, 폴더 누락) | `tests/test_final_report.py`(신규), `docs/usage.md`, `scripts/2_demo.sh` | Normal |

의존 관계: T1 → T2 → T3. 한 번에 한 명이 편집한다.

## 5. 인수 조건

- `uv run pytest` 전체 통과. 기존 시험의 결과는 변하지 않는다.
- demo에 대해 `report`가 exit 0으로 `report.html`을 만들고, 외부 URL(`http`, `<script src`)이 없다.
- A1/A2/B1에 나오는 숫자가 원본 CSV/JSON 값과 같다(시험에서 대조).
- 악화/개선 목록에서:
  - Δ 부호와 정렬이 맞다.
  - 10쌍보다 적으면 있는 만큼만 나온다.
  - Δ = 0인 쌍은 어느 목록에도 들어가지 않는다.
- 위의 각 입력 오류는 exit 2이며 `report.html`을 남기지 않는다.
- 모든 텍스트는 escape한다(기존 report.py와 같은 방식).

## 6. 남은 확인 (구현을 막지 않음)

- 창 20 µm는 임시값이다. 실자료에서 제품별 값이 정해지면 `--rank-window-um`으로 바꾼다.
- 데모 frame은 작아서 창 20 µm가 clipping된다. 그래서 end-to-end 시험은 작은 창을 인수로 주고 실행한다.
- 리뷰: T1~T3 완료 후 fresh Sonnet high 독립 리뷰 1회.

## 7. v1.1 수정 (2026-10-19, 시안 검토 후)

시안: `/tmp/mockgen/report-mockup-v1.html` (생성 스크립트 `/tmp/mockgen/gen.py`). 사용자 판단: "좀 더 다듬어야 하지만 이 정도로 구현".

| # | 피드백 | 반영 |
|---|---|---|
| F1 | 지표 카드가 무엇을 뜻하는지 모르겠다 | 카드마다 쉬운 말 한 줄 설명(무엇을 셌는지, + 이면 b가 더 나쁨/좋음)과 계산 출처를 단다. A1 아래에 "지표 읽는 법" 접이식 설명(`<details>`)을 둔다 |
| F2 | 영역이 전체 어디인지 확실히 보이게 | (a) B3 앞에 제품별 **전체 frame 위치도**: 축소한 layout 위에 모든 쌍의 창을 번호와 색(악화/개선/기타)으로 표시. (b) 각 B3 카드에 작은 locator(전체 frame 축소본 + 창 위치). (c) A2 표에 a/b 중심 좌표 열 추가 |
| F3 | 색: 개선 = 파랑, 악화 = 빨강 | Δ와 악화/개선 표시는 빨강/파랑만 쓴다. 그래프의 a/b 계열은 이와 겹치지 않게 a = 회색, b = 보라로 바꾼다 |
| F4 | B3는 그대로, 다만 중복 영역 확인 필요 | 같은 제품·frame·layer에서 창(순위 기준 창 크기)이 서로 겹치는 쌍을 찾아 표시한다. B3 카드에 "창 겹침: P07(겹침 x µm²)" 배지, A2 아래와 B7에 겹침 목록, 그리고 "겹친 쌍의 Δ를 단순 합산하지 말 것" 주의를 넣는다. 값은 바꾸거나 빼지 않는다 |
| F5 | 일단 다 넣고 진행 | 섹션은 v1 그대로 모두 넣는다 |

T1 추가: crop PNG 외에, 제품(side)별 **전체 frame 축소 layout PNG**(긴 변 ≤ 800 px, valid 밖 회색)와 frame 메타(origin, pitch, shape)를 `local_delta_thumbs.json`에 함께 쓴다. report는 이 PNG 위에 SVG로 창 사각형을 그린다.

리뷰: 사용자 지시로 **이번에만** Sonnet high 대신 Codex `gpt-6-astra / high` 독립 리뷰 1회(읽기 전용, 명시 terminal 경로).

Orca Tasks: T1 → T2(기존 T2+T3 통합: report 모듈, CLI, 시험, 문서) → 리뷰.
