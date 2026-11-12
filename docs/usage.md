# simcompare 실행 가이드

두 제품(reference, target)의 hotspot 결과를 가져와 line/pixel 면적을 비교하고, 설계자가 짝지은 위치의 국부 ΔCA를 계산한다. 환경에 따라 아래 둘 중 한 절만 따른다.

- **ETX (Linux 서버)**: 큰 자료. 통합 실행기 `.py` 파일 하나를 올려 설정 파일 두 개(`real.yaml`, `compare2.yaml`)로 돌린다.
- **Windows 폐쇄망 (CMD)**: 설치 ZIP으로 오프라인 설치한 뒤 명령을 직접 친다.

모든 명령의 옵션, 오류 코드, 출력 스키마, 내부 동작은 [상세 레퍼런스](usage_reference.md)에 있다.

## 6회차 흐름

| 회차 | 명령 | 입력 | 출력 | 다음 회차 전 수작업 |
|---|---|---|---|---|
| 0 (선택) | `dv-template` | 제품 case 폴더 이름, 임피던스 DB(선택) | `dv_sheet.csv`, `new_electrodes.csv` | 전압 칸과 새 전극 임피던스를 사용자가 채움. 1~6회차와 독립. [상세](dv_template.md) |
| 0 (선택) | `dv-grade` | 채운 `dv_sheet.csv`, 제품 case 폴더 이름, `--product-id` | `dv_grades.csv`, `dv_grades_by_case.csv`, `dv_grade_manifest.json` | short 쌍별 ΔV·최대 타이밍·등급(None/Low/High) 계산. 1~6회차와 독립. [상세](dv_grade.md) |
| 1 | `import-hotspots` | hotspot 폴더 `data/input/<모델>` | `data/imported/reference`, `target` | 없음(`pairs.csv`는 `data/input/pairs.csv` 한 곳에 두고 복사하지 않는다) |
| 2 | `validate-data`, `compare` | `real.yaml` | `comparison.json`, `comparison.csv`, `comparison_masks.zip` | 없음(통합 실행기에서는 3회차 뒤에 돈다) |
| 3 | `import-electrode-assets` | 전극 PNG, `real.yaml`(`--config real.yaml --side`) | `data/assets/reference`, `target` | 없음(출력을 복사하지 않는다. `real.yaml`의 `asset_manifest.path`·`electrode_import_record.path`가 그 폴더를 가리킨다) |
| 4 | `candidates-change` | `real.yaml`, DL1 PNG root | `data/changes`(`alignment.html`, `alignment.json`, `direct/roi_correspondence.json`, `*_export`) | 인수 예시 값을 제품에 맞게 확인(선택 인수 `--merge-scope`·`--suppress-overlap`·`--electrode-png-root` 포함). 로그의 `candidates-change exit N`과 `alignment.html` 확인 |
| 4b (선택) | `candidates-dv` | `real.yaml`(3회차 뒤), 0b회차 `dv-grade` 출력 둘 | `data/changes_dv`(`vulnerability_v1.json`, `grade_maps/`, `*_export`) | 인수 예시 값을 제품에 맞게 확인. 로그의 `candidates-dv exit N` 확인. 4회차와 독립. [상세](dv_candidates.md) |
| 4c (선택) | `vulnerability-report` | 4b회차 출력 폴더 | `data/vulnerability_report/report.html` | 4b회차와 같은 서버에서 이어서 실행. [상세](dv_candidates.md) |
| (DL2) | 선택 | `data/changes/*_export/review_rois.json` | `data/dl2/ncc_*.json`, `decisions.csv` | 다른 제품 탐색이 필요할 때만 |
| 5 | `confirm-pairs` (DL2 경로 전용) | DL2 결과, `decisions.csv` | `data/confirmed/roi_correspondence.json` | `--labelled-by`를 실제 ID로 |
| 6 | `compare-windows` | 직접 대응 `data/changes/direct/roi_correspondence.json` (DL2 경로는 `data/confirmed/…`) | `data/local/local_delta.html` 등 | 없음 |

통합 실행기의 단계 이름과 실행 순서는 01 import → 03 전극 asset → 02 `compare2` → diag(validate/compare) → 04 `candidates-change` → (05 `confirm-pairs`) → 06 `compare-windows` → 07 `compare2-report`다(아래 ETX 절). 이 표의 번호는 명령 순서 설명용이다. 명령 순서: 1 import → 2 validate/compare → 3 전극 asset → 4 `candidates-change` → (선택 DL2 → 5 `confirm-pairs`) → 6 `compare-windows`. 직접 대응을 쓰면 DL2와 5회차를 건너뛴다. 변경점 ROI(`candidates-change`)가 4회차 ROI 선정을 대체했다. ΔV 취약점 ROI는 선택 회차다: 0 `dv-template` → (시트 기입) → 0b `dv-grade`(제품마다) → 3회차 뒤 4b `candidates-dv` → 4c `vulnerability-report`. 예전 `candidates`(max_count, 비가중 취약점 ROI)는 참고용이다.

수치를 새로 만드는 것은 2회차(면적)와 6회차(창별 ΔCA)이고 나머지는 입력을 준비하고 짝을 고르는 단계다. 6회차 `compare-windows`는 reference가 a, target이 b이며 방향은 b − a다.

### 모델이 여럿일 때 (다제품 흐름 1m → 3m → 5m → 6m → 7, 선택)

reference·target 외에 비교할 모델(제품)이 더 있으면 `models.yaml`(`compare_models_v1`)에 적고 아래 한 흐름을 따른다. 기존 1~6회차의 코드 경로와 출력은 바뀌지 않는다(`--models`가 없으면 5회차는 그대로다). 예시 파일은 `configs/etx/models.example.yaml`, 회차별 실행기는 `bundles/etx/simcompare_job_models_01~05_*.py`다(통합 실행기와 따로 쓰는 회차별 파일이다).

| 회차 | 명령 | 하는 일 | 회차 뒤 할 일 |
|---|---|---|---|
| 1m | `import-hotspots --root data/input/<모델> --expected-cases-from-roots data/input/<target> --out data/models/<모델>` | 모델 hotspot import(모델마다 한 번) | 없음(`pairs.csv`는 `data/input/pairs.csv` 하나를 `models.yaml`이 가리킨다) |
| 3m (선택) | `import-electrode-assets … --config models.yaml --model <모델> --out data/assets/models/<모델>` | 모델 전극 PNG import | 없음(복사하지 않는다). `models.yaml`의 그 모델 절에 `asset_manifest: {path: data/assets/models/<모델>/review_assets.json}`, `electrode_import_record: {path: …/electrode_asset_import_record.json}`를 쓴다(sha256 생략 가능) |
| 5m | `confirm-pairs --list/--models …` | 5회차를 대신하는 모델 포함 확정(reference 쌍도 같은 출력에 나온다. DL2 `search_batch.json` 하나를 두 `--match`에 줄 수 있고 `--out`은 `data/confirmed`) | `data/confirmed/models/<모델>/roi_correspondence.json` 확인 |
| 6m | `compare-windows --models … --model <모델> --case-window-um … --case-diameter-um … --out data/local/models/<모델>` | 모델 쌍 창별 ΔCA + case 구성 | `models_04` 실행기의 첫 줄이 `data/confirmed/roi_correspondence.json`으로 reference 쌍 `data/local`을 둔다(`models_05` 입력). 통합 실행기 06 단계는 05 단계가 STEPS 에 있을 때만 이 대응을 쓰는데 05 단계는 `data/confirmed`를 보관(이동)하므로 이 흐름에서는 쓰지 않는다 |
| 7 | `model-report …` | 행 = target ROI, 열 = 모델 한 장 표 | `data/model_report/model_report.html` |

모델 자산은 사용자가 나중에 모델별 폴더로 준다. 폴더가 없는 모델은 `assets_pending`이라 후보만 보이고, 자산이 오면 1m(필요하면 3m)부터 해당 모델만 다시 하고 5m 이후는 새 `--out`으로 돌린다. 6m은 모델마다 한 번씩 돌리고 `--out`은 모델별 폴더다. 6m은 2회차 마스크를 재사용하지 않고 target·모델 두 쪽 면적을 다시 계산해 6회차보다 시간·메모리가 더 들고, `--models`는 `models.yaml`의 모든 configured 모델을 로드하며, `--case-*` 인수는 쌍×case 마다 마스크를 다시 읽는다. 실규모 시간은 로그의 `compare-windows exit N in Ns` 줄로 확인한다. 주의: 6회차가 `data/local`을 한 세대로 교체하므로(관리 파일 이름만 교체) `data/local/models/`는 지워지지 않지만, 모델 폴더가 reference 폴더 안에 있으므로 7회차는 두 폴더를 각각 `--windows`로 따로 준다.

- `models.yaml`은 `real.yaml`과 **같은 폴더**에 둔다(다르면 `models_config_dir_mismatch`, 종료 2). 모델 절의 상대 경로는 `real.yaml`의 reference/target 절과 같은 기준으로 읽는다. 모델 폴더는 reference와 같은 1회차 산출물(`manifest.json` 등)이며 `data/models/<product_id>/`에 둔다. 3m 출력은 `data/assets/models/<product_id>/`에 그대로 둔다(복사하지 않는다). 모델 절의 `condition_assignment`는 `pair_table_path: data/input/pairs.csv`와 `interlayer_distance_path: data/input/thk_info-<product_id>.txt`를 쓴다(아래 층간 거리 절).
- `data_dir/manifest.json`이 아직 없는 모델은 `assets_pending`이다. 후보는 `--list`에 `model_status`로 보이고, accept 하면 쌍 없이 `models/<product_id>/deferred.json`에 남고 종료 코드는 3이다. 자산이 오면 새 `--out`으로 다시 돌린다.
- 모델 후보는 target 질의(`target_to_reference`) ROI에서 나온 것만 쓴다. reference 질의의 모델 후보는 `candidate_model_query_not_target`이며 accept 하면 종료 2이다.
- 모델 쌍은 a = target 질의, b = 모델이다. 그래서 이 쌍으로 계산한 Δ는 모델 − target이다(reference 쌍은 target − reference).

```text
python -m simcompare confirm-pairs --list --config real.yaml --models models.yaml --candidates data/changes --match target_to_reference=data/dl2/search_batch.json
python -m simcompare confirm-pairs --config real.yaml --models models.yaml --candidates data/changes --match reference_to_target=data/dl2/search_batch.json --match target_to_reference=data/dl2/search_batch.json --decisions decisions.csv --labelled-by jdoe --mutual-tolerance-um 5 --out data/confirmed
```

모델 한 개의 창 비교는 `compare-windows`에 `--models`와 `--model`을 같이 준다. 두 인수와 `--out`이 모두 필요하고, a = config target, b = 모델이며 Δ = 모델 − target이다. `--out`은 reference 용 `data/local`과 다른 폴더(예 `data/local/models/<key>`)여야 하며, 인수 오류는 파일을 하나도 쓰지 않고 종료 2이다.

```text
python -m simcompare compare-windows --config real.yaml --models models.yaml --model QA4_C00 --correspondence data/confirmed/models/QA4_C00/roi_correspondence.json --window-sizes 20 --out data/local/models/QA4_C00
```

모델 쌍의 "창 안 관측 case 구성 차이"(둘 다 / target 에만 / 모델에만, 개수와 이름)는 같은 명령에 `--case-window-um`과 `--case-diameter-um`을 함께 주면 `window_cases.json`·`window_cases.csv`로 나온다. 창은 `--window-sizes` 안의 값이어야 하고 지름은 두 데이터셋에 있어야 하며 기본값은 없다. 목록·개수일 뿐 인과를 뜻하지 않는다([데이터 계약](data_contract.md)의 `window_cases_v1`).

```text
python -m simcompare compare-windows --config real.yaml --models models.yaml --model QA4_C00 --correspondence data/confirmed/models/QA4_C00/roi_correspondence.json --window-sizes 20 --case-window-um 20 --case-diameter-um 5 --out data/local/models/QA4_C00
```

모델별 한 장 표(행 = target 질의 ROI, 열 = 모델, 셀 = 쌍 목록과 불량 변화 Δ)는 `model-report`가 만든다. 모든 인수가 필수이고 수치는 기존 출력에서 복사하며, 입력 폴더의 세대가 어긋나면(대응 파일 sha256, 제품 방향, target 입력, 미완료 폴더 등) 아무것도 쓰지 않고 종료 2이다. `--windows`는 reference 쌍 폴더(키 = reference product_id)와 모델 폴더(키 = 모델 product_id)를 제품마다 하나씩 준다. reference 폴더는 6회차를 DL2 경로의 `data/confirmed/roi_correspondence.json`으로 돌린 결과여야 한다(4회차 직접 대응으로 만든 폴더면 `model_report_correspondence_mismatch`). 같은 쌍을 두 경로(사이드카 `target_to_reference` 후보, 후보 payload `region_id`)로 연결했는데 서로 다른 ROI이면 사이드카 쪽을 쓰고 행에 `link_mismatch`를 표시한다(CSV/JSON 열 `link_source`·`link_flags`). 부호는 모델 − target으로 통일한 열이 따로 있고 원값(b − a)도 함께 나온다. case 구성 차이는 인과를 뜻하지 않는다. 필드와 검증 목록은 [데이터 계약](data_contract.md)의 `model_report_v1` 절에 있다.

```text
python -m simcompare model-report --candidates data/changes --confirm data/confirmed --windows QA4_R00=data/local --windows QA4_C00=data/local/models/QA4_C00 --diameter-um 5 --window-um 20 --out data/model_report
```

`confirm-pairs --models`의 출력은 기존 파일(reference↔target 쌍만) 외에 `models/<product_id>/roi_correspondence.json`, `confirmed_pairs_sidecar.json`, `deferred.json`(pending 모델에 accept 가 있을 때만), `models/decisions.json`, `models/index.json`이다. 스키마는 [데이터 계약](data_contract.md)의 확정 쌍 절에 있다. 같은 봉투 파일을 두 `--match`에 줘도 모델 후보는 한 번만 센다.

## ETX (Linux 서버)

**준비물**: Python 3.8 이상, numpy, Pillow(DL1 환경에 이미 있음). 패키지 설치는 필요 없다.

**실행기**: 기본 흐름은 파일 하나다: `bundles/etx/simcompare_job_etx.py`. 서버의 작업 폴더(BASE_DIR)에 복사한다. 이 파일 하나가 01 → 03 → 02 → diag → 04 → (05) → 06 → 07을 돌린다. 묶인 패키지(payload)는 파일에 한 번만 들어 있다. 다제품 흐름(`bundles/etx/simcompare_job_models_01~05_*.py`)과 ΔV·위험도 흐름(`bundles/etx/dv/` 01~06)은 회차별 파일을 따로 쓴다. 설정 예시와 대응표는 [configs/etx/README.md](../configs/etx/README.md)에 있다. 맨 위 `EDIT BELOW` ~ `EDIT ABOVE` 블록만 고친다.

> **이름 대응**: 예전 기본 흐름 실행기 8개(`simcompare_job_01_import_hotspots.py` … `07_vi_report.py`, `diag_validate_compare.py`)는 없어졌다. 통합 실행기의 STEPS 이름이 `01`(hotspot import), `03`(전극 PNG import), `02`(compare2), `diag`(validate-data·compare), `04`(candidates-change), `05`(confirm-pairs, DL2), `06`(compare-windows), `07`(compare2-report)다. 이 문서 아래의 옛 번호(0, 0b, 1, 1m, 2, 2b, 3, 3m, 4, 4b, 4c, 5, 5m, 6, 6m, 7, 8)는 예전 번호다(전체 표는 [상세 레퍼런스](usage_reference.md) "ETX 실행 파일 이름"). CLI 명령 이름은 그대로다.

### 폴더 구조 (BASE_DIR 기준)

```text
BASE_DIR/
  simcompare_job_etx.py               통합 실행기 (EDIT 블록만 고친다)
  real.yaml                           configs/etx/templates/real.yaml 을 복사해 [모델] 줄만 고친다
  compare2.yaml                       configs/etx/templates/compare2.yaml 을 복사해 [모델] 줄만 고친다
  data/input/                         ── 사용자가 미리 둔다 ──
    QA4_R00/  QA4_T00/                hotspot 모델 폴더 (<case>/images/..., <case>/result/<불량명>.csv)
    thk_info-QA4_R00.txt              층간 거리 (모델마다 하나, 탭 구분)
    thk_info-QA4_T00.txt
    pairs.csv                         전극 쌍 표 (두 모델 공통 한 파일)
    density/<제품군>_line.csv, <제품군>_pixel.csv     밀도 CSV
    dl1/<모델>/                       DL1 PNG (CNO, Merged_Layer, Layer)
  data/imported/reference|target      01 출력 (실행기가 만든다)
  data/assets/reference|target        03 출력 (복사하지 않는다)
  data/compare2                       02 출력
  outputs/numeric_real                diag 출력 (real.yaml 의 output_dir)
  data/changes  data/confirmed  data/local  data/compare2_report     04 · 05 · 06 · 07 출력
  data/_runs/<RUN_TAG>/               다시 실행할 때 옮겨 둔 이전 출력
  runs/simcompare_job_etx.log         로그
```

모델 폴더 위에 `hotspot`이라는 중간 폴더는 없다. 모델 폴더와 `thk_info-<모델>.txt`는 `data/input/` 바로 아래에 둔다. DL1 입력(`data/input/dl1`)은 그대로다.

### 고칠 곳

1. **템플릿의 `[모델]` 표시 줄**: `real.yaml`(모델 이름 `product_id`, `source_id`, `interlayer_distance_path`의 파일 이름)과 `compare2.yaml`(제품군, 해상도, `hotspot_root`, `dl1_model`)에서 `[모델]` 표시가 붙은 줄만 고친다. 모델 이름, hotspot root, DL1 root, 지름, 창은 이 두 파일에서 한 번만 읽고 실행기가 모든 단계의 인수로 넘긴다.
2. **실행기의 `BASE_DIR`과 `STEPS`**: `BASE_DIR`은 위 폴더 구조의 맨 위 폴더(절대 경로)다. `STEPS`는 돌릴 단계 목록이다. 04 정렬 인수 등 나머지 상수는 예시 값이니 제품에 맞게 확인한다.

### 실행기 EDIT 블록

| 상수 | 기본값 | 뜻 |
|---|---|---|
| `STEPS` | `['01', '03', '02', 'diag', '04', '06', '07']` | 돌릴 단계. 순서는 적은 순서가 아니라 항상 01, 03, 02, diag, 04, 05, 06, 07이다. 모르는 이름은 실행 전에 오류로 멈춘다. `'05'`(DL2 확정)는 단계로 있지만 기본에서 뺐다 |
| `RUN_TAG` | `None` | 이전 출력 보관 폴더 이름. `None`이면 UTC 시각 `YYYYMMDD-HHMMSS` |
| `DRY_RUN` | `False` | `True`이면 만든 JOBS와 옮길 폴더 목록을 로그에 찍기만 하고 아무것도 실행·이동하지 않는다. 처음에는 `True`로 확인한다 |
| `REAL_CONFIG`, `COMPARE2_CONFIG` | `real.yaml`, `compare2.yaml` | BASE_DIR 안의 설정 파일 이름. `compare2.yaml`의 `numeric_config`가 같은 `real.yaml`을 가리켜야 한다 |
| `INCLUDE_CNO` | `True` | 01에서 CNO case도 import한다. 켠/끈 실행은 01부터 다시 한다 |
| `MAX_ASSET_MEMORY_MB` | `16384` | 03의 `--max-asset-memory-mb`(기본값 없는 필수 인수, 예시 값) |
| `ALIGN_ARGS` | 예시 목록 | 04 `candidates-change` 인수(탐색 반경 30 µm, 최소 Dice 0.3, 층 허용 1 µm, 변경 허용 0.5 µm, 병합 0.5, top-k 20, `--mask-derivation-rule between_stack_order_v1`, `--merge-scope all`, `--suppress-overlap 0.3`, unit 정렬 3 µm, 국소 정렬 1 µm, `--unit-fold`). 모두 **예시**다. `--config`, `--cnt-from-dl1-root`, `--electrode-png-root`, `--window-um`, `--diameter-um`, `--out`은 설정 파일에서 오므로 여기 적으면 오류다 |
| `EXTRA_WINDOWS_UM` | `[]` | 06에서 `report.map_window_um` 말고 더 볼 창(µm). 예 `[20, 30]` |
| `JITTER_UM` | `0.5` | 06 `--jitter-um` |
| `DL2_MATCH_ARGS`, `DL2_DECISION_ARGS` | 예시 | 05를 STEPS에 넣을 때만 쓰는 DL2 인수(`--match`, `--decisions`, `--labelled-by`, `--mutual-tolerance-um`) |
| `CHANGES_DIR`, `CONFIRMED_DIR`, `LOCAL_DIR`, `REPORT_DIR` | `data/changes`, `data/confirmed`, `data/local`, `data/compare2_report` | 04~07 출력 폴더(기본 그대로 쓴다) |
| `LOG_FILE`, `WORK_DIR`, `BASE_DIR` | `runs/simcompare_job_etx.log`, `None`, `None` | 로그, 패키지를 풀 폴더, 상대 경로 기준 폴더 |

설정 파일에서 오는 값은 다음과 같다.

| 값 | 읽는 곳 | 쓰는 단계 |
|---|---|---|
| hotspot 입력 root | `compare2.yaml` `products.<role>.hotspot_root` | 01, 03 |
| DL1 root | `compare2.yaml` `report.dl1_root` | 03, 04 (`--cnt-from-dl1-root`, `--electrode-png-root`) |
| 지름 | `compare2.yaml` `report.diameter_um` | 04 `--diameter-um`, 06 `--sort-diameter-um` |
| 창 | `compare2.yaml` `report.map_window_um` | 04 `--window-um`, 06 `--window-sizes`(`EXTRA_WINDOWS_UM`이 뒤에 붙는다) |
| 01 출력 | `real.yaml` 양쪽 `data_dir` | 01 `--out` |
| 03 출력 | `real.yaml` 양쪽 `asset_manifest.path`·`electrode_import_record.path`의 부모 폴더 (둘이 같은 폴더이고 파일 이름이 `review_assets.json`·`electrode_asset_import_record.json`이어야 한다. 아니면 실행 전 오류) | 03 `--out` |
| 02 출력 | `compare2.yaml` `output_dir` | 02 `--out`, 07 `--compare2-dir` |
| diag 출력 | `real.yaml` `output_dir` | diag |

- 경로는 `data/...`처럼 쓴다. `/data`처럼 `/`로 시작하면 루트 아래 절대 경로가 되어 권한 오류가 난다.
- `BASE_DIR`: 설정 파일 이름·`LOG_FILE`·`WORK_DIR`·모든 상대 경로의 기준 폴더. 비워 두면 실행기가 있는 폴더다. wrapper가 `.py`를 다른 곳에 복사해 실행한다면 절대 경로를 적는다. 로그의 `BASE_DIR requested=... resolved=...; relative paths resolve from ...` 줄에서 실제 기준 폴더를 확인한다.
- 로그는 실행 전에 `flow: ... job(s):` 아래에 만든 JOBS 전체를 `job <명령> <인수...>` 줄로, 옮길 폴더를 `move <원래> -> <보관>` 줄로 찍는다. 작업마다 `--root`, `--config`, `--out` 같은 경로 인수가 `인수 '적은 값' -> 절대 경로` 줄로 찍히고, `payload_sha256=`(묶인 코드 해시), numpy·Pillow 버전, 작업이 끝날 때마다 `<명령> exit N` 줄이 남는다. 종료 코드 0·3이면 다음 작업으로 가고 그 밖이면 멈춘다.
- 계획 단계의 오류(모르는 단계 이름, 설정 파일 오류, 03 출력 폴더 불일치, 보관 대상이 이미 있음)는 `오류: <코드>: <메시지>`와 종료 코드 2로 멈춘다. 아무것도 실행하거나 옮기지 않는다.
- 소스를 고친 뒤에는 `uv run python scripts/build_etx_stage_runners.py --overwrite`로 실행기 파일을 다시 만든다(통합 실행기, `models_`, dv 폴더 포함).

**단계별 설명**: 통합 실행기를 한 번 돌리면 STEPS의 단계가 위 고정 순서로 차례로 실행된다. 단계 사이에 파일을 복사하거나 설정 파일을 고치는 수작업은 없다.

1. 단계 01: 두 모델을 각각 import하며 `--expected-cases-from-roots`로 다른 모델 root를 함께 준다. 기대 case 목록이 없으면 04의 모든 ROI가 `blocked`(`map_coverage_incomplete`)가 된다. `pairs.csv`는 복사하지 않는다. 두 모델이 `data/input/pairs.csv` 한 파일을 `real.yaml`의 `pair_table_path`로 읽는다.
2. 단계 03: 전극 PNG를 import한다. condition_assignment는 `--config real.yaml --side reference|target`으로 `real.yaml`에서 읽는다(`assignment_<side>.json`은 쓰지 않는다). 출력은 `real.yaml`의 `asset_manifest.path`가 가리키는 `data/assets/<side>/`에 그대로 두고 복사하지 않으며 sha256도 적지 않는다. `MAX_ASSET_MEMORY_MB`(예시 16384)를 서버에 맞게 고친다. 전극 role은 `electrode_role_policy: electrode_id`로 pair 표의 전극 이름을 그대로 쓴다.
3. 단계 02: `compare2`. line/pixel 면적, λ, CAA, VI와 reference→target 변화를 계산한다. `real.yaml`의 `condition_assignment` 값(`max_operation_bytes` 포함)은 이 단계 전에 정한다([다시 실행하기](#다시-실행하기)). glass 면적 값이 비어 있으면 vi는 '대기'이고 종료 코드 3이다.
4. 단계 diag: `validate-data`, `compare`. 종료 코드 3은 partial 항목이 있다는 뜻이므로 `reasons`를 본다. `validation.json`의 `reasons`에 `invalid_configured_input`이 있으면 설정 입력이 읽히지 않은 것이다.
5. 단계 04: `candidates-change`. 두 제품의 CNT 3층을 한 offset으로 정렬하고 변경 위치 ROI를 방향별 top-k로 뽑는다. 필수 인수에는 기본값이 없고 EDIT 블록 `ALIGN_ARGS`의 값은 **예시**다(탐색 반경 30 µm, 최소 Dice 0.3, 층 허용 1 µm, 변경 허용 0.5 µm, 병합 0.5, top-k 20, `--mask-derivation-rule between_stack_order_v1`): 제품에 맞게 바꾼다. 창과 지름은 `compare2.yaml`의 `report.map_window_um`·`report.diameter_um`에서 온다. 탐색 반경의 최소값은 pixel pitch의 2배 이상이고(코드 검사), 권장값은 예상 레이아웃 이동과 반복 패턴 주기를 모두 덮는 값이다. `--cnt-from-dl1-root`는 3회차와 같은 DL1 PNG root다. `--merge-overlap 0.5`는 두 창이 50 % 이상 겹치면 병합한다는 뜻이다(50 % 미만으로 겹친 창은 따로 남는다).
   - **선택 인수 3개**(생략하면 이전 동작): `--merge-scope all`은 층이 달라도 겹친 창을 하나의 ROI로 병합한다(생략하면 층마다 따로). `--suppress-overlap 0.3`은 병합 뒤 순위가 앞선 ROI와 30 % 이상 겹치는 ROI를 층 구분 없이 뺀다(생략하면 억제 없음). 실행기 예시에는 이 두 값이 들어 있고 **예시 값**이다. `--electrode-png-root <폴더>`는 alignment.html 층 묶음의 층별 전극 형상을 읽을 **DL1 전극 PNG root**다(구조 `<제품>/<전극>/<층>.png`). hotspot 폴더의 case 이미지는 쓰지 않는다. 실행기 예시는 `data/input/dl1`이며, 전극 PNG가 다른 곳에 있으면 그 폴더로 바꾼다(생략하면 `--cnt-from-dl1-root`).
   - **두 제품의 frame 크기가 다를 때**(예: unit size 차이로 2004 px와 2014 px): 오류가 아니다. 정렬 후 두 frame이 겹치는 공통 영역만 비교하고 resize하지 않는다. `alignment.json`에 두 frame 크기·차이·공통 영역(픽셀 수, 비율, 위치)이 기록되고 `candidate_queries_v1.json` notes에 `frame_shape_differs`(정보, 종료 코드 영향 없음)가 남는다. 공통 영역 밖 띠는 변경 후보가 되지 않는다.
   - **alignment.html 확인 순서**: 1절 요약(맨 위 정렬 상태 배지, frame 크기·공통 영역, 사분면 offset — 허용값 초과 칸은 빨강) → 2절 CNT 정렬 정렬 전·후 겹침(정렬 후 같은 패턴은 회색~검정, 남은 색이 변경) → 3절 변경 강조 지도(빨강 = 변경, ROI 번호) → 4절 ROI 목록 → 5절 ROI 상세(위치 그림, 불량 조합이 있는 층마다 비교·reference·target 묶음, 한글 기여 조합 표). "창 안 새로 생김/사라짐"은 그 창 안에서 한쪽 모델의 hotspot이 없다는 뜻이고 case 자체는 두 모델에 다 있을 수 있다(있는 모델 열). 정렬 상태가 `aligned`가 아니면(`aligned_uncertain`/`alignment_failed_identity_used`) 2절 그림을 먼저 확인한다.
   - 로그의 `candidates-change exit N`을 본다(실행기는 3도 '계속'으로 처리한다). 그림이 메모리 한도로 빠지면 `alignment_report_images_skipped` 또는 `alignment_report_layer_base_skipped`(층 묶음 바탕만 빠짐) 경고와 함께 3이며, 계산 결과(JSON)는 같다. 존재하지 않는 DL1 root는 쓰기 전에 종료 코드 2(`cnt_dl1_root_missing`)로 멈추고 `--out`을 만들지 않으며 실행기도 멈춘다(로그의 `오류:` 줄과 `--cnt-from-dl1-root '…' -> <절대 경로>` 줄을 본다). 질의·index export 폴더마다 DL2용 `mask_derivation.json`도 생긴다. 상세는 [상세 레퍼런스](usage_reference.md)의 "ROI 선정".
   - 이전 문서의 `--cnt-from-dl1-root`는 03 단계와 같은 DL1 PNG root(`compare2.yaml`의 `report.dl1_root`)다. 04의 `--out`(`CHANGES_DIR`)이 이미 있으면 실행기가 실행 전에 `data/_runs/<RUN_TAG>/`로 옮긴다.
4b. 회차 4b(선택, 0b회차와 dv_02회차 뒤): 실행기 4b(`candidates-dv`, 파일 이름은 `dv/simcompare_job_05_candidates_dv.py`). 0b회차의 `dv-grade` 출력 폴더 둘(제품마다 하나)과 dv_02회차(`dv/simcompare_job_02_import_assets.py`, 통합 실행기 03 단계와 같은 인수) 뒤 `real.yaml`이 필요하다. dv 실행기는 통합 실행기의 보관(이동)을 하지 않으므로 `--out`은 매번 새 경로다. 모든 인수가 필수이며 EDIT 블록의 값(K 4, `--roi-grade high`, 창 20 µm, 병합 0.5)은 **예시**다. 종료 코드 3은 '계속'으로 처리되므로 로그의 `candidates-dv exit N` 줄과 `candidate_queries_v1.json`의 `warnings`를 본다. 4회차와 독립이다. 상세는 [dv_candidates.md](dv_candidates.md).
4c. 회차 4c(선택, 4b회차 뒤): 실행기 4c(`vulnerability-report`, 파일 이름은 `dv/simcompare_job_06_vulnerability_report.py`). 4b회차 출력 폴더를 읽어 `data/vulnerability_report/report.html`을 만든다. 입력 기록의 `data_dir`(절대 경로)로 전극 layout을 다시 읽으므로 4b회차와 같은 서버에서 이어서 실행한다.
6. 06 입력 고르기: **직접 대응**을 쓰면 `data/changes/direct/roi_correspondence.json`이 06 입력이므로 DL2와 05 단계를 건너뛴다(기본). 다른 제품 탐색이 필요할 때만 아래 DL2 경로를 쓴다.
7. (DL2 경로 전용) DL2: DL2 저장소의 `bundles/etx/psdl2_job_baseline_ncc.py`를 같은 `BASE_DIR`에서 돌려 `data/dl2/ncc_r2t.json`, `ncc_t2r.json`과 `*_sheet/sheet.html`을 만든다(torch 불필요, `--allow-unversioned-producer` 사용). 입력은 `data/changes/*_export/review_rois.json`, `*_index_export/review_rois.json`이다. 시트를 보고 `data/dl2/decisions.csv`(헤더 `candidate_id,decision,note`, `decision`은 `accept` 또는 `reject`)를 쓴다.
8. (DL2 경로 전용) 단계 05: 첫 작업이 `--list`로 `candidate_id` 목록을 로그에 찍고, 둘째 작업이 accept한 후보만 `data/confirmed/roi_correspondence.json`으로 바꾼다. `DL2_DECISION_ARGS`의 `--labelled-by CHANGE_ME`를 실제 ID로 바꾼다. `decisions.csv`가 없으면 둘째 작업이 종료 코드 2로 멈춘다. 05를 STEPS에 넣으면 06은 `data/confirmed/roi_correspondence.json`을 쓰고 07에는 `--confirm data/confirmed`가 붙는다. 처음에는 `STEPS = ['05']`로 목록만 보고 `decisions.csv`를 쓴 뒤 `STEPS = ['05', '06', '07']`로 돌린다.
9. 단계 06: `compare-windows`. 창은 `report.map_window_um`(`EXTRA_WINDOWS_UM`이 있으면 뒤에 더한다), jitter 0.5 µm(권장 운용값), 정렬 지름은 `report.diameter_um`이다. `data/local/local_delta.html`에서 쌍별 결과를 본다. 창이 frame 밖으로 나가거나 valid 구멍을 덮으면 그 Δ는 null이고 종료 코드 3이다. diag의 `comparison_masks.zip`이 맞으면 재사용하므로 diag를 06 앞에 두는 것이 빠르다.
10. 단계 07: `compare2-report`. 02 출력(`compare2.json`)으로 보고서 HTML 한 장(`data/compare2_report/report.html`)을 만든다. 04·06이 STEPS에 있거나 그 출력 폴더가 있으면 `--candidates`·`--windows`를 붙인다. DL1 검색 결과가 있으면 EDIT 블록과 별개로 07 JOBS 인수에 `--dl1-search <파일>`이 필요하다(통합 실행기에는 이 인수 상수가 없으므로 필요하면 `compare2-report`를 직접 실행한다).

통합 `report`는 아직 실자료 `compare` 출력을 읽지 못해(`missing_input_file`, 종료 코드 2) ETX 단계에 넣지 않았다.

### 다시 실행하기

고른 단계의 출력 폴더가 이미 있으면 실행기가 실행 전에 `data/_runs/<RUN_TAG>/` 아래로 옮긴다(`RUN_TAG`가 `None`이면 UTC 시각 `YYYYMMDD-HHMMSS`). 옮기는 폴더는 고정 경로다: 01 = `real.yaml` 양쪽 `data_dir`, 03 = 양쪽 `asset_manifest.path`의 부모 폴더, 02 = `compare2.yaml`의 `output_dir`, diag = `real.yaml`의 `output_dir`, 04 = `data/changes`, 05 = `data/confirmed`, 06 = `data/local`, 07 = `data/compare2_report`. 지우거나 복사하지 않고 같은 파일 시스템 안에서 `os.rename`만 쓴다. 보관 위치가 이미 있으면(같은 `RUN_TAG`를 다시 썼을 때) 아무것도 옮기지 않고 종료 코드 2로 멈춘다. `real.yaml`은 고치지 않는다. 옮긴 폴더는 쓸모가 없어지면 사용자가 직접 지운다.

- **이번 변경(층간 거리 필터, 입력 경로 통일) 뒤 첫 실행**: 01부터 전체를 다시 돈다(`STEPS` 기본값). 필터는 로드 단계에 있어 01 출력은 같지만, 03 자산·04~06 산출물이 입력 해시에 필터 규칙을 넣어 이전 것을 거부하고, 설정 구조(경로)가 바뀌었으므로 처음부터 하는 것이 안전하다. 이전 결과는 `data/_runs/`에 남는다.
- **일부만 다시**: 입력 해시를 바꾸는 값(`real.yaml`의 `condition_assignment` 값 — `max_operation_bytes` 포함 —, `thk_info` 파일, `pairs.csv`)을 고쳤다면 02부터 다시 돈다(07의 `compare2_input_hash_mismatch`가 요구하는 최소 범위). 03 자산 기록은 같은 입력 해시에 묶여 있어서(04가 `source_asset_manifest_identity_mismatch`로 막는다) 04 이후도 다시 만들려면 03부터 돈다: `STEPS = ['03', '02', 'diag', '04', '06', '07']`. `max_operation_bytes`는 **02 전에 넉넉히** 정한다: 07이 메모리 가드(`oversized_real_operation`)로 멈춰 한도를 올리면 입력 해시가 달라져 07이 `compare2_input_hash_mismatch`로 다시 멈춘다. 그때는 `real.yaml`의 `condition_assignment` 값을 바꾼 것이므로 02부터 다시 돈다. `compare2.yaml`을 고쳐도 07이 `compare2_config_mismatch`로 멈추므로 02부터 다시 돈다.
- **04만 다시**(인수만 바뀐 경우): `STEPS = ['04', '06', '07']`. 01~03 출력은 그대로 쓴다.
- **07만 다시**: `STEPS = ['07']`.
- **실행기만 교체해서 이어갈 때**: 01 import 결과(`.npz`, 옛 `.npy`)는 그대로 읽힌다. 새 실행기 파일로 바꾸고(`BASE_DIR`, `STEPS`는 다시 고친다) 02부터 다시 돈다. 새 실행기로 `compare`(diag)를 다시 돌려야 `comparison_masks.zip`이 생겨 06이 면적 계산을 건너뛴다.
- 다제품(`models_`)과 dv 실행기는 보관 기능이 없다. `--out`이 이미 있으면 거부하므로 먼저 옮기거나 이름을 바꾼다.

### 층간 거리 필터 (`thk_info-<모델>.txt`)

vertical short(층 사이 단락)는 두 끝점 층 사이 거리 d보다 지름이 **작거나 같으면** 불량으로 세지 않는다. 지름 ≤ d이면 그 case의 그 지름 hotspot을 계산에서 뺀다(같을 때도 뺀다). 이 규칙은 2026-11-05 결정(같을 때는 포함)을 **번복**한 것이다(2026-11-12). 뺀 항목은 기여 0이고 누락이 아니다. lateral·CNO·open은 대상이 아니다. 값은 지름과 같은 단위(µm)라고 가정한다.

- **파일 형식**: 탭 구분 텍스트. 첫 줄은 머리글 `layers<TAB>distance`. 둘째 줄부터 `<층A><층B><TAB><거리>`(층 이름을 붙여 쓴다. 예 `BMLACT<TAB>3`). 양쪽 순서(`BMLACT`, `ACTBML`)가 모두 있어도 되고 값이 같아야 한다. 한쪽만 있어도 받는다. 같은 층 행(`BMLBML<TAB>0`)과 거리 0은 오류 없이 받는다. 거리는 0 이상의 유한한 수다. 쉼표 구분 파일, 칸 수가 2가 아닌 줄, 음수·비숫자는 줄 번호와 함께 오류다.
- **경로**: `real.yaml`(또는 `models.yaml`)의 모델별 `condition_assignment.interlayer_distance_path`(설정 파일 폴더 기준, 예 `data/input/thk_info-QA4_R00.txt`). 암묵 기본 위치는 없다.
- **키가 없으면 필터는 꺼진다.** 출력은 이 기능이 없을 때와 같다. 단 02(`compare2`)는 설정에 남은 vertical short가 있으면 키가 **필수**다(`interlayer_distance_path_required`, 종료 코드 2).
- 키가 있는데 표에 없는 층 조합이 있으면 계산 전에 종료 코드 2로 멈추고 빠진 조합을 모두 나열한다.
- 기록: 02·04·06 출력과 07 보고서 입력 표에 `distance_filter`(규칙, 파일 이름, 표 sha256, 뺀 record 수)가 남는다. 표 내용이 바뀌면 입력 해시가 달라져 이전 산출물을 거부한다(공백·행 순서만 바뀌면 같다).

### 입력 위치 규칙 (알아 둘 점)

- `pair_table_path`, `interlayer_distance_path`, `asset_manifest.path`, `electrode_import_record.path`는 **설정 파일이 있는 폴더**(`real.yaml`·`models.yaml` 폴더) 기준이다(절대 경로·`..`·백슬래시는 거부). `asset_manifest`의 sha256은 생략할 수 있다.
- **옛 `pairs.csv`가 BASE_DIR에 남아 있으면**: 설정 폴더 기준 경로(`data/input/pairs.csv`)와 `data_dir` 기준 경로(옛 위치)에 파일이 둘 다 있으면 `input_location_conflict`(종료 코드 2)로 멈춘다. 내용이 같아도 멈춘다. 옛 위치의 `pairs.csv`를 지우거나 옮긴다.
- `data_dir`과 설정 폴더 기준 경로가 **같은 파일**(심볼릭 링크·하드 링크 포함)이면 옛 규칙으로 읽는다(옛 설정과 같은 결과).
- **03 해시**: 03은 `--config real.yaml`로 `real.yaml`과 같은 `condition_assignment`를 읽으므로 04의 데이터셋 해시와 일치한다. 옛 `--condition-assignment <JSON>`을 쓰면 경로 문자열까지 `real.yaml`과 같아야 한다.
- **sha256을 생략하면** 두 asset 파일은 03이 남긴 기록(`electrode_asset_import_record.json`의 `manifest_sha256`)과 서로 맞는지(체인)만 확인한다. 다른 03 실행의 결과가 섞이는 것은 잡지만, manifest와 record를 둘 다 다른 실행의 것으로 통째로 바꾸는 것은 막지 못한다. 그런 보장이 필요하면 `asset_manifest`·`electrode_import_record`에 `sha256`을 적는다(적으면 엄격 비교한다). `local_delta_report`(06 보고서)의 체인 모드는 record를 다시 확인하지 않는다.
- **수동 대응의 `named_pair`(06)**: 이제 (전극, 층) 쌍으로 case를 찾는다. 이전에는 전극 이름만으로 찾아 층이 엇갈린 vertical short case가 모호(ambiguous)했다. 동작 변경이므로 이전 06 산출물과 `named_pair` 행이 달라질 수 있다.

### 예상 시간·메모리

합성 자료 실측이다(macOS 12코어·48 GiB, 모델당 1,682 case, 한 layer, crop 1262², 32 지름). 실자료 실측이 아니며 Python 3.8에서는 `compare`가 약 2배 느리다.

| 회차 | 시간 | 최대 메모리 |
|---|---|---|
| 1 `import-hotspots` | 모델당 4~5.5분 | 0.85 GiB |
| 2 `validate-data` | 1~1.5분 | 1.1 GiB |
| 2 `compare` | 10~12분 | 3.3~3.8 GiB |
| 3 `import-electrode-assets` | 모델당 1.2~2.4분 | 0.57 GiB |
| 4 `candidates` (비가중 취약점 ROI의 실측) | 5~6.5분 | 1.2~1.3 GiB |
| 4b `candidates-dv`, 4c `vulnerability-report` | 실규모 실측 없음(합성 소형 자료만 확인) | 실규모 실측 없음 |
| 4 `candidates-change` | 실규모 실측 없음(합성 소형 자료만 확인) | 실규모 실측 없음 |
| 5 `confirm-pairs` | 3.5~4.6분 | 0.9~1.25 GiB |
| 6 `compare-windows` | 쌍 50개 5~5.5분, 쌍 200개 6~9.5분(2회차 `comparison_masks.zip` 사용) | 0.8~1.25 GiB |

`comparison.json`은 약 380 MB, `comparison.csv`는 약 52 MB이고 한 번에 디스크는 중간 산출물까지 약 3.5~3.8 GB다. 출처: `docs/plan/scale_compare/T5_benchmark.md`, `docs/plan/scale_stage46/T5_benchmark.md`. 6회차에서 `comparison_masks.zip`이 없으면 쌍 200개에 14~23분(최대 3.4 GiB)이 걸린다.

### 자주 나는 오류

| 오류 | 의미와 대처 |
|---|---|
| `oversized_real_operation: ... estimated N bytes exceed limit` | 계산 전에 추정 메모리가 `max_operation_bytes`(기본 8 GiB)를 넘어 멈췄다. 아무것도 공개되지 않았다. 메시지의 추정치 N에 여유를 더해(물리 메모리 이하) 두 `condition_assignment`에 넣고 다시 돌린다. `max_operation_bytes`는 입력 해시에 들어가므로 02 전에 정한다. 07이 이 오류로 멈춘 뒤 값을 올리면 02부터 다시 돌아야 한다. 호스트 RAM만 확인하므로 컨테이너·스케줄러 한도가 더 작으면 그 이하로 직접 낮춘다. 4·5·6회차는 쌍·후보·창 크기를 줄여도 된다 |
| `oversized_real_bucket_output` | 한 layer×지름 그룹의 bucket 항목이 `max_bucket_output_entries`를 넘었다. 값을 올린다 |
| `oversized_real_label_patterns` | 겹침 패턴 항목이 `max_label_pattern_entries`를 넘었다. 값과 필요하면 `max_operation_bytes`를 함께 올린다 |
| `real_hotspot_output_exists`, `electrode_output_exists`, `output_dir_exists`(종료 코드 2) | `--out`이 이미 있다. 통합 실행기는 고른 단계의 출력 폴더를 실행 전에 `data/_runs/<RUN_TAG>/`로 옮기므로 보통 나지 않는다(`STEPS`에 없는 단계의 폴더가 남아 있을 때 난다). `models_`·dv 실행기는 기존 폴더를 직접 옮기거나 이름을 바꾸고 다시 돌린다. 기존 폴더는 건드리지 않았다 |
| `input_location_conflict`(종료 코드 2) | 설정 폴더 기준 경로와 `data_dir` 기준 경로에 같은 입력 파일이 둘 다 있다(예 BASE_DIR에 옛 `pairs.csv`). 옛 위치 파일을 지우거나 옮긴다 |
| `interlayer_distance_path_required`, `interlayer_distance_*`(종료 코드 2) | 02에서 남은 vertical short가 있는데 `interlayer_distance_path` 키가 없거나, thk 파일 형식·조합이 틀렸다. 위 "층간 거리 필터" 절 |
| `compare2_input_hash_mismatch`(종료 코드 2) | 02 뒤에 `real.yaml`의 `condition_assignment` 값(`max_operation_bytes` 포함)이나 thk·pair 파일을 바꿨다. 02부터 다시 돈다 |
| `etx_unknown_step`, `etx_asset_folder_mismatch`, `etx_archive_target_exists` 등 `etx_*`(종료 코드 2) | 통합 실행기가 실행 전에 설정을 검사해 멈췄다. 아무것도 실행하거나 옮기지 않았다. 메시지의 안내대로 EDIT 블록이나 설정 파일을 고친다 |
| `expected_case_folder_missing` | 오류가 아니라 진단이다(종료 코드 3). 기대 case 목록(두 모델 root에서 발견된 case 이름의 합집합)에 있는데 이 모델에는 case 폴더가 없다는 뜻이다. 그 case의 모든 지름이 누락으로 기록된다. 두 모델 모두에 없는 case는 찾을 수 없다 |
| `expected_case_inventory_unprovided` | import 때 기대 case 목록을 주지 않았다. coverage가 `unconfirmed`라 4회차 ROI가 모두 막히므로 `--expected-cases-from-roots`로 1회차부터 다시 한다 |
| `cnt_dl1_root_missing`, `electrode_png_root_missing`(종료 코드 2) | 4회차 `--cnt-from-dl1-root` 또는 `--electrode-png-root` 경로가 폴더가 아니다(빈 문자열 포함). `--out`을 만들지 않고 멈췄으므로 경로를 고쳐 같은 `--out`으로 다시 돌린다 |
| `alignment_report_images_skipped`, `alignment_report_layer_base_skipped`(종료 코드 3) | 4회차 alignment.html 그림(또는 층 묶음 바탕만)이 메모리 한도로 빠졌다. 표와 JSON 결과는 같다. 그림이 필요하면 `max_operation_bytes`를 물리 메모리 안에서 올린다 |
| `map_coverage_incomplete` | 지도 coverage가 불완전해 ROI가 막혔다. 기대 case 목록 없이 import했다면 1회차부터 다시 한다 |
| `electrode_role_required_for_matching` | role이 없는 전극이 있다. `electrode_role_policy: electrode_id`를 쓰고 03 단계부터 다시 돈다 |
| `comparison_recovery_unreadable`, `local_delta_recovery_unreadable` | 강제 종료 뒤 복구 기록을 읽을 수 없다. 아무것도 지우지 않고 멈춘 것이므로 원인(권한·I/O)을 고쳐 다시 실행한다. 복구 방법은 레퍼런스의 공개 절 참조 |

## Windows 폐쇄망 (CMD)

CMD, 설치된 Python, 동봉된 wheel만 쓴다. 가상환경 활성화, 관리자 실행, 실행 정책 변경이 필요 없다. PC에는 **CPython 3.12 x64와 Python Launcher(`py.exe`)**가 있어야 하며 Python 설치 파일은 ZIP에 없다.

**오프라인 설치**

1. `bundles/windows_cp312_win_amd64.zip`을 폐쇄망으로 옮기고 `%USERPROFILE%\simcompare-offline\` 아래에 푼다. 기존 가상환경을 지우거나 덮어쓰지 말고 새 설치는 새 폴더를 쓴다. 풀면 `windows_cp312_win_amd64\` 안에 `install_offline.cmd`, `verify_windows_bundle.py`, `requirements-windows.txt`, `manifest.json`, `wheelhouse\`, `fixtures\`가 있어야 한다.
2. 명령 프롬프트에서 한 줄씩 실행한다. **중간에 오류가 나면 다음 명령을 실행하지 않는다.**

```bat
cd /d "%USERPROFILE%\simcompare-offline\windows_cp312_win_amd64"
py -3.12 -m venv .venv-cmd
.venv-cmd\Scripts\python.exe verify_windows_bundle.py .
.venv-cmd\Scripts\python.exe -m pip --disable-pip-version-check install --no-index --only-binary=:all: --require-hashes --find-links=wheelhouse -r requirements-windows.txt
.venv-cmd\Scripts\python.exe -m pip check
.venv-cmd\Scripts\python.exe -m simcompare --help
```

`wheel integrity OK`, `No broken requirements found`, 도움말이 나오면 설치 확인이 끝난 것이다. 이것만으로 실제 데이터나 수치가 검증된 것은 아니다. 같은 절차를 묶은 `install_offline.cmd`도 있다(기존 `.venv-cmd`는 지우지 않고 Python 버전을 확인해 쓰며 어느 단계든 실패하면 멈춘다). 이 파일 자체의 Windows 실행은 아직 별도 확인이 필요하다.

**실행 예시** (압축 해제 폴더에서, 경로는 실제 위치로 바꾼다. import 출력은 아직 없는 새 폴더)

```bat
.venv-cmd\Scripts\python.exe -m simcompare import-hotspots --root "D:\data\input\hotspot\QA4_R00" --out "D:\data\imported\reference" --expected-cases-from-roots "D:\data\input\hotspot\QA4_T00"
.venv-cmd\Scripts\python.exe -m simcompare validate-data --config "D:\data\real.yaml"
.venv-cmd\Scripts\python.exe -m simcompare compare --config "D:\data\real.yaml"
.venv-cmd\Scripts\python.exe -m simcompare electrode-map --config "D:\data\real.yaml"
.venv-cmd\Scripts\python.exe -m simcompare compare-windows --config "D:\data\real.yaml" --correspondence "D:\data\pairs.json" --window-sizes 10 20 30 --sort-diameter-um 5 --out "D:\data\local"
```

`real.yaml`은 저장소의 `configs/real.example.yaml`을 복사해 `data_dir`를 실제 변환 폴더로 바꾸고 확인된 layer·조건·pixel/line 분류표만 넣는다. 설정 파일 안의 상대 경로는 그 파일 위치 기준이다. 합성 예제(`demo-data --out demo_data` 뒤 `validate-data`, `compare`, `electrode-map`)는 numeric_v2 합성 데이터만 만들며 실제 검증을 대신하지 않는다.

**ETX와 다른 점**

- 실행기 `.py`와 `BASE_DIR`이 없다. 명령을 `.venv-cmd\Scripts\python.exe -m simcompare <명령>`으로 직접 친다. 경로는 `D:\...` 절대 경로를 따옴표로 감싼다.
- 6회차 흐름(import → compare → 전극 asset → candidates-change → (선택 DL2 → confirm-pairs) → compare-windows)은 같지만 Windows에서 실자료 전체 흐름은 아직 검증되지 않았다. 확인된 것은 설치·`pip check`·도움말까지다.
- 모든 명령 옵션은 [상세 레퍼런스](usage_reference.md)에 있다. 통합 HTML 보고서(`report`)와 대응 파일 변환(`simcompare.correspondence`)도 거기에 있다.

## 공통

**종료 코드**

| 코드 | 의미 | 확인 |
|---|---|---|
| 0 | 요청 범위의 결과가 available이거나 생성 완료 | `execution.json` |
| 3 | partial/unavailable/blocked 항목이 있지만 사용 가능한 부분 출력 | `status`, `reasons`, `coverage` |
| 2 | 입력·설정 오류(또는 지도 incomplete) | 오류 메시지, `error.json` |

**결과 읽기**

- `comparison.json`, `comparison.csv`: line/pixel, pair exclusive/shared, 원 case CA, 지름별 coverage, provenance. partial/unavailable의 value는 null이고 부분 합은 `partial_value`다. 관측 0은 available 0이며 누락과 다르다.
- `local_delta.html`: 쌍마다 a/b layout과 hotspot crop, a/b/Δ 표. 정렬 지름의 |Δ_union| 내림차순이며 `file://`로 열린다. `local_delta.json`과 `local_delta_rows.csv`는 쌍 × 창 크기 × 32 지름의 수치다.
- `window_sensitivity.*`: 창 크기별 Δ 부호 일치와 순위 상관. 추천값은 없다.
- 실행마다 `execution.json`이 남고, 오류면 `error.json`도 쓴다.

**결과 교체 보장**: `compare`와 `compare-windows`는 결과를 한 세대로 공개한다. 새 파일을 전부 임시 위치에 쓴 뒤 한 번에 바꾸므로, 실패하거나 Ctrl-C로 멈추거나 디스크가 차도 이전 결과가 그대로 남는다. 실자료(`mode: real`) `compare`와 `compare-windows`는 모든 오류에서 `error.json`/`execution.json`만 새로 쓰고 이전 결과는 지우지 않는다(입력 manifest 검증 오류·옮겨진 import 폴더 포함). 예외로, 위 교체 절차를 쓰지 않는 비트랜잭션 실자료 출력(`condition_assignment`가 없는 점검 출력, `mode: real`의 numeric_v2 자료)은 쓰는 도중 오류가 나면 일부만 쓰인 파일이 이전 파일과 섞이지 않도록 `comparison*` 리포트를 지운다. 설정 파일 자체를 읽지 못하는 경우와 합성(`mode: synthetic`) `compare`는 예외다. 강제 종료로 중간 상태가 남아도 다음 실행이 시작할 때 이전 세대로 되돌리거나 새 세대로 확정한다. 같은 출력 폴더에서 두 명령을 동시에 돌리는 것은 지원하지 않는다.

**설정 키 요약** (`real.yaml`의 `condition_assignment`와 최상위 `compare`)

| 키 | 기본값 | 설명 |
|---|---|---|
| `max_operation_bytes` | 8 GiB | 추정 최대 메모리 상한. 물리 메모리를 넘길 수 없다 |
| `max_group_mask_cells` | 5,000,000,000 | 한 layer×지름 그룹의 cells |
| `max_materialized_source_cells` | 6,000,000,000 | 가장 큰 layer 그룹의 cells(= case 수 × frame cells). ETX 예시는 20,000,000,000 |
| `max_bucket_output_entries` | 1,000,000 | 한 그룹이 내보내는 bucket 항목 상한 |
| `max_label_pattern_entries` | 24,000,000 | 정확한 겹침 분할이 한 그룹에서 보관하는 패턴 항목 상한 |
| `compare.full_bucket_detail` | `false` | `true`면 모든 bucket 목록을 쓴다. `comparison.json`이 GB 단위가 될 수 있고 1,682 case·한 layer 기준 추정 약 10.5 GB라 기본 한도를 넘는다 |
| `compare.top_buckets` | 20 | 요약 모드에서 보관할 상위 bucket 수 |
| `map.diameter_um` | 5 | 전극 수 지도 대표 지름(µm) |
| `electrode_role_policy` | 없음 | `electrode_id`면 pair 표의 전극 이름을 role로 쓴다. `electrode_roles`와 함께 쓸 수 없다 |

한도 값들의 계산 방법과 추정식은 레퍼런스의 "Linux 서버 단일 파일 실행" 절을 본다.
