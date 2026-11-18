# 장부 보고서 1단계 — 규칙 문서

Run 20261115-ledger-report, 계획 v1.1. 이 문서는 T2–T6 의 계약이다.
기준: `feat/template-export` @ `40234b45d5e703bc20f397bb8d2de0288dc1eb1f`.
양식 목업: [`ledger_report_mockup_v8.html`](ledger_report_mockup_v8.html) (값·이름·형상은 가상. `#size`, `#demo`, `#print` 해시로 보기 전환).

표기: **(제안)** 은 PLAN 에 이름이 없어 이 문서가 정한 것이다. T2–T4 가 구현 중 바꾸면 Lead 에 보고한다.

## 1. 목적과 범위

- 목적: 전극 이름을 연결 열쇠로 쓰는 장부 보고서를 만든다. 회로·배치가 달라지는 비교에서도 "얼마나, 어느 전극이, 무엇이 달라졌나"를 보인다.
- 1단계 범위: 규칙 문서 → linepixel 분담 → compare2 항목 → 장부 계산(`ledger`) → 보고서 1·3·4·6·7절(`ledger-report`) → ETX 08.
- 2절(layout 리스크 지도)과 5절(위치 상세)은 다음 Run 이다. 1단계 보고서는 두 절의 제목과 "다음 단계에서 추가됩니다" 한 줄만 둔다.
- 07 보고서(`compare2-report`)는 그대로 둔다. 같은 쌍·같은 레이어 위치의 상세 화면으로 계속 쓴다.
- 기존 `delta.*` 는 읽기만 한다.

## 2. 용어

| 용어 | 뜻 |
|---|---|
| 쌍 | 전극 쌍. `pair_id` 로 식별한다. `pairs.csv` 는 두 모델 공통 한 파일이다. |
| item | 줄 단위 항목. 키 = (pair_id, failure_type, endpoints_key). endpoints_key = 두 끝의 (electrode_id, actual_layer) 를 electrode_id(같으면 layer) 순으로 정렬한 튜플. upper_layer/lower_layer 는 함께 기록한다. 방향이 다른 상하 short((A@SD1, B@SD2) 와 (A@SD2, B@SD1))는 서로 다른 item 이다. |
| 불량 방식 | `lateral_short`(측면), `vertical_short`(상하), `lateral_open`(CNO). |
| 위/아래 레이어 | 상하 short 의 두 전극 레이어. `위/아래`(stack 순서)로 적는다. 측면은 위 = 아래. |
| line / pixel | 불량 종류. line 과 pixel 의 % 는 더하지 않고 한 순서로 정렬하지 않는다. |
| 곡선 보정 줄 | 절편 c. 전극에 나누지 않는 한 줄(1절). |
| CRP 줄 | 두 제품의 CRP 가 다를 때의 차이 한 줄(1절). pixel 에만 있다. |
| 그룹 | GroupKey 전체(대표 축 analysis_layer, density_layer, 이물 크기). |
| s | secant 기울기 `delta.slopes.<kind>.value`. |

## 3. 분담 규칙 Q1

- 그룹과 종류(line / pixel)마다 셀 c 의 n(c) = 그 종류에서 c 를 덮는 **쌍** 수.
- 쌍 몫 = Σ_c 1/n(c).
- 한 쌍의 item m 개가 같은 셀을 덮으면 그 쌍의 1/n 을 m 으로 다시 나눈다. item 몫 = 1/(n·m). 쌍 합은 변하지 않는다.
- label 분할(`_partition_labels`)은 바꾸지 않는다. 그룹·종류마다 셀별 덮는 쌍 수 배열(uint16, 넘치면 int32)을 만들고 쌍마다 히스토그램 `hist[pid] = {n: cells}`(정수)를 낸다. 몫 = fsum(cells/n).
- `multi_item_overlap_cells`(같은 쌍의 item 끼리 겹친 셀 수)를 기록한다.

정수 검산 3개 (종류·그룹마다, 부동소수 합으로 검산하지 않는다):

1. `hist[pid][1] == exclusive_cells[pid]` (모든 pid).
2. H_n = Σ_pid hist[pid][n] 에 대해 `H_n % n == 0` (모든 n ≥ 2).
3. `Σ_{n≥2} H_n // n == shared_cells`.

## 4. transfer 규칙 Q2

- 기존 규칙을 유지한다. line 은 keep = valid 에서 line 쌍끼리 나눈다. pixel 은 keep = valid & ~line(pixel effective)에서 pixel 쌍끼리 나눈다(`linepixel.py:733-734` 와 같은 keep).
- line 에 덮인 pixel 쌍 셀의 pixel 몫은 0 이다.
- 설명용 `transferred_out_cells` 만 남기고 합계에 넣지 않는다.

## 5. 값 정의 Q3

- x = λ_pixel + λ_cno.
- item i 의 값: **a_i = s·λ_i^A, b_i = s·λ_i^B, Δ_i = s·Δλ_i** (07 의 조건 Δ 와 같은 기울기 규칙). CNO 도 같은 식(pixel)이다.
- 절편 줄 c 는 전극에 나누지 않는다. A = B, 변화 0.
  - c_line = vi_line_A − s_line·λ_line_A (= vi_line_B − s_line·λ_line_B)
  - c_pixel = vi_pixel_B − s_pixel·x_B
- CRP 줄 (d = `delta.crp_difference.delta_vi_pixel`): **A = −d, B = 0, Δ = d**. 모든 줄에서 Δ = B − A 이다.
- 쌍의 A 값은 "B 의 CRP 기준 환산값"이다. 보고서가 1절·7절 문구로 밝힌다.

합계식 6개 (열 합계가 실제 vi 와 같은 형태):

| # | 식 |
|---|---|
| 1 | line A: Σa + c_line = vi_line_A |
| 2 | line B: Σb + c_line = vi_line_B |
| 3 | line Δ: ΣΔ = Δvi_line |
| 4 | pixel A: Σa + CNO_A + c_pixel − d = vi_pixel_A |
| 5 | pixel B: Σb + CNO_B + c_pixel = vi_pixel_B |
| 6 | pixel Δ: ΣΔ + ΔCNO + d = Δvi_pixel |

(pixel 의 Σ 는 pixel 쌍 item 의 합. line 의 c_line 은 A = B 이므로 식 3 에 나타나지 않는다.)

- 주 숫자 % = 100 × 값 / vi_A(같은 종류). vi_A 가 **정확히 0** 이거나 null 이면 % 없이 원래 값만 보인다. 작은 양수 vi_A 는 그대로 계산한다(compare2 의 `RATIO_EPS` 1e-15 컷을 쓰지 않는다).
- 모든 합계(합계식 6개, 쌍 합, 전극 합, 레이어 합, 증가 합·감소 합, compare2 `ledger_check` 의 item 합)는 `math.fsum` 으로 정확히 더한다. compare2 의 다른 합은 바꾸지 않는다.
- 쌍 Δ 는 07 과 같은 기울기 규칙이다. 그러나 공유 몫을 나눠 받으므로 쌍 Δ 는 07 의 조건 Δ(exclusive 만)와 같지 않다. 관계식: 쌍 Δ 합 = `delta.conditions` 합 + `delta.shared`.
- 전극 몫: 쌍 값의 1/2 씩을 두 전극에 넣는다. CNO 는 그 전극에 전부 넣는다. Σ 전극 = Σ 쌍 + CNO.

## 6. 계열 불능의 null 전파 (X2)

- 계열(line / pixel)마다 먼저 "계산 가능"을 정한다. 두 제품의 그 계열 vi 가 숫자이고 `delta.slopes.<kind>` 가 있어야 한다.
- 계산 불가 계열: 그 계열의 모든 item·CNO·절편·CRP 줄 값(A, B, Δ, %)은 null 이다. 그 계열 검산은 `not_evaluated`. 다른 계열은 영향이 없다.
- presence(있음/없음)는 값과 따로 보존한다. 조건이 있으나 계산 불가(`computable=false`)인 쌍은 "있음, 값 없음"이다. absent = 0 으로 바꾸지 않는다.
- 값이 필요한 판정(같은 쌍 증감 / 변화 없음)이 null 이면 변화 종류는 **"값 대기"** 이다. 건수 표에 "값 대기" 줄을 둔다(0 이면 숨김).
- 관측 사실 판정(전극 추가·제거, 새 쌍·사라진 쌍, 레이어 변경)은 값 없이도 한다.

## 7. 상하 레이어 표기 Q4

- 표기 `위/아래`(stack 순서). 집계 레이어 = analysis_layer(위).
- 출처 `NumericCase.endpoints[].actual_layer`. item 에 `endpoints: [{electrode_id, actual_layer}]` 를 둔다.
- T2 구현(`linepixel._item_layers`): `_group` 안에서는 stack 순서를 얻을 수 없다. 상하 short 는 `upper = GroupKey.analysis_layer`, `lower = 그 밖의 끝 레이어`(둘 다 analysis_layer 와 같으면 analysis_layer, 여럿이면 이름순 첫째)다. 측면은 upper = lower = analysis_layer 다. 어댑터가 상하 short 의 analysis_layer 를 stack order 가 높은 끝으로 정하므로 위 레이어와 같다(근거: `real_hotspot.py:1708`(manifest)·`:1769`·`:1841`, `adapters/numeric.py:225-226`).
- T2 구현의 `GroupArea.ledger_shares`(`condition_stats=True` 일 때만, 직렬화 제외) 구조: `{line|pixel: {items: [{pair_id, failure_type, upper_layer, lower_layer, endpoints, share_cells, own_cells, transferred_out_cells}], pair_hist: {pair_id: {n: cells}}, multi_item_overlap_cells, checks: {exclusive_ok, divisible_ok, shared_ok, sum_ok, expected_cells, share_sum, n_hist}}}`. items 는 (pair_id, failure_type, endpoints_key) 순이다(T3 에서 방향 분리를 위해 키를 이렇게 바꿨다. `upper_layer`·`lower_layer` 는 항목 필드로 남는다). `n_hist`·`pair_hist` 의 n 키는 정수다.

## 8. 변화 종류 판정 Q5

쌍 하나에 종류 하나. 우선순위 순서로 처음 맞는 것을 쓴다.

1. 두 전극 중 하나라도 한쪽 제품의 전극 목록에 없다 → **전극 추가·제거**
2. 쌍이 B 에만 → **새 쌍**, A 에만 → **사라진 쌍**
3. A, B 의 item 키 집합이 다르다 → **레이어 변경** (방식만, 아래 레이어만, 방향만 바뀐 경우 포함). 키 = `item_id`(9절). 거리 필터가 전부 지운 item(아래 "거리 필터 item")은 상대 제품에 같은 `item_id` 가 아예 없을 때만 키 집합에서 뺀다. 상대 제품에 같은 `item_id` 가 있으면(필터든 값이든) 양쪽에 있는 것으로 센다.
4. |Δ| ≤ 1e-12 + 1e-9·max(|a|,|b|) → **변화 없음**
5. 그 밖 → **같은 쌍 증감**

- 쌍 "있음" = compare2 그 제품에 조건이 있음(면적 0 포함). 폴더 없는 기대 case 만 가진 쌍은 없음이다.
- 쌍 줄은 우선순위 최상 종류 하나. item 줄은 각자 both / new / gone. 건수는 쌍 하나에 한 번.
- 계산 불가 조건(`computable=false`)의 쌍은 "있음, 값 없음"이다. 그 쌍은 12절 7절(참고) "계산 불가 항목" 목록(`ledger.json` `unavailable`)에도 올린다.
  - 한쪽 또는 양쪽 조건이 계산 불가라 item 키를 알 수 없으면 **레이어 변경을 판정하지 않고** "값 대기"로 둔다(구현: 쌍의 `_keys_known` 이 false).
  - 관측 사실 판정(전극 추가·제거, 새 쌍·사라진 쌍)은 그대로 한다. 그 판정은 item 키를 쓰지 않는다.
- 거리 필터 item: compare2 `ledger_items[].reasons` 가 `diameter_filter_removed_all` 하나뿐인 item(λ 0, 이물 크기 필터가 모든 셀을 지움)은 상대 제품에 같은 `item_id` 가 **아예 없을 때만** 레이어 변경 판정의 키 집합에서 뺀다(T8). 상대에 같은 `item_id` 가 있으면 그 item 은 양쪽에 있는 것으로 센다. 그러면 "A 필터 0, B 값"은 키가 같아 값 판정(같은 쌍 증감 / 변화 없음)이 되고, "A 필터 0, B 에 item 없음"은 키에서 빠져 변화 없음 쪽이며, "양쪽 필터 0"은 값 0 이라 변화 없음이다. 쌍 줄 안에서는 그대로 보이고, `ledger.json` 쌍의 item 에 `filtered_out`, `a_filtered`, `b_filtered` 가 붙는다. `a_filtered`/`b_filtered` 는 그쪽 item 이 거리 필터로 0 이면 `true`(그쪽 값 칸에 "거리 필터로 0" 표시), item 이 없으면 `false`. `filtered_out` 은 있는 쪽이 모두 필터일 때만 `true`(표시 상태 "거리 필터로 0")이다.
- CNO 는 (electrode_id, density_layer) 단위의 별도 종류 "CNO"이다.

전극 목록 완전성 (X3):

- 전극 목록 = `adapters/electrode_png.scan_electrode_shapes(report.dl1_root, products.<role>.dl1_model)` (`electrode_png.py:305-314`, cno 제외, 전극별 레이어 PNG). 출처 `dl1`, 상태 `complete`.
- 목록이 없거나 비면 hotspot·CNO 에 나온 전극으로 대신한다. 출처 `observed`, 상태 `incomplete`, 경고 `electrode_inventory_from_hotspots`. 이때 목록에 없다는 것은 "없음"이 아니라 **"확인 불가"** 이다.
- 전극 추가·제거 판정(우선순위 1)은 두 제품 목록이 모두 complete 일 때만 한다. 아니면 우선순위 1을 건너뛰고 2 이하로 판정한다. 3절 전극 상태는 "확인 불가"로 보인다. 1절에 문구 1줄을 둔다.
- 충돌: complete 목록에 없는 전극이 그 제품의 조건 끝점에 나오면 경고 `electrode_inventory_conflict`(전극 이름 목록)를 남기고, 그 전극의 존재는 "있음"(관측 우선)으로 본다.
- 3절 "전극 레이어 A → B" 도 이 목록의 레이어를 쓴다.

입력 검사 (X1):

- `ledger` 는 `products.reference.pair_table_hash == products.target.pair_table_hash`(`compare2.py:687`)를 먼저 확인한다. 다르면 종료 2 `ledger_pair_table_mismatch`("두 모델이 같은 pairs.csv 를 쓰도록 real.yaml 을 고친 뒤 02 부터 다시").
- 해시가 같은데 한 pair_id 가 양쪽에서 다른 class 면 종료 2 `ledger_pair_class_mismatch`(방어).
- 어느 한쪽 `pair_table_hash` 가 null 이어도 같은 오류(`ledger_pair_table_mismatch`)다(둘 다 null 인 경우 포함).
- `ledger_basis` 가 없으면 `ledger_compare2_stale`("02 를 새 번들로 다시 실행"). config sha256 이 다르면 `compare2_config_mismatch`.
- 07 과 같은 stale 검사(공통 함수 `compare2.stale_reason(doc)` 를 07 과 ledger 가 함께 쓴다. 같은 상수·같은 코드): `inputs.area_basis` 가 `compare2.AREA_BASIS` 와 다르면 `compare2_stale_area_basis`, `inputs.case_exclusions.<role>.rule` 이 `CASE_EXCLUSION_RULE` 과 다르거나 없으면 `compare2_stale_case_exclusion_rule`.
- item 검사(9절): item_id 가 구조 필드와 맞지 않으면 `ledger_item_id_mismatch`, 한 side 에 같은 item_id 가 둘이면 합치지 않고 `ledger_duplicate_item_id`. 모두 종료 2.

## 9. `compare2.json` 추가 키

기존 키·값·CSV 4개·`OWNED_NAMES` 는 불변이다. `schema_version` 은 `compare2_v1` 을 유지한다(07·compare2_link 가 문자열 비교: `compare2_report.py:257`, `compare2_link.py:64`).

- `sides.<role>.ledger_items[]` — item = (pair_id, failure_type, endpoints_key, analysis_layer, class) 마다 하나. 이물 크기·density_layer 는 `by_size` 로 펼친다. 정렬은 (analysis_layer, pair_id, failure_type, endpoints_key, class). 끝점(`endpoints`)은 `(electrode_id, actual_layer)` 순으로 정렬한다. 필드(구현된 이름):
  - `item_id`: 구조 값 `[pair_id, failure_type, upper, lower, [[electrode_id, actual_layer], [electrode_id, actual_layer]]]` 를 `json.dumps(…, ensure_ascii=False, separators=(',', ':'))` 로 쓴 문자열(끝점은 endpoints_key 순). 이름에 `|`·`,`·`@`·`"`·한글이 있어도 두 item 이 같은 id 가 되지 않는다(예전 `|`·`,`·`@` 이음 문자열은 레이어 `Z,B@Z` 와 `Z` 의 두 방향이 같은 id 가 되었다). compare2 의 `_item_id` 가 만들고 `ledger` 가 같은 함수로 다시 계산해 일치를 검사한다. 한 side 안에서 유일하다.
  - `pair_id`, `class`(line/pixel; CNO 는 `ledger_items` 에 없다), `failure_type`, `analysis_layer`, `upper_layer`, `lower_layer`
  - `electrodes`(그 쌍의 전극 목록), `endpoints: [{electrode_id, actual_layer}]`(electrode_id 순)
  - `lambda`(item 몫 λ; 계산 불가면 null), `area_share_um2`(몫 면적 합)
  - `by_size: [{diameter_um, density_layer, area_share_um2, lambda}]`(그룹 = 이물 크기마다. 몫이 0 인 크기도 줄이 있다)
  - `transferred_out_area_um2`(pixel item 중 line 이 덮어 몫 0 이 된 면적. 설명용, 합계에 넣지 않는다)
  - `computable`(λ 가 숫자면 true), `reasons`(그 item 의 그룹 사유: `density_missing_zero_area:<kind>` 등. `lambda_diameters_um` 으로 모든 셀이 빠지면 λ 0, `diameter_filter_removed_all`)
  - λ 는 기존 `lam()` 과 같은 함수·밀도·계수·이물 크기 필터·not-ok 규칙을 쓴다(line item → line 밀도, pixel item → pixel 밀도).
  - `exclusive_cells`·item 별 `multi_item_overlap_cells` 는 두지 않는다(쌍 단위 합은 `ledger_check.integer_checks.<kind>.multi_item_overlap_cells`).
- `sides.<role>.ledger_check`:
  - `status`: failed(아래 어느 하나라도 failed) > not_evaluated > passed
  - `integer_checks`: `{status, line: {exclusive_ok, divisible_ok, shared_ok, sum_ok, multi_item_overlap_cells}, pixel: {…}}`(3절 정수 검산 3개 + 몫 합 = 면적. 모든 그룹의 AND)
  - `lambda_line`, `lambda_pixel`: `{status, sum, total, abs_error}` — Σ item λ(종류별) = `lambda.line` / `lambda.pixel`(CNO 제외)
  - `shared_line`, `shared_pixel`: 같은 모양 — Σ item λ − Σ 쌍 `lambda_excl`(종류별 exclusive) = `shared.lambda_line/pixel`
  - 허용 오차는 기존 `_within`. 계열 λ 가 null 이거나 item λ 가 null 이면 그 계열은 `not_evaluated`. 위 두 합과 item λ 합은 `math.fsum` 이다.
  - failed 면 side `reasons` 에 `ledger_check_failed`, status partial, 종료 3. passed·not_evaluated 면 기존 status·종료 코드 그대로.
- `inputs.ledger_basis = 'pair_share_v1'`.

불변 조건:

- 새 키 3종을 지운 compare2.json 은 변경 전과 같다.
- Σ item λ = `sides.lambda.line/pixel`.
- 쌍별 Σ item λ − lambda_excl 의 합 = shared λ.
- 입력 해시를 바꾸지 않는다(`real_hotspot.py:1553-1571`). compare2.yaml 불변.
- `linepixel.py` `_group` 은 `condition_stats=True` 일 때만 `GroupArea.ledger_shares` 를 만든다(compare2 키는 `ledger_items`). `False` 결과는 바이트 동일.

## 10. `ledger.json` (`ledger_v1`)

`compare2.json` → `ledger.json`. 마스크는 읽지 않는다. 같은 입력 두 번이면 바이트 동일이다(입력에 DL1 전극 폴더 목록 포함). 최상위 키는 T4 가 확정한 이름이다(PLAN 은 이름을 정하지 않았다. `inputs.electrode_inventory` 만 PLAN 이름). 출력 파일: `ledger.json`, `ledger_pairs.csv`, `ledger_electrodes.csv`, `execution.json`(시각은 여기에만).

| 키 | 뜻 |
|---|---|
| `schema_version` | `ledger_v1` |
| `status`, `reasons` | `ok` / `partial`(종료 3, 사유 `ledger_totals_check_failed`) |
| `change_kinds`, `electrode_status` | 코드 → 표시 이름 표(`same_pair_change` 같은 쌍 증감, `layer_change` 레이어 변경, `new_pair` 새 쌍, `gone_pair` 사라진 쌍, `electrode_added_removed` 전극 추가·제거, `no_change` 변화 없음, `cno` CNO, `value_pending` 값 대기 / `common` 공통, `new` 신규, `removed` 제거, `unverifiable` 확인 불가) |
| `inputs` | `compare2_json_sha256`, `config_sha256`, `ledger_basis`, `products.<role>`(model_group, pair_table_hash, crp), `electrode_inventory.<role>` {`electrodes`(정렬), `layers`(dl1 만), `source` dl1/observed, `status` complete/incomplete, `sha256`}, `diameter_filter`(rtrim/T3: compare2.json `inputs.diameter_filter` 를 그대로 복사하고 `formula_basis` 를 더한다. 필터가 없으면 `{"mode": "all"}`. 키가 없으면 T3 이전 산출물이라 보고서가 "이물 크기 범위 정보 없음" 으로 적는다) |
| `totals.<line/pixel>` | `items`(쌍 item 합 A·B·Δ), `intercept`(절편 줄), pixel 만 `cno`·`crp`(A = −d, B = 0, Δ = d; `present`), `a`·`b`·`delta`(열 합계), `vi_a`·`vi_b` |
| `kinds.<line/pixel>` | `computable`, `vi_a`, `vi_b`, `slope`, `slope_method`, `lambda_a`, `lambda_b`, `pair_count`, `change_kinds`(코드별 건수; pixel 은 `cno` 건수 포함), `increase_sum`·`decrease_sum`, `value_pending_rows` |
| `kinds.change_kind_counts` | 쌍 + CNO 전체의 변화 종류별 건수(건수는 쌍 하나 한 번) |
| `pairs` | 쌍별 `rank`(L#, P#), `pair_id`, `electrodes`, `kind`, `change_kind`, `present_a/b`, `computable_a/b`, `a`·`b`·`delta`·`pct_a`·`pct_b`·`pct_delta`, `layers_a/b`, `items`[`item_id`, 방식, 레이어, `endpoints`, `presence` both/new/gone, `filtered_out`(거리 필터로 0, 8절), `a_filtered`/`b_filtered`(side 별 거리 필터 0, 8절), `lambda_a/b`, `a`·`b`·`delta`]. 순서: line 순위, pixel 순위, 종류 불명 |
| `electrodes` | 전극별 `status`, `present_a/b`, `layers_a/b`, `line`·`pixel`(A·B·Δ·% ; pixel 은 CNO 포함), `cno`, `partners_a/b`, `layers`(레이어별 line·pixel 값) |
| `cno` | (electrode_id, density_layer) 줄: `change_kind: cno`, `presence`, `lambda_a/b`, `a`·`b`·`delta`·% |
| `layers.<line/pixel/cno>` | 레이어(analysis_layer, CNO 는 density_layer)별 `a`·`b`·`delta`, `by_size`(이물 크기별), `top_pairs`(Δ 절댓값 상위 2쌍) |
| `checks` | `equations`(`eq1_line_a` … `eq6_pixel_delta`), `electrode_sum`(Σ 전극 = Σ 쌍 + CNO, 종류별 a/b/delta), `pair_delta_vs_compare2`(쌍 Δ 합 = `delta.conditions` + `delta.shared`; 조건 Δ 중 null 이 있으면 `not_evaluated`), `layer_size_sum`(종류 line/pixel/cno × a/b/delta: 레이어마다 이물 크기 열의 합 = 레이어 값. `status`, 검사한 레이어 수 `layers`, `max_abs_error`), `status` |
| `warnings` | `electrode_inventory_from_hotspots`(role), `electrode_inventory_conflict`(role, electrodes) |
| `unavailable` | 계산 불가 항목(`scope` kind/pair/item/cno) |

번호 L#, P# 는 `ledger.json` 에 저장한다(다음 Run 지도가 같은 번호를 쓴다).

순위 규칙(7항): 종류(line / pixel)별로 따로 센다. 정렬 값 = B 값(쌍이 B 에 있고 값이 있을 때), 아니면 A 값. 큰 값이 앞이다. 값이 둘 다 없으면 맨 뒤. 동률은 pair_id 순이다. 종류를 알 수 없는 쌍은 번호가 없다(`rank: null`).

값과 합계 규칙(T4 확정):

- 증가 합 / 감소 합 = 그 종류의 쌍 Δ(값 있는 것) + CNO Δ(pixel) + CRP 줄 Δ(pixel) 중 양수의 합 / 음수의 합이다. 절편 줄은 0 이다.
- 합계식과 Σ 전극 검산은 item 값으로 계산한다. 쌍 값이 null 인 쌍(조건 계산 불가)에는 item 이 없어 합계에 0 으로 들어간다.
- 전극 값 = item 값을 두 끝점 전극(같은 이름이면 한 전극)에 1/2 씩 넣는다. 레이어별 값은 끝점의 actual_layer 에 넣는다. CNO 는 그 전극에 전부, 레이어는 density_layer 이다.
- 이물 크기별 값: item 의 크기 줄은 (diameter, density_layer) 를 키로 모은다(한 item 이 같은 지름에서 density_layer 둘에 걸쳐도 덮어쓰지 않는다). 레이어 × 이물 크기 표의 한 칸 = 그 레이어(analysis_layer)·지름의 density_layer 들의 합이다. 그래서 크기 열의 합 = 레이어 값이고, 이를 `checks.layer_size_sum` 이 검산한다.
- 전극 존재: 목록이 complete 면 목록 ∪ 관측(충돌은 경고), incomplete 면 관측(조건 전극, item 끝점, CNO 전극)이다.
- 전극 레이어 A → B: 목록이 incomplete(출처 observed)일 때 레이어는 item 끝점의 actual_layer 이다. item 이 없고 CNO 에만 나오는 전극은 그 CNO 의 density_layer 로 채운다.

## 11. 출력 폴더 규약과 08 단계 (X4)

- 폴더: `ledger` → `data/ledger`, `ledger-report` → `data/ledger_report`. 02·07 과 겹치지 않는다. 실행기 상수 `LEDGER_DIR`, `LEDGER_REPORT_DIR`(설정 이름 `ledger_dir`, `ledger_report_dir`)를 `output_folders` 의 08 항목에 둔다(실행 전 `data/_runs/<RUN_TAG>/` 로 이동). 이미 있는 두 폴더는 소유 여부와 관계없이 이동하므로 `--out` 거부(남의 파일)는 08 에서 보관 위치가 이미 있을 때(`etx_archive_target_exists`, 종료 2)로 나타난다.
- 작업(`etx_flow.plan_jobs`): `ledger --compare2-dir <02 output_dir> --config <compare2.yaml> --out <LEDGER_DIR>` → `ledger-report --ledger-dir <LEDGER_DIR> --out <LEDGER_REPORT_DIR>`. 상위 N 옵션은 기본값. 02 가 STEPS 에 없으면 02 출력 폴더의 `compare2.json` 이 있어야 하고 없으면 실행 전 종료 2(`etx_compare2_output_missing`).
- 두 명령은 compare2 규약을 따른다. `--out` 은 새 폴더이거나 그 명령이 만든 폴더(OWNED_NAMES 만 있음)만 허용한다. 결과는 임시 파일에 쓴 뒤 rename 으로 게시한다. 오류면 자기 소유 결과 파일을 지우고 `error.json`·`execution.json` 만 남긴다(종료 2).
- `ledger` 종료 3(partial)이면 실행기는 계속하고 `ledger-report` 는 맨 위에 "부분 결과" 띠와 사유를 보인다. 종료 2 면 실행기가 멈춘다(`CONTINUE_CODES` 그대로).
- 재시도: `STEPS = ['08']` 로 08 만 다시 돈다. `ledger` 성공 뒤 `ledger-report` 만 실패해도 08 전체를 다시 돈다.
- `ledger-report` 는 `ledger.json` 의 sha256 과 `ledger` 의 `execution.json` 상태를 `report.json` 에 기록한다. `ledger.json` 이 없거나 execution 이 error 면 종료 2 `ledger_input_missing`.
- 08 은 기본 STEPS 에 포함한다(`STEPS` 기본 = 01, 03, 02, diag, 04, 06, 07, 08).

## 12. 보고서 구성

단일 HTML(file://, CDN 0). 자료는 `<script type="application/json">`. 인쇄 모드(`beforeprint`·`#print`). 색은 07 과 같다(보라 line, 주황 pixel, 빨강 증가, 파랑 감소).

- 1절 전체 비교: vi 표(A = 100% + 원래 값), 증가 합/감소 합, 핵심 요약, 변화 종류 표, CRP 차이 줄, 곡선 보정(절편) 줄, 전극 대응 수.
- 2절: 제목 + "다음 단계에서 추가됩니다".
- 3절 전극별: 전극 장부(종류별 A → B, 변화, 그중 CNO, 상대 전극, 레이어별 펼침).
- 4절 전극 쌍별: 쌍 장부(종류별, 방식·레이어 펼침), 쌍 × 레이어 표, CNO 줄.
- 5절: 제목 + "다음 단계에서 추가됩니다".
- 6절 레이어·이물 크기: 레이어 집계, 레이어 × 이물 크기. CNO 는 density_layer 이름의 줄(analysis 레이어와 이름이 다르면 별도 줄. T4 확인).
- 7절 참고: 규칙, 용어, 제품, caa, 입력, 경고, 계산 불가 항목.
- 1단계에서 빼는 열: 방향, 분포(점형/선형), 카드. 요약 타일 연결은 3·4절로 한다.
- 상위 N + "기타 k 쌍" 합계 줄(펼치면 전체). 기본 쌍 30, 전극 30(종류별). 옵션 `--top-pairs`, `--top-electrodes`. 합계 줄은 묶어도 totals(N=1, 전체)와 같다.

구현 (T5, `src/simcompare/ledger_report.py`):

- 명령: `python -m simcompare ledger-report --ledger-dir <data/ledger> --out <새 폴더> [--top-pairs N] [--top-electrodes N]` (기본 30/30, 1 이상 정수).
- 출력: `ledger_report.html`(단일 파일, 외부 URL 0), `report.json`(`ledger.json` sha256, `ledger` execution 상태, 옵션, 절 구성), `execution.json`. 시각은 `execution.json` 에만 둔다. 같은 입력 두 번이면 HTML 이 바이트 동일이다.
- 종료 코드: `ledger.json` 이 ok 면 0. `ledger.json` 이 partial 이면 보고서를 만들고(맨 위 "부분 결과" 띠 + 사유) **3** 으로 끝난다(`compare2-report` 와 같은 규칙). 실행기는 3 을 계속 진행으로 본다.
- 오류(종료 2, `error.json` + `execution.json` 만 남김): `ledger_input_missing`(`ledger.json` 없음, `ledger` 의 `execution.json` 이 error, 또는 `error.json` 이 있는 폴더), `ledger_schema_mismatch`(`schema_version` 이 `ledger_v1` 이 아님), `ledger_report_option_invalid`(상위 N 이 1 미만), `ledger_report_output_not_owned`(이 명령이 만들지 않은 파일이 있는 폴더 — 아무것도 쓰지 않고 지우지 않는다).
- 출력 폴더 규약은 `ledger` 와 같다(새 폴더 또는 이 명령이 만든 폴더만, 임시 폴더에 쓴 뒤 rename, 오류 시 이전 결과 파일 삭제).
- 계산하지 않는다. `ledger.json` 값의 형식 변환(A 의 vi 대비 %, %p)과 상위 N 에 못 든 줄의 합("기타 k" 줄)만 한다. 보고서가 하는 표시용 합산은 둘이다. (1) "기타 k" 줄의 합. (2) `_group_sums`(변화 종류별 쌍 Δ·증가 합, 1절 변화 종류 표와 타일 ②). 둘 다 `math.fsum` 이고 `ledger.py` 와 같은 null 규칙을 쓴다. 한 부분이라도 null(대기)이면 합도 null 이고 "대기" 로 보인다(`ledger.py:73-87`). 기타 줄은 옆에 "값 대기 n 줄 포함" 을 적는다. 타일 ② 는 변화 종류 그룹의 증가 합이 null 이면 "증가 없음" 이라 단정하지 않는다. 값 있는 그룹 중 최대에 "(값 대기 포함)" 을 붙이고, 값 있는 그룹이 없으면 "대기" 를 보인다. 합계 줄은 `ledger.json` totals 그대로이고 전극 pixel 합계 줄만 `items + cno` 이다. 쌍 × 레이어 표의 칸은 그 쌍의 item Δ 를 레이어별로 더한 값이다.
- 정렬: 쌍은 `ledger.json` 순위(L#/P#) 순서, 전극은 변화(Δ) 절댓값이 큰 순(값 없음은 맨 뒤). 불량 종류를 알 수 없는 쌍은 번호 없이 이름만 나열한다.
- 표시: "없음"(그 제품에 없음) / "0%"(있고 값 0) / "해당 없음"(그 종류의 쌍·CNO 가 없는 전극, CNO 가 없는 전극의 CNO 칸) / "대기"(값 null) / "거리 필터로 0"(item 의 그쪽 값이 거리 필터로 0). `#print`·`beforeprint` 에서 모든 `<details>` 가 열린다.
- `caa` 지표는 `ledger.json` 에 없다. 7절에는 "07 보고서에서 본다" 한 줄만 둔다.
- "없음"(그 제품에 없음) / "0%"(있고 값 0) / "해당 없음"(그 종류와 무관)을 구분한다.
- line % 와 pixel % 는 합산·통합 정렬하지 않는다.

## 13. 넣지 않는 것

- 간격(spacing) 측정, "간격 +δ" 추정.
- "A 수준으로 되돌리기" 제안.
- 조치 우선순위, 신뢰도 점수, 설계 규칙(DR) 판정.
- 기능 묶음 표.
- 1단계에서 뺀 것: 2절 지도, 5절 위치 상세·범위 합계, 방향·분포 열, 가장자리 반복.
- 07 보고서와 기존 `delta.*` 의 변경.

## 14. 판정 예시 (목업 v8 의 자료)

전극 상태(목업 EL): 신규 = Scan3, Vref. 제거 = Vinit2. 나머지는 공통. 목업은 전극 목록이 complete 라고 가정한다.

| # | 쌍 | 종류 | A → B (item) | 기대 변화 종류 | 근거 |
|---|---|---|---|---|---|
| 1 | Data–ELVDD | line | 측면 SD1 .0120 → .0150 | 같은 쌍 증감 (증가) | 전극 공통, item 키 같음, Δ = +.0030 |
| 2 | Scan1–EM | line | 측면 GAT1 .0060 → 측면 GAT2 .0078 | 레이어 변경 | item 키 다름(GAT1 → GAT2) |
| 3 | Anode–Vint | pixel | 측면 PXL .0200 → .0200 | 변화 없음 | 키 같음, Δ = 0 |
| 4 | Gnode–Anode | pixel | 상하 SD1/PXL .0090 → 상하 SD2/PXL .0050 | 레이어 변경 | 위 레이어 변경(SD1 → SD2), 아래는 PXL |
| 5 | Scan2–Vint | line | 없음 → 측면 SD1 .0065 | 새 쌍 | 두 전극 모두 양쪽 제품에 있음, 쌍이 B 에만 있음 |
| 6 | Scan3–Data | line | 없음 → 상하 GAT2/SD1 .0040 | 전극 추가·제거 | Scan3 이 B 에만 있음(우선순위 1) |
| 7 | Scan3–Vref | line | 없음 → 상하 GAT2/SD2 .0025 | 전극 추가·제거 | 두 전극 모두 B 에만 있음 |
| 8 | Scan2–Vinit2 | line | 측면 GAT1 .0050 → 없음 | 전극 추가·제거 | Vinit2 가 A 에만 있음(제거) |

CNO (종류 CNO, 목업에서 K6):

| # | (전극, density_layer) | A → B | 비고 |
|---|---|---|---|
| C1 | Anode (PXL) | .0050 → .0060 | 전극 하나의 값. 변화 +.0010 |
| C2 | Gnode (SD1 → SD2) | .0020 → .0020 | 변화 0. 3절 레이어 A → B 가 다르다 |
| C3 | Vref (SD2) | 없음 → .0015 | 전극이 B 에만 있음. CNO 줄은 CNO 로 센다 |

- 목업 건수: 같은 쌍 증감 1, 레이어 변경 2, 새 쌍 1, 전극 추가·제거 3, 변화 없음 1, CNO 3(쌍 8 + CNO 3 = 11).
- 목업은 CRP 차이를 0 으로 둔다(두 제품의 CRP 가 같음).
- 판정 예시의 A·B 값은 목업의 가상 값이다. T4 의 합성 `compare2.json` fixture 가 이 표와 같은 종류를 내야 한다.

## 15. ETX 재실행 범위

- 기존 결과가 있으면 `STEPS = ['02', '08']`(02 를 새 번들로 다시). 08 만 다시는 `STEPS = ['08']`(`LEDGER_MAPS = True` 면 `ledger-maps` 가 hotspot 을 다시 읽어 02 와 비슷한 시간이 든다. 무거우면 `LEDGER_MAPS = False`). 01·03·04·06 은 필요 없다.
- 07 은 선택이다. 07 의 값은 같다. 07 `report.json` 의 compare2.json sha256 기록을 맞추려 할 때만 다시 돈다(검증하는 소비자는 src 에 없음). `docs/usage.md` 에 그대로 적는다(T6).
- Python 3.9.14(numpy 1.23.5, Pillow 10.1.0)로 bundle 을 확인한다. ETX 화면은 EUC-KR 이고 환경 변수를 넘길 수 없다.
- 실자료는 폐쇄망에만 있다. 이 호스트에서는 합성 자료와 fixture 로 확인한다.

# 2단계(unit 지도)·3단계(범위 합계) 규칙 — Run 20261116-ledger-maps (lmap)

이 아래 16절부터는 2단계와 3단계의 규칙이다. 원천은 계획 v1, v1.1, v1.2 이고 뒤가 우선한다(v1 ← v1.1 ← v1.2). 코드 사실은 기준 commit 1ce685b 를 직접 읽어 확인했다. 계획과 다른 사실은 해당 절에 "계획과 다름" 으로 적었다. 이 문서의 구현 Task 는 T2 이후다.

## 16. 개요와 흐름

- 새 명령 `ledger-maps` 가 hotspot 과 DL1 을 한 번 다시 읽는다. 결과는 `ledger_maps.json`(`ledger_maps_v2`, mrep/T3 에서 v1 → v2: 28절)과 번호 PNG 다.
- `ledger-report --maps-dir <data/ledger_maps>` 가 그 결과를 읽어 2절과 5절을 그린다. 보고서는 다시 계산하지 않는다.
- 흐름: `compare2.json`(02) + `compare2.yaml` + `ledger.json`(08) + 선택 `data/changes`(04) → `ledger-maps` → `ledger_maps.json` + PNG → `ledger-report --maps-dir`.
- 명령: `ledger-maps --compare2-dir <02> --config <compare2.yaml> --ledger-dir <data/ledger> [--candidates <data/changes>] --out <data/ledger_maps> [옵션]`.
- 출력 파일: `ledger_maps.json`, `map_<role>_<nnn>_<el|it>.png`, `execution.json`(시각은 여기에만). 출력 폴더 규약(새 폴더 또는 이 명령이 만든 폴더만, 임시 폴더에 쓴 뒤 rename)은 11절과 같다.
- 설정값은 CLI 옵션으로만 준다. compare2.yaml 에 넣으면 config sha256 이 바뀌어 02 를 다시 돌려야 한다.
- 값은 02 와 같은 코드 경로에서 얻는다. `linepixel._ledger_kind`(`linepixel.py:552`)에 선택 인자 sink 를 단다. sink 는 **관찰만** 한다. 기존 `terms → math.fsum` 계산은 그대로다. 호출 지점은 `im = full & keep` 이 확정된 뒤(`linepixel.py:579-593`)다. 기본 None 이면 02 결과는 바이트 동일이다.
- sink 는 `compare2._load_side`(`compare2.py:239`)에 `share_sink=None` 인자를 더해 `compute_areas`(`compare2.py:258` 호출)로 넘긴다. 1ce685b 에는 `share_sink` 가 아직 없다(`git grep share_sink src` 결과 0).
- 07(`compare2_report.py`)·`compare2_link.py`·`ledger.py` 의 값과 출력은 바꾸지 않는다. 07 의 `_side_info`·`_FrameInfo`(`compare2_report.py:277-305`)와 input_hash 대조 문구(`compare2_report.py:367-374`)는 import 해서 쓴다. `_scan_side`(`compare2_report.py:352`)는 쓰지 않는다(한 지름·선택 조건 전용, 몫 없음).
- real.yaml 을 여는 방식(코드 사실 d): 07 은 `_side_info`(`compare2_report.py:287-305`)로 `cfg[role]` 의 `real_hotspot_v1` 소스(`pipeline._real_source`)와 frame 명세를 읽는다. 마스크는 `_scan_side` 안에서 `pipeline._configured_real(cfg, info['section'], info['real'], {'bucket_detail': 'summary', 'top_buckets': ...})`(`compare2_report.py:361-363`, 정의 `pipeline.py:33-44`)로 연다. `condition_assignment` 가 없으면 `None` 이고 `compare2_condition_assignment_required` 오류다(`compare2_report.py:364-365`). `ledger-maps` 는 같은 호출을 쓴다. 02 와 같은 몫을 얻으려면 02 의 `_load_side` 처럼 `require_distance=True` 로 연다(`compare2.py:249`). 계획 v1 은 이 차이를 적지 않았다.
- input_hash 대조: `compare2.json` 의 `input_hash` 와 지금 자료의 `dataset.provenance['input_hash']` 가 다르면 `compare2_input_hash_mismatch`(`compare2_report.py:367-374`). `ledger-maps` 도 같은 문구로 멈춘다(종료 2 `ledger_maps_input_missing` 이 아니라 07 과 같은 코드를 쓸지는 T3 가 정하고 이 표에 적는다).
- ETX 08 확장(T7 구현됨): `ledger` → `ledger-maps` → `ledger-report --maps-dir`. 실행기 상수 `LEDGER_MAPS`(기본 True, 스위치), `LEDGER_MAPS_DIR`(`data/ledger_maps`), `LEDGER_MAPS_ARGS`(문자열 목록, 설정 이름 `ledger_maps`, `ledger_maps_dir`, `ledger_maps_args`). 04 출력(`data/changes`)은 07 처럼 `'04' in steps` 이거나 폴더가 있으면 `--candidates` 로 넘긴다. `LEDGER_MAPS=False` 면 08 은 작업 2개(`ledger`, `ledger-report`)이고 변경 위치·지도·카드가 없다. mrep/T9: `ledger-report` 에는 두 경우 모두 `--report07-dir <REPORT_DIR>`(07 출력 폴더, 새 상수 없음)를 넘긴다. `ledger-maps` 종료 3 → 실행기 계속, 종료 2 → 멈춤. 재실행은 새 번들로 `STEPS=['08']` 이다(compare2.json 이 바뀌지 않으므로 02 는 필요 없다).

## 17. 값

- item 값 = s_kind × λ_item. 종류별 기울기 s 는 장부와 같다. pixel 의 s 에는 이미 B 의 CRP 가 들어 있다(`ledger.py:348-362, 497-525`). 타일 값에 CRP 를 다시 곱하지 않는다.
- 셀 몫은 `밀도 × 계수 × pitch²`(`compare2.py` 의 `lam` 과 같은 식) × 1/(n·m) 이다. n·m 은 장부와 같은 값이다(`linepixel.py:579-593`). 지름은 장부와 같은 범위(`lambda_diameters_um`) 전체다. 거리 필터로 0 이 된 item·side 는 장부와 같게 0 이다.
- ledger item 값 키(코드 사실 g): `ledger.json` `pairs[].items[]` 의 `item_id`, `lambda_a`, `lambda_b`, `a`, `b`, `delta`, `presence`, `filtered_out`, `a_filtered`, `b_filtered`(`ledger.py:354-362`). 종류별 item 합은 `totals.<kind>.items` 의 `a`·`b`·`delta` 이다(`ledger.py:508, 525`, `_Tri.out` 은 `ledger.py:89-102`). 경로는 `totals.line.items.a` / `totals.line.items.b` / `totals.pixel.items.a` / `totals.pixel.items.b` 로 고정한다.
- **공간에 넣지 않는 것**: 곡선 보정(절편) 줄 `totals.<kind>.intercept`, CRP 줄 `totals.pixel.crp`, CNO `totals.pixel.cno`. CNO λ 는 CSV 합 기준이라 셀로 나눌 수 없다(`compare2_link.py:574-577`). 지도 문구는 "CNO 는 지도가 없어 이 목록에 없습니다"를 유지하고 5절에 "pixel 범위 합계는 CNO 제외" 를 적는다. 1단계 값은 그대로다.
- 검산(`math.fsum`, 상대 1e-9). 값이 null(대기)인 계열·item 은 0 이나 passed 로 보지 않고 `not_evaluated` 로 적고 개수를 센다.
  1. item 전체 타일 합 = `ledger.json` 의 그 item 값(A 쪽 `a`, B 쪽 `b`). 대기 item 은 제외하고 개수를 기록한다.
  2. 종류별 전체 타일 합 = `totals.<kind>.items.a` / `totals.<kind>.items.b`. 절편·CRP·CNO 는 이 합에 없다(`not_evaluated` 가 아니라 제외로 명시).
  3. 줄마다 범위 합계 = 이 쌍 + 다른 쌍 전부(같은 최종 집계 타일 집합 위에서).
  4. 제품별 Σ(줄의 이 쌍 값, 최종 집합) ≤ 관찰 영역의 이 쌍 값 ≤ 4절 쌍 값(타일 값 ≥ 0, 상대 1e-9). 최종 집합이 서로 겹치지 않으므로 앞 부등식은 포함 관계다.
  5. 분할 검산(18절 G1 5단계): B 의 `{T_i} ∪ {U_j}` 는 서로 겹치지 않고 합집합 = `∪T_i ∪ ∪S_B,j`. A 의 대응 줄과 A 전용 줄 = `{S_A,i}` 전체. 누락·중복 없음.
  6. (mrep/T3) 줄의 item 값 합 = 그 줄의 이 쌍 값(`rows[].items[].value` 의 `math.fsum` = `rows[].pair`, 상대 1e-9). `checks.row_items`.
- 검사 3–6 은 **끝까지 완료된 카드**만 센다. 예산 때문에 빠진 카드는 3–6 의 집계와 `cards` 어디에도 없다(28.4).
- 검사 실패(통과하지 못함)는 종료 3 `ledger_maps_value_check_failed` 다. 보고서는 부분 결과 띠를 보인다.
- 수치 검산(상대 1e-9)과 바이트 동일은 따로 확인한다(T2). "sink 있음 = 없음"은 `tests/test_linepixel_share_sink.py` 가, "sink 없음 = 기준(1ce685b 결과의 `compare2.json`·CSV sha256)"은 기존 GOLDEN 테스트가 맡는다.

## 18. 저장 구조, 메모리 예산, 창, 줄

### 18.1 저장 구조

- 종류별 합계 격자: 제품·종류마다 frame 전체 dense float64 타일 배열 1개(기본 1 µm 타일).
- item 희소 값: item 마다 (타일 색인 int32, 값 float64) 정렬 배열. `np.unique(tile_idx, return_inverse=True)` + `np.bincount(inverse)` 로 만든다(항목 수 비례, Python dict 금지).
- membership(분석용): item 별 희소 타일 집합. 지도 지름 `report.diameter_um` 의 셀이 하나라도 있는 타일이다. 가림이 없다. 연결 성분·선형 판정·반복 Dice·좌표 대응 겹침은 모두 membership 으로 계산한다.
- 번호 PNG(표시·hover 전용): 모든 item 을 넣는다. 번호 16비트 = R 하위 8비트, G 상위 8비트. gAMA·sRGB·iCCP 청크 없이 쓴다. B 는 0. `--top-pairs` 는 목록·라벨 수만 줄인다. 한 픽셀에 item 하나이고 우선순위는 line > pixel, 같은 종류는 순위가 앞선 쌍이다. 가려진 픽셀 수 `hidden_px` 를 item 에 기록하고 범례에 한 줄 적는다. 레이어별 전극 번호 그림도 같은 형식이다. 번호 → 이름 표는 JSON 에 둔다.
- 카드 대상은 `ledger.json` 에서 고르며 지도 순위와 무관하다. v1 은 종류별 |Δ| 상위뿐이었고 **v2 는 탭 3개(같은 불량 증감 / 불량 구성 변경 / 전체)의 합집합**이다(28.1).
- 한 제품씩 차례로 처리한다. 한 제품의 희소 항목은 그 제품의 카드·검산을 낸 뒤 버린다. 다만 A→B 대응에 필요한 카드 쌍의 A 쪽 줄 타일·membership 은 B 처리 때까지 남긴다(카드 쌍만). 남긴 자료는 카드마다 따로 예약하고(`_Hold`) 그 카드가 B 까지 끝나면 반환한다(28.4).
- 분석 타일 크기 `--tile-um` 은 JSON 과 보고서 1절 조건 표에 적는다. 허용 [0.5, 2.0], 기본 1.0.

### 18.2 메모리 예산

- 예산 하나: `max_operation_bytes`(`_side_info` 의 `limit`, `compare2_report.py:305`, 기본 `DEFAULT_MAX_OPERATION_BYTES`)를 이 명령 전체의 동시 보유 상한으로 쓴다. `--max-tile-entries` 는 보조 상한이다.
- 회계 객체 `_Budget`: `reserve(nbytes, what)` / `release(nbytes, what)`. 큰 할당(추정 ≥ 1 MB) **전에** `reserve` 를 부른다. 넘으면 할당하지 않고 `oversized_real_operation`(종료 2, 출력 파일 쓰지 않음, 메시지에 `what`·추정·상한)을 낸다.
- 회계 대상 6가지:
  1. 기존 `compute_areas` 보유량 추정. 기존 가드의 추정식은 `adapters/real_hotspot.py:1119-1151` `estimate_area_bytes`(masks + output + group + history + payload)이고 호출·비교는 `real_hotspot.py:1749-1754`(`estimated_peak_bytes > max_operation_bytes` → `oversized_real_operation`)다. 시작 때 같은 식으로 예약한다. 계획 v1.2 는 `linepixel.py` 의 가드를 가리켰으나 **계획과 다름**: `linepixel.py` 에는 가드가 없고(`oversized_real_bucket_output`·`oversized_real_label_patterns` 만 `linepixel.py:344-353`) 위 위치가 맞다.
  2. 종류별 dense 합계 격자.
  3. 표시용 번호 그림·전극 그림.
  4. item 희소 값·membership 완성 배열과 정렬·병합 사본. 병합(`_merge_sparse`)은 할당 전에 **동시 생존량 최대치**를 예약한다: 1단계(타일·값 이어 붙이기 + `np.unique(return_inverse)` 의 argsort·inverse·cumsum 임시 + `bincount` 결과)는 항목 수 × `MERGE_BYTES_PER_ENTRY`(64 B, 측정 49 B), 2단계(membership 이어 붙이기·정렬)는 항목 수 × `MERGE_MEMBER_BYTES_PER_ENTRY`(40 B). 청크는 호출자가 이미 예약해 두었으므로 원본 + 병합 결과 + 임시가 모두 예산에 들어간다. 검산의 합(`math.fsum`)은 배열 조각(65,536개)씩 넘겨 전체 배열의 Python list 를 만들지 않는다(값은 같다).
  5. sink 호출마다의 임시 배열(index·몫·`unique`/`bincount` 결과).
  6. B 처리 중 남겨 둔 A 카드 자료(mrep/T3: 카드마다 `_Hold` 로 예약, B 읽기 전·중에 부족하면 순위 낮은 카드부터 버림: 28.4).
  7. 카드 단계 임시: 성분 라벨링(창 bool 그림 + 성분당 64 B, `COMPONENT_BYTES_PER_MEMBER`), 창 색인 배열·관찰 영역 합집합(`IDS_UNIQUE_BYTES_PER_ID` × (창 타일 수 + 줄 타일 수))과 쌍 희소 값 합계. 모두 만들기 전에 예약하고 끝나면 반환한다.
- `--max-tile-entries`(기본 40,000,000 ≈ 480 MB)는 item 희소 항목 합의 상한이다. 읽는 중 넘으면 즉시 멈추고(종료 2 `oversized_real_operation`, 조치 `--tile-um` 키우기) 출력 파일을 쓰지 않는다. 표시 옵션(`--map-px-um` 등)으로 줄지 않는 비용이다.
- 읽기 전 가드 `guard_stage_ledger_maps`: frame 셀 수·item 수·레이어 수로 dense 배열 크기를 추정해 마스크를 읽기 전에 비교한다. `compute_areas` 기존 가드는 그대로 둔다. `execution.json` 에 최대 예약량 `max_reserved_bytes` 와 단계별 최대 예약량 `max_reserved_by_stage`(28.5)를 기록한다.

### 18.3 창과 타일 격자 (G6)

- unit 한 장 창: U_px = round(U_um / pitch), 창 원점 = ((W−U_px)//2, (H−U_px)//2)(frame px, 축별). U_px ≥ frame 이면 창 = frame. U_um 은 `_side_info` 의 `unit_um`(설정 `product.unit_size_um`, 없으면 유도)이다. JSON 에 frame 물리 원점·pitch·U 출처(설정/유도)를 같이 남긴다.
- **`UnitGeometry` 의 unit 번호와 다르다**(코드 사실 f). `_side_info` 의 유도식은 `unit_um = ((nx + UNIT_MARGIN_PX)/2 × pitch, (ny + UNIT_MARGIN_PX)/2 × pitch)`, `UNIT_MARGIN_PX = 2 × BORDER_PX = 1000`(`compare2_report.py:50-51, 296-303`)다. `layer_align.UnitGeometry` 는 `unit_px = ((h + 2·border)//2, (w + 2·border)//2)`(`layer_align.py:44-48`)로 같은 크기식이지만 정수 나눗셈이고 홀수 크기를 `unit_size_odd_px` 로 표시한다. 결정적 차이는 위치다. `UnitGeometry` 는 frame 을 경계 `unit_px − border` 에서 2×2 영역으로 나눈다(`layer_align.py:59-69`). 창은 frame **중앙의 위상 이동된 한 주기 창**이라 이 영역과 경계가 맞지 않는다. 그래서 창 번호를 unit 번호로 부르지 않는다.
- 타일 격자는 **제품별**이다. 제품 r 의 타일 0 은 그 제품 frame 의 픽셀 (0,0), 물리 원점 g_r(µm)이다. 타일 k 는 물리 구간 [g_r + k·tile, g_r + (k+1)·tile).
- A 점의 B 물리 좌표 = A 물리 좌표 + d_i. **d_i 는 `_shift_um`(`change_candidates.py:322-326`)이 준 물리 이동량 = (B 원점 − A 원점) + offset_px × pitch 다. frame 원점 차이가 이미 들어 있다**(코드 사실 b). 그러므로 g_B − g_A 를 한 번 더 빼거나 더하지 않는다. 이동 색인은 G6 식 `k_B = k_A + round((g_A + d_i − g_B) / tile)` 이다. 이 식은 `d_i` 의 원점 차이 항과 `g_A − g_B` 가 서로 상쇄되어 `k_B = k_A + round(offset_px × pitch / tile)` 이 된다(offset 은 04 가 정한 픽셀 이동). **구현(T8)**: 부동소수로 원점을 더하고 빼면 반 타일 근처에서 틀린 쪽으로 반올림한다(예: g_A=0, g_B=0.8, pitch 0.1, offset 15 px 이면 `g_A + d − g_B = 1.4999999999999998`). 그래서 상쇄를 **먼저** 하고 `k_B − k_A = round_half_away(offset_px / tile_px)` 를 `fractions.Fraction` 으로 정확히 계산한다(정확한 반 타일은 0 에서 먼 쪽: 1.5 → 2, −1.5 → −2). `rounding_error_um` = `(이동 × tile_px − offset_px) × pitch`(픽셀 차이가 정확하므로 오차도 정확하다). `d_i` 정의(원점 차이를 이미 포함)가 바뀌면 이 절과 `_tile_shift` 를 함께 고친다. fixture: A 원점 0 µm, B 원점 10 µm, 픽셀 offset 0 이면 기대 이동 색인 0(원점 차이를 두 번 세거나 빠뜨리면 실패).
- 계획과 다름 아님: 계획 v1 은 "R_B = R_A + offset + 원점 차이" 라고 적었고 코드와 같다.

### 18.4 줄 (선형/점형)

- 줄 후보는 창 안으로 자른 membership 의 4-이웃 연결 성분에서 만든다(`electrode_map._label_runs` 재사용).
- 분류: 선형 성분 = bbox 긴 변 ≥ `--linear-min-length-um`(50) 이고 긴/짧은 변 ≥ `--linear-min-aspect`(4). 쌍이 선형 = 선형 성분 셀이 절반 이상. 대각선 배선은 점형으로 분류된다(한계).
- 선형 구간: 긴 축을 `ceil(L / --segment-um)` 등분하고 `--max-rows` 이하로 한다. 구간 사각형은 **짧은 축 방향과 성분 양 끝에서만** `--range-um` 넓힌다. 이웃 구간 사이 경계는 자른 선 그대로다.
- 점형: 사방으로 `--range-um` 넓히고 넓힌 사각형이 겹치는 성분끼리 한 위치로 묶는다. 값 큰 순 `--max-rows` 개 + "그 밖 k 곳". 선형 쌍의 점형 성분은 점형 줄로 뒤에 붙인다.
- 범위는 frame 좌표 그대로 센다(창 밖이어도 frame 안이면 센다). 사각형은 타일 경계로 바깥 맞춤한다.
- **타일 소유**: 한 카드 안에서 각 타일은 줄 하나에만 속한다. 줄 순서(선형 구간 → 점형 위치 값 큰 순)대로 먼저 차지한 줄이 갖는다. 뺏긴 타일 수를 줄마다 기록한다.
- 가장자리 반복(결정 11, **T8 에서 타일 단위 주기 매핑으로 바꿈**; Lead 결정, 결정 11 "같은 위치의 안쪽 반복" 의 해석): 줄 범위의 타일 중 **frame 밖 타일만** 한 주기 U(타일 단위 `ux`, `uy`; 창 크기 / 타일을 반올림, 오차는 `edge_rounding_um` 에 기록하고 `--coord-tolerance-um` 를 넘으면 그 축은 주기 없음)만큼 옮긴 frame 안 타일(이미지 타일)로 바꾼다. frame 왼쪽·위쪽 밖 타일은 +U, 오른쪽·아래쪽 밖 타일은 −U 이고 두 축 모두 밖이면 두 축 모두 옮긴다. 옮겨도 frame 밖이면 줄은 `excluded`(`edge_no_repeat_position`). 줄의 최종 집합 = frame 안 원래 타일 ∪ 이미지 타일이다(겹치면 한 번만 센다). 이미지 타일도 타일 소유에 같이 들어가므로 다른 줄과 겹치면 앞 줄이 갖는다. 통과 줄은 `edge: repeat_used` 이고 `image_tiles`(이미지 타일 수)·`image_shifts` 를 기록한다. 이전의 "범위 전체를 ±U 옮긴 복사본" 규칙은 중앙 창(frame = U + 2m)에서 복사본이 반대편 가장자리를 넘어 거의 쓸 수 없었다(R2).
  동등성(같은 주기 가정이 맞는지): 바뀐 타일마다 **원래 이웃 띠** = 가장자리에서 밖으로 나간 폭만큼의 frame 안 띠(다른 축은 줄 범위 전체)와, 그 띠를 같은 ∓U 로 옮긴 띠를 비교한다. 이웃 띠가 하나라도 아래 조건을 못 지키면 줄은 `excluded` 다. (1) 관련 레이어 타일 전극 격자 일치율 ≥ `--edge-repeat-min-match`, (2) 그 띠 쌍의 membership 타일 Dice ≥ 같은 값. DL1 이 없으면 `edge_no_dl1`, 둘 다 비교할 값이 없으면(두 띠에 전극이 없거나 membership 이 둘 다 비어 있음) Dice·일치율을 1 로 치지 않고 `edge_unverifiable`, 기준 미달이면 `edge_mismatch`. `--tile-um` > `--coord-tolerance-um` 이면 `coords_resolution_too_coarse`(G4). 아니면 `edge: excluded`(빗금, 비교 제외, 줄 값 없음). 창 밖 불량 영역은 지도에 그리지 않는다.
- "그 밖 k 곳"(목록에 못 든 위치·선형 구간)의 값 = 줄을 배치한 **뒤**(목록 밖 상자 합집합 − 목록 줄이 가진 타일) 위의 이 쌍 값이다. 겹치는 타일을 한 번만 센다(`others_free_value`).
- **최종 집계 타일 집합 (G1)**. 사각형은 표시용이고 값·검산·관찰 영역은 최종 집합으로만 계산한다. 한 카드(한 쌍)에서 다음 순서로 확정한다.
  1. A 줄 집합: A 창·형상에서 줄 사각형 → 가장자리 반복(frame 밖 타일을 이미지 타일로) → 줄 순서대로 타일 소유 → `S_A,i`(서로 겹치지 않음).
  2. 대응 이동: 대응 조건(19절)을 통과한 A 줄은 G6 식으로 `T_i = S_A,i + round((g_A + d_i − g_B) / tile)` 을 만든다(반올림 오차를 JSON 에 기록). `T_i` 가 B frame 을 벗어나면 대응하지 않는다.
  3. B 쪽 충돌 처리: A 줄 순서대로 `T_i` 를 확정한다. `T_i` 가 이미 확정된 `T_k`(k<i)와 타일이 하나라도 겹치면 줄 i 의 대응을 **거부**한다(그 A 줄은 "있는 쪽 값만" 표의 A 줄). 대응 줄은 A·B 양쪽 모두 서로 겹치지 않고, A 값 = `S_A,i` 합, B 값 = `T_i` 합이다.
  4. B 줄 집합: B 창·형상에서 줄 사각형 → 가장자리 반복 → 타일 소유 → `S_B,j`. 그다음 `U_j = S_B,j − ∪T_i`. 비어 있지 않으면 "있는 쪽 값만" 표의 B 줄이다(대응 부분을 뺐으면 "대응 부분 제외" 표시). 비어 있으면 대응 줄에 모두 포함된 것으로 기록한다.
  5. 분할 검산(검산 5): 17절 5번.
  6. 관찰 영역: 제품별 `O_A = 창_A ∪ ∪S_A,i`, `O_B = 창_B ∪ ∪T_i ∪ ∪U_j`(타일 집합).
  7. 검산 3: 줄마다 범위 합계 = 이 쌍 + 다른 쌍 전부.
  8. 검산 4: 17절 4번.
  9. 04 가 없거나 대응이 꺼진 경우: 2–3 단계를 건너뛰고 A·B 각각 `S_A,i`, `S_B,j` 를 그대로 "있는 쪽 값만" 줄로 쓴다.
- 줄 값: 이 쌍 = 범위 안 이 쌍 타일 합(최종 집합). 다른 쌍 = 범위 안 타일 합(상위 3 + "외 k 쌍"). 범위 합계 line/pixel = 그 종류 모든 쌍 합(CNO 제외).
- 반례 fixture(T4): (a) A 에서 겹치지 않는 두 줄이 서로 다른 offset 으로 B 에서 겹침 → 뒤 줄 대응 거부, (b) B 줄 100타일 중 1타일만 대응 집합과 겹침 → 나머지 99타일이 B 전용 줄로 남음, (c) 타일 소유로 줄어든 A 줄의 값 = 최종 집합 합(사각형 합 아님), (d) 검산 5 의 분할 확인, (e) 선형 300 µm + `--segment-um 70` → 구간 5개·경계 60 µm 간격·구간별 값 = 기대값, (f) 점 8곳 + `--max-rows 6` → 6줄 + "그 밖 2곳".

## 19. 좌표 대응

줄은 제품별로 만든다(A 줄은 A 창·형상, B 줄은 B 창·형상). A 줄을 B 에 대응시키려면 04(`candidates-change`) 출력이 필요하다.

### 19.1 04 출력 구조 (코드 사실 a, 확인)

| 항목 | 위치 | 근거 |
|---|---|---|
| 전역 offset | `alignment.json` `offset_xy_px`(`[dx, dy]`, int), `pitch_um` | `change_candidates.py:1643-1644, 1657` |
| unit offset | `alignment.json` `unit_offsets[]`(`unit_align_um` 를 줬을 때만): 각 항목 `region_px = [y0, y1, x0, x1]`(reference px, **끝 미포함**), `offset_xy_px = [dx, dy]`(**x 먼저**), `applied`(bool), `offset_xy_um`, `delta_xy_px`, `unit_index_xy = [x, y]` | `change_candidates.py:1150-1163`. 정렬 `order`·`regions` 는 `UnitGeometry.unit_regions()`(`layer_align.py:63-69`), `alignment_doc.update(unit['doc'])` 는 `change_candidates.py:1700`. **T4 확정**: `unit_offsets` 는 `unit['doc']` 의 키다(`change_candidates.py:1165` `doc = {'layer_global_check': global_doc, 'unit_offsets': units, ...}`)이고, `alignment.json` 최상위로 합쳐진다(`:1700`). 항목 키 `region_px`·`offset_xy_px`·`unit_index_xy`·`applied` 는 `:1159` `record.update(...)` 와 같다. offset 은 적용 안 된 unit 도 전역 offset 이다(`:1153`) |
| stretch | `alignment.json` `stretch.path = 'stretch_map.json'`(요약), 본문은 `stretch_map.json` | `change_candidates.py:1244-1254, 1704, 1752` |
| stretch 타일 | `stretch_map.json` `tiles[]`: 레코드마다 `applied`, `offset_source`, offset 은 `field` 튜플 `(y0, y1, x0, x1, dx, dy)` 와 같은 값. 조회 `lookup(y, x)` 가 `((dx, dy), offset_source, 타일 색인)` | `change_candidates.py:1238-1243`. **T4 확정**: `stretch_map.json` 의 키는 `tiles`(`change_candidates.py:1254` `'units': unit_docs, 'tiles': records`)이고 파일은 `:1752` 가 쓴다. 레코드 키는 `layer_align.py:516-521`(`unit_index_xy`, `tile_index_xy`, `region_px = [y0, y1, x0, x1]`, `applied`)와 `:563-566`(`offset_xy_px = [dx, dy]`, `offset_source`)이다. 적용 안 된 타일도 `offset_xy_px` 는 시작 offset 이라 field 칸으로 쓴다 |
| frame 크기 | `alignment.json` `frame_shape_reference_yx`, `frame_shape_target_yx`, `frame_anchor = 'top_left_index'` | `change_candidates.py:1602-1613, 1741` |
| 입력 지문 | `candidate_queries_v1.json` `inputs.config_sha256`, `inputs.reference/target`(`condition_assignment_sha256`, `asset_manifest`, `electrode_import_record` 등 `input_record` 와 `product_ids`), `inputs.cnt.<reference|target>.product_id` | `change_candidates.py:2128-2132`, `candidates.py:200-224` |
| 적용 모드 | `candidate_queries_v1.json` `change_args.apply_align` ∈ {`diagnostic`, `unit`, `local`, `stretch`}. 키가 없으면 모드 없음 | `change_candidates.py:66, 759-760, 2175-2180, 2189` |

- 물리 이동량 = Δorigin + offset_px × pitch(`_shift_um`, `change_candidates.py:322-326`). 18.3 참고.
- 계획과 다름: 계획 v1.1 D1 은 `stretch.path → stretch_map.json.tiles[]` 라고만 적었다. 코드의 `alignment_doc['stretch']` 는 요약(`n_tiles`, `n_applied` 등)이고 `path` 는 `'stretch_map.json'` 이다(`change_candidates.py:1244-1247`). 맞다.
- 계획과 다름: `candidates-change` 에서 `unit` 모드에는 `--unit-align-um` 이 필수이고(`change_candidates.py:147-148`), `local` 에서 `--unit-align-um` 이 없으면 전역 offset 이 시작점이다(`:1727`).

### 19.2 모드 판별과 field (G3, D2)

- 모드는 `candidate_queries_v1.json.change_args.apply_align` 에서만 읽는다. `alignment.json.args` 와 `unit_offsets[].applied` 로 판별하지 않는다(`unit_offsets[].applied` 는 진단 단계에서도 참일 수 있다).
- 모드별 field: `stretch` → `stretch_map.json.tiles[]` 최종 offset. `unit` → `alignment.json.unit_offsets[]`. `local` + unit 정렬 있음 → unit field. `local` + unit 정렬 없음 → 전역 offset. `diagnostic`·모드 없음 → 대응하지 않는다(띠 `coords_not_applied`). 이 선택은 04 의 `use_field`·`active_field` 와 같다(`change_candidates.py:1727-1729`).
- `local` 의 ROI 별 추가 보정(`roi_local_alignment.json`, 변경 지도 생성 뒤 적용, `change_candidates.py:1541-1563, 1754-1767`)은 쓰지 않는다. "변경 지도 기준 field 만 쓰며 ROI 별 최종 보정과 다를 수 있음"을 문서와 보고서에 적는다.
- 04 가 실제로 적용한 field 만 쓴다. 진단만 한 offset 은 새로 채택하지 않는다.
- 모드나 필요한 field 자료를 확인할 수 없으면 대응하지 않는다(띠 `coords_unavailable`).

### 19.3 지문·pitch 확인 (코드 사실 c)

- 04 의 제품 이름(`inputs.cnt.<role>.product_id`), frame 크기(`frame_shape_reference_yx`·`frame_shape_target_yx`), `pitch_um`, 설정 해시(`inputs.config_sha256`, `inputs.reference/target.condition_assignment_sha256`)가 현재 자료와 같아야 한다(**추가 조건: 아래 입력 지문도 같아야 한다**). 다르면 `coords_stale`, 키가 없거나 다시 계산할 수 없으면 `coords_unavailable`(대응 끔). 계획 v1.1 D3 에 맞다.
- **입력 지문 대조(T8, C2)**: 설정 해시는 설정 파일이 같다는 뜻일 뿐이라 자료 파일이 바뀐 04 를 걸러내지 못한다. 04 가 `candidate_queries_v1.json` `inputs.<role>` 에 기록하는 지문(`candidates.py:233-237` `prepare_side`, `change_candidates.py:2128-2132` 가 `inputs` 로 옮김) 가운데 ledger-maps 가 **현재 입력에서 같은 방식으로 다시 계산할 수 있는 것**을 모두 대조한다. 제품마다 (`ledger_maps.py` `_current_input_fingerprints`, `_current_asset_fingerprints`):

  | 04 키 | 현재 값 계산 | 비고 |
  |---|---|---|
  | `inputs.<role>.manifest_sha256` | real hotspot 폴더(`data_dir`, `manifest_path`)의 manifest 파일 sha256 | `candidates.py:235` 와 같은 파일·같은 방식 |
  | `inputs.<role>.dataset_input_hash` | `real_hotspot.configured_input_hash(root, manifest, condition_assignment, config_dir)`(마스크를 읽지 않음) | `candidates.py:236`(`provenance['input_hash']`). manifest 원본 해시 + 배정 + pair 표 + 거리 표 |
  | `inputs.<role>.asset_manifest.sha256`, `inputs.<role>.electrode_import_record.sha256` | `pipeline._electrode_evidence` 가 받아들인 두 파일의 sha256 | `candidates.py:210-225`. 04 가 이 sha256 을 기록한 경우에만 대조한다(설정에 전극 asset 이 없으면 기록 자체가 없다) |

  - 기록된 지문이 하나라도 현재 값과 다르면 `coords_stale`(사유에 키·04 값·현재 값). 기록된 지문을 지금 다시 계산할 수 없으면 `coords_unavailable`.
  - 제품마다 대조에 성공한 입력 지문이 **하나도 없으면**(키가 없음 = 04 가 기록하지 않음) `coords_unavailable` 이다. 설정 해시·제품명·frame·pitch 가 모두 같아도 입력 지문 없이는 켜지 않는다.
  - 대조하지 **않는** 지문과 이유: `inputs.cnt.<role>.files_sha256`·`inputs.layers`(CNT·Layer PNG 파일 목록 해시)는 04 CLI 인자 `--cnt-from-dl1-root` 로 찾은 파일의 목록이고 그 root 를 04 가 기록하지 않아(`change_args` 에 없음, `change_candidates.py:2160`) ledger-maps 가 같은 목록을 만들 수 없다. `inputs.<role>.export_sha256`·`mask_derivation_sha256` 은 04 가 만든 파일(그 폴더의 것)의 해시이고 입력이 아니다.
  - 실자료: 04 는 `prepare_side` 에서 real 이든 numeric 이든 `manifest_sha256`·`dataset_input_hash` 를 항상 기록하므로(`candidates.py:233-237`) 실자료에서 대응이 항상 꺼지지는 않는다. 이 키를 기록하지 않은 04 폴더(이 저장소의 `prepare_side` 이전 코드로 만든 것 등)만 `coords_unavailable` 이 된다. 대응을 실제로 켤 수 있는지는 폐쇄망 실행에서 확인한다(ETX 04 가 같은 버전의 코드로 만들어진 경우).
- A·B pitch 가 다르면 대응하지 않는다. 04 도 거부한다(`candidates_change_pitch_mismatch`, `change_candidates.py:712-714`).
- `--tile-um` > `--coord-tolerance-um` 이면 좌표 대응과 가장자리 동등성 판정을 하지 않는다(띠 `coords_resolution_too_coarse`, 가장자리 줄은 `excluded`). 허용치는 타일 수 `ceil(tol / tile)` 로 바꿔 쓴다.
- 04 결과 폴더가 없으면 대응 판정 없이 "있는 쪽 값만" 으로 보이고 맨 위 띠 1줄이 선다(D4).

### 19.4 이동량 선택과 대응 조건

- 범위와 겹치는 **모든 field 칸**(unit 영역·stretch 타일)의 offset 을 모은다. 모두 서로 `--coord-offset-spread-um` 이내여야 한다. 쓰는 이동량 = 범위와 겹친 면적이 가장 큰 칸의 offset(같으면 색인 작은 쪽). offset 순서는 `[dx, dy]` 이고 영역은 `[y0, y1, x0, x1]` 이다.
- 대응 조건: (1) offset 출처 상태 ok, (2) 위 spread 통과, (3) `T_i` 가 B frame 안, (4) 그 쌍의 A membership(이동 후)과 B membership 이 `--coord-tolerance-um` 허용으로 겹치는 비율 ≥ `--coord-min-overlap`. 충돌 처리는 18.4 의 G1 3단계다.
- 비교는 change_kind 가 같은 쌍 변화·변화 없음·**불량 구성 변경**(`layer_change`)이고 양쪽에 있는 item 이 1개 이상인 쌍만 한다(mrep/T3: 구성 변경 쌍은 양쪽에 있는 item 으로 대응, 28.2). 양쪽에 있는 item 이 없으면 `no_common_item`. 그 밖(B-only·A-only·전극 추가·삭제 쌍)은 "있는 쪽 값만" 표에 side 를 밝혀 넣는다. v1 은 구성 변경 쌍을 대응하지 않고 양쪽 위치·값을 각각 보였다.
- 04 없음·진단 모드·stale 이면 모든 줄이 제품별 "있는 쪽 값만" 이다(A 사각형을 B 에 그대로 쓰지 않는다).
- 띠 코드: `coords_not_applied`, `coords_stale`, `coords_unavailable`, `coords_resolution_too_coarse`, 그리고 `--candidates` 를 주지 않았을 때의 `coords_no_candidates`(T4 가 더함, D4 의 "맨 위 띠 1줄"). `doc['bands']` 는 `{code, detail}` 목록이다. 카드 `match.reason` 은 대응을 못 한 이유를 같은 코드로 적는다.
- T4 가 정한 세부(계획에 없던 부분):
  - **spread**: field 칸 offset 의 서로 차이는 축별 최댓값(체비쇼프, `max(|Δdx|, |Δdy|)` × pitch)이 `--coord-offset-spread-um` 이내여야 한다. 초과 → 줄 사유 `offset_spread`. 겹치는 칸이 없으면 `no_field_cell`.
  - **범위로 쓰는 박스**: 줄의 최종 사각형(`fbox`, 타일 단위)을 reference px 로 바꿔 field 칸과 겹침 면적을 센다.
  - **지문 비교**: `inputs.config_sha256` = 현재 numeric config 파일의 sha256, `inputs.<role>.condition_assignment_sha256` = `candidates._canonical_sha(condition_assignment)`, `inputs.cnt.<role>.product_id` = `condition_assignment.product_id`, `alignment.json` `frame_shape_reference_yx`·`frame_shape_target_yx`·`pitch_um`, 그리고 위 19.3 의 입력 지문(`manifest_sha256`, `dataset_input_hash`, 전극 asset sha256). 값이 다르면 `coords_stale`, 키가 없거나 다시 계산할 수 없으면 `coords_unavailable`.
  - **대응 줄 거부 사유**: `b_conflict`(앞 줄 T 와 겹침), `outside_b_frame`, `low_membership_overlap`(대응 비율 < `--coord-min-overlap`), `offset_spread`, `no_field_cell`.
  - 대응 membership 비율 = 이동한 A membership(그 줄 소유 타일 안) 중 B membership 이 `ceil(tol/tile)` 타일(체비쇼프) 안에 있는 비율.
  - `unit`·`local`(unit 있음) 은 `unit_offsets[]`, `stretch` 는 `stretch_map.json.tiles[]`, `local` 에 unit 정렬이 없으면 `alignment.json.offset_xy_px` 하나를 frame 전체 칸으로 쓴다.
  - **가장자리(T8 에서 바꿈)**: T4 의 "범위 전체를 ±U 옮긴 복사본" 은 창이 frame 중앙(frame = U + 2m)이라 범위가 frame 밖으로 나가면(r > m) 복사본이 반대편 가장자리를 넘어 실제 경로에서 `repeat_used` 에 도달하지 못했다(R2:1). 이제 18.4 의 타일 단위 주기 매핑(frame 밖 타일만 한 주기 안쪽으로)이라 `repeat_used` 가 실제 경로에서 나온다. 합성 격자 단위 테스트와 `tests/test_ledger_maps_cards.py` 의 실행 테스트(`test_run_with_a_small_unit_counts_the_image_tiles_and_the_range_checks_hold`)로 확인한다.

## 20. 보고서 2절·5절 표시 규칙

- 입력은 `ledger_maps.json` 과 PNG 뿐이다. 카드 숫자는 `ledger_maps.json` 값이다(보고서는 다시 더하지 않는다).
- 2절(지도): 제품별 지도. 선택 상태 `{전극|쌍, 이름}` 하나를 두 canvas(전극 그림·item 그림)가 함께 강조한다. 처음엔 선택 없음. 번호 그림을 canvas 가 읽어 보기별로 칠한다. hover·선택은 번호 조회다.
- 진하기("리스크 크기"): 그 제품 item 장부 값(% of vi_A) ÷ 같은 종류 최댓값, 불투명도 0.18–1.0(목업 v8 과 같은 식).
- "변화" 보기: 색 = 쌍 전체 Δ 부호, 무늬 = `ledger.json` 의 `change_kind` 코드.
- 새·사라진 전극: 없는 쪽 지도 아래 "이 제품에 없음". 그 지름에 영역이 없는 item 은 "지도 영역 없음".
- 레이어 변경 쌍: A 는 옛 레이어, B 는 새 레이어 item 을 빗금. 새 쌍은 굵은 테두리, 사라진 쌍은 점선 테두리(목업 v8).
- 5절(위치 카드): (v1) 종류별 |Δ| 상위 `--top-cards`(기본 5). (v2) 탭마다 종류별 상위 `--top-cards`, 한 쌍은 카드 한 장(28.1). 카드 표 2종: "같은 좌표 비교" 표(A 값 = `S_A,i` 합, B 값 = `T_i` 합)와 "있는 쪽 값만" 표(side 명시, "대응 부분 제외" 표시).
- **합계 줄**: "범위 안 합 / 이 쌍 관찰 영역 값"(제품별 `O_A`, `O_B` 기준). 4절 쌍 값은 참고 줄로 따로 적고 "unit 한 장 기준이라 4절 값과 다릅니다" 를 붙인다. 계획 v1 의 "unit 한 장 값" 은 v1.1 C5 에서 "관찰 영역(창 ∪ 쓴 범위) 값" 으로 바뀌었다.
- 07 위치 Δvi(`compare2_link._pair_location`)는 창 면적 × 밀도 근사라 5절 값과 다를 수 있다고 한 줄 적는다. 07 은 바꾸지 않는다.
- 표시 구분: "대기"(값 null) / "없음"(그 제품에 없음) / "0%"(있고 값 0) 를 서로 다르게 보인다. "해당 없음" 은 12절 규칙을 따른다. `edge: excluded` 는 "비교 제외" 로 보인다.
- 인쇄: `beforeprint`·`#print` 에서 canvas 를 다시 그리고 카드를 모두 편다.
- canvas 값 자기 검사: 로드 때 번호별 픽셀 수를 JSON 과 대조한다. 다르면 지도 대신 경고 + 목록만 보인다(폐쇄망 브라우저가 색을 바꾸는 경우의 탐지).
- 상한: `--map-px-um`(0.2), `--max-map-px`(2000), `--max-map-bytes`(12 MB). 넘으면 배율을 2배씩 거칠게 하고 `scale_steps` 에 기록한다. 줄일 때 블록 안에 하나라도 있으면 남긴다(가는 선 보존).
- `--maps-dir` 가 없으면 보고서 HTML 은 1ce685b 출력과 바이트 동일이다. 있으면 `report.json` 에 `ledger_maps.json` sha256 을 기록하고, `ledger.json` sha256 이 맞지 않으면 종료 2 `ledger_maps_stale` 이다.

## 21. CLI 옵션 표 (`ledger-maps`, `ledger-report`)

`ledger-maps` 옵션. 허용 범위 밖이면 종료 2 `ledger_maps_option_invalid`.

| 옵션 | 기본값 | 허용 범위·뜻 |
|---|---|---|
| `--range-um` | 10 | > 0. 줄 범위 넓힘 |
| `--tile-um` | 1.0 | [0.5, 2.0]. 분석 타일 크기 |
| `--segment-um` | 70 | > 0. 선형 구간 길이 |
| `--max-rows` | 6 | ≥ 1. 카드의 줄 수 상한 |
| `--linear-min-length-um` | 50 | > 0. 선형 성분 최소 길이 |
| `--linear-min-aspect` | 4 | ≥ 1. 긴/짧은 변 비 |
| `--edge-repeat-min-match` | 0.98 | (0, 1]. 가장자리 반복 동등성(일치율·Dice) |
| `--coord-offset-spread-um` | 1.0 | ≥ 0. 범위 안 field offset 차이 상한 |
| `--coord-tolerance-um` | 2.0 | > 0. 대응 membership 겹침 허용 |
| `--coord-min-overlap` | 0.5 | (0, 1]. 대응 겹침 비율 |
| `--map-px-um` | 0.2 | > 0. 번호 PNG 표시 배율 |
| `--max-map-px` | 2000 | ≥ 1. PNG 한 변 상한 |
| `--max-map-bytes` | 12 MB | ≥ 1. PNG 합계 상한 |
| `--top-pairs` | 보고서 기본과 같음(30) | ≥ 1. 지도 라벨·목록에 넣는 종류별 쌍 수(번호 PNG 와 카드 대상에는 영향 없음) |
| `--top-cards` | 5 | ≥ 1. 종류별 카드 수 |
| `--max-tile-entries` | 40,000,000 | ≥ 1. item 희소 항목 합 상한 |

- 이 표의 범위 열(허용 범위의 구체값)은 계획에 없는 부분을 이 문서가 정했다. 구현 Task 가 다르게 구현해야 하면 이 표를 먼저 고친다. 확정된 값은 이름과 기본값, `--tile-um` [0.5, 2.0] 이다. `--top-pairs` 기본은 계획에 적혀 있지 않아 `ledger-report` 와 같은 30 으로 둔다(남은 의문).
- 필수 인자: `--compare2-dir`, `--config`, `--ledger-dir`, `--out`. 선택: `--candidates`.
- `ledger-report` 는 `--maps-dir` 를 더한다. 값은 `ledger-maps` 의 `--out` 폴더다.

## 22. `ledger_maps_v2` 키 표

(v1 → v2 로 바뀐 것은 28절에 모아 두었다: 카드 `tabs`·`tab_ranks`, 줄 `items[]`, 검산 6, `cards_dropped_for_budget`, 구성 변경 쌍 대응.)

| 위치 | 키 | 뜻 |
|---|---|---|
| 최상위 | `schema_version` | `ledger_maps_v2` |
| | `status`, `reasons` | `ok` / `partial`(종료 3, 사유 `ledger_maps_value_check_failed`, `cards_budget_reduced`) |
| | `inputs` | `compare2_json_sha256`, `ledger_json_sha256`, `config_sha256`, `candidates`(04 폴더 상태·`apply_align`·지문 대조 결과), `real_input_hash`(제품별) |
| | `options` | 21절 옵션 값 전체(`tile_um` 포함) |
| | `report` | `diameter_um`(지도 지름) |
| | `window` | 제품별 `origin_px`, `size_px`, `origin_um`, `unit_um`, `unit_source`(설정/유도), `tile_origin_um`(g_r) |
| | `sides` | `<role>` 별 항목(아래) |
| | `cards` | 카드 목록(아래) |
| | `checks` | 검산 1–6 결과와 `not_evaluated` 개수 |
| | `cards_dropped_for_budget` | 예산 때문에 빠진 카드 목록(28.4). 없으면 `[]` |
| | `scale_steps` | PNG 배율을 거칠게 한 횟수와 사유 |
| | `warnings`, `bands` | 경고(`maps_dl1_not_configured`), 띠 코드(19절) |
| `sides.<role>` | `frame`(shape, origin_um, pitch), `items[]`, `maps[]`, `electrodes`(번호 → 이름 표), `hidden` | 제품별 자료 |
| `sides.<role>.items[]` | `item_id`(ledger 와 같음), `pair_id`, `kind`, `layers`, `number`(번호 PNG 의 번호), `value`(`ledger.json` 의 그 side 값), `tile_sum`(타일 합), `diameter_um` 영역 유무, `hidden_px`, `status`(`ok`, `no_map_area`, `pending`) | item 값은 `ledger.json` 값과 같다 |
| `sides.<role>.maps[]` | `file`(`map_<role>_<nnn>_<el|it>.png`), `layer`, `kind`(el/it), `px_um`, `shape`, `numbers`(번호별 픽셀 수) | 자기 검사에 쓴다 |
| `cards[]` | `kind`, `pair_id`, `rank`, `delta`, `rows`(줄 목록: `type`(선형/점형), `rect`, `edge`(`none`/`repeat_used`/`excluded`), `side`, `pair`(이 쌍 값), `others`(상위 3 + `others_n`), `range_total`, `tiles_lost`), `match`(`matched`/`a_only`/`b_only`/`not_compared` 와 사유 띠), `observed`(`O_A`, `O_B` 값), `section4_ref`(4절 쌍 값), `rounding_error` | 5절 카드 자료 |

**`cards[]` 세부 키(T4 확정)**: 카드 `kind`, `pair_id`, `rank`(종류별 |Δ| 순위), `delta`, `a`, `b`(쌍 값), `change_kind`, `rows`, `match`, `observed`, `section4_ref`, `rows_sum`, `rounding_error_um`.
- `rows[]`: 제품마다 줄 하나가 항목 하나다(대응 줄은 A 줄과 B 줄 둘이 서로 `match.counterpart` 로 가리킨다). 키 `id`(`A1`…, `B1`…), `side`(`reference`/`target`), `type`(`linear`/`point`), `rect`(frame 물리 µm `[x0, y0, x1, y1]`, 세는 상자), `rect_tiles`(타일 단위), `edge`(`none`/`repeat_used`/`excluded`), `edge_reason`(`coords_resolution_too_coarse`, `edge_no_dl1`, `edge_no_repeat_position`, `edge_mismatch`, `edge_unverifiable`), `shift_tiles`(반복 줄은 첫 이동 묶음의 `[sx, sy]`), `image_tiles`(이미지 타일 수, 반복 아니면 0), `image_shifts`(이동 묶음 `[sx, sy]` 목록, 반복 아니면 `[]`), `edge_rounding_um`(주기 반올림 오차 µm), `tiles`(최종 집합 타일 수), `tiles_lost`(앞 줄이 가져간 타일 수), `pair`(이 쌍 값, `excluded` 면 null), `others`(`top`[`pair_id`, `value`] 상위 3 + `others_n`·`others_value`), `range_total`, `match`(`status` `matched`/`only`, `reason`, `counterpart`, `offset_px`, `shift_tiles`, `overlap`, `rounding_error_um`, 부분 대응이면 `partial_matched`).
- `match`: `status`(`matched`/`a_only`/`b_only`/`not_compared`), `reason`(띠 코드·변화 종류·`no_common_item` 등; v2 에서 `items_not_on_both_sides` 는 없어지고 양쪽에 있는 item 이 없으면 `no_common_item`, 28.2), `matched_rows`, `absorbed_b_rows`(B 줄이 대응 줄에 모두 포함된 경우 그 B 줄 번호), `not_listed`(`reference`/`target` 별 최대 줄 수에 못 든 점형 "그 밖 k 곳": `n`, `value`).
- `observed`·`rows_sum`: 제품별(`reference`/`target`) 관찰 영역 이 쌍 값, 줄 이 쌍 값 합.
- `checks.range_sum`/`pair_bound`/`partition`/`row_items`: `{status, checked, failed[], failed_count}`. 확인할 줄이 없으면 `not_evaluated`.

세부 키 이름은 T3·T4 가 확정하고 이 표를 고친다. 이 표의 최상위 키와 `sides.<role>.items[]`, `maps[]`, `cards[]`, `checks` 구성은 고정이다.

## 23. 오류·종료 코드

| 종료 | 코드 | 뜻 |
|---|---|---|
| 2 | `ledger_maps_input_missing` | `compare2.json`·`ledger.json`·execution.json·config·`--candidates` 폴더 없음, 또는 입력 execution 이 error. 읽은 자료의 input_hash 가 compare2.json 기록과 다르면 이 코드가 아니라 07 과 같은 `compare2_input_hash_mismatch` 다(문구 동일). `ledger.json` 이 가리키는 compare2.json·config sha256 이 다르면 `ledger_maps_stale`(ledger-maps 쪽)이다. 모든 지름이 `lambda_diameters_um` 밖이면 오류 없이 모든 item 이 값 대기(`pending`)이고 검산은 `not_evaluated` 인 지도가 나온다(종료 0) |
| 2 | `compare2_config_mismatch` | `compare2.json` 이 기록한 config sha256(`inputs.compare2_config.sha256`)이 이 `--config` 의 sha256 과 다름(02 부터 다시 실행). `ledger.json` 쪽 불일치는 `ledger_maps_stale` |
| 2 | `ledger_maps_output_not_owned` | `--out` 에 이 명령이 만들지 않은 파일이 있음(아무것도 쓰지 않고 지우지 않는다) |
| 2 | `ledger_maps_option_invalid` | 21절 허용 범위 밖 |
| 2 | `oversized_real_operation` | `_Budget` 예약이나 읽기 전 가드가 `max_operation_bytes` 또는 `--max-tile-entries` 초과(출력 파일 쓰지 않음). 카드 단계의 예약 실패는 그 카드만 빼고 종료 3(아래 `cards_budget_reduced`) |
| 2 | `ledger_maps_stale`(보고서 쪽) | `ledger-report --maps-dir` 에서 `ledger_maps.json` 이 기록한 `ledger.json` sha256 이 현재와 다름 |
| 3 | `ledger_maps_value_check_failed` | 검산 1·2·3·4·5·6 중 하나라도 통과하지 못함(결과 파일은 쓴다) |
| 3 | `cards_budget_reduced` | 카드 단계 메모리 예산 때문에 카드가 하나 이상 빠짐(28.4). 지도·값·검산 1–2 는 남고 `cards_dropped_for_budget` 에 목록, 띠 `cards_budget_reduced`(개수). 카드와 무관한 예산 초과(읽기·병합·지도, 모든 카드를 버려도 B 가 안 됨)는 계속 종료 2 |
| 0 + 경고 | `maps_dl1_not_configured` | DL1 전극 폴더가 설정에 없음. 전극 그림 없이 item 지도만 만든다 |
| 0 + 경고 | `maps_resource_estimate_missing` | 데이터셋 provenance 에 `resource_estimate` 가 없어 `compute_areas` 보유량을 예약하지 못함(예약 0 으로 진행, 제품마다 `warnings` 에 `{code, role}`) |

- 오류(종료 2)면 자기 소유 결과 파일을 지우고 `error.json`·`execution.json` 만 남긴다(11절과 같음).
- 실행기: 종료 3 은 계속, 종료 2 는 멈춘다(`CONTINUE_CODES` 그대로).

## 24. Lead 결정 D1–D5 와 바뀐 결정

사용자 위임으로 Lead 가 정했다. 사용자가 바꿀 수 있다.

| # | 결정 | 채택 |
|---|---|---|
| D1 | 5절 줄 값 기준 | v1: unit 한 장 값. **v1.1 C5 로 바뀜**: 관찰 영역(창 ∪ 쓴 범위) 값. v1.2 G1 로 최종 집계 타일 집합 기준. 합계 줄 = 범위 안 합 / 이 쌍 관찰 영역 값, 4절 값은 참고 줄 |
| D2 | 지도 영역 지름 | `report.diameter_um` 하나. 값은 장부 지름 전체 |
| D3 | CNO | 지도·범위 합계에서 제외 + 주석 |
| D4 | 04 결과 없음 | 대응 판정 없이 있는 쪽 값만 + 띠 |
| D5 | ETX 단계 | 08 확장 + `LEDGER_MAPS` 스위치 |

추가로 바뀐 결정(v1.1·v1.2):
- 04 는 적용된 field 만 쓰고 진단 offset 은 채택하지 않는다. 모드는 `change_args.apply_align` 에서만 읽는다(G3).
- 줄은 제품별로 만든다. 타일 소유로 줄 중복을 없앤다.
- 대응 줄이 B 에서 충돌하면 뒤 줄 대응을 거부한다(나누지 않는다). B 줄은 대응 부분을 뺀 나머지를 B 전용 줄로 남긴다(G5).
- 메모리는 `_Budget` 하나로 회계한다(G2). 분석 해상도 `--tile-um` 은 [0.5, 2.0] 이고 정렬 허용보다 거칠면 대응하지 않는다(G4).
- 타일 격자는 제품별이다(G6).
- Lead 기본값: 카드 5, 창 중앙, 진하기 = item 장부 값, 정적 그림 대체 없음.

## 25. 한계

- 대각선 배선은 점형으로 분류된다.
- 07 위치 Δvi(`compare2_link._pair_location`)는 창 면적 × 밀도 근사라 5절 값과 다를 수 있다.
- local ROI 별 보정은 쓰지 않는다. 변경 지도 기준 field 만 쓰므로 ROI 별 최종 보정과 다를 수 있다.
- 줄 값은 분석 타일(기본 1 µm)로 반올림한 집합이라 대응 위치에 반올림 오차가 있다(JSON `rounding_error` 에 기록).
- CNO·곡선 보정 줄·CRP 줄은 지도·범위 합계에 없다.
- 실측 대상 값(실자료 unit·frame 크기, 창 여유 ≥ 10 µm 여부, 02·08 시간, DL1 전극 PNG 수, HTML 크기, 폐쇄망 브라우저 canvas 값 보존)은 폐쇄망 실행에서 확인한다. Pillow PNG 바이트 결정성은 같은 환경 안에서만 요구한다.

## 26. 13절과의 충돌 확인

13절 "넣지 않는 것"(간격 측정, A 로 되돌리기, 우선순위, 신뢰도, DR, 기능 묶음 표, 07·`delta.*` 변경)과 16–25절은 충돌하지 않는다. 13절의 "1단계에서 뺀 것"(2절 지도, 5절 위치 상세·범위 합계, 방향·분포 열, 가장자리 반복)은 이 Run 이 2·3단계로 넣는 항목이다. 방향·분포 열은 여전히 넣지 않는다.

## 27. item 분해·구성 변경·칸 상태 (mrep/T1)

`ledger.json` 은 `ledger_v1` 을 유지한다. 키만 더한다. 새 키를 지우고 `change_kinds` 의 `layer_change` label 하나만 되돌리면 기준 commit(c194d0e) 출력과 같다(`tests/test_ledger_breakdown.py` 가 옛 `ledger.py` 로 비교한다). `ledger_pairs.csv`·`ledger_electrodes.csv` 는 코드만 쓰므로 바뀌지 않는다.

| 키 | 내용 |
|---|---|
| `pairs[].breakdown` | `{same_delta, new_value, gone_value, n_both, n_new, n_gone, status}`. `same_delta` = presence `both` item 의 Δ 합, `new_value` = `new` item 의 B 값 합, `gone_value` = `gone` item 의 A 값 합(둘 다 ≥ 0). 쌍 Δ = same_delta + new_value − gone_value |
| `pairs[].composition` | `{added, removed}`. `layer_change` 쌍만 채운다(8절 키 집합과 같은 규칙, 거리 필터 0 item 처리 포함). 원소는 `{item_id, failure_type, layers, text}`(예 `상하 GAT2-SD2`, `측면 GAT2`)이고 `(failure_type, layers, item_id)` 순이다. 다른 쌍은 빈 목록 |
| `pairs[].items[].by_size` | 이미 있는 `_size_map` 자료를 공개한 것. `{diameter_um, density_layer, area_a, area_b, lambda_a, lambda_b, value_a, value_b}`. 없는 item·크기는 0, 기울기가 없으면 value 만 null. 새 계산은 없다 |
| `kinds.<kind>.item_breakdown` | `{same, new, gone, cno, crp}`. `totals.<kind>.items` 를 만드는 같은 내부 item 값의 합(쌍 공개 값이 null 이어도 포함, E1). line 의 cno·crp 는 0. 종류 계산 불가면 전부 null |
| `checks.item_breakdown` | `pair`: `{status, checked, failed, not_evaluated}`(쌍마다 same + new − gone = 쌍 Δ). `kind.<kind>`: `{status, items, total}`. `items` = same + new − gone vs `totals.<kind>.items.delta`, `total` = 거기에 cno + crp 를 더한 값 vs `totals.<kind>.delta`. 오차는 compare2 와 같은 `_within` |
| `electrodes[].layer_state` | `{<layer>: both / a_only / b_only}`. 출처는 `layers_a/b`(DL1 inventory) |
| `electrodes[].moved` | 양쪽에 있고 A 전용·B 전용 레이어가 모두 있으면 true. inventory 가 `observed` 면 null |
| `electrodes[].layer_state_status` | `verified` 또는 `unverified`(어느 한쪽 inventory 가 `observed`, DL1 없음) |

null 규칙:
- 쌍 공개 값(Δ)이 null 이면(대기 `condition_not_computable`, item 없는 대기 쌍, 종류 계산 불가) `breakdown` 의 값 전부와 개수가 null, `status = not_evaluated`. 빈 합 0 으로 두지 않는다. 아니면 `status = ok`.
- 종류 분해는 쌍 공개 null 과 무관하게 내부 item 값으로 합하므로, 대기 쌍이 있어도 종류 검산은 숫자로 passed 다. 대기 쌍은 `pair.not_evaluated` 로만 센다.
- `checks.item_breakdown` 은 `checks.status` 와 최상위 `status`(종료 코드)에 넣지 않는다(기준 출력과 같게 두기 위해). 실패는 이 키에서 읽는다.

이름과 값:
- `change_kinds` 표에서 `layer_change` 의 label 은 "불량 구성 변경" 이다. 코드 `layer_change` 와 item `presence` 값(`both/new/gone`)은 그대로다(E4). 카드 줄의 `a_only/b_only` 변환은 보고서 쪽에서 한다.
- item 의 presence 판정은 8절 규칙이다. 한쪽이 거리 필터로 0 이고 상대에 같은 `item_id` 가 있으면 `both` 다.
- (28.3 으로 바뀜) 위 "`a_only/b_only` 변환은 보고서 쪽에서 한다" 는 계획 v1.2 E4 로 `ledger-maps` 쪽으로 옮겼다. `ledger.json` 의 `presence` 는 그대로다.

## 28. `ledger_maps_v2` 카드 규칙 (mrep/T3)

근거: 계획 초안 Q3·T3, 계획 v1 D3·B3, v1.1 C2·C3·C5, v1.2 E2·E3·E4. `ledger.json` 의 새 키(27절: `pairs[].breakdown`, `items[].presence`, `change_kind`)를 입력으로 쓴다. `ledger.py`·`ledger_changes.py`·`ledger_report.py` 는 건드리지 않는다. 이 변경으로 `ledger_report.py` 의 `MAPS_SCHEMA = 'ledger_maps_v1'` 은 v2 를 거부한다(보고서 쪽은 T5 가 고친다).

### 28.1 카드 대상 = 탭 3개의 합집합

- 후보: 종류(line·pixel)별로 `a`·`b`·`delta` 가 모두 있는 쌍(지금과 같음).
- 탭과 점수(탭마다 종류별로 `--top-cards` 개, 동점은 `pair_id` 순):

  | 탭 | 들어가는 쌍 | 점수 |
  |---|---|---|
  | `same` | `breakdown.same_delta` 가 null 이 아니고 양쪽에 있는 item(presence `both`)이 1개 이상 | `|breakdown.same_delta|` |
  | `comp` | `change_kind == "layer_change"`(불량 구성 변경)이고 `breakdown.new_value`·`gone_value` 가 null 이 아님 | `new_value + gone_value` (둘 다 ≥ 0, 상쇄 없음) |
  | `all` | 전부 | `|delta|` (지금 규칙) |

- 같은 쌍이 여러 탭에 들면 카드는 한 장이다. 카드 키: `tabs`(`same`, `comp`, `all` 중 든 탭, 이 순서), `tab_ranks`(탭별 순위, 1부터), `rank`(종류 안에서 `|delta|` 순위 — 전체 후보 기준이라 `all` 탭에 안 든 카드는 `--top-cards` 보다 클 수 있다), `common_items`(양쪽에 있는 item 수), `items_total`(쌍의 item 수).
- `breakdown` 이 null(대기)이거나 없는 쌍은 `same`·`comp` 탭에서 빠지고 `all` 탭에만 들 수 있다.
- 카드 순서: 종류별로 `rank` 순. 모든 카드가 `all` 탭에만 있으면 v1 과 같은 쌍·순서다.
- 반례 fixture: 새 방식 100·없어진 방식 100 으로 Δ ≈ 0 인 구성 변경 쌍이 `comp` 탭 1위(`tests/test_ledger_maps_v2.py`).

### 28.2 구성 변경 쌍의 대응

- 대응 대상: 두 제품 모두에 있는 쌍이고 `change_kind` 가 `same_pair_change`·`no_change`·`layer_change` 일 때. 양쪽에 있는 item(`presence == both`)이 1개 이상이면 `matched`, 없으면 `not_compared` + 사유 `no_common_item`. 그 밖의 `change_kind`(전극 추가·삭제 등)는 지금처럼 사유 = `change_kind`.
- v1 의 사유 `items_not_on_both_sides` 는 없어졌다. 같은 쌍 변화인데 한쪽에만 있는 item 이 섞인 쌍은 이제 양쪽 item 으로 대응하고, 양쪽 item 이 없으면 `no_common_item` 이다.
- **대응 비율**(19.4 의 membership 겹침)은 양쪽에 있는 item 의 membership 합집합으로 잰다. 쌍의 item 이 모두 양쪽에 있으면 쌍 전체 membership 과 같으므로 결과가 v1 과 같다(같은 배열을 그대로 쓴다). 줄(타일 집합·선형 판정)은 쌍 전체 membership 으로 만들고 값도 쌍 전체로 센다(줄의 타일 집합은 바꾸지 않는다).
- 대응하지 않는 카드(`matched` 가 아닌 것)는 A 쪽 `a_member` 를 만들지 않는다(쓰이지 않는다).

### 28.3 줄의 item 값과 검산 6

- 줄마다 `items[] = {item_id, presence, value}`. 그 제품에 있는 이 쌍의 item 전부이고 값은 줄이 가진 타일 위의 item 값이다(0 도 적는다). `edge == excluded` 인 줄은 `items = null`(`pair` 도 null).
- `presence`: ledger 값 `both / new / gone` → **`both / b_only / a_only`**(`gone → a_only`, `new → b_only`, E4). A 줄에는 `both`·`a_only`, B 줄에는 `both`·`b_only` 만 있다.
- 검산 6 `checks.row_items`: `math.fsum(items[].value)` = 줄의 `pair`(상대 1e-9). 줄마다 센다. 보고서의 표 구분은 `row.match` 가 아니라 이 `presence` 다(C5): `both` = "같은 좌표 비교", `a_only`/`b_only` = "이 제품에만 있는 불량 방식".
- 타일 소유(G1)와 검산 3–5 는 바뀌지 않는다.

### 28.4 카드 단위 원자성과 예산 처리 (E2, E3)

**회계 방식(하나로 정함): 카드 내부의 기존 `reserve` 만 쓰고 카드 단위로 되돌린다.** 카드 일괄 예약은 쓰지 않는다. 이유: 줄의 타일 수·후보 상자 크기는 줄을 만들어 봐야 알아서 처리 전에 닫힌 식으로 추정할 수 없고, 일괄 예약을 하면 내부 `reserve` 와 이중으로 센다. 카드 안에서 하는 모든 할당은 지금처럼 만들기 **전에** `reserve` 한다. 달라진 것은 소유 기록이다.

- `_Hold`(바이트 하나)가 카드의 한 부분(A 부분, B 부분)이다. `_Budget.begin(hold)` 와 `end()` 사이의 모든 `reserve`·`release` 는 그 hold 에 더하고 뺀다(`owned=False` 인 예약, 곧 여러 카드가 같이 쓰는 타일 전극 격자는 제외하고 `ctx.el_bytes` 로 따로 센다). 카드 안에서 예약이 실패하면(`_BudgetExceeded`, 코드 `oversized_real_operation`) 그 hold 의 남은 바이트를 `free(hold)` 로 돌려주고 **그 카드만** 건너뛴다. 다른 카드를 버리지 않는다. 카드를 건너뛴 뒤에도 같은 제품의 다음 카드는 계속 처리한다.
- A 카드가 끝나면 hold 에는 B 부분이 쓸 자료만 남는다: 줄 타일 배열과 대응용 membership(`a_member`). 이 예약은 그 카드가 B 까지 끝나거나 버려질 때 반환한다. 예전처럼 모든 A 카드를 끝낸 뒤 한 번에 예약하지 않는다.
- **A 카드 결과·검산은 카드별로 임시 보관**한다(`_Card.acc_a`, B 부분은 `acc_b`). B 까지 끝난 카드만 `_CardRun.finish` 로 전역 집계(검산 3–6 의 `checked`·실패 목록)에 더한다. 버린 카드의 검산·줄·값은 집계와 출력 어디에도 없다.
- **B 읽기 전·중 부족(E2)**: 카드 밖의 `reserve`(읽기·병합·지도 단계 전부)는 `_Budget.reserve(nbytes, what, evict=…)` 로 부른다. 기본 콜백 `_CardRun.evict_one` 이 이미 끝난 A 카드 하나를 버리고(순위가 가장 낮은 카드: `drop_key` = 가장 좋은 탭 순위가 큰 것, 같으면 탭이 적은 것) `used` 를 줄인다. `used + nbytes ≤ limit` 이 될 때까지 되풀이하고, 버릴 카드가 없으면 종료 2(`oversized_real_operation`)다. 그래서 B 읽기 전 판정 `required = used(A 카드·PNG 포함 실제 전체) + B 추정`(compute_areas 추정, B 밀집 격자, 지도 canvas)이 상한을 넘으면 낮은 순위 카드부터 버리고 다시 판정하고, 읽는 중에 생기는 데이터 의존 임시(sink·병합·PNG)가 모자라도 같은 규칙이다. 카드 안(`begin` 와 `end` 사이)에서는 다른 카드를 버리지 않는다.
- 버린 카드 기록: `cards_dropped_for_budget[] = {kind, pair_id, rank, tabs, stage, reason, detail}`. `stage` = `reference`(A 부분 예약 실패), `target`(B 부분 예약 실패), `reference_held`(A 부분을 끝낸 뒤 B 쪽 부족으로 버림). `reason` = `card_reserve_failed` / `evicted_for_budget`. 카드가 하나라도 빠지면 `status = partial`, `reasons` 에 `cards_budget_reduced`, 종료 3, 띠 `{code: cards_budget_reduced, count, detail}`. 지도·item 값·검산 1–2 는 그대로 남는다.
- 타일 전극 격자는 만드는 도중(PNG 디코드 예약 실패 등) 중단되면 그 격자 예약과 `ctx.el_bytes` 를 함께 되돌린다(astra D1, mrep/T10). 캐시에 있는 격자 수 × 격자 크기 = 격자 예약 합이 항상 성립한다.
- 카드 소유 예약의 잔액은 카드가 끝나거나 버려지면 0 이다. 비카드 예약(번호 PNG 바이트, 타일 전극 격자 등)은 유지된다(`tests/test_ledger_maps_v2.py` 가 실행 끝의 `budget.used` 를 정상 실행과 비교한다).
- `ledger-report` 와 ETX 안내: 종료 3 이어도 실행기는 계속한다. 예산 때문에 카드가 빠지면 `LEDGER_MAPS_ARGS = ["--tile-um", "2"]`(B3, 기존 안내)로 타일을 키우거나 `--top-cards` 를 줄인다.

### 28.5 `execution.json` 단계별 최대 예약량 (B3)

`execution.json` 에 `max_reserved_by_stage = {reference: {read, merge, maps, cards}, target: {read, merge, maps, cards}}`(바이트)를 더한다. 단계 안에서 `used` 가 가장 컸던 값이다(단계가 시작할 때 이미 쥐고 있는 양도 포함).

| 단계 | 구간 |
|---|---|
| `read` | `compute_areas` 추정 예약 → dense 격자·canvas → 마스크 읽기(sink 임시·희소 값 청크) |
| `merge` | item 희소 값·membership 병합 |
| `maps` | 번호 PNG(item, DL1 전극) 만들기 |
| `cards` | 검산 1–2 뒤 카드 처리 전체(첫 제품의 A 부분, 둘째 제품의 B 부분), 희소 자료 정리까지 |

실행 기록의 `max_reserved_bytes`(전체 최대)는 그대로다. 다음 ETX 실행에서 90% 가 어느 단계의 최댓값인지 이 표로 확인한다.

### 28.6 `cards[]` 새 키 요약

`tabs`, `tab_ranks`, `common_items`, `items_total`, `rows[].items[]`, 그리고 최상위 `cards_dropped_for_budget`, `checks.row_items`. 기존 키·값은 모든 item 이 양쪽에 있는 쌍에서 v1 과 같다.

## 29. 변경 위치(04 ROI)와 지도 사각형 (mrep/T4)

`ledger_maps.json` 최상위에 `changes` 와 `overlay` 를 더한다(스키마는 `ledger_maps_v2` 그대로, 키 추가). 연결은 `ledger_changes.build_changes`(T2), 좌표는 `ledger_maps.roi_map_rects`(T4)가 만든다.

### 29.1 `changes`

`link_status`(`ok` / `stale` / `no_candidates` / `invalid`, 사유는 `detail`), `coords_status`(`ok` 또는 19 절의 좌표 코드: `coords_no_candidates`, `coords_not_applied`(diagnostic), `coords_stale`, `coords_unavailable`), `apply_align`, `rois[]`, `pairs`, `items`, `cno`, `outside_map[]`(roi_key 목록), `summary`, `warnings`.

- `--candidates` 가 없거나 폴더에 `candidate_queries_v1.json` 이 없으면 `link_status = no_candidates`(ROI 0). 04 의 `inputs.<role>.dataset_input_hash` 가 02 `inputs.manifests.<role>.input_hash` 와 다르거나 한쪽이 비면 `stale`(연결 0). diagnostic 모드에서도 같은 검사를 한다(`coords_status` 와 `link_status` 는 따로다).
- C1(필수 구조): `reference_to_target` 질의마다 region 식별자(`region_id`/`merged_from`), `center_xy_um`(숫자 2개), `window_um`(0 보다 큰 숫자), `contributing_cases`(객체 목록)가 있어야 한다. 하나라도 빠지면 `link_status = invalid`, `detail` 에 어느 질의(`directions[i].queries[j]`)의 어느 필드인지 적는다. 빈 `roi_key`(`roi:`)는 만들지 않는다. `inputs.<role>`·`change_args` 가 객체가 아니어도 `invalid` 다(예외 없음).
- C2: ROI 안에서 `contributing_cases` 와 `one_sided_cases` 를 `case_key` 로 합쳐 case 한 줄로 만든다. `change_kind` 는 처음 본 값, `change_kinds` 는 본 모든 태그(쌍 태그에도 모두 들어간다). `one_sided` 는 모든 목록에서 한쪽만일 때만 참, `present_in` 은 값이 다르면 `both`.
- R1:3: `summary.linked_pairs` 는 분모(`no_change` 제외) 안의 쌍만 센다. 분모 밖(`no_change`)인데 연결된 쌍은 `linked_no_change_pairs` 다. `linked_pairs + unlinked_pairs = pair_denominator`. 추가 키 `rois_outside_map`.

### 29.2 `rois[].map`

`status`(`ok` / `outside_map`), `center_px`·`size_px`(A frame px), `shift_px`·`shifted`, `clipped`, `larger_than_window`, `offset_px`(04 field offset, frame px)·`offset_reason`, `reference`·`target`.

- `reference = {rect_px, rect_map, clipped}`, `target = {status: ok / no_offset / outside_b_window, reason, rect_px, rect_map, clipped}`. `rect_px` 는 해당 제품 frame px 사각형 `[x0, y0, x1, y1]`, `rect_map` 은 그 제품 지도 그림의 표시 px 이다.
- 변환(C7): `pA = (x_um − g_A_um) / pitch`, `pB = pA + offset_px`(frame 원점을 다시 더하지 않는다), `rect_map = (p − region.origin_px) / region.block_px`(제품별). A 원점 0 · B 원점 10 µm · offset 0 이면 B 사각형은 A 와 같다.
- field 는 원래 ROI 사각형으로 `pick_offset` 을 먼저 고른 뒤(spread 기준 `--coord-offset-spread-um`) 표시용 주기 이동을 적용한다. 사각형이 창(18.3)과 겹치면 이동 0, 겹치지 않고 창이 frame 보다 작으면(주기가 있으면) 중심이 창에 들어오는 정수 주기만큼 옮긴다(`shifted`). **이동량은 반올림한 창 px 가 아니라 실수 주기 `k × unit_um / pitch`** 다(mrep/T10 R2:2; 창 크기를 정수 px 로 반올림한 값의 k 배를 쓰면 k × 반올림 오차(예 U/pitch = 123.45 px 에서 20 주기 = 9 px)만큼 어긋난다). `shift_px` 는 그래서 실수이고, `unit_um` 이 없는 손으로 만든 창은 창 크기를 주기로 쓴다. 창 경계를 넘으면 창 안만 그리고 `clipped`, 창보다 크면 창 테두리까지 그리고 `larger_than_window`. 옮겨도 창 밖(창이 frame 전체여서 주기가 없을 때)이면 `outside_map`(사각형 없음, `changes.outside_map` 목록에만).
- 좌표 대응이 없으면(`coords_status` ≠ `ok`) B 는 `no_offset`(사유 = 좌표 코드, 또는 `no_field_cell` / `offset_spread`), A 사각형은 그린다. 이동한 B 사각형이 B 창 밖이면 `outside_b_window`.

### 29.3 `overlay`

`{available, offset_px, reason}`. 지도 영역 전체(A 지도 그림)에서 field offset 이 spread 기준 안에서 하나로 정해질 때만 `available`. 아니면 `reason`(`offset_spread`, `no_field_cell`, 좌표 코드).

## 30. 지도 영역과 A·B 같은 축척 (mscale/T1)

- 지도 영역 = 제품별 unit 창(U × U) + 사방 `--range-um` 여백이다. unit 창은 frame **중앙**에 놓이므로 네 unit 이 만나는 곳이 창 중심이고, 지도에는 네 unit 의 1/4 씩이 보인다(unit 한 장도 2×2 도 아니다).
- A·B 지도 쌍은 같은 µm 축척으로 그린다. 물리 폭 = 지도 폭 px × `block_px` × `pitch_um`. 큰 쪽이 칸 폭 100%, 작은 쪽은 물리 폭 비율만큼 좁다. 두 제품 pitch 가 달라도 µm 기준이다.
- 각 지도 아래에 µm 눈금 막대(1·2·5×10ⁿ, 그림 폭의 약 1/5)와 캡션 "영역 W × H µm · unit 창 U µm(설정|유도) + 여백 r µm"을 보인다. unit 이 frame 에서 유도되면 "(frame 에서 유도)"를 덧붙인다.
- unit 창은 회색 점선 사각형으로 형상 층과 함께 그린다. 창이 frame 전체이면 그리지 않고 "창 = frame 전체"라고 쓴다.
- 확인용 `data-*`: canvas 의 `data-phys-w-um`, `.mpair` 의 `data-scale-ratio`(작은/큰 물리 폭), canvas 의 `data-win`(창 점선 여부).
- 자료는 `ledger_maps_v2` 의 `sides.<role>.frame.pitch_um`, `sides.<role>.region`, 최상위 `window.<role>` 이다. 하나라도 없으면 축척 없이 예전처럼 그린다.
- (rtrim/T2 보정) 캡션의 "영역 W × H µm" 는 `region.size_px × pitch_um` 이다(렌더 폭 비율은 위처럼 지도 px × `block_px` × pitch). "유도"는 한 번만 쓴다: "unit 창 U µm(frame 에서 유도)" / "(설정)". unit 창 점선은 전극 윤곽(연한 회색)과 다르게 진한 회색 긴 점선이다.

## 31. 보고서 표시 정리 (rtrim/T2) — 배치만 바꾸고 계산은 그대로

사용자 결정(2026-11-17): ② 상대 전극 목록(`#el-partners`)은 **그대로 둔다**. 숫자 값과 ledger·ledger_maps 스키마는 바꾸지 않는다.

- **삭제**: ① 의 작은 A·B 지도(`#s1-maps`, `data-view="s1"`)와 그 그리기·클릭 코드. 순위 목록 줄을 누르면 ③ 으로 가는 동작은 그대로다. ① 은 2열(분해 / 순위 목록)이다.
- **이동**: 형상 태그 표(`#tags`, `#tags-folded`)는 ① 에서 ③ "변경 위치로 찾기"(`#pn-loc`) 맨 위로 옮겼다(같은 id, 같은 `#cat=tag:…&entry=pair` 동작). 선택 쌍 조건 표는 ⑤ `#cond` 에서 ③ 쌍 상세 맨 아래 `<details id="cond-detail">` 로 옮겼다(표 id `cond-table`, 값 0 접기 규칙 그대로). ⑤ 에는 `#cond-link`(③ 으로 가는 링크)와 `#cond-all`(전체 보기)만 남는다.
- **접기**: ⑤ 검산 두 표(`sum-check`, `item-check`)는 `<details id="checks-details">` 안에 있다. 어느 검산이든 `passed` 가 아니거나(failed·pending·partial) ledger 가 partial 이면 `open`. ② 전극 × 레이어 표(범례·검색·A/B layout 포함)는 신규 0 · 제거 0 · 레이어 이동 0 이고 확인 불가 전극도 없을 때만 `<details id="corr-details">`(요약 줄 = `#corr-summary`)로 닫혀 있다. 차이가 있으면 지금처럼 펼침. `#print` 는 모든 `details` 를 연다.
- **③ 쌍 목록**: 변화 종류 필터 버튼에 그 종류의 쌍 수를 쓴다("같은 쌍 증감 (121)", ledger.json `pairs[].change_kind` 집계). 정렬 버튼 "변화 큰 순(기본) / B 위험 큰 순"은 해시 `#sort=b`(허용 값은 `b` 하나)이고, 목록에 "B 값" 열을 더했다. B 순서 = ① "B 에서 가장 위험한 쌍" 과 같은 기준(`present_b` 이고 `pct_b` 가 있는 쌍을 `pct_b` 큰 순, 같으면 pair_id; 값 없는 쌍은 뒤에 기본 순서).
- **문구**: ① 제목 "변화의 구성 — item 단위 분해"(질문 "얼마나 달라졌고, 어떻게 구성되나"). 인과를 확정하는 말은 쓰지 않는다.
- (rnarr/T1) unit 창 점선은 노란색 + 어두운 테두리 + 왼쪽 위 "unit" 라벨로 그린다. 변경 위치(04)는 보조 정보다: ③ "변경 위치로 찾기" 맨 위와 ① 신뢰 줄에 "A·B 설계가 비슷할 때만 비교 의미가 크다"는 안내를 둔다. 판단 기준은 쌍·전극 장부다.
