# R5 독립 리뷰 — T12 파일 교환 통합·폐쇄망 인수 패키지

- 리뷰어: 별도 fresh Claude Opus 5.5 (high) 세션, 읽기 전용. 작성자·coordinator 아님.
- 대상: `particle-sim-compare` branch `rewrite/line-pixel-rate`, commit `15aa90ab9bb1fd5138d8587795a2e715d972709a` (diff `f9d7a31..15aa90a`) + `f5e8600` (극성 옵션).
- 형제 저장소(읽기 전용): DL1 `7f9d659`, DL2 `e46a44e`.
- 일자: 2026-10-09. 이 판정은 **15aa90a에만** 유효하다.

## 판정: FAIL

차단 사항 2건(B1, B2). 둘 다 수정 범위가 작고 C 저장소 안에서 고칠 수 있다. 그 외 join 반례, bundle 변조 검사, 문서 명령, 극성 옵션은 아래 note 수준의 문제만 발견됐다.

## 차단 사항

### B1 — T12 수락 명령이 후보 commit에서 실패한다 (507 passed 아님)

- 위치: `tests/test_handoff.py:143-149` (실패 지점 148행).
- 재현: `.venv/bin/python -m pytest -p no:warnings tests/test_dl_results.py tests/test_handoff.py`
- 관측: `1 failed, 29 passed`. 전체 suite는 `1 failed, 506 passed, 2 skipped`.
  `test_offline_full_candidate_in_temporary_git_archive` → `FileExistsError: .../candidate/docs/handoff`.
- 기대: 계약의 수락 명령이 통과(보고된 30 passed / 507 passed, 2 skipped).
- 원인: 이 테스트는 `git archive HEAD`를 푼 뒤 T12 새 파일을 그 위에 `shutil.copytree`로 덮어쓴다. T12가 미커밋일 때는 archive에 `docs/handoff`, `tests/fixtures/handoff`가 없어 통과했지만, 커밋된 15aa90a에서는 이미 존재해 `copytree`가 거부한다. 즉 worker의 507 passed는 커밋 전 working tree의 결과이고 후보 commit의 결과가 아니다.
- 영향: T12 Acceptance 첫 항목 불충족. `docs/handoff/README.md:78-79`의 "507 passed / 30 passed" 기록도 후보와 맞지 않는다. 실제 동작 자체는 정상이다(같은 경로를 `validate_offline.sh`로 직접 실행하면 24단계 모두 기대대로 통과 — 아래 검증 참조).
- 수정 방향: 커밋 이후에는 overlay 복사가 불필요하므로 제거하거나 `dirs_exist_ok=True`.

### B2 — 다른 checkpoint의 DL2 검색 결과가 승인된 fingerprint 아래로 봉인·표시된다

- 위치: `src/simcompare/dl_results.py:101-132` (`_dl2`; 119행에서 embedding을 버리고 score를 index와 대조하지 않음), `docs/handoff/commands.md:109` (expected fingerprint를 검사 대상 index 자신에서 추출).
- 계약: T12 반례 "old weights와 새 channel schema가 shape상 우연히 맞아도 fingerprint 불일치로 거부해야 한다."
- 재현(실제 DL2 CLI, C가 내보낸 fixture ROI, labels 없음):
  1. 같은 `review_rois.json`·같은 view 설정으로 seed만 다른 두 모델을 `psdl2.train`으로 학습 → A `checkpoint_fingerprint=bdb259829c6e…`, B `b2db9103eafc…` (view fingerprint 동일, embedding 상이).
  2. 각각 `psdl2.index`, `psdl2.search` 실행.
  3. `dl_results seal --dl2 dl2_search_b.json --dl2-index dl2_index.json(A) --dl2-expected dl2_expected.json(A)` 후 `read`.
- 관측: seal exit 0, read exit 0, `dl2.status=available`. joined 결과의 score는 `[0.999386, 0.999386, 1.0]`(모델 B의 값)인데 sidecar의 승인 fingerprint는 A(`bdb259829c6e…`). A의 실제 score는 `[0.999948, 0.999948, 1.0]`.
- 기대: fingerprint 불일치로 거부.
- 원인: native DL2 search 파일에 model fingerprint가 없고, C는 search↔index를 `index_roi_ids`와 embedding 제외 필드로만 묶는다. 이 필드들은 같은 ROI export면 checkpoint와 무관하게 동일하다. README는 이 한계를 일부 밝히지만("model fingerprint를 직접 담지 않는다") sidecar가 고정한다고 서술하며, 실제로는 고정되지 않는다.
- 영향: 수치(CA·line/pixel) join은 정확하다. 잘못되는 것은 DL score의 model 버전 귀속이다. 폐쇄망에서 여러 run의 파일을 손으로 모으는 절차에서 일어나기 쉬운 실수다.
- 수정 방향(C만으로 가능): index에 query와 모든 후보의 embedding이 있으므로 `read`/`seal`에서 cosine을 다시 계산해 native score와 허용오차 내 일치를 요구한다. 또는 DL2 search 출력에 checkpoint/view fingerprint를 넣는다(DL2 변경 필요). 아울러 `commands.md:109`는 expected를 index가 아닌 승인된 checkpoint 쪽 출력(`psdl2.index` stdout의 `checkpoint_fingerprint` 등)에서 얻도록 바꿔야 검사가 자기 참조가 되지 않는다.

## Notes

- **N1 (중요) manifest 자체는 보호되지 않는다.** `check_handoff.sh`는 manifest를 기준으로만 검사한다. 재현: (a) manifest의 DL1 `commit`을 `000…0`으로 수정 → check exit 0. (b) bundle 안 `dl_results.py`를 고치고 manifest의 해당 hash를 함께 수정 → check exit 0. README는 "승인받은 manifest hash를 별도 전달"이라 적지만 build/check 어느 쪽도 manifest SHA256을 출력하거나 대조하지 않고 `commands.md`에도 그 단계가 없다. R5 행의 "bundle hash"가 산출되지 않는다. 권장: build와 check가 manifest SHA256을 출력하고 check에 `--expect-manifest-sha256`를 추가, `commands.md:14`에 대조 단계 명시.
- **N2 빈 디렉터리는 탐지되지 않는다.** bundle 안에 빈 `.venv/`를 추가해도 check exit 0 (`scripts/check_handoff.sh:36`은 파일·symlink만 수집). 파일 추가/삭제/수정, symlink 추가, root 추가 항목, pending gate 삭제, schema 파일 수정은 모두 거부됨을 확인했다.
- **N3 build는 `python3` ≥ 3.12(또는 3.11.4+)가 필요하나 문서에 없다.** macOS 기본 3.9.6으로 실행하면 `TypeError: extractall() got an unexpected keyword argument 'filter'` traceback으로 죽고(잡히지 않는 예외) 일부만 채워진 출력 디렉터리가 남는다(`scripts/build_handoff.sh:34`). check는 3.9에서도 동작한다. manifest의 `python_version`은 build 호스트의 `python3` 버전(이번 3.14.7)일 뿐 세 env의 버전이 아니어서 오해 소지가 있다.
- **N4 network 차단은 Python 수준 TCP/`getaddrinfo`에 한정된다.** 동일한 `sitecustomize`로 확인: `socket.connect`, `urllib`, 환경을 상속한 Python 손자 프로세스는 차단. UDP `sendto`, `socket.gethostbyname`, `_socket` 직접 호출, `python -I` 자식, 비-Python 프로세스는 차단되지 않는다. README 32행은 범위를 대체로 정직하게 적었으나 `validate_offline.sh:53`의 "sockets/DNS denied"는 실제보다 넓다. 현재 24단계 중 이 틈을 쓰는 단계는 없다.
- **N5 `validate_offline.sh`는 기존 출력 디렉터리를 경고 없이 덮어쓴다.** 같은 `--out`으로 두 번 실행하면 exit 0으로 `validation.json`·demo·native 결과가 교체된다(`:11` `exist_ok=True`). 기존 run 보존 원칙과 어긋난다. build 쪽은 비어 있지 않은 출력과 저장소 내부 출력을 올바르게 거부한다.
- **N6 README의 "T12 구현 검증 기록"이 후보와 맞지 않는다** (`docs/handoff/README.md:59-87`). 커밋 전 상태(C `f9d7a31`, `dirty_test_only=true`, "HEAD archive offline exit 2", "worker는 커밋하지 않는다")를 기술하며 bundle에 그대로 실린다. 실제 성능 주장이나 수락값 선기입은 없다. 15aa90a 기준으로 갱신하거나 인수 문서에서 분리 권장.
- **N7 DL1 binding은 사용자 주장에 의존한다.** DL1 입력이 case A에서 만들어졌는데 binding을 같은 제품의 case B ROI로 바꿔 재봉인하면 `available`로 읽힌다. C는 `input_identity`와 원본 asset의 대응을 검증할 수 없고 DL1 ranking 행에는 `design_version`이 없다. 다른 제품으로의 binding은 거부된다. README 42행에 한계가 적혀 있으나 binding 파일을 만드는 도구가 없어 `source_identity`/`frame`/`source_assets` 전체를 손으로 옮겨야 한다. 중복 binding은 `original_values`를 중복시킨다.
- **N8 joined 출력의 DL2 부분.** primary와 supplementary가 `matches` 한 목록에 `search_scope` 표시로 함께 들어간다(재표시는 없음, primary가 앞). `primary_cohort`, `expanded`, coverage는 joined에 남지 않아 `commands.md:114`의 "coverage" 서술과 다르다. 검색에 쓴 cohort 파일은 sidecar에 고정되지 않는다.
- **N9 `rejected` 키가 `_dl2`의 필수 집합에 없다** (`dl_results.py:107`, 사용은 290). 없으면 API는 `KeyError`, CLI는 exit 2에 사유가 `'rejected'`로만 나온다.
- **N10 CLI 실패 시 `--out`을 삭제한다** (`dl_results.py:345`). `read`에는 의도된 동작이나 `seal`/`context`에서 `--out`을 기존 파일로 잘못 지정하고 실패하면 그 파일이 지워진다. `context` 실패 시 `numeric_map/*.npz`는 남는다.
- **N11 map ROI의 대표 지름이 어떤 group과도 일치하지 않으면 `original_values`가 빈 목록으로 조용히 기록된다** (`dl_results.py:219-227`). 오류나 사유가 없다. fixture에서는 5.0이 정상 연결된다.
- **N12 `commands.md` 사용성.** 모든 명령·옵션은 실제 CLI와 일치한다(아래 검증). 다만 (a) `$SOURCE_ROOT`, `$ASSET_MANIFEST`, `$DL1_*`, `$DL2_*`, `$QUERY_ROI`, `$COHORT_JSON`, `$TOPK`, `$DL1_FINGERPRINT` 등은 본문에만 언급되고 설정 블록에 없다. (b) demo config의 ROI export는 `n_rois=0`이라 4~7절은 demo로 이어지지 않는다. (c) demo의 `validate-data`는 exit 3(partial)인데 2절에 언급이 없다. (d) DL2 config 예시(`configs/synthetic.example.json`)는 `config` 키 아래에 있어 그대로는 `--config`로 쓸 수 없다는 안내가 없다. (e) DL1 train/evaluate는 `--relations`를 무조건 넣는데 G6 미결 시 처리 안내가 약하다.
- **N13 이전 스크립트 `scripts/1_install.sh` … `5_check_particle_sim_run.sh`는 동작한다.** bundle 사본에서 `UV_OFFLINE=1`로 1~4 실행 exit 0, 5 `--help` exit 0. 폐기된 명령 참조 없음. 단 저장소 디렉터리 안에 `.venv`, `demo_data`를 만들므로 bundle 안에서 실행하면 inventory 검사가 깨지고 `uv sync`가 network를 쓸 수 있다. `commands.md`는 이 스크립트들을 쓰지 않는다.
- **N14 극성(f5e8600).** 구현은 정확하다(아래). 남는 점: (a) asset에 `value_semantics`를 생략하면 `binary`로 읽혀 검정 hotspot의 여집합 면적이 조용히 나온다 — 문서에 명시돼 있으나 코드가 강제하지 않는다(real 경로는 현재 `real_source_format_unconfirmed`로 막혀 있음). (b) C는 `.npy`/`.png`만, DL2는 `.bmp`/`.tif`도 읽는다 — G2 container 결정 때 맞춰야 한다. (c) DL1 reader(`psdl1/data.py:276`)에는 극성 옵션이 없다 — layout 극성 pending과 함께 다뤄야 한다.

## 확인한 것과 방법

**suite**: 전체 `1 failed, 506 passed, 2 skipped`; 대상 2파일 `1 failed, 29 passed` (B1).

**bundle (15aa90a, clean tree)**: `build_handoff.sh` exit 0, 기록 commit C `15aa90ab…`, DL1 `7f9d659a…`, DL2 `e46a44e0…`, `dirty=false`, lock `uv.lock`/`requirements.lock`/`requirements.lock`, 파일 204/32/53개, 6.3 MB. `.venv`·`.git`·data·weights 없음. `check_handoff.sh` exit 0. 공백 포함 경로 build/check 정상. 비어 있지 않은 출력과 저장소 내부 출력은 exit 2. manifest 필드는 `manifest.schema.json`의 required/enum과 눈으로 대조해 일치(`jsonschema` 미설치로 기계 검증은 못 함). 세 저장소 모두 리뷰 전후 `git status` clean.

**offline**: `validate_offline.sh` exit 0, status `available`, 24단계. `numeric_validate`만 exit 3(선언된 partial), 나머지 0. 조용히 건너뛰는 단계 없음. import probe의 `__file__`이 bundle 경로임을 기록에서 확인. interpreter 누락은 `unavailable` exit 2.

**실제 CLI 왕복**: bundle의 소스로 `psdl2.data` → `train`(labels 없음, exit 0) → `index`(4 indexed, 1 blocked) → `search`(primary 2, supplementary 1) → `evaluate`(labels 없음 → `unavailable`, exit 3) → C `context` → `seal` → `read` 모두 문서대로 동작. joined: 전체 `pending`(G1, G2, G4 + 지정 gate), `dl1=unavailable`(파일 없음), `dl2=available`, PRODUCT/A·B primary는 d=5 CA 4.0 µm², OTHER/A는 supplementary. C 명령(`demo-data`, `validate-data`, `compare`, `electrode-map`, `export-review-rois`)과 `uv export`(hash 포함, bundle 미변경)도 그대로 실행됨. DL1 4개 CLI는 `--help`로 옵션 대조.

**join 반례(`dl_results.py`, fixture 사본 변형)** — 거부 확인: 다른 제품의 같은 `case_id`(OTHER 행을 PRODUCT로 표기), design/source/step/exposure/frame/contract version 변경, export 뒤 origin·pitch·centre 변경, producer commit 불일치, asset hash·source_version 불일치, index 행 재정렬, index/context의 중복 `(roi_id, identity)`, 무관한 native 파일로 바꾼 sidecar(hash 유지·갱신 둘 다), `../`·절대·Windows 경로·symlink 탈출(sidecar와 context의 map 파일), supplementary를 primary로 재표시, DL1 fingerprint 불일치, NaN·범위 밖 score, map NPZ 변조. multi-case 영역의 두 행은 서로 다른 `roi_id`로 보존. sidecar의 `pending_gates`를 비워도 context의 gate로 `pending` 유지. DL 파일이 없으면 `unavailable`/`dl_result_missing`이고 numeric은 그대로. DL score가 numeric 값에 쓰이거나 값을 바꾸는 코드 경로는 없고 `interpretation`은 고정 문자열이다. 수용된 사례는 B2, N7, N8에 기록.

**fixture**: `generate.py`를 bundle에서 다시 실행한 결과 `dl1_search.json`, `dl1_bindings.json`, `dl1_standalone_search.json`, `dl2_index.json`, `dl2_search.json`, `dl2_expected.json`, `dl2_standalone/{index,search}.json`이 커밋된 파일과 byte 단위로 동일. `review_rois.json`은 `assets_root` 절대 경로만 다르고 그에 따라 context/exchange hash가 달라진다(README 45행에 설명됨). fixture는 CLI가 아니라 producer **함수** 호출로 만들었고 README도 그렇게 적는다. `dl2_index.json`의 `blocked_export_records`는 generator가 덧붙이지만 실제 `psdl2.index` CLI도 같은 키를 쓴다.

**극성(f5e8600)**: `MASK_SEMANTICS` 두 값만 허용, 자동 판별 없음, 반전 결과도 read-only, 0/1·0/255 인코딩에서 반전 정상, threshold 없음. record 수준은 `binary`만 허용. `roi_export`는 asset 참조의 극성을 `read_binary`에 그대로 넘기고 valid는 frame의 valid와 배열 동등성을 검사하므로 극성을 다르게 선언하면 `source_valid_mismatch`로 막힌다. DL2 `read_asset`도 같은 두 값을 같은 의미로 읽는다. electrode map은 loader가 만든 mask만 쓴다. legacy loader(`adapters/example.py`, `particle_sim_run.py`)는 별도 schema라 이 옵션을 쓰지 않는다.

**문서**: 실제 성능 주장·수락값 선기입 없음. `validation-template.json`은 전부 null/빈 목록. hotspot 검정 지시, DL2 실측 기본값 없음, G1/G2/G4/G5/G6·proxy·weights pending, G7a/G7b 답변 반영이 `implementation_memory.json`의 2026-10-09 기록과 일치.

## 확인하지 못한 것

- 깨끗한 env에 대한 실제 offline **설치**(wheel directory 없음). 검증은 기존 개발 `.venv` 세 개로 수행했다. Linux 등 목표 플랫폼에서의 DL lock 적합성도 미확인.
- 실제 DL1 CLI의 train → search → C 왕복(DL1 manifest 예제를 만들지 않음). DL1 fixture는 함수 재생성 일치와 `--help` 대조까지만 확인.
- 실자료 경로 전체(G1/G2/G4/G5/G6), 실제 hotspot 이미지의 container·bit depth, layout 극성.
- `manifest.schema.json`에 대한 기계적 JSON Schema 검증.
- OS 수준 network 차단(스크립트 범위 밖).
- `scripts/5_check_particle_sim_run.sh`의 실제 run 디렉터리 점검(`--help`만 확인).
