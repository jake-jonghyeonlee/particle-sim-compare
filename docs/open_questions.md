# 확정 필요 사항과 현재 임시 처리

"실제 경로"는 `mode: real` 설정, "demo 경로"는 `mode: demo` 설정을 뜻한다. 물리적 의미를 바꾸는 미확정 값은 실제 경로에서 오류를 내고, demo 경로에서만 명시적 demo 규칙으로 진행한다.

| 항목 | 현재 임시 처리 | 실제 경로 동작 | 확정 후 수정 위치 |
|---|---|---|---|
| 실제 파일 형식·디렉터리 구조 | CSV/JSON + PNG/NPY 예시 어댑터 | 어댑터 교체 전제 | `src/simcompare/adapters/` 새 어댑터 |
| Hotspot 색상·투명도·축척 | grayscale 강도를 "가공 강도"로만 사용 | 범례(`data.hotspot_legend`) 없으면 확률 변환 금지 | `preprocess.legend_to_values`, 어댑터 |
| CA 의미·범위 | legacy **확정**: 0~1 무차원 확률 기반 누적 위험값, 보통 최대 0.02~0.03, 면적 정규화 전 값. particle-sim 면적 규칙은 provisional; 환산 근거 확정 전 교차 계열 비교 금지 | 원 CA와 `CA / unit-pixel 면적`을 함께 사용; 혼합 계열 real 오류/demo 경고 | `schema.AnalysisBundle.ca_per_area`; 계산 담당자 환산 기준 확인 |
| Fab 자료 | **확정**: 특정 기간의 glass 1장당 평균 검출 개수와 glass 한 장의 실제 조사 면적을 가공자료로 입력 | `fab_measurements.csv`, 총량은 조사 면적으로 정규화 | `adapters.example`, `caa.fab_shape_and_total` |
| CAA 실제 계산식 | `demo_probability_per_bin`: 동일 크기 격자의 확률 가중치 × CA 합 | demo 규칙 사용 시 설정 오류, 확정 규칙 미구현 시 `CAAError` | `caa.interpret_fab`에 확정 규칙 추가, `caa.confirmed: true` |
| 크기 격자 | 확정: 1–30 um 1 um 간격 + 40, 50 um (32개), CA·hotspot·Fab 전체 제공 | `exact`, 보간하지 않음 | `data.ca_alignment` |
| Vertical short 적용 공정 layer | **2026-09-27 계약 1.2**: source-step Fab layer; **legacy(미표시/1.1)**: 적층표 상부 Fab layer | `layer_stack.csv`가 없거나 순서를 판정할 수 없으면 오류 | 입력 데이터/어댑터 |
| 제품 간 layer 대응·비교 가능 조건 | layer 이름 문자열 일치(`layer_policy: exact`) 또는 무시(`ignore`) | 대응표 없음 | `retrieval.candidate_mask`에 대응표 추가 |
| Lateral open 전극 치수 정의 | 형상 수치(`shape_metrics`)의 이름만 전달, 의미 해석 없음 | 해석하지 않음 | `geometry.json` 정의 확정 |
| 국부 이미지 범위·이물 영향 영역 | 전체 이미지만 사용 | 국부 조각 미지원 | 확장 지점: 같은 `sample_id` 아래 추가 이미지 목록 (독립 제품으로 취급 금지) |
| 전문가 검토 기준·개선 후보 제약 | 간격·폭·평행 길이·겹침·우회 후보를 넓게 제시하고 사람이 검토 | 외부 저항·Cap·coupling·신호·DRC·다른 short/open 결과 상태만 연결 | `cards.build_review_cards` |
| 전문가 정답 라벨 | 현재 없음 | 정확도·precision·recall을 주장하지 않고 held-out 반복성만 보고 | `pipeline.run_evaluate` |
| 학습/평가 분리 단위 | 제품 ID와 설계 계열 ID의 연결 성분 | 그룹 2개 미만이면 오류 | `split.group_split` |
| CNO(`contact_not_open`, contact·via hole 막힘) 불량 유형 | **보류**(2026-09-27): `FAILURE_TYPES`에 없음. particle-sim exporter는 CNO unit을 제외하고 `export_warnings.json`에 남기며, `particle_sim_run` 검사기는 미지원 경고를 낸다 | 기본은 경고, `--strict`면 실패 | 재개 시 결정: hole 형상 표현(3번째 layout 또는 형상 수치), 아래·위 전극 식별, 가림 기준(잠정: hole 면적 절반 이상, hole 하나라도 막히면 CNO), Fab layer(hole layer 이물 불량 Fab 자료는 있음), CAA에서 자동 open과의 중복 방지. 수정 위치: `schema.FAILURE_TYPES`, 어댑터, `dataset`/`model`(layout 2 slot 고정), `caa`, 분석·카드 |
| open 불량 회피안(선폭 보강) | **보류**(2026-09-29): 금속을 추가하는 편집이 없어 `edit_candidates`는 open 질의를 사유와 함께 건너뛴다(`edit_candidates.csv`의 `status=skipped`). 비교군 비교와 선폭 차이(`pair:a_min_width`)는 계속 계산한다 | 편집 후보 없음 | 재개 시 결정: particle-sim `make-variant`에 국소 추가(보강) 편집을 넣을지, 설계자 YAML로만 처리할지. 수정 위치: particle-sim `variant/edits.py`, simcompare `variants.edit_candidates` |
| 설계 규칙(최소 선폭·간격) 입력 | **보류**(2026-09-29): 입력 가능 여부 미확인 | 규칙 위반 편집 후보도 걸러지지 않고 제시된다. 다른 불량 증가 허용 여부와 함께 설계자가 판단 | 재개 시 `analysis` 설정 또는 별도 규칙 파일, `variants.edit_candidates`에서 후보 제외 |
