# 질문 상태와 보류 범위

## numeric_v2 (PLAN_v2 §3 / T1)

`answered`는 기록으로 확인한 결정, `pending`은 사용자 답변이 없는 항목이다. pending의 답변은 `null`이며 합성 예제는 답변의 대체물이 아니다. 실제 코드 지원 여부와 질문 답변 여부는 구별한다.

| ID | 상태 | 답변 / 필요한 근거 | 답변 전 가능한 일 | 차단되는 범위 |
|---|---|---|---|---|
| N1 | answered | 원 CA는 um^2, hotspot은 이물 중심 궤적, 제품 내 공통 좌표·축척 | full-mask 면적·frame 검증 | 임의 팽창/resize/warp 허용 없음 |
| N2 | answered | line/pixel 수치는 shorts-only·line 우선; shared를 나누지 않음 | 기존 line/pixel·pair membership 검산 | yield·종류별 simultaneous로 대체 불가 |
| N3 | answered | vertical은 실제 상부 layer의 불량/density, 높이 미만은 N/A; 5 um 이상 높이 없음 | 명시 height·stack에 따른 합성 판정 | 실제 개별 height를 추측할 수 없음 |
| N4 | answered | 지름 1..30, 40, 50 um의 32개 조건 | case별 expected coverage·0/누락/N/A 구별 | 누락 0 채움/40·50 폭 추측 금지 |
| N5 | answered | case ID·failure_type·전극/role/actual_layer 결합 보존, pair class는 두 전극 unordered 기준 | orientation 반례·유형 추적 | raw fab_pair_key 분해/정렬 금지 |
| N6 | answered | ROI 생산 C/T6, 소비 DL2/T10, 왕복 T12; 출처는 지도 영역·manual | review_roi_v1 문서·위치 export 계약 | 제품 간 구간 대응/검색 결과로 ROI 자동 선택 금지 |
| G1 | pending (일부 answered) | 지름마다 한 값(per_bin, 2026-10-08), 연속 per-um 아님; bin 폭 질문은 종료. null — 실제 단위·조사면적 변환·공정/기간 근거 미답변 | 지름별 raw 값·명시 합성 per-bin 검산·면적 | 실자료 가중값 및 parser 최종 승인 |
| G2 | pending (일부 answered) | 제품 간 동일 um/pixel·제품별 pixel 수·제품 내 같은 layer 크기, 2×2 약 100 um/2000 px(2026-10-08). null — 한 unit 환산 및 원천/migration 세부 미답변 | 원본 2×2 지도 표시·full-mask 면적 | 자동 /4·unit/panel 완료 및 reader 최종 승인 |
| G7a | answered | lateral-only/vertical-only/simultaneous 지표 구현, open 제외(2026-10-08) | case 유형·기존 line/pixel 보존 | 면적·가중·panel·delta 구현; G1/G2 미답변 real 값은 null, shared/transfer를 simultaneous로 이름 변경 금지 |
| G7b | answered | 지도는 현재 short-only, 사용자 결정 2026-10-08 | count 배열·heat-map, 수동 ROI 기본, 명시 threshold 영역 ROI | 누락 source/valid/frame/shape의 차단 사유 유지; legacy open helper는 보존 |
| G3 | pending | null — DL1/DL2 별도 repo 이름·경로 확인 | 교환 계약 초안 | DL2 생성/rename/remote/push; 이름 답변은 push 허가 아님 |
| G4 | pending | null — near/wide 물리 크기 및 비인접 pair 중간 layer 정책 | 매개변수 crop 시험·원본 layer·ROI 위치 계약 | 실자료 기본 crop/channel/학습 recipe 확정; 위치 export 자체는 가능 |
| G5 | pending | null — whole-unit isotropic resize+pad / electrode×layer set 제안, 최소 선폭·격자, CNT/VIA endpoint 형식 | 독립 overlap/validator 수정·합성 비교 | 권고를 확정 설계로 표시·실자료 grid/augmentation 확정 |
| G6 | pending | null — lineage/split, 선택 labels·unknown/negative, Recall@k/누락·자원/시간 목표, 수치 검산/오차 | harness·빈 수락 양식·baseline | 실자료 성능/기존 수치 일치 PASS |

G7a와 G7b는 독립 질문이며 2026-10-08 답변을 각각 반영한다. 지도는 자동 threshold/영역 순위를 기본 생성하지 않고 사용자가 판단한다. G1 bin 폭은 닫혔지만 단위/조사면적은 pending이며, G2의 한 unit 환산도 pending이다. 실자료 수락과 사용자 검산은 별도다.

## legacy export와 폐기 상태

| 항목 | 상태 | 범위 |
|---|---|---|
| 기존 1.x source/ID 보존 | answered | merge-exports와 particle-sim 검사 유지. 1.2 raw source-step assignment는 numeric 상부 layer로 암묵 변경하지 않음 |
| legacy loader 유지 | answered | example/preprocess는 export 검산 시험에서 사용. numeric CLI에서 사용하지 않음 |
| old AE/PCA/CAA 실행 | answered | T7에서 폐기. train/embed/analyze/evaluate/all은 명시 오류. CAA placeholder·legacy demo 없음 |
| 현재 문서와 역사 기록 | answered | 남은 docs/arch·walkthrough는 legacy / archived, 현재 설명에서 연결하지 않음 |
| 실제 numeric adapter/migration | pending | G2 원천 형식·귀속 대응 확인 전 legacy export를 자동 변환하지 않음 |

기존 count/glass 설명은 G1의 실제 단위/조사면적 변환 승인이 아니다. legacy 1.2 source-step 결정은 그대로 유지하고 numeric_v2와 충돌하면 명시 migration 확인을 요구한다.

## 2026-10-09 답변 (DL2 저장소·창 크기·hotspot 파일)

| 항목 | 상태 | 내용 | 남은 것 |
|---|---|---|---|
| G3 | answered | DL2 저장소 생성 승인. `~/git/particle-sim-dl2`, package `psdl2`, 로컬만 | remote/push는 별도 요청 |
| G4 | pending (방침 answered) | 창 크기는 실제 실행 전에는 알 수 없음. near/wide um 값은 기본값 없는 필수 설정 | 실제 크기, 비인접 vertical pair의 사이 layer 포함 여부 |
| hotspot 파일 | 일부 answered | 이진 이미지이고 hotspot이 검정. asset 참조에 `value_semantics: binary_zero_is_marked`를 적어야 함(자동 판별 없음) | 파일 형식(PNG/BMP/TIFF, bit depth), layout 이미지의 극성, 2×2에서 한 unit 값 환산 |


## 2026-10-13 답변 (보류 항목·hotspot 파일)

| 항목 | 상태 | 내용 | 남은 것 |
|---|---|---|---|
| 전극 쌍 조각별 기여 | answered | "픽셀별 기여도"는 전극 쌍 사이에 공유되는 hotspot을 처리하고 쌍별 기여를 판단하는 것을 뜻함. 단독/공동 조각(`Bucket`)과 `contributing_source_case_keys`로 구현된 범위가 맞음 | 없음 |
| layer별 윤곽을 한 평면에 겹쳐 보는 화면 | 보류 | 더 고민이 필요하다는 사용자 판단. 구현하지 않음 | 사용자가 다시 꺼낼 때 |
| 눌러서 따라가는 뷰어 | 보류 | 구현하지 않음. 추적은 JSON/CSV의 ID로 함 | 사용자가 다시 꺼낼 때 |
| 밀도 단위·조사면적 환산 (G1 나머지) | 보류 | 사용자가 보류로 지정 | 사용자가 다시 꺼낼 때 |
| 수율 환산 | 보류 | 기존 보류 유지 | 사용자가 다시 꺼낼 때 |
| 같은 net 쌍 제외 | 논의 중 | 현재 자동 판정 없음. 입력에 같은 net 쌍이 있으면 그대로 계산됨 | 실제 시뮬레이터가 같은 net 쌍의 hotspot을 내는지, 제외 목록의 출처 |
| hotspot 파일 | **확인 필요** | 사용자(2026-10-13): PNG이고 검정 배경 / 흰 패턴. 이 말대로면 `value_semantics: binary`. 2026-10-09 기록은 "hotspot이 검정"(`binary_zero_is_marked`)이라 서로 반대임 | 어느 쪽이 맞는지 사용자 확인. 확인 전에는 코드·기본값을 바꾸지 않음(자동 판별 없음, asset마다 명시) |

참고: reader는 PNG mode `1`, `L`, `I`, `I;16`만 받는다. RGB나 팔레트 PNG는 `unsupported_png_mode`로 거부된다.
