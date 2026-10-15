# numeric_v2 / real_hotspot_v1 실행 가이드

## Windows 폐쇄망 설치 — CMD만 사용

사용자가 폐쇄망에서 성공을 확인한 방식은 **명령 프롬프트(CMD) + 설치된 Python + 로컬 wheel**이다. 온라인 설치와 PowerShell 설치 스크립트는 제거했다. 가상환경 활성화, 관리자 실행, 실행 정책 변경이 필요하지 않다.

1. Git에 포함된 [`bundles/windows_cp312_win_amd64.zip`](../bundles/windows_cp312_win_amd64.zip)을 폐쇄망으로 옮긴다.
2. 파일 탐색기 주소창에 `%USERPROFILE%`을 입력하고 `simcompare-offline` 폴더를 만든다. ZIP을 풀어 아래 구조로 준비한다. 기존 가상환경을 삭제하거나 덮어쓰지 말고 새 설치는 새 폴더를 사용한다.

```text
%USERPROFILE%\simcompare-offline\windows_cp312_win_amd64\
    install_offline.cmd
    verify_windows_bundle.py
    requirements-windows.txt
    manifest.json
    wheelhouse\
    fixtures\
```

3. 시작 메뉴에서 **명령 프롬프트**를 열고 아래 명령을 한 줄씩 실행한다. 실제 해제 경로가 다르면 첫 줄을 바꾼다. PC에는 **CPython 3.12 x64와 Python Launcher (`py.exe`)**가 설치되어 있어야 하며 Python 설치 파일은 ZIP에 포함되지 않는다.

```bat
cd /d "%USERPROFILE%\simcompare-offline\windows_cp312_win_amd64"
py -3.12 -m venv .venv-cmd
.venv-cmd\Scripts\python.exe verify_windows_bundle.py .
.venv-cmd\Scripts\python.exe -m pip --disable-pip-version-check install --no-index --only-binary=:all: --require-hashes --find-links=wheelhouse -r requirements-windows.txt
.venv-cmd\Scripts\python.exe -m pip check
.venv-cmd\Scripts\python.exe -m simcompare --help
```

**중간에 오류가 나면 다음 명령을 실행하지 않는다.** `wheel integrity OK`, `No broken requirements found`, simcompare 도움말이 표시되면 설치·CLI 확인 완료다. 이 과정만으로 실제 데이터나 수치 검증이 완료된 것은 아니다. 위 수동 절차는 사용자 실제 성공 확인이 있다.

같은 절차를 묶은 `install_offline.cmd`도 동봉했다. 같은 CMD 폴더에서 `install_offline.cmd`로 실행한다. 기존 `.venv-cmd`가 있으면 삭제하지 않고 Python 버전을 확인해 사용하며, 어느 단계든 실패하면 중단한다. 이 자동화 파일 자체의 Windows 실행은 아직 별도 확인이 필요하다. 이미 수동 설치에 성공했다면 재설치 없이 다음 단계로 간다.

## 실제 데이터 첫 실행

이후 명령도 위 압축 해제 폴더에서 실행한다. 경로는 실제 데이터 위치로 바꾸고, import 출력은 아직 없는 새 폴더를 지정한다. 원본은 수정하지 않는다.

```bat
.venv-cmd\Scripts\python.exe -m simcompare import-hotspots --root "D:\data\input\hotspot" --out "D:\data\imported\reference"
```

저장소의 [`configs/real.example.yaml`](../configs/real.example.yaml)을 `D:\data\real.yaml`로 복사한다. reference/target/map의 data_dir를 실제 변환 폴더로 설정하고, 확인된 layer·조건·pixel/line 분류표만 입력한다. 원본 YAML의 상대 경로는 복사한 설정 파일 기준이므로 실제 경로로 수정해야 한다.

```bat
.venv-cmd\Scripts\python.exe -m simcompare validate-data --config "D:\data\real.yaml"
.venv-cmd\Scripts\python.exe -m simcompare compare --config "D:\data\real.yaml"
.venv-cmd\Scripts\python.exe -m simcompare electrode-map --config "D:\data\real.yaml"
```

## 합성 예제 실행 (선택)

```bat
.venv-cmd\Scripts\python.exe -m simcompare demo-data --out demo_data
.venv-cmd\Scripts\python.exe -m simcompare validate-data --config demo_data/config.yaml
.venv-cmd\Scripts\python.exe -m simcompare compare --config demo_data/config.yaml
.venv-cmd\Scripts\python.exe -m simcompare electrode-map --config demo_data/config.yaml
```

`demo-data`는 numeric_v2 합성 데이터만 만든다. 실제 데이터 검증을 대신하지 않는다.

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

```bat
.venv-cmd\Scripts\python.exe -m simcompare import-hotspots --root input/hotspot --out imported/reference --expected-cases expected_cases.txt
```

`--expected-cases`는 case 이름을 줄마다 적은 선택 파일이며, 없으면 발견되지 않은 case folder coverage는 unconfirmed로 남는다. 입력 hotspot은 opaque grayscale PNG여야 한다. 검정=0, 모든 회색 nonzero=occupied다. 변환기는 1/2 electrode image shape와 hotspot shape의 정확한 1000 px 축 차이를 검사하고 중앙 frame `(50,50)` µm로 기록한다. case-local `result/<불량명>.csv`의 원 숫자/정밀도와 PNG 면적 차이를 보존한다. 누락 image는 missing, 검정 image는 observed area 0이다.

`validate-data`는 관측 면적과 missing/CSV 진단을 출력하고 `compare`는 raw 면적과 상태를 출력한다. config에 확인된 `condition_assignment`와 선택 pair table을 제공하면 기존 line/pixel 면적 집계와 electrode map을 연결한다. assignment에는 `product_id`, `design_version`, `source_id`, `process_step_id`, `exposure_id`, `unit_id`, `layer_stack`을 넣고 pair table은 기존 `pairs.csv` 열을 사용한다. 값이 없으면 null로 남기고 grouping을 요구하는 결과는 unavailable이다. pair table이 없는 pair를 pixel로 자동 배정하지 않는다. density 단위/조사면적 및 한 unit 환산이 없으면 weighted rate, panel, delta는 unavailable이며 electrode image polarity가 확인되지 않은 경우 review ROI만 blocked다. Electrode map은 hotspot mask와 explicit case assignment를 사용하며 polarity를 요구하지 않는다. configured mask materialization은 group 500,000,000 cells, 전체 source 6,000,000,000 cells, 보수적 whole-operation 추정 8 GiB로 제한한다. 마지막 제한은 모든 source mask, diameter별 retained output, per-case transient margin을 추정하며 streaming 보장은 아니다. `max_operation_bytes`는 낮출 수 있고 8 GiB보다 높일 수 없다. 초과 입력은 명시 오류로 중단된다. Windows 설치·pip check·도움말 실행은 사용자 성공 확인이 있으며, 실자료 수치·메모리 검증은 별도 수행해야 한다.

실제 adapter가 미확정이면 명시 오류가 정상이며 임의 synthetic 표시로 우회하지 않는다. [데이터 계약](data_contract.md), [질문 상태](open_questions.md)를 확인한다.

## legacy export 보존

```bat
.venv-cmd\Scripts\python.exe -m simcompare merge-exports --out merged export_a export_b
```

merge-exports는 기존 1.x CSV/JSON/배열의 source IDs와 raw ordered tuple을 보존한다. particle-sim 검사 wrapper의 parquet/zarr reader를 위해 pyarrow와 zarr를 직접 의존성으로 선언한다. 기존 wrapper의 --with 옵션도 호환 유지한다. 이 검사는 numeric 변환이나 실제 수치 비교 승인을 뜻하지 않는다. `schema.py`, `adapters/example.py`, `preprocess.py`는 legacy export 검산용으로 유지한다.

## 검증 범위와 개발 기록

설치용 ZIP에는 실행 의존성이 포함되며 pytest 같은 개발 도구는 포함되지 않는다. Windows 16GB에서 수백 실제 이미지 처리, 실자료 비교·지도·ROI 검증은 남아 있다. train/embed/analyze/evaluate/all은 폐기되었고 compare/electrode-map을 사용한다.

배포 파일을 만드는 유지보수 절차는 [Windows 묶음 빌드](windows_bundle_build.md)에 별도로 기록한다. 과거 계획·리뷰의 PowerShell 설명은 당시 검증 이력이며 현재 설치 지침이 아니다. R3 검토의 애플리케이션 wheel은 유지되지만 설치 파일 교체로 ZIP 해시는 달라졌다.
