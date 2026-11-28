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

real.yaml 의 경로는 BASE_DIR 안의 상대 경로만 쓴다(절대 경로·`..`·백슬래시는 거부된다). 절대 경로(`C:/...`, 슬래시)는 compare2.yaml 의 `hotspot_root`·`dl1_root` 같은 root 경로에만 쓴다.

## 실행

1. `simcompare_job_local.py` 의 EDIT 블록에서 필요하면 `BASE_DIR` 만 고친다. 나머지 기본값은 그대로 쓴다.
2. `run_local.cmd` 를 더블클릭한다. 또는 `python simcompare_job_local.py`.
   - Python 은 `SIMCOMPARE_PYTHON` → PATH 의 모든 `python.exe`(Microsoft Store 별칭 `\WindowsApps\` 는 건너뜀) → Anaconda·Miniconda 기본 폴더 순서로 시험하고, `import numpy, PIL` 이 처음 성공하는 것을 쓴다.
3. 처음에는 `STEPS = ["01", "02", "08"]`. 보고서만 다시 만들 때는 `STEPS = ["08"]`.

## 제품 모드 (선택)

제품을 `products.yaml` 에 한 번 등록하고 자산을 제품마다 만들어 두는 방식이다. 낡았을 때만 다시 만든다.

1. 기존 `real.yaml`·`compare2.yaml` 이 있으면 실행기 EDIT 의 `PRODUCTS_IMPORT = [("real.yaml", "compare2.yaml", "<그룹>")]` 로 `products.yaml` 을 만든다(권장). 실행기가 계획 전에 `products-import` 를 돌린다. 이미 등록된 제품은 값이 달라도 건너뛰고(경고) 새 제품만 넣는다. 덮어쓰려면 `PRODUCTS_IMPORT_UPDATE = True`. 패키지가 설치된 PC 에서는 `python -m simcompare products-import ...` 명령으로도 된다. `DRY_RUN = True` 이면 `--check` 로만 돌아 아무것도 쓰지 않는다.
2. 실행기 EDIT 블록에서 `MODE = "products"`, `ASSET_STEPS = ["01", "02"]`, 비교할 쌍은 `COMPARE = [("A", "B")]` 로 한다. 먼저 `DRY_RUN = True` 로 계획을 본다.
3. 실행 뒤 `data/products/products_sheet.html` 에서 제품별 자산 상태를 본다.

제품 모드에서는 `STEPS`, `REAL_CONFIG`, `COMPARE2_CONFIG` 를 읽지 않는다. 설정·이유 코드·정리 방법은 저장소의 docs/product_assets.md 를 따른다.

## 결과·로그

- 보고서: `data/ledger_report/ledger_report.html`
- 로그: `runs/simcompare_job_local.log` (BASE_DIR 기준)

## 주의

- 경로 길이가 260 자를 넘으면 Windows 에서 오류가 날 수 있다. 짧은 BASE_DIR(예: `C:\sc`)을 권장한다.
- 작업 폴더·Python 경로에 `%` `^` 를 쓰지 않는다. `run_local.cmd` 가 경로 값을 바꿔 읽는다.
- 메모리 32 GB 기준으로 기본값을 그대로 쓴다.
- 첫 실행 때 패키지가 실행기 옆 `.simcompare_pkg_*` 폴더로 풀린다. 지워도 된다(다음 실행 때 다시 풀린다).
