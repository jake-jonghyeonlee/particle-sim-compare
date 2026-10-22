# ETX 회차별 예시 파일

`docs/usage.md`의 "ETX(Linux GPU 서버) 실행 순서"에 맞춘 예시다. 경로는 실행기 `BASE_DIR`(작업 폴더) 기준 상대 경로 예시다. 입력은 `data/input/...`, 출력은 `data/imported/...` 등에 둔다고 가정한다. `/`로 시작하면 절대 경로가 되므로 주의한다.

| 회차 | 실행 파일 (`bundles/etx/`) | 필요한 설정 파일 | 회차 뒤 할 일 |
|---|---|---|---|
| 1 | `simcompare_job_1_import_hotspots.py` | 없음 | `pairs.csv`를 `data/imported/reference/`, `data/imported/target/`에 복사 |
| 2 | `simcompare_job_2_validate_compare.py` | `real.stage2.example.yaml` → `real.yaml` | 기본 한도로 충분한 것이 정상. `oversized_real_operation`이 나오면 추정치로 `max_operation_bytes` 결정 |
| 3 | `simcompare_job_3_import_electrode_assets.py` | `assignment_reference.example.json`, `assignment_target.example.json` → `assignment_<side>.json` | 출력 파일을 각 `data_dir`로 복사, sha256 기록 |
| 4 | `simcompare_job_4_candidates.py` | `real.stage4.example.yaml` → `real.yaml` | `*_export`, `*_index_export`의 `review_rois.json`을 DL2로 |
| DL2 | DL2 저장소 `bundles/etx/psdl2_job_baseline_ncc.py` | 없음 | `data/dl2/*_sheet/sheet.html`을 보고 `data/dl2/decisions.csv` 작성 |
| 5 | `simcompare_job_5_confirm_pairs.py` | `real.yaml`(4회차와 같음), `decisions.csv` | `--labelled-by CHANGE_ME`를 실제 ID로 |
| 6 | `simcompare_job_6_compare_windows.py` | `real.yaml` | `data/local/local_delta.html`에서 쌍별 ΔCA 확인 |

- 실행 파일은 맨 위 EDIT 블록(`JOBS`, `LOG_FILE`, `WORK_DIR`)만 고친다. 네 파일의 묶인 코드는 같고 `JOBS`만 다르다.
- `assignment_<side>.json`은 `real.yaml`의 해당 `condition_assignment`와 똑같은 내용이다. JSON에는 주석을 쓸 수 없다.
- role은 `electrode_role_policy: electrode_id`로 자동이다: `pairs.csv`의 모든 전극이 자기 이름을 role로 받는다. 4회차 `candidate_queries_v1.json`·`execution.json`의 `dl2_model_roles`가 DL2 설정 `model.roles`에 넣을 목록이다.
- `UNSPECIFIED`는 "정해지지 않음" 이름표다. 실제 값을 알면 바꾼다. 빈 값은 오류다.
- layer 순서는 번호가 클수록 위층(PXL 최상단)으로 가정했다. 반대면 `stack_order`를 뒤집는다.
- 2회차 한도 키: `max_bucket_output_entries`(출력 항목, 기본 1,000,000)와 `max_label_pattern_entries`(정확한 겹침 분할이 보관하는 패턴, 기본 24,000,000, 넘으면 `oversized_real_label_patterns`)는 별개다. 요약 모드에서는 둘 다 기본값으로 1,682 케이스 한 layer 합성 자료가 끝난다(`docs/plan/scale_compare/T5_benchmark.md`). bucket 출력은 32개 그룹이 모두 보관하므로 추정에 그룹 수만큼 곱해 들어가며, 전체 모드(`full_bucket_detail: true`)는 같은 규모에서 기본 8 GiB를 넘는다(`docs/usage.md`).
- 2회차(compare), 4회차(electrode-map/candidates), 6회차(compare-windows)는 모두 마스크를 한 장씩 읽는 스트리밍이다(Phase B). 2회차는 `comparison_masks.zip`도 만들어 6회차가 두 모델의 `compute_areas`를 건너뛰게 한다. 4회차 `review_rois.json`은 슬림 형식(행마다 `observed_ref`, 목록은 최상위 `region_coverages`에 한 번)이며 같은 `review_roi_v1`이라 DL2가 그대로 읽는다. 추정 메모리가 `max_operation_bytes`를 넘으면 계산 전에 `oversized_real_operation: <단계>: estimated X bytes exceed limit Y; ...`로 멈춘다. 물리 메모리 검사는 호스트 RAM 기준이다(cgroup/job 한도는 알 수 없다). 실측 시간·메모리는 `docs/usage.md` ETX 절과 `docs/plan/scale_stage46/T5_benchmark.md`.
- 소스를 고친 뒤에는 `uv run python scripts/build_etx_stage_runners.py --overwrite`로 여섯 파일을 다시 만든다.
- 통합 `report`는 아직 실자료 compare 출력을 읽지 못해 회차에 넣지 않았다(`docs/usage.md` ETX 절).
