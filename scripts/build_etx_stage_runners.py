#!/usr/bin/env python3
"""ETX(Linux 서버) 단일 파일 실행기를 bundles/etx/ 에 만든다.
- bundles/etx/simcompare_job_etx.py: 기본 흐름(01·03·02·diag·04·05·06·07)을 실행기 하나로 돌리는 통합 실행기.
  JOBS 를 직접 적지 않는다. EDIT 블록의 STEPS 와 설정 파일 두 개(real.yaml, compare2.yaml)에서 simcompare.etx_flow 가 JOBS 를 만든다.
- bundles/etx/simcompare_job_models_01~05_*.py: 다제품(모델별 리포트) 회차별 실행기.
- bundles/etx/dv/simcompare_job_NN_*.py: ΔV·위험도 흐름 회차별 실행기.
모든 파일은 같은 simcompare 묶음을 담는다. 경로는 실행기 BASE_DIR(작업 폴더) 기준 상대 경로다.
사용 순서와 사용자가 미리 둘 파일은 docs/usage.md "ETX(Linux GPU 서버) 실행 순서"를 따른다."""
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
FLOW_NAME = "etx"      # 통합 실행기 파일 이름: simcompare_job_etx.py

FLOW_HEADER = [
    "ETX 기본 흐름 통합 실행기. 01(hotspot import) → 03(전극 PNG import) → 02(compare2) → diag(검사·비교) → 04(변경점 ROI) → 05(DL2 확정, 선택) → 06(창별 국부 ΔCA) → 07(보고서)를 한 파일로 돌린다.",
    "모델 이름·hotspot 입력 root·DL1 root·지름·창은 설정 파일(real.yaml, compare2.yaml)에서 읽는다. 이 파일의 EDIT 블록에는 그 밖의 값만 둔다.",
    "순서·준비물·재실행 방법은 docs/usage.md \"ETX(Linux GPU 서버) 실행 순서\"에 있다.",
]

FLOW_EDIT = '''# 돌릴 단계. 순서는 여기 적은 순서가 아니라 항상 01, 03, 02, diag, 04, 05, 06, 07 이다. 모르는 이름은 실행 전에 오류로 멈춘다.
#   01 hotspot import   03 전극 PNG import   02 compare2(vi)   diag 검사·비교   04 변경점 ROI   05 DL2 확정(기본에서 뺌)   06 창별 ΔCA   07 보고서
# 이전 결과 보관: 고른 단계의 출력 폴더가 이미 있으면 실행 전에 data/_runs/<RUN_TAG>/ 로 옮긴다(삭제하지 않는다). 대상이 이미 있으면 아무것도 옮기지 않고 멈춘다.
# 단계를 고르는 법: 전체 다시 = 아래 기본값. 02 부터 = ["02", "diag", "04", "06", "07"] (01·03 결과를 그대로 쓴다). 07 만 = ["07"].
STEPS = ["01", "03", "02", "diag", "04", "06", "07"]
RUN_TAG = None                                # None 이면 UTC 시각 YYYYMMDD-HHMMSS. 문자열이면 그 이름(/ 와 \\ 금지)
DRY_RUN = False                               # True 이면 만든 JOBS 와 옮길 폴더만 로그에 찍고 아무것도 실행·이동하지 않는다. 처음에는 True 로 확인한다

REAL_CONFIG = "real.yaml"                     # BASE_DIR 안의 real.yaml (모델 이름·data_dir·asset 폴더·output_dir 을 여기서 읽는다)
COMPARE2_CONFIG = "compare2.yaml"             # BASE_DIR 안의 compare2.yaml (hotspot_root·dl1_root·diameter_um·map_window_um·output_dir 을 여기서 읽는다)

INCLUDE_CNO = True                            # 01: CNO(lateral_open) case 도 import. 쓰지 않으면 False (켠/끈 실행은 01 부터 다시 한다)
MAX_ASSET_MEMORY_MB = 16384                   # 03: asset 디코딩 메모리 상한(MB, 기본값 없는 필수 인자). 16384 는 예시
# 04: candidates-change 인수. 아래 값은 모두 예시이니 제품에 맞게 바꾼다. --config, --cnt-from-dl1-root, --electrode-png-root, --window-um, --diameter-um, --out 은
# 설정 파일에서 오므로 여기 적으면 오류다. 인수의 뜻은 docs/usage_reference.md "ROI 선정"과 docs/usage.md 에 있다.
ALIGN_ARGS = [
    "--align-search-um", "30", "--align-min-dice", "0.3", "--align-layer-tolerance-um", "1",
    "--change-tolerance-um", "0.5", "--merge-overlap", "0.5", "--top-k", "20",
    "--mask-derivation-rule", "between_stack_order_v1", "--merge-scope", "all", "--suppress-overlap", "0.3",
    "--unit-align-um", "3", "--apply-align", "local", "--local-align-um", "1", "--unit-fold",
]
EXTRA_WINDOWS_UM = []                         # 06: --window-sizes 는 compare2.yaml report.map_window_um 하나. 더 보려면 [20, 30] 처럼 추가
JITTER_UM = 0.5                               # 06: --jitter-um (권장 운용값, 코드 기본값 아님)
# 05(DL2 확정)를 STEPS 에 넣을 때만 쓴다. 05 를 고르면 06 은 data/confirmed 의 대응을, 07 은 --confirm data/confirmed 를 쓴다.
DL2_MATCH_ARGS = ["--match", "reference_to_target=data/dl2/ncc_r2t.json", "--match", "target_to_reference=data/dl2/ncc_t2r.json"]
DL2_DECISION_ARGS = ["--decisions", "data/dl2/decisions.csv", "--labelled-by", "CHANGE_ME", "--mutual-tolerance-um", "5"]
# 04–07 의 출력 폴더(기본값 그대로 쓰면 된다). 01·03·02·diag 의 출력 폴더는 설정 파일에서 온다.
CHANGES_DIR = "data/changes"
CONFIRMED_DIR = "data/confirmed"
LOCAL_DIR = "data/local"
REPORT_DIR = "data/compare2_report"

LOG_FILE = "runs/simcompare_job_etx.log"      # stdout/stderr를 이 파일에도 남긴다. 필요 없으면 None
WORK_DIR = None                               # 패키지를 풀 폴더. None이면 이 파일 옆, 쓸 수 없으면 임시 폴더
BASE_DIR = None                               # 설정 파일·상대 경로·LOG_FILE·WORK_DIR 의 기준 폴더(절대 경로). None이면 이 .py가 있는 폴더
'''

FLOW_FUNC = '''def _flow_jobs():
    """EDIT 블록 값과 설정 파일에서 JOBS 를 만들고(simcompare.etx_flow), 고른 단계의 이전 출력 폴더를 data/_runs/<RUN_TAG>/ 로 옮긴다."""
    from simcompare import etx_flow
    settings = dict(steps=STEPS, run_tag=RUN_TAG, real_config=REAL_CONFIG, compare2_config=COMPARE2_CONFIG,
                    include_cno=INCLUDE_CNO, max_asset_memory_mb=MAX_ASSET_MEMORY_MB, align_args=ALIGN_ARGS,
                    extra_window_sizes_um=EXTRA_WINDOWS_UM, jitter_um=JITTER_UM, dl2_match_args=DL2_MATCH_ARGS,
                    dl2_decision_args=DL2_DECISION_ARGS, changes_dir=CHANGES_DIR, confirmed_dir=CONFIRMED_DIR,
                    local_dir=LOCAL_DIR, report_dir=REPORT_DIR)
    try:
        return etx_flow.prepare(os.getcwd(), settings, dry_run=DRY_RUN)
    except etx_flow.FlowError as exc:
        print(f"[simcompare_job] 오류: {exc.code}: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(2)
'''


def _electrode(side, model):
    """통합 실행기 03 단계와 같은 인수(dv 흐름). condition_assignment 는 real.yaml 에서 읽는다(--config)."""
    return ("import-electrode-assets", [
        "--source-kind", "hotspot_case_electrode",
        "--root", f"{DATA}/input/{model}",
        "--config", CONFIG, "--side", side,
        "--cnt-from-dl1-root", f"{DATA}/input/dl1",
        "--source-version", "LAYOUT_V1",
        "--max-asset-memory-mb", "16384",
        "--out", f"{DATA}/assets/{side}",
    ], f"{side} ({model}). --max-asset-memory-mb는 기본값이 없는 필수 인자이며 16384는 예시다")


def _model_electrode(model):
    return ("import-electrode-assets", [
        "--source-kind", "hotspot_case_electrode",
        "--root", f"{DATA}/input/{model}",
        "--config", "models.yaml", "--model", model,
        "--cnt-from-dl1-root", f"{DATA}/input/dl1",
        "--source-version", "LAYOUT_V1",
        "--max-asset-memory-mb", "16384",
        "--out", f"{DATA}/assets/models/{model}",
    ], f"모델 {model}. --max-asset-memory-mb는 기본값이 없는 필수 인자이며 16384는 예시다. 모델마다 한 번 실행한다")


DV_IMPORT_HEADER = [
    "dv_01회차: hotspot import. 통합 실행기(simcompare_job_etx.py)의 01 단계와 같은 인수다(모델 이름·root 는 이 파일에 적혀 있다).",
    "각 import는 --expected-cases-from-roots로 다른 모델의 root를 함께 주어, 기대 case 목록을 두 모델에서 발견된 case 이름의 합집합으로 만든다",
    "(한쪽에만 없는 case 폴더는 expected_case_folder_missing으로 보고된다. 두 모델 모두에 없는 case는 찾을 수 없다).",
    "--include-cno 는 CNO(lateral_open) case 도 import 한다. CNO 를 쓰지 않으면 두 줄에서 지운다. 켠 실행은 dv_01회차부터 다시 한다.",
    "pairs.csv 는 복사하지 않는다: data/input/pairs.csv 를 real.yaml 의 condition_assignment.pair_table_path 가 가리킨다.",
    "이미 import했고 다시 한다면 먼저 이전 출력 폴더를 옮기거나 이름을 바꾼다",
    "(--out은 없는 경로여야 한다): data/imported/reference·target, data/assets/reference·target, data/dv_template, data/dv_grade, data/changes_dv, data/vulnerability_report."]

DV_ASSETS_HEADER = [
    "dv_02회차: 전극 PNG import. 통합 실행기의 03 단계와 같은 인수다. condition_assignment 는 real.yaml 에서 읽는다(--config real.yaml --side).",
    "출력(data/assets/<side>)은 복사하지 않는다. real.yaml 의 asset_manifest.path·electrode_import_record.path 가 그 폴더를 가리키면 된다",
    "(sha256 은 생략한다. 생략하면 import 기록으로 확인한다)."]

DV_IMPORT_JOBS = [
    ("import-hotspots", ["--root", f"{DATA}/input/QA4_R00", "--expected-cases-from-roots", f"{DATA}/input/QA4_T00",
                         "--include-cno", "--out", f"{DATA}/imported/reference"],
     "reference (QA4_R00). --out은 아직 없는 새 경로"),
    ("import-hotspots", ["--root", f"{DATA}/input/QA4_T00", "--expected-cases-from-roots", f"{DATA}/input/QA4_R00",
                         "--include-cno", "--out", f"{DATA}/imported/target"],
     "target (QA4_T00)")]

DV_ASSETS_JOBS = [_electrode("reference", "QA4_R00"), _electrode("target", "QA4_T00")]

# 다제품 흐름(bundles/etx/). 키 = 파일 이름의 번호·이름. simcompare_job_<키>.py
STAGES = {
    "models_01_import": (
        ["models_01회차(선택, 다제품·모델별 리포트): 모델(제3 제품) hotspot import. 통합 실행기의 01 단계와 같은 명령을 모델마다 한 번 실행한다.",
         "모델 자산은 사용자가 나중에 모델별 폴더로 준다. hotspot 입력은 data/input/<product_id>/, 출력 폴더는 data/models/<product_id>/ 이다.",
         "pairs.csv 는 복사하지 않는다: models.yaml 의 condition_assignment.pair_table_path(data/input/pairs.csv)가 가리킨다. 이 폴더(manifest.json)가 없으면 그 모델은 assets_pending 이다",
         "(models_03회차에서 후보는 보이지만 accept 해도 deferred.json 에 남고 종료 코드 3).",
         "--root는 모델 hotspot root, --expected-cases-from-roots는 target(QA4_T00) root, --out은 아직 없는 data/models/<모델>이다. 모델 id QA4_C00는 예시다.",
         "모델 키(product_id)는 models.yaml(configs/etx/models.example.yaml)의 모델 키와 같아야 한다.",
         "모델이 둘 이상이면 모델마다 JOBS 줄을 늘린다. 통합 실행기(simcompare_job_etx.py)의 reference·target 흐름은 바뀌지 않는다."],
        [("import-hotspots", ["--root", f"{DATA}/input/QA4_C00", "--expected-cases-from-roots", f"{DATA}/input/QA4_T00",
                              "--out", f"{DATA}/models/QA4_C00"],
          "모델 QA4_C00 (예시). --out은 아직 없는 새 경로")]),
    "models_02_import_assets": (
        ["models_02회차(선택, models_01회차 뒤): 모델 전극 PNG import. 통합 실행기 03 단계의 import-electrode-assets와 같은 형식이며 모델마다 한 번 실행한다.",
         "condition_assignment 는 models.yaml 의 해당 모델 절에서 읽는다(--config models.yaml --model <모델>). assignment JSON 은 쓰지 않는다.",
         "--out은 아직 없는 새 경로(data/assets/models/<모델>)다. 출력은 복사하지 않는다: models.yaml 의 그 모델 절에",
         "asset_manifest: {path: data/assets/models/<모델>/review_assets.json}, electrode_import_record: {path: data/assets/models/<모델>/electrode_asset_import_record.json}",
         "을 넣는다(sha256 은 생략하면 import 기록으로 확인한다. 없으면 asset 없이 후보만 본다)."],
        [_model_electrode("QA4_C00")]),
    "models_03_confirm_pairs": (
        ["models_03회차(선택, DL2 경로, 통합 실행기 04 단계 뒤): 다제품 확정. 통합 실행기 05 단계와 같은 confirm-pairs에 --models(models.yaml, real.yaml과 같은 폴더)를 더한다.",
         "모델 후보는 DL2 search_batch.json(psdl2_search_batch_v1)에서 온다. 파일 하나를 두 --match에 같이 줄 수 있다(아래 예시).",
         "모델 쌍은 target 질의(target_to_reference)에서만 생긴다. reference_to_target 후보는 reference 쌍으로만 확정된다.",
         "첫 작업(--list)은 모델 후보를 포함한 candidate_id 목록을 로그에 찍는다. decisions.csv가 없으면 둘째 작업이 종료 코드 2로 멈춘다.",
         "출력 data/confirmed 에는 reference 쌍(roi_correspondence.json)과 모델 쌍(models/<모델>/roi_correspondence.json), models/decisions.json·index.json이 나온다.",
         "자산이 없는 모델(assets_pending)을 accept 하면 deferred.json에 남고 종료 코드 3이다(종료 코드 3은 실패가 아니다).",
         "--labelled-by(확정한 사용자 ID)와 --mutual-tolerance-um은 필수이며 기본값이 없다. 값은 예시다. --out은 아직 없는 새 경로여야 한다(통합 실행기 05 단계의 data/confirmed와 같은 경로를 쓰므로 둘 중 하나만 실행한다)."],
        [("confirm-pairs", ["--list", "--config", CONFIG, "--candidates", f"{DATA}/changes", "--models", "models.yaml",
                            "--match", f"reference_to_target={DATA}/dl2/search_batch.json",
                            "--match", f"target_to_reference={DATA}/dl2/search_batch.json"], "candidate_id 목록만 출력(모델 후보 포함)"),
         ("confirm-pairs", ["--config", CONFIG, "--candidates", f"{DATA}/changes", "--models", "models.yaml",
                            "--match", f"reference_to_target={DATA}/dl2/search_batch.json",
                            "--match", f"target_to_reference={DATA}/dl2/search_batch.json",
                            "--decisions", f"{DATA}/dl2/decisions.csv", "--labelled-by", "CHANGE_ME",
                            "--mutual-tolerance-um", "5", "--out", f"{DATA}/confirmed"],
          "accept 후보만 roi_correspondence.json(reference)과 models/<모델>/roi_correspondence.json으로. --labelled-by CHANGE_ME를 실제 ID로 바꾼다")]),
    "models_04_compare_windows": (
        ["models_04회차(선택, models_03회차 뒤): 모델 쌍의 창별 국부 ΔCA(a = target, b = 모델). 모델마다 한 번 실행한다.",
         "--out은 모델별 폴더(data/local/models/<모델>)다. --correspondence는 models_03회차의 data/confirmed/models/<모델>/roi_correspondence.json이다.",
         "모델 실행은 diag 단계 comparison_masks.zip을 재사용하지 않고 target·모델 두 쪽 면적을 다시 계산한다(통합 실행기 06 단계보다 시간·메모리가 더 든다).",
         "--models는 models.yaml의 모든 configured 모델을 로드한다(--model 하나만 쓰더라도 다른 모델 폴더가 깨져 있으면 영향을 받는다).",
         "--case-window-um·--case-diameter-um(창 20 µm, 지름 5 µm는 예시)을 주면 쌍×case 마다 마스크를 다시 읽어 window_cases.json/csv(관측 hotspot case 구성 차이)를 만든다.",
         "  이 값은 --window-sizes 안의 창, 두 데이터셋에 있는 지름이어야 하고 models_05회차 model-report의 --window-um·--diameter-um 과 같아야 한다.",
         "실규모 실행 시간은 로그의 'compare-windows exit N in Ns' 줄로 확인한다(아직 실측이 없다).",
         "첫 JOBS 줄은 reference 쌍의 창 계산을 DL2 대응 data/confirmed/roi_correspondence.json 으로 data/local 에 둔다(models_05회차 입력). 통합 실행기의 06 단계는",
         "05 를 STEPS 에 넣을 때만 이 대응을 쓰는데 05 는 data/confirmed 를 보관(이동)하므로, models_03회차 뒤에는 통합 실행기 06 대신 이 줄을 쓴다.",
         "이 줄은 data/local 을 한 세대로 교체한다(data/local/models/ 아래 모델 폴더는 그대로 남는다)."],
        [("compare-windows", ["--config", CONFIG, "--correspondence", f"{DATA}/confirmed/roi_correspondence.json",
                              "--window-sizes", "10", "20", "30", "--jitter-um", "0.5", "--sort-diameter-um", "5",
                              "--out", f"{DATA}/local"],
          "reference 쌍(DL2 대응) 창 계산. models_05회차의 reference 열 입력"),
         ("compare-windows", ["--config", CONFIG, "--models", "models.yaml", "--model", "QA4_C00",
                              "--correspondence", f"{DATA}/confirmed/models/QA4_C00/roi_correspondence.json",
                              "--window-sizes", "10", "20", "30", "--jitter-um", "0.5", "--sort-diameter-um", "5",
                              "--case-window-um", "20", "--case-diameter-um", "5",
                              "--out", f"{DATA}/local/models/QA4_C00"],
          "모델 QA4_C00 (예시). --out은 모델별 폴더. 모델이 여럿이면 모델마다 JOBS 줄을 늘린다")]),
    "models_05_report": (
        ["models_05회차(선택, models_04회차 뒤): 모델별 변경사항·불량 변화 한 장(model-report). 행 = target ROI, 열 = 모델(reference 포함).",
         "각 셀은 기존 출력(candidates-change, confirm-pairs --models, compare-windows)에서 복사한 값이며 새로 계산하지 않는다. 모든 인수는 필수이며 기본값이 없다.",
         "변경사항 = reference→target 변경 속성(ROI 열)과 모델 쌍의 case 구성 차이(둘 다/target만/모델만). 불량 변화 = 창 --window-um·지름 --diameter-um 의 union ΔCA(µm²)다.",
         "부호: 원값은 모델 쌍 = 모델−target, reference 쌍 = target−reference다. 통일 열(delta_model_minus_target_um2)은 reference 쌍만 뒤집어 모두 모델−target 으로 맞춘다.",
         "case 구성 차이는 인과를 뜻하지 않는다(관측 목록일 뿐이다).",
         "--windows는 제품마다 하나: 키 QA4_R00 = reference 쌍 폴더(data/local), 키 QA4_C00 = models_04회차 모델 폴더. 모델이 여럿이면 줄을 늘린다.",
         "reference 열이 채워지려면 reference 쌍의 06 단계 실행이 DL2 경로의 대응 파일 data/confirmed/roi_correspondence.json 을 써야 한다",
         "(직접 대응으로 만든 data/local 이면 sha256 이 달라 model_report_correspondence_mismatch 로 멈춘다). --out은 아직 없는 새 경로다."],
        [("model-report", ["--candidates", f"{DATA}/changes", "--confirm", f"{DATA}/confirmed",
                           "--windows", f"QA4_R00={DATA}/local", "--windows", f"QA4_C00={DATA}/local/models/QA4_C00",
                           "--diameter-um", "5", "--window-um", "20", "--out", f"{DATA}/model_report"],
          "예시 — 지름 5 µm, 창 20 µm. 결과: model_report.html/.csv/.json, execution.json")]),
}

# ΔV·위험도 흐름(bundles/etx/dv/). 기본 흐름과 따로 진행하며, 공통 단계(import·asset)는 통합 실행기 01·03 단계와 같은 인수를 복사해 둔다.
DV_NOTE = "(dv 폴더의 이 단계는 통합 실행기(simcompare_job_etx.py) {base} 단계와 같은 JOBS다. 이 폴더 안의 번호는 dv_NN회차로 부른다.)"

DV_STAGES = {
    "01_import_hotspots": ([DV_NOTE.format(base="01")] + DV_IMPORT_HEADER, DV_IMPORT_JOBS),
    "02_import_assets": ([DV_NOTE.format(base="03")] + DV_ASSETS_HEADER, DV_ASSETS_JOBS),
    "03_dv_template": (
        ["dv_03회차(dv 흐름, 통합 실행기와 독립): 제품 case 폴더 이름에서 전극×타이밍 전압 시트의 빈 양식(dv_sheet.csv)을 만든다.",
         "PNG·CSV 내용은 열지 않는다. 전압은 항상 빈칸이고, 임피던스(low/high)는 --impedance-db CSV에 있는 전극만 채워진다.",
         "--timings(구동 타이밍 수 1~20)는 필수이며 기본값이 없다. 아래 값은 예시다. --impedance-db는 처음 제품이면 지운다",
         "(DB가 없으면 모든 전극이 new_electrodes.csv에 나온다). 채운 dv_sheet.csv를 다음 제품의 --impedance-db로 그대로 줄 수 있다.",
         "--out은 아직 없는 새 경로여야 한다. 상세는 docs/dv_template.md."],
        [("dv-template", ["--root", f"{DATA}/input/QA4_R00", "--timings", "4",
                          "--impedance-db", f"{DATA}/electrode_impedance.csv", "--out", f"{DATA}/dv_template/QA4_R00"],
          "reference (QA4_R00). 제품마다 한 번 실행한다. 예시 — 제품에 맞게 바꾼다: 타이밍 4개. electrode_impedance.csv가 아직 없으면 --impedance-db 줄을 지운다"),
         ("dv-template", ["--root", f"{DATA}/input/QA4_T00", "--timings", "4",
                          "--impedance-db", f"{DATA}/electrode_impedance.csv", "--out", f"{DATA}/dv_template/QA4_T00"],
          "target (QA4_T00). 제품마다 한 번 실행한다. 앞 제품에서 채운 dv_sheet.csv를 electrode_impedance.csv로 두면 겹치는 전극의 임피던스가 채워진다")]),
    "04_dv_grade": (
        ["dv_04회차(선택, dv_03회차 뒤): 전압·임피던스를 채운 dv_sheet.csv로 전극쌍별 ΔV, 최대 타이밍, 등급(None/Low/High)을 계산한다.",
         "PNG·CSV 내용은 열지 않고 case 폴더 이름에서 short 쌍을 모은다(open과 같은 이름 쌍은 빼고 manifest에 기록).",
         "--product-id는 필수이며 기본값이 없다. --low-low-threshold-v는 선택이다(없으면 low–low 쌍도 ΔV>0이면 High).",
         "시트 오류는 행 번호와 함께 모두 한 번에 보여 주고 종료 코드 2로 멈춘다(출력 없음). Excel에서는 'CSV UTF-8'로 저장한다.",
         "--out은 아직 없는 새 경로여야 한다. 상세는 docs/dv_grade.md."],
        [("dv-grade", ["--sheet", f"{DATA}/dv_template/QA4_R00/dv_sheet.csv", "--root", f"{DATA}/input/QA4_R00",
                       "--product-id", "QA4_R00", "--low-low-threshold-v", "3", "--out", f"{DATA}/dv_grade/QA4_R00"],
          "reference (QA4_R00). 제품마다 한 번 실행한다. 예시 — 제품에 맞게 바꾼다: 경계 3 V는 예시값. 경계를 쓰지 않으면 --low-low-threshold-v 와 값을 지운다"),
         ("dv-grade", ["--sheet", f"{DATA}/dv_template/QA4_T00/dv_sheet.csv", "--root", f"{DATA}/input/QA4_T00",
                       "--product-id", "QA4_T00", "--low-low-threshold-v", "3", "--out", f"{DATA}/dv_grade/QA4_T00"],
          "target (QA4_T00). 제품마다 한 번 실행한다(dv_05회차는 두 제품의 dv-grade 출력 폴더를 모두 읽는다)")]),
    "05_candidates_dv": (
        ["dv_05회차(선택, dv_04회차와 dv_02회차 뒤): ΔV 등급으로 가중한 취약점 ROI(candidates-dv). 변경점 ROI(통합 실행기 04 단계)와 독립이며 출력 폴더도 따로 쓴다.",
         "두 제품 모두 real_hotspot_v1이어야 하고, dv_04회차의 dv-grade 출력 폴더 둘(제품마다 하나, 서로 다른 폴더)이 필요하다.",
         "모든 인수는 필수이며 기본값이 없다. 아래 값은 예시이니 제품에 맞게 바꾼다: --dv-grades-reference/-target(dv_04회차 출력 폴더;",
         "  manifest의 product_id가 각 제품과 같아야 한다), --min-count(영역 최소 전극 수 K), --roi-grade(high | low_or_high),",
         "--window-um(질의 창 한 변 µm), --merge-overlap(창 병합 기준 (0,1]). --max-candidates(방향별 질의 수 상한)는 선택이라 예시에 넣지 않았다.",
         "real.yaml은 dv_02회차 뒤 상태(asset_manifest, electrode_import_record 가 dv_02 출력 폴더를 가리킴)여야 한다. --out은 아직 없는 새 경로여야 한다.",
         "결과: candidate_queries_v1.json, vulnerability_v1.json, grade_maps/, funnel.json/csv, *_export/·*_index_export/(DL2 입력). 상세는 docs/dv_candidates.md.",
         "종료 코드 3(부분 결과)이어도 실행기는 계속한다. 결과는 로그의 'candidates-dv exit N' 줄로 판정한다."],
        [("candidates-dv", ["--config", CONFIG, "--dv-grades-reference", f"{DATA}/dv_grade/QA4_R00",
                            "--dv-grades-target", f"{DATA}/dv_grade/QA4_T00", "--min-count", "4", "--roi-grade", "high",
                            "--window-um", "20", "--merge-overlap", "0.5", "--out", f"{DATA}/changes_dv"],
          "예시 — 제품에 맞게 바꾼다: K 4, High 쌍만 선정, 창 20 µm, 병합 0.5. 기본값이 아니다. dv_04회차 출력 폴더 이름(QA4_R00, QA4_T00)도 맞춘다")]),
    "06_vulnerability_report": (
        ["dv_06회차(선택, dv_05회차 뒤): 취약점 ROI 수치 근거 HTML(vulnerability-report). dv_05회차 출력 폴더만 읽고 수치를 다시 계산하지 않는다.",
         "--candidates-dv-dir(dv_05회차의 --out 폴더)와 --out은 필수이며 기본값이 없다. --out은 아직 없는 새 경로이고 입력 폴더 밖이어야 한다.",
         "--top-n(표시할 상위 ROI 수)은 선택이며 기본 10이다(표시용). 아래 예시는 기본을 따른다.",
         "dv_05회차 입력 기록의 data_dir(절대 경로)로 전극 layout 자산을 다시 읽으므로 dv_05회차와 같은 서버에서 이어서 실행해야 한다.",
         "자산 해시가 어긋나면 layout 지도만 빠지고 종료 코드 3이다. 결과: report.html, report.json. 상세는 docs/dv_candidates.md."],
        [("vulnerability-report", ["--candidates-dv-dir", f"{DATA}/changes_dv", "--out", f"{DATA}/vulnerability_report"],
          "예시 — dv_05회차 --out과 같은 폴더를 입력으로. --top-n을 바꾸려면 인수를 추가한다")]),
}


def _build_all(out_dir, overwrite):
    results = []
    results.append(build(Path(out_dir) / f"simcompare_job_{FLOW_NAME}.py", overwrite=overwrite, header=FLOW_HEADER,
                         flow=(FLOW_EDIT, FLOW_FUNC)))
    for sub, stages in (("", STAGES), ("dv", DV_STAGES)):
        for stage, (header, jobs) in stages.items():
            out = Path(out_dir) / sub / f"simcompare_job_{stage}.py"
            results.append(build(out, overwrite=overwrite, jobs=jobs, header=header,
                                 log_file=f"runs/simcompare_{stage}.log"))
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=str(ROOT / "bundles/etx"))
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args(argv)
    print(json.dumps(_build_all(a.out_dir, a.overwrite), indent=2))


if __name__ == "__main__":
    main()
