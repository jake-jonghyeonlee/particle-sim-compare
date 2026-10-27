# candidates-dv, vulnerability-report — ΔV 등급 취약점 ROI

`candidates-dv` 는 두 제품의 전극쌍 ΔV 등급(`dv-grade` 출력)으로 가중한 취약점 영역을 뽑는다.
`vulnerability-report` 는 그 결과를 수치 근거 HTML 로 보여 준다.
등급을 모르는 비가중 순위(`count_all`)도 같은 표에 나란히 둔다.

## 흐름

```
0 dv-template → (사용자가 시트 기입) → 0b dv-grade(제품마다)
1 import-hotspots → 2 validate/compare → 3 import-electrode-assets
→ 4b candidates-dv → 4c vulnerability-report
```

4b 와 4c 는 변경점 ROI(4회차 `candidates-change`)와 독립이다. 출력 폴더도 따로 쓴다.
ETX 실행기는 `simcompare_job_4b_candidates_dv.py`, `simcompare_job_4c_vulnerability_report.py` 다.

## candidates-dv

```
python -m simcompare candidates-dv --config <real.yaml> --dv-grades-reference <폴더> --dv-grades-target <폴더> \
  --min-count K --roi-grade high|low_or_high --window-um W --merge-overlap R [--max-candidates N] --out <없는 새 폴더>
```

### 입력 3종

1. `--config`: reference·target 모두 `real_hotspot_v1` 인 `numeric_config_v1`(3회차 뒤 상태: `asset_manifest`, `electrode_import_record` 기입). numeric_v2 는 `dv_candidates_requires_real_hotspot` 으로 거부한다.
2. `--dv-grades-reference`: reference 제품의 `dv-grade` 출력 폴더. manifest 의 `product_id` 가 reference 제품과 같아야 한다.
3. `--dv-grades-target`: target 제품의 `dv-grade` 출력 폴더. reference 와 다른 폴더여야 한다.

### 인수

| 인수 | 뜻 |
|---|---|
| `--min-count` | 영역 최소 전극 수 K. 필수, 기본값 없음 |
| `--roi-grade` | 영역 선정 지도. `high` 는 High 쌍만 센 `count_high`, `low_or_high` 는 Low·High 쌍을 센 `count_visible`. 필수, 기본값 없음 |
| `--window-um` | 질의 창 한 변(µm). 병합 판정과 창 포함 비율에 쓴다. 필수 |
| `--merge-overlap` | 두 창의 `intersection/min(area)` 병합 기준 (0,1]. 필수 |
| `--max-candidates` | 선택. 방향(제품)별 질의 수 상한. 잘린 수를 `funnel` 에 기록한다 |
| `--out` | 새 폴더. 이미 있으면 종료 코드 2 |

대표 지름은 `map.diameter_um`(없으면 5 µm)이다.

### 계산 규칙

- 한 번의 지도 패스에서 `count_all`(비가중), `count_high`, `count_visible`, `grade_max` 를 만든다.
- 영역은 선정 지도가 K 이상인 4-neighbour 연결 성분이다. 병합은 지도 키(source·process·exposure·layer) 안에서만 한다.
- 등급 순위 키: 선정 지도 최대 전극 수 ↓, 최고 등급 ↓, `count_all` 최대 ↓, 면적 ↓, `region_id` ↑. 규칙 버전은 `dv_roi_rank_v1` 이다.
- 비가중 순위 키: `count_all` 최대 ↓, 면적 ↓, `region_id` ↑.
- 병합 영역의 case 는 구성 영역의 합집합이다(같은 case 는 한 번).
- export 는 대표 영역만 `roi_export.export_review_rois` 경로로 내보낸다. index export 는 `candidates` 와 같다.

### 출력 파일

| 파일 | 내용 |
|---|---|
| `candidate_queries_v1.json` | 방향별 질의, 인수, 입력 해시, 경고, `match_inputs`, `match_files`. `roi_source` 는 `candidates_dv_v1` |
| `vulnerability_v1.json` | 제품별 등급 영역, 비가중 영역, 쌍 표, case 표, 등급 없는 case |
| `grade_maps/` | layer 마다 `count_high`, `count_visible`, `grade_max` PNG + sidecar |
| `funnel.json`, `funnel.csv` | 단계별 개수. `unweighted_regions` → `graded_regions` → `merged_queries` → … |
| `reference_export/`, `target_export/` | 질의 export(`review_rois.json`) |
| `reference_index_export/`, `target_index_export/` | index export |
| `execution.json` | 명령, 상태, 출력 목록, 종료 코드 |

### 질의 플래그

| 플래그 | 뜻 |
|---|---|
| `window_clipped` | 대표 영역의 창이 frame 밖으로 나간다 |
| `merged_member_window_clipped` | 대표가 아니라 병합 구성원의 창이 나간다 |
| `selected_cells_outside_window` | 선정 셀 일부가 질의 창 밖이다 |
| `merged_member_cells_outside_window` | 병합 구성원의 선정 셀이 질의 창 밖이다 |
| `query_centre_invalid` | 질의 중심이 frame 밖이거나 valid 가 아닌 셀 위다. exporter 는 `position_outside_valid_domain` 으로 막는다 |
| `roi_rows_partially_blocked` | ROI 행 일부만 blocked 다 |

### 경고와 종료 코드

- 0: 모든 질의가 ready 이고 경고가 없다.
- 3: 부분 결과. 대표 경고는 아래와 같다.
  - `dv_ungraded_cases`: dv-grade 에 없는 case. `count_all` 에는 들어가고 등급 지도에는 들어가지 않는다.
  - `dv_candidates_side_has_no_cases`: 한 제품의 데이터셋에 case 가 없다(동명 전극 미해결 등). `side_unavailable` 과 함께 나온다.
  - `map_coverage_incomplete`, `index_coverage_incomplete`, `side_blocked` 등: `candidates` 와 같다.
- 2: 입력 오류. 아무것도 쓰지 않는다.
  - `dv_grade_product_mismatch`: dv-grade 제품이나 case 가 데이터셋과 다르다.
  - `dv_grade_case_endpoint_mismatch`: case 의 전극·layer·불량 종류가 다르다.
  - `dv_grades_dir_shared`: 두 제품이 같은 dv-grade 폴더를 가리킨다.
  - `dv_candidates_duplicate_case_id`: 한 제품 데이터셋에 같은 case_id 가 두 번 있다.
  - `invalid_candidates_dv_argument`, `output_dir_exists`.
- 참고(`notes`): `dv_grade_root_name_differs`(dv-grade 때 쓴 root 이름이 import 원본과 다르다), `dv_grades_identical_outputs`(두 제품의 dv-grade 출력 해시가 같다). 오류가 아니다.

실패하면 이 실행이 만든 파일과 폴더만 지운다.

### 한계

- **질의 중심과 창.** 질의 중심은 대표 영역의 bbox 중심이다. 영역이 U 자나 고리이면 중심이 영역 밖의 셀 위에 있을 수 있다. 병합 영역의 선정 셀 중 창 안에 든 비율을 `sel_cells_in_window_ratio` 에 적는다. 1 미만이면 질의 창이 영역 전체를 덮지 못한다.
- **ROI 안 CA.** 쌍 표의 "ROI 내 CA" 는 병합 전 등급 영역(선정 성분) 안의 CA 다. 질의 창이나 병합 영역 기준이 아니다. 여러 병합 영역에 걸린 case 는 대표 영역 쪽 값을 보인다.
- **layer 색 순서.** 보고서의 layer 색은 layer 이름의 자연 정렬(L2 < L10)로 정한다. 입력 기록에 `stack_order` 가 없기 때문이다. 슬롯은 4개이고 그 밖의 layer 는 색 없이(점선 윤곽) 그린다.
- **data_dir 절대 경로.** 입력 기록에 각 제품 `data_dir` 의 절대 경로가 남는다. `vulnerability-report` 는 이 경로로 전극 layout 자산을 다시 읽는다. 4b 와 4c 는 같은 서버에서 연속으로 실행해야 한다. 경로나 해시가 다르면 layout 없이 그리고 종료 코드 3이다.
- 이 명령은 점수·ΔCA·확정 쌍을 만들지 않는다. 6회차 입력(`roi_correspondence.json`)도 만들지 않는다.
- 실규모 시간·메모리 실측은 아직 없다(합성 소형 자료만 확인).

## vulnerability-report

```
python -m simcompare vulnerability-report --candidates-dv-dir <candidates-dv 출력 폴더> --out <없는 새 폴더> [--top-n 10]
```

- 입력은 `candidate_queries_v1.json`, `vulnerability_v1.json`, `grade_maps/` 뿐이다. 수치를 다시 계산하지 않는다.
- `--out` 은 새 폴더이고 입력 폴더 밖이어야 한다(`output_is_input`). 임시 폴더에 쓴 뒤 옮기므로 실패하면 출력이 남지 않는다.
- `--top-n` 은 표시할 상위 ROI 수다(표시용, 기본 10).
- 출력: `report.html`(외부 CDN 없음, `file://` 로 열린다), `report.json`, `execution.json`.
- 내용: 등급 순위와 비가중 순위 비교, ROI 별 쌍 표(쌍·case·원본 CA·ROI 내 CA), layer 별 색 전극 layout 지도, 등급 분포.
- 종료 코드: 0 = 입력이 available 이고 경고가 없다. 3 = 입력이 partial 이거나 경고가 있다(layout 자산 해시 불일치 등). 2 = 입력 JSON 이 없거나 모양이 다르다.
