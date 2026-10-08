# R2 독립 코드 검토 — CLI·리포트·ROI 내보내기·게이트 답변 적용·조각 지표·폐기 (T6~T7)

- 검토자: 새 Claude Opus 5.5 세션, effort high, 읽기 전용. 이 코드와 계획을 작성하거나 채택하지 않았다.
- 대상: `/Users/jonghyeonlee/git/particle-sim-compare`, branch `rewrite/line-pixel-rate`
- **후보 commit: `e4086dd9f5dc5bde42033b2866454916856dbe47`** (diff `8a35c0e..e4086dd`와 `ad3b664`)
- 범위: T6(`ad3b664`), 게이트 답변 적용(`957d581`), 조각 지표와 heat map(`a3001fc`), T7 폐기(`e4086dd`). T2~T5 수치 계산은 R1의 `8a35c0e` 판정을 그대로 두고, 이후 commit이 바꾼 곳만 다시 보았다.
- 계약: `docs/plan/PLAN_v2.md` §2.2~2.4, §3, T6, T7, §8 R2와 `docs/implementation_memory.json`의 `gate_answers_2026_10_08`
- 방법: `git archive e4086dd`를 저장소 밖 임시 폴더에 풀어 그 사본으로 실행했다 (`PYTHONPATH=<사본>/src`, 저장소 `.venv/bin/python`). 작업 트리는 검토 전후 모두 clean이었다.

## 판정: **PASS-WITH-NOTES**

막는 결함은 찾지 못했다. 값이 틀리게 나오거나, 미답변 게이트가 추정값으로 채워지거나, 폐기 명령이 0으로 끝나는 경로는 없었다. 아래 지적 13건은 모두 막지 않는다. 다만 R2-1(N6)과 R2-2(큰 격자 출력)는 실자료 adapter를 열기 전에 고쳐야 한다.

이 판정은 위 후보 commit에만 유효하다. 모든 결과는 합성 자료 기준이며 실자료 정확도나 성능은 확인되지 않았다.

| 구분 | 건수 | 요약 |
|---|---:|---|
| 막는 결함 | 0 | — |
| 막지 않는 지적 | 13 | R2-1 ~ R2-13 |

## R1에서 넘어온 항목의 상태

| 항목 | 상태 | 근거 |
|---|---|---|
| N5 | **변경 없음, 열린 항목** | `linepixel.py:505-518`. reference `source='run-ref'`, target `source='run-tgt'` → delta 행 2개, 모두 `None`/`unavailable`, 사유 `target_layer_unknown`·`reference_layer_unknown`, 조각 delta도 `None`, 리포트 `partial`. 오류로 닫히지만 원천 ID가 제품별 실행 ID이면 실자료 비교가 전부 막힌다. G2에서 ID 의미를 확인해야 한다 |
| N6 | **실자료 설정에서는 누출 없음. 합성 mode에서는 여전히 열림 (R2-1)** | CLI와 pipeline은 `is_synthetic`을 스스로 넣지 않는다. 값은 설정 파일에서만 온다 (`pipeline.py:52-53`, `:66-67`). `mode: real`은 loader가 항상 막는다 (`adapters/numeric.py:312-317`). 아래 실행 결과 참조 |
| N9 | **변경 없음, 열린 항목** | `adapters/numeric.py`·`measurement.py`·`numeric_images.py`는 `8a35c0e` 이후 바뀌지 않았다. 예: pitch 0.05, 폭 2001 px이면 `2001×0.05 = 100.05000000000001`이라 선언값 100.05와 달라 `measured_domain_mismatch`가 된다 (`adapters/numeric.py:346-348`). 1990~2010 px 중 8개 폭이 이렇다. 오류로 닫힌다. 보존 허용오차 `1e-9`도 그대로다. G2에서 정할 항목이다 |

N6 실행 결과:

| 설정 | 결과 |
|---|---|
| `mode: real` + 합성 manifest + 설정에 `is_synthetic: true` | `validate-data`·`compare`·`electrode-map` 모두 exit 2, `synthetic_input_in_real_mode`, `error.json`만 생성 |
| manifest `is_synthetic: false` + `mode: synthetic` | exit 2, `real_input_requires_approved_adapter` |
| manifest `is_synthetic: false` + `mode: real` | exit 2, `real_source_format_unconfirmed` |
| `pending.yaml` (density 계약·unit basis 없음) | exit 3, 가중값·panel·delta 모두 `null` + `density_contract_unconfirmed`·`unit_area_basis_unconfirmed`, 면적은 유지 |
| `bin_semantics: per_um` 계약 | exit 2, `density_contract_metadata_mismatch` |

## 막지 않는 지적

우선순위 순이다.

### R2-1. unit basis 차단이 설정이 선언한 값에만 의존하고 manifest의 unit domain과 대조하지 않는다 (N6)

- 위치: `src/simcompare/pipeline.py:65-71`, `src/simcompare/linepixel.py:595-599`
- 실패 입력 (실행 확인): 합성 demo의 manifest에서 `unit_domain`을 "측정 domain은 unit 2개"로 바꿨다. reference는 `unit_width_um=3`, `measured_width_um=6`, `domain_shape_units_yx=[1,2]`이다. 설정은 그대로 `unit_bases: one_unit, is_synthetic: true, unit_size_um: [6,1]`이다.
- 결과: `compare` exit 0, 상태 `available`, 사유 없음. unit line 4.0, panel 400.0, N=100. unit 2개 분량의 mask 전체가 한 unit 값으로 나온다.
- 기대 동작: manifest가 측정 domain을 여러 unit으로 선언하면 frame 전체를 `one_unit`으로 보는 basis는 `unit_area_basis_unconfirmed` 또는 불일치 사유로 막힌다. "2×2 결과에서 한 unit 값을 얻는 방식"은 아직 미답변이다.
- 막지 않는 이유: 기본값이 아니라 합성 표시가 붙은 명시 설정이고, 실자료 mode는 loader에서 막힌다. 합성 demo 자신의 manifest는 unit과 측정 domain이 같아 모순이 없다.

### R2-2. 전극 수 지도 출력이 셀 수에 비례해 커져 사용자가 말한 격자 크기에서 쓸 수 없다

- 위치: `src/simcompare/report.py:176-186` (셀마다 CSV 행, 행마다 `coverage` 전체), `src/simcompare/electrode_map.py:205` (frame마다 `valid` 목록), `src/simcompare/report.py:225` (HTML에 JSON 전체 삽입)
- 측정 (case 3개, layer 1개):

  | 격자 | `electrode_map_map.csv` | `electrode_map.json` / `.html` | 쓰기 시간 |
  |---|---:|---:|---:|
  | 200² | 39.9 MB | 3.6 MB / 3.6 MB | 1.2 s |
  | 400² | 159.8 MB | 14.1 MB / 14.1 MB | 5.1 s |

- 2000² (게이트 답변의 2×2 domain 크기)로 비례 계산하면 CSV 약 4 GB, JSON과 HTML 각 약 350 MB다. 이는 추정값이며 2000²를 직접 실행하지는 않았다. case가 늘면 행마다 붙는 `coverage`도 커진다.
- 기대 동작: 셀별 행에 coverage를 반복하지 않고, 지도 값은 배열 파일로 내보낸다. heat map PNG와 sidecar는 이 문제가 없다 (400²에서 0.1 s).
- 막지 않는 이유: 값은 맞고, 이 후보는 실자료를 읽지 못한다.

### R2-3. heat map 색이 전극 수 순서를 따르지 않는다

- 위치: `src/simcompare/heatmap.py:18-28`
- 내용: 0~6은 고정 색이고 7 이상은 황금비 hue로 정한다. 7 초록 `(61,217,54)`, 8 분홍 `(217,54,109)`, 9 파랑 `(54,156,217)`으로 수가 커져도 색이 "더 뜨거워지지" 않는다. 1과 22는 RGB 거리 29.5, 7과 20은 27.9로 가깝다. 0~199에서 완전히 같은 색은 없다.
- 기대 동작: 사용자가 색만 보고 취약 위치를 판단하므로 수가 클수록 한 방향으로 진해지는 순서형 색을 쓴다.
- 확인된 것: 셀 색은 정확하다 (원점 `(100,200)`, pitch 0.05, 3×2 격자에서 6개 셀 모두 기대 색, 셀 경계 픽셀도 일치). 범례는 valid 안에 실제로 나온 값만 싣는다. extent `[100,200,100.1,200.15]`는 손계산과 같다. invalid 셀은 회색이고 0(흰색)과 구별된다.

### R2-4. 오류 뒤에 이전 실행의 완성 리포트가 그대로 남고, 성공 뒤에 이전 `error.json`이 남는다

- 위치: `src/simcompare/cli.py:87-113`
- 실행 확인: `compare` 성공 후 `target/pairs.csv`를 손상시켜 다시 실행 → exit 2, `error.json`·`execution.json`은 실패를 기록하지만 `comparison.json`·`comparison.html`·CSV 7개가 폴더에 남는다. 반대로 실패 후 성공하면 `error.json`이 완성 리포트 옆에 남는다.
- 기대 동작: 실패 시 같은 이름의 이전 리포트를 치우거나 표시하고, 성공 시 이전 `error.json`을 지운다.
- 막지 않는 이유: 오류 실행이 새 리포트를 쓰지는 않는다 (빈 폴더에서는 `error.json`·`execution.json`만 생긴다). `docs/usage.md:26`이 `execution.json`을 먼저 보라고 적어 두었다.

### R2-5. `electrode-map`은 exit 2일 때도 리포트 한 벌을 쓴다

- 위치: `src/simcompare/pipeline.py:104-113`
- 실행 확인: `diameter_um: 7.5` (32개 지름에 없는 값) → exit 2, 상태 `unavailable`, 사유 `source_case_missing`. `electrode_map.json`·`.html`·CSV·PNG가 모두 생기고 `error.json`은 없다. HTML은 `unavailable`을 표시하고 지도는 전부 회색, counts는 `null`이다.
- 기대 동작: 지름 격자에 없는 값은 설정 오류로 거부한다. 같은 exit 2가 "오류, 출력 없음"과 "unavailable 리포트 있음" 두 뜻으로 쓰이지 않게 한다.
- 막지 않는 이유: 완전한 값처럼 보이지 않고 `docs/usage.md:24`에 적혀 있다.

### R2-6. layer가 여러 개면 density 분포 하나만 쓰여 나머지 layer가 다른 사유로 막힌다

- 위치: `src/simcompare/pipeline.py:57-62`
- 입력 (실행 확인, API): M2와 M3 두 layer에 M2용 분포 하나 → M2 `2.0 available`, M3 `None unavailable`, 사유 `density_contract_unconfirmed`.
- 기대 동작: "이 layer의 분포가 없다"는 별도 사유를 낸다. 지금 사유는 G1 미답변과 구별되지 않는다. manifest가 분포 ID 하나만 가리키는 구조도 G1 답변 때 다시 정해야 한다.

### R2-7. 리포트의 합성 표시가 고정 문자열이다

- 위치: `src/simcompare/report.py:57` (`'is_synthetic': True`), `:224-225` ("합성 동작 검증")
- 지금은 실자료가 막혀 있어 틀린 표시가 나오지 않는다. 실자료 adapter를 열 때 dataset에서 읽도록 바꿔야 한다.

### R2-8. 모든 vertical case가 N/A인 지름에서 `vertical_only`·`simultaneous`가 `available 0.0`으로 나온다

- 위치: `src/simcompare/linepixel.py:357-362`. 이 동작은 `tests/test_failure_pieces.py:59-66`이 기대값으로 고정한다.
- 입력: 합성 demo의 d=1 (vertical AB는 높이 3 um 미만이라 N/A) → 세 조각 모두 `0.0 available`, group coverage에 `not_applicable=1`.
- 판단할 것: 계약(PLAN §2.1)은 N/A를 0으로 만들지 말라고 한다. union 쪽은 R1이 "N/A case는 0 기여, 다른 short는 계산"으로 받아들였고 조각도 같은 규칙을 따른다. 조각별로 `not_applicable`을 따로 표시할지는 사용자 확인 항목이다.

### R2-9. 한 출력 안에 게이트 상태가 두 가지로 보인다

- 위치: `src/simcompare/synthetic.py:106-107`
- 합성 manifest는 G7a·G7b를 `pending`으로 선언하고, 이 값이 `comparison.json`의 `reference.gates`에 그대로 실린다. 같은 파일의 `applied_gate_decisions`는 `answered`다. 적용 결정이 맞는 값이지만 읽는 사람이 혼동할 수 있다.

### R2-10. `producer_commit`이 git checkout 밖에서 `null`이다

- 위치: `src/simcompare/roi_export.py:271-274`
- 실행 확인: 저장소 밖 사본에서 `review_rois.json`의 `producer_commit`이 `null`이고 사유가 없다. 패키지가 다른 git 저장소 아래에 설치되면 그 저장소의 HEAD가 기록될 수 있다 (코드 판독, 실행하지 않음). 계약(PLAN §2.4)은 이 필드를 요구한다.

### R2-11. 삭제된 모듈을 설명하는 문서에 legacy 표시가 없다

- 위치: `docs/arch/config.md`, `docs/arch/checks.md` (둘 다 삭제된 `config.py`·`checks.py`의 계약을 현재형으로 적는다)
- `docs/usage.md:53`은 남은 `docs/arch`가 legacy라고 밝히고 README에서 링크하지 않는다. PLAN T7은 문서 자체에 표시를 요구한다.

### R2-12. `pyarrow`·`zarr`가 의존성에 없다

- 위치: `src/simcompare/adapters/particle_sim_run.py:147` (`pd.read_parquet`), `:225` (`import zarr`)
- `scripts/5_check_particle_sim_run.sh`가 `--with pyarrow --with zarr`로 넣어 준다. 수치 CLI 경로는 이 모듈을 불러오지 않는다. T7 이전부터 있던 상태다. 수치 경로가 쓰는 numpy·pillow·pyyaml은 선언돼 있고 scipy·torch·scikit-learn import는 남아 있지 않다.

### R2-13. 일부 설정 오류 메시지가 Python 내부 문구다

- 위치: `src/simcompare/pipeline.py:67`, `:72`
- 입력: `unit_bases: [5]` → `오류: 'int' object is not a mapping`; `panel: 7` → `오류: simcompare.linepixel.PanelSpec() argument after ** must be a mapping, not int`. exit 2이고 traceback은 없다.

## 점검 항목별 결과

| 항목 | 결과 | 근거 |
|---|---|---|
| 조각 3개의 합 = union | 맞음 | 무작위 200회(지름 3개, case 5개)에서 group별 `lateral_only+vertical_only+simultaneous = union`, `union = line + pixel`, 가중 조각 합 = 가중 line+pixel. 위반 0건 |
| 두 유형이 덮은 셀 = simultaneous | 맞음 | lateral {0,1}, vertical {1,2} → 1 / 1 / 1. 같은 pair의 lateral·vertical 한 셀 → simultaneous 1, shared bucket 없음 |
| line / pixel과 섞이지 않음 | 맞음 | 별도 dict·별도 CSV(`comparison_failure_pieces.csv`)·별도 HTML 표와 안내문. line/pixel/transfer 값은 조각 추가 전후 같음 |
| 조각의 상태 규칙 | 맞음 (R2-8 유의) | vertical missing → 세 조각 모두 `partial`, `value=None`; 전부 N/A → `not_applicable`; 미매핑 pair → `partial` + `unmapped_pair`; valid 구멍 → `partial` |
| 지름별 가중, 제품별 N, delta 부호 | 맞음 | 합성 demo 손계산: reference 5/0/1 × N=100, target 5/0/1 × N=50, delta −250 / 0 / −50. 출력과 일치 |
| heat map 셀 색·범례·extent | 맞음 | R2-3 참조 |
| heat map 배율 | 맞음 | 긴 변 400 px 이상이 되도록 정수 배율, 2000 px는 1:1 |
| 지도 기본값: threshold 없으면 영역 없음 | 맞음 | 기본 실행 `regions=0`, `threshold=None`, `regions_requested=False`. `--min-count 3` → 영역 1개. `0`, `True`, `2.0`, `'3'`, `-1`은 `invalid_count_threshold` |
| canonical 경로의 open 거부 | 맞음 | manifest에 `failure_type: open` → exit 2 `unsupported_numeric_failure_type`; API는 `map_requires_short_only` |
| ROI: 원점이 0이 아닌 물리 좌표 | 맞음 | 원점 `(100,200)`, 영역 셀 1~4 → bbox `[101,200,105,201]`, 중심 `[103,200.5]`. index 같은 좌표 `[2.5,0.5]`는 `position_outside_source_frame` |
| ROI: 구별된 ID, 차단 사유 | 맞음 | 10행 ID 모두 다름. 영역 하나의 case 4개가 각각 한 행 |
| ROI: 수동 위치를 case에 자동 배정하지 않음 | 맞음 | case 없는 수동 점 → `blocked: case_context_required`, `case_id=None` |
| ROI: threshold 없이 `map_region_ids: all` | 맞음 | 영역 행 없음, `excluded_records`에 `map_threshold_required`, exit 3 |
| ROI: hotspot·CA를 모델 입력으로 내보내지 않음 | 맞음 | asset은 layout·valid·CNT·VIA만. `tensor_input_policy` 명시. `crop_recipe`는 `pending`(G4) |
| 리포트: unavailable이 0이나 빈칸이 아님 | 맞음 | pending 실행의 summary CSV: 빈칸 0개, 0 값 0개, 모두 `null` + 상태·사유 |
| 리포트: partial이 complete로 보이지 않음 | 맞음 | pending HTML `상태: partial`; 부분 값은 `partial_value`에만 |
| 리포트: shared bucket 중복 합산 없음 | 맞음 | `unique_bucket_total`이 bucket ID로 한 번만 세고 class 합과 다르면 오류 |
| 리포트: HTML escape | 맞음 | 제품 ID `</script><script>alert(1)</script><img src=x onerror=alert(2)>&"'`와 case ID `<b>x</b>` → HTML 3종에서 날 `<script`·`<img`·`<b>` 0건 |
| CLI 종료 코드 | 맞음 | available 0, partial/blocked 3, 입력·설정 오류 2. 깨진 설정 6종 모두 exit 2, traceback 없음 |
| 오류 시 완성 리포트를 쓰지 않음 | 맞음 (R2-4, R2-5 유의) | 빈 폴더에서는 `error.json`·`execution.json`만 |
| 폐기: retired module import 없음 | 맞음 | `src`·`scripts`·`tests`·`configs` 검색 결과 없음. 모듈 파일 15개 삭제 확인 |
| 폐기: 제거 명령이 0이 아닌 코드로 실패 | 맞음 (직접 실행) | `train`·`embed`·`analyze`·`evaluate`·`all` 모두 **exit 2**, stderr에 폐기 안내. `--help`는 승인된 6개 명령만 |
| 폐기: 삭제 시험의 대응 | 맞음 | `docs/model_design.md:13-45`에 대응표. 유지 파일 5개에서 빠진 시험 함수는 이름으로 볼 때 삭제 모듈(ranking·retrieval·CAA·config·checkpoint·legacy generator) 전용이다. 이 중 2개(`test_size_tolerance_converts_physical_units`, `test_average_count_per_glass_is_normalized_by_surveyed_area`)는 본문까지 읽어 확인했고 나머지는 이름만 대조했다 |
| 폐기: 0/255 mask·경로 안전 | 맞음 | `tests/test_numeric_images.py:15-16` (PNG·bool·0/1·0/255 동일 CA), `:41-47` (절대·상위·symlink 탈출) |
| 폐기: 의존성 선언 | 맞음 (R2-12 유의) | `pyproject.toml`에서 torch·scikit-learn 제거 |
| 시험의 기대값 | 손으로 쓴 값 | 조각·heat map·CLI E2E·ROI 모두 리터럴이나 손계산 fixture. 무작위 시험은 순수 집합 oracle |
| G1 단위·조사면적, G2 unit 환산, G4 | pending으로 표시 | `current_gate_decisions()`가 세 항목을 `pending`, `answer: null`, 사유와 함께 낸다. `/4` 적용 코드 없음 |
| density 지름당 한 값 | 맞음 | `diameter_bin_policy`에 기록, bin 폭을 요구하지 않음 |

## 실행한 것

모두 후보 사본에서 실행했다. 네트워크·학습·저장소 파일 수정 없음.

1. `python -m pytest -p no:warnings -p no:cacheprovider -q` → **464 passed, 2 skipped**.
2. 합성 CLI: `demo-data`, `validate-data`(exit 3), `compare`(exit 0), `electrode-map`(exit 0), `export-review-rois`(exit 0), `pending.yaml`의 `compare`(exit 3). 결과를 손계산과 대조했다.
3. 폐기 명령 5개와 없는 명령, `--help`.
4. 변형 입력: real mode 3종, unit domain 불일치(R2-1), open case, per_um 계약, 손상된 pairs.csv의 재실행, 깨진 설정 6종, 수동 ROI 6종과 `--min-count` 0·3, 악성 ID.
5. API 실험: heat map 색 0~199와 3×2 격자의 픽셀, threshold 검증, N5, 조각 보존 200회, 다층 density, 200²·400² 지도 출력 크기.

## 확인하지 못한 것

- 실자료 입력과 게이트 의존 동작.
- torch 없는 새 환경을 직접 만들지는 않았다. `tests/test_retired_paths.py`의 import 차단 시험이 통과한 것만 확인했다.
- `scripts/*.sh`는 `uv`를 쓰므로 실행하지 않고 읽기만 했다.
- 2000² 격자의 실제 출력 크기와 시간 (R2-2는 비례 추정).
- DL2 쪽 소비자와 `review_roi_v1` 왕복 (T10·T12 범위).
