# R6b 독립 리뷰 — R6 notes 수정(3624a4a)의 좁은 재확인

- 리뷰어: 별도 fresh Claude Opus 5.5 세션(effort high), 읽기 전용. 작성자·coordinator가 아니다.
- 대상: `particle-sim-compare` branch `rewrite/line-pixel-rate`, diff `91c6da2e117c5b7607ee7c5d45713afd60b327d5..3624a4ab24cf7d879483e58ab9b4c80a121c6554`의 `src`, `tests`, `docs/data_contract.md`, `docs/handoff`, `docs/open_questions.md`.
- 범위 밖: 이후의 docs-only commit `901255f`. 항목 5의 해상도 서술만 보았다.
- 환경: macOS, `.venv` Python 3.11, Pillow 12.3.0. 일자 2026-10-13.

Verdict: PASS-WITH-NOTES for 3624a4ab24cf7d879483e58ab9b4c80a121c6554

이 판정은 위 commit에만 유효하다.

## 차단 사항

없음. R6과 같은 차단 기준(조용히 틀린 mask/면적, 사유 없는 정상 입력·정상 producer 결과 거부, generator 소유가 아닌 것의 삭제, 인수 문서의 틀리거나 뒤바뀐 서술, 계약 회귀)에 해당하는 입력을 찾지 못했다.

## R6 notes의 상태

| note | 상태 | 근거 |
| --- | --- | --- |
| N1 빈 post-IDAT `iCCP`의 `IndexError` | 고쳐짐 | `png_strict.py:249-274`. 재현 파일이 `read_binary`에서 `png_decode_failed: i.png: index out of range`, CLI `validate-data`에서 exit 2, stderr에 reason과 상대 경로, traceback 없음, `error.json`의 reasons가 `['png_decode_failed']`. |
| N2 `open_questions.md`의 mode 서술 | 고쳐짐 | `3624a4a:docs/open_questions.md:59`가 `1`/`L`/`RGB`/`LA`/`RGBA`/`P` 수락과 16-bit 거부를 적는다. 실행 결과와 일치. |
| N3 Windows에서의 정리 경로 비교 | 고쳐짐(코드 추론, Windows 미실행) | `dl_results.py:303`이 `PurePosixPath(path).parent.as_posix()`를 쓴다. 아래 "N3" 참조. |
| N5 malformed DL 구조의 이름 없는 예외 | 고쳐짐 | 구조 변이 20,272건 × seal/read에서 제품 코드의 bare 예외 0건. |
| N6 살아남은 mutant | 고쳐짐 | 지정된 7개 모두 killed. |
| N8 Pillow 버전 | 고쳐짐(문서) | `data_contract.md:208`, `handoff/README.md:100`, `troubleshooting.md:41`에 12.3.0만 검증했다는 문장이 있다. lock은 `pillow-12.3.0`, 실행 환경도 12.3.0. |

## Notes (비차단)

- **B1 `901255f`의 `docs/data_contract.md:210`이 같은 파일 `:58`과 어긋난다(판정 commit 밖).** `:210`의 알려진 한계 문단은 "DL1의 고정 0.1 µm/pixel과 사용자 기록의 2×2 약 100 µm/2000 px(약 0.05 µm/pixel)의 실제 관계는 사용자 확인 사항이다"로 남아 있다. `901255f`는 `:58`과 `open_questions.md:16, :58`에서 0.1 um/pixel로 확인됐고 0.05 수치를 대체한다고 적는다. `3624a4a` 시점에는 `open_questions.md:16`이 아직 0.05였으므로 후보 안에서는 맞는 서술이다. `docs/handoff`에는 0.05 서술이 없고 `commands.md:202`의 0.1 µm/pixel과 일치한다. 권장: `:210`의 그 문장을 지우거나 "2026-10-13 확인으로 닫힘"으로 고친다.
- **B2 `_native_contract`의 fallback 분기에 테스트가 없다.** `dl_results.py:122-125`의 `except (TypeError, AttributeError, KeyError, OverflowError)`를 제거한 mutant가 살아남았다. 앞선 이름 있는 검사가 테스트 입력을 먼저 잡기 때문이다. 변이 battery에서는 `dl2_native_structure_invalid` 등으로 실제 도달한다.
- **B3 `_native_contract('dl2', join, …)`는 compare 쪽 원인도 DL2 탓으로 이름 붙인다.** `dl_results.py:456, :460`. `join` 안의 `original[key]`, `context['review_versions']`에서 나는 `KeyError`는 native 파일이 아니라 numeric context의 문제일 수 있다. 거부 자체는 맞고 reason 이름만 부정확하다.
- **B4 `except Exception`은 입력과 무관한 실패도 `png_decode_failed`로 바꾼다.** `MemoryError`나 이 구간의 코드 결함이 해당한다. 원인은 `__cause__`와 메시지에 남고, mask가 반환되는 경로는 없다.
- **B5 `troubleshooting.md:32`의 reason 목록.** `dl1_query_identifier_required`(`dl_results.py:167`)가 문서 어디에도 없다. `invalid_dl2_index_embedding`은 `:31`과 `:32`에 두 번 나온다. `_score`의 `invalid_dl_candidate_row`(`:101`)는 모든 호출 전에 `_candidate_rows`가 실행되므로 도달하지 않는다.
- **B6 Windows separator 테스트는 표기만 모사한다.** `test_cleanup_reference_comparison_is_independent_of_windows_separator`는 `str()`만 Windows 표기로 바꾼다. `handoff/README.md:98`이 이 한계를 밝힌다.

## 확인한 것과 방법

임시 출력은 `/tmp/r6breview.9SZG5u`에만 썼다. `git archive 3624a4a`를 그 안에 풀어 `PYTHONPATH`로 실행했고 `PYTHONDONTWRITEBYTECODE=1`, pytest는 `-p no:cacheprovider`다. R6 때의 스크립트를 그대로 복사해 다시 돌렸다.

### 1. N1 — Pillow 구간의 예외 처리

- 코드: 안쪽 `try`는 `_PNGError`, `NumericContractError`, `DecompressionBombError`, `DecompressionBombWarning`을 그대로 올리고 나머지 `Exception`만 `png_decode_failed`로 바꾼다. `return`은 검사 뒤의 두 곳뿐이고 except 절은 모두 raise하므로 실패가 성공으로 바뀌는 경로가 없다. 구조 검증(`_validate_png_structure`)은 이 `try` 밖이라 reason이 바뀌지 않는다.
- `KeyboardInterrupt`/`SystemExit`: `Exception`의 하위가 아니어서 통과한다. `except BaseException`으로 바꾼 mutant는 `test_png_decode_does_not_swallow_process_control` 2건이 잡는다.
- ancillary chunk 15종 × payload 8종 × colour 2종: bare 예외 0건. 빈 `gAMA`, 짧은 `pHYs`, 빈 `sRGB`는 `png_decode_failed`, grey/RGB `tRNS`는 `png_nonopaque_transparency`로 validator의 reason이 유지된다.
- R6 matrix 재실행 결과는 R6과 같다.

| matrix | 결과 |
| --- | --- |
| 유효 528건(극성, filter, 크기, palette) | 실패 0 |
| 리뷰 재현 612건 | 수락 0, 비계약 예외 0 |
| inflated byte 변이 6,000건, 독립 decoder 대조 | 수락 3,650 / 거부 2,350, 불일치 0 |
| 구조 변이 8,000건 | 수락 3,450(R6과 같은 수), 독립 decoder와 불일치 0, 비계약 예외 0(R6에서는 6회) |
| 16-bit, Pillow `I`/`I;16` | 모두 `png_unsupported_bit_depth` |
| pixel 한계 None/0/경계, `LOAD_TRUNCATED_IMAGES=True` | reason과 전역값 복구가 R6과 같음 |

### 2. N3 — 정리 범위

- R6의 조작 context 20종 재실행: 밖의 victim 디렉터리 손실 0. 지워진 것은 이름 규칙에 맞는 한 디렉터리뿐이다(안쪽 symlink는 링크만, `./` segment는 같은 소유 디렉터리로 정규화). `numeric_map`이 밖을 가리키는 symlink, `../` 경로, 절대 경로, 정규식 문자 stem, 다른 stem, 두 디렉터리, 다른 schema는 모두 아무것도 지우지 않는다.
- 쓰기 실패 3종(`write`, 두 번째 NPZ, export 누락): 이전 context와 지도 보존.
- Windows 추론(미실행): 참조는 `:339, :344`에서 `/`로 만들어 저장하므로 `PurePosixPath(...).parent.as_posix()`는 호스트와 무관하게 `numeric_map/<stem>_<hex>`가 되고 `:307`의 정규식과 맞는다. `\`가 든 조작 참조는 POSIX 의미에서 구분자가 아니므로 parent가 정규식에 맞지 않거나, 맞더라도 디렉터리 이름만 쓰이고 파일 부분은 쓰이지 않는다. 그 뒤의 symlink·`is_relative_to(root)` 검사는 바뀌지 않았다. **Windows에서는 실행하지 않았다.**

### 3. N5 — DL 결과 구조

- 실제 DL2 `search()` 출력 300건(R6에서 DL2 `7397c96`로 만든 파일을 재사용, 재생성하지 않음) → `_dl2`+`_cohort` 300건 통과. 분포는 R6과 같다(available 216, unavailable 84).
- DL1 producer 정렬식 2,000건 → `_dl1` 2,000건 통과.
- 새 식별자 검사가 정상 출력을 거부하지 않는 근거: DL2 `validate_record`(`psdl2/search.py:29-30`)가 `roi_id`, `product_id`에 `text()`(비어 있지 않은 문자열)를 강제한다. DL1은 manifest에서 `identifier(raw["product_id"])`를 거치고 `query_id`는 `query.product_id`다.
- 손 편집 34종: R6과 같은 reason. R6에서 bare였던 `rankings=None`/dict, 행이 문자열, `product_id` 없음·list·int 혼합, `primary=None`, `roi_id` 없음, embedding `10**400`은 각각 `*_candidate_list_required`, `*_candidate_row_required`, `*_candidate_identifier_required`, `invalid_dl2_index_embedding`이다.
- 구조 변이 battery: `dl1_search.json`, `dl2_search.json`, `dl2_index.json`의 모든 경로(목록은 앞 2개)에 삭제와 13종 값 치환, 20,272건을 seal과 `read_results` 양쪽으로 실행. 제품 코드의 bare 예외 0건. 유일한 bare 1건은 테스트 helper `reseal`이 `fingerprint`를 직접 읽는 줄(`tests/test_dl_results.py:24`)에서 났다.

### 4. N6 — mutant

`3624a4a`의 임시 복사본에서 `tests/test_numeric_images.py`, `tests/test_dl_results.py`를 실행했다.

| mutant | 결과 | 잡은 테스트 |
| --- | --- | --- |
| stem `re.escape` 제거 | killed | `test_cleanup_stem_is_literal_even_with_regex_characters` |
| symlink 검사 제거 | killed | `test_cleanup_never_follows_numeric_map_symlink_outside_root` |
| supplementary query 자기 자신 검사 제거 | killed | `test_supplementary_self_match_is_rejected_without_primary_cohort` |
| 디렉터리 1개 규칙 제거 | killed | `test_cleanup_requires_one_directory_and_previous_context_schema` |
| 이전 context schema 검사 제거 | killed | 같은 테스트 |
| `IndexError` 미처리(이전 except 목록 복원) | killed | `test_r6_empty_post_idat_iccp_*` 3건 |
| Windows 비교를 `str(Path)`로 복원 | killed | `test_cleanup_reference_comparison_is_independent_of_windows_separator` |

추가로 넣은 4개 중 3개(`candidate.parent.is_symlink()` 절만 제거, `BaseException` 삼킴, embedding `OverflowError` 미처리)는 killed, `_native_contract` fallback 제거는 survived(B2)다.

### 5. 문서

- `open_questions.md:59`, `data_contract.md:192-197`의 수락 mode는 실행 결과와 일치한다.
- Pillow 12.3.0 문장은 세 곳에 있고 lock과 일치한다.
- `data_contract.md:210` 알려진 한계 문단: pixel-limit override 없음과 Pillow 기본 89,478,485, 깨진 `eXIf` 거부, CRC가 맞는 깨진 post-IDAT `zTXt`/`iCCP` 수락, 빈 `iCCP`의 계약 오류 반환을 실행으로 확인했다. 디렉터리 전체 삭제와 완전성 미검증은 20종 context와 손 편집 결과와 맞는다. 해상도 문장은 B1 참조.
- `901255f`: `data_contract.md:58`, `open_questions.md:16, :58`이 0.1 um/pixel로 일치하고 `docs/handoff`와 충돌하지 않는다. 충돌은 `data_contract.md:210` 한 곳이다(B1).

### 6. 범위와 suite

- `91c6da2..3624a4a`의 변경 파일은 지정된 9개(`docs/review/R6_REVIEW.md` 추가 포함)다. `src`는 `dl_results.py`, `png_strict.py`뿐이고 `linepixel.py`, `electrode_map.py`, `density.py`, `measurement.py`, `numeric_images.py`, `cli.py`는 변경 없음.
- `3624a4a..901255f`는 `docs/data_contract.md`, `docs/open_questions.md`, `docs/png-input-orca-checkpoint.md`만 바꾼다.
- 전체 suite: checkout(`901255f`, `src`·`tests`는 `3624a4a`와 동일)에서 `796 passed, 2 skipped`. skip은 `tests/test_particle_sim_export_integration.py:68, :162`다. `3624a4a`를 푼 임시 복사본에서는 `793 passed, 5 skipped`(git 저장소가 아니어서 3건이 더 skip, 실패 0).
- 리뷰 후 `HEAD`는 `901255f`, 변경은 이 파일(untracked) 하나.

## 확인하지 못한 것

- Windows에서의 실행 전부(N3 포함). 다른 Pillow 버전.
- 실제 hotspot PNG. 모든 입력은 합성이다.
- 실제 DL1 CLI와 DL2 CLI의 왕복. DL2는 R6 때 `search()`로 만든 출력의 재사용, DL1은 정렬식 모사다.
- `build_handoff.sh` → `check_handoff.sh` → `validate_offline.sh`의 bundle 실행(`tests/test_handoff.py` 통과만 확인).
- `git archive` 복사본이 아닌 `3624a4a` checkout에서의 suite 실행. git 상태를 바꾸지 않기 위해 하지 않았다.
