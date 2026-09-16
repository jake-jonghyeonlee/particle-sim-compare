# 모델 설계와 분석 목적

## 목적과 비목적

- 목적: 이미 계산된 시뮬레이션 결과를 제품 간 비교·검색·검토하기 위한 **사례 특징 벡터**를 만든다.
- 비목적: CA·hotspot 예측, layout 자동 수정, 전기적 성능 예측, 인과관계 확정.

## 주 모델: 다중 입력 오토인코더 (`model.py`)

```text
layout (2ch: 전극 a, b) ──CNN enc──┐
hotspot (Kch: 이물 크기별) ─CNN enc─┤→ concat → Linear → z_struct ┐
CA 곡선 (log, mask) ────MLP enc────┤                              ├→ z (결합) → 입력별 decoder
형상·두께·실제 크기 ─────MLP enc────┘                              │
Fab 모양 + log 총량 (mask) ─MLP enc──→ Linear → z_fab ────────────┘
```

- 이미지 encoder 입력은 `[image*valid, valid]`, 수치 encoder 입력은 `[value*mask, mask]`다.
- 샘플에 없는 입력의 embedding은 presence(0/1)를 곱해 끈다.
- `model.enabled_inputs`, `conv_channels`, `modality_dim`, `latent_dim`은 설정값이다. `image_only`/`numeric_only`/`full` 비교는 `evaluate`가 수행한다.
- `z_struct`(구조·시뮬레이션 결과 측)와 `z_fab`(Fab 측)을 따로 저장하지만, 이것만으로 두 효과가 인과적으로 분리되었다고 볼 수 없다. `z_struct`에는 CA·hotspot이 들어 있어 Fab과 무관하지 않다.
- decoder의 CA·hotspot 복원은 특징 학습용 내부 목적이다.

## 손실 (`losses.py`)

| 입력 | 손실 | 유효 원소 |
|---|---|---|
| layout (이진) | BCE with logits | valid 영역 × 전극 존재 |
| hotspot (가공 강도) | sigmoid 출력 MSE | valid 영역 × 크기별 존재 |
| CA, 형상 | MSE | 관측 마스크 |
| Fab | 모양 MSE와 총량 MSE의 평균 | 관측 마스크 |

샘플별로 유효 원소 평균을 먼저 구하고, 입력이 없는 샘플은 제외한 뒤 샘플 평균을 낸다. 그다음 입력별 가중 평균을 합산한다. 따라서 이미지의 픽셀 수가 수치 입력을 압도하지 않는다. 입력별 손실은 `train_history.csv`에 따로 기록된다.

## 학습 (`train.py`)

- 제품별 표본 수 역수 가중 샘플링(`train.balance_products`)으로 편중을 줄인다.
- 정규화 통계(`dataset.fit_normalizers`)는 학습 행에서만 계산한다.
- seed, 설정 전체, 학습/평가 sample_id, 손실 종류, 라이브러리 버전은 checkpoint와 `run_record.json`에 기록한다.
- 기본 device는 `cpu`. `auto`는 CUDA → MPS → CPU 순서로 선택한다.

## 기준 모델 (`baseline.py`)

명시적 특징 → 학습 행 기준 표준화 → 누락 지시 변수 → PCA.

- `shape_only`: layout 채움 비율·경계 밀도·전경 종횡비, 실제 크기, 두께·형상 수치
- `with_risk_baseline`: 위 특징 + hotspot 평균/최대/0.5 초과 비율, CA 곡선, Fab 모양·총량

같은 `retrieval.search` 절차를 AE 공간(`with_risk_ae`, `ae_struct`, `ae_fab`)과 기준선 공간에 적용해 딥러닝의 필요성을 비교할 수 있다.

## 분석 (`analysis.py`, `retrieval.py`, `cards.py`)

1. 유사 사례 검색: 같은 불량 종류·전극 역할 집합, layer 조건, 다른 제품만(`other_products_only`), 필터, 실제 크기 허용 비율.
2. 군집화: 학습 행으로 KMeans 적합, 대표 사례(중심 최근접)와 반대 사례(다른 군집의 가장 가까운 학습 행). 군집 번호에는 의미가 없다.
3. 제품별 비교: 원 CA, CA/면적, 크기별 곡선, demo CAA(실제 Fab 분포 / 지정 기준 분포).
4. 순위 비교: 실제 Fab vs 기준 분포, 원 CA vs 면적 보정 CA의 순위 변화와 Spearman 상관.
5. 제품 하나씩 제외: 상위 항목 Jaccard, 형상 기준선 이웃 집합 Jaccard.
6. 검토 카드: 근거와 "자료 없음", "전문가 확인 필요" 목록. 중요 인자 자동 판정은 하지 않는다.

**해석 주의**: `with_risk_*` 공간은 CA·hotspot을 입력으로 쓰므로, 그 공간에서 위험이 비슷한 사례를 찾은 것은 독립적 발견이 아니다. `neighbors.csv`의 `reuses_risk_inputs` 열과 카드의 `retrieval.note`로 구분한다. latent·attention 값으로 중요도 점수를 만들지 않는다.

## 검증 (`split.py`, `pipeline.run_evaluate`)

- 제품 ID와 설계 계열 ID의 연결 성분 단위로 분리한다. 변경본·크기별 기록·국부 조각이 학습/평가 양쪽에 섞이지 않는다(`assert_no_leakage`).
- Fab·제품군 그룹 제외 검증은 교락 상태를 함께 보고한다. 그룹이 2개 미만이면 "평가 불가", 일대일 교락이면 "효과 분리 불가"다.
- 복원 오차는 해석 품질을 보장하지 않는다.

## 한계

- 합성 데이터 결과는 코드 동작 확인일 뿐 연구 성능 근거가 아니다. 실제 자료로 한 검증은 없다.
- 표본이 작으면 AE가 기준선보다 나을 근거가 없다. 두 공간을 함께 보고 판단한다.
- CAA 규칙, layer 대응, Fab 단위가 확정되지 않았다(`open_questions.md`).
- 후속 후보(미구현): MOFA+, DCCAE, GNN (`references.md`).
