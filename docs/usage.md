# 실행 가이드

이 문서는 simcompare를 설치하고, 합성 데이터로 전체 흐름을 실행하고, 결과를 읽고, 실제 자료로 바꿔 실행하는 방법을 순서대로 설명한다.

> simcompare는 **이미 계산된** 시뮬레이션 결과를 제품 간에 비교·해석하는 도구다. CA나 hotspot을 예측하지 않고, 인과관계나 수율을 판정하지 않는다. 합성 데이터 결과는 코드 동작 확인용이며 연구 근거가 아니다.

> 명령을 하나씩 입력하지 않고 파일만 실행하려면 [README의 시작하기 표](../README.md#시작하기-이-순서대로-따라-하기)를 따른다: `scripts/1_install.sh` → `scripts/2_demo.sh` → `scripts/3_check_my_data.sh` → `scripts/4_analyze_my_data.sh`. 이 문서는 그 파일들이 내부에서 실행하는 명령과 결과 파일을 자세히 설명한다.

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
| `analyze` | 검색·순위·군집·안정성·검토 카드 | `ranking.csv`, `neighbors.csv`, `clusters.json`, `cluster_map.png`, `loo_stability.csv`, `review_cards.json`, `figures/*.svg` 등 | `review_cards.json`, `figures/rank_shift.svg` |
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
- `figures/` (SVG, 브라우저로 열기): 표를 그림으로 옮긴 것이며 새 판단을 더하지 않는다. 비교 그룹마다 한 줄씩 쌓인다.
  - `rank_shift.svg`: ① 원 CA → 면적당 CA, ② 기준 분포 CAA → 실제 Fab CAA 순위 변화. 가장 크게 바뀐 두 사례만 색으로 표시하고, 한쪽 값이 없는 사례는 `*`로 표시한다.
  - `ca_curves.svg`: 이물 크기별 원 CA 곡선(양쪽 로그 눈금). 원 CA 최댓값이 가장 큰 사례를 강조하고, 빠진 값이나 0 이하 값은 채우지 않고 선을 끊는다.
  - `loo_stability.svg`: `loo_stability.csv`의 두 유지율을 제품별 막대로 표시한다.
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
2. **설정 작성**: `configs/real.example.yaml`을 복사해 `data_dir`, `output_dir`를 실제 값으로 바꾼다. 크기 격자는 확정: 1–30 um 1 um 간격 + 40, 50 um (32개).
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
| vertical short 적용 layer | 1.2는 source-step Fab layer 유지, 적층표는 geometry 검증. Legacy(미표시/1.1)는 상부 Fab layer 선택 |
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

## 9. particle-sim 원 실행 결과 점검

```bash
bash scripts/5_check_particle_sim_run.sh "../particle-sim/run" --output /tmp/run-report.json
bash scripts/5_check_particle_sim_run.sh RUN_A RUN_B --ca mean --strict
```

여러 run 경로와 공백이 있는 경로를 받으며 상대 경로는 호출한 디렉터리를 기준으로 해석한다.
Python 진입점은 `simcompare.adapters.particle_sim_run.main(argv)`이다.
이 점검기는 `run_manifest.json`, `ca.parquet`, `hotspots.zarr` 또는 `probability.zarr`를 읽고
입력 파일을 수정하지 않는다. schema 1.0/2.0, CA 정규화, 중복 키, unit 정체성, zarr 면적과
simcompare 묶음 계약을 검사한다. 모든 크기가 `not_applicable_source_step`인 (공정, unit)은
건너뛰고, 같은 (공정, unit)의 크기별 classification이 다르면 오류다.

| 옵션 | 의미 |
|---|---|
| `--ca nominal\|mean` | 기본은 정규화 nominal CA; mean은 확률 run의 `ca_mean_um2 / ROI 면적` |
| `--mode real\|demo` | 계약 검사 모드, 기본 real |
| `--product-config manifest.yaml` | 제품/버전 대조, 전극 역할, 제품군, 설계 계열, Fab ID 및 vertical short 상부 layer 검증 |
| `--product-family`, `--design-lineage-id`, `--fab-id` | 메타데이터 직접 지정 |
| `--mapping mapping.yaml` | 기본 unit 대응표를 진단 목적으로 덮어쓰기 |
| `--output report.json` | verdict, mapping, run별 findings/units/bundles를 JSON으로 저장 |
| `--strict` | 미지원 unit이 하나라도 있으면 실패 |

종료 코드는 0(통과), 1(무결성/계약 오류 또는 strict 미지원), 2(사용법/읽을 수 없는 입력)다.
전극쌍 lateral/vertical short와 명시/자동 open은 대응한다. Open의 b 역할/layer는 `N/A`다.
무결성 오류가 있으면 대응 단계 전에 보고를 중단하므로 JSON의 `units`와 `bundles`는 비어 있고,
`--strict`의 미지원 unit 집계도 제공되지 않는다(종료 코드 1은 유지).
알 수 없는 `(unit_kind, failure_mode)`는 이 점검기에서 경고로 보고하지만 exporter는 거부한다.
Open의 `baseline_open`, `terminal_lost`, `insufficient_terminals` classification은 (공정, unit)마다
경고를 내고 묶음은 유지한다. 이때 CA 0은 제외된 baseline 조건을 뜻하며 낮은 위험을 뜻하지 않는다.
CNO(`contact_not_open`)는 지원하지 않으며 기본 실행에서는 경고, `--strict`에서는 실패한다.
`--mapping` YAML은 다음처럼 항목 목록 또는 `mappings` 목록을 받는다.

```yaml
- unit_kind: electrode_pair
  failure_mode: lateral_short
  failure_type: lateral_short
  note: diagnostic override
```

대상은 현재 `schema.FAILURE_TYPES`에 포함되어야 한다(`null`은 미지원). 이 옵션은 exporter를
확장하거나 CNO 지원을 추가하지 않는다. 기본 대응표도 바꾸지 않는다. 샘플 ID는
`product-version-step-unit`의 허용 문자 `[A-Za-z0-9_.-]` 밖 문자 각각을 `_`로 바꾼다.
서로 다른 전체 raw tuple이 같은 ID로 변환되면 오류이며, 다른 run에서 동일한 raw tuple의
ID를 다시 사용하면 경고다. 이 경고가 있는 자료를 중복 ID를 가진 하나의 데이터셋으로 합치면 안 된다.

점검 통과가 분석용 `data_dir` 준비 완료를 뜻하지 않는다. 원 run에는 layout 연결과 실제 Fab
입력이 없으며, `adapters.example.load_dataset`은 exporter의 CSV/NPY 데이터 디렉터리를 받는다.
particle-sim export는 nominal run만 지원하고 CNO를 경고와 함께 제외한다. 실제 Fab 데이터가
없으면 export를 그대로 load할 수 없다(`fab.json` 또는 `fab_measurements.csv` 필요).
테스트의 synthetic Fab은 임시 디렉터리에서 adapter 연결만 확인하며 실제 분석 근거가 아니다.

2026-09-27 계약 1.2: checker의 `--mapping-contract-version {1.1,1.2}` 기본값은 1.2다.
이는 오래된 run도 source-step Fab assignment로 해석하는 선택이며 생성 당시 계약을 주장하지 않는다.
CA의 source Fab label은 step 내 모든 행에서 nonblank·uniform이어야 하고 설정의 source step과 일치해야 한다.
Vertical geometry는 `stack`과 `electrodes`로 검증하고 물리적 upper layer를 별도 보고한다.
`assigned_process_layer`는 source-step label을 유지하며 upper와 달라도 정상이다.
설정이 없으면 geometry/config-unverified 경고로 제한된 검증을 알리고 assignment/key는 real 검증한다.
**Legacy 1.1**: upper physical layer를 assignment로 사용하고 source와 차이는 경고한다.
오래된 CSV export는 미표시/1.1 marker라면 기존 상부 Fab layer 규칙으로 계속 읽는다.

Parquet/zarr reader는 선택 의존성이다. wrapper는 `uv --with pyarrow --with zarr`로 실행에만
추가하고 프로젝트 의존성 파일을 바꾸지 않는다. Reader 없이 parquet은 입력 오류이며,
zarr reader/저장소 부재는 면적 및 hotspot 검사를 생략했다는 경고를 낸다.

```bash
# 기본 환경: reader 테스트는 skip, CLI/wrapper 테스트는 실행
UV_NO_SYNC=1 uv run --project . pytest -q -rs
# 임시 reader 환경: 생성한 작은 parquet/zarr fixture 검사
UV_NO_SYNC=1 uv run --project . --with pyarrow --with zarr pytest -q -rs tests/test_particle_sim_run.py tests/test_particle_sim_run_cli.py
# 두 저장소 연결 검사: 환경변수가 없으면 skip
PARTICLE_SIM_ROOT=/path/to/particle-sim PARTICLE_SIM_PYTHON=/path/to/particle-sim/.venv/bin/python \
  UV_NO_SYNC=1 uv run --project . --with pyarrow --with zarr pytest -q -rs tests/test_particle_sim_export_integration.py
```

두 저장소의 `tests/fixtures/particle_sim_mapping_v1.json`은 동일한 버전 계약이다. Mapping 변경은
양쪽 구현/fixture 및 exporter→adapter 연결 검사를 함께 갱신해야 한다.

## Fab 매칭 계약 1.2 (2026-09-27)

`provenance.json.mapping_contract_version: "1.2"`를 선언하고 모든 bundle에 nonblank
`assigned_process_layer`(source-step Fab label)와 `fab_pair_key`를 제공한다.
키는 raw ordered `<unit>:<layer_a>-<layer_b>`이며 open은 `<unit>:<layer_a>-N/A`다.
문장 부호/대소문자/Unicode를 보존하고 endpoint를 정렬하거나 delimiter로 분해하지 않는다.
Particle-sim identity map이 있으면 raw tuple/key/assignment를 모두 대조한다.
미표시/1.1은 populated pair key 없이 legacy 규칙을 유지한다. Unknown marker 및 부분 migration은 오류다.
전환할 때 새 export 디렉터리를 만들고 derived cache/model을 다시 생성한다.

Fab JSON/measurement CSV의 `fab_pair_key`는 선택 필드다. null/blank는 layer-wide,
nonblank는 pair refinement이며 `*` wildcard는 없다. CSV source_id 안에서는 blank와 nonblank도 섞을 수 없다.
모든 mode에서 자동 선택은 family+layer-wide → null-family+layer-wide 순서다.
Pair-specific 분포는 자동 선택에 참여하지 않으며 명시적인 fab_distribution_id로만 선택한다.
첫 nonempty tier의 하나만 선택하며 중복/무매칭은 오류다. 절대로 sum/merge하지 않는다.
명시 distribution ID는 자동 선택보다 우선하며 1.2에서 Fab/layer/family/pair/reference를 검증한다.
Legacy 자동 선택은 layer-wide만 고려하며 pair-specific explicit ID도 거부한다.
1.2 reference distribution에는 pair key가 없어야 하며 source assignment와 다른 override는 오류다.
선택된 분포의 누락 bin은 layer-wide fallback을 유발하지 않는다.

Pair ID는 product-local이다. Pair-specific 측정은 항상 검증된 explicit distribution ID로 선택한다.
같은 key가 여러 step에서 반복되어도 sample 행은 별도이며 retrieval/grouping은 기존 geometry를 유지한다.

CA에 source Fab label이 없는 legacy run은 checker에 `--mapping-contract-version 1.1`을 지정한다.
