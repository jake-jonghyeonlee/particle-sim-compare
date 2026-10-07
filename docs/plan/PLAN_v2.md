# simcompare 수치 재작성 · DL1 · DL2 구현 계획

버전: **PLAN_v2 / 2026-10-07 / 제안 상태 — Opus 재검토·채택 전 구현 금지**

### v1 → v2 변경 요약

이 문서는 PLAN_v1의 완전한 대체 계획이다. `PLAN_v1_REVIEW.md`의 B1~B3와 A1~A5를 모두 반영했으며, 별도로 기각한 권고는 없다. 문서 버전 v2와 아래의 **첫 구현 범위(v1)**는 구분한다.

| 리뷰 항목 | 반영 내용 | 해결 절·Task |
|---|---|---|
| B1 | DL2는 같은 위치의 두 crop을 가깝게 하는 무라벨 학습으로 시작; 다른 위치를 자동 negative로 강제하지 않는 손실·반례, 선택 labels, hotspot 보조 학습 제외 | §2.5, §6 T11, §9 |
| B2 | C가 지도 영역 또는 사용자 지정 위치로 ROI 목록을 내보내고 DL2가 읽는 양방향 계약; 제품 간 구간 대응은 출처에서 제외 | §2.4, §6 T6/T10/T12 |
| B3 | case의 lateral/vertical 표시 보존; 종류별 조각 지표는 첫 구현의 non-goal 제안으로 명시하고 G7a 확인 | §1, §2.3, §3 G7, §6 T1/T2/T3 |
| A1/A2 | 수치·DL1·DL2·인수의 4단계와 단계 보고, 독립 리뷰 5묶음 고정 | §7, §8 |
| A3 | 지도 short-only는 G7b 답변 전 미확정으로 수정; 수치 short-only는 확정 유지 | §2.1, §3 G7, §4.1, §6 T5/T6 |
| A4 | T2는 새 numeric schema/image reader/adapter/measurement만 작성; legacy 파일은 읽어 필요한 원리만 이식 | §5, §6 T2/T7 |
| A5 | G5 유지; 겹침 수정·validator 등 답변 비의존 작업 허용 유지 | §3 G5, §6 T8/T9 |

리뷰가 채택한 G1~G6, 2×2 자동 나누기 금지, missing/N/A/zero 구분, 나누지 않는 shared 몫, 62쌍 제한 제거, 파일별 판정의 등급·근거, archive 선별 이식, 중단·재시도 절차는 유지한다. 파일별 판정 중 지도 open 처리의 실행 조건만 A3에 따라 G7b로 한정한다.

작성자는 Orca의 읽기 전용 자문 planner이며 이 문서만 작성한다. 이번 작업은 구현, 학습, 저장소 생성, push 또는 실자료 성능 검증을 수행하지 않는다. 아래 명령·파일명 중 현재 없는 것은 **후속 Task가 제공할 수락 인터페이스 제안**이다. 실행 완료 증거가 아니다.

## 1. 범위와 기준선

목표는 (1) 기존 수율 실무에 맞는 layer별/panel별 line·pixel 두 값, 전극 쌍 단독·공동 몫과 대상−참고 차이, (2) 5 um 전극 수 지도, (3) 단위 크기가 다른 제품의 형상·연결성 검색 DL1, (4) 한 검토 ROI로 국부 구조를 검색하는 DL2, (5) 폐쇄망 학습·검증 인수 패키지다. 수치·입출력·리포트는 `particle-sim-compare`에 두고 DL 코드는 별도 저장소에 둔다.

**일정에 넣지 않는 것:** 2×2 경계 횡단 기여의 새 모델링/보정, 합산값의 수율 환산, 변경 위치별 인과 기여 분해(변경 영역 내외·onset 지름·단일 변경 되돌리기), line/pixel 수치 경로의 open, 배선 보강, 자동 CNT/우회 편집, 슬라이드, 제품 간 구간 대응에 의한 검토 ROI 자동 선정, DL2 hotspot 예측 보조 학습. **첫 구현(v1)에서 불량 종류별 lateral-only/vertical-only/simultaneous 조각 지표는 제외하는 안**이며 사용자 G7a 확인이 남아 있다. 지도의 open 제외는 G7b 미확정으로, 수율 계산에서 open을 제외한 결정을 지도까지 자동 적용하지 않는다. DL2의 원본 ROI 측정은 검색 근거를 보여 주기 위한 것으로, 보류된 변경 위치별 기여 분해를 다시 도입하지 않는다.

확인한 기준선:

| 별칭 | 저장소·branch | commit / 관측 상태 |
|---|---|---|
| C0 | `/Users/jonghyeonlee/git/particle-sim-compare`, `rewrite/line-pixel-rate` | `758db3656c304362340cda5c79204a35190f12c3`; 계획 작성 전 clean |
| C1 | 같은 저장소, `rewrite/line-pixel-rate` | `2652fe374008aaae2ab598f2be9d8e15298cb214`; PLAN_v1과 리뷰가 커밋된 v2 작업 기준선, 작성 전 clean |
| A0 | 같은 저장소, `archive/pre-rewrite-20261007` | `5bde9f3df93fbfa6bcc65ec09f355f17390cc905`; `git show`로만 읽음, checkout하지 않음 |
| D0 | `/Users/jonghyeonlee/git/particle-sim-dl1`, `main` | `44a13a671d80f41ce495a7f1bf2da2cca2e16264`; clean, 로컬 전용 |
| N0 | 별도 DL2 저장소 | 아직 생성되지 않음; 경로·이름 확정 후 새 초기 commit을 기록 |

v2에서는 `PLAN_v1.md`, `PLAN_v1_REVIEW.md`, `PLAN_REQUEST.md` 전체와 아래 결정 기록을 다시 대조했다. v1에서 읽고 유지한 코드 근거는 `implementation_memory.json`의 2026-10-06/07 여섯 섹션 전체와 앞선 DL1/DL2·단독 신규 제품·32조건·다층 연결성 기록, 현행 `README.md`/`data_contract.md`, 현행 수치 후보 4개 파일, D0의 추적 파일 12개 전부다. A0에서는 이미지 reader, 경로/지름/단위 검증, Fab measurement reader, `regional_measurement.py`, regional source의 결측·격자 검증, 합성 생성기를 읽었다. 이 계획 작업에서는 구현 테스트나 학습을 실행하지 않았다. DL2 무라벨 시작 방향은 리뷰 B1이 명시한 사용자 결정을 적용하고, 정확한 손실 함수는 아래 구현 제안으로 구분한다. 코드 관찰과 사용자 확정 사실, 아래의 구현 제안을 구별한다.

## 2. 확정 규칙과 계산 계약 제안

### 2.1 기록의 우선순위

- hotspot은 이물 **중심**의 궤적이며 입자 반경만큼 다시 팽창하지 않는다. 원 CA는 `um^2`다. 제품 안의 조합은 공통 좌표·축척을 쓴다. 로더는 메타데이터 일치를 검사하되 임의 resize/warp로 일치시키지 않는다.
- union은 같은 제품·설계·원천/공정 조건·layer(process step)·지름 안에서만 한다. layer가 같아도 서로 다른 공정 노출/원천 조건을 무조건 합치지 않는다. 새 입력에서 layer와 공정 step이 일대일인지 G2에서 확인한다.
- vertical short는 상부 layer의 불량 및 density를 사용한다. 높이 미만 지름은 `not_applicable`이며 0이나 결측으로 만들지 않는다. 5 um 이상 높이는 없다는 사용자 사실을 유지한다. 누락 파일만 보고 높이 미만으로 추론하지 않는다.
- line/pixel 경로는 shorts only다. pair 표는 두 전극만 보며 순서·layer·lateral/vertical에 의존하지 않는다. 그러나 **원천 case ID와 `(electrode, role, actual_layer)` 양 끝 결합은 보존**한다. pair 분류용 키와 원천/검색 정체성은 다른 용도다.
- 10월 6일의 일반 union에 대한 ‘우선순위 없음’보다 10월 7일 line/pixel 실무의 **line 우선**을 이 경로에 적용한다. 원천별 CA와 중복 없는 union은 모두 보존한다. line과 pixel을 다시 한 수치나 수율로 합치지 않는다.
- 전극 수 지도 기록에는 open이 전극 하나로 들어가는 예시가 있다. 지도도 short-only로 할지는 **G7b 사용자 확인 대상**이다. 답변 전에는 short-only 합성 경로와 그 제한을 명시한 부분 지도만 검증할 수 있고 이를 확정된 전체 취약 지도라고 발표하지 않는다. open 포함 구현은 자동 착수하지 않고 답변에 따라 Task 범위를 재채택한다. 대표 지름은 기본 5 um, 변경 가능한 설정값이다. 모든 지름을 섞는 지도는 만들지 않는다.
- DL1/DL2는 원본 shape/CNT/연결 메타데이터만 입력받는다. hotspot·CA·Fab은 embedding 입력에 넣지 않는다. 새 제품 하나 또는 ROI 하나로 검색하며 수정 전후 쌍을 필수 입력으로 요구하지 않는다. 참고 CA를 대상 실제 CA로 대입하지 않는다.

### 2.2 수작업으로 검산 가능한 정의

한 그룹 `g,d`의 line pair union을 `L`, pixel pair union을 `P`라 하면 `P_effective = P ∩ complement(L)`, transfer `T = P ∩ L`, 전체 short union `U = L ∪ P`다. `area(U) = area(L) + area(P_effective)`이고 `area(T)`는 별도 진단이므로 총합에 더하지 않는다. 픽셀 면적은 pitch를 um로 변환한 뒤 제곱한다. valid/관측 범위 밖은 0 불량이 아니다.

각 class 안에서 한 픽셀을 덮는 **서로 다른 pair의 집합**을 유지한다. 집합 크기 1은 exclusive, 2 이상은 shared bucket이다. 같은 pair의 여러 원천 case는 pair 수준에서는 OR하지만 원천 case별 CA는 별도 남긴다. 공동 몫은 구성 pair를 나열하고 나누어 배분하지 않는다. pair별 화면에 같은 shared bucket을 여러 번 링크해도 합산은 bucket ID별 한 번만 한다. `sum(exclusive)+sum(shared)=class_total`을 지름별·layer별·panel별로 검사한다.

밀도가 **지름 bin당 면적 역수**로 확인된 경우에만 `V[g,class] = Σ_d area_um2[g,d,class] × density_per_um2[g,d]`를 사용한다. 지름당 연속 density이면 bin 적분/폭이 필요하므로 G1 답변 전에 적용하지 않는다. 40/50 um의 폭을 임의로 1 또는 10으로 정하지 않는다. 값은 정규화한 확률 가중치가 아니며 총 density를 보존한다. line과 pixel은 동일한 density 계약을 사용한다.

측정 domain→한 unit의 CA 변환은 G2에서 확인된 계약을 사용한다. **2×2라는 이유만으로 `/4`를 기본 적용하지 않는다.** 미확정이면 full-mask area/union/지름별 항목까지 제공하고 unit/panel 가중값·delta는 `null + unit_area_basis_unconfirmed`로 둔다. 사용자가 현행 `/4` 관행을 확인한 경우에만 그 관행과 한계를 명시해 적용하며 새로운 경계 보정은 구현하지 않는다.

panel unit 수는 `N = f(W_panel/W_unit) × f(H_panel/H_unit)`이며 제안 기본은 각 축 floor, 선택은 half-up round다. 선택과 입력 치수를 출력에 남긴다. 수치 임의 epsilon으로 floor를 올리지 않는다. `V_panel = N × V_unit`; `delta = V_panel,target − V_panel,reference`. 제품끼리 픽셀 좌표를 overlay할 필요는 없다. 비교할 layer/공정·지름·density 기준·pair 표 버전·면적 기준이 호환되는지 확인하고, 차이의 기준을 보고한다. 서로 다른 Fab 분포를 쓸 때 구조 변화만의 효과라고 부르지 않는다.

`Δtransfer = transfer_target,panel − transfer_reference,panel`은 line 우선으로 제외된 pixel 몫의 **순변화**다. 실제 같은 위치가 pixel→line 또는 역방향으로 이동했다는 사건 추적을 뜻하지 않는다. 제품 좌표 대응 없이 공간 이동을 확정하지 않는다.

모든 수치는 `available / partial / unavailable / not_applicable`와 사유·coverage를 함께 갖는다. observed zero, 기대 case 부재, 미매핑 pair, 누락 density, 높이 미만 N/A를 구별한다. 알려진 부분합은 별도 `partial`로 볼 수 있으나 완전한 panel 값이나 delta로 승격하지 않는다. `_diff`의 누락=0은 해당 항목의 부재가 관측 완료로 확인된 경우에만 허용한다.

### 2.3 불량 종류별 조각 지표의 범위 — B3

2026-10-06 합의한 lateral-only / vertical-only / simultaneous 분해는 **line/pixel과 독립인 축**이다. 예를 들어 lateral AB와 vertical AB는 pair 분류가 같더라도 서로 다른 source case다. 10월 7일 line 우선 규칙이 이 합의 자체를 없앤 것으로 해석하지 않는다.

coordinator 권고를 반영한 첫 구현 제안은 다음과 같다. T1/T2는 case에 `failure_type=lateral_short|vertical_short`와 양 끝 실제 layer를 보존하고, T3가 same-pair OR 후에도 원천 case·유형을 역추적할 수 있게 한다. **종류별 disjoint mask/면적/가중합/증감/리포트 지표는 이번 v1에 만들지 않는다.** pair shared bucket이나 pixel-to-line transfer를 이 종류별 simultaneous 지표라고 부르지 않는다. 이 제외는 G7a에서 사용자가 확인할 때 범위 확정된다. 미답변이면 유형 보존과 기존 line/pixel 계산까지만 진행하고 종류 분해 구현이나 합의 철회를 선언하지 않는다. 사용자가 포함을 원하면 T3/T6 범위·수락 반례·review candidate를 재계획하여 채택한 후 구현한다.

### 2.4 C → DL2 검토 ROI 내보내기 계약 — B2

**생산자는 C의 T6, 소비자는 DL2의 T10이며 왕복 검증은 T12다.** 첫 구현의 ROI 출처는 `electrode_count_region`(T5의 layer별 영역)과 `manual`(사용자 지정 위치) 두 가지뿐이다. 제품 간 구간 대응·등록 결과로 ROI를 만들지 않는다. DL2 검색 결과도 자동으로 새로운 query ROI를 선정하지 않는다.

T6의 제안 명령은 `simcompare export-review-rois --config <config> --out <directory>`이고 설정에서 지도 영역 목록 또는 사용자 위치 목록을 명시한다. 한 manifest 안에서 섞을 수 있으나 각 행의 출처는 보존한다. 출력은 `review_rois.json`과 원본 shape/CNT/valid asset의 상대경로 참조다. 수치 리포트의 hotspot·CA 파일은 embedding 입력 패키지에서 제외한다. 저장 형식은 다음 내부 계약이며 실제 원천 파일 양식은 G2의 답변에 맞춘다.

| 범위 | 필드·규칙 |
|---|---|
| envelope | `schema_version=review_roi_v1`, `producer_commit`, `source_manifest_hash`, `assets_root`, `rois` 및 `excluded_records`; asset 경로는 root 내부 상대경로, 파일 hash 포함 |
| ROI 정체성 | `roi_id`, `product_id`, `design_version`, `case_id`, `source_kind`, `source_region_id`(manual이면 null), `selection_policy_version`; 제품마다 반복되는 case ID를 단독 키로 사용 금지 |
| 원본 frame | `frame_id`, `length_unit=um`, `origin_xy_um`, `pixel_pitch_um`, `shape_yx`, `axis_order=yx`, 좌상단 cell-edge 원점·half-open bbox; 물리 축척을 잃지 않음 |
| 위치 | `center_xy_um`, `region_bbox_um`(지도는 원래 영역의 bbox, manual 점이면 null); 지도 영역은 bbox 중앙을 기준점으로 쓰는 제안 규칙을 기록하고 실제 영역 ID를 유지. 이 bbox는 near/wide crop 크기를 뜻하지 않음 |
| 분석 문맥 | 관여 전극·role·actual layer의 양 끝 결합, `failure_type`, process/source ID, 원본 layout/CNT/VIA endpoint/valid asset 참조 및 관측 상태; 임의 pair나 layer를 추정하지 않음 |
| 검토 provenance | 지도 대표 지름·threshold·connectivity·coverage·G7b 정책 상태는 선택 근거로만 기록. 모델 tensor에는 전달하지 않음. hotspot/CA/density/rate는 tensor 입력 필드로 금지 |
| 실행 상태 | `ready` 또는 `blocked`와 `reasons`; case·frame·원본 shape 대응이 빠지면 blocked. G4 near/wide 미답변은 위치 내보내기를 막지 않지만 실제 crop recipe 확정을 막음 |

지도 한 영역에 여러 source case가 있으면 `(region_id, case_id, 문맥)`별 ROI 행을 내보내고 같은 위치의 관계를 보존한다. 같은 위치의 여러 case/증강본을 독립 제품이나 무조건 negative로 취급하지 않는다. 사용자가 직접 지정하는 입력은 제품/설계/원본 frame/물리 좌표와 검색할 case 또는 명시적 전극·layer 문맥을 포함한다. 위치만 주어 case가 결정되지 않으면 `blocked: case_context_required`를 기록하고 임의로 가장 가까운 pair를 고르지 않는다. 원본 shape asset 연결이 없으면 지도는 표시해도 DL2 실행은 blocked다.

G7b 미답변 상태에서 short-only 지도 유래 ROI는 `blocked: map_scope_unconfirmed`로 내보낼 수 있으나 확정된 지도 추천으로 DL2를 실행하지 않는다. 수동 ROI는 독립적인 case/frame/shape 조건을 만족하면 이 게이트와 무관하게 ready일 수 있다. T10은 ready 행의 물리 중심에서 G4의 near/wide 창을 동일 좌표로 추출하며, crop·valid·source version을 index/query 결과에 남긴다. T12는 C→DL2→C roundtrip에서 `roi_id`와 복합 source identity를 그대로 대조한다.

### 2.5 DL2의 무라벨 기본 학습 — B1

DL2는 **같은 물리 위치를 조금 다르게 잘라 본 두 view를 positive로 하는 대조 학습**으로 시작한다. labels 파일 없이 데이터 로딩·view 생성·loss·학습 명령이 구성되어야 한다. 두 view 각각은 모든 electrode/layer/CNT/valid에 동일 변환을 적용하고, 중심의 공통 관측 영역과 관여 전극·연결을 유지한다. 다른 위치를 우연히 같은 중심으로 재정렬하거나 bridge가 사라진 crop을 positive로 채택하지 않는다. G4/G6 전에는 synthetic 설정으로 이를 검증하며 실제 jitter/유효 overlap 기준은 config와 검증 기록에 남긴다.

**첫 손실 설계 제안:** 기본 무라벨 경로에서는 다른 위치를 모두 negative로 넣는 instance InfoNCE를 쓰지 않는다. 같은 위치 두 view의 projection `h1,h2`를 가깝게 하는 symmetric alignment 항과, batch의 차원별 분산 하한·off-diagonal covariance 규제를 결합한다. 이 무음성쌍 two-view 목적에서 다른 위치 간 cosine을 낮추는 직접 pair 항은 없다. variance/covariance 항은 모두 같은 feature로 붕괴하는 것을 막기 위한 분포 규제이며, 이 규제의 영향까지 없다고 주장하지 않는다. projection에는 분산 규제를 적용하고 검색 feature 정규화와 구별한다. 최소 유효 batch/양의 가중치/유한 입력을 검사하고 상수 embedding에서 규제가 0이 되지 않게 수락 oracle를 둔다. 정확한 계수·margin·projection 구조는 Opus가 수락할 구현 설정이며 실자료 효과를 사전 주장하지 않는다.

폐쇄망 ROI 대응 정답은 **선택 확장**이다. 있을 때만 알려진 similar/different 쌍에 제한된 supervised relation 항을 추가하고 unknown은 해당 항의 분자·분모에서 제외한다. contradictory label은 오류, 별도 위치의 unlabeled 유사 patch를 자동 negative로 채우는 fallback은 금지한다. supervised 항이 없어도 기본 학습이 성립해야 한다. label이 부족하다는 이유로 무라벨 경로를 실패시키거나 전체 DB를 negative로 바꾸지 않는다. 기본 alignment·분포 규제·선택 supervised 항과 사용 pair 수를 각각 로그에 남긴다.

hotspot 예측 보조 학습은 가능한 선택 연구 방향일 뿐 **첫 구현(v1) 범위 밖**이다. 따라서 기본/선택 relation 학습 모두 hotspot·CA·density를 입력/보조 target으로 읽지 않는다. 향후 보조 감독은 별도 계획이 필요하며 이 계획의 평가용 원본 CA 후처리와 혼동하지 않는다.

## 3. 사용자 게이트

답변은 coordinator가 Orca 질문으로 받아 정확한 문구·결정 버전·적용 Task를 checkpoint에 기록한다. 아래 게이트는 확정된 결정을 재질문하기 위한 것이 아니다. 미답변 자체를 동의로 간주하지 않는다.

| Gate | 질문 / 필요한 증거 | 답변 전 가능한 일 | 답변 전 불가능한 일 / 영향 Task |
|---|---|---|---|
| G1 — Fab | 실제 열·예시 값·단위, layer/지름 key, per-bin 또는 per-um density인지, 평균 count/조사면적 변환 여부, 40/50 bin 폭, 적용 공정·기간/분포 기준 | typed density 계약 초안, 단위·중복·결측 검사, 인공 per-bin fixture, 면적만 계산 | 실자료 parser 최종 승인과 실제 가중값/단위 확정; T1,T2,T4,T6,T12의 real 경로 |
| G2 — hotspot/domain | case·지름별 mask 전달 형식 및 전극/실제 layer/공정 ID, pitch·origin·valid, 2×2 여부와 한 unit 면적을 얻는 **현재** 방식, 기대 case/높이 N/A 판정 정보 | 원본 binary reader, full-mask union, explicit-domain 합성 실험, 미확정 사유 출력 | 임의 `/4` 또는 crop 확정, 실제 unit/panel 완료 선언; T1~T6,T12. 지도도 실자료 격자·case coverage 확인이 필요 |
| G3 — 저장소 이름 | DL1/DL2를 별도 두 repo로 두는 안 및 DL1 기존 이름 유지/DL2 경로·이름 확인 | 본 계획과 파일 교환 계약 초안; 기존 DL1의 명확한 로컬 버그 수정 범위 검토 | DL2 repo 생성·rename·remote/push; T8~T12. 이름 확정은 push 허가가 아님 |
| G4 — DL2 물리 문맥 | near/wide 창 크기(um), 비인접 vertical pair에서 중간 layer 포함 여부 | 창을 매개변수로 하는 crop/좌표/valid 테스트, 원본 layer 보존 스키마 초안 | 실자료 기본 창·채널 정책·학습 recipe 확정; T10,T11,T12. simulator의 중간층 제거 이미지를 원본 DL 입력으로 대체하지 않음 |
| G5 — DL1 입력 제안 | 메모리에 ‘권고·미확정’인 whole-unit isotropic resize+pad 및 electrode×layer set 집계 승인, 실제 최소 선폭/격자 기준, CNT/VIA 양 끝 연결 메타데이터 형식 | 현재 코드의 overlap/검증 결함 수정 설계, 복수 grid의 합성 비교, 원본 연결 정보 보존 계약 | Set Transformer/3채널을 확정 사용자 설계로 표시, 실자료 grid·안전한 augmentation 확정; T8,T9,T12 |
| G6 — 폐쇄망 수락 기준 | 사용자가 제공 가능한 lineage/family split, 제품/ROI 대응 labels 형식과 미지·negative 정책, 목표 Recall@k·누락 허용치·자원/시간 예산, 기존 line/pixel 값의 검산 사례·허용오차 | 평가 harness·빈 결과 양식·누수 검사·baseline·실행 manifest 제공 | 실제 우수성/연결성 검색 성능/기존 수치 일치 PASS; T9,T11,T12 |

G7은 두 독립 답변을 기록하며 묶어서 자동 동의 처리하지 않는다.

| Gate | 질문 / 필요한 증거 | 답변 전 가능한 일 | 답변 전 불가능한 일 / 영향 Task |
|---|---|---|---|
| G7a — 종류별 조각 지표 | 합의된 lateral-only/vertical-only/simultaneous 지표는 첫 구현에서는 제외하고 case의 종류만 보존하는 안에 동의하는가? | T1/T2의 원천 유형 보존, T3의 기존 line/pixel·pair 몫 계산과 유형 추적 시험 | 종류별 지표 구현·제외 확정 또는 기존 합의 철회 선언; T1/T3/T6/T12. 포함 요청이면 범위 재채택 |
| G7b — 지도의 open | line/pixel 수치의 shorts-only와 별개로, 첫 전극 수 지도도 shorts-only로 제한할 것인가, 기록대로 open 한 전극도 포함할 것인가? | T5의 short-only 합성 계산·coverage, T6의 조건부 지도/blocked ROI export, 수동 ROI 경로 | open fixture/입력 의미의 최종 삭제, 전체 지도·지도 유래 ready ROI의 실자료 수락; T1/T2/T5/T6/T10/T12. open 포함 요청이면 지도 경로만 재계획하고 line/pixel exclusion은 유지 |

G7a/G7b의 총칭은 **G7**이다. G7a의 제외 확인과 G7b의 shorts-only 확인은 본 계획의 권고이지 받은 답변이 아니다. G5 역시 기존 권고의 확인 게이트이며 겹침 버그 수정·검증기 등 답변 비의존 작업은 T8에서 계속 가능하다.

경계 보정과 수율 변환은 게이트 답변을 재촉해 구현할 후속 Task가 아니다. G2 답변이 없으면 미확정 상태로 인도할 수 있으며, 인도 완료와 실자료 수락 완료를 별도 기록한다.

## 4. 계획 없이 작성된 후보의 파일별 판정

다음은 채택을 위한 자문 판정이며 독립 code-review PASS가 아니다. `수정 후 채택`은 표의 수정과 해당 Task 수락 전까지 미승인이라는 뜻이다.

### 4.1 simcompare 후보 (`8f6eef9`, `d7eb11e`)

| 파일 | 판정 | 읽은 근거와 필수 수정 | Task |
|---|---|---|---|
| `src/simcompare/linepixel.py` | **수정 후 채택** | OR, line priority, exclusive/shared, B−A 방향은 목적과 맞음. `units_in_mask`가 2×2 `/4`를 기정사실화하고 density 단위를 호출자에게 위임함. `astype(bool)`는 NaN·음수·확률도 활성화하고 finite/비음수/positive 지름 검사가 없음. shape만 비교하며 pitch/origin/valid/원천 조건을 모름. `compare_rates`는 누락 layer를 0으로 만들고 unmapped 불완전성을 전파하지 않음. `unit_count` 길이/finite 검증 및 `+1e-9` floor 오차 수정. 62-pair 상한을 조합 수가 늘어도 동작하는 chunked membership 방식으로 교체하거나 명시된 제한으로 거부하는 계약 필요; 본 계획은 제한 없는 정확한 집합 계수 제안. 원천 case/상태/단위 계약에 연결 | T2~T4 |
| `tests/test_linepixel.py` | **수정 후 채택** | 작은 손계산 union·몫·별도 panel 배율 사례는 보존. 2×2 `/4` 테스트는 승인된 예시 정책 fixture로 바꾸고 미확정 거부 사례 추가. lateral/vertical 테스트는 실제 유형·상부 layer가 없는 동일 pair OR만 검증하므로 loader 통합 테스트 추가. 불완전 비교, 63개 pair, 삼중 shared, NaN, 단위·frame·32조건 coverage 반례 보강 | T2~T4 |
| `src/simcompare/electrode_map.py` | **수정 후 채택** | distinct electrode OR 및 layer별 max는 맞음. open 입력 계약 제거/명시 거부는 G7b에서 short-only를 확인한 뒤에만 적용하며 답변 전에는 기존 open 의미를 삭제하지 않음. 2D·finite·빈 전극·대표 지름 검사 필요. `Region`에 조합 목록/원본 조합별 CA/물리 bbox·면적·coverage가 없음. `find_regions`의 기본 4-neighbor 연결을 명시하고 valid를 보존. summary 전 layer frame 일치 확인. scipy를 직접 쓰는데 직접 dependency 선언이 없으므로 T5/T7에서 처리 | T2,T5,T7 |
| `tests/test_electrode_map.py` | **수정 후 채택** | 서로 다른 전극 수와 max 테스트는 유효. G7b의 short-only 확인 시 open 한 전극 fixture를 short-only + open 거부로 변경; 답변 전에는 해당 fixture를 보존하고 short-only 부분 경로 시험을 추가. 중복 조합·원점 불일치·5 um 누락/N/A·diagonal connectivity·지역 case/CA·valid hole 테스트 추가 | T5 |

### 4.2 DL1 D0의 추적 파일 전체

| 파일 | 판정 | 근거와 변경 | Task |
|---|---|---|---|
| `.gitignore` | **그대로 채택** | venv/cache/run/data/weights 제외는 폐쇄망 원본과 생성물을 Git에 넣지 않는 목적에 맞음. 실제 ignore 목록을 읽음 | T8 |
| `src/psdl1/__init__.py` | **그대로 채택** | 제품 크기를 넘어 형상/연결이 유사한 제품 검색이라는 목적 설명만 존재 | T8 |
| `pyproject.toml` | **수정 후 채택** | 작은 numpy/torch 기반과 선택 timm은 보존. 재현 가능한 잠금/버전 기록, 신규 CLI/test 의존성 명시; 검증 없이 dependency 최신화를 목표로 삼지 않음 | T8,T9 |
| `README.md` | **수정 후 채택** | 합성 검증 한계와 파일 weights 방향은 유효. 선택 전인 DL1 입력을 확정 기능처럼 설명하지 않도록 수정. ‘grid-size check’는 helper 안내일 뿐 train에서 자동 gate가 아님을 명확히 하고 실제 validator 문서화. CNT 영상만으로 검증된 연결성을 보장하지 않음. 가중치 license의 추정 문구는 특정 반입 artifact·license 확인 기록으로 대체 | T8,T9 |
| `src/psdl1/data.py` | **재작성** | `total=clip(sum(norm)); rest=clip(total-n)`은 n=1과 다른 전극=1의 겹침에서 rest=0이 되어 다른 배선을 지움. 하나의 전역 CNT, layer를 지운 rest만으로 연결 양 끝·관측 mask를 표현 못 함. `arr>0`은 잘못된 음수/NaN을 조용히 배경 처리; 상대경로 문서와 달리 root 내부 절대경로는 허용. CNT shape/2D/빈 ID·중복 role-layer·단위 크기 검사가 미흡. area resize helper와 공통 transform 원리는 재사용하되 schema와 tensor 생성 경로 재설계, rest는 자기 원소 제외 후 합성, valid/CNT endpoint 보존. `narrowest_run_px`는 빈 mask에도 양수를 반환하고 대각선/형상 끝에서 실제 선폭과 다르므로 보조 경고 이상으로 해석 금지 | T8 |
| `src/psdl1/model.py` | **수정 후 채택** | shared encoder+결합된 role/layer condition+set pooling을 G5 승인 시 후보로 보존. CNT endpoint/valid를 받을 입력 변경, global average 이후 연결성 소실을 baseline·반례로 평가. 학습 BatchNorm이 항상 collapse를 막는다는 주석은 증거 없음. `timm` file overlay는 network 차단 시험·파일 존재/format 검사 필요. shape/dim/head/전부 padding 입력 검증 | T9 |
| `src/psdl1/losses.py` | **수정 후 채택** | UNKNOWN을 분자·분모에서 제외하고 usable row 없는 상태를 구별하는 것은 적절. 동시에 similar/different인 pair는 현재 similar가 우선하므로 충돌 거부 필요. temperature 양수/유한, matrix shape/value/대칭, finite embedding 검증; positive-only 행의 제외 수와 실제 사용 pair coverage를 기록 | T9 |
| `src/psdl1/train.py` | **재작성** | 모든 제품으로 vocab/train, lineage split/eval/resume/device·효과적 batch/관계 coverage 기록 없음. sparse labels의 negative가 다른 batch에 있으면 데이터에 있어도 실패 가능. zero epochs 등 인자 검사와 checkpoint optimizer/RNG/버전/hash가 없음. 공통 augmentation은 좋은 출발이나 crop이 유일한 연결 bridge를 자른 view를 동일제품 positive로 강제할 수 있음. 데이터 계약·분리 평가·관계-aware sampler·중단 복구 중심으로 재작성 | T9 |
| `src/psdl1/search.py` | **수정 후 채택** | weights-only checkpoint 읽기, eval/no_grad, product 단위 cosine 순위는 보존. 한 번에 모든 후보를 encode하여 메모리 확장 한계, 형식 version/fingerprint 검사 없음. 독립 query+candidate 입력, batch index/cache, stable tie, topk 검증, 후보 없음과 unknown vocab의 명시 사유, JSON 근거/coverage 필드 추가 | T9 |
| `tests/conftest.py` | **수정 후 채택** | bus/comb/ring의 크기 변형 fixture는 재사용. 현재 모든 family의 CNT가 같은 위치라 연결성 반례 없음. 동일 XY·다른 endpoint/role-layer binding, bridge 삭제, valid hole, family/lineage fixture 추가 | T8,T9 |
| `tests/test_data.py` | **수정 후 채택** | aspect/area averaging·공통 augmentation·경로 탈출 시험은 보존. rest 채널 테스트가 ‘자기와 덜 겹침’만 확인해 실제 다른 전극 겹침 삭제를 놓침. 위 data 결함의 정확한 pixel oracle과 입력 거부 추가 | T8 |
| `tests/test_model_and_training.py` | **수정 후 채택** | 순서 불변/padding/UNKNOWN 손실 테스트는 유효. 학습 family에서 크기만 바꾼 성공은 held-out family 성능 증거가 아님. 순수 계산·checkpoint·검색·선택적 합성 학습 테스트를 분리하고 network 요청 즉시 실패 fixture, relation sampler/재개/누수 반례 추가. planner/기본 비학습 검사에서는 학습 테스트 실행 금지 | T9 |

## 5. archive에서 가져올 것과 폐기할 것

A0 전체를 복원하거나 그때의 리뷰 PASS를 새 후보에 재사용하지 않는다. 필요한 함수와 반례를 현재 계약으로 이식한다. **이식 대상은 새 `numeric_schema.py`, `numeric_images.py`, `measurement.py`, `adapters/numeric.py`이며 legacy `schema.py`, `preprocess.py`, `adapters/example.py`를 T2에서 수정하지 않는다.** 새 경로는 이들 legacy 모듈에 runtime import로 의존하지 않는다. T7은 남은 legacy 참조를 확인해 유지/폐기하므로 T2 수정물을 다시 삭제하는 일이 없다.

| 근거 파일/기능 | 결정 |
|---|---|
| `adapters/example.py::read_image`, binary 입력 관련 archive 테스트 | 정수 NPY 0/255와 PNG 0/255→0/1 fix 이식. nominal만 union 허용; `processed_intensity`·probability를 bool로 바꾸지 않음. finite·빈 배열 검사 추가. 원본 binary CA = active pixel×pitch² 확인 |
| `adapters/example.py::_resolve`, `schema.py::group_size_records`, 단위 변환 | 절대/탈출/symlink 경로 거부, 중복 지름·unit family 혼합 거부를 이식. 구 schema의 legacy dimensionless/role-layer 집합 분리 등은 새 경로에 확장하지 않음. line/pixel의 open은 제외하고 지도 open 처리는 G7b에 따름 |
| `regional_measurement.py::{geometry_um,measure_union,measure_window}`, `regional_schema.py`의 최소 타입 | 원본 좌표·valid·fractional cell clipping·겹친 창 union·보존 잔차 로직만 T2에 축소 이식. 기존 correspondence/변경영역 파이프라인 전체 부활 금지 |
| `adapters/regional.py::{scalar_observation,source_frame_reason}` 및 관련 테스트 | missing과 0, 단위·shape뿐 아니라 origin 비교하는 원리를 이식. 전체 legacy export 계약과 위험 mean의 union 해석은 가져오지 않음 |
| `adapters/example.py::_load_processed_fab_measurements` | parsing/중복·빈값·분포 내부 일관성 검사만 후보. 이 reader는 glass 평균 count와 ±0.5 bin을 만들므로 새 defect density의 근거가 아님. G1 전에 그대로 적용하지 않음 |
| `synthetic.py` | deterministic seed·provenance·다제품 생성 구조만 보존. 현재 `_hotspot`은 blur한 intensity라 binary union의 손계산 oracle로 사용 불가. 새 synthetic은 명시 binary masks·단위·32 지름·예외 상태와 사람이 정한 정답 사용 |
| AE/model/train/retrieval, avoidance variants, old cards/plots, CAA placeholder | C repo 실행 경로에서 폐기. DL1/DL2에 risk-input AE를 복사하지 않음. T7에서 import·CLI·scripts·config·문서·테스트를 함께 정리 |

## 6. Task 실행 공통 계약

각 Task는 아래 Target에 적힌 파일만 쓴다. 파일이 추가로 필요하면 worker가 범위를 넓히지 않고 coordinator가 계약을 갱신한다. 아래의 `tests/test_*.py` 이름은 생성할 구체 수락 파일이다. 각 Task의 **Baseline**은 코드 출발 C0/D0/N0 및 현재 계획 기준 C1과 함께 실제 dispatch 당시 accepted predecessor commit SHA 및 tracked/staged/untracked 상태를 다시 적는다. 이 문서 외 미승인 기존 변경이 있으면 보존하고 소유자를 먼저 확인한다. 현재 알려진 새 변경은 이 계획서 하나뿐이다.

구현은 채택된 계약으로 Sol medium, 구체 reasoning 한계가 확인될 때만 high를 쓴다. Opus가 계획을 high로 검토하고 최종 검증한다. 저장소당 편집 worker는 최대 1명이며 이번 Run은 **전체 활성 editor도 1명**으로 직렬 배치한다. 추가 read-only 슬롯 1개만 사용하고 Astra planning과 fresh Opus review가 공유한다. Task DAG의 무관한 작업을 선후 관계로 위장하지 않는다. editor 직렬 배치는 자원 제약이지 논리 의존성이 아니다.

수락은 Task별 명시한 oracle, 오류 상태, 출력 artifact 확인으로 판단한다. 아래 `python -m pytest`는 해당 repo에 설치된 interpreter로 실행하며 후속 구현에서 정확한 실행 환경을 기록한다. 수치 입력이 바뀌는 Task는 finite/음수/단위/coverage 반례를 확인한다. 합성 시험 통과는 실자료 성능을 뜻하지 않는다.

### T1 — 수치 입력·출력 계약 확정

- **Target:** C / `rewrite/line-pixel-rate`; `docs/data_contract.md`, `docs/open_questions.md`, `docs/domain-input-example/README.md`, 새 `docs/domain-input-example/numeric_v2/{manifest.json,pairs.csv,density.csv,README.md}`. 코드는 쓰지 않는다.
- **Change:** 기존 1.x/CAA/AE 계약과 구별되는 `numeric_v2` 초안 및 G1/G2 답변에 따른 확정 부분, source case/pair/role-layer·frame·N/A·density·unit-domain·출력 상태 스키마와 32 지름 coverage 예시를 제공한다. raw IDs/표 버전/입력 hash와 case별 `failure_type`을 보존한다. G7의 두 범위 결정을 별도 pending 항목으로 기록하고 ROI 교환 계약은 §2.4를 명시한다. legacy raw pair key를 분해하거나 정렬해 새로운 electrode pair로 오인하지 않는다.
- **Constraints:** 원천 형식과 내부 canonical 형식을 구분; 1.2 source-step assignment를 상부 layer로 조용히 재해석하지 않는다. 충돌 시 migration에 명시 오류와 근거를 요구한다. 기존 legacy 입력은 명시 변환 또는 unsupported로 처리할 방침을 문서화한다.
- **Ownership:** 위 문서·합성 예시만. 실자료 없음, 구현 memory와 archive 수정 금지.
- **Acceptance:** Opus가 §2 및 G1/G2의 각 입력·단위·state를 문서 예시와 일대일 대조; 관측 zero/누락/N/A 각각 별도 예시, panel pending 예시, pair orientation을 바꿔도 분류 동일하나 실제 layer binding은 보존되는 예시 확인. `python -m json.tool docs/domain-input-example/numeric_v2/manifest.json`으로 문법 확인, 모든 질문이 answered 또는 pending으로 남는다.
- **Baseline:** C0 + 채택된 본 계획 commit; 알려진 미승인 코드 후보는 §4의 상태 유지.
- **의존/게이트:** 계획 채택만 선행. G1/G2 미답변 시 typed 초안 및 pending 예시까지 가능, 실자료 계약 확정 불가. G7 미답변 시 종류별 조각 지표 제외·지도 open 정책의 최종 확정은 불가. T2~T6는 승인된 초안으로 합성 경로만 먼저 개발 가능.
- **독립 검토:** 필요(공개 schema·수치 의미). **반례:** `density=10 count/glass`를 `10/um²`로 읽으면 shape만 동일한 schema 검사가 통과할 수 있다. 단위·조사면적·bin 의미 없이는 가중값 불가임을 예시에 명시한다.

### T2 — 검증된 입력·원본 면적 측정

- **Target:** C / 동일 branch; `src/simcompare/{numeric_schema.py,numeric_images.py,measurement.py}`, `src/simcompare/adapters/numeric.py`, `tests/{test_numeric_schema.py,test_numeric_images.py,test_numeric_inputs.py,test_measurement.py}`, `tests/fixtures/numeric_v2/`.
- **Change:** 새 schema/단위 변환/오류 타입·binary image reader·loader를 기존 AE 경로와 독립 작성; 원천 `failure_type`과 case identity를 손실 없이 보존하고 binary mask, case×지름, pair 표, layer/height/원천 조건, Fab raw 측정, panel/unit 치수와 상태를 읽는다. 0/255 fix·안전 경로·중복·단위 검사 및 frame/valid를 이식한다. full-mask CA와 명시한 물리 창 CA/valid 면적을 원본에서 계산한다.
- **Constraints:** 확률 mask의 주변합으로 joint union을 추정하지 않음; 입력은 resize/threshold/수율 변환하지 않음. 실제 N/A에는 source 근거 필요. duplicate case+지름은 거부하지만 서로 다른 case가 같은 pair인 것은 유지. G1/G2 unknown은 오류/상태로 보존한다.
- **Ownership:** 위 파일과 합성 fixture만; `particle-sim` 원 저장소/exporter 및 archive에 쓰지 않음. legacy `schema.py`, `preprocess.py`, `adapters/example.py`는 읽기 전용이며 이들 runtime import도 새 모듈에서 금지.
- **Acceptance:** `python -m pytest tests/test_numeric_schema.py tests/test_numeric_images.py tests/test_numeric_inputs.py tests/test_measurement.py`; PNG·NPY bool·NPY 0/1·NPY integer 0/255가 동일 CA, 상대 path/symlink 탈출과 절대 path 거부, NaN·negative·3D·fractional nominal·중복 변환 지름 거부를 확인한다. grid 같아도 origin/pitch가 다르면 union 불가. source CA가 제공되면 동일 domain 보존 오차를 보고하며 강제 rescale하지 않는다. import 참조 검사로 새 numeric 모듈이 legacy schema/preprocess/example에 의존하지 않음을 확인한다. 같은 pair의 lateral/vertical source case 두 행은 유형을 유지한다.
- **Baseline:** C0에서 T1 accepted 계약과 실제 dispatch SHA; 다른 활성 편집 없음.
- **의존/게이트:** T1 초안 승인. G1/G2 전에 canonical 합성 loader/면적만 가능; real adapter·unit 환산은 답변 전 수락 보류. G7 전 lateral/vertical 유형 보존은 가능하고 지도 open 정책을 새 schema의 확정 기본값으로 고정할 수 없음.
- **독립 검토:** 필요(입력·path 보안·schema·계산). **반례:** pitch=0.5 um, 활성 셀 2개면 CA=0.5 um²다. 하나의 셀 안에서 겹치는 두 half-open ROI 면적을 단순 합산하면 중복되므로 손계산 union 면적과 비교한다. 관측 밖 창과 관측된 all-zero 창은 둘 다 합계 0이지만 전자는 unavailable, 후자는 available 0이어야 한다.

### T3 — 지름별 union과 전극 쌍 몫

- **Target:** C / 동일 branch; `src/simcompare/linepixel.py`, `tests/test_linepixel.py`.
- **Change:** T2 canonical 입력으로 §2의 L/P/T/U area 및 exclusive/shared bucket을 산출하고 원천 case CA·pair membership·coverage를 연결한다. same pair 중복 mask는 OR하되 원천 case별 lateral/vertical 표시는 유지하고, 서로 다른 process/지름은 분리한다. 종류별 조각 지표 자체는 §2.3/G7a에 따른 v1 제외 제안이다. 62-pair 구현 제한 제거와 안정적인 bucket 직렬화 키를 제공한다.
- **Constraints:** density·panel 환산은 T4가 소유; 값 우선순위/게이트를 숨기지 않음. shared를 균등분할하거나 전극별 단독 인과로 표현 금지. 제품 간 격자는 달라도 됨.
- **Ownership:** 해당 코드/시험 2개 파일만; T4가 같은 파일을 쓰므로 T3 편집이 끝난 뒤 T4를 dispatch한다.
- **Acceptance:** `python -m pytest tests/test_linepixel.py -k 'union or share or priority or coverage or membership'`; `area(U)=area(L)+area(P_effective)`와 exclusive+shared 보존을 독립 작은 set oracle로 확인. permutation·중복 pair·서로 다른 지름/step·63 distinct pair·3-way overlap 모두 검사한다. 동일 pair의 lateral/vertical 두 case를 OR해도 source 유형 두 값은 남고, `shared`를 종류별 `simultaneous`로 이름 바꾸지 않는 serialization 시험을 포함한다.
- **Baseline:** C0 + T2 accepted SHA(실자료 게이트 미해결이면 합성 부분 수락 기록 포함).
- **의존/게이트:** T2. G2 실자료 부분만 차단; 주어진 canonical domain의 순수 union 구현은 가능. G7a 전에 유형 보존과 line/pixel 계산은 가능하지만 종류별 지표 구현·제외 확정은 불가. 높이 정보 없는 누락 vertical 결과를 N/A로 만들 수 없음.
- **독립 검토:** 필요(core 계산). **반례:** 한 줄에서 line AB={0,1,2}, line AC={2,3}, pixel DE={1,3,4}, pixel EF={4,5}, pitch=1. 기대 line=4, pixel=2, transfer=2, union=6; line exclusive AB=2/AC=1/shared{AB,AC}=1, pixel exclusive EF=1/shared{DE,EF}=1. 단순 CA 합산·pixel union만 검사하면 line=5/pixel=4 또는 shared 중복을 놓친다. 63개 pair가 같은 한 셀을 덮을 때 union=1/shared bucket 하나여야 한다.

### T4 — density 가중·panel 확장·B−A

- **Target:** C / 동일 branch; `src/simcompare/{linepixel.py,density.py}`, `tests/{test_linepixel.py,test_panel_comparison.py,test_density.py}`.
- **Change:** G1 계약을 명시한 density adapter, 지름·layer별 두 가중값, G2 확인 시 unit→panel 배율, target−reference, exclusive/shared delta 및 transfer 순변화를 산출한다. 미완료 입력은 값과 이유를 분리한다.
- **Constraints:** 총 density를 정규화하지 않고 line/pixel에 동일 적용. 32조건 미관측을 0 채우지 않음. below-height N/A는 해당 case만 제외하고 다른 short를 계산. layer가 비교 상대에 없을 때 observed absent와 unknown을 구별한다. floor 기본/half-up round 선택 기록. yield·경계 모델 금지.
- **Ownership:** 위 파일만; G1 답변을 새 가정으로 덮지 않는다.
- **Acceptance:** `python -m pytest tests/test_density.py tests/test_panel_comparison.py tests/test_linepixel.py`; density unit conversion, 대소 bin coverage, panel 치수 길이=2/finite/positive·단위, 각 제품 별도 N, delta 부호와 몫 보존 확인. 출력값의 단위/분포 ID/면적 기준/rounding이 함께 있어야 한다. G1/G2 미해결 real 입력의 panel/delta는 null이고 area는 남는다.
- **Baseline:** C0 + T3 accepted SHA; T1 계약 최신 version/hash 포함.
- **의존/게이트:** T3; G1/G2 이전에는 명시 synthetic per-bin·one-unit oracle와 차단 상태만 구현·검증 가능. 실자료 가중값과 panel 수락은 답변 후.
- **독립 검토:** 필요(core 계산). **반례:** d=5 면적 2 um²와 density 0.5/um², d=7 면적 6 um²와 density 2/um²는 13이고 평균 5.2가 아니다. reference unit line=2,N=100; target=3,N=50이면 delta=-50이다. 공통 N을 쓰는 구현은 부호도 틀린다. panel/unit 비율 `(2.5,3.5)`이면 round N=12, floor N=6; bankers rounding 및 면적비를 한 번 floor하는 구현을 잡는다. 누락 density 한 bin과 관측 density=0 한 bin은 다른 상태다.

### T5 — 대표 지름 전극 수 지도·취약 영역

- **Target:** C / 동일 branch; `src/simcompare/electrode_map.py`, `tests/test_electrode_map.py`, `pyproject.toml`, `uv.lock`(scipy 직접 의존성이 필요할 경우에만).
- **Change:** 5 um에서 short hotspot이 덮는 distinct electrode를 layer별로 세고 제품 요약은 pixelwise max로 만든다. 4-neighbor 영역을 count→area→stable ID로 정렬; electrode/case 목록·조합별 CA·physical bbox·valid coverage를 붙인다. full-mask domain과 적용 defect-scope 정책을 출력한다. 원본 case ID·frame을 T6가 ROI 출처로 사용할 수 있게 내보낸다.
- **Constraints:** line/pixel pair 표나 density 없이도 지도 계산 가능; probability/온셋/전극 단독 인과로 표현하지 않음. 다른 layer frame이 호환되지 않으면 summary unavailable, 개별 layer는 보존. 누락 조합 때문에 낮아진 count를 완전값으로 표시하지 않음. open 거부는 G7b short-only 확인 후에만 확정; 답변 전 short-only 경로는 제한된 합성/부분 결과로 표시하고 기존 open 의미를 파괴하지 않음.
- **Ownership:** 위 파일만. 영역에 붙일 CA는 원본 case CA와 ROI CA를 이름으로 구별하며 T2 측정 함수를 사용한다.
- **Acceptance:** `python -m pytest tests/test_electrode_map.py`; 동일 electrode 중복 제거, max와 sum 차이, 대표 지름 변경/부재, 4-neighbor 대각선 분리, 좌표와 valid hole, bbox 끝 제외, case별 CA 연결을 확인한다. 설치 의존성에서 scipy import가 우연한 전이 의존에 기대지 않는다.
- **Baseline:** C0 + T2 accepted SHA 및 앞서 같은 repo에서 수락된 무관 Task 변경을 기록.
- **의존/게이트:** T2만(밀도/T4 불필요). G2 전 합성/full-domain 가능, real coverage 판정 보류. G7b 전 short-only 부분 계산·지도 provenance는 가능하나 전체 지도 수락 및 지도 유래 ready ROI 추천은 차단한다. open 포함 요청은 T5 입력·시험 계약을 재채택한다. N/A를 height 근거 없이 생성 금지.
- **독립 검토:** 필요(core map 계산). **반례:** 한 셀에서 AB, AC, 중복 BA를 덮으면 전극 수=3이지 case 수=3을 우연히 맞춘 것으로 충분하지 않다. 중복 BA를 하나 더 추가해도 3이어야 한다. 다른 layer가 CD로 2이면 summary=3이지 5도 전체 electrode union 4도 아니다. 고립 대각선 두 셀은 기본 연결 규칙에서 두 영역이다.

### T6 — 수치 CLI·리포트·검토 ROI 내보내기·합성 E2E

- **Target:** C / 동일 branch; `src/simcompare/{cli.py,pipeline.py,report.py,roi_export.py,synthetic.py,__main__.py}`, `configs/{demo.yaml,real.example.yaml}`, `scripts/{2_demo.sh,3_check_my_data.sh,4_analyze_my_data.sh}`, `tests/{test_numeric_cli.py,test_numeric_report.py,test_roi_export.py,test_synthetic.py}`, `tests/fixtures/numeric_v2/expected/`, 새 `docs/review_roi_contract.md`, `tests/fixtures/review_roi_v1/`.
- **Change:** 제안 명령 `simcompare validate-data`, `compare`, `electrode-map`, `demo-data`, `export-review-rois`를 연결한다. 제품 하나의 지도와 reference+target panel 비교를 별도로 실행한다. JSON/CSV 및 가벼운 HTML 보고서에 line/pixel·pair 공유 bucket·transfer·case CA·지름/coverage·지도·원본 좌표·계산 provenance를 제공한다. CLI에서 training을 호출하지 않는다. **C→DL2 방향은 이 Task가 소유한다:** T5 영역과 사용자 지정 위치만으로 §2.4의 `review_rois.json`을 생성하고 원본 shape/CNT/valid와 source identity를 연결한다. 이를 위한 최소 읽기 전용 asset manifest 참조 검증을 `roi_export.py`에 두며 새 수치 schema/이미지 reader를 재사용한다.
- **Constraints:** unavailable을 blank/0으로 숨기지 않고 JSON은 null+reason, CSV도 status/reason 열 유지. 서로 다른 제품의 좌표 overlay나 yield 표기를 만들지 않음. 입력 불가/부분 결과에 대한 exit-code 정책을 문서화하고 오류 때 완성 보고서로 보이게 쓰지 않음. 텍스트/ID를 HTML escape한다. 제품 간 구간 대응을 ROI 생성에 사용하지 않으며 hotspot/CA를 모델 입력으로 내보내지 않는다. manual 위치의 case/frame/원본 shape가 불명확하면 blocked로 기록하고 추정하지 않는다.
- **Ownership:** 위 파일만. old CLI 제거 자체는 T7에 맡기되 새 진입점에서 old train 호출은 금지.
- **Acceptance:** `python -m pytest tests/test_numeric_cli.py tests/test_numeric_report.py tests/test_roi_export.py tests/test_synthetic.py`; hand-authored expected fixture를 쓰는 subprocess E2E, 양 제품 grid가 달라도 panel 비교, 단독 지도, gate-pending 출력, 잘못된 입력 nonzero exit 확인. `simcompare demo-data --out <temp>/demo` 후 `simcompare compare --config <temp>/demo/config.yaml`의 결과를 expected JSON/CSV와 비교; expected 생성에 피시험 계산 함수를 사용하지 않는다. `export-review-rois`의 지도/수동 두 출처, multi-case 영역의 구별된 ROI ID, nonzero-origin 물리 좌표, relative-path/hash/valid, blocked 사유, source order 재배열 시 stable ID를 손으로 정한 fixture와 비교한다. G4 미답변이어도 crop 크기를 만들어내지 않고 위치 목록을 출력해야 한다.
- **Baseline:** C0 + T4/T5 accepted candidate SHA(합성 부분 수락과 실자료 미결은 구분).
- **의존/게이트:** T4,T5. G1/G2 전에 합성 E2E·pending report까지 가능, real 최종 panel 보고서 수락 불가. G7a 전 종류별 지표 제외 확정은 보류하며, G7b 전 지도 ROI는 `map_scope_unconfirmed`로 blocked; 수동 ROI는 별도의 완전한 원본 문맥이 있으면 export 가능. G4 전 near/wide 크기 없는 위치 계약은 확정 가능.
- **독립 검토:** 필요(공개 CLI/출력 schema, HTML 입력 처리, 수치 통합). **반례:** 한 pair 미매핑인 target과 완전한 reference를 비교하면 알려진 부분합끼리 그럴듯한 delta가 나오지만 최종 delta는 unavailable이어야 한다. shared bucket을 CSV pair별 반복행으로 출력한 뒤 report가 합산해 두 배로 만드는 오류도 총합 oracle로 검출한다. `</script>`를 포함한 ID는 실행되지 않고 문자로 표시되어야 한다. ROI 반례: origin=(100,200) um, pitch=0.5 um, bbox(row0=2,col0=4,row1=6,col1=8)는 bbox_um=(102,201,104,203), 중심=(103,202)다. 배열 index를 um로 직출력하는 구현을 잡는다. 같은 영역의 case A/B는 서로 다른 ROI 행으로 남고 case가 없는 manual 점은 자동 A 선택 없이 blocked여야 한다.

### T7 — 오래된 실행 경로·문서·의존성 폐기

- **Target:** C / 동일 branch. 삭제 후보는 `src/simcompare/{model.py,train.py,losses.py,dataset.py,retrieval.py,baseline.py,split.py,variants.py,cards.py,plots.py,caa.py,analysis.py,neighborhood.py,pair_geometry.py}`와 이에만 종속된 `tests/{test_analysis_cards.py,test_caa_contract.py,test_dataset_baseline.py,test_losses_contract.py,test_model_contract.py,test_pair_neighborhood.py,test_retrieval_contract.py,test_plots.py,test_split.py,test_split_contract.py,test_train.py,test_variants_merge.py}` 및 `docs/arch/{analysis.md,baseline.md,caa.md,cards.md,dataset.md,losses.md,model.md,neighborhood.md,pair_geometry.md,plots.md,retrieval.md,split.md,train.md,variants.md}`다. 수정은 `cli.py,pipeline.py,config.py,checks.py,merge.py,adapters/particle_sim_run.py`, `pyproject.toml`, `uv.lock`, `README.md`, `SPEC.md`, `docs/{usage.md,data_contract.md,model_design.md,open_questions.md}`, `scripts/{1_install.sh,2_demo.sh,3_check_my_data.sh,4_analyze_my_data.sh,5_check_particle_sim_run.sh,check_particle_sim_run.py}`, `configs/{demo.yaml,real.example.yaml}`, `tests/{conftest.py,test_cli_e2e.py,test_config_contract.py,test_domain_rules.py,test_review_findings.py,test_particle_sim_run.py,test_particle_sim_run_cli.py,test_particle_sim_export_integration.py}` 및 새 `tests/test_retired_paths.py`다. **dispatch 전에 coordinator가 `rg` 참조 분석 결과로 삭제/수정할 파일의 정확한 목록을 고정한다.** legacy `src/simcompare/{schema.py,preprocess.py}`, `src/simcompare/adapters/example.py`는 T2의 쓰기 대상이 아니며 T7의 참조 조사 대상이다. 유지할 legacy adapter가 참조하면 해당 용도로만 남기고, 참조가 모두 사라졌다면 삭제 목록에 명시한 뒤 제거한다. T2의 새 numeric 모듈 4개는 폐기 대상이 아니다.
- **Change:** 살아 있는 numeric CLI를 기준으로 obsolete AE/train/retrieval/avoidance/CAA/old cards/plots와 설정·문서·테스트를 제거한다. 필요한 입력 검사 helper는 T2의 새 numeric 모듈에 이미 이식된 것을 사용한다. 새 CLI의 `DataContractError`/단위/reader 참조도 새 numeric 경로로 옮겨 legacy import가 재유입되지 않게 한다. walkthrough PDF/HTML과 오래된 architecture 문서는 최신 설명에서 링크를 제거하고 legacy/archive라는 표시를 하며, 현재 기능 증거로 쓰지 않는다.
- **Constraints:** §5의 reader·단위·경로·Fab raw 자료 검사 보존. `merge`/particle-sim adapter의 실제 source/ID 보존 기능은 필요성 검토 없이 지우지 않는다. 새 numeric 경로 지원이 불명확하면 legacy 입력에 명시 오류와 migration 안내를 제공한다. archive branch, 기존 사용자 data, DL 저장소에는 쓰지 않음. 변경 위치 분석을 복원하지 않음.
- **Ownership:** 정확히 고정된 inventory만. author가 단순 ‘불필요해 보임’으로 범위를 늘릴 수 없음.
- **Acceptance:** `python -m pytest tests/test_retired_paths.py tests/test_numeric_cli.py tests/test_numeric_inputs.py`; `python -m simcompare --help`에 승인된 명령만 노출, 제거 명령은 분명한 오류. `rg`로 retired module import·old shell train 호출·stale active documentation 링크 없음 확인. DL 의존성 없는 새 환경에서 수치 CLI와 대표 E2E 실행; T5 사용 scipy는 직접 선언. 관련 old tests를 삭제해 수락을 회피하지 않고 대응하는 새 invariant test가 있는지 inventory 대조한다. `tests/test_numeric_schema.py`, `tests/test_numeric_images.py`, `tests/test_measurement.py`, `tests/test_roi_export.py`는 보존하고 numeric-only 설치에서 재검증한다.
- **Baseline:** C0 + T6 accepted SHA 및 inventory diff fingerprint.
- **의존/게이트:** T6(대체 경로 확보). G1/G2 답이 없어도 pending 상태를 지원하는 수치 CLI가 수락되면 cleanup 가능. 실자료 지원 완료 선언은 금지.
- **독립 검토:** 필요(공개 API 제거·migration). **수치 회귀 반례:** cleanup 뒤 0/255 loader fix가 사라지거나 전이 scipy 제거로 지도 import가 실패할 수 있다. T2의 0.5 um² oracle와 T5의 count=3 oracle를 numeric-only 설치에서 재실행한다.

### T8 — DL1 원본 형상·연결 입력 재설계

- **Target:** D / 기존 `main`에서 별도 구현 branch `rewrite/shape-connectivity-v1` 제안; `src/psdl1/data.py`, 새 `src/psdl1/schema.py`, `tests/{conftest.py,test_data.py,test_input_contract.py}`, `README.md`, `pyproject.toml`, 새 `docs/data_contract.md`, dependency lock 파일.
- **Change:** product/lineage/unit/frame, electrode×actual-layer 요소, valid, CNT/VIA endpoint 관계를 보존하는 versioned manifest와 validator. G5 승인 시 whole-unit normalize+pad 사용, 안전한 common transform, 자기 원소를 제외한 rest 생성, 최소 선/연결 bridge 보존 경고·gate를 제공한다. hotspot/CA/Fab 필드는 model tensor에 들어갈 수 없게 한다.
- **Constraints:** 실제 connectivity labels/geometry를 발명하지 않음. 연결 metadata 없으면 unavailable이지 CNN이 검증한 연결로 표시하지 않음. 다른 layer를 각자 중심정렬하지 않음. 이미 정해진 원본 shape-only 목적은 재논의하지 않음. pretrained/학습 불필요.
- **Ownership:** D의 위 파일만; C 파일 변경 금지. 두 repo 분리 및 이름은 G3에서 확인, 기존 D의 bounded fix는 coordinator 채택 후 가능.
- **Acceptance:** `python -m pytest tests/test_data.py tests/test_input_contract.py`; 동일 shape의 여러 unit 크기 normalize 오차를 수치 허용오차와 함께 검사, binary·finite·원점·unit·CNT 격자·endpoint·관측 누락·ID/경로 검증. 원소 순서 변경은 같은 representation이고 role-layer 결합 변경은 입력 identity가 달라야 한다. grid 검사 결과는 제품별 증거와 함께 저장한다.
- **Baseline:** D0, clean 확인 후 구현 branch 생성 SHA 기록. `data.py` 전체는 미승인 후보이며 재작성이다.
- **의존/게이트:** 계획 채택; T1/T2에 논리 의존하지 않는다. G3/G5 전 schema draft·겹침 fix·validator 합성 검증까지, 실제 tensor 계약/grid/연결 입력 확정은 불가.
- **독립 검토:** 필요(schema·공개 입력·path·geometry). **반례:** 두 전극이 같은 위치에 1이면 각자의 rest 채널도 그 위치에서 1이어야 한다. 현재 clip(sum)−self는 0이다. 동일 XY에서 role A/L1,B/L2를 A/L2,B/L1로 바꾼 제품은 metadata 집합은 같아도 binding이 달라야 한다. 유일한 1px bridge가 crop/축소로 사라지면 단순 면적 유사도 통과로 연결 보존을 선언하지 않는다.

### T9 — DL1 학습·검색·독립 평가와 복구

- **Target:** D / T8 branch; `src/psdl1/{model.py,losses.py,train.py,search.py}`, 새 `src/psdl1/{split.py,evaluate.py,checkpoint.py}`, `tests/{test_model_and_training.py,test_relations.py,test_split.py,test_checkpoint.py,test_search.py,test_offline.py}`, `README.md`, `docs/{training.md,validation.md}`, `configs/`.
- **Change:** G5 승인 모델 후보, label-aware loss/sampler, product/lineage 분리, 관계 coverage·unknown 처리, 작은 batch 입력 검증, checkpoint/resume 및 batch 검색/JSON 출력. training-only vocab/통계·split manifest·코드/입력/weights hash·seed·optimizer/RNG·유효 학습 pair 수를 저장한다. 단순 normalized image baseline과 비교하는 평가 CLI를 제공한다.
- **Constraints:** 무라벨 다른 제품을 자동 negative로 보는 것은 opt-in 실험이며 실제로 닮은 제품을 밀어낼 위험을 기록. family/lineage 분리는 사용자의 실제 ID 기준; resize variant/32조건/patch를 독립 제품으로 분리하지 않음. crop augmentation은 연결 변화가 positive로 학습되지 않도록 제한하거나 끄고 ablation. `pretrained=False` 기본, local file만 선택 가능, 실패 시 온라인 fallback 금지. 실제 학습은 사용자 폐쇄망에서 한다.
- **Ownership:** 위 D 파일만. 학습 실행은 이 계획·현재 planner의 권한에 포함되지 않음; 후속 단위시험은 직접 forward/loss/optimizer-state fixture로 구성하고 선택적 synthetic training smoke도 별도 실행 허가 범위를 명시한다.
- **Acceptance:** 비학습 수락 명령 `python -m pytest tests/test_relations.py tests/test_split.py tests/test_checkpoint.py tests/test_search.py tests/test_offline.py` 및 분리한 model forward/loss tests. UNKNOWN logit 변경이 loss에 영향 없음, conflicting relation 오류, 학습에 없던 role/layer 정책, sparse negative가 batch 밖이라 영원히 제외되지 않는 coverage 검사. network 호출을 실패시키는 test에서 tiny/local weights 로드; checkpoint roundtrip embedding 일치, incompatible schema 거부, RNG/optimizer 재개 상태 동일. 사용자용 `python -m psdl1.train ...`, `python -m psdl1.evaluate ...`, `python -m psdl1.search ...`는 완전한 config와 함께 T12에서 전달한다.
- **Baseline:** D0 + T8 accepted SHA; 재현 seed/환경과 candidate hash 기록.
- **의존/게이트:** T8. G5 전 최종 model input 확정 불가, G6 전 evaluation harness·합성 계약 검증까지 가능하나 real 성능 PASS 불가. G3 이름 변경 미결 시 기존 repo 내 승인 범위만.
- **독립 검토:** 필요(손실·검색 수치·schema·복구). **반례:** relation에서 unknown인 B의 유사도를 극단적으로 올려도 A의 loss가 변하지 않아야 한다. A-B가 similar와 different 양쪽에 있으면 순서에 따라 조용히 채택하지 않고 오류. 같은 lineage의 40/64/96 크기를 train/test에 나누면 크기 일반화처럼 보여도 누수이므로 split validation 실패. CNT endpoint만 바꾼 hard negative는 held-out 평가 항목으로 두되 랜덤 미학습 encoder의 순위로 성능을 검증하지 않는다.

### T10 — DL2 별도 repo·원본 ROI와 물리 창 계약

- **Target:** N / G3 승인 경로, 초기 branch `main` 제안; `pyproject.toml`, `.gitignore`, `README.md`, `docs/data_contract.md`, `src/psdl2/{__init__.py,schema.py,data.py,crop.py}`, `tests/{test_schema.py,test_crop.py,test_roi_import.py}`, `tests/fixtures/`.
- **Change:** T6가 내보낸 `review_roi_v1`의 ready 행을 읽고 한 review ROI(제품/설계/case/XY/um/frame/관여 전극·layer/연결/valid)로 near/wide 원본 다층 patch 묶음을 생성한다. producer version/asset hash/복합 identity를 검증하고 blocked 행은 reason을 보존하여 crop·학습·검색에서 제외한다. DB 후보 ROI도 같은 계약을 사용. 창은 실제 um 기준이며 resize가 필요해도 물리 pitch/공통 좌표 변환을 명시해 같은 물리 크기를 같은 grid에 담는다. 단일 ROI는 인접 문맥을 포함할 수 있지만 수정 전후 두 ROI를 요구하지 않는다.
- **Constraints:** G3 이전 repo 생성 금지. G4 전 실제 near/wide 기본값을 정하지 않고 synthetic config에만 예시 값을 쓴다. DL 입력은 simulator의 중간 layer 제거 hotspot이 아닌 원본 shape. 중간 layer 포함 정책은 metadata에 version으로 남기고 누락 관측과 빈 mask 구별. 정량 CA를 DL용 resample에서 측정하지 않음.
- **Ownership:** N의 위 파일만; C/D 수정 금지. dependency는 파일 교환 계약이며 C/D private Python module import 없음.
- **Acceptance:** `python -m pytest tests/test_schema.py tests/test_crop.py tests/test_roi_import.py`; source pixel→physical→crop 좌표 roundtrip, near/wide 같은 중심, 모든 layer 공통 transform, padding/valid, physical-unit 환산, 비인접 pair binding 및 intermediate policy 명시를 검증한다. package import와 CLI help 경로가 network 없이 가능. T6의 지도/수동 export fixture를 import해 같은 center·case·frame을 얻고, unknown schema·asset hash 불일치·blocked 행을 실행 후보로 승격하지 않는다.
- **Baseline:** N0; G3 후 경로가 비어 있는지 확인하고 초기 commit/hash 기록. 이미 파일이 있으면 덮지 않고 coordinator에 상태 보고.
- **의존/게이트:** 계획 채택+G3 및 §2.4 교환 계약. T8/T9 완료에 논리 의존하지 않음. T6와의 실제 producer-consumer 수락은 T6 export fixture가 선행하며 순수 crop 구현은 승인된 계약 fixture로 시작 가능. G4 전 parameterized crop와 합성 시험 가능, 실제 recipe 및 intermediate 채널 확정 불가. G7b 전 지도 출처는 blocked이고 수동 ready ROI 경로는 독립 검증 가능.
- **독립 검토:** 필요(schema·공개 입력·물리 crop). **반례:** 10 um 창은 pitch 0.5에서 20px, pitch 1에서 10px다. 둘 다 20px로 자르는 구현은 shape test에 통과하지만 물리 범위가 다르다. origin=(100,200)에서 ROI를 index로 곧바로 쓰는 오류와 한 layer만 별도 중심정렬하는 오류를 교차 layer 위치 oracle로 검출한다.

### T11 — DL2 무라벨 학습·국부 표현·주 비교군/추가 참고 검색

- **Target:** N / T10 branch; `src/psdl2/{model.py,losses.py,train.py,index.py,search.py,evaluate.py,checkpoint.py}`, `tests/{test_model.py,test_views.py,test_losses.py,test_search.py,test_split.py,test_checkpoint.py,test_offline.py}`, `configs/`, `docs/{training.md,validation.md}`, `README.md`.
- **Change:** 작은 다채널 2D shared encoder와 near/wide 결합 후보로 role-layer/CNT endpoint·valid를 보존한다. **기본 학습은 정답 없이 같은 위치의 두 crop을 가깝게 하는 §2.5 two-view 대조 목적**으로 구현한다. view 생성은 `train.py`에서 T10 crop을 호출하며 불완전/연결이 바뀐 view를 제외한다. 다른 위치를 자동 negative로 사용하지 않는 alignment+분산/covariance 규제를 기본으로 두고 ROI 대응 정답의 supervised relation 항은 선택 옵션으로만 추가한다. 사용자 ROI 하나→명시된 DL1 cohort의 case-compatible 검색, 필요 시 전체 DB `supplementary` 검색을 제공한다. ROI/source frame/case/coverage/score/검색 범위/no-match 이유 및 checkpoint/eval/resume를 제공한다.
- **Constraints:** **무라벨 시작은 사용자 결정**, 정확한 encoder/projection/loss 계수는 Opus가 채택할 구현 제안이다. 무라벨 데이터에 label 파일·different 쌍을 요구하거나 batch 내 다른 위치를 모두 negative로 만드는 fallback 금지. batch 분포 규제의 간접 영향은 별도 평가하며 false-negative 문제가 모두 해결됐다고 주장하지 않음. 선택 labels의 unknown은 supervised 항에서 제외하고 충돌은 오류. 모든 layer/view의 공통 좌표·핵심 연결 보존, lineage/위치별 split 필수. hotspot 예측 보조 학습은 v1 범위 밖이며 hotspot/CA/Fab을 model input 또는 auxiliary target으로 읽지 않음. 참고 제품 확대는 주 cohort를 바꾸지 않고 embedding은 공간 등록·CA 원인 증거가 아님. numeric 측정은 C 소유.
- **Ownership:** 위 N 파일만; D에서 private module을 무조건 import하지 않음. `train.py`의 view 생성 범위 밖에서 T10 crop 규칙 수정이 필요하면 계약 갱신. 실제 학습은 사용자 폐쇄망 실행이며 비학습 forward/loss/gradient·checkpoint fixture 검사를 기본 수락으로 한다.
- **Acceptance:** `python -m pytest tests/test_model.py tests/test_views.py tests/test_losses.py tests/test_search.py tests/test_split.py tests/test_checkpoint.py tests/test_offline.py`; labels 인자를 생략한 설정으로 view 생성과 finite loss/gradient가 성립하고 `labels_required` 오류가 없어야 한다. 위치가 같은 valid 두 view만 positive로 묶고 common core/bridge가 사라진 쌍은 거부한다. alignment·분산·covariance·선택 supervised 항을 따로 반환/검산하며 상수 embedding에서 collapse 규제가 양수, 유효 batch 부족은 명시 오류여야 한다. 무라벨 기본 loss에 cross-location negative pair 항이 없고 선택 relation의 unknown logit을 바꿔도 **supervised 항만** 불변임을 검사한다. mock/고정 embedding으로 cosine 순위·stable tie·no-match·primary/supplementary 검증; product lineage/겹친 ROI/증강의 split 누수 방지, offline checkpoint와 fingerprint 검사. `python -m psdl2.evaluate`는 labels가 있으면 Recall@k/반례 회수를 계산하고 없으면 정답 기반 지표를 unavailable로 남긴다.
- **Baseline:** N 초기 commit + T10의 coordinator 작업 검증 SHA; 독립 최종 수락은 §8 R4 candidate 기준.
- **의존/게이트:** T10; DL1 실제 실행/학습은 불필요하고 cohort fixture로 개발. G4/G6 전에 무라벨 기본 구조·합성 계약 검증 가능하나 실자료 augmentation recipe/성능 수락 불가. G6의 label 형식 미답변·label 부재는 기본 무라벨 학습 구현의 blocker가 아니다. 지도 출처 ready 여부는 T10의 G7b 상태를 따르고 수동 ROI 경로는 독립적이다.
- **독립 검토:** 필요(손실·검색·view·복구), R4에 포함. **반례:** 두 위치 A/B에 동일 shape·연결을 놓고 각 위치에서 두 crop을 만든다. naive instance InfoNCE는 A의 positive 외 B를 negative로 밀어내지만, 기본 경로에는 A-B repulsion 항이 없어야 한다. 테스트는 전체 loss 불변을 잘못 요구하지 않고 alignment의 정확한 대응 및 negative pair 수=0, 분포 규제의 별도 항을 검사한다. 반대로 모든 embedding이 같으면 alignment=0만 보고 성공시키지 않고 variance 규제가 양수여야 한다. labels가 없는데 known negative 없음으로 fail하는 DL1 방식 복사는 실패다. 유일한 연결 bridge를 한 crop에서 제거하면 같은 중심이어도 positive가 될 수 없다. primary={A}일 때 더 높은 cosine의 전체 DB B는 기본 결과에 나오지 않고 expanded 결과에서도 supplementary로만 남는다. 동일 XY지만 role/layer·CNT endpoint가 다른 후보는 동일 물리 사례로 승인하지 않으며 후보 없음은 위험 0이 아니다.

### T12 — 파일 교환 통합·폐쇄망 인수 패키지

- **Target:** C / rewrite branch; 새 `src/simcompare/dl_results.py`, `tests/{test_dl_results.py,test_handoff.py}`, `docs/handoff/{README.md,manifest.schema.json,validation-template.json,commands.md,troubleshooting.md}`, `scripts/{check_handoff.sh,validate_offline.sh}`. D/N에는 각각 `docs/handoff.md`와 `configs/closed-network.example.*`만 추가할 수 있으며 **repo별 subtask로 나눠 serial dispatch**한다.
- **Change:** 세 repo별 정확한 commit/dependency lock·파일 hash·schema/ID 버전·환경·weights 옵션·예제와 재현 명령을 묶는다. DL1 product 후보/근거, DL2 ROI/reference·scope 결과를 C에서 읽어 case/원본 CA/수치 결과로 연결하되 위험값을 embedding에 재입력하지 않는다. T6의 `review_rois.json` → T10 import/crop → T11 query/index → C의 결과 읽기까지 양방향 연결을 검증한다. DL2가 없거나 실자료 게이트가 미결이어도 수치 CLI 사용 경로와 상태를 명확히 제공한다.
- **Constraints:** 원본 실자료 반출 요구 없음, network 자동 download/API/유료 credit 없음, push 없음. pretrained 기본 제외; 사용자가 반입할 정확한 파일의 checksum/모델·입력/라이선스 증거가 있을 때만 optional 경로. 평가 숫자·정답·실행 성공을 미리 채우지 않는다. 서로 다른 repo를 하나의 동시 편집 worker가 제어하지 않는다.
- **Ownership:** 각 subtask의 한 repo 목록만 허용. 실제 bundle은 기존 artifact를 복사하고 manifest로 추적하며 live repo/기존 run을 덮지 않는다.
- **Acceptance:** `python -m pytest tests/test_dl_results.py tests/test_handoff.py` 및 `bash scripts/check_handoff.sh <bundle-dir>`; 세 commit/hash·설치 항목·입력 예제·실행 명령·예상 output·오류 복구·pending gate 목록이 모두 존재. network 차단 상태의 설치 후 import/help/fixture inference·수치 E2E 검증(의존성 배포는 허용 PyPI/내부 mirror 또는 준비된 wheel 경로). 지도/수동 두 ROI 출처에서 `roi_id`·case·원본 frame·asset hash가 왕복 보존되고 blocked ROI가 실행되지 않는 E2E fixture를 검사한다. DL2 기본 학습 예제는 labels 파일 없이 실행 가능해야 하고 supervised 예제는 별도 선택 파일로 제공한다. 작업자가 제공하는 것은 **실행 준비된 패키지**와 빈 수락 기록이다. 사용자는 아래 절차로 학습/검증하고 결과 evidence를 채운다.
- **Baseline:** C=T7, D=T9, N=T11의 정확한 accepted SHA 및 G3 확정 이름. repo마다 unrelated 변경 없음 확인.
- **의존/게이트:** 수치 인수 subtask는 T7, DL1은 T9, DL2는 T11; 최종 교환 통합만 세 결과 모두 필요. 문서 골격은 먼저 작성 가능. G1/G2/G4/G5/G6/G7 미결이면 해당 실자료 단계를 pending으로 인도하며 완료인 척하지 않는다. G7b 미결이 지도 유래 ROI 실행을 막는 것과 수동 ROI 사용 가능 여부를 구별하고, G7a 답변 상태는 인도 범위에 명시한다.
- **독립 검토:** 필요(공개 결과 schema·단위/ID join·package 실행 경로). 문서 오탈자만 후속 수정할 때는 생략 가능하나 candidate hash를 갱신한다.
- **반례:** DL2의 `case_id`가 제품마다 반복되는데 product/design 없이 join하면 A의 CA를 B의 CA로 붙일 수 있다. `(product,design,case,diameter,source/frame/version)` 일치 및 mismatch 거부를 검사한다. old weights와 새 channel schema가 shape상 우연히 맞아도 fingerprint 불일치로 거부해야 한다. export 뒤 query의 frame origin을 바꾼 결과를 원본 ROI와 같은 것으로 join하면 실패이며, multi-case 영역의 ROI 두 행이 하나로 사라지지 않아야 한다.

## 7. 네 단계 실행·보고와 실제 의존성

아래 **단계 순서**는 사용자에게 진행을 보고하는 운영 순서다. 코드의 실제 의존성과 구분하며 없는 Orca dependency를 만들지 않는다. 각 단계의 검증/검토 결과를 사용자에게 보고한 뒤 다음 단계에 들어간다. 보고 자체는 새 승인 질문이 아니며, 미답변 gate와 scope 변경에만 해당 승인 조건을 적용한다.

| 단계 | Task | 단계 종료 시 사용자 보고·다음 단계 조건 |
|---|---|---|
| 1단계 — 수치 | T1~T7 | R1 수치 계산·R2 CLI/리포트/폐기 결과, 손계산 반례, ROI export 두 출처, G1/G2/G7 답변·pending 상태를 보고. 실자료 미결이 있으면 완료 범위와 보류 Task를 남기고 2단계 진입을 보고 |
| 2단계 — DL1 | T8~T9 | R3 결과, 겹침·입력/연결 수정, 기본 학습/검색/평가 준비, G3/G5/G6 미결과 실제 성능 미검증을 보고한 뒤 3단계로 이동 |
| 3단계 — DL2 | T10~T11 | R4 결과, ROI import, 무라벨 기본·선택 labels, false-negative/collapse 반례, G4/G6/G7b 미결을 보고한 뒤 4단계로 이동 |
| 4단계 — 인수 | T12의 repo별 subtask와 교환 통합 | R5 결과, 세 repo의 exact candidate, 왕복 교환·offline 증거, 사용자 실자료 수락 절차와 pending을 인도 보고 |

단계 안의 사용자 답변 대기로 나머지 진행이 막히면 현재 단계의 **부분 검증/보류 보고**를 먼저 한다. 다음 단계의 답변 비의존 작업을 시작하는 이유와 미완료 범위를 명시하고 이동하며 이전 단계 완료로 기록하지 않는다. 보류 항목은 같은 Run의 원 Task/재채택 계약으로 이어간다. 독립 리뷰 blocking finding은 고친 후보를 재검토하기 전 그 결과를 최종 수락으로 표시하지 않는다.

논리 DAG:

```text
계획 채택 → T1 → T2 → T3 → T4 ─┐
                   └→ T5 ─────┴→ T6 → T7 → T12-수치
계획 채택 → T8 → T9 ──────────────────────→ T12-DL1
계획 채택 + G3 → T10 → T11 ───────────────→ T12-DL2
T6의 export 계약·fixture → T10의 실제 ROI import 수락
T12-수치 + T12-DL1 + T12-DL2 → T12-교환통합/인도
```

T1 승인된 초안은 T2 합성 경로의 충분한 선행조건이고 사용자 real gate 해결과 다르다. 각 역할(coordinator/planner/implementer/reviewer)의 경과시간, 첫 수락 여부, review finding, retry, 사용자 개입, 관측 가능한 subscription 사용량을 별도로 기록하며 관측할 수 없는 사용량은 unknown으로 둔다. T3/T4의 `linepixel.py` 공유는 순차 의존과도 일치한다. T5는 T3/T4에 의존하지 않지만 동일 repo 편집 슬롯 때문에 겹쳐 실행하지 않는다. T8과 T10의 제품/ROI 식별자·role-layer 의미는 coordinator가 계약 검토에서 맞추고, T11 개발은 DL1 cohort fixture를 써 T9 학습을 기다리지 않는다. T6→T10은 검토 ROI producer/consumer 계약의 실제 의존성이다. 순수 crop은 계약 fixture로 먼저 개발할 수 있으나 producer fixture를 받기 전 import 수락은 미완료다. T12는 실제 C→DL2→C와 DL1 interface version의 왕복을 대조한다.

권장 착수는 T1 초안과 사용자 질문 → T2 → T3 → T4 합성 → T5 → T6 → T7이다. 사용자 답변이 기다려지는 동안 위 단계 보고를 거쳐 편집 슬롯을 다음 단계의 답변 비의존 범위로 옮길 수 있다. 이 순서는 작업 편의를 위한 것이며 없는 dependency를 Orca에 만들지 않는다. gated Task의 부분 artifact 수락을 전체 성공으로 기록하지 말고, Task 계약이 부분 단계라면 그 완료 범위와 남은 real 단계의 Task ID를 명시한다.

## 8. Opus 계획 검토·독립 코드 검토

계획 채택은 active Opus coordinator가 high로 수행한다. 검토 항목은 요구사항 누락, 파일 소유권, 실제 dependency, unknown을 사실화한 부분, §6 반례, 수락 대상 candidate와 중단 복구다. Astra는 자문이며 coordinator 권한을 얻지 않는다.

**독립 Claude Opus 5.5 high 검토 필요:** T1~T6, T8~T12는 core 계산/schema/공개 입력/검색·학습 수치·복구 중 하나 이상에 해당한다. T7은 공개 API 폐기/migration 때문에 필요하다. 단순 문서 formatting만의 patch는 coordinator 판정으로 생략 가능하다. 독립 리뷰는 다음 **5묶음**으로 수행하며 Task별 중복 전체 리뷰를 만들지 않는다. 단, 이후 변경에 영향받은 PASS는 새 후보에 재사용하지 않는다.

| 검토 묶음 | Task·candidate | 검토 시점·주요 증거 |
|---|---|---|
| R1 — 수치 계산 | T2~T5를 하나의 C candidate로; T1 계약 문서 diff를 규범 근거로 함께 검토 | T2~T5 작업 검증 후 T6 본 통합 수락 전. 단위·union·shared·panel·지도·case 유형 보존·G7 범위, 신규 모듈의 legacy 비의존, T1~T5 반례 |
| R2 — CLI·리포트·폐기 | T6~T7를 하나의 C candidate로 | 1단계 종료 전. 수치 회귀·보고서 상태/escape·두 출처 ROI export·legacy 제거와 migration; R1 이후 변경된 계산면이 있으면 영향 검토도 포함 |
| R3 — DL1 | T8~T9를 하나의 D candidate로 | 2단계 종료 전. 원본 연결/overlap·split·loss/unknown·checkpoint·offline·G5 상태 |
| R4 — DL2 | T10~T11를 하나의 N candidate로 | 3단계 종료 전. T6 producer fixture·ROI 좌표, 무라벨 기본 손실/false-negative·collapse·view·선택 labels·검색 scope |
| R5 — 인수 | T12 전체의 C/D/N exact commit tuple와 bundle hash | 4단계 인도 전. repo별 serial subtask 결과·C→DL2→C 왕복·offline·문서 명령·모든 gate/pending evidence |

T1 초안은 coordinator가 high로 먼저 받아 T2의 합성 구현 계약으로 쓰며, schema에 필요한 **독립** 검토는 R1에서 T1 문서와 구현을 함께 대조한다. 묶음 안 선행 Task의 `accepted SHA`는 coordinator의 범위별 작업 검증/통합 기준을 뜻하며 독립 PASS를 대신하지 않는다. 독립 수락은 R1~R5 exact candidate로만 기록한다. 각 묶음은 별도 fresh `claude-opus-5-5 / high` 읽기 전용 session에 맡기고, author/coordinator의 자체 점검을 독립 PASS로 부르지 않는다. 묶음의 미답변 gate는 확인된 범위의 PASS와 실자료 pending을 구별하며 무응답을 승인으로 만들지 않는다.

fresh reviewer에게 다음을 제공한다:

1. 채택 PLAN version/hash와 해당 Task 계약, 답변된 사용자 gate의 원문·미답변 목록.
2. base/candidate SHA 또는 tracked/staged/untracked를 포함한 exact diff hash, 허용 file scope, 읽을 관련 호출부/시험.
3. 위 손계산 oracle·실제 실행 명령/환경/출력/실패·수정 기록; 합성/real/미실행을 구분한 증거.
4. 적용 단위·geometry·N/A/누락·shared 합산·unknown loss/split·checkpoint 호환성·network 차단 체크 대상.
5. reviewer에게 쓰기/학습/네트워크 다운로드를 허용하지 않는 읽기 전용 범위와 blocking finding 출력 형식.

구현 author나 계획을 수락한 coordinator가 독립 PASS를 대신하지 않는다. fresh `claude-opus-5-5`, effort high가 exact candidate를 검토하고 coordinator는 실제 diff·필수 명령으로 최종 수락한다. Claude 사용 불가면 이 검토는 pending이고 다른 모델 PASS로 대체하지 않는다. review 수정 후 영향 테스트와 필요한 검토만 갱신하며 이유 없는 full suite 반복은 하지 않는다.

## 9. 폐쇄망 사용자 실행·수락 절차

T12가 제공할 명령은 `...` 생략 없는 실제 config 경로/예제 입력으로 완성한다. 현재 계획의 module 이름은 목표 인터페이스이지 이미 존재하는 기능이라는 뜻이 아니다.

1. bundle manifest로 repo commit·lock·weights 선택·hash 확인 후 설치한다. PyPI/내부 mirror로 dependency를 준비할 수 있으나 실행 중 package 내부 model download는 금지한다. optional pretrained 파일이 없으면 from-scratch 경로를 쓴다. GPU 종류/메모리/시간은 G6의 사용자 환경 기록에 맡긴다.
2. 실자료 변환 후 validate를 실행한다. G1/G2 미해결, unknown pair, source assignment 충돌, missing/vertical N/A, frame·unit·density coverage를 해결하거나 pending으로 남긴다. density bin 40/50 또는 2×2 정책을 조용히 보정하지 않는다.
3. 기존 line/pixel 계산에서 확인 가능한 제품·layer·지름과 overlap 사례를 선택한다. 원 CA→union→priority→density→unit→panel 단계별 수치와 차이/사유를 비교한다. 검산 oracle와 허용오차는 G6로 고정하고 최종 합계만 맞는 우연한 상쇄를 통과시키지 않는다.
4. DL1 train/validation/test 제품 계열을 고정하고 raw/correspondence hash를 내부에 기록한다. 크기 비슷/다름 × 구조 유사/비유사, 연결만 다른 hard negative, layer/role 교환, 한 국부만 닮은 제품을 분리 집계한다. normalized-image baseline과 Recall@k, 제품 coverage, 실패 사례, 시간/메모리를 비교한다. reconstruction loss나 훈련 loss 하락만으로 채택하지 않는다.
5. DL2는 사용자 near/wide/intermediate 정책을 고정한 후 C에서 지도 또는 수동 위치의 `review_rois.json`을 내보내 검증한다. ready ROI의 원본 shape를 읽어 **labels 없이 같은 위치 두 view로 기본 학습**을 시작한다. ROI 대응 labels가 있으면 선택 supervised 실험 및 정답 기반 평가를 별도로 수행하며, labels가 없으면 Recall@k 등을 채우지 않고 전문가 검토·추후 평가 pending으로 둔다. primary cohort와 supplementary 검색을 따로 평가하고 no-match, physical pitch 차이, 동일 전체 silhouette·다른 연결, 희귀 고CA 사례의 누락을 확인한다. CA는 원본 후처리 평가 항목일 뿐 입력이나 검색 임베딩에 섞지 않는다.
6. `validation-template.json`에 candidate·계약·split·seed·명령·환경·평가 지표·오류·G1~G7 답변·판정/보류를 채운다. 설계 검토 시간과 baseline 대비 효과도 실제 관측값만 기록한다. 민감한 layout/labels를 외부로 보내지 않아도 내부 경로/hash와 비식별 결과로 추적 가능하게 한다.

인수 상태는 `코드/합성 수락`, `폐쇄망 실행 준비`, `사용자 실자료 수락` 세 가지로 나눈다. 마지막 상태의 승인 주체는 실자료를 보유한 사용자다. 첫 두 상태를 실제 성능 검증으로 표현하지 않는다.

## 10. 중단·재시도·checkpoint

- 각 dispatch 전 active Orca Run/Task/Dispatch, terminal authority, Git branch/HEAD/status, MMO 활성 작업을 coordinator가 확인한다. MMO가 편집 중이면 동일 repo에 Orca editor를 시작하지 않는다. 기존 MMO plan/hash·candidate 승인 상태를 새 Orca Task로 복제하지 않는다.
- meaningful boundary마다 계획 version, gate 답변, base/candidate SHA 또는 diff fingerprint, modified/untracked 목록과 소유자, 실행 명령·결과·미실행 항목, 다음 동작을 checkpoint로 남긴다. worker는 5분 heartbeat와 자연스러운 경계의 `check`, 질문은 `ask`, 결과는 Task/Dispatch가 모두 있는 `worker_done`을 한 번 보낸다.
- 중단 시 파일을 그대로 보존한다. coordinator가 실제 diff와 계약, reviewer finding, 테스트 증거를 확인한 뒤 같은 Task의 남은 범위를 재개한다. hard reset, 전체 checkout, global stash/pop, worktree 삭제, 기존 checkpoint 덮어쓰기로 ‘깨끗한 재시작’을 만들지 않는다.
- timeout/silence/contact loss는 사망 증거가 아니다. `worker-list`, `worker-show`, bounded `worker-read` 및 installed skill의 literal nextAction으로 liveness/권한을 확인한다. 명확히 failed/stopped된 Dispatch만 같은 Task에서 `--retry-of`로 재시도하며 duplicate editor를 만들지 않는다.
- local bug는 원 worker가 evidence와 함께 수정; 명세 결함은 Astra high 재계획→Opus high 재채택. 환경/의존성 문제는 환경 원인으로 처리한다. 구현 reasoning 한계의 구체 증거 없이 effort/provider를 바꾸지 않는다. 추가 유료/API 사용을 자동 활성화하지 않는다.
- 데이터 계약이나 모델 channel이 바뀌면 fingerprint와 artifact directory를 새로 만들고 이전 run/index/checkpoint를 보존한다. 학습 재개는 optimizer/RNG/split/입력 fingerprint가 일치하는 checkpoint에서만; `resume`와 새 seed의 fresh train을 구분한다. 반쯤 쓰인 output은 temporary 표시로 남기고 완료 manifest 없이 성공 artifact로 쓰지 않는다.
- worker launch/lifecycle은 설치된 Orca orchestration skill과 user policy가 유일한 절차다. version-pinned structured start race가 검증으로 해소되기 전에는 명시 terminal 절차를 사용하고 model/effort를 terminal 자체에서 확인한다. 이 계획서는 새로운 CLI 복구 명령을 발명하지 않는다. 기존 coordinator에게만 계획 검토/dispatch/final authority가 있다.

## 11. 우선순위 위험·열린 질문

| 순위 | 위험/미확정 | 대응·완료 증거 |
|---|---|---|
| P0 | Fab 단위·bin 폭 오해로 절대값이 수 배~대단위로 틀림 | G1 원자료 설명; T2/T4 단위·40/50 반례; 미답변 real 값 null |
| P0 | 2×2를 자동 `/4`해 현재 실무와 다른 panel 값 생성 | G2 현행 unit-domain 근거; boundary 계산은 보류 유지; T4 미확정 차단 |
| P0 | missing/N/A/unmapped를 0으로 처리해 개선처럼 보고 | case coverage와 상태 전파; T2/T4/T6 누락 target 반례 |
| P0 | 전극 identity/actual layer/공정 step 붕괴 또는 잘못된 원본 결과 join | 원천 case와 pair 분류 키 분리, 상부 layer 검증, T12 복합 ID 검사 |
| P1 | DL1 resize/rest/CNT 입력이 연결성 차이를 지움 | T8 rest oracle·valid/endpoint·bridge 반례, G5 grid와 표현 승인; 성능은 G6 held-out 평가 |
| P1 | 알려진 family/증강/ROI가 split 양쪽에 있어 성능 과장 | lineage split, 관계 coverage, family 크기 변화만의 현재 시험과 실제 평가 분리 |
| P1 | DL2 물리 창/중간 layer 미정 또는 국부 유사도를 제품 승인으로 오인 | G4, T10 physical crop, T11 primary/supplementary 분리, C의 case coverage 판정 |
| P1 | 62 pair 제한·full-resolution membership·전제품 동시 embedding의 메모리 비용 | T3 chunking exactness, T9/T11 batch indexing; G6 실제 해상도·제품수·자원 예산 후 benchmark 크기 결정. 성능 목표를 발명하지 않음 |
| P1 | 구 AE/CAA schema·source-step assignment가 새 상부-layer 실무와 혼재 | T1 명시 contract version/migration, T7 참조 inventory/실행 경로 폐기. archive는 보존 |
| P1 | G7 미답변을 종류별 합의 철회·지도 open 제외 승인으로 오인 | T3의 source 유형 보존, G7a/G7b 각각 확인, T5/T6 조건부 출력·지도 ROI blocked |
| P1 | DL2의 다른 위치 유사 구조를 negative로 밀거나 embedding 붕괴 | §2.5/T11 무음성쌍 기본·분포 규제·선택 label, 손실 항별 반례 및 실제 효과는 G6 평가 |
| P2 | pretrained 파일과 package 버전/라이선스·channel 호환성 불명 | optional 파일 경로·checksum·license 기록, network 차단 테스트; 모델별 라이선스 사실은 이 계획에서 추정하지 않음 |
| P2 | 사용자 검산 기준/실제 학습 효과 미확정 | G6 검증 양식과 baseline·지표 제공; 실자료 수락은 사용자에게 남김 |

남는 질문은 G1~G7에 모아 추적한다. 결정된 shape-only, line/pixel 수치의 shorts-only, line priority, panel 비교, 별도 DL 목적, 5 um 대표 지도와 폐쇄망 실행은 재개방하지 않는다. 보류된 수율/경계/변경위치 분석은 이 계획의 완료 조건에 추가하지 않는다.

## 12. 계획 수락 대조표

| PLAN_REQUEST 요구 | 이 문서의 근거 |
|---|---|
| 버전·범위·non-goals | 머리말 및 §1 |
| 모든 Task의 Target/Change/Constraints/Ownership/Acceptance/Baseline | §6 T1~T12 |
| 실제 dependency·편집자 제한 | §6 공통 계약, §7 DAG |
| 무계획 작성 코드 파일별 판정 | §4: C 후보 4개, D 추적 파일 12개 |
| independent Opus high 검토·reviewer 자료 | 각 Task의 검토 표시, §8 |
| user gate별 답변 전 가능/불가능 | §3, 각 Task 의존/게이트 |
| 각 수치 Task의 수락 반례 | T1~T12의 반례(계산 외 schema/cleanup/integration 회귀 포함) |
| 중단·재시도·부분 작업 보존 | §10 |
| 우선순위 위험·열린 질문 | §11, G1~G7 |
| 폐쇄망 인도와 실자료 검증 역할 | T12, §9 |

| PLAN_v1_REVIEW 추가 수락 | 해결 위치 |
|---|---|
| B1 — 무라벨 DL2 기본·false-negative 반례·hotspot 보조 제외 | §2.5, T11, §9 |
| B2 — ROI 생산자/출처/파일 내보내기 | §2.4, T6 생산/T10 소비/T12 왕복 |
| B3 — 종류별 지표 제외 제안·case 유형 보존·사용자 게이트 | §1/§2.3, T1/T2/T3/T6, G7a |
| A1 — 4단계·단계별 사용자 보고 | §7의 단계 표 |
| A2 — 5개 독립 검토 묶음 | §8 R1~R5 |
| A3 — 지도 open 미확정 | §2.1, G7b, T5/T6 |
| A4 — T2 legacy 수정 후 폐기 방지 | §5, T2 새 모듈만/T7 참조 조사 |
| A5 — G5 유지·독립 가능한 버그/검증 작업 | G5, T8/T9 |
