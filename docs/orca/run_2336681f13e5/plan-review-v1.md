# 계획 v1 검토·채택 기록 (Opus coordinator)

- 검토자: coordinator `term_889af2e3-9387-477f-965b-075206f2b853` (`claude-opus-5-5`), 2026-10-01
- 대상: `plan-v1.md` (Astra high, `task_4563cf66520a` / `ctx_fd4c5866dd7c`, `worker_done` outcome=succeeded, message `msg_d466ab802158`)
- effort 기록: 세션은 `--effort medium`으로 시작했다. 검토 전에 자기 터미널에 `/effort high`를 보냈고 입력은 수락되었으나(`requestId 5588075b…`), 진행 중인 turn 안에서는 적용 여부를 화면·환경변수로 **확인하지 못했다**(`CLAUDE_EFFORT=medium`은 시작 값). 따라서 "high로 검증됨"이라고 주장하지 않는다. 검토 자체는 아래 항목을 직접 코드와 대조해 수행했다.
- 플래너 산출 후 `git diff | shasum -a 256` = `a00a5706…a8b3` (기준선과 동일), 추가된 것은 untracked `docs/orca/`뿐.

- 추가(검토 후): 읽기 전용 관찰자 `term_83eeafed…`가 `msg_b02caee32619`로, 계획 검토 중 이 터미널 화면에 "thinking with high effort" / "high · /effort"가 표시됐다고 보고했다. 이는 관찰자 보고 증거이며 coordinator 자체 확인은 아니다.

## 결정: **채택 (v1 + 아래 coordinator 보완 A1–A7)**

보완은 설계를 바꾸지 않는 추가 조건이므로 Astra 재계획 없이 Task 계약에 넣는다.

## 검토 항목

| 항목 | 판단 |
| --- | --- |
| 요구 범위 | ROI CA 측정(원본 정량 map), 동일 전극/layer/불량/공정/크기 비교, hotspot 포함 공통 창, 위치 중심 리포트(HTML+JSON/CSV+그림), 대응표 입력 인터페이스, Fab·Tree 제외 경계 — 모두 Task에 배정됨 |
| 정량 원천 | coordinator가 직접 확인: particle-sim `critical_area_um2` = `sum(map) × pixel_area` (`aggregation/critical_area.py:10–20`), 같은 `pair_result.failure`를 store에 쓰고 적분(`pipeline.py:382–412`), mean은 probability map 적분(`pipeline.py:741–758`). 계획의 "typed source + 전체 영역 보존 gate, 그 외 unavailable" 결정은 사실과 일치 |
| 사용자 결정 반영 | hotspot 재팽창 금지(R1), 표시 여백과 정량 창 분리, 대응 불명≠부재/0, 이름·embedding 대응 금지(C6), 양쪽·전 크기 support를 포함한 고정 창(R2/R3), 미등록 시 픽셀 차감 없음(C7) |
| 불변식 | raw CA/share/면적 정규화 분리(M6), legacy 무차원≠um²(S3), 원점 None≠0·anisotropy 거부(M5), 중첩 union 1회 적분(M3), 결측≠0·0 기준 상대증감 null(C3/C4), 구조 추가/부재와 위험 출현/소멸 구분(C5), nominal/mean 혼용 금지(S2/C9) |
| 읽기/쓰기 범위 | 신규 모듈 중심, 기존 모듈은 `schema.py`(optional 필드), `config.py`, 두 adapter의 작은 연결, `pipeline.py`의 얇은 호출. 형제 저장소 수정 없음 |
| 의존 순서 | T1(계약·측정) → T2(대응·창·비교) → T3(리포트·연결). 실제 데이터 의존이며 editor 1명 순차 실행과 부합 |
| 중단·재시도 | 6절에 기준선 hunk 보존, blanket reset 금지, `--retry-of` 규칙 명시 |

## Coordinator 보완 (Task 계약에 포함)

- **A1 대표 사례 생성기**: 기본 합성 demo의 hotspot은 `processed_intensity`라 정량 원천이 아니다. 대응표·원천 manifest가 없으면 비교가 비어 보이므로, T3는 결정적 대표 사례(정량 nominal, 총량 감소/국부 증가, 이동·신규·소멸, source unavailable, 미등록 쌍)를 만드는 `scripts/regional_demo.py`와 예시 대응표/원천 manifest를 제공하고, 학습 없이 지역 분석만 실행해 `outputs/regional-demo/`에 리포트를 쓴다. 같은 builder를 테스트 fixture로 재사용한다.
- **A2 창 밖 잔여**: 최종 창은 서로 겹치지 않고 support 전체를 덮으므로, source마다 `outside_regions_ca_um2 = total − ∪창 적분`을 출력하고(정상 0), "겹치지 않는 최종 창의 합 + 창 밖 잔여 = 전체"를 테스트한다. 누락된 hotspot이 있으면 수치로 드러난다.
- **A3 유지보수성(사용자 명시 요구)**: 각 신규 모듈 상단 docstring에 책임·하지 않는 일·"무엇을 바꾸려면 여기를" 안내를 한국어로 적고, 모듈 안은 주석 구획으로 기능을 구분한다. 숫자 상수는 설정 키 또는 이유가 적힌 이름 있는 상수로 둔다. `docs/regional_analysis.md`에 "변경 목적 → 수정할 모듈/함수" 표를 둔다.
- **A4 기존 dirty 파일**: `adapters/particle_sim_run.py`, `tests/test_particle_sim_export_integration.py`에 쌓는 hunk는 최소·추가형이어야 하고 기존 3파일의 선행 hunk는 그대로 둔다. `docs/open_questions.md`는 worker가 수정하지 않는다. worker는 자신이 추가한 hunk를 보고에 구분해 적는다.
- **A5 생산자 통합 gate**: 설치 없이 `PYTHONDONTWRITEBYTECODE=1 PARTICLE_SIM_ROOT=/Users/jonghyeonlee/git/particle-sim PARTICLE_SIM_PYTHON=/Users/jonghyeonlee/git/particle-sim/.venv/bin/python`으로 `tests/test_particle_sim_export_integration.py`를 실행한다(기준선: 1 passed, 1 skipped). skip은 PASS로 세지 않는다.
- **A6 시각 검증**: 브라우저/스크린샷 확인은 coordinator가 최종 후보에서 수행한다. worker는 리포트 경로와 자동 검사 결과를 넘긴다(가능하면 설치 없이 headless 스크린샷).
- **A7 Git**: worker는 commit/stash/reset/checkout을 하지 않는다. 후보는 작업 트리 diff 지문으로 식별한다.

## 채택한 기본값 (계획 6절 a–d)

(a) translation + 동일 isotropic lattice만 지원, 교차 sample 비교는 명시 대응표 필수 — 채택. (b) `analysis.regional.enabled` 기본 true — 채택(기존 산출물은 유지, 지역 원천이 없으면 사유와 함께 global CA만 표시). (c) typed nominal 자동 승격 + 명시 density manifest로 mean 연결 — 채택. (d) 3 Task 순차 — 채택.

## 남는 한계 (최종 보고에 포함)

- 현 particle-sim exporter는 nominal map만 내보내므로 mean ROI CA는 direct run 또는 명시 manifest 경로에서만 가능하다(upstream 의존).
- 실사용 대응표·좌표 등록, Fab 가중은 폐쇄망 자료가 있어야 한다.
