import pytest
import yaml

from simcompare.adapters.example import load_dataset
from simcompare.config import load_config
from simcompare.synthetic import generate_demo_data


def write_demo_config(tmp_path, data_dir, **train):
    raw = yaml.safe_load(open(__file__.replace("tests/conftest.py", "configs/demo.yaml")))
    raw["data"]["data_dir"] = str(data_dir)
    raw["output_dir"] = str(tmp_path / "out")
    raw["data"]["image_size"] = [32, 32]
    raw["train"].update({"epochs": 2, "batch_size": 8, **train})
    path = tmp_path / "demo.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


@pytest.fixture(scope="session")
def demo_dir(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo_data")
    generate_demo_data(path, seed=0)
    return path


@pytest.fixture()
def demo_cfg(tmp_path, demo_dir):
    return load_config(write_demo_config(tmp_path, demo_dir))


@pytest.fixture(scope="session")
def demo_loaded(demo_dir):
    return load_dataset(demo_dir, mode="demo")
