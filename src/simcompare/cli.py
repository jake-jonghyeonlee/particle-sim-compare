"""simcompare 명령행 인터페이스.

numeric_v2 명령과 legacy export 병합만 제공한다.
데이터·설정 계약 오류는 트레이스백 대신 '오류:' 메시지와 종료 코드 2로 끝낸다.
"""
from __future__ import annotations

import argparse
import json
import sys
import yaml
from pathlib import Path


DEMO_NOTICE = "DEMO 모드: 합성/예시 설정이며 결과는 연구 근거가 아닙니다"
RETIRED = {"train": "학습 기능은 폐기되었습니다. DL1/DL2의 별도 학습 경로를 사용하세요.",
           "embed": "임베딩 기능은 폐기되었습니다. DL1/DL2의 별도 검색 경로를 사용하세요.",
           "analyze": "compare 또는 electrode-map을 사용하세요.",
           "evaluate": "평가 기능은 폐기되었습니다. DL1/DL2의 별도 평가 경로를 사용하세요.",
           "all": "validate-data, compare, electrode-map을 각각 실행하세요."}


def _korean_help(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """argparse 파서에 한글 '옵션' 제목과 도움말 옵션(-h/--help)을 붙인다."""
    parser._optionals.title = "옵션"
    parser.add_argument("-h", "--help", action="help", help="도움말을 표시하고 종료")
    return parser


def _parser() -> argparse.ArgumentParser:
    """명령과 인자를 정의한 argparse 파서를 만든다."""
    parser = argparse.ArgumentParser(
        add_help=False,
        prog="simcompare",
        description="이미 계산된 디스플레이 이물 시뮬레이션 결과를 제품 간 비교·해석합니다(CA·hotspot 예측 아님).",
    )
    _korean_help(parser)
    sub = parser.add_subparsers(dest="command", required=True, title="명령")
    demo = sub.add_parser("demo-data", add_help=False, help="합성 demo 데이터 생성(물리 시뮬레이션 결과 아님)")
    _korean_help(demo)
    demo.add_argument("--out", required=True, help="출력 디렉터리")
    demo.add_argument("--numeric-only", action="store_true", help=argparse.SUPPRESS)
    importer = _korean_help(sub.add_parser("import-hotspots", add_help=False, help="실제 hotspot 폴더를 관측 우선 manifest로 변환"))
    importer.add_argument("--root", required=True, help="입력 hotspot root")
    importer.add_argument("--out", required=True, help="새 출력 디렉터리")
    importer.add_argument("--expected-cases", help="누락된 case 폴더 확인용 case 이름 목록")
    importer.add_argument("--expected-cases-from-roots", nargs="+", metavar="ROOT",
                          help="기대 case 목록을 --root와 여기 적은 다른 root에서 발견된 case 이름의 합집합으로 만든다 (--expected-cases와 함께 쓸 수 없음)")
    electrode = _korean_help(sub.add_parser(
        "import-electrode-assets", add_help=False,
        help="실제 전극 PNG(DL1 폴더 또는 hotspot case의 1.png/2.png)를 review_assets_v1 manifest와 asset으로 변환"))
    electrode.add_argument("--source-kind", required=True,
                           help="출처 종류: dl1_png_folder 또는 hotspot_case_electrode (극성 계약 표에 있어야 함)")
    electrode.add_argument("--root", required=True, help="입력 전극 PNG root")
    electrode.add_argument("--numeric-data-dir", required=True,
                           help="case·frame·valid·input_hash를 가져올 numeric_v2(합성) 또는 real_hotspot_v1 폴더")
    electrode.add_argument("--manifest-path", default="manifest.json", help="numeric-data-dir 안의 manifest (기본 manifest.json)")
    electrode.add_argument("--condition-assignment", help="real_hotspot_v1일 때 명시 조건 할당 JSON")
    electrode.add_argument("--stack-order", help="dl1_png_stack_order_v1 JSON (layer 순서는 이 파일에서만 옴)")
    electrode.add_argument("--cnt-from-dl1-root", help="hotspot_case_electrode의 CNT를 가져올 DL1 PNG root (없으면 CNT 없는 case는 blocked)")
    electrode.add_argument("--source-version", required=True, help="원본 layout 버전 문자열 (기본값 없음)")
    electrode.add_argument("--max-asset-memory-mb", required=True, type=float,
                           help="asset 디코딩 메모리 추정 상한(MB, 기본값 없음). 초과하면 아무것도 쓰지 않고 종료 코드 2")
    electrode.add_argument("--out", required=True, help="새 출력 디렉터리(이미 있으면 거부)")
    for name in ("compare", "electrode-map", "export-review-rois"):
        command = _korean_help(sub.add_parser(name, add_help=False, help="numeric_v2 수치/검토 위치 명령(학습 호출 없음)"))
        command.add_argument("--config", required=True, help="numeric_config_v1 YAML")
        command.add_argument("--out", required=name == "export-review-rois", help="출력 디렉터리")
        if name == "electrode-map":
            command.add_argument("--per-cell-text", action="store_true", help="4096 cells 이하에서만 셀별 텍스트 출력")
        if name in ("electrode-map", "export-review-rois"):
            command.add_argument("--min-count", type=int, help="명시할 때만 4-neighbour 영역 계산")
    template = _korean_help(sub.add_parser(
        "export-template-rois", add_help=False,
        help="한 제품의 template ROI와 mask_derivation을 hotspot·CA를 열지 않고 내보냄(1.png/2.png만 있는 트리)"))
    template.add_argument("--config", required=True, help="template_export_config_v1 YAML")
    template.add_argument("--out", required=True, help="새 출력 디렉터리(이미 있으면 거부)")
    cand = _korean_help(sub.add_parser(
        "candidates", add_help=False,
        help="두 제품의 취약 영역(양방향) → 병합된 질의 ROI와 바로 매칭할 export, 깔때기 표(점수·CA·확정 없음)"))
    cand.add_argument("--config", required=True, help="numeric_config_v1 YAML (reference·target 각각 단일 제품)")
    cand.add_argument("--min-count", required=True, type=int, help="영역 최소 전극 수 k (기본값 없음)")
    cand.add_argument("--window-um", required=True, type=float, help="질의 창 한 변(µm). 병합 판정에만 쓰임 (기본값 없음)")
    cand.add_argument("--merge-overlap", required=True, type=float,
                      help="두 창의 intersection/min(area) 병합 기준 (0,1] (기본값 없음)")
    cand.add_argument("--max-candidates", type=int,
                      help="방향(제품)별 질의 수 상한. 명시할 때만 적용하며 잘린 수를 funnel에 기록")
    cand.add_argument("--out", required=True, help="새 출력 디렉터리(이미 있으면 거부)")
    change = _korean_help(sub.add_parser(
        "candidates-change", add_help=False,
        help="두 제품(real_hotspot_v1)의 CNT 정렬 + 5 µm hotspot 변경 지도 → 변경 위치 질의 ROI와 DL2 export(모든 인수 필수, 기본값 없음)"))
    change.add_argument("--config", required=True, help="numeric_config_v1 YAML (reference·target 각각 real_hotspot_v1 단일 제품·단일 frame)")
    change.add_argument("--cnt-from-dl1-root", required=True, help="두 제품의 cno/ CNT 층 PNG가 있는 DL1 PNG root (제품 폴더 = product_id)")
    change.add_argument("--electrode-png-root", help="선택. alignment.html 층 묶음의 전극 형상 바탕을 읽을 PNG root (<root>/<product_id>/<전극>/<층>.png, DL1 과 같은 구조). 생략하면 --cnt-from-dl1-root. 전극 목록 비교에는 쓰지 않는다")
    change.add_argument("--align-search-um", required=True, type=float, help="CNT 정렬 탐색 반경 µm. 최소값은 pixel pitch의 2배 이상(코드 검사). 권장값은 예상 레이아웃 이동과 반복 패턴 주기를 모두 덮는 값(예시 30 µm). 주기가 탐색 반경 안에 있으면 peak_ambiguous 와 대안 offset 으로 표시된다")
    change.add_argument("--align-min-dice", required=True, type=float, help="정렬 최소 Dice [0, 1]. 미만이면 aligned_uncertain")
    change.add_argument("--align-layer-tolerance-um", required=True, type=float, help="층별·4분면 offset 이 전역 offset 과 달라도 되는 거리 µm")
    change.add_argument("--change-tolerance-um", required=True, type=float, help="변경 판정 허용 이동 거리 µm (이 안의 이동은 변경으로 세지 않음)")
    change.add_argument("--window-um", required=True, type=float, help="변경 창 한 변(µm)")
    change.add_argument("--merge-overlap", required=True, type=float, help="두 창의 intersection/min(area) 병합 기준 (0,1]")
    change.add_argument("--merge-scope", choices=("layer", "all"),
                        help="선택. 창 병합 범위: layer(생략 시, 층마다 병합) 또는 all(층을 넘어 병합)")
    change.add_argument("--suppress-overlap", type=float,
                        help="선택. (0,1]. 병합 뒤 순위 순서로 앞선 대표 창과 겹침 비율이 이 값 이상이면 버린다(층 구분 없음). 생략하면 억제 없음")
    change.add_argument("--top-k", required=True, type=int, help="방향(reference_to_target / target_to_reference)별 질의 수")
    change.add_argument("--mask-derivation-rule", required=True,
                        help="DL2 mask_derivation 규칙: between_stack_order_v1 만 허용(기본값 없음). 질의·index export 폴더마다 mask_derivation.json 을 쓴다")
    change.add_argument("--out", required=True, help="새 출력 디렉터리(이미 있으면 거부)")
    cdv = _korean_help(sub.add_parser(
        "candidates-dv", add_help=False,
        help="두 제품(real_hotspot_v1)의 ΔV 등급(dv-grade 출력)으로 가중한 취약점 영역 → 질의 ROI와 DL2 export, 비가중 순위와의 비교(모든 인수 필수, 기본값 없음)"))
    cdv.add_argument("--config", required=True, help="numeric_config_v1 YAML (reference·target 각각 real_hotspot_v1 단일 제품)")
    cdv.add_argument("--dv-grades-reference", required=True, help="reference 제품의 dv-grade 출력 폴더 (manifest product_id 가 reference 제품과 같아야 함)")
    cdv.add_argument("--dv-grades-target", required=True, help="target 제품의 dv-grade 출력 폴더 (reference 와 다른 폴더)")
    cdv.add_argument("--min-count", required=True, type=int, help="영역 최소 전극 수 K (선정 지도 기준, 기본값 없음)")
    cdv.add_argument("--roi-grade", required=True, choices=("high", "low_or_high"),
                     help="영역 선정 등급: high 는 High 쌍만, low_or_high 는 Low·High 쌍으로 센 전극 수 (기본값 없음)")
    cdv.add_argument("--window-um", required=True, type=float, help="질의 창 한 변(µm). 병합 판정과 창 포함 비율에 쓰임 (기본값 없음)")
    cdv.add_argument("--merge-overlap", required=True, type=float, help="두 창의 intersection/min(area) 병합 기준 (0,1] (기본값 없음)")
    cdv.add_argument("--max-candidates", type=int, help="방향(제품)별 질의 수 상한. 명시할 때만 적용하며 잘린 수를 funnel에 기록")
    cdv.add_argument("--out", required=True, help="새 출력 디렉터리(이미 있으면 거부)")
    vrep = _korean_help(sub.add_parser(
        "vulnerability-report", add_help=False,
        help="candidates-dv 출력 폴더로 취약점 ROI 수치 근거 HTML(등급 순위 vs 비가중 순위, ROI 별 쌍 표와 전극 layout 지도)을 만듦. 수치는 다시 계산하지 않음"))
    vrep.add_argument("--candidates-dv-dir", required=True, help="candidates-dv 출력 폴더 (candidate_queries_v1.json, vulnerability_v1.json, grade_maps/)")
    vrep.add_argument("--out", required=True, help="새 출력 디렉터리(이미 있거나 입력 폴더 안이면 거부)")
    vrep.add_argument("--top-n", type=int, default=10, help="표시할 상위 ROI 수(표시용, 기본 10)")
    dvt = _korean_help(sub.add_parser(
        "dv-template", add_help=False,
        help="제품 case 폴더 이름에서 전극×타이밍 전압 시트의 빈 양식을 만듦(PNG·CSV 내용은 열지 않음, 전압·임피던스 기본값 없음)"))
    dvt.add_argument("--root", required=True, help="제품 hotspot 또는 template case root")
    dvt.add_argument("--timings", required=True, type=int, help="구동 타이밍 수 N (1–20, 기본값 없음)")
    dvt.add_argument("--impedance-db", help="전극별 임피던스 DB CSV (electrode_id, impedance=low|high|빈칸; 다른 열은 무시)")
    dvt.add_argument("--out", required=True, help="새 출력 디렉터리(이미 있으면 거부)")
    dvg = _korean_help(sub.add_parser(
        "dv-grade", add_help=False,
        help="채운 전압 시트와 case 폴더 이름으로 short 쌍별 ΔV·최대 타이밍·등급(None/Low/High)을 계산(PNG·CSV 내용은 열지 않음, 경계 기본값 없음)"))
    dvg.add_argument("--sheet", required=True, help="사용자가 채운 dv_sheet.csv (UTF-8)")
    dvg.add_argument("--root", required=True, help="제품 hotspot 또는 template case root")
    dvg.add_argument("--product-id", required=True, help="제품 식별자(기본값 없음, 빈 문자열 금지). manifest 에 기록")
    dvg.add_argument("--low-low-threshold-v", help="low–low 쌍의 High 경계 V (0 보다 큰 일반 소수). 없으면 ΔV>0 인 모든 쌍이 High")
    dvg.add_argument("--out", required=True, help="새 출력 디렉터리(이미 있으면 거부)")
    confirm = _korean_help(sub.add_parser(
        "confirm-pairs", add_help=False,
        help="사용자가 decisions.csv 로 accept 한 NCC/DL2 후보만 roi_correspondence_v1 로 변환(점수는 사이드카에만)"))
    confirm.add_argument("--config", required=True, help="numeric_config_v1 YAML (reference=a, target=b)")
    confirm.add_argument("--candidates", required=True, help="candidates 출력 폴더")
    confirm.add_argument("--match", action="append", default=[], metavar="DIRECTION=JSON",
                         help="reference_to_target=<ncc.json> 또는 target_to_reference=<ncc.json> (각각 선택, 하나 이상)")
    confirm.add_argument("--list", action="store_true", help="candidate_id 목록(<direction>:<query_roi_id>:r<rank>)만 출력하고 종료")
    confirm.add_argument("--decisions", help="decisions.csv (candidate_id,decision,note; decision=accept|reject, UTF-8, 헤더 필수)")
    confirm.add_argument("--labelled-by", help="확정한 사용자 ID (기본값 없음)")
    confirm.add_argument("--mutual-tolerance-um", type=float, help="mutual/one_way 판정 중심 허용 거리 µm (기본값 없음)")
    confirm.add_argument("--models", help="compare_models_v1 models.yaml(--config 와 같은 폴더). 있으면 모델 후보를 models/<product_id>/ 아래 별도 대응 파일로 낸다")
    confirm.add_argument("--out", help="새 출력 디렉터리(이미 있으면 거부)")
    windows = _korean_help(sub.add_parser("compare-windows", add_help=False,
                                          help="대응 위치의 같은 물리 창 안 ΔCA(b−a). 창 크기는 기본값 없음"))
    windows.add_argument("--config", required=True, help="numeric_config_v1 YAML (reference=a, target=b)")
    windows.add_argument("--correspondence", required=True, help="roi_correspondence_v1 JSON")
    windows.add_argument("--window-sizes", required=True, nargs="+", type=float, metavar="UM",
                         help="창 한 변(µm) 목록. 예: --window-sizes 10 20 30")
    windows.add_argument("--jitter-um", type=float, help="명시할 때만 중심을 ±j 이동한 네 창의 Δ 범위를 기록")
    windows.add_argument("--sort-diameter-um", type=float, help="명시할 때만 이 지름의 |Δ|로 쌍을 정렬")
    windows.add_argument("--compact-json", action="store_true",
                         help="JSON에서 행별 세부 지표를 빼고 CSV에만 남김(수십 쌍 × 여러 창일 때 용량 절약)")
    windows.add_argument("--models", help="compare_models_v1 models.yaml(--config 와 같은 폴더). --model 과 함께 쓴다")
    windows.add_argument("--model", help="models.yaml 의 모델 키. a = config target, b = 이 모델로 비교한다(--out 필수)")
    windows.add_argument("--case-window-um", type=float, metavar="UM",
                         help="--model 과 함께: 이 창(--window-sizes 안의 값)에서 모델 쌍의 관측 hotspot case 구성 차이를 window_cases.json/csv 로 낸다(--case-diameter-um 과 함께, 기본값 없음)")
    windows.add_argument("--case-diameter-um", type=float, metavar="UM",
                         help="window_cases 를 낼 지름 µm(두 데이터셋에 있는 값, --case-window-um 과 함께)")
    windows.add_argument("--out", help="출력 디렉터리")
    merge = _korean_help(sub.add_parser(
        "merge-exports", add_help=False,
        help="particle-sim export-simcompare 결과 여러 개(원안·변경본·다른 제품)를 data_dir 하나로 합침"))
    merge.add_argument("--out", required=True, help="비어 있는 출력 디렉터리")
    merge.add_argument("sources", nargs="+", help="export-simcompare 출력 디렉터리들")
    report = _korean_help(sub.add_parser("report", add_help=False,
                                         help="compare / electrode-map / compare-windows / export-review-rois 출력을 읽어 HTML 보고서 한 파일 생성"))
    report.add_argument("--compare-dir", required=True, help="compare 출력 폴더(필수)")
    report.add_argument("--map-dir", help="electrode-map 출력 폴더. 없으면 해당 섹션에 '입력 없음'")
    report.add_argument("--windows-dir", help="compare-windows 출력 폴더. 없으면 해당 섹션에 '입력 없음'")
    report.add_argument("--rois-dir", help="export-review-rois 출력 폴더. 없으면 해당 섹션에 '입력 없음'")
    report.add_argument("--candidates-dir", help="candidates 출력 폴더. 있으면 'ROI 두 축 보기'에 후보 깔때기와 취약 max_count를 표시")
    report.add_argument("--confirm-dir", help="confirm-pairs 출력 폴더(--windows-dir 필요). 있으면 쌍 출처·확정 깔때기를 표시하고 대응 파일 sha256을 대조")
    report.add_argument("--out", required=True, help="출력 디렉터리(입력 폴더와 달라야 함)")
    report.add_argument("--diameter-um", type=float, default=5.0, help="대표 지름 µm (기본 5)")
    report.add_argument("--rank-window-um", type=float, default=20.0,
                        help="악화/개선 순위 기준 창 µm (기본 20, 임시 기준값. local_delta.json의 창 크기 중 하나여야 함)")
    report.add_argument("--top-n", type=int, default=10, help="악화/개선 각각 표시할 쌍 수 (기본 10)")
    validate = _korean_help(sub.add_parser("validate-data", add_help=False, help="numeric_v2 계약·누락·단위 점검"))
    validate.add_argument("--config", required=True, help="numeric_config_v1 YAML")
    validate.add_argument("--out", help="출력 디렉터리")
    return parser


def _print(payload) -> None:
    """결과 dict를 한글이 깨지지 않는 JSON으로 표준 출력에 쓴다."""
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def _remove_reports(folder, command=None):
    """Only this application's report files, never inputs/user assets."""
    if command == "report":
        # report는 자기 출력 이름만 지운다. report* 같은 접두 패턴은 사용자 파일(report_notes.json 등)까지 지운다.
        for name in ("report.html", "report.json"):
            (Path(folder)/name).unlink(missing_ok=True)
        return
    from simcompare.publication import LOCAL_DELTA_FILES
    # 공개 트랜잭션이 남긴 제어 파일(.comparison.* / .local_delta.*)이 있으면 그 세대는 복구 대상이므로 지우지 않는다
    # (지우면 다음 begin()이 "neither the file nor its backup exists"로 막힌다).
    in_flight = {stem: any((Path(folder)/f'.{stem}.{kind}').exists() for kind in ('publishing', 'committed'))
                 for stem in ('comparison', 'local_delta')}
    for prefix in ('comparison','electrode_map','validation','review_rois','local_delta','window_sensitivity'):
        for path in Path(folder).glob(prefix+'*'):
            if not path.is_file():
                continue
            if path.suffix in ('.json','.csv','.html','.png','.npz') or path.name == 'comparison_masks.zip':
                managed = LOCAL_DELTA_FILES.fullmatch(path.name) and 'local_delta' or (
                    path.name in ('comparison.json', 'comparison.csv', 'comparison_masks.zip') and 'comparison')
                if managed and in_flight[managed]:
                    continue
                path.unlink()


def main(argv: list[str] | None = None) -> int:
    """완료 0, 부분/blocked 결과 3, 입력 불가 또는 폐기 명령 2."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in RETIRED:
        print(f"오류: {arguments[0]} 명령은 폐기되었습니다. {RETIRED[arguments[0]]}", file=sys.stderr)
        return 2
    parser = _parser()
    args = parser.parse_args(arguments)
    try:
        if args.command == "demo-data":
            from simcompare.synthetic import generate_numeric_demo
            print(DEMO_NOTICE, file=sys.stderr)
            _print({"numeric_v2": generate_numeric_demo(args.out)})
            return 0
        if args.command == "merge-exports":
            from simcompare.merge import merge_exports

            _print(merge_exports([Path(s) for s in args.sources], Path(args.out)))
            return 0
        if args.command == "import-hotspots":
            from simcompare.adapters.real_hotspot import import_hotspots
            if args.expected_cases and args.expected_cases_from_roots:
                parser.error("--expected-cases와 --expected-cases-from-roots는 함께 쓸 수 없습니다")
            # 원문 문자열 그대로 넘긴다: 경로가 없을 때 오류 메시지가 사용자가 적은 문자열을 보여 준다.
            result = import_hotspots(args.root, Path(args.out),
                                     expected_cases=args.expected_cases if args.expected_cases else None,
                                     expected_case_roots=(list(args.expected_cases_from_roots)
                                                          if args.expected_cases_from_roots is not None else None))
            _print(result)
            return 0 if result['status'] == 'available' else 3
        if args.command == "import-electrode-assets":
            from simcompare.adapters.electrode_png import import_electrode_assets
            result = import_electrode_assets(
                args.source_kind, Path(args.root), Path(args.out), numeric_data_dir=Path(args.numeric_data_dir),
                manifest_path=args.manifest_path, condition_assignment=args.condition_assignment,
                stack_order=args.stack_order, cnt_from_dl1_root=args.cnt_from_dl1_root,
                source_version=args.source_version, max_asset_memory_mb=args.max_asset_memory_mb)
            _print(result)
            return result["exit_code"]
        if args.command == "export-template-rois":
            # 출력 폴더는 새 것이어야 하며 run_template_export가 이 실행이 만든 것만 정리한다(공용 오류 핸들러 제외).
            from simcompare import template_export
            result = template_export.run_template_export(args.config, Path(args.out))
            _print(result)
            return result["exit_code"]
        if args.command == "candidates":
            # 출력 폴더는 새 것이어야 한다. run_candidates가 이 실행이 만든 파일만 정리하므로
            # 공용 오류 핸들러(error.json 기록·_remove_reports)를 거치지 않는다.
            from simcompare import candidates, pipeline
            cfg = pipeline.load_numeric_config(args.config)
            result = candidates.run_candidates(cfg, Path(args.out), min_count=args.min_count, window_um=args.window_um,
                                               merge_overlap=args.merge_overlap, max_candidates=args.max_candidates)
            _print(result)
            return result["exit_code"]
        if args.command == "candidates-change":
            # candidates 와 같다: 출력 폴더는 새 것이어야 하고 run_candidates_change 가 이 실행이 만든 파일만 정리한다.
            from simcompare import change_candidates, pipeline
            cfg = pipeline.load_numeric_config(args.config)
            result = change_candidates.run_candidates_change(
                cfg, Path(args.out), cnt_from_dl1_root=args.cnt_from_dl1_root, align_search_um=args.align_search_um,
                align_min_dice=args.align_min_dice, align_layer_tolerance_um=args.align_layer_tolerance_um,
                change_tolerance_um=args.change_tolerance_um, window_um=args.window_um,
                merge_overlap=args.merge_overlap, top_k=args.top_k, mask_derivation_rule=args.mask_derivation_rule,
                merge_scope=args.merge_scope, suppress_overlap=args.suppress_overlap,
                electrode_png_root=args.electrode_png_root)
            _print(result)
            return result["exit_code"]
        if args.command == "candidates-dv":
            # candidates 와 같다: 출력 폴더는 새 것이어야 하고 run_candidates_dv 가 이 실행이 만든 파일만 정리한다.
            from simcompare import dv_candidates, pipeline
            cfg = pipeline.load_numeric_config(args.config)
            result = dv_candidates.run_candidates_dv(
                cfg, Path(args.out), dv_grades_reference=args.dv_grades_reference, dv_grades_target=args.dv_grades_target,
                min_count=args.min_count, roi_grade=args.roi_grade, window_um=args.window_um, merge_overlap=args.merge_overlap,
                max_candidates=args.max_candidates)
            _print(result)
            return result["exit_code"]
        if args.command == "vulnerability-report":
            # 출력 폴더는 새 것이어야 하고 staging 후 rename 하므로 실패 시 출력이 남지 않는다(공용 오류 핸들러 제외).
            from simcompare import dv_report
            result = dv_report.run_vulnerability_report(args.candidates_dv_dir, Path(args.out), top_n=args.top_n)
            _print(result)
            return result["exit_code"]
        if args.command == "dv-template":
            # 출력 폴더는 새 것이어야 하고 staging 후 rename 하므로 실패 시 출력이 남지 않는다(공용 오류 핸들러 제외).
            from simcompare import dv_template
            result = dv_template.run_dv_template(args.root, Path(args.out), args.timings, args.impedance_db)
            print("\n".join(dv_template.summary_lines(result)))
            return 0
        if args.command == "dv-grade":
            # dv-template 과 같다: staging 후 rename 하므로 실패 시 출력이 남지 않는다(공용 오류 핸들러 제외).
            from simcompare import dv_grade
            result = dv_grade.run_dv_grade(args.sheet, args.root, Path(args.out), args.product_id, args.low_low_threshold_v)
            print("\n".join(dv_grade.summary_lines(result)))
            return 0
        if args.command == "confirm-pairs":
            # 출력 폴더는 새 것이어야 하며 run_confirm_pairs 가 이 실행이 만든 파일만 정리한다(공용 오류 핸들러 제외).
            from simcompare import confirm_pairs, pipeline
            from simcompare.numeric_schema import NumericContractError
            matches = {}
            for spec in args.match:
                name, separator, path = spec.partition("=")
                if not separator or not path or name in matches:
                    raise NumericContractError("invalid_match_argument", f"--match {spec!r}: DIRECTION=JSON 형식, 방향당 하나")
                matches[name] = path
            cfg = pipeline.load_numeric_config(args.config)
            if args.list:
                _print(confirm_pairs.list_candidates(cfg, args.candidates, matches, models_path=args.models))
                return 0
            for flag, value in (("--decisions", args.decisions), ("--labelled-by", args.labelled_by),
                                ("--mutual-tolerance-um", args.mutual_tolerance_um), ("--out", args.out)):
                if value is None:
                    raise NumericContractError("confirm_pairs_argument_required", flag + " 가 필요합니다(기본값 없음)")
            result = confirm_pairs.run_confirm_pairs(cfg, Path(args.out), candidates_dir=args.candidates, matches=matches,
                                                     decisions_path=args.decisions, labelled_by=args.labelled_by,
                                                     mutual_tolerance_um=args.mutual_tolerance_um, models_path=args.models)
            _print(result)
            return result["exit_code"]
        if args.command == "report":
            from simcompare import final_report
            result = final_report.run_report(args.compare_dir, args.out, map_dir=args.map_dir, windows_dir=args.windows_dir,
                                             rois_dir=args.rois_dir, diameter_um=args.diameter_um,
                                             rank_window_um=args.rank_window_um, top_n=args.top_n,
                                             candidates_dir=args.candidates_dir, confirm_dir=args.confirm_dir)
            output = Path(args.out)
            (output/"execution.json").write_text(json.dumps({"command":args.command,**result},ensure_ascii=False,indent=2),encoding="utf-8")
            (output/"error.json").unlink(missing_ok=True)
            _print(result)
            return result["exit_code"]
        from simcompare import pipeline
        model_run = args.command == "compare-windows" and (args.models is not None or args.model is not None)
        derived, model_provenance = None, None
        case_run = args.command == "compare-windows" and (args.case_window_um is not None or args.case_diameter_um is not None)
        if case_run:
            # case 인수 검사도 어떤 파일도 쓰기 전에 끝난다(오류 stderr + 종료 2).
            from simcompare import window_cases
            from simcompare.numeric_schema import NumericContractError
            try:
                if args.model is None:
                    raise NumericContractError("case_args_require_model", "--case-window-um/--case-diameter-um 은 --model 과 함께만 쓸 수 있습니다")
                if args.case_window_um is None or args.case_diameter_um is None:
                    raise NumericContractError("case_args_incomplete", "--case-window-um 과 --case-diameter-um 은 함께 지정해야 합니다")
                window_cases.check_window(args.case_window_um, args.window_sizes)
            except NumericContractError as exc:
                print(f"오류: {exc}", file=sys.stderr)
                return 2
        if model_run:
            # --model 실행의 인수·models·충돌 검사는 어떤 파일도 쓰기 전에 끝난다: 실패하면 stderr 오류와 종료 2 뿐이다(공용 오류 처리기 제외).
            from simcompare import models as models_module
            from simcompare.numeric_schema import NumericContractError
            try:
                if args.models is None or args.model is None:
                    raise NumericContractError("model_arguments_paired", "--models 와 --model 은 함께 지정해야 합니다")
                cfg = pipeline.load_numeric_config(args.config)
                derived, model_provenance = models_module.prepare_model_run(cfg, args.models, args.model, args.out)
            except (NumericContractError, OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
                print(f"오류: {exc}", file=sys.stderr)
                return 2
        else:
            cfg = pipeline.load_numeric_config(args.config)
        if getattr(args, "min_count", None) is not None:
            cfg.setdefault("map", {})["min_count"] = args.min_count
        if getattr(args,"per_cell_text",False):
            cfg.setdefault("map",{})["per_cell_text"]=True
        if args.command == "compare-windows":
            result = pipeline.run_numeric_local_delta(derived if model_run else cfg, args.out, correspondence=args.correspondence,
                                                      window_sizes_um=args.window_sizes, jitter_um=args.jitter_um,
                                                      sort_diameter_um=args.sort_diameter_um,
                                                      compact_json=args.compact_json, config_path=args.config,
                                                      **({"model": model_provenance} if model_run else {}),
                                                      **({"case_window_um": args.case_window_um, "case_diameter_um": args.case_diameter_um} if case_run else {}))
        else:
            runners = {"compare":pipeline.run_numeric_compare,"electrode-map":pipeline.run_numeric_map,
                       "validate-data":pipeline.run_numeric_validate,"export-review-rois":pipeline.run_numeric_roi_export}
            result = runners[args.command](cfg,args.out)
        output = Path(args.out) if args.out is not None else pipeline._numeric_path(cfg,cfg["output_dir"])
        (output/"execution.json").write_text(json.dumps({"command":args.command,**result},ensure_ascii=False,indent=2),encoding="utf-8")
        (output/"error.json").unlink(missing_ok=True)
        _print(result)
        return result["exit_code"]
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        if getattr(exc, "writes_nothing", False):
            # 쓰기 전에 거부한 인수 오류(case 지름 등): 출력 폴더·error.json 을 만들지 않는다.
            print(f"오류: {exc}", file=sys.stderr)
            return 2
        if args.command in ("import-electrode-assets", "candidates", "candidates-change", "candidates-dv", "vulnerability-report", "confirm-pairs", "dv-template", "dv-grade"):
            # 출력 디렉터리는 새 것이어야 하므로 실패 marker나 정리 없이 오류만 알린다.
            print(f"오류: {exc}", file=sys.stderr)
            return 2
        if args.command == "import-hotspots":
            from simcompare.adapters.real_hotspot import InputPathMissingError
            if isinstance(exc, InputPathMissingError):
                # 입력 경로를 고치고 같은 --out 으로 다시 실행할 수 있게 --out 폴더·error.json·execution.json 을 만들지 않는다.
                print(f"오류: {exc}", file=sys.stderr)
                return 2
        # numeric 계약 오류에는 완성 리포트 대신 실패 마커만 쓴다.
        if args.command in ("compare", "electrode-map", "export-review-rois", "validate-data", "import-hotspots", "compare-windows", "report"):
            error = {"status":"unavailable","reasons":[getattr(exc,"code","input_or_config_error")],
                     "detail":str(exc),"command":args.command,"outputs":[],"exit_code":2}
            output = getattr(args,"out",None)
            if output is None and isinstance(locals().get("cfg"),dict):
                try:
                    output = pipeline._numeric_path(cfg,cfg["output_dir"])
                except (ValueError,TypeError,KeyError):
                    output = None
            if args.command == "report" and any(
                    folder is not None and Path(folder).resolve() == Path(output).resolve()
                    for folder in (args.compare_dir, args.map_dir, args.windows_dir, args.rois_dir, args.candidates_dir, args.confirm_dir)):
                output = None  # --out이 입력 폴더면 입력 파일을 지우지 않는다
            if output is not None:
                try:
                    folder = Path(output)
                    folder.mkdir(parents=True,exist_ok=True)
                    # compare-windows: every error keeps the previous good generation. compare (real): errors before any output
                    # is written and inside the publication transaction keep it; the two non-transactional real writers
                    # (inspection without condition_assignment, numeric_v2 report) set keeps_previous_outputs=False (F4).
                    if not (getattr(exc, "keeps_previous_outputs", False) or args.command == "compare-windows"):
                        _remove_reports(folder,args.command)
                    encoded_error = json.dumps(error,ensure_ascii=False,indent=2)
                    (folder/"error.json").write_text(encoded_error,encoding="utf-8")
                    (folder/"execution.json").write_text(encoded_error,encoding="utf-8")
                except OSError:
                    pass
        print(f"오류: {exc}", file=sys.stderr)
        return 2
