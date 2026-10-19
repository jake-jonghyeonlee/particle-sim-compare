# numeric_v2 CLI와 review_roi_v1 위치 계약

근거는 채택된 [PLAN_v2 §2.2–2.4, T6](plan/PLAN_v2.md)다. 2026-10-08 사용자 답변은 implementation_memory.json의 gate_answers_2026_10_08에 기록돼 있다. G7b는 short-only로 answered다. G1 지름별 한 값과 G2 격자 크기 사실은 answered이며 단위/조사면적 및 한 unit 환산은 pending/null이다. G4는 pending, G7a 조각 지표는 구현되었으며 line/pixel과 별도 축이다. 실자료 수락은 보류다. 합성 계약으로 계산하는 숫자는 사용자 답변을 대신하지 않는다. 실제 원천 형식은 G2 확인 전 실행하지 않는다.

## 명령과 종료 코드

```sh
simcompare demo-data --out demo_data
simcompare compare --config demo_data/config.yaml
simcompare electrode-map --config demo_data/config.yaml
simcompare validate-data --config demo_data/pending.yaml
simcompare export-review-rois --config demo_data/config.yaml --out demo_data/review_rois
```

`demo-data`는 numeric_v2만 생성한다. legacy generator와 train/embed/analyze/evaluate/all은 T7에서 폐기되었다. 수치 명령은 학습을 호출하지 않는다.

| exit code | 의미 |
|---|---|
| 0 | 명령의 합성 계산/생성이 완료됨. 실제 자료 승인 의미 없음 |
| 3 | 읽을 수 있는 부분 결과 또는 blocked 검토 위치 목록. status/reasons를 반드시 확인 |
| 2 | 설정/입력 계약 오류 또는 사용 불가 입력. stderr 오류 및 가능한 경우 error.json, 완성 리포트는 새로 쓰지 않음 |

관측이 완전한 short-only 지도는 available/exit 0이다. CLI electrode-map의 누락·valid hole·frame 불일치는 exit 2로 처리하고 리포트 세트를 쓰지 않는다. API의 per-layer/summary 상태와 부분 하한은 별도로 보존한다. numeric validation은 pending으로 exit 3, comparison은 명시 합성 계약에서 complete 0 또는 부분 3이다. CLI 오류 시 관리 리포트 파일은 이전 실행분까지 제거하고 error.json/execution.json만 남긴다. 정상 출력 후에는 stale error.json을 제거한다.


## numeric_config_v1

상대 data_dir/output_dir는 설정 파일 기준이다. reference/target에는 단일 product_id와 data_dir를 명시한다. map에는 독립 input 및 diameter_um(기본 5), min_count(기본 null)를 선택적으로 넣는다. min_count가 없으면 regions=[]이며 영역·순위가 없다. config의 명시 min_count 또는 --min-count N이 있을 때만 4-neighbour 영역을 계산한다. export-review-rois도 --min-count를 지원한다. 비교는 각 제품의 원본 격자에서 별도로 수행하여 제품 간 좌표를 overlay하지 않는다.

합성 계산에만 `density_contracts`(DensityContract 필드), `unit_bases`(UnitAreaBasis 필드), `panel`(PanelSpec 필드)를 명시할 수 있다. 원천의 지정 distribution_id를 선택하고 분포를 합치거나 정규화하지 않는다. line/pixel은 분리하며 transfer는 진단 순변화다. 2×2 mask의 기본 divisor는 없다. 지름마다 한 값이므로 bin 폭을 곱하지 않는다. G7a 종류별 조각 지표는 별도 Task다.

`comparison.json`에는 T3/T4 전체 schema와 case별 원 CA, frame, coverage, weighted bucket/delta, 계산 provenance가 있다. CSV는 summary, areas, buckets, case_ca, weighted_buckets, delta_buckets로 구분한다. buckets의 pair별 shared 반복행은 같은 bucket_id의 링크이며 합계는 ID별 한 번만 센다. `bucket_totals`는 이 중복 제거를 검산하며, float 합산 오차는 반 셀 면적까지만 허용한다. weighted CSV는 partial_value·coverage·source/process 문맥도 보존한다. null은 CSV 문자열 `null`, 사유는 JSON 배열이며 빈 숫자나 0으로 바꾸지 않는다. HTML은 모든 텍스트/ID를 escape한 정적 JSON 표시다. map CSV는 원본 row/col과 물리 셀 좌상단 좌표, valid 및 관측 count 하한을 표시한다.

## 원본 asset manifest 읽기 검사

numeric input 설정의 `asset_manifest: {path, sha256}`는 data_dir 내부 상대 JSON을 참조한다. 이는 합성 내부 `review_assets_v1` 형식이며 실제 원천 형식을 정한 것이 아니다. shape/CNT/VIA/valid는 T2 strict binary reader로 읽기 검사만 한다. threshold/resize/warp/좌표 추정 없이 원본 shape/pitch/origin/valid를 유지한다.

```json
{
  "schema_version": "review_assets_v1",
  "source_manifest_hash": "numeric manifest의 input_hash",
  "cases": [{
    "identity": {"product_id":"P","design_version":"D","source_id":"S","process_step_id":"STEP","exposure_id":"E","case_id":"A","frame_id":"F","source_contract_version":"synthetic_v2"},
    "endpoints": [{"electrode_id":"A","role":"gate","actual_layer":"M2"},{"electrode_id":"B","role":"data","actual_layer":"M2"}],
    "failure_type":"lateral_short",
    "source_version":"원본 layout 버전",
    "frame": {"frame_id":"F","length_unit":"um","origin_xy_um":[100,200],"pixel_pitch_um":0.5,"shape_yx":[8,10],"axis_order":"yx","origin_convention":"top_left_cell_edge"},
    "assets": {"layouts":[{"endpoint":{"electrode_id":"A","role":"gate","actual_layer":"M2"},"asset":{"path":"assets/a.npy","sha256":"파일 SHA256","observed":true,"value_semantics":"binary"}}],"valid":{"path":"assets/valid.npy","sha256":"파일 SHA256","observed":true,"value_semantics":"binary"},"cnt":{"path":"assets/cnt.npy","sha256":"파일 SHA256","observed":true,"value_semantics":"binary"},"via":[]}
  }]
}
```

위 축약 예제의 layouts에는 실제로 양 끝 endpoint 각각 한 행이 필요하다. layout endpoint binding과 identity/failure_type/frame은 원본 case와 정확히 같아야 한다. 모든 asset은 root 내부 상대경로·파일 hash·observed·binary 의미를 요구한다. valid는 T2 frame.valid와 같아야 한다. CNT가 관측된 0이면 0 배열 참조를 사용하며 미관측을 0으로 만들지 않는다. VIA가 존재하면 참조를 제공한다. 절대경로/`..`/symlink 탈출, hash/shape/valid 불일치는 해당 case를 blocked로 만든다. hotspot/CA/density/rate 종류의 asset은 허용하지 않는다.

## 실자료(real_hotspot_v1) export 조건

`export-review-rois`는 mode=real에서 `map.input`의 `condition_assignment`, `asset_manifest`, `electrode_import_record`(각각 `{path, sha256}`, 후자 둘은 data_dir 상대 경로)가 모두 있고 `electrode_asset_import_record_v1`과 `review_assets_v1`이 같은 `polarity_basis`(출처별 계약 표와 일치)를 기록하며 모든 asset의 `value_semantics`가 `binary`/`binary_zero_is_marked`일 때만 `configured_dataset`과 electrode-map 영역으로 ROI를 만든다. 극성은 실행 중 추측하지 않는다. 증거가 없으면 이전과 같은 `unavailable`(exit 3), 표 밖 출처·값이면 `blocked electrode_polarity_unspecified`(exit 3, ROI 없음), hash·dataset identity(`source_asset_manifest_identity_mismatch`)·기록 binding(`electrode_import_record_mismatch`) 불일치는 exit 2 오류다. ROI 행 스키마는 바뀌지 않으며 위치는 hotspot에서 온 영역, 레이아웃 asset은 PNG에서 온 것이다.

## review_roi_v1

출력 envelope는 schema_version, producer_commit, source_manifest_hash, assets_root(원본 root), rois, excluded_records를 갖는다. 원본 asset 파일은 복사·수정하지 않으며 참조한 asset의 상대경로/hash를 그대로 전달한다. 이 참조 목록만 입력 패키지로 사용하며 원본 root의 다른 파일을 자동 수집하지 않는다.

review_rois 설정의 `map_region_ids`는 명시 영역 ID 목록 또는 명시 `all`, `manual`은 지정 위치 목록이다. 출처는 `electrode_count_region` 또는 `manual` 두 가지다. 검색 결과/제품 간 구간 대응으로 ROI를 만들지 않는다. 지도 영역에 여러 case가 있으면 복합 source identity별 다른 roi_id/행으로 남기고 source_region_id로 위치 관계를 연결한다. row/col bbox는 물리 bbox로 T5에서 변환된 값을 사용한다: origin(100,200), pitch 0.5, bbox(2,4,6,8) → (102,201,104,203), centre(103,202). 끝은 exclusive이며 bbox는 crop 크기가 아니다.

manual에는 product_id/design_version/frame_id/center_xy_um와 case_id 또는 명시 endpoint binding이 필요하다. case_id만으로 여러 source 조건이 맞으면 source/process/exposure 등을 더 지정해야 한다. 불명확하면 case_context_required/ambiguous로 blocked이며 임의 case를 고르지 않는다. frame/shape/valid/원본 asset 연결도 검사한다. 명시 endpoint 문맥이 단일 원본 case를 결정할 때만 그 case identity를 보존한다.

지도 대표 지름·threshold·4-neighbour connectivity·coverage·G7b는 선택 provenance에만 둔다. 수동 위치가 기본 출처다. 지도 행은 명시 threshold로 계산한 영역 중 사용자가 선택한 경우에만 내보내며 scope 미확정 때문에 차단하지 않는다. source coverage가 불완전하면 map_coverage_incomplete로 차단한다. 완전한 case/frame/원본 shape 문맥의 수동 위치와 명시 영역은 ready일 수 있다. threshold 없이 map_region_ids=all을 요구하면 map_threshold_required를 제외 기록에 남긴다. 지도는 취약 위치를 보여 주고 DL2는 사용자가 고른 위치와 유사한 구조를 검색한다. G4 pending은 `crop_recipe={status:pending,value:null,reasons:[crop_recipe_unconfirmed]}`로 기록하며 near/wide 크기를 만들지 않는다. 원본 endpoint/role/actual_layer/failure_type/source version을 유지하며 hotspot/CA/density/rate 및 지도 선택 provenance는 model tensor 입력이 아니다. DL2 소비와 왕복 검증은 T10/T12의 소유다.

## heat-map 출력

`electrode_map.json`은 layer와 product/design/unit summary의 작은 메타데이터이며 count·valid 압축 NPZ를 상대 경로·hash로 참조한다. shape, origin/pitch, histogram, 상태와 short-only 사용자 결정을 기록한다. 기본 셀별 텍스트는 없고 --per-cell-text는 4096 cells 이하에서만 가능하다. PNG와 sidecar는 그대로 제공하며 작은 grid는 정수 nearest 확대, 2000 px grid는 1:1이다. 순서형 색은 count가 증가하면 한 방향으로 진해지고 회색 invalid와 흰색 count 0은 구별된다. 2×2 domain을 그대로 표시하며 /4나 제품 간 overlay는 없다. 명시 threshold가 있어야 regions를 만들고 exporter는 API의 원 source/frame 연결을 사용한다.


```sh
simcompare electrode-map --config demo_data/config.yaml --min-count 3
# 영역을 선택하여 export할 때 설정에 review_rois.map_region_ids를 명시
simcompare export-review-rois --config demo_data/config.yaml --min-count 3 --out demo_data/review_rois
```

## producer revision 출처

producer_commit은 이 패키지 자체의 git checkout root에 .git이 있을 때만 HEAD를 읽는다. 설치 패키지/사본이 다른 git 저장소 아래에 있어도 부모 저장소 HEAD를 기록하지 않는다. git checkout 밖에서는 producer_commit=null, producer_commit_reason=producer_not_in_git_checkout이다. 명시 producer_commit을 전달할 수 있으며 producer source가 해당 root에 tracked되지 않으면 producer_source_not_tracked이고, git 실패는 producer_git_revision_unavailable 또는 producer_git_unavailable로 기록한다.
