# PLAN_v2 보완 — DL2 view 연결성 v3

버전: 2026-10-09 / 자문 제안, Opus 채택 전 구현 금지. PLAN_v2 §2.5·T11의 모호한 bridge 수락 기준을 이 문서로 대체한다.
근거: R4_REVIEW B2, R4_REREVIEW B3/P1/M1~M8, DL2 `e03cb5c9cce7a36dfb73c0c53b2a9f99eb1448ba`의 `views.py`·관련 train/index 코드. 현재 HEAD는 C=`f5e8600`, DL2=`20a2ca8`이며 두 작업 트리는 작성 전 clean이었다. 이 작업은 이 문서만 쓰며 구현·학습·성능 측정은 하지 않는다.

## 1. 결정: 공동 그래프, 두 view끼리의 core 관계 보존

coordinator의 공동 그래프 제안을 채택한다. 정책 ID는 `common_core_connectivity_v3`이며 v2 checkpoint/index와 자동 호환시키지 않는다.
- 같은 원본 ROI·case·frame·asset·endpoint·원래 중심을 확인하고 **near끼리, wide끼리 각각** 판정한다. near와 wide의 연결 관계가 서로 같을 필요는 없다.
- core는 jitter 전 ROI 중심의 고정 half-open 물리 사각형이다. core와 양의 면적으로 겹치는 모든 원본 cell을 core cell로 삼는다. cell의 단순 접촉은 겹침이 아니다. core와 해당 cell의 source valid가 모두 관측되어야 하고 각 view에서 그 cell들이 모두 표현되어야 한다.
- 각 scale/view의 그래프 노드는 `(channel, cell)`이다. channel은 두 관여 `layout:*`와 `cnt`, 각각의 `via:*`; valid는 노드가 아닌 관측 마스크다. marked는 native binary의 1, invalid cell은 경로에 사용하지 않는다.
- native nearest는 **실제 patch 값과 exact source_indices**로 그래프를 만든다. 창의 실효 표본 범위만 사용하고 ROI 전체 component로 창 밖을 연결하지 않는다. cell 선택은 이미 고친 exact crop/tie 규칙을 유지한다.
- 같은 channel의 상하좌우 marked cell 사이에만 공간 edge를 둔다. 같은 XY에서 layout과 cnt/via가 모두 marked이면 channel 사이 edge를 둔다. layout-layout, cnt-via, 서로 다른 via 사이의 직접 edge는 없다. 이들은 layout을 경유해서만 간접 연결될 수 있다.
- **layer 기반 CNT/VIA endpoint 제한은 적용하지 않는다.** producer는 global CNT와 endpoint binding 없는 VIA asset을 내보내므로 임의 layer/전극 대응을 만들 수 없다. 같은 XY의 모든 관여 layout에 연결 가능한 기하 proxy라는 가정을 `connection_semantics=global_contact_proxy_unverified`로 기록한다. 실제 net·층간 도통/short의 검증이 아니며 layer 정보와 case endpoint는 정체성/검색 문맥으로 계속 보존한다. 물리 연결 검증이 필요하면 producer endpoint 계약 추가를 별도 사용자 게이트로 다뤄야 한다.
- layout끼리 같은 XY에 있어도 CNT/VIA가 없으면 연결되지 않는다. 반대로 CNT 막대 자체의 4-neighbour 경로는 양쪽 layout을 매개할 수 있다. 이는 B3 재현을 판정하기 위한 명시된 proxy다.
- core 안의 **모든 비-valid channel** marked 내용이 두 view 및 원본 core와 동일해야 한다. CNT/VIA의 core 내용도 검사하되 core 밖 무관한 CNT component 수나 전체 면적은 비교하지 않는다.
- anchor 집합 A는 core 안의 marked layout cell 전체이며 key는 `(layout channel, source row, source col)`이다. 두 view에 동일한 A가 있어야 한다. A가 비면 `no_core_layout_anchor`로 거부한다.
- 각 view의 전체 창 그래프에서 A가 이루는 연결 동치분할을 구한다. 동일 channel 내부와 channel 사이의 연결을 **하나의 분할**로 비교한다. 고정 anchor 순서에서 component label을 최초 등장 순으로 재번호화하면 충분하며 A×A 행렬은 만들지 않는다.
- 두 분할이 같으면 그 scale은 수락, 다르면 `core_joint_connectivity_changed`로 거부한다. 한 scale이라도 실패하면 pair 거부다. 창보다 긴 선, core에 닿지 않는 component, anchor 관계를 바꾸지 않는 경계 출입은 거부 이유가 아니다.
- **M7: un-jittered centre 창과 비교하지 않는다.** 두 view가 모두 같은 bridge를 잃어 A의 분할이 같다면 수락한다. 학습 목적은 두 관측 view의 일치이며 원래 centre 창의 모든 연결 보존이 아니다. 원본은 core 내용·좌표 확인과 §2 resampling 기준에만 사용한다. centre 창까지 비교하면 의도한 경계 jitter를 다시 과도하게 제한한다. 이 제한을 training 문서·수락 evidence에 `comparison_basis=between_views_only`로 남긴다.

## 2. 거친 area-coverage view

- source pitch보다 거친 출력은 `area_coverage`만 허용한다. native와 거친 view를 혼합해도 scale별로 아래 규칙을 적용한다. nearest를 거친 pitch에서 쓰는 M2 설정은 학습 전 오류다. area가 source보다 고운 설정도 오류이며 native nearest는 유지한다.
- 거친 view마다 **그 view의 물리 창 안에 원본 cell 중심이 있는** source cell로 native reference 공동 그래프를 만든다. 반열린 창 경계의 포함 규칙은 정확한 유리수/정수 index 연산으로 고정한다. core 내용·valid·anchor 존재는 이 source 기준에서 먼저 검사한다. centre 창이나 원본 전체 연결과 비교하지 않는다.
- 실제 resampled patch는 coverage>0인 channel cell을 marked로 삼는다. valid coverage>0인 cell만 사용하며 이는 관측된 부분의 support proxy임을 기록한다. 모든 core source cell은 fully observed이고 창 안에 표현되어야 한다. zero/partial coverage의 차이를 bool valid=1로 위장하지 않는다.
- 모든 channel의 marked core source cell 중심을 output cell에 사상하여 해당 channel coverage>0을 검사한다(half-open floor, exact grid 연산). CNT/VIA도 포함하며 없으면 `resampled_core_marked_content_lost`다. 연결 분할의 anchor는 그중 layout cell만이다. fractional 값 자체를 두 jitter view 사이에 동일하다고 요구하지 않는다.
- resampled 공동 그래프에서도 A의 분할을 구하고 **각 view의 native reference 분할과 같아야** 한다. 기존 v2의 loss-only 검사를 강화하여 anchor 간 분리뿐 아니라 거친 cell 충돌로 생긴 가짜 병합도 `resampled_core_joint_relation_changed`로 거부한다. native 두 view의 분할도 §1대로 같아야 한다. 따라서 sampled 두 view의 분할도 같아진다.
- core 밖 bridge를 실제 coarse patch에서 삭제하면 source reference는 그대로여도 sampled 분할이 달라져 거부된다. resampling으로 새로 생긴 core 바깥 component라도 A의 관계에 영향이 없으면 허용한다. 원래 빈 source cell이 주변 coverage로 채워지는 현상 자체는 오류가 아니며, 면적/정량 CA 보존이나 정확한 물리 topology를 주장하지 않는다.
- 거친 grid 때문에 anchor 병합이 빈번하면 pitch를 조정해야 한다. 이 보수적 거부율은 보고하고 policy를 몰래 완화하지 않는다. **G4 실제 near/wide/core/jitter/output pitch는 미확정**이며 여기의 fixture 크기는 실자료 권고가 아니다.

## 3. M1/M2 설정 검증과 M4 동일성

M1은 문서 경고만으로 남기지 않고 **recipe+ROI/frame를 결합하는 학습 전 preflight**에서 거부한다. source pitch/centre가 없는 `ViewConfig` 생성 시에는 구조 검증만 가능하다.
- 각 축·scale에서 `core+2*jitter <= window` 및 crop의 정수 output-cell 수를 검사한다. 또한 jitter 전체 허용 범위에서 모든 core cell이 항상 표현되는지 exact source index의 양 끝값으로 검사한다. native nearest 시작 index는 `floor((centre+offset-window/2-origin)/p+1/2)`, 끝은 시작+cell 수다. 최대 시작≤core 최소 index이고 최소 끝>core 최대 index여야 한다.
- area reference의 시작/끝은 각각 `ceil((left-origin)/p-1/2)`, `ceil((right-origin)/p-1/2)`다. 이 단조 index 경계의 극값으로 같은 검사를 한다. frame 밖 core·valid hole도 preflight 오류이며 희소 topology 변화만 sampling-time 거부로 남긴다.
- M1의 pitch=.5, near=20, core=10.2, jitter=4.9는 core 양 끝 cell을 놓쳐 preflight 실패한다. grid 정렬 core=10인 R4 (a)의 jitter=4.9는 exact index 조건을 만족하므로 일괄적인 한 cell margin 강제 없이 통과한다. M2 coarse nearest는 `coarse_nearest_not_supported_for_views`로 실패하고 area 대안을 안내한다.
- **M4: `analysis_layer`는 `same_physical_case`의 필수 동일성 항목이다.** 그 값만 다른 후보는 False다. 기존 role-layer/종류 기반 검색 호환성까지 강화하지는 않으므로 후보로는 남을 수 있다. strict identity와 검색 compatibility를 구분해 결과·문서에 명시한다.

## 4. M8 batch 거부·재시도와 재현성

- 설정에 양의 정수 `max_pair_attempts`를 명시한다(제안 3회, **최초 시도 포함**). 실패한 ROI만 같은 RNG 흐름에서 다시 jitter한다. 이미 수락한 pair는 보존하고 같은 원본 위치를 복제해 batch 크기를 채우지 않는다. 각 ROI당 최대 횟수 후 포기한다.
- batch의 서로 다른 수락 위치가 2개 이상이면 해당 pair만으로 loss/optimizer step을 수행한다. 2개 미만이면 batch를 skip하고 다음 batch로 간다. 다른 batch의 중복 복사나 무제한 resample은 금지한다.
- 재시도 대상은 관측/core/연결 판정의 명시적 view rejection reason만이다. schema·identity 위조·정책 불일치·파일/설정 오류는 즉시 실패하며 `ContractError` 전체를 잡아 skip하지 않는다. preflight 오류는 첫 optimizer step 전에 실패한다.
- batch/epoch에 `attempted_pairs`, `retry_attempts`, `rejected_attempts_by_reason`, `accepted_pairs_generated`, `accepted_pairs_used`, `skipped_batches`, `optimizer_steps`를 기록한다. skip batch에서 버린 수락 pair도 generated에는 포함하되 used에는 넣지 않는다.
- epoch 전체 `optimizer_steps=0`이면 counters와 함께 `no_usable_batch_in_epoch` 명시 오류다. 실패 epoch를 완료로 저장하지 않고 마지막 완료 epoch checkpoint를 보존한다. 실패/skip 통계도 반환/오류 artifact에 남긴다.
- retry 설정·v3 정책·graph semantics는 fingerprint에 넣고 RNG와 완료 epoch 통계를 checkpoint에 보존한다. 연속 실행과 resume에서 retry/skip 순서까지 재현되어야 한다. topology 기준을 낮춰 성공시키는 fallback은 없다.

## 5. P1 성능 수락 기준

배열 기반 component labelling/union-find(예: numpy의 run-length+정수 label 또는 torch)를 사용한다. scipy 추가에 기대지 않는다. component마다 `min(remaining)` 전수 탐색, cell별 Python tuple/set 객체 대량 생성, anchor마다 Fraction, A×A 비교를 금지한다. 좌표는 축별 exact index를 한 번 생성해 vectorize하고, 그래프/anchor 메모리는 O(channel×cell 수), union-find 시간 목표는 O(V+E)에 가까운 수준이다.
합성 benchmark는 2000×2000 source, near 400-cell/wide 2000-cell, core 200-cell, layout 2개+cnt+via+valid, 긴 직선과 1% 고립 CNT 및 30% CNT의 별도 fixture로 고정한다. wide 2000→500-cell area도 측정한다. 이 숫자는 P1 회귀시험용이지 실제 G4 값이 아니다.
같은 CPU·thread 설정에서 warm-up 2회 후 5회 중앙값: **쌍 검증 시간 ≤ max(2초, 같은 두 bundle crop 생성 시간×10)**, pair 전체 시간·최대 메모리·채널/밀도/seed도 보고한다. 입력 재사용 결과 cache로 측정을 우회하지 않는다. 500/1000/2000-cell sweep에서 같은 밀도의 검증 시간이 한 축 2배마다 6배를 넘지 않는지도 확인한다. 초과 시 P1 미해결로 남기고 실제 규모 실행을 승인하지 않는다; 성능을 맞추려고 판정을 생략하지 않는다.

## 6. 손계산 acceptance — 구현 함수로 기대값을 생성하지 않음

모든 연결 소실 시험은 core 내용이 양쪽 동일하다는 선행 단언을 두고 **정확한 연결 reason**을 확인한다. 내용 소실과 연결 소실을 넓은 regex 하나로 통과시키지 않는다.

| 시험 | 구성과 손으로 판단한 기대 결과 |
|---|---|
| B3 자연 crop | review 그대로 frame40², origin(100,200), p=1, 중심(120,220), L0[:,17], L1[:,22], CNT[14,17:23], near14/wide30/core6, offset(0,0)/(0,3). near1에는 두 anchor 전극을 잇는 CNT 경로가 있고 near2에는 없어 **거부 `core_joint_connectivity_changed`**. CNT 대신 VIA여도 동일 |
| B3 patch 삭제 | 위 구성에서 offset 둘 다 0, 두 번째 near의 CNT만 삭제. core 내용은 동일하지만 joint anchor partition은 1개→2개, **거부**. native 실제 patch를 보지 않고 source만 재구성하면 이 시험을 놓침 |
| R4 B2 (a) | 200², p=.5, origin(100,200), 20px 주기 2px 가로/세로 full-frame 선, near20/wide40/core10; review의 9중심×20회 및 jitter 0/.1/.3/1/4.9. 각 anchor는 같은 선 안에서만 연결되고 경계 길이 변화는 분할 불변이므로 **모두 수락** |
| R4 B2 (b) | review의 2% 고립-cell fixture·동일 seed, jitter 0/.1/.3/1. core 밖 고립 cell 출입은 anchor 관계에 영향 없어 **모두 수락**; fixture의 고립성이 실제 4-neighbour graph에서도 성립함을 먼저 확인 |
| dense CNT 경계 | core를 관통하는 두 평행 layout 선의 끝점 주위는 고정; 창 가장자리 band에 많은 고립 CNT/작은 CNT 덩어리를 두되 layout과 닿지 않게 함. jitter로 band 일부 출입, core 내용/분할 동일 → **수락**. core를 잇는 실제 CNT 경로가 추가/삭제되는 경우까지 수락하라는 뜻은 아님 |
| 층 교차 | 다른 layout 채널의 가로/세로선이 같은 XY에서 교차, CNT/VIA 모두 0. core anchor 분할은 **서로 다른 두 component**, 두 view 관계 동일이면 수락. CNT를 교차 셀에 넣으면 proxy상 하나로 연결됨 |
| 양쪽 동일 bridge 소실(M7) | B3의 CNT bridge 또는 review U자 bridge를 offset(0,3)/(0,2.5) 양쪽 near에서 잃음. near끼리 모두 분리, wide끼리 동일 연결이면 **수락**; centre 창과 비교해 거부하는 구현은 실패 |
| coarse 연결 삭제/병합 | 서로 다른 두 core anchor를 잇는 core 밖 CNT가 실제 area patch에서만 지워지면 reference 연결→sampled 분리로 **거부**. source에서 분리된 두 anchor가 coarse 한 셀에 합쳐져도 **거부**. fractional 값만 달라지고 joint 분할 보존이면 수락 |
| core 관측·내용 | 한 core cell valid=0이면 관측 오류; 한 view core CNT를 삭제하면 `core_marked_content_changed`; 빈 core layout은 `no_core_layout_anchor`. bridge reason으로 오분류하지 않음 |
| config·identity | M1 제한값은 preflight 실패/core10은 통과; coarse nearest 실패/area는 다음 검증으로 진행. analysis_layer만 M3→M1이면 검색 호환 가능, same_physical_case=False |
| bounded retry/skip | attempts=3; batch 첫 위치는 1회 성공, 둘째는 거부·거부·성공 → used=2, attempts=4, retries=2. 둘째가 3회 모두 거부면 used=0, generated=1, skip=1; 다음 정상 batch는 학습. 모든 batch skip인 epoch만 명시 오류 |

## 7. 구현 계약·검토 범위

후속 Task Target/Ownership은 DL2 `views.py`, `train.py`, `index.py`·`search.py`의 strict identity, 관련 `schema.py`/config 검증, checkpoint metadata, view/R4/config/train/checkpoint 시험과 training/review 문서로 제한해 coordinator가 실제 파일 목록·base SHA를 고정한다. C producer 변경이나 새로운 CNT endpoint schema는 포함하지 않는다. Change는 위 v3 판정·bounded retry·P1 개선이고 Acceptance는 §6 및 §5 수치 증거다. 기존 exact crop B1 수정·무라벨 loss·primary/supplementary·split·offline 규칙은 유지한다.
M3의 0==0 단언은 증거로 쓰지 않고 독립 loss 산술·view 행 순열·다른 위치 alignment 불변 시험을 유지한다. M5의 VIA 유무 호환성은 그대로이며 개수/endpoint 추론을 추가하지 않는다. M6의 producer_commit 누락/null은 기존 명시 opt-in+reason·pin 검증을 유지한다.
최소 검증은 관련 비학습 `test_views`, `test_r4_regressions`, config/identity/checkpoint 시험 및 합성 성능 harness다. retry/resume는 고정 rejection schedule과 가벼운 학습 대역으로도 검증하며 실제 학습은 실행하지 않는다. 새 exact candidate에 fresh Opus high R4 재검토가 필요하고 이전 FAIL/부분 PASS를 재사용하지 않는다. G4 실제 창과 실제 연결 의미/빈도·검색 품질은 미검증으로 남긴다.

## 8. Coordinator 채택 기록 (Opus, 2026-10-09)

채택한다. 요구 범위(B3, P1, M1·M2·M4·M7·M8), 기존 수락 사례(R4 B2 (a)(b))와의 양립, 재시도·재개 복구를 확인했다. 조건 두 가지를 덧붙인다.
- `max_pair_attempts`는 package 기본값 없이 설정에 반드시 적는다(문서의 3회는 합성 예시일 뿐이다).
- CNT/VIA를 layer 제한 없이 같은 XY의 관여 layout에 잇는 것은 기하 proxy이며 실제 도통 검증이 아니다. 결과와 문서에 `connection_semantics=global_contact_proxy_unverified`로 남긴다.
