# 데이터 계약

## numeric_v2 — 수치 경로의 내부 canonical 계약 초안

### real_hotspot_v1 관측 import

`simcompare import-hotspots --root <hotspot-root> --out <new-directory> [--expected-cases <text-file>]`는 사용자가 확인한 실제 폴더 레이아웃을 별도 `real_hotspot_v1` manifest로 변환한다. 입력 root는 바깥 `images` 폴더 유무와 무관하며 하위 case 디렉터리에서 찾는다. case 폴더 안의 PNG를 `<case>/images/`에 둔 배치(`<case>/images/1.png`, `2.png`, `hotspot_*.png` + `<case>/result/<불량명>.csv`)도 받는다. 같은 case에 PNG가 case 폴더와 `images/` 양쪽에 있으면 `ambiguous_real_images_location`이다. 이 배치의 case는 manifest에 `image_subdir: "images"`가 기록되고(평면 배치는 필드 없음) source 경로는 `<case>/images/...`다. case 이름의 불량명은 붙여 쓴 표기(`lateralshort`, `verticalshort`, `lateralopen`, `verticalopen`)도 정식 이름으로 받으며, `failure_type`과 CSV·hotspot 파일명은 정식 이름(`lateral_short` 등)만 쓴다. case 이름은 `불량명-전극1-layer1-전극2-layer2` 다섯 필드이고 `1.png`, `2.png`, `hotspot_<불량명><지름>.png`, case 내부 `result/<불량명>.csv`를 사용한다. case-local CSV가 없으면 다른 위치에서 찾아 대체하지 않는다.

Hotspot PNG는 엄격한 기존 PNG chunk/CRC/zlib/orientation/pixel-limit 검증 뒤 grayscale로만 받는다. 검정은 0, 회색 nonzero는 occupied다. RGB는 세 channel이 pixel마다 동일할 때 grayscale로 허용한다. palette, alpha, 유색, 손상 파일은 거부하며 기존 numeric_v2 binary reader의 의미는 바뀌지 않는다. 전극 image 크기는 서로 같아야 하고 hotspot은 전극의 각 축보다 정확히 1000 px 작아야 한다. importer는 중앙 crop mask를 저장하고 frame origin `(50,50)` µm, pitch `0.1` µm/px, 공통 crop frame을 기록한다. 원본 이미지와 hotspot hash도 보존한다.

mask asset은 압축 저장한다(2026-10-22, ETX `No space left on device` 대응). observed record마다 `mask_path: "assets/<index>_<diameter>.npz"`, `mask_encoding: "npz_deflate_bool_v1"`을 쓰며 파일은 `np.savez_compressed`로 만든 member 하나(`mask`, 2차원 bool)짜리 archive다. `mask_sha256`은 디스크의 `.npz` 파일 hash다. `mask_encoding` 키가 없는 record는 이전 import로 보고 `.npy`(bool, 압축 없음)로 읽으므로 기존 import 결과는 다시 만들 필요가 없다. 그 밖의 encoding 값, 확장자 불일치, member 이름/개수 불일치, 배열이 아닌 member는 `real_mask_encoding_invalid`다. missing record에는 `mask_path`·`mask_sha256`·`mask_encoding`이 없거나 null이어야 한다. `schema_version`은 바꾸지 않았다. 검증(`load_real_manifest`)은 mask를 한 번에 하나만 메모리에 두고 legacy `.npy`는 mmap으로 읽는다. 압축 해제 후 메모리는 이전과 같으므로(pixel당 1 byte) 디스크 크기로 RAM을 추정하지 않는다. import는 끝에 stderr로 쓴 asset 총 byte 수를 출력한다. import가 실패하면 `--out`에 `error.json`/`execution.json`만 남으므로 다시 실행하기 전에 그 폴더를 지운다.

지름은 1–30, 40, 50 µm를 기대한다. `03`은 숫자 3이고 같은 case의 `3`과 중복이다. 누락 image는 `missing`, 검정 관측 image는 면적 `0`인 `observed`이며 서로 대체할 수 없다. 이미지 면적은 occupied pixel count × 0.01 µm²다. case CSV의 `defect_size(um),Area(um2)` 원문 문자열/정밀도와 값은 별도 보존한다. 일치 tolerance는 CSV 마지막 기록 자릿수 절반과 pixel 면적 절반의 합으로 정하고, PNG 면적이나 CSV 값을 덮어쓰지 않는다.

이 계약은 원천 관측용이다. product/design/source/process/exposure/unit, electrode role/polarity, vertical height는 제공되지 않으므로 JSON null로 남는다. `validate-data`는 관측 면적과 missing/CSV 진단을 출력한다. `compare`는 case별 원 면적만 제공하고 line/pixel, density, unit/panel, delta 계산을 unavailable로 둔다. `electrode-map`은 극성 및 pair classification이 확인될 때까지 blocked다. `--expected-cases`(또는 `--expected-cases-from-roots`)가 없으면 발견한 디렉터리 밖의 case coverage는 미확정이다. `--expected-cases-from-roots ROOT...`는 기대 목록을 `--root`와 각 ROOT에서 발견된 case 이름의 합집합(정렬)으로 만들며 `--expected-cases`와 함께 쓸 수 없다. manifest의 `expected_case_source`는 `{"kind":"file"}`, `{"kind":"union_of_discovered_roots","roots":[추가 root(입력 그대로, posix)],"limitation":"cases absent from every root are not detected"}` 또는 null(목록 없음)이며 이 키가 없는 기존 manifest도 유효하다(있으면 모양을 검증). 한 root에만 없는 case는 `expected_case_folder_missing`으로 보고되고 모든 root에 없는 case는 탐지할 수 없다. pair 표나 condition assignment를 폴더명/정렬순으로 만들지 않는다. 한 번에 한 PNG/crop mask만 materialize하며 개별 source pixel 수는 import resource limit로 제한한다. numeric_v2 manifest·loader·legacy 동작은 별도 유지된다.

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

Class 내 서로 다른 pair membership 크기 1은 exclusive, 2 이상은 shared bucket이다. bucket에는 안정 ID, 구성 pair ID, 지름·layer·면적·상태가 있다. pair_lineage_case_keys는 해당 pair의 원천 계보 전체이며 missing/관측 0 case도 포함할 수 있다. contributing_source_case_keys는 해당 지름에서 원본 관측 mask가 실제 그 bucket과 겹치는 case만 나열한다. 리포트는 계보를 면적 기여자로 표시하지 않는다. shared는 나누어 배분하지 않고 bucket ID별 한 번만 합산한다. `exclusive+shared=class_total`을 지름/layer/panel에서 검사한다. 리포트의 이 검산은 bucket 면적의 float 합과 class 총합이 반 셀 면적(0.5 × pitch²) 이내면 통과한다. 두 값은 같은 정수 셀 수에서 오지만 곱셈·합산 순서가 달라 pitch 0.1 µm에서 ulp 차이가 생기기 때문이며, 실제 위반은 최소 한 셀이라 구분된다(2026-10-18). shared 또는 transfer를 종류별 simultaneous라고 부르지 않는다. G7a 사용자 답변(2026-10-08)은 lateral-only/vertical-only/simultaneous 지표 구현이다. 같은 그룹의 mapped short 원본 mask에서 종류별 조각을 계산하며 기존 line/pixel은 독립 축으로 유지한다.

| density 필드 | 의미 |
|---|---|
| identity | `distribution_id`, Fab/product family, `process_layer`, 기간, 원천 ID·hash, 적용 기준 |
| 원 값 | `diameter_um`, `raw_value`, `raw_value_unit`, `observed`, `surveyed_area_um2`, `averaging_basis`, `bin_semantics`, `bin_lower_um`, `bin_upper_um` |
| canonical | `density_per_um2`, `canonical_unit`, `status`, `reasons`, 명시 변환 근거. null은 0이 아님 |

G1 일부 answered(2026-10-08): 지름마다 한 값이며 `bin_semantics=per_bin`이다. 연속 per-um density가 아니므로 bin 폭 질문은 닫혔고 40/50에서도 폭을 곱하거나 적분하지 않는다. 실제 단위, 조사면적/count 변환, 적용 공정/기간은 pending이다. **10 count/glass는 단위·실제 조사면적·bin 의미 없이 10 per um^2로 사용할 수 없다.** per-bin 역면적 density가 확인된 때만 `V=Σ area_um2 × density_per_um2`를 쓴다. per-um 연속 density는 이 계약에서 승인되지 않는다. bin_lower_um/bin_upper_um은 지름별 값 계산에 필요하지 않으며 null로 남길 수 있다. 총 density를 확률 1로 정규화하지 않으며 line/pixel에 같은 계약을 적용한다. observed raw zero도 단위 계약 미확정이면 canonical 가중값은 pending이고, missing density는 별도 기록한다.

`unit_domain`은 측정 domain/frame, unit 치수, domain 배열 형태, `unit_area_basis`, `conversion_policy`, `divisor`, 승인 근거/상태를 남긴다. G2 일부 answered(2026-10-08): 제품 간 um/pixel은 같고 pixel 수는 제품마다 다르며 한 제품의 모든 layer 크기는 같다. 실제 이미지 해상도는 hotspot을 포함해 0.1 um/pixel이다(사용자 확인 2026-10-13; 2026-10-08의 "2×2 약 100 um, 2000 px(약 0.05 um/pixel)" 수치를 대체한다). 이 값도 입력 frame의 기본값으로 만들지 않으며 frame에 명시된 pitch를 쓴다. 한 unit을 얻는 환산 방식은 pending이므로 basis/policy/divisor는 `null`과 `unit_area_basis_unconfirmed`다. **2×2 mask를 기본 `/4`하지 않는다.** full-mask union/면적은 가능하나 unit/panel 가중값·delta는 null이다. 현재 `/4` 관행을 사용자가 확인한 경우에만 명시 정책·한계와 함께 적용하고 새로운 경계 보정/수율 환산은 하지 않는다.

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

### 대응 위치 교환 계약 (`roi_correspondence_v1`) — PLAN_v3 T1

C가 소유하는 형식이다. 수동 라벨, template matching, DL2 검색이 같은 형식으로 두 제품의 대응 위치를 적고, `compare-windows`는 출처와 무관하게 읽는다. envelope은 `schema_version=roi_correspondence_v1`, `producer{kind=manual|template_match|dl2_search, version, commit, created_at}`, `pairs[]`다. 쌍은 `pair_id`, `a`, `b`, 선택 `repeats_per_frame{a,b}`(양의 정수, 측정 frame 안의 반복 수이며 per-unit 수가 아님), `source`, `status=ready|blocked`, `reasons`다. 각 side는 `product_id`, `design_version`, `frame_id`, `center_xy_um`(자기 frame의 물리 좌표), `analysis_layer` 필수이고 `endpoints`(두 결합 객체, 서로 다른 전극), `failure_type`, `roi_id`, `source_id`/`process_step_id`/`exposure_id`/`unit_id` 필터는 선택이다. `source.kind=manual`은 `labelled_by`/`labelled_at`, `dl2_search`는 `score`/`rank`/`checkpoint_fingerprint`/`index_fingerprint`, `template_match`는 `method`/`version`이 필수다. 제품 간 좌표 변환·추론은 없다. a는 config의 reference, b는 target에 묶이며 제품·design·frame 불일치는 파일 거부(`correspondence_*_mismatch`), frame 밖 위치나 원천에 없는 layer는 그 쌍만 blocked다. 같은 두 중심의 중복 쌍은 경고만 남긴다. 변환기 `python -m simcompare.correspondence`는 review ROI manual 요청(endpoints 없으면 export에서 `case_context_required`), roi_id 왕복(identity·중심·문맥 일치만, 형상으로 연결하지 않음), DL2 `roi_relations_v1`(`similar`만), DL1 `product_a,product_b,similar`, DL2 search JSON 역변환(ROI 문맥은 export에서, 점수는 provenance)을 제공한다.

### 변경점 ROI (`candidates-change`) — change-roi T6~T8b

`candidate_queries_v1.json`은 기존 `candidates` 스키마를 그대로 두고 키를 **더한다**. `roi_correspondence_v1`과 `review_roi_v1`은 바뀌지 않는다.

- 최상위 추가 키: `roi_source`(`cnt_change`), `diameter_um`(5.0 고정), `change_args`(`align_search_um`, `align_min_dice`, `align_layer_tolerance_um`, `change_tolerance_um`, `window_um`, `merge_overlap`, `top_k`), `alignment`(`status`, `reasons`, `offset_xy_um`, `dice_best`, `peak_ratio`; 전체는 `alignment.json`의 `cnt_alignment_v1`), `change_map`(`stats`, `pairing`, `cases_excluded`, `cases_only_in_reference`, `cases_only_in_target`), `electrode_set_basis`(`dl1_electrode_folders`), `union_basis`(`paired_and_one_sided_cases`), `alignment_report`(`path`, `bytes`, `downscaled`, `crops_omitted`), `direct`(`correspondence`, `sidecar`, `pairs_ready`, `pairs_blocked`), `mask_derivation_rule`(`between_stack_order_v1`), `mask_derivation_files`(방향별 `{query, index}` = 출력 폴더 기준 상대 경로 또는 export가 없으면 null). `inputs.<side>`에 `mask_derivation_sha256`·`index_mask_derivation_sha256`이 더해진다. `args.min_count`는 null, `args.max_candidates`는 `top_k`다.
- **`mask_derivation.json`(R1, DL2 `psdl2_mask_derivation_v1`).** 질의 export 2개와 index export 2개 폴더마다 `review_rois.json` 옆에 쓴다. 형식과 규칙은 `export-template-rois`의 writer를 그대로 쓴다(`rule_version=between_stack_order_v1`: vertical 은 위 전극 마스크에서 두 끝 층 사이 층 제거, lateral 은 `processed_endpoint=null`·`removed_layers=[]`, CNT 는 DL1 `cno` 합집합, VIA 미사용; 층 순서는 제품의 `condition_assignment.layer_stack`). 항목은 그 폴더 `review_rois.json`의 **ready 행**의 case(failure_type + 정렬한 endpoints)마다 하나이며, 행이 없는 case(한쪽 조합, blocked 행만 있는 case)에는 없다. DL2 `load_dataset`의 `mask_derivation_case_missing`(ready 행에 항목 없음)과 `mask_derivation_case_unreferenced`(항목이 어떤 행과도 안 맞음)를 만들지 않는다. 기존 `candidates`·`export-template-rois` 출력은 바뀌지 않는다. `--mask-derivation-rule`(필수, 기본값 없음, `between_stack_order_v1`만 허용)로 규칙 값을 받는다: `numeric_config_v1`의 최상위 키가 고정이라 config 키를 더하지 않았다.
- 질의(`directions[].queries[]`) 추가 키: `score_um2`(순위 점수 = 층별 hotspot 합집합 변화 면적), `score_basis`, `score_layer`, 속성 `sum_residual_um2`, `changed_case_count`, `union_area_reference_um2`, `union_area_target_um2`, `union_delta_um2`, `observed_fraction`, `contributing_cases[]`(`case_key`, `residual_um2`, `change_kind`=`shifted|reshaped|appeared|disappeared|mixed_undetermined`, `iou_global`, `iou_local`, `local_shift_um`, `flags`, `present_in`, `layer_changed`), `one_sided_cases[]`, `cnt_change_um2_by_layer`. `max_count`는 null이다(취약점 `max_count`와 뜻이 달라 쓰지 않는다).
- 점수는 순위와 표시에만 쓴다. 속성은 순위에 쓰지 않는다.

`direct/roi_correspondence.json`은 평범한 `roi_correspondence_v1`이다. `producer.kind=template_match`, `source={kind: template_match, method: cnt_alignment_v1, version: 1}`이며 b 중심 = a 중심 + 정렬 offset으로 계산한다(`template_match` producer가 b 중심을 계산한다). 계약 문장 "제품 간 좌표 변환·추론은 없다"는 **읽는 쪽**(`compare-windows`)의 규칙이며 바뀌지 않았다: `compare-windows`는 파일의 두 중심을 그대로 읽는다. ready 쌍의 `reasons`는 비어 있다(정렬 불확실성은 사이드카에만 쓴다). blocked 쌍은 `alignment_failed`, `position_outside_valid_domain_<a|b>`, `one_sided_layer_absent_<a|b>`를 가질 수 있다.

`direct/direct_pairs_sidecar.json`(`direct_pairs_sidecar_v1`): `schema_version`, `method`(`cnt_alignment_v1`), `sign`(`p_target = p_reference + offset`), `alignment`(`status`, `reasons`, `offset_xy_um`, `offset_xy_px`, `dice_best`, `peak_ratio`, `pattern_period_vectors_um`, `alternatives`[최대 5]), `inputs`(`config_sha256`, `reference`/`target`의 `manifest_sha256`·`asset_manifest`·`electrode_import_record`, `cnt`), `limitations`(`compare-windows does not verify these input hashes`), `pairs`(pair_id 키: `status`, `reasons`, `offset_xy_um`, `alignment_status`, `alignment_reasons`, `peak_ratio`, `score_um2`, `score_layer`, 속성 여섯 개, `region_id`, `one_sided_case`, `present_in`, `layer_changed`, `change_kind`, `residual_um2`, `iou_global`, `iou_local`, `local_shift_um`, `observed_fraction`, `flags`(한쪽 조합의 `filter_unresolved_<a|b>` 포함), `directions`). 시각·경로·호스트 필드는 없다.

### 사용자 확정 쌍 (`confirm-pairs`) — PLAN_v4 T3

`roi_correspondence_v1`은 바뀌지 않는다. 확정 쌍은 `producer.kind=manual`, `source={kind: manual, labelled_by, labelled_at}`로 쓰고, NCC/DL2 출처는 계약에 필드가 없으므로 사이드카 `confirmed_pairs_sidecar_v1`(pair_id 키: candidate_id=`<direction>:<query_roi_id>:r<rank>`, 점수·순위·flags·score_gap·template_sha256·implementation_revision·입력 sha256·결정 메모·`mutual_status`·`merged_with`·blocked 사유)에만 둔다. 점수는 표시·출처 기록용이며 쌍 선택·정렬·병합·차단에 쓰이지 않는다(S4). `decisions.csv`에서 accept 되지 않은 후보(reject·미결정)는 대응 파일에도 `local_delta`에도 들어가지 않는다. side a는 항상 reference, b는 target이며 `target_to_reference` 후보는 뒤집어 쓴다. `labelled_at`은 파일에 쓰되 `pair_id`에 들어가지 않는다. 변환 전에 candidates → export → 매칭 → **현재** 데이터셋 사슬(manifest·입력 해시, export 바이트 해시, export 행 frame 서술, roi_id 유일성)을 검사하고 어긋나면 `confirm_*_changed_since_candidates`/`duplicate_roi_id_in_export`로 종료 코드 2이다. 상호 병합은 분석층·failure_type이 맞을 때만 하며 다르면 두 쌍을 모두 내보내고 사이드카에 `mutual_context_differs`를 적는다. 사용법과 검사 목록은 `docs/usage.md`의 "확정 쌍 변환"에 있다.

**입력 확장(T10 R3): `psdl2_search_batch_v1`.** `--match <direction>=<file>`의 파일은 `schema_version`으로 갈린다. `psdl2_baseline_ncc_v1`은 위 설명 그대로이고 바이트·동작이 바뀌지 않았다. `psdl2_search_batch_v1`(DL2 `docs/data_contract.md`의 배치 봉투)은 방향별 한 파일이다.
- 질의 = `rankings[i].query_roi_id`(질의 export의 ready 행이어야 하고 `query_product_id`가 질의 제품과 같아야 한다). 후보 = `rankings[i].candidates[]`의 `rank`, `roi_id`, `product_id`, `frame_id`, `center_xy_um`, `search_scope`. `candidate_id`는 기존과 같은 `<direction>:<query_roi_id>:r<rank>`이며 `rank`는 DL2 봉투의 순위(primary 뒤 supplementary를 이어 센 값)다. 점수는 봉투에 없어 `null`이다.
- 질의 export 해시: `rankings[i].query_export_sha256`을 쓰고(데이터셋 질의에서는 `inputs.query_export_sha256`이 null이다) 없으면 `inputs.query_export_sha256`으로 대신한다. 이 값이 candidates 출력의 그 방향 질의 export sha256과 다르면 `match_input_hash_mismatch`(종료 2, NCC 경로와 같은 코드), 둘 다 없으면 `match_input_hash_missing`이다.
- index 쪽: `index_sources`가 배열이면 `export_sha256`이 candidates의 그 방향 index export sha256과 같은 source가 하나는 있어야 한다(없으면 `match_input_hash_mismatch`, 모양이 틀리면 `match_index_sources_invalid`). 그 source들의 `product_ids`에 후보 제품이 들어 있어야 후보가 쓸 수 있고, `product_ids`가 `null`이거나 후보 제품이 없으면 `candidate_product_unverifiable`이다. `index_sources == "single_export"`이면 봉투에 export 해시가 없다(`inputs.index_sha256`은 index 체크포인트 파일의 해시이지 export 해시가 아니다). 그 경우 export 해시는 대조하지 못하고 후보 제품 id만 config 제품과 대조한다. 사이드카 `index_export_verification`이 `unverifiable_single_export`로 그 사실을 남긴다.
- 후보별 사용 불가 사유(그 후보만 목록에 `unavailable_candidates`로 남고 accept 하면 `candidate_match_unavailable`, 종료 2): `unsupported_search_scope`(`primary`/`supplementary` 외), `candidate_same_product`(질의와 같은 제품), `candidate_product_not_configured`(config의 reference/target에 없는 제품), `candidate_product_unverifiable`. `search_scope=supplementary` 후보는 목록에 보이고 `flags.supplementary=true`다. 질의에 후보가 없으면 NCC 경로와 같이 `unavailable_queries`(`no_candidates`)다.
- 출력 `roi_correspondence_v1`은 NCC 경로와 같은 쌍 단위(a=reference, b=target, `producer.kind=manual`, `source={kind: manual, labelled_by, labelled_at}`)다. 사이드카 `confirmed_pairs_sidecar_v1`에는 `inputs.matches.<direction>`에 `match_schema`(`psdl2_search_batch_v1`), `checkpoint_fingerprint`, `view_fingerprint`, `arguments`, `index_sources`/`matched_sources`, `index_export_verification`, `index_sha256`이, 쌍의 `candidates[]`에 `match_schema`, `rank`, `search_scope`, `candidate_roi_id`가 더해진다(NCC 항목에는 이 키가 없다). 점수·순위는 여전히 선택·정렬·병합에 쓰이지 않는다.

### 국부 ΔCA (`local_delta_v1`) — PLAN_v3 T2/T4

창은 대응 중심 `c`와 한 변 `s`(µm, 명시 필수)의 half-open 정사각형 `[cx−s/2, cx+s/2)×[cy−s/2, cy+s/2)`이며 `PhysicalWindow`/`measure_union`으로 원본 mask를 그대로 잰다. 대상은 해당 제품·`analysis_layer`·지름 group의 mapped short union과 `lateral_only/vertical_only/simultaneous` 조각이고 창 안에서도 조각 합 = union이다. group 선택은 product·frame·layer·지름에 side 필터를 더해 정확히 하나여야 하며 없으면 `no_source_group_*`, 여럿이면 `ambiguous_source_group_*`다. frame 전체의 valid 구멍(`partial_observation`)은 창 안에서 다시 판정하고, 원천 누락·미매핑·rejected는 창 측정도 partial로 만든다. 창 값은 `{value, partial_value, status, reasons, window_area_um2, valid_area_um2, fraction_of_window}`이며 fraction은 선언 창 면적(정확한 십진 사각형 면적)으로 나눈다. Δ는 `b − a`로 양쪽 available일 때만 available이고, 양쪽 not_applicable이면 not_applicable, 그 외는 `value=null`과 `a_partial`/`b:…` 사유에 원인별 `window_clipped_<side>`, `valid_incomplete_<side>`, `below_height_vertical_short_<side>`를 더한다. 완전성(창이 frame 안인지)은 선언 십진수 경계로 판정해 frame 가장자리에 맞닿은 창이 float 오차로 partial이 되지 않는다. 창 측정은 창 주변 한 셀 여백의 crop에서 하며 전체 frame 측정과 같은 값이다. endpoints가 있는 named_pair 값이 같은 창의 group union을 넘으면 `named_pair_exceeds_group_union_<side>`를 기록한다. group·case 매칭은 product·design_version·frame·layer·지름과 side 필터를 쓴다. 부분 Δ는 없다. attributable core는 한 변 `s − d`의 안쪽 정사각형이고 `s − d ≤ 0`이면 `window_smaller_than_diameter`로 not_applicable이다. 선택 jitter는 중심을 x·y 각각 ±j 이동한 네 창의 Δ_union 값·범위·부호 일치를 기록하며 임계값은 없다. endpoints가 있으면 unordered 전극 집합(+failure_type)으로 case를 찾아 그 mask의 창 안 CA를 `named_pair`로 추가한다. `explained_fraction`은 모든 ready 쌍에 `repeats_per_frame`이 있을 때 side별 Σ(창 union CA × repeats) / frame_area와 참조 group들의 `fraction_of_frame(union)` 차이를 나란히 둔다. 같은 group의 겹치는 창은 합집합으로 한 번 재고 repeats가 다르면 그 지름은 unavailable이다. 정렬 지름이 있으면 창 크기별 |Δ_union| 내림차순 순위를, 항상 창 크기 민감도(`window_sensitivity_v1`: 쌍×지름의 크기별 Δ, 부호 일치, 크기 쌍 간 Spearman, core 적용 가능 지름)를 쓴다. 추천 창 크기는 계산하지 않는다. HTML은 PNG를 base64로 내장하고 제품 간 좌표를 겹쳐 그리지 않으며 선택 IoU는 셀 중심 표본의 `registration: none`이다.

### frame 면적 비율 (`fraction_of_frame`) — PLAN_v3 T3

각 group의 line, pixel_effective, transfer, union, 세 조각에 `fraction_of_frame = area / frame_area_um2`를 붙인다. `frame_area_um2 = shape_yx[0]×shape_yx[1]×pitch²`(실제 import 경로에서는 중앙 2×2 crop frame)이고 `valid_area_um2`를 함께 기록한다. 상태는 해당 AreaMetric을 따른다: available이면 value, partial이면 `partial_value = partial area / frame_area`, 그 외 null. 제품 간 `compare_fractions`는 같은 ComparisonKey·지름에서 양쪽 available일 때만 `target − reference`를 내고, 한쪽에 없는 layer는 0이 아니라 `*_layer_unknown`이다. 분자·분모가 같은 frame 위에 있으므로 G2의 한 unit 환산(/4)이 상쇄되어 유닛 크기가 다른 제품을 비교할 수 있다. 유한 2×2 경계 효과는 남는 한계이며 밀도·unit·panel 가중, 확률, 수율이 아니다. 기존 면적·가중·panel 값과 CSV 열은 바꾸지 않고 `comparison_fractions.csv`와 JSON `fraction_of_frame`/`fraction_deltas`를 추가한다.

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

### 이진 이미지의 극성 (2026-10-09)

asset 참조의 `value_semantics`는 두 값만 허용한다. `binary`는 0이 아닌 값이 표시 영역이고 기본값이다. `binary_zero_is_marked`는 0(검정)이 표시 영역이다. 사용자 자료의 hotspot 이미지는 PNG이고 검정 배경 / 흰 패턴(hotspot이 흰색)이므로 실제 hotspot asset에는 `binary`를 적는다(사용자 확인 2026-10-13; 2026-10-09의 "hotspot이 검정" 기록을 대체한다). 극성은 자동으로 판별하지 않는다. 극성을 반대로 적으면 여집합 면적이 나오므로 폐쇄망 manifest에서 반드시 지정한다. record 수준의 `value_semantics`는 `binary`만 허용하며 극성은 asset 참조에만 적는다. 값은 허용된 두 수준의 집합(0/1 또는 0/255)에 속해야 하고 threshold는 적용하지 않는다. 한 값만 있는 전체 검정/흰 mask도 허용한다.


### R5: asset polarity 명시

non-synthetic asset reference는 value_semantics를 반드시 명시한다. 누락은 explicit_value_semantics_required이며 실제 hotspot(흰 패턴)은 binary다(2026-10-13 확인). binary_zero_is_marked는 검정이 표시인 자료를 위해 남아 있다. synthetic inline fixture는 기존 nonzero-is-marked 기본을 유지한다. 실제 adapter 승인은 여전히 G2 pending이다. C reader는 NPY 및 아래의 strict PNG 계약을 지원한다. DL1 importer의 layout/electrode·CNT PNG는 검정 패턴 / 흰 배경이고, compare의 실제 hotspot PNG는 흰 패턴 / 검정 배경 (`binary`)이다. 실제 mode/bit depth 실물 확인과 G2 adapter/unit 환산 승인은 별도다.


### electrode asset import (review_assets_v1 source)

`simcompare import-electrode-assets`(`adapters/electrode_png.py`, 2026-10-19, PLAN_v4 T1a)는 **새 schema가 아니다**. 기존 `review_assets_v1`을 실제 전극 PNG에서 만든다. case 문맥은 numeric dataset에서만 오고 어댑터는 endpoint를 이름이 정확히 같은 PNG에 묶는다. 출처 종류별 극성 계약 표(코드 `SOURCE_KIND_CONTRACT`): `dl1_png_folder`, `hotspot_case_electrode` 모두 `value_semantics=binary_zero_is_marked`(검정 = 형상), `polarity_basis=user_answer_2026_10_19`. 표에 없는 종류는 `electrode_polarity_unspecified`로 blocked다. asset은 mode L PNG {0,255}로 쓰며 DL2 `read_asset`이 받는 값(`binary`, `binary_zero_is_marked`)만 쓴다. PNG는 DL1 strict 규칙(정확한 검정/흰색, 불투명, 8비트, 항등 EXIF, threshold 없음)으로 디코딩한다.

layout PNG는 전체 frame, dataset frame은 hotspot crop이므로 원점 (50,50) µm·pitch 0.1·`shape+1000 px`를 검사하고 중앙 500 px를 정확히 잘라 쓴다(crop offset·전체 shape·원본 SHA256은 provenance). 50 µm 테두리 문맥 손실은 알려진 한계다. 변환 기록에는 source_kind, polarity_basis, 파일별 hash, adapter 버전, input hash, 개수를 남긴다. manifest의 case 항목과 최상위에 선택 키 `electrode_asset_provenance`, `electrode_asset_import`를 더하지만 기존 reader(`roi_export.check_asset_manifest`)는 이를 무시한다(시험으로 확인). 실자료(real_hotspot_v1) dataset을 `export-review-rois`가 소비하는 경로는 별도 Task(T1b)이며 그 전에는 합성 모드 numeric_v2 dataset만 소비한다.

### Strict numeric PNG reader (2026-10-13)

reader만 강화했으며 실제 non-synthetic manifest는 여전히 `real_input_requires_approved_adapter`로 load 단계에서 차단한다. 실자료 adapter를 열거나 unit 환산을 추측하지 않는다. 원본 byte buffer를 한 번 읽고 그 buffer의 SHA256을 확인한 뒤 구조를 검증하고 같은 buffer에서 decode한다. 경로 해석·NPY·`check_hash`의 계약과 기존 수치 계산은 유지한다.

| 수락 입력 | 조건 |
| --- | --- |
| greyscale (PNG colour type 0) | bit depth 1/2/4/8; Pillow mode 1/L. 모든 확장 pixel은 정확히 0/255이며, 기존 8-bit L의 0/1도 동일한 mask로 수락한다. 0/1과 0/255를 섞은 1/255는 거부한다. |
| RGB, LA, RGBA | bit depth 8; 모든 pixel이 정확히 검정 또는 흰색이고 alpha는 전부 255. RGB→luminance 변환이나 threshold를 하지 않는다. |
| palette (P, colour type 3) | bit depth 1/2/4/8; PLTE가 있고 모든 사용 index가 entry 범위 안이다. 확장 색은 정확히 검정/흰색이며 tRNS가 있다면 사용하지 않는 entry까지 전부 alpha 255여야 한다. |
| interlace | 0 (none) 또는 1 (Adam7). 각 pass의 정확한 scanline byte 수와 filter가 검증된다. |

모든 chunk의 signature/order/CRC, IHDR dimensions/depth/type/method, 단일 IDAT zlib stream과 정확한 inflated 길이, PLTE/index를 Pillow decode 전에 독립적으로 검사한다. APNG의 `acTL`/`fcTL`/`fdAT`는 단일 프레임도 거부한다. grey/RGB의 tRNS는 transparent sample이 사용되지 않아도 거부한다. non-255 palette alpha, 비불투명 alpha pixel, 회색/색 pixel, IEND 뒤 byte, 잘못된 ancillary CRC, EXIF/XMP/raw-profile의 orientation != 1은 거부한다. 기존 reader의 **I / I;16 PNG 수락은 제거**됐으며 **16-bit PNG는 `png_unsupported_bit_depth`**다 (tracked fixture 의존 없음 확인 후 승인; 과거 open_questions의 mode 목록은 R6에서 현행 계약으로 정정). NPY integer mask 수락은 바꾸지 않는다.

Pillow의 설치된 `MAX_IMAGE_PIXELS`를 올리거나 끄지 않는다. 한계를 넘는 warning/error는 모두 `NumericContractError`이고, 비활성화된 guard도 오류다. truncation을 수락하는 전역 옵션은 decode 동안 false로 고정하고 원래 값으로 복구한다. 구조 검증은 compressed snapshot + bounded inflate block + 최대 두 packed rows를 보존하며 전체 inflated stream을 쌓지 않는다. 픽셀 decode와 반환 mask에는 원본 pixel 수에 비례하는 메모리가 필요하다. `import-electrode-assets`의 메모리 가드는 숫자 데이터셋(frame.valid와 관측된 모든 diameter별 mask)을 이미 올린 뒤에 실행되며, 그 배열을 추정에 포함하지만 한도를 넘는 모든 할당을 막지는 않는다.

모든 PNG 실패는 안정된 reason과 요청한 상대 경로를 포함한다. 구조: `png_invalid_signature`, `png_truncated_chunk`, `png_invalid_chunk`, `png_invalid_chunk_order`, `png_crc_mismatch`, `png_invalid_header`, `png_unknown_critical_chunk`; 데이터: `png_invalid_idat_stream`, `png_idat_length_mismatch`, `png_invalid_scanline_filter`, `png_missing_palette`, `png_invalid_palette`, `png_palette_index_out_of_range`; 의도된 엄격성: `png_apng_unsupported`, `png_invalid_transparency`, `png_nonopaque_transparency`, `png_nonopaque_pixels`, `png_unsupported_bit_depth`, `png_trailing_bytes`, `png_nonidentity_orientation`, `nonbinary_nominal_mask`, `unsupported_png_mode`; decoder: `png_pixel_limit_warning`, `png_pixel_limit_error`, `png_pixel_limit_disabled`, `png_decode_failed`. hash 불일치는 `input_hash_mismatch`다.

복구는 **plain opaque black/white PNG로 다시 저장**하는 것이다. 손상/잘림의 원본을 먼저 복구하고 animation/transparency/orientation metadata/16-bit를 제거한 정확한 이진 pixel을 확인한다. reader가 자동 threshold, crop, 회전, resize 또는 누락 pixel zero 채움을 하지 않는다. manifest의 SHA256과 극성을 새 원본에 맞춰 검토한다. hotspot의 `binary`는 흰 pixel, `binary_zero_is_marked`는 검정 pixel을 표시 영역으로 반환한다.


Strict PNG 동작은 lock의 **Pillow 12.3.0에서만** 실행 검증했다. `pyproject.toml`이 `pillow>=10.0`을 허용하는 것은 다른 버전의 decoder 동작 검증을 의미하지 않는다. 폐쇄망 설치는 lock의 **12.3.0**을 사용하고 버전을 확인한다. lock/pyproject 요구사항은 바꾸지 않는다.

R6의 알려진 한계(N4/N7/N9/N10/N11): compare에는 pixel-limit override가 없으며 설치된 Pillow 기본 한계(12.3.0 기본 89,478,485 pixels, compare가 선택한 숫자가 아님)를 넘는 파일은 거부한다. context 교체 시 generator 소유 디렉터리 전체를 지우므로 그 안에 사용자가 둔 파일과 다른 context 사본이 참조한 지도도 함께 사라질 수 있다. corrupt eXIf는 `png_decode_failed`로 거부하지만 CRC가 정상인 corrupt post-IDAT zTXt/iCCP는 pixel이 정확하면 수락하며, 빈 iCCP의 decoder 예외는 계약 오류로 반환한다. 이미지 해상도는 hotspot을 포함해 0.1 µm/pixel로 사용자가 확인했으며(2026-10-13) DL1의 고정 0.1 µm/pixel과 일치한다. 이전 기록의 약 0.05 µm/pixel 수치는 쓰지 않는다. 결과 목록의 완전성을 증명하지 않으므로 최상위 후보 삭제 또는 primary를 모두 비우고 unavailable로 변경한 일관된 native 파일은 계속 수락한다.
