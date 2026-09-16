# simcompare — 디스플레이 이물 시뮬레이션 결과 제품 간 비교·해석

이미 수행한 이물 시뮬레이션 결과(layout, hotspot, CA)와 Fab 가공 요약자료를 입력으로 받아 다음을 지원하는 Python CLI다.

- 여러 제품에서 반복되는 위험 유형 탐색(군집·대표/반대 사례)
- 전극 역할·layer·불량 종류 조건을 지키는 유사 사례 검색
- 같은 전극 역할·layer 비교 그룹 안에서 원 CA, 면적 보정 CA, Fab 분포 순위 비교
- 설계자가 개선 가설을 검토할 수 있는 근거 카드(review card)

**하지 않는 것**: CA·hotspot 예측, layout 자동 수정, 전기적 성능 예측, 인과관계 확정, 수율 계산. CAA는 파생 지표이며 실제 불량률·수율이 아니다.

> 현재 저장소에서 확인한 것은 **합성 데이터 기반 코드 동작**뿐이다. 실제 데이터 분석이나 연구 성능 검증은 수행하지 않았다.

> 설치부터 결과 해석, 실제 자료 연결, 오류 해결까지의 자세한 절차는 [docs/usage.md](docs/usage.md)(실행 가이드)에 있다.
>
> 처음이라면 [docs/walkthrough.pdf](docs/walkthrough.pdf)(따라하기)부터 보면 된다. 더미 데이터 생성부터 평가까지 실제로 실행한 결과를 그림과 쉬운 설명으로 정리했다.

## 설치

Python 3.11+와 [uv](https://docs.astral.sh/uv/)가 필요하다. 외부 API·모델 다운로드·웹 서비스 없이 CPU에서 동작한다.

```bash
uv sync                 # numpy, pandas, scikit-learn, torch, pillow, pyyaml + pytest(dev)
uv run pytest           # 단위·계약·CPU 소규모 E2E 테스트
```

## 빠른 실행 (합성 데이터)

```bash
uv run simcompare demo-data --out demo_data          # 합성 데이터 생성 (물리 시뮬레이션 결과 아님)
uv run simcompare validate-data --config configs/demo.yaml
uv run simcompare train    --config configs/demo.yaml
uv run simcompare embed    --config configs/demo.yaml
uv run simcompare analyze  --config configs/demo.yaml
uv run simcompare evaluate --config configs/demo.yaml
# 또는 한 번에 (--demo-out에 생성한 데이터로 설정의 data_dir을 대체)
uv run simcompare all --config configs/demo.yaml --demo-out demo_data
```

`configs/demo.yaml`은 `mode: demo`와 demo CAA 규칙을 사용한다. 경로는 YAML 파일 위치 기준 상대경로다(`../demo_data`, `../outputs/demo`). demo 모드의 모든 명령은 `DEMO 모드: 합성/예시 설정이며 결과는 연구 근거가 아닙니다` 안내를 stderr에 출력한다.

## 출력 언어

오류·경고·안내 문구, 검토 카드와 평가 결과의 설명, CLI 도움말은 한국어로 출력한다. 다른 프로그램이 읽을 수 있도록 JSON 키, CSV 열 이름, `lateral_open` 같은 열거값과 ID는 영문을 유지한다. `cluster_map.png`의 그림 안 글자는 Pillow 기본 글꼴이 한글을 지원하지 않아 `PC1/PC2`만 표시하고, 설명은 `clusters.json`에 있다. argparse가 자체 생성하는 `usage:` 줄과 인자 누락 문구(`the following arguments are required`)는 영문으로 남는다.

## 산출물 (`output_dir`)

| 명령 | 파일 | 내용 |
|---|---|---|
| validate-data | `validation.json` | 입력 누락 수, 실제 크기 부재 샘플, hotspot–layout 중복 의심, Fab·제품군 교락, CAA 적용 불가 사유, 크기 격자 관측률 |
| train | `model.pt`, `train_history.csv`, `split.json`, `run_record.json` | checkpoint(입력·학습 계약 fingerprint, 설정·seed·학습/평가 ID·버전), 입력별 손실 로그, 그룹 분리 결과 |
| embed | `embeddings.npz`, `embedding_meta.csv` | `shape_only`, `with_risk_baseline`(PCA), `with_risk_ae`, `ae_struct`, `ae_fab` |
| analyze | `neighbors.csv` | 공간별 유사 사례. `reuses_risk_inputs=True`인 공간은 독립 발견이 아님 |
| | `risk_table.csv`, `ranking.csv`, `rank_agreement.json` | 원 CA, CA/면적, demo CAA(실제 Fab / 기준 분포), 순위 변화 |
| | `clusters.json`, `cluster_map.png` | 학습 행으로 적합한 군집, 대표·반대 사례(군집 번호는 의미 없음) |
| | `loo_stability.csv` | 제품 하나씩 제외 시 상위 항목·이웃 유지율 |
| | `shape_features.csv`, `review_cards.json` | 명시적 형상 특징, 검토 카드 |
| evaluate | `evaluation.json` | held-out 제품→학습 제품 형상 검색·면적 보정 CA 경향, 입력별 복원 손실, 입력 구성 비교, Fab·제품군 그룹 제외 검증과 교락 판정 |

## 실제 데이터 연결

1. **어댑터 작성**: `src/simcompare/adapters/example.py`를 참고해 원본 형식을 `AnalysisBundle`/`FabDistribution`(`src/simcompare/schema.py`)으로 바꾸는 `load_dataset`을 만든다. 형식이 같다면 예시 어댑터를 그대로 쓴다. 파일 형식과 규칙은 [docs/data_contract.md](docs/data_contract.md)에 있다.
2. **설정 작성**: `configs/real.example.yaml`을 복사해 `data.data_dir`, `ca_grid`, `hotspot_sizes`, `fab_grid`를 실제 격자로 맞춘다.
3. **도메인 입력 작성**: [docs/domain-input-example](docs/domain-input-example/README.md)을 참고해 `fab_measurements.csv`와 `layer_stack.csv`를 작성한다. CAA 규칙을 확정해 `caa.interpret_fab`에 추가하고 `caa.confirmed: true`로 명시하기 전까지 CAA 값은 사유와 함께 비어 있다.
4. **범례가 있는 색상 hotspot**: `data.hotspot_legend`를 설정한 경우에만 색을 확률로 변환한다.
5. `validate-data` 결과의 경고(실제 크기 부재, 교락, 중복 의심)를 먼저 검토한 뒤 학습·분석을 실행한다.

## 설정 요약

- `model.enabled_inputs`: `layout`, `hotspot`, `ca`, `fab`, `geometry` 중 선택
- `model.conv_channels`, `modality_dim`, `latent_dim`, `loss_weights`
- `train.device`: `cpu`(기본) \| `auto`(CUDA→MPS→CPU) \| `cuda` \| `mps`; `train.seed`; `train.balance_products`
- `split.test_fraction` 또는 `split.test_groups`(제품/설계 계열 ID)
- `analysis.layer_policy`(`exact`\|`ignore`), `other_products_only`, `filters`(예: `{fab_id: [FAB_A]}`), `size_tolerance`(실제 크기 허용 비율), `k_neighbors`, `n_clusters`, `loo_top_k`
- `data.canonical_length_unit`: `um` 권장. 입력 `nm`/`um`/`mm`는 명시적으로 환산된다.

알 수 없는 설정 키, NaN, 잘못된 타입은 오류다.

## 문서

- [docs/walkthrough.pdf](docs/walkthrough.pdf): 따라하기(더미 데이터로 전체 흐름 실행, 결과 그림과 읽는 법). `uv run --with matplotlib python docs/walkthrough/build.py`로 다시 생성
- [docs/usage.md](docs/usage.md): 실행 가이드(단계별 실행, 결과 읽는 법, 설정 변경, 실제 자료 실행, 오류 해결)
- [docs/data_contract.md](docs/data_contract.md): 공통 형식, 예시, 단위·매칭 규칙
- [docs/model_design.md](docs/model_design.md): 모델 구조, 분석 목적, 한계
- [docs/open_questions.md](docs/open_questions.md): 확정 필요 사항과 현재 임시 처리
- [docs/references.md](docs/references.md): 참고 방법과 본 구현의 차이
- `docs/arch/*.md`: 모듈별 인터페이스 계약(구현 작업 단위 명세)

## 구현 방식 기록

이 저장소는 로컬 Mac mini 오케스트레이터(mmo)로 구현했다. 계약 문서와 공통 schema는 계획 단계에서 작성했고, 기본 모듈은 로컬 Qwen(OpenCode) 작업으로 생성한 뒤 Codex/Claude 독립 검토의 blocking finding을 반영했다. 통합 계층(dataset/train/baseline/synthetic/analysis/checks/cards/pipeline/cli)과 테스트 일부는 직접 작성했다. 자세한 작업 기록은 오케스트레이터 상태 DB에 보존되어 있다.
