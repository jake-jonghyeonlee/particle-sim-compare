# ETX 회차별 예시 파일

`docs/usage.md`의 "ETX (Linux 서버)" 절에 맞춘 예시다. 경로는 실행기 `BASE_DIR`(작업 폴더) 기준 상대 경로 예시다. 입력은 `data/input/...`, 출력은 `data/imported/...` 등에 둔다고 가정한다. `/`로 시작하면 절대 경로가 되므로 주의한다.

| 회차 | 실행 파일 (`bundles/etx/`) | 필요한 설정 파일 | 회차 뒤 할 일 |
|---|---|---|---|
| 1 | `simcompare_job_1_import_hotspots.py` | 없음 | `pairs.csv`를 `data/imported/reference/`, `data/imported/target/`에 복사 |
| 2 | `simcompare_job_2_validate_compare.py` | `real.stage2.example.yaml` → `real.yaml` | 기본 한도로 충분한 것이 정상. `oversized_real_operation`이 나오면 추정치로 `max_operation_bytes` 결정 |
| 3 | `simcompare_job_3_import_electrode_assets.py` | `assignment_reference.example.json`, `assignment_target.example.json` → `assignment_<side>.json` | 출력 파일을 각 `data_dir`로 복사, sha256 기록 |
| 4 | `simcompare_job_4_candidates.py` (`candidates-change`) | `real.stage4.example.yaml` → `real.yaml`, DL1 PNG root | `data/changes/alignment.html`로 정렬 확인, 로그의 `candidates-change exit N` 확인. 직접 대응을 쓰면 DL2와 5회차를 건너뛴다 |
| DL2 (선택) | DL2 저장소 `bundles/etx/psdl2_job_baseline_ncc.py` | 없음 | 다른 제품 탐색이 필요할 때만. `data/dl2/*_sheet/sheet.html`을 보고 `data/dl2/decisions.csv` 작성 |
| 5 (DL2 경로 전용) | `simcompare_job_5_confirm_pairs.py` | `real.yaml`(4회차와 같음), `decisions.csv` | `--labelled-by CHANGE_ME`를 실제 ID로 |
| 6 | `simcompare_job_6_compare_windows.py` | `real.yaml` | 기본 입력은 `data/changes/direct/roi_correspondence.json`. DL2 경로를 썼다면 `--correspondence`를 `data/confirmed/roi_correspondence.json`으로 바꾼다. `data/local/local_delta.html`에서 쌍별 ΔCA 확인 |

- 실행 파일은 맨 위 EDIT 블록(`JOBS`, `LOG_FILE`, `WORK_DIR`)만 고친다. 여섯 파일의 묶인 코드는 같고 `JOBS`만 다르다.
- 4회차 `candidates-change`의 인수는 모두 필수이며 기본값이 없다. 실행기 EDIT 블록의 값(탐색 반경 30 µm, 최소 Dice 0.3, 층 허용 1 µm, 변경 허용 0.5 µm, 창 20 µm, 병합 0.5, top-k 20, `--mask-derivation-rule between_stack_order_v1`)은 **예시**이니 제품에 맞게 바꾼다. 탐색 반경의 최소값은 pixel pitch의 2배 이상(코드 검사)이고, 권장값은 예상 레이아웃 이동과 반복 패턴 주기를 모두 덮는 값이다. 주기가 탐색 반경 안에 있으면 `peak_ambiguous`와 대안 offset으로 표시된다. 의미는 `docs/usage_reference.md`의 "ROI 선정"에 있다. `--cnt-from-dl1-root`는 3회차와 같은 DL1 PNG root(`data/input/dl1`)다. 존재하지 않는 root는 쓰기 전에 종료 코드 2(`cnt_dl1_root_missing`)이며 `--out`을 만들지 않고 실행기가 멈추므로 로그의 `오류:` 줄과 `--cnt-from-dl1-root '…' -> <절대 경로>` 줄을 확인한다. 예전 `candidates`(max_count, 취약점 ROI)는 보류다.
- `assignment_<side>.json`은 `real.yaml`의 해당 `condition_assignment`와 똑같은 내용이다. JSON에는 주석을 쓸 수 없다.
- role은 `electrode_role_policy: electrode_id`로 자동이다: `pairs.csv`의 모든 전극이 자기 이름을 role로 받는다. 4회차 `candidate_queries_v1.json`·`execution.json`의 `dl2_model_roles`가 DL2 설정 `model.roles`에 넣을 목록이다.
- `UNSPECIFIED`는 "정해지지 않음" 이름표다. 실제 값을 알면 바꾼다. 빈 값은 오류다.
- layer 순서는 번호가 클수록 위층(PXL 최상단)으로 가정했다. 반대면 `stack_order`를 뒤집는다.
- 2회차 한도 키: `max_bucket_output_entries`(출력 항목, 기본 1,000,000)와 `max_label_pattern_entries`(정확한 겹침 분할이 보관하는 패턴, 기본 24,000,000, 넘으면 `oversized_real_label_patterns`)는 별개다. 요약 모드에서는 둘 다 기본값으로 1,682 케이스 한 layer 합성 자료가 끝난다(`docs/plan/scale_compare/T5_benchmark.md`). bucket 출력은 32개 그룹이 모두 보관하므로 추정에 그룹 수만큼 곱해 들어가며, 전체 모드(`full_bucket_detail: true`)는 같은 규모에서 기본 8 GiB를 넘는다(`docs/usage_reference.md`).
- 2회차(compare), 4회차(electrode-map/candidates), 6회차(compare-windows)는 마스크를 한 장씩 읽는 스트리밍이다(Phase B). 2회차는 `comparison_masks.zip`도 만들어 6회차가 두 모델의 `compute_areas`를 건너뛰게 한다. 취약점 ROI(`candidates`)의 `review_rois.json`은 슬림 형식(행마다 `observed_ref`, 목록은 최상위 `region_coverages`에 한 번)이며 같은 `review_roi_v1`이라 DL2가 그대로 읽는다. 추정 메모리가 `max_operation_bytes`를 넘으면 계산 전에 `oversized_real_operation: <단계>: estimated X bytes exceed limit Y; ...`로 멈춘다. 물리 메모리 검사는 호스트 RAM 기준이다(cgroup/job 한도는 알 수 없다). 실측 시간·메모리는 `docs/usage.md` ETX 절과 `docs/plan/scale_stage46/T5_benchmark.md`(4회차 `candidates-change`는 실규모 실측이 아직 없다).
- 1회차 import는 `--expected-cases-from-roots`로 다른 모델 root를 함께 받아 기대 case 목록을 두 모델 발견 case 이름의 합집합으로 만든다(한쪽에만 없는 case 폴더는 `expected_case_folder_missing`; 두 모델 모두에 없는 case는 찾을 수 없다). 출력 폴더에 `expected_cases.txt`(기대 case 목록)도 함께 써지고, 로그에 `payload_sha256=`와 `expected_cases: N (…)` 줄이 남는다. 입력 root 경로가 없으면 `expected_case_root_missing` 등 오류가 인수 이름과 경로를 알려 주고 종료 코드 2로 멈추며 `--out` 폴더를 만들지 않는다(경로를 고치고 같은 `--out`으로 다시 실행하면 된다). 예전 실행기로 이미 import해 4회차가 `expected_case_inventory_unprovided`로 막혔다면 1회차부터 다시 한다. 다시 실행하려면 먼저 이전 출력 폴더를 옮기거나 이름을 바꾼다(`--out`은 없는 경로여야 하며 3·4·5회차도 기존 출력 폴더를 거부한다): `data/imported/reference`, `data/imported/target`, `data/assets/reference`, `data/assets/target`, `data/changes`, `data/confirmed`(`data/local`은 6회차가 한 세대로 교체하므로 그대로 두어도 된다). 그다음 1회차부터 순서대로 다시 하고, 회차 사이 수작업도 다시 한다: 1회차 뒤 `pairs.csv`를 새 `data/imported/reference`·`target` 안에 다시 복사하고, 3회차 뒤 3회차 안내대로 import 결과를 `data_dir`에 다시 두고 `real.yaml`의 `electrode_import_record.sha256`을 새 파일 값으로 고친다.
- 소스를 고친 뒤에는 `uv run python scripts/build_etx_stage_runners.py --overwrite`로 여섯 파일을 다시 만든다.
- 통합 `report`는 아직 실자료 compare 출력을 읽지 못해 회차에 넣지 않았다(`docs/usage.md` ETX 절).
