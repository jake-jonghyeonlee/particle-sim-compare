# 실행 가이드

이 문서는 simcompare를 설치하고, 합성 데이터로 전체 흐름을 실행하고, 결과를 읽고, 실제 자료로 바꿔 실행하는 방법을 순서대로 설명한다.

> simcompare는 **이미 계산된** 시뮬레이션 결과를 제품 간에 비교·해석하는 도구다. CA나 hotspot을 예측하지 않고, 인과관계나 수율을 판정하지 않는다. 합성 데이터 결과는 코드 동작 확인용이며 연구 근거가 아니다.

## 1. 준비

| 항목 | 요구 사항 |
|---|---|
| Python | 3.11 이상 |
| 패키지 관리 | [uv](https://docs.astral.sh/uv/) |
| 장치 | CPU로 전체 실행 가능. CUDA/MPS는 선택 |
| 네트워크 | 설치 후에는 필요 없음(외부 API·모델 다운로드 없음) |

```bash
cd particle-sim-compare
uv sync                    # 의존성과 simcompare 명령 설치
uv run pytest              # 설치 확인: 모든 테스트 통과해야 함
uv run simcompare --help   # 명령 목록 확인
```

`uv run` 대신 가상환경을 직접 쓸 수도 있다: `.venv/bin/simcompare ...` 또는 `.venv/bin/python -m simcompare ...`.

## 2. 가장 빠른 실행 (합성 데이터 한 번에)

```bash
uv run simcompare all --config configs/demo.yaml --demo-out demo_data
```

1. `demo_data/`에 합성 데이터를 만들고, 설정의 `data_dir`을 이 경로로 바꾼다.
2. `validate-data → train → embed → analyze → evaluate`를 차례로 실행한다.
3. 결과는 `outputs/demo/`에 저장된다(설정 `output_dir`).

stderr에 `DEMO 모드: 합성/예시 설정이며 결과는 연구 근거가 아닙니다`가 출력되면 정상이다. CPU에서 수 초 안에 끝난다.

## 3. 단계별 실행

각 명령은 같은 설정 파일을 받는다. `train` 이후 단계는 `output_dir/model.pt`가 있어야 한다.

```bash
uv run simcompare demo-data --out demo_data --seed 0
uv run simcompare validate-data --config configs/demo.yaml
uv run simcompare train         --config configs/demo.yaml
uv run simcompare embed         --config configs/demo.yaml
uv run simcompare analyze       --config configs/demo.yaml
uv run simcompare evaluate      --config configs/demo.yaml
```

| 단계 | 하는 일 | 주요 결과 | 먼저 볼 것 |
|---|---|---|---|
| `demo-data` | 합성 데이터 생성 | `demo_data/` 전체, `summary.json` | `summary.json`의 `notes` |
| `validate-data` | 입력 점검(자료를 수정하지 않음) | `validation.json` | 누락 수, 실제 크기 없는 샘플, 교락 상태, CAA 적용 불가 사유 |
| `train` | 제품·설계 계열 단위 분리 후 오토인코더 학습 | `model.pt`, `train_history.csv`, `split.json`, `run_record.json` | 평가로 간 제품, 입력별 손실 추이 |
| `embed` | 특징 공간 계산 | `embeddings.npz`, `embedding_meta.csv` | 공간 이름과 차원 |
| `analyze` | 검색·순위·군집·안정성·검토 카드 | `ranking.csv`, `neighbors.csv`, `clusters.json`, `cluster_map.png`, `loo_stability.csv`, `review_cards.json` 등 | `review_cards.json` |
| `evaluate` | 평가 행 복원 손실, 보지 않은 제품의 반복성, 입력 구성 비교, 그룹 제외 검증 | `evaluation.json` | `held_out_product_evidence`, `ablation`, `leave_one_group_out.*.verdict` |

모든 명령은 끝나면 생성한 파일 경로와 요약을 JSON으로 표준 출력에 쓴다.

## 4. 결과 읽는 법

### 4.1 `validation.json` (데이터 점검)

| 키 | 의미 |
|---|---|
| `counts` | 묶음·제품·설계 계열·제품군·Fab 수, 불량 종류별 건수 |
| `missing_inputs` | 입력별 누락 건수. `open_b_not_applicable`은 누락이 아니라 open의 두 번째 전극 '해당 없음' 건수 |
| `physical_scale` | 실제 크기 정보가 없어 물리 길이·면적 비교가 불가능한 샘플 |
| `hotspot_layout_overlap` | hotspot과 layout의 상관이 0.8을 넘는 샘플(이미지 특징이 형상을 이중 반영할 가능성) |
| `confounding` | Fab × 제품군 교차표와 상태(`ok`, `partially_confounded`, `fully_confounded`, `insufficient_groups`) |
| `independent_groups`, `split_feasible` | 학습/평가로 나눌 수 있는 독립 그룹 수와 분리 가능 여부 |
| `caa_applicability` | CAA를 계산할 수 없는 샘플과 사유 |
| `grid_coverage` | 크기 격자별 CA 관측 비율 |
| `warnings` | 로딩·변환 중 경고 전체 |

### 4.2 학습 기록

- `split.json`: 학습/평가로 간 `sample_id`와 그룹 라벨. 같은 그룹이 양쪽에 섞이지 않는다.
- `train_history.csv`: 에폭별 `total`과 입력별(`layout`, `hotspot`, `ca`, `fab`, `geometry`) 손실.
- `run_record.json`: seed, 전체 설정, 장치, 라이브러리 버전, 합성 데이터 여부.

### 4.3 특징 공간 (`embeddings.npz`)

| 공간 | 만든 방법 | 위험 수치 재사용 |
|---|---|---|
| `shape_only` | layout 요약·실제 크기·두께·형상 수치 → 표준화 → PCA | 아니오 |
| `with_risk_baseline` | 위 특징 + hotspot 요약·CA 곡선·Fab 분포 → PCA | 예 |
| `with_risk_ae` | 오토인코더 결합 특징 z | 예 |
| `ae_struct`, `ae_fab` | 오토인코더 구조 측·Fab 측 특징(인과적 분리는 아님) | 예 |

```python
import numpy as np, pandas as pd
z = np.load("outputs/demo/embeddings.npz")
meta = pd.read_csv("outputs/demo/embedding_meta.csv")   # 행 순서가 z의 행 순서와 같다
print(z.files, z["shape_only"].shape)
```

### 4.4 `analyze` 결과

- `ranking.csv` / `risk_table.csv`: 비교 그룹별 크기별 무차원 원 CA(`ca@크기`), 단위 면적당 위험 지표(`ca_area@크기`, `1/um^2`), `ca_max`, CAA와 각 사유 열. 순위는 불량 종류뿐 아니라 전극 역할과 layer가 같은 `comparison_group_id` 안에서만 계산한다.
- `rank_agreement.json`: 비교 그룹별 순위 상관(실제 Fab 대 기준 분포, 원 CA 대 면적 대비 CA).
- `neighbors.csv`: 공간(`space`)별 유사 사례. `reuses_risk_inputs=True`인 행은 위험 수치를 입력으로 쓴 공간의 결과이므로 독립적인 발견으로 보지 않는다.
- `clusters.json`, `cluster_map.png`: 학습 행으로 적합한 군집과 대표·반대 사례. 군집 번호에는 의미가 없다. PNG는 PC1/PC2 산점도(채운 점=학습, 테두리=평가)다.
- `loo_stability.csv`: 제품을 하나씩 뺐을 때 상위 항목(`top_items_jaccard`)과 형상 공간 이웃(`neighbor_jaccard`)이 얼마나 유지되는지(1에 가까울수록 안정).
- `shape_features.csv`: 형상 특징 원표(누락은 빈 값).
- `review_cards.json`: 검토 카드. 불량 종류·전극 역할·layer 조합마다 한 장이다.

| 카드 필드 | 내용 |
|---|---|
| `products`, `n_comparable_products`, `status` | 포함된 제품과 비교 가능 여부 |
| `representative_cases`, `counter_cases` | 대표 사례와 반대 사례 |
| `repeated_shape_features` | 그룹 전체보다 위험이 높은 사례끼리 변동이 작은 형상 특징(기술적 요약) |
| `risk_comparison`, `size_correction` | CA·CAA 비교, 면적 보정 전후 순위 변화 |
| `retrieval` | 공간별 유사 사례(형상 전용과 위험 포함을 구분) |
| `available_conditions`, `unavailable_conditions` | 적용 가능한 조건과 자료가 없어 비교할 수 없는 조건 |
| `expert_review_required` | 전문가 확인·재계산이 필요한 항목 |

### 4.5 `evaluation.json`

- `trained_model_test_losses`: 평가 행의 입력별 복원 손실. 복원 오차는 해석 품질을 보장하지 않는다.
- `held_out_product_evidence`: 보지 않은 제품을 query로, 학습 제품만 후보로 검색한 형상 이웃과 면적 정규화 CA 경향 반복성. 전문가 정답표가 없으므로 정확도나 recall이 아니다.
- `ablation`: `image_only`, `numeric_only`, `full` 구성을 같은 seed로 학습한 평가 손실과, 형상 기준선 이웃과의 평균 Jaccard.
- `leave_one_group_out.fab_id` / `.product_family`: 그룹 제외 검증. `verdict`가 `평가 불가`면 그룹이 부족한 것이고, `효과 분리 불가`면 두 요인이 완전히 겹친 것이다.

## 5. 설정 바꾸기

설정 파일은 알 수 없는 키, NaN, 잘못된 타입을 모두 오류로 거부한다. 전체 항목과 설명은 [`configs/real.example.yaml`](../configs/real.example.yaml)의 주석에 있다.

자주 쓰는 변경:

```yaml
# 이미지 입력만으로 오토인코더 학습
model:
  enabled_inputs: [layout, hotspot]

# 특정 제품/설계 계열을 평가로 고정
split:
  test_groups: [P3, L4]

# 같은 Fab의, 실제 크기가 ±20% 안인 다른 제품 사례만 검색
analysis:
  other_products_only: true
  filters: {fab_id: [FAB_A]}
  size_tolerance: 0.2
  layer_policy: exact

# GPU/MPS가 있으면 사용
train:
  device: auto
```

설정을 바꾼 뒤에는 `train`부터 다시 실행한다. `embed`/`analyze`/`evaluate`는 `model.pt`에 저장된 학습/평가 분리를 사용하므로, 데이터나 분리 설정이 바뀌면 재학습이 필요하다.

## 6. 실제 자료로 실행하기

1. **자료 정리**: [`docs/domain-input-example`](domain-input-example/README.md)과 [`docs/data_contract.md`](data_contract.md)를 참고한다. 실제 Fab 자료는 `fab_measurements.csv`, vertical short 적층은 `layer_stack.csv`로 제공할 수 있다.
2. **설정 작성**: `configs/real.example.yaml`을 복사해 `data_dir`, `output_dir`, 크기 격자를 실제 값으로 바꾼다.
3. **점검 먼저**:
   ```bash
   uv run simcompare validate-data --config configs/real.yaml
   ```
   `validation.json`의 실제 크기 부재, 교락, 중복 의심, CAA 적용 불가 사유를 검토한다.
4. **학습·분석**:
   ```bash
   uv run simcompare train    --config configs/real.yaml
   uv run simcompare embed    --config configs/real.yaml
   uv run simcompare analyze  --config configs/real.yaml
   uv run simcompare evaluate --config configs/real.yaml
   ```

확정 전 항목의 현재 동작(`docs/open_questions.md` 참고):

| 항목 | real 모드 동작 |
|---|---|
| CAA 계산식 | `caa.rule: null`이면 CAA 열이 `CAA 계산 규칙이 설정되지 않았습니다` 사유와 함께 비어 있다. 확정 규칙을 `caa.interpret_fab`에 추가하고 `confirmed: true`로 설정해야 계산된다 |
| vertical short 적용 layer | 제품·버전별 `layer_stack.csv`에서 두 대상 중 상부 layer를 선택. 찾지 못하면 오류 |
| 색상 hotspot | `hotspot_legend`가 없으면 흑백 강도로만 사용 |
| 서로 다른 크기 구간 | 구간 중앙값과 CA 크기가 정확히 같은 점만 사용 |
| 제품 간 layer 대응 | 현재 보류. 이름이 같은 layer끼리만 비교 |

## 7. 오류와 해결

오류는 `오류: ...` 형식으로 stderr에 출력되고 종료 코드는 2다.

| 메시지 예 | 원인 | 해결 |
|---|---|---|
| `설정 파일을 찾을 수 없습니다` | 경로 오타 | `--config` 경로 확인 |
| `알 수 없는 설정 키 'model.foo'` | 오타 또는 없는 항목 | `configs/real.example.yaml`과 비교 |
| `mode 'real'에서는 'demo_'로 시작하는 caa.rule을 사용할 수 없습니다` | 실제 모드에 demo 규칙 사용 | `caa.rule: null` 또는 확정 규칙 사용 |
| `bundles.csv 파일이 없습니다` / `필수 열 누락` | 자료 형식 불일치 | `docs/data_contract.md`의 필수 파일·열 확인 |
| `부속 기록에 알 수 없는 sample_id` | CSV 간 ID 불일치 | `bundles.csv`와 부속 CSV의 `sample_id` 대조 |
| `layer_stack.csv에서 ... 적층 순서를 찾을 수 없습니다` | vertical short 상부 layer 판단 불가 | 제품·버전별 적층표에 두 대상 layer 추가 |
| `학습/평가 분리 실패: 분리 불가: 독립 그룹(제품·설계 계열)이 2개 미만입니다` | 제품·계열이 너무 적음 | 비교할 제품을 늘리거나 `split.test_groups` 확인 |
| `.../model.pt이(가) 없습니다. 먼저 'simcompare train'을 실행하세요` | 학습 전 분석 실행 | `train` 먼저 실행 |
| `체크포인트의 샘플이 현재 데이터에 없습니다(재학습 필요)` | 학습 후 데이터가 바뀜 | `train` 다시 실행 |
| `오래된 체크포인트` | 입력 파일 또는 단위·격자·모델·분리 설정 변경 | `train` 다시 실행 |
| `device 'cuda'를 요청했지만 CUDA를 사용할 수 없습니다` | 가속기 없음 | `train.device: cpu` 또는 `auto` |

## 8. 테스트

```bash
uv run pytest                                  # 전체(단위·계약·CPU 소규모 전체 실행)
uv run pytest tests/test_cli_e2e.py            # CLI 전체 흐름만
uv run pytest tests/test_caa_contract.py -q    # 특정 모듈 계약
```
