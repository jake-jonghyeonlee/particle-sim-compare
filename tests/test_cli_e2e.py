import json

import numpy as np
import yaml

from conftest import write_demo_config
from simcompare.cli import main
from simcompare.config import load_config
from simcompare.pipeline import _training_fingerprint


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
                 "loo_stability.csv", "review_cards.json", "evaluation.json",
                 "figures/rank_shift.svg", "figures/ca_curves.svg", "figures/loo_stability.svg"):
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


def test_checkpoint_fingerprint_rejects_changed_input(tmp_path, capsys):
    data = tmp_path / "data"
    config = write_demo_config(tmp_path, data, epochs=1)
    assert main(["demo-data", "--out", str(data)]) == 0
    assert main(["train", "--config", str(config)]) == 0
    provenance = data / "provenance.json"
    provenance.write_text(provenance.read_text() + "\n")
    assert main(["embed", "--config", str(config)]) == 2
    assert "오래된 체크포인트" in capsys.readouterr().err


def test_checkpoint_fingerprint_rejects_changed_training_contract(tmp_path, capsys):
    data = tmp_path / "data"
    config = write_demo_config(tmp_path, data, epochs=1, seed=0)
    assert main(["demo-data", "--out", str(data)]) == 0
    assert main(["train", "--config", str(config)]) == 0
    payload = yaml.safe_load(config.read_text())
    payload["train"]["seed"] = 1
    config.write_text(yaml.safe_dump(payload))
    assert main(["analyze", "--config", str(config)]) == 2
    assert "오래된 체크포인트" in capsys.readouterr().err


def test_external_review_status_is_not_a_training_fingerprint_input(tmp_path):
    data = tmp_path / "data"
    config = write_demo_config(tmp_path, data, epochs=1)
    assert main(["demo-data", "--out", str(data)]) == 0
    cfg = load_config(config)
    before = _training_fingerprint(cfg)
    (data / "design_review_status.csv").write_text("human review state may change\n")
    assert _training_fingerprint(cfg) == before
