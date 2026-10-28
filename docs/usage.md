# simcompare 실행 가이드

두 제품(reference, target)의 hotspot 결과를 가져와 line/pixel 면적을 비교하고, 설계자가 짝지은 위치의 국부 ΔCA를 계산한다. 환경에 따라 아래 둘 중 한 절만 따른다.

- **ETX (Linux 서버)**: 큰 자료. 실행기 `.py` 파일만 올려서 6회차로 나눠 돌린다.
- **Windows 폐쇄망 (CMD)**: 설치 ZIP으로 오프라인 설치한 뒤 명령을 직접 친다.

모든 명령의 옵션, 오류 코드, 출력 스키마, 내부 동작은 [상세 레퍼런스](usage_reference.md)에 있다.

## 6회차 흐름

| 회차 | 명령 | 입력 | 출력 | 다음 회차 전 수작업 |
|---|---|---|---|---|
| 0 (선택) | `dv-template` | 제품 case 폴더 이름, 임피던스 DB(선택) | `dv_sheet.csv`, `new_electrodes.csv` | 전압 칸과 새 전극 임피던스를 사용자가 채움. 1~6회차와 독립. [상세](dv_template.md) |
| 0 (선택) | `dv-grade` | 채운 `dv_sheet.csv`, 제품 case 폴더 이름, `--product-id` | `dv_grades.csv`, `dv_grades_by_case.csv`, `dv_grade_manifest.json` | short 쌍별 ΔV·최대 타이밍·등급(None/Low/High) 계산. 1~6회차와 독립. [상세](dv_grade.md) |
| 1 | `import-hotspots` | hotspot 폴더(모델별) | `data/imported/reference`, `target` | `pairs.csv`를 두 출력 폴더 안에 복사 |
| 2 | `validate-data`, `compare` | `real.yaml` | `comparison.json`, `comparison.csv`, `comparison_masks.zip` | 3회차용 `assignment_<side>.json` 준비 |
| 3 | `import-electrode-assets` | 전극 PNG, assignment JSON | `data/assets/reference`, `target` | 출력을 각 `data_dir`로 복사하고 `real.yaml`에 경로·sha256 기입 |
| 4 | `candidates-change` | `real.yaml`, DL1 PNG root | `data/changes`(`alignment.html`, `alignment.json`, `direct/roi_correspondence.json`, `*_export`) | 인수 예시 값을 제품에 맞게 확인(선택 인수 `--merge-scope`·`--suppress-overlap`·`--electrode-png-root` 포함). 로그의 `candidates-change exit N`과 `alignment.html` 확인 |
| 4b (선택) | `candidates-dv` | `real.yaml`(3회차 뒤), 0b회차 `dv-grade` 출력 둘 | `data/changes_dv`(`vulnerability_v1.json`, `grade_maps/`, `*_export`) | 인수 예시 값을 제품에 맞게 확인. 로그의 `candidates-dv exit N` 확인. 4회차와 독립. [상세](dv_candidates.md) |
| 4c (선택) | `vulnerability-report` | 4b회차 출력 폴더 | `data/vulnerability_report/report.html` | 4b회차와 같은 서버에서 이어서 실행. [상세](dv_candidates.md) |
| (DL2) | 선택 | `data/changes/*_export/review_rois.json` | `data/dl2/ncc_*.json`, `decisions.csv` | 다른 제품 탐색이 필요할 때만 |
| 5 | `confirm-pairs` (DL2 경로 전용) | DL2 결과, `decisions.csv` | `data/confirmed/roi_correspondence.json` | `--labelled-by`를 실제 ID로 |
| 6 | `compare-windows` | 직접 대응 `data/changes/direct/roi_correspondence.json` (DL2 경로는 `data/confirmed/…`) | `data/local/local_delta.html` 등 | 없음 |

순서: 1 import → 2 validate/compare → 3 전극 asset → 4 `candidates-change` → (선택 DL2 → 5 `confirm-pairs`) → 6 `compare-windows`. 직접 대응을 쓰면 DL2와 5회차를 건너뛴다. 변경점 ROI(`candidates-change`)가 4회차 ROI 선정을 대체했다. ΔV 취약점 ROI는 선택 회차다: 0 `dv-template` → (시트 기입) → 0b `dv-grade`(제품마다) → 3회차 뒤 4b `candidates-dv` → 4c `vulnerability-report`. 예전 `candidates`(max_count, 비가중 취약점 ROI)는 참고용이다.

수치를 새로 만드는 것은 2회차(면적)와 6회차(창별 ΔCA)이고 나머지는 입력을 준비하고 짝을 고르는 단계다. 6회차 `compare-windows`는 reference가 a, target이 b이며 방향은 b − a다.

### 모델이 여럿일 때 (`confirm-pairs --models`, 선택)

reference·target 외에 비교할 모델(제품)이 더 있으면 `models.yaml`(`compare_models_v1`)에 적고 5회차에 `--models`를 더한다. `--models`가 없으면 5회차의 코드 경로와 출력은 그대로다. 예시 파일은 `configs/etx/models.example.yaml`이다.

- `models.yaml`은 `real.yaml`과 **같은 폴더**에 둔다(다르면 `models_config_dir_mismatch`, 종료 2). 모델 절의 상대 경로는 `real.yaml`의 reference/target 절과 같은 기준으로 읽는다. 모델 폴더는 reference와 같은 1·3회차 산출물(`manifest.json`, `pairs.csv`, 3회차 뒤 `review_assets.json` 등)이며 `data/models/<product_id>/`에 둔다.
- `data_dir/manifest.json`이 아직 없는 모델은 `assets_pending`이다. 후보는 `--list`에 `model_status`로 보이고, accept 하면 쌍 없이 `models/<product_id>/deferred.json`에 남고 종료 코드는 3이다. 자산이 오면 새 `--out`으로 다시 돌린다.
- 모델 후보는 target 질의(`target_to_reference`) ROI에서 나온 것만 쓴다. reference 질의의 모델 후보는 `candidate_model_query_not_target`이며 accept 하면 종료 2이다.
- 모델 쌍은 a = target 질의, b = 모델이다. 그래서 이 쌍으로 계산한 Δ는 모델 − target이다(reference 쌍은 target − reference).

```text
python -m simcompare confirm-pairs --list --config real.yaml --models models.yaml --candidates data/changes --match target_to_reference=data/dl2/search_batch.json
python -m simcompare confirm-pairs --config real.yaml --models models.yaml --candidates data/changes --match reference_to_target=data/dl2/search_batch.json --match target_to_reference=data/dl2/search_batch.json --decisions decisions.csv --labelled-by jdoe --mutual-tolerance-um 5 --out data/confirmed_models
```

모델 한 개의 창 비교는 `compare-windows`에 `--models`와 `--model`을 같이 준다. 두 인수와 `--out`이 모두 필요하고, a = config target, b = 모델이며 Δ = 모델 − target이다. `--out`은 reference 용 `data/local`과 다른 폴더(예 `data/local/models/<key>`)여야 하며, 인수 오류는 파일을 하나도 쓰지 않고 종료 2이다.

```text
python -m simcompare compare-windows --config real.yaml --models models.yaml --model QA4_C00 --correspondence data/confirmed_models/models/QA4_C00/roi_correspondence.json --window-sizes 20 --out data/local/models/QA4_C00
```

모델 쌍의 "창 안 관측 case 구성 차이"(둘 다 / target 에만 / 모델에만, 개수와 이름)는 같은 명령에 `--case-window-um`과 `--case-diameter-um`을 함께 주면 `window_cases.json`·`window_cases.csv`로 나온다. 창은 `--window-sizes` 안의 값이어야 하고 지름은 두 데이터셋에 있어야 하며 기본값은 없다. 목록·개수일 뿐 인과를 뜻하지 않는다([데이터 계약](data_contract.md)의 `window_cases_v1`).

```text
python -m simcompare compare-windows --config real.yaml --models models.yaml --model QA4_C00 --correspondence data/confirmed_models/models/QA4_C00/roi_correspondence.json --window-sizes 20 --case-window-um 20 --case-diameter-um 5 --out data/local/models/QA4_C00
```

`confirm-pairs --models`의 출력은 기존 파일(reference↔target 쌍만) 외에 `models/<product_id>/roi_correspondence.json`, `confirmed_pairs_sidecar.json`, `deferred.json`(pending 모델에 accept 가 있을 때만), `models/decisions.json`, `models/index.json`이다. 스키마는 [데이터 계약](data_contract.md)의 확정 쌍 절에 있다. 같은 봉투 파일을 두 `--match`에 줘도 모델 후보는 한 번만 센다.

## ETX (Linux 서버)

**준비물**: Python 3.8 이상, numpy, Pillow(DL1 환경에 이미 있음). 패키지 설치는 필요 없다. hotspot 입력 폴더(모델 폴더 `QA4_R00`, `QA4_T00` 아래 `<case>/images/...`, `<case>/result/<불량명>.csv`)와 DL1 PNG 폴더를 서버에 둔다.

**실행기**: `bundles/etx/simcompare_job_<회차>_*.py` 중 쓸 회차의 파일을 서버로 복사한다. 설정 예시는 `configs/etx/`에 있고 대응표는 [configs/etx/README.md](../configs/etx/README.md)에 있다. 각 파일은 맨 위 `EDIT BELOW` ~ `EDIT ABOVE` 블록만 고친다.

- `JOBS`: `(명령, [인자...])` 목록. `python -m simcompare <명령> <인자...>`와 같다. 종료 코드 0·3이면 다음 작업으로 가고 그 밖이면 멈춘다.
- `BASE_DIR`: `JOBS`·`LOG_FILE`·`WORK_DIR`의 상대 경로 기준 폴더. 비워 두면 실행기가 있는 폴더다. wrapper가 `.py`를 다른 곳에 복사해 실행한다면 절대 경로를 적는다. 로그의 `BASE_DIR requested=... resolved=...; relative paths resolve from ...` 줄(시작 정보 몇 줄 안)에서 실제 기준 폴더를 확인한다. 작업마다 `--root`, `--config`, `--out` 같은 경로 인수가 `인수 '적은 값' -> 절대 경로` 줄로 찍힌다. 로그에는 `payload_sha256=`(묶인 코드 해시)와 numpy·Pillow 버전도 남고, 작업이 끝날 때마다 `<명령> exit N` 줄이 남는다.
- 경로는 `data/...`처럼 쓴다. `/data`처럼 `/`로 시작하면 루트 아래 절대 경로가 되어 권한 오류가 난다.
- 소스를 고친 뒤에는 `uv run python scripts/build_etx_stage_runners.py --overwrite`로 회차별 파일을 다시 만든다.

**회차별 실행**: 실행기를 하나 고르고 그 회차의 `JOBS`를 맞춘 뒤 실행한다. 출력 폴더(`--out`)는 1·3·4·5회차에서 매번 **새 경로**여야 한다(2회차 `output_dir`과 6회차 `data/local`은 한 세대로 통째로 교체되므로 다시 써도 된다).

1. 회차 1: 실행기 1. 두 모델을 각각 import하며 `--expected-cases-from-roots`로 다른 모델 root를 함께 준다. 기대 case 목록이 없으면 4회차의 모든 ROI가 `blocked`(`map_coverage_incomplete`)가 된다. 끝나면 `pairs.csv`를 `data/imported/reference/`와 `data/imported/target/` 안에 복사한다. `pair_table_path`는 `data_dir` 기준 상대 경로(`pairs.csv`)만 받는다(절대 경로·`..`·백슬래시는 거부).
2. 회차 2: `configs/etx/real.stage2.example.yaml`을 `real.yaml`로 복사해 `data_dir`(서버 절대 경로)와 필요한 값을 고친 뒤 실행기 2(`validate-data`, `compare`)를 돌린다. 종료 코드 3은 partial 항목이 있다는 뜻이므로 `reasons`를 본다.
3. 회차 3: `assignment_reference.json`, `assignment_target.json`을 만든다(`real.yaml`의 `condition_assignment`와 같은 내용, JSON에는 주석을 쓸 수 없다). 실행기 3을 돌리고 `--max-asset-memory-mb`는 예시값(16384)을 서버에 맞게 고친다. 끝나면 각 출력의 `review_assets.json`, `electrode_asset_import_record.json`, `assets/`를 해당 `data_dir`로 복사하고, `real.yaml`의 reference·target에 `asset_manifest`와 `electrode_import_record`(`{path, sha256}`)를 넣는다. sha256은 방금 복사한 파일 값이다. 전극 role은 `electrode_role_policy: electrode_id`로 pair 표의 전극 이름을 그대로 쓴다.
4. 회차 4: 실행기 4(`candidates-change`, 파일 이름은 `simcompare_job_4_candidates.py` 그대로). 두 제품의 CNT 3층을 한 offset으로 정렬하고 변경 위치 ROI를 방향별 top-k로 뽑는다. 필수 인수에는 기본값이 없고 EDIT 블록의 값은 **예시**다(탐색 반경 30 µm, 최소 Dice 0.3, 층 허용 1 µm, 변경 허용 0.5 µm, 창 20 µm, 병합 0.5, top-k 20, `--mask-derivation-rule between_stack_order_v1`): 제품에 맞게 바꾼다. 탐색 반경의 최소값은 pixel pitch의 2배 이상이고(코드 검사), 권장값은 예상 레이아웃 이동과 반복 패턴 주기를 모두 덮는 값이다. `--cnt-from-dl1-root`는 3회차와 같은 DL1 PNG root다. `--merge-overlap 0.5`는 두 창이 50 % 이상 겹치면 병합한다는 뜻이다(50 % 미만으로 겹친 창은 따로 남는다).
   - **선택 인수 3개**(생략하면 이전 동작): `--merge-scope all`은 층이 달라도 겹친 창을 하나의 ROI로 병합한다(생략하면 층마다 따로). `--suppress-overlap 0.3`은 병합 뒤 순위가 앞선 ROI와 30 % 이상 겹치는 ROI를 층 구분 없이 뺀다(생략하면 억제 없음). 실행기 예시에는 이 두 값이 들어 있고 **예시 값**이다. `--electrode-png-root <폴더>`는 alignment.html 층 묶음의 층별 전극 형상을 읽을 **DL1 전극 PNG root**다(구조 `<제품>/<전극>/<층>.png`). hotspot 폴더의 case 이미지는 쓰지 않는다. 실행기 예시는 `data/input/dl1`이며, 전극 PNG가 다른 곳에 있으면 그 폴더로 바꾼다(생략하면 `--cnt-from-dl1-root`).
   - **두 제품의 frame 크기가 다를 때**(예: unit size 차이로 2004 px와 2014 px): 오류가 아니다. 정렬 후 두 frame이 겹치는 공통 영역만 비교하고 resize하지 않는다. `alignment.json`에 두 frame 크기·차이·공통 영역(픽셀 수, 비율, 위치)이 기록되고 `candidate_queries_v1.json` notes에 `frame_shape_differs`(정보, 종료 코드 영향 없음)가 남는다. 공통 영역 밖 띠는 변경 후보가 되지 않는다.
   - **alignment.html 확인 순서**: 1절 요약(맨 위 정렬 상태 배지, frame 크기·공통 영역, 사분면 offset — 허용값 초과 칸은 빨강) → 2절 CNT 정렬 정렬 전·후 겹침(정렬 후 같은 패턴은 회색~검정, 남은 색이 변경) → 3절 변경 강조 지도(빨강 = 변경, ROI 번호) → 4절 ROI 목록 → 5절 ROI 상세(위치 그림, 불량 조합이 있는 층마다 비교·reference·target 묶음, 한글 기여 조합 표). "창 안 새로 생김/사라짐"은 그 창 안에서 한쪽 모델의 hotspot이 없다는 뜻이고 case 자체는 두 모델에 다 있을 수 있다(있는 모델 열). 정렬 상태가 `aligned`가 아니면(`aligned_uncertain`/`alignment_failed_identity_used`) 2절 그림을 먼저 확인한다.
   - 로그의 `candidates-change exit N`을 본다(실행기는 3도 '계속'으로 처리한다). 그림이 메모리 한도로 빠지면 `alignment_report_images_skipped` 또는 `alignment_report_layer_base_skipped`(층 묶음 바탕만 빠짐) 경고와 함께 3이며, 계산 결과(JSON)는 같다. 존재하지 않는 DL1 root는 쓰기 전에 종료 코드 2(`cnt_dl1_root_missing`)로 멈추고 `--out`을 만들지 않으며 실행기도 멈춘다(로그의 `오류:` 줄과 `--cnt-from-dl1-root '…' -> <절대 경로>` 줄을 본다). 질의·index export 폴더마다 DL2용 `mask_derivation.json`도 생긴다. 상세는 [상세 레퍼런스](usage_reference.md)의 "ROI 선정".
4b. 회차 4b(선택, 0b회차와 3회차 뒤): 실행기 4b(`candidates-dv`, 파일 이름은 `simcompare_job_4b_candidates_dv.py`). 0b회차의 `dv-grade` 출력 폴더 둘(제품마다 하나)과 3회차 뒤 `real.yaml`이 필요하다. 모든 인수가 필수이며 EDIT 블록의 값(K 4, `--roi-grade high`, 창 20 µm, 병합 0.5)은 **예시**다. 종료 코드 3은 '계속'으로 처리되므로 로그의 `candidates-dv exit N` 줄과 `candidate_queries_v1.json`의 `warnings`를 본다. 4회차와 독립이다. 상세는 [dv_candidates.md](dv_candidates.md).
4c. 회차 4c(선택, 4b회차 뒤): 실행기 4c(`vulnerability-report`, 파일 이름은 `simcompare_job_4c_vulnerability_report.py`). 4b회차 출력 폴더를 읽어 `data/vulnerability_report/report.html`을 만든다. 입력 기록의 `data_dir`(절대 경로)로 전극 layout을 다시 읽으므로 4b회차와 같은 서버에서 이어서 실행한다.
5. 6회차 입력 고르기: **직접 대응**을 쓰면 `data/changes/direct/roi_correspondence.json`이 6회차 입력이므로 DL2와 5회차를 건너뛴다. 다른 제품 탐색이 필요할 때만 아래 DL2 경로를 쓴다.
6. (DL2 경로 전용) DL2: DL2 저장소의 `bundles/etx/psdl2_job_baseline_ncc.py`를 같은 `BASE_DIR`에서 돌려 `data/dl2/ncc_r2t.json`, `ncc_t2r.json`과 `*_sheet/sheet.html`을 만든다(torch 불필요, `--allow-unversioned-producer` 사용). 입력은 `data/changes/*_export/review_rois.json`, `*_index_export/review_rois.json`이다. 시트를 보고 `data/dl2/decisions.csv`(헤더 `candidate_id,decision,note`, `decision`은 `accept` 또는 `reject`)를 쓴다.
7. (DL2 경로 전용) 회차 5: 실행기 5. 첫 작업이 `--list`로 `candidate_id` 목록을 로그에 찍고, 둘째 작업이 accept한 후보만 `data/confirmed/roi_correspondence.json`으로 바꾼다. `--labelled-by`의 `CHANGE_ME`를 실제 ID로 바꾼다. `decisions.csv`가 없으면 둘째 작업이 종료 코드 2로 멈춘다.
8. 회차 6: 실행기 6(`compare-windows`, 창 10/20/30 µm, jitter 0.5, 정렬 지름 5 µm). 기본 `--correspondence`는 `data/changes/direct/roi_correspondence.json`이고, DL2 경로를 썼다면 `data/confirmed/roi_correspondence.json`으로 바꾼다. `data/local/local_delta.html`에서 쌍별 결과를 본다. 창이 frame 밖으로 나가거나 valid 구멍을 덮으면 그 Δ는 null이고 종료 코드 3이다.

통합 `report`는 아직 실자료 `compare` 출력을 읽지 못해(`missing_input_file`, 종료 코드 2) ETX 회차에 넣지 않았다.

**회차 사이 수작업을 빠뜨리지 않는다**: 1회차 뒤 `pairs.csv` 복사, 3회차 뒤 import 결과 배치와 `electrode_import_record.sha256` 갱신.

**처음부터 다시 할 때**: `--out`은 없는 경로여야 하고 3·4·5회차도 기존 출력 폴더를 거부하므로, 다음 폴더를 먼저 옮기거나 이름을 바꾼다: `data/imported/reference`, `data/imported/target`, `data/assets/reference`, `data/assets/target`, `data/changes`, `data/confirmed`. `data/local`은 6회차가 한 세대로 교체하므로 그대로 둬도 된다. 그다음 1회차부터 위 수작업까지 모두 다시 한다.

**4회차만 다시 할 때**(4회차 코드나 인수만 바뀐 경우): 1~3회차 출력은 그대로 쓴다. 기존 `data/changes`를 옮기거나 이름을 바꾼 뒤 새 실행기 4를 돌린다. 그다음 직접 대응이면 6회차를, DL2 경로면 DL2 → 5회차 → 6회차를 다시 돈다. 실행기들은 묶인 코드가 모두 같으므로 `bundles/etx/` 안의 파일을 전부 같이 교체한다.

**실행기만 교체해서 이어갈 때**: 1회차 import 결과(`.npz`, 옛 `.npy`)는 그대로 읽힌다. 쓰는 실행기를 모두 새 파일로 바꾸고(`JOBS`, `BASE_DIR`은 다시 고친다) 2회차부터 다시 돈다. 이미 끝난 2회차 출력은 유효하지만, 새 실행기로 `compare`를 다시 돌려야 `comparison_masks.zip`이 생겨 6회차가 면적 계산을 건너뛴다. 4회차 이후 폴더는 새 경로를 쓴다.

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
| `oversized_real_operation: ... estimated N bytes exceed limit` | 계산 전에 추정 메모리가 `max_operation_bytes`(기본 8 GiB)를 넘어 멈췄다. 아무것도 공개되지 않았다. 메시지의 추정치 N에 여유를 더해(물리 메모리 이하) 두 `condition_assignment`에 넣고 다시 돌린다. 호스트 RAM만 확인하므로 컨테이너·스케줄러 한도가 더 작으면 그 이하로 직접 낮춘다. 4·5·6회차는 쌍·후보·창 크기를 줄여도 된다 |
| `oversized_real_bucket_output` | 한 layer×지름 그룹의 bucket 항목이 `max_bucket_output_entries`를 넘었다. 값을 올린다 |
| `oversized_real_label_patterns` | 겹침 패턴 항목이 `max_label_pattern_entries`를 넘었다. 값과 필요하면 `max_operation_bytes`를 함께 올린다 |
| `real_hotspot_output_exists`, `electrode_output_exists`, `output_dir_exists`(종료 코드 2) | `--out`이 이미 있다. 기존 폴더를 옮기거나 이름을 바꾸고 다시 돌린다. 기존 폴더는 건드리지 않았다 |
| `expected_case_folder_missing` | 오류가 아니라 진단이다(종료 코드 3). 기대 case 목록(두 모델 root에서 발견된 case 이름의 합집합)에 있는데 이 모델에는 case 폴더가 없다는 뜻이다. 그 case의 모든 지름이 누락으로 기록된다. 두 모델 모두에 없는 case는 찾을 수 없다 |
| `expected_case_inventory_unprovided` | import 때 기대 case 목록을 주지 않았다. coverage가 `unconfirmed`라 4회차 ROI가 모두 막히므로 `--expected-cases-from-roots`로 1회차부터 다시 한다 |
| `cnt_dl1_root_missing`, `electrode_png_root_missing`(종료 코드 2) | 4회차 `--cnt-from-dl1-root` 또는 `--electrode-png-root` 경로가 폴더가 아니다(빈 문자열 포함). `--out`을 만들지 않고 멈췄으므로 경로를 고쳐 같은 `--out`으로 다시 돌린다 |
| `alignment_report_images_skipped`, `alignment_report_layer_base_skipped`(종료 코드 3) | 4회차 alignment.html 그림(또는 층 묶음 바탕만)이 메모리 한도로 빠졌다. 표와 JSON 결과는 같다. 그림이 필요하면 `max_operation_bytes`를 물리 메모리 안에서 올린다 |
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
