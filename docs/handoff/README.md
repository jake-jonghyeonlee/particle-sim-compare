# 폐쇄망 인수: C / DL1 / DL2

이 패키지는 파일 교환과 합성 실행을 검증한다. 실제 정확도, 인수 합격 또는 전기적 도통을 증명하지 않는다. C는 원본 CA, line / pixel, 전극 수 지도를 계산한다. DL1은 원본 구조로 제품 후보를 찾고 DL2는 사용자가 고른 위치의 원본 구조와 비슷한 ROI를 찾는다. DL score는 원본 수치 옆에 표시하며 CA나 위험 수치로 재투입하지 않는다. 다른 제품의 좌표를 겹치지 않는다.

[commands.md](commands.md)의 설치 → 입력 검증 → C 비교 → 지도 → ROI → DL1 → DL2 → 결과 읽기 순서를 따른다. [troubleshooting.md](troubleshooting.md)는 명시적인 실패의 복구 절차다. [validation-template.json](validation-template.json)은 사용자 인수 기록의 빈 양식이며 평가 숫자·label·목표·성공 판정을 미리 넣지 않는다.

## 패키지 만들기와 검증

폐쇄망에서는 CMD를 사용한다. compare 설치·pip check·도움말은 사용자 성공 확인이 있으며 전체 DL 연동은 Windows 미검증이다. Windows용 Python/wheel과 handoff 도구용 Git Bash를 준비한다. [commands.md](commands.md)에 CMD 명령을 기록한다.

DL1 pin: **`main` @ `dc9a0197f0d9dce127072cdbbca4c9697344aac3`** (`feat/png-input`을 2026-10-18 fast-forward 병합). `d4dd896..f0a5598` 독립 리뷰는 PASS-WITH-NOTES다([DL1_HEAD_F0A5598_REVIEW.md](../review/DL1_HEAD_F0A5598_REVIEW.md)). `dc9a019`는 그 위에 메모 2–3 보완(문서, `.gitattributes`)만 더했다. DL1 Windows 설치 ZIP의 psdl1은 `78a2f91` 소스이므로 현재 코드를 쓰려면 DL1의 `bundles/updates/grid-diagnostics-f74a718` wheel을 추가로 설치한다. 설치 후에는 ZIP 기준 설치 검증이 `installed psdl1 source differs`로 실패하는 것이 예상 동작이다(리뷰 메모 1–2, DL1 `docs/WINDOWS_OFFLINE.md`). 상세 입력 계약은 bundle의 `particle-sim-dl1/docs/USAGE.md`를 따른다.

change-roi-v2는 `review_rois.json`·`mask_derivation.json`·`roi_correspondence.json` 형식을 바꾸지 않았다.

DL2 pin: **`feat/dl2-patch-dataset` @ `2d65f8fed9c9196b849b9c8f77e76fb8aa23a7bc`** (2026-10-28, origin에 push됨). `77970b7` 위에 DL2 최종 리뷰 권고(계약 문구, ETX 실행기 문구 경고, 혼합 봉투 수)와 문서 개인 경로 제거를 더한 commit이다(DL2 Run `20261026-dl2-db-search` T10, Opus high PASS-WITH-NOTES). 입력·출력 형식은 `77970b7`과 같고, 이 commit의 DL2 코드로 `tests/test_dl2_e2e.py` 18개가 통과했다. 이전 pin `77970b7`(2026-10-27): DL2 Run `20261026-dl2-db-search`의 `feat/dl2-db-search`를 fast-forward 병합한 commit이다. 이전 pin `f41d427`(`main`) 위에 패치 데이터셋·multi-export(`efc6dac`), DB 검색 배치 봉투 `psdl2_search_batch_v1`·`mask_derivation`·multi 평가(PLAN_v3_db_search, 최종 HEAD 독립 Opus high 리뷰 PASS-WITH-NOTES)를 더했다. 수치 저장소는 이 봉투를 `confirm-pairs --match`로 읽고(R3), 변경점 export에 `mask_derivation.json`을 함께 둔다(R1). 연결은 실제 DL2 코드로 검증했다(`tests/test_dl2_e2e.py`, [change_roi](../plan/change_roi/README.md)). 그 이전 기록: pin `f41d427`(2026-10-20)은 `ce98fd3`(2026-10-18) 위에 PLAN_v4 배치 NCC(`d72e839`), GPU 선택·공유 asset 캐시·torch 완화·진행 출력(`08ed566`, 독립 Sonnet high 리뷰 PASS-WITH-NOTES), 그리고 Astra high 검수 세 번의 FAIL을 반영한 수정(`440d329`, `c297774`, `f41d427`)을 더했다. 최종 `f41d427`은 Astra high PASS-WITH-NOTES다. `psdl2.eval_position`/`psdl2.baseline_ncc`는 수치 저장소의 `roi_correspondence_v1`과 `review_rois.json`을 읽는다([PLAN_v3](../plan/PLAN_v3_local_delta_ca.md), [PLAN_v4](../plan/PLAN_v4_auto_candidates.md)).

build script의 python3는 >=3.11.4가 필요하다. 미지원 버전은 출력 생성 전에 오류로 중단한다.

Coordinator가 승인한 T12 커밋 이후, 세 저장소가 clean일 때 실행한다. 승인된 정확한 ref를 사용한다.


Windows CMD (미실행, Git Bash `bash` 필요):

```bat
bash scripts/build_handoff.sh D:/approved-output/bundle --compare-ref APPROVED_COMPARE_COMMIT --dl1-ref dc9a0197f0d9dce127072cdbbca4c9697344aac3 --dl2-ref 2d65f8fed9c9196b849b9c8f77e76fb8aa23a7bc
bash scripts/check_handoff.sh D:/approved-output/bundle --expect-manifest-sha256 APPROVED_MANIFEST_SHA256
```

`APPROVED_COMPARE_COMMIT`은 coordinator가 승인한 실제 commit으로 바꾼다. 기본 ref는 각 저장소의 `HEAD`다. `git archive <commit>`으로 tracked 파일을 복사하며 원본을 이동하거나 remote를 사용하지 않는다. `.venv`, 미추적 데이터와 가중치는 포함하지 않는다. tracked `.pt` / `.pth` / `.ckpt` / `.safetensors`가 있으면 묵시적으로 포함하지 않고 build를 거부한다. `manifest.json`은 commit, lock hash, 각 파일 SHA256, schema/id 버전, build 호스트 Python 버전 (runtime env 버전이 아님), 저장소별 Python 요구사항, 빈 `weights`, pending 항목을 담는다. C는 uv.lock, DL1/DL2는 requirements.lock을 기록하며 path·sha256·kind를 명시한다. DL lock은 macOS 개발 환경의 설치 버전 snapshot으로 portable wheel lock이 아니므로 목표 플랫폼용 dependency를 승인 mirror 또는 wheel directory에서 준비한다. 파일 inventory/hash가 다르거나 dirty archive이면 check가 실패한다. SHA256은 전달 무결성 검사이며 서명 인증을 대체하지 않는다. build/check가 manifest SHA256을 출력한다. 승인 채널로 따로 받은 값을 --expect-manifest-sha256에 넣어 대조한다. 검사 대상 manifest에서 기대 hash를 다시 계산하지 않는다.

`--allow-dirty-for-test`는 로컬 시험에만 쓰며 여전히 committed ref만 archive한다. manifest의 `dirty=true`를 숨기지 않고 check에도 같은 flag가 필요하다. 미커밋 T12 도구는 이 archive에 들어가지 않으므로 실제 인수용 bundle로 사용하지 않는다. 테스트는 임시 git 저장소에서 clean archive와 tamper를 별도로 검증한다.

검증/설치 결과와 실제 입력은 bundle 밖에 둔다. bundle 내부 파일을 수정하거나 `.venv`를 추가하면 inventory가 바뀐다. 세 env를 밖에 설치한 뒤:


Windows CMD (전체 연동 미실행):

```bat
set "COMPARE_PYTHON=D:/approved-envs/compare/Scripts/python.exe"
set "DL1_PYTHON=D:/approved-envs/dl1/Scripts/python.exe"
set "DL2_PYTHON=D:/approved-envs/dl2/Scripts/python.exe"
bash scripts/validate_offline.sh D:/approved-output/bundle --out D:/approved-output/offline-validation --expect-manifest-sha256 APPROVED_MANIFEST_SHA256
```

승인 hash는 **실제로 전달한 bundle**에서 build가 출력한 값을 별도 승인 채널로 전달받는다. manifest에 build 호스트 Python 버전이 들어가므로 호스트마다 hash가 다를 수 있다.

이 script는 먼저 hash를 검사하고, 각 자식 Python에 socket connect / DNS를 거부하는 `sitecustomize`를 적용한다. import / help / C demo·validate·compare·map / synthetic DL1 search / DL2 import·index·search / C read를 실행한다. dependency 설치나 download는 하지 않는다. 단계별 command/stdout/stderr/exit가 `validation.json`에 기록되며 실패는 `unavailable`, exit 2다. 차단은 환경을 상속한 non-isolated Python 자식의 socket.connect/connect_ex/create_connection/getaddrinfo에 한정된다. UDP sendto, gethostbyname, _socket 직접 호출, python -I, 비-Python 프로세스는 차단하지 않는다. 운영체제 방화벽 설정을 바꾸지 않는다. 실제 폐쇄망에서는 조직의 네트워크 차단도 유지한다. 합성 DL 모델은 고정 seed의 로컬 초기화 tensor를 쓰며 공개 pretrained weights나 학습 품질 판정을 포함하지 않는다.

## 교환 검증 계약

`python -m simcompare.dl_results`의 `context`, `seal`, `read`를 쓴다. 기존 numeric CLI 동작은 바꾸지 않았다. `context`는 numeric_v2와 원본 asset manifest를 다시 읽어 CA·line/pixel·지도와 ready ROI를 연결한다. 전극 수 배열은 `numeric_map/*.npz`로 저장하고 context에 상대 경로와 SHA256을 고정하며 read가 이 파일도 검증한다. manual 위치에는 diameter를 추측하지 않고 모든 diameter 관측을 붙인다. map 위치에는 선언된 representative diameter를 사용한다. blocked ROI는 numeric join 후보나 DL 실행 대상으로 들어가지 않는다.

**알려진 한계: native DL result files carry no schema_version of their own.** DL1 search는 `fingerprint`, `grid`, `grid_selection`, `grid_gate`, `rankings[].input_identity`, exclusions를 제공한다. DL2 search는 `query`, `primary`, `supplementary`, `index_roi_ids`, scope, coverage, no-match 사유를 제공하지만 model fingerprint를 직접 담지 않는다. 따라서 원본 JSON을 수정하지 않고 C의 `seal` 명령이 `dl_file_exchange_v1` sidecar를 생성한다. sidecar는 각 native 파일의 정확한 SHA256, DL1 config fingerprint, DL2 index의 `schema_version=psdl2_index_v2`, `checkpoint_fingerprint`, `view_fingerprint`, numeric context SHA256을 고정한다. 사용자 승인된 checkpoint/config의 fingerprint를 `seal`에 제공해야 한다. model 신원을 확인하지 않고 다른 결과에서 fingerprint만 복사하는 절차는 승인 절차가 아니다.

`read`는 native hash와 profile/version/fingerprint를 먼저 검사한다. DL2 query/match는 embedding 제외 필드가 index와 같아야 하고, numeric context와 `(roi_id, product_id, design_version, source_id, process_step_id, exposure_id, case_id, frame_id, source_contract_version)`를 정확히 맞춘다. frame origin/pitch/shape, source_version, producer schema/commit/source hash, asset hashes, centre, endpoint/failure/layer binding도 같아야 한다. CA diameter는 context의 원본 관측 값에서 보존한다. shape만 같거나 case_id만 같다고 연결하지 않는다.

seal/read는 producer의 보장에 따라 primary를 score 내림차순, 동점에서 DL1 product_id / DL2 roi_id 오름차순으로 검사하며 중복 후보, query 자기 자신, status와 primary 유무 불일치를 거부한다. DL2 unavailable은 primary 없음이라는 뜻이며 supplementary hit는 있을 수 있다. top-k 절단 뒤 목록의 완전성이나 `expanded=true`일 때 supplementary가 반드시 존재한다는 조건은 producer가 보장하지 않아 검사하지 않는다.

DL1은 제품 score이므로 단일 case context가 없다. 사용자가 해당 `input_identity`의 원본 구조와 일치함을 확인한 `dl1_bindings.json`에 product, input_identity, roi_id, 전체 source_identity, frame, source_assets, source_version을 명시해야 한다. C가 제품명으로 case를 고르지 않는다. binding이 없으면 `dl1_product_source_context_required`로 unavailable이며 수치는 유지된다. synthetic fixture의 DL1 입력은 실제 C 원본 layout/CNT/valid로 만들고 case A의 source binding만 명시한다; case B를 DL1 기여자로 만들어 넣지 않는다.

원본 결과와 sidecar를 같은 교환 디렉터리에 둔다. absolute/상위/symlink escape는 거부한다. 파일이나 fingerprint가 다르거나 복합 identity가 어긋나면 read는 exit 2로 거부하고 기존 joined output도 지운다. DL 파일이 없거나 gate가 pending이면 explicit unavailable/pending 상태를 반환하되 원본 numeric context는 남는다. supplementary는 primary로 재표시하지 않는다.

## DL1 PNG 입력

| 입력 | 극성 | 경로 |
| --- | --- | --- |
| layout/electrode 및 CNT PNG | **BLACK pattern on WHITE background** (검정 패턴 / 흰 배경) | `python -m psdl1.import_png` |
| hotspot PNG | **WHITE pattern on BLACK background** (흰 패턴 / 검정 배경) | compare asset의 `value_semantics: binary` |

DL1 입력은 manifest를 손으로 쓰지 않고 importer로 검사 → `--inspect-grids` → 승인 metadata와 `--candidate-grid`를 명시한 `--export` → `--verify-bundle` 순서로 만든다. hotspot/CA는 DL1 입력 shape가 아니다. importer는 전체 원본을 사용하며 unit 환산이나 crop을 추측하지 않는다. grid와 계보 metadata는 사용자가 정한다.

## 실제 입력에서 아직 결정해야 할 사항

- G1: diameter별 bin이라는 뜻은 확정됐지만 density 단위와 inspected-area 환산은 pending이다. count/glass를 per um²로 간주하지 않는다.
- G2: 2x2 결과에서 한 unit 값을 취하는 방법과 실제 이미지 mode/bit depth 확인은 pending이다. layout/electrode와 CNT의 PNG 극성은 위 표와 같이 확정됐다. 4로 나누지 않는다. 제품 간 um/pixel은 같고 pixel 수가 다르며 제품 내부 layer 크기는 같다.
- 실제 hotspot은 PNG이고 검정 배경 / 흰 패턴이다(사용자 확인 2026-10-13, 이전의 "검정이 hotspot" 기록을 대체). **실제 hotspot asset은 `value_semantics: binary`를 명시**한다. 자동 검출하지 않는다. layout/electrode·CNT PNG는 **흰 배경 / 검정 패턴**이며 DL1 importer가 읽는다.
- G4: DL2 near/wide window, pitch, jitter, core size, intermediate-layer policy, max_pair_attempts를 사용자가 실제 run 전에 모두 고른다. 실측용 default는 없다. synthetic config 수치를 실측에 복사하지 않는다.
- G5: DL1 real candidate_grid/minimum line width, CNT/VIA endpoint source format은 pending이다. DL1 grid는 manifest의 `candidate_grid`에서 온다.
- G6: label format과 acceptance targets는 pending이다. 합성 fixture 출력은 구조 검증이며 retrieval 품질 평가 숫자가 아니다.
- DL2 CNT/VIA view checks는 `global_contact_proxy_unverified` 기하 proxy이며 실제 도통 검증이 아니다.
- 공개 ConvNeXt V2 가중치는 검증하지 않았고 licence는 사용자가 확인해야 한다. 기본 package weights는 비어 있다.

G7a의 failure pieces와 G7b short-only map은 2026-10-08 사용자 결정이 적용됐다. map은 위치 취약성을 표시하고 DL2는 사용자가 선택한 위치와 비슷한 구조를 검색한다. open piece나 자동 영역 순위를 추가하지 않는다.


가상환경·demo_data는 검증 대상 bundle 밖에 둔다. 설치는 CMD 로컬 wheel 절차를 사용한다. validate_offline은 기존 non-empty 출력 디렉터리를 거부하고 각 환경 interpreter 버전을 기록한다. 실행 증거는 배포 설명서가 아닌 별도 review/검증 기록에 둔다.

DL2 score는 승인 index query/candidate embedding의 float64 cosine을 6자리 반올림하여 다시 계산하고 absolute tolerance 1.5e-6(반올림 한 단계와 여유) 이내 일치를 요구한다. 이는 supplied index와의 수치 일치를 증명할 뿐, native search에 producer fingerprint가 없어 원래 생성 checkpoint 신원을 암호학적으로 증명하지 못한다. 서로 다른 checkpoint가 동일한 rounded scores를 내면 구별할 수 없다. primary/supplementary는 별도 목록이고 cohort 파일 SHA256, expanded, coverage를 보존한다. seal/context는 --force 없이 기존 output을 교체하지 않으며 실패 시 기존 파일을 지우지 않는다. `context --force`는 새 context를 성공적으로 쓴 뒤, 교체한 context가 참조한 output root 내부의 `numeric_map/<stem>_<uuid>/` 한 디렉터리만 정리한다. 이 정리 실행은 macOS에서만 검증했으며 Windows에서는 경로 separator 비교를 모사한 시험만 했다.

Strict PNG 동작을 실행 검증한 Pillow 버전은 lock의 **12.3.0만**이다. `pyproject.toml`은 `pillow>=10.0`을 허용하지만 다른 버전의 동작을 검증했다는 뜻은 아니다. 폐쇄망 설치는 승인 wheel과 lock을 통해 Pillow **12.3.0**을 사용하고 실제 설치 버전을 확인한다.
