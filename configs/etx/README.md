# ETX 회차별 예시 파일

`docs/usage.md`의 "ETX(Linux GPU 서버) 실행 순서"에 맞춘 예시다. 경로는 실행기 `BASE_DIR`(작업 폴더) 기준 상대 경로 예시다. 입력은 `data/input/...`, 출력은 `data/imported/...` 등에 둔다고 가정한다. `/`로 시작하면 절대 경로가 되므로 주의한다.

| 회차 | 실행 파일 (`bundles/etx/`) | 필요한 설정 파일 | 회차 뒤 할 일 |
|---|---|---|---|
| 1 | `simcompare_job_1_import_hotspots.py` | 없음 | `pairs.csv`를 `data/imported/reference/`, `data/imported/target/`에 복사 |
| 2 | `simcompare_job_2_validate_compare.py` | `real.stage2.example.yaml` → `real.yaml` | 오류 메시지의 추정치로 `max_operation_bytes` 결정 |
| 3 | `simcompare_job_3_import_electrode_assets.py` | `assignment_reference.example.json`, `assignment_target.example.json` → `assignment_<side>.json` | 출력 파일을 각 `data_dir`로 복사, sha256 기록 |
| 4 | `simcompare_job_4_candidates.py` | `real.stage4.example.yaml` → `real.yaml` | `*_export`, `*_index_export`의 `review_rois.json`을 DL2로 |

- 실행 파일은 맨 위 EDIT 블록(`JOBS`, `LOG_FILE`, `WORK_DIR`)만 고친다. 네 파일의 묶인 코드는 같고 `JOBS`만 다르다.
- `assignment_<side>.json`은 `real.yaml`의 해당 `condition_assignment`와 똑같은 내용이다. JSON에는 주석을 쓸 수 없다.
- role은 `electrode_role_policy: electrode_id`로 자동이다: `pairs.csv`의 모든 전극이 자기 이름을 role로 받는다. 4회차 `candidate_queries_v1.json`·`execution.json`의 `dl2_model_roles`가 DL2 설정 `model.roles`에 넣을 목록이다.
- `UNSPECIFIED`는 "정해지지 않음" 이름표다. 실제 값을 알면 바꾼다. 빈 값은 오류다.
- layer 순서는 번호가 클수록 위층(PXL 최상단)으로 가정했다. 반대면 `stack_order`를 뒤집는다.
- 소스를 고친 뒤에는 `uv run python scripts/build_etx_stage_runners.py --overwrite`로 네 파일을 다시 만든다.
