# numeric_v2 / real_hotspot_v1 상세 레퍼런스

짧은 실행 순서는 [실행 가이드](usage.md)(ETX·Windows)에 있다. 이 문서는 모든 명령의 옵션, 오류 코드, 출력 스키마, 내부 동작을 모아 둔 상세 설명이다. 아래 "Windows 폐쇄망 설치", "ETX 실행 순서" 절은 같은 내용을 usage.md가 요약한 것이다.

## Windows 폐쇄망 설치 — CMD만 사용

사용자가 폐쇄망에서 성공을 확인한 방식은 **명령 프롬프트(CMD) + 설치된 Python + 로컬 wheel**이다. 온라인 설치와 PowerShell 설치 스크립트는 제거했다. 가상환경 활성화, 관리자 실행, 실행 정책 변경이 필요하지 않다.

1. Git에 포함된 [`bundles/windows_cp312_win_amd64.zip`](../bundles/windows_cp312_win_amd64.zip)을 폐쇄망으로 옮긴다.
2. 파일 탐색기 주소창에 `%USERPROFILE%`을 입력하고 `simcompare-offline` 폴더를 만든다. ZIP을 풀어 아래 구조로 준비한다. 기존 가상환경을 삭제하거나 덮어쓰지 말고 새 설치는 새 폴더를 사용한다.

```text
%USERPROFILE%\simcompare-offline\windows_cp312_win_amd64\
    install_offline.cmd
    verify_windows_bundle.py
    requirements-windows.txt
    manifest.json
    wheelhouse\
    fixtures\
```

3. 시작 메뉴에서 **명령 프롬프트**를 열고 아래 명령을 한 줄씩 실행한다. 실제 해제 경로가 다르면 첫 줄을 바꾼다. PC에는 **CPython 3.12 x64와 Python Launcher (`py.exe`)**가 설치되어 있어야 하며 Python 설치 파일은 ZIP에 포함되지 않는다.

```bat
cd /d "%USERPROFILE%\simcompare-offline\windows_cp312_win_amd64"
py -3.12 -m venv .venv-cmd
.venv-cmd\Scripts\python.exe verify_windows_bundle.py .
.venv-cmd\Scripts\python.exe -m pip --disable-pip-version-check install --no-index --only-binary=:all: --require-hashes --find-links=wheelhouse -r requirements-windows.txt
.venv-cmd\Scripts\python.exe -m pip check
.venv-cmd\Scripts\python.exe -m simcompare --help
```

**중간에 오류가 나면 다음 명령을 실행하지 않는다.** `wheel integrity OK`, `No broken requirements found`, simcompare 도움말이 표시되면 설치·CLI 확인 완료다. 이 과정만으로 실제 데이터나 수치 검증이 완료된 것은 아니다. 위 수동 절차는 사용자 실제 성공 확인이 있다.

같은 절차를 묶은 `install_offline.cmd`도 동봉했다. 같은 CMD 폴더에서 `install_offline.cmd`로 실행한다. 기존 `.venv-cmd`가 있으면 삭제하지 않고 Python 버전을 확인해 사용하며, 어느 단계든 실패하면 중단한다. 이 자동화 파일 자체의 Windows 실행은 아직 별도 확인이 필요하다. 이미 수동 설치에 성공했다면 재설치 없이 다음 단계로 간다.

## 실제 데이터 첫 실행

이후 명령도 위 압축 해제 폴더에서 실행한다. 경로는 실제 데이터 위치로 바꾸고, import 출력은 아직 없는 새 폴더를 지정한다. 원본은 수정하지 않는다.

```bat
.venv-cmd\Scripts\python.exe -m simcompare import-hotspots --root "D:\data\input\hotspot" --out "D:\data\imported\reference"
```

저장소의 [`configs/real.example.yaml`](../configs/real.example.yaml)을 `D:\data\real.yaml`로 복사한다. reference/target/map의 data_dir를 실제 변환 폴더로 설정하고, 확인된 layer·조건·pixel/line 분류표만 입력한다. 원본 YAML의 상대 경로는 복사한 설정 파일 기준이므로 실제 경로로 수정해야 한다.

```bat
.venv-cmd\Scripts\python.exe -m simcompare validate-data --config "D:\data\real.yaml"
.venv-cmd\Scripts\python.exe -m simcompare compare --config "D:\data\real.yaml"
.venv-cmd\Scripts\python.exe -m simcompare electrode-map --config "D:\data\real.yaml"
```

## 합성 예제 실행 (선택)

```bat
.venv-cmd\Scripts\python.exe -m simcompare demo-data --out demo_data
.venv-cmd\Scripts\python.exe -m simcompare validate-data --config demo_data/config.yaml
.venv-cmd\Scripts\python.exe -m simcompare compare --config demo_data/config.yaml
.venv-cmd\Scripts\python.exe -m simcompare electrode-map --config demo_data/config.yaml
```

`demo-data`는 numeric_v2 합성 데이터만 만든다. 실제 데이터 검증을 대신하지 않는다.

## 대응 위치와 국부 ΔCA (compare-windows)

비슷한 두 제품에서 설계자가 짝지은 위치의 **같은 물리 창** 안 CA를 비교한다([PLAN_v3](plan/PLAN_v3_local_delta_ca.md)). 창은 유닛 크기가 아니라 ROI 중심 주변 정사각형의 한 변(µm)이며 기본값이 없다. 대응 위치는 `roi_correspondence_v1` JSON으로 준다([데이터 계약](data_contract.md)). reference가 a, target이 b이고 방향은 b − a다. 유닛 크기가 달라도 같은 물리 창이므로 절대 비교가 된다.

```bat
.venv-cmd\Scripts\python.exe -m simcompare.correspondence validate --correspondence "D:\data\pairs.json"
.venv-cmd\Scripts\python.exe -m simcompare compare-windows --config "D:\data\real.yaml" --correspondence "D:\data\pairs.json" --window-sizes 10 20 30 --sort-diameter-um 5 --out "D:\data\local"
```

출력:

| 파일 | 내용 |
|---|---|
| `local_delta.json`, `local_delta_rows.csv` | 쌍 × 창 크기 × 32 지름 × {window, attributable_core} × {union, lateral_only, vertical_only, simultaneous}의 a / b / Δ, 창 면적 비율, 상태·사유. endpoints가 있으면 named_pair(그 case 자체의 창 안 CA) 추가 |
| `window_sensitivity.json/csv` | 창 크기별 Δ_union, 부호 일치, 크기 쌍 간 \|Δ\| 순위 상관, core 적용 가능 지름. 추천값은 없고 사용자가 G4를 정한다 |
| `local_delta.html` | 쌍마다 a/b layout crop과 hotspot crop(조각 색), a/b/Δ 표. 정렬 지름의 \|Δ_union\| 내림차순. PNG 내장이라 `file://`로 열린다 |

소요 시간은 개발기 합성 기준으로 2000×2000 frame에서 쌍·창당 약 0.13 s이고, 그 앞의 group 계산(`compare`와 같은 비용)이 case 수에 비례해 더 크다. `--compact-json`을 주면 JSON에서 행별 세부 지표를 빼고 CSV에만 남긴다(쌍 하나·창 하나가 약 0.4 MB이므로 수십 쌍 × 여러 창일 때 쓴다). 규칙: attributable core는 한 변 (창 − 지름)의 안쪽 정사각형이며 창 ≤ 지름이면 not_applicable이다. 창이 frame 밖으로 나가면 `window_clipped_<side>`, valid 구멍을 포함하면 `valid_incomplete_<side>` 사유로 해당 Δ는 null이고 실행은 exit 3이다. 원천 누락·미매핑은 창 측정도 partial로 만든다. **실자료에서는** `import-hotspots --expected-cases`로 case 목록을 확인하고 config에 `condition_assignment`와 pair 표를 넣어야 group이 available이 되며, 그 전에는 모든 창이 partial이고 Δ는 null이다. 대응 파일의 `repeats_per_frame`은 측정 hotspot frame(2×2 unit 원본 영상에서 사방 500 px를 잘라낸 중앙 영역) 안의 반복 수이며 per-unit 수를 넣지 않는다. 대응 파일의 endpoints 순서는 export-review-rois가 case와 같은 순서로 비교하므로 case의 순서대로 적는다(창 측정 자체는 순서를 보지 않는다). `--jitter-um j`를 주면 중심을 ±j 이동한 네 창의 Δ_union 범위와 부호 일치를 기록한다. DL2 검색에서 온 쌍의 점수는 출처 표시일 뿐 수치에 들어가지 않는다.

**실자료 규모**: 결과 숫자와 파일은 이전과 같다(frozen 7bcb07a와 바이트 비교). 바뀐 것은 메모리·시간이다. (1) 한 명령 안에서만 사는 마스크 풀기 캐시(최대 32개: 양쪽 × 4 지표 × 4개, 오래된 것부터 버림)로 같은 그룹 마스크를 행마다 24번 풀지 않는다. 캐시는 원본 `PackedMask`를 붙들고 있어 다른 객체와 키가 겹치지 않는다. (2) 이름 붙은 case(named pair)는 (제품, design, frame, layer, 전극 쌍)으로 한 번 색인하고 pair 하나 동안은 그 case의 지름별 레코드를 한 번만 읽는다(창 크기마다 다시 읽지 않는다). (3) 전극 layout 마스크는 모든 case를 보관하지 않는다: 시작할 때 manifest를 검증하고 layout을 case 순서대로 한 번 읽어 인증만 하며(첫 읽기 실패는 이후 case의 layout을 막고 쪽 전체 사유가 되는 것까지 이전과 같다), 쓸 때 다시 읽어 쪽마다 최대 8 case만 둔다(읽기 실패는 `thumbnail`/`layout` 메모에 같은 사유 코드로 남는다). 인증과 달리 **인증 뒤에 파일이 바뀌면** 그 case만 layout 없음으로 처리한다(이전에는 시작할 때 읽은 사본을 계속 썼다).

**admission**: 대응 파일을 읽고 쌍을 묶은 **뒤**, 계산·디코딩·캐시 할당 **전에** 한 번 비교한다(`oversized_real_operation: compare-windows: estimated X bytes exceed limit Y (R ready and B blocked pairs x S window sizes x 32 diameters); fewer pairs or window sizes (split the correspondence file), or raise max_operation_bytes ...`). 추정은 단계별 최대값이다(합이 아님): ① 두 모델 `compute_areas`(또는 아래 artefact 읽기) ② 행 단계 = 보관 결과 + 쌍 + `ready × 창 크기 수 × 32 × 32 KiB`(행) + 마스크 풀기 캐시(최대 32 × 큰 쪽 frame cells) + named 레코드(2 × 32 × cells) + 창 하나의 측정 임시 ③ 보고서 단계 = 보관 결과 + 행 + 쪽마다 layout 8 case × 2 마스크 + 고정된 개요 마스크 + 디코드 임시 + 쪽 문자열(`ready × 창 크기 수 × 256 KiB`). 두 모델의 frame이 다르면 큰 쪽 기준이다. `--compact-json`은 메모리 대책이 아니다(행은 계산 뒤에야 줄어든다). 라이브러리 호출 `compute_local_delta(...)`와 HTML 작성기도 같은 함수로 자기 단계를 다시 확인한다.

**compare 마스크 artefact**: 실자료 `compare`(2회차)는 `comparison.json`/`comparison.csv` 옆에 `comparison_masks.zip`도 공개한다(그룹마다 union·failure piece 3개의 비트 패킹 마스크와 그 지표, 입력 해시·엔진 태그; 1262² 한 layer 약 51 MB, 케이스 수와 무관). `compare-windows`는 `output_dir`의 이 파일이 두 모델의 `input_hash`·frame·마스크 sha256과 모두 맞을 때만 읽고 `compute_areas` 두 번을 건너뛴다(같은 숫자·같은 파일; 테스트가 재계산과 바이트로 비교한다). 없거나 오래됐거나 깨졌거나 공개 중간 상태(`.comparison.publishing`/`.comparison.committed`가 보임)면 조용히 재계산한다. 이 artefact가 없는 기존 2회차 출력도 그대로 유효하다. 크기가 `max_operation_bytes`를 넘으면 읽지 않는다. admission은 파일 크기만으로 먼저 하고(재사용 추정에 이미 패킹 마스크 비용이 들어 있다) 파일은 그 뒤에 읽는다. 알아 둘 점: artefact가 있으면 admission은 재사용 추정으로 먼저 허가하므로, 그 artefact를 쓸 수 없어 재계산 추정을 다시 허가받을 때 재사용 추정이 재계산 추정보다 크면(frame이 약 22,000 cell 이하인 아주 작은 자료에서만 가능하고 ETX 규모에서는 일어나지 않는다) 예전에는 돌던 실행이 거부될 수 있다. 읽어 보니 쓸 수 없으면 재계산 추정을 다시 허가받은 뒤 `compute_areas`를 한다. `compare --out <다른 폴더>`로 돌렸다면 6회차 config의 `output_dir`과 달라 재사용되지 않는다.

**출력 쓰기와 공개**: 파일 내용(바이트)은 이전과 같고(frozen 7bcb07a와 비교, `--compact-json` 포함), 바뀐 것은 쓰는 방식과 공개 방식이다.

- *스트리밍.* `local_delta.json`·`window_sensitivity.json`은 행을 하나씩 변환해 64 KiB 조각으로 쓰고(결과 전체의 변환 사본이나 하나의 큰 문자열이 없다), `local_delta_rows.csv`는 행을 만드는 대로, `local_delta.html`은 쪽의 행을 만드는 대로 쓴다. 각 파일은 같은 폴더의 임시 파일에 쓰고 끝나야 제 이름이 된다. 개발기 N=300·1262²·창 3개·jitter 기준 최대 RSS는 P=50에서 1.2~1.3 GB → 0.75 GB(재계산)·0.68 GB(artefact), P=200에서 3.0~3.1 GB → 0.93 GB·0.85 GB였다(`plan/scale_stage46/T4_report.md`).
- *생성 파일 목록(allowlist).* 한 명령이 만드는 파일은 정확히 `local_delta.json`, `local_delta_rows.csv`, `window_sensitivity.json`, `window_sensitivity_rows.csv`, `window_sensitivity_rank_agreement.csv`, `local_delta.html`, `local_delta_thumbs.json`, `local_delta_thumb_<12자리 hex>.png`, `local_delta_overview_<a|b>_<12자리 hex>.png`이다(이 이름들을 **한 세대**라고 부른다). 출력 폴더의 다른 파일(`execution.json`/`error.json`은 CLI가 따로 쓴다, `comparison*`, 사용자 파일, 이름이 비슷한 `local_delta_notes.csv` 등)은 읽지도 옮기지도 지우지도 않는다. 이전에는 `local_delta*`·`window_sensitivity*`의 `.json/.csv/.html`을 모두 지웠지만 이제는 위 이름만 세대에 속하고, 이전 실행의 썸네일 PNG도 세대와 함께 교체된다(새 세대에 없는 이름은 공개 때 사라진다).
- *전부 아니면 전무.* 새 세대는 먼저 출력 폴더 안의 임시 폴더 `.local_delta.staging.<pid>.<난수>/`에 전부 쓰고(JSON·CSV·HTML·PNG·`local_delta_thumbs.json`), 모두 성공한 뒤에만 공개한다. 공개 순서: ① 임시 폴더 파일을 fsync하고 이전 세대 이름을 센다 ② 기록 `.local_delta.publishing`(`old`/`new` 이름 목록)을 임시 이름에 쓴 뒤 이름 바꾸기로 남긴다 ③ 이전 세대 파일을 `.local_delta.prev/`에 보존(하드링크, 실패하면 복사) ④ 새 파일을 `os.replace`로 제 이름에 놓고 새 세대에 없는 이전 파일을 지운다 ⑤ **커밋 표시 `.local_delta.committed`를 원자적 이름 바꾸기로 만든다** ⑥ `.prev/` → 기록 → 표시 순으로 지운다(`compare`의 공개 트랜잭션과 같은 프로토콜이며 파일 이름이 고정 둘이 아니라 목록이다).
- *코드가 보장하는 것.* 커밋 표시 **전**의 오류·`OSError`(ENOSPC 포함)·Ctrl-C·계산 오류·HTML 쪽 admission 거부는 이전 세대 전체를 그대로 두고(공개를 시작하기 전이면 건드리지도 않고, 시작한 뒤면 되돌린다) 임시 폴더를 지운다. 커밋 표시 **후**의 오류·Ctrl-C는 절대 되돌리지 않고 새 세대가 확정이며 남은 정리는 다음 실행이 마친다. 프로세스가 강제 종료되면 다음 `compare-windows` 시작 때 표시가 있으면 새 세대로 확정하고 없으면 기록을 보고 이전 세대로 되돌린다(임시 폴더는 지운다; 살아 있는 다른 실행의 임시 폴더는 지우지 않는다). 복구는 읽기에 성공한 기록·표시만 따른다: 읽을 수 없거나(권한·I/O) 내용이 깨졌거나 생성 파일 목록 밖의 이름이 적혀 있으면 `local_delta_recovery_unreadable` 오류로 끝나며 기록·`.prev/`·출력 파일을 지우거나 바꾸지 않는다(원인을 고쳐 다시 실행하면 이어서 복구하고, 기록이 깨졌다면 `.local_delta.prev/`의 파일을 직접 제 이름으로 되돌리고 `.local_delta.*` 파일을 지운다). 표시나 기록을 지우지 못하는 상태(권한 등)면 새로 쓰지 않고 오류로 끝난다. 따라서 복구가 끝난 뒤 폴더의 생성 파일은 항상 이전 세대 전체 또는 새 세대 전체다.
- *코드가 보장하지 않는 것.* 강제 종료 직후부터 다음 실행이 복구하기 전까지는 두 세대가 섞여 보일 수 있다(`.local_delta.publishing`/`.local_delta.committed`가 보이면 복구 전이다). 같은 출력 폴더에서 `compare-windows`를 동시에 두 개 돌리는 것은 지원하지 않는다. 공개 중에는 새 세대와 이전 세대(백업)가 디스크에 함께 있어야 하고, 공개가 끝나면 이전 세대는 사라진다. fsync 이후의 저장장치 거짓말(쓰기 캐시)은 막지 못한다.
- *CLI 종료 코드와 `error.json`.* `compare-windows`가 공개 트랜잭션을 연 **뒤**(대응 파일·쌍 묶기·admission을 통과한 뒤) 오류로 끝나면 CLI는 `error.json`/`execution.json`만 쓰고 관리 리포트(`local_delta*`, `window_sensitivity*`)는 지우지 않는다 — 이전 세대가 마지막으로 성공한 실행의 결과로 남는다(`error.json`은 다음 성공 때 지워진다). 그 **전**의 입력·설정·admission 오류(창 크기, 대응 파일, 크기·admission 오류, 설정 오류)도 마찬가지로 `error.json`/`execution.json`만 쓰고 `local_delta*`·`window_sensitivity*`는 지우지 않는다. 즉 `compare-windows`는 어떤 오류에서도 이전 세대를 그대로 둔다. 공개 제어 파일(`.local_delta.publishing|committed`)이 남아 있으면 관리 파일을 지우지도 않으므로 강제 종료 뒤 `local_delta_recovery_unreadable`로 막히는 일이 없다.

같은 실행의 `compare` 결과에는 `fraction_of_frame`(면적 / hotspot frame 면적, 단위 없음. hotspot frame = 2×2 unit 원본 영상에서 사방 500 px를 잘라낸 중앙 영역)이 group별·delta별로 추가된다(`comparison_fractions.csv`, HTML의 "frame 면적 비율"). G2의 한 unit 환산 없이 유닛 크기가 다른 제품을 비교하는 제품 수준 지표다.

권장 운용값(코드 기본값이 아니며 실행 때 명시한다): 정렬·썸네일 지름은 전극 수 지도의 대표 지름과 같은 **5 µm**, 창 후보는 **10 / 20 / 30 µm**(밀도가 큰 1~10 µm 이물에서 창 반폭 > d/2가 되는 크기), jitter는 **0.5 µm**(5 px, 라벨 찍기 오차 수준), DL2 위치 hit 허용오차는 **선택한 창의 반폭**. 민감도 표와 설계자 검토 결과에 따라 바꾼다.

대응 파일 변환(ROI 왕복, DL 라벨, DL2 결과 받기, 후보 커버리지):

```bat
.venv-cmd\Scripts\python.exe -m simcompare.correspondence to-review-rois --config "D:\data\real.yaml" --correspondence "D:\data\pairs.json" --side reference --out "D:\data\real_a.yaml"
.venv-cmd\Scripts\python.exe -m simcompare export-review-rois --config "D:\data\real_a.yaml" --out "D:\data\rois_a"
.venv-cmd\Scripts\python.exe -m simcompare.correspondence to-review-rois --config "D:\data\real.yaml" --correspondence "D:\data\pairs.json" --side target --out "D:\data\real_b.yaml"
.venv-cmd\Scripts\python.exe -m simcompare export-review-rois --config "D:\data\real_b.yaml" --out "D:\data\rois_b"
.venv-cmd\Scripts\python.exe -m simcompare.correspondence attach-roi-ids --correspondence "D:\data\pairs.json" --export-a "D:\data\rois_a\review_rois.json" --export-b "D:\data\rois_b\review_rois.json" --out "D:\data\pairs_ids.json"
.venv-cmd\Scripts\python.exe -m simcompare.correspondence to-dl2-relations --correspondence "D:\data\pairs_ids.json" --out "D:\data\dl2_relations.json"
.venv-cmd\Scripts\python.exe -m simcompare.correspondence to-dl1-relations --correspondence "D:\data\pairs.json" --out "D:\data\dl1_relations.csv"
.venv-cmd\Scripts\python.exe -m simcompare.correspondence from-dl2-search --search "D:\data\dl2_search.json" --index "D:\data\dl2_index.json" --query-export "D:\data\rois_a\review_rois.json" --index-export "D:\data\rois_b\review_rois.json" --out "D:\data\pairs_dl2.json"
.venv-cmd\Scripts\python.exe scripts\check_candidate_coverage.py --config "D:\data\real.yaml" --correspondence "D:\data\pairs.json" --min-counts 2 3 4 --out "D:\data\coverage"
```

endpoints가 없는 쌍의 manual 요청은 export에서 `case_context_required`로 blocked가 된다. `to-dl2-relations`는 양쪽 roi_id가 있는 ready 쌍만 `similar`로 쓴다. `check_candidate_coverage.py`는 라벨 위치가 전극 수 지도 영역 안에 드는 비율을 threshold별로 보고하는 읽기 전용 점검이다.

### 모델 쌍 창 비교 인수 (`--models` / `--model` / `--case-window-um` / `--case-diameter-um`)

다제품 흐름(6m회차)에서 `compare-windows`에 더하는 선택 인수다. 모두 생략하면 위 동작·출력이 그대로다. 흐름은 `docs/usage.md`의 "모델이 여럿일 때".

| 인수 | 값 | 필수 조건 | 설명 |
|---|---|---|---|
| `--models` | `models.yaml`(`compare_models_v1`, `--config`와 같은 폴더) | `--model`과 함께 | `models.yaml`의 모든 configured 모델을 로드한다 |
| `--model` | 모델 키(= product_id) | `--models`와 `--out`과 함께 | a = config target, b = 모델, Δ = 모델 − target. 마스크는 2회차 `comparison_masks.zip`을 재사용하지 않고 두 쪽을 다시 계산한다 |
| `--case-window-um` | µm, `--window-sizes` 안의 값 | `--model`, `--case-diameter-um`과 함께 | 이 창에서 모델 쌍의 관측 hotspot case 구성 차이를 `window_cases.json/csv`로 낸다 |
| `--case-diameter-um` | µm, 두 데이터셋에 있는 지름 | `--model`, `--case-window-um`과 함께 | `window_cases` 지름. 쌍×case 마다 마스크를 다시 읽는다 |

어기면 파일을 쓰지 않고 종료 2다(`model_arguments_paired`, `case_args_require_model`, `case_args_incomplete`, `case_window_not_in_sizes`, `case_diameter_missing`). 필드는 [데이터 계약](data_contract.md)의 `window_cases_v1`.

## 통합 HTML 보고서 (report)

이미 만든 출력 폴더를 읽어 **HTML 한 파일**(PNG는 base64, 그래프는 inline SVG, 외부 URL·`<script src>` 없음, `file://`로 열림)을 만든다. 수치는 다시 계산하지 않고 수율·밀도 단위 환산(보류)도 넣지 않는다. 계획: [report_html_plan_v1](plan/report_html_plan_v1.md).

```bat
.venv-cmd\Scripts\python.exe -m simcompare report --compare-dir "D:\data\numeric_outputs" --map-dir "D:\data\numeric_outputs" --windows-dir "D:\data\local" --rois-dir "D:\data\rois_a" --out "D:\data\report" --diameter-um 5 --rank-window-um 20 --top-n 10
```

| 인수 | 설명 |
|---|---|
| `--compare-dir` | 필수. `compare` 출력(`comparison.json`, `comparison_fractions/summary/failure_pieces.csv`) |
| `--map-dir`, `--windows-dir`, `--rois-dir` | 선택. 각각 `electrode-map`, `compare-windows`(crop·전체 frame PNG가 있는 출력), `export-review-rois` 출력. 주지 않으면 해당 섹션에 "입력 없음"이 표시되고 종료 코드에는 영향이 없다 |
| `--candidates-dir`, `--confirm-dir` | 선택. 각각 `candidates`, `confirm-pairs` 출력. 주면 A2 다음에 "ROI 두 축 보기" 섹션이 생긴다(아래). 둘 다 `--out`과 달라야 하고 `--confirm-dir`은 `--windows-dir`가 필요하다 |
| `--diameter-um` | 대표 지름(기본 5) |
| `--rank-window-um` | 악화/개선 순위 기준 창(기본 20). **임시 기준값**이며 제품마다 다를 수 있다. `local_delta.json`의 `window_sizes_um` 중 하나여야 한다 |
| `--top-n` | 악화/개선 각각 표시할 쌍 수(기본 10) |

출력: `report.html`, `report.json`(보고서에 쓴 값과 입력 파일 SHA256), `execution.json`.

- A: 표지, 핵심 지표 카드(쉬운 말 설명·출처, "지표 읽는 법"), 악화(Δ>0)·개선(Δ<0) 상위 쌍, 판정 조건. Δ = 0인 쌍은 개수만 센다. 색은 악화 빨강, 개선 파랑이고 그래프의 a는 회색, b는 보라다.
- B: frame 면적 비율, line/pixel/조각, 대응 위치 상세(제품별 전체 frame 위치도와 쌍별 locator·crop), 창 민감도, 취약 위치 지도, 검토 ROI, 상태·누락, 출처.
- 같은 제품·frame·layer에서 순위 기준 창이 겹치는 쌍은 겹침 면적(µm²)과 함께 표시한다. **겹친 쌍의 Δ를 단순 합산하지 마세요.** 값은 바꾸지 않는다.
- 종료 코드: 0 = 완료, 3 = 입력이 partial이거나 값 없는 쌍·blocked 쌍·crop 누락이 있음, 2 = 입력 오류(폴더 사이 제품 불일치, `is_synthetic` 혼합, 순위 창·지름이 없음(사용 가능한 값을 메시지로 알림), 예상 밖 `schema_version`, 필수 파일 없음, `--out`이 입력 폴더). 2이면 `report.html`을 남기지 않고 `error.json`/`execution.json`만 둔다.
- **ROI 두 축 보기**(`--candidates-dir` 또는 `--confirm-dir`를 줄 때만 생긴다. 주지 않으면 보고서는 이전과 바이트 단위로 같다): `--rank-window-um`·`--diameter-um`의 window union 면적에서 (1) 절대 CA 상위 N(`max(a, b)` 내림차순, 같으면 쌍 이름 순), (2) CA 변화 큰 곳 = 기존 악화(Δ>0)·개선(Δ<0) 상위 N을 따로 보여 주고, 두 목록에 모두 든 쌍은 "절대·변화 모두 큼", 절대 목록에만 있으면 "절대 CA 큼 (변화 작음)", 변화 목록에만 있으면 "변화 큼 (절대 CA 작음)"으로 표시한다. 등수만 쓰고 임계값은 없으며 값이 없는(partial/unavailable) 쌍은 순위에 넣지 않는다. CA 변화가 크다는 것만으로 그 위치의 형상이 바뀌었다고 말할 수 없으므로(이웃 영역 영향) "변경부"라고 부르지 않는다.
- `--confirm-dir`를 주면 쌍마다 방향, `mutual`/`one_way`, 출발 영역이 reference/target 쪽 취약 영역이었는지(`--candidates-dir`의 질의 `max_count`, 찾지 못하면 "알 수 없음"), `merged_from` 영역 수, 상호 병합 쌍 수, 그리고 마지막 열 "매칭 근거(표시용, 수치에 쓰지 않음)"에 NCC 점수·플래그를 보여 준다. 점수는 순위·색·판정에 쓰지 않는다.
- 깔때기 표 세 개: 후보 생성(regions → merged → after_max_candidates → exported_ready, index 전극 덮임), 사용자 확정(후보 → 결정 → blocked 사유 → 출력 쌍), compare-windows(ready / 값 있음 / 값 없음 / blocked). 사유 코드와 개수는 `funnel.json`, `confirm_funnel.json`, `local_delta.json` 그대로다. 확정되지 않은 후보는 쌍으로 나열하지 않고 개수로만 나온다. `--confirm-dir`이 없으면 그 사실을 섹션에 적는다.
- 추가 입력 오류(exit 2): `confirm_windows_correspondence_mismatch`(`roi_correspondence.json`의 sha256이 `local_delta.json`의 `provenance.correspondence_sha256`과 다름), `confirm_candidates_mismatch`(사이드카가 기록한 candidates sha256이 다름), `confirm_pair_set_mismatch`, `confirm_requires_windows_dir`, `candidates_funnel_mismatch`, 예상 밖 `schema_version`, 제품 불일치, 개수가 아닌 깔때기 값, `--out`이 입력 폴더. `report.json`의 `sections.A2b`는 화면에 보인 값을 그대로 담고 새 입력 파일 SHA256은 `inputs`에 들어간다.
- 데모처럼 frame이 작으면 창 20 µm가 clipping되므로 작은 창으로 `compare-windows`를 돌리고 같은 값을 `--rank-window-um`에 준다(`scripts/2_demo.sh` 참고).

## 전극 PNG → review_assets_v1 (import-electrode-assets)

실제 전극 PNG를 `export-review-rois`가 읽는 `review_assets_v1` manifest와 asset 파일로 바꾼다(새 스키마 아님). 출력 디렉터리는 새 경로여야 하고 입력은 수정하지 않는다.

```bat
.venv-cmd\Scripts\python.exe -m simcompare import-electrode-assets --source-kind dl1_png_folder --root input/dl1 --numeric-data-dir imported/reference --source-version LAYOUT_V1 --max-asset-memory-mb 4096 --out assets/reference
.venv-cmd\Scripts\python.exe -m simcompare import-electrode-assets --source-kind hotspot_case_electrode --root input/hotspot --numeric-data-dir imported/reference --condition-assignment assignment.json --cnt-from-dl1-root input/dl1 --source-version LAYOUT_V1 --max-asset-memory-mb 4096 --out assets/reference
```

- `--numeric-data-dir`(필수)는 case identity·endpoint·failure_type·frame·valid·input_hash의 유일한 출처다. numeric_v2(합성) 폴더이거나 real_hotspot_v1 폴더 + `--condition-assignment`다. 어댑터는 값을 만들지 않고 endpoint `(product_id, electrode_id, actual_layer)`를 이름이 정확히 같은 PNG에 묶는다. 대소문자만 다른 layer, PNG 없음, case 이름·endpoint 불일치는 그 case만 blocked(변환 기록의 `blocked_cases`, 종료 코드 3)이고 manifest에 넣지 않는다. 변환 가능한 case가 없으면 종료 코드 2다.
- `dl1_png_folder`: `<root>/<model>/<electrode>/<n>-<layer>.png`, `<root>/<model>/cno/<n>-<layer>.png`(`cno` 는 전극 폴더와 같은 위상; 예전 자료의 `<n>_<layer>.png` 도 받는다). 모델 폴더 이름 = product_id. 파일 번호는 출처 기록일 뿐이다. CNT asset은 그 모델 cno 마스크들의 합집합이며 디코딩 마스크 hash로 식별한다. `--stack-order`(dl1_png_stack_order_v1)를 주면 모든 이름이 순서 파일에 있어야 하고 대소문자 충돌은 거부한다. 순서는 이 파일에서만 오고 추정하지 않는다.
- `hotspot_case_electrode`: case 폴더(이름 = case_id)의 `1.png`는 endpoint 1, `2.png`는 endpoint 2다. 둘의 shape가 다르면 `electrode_shape_mismatch`다. 이 폴더에는 CNT가 없으므로 `--cnt-from-dl1-root`로 DL1 모델의 cno를 지정하지 않으면 case는 `cnt_not_provided`로 blocked다(0 배열로 채우지 않는다).
- 극성은 출처별 계약 표다: 두 종류 모두 검정 = 형상, `binary_zero_is_marked`, `polarity_basis: user_answer_2026_10_19`. 표에 없는 종류는 blocked `electrode_polarity_unspecified`(종료 코드 3). 회색·유색·투명·16비트·EXIF 회전·애니메이션·손상 PNG는 파일 이름과 함께 거부하며 threshold가 없다. asset은 mode L PNG {0,255}(0 = 형상)다.
- frame: PNG는 전체 frame(0.1 µm/pixel, 원점 (0,0))이고 dataset frame은 hotspot crop(원점 (50,50) µm, 축마다 1000 px 작음)이다. 어댑터는 dataset frame 원점·pitch와 `PNG shape = frame shape + 1000`을 검사하고(`layout_hotspot_frame_mismatch`, `electrode_frame_mismatch`) 중앙 500 px 테두리를 정확히 잘라낸 부분만 내보낸다. **알려진 한계**: 50 µm 테두리 문맥은 asset에 없어서 DL2의 near/wide 창이 그 바깥을 볼 수 없다.
- 같은 마스크는 한 번만 디코딩·기록한다. `--max-asset-memory-mb`(필수)는 보수적 추정치(측정 아님)와 비교하며 초과하면 아무것도 쓰지 않고 추정치와 함께 종료 코드 2다. 추정은 PNG 모드별(RGB/RGBA/팔레트/회색조) Pillow 디코드 버퍼·변환본·NumPy 임시·잘라낸 사본·인코딩 버퍼와, 가드 이전에 이미 올라온 숫자 데이터셋이 붙들고 있는 모든 배열(frame.valid와 관측된 diameter별 원본 hotspot mask; 같은 버퍼는 한 번만)을 더한 값이다(전체 import 경로 RSS 측정 테스트로 검증: 32 지름·2400² mask·3400² RGB에서 측정 최대치 ≤ 1.25 × 추정; 배열이 아닌 메타데이터는 계상하지 못한다). **이 가드는 데이터셋을 이미 올린 뒤에 실행되므로 한도를 넘는 모든 할당을 막지는 않는다.** 가드가 다루는 것은 이후 PNG 디코드·변환·쓰기 단계로, 추정이 한도를 넘으면 아무것도 쓰기 전에 종료 코드 2다.
- 출력: `review_assets.json`, `assets/*.png`, `electrode_asset_import_record.json`(source_kind, polarity_basis, 파일별 hash, adapter 버전, input hash, 개수, 경고). 같은 입력은 바이트까지 같다.
- **실자료 export**: `export-review-rois`는 mode=real에서 `map.input`에 `condition_assignment`, `asset_manifest`({path, sha256}), `electrode_import_record`({path, sha256}, 이 두 키만 허용)가 **모두** 있고 증거가 맞을 때만 real_hotspot_v1을 처리한다(`configured_dataset` → electrode-map 영역 → `review_roi_v1`; 영역은 `--min-count`/`map.min_count`가 있어야 생기고 hotspot 마스크 자체는 ROI가 되지 않는다). 두 파일은 `data_dir` 안에 있어야 한다(`import-electrode-assets` 출력을 data_dir로 복사). 수용 조건: 기록과 manifest가 `polarity_basis`를 가지며 계약 표의 출처 종류와 값이 같고, 모든 asset의 `value_semantics`가 `binary`/`binary_zero_is_marked`이며, manifest의 `source_manifest_hash`가 dataset input hash와 같고 기록의 `manifest_sha256`이 manifest 파일과 같다. 키가 하나라도 없거나 `polarity_basis`가 없으면 예전처럼 `unavailable`(`electrode_polarity_unknown`, `source_layout_assets_unconfirmed`, 종료 코드 3)이다. 출처 종류·`polarity_basis`가 표와 다르거나 지원하지 않는 `value_semantics`는 `blocked`(`electrode_polarity_unspecified`, 코드 3, ROI 없음), sha256·identity·기록 불일치는 이름 있는 오류(코드 2)다. 영역 coverage가 불완전하면(예: `import-hotspots --expected-cases` 없음) 그 ROI는 `map_coverage_incomplete`로 blocked다.

## ROI 선정

ROI 선정 방식은 셋이다. **변경점 ROI(`candidates-change`)와 ΔV 취약점 ROI(`candidates-dv`)가 현행이다.** 비가중 취약점 ROI(`candidates`, `max_count` 기준)는 참고용으로 코드와 문서를 남겨 둔다. ETX 4회차 실행기(`simcompare_job_4_candidates.py`)는 `candidates-change`를, 4b·4c 실행기는 `candidates-dv`·`vulnerability-report`를 돈다.

### 변경점 ROI (candidates-change) — 현행

**목적.** 두 제품(reference, target)의 CNT 층을 한 offset으로 정렬한 뒤, 정렬로 설명되지 않는 5 µm hotspot 변경 위치를 순위로 뽑는다. 결과는 DL2에 넣을 질의 ROI이고, DL2 없이 6회차로 가는 직접 대응 파일도 함께 나온다.

**흐름.**

```text
config(reference, target: real_hotspot_v1)      PNG root (<제품>/CNO/, Merged_Layer/<전극>/, Layer/; 예전 구조도 읽는다)
        |                                              |
        +--> hotspot 마스크 (5 µm) ---+                v
        |                             |       CNT 3층(pcnt/gcnt/ocnt)을 한 offset으로 동시 정렬
        |                             |                |  offset: p_target = p_reference + offset
        |                             v                v
        +------------------> 변경 지도 (짝 + 한쪽 조합, 층별 합집합 변화) 
                                      |
                                      v
                    창(--window-um) 후보 -> 층별 병합(--merge-overlap) -> 방향별 top-k
                                      |
        +-----------------------------+-----------------------------+
        v                                                           v
 DL2 경로: *_export, *_index_export (review_roi_v1)         직접 경로: direct/roi_correspondence.json
 (선택) DL2 -> 5회차 confirm-pairs                                   |
        |                                                           |
        +-------------------> 6회차 compare-windows <---------------+
```

```bat
.venv-cmd\Scripts\python.exe -m simcompare candidates-change --config config.yaml --cnt-from-dl1-root dl1 --align-search-um 30 --align-min-dice 0.3 --align-layer-tolerance-um 1 --change-tolerance-um 0.5 --window-um 10 --merge-overlap 0.5 --top-k 20 --mask-derivation-rule between_stack_order_v1 --out changes
```

**인수.** 모두 필수이며 기본값이 없다. 예시 값은 쓰기 쉬운 출발점일 뿐이고 제품에 맞게 바꾼다.

| 인수 | 의미 | 예시 값과 근거 |
|---|---|---|
| `--config` | `numeric_config_v1` YAML. reference·target은 각각 real_hotspot_v1 단일 제품·단일 frame이다. 두 제품의 pixel pitch가 같아야 한다 | `real.yaml`(4회차 설정과 같다) |
| `--cnt-from-dl1-root` | 두 제품의 CNT 층 PNG(`CNO/`, 예전 `cno/`)와 전극 폴더, 새 구조면 `Layer/`가 있는 PNG root. 제품 폴더 이름 = `product_id`. 전극 폴더 이름은 두 모델의 전극 집합을 비교하는 데 쓴다. 폴더 구조는 아래 "입력 폴더 구조" | 3회차 `--cnt-from-dl1-root`와 같은 root |
| `--align-search-um` | CNT 정렬 탐색 반경(µm). 최소값은 pixel pitch의 2배 이상이다(코드 검사) | 30. 권장값은 예상 레이아웃 이동과 반복 패턴 주기를 모두 덮는 값이다. 주기가 탐색 반경 안에 있으면 `peak_ambiguous`와 대안 offset으로 표시된다 |
| `--align-min-dice` | 정렬 최소 Dice `[0, 1]`. 이보다 낮으면 `aligned_uncertain`이다 | 0.3. 층 패턴이 성기면 낮추고, 정렬을 엄격히 보려면 올린다 |
| `--align-layer-tolerance-um` | 층별·4분면 offset이 전역 offset과 달라도 되는 거리(µm) | 1. 이를 넘으면 `layer_offsets_disagree`(불확실) |
| `--change-tolerance-um` | 변경 판정 허용 이동 거리(µm). 이 안의 이동은 변경으로 세지 않는다 | 0.5. 정렬 오차와 공정 변동을 합친 값으로 둔다 |
| `--window-um` | 변경 창 한 변(µm) | 20. 6회차 창 10/20/30 µm 중 가운데 값이다 |
| `--merge-overlap` | 두 창의 `intersection/min(area)`가 이 값 이상이면 병합한다(기본은 층 안에서). `(0, 1]`. 0.5이면 두 창이 50 % 이상 겹칠 때 병합하고 50 % 미만으로 겹치는 창은 따로 남는다. 창 격자 간격은 W/4이다 | 0.5 |
| `--merge-scope` | 선택(생략 시 `layer`, 이전 동작). `all`이면 층을 넘어 병합한다. 대표 창과 같은 위치의 구성원 층 case를 모두 다시 세어 기여 case에 `layer`를 붙인다. `score_um2`·`score_layer`·`sum_residual_um2`·`union_*`는 대표 층 창의 값이고, `changed_case_count`·`contributing_cases`·`one_sided_cases`는 `layers`의 모든 층 case를 센다. 한계: 연쇄 병합에서 대표 창과 먼 구성 창에서만 일어난 변경은 다시 세지 않는다 | 생략 |
| `--suppress-overlap` | 선택(생략 시 억제 없음). `[0, 1]`. 병합·정렬 뒤 `top-k` 전에, 순위가 앞선 대표 창과 겹침 비율(`ox*oy/W²`)이 이 값 이상인 대표 창을 층 구분 없이 버린다. **`0` = 조금이라도 겹치면 억제**(겹침 > 0, 맞닿기만 한 창은 겹침이 아니다; 정책 문자열 `greedy_score_order_any_window_overlap_all_layers`). 예전 문서의 "0 은 첫 ROI 외 전부 억제"는 오류였고 고쳤다. funnel에 `suppressed` 단계가 생긴다 | 0.3(4회차 실행기는 `0.3`) |
| `--unit-align-um` | 선택(생략 시 하지 않음). Layer 전역 교차 확인과 unit 4개 offset의 탐색 반경(µm). 두 모델 모두 새 입력 구조(`Merged_Layer` + `Layer/`)여야 한다(아니면 쓰기 전에 종료 코드 2 `layer_folder_required`). 반경(px) = round(값 / pitch)는 `2·|ΔU| + 2` px 이상, 300 px 이하여야 한다(아래 "반경 요구식"). 지정하면 `alignment.json`에 `layer_global_check`·`unit_offsets`·`unit_geometry`·`layer_inputs`가 더해진다. `--apply-align`을 주지 않으면 진단 전용이라 계산·순위는 바뀌지 않는다 | 3(예시 값. 실자료 ΔU로 맞춘다) |
| `--apply-align` | 선택. `diagnostic`(생략 시, 기존 동작) / `unit` / `local`. `unit`은 unit offset 장(場)을 변경 지도·창 재계산·target 방향 질의 중심·직접 쌍에 적용한다(`--unit-align-um` 필요: `apply_align_requires_unit_align`). `local`은 그 위에 ROI마다 Layer 층 + CNT 층 Dice 우선 국소 offset을 구해 재점수화에 적용한다(`--local-align-um` 필요: `apply_align_requires_local_align`; `--unit-align-um`은 시작점용 선택). 아래 "정렬 적용" 참조 | `local` |
| `--refine-pool` | 선택, `--apply-align local` 전용(아니면 `refine_pool_requires_local`). 창 후보 점수 상위 N개만 국소 보정·재점수화한다. 1 이상의 정수. 생략하면 `10 × top-k`. 풀 밖 창은 순위에서 빠지고 funnel `refine_pool` 단계에 `outside_refine_pool`로 센다 | 생략 |
| `--unit-fold` | 선택(플래그). 억제 뒤·`top-k` 앞에서 서로 다른 unit의 같은 unit 안 위치 ROI를 대표 하나로 접는다. 기준은 reference 모델의 unit 크기다. 접힌 ROI는 대표의 `unit_fold.replicas`에 남는다 | 켬 |
| `--unit-fold-tolerance-um` | 선택, `--unit-fold`와 함께만(아니면 `invalid_candidates_change_argument`). 접힌 좌표(unit 안 위치) 거리 허용값(µm, 0 이상). 생략하면 `--window-um / 4` | 생략 |
| `--electrode-png-root` | 선택(생략 시 `--cnt-from-dl1-root`, 이전 동작). `alignment.html` 5절 층 묶음의 전극 형상 바탕에만 쓰는 PNG root다. 폴더 구조는 `--cnt-from-dl1-root`와 같은 두 가지(예전 `<root>/<product_id>/<전극>/<층>.png`, 새 `<root>/<product_id>/Merged_Layer/<전극>/<층>.png`)를 받는다. 전극 목록 비교(`electrode_set_differs`, 조합 짝짓기)에는 쓰지 않고 항상 `--cnt-from-dl1-root`를 쓴다. 폴더가 아니면 쓰기 전에 종료 코드 2(`electrode_png_root_missing`). 지정하면 `change_args`·`execution.json`의 `args`에 `electrode_png_root` 키가 생긴다. 그 층 PNG가 없거나 읽을 수 없으면 바탕 없이 그리고 "전극 형상 없음"으로 적는다. 메모리 추정이 한도를 넘으면 바탕만 빼고 경고 `alignment_report_layer_base_skipped`(종료 코드 3). 빈 문자열(`''`)은 폴더로 보지 않고 같은 종료 코드 2다. 바탕이 없는 그림의 캡션은 사유를 적는다: "전극 형상 없음 (파일 없음)", "(제품 폴더 없음)", "(읽기 실패: <코드>)", "(읽기 생략: 메모리 한도)" | 생략 |
| `--local-align-um` | 선택(기본값 없음, 생략하면 진단 없음). ROI 국소 정렬 진단의 탐색 반경(µm). round(R / pitch) 가 2..30 px 가 아니면 쓰기 전에 종료 코드 2(`local_align_um_invalid`)이다. 예: pitch 0.1 µm 에서 0.15 µm 는 1 px 로 거부(부동소수 0.15/0.1 < 1.5), 0.04 µm 는 0 px 로 거부, 0.2 µm 는 2 px 로 허용. 지정하면 `roi_local_alignment.json` 을 더 쓰고 `alignment.html` 5절 ROI 마다 "국소 정렬 진단" 표, 1절에 상태별 개수 한 줄(선형 적합이 되면 배율 추정 ppm)을 더한다. `--apply-align local`을 주지 않으면 진단 전용이다: 순위·기여 case·export·경고·종료 코드는 바뀌지 않는다. `--apply-align local`이면 이 인수가 적용 경로의 탐색 반경이고, 진단 경로의 `2..30 px` 규칙 대신 `|ΔU| + 2 px`(`--unit-align-um` 없으면 `2·|ΔU| + 2 px`) 이상 300 px 이하여야 한다(아래 "반경 요구식"; 어긋나면 쓰기 전에 `local_align_um_too_small_for_unit_delta` 또는 `local_align_um_invalid`, 종료 코드 2). 그림을 만들지 않았거나(`images_skipped`) 전극 바탕을 읽지 않았거나(`electrode_bases_skipped`) 진단 메모리 한도를 넘으면(`diagnostic_memory_limit`) 전 ROI 가 `skipped` 이고 표에 사유가 나온다 | 1 (예시 값). 상태 정의는 `docs/data_contract.md` |
| `--top-k` | 방향(`reference_to_target`, `target_to_reference`)별 질의 수 | 20. 설계자가 보는 ROI 수에 맞춘다 |
| `--mask-derivation-rule` | DL2 `mask_derivation` 규칙. `between_stack_order_v1`만 허용한다(없거나 다른 값이면 종료 코드 2: `mask_derivation_rule_required`·`mask_derivation_rule_unknown`) | `between_stack_order_v1` |
| `--out` | 새 출력 폴더. 이미 있으면 아무것도 건드리지 않고 종료 코드 2 | `data/changes` |

대표 지름은 5 µm 하나로 고정이다. `map.diameter_um`이 5가 아니면 `candidates_change_diameter_fixed_5um`(코드 2)이다.

**입력 폴더 구조(`--cnt-from-dl1-root`, `--electrode-png-root`).** 모델 폴더 안에 `Merged_Layer` 폴더(대소문자 무관)가 있으면 새 구조, 없으면 예전 구조다. 두 구조를 모두 읽는다.

| | 새 구조 | 예전 구조 |
|---|---|---|
| CNT | `<root>/<모델>/CNO/<n>-<층>.png` | `<root>/<모델>/cno/<n>-<층>.png`(내부 동일) |
| 전극 | `<root>/<모델>/Merged_Layer/<전극>/<n>-<층>.png` | `<root>/<모델>/<전극>/<n>-<층>.png` |
| Layer | `<root>/<모델>/Layer/<n>-<층>.png`: 층마다 1장, 원본 층 영상, 크기 = 전극 PNG와 같은 2×2 unit 영역, 검정 = 형상 | 없음. `Layer`라는 이름의 폴더는 전극 폴더로 읽는다 |

- CNT 폴더 이름은 `CNO`/`cno`/`Cno` 등 대소문자를 구분하지 않는다(casefold). 대소문자만 다른 CNT 폴더가 둘이면 `cnt_folder_duplicate`(코드 2). 예전 자료도 `cno` 소문자 그대로 읽는다. **예전 구조에서도 같은 규칙이라** `CNO`뿐 아니라 `Cno`·`cNO` 폴더도 CNT로 읽는다. 예전 코드는 이 폴더들을 전극 폴더로 읽었으므로 그런 이름의 전극 폴더가 있으면 이름을 바꾼다 (R1 adv:5).
- 새 구조에서 `CNO`, `Merged_Layer`, `Layer` 외의 폴더가 PNG를 담으면 `layout_structure_mixed`, PNG가 없으면 경고 `unrelated_folder`(정보)다. `Merged_Layer`가 대소문자만 다르게 둘이면 `layout_structure_mixed`, `Layer`가 둘이면 `layer_folder_duplicate`다.
- 같은 전극·층이 둘이면 `duplicate_electrode_layer`, 같은 Layer 층이 둘이면 `duplicate_layer_png`, Layer 파일 이름이 `<n>-<층>.png`가 아니면 `invalid_layer_png_name`이다(모두 코드 2).
- Layer 폴더는 DL1 입력과 별개다(DL1은 읽지 않는다). Layer 폴더가 없으면 `--unit-align-um`·`--apply-align`·Layer 그림을 쓸 수 없고 예전 그림·예전 정렬만 쓴다.
- 두 모델 중 하나라도 새 구조 + Layer PNG가 아니면서 `--unit-align-um`, `--apply-align unit|local`이 있으면 `layer_folder_required`(코드 2, 쓰기 전). 구조가 서로 다른 두 모델(예: reference 새 구조, target 예전 구조)도 마찬가지다.

**unit 크기와 반경 요구식.** hotspot frame은 2×2 unit 원본 영상에서 사방 500 px를 잘라낸 중앙 영역이다. unit 크기(px) = (hotspot 크기 + 1000) / 2 (축마다, 홀수면 내림, `unit_size_odd_px`로 표시), 1 px = 0.1 µm. 두 모델의 unit 크기 차이 `ΔU` = target unit − reference unit (px, 축마다). 구하는 법: `alignment.json`의 `frame_shape_delta_px`를 2로 나눈다(`unit_geometry.delta_u_yx_px`에 이미 기록된다). `|ΔU|max`는 축별 `|ΔU|` 중 큰 값이다.

| 인수 | 필요한 반경(px) | 상한 | 어기면(쓰기 전, 코드 2) |
|---|---|---|---|
| `--unit-align-um` | `2·|ΔU|max + 2` | 300 | `unit_align_um_too_small` / `unit_align_um_too_large` |
| `--local-align-um`, `--apply-align local` + unit 정렬 | `|ΔU|max + 2` | 300 | `local_align_um_too_small_for_unit_delta` / `local_align_um_invalid` |
| `--local-align-um`, `--apply-align local` + unit 정렬 없음 | `2·|ΔU|max + 2` | 300 | 같음 |
| `--local-align-um` 진단 전용(`--apply-align local` 아님) | 2 ~ 30 (이전과 같음, 불변) | 30 | `local_align_um_invalid` |

반경(px) = 값(µm) / pitch의 반올림이다. `--unit-align-um`은 .5 올림이고, `--local-align-um`(진단·적용 모두)은 Python `round`(짝수 반올림)다. 예: `--local-align-um 0.45`는 4 px(`--unit-align-um 0.45`는 5 px). 예시 값 `--unit-align-um 3`(30 px)은 `|ΔU|max ≤ 14`, `--local-align-um 1`(10 px)은 `|ΔU|max ≤ 8`까지 맞는다. 둘을 함께 쓰면 `|ΔU|max ≤ 8`이다. 실자료 ΔU로 `--unit-align-um ≥ (2|ΔU|+2)·0.1 µm`, `--local-align-um ≥ (|ΔU|+2)·0.1 µm`를 맞춘다.

**정렬 적용(`--apply-align`).** 전역 정렬은 CNT 3층이 계속 정한다. Layer 정렬은 그것을 교차 확인하고(`layer_global_check`; CNT와 다르면 `layer_global_check_differs` 정보) unit 4개의 offset을 따로 구한다. 국소 정렬은 ROI마다 Layer 층 + CNT 층을 **Dice 우선**으로 맞춘다. 평균·중앙값은 쓰지 않는다. 층마다 후보 offset별 Dice를 구해 Dice가 가장 높은 층의 선택을 따르고, 그 층의 1·2위가 모호하면 다음 Dice 층으로 동률을 가린다. 변경된 층도 빼지 않는다. 상태와 규칙은 `docs/data_contract.md`.

- `diagnostic`(생략): 계산·순위는 이전과 같다. unit 정보는 `alignment.json`에만 더해진다.
- `unit`: 변경 지도·창 재계산·target 방향 질의 중심·직접 쌍에 unit offset을 적용한다. 지도·열지도는 unit offset을 쓴다.
- `local`: 창 후보 점수 상위 `--refine-pool`개를 국소 보정하고 재점수화한 뒤 병합 → 억제 → 접기 → `top-k` 순으로 진행한다. ROI의 기여 case·점수는 그 ROI의 **적용 offset**을 쓴다. 재점수 뒤 변경이 0이 된 창은 funnel `refine_pool`에서 `zero_after_refine`로 빠지고 병합 다리도 놓지 않는다.
- 적용하지 않은 offset(`low_score`, `ambiguous` 등)은 기록만 하고 전역 offset을 쓴다. 잘 맞지 않는 ROI도 숨기지 않고 표시한다.

**소요 시간·메모리.** 아래는 개발기 **합성 기준** 측정이다(실자료 변경이 더 크면 재점수 비용이 늘 수 있으므로 실자료로 다시 잰다). 3000×3000 frame, Layer 10층 + CNT 10층, 창 3150, 풀 200: 전체 27.9 s(unit 정렬 10.6 s, 국소 보정 5.1 s, 재점수 0.04 s), 프로세스 peak RSS 3161 MB. 같은 크기의 `change_maps` scalar/field는 0.3 s에 ~1.0 GiB, unit 단계는 5.2 s에 +806 MiB(추정 501 MiB; `change_candidates`가 2배로 admission). ROI 200개 국소 정렬은 반경 10 px에서 4.3 s/530 MiB, 30 px에서 12.8 s/606 MiB. 실행마다 `execution.json`의 `timings_s`(`unit_align`, `layer_read`, `local_refine`, `local_layer_read`)가 단계별 초를 남긴다.

**점수와 순위.** 순위 점수 `score_um2`는 층별 hotspot **합집합의 변화 면적**이다. 합집합 변화 = (reference 합집합 XOR 정렬한 target 합집합) 가운데 `--change-tolerance-um` 이동으로 설명되지 않는 부분이다. 6회차 보고서의 Δ_union과 같은 기준이다. 조합별 잔차의 합(`sum_residual_um2`), 바뀐 조합 수(`changed_case_count`), `union_area_reference_um2`, `union_area_target_um2`, `union_delta_um2`는 **속성**이며 순위에 쓰지 않는다. CNT 층별 변경 면적은 `cnt_change_um2_by_layer`에 속성으로 남는다. 점수가 같으면 잔차 합, 층, 중심, region id 순이다. 배선 변경 면적(`wiring_change_um2`)은 계산하지 않는다(보류).

**합집합에 들어가는 조합.** (1) 두 모델에 모두 있는 조합(짝). (2) 한쪽 모델에만 있는 조합: 그 모델의 합집합에만 들어간다(`one_sided_case`). (3) 층이 바뀐 조합: 쌍 하나(a=L1, b=L2)로 다루지 않고 한쪽 조합 두 개(L1은 L1 합집합, L2는 L2 합집합)로 다룬다. 층 사이 합집합은 만들지 않는다. (4) 한쪽 모델에만 있는 전극(배선)의 조합도 비교 대상이다: 새 전극은 `new_electrode`, 없어진 전극은 `removed_electrode` 표시가 붙고 `appeared`/`disappeared`로 분류된다. 두 모델의 DL1 전극 폴더 집합이 다르면 `electrode_set_differs`가 `notes`에 정보로 남는다(종료 코드에 영향이 없다).

**한쪽 조합의 직접 쌍.** 직접 쌍의 한 side에 case가 없으면(반대 모델에 그 조합이 없음) `endpoints`와 `failure_type`을 쓰지 않고 `analysis_layer`와 group 필터(`source_id`, `process_step_id`, `exposure_id`, `unit_id`)만 쓴다. 필터는 필드마다 따로 정하지 않고 group 튜플 `(source_id, process_step_id, exposure_id, unit_id)` 단위로 정한다. 반대 dataset의 같은 층 case가 튜플 하나만 가지면 그 튜플이고, 튜플이 여럿이면 case가 있는 쪽 case의 튜플과 같은 튜플이 정확히 하나일 때 그 튜플이다. 그래도 정해지지 않으면 필터를 비우고 사이드카 `flags`에 `filter_unresolved_<a|b>`를 적는다. 반대 dataset에 그 층의 case가 **하나도 없으면** 그 쌍은 `blocked`이고 `reasons`는 `["one_sided_layer_absent_<a|b>"]`다(예: 층 변경 L1→L2에서 반대 모델에 L1 case가 전혀 없음). 이 쌍은 `compare-windows`의 `analysis_layer_not_in_source_*`에 맡기지 않고 여기서 막는다.

**출력 폴더.** 같은 입력·인수는 같은 바이트를 만든다(경로는 출력 폴더 상대, 시각·호스트 없음).

| 파일 | 내용 |
|---|---|
| `candidate_queries_v1.json` | 기존 스키마에 키를 더한 것(`roi_source=cnt_change`, `alignment`, `diameter_um`, `change_args`, `change_map`, `union_basis`, `alignment_report`, `direct`, 질의별 점수·속성; 아래 `docs/data_contract.md`) |
| `alignment.json` | 정렬 결과(`cnt_alignment_v1`): offset, Dice, peak 비율, 대안 offset, 층별·4분면 offset |
| `alignment.html` | 정렬 요약·층별 겹침·변경 강조 지도·ROI 목록·ROI 상세(층 묶음)·열지도·각주. 구성은 아래 "alignment.html 구성". 외부 참조 없이 `file://`로 열린다 |
| `direct/roi_correspondence.json` | 6회차 `compare-windows`가 그대로 읽는 `roi_correspondence_v1`(DL2 건너뛰기) |
| `direct/direct_pairs_sidecar.json` | `direct_pairs_sidecar_v1`: 쌍별 정렬 상태·점수·속성·불확실성 |
| `reference_export/`, `target_export/` | 질의 `review_rois.json`(DL2 입력, manual 행)과 `mask_derivation.json` |
| `reference_index_export/`, `target_index_export/` | 검색 대상 index export(`review_rois.json`)와 `mask_derivation.json` |
| `funnel.json`, `funnel.csv` | 방향별 깔때기(`change_candidates` → `merged` → `top_k` → `exported_ready`)와 직접 쌍 수 |
| `execution.json` | 실행 요약 |

**종료 코드.** 0 = 경고 없이 완료. 3 = 부분 결과: 정렬 `aligned_uncertain`·`alignment_failed_identity_used`, blocked 질의, `direct_pairs_blocked`, `top_k_not_filled`, index coverage 불완전, `alignment_report_images_skipped`(그림 생성 메모리 추정이 한도 초과: 표만 쓴다), `alignment_report_layer_base_skipped`(전극 형상 바탕 읽기 메모리 추정이 한도 초과: 바탕만 뺀다), `alignment_report_layer_figures_skipped`(Layer stack 그림의 메모리 추정이 한도 초과이거나 Layer PNG를 읽지 못함: Layer 그림만 빼고 예전 그림으로 쓴다) 등. 2 = 입력·인수 오류. 새 오류 코드(전부 쓰기 전): `layer_folder_required`, `unit_align_um_too_small`, `unit_align_um_too_large`, `local_align_um_too_small_for_unit_delta`, `local_align_um_invalid`, `apply_align_requires_unit_align`, `apply_align_requires_local_align`, `refine_pool_requires_local`, `layout_structure_mixed`, `layer_folder_duplicate`, `cnt_folder_duplicate`, `duplicate_electrode_layer`, `duplicate_layer_png`, `invalid_layer_png_name`, 그리고 값 오류용 `invalid_candidates_change_argument`(`--apply-align` 값, `--refine-pool` < 1, `--unit-fold-tolerance-um` 단독·음수, `--suppress-overlap` 범위 밖). 그 밖의 2 = (필수 인수 누락, 이미 있는 `--out`, 인수 값 오류, config 종류 오류, 제품·frame 개수, pitch 불일치, 탐색 반경 < 2 × pitch, 창 크기 오류, 없는 `--cnt-from-dl1-root`, `--mask-derivation-rule` 누락·오류). 쓰는 도중 실패하면 이 실행이 만든 파일과 폴더만 지운다. **ETX 실행기는 3을 '계속'으로 처리하므로** 결과는 로그의 `candidates-change exit N` 줄과 `reasons`로 판정한다. 존재하지 않거나 폴더가 아닌 `--cnt-from-dl1-root`는 **쓰기 전에** 종료 코드 2다: `오류: cnt_dl1_root_missing: --cnt-from-dl1-root 경로가 없습니다: <입력> (resolved: <절대 경로>)`. `--out`은 만들어지지 않고 ETX 실행기는 멈춘다(종료 2는 '계속'이 아니다). root는 있는데 모델 폴더나 `cno/`가 없으면 지금처럼 정렬 실패(종료 코드 3: `alignment_failed`, `cnt_missing_reference`, `cnt_missing_target`, 직접 쌍 전부 blocked)다.

**`mask_derivation.json`(R1).** DL2는 `1.png`/`2.png`를 case 처리 마스크로 보고, 두 제품의 마스크가 같은 방식으로 만들어졌을 때만 비교한다. 그 사실을 질의 export 2개와 index export 2개 폴더마다 `mask_derivation.json`(`psdl2_mask_derivation_v1`)으로 동봉한다. 형식·규칙은 `export-template-rois`와 같은 writer를 쓴다: 규칙 `between_stack_order_v1`, vertical 은 위 전극 마스크에서 두 끝 층 사이 층(제품의 `condition_assignment.layer_stack` 기준)을 제거, lateral 은 제거 없음(`processed_endpoint: null`), CNT 는 DL1 `cno` 합집합, VIA 미사용. 항목은 그 폴더 `review_rois.json`의 **ready 행** case(failure_type + 정렬한 endpoints)마다 하나다. blocked 행과 한쪽 조합(반대 모델에 case가 없어 행이 없음)은 항목이 없다(DL2 `mask_derivation_case_missing`은 ready 행에만 적용된다). 경로는 `candidate_queries_v1.json`의 `mask_derivation_files`(방향별 `query`/`index`)와 `execution.json`의 `outputs`에 있다. DL2 `psdl2_dataset_v1` 매니페스트의 `mask_derivation` 값으로 출력 폴더 기준 상대 경로를 그대로 쓴다. DL2 문서 흐름의 DB·후보는 제품별 `export-template-rois` 출력(template export)이고, 이 `*_index_export` 흐름도 같이 지원한다. 아래는 `*_index_export` 를 DB source 로 쓰는 예시다:

```json
{"schema_version": "psdl2_dataset_v1",
 "sources": [{"export": "target_index_export/review_rois.json", "export_sha256": "<64 hex>",
              "mask_derivation": "target_index_export/mask_derivation.json"}]}
```

**정렬 상태와 사유.**

| 상태 | 사유 | 의미와 동작 |
|---|---|---|
| `aligned` | 없음 | 한 offset으로 정렬했고 모든 확인을 통과했다 |
| `aligned_uncertain` | `peak_ambiguous`(2×2 같은 반복 패턴: 대안 offset과 점수가 비슷), `peak_plateau`, `layer_offsets_disagree`, `residual_nonuniform`, `dice_below_min`, `search_bound_hit`, `cnt_layer_set_mismatch`, `cnt_empty_<side>_<층>` | best-effort offset으로 계속 진행한다. 직접 쌍은 `ready`로 두고 불확실성은 사이드카 `alignment_reasons`에만 쓴다. `alignment.html`에 대안 offset이 보인다. 종료 코드 3 |
| `alignment_failed_identity_used` | `no_usable_layers`, `no_overlap`, `cnt_missing_reference`, `cnt_missing_target` | offset 0으로 계속한다. 레이아웃 이동이 변경으로 잡히므로 모든 직접 쌍은 `blocked`(`alignment_failed`)이다. 종료 코드 3 |

정렬 부호식은 `p_target = p_reference + offset`이다. target 방향 질의 중심 = reference 질의 중심 + (frame 원점 차이 + offset)이다.

**한계.**

- `compare-windows`는 `direct/` 파일이 가리키는 입력 해시(config, manifest, CNT 파일)를 검증하지 않는다. 사이드카 `limitations`에 같은 문장이 있다. 입력을 바꾼 뒤에는 4회차부터 다시 한다.
- 반대 방향 질의의 `no_case_in_this_model`은 실패가 아니라 정보다. 한쪽 모델에만 있는 조합은 그 모델 방향에서만 case 맥락이 있으므로 반대 방향 질의는 ROI 행이 없고 `blocked`로 표시된다. 종료 코드에 영향이 없다.
- 변경 지도 합집합은 층의 **모든** group(source·process_step·exposure·unit)을 모은다. `compare-windows`는 GroupKey 하나만 잡는다. 한 데이터셋에 group이 여럿이면 두 숫자가 다를 수 있다.
- 합집합 기준에서 남은 차이: 중복 case key는 `duplicate_case_key`로 제외되고, 관측되지 않은 case는 합집합에 들어가지 않는다.
- 한 ROI의 직접 쌍들은 중심을 공유한다(기여 case마다 쌍이 하나). `compare-windows`의 `duplicate_centres` 경고는 정상이다.
- `alignment.html`의 PNG는 zlib 구현에 따라 압축 바이트가 달라질 수 있다. 환경(Python 3.9 / 3.11)이 다르면 이 파일의 sha256이 다를 수 있다. 다른 출력 파일은 바이트 단위로 같다(테스트).
- 중간에 하드 실패하면 `alignment.html`은 없다. 정렬이 실패·불확실한 경우에는 항상 쓴다.
- 층 사이 합집합을 만들지 않는다. 층이 바뀐 조합은 위처럼 한쪽 조합 둘이다.
- 실데이터 규모, 메모리·시간, ETX Linux 실행은 아직 검증하지 않았다(`docs/plan/change_roi/README.md`).
- frame 크기가 다르면 두 frame 의 공통 영역만 비교한다. 왼쪽 위 인덱스를 맞추고 offset 만 더하며 resize 하지 않는다. unit 별 offset은 `--unit-align-um`/`--apply-align`으로 구한다(위 "정렬 적용").
- X7 한계: 탐색 범위 밖의 진짜 peak와 안쪽 부차 peak가 함께 있으면 원리상 완전히 막지 못한다. 막는 장치는 `--align-min-dice`(선택 offset의 Dice가 그 미만이면 `low_score`)와 경계 1 px 안 선택의 `search_bound_hit`(적용 안 함)뿐이다.
- Layer 그림 조건: 두 모델 모두 새 구조 + Layer PNG이고 그림을 만들 때만 그린다. 아니면 예전 그림이다. 국소 정렬 진단(`--local-align-um`, 적용 아님) 실행의 층 묶음 바탕은 계산에 쓰는 배열과 같아야 하므로 Merged 전극 PNG를 유지한다.
- 면적 지표(`area_reference_um2`, `area_target_um2`, `union_area_*`)는 두 모델 모두 같은 reference 좌표 창과 같은 공통 비교 영역(침식 후)에서 센다. 그래서 frame 크기가 달라도 두 값을 바로 비교할 수 있다. 공통 비교 영역은 두 frame 어느 쪽보다 작을 수 있으며, frame 전체 대비 비율(`alignment.json`의 `fraction_of_reference`·`fraction_of_target`)은 분모가 각 frame 이라 서로 다르다.
- `alignment_report_layer_base_skipped` 경고의 `estimated_bytes`·`reserved_bytes` 에는 보고서 그림이 이미 잡고 있는 바이트가 들어가지 않는다. 그래서 "추정 + 예약 ≤ 한도"로 보여도 바탕이 빠질 수 있다.

**alignment.html 그림(새 구조 + Layer).** 5절 ROI 상세의 위치 그림은 reference Layer를 모든 층, stack order 순 색, 투명도로 쌓은 바탕 위에 변경 위치(진자홍)와 빨간 ROI 박스(흰 테)를 그린다. ROI 주변 crop(창의 3~5배, 원본 해상도)도 Layer stack에 빨간 박스를 그린다. 축소는 층 분율 블록 평균으로 해서 회백색 포화를 피한다. 크기는 `MAX_HTML_BYTES`(30 MiB) 상한 안에서 순위 낮은 ROI의 crop부터 줄이거나 뺀다. Layer 폴더가 없으면 예전 그림이다.

**alignment.html 층별 비교(새 구조 + Layer).** ROI 마다 stack crop 두 장 아래에 층별 비교를 둔다. 층마다 한 줄에 세 장(reference · target · 차이)이며, 형상은 검정·바탕은 흰색이다. target 은 그 ROI 의 적용 offset 으로 reference 좌표에 옮긴 것이다. 차이 그림은 공통 = 진회색, reference 만 = 파랑, target 만 = 빨강, 둘 다 없음 = 흰색이고, 비교 영역 밖은 연회색, ROI 창은 주황 테두리다. 창 안·비교 영역 안에서 한 픽셀이라도 다른 층(또는 한쪽 모델에만 있는 층)은 차이 픽셀 수 순으로 펼치고, 나머지는 `차이 없는 층 n개` 로 접는다. 층 캡션에 창 안 차이 픽셀 수와 µm², reference 만·target 만 픽셀 수를 적는다. PNG 는 팔레트 PNG 다. 크기 상한에서는 crop 과 같이 반 해상도 → 생략("층별 비교 생략(크기 상한)") 순이다. 층별 bool crop 이 메모리 한도를 넘으면 층별 비교만 빼고 경고 `alignment_report_layer_compare_skipped`(종료 코드 3)를 남긴다.

**alignment.html 구성(v3 순서).** 1 요약(정렬 상태·offset·frame 크기·합집합 조합 수), 2 CNT 정렬과 정렬 진단(층별 겹침 정렬 전·후, 대안 offset), 3 변경 강조 지도(변경 위치를 빨강으로), 4 ROI 목록(순위 표, 각 ROI 로 이동), 5 ROI 상세(위치 그림, 기여 조합 표, 그 ROI 에 불량 조합이 있는 층마다 층 묶음 = 비교·reference·target 그림에 전극 형상 바탕. 층이 4개를 넘으면 창 안 변경 면적 합이 큰 4개만, 기여 조합이 없는 ROI 는 묶음 없이 한 줄), 6 변경 지도 열지도(참고, 접어 둠), 7 각주. 파일 크기가 상한을 넘으면 순위가 낮은 ROI 의 crop 부터 줄이거나 뺀다.

### 취약점 ROI (candidates-dv) — 현행

두 제품의 `dv-grade` 출력(전극쌍 ΔV 등급)으로 가중한 취약점 영역을 뽑는다. real_hotspot_v1 전용이다. 변경점 ROI(`candidates-change`)와 독립이고 출력 폴더도 따로 쓴다. 상세는 [dv_candidates.md](dv_candidates.md)에 있다.

```bat
.venv-cmd\Scripts\python.exe -m simcompare candidates-dv --config real.yaml --dv-grades-reference dv_grade_ref --dv-grades-target dv_grade_tgt --min-count 4 --roi-grade high --window-um 20 --merge-overlap 0.5 --out changes_dv
```

- 모든 인수(`--config`, `--dv-grades-reference`, `--dv-grades-target`, `--min-count`, `--roi-grade`, `--window-um`, `--merge-overlap`, `--out`)는 **필수이며 기본값이 없다**. `--max-candidates N`은 선택이다. 위 값은 예시다.
- `--roi-grade high`는 High 쌍으로 센 전극 수(`count_high`), `low_or_high`는 Low·High 쌍으로 센 전극 수(`count_visible`)로 영역을 고른다. 비가중 `count_all`은 비교용으로만 계산한다.
- 출력: `candidate_queries_v1.json`(`roi_source: candidates_dv_v1`), `vulnerability_v1.json`, `grade_maps/`, `funnel.json`/`funnel.csv`, `*_export/`, `*_index_export/`, `execution.json`.
- 종료 코드: 0 = 완전, 3 = 부분 결과(등급 없는 case, case 없는 제품, 지도·index coverage 불완전 등), 2 = 입력·설정 오류(dv-grade 제품·case 불일치, 같은 dv-grade 폴더, 중복 case_id 등). 입력 오류는 아무것도 쓰기 전에 멈춘다.
- 한계: 질의 중심은 영역 bbox 중심이라 U 자·고리 영역에서는 영역 밖일 수 있다(`query_centre_invalid`, `selected_cells_outside_window`). 쌍 표의 ROI 내 CA는 병합 전 등급 영역 기준이다. 실규모 실측은 아직 없다.

#### 수치 근거 보고서 (vulnerability-report)

```bat
.venv-cmd\Scripts\python.exe -m simcompare vulnerability-report --candidates-dv-dir changes_dv --out vulnerability_report
```

- `candidates-dv` 출력 폴더(`--candidates-dv-dir`)만 읽고 수치를 다시 계산하지 않는다. `--out`은 입력 폴더 밖의 새 폴더이며 필수다. `--top-n`(표시할 상위 ROI 수, 기본 10)은 선택이다.
- 출력: `report.html`(외부 CDN 없음), `report.json`, `execution.json`. 등급 순위와 비가중 순위 비교, ROI 별 쌍 표, layer 별 색 전극 layout 지도를 보인다. layer 색은 layer 이름의 자연 정렬 순서(슬롯 4개, 초과는 색 없이 점선)다.
- 입력 기록의 `data_dir`(절대 경로)로 전극 layout 자산을 다시 읽으므로 `candidates-dv`와 같은 서버에서 이어서 실행한다. 자산이 없거나 해시가 다르면 layout 없이 그리고 종료 코드 3이다.
- 종료 코드: 0 = 완전, 3 = 입력이 partial이거나 경고가 있다, 2 = 입력 없음·모양 오류·`--out` 오류.

### 취약점 ROI (candidates) — 비가중(참고)

아래는 `max_count` 기준 비가중 취약점 ROI(`candidates`)의 설명이다. ΔV 등급을 쓰지 않는 참고용이며 현행 취약점 ROI는 위 `candidates-dv`다. 변경점 ROI와 같은 출력 스키마(`candidate_queries_v1`)를 쓰지만 점수·순위가 다르다.

두 제품(`config.reference`, `config.target`)의 electrode-map 영역을 **양방향**으로 뽑아 질의 ROI로 정리하고, 바로 매칭에 쓸 export와 깔때기 표를 만든다. 점수·CA·Δ·확정·`mutual/one_way`는 다루지 않는다(후속 Task).

```bat
.venv-cmd\Scripts\python.exe -m simcompare candidates --config config.yaml --min-count 3 --window-um 20 --merge-overlap 0.5 --out candidates_k3
```

- `--min-count K`, `--window-um W`, `--merge-overlap R`은 **필수이며 기본값이 없다**. `--max-candidates N`은 선택이며 방향(제품)별 질의 수 상한이다. 대표 지름은 `map.diameter_um`(없으면 electrode-map 기본 5 µm)을 쓴다. 출력 폴더는 **새 경로**여야 하고 이미 있으면 아무것도 건드리지 않고 종료 코드 2다.
- 설정: `reference`/`target` 각각 단일 제품(`product_id`, `data_dir`). numeric_v2(합성/데모)는 `asset_manifest`를, real_hotspot_v1은 `condition_assignment`, `asset_manifest`, `electrode_import_record`(export-review-rois와 같은 조건)를 쓴다. 새 설정 키는 없다. 둘은 같은 종류여야 한다(`mixed_real_and_numeric_source_formats`).
- 병합: 같은 제품·design·frame의 영역(layer 무관)을 region_id 순서로 union-find(전이적)한다. 각 영역의 창은 `centre_xy_um`을 중심으로 한 변 W의 정사각형이고, 두 창의 `intersection/min(area)`가 R 이상이면 합친다(모든 창이 같은 크기이므로 `intersection/W²`). 겹침은 frame으로 자르지 않은 공칭 창으로 판정한다. 대표 = `max_count`가 가장 큰 영역(동률은 면적 큰 쪽, 그다음 region_id). `merged_from`은 대표를 포함한 구성원 region_id 목록이고 병합이 없으면 자기 하나다. frame 경계를 넘는 창은 버리지 않고 `flags: window_clipped`로 표시한다(대표가 아니라 구성원만 넘으면 `merged_member_window_clipped`).
- export: 대표 region만 `map_region_ids`로 기존 경로(numeric 또는 실자료 경로)에 넘겨 `reference_export/review_rois.json`, `target_export/review_rois.json`을 만든다. 이 export는 질의 전용이며 반대 방향의 index로 쓰지 않는다(index는 아래 `*_index_export`). 방향별 파일은 `candidate_queries_v1.json`의 `directions[].query_export/index_export`에 있다. real인데 전극 증거가 없으면 export 없이 질의가 `electrode_polarity_unknown`, `source_layout_assets_unconfirmed`로 blocked된다.
- **index export**: 질의 export는 선택한 영역의 case 전극 layout만 참조하므로 DL2 `role_layer_unions`(ready ROI 행의 layout 채널 합집합)가 부분적이다. 그래서 제품마다 `reference_index_export/`, `target_index_export/`를 추가로 만든다. 데이터셋 case마다 명시 manual ROI 하나(identity·endpoints·failure_type은 데이터셋 그대로, `center_xy_um` = frame 중심이 valid면 그것, 아니면 row-major 첫 valid 셀 중심, valid 셀이 없으면 그 case는 `index_frame_has_no_valid_cell`로 blocked)를 같은 경로(numeric 또는 실자료 경로)로 내보낸다. **index 전용이다: 이 행들은 질의가 아니고 region provenance가 없다**(`source_kind: manual`, `position_policy: explicit_manual_point`). 매칭 입력은 `execution.json`/`candidate_queries_v1.json`의 `match_inputs`(폴더 이름; confirm-pairs가 읽는다)와 `match_files`(DL2에 넘길 **`review_rois.json` 파일 경로**, 출력 폴더 기준 상대): `reference_to_target = {query: reference_export/review_rois.json, index: target_index_export/review_rois.json}`, `target_to_reference = {query: target_export/review_rois.json, index: reference_index_export/review_rois.json}`.
- **전극 role 전제(real_hotspot_v1)**: DL2는 ready 행의 모든 endpoint에 비어 있지 않은 `role`과 `actual_layer`를 요구한다. 역할은 추측하지 않으므로 `condition_assignment.electrode_roles`(전극 id → role)를 명시해야 한다. 전극 이름을 그대로 role로 쓰려면 `electrode_roles` 대신 `electrode_role_policy: electrode_id`를 적는다. 이때 pair 표(`pair_table_path`, 필수)에 나오는 모든 전극이 자기 이름을 role로 받고(case에 안 나온 전극도 포함), pair 표에 없는 전극은 role이 없다. `electrode_roles`와 함께 쓰면 `electrode_roles_conflict_with_role_policy`다. 쓰인 role 목록은 `candidates` 출력의 `dl2_model_roles`에 있다. 없으면 role이 null인 행은 매칭 불가로 취급해 질의·index 행이 `electrode_role_required_for_matching`으로 blocked, index coverage는 그 전극을 덮지 못한 것으로 세고 종료 코드 3이다(`warnings`에 같은 코드와 안내). 역할을 추가한 뒤에는 import-electrode-assets를 다시 실행해 증거(sha256)를 새로 만들고 config에 반영한다. numeric_v2(합성)는 영향이 없다.
- **`index_union_partial`**: 질의 export를 index로 쓰면 안 된다는 정보 메모(`notes`, `severity: info`)일 뿐이며 종료 코드에 영향이 없다.
- **index coverage 깔때기**: 제품별 `funnel.index_coverage`(= `funnel.json`, funnel.csv의 `index_<product>` 행): `dataset_cases`, `index_rois_ready`, `index_rois_blocked`와 사유(`blocked_reasons`), `electrodes_covered/electrodes_total`(전체 및 `by_role_layer`의 `role|layer`별). 하나라도 덮이지 않았거나 blocked ROI가 있으면 `index_coverage_incomplete` 경고와 종료 코드 3이다.
- 출력: `candidate_queries_v1.json`(schema_version, `inputs`의 config·manifest·asset_manifest·import record·export sha256, `args`, 방향별 `queries`[query_id, product_id, frame_id, center_xy_um, region_id, merged_from, max_count, area_um2(영역 면적, CA 아님), layers, flags, status, reasons, roi_ids], `funnel`, `warnings`, `notes`, `match_inputs`), `funnel.json`/`funnel.csv`, `reference_export/`, `target_export/`, `reference_index_export/`, `target_index_export/`, `execution.json`. 같은 입력·인수는 같은 바이트를 만든다(경로는 출력 폴더 상대).
- 깔때기: 방향별 `regions` → `merged_queries`(탈락 사유 `merged_overlap`) → `after_max_candidates`(`cut_by_max_candidates`는 `--max-candidates`로 잘린 수, 인수가 없으면 0이고 침묵 절단은 없다) → `exported_ready`(탈락은 export 사유 코드별 질의 수; 한 질의가 여러 코드를 가질 수 있어 합이 `dropped`보다 클 수 있음). 질의는 ready ROI 행이 하나라도 있으면 `ready`, 일부 행만 blocked면 `roi_rows_partially_blocked` 플래그와 사유가 남는다.
- 종료 코드: 0 = 모든 질의 ready이고 index coverage 완전(경고 없음), 3 = blocked/partial 질의, `index_coverage_incomplete`, 지도 coverage 불완전, 미해결 동명 case, side blocked, 2 = 입력·설정 오류(필수 인수 누락 포함). 증거 파일(asset manifest, import record)은 한 번만 읽어 같은 bytes를 해시·파싱하며, export 전후 해시 확인 시점에도 바뀐 채로 남아 있으면 `evidence_changed_during_read`다(아래 입력 불변 가정 참고). 실패하면 이 실행이 만든 파일과 폴더만 지우며 이미 있던 것은 지우지 않는다.
- **입력 불변 가정(한계)**: 증거 게이트는 같은 bytes를 해시·파싱하고 export는 전후로 다시 해시하지만, `export_review_rois`는 manifest를 자체적으로 다시 읽으므로 그 호출 **안에서** manifest를 바꿨다가 되돌리는 것은 감지하지 못한다. 이 도구는 명령이 실행되는 동안 다른 프로세스가 입력을 수정하지 않는다고 가정한다(단일 사용자 폐쇄망). 데이터 폴더를 복사해 그 사본에서 실행하기를 권장한다. 이를 막는다고 주장하지 않는다.
- **role 차단은 export 파일에 기록된다**: role/actual_layer가 비어 있는 endpoint가 있는 행은 `candidates`가 해시를 계산하기 전에 `{reference,target}_export`와 `*_index_export`의 `review_rois.json`에 `status: blocked`, `reasons`에 `electrode_role_required_for_matching`으로 다시 쓰이고 파일의 top-level `status`는 `partial`이 된다(DL2는 디스크의 이 파일을 읽는다). `candidate_queries_v1.json`에 `match_files`가 있으면 `confirm-pairs`는 `match_inputs`와의 일치(같은 방향, 폴더 안의 `review_rois.json`)를 검사하고 어긋나면 `candidates_match_files_inconsistent`(코드 2)다. 필드가 없는 옛 출력은 그대로 받는다.
- **export 크기**: region 행의 `provenance.coverage`는 case 식별 목록(`observed`, `missing`, `not_applicable`, `measurement_errors`) 대신 `observed_ref`(같은 목록을 가리키는 id)만 갖고, 목록은 파일 최상위 `region_coverages`에 **한 번만** 쓴다(이전에는 모든 행이 케이스 수만큼의 식별자를 되풀이해 `행 수 × 케이스 수`로 커졌다: N=300 중심 영역 한 개에서 37 MB → 1.5 MB, 영역 25개·행 5,269개에서 650 MB → 24 MB). 행은 `source_coverage_status`·`source_coverage_summary`(개수)·면적 등 스칼라 coverage를 그대로 가진다. `region_coverages`는 region 행이 있을 때만 쓰며(manual 전용 export는 바이트가 이전과 같다) `schema_version`은 `review_roi_v1` 그대로다: DL2(`psdl2.data.load_export`)는 `schema_version`과 region 행의 `provenance.coverage.source_coverage_status`만 읽으며, 실제 DL2 검증기로 새 형식과 예전 형식을 읽어 ready/blocked 행이 같음을 확인했다. `simcompare.roi_export.expand_region_coverages(payload)`는 옛 형식(식별 목록이 행마다 들어간 형태)으로 되돌리며, 파싱한 새 파일을 `indent=2`로 다시 쓰면 7bcb07a가 쓴 바이트와 같다(테스트). 이 저장소의 읽는 쪽(`confirm-pairs`, 보고서)은 두 형식을 모두 받는다. export는 임시 파일에 조각씩 쓴 뒤 완성되면 교체하고(큰 문자열을 만들지 않는다), `candidates`는 자기가 쓴 export를 다시 읽지 않으며(행은 exporter가 넘겨 준다) SHA256은 청크로 계산한다. role 차단은 쓰기 **전에** 행에 적용되어 파일은 한 번만 쓰인다. 같은 실행 안에서 이미 검증한 asset은 (절대 경로, sha256, value_semantics)로 기억해 PNG 재디코딩만 생략한다(적중해도 파일을 다시 읽어 해시를 확인하고, frame shape와 `valid` 일치는 쓸 때마다 다시 검사한다; 배열은 보관하지 않는다).

## 확정 쌍 변환 (confirm-pairs)

사용자가 **accept 로 적은 후보만** `roi_correspondence_v1`로 바꾼다. 흐름: `candidates` → DL2 `psdl2.baseline_ncc --all-ready`(방향별 한 번, 질의 = `<query>_export/review_rois.json`, index = `<opposite>_index_export/review_rois.json`) → 검토 시트(`sheet.html`) → `decisions.csv` → `confirm-pairs` → `compare-windows`.

`--query-export`와 `--index-export`는 **`review_rois.json` 파일**을 받는다(폴더를 주면 `IsADirectoryError`). 정방향(reference 질의 → target index) 예시이며 `--window-um`, `--topk`, `--max-memory-mb` 값은 예시일 뿐 기본값이 아니다. 반대 방향은 질의와 index 제품을 바꾼다(`target_export` 질의, `reference_index_export` index).

```text
python -m psdl2.baseline_ncc --all-ready --query-export candidates_k3/reference_export/review_rois.json --index-export candidates_k3/target_index_export/review_rois.json --window-um 20 --topk 4 --max-memory-mb 512 --sheet-dir ncc_r2t_sheet --out ncc_r2t.json
python -m psdl2.baseline_ncc --all-ready --query-export candidates_k3/target_export/review_rois.json --index-export candidates_k3/reference_index_export/review_rois.json --window-um 20 --topk 4 --max-memory-mb 512 --sheet-dir ncc_t2r_sheet --out ncc_t2r.json
```

같은 기계에서는 DL2가 각 JSON의 `assets_root`를 읽는다. 입력을 옮겼다면 `--query-assets-root`, `--index-assets-root`에 각각 참조 asset이 들어 있는 **데이터 폴더**(원본/병합된 `data_dir`; export 폴더가 아니다)를 준다. 전극 role 전제는 위 "전극 role 전제"를 따른다.

```bat
.venv-cmd\Scripts\python.exe -m simcompare confirm-pairs --list --config config.yaml --candidates candidates_k3 --match reference_to_target=ncc_r2t.json --match target_to_reference=ncc_t2r.json
.venv-cmd\Scripts\python.exe -m simcompare confirm-pairs --config config.yaml --candidates candidates_k3 --match reference_to_target=ncc_r2t.json --match target_to_reference=ncc_t2r.json --decisions decisions.csv --labelled-by jdoe --mutual-tolerance-um 5 --out confirmed
.venv-cmd\Scripts\python.exe -m simcompare compare-windows --config config.yaml --correspondence confirmed\roi_correspondence.json --window-sizes 10 20 30 --out local
```

- `candidate_id` = `<direction>:<query_roi_id>:r<rank>` (`direction` = `reference_to_target` 또는 `target_to_reference`, `rank`는 시트의 "순위 N"). `--list`가 목록(점수 없음)을 출력한다.
- `decisions.csv`: UTF-8, 헤더 `candidate_id,decision,note` 정확히 이 세 열(다른 열은 거부), `decision` = `accept` 또는 `reject`. 적지 않은 후보는 **미결정**이며 어떤 출력의 쌍에도 들어가지 않는다. 같은 id 중복, accept·reject 충돌, 모르는 id, 결과가 `unavailable`인 질의의 id, match 파일을 주지 않은 방향의 id는 이름 있는 오류(코드 2)이고 문제를 모두 나열한 뒤 아무것도 쓰지 않는다.
- `--labelled-by`, `--mutual-tolerance-um`은 **필수이며 기본값이 없다**. `--match`는 방향마다 선택이지만 하나 이상 필요하다. 출력 폴더는 새 경로여야 한다.
- match 파일 검사(전부 코드 2): `schema_version == psdl2_baseline_ncc_v1`, `mode == all_ready`, `self_check`가 있으면 `passed: true`, `inputs.query_export_sha256`/`index_export_sha256`가 candidates 폴더의 해당 방향 질의 export·index export와 같음, `candidate_product_id`가 반대 제품과 같음, 모든 질의가 export의 ready ROI. 후보의 제품·design·frame이 config 데이터셋과 맞지 않으면 파일 단위 거부(코드 2). 후보 중심이 frame 밖, 반대 데이터셋에 analysis layer 없음, 두 frame의 pixel pitch 다름은 **그 쌍만** `status=blocked`(사유 코드)로 파일에 들어가 `compare-windows`가 보고한다. 같은 두 중심의 중복은 경고만 한다.
- **DL2 배치 봉투 `psdl2_search_batch_v1`(T10).** `--match`의 파일은 `schema_version`으로 갈린다. `psdl2_baseline_ncc_v1`은 위 설명 그대로이고, `psdl2_search_batch_v1`(`python -m psdl2.search --all-ready`가 쓰는 파일. 방향마다 따로 줄 수도 있고, DL2 가 쓴 `search_batch.json` 하나를 두 `--match` 에 같이 줄 수도 있다)은 `rankings[i]`의 질의 `query_roi_id`와 후보 `rank`, `roi_id`, `product_id`, `frame_id`, `center_xy_um`, `search_scope`를 읽는다. `candidate_id`는 같은 형태이고 `rank`는 봉투의 순위다(primary 뒤에 supplementary를 이어 센다). 점수는 봉투에 없어 `null`이다. 두 형식을 방향마다 섞어 써도 된다.
  - 해시(전부 코드 2, 아무것도 쓰지 않음): `rankings[i].query_export_sha256`(없으면 `inputs.query_export_sha256`)이 candidates 폴더의 그 방향 질의 export와 다르면 `match_input_hash_mismatch`, 둘 다 없으면 `match_input_hash_missing`. index 쪽은 `product_ids` 로 검사하고 해시는 **등급**으로만 남긴다. 후보 제품을 `product_ids`에 담은 `index_sources[]` source 가 있으면 후보가 쓸 수 있다. 그 source 의 `export_sha256`이 candidates 의 그 제품 `*_index_export` 해시와 같으면 `index_export_verification=candidates_index_export`, 다르면 `product_ids_only`(DL2 문서 흐름: DB 가 template export 라 해시가 다르다), `index_sources`가 `"single_export"`이면 `unverifiable_single_export`(봉투에 export 해시가 없다), 후보 제품을 담은 source 가 없으면 `no_source_for_index_product`(그 방향 후보가 모두 `candidate_product_unverifiable`). 전제: template export 는 confirm-pairs 와 같은 config 의 같은 frame 에서 만든다. 일치하는 source 가 없다는 이유만으로 종료 2 를 내지 않는다(모양 오류는 `match_index_sources_invalid`). 등급은 `--list`의 후보별 `index_export_verification`과 방향별 `batch_directions`에 보인다.
  - 한 봉투 두 방향: DL2 실행기는 질의 dataset 하나로 봉투 하나를 쓴다. 같은 파일을 `--match reference_to_target=F --match target_to_reference=F`로 줄 수 있다. 각 방향은 자기 질의 export 해시의 ranking 만 쓰고, 반대 방향 해시의 ranking 은 건너뛰며 방향별 `rankings_skipped_other_direction`에 센다. 어느 방향 해시와도 다른 ranking 은 `match_input_hash_mismatch`(종료 2). 한 방향의 ranking 이 0개이면 그 방향 `warnings=["no_rankings_for_direction"]`이다.
  - 후보별 사용 불가(`--list`의 `unavailable_candidates`에 사유와 함께 보이고, accept 하면 `candidate_match_unavailable`로 종료 코드 2): `candidate_product_unverifiable`(후보 제품을 담은 source 가 없거나 그 `product_ids`가 `null`), `candidate_product_not_configured`(config의 reference/target에 없는 제품), `candidate_same_product`(질의와 같은 제품), `unsupported_search_scope`(`primary`/`supplementary` 외). `search_scope=supplementary` 후보는 목록에 보이며 `search_scope`와 `flags.supplementary`가 표시된다.
  - 출력은 NCC 경로와 같은 쌍 단위 `roi_correspondence.json`(a=reference, `producer.kind=manual`)이다. 사이드카의 `inputs.matches.<direction>`에 `match_schema`, `index_sources_kind`(`sources`|`single_export`), `index_sources_used`(후보 제품을 담은 source 의 `export_sha256`·`product_ids`), `index_export_verification`, `rankings_skipped_other_direction`, `warnings`, `checkpoint_fingerprint`, `view_fingerprint`, `arguments`가, 쌍의 `candidates[]`에 `match_schema`, `rank`, `search_scope`, `candidate_roi_id`가 더해진다.
- **`--models <models.yaml>`(선택, 다제품)**: `compare_models_v1` 파일(`--config`와 같은 폴더, 아니면 `models_config_dir_mismatch`)을 주면 reference↔target 출력은 그대로 두고 모델 후보를 `models/<product_id>/roi_correspondence.json`, `confirmed_pairs_sidecar.json`, `deferred.json`(자산 대기 모델에 accept 가 있을 때만), `models/decisions.json`, `models/index.json`으로 따로 낸다. `--list`에는 모델 후보와 `model_status`가 같이 나온다. 모델 쌍은 target 질의(`target_to_reference`) 후보에서만 생기며 reference 질의의 모델 후보를 accept 하면 `candidate_model_query_not_target`(종료 2)이다. 모델 쌍은 a = target 질의, b = 모델이다.
- 방향: `target_to_reference` 후보는 **뒤집어서** 항상 a = reference, b = target 이다. 질의 쪽 side는 질의 export 행(endpoints·failure_type·roi_id 포함)에서 그대로 가져오고, 후보 쪽 side는 후보 중심·frame·반대 index export의 design_version·질의의 `analysis_layer`만 가진다(endpoints·roi_id·failure_type 없음).
- `source = {kind: manual, labelled_by, labelled_at}`(UTC, 한 실행에서 한 번). `pair_id`는 방향·질의 roi_id·rank·후보 중심의 해시이며 `labelled_at`에는 의존하지 않는다.
- `mutual`/`one_way`: 반대 방향에서 accept 된 쌍의 a·b 중심이 모두 `--mutual-tolerance-um` 이내면 `mutual`, 아니면 `one_way`(표시만 한다). 두 방향이 같은 대응을 accept 했으면 쌍은 **하나**만 내보내고(pair_id가 작은 쪽) 사이드카에 `merged_with`를 적는다. 반대 방향의 reject·미결정 후보는 `opposite_candidates_within_tolerance`에 정보로만 남는다. 병합은 두 쌍의 분석층(a·b 양쪽)이 같고, failure_type이 양쪽에 있으면 같을 때만 한다. 맥락이 다르면 `mutual` 표시는 두되 **두 쌍 모두 내보내고** 사이드카에 `mutual_context_differs: true`를 적는다(accept 한 측정은 버리지 않는다).
- 사슬 검사(실패하면 종료 코드 2, 아무것도 쓰지 않음): 현재 두 데이터셋의 manifest sha256·`dataset_input_hash`가 `candidates` 때와 다르면 `confirm_source_changed_since_candidates`, 디스크의 export JSON 바이트가 `candidates`가 기록한 `export_sha256`/`index_export_sha256`과 다르면 `confirm_export_changed_since_candidates`(DL2가 기록한 해시와의 비교는 그대로), export 행의 frame 서술(frame_id·origin·pitch·shape·axis_order·origin_convention)이 현재 데이터셋 frame과 다르면 `confirm_frame_changed_since_candidates`, 한 export에 같은 roi_id가 둘 이상이면 `duplicate_roi_id_in_export`.
- **큰 export 사전 게이트**: real 데이터셋에서는 candidates export 네 개(`*_export`, `*_index_export`의 `review_rois.json`)를 읽기 **전에** 파일 크기를 확인한다. 파싱한 객체는 파일 바이트의 약 1.9~2.5배(Python 3.8/3.11 실측; 텍스트 포함 순간 최대 약 3.5배)를 차지하므로 `합계 × 5 + 가장 큰 파일 × 3`(합계에는 `--match` DL2 JSON 파일의 디스크 크기도 들어간다)이 `max_operation_bytes`(두 데이터셋 중 작은 값)를 넘으면 아무것도 읽지 않고 `oversized_real_operation: confirm-pairs (stage-5 export read: B bytes in F files x 5 parsed): estimated X bytes exceed limit Y; rerun candidates with --max-candidates ...`(코드 2)로 멈춘다. 파싱 뒤에는 읽는 쪽이 쓰지 않는 `region_coverages`를 버린다. numeric 데이터셋에는 한도가 없어 게이트가 없다.
- 출력: `roi_correspondence.json`(`validate_correspondence` 통과, `load_correspondence`로 재적재), `confirmed_pairs_sidecar.json`(pair_id별 candidate_id·점수·순위·flags·score_gap·template_sha256·implementation_revision·결정 메모·mutual/one_way·입력 파일 sha256·blocked 사유), `confirm_funnel.json`/`.csv`(match 후보 → accept/reject/미결정 → blocked(사유) → 병합 → 출력), `execution.json`. **점수는 대응 파일에 없고 어떤 판정·정렬에도 쓰이지 않는다.**
- 종료 코드: 0 = 쌍이 나갔고 blocked 없음, 3 = blocked 쌍이 있거나 accept 0건(출력은 씀), 2 = 입력·정합성 오류(출력 없음). 쓰는 도중 실패하면 이 실행이 만든 파일과 폴더만 지운다.

## 모델별 리포트 (model-report)

행 = target 질의 ROI, 열 = 모델(reference 포함)인 한 장 표다. 수치는 기존 출력에서 복사하며 새로 계산하지 않는다. 모든 인수가 필수이고 기본값이 없다. 입력 폴더의 세대·제품 방향·미완료 여부가 어긋나면 아무것도 쓰지 않고 종료 2다. 필드와 검증 목록은 [데이터 계약](data_contract.md)의 `model_report_v1`.

| 인수 | 값 | 설명 |
|---|---|---|
| `--candidates` | candidates-change 출력 폴더 | ROI 행과 변경 속성(점수·case 수·union 변화) |
| `--confirm` | `confirm-pairs --models` 출력 폴더 | 확정 쌍(reference, 모델별)과 `models/decisions.json` |
| `--windows` | `<product_id>=<compare-windows 폴더>`, 제품마다 반복 | reference 쌍 폴더는 reference product_id 키(6회차를 DL2 경로 대응 파일로 실행한 폴더), 모델은 모델 키(6m회차 폴더) |
| `--diameter-um` | µm | 표시 지름. `local_delta_rows.csv`에 있어야 한다 |
| `--window-um` | µm | 표시 창. `window_sizes_um` 안, 모델 `window_cases`가 있으면 그 창과 같아야 한다 |
| `--out` | 새 폴더 | 입력 폴더 밖이어야 한다(`output_dir_exists`/`output_is_input`) |

```text
python -m simcompare model-report --candidates data/changes --confirm data/confirmed --windows QA4_R00=data/local --windows QA4_C00=data/local/models/QA4_C00 --diameter-um 5 --window-um 20 --out data/model_report
```

부호: 원값 `delta_um2`는 b − a(모델 쌍 = 모델 − target, reference 쌍 = target − reference)이고 `delta_model_minus_target_um2`가 reference 쌍만 뒤집어 모델 − target으로 통일한다. case 구성 차이는 인과를 뜻하지 않는다. 출력: `model_report.html/.csv/.json`, `execution.json`.

## 종료 코드

| 코드 | 의미 | 확인 |
|---|---|---|
| 0 | 요청 범위의 결과 available 또는 생성 완료 | execution.json과 결과 provenance |
| 3 | partial/unavailable/blocked 항목을 포함한 사용 가능한 부분 출력 | status, reasons, coverage, partial_value |
| 2 | 입력/설정 불가 또는 지도 incomplete/unavailable | 오류 메시지, error.json; 지도 리포트 없음 |

`compare-windows`는 모든 ready 쌍의 창 Δ가 available(또는 근거 있는 not_applicable)이면 0, 창 clipping·valid 구멍·원천 누락·blocked 쌍이 있으면 3, 대응 파일·창 크기·제품 불일치 오류는 2다.

입력 오류 시 error.json/execution.json을 쓴다. 이전 결과를 어떻게 다루는지는 명령마다 다르다: `compare-windows`는 어떤 오류에서도 이전 세대를 지우지 않는다. 실자료(`mode: real`) `compare`도 모든 오류(manifest 검증·`mixed_real_and_numeric_source_formats`·옮겨진 import 폴더의 `missing_or_escaping_path`·데이터셋·pair 표·admission 오류 포함)에서 `comparison.json`, `comparison.csv`, `comparison_masks.zip`을 지우지 않는다. 단 비트랜잭션 실자료 출력(`condition_assignment`가 없는 점검 출력, `mode: real`의 numeric_v2 자료)의 쓰기 오류는 예전처럼 `comparison*` 리포트를 제거한다(`keeps_previous_outputs`를 세우지 않는다; 일부만 쓰인 새 파일이 옛 `comparison_masks.zip` 옆에 남는 것을 막는다). 설정 파일 자체를 읽지 못하는 경우와 합성(`mode: synthetic`) `compare`, 그 밖의 명령(electrode_map/validation/review_rois 등)은 예전처럼 관리 대상 리포트(JSON·CSV·HTML·PNG·NPZ)를 제거하고 error.json/execution.json만 남긴다. 이 정리는 `comparison_masks.zip`도 함께 지우며, 공개 제어 파일(`.comparison.publishing|committed`, `.local_delta.publishing|committed`)이 있으면 관리 파일을 지우지 않는다. 성공 또는 허용된 부분 비교 실행 후에는 stale error.json을 제거한다. electrode-map은 incomplete/unavailable이면 exit 2이고 리포트 세트를 쓰지 않는다. 기본 지도·ROI 출처 선택과 가중값 미확정 여부는 서로 독립이다.

## 결과 읽기

`comparison.json` 및 CSV/HTML은 line / pixel, pair exclusive/shared, transfer, 원 case CA, 지름/coverage, provenance를 기록한다. 세 failure pieces는 line / pixel과 다른 축이다. shared bucket이 pair별 CSV에 반복되더라도 총계에는 한 번만 포함된다. partial/unavailable의 value는 null이며 부분 합은 partial_value다. 관측 0은 available 0이고 누락을 대신하지 않는다.

`electrode_map.json`은 한 제품의 layer별 count와 product maximum의 작은 summary다. counts와 valid는 압축 NPZ 상대 경로로 연결하고 shape/pitch/origin, count histogram, status/provenance와 파일 hash를 기록한다. 기본 셀별 CSV·JSON 배열·HTML 배열은 없다. `--per-cell-text`는 4096 cells 이하에서만 명시 셀별 텍스트를 추가한다. PNG는 정수 count마다 다른 색과 legend를 쓰고 sidecar는 원 frame, um extent, integer display scale을 기록한다. hotspot frame(2×2 unit 원본 영상에서 사방 500 px를 잘라낸 중앙 영역)을 그대로 표시한다. 기본 regions=[]이며 명시 --min-count 또는 map.min_count에서만 영역을 생성한다. 다른 제품의 지도는 별도 실행한다.

`review_rois.json`의 기본 출처는 review_rois.manual이다. product/design/source/step/exposure/case/frame/version과 원 shape 문맥을 모두 지정해야 ready다. 문맥이 부족하면 blocked 이유를 보존한다. 지도 영역을 쓰려면 threshold와 review_rois.map_region_ids를 명시한다. crop 크기는 정하지 않으며 DL2가 선택한 위치와 유사 구조를 찾는 별도 역할을 담당한다. [ROI 교환 계약](review_roi_contract.md)을 따른다.

## Linux 서버 단일 파일 실행 (`bundles/simcompare_job.py`)

서버에 `.py` 파일만 올릴 수 있고 명령줄 인자를 넘길 수 없을 때 쓴다. `scripts/build_single_file.py`가 `src/simcompare` 전체와 순수 Python PyYAML을 `.py` 하나로 묶은 것이며, 서버에는 numpy와 Pillow만 있으면 된다(pandas·pyarrow·zarr는 legacy 경로 전용이라 필요 없다). 서버 Python은 3.8 이상이면 된다(3.8·3.9에서 테스트, 개발 기준은 3.11). 3.8의 오래된 Pillow에서는 보고서 PNG 바이트가 golden과 달라질 수 있다.

```bash
uv run python scripts/build_single_file.py --out bundles/simcompare_job.py --overwrite   # 소스를 고친 뒤 다시 만든다
```

- 상대 경로: 실행기는 시작할 때 작업 폴더를 `BASE_DIR`(없으면 실행기 `.py`가 있는 폴더)로 바꾸므로 `JOBS`·`LOG_FILE`의 상대 경로는 그 폴더 기준이다. wrapper가 `.py`를 다른 곳에 복사해 실행하면 기준이 바뀌므로 상대 경로를 쓸 때는 `BASE_DIR`에 절대 경로를 적는다. 로그 첫 줄 `relative paths resolve from ...`에 실제 기준 폴더가 나온다. config 파일 **안**의 상대 경로는 이와 별개로 config 파일 위치 기준이고, `pair_table_path`·`asset_manifest.path` 등은 `data_dir` 기준이다.
- 파일 맨 위 `EDIT BELOW` ~ `EDIT ABOVE` 블록만 고친다. `JOBS`는 `(명령, [인자...])` 목록이며 `python -m simcompare <명령> <인자...>`와 같다. 종료 코드 0·3이면 다음 작업으로, 그 밖이면 멈춘다. `LOG_FILE`에 진행 줄과 출력이 남는다.
- 실행하면 `.simcompare_pkg_<해시>/`에 패키지를 바이트 그대로 풀고 그 복사본을 먼저 import한다(서버에 다른 simcompare·yaml이 있으면 오류로 알린다).
- 실자료 `compare`(2회차)는 스트리밍이다: 케이스의 마스크는 접근할 때만 읽고(캐시 없음) 한 layer×지름 그룹에서 한 번에 한 장만 메모리에 둔다. 결과는 비트 패킹으로 보관한다. `condition_assignment`의 한도: `max_group_mask_cells`(한 layer×지름 그룹의 cells, 기본 5,000,000,000), `max_materialized_source_cells`(가장 큰 layer 그룹의 cells = 케이스 수 × 프레임 cells, 기본 6,000,000,000), `max_operation_bytes`(추정 최대 메모리, 기본 8 GiB, 물리 메모리 이하), `max_bucket_output_entries`(한 그룹이 실제로 내보내는 bucket pair id + lineage + contributor 항목 상한, 기본 1,000,000, 넘으면 `oversized_real_bucket_output`), `max_label_pattern_entries`(정확한 겹침 분할이 한 그룹에서 보관하는 패턴 항목 = 서로 다른 패턴에 든 pair 수의 합 상한, 기본 24,000,000, 넘으면 `oversized_real_label_patterns: <layer> d=<지름>: <개수> live label-pattern entries; limit <한도>; ...`).
- 메모리 추정(`provenance.resource_estimate`) = 결과 보관 `layers × 32 × 7 × cells/8`(비트 패킹 마스크) + **모든 layer×지름 그룹이 끝까지 보관하는 bucket 출력** `layers × 32 × 그룹당 항목 수 × 100` B + 그룹 임시 `최대 layer 케이스 수 × 2 × cells/8 + 64 × cells` + label 패턴 예산 `max_label_pattern_entries × 256` B(실측 항목당 약 190~220 B) + 한 그룹 직렬화 `그룹당 항목 수 × 200` B + 레코드당 3 KiB. 그룹당 항목 수는 전체 모드(`full_bucket_detail: true`)에서 `max_bucket_output_entries`, 요약 모드에서 `min(max_bucket_output_entries, 2 × top_buckets × 3 × layer 케이스 수)`이고 둘 다 그룹이 구조적으로 만들 수 있는 최대(서로 다른 패턴 수 ≤ `min(cells, 2^케이스 수)`)를 넘지 않는다. 같은 구조적 상한이 label 예산에도 적용돼 아주 작은 입력은 기본 예산 전체를 물지 않는다. 1,682 케이스·한 layer·1262² 프레임이면 **요약 모드(기본, top 20)는 약 7.8 GB(7.3 GiB)로 기본 8 GiB 안**이고(결과 보관 0.69 GB, 그중 bucket 출력 0.65 GB, 임시 약 7.0 GB 대부분이 label 예산 6.1 GB), **전체 모드(`max_bucket_output_entries` 1,000,000)는 약 10.5 GB(9.8 GiB)라 기본 8 GiB를 넘는다**(bucket 출력 3.2 GB를 32그룹이 모두 보관하는 것으로 계산). `validate-data`는 `compare`와 같은 설정(`compare.full_bucket_detail`/`top_buckets`)으로 허가하고, bucket 출력을 보관하지 않는 4·6회차 로더는 요약 모드 기준으로 허가한다. 전체 모드는 `max_operation_bytes`를 물리 메모리 이하로 올리거나 `max_bucket_output_entries`를 낮춰야 한다. 예전 eager 추정은 85.7e9 cells로 항상 초과였다. 추정이 넘치면 `oversized_real_operation: estimated N bytes exceed ...`에 추정치 N이 나온다. 보수적 추정이며 실측이 아니다.
- 4회차(`electrode-map`, `candidates`)는 스트리밍이다. 케이스의 대표 지름(기본 5 µm) 마스크는 **한 장씩만** 읽어 비트 패킹(`cells/8`)으로 보관하고 전체 마스크는 바로 버린다. 나머지 지름의 레코드는 헤더(상태·사유)만 읽는다(비대표 마스크는 4회차 지도가 읽지 않는다; manifest 인증은 그대로 모든 마스크를 한 번 읽는다). 전극별 count는 전극마다 그 전극 케이스들의 active를 OR한 뒤 더한다(공유·반복 endpoint는 한 번만 센다). 영역(region) 마스크도 패킹해 보관하고(`ElectrodeRegion.mask`는 읽을 때 풀어 주는 같은 이름의 필드다), case 연결(`CaseLink`)의 raw/metadata와 영역 coverage·provenance는 영역·연결마다 복사하지 않고 한 벌을 공유한다. 계산 전 admission은 가장 큰 layer의 `케이스 × (cells/8 + 2 KiB)` + counts(`8 × cells`) + 첫 측정 임시(셀당 약 32 B)를 `max_operation_bytes`와 비교하고, `candidates`는 두 번째 제품에 첫 제품이 쥐고 있는 행·영역 바이트와, 첫 제품의 설정 데이터셋이 아직 살아 있는 몫(case·지름 레코드당 5 KiB + 2 MiB)을 더해 비교한다(둘째 제품만 약간 더 엄격하다). 영역(region) 선택(`min_count`)을 쓰면 counts가 나온 뒤 연결 성분을 **셀마다 파이썬 객체를 만들지 않는** 행 단위(run-length) union-find로 센다(메모리는 가로 run마다 약 48 B까지). run 수는 행 스캔만으로 먼저 알 수 있으므로 라벨 배열을 만들기 **전에** 이 센 단계를 비교하고(`stage-4 component count: N runs of C cells`), 센 뒤에는 **영역 마스크를 만들기 전에** `영역 수 × (cells/8 + 16 KiB)` + run 라벨 + 가장 큰 영역의 id 임시치(셀당 200 B) + 영역 하나의 측정 작업 집합(셀당 40 B)을 다시 비교하고(`stage-4 regions: N regions of C cells`), **영역마다 연결 case를 알게 된 뒤 윈도·CaseLink·측정을 만들기 전에** 지금까지의 링크 수 + 이 영역의 링크 수 × 3 KiB를 더해 비교한다(`stage-4 links: L links in N regions of C cells`; 한 영역이 모든 케이스를 잇는 중심 영역이면 링크 수는 케이스 수다). export 행(행당 12 KiB)은 행을 만들기 전에 `stage-4 export: R rows`로 비교한다. 넘으면 `oversized_real_operation: <단계>: estimated X bytes exceed limit Y; split cases, select fewer regions (--max-candidates, fewer map regions) or raise max_operation_bytes up to physical RAM`으로 멈춘다. 6회차(`compare-windows`, 보고서)의 계산 쪽도 제한된 캐시로 바뀌었다(위 "실자료 규모"): 대응 쌍을 묶은 뒤 `(ready 쌍 수, blocked 쌍 수, 창 크기 수)`를 넣어 단계별 최대 메모리(두 모델 결과 또는 artefact, 행, 보고서)를 `max_operation_bytes`와 비교하고, 넘으면 `oversized_real_operation: <단계>: estimated X bytes exceed limit Y (R ready and B blocked pairs x S window sizes x 32 diameters); fewer pairs or window sizes ...`로 계산 전에 멈춘다. 숫자 파일·HTML을 쓰는 쪽도 스트리밍으로 바뀌었다(위 "실자료 규모" 다음의 "출력 쓰기와 공개").
- `max_operation_bytes`의 물리 메모리 상한은 호스트 RAM(`sysconf`)이며 cgroup/job 메모리 한도는 감지하지 못한다. 컨테이너·스케줄러 한도가 더 작으면 그 한도 이하로 직접 낮춘다.
- 기존 1회차 import(`.npz`와 옛 `.npy`)는 그대로 읽힌다.

## ETX(Linux GPU 서버) 실행 순서

ETX는 DL1을 돌리는 Linux 서버다. simcompare 계산은 NumPy/Pillow CPU 계산이라 GPU를 쓰지 않으며, 큰 RAM 때문에 이 서버를 쓴다. 패키지를 설치하지 않고 위의 `bundles/simcompare_job.py` 하나만 올려 실행한다(서버에 numpy·Pillow 필요, DL1 환경에 있음). 실행기는 명령만 실행하고 파일 복사·config 수정은 하지 않으므로, 아래처럼 회차를 나눠 각 회차 사이에 사람이 준비 작업을 한다. 경로는 실행기 `BASE_DIR`(작업 폴더) 기준 상대 경로 예시다. `/data`처럼 `/`로 시작하면 루트 아래 절대 경로가 되어 권한 오류가 날 수 있으니 `data/...`로 쓴다. 실제 데이터 전체 흐름은 ETX에서 아직 검증되지 않았다.

회차별 실행 파일은 `bundles/etx/simcompare_job_<회차>_*.py`, 회차별 설정 예시는 `configs/etx/`(`real.stage2.example.yaml`, `real.stage4.example.yaml`, `assignment_<side>.example.json`)에 있다. 대응표는 [`configs/etx/README.md`](../configs/etx/README.md). 소스를 고치면 `uv run python scripts/build_etx_stage_runners.py --overwrite`로 다시 만든다.

준비: hotspot 입력 폴더(모델 폴더 `QA4_R00`, `QA4_T00` 아래 `<case>/images/...`, `<case>/result/<불량명>.csv`)와 DL1 PNG 폴더를 서버에 둔다. `git pull` 후 `bundles/simcompare_job.py`를 복사해 `JOBS`, `LOG_FILE`을 고친다. 회차마다 `JOBS`만 바꿔 다시 실행한다. 출력 폴더(`--out`)는 매번 새 경로여야 한다.

1회차 — hotspot import (마스크를 한 장씩 처리하므로 메모리 문제 없음):

```python
JOBS = [
    ("import-hotspots", ["--root", "data/input/hotspot/QA4_R00", "--out", "data/imported/reference"]),
    ("import-hotspots", ["--root", "data/input/hotspot/QA4_T00", "--out", "data/imported/target"]),
]
```

1회차 `import-hotspots`에는 4회차 ROI를 `ready`로 만들기 위해 `--expected-cases`(case 이름을 줄마다 적은 txt, 아래 `import-hotspots` 설명)를 주어야 한다: 기대 case 목록이 없으면 coverage가 `unconfirmed`라 `source_coverage_status`가 `partial`이 되고 4회차의 **모든 ROI가 `blocked`(`map_coverage_incomplete`)**이며 DL2 쪽 질의가 0개다(T5 py3.8 파이프라인에서 확인; `docs/plan/scale_stage46/T5_benchmark.md`). 목록 없이 이미 import했다면 1회차부터 다시 한다. 다시 실행하려면 먼저 이전 출력 폴더를 옮기거나 이름을 바꾼다(`--out`은 없는 경로여야 하며 3·4·5회차도 기존 출력 폴더를 거부한다): `data/imported/reference`, `data/imported/target`, `data/assets/reference`, `data/assets/target`, `data/changes`, `data/confirmed`(`data/local`은 6회차가 한 세대로 교체하므로 그대로 두어도 된다). 그다음 1회차부터 순서대로 다시 하고, 회차 사이 수작업도 다시 한다: 1회차 뒤 `pairs.csv`를 새 `data/imported/reference`·`target` 안에 다시 복사하고, 3회차 뒤 3회차 안내대로 import 결과를 `data_dir`에 다시 두고 `real.yaml`의 `electrode_import_record.sha256`을 새 파일 값으로 고친다. 예시 실행기 JOBS는 목록 파일 대신 `--expected-cases-from-roots <다른 모델 root>`를 쓴다(기대 case = 두 모델 root에서 발견된 case 이름의 합집합; 두 모델 모두에 없는 case는 탐지할 수 없다).

1회차 뒤: `pairs.csv`를 `data/imported/reference/`와 `data/imported/target/` 안에 복사한다. `pair_table_path`는 `data_dir` 기준 상대 경로(`pairs.csv`)만 받는다(절대 경로·`..`·백슬래시 거부). `real.yaml`의 `data_dir`를 서버 절대 경로로 바꾸고, reference·target `condition_assignment`에 큰 자료용 한도를 넣는다:

```yaml
    max_materialized_source_cells: 20000000000
    max_group_mask_cells: 5000000000
    # max_operation_bytes: 기본(8 GiB)이면 compare에 충분하다. 넘는다는 오류가 나오면 추정치에 여유를 더해 넣는다
```

2회차 — 검사·비교:

```python
JOBS = [
    ("validate-data", ["--config", "real.yaml"]),
    ("compare", ["--config", "real.yaml"]),
]
```

`compare`는 스트리밍이라 `max_operation_bytes` 기본 8 GiB로 충분한 것이 정상이다. 기본값을 넘으면 `oversized_real_operation: estimated N bytes exceed ...`로 멈추며 N이 추정치다. N에 여유를 더한 값(물리 메모리 이하)을 넣고 다시 실행한다. 종료 코드 3은 partial 항목이 있다는 뜻이며 `reasons`를 확인한다.

**스케일 개선 후 재시작(2회차부터)**: 1회차 import 결과(`.npz`·옛 `.npy`)는 그대로 읽히므로 1회차는 다시 하지 않는다. 6개 실행기(`bundles/etx/simcompare_job_1~6_*.py`)를 모두 새 파일로 교체하고 2회차부터 다시 돈다(`JOBS`·`BASE_DIR`은 새 파일에서 다시 고친다). `real.yaml`의 새 선택 키는 `compare: {full_bucket_detail: false, top_buckets: 20}`(기본 요약, 위 "요약 모드")과 `condition_assignment.max_bucket_output_entries`(기본 1,000,000)와 `max_label_pattern_entries`(기본 24,000,000)다. 모든 bucket 목록(`full_bucket_detail: true`)은 GB 단위 `comparison.json`을 만들 수 있어 필요할 때만 켠다. 4·6회차도 계산 전 admission(위 설명)으로 `oversized_real_operation`이 나올 수 있다. 2회차를 새 실행기로 다시 돌리면 `comparison_masks.zip`이 함께 만들어져 6회차가 `compute_areas` 두 번을 건너뛴다(이미 끝난 2회차 출력은 그대로 유효하며 6회차가 재계산한다).

**4~6회차 재시작**: 6개 실행기를 다시 받아 교체한다(`JOBS`·`BASE_DIR`은 새 파일에서 다시 고친다). 1·3회차 출력과 이미 끝난 2회차 `comparison.json/csv`는 그대로 유효하다(2회차 입력 형식·해시·`ElectrodeMaps.to_dict`는 바뀌지 않았다). 새로 생기거나 달라진 것: (1) **2회차 `compare`**가 `comparison_masks.zip`(그룹별 패킹 마스크, 입력 해시로 묶임)을 `comparison.json/csv`와 한 번에(같은 공개 트랜잭션) 만든다. 6회차가 이 파일이 맞으면 두 모델의 면적 계산을 건너뛰고(결과는 재계산과 같음), 없거나 입력이 다르면 조용히 다시 계산한다. 이미 끝난 2회차에서 이 파일만 얻으려면 2회차 `compare`를 새 실행기로 다시 돌린다(기존 `comparison.*`는 새 세대로 통째로 바뀌며, 실패하면 이전 세대가 그대로 남는다: 공개 트랜잭션을 연 뒤(reference 계산이 끝나고 쓰기를 시작한 뒤)의 오류는 CLI도 `comparison.json/csv/comparison_masks.zip`을 지우지 않고 `error.json`만 쓴다. 그 전의 오류도 `mode: real`이면 이전 세대를 지우지 않는다. 설정 파일을 읽지 못하는 경우와 합성 `compare`만 예외다). (2) **4회차 `review_rois.json`**은 슬림 형식이다: 행마다 coverage 스칼라 필드와 `observed_ref`만 두고 `observed`·`missing`·`not_applicable`·`measurement_errors` 목록은 최상위 `region_coverages`에 영역마다 한 번만 쓴다(같은 `review_roi_v1`; DL2 `psdl2.data.load_export`는 읽기만 확인했고 그대로 받는다). 5회차 `confirm-pairs`는 슬림·옛 형식 모두 읽고, 읽기 전에 파일 크기×해석 배수를 `max_operation_bytes`와 견주어 크면 `json.loads` 전에 멈춘다. 이미 만든 4회차 출력은 그대로 5회차에 쓸 수 있으나 큰 자료의 메모리를 줄이려면 4회차를 다시 돌린다. 이때 `candidates`·`confirm-pairs`는 이미 있는 `--out` 폴더를 거부하므로 예전 출력 폴더(예: `data/candidates_k3`)를 옮기거나 이름을 바꾸거나, 실행기 JOBS의 `--out`을 새 경로로 바꾼다. (3) **6회차**는 출력 폴더를 `local_delta.json`, `local_delta_rows.csv` 외 두 표, `window_sensitivity.*`, `local_delta.html`, `_thumbs.json`, 썸네일·개요 PNG의 한 세대로 공개한다. 죽은 뒤에는 같은 명령을 다시 돌리면 복구한다(`.local_delta.staging.*`는 지워도 되는 임시 폴더). 예전 6회차가 남긴 썸네일 PNG는 새 세대로 바뀐다.

**실측과 예상 시간·메모리**(`docs/plan/scale_compare/T5_benchmark.md`, 한 layer, 합성 자료, macOS, 모델당 1,682 case, crop 1262²·32 지름, 모든 case가 crop 중앙 근처에 겹치는 "centered" 모델): `validate-data` 약 1~1.5분·1.1 GiB, `compare` 약 10~12분·3.3~3.7 GiB(crop 1500²에서 12분·3.7 GiB), `import-hotspots`(1회차) 모델당 4~5.5분·0.85 GiB. 한 layer 기준 `comparison.json` 약 380 MB(요약), `comparison.csv` 약 52 MB. 기본 한도 그대로 두 크기 모두 끝난다(T6: 1262²에서 compare 584 s·3.5 GiB, 1500²에서 747 s·3.7 GiB, 둘 다 exit 3). 한도를 넘는 더 큰 자료는 `oversized_real_label_patterns: <layer> d=<지름>: N live label-pattern entries; limit L`로 멈추며, `max_label_pattern_entries`(항목당 약 256 B로 추정에 반영)와 필요하면 `max_operation_bytes`(물리 메모리 이하)를 함께 올린다. 케이스는 layer로 나뉘므로 한 layer가 최악이며, 11 layer라고 시간이 11배가 되지는 않는다(실측 아님). Python 3.8(numpy 1.21.6)에서도 동작하나 compare가 약 2배 느리다(N=300: 176 s vs 82 s).

**4~6회차 실측과 예상 시간·메모리**(`docs/plan/scale_stage46/T5_benchmark.md`, 합성 자료, macOS 12코어·48 GiB, 모델당 1,682 case·한 layer·32 지름, 창 10/20/30 µm·jitter 0.5; 세 실행을 동시에 돌려 시간은 단독보다 최대 1.3~1.4배 길다): 3회차 `import-electrode-assets` 모델당 1.2~2.4분·0.57 GiB. 4회차 `candidates` 5~6.5분·1.2~1.3 GiB(crop 1262²/1500², 중앙 큰 영역 1개 또는 분리된 영역 196개). 5회차 `confirm-pairs` 3.5~4.6분·0.9~1.25 GiB(모든 마스크를 인증하므로 분 단위). 6회차 `compare-windows`: 2회차 artefact가 있으면 쌍 50개 5~5.5분·0.8~1.1 GiB, 쌍 200개 6~9.5분·0.9~1.25 GiB; artefact가 없으면 쌍 200개 14~23분·1.5 GiB(전극 59개를 공유하는 자료)~3.4 GiB(case마다 전극이 따로인 자료)(재계산이 면적 계산 한 번을 더 한다). 출력은 쌍 200개에 약 275 MB(행 수 = 쌍 × 창 크기 × 32). 2회차 `compare`는 `comparison_masks.zip` 약 50~70 MB가 더 늘고 그 비용은 시간·RAM에 거의 안 보인다. 한 번에 디스크는 1262²에서 모델·중간 산출물까지 약 3.5~3.8 GB, 1500²에서 약 4.7 GB가 필요했다. 6회차 admission 추정은 실제 RSS보다 크다(쌍 50·재계산 기준 추정 7.84 GB, 실측 1.6 GB): 기본 `max_operation_bytes`(8 GiB)에 걸리면 추정치 X에 여유를 더한 값(물리 메모리 이하)을 넣는다. 거부는 입력 인증(약 100초) 뒤 계산 전에 일어나고 아무것도 공개하지 않는다.

3회차 — 전극 PNG import (`real.yaml`의 `condition_assignment`와 같은 내용의 assignment JSON 필요, `--max-asset-memory-mb`는 기본값 없음). role은 `electrode_role_policy: electrode_id`로 자동이다(pair 표의 모든 전극 = 자기 이름):

```python
JOBS = [
    ("import-electrode-assets", ["--source-kind", "hotspot_case_electrode", "--root", "data/input/hotspot/QA4_R00",
        "--numeric-data-dir", "data/imported/reference", "--condition-assignment", "assignment_reference.json",
        "--cnt-from-dl1-root", "data/input/dl1", "--source-version", "LAYOUT_V1",
        "--max-asset-memory-mb", "<MB>", "--out", "data/assets/reference"]),
    ("import-electrode-assets", ["--source-kind", "hotspot_case_electrode", "--root", "data/input/hotspot/QA4_T00",
        "--numeric-data-dir", "data/imported/target", "--condition-assignment", "assignment_target.json",
        "--cnt-from-dl1-root", "data/input/dl1", "--source-version", "LAYOUT_V1",
        "--max-asset-memory-mb", "<MB>", "--out", "data/assets/target"]),
]
```

3회차 뒤: 각 출력의 `review_assets.json`, `electrode_asset_import_record.json`, `assets/`를 해당 `data_dir`로 복사하고 `real.yaml`의 reference·target에 `asset_manifest`, `electrode_import_record`({path, sha256})를 넣는다(위 "전극 PNG → review_assets_v1" 절).

4회차 — 변경점 ROI(`candidates-change`; 실행기 파일 이름은 그대로 `simcompare_job_4_candidates.py`):

```python
JOBS = [("candidates-change", ["--config", "real.yaml", "--cnt-from-dl1-root", "data/input/dl1",
                               "--align-search-um", "30", "--align-min-dice", "0.3", "--align-layer-tolerance-um", "1",
                               "--change-tolerance-um", "0.5", "--window-um", "20", "--merge-overlap", "0.5",
                               "--top-k", "20", "--out", "data/changes"])]
```

모든 인수는 필수이며 값은 예시다(제품에 맞게 바꾼다; 의미는 위 "ROI 선정" 절). `--cnt-from-dl1-root`는 3회차와 같은 DL1 root다. 결과: `data/changes/alignment.html`(정렬·변경 지도), `direct/roi_correspondence.json`(6회차 기본 입력), `*_export/review_rois.json`, `*_index_export/review_rois.json`(DL2 입력). `candidate_queries_v1.json`·`execution.json`의 `dl2_model_roles`가 DL2 설정 `model.roles`에 넣을 role 목록이다. 실행기는 종료 코드 3을 '계속'으로 처리하므로 로그의 `candidates-change exit N` 줄과 `reasons`를 직접 확인한다. ΔV 취약점 ROI는 4b·4c회차(`candidates-dv`, `vulnerability-report`)에서 따로 돈다. 비가중 `candidates`는 참고용이며 이 회차에서 쓰지 않는다.

DL2 — 위치 매칭(**DL2 경로 전용**: 직접 대응을 쓰면 DL2와 5회차를 건너뛰고 6회차로 간다. 다른 제품 탐색이 필요할 때만 쓴다. DL2 저장소 `particle-sim-dl2`의 `bundles/etx/psdl2_job_baseline_ncc.py`, 같은 `BASE_DIR`에서 실행): 4회차 결과로 두 방향 `baseline_ncc --all-ready`를 돌려 `data/dl2/ncc_r2t.json`, `ncc_t2r.json`과 검토 시트 `data/dl2/*_sheet/sheet.html`을 만든다. torch는 필요 없다. ETX 실행기로 만든 export에는 `producer_commit`이 없으므로(git checkout이 아님, 사유는 export에 기록) DL2 실행기는 `--allow-unversioned-producer`를 쓴다. 시트를 보고 `data/dl2/decisions.csv`(헤더 `candidate_id,decision,note`, `decision`=`accept`|`reject`)를 쓴다. `candidate_id` 목록은 5회차 첫 작업(`confirm-pairs --list`)이 로그에 찍는다.

5회차 — 확정 쌍 변환(DL2 경로 전용, `simcompare_job_5_confirm_pairs.py`; `--candidates data/changes`): `--list`로 id 목록을 찍은 뒤 `decisions.csv`의 accept 후보만 `data/confirmed/roi_correspondence.json`으로 바꾼다. `--labelled-by`의 `CHANGE_ME`를 실제 ID로 바꾼다. `decisions.csv`가 없으면 둘째 작업이 종료 코드 2로 멈춘다(목록은 이미 로그에 있음).

6회차 — 국부 ΔCA(`simcompare_job_6_compare_windows.py`): 기본 `--correspondence`는 4회차의 `data/changes/direct/roi_correspondence.json`이다(DL2 경로를 썼다면 `data/confirmed/roi_correspondence.json`으로 바꾼다). 창 10/20/30 µm, jitter 0.5, 정렬 지름 5(권장 운용값)로 `data/local/`에 `local_delta.json`·`local_delta.html`을 만든다. 창이 frame 밖으로 나가거나 valid 구멍을 덮으면 그 Δ는 null이고 종료 코드 3이다.

통합 보고서(`report`)는 아직 실자료 경로를 지원하지 않는다: 실자료 `compare`는 `comparison.json`/`comparison.csv`(조건 배정이 있으면 `real_hotspot_comparison_v2`, 없으면 `real_hotspot_comparison_v1`)만 쓰고, `report`는 합성 numeric_v2의 `comparison_fractions.csv` 등을 요구해 `missing_input_file`(종료 코드 2)로 멈춘다. 그래서 ETX 회차에 넣지 않았다. 쌍별 결과는 6회차 `local_delta.html`에서 본다.

### 실자료 `comparison.json` v2

`condition_assignment`가 reference/target 양쪽에 설정된 실자료 `compare`는 `schema_version: real_hotspot_comparison_v2`를 쓴다. 합성 경로(`numeric_v2`)와 조건 배정이 없는 실자료 경로(검사용 `records`와 소스 해시만 쓰는 `real_hotspot_comparison_v1`)는 바뀌지 않았다. v2는 같은 정보를 잃지 않고 다음만 다르다.

- 들여쓰기 없는 compact JSON이며 최상위 키(`reference`, `target`, `records`, `status`, `reasons`, `*_status`)와 `reference`/`target` 아래 `groups[*]`의 모든 필드는 v1과 같다. 한 쪽이 계산할 case가 없으면 그 쪽은 `null`이다.
- 각 쪽(`reference`, `target`)에 `cases_raw`가 추가된다: `{case_id: raw}`로 case당 한 번만 기록한다(v1은 지름 32개 × case마다 같은 raw를 반복했다).
- `groups[*].source_cases[*].raw`는 사라지고 `raw_ref`(= `cases_raw`의 키, 보통 `identity.case_id`)가 들어간다. 같은 case_id가 서로 다른 raw를 가지면 `case_id#2`처럼 구분한다.
- `pair_ids`, `pair_lineage_case_keys`, `contributing_source_case_keys`는 v1과 같은 문자열 목록이다(정수 색인 인코딩은 쓰지 않았다; 근거는 `docs/plan/scale_compare/T2_payload.md`).

v1로 되돌리는 규칙: `schema_version`을 `real_hotspot_comparison_v1`로 바꾸고, 각 쪽에서 `cases_raw`를 빼면서 모든 `source_cases[*]`에 `raw = cases_raw[raw_ref]`를 넣고 `raw_ref`를 지운다. 시험(`tests/test_comparison_v2.py`의 `decode_v2_to_v1`)이 이 복원 결과가 v1 기록기(`tests/reference_comparison_v1.py`)의 payload와 같음을 확인한다. `comparison.csv`는 v1과 바이트 단위로 같다.

#### 요약 모드(기본)와 `compare` 설정

설정 최상위의 선택 키 `compare: {full_bucket_detail: false, top_buckets: 20}`(둘 다 생략 가능, 다른 키는 `invalid_compare_options`). 기본은 **요약**이다: line/pixel/transfer/union/조각/fraction/coverage/`source_cases`의 면적은 전체 모드와 똑같이 계산하고 `comparison.csv`도 바이트 단위로 같다. 달라지는 것은 bucket 목록뿐이다.

- 파일 최상위에 `bucket_detail: "summary"`와 `top_buckets: N`이 붙고, `groups[*].line`/`pixel`은 `exclusive`/`shared` 목록 대신 `bucket_count_exclusive`, `bucket_count_shared`, `top_buckets`(exclusive·shared를 합쳐 면적 큰 순 N개, 동률은 pair_ids 순; lineage·contributors 포함)를 가진다. `area`는 전체 bucket 면적의 합이다.
- 요약 파일은 v1로 복원할 수 없다(시험의 `decode_v2_to_v1`은 거부한다). `bucket_detail` 키가 없으면 전체 모드(위 v2 레이아웃 그대로)다.
- `full_bucket_detail: true`면 모든 bucket 목록을 쓴다. 이때 `max_bucket_output_entries`는 실제로 쓰는 항목에 적용된다. 요약 모드에서는 쓰는 상위 N개 항목에만 적용된다. 정확한 영역 분할(label 패턴)의 메모리는 별도 한도 `max_label_pattern_entries`가 막는다. 메모리 추정(`resource_estimate`)의 bucket 출력은 그룹마다 끝까지 보관되므로 32그룹×layer 수만큼 곱해 결과 보관에 들어가고(전체: 그룹당 `max_bucket_output_entries`, 요약: 그룹당 `2 × top_buckets × 3 × layer 케이스 수`), 한 그룹 직렬화분(항목 × 200 B)만 임시에 더해진다. label 패턴 부분은 한도 × 256 B 그대로다.

기록은 스트리밍이다: 전체 v1 dict를 만들지 않고 group/source 단위로 같은 폴더의 임시 파일(`.comparison.json.<pid>.<난수>.tmp`)에 쓴다. reference 쪽은 임시 파일에 다 쓴 뒤 해제하고 target을 계산한다. 두 쪽이 모두 성공하고 JSON과 CSV 임시 파일이 fsync된 뒤에만 공개하며, 쌍으로 전부 아니면 전무다: 공개 직전에 `.comparison.publishing` 기록을 (임시 파일에 쓴 뒤 이름을 바꿔) 남기고 기존 쌍을 `.comparison.json.prev`/`.comparison.csv.prev`로 보존(하드링크, 실패하면 복사)한 뒤 두 파일을 `os.replace`로 바꾸고, 그 다음 **커밋 표시 `.comparison.committed`를 원자적 이름 바꾸기로 만든다. 이 표시가 있어야 새 쌍이 확정이다.** `.prev`와 기록 파일은 커밋 표시가 생긴 뒤에만 지우고(`.prev` → 기록 → 표시 순), 이 정리 중의 오류나 Ctrl-C는 절대 되돌리지 않는다. 커밋 표시 전에 오류·Ctrl-C가 오면 기존 쌍으로 되돌린다(새 JSON + 옛 CSV가 남지 않는다). 어느 단계에서든 프로세스가 강제 종료되면 다음 `compare` 시작 때 커밋 표시가 있으면 새 쌍으로 확정하고 정리를 마치고, 없으면 기록을 보고 기존 쌍으로 되돌린다. 복구는 읽기에 성공한 기록·커밋 표시만 따른다: 기록이나 커밋 표시를 읽을 수 없거나(권한·I/O 오류) 내용이 깨졌으면 `.prev`와 기록을 지우지 않고 아무것도 바꾸지 않은 채 `comparison_recovery_unreadable` 오류로 끝난다. 원인을 고쳐 `compare`를 다시 실행하면 이어서 복구하고, 기록이 깨졌다면 `.comparison.json.prev`/`.comparison.csv.prev`를 직접 `comparison.json`/`comparison.csv`로 되돌리고 `.comparison.*` 파일을 지운다. 따라서 `comparison.json`/`comparison.csv`는 복구가 끝난 뒤 항상 (옛 JSON, 옛 CSV) 또는 (새 JSON, 새 CSV) 한 쌍이다. 강제 종료 직후부터 다음 `compare`가 복구하기 전까지는 두 파일이 서로 다른 세대일 수 있다(`.comparison.publishing`/`.comparison.committed`가 보이면 복구 전이다). 커밋 표시를 지우지 못하는 상태(권한 등)면 `compare`는 새로 쓰지 않고 오류로 끝난다. 공개를 시작하기 전(`.comparison.publishing`을 남기기 전)의 오류, 곧 target 계산 오류, 쓰는 도중의 디스크 부족(ENOSPC) 등 `OSError`, Ctrl-C는 임시 파일만 지우고 기존 `comparison.json`/`comparison.csv`를 건드리지 않는다. 공개 중 커밋 표시 전의 오류·Ctrl-C는 위 규칙대로 되돌리는 동안 잠깐 두 파일이 서로 다른 세대일 수 있다(프로세스가 강제 종료되면 `.tmp`가 남을 수 있으나, 다음 `compare`가 시작할 때 죽은 실행의 `.comparison.json`·`.comparison.csv`·`.comparison_masks.zip` 임시 파일(`.<pid>.<난수>.tmp`)을 치운다. 살아 있는 pid나 이 프로세스의 파일, 다른 이름은 건드리지 않는다. pid 확인은 POSIX에서 `os.kill(pid, 0)`, Windows에서 `OpenProcess`를 쓰고 알 수 없으면 살아 있다고 본다). CLI(`simcompare compare`)도 같은 규칙을 따른다: 위 트랜잭션을 연 뒤(`begin()` 이후, 복구 오류 `comparison_recovery_unreadable` 포함)에 나는 `OSError`·계약 오류는 종료 코드 2와 `error.json`/`execution.json`만 남기고 `comparison.json`/`comparison.csv`/`comparison_masks.zip`(이전 세대)은 지우지 않는다(`tests/test_compare_publication_cli.py`). 그 전의 입력·설정·admission 오류(manifest 검증, 옮겨진 import 폴더 포함)도 `mode: real`이면 `comparison.json`/`comparison.csv`/`comparison_masks.zip`을 지우지 않고 `error.json`만 쓴다. 설정 파일을 읽지 못하는 경우와 합성 `compare`만 예전처럼 `comparison*`을 지운다. 종료 코드 3 의미는 같다.

**`comparison_masks.zip`도 같은 공개 트랜잭션이다.** 실자료 `compare`가 만든 artefact는 `comparison.json`·`comparison.csv`와 함께 세 파일로 공개한다: 임시 파일 `.comparison_masks.zip.<pid>.<난수>.tmp`에 그룹마다 마스크를 쓰고(`meta.json`이 마지막) fsync한 뒤, `.comparison.publishing` 기록에 세 파일의 존재 여부를 적고, 기존 파일을 `.prev`로 보존하고, 세 개를 `os.replace`한 다음 같은 커밋 표시로 확정한다. 되돌리기·확정·복구·`comparison_recovery_unreadable` 규칙은 위와 같고 이름이 둘에서 셋으로 늘었을 뿐이다(예전 두 이름 기록도 읽는다). artefact를 **만들 수 없는** 경우(I/O 오류가 아니라 직렬화 오류 등)에는 경고(`comparison_masks.zip not written`)를 stderr에 내고 `comparison.json`/`comparison.csv`만 공개하며 `compare`는 실패하지 않는다(6회차는 재계산). 이때 이전 `comparison_masks.zip`은 같은 트랜잭션 안에서 함께 지워진다(실패하면 JSON·CSV와 함께 되돌아간다): 새 JSON·CSV 옆에 옛 마스크 파일이 남지 않으므로 항상 한 세대다. 쓰는 도중의 I/O 오류(ENOSPC 등)는 다른 파일과 같이 `compare`를 실패시키고 기존 세트를 건드리지 않는다. `comparison.json`·`comparison.csv`의 바이트는 artefact가 있든 없든 7bcb07a와 같다(테스트).

## 실제 자료

확인된 실제 폴더 구조는 먼저 별도 `real_hotspot_v1` 관측 manifest로 변환한다. 출력 디렉터리는 새 경로여야 한다.

```bat
.venv-cmd\Scripts\python.exe -m simcompare import-hotspots --root input/hotspot --out imported/reference --expected-cases expected_cases.txt
```

폴더 배치는 `--root`(모델 폴더, 예: `QA4_R00`) 아래 `<case>/images/{1.png,2.png,hotspot_lateral_short01.png...}`와 `<case>/result/lateral_short.csv`다(PNG를 case 폴더 바로 아래에 둬도 된다). case 폴더 이름의 불량명은 `lateralshort`처럼 붙여 써도 된다. reference/target 모델 폴더는 각각 따로 실행한다.

`--expected-cases-from-roots ROOT [ROOT ...]`는 `--root`와 여기 적은 root들에서 발견된 case 이름의 합집합을 기대 case 목록으로 쓴다(`--expected-cases`와 함께 쓸 수 없음; 빈 목록은 `expected_case_roots_empty`, `--root`와 같은 root는 `expected_case_root_is_import_root`, 같은 root 중복은 `duplicate_expected_case_root`(모두 resolve 뒤 비교, 아무것도 쓰기 전에 거부)이고, 없는 root는 `expected_case_root_missing`(`--root`는 `real_hotspot_root_missing`, `--expected-cases` 파일은 `expected_cases_file_missing`)으로 `오류: <코드>: <인수 이름> 경로가 없습니다: <입력 문자열> (resolved: <절대 경로>)`를 내고 종료 코드 2로 끝난다; manifest `expected_case_source`에 root와 한계가 기록됨. 모든 root에 없는 case는 탐지할 수 없다). 기대 목록이 있으면 출력 폴더에 `expected_cases.txt`(줄마다 case 이름, manifest `expected_cases`와 같은 순서, 머리 `#` 주석에 출처·root·한계)를 함께 쓴다. 이 파일은 `manifest.json`·`source_files`에 들어가지 않으며 기대 목록이 없으면 만들지 않는다. 끝에는 stderr에 `expected_cases: N (source=…; roots=…)` 또는 `expected_cases: none (coverage unconfirmed; stage-4 ROIs will be blocked)`를 낸다. `--expected-cases`는 case 이름을 줄마다 적은 선택 파일이며, 없으면 발견되지 않은 case folder coverage는 unconfirmed로 남는다. 입력 hotspot은 opaque grayscale PNG여야 한다. 검정=0, 모든 회색 nonzero=occupied다. 변환기는 1/2 electrode image shape와 hotspot shape의 정확한 1000 px 축 차이를 검사하고 중앙 frame `(50,50)` µm로 기록한다. manifest를 읽을 때(`load_real_manifest`)는 `expected_case_coverage_status`가 `confirmed` 또는 `unconfirmed`여야 하고 `confirmed`는 `expected_cases`가 있을 때만 허용된다(아니면 `invalid_real_expected_case_coverage_status`). `expected_case_source`를 기록한 manifest는 그 값이 `expected_cases`와 함께 있거나 함께 없어야 한다. 이 키가 없는 예전 manifest는 그대로 읽힌다. case-local `result/<불량명>.csv`의 원 숫자/정밀도와 PNG 면적 차이를 보존한다. 누락 image는 missing, 검정 image는 observed area 0이다.

`validate-data`는 관측 면적과 missing/CSV 진단을 출력하고 `compare`는 raw 면적과 상태를 출력한다. config에 확인된 `condition_assignment`와 선택 pair table을 제공하면 기존 line/pixel 면적 집계와 electrode map을 연결한다. assignment에는 `product_id`, `design_version`, `source_id`, `process_step_id`, `exposure_id`, `unit_id`, `layer_stack`을 넣고 pair table은 기존 `pairs.csv` 열을 사용한다. 값이 없으면 null로 남기고 grouping을 요구하는 결과는 unavailable이다. pair table이 없는 pair를 pixel로 자동 배정하지 않는다. density 단위/조사면적 및 한 unit 환산이 없으면 weighted rate, panel, delta는 unavailable이며 electrode image polarity가 확인되지 않은 경우 review ROI만 blocked다. Electrode map은 hotspot mask와 explicit case assignment를 사용하며 polarity를 요구하지 않는다. configured mask materialization은 group 500,000,000 cells, 전체 source 6,000,000,000 cells, 보수적 whole-operation 추정 8 GiB로 제한한다. 마지막 제한은 모든 source mask, diameter별 retained output, per-case transient margin을 추정하며 streaming 보장은 아니다. `max_operation_bytes`의 기본값은 8 GiB이며, 큰 메모리 서버에서는 명시해서 올릴 수 있지만 물리 메모리(POSIX에서 확인 가능할 때)를 넘길 수 없다. 초과 입력은 명시 오류로 중단된다. Windows 설치·pip check·도움말 실행은 사용자 성공 확인이 있으며, 실자료 수치·메모리 검증은 별도 수행해야 한다.

실제 adapter가 미확정이면 명시 오류가 정상이며 임의 synthetic 표시로 우회하지 않는다. [데이터 계약](data_contract.md), [질문 상태](open_questions.md)를 확인한다.

## legacy export 보존

```bat
.venv-cmd\Scripts\python.exe -m simcompare merge-exports --out merged export_a export_b
```

merge-exports는 기존 1.x CSV/JSON/배열의 source IDs와 raw ordered tuple을 보존한다. particle-sim 검사 wrapper의 parquet/zarr reader를 위해 pyarrow와 zarr를 직접 의존성으로 선언한다. 기존 wrapper의 --with 옵션도 호환 유지한다. 이 검사는 numeric 변환이나 실제 수치 비교 승인을 뜻하지 않는다. `schema.py`, `adapters/example.py`, `preprocess.py`는 legacy export 검산용으로 유지한다.

## 검증 범위와 개발 기록

설치용 ZIP에는 실행 의존성이 포함되며 pytest 같은 개발 도구는 포함되지 않는다. Windows 16GB에서 수백 실제 이미지 처리, 실자료 비교·지도·ROI 검증은 남아 있다. train/embed/analyze/evaluate/all은 폐기되었고 compare/electrode-map을 사용한다.

배포 파일을 만드는 유지보수 절차는 [Windows 묶음 빌드](windows_bundle_build.md)에 별도로 기록한다. 과거 계획·리뷰의 PowerShell 설명은 당시 검증 이력이며 현재 설치 지침이 아니다. R3 검토의 애플리케이션 wheel은 유지되지만 설치 파일 교체로 ZIP 해시는 달라졌다.
