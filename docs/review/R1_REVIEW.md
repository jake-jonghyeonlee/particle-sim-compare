# R1 독립 코드 검토 — 수치 계산 (T1 계약 + T2~T5)

- 검토자: 새 Claude Opus 5.5 세션, effort high, 읽기 전용. 이 코드와 계획을 작성하거나 채택하지 않았다.
- 대상: `/Users/jonghyeonlee/git/particle-sim-compare`, branch `rewrite/line-pixel-rate`
- **후보 commit: `d0cde4d19525a638ea526388eb780f30d17ce2e1`** (diff `778a5a5..d0cde4d`, 지정된 파일 범위만)
- 계약: `docs/plan/PLAN_v2.md` §2.1~2.4, §3, §4.1, §6 T1~T5, §8 R1
- 게이트 상태: dispatch 브리프대로 G1·G2·G7a·G7b **미답변**으로 보고 판정했다.
- 검토 방법: 작업 트리에 다른 worker의 미커밋 변경(`cli.py`, `pipeline.py`, `synthetic.py`, `report.py`, `roi_export.py`)이 있어, `git archive d0cde4d`로 후보를 저장소 밖 임시 폴더에 풀어 그 사본만 읽고 실행했다.

## 판정: **FAIL**

막는 결함이 2건 있다. 둘 다 좁고 국소적인 수정으로 끝나며, 나머지 검토 항목은 계약과 맞았다. 이 판정은 위 후보 commit에만 유효하다. 수정 후에는 B1·B2와 그 시험만 다시 검토하면 된다.

| 구분 | 건수 | 요약 |
|---|---:|---|
| 막는 결함 | 2 | B1 제품 요약 지도가 layer를 합치지 못함, B2 mm·nm 입력에서 panel unit 수가 1 작게 나옴 |
| 막지 않는 지적 | 10 | 아래 N1~N10 |

모든 결과는 합성 자료 기준이다. 실자료 정확도나 성능은 이 검토로 확인되지 않는다.

## 막는 결함

### B1. 제품 요약 지도가 공정 step이 layer마다 다르면 layer를 하나도 합치지 않는다

- 위치: `src/simcompare/electrode_map.py:131-133` (`MapKey.summary_key`), `:391-393` (요약 묶기)
- 원인: 요약을 묶는 키가 `layer`만 빼고 `process_step_id`·`source_id`·`exposure_id`를 그대로 포함한다. layer마다 공정 step ID가 다르면 layer 하나가 요약 하나가 되어 layer 간 pixel별 최댓값이 계산되지 않는다.
- 실패 입력 (실행 확인): T1 fixture `tests/fixtures/numeric_v2`에 M1 lateral case 하나를 추가했다. fixture 자신의 표기대로 `process_step_id=EXAMPLE_STEP_M1`, `analysis_layer=EXAMPLE_M1`, 전극 C·D, d5 mask는 기존 lateral과 같다. `load_dataset` 후 `compute_electrode_maps(dataset, min_count=2)` 결과:
  - layer 2개 (M1, M2), 각 `[[2,2,0,0],[0,0,0,0]]`
  - **요약 2개** (`EXAMPLE_STEP_M1`, `EXAMPLE_STEP_M2`), 각각 layer 하나만 담음. 상태는 `partial`, 사유는 `map_scope_unconfirmed`뿐이다.
  - 더 작은 재현: L1에 AB·AC (`step='STEP_L1'`), L2에 CD (`step='STEP_L2'`) → 요약 2개 `[[3,0]]`, `[[2,0]]`.
- 기대 동작: 한 제품·설계·unit의 layer들이 요약 하나로 모이고 pixel별 최댓값을 낸다 (PLAN T5 Change, T5 반례 "다른 layer가 CD로 2이면 summary=3"). layer와 step의 일대일 여부는 G2 미답변이므로, 합칠 수 없다고 판단한다면 조용히 쪼개지 말고 요약에 명시 사유를 남겨야 한다.
- 시험이 놓친 이유: `tests/test_electrode_map.py:94-101`의 요약 시험은 두 layer가 같은 `step='step'`을 쓴다. `:299-309`는 step이 다르면 요약이 분리되는 것을 오히려 기대값으로 고정한다. T1 fixture는 `process_step_id`를 layer별(`EXAMPLE_STEP_M2`)로 쓰고 `docs/data_contract.md:44`도 layer↔step 일대일을 G2 pending으로 둔다.
- 필요한 시험: layer마다 step이 다른 두 layer에서 요약 `[[3,0]]` 하나가 나오는 손계산 사례.

### B2. `unit_count`가 mm·nm 입력에서 정확히 나누어떨어지는 치수를 1 작게 센다

- 위치: `src/simcompare/linepixel.py:411-417` (`_size_um`), `:441-449` (`unit_count`); 변환은 `src/simcompare/numeric_schema.py:52-60`
- 원인: 비율을 구하기 전에 치수를 float로 um 변환한다. `2.01 × 1000.0 = 2009.9999999999998`이 되어 Decimal 비율이 `200.99999…`가 되고 floor가 200을 낸다. Decimal은 변환 뒤의 오차를 그대로 받는다.
- 실패 입력 (실행 확인), 모두 `rounding='floor'`:

  | 호출 | 결과 | 기대 |
  |---|---:|---:|
  | `unit_count((2.01,1),(0.01,1),length_unit='mm')` | 200 | 201 |
  | `unit_count((32.3,1),(0.1,1),length_unit='mm')` | 322 | 323 |
  | `unit_count((65.1,1),(0.3,1),length_unit='mm')` | 216 | 217 |
  | `unit_count((1.001,1),(0.001,1),length_unit='mm')` | 1000 | 1001 |

  mm에서 `panel = n × unit` (n=1..1999, unit ∈ 0.1, 0.01, 0.05, 0.3, 0.001)인 9,995개 조합 중 65개가 1 작게 나왔다. um과 nm(정수 치수)에서는 같은 탐색에서 0건이었다.
- 영향: 오류 없이 N이 한 축에서 1 줄어 `V_panel`과 delta가 틀린다. panel 치수가 unit의 정수배인 것은 설계상 흔한 경우다.
- 기대 동작: 비율은 단위와 무관하므로 선언된 단위의 값끼리 Decimal로 나눈 뒤 floor/half-up을 적용한다. epsilon은 쓰지 않는다 (PLAN §2.2).
- 시험이 놓친 이유: `tests/test_panel_comparison.py:52-57`의 um 외 단위 사례는 `(2500,3500) nm` 하나뿐이고 변환이 정확히 떨어진다. 현재 loader는 um만 받으므로 manifest 경로는 영향이 없지만, `PanelSpec(length_unit=…)`은 공개 인자이고 시험도 nm를 쓴다.

## 막지 않는 지적

우선순위 순이다.

**N1. 전극 수 지도의 N/A 근거 조회가 `diameter`+`diameter_unit` 표기를 못 읽는다.**
`src/simcompare/electrode_map.py:241-242`는 `diameter_um` 키만 찾는다. loader(`adapters/numeric.py:239-244`)와 `linepixel.py:205-213`은 두 표기를 모두 받는다. 입력: vertical case의 N/A record를 `{"diameter": 1000, "diameter_unit": "nm"}`로 적으면 `compute_areas`는 `not_applicable`을 내고 `compute_electrode_maps`는 `not_applicable_evidence_required`로 전체 실패한다 (실행 확인). 기대: 같은 입력을 두 경로가 같게 받아들인다. 값이 틀리는 것이 아니라 오류로 닫히므로 막지 않는다.

**N2. valid 밖에 켜진 hotspot 셀이 보고 없이 버려진다.**
`linepixel.py:311`, `measurement.py:109-111`, `electrode_map.py:347`. 입력: `valid=[[1,0,1,1,1,1]]`, mask 열 {0,1} → line 면적 1.0, 원천 CA 1.0, 사유는 `partial_observation`뿐 (실행 확인). 기대: 미관측 셀의 활성 값은 입력 불일치이므로 사유(예: 활성 셀 수)를 남긴다.

**N3. 불완전·부분 상태 값의 표기가 면적과 가중값에서 다르다.**
`AreaMetric`은 `partial`일 때 `value`에 부분합을 넣는다 (`linepixel.py:307-309`). `WeightedMetric`은 `value=None`, `partial_value=부분합`이다 (`:593-601`). `docs/data_contract.md:60`은 필드를 `{value, unit, status, reasons, coverage, basis}`로만 정한다. 기대: 한 규칙으로 통일. T6 보고서가 `value`만 읽으면 부분 면적을 완전값처럼 쓴다.

**N4. `unavailable` layer 지도가 null이 아닌 0 배열을 돌려준다.**
`electrode_map.py:360-361`, `:388-389`, `:405-408`. 입력: 대표 지름 record가 `missing`인 case 하나 → layer `status='unavailable'`, `counts=[[0,0]]`; 요약도 `unavailable`인데 `[[0,0]]` (실행 확인). frame 불일치 요약만 `counts=None`이다. 기대: 산출 차단이면 null (계약 `data_contract.md:60`).

**N5. 비교 키가 두 제품의 `source_id`·`exposure_id`·`process_step_id` 일치를 요구한다.**
`linepixel.py:465-478`. 입력: reference `source='run-ref'`, target `source='run-tgt'` → 모든 delta가 `None`, 사유 `reference_layer_unknown`/`target_layer_unknown` (실행 확인). 오류로 닫히지만, 원천 ID가 제품별 실행 ID라면 실자료 비교가 전부 막힌다. 모든 시험이 같은 문자열을 쓴다. G2에서 ID 의미를 확인할 항목이다.

**N6. G1·G2 차단이 호출자가 넣는 `is_synthetic=True`에만 의존한다 (코드 판독).**
`density.py:29`, `linepixel.py:548`. `compute_rates`(`:620-644`)는 `areas.gates`와 manifest의 `unit_domain`을 보지 않는다. fixture는 측정 domain 4×2 um이 2×2 unit이라고 적지만, 호출자가 frame 전체 `(4,2)`를 `one_unit`으로 선언하면 `:550-552` 검사를 통과한다. 이번 후보에서는 실자료 loader가 전부 막혀 있어 실제 누출은 없다. T6가 실자료 설정에서 이 표시를 자동으로 채우지 않는지 R2에서 확인해야 한다.

**N7. panel이 unit보다 작으면 N=0, panel 값 0.0이 `available`로 나온다.**
`linepixel.py:448-449`, `:603`. 입력: panel `(3,10)`, unit `(6,1)` um, floor (실행 확인). 기대: 오류 또는 명시 사유. 치수 단위를 잘못 넣은 경우와 구별되지 않는다.

**N8. bucket의 `source_case_keys`가 면적에 기여하지 않은 case도 나열한다.**
`linepixel.py:281`, `:328`. 입력: 같은 pair의 case 두 개 중 하나가 d5 `missing` → bucket 면적 1.0, 원천 키 2개 (실행 확인). 주석은 "pair lineage"라고 밝히지만, T6가 "이 면적을 만든 case"로 표시하면 틀린다.

**N9. 실자료에서 깨질 수 있는 정확 일치 비교와 기본 허용오차.**
- `adapters/numeric.py:108`, `:346-348`, `linepixel.py:550-553`은 float를 `==`로 비교한다. pitch 0.1, 폭 3셀이면 `3×0.1=0.30000000000000004`라 선언값 0.3과 달라 `one_unit_domain_mismatch`가 된다 (실행 확인). 오류로 닫힌다.
- `measurement.py:85-86`의 보존 허용오차 기본값은 `1e-9`다. 원천 CA가 pixel 수가 아닌 도형 면적이면 모든 record가 `conservation_failed`로 제외된다 (`linepixel.py:283-285`). G2에서 원천 CA 산정 방식을 확인해 허용오차를 정해야 한다.
- valid에 구멍이 하나라도 있으면 그 layer는 항상 `partial`이고 delta는 나오지 않는다 (실행 확인). 계약(`data_contract.md:32`)과 일치하는 의도된 동작이지만 실자료 valid 형태에 따라 비교가 전부 막힌다.

**N10. 시험 보강 항목.**
- line/pixel 시험의 mask는 모두 1행이다. 2차원 다행 mask의 membership 사례가 없다.
- legacy import 차단 시험(`tests/test_numeric_inputs.py:175-191`, `tests/test_electrode_map.py:276-288`)은 `linepixel`·`density`를 불러오지 않는다. 직접 확인한 결과 의존은 없다.
- 사소: `adapters/numeric.py:226`은 높이 5 um 이상을 `height_evidence_required` 코드로 거부한다. `measurement.py:89-90` docstring은 `ca_um2`가 관측 밖에서 null이라 하지만 부분 창은 부분합을 넣는다 (`:118`).

## 점검 항목별 결과

| 항목 | 결과 | 근거 |
|---|---|---|
| 단위, pitch→면적 | 맞음 | 셀 면적 = pitch(um)²; nm/mm 길이·면적·density 변환 확인. pitch 0.5, 활성 2셀 → 0.5 um² |
| union, line 우선, transfer | 맞음 | `P_eff=P∖L`, `T=P∩L`, `U=L∪P`; transfer는 line/pixel 합계 어디에도 더해지지 않음 |
| exclusive+shared=class 합, shared 미분할 | 맞음 | membership 패턴별 셀 수로 구성되어 항등이 구조적으로 성립; 나누는 코드 없음. 63·65·129 pair, 3중 겹침 확인 |
| target−reference 부호, 제품별 N | 맞음 | `bv−av`; 각 제품의 `PanelSpec`으로 N 산정. −50 사례 확인 |
| floor / half-up, epsilon 없음 | **B2** | um에서는 맞음 (`(2.5,3.5)` → 6 / 12, `0.3/0.1` → 3). mm에서 틀림 |
| missing / N/A / 관측 0, 불완전성 전파 | 맞음 | missing→partial, 전부 N/A→not_applicable(0 기여), 미매핑→partial; partial은 delta에서 `None`. 누락 density bin과 관측 0 구별 |
| 원천 case 정체성, failure_type 보존 | 맞음 | `SourceCaseArea`, `CaseLink`에 identity·endpoint 결합·raw·유형 유지; G7a 조각 지표는 pending 표시만 |
| legacy 비의존 | 맞음 | 새 모듈 7개를 legacy·scipy·torch import 차단 상태의 새 interpreter에서 불러옴 |
| reader 경로 안전 | 맞음 | 절대경로, `..`, 역슬래시, root 밖 symlink(파일·폴더), NUL, 폴더, 빈 문자열 거부; pickle·palette·RGB·LA·16bit 0/65535 거부 |
| 지도: distinct 전극 | 맞음 | 전극별 mask OR 후 합; 중복 BA 추가해도 3 |
| 지도: layer 간 pixel별 최댓값 | **B1** | 같은 step일 때만 성립 |
| 지도: 4-이웃, 성분 기준 CA | 맞음 | 대각 두 셀은 두 영역; 영역 CA는 성분 행 구간 창의 합집합으로 측정 (bbox 4가 아닌 3) |
| 지도: G7b pending | 맞음 | 항상 `partial`, `G7b.value=None`, 영역 ROI `blocked: map_scope_unconfirmed`; legacy open 의미 유지 |
| 게이트 G1·G2 | 맞음 (N6 유의) | 실자료 mode·비합성 manifest는 오류; fixture의 가중값·panel·delta는 `None`, 면적은 유지; pending 중 `/4` 제공 시 오류 |
| 계획 반례의 기대값 | 손으로 쓴 값 | T2~T5 반례 모두 리터럴 기대값. `set_oracle`은 순수 Python 집합으로 별도 구현 |
| T1 문서 | 맞음 | 관측 0·누락·N/A·panel pending·endpoint 순서 예시 있음; 네 게이트 모두 pending; manifest JSON 문법 통과 |

## 실행한 것

모두 후보 사본(`git archive d0cde4d`)에서 `PYTHONPATH=<사본>/src`, 저장소의 `.venv/bin/python`(3.11.15)으로 실행했다. 네트워크·학습·저장소 파일 수정 없음.

1. `python -m pytest -p no:warnings -p no:cacheprovider -q tests/test_numeric_schema.py tests/test_numeric_images.py tests/test_numeric_inputs.py tests/test_measurement.py tests/test_linepixel.py tests/test_density.py tests/test_panel_comparison.py tests/test_electrode_map.py` → **223 passed**. 전체 suite는 실행하지 않았다.
2. `python3 -m json.tool docs/domain-input-example/numeric_v2/manifest.json` → 통과. 문서 예제와 fixture의 manifest·CSV는 동일.
3. 임시 폴더의 읽기 전용 실험 스크립트 3개:
   - `unit_count` 경계값과 um/mm/nm 정수배 탐색 (B2)
   - layer별 step에서의 요약 지도, T1 fixture에 M1 case를 추가한 loader 경유 재현 (B1)
   - 변환 지름의 N/A 근거, valid 밖 활성 셀, valid 구멍의 delta, 기여 없는 lineage, N=0, unavailable 지도, 원천 ID가 다른 비교, float 일치 (N1~N9)
   - 경로 14종과 PNG/NPY 형식 6종, legacy import 차단 interpreter
   - 영역 계산 시간: pitch 0.05 um 원형 영역, 1000² 셀 1.3 s, 2000² 셀 8.1 s (문제 없음)

## 확인하지 못한 것

- 실자료 입력, 실제 density 단위, 2×2 unit 환산은 게이트 미답변이라 검토 범위 밖이다.
- `cli.py`·`pipeline.py`·`report.py`·`roi_export.py`·`synthetic.py`와 legacy 모듈은 보지 않았다 (R2 범위).
- N6은 코드 판독만 했고 실행으로 재현하지 않았다.

---

# Re-review of 8a35c0e

- 검토자: R1을 작성한 독립 검토 세션 (Claude Opus 5.5, 읽기 전용). 수정 코드를 작성하지 않았다.
- **후보 commit: `8a35c0e2b0de93bc8684e23b567cf02376c3a83c`**, 수정 diff `ad3b664..8a35c0e` (14개 파일, +305 −63)
- 범위: 수치 계산 (T1 계약 + T2~T5)의 수정분만. `pipeline.py`·`report.py`는 N3·N4·N8 때문에 바뀐 줄만 읽었다. T6 전체는 R2 범위다.
- 방법: `git archive 8a35c0e`를 저장소 밖 임시 폴더에 풀어 그 사본으로 실행했다 (`PYTHONPATH=<사본>/src`, 저장소 `.venv/bin/python`). 작업 트리는 clean이었다.

## 판정: **PASS-WITH-NOTES**

B1·B2는 둘 다 해결됐다. 수정 diff에서 값이 틀리게 나오는 새 결함은 찾지 못했다. 아래 "새로 생긴 지적" 3건은 막지 않는다. 이 판정은 위 후보 commit의 수치 계산 범위에만 유효하며, 모든 결과는 합성 자료 기준이다.

## 막는 결함의 상태

| 결함 | 상태 | 실행한 것 | 결과 |
|---|---|---|---|
| B1 | **해결** | R1의 두 재현을 그대로 다시 실행 | 요약 1개, 기대값과 일치 |
| B2 | **해결** | R1의 표 4건과 정수배 탐색을 다시 실행 | 4건 모두 기대값, 탐색 0건 실패 |

**B1.** `MapKey.summary_key`가 `(product_id, design_version, unit_id)`가 됐다 (`electrode_map.py:132-133`).
- 작은 재현: L1에 AB·AC (`STEP_L1`), L2에 CD (`STEP_L2`) → layer 2개, **요약 1개 `[[3,0]]`**, `combined_steps=['STEP_L1','STEP_L2']`.
- loader 경유 재현: T1 fixture에 M1 lateral case(`EXAMPLE_STEP_M1`, 전극 C·D)를 추가 → layer 2개 각 `[[2,2,0,0],[0,0,0,0]]`, **요약 1개** `[[2,2,0,0],[0,0,0,0]]`, `combined_steps` 2개.
- 경계: frame이 다른 두 layer → 요약 `unavailable`, `counts=None`, `incompatible_layer_frames` (조용히 쪼개지 않음). 모든 layer가 missing → 요약 `unavailable`, `counts=None`. 한 layer만 missing → 알려진 layer의 값으로 `partial` + `source_case_missing`.
- 새 시험 `test_product_summary_combines_two_layer_steps_with_hand_written_maximum`은 리터럴 `[[3,0]]`을 쓴다.

**B2.** `unit_count`가 선언 단위의 원값을 `Decimal(str(x))`로 나눈 뒤 축별 반올림한다 (`linepixel.py:461-475`). epsilon 없음.
- 표 4건: 201, 323, 217, 1001 (모두 기대값).
- 정수배 탐색: unit ∈ {0.1, 0.01, 0.05, 0.3, 0.001, 0.7, 1.3}, n=1..1999, mm·um·nm 각 13,993개 → **실패 0건**.
- 정수배가 아닌 값(±1/4 unit, +1/2 unit) 1,996개: floor와 half-up 모두 실패 0건. 기존 um 사례 `(2.5,3.5)` → 6 / 12, `0.3/0.1` → 3 유지.
- 0, inf, 길이 1, bool, 미지원 단위, 잘못된 반올림 정책은 모두 `NumericContractError`.

## 막지 않는 지적의 상태

| 지적 | 상태 | 근거 |
|---|---|---|
| N1 | 해결 | `diameter`+`diameter_unit`(nm) N/A record → 지도 `partial`, N/A 1건 집계; 면적 `not_applicable` (직접 실행 + 새 loader 시험) |
| N2 | 해결 (보고만) | 이유: 값을 바꾸지 않고 사유만 남기는 것이 R1의 기대였다. `valid=[[1,0,1,1,1,1]]`, 열 {0,1} → 사유에 `active_cells_outside_valid:1` 추가. clipping 정책은 그대로이며 `data_contract.md`에 적혔다 |
| N3 | 해결 | 같은 입력 → `value=None`, `partial_value=1.0`, `partial`. 원 case `ca_um2`도 같은 규칙. 완전 관측은 `value=2.0`, `partial_value=None`. 계약 문서 갱신됨 |
| N4 | 해결 | `unavailable` layer·요약 모두 `counts=None`; 관측 0은 `[[0,0]]`로 구별 |
| N7 | 해결 | panel `(3,10)`, unit `(6,1)` → panel 값 `None`, `unavailable`, `panel_has_no_units`; unit 값 2.0은 유지 |
| N8 | 해결 | `pair_lineage_case_keys`와 `contributing_source_case_keys` 분리. 129 pair 무작위 사례에서 모든 bucket의 기여자가 비어 있지 않고 계보의 부분집합 |
| N10 | 해결 | 2행 mask membership 시험 추가 (기대값을 손으로 다시 계산해 일치 확인); legacy 차단 시험 두 곳이 `linepixel`·`density`를 불러옴 |
| N5, N6, N9 | **변경 없음, 열린 항목** | diff에 해당 코드 변경 없음. N5·N9는 게이트 G2, N6은 R2 batch에서 확인 |

## 시험 점검

- 삭제된 시험 함수는 `test_source_conditions_products_and_units_do_not_mix` 하나다 (B1이 지목한 step별 분리 기대). 제품·unit·설계 분리는 새 시험 `test_product_design_and_unit_stay_separate_in_summary`가 이어받는다.
- 나머지 삭제 줄 12개는 모두 N3·N4의 표기 변경이다 (`value==1` → `value is None and partial_value==1`, `[[0,0]]` → `None`). 숫자 기대값은 그대로 남아 약화가 아니다.
- 새 시험의 기대값은 모두 리터럴이다.
- 실행: 수치 시험 8개 파일 238건, `test_numeric_report.py`+`test_numeric_cli.py` 19건 모두 통과 (두 실행 모두 pytest 종료 코드 0, 실패·오류 표시 없음; 건수는 진행 표시로 셌다). 전체 suite는 실행하지 않았다.

## 새로 생긴 지적 (막지 않음)

**R1. 같은 layer·다른 step의 layer 지도 분리를 고정하는 시험이 없어졌다.** 삭제된 시험은 같은 layer에 step이 다르면 layer 지도가 따로 나오는 것도 함께 확인했다. 동작은 유지된다 (직접 실행: layer 2개 각 `[[2,0]]`, 요약 `[[2,0]]`). 이때 요약은 전극을 합쳐 3을 내지 않고 최댓값 2를 낸다. 같은 layer가 여러 원천·노출 조건으로 들어올 때 이것이 맞는지는 G2에서 정할 항목이다.

**R2. bucket 기여자 계산이 chunk 처리를 우회한다.** `linepixel.py:338-343`은 bucket마다 전체 frame 배열을 pair 수만큼 다시 계산한다. 측정: 129 pair, bucket 약 1,160개, 지름 32개 기준 500² 셀 2.9 s, 1000² 셀 10.7 s. 같은 1000² 입력을 수정 전(`ad3b664`)과 나란히 다시 재면 6.8 s → 10.1 s로 약 1.5배다. 실자료 크기에서 다시 확인해야 한다.

**R3. 형식이 틀린 원 record가 `KeyError`로 끝난다.** `electrode_map.py:243-244`. N/A case의 `metadata['records']`에 지름 키가 둘 다 없으면 `KeyError: 'diameter'`가 난다 (실행 확인). loader를 거친 입력에서는 생기지 않는다. `linepixel._raw_record`도 같은 형태다.

## 확인하지 못한 것

- 실자료 입력과 게이트 의존 동작. 
- `report.py`·`pipeline.py`의 바뀌지 않은 부분, `roi_export.py`, `cli.py` (R2).
