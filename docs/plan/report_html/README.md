# 통합 HTML 보고서: 사용법과 이어하기 (2026-10-19 21:35 기준)

계획: [`../report_html_plan_v1.md`](../report_html_plan_v1.md). 특히 §7의 v1.1 수정을 함께 본다.
이 폴더에 있는 것:
- `T1.md`, `T2.md`: Orca Task 명세 원문
- `report-mockup-v1.html`: 가짜 데이터 시안
- `mockup_gen.py`: 시안 생성 스크립트

---

## 1. 지금 상태

| 항목 | 상태 |
|---|---|
| Orca Run | `run_bb47043d1dd3` |
| T1 `task_39f7a2ac48f9`: crop/전체 frame PNG 저장 | **완료·검증됨**. Sonnet 5.5 medium(실행값 확인), dispatch `ctx_a5329b4de608`. worker는 release됨. 전체 pytest 통과(rc=0) |
| T2 `task_c6442f8d5c21`: `simcompare report` 명령 | **완료·검증됨** (2026-10-19 22:30). Sonnet 5.5 medium, dispatch `ctx_41b0fff5cd3d`, release됨. 전체 pytest rc=0, 데모 report exit 0, 기준 창 없음 → exit 2 확인. 추가 수정: `cli.py`, `docs/usage.md`, `scripts/2_demo.sh`, `tests/test_retired_paths.py`, 신규 `final_report.py`, `tests/test_final_report.py` |
| 커밋 | `801c8da` (T1+T2, 다른 세션이 사용자 승인으로 커밋, push 없음) |
| Astra high 리뷰 `task_4f57b4361a73` | **FAIL** (2026-10-19 22:42). 수정 필요 6건 + 메모 2건. 전문: `astra_review_801c8da.md` |
| FIX1 `task_0c9e516cd11c` | **완료·검증됨** (2026-10-19 23:05). Sonnet 5.5 high, dispatch `ctx_00b16275a6a6`, release됨. 8건 모두 수정, 회귀 시험 14개 추가. 커밋 `d4c234e`(문서 `8f3c96a`). 후보 diff(`git diff -- src tests`) sha256 `e9b36af8…61f5a4cd` |
| Opus high 리뷰 `task_391c198646c8` | **PASS-WITH-NOTES** (같은 sha256 기준). 전문: `opus_review_fix1.md`. 남은 비차단 메모 N1~N5(기존 명령의 접두 정리, AttributeError exit 1, fraction 중복 행, usage.md exit 코드, crop 파일 누락 exit 2) |
| 상태 | **완료** (2026-10-20). `d4c234e`의 src/tests diff(c6c3081 대비) sha256이 리뷰 후보와 같음. Run의 Task 5개 모두 completed, 정리할 worker 없음. 이후 다른 세션 커밋 `a8409dc`, `724e1b6`이 `final_report.py`를 다시 바꿨으며 이 리뷰 범위 밖이다 |
| 다음 (선택) | 비차단 메모 N1~N5 처리, 실자료에서 순위 기준 창 20 µm 확인 |
| Git | branch `rewrite/line-pixel-rate`, base `1ac6441`. **커밋 안 됨.** 수정: `src/simcompare/local_delta_report.py`, `tests/test_local_delta_report.py`. 새 파일: `docs/plan/report_html_plan_v1.md`, `docs/plan/report_html/` |

사용자 결정 요약:
- 요약 + 상세 위치를 함께 둔다.
- 새 `report` 명령이 기존 출력 폴더를 읽는다.
- 대표 지름 5 µm, 악화/개선 각 상위 10쌍.
- 순위 기준 창 20 µm(임시값, 제품마다 다를 수 있음).
- 개선 = 파랑, 악화 = 빨강.
- 지표 카드에 뜻 설명을 단다.
- 전체 frame 위치도를 넣는다.
- 창이 겹치는 쌍을 표시한다.

---

## 2. 지금 바로 쓸 수 있는 것: T1으로 늘어난 `compare-windows` 출력

명령은 그대로다.

```bash
uv run simcompare compare-windows --config <config.yaml> --correspondence <pairs.json> \
    --window-sizes 10 20 30 --sort-diameter-um 5 --out <출력폴더>
```

기존 출력(`local_delta.html/json`, `local_delta_rows.csv`, `window_sensitivity.*`)은 바이트 단위로 같다. 다음 파일이 새로 생긴다.

| 파일 | 내용 |
|---|---|
| `local_delta_thumb_<sha12>.png` | 쌍마다 a/b × layout/hotspot crop 이미지. HTML에 넣던 것과 같은 그림 |
| `local_delta_overview_<a\|b>_<sha12>.png` | 제품별 전체 frame 축소 layout(긴 변 ≤ 800 px, valid 밖은 회색) |
| `local_delta_thumbs.json` | 위 파일 목록과 위치 정보. crop마다 `crop_bounds_um`, 쌍·창마다 창/core 경계(µm), 제품마다 frame origin·pitch·shape와 축소 배율(`overview_scale`) |

**알려진 한계**: 전체 frame 그림은 첫 번째 ready 쌍의 case 전극 2개만으로 그린다(`overview_source_pair_id`에 기록됨). 레이아웃 전체가 아니다. 위치는 frame 좌표로 정확히 표시되지만 배경 그림은 일부다. T2 리뷰에서 다시 보고, 필요하면 후속 작업으로 만든다.

---

## 3. 내일 이어하기

### 가장 간단한 방법

이 레포에서 Claude Code(Opus 5.5) 세션을 열고 이렇게 말한다.

> `docs/plan/report_html/README.md` 읽고 Orca Run `run_bb47043d1dd3`의 T2부터 이어서 진행해. 리뷰는 astra high.

### 직접 할 때의 순서

```bash
cd ~/git/particle-sim-compare
git status --short          # §1의 수정 파일만 있는지 확인
orca status --json          # Orca 실행 확인
orca orchestration task-list --json   # T1 completed, T2 ready 확인
```

1. **시안 경로 복구.** T2 명세는 `/tmp/mockgen/…` 경로를 가리킨다. 재부팅으로 `/tmp`가 비었다면 레포에서 다시 복사한다.
   ```bash
   mkdir -p /tmp/mockgen
   cp docs/plan/report_html/report-mockup-v1.html /tmp/mockgen/
   cp docs/plan/report_html/mockup_gen.py /tmp/mockgen/gen.py
   ```
2. **T2 시작.** Sonnet 5.5 medium, 편집자는 한 명만 둔다.
   ```bash
   orca orchestration worker-start --task task_c6442f8d5c21 --worktree current \
     --agent claude --model claude-sonnet-5-5 --effort medium --json
   ```
   receipt의 `launch.requested`와 `launch.effective`가 같은지 확인한다.
3. **완료 대기.**
   ```bash
   orca orchestration check --wait --types "worker_done,escalation,question" --timeout-ms 900000 --json
   ```
4. **coordinator 검증.**
   - `git diff`
   - `uv run pytest -q`
   - demo 흐름으로 만든 `report.html`을 브라우저로 열어 본다.
5. **정리.** `worker-release --dispatch <id>`를 실행한 뒤 `check --ack <delivery_id>`.
6. **Astra high 리뷰** (읽기 전용, 명시 terminal 경로).
   ```bash
   orca terminal create --worktree active \
     --command 'codex --model gpt-6-astra -c model_reasoning_effort="high" --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust' --json
   orca terminal wait --terminal <handle> --for tui-idle --timeout-ms 120000 --json
   # 리뷰 Task를 task-create로 만들고(T1·T2 계약, git diff, 검증 결과, 관련 파일만 전달)
   orca orchestration worker-start --task <review_task_id> --terminal <handle> --json
   ```
   - 실제 model/effort는 terminal 화면에서 확인한다.
   - Codex를 쓸 수 없으면 리뷰를 건너뛰지 말고 사용자에게 알린다.
7. **수정과 커밋.**
   - 리뷰 지적 사항은 원래 작업자나 새 Sonnet 작업자가 고친다.
   - 고친 뒤 다시 검증한다.
   - 커밋은 사용자 확인 후 한다.

---

## 4. T2 완료 후의 보고서 사용법 (예정 인터페이스)

같은 config로 네 명령을 돌린 다음 `report`를 실행한다.

```bash
uv run simcompare compare            --config real.yaml --out out/compare
uv run simcompare electrode-map      --config real.yaml --out out/map
uv run simcompare export-review-rois --config real.yaml --out out/rois --min-count 2
uv run simcompare compare-windows    --config real.yaml --correspondence pairs.json \
    --window-sizes 10 20 30 --sort-diameter-um 5 --out out/windows
uv run simcompare report --compare-dir out/compare --map-dir out/map \
    --windows-dir out/windows --rois-dir out/rois --out out/report
```

Windows 폐쇄망에서는 `uv run simcompare` 대신 `.venv-cmd\Scripts\python.exe -m simcompare`를 쓴다.

| 인수 | 기본값 | 뜻 |
|---|---|---|
| `--compare-dir` | 필수 | `compare` 출력 폴더 |
| `--map-dir`, `--windows-dir`, `--rois-dir` | 없음 | 없으면 해당 섹션에 "입력 없음"으로 표시 |
| `--diameter-um` | 5 | 대표 지름 |
| `--rank-window-um` | 20 | 순위 기준 창(임시값). `compare-windows`의 `--window-sizes`에 들어 있어야 한다 |
| `--top-n` | 10 | 악화/개선 각각 몇 쌍을 요약에 올릴지 |

결과:
- `out/report/report.html`: 한 파일. 이미지가 내장돼 있어 `file://`로 열린다.
- `report.json`: 표시한 값과 입력 SHA256.
- `execution.json`

종료 코드:

| 코드 | 뜻 |
|---|---|
| 0 | 완료 |
| 3 | 일부 섹션 partial |
| 2 | 입력 오류. 제품 불일치, 합성/실자료 혼합, 기준 창 없음, schema 불일치 |

**주의**: 데모 데이터는 frame이 작아서 20 µm 창이 frame 밖으로 잘린다. 데모로 시험할 때는 `--window-sizes 1`과 `--rank-window-um 1`을 쓴다.
