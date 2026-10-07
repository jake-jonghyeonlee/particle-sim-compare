# 독립 코드 리뷰 v1 — ROI CA 비교와 위치 중심 리포트 (candidate v1)

- 리뷰어: 새 세션의 독립 리뷰어(Claude `claude-opus-5-5`, effort high). 이 변경을 작성·계획하지 않았고 coordinator가 아니다. 하위 worker를 띄우지 않았고 코드를 고치지 않았다.
- Run `run_2336681f13e5`, Task `task_ad261bc34ace`, Dispatch `ctx_7ea3ac9ebb41`, 2026-10-01.
- 대상: `/Users/jonghyeonlee/git/particle-sim-compare`, branch `record-cno-open-fab-decisions`, HEAD `b507da96554baa658171dcae61a99342c9ec36e3`, 미커밋 작업 트리.
- 계약: `request.txt`, `plan-v1.md`, `plan-review-v1.md`(A1–A7), `task-T1/T2/T3.spec.md`, `checkpoint.md` 결정 기록을 전부 읽었다.

## 판정: **PASS** (blocking 0건) — 이 후보(candidate-v1.sha256)에만 유효

핵심 면적 계산, 원천 typing, 창 선정·비교, 리포트 출력에서 계약을 위반하는 틀린 수치를 만들지 못했다.
다만 should-fix 6건이 있고, 그중 **S1–S3은 실제 자료(특히 원천 manifest)를 쓰기 전에 고치기를 권한다.**
이 PASS는 합성 자료와 합성 생산자 사례에 대한 코드 검증이며 실제 정확도·수율에 대한 주장이 아니다.
브라우저 시각 검증은 하지 않았다(A6에 따라 coordinator 몫).

## 후보 해시 검증

| 시점 | 명령 | 결과 |
| --- | --- | --- |
| 시작 | `shasum -a 256 -c docs/orca/run_2336681f13e5/candidate-v1.sha256` | 29개 전부 OK |
| 시작 | `candidate-v1.sha256` 자체 / `candidate-v1.tracked.diff` | `adb0fce1…a8a6c` / `e71a1f9c…23fb` — 지시서 값과 일치 |
| 종료(이 문서 작성 직전과 직후 두 번) | 같은 `shasum -c` | 29개 전부 OK, HEAD 동일, `git status --short` 30행으로 시작과 동일 |

기존 미커밋 hunk 보존: `git apply --check --reverse baseline-preexisting.diff` 통과(선행 hunk 전부 존재).
`docs/open_questions.md`의 diff는 기준선과 바이트 단위로 같다. `adapters/particle_sim_run.py`에는 import 1줄과
`particle_sim_sources` 호출 hunk, `tests/test_particle_sim_export_integration.py`에는 assert hunk 2개만 추가되었다.
`baseline-preexisting.diff` sha256 `a00a5706…a8b3`도 기록과 일치한다.

## 실행한 명령과 결과

| 명령 | 결과 |
| --- | --- |
| `.venv/bin/python -m pytest -p no:cacheprovider -rs` | **333 passed, 56 skipped, 1 warning** (coordinator 기록과 동일). skip은 pass가 아니다 |
| 생산자 통합(`PARTICLE_SIM_ROOT`/`PARTICLE_SIM_PYTHON` 지정) `tests/test_particle_sim_export_integration.py` | **1 passed, 1 skipped**. skip된 것은 `test_export_load_contract`(pyarrow 없음) — S5 참고 |
| `.venv/bin/python scripts/regional_demo.py --output-dir /private/tmp/claude-501/review-run_2336681f13e5/demo` | exit 0, HTML 0.47 MB, SVG 28개, JSON/CSV 생성 |
| probe 1–7 (`/private/tmp/claude-501/review-run_2336681f13e5/probe*.py`) | 아래 "실행으로 확인" 참고 |
| probe 5 (생산자 venv, 두 저장소 src를 PYTHONPATH로, 출력은 tmp만) | 실제 nominal/probability run 생성 후 compare 어댑터 통과 |

저장소에는 이 문서 외에 아무것도 쓰지 않았다. git 변경·설치·네트워크 없음. 형제 저장소는 읽고 실행만 했다.

## 실행으로 확인한 것 (틀린 수치를 만들지 못한 항목)

**1. 면적 계산** (`regional_measurement.py`)
- 전체 창 적분 = 원천 CA(잔차 0), 틀린 total은 `conservation_failed`로 ROI null(재스케일 없음).
- fractional clipping: 원점 (10.25, −3.5), pitch 0.5에서 손계산과 차이 2.8e-17.
- 창 union 1회 적분: `a+b−교집합 = union`(오차 8.9e-16). 무작위 창 1–5개 union 200회가 20배 supersampling 값과 일치.
- 맞닿은 half-open 창 두 개의 합 = 전체. nm 격자 + um/nm/mm 창, total `mm^2`/`nm^2` 모두 같은 값.
- total 단위가 길이(`um`)면 `invalid_total_ca`, 무차원 `"1"`이면 `legacy_ca_not_area`.
- valid hole만 덮는 창·도메인 밖 창은 `empty_valid_area`(0 아님). zero total은 ROI 0, share null(`zero_total`).
- anisotropy `unsupported_grid`, 원점 None `missing_grid`, 음수/inf pitch·NaN 원점·미지원 단위 `invalid_grid`.
- float32 probability 512²: 생산자와 같은 float64 합에서 잔차 0. scalar를 float32로 반올림해도 상대 1.2e-8로 통과.

**2. 원천 typing** (`adapters/regional.py`)
- nominal kind + mean mode는 합이 같아도 `source_mode_mismatch`. mean 행 + nominal scalar도 같다.
- manifest: `..`·절대 경로·디렉터리 밖 symlink·중복·알 수 없는 sample·embedded 충돌·NaN pixel·NaN density·pickle NPY 모두 `DataContractError`.
- bundle이 scalar field를 모르면 `unverified_scalar_source`, legacy bundle은 `legacy_ca_not_area`.
- canonicalize 상호작용: 원천 부착 뒤 `prepare_ca_bundles(…, "nm")`로 CA가 `nm^2`로 바뀌어도 total 4.0 µm²로 일관. canonicalize 뒤 manifest를 읽어도 gate 통과.
- **실제 생산자 출력**(probe 5/5b/7): exporter → `load_dataset(mode="real")`에서 원천 36개(3+9 bundle × 3지름) 전부 available, 잔차 0. 직접 run nominal·probability(`--ca mean`)도 available, probability run + `--ca nominal`은 전부 `source_mode_mismatch`. 이 export로 리포트와 `run_record` 직렬화까지 성공.

**3. 창·비교** (`regions.py`, `regional_comparison.py`, `correspondence.py`)
- 지름 50 µm에서도 창 bounds 불변(재팽창 없음). 표시 margin 5 µm에서 정량 bounds·ID 동일.
- 100→80 / 20→30: `delta_total=-20`, `delta_roi=+10`, `local_ca_increased@1um`. pair 방향을 뒤집으면 ID 동일, delta 부호 반전.
- 무작위 150회(4/8 연결, merge gap 0/1/2.5): 최종 창 중첩 0건, 창 밖 잔여 0, 창별 ROI 합 = total.
- nonzero 원점 + translation 등록, 한쪽 지름 누락(`missing_size`, delta null), 기준 0(절대 delta 4, 상대 null, `new_risk`).
- subpixel 원점·다른 pitch·등록 없음: local delta null, own 값 유지. valid coverage 불일치는 `incomparable_domain`.
- `confirmed_added/removed/unknown`: 없는 쪽 CA null, `new_risk`/`risk_disappeared` 없음.
- 입력 배열·CA·원천 불변.

**4. 리포트·파이프라인** (`regional.py`, `regional_report.py`, `regional_plots.py`, `pipeline.py`)
- sample_id·comparison_id·frame_id·product_id·evidence·전극 role에 `<script>…"><img onerror>` 삽입: HTML 파서 기준 `script`/`img`/`link` 태그 0, `on*` 속성 0, 외부 URL 0, 중복 id 0, 끊긴 anchor 0. SVG 파일은 전부 XML로 파싱된다.
- JSON에 NaN/Infinity 0. JSON 행 수 = 비교 CSV 행 수, delta 값 불일치 0. CSV 수치가 HTML 표에 전부 나타난다.
- `enabled=false`는 출력 0개·디렉터리 미생성. 기본 on 상태에서 이상한 legacy bundle 14종(pixel size 없음, 단위 없음, CA 없음, hotspot 없음, 배열 None, 지름별 shape 다름, open 등)에 예외 없음.
- 전체 suite 안의 E2E·fingerprint·legacy 산출물 바이트 비교 테스트 통과.

## 읽어서만 확인한 것

- 생산자 의미: `critical_area_um2` = float64 `sum(map) × pixel_area`, mean은 float32 probability map의 같은 적분(`aggregation/critical_area.py:10–20`, `probability.py:50–54,73–85`). exporter provenance에 `assumption_set`/`legacy_kernel`/`post_step_policy`/`ca_source`가 있다(`export/simcompare.py:427–441`).
- `_training_fingerprint`는 `analysis` 절을 해시하지 않는다(`pipeline.py:92–118`).
- Fab/수율/Tree/SHAP 연산·placeholder 없음. CNO는 `FAILURE_TYPES`에 없고 `compare_pair`가 거부한다.
- 문서의 한계 서술(nominal 전용 exporter, 미등록 제품 간 local 대응 미구현, 합성 검증만)은 사실과 맞다.
- 신규 소스·테스트·문서 19개 파일에 가나/한자 0건(유니코드 범위 스캔 실행).

## 발견 사항

### Blocking
없음.

### Should-fix

**S1. 같은 sample 안에서 hotspot 이미지 좌표계와 manifest 원천 좌표계가 같은지 확인하지 않는다 (가장 우선)**
- 위치: 창은 hotspot 이미지에서 만들고(`src/simcompare/regions.py:110–129`, `regional_comparison.py:408–409,420`), 적분은 원천의 `frame`으로 한다(`regional_measurement.py:143–149`). manifest의 `origin_xy`/`frame_id`는 그대로 들어오고(`adapters/regional.py:288`) 어디에서도 이미지와 대조하지 않는다. hotspot 이미지가 있으면 원천 support는 쓰지 않는다(`regions.py:130`).
- 반례(probe 2-A): hotspot 이미지 origin (0,0), 같은 density를 가진 원천을 origin (1,0), `frame_id="some-other-frame"`으로 선언 → 창 두 개가 각각 `roi_ca_um2=2.0`, status available, reason 없음. 실제 그 hotspot의 CA는 각 4.0이다. 신호는 `outside_regions_ca_um2=4.0`뿐이고, 그림에도 원천 위치는 그려지지 않는다(`regional_plots.py:106`).
- 계약: request "Honor … physical origin … provenance", "mismatch … registration explicitly unavailable rather than wrong number". blocking으로 올리지 않은 이유: 선언된 두 origin을 문자 그대로 적용한 결과이고, 같은 sample 안의 frame 일치는 요청·계획 어디에도 정의되지 않았으며, A2 잔여가 불일치를 수치로 드러낸다. 자동 승격 경로(exporter·직접 run)는 이미지의 origin/pitch를 그대로 쓰므로 영향이 없다. manifest 경로만 해당한다.
- 권고: 같은 sample·지름에 hotspot 이미지와 원천이 모두 있으면 frame(최소 `frame_id`, particle_sim_* kind는 origin·pitch·shape까지)을 대조해 다르면 `incompatible_source_frame` 같은 사유로 unavailable 처리. 문서에 manifest `frame_id`/`origin_xy` 규약을 적는다.

**S2. 지름을 단위 환산 뒤 float `==`로 맞춘다**
- 위치: `adapters/regional.py:35–36,52,222,311–318`, `regions.py:112`, `regional_comparison.py:96–97`.
- 반례(probe 2-B, 3): `convert_length(700,"nm")` = `0.7000000000000001` ≠ `0.7`. bundle CA가 0.7 um일 때 manifest 행을 `700 nm`로 쓰면 `missing_size`로 unavailable. `0.7 um` 행과 `700 nm` 행을 함께 넣으면 **중복 검사를 통과**해 원천이 두 개 생긴다. hotspot이 nm, CA가 um이면 리포트에 0.7과 0.7000000000000001 지름 행이 따로 생긴다.
- 틀린 수치는 나오지 않지만(unavailable), "중복 source key 거부"(계획 3.2)가 이 경우 지켜지지 않는다. 저장소 자체 테스트도 manifest 지름을 nm로 쓴다(`tests/test_regional_sources.py` `manifest()`), 2000 nm라 우연히 맞을 뿐이다.
- 권고: 지름 키를 한 곳에서 정규화(예: um 환산 후 고정 자릿수 반올림 또는 명시 허용오차)하고 모든 매칭이 그 함수를 쓰게 한다.

**S3. 위치 근거가 없을 때 `selected_union_ca_um2`가 0.0/available로 나온다**
- 위치: `regional_comparison.py:295–296`. 창 목록이 비면 이유와 무관하게 선택 CA 0.0, reason None.
- 반례(probe 2-K): hotspot 배열 None(`location_unavailable`)과 관측된 all-zero(`observed_empty`)가 summary/CSV `selected_union` 행에서 똑같이 `(0.0, total, available)`. HTML 문구도 두 경우를 한 문장으로 묶는다(`regional_report.py:273`).
- 계약: request "Missing size/map/reference != zero; numeric zero and no hotspot distinguished", 계획 3.3-1. 완화 요소: `outside=total`이고 global 행의 `location_status`는 구분된다.
- 권고: region set의 reason이 있으면 union/outside를 null + 그 사유로 내보낸다.

**S4. 리포트 크기와 시간이 (창 수 × 지름 수 × 전체 이미지)로 커진다**
- 위치: `regional_plots.py:170–174`가 패널마다 모든 plane을 전체 해상도 PNG로 다시 인코딩해 inline하고, crop도 전체 plane을 넣고 clipPath로 자른다(`:243`). 같은 SVG를 HTML과 파일에 두 번 쓴다(`regional_report.py:280–285`). 분석 쪽도 행마다 `collect_support`(`regional_comparison.py:268`)와 전체 gate를 다시 돌리고, support 셀을 Python set으로 만든다(`regions.py:169–170`).
- 측정(probe 3, 6; 등록 pair 1개, 창 약 60개): 512²×8지름 HTML 9.3 MB/6.5 s, 1024²×8지름 16.1 MB/29 s, 512²×32지름 **35 MB, 출력 합계 78 MB, 25 s**. 실제 격자는 32지름이고 sample마다 own 비교가 생기므로 sample 수십 개면 수 GB가 된다.
- 계약: request "A clean location-centered integrated report", 계획 6절 위험 4(실측 뒤 최적화)에 해당하므로 blocking은 아니다.
- 권고: 표시 bounds로 실제 crop해서 인코딩하고, plane PNG를 sample 단위로 한 번만 만들어 재사용(`<use>` 또는 공유 data URI).

**S5. 실제 exporter의 "전체 합 = CA" assert가 이 환경에서 한 번도 실행되지 않는다**
- 위치: 추가된 assert는 `tests/test_particle_sim_export_integration.py:114–120`인데, 그 테스트는 `:72`의 `importorskip('pyarrow')`로 skip된다. 통과한 1건은 scalar를 일부러 덮어쓴 `conservation_failed` 쪽이다. 직접 run 연결(`adapters/particle_sim_run.py:679–684`)도 `test_particle_sim_run.py` 전체가 pyarrow로 skip되어 테스트로는 실행되지 않는다.
- 내가 probe 5/5b로 직접 실행해 양쪽 모두 통과를 확인했으므로 코드 결함은 아니다. 그러나 저장소의 acceptance 증거로는 S1(계획 5절) 양성 사례가 비어 있다.
- 권고: export를 읽는 쪽은 pyarrow가 필요 없으므로(통과한 테스트가 이미 그렇게 동작) 보존 assert를 pyarrow 없이 도는 테스트로 분리한다.

**S6. 유지보수성 — T2/T3 모듈이 저장소 스타일과 다르다 (사용자 명시 요구)**
- 쉼표 뒤 공백 없는 줄: `regional_plots.py` 86/309, `regional_report.py` 75/349, `regional_comparison.py` 43/445, `regional.py` 33/216. 기존 `pipeline.py`/`neighborhood.py`/`variants.py`는 0. T1 모듈은 1–3줄로 양호.
- `regional_report.py`는 120자 초과 28줄(최대 169자). 기존 모듈 최대는 150자.
- 이유 없는 숫자: `regional_plots.py` `_panel`의 y 위치 25/48/80/365/387/15(`:154–157,181,185–186`), `_chart`의 10/45/35/18/220/30/80(`:272–275,293,296,298`). A3 "숫자 상수는 설정 키 또는 이유가 적힌 이름 있는 상수".
- 중복: `_scalar`가 `adapters/regional.py:31`와 `regional_comparison.py:90`에 따로 있다(S2를 고칠 때 두 곳을 같이 고쳐야 한다). `frame_geometry_um`을 쓰려고 가짜 `SpatialCASource`를 만드는 코드가 `regions.py:87–93`, `regional_plots.py:96–99` 두 곳. 정렬 값 선택 로직이 `regional_comparison.py:361–372`와 `regional_report.py:191–202`에 중복(렌더러가 계산을 다시 하지 않는다는 원칙과도 어긋난다).
- 양호: 모듈 책임 분리, 모듈 상단 한국어 docstring(책임·하지 않는 일·수정 위치), 구획 주석, WHY 주석(재팽창 금지·보존 gate·등록·이중 합산·결측≠0), `docs/regional_analysis.md`의 "변경 목적 → 모듈/함수" 표는 실제 함수와 일치한다.

### Nit

- N1. 전체 CA delta는 양쪽 raster의 shape·pitch·valid mask가 같아야 나온다(`regional_comparison.py:166–179,201–205`). unit 크기가 다른 제품 간에는 항상 `incomparable_domain`(probe 2-E). 계획 3.2대로지만 문서에는 "호환되는 전체 scalar 비교"라고만 되어 있다. 문서에 조건을 적는 편이 정직하다. 한쪽만 원천이 있을 때도 같은 사유가 나와 원인이 가려진다.
- N2. `confirmed_added/removed`에 등록이 없으면 지역 행 `local_reason`이 관계가 아니라 `unregistered`로 나온다(`regional_comparison.py:436`). 짝이 없는 own 행도 `unregistered`.
- N3. `SupportStatus` docstring의 `invalid` 상태는 생성되지 않는다(`regions.py:43`).
- N4. demo manifest가 합성 map을 `kind="particle_sim_nominal"`로 선언한다(`scripts/regional_demo.py:97`). 계획 2절은 fixture용을 `explicit_ca_density`로 두었다. 리포트 원천 kind 칸에 합성 자료가 particle-sim으로 표시된다.
- N5. `exported_particle_sim_sources`가 잘못된 provenance(`calc_settings`가 문자열, `sample_id_mapping` 원소가 dict 아님, `formats`가 list)에서 `AttributeError`를 낸다(`adapters/regional.py:153–160,101`). 계약 1.2가 아닌 자료는 앞단에서 걸러지지 않는다.
- N6. `<image href>`만 쓰고 `xlink:href`가 없다(`regional_plots.py:173`). 오래된 SVG 뷰어에서 그림이 빈다.
- N7. 배경이 아주 작은 양수인 processed-intensity hotspot은 이미지 전체가 창 하나가 된다(probe 2-L). 계획 3.3-1대로지만 문서에 적어 둘 만하다.
- N8. `regional.py:199`의 함수 내부 import는 `regional_report.py:17`과의 순환 때문이다. `json_safe`/`RegionalAnalysis`를 별도 모듈로 옮기면 없어진다.
- N9. 원천 승격이 `analysis.regional.enabled=false`여도 매 load마다 hotspot 파일을 다시 읽고 해시한다(`adapters/example.py:820–821`).

## 테스트 평가 (계획 5절)

계획 5절에 backtick으로 이름이 적힌 테스트 62개는 모두 정의되어 있다(pyarrow로 skip되는 파일의 것은 실행되지 않았다). 대부분 반례를 실제 수치로 assert한다. 약한 것:

| 테스트 | 문제 |
| --- | --- |
| `tests/test_regions.py:55` `test_common_window_is_fixed_across_diameters` | 마지막 assert가 창 개수와 같은 길이의 리스트 길이를 비교해 항상 참. 지름별로 같은 창이 쓰였는지는 검사하지 않는다 |
| `tests/test_regional_measurement.py:59`, `tests/test_regional_comparison.py:248` `test_comparison_never_sums_pair_ca_into_product_total` | 필드 이름이 없다는 것만 확인 |
| `tests/test_regional_pipeline.py:75` `test_regional_results_do_not_depend_on_model_image_size` | `analyze_regions`가 `image_size`를 받지 않으므로 `first == second`는 구조상 항상 참 |
| `tests/test_regional_report.py:64` `test_report_is_self_contained_and_escapes_input_labels` | `product_id`만 주입. sample_id·comparison_id·frame_id·SVG 텍스트는 다루지 않는다(내 probe로는 통과) |

테스트가 없는 중요한 동작: S1(이미지와 원천 frame 불일치), S2(nm 지름), S3(위치 불명 시 union 요약), 실제 exporter 양성 gate(S5), 직접 run 연결, 잘못된 provenance payload(N5), 큰 입력의 리포트 크기(S4).

## 남는 한계 (계약상 허용, 최종 보고에 포함 권장)

- 현 exporter는 nominal map만 내보낸다. mean ROI는 직접 run 또는 manifest가 필요하다(직접 run 경로는 probe로만 실행 확인).
- local delta는 명시 대응 + translation 등록 + 같은 등방 lattice에서만 나온다. 미등록 제품 간 자동 국부 대응은 없다.
- 검증은 합성 fixture와 합성 생산자 사례뿐이다. 실제 대응표·등록·Fab 가중은 폐쇄망 자료가 필요하다.
- 시각 검증과 스크린샷은 이 리뷰에 포함되지 않는다.
