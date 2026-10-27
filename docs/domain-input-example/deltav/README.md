# deltav 예시 파일

`dv-template`(`docs/dv_template.md`)이 읽고 쓰는 파일 형식의 예시다.

**이 폴더의 숫자와 전극 이름은 모두 가짜다.** 실제 임피던스나 전압이 아니다.

- `electrode_impedance.example.csv`: `--impedance-db` 입력 예시. `electrode_id`, `impedance`(`low`/`high`/빈칸) 열이 필요하다. 빈칸은 DB 에 없음이다.
- `dv_sheet.example.csv`: 사용자가 채운 `dv_sheet.csv` 의 예(타이밍 2개). 이 파일도 그대로 `--impedance-db` 로 줄 수 있다. 다른 열은 무시된다.
