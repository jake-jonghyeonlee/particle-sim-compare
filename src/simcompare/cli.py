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
    for name in ("compare", "electrode-map", "export-review-rois"):
        command = _korean_help(sub.add_parser(name, add_help=False, help="numeric_v2 수치/검토 위치 명령(학습 호출 없음)"))
        command.add_argument("--config", required=True, help="numeric_config_v1 YAML")
        command.add_argument("--out", required=name == "export-review-rois", help="출력 디렉터리")
        if name in ("electrode-map", "export-review-rois"):
            command.add_argument("--min-count", type=int, help="명시할 때만 4-neighbour 영역 계산")
    merge = _korean_help(sub.add_parser(
        "merge-exports", add_help=False,
        help="particle-sim export-simcompare 결과 여러 개(원안·변경본·다른 제품)를 data_dir 하나로 합침"))
    merge.add_argument("--out", required=True, help="비어 있는 출력 디렉터리")
    merge.add_argument("sources", nargs="+", help="export-simcompare 출력 디렉터리들")
    validate = _korean_help(sub.add_parser("validate-data", add_help=False, help="numeric_v2 계약·누락·단위 점검"))
    validate.add_argument("--config", required=True, help="numeric_config_v1 YAML")
    validate.add_argument("--out", help="출력 디렉터리")
    return parser


def _print(payload) -> None:
    """결과 dict를 한글이 깨지지 않는 JSON으로 표준 출력에 쓴다."""
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


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
        from simcompare import pipeline
        cfg = pipeline.load_numeric_config(args.config)
        if getattr(args, "min_count", None) is not None:
            cfg.setdefault("map", {})["min_count"] = args.min_count
        runners = {"compare":pipeline.run_numeric_compare,"electrode-map":pipeline.run_numeric_map,
                   "validate-data":pipeline.run_numeric_validate,"export-review-rois":pipeline.run_numeric_roi_export}
        result = runners[args.command](cfg,args.out)
        output = Path(args.out) if args.out is not None else pipeline._numeric_path(cfg,cfg["output_dir"])
        (output/"execution.json").write_text(json.dumps({"command":args.command,**result},ensure_ascii=False,indent=2),encoding="utf-8")
        _print(result)
        return result["exit_code"]
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        # numeric 계약 오류에는 완성 리포트 대신 실패 marker만 쓴다.
        if args.command in ("compare", "electrode-map", "export-review-rois", "validate-data"):
            error = {"status":"unavailable","reasons":[getattr(exc,"code","input_or_config_error")],
                     "detail":str(exc),"command":args.command,"outputs":[],"exit_code":2}
            output = getattr(args,"out",None)
            if output is None and isinstance(locals().get("cfg"),dict):
                try:
                    output = pipeline._numeric_path(cfg,cfg["output_dir"])
                except (ValueError,TypeError,KeyError):
                    output = None
            if output is not None:
                try:
                    folder = Path(output)
                    folder.mkdir(parents=True,exist_ok=True)
                    encoded_error = json.dumps(error,ensure_ascii=False,indent=2)
                    (folder/"error.json").write_text(encoded_error,encoding="utf-8")
                    (folder/"execution.json").write_text(encoded_error,encoding="utf-8")
                except OSError:
                    pass
        print(f"오류: {exc}", file=sys.stderr)
        return 2
