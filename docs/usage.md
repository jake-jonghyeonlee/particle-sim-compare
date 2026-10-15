# numeric_v2 / real_hotspot_v1 실행 가이드

## Windows 폐쇄망 설치 — 먼저 실행

폐쇄망에서는 `scripts/1_install.sh`, `uv sync`, `uv run`을 사용하지 않는다. 이 경로는 인터넷에서 패키지를 다운로드할 수 있다. `Failed to download pandas...`가 발생했다면 아래 wheel 묶음 설치를 사용한다.

Git에 포함된 [`bundles/windows_cp312_win_amd64.zip`](../bundles/windows_cp312_win_amd64.zip)을 사용한다. Git으로 저장소를 옮기면 ZIP도 함께 전달된다. ZIP은 압축 해제해야 사용할 수 있으며, PC에는 **CPython 3.12 x64와 Python Launcher (`py.exe`)가 이미 설치**되어 있어야 한다. Python 자체 설치 파일은 이 묶음에 포함되지 않는다.

저장소 루트에서 **64비트 PowerShell**로 실행한다(Windows PowerShell 5.1 지원, x86 버전 제외). `outputs/offline`은 아직 없는 경로를 사용한다.

```powershell
Expand-Archive -LiteralPath '.\bundles\windows_cp312_win_amd64.zip' -DestinationPath '.\outputs\offline'
Set-Location '.\outputs\offline\windows_cp312_win_amd64'
Set-ExecutionPolicy -Scope Process Bypass
.\install_and_smoke.ps1 -VenvPath .\.venv-offline
```

이 스크립트는 가상환경 생성, 동봉된 wheel의 해시 확인, `pip --no-index` 설치, 설치된 프로그램의 smoke 검증을 수행한다. 기존 저장소 `.venv`와 별도 경로를 사용한다. 설치 후에는 가상환경 활성화나 uv 없이 같은 폴더에서 직접 실행한다.

```powershell
.\.venv-offline\Scripts\python.exe -m simcompare --help
# 실제 입력·설정 파일의 절대 경로로 변경한다. import 출력은 새 경로여야 한다.
.\.venv-offline\Scripts\python.exe -m simcompare import-hotspots --root 'D:\data\input\hotspot' --out 'D:\data\imported\reference'
.\.venv-offline\Scripts\python.exe -m simcompare validate-data --config 'D:\data\real.yaml'
.\.venv-offline\Scripts\python.exe -m simcompare compare --config 'D:\data\real.yaml'
```

설정은 [`configs/real.example.yaml`](../configs/real.example.yaml)을 복사해 실제 입력 경로와 확인된 메타데이터를 지정한다. 아래 예제의 `uv run simcompare`도 폐쇄망에서는 이 가상환경의 `python.exe -m simcompare`로 바꿔 실행한다. 실제 Windows 실행·실자료 검증은 아직 별도 확인이 필요하다.

## 인터넷 연결 환경의 설치와 예제

저장소 루트에서 `uv sync`로 설치한다. torch나 GPU가 필요하지 않다. pandas는 particle-sim legacy export 검사용 직접 의존성이다. `bash scripts/1_install.sh`와 `bash scripts/2_demo.sh`로 설치·합성 실행을 할 수 있다.

```bash
uv run simcompare demo-data --out demo_data
uv run simcompare validate-data --config demo_data/config.yaml
uv run simcompare compare --config demo_data/config.yaml
uv run simcompare electrode-map --config demo_data/config.yaml
uv run simcompare electrode-map --config demo_data/config.yaml --min-count 3
uv run simcompare export-review-rois --config demo_data/config.yaml --out demo_data/review_rois
```

`demo-data`는 numeric_v2만 만든다. 숨겨진 호환 옵션 `--numeric-only`는 같은 동작이며 legacy demo는 생성하지 않는다. 생성된 `config.yaml`은 명시 합성 density/one-unit 계약이고 `pending.yaml`은 미확정 gate의 null 출력을 검산한다. `configs/demo.yaml`은 demo_data를 가리키는 gate-pending 수치 예제다.

## 종료 코드

| 코드 | 의미 | 확인 |
|---|---|---|
| 0 | 요청 범위의 결과 available 또는 생성 완료 | execution.json과 결과 provenance |
| 3 | partial/unavailable/blocked 항목을 포함한 사용 가능한 부분 출력 | status, reasons, coverage, partial_value |
| 2 | 입력/설정 불가 또는 지도 incomplete/unavailable | 오류 메시지, error.json; 지도 리포트 없음 |

입력 오류 시 출력 디렉터리의 관리 리포트(comparison/electrode_map/validation/review_rois JSON·CSV·HTML·PNG·NPZ)를 제거하고 error.json/execution.json만 남긴다. 성공 또는 허용된 부분 비교 실행 후에는 stale error.json을 제거한다. electrode-map은 incomplete/unavailable이면 exit 2이고 리포트 세트를 쓰지 않는다. 기본 지도·ROI 출처 선택과 가중값 미확정 여부는 서로 독립이다.

## 결과 읽기

`comparison.json` 및 CSV/HTML은 line / pixel, pair exclusive/shared, transfer, 원 case CA, 지름/coverage, provenance를 기록한다. 세 failure pieces는 line / pixel과 다른 축이다. shared bucket이 pair별 CSV에 반복되더라도 총계에는 한 번만 포함된다. partial/unavailable의 value는 null이며 부분 합은 partial_value다. 관측 0은 available 0이고 누락을 대신하지 않는다.

`electrode_map.json`은 한 제품의 layer별 count와 product maximum의 작은 summary다. counts와 valid는 압축 NPZ 상대 경로로 연결하고 shape/pitch/origin, count histogram, status/provenance와 파일 hash를 기록한다. 기본 셀별 CSV·JSON 배열·HTML 배열은 없다. `--per-cell-text`는 4096 cells 이하에서만 명시 셀별 텍스트를 추가한다. PNG는 정수 count마다 다른 색과 legend를 쓰고 sidecar는 원 frame, um extent, integer display scale을 기록한다. 2×2 domain을 그대로 표시한다. 기본 regions=[]이며 명시 --min-count 또는 map.min_count에서만 영역을 생성한다. 다른 제품의 지도는 별도 실행한다.

`review_rois.json`의 기본 출처는 review_rois.manual이다. product/design/source/step/exposure/case/frame/version과 원 shape 문맥을 모두 지정해야 ready다. 문맥이 부족하면 blocked 이유를 보존한다. 지도 영역을 쓰려면 threshold와 review_rois.map_region_ids를 명시한다. crop 크기는 정하지 않으며 DL2가 선택한 위치와 유사 구조를 찾는 별도 역할을 담당한다. [ROI 교환 계약](review_roi_contract.md)을 따른다.

## 실제 자료

확인된 실제 폴더 구조는 먼저 별도 `real_hotspot_v1` 관측 manifest로 변환한다. 출력 디렉터리는 새 경로여야 한다.

```bash
uv run simcompare import-hotspots --root input/hotspot --out imported/reference --expected-cases expected_cases.txt
```

`--expected-cases`는 case 이름을 줄마다 적은 선택 파일이며, 없으면 발견되지 않은 case folder coverage는 unconfirmed로 남는다. 입력 hotspot은 opaque grayscale PNG여야 한다. 검정=0, 모든 회색 nonzero=occupied다. 변환기는 1/2 electrode image shape와 hotspot shape의 정확한 1000 px 축 차이를 검사하고 중앙 frame `(50,50)` µm로 기록한다. case-local `result/<불량명>.csv`의 원 숫자/정밀도와 PNG 면적 차이를 보존한다. 누락 image는 missing, 검정 image는 observed area 0이다.

`validate-data`는 관측 면적과 missing/CSV 진단을 출력하고 `compare`는 raw 면적과 상태를 출력한다. config에 확인된 `condition_assignment`와 선택 pair table을 제공하면 기존 line/pixel 면적 집계와 electrode map을 연결한다. assignment에는 `product_id`, `design_version`, `source_id`, `process_step_id`, `exposure_id`, `unit_id`, `layer_stack`을 넣고 pair table은 기존 `pairs.csv` 열을 사용한다. 값이 없으면 null로 남기고 grouping을 요구하는 결과는 unavailable이다. pair table이 없는 pair를 pixel로 자동 배정하지 않는다. density 단위/조사면적 및 한 unit 환산이 없으면 weighted rate, panel, delta는 unavailable이며 electrode image polarity가 확인되지 않은 경우 review ROI만 blocked다. Electrode map은 hotspot mask와 explicit case assignment를 사용하며 polarity를 요구하지 않는다. configured mask materialization은 group 500,000,000 cells, 전체 source 6,000,000,000 cells, 보수적 whole-operation 추정 8 GiB로 제한한다. 마지막 제한은 모든 source mask, diameter별 retained output, per-case transient margin을 추정하며 streaming 보장은 아니다. `max_operation_bytes`는 낮출 수 있고 8 GiB보다 높일 수 없다. 초과 입력은 명시 오류로 중단된다. Windows 실행과 실자료 검증은 별도 수행해야 한다.

`bash scripts/3_check_my_data.sh configs/real.yaml`과 `bash scripts/4_analyze_my_data.sh configs/real.yaml`은 numeric 검사·비교·지도만 호출한다. 실제 adapter가 미확정이면 명시 오류가 정상이며 임의 synthetic 표시로 우회하지 않는다. [데이터 계약](data_contract.md), [질문 상태](open_questions.md)를 확인한다.

## legacy export 보존

```bash
uv run simcompare merge-exports --out merged export_a export_b
bash scripts/5_check_particle_sim_run.sh /path/to/run --strict
```

merge-exports는 기존 1.x CSV/JSON/배열의 source IDs와 raw ordered tuple을 보존한다. particle-sim 검사 wrapper의 parquet/zarr reader를 위해 pyarrow와 zarr를 직접 의존성으로 선언한다. 기존 wrapper의 --with 옵션도 호환 유지한다. 이 검사는 numeric 변환이나 실제 수치 비교 승인을 뜻하지 않는다. `schema.py`, `adapters/example.py`, `preprocess.py`는 legacy export 검산용으로 유지한다.

## 폐기와 시험

train/embed/analyze/evaluate/all은 명시 오류를 낸다. analyze의 대체는 compare/electrode-map이며 학습·검색·평가는 별도 DL 저장소 범위다. legacy demo와 CAA placeholder는 없다. 남은 docs/arch와 walkthrough는 legacy / archived 기록이며 현재 설명으로 연결하지 않는다.

```bash
uv run pytest -p no:warnings
uv run pytest -p no:warnings tests/test_retired_paths.py tests/test_numeric_cli.py tests/test_numeric_inputs.py
```

## Windows 폐쇄망 CPython 3.12 x64 묶음

연결된 빌드 컴퓨터에서 `uv 0.11.23`, Python/pip, 네트워크를 준비한 뒤 저장소 루트에서 `python scripts/build_windows_bundle.py`를 실행한다. 이 명령은 pyproject의 직접 의존성 여섯 개를 포함해 CPython 3.12 `win_amd64` 대상 전이 의존성을 binary wheel로 해석·다운로드하고 자체 `simcompare` wheel, requirements 해시, manifest, fixture, 설치 검증 스크립트를 `outputs/windows_cp312_win_amd64.zip`에 넣는다. `scripts/windows-build-constraints.txt`는 Hatchling 1.27.0을 고정한다. 빌더는 연결 환경용이며 생성 ZIP만 폐쇄망으로 반입한다.

Windows x64에서 ZIP을 한글 또는 공백이 포함된 경로에 풀고 PowerShell에서 실행한다. 스크립트는 CPython 3.12 x64를 확인하고 wheel SHA256 및 wheel 목록을 검사한 뒤 venv를 만들고 `pip --no-index --only-binary=:all: --require-hashes`로 설치한다. fixture의 validate/compare/map 출력은 `is_synthetic=false`를 보존하며 분류·물리 메타데이터가 미제공된 결과의 exit 3만 허용한다. 다른 오류 코드는 그대로 전달된다.

```powershell
Expand-Archive -LiteralPath '.\bundles\windows_cp312_win_amd64.zip' -DestinationPath '.\폐쇄망 테스트'
Set-Location '.\폐쇄망 테스트\windows_cp312_win_amd64'
Set-ExecutionPolicy -Scope Process Bypass
.\install_and_smoke.ps1 -VenvPath .\.venv-offline
```

위 명령은 저장소 루트에서 시작한다. 연결 환경에서 묶음을 재생성한 경우 검증을 마친 `outputs/windows_cp312_win_amd64.zip`을 `bundles/windows_cp312_win_amd64.zip`에 복사해 커밋해야 Git으로 새 묶음이 전달된다. 애플리케이션 wheel의 검토 대상과 해시는 [R3 검토 기록](review/REAL_HOTSPOT_REVIEW_R3.md)에 있다. 이후 설치 스크립트의 PowerShell 5.1 호환성 수정으로 ZIP 자체의 해시는 달라졌다. 실제 Windows 실행 검증은 별도로 필요하다.

이전 압축 해제본에서 `OSArchitecture` 속성 오류가 발생하면 저장소를 업데이트한 후, 저장소 루트에서 아래 명령으로 설치 스크립트만 교체할 수 있다. 기존 wheel을 다시 다운로드할 필요는 없다.

```powershell
Copy-Item -LiteralPath '.\scripts\install_and_smoke.ps1' -Destination '.\outputs\offline\windows_cp312_win_amd64\install_and_smoke.ps1' -Force
Set-Location '.\outputs\offline\windows_cp312_win_amd64'
.\install_and_smoke.ps1 -VenvPath .\.venv-offline -KeepVenv
```

이 구현 환경의 wheel 다운로드·hash·fixture 검증은 Windows 실행 검증을 대신하지 않는다. 실제 Windows 인수 시 설치, `pip check`, import, import-hotspots, validate-data, compare, electrode-map이 성공하고 마지막 네 CLI 단계만 문서화된 incomplete 결과로 exit 3이어야 한다. 수백 파일 resource 측정은 `python scripts/benchmark_real_hotspots.py --cases 312`로 재현하며 출력에 플랫폼, case/파일 수, 1270×1270 입력, peak RSS와 시간, 500,000,000-cell 그룹 guard 결과가 포함된다.
