# dv-grade — short 쌍별 ΔV·최대 타이밍·등급

OLED 화소 전극쌍 short 의 위험 등급을 구동 타이밍별 전압 차이(ΔV)로 매긴다.
`dv-grade` 는 `dv-template`(`dv_template.md`)이 만든 시트를 사용자가 채운 뒤에 쓴다. PNG·CSV 내용은 열지 않고 case 폴더 **이름**만 읽는다.

## 명령

```
python -m simcompare dv-grade --sheet <채운 dv_sheet.csv> --root <제품 case root> --product-id <제품 식별자> --out <없는 새 폴더> [--low-low-threshold-v <소수>]
```

| 옵션 | 뜻 |
|---|---|
| `--sheet` | 사용자가 채운 `dv_sheet.csv`. UTF-8(BOM 허용) |
| `--root` | 제품 case 폴더가 있는 root (`import-hotspots` 와 같은 규칙, `<case>/images/` 배치 인식) |
| `--product-id` | 제품 식별자. 필수, 기본값 없음, 빈 문자열 금지(`dv_product_id_empty`). manifest 에 기록 |
| `--low-low-threshold-v` | 선택. low–low 쌍의 High 경계(V). 0 보다 큰 일반 소수. 없으면 ΔV>0 인 low–low 쌍은 모두 High |
| `--out` | 새 폴더. 이미 있으면 종료 코드 2. 실패하면 부분 출력을 남기지 않는다 |

코드에는 전압·임피던스·경계 기본값이 없다.

## 쌍 고르기

1. 두 전극 이름이 같은 case(`ea == eb`)를 먼저 뺀다. 불량 종류와 상관없다. manifest 의 `ignored_same_name_cases` 에 남는다.
2. 남은 case 중 open(`lateral_open`, `vertical_open`)을 뺀다. manifest 의 `excluded_open_cases` 에 남는다.
3. 남은 short case 를 전극 쌍으로 묶는다. `pair_key = a + "-" + b` 이고 `a < b`(코드 포인트 순)다. 전극 이름에는 `-` 가 올 수 없으므로 키가 겹치지 않는다.
4. short case 가 하나도 없으면 `dv_no_short_pairs`, 종료 코드 2.

폴더 이름의 전극 순서가 `B-A` 여도 `A-B` 와 같은 쌍이다. 쌍 하나에 case 가 여러 개일 수 있다. ΔV 와 등급은 쌍마다 한 번 계산한다.

## 숫자 규칙

전압 칸과 경계 인자는 정규식 `^[+-]?\d{1,6}(\.\d{1,6})?$` 만 받는다(앞뒤 공백은 무시).
지수 표기(`1e-3`), 쉼표, 단위(`5V`), `.5`, `nan`, `inf` 는 거부한다. 계산은 모두 `Decimal` 이고 float 는 쓰지 않는다. 이 범위에서 뺄셈은 정확하다.

## 계산

타이밍 t 마다, 두 전극의 전압 범위 `[vmin, vmax]` 로:

```
dv_t = max(vmax_a − vmin_b, vmax_b − vmin_a)
ΔV   = max over t of dv_t
```

- 두 값의 합은 폭_a + 폭_b ≥ 0 이므로 큰 쪽은 항상 ≥ 0 이다. 같은 비퇴화 범위(예 `[1,3]`, `[1,3]`)의 ΔV 는 그 폭(2)이다.
- ΔV = 0 은 **모든 타이밍에서** 두 전극이 같은 한 점 값일 때뿐이다.
- 최대가 같은 타이밍이 여럿이면(동점) 타이밍 **번호** 순(T2 < T10)으로 모두 기록한다. `dv_timing` 은 그중 가장 작은 번호다.

## 등급

| 조건 | grade |
|---|---|
| ΔV = 0 | `None` |
| 한쪽이라도 임피던스 `high`, ΔV > 0 | `High` |
| low–low, 경계 없음, ΔV > 0 | `High` |
| low–low, 경계 있음 | ΔV ≥ 경계 이면 `High`, 아니면 `Low` |

경계와 같은 값은 `High` 다(`≥`).

## 입력 검증

모든 오류를 모아 stderr 에 한 줄씩 쓰고 종료 코드 2 로 끝난다. 출력 폴더는 만들지 않는다.
형식: `<code> row=<줄> electrode=<id> column=<열> value=<원문> : <설명>`. 줄 번호는 파일 줄 번호(헤더 = 1)다.

| code | 뜻 |
|---|---|
| `dv_sheet_encoding_invalid` | UTF-8 이 아님. 엑셀에서 "CSV UTF-8(쉼표로 분리)" 로 저장 |
| `dv_sheet_header_invalid` | 헤더가 `electrode_id,impedance,layers,T1_min,T1_max,…,TN_min,TN_max` 와 다름(N 1–20, 추가 열 금지) |
| `dv_sheet_row_width_invalid` | 칸 수가 헤더와 다름 |
| `dv_sheet_electrode_id_empty` | `electrode_id` 빈칸 |
| `dv_sheet_duplicate_electrode` | 같은 `electrode_id` 가 두 번 |
| `dv_sheet_electrode_missing` | 쌍에 쓰이는 전극이 시트에 없음 |
| `dv_impedance_invalid` | `low`/`high` 아님. 쌍에 쓰이는 전극은 빈칸도 오류 |
| `dv_voltage_invalid` | 숫자 규칙 위반. 쌍에 쓰이는 전극은 빈칸도 오류 |
| `dv_voltage_range_inverted` | min > max |
| `dv_threshold_invalid` | 경계가 숫자 규칙 위반 또는 0 이하 |

모든 칸이 빈 행은 건너뛴다. 쌍에 쓰이지 않는 행도 **채운 값**은 검사한다(빈 칸은 허용). 시트에만 있는 전극은 오류가 아니며 manifest 의 `unused_sheet_electrodes` 와 요약에 나온다.

## 출력

CSV 는 UTF-8 BOM, 줄바꿈 LF 다. `grade` 는 문자열 `None`/`Low`/`High` 다.

| 파일 | 내용 |
|---|---|
| `dv_grades.csv` | 쌍 1행. `pair_key, electrode_a, electrode_b, impedance_a, impedance_b, impedance_class(high_any/low_low), dv_v, dv_timing, dv_timings_tied(;), dv_timing_tie_count, grade, threshold_v, case_count, cases(;), defects(;), layer_pairs(;)`. ΔV 가 0 이면 타이밍 3열은 빈칸. `threshold_v` 는 이번 실행의 경계(없으면 빈칸). `layer_pairs` 는 `layer_a:layer_b`(쌍의 a, b 기준) |
| `dv_grades_by_case.csv` | short case 1행. `case_name, defect, electrode_a, layer_a, electrode_b, layer_b`(폴더 이름 순서 그대로)`, pair_key, dv_v, dv_timing, grade` |
| `dv_pair_timings.csv` | 검산용. 쌍·타이밍마다 `dv_ab, dv_ba, dv_t` (`dv_ab = vmax_a − vmin_b`) |
| `dv_grade_manifest.json` | `schema_version: dv_grade_v1`, `grade_rule_version: dv_grade_rule_v1`, `product_id`, `sheet`(경로·sha256), `root`, `timings`, `threshold_v`, 개수, `grade_counts`, `ignored_same_name_cases`, `excluded_open_cases`, `unused_sheet_electrodes`, `outputs`(파일별 sha256), `tool_version` |

표준 출력에 한국어 요약(등급 개수, 경계, 제외 case, ΔV 큰 쌍 5개)이 나온다.

## 소비자용 로더

```python
from simcompare.dv_grade import load_dv_grades, DvGradeLoadError
loaded = load_dv_grades(out_dir)   # {'manifest', 'pairs', 'by_case'}; CSV 값은 모두 문자열
```

다음을 확인하고 어긋나면 `DvGradeLoadError(code)` 를 올린다.
`dv_grade_schema_unsupported`·`dv_grade_rule_unsupported`(버전), `dv_grade_file_missing`·`dv_grade_hash_mismatch`(manifest `outputs` sha256),
`dv_grade_duplicate_case`(`case_name` 중복), `dv_grade_pair_missing`(by_case 의 `pair_key` 가 `dv_grades.csv` 에 없음),
`dv_grade_case_value_mismatch`(by_case 의 `dv_v`·`grade`·`dv_timing` 이 쌍 행과 다름), `dv_grade_case_electrode_mismatch`(전극 집합이 쌍과 다름).

같은 case 이름이 다른 제품에 있을 수 있다. 제품 일치 확인은 소비자(T3)의 몫이다. 근거는 manifest 의 `product_id`·`root`·`sheet.sha256` 이다.

예시 결과(가짜 숫자): `docs/domain-input-example/deltav/dv_grades.example.csv`, `dv_grades_by_case.example.csv`.
