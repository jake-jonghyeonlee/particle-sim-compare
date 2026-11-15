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
| item | 줄 단위 항목. 키 = (pair_id, failure_type, upper_layer, lower_layer). |
| 불량 방식 | `lateral_short`(측면), `vertical_short`(상하), `lateral_open`(CNO). |
| 위/아래 레이어 | 상하 short 의 두 전극 레이어. `위/아래`(stack 순서)로 적는다. 측면은 위 = 아래. |
| line / pixel | 불량 종류. line 과 pixel 의 % 는 더하지 않고 한 순서로 정렬하지 않는다. |
| 곡선 보정 줄 | 절편 c. 전극에 나누지 않는 한 줄(1절). |
| CRP 줄 | 두 제품의 CRP 가 다를 때의 차이 한 줄(1절). pixel 에만 있다. |
| 그룹 | (analysis_layer, density_layer, 이물 크기). |
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

- 주 숫자 % = 100 × 값 / vi_A(같은 종류). vi_A 가 0 또는 null 이면 % 없이 원래 값만 보인다.
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

## 8. 변화 종류 판정 Q5

쌍 하나에 종류 하나. 우선순위 순서로 처음 맞는 것을 쓴다.

1. 두 전극 중 하나라도 한쪽 제품의 전극 목록에 없다 → **전극 추가·제거**
2. 쌍이 B 에만 → **새 쌍**, A 에만 → **사라진 쌍**
3. A, B 의 item 키 집합이 다르다 → **레이어 변경** (방식만, 아래 레이어만 바뀐 경우 포함)
4. |Δ| ≤ 1e-12 + 1e-9·max(|a|,|b|) → **변화 없음**
5. 그 밖 → **같은 쌍 증감**

- 쌍 "있음" = compare2 그 제품에 조건이 있음(면적 0 포함). 폴더 없는 기대 case 만 가진 쌍은 없음이다.
- 쌍 줄은 우선순위 최상 종류 하나. item 줄은 각자 both / new / gone. 건수는 쌍 하나에 한 번.
- 계산 불가 조건의 쌍은 판정하지 않고 7절 "계산 불가 항목"에 둔다(6절의 값 대기와 구분: 값 대기는 있음·값 없음, 계산 불가는 조건 자체가 계산되지 않음).
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
- `ledger_basis` 가 없으면 `ledger_compare2_stale`("02 를 새 번들로 다시 실행"). config sha256 이 다르면 `compare2_config_mismatch`.

## 9. `compare2.json` 추가 키

기존 키·값·CSV 4개·`OWNED_NAMES` 는 불변이다. `schema_version` 은 `compare2_v1` 을 유지한다(07·compare2_link 가 문자열 비교: `compare2_report.py:257`, `compare2_link.py:64`).

- `sides.<role>.ledger_items[]` — item 마다 (필드 이름은 제안):
  - `pair_id`, `failure_type`, `upper_layer`, `lower_layer`, `kind`(line/pixel/cno)
  - `analysis_layer`, `density_layer`, `diameter`(그룹)
  - `endpoints: [{electrode_id, actual_layer}]`
  - `lambda`(item 몫의 λ), `exclusive_cells`, `shared_share`(제안: 공유 셀 몫), `multi_item_overlap_cells`
- `sides.<role>.ledger_check` — 정수 검산 3개 결과와 Σ item λ = `sides.lambda.line/pixel`, 쌍별 Σ item λ − lambda_excl 의 합 = shared λ 대조. 실패하면 reasons `ledger_check_failed` + partial(종료 3).
- `inputs.ledger_basis = 'pair_share_v1'`.

불변 조건:

- 새 키 3종을 지운 compare2.json 은 변경 전과 같다.
- Σ item λ = `sides.lambda.line/pixel`.
- 쌍별 Σ item λ − lambda_excl 의 합 = shared λ.
- 입력 해시를 바꾸지 않는다(`real_hotspot.py:1553-1571`). compare2.yaml 불변.
- `linepixel.py` `_group` 은 `condition_stats=True` 일 때만 `ledger_items` 를 추가한다. `False` 결과는 바이트 동일.

## 10. `ledger.json` (`ledger_v1`)

`compare2.json` → `ledger.json`. 마스크는 읽지 않는다. 같은 입력 두 번이면 바이트 동일이다(입력에 DL1 전극 폴더 목록 포함). 최상위 키는 모두 **제안**이다(PLAN 은 이름을 정하지 않았다. `inputs.electrode_inventory` 만 PLAN 이름).

| 키 | 뜻 |
|---|---|
| `schema_version` | `ledger_v1` |
| `status`, `reasons` | ok / partial(종료 3) 과 사유 |
| `inputs` | compare2.json sha256, config sha256, `ledger_basis`, `electrode_inventory.<role>` {정규화한 전극 목록(정렬), 출처, 상태, 목록 sha256} |
| `totals` | 종류별 A·B·Δ 합계, 절편 줄, CRP 줄, 합계식 6개 결과(passed/not_evaluated) |
| `kinds` | 종류(line/pixel)별 vi A·B, s, 계산 가능 여부, 변화 종류별 건수·증가 합·감소 합 |
| `pairs` | 쌍별 번호(L#, P# = 종류별 B 값 순위, 동률 pair_id 순), 변화 종류, A·B·Δ·%, item 목록(both/new/gone) |
| `electrodes` | 전극별 상태(공통/신규/제거/확인 불가), 레이어 A → B, 종류별 A·B·Δ, 그중 CNO, 상대 전극 A → B, 레이어별 값 |
| `cno` | (electrode_id, density_layer) 줄 |
| `layers` | 레이어 집계와 레이어 × 이물 크기, 주된 기여 |
| `checks` | 합계식 6개, Σ 전극 = Σ 쌍 + CNO, 쌍 Δ 합 = `delta.conditions` + `delta.shared` |
| `warnings` | `electrode_inventory_from_hotspots`, `electrode_inventory_conflict` 등 |
| `unavailable` | 계산 불가 항목(7절) |

번호 L#, P# 는 `ledger.json` 에 저장한다(다음 Run 지도가 같은 번호를 쓴다).

## 11. 출력 폴더 규약과 08 단계 (X4)

- 폴더: `ledger` → `data/ledger`, `ledger-report` → `data/ledger_report`. 02·07 과 겹치지 않는다. 실행기 상수 `LEDGER_DIR`, `LEDGER_REPORT_DIR` 를 `output_folders` 의 08 항목에 둔다(실행 전 `data/_runs/<RUN_TAG>/` 로 이동).
- 두 명령은 compare2 규약을 따른다. `--out` 은 새 폴더이거나 그 명령이 만든 폴더(OWNED_NAMES 만 있음)만 허용한다. 결과는 임시 파일에 쓴 뒤 rename 으로 게시한다. 오류면 자기 소유 결과 파일을 지우고 `error.json`·`execution.json` 만 남긴다(종료 2).
- `ledger` 종료 3(partial)이면 실행기는 계속하고 `ledger-report` 는 맨 위에 "부분 결과" 띠와 사유를 보인다. 종료 2 면 실행기가 멈춘다(`CONTINUE_CODES` 그대로).
- 재시도: `STEPS = ['08']` 로 08 만 다시 돈다. `ledger` 성공 뒤 `ledger-report` 만 실패해도 08 전체를 다시 돈다.
- `ledger-report` 는 `ledger.json` 의 sha256 과 `ledger` 의 `execution.json` 상태를 `report.json` 에 기록한다. `ledger.json` 이 없거나 execution 이 error 면 종료 2 `ledger_input_missing`.
- 08 은 기본 STEPS 에 포함한다.

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

- 02 → 08. 01·03·04·06 은 필요 없다.
- 07 은 선택이다. 07 의 값은 같다. 07 `report.json` 의 compare2.json sha256 기록을 맞추려 할 때만 다시 돈다(검증하는 소비자는 src 에 없음). `docs/usage.md` 에 그대로 적는다(T6).
- Python 3.9.14(numpy 1.23.5, Pillow 10.1.0)로 bundle 을 확인한다. ETX 화면은 EUC-KR 이고 환경 변수를 넘길 수 없다.
- 실자료는 폐쇄망에만 있다. 이 호스트에서는 합성 자료와 fixture 로 확인한다.
