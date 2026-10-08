# 도메인 입력 확인 패키지

## numeric_v2와 legacy 예제의 경계

새 수치 경로 문서 예제는 [numeric_v2/README.md](numeric_v2/README.md)의 `manifest.json`, `pairs.csv`, `density.csv`다. 내부 canonical 계약 초안 및 합성 검산 자료이며 실제 원천 파일 형식/G1/G2/G7 답변이나 현재 어댑터가 읽는 입력으로 승인한 것이 아니다. 원본 ID·source-step assignment·실제 layer 결합과 32 지름 상태를 보존하며 pending panel과 blocked 지도 ROI를 보여 준다. G1/G2/G7a/G7b의 답변 값은 null이다.

아래 기존 파일은 **1.x / CAA / AE legacy 확인 패키지**다. 기존 count/glass·정규화·source-step 의미를 numeric_v2 density나 unit 환산으로 자동 적용하지 않는다. 실제 새 입력은 명시 어댑터/변환 근거로 전환하거나 unsupported로 남긴다. 모든 예제 값은 합성이다.

## 기존 패키지 (legacy)


이 디렉터리는 실제 데이터 연결 전에 공정·시뮬레이션 담당자와 확인할 입력 형식을 한곳에 모은 예시다.
`fab_measurements.csv`, `layer_stack.csv`, `design_review_status.csv`는 예시 어댑터가 같은 이름으로 직접 읽는다.
나머지 파일은 현재 규칙과 향후 선택 입력 형식을 보존하는 확인용 템플릿이다.

모든 예시 ID는 `EXAMPLE_`로 시작한다. 실제 자료를 작성할 때는 예시 행을 복사하지 말고 실제 값으로 교체한다.

## 파일 목록

| 파일 | 용도 |
|---|---|
| `domain_rules.yaml` | 지금까지 확정된 단위·CA·Fab·이미지·vertical short 규칙과 미확정 항목 |
| `layer_stack.csv` | 제품별 하부→상부 적층 순서와 Fab 공정 layer 대응 |
| `fab_measurements.csv` | Fab·제품군·공정 layer별 확정 32개 크기의 평균 검출 개수와 조사 조건 |
| `image_pairs.csv` | Layout·hotspot의 픽셀 1:1 대응 및 실제 영역 확인 |
| `layer_equivalence.csv` | 제품 간 이름이 다른 layer의 공통 역할 대응표(현재 보류) |
| `shape_feature_definitions.csv` | 개선 가설에 사용할 형상 지표의 정의와 변경 가능 조건 |
| `expert_validation.csv` | 제품 제외 검증에 사용할 전문가 위험 유형·반대 사례 라벨 |
| `design_review_status.csv` | 개선 후보의 재시뮬레이션·전기적 검토 진행 상태 |

`design_review_status.csv`의 `comparison_group_id`는 분석 결과의 값을 그대로 복사한다. 형식은
`<failure_type>|<정렬된 전극 역할...>::<정렬된 layer...>`이며 임의의 별칭으로 바꾸지 않는다.

## 상태 값

- `confirmed`: 현재 대화에서 확정됨
- `provisional`: 대략 맞지만 계산 담당자의 최종 확인이 필요함
- `pending`: 아직 결정되지 않음
- `not_applicable`: 해당 없음

## 적층 순서 작성 규칙

- `stack_order`가 클수록 상부다.
- 대체로 모든 설계 버전이 같은 순서를 쓰면 `design_version`에 `*`를 쓴다.
- 특정 설계 버전만 순서가 다르면 그 버전의 행을 별도로 추가하며, 명시 버전이 `*`보다 우선한다.
- 제품마다 layer 개수가 달라도 된다.
- 2026-09-27 계약 1.2: Vertical short도 source-step Fab layer를 유지하고 적층표는 geometry만 검증한다.
- Legacy(미표시/1.1): 두 대상 layer 중 `stack_order`가 큰 layer의 `fab_process_layer`를 적용한다. 이 예시의 기존 측정/적층표는 legacy 의미를 유지한다.
- 대상 layer가 표에 없거나 순서를 판정할 수 없으면 CAA를 계산하지 않는다.

## Fab 자료 작성 규칙

- 한 행은 `Fab × 제품군 × 공정 layer × 이물 크기` 한 점이다.
- 원시 검출 로그가 아니라 위 조건별로 집계한 가공 요약자료를 입력한다.
- 크기 격자는 확정: 1–30 um 1 um 간격 + 40, 50 um (32개).
- 이물 크기는 원형으로 시뮬레이션한 이물의 지름이다. 비원형 이물은 현재 범위 밖이다.
- CA·hotspot·Fab은 모두 확정 32개 전체 격자를 제공하므로 기본 연결에서는 보간하지 않는다.
- 같은 정수 크기의 값을 정확히 연결하고 누락된 크기는 0으로 채우지 않는다.
- `avg_detected_count`는 지정된 기간의 glass 1장당 평균 검출 개수다. 0과 미관측 빈 값은 구분한다.
- `surveyed_area_um2`는 glass 한 장에서 실제로 검사한 물리 면적이다.
- `surveyed_area_um2`와 `averaging_basis=per_glass`는 unit-pixel 예상 개수 환산에 필요하다.
- 현재 CAA 식은 잠정 규칙이므로 `domain_rules.yaml`의 `real_mode_enabled`가 `false`인 동안 실제 결과로 사용하지 않는다.
- CAA는 제품 간 비교용 상대 위험 지표로만 사용하며 불량 확률·예상 불량 개수·수율로 해석하지 않는다.

## 이미지 대응 작성 규칙

- 같은 행의 layout과 hotspot은 동일한 unit-pixel 영역이어야 한다.
- `width_px`, `height_px`가 같고 `pixel_match=true`, `transform=identity`여야 현재의 1:1 대응 조건을 만족한다.
- 뒤집힘·회전·crop이 있으면 `pixel_match=false`로 기록하고 정합 규칙이 추가되기 전까지 학습에서 제외한다.

## 개선 후보 작성 규칙

- 전극 간격, 배선 폭, 평행 구간 길이, 겹침 면적, 우회 형상을 모두 후보로 다룬다.
- 전기적 제약으로 후보를 자동 승인하거나 자동 탈락시키지 않는다.
- 고위험·저위험 비교 사례에서 실제로 관찰된 차이에 근거한 후보를 가능한 한 넓게 제시한다.
- 실제 CAA가 비활성화된 동안 제품 간 고위험·저위험 순서는 면적 보정 CA를 우선 사용한다.
- 각 후보는 사람이 하나씩 검토하며 재시뮬레이션과 전기적 검토 상태를 별도로 기록한다.
- 저항, capacitance, coupling, 신호 지연·무결성, 설계 규칙, 다른 short/open 증가는 외부 시뮬레이션·설계 검토 항목이다.
- 이 프로젝트는 위 전기량을 계산하거나 예측하지 않고 외부 결과의 상태와 참고 경로만 기록한다.
- 외부 결과가 없으면 후보를 제거하지 않고 `외부 검토 필요`로 표시한다.
- 후보는 원인이나 개선 효과가 확정되었다는 표현을 사용하지 않는다.

## 첫 버전 검증 규칙

- 현재 전문가 위험 유형 정답 라벨은 없다.
- 제품 제외 검색 일관성, 위험 경향 일치, 군집 배정 안정성, seed 안정성, 반대 사례 일관성을 근거로 보고한다.
- 전문가 라벨이 생기기 전에는 정확도·precision·recall이나 위험 인자 탐지 성능을 주장하지 않는다.
- `expert_validation.csv`는 향후 전문가 라벨을 추가할 때 사용할 선택 입력 예시다.

## 아직 답이 필요한 핵심 항목

1. **pending** — CAA의 공식 계산식 최종 확인
2. **pending** — 제품 간 layer·전극 역할 대응표

전문가 라벨은 현재 필수 질문이 아니라 향후 선택 입력이다.

Fab 자동 선택은 모든 mode에서 family+layer-wide, null-family+layer-wide 순서다.
Pair-specific 분포는 bundle의 명시적인 `fab_distribution_id`로만 선택한다.
