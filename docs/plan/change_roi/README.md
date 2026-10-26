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
| 소스 `src/simcompare`, 문서 | T10 commit(이 문서가 들어간 commit). T9 값은 이 표의 이전 판(T9 commit)에 있다 | — |
| `bundles/simcompare_job.py` | T10 commit(R1·R3 포함). 묶인 소스 해시(`PAYLOAD_SHA256`) `c02e05c6835164ff26cfb6b6b8d8134baa62fea3663ae91132987978386afef1` | `35266eac7b30e3ce1322d88a4f2c3db82e8a2e5ac2e455453bf77d5f1d91e39b` |
| `bundles/etx/simcompare_job_1_import_hotspots.py` | T10 commit(같은 `PAYLOAD_SHA256`) | `7e2f7bca338ff50b71fe5a2fbce1b6ed6d607d33cebe8f4fb233e908af2604e5` |
| `bundles/etx/simcompare_job_2_validate_compare.py` | 〃 | `a5c1240a2ff8a9cb7a52ad8362aa88bb33f1b8d7d709e447a05262263f268d98` |
| `bundles/etx/simcompare_job_3_import_electrode_assets.py` | 〃 | `494bf6877339ccf74fef657eff4991186fff1d105e0e00f7fbe9fe98f7235744` |
| `bundles/etx/simcompare_job_4_candidates.py` | 〃 (`candidates-change`) | `ed0e08a53d255382fe51078f65e2732ae382c828c23a58195ac136e91d483515` |
| `bundles/etx/simcompare_job_5_confirm_pairs.py` | 〃 | `fa3d75aaf56888924426dcf728b17413ef5b9450533b02647ff3d84640eb07fb` |
| `bundles/etx/simcompare_job_6_compare_windows.py` | 〃 | `1c28d2eee6169fc88926cf3fb2197bd30ba064a307d2a9b760c68c53ba5a61a0` |
| `bundles/windows_cp312_win_amd64.zip` | **이전 revision**(`d774920`, 2026-10-15). **이번 기능을 포함하지 않는다** | `103d4cb95b3f99308682b2de61c3b21201a07570d82c43a449f59445f1678750` |
| `bundles/updates/simcompare-0.1.0-py3-none-any.whl` | **이전 revision**(`edc7d56`, 2026-10-21). **이번 기능을 포함하지 않는다** | `062deb111d61c1b141d50d31277d31162cb32469552990cbf9518067bd5c85a7` |

Windows zip·wheel은 T9에서 다시 만들지 않았다. Windows에서 `candidates-change`를 쓰려면 별도 Task로 다시 묶어야 한다. 실행기 해시는 T9 commit의 값이며 소스를 고치면 `scripts/build_etx_stage_runners.py --overwrite`와 `scripts/build_single_file.py`로 다시 만들고 이 표를 갱신한다.

## DL2 인터페이스 요구 (기록용)

- 질의: `review_roi_v1`의 manual 행(`reference_export/`, `target_export/review_rois.json`). 검색 대상: `*_index_export/review_rois.json`. 매칭 쌍은 `candidate_queries_v1.json`의 `match_files`가 알려 준다.
- 출력: 현 `NCC_SCHEMA`(`psdl2_baseline_ncc_v1`, `mode=all_ready`, `inputs.query_export_sha256`/`index_export_sha256`, `results[].query.roi_id`/`center_xy_um`, `primary[].rank`/`center_xy_um`/`frame_id`/`product_id`).
- 향후 요청(아직 DL2에 없음): (1) `expected_center_xy_um` 힌트(정렬 offset으로 계산한 기대 중심 근처만 검색), (2) CNT 층별 채널(층 합집합 대신), (3) `--window-um`을 `candidates-change --window-um`과 일치시키는 옵션.
- `docs/handoff/README.md`는 **고치지 않았다**. DL2 pin(`f41d42709f4f17e0e7891b1c50d0547616b8f791`)은 그대로다.

### T10: DL2 요청 R1~R5 처리 상태 (DL2 `PLAN_v3_db_search.md` §5)

| 요청 | 상태 | 내용 |
|---|---|---|
| R1 `mask_derivation` | **완료(T10)** | `candidates-change`가 질의 export 2개·index export 2개 폴더마다 `mask_derivation.json`을 쓴다(`export-template-rois`와 같은 writer·규칙, 필수 인수 `--mask-derivation-rule between_stack_order_v1`). 경로는 `candidate_queries_v1.json`의 `mask_derivation_files`. DL2 `parse_mask_derivation` 규칙은 테스트 안에 다시 구현해 검사했다(DL2 코드를 import하지 않음) |
| R2 변경점을 `review_roi_v1` manual 행으로 | 충족(T6) | DL2 원문 그대로 변경점이 `review_roi_v1` 행인 `reference_export/`·`target_export/`로, 검색 대상은 `*_index_export/review_rois.json`으로 나간다 |
| R3 `psdl2_search_batch_v1` 읽기 | **완료(T10)** | `confirm-pairs --match`가 배치 봉투를 읽는다(질의 해시 `rankings[i].query_export_sha256`, `index_sources[].export_sha256`·`product_ids`, `search_scope`). NCC 경로는 바이트·동작 그대로 |
| R3e 출력 | **확인** | `roi_correspondence_v1`은 쌍 단위(a=reference, b=target, producer `manual`)이고 `compare-windows`가 그대로 읽는다. 사이드카에 `match_schema`, `rank`, `search_scope`, index source 해시를 남긴다 |
| R4 | 보류 | 이번 Run에서 하지 않는다 |
| R5 DL2 pin | 대기 | DL2 병합 뒤 T11에서 `docs/handoff/README.md`의 pin을 갱신한다. T10은 고치지 않았다 |

T10b 수정(T10 리뷰 FAIL blk:1): DL2 문서의 DB 는 제품별 template export 이므로 `index_sources[].export_sha256`이 `*_index_export` 해시와 같을 수 없다. 후보 제품 검증은 `product_ids` 기준이고 해시 대조는 등급(`candidates_index_export`/`product_ids_only`/`unverifiable_single_export`)으로만 남는다. 일치 source 가 없다는 이유로 종료 2 를 내지 않는다. 한 봉투에 두 방향 질의가 들어 있어도 같은 파일을 두 `--match`에 줄 수 있다. `index_sources == "single_export"`일 때는 봉투에 export 해시가 없어 대조하지 못한다(`inputs.index_sha256`은 index 체크포인트 파일의 해시다). DL2가 이 경우에도 export 해시를 싣도록 바뀌면 `confirm_pairs._index_grade`에서 대조를 켠다.

## compare ↔ DL2 최종 연결 체크 목록

DL2를 실제로 연결할 때 하나씩 확인한다. 1~10은 계획 초안(PLAN_v1_draft §7), 11~14는 이 Run에서 더했다.

1. `match_inputs`/`match_files`가 가리키는 `review_rois.json` 네 개가 실제로 있다.
2. 질의 export의 ready 행은 valid 위치, `source_kind=manual`, 비어 있지 않은 `role`과 `actual_layer`를 가진다.
3. index coverage가 complete다(`index_coverage_incomplete` 경고 없음).
4. 정렬 상태가 세 곳에서 같다: `candidate_queries_v1.json`의 `alignment`, `alignment.json`, `direct/direct_pairs_sidecar.json`의 `alignment`.
5. target→reference 방향 질의 중심 = reference→target 방향 중심 + offset(±pitch/2 이내).
6. `baseline_ncc`를 두 방향 모두 돌렸고 `inputs.*_export_sha256`이 현재 export 파일과 같다.
7. `confirm-pairs --list`가 `candidate_id` 목록을 찍고, `decisions.csv`의 id가 그 목록에 있다.
8. `compare-windows`가 확정 쌍 파일로 끝까지 돈다(종료 코드 0 또는 3과 그 사유 확인).
9. 실행기 로그에 `payload_sha256=`, numpy·Pillow 버전, `<명령> exit N`이 있고 N이 0 또는 3이다. Python 3.9 실행기 결과가 현재 인터프리터와 같다(`alignment.html` 제외, 아래 한계).
10. DL2 pin과 불변 bundle: DL2 pin이 `docs/handoff/README.md`와 같고 Windows zip·wheel이 바뀌지 않았다.
11. (X3) 질의 export 행의 `roi_id`와 `direct/roi_correspondence.json`의 `a.roi_id`/`b.roi_id`가 같은 행(중심·endpoints 일치)을 가리킨다.
12. 정렬 부호식 `p_target = p_reference + offset`이 `alignment.json`, 직접 쌍 b 중심(= a 중심 + 원점 차이 + offset), 질의 중심에서 같은 방향이다.
13. 직접 경로로 `compare-windows`를 실행한다: `--correspondence data/changes/direct/roi_correspondence.json`. blocked 쌍이 있으면 `blocked_pairs_present`로 막힌다(사유는 사이드카).
14. 한쪽 조합 쌍(endpoints 없음)이 `compare-windows`에서 group 합집합 Δ로 계산되는지, 층 변경 쌍이 `one_sided_layer_absent_*`로 막히는지 확인한다.

## 검증한 것

- 전체 흐름 게이트 `tests/test_etx_chain.py`: 합성 두 모델(알려진 offset, 심은 변경)을 ETX 폴더 구조로 만들고 재생성한 실행기 1·2·3·4·6회차를 BASE_DIR 지정·상대 경로·새 `--out` 규칙으로 차례로 실행한다. 5회차는 합성 NCC JSON으로 `confirm-pairs --list`까지. 현재 인터프리터와 `SIMCOMPARE_ETX_PY`(Python 3.9) 모두에서 돈다. `SIMCOMPARE_REQUIRE_ETX_PY=1`이면 ETX 인터프리터가 없을 때 실패한다.
- 잘못된 DL1 root(T10에서 바뀜): 없거나 폴더가 아닌 `--cnt-from-dl1-root`는 쓰기 전에 종료 코드 2(`cnt_dl1_root_missing`)이고 `--out`을 만들지 않으며 ETX 실행기가 멈춘다(뒤 작업은 시작하지 않는다). 로그의 `오류:` 줄과 인수·절대 경로 줄로 원인이 보인다. root는 있는데 모델 폴더·`cno/`가 없으면 종전처럼 정렬 실패(종료 코드 3, `cnt_missing_*`)다.
- DL2 연결(T10, `tests/test_dl2_link.py`): `mask_derivation.json` 4개가 DL2 `parse_mask_derivation` 규칙(테스트에 다시 구현)을 통과하고 같은 입력이 같은 바이트를 만든다. 합성 `psdl2_search_batch_v1`(`index_sources`가 배열인 형태와 `single_export`, `product_ids: null` 포함)로 `confirm-pairs --list`·accept·`compare-windows`·해시 불일치(종료 2, 아무것도 쓰지 않음)·사용 불가 후보 사유를 확인했다.
- `--align-search-um` 안내 통일(T9 리뷰 권고): 최소값 = pixel pitch 2배 이상(코드 검사), 권장 = 예상 레이아웃 이동과 반복 패턴 주기를 모두 덮는 값(예시 30 µm). 실행기 머리 주석·`--help`·`docs/usage_reference.md`가 같은 뜻이다.
- C6 필터(T9 리뷰 권고): 한쪽 조합의 반대 dataset 필터는 group 튜플 `(source_id, process_step_id, exposure_id, unit_id)` 단위로만 정한다. `(S1,P1),(S2,P2)`에 hint `(S1,P2)`이면 필터를 비우고 `filter_unresolved_<a|b>`다(`tests/test_direct_pairs.py`).
- 층 변경 CLI 전체 흐름(`tests/test_direct_pairs.py`): 두 모델의 stack 순서가 달라 분석 층이 L2→L1로 바뀐 사례에서 직접 쌍 둘이 `one_sided_layer_absent_*`로 blocked다.

## 미검증 항목

- 실데이터 규모의 시간·메모리(`candidates-change`는 합성 소형 자료만 돌렸다).
- 예시 탐색 반경 30 µm(0.1 µm pitch에서 300 px)의 탐색 비용·메모리는 측정하지 않았다.
- 실제 DL2 코드로 `mask_derivation.json`·배치 봉투를 읽어 본 적이 없다(DL2 코드를 import하지 않는 규칙이라 DL2 문서·`data.py`의 규칙을 테스트에 다시 구현해 검사했다). 실제 `psdl2.search --all-ready` 출력으로 한 번 확인해야 한다.
- `index_sources == "single_export"` 봉투에서는 index export 해시를 대조하지 못한다(위 T10 절).
- ETX Linux 서버에서의 실제 실행(Python 3.9 인터프리터는 macOS의 3.9 가상환경으로 대신 확인했다).
- 한 데이터셋에 source group이 여럿인 경우(변경 지도 합집합은 모든 group을 모으고 `compare-windows`는 GroupKey 하나만 잡는다).
- 반대 dataset의 group 필터가 여럿인 경우의 CLI 전체 흐름(필터 선택 규칙은 함수 단위 테스트만 있다).
- `alignment.html`의 PNG 압축 바이트는 zlib 구현에 따라 환경마다 달라 sha256이 다를 수 있다. 다른 출력 파일은 3.9와 3.11에서 바이트가 같다(테스트).
- 중간에 하드 실패하면 `alignment.html`은 없다.
- Windows zip·wheel은 이번 기능을 포함하지 않는다.
