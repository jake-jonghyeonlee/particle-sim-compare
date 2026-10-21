# ETX 회차별 예시 파일

`docs/usage.md`의 "ETX(Linux GPU 서버) 실행 순서"에 맞춘 예시다. 경로는 `/data` 아래 예시이므로 서버 경로로 바꾼다.

| 회차 | 실행 파일 (`bundles/etx/`) | 필요한 설정 파일 | 회차 뒤 할 일 |
|---|---|---|---|
| 1 | `simcompare_job_1_import_hotspots.py` | 없음 | `pairs.csv`를 `/data/imported/reference/`, `/data/imported/target/`에 복사 |
| 2 | `simcompare_job_2_validate_compare.py` | `real.stage2.example.yaml` → `/data/real.yaml` | 오류 메시지의 추정치로 `max_operation_bytes` 결정 |
| 3 | `simcompare_job_3_import_electrode_assets.py` | `assignment_reference.example.json`, `assignment_target.example.json` → `/data/assignment_<side>.json` | 출력 파일을 각 `data_dir`로 복사, sha256 기록 |
| 4 | `simcompare_job_4_candidates.py` | `real.stage4.example.yaml` → `/data/real.yaml` | `*_export`, `*_index_export`의 `review_rois.json`을 DL2로 |

- 실행 파일은 맨 위 EDIT 블록(`JOBS`, `LOG_FILE`, `WORK_DIR`)만 고친다. 네 파일의 묶인 코드는 같고 `JOBS`만 다르다.
- `assignment_<side>.json`은 `real.yaml`의 해당 `condition_assignment`와 같은 내용에 `electrode_roles`를 더한 것이다. `<ELECTRODE_ID>`, `<ROLE>` 자리표시는 실제 전극 id와 role로 바꾼다. JSON에는 주석을 쓸 수 없다.
- `UNSPECIFIED`는 "정해지지 않음" 이름표다. 실제 값을 알면 바꾼다. 빈 값은 오류다.
- layer 순서는 번호가 클수록 위층(PXL 최상단)으로 가정했다. 반대면 `stack_order`를 뒤집는다.
- 소스를 고친 뒤에는 `uv run python scripts/build_etx_stage_runners.py --overwrite`로 네 파일을 다시 만든다.
