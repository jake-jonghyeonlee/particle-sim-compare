"""simcompare 명령행 인터페이스.

demo-data, merge-exports, validate-data, train, embed, analyze, evaluate, all 명령을 제공한다.
데이터·설정 계약 오류는 트레이스백 대신 '오류:' 메시지와 종료 코드 2로 끝낸다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from simcompare.schema import DataContractError

DEMO_NOTICE = "DEMO 모드: 합성/예시 설정이며 결과는 연구 근거가 아닙니다"
COMMANDS = ("validate-data", "train", "embed", "analyze", "evaluate")


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
    demo.add_argument("--seed", type=int, default=0, help="난수 seed")
    merge = _korean_help(sub.add_parser(
        "merge-exports", add_help=False,
        help="particle-sim export-simcompare 결과 여러 개(원안·변경본·다른 제품)를 data_dir 하나로 합침"))
    merge.add_argument("--out", required=True, help="비어 있는 출력 디렉터리")
    merge.add_argument("sources", nargs="+", help="export-simcompare 출력 디렉터리들")
    helps = {
        "validate-data": "입력 데이터 점검(누락·실제 크기·중복 의심·교락·CAA 적용 가능 여부)",
        "train": "제품·설계 계열 단위로 분리해 다중 입력 오토인코더 학습",
        "embed": "AE와 기준 PCA 특징 벡터 저장",
        "analyze": "유사 사례 검색·군집·위험 순위 비교·제품 제외 안정성·검토 카드 생성",
        "evaluate": "평가 분리 복원 손실·입력 구성 비교·Fab/제품군 제외 검증",
    }
    for name in COMMANDS:
        command = _korean_help(sub.add_parser(name, add_help=False, help=helps[name]))
        command.add_argument("--config", required=True, help="YAML 설정 파일 경로")
    run_all = _korean_help(sub.add_parser("all", add_help=False, help="validate-data부터 evaluate까지 순서대로 실행"))
    run_all.add_argument("--config", required=True, help="YAML 설정 파일 경로")
    run_all.add_argument("--demo-out", help="이 경로에 demo 데이터를 먼저 생성하고 그 데이터로 분석(mode demo 전용)")
    return parser


def _print(payload) -> None:
    """결과 dict를 한글이 깨지지 않는 JSON으로 표준 출력에 쓴다."""
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> int:
    """명령행 진입점. 성공하면 0, 계약 오류나 파일 누락이면 2를 반환한다.

    demo 모드에서는 결과가 연구 근거가 아니라는 안내를 stderr에 출력한다.
    all --demo-out을 쓰면 생성한 demo 데이터 경로로 data_dir을 바꿔 분석한다.
    """
    args = _parser().parse_args(argv)
    try:
        if args.command == "demo-data":
            from simcompare.synthetic import generate_demo_data

            print(DEMO_NOTICE, file=sys.stderr)
            _print(generate_demo_data(args.out, seed=args.seed))
            return 0
        if args.command == "merge-exports":
            from simcompare.merge import merge_exports

            _print(merge_exports([Path(s) for s in args.sources], Path(args.out)))
            return 0
        from simcompare import pipeline
        from simcompare.config import load_config

        cfg = load_config(args.config)
        if cfg.mode == "demo":
            print(DEMO_NOTICE, file=sys.stderr)
        runners = {
            "validate-data": pipeline.run_validate, "train": pipeline.run_train,
            "embed": pipeline.run_embed, "analyze": pipeline.run_analyze,
            "evaluate": pipeline.run_evaluate,
        }
        if args.command == "all":
            results = {}
            if args.demo_out:
                if cfg.mode != "demo":
                    raise DataContractError("--demo-out은 mode: demo에서만 사용할 수 있습니다")
                from simcompare.synthetic import generate_demo_data

                results["demo-data"] = generate_demo_data(args.demo_out, seed=cfg.train.seed)
                # 방금 생성한 데이터를 그대로 분석한다.
                cfg.data.data_dir = str(Path(args.demo_out).resolve())
            for name in COMMANDS:
                results[name] = runners[name](cfg)
            _print(results)
        else:
            _print(runners[args.command](cfg))
        return 0
    except (DataContractError, FileNotFoundError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
