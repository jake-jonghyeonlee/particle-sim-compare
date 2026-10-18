# simcompare 수치 명세

채택 기준은 docs/plan/PLAN_v2.md와 기록된 사용자 gate 답변이다. 이 저장소의 현재 역할은 이미 계산된 numeric_v2 binary mask의 수치 비교와 검토 위치 내보내기다.

## 공개 명령

| 명령 | 역할 |
|---|---|
| demo-data | 결정적인 합성 numeric_v2 예제 생성 |
| validate-data | numeric 계약·hash·case/frame/diameter coverage 검사 |
| compare | reference와 target의 독립 frame에서 수치와 target-minus-reference |
| electrode-map | 단독 제품의 distinct electrode count와 layer maximum 요약 |
| export-review-rois | 사용자 위치 또는 명시 threshold 영역에서 review_roi_v1 생성 |
| merge-exports | 기존 particle-sim 1.x export의 source/ID 보존 병합 |
| compare-windows | 대응 위치의 같은 물리 창 안 ΔCA(b−a), attributable core, 창 크기 민감도, 나란히 보는 HTML |
| `python -m simcompare.correspondence` | `roi_correspondence_v1` 검증과 review ROI·DL1/DL2 라벨·DL2 검색 결과 변환 |

## 계산과 상태

line union L, pixel effective P\L, transfer P∩L(진단), union U=L∪P를 보존한다. pair exclusive/shared bucket은 나누지 않는다. source case의 lateral_short / vertical_short는 pair OR 이후에도 보존한다. 별도 축인 lateral_only / vertical_only / simultaneous의 합은 U이며 line / pixel에 더하지 않는다. open은 count 지도와 short 수치에 포함하지 않는다.

원 mask를 resize·확률 union 추정 없이 측정한다. pitch, origin, valid가 호환되어야 같은 group 안에서 union할 수 있다. 원 CA와 명시 window의 CA·valid area는 별도로 보고한다. 관측 0, missing, height evidence에 따른 N/A는 구별한다.

밀도는 지름별 한 값이며 동일 분포를 line과 pixel 및 세 failure pieces에 적용한다. 실제 단위/조사면적 변환 G1과 한-unit basis G2는 pending이다. 명시 합성 per-bin per-um²와 one-unit 계약에서만 weighted/panel 값을 계산한다. panel N은 제품별 축별 floor 또는 half-up이며 provenance에 치수와 rounding을 보존한다. 자동 /4는 없다.

유닛 크기가 다른 제품은 두 수준으로 비교한다(PLAN_v3, 2026-10-18). 제품 수준 `fraction_of_frame`은 group 면적을 측정 2×2 frame 면적으로 나눈 단위 없는 값이며 G2의 /4가 상쇄된다. 국부 수준 `compare-windows`는 `roi_correspondence_v1`의 대응 중심 주변 같은 물리 창(µm, 기본값 없음) 안에서 union과 세 조각을 재고 b − a를 낸다. 창 ≤ 지름이면 attributable core는 not_applicable, 창 clipping·valid 구멍·원천 누락이면 Δ는 null이다. 밀도·수율·확률은 아니며 DL 점수는 수치에 들어가지 않는다.

available 값만 value에 쓴다. partial/unavailable은 value=null, 알려진 부분 합은 partial_value이며 사유·coverage를 함께 쓴다. CSV도 null을 빈칸이나 0으로 바꾸지 않는다. delta는 양쪽 complete일 때만 complete다.

## 지도와 ROI

기본 지도는 short-only count 배열·discrete PNG·physical extent sidecar다. 대표 지름 기본 5 um. summary는 제품의 모든 layer를 pixelwise maximum으로 결합하고 frame 불일치면 unavailable이다. 기본 영역은 없고 사용자가 threshold를 지정할 때만 4-neighbour 영역을 만든다. shape/CNT/valid 원본으로 연결되는 사용자 위치가 기본 ROI 출처다. crop 크기 G4는 pending이며 exporter는 hotspot/CA를 모델 입력으로 내보내지 않는다.

## 호환 경계

기존 1.x `AnalysisBundle`은 legacy export 형식이다. source-step assignment를 실제 상부 layer로 암묵 변환하지 않는다. 유지된 loader와 merge-exports는 이 형식의 원본 연결 검산만 담당한다. numeric 비교는 독립 canonical schema를 사용한다.

AE/PCA 학습, retrieval, CAA, avoidance variants, 변경 위치 분석, old cards/plots는 폐기되었다. docs/arch와 walkthrough는 legacy / archived 기록이며 현재 기능 명세가 아니다. 현재 계산과 교환 세부 규칙은 [데이터 계약](docs/data_contract.md), [ROI 계약](docs/review_roi_contract.md)을 따른다.
