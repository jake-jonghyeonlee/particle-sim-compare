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


STAGES = {
    "0_dv_template": (
        ["0회차(선택, 1~6회차와 독립): 제품 case 폴더 이름에서 전극×타이밍 전압 시트의 빈 양식(dv_sheet.csv)을 만든다.",
         "PNG·CSV 내용은 열지 않는다. 전압은 항상 빈칸이고, 임피던스(low/high)는 --impedance-db CSV에 있는 전극만 채워진다.",
         "--timings(구동 타이밍 수 1~20)는 필수이며 기본값이 없다. 아래 값은 예시다. --impedance-db는 처음 제품이면 지운다",
         "(DB가 없으면 모든 전극이 new_electrodes.csv에 나온다). 채운 dv_sheet.csv를 다음 제품의 --impedance-db로 그대로 줄 수 있다.",
         "--out은 아직 없는 새 경로여야 한다. 상세는 docs/dv_template.md."],
        [("dv-template", ["--root", f"{DATA}/input/hotspot/QA4_R00", "--timings", "4",
                          "--impedance-db", f"{DATA}/electrode_impedance.csv", "--out", f"{DATA}/dv_template/QA4_R00"],
          "예시 — 제품에 맞게 바꾼다: 타이밍 4개. electrode_impedance.csv가 아직 없으면 --impedance-db 줄을 지운다")]),
    "0b_dv_grade": (
        ["0b회차(선택, 0회차 뒤): 전압·임피던스를 채운 dv_sheet.csv로 전극쌍별 ΔV, 최대 타이밍, 등급(None/Low/High)을 계산한다.",
         "PNG·CSV 내용은 열지 않고 case 폴더 이름에서 short 쌍을 모은다(open과 같은 이름 쌍은 빼고 manifest에 기록).",
         "--product-id는 필수이며 기본값이 없다. --low-low-threshold-v는 선택이다(없으면 low–low 쌍도 ΔV>0이면 High).",
         "시트 오류는 행 번호와 함께 모두 한 번에 보여 주고 종료 코드 2로 멈춘다(출력 없음). Excel에서는 'CSV UTF-8'로 저장한다.",
         "--out은 아직 없는 새 경로여야 한다. 상세는 docs/dv_grade.md."],
        [("dv-grade", ["--sheet", f"{DATA}/dv_template/QA4_R00/dv_sheet.csv", "--root", f"{DATA}/input/hotspot/QA4_R00",
                       "--product-id", "QA4_R00", "--low-low-threshold-v", "3", "--out", f"{DATA}/dv_grade/QA4_R00"],
          "예시 — 제품에 맞게 바꾼다: 경계 3 V는 예시값. 경계를 쓰지 않으면 --low-low-threshold-v 와 값을 지운다")]),
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
    "4_candidates": (
        ["4회차: 변경점 ROI(candidates-change). 두 제품의 CNT 층 3개를 한 offset으로 정렬하고, 정렬로 설명되지 않는 5 µm hotspot 변경 위치를 순위로 뽑는다.",
         "(실행기 파일 이름은 그대로 simcompare_job_4_candidates.py다. 예전 candidates(max_count, 취약점 ROI)는 보류다.)",
         "모든 인수는 필수이며 기본값이 없다. 아래 값은 예시이니 제품에 맞게 바꾼다: --cnt-from-dl1-root(3회차와 같은 DL1 PNG root),",
         "--align-search-um(정렬 탐색 반경 µm; 최소값은 pixel pitch의 2배 이상으로 코드가 검사한다. 권장값은 예상 레이아웃 이동과 반복 패턴 주기를 모두 덮는 값(예시 30 µm).",
         "  주기가 탐색 반경 안에 있으면 peak_ambiguous 와 대안 offset 으로 표시된다), --align-min-dice(정렬 최소 Dice), --align-layer-tolerance-um(층별 offset 허용 µm),",
         "--change-tolerance-um(이 거리 안의 이동은 변경이 아님), --window-um(변경 창 한 변), --merge-overlap(창 병합 기준), --top-k(방향별 ROI 수),",
         "--mask-derivation-rule(DL2 mask_derivation 규칙. between_stack_order_v1 만 허용).",
         "결과: alignment.html(정렬 요약·변경 지도), direct/roi_correspondence.json(6회차 입력), *_export/·*_index_export/ 의 review_rois.json + mask_derivation.json(DL2 입력).",
         "종료 코드 3(부분 결과)이어도 실행기는 계속한다. 결과는 로그의 'candidates-change exit N' 줄로 판정한다.",
         "DL2 없이 직접 대응을 쓰면 5회차를 건너뛰고 6회차로 간다."],
        [("candidates-change", ["--config", CONFIG, "--cnt-from-dl1-root", f"{DATA}/input/dl1",
                                "--align-search-um", "30", "--align-min-dice", "0.3", "--align-layer-tolerance-um", "1",
                                "--change-tolerance-um", "0.5", "--window-um", "20", "--merge-overlap", "0.5",
                                "--top-k", "20", "--mask-derivation-rule", "between_stack_order_v1", "--out", f"{DATA}/changes"],
          "예시 — 제품에 맞게 바꾼다: 탐색 30 µm, Dice 0.3, 층 허용 1 µm, 변경 허용 0.5 µm, 창 20 µm, top-k 20. 기본값이 아니다")]),
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
