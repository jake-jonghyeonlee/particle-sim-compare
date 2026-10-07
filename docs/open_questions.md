# 확정 필요 사항과 현재 임시 처리

"실제 경로"는 `mode: real` 설정, "demo 경로"는 `mode: demo` 설정을 뜻한다. 물리적 의미를 바꾸는 미확정 값은 실제 경로에서 오류를 내고, demo 경로에서만 명시적 demo 규칙으로 진행한다.

| 항목 | 현재 임시 처리 | 실제 경로 동작 | 확정 후 수정 위치 |
|---|---|---|---|
| 실제 파일 형식·디렉터리 구조 | CSV/JSON + PNG/NPY 예시 어댑터 | 어댑터 교체 전제 | `src/simcompare/adapters/` 새 어댑터 |
| Hotspot 색상·투명도·축척 | **2026-10-01 사용자 확정**: 실제 nominal은 binary(0/1, PNG 0/255는 0/1 복호). 한 source·지름에서 `CA_R = k × N_active,R`, particle-sim 면적 nominal만 `k = pitch²` | gray는 가공 강도로만 사용하고 CA로 환산하지 않음. 색상은 범례(`data.hotspot_legend`)가 있을 때만 확률 변환 | `adapters.example.read_image`, `preprocess.legend_to_values`; source별 계수 계약 |
| CA 의미·범위 | legacy **확정**: 0~1 무차원 확률 기반 누적 위험값, 보통 최대 0.02~0.03, 면적 정규화 전 값. particle-sim 면적 규칙은 provisional; 환산 근거 확정 전 교차 계열 비교 금지 | 원 CA와 `CA / unit-pixel 면적`을 함께 사용; 혼합 계열 real 오류/demo 경고 | `schema.AnalysisBundle.ca_per_area`; 계산 담당자 환산 기준 확인 |
| Fab 자료 | **확정**: 특정 기간의 glass 1장당 평균 검출 개수와 glass 한 장의 실제 조사 면적을 가공자료로 입력 | `fab_measurements.csv`, 총량은 조사 면적으로 정규화 | `adapters.example`, `caa.fab_shape_and_total` |
| CAA 실제 계산식 | **2026-10-01 사용자 결정**: 실제 Fab 가중은 폐쇄망에서 확인 후 확정. 현재는 `demo_probability_per_bin`: 동일 크기 격자의 확률 가중치 × CA 합 | demo 규칙 사용 시 설정 오류, 확정 규칙 미구현 시 `CAAError`. 확정 전 CA 기반 비교·위치 리포트는 별도 진행 | `caa.interpret_fab`에 확정 규칙 추가, `caa.confirmed: true` |
| 크기 격자 | 확정: 1–30 um 1 um 간격 + 40, 50 um (32개), CA·hotspot·Fab 전체 제공 | `exact`, 보간하지 않음 | `data.ca_alignment` |
| Vertical short 적용 공정 layer | **2026-09-27 계약 1.2**: source-step Fab layer; **legacy(미표시/1.1)**: 적층표 상부 Fab layer | `layer_stack.csv`가 없거나 순서를 판정할 수 없으면 오류 | 입력 데이터/어댑터 |
| 이물 유입 공정의 Fab 분포 layer | **결정**(2026-09-30): 착지 layer 바로 위 마스크 layer(patterned 또는 contact hole layer, 마스크 없는 절연막 제외)의 Fab 값을 쓴다. particle-sim manifest가 `fab_distribution_layer`를 이 규칙으로 정하고, 계약 1.2 export는 그 값을 `assigned_process_layer`로 넘긴다. 위에 마스크 layer가 없으면 manifest에 직접 적는다 | compare는 `assigned_process_layer`를 그대로 쓴다(추정하지 않음) | particle-sim `ProductManifest.upper_mask_layer`, extractor `export_config.upper_mask_row` |
| 제품 간 전극/layer 대응·비교 가능 조건 | **2026-10-01 사용자 결정**: 제품 전극/layer 대응표는 실사용 시 추가. 현재 layer 이름 문자열 일치(`layer_policy: exact`) 또는 무시(`ignore`) | 지역 리포트의 명시 대응 입력(`analysis.regional.correspondence_file`)은 구현됨. `retrieval.candidate_mask`는 이름/ignore 정책을 유지하며 실제 대응 자료는 실사용 시 제공. 대응 불명은 부재·위험 0으로 해석하지 않음 | 대응표 입력 계약과 `retrieval.candidate_mask` 및 비교 리포트에 대응표 적용 |
| Lateral open 전극 치수 정의 | 형상 수치(`shape_metrics`)의 이름만 전달, 의미 해석 없음 | 해석하지 않음 | `geometry.json` 정의 확정 |
| 국부 이미지 범위·이물 영향 영역 | **2026-10-01 사용자 확정**: hotspot은 이물 전체 크기를 포함한 궤적이며 실제 nominal 입력은 binary. 한 source·지름 안에서 `CA_R = k(source,d) × N_active,R` | hotspot 포함 고정 창을 사용하고 반경으로 재팽창하지 않음. particle-sim 면적 nominal만 `k = pitch²`; 다른 source의 k와 legacy 무차원→면적 환산은 미확정 | `adapters.example.read_image`, `regional_measurement`, `regions`; source별 계수 계약 |
| 전문가 검토 기준·개선 후보 제약 | 간격·폭·평행 길이·겹침·우회 후보를 넓게 제시하고 사람이 검토 | 외부 저항·Cap·coupling·신호·DRC·다른 short/open 결과 상태만 연결 | `cards.build_review_cards` |
| 전문가 정답 라벨 | 현재 없음 | 정확도·precision·recall을 주장하지 않고 held-out 반복성만 보고 | `pipeline.run_evaluate` |
| 학습/평가 분리 단위 | 제품 ID와 설계 계열 ID의 연결 성분 | 그룹 2개 미만이면 오류 | `split.group_split` |
| CNO(`contact_not_open`, contact·via hole 막힘) 불량 유형 | **결정**(2026-09-30): compare에 넣지 않고 particle-sim 단독 항목으로 둔다. `FAILURE_TYPES`에 없음. particle-sim exporter는 CNO unit을 제외하고 `export_warnings.json`에 남기며, `particle_sim_run` 검사기는 미지원 경고를 낸다. 가림 기준은 particle-sim에서 "margin 없는 이물 원판이 hole 중심을 덮으면 막힘, hole 하나라도 막히면 CNO"로 확정(`cno_coverage_rule=hole_centre_any_hole`) | 기본은 경고, `--strict`면 실패 | 없음(결정이 바뀌면 `schema.FAILURE_TYPES`, 어댑터, `dataset`/`model`, `caa`, 분석·카드) |
| open 불량 회피안(선폭 보강) | **결정**(2026-09-30): 보강(금속 추가) 편집은 하지 않는다. `edit_candidates`는 open 질의를 사유와 함께 건너뛴다(`edit_candidates.csv`의 `status=skipped`). 비교군 비교와 선폭 차이(`pair:a_min_width`)는 참고용으로 계속 계산한다 | 편집 후보 없음 | 없음 |
| 설계 규칙(최소 선폭·간격) 입력 | **보류**(2026-09-29): 입력 가능 여부 미확인 | 규칙 위반 편집 후보도 걸러지지 않고 제시된다. 다른 불량 증가 허용 여부와 함께 설계자가 판단 | 재개 시 `analysis` 설정 또는 별도 규칙 파일, `variants.edit_candidates`에서 후보 제외 |

## 2026-10-01 결정에 따른 구현 기준

아래는 사용자 확정 사항을 반영한 후속 구현 기준이며, 기능 구현 완료를 뜻하지 않는다.

- Hotspot은 이물의 전체 크기가 이미 반영된 궤적이다. 표시·위치 선정에 이를 그대로 사용하고, 이물 크기를 반영한다는 이유로 다시 팽창시키지 않는다. 주변 전극을 보기 위한 문맥 여백은 별도 설정으로 구분한다.
- 비교 영역은 hotspot을 포함하도록 잡는다. 위치 정렬이 가능한 비교에서는 양쪽 hotspot과 비교할 이물 크기들의 범위를 포함하는 공통 창을 고정하여, 각각 다른 crop 면적 때문에 증감이 달라지지 않도록 한다. 정렬이 불가능하면 위치별 차영상·직접 증감은 제공하지 않고 별도 영역으로 표시한다.
- 확정된 binary nominal 입력은 한 source·지름 안에서 모든 활성 픽셀의 CA 기여가 같다. 일반식은 `CA_R = k(source,d) × N_active,R`이며 ROI로 나눠도 픽셀별 강도 차이가 생기지 않는다. particle-sim 면적 nominal(`ca_nominal_um2`)만 생산자 정의와 보존 검사에 따라 `k = pitch²`다. 다른 source는 계수를 별도로 선언해야 하며, 서로 다른 pitch/ROI가 같은 계수를 가진다고 가정하지 않는다.
- 범례·가공 강도·출처 불명 이미지는 여전히 궤적 합집합 면적이 CA와 같다고 가정하지 않는다. 정량 원천 선언과 동일-domain 보존 검사 없이 밝기나 픽셀 수를 CA로 승격하지 않는다. mean/probability 원천은 binary nominal과 별개다.
- 이번 확인은 한 source 안의 상수 계수만 확정하며 legacy 무차원 CA를 µm²로 바꾸는 환산 계수를 확정하지 않는다. 해당 환산과 일반 binary 자료의 정량 원천 선언은 후속 결정으로 남긴다.
- 대응표는 실사용 시 사용자가 제공한다. 사전 개발은 입력 형식과 연결 지점을 준비할 수 있으나, 대응 관계를 임의 확정하지 않는다. Fab 가중은 폐쇄망에서 확인하며, 미확정 동안 Fab 가중 점수·수율 환산을 제시하지 않는다.
- 보고용 취약부는 hotspot을 포함한 영역으로 선정한다. 향후 Tree 학습 표본은 이와 구분하여 비취약 영역도 포함하고, hotspot은 정답·위치 근거로만 사용해 정답 누출을 방지한다.
