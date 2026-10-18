# simcompare — 이물 시뮬레이션 결과의 수치 비교

이미 계산된 binary short masks로 line / pixel 면적, pair exclusive / shared, transfer와 불량 유형 조각을 계산한다. 전극 수 지도는 한 제품에서 별도로 만들며 제품 요약은 layer별 count의 pixelwise maximum이다. 좌표가 다른 제품은 겹쳐 그리지 않는다.

현재 검증 범위는 합성 numeric_v2 데이터다. G1의 실제 밀도 단위·조사면적 변환과 G2의 원천 어댑터·한 unit 환산은 pending이다. 전체 2×2 domain을 임의로 4로 나누지 않는다. 가중값과 panel delta는 명시 합성 계약에서만 계산하고 실제 미확정 값은 null과 사유로 표시한다.

## Windows 폐쇄망 설치와 실행

**CMD + 설치된 Python 3.12 x64 + 동봉된 wheel**을 사용한다. Git에 포함된 [설치 ZIP](bundles/windows_cp312_win_amd64.zip)을 사용자 폴더에 풀고 [실행 가이드](docs/usage.md)의 CMD 명령을 한 줄씩 실행한다. 설치·pip check·도움말까지 사용자 성공 확인이 있으며, 실자료 검증은 별도다.

설치 후 압축 해제 폴더의 CMD에서:

```bat
.venv-cmd\Scripts\python.exe -m simcompare --help
.venv-cmd\Scripts\python.exe -m simcompare demo-data --out demo_data
.venv-cmd\Scripts\python.exe -m simcompare compare --config demo_data/config.yaml
```

`validate-data`는 gate pending을 포함하면 exit 3이다. 완료는 0, 부분/blocked는 3, 입력 불가는 2다. 기본 지도는 count 배열과 PNG이며 자동 영역/순위를 만들지 않는다. 영역은 `electrode-map --min-count 3`처럼 사용자가 threshold를 지정할 때만 계산한다. 기본 demo ROI는 빈 목록이며 사용자 위치를 명시한 뒤 내보낸다.

## 출력과 문서

`demo_data/numeric_outputs/`에서 `comparison.json`, CSV들과 `comparison.html`, `electrode_map.json`과 PNG/sidecar를 확인한다. line / pixel, pair shared, transfer와 failure pieces는 서로 다른 지표이며 합쳐 단일 숫자로 만들지 않는다. 누락은 관측 0과 다르다.

- [실행 가이드](docs/usage.md)
- [수치 명세](SPEC.md)
- [데이터 계약](docs/data_contract.md)
- [numeric_v2 합성 예제](docs/domain-input-example/numeric_v2/README.md)
- [review ROI 계약](docs/review_roi_contract.md)
- [답변과 pending 항목](docs/open_questions.md)
- [대응 위치와 국부 ΔCA, frame 면적 비율](docs/plan/PLAN_v3_local_delta_ca.md) — `compare-windows`, `python -m simcompare.correspondence`, 사용법은 [실행 가이드](docs/usage.md)

## 유지된 legacy export 호환 경로

`merge-exports`는 particle-sim의 기존 1.x export를 병합하며 source ID, raw tuple, source-step assignment와 배열을 보존한다. numeric_v2 변환이나 비교를 수행하지 않는다.

```bat
.venv-cmd\Scripts\python.exe -m simcompare merge-exports --out merged export_reference export_target
```

`schema.py`, `adapters/example.py`, `preprocess.py`는 legacy export를 검산하는 loader로만 유지한다. `adapters/particle_sim_run.py`와 `merge.py`도 이 형식에 속한다. 숫자 비교 CLI는 이 loader를 사용하지 않는다. 실제 numeric adapter는 G2 확인 전 pending이다.

## 폐기된 경로와 기록

`train`, `embed`, `analyze`, `evaluate`, `all`은 폐기되어 명시 오류를 낸다. `analyze`는 `compare` 또는 `electrode-map`으로 이동한다. 학습·검색·평가는 별도 DL1/DL2 저장소의 역할이다. legacy demo generator와 CAA placeholder도 제거했다.

남아 있는 docs/arch 및 walkthrough PDF/HTML/생성기는 **legacy / archived 기록**이다. 현재 CLI나 결과의 사용 설명·검증 근거로 쓰지 않는다. archive branch는 보존되어 있으며 현재 경로에서 연결하지 않는다.
