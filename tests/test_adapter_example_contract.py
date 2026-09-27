"""Contract tests for adapter_example (derived from the orchestrator smoke gate reproducing review findings)."""

import csv
import json
import pytest
from simcompare.adapters.example import load_dataset, _load_processed_fab_measurements
from simcompare.schema import DataContractError


def test_contract_gate():
    import json
    import tempfile
    import csv
    from pathlib import Path
    import numpy as np
    from PIL import Image
    from simcompare.adapters.example import load_dataset
    from simcompare.schema import NOT_APPLICABLE, DataContractError

    def build(root, mutate=None):
        root = Path(root)
        (root / "a").mkdir(parents=True, exist_ok=True)
        np.save(root / "a" / "l.npy", np.eye(4, dtype=np.float32))
        Image.fromarray((np.eye(4) * 255).astype(np.uint8), mode="L").save(
            root / "a" / "h.png"
        )
        files = {
            "bundles.csv": [
                dict(
                    sample_id="S1",
                    product_id="P1",
                    product_family="F1",
                    design_version="v1",
                    design_lineage_id="L1",
                    electrode_a_role="data",
                    electrode_b_role="",
                    layer_a="M2",
                    layer_b="",
                    failure_type="lateral_open",
                    fab_id="FAB_A",
                    fab_distribution_id="",
                    assigned_process_layer="M2",
                    unit_width="50",
                    unit_height="",
                    physical_length_unit="um",
                ),
                dict(
                    sample_id="S2",
                    product_id="P1",
                    product_family="F1",
                    design_version="v1",
                    design_lineage_id="L1",
                    electrode_a_role="data",
                    electrode_b_role="pix",
                    layer_a="M2",
                    layer_b="ITO",
                    failure_type="lateral_short",
                    fab_id="FAB_A",
                    fab_distribution_id="D1",
                    assigned_process_layer="ITO",
                    unit_width="",
                    unit_height="",
                    physical_length_unit="",
                ),
            ],
            "layouts.csv": [
                dict(
                    sample_id="S1",
                    slot="a",
                    path="a/l.npy",
                    pixel_size="1.5",
                    origin_x="0",
                    origin_y="0",
                )
            ],
            "hotspots.csv": [
                dict(
                    sample_id="S1",
                    particle_size="2",
                    particle_size_unit="um",
                    path="a/h.png",
                    pixel_size="1.5",
                    origin_x="0",
                    origin_y="0",
                )
            ],
            "ca_records.csv": [
                dict(
                    sample_id="S1",
                    particle_size="1",
                    particle_size_unit="um",
                    ca_value="0",
                    ca_unit="1",
                ),
                dict(
                    sample_id="S1",
                    particle_size="2",
                    particle_size_unit="um",
                    ca_value="",
                    ca_unit="1",
                ),
            ],
        }
        fab = {
            "distributions": [
                dict(
                    fab_distribution_id="D1",
                    fab_id="FAB_A",
                    product_family="F1",
                    process_layer="ITO",
                    bin_lower=[0.5],
                    bin_upper=[1.5],
                    raw_values=[None],
                    value_kind="probability",
                    value_unit="1",
                    size_unit="um",
                    surveyed_area=None,
                    area_unit=None,
                    period=None,
                    is_reference=False,
                )
            ]
        }
        if mutate:
            mutate(files, fab)
        for name, rows in files.items():
            with open(root / name, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
        (root / "fab.json").write_text(json.dumps(fab))
        return root

    with tempfile.TemporaryDirectory() as tmp:

        def fix(files, fab):
            files["bundles.csv"][0]["unit_width"] = ""

        data = load_dataset(build(Path(tmp) / "ok2", fix), mode="real")
        b = {x.sample_id: x for x in data.bundles}
        s1 = b["S1"]
        assert (
            s1.electrode_b_role == NOT_APPLICABLE
            and s1.layer_b == NOT_APPLICABLE
            and s1.unit_width is None
        )
        assert (
            len(s1.layouts) == 1
            and s1.layouts[0].value_semantics == "binary"
            and s1.layouts[0].pixel_size == 1.5
        )
        assert (
            s1.hotspots[0].value_semantics == "processed_intensity"
            and s1.hotspots[0].particle_size == 2.0
        )
        assert (
            s1.ca.observed.tolist() == [True, False]
            and s1.ca.values[0] == 0.0
            and np.isnan(s1.ca.values[1])
        )
        assert b["S2"].ca is None and any("S2" in w for w in data.warnings)
        assert (
            np.isnan(data.distributions["D1"].raw_values[0])
            and not data.distributions["D1"].observed[0]
        )
        assert s1.provenance.adapter == "example"
        cases = {
            "unknown": lambda f, d: f["ca_records.csv"].append(
                dict(
                    sample_id="NOPE",
                    particle_size="4",
                    particle_size_unit="um",
                    ca_value="1",
                    ca_unit="1",
                )
            ),
            "dup": lambda f, d: f["bundles.csv"].append(dict(f["bundles.csv"][0])),
            "slot_b_open": lambda f, d: f["layouts.csv"].append(
                dict(
                    sample_id="S1",
                    slot="b",
                    path="a/l.npy",
                    pixel_size="",
                    origin_x="",
                    origin_y="",
                )
            ),
            "escape": lambda f, d: f["layouts.csv"].__setitem__(
                0, dict(f["layouts.csv"][0], path="../x.npy")
            ),
            "bad_fab_id": lambda f, d: f["bundles.csv"][1].__setitem__(
                "fab_distribution_id", "D9"
            ),
            "open_with_b": lambda f, d: f["bundles.csv"][0].__setitem__(
                "electrode_b_role", "pix"
            ),
            "missing_image": lambda f, d: f["hotspots.csv"].__setitem__(
                0, dict(f["hotspots.csv"][0], path="a/none.png")
            ),
        }
        for name, mutate in cases.items():

            def both(f, d, m=mutate):
                fix(f, d)
                m(f, d)

            try:
                load_dataset(build(Path(tmp) / name, both), mode="real")
                raise AssertionError(f"accepted {name}")
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

    header_only = variant(
        "header", lambda r: (r / "bundles.csv").write_text("sample_id,product_id\n")
    )
    blank_id = variant(
        "blank", lambda r: (r / "ca_records.csv").open("a").write(",1,um,0.2,1\n")
    )
    bad_size = variant(
        "bad",
        lambda r: (r / "hotspots.csv").write_text(
            (r / "hotspots.csv").read_text().replace(",2.0,", ",bad,", 1)
        ),
    )

    def bad_fab(root):
        payload = json.loads((root / "fab.json").read_text())
        del payload["distributions"][0]["fab_id"]
        (root / "fab.json").write_text(json.dumps(payload))

    missing_fab_id = variant("fab", bad_fab)
    for root in (header_only, blank_id, bad_size, missing_fab_id):
        with pytest.raises(DataContractError):
            load_dataset(root, mode="demo")


def f4_data(root):
    row = dict(
        sample_id="P-V-S-U",
        product_id="P",
        product_family="F",
        design_version="V",
        design_lineage_id="L",
        electrode_a_role="a",
        electrode_b_role="b",
        layer_a="A",
        layer_b="B",
        failure_type="vertical_short",
        fab_id="FAB",
        assigned_process_layer="SOURCE",
        fab_pair_key="U:A-B",
    )
    with (root / "bundles.csv").open("w") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    (root / "layer_stack.csv").write_text(
        "product_id,design_version,layer_name,stack_order,fab_process_layer\nP,*,A,1,LOW\nP,*,B,2,UPPER\n"
    )
    fab = dict(
        distributions=[
            dict(
                fab_distribution_id="D",
                fab_id="FAB",
                process_layer="SOURCE",
                fab_pair_key="U:A-B",
                bin_lower=[0.5],
                bin_upper=[1.5],
                raw_values=[0.2],
                value_kind="probability",
                value_unit="1",
                size_unit="um",
            )
        ]
    )
    (root / "fab.json").write_text(json.dumps(fab))
    provenance = dict(
        mapping_contract_version="1.2",
        calc_version="test",
        calc_settings={"x": 1},
        sample_id_mapping=[
            dict(
                sample_id=row["sample_id"],
                raw_tuple=["P", "V", "S", "U"],
                fab_pair_key="U:A-B",
                assigned_process_layer="SOURCE",
            )
        ],
    )
    (root / "provenance.json").write_text(json.dumps(provenance))
    return row, provenance, fab


def test_f4_adapter_round_trip_and_reference(tmp_path):
    _, _, fab = f4_data(tmp_path)
    loaded = load_dataset(tmp_path, mode="real")
    b = loaded.bundles[0]
    assert (b.assigned_process_layer, b.fab_pair_key, b.mapping_contract_version) == (
        "SOURCE",
        "U:A-B",
        "1.2",
    )
    assert b.provenance.calc_version == "test" and b.provenance.calc_settings["x"] == 1
    assert b.provenance.calc_settings["upper_physical_layer"] == "B"
    assert loaded.distributions["D"].fab_pair_key == b.fab_pair_key
    fab["distributions"][0]["is_reference"] = True
    (tmp_path / "fab.json").write_text(json.dumps(fab))
    with pytest.raises(DataContractError, match="reference"):
        load_dataset(tmp_path, mode="demo")


@pytest.mark.parametrize(
    "defect",
    [
        "missing_marker",
        "legacy_marker",
        "unknown_marker",
        "missing_key",
        "missing_assignment",
        "padded_assignment",
        "padded_pair",
        "raw_unit",
        "map_assignment",
        "missing_map_entry",
    ],
)
def test_f4_adapter_rejects_partial_and_tampered_inputs(tmp_path, defect):
    row, provenance, _ = f4_data(tmp_path)
    if defect == "missing_marker":
        provenance.pop("mapping_contract_version")
    elif defect == "legacy_marker":
        provenance["mapping_contract_version"] = "1.1"
    elif defect == "unknown_marker":
        provenance["mapping_contract_version"] = "2.0"
    elif defect == "missing_key":
        row.pop("fab_pair_key")
    elif defect == "missing_assignment":
        row["assigned_process_layer"] = ""
    elif defect == "padded_assignment":
        row["assigned_process_layer"] = " SOURCE "
    elif defect == "padded_pair":
        row["fab_pair_key"] = " U:A-B "
    elif defect == "raw_unit":
        provenance["sample_id_mapping"][0]["raw_tuple"][3] = "other"
    elif defect == "map_assignment":
        provenance["sample_id_mapping"][0]["assigned_process_layer"] = "OTHER"
    else:
        provenance["sample_id_mapping"] = []
    (tmp_path / "provenance.json").write_text(json.dumps(provenance))
    with (tmp_path / "bundles.csv").open("w") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    with pytest.raises(DataContractError):
        load_dataset(tmp_path, mode="demo")


@pytest.mark.parametrize("pair", [None, "U:A-B"])
def test_f4_csv_pair_consistency(tmp_path, pair):
    rows = [
        dict(
            source_id="D",
            fab_id="FAB",
            product_family="F",
            process_layer="SOURCE",
            particle_size_um=size,
            avg_detected_count=1,
            surveyed_area_um2=100,
            averaging_basis="per_glass",
            period_start="2026-01",
            period_end="2026-02",
        )
        for size in (1, 2)
    ]
    if pair:
        for row in rows:
            row["fab_pair_key"] = pair

    def write():
        with (tmp_path / "fab_measurements.csv").open("w") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    write()
    assert (
        _load_processed_fab_measurements(tmp_path, mode="demo")["D"].fab_pair_key
        == pair
    )
    if pair:
        rows[1]["fab_pair_key"] = ""
        write()
        with pytest.raises(DataContractError, match="fab_pair_key"):
            _load_processed_fab_measurements(tmp_path, mode="demo")


@pytest.mark.parametrize("version", [None, "1.1"])
@pytest.mark.parametrize("assignment", [" UPPER ", "   "])
def test_legacy_bundle_whitespace_normalization(tmp_path, version, assignment):
    from simcompare.caa import select_distribution

    row, provenance, fab = f4_data(tmp_path)
    if version is None:
        provenance.pop("mapping_contract_version")
    else:
        provenance["mapping_contract_version"] = version
    provenance.pop("sample_id_mapping")
    row["assigned_process_layer"] = assignment
    row["fab_pair_key"] = "   "
    distribution = fab["distributions"][0]
    distribution["fab_pair_key"] = None
    distribution["process_layer"] = "UPPER"
    (tmp_path / "provenance.json").write_text(json.dumps(provenance))
    (tmp_path / "fab.json").write_text(json.dumps(fab))
    with (tmp_path / "bundles.csv").open("w") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    loaded = load_dataset(tmp_path, mode="real")
    bundle = loaded.bundles[0]
    assert bundle.assigned_process_layer == "UPPER"
    assert bundle.fab_pair_key is None
    assert select_distribution(bundle, loaded.distributions).fab_distribution_id == "D"
