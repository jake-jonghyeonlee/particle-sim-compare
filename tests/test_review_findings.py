"""Regression tests for the independent Codex review of the integration branch."""
import copy
from pathlib import Path

import numpy as np
import pytest
import yaml
from PIL import Image

from simcompare.adapters.example import load_dataset, read_image
from simcompare.cli import main
from simcompare.config import ConfigError, config_from_dict
from simcompare.schema import DataContractError

DEMO = yaml.safe_load((Path(__file__).resolve().parents[1] / "configs" / "demo.yaml").read_text())


@pytest.mark.parametrize("section, key, value", [
    ("analysis", "size_tolerance", -0.5),
    ("model", "conv_channels", []),
    ("model", "conv_channels", [4, 4, 4, 4, 4, 4, 4]),  # 64 not divisible by 2**7
])
def test_config_rejects_values_that_crash_later(section, key, value):
    raw = copy.deepcopy(DEMO)
    raw[section][key] = value
    with pytest.raises(ConfigError):
        config_from_dict(raw)


def test_ca_records_requires_ca_value_column(tmp_path, demo_dir):
    import shutil

    root = tmp_path / "d"
    shutil.copytree(demo_dir, root)
    lines = (root / "ca_records.csv").read_text().splitlines()
    (root / "ca_records.csv").write_text("\n".join(
        ",".join(p for i, p in enumerate(line.split(",")) if i != 2) for line in lines) + "\n")
    with pytest.raises(DataContractError, match="ca_value"):
        load_dataset(root, mode="demo")


def test_unmapped_legend_color_is_contract_error(tmp_path):
    path = tmp_path / "h.png"
    Image.fromarray(np.full((2, 2, 3), 7, np.uint8), mode="RGB").save(path)
    legend = {"legend_id": "L", "entries": [{"rgb": [255, 0, 0], "value": 1.0}], "tolerance": 0}
    with pytest.raises(DataContractError):
        read_image(path, kind="hotspot", legend=legend)


def test_all_with_demo_out_analyses_generated_data(tmp_path):
    raw = copy.deepcopy(DEMO)
    raw["data"]["data_dir"] = str(tmp_path / "does_not_exist")
    raw["output_dir"] = str(tmp_path / "out")
    raw["data"]["image_size"] = [32, 32]
    raw["train"]["epochs"] = 1
    config = tmp_path / "c.yaml"
    config.write_text(yaml.safe_dump(raw))
    assert main(["all", "--config", str(config), "--demo-out", str(tmp_path / "gen")]) == 0
    assert (tmp_path / "out" / "review_cards.json").exists()
