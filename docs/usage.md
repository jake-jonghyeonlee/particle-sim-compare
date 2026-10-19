# numeric_v2 / real_hotspot_v1 실행 가이드

## Windows 폐쇄망 설치 — CMD만 사용

사용자가 폐쇄망에서 성공을 확인한 방식은 **명령 프롬프트(CMD) + 설치된 Python + 로컬 wheel**이다. 온라인 설치와 PowerShell 설치 스크립트는 제거했다. 가상환경 활성화, 관리자 실행, 실행 정책 변경이 필요하지 않다.

1. Git에 포함된 [`bundles/windows_cp312_win_amd64.zip`](../bundles/windows_cp312_win_amd64.zip)을 폐쇄망으로 옮긴다.
2. 파일 탐색기 주소창에 `%USERPROFILE%`을 입력하고 `simcompare-offline` 폴더를 만든다. ZIP을 풀어 아래 구조로 준비한다. 기존 가상환경을 삭제하거나 덮어쓰지 말고 새 설치는 새 폴더를 사용한다.

```text
%USERPROFILE%\simcompare-offline\windows_cp312_win_amd64\
    install_offline.cmd
    verify_windows_bundle.py
    requirements-windows.txt
    manifest.json
    wheelhouse\
    fixtures\
```

3. 시작 메뉴에서 **명령 프롬프트**를 열고 아래 명령을 한 줄씩 실행한다. 실제 해제 경로가 다르면 첫 줄을 바꾼다. PC에는 **CPython 3.12 x64와 Python Launcher (`py.exe`)**가 설치되어 있어야 하며 Python 설치 파일은 ZIP에 포함되지 않는다.

```bat
cd /d "%USERPROFILE%\simcompare-offline\windows_cp312_win_amd64"
py -3.12 -m venv .venv-cmd
.venv-cmd\Scripts\python.exe verify_windows_bundle.py .
.venv-cmd\Scripts\python.exe -m pip --disable-pip-version-check install --no-index --only-binary=:all: --require-hashes --find-links=wheelhouse -r requirements-windows.txt
.venv-cmd\Scripts\python.exe -m pip check
.venv-cmd\Scripts\python.exe -m simcompare --help
```

**중간에 오류가 나면 다음 명령을 실행하지 않는다.** `wheel integrity OK`, `No broken requirements found`, simcompare 도움말이 표시되면 설치·CLI 확인 완료다. 이 과정만으로 실제 데이터나 수치 검증이 완료된 것은 아니다. 위 수동 절차는 사용자 실제 성공 확인이 있다.

같은 절차를 묶은 `install_offline.cmd`도 동봉했다. 같은 CMD 폴더에서 `install_offline.cmd`로 실행한다. 기존 `.venv-cmd`가 있으면 삭제하지 않고 Python 버전을 확인해 사용하며, 어느 단계든 실패하면 중단한다. 이 자동화 파일 자체의 Windows 실행은 아직 별도 확인이 필요하다. 이미 수동 설치에 성공했다면 재설치 없이 다음 단계로 간다.

## 실제 데이터 첫 실행

이후 명령도 위 압축 해제 폴더에서 실행한다. 경로는 실제 데이터 위치로 바꾸고, import 출력은 아직 없는 새 폴더를 지정한다. 원본은 수정하지 않는다.

```bat
.venv-cmd\Scripts\python.exe -m simcompare import-hotspots --root "D:\data\input\hotspot" --out "D:\data\imported\reference"
```

저장소의 [`configs/real.example.yaml`](../configs/real.example.yaml)을 `D:\data\real.yaml`로 복사한다. reference/target/map의 data_dir를 실제 변환 폴더로 설정하고, 확인된 layer·조건·pixel/line 분류표만 입력한다. 원본 YAML의 상대 경로는 복사한 설정 파일 기준이므로 실제 경로로 수정해야 한다.

```bat
.venv-cmd\Scripts\python.exe -m simcompare validate-data --config "D:\data\real.yaml"
.venv-cmd\Scripts\python.exe -m simcompare compare --config "D:\data\real.yaml"
.venv-cmd\Scripts\python.exe -m simcompare electrode-map --config "D:\data\real.yaml"
```

## 합성 예제 실행 (선택)

```bat
.venv-cmd\Scripts\python.exe -m simcompare demo-data --out demo_data
.venv-cmd\Scripts\python.exe -m simcompare validate-data --config demo_data/config.yaml
.venv-cmd\Scripts\python.exe -m simcompare compare --config demo_data/config.yaml
.venv-cmd\Scripts\python.exe -m simcompare electrode-map --config demo_data/config.yaml
```

`demo-data`는 numeric_v2 합성 데이터만 만든다. 실제 데이터 검증을 대신하지 않는다.

## 대응 위치와 국부 ΔCA (compare-windows)

비슷한 두 제품에서 설계자가 짝지은 위치의 **같은 물리 창** 안 CA를 비교한다([PLAN_v3](plan/PLAN_v3_local_delta_ca.md)). 창은 유닛 크기가 아니라 ROI 중심 주변 정사각형의 한 변(µm)이며 기본값이 없다. 대응 위치는 `roi_correspondence_v1` JSON으로 준다([데이터 계약](data_contract.md)). reference가 a, target이 b이고 방향은 b − a다. 유닛 크기가 달라도 같은 물리 창이므로 절대 비교가 된다.

```bat
.venv-cmd\Scripts\python.exe -m simcompare.correspondence validate --correspondence "D:\data\pairs.json"
.venv-cmd\Scripts\python.exe -m simcompare compare-windows --config "D:\data\real.yaml" --correspondence "D:\data\pairs.json" --window-sizes 10 20 30 --sort-diameter-um 5 --out "D:\data\local"
```

출력:

| 파일 | 내용 |
|---|---|
| `local_delta.json`, `local_delta_rows.csv` | 쌍 × 창 크기 × 32 지름 × {window, attributable_core} × {union, lateral_only, vertical_only, simultaneous}의 a / b / Δ, 창 면적 비율, 상태·사유. endpoints가 있으면 named_pair(그 case 자체의 창 안 CA) 추가 |
| `window_sensitivity.json/csv` | 창 크기별 Δ_union, 부호 일치, 크기 쌍 간 \|Δ\| 순위 상관, core 적용 가능 지름. 추천값은 없고 사용자가 G4를 정한다 |
| `local_delta.html` | 쌍마다 a/b layout crop과 hotspot crop(조각 색), a/b/Δ 표. 정렬 지름의 \|Δ_union\| 내림차순. PNG 내장이라 `file://`로 열린다 |

소요 시간은 개발기 합성 기준으로 2000×2000 frame에서 쌍·창당 약 0.13 s이고, 그 앞의 group 계산(`compare`와 같은 비용)이 case 수에 비례해 더 크다. `--compact-json`을 주면 JSON에서 행별 세부 지표를 빼고 CSV에만 남긴다(쌍 하나·창 하나가 약 0.4 MB이므로 수십 쌍 × 여러 창일 때 쓴다). 규칙: attributable core는 한 변 (창 − 지름)의 안쪽 정사각형이며 창 ≤ 지름이면 not_applicable이다. 창이 frame 밖으로 나가면 `window_clipped_<side>`, valid 구멍을 포함하면 `valid_incomplete_<side>` 사유로 해당 Δ는 null이고 실행은 exit 3이다. 원천 누락·미매핑은 창 측정도 partial로 만든다. **실자료에서는** `import-hotspots --expected-cases`로 case 목록을 확인하고 config에 `condition_assignment`와 pair 표를 넣어야 group이 available이 되며, 그 전에는 모든 창이 partial이고 Δ는 null이다. 대응 파일의 `repeats_per_frame`은 측정 2×2 frame 안의 반복 수이며 per-unit 수를 넣지 않는다. 대응 파일의 endpoints 순서는 export-review-rois가 case와 같은 순서로 비교하므로 case의 순서대로 적는다(창 측정 자체는 순서를 보지 않는다). `--jitter-um j`를 주면 중심을 ±j 이동한 네 창의 Δ_union 범위와 부호 일치를 기록한다. DL2 검색에서 온 쌍의 점수는 출처 표시일 뿐 수치에 들어가지 않는다.

같은 실행의 `compare` 결과에는 `fraction_of_frame`(면적 / 2×2 frame 면적, 단위 없음)이 group별·delta별로 추가된다(`comparison_fractions.csv`, HTML의 "frame 면적 비율"). G2의 한 unit 환산 없이 유닛 크기가 다른 제품을 비교하는 제품 수준 지표다.

권장 운용값(코드 기본값이 아니며 실행 때 명시한다): 정렬·썸네일 지름은 전극 수 지도의 대표 지름과 같은 **5 µm**, 창 후보는 **10 / 20 / 30 µm**(밀도가 큰 1~10 µm 이물에서 창 반폭 > d/2가 되는 크기), jitter는 **0.5 µm**(5 px, 라벨 찍기 오차 수준), DL2 위치 hit 허용오차는 **선택한 창의 반폭**. 민감도 표와 설계자 검토 결과에 따라 바꾼다.

대응 파일 변환(ROI 왕복, DL 라벨, DL2 결과 받기, 후보 커버리지):

```bat
.venv-cmd\Scripts\python.exe -m simcompare.correspondence to-review-rois --config "D:\data\real.yaml" --correspondence "D:\data\pairs.json" --side reference --out "D:\data\real_a.yaml"
.venv-cmd\Scripts\python.exe -m simcompare export-review-rois --config "D:\data\real_a.yaml" --out "D:\data\rois_a"
.venv-cmd\Scripts\python.exe -m simcompare.correspondence to-review-rois --config "D:\data\real.yaml" --correspondence "D:\data\pairs.json" --side target --out "D:\data\real_b.yaml"
.venv-cmd\Scripts\python.exe -m simcompare export-review-rois --config "D:\data\real_b.yaml" --out "D:\data\rois_b"
.venv-cmd\Scripts\python.exe -m simcompare.correspondence attach-roi-ids --correspondence "D:\data\pairs.json" --export-a "D:\data\rois_a\review_rois.json" --export-b "D:\data\rois_b\review_rois.json" --out "D:\data\pairs_ids.json"
.venv-cmd\Scripts\python.exe -m simcompare.correspondence to-dl2-relations --correspondence "D:\data\pairs_ids.json" --out "D:\data\dl2_relations.json"
.venv-cmd\Scripts\python.exe -m simcompare.correspondence to-dl1-relations --correspondence "D:\data\pairs.json" --out "D:\data\dl1_relations.csv"
.venv-cmd\Scripts\python.exe -m simcompare.correspondence from-dl2-search --search "D:\data\dl2_search.json" --index "D:\data\dl2_index.json" --query-export "D:\data\rois_a\review_rois.json" --index-export "D:\data\rois_b\review_rois.json" --out "D:\data\pairs_dl2.json"
.venv-cmd\Scripts\python.exe scripts\check_candidate_coverage.py --config "D:\data\real.yaml" --correspondence "D:\data\pairs.json" --min-counts 2 3 4 --out "D:\data\coverage"
```

endpoints가 없는 쌍의 manual 요청은 export에서 `case_context_required`로 blocked가 된다. `to-dl2-relations`는 양쪽 roi_id가 있는 ready 쌍만 `similar`로 쓴다. `check_candidate_coverage.py`는 라벨 위치가 전극 수 지도 영역 안에 드는 비율을 threshold별로 보고하는 읽기 전용 점검이다.

## 통합 HTML 보고서 (report)

이미 만든 출력 폴더를 읽어 **HTML 한 파일**(PNG는 base64, 그래프는 inline SVG, 외부 URL·`<script src>` 없음, `file://`로 열림)을 만든다. 수치는 다시 계산하지 않고 수율·밀도 단위 환산(보류)도 넣지 않는다. 계획: [report_html_plan_v1](plan/report_html_plan_v1.md).

```bat
.venv-cmd\Scripts\python.exe -m simcompare report --compare-dir "D:\data\numeric_outputs" --map-dir "D:\data\numeric_outputs" --windows-dir "D:\data\local" --rois-dir "D:\data\rois_a" --out "D:\data\report" --diameter-um 5 --rank-window-um 20 --top-n 10
```

| 인수 | 설명 |
|---|---|
| `--compare-dir` | 필수. `compare` 출력(`comparison.json`, `comparison_fractions/summary/failure_pieces.csv`) |
| `--map-dir`, `--windows-dir`, `--rois-dir` | 선택. 각각 `electrode-map`, `compare-windows`(crop·전체 frame PNG가 있는 출력), `export-review-rois` 출력. 주지 않으면 해당 섹션에 "입력 없음"이 표시되고 종료 코드에는 영향이 없다 |
| `--diameter-um` | 대표 지름(기본 5) |
| `--rank-window-um` | 악화/개선 순위 기준 창(기본 20). **임시 기준값**이며 제품마다 다를 수 있다. `local_delta.json`의 `window_sizes_um` 중 하나여야 한다 |
| `--top-n` | 악화/개선 각각 표시할 쌍 수(기본 10) |

출력: `report.html`, `report.json`(보고서에 쓴 값과 입력 파일 SHA256), `execution.json`.

- A: 표지, 핵심 지표 카드(쉬운 말 설명·출처, "지표 읽는 법"), 악화(Δ>0)·개선(Δ<0) 상위 쌍, 판정 조건. Δ = 0인 쌍은 개수만 센다. 색은 악화 빨강, 개선 파랑이고 그래프의 a는 회색, b는 보라다.
- B: frame 면적 비율, line/pixel/조각, 대응 위치 상세(제품별 전체 frame 위치도와 쌍별 locator·crop), 창 민감도, 취약 위치 지도, 검토 ROI, 상태·누락, 출처.
- 같은 제품·frame·layer에서 순위 기준 창이 겹치는 쌍은 겹침 면적(µm²)과 함께 표시한다. **겹친 쌍의 Δ를 단순 합산하지 마세요.** 값은 바꾸지 않는다.
- 종료 코드: 0 = 완료, 3 = 입력이 partial이거나 값 없는 쌍·blocked 쌍·crop 누락이 있음, 2 = 입력 오류(폴더 사이 제품 불일치, `is_synthetic` 혼합, 순위 창·지름이 없음(사용 가능한 값을 메시지로 알림), 예상 밖 `schema_version`, 필수 파일 없음, `--out`이 입력 폴더). 2이면 `report.html`을 남기지 않고 `error.json`/`execution.json`만 둔다.
- 데모처럼 frame이 작으면 창 20 µm가 clipping되므로 작은 창으로 `compare-windows`를 돌리고 같은 값을 `--rank-window-um`에 준다(`scripts/2_demo.sh` 참고).

## 전극 PNG → review_assets_v1 (import-electrode-assets)

실제 전극 PNG를 `export-review-rois`가 읽는 `review_assets_v1` manifest와 asset 파일로 바꾼다(새 스키마 아님). 출력 디렉터리는 새 경로여야 하고 입력은 수정하지 않는다.

```bat
.venv-cmd\Scripts\python.exe -m simcompare import-electrode-assets --source-kind dl1_png_folder --root input/dl1 --numeric-data-dir imported/reference --source-version LAYOUT_V1 --max-asset-memory-mb 4096 --out assets/reference
.venv-cmd\Scripts\python.exe -m simcompare import-electrode-assets --source-kind hotspot_case_electrode --root input/hotspot --numeric-data-dir imported/reference --condition-assignment assignment.json --cnt-from-dl1-root input/dl1 --source-version LAYOUT_V1 --max-asset-memory-mb 4096 --out assets/reference
```

- `--numeric-data-dir`(필수)는 case identity·endpoint·failure_type·frame·valid·input_hash의 유일한 출처다. numeric_v2(합성) 폴더이거나 real_hotspot_v1 폴더 + `--condition-assignment`다. 어댑터는 값을 만들지 않고 endpoint `(product_id, electrode_id, actual_layer)`를 이름이 정확히 같은 PNG에 묶는다. 대소문자만 다른 layer, PNG 없음, case 이름·endpoint 불일치는 그 case만 blocked(변환 기록의 `blocked_cases`, 종료 코드 3)이고 manifest에 넣지 않는다. 변환 가능한 case가 없으면 종료 코드 2다.
- `dl1_png_folder`: `<root>/<model>/<electrode>/<n>-<layer>.png`, `<root>/<model>/cno/<n>_<layer>.png`. 모델 폴더 이름 = product_id. 파일 번호는 출처 기록일 뿐이다. CNT asset은 그 모델 cno 마스크들의 합집합이며 디코딩 마스크 hash로 식별한다. `--stack-order`(dl1_png_stack_order_v1)를 주면 모든 이름이 순서 파일에 있어야 하고 대소문자 충돌은 거부한다. 순서는 이 파일에서만 오고 추정하지 않는다.
- `hotspot_case_electrode`: case 폴더(이름 = case_id)의 `1.png`는 endpoint 1, `2.png`는 endpoint 2다. 둘의 shape가 다르면 `electrode_shape_mismatch`다. 이 폴더에는 CNT가 없으므로 `--cnt-from-dl1-root`로 DL1 모델의 cno를 지정하지 않으면 case는 `cnt_not_provided`로 blocked다(0 배열로 채우지 않는다).
- 극성은 출처별 계약 표다: 두 종류 모두 검정 = 형상, `binary_zero_is_marked`, `polarity_basis: user_answer_2026_10_19`. 표에 없는 종류는 blocked `electrode_polarity_unspecified`(종료 코드 3). 회색·유색·투명·16비트·EXIF 회전·애니메이션·손상 PNG는 파일 이름과 함께 거부하며 threshold가 없다. asset은 mode L PNG {0,255}(0 = 형상)다.
- frame: PNG는 전체 frame(0.1 µm/pixel, 원점 (0,0))이고 dataset frame은 hotspot crop(원점 (50,50) µm, 축마다 1000 px 작음)이다. 어댑터는 dataset frame 원점·pitch와 `PNG shape = frame shape + 1000`을 검사하고(`layout_hotspot_frame_mismatch`, `electrode_frame_mismatch`) 중앙 500 px 테두리를 정확히 잘라낸 부분만 내보낸다. **알려진 한계**: 50 µm 테두리 문맥은 asset에 없어서 DL2의 near/wide 창이 그 바깥을 볼 수 없다.
- 같은 마스크는 한 번만 디코딩·기록한다. `--max-asset-memory-mb`(필수)는 보수적 추정치(측정 아님)와 비교하며 초과하면 아무것도 쓰지 않고 추정치와 함께 종료 코드 2다.
- 출력: `review_assets.json`, `assets/*.png`, `electrode_asset_import_record.json`(source_kind, polarity_basis, 파일별 hash, adapter 버전, input hash, 개수, 경고). 같은 입력은 바이트까지 같다.
- **실자료 export (T1b)**: `export-review-rois`는 mode=real에서 `map.input`에 `condition_assignment`, `asset_manifest`({path, sha256}), `electrode_import_record`({path, sha256}, 이 두 키만 허용)가 **모두** 있고 증거가 맞을 때만 real_hotspot_v1을 처리한다(`configured_dataset` → electrode-map 영역 → `review_roi_v1`; 영역은 `--min-count`/`map.min_count`가 있어야 생기고 hotspot 마스크 자체는 ROI가 되지 않는다). 두 파일은 `data_dir` 안에 있어야 한다(`import-electrode-assets` 출력을 data_dir로 복사). 수용 조건: 기록과 manifest가 `polarity_basis`를 가지며 계약 표의 출처 종류와 값이 같고, 모든 asset의 `value_semantics`가 `binary`/`binary_zero_is_marked`이며, manifest의 `source_manifest_hash`가 dataset input hash와 같고 기록의 `manifest_sha256`이 manifest 파일과 같다. 키가 하나라도 없거나 `polarity_basis`가 없으면 예전처럼 `unavailable`(`electrode_polarity_unknown`, `source_layout_assets_unconfirmed`, 종료 코드 3)이다. 출처 종류·`polarity_basis`가 표와 다르거나 지원하지 않는 `value_semantics`는 `blocked`(`electrode_polarity_unspecified`, 코드 3, ROI 없음), sha256·identity·기록 불일치는 이름 있는 오류(코드 2)다. 영역 coverage가 불완전하면(예: `import-hotspots --expected-cases` 없음) 그 ROI는 `map_coverage_incomplete`로 blocked다.

## 취약 영역 후보 (candidates)

두 제품(`config.reference`, `config.target`)의 electrode-map 영역을 **양방향**으로 뽑아 질의 ROI로 정리하고, 바로 매칭에 쓸 export와 깔때기 표를 만든다. 점수·CA·Δ·확정·`mutual/one_way`는 다루지 않는다(후속 Task).

```bat
.venv-cmd\Scripts\python.exe -m simcompare candidates --config config.yaml --min-count 3 --window-um 20 --merge-overlap 0.5 --out candidates_k3
```

- `--min-count K`, `--window-um W`, `--merge-overlap R`은 **필수이며 기본값이 없다**. `--max-candidates N`은 선택이며 방향(제품)별 질의 수 상한이다. 대표 지름은 `map.diameter_um`(없으면 electrode-map 기본 5 µm)을 쓴다. 출력 폴더는 **새 경로**여야 하고 이미 있으면 아무것도 건드리지 않고 종료 코드 2다.
- 설정: `reference`/`target` 각각 단일 제품(`product_id`, `data_dir`). numeric_v2(합성/데모)는 `asset_manifest`를, real_hotspot_v1은 `condition_assignment`, `asset_manifest`, `electrode_import_record`(export-review-rois와 같은 조건)를 쓴다. 새 설정 키는 없다. 둘은 같은 종류여야 한다(`mixed_real_and_numeric_source_formats`).
- 병합(R3): 같은 제품·design·frame의 영역(layer 무관)을 region_id 순서로 union-find(전이적)한다. 각 영역의 창은 `centre_xy_um`을 중심으로 한 변 W의 정사각형이고, 두 창의 `intersection/min(area)`가 R 이상이면 합친다(모든 창이 같은 크기이므로 `intersection/W²`). 겹침은 frame으로 자르지 않은 공칭 창으로 판정한다. 대표 = `max_count`가 가장 큰 영역(동률은 면적 큰 쪽, 그다음 region_id). `merged_from`은 대표를 포함한 구성원 region_id 목록이고 병합이 없으면 자기 하나다. frame 경계를 넘는 창은 버리지 않고 `flags: window_clipped`로 표시한다(대표가 아니라 구성원만 넘으면 `merged_member_window_clipped`).
- export: 대표 region만 `map_region_ids`로 기존 경로(numeric 또는 real T1b)에 넘겨 `reference_export/review_rois.json`, `target_export/review_rois.json`을 만든다. 한 제품의 export는 그 제품 질의 집합이면서 반대 방향 실행의 index 입력이다(`candidate_queries_v1.json`의 `directions[].query_export/index_export`). real인데 전극 증거가 없으면 export 없이 질의가 `electrode_polarity_unknown`, `source_layout_assets_unconfirmed`로 blocked된다.
- **index export(T1c)**: 질의 export는 선택한 영역의 case 전극 layout만 참조하므로 DL2 `role_layer_unions`(ready ROI 행의 layout 채널 합집합)가 부분적이다. 그래서 제품마다 `reference_index_export/`, `target_index_export/`를 추가로 만든다. 데이터셋 case마다 명시 manual ROI 하나(identity·endpoints·failure_type은 데이터셋 그대로, `center_xy_um` = frame 중심이 valid면 그것, 아니면 row-major 첫 valid 셀 중심, valid 셀이 없으면 그 case는 `index_frame_has_no_valid_cell`로 blocked)를 같은 경로(numeric 또는 real T1b)로 내보낸다. **index 전용이다: 이 행들은 질의가 아니고 region provenance가 없다**(`source_kind: manual`, `position_policy: explicit_manual_point`). 매칭 입력은 `execution.json`/`candidate_queries_v1.json`의 `match_inputs`: `reference_to_target = {query: reference_export, index: target_index_export}`, `target_to_reference = {query: target_export, index: reference_index_export}`.
- **`index_union_partial`**: 질의 export를 index로 쓰면 안 된다는 정보 메모(`notes`, `severity: info`)일 뿐이며 종료 코드에 영향이 없다.
- **index coverage 깔때기**: 제품별 `funnel.index_coverage`(= `funnel.json`, funnel.csv의 `index_<product>` 행): `dataset_cases`, `index_rois_ready`, `index_rois_blocked`와 사유(`blocked_reasons`), `electrodes_covered/electrodes_total`(전체 및 `by_role_layer`의 `role|layer`별). 하나라도 덮이지 않았거나 blocked ROI가 있으면 `index_coverage_incomplete` 경고와 종료 코드 3이다.
- 출력: `candidate_queries_v1.json`(schema_version, `inputs`의 config·manifest·asset_manifest·import record·export sha256, `args`, 방향별 `queries`[query_id, product_id, frame_id, center_xy_um, region_id, merged_from, max_count, area_um2(영역 면적, CA 아님), layers, flags, status, reasons, roi_ids], `funnel`, `warnings`, `notes`, `match_inputs`), `funnel.json`/`funnel.csv`, `reference_export/`, `target_export/`, `reference_index_export/`, `target_index_export/`, `execution.json`. 같은 입력·인수는 같은 바이트를 만든다(경로는 출력 폴더 상대).
- 깔때기: 방향별 `regions` → `merged_queries`(탈락 사유 `merged_overlap`) → `after_max_candidates`(`cut_by_max_candidates`는 `--max-candidates`로 잘린 수, 인수가 없으면 0이고 침묵 절단은 없다) → `exported_ready`(탈락은 export 사유 코드별 질의 수; 한 질의가 여러 코드를 가질 수 있어 합이 `dropped`보다 클 수 있음). 질의는 ready ROI 행이 하나라도 있으면 `ready`, 일부 행만 blocked면 `roi_rows_partially_blocked` 플래그와 사유가 남는다.
- 종료 코드: 0 = 모든 질의 ready이고 index coverage 완전(경고 없음), 3 = blocked/partial 질의, `index_coverage_incomplete`, 지도 coverage 불완전, 미해결 동명 case, side blocked, 2 = 입력·설정 오류(필수 인수 누락 포함). 실패하면 이 실행이 만든 파일과 폴더만 지우며 이미 있던 것은 지우지 않는다.

## 종료 코드

| 코드 | 의미 | 확인 |
|---|---|---|
| 0 | 요청 범위의 결과 available 또는 생성 완료 | execution.json과 결과 provenance |
| 3 | partial/unavailable/blocked 항목을 포함한 사용 가능한 부분 출력 | status, reasons, coverage, partial_value |
| 2 | 입력/설정 불가 또는 지도 incomplete/unavailable | 오류 메시지, error.json; 지도 리포트 없음 |

`compare-windows`는 모든 ready 쌍의 창 Δ가 available(또는 근거 있는 not_applicable)이면 0, 창 clipping·valid 구멍·원천 누락·blocked 쌍이 있으면 3, 대응 파일·창 크기·제품 불일치 오류는 2다.

입력 오류 시 출력 디렉터리의 관리 리포트(comparison/electrode_map/validation/review_rois/local_delta/window_sensitivity JSON·CSV·HTML·PNG·NPZ)를 제거하고 error.json/execution.json만 남긴다. 성공 또는 허용된 부분 비교 실행 후에는 stale error.json을 제거한다. electrode-map은 incomplete/unavailable이면 exit 2이고 리포트 세트를 쓰지 않는다. 기본 지도·ROI 출처 선택과 가중값 미확정 여부는 서로 독립이다.

## 결과 읽기

`comparison.json` 및 CSV/HTML은 line / pixel, pair exclusive/shared, transfer, 원 case CA, 지름/coverage, provenance를 기록한다. 세 failure pieces는 line / pixel과 다른 축이다. shared bucket이 pair별 CSV에 반복되더라도 총계에는 한 번만 포함된다. partial/unavailable의 value는 null이며 부분 합은 partial_value다. 관측 0은 available 0이고 누락을 대신하지 않는다.

`electrode_map.json`은 한 제품의 layer별 count와 product maximum의 작은 summary다. counts와 valid는 압축 NPZ 상대 경로로 연결하고 shape/pitch/origin, count histogram, status/provenance와 파일 hash를 기록한다. 기본 셀별 CSV·JSON 배열·HTML 배열은 없다. `--per-cell-text`는 4096 cells 이하에서만 명시 셀별 텍스트를 추가한다. PNG는 정수 count마다 다른 색과 legend를 쓰고 sidecar는 원 frame, um extent, integer display scale을 기록한다. 2×2 domain을 그대로 표시한다. 기본 regions=[]이며 명시 --min-count 또는 map.min_count에서만 영역을 생성한다. 다른 제품의 지도는 별도 실행한다.

`review_rois.json`의 기본 출처는 review_rois.manual이다. product/design/source/step/exposure/case/frame/version과 원 shape 문맥을 모두 지정해야 ready다. 문맥이 부족하면 blocked 이유를 보존한다. 지도 영역을 쓰려면 threshold와 review_rois.map_region_ids를 명시한다. crop 크기는 정하지 않으며 DL2가 선택한 위치와 유사 구조를 찾는 별도 역할을 담당한다. [ROI 교환 계약](review_roi_contract.md)을 따른다.

## 실제 자료

확인된 실제 폴더 구조는 먼저 별도 `real_hotspot_v1` 관측 manifest로 변환한다. 출력 디렉터리는 새 경로여야 한다.

```bat
.venv-cmd\Scripts\python.exe -m simcompare import-hotspots --root input/hotspot --out imported/reference --expected-cases expected_cases.txt
```

`--expected-cases`는 case 이름을 줄마다 적은 선택 파일이며, 없으면 발견되지 않은 case folder coverage는 unconfirmed로 남는다. 입력 hotspot은 opaque grayscale PNG여야 한다. 검정=0, 모든 회색 nonzero=occupied다. 변환기는 1/2 electrode image shape와 hotspot shape의 정확한 1000 px 축 차이를 검사하고 중앙 frame `(50,50)` µm로 기록한다. case-local `result/<불량명>.csv`의 원 숫자/정밀도와 PNG 면적 차이를 보존한다. 누락 image는 missing, 검정 image는 observed area 0이다.

`validate-data`는 관측 면적과 missing/CSV 진단을 출력하고 `compare`는 raw 면적과 상태를 출력한다. config에 확인된 `condition_assignment`와 선택 pair table을 제공하면 기존 line/pixel 면적 집계와 electrode map을 연결한다. assignment에는 `product_id`, `design_version`, `source_id`, `process_step_id`, `exposure_id`, `unit_id`, `layer_stack`을 넣고 pair table은 기존 `pairs.csv` 열을 사용한다. 값이 없으면 null로 남기고 grouping을 요구하는 결과는 unavailable이다. pair table이 없는 pair를 pixel로 자동 배정하지 않는다. density 단위/조사면적 및 한 unit 환산이 없으면 weighted rate, panel, delta는 unavailable이며 electrode image polarity가 확인되지 않은 경우 review ROI만 blocked다. Electrode map은 hotspot mask와 explicit case assignment를 사용하며 polarity를 요구하지 않는다. configured mask materialization은 group 500,000,000 cells, 전체 source 6,000,000,000 cells, 보수적 whole-operation 추정 8 GiB로 제한한다. 마지막 제한은 모든 source mask, diameter별 retained output, per-case transient margin을 추정하며 streaming 보장은 아니다. `max_operation_bytes`는 낮출 수 있고 8 GiB보다 높일 수 없다. 초과 입력은 명시 오류로 중단된다. Windows 설치·pip check·도움말 실행은 사용자 성공 확인이 있으며, 실자료 수치·메모리 검증은 별도 수행해야 한다.

실제 adapter가 미확정이면 명시 오류가 정상이며 임의 synthetic 표시로 우회하지 않는다. [데이터 계약](data_contract.md), [질문 상태](open_questions.md)를 확인한다.

## legacy export 보존

```bat
.venv-cmd\Scripts\python.exe -m simcompare merge-exports --out merged export_a export_b
```

merge-exports는 기존 1.x CSV/JSON/배열의 source IDs와 raw ordered tuple을 보존한다. particle-sim 검사 wrapper의 parquet/zarr reader를 위해 pyarrow와 zarr를 직접 의존성으로 선언한다. 기존 wrapper의 --with 옵션도 호환 유지한다. 이 검사는 numeric 변환이나 실제 수치 비교 승인을 뜻하지 않는다. `schema.py`, `adapters/example.py`, `preprocess.py`는 legacy export 검산용으로 유지한다.

## 검증 범위와 개발 기록

설치용 ZIP에는 실행 의존성이 포함되며 pytest 같은 개발 도구는 포함되지 않는다. Windows 16GB에서 수백 실제 이미지 처리, 실자료 비교·지도·ROI 검증은 남아 있다. train/embed/analyze/evaluate/all은 폐기되었고 compare/electrode-map을 사용한다.

배포 파일을 만드는 유지보수 절차는 [Windows 묶음 빌드](windows_bundle_build.md)에 별도로 기록한다. 과거 계획·리뷰의 PowerShell 설명은 당시 검증 이력이며 현재 설치 지침이 아니다. R3 검토의 애플리케이션 wheel은 유지되지만 설치 파일 교체로 ZIP 해시는 달라졌다.
