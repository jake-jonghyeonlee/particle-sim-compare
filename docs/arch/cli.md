# cli.py contract

argparse program "simcompare" with subcommands; `main(argv: list[str] | None = None) -> int`.
`src/simcompare/__main__.py` calls `raise SystemExit(main())`.

- `demo-data --out DIR [--seed N]` -> synthetic.generate_demo_data; prints summary JSON.
- `validate-data --config PATH` -> pipeline.run_validate
- `train --config PATH` -> pipeline.run_train
- `embed --config PATH` -> pipeline.run_embed
- `analyze --config PATH` -> pipeline.run_analyze
- `evaluate --config PATH` -> pipeline.run_evaluate
- `all --config PATH [--demo-out DIR]` runs validate-data, train, embed, analyze,
  evaluate in order (demo-data first when --demo-out given and mode is demo).
Config commands print the returned dict as JSON to stdout. DataContractError /
ConfigError / FileNotFoundError -> message to stderr prefixed "오류: ", exit 2.
In mode "demo" every command prints a one-line stderr notice:
"DEMO 모드: 합성/예시 설정이며 결과는 연구 근거가 아닙니다".
