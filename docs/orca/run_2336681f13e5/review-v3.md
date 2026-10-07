# 독립 코드 재리뷰 v3 — review-v2 F1–F4 최종 수정 (candidate v3)

- 리뷰어: 새 세션의 독립 리뷰어(Claude `claude-opus-5-5`, effort high). 이 변경을 작성·계획하지 않았고 coordinator가 아니다. 하위 worker를 띄우지 않았고 코드를 고치지 않았다.
- Run `run_2336681f13e5`, Task `task_87fdce91694a`, Dispatch `ctx_e82666388a86`, 2026-10-01.
- 대상: `/Users/jonghyeonlee/git/particle-sim-compare`, branch `record-cno-open-fab-decisions`, HEAD `b507da96554baa658171dcae61a99342c9ec36e3`, 미커밋 작업 트리.
- 계약: `review-v2.md`, `task-T7-review2-fixes.spec.md`, `checkpoint.md`(T7 행과 결정 기록), `outputs/regional-demo/t7-review-fixes.md`, `request.txt`, `plan-v1.md`, `plan-review-v1.md`, `audit-binary-hotspot-contract.md`, `review-v1.md`, `task-T4/T5/T6` spec을 전부 읽었다.

## 판정: **PASS** (blocking 0건) — 이 후보(`candidate-v3.sha256`)에만 유효

v2의 should-fix 네 건 중 F1, F2, F3은 수정되었고 F4는 대부분 수정되었으나 일부가 남았다.
핵심 수치는 내가 새로 쓴 probe로 다시 확인했고 틀린 수치를 만들지 못했다.
서식 단계 전후의 동작 차이도 찾지 못했다.
새 should-fix는 세 건이다(S1 frame 검사 공백, S2 단위 표현이 다른 pitch의 비교 차단, S3 F4 잔여).
**S1은 실제 manifest 자료를 쓰기 전에 처리하기를 권한다.** S1과 S2는 v2에도 있던 동작이며 v3에서 생긴 회귀가 아니다.
이 PASS는 합성 자료와 합성 생산자 사례에 대한 코드 검증이며 실제 정확도·수율 주장이 아니다.
v1, v2 PASS는 재사용하지 않았다. 브라우저 시각 검증은 하지 않았다(coordinator 몫).

## 후보 해시 검증

| 시점 | 확인 | 결과 |
| --- | --- | --- |
| 시작 | `shasum -a 256 -c docs/orca/run_2336681f13e5/candidate-v3.sha256` | 35개 전부 OK |
| 시작 | manifest 자체 / `candidate-v3.tracked.diff` / `git diff \| shasum` | `e3a07479…7060` / `299a087b…9e44` / `299a087b…9e44` — checkpoint 기록과 일치 |
| 종료(이 문서 작성 직전과 직후) | 같은 `shasum -c`, `git diff` 해시, HEAD, `git status --short` 36행 | 35개 전부 OK, 시작과 동일 |

- v2 대비 변경: 해시 목록을 비교하면 16개 파일이 다르고 19개는 v2와 같다(`example.py`, `pipeline.py`, `schema.py`, `config.py`, `particle_sim_run.py`, 나머지 테스트 포함).
- 기존 미커밋 hunk: `baseline-preexisting.diff`(sha256 `a00a5706…a8b3`)를 파일별로 나눠 `git apply --check --reverse`로 확인했다. `adapters/particle_sim_run.py`, `tests/test_particle_sim_export_integration.py`의 선행 hunk는 그대로다.
- `docs/open_questions.md`: 새 문구를 원래 문구("대응표 연결 미구현.")로 되돌린 내용의 sha256이 `candidate-v2.sha256`의 값과 같다. v2 이후 바뀐 곳은 허가된 그 한 구절뿐이다.
- 저장소에서 내가 만든 파일은 이 문서뿐이다. 형제 저장소 `particle-sim`의 `git status` 해시와 HEAD는 실행 전후가 같다. git 변경·설치·네트워크 없음.

## 실행한 명령과 결과

probe는 모두 `/private/tmp/claude-501/review3-run_2336681f13e5/`에 있다. `p1`–`p6`, `p9`–`p11`은 새로 썼고, `p7`, `p8`은 이전 리뷰어의 probe 9, 5, 5b, 5c, 7을 다시 돌린 것이다.

| 명령 | 결과 |
| --- | --- |
| `.venv/bin/python -m pytest -p no:cacheprovider -o addopts="" -q -rs` | **386 passed, 57 skipped, 1 warning** (coordinator 기록과 동일). skip은 pyarrow 54, 생산자 env 3이며 pass가 아니다 |
| 생산자 env 지정, `tests/test_particle_sim_export_integration.py tests/test_particle_sim_export_positive.py -rs -v` | **2 passed, 1 skipped**. skip은 `test_export_load_contract`(pyarrow 없음) |
| `scripts/regional_demo.py --output-dir <tmp>` | exit 0, HTML 245 KB, SVG 34개 |
| `p1_core.py` (핵심 수치 47항목) | 47/47 통과 |
| `p2_frame.py` (F1, F2, N-a, N-b, frame) | 27항목 중 24 통과. 나머지 3개는 내가 건 "사유 문자열까지 일치" 조건이며 nit N1로 정리했다 |
| `p3_demo_diff.py` (v2 리뷰어 산출물·저장소 산출물과 demo 비교) | 아래 4번 참고 |
| `p4_style.py` (토큰 기반 스타일 스캔), `git diff --check`, 가나/한자 스캔 | 아래 4번 참고. `git diff --check` 통과, 가나/한자 0 |
| `p5_roi.py` (N-c, `_roi`) | 아래 5, 6번 참고 |
| `p6_e2e.py` (HEAD 코드와 후보 코드로 demo 전체 파이프라인 4회) | 아래 6번 참고 |
| `p7` (read_image, HEAD 대비), `p8` (생산자 인터프리터로 실제 nominal/probability run과 export) | 아래 6번 참고 |
| `p9_misc.py` (등록·관계·CNO·escape·demo CSV 기대값·시간), `p10`, `p11` | 20/20 통과, 나머지는 측정값 |

## 1. 핵심 수치 재검증 (전부 실행)

서식 단계가 모든 지역 모듈을 건드렸으므로 이전 probe를 쓰지 않고 새로 확인했다.

| 항목 | 결과 |
| --- | --- |
| 전체 영역 보존 | 무작위 50건(binary·density, pitch 5종, 0이 아닌 원점): 전체 창 적분 = 원천 CA. 격자보다 큰 창도 같은 값. 틀린 total은 `conservation_failed`, ROI null(재스케일 없음) |
| fractional clipping, union 1회 | 무작위 300건(valid hole 포함, 창 1–4개)이 독립 구현(절단 좌표로 나눈 조각별 합)과 최대 오차 3.6e-15. 9 + 9 − 4 = 14. 같은 창 3번은 9 |
| 단위 환산 | um 격자, nm 격자 + `nm^2` total, mm 격자 + `mm^2` total, nm 창이 모두 손계산 0.625와 일치. total 단위가 길이면 `invalid_total_ca` |
| 결측과 0 | zero total은 ROI 0, share null(`zero_total`). valid hole만 덮는 창·영역 밖 창은 `empty_valid_area`(0 아님). map/total 결측, NaN density는 각각 사유와 null |
| 100→80, ROI 20→30 | `delta_total=-20`, `delta_roi=+10`, `local_ca_increased@1um`만 붙고 `total_ca_increased`는 없음. 2 um의 40→20(−20)도 남는다. demo CSV에서도 같은 값(100/80/−20, 20/30/+10, 80/50/−30) |
| 지름 간 고정 창 | 요청 창이 모든 지름·양쪽에서 동일. 창 높이는 양쪽·전 지름 support의 합집합(4). demo CSV의 region ID 집합이 세 지름에서 같다 |
| 결정적 ID | pair 방향을 뒤집어도 ID 동일, delta 부호만 반전. hotspot·원천 순서를 뒤집어도 행이 같다. 입력 배열·CA·원천 불변 |
| 재팽창 없음 | 지름 50 µm에서도 창은 셀 하나. 표시 margin 7 µm는 표시 bounds만 바꾼다 |
| 원천 typing | nominal kind + mean mode, nominal kind + mean scalar, mean kind + nominal scalar는 합이 같아도 `source_mode_mismatch`. 비이진 nominal은 `invalid_nominal_values`. 계약 ID 없음은 `unverified_source_semantics` |
| legacy 무차원 | 원천이 있어도 ROI null, `unit_total_ca_um2` null, 원 값 0.02는 보존, 사유 `legacy_ca_not_area`. 원천이 없어도 같은 사유 |
| frame·등록 | 원점이 (100,200)과 (−7,3)인 두 sample을 translation으로 등록: 공통 창 하나, ROI 1.5→2.25, delta 0.75. subpixel translation과 다른 pitch는 local delta null + own 값 유지. 등록이 없으면 own 창 두 벌, local delta null, total delta는 제공 |
| 0 기준, 지름 결측 | 기준 0은 절대 delta 2, 상대 null, `new_risk`. reference에 없는 지름은 delta null(`missing_size`), query own 3.0 유지 |
| 창 밖 잔여 | demo의 available 원천 14개 모두 union + 창 밖 = total |

## 2. 지정 항목별 상태

"실행"은 내가 직접 돌려 확인한 것, "읽기"는 코드·문서를 읽어 확인한 것이다.

| 항목 | 상태 | 확인 방법과 결과 |
| --- | --- | --- |
| F1 frame_id만 다른 원천 | **fixed** | 실행(p2, 실제 manifest 적재 → `write_regional_analysis`): `frame_id`가 `unit-frame`, `LOCAL:S`, `local:other-sample`, `wafer-global-7`인 원천이 모두 측정되고(ROI 1.0, 창 밖 0) provenance에 이름이 남는다. nm·mm로 표현한 같은 격자도 측정된다. 빈 문자열은 적재 오류 |
| F1 여전히 차단 | **유지** | 실행(p2): 원점 이동(x, y, 2e-9 um), pitch 차이(2배, 1e-8), shape 차이(열 추가, 전치), nm 값을 um로 선언한 경우 모두 ROI null, union·창 밖 null, 지역 행 사유 `incompatible_source_frame`. 원점 차이 5e-10 um는 허용오차 안이라 통과. 등록 pair 안에서 한쪽이 차단되면 local delta null, 상대 쪽 own 값 유지 |
| N-a 원천 격자 사유 | **fixed** | 실행(p2): anisotropic → `unsupported_grid`, 원점 None·pitch None → `missing_grid`, 음수 pitch·미지원 단위 → `invalid_grid`. 지역 행, source 요약, `source_validations`, run_record가 같은 사유 |
| F2 요약 일치 | **fixed, 사유 우선순위만 nit** | 실행(p2): 원점만 다른 원천 → `source_validations` unavailable/`incompatible_source_frame`, run_record `source_unavailable=1`·`conservation_available=0`·`unavailable_reasons={incompatible_source_frame: 1}`, HTML 머리말 "available 0 · unavailable 1". 상태와 개수는 모든 차단 사례에서 지역 행과 맞는다. 두 문제가 겹칠 때 사유 문자열이 다른 점은 N1 |
| N-b 지름별 문구 | **fixed** | 실행(p2): 1 µm 빈 영상, 2 µm 배열 없음, 3 µm NaN인 sample에서 문구가 지름별로 따로 나온다(1 µm "관측된 빈 support", 2·3 µm "위치 자료 미확인"). 요약도 1 µm (0, 전체, available), 2·3 µm (null, null, `location_unavailable`). 격자 불일치로 창이 없을 때의 문구는 N2 |
| F3 재학습 안내 | **fixed(문서)** | 읽기: `docs/data_contract.md:55–58`에 재학습 의무와 이유(fingerprint는 파일 바이트와 설정만 해시)가 적혀 있다. 실행(p10): `_training_fingerprint` 함수 소스가 HEAD와 같다. 같은 설정 파일에 대해 HEAD 코드와 후보 코드가 같은 fingerprint를 낸다. 정수 0/255 NPY의 값이 HEAD와 달라지는 것은 p7로 확인(HEAD 합 1275, 후보 합 5). 문서 내용은 사실과 맞다. 문구 nit은 N7 |
| F4 스타일 | **대부분 fixed, 일부 잔여(S3)** | 실행(p4): 지역 모듈 9개 + demo script에서 120자 초과 0(최대 120), 쉼표 뒤 공백 누락 0, dict `키:값` 공백 누락 0, `lambda b:b.x` 형태 0, 빈 줄 3개 연속 0, 이항 연산자 공백 누락 1(`regional_report.py:421`). `regional_schema.py` import 순서 정리됨. 기계적 줄바꿈은 남아 있다 |
| F4 동작 불변 | **확인** | 실행(p3): 후보 v3의 demo 산출물을 v2 리뷰어가 후보 v2로 만든 산출물과 비교. 비교 CSV는 바이트 동일. 측정 CSV, summary JSON(fingerprint 제외), HTML(fingerprint 제외), SVG 34개는 출력 디렉터리 절대 경로를 치환하면 전부 동일. 저장소의 `outputs/regional-demo/`와도 같은 결과. worker의 서식 전 AST dump(`/tmp/t7-format-before.json`)와 현재 소스를 대조: 8개 파일 완전 일치, 2개(`regional_measurement.py`, `adapters/regional.py`)는 docstring 문자열만 다르다. worker가 남긴 T7 전 CSV 사본과 저장소 CSV의 sha256도 같다 |
| N-c 반올림 | **fixed** | 실행(p5): `_bounds_um`에 Python int, `np.int64`, `np.int32`, `np.intp`를 넣은 672000건이 같은 bbox의 `_bbox_um`과 비트 단위로 같다(pitch 14종, 소수 7자리 5종 포함). v2 방식(Python int 그대로)이라면 168000건 중 11521건이 달랐다. 기존 `_bbox_um` 경로는 HEAD와 56000건 모두 값·원소 타입이 같다 |
| N-e 문서 | **fixed** | 읽기: `docs/regional_analysis.md:62–64` 문장 정상. 표에 `source_frame_reason`, `read_image`, `_roi` 행 추가(`:215–217`). 실행(p10): 표에 적힌 이름 20개가 모두 실제 모듈에 있다. `open_questions.md`는 위 해시 확인 참고 |
| N-h 테스트 | **fixed** | 읽기: `test_regional_results_do_not_depend_on_model_image_size`는 측정 진입 함수에 들어오는 배열 shape가 원본 (12, 30)이고 모델 크기 (8, 8)/(128, 128)이 아님을 assert한다. `test_malformed_export_provenance_is_contract_error`는 세 경우 모두 `DataContractError` 하나만 허용한다. 남는 점은 N4 |
| N-f | **미수정(보류)** | 읽기: checkpoint에 보류로 기록됨. `build_regions(_supports=)`, `summarize_source(_cached=)`, `_measure_union_validated`의 모듈 간 import가 그대로다(N3) |

## 3. 회귀 확인

| 항목 | 결과 |
| --- | --- |
| legacy analyze 산출물 | 실행(p6): HEAD 코드(`git archive`로 tmp에 푼 것)와 후보 코드로 demo 전체 파이프라인(demo-data → train → embed → analyze)을 각각 실행. HEAD가 쓰는 23개 파일 중 21개가 바이트 동일(neighbors, ranking, clusters, shape_features, neighborhood_*, edit_candidates.json/csv, variant_comparison, review_cards, 그림 포함). 다른 두 개는 `model.pt`와 `run_record.json`이다(출력 경로가 달라 fingerprint와 기록 시각이 다르다). `enabled=false`이면 추가 파일 0개 |
| 학습 fingerprint | 실행: 위 F3 참고. HEAD와 같다 |
| 편집 후보·`_roi` (T5, T6) | 실행(p5): 단일 성분·graded·다지름·평탄한 비이진 6000건이 HEAD와 값·타입까지 동일. 다중 성분 binary 1500건이 독립 기준 구현(union-find 4-연결 → 면적 내림차순 → 첫 raster 픽셀 → pad한 bbox가 편집 픽셀을 포함하는 첫 성분)과 전부 일치, 그중 718건은 HEAD와 다르다(T5가 의도한 변경). 큰 지름에 맞는 성분이 없으면 다음 지름으로, 어느 지름에도 없으면 `closest_zone`으로 가는 것을 지정 사례로 확인(p10). 256² 체커보드 1.2 s, tracemalloc peak 8.7 MiB, 입력 불변 |
| binary hotspot 계약 (T5) | 실행(p7, HEAD의 `read_image`와 비교): PNG 0/255, NPY 0/1, 정수 NPY 0/255가 같은 0/1 배열과 `binary`. 회색·비이진 [0,1]은 값 그대로 `processed_intensity`. 범위 밖 NPY는 파일 이름이 든 `DataContractError`. layout과 범례 경로는 HEAD와 같다. `adapters/example.py`는 v2와 해시가 같다 |
| 생산자 원천 | 실행(p8, 생산자 인터프리터): 실제 nominal run과 probability run을 만들어 확인. 직접 nominal 36개, 직접 probability + `--ca mean` 36개 원천 available, probability + `--ca nominal` 36개는 `source_mode_mismatch`. exporter → `load_dataset(real)` 원천 36개 available, 잔차 0. `compare_pair`에서 frame 차단 0건, 창 밖 잔여 0. 이 export로 리포트 작성과 run_record 직렬화 성공(NaN 0) |
| CNO 제외 | 실행: `failure_type="contact_not_open"`은 `compare_pair`가 `DataContractError`. 읽기: `FAILURE_TYPES`에 없다 |
| Fab·수율·Tree 없음 | 실행(grep): 지역 모듈과 demo script에 yield/수율/fab weight/SHAP/tree 모델 관련 식별자 0. `caa`, `model`, `retrieval`, `neighborhood`, `variants`, `pair_geometry`, torch, sklearn import 0. 원천 목록을 넘겨 합산하려는 호출은 `DataContractError` |
| 리포트 안전성 | 실행(p9): `frame_id`와 sample_id에 `<script>…"><img onerror>`를 넣어도 측정은 되고 HTML 파서 기준 `script`/`img` 태그 0, `on*` 속성 0, 외부 URL 0. SVG 전부 XML 파싱. JSON에 NaN/Infinity 0 |
| 크기·시간 | 실행(p9): 256²×8지름 분석 0.37 s + 리포트 0.37 s, HTML 1.77 MB. 512²×8지름 1.09 s + 0.40 s, 1.78 MB. v2 측정과 같은 수준 |

## 4. 유지보수성 (사용자 명시 요구)

- 충족(실행 스캔 + 읽기): 줄 길이, 쉼표·연산자·콜론·lambda 공백, 연속 빈 줄, 가나/한자 0, 모듈 상단 한국어 docstring(책임·하지 않는 일·수정 위치), 구획 주석, WHY 주석, 이름 있는 표시 상수, 문서의 "변경 목적 → 모듈/함수" 표.
- 기준으로 삼은 기존 모듈(`pipeline.py`, `neighborhood.py`, `cards.py`, `retrieval.py`, `pair_geometry.py`)은 같은 스캔에서 연산자 공백 누락 0, 불규칙 이어쓰기 들여쓰기 0이다.
- 미흡: S3(구조와 맞지 않는 줄바꿈·들여쓰기), N3(밑줄 인자·private import), N5(import 순서).

## 발견 사항

### Blocking
없음.

### Should-fix

**S1. 같은 지름의 hotspot 이미지가 없는 원천은 frame 검사를 받지 않는다 (실제 manifest 자료 전에 권고)**
- 위치: `src/simcompare/adapters/regional.py:90–94`는 지름이 같고 배열이 있는 이미지만 대조한다. 그런 이미지가 없으면 `None`을 돌려준다. 창은 다른 지름의 이미지에서도 만들어지고(`regions.py:109–129`), 이미지가 아예 없는 지름은 원천 support가 창에 들어간다(`regions.py:130–139`).
- 반례 1(실행, p2): 1 µm는 이미지와 원천이 같은 격자, 2 µm는 hotspot 배열이 None이고 원천 원점만 x로 0.5 µm 이동 → 2 µm 지역 행 `roi_ca_um2=0.5`(같은 격자였다면 1.0), 사유 없음, source 요약 union 1.0 / 창 밖 1.0 / available, `source_validations` available. 신호는 창 밖 잔여와 `location_status=missing`뿐이다.
- 반례 2(실행, p2): 2 µm hotspot 항목이 아예 없고 원천 원점이 0.5 µm 이동 → 창이 `[10, 11)`, `[12, 13)`에서 `[10, 11.5)`, `[12, 13.5)`로 넓어진다. 모든 지름의 지역 ID와 ROI 범위가 바뀌는데 불일치 신호는 없다.
- 계약: request "mismatch … registration explicitly unavailable rather than wrong number", T4 S1의 "before any ROI is measured from windows derived from the image". blocking이 아닌 이유: T4 S1의 조건은 "sample+diameter에 이미지와 원천이 모두 있을 때"이고 그 조건은 지켜진다. 값은 선언된 두 원점을 그대로 적용한 적분이다. v2에도 있던 동작이다. 자동 승격 경로(exporter·직접 run)는 이미지의 원점·pitch를 쓰므로 해당하지 않는다.
- 권고: 같은 지름 이미지가 없으면 그 sample의 다른 hotspot 이미지 격자와 대조하거나(real 모드에서는 한 bundle의 이미지 격자가 같다), 대조할 이미지가 없다는 사유를 요약에 남긴다. 문서 `docs/regional_analysis.md:101–107`에도 이 범위를 적는다.

**S2. 단위 표현이 다른 같은 pitch를 float `==`로 비교해 정상 pair의 delta가 모두 막힌다**
- 위치: `src/simcompare/regional_comparison.py:154`(domain 서명의 pitch), `:196`(`pa != pb`), `src/simcompare/regions.py:167`(`plane.pitch_um == pitch`).
- 반례(실행, p2·p11): 양쪽 hotspot pitch 0.7 um, query 원천만 `pixel_size=700`, `length_unit="nm"` → `convert_length(700, "nm")`은 `0.7000000000000001`. own ROI는 양쪽 다 측정되지만 total delta는 `incomparable_domain`, local delta는 `incompatible_comparison_grid`. query bundle 전체가 nm이면 등록 자체가 `incompatible_comparison_grid`. 1–2000 nm 정수 pitch 중 282개(14%)가 해당한다(50, 100, 250 nm는 통과, 9, 13, 700 nm는 차단).
- 계약: request "Honor units, pixel scale", review-v2 F1과 같은 종류("정상 원천을 잘못 막는 경우"). 틀린 수치가 아니라 unavailable이므로 blocking이 아니다. v2에도 있던 동작이지만, F1 수정으로 nm 표현 격자의 own 측정은 허용되고 pair 비교만 막히는 비대칭이 생겼다. 사유 문자열도 원인(단위 표현)을 알려 주지 않는다.
- 권고: pitch 비교를 `coordinate_atol_um`보다 훨씬 작은 명시 허용오차나 `diameter_key`와 같은 방식의 정규화 키로 통일한다. 그 전에는 한 비교 안의 manifest·bundle 길이 단위를 통일하라고 문서에 적는다.

**S3. F4 잔여 — 구조와 맞지 않는 줄바꿈·들여쓰기가 남아 있다 (사용자 명시 요구)**
- 식 중간에서 끊긴 곳: `regional_report.py:272–273`(조건식 안의 tuple이 `("confirmed_added",` / `"confirmed_removed")`로 갈라짐), `:193–200`과 `:213–220`(list 원소의 이어쓰기 들여쓰기가 원소마다 다름), `:314–317`.
- 인자 경계에서 끊었지만 들여쓰기가 여는 괄호나 문장 구조와 맞지 않는 곳: `regional_report.py:385–386`(def 인자가 본문과 같은 4칸), `:396–397`, `:401–402`, `:420–422`, `:425–426`, `:427–430`; `regional_comparison.py:234–235`, `:288–289`, `:466–469`; `regions.py:138–139`; `correspondence.py:122–123`.
- 연산자 공백: `regional_report.py:421`의 `'…<pre>'+`.
- T7 지시는 "no mechanical mid-expression wraps … structural indentation"이었다. 서식 단계에서 중간 변수를 새로 만들지 않았기 때문에(AST 동일성 조건) 긴 식이 그대로 남은 것으로 보인다. 동작에는 영향이 없다. 줄 길이·공백 항목은 충족했다.

### Nit

- N1. 문제가 두 개 겹치면 사유 우선순위가 요약과 지역 행에서 다르다. `regional.py:163–165`는 보존 gate가 통과한 원천에만 frame 사유를 넣고, `regional_comparison.py:453–455`는 frame 사유를 먼저 넣는다. 실행(p2): pitch나 shape가 달라 보존도 실패하는 원천은 지역 행·source 요약이 `incompatible_source_frame`, `source_validations`·run_record는 `conservation_failed`. `source_mode_mismatch` + 원점 이동도 같은 식으로 갈린다. 상태와 개수는 일치한다.
- N2. 한 sample 안에서 지름별 hotspot pitch가 달라 창이 없을 때(`incompatible_comparison_grid`) HTML 문구가 "위치 자료 미확인"으로 나온다(`regional_report.py:326–330`). 위치는 관측되었고 요약 사유는 올바르다(실행, p11).
- N3. N-f 보류분: 공개 함수의 밑줄 인자(`regions.py:249`, `regional_comparison.py:279`)와 `_measure_union_validated`의 모듈 간 import(`regional_comparison.py:18`). `tests/test_regional_pipeline.py`의 N-h 테스트도 이 private 이름을 monkeypatch한다.
- N4. N-h 테스트의 `first == second`는 여전히 구조상 참이다(`analyze_regions`가 image size를 받지 않는다). 새로 추가된 shape assert가 실제 검증이다. `test_regional_source_style_has_bounded_lines`는 줄 길이만 본다.
- N5. import 순서: `regional.py:25–27`(`.schema` 다음에 `.regional_schema`, `.regional_report`), `regional_report.py:18–21`. T7 지시는 `regional_schema.py`만 지정했다.
- N6. 리포트 fingerprint에 manifest 절대 경로(`adapters/regional.py:348`의 `manifest_path`)가 들어가 같은 입력을 다른 디렉터리에서 돌리면 값이 달라진다(실행, p3: 내용이 같은 두 demo의 fingerprint가 다름). v2에도 있던 동작이다.
- N7. `docs/data_contract.md:56`의 재학습 문장은 "범례 없는"이라는 한정이 없다. 범례가 설정된 자료의 NPY는 복호가 바뀌지 않았으므로 재학습 대상이 아니다. 문단 제목이 범례 없는 hotspot이라 오해 여지는 작고, 안전한 쪽으로 넓다.
- N8. review-v2의 N-d, N-g, N-i, N-j는 T7 범위 밖이라 그대로다. B3, B4는 미결정으로 문서화된 상태다.

## 5. 테스트 평가와 남는 공백

T7이 추가한 테스트는 실제 반례를 assert한다: manifest 적재를 거친 다른 이름 frame의 ROI 8과 provenance, anisotropic·원점 결측의 구체 사유, 원점 이동 원천의 run_record·머리말 집계, 지름별 문구 두 종류, 소수 7자리 pitch의 반올림 값.

테스트가 없는 동작(내 probe로는 통과하거나 위 발견 사항에 적었다):
- 같은 지름 이미지가 없는 원천의 frame(S1), 단위 표현이 다른 pitch의 pair 비교(S2).
- pitch·shape 불일치와 보존 실패가 겹칠 때의 사유(N1).
- nm·mm로 표현한 같은 격자의 측정, 허용오차 경계(5e-10 통과, 2e-9 차단).
- exporter·직접 run 원천이 `compare_pair`의 frame 검사를 통과하는지(직접 run 테스트는 pyarrow로 skip).
- 다중 성분 binary에서 다음 지름·`closest_zone`으로 넘어가는 경로.
- 줄 길이 외의 스타일 규칙.

## 남는 한계 (계약상 허용)

- 현 exporter는 nominal map만 내보낸다. mean ROI는 직접 run 또는 manifest가 필요하다.
- local delta는 명시 대응 + translation 등록 + 같은 등방 lattice에서만 나온다.
- hotspot 표시 영상과 정량 원천의 해상도가 다른 자료는 `incompatible_source_frame`으로 막힌다(문서화된 제한).
- B3(비-exporter binary 자료의 정량 원천 선언), B4(무차원 CA의 지역 기여)는 사용자 결정이 필요하다.
- 검증은 합성 fixture와 합성 생산자 사례뿐이다. 시각 검증과 스크린샷은 이 리뷰에 포함되지 않는다.
- pyarrow가 없어 skip된 54개 테스트와 `test_export_load_contract`는 이 환경에서 실행되지 않았다.
