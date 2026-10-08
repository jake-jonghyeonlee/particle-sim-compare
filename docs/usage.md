# numeric_v2 실행 가이드

## 설치와 예제

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
| 2 | 입력/설정 불가 또는 전체 결과 unavailable | 오류 메시지, error.json 또는 unavailable 지도 |

입력 오류 시 완성 비교 리포트를 새로 쓰지 않고 error.json/execution.json에 실패를 기록한다. 이전 실행 파일이 있는 폴더에서는 execution.json의 마지막 상태를 먼저 확인한다. 기본 지도·ROI 출처 선택과 가중값 미확정 여부는 서로 독립이다.

## 결과 읽기

`comparison.json` 및 CSV/HTML은 line / pixel, pair exclusive/shared, transfer, 원 case CA, 지름/coverage, provenance를 기록한다. 세 failure pieces는 line / pixel과 다른 축이다. shared bucket이 pair별 CSV에 반복되더라도 총계에는 한 번만 포함된다. partial/unavailable의 value는 null이며 부분 합은 partial_value다. 관측 0은 available 0이고 누락을 대신하지 않는다.

`electrode_map.json`은 한 제품의 layer별 count와 product maximum 배열이다. PNG는 정수 count마다 다른 색과 legend를 쓰고 sidecar는 원 frame, um extent, integer display scale을 기록한다. 2×2 domain을 그대로 표시한다. 기본 regions=[]이며 명시 --min-count 또는 map.min_count에서만 영역을 생성한다. 다른 제품의 지도는 별도 실행한다.

`review_rois.json`의 기본 출처는 review_rois.manual이다. product/design/source/step/exposure/case/frame/version과 원 shape 문맥을 모두 지정해야 ready다. 문맥이 부족하면 blocked 이유를 보존한다. 지도 영역을 쓰려면 threshold와 review_rois.map_region_ids를 명시한다. crop 크기는 정하지 않으며 DL2가 선택한 위치와 유사 구조를 찾는 별도 역할을 담당한다. [ROI 교환 계약](review_roi_contract.md)을 따른다.

## 실제 자료

`configs/real.example.yaml`을 복사하되 현재 G2 실제 원천/migration reader는 pending이다. legacy export를 numeric manifest로 이름만 바꿔 실행하지 않는다. G1 실제 밀도 단위·조사면적 변환, G2 한-unit 환산과 G4 crop 정책은 미확정이다. full-mask 면적을 기록하는 것과 실제 weighted/panel 결과를 승인하는 것은 다르다. 자동 /4, 누락 0 채움은 없다.

`bash scripts/3_check_my_data.sh configs/real.yaml`과 `bash scripts/4_analyze_my_data.sh configs/real.yaml`은 numeric 검사·비교·지도만 호출한다. 실제 adapter가 미확정이면 명시 오류가 정상이며 임의 synthetic 표시로 우회하지 않는다. [데이터 계약](data_contract.md), [질문 상태](open_questions.md)를 확인한다.

## legacy export 보존

```bash
uv run simcompare merge-exports --out merged export_a export_b
bash scripts/5_check_particle_sim_run.sh /path/to/run --strict
```

merge-exports는 기존 1.x CSV/JSON/배열의 source IDs와 raw ordered tuple을 보존한다. particle-sim 검사 wrapper의 parquet/zarr reader는 `uv --with pyarrow --with zarr`로 명시 설치한다. 이 검사는 numeric 변환이나 실제 수치 비교 승인을 뜻하지 않는다. `schema.py`, `adapters/example.py`, `preprocess.py`는 legacy export 검산용으로 유지한다.

## 폐기와 시험

train/embed/analyze/evaluate/all은 명시 오류를 낸다. analyze의 대체는 compare/electrode-map이며 학습·검색·평가는 별도 DL 저장소 범위다. legacy demo와 CAA placeholder는 없다. 남은 docs/arch와 walkthrough는 legacy / archived 기록이며 현재 설명으로 연결하지 않는다.

```bash
uv run pytest -p no:warnings
uv run pytest -p no:warnings tests/test_retired_paths.py tests/test_numeric_cli.py tests/test_numeric_inputs.py
```
