# 확정 입력 계약(binary nominal hotspot) 반영 audit — coordinator

- 작성: 2026-10-01 09:3x UTC, Run `run_2336681f13e5` coordinator (읽기 전용 조사, 편집 없음)
- 대상 스냅숏: branch `record-cno-open-fab-decisions`, HEAD `b507da9…`, 작업 트리는 T4(리뷰 지적 수정) 진행 중
- 재현 스크립트 산출물은 세션 scratchpad에만 있다(저장소 밖).

## 사용자 확정 (2026-10-01, 관찰자 터미널 경유로 전달된 원문)

> "핫스팟 이미지는 바이너리 이미지이고 면적의 픽셀이 ca값으로 환산되어 면적으로 나누어도 한픽셀은 모두 같은 값이야 업데이트하고 현재 레포에 문제될부분 있는지 확인해"

계약으로 정리하면:

1. 실제 사용자의 nominal hotspot 이미지는 **binary**다. 활성 픽셀은 모두 같은 CA 기여를 갖는다.
2. 일반형: `CA_R = k(source, d) × N_active,R`. R은 영역(전체 도메인 포함), `N_active,R`은 그 영역의 활성 픽셀 수, `k`는 **같은 source·같은 지름 d 안에서 상수**인 픽셀 계수다. 사용자 확정은 이 "source 내 상수 계수"까지다.
   - particle-sim 면적 nominal(`ca_nominal_um2`)에서만 `k = pitch²`(물리 픽셀 면적, µm²)로 특정된다(생산자 코드와 보존 gate로 확인).
   - 그 외 source(legacy 무차원 CA 등)의 `k` 값은 확정되지 않았다. 필요하면 source마다 명시해야 하며, 다른 source의 `k`를 빌려 쓰지 않는다.
3. 면적으로 나눈(정규화한) CA에서도 한 영상 안의 활성 픽셀은 모두 같은 값이다. 즉 한 영상 안에서 ROI로 나눠도 픽셀 간 강도 차이는 없다.
4. 유지되는 것: mean/probability 지원, 출처를 모르는 import의 검증 gate(전체 합 = CA 보존 검사), 제품 간 pitch/ROI가 다르면 픽셀 계수가 같다고 가정하지 않음.

이전 결정 중 바뀌는 문구: `docs/open_questions.md`의 "궤적의 합집합 면적이 CA 계산값과 같다고 가정하지 않는다"는, **확정된 binary nominal 입력에 한해** "CA = k × 활성 픽셀 수(같은 source 안에서 k 상수), particle-sim 면적 nominal에서는 k = pitch²"로 갱신한다. 그 외 입력(범례·가공 강도·출처 불명)은 기존 문구 그대로다.

## 이미 계약과 맞는 부분 (확인)

| 항목 | 근거 |
| --- | --- |
| 생산자 nominal CA = `sum(binary map) × pixel_area` | particle-sim `aggregation/critical_area.py:10–20`, 같은 map을 store와 적분에 사용(`pipeline.py:382–412`) |
| exporter nominal PNG는 0/255 무손실, NPY는 float32 0/1 | particle-sim `export/simcompare.py:59–63` |
| compare의 자동 승격은 exporter 선언 + 0/255 PNG 정확 복호 + 전체 합 보존 gate | `adapters/regional.py` `particle_sim_sources`, `exported_particle_sim_sources` |
| nominal source는 정확한 {0,1}만 허용, 값 rescale 없음 | `regional_measurement.validate_source` |
| ROI 적분은 원본 격자의 셀 면적 기준, 원천마다 자기 pitch 사용 | `regional_measurement.measure_union` |
| 위험 영역은 양수 support 전체(밝기 컷 없음) | `regions.collect_support` |
| 리뷰어가 실제 exporter 산출물 36개 원천에서 잔차 0 확인 | `review-v1.md` |

## 발견 사항

### B1. binary hotspot도 `processed_intensity`로 표시되고, NPY와 PNG의 값 범위가 다르다 (결함)
- 위치: `src/simcompare/adapters/example.py:109–147` `read_image`.
- 재현: 같은 binary 영상을 세 형식으로 저장해 `read_image(kind="hotspot", legend=None)`로 읽음.
  - PNG 0/255 → 0.0–1.0, 합 24, semantics `processed_intensity`
  - NPY float 0/1 → 0.0–1.0, 합 24, `processed_intensity`
  - NPY uint8 0/255 → **0.0–255.0, 합 6120**, `processed_intensity`
- 영향: (i) 0/255 NPY hotspot은 PNG보다 255배 큰 값으로 AE 입력과 위치 근거에 들어간다. (ii) binary라는 확정 의미가 bundle에 남지 않는다(`value_semantics`가 항상 가공 강도). layout은 0.5 기준 이진화라 영향 없음(0/255 NPY도 1.0).
- exporter 경로는 NPY를 0/1로 쓰므로 현재 exporter 산출물에서는 발생하지 않는다. 다른 도구가 만든 0/255 NPY에서 발생한다.

### B2. 기존 편집 후보 ROI(`variants._roi`)가 binary hotspot에서 "첫 활성 픽셀의 성분"만 고른다 (기존 heuristic의 한계)
- 위치: `src/simcompare/variants.py:105–119`. `nanargmax(heat)`가 binary에서는 raster 순서상 첫 활성 픽셀을 주고, `heat >= 0.5·max`는 활성 전체라서 그 픽셀의 4-연결 성분 하나만 선택된다.
- 재현: 4픽셀 성분(위쪽)과 20픽셀 성분(아래쪽)이 있는 binary hotspot → ROI는 4픽셀 성분 bbox. 편집 영역이 20픽셀 성분 쪽에만 있으면 hotspot ROI를 버리고 `closest_zone`으로 떨어진다.
- 영향: 기존 `edit_candidates`의 ROI 선정에만 해당한다. 새 지역 분석(`regions.py`)은 이 함수를 쓰지 않고 모든 성분을 유지하므로 영향이 없다. "가장 밝은 한 곳"이라는 전제가 binary 입력에서는 성립하지 않는다는 점을 문서에 구분해 적어야 한다.
- 참고: 같은 함수가 ROI를 이물 지름만큼 pad한다(`_bbox_um(region, pixel_um, float(size))`). 이것은 make-variant 편집 지정용 창의 여백이고 CA 적분에는 쓰이지 않는다. 재팽창 금지 결정(정량·위치 선정)과 구분해 문서화 필요.

### B3. exporter를 거치지 않은 binary hotspot은 확정 계약이 있어도 ROI CA가 unavailable이다 (설계 공백, 결정 필요)
- 현재 자동 승격 조건은 "particle-sim exporter 선언"이다. 다른 경로로 들어온 binary hotspot + 면적 CA는 `missing_source`/`unverified_source_semantics`로 남는다. 명시 manifest는 NPY density만 받는다(기존 hotspot PNG를 가리킬 수 없음).
- 사용자 확정을 그대로 쓰려면 "이 자료의 nominal hotspot은 CA 생성 binary map이다"라는 **자료 단위 선언**(설정 또는 manifest)과 기존 보존 gate의 조합이 필요하다. 선언 없이 binary라는 사실만으로 자동 승격하면 "출처 불명 import의 gate 유지"와 충돌한다.
- 실제 자료가 particle-sim exporter로 들어오면 이 공백은 문제가 되지 않는다.

### B4. 면적으로 나눈(무차원) CA의 국부 기여 (설계 공백, 결정 필요)
- 현재: legacy 무차원 CA는 `legacy_ca_not_area`로 ROI CA를 내지 않는다(면적 단위로 둔갑 금지).
- 확정 계약 3번에 따르면 binary 영상에서는 정규화 여부와 무관하게 픽셀 계수가 균일하므로, **기여율(ROI 활성 픽셀 수 / 전체 활성 픽셀 수)** 은 단위와 무관하게 정의된다. 다만 무차원 값을 um²로 표기하지 않는 원칙은 유지해야 하고, 전체 합 검증에는 정규화 분모(단위 면적)가 필요하다.
- 제품 간 비교에서는 pitch/ROI가 다르면 픽셀 계수가 달라지므로 "픽셀 수" 자체를 비교하면 안 된다(확정 4번). 현재 구현은 원천별 pitch로 um²를 계산하므로 면적 CA에서는 맞다.

### B5. 모델 입력용 hotspot은 binary여도 쌍선형 보간된다 (보고만)
- 위치: `dataset.py` `letterbox(…, binary=False)`. AE 입력 전용이며 ROI 정량에는 쓰이지 않는다(정량은 원본 배열). 바꾸면 학습 입력과 fingerprint 의미가 달라지므로 이번에는 수정하지 않고 기록만 한다.

### B6. 문서 정본의 문구가 확정 계약 이전 상태다
- `docs/open_questions.md` 17행, 29–31행(특히 31행 "합집합 면적 = CA로 가정하지 않는다").
- `docs/data_contract.md` 29, 35, 115행: hotspot을 "가공 hotspot"으로만 설명, binary nominal의 값 규약(0/1, PNG 0/255, NPY 범위)과 CA 환산이 없다.
- `docs/regional_analysis.md`: binary nominal 계약과 "제품 간 픽셀 계수 동일 가정 금지"를 명시적으로 적어야 한다.

### B7. 기준(baseline) hotspot 요약 특징이 binary 입력에서는 정보가 겹친다 (해석 한계, 보고만)
- 관찰자 재현(읽기 전용)과 일치: binary 원본에서는 "평균"과 "0.5 이상 비율"이 같은 값이고, "최댓값"은 비어 있지 않으면 항상 1이다. 즉시 결함은 아니지만 `with_risk_baseline` 공간(`baseline.py:59–67`의 mean/max/frac50, 위험 입력을 포함할 때만 사용)에서 hotspot 특징 세 개가 사실상 하나의 정보(활성 면적 비율)만 준다는 점을 문서에 적는다.

### B8. 정규화 지표의 분모 — 무엇으로 나누면 의미가 있는가 (문서화 필요, 현재 구현은 적합)
- binary 계약에서 `CA / hotspot 자체 활성 면적`은 픽셀 계수 그 자체라 **상수**다. 취약 순위에 쓸 수 없다.
- `CA / 고정 공통 비교 창의 유효 면적`은 그 창의 점유율이라 의미가 있다. 현재 `roi_ca_per_valid_area`는 창의 유효 면적으로 나누므로(활성 면적이 아님) 이 정의에 해당하고, 등록된 쌍의 고정 공통 창에서는 양쪽 분모가 같다.
- 서로 다른 tight bbox(미등록 쌍의 자체 창)로 나눈 값은 분모가 달라 제품 간 비교에 쓰면 안 된다. 문서에 "면적 정규화 값은 같은 고정 창에서만 비교"라고 명시한다.
- raw ROI CA(µm²)는 분모 문제는 없지만, 그것만으로 공정한 비교가 되지는 않는다. 서로 다른 기능·범위를 각자 tight하게 잘라 비교하면 적분하는 support 자체가 달라진다. raw 값의 비교에도 **같은 구조 + 동등한 측정 범위**(등록된 고정 공통 창, 또는 합의된 비교 단위)가 필요하다. 그 조건이 없으면 자체 값만 나란히 보이고 증감은 내지 않는다(현재 구현의 미등록 쌍 처리와 같다).

### 관찰자 재현과의 대조 (2026-10-01, 읽기 전용, 저장소 무편집)
- B1: uint8 0/255 2×2 → PNG는 값 {0,1}·합 2, NPY는 값 {0,255}·합 510, 둘 다 `processed_intensity`. coordinator 재현(합 24 vs 6120)과 같은 결론. 실제 결함은 "NPY 255가 sigmoid 복원 타깃 범위 밖 + 파일 형식에 따라 값이 달라짐"이다.
- B5: `dataset.py`의 보간으로 binary 2×2가 5×5에서 중간값을 갖게 되지만 AE 입력 전용이며, 원본 배열로 계산하는 지역 CA를 훼손하지 않는다. 둘을 혼동하지 않는다.
- B2: binary 100×100에 1픽셀 성분과 400픽셀 성분 → `_roi`는 1픽셀 성분 주변 `[9,9,12,12]`만 선택. coordinator 재현(4픽셀 vs 20픽셀)과 같은 결론.

### 해석 범위 주의 (확정의 한계)
사용자 확정은 **같은 source(같은 영상) 안에서 활성 픽셀의 계수가 상수**라는 데까지다. legacy 무차원 CA를 µm²로 바꾸는 환산 계수가 새로 확정된 것은 아니다. 따라서 B4는 "무차원 CA에서도 기여율(픽셀 수 비)은 정의된다"까지만 말할 수 있고, 무차원 값을 면적으로 환산하거나 면적 계열과 섞어 비교하는 것은 여전히 금지다(`docs/decisions/2026-09-27-ca-unit-and-area.md`의 기존 결정 유지).

## 처리 방침 (coordinator)

- T4(리뷰 지적 수정)가 끝난 뒤 같은 editor에 직렬로 T5를 보낸다. 자동 대응 모델 구현으로 확대하지 않는다.
- T5 범위: B6 문서 갱신(B7·B8·해석 범위 주의 포함), B1 수정(값 범위·semantics 일관화), B2 최소 수정과 문서화, B5 기록.
- B3, B4는 계약·schema 확장이므로 구현하지 않고 선택지를 사용자에게 올린다.
- 코드 변경이 포함되므로 T4+T5를 합친 후보 v2로 독립 Opus high 재리뷰를 받는다.
