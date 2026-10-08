"""유지된 legacy export 병합 회귀 시험 (variants와 독립)."""
import csv
import json
import pandas as pd
import pytest
from simcompare.cli import main
from simcompare.merge import merge_exports
from simcompare.schema import DataContractError


def _export(root, name, sample, *, version="V01", mapping="1.2", settings=None):
    out = root / name
    (out / "arrays").mkdir(parents=True)
    (out / "arrays" / "layout-A.png").write_bytes(name.encode())
    tables = {
        "bundles.csv": (["sample_id", "product_id", "design_version"], [[sample, "P", version]]),
        "layouts.csv": (["sample_id", "slot", "path"], [[sample, "a", "arrays/layout-A.png"]]),
        "hotspots.csv": (["sample_id", "particle_size", "path"], [[sample, "2", "arrays/layout-A.png"]]),
        "ca_records.csv": (["sample_id", "particle_size", "ca_value"], [[sample, "2", "0.1"]]),
        "layer_stack.csv": (["product_id", "design_version", "layer_name"], [["P", version, "M1"]]),
    }
    for file, (header, rows) in tables.items():
        with (out / file).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
    (out / "geometry.json").write_text(json.dumps({sample: {}}))
    (out / "provenance.json").write_text(json.dumps({
        "mapping_contract_version": mapping, "is_synthetic": True, "calc_version": "particle-sim-0.1.0",
        "calc_settings": settings or {"run_id": name, "ca_mode": "nominal"},
        "sample_id_mapping": [{"sample_id": sample}],
        "array_storage": {"format": "per-array", "formats": {"arrays/layout-A.png": "png"}},
    }))
    (out / "export_warnings.json").write_text(json.dumps(["Fab 없음"]))
    return out


def test_merge_exports_rebases_arrays_and_merges_provenance(tmp_path):
    base = _export(tmp_path, "base", "P-V01-S-A")
    variant = _export(tmp_path, "var", "P-V01-e1-S-A", version="V01-e1")
    summary = merge_exports([base, variant], tmp_path / "merged")
    out = tmp_path / "merged"
    assert summary["bundles"] == 2 and summary["arrays"] == 2
    layouts = pd.read_csv(out / "layouts.csv")
    assert layouts["path"].tolist() == ["arrays/01_base/layout-A.png", "arrays/02_var/layout-A.png"]
    assert (out / "arrays/02_var/layout-A.png").read_bytes() == b"var"
    assert len(pd.read_csv(out / "layer_stack.csv")) == 2
    provenance = json.loads((out / "provenance.json").read_text())
    assert provenance["calc_settings"]["ca_mode"] == "nominal"
    assert "run_id" not in provenance["calc_settings"]
    assert [s["run_id"] for s in provenance["calc_settings"]["merged_sources"]] == ["base", "var"]
    assert set(provenance["array_storage"]["formats"]) == {
        "arrays/01_base/layout-A.png", "arrays/02_var/layout-A.png"}
    assert len(provenance["sample_id_mapping"]) == 2
    assert any("Fab" in w for w in json.loads((out / "export_warnings.json").read_text()))


def test_merge_exports_rejects_conflicts(tmp_path):
    base = _export(tmp_path, "base", "P-V01-S-A")
    with pytest.raises(DataContractError, match="sample_id가 겹칩니다"):
        merge_exports([base, _export(tmp_path, "dup", "P-V01-S-A")], tmp_path / "o1")
    with pytest.raises(DataContractError, match="mapping_contract_version"):
        merge_exports([base, _export(tmp_path, "old", "P-V02-S-A", version="V02", mapping="1.1")],
                      tmp_path / "o2")
    extra = _export(tmp_path, "extra", "P-V03-S-A", version="V03")
    (extra / "fab.json").write_text("{}")
    with pytest.raises(DataContractError, match="합칠 수 없는 파일"):
        merge_exports([base, extra], tmp_path / "o3")
    with pytest.raises(DataContractError, match="두 개 이상"):
        merge_exports([base], tmp_path / "o4")
    assert main(["merge-exports", "--out", str(tmp_path / "o5"), str(base)]) == 2
