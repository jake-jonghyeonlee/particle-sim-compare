"""Contract tests for adapter_example (derived from the orchestrator smoke gate reproducing review findings)."""


def test_contract_gate():
    import sys, json, tempfile, csv
    from pathlib import Path
    import numpy as np
    from PIL import Image
    from simcompare.adapters.example import load_dataset
    from simcompare.schema import NOT_APPLICABLE, DataContractError
    def build(root, mutate=None):
        root = Path(root); (root / "a").mkdir(parents=True, exist_ok=True)
        np.save(root / "a" / "l.npy", np.eye(4, dtype=np.float32))
        Image.fromarray((np.eye(4) * 255).astype(np.uint8), mode="L").save(root / "a" / "h.png")
        files = {
            "bundles.csv": [dict(sample_id="S1", product_id="P1", product_family="F1", design_version="v1", design_lineage_id="L1",
                                 electrode_a_role="data", electrode_b_role="", layer_a="M2", layer_b="", failure_type="lateral_open",
                                 fab_id="FAB_A", fab_distribution_id="", assigned_process_layer="M2", unit_width="50", unit_height="",
                                 physical_length_unit="um"),
                            dict(sample_id="S2", product_id="P1", product_family="F1", design_version="v1", design_lineage_id="L1",
                                 electrode_a_role="data", electrode_b_role="pix", layer_a="M2", layer_b="ITO", failure_type="lateral_short",
                                 fab_id="FAB_A", fab_distribution_id="D1", assigned_process_layer="ITO", unit_width="", unit_height="",
                                 physical_length_unit="")],
            "layouts.csv": [dict(sample_id="S1", slot="a", path="a/l.npy", pixel_size="1.5", origin_x="0", origin_y="0")],
            "hotspots.csv": [dict(sample_id="S1", particle_size="2", path="a/h.png", pixel_size="", origin_x="", origin_y="")],
            "ca_records.csv": [dict(sample_id="S1", particle_size="1", ca_value="0", ca_unit="um^2"),
                               dict(sample_id="S1", particle_size="2", ca_value="", ca_unit="um^2")],
        }
        fab = {"distributions": [dict(fab_distribution_id="D1", fab_id="FAB_A", product_family="F1", process_layer="ITO",
                                      bin_lower=[0.5], bin_upper=[1.5], raw_values=[None], value_kind="probability",
                                      value_unit="1", size_unit="um", surveyed_area=None, area_unit=None, period=None, is_reference=False)]}
        if mutate:
            mutate(files, fab)
        for name, rows in files.items():
            with open(root / name, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
        (root / "fab.json").write_text(json.dumps(fab))
        return root
    with tempfile.TemporaryDirectory() as tmp:
        def fix(files, fab):
            files["bundles.csv"][0]["unit_width"] = ""
        data = load_dataset(build(Path(tmp) / "ok2", fix), mode="real")
        b = {x.sample_id: x for x in data.bundles}
        s1 = b["S1"]
        assert s1.electrode_b_role == NOT_APPLICABLE and s1.layer_b == NOT_APPLICABLE and s1.unit_width is None
        assert len(s1.layouts) == 1 and s1.layouts[0].value_semantics == "binary" and s1.layouts[0].pixel_size == 1.5
        assert s1.hotspots[0].value_semantics == "processed_intensity" and s1.hotspots[0].particle_size == 2.0
        assert s1.ca.observed.tolist() == [True, False] and s1.ca.values[0] == 0.0 and np.isnan(s1.ca.values[1])
        assert b["S2"].ca is None and any("S2" in w for w in data.warnings)
        assert np.isnan(data.distributions["D1"].raw_values[0]) and not data.distributions["D1"].observed[0]
        assert s1.provenance.adapter == "example"
        cases = {
            "unknown": lambda f, d: f["ca_records.csv"].append(dict(sample_id="NOPE", particle_size="4", ca_value="1", ca_unit="um^2")),
            "dup": lambda f, d: f["bundles.csv"].append(dict(f["bundles.csv"][0])),
            "slot_b_open": lambda f, d: f["layouts.csv"].append(dict(sample_id="S1", slot="b", path="a/l.npy", pixel_size="", origin_x="", origin_y="")),
            "escape": lambda f, d: f["layouts.csv"].__setitem__(0, dict(f["layouts.csv"][0], path="../x.npy")),
            "bad_fab_id": lambda f, d: f["bundles.csv"][1].__setitem__("fab_distribution_id", "D9"),
            "open_with_b": lambda f, d: f["bundles.csv"][0].__setitem__("electrode_b_role", "pix"),
            "missing_image": lambda f, d: f["hotspots.csv"].__setitem__(0, dict(f["hotspots.csv"][0], path="a/none.png")),
        }
        for name, mutate in cases.items():
            def both(f, d, m=mutate):
                fix(f, d); m(f, d)
            try:
                load_dataset(build(Path(tmp) / name, both), mode="real"); raise AssertionError(f"accepted {name}")
            except DataContractError:
                pass


def test_review_findings_adapter(tmp_path):
    """ADAPTER-001..003: header-only CSV, blank sample_id, non-numeric values."""
    import json
    import shutil

    import pytest

    from simcompare.adapters.example import load_dataset
    from simcompare.schema import DataContractError
    from simcompare.synthetic import generate_demo_data

    base = tmp_path / "base"
    generate_demo_data(base)

    def variant(name, edit):
        root = tmp_path / name
        shutil.copytree(base, root)
        edit(root)
        return root

    header_only = variant("header", lambda r: (r / "bundles.csv").write_text("sample_id,product_id\n"))
    blank_id = variant("blank", lambda r: (r / "ca_records.csv").open("a").write(",1,2,um^2\n"))
    bad_size = variant("bad", lambda r: (r / "hotspots.csv").write_text(
        (r / "hotspots.csv").read_text().replace(",2.0,", ",bad,", 1)))

    def bad_fab(root):
        payload = json.loads((root / "fab.json").read_text())
        del payload["distributions"][0]["fab_id"]
        (root / "fab.json").write_text(json.dumps(payload))

    missing_fab_id = variant("fab", bad_fab)
    for root in (header_only, blank_id, bad_size, missing_fab_id):
        with pytest.raises(DataContractError):
            load_dataset(root, mode="demo")
