# R6 독립 리뷰 — strict PNG reader(CB)와 R5b 후속 수정(CA)

- 리뷰어: 별도 fresh Claude Opus 5.5 세션(effort high), 읽기 전용. 작성자·coordinator가 아니다.
- 대상: `particle-sim-compare` branch `rewrite/line-pixel-rate`, diff `b5227bd2cf5931ad337424416649257fc490d076..91c6da2e117c5b7607ee7c5d45713afd60b327d5` (CA `27f462c`, CB `91c6da2`; `918a81d`의 checkpoint 문서는 범위 밖).
- 참조(읽기 전용): DL1 `feat/png-input` @ `e67a8b2` (`src/psdl1/png_source.py`, `search.py`), DL2 `7397c96` (`src/psdl2/search.py`; `e46a44e`와 diff 0줄).
- 환경: macOS, `.venv` Python 3.11, Pillow 12.3.0. 일자 2026-10-13.

Verdict: PASS-WITH-NOTES for 91c6da2e117c5b7607ee7c5d45713afd60b327d5

이 판정은 위 commit에만 유효하다.

## 차단 사항

없음. 차단 기준(조용히 틀린 mask/면적, 사유 없는 정상 입력·정상 producer 결과 거부, generator 소유가 아닌 것의 삭제, 인수 문서의 틀리거나 뒤바뀐 서술, 계약 회귀)에 해당하는 입력을 찾지 못했다.

## Notes (비차단)

중요도 순이다. N1~N3은 다음 수정 때 함께 처리하기를 권한다.

- **N1 빈 `iCCP` chunk가 IDAT 뒤에 있으면 `IndexError`가 그대로 나온다.** `src/simcompare/png_strict.py:268-281`의 except 목록에 `IndexError`가 없다. 재현: 2×1 8-bit grey PNG에 `IHDR, IDAT, iCCP(길이 0, CRC 정상), IEND`. 구조 검증은 통과하고 Pillow `image.load()` → `load_end` → `chunk_iCCP`에서 `IndexError('index out of range')`가 난다. `read_binary`도, `cli.py:107`의 except도 이를 잡지 않아 CLI에서는 traceback이 된다. mask가 틀리게 나오지는 않고 파일도 비정상이며 `b5227bd`에서도 같은 예외가 나오므로 회귀는 아니다. 다만 (a) `docs/data_contract.md:203`의 "모든 PNG 실패는 안정된 reason과 요청한 상대 경로를 포함한다"와 맞지 않고, (b) DL1 참조는 `_decode`에서 `except Exception`으로 감싸는데 port가 이를 좁혔다. 권장: Pillow 구간의 마지막 except를 `Exception` → `png_decode_failed`로 넓힌다. 구조 변이 8,000건 중 이 형태 3건(양쪽 극성 6회)만 해당했다.
- **N2 `docs/open_questions.md:59`가 이제 틀린 서술이다.** "reader는 PNG mode `1`, `L`, `I`, `I;16`만 받는다. RGB나 팔레트 PNG는 `unsupported_png_mode`로 거부된다"가 그대로 남아 있다. 후보에서는 I/I;16을 거부하고 RGB/LA/RGBA/P를 받는다. 이 파일은 diff에 없고 `docs/handoff/` 밖이라 차단으로 올리지 않았지만, tracked 문서이고 bundle에 실리며 `data_contract.md:199`의 "document 의존 없음 확인"과도 어긋난다.
- **N3 R5 디렉터리 정리는 Windows에서 동작하지 않을 것이다(추론, 미실행).** `dl_results.py:266`의 `str(Path(path).parent)`는 Windows에서 `numeric_map\c_<hex>`가 되고 `:270`의 정규식은 `/`를 요구한다(`PureWindowsPath`로 문자열만 확인). 그러면 `_replaced_map_directory`가 항상 `None`이라 이전 디렉터리가 남는다. 삭제가 덜 되는 쪽이라 안전하지만, 폐쇄망 호스트가 Windows이고 `docs/handoff/README.md:98`은 정리된다고 적는다. 권장: `PurePosixPath(path).parent.as_posix()`로 비교하거나 README에 macOS에서만 확인했다고 적는다.
- **N4 pixel 한계에 override가 없다.** DL1에는 `--png-max-image-pixels`가 있으나 port는 이를 뺐다. 설치된 Pillow 한계(기본 89,478,485 px)를 넘는 hotspot PNG는 `png_pixel_limit_warning`으로 거부된다. `b5227bd`에서는 2배까지 warning만 나고 읽혔다. `data_contract.md:201`과 `troubleshooting.md:40`에 적혀 있고 기록된 실자료 크기(약 2000 px, `open_questions.md:16`)는 한참 아래다. 약 9,459 px 정사각형을 넘는 자료가 생기면 현재는 방법이 없다.
- **N5 R12의 나머지.** 고친 것: embedding이 문자열/문자열 목록/dict/ragged이면 `invalid_dl2_index_embedding`. 남은 것: embedding에 `10**400` 같은 정수가 있으면 `OverflowError`(`dl_results.py:168`, CLI의 except 밖). `rankings`/`primary`가 list가 아니거나 행이 dict가 아니거나 `product_id`/`roi_id`가 없으면 `TypeError`/`AttributeError`/`KeyError`(base와 동일). 새로 생긴 것: 점수가 같은 두 행의 `product_id` 타입이 섞이면 `sorted`에서 `TypeError`(base는 수락). 모두 producer가 만들지 않는 입력이다.
- **N6 테스트가 잡지 못한 mutant.** 32개 중 6개가 살아남았다: stem `re.escape` 제거, symlink 검사 제거, supplementary의 query 자기 자신 검사 제거, 디렉터리 1개 규칙 제거, 이전 context의 schema 검사 제거, supplementary 중복 검사 제거(다른 검사가 대신 잡아 사실상 동치). 후보 동작 자체는 아래 표의 직접 입력으로 모두 올바름을 확인했다. symlink 검사는 `numeric_map`이 밖을 가리키는 symlink일 때 실제로 삭제를 막는 유일한 방어이므로 테스트를 추가할 가치가 있다. `colour != 3` tRNS 분기 제거 mutant도 살아남았으나 뒤의 alpha 검사가 같은 reason으로 거부하므로 동치다.
- **N7 정리 대상 디렉터리는 내용과 무관하게 지워진다.** 이전 context가 `numeric_map/<stem>_<32hex>/` 하나를 가리키면 그 안에 사용자가 넣은 파일도 함께 지워진다. 이름 규칙상 generator 소유라 의도된 범위다. 이전 context를 같은 디렉터리에 다른 이름으로 복사해 둔 경우 그 복사본은 지도를 잃는다.
- **N8 Pillow 버전.** `pyproject.toml:12`는 `pillow>=10.0`, 검증은 lock의 12.3.0 한 버전에서만 했다. 2/4-bit grey의 0..255 확장과 `P`→`RGBA` 변환은 Pillow 동작에 기대므로 폐쇄망에서도 lock 버전을 쓰는지 확인이 필요하다.
- **N9 ancillary chunk 처리의 변화.** 내용이 깨진 `eXIf`는 base에서 읽혔고 후보에서는 `png_decode_failed`다(의도된 방향, reason은 일반적). 반대로 IDAT 뒤의 깨진 `zTXt`나 zlib가 깨진 `iCCP`는 CRC만 맞으면 수락된다(pixel은 정확).
- **N10 인수 문서의 DL1 pitch.** `commands.md:202`의 "DL1은 0.1 µm/pixel, 각 변 >1000 pixels"는 DL1 `docs/USAGE.md:108-109`와 일치한다. 한편 `open_questions.md:16`은 사용자 답으로 "2×2 약 100 um/2000 px"(약 0.05 µm/pixel)을 기록한다. compare 문서의 오류는 아니고 DL1 쪽 고정값과 실자료의 관계를 사용자에게 확인할 사항이다.
- **N11 손으로 고친 결과 중 여전히 수락되는 것.** 최상위 후보 삭제, primary를 모두 지우고 `unavailable`로 바꾸기. README:67이 producer가 보장하지 않아 검사하지 않는다고 밝힌다.

## 확인한 것과 방법

임시 출력은 `/tmp/r6review.*`에만 썼다. `PYTHONDONTWRITEBYTECODE=1`, pytest는 `-p no:cacheprovider`.

### A. strict PNG

| 항목 | 방법 | 결과 |
| --- | --- | --- |
| A1 port 대조 | `git show e67a8b2:src/psdl1/png_source.py`와 `diff` | chunk 순회, CRC, Adam7 pass별 길이, PLTE/index, tRNS, APNG, trailing, zlib 종료 검사는 예외 타입만 다르고 동일. 달라진 것: pixel 한계 override 삭제(N4), `n_frames` 검사 삭제(acTL/fcTL/fdAT 구조 거부로 대체됨), 16-bit 명시 거부 추가, missing PLTE reason 분리, `except Exception` 축소(N1), 반환이 black이 아니라 white. 검증·hash·decode가 `data` 한 buffer를 쓴다(`:244-249`). |
| A2 리뷰 재현 | 자체 PNG 작성기로 11개 type × interlace 0/1, 64×64 hotspot | 612건 모두 거부, 수락 0, 비계약 예외 0. short IDAT 63/32/1/0행과 임의 절단 → `png_idat_length_mismatch`; zlib 절단·sync flush·adler 오류 → `png_invalid_idat_stream`; grey/RGB tRNS → `png_nonopaque_transparency`; index 초과 → `png_palette_index_out_of_range`; PLTE 없음 → `png_missing_palette`; fcTL 8×4/8×7/1×1/전체 → `png_apng_unsupported`; trailing → `png_trailing_bytes`; ancillary CRC → `png_crc_mismatch`. |
| A3 극성 | 같은 matrix에 임의 filter(0~4), 12개 크기, palette는 2~2^depth entry를 섞어 흰/검정 index 무작위 배치 | 528건 모두 `binary`=흰색, `binary_zero_is_marked`=검정, dtype bool, read-only. 8-bit L `{0,1}`, `{0,255}`, 전부 1, 전부 255는 이전과 같고 `{1,255}`, 2, 254, 128은 `nonbinary_nominal_mask`. |
| A3/A4 차등 fuzz | 독립 decoder(직접 inflate·unfilter·deinterlace)와 비교. inflated byte 변이 6,000건(다중 IDAT, 임의 ancillary 포함) | 수락 3,650 / 거부 2,350, 불일치 0. |
| A4 Pillow 작성 파일 | mode 1/L/RGB/LA/RGBA/P × optimize, compress 0, text+dpi, bits 1/2/4, icc, gamma | 78건 모두 정확. palette 순서를 바꾼 파일도 정확. |
| A6 거부 형태 | 구조 변이 8,000건(byte flip, 절단·삽입, IHDR 필드, CRC를 맞춘 payload 변이, chunk 순서·중복·삭제·추가) | 수락 3,450건은 독립 decoder와 모두 일치. 거부는 모두 상대 경로를 포함한 `NumericContractError`, 예외는 N1의 6회. |
| A5 hash | 틀린 hash 22개 type → `input_hash_mismatch`. mutant "hash 미적용"은 `test_asset_hash_checked`가 잡음 | PNG는 decode하는 buffer의 hash를 검사. NPY 분기는 diff상 변경 없음. |
| A6 16-bit, 전역 상태 | colour 0/2/4/6 depth 16, Pillow가 쓴 I;16과 I → `png_unsupported_bit_depth`. `MAX_IMAGE_PIXELS` None/0/정확히 한계/한계-1/절반 미만, `LOAD_TRUNCATED_IMAGES=True` 상태 | 한계 None/0 → `png_pixel_limit_disabled`, 초과 → warning/error reason, 모든 경로에서 세 전역값과 `warnings.filters` 복구. 2^31-1 정사각형 header는 inflate 전에 즉시 거부. 16-bit에 의존하는 tracked fixture·코드 없음(tracked PNG는 `tests/fixtures/numeric_v2/mask.png` 하나, 문서는 N2). |
| A7 전후 동일성 | `git archive b5227bd`와 `91c6da2`를 임시 디렉터리에 풀어 별도 프로세스로 실행 | fixture의 PNG/NPY 전체 × 두 극성 × hash 없음/맞음/틀림의 결과(예외 메시지 포함), `load_dataset` 2종, `demo-data`→`validate-data`/`compare`/`electrode-map`(config 2개) 출력 88개 파일의 SHA256이 모두 같다. |

### B. dl_results

| 항목 | 방법 | 결과 |
| --- | --- | --- |
| B1 producer 보장 | `psdl1/search.py:138`(반올림 뒤 `(-score, product_id)` 정렬, 후보 ID 유일, `query_itself` 제외, 순위가 있을 때만 available), `psdl2/search.py:124-131`(`(-score, roi_id)`, primary 유무로 status, index의 roi_id 유일) | R3 검사와 일치. DL2 정렬 검사는 primary에만 적용되어 producer보다 약하다. |
| B1 실제 DL2 `search()` | DL2 venv에서 정수 벡터로 동점·±1·반올림 동점을 만든 index 300개, cohort 5종, topk 1/2/5/100, expand 유무 → C의 `_dl2`+`_cohort` | 300건 모두 통과. 분포: available 216(동점 포함 64), unavailable 84(그중 supplementary 있음 36). |
| B1 DL1 | producer의 정렬식 그대로 2,000건(동점, -0.0, 대소문자·비ASCII ID, topk 절단, 빈 목록) → `_dl1` | 2,000건 통과. 실제 DL1 CLI는 실행하지 않음. |
| B1 손 편집 | 순서 뒤집기, 중복, query 자신, status 불일치 | base는 수락, 후보는 각각 `*_matches_not_sorted`, `*_duplicate_candidate`, `*_query_self_match`, `*_status_matches_mismatch`. |
| B2 정리 범위 | 조작한 이전 context 20종: `../` 경로, 절대 경로, 다른 stem, 사용자 디렉터리, `numeric_map` 자체, 두 디렉터리, 대상이 symlink, `numeric_map`이 밖을 가리키는 symlink, 대문자 hex, 개행, 더 깊은 경로, 정규식 문자가 든 stem(`a.+(b)[c]`, `.*`), list, 다른 schema | 이름 규칙에 맞는 한 디렉터리 외에는 아무것도 지워지지 않음. 밖의 victim 디렉터리 손실 0. 안쪽 symlink는 링크만 지워짐. |
| B2 순서 | `write` 실패, 두 번째 NPZ 실패, export 누락을 주입 | 이전 context와 지도 모두 보존, 새 디렉터리만 정리. 삭제는 새 context 쓰기 성공 뒤에만 일어난다(`:360-361`). |
| B3 fixture | `git show b5227bd:` 대비 | `numeric_context.json`은 record 4개에 `numeric_status`/`numeric_reasons`만 추가, 나머지 동일. `exchange.json`은 그 hash 한 줄만 변경이고 실제 파일 hash와 일치. 재생성한 context는 지도 디렉터리 이름만 다르고 동일. negative fixture는 suite에서 원래 사유로 실패. |
| B4 R12 | 문자열 embedding 등 → 이름 있는 오류 | N5 참조. |

### C. 인수 문서

- 극성: `README.md:77-78, :86`, `commands.md:202`, `troubleshooting.md:3, :13, :41` 모두 layout/electrode·CNT = 검정 패턴/흰 배경(DL1), hotspot = 흰 패턴/검정 배경(`binary`). 뒤바뀐 곳 없음. DL1 참조 코드도 `mask = black`이다.
- DL1 pin: `e67a8b2`는 `feat/png-input`에만 있고 `main`의 조상이 아님. `1f9d005..e67a8b2`는 `docs/USAGE.md`만 변경.
- `psdl1.import_png` 명령: `--root`, `--out`, `--inspect-grids`(nargs +), `--export`, `--metadata`, `--candidate-grid`, `--verify-bundle`(단독 사용)이 실제 `--help` 및 USAGE와 일치. 폴더·파일명 규칙도 USAGE와 일치.
- Windows 블록은 모두 "미실행"으로 표시. 실자료 검증·전기적 검증·수율·밀도 환산 주장 없음, 보류 기능(겹쳐 보기, 뷰어, 밀도 환산, 수율)을 제공한다고 적은 곳 없음.
- `build_handoff.sh`/`check_handoff.sh`의 gate 이름 변경은 서로 같고 옛 이름은 저장소에 남아 있지 않다. `tests/test_handoff.py` 통과.

### D. 범위와 suite

- diff의 `src` 변경은 `dl_results.py`, `numeric_images.py`, `png_strict.py`(신규)뿐. `linepixel.py`, `electrode_map.py`, `density.py`, `measurement.py`는 변경 없음.
- 전체 suite: `761 passed, 2 skipped in 36.76s`. skip은 `tests/test_particle_sim_export_integration.py:68, :162`(`PARTICLE_SIM_ROOT` 미설정).
- mutant 32개(PNG 20, dl_results 12)를 임시 복사본에서 실행: 25개 killed, 6개 survived(N6), 1개 동치.
- 리뷰 후 `HEAD`는 `91c6da2e…`, 변경은 이 파일(untracked) 하나.

## 확인하지 못한 것

- Windows에서의 실행 전부(N3 포함), 다른 Pillow 버전(N8).
- 실제 hotspot PNG의 mode·bit depth·크기. 모든 입력은 합성이다.
- 실제 DL1 CLI의 train → search → seal 왕복과 실제 DL2 CLI. DL1은 정렬식 모사, DL2는 `search()` 함수 호출까지다.
- `build_handoff.sh` → `check_handoff.sh` → `validate_offline.sh`의 bundle 실행(테스트 통과와 diff 대조만).
- loader를 거친 non-synthetic 경로. `real_input_requires_approved_adapter`로 막혀 있어 `read_binary` 수준에서만 확인했다(R5b R9와 같음).
- thread 동시 호출에서의 `warnings` 필터 복구.
