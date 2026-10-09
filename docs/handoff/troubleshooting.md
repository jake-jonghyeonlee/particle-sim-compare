# 실패와 복구

| 출력 사유 | 확인과 복구 |
| --- | --- |
| `dirty working tree refused`, `dirty test archive refused` | 원본 수정과 작업 소유권을 보존하고 coordinator의 승인 커밋/clean 상태를 기다린다. 실제 배포에서 test flag로 우회하지 않는다. archive는 working files를 포함하지 않는다. |
| `file hash mismatch`, inventory / dependency lock mismatch | 승인한 manifest와 원본 매체를 대조하고 bundle 전체를 다시 **복사**한다. hash를 수정해 성공으로 만들지 않는다. |
| `interpreter_missing` | bundle 밖에 요구 Python과 해당 lock dependency를 승인 wheel directory 또는 내부 mirror로 설치한다. DL2는 >=3.12다. validation은 설치/download하지 않는다. env 변수를 정확히 지정한다. |
| `fixture_runner_missing_in_committed_compare_archive` | T12가 아직 미커밋이거나 과거 ref를 archive한 시험 bundle이다. coordinator가 T12를 승인/커밋한 뒤 정확한 ref로 다시 만든다. |
| `offline_network_blocked` | download나 외부 연결 요청은 의도적으로 막혔다. 누락 wheel/가중치를 자동 다운로드하지 말고 승인된 오프라인 준비를 한다. 기본 fixture는 pretrained weights가 필요 없다. |
| C `real_source_format_unconfirmed`, density / unit-area pending | G2 실제 adapter나 G1/G2 값이 미확정이다. null+reason을 유지한다. count/glass→per um² 환산, 2x2÷4를 추측하지 않는다. synthetic 성공을 실측 성공으로 표시하지 않는다. |
| C missing mask / invalid unit / duplicate diameter / frame mismatch | manifest·원본 파일·단위·coverage를 수정하고 validate-data부터 다시 실행한다. missing을 zero/N/A로 채우지 않는다. 실제 hotspot 검정은 `binary_zero_is_marked`를 선언한다. layout polarity는 별도 확인한다. |
| ROI `source_shape_required`, `source_frame_required`, case missing | 사용자 점의 전체 product/design/source/case/frame 문맥과 원본 asset/hash/valid를 채운다. case A로 자동 배정하지 않는다. blocked는 실행하지 않는다. |
| ROI `map_threshold_required`, incomplete map coverage | 원한다면 사용자 threshold를 명시하고 관측 범위를 해결한다. 지도 위치와 manual 위치를 혼동하지 않는다. 실측 crop 크기는 G4의 별도 사용자 결정이다. |
| DL1 manifest `candidate_grid` conflict / inconsistent grid / override | query/candidate/training manifest가 승인된 동일 grid를 선언하는지 확인한다. 실제 grid/minimum line width G5는 사용자 답변이 필요하다. 단순 override로 conflict를 숨기지 않는다. |
| DL1 `grid_check_failed`, thin-line/component/bridge gate | original physical pitch, minimum line width, candidate grid와 resampling 증거를 확인한다. 세선 손실을 success로 만들기 위해 gate flag를 넣지 않는다. 실제 endpoint source format은 아직 pending이다. |
| DL1 no candidates / unknown vocabulary / held-out split error | 제품 role/layer와 lineage split, source manifests를 확인한다. score 대신 명시적인 exclusion/no-match를 보존한다. held-out/label 없는 결과로 정확도를 주장하지 않는다. |
| DL2 preflight `no_usable_batch_in_epoch` | 모든 epoch에서 usable batch가 없으면 학습을 멈춘다. near/wide sizes, pitches, jitter, core, intermediate policy, max_pair_attempts와 valid/geometry를 실제 사용자가 검토한다. label 없는 것을 억지 negative로 채우거나 ROI frame을 옮기지 않는다. |
| DL2 view preflight / crop/core/connectivity failures | 원본 CNT/VIA/layout binding과 valid를 확인한다. view 비교는 `common_core_connectivity_v3`, `between_views_only`, `global_contact_proxy_unverified`다. shared core 밖 connection은 전역 contact 증거를 유지하되 실제 conduction 판정으로 말하지 않는다. |
| DL2 coarse nearest warning / dropped structure | source pitch보다 coarse한 nearest는 세선/점 contact를 놓칠 수 있다. 사용자가 pitch를 재선택하고 preflight 증거를 본다. area resampling은 coverage이며 임의 threshold로 binary를 복구하지 않는다. |
| DL2 `producer_commit` null | C가 git checkout 밖이면 null과 producer_commit_reason이 나온다. 검증된 producer ref가 없으면 그대로 멈춘다. 사용자에게 이유를 확인받은 경우에만 validate/train/index/search에 `--allow-unversioned-producer`를 명시한다; source provenance에 수용 사실과 이유가 남는다. commit을 만들어 넣지 않는다. |
| DL2 incompatible index checkpoint/transform | 승인 checkpoint, config/view fingerprint와 일치하는 index를 다시 생성한다. 같은 shape라고 다른 index를 쓰지 않는다. |
| DL2 no usable/compatible primary cohort / no-match | DL1 후보 cohort, source case role-layer/failure type, query 자체 exclusion을 확인한다. supplementary hit는 primary cohort hit로 재표시하지 않는다. |
| C `dl_native_file_hash_mismatch`, fingerprint/schema mismatch | 승인 native JSON, checkpoint/config, index와 sidecar를 대조한다. 원본 JSON을 hand edit하거나 hash만 재작성해서 우회하지 않는다. 변경된 승인 run이면 모든 증거와 context를 다시 생성한다. |
| C `numeric_composite_identity_mismatch`, frame/source/version/binding mismatch | product/design/source/step/exposure/case/frame/version, 원본 origin/pitch/shape/centre와 asset hash를 확인한다. 다른 제품의 같은 case_id 또는 같은 shape를 대신 연결하지 않는다. |
| C `dl1_product_source_context_required` | DL1 제품 결과에는 case가 없다. 원본과 일치함이 확인된 explicit binding을 사용자가 제공하거나 수치 연결 unavailable을 유지한다. |
| DL1 / DL2 evaluation unavailable, labels missing | G6 label format과 사용자 acceptance target를 결정한 뒤 해당 별도 평가 절차를 실행한다. 빈 인수 record에는 숫자나 성공을 미리 채우지 않는다. |

직전 partial/error output을 complete로 재사용하지 않는다. `dl_results read` 실패 시 joined output을 지우고 numeric report는 별개로 유지한다. `validate_offline.sh`의 validation record는 실행 증거이며 사용자 실제 성능 인수 record와 별도다. `weights=[]`는 공개 ConvNeXt V2를 검증했다는 뜻이 아니다. 가중치 licence와 공급 hash는 사용자 확인 후 별도 승인 패키지에만 넣는다.
