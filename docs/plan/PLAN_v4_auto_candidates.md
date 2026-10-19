# PLAN v4 — 취약 영역 기반 자동 후보 → 대응 매칭 → 확정 → 국부 ΔCA

2026-10-19 사용자 결정을 구현 Task로 바꾼 계획이다. PLAN_v3의 `roi_correspondence_v1`, `compare-windows`, 불량 유형 조각은 **바꾸지 않고** 그 앞단(후보 생성·매칭·확정)을 더한다. 작성: coordinator. 이 문서만 쓴다. 구현, 학습, 실자료 검증, commit, push는 하지 않는다. 아래 명령·파일명 중 현재 없는 것은 후속 Task가 제공할 수락 인터페이스 제안이다.

## 1. 결정 (2026-10-19)

| 번호 | 결정 |
|---|---|
| E1 | 취약부는 각 제품의 `electrode-map`(5 µm) 영역에서 뽑는다. 수동 라벨링이 기본 경로가 아니다 |
| E2 | 변경부 대응은 template matching(NCC)으로 먼저 하고, **DL2 검색을 최종 메인**으로 올린다. 승격 조건은 같은 확정 쌍에서 DL2 위치 오차 < NCC 오차 |
| E3 | ROI는 두 축을 **구분해** 본다: 절대 CA가 큰 곳, CA 변화(Δ)가 큰 곳. 둘 다 취약부/변경부와 겹칠 가능성이 높지만 서로의 대용으로 쓰지 않고 교집합을 별도 표시한다 |
| E4 | 영역 수는 개수를 목표로 두고 k를 맞춘다. 제품당 50~100개가 출발점이며 실자료에서 `k=2,3,4`의 영역 수를 보고 사용자가 정한다. 코드에 기본 k를 넣지 않는다 |
| E5 | 취약 영역은 **양방향**(A→B, B→A)으로 뽑는다. B에서 새로 생긴 취약부를 놓치지 않기 위해서다 |
| E6 | NCC 점수가 낮거나 모호하면 **대응 없음**으로 둔다. threshold는 쓰지 않고, 점수순으로 정렬한 후보와 이미지를 사용자가 보고 쌍을 **확정**한다 |
| E7 | 유닛 크기는 일반적으로 50~120 µm. 측정 창 후보 10/20/30 µm는 유지하고, NCC 템플릿 창은 별도 인수(최소 유닛 50 µm보다 작게)로 둔다 |
| E8 | 중복 비용을 줄인다(§3 R1~R4). 안전장치를 둔다(§3 S1~S8) |
| E9 | 검수는 이번만 Codex `gpt-6-astra / high` 1회(사용자 지시). 평소 규칙(High-risk = Opus high)을 대체하며 기록한다 |

## 2. 흐름

```text
[제품 A, B 각각]  electrode-map --min-count k  ─►  취약 영역(양방향)
        │
T1  simcompare candidates   : 영역 → 질의 ROI 목록(중복 제거·깔때기 표)
        │
T2  psdl2 baseline_ncc batch: 질의별 상위 매칭 + 점수 + 이미지(질의/후보 crop) + 모호성 표시
        │
        ▼  사용자가 점수·이미지를 보고 쌍을 확정 (decisions CSV)
T3  simcompare confirm-pairs: 확정 쌍만 roi_correspondence_v1 로 변환 (+사이드카)
        │
        ▼  기존: compare-windows → local_delta.html / 통합 report
T4  절대 CA 목록 + Δ 목록 + 교집합 (통합 report 증보)
        │
단계 3 (이 계획 밖, 게이트): DL2 와 NCC 를 확정 쌍에서 비교 → 승격 판정
```

대응 파일은 출처(`template_match` 후보 → 사용자 확정)와 무관하게 `compare-windows`가 읽는다. **확정되지 않은 후보는 어떤 수치에도 들어가지 않는다.**

## 3. 비용과 안전장치

중복 비용(R) — 코드 근거: `psdl2/baseline_ncc.py`는 질의마다 후보 이미지의 `rfft2`와 `rfft2(image²)`를 다시 계산한다(`zncc_map`).

| 번호 | 내용 |
|---|---|
| R1 | 후보 이미지(역할·layer 합집합)의 FFT 두 개를 `(role, layer)`별로 **한 번만** 계산해 모든 질의가 공유한다. 가장 큰 절감이다 |
| R2 | 같은 템플릿(창 안 마스크 해시 동일)은 한 번만 매칭하고 결과를 질의 여럿에 연결한다 |
| R3 | 양방향 영역 중 물리 창이 겹치는 것은 질의를 하나로 합친다(합친 근거 ID 목록 보존). 병합 기준은 창 겹침 비율이며 값은 인수로 받는다(기본값 없음) |
| R4 | 상호 매칭: A 영역 a가 B의 b에 대응하고 B 영역 b'(≈b)가 다시 a로 대응하면 한 쌍으로 묶고 `mutual`로 표시한다. 한쪽 방향만 맞으면 `one_way`로 표시하고 버리지 않는다 |

안전장치(S):

| 번호 | 내용 |
|---|---|
| S1 | 깔때기 표: 영역 수 → 병합 후 질의 수 → 매칭 가능(구조 있음) → 후보 있음 → 사용자 확정 → ΔCA 계산됨. 단계마다 탈락 수와 사유 코드 |
| S2 | 자기 일치 점검: 제품 A를 A 자신에 매칭하면 모든 질의가 같은 위치에서 점수 ≈ 1이어야 한다. 아니면 중단(코드·좌표 오류 탐지) |
| S3 | 모호성 표시: 1위·2위 점수 차, 거의 같은 점수의 다중 peak(§4 주기성), 템플릿 구조 없음, 창 가장자리·valid 구멍 |
| S4 | 점수는 정렬·표시용이다. 어떤 수치 계산에도 넣지 않고 임계값 판정에도 쓰지 않는다 |
| S5 | 확정 변환은 제품·design·frame·pitch 일치, 실제 layer 존재, 이미 확정된 두 중심 중복을 검사한다. 불일치는 해당 쌍만 blocked, 파일 단위 불일치는 거부 |
| S6 | 모든 출력에 입력 파일 SHA256, 사용한 k·창·topk 인수, 코드 commit 기록. 재실행 시 결과 동일(결정적 정렬) |
| S7 | report에 매칭 출처(NCC/DL2)와 `mutual/one_way`, 확정 여부를 표시. 확정 안 된 후보는 별도 표로만 나온다 |
| S8 | 후보 수 상한은 인수로 명시할 때만 적용하고, 잘릴 때는 몇 개가 잘렸는지 깔때기 표에 남긴다. 침묵 절단 금지 |

## 4. 알려진 위험

- **주기성**: 제품 프레임은 2×2 유닛이라 같은 형상이 유닛 주기로 반복된다. NCC peak가 4개 가까이 거의 같은 점수로 나올 수 있다. topk는 반복 수 이상으로 받고(`repeats_per_frame` 활용), 사용자가 이미지를 보고 어느 반복인지 고른다. 자동으로 하나를 고르지 않는다.
- **큰 변경에 약함**: NCC는 형상이 많이 바뀐 곳에서 점수가 낮다. 낮은 점수가 "대응 없음"인지 "크게 바뀜"인지는 구분할 수 없다. 둘 다 사용자 확정 대상으로 남기되 `low_score`로 표시한다.
- **전극 커버리지**: `role_layer_unions`는 export가 참조한 전극만 덮는다. 영역에서 만든 export는 해당 layer의 전극을 모두 포함해야 한다. T1 수락 조건에 넣는다.
- **인과 방향**: CA 변화가 큰 곳이 형상 변경 때문이라는 보장은 없다. "변경부"로 이름 붙이지 않고 "Δ 큰 곳"과 "대응 형상이 다른 곳"의 교집합으로만 표시한다. 변경 구역 안팎 분해(2026-10-06 보류)는 하지 않는다.

## 5. Task

기준선(2026-10-19 개정 시점): C `rewrite/line-pixel-rate @ 1ac6441` (미커밋: 보고서 T1 변경 2개 파일, 문서 2개), DL2 `main @ ce98fd3`(clean). 편집 worker는 저장소당 하나. C에는 보고서 T2(`task_c6442f8d5c21`)가 대기 중이므로 C 편집 Task는 그 뒤에 시작하거나 사용자가 먼저 커밋한다.

| Task | 저장소 | 위험 | 내용 | 의존 |
|---|---|---|---|---|
| T0 | C (문서) | Trivial | `open_questions.md`에 E1~E9 표, `implementation_memory.json`에 `auto_candidates_2026_10_19` 절 | 없음 |
| T2 | DL2 | Normal | `psdl2.baseline_ncc` 배치 모드: 라벨 없이 export의 ready ROI 전체를 질의로 받음. R1 후보 FFT 공유, R2 템플릿 해시 중복 제거, 질의별 topk + 점수 + S3 표시, 질의/후보 crop PNG와 점수순 정렬된 HTML 시트(검토용, `file://`), S2 자기 일치 모드. 기존 라벨 기반 입력·출력 형식은 유지 | 없음 |
| T2b | DL2 | Normal | T2의 메모리 상한(§6): layer별 순차 처리, `--max-memory-mb`(필수, 기본값 없음), 추정치 초과 시 exit 2 | T2 |
| T2c | DL2 | Normal | T2b가 남긴 한계 해소: 다층 질의의 누적 점수 map(3400²×100이면 약 9 GB)을 `--max-memory-mb`에 맞춰 질의 chunk로 나눠 처리(chunk마다 FFT 재계산, 결과는 chunk 수와 무관하게 동일) | T2b |
| T1a | C | **High**(원천 schema 경계) | DL1 PNG 폴더(+선택 stack-order) → `review_assets_v1` 어댑터(§6). T1보다 먼저 | 보고서 T2 |
| T1b | C | **High**(pipeline·실자료 경로) | `export-review-rois`의 real_hotspot_v1 경로 연결: 지금 `pipeline.py:446-455`는 real_hotspot_v1 입력을 `electrode_polarity_unknown`/`source_layout_assets_unconfirmed`로 단락시키고 `asset_manifest`를 읽기 전에 반환한다. T1a가 만든 manifest를 실제 데이터에서 소비하도록 `configured_dataset`(compare-windows가 이미 사용)을 이 경로에 연결하고, 극성은 T1a의 출처별 계약을 근거로 한다. 합성 경로의 동작은 바뀌지 않아야 한다 | T1a |
| T1 | C | Normal | `simcompare candidates`: 두 제품의 electrode-map 영역(`--min-count`, 양방향) → 질의 ROI 목록(`candidate_queries_v1` JSON), R3 병합, S1 깔때기 표, S8 상한 | T1b |
| T3 | C | **High**(경계) | `simcompare confirm-pairs`: decisions CSV(`candidate_id, decision{accept,reject}, note`) + 매칭 결과 → `roi_correspondence_v1`(변환기 기존 규칙, schema 변경 없음) + 사이드카(후보 ID·점수·mutual/one_way). S5, S7. 확정되지 않은 후보는 대응 파일에 들어가지 않음 | T1, T2c |
| T4 | C | Normal | 통합 report 증보: 절대 CA 상위 N, Δ 상위 N(worse/better), 교집합 표시, 출처·확정 표시, 깔때기 표 | 보고서 T2, T3 |
| D1~D4 | DL2 (다른 세션 담당) | Normal | D1 `--device cuda\|cpu`·map_location·CUDA RNG, D2 디코딩 asset의 sha256 공유 캐시, D3 torch 고정 완화와 DL1 GPU venv 호환, D4 stderr JSON 진행 출력. **T2b 뒤**에 순서대로. D2는 실자료 학습 전 필수 | T2c |

T3의 확정 출처: schema를 바꾸지 않기 위해 `source.kind=manual`, `labelled_by=<사용자 ID 인수>`로 쓰고 NCC/DL2 provenance는 사이드카에 둔다. (PLAN_v3 계약은 `template_match`에 method/version을 요구하며 사용자 확정 필드가 없다. schema 확장은 하지 않는다.)

## 6. 실제 입력 경로와 자원 제약 (2026-10-19 개정, 사용자 지시를 DL2 세션이 전달)

**입력 경로 간극.** `export-review-rois`는 `asset_manifest`(`review_assets_v1`)만 읽는다. 이 형식은 합성 내부 형식이며 실제 원천 형식을 정한 것이 아니다(`docs/review_roi_contract.md:26`). real_hotspot importer는 hotspot과 case별 `1.png`/`2.png`만 다루고 전극 극성은 None이다(`adapters/real_hotspot.py:193,278`). 실제 전극 layout은 DL1 PNG 폴더 형식이다: `<root>/<model>/<electrode>/<n>-<layer>.png`, `<root>/<model>/cno/<n>_<layer>.png`, 0.1 µm/pixel, 전체 frame 공유, W와 H 모두 1000 px 초과, 검정 = 형상(DL1 `docs/data_contract.md` PNG source adapter v3). 따라서 실자료에서 영역 → 질의 ROI → 매칭으로 가는 `review_roi_v1` 경로는 아직 없고, T1a가 그 경로를 만든다.

**T1a — DL1 PNG 폴더 → `review_assets_v1` 어댑터 (C, High: 원천 schema 경계).**
- 입력: DL1 PNG 폴더와 선택 stack-order. 0.1 µm pitch와 전체 frame을 그대로 쓴다. threshold, resize 없음. DL1 png_source 규칙(파일 번호는 출처 기록용, CNT는 마스크 해시로 식별)과 일치시킨다.
- DL2 호환: DL2는 PNG 모드 1/L만 받는다(`psdl2/data.py:71`). RGB/P/RGBA 흑백 PNG는 1/L PNG나 NPY로 변환해 내보낸다. 원본 hash는 provenance에 남긴다.
- 극성: 검정 = 형상이면 `value_semantics: binary_zero_is_marked`. 0/1로 정규화했다면 `binary`. 둘 중 하나를 명시한다.
- `product_id`는 DL1 모델 폴더 이름과 같게 한다. DL2 search cohort가 DL1 product ID 목록이다(`psdl2/search.py:97`).
- 전극 사이 layer와 CNT 관계는 사용자 기준 `particle-sim-dl1/configs/stack-order.json`(2026-10-13)을 근거로 한다. 순서는 추정하지 않는다.
- frame 정합: 레이아웃 frame 원점은 `[0,0]`, hotspot frame은 `(50,50)` µm이고 hotspot이 각 축 1000 px 작다(중앙 crop). 어댑터가 두 frame의 물리 좌표 대응을 명시 검사한다.
- **T1a 범위 확정(2026-10-19, worker 질문 답)**: 어댑터는 필수 `--numeric-data-dir`(numeric_v2 manifest 폴더, 또는 real_hotspot_v1 + `--condition-assignment`)에서 identity·endpoints·failure_type·frame·`frame.valid`·input_hash를 **가져온다**(값을 만들어 내지 않음, 별도 case table 없음). `roi_export.py:51-54,93-104`가 asset shape = 데이터셋 frame shape, manifest frame = 데이터셋 frame, valid asset = `frame.valid`를 요구하므로 전극 이미지는 중앙 500 px 경계를 제외한 **정확한 부분 배열**로 쓴다(layout shape = frame shape + 1000, origin (50,50), pitch 0.1). 경계 50 µm 바깥 맥락은 DL2가 볼 수 없다는 한계를 문서에 적는다. real_hotspot_v1 경로를 `export-review-rois`가 소비하게 만드는 일은 T1a 밖의 **T1b**다.
- 같은 hash의 frame은 한 번만 디코딩해 공유하고 어댑터에도 메모리 상한을 둔다.
- **입력 범위(2026-10-19, 사용자 결정을 particle-sim-dl2-68 세션이 전달)**: DL1 PNG 폴더와 hotspot case의 전극 영상 `1.png`/`2.png`를 **둘 다** 지원한다. `1.png`의 극성은 **흰색 배경, 패턴 = 검정(검정 = 형상)**이다(사용자 답, 2026-10-19). DL1 PNG 폴더와 같은 극성이므로 `value_semantics`는 `binary_zero_is_marked`다. DL2 `read_asset`이 받는 값은 `binary`와 `binary_zero_is_marked`뿐이다(`psdl2/data.py:60`). `2.png`도 같다(사용자 답, 2026-10-19: 흰 배경·검정 패턴). 두 파일 모두 `binary_zero_is_marked`다. 이 극성은 코드의 묵시적 기본값이 아니라 **출처별 계약**으로 고정한다: 어댑터는 출처 종류(`dl1_png_folder`, `hotspot_case_electrode`)마다 `value_semantics`를 계약 표에 명시하고, 변환 기록(provenance)에 `polarity_basis: user_answer_2026_10_19`를 남긴다. 계약 표에 없는 출처 종류는 blocked(`electrode_polarity_unspecified`)다. 회색·유색 픽셀은 DL1과 같이 거부하고 threshold를 쓰지 않는다. 극성 반전이 필요한 소비자(DL2)에는 어댑터가 `binary` 0/1(1 = 형상)로 정규화한 사본을 내보내고 원본 hash를 provenance에 남긴다.

**자원 제약 (사용자 확정 2026-10-19).** 실행 PC는 GPU GTX/RTX 1660 Super 6GB, RAM 16GB.
- T2의 R1은 full-frame `rfft2` 두 개를 `(role, layer)`마다 보관한다. 3400² 기준 layer당 약 180 MB이며 layer 13개면 2 GB를 넘는다. **T2b**: layer 단위 순차 처리(해당 layer FFT를 쓰는 질의를 모두 돌린 뒤 해제), `--max-memory-mb`(필수, 기본값 없음), 추정 초과 시 침묵 절단 없이 exit 2, 추정치와 최대 layer 크기를 출력에 기록(S8과 같은 원칙). float32는 순위·점수가 변하지 않음을 시험으로 입증할 때만 허용한다.
- DL2 현재 코드는 ROI마다 full-frame mask를 따로 든다(`data.py:161-168`). 100 ROI × 2제품이면 약 11 GB로 추정되므로 DL2 D2(sha256 공유 캐시)는 실자료 학습 전 필수다. 이 편집은 DL2 `data.py`를 건드리므로 T2/T2b 뒤에 다른 세션이 맡는다.

- **DL2 D1~D4 완료(`08ed566`, 2026-10-19)**: `--device cpu|cuda`, 디코딩 mask의 ROI 간 공유, `--max-asset-memory-mb`(export 전체 거부), torch>=2.7.1과 GPU venv 스크립트, stderr 진행 출력. 남은 간극: `baseline_ncc`는 `load_export`를 기본값으로 호출하므로 asset 공유는 적용되지만 **asset RAM 상한 인수는 노출하지 않는다**(`--max-memory-mb`는 FFT용). 실자료 크기(3400², 100 ROI × 2제품)에서 필요하면 후속 Task **T2d**(`baseline_ncc`에 `--max-asset-memory-mb` 전달)로 추가한다. 실자료 규모를 보기 전에는 보류.

### 수락 기준(전체)

1. 합성 두 제품 fixture에서 T1→T2→(가짜 decisions)→T3→`compare-windows`가 끝까지 실행된다.
2. R1 효과: 질의 N개 처리 시간이 후보 FFT 공유 전보다 줄어든다(합성 fixture에서 측정해 기록, 정확도는 변하지 않고 결과 JSON이 동일).
3. S2 자기 일치 점검이 의도적으로 틀린 좌표에서 실패한다.
4. 확정되지 않은 후보가 `local_delta` 수치에 들어가지 않는다는 반례 시험.
5. 폐쇄망 수락(사용자): 실제 두 제품에서 k=2,3,4 영역 수 확인 → 깔때기 표 검토 → 점수·이미지 보고 확정 → `compare-windows` 결과가 설계자 판단과 일치하는지 기록.

### 검수

C 변경분(T1, T3, T4)과 DL2 변경분(T2)을 Codex `gpt-6-astra / high` 읽기 전용으로 **1회** 검수한다(사용자 지시, 이번에만). brief에는 Task 계약, 정확한 diff 또는 commit, 검증 결과, 관련 호출처와 시험만 준다. 이 검수는 평소 High-risk 요건(Opus high)을 대체하는 사용자 예외이며 체크포인트에 기록한다. Codex 사용 불가 시 건너뛰지 않고 사용자에게 알린다.

### 열린 질문

| 번호 | 질문 | 임시 처리 |
|---|---|---|
| Q1 | 영역 병합 겹침 비율(R3) | 인수로 받음, 기본값 없음 |
| Q2 | NCC 템플릿 창 크기 | 인수로 받음, 후보 30/40 µm(최소 유닛 50 µm 미만) |
| Q3 | 영역 수 목표와 k | 실자료에서 k=2,3,4 영역 수를 보고 사용자가 결정 |
| Q4 | 사용자 확정 출처 표기(schema 확장 여부) | 확장 안 함, 사이드카 |
| Q5 | DL2 승격 판정 라벨 수 | 확정 쌍 수십 개, 통계 주장 없음 |
| Q6 | hotspot case의 `1.png`/`2.png` 사용 여부와 극성 | **결정됨**(2026-10-19): 둘 다 지원, 둘 다 흰 배경·검정 패턴(검정 = 형상), `binary_zero_is_marked`. 근거 `user_answer_2026_10_19`를 provenance에 남기고 출처별 계약으로 고정 |
