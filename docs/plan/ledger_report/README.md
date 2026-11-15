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
- 07 과 같은 stale 검사(`compare2_report.py:259-271` 와 같은 상수·같은 코드): `inputs.area_basis` 가 `compare2.AREA_BASIS` 와 다르면 `compare2_stale_area_basis`, `inputs.case_exclusions.<role>.rule` 이 `CASE_EXCLUSION_RULE` 과 다르거나 없으면 `compare2_stale_case_exclusion_rule`.
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
| `inputs` | `compare2_json_sha256`, `config_sha256`, `ledger_basis`, `products.<role>`(model_group, pair_table_hash, crp), `electrode_inventory.<role>` {`electrodes`(정렬), `layers`(dl1 만), `source` dl1/observed, `status` complete/incomplete, `sha256`} |
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
- 계산하지 않는다. `ledger.json` 값의 형식 변환(A 의 vi 대비 %, %p)과 상위 N 에 못 든 줄의 합("기타 k" 줄)만 한다. 보고서가 하는 표시용 합산은 둘이다. (1) "기타 k" 줄의 합. (2) `_group_sums`(변화 종류별 쌍 Δ·증가 합, 1절 변화 종류 표와 타일 ②). 둘 다 `math.fsum` 이고 `ledger.py` 와 같은 null 규칙을 쓴다. 한 부분이라도 null(대기)이면 합도 null 이고 "대기" 로 보인다(`ledger.py:73-87`). 기타 줄은 옆에 "값 대기 n 줄 포함" 을 적는다. 합계 줄은 `ledger.json` totals 그대로이고 전극 pixel 합계 줄만 `items + cno` 이다. 쌍 × 레이어 표의 칸은 그 쌍의 item Δ 를 레이어별로 더한 값이다.
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

- 기존 결과가 있으면 `STEPS = ['02', '08']`(02 를 새 번들로 다시). 08 만 다시는 `STEPS = ['08']`. 01·03·04·06 은 필요 없다.
- 07 은 선택이다. 07 의 값은 같다. 07 `report.json` 의 compare2.json sha256 기록을 맞추려 할 때만 다시 돈다(검증하는 소비자는 src 에 없음). `docs/usage.md` 에 그대로 적는다(T6).
- Python 3.9.14(numpy 1.23.5, Pillow 10.1.0)로 bundle 을 확인한다. ETX 화면은 EUC-KR 이고 환경 변수를 넘길 수 없다.
- 실자료는 폐쇄망에만 있다. 이 호스트에서는 합성 자료와 fixture 로 확인한다.
