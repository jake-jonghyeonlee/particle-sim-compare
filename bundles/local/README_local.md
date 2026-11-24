# 로컬 PC 실행기 (Windows·macOS)

ETX 처럼 패키지를 등록하지 않고, 실행기 파일 하나로 01(import) → 02(`compare2`) → 08(ledger·보고서)을 돌린다.

## 준비물

- Python 3.9 이상 + numpy + Pillow (Anaconda base 에 보통 있다). 그 밖의 설치·PATH·레지스트리 변경·관리자 권한은 필요 없다.
- `simcompare_job_local.py`, `run_local.cmd` (이 폴더의 두 파일).

## 폴더 구조 (BASE_DIR)

`BASE_DIR` 은 작업 폴더다. 기본은 `simcompare_job_local.py` 가 있는 폴더다. 예:

```
BASE_DIR/
  simcompare_job_local.py
  run_local.cmd
  real.yaml            # configs/etx/templates/real.yaml 을 복사해 고친다
  compare2.yaml        # configs/etx/templates/compare2.yaml 을 복사해 고친다
  data/input/...       # hotspot 입력, pairs.csv
  data/input/dl1/...   # DL1 폴더
```

YAML 안 경로는 BASE_DIR 기준 상대 경로를 권장한다. 절대 경로를 쓰면 `C:/...` 처럼 슬래시로 적는다.

## 실행

1. `simcompare_job_local.py` 의 EDIT 블록에서 필요하면 `BASE_DIR` 만 고친다. 나머지 기본값은 그대로 쓴다.
2. `run_local.cmd` 를 더블클릭한다. 또는 `python simcompare_job_local.py`.
   - Python 은 `SIMCOMPARE_PYTHON` → PATH 의 `python` → Anaconda·Miniconda 기본 폴더 순서로 찾는다.
3. 처음에는 `STEPS = ["01", "02", "08"]`. 보고서만 다시 만들 때는 `STEPS = ["08"]`.

## 결과·로그

- 보고서: `data/ledger_report/ledger_report.html`
- 로그: `runs/simcompare_job_local.log` (BASE_DIR 기준)

## 주의

- 경로 길이가 260 자를 넘으면 Windows 에서 오류가 날 수 있다. 짧은 BASE_DIR(예: `C:\sc`)을 권장한다.
- 메모리 32 GB 기준으로 기본값을 그대로 쓴다.
- 첫 실행 때 패키지가 실행기 옆 `.simcompare_pkg_*` 폴더로 풀린다. 지워도 된다(다음 실행 때 다시 풀린다).
