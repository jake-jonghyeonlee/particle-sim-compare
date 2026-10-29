# layer-align Run (2026-10-29)

Layer PNG 를 정렬·그림에 쓰고, 정렬 결과를 변경 지도·순위·export 계산에 적용한다. 계획·검토 기록은 `paseo-ops/migration/runs/20261029-layer-align/`(PLAN_v1.2, PLAN_v1.3, R1/R2 결과)에 있다. 사용법은 `docs/usage_reference.md` "변경점 ROI", 계약은 `docs/data_contract.md` "Layer 정렬·unit 적용"에 있다.

## 사용자 결정 (2026-10-29)

| | 결정 |
|---|---|
| D1 | unit 접기: 4 unit 은 평행 복제. ROI 중심을 `(hotspot 좌표 + 500) mod unit` 로 접어 같은 unit 위치 ROI 는 대표 1개만 순위에 두고 나머지는 복제 위치 속성으로 남긴다. 기준은 reference 모델 unit 크기. `--suppress-overlap 0` = 조금이라도 겹치면 억제 |
| D2 | 새 입력 폴더 구조(`CNO/`, `Merged_Layer/<전극>/`, `Layer/`). 예전 구조도 읽는다(`Merged_Layer` 유무로 판단, CNO 대소문자 무관) |
| D3 | 그림: (i) frame 위치 그림 = Layer 별 색 stack(투명도) 위에 변경 위치·ROI 박스, (ii) ROI 주변 crop Layer stack. Layer 폴더가 없으면 예전 그림 |
| D4 | 전역 정렬은 CNT 유지, Layer 전역 정렬은 교차 확인. 국소 정렬은 Layer 별 Dice 우선(D4a). 변경된 층도 빼지 않음. 평균·중앙값 금지 |
| D5 | unit 크기가 다르면 리샘플하지 않고 unit 별 평행이동(unit offset → ROI 국소 offset) |
| D6 | unit offset·ROI 국소 offset 을 변경 지도·순위·case·export 계산에 적용(진단 전용 아님) |
| D7 | stack 그림은 모든 층, stack order 순 색 |

좌표 사실: hotspot frame 은 2×2 unit 원본 영상에서 사방 500 px 를 잘라낸 중앙 영역이다. unit 크기(px) = (hotspot 크기 + 1000) / 2, 0.1 µm/px.

## 커밋 (기준 `3efb504`, 브랜치 `feat/template-export`)

| Task | commit | 내용 |
|---|---|---|
| T1 | 26c8360 | 입력 스캔 v2 (CNO / Merged_Layer / Layer, 예전 구조 불변) |
| T2 | 8d059d3 | `layer_align.py` 순수 함수(unit 기하, Dice 우선 선택, Ω, unit 점수 창, 접기, 이음새) |
| T3 | c110726 | `change_maps` offset 장 + 이음새, 창별 offset, `--suppress-overlap 0` |
| T4a | cba2186 | `--unit-align-um`, `--apply-align unit`, `--unit-fold`, Layer 교차 확인 |
| T4b | 451f4cb | `--apply-align local`, `--refine-pool`, ROI 별 Dice 우선 국소 offset |
| T4c | 4014e2e | R2 권고 adv:1,2,3,4,7 + 재점수 비용 측정 |
| T5 | e0993a2 | alignment.html Layer stack 그림 |
| T6 | (이 커밋) | 문서, ETX 4회차 예시, bundles 재생성, 이 문서 |

## 리뷰

- R1 (T1–T3, Opus high) PASS @ c1107269, 차단 0. 권고 adv:1–7.
- R2 (T4a+T4b, Opus high) PASS @ 451f4cbe, 차단 0. 권고 adv:1–9.
- R3 (T4c+T5+T6, Opus high) 대기.

## 남은 일

1. **실자료 재실행 확인** (ETX 4회차): `alignment.json` 의 `unit_offsets` 상태(ok/ambiguous/low_score/search_bound_hit 비율)와 `unit_geometry.delta_u_yx_px`(ΔU), 접힌 수(funnel `unit_folded`, 질의 `unit_fold.n_replicas`), `refine_pool` 탈락 수(`outside_refine_pool`, `zero_after_refine`), `execution.json` `timings_s`, 프로세스 RSS. 실자료 ΔU 로 `--unit-align-um`·`--local-align-um` 하한을 확인한다.
2. **상수 조정**: `--unit-align-um`·`--local-align-um`·`--unit-fold-tolerance-um`(기본 window / 4)·`--refine-pool`(기본 10 × top-k)·`--align-min-dice`, `AMBIGUITY_DELTA`, `MIN_SUPPORT_PX` 는 합성 기준 초기값이다.
3. **DL1 T7**: DL1 저장소가 새 폴더 구조(대문자 `CNO`, `Merged_Layer`)를 읽게 한다. DL1 은 현재 대문자 `CNO` 를 오류로 거부한다. dl1-incr Run 이 끝난 뒤 이 Run 에서 이어서 한다. 시작 전 `paseo-editor-check` 를 다시 실행한다. DL1 의 정렬 통일은 사용자가 폐기했다(DL1 은 정렬을 측정하지 않음).
4. **측정 재확인 (R2 adv:5)**: 재점수 비용은 합성 기준 0.04 s(풀 200)이다. 실자료 변경은 훨씬 크므로 풀 200 의 재점수 시간(창마다 `_classify` FFT 포함)을 실자료로 다시 잰다. field 경로의 `estimated_bytes` 도 실측과 비교한다(합성: scalar/field 1.0 GiB, unit 단계 +806 MiB 대 추정 501 MiB, ×2 admission).
5. **Layer 있는 기본 실행의 시간 (R3 adv:9)**: 새 인수가 없어도 Layer PNG 를 2·L 번 디코드하고 sha256 을 계산한다. 실자료로 이 실행의 시간(2·L 디코드 + sha256)을 잰다.

## 열린 비차단 권고

| 출처 | 내용 | 상태 |
|---|---|---|
| R1 adv:3 | field 경로 임시 배열(cover uint8, seam mask, shifted valid)이 admission 추정에 없음 | 합성 실측만(×2 admission). 실자료 재측정 |
| R1 adv:4 | `_field_seam` 은 1×1~2×2 격자만 받음(모든 offset 이 같아도 거부) | unit 격자만 쓰므로 문제 없음. docstring 에 적을 것 |
| R1 adv:5 | 예전 구조도 casefold 라서 `Cno`·`cNO` 도 CNT | `docs/usage_reference.md` 에 적음 |
| R1 adv:6 | `merge_scope=all` 의 기여 case 중복 | T4 에서 대표 창 1개 + 모든 층 기여로 해결 |
| R1 adv:7 | `decided_by_layer` 는 선택이 없으면 `None` | `docs/data_contract.md` 에 적음 |
| R2 adv:2 | 국소 Ω 는 unit 영역으로 한정하고 unit 내부 경계 쪽만 r 침식(frame·상자 가장자리는 침식 안 함) | `docs/data_contract.md` 에 적음 |
| R2 adv:6 | 경계 창에서 `cnt_change_um2_by_layer`(unit 스칼라 offset)와 점수(field)의 기준이 다름 | `docs/data_contract.md` 에 적음 |
| R2 adv:8 | unit + local 진단 표의 g 는 전역 CNT offset | `docs/data_contract.md` 에 적음 |
| R2 adv:9 | `zero_after_refine` 은 병합 다리를 놓지 않음, `ring_inconclusive` 는 unit offset 으로 되돌림 | `docs/data_contract.md` 에 적음. `ring_inconclusive` 되돌림은 계약에 없던 보수적 선택 |
| X7 | 범위 밖 진짜 peak + 안쪽 부차 peak 는 `--align-min-dice`(`low_score`)와 `search_bound_hit` 로만 막힘 | 한계로 문서화 |
| T6 | 반경 반올림이 인수마다 다름(`--unit-align-um` .5 올림, `--local-align-um` Python `round`) | 문서에 적음. 통일 여부는 사용자 결정 |
