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
    for name in ("compare", "electrode-map", "export-review-rois"):
        command = _korean_help(sub.add_parser(name, add_help=False, help="numeric_v2 수치/검토 위치 명령(학습 호출 없음)"))
        command.add_argument("--config", required=True, help="numeric_config_v1 YAML")
        command.add_argument("--out", required=name == "export-review-rois", help="출력 디렉터리")
        if name == "electrode-map":
            command.add_argument("--per-cell-text", action="store_true", help="4096 cells 이하에서만 셀별 텍스트 출력")
        if name in ("electrode-map", "export-review-rois"):
            command.add_argument("--min-count", type=int, help="명시할 때만 4-neighbour 영역 계산")
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


def _remove_reports(folder):
    """Only this application's report files, never inputs/user assets."""
    for prefix in ('comparison','electrode_map','validation','review_rois','local_delta','window_sensitivity','report'):
        for path in Path(folder).glob(prefix+'*'):
            if path.is_file() and path.suffix in ('.json','.csv','.html','.png','.npz'):
                path.unlink()


def main(argv: list[str] | None = None) -> int:
    """완료 0, 부분/blocked 결과 3, 입력 불가 또는 폐기 명령 2."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in RETIRED:
        print(f"오류: {arguments[0]} 명령은 폐기되었습니다. {RETIRED[arguments[0]]}", file=sys.stderr)
        return 2
    args = _parser().parse_args(arguments)
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
            result = import_hotspots(Path(args.root), Path(args.out),
                                     expected_cases=Path(args.expected_cases) if args.expected_cases else None)
            _print(result)
            return 0 if result['status'] == 'available' else 3
        if args.command == "report":
            from simcompare import final_report
            result = final_report.run_report(args.compare_dir, args.out, map_dir=args.map_dir, windows_dir=args.windows_dir,
                                             rois_dir=args.rois_dir, diameter_um=args.diameter_um,
                                             rank_window_um=args.rank_window_um, top_n=args.top_n)
            output = Path(args.out)
            (output/"execution.json").write_text(json.dumps({"command":args.command,**result},ensure_ascii=False,indent=2),encoding="utf-8")
            (output/"error.json").unlink(missing_ok=True)
            _print(result)
            return result["exit_code"]
        from simcompare import pipeline
        cfg = pipeline.load_numeric_config(args.config)
        if getattr(args, "min_count", None) is not None:
            cfg.setdefault("map", {})["min_count"] = args.min_count
        if getattr(args,"per_cell_text",False):
            cfg.setdefault("map",{})["per_cell_text"]=True
        if args.command == "compare-windows":
            result = pipeline.run_numeric_local_delta(cfg, args.out, correspondence=args.correspondence,
                                                      window_sizes_um=args.window_sizes, jitter_um=args.jitter_um,
                                                      sort_diameter_um=args.sort_diameter_um,
                                                      compact_json=args.compact_json, config_path=args.config)
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
        # numeric 계약 오류에는 완성 리포트 대신 실패 marker만 쓴다.
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
                    for folder in (args.compare_dir, args.map_dir, args.windows_dir, args.rois_dir)):
                output = None  # --out이 입력 폴더면 입력 파일을 지우지 않는다
            if output is not None:
                try:
                    folder = Path(output)
                    folder.mkdir(parents=True,exist_ok=True)
                    _remove_reports(folder)
                    encoded_error = json.dumps(error,ensure_ascii=False,indent=2)
                    (folder/"error.json").write_text(encoded_error,encoding="utf-8")
                    (folder/"execution.json").write_text(encoded_error,encoding="utf-8")
                except OSError:
                    pass
        print(f"오류: {exc}", file=sys.stderr)
        return 2
