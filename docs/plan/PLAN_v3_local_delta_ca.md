# 국부 ΔCA · 제품 비율 · 대응 라벨 계획

버전: **PLAN_v3 / 2026-10-18 / 제안 상태 — coordinator 채택 전 구현 금지**

이 문서는 2026-10-18 브레인스토밍에서 사용자가 결정한 방향을 구현 Task로 바꾼 계획이다. PLAN_v2의 수치 경로(line / pixel, failure pieces, 전극 수 지도, review_roi_v1, DL1/DL2 교환)는 그대로 두고 그 위에 더한다. 작성자는 planner이며 이 문서만 쓴다. 구현, 학습, 실자료 검증, commit, push는 하지 않는다. 아래 명령·파일명 중 현재 없는 것은 후속 Task가 제공할 수락 인터페이스 제안이다.

## 0. 배경과 결정 (2026-10-18, 사용자)

| 번호 | 결정 | 근거 메모 |
|---|---|---|
| D1 | 설계자는 DL2가 찾은 위치에서 대조된 CA를 보고, 형상 변경에 따른 **불량 유형별 CA 변화**를 본다. 눈으로 하기엔 너무 오래 걸린다 | 설계자 작업 흐름 |
| D2 | 설계 전후 쌍은 없다. **비슷한 모델끼리 짝**이 있고 양쪽 시뮬레이션 결과가 있다. 대응 위치 라벨은 찾아보면 **수십 개** 있다 | 라벨 자료 |
| D3 | **open 미포함**. short-only 유지 | G7b와 일치 |
| D4 | 결론은 **두 수준 모두**: 어느 설계가 더 안전한가(제품 수준), 이 형상 변경이 CA를 얼마나 바꿨나(국부). 공통 목표는 더 나은 설계 | |
| D5 | 창(window)은 유닛 크기가 아니라 **ROI 중심 주변의 물리 크기(µm)**. C의 측정 창은 DL2 near/wide와 분리한다. 기본값 없음. **10 / 20 / 30 µm** 후보를 수십 개 라벨 위치에서 실험해 G4를 닫는다 | G4 |
| D6 | 유닛 크기가 다른 제품은 CA 절대값으로 비교할 수 없다. 제품 수준은 **CA / 2×2 frame 면적** 비율(단위 없음, G2의 /4가 분자·분모에서 상쇄), 국부 수준은 **같은 물리 창 안의 CA**로 비교한다 | |
| D7 | 순서: DL 없이 수동 라벨 + C 창 측정으로 가치 확인 → 창 크기 실험 → DL2와 비DL 기준선 평가 → DL2로 확장 | |

분해식. 제품 수준 차이와 국부 차이를 잇는 틀이며 모든 지표 이름은 이 식을 따른다.

```text
CA / 2×2 면적  =  (면적당 구조 수)  ×  (구조 하나당 CA)
                   ↑ 유닛 크기·pitch       ↑ 형상 변경   ← 설계자가 보려는 항
제품 수준:  F(d)      = CA(d) / frame_area                      (fraction_of_frame)
국부 수준:  ΔCA_w(d)  = CA_w,B(d) − CA_w,A(d), 같은 물리 창 w   (local_delta)
연결:       Σ ΔCA_w × repeats_per_frame / frame_area  vs  ΔF    (explained_fraction, repeats 확보 시)
```

## 1. 범위와 비범위

범위.

- 대응 위치 교환 형식 `roi_correspondence_v1`와 변환기 (수동 라벨 ↔ review_roi_v1 manual 행 ↔ DL2 `roi_relations_v1`).
- 창 안 국부 측정과 Δ (`compare-windows`), 제품 수준 비율 (`fraction_of_frame`), 창 크기 민감도 요약, 나란히 보는 HTML.
- DL2 평가 준비: 라벨 변환, 위치 기반 지표, template matching 기준선, 후보 ROI 커버리지 점검.
- DL2 검색 결과를 같은 교환 형식으로 받아 국부 측정에 넣는 확장.

비범위. 다음은 이 계획에서 하지 않는다.

- 밀도 가중 실자료 값 (G1 보류), 수율 환산 (보류), 2×2 → 한 unit 환산의 기본 /4 (G2).
- CA 예측 모델, DL 점수의 CA·위험 수치 재투입, DL 결과로 ROI 자동 선택, 수정안 생성·제안.
- 제품 간 픽셀 겹쳐 그리기. 창 안 정합은 별도 판정이며 이 계획에서는 면적 비교만 한다.
- 반복 구조 수의 자동 계산. `repeats_per_unit`은 사용자가 준다.
- open 조각. 측정은 mapped short case(lateral / vertical)만 쓴다.

## 2. 교환 형식 `roi_correspondence_v1` (T1)

C가 소유하는 schema다. 수동 라벨, template matching, DL2 검색이 **같은 형식**으로 대응을 적고, `compare-windows`는 출처와 무관하게 이 파일을 읽는다. `docs/data_contract.md`에 절을 추가한다.

```json
{
  "schema_version": "roi_correspondence_v1",
  "producer": {"kind": "manual", "version": "1", "commit": null, "created_at": "2026-10-18"},
  "pairs": [
    {
      "pair_id": "P0001",
      "a": {"product_id": "A", "design_version": "v1", "frame_id": "f0",
            "center_xy_um": [123.4, 56.7], "analysis_layer": "SD",
            "endpoints": [{"electrode_id": "G1", "role": "gate", "actual_layer": "GAT"},
                          {"electrode_id": "D1", "role": "data", "actual_layer": "SD"}],
            "failure_type": "vertical_short", "roi_id": null},
      "b": {"product_id": "B", "design_version": "v3", "frame_id": "f0",
            "center_xy_um": [98.0, 61.2], "analysis_layer": "SD",
            "endpoints": [{"electrode_id": "G1", "role": "gate", "actual_layer": "GAT"},
                          {"electrode_id": "D1", "role": "data", "actual_layer": "SD"}],
            "failure_type": "vertical_short", "roi_id": null},
      "repeats_per_frame": null,
      "source": {"kind": "manual", "labelled_by": "designer-1", "labelled_at": "2026-10-20", "note": ""},
      "status": "ready", "reasons": []
    }
  ]
}
```

규칙.

| 항목 | 규칙 |
|---|---|
| 좌표 | `center_xy_um`은 각 제품 **자기 frame**의 물리 좌표. 제품 간 좌표 변환은 없다 |
| `analysis_layer` | 양쪽 **필수**. 창 안 group 측정이 비교할 layer를 정한다. 이름이 달라도 추론하지 않는다 |
| `endpoints` / `failure_type` | 선택. 있으면 해당 case의 창 안 CA를 **named_pair**로 추가 보고. 없으면 group 측정만 |
| `repeats_per_frame` | 선택. `{"a": n, "b": n}`, 측정 frame(실자료는 2×2 영역) 안에 그 구조가 반복되는 수. unit당 수를 넣지 않는다(G2 /4 미확정). 없으면 연결 항 `explained_fraction`은 unavailable |
| `source.kind` | `manual` / `template_match` / `dl2_search`. `dl2_search`는 `score`, `rank`, `checkpoint_fingerprint`, `index_fingerprint` 필수. 점수는 표시 문맥이며 측정에 쓰지 않는다 |
| `roi_id` | review_roi_v1 export 뒤 채우는 왕복 필드. 없으면 null |
| 검증 | 제품·design·frame이 config의 reference/target과 일치해야 한다. 불일치는 `correspondence_product_mismatch`로 거부. 중복 `pair_id` 거부. 같은 두 중심의 중복 쌍은 허용하되 경고 |
| 상태 | `ready` / `blocked`. blocked 행은 측정하지 않고 사유를 남긴다 |

변환기 (T1, `python -m simcompare.correspondence`):

- `to-review-rois`: 쌍의 a 쪽과 b 쪽을 각각 numeric_config의 `review_rois.manual` 요청으로 만든다. manual 요청의 허용 필드는 identity + `center_xy_um`, `endpoints`, `failure_type`이다 (`src/simcompare/roi_export.py:262`). endpoints가 없으면 `case_context_required`로 blocked가 되므로 변환기는 그 사실을 표에 남긴다.
- `from-dl2-search`: DL2 search JSON의 `primary` 행을 쌍으로 만든다. `supplementary`는 `source.scope=supplementary`로 표시하고 기본 제외.
- `to-dl2-relations`: `roi_id`가 양쪽에 채워진 쌍만 `roi_relations_v1`의 `{a, b, relation: "similar"}`로 만든다 (`particle-sim-dl2/src/psdl2/losses.py:61-67`). `different`는 만들지 않는다.
- `to-dl1-relations`: 쌍이 있는 제품 조합을 DL1 `product_a,product_b,similar` 행으로 만든다 (중복 제거).

## 3. 수치 정의

### 3.1 창 안 측정 (T2)

- 창: 중심 `c`, 한 변 `s` µm의 정사각형, half-open `[cx − s/2, cx + s/2) × [cy − s/2, cy + s/2)`. 기존 `PhysicalWindow`와 `measure_union`을 그대로 쓴다 (`src/simcompare/numeric_schema.py:296`, `src/simcompare/measurement.py:88`). resize·보간 없음. 부분 셀 clipping은 기존 규칙대로 물리 비례.
- 대상 mask: 해당 제품·`analysis_layer`·지름 group의 mapped short case. 기존 `compute_areas`가 만드는 lateral 합집합 A, vertical 합집합 B에서 `lateral_only = A\B`, `vertical_only = B\A`, `simultaneous = A∩B`, `union = A∪B`를 창 안에서 잰다 (`src/simcompare/linepixel.py:382`, `docs/data_contract.md:104`). 조각 합 = union을 창 안에서도 검사한다.
- 비율: `fraction_of_window = ca_w / window_area`. available일 때 valid 면적 = 창 면적이므로 `ca_per_valid_area`와 같다.
- Δ: `delta = b − a`, 조각별·union별. **양쪽 available일 때만 available**. 한쪽이라도 partial / unavailable / not_applicable이면 delta는 `value = null`, 사유 `window_clipped_a`, `valid_incomplete_b`, `below_height_vertical_short_b` 등. 부분 Δ는 만들지 않는다.
- 귀속 core: 창 안 hotspot 픽셀은 창 밖 d/2까지의 전극이 원인일 수 있다. 한 변 `s − d`의 안쪽 정사각형을 **attributable_core**로 따로 잰다. `s − d ≤ 0`이면 `not_applicable`, 사유 `window_smaller_than_diameter`. 창 값은 정확한 국부 위험이고 core 값은 "창 안 형상 탓"으로 읽을 수 있는 부분이다. 둘을 섞지 않는다.
- jitter (선택, `--jitter-um j`, 기본 없음): 중심을 x·y 각각 ±j 이동한 네 창에서 Δ_union을 다시 재고 `[min, max]`와 부호 일치 여부를 적는다. 임계값은 두지 않는다.
- named_pair (endpoints가 있을 때): unordered 전극 ID 집합 + `failure_type`으로 case를 찾아 그 case 원본 mask의 창 안 CA와 Δ를 보고한다. group union 이하인지 검사한다. 못 찾으면 `named_pair_case_not_found`.

### 3.2 제품 수준 비율 (T3)

- `frame_area_um2 = shape_yx[0] × shape_yx[1] × pitch²`. 실제 import 경로에서는 중앙 2×2 crop frame이다 (`src/simcompare/adapters/real_hotspot.py:204`, `:270`).
- 각 group의 L, P_effective, U, 세 조각에 `fraction_of_frame = area / frame_area_um2`를 추가한다. 분모와 `valid_area_um2`를 provenance에 남긴다.
- 상태: area available이고 valid 면적 = frame 면적이면 available. valid가 작으면 partial, `partial_value = partial area / frame_area`. missing은 그대로 null.
- 제품 간 Δ: `delta_fraction_of_frame = F_target − F_reference`, 양쪽 complete일 때만. G2의 한 unit 환산 없이 가능한 이유(비율에서 /4 상쇄)와 유한 2×2 경계 효과가 남는다는 한계를 data_contract에 적는다. 밀도 가중은 하지 않는다.

### 3.3 연결 항 (T3b, 선택)

`explained = Σ_side(window union CA × repeats_per_frame) / frame_area`를 side별로 구해 `explained_delta = explained_b − explained_a`를 `delta_fraction_of_frame`(참조 group들의 union fraction 차이)과 나란히 보고한다. `repeats_per_frame`은 측정 frame 안의 반복 수이며 per-unit 수가 아니다. 모든 ready 쌍에 repeats가 있어야 하고, 같은 group의 창이 겹치면 겹친 창들을 `measure_union`으로 한 번에 재어 중복을 제거하되 repeats가 다르면 그 지름은 unavailable(`overlapping_windows_with_different_repeats_<side>`)이다. 설명 안 된 나머지(`residual`)는 숫자로 두되 해석을 붙이지 않는다.

## 4. Task 목록

각 Task의 위험 등급은 전역 Orca 정책의 기준(schema, 핵심 계산 = High)을 따른다. 기준선은 C `rewrite/line-pixel-rate @ 2a54543`(clean), DL2 `main @ 7397c96`, DL1 `main @ dc9a019`다.

### T0 결정 기록 — Trivial, docs

- Target: `docs/open_questions.md`, `docs/implementation_memory.json`.
- Change: open_questions에 "2026-10-18 답변" 표를 추가(D1~D7). G4 행에 "창 = ROI 주변 물리 크기, 측정 창과 DL2 창 분리, 10/20/30 µm 실험으로 결정" 방침을 적되 상태는 pending 유지. implementation_memory에 `local_delta_ca_2026_10_18` 절(결정, 분해식, 작업 순서, 라벨 수십 개).
- Constraints: 코드와 `current_gate_decisions`(`src/simcompare/numeric_schema.py`)는 건드리지 않는다. G4는 T4 결과 뒤에만 answered로 바꾼다.
- Acceptance: 두 문서 diff 검토. 다른 파일 변경 없음.

### T1 `roi_correspondence_v1` schema와 변환기 — High (schema)

- Target: 신규 `src/simcompare/correspondence.py`, `docs/data_contract.md` 절 추가, `tests/test_correspondence.py`.
- Change: §2의 로더·검증기·네 변환기. CLI `python -m simcompare.correspondence <subcommand>`.
- Constraints: review_roi_v1, numeric_config_v1, DL2 `roi_relations_v1` 형식은 바꾸지 않는다. DL2 모듈을 import하지 않는다. 좌표 변환·추론 없음.
- Acceptance: 예시 파일 왕복(load → to-review-rois → export-review-rois → roi_id 채움 → to-dl2-relations)이 DL2 `read_labels`를 통과하는 fixture. 반례: 제품 불일치 거부, `analysis_layer` 누락 거부, 중복 pair_id 거부, endpoints 없는 쌍의 manual 요청은 `case_context_required` 표기, `dl2_search` 출처에 fingerprint 없으면 거부.

### T2 `compare-windows` 국부 측정 — High (핵심 계산)

- Target: 신규 `src/simcompare/local_delta.py`, `src/simcompare/cli.py`(명령 추가), `src/simcompare/pipeline.py`(실행 함수), `tests/test_local_delta.py`.
- Change: `simcompare compare-windows --config <numeric_config_v1> --correspondence <json> --window-sizes 10 20 30 [--jitter-um j] [--sort-diameter-um d] --out <dir>`. 출력 `local_delta.json`(schema `local_delta_v1`, config hash, correspondence hash, window sizes, 행별 §3.1 값), `local_delta.csv`. `--window-sizes`는 필수이고 기본값이 없다. 정렬 지름은 명시할 때만 summary를 만든다.
- Constraints: `numeric_config_v1`의 허용 key(`src/simcompare/pipeline.py:92`)는 바꾸지 않는다. 기존 `compare` 출력과 수치는 변하지 않는다. mask resize 없음. null을 0으로 쓰지 않는다. DL 점수는 읽어도 계산에 쓰지 않는다. Δ 사유는 `window_clipped_<side>`, `valid_incomplete_<side>`, `below_height_vertical_short_<side>`를 포함한다.
- Acceptance (손으로 계산한 기대값):
  - pitch 0.5 µm, 3×3 전부 활성 mask, 창 1.0 µm → `ca_w = 1.0`, `fraction_of_window = 1.0`. 창이 셀 경계와 어긋나도 같은 값(부분 셀 비례).
  - A와 B가 같은 mask, 같은 중심 → 모든 Δ = 0, status available.
  - B = A를 한 셀 평행이동, 중심도 같이 이동 → Δ = 0 (평행이동 불변).
  - 창이 frame 밖으로 나감 → 해당 쪽 partial, Δ null, 사유 `window_clipped_*`.
  - valid hole이 창 안에 있음 → partial, Δ null, 사유 `valid_incomplete_*`.
  - `s ≤ d` → attributable_core `not_applicable`; `s > d`이면 core ≤ window.
  - lateral_only + vertical_only + simultaneous = union, 창 안에서 1e-9 이내.
  - named_pair case CA ≤ group union; 없는 전극 쌍 → `named_pair_case_not_found`.
  - height 미만 vertical N/A인 지름 → 그 지름의 vertical 조각 not_applicable, lateral 조각은 available.
  - `--window-sizes` 누락 → exit 2, 명시 오류. 대응 파일의 제품이 config와 다르면 exit 2.
  - jitter 지정 시 네 이동 창의 Δ 범위가 기록되고, 미지정 시 `jitter: null`.

### T3 `fraction_of_frame` 제품 수준 비율 — High (핵심 계산)

- Target: `src/simcompare/linepixel.py`(GroupArea/LayerDelta 확장), `src/simcompare/report.py`, `docs/data_contract.md`, `tests/test_failure_pieces.py` 또는 신규 `tests/test_fraction_of_frame.py`.
- Change: §3.2. JSON·CSV·HTML에 "단위 없는 비율" 열 추가, 분모 표기.
- Constraints: 기존 면적·가중·panel 값과 CSV 열 순서는 유지하고 열을 뒤에 추가한다. 밀도·unit·panel과 곱하지 않는다. 이름에 "확률"이나 "수율"을 쓰지 않는다.
- Acceptance: frame 10×10 셀, pitch 0.1 → 면적 1 µm²; 활성 25셀 → `fraction_of_frame = 0.25`. valid hole → partial과 partial_value. 같은 mask를 frame만 두 배로 → 면적 같고 비율 절반(비교 불가 사례를 시험이 문서화). 양쪽 complete일 때만 delta. 기존 시험 전부 통과.

### T3b `explained_fraction` 연결 항 — Normal, T2·T3 뒤

- Target: `src/simcompare/local_delta.py`, 시험.
- Change: §3.3. `repeats_per_unit`이 없으면 unavailable과 사유.
- Acceptance: 합성에서 반복 구조 n개를 같은 창 Δ로 만들었을 때 `explained_fraction = delta_fraction_of_frame` 1e-9 이내. 겹치는 창은 합집합으로 처리, repeats 불일치는 unavailable.

### T4 창 크기 민감도 요약 — Normal

- Target: `src/simcompare/local_delta.py`(summary), 출력 `window_sensitivity.json/csv`, 시험.
- Change: 쌍별로 창 크기별 Δ_union(d) 표, 부호 일치 여부, 크기 쌍 간 |Δ| 순위 상관(정렬 지름에서), 지름별 attributable_core 적용 가능 비율. 추천값은 계산하지 않는다. 사용자가 보고 G4를 정한다.
- Acceptance: 세 크기 입력 시 쌍×크기 행 수 일치. 순서가 알려진 합성에서 순위 상관 값이 손계산과 일치. 크기 한 개만 주면 상관 항은 not_applicable.

### T5 나란히 보는 HTML — Normal

- Target: 신규 `src/simcompare/local_delta_report.py`, 시험.
- Change: `local_delta.html`. 쌍마다 A/B의 endpoint 전극 layout crop(창 + 창/2 여백, 창 사각형 표시), 정렬 지름의 hotspot crop, 조각×지름 Δ 표, 상태·사유, 출처(manual / dl2_search와 점수는 문맥으로만). `|Δ_union|` 내림차순. PNG는 base64로 내장해 폐쇄망 `file://`에서 외부 참조 없이 열린다. 자산은 review_roi 경로의 원본 shape 참조를 쓴다 (`src/simcompare/roi_export.py:58`).
- 선택: endpoint 전극 layout mask의 창 안 IoU를 `registration: none` 표기와 함께 표시. 정합은 하지 않는다.
- Acceptance: 행 수와 정렬이 JSON summary와 일치. HTML 안에 `http://`·`https://` 참조 없음. 자산이 없는 쌍은 썸네일 자리에 `source_shape_required`.

### T6a 후보 ROI 커버리지 점검 — Normal, 읽기 전용 분석

- Target: 신규 `scripts/check_candidate_coverage.py`(C), 문서.
- Change: 라벨된 B 쪽 중심이 `electrode-map --min-count k` 영역 안에 들어가는 비율을 k별로 보고한다. DL2 index의 후보 ROI가 지도 영역에서 나올 수 있는지를 실자료 전에 판단하는 근거다.
- Constraints: schema 변경 없음. 커버리지가 부족하면 격자 후보(`source_kind=grid_candidate`)는 **별도 schema v2 계획**으로 넘긴다.
- Acceptance: 합성에서 영역 안/밖 중심을 심어 비율이 손계산과 같다.

### T6 DL2 평가 준비 — Normal, DL2 저장소

- Target: `particle-sim-dl2` 신규 `src/psdl2/baseline_ncc.py`, `src/psdl2/eval_position.py`, `docs/validation.md`, 시험. 기준선 `main @ 7397c96`.
- Change: (1) 위치 기반 지표: 라벨된 A ROI마다 DL2 `search --cohort {B}` top-1 중심과 라벨 B 중심의 거리, `--hit-tolerance-um t` 이내면 hit. (2) template matching 기준선: A의 창 안 endpoint 전극 layout mask를 B의 같은 `actual_layer` layout mask 전체에 정규화 상호상관으로 맞춰 peak 중심을 낸다. 같은 지표로 보고. (3) roi_id 기반 Recall@k는 기존 `evaluate`를 쓴다 (`src/psdl2/evaluate.py:11`). 세 결과를 한 JSON에 둔다.
- Constraints: C 모듈 import 없음. 학습 변경 없음. 허용오차 `t`는 필수 인수.
- Acceptance: 합성에서 B가 A의 정확한 복사본이면 NCC 기준선 오차 ≤ 1 px. 라벨 없는 질의는 unavailable. `t` 누락 시 오류.

### T7 DL2 결과로 확장 — Normal, T1·T2·T6 뒤

- Target: `src/simcompare/correspondence.py`(`from-dl2-search` 실사용), `docs/usage.md`.
- Change: DL2 search JSON → `roi_correspondence_v1`(`source.kind=dl2_search`) → `compare-windows` → HTML. 수동 라벨과 같은 경로다.
- Acceptance: handoff 합성 fixture의 DL2 search 결과로 끝까지 실행되고, 출처가 HTML에 표시되며 점수가 어떤 수치에도 들어가지 않음을 시험이 확인.

### T8 문서와 폐쇄망 절차 — Normal

- Target: `docs/usage.md`, `docs/handoff/commands.md`, `docs/handoff/validation-template.json`, `docs/open_questions.md`(G4 결과 반영), `SPEC.md`.
- Change: 라벨 수집 양식, 실행 순서(§7), 수락 기록 항목(§6). T4 결과가 나오면 G4 답을 적고 `current_gate_decisions`의 G4를 갱신하는 후속 Task를 연다.

## 5. 의존성과 순서

```text
T0 ─► T1 ─► T2 ─► T4 ─► (G4 결정) ─► T8
            │  └─► T5
            ├─► T3b (T3 필요)
T3 (독립) ──┘
T6a (독립, 읽기 전용) ─► T6 (DL2) ─► T7 ─► T8
```

- 1단계 (C만, 수동 라벨): T0, T1, T2, T3, T4, T5. DL 없이 설계자에게 ΔCA 표와 HTML을 보여 주는 지점이다. 여기서 "눈으로 보던 것과 맞다"는 확인을 받는다.
- 2단계 (DL2 평가·확장): T6a, T6, T7, T8.
- 편집 worker는 저장소당 하나. T3는 T1/T2와 파일이 겹치지 않으므로 순서만 바꿔 실행할 수 있다. T6는 다른 저장소라 C 편집과 병행 가능하나 읽기 전용 슬롯 규칙은 유지한다.

## 6. 독립 리뷰와 검증

- T1, T2, T3는 High이므로 각각 fresh `claude-opus-5-5 / high` 리뷰. T2와 T3는 수치 정의(§3)를 리뷰 brief에 넣고, 손계산 기대값 표를 함께 준다.
- T4, T5, T6, T6a, T7, T8은 `claude-sonnet-5-5 / high` 리뷰.
- 전체 수락 기준(폐쇄망, 사용자 기록):
  1. 수십 개 수동 쌍에서 `compare-windows` 결과 표를 설계자가 보고 기존 판단과 일치한다고 기록한다. 불일치 쌍은 사유와 함께 남긴다.
  2. 창 크기 세 후보의 민감도 표를 보고 사용자가 G4 값을 정한다.
  3. DL2 위치 오차와 NCC 기준선 오차를 같은 라벨 집합에서 비교해 DL2 채택 여부를 기록한다. 라벨 수가 수십 개이므로 통계 주장은 하지 않는다.
  4. `validation-template.json`에 위 세 항목의 자리(`local_delta_designer_agreement`, `window_size_decision`, `dl2_vs_baseline`)를 T8에서 만든다.

## 7. 폐쇄망 실행 절차 초안 (T8에서 확정)

```text
1. 라벨 수집: 설계자가 roi_correspondence_v1 파일을 채운다 (수십 쌍).
2. simcompare import-hotspots … (양쪽 제품)            — 기존
3. simcompare validate-data --config …                 — 기존
4. python -m simcompare.correspondence to-review-rois … — T1
5. simcompare export-review-rois --config … --out …    — 기존, roi_id 확보
6. simcompare compare-windows --config … --correspondence … --window-sizes 10 20 30 --sort-diameter-um 5 --out … — T2/T4/T5
7. 설계자 검토: local_delta.html, window_sensitivity.csv → G4 결정, 수락 기록
8. (2단계) psdl2 index/search --cohort {B} → eval_position, baseline_ncc → 비교 기록 — T6
9. (2단계) from-dl2-search → compare-windows → HTML     — T7
```

## 8. 미결 질문 (사용자)

| ID | 질문 | 답 전 가능한 일 | 차단되는 것 |
|---|---|---|---|
| Q1 | `repeats_per_frame`(측정 2×2 frame 안의 반복 수)을 누가 어떻게 주는가. per-unit 수는 G2 미확정이라 받지 않는다 | T3b 외 전부 | explained_fraction |
| Q2 | jitter 폭 (예 0.5 µm = 5 px) | 기본 없음으로 구현 | 안정성 표의 실자료 값 |
| Q3 | 정렬·썸네일 지름 (지도 대표값 5 µm과 같게?) | 인수로 받음 | 없음 |
| Q4 | T6 위치 hit 허용오차 `t` (창 반폭?) | 인수로 받음 | DL2 채택 판정 |
| Q5 | 설계자에게 기본으로 보일 것이 layer group 조각인가 named_pair인가 | 둘 다 계산 | HTML 기본 정렬 열 |
| Q6 | 라벨 양식을 JSON으로 직접 쓰는가, CSV로 받아 변환하는가 | JSON 우선 | CSV 변환기 추가 여부 |

## 9. 중단·재시도

- 각 Task는 작업 트리 변경을 보존하고 coordinator 지시 없이는 commit하지 않는다. 되돌리기 전에 tracked/staged/untracked 상태를 기록한다.
- T2 실패 시 T1 결과물은 유지한다. T4/T5는 T2의 `local_delta.json`만 읽으므로 T2를 다시 돌려도 영향이 없다.
- G4가 정해지기 전에는 어떤 창 크기도 기본값으로 코드에 넣지 않는다.

## 10. 구현 상태 (2026-10-18, branch rewrite/line-pixel-rate, 미커밋)

사용자 지시("진행해")로 일반 세션에서 구현했다. 독립 리뷰(§6)는 아직 없으며 아래 시험은 구현자 시험이다. 모두 합성 자료다.

| Task | 상태 | 산출물 | 시험 |
|---|---|---|---|
| T0 | 완료 | `docs/open_questions.md` 2026-10-18 표·G4 행, `docs/implementation_memory.json` `local_delta_ca_2026_10_18` | 문서 |
| T1 | 완료 | `src/simcompare/correspondence.py` (검증, bind, to-review-rois, attach-roi-ids, to-dl2-relations, to-dl1-relations, from-dl2-search) | `tests/test_correspondence.py` 19 |
| T2 | 완료 | `src/simcompare/local_delta.py`, `compare-windows` CLI, `pipeline.run_numeric_local_delta` | `tests/test_local_delta.py` 16 (손계산 창 면적, 평행이동 불변, clipping/valid, core, 조각 합, named_pair, jitter, 오류, CLI) |
| T3 | 완료 | `linepixel.FractionMetric`/`fraction_of_frame`/`compare_fractions`, report `fractions` 표·HTML 절 | `tests/test_fraction_of_frame.py` 5 |
| T3b | 완료 | `local_delta.explained_fraction` (`repeats_per_frame` 필수, 같은 group의 겹치는 창은 합집합으로 한 번 측정, repeats 다르면 unavailable) | `test_local_delta.py::test_explained_fraction_*`, `test_overlapping_windows_*` |
| T4 | 완료 | `local_delta.window_sensitivity` (`window_sensitivity.json/csv`, Spearman, 부호 일치, core 적용 지름) | `test_local_delta.py` |
| T5 | 완료 | `src/simcompare/local_delta_report.py` (`local_delta.html`, base64 PNG, 조각 색, 창/core 사각형, 선택 IoU) | `tests/test_local_delta_report.py` 4 |
| T6a | 완료 | `scripts/check_candidate_coverage.py` | `tests/test_candidate_coverage.py` 1 |
| T6 | 완료 (DL2 저장소, 미커밋) | `psdl2/eval_position.py`, `psdl2/baseline_ncc.py`, `docs/validation.md`, `docs/USAGE.md` §4 8)·9) | `tests/test_position_baseline.py` 6; DL2 전체 190 passed, 1 skipped |
| T7 | 완료 | `from-dl2-search` → bind → `compute_local_delta` 왕복, 점수가 수치에 들어가지 않음 | `tests/test_dl2_round_trip.py` 1 |
| T8 | 완료 (G4 결과 반영 제외) | `docs/usage.md` 새 절, `SPEC.md`, `README.md`, `docs/data_contract.md` 세 절, `docs/handoff/commands.md` 4b절, `validation-template.json` 세 빈 항목 | `tests/test_handoff.py` 양식 시험 |

C 전체 suite: 신규 시험 포함 통과. 단 `tests/test_handoff.py::test_offline_full_candidate_in_temporary_git_archive`는 형제 저장소 DL2의 작업 트리가 dirty(이 구현의 미커밋 변경)라 `dirty working tree refused`로 실패하며, DL2 커밋 뒤 다시 통과해야 한다.

알려진 비용·한계: `local_delta.json`은 쌍 하나·창 하나에 약 0.4 MB(32 지름 × 2 kind × 4 metric의 전체 dataclass)라 수십 쌍 × 세 창이면 수십 MB다. 필요하면 요약 전용 출력 옵션을 후속으로 둔다. HTML의 선택 IoU는 셀 중심 표본이며 정합이 아니다. 후보 ROI 격자(`grid_candidate`)는 미구현이며 T6a 결과에 따라 schema v2로 다룬다. 남은 사용자 결정은 §8 Q1~Q6과 G4 값이다.

성능(2026-10-18, macOS 개발기, 합성): 2000×2000 frame(0.1 µm/px) 제품 2개 × case 2개 × 32 지름의 `compute_areas` 1.8 s, `compute_local_delta` 5쌍 × 창 3개 × 32 지름 + jitter 2.0 s(쌍·창당 약 0.13 s). 창 측정은 창 주변 한 셀 여백의 crop에서 하며(`crop_to_window`), 전체 frame 측정과 같은 값·상태임을 무작위 시험으로 확인했다. 실자료의 비용은 case 수에 비례하는 `compute_areas`가 좌우한다.

## 11. 독립 리뷰 (2026-10-18)

별도 세션(Opus, high, 읽기 전용)이 C `2a54543..65195a5`와 DL2 `43614d3`을 검토했다. 판정 **PASS-WITH-NOTES**, blocking 없음("조사한 모든 곳에서 fail-closed이며 잘못된 숫자, 부분 Δ, 기본 창 크기는 없다"). advisory 12건 중 아래를 후속 커밋으로 반영했다.

| 리뷰 항목 | 조치 |
|---|---|
| A1 repeats의 per-unit/per-frame 의미 | 필드를 `repeats_per_frame`으로 바꾸고 측정 frame 안의 반복 수로 정의. per-unit 수는 받지 않음(G2 /4 미확정). Q1 문구 갱신 |
| A2 창 crop이 frame 가장자리에서 ulp 차이로 상태를 뒤집음 | 완전성 판정을 선언 십진수(Fraction) frame 경계로 다시 결정(`_reconcile`). 리뷰어 반례 두 건을 시험으로 추가. 전체 frame `measure_union`의 같은 float 문제는 창 경로에서만 보정 |
| A3 겹치는 창을 거부만 함 | 같은 group의 겹치는 창을 합집합으로 한 번 측정, repeats 다르면 unavailable |
| A4 `group_missing` 미발생·최상위 status 고정 | 조건 수정, status를 지름별 집계(available/partial/unavailable)로 |
| A5 사유 코드가 계획과 다름 | `window_clipped_<side>`, `valid_incomplete_<side>`, `below_height_vertical_short_<side>` 추가(기존 `a_partial`, `a:…`도 유지) |
| A6 named_pair ≤ union 검사 없음, config hash 없음 | 창 안 named 값이 group union을 넘으면 `named_pair_exceeds_group_union_<side>` 기록; provenance에 `config_sha256` 추가 |
| A8 design_version 무시 | group·case 매칭에 design_version 포함 |
| A9 blocked ROI의 roi_id를 붙임, map 설정 덮어씀 | blocked 행은 붙이지 않고 보고(exit 3); map의 다른 key 보존, diameter 주입 없음, output_dir 절대경로화 |
| A10 real/numeric 혼합 미검사, HTML writer의 조용한 ImportError | `mixed_real_and_numeric_source_formats` 거부; try/except 제거 |
| A11 썸네일 예외가 CLI를 중단 | 썸네일 실패는 `thumbnail_failed:<code>`로 표기하고 페이지는 씀 |
| A12 DL2: pitch 불일치 미검사, 짝수 median, 중복 질의 | `pixel_pitch_mismatch` 사유, 올바른 median, 중복 예측은 무시하고 집계에 기록. `role_layer_unions` docstring에 "export 행이 참조한 전극의 합집합"임을 명시 |
| A7 실자료 운영 결과 | 문서화: real_hotspot의 case 목록·condition_assignment·pair 표가 확인되기 전에는 모든 창이 partial이고 Δ가 null이다 |

A9의 endpoint 순서(review ROI export는 순서 비교, 창 측정은 무순서)는 문서에 적고 코드 동작은 유지했다. 수정 뒤 두 저장소 suite를 다시 통과시켰다. 이 수정 커밋 자체는 아직 독립 리뷰를 받지 않았다.

