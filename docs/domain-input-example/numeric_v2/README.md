# numeric_v2 합성 입력·출력 검산 예제

이 패키지는 [PLAN_v2 §2.1–2.4 / T1](../../plan/PLAN_v2.md)의 내부 canonical **문서 초안**이다. [데이터 계약](../../data_contract.md)과 [질문 상태](../../open_questions.md)를 함께 읽는다. 실제 제품/검출 자료가 없으며 모든 EXAMPLE ID·숫자·배열은 합성이다. 현재 로더/CLI의 실행 입력이나 실자료 승인 결과로 사용하지 않는다. 실제 원천 format은 G2 pending이고 inline 배열은 문서 검산용이다.

| 파일 | 역할 |
|---|---|
| `manifest.json` | canonical case/frame/coverage, raw 원천 ID, 합성 mask, 상태 출력, pending 게이트 |
| `pairs.csv` | product-local 전극 pair의 line/pixel 분류, 표 버전 |
| `density.csv` | 32개 raw density 행과 G1으로 차단된 canonical 값 |

## 읽는 규칙과 식별자

JSON null과 CSV 빈 셀은 미확정/미관측이며 0이 아니다. CSV의 boolean은 `true/false`, 지름은 um, `raw_value`는 비음수 유한 숫자 또는 빈 셀이다. `reasons`는 세미콜론으로 구분한 사유 코드다. `status`와 이유는 같이 읽는다. `observed=true, raw_value=0`과 `observed=false, raw_value=`는 구별한다. 빈 `bin_semantics`, 경계, 조사면적, canonical density/unit은 모두 null로 해석한다.

pair 표의 `EXAMPLE_pairs_v2_1`은 manifest와 각 행에 동일하게 남는다. pair ID와 전극 ID는 이 합성 제품 안에서만 유효하다. 실제 source의 `fab_pair_key`는 raw ordered 문자열이다. 예제 `EXAMPLE_UNIT:M1-M2`를 delimiter로 분해해 전극 A/B를 만들지 않는다. raw 문자열과 `ordered_endpoints`는 변경 없이 보존한다. 원천 case ID는 product/design/source/process/exposure/frame/version과 결합해야 유일하다.

hash 알고리즘은 SHA-256이다. `files.*.sha256`와 `pair_table_hash`는 저장된 CSV의 실제 UTF-8 파일 bytes(헤더·LF 포함)를 해시한다. `input_hash`와 `source_manifest_hash`는 이 문서용 source payload의 같은 해시다. payload는 manifest의 다음 키만 포함한 객체를 `json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')`로 직렬화한다:

`source_format`, `source_contract_version`, `is_synthetic`, `diameters_um`, `frames`, `layer_stack`, `layer_process_mapping_status`, `cases`, `unit_domain`, `panel`, `pair_table_version`, `files`.

`outputs`, `gates`, hash 필드 자체는 payload에서 제외하므로 자기 참조가 없다. CSV hash가 payload에 포함되므로 density/pair 변경도 입력 hash를 바꾼다. 실제 원천 어댑터는 실제 source manifest와 파일 bytes의 hash/산정 범위를 별도로 기록해야 한다. 이 합성 hash를 실측 원자료의 hash로 대입하지 않는다.

## 32 지름과 0 / missing / N/A

모든 case는 지름 `[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,40,50]`의 32개 record를 갖는다. 배열은 이물 중심 궤적이며 재팽창하지 않는다. frame은 shape yx=2×4, pitch=1 um, 원점 xy=(100,200) um, 좌상단 cell-edge, valid=전부 1이다. domain은 `[100,200,104,202)`이며 셀당 면적은 1 um^2다.

| case | 유형 | observed | missing | N/A | 검산 |
|---|---|---:|---:|---:|---|
| `EXAMPLE_LAT_AB` | lateral_short | 31 | 1 | 0 | d1 mask 전부 0 → available, CA=0; d40 mask=null → unavailable/missing_input |
| `EXAMPLE_VERT_AB` | vertical_short | 30 | 0 | 2 | height=3 um, d1/d2 → not_applicable, CA=null, below_height_vertical_short |
| `EXAMPLE_VERT_BA` | vertical_short | 30 | 0 | 2 | 위와 같은 물리 case 문맥을 반대 endpoint 순서로 기록한 합성 반례 |

총 expected=96, observed=91, missing=1, N/A=4다. 기대 record 수가 96이라는 것은 전체 입력 관측 완료라는 뜻이 아니다. d3=height는 적용 가능하다. 누락된 d40을 높이 미만으로 추론하지 않는다. d1에서 vertical 두 case가 N/A라도 lateral의 관측 zero는 그대로 available이다.

## 순서 불변 class와 실제 layer 결합

| case | endpoint 0 | endpoint 1 | unordered pair/class | 상부 분석·density 귀속 |
|---|---|---|---|---|
| `EXAMPLE_VERT_AB` | A / gate / M1 | B / data / M2 | AB / line | M2 / STEP_M2 |
| `EXAMPLE_VERT_BA` | B / data / M2 | A / gate / M1 | AB / line | M2 / STEP_M2 |
| `EXAMPLE_LAT_AB` | A / gate / M2 | B / data / M2 | AB / line | lateral M2 / STEP_M2 |

표의 축약 ID는 manifest의 EXAMPLE 접두 ID를 뜻한다. reverse는 endpoint 객체 전체를 바꾼다. A의 layer를 M2로 바꾸거나 role을 data로 바꾸는 변환이 아니다. lateral과 vertical은 같은 pair/class여도 별도 source case이고 `failure_type`이 모두 남는다. 순서 반례의 두 vertical case가 같은 mask를 덮어도 OR 영역을 두 번 더하지 않는다.

d5의 lateral mask는 첫 행 첫 두 셀, 각 vertical mask는 첫 셀만 덮는다. 같은 source/process/exposure/M2/frame 그룹에서 pair AB union과 line 면적은 2 um^2, pixel_effective=0, transfer=0, U=2다. exclusive AB=2, shared=없음이며 case CA는 각각 2,1,1로 별도 남는다. case CA 합 4를 union 2로 오인하지 않는다. 원천별 유형은 이 OR 뒤에도 source_case_ids로 연결된다. CD/pixel 행은 표 형식 예시이며 이 패키지의 expected source case에는 CD가 없다. 실제 expected case 부재를 0으로 만드는 근거로 사용하지 않는다.

## density와 panel이 pending인 이유

`density.csv`는 별도의 32개 행을 갖는다. d1 raw count=0은 관측 zero, d40 raw 값은 누락, 나머지는 합성 10 count/glass다. **10 count/glass를 10 per um^2로 읽을 수 없다.** 단위·glass 실제 조사면적·평균 기준·지름 bin의 의미와 범위를 확인해야 하며 count/glass 표기만으로 단위 환산이 끝나지 않는다. 조사면적, per-bin/per-um 의미, 모든 bin 경계는 비어 있고 40/50의 폭은 1 또는 10으로 가정하지 않는다. canonical density는 전 행 null/unavailable이다. raw 0도 미확정 계약을 해결하지 않는다.

unit 치수는 합성 2×1 um이고 측정 domain은 4×2 um(2×2 배열)이다. `unit_area_basis`, `conversion_policy`, `divisor`는 null/pending이다. **2×2라는 이유로 CA/4를 기본 계산하지 않는다.** full-mask d5 면적만 검산 가능하다. panel 치수 20×10 um과 floor 제안은 합성 메타데이터로만 남기고 unit_count 및 unit/panel line·pixel, target−reference delta는 null이다. reference 자체도 제공되지 않았다.

출력의 metric `status=unavailable`과 정책 `decision_status=pending`은 다른 필드다. `partial`은 알려진 부분합에 쓰고 완전 panel/delta로 승격하지 않는다. 가중값 단위도 G1 전에는 null이다. line/pixel 두 가중값을 합친 수율을 만들지 않는다.

| 게이트 | 상태·답변 | null인 이유 / 차단 |
|---|---|---|
| G1 | pending / null | density 단위·조사면적·bin 의미/폭·공정/기간 답변 없음 → 실자료 parser 최종 승인·가중값 불가 |
| G2 | pending / null | 실제 mask/frame/case/height·layer↔step·unit 환산 근거 없음 → 실자료 reader/migration 승인·unit/panel 완료 불가 |
| G7a | pending / null | 종류별 지표 제외 제안 미답변 → 구현/제외 확정 불가; case 유형·기존 line/pixel은 보존 |
| G7b | pending / null | 지도 open/shorts-only 미답변 → 전체 지도·지도 유래 ready ROI 수락 불가 |

합성 stack M1<M2와 STEP_M2 대응은 synthetic_only이며 G2 실자료 답변이 아니다. 1.2 원천 source-step assignment를 상부 layer로 자동 바꾸지 않는다. 실제 변환에서 두 의미가 충돌하면 `migration_assignment_conflict`와 raw 값·상부 판정·대응 근거를 보고하고 명시 확인 전 실패/pending으로 둔다. 변환 없는 기존 1.x/CAA/AE 입력은 unsupported다.

## review-ROI 교환 예시 (후속 T6/T10/T12 계약)

실제 export 파일/asset은 이 문서 작업에서 만들지 않는다. 아래는 `review_roi_v1`의 **문서 예시**이며 missing asset을 ready라고 표시하지 않는다. 모든 필드 규칙은 데이터 계약의 review-ROI 절을 따른다. root 내부 상대 asset 경로와 파일 hash가 확보되어야 실행할 수 있다. hotspot/CA/density/rate 파일은 embedding 입력 패키지에서 제외한다.

```json
{
  "schema_version": "review_roi_v1",
  "producer_commit": "EXAMPLE_DOCUMENT_ONLY_NOT_AN_EXPORT",
  "source_manifest_hash": "manifest.json의 source_manifest_hash를 그대로 전달",
  "assets_root": ".",
  "rois": [
    {
      "roi_id": "EXAMPLE_MAP_ROI_AB",
      "product_id": "EXAMPLE_PRODUCT",
      "design_version": "EXAMPLE_DESIGN",
      "case_id": "EXAMPLE_VERT_AB",
      "source_id": "EXAMPLE_SOURCE",
      "process_step_id": "EXAMPLE_STEP_M2",
      "exposure_id": "EXAMPLE_EXPOSURE",
      "source_contract_version": "EXAMPLE_SYNTHETIC_V2",
      "source_kind": "electrode_count_region",
      "source_region_id": "EXAMPLE_REGION_1",
      "selection_policy_version": "EXAMPLE_bbox_center_v1",
      "frame_id": "EXAMPLE_FRAME",
      "length_unit": "um",
      "origin_xy_um": [100, 200],
      "pixel_pitch_um": 1,
      "shape_yx": [2, 4],
      "axis_order": "yx",
      "origin_convention": "top_left_cell_edge",
      "center_xy_um": [100.5, 200.5],
      "region_bbox_um": [100, 200, 101, 201],
      "endpoints": [
        {"electrode_id": "EXAMPLE_A", "role": "gate", "actual_layer": "EXAMPLE_M1"},
        {"electrode_id": "EXAMPLE_B", "role": "data", "actual_layer": "EXAMPLE_M2"}
      ],
      "failure_type": "vertical_short",
      "original_assets": {"layout": null, "cnt": null, "via_endpoints": null, "valid": null},
      "asset_observation_status": "missing",
      "selection_provenance": {
        "diameter_um": 5, "threshold": 2, "connectivity": 4,
        "coverage": {"expected_cases": 3, "observed_cases": 3},
        "map_scope": null, "G7b_status": "pending"
      },
      "status": "blocked",
      "reasons": ["map_scope_unconfirmed", "source_shape_required"]
    }
  ],
  "excluded_records": []
}
```

위 snippet의 producer/hash 문자열은 설명용으로 실행 artifact의 commit/hash를 대신하지 않는다. 실제 T6은 정확한 producer commit과 이 manifest의 실제 hash를 기록해야 한다. 같은 지도 영역에 `EXAMPLE_VERT_BA`가 관여하면 별도 ROI 행과 roi_id를 만들고 같은 source_region_id/center 관계를 유지한다. source case를 하나로 축약하거나 두 행을 독립 제품/무조건 negative로 취급하지 않는다. 영역 bbox는 near/wide crop 크기가 아니다.

manual 행은 `source_kind=manual`, `source_region_id=null`, `region_bbox_um=null`과 제품/설계/원본 frame/물리 center·case 또는 명시 전극/layer 문맥을 갖는다. 위치만 있으면 `blocked: case_context_required`, 원본 shape 연결이 없으면 `blocked: source_shape_required`다. 이 패키지에는 원본 shape/CNT asset이 없어 manual도 실행 예제가 아니다. 실제 독립 case/frame/shape 조건 및 asset hash가 충족되면 manual은 G7b 미답변과 무관하게 ready일 수 있다. G4 pending은 위치 export를 막지 않지만 실제 near/wide crop recipe 확정을 막는다.

C/T6이 두 출처를 내보내고 DL2/T10은 ready 행만 같은 물리 중심에서 crop하며 crop/valid/source version을 보존한다. T12는 C→DL2→C의 roi_id·복합 source identity·원본 frame·asset hash를 대조한다. DL2 검색 결과나 제품 간 등록으로 새로운 ROI를 자동 선정하지 않는다. 선택 provenance와 수치 지표는 tensor에 넣지 않는다.

## 문서 검증

저장소 루트에서 다음 명령은 JSON 문법만 검사하며 계산 구현/실자료 수락을 증명하지 않는다.

```sh
python3 -m json.tool docs/domain-input-example/numeric_v2/manifest.json
```

질문 상태는 [open_questions.md](../../open_questions.md)의 answered/pending 표로 추적한다. G1/G2/G7a/G7b가 pending인 상태에서 문서·합성 부분만 검토 가능하다.
