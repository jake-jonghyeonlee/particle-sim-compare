# deltav 예시 파일

**흐름.** `dv-template` 이 빈 시트를 만들고, 사용자가 전압·임피던스를 채우고, `dv-grade` 가 제품마다 쌍별 등급을 계산한다. 그 출력 폴더 둘을 `candidates-dv`(`docs/dv_candidates.md`)에 주면 등급으로 가중한 취약점 ROI 가 나오고, `vulnerability-report` 가 수치 근거 HTML 을 만든다. ETX 에서는 0 → 0b → (1~3회차) → 4b → 4c 순서다.

`dv-template`(`docs/dv_template.md`)이 읽고 쓰는 파일 형식의 예시다.

**이 폴더의 숫자와 전극 이름은 모두 가짜다.** 실제 임피던스나 전압이 아니다.

- `electrode_impedance.example.csv`: `--impedance-db` 입력 예시. `electrode_id`, `impedance`(`low`/`high`/빈칸) 열이 필요하다. 빈칸은 DB 에 없음이다.
- `dv_grades.example.csv`, `dv_grades_by_case.example.csv`: `dv-grade`(`docs/dv_grade.md`)가 쓰는 결과의 예. 가짜 숫자다. `dv_sheet.example.csv` 와 가상 case 폴더 3개(+ open 1개)로 `--low-low-threshold-v 12` 를 줘서 실행한 결과이며 실제 결과가 아니다.
- `dv_sheet.example.csv`: 사용자가 채운 `dv_sheet.csv` 의 예(타이밍 2개). 이 파일도 그대로 `--impedance-db` 로 줄 수 있다. 다른 열은 무시된다.
