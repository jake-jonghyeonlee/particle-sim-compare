# 변경점 ROI (change-roi) — Run 요약과 DL2 연결 기록

Run `20261026-change-roi` (Paseo). 기준 작업 순서: T6 `candidates-change` → T7 직접 대응 → T7b `alignment.html` → T8 변경 지도 순위 → T8b 한쪽 조합 → T9 문서·ETX 실행기·전체 흐름 게이트. 이 문서는 T9가 쓴다.

## 확정 결정

1. 변경점 ROI(`candidates-change`)가 4회차 ROI 선정을 대체한다. 기존 `candidates`(max_count)는 취약점 ROI 자리로 남기되 **보류**한다(ΔV 가중 설계 중).
2. 정렬: CNT pcnt/gcnt/ocnt 3층을 한 offset으로 동시에 정렬한다(층 합집합 금지). 탐색 반경 예시는 30 µm. 2×2 반복 패턴은 `peak_ambiguous`와 대안 offset으로 표시한다. 저신뢰도여도 best-effort로 진행하고 `alignment.html`에 보인다.
3. 지름은 5 µm 하나다. top-k는 방향별이다. 모든 새 인수는 필수이며 기본값이 없다.
4. 순위 = 층별 hotspot **합집합 변화** 면적(6회차 Δ_union·보고서 기준과 같다). 조합별 잔차 합, 바뀐 조합 수, `union_delta_um2`는 속성이다.
5. 합집합 = 짝지어진 조합 + 한쪽에만 있는 조합 + 층이 바뀐 조합. 새 전극(배선)의 조합도 비교 대상이다(`appeared`/`disappeared` + `new_electrode`/`removed_electrode`). `electrode_set_differs`는 정보 note다.
6. 층 변경은 쌍 하나(a=L1, b=L2)가 아니라 한쪽 조합 둘(L1/L1, L2/L2)로 다룬다. 층 사이 합집합을 만들지 않는다(C8).
7. 직접 대응(`direct/roi_correspondence.json`)으로 DL2를 건너뛰고 6회차로 간다. 다른 제품 탐색이 필요할 때만 DL2 경로(5회차 `confirm-pairs`)를 쓴다.
8. 배선 변경 면적(`wiring_change_um2`)은 계산하지 않는다(보류).

## 산출물과 기준 commit

| 산출물 | 기준 | sha256 |
|---|---|---|
| 소스 `src/simcompare`, 문서 | T12 commit(이 문서가 들어간 commit). 소스는 T10b commit과 같다. T10 값은 T10b 소스 수정 뒤 낡았고 아래 값으로 바꿨다 | — |
| `bundles/simcompare_job.py` | T12 commit(R1·R3·T10b 포함). 묶인 소스 해시(`PAYLOAD_SHA256`) `d3d167426d5e21a1aef3da2ea3b576e99d3210ee84edb5e44c0f93872f87aced` | `e6b91eca33e15071249909469f3f5f8fd5f8d19d0ea0bb915c2ced0a0f88e446` |
| `bundles/etx/simcompare_job_1_import_hotspots.py` | T12 commit(같은 `PAYLOAD_SHA256`) | `e681db90379941887384dd2e590ec369941dbc941e20f94dad1e3e5ff8789568` |
| `bundles/etx/simcompare_job_2_validate_compare.py` | 〃 | `9d8b57a2ab38792ea29107162fdd1378b3d2346c10d7fd712ebdf0d9d5c7a3dd` |
| `bundles/etx/simcompare_job_3_import_electrode_assets.py` | 〃 | `92ead42733c6e93379a9fa438454ff6047b69a86d4687a77f986aa193502e5c4` |
| `bundles/etx/simcompare_job_4_candidates.py` | 〃 (`candidates-change`) | `c04e5154658e0fe851677c944fc5b1c6d076ce0467c43d5e219685d732d6bcdf` |
| `bundles/etx/simcompare_job_5_confirm_pairs.py` | 〃 (머리 주석: 배치 봉투를 방향마다 따로 또는 하나를 두 `--match`에 준다) | `8de37ead523d5b80802219d259cf19b6461404e823e2eb79f6bfcba174545315` |
| `bundles/etx/simcompare_job_6_compare_windows.py` | 〃 | `245ecfd752a118074b9876c5c6eb8a87bbb0e489a887f047be97f13edcda0dd9` |
| `bundles/windows_cp312_win_amd64.zip` | **이전 revision**(`d774920`, 2026-10-15). **이번 기능을 포함하지 않는다** | `103d4cb95b3f99308682b2de61c3b21201a07570d82c43a449f59445f1678750` |
| `bundles/updates/simcompare-0.1.0-py3-none-any.whl` | **이전 revision**(`edc7d56`, 2026-10-21). **이번 기능을 포함하지 않는다** | `062deb111d61c1b141d50d31277d31162cb32469552990cbf9518067bd5c85a7` |

Windows zip·wheel은 T9에서 다시 만들지 않았다. Windows에서 `candidates-change`를 쓰려면 별도 Task로 다시 묶어야 한다. 실행기 해시는 T9 commit의 값이며 소스를 고치면 `scripts/build_etx_stage_runners.py --overwrite`와 `scripts/build_single_file.py`로 다시 만들고 이 표를 갱신한다.

## DL2 인터페이스 요구 (기록용)

- 질의: `review_roi_v1`의 manual 행(`reference_export/`, `target_export/review_rois.json`). 검색 대상: `*_index_export/review_rois.json`. 매칭 쌍은 `candidate_queries_v1.json`의 `match_files`가 알려 준다.
- 출력: 현 `NCC_SCHEMA`(`psdl2_baseline_ncc_v1`, `mode=all_ready`, `inputs.query_export_sha256`/`index_export_sha256`, `results[].query.roi_id`/`center_xy_um`, `primary[].rank`/`center_xy_um`/`frame_id`/`product_id`).
- 향후 요청(아직 DL2에 없음): (1) `expected_center_xy_um` 힌트(정렬 offset으로 계산한 기대 중심 근처만 검색), (2) CNT 층별 채널(층 합집합 대신), (3) `--window-um`을 `candidates-change --window-um`과 일치시키는 옵션.
- `docs/handoff/README.md`는 T9·T10에서 고치지 않았고, T11에서 DL2 pin을 `77970b76625ed3ef38e888add769698b076293ae`(이전 `f41d42709f4f17e0e7891b1c50d0547616b8f791`)로 갱신했다.

### T10: DL2 요청 R1~R5 처리 상태 (DL2 `PLAN_v3_db_search.md` §5)

| 요청 | 상태 | 내용 |
|---|---|---|
| R1 `mask_derivation` | **완료(T10)** | `candidates-change`가 질의 export 2개·index export 2개 폴더마다 `mask_derivation.json`을 쓴다(`export-template-rois`와 같은 writer·규칙, 필수 인수 `--mask-derivation-rule between_stack_order_v1`). 경로는 `candidate_queries_v1.json`의 `mask_derivation_files`. DL2 `parse_mask_derivation` 규칙은 테스트 안에 다시 구현해 검사했다(DL2 코드를 import하지 않음) |
| R2 변경점을 `review_roi_v1` manual 행으로 | 충족(T6) | DL2 원문 그대로 변경점이 `review_roi_v1` 행인 `reference_export/`·`target_export/`로, 검색 대상은 `*_index_export/review_rois.json`으로 나간다 |
| R3 `psdl2_search_batch_v1` 읽기 | **완료(T10)** | `confirm-pairs --match`가 배치 봉투를 읽는다(질의 해시 `rankings[i].query_export_sha256`, `index_sources[].export_sha256`·`product_ids`, `search_scope`). NCC 경로는 바이트·동작 그대로 |
| R3e 출력 | **확인** | `roi_correspondence_v1`은 쌍 단위(a=reference, b=target, producer `manual`)이고 `compare-windows`가 그대로 읽는다. 사이드카에 `match_schema`, `rank`, `search_scope`, index source 해시를 남긴다 |
| R4 | 보류 | 이번 Run에서 하지 않는다 |
| R5 DL2 pin | **완료(T11)** | DL2 `feat/dl2-db-search`를 `feat/dl2-patch-dataset`로 fast-forward 병합하고 origin에 push했다(2026-10-27). `docs/handoff/README.md`의 DL2 pin과 `--dl2-ref`를 `77970b76625ed3ef38e888add769698b076293ae`로 갱신했다 |

T10b 수정(T10 리뷰 FAIL blk:1): DL2 문서의 DB 는 제품별 template export 이므로 `index_sources[].export_sha256`이 `*_index_export` 해시와 같을 수 없다. 후보 제품 검증은 `product_ids` 기준이고 해시 대조는 등급(`candidates_index_export`/`product_ids_only`/`unverifiable_single_export`/`no_source_for_index_product`)으로만 남는다. 일치 source 가 없다는 이유로 종료 2 를 내지 않는다. 한 봉투에 두 방향 질의가 들어 있어도 같은 파일을 두 `--match`에 줄 수 있다. `index_sources == "single_export"`일 때는 봉투에 export 해시가 없어 대조하지 못한다(`inputs.index_sha256`은 index 체크포인트 파일의 해시다). DL2가 이 경우에도 export 해시를 싣도록 바뀌면 `confirm_pairs._index_grade`에서 대조를 켠다.

## compare ↔ DL2 최종 연결 체크 목록

DL2를 실제로 연결할 때 하나씩 확인한다. 1~10은 계획 초안(PLAN_v1_draft §7), 11~14는 이 Run에서 더했다. 마지막 열은 T12(`tests/test_dl2_e2e.py`, 실제 DL2 worktree 코드 `feat/dl2-db-search @ 77970b7`로 실행)의 결과다.

| # | 확인 항목 | T12 결과 |
|---|---|---|
| 1 | `match_inputs`/`match_files`가 가리키는 `review_rois.json` 네 개가 실제로 있다. | **확인.** `match_files` 두 방향의 파일이 모두 있다. |
| 2 | 질의 export의 ready 행은 valid 위치, `source_kind=manual`, 비어 있지 않은 `role`과 `actual_layer`를 가진다. | **확인.** 두 질의 export의 모든 행. DL2 `search`가 그 행을 질의로 읽었다. |
| 3 | index coverage가 complete다(`index_coverage_incomplete` 경고 없음). | **확인.** 경고 코드에 없다. |
| 4 | 정렬 상태가 세 곳에서 같다: `candidate_queries_v1.json`의 `alignment`, `alignment.json`, `direct/direct_pairs_sidecar.json`의 `alignment`. | **확인.** 상태 `aligned`, offset `[0.7, -0.4]`가 세 곳에서 같다. |
| 5 | target→reference 방향 질의 중심 = reference→target 방향 중심 + offset(±pitch/2 이내). | **확인.** 합성 값은 오차 0.05 µm 이내다. |
| 6 | `baseline_ncc`를 두 방향 모두 돌렸고 `inputs.*_export_sha256`이 현재 export 파일과 같다. | **확인.** 실제 `psdl2.baseline_ncc --all-ready`를 두 방향 모두 돌렸다. 해시가 같고 `confirm-pairs --list`·accept가 통과했다. |
| 7 | `confirm-pairs --list`가 `candidate_id` 목록을 찍고, `decisions.csv`의 id가 그 목록에 있다. | **확인.** 배치 봉투 세 종류와 NCC 모두에서 첫 usable 후보를 accept 해 종료 0. |
| 8 | `compare-windows`가 확정 쌍 파일로 끝까지 돈다(종료 코드 0 또는 3과 그 사유 확인). | **확인.** 종료 3, `incomplete_local_delta`. 합성 hotspot이 5 µm뿐이라 다른 지름은 `source_coverage_incomplete`이고, ready 쌍마다 5 µm 행은 available이다. |
| 9 | 실행기 로그에 `payload_sha256=`, numpy·Pillow 버전, `<명령> exit N`이 있고 N이 0 또는 3이다. Python 3.9 실행기 결과가 현재 인터프리터와 같다(`alignment.html` 제외, 아래 한계). | **T12에서 못 봄.** `tests/test_etx_chain.py`가 본다. T12는 단일 파일 실행기를 쓰지 않고 CLI를 직접 불렀다. |
| 10 | DL2 pin과 불변 bundle: DL2 pin이 `docs/handoff/README.md`와 같고 Windows zip·wheel이 바뀌지 않았다. | **일부.** zip·wheel 해시는 위 표와 같다(바뀌지 않음). pin은 T11에서 `77970b7`로 갱신했다(이 연결을 검증한 commit과 같다). → **확인.** |
| 11 | (X3) 질의 export 행의 `roi_id`와 `direct/roi_correspondence.json`의 `a.roi_id`/`b.roi_id`가 같은 행(중심·endpoints 일치)을 가리킨다. | **확인.** ready 쌍마다 중심·endpoints가 질의 export 행과 같다. |
| 12 | 정렬 부호식 `p_target = p_reference + offset`이 `alignment.json`, 직접 쌍 b 중심(= a 중심 + 원점 차이 + offset), 질의 중심에서 같은 방향이다. | **확인.** `alignment.json`·질의 중심·직접 쌍 b 중심. |
| 13 | 직접 경로로 `compare-windows`를 실행한다: `--correspondence data/changes/direct/roi_correspondence.json`. blocked 쌍이 있으면 `blocked_pairs_present`로 막힌다(사유는 사이드카). | **일부.** 실행해 종료 3(`incomplete_local_delta`, 8번과 같은 이유). 이 세계에는 blocked 쌍이 없어 `blocked_pairs_present` 경로는 못 봤다. |
| 14 | 한쪽 조합 쌍(endpoints 없음)이 `compare-windows`에서 group 합집합 Δ로 계산되는지, 층 변경 쌍이 `one_sided_layer_absent_*`로 막히는지 확인한다. | **T12에서 못 봄.** 이 세계에는 한쪽 조합·층 변경이 없다(`tests/test_direct_pairs.py`가 본다). |
| R1 | `mask_derivation.json`을 DL2가 받는다. | **확인.** 실제 `parse_mask_derivation`이 질의 export 두 개의 파일을 `--ignore-mask-derivation` 없이 받았고 `mask_derivation_*` 제외가 없다. `*_index_export` 두 개의 파일도 DL2 색인 manifest에 쓰였다. 음성 대조: 질의의 `rule_version`만 바꾸면 모든 후보가 `mask_derivation_mismatch`로 빠진다. |
| R3 | `psdl2_search_batch_v1`을 읽는다. | **확인.** 실제 `psdl2.search --all-ready` 출력 3종(템플릿 DB 방향별 봉투, 템플릿 DB 한 봉투 두 방향, `*_index_export` DB)을 `--list`, accept, `compare-windows`까지 읽었다. 등급은 `product_ids_only`, `product_ids_only`, `candidates_index_export`다. `rankings[i].query_export_sha256`이 질의 export sha256과 같다. |
| R3e | `roi_correspondence_v1` 출력이 `compare-windows`에서 읽힌다. | **확인.** 쌍 단위(a=reference), `producer.kind=manual`. |
| adv:3 | template export DB 후보의 `frame_id`·중심과 `*_index_export`의 frame·중심이 같다. | **확인.** 같은 제품의 두 export 행이 `frame_id`·origin·pitch·shape·axis_order·origin_convention이 같고 같은 case·전극의 layout asset 해시가 같다. 두 DB가 돌려준 후보는 같은 `frame_id`이고 같은 격자(frame origin + 정수 × step) 점이다. |

## 검증한 것

- 전체 흐름 게이트 `tests/test_etx_chain.py`: 합성 두 모델(알려진 offset, 심은 변경)을 ETX 폴더 구조로 만들고 재생성한 실행기 1·2·3·4·6회차를 BASE_DIR 지정·상대 경로·새 `--out` 규칙으로 차례로 실행한다. 5회차는 합성 NCC JSON으로 `confirm-pairs --list`까지. 현재 인터프리터와 `SIMCOMPARE_ETX_PY`(Python 3.9) 모두에서 돈다. `SIMCOMPARE_REQUIRE_ETX_PY=1`이면 ETX 인터프리터가 없을 때 실패한다.
- 잘못된 DL1 root(T10에서 바뀜): 없거나 폴더가 아닌 `--cnt-from-dl1-root`는 쓰기 전에 종료 코드 2(`cnt_dl1_root_missing`)이고 `--out`을 만들지 않으며 ETX 실행기가 멈춘다(뒤 작업은 시작하지 않는다). 로그의 `오류:` 줄과 인수·절대 경로 줄로 원인이 보인다. root는 있는데 모델 폴더·`cno/`가 없으면 종전처럼 정렬 실패(종료 코드 3, `cnt_missing_*`)다.
- DL2 연결(T10, `tests/test_dl2_link.py`): `mask_derivation.json` 4개가 DL2 `parse_mask_derivation` 규칙(테스트에 다시 구현)을 통과하고 같은 입력이 같은 바이트를 만든다. 합성 `psdl2_search_batch_v1`(`index_sources`가 배열인 형태와 `single_export`, `product_ids: null` 포함)로 `confirm-pairs --list`·accept·`compare-windows`·해시 불일치(종료 2, 아무것도 쓰지 않음)·사용 불가 후보 사유를 확인했다.
- 실제 DL2 연결(T12, `tests/test_dl2_e2e.py`, 선택 실행): `SIMCOMPARE_DL2_SRC`(DL2 `src`)와 `SIMCOMPARE_DL2_PY`(DL2 인터프리터)가 둘 다 있을 때만 돈다. `SIMCOMPARE_REQUIRE_DL2=1`이면 없을 때 실패한다. DL2는 하위 프로세스(`python -m psdl2.<모듈>`, `PYTHONPATH=<src>`)로만 부르고 import하지 않으며, 산출물은 pytest `tmp_path`에만 쓴다. 흐름: 합성 두 모델 + DB 전용 제품(`tests/dl2_e2e_helpers.py`; 전극 layout은 변경 영역을 지나는 막대, target 쪽은 CNT와 같은 offset으로 이동) → `export-template-rois` 제품별 3개 → `candidates-change`(top-k 1) → DL2 `patches`(random·grid) → `make_split` → `train`(CPU 1 epoch) → `index` → `search --all-ready`(DB는 템플릿 export, 그리고 `*_index_export`의 grid patch 색인 둘 다; 질의 두 방향을 봉투 하나에 담은 실행도 한 번) → `confirm-pairs --list`/확정 → `compare-windows`(확정 쌍, 직접 대응) → `baseline_ncc` 두 방향 → `confirm-pairs`. 전체 약 10초.
- `--align-search-um` 안내 통일(T9 리뷰 권고): 최소값 = pixel pitch 2배 이상(코드 검사), 권장 = 예상 레이아웃 이동과 반복 패턴 주기를 모두 덮는 값(예시 30 µm). 실행기 머리 주석·`--help`·`docs/usage_reference.md`가 같은 뜻이다.
- C6 필터(T9 리뷰 권고): 한쪽 조합의 반대 dataset 필터는 group 튜플 `(source_id, process_step_id, exposure_id, unit_id)` 단위로만 정한다. `(S1,P1),(S2,P2)`에 hint `(S1,P2)`이면 필터를 비우고 `filter_unresolved_<a|b>`다(`tests/test_direct_pairs.py`).
- 층 변경 CLI 전체 흐름(`tests/test_direct_pairs.py`): 두 모델의 stack 순서가 달라 분석 층이 L2→L1로 바뀐 사례에서 직접 쌍 둘이 `one_sided_layer_absent_*`로 blocked다.

## 미검증 항목

- 실데이터 규모의 시간·메모리(`candidates-change`는 합성 소형 자료만 돌렸다).
- 예시 탐색 반경 30 µm(0.1 µm pitch에서 300 px)의 탐색 비용·메모리는 측정하지 않았다.
- (T12에서 해소) 실제 DL2 코드(`feat/dl2-db-search @ 77970b7`)로 `mask_derivation.json`·배치 봉투를 읽었다. 위 표와 아래 "검증한 것"을 본다. DL2 pin도 T11에서 `77970b7`로 갱신했다.
- T12는 합성 소형 자료(모델 2개 + DB 전용 제품 1개, CPU 학습 1 epoch)로 연결만 증명한다. 순위 품질(정답 위치가 상위에 오는지)은 단언하지 않았고 DL2 후보는 격자 점(1 µm 간격)이다.
- `index_sources == "single_export"` 봉투에서는 index export 해시를 대조하지 못한다(위 T10 절).
- ETX Linux 서버에서의 실제 실행(Python 3.9 인터프리터는 macOS의 3.9 가상환경으로 대신 확인했다).
- 한 데이터셋에 source group이 여럿인 경우(변경 지도 합집합은 모든 group을 모으고 `compare-windows`는 GroupKey 하나만 잡는다).
- 반대 dataset의 group 필터가 여럿인 경우의 CLI 전체 흐름(필터 선택 규칙은 함수 단위 테스트만 있다).
- `alignment.html`의 PNG 압축 바이트는 zlib 구현에 따라 환경마다 달라 sha256이 다를 수 있다. 다른 출력 파일은 3.9와 3.11에서 바이트가 같다(테스트).
- 중간에 하드 실패하면 `alignment.html`은 없다.
- Windows zip·wheel은 이번 기능을 포함하지 않는다.

## change-roi-v2 (2026-10-28)

기준 `feat/template-export` @ `0717947`, 계획 v1.3. 모든 Task는 독립 리뷰 PASS 뒤 브랜치 사슬(t1 → t2 → t4b → t3a → t3b → t6)에 쌓였다.

**문제(사용자가 `alignment.html`로 본 것).**
1. CNT 정렬: reference frame 2004 px, target frame 2014 px. pitch는 같고(0.1 µm) 제품 unit size가 조금 달라서 생긴 차이다.
2. 중앙 2×2 frame을 쓰다 보니 ROI 후보가 많이 겹친다(층마다 따로 병합, 창 격자 W/4 에서 겹침 50 % 미만 창은 남음).
3. ROI 상세에 후보별 CNT 정렬 crop만 있어 frame 어디인지 모른다. 전 층 겹침 비교, layout 변경 강조, ROI 위치 표시가 필요하다.
4. `alignment.html` "기여 조합" 상세가 이해되지 않는다. 한글로 바꾸고 판단에 확실한 요소만 남긴다.
5. 문제 1과 연계: unit size가 다를 때의 비교 방식.

**사용자 결정(2026-10-28).**
- D0: 새 인수는 선택 인수이고 기본값은 이전 동작이다. 인수를 준 경우에만 `change_args`·`execution.json`의 `args`에 키를 적는다.
- D1: frame 크기 차이는 info note `frame_shape_differs`로만 기록한다. 경고나 `aligned_uncertain` 사유가 아니다.
- D2: `--merge-scope {layer,all}`과 `--suppress-overlap X`를 둘 다 추가한다. D3: ETX 예시는 `--merge-scope all --suppress-overlap 0.3`이며 예시 값이다.
- D4: 전 층 겹침 색은 side 2색(reference 파랑 계열, target 주황 계열) × 층 명도다. D5: 새 그림은 기본 포함이다. D6: `area_reference_um2`·`area_target_um2`를 JSON에 더한다.
- Q4: `shifted`·`reshaped`·`mixed_undetermined`는 HTML에서 "형상 변화" 하나로 합치고 IoU 값은 HTML에서 뺀다(JSON은 유지). 리뷰 뒤 appeared/disappeared 문구는 "창 안 새로 생김 / 창 안 사라짐"이 되었다(창 단위 판정).
- 문제 5의 1단계는 "공통 영역 교집합 명시 + 크기 차이 기록"까지다. resize/warp는 하지 않는다(`docs/open_questions.md` N1).
- 화면 검토(목업 v2·v3): 정렬 상태 배지, ROI 목록, 사분면 offset의 허용값 초과 강조, 공통 영역 밖 회색, 층 명도 최대 6단계, 한글 표의 행 강조·10행 접기. V1: CNT 정렬·정렬 진단을 앞쪽 절로. V2: ROI 상세에서 CNT 정렬 crop을 뺀다. V3: ROI 안에 불량 조합이 있는 층마다 비교·reference·target 한 묶음(최대 4층)이며 바탕은 그 층 전극 형상이다(`--electrode-png-root`, 생략 시 `--cnt-from-dl1-root`).

**Task와 후보 commit.**

| Task | 내용 | 후보 commit |
|---|---|---|
| T1 | 기준선 해시 테스트, frame 크기·비교 영역 기록 | `faffe9ea7d2698e3a753f78276f82b1651aae14e` |
| T2 | `--merge-scope`, `--suppress-overlap` | `aecfcfcbc77f934222183ffff1e21e226f2ad0d3` |
| T4b | case 면적 키, 계약 문서 | `64ae3c03a67c01055c9d148fe8962762d87dd4c8` |
| T3a | `alignment.html` 재구성·그림·한글 표 | `646f447847bb361a590f6171633c3eb8b33ed782` |
| T3b | `--electrode-png-root`, ROI 층 묶음 | `4a59ff8e15539a53f0ba3394f194e1d92e1f7abc` |
| T6 | T3b 리뷰 권고(admission 추정·빈 인수 거부·캡션 사유), 문서, ETX 예시, bundles | 이 브랜치 head(`change-roi-v2/t6`) |

**보류.** 2단계(E): unit 별·4분면 offset 보정과 T5 설계 메모는 사용자의 실자료 `alignment.json` 진단을 받은 뒤 착수 여부를 정한다. 지금은 frame 크기가 다르면 공통 영역만 비교한다(resize 없음).

**남은 비차단 권고.** T2 adv:4: `--merge-scope all`의 층을 넘는 병합은 후보 수 N에 대해 O(N²)이다. 실자료 후보 수가 크면 다시 확인한다.

## roi-detail (2026-10-29)

**사용자 원문.** "4회차 실행했는데 5. roi 상세 순위순에 위치자체가 회백색 이미지로 나와. 그리고 정렬자체를 cnt으로 하다보니 overlap이 최대인 부분의 정렬이 완벽하게 맞지는 않네. 전극별 layer 이미지를 쓰니까 이걸 정렬 보조 수단으로 이용하는 방법이 있는지 확인해봐 … roi를 고정하다보니 case가 너무많이 나와. 좀더 roi크기를 줄여야할것 같아. 지금 roi는 140개 case나 나와" → "일단 10으로 줄여서 진행해볼게." 그다음 "병합하고 추천대로".

**결정.** D1 = 진단 전용(A)을 먼저 하고, 국소 offset 을 계산에 반영하는 B 는 실자료 진단을 본 뒤 별도 계획으로 보류한다. D2 = 인수 `--local-align-um`(선택, 기본값 없음, ETX 예시 1 µm). D3 = 국소 offset 이 전역과 허용값 이상 다르면 정보 note 만(경고·종료 코드 변화 없음). D4 = "국소 보정 후" 세 번째 그림은 넣지 않고 표만 넣는다(HTML 크기). 초안의 A2(대안 offset 의 전극 Dice 재점수)와 전역 offset 수동 지정은 이번에 넣지 않았다.

**Task.**

| Task | 내용 | 후보 commit |
|---|---|---|
| T1 | 5절 위치 그림: 변경 위치와 사분면 선(CNT 패턴 대신), ETX 4회차 예시 창 10 µm | `13f0bb6ba3866469572def0dad69656614cd41d4` |
| T2 | 국소 정렬 진단(`local_align.py`, `roi_local_alignment.json`, `--local-align-um`) | `roi-detail/t2` 브랜치 (기준 `508744d`) |
| T3 | HTML 표·요약 줄·각주, 위치 그림 캡션, 문서, ETX 예시, bundles, 리뷰 권고 테스트 | `roi-detail/t3` 브랜치 head |

**경과.** Fable 초안(1회) → Lead 검토 → Astra 점검(BLOCKING X1–X4: admission 공유, 인수 생략 불변 증명, 이동 valid 의 가짜 내부 최적, 평탄·빈 target 의 ok) → 계획 v1.2 → T2 구현 → Opus 리뷰 FAIL(ROI 별 shift 배선 테스트 없음 등) → 수정 → 재리뷰 권고를 T3 테스트로 반영.

**알고리즘 요약.** ROI 상자(창 + 50 % 여백)마다 묶음 층 전극 합집합과 CNT 전 층을 합쳐 pooled Dice 를 정수로 센다. 전역 offset 둘레 ±r px(체비쇼프)의 모든 d 를 같은 비교 영역 Ω 에서 비교한다. 상태 사다리와 JSON 키는 `docs/data_contract.md` 의 `roi_local_alignment.json` 항목이다.

**상수(시험값).** `MIN_SUPPORT_PX` 200, `MIN_RING_SUPPORT_PX` 100, `AMBIGUITY_DELTA` 1/100, `MAX_LOCAL_SEARCH_PX` 30(3 µm @ 0.1 µm). 근거는 `local_align.py` 머리 주석이다: ROI 상자 한 변이 보통 200 px 이고 전극 선폭이 수 px 이므로 전경이 200 px 미만이면 Dice 가 한 픽셀 차이로 뒤바뀐다. 모두 실자료로 다시 확인한다.

**r 하한이 2 px 인 이유.** ambiguous 판정은 최적과 체비쇼프 거리 2 이상인 후보와 비교한다. r = 1 이면 그런 후보가 없어 평탄·주기 구조를 가려낼 수 없다. 그래서 round(R / pitch) < 2 는 종료 코드 2(`local_align_um_invalid`)다.

**시간.** 점수를 직접 합으로 세므로 후보 수 (2r+1)² 에 비례한다. 실측: r = 10, ROI 5 개, 2 층, ETX Python 3.9 에서 0.38 s. 최악 추정(ETX 3.9, ROI 20, 7 층, 상자 400 px, r = 30)은 약 3 분이다(추정, 실측 아님).

**오류 처리.** 진단 계산 중 예상하지 못한 예외는 실행 전체를 종료 코드 2 로 끝낸다(진단이 조용히 사라지지 않게 한다). 쓰기 중·직후 실패는 이 실행이 만든 파일을 지운다. 기존 출력 폴더는 거부한다.

**3.9 고정 해시.** `tests/test_roi_local_alignment_baseline.py` 의 해시는 처음 numpy 2 에서만 맞았다. 원인은 입력 PNG 의 sha256 이 Pillow/zlib 버전(3.11 대 3.9.14/Pillow 10.1.0)에 따라 달라지는 것이다. 지문 키 값을 placeholder 로 바꾸고, 각 지문이 입력 파일의 실제 해시와 같은지는 별도 테스트로 단정한다. T3 에서 위치 그림 캡션이 바뀌어 `alignment.html` 과 `candidate_queries_v1.json`(`alignment_report.bytes`) 두 해시만 다시 기록했다. 나머지 14개는 `13f0bb6` 그대로다.

**보류.** B(국소 offset 을 계산에 반영), A2(대안 offset 의 전극 Dice 재점수), 전역 offset 수동 지정.

**실자료로 확인할 것.** (1) 상수 4 개가 실제 ROI 에 적정한가(지지 부족·모호가 너무 많거나 적지 않은가). (2) 전극 PNG 가 있는 층 수와 `excluded_layers`. (3) `alignment.json` 의 `quadrant_offsets_um` 와 `roi_local_alignment.json` 의 국소 offset 이 같은 방향의 어긋남을 보이는가. (4) 배율 추정(`linear_fit`)이 ppm 단위로 의미 있는 값인가.
