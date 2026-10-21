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
        ["2회차: 검사·비교. 처음에는 real.yaml에서 max_operation_bytes를 빼고 실행해",
         "oversized_real_operation 메시지의 추정치 N을 확인한 뒤 여유를 더해 넣고 다시 실행한다."],
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
