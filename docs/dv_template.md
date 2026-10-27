# dv-template — 전극×타이밍 전압 시트 빈 양식

OLED 화소 전극쌍 short 의 위험 등급은 구동 타이밍별 전압 차이(ΔV)로 매긴다. 전압 자료는 폐쇄망 내부 자료이므로 사용자가 직접 채운다.
`dv-template` 은 사용자가 채울 **빈 양식**만 만든다. ΔV 계산은 이 명령의 일이 아니다.

## 명령

```
python -m simcompare dv-template --root <제품 hotspot 또는 template case root> --timings <N> --out <없는 새 폴더> [--impedance-db <csv>]
```

| 옵션 | 뜻 |
|---|---|
| `--root` | 제품 case 폴더가 있는 root. `import-hotspots` 와 같은 규칙으로 case 폴더를 찾는다(`<case>/images/` 배치도 인식) |
| `--timings` | 구동 타이밍 수 N. 1–20 정수. 기본값 없음. 밖이면 종료 코드 2 |
| `--impedance-db` | 선택. 전극별 임피던스 DB(CSV) |
| `--out` | 새 폴더. 이미 있으면 종료 코드 2. 실패하면 부분 출력을 남기지 않는다 |

## 읽는 것

- case 폴더 **이름**만 읽는다(`<불량>-<전극A>-<layerA>-<전극B>-<layerB>`). PNG·CSV 내용은 열지 않는다.
- 두 전극 이름의 합집합이 시트 행이 된다. 전극 이름은 unit pixel 기준이고, 같은 역할 전극은 제품이 달라도 이름이 같다.
- 두 전극 이름이 같은 case(`ea == eb`)는 무시한다. 이름은 manifest 의 `ignored_same_name_cases` 에 남는다.
- manifest 의 `case_count` 는 무시한 case 를 포함한 전체 case 수다.
- 같은 이름 case 를 빼고 전극이 0개면 `dv_template_no_electrodes`, 종료 코드 2, 출력 없음.

## 임피던스 DB

- UTF-8(BOM 허용) CSV. `electrode_id`, `impedance` 열이 필요하다. 다른 열은 무시한다.
- `impedance` 는 `low`, `high`, 빈칸만 허용한다(소문자, 앞뒤 공백 무시). 빈칸은 "DB 에 없음"이다.
- 같은 `electrode_id` 가 두 번 나오면 값이 같아도 오류다. 오류는 종료 코드 2이고 출력은 없다.
- 코드에 임피던스 기본값은 없다. 첫 제품에서 채운 `dv_sheet.csv` 가 다음 제품의 DB 가 된다(그대로 `--impedance-db` 에 줄 수 있다).

## 출력

| 파일 | 내용 |
|---|---|
| `dv_sheet.csv` | 헤더 `electrode_id,impedance,layers,T1_min,T1_max,…,TN_min,TN_max`. `electrode_id` 순. `impedance` 는 DB 값 또는 빈칸. `layers` 는 layer 이름을 `;` 로 이은 참고 정보. 전압 칸은 항상 빈칸(이전 제품 값을 복사하지 않는다) |
| `new_electrodes.csv` | `electrode_id,layers,case_count`. DB 에 값이 없는 전극(DB 를 안 주면 전부). 사용자가 임피던스를 정할 전극 목록 |
| `dv_template_manifest.json` | `schema_version: dv_template_v1`, 입력·옵션, 개수, `ignored_same_name_cases`, `db_only_electrodes`(DB 에만 있는 전극), `impedance_db`(경로·sha256) |

CSV 는 UTF-8 BOM, 줄바꿈 LF 다(Excel 에서 열린다). 표준 출력에 한국어 요약이 나온다.

## 흐름

1. 첫 제품: `--impedance-db` 없이 실행한다. 모든 전극이 `new_electrodes.csv` 에 나온다.
2. `dv_sheet.csv` 의 `impedance` 와 전압 칸을 채운다.
3. 다음 제품: 채운 시트를 `--impedance-db` 로 준다. 이미 아는 전극의 임피던스만 채워지고, 새 전극만 `new_electrodes.csv` 에 남는다.

ETX 실행기: `bundles/etx/simcompare_job_0_dv_template.py`(0회차, 1~6회차와 독립). 예시 파일: `docs/domain-input-example/deltav/`.
