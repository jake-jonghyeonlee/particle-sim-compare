# simcompare 실행 가이드

두 제품(reference, target)의 hotspot 결과를 가져와 line/pixel 면적을 비교하고, 설계자가 짝지은 위치의 국부 ΔCA를 계산한다. 환경에 따라 아래 둘 중 한 절만 따른다.

- **ETX (Linux 서버)**: 큰 자료. 실행기 `.py` 파일만 올려서 6회차로 나눠 돌린다.
- **Windows 폐쇄망 (CMD)**: 설치 ZIP으로 오프라인 설치한 뒤 명령을 직접 친다.

모든 명령의 옵션, 오류 코드, 출력 스키마, 내부 동작은 [상세 레퍼런스](usage_reference.md)에 있다.

## 6회차 흐름

| 회차 | 명령 | 입력 | 출력 | 다음 회차 전 수작업 |
|---|---|---|---|---|
| 1 | `import-hotspots` | hotspot 폴더(모델별) | `data/imported/reference`, `target` | `pairs.csv`를 두 출력 폴더 안에 복사 |
| 2 | `validate-data`, `compare` | `real.yaml` | `comparison.json`, `comparison.csv`, `comparison_masks.zip` | 3회차용 `assignment_<side>.json` 준비 |
| 3 | `import-electrode-assets` | 전극 PNG, assignment JSON | `data/assets/reference`, `target` | 출력을 각 `data_dir`로 복사하고 `real.yaml`에 경로·sha256 기입 |
| 4 | `candidates` | `real.yaml` | `data/candidates_k3` | `*_export/review_rois.json`을 DL2에 넘겨 매칭, 검토 시트로 `decisions.csv` 작성 |
| 5 | `confirm-pairs` | DL2 결과, `decisions.csv` | `data/confirmed/roi_correspondence.json` | `--labelled-by`를 실제 ID로 |
| 6 | `compare-windows` | 확정 쌍 | `data/local/local_delta.html` 등 | 없음 |

수치를 새로 만드는 것은 2회차(면적)와 6회차(창별 ΔCA)이고 나머지는 입력을 준비하고 짝을 고르는 단계다. 6회차 `compare-windows`는 reference가 a, target이 b이며 방향은 b − a다.

## ETX (Linux 서버)

**준비물**: Python 3.8 이상, numpy, Pillow(DL1 환경에 이미 있음). 패키지 설치는 필요 없다. hotspot 입력 폴더(모델 폴더 `QA4_R00`, `QA4_T00` 아래 `<case>/images/...`, `<case>/result/<불량명>.csv`)와 DL1 PNG 폴더를 서버에 둔다.

**실행기**: `bundles/etx/simcompare_job_<회차>_*.py` 여섯 개를 서버로 복사한다. 설정 예시는 `configs/etx/`에 있고 대응표는 [configs/etx/README.md](../configs/etx/README.md)에 있다. 각 파일은 맨 위 `EDIT BELOW` ~ `EDIT ABOVE` 블록만 고친다.

- `JOBS`: `(명령, [인자...])` 목록. `python -m simcompare <명령> <인자...>`와 같다. 종료 코드 0·3이면 다음 작업으로 가고 그 밖이면 멈춘다.
- `BASE_DIR`: `JOBS`·`LOG_FILE`·`WORK_DIR`의 상대 경로 기준 폴더. 비워 두면 실행기가 있는 폴더다. wrapper가 `.py`를 다른 곳에 복사해 실행한다면 절대 경로를 적는다. 로그 첫 줄 `relative paths resolve from ...`에서 실제 기준 폴더를 확인한다.
- 경로는 `data/...`처럼 쓴다. `/data`처럼 `/`로 시작하면 루트 아래 절대 경로가 되어 권한 오류가 난다.
- 소스를 고친 뒤에는 `uv run python scripts/build_etx_stage_runners.py --overwrite`로 여섯 파일을 다시 만든다.

**회차별 실행**: 실행기를 하나 고르고 그 회차의 `JOBS`를 맞춘 뒤 실행한다. 출력 폴더(`--out`)는 1·3·4·5회차에서 매번 **새 경로**여야 한다(2회차 `output_dir`과 6회차 `data/local`은 한 세대로 통째로 교체되므로 다시 써도 된다).

1. 회차 1: 실행기 1. 두 모델을 각각 import하며 `--expected-cases-from-roots`로 다른 모델 root를 함께 준다. 기대 case 목록이 없으면 4회차의 모든 ROI가 `blocked`(`map_coverage_incomplete`)가 된다. 끝나면 `pairs.csv`를 `data/imported/reference/`와 `data/imported/target/` 안에 복사한다. `pair_table_path`는 `data_dir` 기준 상대 경로(`pairs.csv`)만 받는다(절대 경로·`..`·백슬래시는 거부).
2. 회차 2: `configs/etx/real.stage2.example.yaml`을 `real.yaml`로 복사해 `data_dir`(서버 절대 경로)와 필요한 값을 고친 뒤 실행기 2(`validate-data`, `compare`)를 돌린다. 종료 코드 3은 partial 항목이 있다는 뜻이므로 `reasons`를 본다.
3. 회차 3: `assignment_reference.json`, `assignment_target.json`을 만든다(`real.yaml`의 `condition_assignment`와 같은 내용, JSON에는 주석을 쓸 수 없다). 실행기 3을 돌리고 `--max-asset-memory-mb`는 예시값(16384)을 서버에 맞게 고친다. 끝나면 각 출력의 `review_assets.json`, `electrode_asset_import_record.json`, `assets/`를 해당 `data_dir`로 복사하고, `real.yaml`의 reference·target에 `asset_manifest`와 `electrode_import_record`(`{path, sha256}`)를 넣는다. sha256은 방금 복사한 파일 값이다. 전극 role은 `electrode_role_policy: electrode_id`로 pair 표의 전극 이름을 그대로 쓴다.
4. 회차 4: 실행기 4(`candidates`). `--min-count`, `--window-um`, `--merge-overlap`은 필수이며 예시값을 쓴다. 결과의 `*_export/review_rois.json`, `*_index_export/review_rois.json`이 DL2 입력이고, `dl2_model_roles`가 DL2 설정 `model.roles`에 넣을 목록이다.
5. DL2: DL2 저장소의 `bundles/etx/psdl2_job_baseline_ncc.py`를 같은 `BASE_DIR`에서 돌려 `data/dl2/ncc_r2t.json`, `ncc_t2r.json`과 `*_sheet/sheet.html`을 만든다(torch 불필요, `--allow-unversioned-producer` 사용). 시트를 보고 `data/dl2/decisions.csv`(헤더 `candidate_id,decision,note`, `decision`은 `accept` 또는 `reject`)를 쓴다.
6. 회차 5: 실행기 5. 첫 작업이 `--list`로 `candidate_id` 목록을 로그에 찍고, 둘째 작업이 accept한 후보만 `data/confirmed/roi_correspondence.json`으로 바꾼다. `--labelled-by`의 `CHANGE_ME`를 실제 ID로 바꾼다. `decisions.csv`가 없으면 둘째 작업이 종료 코드 2로 멈춘다.
7. 회차 6: 실행기 6(`compare-windows`, 창 10/20/30 µm, jitter 0.5, 정렬 지름 5 µm). `data/local/local_delta.html`에서 쌍별 결과를 본다. 창이 frame 밖으로 나가거나 valid 구멍을 덮으면 그 Δ는 null이고 종료 코드 3이다.

통합 `report`는 아직 실자료 `compare` 출력을 읽지 못해(`missing_input_file`, 종료 코드 2) ETX 회차에 넣지 않았다.

**회차 사이 수작업을 빠뜨리지 않는다**: 1회차 뒤 `pairs.csv` 복사, 3회차 뒤 import 결과 배치와 `electrode_import_record.sha256` 갱신.

**처음부터 다시 할 때**: `--out`은 없는 경로여야 하고 3·4·5회차도 기존 출력 폴더를 거부하므로, 다음 폴더를 먼저 옮기거나 이름을 바꾼다: `data/imported/reference`, `data/imported/target`, `data/assets/reference`, `data/assets/target`, `data/candidates_k3`, `data/confirmed`. `data/local`은 6회차가 한 세대로 교체하므로 그대로 둬도 된다. 그다음 1회차부터 위 수작업까지 모두 다시 한다.

**실행기만 교체해서 이어갈 때**: 1회차 import 결과(`.npz`, 옛 `.npy`)는 그대로 읽힌다. 여섯 실행기를 새 파일로 바꾸고(`JOBS`, `BASE_DIR`은 다시 고친다) 2회차부터 다시 돈다. 이미 끝난 2회차 출력은 유효하지만, 새 실행기로 `compare`를 다시 돌려야 `comparison_masks.zip`이 생겨 6회차가 면적 계산을 건너뛴다. 4회차 이후 폴더는 새 경로를 쓴다.

### 예상 시간·메모리

합성 자료 실측이다(macOS 12코어·48 GiB, 모델당 1,682 case, 한 layer, crop 1262², 32 지름). 실자료 실측이 아니며 Python 3.8에서는 `compare`가 약 2배 느리다.

| 회차 | 시간 | 최대 메모리 |
|---|---|---|
| 1 `import-hotspots` | 모델당 4~5.5분 | 0.85 GiB |
| 2 `validate-data` | 1~1.5분 | 1.1 GiB |
| 2 `compare` | 10~12분 | 3.3~3.8 GiB |
| 3 `import-electrode-assets` | 모델당 1.2~2.4분 | 0.57 GiB |
| 4 `candidates` | 5~6.5분 | 1.2~1.3 GiB |
| 5 `confirm-pairs` | 3.5~4.6분 | 0.9~1.25 GiB |
| 6 `compare-windows` | 쌍 50개 5~5.5분, 쌍 200개 6~9.5분(2회차 `comparison_masks.zip` 사용) | 0.8~1.25 GiB |

`comparison.json`은 약 380 MB, `comparison.csv`는 약 52 MB이고 한 번에 디스크는 중간 산출물까지 약 3.5~3.8 GB다. 출처: `docs/plan/scale_compare/T5_benchmark.md`, `docs/plan/scale_stage46/T5_benchmark.md`. 6회차에서 `comparison_masks.zip`이 없으면 쌍 200개에 14~23분(최대 3.4 GiB)이 걸린다.

### 자주 나는 오류

| 오류 | 의미와 대처 |
|---|---|
| `oversized_real_operation: ... estimated N bytes exceed limit` | 계산 전에 추정 메모리가 `max_operation_bytes`(기본 8 GiB)를 넘어 멈췄다. 아무것도 공개되지 않았다. 메시지의 추정치 N에 여유를 더해(물리 메모리 이하) 두 `condition_assignment`에 넣고 다시 돌린다. 호스트 RAM만 확인하므로 컨테이너·스케줄러 한도가 더 작으면 그 이하로 직접 낮춘다. 4·5·6회차는 쌍·후보·창 크기를 줄여도 된다 |
| `oversized_real_bucket_output` | 한 layer×지름 그룹의 bucket 항목이 `max_bucket_output_entries`를 넘었다. 값을 올린다 |
| `oversized_real_label_patterns` | 겹침 패턴 항목이 `max_label_pattern_entries`를 넘었다. 값과 필요하면 `max_operation_bytes`를 함께 올린다 |
| `real_hotspot_output_exists`, `electrode_output_exists`, `output_dir_exists`(종료 코드 2) | `--out`이 이미 있다. 기존 폴더를 옮기거나 이름을 바꾸고 다시 돌린다. 기존 폴더는 건드리지 않았다 |
| `expected_case_folder_missing` | 오류가 아니라 진단이다(종료 코드 3). 기대 case 목록(두 모델 root에서 발견된 case 이름의 합집합)에 있는데 이 모델에는 case 폴더가 없다는 뜻이다. 그 case의 모든 지름이 누락으로 기록된다. 두 모델 모두에 없는 case는 찾을 수 없다 |
| `expected_case_inventory_unprovided` | import 때 기대 case 목록을 주지 않았다. coverage가 `unconfirmed`라 4회차 ROI가 모두 막히므로 `--expected-cases-from-roots`로 1회차부터 다시 한다 |
| `map_coverage_incomplete` | 지도 coverage가 불완전해 ROI가 막혔다. 기대 case 목록 없이 import했다면 1회차부터 다시 한다 |
| `electrode_role_required_for_matching` | role이 없는 전극이 있다. `electrode_role_policy: electrode_id`를 쓰고 3회차를 다시 돌려 sha256을 갱신한다 |
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
- 6회차 흐름(import → compare → 전극 asset → candidates → DL2 → confirm-pairs → compare-windows)은 같지만 Windows에서 실자료 전체 흐름은 아직 검증되지 않았다. 확인된 것은 설치·`pip check`·도움말까지다.
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
