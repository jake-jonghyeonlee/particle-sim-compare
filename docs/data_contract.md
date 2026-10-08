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

Class 내 서로 다른 pair membership 크기 1은 exclusive, 2 이상은 shared bucket이다. bucket에는 안정 ID, 구성 pair ID, 지름·layer·면적·상태가 있다. pair_lineage_case_keys는 해당 pair의 원천 계보 전체이며 missing/관측 0 case도 포함할 수 있다. contributing_source_case_keys는 해당 지름에서 원본 관측 mask가 실제 그 bucket과 겹치는 case만 나열한다. 리포트는 계보를 면적 기여자로 표시하지 않는다. shared는 나누어 배분하지 않고 bucket ID별 한 번만 합산한다. `exclusive+shared=class_total`을 지름/layer/panel에서 검사한다. shared 또는 transfer를 종류별 simultaneous라고 부르지 않는다. G7a 사용자 답변(2026-10-08)은 lateral-only/vertical-only/simultaneous 지표 구현이다. 같은 그룹의 mapped short 원본 mask에서 종류별 조각을 계산하며 기존 line/pixel은 독립 축으로 유지한다.

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

[open_questions.md](open_questions.md)의 numeric_v2 표가 질문 상태를 추적한다. G1/G2는 일부 answered이며 미답변 부분은 pending/null, G4는 pending이다. G7b는 short-only로 answered다. G7a는 answered이며 종류별 조각의 면적·가중·panel·delta가 구현됐다. 현재 line/pixel·shared의 의미는 유지한다. G1 단위/조사면적은 실자료 가중값을, G2 한 unit 환산·원천 승인은 unit/panel 및 migration을 차단한다. 합성 문서 검산은 가능하며 게이트 승인이나 실자료 성능 검증이 아니다.

---

### R1 전극 지도 요약 규칙

개별 layer 지도는 원천 조건별로 보존한다. 제품 요약은 product_id/design_version/unit_id별 모든 layer의 pixelwise max이며 combined_steps/combined_sources/combined_exposures 및 layer_inputs를 provenance에 남긴다. 서로 다른 frame은 조용히 분리하지 않고 summary unavailable/counts=null 및 incompatible_layer_frames로 표시한다. unavailable layer도 counts=null이며 관측 0 배열과 구별한다. G7b short-only 사용자 결정으로 인한 partial 사유는 없다. source 누락·valid hole은 여전히 partial이며 incompatible summary는 unavailable이다. PNG는 count마다 discrete colour와 legend를 가지며 물리 extent(um)·원본 shape/pitch/origin·status·scope는 sidecar JSON에 남긴다. 미관측/invalid 회색과 관측 0 색을 구별한다.

원천 manifest의 gates는 작성 당시 선언을 그대로 보존할 수 있다. 최신 사용자 결정은 comparison의 applied_gate_decisions, 지도 scope provenance, validation/ROI의 gates에 별도로 표시한다. 원천 pending 선언이나 최신 bin 의미 답변 어느 쪽도 실제 밀도 단위 또는 한 unit 환산 승인으로 사용하지 않는다.

### 불량 유형 조각 (`failure_pieces`) — G7a 구현

사용자 답변(2026-10-08)에 따라 line/pixel union과 같은 product/design/unit/source/process/exposure/layer/diameter/frame 그룹에서 mapped short case의 유형을 사용한다. lateral mask 합집합을 A, vertical mask 합집합을 B라 하면 `lateral_only=A\B`, `vertical_only=B\A`, `simultaneous=A∩B`다. 세 조각은 서로 겹치지 않고 합이 U다. 같은 pair의 lateral/vertical도 원천 유형을 잃지 않아 겹친 셀은 simultaneous다. open 조각은 없고 line/pixel class와 무관하게 mapped short 전체를 사용한다. pair가 미매핑이면 원본 CA는 보존하고 union/조각의 완전값을 차단한다. shared는 서로 다른 pair membership이며 simultaneous와 다르다.

GroupArea.failure_pieces에는 세 AreaMetric(um^2), LayerRate.failure_pieces에는 각각 unit/panel WeightedMetric(명시 synthetic 역면적 density × area의 단위 1), LayerDelta.failure_pieces에는 각각 panel target−reference가 있다. 지름별 원 면적에 같은 density를 곱해 합산하고 제품마다 N을 따로 쓴다. 평균이나 density 정규화는 없다. 선/화소값·transfer·pair bucket에 조각을 더하거나 빼지 않는다. JSON은 각 그룹/가중 layer/delta 및 failure_pieces_table에 보존하며 CSV는 comparison_failure_pieces.csv, HTML은 line/pixel 옆 별도 조각 표와 서로 다른 축이라는 설명을 제공한다.

height 근거 있는 vertical N/A는 해당 case만 제외하므로 다른 lateral 값은 available일 수 있다. source/density 누락·valid 불완전·미매핑은 value=null과 별도 partial_value 및 사유다. 관측 원천만의 부분 조각은 추가 관측에 따라 다른 조각으로 이동할 수 있어 완전한 분류나 delta가 아니다. 두 제품 모두 complete일 때만 delta가 available이다. 실제 density 단위/조사면적(G1)과 2×2→한 unit 환산(G2)은 pending이며 area는 보존하지만 weighted/unit/panel/delta는 null이다. 기본 /4나 위치별 인과 기여는 구현하지 않는다.

### heat-map 셀 표시 크기

작은 원본 격자의 표시 scale은 `max(1, ceil(400 / max(height_px, width_px)))` 정수다. 2×4는 scale=100, 표시 영역 400×200이며 2000 px 격자는 scale=1이다. 이는 Pillow nearest-neighbour 표시 확대이며 원본 count/frame/물리 extent/CA를 바꾸지 않는다. sidecar.rendering.scale 및 cell_pixels는 같은 정수이고 grid_bbox_px는 확대된 표시 영역이다. discrete 색·invalid 회색·legend는 유지하며 좌표값은 원본 um다. 큰 격자를 축소하지 않는다.

## 유지된 legacy export 계약 (1.x, numeric_v2와 별도)

`schema.py`, `adapters/example.py`, `preprocess.py`, `merge.py`, `adapters/particle_sim_run.py`는 원 export의 연결과 ID를 검산하는 호환 경로다. CLI의 merge-exports는 파일을 병합할 뿐 numeric_v2로 변환하지 않는다. 실제 numeric adapter와 migration은 G2 pending이다.

기존 AE/CAA 실행, CAA placeholder, legacy demo generator는 폐기되었다. docs/arch와 walkthrough는 legacy / archived 기록이다. 아래는 유지된 파일/원천 계약이며 기존 학습·순위·CAA 기능의 사용 지침이 아니다.

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

## Fab 매칭 계약 1.2 (2026-09-27)

`provenance.json.mapping_contract_version: "1.2"`를 선언하고 모든 bundle에 nonblank
`assigned_process_layer`(source-step Fab label)와 `fab_pair_key`를 제공한다.
키는 raw ordered `<unit>:<layer_a>-<layer_b>`이며 open은 `<unit>:<layer_a>-N/A`다.
문장 부호/대소문자/Unicode를 보존하고 endpoint를 정렬하거나 delimiter로 분해하지 않는다.
Particle-sim identity map이 있으면 raw tuple/key/assignment를 모두 대조한다.
미표시/1.1은 populated pair key 없이 legacy 규칙을 유지한다. Unknown marker 및 부분 migration은 오류다.
전환할 때 새 export 디렉터리를 만들며 폐기된 model/cache를 재사용하지 않는다.

Fab JSON/measurement CSV의 `fab_pair_key`는 선택 필드다. null/blank는 layer-wide,
nonblank는 pair refinement이며 `*` wildcard는 없다. CSV source_id 안에서는 blank와 nonblank도 섞을 수 없다.
loader는 원 분포와 명시 distribution ID, Fab/layer/family/pair/reference 관계를 검증한다. 기존 CAA의 자동 선택/가중 실행은 폐기되었다. 원 분포 누락을 다른 분포로 채우거나 합치지 않으며 numeric density로 자동 변환하지 않는다.

Pair ID는 product-local이다. Pair-specific 측정은 항상 검증된 explicit distribution ID로 선택한다.
같은 key가 여러 step에서 반복되어도 sample 행은 별도이며 loader는 원 geometry와 source identity를 유지한다.

CA 단위 계약: particle-sim 입력은 원 CA(`ca_nominal_um2`, 확률 mean은 `ca_mean_um2`)를 `um^2`로 전달합니다. ROI 크기는 유지하고 simcompare에서 한 번만 면적으로 나눠 `ca_area_unit="1"`로 기록합니다. legacy 무차원 입력은 기존 `1/um^2` 경로를 유지합니다. 도메인 해석은 담당자 확인 필요입니다.



### 유지된 CA 단위 검사

legacy 무차원 CA(1)와 원 면적 CA(nm^2, um^2, mm^2)는 다른 계열이다. real loader는 혼합 계열을 거부하고 legacy demo 모드 loader는 경고한다. 여러 면적 단위는 canonical 길이 제곱으로 환산한다. 원 면적 CA는 ROI 면적으로 한 번만 나눠 단위 1이며 legacy 무차원 입력의 면적 보정은 1/um^2다. pre-fix particle-sim dimensionless export는 real 오류/demo 경고로 re-export를 요구한다. 이 호환 규칙은 numeric G1/G2의 답변이 아니다.

source-step assignment는 1.2 원천 의미로 보존한다. legacy 미표시/1.1 vertical의 상부-layer 검증과 구별하며 numeric 실제 상부 귀속으로 조용히 reinterpret하지 않는다. 모든 경로는 ID, 상대 경로, 단위, 중복 지름과 missing/zero의 구별을 유지한다.

## R2 출력 및 설정 검산 보완

one_unit config는 manifest의 unit_domain frame·unit_width_um/unit_height_um·measured size 및 domain_shape_units_yx와 대조한다. 다-unit 측정 domain을 one_unit으로 선언하면 unit/panel 값은 null과 unit_area_basis_unconfirmed로 차단한다. 한 unit 추출 방식 G2는 pending이며 /4를 적용하지 않는다.

여러 layer는 각 density_process_layer의 별도 distribution을 사용한다. 같은 layer의 여러 후보는 ambiguity 오류이며 임의 합산하지 않는다. 분포가 없는 layer에 missing_layer_density_distribution을 남기고 나머지 layer의 값은 유지한다. 실제 밀도 단위 G1은 여전히 pending이다.

지름별로 vertical case가 존재하지만 모두 height evidence로 N/A이면 vertical_only와 simultaneous는 not_applicable/value=null이다. lateral_only와 전체 short union은 적용 가능한 lateral case로 계산한다. missing vertical은 N/A로 바꾸지 않는다. 합계 보존에서 N/A는 기여 없음이며 관측 0으로 표시하지 않는다.

numeric_electrode_map_report_v2의 counts/valid는 compressed NPZ 참조다. 기본 JSON/HTML은 frame·histogram·상태·provenance만 포함하고 셀별 CSV는 없다. --per-cell-text는 최대 4096 cells다. PNG는 순서형 discrete palette와 physical sidecar를 유지한다. default regions는 없고 명시 threshold를 요구한다.

직렬화 출력의 gate는 current_gate_decisions를 하나의 기준으로 사용한다. 원 source manifest의 gate 기록은 loader 내부에 보존하며 현행 적용 결정과 혼합해 출력하지 않는다. is_synthetic은 실제 입력 provenance에서 유도하고 고정 true로 쓰지 않는다. producer_commit의 checkout 부재는 이유를 붙여 null로 기록한다. CLI 오류는 관리 리포트를 제거하고 실패 marker만 남기며 성공은 stale error.json을 지운다.
