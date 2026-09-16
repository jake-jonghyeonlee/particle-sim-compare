"""Command line interface."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from simcompare.schema import DataContractError

DEMO_NOTICE = "DEMO mode: synthetic/illustrative settings; results are not research evidence"
COMMANDS = ("validate-data", "train", "embed", "analyze", "evaluate")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="simcompare",
        description="Compare already computed particle-defect simulation results across products.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo-data", help="write synthetic demo data (not physical results)")
    demo.add_argument("--out", required=True)
    demo.add_argument("--seed", type=int, default=0)
    for name in COMMANDS:
        sub.add_parser(name).add_argument("--config", required=True)
    run_all = sub.add_parser("all", help="validate-data, train, embed, analyze, evaluate")
    run_all.add_argument("--config", required=True)
    run_all.add_argument("--demo-out", help="generate demo data here first (mode demo only)")
    return parser


def _print(payload) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "demo-data":
            from simcompare.synthetic import generate_demo_data

            print(DEMO_NOTICE, file=sys.stderr)
            _print(generate_demo_data(args.out, seed=args.seed))
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
                    raise DataContractError("--demo-out is only allowed with mode: demo")
                from simcompare.synthetic import generate_demo_data

                results["demo-data"] = generate_demo_data(args.demo_out, seed=cfg.train.seed)
                # Analyse exactly the data that was just generated.
                cfg.data.data_dir = str(Path(args.demo_out).resolve())
            for name in COMMANDS:
                results[name] = runners[name](cfg)
            _print(results)
        else:
            _print(runners[args.command](cfg))
        return 0
    except (DataContractError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
