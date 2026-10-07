# 위치 중심 지역 CA 분석

지역 분석은 원본 정량 배열의 CA 기여를 물리 창에서 측정하고, 명시 대응·등록된 쌍을
동일 창에서 비교한다. 오토인코더 학습용 resize 배열이나 유사 사례 순위는 지역 근거로
사용하지 않는다. 전체 CA 감소와 특정 위치 CA 증가를 함께 볼 수 있다.

## 실행과 산출물

기존 학습·analyze 명령은 유지된다. `analysis.regional.enabled`의 기본값은 `true`이며
원천이 없는 기존 데이터도 전체 CA와 unavailable 사유를 담은 부가 보고서를 만든다.
기존 real 데이터/Fab 검증 및 체크포인트 검증은 그대로 적용된다.

```sh
.venv/bin/python scripts/regional_demo.py
```

이 명령은 학습 없이 `outputs/regional-demo/`에 결정적 **합성** 사례와 보고서를 쓴다.
대표 사례는 전체 100→80 µm²인데 고정 지역 20→30 µm², 지름별 곡선 교차,
hotspot 이동·신규·소멸, source 누락, 미등록, 명시 구조 추가/소멸이다.
합성 nominal source는 binary 배열과 동일-domain scalar의 보존 검사를 통과하지만
실제 particle-sim 생산 결과나 실제 정확도·수율·인과관계의 증거는 아니다.

| 파일 | 내용 |
| --- | --- |
| `regional_report.html` | 전체 위치 → 동일 축척 crop → 지름별 CA 곡선/표 → raw·delta·기여율·선정 사유 → provenance/불가 사유 |
| `regional_summary.json` | schema/algorithm/config/fingerprint, sample, pair, 창, 지름별 관측·delta, 보존 gate, 출력 목록 |
| `regional_measurements.csv` | 양쪽 원 CA/단위, 지역 raw·기여율·valid 면적·선택 union·창 밖 잔여, 상태/사유/원천 추적 |
| `regional_comparisons.csv` | query − reference delta, 상대율, 관계/등록/원천 불가 사유 |
| `figures/regional/*.svg` | 안전한 hash 이름의 전체 위치·crop·곡선 그림 |
| `inputs/correspondence.json`, `inputs/source_manifest.json`, `inputs/maps/*.npy` | demo의 완전한 명시 대응표·원천 예제 |

HTML의 CSS/SVG/PNG는 inline이며 JS·외부 폰트·CDN·네트워크 요청이 없다. HTML만 옮겨도
본문 그림을 볼 수 있다. JSON/CSV/SVG 다운로드 링크는 sibling 파일에 대한 로컬 링크다.
원본 plane의 표시 이미지는 sample마다 한 번 만들고, crop은 표시 bounds와 만나는 셀만 인코딩한다.
본문 PNG는 공유 SVG defs/use로 재사용한다. 정적 SVG에는 그 그림의 PNG만 포함하므로 단독 열기가
가능하다. 전체 CA 곡선은 비교마다 한 번 표시하며 상세 provenance는 JSON/CSV에 모두 남긴다.
긴 provenance는 접힌 상세에서 확인한다. 자동 검사와 별도로 최종 브라우저 시각 검증이 필요하다.

## 설정

```yaml
analysis:
  regional:
    enabled: true
    correspondence_file: ../regional-inputs/correspondence.json
    source_manifest: ../regional-inputs/source_manifest.json
    connectivity: 8
    merge_gap_um: 0.0
    display_context_margin_um: 0.0
    conservation_rtol: 0.000001
    conservation_atol_um2: 0.000000001
    coordinate_atol_um: 0.000000001
    delta_atol_um2: 0.000000001
    include_area_normalized: false
```

두 파일 키만 `null`이 허용된다. 파일 경로는 YAML 기준이며 지정 파일 누락, 알 수 없는 키,
잘못된 타입/null/NaN/음수 값은 오류다. connectivity는 4 또는 8이다.
margin은 표시 crop만 넓히며 정량 창·CA·지역 ID를 바꾸지 않는다. merge gap은 성분 창
묶음 정책이다. 보존 허용값은 float 오차 검사이며 수치 rescale 허용이 아니다.
coordinate 허용값은 절대 um이며 큰 좌표에 비례해 늘리지 않는다.
공통 `diameter_key`는 지름을 um 환산 후 소수 12자리(1e-12 um 해상도)로 고정 반올림한다.
700 nm와 0.7 um의 float 표현 오차만 제거하며 다른 키 사이에는 보간/외삽하지 않는다.
매칭·union 지름 목록·중복 검사는 모두 같은 키를 사용한다. delta 허용값은 증가/감소 label에만 사용하며
0 기준 상대율을 만들어 내는 epsilon이 아니다.

대응표/manifest는 기본적으로 `data_dir` **밖**에 둔다. 보고 전용 입력의 의미가 바뀌면
regional fingerprint와 run_record의 입력 파일 SHA-256이 바뀌지만 학습 fingerprint는
바뀌지 않는다. `data_dir` 안에 둔 파일은 기존 보수적 학습 fingerprint 정책에 포함되므로
변경하면 재학습이 요구된다. `enabled: false`는 regional orchestration의 manifest IO/출력을 수행하지 않는다.
기존 dataset 어댑터는 analysis 설정을 받지 않는 공통 적재 계약이므로 exporter 원천 자동 승격의
원본 재읽기/해시는 disabled에서도 수행된다. 이 IO를 생략하려면 향후 어댑터 호출 계약에
적재 목적을 명시적으로 추가해야 한다. 이번에는 기존 어댑터 signature를 바꾸지 않았다.
이미 있는 보고 파일을 삭제하지 않으므로 현재 실행 여부는 run_record를 확인한다.

## 정량 원천과 좌표

허용 kind는 `particle_sim_nominal`(binary), `particle_sim_mean`(probability),
`explicit_ca_density`([0,1] density)다. 계산 field `ca_nominal_um2`/`ca_mean_um2`,
producer/version, calculation contract, source step/unit/size 및 settings를 보존한다.
nominal/mean 혼합 또는 미확인 계산 정의는 합계가 우연히 같아도 비교 불가다.
설계가 달라져 config hash가 바뀌는 것만으로 비교를 막지는 않는다.

배열은 `[y,x]`, 원점은 `[0,0]` 셀의 좌상단 모서리, x는 오른쪽/y는 아래쪽이다.
원점 `None`은 0이 아니다. 등방 scalar pitch만 지원하며 anisotropy·불명 원점은
명시 unavailable이다. nm/um/mm 입력은 um로, 면적은 µm²로 환산한다. 유효 mask는
동일 shape의 bool NPY이며 생략하면 전체 직사각 격자가 유효하다.
창은 반개구간 `[x0,x1) × [y0,y1)`이고 경계 셀은 교집합 면적만 반영한다.
유효 영역 밖/구멍은 위험 0으로 표현하지 않는다. 그림의 사선은 영역 밖 또는 유효성 미확인이다.

정량 계산 전에 **전체 valid domain의 density 합 × 셀 면적**과 원래 동일 계산의 scalar
CA를 보존 gate로 비교한다. residual·허용값·provenance를 저장한다. 실패하면 지역 CA는
null이다. map을 CA에 맞춰 보정하거나 전체 CA를 픽셀에 분배하지 않는다.
일반 processed intensity, 색상 범례 이미지, union footprint, [0,1] 값이라는 사실만으로는
정량 source로 승격되지 않는다. legacy 무차원 CA는 원래 단위로 남고 µm² 지역값을 만들지 않는다.

particle-sim 직접 run은 nominal/mean 원본을 연결할 수 있다. 현재 exporter는 nominal map만
내보내므로 native mean ROI는 직접 run 또는 명시 manifest가 필요하다. upstream exporter는
변경하지 않았다. demo 성공으로 실제 생산자/mean exporter 검증을 대신하지 않는다.

동일 sample의 hotspot과 quantitative source는 같은 격자여야 한다. source의 `origin_xy`는
hotspot `[0,0]` 셀의 좌상단이며 translation 이후 공통 원점이 아닌 **원래 local 원점**이다.
ImageInput에는 frame_id 선언이 없으므로 manifest의 frame_id는 provenance로 보존하며
이름 차이만으로 원천을 차단하지 않는다. `local:<sample_id>`는 예제의 이름일 뿐 필수 규약이 아니다.
um 환산 origin/pitch(절대 coordinate_atol_um), shape가 이미지와 다르면
`incompatible_source_frame`으로 지역 CA/선택 union/창 밖 잔여를 null 처리한다.
등록 translation을 manifest 원점에 미리 적용하면 이 검사에서 불일치가 된다.

manifest version은 문자열 `"1"`이며 root는 `schema_version`, `sources`만 가진다.
행은 demo의 JSON을 참고한다. 필수 항목은 sample/diameter/단위/kind/mode/semantics/
ca_source/calculation_contract_id/density_path/origin_xy/pixel_size/length_unit/frame_id/
producer/version/source_files/evidence다. 선택 항목은 valid_mask_path/source_step/unit_id/settings다.
배열은 원본 NPY만 허용하고 pickle은 읽지 않는다. 경로는 manifest 디렉터리 상대 경로이며
절대 경로/`..`/디렉터리 밖 symlink, 중복 sample·지름, embedded 원천 override는 거부한다.
scalar는 bundle의 동일 지름·ca_source에서 연결하므로 문서에 임의 total을 넣지 않는다.

## Binary nominal 계약과 분모의 의미

확정된 binary nominal은 `CA_R = k(source,d) × N_active,R`이며 같은 source·지름의
활성 픽셀은 모두 같은 CA 기여를 가진다. ROI를 나누거나 면적으로 정규화해도 원본에
픽셀별 강도 차이가 새로 생기지 않는다. particle-sim 면적 nominal(`ca_nominal_um2`)만
생산자 정의와 보존 gate로 `k = pitch²`가 확인된다. 다른 source의 k는 별도 선언이 필요하고
제품 간 pitch/ROI가 달라도 계수가 같다고 가정하지 않는다. mean/probability는 별도 계약이다.

범례 없는 PNG 0/255와 정수 NPY의 정확한 0/255는 모두 0/1·`binary`로 읽는다.
NPY 0/1은 그대로이며 비이진 [0,1]은 `processed_intensity`다. 그 외 범위 밖 NPY는 오류다.
이 라벨만으로 정량 원천을 만들지 않으며 exporter 선언·동일-domain 보존 gate를 유지한다.

- CA / 자기 활성 hotspot 면적은 `k / pitch²`라는 상수다. particle-sim 면적 nominal에서는
  1이므로 취약 위치 순위로 쓰지 않는다.
- CA / **고정 공통 창의 valid 면적**은 particle-sim 면적 nominal에서 점유율이다.
  일반 binary source에서는 점유율에 `k / pitch²`가 곱해진 값이다. 동일 계수 계약과
  동일 고정 창에서만 비교하며 제품마다 따로 만든 tight bbox의 분모는 비교하지 않는다.
- raw ROI CA는 분모 문제가 없지만 동일 구조와 동등한 측정 범위를 요구한다.
  등록된 고정 공통 창 또는 합의한 비교 단위가 필요하다. 기능·범위가 다른 tight crop은
  서로 다른 support를 적분하므로 own 값을 나란히 보여 주고 delta를 만들지 않는다.

일반 비-exporter binary 자료의 dataset-level 정량 원천 선언(B3)은 **미결정**이다.
기존 explicit manifest는 NPY를 받고 PNG 자체를 정량 원천으로 승격하지 않는다.
legacy 무차원 CA의 지역 기여와 µm² 환산 계수(B4)도 **미결정**이며 이번 binary 확인으로
환산 근거가 생기지 않는다. 입력별 계수·단위·provenance 계약을 받은 뒤 별도 구현해야 한다.

모델 입력의 `dataset.letterbox`는 기존 bilinear 보간을 유지한다(B5). 원본 binary가 모델용
이미지에서 중간값이 되어도 원본 배열을 쓰는 지역 CA에는 영향을 주지 않는다.
학습 관련 수치 수정은 정수 0/255 hotspot NPY 디코딩 오류(B1)에 한정된다.
원본과 같은 격자의 binary 입력에서 hotspot mean은 활성 비율, 비어 있지 않은 max는 1이며
frac50도 활성 비율이다. 모델 resize가 있으면 보간된 배열의 요약값이므로 정확한 원본
점유율과 다를 수 있다.
이 요약 feature는 `with_risk_baseline`만 사용하며 `shape_only`는 `include_risk=False`라
hotspot 요약을 넣지 않는다(B7). feature 구성은 이번에 변경하지 않았다.

legacy `variants._roi`는 편집 후보 표시용 heuristic이며 입자 지름만큼 bbox를 pad한다(B2).
binary 동률 성분은 면적 내림차순·좌상단 순으로 편집 전극을 포함하는 창을 찾는다.
단일 성분·graded intensity와 pad는 유지한다. 이는 정량 지역 창과 별도이며
`regions.build_regions`의 hotspot에는 입자 반경을 다시 더하지 않는다.

## 대응·등록·선정·비교

대응표 version `"1"`의 `pairs`에 comparison ID, 양쪽 sample ID, relationship,
전극 slot a/b의 실제 role/layer와 canonical ID, 공정 대응, evidence를 명시한다.
샘플 이름·형상 유사도·embedding으로 대응을 추론하지 않는다. 구조 상태는 다음과 같다.

| relationship | 관측 쪽 | 없는 쪽 |
| --- | --- | --- |
| `confirmed_same_structure` | 양쪽 | 없음 |
| `confirmed_added` | query | reference의 명시 product/design context; CA null |
| `confirmed_removed` | reference | query의 명시 product/design context; CA null |
| `unknown` | 적어도 한쪽 | 미확인; 구조 부재/위험 0을 추론하지 않음 |

등록은 evidence와 frame ID, 길이 단위, 양쪽 translation만 받는다. 같은 등방 pitch와
공통 lattice 정렬을 확인한다. rotation/scale/shear 또는 subpixel 정렬은 지원하지 않는다.
배열 shape가 같다는 사실은 등록 증거가 아니다. 미등록은 각자의 local 좌표/창/CA를 별도
패널에 보여 준다. 호환되는 전체 scalar 비교와 local delta 불가 상태를 구분한다.
전체 scalar delta도 domain 동등성이 필요하다. 양쪽 typed 원천이 있으면 raster shape·pitch·
valid mask가 같아야 한다(원점 등록은 local delta 조건). 양쪽 원천이 없으면 명시 unit 폭/높이를
환산한 domain과 scalar 계산 정의가 같아야 한다. 한쪽에만 원천이 있으면 domain 증거가 달라
`one_sided_source_domain`, 양쪽 domain이 다르면 `incomparable_domain`으로 delta를 비운다.

**v1 local delta는 명시적으로 대응되고 등록된 pair에만 제공한다.** 등록되지 않은 제품 간
layout 기반 자동 local correspondence는 구현하지 않았다. 이 후속 확장 지점은
`regions.py`의 위치/창 결과와 `regional_comparison.py`의 비교 자격 검사 사이이다.
자동 연결을 추가하더라도 대응 evidence, 좌표/valid coverage, 불확실성 계약부터 별도 정의해야 한다.

processed-intensity의 배경이 아주 작더라도 양수이면 포함하므로 전체 이미지가 한 창이 될 수 있다.
자동 threshold로 그 배경을 버리지 않는다. 원 자료의 의미를 확인해 생산자 단계에서 정해야 한다.

양쪽·모든 관측 지름의 양수 support를 함께 사용한다. 약한/작은 성분, 이전 위치,
신규/소멸 위치도 포함한다. 이미 입자 footprint가 포함된 hotspot을 반경으로 재팽창하지 않는다.
성분 bbox를 결정적으로 묶고 중첩 창을 병합하여 최종 창이 서로 겹치지 않게 한다.
이 창과 지역 ID는 지름별로 고정된다. 각 창의 CA level/증가량으로 지름별 정렬하지만
peak/overlap/geometry로 지역을 누락하거나 상위 k만 남기지 않는다.

delta는 항상 query − reference다. 미관측 지름·map·baseline은 null이며 곡선은 해당 지름에서
끊긴다. 관측된 0과 빈 hotspot은 결측과 다르다. ref=0이면 절대 delta는 제공할 수 있으나
상대율은 null이다. 위험 소멸은 동일 구조의 관측된 지역 0이며 구조 소멸과 다르다.
원천 정의/등록/valid coverage가 다르면 local delta=null, 각자의 own 값은 남긴다.

raw `roi_ca_um2`, 기여율 `roi_ca_um2 / unit_total_ca_um2`, 별도 면적 정규화
`roi_ca_per_valid_area`는 다른 출력이다. share/정규화 비율을 합산하지 않는다.
같은 source·mode·지름·domain의 창 union은 **한 번만** 적분한다.
`outside_regions_ca_um2 = unit_total_ca_um2 − selected_union_ca_um2`도 기록한다.
위치 근거가 없는 region set에서는 선택 union/창 밖 잔여가 `location_unavailable`과 null이다.
실제로 관측된 빈 support만 선택 union=0/창 밖=전체 CA로 표현한다. HTML도 두 경우를 구분한다.
서로 다른 전극쌍·step·불량 source의 total을 제품/layer total로 합하지 않는다.

## 유지보수와 확장 경계

| 변경 목적 → 수정할 모듈/함수 | 경계 |
| --- | --- |
| typed source/좌표/측정 필드 → `regional_schema.py` | optional bundle 필드로 기존 적재 계약 유지 |
| 단위·fractional clipping·보존/union 규칙 → `regional_measurement.py` | 순수 계산, IO/plot/입력 변경 금지 |
| 지름 키 정밀도 → `regional_measurement.py`의 `diameter_key` | 모든 match/중복 검사에 공통 적용, 보간 없음 |
| 실제 생산자 또는 source manifest 연결 → `adapters/regional.py`의 `particle_sim_sources`, `load_source_manifest` | scalar 분할/보정 없이 원본 의미·파일 추적 |
| 실제 대응표 공급 → `correspondence.py`의 `load_correspondence`, `parse_correspondence` | slot/layer/process/불량 검증, unknown≠absent |
| 이미지·원천 frame 검사 → `adapters/regional.py`의 `source_frame_reason` | 이름 추론 없이 실제 격자 비교, 불가 사유를 행·요약에 반영 |
| binary 파일 복호 → `adapters/example.py`의 `read_image` | PNG/정수 NPY 0/255 복호와 값 의미, 정량 승격은 별도 |
| legacy 편집 ROI → `variants.py`의 `_roi` | regional 창과 별도, pad·graded 경로 유지 |
| support 연결/고정 창 정책 → `regions.py`의 `build_regions` | 양쪽·모든 지름, 추가 반경 dilation 금지 |
| 비교 자격·delta·선정 이유 → `regional_comparison.py`의 `compare_pair`, `rank_rows` | 등록/원천/valid coverage 검증 |
| 분석 조합/fingerprint/run_record → `regional.py`의 `analyze_regions`, `write_regional_analysis` | AE/search와 보고 입력 분리 |
| 원본 좌표 그림/결측 곡선 → `regional_plots.py`의 `overview_svg`, `crop_svg`, `curves_svg` | 표시만; CA 적분/등록 추론 금지 |
| 보고 계약/JSON 결측 변환 → `regional_schema.py`의 `RegionalAnalysis`, `json_safe` | 계산·렌더러 간 순환 import 없음 |
| HTML/CSV/JSON 출력 → `regional_report.py`의 `write_report` | 한 결과 객체에서 offline 안전 출력 |
| 대표 합성 반례 → `scripts/regional_demo.py`의 `build_demo` | 테스트도 같은 builder, 실제 정확도 주장 금지 |

실제 correspondence 자료는 resolver 입력 인터페이스로 연결한다. 공장 weighting의 후속 입력
경계는 검증된 지름별 RegionMeasurement와 source provenance다. 별도 Fab 계약을 받아야 하며
이번에는 Fab 가중식/CAA/yield 연산을 추가하지 않았다.

향후 Tree는 취약·비취약 지역의 geometry/context feature를 별도로 측정한 뒤 명시 label과
분리해야 한다. hotspot은 label로만 사용하고 CA/hotspot/full AE z를 feature로 넣지 않는다.
pair-global geometry를 local feature로 복사하지 않는다. 제품/설계 그룹 분리와 누출 검증은
후속 모델 계약이 필요하다. 현재 placeholder model/class/training/SHAP 코드는 없다.
