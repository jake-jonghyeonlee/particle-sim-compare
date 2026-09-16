# 확정 필요 사항과 현재 임시 처리

"실제 경로"는 `mode: real` 설정, "demo 경로"는 `mode: demo` 설정을 뜻한다. 물리적 의미를 바꾸는 미확정 값은 실제 경로에서 오류를 내고, demo 경로에서만 명시적 demo 규칙으로 진행한다.

| 항목 | 현재 임시 처리 | 실제 경로 동작 | 확정 후 수정 위치 |
|---|---|---|---|
| 실제 파일 형식·디렉터리 구조 | CSV/JSON + PNG/NPY 예시 어댑터 | 어댑터 교체 전제 | `src/simcompare/adapters/` 새 어댑터 |
| Hotspot 색상·투명도·축척 | grayscale 강도를 "가공 강도"로만 사용 | 범례(`data.hotspot_legend`) 없으면 확률 변환 금지 | `preprocess.legend_to_values`, 어댑터 |
| CA 단위·몬테카를로 설정 | `ca_unit` 문자열 필수, 설정은 provenance에 보존 | 단위가 `<length>^2`와 다르면 면적 보정 불가로 기록 | `schema.AnalysisBundle.ca_per_area` |
| Fab 원자료 단위·조사 면적·구간·기간 | `value_kind`/`value_unit`/`size_unit` 필수, count는 조사 면적 필수 | 불명확하면 `DataContractError` | `schema.validate_fab` |
| CAA 실제 계산식 | `demo_probability_per_bin`: 동일 크기 격자의 확률 가중치 × CA 합 | demo 규칙 사용 시 설정 오류, 확정 규칙 미구현 시 `CAAError` | `caa.interpret_fab`에 확정 규칙 추가, `caa.confirmed: true` |
| 서로 다른 크기 구간 변환 | 구간 중앙값과 CA 크기의 정확 일치만 사용 | 불일치 크기는 누락으로 보고 | `caa.compute_caa` |
| Vertical short 적용 공정 layer | 입력 `assigned_process_layer` 또는 `caa.process_layer_overrides` | 누락 시 오류, 하부 layer 추정 금지 | 입력 데이터/설정 |
| 제품 간 layer 대응·비교 가능 조건 | layer 이름 문자열 일치(`layer_policy: exact`) 또는 무시(`ignore`) | 대응표 없음 | `retrieval.candidate_mask`에 대응표 추가 |
| Lateral open 전극 치수 정의 | 형상 수치(`shape_metrics`)의 이름만 전달, 의미 해석 없음 | 해석하지 않음 | `geometry.json` 정의 확정 |
| 국부 이미지 범위·이물 영향 영역 | 전체 이미지만 사용 | 국부 조각 미지원 | 확장 지점: 같은 `sample_id` 아래 추가 이미지 목록 (독립 제품으로 취급 금지) |
| 전문가 검토 기준·개선 후보 제약 | 검토 카드에 근거와 "검토 필요" 목록만 출력 | 자동 판정 없음 | `cards.build_review_cards` |
| 크기 격자 보간 | 기본 `exact`; 선택 시 관측 범위 내부 log-size 선형 보간 | 범위 밖 외삽 금지 | `data.ca_alignment` |
| 학습/평가 분리 단위 | 제품 ID와 설계 계열 ID의 연결 성분 | 그룹 2개 미만이면 오류 | `split.group_split` |
