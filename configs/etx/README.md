# ETX 회차별 예시 파일

`docs/usage.md`의 "ETX (Linux 서버)" 절에 맞춘 예시다. 경로는 실행기 `BASE_DIR`(작업 폴더) 기준 상대 경로 예시다. 입력은 `data/input/...`, 출력은 `data/imported/...` 등에 둔다고 가정한다. `/`로 시작하면 절대 경로가 되므로 주의한다.

실행 파일 이름은 실행 순서를 따른다: `simcompare_job_<번호>_<이름>.py`. 기본 흐름은 `bundles/etx/`(01~07), 진단은 `_diag_`, 다제품은 `_models_01~05`, ΔV·위험도 흐름은 `bundles/etx/dv/`(01~06)에 있다.

## 기본 흐름 (`bundles/etx/`, 실행 순서)

| 회차 | 실행 파일 | 필요한 설정 파일 | 회차 뒤 할 일 |
|---|---|---|---|
| 01 | `simcompare_job_01_import_hotspots.py` | 없음 | `pairs.csv`를 `data/imported/reference/`, `data/imported/target/`에 복사 |
| 02 (선택, 01 뒤) | `simcompare_job_02_vi_compute.py` (`compare2`) | `compare2.example.yaml` → `compare2.yaml`(BASE_DIR, `real.yaml` 옆), `real.yaml`, 밀도 CSV `data/density/<제품군>_line.csv`·`_pixel.csv` | 01회차 뒤 어디서든 실행한다. diag 회차 출력은 쓰지 않는다. 제품군 glass 면적이 비어 있으면 vi 는 '대기'이고 종료 코드 3이다. `data/compare2/compare2.json` 등을 확인. 상세는 `docs/usage_reference.md`의 "compare2" |
| 03 | `simcompare_job_03_import_assets.py` | `assignment_reference.example.json`, `assignment_target.example.json` → `assignment_<side>.json` | 출력 파일을 각 `data_dir`로 복사, sha256 기록 |
| 04 | `simcompare_job_04_change_candidates.py` (`candidates-change`) | `real.stage4.example.yaml` → `real.yaml`, DL1 PNG root | `data/changes/alignment.html`로 정렬 확인, 로그의 `candidates-change exit N` 확인. 선택 인수 3개(`--merge-scope all`·`--suppress-overlap 0.3`은 EDIT 블록의 예시 값, `--electrode-png-root`는 DL1 전극 PNG root로 예시 `data/input/dl1`). 직접 대응을 쓰면 DL2와 05회차를 건너뛴다 |
| DL2 (선택) | DL2 저장소 `bundles/etx/psdl2_job_baseline_ncc.py` | 없음 | 다른 제품 탐색이 필요할 때만. `data/dl2/*_sheet/sheet.html`을 보고 `data/dl2/decisions.csv` 작성 |
| 05 (선택, DL2 경로 전용) | `simcompare_job_05_confirm_pairs_dl2.py` | `real.yaml`(04회차와 같음), `decisions.csv` | `--labelled-by CHANGE_ME`를 실제 ID로 |
| 06 | `simcompare_job_06_compare_windows.py` | `real.yaml` | 기본 입력은 `data/changes/direct/roi_correspondence.json`. DL2 경로를 썼다면 `--correspondence`를 `data/confirmed/roi_correspondence.json`으로 바꾼다. `--window-sizes 10 20 30`(30 = `compare2.yaml` `report.map_window_um` 기본). `data/local/local_delta.html`에서 쌍별 ΔCA 확인 |
| 07 (선택, 02 뒤, 최종 보고서) | `simcompare_job_07_vi_report.py` (`compare2-report`) | 02회차 출력 `data/compare2`, 같은 `compare2.yaml`(BASE_DIR) | JOBS 는 `--candidates data/changes --windows data/local`을 포함한다. 04·06회차를 안 돌렸으면 두 줄을 지운다. DL2(05)를 썼으면 `--confirm data/confirmed`를, DL1 검색 결과가 있으면 `--dl1-search <파일>`을 더한다. `--out data/compare2_report`는 새 경로. 지도·overlay 는 `report.dl1_root`(DL1 Layer PNG)가 있어야 그려진다. `data/compare2_report/report.html`을 연다 |
| diag (선택) | `simcompare_job_diag_validate_compare.py` | `real.stage2.example.yaml` → `real.yaml` | 진단용 `validate-data`·`compare`. 기본 한도로 충분한 것이 정상. `oversized_real_operation`이 나오면 추정치로 `max_operation_bytes` 결정 |

## 다제품 (`bundles/etx/`, `_models_`)

| 회차 | 실행 파일 | 필요한 설정 파일 | 회차 뒤 할 일 |
|---|---|---|---|
| models_01 | `simcompare_job_models_01_import.py` (`import-hotspots`) | 없음 | 모델 hotspot import(모델마다 한 줄, 예시 `QA4_C00`). `pairs.csv`를 `data/models/<모델>/`에 복사 |
| models_02 | `simcompare_job_models_02_import_assets.py` (`import-electrode-assets`) | `assignment_<모델>.json`(= `models.yaml`의 `condition_assignment`) | 출력(`data/assets/models/<모델>`)을 `data/models/<모델>/`로 복사하고 `models.yaml`에 `asset_manifest`·`electrode_import_record` 기입 |
| models_03 (DL2 경로, 05회차 대신) | `simcompare_job_models_03_confirm_pairs.py` | `real.yaml`, `models.yaml`, `decisions.csv`, DL2 `search_batch.json` | `--labelled-by CHANGE_ME`를 실제 ID로. 모델 쌍은 target 질의에서만 생긴다. `data/confirmed/models/<모델>/roi_correspondence.json` 확인 |
| models_04 | `simcompare_job_models_04_compare_windows.py` (`compare-windows --model`) | `real.yaml`, `models.yaml` | 모델마다 한 줄. 06회차를 `data/confirmed/roi_correspondence.json`으로 한 번 더 돌려 reference 쌍 `data/local`을 둔다(models_05 입력) |
| models_05 | `simcompare_job_models_05_report.py` (`model-report`) | models_03·models_04·06회차 출력 | `data/model_report/model_report.html`을 연다 |

## ΔV·위험도 흐름 (`bundles/etx/dv/`, 기본 흐름과 따로 진행)

dv 폴더는 기본 흐름과 트리가 달라 따로 진행한다. 공통 단계(import·asset)는 같은 JOBS·설명으로 복사해 두었고 번호만 dv 순서다. 파일 이름이 기본 흐름과 같아도(`simcompare_job_01_import_hotspots.py`) 폴더가 다르면 다른 흐름이다. 로그는 그 폴더 기준 `runs/simcompare_<이름>.log`에 남는다.

| 회차 | 실행 파일 (`bundles/etx/dv/`) | 필요한 설정 파일 | 회차 뒤 할 일 |
|---|---|---|---|
| dv_01 | `simcompare_job_01_import_hotspots.py` | 없음 | 기본 01회차와 같은 JOBS. `pairs.csv` 복사 |
| dv_02 | `simcompare_job_02_import_assets.py` | `assignment_<side>.json` | 기본 03회차와 같은 JOBS. dv_05 전에 필요 |
| dv_03 (선택) | `simcompare_job_03_dv_template.py` (`dv-template`) | 임피던스 DB CSV(선택) | `--timings`·`--root`를 제품에 맞게 고친다. `data/dv_template/<제품>/dv_sheet.csv`의 전압 칸과 `new_electrodes.csv`의 임피던스를 채운다. 기본 흐름과 독립 |
| dv_04 (선택) | `simcompare_job_04_dv_grade.py` (`dv-grade`) | 채운 `dv_sheet.csv` | `--sheet`·`--root`·`--product-id`·경계를 제품에 맞게 고친다. `data/dv_grade/<제품>/dv_grades.csv`에 쌍별 ΔV·최대 타이밍·등급이 나온다 |
| dv_05 (선택) | `simcompare_job_05_candidates_dv.py` (`candidates-dv`) | dv_02 뒤 `real.yaml`, dv_04 `data/dv_grade/<제품>` 둘 | 모든 인수가 필수이며 기본값이 없다(EDIT 블록의 K 4, `high`, 창 20 µm, 병합 0.5는 예시). 로그의 `candidates-dv exit N` 확인. 04회차와 독립. 상세는 `docs/dv_candidates.md` |
| dv_06 (선택) | `simcompare_job_06_vulnerability_report.py` (`vulnerability-report`) | dv_05 출력 `data/changes_dv` | `data/vulnerability_report/report.html`을 연다. dv_05와 같은 서버에서 이어서 실행한다(입력 기록의 `data_dir` 절대 경로로 전극 layout을 다시 읽는다) |

## 옛 이름 → 새 이름

| 옛 이름 (`simcompare_job_…`) | 새 이름 |
|---|---|
| `0_dv_template` | `dv/03_dv_template` |
| `0b_dv_grade` | `dv/04_dv_grade` |
| `1_import_hotspots` | `01_import_hotspots` (dv 에도 `dv/01_import_hotspots` 복사) |
| `1m_import_models` | `models_01_import` |
| `2_validate_compare` | `diag_validate_compare` |
| `2b_compare2` | `02_vi_compute` |
| `3_import_electrode_assets` | `03_import_assets` (dv 에도 `dv/02_import_assets` 복사) |
| `3m_import_model_assets` | `models_02_import_assets` |
| `4_candidates` | `04_change_candidates` |
| `4b_candidates_dv` | `dv/05_candidates_dv` |
| `4c_vulnerability_report` | `dv/06_vulnerability_report` |
| `5_confirm_pairs` | `05_confirm_pairs_dl2` |
| `5m_confirm_pairs_models` | `models_03_confirm_pairs` |
| `6_compare_windows` | `06_compare_windows` |
| `6m_compare_windows_models` | `models_04_compare_windows` |
| `7_model_report` | `models_05_report` |
| `8_compare2_report` | `07_vi_report` |

CLI 명령 이름(`compare2`, `compare2-report` 등)은 그대로이며 별칭은 없다. 이전 회차 번호(예: "4회차")가 든 옛 문서·메모는 위 표로 바꿔 읽는다.

## 참고

- 다제품 흐름(models_01 → models_02 → models_03 → models_04 → models_05): 모델 자산은 사용자가 나중에 모델별 폴더로 준다. 폴더 규칙은 `data/models/<product_id>/`(`manifest.json` + `pairs.csv` 복사, models_02 뒤 `review_assets.json`·`electrode_asset_import_record.json`·`assets/`)이고 폴더가 없으면 그 모델은 `assets_pending`이다(후보는 보이고 accept 하면 `deferred.json`, 종료 3). 예시 `models.example.yaml`을 `real.yaml`과 같은 폴더에 `models.yaml`로 두고 모델 키를 폴더 이름과 맞춘다. models_04는 diag 회차 마스크를 재사용하지 않아 06회차보다 시간·메모리가 더 들고 `--models`는 configured 모델을 모두 로드하며 `--case-*`는 쌍×case 마다 마스크를 다시 읽는다 — 실규모 시간은 로그로 확인한다.
- 실행 파일은 맨 위 EDIT 블록(`JOBS`, `LOG_FILE`, `WORK_DIR`)만 고친다. 모든 파일의 묶인 코드는 같고 `JOBS`만 다르다.
- 04회차 `candidates-change`의 필수 인수는 기본값이 없다(선택 인수 3개는 아래 줄). 실행기 EDIT 블록의 값(탐색 반경 30 µm, 최소 Dice 0.3, 층 허용 1 µm, 변경 허용 0.5 µm, 창 10 µm, 병합 0.5, top-k 20, `--mask-derivation-rule between_stack_order_v1`)은 **예시**이니 제품에 맞게 바꾼다. 탐색 반경의 최소값은 pixel pitch의 2배 이상(코드 검사)이고, 권장값은 예상 레이아웃 이동과 반복 패턴 주기를 모두 덮는 값이다. 주기가 탐색 반경 안에 있으면 `peak_ambiguous`와 대안 offset으로 표시된다. 의미는 `docs/usage_reference.md`의 "ROI 선정"에 있다. `--cnt-from-dl1-root`는 03회차와 같은 DL1 PNG root(`data/input/dl1`)다. 존재하지 않는 root는 쓰기 전에 종료 코드 2(`cnt_dl1_root_missing`)이며 `--out`을 만들지 않고 실행기가 멈추므로 로그의 `오류:` 줄과 `--cnt-from-dl1-root '…' -> <절대 경로>` 줄을 확인한다. 예전 `candidates`(max_count, 비가중 취약점 ROI)는 참고용이다. ΔV 취약점 ROI는 dv_05·dv_06회차다.
- 04회차의 선택 인수 3개: `--merge-scope all`(층을 넘어 창을 병합; 예시 값, 생략하면 층별 병합), `--suppress-overlap 0.3`(앞 순위 ROI와 이 비율 이상 겹치는 ROI를 뺀다; 예시 값, 생략하면 억제 없음), `--electrode-png-root <폴더>`(alignment.html 층 묶음의 층별 전극 형상을 읽을 **DL1 전극 PNG root**, 구조 `<제품>/<전극>/<층>.png`. hotspot 폴더의 case 이미지는 쓰지 않는다. 예시 값은 `data/input/dl1`이고 전극 PNG가 다른 곳에 있으면 그 폴더로 바꾼다. 생략하면 `--cnt-from-dl1-root`. 폴더가 아니거나 빈 문자열이면 종료 코드 2 `electrode_png_root_missing`). 셋 다 생략하면 이전 동작이다.
- 04회차만 다시 돌릴 때 순서: 기존 `data/changes`를 옮기거나 이름을 바꾼다(`--out`은 없는 경로여야 한다) → 04회차 → 직접 대응이면 06회차, DL2 경로면 DL2 → 05회차 → 06회차. 01~03회차는 다시 돌릴 필요가 없다. 04회차 코드가 바뀌었으므로 `bundles/etx/` 안의 실행 파일을 전부 같이 교체하는 것을 권장한다(묶인 코드가 모두 같다).
- `assignment_<side>.json`은 `real.yaml`의 해당 `condition_assignment`와 똑같은 내용이다. JSON에는 주석을 쓸 수 없다.
- role은 `electrode_role_policy: electrode_id`로 자동이다: `pairs.csv`의 모든 전극이 자기 이름을 role로 받는다. 04회차 `candidate_queries_v1.json`·`execution.json`의 `dl2_model_roles`가 DL2 설정 `model.roles`에 넣을 목록이다.
- `UNSPECIFIED`는 "정해지지 않음" 이름표다. 실제 값을 알면 바꾼다. 빈 값은 오류다.
- layer 순서는 번호가 클수록 위층(PXL 최상단)으로 가정했다. 반대면 `stack_order`를 뒤집는다.
- diag 회차 한도 키: `max_bucket_output_entries`(출력 항목, 기본 1,000,000)와 `max_label_pattern_entries`(정확한 겹침 분할이 보관하는 패턴, 기본 24,000,000, 넘으면 `oversized_real_label_patterns`)는 별개다. 요약 모드에서는 둘 다 기본값으로 1,682 케이스 한 layer 합성 자료가 끝난다(`docs/plan/scale_compare/T5_benchmark.md`). bucket 출력은 32개 그룹이 모두 보관하므로 추정에 그룹 수만큼 곱해 들어가며, 전체 모드(`full_bucket_detail: true`)는 같은 규모에서 기본 8 GiB를 넘는다(`docs/usage_reference.md`).
- diag 회차(compare), 04회차(electrode-map/candidates), 06회차(compare-windows)는 마스크를 한 장씩 읽는 스트리밍이다(Phase B). diag 회차는 `comparison_masks.zip`도 만들어 06회차가 두 모델의 `compute_areas`를 건너뛰게 한다. 취약점 ROI(`candidates`)의 `review_rois.json`은 슬림 형식(행마다 `observed_ref`, 목록은 최상위 `region_coverages`에 한 번)이며 같은 `review_roi_v1`이라 DL2가 그대로 읽는다. 추정 메모리가 `max_operation_bytes`를 넘으면 계산 전에 `oversized_real_operation: <단계>: estimated X bytes exceed limit Y; ...`로 멈춘다. 물리 메모리 검사는 호스트 RAM 기준이다(cgroup/job 한도는 알 수 없다). 실측 시간·메모리는 `docs/usage.md` ETX 절과 `docs/plan/scale_stage46/T5_benchmark.md`(04회차 `candidates-change`는 실규모 실측이 아직 없다).
- **CNO(`lateralopen-<전극>-<층>`) 켜기.** 01회차 JOBS 두 줄(dv 폴더 01 포함)에 `--include-cno` 가 들어 있다. CNO 를 쓰지 않으면 두 줄에서 지운다(지우면 출력은 이 옵션이 생기기 전과 바이트가 같다). **CNO 를 켠 실행은 01회차부터 다시 한다.** 4회차 결과(`data/changes`)도 T22 이후 코드로 다시 만들어야 한다(기존 import·4회차 출력에는 CNO 가 없다). 한쪽 모델에만 있는 CNO 위치는 반대쪽에 같은 이름의 short 층이 있을 때만 ready 이고 없으면 blocked 다. CNO 쌍은 7회차 지도가 없고 결함 조건 표의 CNO 배지로만 보인다.
- 01회차 import는 `--expected-cases-from-roots`로 다른 모델 root를 함께 받아 기대 case 목록을 두 모델 발견 case 이름의 합집합으로 만든다(한쪽에만 없는 case 폴더는 `expected_case_folder_missing`; 두 모델 모두에 없는 case는 찾을 수 없다). 출력 폴더에 `expected_cases.txt`(기대 case 목록)도 함께 써지고, 로그에 `payload_sha256=`와 `expected_cases: N (…)` 줄이 남는다. 입력 root 경로가 없으면 `expected_case_root_missing` 등 오류가 인수 이름과 경로를 알려 주고 종료 코드 2로 멈추며 `--out` 폴더를 만들지 않는다(경로를 고치고 같은 `--out`으로 다시 실행하면 된다). 예전 실행기로 이미 import해 04회차가 `expected_case_inventory_unprovided`로 막혔다면 01회차부터 다시 한다. 다시 실행하려면 먼저 이전 출력 폴더를 옮기거나 이름을 바꾼다(`--out`은 없는 경로여야 하며 03·04·05회차도 기존 출력 폴더를 거부한다): `data/imported/reference`, `data/imported/target`, `data/assets/reference`, `data/assets/target`, `data/changes`, `data/confirmed`(`data/local`은 06회차가 한 세대로 교체하므로 그대로 두어도 된다). 그다음 01회차부터 순서대로 다시 하고, 회차 사이 수작업도 다시 한다: 01회차 뒤 `pairs.csv`를 새 `data/imported/reference`·`target` 안에 다시 복사하고, 03회차 뒤 03회차 안내대로 import 결과를 `data_dir`에 다시 두고 `real.yaml`의 `electrode_import_record.sha256`을 새 파일 값으로 고친다.
- **hotspot 점유는 `gray > 63` 이다 (2026-11-10).** 01회차 import 가 hotspot PNG 의 회색값 64 이상만 점유로 센다(예전 `!= 0` 대신 팀 기존 계산과 같은 규칙; 1..63 은 점유 아님). manifest 의 `hotspot_occupancy.low_gray_pixels_total` 과 01 로그의 `hotspot_occupancy:` 줄이 빠진 픽셀 수를 보인다(0 이면 결과가 예전과 같다). 규칙이 없는 예전 import 는 이후 회차에서 `hotspot_occupancy_rule_mismatch` 로 거부된다. **01회차부터 새 번들·새 `--out` 으로 다시 실행**한다(dv 01, models_01 포함; 이후 회차 전부).
- **SD2·SD3·PXL 수직 단락은 제외한다 (2026-11-10).** `vertical_short` 이고 한쪽 끝 층이 `SD2`·`SD3`·`PXL` 인 case 는 모든 회차에서 빠진다(같은 층 포함; lateral·CNO 는 유지). 규칙 이름이 입력 해시에 들어가므로 이 규칙 이전의 02~07회차 산출물(diag 마스크 zip, 04 후보, 05 확정, 06 창, 03 자산 포함)은 거부된다. 01회차는 그대로 두고 **02→07회차 전부(03 자산 포함)를 새 번들·새 `--out` 으로 다시 실행**한다. dv 흐름은 03→06, models 흐름은 03→05 이다. `compare2.json` 의 `inputs.case_exclusions` 와 07회차 보고서 입력 표에 규칙 한 줄이 있다.
- **폴더가 없는 기대 case 는 면적 0 으로 센다.** 01회차를 `--expected-cases-from-roots` 로 돌렸으면(기대 목록 `confirmed`) 02회차(`compare2`)가 한쪽 모델에 폴더가 없는 case 를 0 으로 센다. 01회차 결과를 그대로 두고 02→07회차만 다시 돌리면 된다. 0 으로 센 목록은 `data/compare2/compare2.json` 의 `sides.<role>.absent_expected_cases` 와 07회차 보고서 맨 위 정보 상자에 있다(없으면 키와 상자가 없다). 한계: case 폴더 안에 1.png·2.png·hotspot PNG 가 하나도 없으면 01회차가 그 폴더를 발견하지 못하므로 다른 모델 root 에 같은 case 가 있으면 0 으로 센다. 1.png 나 2.png 가 있으면 PNG 누락(missing_input)으로 null 이다. 시뮬레이션을 하지 않은 case 와도 구분하지 못하므로 0 처리 목록을 확인한다.
- 셸 문자셋(EUC-KR 등)에 없는 글자(µ 등)는 화면에 `\xb5` 처럼 보인다. 로그 파일(`runs/*.log`)과 출력 파일에는 원문(UTF-8)이 남고, 명령은 이 글자 때문에 실패하지 않는다.
- 소스를 고친 뒤에는 `uv run python scripts/build_etx_stage_runners.py --overwrite`로 회차별 파일을 다시 만든다(dv 폴더 포함).
- 통합 `report`는 아직 실자료 compare 출력을 읽지 못해 회차에 넣지 않았다(`docs/usage.md` ETX 절).
