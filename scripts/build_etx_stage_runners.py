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
    "1_import_hotspots": (
        ["1회차: hotspot import. 끝나면 pairs.csv를 data/imported/reference/와 data/imported/target/ 안에 복사한다."],
        [("import-hotspots", ["--root", f"{DATA}/input/hotspot/QA4_R00", "--out", f"{DATA}/imported/reference"],
          "reference (QA4_R00). --out은 아직 없는 새 경로"),
         ("import-hotspots", ["--root", f"{DATA}/input/hotspot/QA4_T00", "--out", f"{DATA}/imported/target"],
          "target (QA4_T00)")]),
    "2_validate_compare": (
        ["2회차: 검사·비교. 마스크를 한 장씩 읽는 스트리밍이라 기본 max_operation_bytes(8 GiB)로 충분한 것이 정상이다.",
         "oversized_real_operation이 나오면 메시지의 추정치 N에 여유를 더해(물리 메모리 이하) max_operation_bytes로 넣는다."],
        [("validate-data", ["--config", CONFIG], None),
         ("compare", ["--config", CONFIG], None)]),
    "3_import_electrode_assets": (
        ["3회차: 전극 PNG import. assignment_<side>.json(real.yaml의 condition_assignment와 같은 내용)이 필요하다.",
         "끝나면 출력의 review_assets.json, electrode_asset_import_record.json, assets/를 각 data_dir로 복사하고",
         "real.yaml에 asset_manifest, electrode_import_record({path, sha256})를 넣는다."],
        [_electrode("reference", "QA4_R00"), _electrode("target", "QA4_T00")]),
    "4_candidates": (
        ["4회차: 후보(DL2 직전). --min-count, --window-um, --merge-overlap은 필수이며 값은 예시다.",
         "결과의 *_export/review_rois.json, *_index_export/review_rois.json이 DL2 입력이고 dl2_model_roles가 DL2 model.roles 목록이다."],
        [("candidates", ["--config", CONFIG, "--min-count", "3", "--window-um", "20", "--merge-overlap", "0.5",
                         "--out", f"{DATA}/candidates_k3"], None)]),
    "5_confirm_pairs": (
        ["5회차(DL2 뒤): 확정 쌍 변환. DL2 baseline_ncc 결과(ncc_r2t.json, ncc_t2r.json)와 sheet.html을 보고 쓴",
         "decisions.csv(헤더 candidate_id,decision,note; decision=accept|reject)가 필요하다.",
         "첫 작업(--list)은 candidate_id 목록을 로그에 찍는다. decisions.csv가 없으면 둘째 작업이 종료 코드 2로 멈춘다.",
         "--labelled-by(확정한 사용자 ID)와 --mutual-tolerance-um은 필수이며 기본값이 없다. 값은 예시다."],
        [("confirm-pairs", ["--list", "--config", CONFIG, "--candidates", f"{DATA}/candidates_k3",
                            "--match", f"reference_to_target={DATA}/dl2/ncc_r2t.json",
                            "--match", f"target_to_reference={DATA}/dl2/ncc_t2r.json"], "candidate_id 목록만 출력"),
         ("confirm-pairs", ["--config", CONFIG, "--candidates", f"{DATA}/candidates_k3",
                            "--match", f"reference_to_target={DATA}/dl2/ncc_r2t.json",
                            "--match", f"target_to_reference={DATA}/dl2/ncc_t2r.json",
                            "--decisions", f"{DATA}/dl2/decisions.csv", "--labelled-by", "CHANGE_ME",
                            "--mutual-tolerance-um", "5", "--out", f"{DATA}/confirmed"],
          "accept 후보만 roi_correspondence.json으로. --labelled-by CHANGE_ME를 실제 ID로 바꾼다")]),
    "6_compare_windows": (
        ["6회차: 확정 쌍 위치의 창별 국부 ΔCA. 창 10/20/30 µm, jitter 0.5 µm, 정렬 지름 5 µm는 권장 운용값(코드 기본값 아님)이다.",
         "결과 local_delta.html이 쌍별 ΔCA 보기다. 통합 report는 아직 실자료(real_hotspot) compare 출력을 읽지 못해 회차에서 뺐다."],
        [("compare-windows", ["--config", CONFIG, "--correspondence", f"{DATA}/confirmed/roi_correspondence.json",
                              "--window-sizes", "10", "20", "30", "--jitter-um", "0.5", "--sort-diameter-um", "5",
                              "--out", f"{DATA}/local"], None)]),
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
