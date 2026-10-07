# 독립 코드 재리뷰 v2 — 리뷰 v1 수정 + binary hotspot 확정 계약 + `_roi` 회귀 수정 (candidate v2)

- 리뷰어: 독립 리뷰어(Claude `claude-opus-5-5`, effort high). 이 변경을 작성·계획하지 않았고 coordinator가 아니다. 하위 worker를 띄우지 않았고 코드를 고치지 않았다.
- Run `run_2336681f13e5`, Task `task_e969c7995706`, Dispatch `ctx_23722561e819`, 2026-10-01.
- 대상: `/Users/jonghyeonlee/git/particle-sim-compare`, branch `record-cno-open-fab-decisions`, HEAD `b507da96554baa658171dcae61a99342c9ec36e3`, 미커밋 작업 트리.
- 계약: `review-v1.md`, `task-T4-review-fixes.spec.md`, `audit-binary-hotspot-contract.md`, `task-T5-binary-contract.spec.md`, `task-T6-roi-memory.spec.md`, `checkpoint.md`(결정 기록, T4/T5/T6 행), `outputs/regional-demo/t4-review-fixes.md`, `t5-binary-contract.md`, `request.txt`, `plan-v1.md`, `plan-review-v1.md`, `task-review-v2.spec.md`를 전부 읽었다.

## 판정: **PASS** (blocking 0건) — 이 후보(`candidate-v2.sha256`)에만 유효

v1의 should-fix 6건은 모두 수정되었고(S6은 일부 잔여), 재실행한 반례에서 틀린 수치를 만들지 못했다.
binary 계약(T5)과 `_roi`(T5+T6)도 계약대로 동작한다. 새 should-fix 4건과 nit이 있다.
그중 **F1, F2는 실제 manifest 자료를 쓰기 전에, F3은 정수 0/255 NPY hotspot 자료로 이미 학습한 체크포인트가 있다면 그 전에** 처리하기를 권한다.
이 PASS는 합성 자료와 합성 생산자 사례에 대한 코드 검증이며 실제 정확도·수율 주장이 아니다.
v1 PASS는 이 후보에 재사용하지 않았다. 브라우저 시각 검증은 하지 않았다(coordinator 몫).

## 후보 해시 검증

| 시점 | 확인 | 결과 |
| --- | --- | --- |
| 시작 | `shasum -a 256 -c docs/orca/run_2336681f13e5/candidate-v2.sha256` | 35개 전부 OK |
| 시작 | manifest 자체 / `candidate-v2.tracked.diff` | `26c1397e…1064` / `4869b415…b116` — 지시서 값과 일치 |
| 시작 | `git diff \| shasum -a 256` | `4869b415…b116` (tracked diff 사본과 동일) |
| 종료(이 문서 작성 직전과 직후) | 같은 `shasum -c`, `git diff` 해시, HEAD, `git status --short` 36행 | 35개 전부 OK, 시작과 동일 |

기존 미커밋 hunk: `baseline-preexisting.diff`(sha256 `a00a5706…a8b3`)를 파일별로 나눠 `git apply --check --reverse`로 확인했다.
`adapters/particle_sim_run.py`, `tests/test_particle_sim_export_integration.py`의 선행 hunk는 전부 그대로다.
`docs/open_questions.md`는 기준선 diff와 비교해 허가된 세 곳(‘Hotspot 색상·투명도·축척’ 행, ‘국부 이미지 범위·이물 영향 영역’ 행, 2026-10-01 bullet 1개 → 3개)만 달라졌고 나머지 선행 hunk는 바이트 단위로 같다.
저장소에서 내가 만들거나 바꾼 파일은 이 문서뿐이다(후보 35개 해시 불변, 형제 저장소 `git status` 해시 불변). git 변경·설치·네트워크 없음.

## 실행한 명령과 결과

probe는 모두 `/private/tmp/claude-501/review-run_2336681f13e5/`(v1 probe 재사용)와 그 아래 `v2/`(새 probe)에 있다.

| 명령 | 결과 |
| --- | --- |
| `.venv/bin/python -m pytest -p no:cacheprovider -o addopts=""` | **381 passed, 57 skipped, 1 warning** (coordinator 기록과 동일). skip은 pyarrow 54, 생산자 env 3이며 pass가 아니다 |
| 생산자 env 지정, `tests/test_particle_sim_export_integration.py tests/test_particle_sim_export_positive.py -rs -v` | **2 passed, 1 skipped**. 새 `test_particle_sim_export_positive_conservation_without_pyarrow` PASSED(이 환경에 pyarrow 없음 확인). skip은 `test_export_load_contract`(pyarrow) |
| `scripts/regional_demo.py --output-dir <tmp>` | exit 0, HTML 245 KB(v1 471 KB), SVG 34개 |
| v1 probe 1·2·3·4·5·5b·6·7 재실행 | 아래 항목별 표 참고. probe 1 결과는 v1과 같다 |
| 새 probe 5c(생산자 인터프리터), 8, 9, 10, 11 + 소형 확인 스크립트 | 아래 참고 |
| 줄 길이·공백·가나/한자 스캔, `git diff --check` | 아래 F4 참고. `git diff --check` 통과, 가나/한자 0 |

## 1. v1 지적 항목별 상태

"실행"은 내가 직접 돌려 확인한 것, "읽기"는 코드·문서를 읽어 확인한 것이다.

| 항목 | 상태 | 확인 방법과 결과 |
| --- | --- | --- |
| S1 이미지와 원천 frame 불일치 | **fixed** | 실행(probe 2-A 재실행): origin (1,0)·다른 frame_id 원천이 `roi=None`, `incompatible_source_frame`, union/outside null. v1의 `2.0/available`은 재현되지 않는다. shape·pitch 불일치도 차단(probe 8). 회귀 점검: nm로 표현한 같은 frame, `frame_id=None`, origin 차이 5e-10 um(허용오차 안)은 통과. 실제 exporter 36개 원천, 직접 nominal run, 직접 probability run + `--ca mean`(probe 5b/5c/7) 모두 차단 0건, 창 밖 잔여 0. 잔여 문제는 F1, F2 |
| S2 지름 float 일치 | **fixed** | 실행(probe 8, probe 3): `diameter_key(700,"nm") == 0.7`. hotspot 700 nm + CA 0.7 um, CA 지름이 nm인 bundle, 700 nm manifest 행 모두 연결되어 ROI 4.0. `0.7 um + 700 nm`, `700 nm + 0.0007 mm`, embedded 0.7 um + manifest 700 nm는 전부 duplicate `DataContractError`. 32개 격자(um·nm)와 0.1 um 간격 500개는 서로 합쳐지지 않는다. 5e-13 um 이내만 같은 키가 된다(0.7과 0.7+1e-11은 별개). `_scalar` 중복 제거는 읽기로 확인(`scalar_observation` 하나) |
| S3 위치 불명과 관측 0 | **fixed** | 실행(probe 2-K): 배열 None/음수/NaN → union·outside null + `location_unavailable`, all-zero → `0.0 / total / available`. 지름별 혼합(1 um 빈 영상, 2 um 배열 없음)도 지름마다 구분된다. HTML 문구 두 종류 확인. 잔여는 nit N-b |
| S4 리포트 크기·시간 | **fixed** | 실행(probe 3, 6, 같은 생성기): 512²×8지름 HTML **1.6 MB / 1.6 s**(v1 9.3 MB / 6.5 s), 1024²×8지름 1.7 MB / 5.3 s(v1 16.1 MB / 29 s), 512²×32지름 HTML **5.0 MB, 출력 합계 33.3 MB, 6.8 s**(v1 35 MB / 78 MB / 25 s). demo의 비교·측정 CSV는 v1과 수치·지역 ID가 같고(바뀐 것은 N2 사유, N4 kind뿐), JSON의 창·support 상태·source 요약도 같다. crop 타일 90개의 배치가 정량 창과 같은 셀 격자에 놓이는지 확인(오차 3.6e-15 셀) |
| S5 exporter 양성 gate | **fixed** | 실행: 위 표. pyarrow 없이 PASSED |
| S6 스타일·중복 | **대부분 fixed, 일부 잔여** | 실행(스캔): 쉼표 뒤 공백 누락 0, 대상 모듈 120자 초과 0(예외 1줄). 읽기: plot 숫자 상수화와 한국어 이유, 가짜 `SpatialCASource` 제거(`geometry_um` 공유), 정렬 값 `ordering_value` 한 곳. 잔여는 F4 |
| N1 전체 scalar 비교 조건 | fixed + documented | 실행: 한쪽만 원천이면 `one_sided_source_domain`. 문서 `docs/regional_analysis.md:174–177` |
| N2 added/removed 사유 | fixed | 실행: 지역 행 `local_reason`이 `confirmed_added`/`confirmed_removed` |
| N3 `SupportStatus` docstring | fixed | 읽기(`regions.py:43`) |
| N4 demo manifest kind | fixed | 실행: demo CSV의 `source_kind`가 `explicit_ca_density` |
| N5 잘못된 provenance | fixed | 실행(probe 6): 문자열 `calc_settings`, dict 아닌 mapping 원소, list `formats` → `DataContractError`, 그 외는 빈 목록. `AttributeError` 없음 |
| N6 `xlink:href` | fixed | 실행(HTML 파서): `image`/`use`에 `href`와 `xlink:href` 모두 있음 |
| N7 양수 배경 | documented | 읽기(`docs/regional_analysis.md:184–185`), probe 2-L 동작은 그대로 |
| N8 함수 내부 import | fixed | 읽기 + 스캔: 지역 모듈에 함수 내부 import 없음(`TYPE_CHECKING`만) |
| N9 disabled 때 승격 IO | documented(미수정) | 읽기(`docs/regional_analysis.md:70–73`). 어댑터 signature를 바꾸지 않는다는 이유가 적혀 있다 |
| 약한 테스트: 고정 창 | fixed | 읽기(`tests/test_regions.py:55`): 지름별 region ID·요청 창 동일성과 사라진 지름의 0을 assert |
| 약한 테스트: pair 합산 | fixed | 읽기: source 목록을 실제로 넘겨 `DataContractError`를 assert |
| 약한 테스트: resize 독립 | **부분** | 읽기(`tests/test_regional_pipeline.py:75`): encode shape와 합이 다른 것, delta 10을 추가로 확인하지만 `first == second`는 여전히 구조상 참이다(N-h) |
| 약한 테스트: escape | fixed | 읽기 + 실행(probe 3/4): sample_id·comparison_id·frame_id·product_id 주입, HTML 파서 기준 `script`/`img`/`link` 0, `on*` 속성 0, 중복 id 0, 끊긴 anchor 0, SVG 전부 XML 파싱 |
| 시각 nit 3건 | fixed(자동 확인만) | 읽기: 기여율 4자리, `td` 줄바꿈 CSS, 부재 쪽 `null` 제거. 화면 확인은 coordinator 몫 |

## 2. binary 확정 계약 (T5)

**`read_image`** — 실행(probe 9, HEAD 버전을 `git show`로 꺼내 같은 파일을 읽혀 비교):
- PNG 0/255, float32/float64 NPY 0/1, uint8/uint16/int64 NPY 0/255, uint8 NPY 0/1, bool NPY, 1-bit PNG가 모두 같은 float32 0/1 배열과 `binary`.
- 회색 PNG, [0,1] 비이진 NPY, 1e-6 배경 NPY는 값이 HEAD와 같고 `processed_intensity`.
- float 0/255, uint8 회색 0..255, `{0,1,255}`, 음수, `1.0000001`, inf NPY는 파일 이름이 들어간 `DataContractError`.
- layout(PNG/NPY 4종)과 범례 + NPY hotspot은 값·의미가 HEAD와 같다. 범례 PNG 분기는 읽기로만 확인(변경된 줄 없음).
- HEAD와 값이 달라지는 입력은 정수 0/255 NPY(255 → 1)와 새로 거부되는 범위 밖 NPY뿐이다.

**소비자** — 읽기 + 실행: `schema.validate_bundle`은 `binary`를 허용한다. `dataset`/`baseline`/`preprocess`는 `value_semantics`를 읽지 않는다. 지역 모듈은 fingerprint 기록(`regional.py:64`)에만 쓴다. 직접 run 어댑터는 원래 `binary`/`probability`를 준다.
**승격 규칙** — 실행: 합성 demo 자료(hotspot 60개 중 binary 3개)에서 typed 원천 0개, E2E 테스트의 `source_validations` 전부 unavailable. 읽기: `exported_particle_sim_sources`는 `value_semantics`를 보지 않고 exporter 선언·raw identity·인코딩·보존 gate를 요구한다.

**문서** — 읽기: `open_questions.md`, `data_contract.md`, `regional_analysis.md`, README가 일반형 `CA_R = k(source,d) × N_active,R`을 먼저 쓰고 `k = pitch²`를 particle-sim 면적 nominal에만 한정한다. legacy 무차원 → µm² 계수 미확정, 제품 간 계수 동일 가정 금지, 분모 세 가지(자기 활성 면적 = 상수, 고정 공통 창 점유율은 같은 창에서만, raw ROI CA도 같은 구조 + 동등 범위 필요), B3/B4 미결정이 모두 적혀 있다. 과장된 문장은 찾지 못했다. 문서 nit은 N-e.

| audit 항목 | 상태 |
| --- | --- |
| B1 | fixed (실행) |
| B2 | fixed + documented (실행, 3절) |
| B3, B4 | 미구현·미결정으로 문서화 (읽기) |
| B5, B7, B8 | documented, 코드 변경 없음 (읽기; `dataset.py`·`baseline.py`는 후보에 없음) |
| B6 | 문서 갱신 (읽기) |

## 3. `variants._roi` (T5 + T6)

실행(probe 10, 11):
- **HEAD와 동일**: 단일 성분 binary, graded, 최댓값이 두 곳인 graded, NaN 포함, 평탄한 비이진 단일 성분, 여러 지름(shape 불일치 포함) 각 400건, 총 2400건에서 ROI 값과 source가 비트 단위로 같다.
- **다중 성분 binary**: 무작위 1499건(성분 2개 이상, pitch 8종·pad 5종·level 1/0.4/255)이 독립 기준 구현(scipy 4-연결 label → 면적 내림차순 → 첫 raster 픽셀 → pad한 bbox가 편집 픽셀을 포함하는 첫 성분, 없으면 `closest_zone`)과 전부 일치. 그중 740건은 HEAD와 다르다(T5가 의도한 동작 변경).
- **labelling**: `_component_bounds`의 (개수, 첫 raster 픽셀, bbox)가 scipy 4-연결 label과 300건 모두 일치, 각 성분 개수 = `_flood` 결과, 입력 불변.
- **`_contains_prefix`**: 픽셀 중심에 정확히 놓인 경계, `nextafter`로 한 칸 옮긴 경계, `_bounds_um`이 반올림한 ROI, 뒤집힌·격자 밖 ROI 포함 42000건에서 `_contains`와 판정이 같다.
- **비용**: 512×512 체커보드(성분 131072개) 0.12 s(첫 성분 선택) / 0.53 s(전 성분 검사), tracemalloc peak **37.2 MiB**(coordinator 37.6 MiB와 일치. 시간은 tracemalloc 없이 잰 값이라 coordinator의 2.9 s보다 짧다). 128² 1.9 MiB, 256² 8.5 MiB, 1024² 고립 픽셀 262144개 0.28 s / 81.8 MiB — 픽셀 수에 비례한다. 큰 배열은 할당하지 않았다.
- **기존 fixture**: 합성 demo 전체 파이프라인(train → embed → analyze)에서 같은 입력으로 HEAD `edit_candidates`와 후보를 함께 호출해 지정서 JSON(후보 16개)과 표(25행)가 동일함을 확인. demo 질의 hotspot 36개에는 평탄한 다중 성분이 없다.

## 4. 유지보수성 (사용자 명시 요구)

- 충족(실행 스캔 + 읽기): 지역 모듈의 쉼표 뒤 공백 누락 0, T6 대상 8개 파일 120자 초과 0, 가나/한자 0, plot 위치 숫자는 이름 있는 상수와 한국어 이유, 중복 3종 제거(`scalar_observation`, `geometry_um`, `ordering_value`), 모듈 상단 한국어 docstring(책임·하지 않는 일·수정 위치)과 구획 주석, WHY 주석(재팽창 금지·보존 gate·frame 일치·이중 합산·결측≠0·성분별 mask를 쌓지 않는 이유) 유지.
- `docs/regional_analysis.md`의 "변경 목적 → 모듈/함수" 표에 적힌 함수·클래스 이름 17개는 모두 실제 모듈에 있다(import해서 확인). 빠진 행은 N-e.
- 미흡한 부분은 F4(기계적 줄바꿈, 연산자·콜론 공백)와 N-f(밑줄 인자·private import)다.

## 발견 사항

### Blocking
없음.

### Should-fix

**F1. 원점·pitch·shape가 모두 맞는 manifest 원천도 `frame_id`가 `local:<sample_id>`가 아니면 전부 차단된다**
- 위치: `src/simcompare/adapters/regional.py:91–93`. `ImageInput`에는 `frame_id`가 없어서 기대값을 `"local:" + sample_id`로 만들어 비교한다. manifest에서 `frame_id`는 필수 문자열이지만(`:267–275`) 적재 때는 형식을 검사하지 않는다.
- 반례(probe 8, S1-b): 이미지와 격자가 완전히 같은 원천에 `frame_id="unit-frame"` 또는 `"LOCAL:A"` → 모든 지역 행 `incompatible_source_frame`, union null. `None`과 `local:A`만 통과한다.
- 계약: T4 S1은 "frame_id **where declared**"를 대조하라고 했다. 이미지 쪽에는 선언이 없으므로 이 차단은 지시서가 조심하라고 한 "legitimate source를 잘못 막는 경우"에 해당한다. blocking이 아닌 이유: 결과가 틀린 수치가 아니라 unavailable이고, 규약이 `docs/regional_analysis.md:101–107`에 적혀 있으며 demo·테스트 manifest는 규약을 따른다.
- 권고: 이미지에 선언된 frame_id가 없으면 frame_id 비교를 건너뛰고 기하(origin·pitch·shape)만 대조하거나, 규약 위반을 manifest 적재 시점에 `DataContractError`로 알린다.

**F2. frame 불일치가 전체 원천 요약과 run_record 집계에 반영되지 않는다**
- 위치: `src/simcompare/regional.py:139–148`은 frame 검사 전의 원천으로 gate를 다시 계산한다. `regional_report.py:394–398`과 run_record(`regional.py:177–187`)가 이 목록을 센다. frame 검사는 `regional_comparison.py:446–448`에서만 한다.
- 반례(실행): 원천 origin만 (1,0)으로 바꾼 sample 하나 → HTML 머리말 "정량 원천 available 1 · unavailable 0", run_record `source_unavailable=0`, `unavailable_reasons={}`인데 그 원천의 지역 행과 union은 전부 null이다.
- 계약: request "provenance and missing/unavailable explanations", 계획 3.4 "unavailable 이유 집계". 수치는 틀리지 않으므로 blocking이 아니다.
- 권고: frame 사유를 `source_validations`에도 넣거나, 머리말·run_record 집계 이름을 "보존 gate 통과 수"로 한정하고 frame 차단 수를 따로 센다.

**F3. 정수 0/255 NPY hotspot 자료로 수정 전에 학습한 체크포인트를 analyze가 그대로 받아들인다**
- 위치: `src/simcompare/pipeline.py:93–118`의 학습 fingerprint는 입력 파일 바이트와 설정만 해시한다. `adapters/example.py:131–139`의 복호 규칙이 바뀌어도 값이 같다.
- 시나리오: HEAD 코드로 정수 0/255 NPY hotspot 자료를 학습(입력 최댓값 255) → 이 후보로 `analyze` → fingerprint 일치로 통과하지만 hotspot 입력은 0/1이라 학습 때와 255배 다르다. 값이 달라지는 것은 probe 9로 실행 확인, fingerprint가 못 잡는 것은 읽기로 확인했다. 재학습이 필요하다는 안내는 문서에 없다(`docs/data_contract.md:54–55`는 수치 변경 범위만 적는다).
- 계약: request "Existing AE/search pipeline preserved", T5 "training-relevant behaviour changes only for the B1 defect (state this in the docs)". 해당 자료는 exporter가 만들지 않고(exporter는 PNG 또는 float32 0/1 NPY) 저장소의 합성 자료에도 없어서 blocking으로 올리지 않았다.
- 권고: 최소한 문서에 "정수 0/255 NPY hotspot으로 학습한 체크포인트는 재학습" 한 줄을 적는다. 더 안전하게는 입력 복호 계약 버전을 학습 fingerprint에 넣는다.

**F4. S6 잔여 — 줄 길이를 맞추려고 식 중간을 기계적으로 끊은 곳이 많고, 연산자·콜론 공백이 저장소 스타일과 다르다 (사용자 명시 요구)**
- 식 중간 줄바꿈: `regional_report.py:291–293`(`dict[str,` / `dict]`로 갈라진 타입 표기), `:115–122`, `:343–347`; `regional_measurement.py:116–117`, `:139–140`, `:164–165`; `regional.py:54–56`, `:62–64`, `:153–156`; `regional_comparison.py:480–483`; `adapters/regional.py:200–201`, `:330–331`. 들여쓰기가 문법 구조와 맞지 않아 나중에 고치는 사람이 읽기 어렵다.
- 공백(토큰 스캔, 기존 `pipeline.py`/`neighborhood.py`/`plots.py`/`cards.py`는 전부 0): 이항 연산자 공백 누락 `regional_plots.py` 49, `regions.py` 20, `regional_comparison.py` 17; dict `키:값` 공백 누락 `regional.py` 12, `regional_report.py` 9, `scripts/regional_demo.py` 17; `lambda b:b.x` 형태 `regional.py` 5.
- `regional_measurement.py:219`는 129자(T6 줄바꿈 대상 목록에 이 파일이 빠져 있었다). `regional.py:38–40`은 빈 줄 3개.
- 동작에는 영향이 없다. 쉼표·120자·상수화·중복 제거라는 T4 S6의 명시 항목은 충족했다. 사용자 요구가 "나중에 수정하기 쉽도록"이므로 should-fix로 둔다.

### Nit

- N-a. 이미지가 있는 sample에서 원천이 anisotropic이거나 원점이 None이면 사유가 `unsupported_grid`/`missing_grid` 대신 `incompatible_source_frame`으로 나온다(`adapters/regional.py:94–98`, probe 8 S1-e). 거부는 유지되지만 사유가 덜 정확하다.
- N-b. 창이 없을 때의 HTML 문구가 비교 단위로 하나만 나온다(`regional_report.py:311–317`). 1 um은 빈 영상, 2 um은 배열 없음인 sample에서 "관측된 빈 support · 선택 union은 0"만 표시된다(표의 2 um 행은 올바르게 `—`/`location_unavailable`).
- N-c. `_bounds_um`(`variants.py:70–80`)에 Python int를 넣는 다중 성분 경로와 NumPy int를 넣는 `_bbox_um` 경로의 반올림이 pitch가 소수 7자리 이상일 때 1e-6 um 달라진다(소수 7자리 pitch 두 종 0.0131835·0.1234565에서 15만 건 중 약 46%, 소수 6자리 이하 pitch와 1/3에서는 각 15만 건 중 0건). T6 "same rounding as `_bbox_um`"에 엄밀히는 못 미치지만 1 pm 수준이다.
- N-d. 미등록 pair에서 양쪽 지름 집합이 다르면 own `RegionSet.support_statuses`에 상대 쪽 지름이 `missing`으로 추가된다(`regional_comparison.py:411, 436`의 support 재사용). 창·수치는 같고 JSON의 상태 목록만 v1과 다르다. demo에서는 발생하지 않는다.
- N-e. 문서: `docs/regional_analysis.md:61–62` 문장이 깨져 있다("지름은 단위 환산 후 공통 `diameter_key`는 …"). 같은 문서의 "변경 목적 → 모듈/함수" 표(`:208–221`)에 적힌 함수는 전부 실제로 있지만 `source_frame_reason`(frame 일치 정책), `adapters.example.read_image`(binary 복호), `variants._roi` 행이 없다. `docs/open_questions.md:15`의 "대응표 연결 미구현"은 이제 사실과 다르다(T5 허용 범위 밖이라 그대로 둔 것).
- N-f. 캐시를 위해 공개 함수에 밑줄 인자(`build_regions(_supports=)`, `summarize_source(_cached=)`)가 생기고 `_measure_union_validated`를 다른 모듈이 import한다(`regional_comparison.py:18`). `regional_schema.py:9–14`의 import 순서도 섞여 있다.
- N-g. 범례가 설정된 자료의 NPY hotspot은 범위 검사와 0/255 복호를 거치지 않는다(범례 경로 불변이라는 계약대로). 숫자가 아닌 dtype의 NPY는 `DataContractError`가 아니라 `UFuncTypeError`가 난다(HEAD도 계약 밖 `ValueError`였다).
- N-h. 테스트: `test_regional_results_do_not_depend_on_model_image_size`는 여전히 `analyze_regions`를 직접 불러 구조상 통과한다. `test_malformed_export_provenance_is_contract_error`(`tests/test_regional_review_fixes.py:173–183`)는 빈 목록과 예외를 모두 허용한다. `test_regional_source_style_has_bounded_lines`는 6개 모듈의 줄 길이만 본다.
- N-i. binary 단일 성분도 `_component_bounds`로 전체를 한 번 훑은 뒤 `_flood`를 다시 돈다. 512² 전체가 한 성분일 때 0.43 s(HEAD 0.25 s)이며 지름·slot마다 반복된다.
- N-j. S4 잔여: 512²×32지름에서 출력 합계 33 MB의 대부분은 행마다 반복되는 provenance가 든 JSON/CSV다.
- N-k. `checkpoint.md`의 T6 행이 아직 "진행 중"이다(coordinator 문서이며 후보 밖).

## 5. 테스트 평가와 남는 공백

새 테스트는 대부분 실제 반례를 assert한다: frame 3종·shape 불일치, nm 지름 연결과 혼합 단위 중복, 위치 불명과 관측 0의 수치·HTML 구분, 256²×8지름 HTML 3 MB 상한, support/gate 호출 횟수, binary 인코딩 3종의 배열 동일성, 범위 밖 NPY 거부, audit 반례(작은 성분이 raster상 먼저), 128² 체커보드 16 MiB 상한(성분별 mask 방식이면 128 MiB), 무작위 기준 구현 비교, `_contains_prefix` 경계.

테스트가 없는 동작(내 probe로는 통과):
- exporter·직접 run 원천이 `compare_pair`의 frame 검사를 통과하는지(양성 테스트는 적재 시점 gate만 본다. 직접 run 테스트는 pyarrow로 전부 skip).
- frame이 nm로 표현된 정상 원천, `frame_id` 규약 위반(F1), 요약 집계 불일치(F2).
- 지름별로 빈 영상과 결측이 섞인 경우(N-b).
- 정수 0/255 NPY의 다른 dtype(int64, bool), 범례 + NPY 불변.
- 다중 성분 binary에서 큰 지름에 맞는 성분이 없어 다음 지름으로 넘어가는 경로, 소수 7자리 pitch(N-c).

## 남는 한계 (계약상 허용)

- 현 exporter는 nominal map만 내보낸다. mean ROI는 직접 run 또는 manifest가 필요하다.
- local delta는 명시 대응 + translation 등록 + 같은 등방 lattice에서만 나온다.
- hotspot 표시 영상과 정량 원천의 해상도가 다른 자료는 `incompatible_source_frame`으로 막힌다(문서화된 제한).
- B3(비-exporter binary 자료의 정량 원천 선언), B4(무차원 CA의 지역 기여)는 사용자 결정이 필요하다.
- 검증은 합성 fixture와 합성 생산자 사례뿐이다. 시각 검증과 스크린샷은 이 리뷰에 포함되지 않는다.
