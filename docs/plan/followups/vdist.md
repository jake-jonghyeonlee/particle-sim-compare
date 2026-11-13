# 층간 거리 필터·입력 경로 통일·통합 실행기 — 남은 후속 항목 (2026-11-13)

출처 Run:
- `20261112-vdist-filter`: 병합 `f3edc88`. 06 (전극, 층) 매칭, 입력 경로 통일, thk_info 거리 필터, 필터 기록과 07 검사, 통합 ETX 실행기.
- `20261113-vdist-expected`: 병합 `be49c93`. 기대 case 의 거리 행이 없어도 허용.

진행 기록은 `~/git/paseo-ops/migration/runs/<Run>/CHECKPOINT.md` 에 있다.

아래 항목은 모두 **병합을 막지 않는 리뷰 지적(advisory)** 이다. 계산 결과가 조용히 틀리는 항목은 없다. 대부분 사용자 안내, 테스트 강도, 오류 시점에 관한 것이다.

## 통합 실행기 (`src/simcompare/etx_flow.py`, `scripts/build_etx_stage_runners.py`, `docs/usage.md`)

| # | 내용 | 실패 시나리오 | 제안 |
|---|---|---|---|
| E1 | DL2 경로에서 `STEPS = ['07']` 만 다시 돌리면 07 에 `--confirm` 이 붙지 않는다(`etx_flow.py:249`, `'05' in steps` 일 때만 붙음). | `['05','06','07']` 로 돈 뒤 `['07']` 만 다시 돌리면 `windows_correspondence_sha256` mismatch(종료 2)로 멈춘다. 옛 보고서는 이미 `_runs` 로 옮겨진 뒤다. | 06 출력의 provenance 로 대응 종류를 판단한다. 또는 문서에 "DL2 경로는 `['05','06','07']`" 을 적는다. |
| E2 | EDIT 블록 주석은 "02 부터 = [02, diag, 04, 06, 07]" 이라고 적는다. 문서는 입력 해시가 바뀌면 03 부터라고 적는다. | 주석대로 condition_assignment 를 바꾼 뒤 02 부터 돌리면 04 가 `source_asset_manifest_identity_mismatch` 로 멈춘다. | 주석을 문서와 맞춘다. |
| E3 | `models_04` 실행기는 창 `10 20 30`, 지름 `5` 를 상수로 적는다. 통합 실행기는 `compare2.yaml` 값(템플릿 3 µm, 10 µm)을 쓴다. 통합 실행기로 06 을 다시 돌리면 `data/local/models/` 까지 보관 폴더로 옮겨진다. | 두 흐름을 섞으면 표시 지름이 흐름마다 다르다. models_05 의 입력이 사라진다. | models 실행기도 설정 파일에서 읽게 하거나, 문서에 한 줄씩 적는다. |
| E4 | 보관 이동이 중간에 실패하면 이미 옮긴 목록만 찍힌다. | `RUN_TAG=None` 으로 다시 돌리면 한 회차의 보관이 두 태그로 나뉜다. | 메시지에 "같은 RUN_TAG 를 적어 다시 실행" 을 안내한다. 실패 경로 테스트를 더한다. |
| E5 | "01 만 다시" 돌릴 때 02~07 의 옛 출력이 남는다. 입력 해시 검사가 막으므로 조용한 오염은 아니다. | — | 문서에 "01 을 고르면 뒤 단계도 함께 고른다" 를 한 줄 넣는다. |
| E6 | 사전 검증이 일부 빠졌다(`etx_flow.py:195`, `:237`, `:210`). | `report.diameter_um` 이 04 허용 값이 아니면 01~diag 를 다 돈 뒤 04 에서 멈춘다. `EXTRA_WINDOWS_UM=None` 이나 잘못된 타입이면 traceback 이 난다. | 계획 단계에서 검사한다. 기본값(지름 3, 창 30)을 문서에 적는다. |

## 거리 필터·기록 (`src/simcompare/adapters/real_hotspot.py`, `interlayer_distance.py`, `compare2_report.py`)

| # | 내용 | 제안 |
|---|---|---|
| D1 | 07 에서 기록된 input_hash 가 없을 때 오류 코드가 링크 옵션에 따라 다르다. 링크가 있으면 `compare2_link_input_mismatch`, 없으면 `compare2_input_hash_missing` 이다. 둘 다 종료 2 다. | 맞춘다. |
| D2 | 기록된 input_hash 가 없으면 지도를 다 그린 뒤에야 거부한다. | 마스크를 읽지 않는 missing 검사를 stale-rule 검사 쪽으로 앞당긴다. |
| D3 | 04 의 `distance_filter` 블록 값이 02 블록과 같다는 직접 테스트가 없다. 06↔02 는 있다. | 테스트를 더한다. |
| D4 | `local_delta.json` 의 `candidates_truncated` 는 a·b 가 같이 쓰는 플래그 하나다. | 필요하면 label 별로 나눈다. |
| D5 | thk 거리 정규화가 Decimal 기본 정밀도 28자리 context 를 쓴다. 유효숫자가 28자리를 넘는 서로 다른 거리는 같은 표 sha 를 받을 수 있다. 비교는 정확한 값으로 한다. | 실무 영향은 없다. 필요하면 정밀도를 명시한다. |
| D6 | (vdist-expected) 테스트 단언이 약하다. 06 그룹 상태는 `partial` 또는 `unavailable` 로만 단언하고, 07 의 `map_input_missing` 경고와 (h) 의 직경 행은 고정하지 않았다(`tests/test_vdist_expected.py:205`, `:246`, `:249`). | 계획대로 강화한다. |

## 그 밖

| # | 내용 | 제안 |
|---|---|---|
| O1 | `window_cases._key`(models 흐름)도 06 처럼 층을 무시하고 전극 이름만으로 case 를 찾는다. | 06 의 (전극, 층) 매칭과 맞춘다. |
| O2 | `cli.py:339` 의 `manifest_path if manifest_path is not None else …` 앞 분기는 이제 실행되지 않는다(`--config` 와 `--manifest-path` 를 같이 주면 먼저 막음). | 정리한다. |
| O3 | `configs/etx/templates/real.yaml` 의 `layer_stack` 에 CNT 줄이 없다. ETX 는 `lateralopen-CNO` case 때문에 CNT 를 등록해야 하고, 사용자가 직접 넣었다. CNT 는 thk 조회에 쓰지 않는다. | 사용자 ETX 값(`stack_order`, `density_process_layer`)을 받아 템플릿에 넣는다. |
| O4 | `max_operation_bytes` 는 `condition_assignment` 안이라 input_hash 에 들어간다. 그래서 07 이 메모리 한도로 멈춘 뒤 한도를 올리면 02 부터 다시 돌려야 한다. 지금은 안내 문구로만 대응한다. | 자원 한도를 해시에서 빼려면 동결 비교 영향을 따로 계획한다. |
