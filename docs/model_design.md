# legacy / archived 모델 설계 기록의 상태

이 저장소의 AE/PCA 모델, 학습, 임베딩, retrieval, CAA placeholder와 모델 평가 경로는 T7에서 폐기되었다. 남은 docs/arch와 walkthrough의 모델 설명은 legacy / archived 기록이며 현재 기능이나 수락 증거로 사용하지 않는다.

현재 simcompare는 numeric_v2 원 binary mask의 line / pixel, pair shared, transfer, failure pieces 및 short-only electrode count를 계산한다. CLI는 학습을 호출하지 않는다. 학습·검색은 별도 DL1/DL2 역할이며 이 저장소의 risk 입력 모델을 복사하지 않는다. ROI exporter는 사용자 위치와 원 shape/CNT/valid 연결을 전달하며 hotspot/CA는 모델 입력으로 내보내지 않는다.

현재 규칙은 [SPEC](../SPEC.md), [데이터 계약](data_contract.md), [review ROI 계약](review_roi_contract.md)에 있다. 실제 단위·한-unit 환산 및 crop 정책의 미결 상태는 [질문 상태](open_questions.md)에 기록한다.

## T7 폐기·보존 검산 기록 (2026-10-08)

기준 commit a3001fc, branch rewrite/line-pixel-rate. coordinator가 추가 승인한 쓰기 범위는 tests/test_ca_unit_contract.py와 tests/test_merge_exports.py다. 변경은 작업 트리에 남기며 commit하지 않는다. 아래 검산은 구현자 시험이며 독립 R2 PASS를 대신하지 않는다.

### 삭제 시험과 담당 모듈

| 삭제 시험 파일 | 폐기 기능 |
|---|---|
| tests/test_analysis_cards.py | analysis/cards와 기존 pipeline 순위·카드/held-out 평가 |
| tests/test_caa_contract.py | caa의 placeholder 가중·분포 선택 |
| tests/test_dataset_baseline.py | dataset encoding과 baseline PCA |
| tests/test_losses_contract.py | losses의 AE 손실 |
| tests/test_model_contract.py | model의 risk-input AE |
| tests/test_pair_neighborhood.py | pair_geometry/neighborhood의 변경 위치·이웃 비교 |
| tests/test_retrieval_contract.py | retrieval의 embedding 검색 |
| tests/test_plots.py | plots의 기존 순위/CAA 그림 |
| tests/test_split.py, tests/test_split_contract.py | split의 기존 모델 train/test 분리 |
| tests/test_train.py | train의 학습·checkpoint |
| tests/test_variants_merge.py | variants의 자동 edit/variant 제안; 유지할 merge 두 회귀 시험은 tests/test_merge_exports.py로 assertion 변경 없이 이동 |
| tests/test_config_contract.py | 폐기된 AE/CAA/train 설정을 읽는 config |

유지된 파일에서 제거한 부분: test_cli_e2e의 학습/checkpoint 시험은 폐기 CLI 거부·numeric E2E로 대체했다. test_synthetic의 legacy 21-bundle generator 시험 두 개는 generator 폐기에 따라 제거하고 numeric deterministic 시험을 보존했다. test_domain_rules의 ranking/held-out/retrieval/CAA 시험 여섯 개는 해당 모듈 폐기에 따라 제거하고 원 export 단위·registration·raw source-step 시험은 보존했다. test_review_findings의 AE 설정/legend 설정 및 all 학습 시험은 config/all 폐기에 따라 제거하고 loader의 CA 열·범례 오류 시험은 보존했다.

test_ca_unit_contract에서 risk_table/encode_bundles/compute_caa/_order_value 소비자 assertion은 해당 모듈 폐기로 제거했다. CA unit-family, nm²/um² 변환, 면적 정규화, mixed-family 오류와 pre-fix export 경고 검사는 옛 demo 의존을 수작업 legacy_export_dir fixture로 바꿔 유지했다. test_adapter_example_contract의 whitespace normalization 시험은 loader assertion을 모두 유지하되 폐기된 select_distribution assertion 하나만 제거했다. header-only/blank-id/non-numeric 시험은 옛 demo generator 대신 같은 작은 fixture로 유지했다.

test_numeric_cli 변경 기대: demo-data가 bundles.csv를 만들던 legacy 기대는 reference/manifest.json 존재 및 bundles.csv 부재로 변경했다. 기존 training monkeypatch guard는 함수 삭제를 확인한 뒤 임시 forbidden stub을 주입하는 방식으로 강화했다. 수치 기대 fixture와 line/pixel/transfer/pieces/map 계산은 변경하지 않았다.

### 여전히 적용되는 입력 불변식

| 불변식 | 현재 numeric 시험 |
|---|---|
| PNG 및 NPY bool/0-1/integer 0-255 동일 면적 | tests/test_numeric_images.py |
| 상대 경로·절대 경로·symlink 탈출 거부 | tests/test_numeric_images.py, tests/test_numeric_inputs.py |
| 단위·중복 converted diameter·duplicate case/diameter | tests/test_numeric_schema.py, tests/test_numeric_inputs.py |
| missing / observed zero / height evidence N/A 구별 | tests/test_numeric_inputs.py, tests/test_measurement.py, tests/test_failure_pieces.py |
| pitch 0.5 um × active 2 cells = 0.5 um² | tests/test_measurement.py::test_original_full_area_pitch_half |
| AB/AC/BA/BA duplicate → count 3, layer maximum | tests/test_electrode_map.py::test_canonical_duplicate_pairs_distinct_electrodes_and_layer_maximum |

### 조건부 삭제 결정과 참조 근거

- src/simcompare/config.py와 dedicated tests/test_config_contract.py: 삭제. 정리 후 `rg 'simcompare.config|from .*config import' src tests`는 결과 없음(exit 1).
- src/simcompare/adapters/example.py와 dedicated tests/test_adapter_example_contract.py: 유지. `rg 'from simcompare.adapters.example import' tests/test_particle_sim_export_integration.py tests/test_adapter_example_contract.py`는 양쪽의 load_dataset import를 확인한다. 원 export source/ID/CA 검산용이며 numeric CLI에서 쓰지 않는다.
- src/simcompare/preprocess.py와 dedicated tests/test_preprocess_contract.py 및 기존 tests/test_preprocess.py: 유지. `rg 'simcompare.preprocess' src/simcompare/adapters/example.py` → `from simcompare.preprocess import legend_to_values`.
- schema.py, merge.py, adapters/particle_sim_run.py: 원 source/ID 형식 보존. pandas는 kept particle_sim_run의 parquet/DataFrame reader가 직접 사용하므로 유지했다. torch/scikit-learn과 전이 scipy 등을 lock에서 제거했다. numeric PNG는 직접 의존 Pillow만 사용한다.

### 실행 증거

```bash
.venv/bin/python -m pytest -p no:warnings tests/test_retired_paths.py tests/test_numeric_cli.py tests/test_numeric_inputs.py
```

56 passed in 4.96s. help의 명령 집합은 demo-data, validate-data, compare, electrode-map, export-review-rois, merge-exports뿐이며 retired 다섯 명령은 subprocess에서 각각 exit 2와 명시 오류를 확인했다.

```bash
uv venv /tmp/simcompare-t7-no-torch --python .venv/bin/python
uv pip install --python /tmp/simcompare-t7-no-torch/bin/python --group dev -e .
/tmp/simcompare-t7-no-torch/bin/python -c "import importlib.util; assert importlib.util.find_spec('torch') is None; assert importlib.util.find_spec('sklearn') is None; assert importlib.util.find_spec('scipy') is None; print('torch/sklearn/scipy absent')"
/tmp/simcompare-t7-no-torch/bin/python -m pytest -p no:warnings tests/test_numeric_schema.py tests/test_numeric_images.py tests/test_measurement.py tests/test_linepixel.py tests/test_electrode_map.py tests/test_failure_pieces.py tests/test_roi_export.py tests/test_numeric_cli.py
/tmp/simcompare-t7-no-torch/bin/python -m pytest -p no:warnings tests/test_measurement.py::test_original_full_area_pitch_half tests/test_electrode_map.py::test_canonical_duplicate_pairs_distinct_electrodes_and_layer_maximum
/tmp/simcompare-t7-no-torch/bin/python -m simcompare demo-data --out /tmp/simcompare-t7-demo
/tmp/simcompare-t7-no-torch/bin/python -m simcompare compare --config /tmp/simcompare-t7-demo/config.yaml
/tmp/simcompare-t7-no-torch/bin/python -m simcompare electrode-map --config /tmp/simcompare-t7-demo/config.yaml
```

외부 새 환경은 선언된 runtime dependencies와 dev pytest만 설치했다. torch/sklearn/scipy absent, numeric 195 passed in 11.52s, 두 회귀 반례 2 passed in 0.04s. demo-data/compare/electrode-map 각각 exit 0; compare와 map은 available/reasons=[]이며 JSON/CSV/HTML 및 두 heat-map PNG/sidecar를 생성했다. reference와 target의 다른 grid를 지원하는 hand-authored E2E 기대값도 위 시험에서 통과했다.

retired source import, old shell train 호출, 현재 README/SPEC/usage/data_contract/model_design/open_questions의 retired arch/walkthrough 링크는 rg 결과 없음이다. 남아 있는 역사 페이지는 위 문서에서 legacy / archived로 명시하고 현재 설명에서 연결하지 않는다. git diff --check도 통과했다.

### 전체 suite

```bash
.venv/bin/python -m pytest -p no:warnings
# optional reader도 검산하려고 현재 시험 환경에 명시 설치:
uv pip install --python .venv/bin/python pyarrow zarr
.venv/bin/python -m pytest -p no:warnings
```

첫 실행: 410 passed, 56 skipped in 14.12s (optional parquet/zarr 또는 외부 exporter 환경 부재). optional reader 설치 뒤 최종 실행: **464 passed, 2 skipped in 19.83s**. 남은 두 skip은 PARTICLE_SIM_ROOT/PARTICLE_SIM_PYTHON을 요구하는 기존 외부 exporter integration이며 외부 저장소나 사용자 데이터에는 쓰지 않았다. outside-repo numeric 새 환경에는 이 optional packages를 설치하지 않았다.

### 삭제 파일 전체

- docs/arch/analysis.md
- docs/arch/baseline.md
- docs/arch/caa.md
- docs/arch/cards.md
- docs/arch/dataset.md
- docs/arch/losses.md
- docs/arch/model.md
- docs/arch/neighborhood.md
- docs/arch/pair_geometry.md
- docs/arch/plots.md
- docs/arch/retrieval.md
- docs/arch/split.md
- docs/arch/train.md
- docs/arch/variants.md
- src/simcompare/analysis.py
- src/simcompare/baseline.py
- src/simcompare/caa.py
- src/simcompare/cards.py
- src/simcompare/checks.py
- src/simcompare/config.py
- src/simcompare/dataset.py
- src/simcompare/losses.py
- src/simcompare/model.py
- src/simcompare/neighborhood.py
- src/simcompare/pair_geometry.py
- src/simcompare/plots.py
- src/simcompare/retrieval.py
- src/simcompare/split.py
- src/simcompare/train.py
- src/simcompare/variants.py
- tests/test_analysis_cards.py
- tests/test_caa_contract.py
- tests/test_config_contract.py
- tests/test_dataset_baseline.py
- tests/test_losses_contract.py
- tests/test_model_contract.py
- tests/test_pair_neighborhood.py
- tests/test_plots.py
- tests/test_retrieval_contract.py
- tests/test_split.py
- tests/test_split_contract.py
- tests/test_train.py
- tests/test_variants_merge.py

### 수정·추가 파일 전체

- README.md
- SPEC.md
- configs/demo.yaml
- configs/real.example.yaml
- docs/data_contract.md
- docs/model_design.md
- docs/open_questions.md
- docs/usage.md
- pyproject.toml
- scripts/1_install.sh
- scripts/2_demo.sh
- src/simcompare/cli.py
- src/simcompare/pipeline.py
- src/simcompare/synthetic.py
- tests/conftest.py
- tests/test_adapter_example_contract.py
- tests/test_ca_unit_contract.py
- tests/test_cli_e2e.py
- tests/test_domain_rules.py
- tests/test_merge_exports.py
- tests/test_numeric_cli.py
- tests/test_retired_paths.py
- tests/test_review_findings.py
- tests/test_synthetic.py
- uv.lock
