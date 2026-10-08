# 데이터 계약

## numeric_v2 — 수치 경로의 내부 canonical 계약 초안

근거: [PLAN_v2 §1–3, T1](plan/PLAN_v2.md). 이 절은 새 line/pixel 수치 경로의 문서 계약이다. 로더·CLI 구현 완료 또는 실자료 승인 증거가 아니다. 아래 **기존 1.x / CAA / AE 계약**은 별도 legacy 계약이며 numeric_v2의 규칙을 소급 적용하지 않는다. 합성 예제는 [numeric_v2/README.md](domain-input-example/numeric_v2/README.md)에 있다.

### 원천 형식과 변환 경계

원천 CSV/JSON/이미지 형식은 G2 **pending**이다. `schema_version=numeric_v2`는 어댑터가 검증 후 만드는 내부 형식이며, 합성 manifest의 inline binary 배열은 문서 검산용 원천이다. 실제 이미지 전달 형식으로 채택한 것이 아니다. 변환 기록에는 `source_format`, `source_contract_version`, `adapter_version`, `source_manifest_hash`, `input_hash`, 원본 파일별 hash와 변환 결정/근거를 남긴다. 입력 hash 산정 범위와 encoding은 예제 README에 명시한다. hash는 실측 여부나 승인 여부를 증명하지 않는다.

원천 `case_id`, product/design/unit/process/source ID, raw ordered endpoint tuple, raw `fab_pair_key`, raw `assigned_process_layer`를 그대로 보존한다. 문장 부호·대소문자·Unicode를 바꾸거나 raw key를 delimiter로 분해/정렬해 전극 ID를 만들지 않는다. 복합 source identity는 `(product_id, design_version, source_id, process_step_id, exposure_id, case_id, frame_id, source_contract_version)`이며 제품마다 반복되는 case ID를 단독 join key로 쓰지 않는다.

Legacy 미표시/1.1 vertical의 상부 Fab assignment와 1.2의 **source-step Fab assignment**는 각 원래 계약으로 유지한다. numeric_v2는 vertical의 실제 상부 layer에 불량과 density를 귀속한다. 1.2→numeric_v2 이동 시 raw assignment를 덮어쓰지 않고 별도 `upper_actual_layer`, `analysis_layer`, `density_process_layer`와 검증된 layer↔process 대응 및 migration 근거를 요구한다. raw source-step과 상부 density 의미가 충돌하면 `migration_assignment_conflict`로 명시 실패시키고 사용자 확인 전 자동 override하지 않는다. G2 미답변으로 검증할 수 없으면 변환은 pending/unsupported다. 기존 `AnalysisBundle`/CAA/AE 입력을 새 수치 입력으로 암묵 수용하지 않으며 명시 어댑터·변환 기록이 없으면 `unsupported_source_contract`로 거부할 방침이다. 기존 derived cache/model 재사용도 승인하지 않는다.

### source case와 전극 pair

| 범위 | 필드·검증 |
|---|---|
| envelope | `schema_version=numeric_v2`, `contract_status`, `is_synthetic`, 위 provenance, `pair_table_version`, `pair_table_hash`, `diameters_um`, `frames`, `cases`, `density`, `unit_domain`, `outputs`, `gates` |
| case | 복합 identity, `raw`, `endpoints` 두 개, `failure_type=lateral_short\|vertical_short`, `pair_id`, `frame_id`, `analysis_layer`, `process_step_id`, `exposure_id`, `density_process_layer`, 지름별 `records` |
| endpoint | `(electrode_id, role, actual_layer)`를 하나의 결합 객체로 보존. 두 전극은 구별되는 ID. 순서를 바꿀 때 객체 전체를 이동하며 role/layer를 서로 바꾸지 않음 |
| pair 표 | `pair_id`, 두 `electrode_id`, `class=line\|pixel`, `pair_table_version`; product-local 전극 namespace. unordered 전극 ID 집합으로 조회하며 layer/role/failure_type은 분류 key에서 제외 |
| pair 오류 | 같은 unordered pair에 상충 class, 중복 행, blank ID는 오류. 미매핑은 `unmapped_pair`, 기대 coverage에 남기고 0 또는 임의 pixel로 처리하지 않음. 조합 수를 62로 제한하지 않음 |
| vertical | 실제 stack 순서 및 `height_um`/근거 필수. `upper_actual_layer`에 귀속하고 검증된 상부 공정 density 선택. 높이 미만 `d < height_um`만 N/A, `d = height_um`은 적용 대상. 5 um 이상 높이는 없다는 사용자 사실을 유지하되 개별 높이를 추측하지 않음 |

예제의 forward/reverse vertical AB는 동일 line class이나 forward는 A/gate/M1, B/data/M2이고 reverse는 B/data/M2, A/gate/M1이다. raw 순서와 실제 layer 결합이 각각 남는다. lateral AB도 line이어도 별도 case다. 같은 pair의 여러 case를 OR한 후에도 원본 case별 CA·failure_type·layer·raw ID를 추적할 수 있어야 한다.

### frame·관측·32 지름 coverage

Canonical 길이는 `um`, 원 CA는 `um^2`다. frame은 `frame_id`, `length_unit=um`, `origin_xy_um=[x,y]`, `pixel_pitch_um`(양의 유한 scalar, 등방격자), `shape_yx=[height,width]`, `axis_order=yx`, `origin_convention=top_left_cell_edge`, valid binary mask와 관측 provenance를 갖는다. 물리 x는 열 방향, y는 행 방향으로 증가하며 bbox는 `[xmin,ymin,xmax,ymax)`다. 다른 원천 단위는 명시 변환한다. 비등방격자는 별도 명시 계약 없이는 unsupported다.

Hotspot은 입자 중심 궤적의 binary mask다. 확률/강도/NaN/음수 배열을 bool로 자동 변환하지 않고 명시 범례·변환이 없으면 거부한다. layout/hotspot/valid는 원본 frame·pitch·origin·shape가 일치해야 한다. 임의 resize/warp/재팽창 금지. valid 밖은 불량 0이 아닌 미관측 영역이다. 전체 expected domain보다 valid가 작으면 관측 면적만 partial로 제공하고 완전 값은 막는다.

`diameters_um=[1,2,…,30,40,50]`는 32개 원형 지름의 기대 목록이다. 각 expected case에 각각 32개 record가 있어야 하며 보간·외삽·누락 0 채움은 하지 않는다. record는 `diameter_um`, `input_status=observed|missing|not_applicable`, `mask` 또는 root 내부 상대 asset 경로/hash, `reasons`, N/A 판정 근거를 갖는다. 배열은 합성 예제에서만 inline 허용한다. 실제 원천의 기대 case 목록 자체가 없으면 `expected_case_coverage_unconfirmed`다.

- 관측 zero: `observed`, 검증된 전부 0 mask, 면적 `0`, 출력 `available`.
- 누락 input: 기대 record는 유지하되 `missing`, mask/면적 `null`, `missing_input`; 파일 부재로 N/A를 추론하지 않음.
- 높이 미만 vertical: `not_applicable`, mask/면적 `null`, `below_height_vertical_short`, 명시 높이·판정 근거. 기대 목록에서 적용 제외된 수로 따로 집계.

valid 밖 활성 셀은 기존 clipping/coverage 정책을 유지하며 active_cells_outside_valid:<개수> 사유로 원천의 셀 수를 기록한다.

Coverage는 `expected/observed/missing/not_applicable/unmapped`와 valid 관측 셀/면적 범위를 기록한다. N/A는 해당 case만 제외하며 같은 지름의 다른 적용 가능한 short는 계산한다.

### 면적·density·unit-domain·panel 출력

Union key는 `(product,design,source/process 조건,exposure,analysis_layer,diameter,frame)`다. 같은 layer라는 이유로 다른 노출·원천 조건을 합치지 않는다. layer와 step의 일대일 대응 여부는 G2 pending이다. `L`=line pair union, `P`=pixel pair union, `P_effective=P\L`, `transfer=P∩L`, `U=L∪P`다. 면적은 valid 내 셀 수 × pitch²이며 `area(U)=area(L)+area(P_effective)`. transfer는 별도 진단으로 총합에 더하지 않는다. 원 case CA와 union CA를 둘 다 보존한다.

Class 내 서로 다른 pair membership 크기 1은 exclusive, 2 이상은 shared bucket이다. bucket에는 안정 ID, 구성 pair ID, 지름·layer·면적·상태가 있다. pair_lineage_case_keys는 해당 pair의 원천 계보 전체이며 missing/관측 0 case도 포함할 수 있다. contributing_source_case_keys는 해당 지름에서 원본 관측 mask가 실제 그 bucket과 겹치는 case만 나열한다. 리포트는 계보를 면적 기여자로 표시하지 않는다. shared는 나누어 배분하지 않고 bucket ID별 한 번만 합산한다. `exclusive+shared=class_total`을 지름/layer/panel에서 검사한다. shared 또는 transfer를 종류별 simultaneous라고 부르지 않는다. G7a 사용자 답변(2026-10-08)은 lateral-only/vertical-only/simultaneous 지표 구현이다. 이 Task에서는 유형 보존 및 기존 line/pixel만 유지하며 종류별 지표는 후속 Task에서 구현한다.

| density 필드 | 의미 |
|---|---|
| identity | `distribution_id`, Fab/product family, `process_layer`, 기간, 원천 ID·hash, 적용 기준 |
| 원 값 | `diameter_um`, `raw_value`, `raw_value_unit`, `observed`, `surveyed_area_um2`, `averaging_basis`, `bin_semantics`, `bin_lower_um`, `bin_upper_um` |
| canonical | `density_per_um2`, `canonical_unit`, `status`, `reasons`, 명시 변환 근거. null은 0이 아님 |

G1 일부 answered(2026-10-08): 지름마다 한 값이며 `bin_semantics=per_bin`이다. 연속 per-um density가 아니므로 bin 폭 질문은 닫혔고 40/50에서도 폭을 곱하거나 적분하지 않는다. 실제 단위, 조사면적/count 변환, 적용 공정/기간은 pending이다. **10 count/glass는 단위·실제 조사면적·bin 의미 없이 10 per um^2로 사용할 수 없다.** per-bin 역면적 density가 확인된 때만 `V=Σ area_um2 × density_per_um2`를 쓴다. per-um 연속 density는 이 계약에서 승인되지 않는다. bin_lower_um/bin_upper_um은 지름별 값 계산에 필요하지 않으며 null로 남길 수 있다. 총 density를 확률 1로 정규화하지 않으며 line/pixel에 같은 계약을 적용한다. observed raw zero도 단위 계약 미확정이면 canonical 가중값은 pending이고, missing density는 별도 기록한다.

`unit_domain`은 측정 domain/frame, unit 치수, domain 배열 형태, `unit_area_basis`, `conversion_policy`, `divisor`, 승인 근거/상태를 남긴다. G2 일부 answered(2026-10-08): 제품 간 um/pixel은 같고 pixel 수는 제품마다 다르며 한 제품의 모든 layer 크기는 같다. 실제 2×2 domain은 대략 100 um, 2000 px(약 0.05 um/pixel)이며 이 근사치를 입력 frame의 기본값으로 만들지 않는다. 한 unit을 얻는 환산 방식은 pending이므로 basis/policy/divisor는 `null`과 `unit_area_basis_unconfirmed`다. **2×2 mask를 기본 `/4`하지 않는다.** full-mask union/면적은 가능하나 unit/panel 가중값·delta는 null이다. 현재 `/4` 관행을 사용자가 확인한 경우에만 명시 정책·한계와 함께 적용하고 새로운 경계 보정/수율 환산은 하지 않는다.

panel은 unit/panel 양의 유한 치수와 단위, `rounding_policy=floor|half_up_round`, 제품별 `unit_count`를 남긴다. 각 축에 함수를 적용해 곱하며 epsilon으로 floor를 올리지 않는다. `V_panel=N×V_unit`, `delta=target−reference`. 두 제품의 N은 별도로 산정한다. 비율은 선언 단위의 원값끼리 Decimal로 구한 뒤 각 축 floor/half-up을 적용한다. N=0이면 panel 값은 unavailable/null과 panel_has_no_units 사유이며 사용 가능한 0이 아니다. pair 표/공정/layer/density/면적 기준 호환성을 검증하고 실제 pixel overlay는 요구하지 않는다. 서로 다른 Fab 기준이면 구조 변화만의 효과라고 부르지 않는다. `delta_transfer`는 panel transfer의 순변화이며 공간 이동 추적이 아니다.

모든 출력 metric은 `{value, partial_value, unit, status, reasons, coverage, basis}`를 갖는다. **status가 partial 또는 unavailable이면 value는 null이며 알려진 부분합은 partial_value에만 둔다.** 완전값/부분합을 같은 value 필드로 표시하지 않는다. 관측 가능한 부분이 없으면 partial_value도 null이고, 관측한 부분합 0은 partial_value=0으로 구별한다. T2 원 case 측정의 ca_um2도 이 규칙을 따르며 부분 CA는 partial_value, 부분 CA/valid 비율은 partial_ca_per_valid_area다. observed_area_um2/observed_ca_um2는 내부 검산용 관측 부분합이며 완전값으로 표시하지 않는다. `status=available|partial|unavailable|not_applicable`; pending은 게이트/정책 상태이며 metric status enum과 혼합하지 않는다. available은 완전 관측/적용 가능한 값, partial은 알려진 부분합, unavailable은 산출 차단, not_applicable은 근거 있는 적용 제외다. partial을 완전 panel/delta로 승격하지 않는다. observed absence가 확인된 경우만 비교에서 0을 쓰며 누락 layer/pair/density를 0으로 만들지 않는다. 예제의 `decision_status=pending` panel metric은 `value=null,status=unavailable`이다.

### review-ROI 교환 계약 (`review_roi_v1`)

PLAN_v2 §2.4의 생산자는 C/T6, 소비자는 DL2/T10, C→DL2→C 왕복 검증은 T12다. 제안 명령 `simcompare export-review-rois --config <config> --out <directory>`는 아직 구현 완료 명령이 아니다. 출력은 `review_rois.json` 및 원본 shape/CNT/valid 상대 asset 참조다. numeric manifest와 별도 schema이며 embedding 입력 패키지에 hotspot/CA 파일을 넣지 않는다.

| 범위 | 필드·규칙 |
|---|---|
| envelope | `schema_version=review_roi_v1`, `producer_commit`, `source_manifest_hash`, `assets_root`, `rois`, `excluded_records`; root 내부 상대경로 및 파일 hash |
| 정체성 | `roi_id`, `product_id`, `design_version`, `case_id`, `source_kind=electrode_count_region\|manual`, `source_region_id`(manual=null), `selection_policy_version`; 복합 source identity로 join |
| 원본 frame | `frame_id`, `length_unit=um`, `origin_xy_um`, `pixel_pitch_um`, `shape_yx`, `axis_order=yx`, 좌상단 cell-edge 원점·half-open bbox |
| 위치 | `center_xy_um`, `region_bbox_um`(manual 점=null). 지도 bbox 중앙을 제안 기준점으로 기록하고 실제 영역 ID 보존; bbox는 near/wide crop 크기가 아님 |
| 문맥 | 전극/role/actual_layer 양 끝 결합, `failure_type`, process/source ID, 원본 layout/CNT/VIA endpoint/valid 참조 및 관측 상태 |
| provenance | 대표 지름(기본 5 um, 변경 가능), threshold, connectivity(4-neighbor), coverage, G7b 상태를 선택 근거로만 보존; hotspot/CA/density/rate와 선택 근거를 tensor 입력에서 제외 |
| 상태 | `ready\|blocked`, `reasons`; case/frame/원본 shape 대응이 없으면 blocked. `excluded_records`에도 제외 identity·사유를 기록 |

ROI 출처는 위 두 종류뿐이며 제품 간 구간 대응/등록이나 DL2 검색 결과로 자동 선택하지 않는다. 지도 multi-case 영역은 `(region_id,case_id,문맥)`별 행과 같은 위치 관계를 보존한다. 같은 위치 case/증강본은 독립 제품이나 무조건 negative가 아니다. manual에는 제품/설계/frame/물리 좌표와 case 또는 명시 전극/layer 문맥이 필요하고 위치만 있으면 `blocked: case_context_required`다. shape 연결 누락은 `blocked: source_shape_required`다.

G7b answered(사용자, 2026-10-08): 지도는 현재 short-only이며 canonical open 입력은 map_requires_short_only 오류로 거부한다. 별도 legacy DefectHotspot의 open 의미는 유지한다. 기본 지도는 원본 2×2 domain의 layer별 count와 product/design/unit별 최대값 배열 및 Pillow heat-map PNG이고 자동 threshold·영역·순위는 없다. 사용자가 config의 min_count 또는 CLI --min-count를 명시할 때만 영역을 계산하고 선택한 영역을 ROI로 내보낸다. 이 행에는 map_scope_unconfirmed 차단 사유가 없으나 누락 coverage 및 원본 case/frame/shape 검증은 계속 적용한다. 수동 위치가 기본 ROI 출처이고 독립 조건을 만족하면 ready다. 지도는 취약 위치를 보여 주고 DL2는 사용자가 고른 위치와 유사한 구조를 찾는 상호 보완 도구다. G4 near/wide 미답변은 위치 내보내기와 별개로 실제 crop recipe 확정을 막는다. T10은 ready 행의 동일 물리 중심에서 near/wide를 추출하고 crop/valid/source version을 index/query에 남긴다. T12는 `roi_id`와 product/design/case/source/frame/version·asset hash를 왕복 대조하며 다른 제품의 같은 case ID로 원본 CA를 join하지 않는다.

### 질문 상태와 범위

[open_questions.md](open_questions.md)의 numeric_v2 표가 질문 상태를 추적한다. G1/G2는 일부 answered이며 미답변 부분은 pending/null, G4는 pending이다. G7b는 short-only로 answered다. G7a도 종류별 지표 구현으로 answered지만 구현은 별도 Task에 맡기며 현재 line/pixel·shared의 의미를 바꾸지 않는다. G1 단위/조사면적은 실자료 가중값을, G2 한 unit 환산·원천 승인은 unit/panel 및 migration을 차단한다. 합성 문서 검산은 가능하며 게이트 승인이나 실자료 성능 검증이 아니다.

---

## 기존 1.x / CAA / AE 계약 (legacy, numeric_v2와 별도)

다음은 기존 원천 어댑터·모델 경로의 계약 기록이다. 이 절의 무차원 CA 정규화·CAA·source-step assignment·letterbox 규칙은 numeric_v2에 자동 적용하지 않는다.


코드 정의: `src/simcompare/schema.py`. 흐름은 다음과 같다.

```text
원본 파일 → 입력 어댑터(adapters/*) → 공통 구조(AnalysisBundle, FabDistribution) → 전처리(dataset/preprocess) → 모델·기준선·분석
```

모델과 분석 코드는 원본 파일 형식을 모른다. 실제 자료는 어댑터만 새로 작성해 연결한다.

## 1. 분석 묶음 `AnalysisBundle`

한 묶음 = 제품 × 설계 버전 × 전극 역할 조합 × 불량 종류의 크기별 결과.

| 필드 | 필수 | 설명 |
|---|---|---|
| `sample_id` | ✔ | 묶음 고유 ID. 중복 금지 |
| `product_id`, `product_family`, `design_version` | ✔ | 제품·제품군·설계 버전 |
| `design_lineage_id` | ✔ | 원형 설계와 파생 설계를 묶는 ID. 학습/평가 분리 단위 |
| `electrode_a_role`, `layer_a` | ✔ | 첫 전극 역할과 layer |
| `electrode_b_role`, `layer_b` | ✔ | short는 필수. `lateral_open`은 반드시 `NOT_APPLICABLE`(`"N/A"`) — 누락이 아니라 "해당 없음" |
| `failure_type` | ✔ | `lateral_open` \| `lateral_short` \| `vertical_short`. 세 전극 이상은 다루지 않음 |
| `fab_id` | ✔ | Fab ID |
| `fab_distribution_id` | 선택 | 명시하면 해당 분포를 사용; 1.2는 Fab/layer, family exact 또는 null, pair exact 또는 null, non-reference를 검증. Pair-specific 분포는 명시 ID로만 선택 |
| `fab_pair_key` | 1.2 필수 | raw unit ID와 순서 있는 layer 쌍; 자동 선택은 null key의 layer-wide 분포만 고려 |
| `assigned_process_layer` | 조건부 | Fab 분포 매칭용 공정 layer. 1.2는 source-step Fab layer를 유지. legacy(미표시/1.1) vertical은 적층표 상부 Fab layer |
| `unit_width`, `unit_height`, `physical_length_unit` | 선택 | unit-pixel 실제 크기. 없으면 물리 길이·면적 보정 비교 불가로 기록 |
| `layouts` | 선택 | 전극별 `ImageInput` (open 1개, short 2개; slot a, b 순) |
| `hotspots` | 선택 | 이물 크기별 가공 hotspot `ImageInput` |
| `ca` | 선택 | `CACurve` |
| `geometry` | 선택 | 두께(`thickness`), 형상 수치(`shape_metrics`), 길이 단위 |
| `parent_design_id`, `change_description` | 선택 | 변경 설계 추적 |
| `provenance` | ✔ | 어댑터 이름, 원본 파일, 계산 설정(몬테카를로 등), 계산 버전, 합성 여부 |

`ImageInput`: `array`(2-D float32), `path`, `pixel_size`(픽셀당 실제 길이), `origin_xy`, `particle_size`와 `particle_size_unit`(hotspot의 원형 이물 지름), `value_semantics`(`binary` \| `processed_intensity` \| `probability`), `legend_id`. `probability`는 명시 범례가 있을 때만 허용된다. real 모드에서는 같은 묶음의 layout·hotspot 배열 크기, pixel size, 원점과 실제 영역이 일치해야 한다.

`CACurve`: `particle_sizes`(원형 이물의 지름, 엄격 증가), `particle_size_unit`, `values`(비음수 유한값, 미관측은 NaN), `observed`(bool), `unit="1"` 또는 면적 단위 `nm^2`, `um^2`, `mm^2`, `calc_settings`. legacy 무차원 CA는 0~1 범위이며 `CA / unit-pixel 면적`은 `1/um^2` 위험 지표다. 면적 단위 원 CA는 canonical 길이 제곱으로 환산해 ROI 면적으로 한 번만 나누며 결과 단위는 `1`이다.

## 2. Fab 자료 `FabDistribution`

| 필드 | 설명 |
|---|---|
| `fab_distribution_id`, `fab_id`, `product_family`(없으면 null), `process_layer` | 매칭 키 |
| `bin_lower`, `bin_upper` | 크기 구간. 정렬·비중첩·lower < upper |
| `raw_values`, `observed` | 원본 값. 미관측은 NaN/null |
| `value_kind` | `average_count_per_glass` \| `count` \| `probability` \| `rate_per_area` \| `density` — 서로 같은 방식으로 처리하지 않음 |
| `value_unit`, `size_unit` | 필수 |
| `surveyed_area`, `area_unit` | `count`, `average_count_per_glass`는 필수 |
| `period` | 적용 기간 |
| `averaging_basis` | `average_count_per_glass`는 반드시 `per_glass` |
| `is_reference` | 순위 비교용 지정 기준 분포 여부 |

분포의 **모양**과 **총량**은 `caa.fab_shape_and_total`에서 분리한다. `average_count_per_glass`와 `count`의 총량은 실제 조사 면적으로 나눈 뒤 사용하므로 조사 면적 차이가 오염량 차이로 학습되지 않는다.

## 3. CAA

CAA는 CA와 Fab 가중치로 계산하는 **제품 간 비교용 상대 위험 지표**다. 입력이나 정답으로 쓰지 않고, 실제 불량 확률·예상 불량 개수·불량률·수율로 표시하지 않는다.

- 분포 선택(`caa.select_distribution`): `process_layer_overrides["<product_id>|<failure_type>"]` → 없으면 `assigned_process_layer`. 둘 다 없으면 오류.
- 해석(`caa.interpret_fab`): 현재 규칙은 `demo_probability_per_bin` 하나다. `mode: demo` + `value_kind: probability`에서만 동작하고 결과에 `DEMO CAA` 라벨이 붙는다. real 모드와 다른 종류의 값은 오류다.
- 계산(`caa.compute_caa`): 구간 중앙값과 CA 크기가 정확히 일치하는 점만 곱해 합산한다. 보간·구간 변환 없음. 누락 크기는 목록으로 보고하며 `require_full_coverage: true`면 값을 만들지 않는다.

## 4. 예시 어댑터 파일 형식 (`adapters/example.py`)

```text
data_dir/
  bundles.csv       # 묶음 메타데이터 (필수)
  layouts.csv       # sample_id, slot(a|b), path, pixel_size, origin_x, origin_y
  hotspots.csv      # sample_id, particle_size, particle_size_unit, path, pixel_size, origin_x, origin_y
  ca_records.csv    # sample_id, particle_size, particle_size_unit, ca_value, ca_unit(1 또는 nm^2/um^2/mm^2)
  fab.json          # 기존 공통 분포 형식(선택)
  fab_measurements.csv # glass당 평균 검출 개수 가공자료(선택, 둘 중 하나 필수)
  layer_stack.csv   # 제품별 하부→상부 적층 순서(vertical short에 필요)
  design_review_status.csv # 외부 전기·설계 검토 상태(선택)
  geometry.json     # {sample_id: {"thickness": {...}, "shape_metrics": {...}, "length_unit": "um"}}
  provenance.json   # {"calc_settings": {...}, "calc_version": "...", "is_synthetic": false}
  arrays/*.npy, images/*.png
```

`bundles.csv` 예:

```csv
sample_id,product_id,product_family,design_version,design_lineage_id,electrode_a_role,electrode_b_role,layer_a,layer_b,failure_type,fab_id,fab_distribution_id,assigned_process_layer,unit_width,unit_height,physical_length_unit,parent_design_id,change_description,calc_version
P1-v1-OPEN,P1,F1,v1,L1,data,N/A,M2,N/A,lateral_open,FAB_A,,M2,60,90,um,,,sim-3.2
P1-v2-VSHORT,P1,F1,v2,L1,gate,data,M1,M2,vertical_short,FAB_A,,M2,60,90,um,P1-v1,wider gap,sim-3.2
```

`ca_records.csv`의 크기별 기록은 `schema.group_size_records`로 묶음별 곡선이 된다(중복 크기·단위 혼합은 오류).

## 5. 매칭·검증 규칙

- 모든 부속 파일의 `sample_id`는 `bundles.csv`에 있어야 한다. 모르는 ID는 목록과 함께 오류.
- 경로는 `data_dir` 내부 상대경로만 허용(절대경로·`..` 탈출 오류).
- 빈 셀은 None/NaN. 0으로 대체하지 않는다.
- open에 slot b layout이 있으면 오류.
- 유사 사례 비교는 같은 `failure_type`과 같은 전극 역할 집합(순서 무관)에서만. layer는 `analysis.layer_policy: exact`(이름 일치) 또는 `ignore`.
- 지원 길이 단위는 `nm`, `um`, `mm`이며 `data.canonical_length_unit`(현재 `um`)으로 명시 변환한다.
- CA는 legacy 무차원 `1` 또는 원 면적 단위다. 면적 보정은 분자와 unit-pixel 면적을 canonical 단위로 환산해 한 번만 나눈다.
- 이미지 `pixel_size`와 `origin_x/y`는 해당 묶음의 `physical_length_unit`을 사용한다. 현재 실제 자료의 기준 단위는 `um`이다.
- 순위는 `failure_type + 전극 역할 집합 + layer 집합`이 같은 비교 그룹 안에서만 계산한다.
- 서로 다른 CA 크기 격자는 `data.ca_grid`에 정렬한다. 기본 `exact`는 일치하는 크기만, `loglinear_within_range`는 관측 범위 **안**에서만 log-size 선형 보간한다. 범위 밖 외삽 금지.
- 이미지는 종횡비를 유지한 letterbox로 `data.image_size`에 맞추고 여백은 유효 영역 마스크 0이다.

## 6. Fab 가공자료와 적층표

실제 입력 예시와 작성 규칙은 [`domain-input-example/`](domain-input-example/README.md)에 있다. `fab_measurements.csv`는 Fab·제품군·공정 layer·확정 크기 격자의 이물 지름별 `glass 1장당 평균 검출 개수`, glass 한 장의 실제 조사 면적과 측정 기간을 제공한다. 확정: 1–30 um 1 um 간격 + 40, 50 um (32개). real 모드에서는 각 분포에 이 32개 전체 행이 필요하며 격자 밖 크기는 허용하지 않는다.

`layer_stack.csv`는 `product_id, design_version(* 허용), layer_name, stack_order, fab_process_layer`를 가진다. 숫자가 큰 layer를 물리적 상부로 보고 vertical geometry를 검증한다. Legacy(미표시/1.1)에서는 상부 `fab_process_layer`를 assignment로 쓰며, 1.2에서는 source-step assignment를 바꾸지 않는다. 제품 간 이름이 다른 layer의 대응은 아직 보류되어 있어 같은 이름끼리만 비교한다.

`design_review_status.csv`의 `comparison_group_id`는 `ranking.csv` 또는 `review_cards.json`의 값을 그대로 사용한다. 외부 저항·capacitance·coupling·신호·설계 규칙·다른 short/open 검토 상태와 결과 참조만 보존하며 이 프로젝트가 해당 전기량을 계산하지 않는다.

## 7. 확장 지점

- 국부 이미지: 같은 `sample_id` 아래 고정 실제 길이 조각 목록을 추가하는 방식으로 확장한다. 조각을 별도 제품/묶음으로 만들지 않는다(분리 검증 누수 방지). 첫 버전은 전체 이미지만 사용한다.
- 색상 hotspot: `data.hotspot_legend`(`legend_id`, `entries[{rgb, value}]`, `tolerance`)가 있을 때만 확률로 변환한다.

## Fab 매칭 계약 1.2 (2026-09-27)

`provenance.json.mapping_contract_version: "1.2"`를 선언하고 모든 bundle에 nonblank
`assigned_process_layer`(source-step Fab label)와 `fab_pair_key`를 제공한다.
키는 raw ordered `<unit>:<layer_a>-<layer_b>`이며 open은 `<unit>:<layer_a>-N/A`다.
문장 부호/대소문자/Unicode를 보존하고 endpoint를 정렬하거나 delimiter로 분해하지 않는다.
Particle-sim identity map이 있으면 raw tuple/key/assignment를 모두 대조한다.
미표시/1.1은 populated pair key 없이 legacy 규칙을 유지한다. Unknown marker 및 부분 migration은 오류다.
전환할 때 새 export 디렉터리를 만들고 derived cache/model을 다시 생성한다.

Fab JSON/measurement CSV의 `fab_pair_key`는 선택 필드다. null/blank는 layer-wide,
nonblank는 pair refinement이며 `*` wildcard는 없다. CSV source_id 안에서는 blank와 nonblank도 섞을 수 없다.
모든 mode에서 자동 선택은 family+layer-wide → null-family+layer-wide 순서다.
Pair-specific 분포는 자동 선택에 참여하지 않으며 명시적인 fab_distribution_id로만 선택한다.
첫 nonempty tier의 하나만 선택하며 중복/무매칭은 오류다. 절대로 sum/merge하지 않는다.
명시 distribution ID는 자동 선택보다 우선하며 1.2에서 Fab/layer/family/pair/reference를 검증한다.
Legacy 자동 선택은 layer-wide만 고려하며 pair-specific explicit ID도 거부한다.
1.2 reference distribution에는 pair key가 없어야 하며 source assignment와 다른 override는 오류다.
선택된 분포의 누락 bin은 layer-wide fallback을 유발하지 않는다.

Pair ID는 product-local이다. Pair-specific 측정은 항상 검증된 explicit distribution ID로 선택한다.
같은 key가 여러 step에서 반복되어도 sample 행은 별도이며 retrieval/grouping은 기존 geometry를 유지한다.

CA 단위 계약: particle-sim 입력은 원 CA(`ca_nominal_um2`, 확률 mean은 `ca_mean_um2`)를 `um^2`로 전달합니다. ROI 크기는 유지하고 simcompare에서 한 번만 면적으로 나눠 `ca_area_unit="1"`로 기록합니다. legacy 무차원 입력은 기존 `1/um^2` 경로를 유지합니다. 도메인 해석은 담당자 확인 필요입니다.


### CA unit-family comparison (CA-2)

Area CA is converted before comparison to `canonical_length_unit^2` (default `um^2`),
including raw model/PCA channels, CA maxima and CAA. Per-area CA divides by ROI area
once and has unit `1`; legacy dimensionless CA retains `1/um^2` in the default configuration.
CAA uses `relative_index` for legacy CA and `relative_index*um^2` for area CA
(or the configured canonical area unit). This is an area-weighted relative index, not a probability.
Real datasets mixing dimensionless and area families are rejected with counts and sample IDs;
demo mode records a warning. Different area units belong to the same family after conversion.
Legacy CA's original confirmed rule is preserved; the particle-sim sub-rule is provisional.
Cross-family comparisons remain prohibited until a conversion basis is confirmed.
Pre-fix `particle-sim-*` exports with dimensionless CA must be re-exported with the fixed exporter
(real: error; demo: warning). Cards use the first metric complete for every group row in priority
`caa_actual`, `ca_area_max`, `ca_max`; otherwise they use the most observed metric,
with ties resolved in that priority order and missing values preserved.

### R1 전극 지도 요약 규칙

개별 layer 지도는 원천 조건별로 보존한다. 제품 요약은 product_id/design_version/unit_id별 모든 layer의 pixelwise max이며 combined_steps/combined_sources/combined_exposures 및 layer_inputs를 provenance에 남긴다. 서로 다른 frame은 조용히 분리하지 않고 summary unavailable/counts=null 및 incompatible_layer_frames로 표시한다. unavailable layer도 counts=null이며 관측 0 배열과 구별한다. G7b short-only 사용자 결정으로 인한 partial 사유는 없다. source 누락·valid hole은 여전히 partial이며 incompatible summary는 unavailable이다. PNG는 count마다 discrete colour와 legend를 가지며 물리 extent(um)·원본 shape/pitch/origin·status·scope는 sidecar JSON에 남긴다. 미관측/invalid 회색과 관측 0 색을 구별한다.

원천 manifest의 gates는 작성 당시 선언을 그대로 보존할 수 있다. 최신 사용자 결정은 comparison의 applied_gate_decisions, 지도 scope provenance, validation/ROI의 gates에 별도로 표시한다. 원천 pending 선언이나 최신 bin 의미 답변 어느 쪽도 실제 밀도 단위 또는 한 unit 환산 승인으로 사용하지 않는다.
