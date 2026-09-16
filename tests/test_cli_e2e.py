import json

import numpy as np

from conftest import write_demo_config
from simcompare.cli import main


def test_cli_full_demo_run_on_cpu(tmp_path, capsys):
    data = tmp_path / "data"
    config = write_demo_config(tmp_path, data)
    assert main(["demo-data", "--out", str(data)]) == 0
    assert main(["validate-data", "--config", str(config)]) == 0
    out = tmp_path / "out"
    validation = json.loads((out / "validation.json").read_text())
    assert validation["synthetic"] and validation["physical_scale"]["missing_sample_ids"]
    assert validation["missing_inputs"]["open_b_not_applicable"] == 7
    for command in ("train", "embed", "analyze", "evaluate"):
        assert main([command, "--config", str(config)]) == 0, command
    for name in ("model.pt", "train_history.csv", "split.json", "run_record.json", "embeddings.npz",
                 "neighbors.csv", "clusters.json", "cluster_map.png", "ranking.csv",
                 "loo_stability.csv", "review_cards.json", "evaluation.json"):
        assert (out / name).exists(), name
    split = json.loads((out / "split.json").read_text())
    train_products = {s["sample_id"].split("-")[0] for s in split["train"]}
    test_products = {s["sample_id"].split("-")[0] for s in split["test"]}
    assert train_products and test_products and not train_products & test_products
    spaces = np.load(out / "embeddings.npz")
    assert {"shape_only", "with_risk_baseline", "with_risk_ae"} <= set(spaces.files)
    evaluation = json.loads((out / "evaluation.json").read_text())
    assert set(evaluation["ablation"]) == {"image_only", "numeric_only", "full"}
    assert "연구 근거가 아닙니다" in evaluation["notes"][0]
    cards = json.loads((out / "review_cards.json").read_text())["cards"]
    assert cards and all("disclaimer" in card for card in cards)
    assert "DEMO 모드" in capsys.readouterr().err


def test_cli_reports_contract_errors(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("mode: real\noutput_dir: x\n")
    assert main(["train", "--config", str(bad)]) == 2
    assert "오류:" in capsys.readouterr().err
    config = write_demo_config(tmp_path, tmp_path / "missing")
    assert main(["embed", "--config", str(config)]) == 2
