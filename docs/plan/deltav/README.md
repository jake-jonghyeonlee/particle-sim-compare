# ΔV 가중 취약점 ROI — 남은 항목 (2026-10-28)

Paseo Run `20261027-deltav`. `feat/template-export` @ `8eaf7cb` 로 병합했다.
병합한 명령은 `dv-template`, `dv-grade`, `candidates-dv`, `vulnerability-report` 이고, ETX 실행기는 0·0b·4b·4c 다.
사용법은 [dv_template.md](../../dv_template.md), [dv_grade.md](../../dv_grade.md), [dv_candidates.md](../../dv_candidates.md) 에 있다.

## 다음 단계 (사용자 결정: 나중에 진행)
- DL2 에서 취약점 ROI 와 유사한 영역을 검색한다. 결과로 skip 하거나 영역 안의 불량을 찾아 리포트한다.
- 이 흐름을 기존 모델 비교(변경점 리포트)와 묶는다.

## 사용자 결정 대기
| 항목 | 지금 동작 | 바꾸려면 |
|---|---|---|
| 보고서 3절에 전체 전극 수(count_all) 지도 | 선정 전극 수·최고 등급 지도만 보인다 | `candidates-dv` 가 count_all 지도를 출력해야 한다 |
| layer 색 순서 | layer 이름 자연 정렬(L2 < L10) | `candidates-dv` 가 config 의 `layer_stack`(stack_order)을 출력에 기록해야 한다 |

## 확인하지 못한 것
- 실자료 규모(약 3400² frame)에서 `candidates-dv`·`vulnerability-report` 의 시간과 메모리. 합성 1500² 에서 등급 지도는 시간 +7 %, 메모리 +16 MB 였다.
- 4b·4c 는 같은 서버에서 연속 실행한다고 가정한다. 4c 는 4b 가 기록한 `data_dir` 절대 경로에서 전극 PNG 를 읽는다. 폴더를 옮기면 layout 없이 그리고 종료 코드 3 이다.
- 보고서의 layer 색 슬롯 초과(5개 이상) 렌더링은 단위 테스트로만 확인했다.

## 비차단 리뷰 권고 (기록만)
- `dv_candidates.py` case 없는 제품: `condition_assignment.product_id` 와 최상위 `product_id` 가 다를 때 우선순위를 고정하는 테스트가 없다. 오류 메시지에 값을 읽은 키 이름이 없다.
- `docs/dv_candidates.md` 종료 코드 2 목록에 `prepare_side` 의 config 오류(`explicit_condition_assignments_required`, `real_grouping_assignment_required` 등)가 없다.
- 바이트 불변 텍스트 테스트(`tests/test_electrode_map.py`)는 64자리 hex 를 모두 지우고 비교한다. 입력·config sha256 변화는 기준 환경(py3.11/PIL 12.3.0/numpy 2.4.6)의 전체 해시 테스트만 잡는다. 범위를 좁히면 py3.8 에서 npz/PNG 파생 해시가 달라 실패한다.
- `dv-grade` 폴더 이름의 앞뒤 공백 전극(` A`)은 시트의 strip 된 이름과 맞지 않는다(가능성 낮음).
- 출력 폴더 rename 직전에 같은 이름의 빈 폴더가 생기면 POSIX `os.rename` 이 덮어쓴다(가능성 낮음).
- 0b 실행기 로그의 경로 표시(`PATH_FLAGS`)에 `--sheet` 가 없다(`scripts/build_single_file.py`).

## 미해결 별건
- ETX 3회차 `invalid_electrode_png_name`(CNO 이름의 하이픈). 별도 Run 은 사용자 지시로 폐기했다. 전극 이름에 `-` 를 허용하면 case 폴더 이름 분해와 `dv-grade` 의 `pair_key`(`a-b`)가 영향을 받는다.
  - 2026-10-28 change-roi-v2/T9: `cno/` 안의 PNG 파일 이름 `<n>-<layer>.png`(실자료 형식, 예 `1-PXL.png`)를 받도록 고쳤다(예전 `<n>_<layer>.png` 도 계속 받는다). 전극 **이름**(폴더 이름) 자체에 `-` 가 들어가는 경우는 위 이유로 여전히 별개 문제다.

진행 기록: `~/git/paseo-ops/migration/runs/20261027-deltav/CHECKPOINT.md` (저장소 밖).
