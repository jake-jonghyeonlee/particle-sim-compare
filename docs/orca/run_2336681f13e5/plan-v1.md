# ROI CA 비교와 위치 중심 통합 리포트 — 계획 v1

- 작성: 2026-10-01, Codex Astra high, 읽기 전용 자문 Task `task_4563cf66520a`, Dispatch `ctx_fd4c5866dd7c`.
- Run: `run_2336681f13e5`. 채택 권한: Opus coordinator `term_889af2e3-9387-477f-965b-075206f2b853`. 이 문서는 제안이며 구현 완료나 독립 PASS가 아니다.
- 계약 정본: `docs/orca/run_2336681f13e5/request.txt` 전체, 기준선: 같은 디렉터리 `checkpoint.md`, 결정: `docs/open_questions.md` 전체를 읽었다.
- 기준 저장소: `/Users/jonghyeonlee/git/particle-sim-compare`, branch `record-cno-open-fab-decisions`, HEAD `b507da96554baa658171dcae61a99342c9ec36e3`.
- 보존할 기존 변경: `docs/open_questions.md`, `src/simcompare/adapters/particle_sim_run.py`, `tests/test_particle_sim_export_integration.py`. 시작 시 `git diff | shasum -a 256` = `a00a5706a34852ba3bd4c72397d87268b510ed5cce666a586aeeeb1dfc8a28b3`. adapter의 upper-mask Fab layer 검증과 integration fixture 수정은 이번 구현 성과가 아니다.
- 형제 저장소 `/Users/jonghyeonlee/git/particle-sim`은 아래 근거를 읽는 데만 사용했다. 네트워크, 설치, Git mutation, source/test/config 수정은 수행하지 않았다. 계획 단계에서 테스트를 재실행하지 않았으며 `209 passed, 56 skipped`는 coordinator의 기준선 기록이다.

## 1. 직접 확인한 코드 근거

아래 `src/...`는 compare 저장소, `../particle-sim/...`는 형제 저장소의 현재 작업 파일을 뜻한다. 줄 번호는 이 계획 작성 시 작업 트리 기준이다.

| 위치 | 실제 동작과 설계에 주는 제약 |
| --- | --- |
| `src/simcompare/schema.py:76–107` | `ImageInput`은 원본 2D 배열, scalar `pixel_size`, 선택적 `origin_xy`, 크기·semantics를 갖는다. `CACurve`는 크기/값/observed와 원 단위를 보존한다. 이미지가 있다는 사실만으로 정량 CA 원천 계약이 성립하지 않는다. |
| `src/simcompare/schema.py:155–185`, `:218–240`, `:249–309` | `AnalysisBundle`에 지역 계약이 없다. CA 면적을 unit 면적으로 나누면 무차원이고 legacy 무차원 CA를 나누면 `1/um^2`이다. 결측은 NaN+observed=False, real에서 혼합 CA 계열을 거부한다. 기존 `canonicalize_ca`는 in-place이므로 신규 순수 지역 함수가 이를 호출해 입력을 변형하면 안 된다. |
| `src/simcompare/adapters/particle_sim_run.py:162–176` | `hotspots.zarr`를 먼저, 없으면 `probability.zarr`를 고른다. manifest의 x/y pitch와 shape로 면적을 계산하나 anisotropic이면 scalar pixel_size는 None이다. 두 store가 한 디렉터리에 있다고 임의로 일관된 run으로 인정하면 안 된다. |
| 같은 파일 `:517–529`, `:632–675`, `:695–697` | CA는 `args.ca`로 nominal/mean을 선택하고 `curve.calc_settings.ca_source`에 기록한다. hotspot은 run_type에 따라 `nominal_failure`/`probability`를 별도로 고른다. 크기별 원본 plane을 사용하되 `origin_xy=(0,0)`을 넣으며 layout은 없다. probability run + 기본 nominal CA는 정량 source-mode mismatch다. 이 checker는 현재 analyze의 데이터 로더가 아니다. |
| `src/simcompare/adapters/example.py:109–146`, `:429–480`, `:593–630`, `:747–778`, `:798–819` | PNG를 255로 나눈 원 해상도 배열로 읽으며 hotspot을 보통 processed_intensity로 표시한다. origin은 CSV에서 보존한다. registration 검사는 **한 bundle 내부**의 shape/pitch/origin 일치이며 cross-product 등록이 아니다. exporter raw identity mapping을 검증하지만 최종 bundle에는 현재 원 raw tuple 전체가 보존되지 않는다. provenance calc_settings는 전달된다. |
| `src/simcompare/dataset.py:107–155`, `src/simcompare/pipeline.py:86–89` | `prepare`는 example adapter 원본 bundle과 모델용 encoded 배열을 함께 반환한다. 모델용 이미지는 별도 크기로 처리하므로 ROI 측정은 `loaded.bundles` 원본으로 해야 한다. |
| `src/simcompare/neighborhood.py:41–104`, `:107–131` | 공통 관측 크기의 **면적 보정 CA 최대값**으로 이웃 위험·저위험 reference를 고른다. geometry delta는 reference-query이다. 신규 지역 delta는 아래에서 query-reference로 명시하므로 기존 표와 혼동하지 않게 필드·설명을 분리한다. |
| `src/simcompare/variants.py:105–119`, `:138–169` | 기존 편집 ROI는 큰 크기부터 heat 최대점의 50% 이상 연결성분을 선택하고 입자 크기로 pad한다. fallback은 최근접 전극 구간이다. open은 skipped다. 신규 ROI는 이 helper를 재사용하지 않고 이 편집 동작은 유지한다. |
| `src/simcompare/retrieval.py:42–83` | 후보는 role_key가 같아야 하고 layer는 exact/ignore 두 정책이다. 이름 일치나 embedding 이웃은 확인된 제품 대응표가 아니다. 새 비교의 자격 판정에 이 mask만 사용하면 안 된다. |
| `src/simcompare/pair_geometry.py:123–177` | 전체 전극쌍의 min gap, near boundary 길이, overlap, min width를 계산한다. scalar pitch로 환산한다. 이 전체 수치를 ROI-local feature로 복사하지 않는다. |
| `src/simcompare/pipeline.py:248–264`, `:318–429` | analyze는 학습 checkpoint 검증, risk/search/cluster/LOO, pair geometry, neighborhood, legacy edit candidates, cards와 SVG를 쓴다. 신규 파이프라인은 마지막에 결과를 추가하는 얇은 호출로 붙인다. |
| `src/simcompare/pipeline.py:92–118` | training fingerprint는 data_dir의 파일 대부분과 data/caa/model/train/split 설정을 해시한다. report 전용 대응표/원천 manifest를 data_dir 밖에서 참조하면 재학습 계약을 바꾸지 않고 별도의 report fingerprint로 관리할 수 있다. 기존 fingerprint 규칙을 느슨하게 만들지 않는다. |
| `src/simcompare/plots.py:1–5`, `:25–38`, `:134–163`; `src/simcompare/cards.py:14–22`, `:41–80` | 그림은 추가 의존성 없는 SVG, XML escape와 시스템 한글 글꼴을 사용한다. 기존 카드는 metric completeness, 반복 형상 등의 전문가 검토 요약이다. 위치 선정 알고리즘으로 재사용하지 않는다. |
| `src/simcompare/config.py:82–106`, `:142–149`, `:566–572`, `:730–747` | dataclass + strict unknown-key 검증이며 analysis 설정에는 지역 키가 없다. 상대 data/output 경로는 YAML 기준으로 푼다. 신규 중첩 설정도 같은 원칙을 지킨다. |
| `src/simcompare/model.py:19`, `:202–225` | struct modality는 layout/hotspot/ca/geometry다. `ae_struct`도 shape-only가 아니며 미래 지역 모델의 무누출 입력이라고 주장할 수 없다. |

### 생산자: 실제 CA 적분 원천이 존재한다

| 위치 | 검증한 사실 |
| --- | --- |
| `../particle-sim/src/particle_sim/aggregation/critical_area.py:10–20`; `io/masks.py:22–40` | `critical_area_um2`는 2D shape, finite, [0,1]을 검증한 뒤 float64 `sum(values) * pixel_size_x_um * pixel_size_y_um`을 반환한다. 현재 전체 rectangular grid가 적분 도메인이며 별도 valid-domain mask 인자는 없다. GridSpec origin은 upper_left다. |
| `../particle-sim/src/particle_sim/pipeline.py:382–412`; `io/outputs.py:93–114`, `:130–146` | **동일한** `pair_result.failure`를 `nominal_failure` plane에 쓰고 CA에 적분한다. 따라서 nominal 원본 plane은 단순 display/footprint 추정물이 아니라 현 구현의 CA 생성 map이다. `legacy_failure`, `pair_margin_um`, `union_failure`, `union_pair_count`도 있지만 CA source가 서로 다르다. |
| `../particle-sim/src/particle_sim/pipeline.py:680–710`, `:741–758`; `aggregation/probability.py:36–54`, `:73–85`; `io/outputs.py:191–208`, `:223–239` | scenario failure를 누적한 float32 `probability`를 저장하고 그 **같은 map 적분**을 `ca_mean_um2`에 기록한다. raw probability map도 mean 전용 정량 원천이다. scenario CA 분위수는 별도 scalar이며 probability map으로 국부 분위수를 만들 수 없다. `union_probability`는 별도 생산자 union map이다. |
| `../particle-sim/src/particle_sim/io/outputs.py:20–38`; `pipeline.py:432–448` | `ca.parquet`은 unit별 nominal/mean/allocated/unique/분위수, `ca_union.parquet`은 union별 CA와 raw unit sum을 별도로 내보낸다. unit별 CA를 더해 union CA로 대체할 수 없다. |
| `../particle-sim/src/particle_sim/export/simcompare.py:55–63`, `:124–139`, `:172–176`, `:368–404`, `:422–459`, `:488–492` | compare exporter는 lossless 0/255 PNG 또는 float32 NPY, bundles/layouts/hotspots/ca_records/layer_stack CSV, geometry/provenance/export_warnings JSON을 쓴다. anisotropy를 거부하고 layout/hotspot origin을 0으로 기록한다. hotspot은 **항상 nominal_failure**다. provenance에 nominal semantics, ca_mode와 ca_source가 있다. |
| 같은 exporter `:109–111`, `:155–176`; `../particle-sim/src/particle_sim/pipeline.py:608–619` | exporter에는 mean 값 선택 분기가 있지만 `hotspots.zarr/nominal_failure`를 요구한다. native probability run은 `probability.zarr`를 출력하므로 현재 그대로는 exporter 경로가 맞지 않는다. 억지로 nominal store를 함께 놓아 export되어도 mean CA+nominal map은 계산 불가다. upstream exporter 수정은 이번 범위 밖이다. |
| `../particle-sim/src/particle_sim/failure/lateral_short.py:81–92` | short failure는 이미 계산된 contact_a & contact_b다. 사용자 확정대로 hotspot을 입자 중심 점으로 재해석하거나 추가 반경 dilation하지 않는다. producer의 CA 생성 정의와 사용자의 footprint 설명을 동치로 일반화할 필요가 없다. 여기서는 검증한 원본 계산 map만 적분한다. |

## 2. 정량 ROI CA 원천에 대한 결정

**결정: 전체-domain 보존 검사를 통과한 typed nominal binary map 및 typed scenario mean density map을 측정한다. 임의 hotspot 이미지는 측정하지 않는다.** 현 생산자에는 실제 사용 가능한 정량 map이 있으므로 모든 입력을 무조건 unavailable로 만드는 계획도 부정확하다.

| 입력 경로 | v1 처리 |
| --- | --- |
| 현 exporter의 nominal export | `calc_version` particle-sim 계열, `ca_source=ca_nominal_um2`, `ca_mode=nominal`, 알려진 nominal semantics, 원본 binary 인코딩/shape/pitch/origin을 모두 확인한 뒤 `particle_sim_nominal` typed source 후보로 만든다. PNG의 정확한 0/255→0/1 복호화는 가능하나 밝기 정규화/threshold/resize는 불가. 일반 processed_intensity를 숫자가 맞는다는 이유만으로 승격하지 않는다. |
| direct run nominal + nominal CA | 정확한 step/unit/diameter plane, nominal source metadata와 run grid를 연결한다. 원본 scalar CA와 보존 통과 시 사용한다. 기존 hotspot/image 동작과 checker 오류를 바꾸지 않고 optional typed source를 붙인다. |
| direct probability run + mean CA | `probability`와 `ca_mean_um2`를 동일 run/step/unit/size로 묶어 `particle_sim_mean` typed source로 사용 가능하다. report 입력은 아래 명시적 원천 manifest로 원본 probability NPY를 연결할 수도 있다. raw-run checker를 analyze 로더로 오인하거나 신규 raw-run CLI 전체를 만들지 않는다. |
| probability run + nominal CA, nominal export + mean CA | `source_mode_mismatch`. 합계가 우연히 같아도 금지한다. 일치하는 원본 nominal/mean map을 다시 제공해야 한다. |
| legacy dimensionless CA, 범례만 있는 일반 probability 이미지, footprint/display-only mask, 원천·grid·valid domain 불명 | `legacy_ca_not_area`, `unverified_source_semantics`, `missing_grid` 등 명시적 unavailable. 원래 global CA와 위치 근거는 보존한다. |
| allocated/unique/percentile scalar | v1 quantitative source 목록에 포함하지 않는다. 일치하는 공간 기여 map/계약이 없으므로 원 nominal map을 재사용하지 않는다. |

`source_kind`는 `particle_sim_nominal`, `particle_sim_mean`, `explicit_ca_density`만 지원한다. 마지막 것은 fixture와 외부 명시 입력용이며 density가 해당 계산의 CA 생성 map이라는 producer 선언, 계산 계약 ID, 원본 파일, source mode, 동일-domain total CA의 근거가 필수다. 선언 없는 이미지나 global CA 분할값은 거부한다. 향후 per-cell contribution 타입은 별도 정의가 필요하며 지금 추가하지 않는다.

보존 gate는 sample/source/mode/diameter마다 실행한다. canonical um 기준 `integral_full_um2 = sum(density[valid]) * dx_um * dy_um`, `abs(integral_full_um2 - source_total_um2) <= conservation_atol_um2 + conservation_rtol * abs(source_total_um2)`가 통과해야 ROI 값을 공개한다. binary는 정확한 {0,1}, density는 finite [0,1], valid는 bool 동일 shape, grid는 유한한 양수 pitch/명시 origin이어야 한다. 외부 valid mask가 있으면 total 자체도 **그 valid domain의 total**이어야 한다. producer map에는 full rectangular validity를 명시해서 붙이며 전극 occupancy를 valid mask로 쓰지 않는다. NaN이 있는 셀을 임의로 invalid 처리해 보존을 맞추지 않는다.

각 gate 결과에 source_total, integrated_total, residual, tolerances, source path/hash, source kind/mode, run/step/unit/diameter, domain/grid, producer/version/assumptions를 저장한다. gate 실패는 해당 ROI 수치만 null+reason으로 내보내며 기존 분석을 막지 않는다. 다만 새 사용자가 지정한 manifest의 중복 ID, 잘못된 경로, 잘못된 타입 등 형식 오류는 조용히 무시하지 않고 `DataContractError`로 알린다.

ROI는 원본 셀의 실제 면적과 물리 창의 교집합을 적분한다. density는 셀 안에서 일정한 이산장이라는 계약을 명시한다. 이는 원 생성 map 적분이며 global CA를 픽셀별로 배분하는 연산이 아니다. `roi_ca_um2`, `total_ca_um2`, `contribution_share=roi_ca/total_ca`, 선택적 `roi_ca_per_valid_area=roi_ca/valid_roi_area`를 별도 필드로 둔다. total=0일 때 share는 null+`zero_total`, valid area=0일 때 정규화도 null이다. 보존 실패를 숨기는 rescale/값 clip은 없다.

upstream 의존성은 원본 정량 map이 없는 자료의 producer export, native mean exporter 연결, 원래 좌표계 또는 명시적 등록, 실사용 대응표다. 폐쇄망 Fab 가중 자료는 이 기능의 진행 조건이 아니다.

## 3. 설계

### 3.1 책임 경계

작은 모듈로 분리하고 공개 함수에 타입과 한국어 docstring을 둔다. docstring은 입력 배열 shape, 단위, 좌표 원점, source semantics, 결측/오류, 반환 의미, IO 여부를 설명한다. 특히 추가 dilation 금지, 보존 gate, 동일-domain 비교, 미관측과 0의 차이는 이유를 주석으로 남긴다.

| 모듈 | 책임 / 금지 |
| --- | --- |
| 신규 `regional_schema.py` | `SpatialCASource`, `SpatialFrame`, `PhysicalWindow`, `SourceValidation`, `RegionMeasurement`, `RegionalComparison` 등의 typed 계약과 status/reason. 순환 import 방지를 위해 `schema.py`를 import하지 않는다. `schema.AnalysisBundle` 끝에 optional `ca_spatial_sources` 기본 빈 목록을 추가한다. 기존 positional/keyword 생성 호환 유지. |
| 신규 `regional_measurement.py` | 단위 변환, source 검증, full-domain 보존, rectangle/union 적분. IO/plot/AE 의존성 없는 순수 계산. 입력 배열과 bundle을 변경하지 않는다. |
| 신규 `adapters/regional.py` | typed source의 producer별 승격, 명시적 JSON/NPY/valid-mask 읽기, 경로·해시·provenance 검증. 기존 example/run adapter에는 작은 factory 연결만 넣는다. 일반 이미지에 대한 기존 read_image semantics를 변경하지 않는다. |
| 신규 `correspondence.py` | 명시 대응표 파싱/검증과 query-reference의 identity/process/registration 자격 판정. IO 파싱과 순수 resolve helper를 구분한다. 기존 `retrieval.candidate_mask`의 AE/search 정책은 그대로 유지한다. |
| 신규 `regions.py` | 원본 hotspot support를 물리 좌표로 바꿔 component/window 생성, 모든 크기·양쪽 support 포함, 중첩 창 merge, 결정적 ID. CA 계산과 report IO를 넣지 않는다. |
| 신규 `regional_comparison.py` | 크기별 own/global/local 값, source/identity/domain 비교 gate, delta/share/선정 사유. max-only neighborhood 결과에 의존하지 않는다. |
| 신규 `regional_plots.py`, `regional_report.py` | 앞 모듈의 결과만 받아 overview/crop/곡선 정적 SVG/PNG 및 HTML/JSON/CSV 출력. 계산을 renderer에서 재수행하지 않는다. `plots.py`의 톤·단위·escape 관례를 따른다. |
| 신규 `regional.py` | `analyze_regions(bundles, cfg)`와 `write_regional_analysis(...)` 수준의 조합. source/mapping IO → 순수 계산 → rendering. `pipeline.run_analyze`는 이를 호출하고 outputs/run_record만 추가한다. |

기존 `neighborhood.py`, `variants.py`, `pair_geometry.py`, `retrieval.py`, `cards.py`, model/dataset/caa에는 기본적으로 변경이 필요 없다. 새 report에서 기존 카드/전체 geometry를 링크할 수는 있지만 이를 국부 측정이나 선정 근거라고 표시하지 않는다.

### 3.2 입력 계약과 좌표 등록

`analysis.regional.correspondence_file`은 versioned JSON이다. 초기 형식은 의도적으로 명시적 pair 단위로 제한한다. `schema_version: "1"`, `pairs: [...]`; 한 entry에는 `comparison_id`, `query_sample_id`, nullable `reference_sample_id`, `relationship` (`confirmed_same_structure`, `confirmed_added`, `confirmed_removed`, `unknown`), `electrodes` (각 slot의 query/reference role+layer와 canonical electrode/layer ID), `process` (양측 assigned_process_layer와 canonical process ID), `evidence` (사용자 확인 출처)가 있다. 원래 bundle 값과 선언 값이 다르면 거부한다. open은 slot a만, short는 a/b를 모두 대응시킨다. failure type은 반드시 동일하다. 한 entry 안에서 중복 slot/충돌/다대일 모호성은 거부한다.

등록은 같은 entry의 선택적 `registration`에 `frame_id`, `length_unit`, `query_translation_xy`, `reference_translation_xy`, `evidence`를 둔다. `common_xy = local_xy + translation_xy`다. 일치하는 dimensions만으로 등록을 추론하지 않는다. 같은 sample의 자체 측정은 원래 local frame에서 가능하고, 서로 다른 sample의 identity 확인에는 이 대응표가 필요하다. 기존 데이터에 검증된 stable identity가 명시적으로 없는 한 제품/역할/전극 이름 일치, parent_design_id, AE 이웃만으로 identity를 만들어내지 않는다. 대응표가 없으면 각 sample의 global/own ROI를 제공하되 cross-sample delta는 unknown이다. 전체 CA scalar 비교에는 identity/계산 정의/domain 호환이 필요하지만 공간 등록은 필요하지 않다. 위치별 비교에는 공간 등록까지 필요하다.

`confirmed_added/removed`는 명시 구조 변화다. 없는 쪽 CA를 0으로 채우지 않고 비교 delta는 null, 존재하는 쪽 own risk를 유지한다. `unknown`이나 reference 행 누락은 구조 부재 증거가 아니다. 위험 출현/소멸은 **동일 구조 + 관측된 양쪽 source/size + 동일 ROI/domain**에서 한쪽 ROI 값이 실제 0인 경우만 판정한다.

원천 manifest `analysis.regional.source_manifest`도 JSON `schema_version: "1"`, `sources: [...]`이다. 각 행은 sample_id, diameter+unit, kind/mode/semantics, scalar CA source field, calculation_contract_id, density NPY 상대경로, 선택 valid-mask NPY 상대경로, origin_xy, scalar pixel_size+length_unit, frame_id, producer/version/source_files/evidence를 갖는다. total은 bundle의 동일 크기·source scalar에 연결하며 다른 total을 조용히 주입하지 않는다. NPY는 allow_pickle=False, 2D, canonical raw 값만 허용한다. manifest 기준 상대 파일만 읽고 절대/상위 이탈 경로와 중복 source key를 거부한다. embedded auto source와 충돌하면 오류이며 silently override하지 않는다. source manifest로 native mean NPY를 받는 경로는 실제 producer source라는 증빙과 보존 gate를 모두 요구한다.

좌표 계약은 `(x,y)`가 원본 셀 [0,0] **좌상단 모서리**, 배열은 `[y,x]`, x 오른쪽/y 아래쪽, bounds는 half-open `[x0,x1) × [y0,y1)`다. 길이/면적은 계산 전에 um/um²로 변환한다. `origin_xy=None`이면 0으로 채우지 않는다. direct producer의 0은 그 export의 **local frame**일 뿐 전 제품 공통 원점이 아니다. 별도로 알려진 nonzero ImageInput origin을 보존한다.

v1은 isotropic axis-aligned grid와 translation 등록만 지원한다. anisotropic tuple, 회전, scale/shear 등록은 명시적 `unsupported_grid`/`unsupported_registration`으로 거부한다. 서로 다른 scalar pitch 또는 common-frame 원점의 subpixel 어긋남은 자체 ROI 측정은 가능하지만 v1 local delta는 `incompatible_comparison_grid`로 둔다. 같은 pitch 및 정수 cell offset인 공통 lattice에 대해서만 source support/valid domain을 손실 없이 대조한다. 이 제한은 등록 정밀도 보장을 위한 것으로, display resize는 정량 정합에 쓰지 않는다.

### 3.3 hotspot을 포함하는 고정 공통 창과 선택

1. 각 sample의 **원 해상도** hotspot에서 finite 양수 support 전체를 읽는다. support 정의는 0 초과이며 밝기 컷으로 일부 성분을 제외하지 않는다. 관측된 binary all-zero, 실제 배열 누락, invalid intensity를 구분한다. particle radius 재팽창, largest-size-only, brightest-component-only는 금지한다. 신뢰할 source map support만 있는 경우 이를 명시적 위치 근거로 사용할 수 있지만 arbitrary global CA에서 위치를 만들지 않는다. 양쪽 모든 support가 실제로 비면 region 목록은 비어 있고 sample summary에 `observed_empty`를 쓴다. 자료가 없으면 `missing`이며, total>0인데 위치 근거가 없으면 global 값과 `location_unavailable`를 남긴다. 빈 region 목록을 sample 위험 0으로 바꾸지 않는다.
2. 확인된 등록 pair에서는 양쪽 모든 관측/요청 diameter의 support 합집합을 common lattice에서 만든다. 크기가 누락된 쪽도 union 크기 목록과 결측 행을 보존한다. 연결성분(기본 8-neighbor)별 물리 bbox를 만든다. 위치 이동이 떨어진 두 component를 만들면 두 지역을 유지하므로 old/new 위치 모두 보인다. 이것을 자동 trajectory matching으로 주장하지 않는다.
3. 기본 grouping gap 0, 선택적 `merge_gap_um` 이내 성분을 결정적 순서로 연결한다. 그룹 bbox들이 겹치면 합쳐 서로 겹치지 않는 rectangle이 될 때까지 반복한다. 전이 closure 순서는 canonical bbox 정렬로 고정한다. 각 최종 창은 모든 비교 크기와 양쪽에 동일하게 적용한다. 크기마다 다시 창을 만들지 않는다.
4. `display_context_margin_um`은 그림용 crop bounds에만 적용한다. quantitative window와 region ID, 값, grouping에는 영향을 주지 않는다. 영역 선택 때문에 물리 모델을 dilation하는 옵션은 없다.
5. 요청 window는 양쪽 hotspots 전체를 포함한 채 유지한다. 각 측정은 자기 valid domain으로 clip하고 requested bounds, clipped coverage, valid ROI area를 별도 기록한다. 한쪽 도메인 밖이나 hole이 있으면 이를 빈 위험 0으로 보이지 않게 hatch/unknown으로 표시한다. 해당 ROI의 양쪽 유효 coverage가 동일하지 않으면 local delta는 `incomparable_domain`; 핫스폿을 common intersection 밖으로 버려 비교를 강제로 성립시키지 않는다. 양쪽 모두 같은 경계에서 잘린 경우는 실제 공통 coverage를 명시하고 비교 가능하다.
6. unmatched/unregistered sample은 자기 local 창으로 측정/그림을 만든다. 각 패널에 좌표계 미등록을 표시하고 공통 창/차영상처럼 보이지 않게 분리한다. 등록 pair에서도 v1은 pixel subtraction 그림을 만들 필요가 없다.
7. region_id는 algorithm version, 정렬된 member sample IDs, confirmed pair/canonical context, frame ID, canonical quantitative bounds를 canonical JSON으로 직렬화하여 SHA-256으로 만든다. full digest를 machine ID로 쓰고 짧은 표시 ID 충돌 시 늘린다. input list/component 탐색 순서·query/reference 방향·display margin에 의존하지 않는다. 값 변경으로 bounds가 변하면 ID가 달라질 수 있으며, 시계열 영구 추적 ID라는 보장은 하지 않는다.

ROI 수를 위험순 상위 k로 잘라 버리지 않는다. 모든 지역·모든 크기의 evidence row를 보존한다. report는 **크기별** total CA level/increase와 ROI CA level/increase의 서로 다른 정렬·flag를 제공한다. 선택 reason에는 `hotspot_support`, `total_ca_increased@d`, `local_ca_increased@d`, `new_risk@d`, `risk_disappeared@d`, `source_unavailable` 등을 기록한다. full curves와 지름별 값을 본문에 두므로 가장 큰 크기나 한 최대값에 가려진 변화가 없다. 총량 100→80, ROI 20→30이면 `delta_total=-20`, `delta_roi=+10`과 local worsening을 함께 표시한다. overlap rate, heat peak, 반복 형상, embedding 거리는 위험 정렬 점수가 아니다. 고 total이지만 위치 자료가 없는 sample도 global 목록에 남는다.

### 3.4 집계, 설정, 출력

중첩 ROI의 aggregate는 동일 source/map/mode/diameter/domain에서 **window union을 한 번 적분**한 `selected_union_ca_um2`만 허용한다. 서로 다른 전극쌍·step·불량·source mode를 더해 product/layer total로 만들지 않는다. v1은 product/layer total을 새로 생성하지 않는다. source별 full-domain scalar를 `unit_total_ca_um2`로 명시한다. 추후 producer union source를 연결할 때도 union scope와 total provenance를 별도 계약으로 추가해야 한다. partition conservation은 같은 source valid domain을 실제 분할한 fixture에서만 주장한다. share/정규화 비율 합산은 제공하지 않는다.

`analysis.regional` 중첩 dataclass 설정(unknown key/null/type/range 엄격 검증):

| 키 | 기본값 | 의미 |
| --- | --- | --- |
| `enabled` | true | 기존 analyze에 새 부가 report 생성. false면 과거 outputs 동작. |
| `correspondence_file`, `source_manifest` | null | 선택 외부 JSON, 설정 YAML 기준 경로. 지정 파일 누락은 오류. 기본은 mapping 미확인/자동 검증 가능한 source만 사용. |
| `connectivity` | 8 | component 연결 4 또는 8. |
| `merge_gap_um` | 0.0 | 성분 bbox grouping 물리 간격, 음수 금지. quantitative dilation과 다름. |
| `display_context_margin_um` | 0.0 | 그림에만 추가하는 문맥 여백. |
| `conservation_rtol` | 1e-6 | 원본 float32 density·float64 적분 차이에 대한 상대 허용값. |
| `conservation_atol_um2` | 1e-9 | 0 근처 절대 면적 허용값. 값 rescale 용도 아님. |
| `coordinate_atol_um` | 1e-9 | common lattice 원점/경계 일치 판정 허용값. 큰 좌표에 비례해 증가시키지 않음. |
| `delta_atol_um2` | 1e-9 | 증가/감소/동일 label의 절대 허용값. 0 baseline 상대율을 정의하는 epsilon으로 사용하지 않음. |
| `include_area_normalized` | false | raw contribution과 별도로 valid ROI area당 값을 출력할지 여부. |

source/dimension 불일치 허용을 위한 임의 완화 키는 만들지 않는다. diameter는 단위 변환 후 exact match, 보간/외삽 없음. source-mode와 계산 정의가 서로 다르면 delta=null이며 self 값은 남는다. 계산 정의 비교는 run_id/config_hash 동일성으로 대체하지 않는다(설계 변경은 config_hash를 바꾼다). source kind/mode, failure 정의, producer calculation version, assumption_set, legacy_kernel, post_step_policy 등 결과 의미를 바꾸는 settings를 비교하고 누락은 미확인 처리한다. 원본 run/config hash는 provenance로 기록한다.

출력은 `cfg.output_dir` 아래 기존 파일을 유지하면서 다음을 추가한다:

```text
regional_report.html
regional_summary.json
regional_measurements.csv
regional_comparisons.csv
figures/regional/<safe-id>-overview.svg
figures/regional/<region-id>-crops.svg
figures/regional/<region-id>-curves.svg
```

HTML은 inline CSS/SVG 또는 embedded PNG로 **단독 파일을 옮겨도** 본문 그림을 볼 수 있게 하고, 같은 그림을 local static 파일로도 저장한다. JS 없이 anchor/details/table 중심으로 구현해 file://에서 작동하게 한다. 외부 CDN/폰트/이미지/네트워크 요청은 없다. JSON/CSV 다운로드 링크는 sibling 파일을 가리킨다. 제목/이름/사유 escape, hash 기반 안전한 파일명, JSON finite 숫자만 출력(`allow_nan=False`, missing은 null; CSV 공란+status/reason)이 필수다.

report는 전체 layout/hotspot overview와 영역 label → 위치별 동일 축척 crop 양쪽 비교 → 지름별 global/ROI CA 곡선·표 → raw/delta/share/선택 이유 → provenance/불가 사유 순서다. per-side layer/electrode/process/source mode/frame/물리 범위/valid 면적을 표시한다. common scale/coordinate 축과 crop context/measurement 경계는 시각적으로 구분한다. 누락 size는 curve를 연결해 보간하지 않는다. layout이 없으면 hotspot-only와 layout missing을 표시하고 없는 layout을 합성하지 않는다. synthetic/demo와 실제 데이터 상태 및 인과/수율 미주장은 유지한다. 단순 카드 나열로 대체하지 않는다.

run_record에는 regional algorithm/config/schema version, 입력 manifest와 source hashes, 보존 통과/실패 개수, unavailable 이유 집계, 출력 경로를 추가한다. 보고 입력은 기본적으로 data_dir 밖에 두도록 문서화하고 별도 fingerprint를 저장한다. data_dir 안에 넣은 입력은 기존 학습 fingerprint 정책이 그대로 적용됨을 명시한다.

확장 경계: correspondence resolver가 향후 실제 대응표의 공급 인터페이스, measurement output이 미래 Fab weighting의 입력 경계다. 이번에는 Fab weighted formula/CAA/yield 연산을 추가하지 않는다. Tree/SHAP은 문서로만 후속 경계를 정의한다: geometry/context features를 취약·비취약 지역 모두에서 별도로 측정하고 hotspot은 labels로만 사용; CA/hotspot/full AE z를 feature로 넣지 않으며 pair-global 값을 local로 복사하지 않는다. placeholder model/class/training code는 만들지 않는다.

## 4. 순차 구현 Task 계약

모든 구현은 `gpt-6.1-sol / medium` **한 editor씩** 수행한다. 중첩 worker는 금지한다. Opus가 high에서 이 계획을 검토·채택한 뒤 실제 Task ID를 생성한다. 아래 T1→T2→T3는 데이터 계약과 결과 의존성 때문이며 단순 편의를 위한 병렬 의존성은 없다. T2/T3는 coordinator가 하나의 bounded Task로 합칠 수 있지만 T1 acceptance 경계는 보존하는 편이 안전하다.

공통 Baseline B0: 위 branch/HEAD, 위 3개 기존 dirty 파일 및 baseline-preexisting.diff. 첫 worker는 정확한 diff hash와 Git 상태를 확인한다. 후속 worker는 B0 + coordinator가 채택한 앞 Task의 **실제 candidate diff/hash, 검증 evidence**를 받는다. 앞 Task 변경을 이미 완료된 작업으로 인식하고 되돌리거나 재작성하지 않는다. 구현 기간 전체 diff가 B0와 같아야 한다는 뜻은 아니며 pre-existing hunks가 보존되어야 한다는 뜻이다. coordinator가 commit하지 않았다면 임의 commit을 만들지 말고 diff fingerprint를 기준으로 삼는다.

### T1 — 정량 원천 계약·보존 gate·순수 ROI 적분

- **Target:** `src/simcompare/regional_schema.py`, `regional_measurement.py`, `adapters/regional.py`, `schema.py`, `config.py`, `adapters/example.py`, `adapters/particle_sim_run.py`; 신규 `tests/test_regional_measurement.py`, `tests/test_regional_sources.py`, 기존 `tests/test_config_contract.py`, `tests/test_particle_sim_export_integration.py`의 관련 부분.
- **Change:** optional source 계약, nominal exporter와 direct nominal/mean의 typed source 연결, external source manifest, 보존 gate/원천 상태, 원본 bounds·valid mask·unit 기반 ROI/union 측정, regional 설정 parsing. 기존 데이터는 source 없음으로 정상 로딩된다.
- **Constraints:** 2절 source matrix 준수. generic intensity/probability 이미지 승격 금지. 새 코드는 pure/nonmutating. 기존 run checker `--ca`와 hotspot display 선택을 몰래 바꾸지 않고 mismatch를 별도 지역 상태로 반환. sibling producer와 기존 Fab 처리 수정 금지. source unavailable가 기존 load를 실패시키지 않되 기존 validation 오류를 약화하지 않는다.
- **Ownership:** 위 파일만 수정, 새 tests fixture가 필요하면 `tests/fixtures/regional/`에 작고 명시적인 fixture 허용. B0의 기존 3파일 변경은 그대로 보존; docs/open_questions.md는 손대지 않는다. coordinator checkpoint/plan/lifecycle 소유권 없음.
- **Acceptance:** 5절 M/S 테스트 전부, 설정 defaults/unknown/null/path 검증. `.venv/bin/python -m pytest tests/test_regional_measurement.py tests/test_regional_sources.py tests/test_config_contract.py tests/test_ca_unit_contract.py tests/test_adapter_example_contract.py tests/test_particle_sim_run.py tests/test_particle_sim_export_integration.py`. pyarrow 등 없어 skip하면 그것을 PASS로 주장하지 말고 비선택 의존성 없는 source fixture 검증과 구분해 보고한다. nominal 원본 sum이 total과 같은 fixture와 deliberately mismatched sum 양쪽을 반드시 실행한다.
- **Baseline:** B0. baseline adapter/integration diff를 읽고 필요한 추가 hunk만 쌓는다. 재시도는 기존 typed source 구현/테스트와 partial changes를 먼저 확인한다.

### T2 — 명시 대응표·고정 공통 창·크기별 비교

- **Dependency:** T1의 public 계약과 단위/적분 gate가 채택되어야 한다.
- **Target:** 신규 `src/simcompare/correspondence.py`, `regions.py`, `regional_comparison.py`; 신규 `tests/test_regional_correspondence.py`, `test_regions.py`, `test_regional_comparison.py`; 필요 시 T1 regional 계약의 작은 확장과 그 테스트.
- **Change:** JSON 대응표 resolver, identity/process/source/registration gating, 전체 hotspot support 기반 창, deterministic ID, 모든 지름의 own/global/local 비교와 선택 이유, union 집계 제한. 없는 대응표로도 own-region 목록이 나온다.
- **Constraints:** AE/search 순위나 legacy `_roi` 재사용 금지. reference/query 방향은 delta=query-reference로 명시. 모드/단위/registration/domain 불일치나 missing→0 금지. no double dilation, no product/pair-sum total, no normalized-ratio sum. outside-domain hotspot을 삭제해서 공통 창을 만들지 않는다. 전역 geometry를 local feature로 만들지 않는다.
- **Ownership:** Target 파일과 소형 regional fixture만. retrieval/neighborhood/variants/pair_geometry/model/caa 및 sibling는 read-only. T1 계약 결함 때문에 설계 변경이 필요하면 coordinator에게 반환하고 Astra 재계획/Opus 채택을 거친다.
- **Acceptance:** 5절 R/C 테스트 전부. `.venv/bin/python -m pytest tests/test_regional_correspondence.py tests/test_regions.py tests/test_regional_comparison.py tests/test_regional_measurement.py tests/test_pair_neighborhood.py tests/test_retrieval_contract.py tests/test_variants_merge.py`. 총량 감소/국부 증가, 여러 크기 crossing, 이동·신규·소멸의 concrete 숫자 fixture를 evidence로 남긴다.
- **Baseline:** B0 + 채택된 T1 candidate 및 테스트 결과. 시작 시 원천 API와 diff fingerprint를 coordinator receipt와 대조한다.

### T3 — 위치 중심 HTML/구조화 출력과 analyze 연결

- **Dependency:** T1 typed sources/측정과 T2 비교 결과가 채택되어야 한다.
- **Target:** 신규 `src/simcompare/regional.py`, `regional_plots.py`, `regional_report.py`; `pipeline.py`의 얇은 호출/output/run_record; 신규 `tests/test_regional_report.py`, `tests/test_regional_pipeline.py`; `tests/test_cli_e2e.py`의 추가 출력 검증; 신규 `docs/regional_analysis.md`, `README.md`의 사용 예/제약/산출물 안내. 예시 설정은 문서에 담아 전역 config 변경을 최소화한다.
- **Change:** 3.4의 offline standalone HTML, JSON/CSV/static figures, 원본 provenance와 unavailable 사유, 정상 nominal fixture의 실제 ROI 값과 legacy source unavailable에서 기존 분석 완료를 보여준다. source/mapping 경로 없이 기본 analyze가 정상 동작한다.
- **Constraints:** pipeline에 계산/HTML 문자열을 쌓지 않는다. 기존 output/AE/search/카드/edit candidate 결과와 checkpoint 검증을 유지한다. 외부 asset/새 dependency/Tree/Fab 계산/자동 edit/재시뮬레이션 없음. 기존 real Fab/입력 검증을 우회하려고 값을 만들어 넣지 않는다. synthetic fixture는 synthetic임을 표시한다.
- **Ownership:** Target과 regional fixture만. T1/T2의 국소 결함 수정은 coordinator가 같은 Task의 bounded scope로 승인한 범위에 한정한다. 기존 docs/open_questions.md와 sibling는 수정하지 않는다. plan/checkpoint/review는 coordinator 소유다.
- **Acceptance:** 5절 E 테스트 전부. `.venv/bin/python -m pytest tests/test_regional_report.py tests/test_regional_pipeline.py tests/test_cli_e2e.py tests/test_plots.py tests/test_analysis_cards.py tests/test_variants_merge.py` 후 `.venv/bin/python -m pytest` 한 번. 가능하면 이미 설치된 producer 환경과 `PARTICLE_SIM_ROOT`/`PARTICLE_SIM_PYTHON`로 기존 exporter integration gate를 실행하되 설치하지 않는다. native mean exporter의 현재 한계를 test skip이나 synthetic 성공으로 덮지 않는다. 대표 report(정량 nominal, 총량감소/국부증가, source unavailable, unregistered)를 실제 브라우저에서 확인하고 overview label/crop 축/누락 표시/곡선/로컬 asset/긴 한국어와 ID가 읽히는지 확인한다. 자동 HTML 검사만으로 visual PASS라고 하지 않는다. 결과 경로·스크린샷 가능 여부·수동 점검 기록을 coordinator에게 전달한다.
- **Baseline:** B0 + 채택된 T1/T2 candidate/hash/evidence. report-only 외부 입력 변경 때문에 AE 재학습이 요구되지 않는 테스트와 기존 model 입력 변경은 여전히 거부되는 테스트를 모두 유지한다.

구현 후 coordinator gate(새 editor Task 아님): exact candidate diff와 계약, 테스트/skip/시각 점검 증거를 별도 fresh `claude-opus-5-5 / high` read-only reviewer에게 준다. schema/core area/registration 변경이므로 독립 리뷰 필수다. planner와 reviewer는 같은 하나의 read-only slot을 순차 사용한다. coordinator 자체 PASS로 대체하지 않는다. 수정 후 관련 검증과 영향을 받는 독립 리뷰를 갱신하고 coordinator가 실제 최종 candidate를 검증한다.

## 5. 의미 있는 acceptance counterexample → 구체적 테스트

아래 이름은 추가할 테스트 이름이며 기존 통과 사실을 뜻하지 않는다. M은 `test_regional_measurement.py`, S는 `test_regional_sources.py`, R은 `test_regions.py`, C는 `test_regional_comparison.py`(mapping 행만 `test_regional_correspondence.py`), E는 `test_regional_pipeline.py`/`test_regional_report.py`에 둔다.

| ID | 요청 invariant / 반례 | 테스트 이름과 기대 결과 |
| --- | --- | --- |
| M1 | 전체 valid domain 합=원 CA | `test_full_valid_domain_matches_source_ca`: nontrivial binary/mean 두 타입의 total 및 residual 검증. `test_conservation_failure_is_unavailable_without_rescaling`: 틀린 total에서 ROI=null. |
| M2 | 실제 partition 보존 | `test_disjoint_partition_conserves_same_source`: nonzero origin, valid holes 포함한 완전 분할 합이 동일-map total과 같음. 서로 다른 source를 partition이라 주장하지 않음. |
| M3 | ROI 중첩 이중 합산 금지 | `test_overlapping_windows_integrate_union_once`: A+B 합보다 union이 작고 교집합 한 번만 포함. `test_comparison_never_sums_pair_ca_into_product_total`: 같은 영역의 두 unit 값을 product total로 출력하지 않음. |
| M4 | pixel/unit/origin/물리 clip | `test_nm_mm_area_and_nonzero_origin_conversion`, `test_fractional_boundary_crop_and_valid_mask`: 손계산한 cell 교집합 면적과 일치. `test_outside_domain_is_not_risk_zero`: empty coverage는 0 위험이 아니라 unavailable. |
| M5 | anisotropy/불명 원점 | `test_anisotropic_grid_is_explicitly_unsupported`, `test_missing_origin_does_not_default_to_zero`; 음수/무한 pitch와 invalid-mask shape도 parameterize. |
| M6 | raw/share/정규화 단위 구분 | `test_raw_share_and_density_have_distinct_denominators`, `test_zero_total_share_and_empty_valid_area_are_null`, `test_normalized_ratios_are_not_aggregated`. legacy 숫자에 um² label을 붙이지 않음은 S3. |
| S1 | 실제 nominal/mean source 일치 | `test_particle_sim_nominal_export_preserves_raw_map_and_total`, `test_probability_map_matches_mean_ca_only`: exporter lossless 원본과 direct run mode 연결. existing exporter integration에 전체 sum assertion 추가. |
| S2 | 원천 불일치 우연 합계 일치 | `test_equal_sums_do_not_override_source_mode_mismatch`: nominal mask와 mean total이 같아도 불가. `test_untyped_footprint_and_legend_images_are_not_ca_sources`: [0,1]/probability 범례/union footprint라는 사실만으로 승격 불가. |
| S3 | 결측/legacy/source provenance | `test_legacy_dimensionless_ca_is_not_square_micrometers`, `test_missing_source_or_size_preserves_reason_and_global_ca`, `test_source_contract_settings_and_identity_are_preserved`: 원 source/step/unit/size/version 연결 확인. |
| S4 | manifest 실제 연결/오류 | `test_explicit_density_manifest_loads_verified_source`, `test_source_manifest_rejects_duplicate_and_outside_paths`, `test_invalid_source_values_are_not_silently_clipped`: 무한/NaN/범위/경로/중복 반례. |
| R1 | 입자 footprint 이중 dilation 금지 | `test_support_bounds_do_not_expand_by_particle_radius`: 같은 support, 큰 diameter여도 그대로. `test_display_margin_changes_only_plot_bounds`: 정량 bounds/값/ID 불변. |
| R2 | 이동/신규/소멸, peak-only 누락 금지 | `test_common_windows_include_both_sides_all_sizes_and_dim_components`: 작은 밝기의 멀리 떨어진 성분과 reference-only 성분 모두 포함. `test_moved_hotspot_keeps_old_and_new_locations`: 이동한 양쪽 지역 보존. |
| R3 | 크기별 고정 창 | `test_common_window_is_fixed_across_diameters`: 어떤 크기에서 hotspot가 사라져도 같은 창 사용; 누락 size와 빈 support는 구분. |
| R4 | 결정성/입력 불변 | `test_region_ids_are_stable_under_reordering_and_pair_reversal`, `test_regional_functions_do_not_mutate_inputs`: sample/image/size/component 순서 섞기, array와 dataclass deep snapshot 불변. |
| C1 | 전체 100→80, ROI 20→30 | `test_local_worsening_survives_total_improvement`: delta_total=-20, delta_roi=+10, local reason 유지. |
| C2 | size-specific CA, max 숨김 금지 | `test_crossing_diameter_curves_retain_each_change`: 최대값이 같아도 다른 크기 증가/감소를 모두 기록. `test_ca_levels_and_deltas_drive_order_not_overlap_or_peak`: geometry overlap/heat peak 순서를 반대로 해도 CA 기반 순서 유지. |
| C3 | missing size/map/reference != zero | `test_missing_reference_size_or_map_never_becomes_zero` parameterize. common size만 비교하되 한쪽 관측값/미관측 row 삭제 금지. |
| C4 | 관측 0·빈 hotspot·결측 구분 | `test_observed_zero_and_empty_hotspot_differ_from_missing`, `test_zero_reference_has_absolute_delta_without_infinity`: ref 0/query>0에 absolute delta와 new risk, relative delta=null. |
| C5 | 위험 소멸 vs 구조 추가/부재 | `test_confirmed_structure_addition_retains_own_risk_without_fake_baseline`, `test_risk_disappearance_requires_observed_zero_in_same_structure`: absent와 unknown과 observed-zero를 별도 상태로 출력. |
| C6 | 이름/embedding만으로 대응 금지 | `test_matching_names_do_not_confirm_correspondence`, `test_mapping_validates_electrode_layer_failure_and_process`, `test_explicit_mapping_enables_comparison_across_different_names`: complete pair/slot/process 매핑일 때만 자격 부여. |
| C7 | 등록됨 vs dimensions만 같음 | `test_equal_shapes_do_not_imply_registration`, `test_translation_with_nonzero_origin_aligns_common_window`, `test_subpixel_or_different_pitch_blocks_local_delta`: 독립 global 비교 가능성과 local unavailable를 분리. |
| C8 | valid 영역/clip 비교 | `test_different_valid_coverage_blocks_local_delta_without_dropping_hotspot`, `test_identical_boundary_clipping_remains_comparable`: unequal domain을 같다고 보이지 않으며 같은 clip이면 표시된 denominator로 비교. |
| C9 | nominal/mean·calculation 설정 비교 | `test_incompatible_source_semantics_blocks_delta_even_when_numbers_match`, `test_design_config_hash_change_alone_does_not_block_comparison`: source 정의 mismatch는 막고 설계 자체가 바뀐 config hash만으로 막지는 않음. |
| E1 | standalone 출력과 provenance | `test_regional_report_exports_html_json_csv_and_local_figures`: 모든 schema/status/unit/hash/region/bounds 연결, JSON NaN/Infinity 없음, local links 존재. `test_report_is_self_contained_and_escapes_input_labels`: 외부 asset 없음, markup 이름 escape, standalone inline 그림. |
| E2 | 기존 analysis 유지/원본 측정 | `test_analyze_with_unavailable_regional_sources_keeps_legacy_outputs`, `test_regional_results_do_not_depend_on_model_image_size`, `test_regional_disabled_preserves_previous_analysis`: resized/model/AE 값으로 ROI 계산하지 않음. |
| E3 | 낮아진 총량과 국부 증가의 report | `test_report_shows_total_improvement_and_local_worsening_together`, `test_report_breaks_curves_at_missing_sizes`, `test_unregistered_report_uses_separate_coordinate_panels`: 표/곡선/사유에 실제 숫자와 미관측 상태 확인. 별도의 실제 visual inspection도 필수. |
| E4 | AE/search/Fab/CNO/open 제외 유지 | `test_regional_analysis_does_not_change_search_or_legacy_edits`, `test_regional_report_adds_no_fab_yield_or_tree_scores`; 기존 `tests/test_domain_rules.py`, `test_variants_merge.py`, `test_particle_sim_run.py::test_open_cno_and_strict`도 회귀 gate. CNO 입력을 새 경로로 통과시키지 않음. |
| E5 | 학습 fingerprint와 report 경계 | `test_external_regional_inputs_do_not_change_training_fingerprint`: data_dir 밖 report-only mapping 변경은 재학습 없이 report fingerprint를 바꿈. 기존 `test_checkpoint_fingerprint_rejects_changed_input`/`...changed_training_contract` 유지. |
| E6 | 전역 geometry/AE 누출, 자동 편집 금지 | `test_regional_report_does_not_label_pair_global_geometry_as_local`, `test_regional_analysis_creates_no_layout_edits`: 신규 결과/파일에서 해당 효과가 없음. Tree training 누출 테스트 자체는 **not applicable**: Tree를 구현하지 않는다. 대신 문서의 feature/label 분리와 no-placeholder를 독립 리뷰에서 확인하며 AE/search 결과를 신규 지역 점수로 사용하지 않는 E4 gate를 둔다. |

기존 회귀를 포함한 전체 pytest와 exact-candidate 독립 Opus high review가 마지막 acceptance다. 실사용 mapping/폐쇄망 Fab 검증 및 실제 accuracy/yield 증명은 **not applicable**(현재 미제공 자료이며 요청에서 제외); 이를 synthetic 테스트 성공으로 주장하지 않는다. 브라우저 시각 검증과 독립 리뷰는 pytest 이름으로 대체할 수 없는 evidence gate다.

## 6. 위험, coordinator 판단 사항, 중단·재시도 복구

### 위험과 보수적 선택

1. **생산자 metadata와 실제 파일의 신뢰:** 보존 수치 일치만으로 의미가 검증되지는 않는다. producer source/mode/정의 + 원본 인코딩 + 보존을 함께 검사한다. 기존 export가 문서와 다른 별도 버전이면 unavailable로 남기며 수식을 맞춰주지 않는다.
2. **native probability export 단절:** raw probability map은 정량 원천이지만 현 exporter는 nominal store 경로다. 이번 구현은 direct adapter/explicit density source 연결을 제공하고 upstream exporter 변경은 하지 않는다. 이 제한을 최종 사용자 보고에도 적는다.
3. **기존 원점 0과 실사용 등록:** 현재 producer의 0은 local grid다. 실사용 대응표/등록 없이 cross-product ROI delta를 내놓지 않는 것이 기본이다. 내장 nominal fixture에서 local ROI 측정 자체는 가능하므로 기능 검증이 막히지는 않는다.
4. **component 증가와 report 크기:** 원본 support가 조각나면 ROI가 많아질 수 있다. 모든 row를 보존하고 접을 수 있는 report 섹션을 사용한다. 성능을 이유로 상위 peak만 남기지 않는다. 큰 데이터의 optimization은 실제 측정 후 필요하면 bounded Task로 추가한다.
5. **기존 real 데이터 조건:** analyze가 학습 checkpoint와 기존 Fab/data 계약을 요구하는 점은 유지한다. regional source unavailable와 기존 전체 입력 invalid를 혼동해서 검증을 끄지 않는다. real CA-only 독립 CLI를 새로 만드는 것은 본 계획 범위가 아니다.
6. **선택 단위와 집계:** source unit CA를 제품 전체 total처럼 표시하는 것이 가장 큰 오독 위험이다. report의 total은 unit/source/mode/domain별임을 제목과 machine schema에 반복해서 명시한다.
7. **review와 환경:** pyarrow 미설치와 producer 환경 미지정으로 56 skip이 있었다. core 지역 테스트는 NumPy/Pillow 등 이미 있는 의존성으로 실행 가능해야 한다. native producer integration은 설치된 환경이 있을 때 실행하며 skip 수/사유를 분리 보고한다. 독립 Opus 리뷰가 불가능하면 pending으로 남기고 다른 모델의 PASS로 대체하지 않는다.

coordinator에게 남기는 채택 판단(답을 기다릴 blocking question은 아님): (a) v1 translation + 같은 isotropic lattice 제한 및 cross-product 명시 mapping 필수 정책, (b) 기본 report enabled=true, (c) typed nominal 자동 승격과 explicit density manifest를 통한 mean source 연결, (d) 세 Task 분할을 검토한다. 모두 이 계획의 보수적 기본값으로 진행 가능하다. 실제 mapping/등록 정의나 producer 계약이 이 기본값과 충돌하면 원계약을 바꾸지 말고 Opus가 Astra에 versioned replan을 요청한다.

### 중단·재시도

- 다음 worker는 시작 전에 실제 branch/HEAD, `git status --short`, tracked/staged/untracked diff, B0 사본, 채택된 계획 버전, 앞 Task candidate fingerprint와 acceptance 상태를 확인한다. B0 3파일의 기존 hunk가 남아 있는지 확인하고 새 작업으로 주장하지 않는다. staged/partial 파일이 있으면 보존하며 blanket checkout/reset/stash/pop하지 않는다.
- Orca Run/Task/Dispatch/worker authority가 정본이다. worker는 coordinator 역할을 넘겨받지 않으며 timeout/연락 두절을 사망으로 판단하거나 새 editor를 만들지 않는다. retry 결정은 coordinator가 positively failed/stopped Dispatch를 확인한 뒤 같은 Task와 `--retry-of`로 수행한다.
- 같은 파일의 미완료 구현이 있으면 먼저 현 API·테스트 결과를 읽고 완료 지점부터 잇는다. 앞 Task 계약과 다른 상태는 변경을 덮지 않고 coordinator에게 보고한다. source semantics 의문은 임의 fallback 대신 unavailable 또는 ask로 처리한다.
- report를 재생성할 때는 해당 report가 소유한 파일만 갱신하고 기존 분석·입력·다른 실행 산출물을 지우지 않는다. JSON/CSV/HTML이 서로 다른 candidate 결과로 섞이지 않도록 같은 계산 결과 객체에서 함께 생성하고 output manifest에 fingerprint를 기록한다.
- meaningful boundary마다 coordinator가 checkpoint를 갱신한다. worker는 preamble대로 새 파일 시작/테스트 뒤/worker_done 직전에 check하고 5분 heartbeat를 유지한다. 질문은 Orca ask, 종료는 taskId+dispatchId와 3문장 요약을 갖춘 worker_done 한 번이다.
- exact candidate review 이후 source 수정이 있으면 기존 PASS는 자동 재사용하지 않는다. Opus coordinator는 필요한 validation과 별도 리뷰 갱신을 마친 뒤 실제 report 경로, 가능한 source와 unavailable 한계를 최종 보고한다.
