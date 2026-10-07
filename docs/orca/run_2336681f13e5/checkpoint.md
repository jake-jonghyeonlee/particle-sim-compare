# Orca checkpoint — ROI CA 비교 및 위치 중심 종합 리포트

Orca 상태(Run/Task/Dispatch/liveness)가 정본이다. 이 문서는 복사본이므로 행동 전 Orca와 대조한다.

## 식별자

| 항목 | 값 |
| --- | --- |
| Run | `run_2336681f13e5` |
| Coordinator | `term_889af2e3-9387-477f-965b-075206f2b853` (Claude `claude-opus-5-5`, effort medium, bypassPermissions) |
| 관찰자(읽기 전용, lifecycle 권한 없음) | `term_83eeafed-39d4-4131-8831-497fb242c9f7` (Codex) |
| Orca runtime | 1.4.215, CLI `/usr/local/bin/orca` |
| 요청 원문 | `docs/orca/run_2336681f13e5/request.txt` (sha256 `22936766…9d869`) |

## 기준선 (2026-10-01)

- 저장소 `/Users/jonghyeonlee/git/particle-sim-compare`, branch `record-cno-open-fab-decisions`, HEAD `b507da96554baa658171dcae61a99342c9ec36e3`
- 이 Run 이전부터 있던 미커밋 변경 3파일(보존 대상, 새 작업으로 주장 금지):
  `docs/open_questions.md`, `src/simcompare/adapters/particle_sim_run.py`, `tests/test_particle_sim_export_integration.py`
  - `git diff` sha256 `a00a5706a34852ba3bd4c72397d87268b510ed5cce666a586aeeeb1dfc8a28b3`
  - 사본: `docs/orca/run_2336681f13e5/baseline-preexisting.diff`
- 기준선 테스트: `.venv/bin/python -m pytest` → 209 passed, 56 skipped (skip 사유: `pyarrow` 미설치, `PARTICLE_SIM_ROOT`/`PARTICLE_SIM_PYTHON` 미설정)
- 형제 저장소 `/Users/jonghyeonlee/git/particle-sim`: 읽기 전용 참조. branch `decisions-cno-centre-fab-upper-layer`, HEAD `8d4edaf0de93da607e8a55e8f1405cc1fa607f5d`, 자체 미커밋 변경 있음(건드리지 않음)

## 시작 전 확인

- 모델/effort: 세션 모델 `claude-opus-5-5`, `CLAUDE_EFFORT=medium`, 터미널 실행 인자 `--model claude-opus-5-5 --effort medium --permission-mode bypassPermissions`
- 소유권: 시작 시 이 터미널에 바인딩된 Run 없음 → 새 Run 생성·바인딩. 과거 Run `run_b8caa345d874`, `run_e97dac93a45e`는 settled Dispatch만 있음(변경하지 않음). 유일한 active worker는 다른 저장소(schedule-app, `run_e13c918780a2`) 소속으로 무관
- MMO: 이 저장소에 활성 MMO 작업 없음(`mmo` CLI·프로세스·상태 디렉터리 없음; README의 mmo 언급은 과거 구현 이력). 이번 Run에는 MMO를 쓰지 않음
- Codex hooks: `~/.codex/hooks.json`(sha256 `3f21b11b…a549`)과 Orca 관리 계정 홈 `hooks.json`(sha256 `864558ab…98dd`) 검토 — 항목은 `vibe-island-bridge --source codex`와 Orca `codex-hook.sh`(상태 spool)뿐
- 실행 경로: 사용자 정책에 따라 명시적 터미널(`terminal create` → `wait tui-idle` → `worker-start --task --terminal`). structured 경로는 새 smoke 통과 전까지 복원하지 않음

## Task / Dispatch

| 단계 | Task | Dispatch | 터미널 | 모델/effort | 상태 |
| --- | --- | --- | --- | --- | --- |
| 계획 v1 (Astra, 읽기 전용) | `task_4563cf66520a` | `ctx_fd4c5866dd7c` | `term_21deb761-…` (종료) | gpt-6-astra / high (화면 `GPT-6-Astra high` 확인) | **완료·채택** — `worker_done` succeeded (`msg_d466ab802158`, delivery `delivery_02e9f083df99` ack), release 후 터미널 종료 |
| T1 정량 원천·보존 gate·ROI 적분 | `task_e9552b4be1b9` | `ctx_8865d5290b33` | `term_fab901b1-8ce3-4101-8e76-8070dcbf9cbf` | gpt-6.1-sol / medium (화면·provider 확인) | **완료·채택** — `worker_done` succeeded (`msg_ea1e59e5e4b0`, delivery `delivery_92fec7856a1c` ack). coordinator 재검증: 전체 259 passed / 56 skipped, 생산자 통합 1 passed / 1 skipped(pyarrow), `git diff --check` 통과, 기존 3파일 선행 hunk 보존(추가 hunk만). tracked diff sha256 `50f806e4…2c4e`, 후보 지문(추적 diff+신규 5파일 해시) `afd415ee…a4e9` |
| T2 대응표·공통 창·비교 | `task_ab2b64f148f1` | `ctx_c11f24fe64e5` | 같은 Sol 터미널 재사용 | gpt-6.1-sol / medium | **완료·채택** — 질문 1건 답변(결정 기록 참조), `worker_done` succeeded (`msg_fb69c5bf8b7f`, delivery `delivery_6bdf11a61de4` ack). coordinator 재검증: 전체 320 passed / 56 skipped, `git diff --check` 통과, `docs/open_questions.md` 기준선과 동일, tracked diff sha256 `2acd5926…649e`, 후보 지문(추적 diff+신규 src/tests 해시) `7c1a29c9…26eb` |
| T3 리포트·analyze 연결 | `task_80c7382927a1` | `ctx_c11100707ccf` | 같은 Sol 터미널(`term_fab901b1…`, 수정 라운드용으로 유지) | gpt-6.1-sol / medium | **완료·채택(리뷰 대상)** — `worker_done` succeeded (`msg_b6075f813275`, delivery `delivery_0482cfbb0fa8` ack, release=external). coordinator 재검증: 전체 333 passed / 56 skipped, 생산자 통합 1 passed / 1 skipped(pyarrow), `git diff --check` 통과, `docs/open_questions.md` 기준선과 동일, `scripts/regional_demo.py` 재실행 성공, headless Chrome 스크린샷으로 대표 구간(added, moved/new/disappeared, total-down-local-up, unregistered, source-unavailable) 확인 |
| 독립 리뷰 v1 | `task_ad261bc34ace` | `ctx_7ea3ac9ebb41` | `term_878b87bc-32b9-4692-96a8-f4a6db125bac` (재리뷰용 유지) | claude-opus-5-5 / high (화면 확인) | **완료: PASS, blocking 0, should-fix 6(S1–S6), nit 9** — `worker_done` succeeded (`msg_8aa4f1b68ae1`, delivery `delivery_101e648d61d2` ack). 후보 v1 해시 시작·종료 일치. 결과 `review-v1.md`. PASS는 후보 v1에만 유효 |
| T4 리뷰 지적 수정 | `task_a389fc320613` | `ctx_fac99eb5b0d5` | Sol 터미널 재사용 | gpt-6.1-sol / medium | **완료·채택** — `worker_done` succeeded (`msg_b00db6cc6457`, delivery `delivery_b21197256d81` ack). coordinator 재검증: 전체 360 passed / 57 skipped(pyarrow 54, 생산자 env 3), 생산자 env 지정 시 2 passed / 1 skipped(새 양성 보존 테스트 PASS), `git diff --check` 통과, 기준선 hunk 보존, 데모 재실행 성공(HTML 478 KB → 246 KB). 후보 지문 `d7386c31…3e30`. worker 측정: 512×512·64창 리포트 8지름 1.78 MB/1.57 s, 32지름 5.61 MB/6.81 s (리뷰 측정 35 MB/25 s 대비). 항목별 표 `outputs/regional-demo/t4-review-fixes.md`. 남은 것: 120자 초과 21줄 → T5에 포함, N9는 문서화 |
| T5 binary 확정 계약 문서·audit 수정 | `task_f4826fe13c53` | `ctx_f8d6543d0bb1` | Sol 터미널 재사용 | gpt-6.1-sol / medium | **완료·채택(단, `_roi` 회귀는 T6로)** — 지문 질문 1건 답변(정렬 순서 차이, 이후 `LC_ALL=C sort` 통일), `worker_done` succeeded (`msg_6e3c29006fda`, delivery `delivery_b7b65c6086a1` ack). coordinator 재검증: 전체 369 passed / 57 skipped, 생산자 env 2 passed / 1 skipped, `git diff --check` 통과. `open_questions.md` 'Hotspot 색상·투명도·축척' 행 갱신 확인(사용자 요청, `msg_3535ff2b7e7b`). 후보 지문(C sort) `5ed1884b…dbc6`. 보고 `outputs/regional-demo/t5-binary-contract.md` |
| T6 `_roi` 다중 성분 메모리 회귀 수정 | `task_09edb62f0bd2` | `ctx_ce713beea000` | Sol 터미널 재사용 | gpt-6.1-sol / medium | **완료·채택** — 복잡도 질문 1건 답변(성분 탐색·메모리 O(N), 전체 O(N + K log K), `_contains`와 동일 판정의 누적합 검사 허용), `worker_done` succeeded (`msg_a410835838af`, delivery `delivery_11705ec87c02` ack). coordinator 재검증: 전체 381 passed / 57 skipped, 생산자 env 2 passed / 1 skipped, `git diff --check` 통과. coordinator 실측 512×512 체커보드(131072 성분): 2.9 s, tracemalloc peak 37.6 MiB, 올바른 ROI. 이후 coordinator가 `variants.py` 모듈 docstring 줄바꿈만 정리(표시상 수정, 후보 v2에 포함) |
| 독립 재리뷰 v2 | `task_e969c7995706` | `ctx_10ef7514ac17` failed → `ctx_c59313f3ef74` failed → `ctx_23722561e819` succeeded | 새 세션 `term_83ff38b8…` (종료) | claude-opus-5-5 / high (화면 확인) | **완료: PASS, blocking 0, should-fix 4(F1–F4), nit 11** — `worker_done` succeeded (`msg_9be244c0ad36`, delivery `delivery_84e58d62e21b` ack), release 후 터미널 close. 후보 v2 해시 시작·종료 일치. 결과 `review-v2.md`. 리뷰어 실측: 512² 체커보드 37.2 MiB / 0.1–0.5 s, 리포트 512²×8지름 1.6 MB, ×32지름 5.0 MB. PASS는 후보 v2에만 유효 |
| T7 재리뷰 v2 지적 최종 수정 | `task_8f82e4ac52b9` | `ctx_082ad7babb39` | Sol 터미널 재사용 | gpt-6.1-sol / medium | **완료·채택** — 순서 질문 1건 답변(import 순서 정리 먼저, 이후 서식 단계는 AST 동일성 검증), `worker_done` succeeded (`msg_16ed15e94e9c`, delivery `delivery_c56f7662ae52` ack). coordinator 재검증: 전체 386 passed / 57 skipped, 생산자 env 2 passed / 1 skipped, `git diff --check` 통과, 두 코드 파일 기준선 hunk 보존, 120자 초과 0줄, 데모 재실행 성공. 데모 CSV: 비교 CSV는 T4 시점 산출과 동일, 측정 CSV는 provenance/validation JSON 열의 절대 출력 경로만 다름(수치 열 동일). N-f는 보류(캐시 계약 변경 범위). 보고 `outputs/regional-demo/t7-review-fixes.md` |
| 독립 재리뷰 v3 (최종) | `task_87fdce91694a` | `ctx_e82666388a86` | 새 세션 `term_fc8c8b5f…` (종료) | claude-opus-5-5 / high (화면 확인) | **완료: PASS, blocking 0, should-fix 3(S1–S3), nit 8** — `worker_done` succeeded (`msg_e0f17874fb04`, delivery `delivery_18bb7326ba38` ack), release 후 터미널 close. 후보 v3 해시 시작·종료 일치. 결과 `review-v3.md`. PASS는 후보 v3에만 유효 |

산출물: 계획 `plan-v1.md`, 계획 검토·채택 `plan-review-v1.md`(보완 A1–A7), Task spec `task-plan-v1.spec.md`, `task-T1.spec.md`, `task-T2.spec.md`, `task-T3.spec.md`.
메모: Codex 0.159.2 시작 시 업데이트 안내는 매번 "Skip"(2) 선택 — 전역 npm 업데이트는 하지 않음.
effort 메모: 계획 검토 전 `/effort high`를 자기 터미널에 보냄. coordinator 자신은 적용을 확인하지 못했으나, 관찰자(`term_83eeafed…`, 읽기 전용)가 `msg_b02caee32619`로 계획 검토 중 화면에 "thinking with high effort"가 표시됐다고 보고함(관찰자 보고 증거). T1 채택 후 `/effort medium`을 보내 routine 조정으로 복귀 요청. 상세는 `plan-review-v1.md`.
생산자 통합 기준선: `PARTICLE_SIM_ROOT`/`PARTICLE_SIM_PYTHON` 지정 시 `tests/test_particle_sim_export_integration.py` 1 passed, 1 skipped.

## 마일스톤

- [x] 기준선·소유권·모델 확인, Run 생성
- [x] Astra 계획 v1
- [x] Opus 계획 검토·채택 (v1 + A1–A7)
- [x] Sol 구현 (T1·T2·T3 완료·채택)
- [x] 독립 Opus high 리뷰 (v1 PASS → 수정 → v2 PASS → 수정 → v3 PASS, 후보 v3)
- [x] 최종 검증·보고 (2026-10-01 10:5x UTC)

## 결정 기록

- 2026-10-01 T2 질문 `msg_ac223453f709`(delivery `delivery_3bd98b855d13`): `confirmed_removed`에서 query 구조 부재를 표현할 수 없음 → coordinator 답변: 대응표를 대칭 계약으로 명확화(same=양쪽 필수, added=query만, removed=reference만, unknown=한쪽 이상·부재 추론 금지). 부재 쪽은 선언된 context(product_id, design_version)만 표시, CA 0 채움 금지, delta null+사유. 계획 3.2의 국소 명확화이며 설계 변경 아님.

## 방법론 검토 (사용자 추가 요청, 2026-10-01 08:02 UTC 수신) — 구현 계획을 대체하지 않는 자문

- 요청: 제품군 간 대응/추적(매칭·신규·소멸, 다수 취약 위치) 방법론을 Opus와 Astra가 각각 검토 후 종합. 채택된 구현 계획(T1–T3)과 Sol 편집 worker는 그대로 유지.
- Task `task_6d47b7d20c02` (Astra high, read-only), 터미널 `term_c535591f-5da4-438e-bfa7-59afc3feb062` (화면 `GPT-6-Astra high`, YOLO 확인). Spec: `task-method-review.spec.md`.
- Dispatch 이력: `ctx_1d5f60782761` failed(agent_readiness timeout) → `ctx_15964e435376` failed(같은 사유, `--retry-of`) → `ctx_36086083d127` ready/input_accepted (`--retry-of ctx_15964e435376`, 08:09 UTC).
  - 원인: 새 Codex 세션이 첫 turn 전까지 Orca hook 상태 행을 만들지 않아 readiness 검사가 60초 timeout(`last-status.json`에 해당 pane 없음, 터미널 제목 `zsh…`). 세션 사망이 아님(관찰자 보고와 일치).
  - 조치: 같은 터미널에 무해한 readiness probe 프롬프트("reply READY") 1회 → 제목 `Codex ready`로 상태 등록 확인 → 같은 Task·같은 터미널로 재시도. 중복 리뷰어/새 editor 없음.
  - 이전 planner/Sol 시작 때는 같은 절차로 성공했으므로 간헐적 현상. 다음 Codex 터미널 시작 시에도 `worker-start` 전 제목/상태 등록을 확인한다.
- 산출물(예정): `/tmp/particle-sim-method-review-20261001-{opus,astra,synthesis}.txt`
- coordinator 판단(2026-10-01 08:14 UTC): 방법론 검토 요청은 구현 계획을 대체하지 않는다. T1–T3(정량 ROI CA, 자체 영역, 명시 대응표, 리포트)는 어떤 대응 방법에서도 전제(심각도 축·보존 검증)이므로 T3를 hold하지 않고 진행한다. 미등록 제품 간 자동 국부 대응은 계획 v2 후보이며 이번 Run에서 자동 구현하지 않는다(T3 문서에 미구현 확장 지점으로 기재).
- 관찰자 메모 `msg_b68893c644a7`: T2 진행 중 docstring에 일본어 표기가 보였다는 보고 → 채택 시점 소스에는 일본어/한자 없음(스캔 확인), T3 계약에 한국어 주석 규칙 명시.
- effort: 방법론 검토를 위해 08:02 UTC `/effort high` 전송, 도구 셸에서 `CLAUDE_EFFORT=high` 확인.
- 방법론 검토 완료(2026-10-01 08:3x UTC): Astra Dispatch `ctx_36086083d127` `worker_done` succeeded(`msg_d88290af49ab`, delivery `delivery_0cd44a9f9f1f` ack), `worker-release`(external, processAction none) 후 터미널 `term_c535591f…` close(ptyKilled). `worker-list --terminal-state reclaimable` = 0. 보고서: `/tmp/particle-sim-method-review-20261001-{opus,astra,synthesis}.txt`. 결론: 구조 대응과 CA 심각도 분리, 단순 기준 방법부터, T1–T3 유지, 미등록 제품 간 국부 구조 대응은 계획 v2 후보(사용자 승인·확인 사항 필요, 이번 Run에서 미구현).
- 후보 v1(2026-10-01 08:42 UTC): 파일 29개 해시 목록 `candidate-v1.sha256`(sha256 `adb0fce1…a8a6c`), tracked diff 사본 `candidate-v1.tracked.diff`(sha256 `e71a1f9c…23fb`). 리뷰 계약 `task-review-v1.spec.md`, 산출물 `review-v1.md`.
- 시각 확인 메모(coordinator, cosmetic): 리포트 표의 사유 토큰이 좁은 열에서 줄바꿈됨, 기여율 소수 자릿수가 김, reference 부재 칸에 `null` 문자열 노출. 리뷰 결과와 함께 수정 라운드에서 처리 여부 결정.
- coordinator 판단(2026-10-01 09:0x UTC): 리뷰 v1은 PASS지만 S1(이미지와 manifest 원천 frame 불일치 시 available로 틀린 ROI), S2(지름 float 일치·중복 검사 우회), S3(위치 불명과 관측 0이 union 요약에서 같음)은 요청 계약의 "mismatch → unavailable, missing ≠ zero"와 닿아 있어 실제 자료 전에 수정한다. S4(리포트 크기), S5(pyarrow 없는 양성 보존 테스트), S6(스타일·중복, 사용자 명시 요구), nit, 약한 테스트, 시각 nit도 같은 수정 Task T4로 묶음. 국소 결함이므로 원 worker(Sol medium)에 배정, 계약 `task-T4-review-fixes.spec.md`. 수정 후 후보 v2를 고정해 같은 독립 리뷰어 세션에 재리뷰 요청(v1 PASS는 v2에 재사용하지 않음).
- 사용자 추가 확정(2026-10-01, 관찰자 경유 + 사용자 직접 보정 3건): nominal hotspot은 binary, 같은 source·지름 안에서 픽셀 계수 k 상수(`CA_R = k × N_active,R`), particle-sim 면적 nominal에서만 k = pitch². legacy 무차원→µm² 환산 계수는 미확정. audit: `audit-binary-hotspot-contract.md`(B1–B8). T5 계약: `task-T5-binary-contract.spec.md` — 정본 문서 갱신 + B1(read_image 값 범위·semantics) + B2(`variants._roi` 최소 수정). B3(비-exporter binary 선언 경로), B4(무차원 CA 기여율)는 미구현·결정 사항으로 문서화.
- 사용자 지적(2026-10-01 09:48 UTC): T5의 `variants._roi` 다중 성분 처리가 성분마다 전체 mask를 보관해 O(K·N) 메모리/시간(512² 체커보드 ≈ 32 GiB). coordinator가 diff로 확인. T5 worker에 `msg_752e465483b0`로 전달했으나 `worker_done`(09:49:58) 직전이라 미반영 → 별도 Task T6(`task-T6-roi-memory.spec.md`)로 수정. 남은 120자 초과 줄 정리도 T6에 포함.
- 후보 v2(2026-10-01 09:59 UTC): 파일 35개 해시 목록 `candidate-v2.sha256`(sha256 `26c1397e…1064`, `LC_ALL=C sort`), tracked diff 사본 `candidate-v2.tracked.diff`(sha256 `4869b415…b116`). v1 대비 22개 변경, 6개 추가. 기준선 대조: `particle_sim_run.py`·`test_particle_sim_export_integration.py`의 선행 hunk는 그대로(`git apply --check --reverse` 통과), `docs/open_questions.md`는 T5에서 사용자 요청으로 갱신. 재리뷰 계약 `task-review-v2.spec.md`, 산출물 `review-v2.md`.
- 재리뷰 시작 복구(10:00–10:03 UTC): v1 리뷰어 세션(`term_878b87bc…`) 재사용 시 `agent_readiness` timeout 2회(`ctx_10ef7514ac17`, probe 후 `ctx_c59313f3ef74`). 세션은 살아 있었고 probe에 READY로 답했으나, 해당 터미널은 `terminal wait --for tui-idle`도 timeout이라 Orca의 idle/상태 인식이 release 후 재사용 경로에서 맞지 않는 것으로 판단. 같은 start를 반복하지 않고, 활성 Dispatch가 없는 그 세션을 닫은 뒤 새 Opus high 세션을 만들어 같은 Task를 `--retry-of`로 시작. 읽기 전용 slot은 1개 유지. (정책상 독립 리뷰는 원래 새 세션이 기본이기도 함.)
- coordinator 판단(2026-10-01 10:20 UTC): 재리뷰 v2는 PASS. F1(기하가 맞아도 frame_id 문자열 때문에 정상 manifest 원천 차단), F2(frame 차단이 요약·run_record 집계에 안 보임), F3(정수 0/255 NPY로 학습한 checkpoint 재학습 안내 누락), F4(기계적 줄바꿈·공백 스타일, 사용자 명시 요구)와 문서 nit을 **마지막 한 번의 제한된 수정 라운드 T7**로 처리(`task-T7-review2-fixes.spec.md`, 수치 불변 조건). 이후 후보 v3에 대해 차이분 재리뷰. 그 뒤 남는 nit은 수정하지 않고 최종 보고에 기록한다(무한 반복 방지).
- 후보 v3(2026-10-01 10:38 UTC): 파일 35개 해시 목록 `candidate-v3.sha256`(sha256 `e3a07479…7060`, worker 보고 지문과 일치), tracked diff 사본 `candidate-v3.tracked.diff`(sha256 `299a087b…9e44`). v2 대비 16개 파일 변경. 재리뷰 계약 `task-review-v3.spec.md`, 산출물 `review-v3.md`.
- 관찰자 메모 `msg_e3a6bd7deaff`(formatter + AST 동일성 제안): AST 동일성 검증은 채택해 T7에 지시. formatter 일괄 적용은 미채택(ruff/black 미설치, 저장소에 formatter 설정 없음, 기본값이 기존 모듈 스타일과 다름).
- 시각 확인(coordinator, 후보 v3 데모, headless Chrome): 머리말 집계, 전체 감소·국부 증가 구간의 overview/crop/곡선/표 확인. 기여율 4자리 고정, `null` 문자열 없음, 외부 URL·script 없음. 남은 표시 사항: 1400px 폭에서 마지막 열(선정/변화 근거)은 표의 가로 스크롤 영역 안에 있어 정적 캡처에서는 잘려 보임.

## 최종 상태 (2026-10-01 10:55 UTC)

- 최종 후보: **candidate v3** (`candidate-v3.sha256`, 35개 파일, sha256 `e3a07479…7060`). 최종 확인 시 35개 해시 모두 일치. HEAD는 `b507da9…` 그대로이며 **commit/push 없음**(미승인). 형제 저장소 `particle-sim`은 HEAD `8d4edaf…`, 변경 파일 수 12로 시작 시와 동일(이 Run에서 편집 없음).
- 최종 검증(coordinator, 후보 v3): `.venv/bin/python -m pytest` 386 passed / 57 skipped(pyarrow 54, 생산자 env 3) / warning 1; 생산자 env 지정 2 passed / 1 skipped(pyarrow); `scripts/regional_demo.py` 성공; headless Chrome 스크린샷으로 리포트 확인.
- 독립 리뷰: v3 PASS(blocking 0). 남긴 항목(이 Run에서 수정하지 않음): S1 같은 지름 hotspot 이미지가 없는 manifest 원천은 frame 검사를 건너뜀, S2 같은 pitch를 nm/um으로 다르게 표현하면 float 비교로 pair delta가 막힘, S3 일부 줄바꿈·들여쓰기 잔여, nit N1–N8(N-f 보류 포함), B3/B4 결정 사항.
- Orca 정산: 이 Run의 Dispatch 16개 = succeeded 12, failed 4(모두 `agent_readiness` 시작 실패 후 `--retry-of`로 재시도 성공). active 0, reclaimable 0, inbox 0. coordinator가 만든 worker 터미널(Astra 2, Sol 1, Opus 리뷰어 3)은 모두 종료.
- 기존 미커밋 3파일: `adapters/particle_sim_run.py`, `tests/test_particle_sim_export_integration.py`는 선행 hunk 보존 + 추가 hunk만. `docs/open_questions.md`는 사용자 요청으로 지정 행·bullet만 갱신.
