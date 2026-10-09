# R5b 독립 재리뷰 — T12 R5 수정 후보

- 리뷰어: 별도 fresh Claude Opus 5.5 세션, 읽기 전용. 작성자·coordinator·R5 1차 리뷰어가 아니다.
- 대상: `particle-sim-compare` branch `rewrite/line-pixel-rate`, commit `325a33deb37f34362ec266cfcd2b326d2100bdfd` (R5 보고 `a756b6c` 위의 수정 commit, 1차 후보 `15aa90a`).
- 형제 저장소(읽기 전용): DL1 `7f9d659`, DL2 `e46a44e`.
- 계약: `docs/plan/PLAN_v2.md` T12, §2.4, §7.
- 일자: 2026-10-09. 이 판정은 **325a33d에만** 유효하다.

## 판정: PASS-WITH-NOTES

B1, B2는 모두 해소됐다. 새 차단 사항은 없다. 다만 note R1(`read`가 `--force` 없이 기존 출력을 거부하면서 그 파일을 지움)은 이번 수정이 새로 만든 동작이고 coordinator가 제시한 N10 확인 기준("기존 `--out`은 실패 시 절대 삭제되지 않는다")을 `read`에 대해서는 충족하지 못한다. 문서화된 동작과 모순되지 않고 잘못된 값이 나오지 않으므로 note로 분류했지만, 차단으로 올릴지는 coordinator가 정한다.

## B1 — 해소

- 후보 commit, clean tree에서 `.venv/bin/python -m pytest -p no:warnings tests/test_dl_results.py tests/test_handoff.py` → `44 passed`.
- 전체 suite → `523 passed, 2 skipped`. skip 2건은 `tests/test_particle_sim_export_integration.py:68, :162` (`PARTICLE_SIM_ROOT` 미설정)로 T12와 무관하다. cross-repo 테스트 3건은 skip되지 않고 실행됐다.
- 깨끗한 clone(`git clone` 후 `checkout 325a33d`, 형제 저장소는 옆 경로에 symlink, `PYTHONPATH=<clone>/src`로 clone의 소스를 import함을 확인) → 전체 `523 passed, 2 skipped`, 대상 2파일 `44 passed`. clone은 실행 후에도 clean.
- `tests/test_handoff.py:148`의 `dirs_exist_ok=True`가 수정 지점이다. 이 테스트는 `git archive HEAD` 위에 working tree의 T12 파일을 덮은 뒤 임시 저장소에 commit하므로 clean tree에서는 HEAD와 같은 내용을 검사한다.

## B2 — 해소

실제 DL2 CLI로 원래 재현을 반복했다. fixture ROI export, 같은 view 설정, seed만 7/11로 다른 두 checkpoint를 `psdl2.train` → `psdl2.index` → `psdl2.search`로 만들었다. A `checkpoint_fingerprint=12d9d985d674…`, B `e2ea4746ecf1…`, view fingerprint 동일, primary score A `[1.0, 0.999952]`, B `[1.0, 0.999928]`.

| 입력 | seal | read |
| --- | --- | --- |
| A search + A index + A expected (정상) | 통과 | `available` |
| B search + B index + B expected (정상) | 통과 | `available` |
| **B search + A index + A expected (원래 재현)** | `dl2_scores_do_not_match_index` | `dl2_scores_do_not_match_index` |
| B search + B index + A expected | `dl2_checkpoint_fingerprint_mismatch` | 같음 |
| A search + B index + B expected | `dl2_scores_do_not_match_index` | 같음 |
| A의 score를 B 값으로 일관되게 수정 | 거부 | 거부 |
| score를 반올림 한 단계(1e-6) 이동 | 거부 | 거부 |
| supplementary score 수정 | 거부 | 거부 |
| supplementary를 primary로 이동 (cohort 필드 수정 포함) | `dl2_search_scope_mismatch` / `dl2_cohort_mismatch` | 같음 |
| index embedding 교환 cf8812↔8392d9, query↔8392d9 | 거부 | 거부 |
| embedding 누락/null/빈 목록/0 벡터/중첩/차원 불일치/scalar/1e308/NaN | `invalid_dl2_index_embedding` 또는 `nonfinite_dl_number` | 같음 |
| score가 bool/문자열/null/누락/2.0/1.0000005 | 거부 | 거부 |

read 쪽은 정상 A를 봉인한 뒤 native 파일을 바꾸고 sidecar의 hash를 새로 맞춘 상태에서 확인했다(hash 불일치로 먼저 걸리지 않도록).

**정상 결과의 거부 여부**: 발견하지 못했다.
- checkpoint A로 query 4개 × cohort 4종(`[PRODUCT]`, `[OTHER,PRODUCT]`, `[OTHER]`, index에 없는 제품) × `--expand` 유무 × topk 1/10, thread 수 1/4 교대로 64회 실제 `psdl2.search` 후 C `seal`·`read`를 실행했다. search exit 0이 44회, exit 3(`status=unavailable`, primary 없음)이 20회였고 64회 모두 seal exit 0, read exit 0이다.
- C(.venv Python 3.11.15, numpy 2.4.6)와 DL2(.venv Python 3.12.13, numpy 2.5.3)에서 같은 식의 반올림 cosine을 차원 2/8/64/256/768의 무작위 쌍 20만 개에 대해 계산한 결과가 모두 같았다(불일치 0). C의 재계산 식(`dl_results.py:163`)은 `psdl2/search.py:24-25`와 같다.
- 같은 checkpoint로 thread 수 1/2/8에서 다시 계산한 embedding 4개가 index의 값과 완전히 같았다.

수용된 변형과 남는 민감도는 note R2, R3에 적었다.

## 새 차단 사항

없음.

## Notes

- **R1 (중요) `read`는 `--force` 없이 기존 `--out`을 거부하면서 그 파일을 지운다.** 위치 `src/simcompare/dl_results.py:414-418`. 재현: `commands.md:145`의 `read … --out "$RUN/joined.json"`을 그대로 두 번 실행. 두 번째 실행은 `{"status":"unavailable","reason":"output_exists_use_force: …/joined.json"}` exit 2를 내고 첫 실행의 정상 `joined.json`이 삭제된다. `--out`을 무관한 기존 파일로 지정해도 같은 메시지와 함께 그 파일이 삭제된다. 15aa90a에서는 두 번째 실행이 새 결과로 교체했으므로 이번 수정이 만든 동작이다. `seal`·`context`는 기존 파일을 보존한다(확인함). README:46과 R5 N10은 "read 오류 시 joined를 지운다"를 의도된 동작으로 적고 있어 문서와 모순은 아니고, joined는 `--force`로 다시 만들 수 있는 파생 파일이며 값이 틀려지지 않는다. 그러나 "존재하니 --force를 쓰라"는 거부와 삭제는 서로 맞지 않는다. 권장: 존재 검사를 `try` 앞에서 하거나 `read_results` 자체가 실패한 경우에만 unlink. 이 경로를 검사하는 테스트가 없다(`test_failed_creation_preserves_preexisting_output_and_assets`는 `seal`, `context`만 다룬다).
- **R2 (중요) score 대조는 사실상 정확 일치이고 query embedding은 search 시점에 다시 계산된다.** `psdl2/search.py:165`는 index의 값이 아니라 model로 query embedding을 새로 계산하고, C는 index의 query embedding으로 재계산한다(`dl_results.py:159-165`, 허용오차 1e-9는 6자리 반올림 뒤에 적용). 같은 기계에서는 결정적이어서 거부가 재현되지 않았다. index와 search를 다른 기계·BLAS·장치에서 실행해 float32 마지막 자리가 달라지는 경우는 확인하지 못했다. 민감도만 모의로 쟀다: query embedding 전 성분을 float32 1 ulp 옮기면 반올림 score가 달라지는 비율이 차원 8에서 123/20000, 차원 768에서 9/20000이다(실제 발생률이 아니라 모의 값). 발생하면 정상 결과가 `dl2_scores_do_not_match_index`로 거부된다. 권장: index와 search를 같은 환경에서 실행하라는 안내와 복구 절차를 `troubleshooting.md`에 추가하거나 허용오차를 반올림 한 단계로 넓힌다.
- **R3 native 파일을 손으로 고친 경우 중 수용되는 것.** 모두 seal·read 양쪽 통과: primary 순서 뒤집기, 최상위 후보 삭제(top-k 절단), `expanded=true`인데 supplementary 비우기, primary 행 중복, query 자신을 match로 삽입, `status`를 `unavailable`로 바꾸기(match는 그대로 표시), match를 모두 지우고 `available` 유지, score를 5e-10 이동, score가 같은 두 후보(8392d9↔5c4465)나 score 1.0인 query↔후보의 embedding 교환, embedding 전체에 3을 곱하기, index B의 `checkpoint_fingerprint` 필드를 A 값으로 고친 뒤 B search와 봉인. 파일을 섞는 실수로는 생기지 않고 의도적인 편집이 필요하며, sidecar는 변조 방지 수단이 아니라고 README:21, :64가 밝힌다. 정렬 순서, 중복, query 자기 자신, status와 primary 유무의 일치는 C에서 값싸게 검사할 수 있다.
- **R4 커밋된 fixture `numeric_context.json`이 생성기와 맞지 않는다.** `validate_offline.sh`가 다시 만든 context의 record에는 N11의 `numeric_status`, `numeric_reasons`가 있고 커밋된 파일에는 없다. `original_values`, `original_areas`, 지도 NPZ hash는 같다. `read_results`는 필드가 없으면 대체값을 쓰므로(`dl_results.py:338-339`) 테스트는 통과하지만, 커밋된 fixture로 도는 테스트는 새 필드가 아니라 대체 경로를 검사한다. 나머지 native fixture 47개는 byte 단위로 같고 `review_rois.json`은 `assets_root` 절대 경로만 다르다(R5와 동일).
- **R5 `context --force`는 이전 `numeric_map/<stem>_<uuid>/` 디렉터리를 남긴다.** 재실행 후 디렉터리가 2개가 된다. 이전 context로 봉인한 exchange는 `dl_native_file_hash_mismatch`로 거부된다(의도된 동작).
- **R6 `troubleshooting.md`에 새 사유가 없다.** `manifest_sha256_mismatch`, `archive directory inventory mismatch`, `dl2_scores_do_not_match_index`, `dl2_cohort_mismatch`, `explicit_dl2_cohort_file_required`, `output_exists_use_force`, `explicit_value_semantics_required`, `offline output directory must be empty`.
- **R7 manifest pin은 선택 사항이다.** `--expect-manifest-sha256` 없이 실행하면 R5의 두 변조 재현은 여전히 exit 0이다. `commands.md:35, :60`은 pin을 넣지만 `README.md:31`의 `validate_offline.sh` 예시는 넣지 않는다. manifest에 build 호스트의 Python 버전이 들어가므로 hash는 호스트마다 다르다(이번 build는 python3 3.14.7에서 `96f84886…`). 승인값은 실제 전달하는 bundle에서 얻어야 한다.
- **R8 `commands.md` 사용성.** (a) `:71`이 `NUMERIC_CONFIG`를 demo config로 덮어써 3·4절이 demo를 가리킨다(`:78`에 설명은 있음). (b) `:111`의 `psdl1.evaluate`는 주석으로만 보류를 안내하고 명령은 그대로 있어 블록을 통째로 붙이면 빈 `--relations`로 실행된다. (c) `:106`은 주석이 "선택할 때만"인데 기본값으로 `--unlabelled-negatives`를 넣는다.
- **R9 N14a는 reader 함수 수준에서만 도달한다.** `load_dataset`은 non-synthetic manifest를 `real_input_requires_approved_adapter` / `real_source_format_unconfirmed`로 먼저 막으므로 CLI에서는 현재 이 검사에 도달하지 않는다. G2 adapter가 열릴 때 loader 경로 테스트가 필요하다.
- **R10 테스트 범위.** 실제 DL2 CLI 회귀(`test_handoff.py:213`)는 seal만 검사한다(read는 fixture negative와 이번 수동 확인으로 보완). 보존 테스트는 입력 누락으로 일찍 실패하는 경우만 다뤄 NPZ 생성 뒤 실패의 정리 경로(`dl_results.py:289-291`)는 검사되지 않는다.
- **R11 `R5_FIXES.md`의 서술이 commit 뒤 상태와 다르다.** "커밋하지 않는다", "HEAD는 a756b6c", manifest hash `71038b…`는 임시 clone 기준이며 이 파일은 bundle에 실린다. 성능 주장은 아니다.
- **R12 문자열 embedding은 API에서 `ValueError`로 나온다**(`NumericContractError` 아님). CLI는 exit 2로 처리한다.

## 확인한 것과 방법

**bundle·offline (325a33d, 세 저장소 clean)**: `build_handoff.sh` exit 0, commit C `325a33de…` / DL1 `7f9d659a…` / DL2 `e46a44e0…`, `dirty_test_only=false`, 파일 207/32/53개, `manifest_sha256=96f84886544fac17f14ada86cc67808a7d7512383d932a893bc680abd14fc944`. `check_handoff.sh --expect-manifest-sha256` exit 0. `validate_offline.sh`(기존 `.venv` 세 개) exit 0, `status=available`, 27단계, `numeric_validate`만 exit 3(선언된 partial). runtime 3.11.15 / 3.12.13 / 3.12.13 기록.

**note별 확인**
- N1: R5의 두 재현(DL1 commit을 0으로 수정, bundle 안 `dl_results.py`와 manifest hash 동시 수정) 모두 pin과 함께 `manifest_sha256_mismatch` exit 2. 대문자·빈 문자열·앞 12자리 pin도 거부. `validate_offline.sh`에 틀린 pin을 주면 `bundle_integrity: exit 2`.
- N2: 저장소 안 빈 `.venv`, 중첩 빈 디렉터리, bundle root의 빈 디렉터리, 파일 추가·삭제, 파일·디렉터리 symlink, pending gate 삭제, template 수정 모두 exit 2.
- N3: `/usr/bin/python3` 3.9.6을 PATH 앞에 두면 build는 `build_handoff requires Python >= 3.11.4` exit 2, traceback 없음, 출력 디렉터리 없음. check와 validate_offline은 3.9에서도 정상 동작. 비어 있지 않은 출력과 저장소 내부 출력은 거부되고 기존 파일은 유지.
- N5: 같은 `--out`으로 재실행하면 `offline output directory must be empty` exit 2, 기존 `validation.json` hash 불변. bundle 내부 출력 거부.
- N6: `docs/handoff/README.md`에 커밋 전 검증 기록이 없음을 읽어서 확인.
- N8: joined의 `dl2` 키는 `primary`, `supplementary`, `primary_cohort`, `expanded`, `coverage`, `query`, `rejected`, `reasons`, `status`이고 `matches`는 없다. sidecar에 cohort `path`+`sha256` 고정. cohort 내용 변경은 read에서 hash 불일치, 재봉인에서 `dl2_cohort_mismatch`. cohort pin이 없는 sidecar는 `explicit_dl2_cohort_file_required`. 중복 cohort 거부.
- N9, N11: 해당 테스트 통과와 코드(`dl_results.py:125-126, :279-280, :337-339`) 대조.
- N10: `seal`과 `context`는 `--force` 없이 exit 2, 기존 파일 유지, `--force`로 교체. `read`는 R1.
- N12: `commands.md`의 모든 명령을 bundle 소스와 세 interpreter로 대조했다. 실제 실행: `check_handoff.sh`, `uv export`(exit 0, hash 498개, bundle 불변), `validate_offline.sh`, `demo-data`, `validate-data`(exit 3 partial, 문서와 일치), `compare`, `electrode-map`, `export-review-rois`(demo는 `n_rois=0`, 문서와 일치), DL2 config 추출 한 줄, `psdl2.data`/`train`/`index`/`search`/`evaluate`(labels 없음 → `unavailable` exit 3), `:143`의 expected 생성 한 줄(checkpoint에서 계산, index stdout과 대조), `dl_results context`/`seal`/`read`. 결과: 전체 `pending`(G1, G2, G4 + 지정 gate), `dl1=available`, `dl2=available`, PRODUCT/B primary는 d=5 CA 4.0 µm². DL1 4개 CLI는 `--help`로 옵션 대조(`--unlabelled-negatives`, `--relations`, `--report`, `--ckpt`, `--query-manifest`, `--candidates-manifest` 모두 실재). N12(d)의 정정(`read_config`가 wrapper도 읽음)은 테스트가 A wrapper / B flat으로 실제 학습해 확인한다.
- N14a: `read_binary(synthetic=False)`에 semantics가 없으면 `explicit_value_semantics_required`. `binary_zero_is_marked`는 0을 표시로, `binary`는 0이 아닌 값을 표시로 읽는다. 알 수 없는 값과 3수준 배열은 거부. synthetic 기본은 그대로. 기존 numeric suite 통과.
- N4, N7, N13, N14b/c: 문서 서술만 읽어 R5_FIXES의 설명과 일치함을 확인했다.

**회귀**
- 복합 identity join과 반례: `tests/test_dl_results.py`의 반례(다른 제품의 같은 `case_id`, identity 6개 성분, origin 변경, source_version·asset hash, multi-case 두 행, DL1 fingerprint, map NPZ 변조)가 모두 통과. 15aa90a 대비 `join`의 비교 로직은 그대로이고 반환 형태만 바뀌었음을 diff로 확인.
- 경로 안전: sidecar의 `result`, `index`, 새 `cohort` 참조 각각에 `../`, 절대 경로, symlink, Windows 구분자를 넣어 모두 거부됨을 확인. 교환 디렉터리 밖 cohort는 seal에서 거부.
- 수치 불변: 15aa90a 대비 `src` 변경은 `dl_results.py`, `numeric_images.py`(기본값 처리), `adapters/numeric.py`(인자 전달) 3개뿐이고 계산 모듈은 변경 없음. 재생성한 context의 `original_areas`, `original_values`, 지도 hash가 커밋된 fixture와 같다.
- 성능 주장·선기입: `docs/handoff`에 평가 숫자나 합격 표시 없음, `validation-template.json`은 전부 null/빈 목록, manifest `pending_gates` 7개와 README의 G1/G2/G4/G5/G6·proxy·weights pending 목록 유지.
- 세 저장소는 리뷰 전후 `git status` clean. 임시 출력은 세션 scratchpad(`/private/tmp/…`)에만 썼다.

## 확인하지 못한 것

- 깨끗한 env에 대한 실제 offline 설치(wheel directory 없음)와 Linux 등 목표 플랫폼. 기존 개발 `.venv` 세 개로만 검증했다.
- index와 search를 서로 다른 기계·장치에서 실행했을 때의 score 일치(R2).
- 실제 DL1 CLI의 train → search → C 왕복. DL1은 `--help` 대조와 fixture 생성기까지만 확인했다.
- 실자료 경로 전체(G1/G2/G4/G5/G6), 실제 hotspot 이미지의 container·bit depth, layout 극성, loader를 통한 N14a.
- `manifest.schema.json`에 대한 기계적 JSON Schema 검증, OS 수준 network 차단, 이전 `scripts/1…5`의 재실행.
