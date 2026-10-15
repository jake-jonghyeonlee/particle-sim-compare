# 실제 hotspot 입력 변환 및 Windows 폐쇄망 테스트 계획 v1

작성: 2026-10-15. 상태: **구현 전 계획 초안**. 이 문서 자체는 구현 완료나 독립 검토 PASS가 아니다.

기준 저장소: `particle-sim-compare`, branch `rewrite/line-pixel-rate`, commit `40a4d6d3982de2d0beb0939909418eb25f2c2379`. 작성 시작 시 작업 트리는 clean이었다. 기존 Run/Task의 권한이나 상태를 이 문서로 변경하지 않는다.

## 1. 목표와 현재 차이

실제 시뮬레이션 폴더를 수치 비교 입력으로 변환하고 Windows Python 3.12 64비트, RAM 16GB 환경에서 인터넷 없이 설치·검증·비교할 수 있게 한다. DL 저장소 변경, 슬라이드 재작성, 메일 발송은 이 구현 계획에 포함하지 않는다.

현재 `adapters/numeric.py`는 real 입력을 `real_source_format_unconfirmed`로 차단한다. 기존 vertical 계약은 높이 근거를 필수로 요구하고 높이 미만을 `not_applicable`로 제한한다. `numeric_images.py`의 기존 binary reader는 회색을 허용하지 않는다. 따라서 파일 변환만 추가해서 끝나는 작업은 아니다. 승인된 원천 계약에 한정한 real 경로와 관측된 zero 처리, 설치·실행 경로를 함께 연결해야 한다.

## 2. 사용자 확인 사항

| 항목 | 확정 내용 |
|---|---|
| case 이름 | `불량명-전극1-layer1-전극2-layer2`; 전극명과 layer에 하이픈 없음, underscore 가능 |
| 전극 이미지 | case의 `1.png`, `2.png`가 순서대로 두 전극/layer에 대응 |
| hotspot | 전극 이미지와 같은 폴더의 `hotspot_lateral_short03.png` 같은 파일 |
| CSV | **각 case 내부** `result/<불량명>.csv`; 조건마다 독립 결과가 있음 |
| CSV 열 | `defect_size(um), Area(um2)`; 지름별 원천 면적을 보존 |
| 크기 | 전극 2270×2270, hotspot **1270×1270**. 1170은 정정된 오기 |
| 좌표 | 중앙 일치, 상하좌우 각 500 px 제외, 양쪽 모두 0.1 µm/px |
| 픽셀 | 검정 배경, 회색 hotspot |
| 0 | hotspot 없는 결과는 검정 PNG와 면적 0. 높이 미만 vertical도 동일 |
| 데이터 범위 | 모든 전극쌍 데이터가 있음. 실제 파일 누락은 별도 검증 |
| 전극 정체성 | 다른 layer에서 CNT로 연결된 전극은 입력 전에 같은 전극명으로 정리됨 |
| 분류표 | line/pixel 표는 폐쇄망에서 확보. 기존 명시적 pair 표 방식 유지 |
| 환경 | Windows, Python 3.12 64비트, RAM 16GB, 모델당 대략 수백 파일 |
| 배포 | uv 다운로드에 의존하지 않는 wheel 묶음 + venv/pip |

최신 확인 사항은 이 원천의 회색 처리 및 높이 미만 zero에 관해 과거 white-only/N/A 가정보다 우선한다. 기존 원천 계약의 의미는 소급 변경하지 않는다.

확정된 case 내부 구조:

```text
input/hotspot/[상위 디렉터리가 있을 수 있음]/
  lateral_short-electrode_A-layer_1-electrode_B-layer_1/
    1.png
    2.png
    hotspot_lateral_short03.png
    ...
    result/
      lateral_short.csv
```

바깥 `images` 유무를 고정하지 않고 지정한 hotspot root 아래에서 case를 발견한다. `result`를 전역 공용 폴더에서 찾거나 다른 case의 CSV로 대신하지 않는다. 중첩·중복 case는 진단하고 자동 병합하지 않는다.

## 3. 구현 계약

### 이름, 정체성, 메타데이터

- case 이름은 하이픈 기준 정확히 다섯 필드로 파싱한다. underscore, 대소문자, 원래 전극명/layer를 보존한다. 불량명은 지원 목록으로 검증한다.
- hotspot 접두어의 불량명이 case와 일치하는지 검사하고 숫자 suffix를 µm 지름으로 해석한다. `03`과 `3`이 중복되면 오류다. 기존 1~30, 40, 50 µm 격자의 누락·추가를 보고한다.
- pair 분류 키는 기존 순서 없는 전극명 쌍 규칙을 유지하되 원천 endpoint 순서/layer는 보존한다. CNT 연결을 재추론하거나 이름에 layer를 붙여 새 전극으로 나누지 않는다. 같은 전극명이 양 끝에 나타나면 기존 두 전극 제약과 충돌하는 사례로 명시적으로 보고하고 임의 개명하지 않는다.
- 제품·설계·원천 조건 ID, layer 순서/공정 매핑은 기존 명시적 설정을 사용한다. 폴더 정렬순으로 적층 순서를 추정하지 않는다. 필요한 설정이 없으면 원천 검사는 진행하되 해당 집계는 차단하고 설정 템플릿을 제공한다. 별도 조건/모델을 union하지 않는다.
- 입력이 제공하지 않는 role/height는 임의로 추론하지 않는다. 새 계약에서 미제공 상태를 표현한다. source 파일 상대경로·hash·변환 정책·버전·실행 설정을 기록한다. hash를 승인 증거로 취급하지 않는다.

### 영상, 면적, 좌표

- 새 원천 adapter에서만 검정 0, 회색의 nonzero를 hotspot 1로 변환한다. 이는 구현 정책이며 intensity를 확률/면적 가중치로 사용하지 않는다. RGB는 채널 동일 여부를 검사한다. 유색·투명·손상 영상은 진단하고 임의 보정하지 않는다. 기존 strict binary reader와 PNG 구조 검증은 유지한다.
- 전극 쌍 크기 일치 및 hotspot의 각 축 크기가 전극보다 1000 px 작은지 검증한다. 2270만 하드코딩하지 않는다. 원본 유지, resize 금지.
- hotspot frame 원점은 원본 frame에서 `(500,500)` px, 즉 `(50,50)` µm 이동한다. 계산 영역은 실제 중앙 crop이다. 외곽 500 px를 관측된 검정으로 채우지 않는다. 전극/ROI도 같은 좌표 변환을 사용한다.
- PNG 면적은 marked pixel 수 × 0.01 µm². CSV의 지름별 Area를 별도 보존하고 차이를 보고한다. 사용자 표현 'CAA 값'만으로 밀도 가중 CAA라고 해석하지 않는다.
- CSV는 헤더·단위·유한한 비음수 값·중복 지름·누락을 검사한다. UTF-8 BOM을 지원하고 다른 인코딩은 명시적 옵션으로 처리한다. 면적 허용오차는 CSV 기록 정밀도와 픽셀 면적을 근거로 명시하며, 원값/차이/사용 허용오차를 함께 출력한다. 불일치를 맞추기 위한 threshold/resize 변경은 금지한다.
- 검정 PNG + CSV 0은 `observed` zero. 높이 미만이라는 이유로 N/A로 바꾸지 않는다. 파일 누락은 `missing`, 손상은 오류이며 zero로 대체하지 않는다. 새 원천 계약에서 height가 없어도 관측값을 보존하고, legacy 계약의 높이 검증/N/A 규칙은 유지한다.

### 비교와 자원

- line 우선, pair exclusive/shared, lateral/vertical/simultaneous, 전극 지도 및 추적성의 기존 계산을 연결한다. 분류표 미등록 pair를 pixel로 기본 배정하지 않는다. 가능한 원천 면적/지도와 미완료 분류 상태를 구분한다. 완전한 line/pixel 비교에는 폐쇄망 분류표 입력이 필요하다.
- density/조사 면적, 수율, unit/panel 환산을 새로 가정하지 않는다. ROI의 원본 대응이 없으면 ROI만 blocked로 표시한다.
- 1270² bool 한 장은 약 1.6 MB다. 100 case × 32 지름을 동시에 보유하면 mask만 약 5.2 GB이며 복사·중간 배열은 별도다. 실제 수백 파일의 구성과 현재 loader 보유 방식을 측정한다.
- 동일 비교 그룹/지름의 의미를 유지하는 순차 또는 제한된 batch 처리를 사용한다. pair 기여 계산에 필요한 mask를 조기에 버리지 않는다. peak RSS·파일 수·해상도·처리 시간을 기록하고 16GB 환경에서 실행 여유가 있도록 조정한다. 실제 자료 없이 처리 시간을 확약하지 않는다.

## 4. 작업 순서와 수락 조건

공통: 편집 worker는 저장소당 한 명. 모든 Task는 위 baseline과 시작 시 diff를 확인하고 기존 변경을 보존한다. 아래 경로는 예상 수정 범위이며 필요가 커지면 계약을 갱신한다. 새 CLI 이름은 제안이며 아직 실행 가능한 명령이 아니다.

| Task / 의존성 | 수정 범위 및 결과 | 수락 증거 |
|---|---|---|
| T1 원천 계약 / 없음 | `docs/data_contract.md`, `configs/real.example.yaml`, 신규 adapter 계약. case 검색, 설정/분류표 템플릿, 원천 검사 설계 | 위 사례별 입력→결과 계약 확정. 미제공 layer/height/role을 가짜 값으로 채우지 않음 |
| T2 변환기 / T1 | `src/simcompare/adapters/` 신규 원천 adapter, `cli.py`, 관련 신규 테스트. 제안 `import-hotspots`로 binary asset·manifest·진단 생성 | underscore, case-local CSV, leading-zero 지름, 중복/누락/손상, gray/black, CSV 면적 검사. 원본 hash 불변. 실패한 출력은 완성본과 분리 |
| T3 real 계산 연결 / T2 | `adapters/numeric.py`, `numeric_schema.py`, 필요한 measurement/pipeline 호출부와 회귀 테스트 | 새 버전 원천만 real로 허용. synthetic 우회 금지. vertical black=observed zero, legacy N/A 유지. crop 원점·경계·면적 round-trip. 잘못된 metadata 차단 |
| T4 출력·메모리 / T3 | `linepixel.py`, `electrode_map.py`, `pipeline.py`, `report.py`, 필요 시 `roi_export.py` | 기존 계산과 작은 fixture 수치 동등. 분류표 미등록=partial. 다중 조건 혼합 차단. 대표 크기 수백 파일로 peak RSS/소요 시간 기록. 지도·ROI 좌표 대조 |
| T5 Windows 묶음 / T4 | 신규 Windows 설치/검증 스크립트, wheel 생성 스크립트, `docs/usage.md`; dependency 변경 최소화 | cp312 대상 wheel 및 자체 패키지 wheel, 버전/hash manifest. PowerShell/cmd에서 venv→pip no-index 설치→import→fixture 비교. 한글/공백 경로 및 실패 exit code 확인 |
| T6 독립 검토·인수 / T5 | candidate diff 및 검증 증거, 인수 문서 | 새 원천 schema/계산 변경을 독립 검토. 수정 후 후보를 다시 검증. 폐쇄망 첫 실행 절차와 미확인 항목 명시 |

핵심 반례: hotspot 모서리 한 픽셀이 원본 `(500,500)`에 정확히 대응해야 한다. 검정 vertical은 missing과 달라야 한다. CSV가 다른 case에만 있으면 실패해야 한다. line/pixel 표가 없을 때 완전한 비교라고 표시하면 안 된다. 외곽 crop 제외 면적을 zero로 계산하면 안 된다.

T3/T4 완료 시 관련 numeric inputs/schema/images, measurement, linepixel, failure pieces, electrode map, CLI/report, ROI 테스트를 변경 범위에 맞춰 실행한다. 전체 suite 반복은 새 변경이나 실패가 정당화할 때만 한다. 실자료 접근 없이 합성 검증을 실자료 검증이라고 표기하지 않는다.

## 5. 폐쇄망 배포

연결된 환경에서 Windows Python 3.12 대상 wheel을 준비한다. 64비트만으로 CPU 아키텍처가 확정되지는 않으므로 우선 `win_amd64` 번들을 만들고 설치 시작 시 플랫폼을 확인한다. ARM64면 맞지 않는 wheel 설치를 중단하고 해당 target을 별도로 준비한다.

직접·전이 의존성을 모두 고정하여 다운로드하고 자체 패키지 wheel, requirements, SHA-256 목록, 설치/검증 스크립트, 작은 입력 fixture와 기대 출력을 포함한다. 현재 프로젝트의 필요한 의존성을 임의 삭제하지 않는다. source build나 온라인 build isolation이 폐쇄망에서 발생하지 않도록 wheel만 사용한다.

폐쇄망에서는 설치된 Python으로 venv 생성 후 `python -m pip install --no-index --find-links <wheelhouse> ...` 방식으로 설치한다. 실제 스크립트는 shell 인용, Python 버전/아키텍처 확인, 오류 종료를 포함한다. Mac에서 Windows wheel 확보와 파일 검증은 가능하지만 Windows 실행 검증을 대신하지 못한다. Windows 설치→fixture→실자료 변환→분류표 연결→비교를 첫 인수 절차로 제공한다.

## 6. 시간·검토·복구

초기 예상 **작업 1~2일, 약 9~16시간**: T1 1~2h, T2 2~3h, T3 2~3h, T4 1~3h, T5 2~3h, T6 1~2h. 단순 변환 추가보다 real 차단 해제·zero 계약 호환·메모리 검증이 추가된 추정이다. 폐쇄망 반입/사용자 실행 대기, 환경 제한 해결 및 실자료에서 발견되는 추가 양식은 별도다. T2 후 추정을 갱신한다. 프로그램의 모델당 실행 시간은 입력 개수·mask 중첩에 따라 측정한다.

이 문서 작성에서는 Orca Run을 새로 만들거나 coordinator 권한을 인수하지 않는다. Orca로 구현을 진행할 경우 설치된 orchestration 절차로 기존 Run/worker 상태를 먼저 확인한다. 사용자의 이번 작업 지시인 **Claude 최소 사용, 검토 Astra 대체**를 해당 작업의 모델 routing 예외로 기록한다. 실제 검토를 받기 전 Astra PASS라고 표시하지 않는다. 독립 검토는 구현 작성자와 분리하며 동시에 추가 read-only worker는 한 명만 사용한다.

변환 산출물은 원본과 분리하고 manifest 완료 전에 임시/실패 상태를 남긴다. 재실행은 원천 hash와 정책 버전으로 동일성을 검사한다. 중단 후 기존 diff·부분 산출물·Task/Dispatch를 보존하고, 상태 불명 worker를 실패로 간주해 중복 editor를 띄우지 않는다. 수락 시 candidate commit 또는 diff fingerprint, 실행 명령/결과, 미검증 Windows 항목을 checkpoint에 기록한다.

추가 질문 없이 위 범위의 계획 작성은 가능하다. 실자료 실행 때 필요한 layer/process 설정과 폐쇄망 분류표는 템플릿/검증 항목으로 관리하고, 제공 전 완전한 비교 성공을 선언하지 않는다.
