#!/usr/bin/env python3
"""ETX(Linux 서버) 회차별 단일 파일 실행기를 bundles/etx/에 만든다. 각 파일은 같은 simcompare 묶음에 회차별 JOBS만 다르다.

경로는 실행기 BASE_DIR(작업 폴더) 기준 상대 경로 예시다. 회차 사이의 파일 복사·config 수정은 docs/usage.md
"ETX(Linux GPU 서버) 실행 순서"를 따른다."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_single_file import build  # noqa: E402

DATA = "data"          # BASE_DIR 기준 상대 경로. "/data"로 쓰면 루트 아래 절대 경로가 된다
CONFIG = "real.yaml"


def _electrode(side, model):
    return ("import-electrode-assets", [
        "--source-kind", "hotspot_case_electrode",
        "--root", f"{DATA}/input/hotspot/{model}",
        "--numeric-data-dir", f"{DATA}/imported/{side}",
        "--condition-assignment", f"assignment_{side}.json",
        "--cnt-from-dl1-root", f"{DATA}/input/dl1",
        "--source-version", "LAYOUT_V1",
        "--max-asset-memory-mb", "16384",
        "--out", f"{DATA}/assets/{side}",
    ], f"{side} ({model}). --max-asset-memory-mb는 기본값이 없는 필수 인자이며 16384는 예시다")


def _model_electrode(model):
    return ("import-electrode-assets", [
        "--source-kind", "hotspot_case_electrode",
        "--root", f"{DATA}/input/hotspot/{model}",
        "--numeric-data-dir", f"{DATA}/models/{model}",
        "--condition-assignment", f"assignment_{model}.json",
        "--cnt-from-dl1-root", f"{DATA}/input/dl1",
        "--source-version", "LAYOUT_V1",
        "--max-asset-memory-mb", "16384",
        "--out", f"{DATA}/assets/models/{model}",
    ], f"모델 {model}. --max-asset-memory-mb는 기본값이 없는 필수 인자이며 16384는 예시다. 모델마다 한 번 실행한다")


STAGES = {
    "0_dv_template": (
        ["0회차(선택, 1~6회차와 독립): 제품 case 폴더 이름에서 전극×타이밍 전압 시트의 빈 양식(dv_sheet.csv)을 만든다.",
         "PNG·CSV 내용은 열지 않는다. 전압은 항상 빈칸이고, 임피던스(low/high)는 --impedance-db CSV에 있는 전극만 채워진다.",
         "--timings(구동 타이밍 수 1~20)는 필수이며 기본값이 없다. 아래 값은 예시다. --impedance-db는 처음 제품이면 지운다",
         "(DB가 없으면 모든 전극이 new_electrodes.csv에 나온다). 채운 dv_sheet.csv를 다음 제품의 --impedance-db로 그대로 줄 수 있다.",
         "--out은 아직 없는 새 경로여야 한다. 상세는 docs/dv_template.md."],
        [("dv-template", ["--root", f"{DATA}/input/hotspot/QA4_R00", "--timings", "4",
                          "--impedance-db", f"{DATA}/electrode_impedance.csv", "--out", f"{DATA}/dv_template/QA4_R00"],
          "reference (QA4_R00). 제품마다 한 번 실행한다. 예시 — 제품에 맞게 바꾼다: 타이밍 4개. electrode_impedance.csv가 아직 없으면 --impedance-db 줄을 지운다"),
         ("dv-template", ["--root", f"{DATA}/input/hotspot/QA4_T00", "--timings", "4",
                          "--impedance-db", f"{DATA}/electrode_impedance.csv", "--out", f"{DATA}/dv_template/QA4_T00"],
          "target (QA4_T00). 제품마다 한 번 실행한다. 앞 제품에서 채운 dv_sheet.csv를 electrode_impedance.csv로 두면 겹치는 전극의 임피던스가 채워진다")]),
    "0b_dv_grade": (
        ["0b회차(선택, 0회차 뒤): 전압·임피던스를 채운 dv_sheet.csv로 전극쌍별 ΔV, 최대 타이밍, 등급(None/Low/High)을 계산한다.",
         "PNG·CSV 내용은 열지 않고 case 폴더 이름에서 short 쌍을 모은다(open과 같은 이름 쌍은 빼고 manifest에 기록).",
         "--product-id는 필수이며 기본값이 없다. --low-low-threshold-v는 선택이다(없으면 low–low 쌍도 ΔV>0이면 High).",
         "시트 오류는 행 번호와 함께 모두 한 번에 보여 주고 종료 코드 2로 멈춘다(출력 없음). Excel에서는 'CSV UTF-8'로 저장한다.",
         "--out은 아직 없는 새 경로여야 한다. 상세는 docs/dv_grade.md."],
        [("dv-grade", ["--sheet", f"{DATA}/dv_template/QA4_R00/dv_sheet.csv", "--root", f"{DATA}/input/hotspot/QA4_R00",
                       "--product-id", "QA4_R00", "--low-low-threshold-v", "3", "--out", f"{DATA}/dv_grade/QA4_R00"],
          "reference (QA4_R00). 제품마다 한 번 실행한다. 예시 — 제품에 맞게 바꾼다: 경계 3 V는 예시값. 경계를 쓰지 않으면 --low-low-threshold-v 와 값을 지운다"),
         ("dv-grade", ["--sheet", f"{DATA}/dv_template/QA4_T00/dv_sheet.csv", "--root", f"{DATA}/input/hotspot/QA4_T00",
                       "--product-id", "QA4_T00", "--low-low-threshold-v", "3", "--out", f"{DATA}/dv_grade/QA4_T00"],
          "target (QA4_T00). 제품마다 한 번 실행한다(4b회차는 두 제품의 dv-grade 출력 폴더를 모두 읽는다)")]),
    "1_import_hotspots": (
        ["1회차: hotspot import. 끝나면 pairs.csv를 data/imported/reference/와 data/imported/target/ 안에 복사한다.",
         "각 import는 --expected-cases-from-roots로 다른 모델의 root를 함께 주어, 기대 case 목록을 두 모델에서 발견된 case 이름의 합집합으로 만든다",
         "(한쪽에만 없는 case 폴더는 expected_case_folder_missing으로 보고된다. 두 모델 모두에 없는 case는 찾을 수 없다).",
         "예전 실행기로 이미 import했다면(기대 목록 없음 → 4회차 ROI 전부 blocked) 1회차부터 다시 한다. 먼저 이전 출력 폴더를 옮기거나 이름을 바꾼다",
         "(--out은 없는 경로여야 한다): data/imported/reference·target, data/assets/reference·target, data/changes, data/confirmed, data/local.",
         "회차 사이 수작업도 다시 한다: 1회차 뒤 pairs.csv 복사, 3회차 뒤 import 결과 배치와 real.yaml electrode_import_record.sha256 갱신."],
        [("import-hotspots", ["--root", f"{DATA}/input/hotspot/QA4_R00", "--expected-cases-from-roots", f"{DATA}/input/hotspot/QA4_T00",
                            "--out", f"{DATA}/imported/reference"],
          "reference (QA4_R00). --out은 아직 없는 새 경로"),
         ("import-hotspots", ["--root", f"{DATA}/input/hotspot/QA4_T00", "--expected-cases-from-roots", f"{DATA}/input/hotspot/QA4_R00",
                            "--out", f"{DATA}/imported/target"],
          "target (QA4_T00)")]),
    "1m_import_models": (
        ["1m회차(선택, 다제품·모델별 리포트): 모델(제3 제품) hotspot import. 1회차와 같은 명령을 모델마다 한 번 실행한다.",
         "모델 자산은 사용자가 나중에 모델별 폴더로 준다. 폴더 규칙은 data/models/<product_id>/ 이다(manifest.json + pairs.csv 복사,",
         "3m회차 뒤 review_assets.json·electrode_asset_import_record.json·assets/ 도 이 폴더로 복사한다). 이 폴더가 없으면 그 모델은 assets_pending 이다",
         "(5m회차에서 후보는 보이지만 accept 해도 deferred.json 에 남고 종료 코드 3).",
         "--root는 모델 hotspot root, --expected-cases-from-roots는 target(QA4_T00) root, --out은 아직 없는 data/models/<모델>이다. 모델 id QA4_C00는 예시다.",
         "끝나면 pairs.csv를 data/models/<모델>/ 안에 복사한다. 모델 키(product_id)는 models.yaml(configs/etx/models.example.yaml)의 모델 키와 같아야 한다.",
         "모델이 둘 이상이면 모델마다 JOBS 줄을 늘린다. 1~7회차의 reference·target 흐름은 바뀌지 않는다."],
        [("import-hotspots", ["--root", f"{DATA}/input/hotspot/QA4_C00", "--expected-cases-from-roots", f"{DATA}/input/hotspot/QA4_T00",
                            "--out", f"{DATA}/models/QA4_C00"],
          "모델 QA4_C00 (예시). --out은 아직 없는 새 경로. 끝나면 pairs.csv를 이 폴더에 복사")]),
    "2_validate_compare": (
        ["2회차: 검사·비교. 마스크를 한 장씩 읽는 스트리밍이라 기본 max_operation_bytes(8 GiB)로 충분한 것이 정상이다.",
         "oversized_real_operation이 나오면 메시지의 추정치 N에 여유를 더해(물리 메모리 이하) max_operation_bytes로 넣는다.",
         "compare는 comparison.json/csv와 함께 comparison_masks.zip을 만든다(6회차가 두 모델의 면적 계산을 건너뛰는 데 쓴다)."],
        [("validate-data", ["--config", CONFIG], None),
         ("compare", ["--config", CONFIG], None)]),
    "3_import_electrode_assets": (
        ["3회차: 전극 PNG import. assignment_<side>.json(real.yaml의 condition_assignment와 같은 내용)이 필요하다.",
         "끝나면 출력의 review_assets.json, electrode_asset_import_record.json, assets/를 각 data_dir로 복사하고",
         "real.yaml에 asset_manifest, electrode_import_record({path, sha256})를 넣는다."],
        [_electrode("reference", "QA4_R00"), _electrode("target", "QA4_T00")]),
    "3m_import_model_assets": (
        ["3m회차(선택, 1m회차 뒤): 모델 전극 PNG import. 3회차의 import-electrode-assets와 같은 형식이며 모델마다 한 번 실행한다.",
         "assignment_<모델>.json은 models.yaml의 해당 모델 condition_assignment와 똑같은 내용이다(JSON에는 주석을 쓸 수 없다).",
         "--numeric-data-dir은 1m회차 출력(data/models/<모델>), --out은 아직 없는 새 경로(data/assets/models/<모델>)다.",
         "끝나면 출력의 review_assets.json, electrode_asset_import_record.json, assets/를 data/models/<모델>/ 로 복사하고",
         "models.yaml의 그 모델 절에 asset_manifest, electrode_import_record({path, sha256})를 넣는다(없으면 asset 없이 후보만 본다)."],
        [_model_electrode("QA4_C00")]),
    "4_candidates": (
        ["4회차: 변경점 ROI(candidates-change). 두 제품의 CNT 층 3개를 한 offset으로 정렬하고, 정렬로 설명되지 않는 5 µm hotspot 변경 위치를 순위로 뽑는다.",
         "(실행기 파일 이름은 그대로 simcompare_job_4_candidates.py다. 예전 candidates(max_count, 취약점 ROI)는 보류다.)",
         "필수 인수는 기본값이 없다. 아래 값은 예시이니 제품에 맞게 바꾼다: --cnt-from-dl1-root(3회차와 같은 DL1 PNG root),",
         "--align-search-um(정렬 탐색 반경 µm; 최소값은 pixel pitch의 2배 이상으로 코드가 검사한다. 권장값은 예상 레이아웃 이동과 반복 패턴 주기를 모두 덮는 값(예시 30 µm).",
         "  주기가 탐색 반경 안에 있으면 peak_ambiguous 와 대안 offset 으로 표시된다), --align-min-dice(정렬 최소 Dice), --align-layer-tolerance-um(층별 offset 허용 µm),",
         "--change-tolerance-um(이 거리 안의 이동은 변경이 아님), --window-um(변경 창 한 변), --merge-overlap(창 병합 기준), --top-k(방향별 ROI 수),",
         "--mask-derivation-rule(DL2 mask_derivation 규칙. between_stack_order_v1 만 허용).",
         "선택 인수: --merge-scope all(층을 넘어 창 병합), --suppress-overlap 0.3(겹치는 ROI 억제 비율). 예시 값이다 — 생략하면 이전 동작(층별 병합, 억제 없음).",
         "--electrode-png-root: alignment.html 층 묶음의 층별 전극 형상을 읽는 DL1 전극 PNG root(<제품>/<전극>/<층>.png). hotspot 폴더의 case 이미지는 쓰지 않는다.",
         "  예시는 --cnt-from-dl1-root 와 같은 data/input/dl1 이다. 전극 PNG 가 다른 곳에 있으면 그 폴더로 바꾼다(생략하면 --cnt-from-dl1-root).",
         "--local-align-um 1: ROI 국소 정렬 진단 1 µm, 예시 값이다(선택; 생략하면 진단 없음). round(값/pitch) 가 2..30 px 여야 한다. 결과는 roi_local_alignment.json 과 alignment.html 5절 표(참고용, 계산에는 쓰이지 않는다).",
         "결과: alignment.html(정렬 요약·변경 지도), direct/roi_correspondence.json(6회차 입력), *_export/·*_index_export/ 의 review_rois.json + mask_derivation.json(DL2 입력).",
         "종료 코드 3(부분 결과)이어도 실행기는 계속한다. 결과는 로그의 'candidates-change exit N' 줄로 판정한다.",
         "DL2 없이 직접 대응을 쓰면 5회차를 건너뛰고 6회차로 간다."],
        [("candidates-change", ["--config", CONFIG, "--cnt-from-dl1-root", f"{DATA}/input/dl1",
                                "--align-search-um", "30", "--align-min-dice", "0.3", "--align-layer-tolerance-um", "1",
                                "--change-tolerance-um", "0.5", "--window-um", "10", "--merge-overlap", "0.5",
                                "--top-k", "20", "--mask-derivation-rule", "between_stack_order_v1",
                                "--merge-scope", "all", "--suppress-overlap", "0.3",
                                "--electrode-png-root", f"{DATA}/input/dl1", "--local-align-um", "1", "--out", f"{DATA}/changes"],
          "예시 — 제품에 맞게 바꾼다: 탐색 30 µm, Dice 0.3, 층 허용 1 µm, 변경 허용 0.5 µm, 창 10 µm, top-k 20. 기본값이 아니다")]),
    "4b_candidates_dv": (
        ["4b회차(선택, 0b회차와 3회차 뒤): ΔV 등급으로 가중한 취약점 ROI(candidates-dv). 변경점 ROI(4회차)와 독립이며 출력 폴더도 따로 쓴다.",
         "두 제품 모두 real_hotspot_v1이어야 하고, 0b회차의 dv-grade 출력 폴더 둘(제품마다 하나, 서로 다른 폴더)이 필요하다.",
         "모든 인수는 필수이며 기본값이 없다. 아래 값은 예시이니 제품에 맞게 바꾼다: --dv-grades-reference/-target(0b회차 출력 폴더;",
         "  manifest의 product_id가 각 제품과 같아야 한다), --min-count(영역 최소 전극 수 K), --roi-grade(high | low_or_high),",
         "--window-um(질의 창 한 변 µm), --merge-overlap(창 병합 기준 (0,1]). --max-candidates(방향별 질의 수 상한)는 선택이라 예시에 넣지 않았다.",
         "real.yaml은 3회차 뒤 상태(asset_manifest, electrode_import_record 기입)여야 한다. --out은 아직 없는 새 경로여야 한다.",
         "결과: candidate_queries_v1.json, vulnerability_v1.json, grade_maps/, funnel.json/csv, *_export/·*_index_export/(DL2 입력). 상세는 docs/dv_candidates.md.",
         "종료 코드 3(부분 결과)이어도 실행기는 계속한다. 결과는 로그의 'candidates-dv exit N' 줄로 판정한다."],
        [("candidates-dv", ["--config", CONFIG, "--dv-grades-reference", f"{DATA}/dv_grade/QA4_R00",
                            "--dv-grades-target", f"{DATA}/dv_grade/QA4_T00", "--min-count", "4", "--roi-grade", "high",
                            "--window-um", "20", "--merge-overlap", "0.5", "--out", f"{DATA}/changes_dv"],
          "예시 — 제품에 맞게 바꾼다: K 4, High 쌍만 선정, 창 20 µm, 병합 0.5. 기본값이 아니다. 0b회차 출력 폴더 이름(QA4_R00, QA4_T00)도 맞춘다")]),
    "4c_vulnerability_report": (
        ["4c회차(선택, 4b회차 뒤): 취약점 ROI 수치 근거 HTML(vulnerability-report). 4b회차 출력 폴더만 읽고 수치를 다시 계산하지 않는다.",
         "--candidates-dv-dir(4b회차의 --out 폴더)와 --out은 필수이며 기본값이 없다. --out은 아직 없는 새 경로이고 입력 폴더 밖이어야 한다.",
         "--top-n(표시할 상위 ROI 수)은 선택이며 기본 10이다(표시용). 아래 예시는 기본을 따른다.",
         "4b회차 입력 기록의 data_dir(절대 경로)로 전극 layout 자산을 다시 읽으므로 4b회차와 같은 서버에서 이어서 실행해야 한다.",
         "자산 해시가 어긋나면 layout 지도만 빠지고 종료 코드 3이다. 결과: report.html, report.json. 상세는 docs/dv_candidates.md."],
        [("vulnerability-report", ["--candidates-dv-dir", f"{DATA}/changes_dv", "--out", f"{DATA}/vulnerability_report"],
          "예시 — 4b회차 --out과 같은 폴더를 입력으로. --top-n을 바꾸려면 인수를 추가한다")]),
    "5_confirm_pairs": (
        ["5회차(DL2 경로 전용): 직접 대응(4회차의 direct/roi_correspondence.json)을 쓰면 이 회차를 건너뛴다.",
         "다른 제품 탐색이 필요할 때만 쓴다: DL2 baseline_ncc 결과(ncc_r2t.json, ncc_t2r.json)와 sheet.html을 보고 쓴",
         "decisions.csv(헤더 candidate_id,decision,note; decision=accept|reject)가 필요하다.",
         "--match 파일은 baseline_ncc 결과(psdl2_baseline_ncc_v1) 또는 DL2 search 배치 봉투(psdl2_search_batch_v1)를 읽는다. 배치 봉투는 방향마다 따로 줄 수도 있고, DL2 가 쓴 search_batch.json 하나를 두 --match 에 같이 줄 수도 있다.",
         "첫 작업(--list)은 candidate_id 목록을 로그에 찍는다. decisions.csv가 없으면 둘째 작업이 종료 코드 2로 멈춘다.",
         "--labelled-by(확정한 사용자 ID)와 --mutual-tolerance-um은 필수이며 기본값이 없다. 값은 예시다."],
        [("confirm-pairs", ["--list", "--config", CONFIG, "--candidates", f"{DATA}/changes",
                            "--match", f"reference_to_target={DATA}/dl2/ncc_r2t.json",
                            "--match", f"target_to_reference={DATA}/dl2/ncc_t2r.json"], "candidate_id 목록만 출력"),
         ("confirm-pairs", ["--config", CONFIG, "--candidates", f"{DATA}/changes",
                            "--match", f"reference_to_target={DATA}/dl2/ncc_r2t.json",
                            "--match", f"target_to_reference={DATA}/dl2/ncc_t2r.json",
                            "--decisions", f"{DATA}/dl2/decisions.csv", "--labelled-by", "CHANGE_ME",
                            "--mutual-tolerance-um", "5", "--out", f"{DATA}/confirmed"],
          "accept 후보만 roi_correspondence.json으로. --labelled-by CHANGE_ME를 실제 ID로 바꾼다")]),
    "5m_confirm_pairs_models": (
        ["5m회차(선택, DL2 경로, 4회차 뒤): 다제품 확정. 5회차와 같은 confirm-pairs에 --models(models.yaml, real.yaml과 같은 폴더)를 더한다.",
         "모델 후보는 DL2 search_batch.json(psdl2_search_batch_v1)에서 온다. 파일 하나를 두 --match에 같이 줄 수 있다(아래 예시).",
         "모델 쌍은 target 질의(target_to_reference)에서만 생긴다. reference_to_target 후보는 reference 쌍으로만 확정된다.",
         "첫 작업(--list)은 모델 후보를 포함한 candidate_id 목록을 로그에 찍는다. decisions.csv가 없으면 둘째 작업이 종료 코드 2로 멈춘다.",
         "출력 data/confirmed 에는 reference 쌍(roi_correspondence.json)과 모델 쌍(models/<모델>/roi_correspondence.json), models/decisions.json·index.json이 나온다.",
         "자산이 없는 모델(assets_pending)을 accept 하면 deferred.json에 남고 종료 코드 3이다(종료 코드 3은 실패가 아니다).",
         "--labelled-by(확정한 사용자 ID)와 --mutual-tolerance-um은 필수이며 기본값이 없다. 값은 예시다. --out은 아직 없는 새 경로여야 한다(5회차 data/confirmed와 같은 경로를 쓰므로 둘 중 하나만 실행한다)."],
        [("confirm-pairs", ["--list", "--config", CONFIG, "--candidates", f"{DATA}/changes", "--models", "models.yaml",
                            "--match", f"reference_to_target={DATA}/dl2/search_batch.json",
                            "--match", f"target_to_reference={DATA}/dl2/search_batch.json"], "candidate_id 목록만 출력(모델 후보 포함)"),
         ("confirm-pairs", ["--config", CONFIG, "--candidates", f"{DATA}/changes", "--models", "models.yaml",
                            "--match", f"reference_to_target={DATA}/dl2/search_batch.json",
                            "--match", f"target_to_reference={DATA}/dl2/search_batch.json",
                            "--decisions", f"{DATA}/dl2/decisions.csv", "--labelled-by", "CHANGE_ME",
                            "--mutual-tolerance-um", "5", "--out", f"{DATA}/confirmed"],
          "accept 후보만 roi_correspondence.json(reference)과 models/<모델>/roi_correspondence.json으로. --labelled-by CHANGE_ME를 실제 ID로 바꾼다")]),
    "6_compare_windows": (
        ["6회차: 대응 쌍 위치의 창별 국부 ΔCA. 기본 입력은 4회차의 직접 대응 data/changes/direct/roi_correspondence.json이다.",
         "DL2 경로(5회차)를 쓴 경우에는 --correspondence를 data/confirmed/roi_correspondence.json으로 바꾼다(아래 JOBS 주석 참고).",
         "창 10/20/30 µm, jitter 0.5 µm, 정렬 지름 5 µm는 권장 운용값(코드 기본값 아님)이다.",
         "결과 local_delta.html이 쌍별 ΔCA 보기다. 통합 report는 아직 실자료(real_hotspot) compare 출력을 읽지 못해 회차에서 뺐다.",
         "2회차의 comparison_masks.zip이 맞으면 재사용하고 없거나 입력이 바뀌었으면 다시 계산한다. 출력 폴더는 한 세대로 공개되며 죽은 뒤 다시 실행하면 복구한다."],
        [("compare-windows", ["--config", CONFIG, "--correspondence", f"{DATA}/changes/direct/roi_correspondence.json",
                              # DL2 경로: f"{DATA}/confirmed/roi_correspondence.json"
                              "--window-sizes", "10", "20", "30", "--jitter-um", "0.5", "--sort-diameter-um", "5",
                              "--out", f"{DATA}/local"],
          "기본: 4회차 직접 대응. DL2 경로(5회차)를 썼다면 --correspondence 값을 data/confirmed/roi_correspondence.json으로 바꾼다")]),
    "6m_compare_windows_models": (
        ["6m회차(선택, 5m회차 뒤): 모델 쌍의 창별 국부 ΔCA(a = target, b = 모델). 모델마다 한 번 실행한다.",
         "--out은 모델별 폴더(data/local/models/<모델>)다. --correspondence는 5m회차의 data/confirmed/models/<모델>/roi_correspondence.json이다.",
         "모델 실행은 2회차 comparison_masks.zip을 재사용하지 않고 target·모델 두 쪽 면적을 다시 계산한다(6회차보다 시간·메모리가 더 든다).",
         "--models는 models.yaml의 모든 configured 모델을 로드한다(--model 하나만 쓰더라도 다른 모델 폴더가 깨져 있으면 영향을 받는다).",
         "--case-window-um·--case-diameter-um(창 20 µm, 지름 5 µm는 예시)을 주면 쌍×case 마다 마스크를 다시 읽어 window_cases.json/csv(관측 hotspot case 구성 차이)를 만든다.",
         "  이 값은 --window-sizes 안의 창, 두 데이터셋에 있는 지름이어야 하고 7회차 model-report의 --window-um·--diameter-um 과 같아야 한다.",
         "실규모 실행 시간은 로그의 'compare-windows exit N in Ns' 줄로 확인한다(아직 실측이 없다).",
         "reference 쌍의 창 계산은 6회차를 5회차(DL2) 대응 data/confirmed/roi_correspondence.json 으로 다시 실행해 data/local 에 둔다(7회차 입력)."],
        [("compare-windows", ["--config", CONFIG, "--models", "models.yaml", "--model", "QA4_C00",
                              "--correspondence", f"{DATA}/confirmed/models/QA4_C00/roi_correspondence.json",
                              "--window-sizes", "10", "20", "30", "--jitter-um", "0.5", "--sort-diameter-um", "5",
                              "--case-window-um", "20", "--case-diameter-um", "5",
                              "--out", f"{DATA}/local/models/QA4_C00"],
          "모델 QA4_C00 (예시). --out은 모델별 폴더. 모델이 여럿이면 모델마다 JOBS 줄을 늘린다")]),
    "7_model_report": (
        ["7회차(선택, 6m회차 뒤): 모델별 변경사항·불량 변화 한 장(model-report). 행 = target ROI, 열 = 모델(reference 포함).",
         "각 셀은 기존 출력(candidates-change, confirm-pairs --models, compare-windows)에서 복사한 값이며 새로 계산하지 않는다. 모든 인수는 필수이며 기본값이 없다.",
         "변경사항 = reference→target 변경 속성(ROI 열)과 모델 쌍의 case 구성 차이(둘 다/target만/모델만). 불량 변화 = 창 --window-um·지름 --diameter-um 의 union ΔCA(µm²)다.",
         "부호: 원값은 모델 쌍 = 모델−target, reference 쌍 = target−reference다. 통일 열(delta_model_minus_target_um2)은 reference 쌍만 뒤집어 모두 모델−target 으로 맞춘다.",
         "case 구성 차이는 인과를 뜻하지 않는다(관측 목록일 뿐이다).",
         "--windows는 제품마다 하나: 키 QA4_R00 = reference 쌍 폴더(data/local), 키 QA4_C00 = 6m회차 모델 폴더. 모델이 여럿이면 줄을 늘린다.",
         "reference 열이 채워지려면 reference 쌍의 6회차 실행이 DL2 경로의 대응 파일 data/confirmed/roi_correspondence.json 을 써야 한다",
         "(4회차 직접 대응으로 만든 data/local 이면 sha256 이 달라 model_report_correspondence_mismatch 로 멈춘다). --out은 아직 없는 새 경로다."],
        [("model-report", ["--candidates", f"{DATA}/changes", "--confirm", f"{DATA}/confirmed",
                           "--windows", f"QA4_R00={DATA}/local", "--windows", f"QA4_C00={DATA}/local/models/QA4_C00",
                           "--diameter-um", "5", "--window-um", "20", "--out", f"{DATA}/model_report"],
          "예시 — 지름 5 µm, 창 20 µm. 결과: model_report.html/.csv/.json, execution.json")]),
}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=str(ROOT / "bundles/etx"))
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args(argv)
    results = []
    for stage, (header, jobs) in STAGES.items():
        out = Path(a.out_dir) / f"simcompare_job_{stage}.py"
        results.append(build(out, overwrite=a.overwrite, jobs=jobs, header=header,
                             log_file=f"runs/simcompare_{stage}.log"))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
