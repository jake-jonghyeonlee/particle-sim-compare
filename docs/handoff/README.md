# 폐쇄망 인수: C / DL1 / DL2

이 패키지는 파일 교환과 합성 실행을 검증한다. 실제 정확도, 인수 합격 또는 전기적 도통을 증명하지 않는다. C는 원본 CA, line / pixel, 전극 수 지도를 계산한다. DL1은 원본 구조로 제품 후보를 찾고 DL2는 사용자가 고른 위치의 원본 구조와 비슷한 ROI를 찾는다. DL score는 원본 수치 옆에 표시하며 CA나 위험 수치로 재투입하지 않는다. 다른 제품의 좌표를 겹치지 않는다.

[commands.md](commands.md)의 설치 → 입력 검증 → C 비교 → 지도 → ROI → DL1 → DL2 → 결과 읽기 순서를 따른다. [troubleshooting.md](troubleshooting.md)는 명시적인 실패의 복구 절차다. [validation-template.json](validation-template.json)은 사용자 인수 기록의 빈 양식이며 평가 숫자·label·목표·성공 판정을 미리 넣지 않는다.

## 패키지 만들기와 검증

Coordinator가 승인한 T12 커밋 이후, 세 저장소가 clean일 때 실행한다. 이 Task의 worker는 커밋하지 않는다.

```bash
bash scripts/build_handoff.sh /approved-output/bundle \
  --compare-ref APPROVED_COMPARE_COMMIT \
  --dl1-ref 7f9d659a014d7f5e3aaf4b63118d0ca51774b75f \
  --dl2-ref e46a44e0afbe94cd3f7a8d0264ee02f3076cf44b
bash scripts/check_handoff.sh /approved-output/bundle
```

`APPROVED_COMPARE_COMMIT`은 coordinator가 승인한 실제 commit으로 바꾼다. 기본 ref는 각 저장소의 `HEAD`다. `git archive <commit>`으로 tracked 파일을 복사하며 원본을 이동하거나 remote를 사용하지 않는다. `.venv`, 미추적 데이터와 가중치는 포함하지 않는다. tracked `.pt` / `.pth` / `.ckpt` / `.safetensors`가 있으면 묵시적으로 포함하지 않고 build를 거부한다. `manifest.json`은 commit, lock hash, 각 파일 SHA256, schema/id 버전, build Python 버전, 저장소별 Python 요구사항, 빈 `weights`, pending 항목을 담는다. C는 uv.lock, DL1/DL2는 requirements.lock을 기록하며 path·sha256·kind를 명시한다. DL lock은 macOS 개발 환경의 설치 버전 snapshot으로 portable wheel lock이 아니므로 목표 플랫폼용 dependency를 승인 mirror 또는 wheel directory에서 준비한다. 파일 inventory/hash가 다르거나 dirty archive이면 check가 실패한다. SHA256은 전달 무결성 검사이며 서명 인증을 대체하지 않는다. 사용자에게 승인받은 manifest hash를 별도 전달한다.

`--allow-dirty-for-test`는 로컬 시험에만 쓰며 여전히 committed ref만 archive한다. manifest의 `dirty=true`를 숨기지 않고 check에도 같은 flag가 필요하다. 미커밋 T12 도구는 이 archive에 들어가지 않으므로 실제 인수용 bundle로 사용하지 않는다. 테스트는 임시 git 저장소에서 clean archive와 tamper를 별도로 검증한다.

검증/설치 결과와 실제 입력은 bundle 밖에 둔다. bundle 내부 파일을 수정하거나 `.venv`를 추가하면 inventory가 바뀐다. 세 env를 밖에 설치한 뒤:

```bash
COMPARE_PYTHON=/approved-envs/compare/bin/python \
DL1_PYTHON=/approved-envs/dl1/bin/python \
DL2_PYTHON=/approved-envs/dl2/bin/python \
bash scripts/validate_offline.sh /approved-output/bundle --out /approved-output/offline-validation
```

이 script는 먼저 hash를 검사하고, 각 자식 Python에 socket connect / DNS를 거부하는 `sitecustomize`를 적용한다. import / help / C demo·validate·compare·map / synthetic DL1 search / DL2 import·index·search / C read를 실행한다. dependency 설치나 download는 하지 않는다. 단계별 command/stdout/stderr/exit가 `validation.json`에 기록되며 실패는 `unavailable`, exit 2다. Python 자식 실행의 네트워크를 차단하며 운영체제 전체 방화벽 설정을 바꾸지는 않는다. 실제 폐쇄망에서는 조직의 네트워크 차단도 유지한다. 합성 DL 모델은 고정 seed의 로컬 초기화 tensor를 쓰며 공개 pretrained weights나 학습 품질 판정을 포함하지 않는다.

## 교환 검증 계약

`python -m simcompare.dl_results`의 `context`, `seal`, `read`를 쓴다. 기존 numeric CLI 동작은 바꾸지 않았다. `context`는 numeric_v2와 원본 asset manifest를 다시 읽어 CA·line/pixel·지도와 ready ROI를 연결한다. 전극 수 배열은 `numeric_map/*.npz`로 저장하고 context에 상대 경로와 SHA256을 고정하며 read가 이 파일도 검증한다. manual 위치에는 diameter를 추측하지 않고 모든 diameter 관측을 붙인다. map 위치에는 선언된 representative diameter를 사용한다. blocked ROI는 numeric join 후보나 DL 실행 대상으로 들어가지 않는다.

**알려진 한계: native DL result files carry no schema_version of their own.** DL1 search는 `fingerprint`, `grid`, `grid_selection`, `grid_gate`, `rankings[].input_identity`, exclusions를 제공한다. DL2 search는 `query`, `primary`, `supplementary`, `index_roi_ids`, scope, coverage, no-match 사유를 제공하지만 model fingerprint를 직접 담지 않는다. 따라서 원본 JSON을 수정하지 않고 C의 `seal` 명령이 `dl_file_exchange_v1` sidecar를 생성한다. sidecar는 각 native 파일의 정확한 SHA256, DL1 config fingerprint, DL2 index의 `schema_version=psdl2_index_v2`, `checkpoint_fingerprint`, `view_fingerprint`, numeric context SHA256을 고정한다. 사용자 승인된 checkpoint/config의 fingerprint를 `seal`에 제공해야 한다. model 신원을 확인하지 않고 다른 결과에서 fingerprint만 복사하는 절차는 승인 절차가 아니다.

`read`는 native hash와 profile/version/fingerprint를 먼저 검사한다. DL2 query/match는 embedding 제외 필드가 index와 같아야 하고, numeric context와 `(roi_id, product_id, design_version, source_id, process_step_id, exposure_id, case_id, frame_id, source_contract_version)`를 정확히 맞춘다. frame origin/pitch/shape, source_version, producer schema/commit/source hash, asset hashes, centre, endpoint/failure/layer binding도 같아야 한다. CA diameter는 context의 원본 관측 값에서 보존한다. shape만 같거나 case_id만 같다고 연결하지 않는다.

DL1은 제품 score이므로 단일 case context가 없다. 사용자가 해당 `input_identity`의 원본 구조와 일치함을 확인한 `dl1_bindings.json`에 product, input_identity, roi_id, 전체 source_identity, frame, source_assets, source_version을 명시해야 한다. C가 제품명으로 case를 고르지 않는다. binding이 없으면 `dl1_product_source_context_required`로 unavailable이며 수치는 유지된다. synthetic fixture의 DL1 입력은 실제 C 원본 layout/CNT/valid로 만들고 case A의 source binding만 명시한다; case B를 DL1 기여자로 만들어 넣지 않는다.

원본 결과와 sidecar를 같은 교환 디렉터리에 둔다. absolute/상위/symlink escape는 거부한다. 파일이나 fingerprint가 다르거나 복합 identity가 어긋나면 read는 exit 2로 거부하고 기존 joined output도 지운다. DL 파일이 없거나 gate가 pending이면 explicit unavailable/pending 상태를 반환하되 원본 numeric context는 남는다. supplementary는 primary로 재표시하지 않는다.

## 실제 입력에서 아직 결정해야 할 사항

- G1: diameter별 bin이라는 뜻은 확정됐지만 density 단위와 inspected-area 환산은 pending이다. count/glass를 per um²로 간주하지 않는다.
- G2: 2x2 결과에서 한 unit 값을 취하는 방법, 이미지 container/bit depth, layout 이미지 polarity는 pending이다. 4로 나누지 않는다. 제품 간 um/pixel은 같고 pixel 수가 다르며 제품 내부 layer 크기는 같다.
- 실제 hotspot은 binary이고 검정이 hotspot이다. **실제 hotspot asset은 `value_semantics: binary_zero_is_marked`를 명시**한다. 자동 검출하지 않는다. layout polarity는 별도 pending이다.
- G4: DL2 near/wide window, pitch, jitter, core size, intermediate-layer policy, max_pair_attempts를 사용자가 실제 run 전에 모두 고른다. 실측용 default는 없다. synthetic config 수치를 실측에 복사하지 않는다.
- G5: DL1 real candidate_grid/minimum line width, CNT/VIA endpoint source format은 pending이다. DL1 grid는 manifest의 `candidate_grid`에서 온다.
- G6: label format과 acceptance targets는 pending이다. 합성 fixture 출력은 구조 검증이며 retrieval 품질 평가 숫자가 아니다.
- DL2 CNT/VIA view checks는 `global_contact_proxy_unverified` 기하 proxy이며 실제 도통 검증이 아니다.
- 공개 ConvNeXt V2 가중치는 검증하지 않았고 licence는 사용자가 확인해야 한다. 기본 package weights는 비어 있다.

G7a의 failure pieces와 G7b short-only map은 2026-10-08 사용자 결정이 적용됐다. map은 위치 취약성을 표시하고 DL2는 사용자가 선택한 위치와 비슷한 구조를 검색한다. open piece나 자동 영역 순위를 추가하지 않는다.

## T12 구현 검증 기록 (2026-10-09, 인수 합격 기록 아님)

worker는 C baseline `f9d7a31`의 working tree에만 새 파일을 작성했고 기존 numeric 파일과 DL1/DL2 저장소는 수정하지 않았다. 기존 test expectation 변경/삭제는 없다. `tests/fixtures/handoff/README.md`에 실제 producer 함수/commit/명령을 기록했다. 새 임시 negative case는 product 충돌, design/source/step/exposure/frame/source-contract version, origin 변경, schema/model/view fingerprint, native hash 변조를 검사한다. 합성 native 결과는 expected 성능 oracle가 아니다.

```bash
.venv/bin/python -m pytest -p no:warnings tests/test_dl_results.py tests/test_handoff.py
.venv/bin/python -m pytest -p no:warnings
bash scripts/build_handoff.sh /tmp/simcompare-t12-bundle-20261009d --allow-dirty-for-test
bash scripts/check_handoff.sh /tmp/simcompare-t12-bundle-20261009d --allow-dirty-for-test
bash scripts/check_handoff.sh /tmp/simcompare-t12-bundle-tampered --allow-dirty-for-test
COMPARE_PYTHON=/Users/jonghyeonlee/git/particle-sim-compare/.venv/bin/python \
DL1_PYTHON=/Users/jonghyeonlee/git/particle-sim-dl1/.venv/bin/python \
DL2_PYTHON=/Users/jonghyeonlee/git/particle-sim-dl2/.venv/bin/python \
  bash scripts/validate_offline.sh /tmp/simcompare-t12-bundle-20261009d \
  --allow-dirty-for-test --out /tmp/simcompare-t12-offline-final
.venv/bin/python -m simcompare.dl_results read tests/fixtures/handoff/exchange.json \
  --out /tmp/simcompare-t12-joined-final.json
```

- Full suite: **507 passed, 2 skipped**. 기존 numeric test는 유지됐으며 sibling `.venv` 실행과 임시 candidate offline 검증도 포함됐다.
- Target tests: **30 passed**. Cross-repo tests는 실제 sibling `.venv`에서 실행됐다.
- build/check: exit 0, 정확히 C `f9d7a31d85eadc14d4b8467f452e3610ea6378cb`, DL1 `7f9d659a014d7f5e3aaf4b63118d0ca51774b75f`, DL2 `e46a44e0afbe94cd3f7a8d0264ee02f3076cf44b` archive; weights 빈 배열. 이 bundle은 `dirty_test_only=true`로 기록된다.
- Tamper: 별도로 복사한 `/tmp/simcompare-t12-bundle-tampered/particle-sim-dl2/README.md`를 변조하자 check exit 2, `file hash mismatch: particle-sim-dl2/README.md`. 원래 bundle은 이후 재검사 exit 0.
- HEAD archive offline: exit 2, `fixture_runner_missing_in_committed_compare_archive; commit T12 before a production build`. 그 전 integrity, 세 package import/network-denial probe, 모든 기존 help, numeric demo/compare/map은 exit 0이었다. numeric validate는 원래 pending 계약대로 **partial, exit 3**이며 기록에 남겼다. T12를 커밋하지 않으므로 HEAD archive에 새 fixture runner/read module이 없는 것이 정확한 중단 원인이다. 기록: `/tmp/simcompare-t12-offline-final/validation.json`.
- 별도의 `test_offline_full_candidate_in_temporary_git_archive`는 repo 밖의 임시 git에 현재 후보 파일을 snapshot하여 clean archive를 만들었다. 작업 저장소 HEAD는 건드리지 않았다. 이 **시험용** bundle에서 같은 offline script의 imports/help, numeric E2E, native DL1 fixture search, DL2 import/index/search, seal/read까지 모두 통과하고 bundle hash 재검사도 통과했다. 이 임시 commit은 승인된 release commit이 아니다.
- Read CLI: exit 0, 원래 numeric 값은 그대로이며 synthetic DL scores는 primary/supplementary를 유지한다. context의 G1/G2/G4 pending을 자동 보존하므로 전체 join은 pending이다. 개별 synthetic retrieval availability를 실제 gate 완료로 해석하지 않는다.

실제 인수 bundle은 coordinator의 T12 승인 커밋 후 clean build/check/offline을 다시 수행해야 한다. 실제 입력, real G1/G2/G4/G5/G6, 도통 검증, 공개 weights/licence 및 사용자 label/인수 목표는 위 pending 목록대로 남는다. `validation-template.json`은 여전히 비어 있다.
