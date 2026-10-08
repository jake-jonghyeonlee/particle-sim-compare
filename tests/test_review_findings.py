"""유지된 legacy export loader의 입력 회귀; 학습 설정 검사는 폐기."""
import numpy as np
import pytest
from PIL import Image
from simcompare.adapters.example import load_dataset, read_image
from simcompare.schema import DataContractError


def test_ca_records_requires_ca_value_column(tmp_path, legacy_export_dir):
    import shutil

    root = tmp_path / "d"
    shutil.copytree(legacy_export_dir, root)
    lines = (root / "ca_records.csv").read_text().splitlines()
    header = lines[0].split(",")
    remove = header.index("ca_value")
    (root / "ca_records.csv").write_text("\n".join(
        ",".join(p for i, p in enumerate(line.split(",")) if i != remove) for line in lines) + "\n")
    with pytest.raises(DataContractError, match="ca_value"):
        load_dataset(root, mode="demo")


def test_unmapped_legend_color_is_contract_error(tmp_path):
    path = tmp_path / "h.png"
    Image.fromarray(np.full((2, 2, 3), 7, np.uint8), mode="RGB").save(path)
    legend = {"legend_id": "L", "entries": [{"rgb": [255, 0, 0], "value": 1.0}], "tolerance": 0}
    with pytest.raises(DataContractError):
        read_image(path, kind="hotspot", legend=legend)


def test_legend_conversion_errors_are_contract_errors(tmp_path):
    path = tmp_path / "h.png"
    Image.fromarray(np.full((2, 2, 3), 7, np.uint8), mode="RGB").save(path)
    with pytest.raises(DataContractError):
        read_image(path, kind="hotspot", legend={"legend_id": "L", "entries": [{"rgb": [7, 7, 7]}]})
    ok = {"legend_id": "L", "entries": [{"rgb": [7, 7, 7], "value": 0.5}], "tolerance": 0}
    arr, semantics, legend_id = read_image(path, kind="hotspot", legend=ok)
    assert semantics == "probability" and legend_id == "L" and np.allclose(arr, 0.5)
