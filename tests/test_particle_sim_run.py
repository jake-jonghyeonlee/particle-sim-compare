"""Small reader fixtures verify the maintained raw-run contract."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from simcompare.adapters import particle_sim_run as checker

CONTRACT = json.loads(
    (Path(__file__).parent / "fixtures/particle_sim_mapping_v1.json").read_text()
)


@pytest.fixture
def make_run(tmp_path):
    pytest.importorskip("pyarrow")
    zarr = pytest.importorskip("zarr")

    def make(*, version="2.0", probability=False, steps=("S",), units=None):
        units = units or [("U", "electrode_pair", "lateral_short", "A", "B")]
        path = tmp_path / "run"
        path.mkdir()
        manifest = dict(
            schema_version=version,
            run_id="R",
            product_id="P",
            design_version="V",
            simulator_version="test",
            source_steps=list(steps),
            particle_sizes_um=[1.0, 2.0],
            pixel_size_x_um=1.0,
            pixel_size_y_um=1.0,
            shape_yx=[3, 4],
        )
        (path / "run_manifest.json").write_text(json.dumps(manifest))
        data = np.zeros((len(steps), 2, len(units), 3, 4), dtype=np.float32)
        rows = []
        for si, step in enumerate(steps):
            for ui, (uid, kind, mode, a, b) in enumerate(units):
                for di, size in enumerate([1.0, 2.0]):
                    # Distinct area and spatial pattern for every step/unit/size.
                    count = 1 + si * len(units) + ui + di
                    data[si, di, ui].flat[:count] = 0.5 if probability else 1.0
                    area = float(data[si, di, ui].sum())
                    rows.append(
                        dict(
                            source_step=step,
                            particle_size_um=size,
                            pair_id=uid,
                            failure_unit_id=uid,
                            unit_kind=kind,
                            failure_mode=mode,
                            electrode_a=a,
                            electrode_b=b,
                            electrode_a_layer="M1",
                            electrode_b_layer="N/A" if mode == "lateral_open" else "M2",
                            classification="confirmed",
                            fab_distribution_layer="M1",
                            ca_nominal_um2=area,
                            ca_nominal_normalized=area / 12,
                            ca_mean_um2=area,
                        )
                    )
        table = pd.DataFrame(rows)
        if version == "1.0":
            table = table.drop(columns=list(checker.V2_COLUMNS))
        table.to_parquet(path / "ca.parquet", index=False)
        store = zarr.open_group(
            str(path / ("probability.zarr" if probability else "hotspots.zarr")),
            mode="w",
        )
        store.attrs.update(
            dict(
                source_steps=list(steps),
                particle_sizes_um=[1.0, 2.0],
                pair_ids=[u[0] for u in units],
                failure_unit_ids=[u[0] for u in units],
            )
        )
        store.create_array(
            "probability" if probability else "nominal_failure", data=data
        )
        return path

    return make


def report(path, *flags):
    args = checker.parse_args([str(path), *flags])
    return checker.check_run(path, args, checker.load_mapping(args.mapping), None, {})


def codes(result):
    return {f.code for f in result.findings}


def rewrite(path, change):
    table = pd.read_parquet(path / "ca.parquet")
    table = change(table)
    table.to_parquet(path / "ca.parquet", index=False)


def test_mapping_conformance():
    assert CONTRACT["contract_version"] == "1.2"
    assert checker.EXCLUDED_OPEN_CLASSIFICATIONS == set(
        CONTRACT["excluded_open_warnings"]["classifications"]
    )
    assert {
        key: rule.failure_type for key, rule in checker.FAILURE_MAPPING.items()
    } == {(r["unit_kind"], r["mode"]): r["target"] for r in CONTRACT["mapping_rows"]}
    for vector in CONTRACT["sample_id"]["vectors"]:
        p, v, s, u = vector["raw_tuple"]
        assert (
            checker._sample_id(dict(product_id=p, design_version=v), s, u)
            == vector["sample_id"]
        )


@pytest.mark.parametrize("version", ["1.0", "2.0"])
@pytest.mark.parametrize("probability", [False, True])
def test_nominal_and_mean(make_run, version, probability):
    path = make_run(version=version, probability=probability)
    result = report(
        path,
        "--mapping-contract-version",
        "1.1" if version == "1.0" else "1.2",
        *(["--ca", "mean"] if probability else []),
    )
    assert result.count("error") == 0
    assert result.bundles == ["P-V-S-U"]
    run = checker.load_run(path)
    args = checker.parse_args(
        [
            str(path),
            "--mapping-contract-version",
            "1.1" if version == "1.0" else "1.2",
            *(["--ca", "mean"] if probability else []),
        ]
    )
    bundles = checker.build_bundles(
        run,
        checker.RunReport(str(path)),
        {"U": checker.FAILURE_MAPPING[("electrode_pair", "lateral_short")]},
        checker.open_store(run.store_path),
        None,
        args,
        {},
    )
    np.testing.assert_allclose(
        bundles[0].ca.values, np.array([1, 2]) * (0.5 if probability else 1) / 12
    )
    assert bundles[0].hotspots[0].legend_id == (
        checker.PROBABILITY_LEGEND_ID if probability else None
    )


@pytest.mark.parametrize(
    "kind,uid", [("open_path", "W:path"), ("open_electrode", "auto_open:A")]
)
def test_open_cno_and_strict(make_run, tmp_path, kind, uid):
    path = make_run(
        units=[
            (uid, kind, "lateral_open", "A", "N/A"),
            ("cno:C:A__B", "contact", "contact_not_open", "A", "B"),
        ]
    )
    out = tmp_path / "report.json"
    assert checker.main([str(path), "--output", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert set(payload) == {
        "verdict",
        "strict",
        "mode",
        "ca",
        "simcompare_failure_types",
        "mapping",
        "runs",
        "mapping_contract_version",
    }
    assert len(payload["runs"][0]["bundles"]) == 1
    assert payload["runs"][0]["units"][1]["status"] == "unsupported"
    assert checker.main([str(path), "--strict"]) == 1
    run = checker.load_run(path)
    r = checker.RunReport(str(path))
    bundles = checker.build_bundles(
        run,
        r,
        checker.map_units(run, r, checker.FAILURE_MAPPING),
        checker.open_store(run.store_path),
        None,
        checker.parse_args([str(path)]),
        {},
    )
    assert bundles[0].electrode_b_role == bundles[0].layer_b == "N/A"


@pytest.mark.parametrize(
    "defect,code",
    [
        ("normalization", "normalized_mismatch"),
        ("duplicate", "duplicate_rows"),
        ("sum", "store_area"),
        ("cno_merge", "unit_identity"),
        ("columns", "ca_columns"),
        ("axes", "store_axes"),
        ("shape", "store_shape"),
    ],
)
def test_injected_defects(make_run, defect, code):
    path = make_run(
        units=[("cno:C:A__B", "contact", "contact_not_open", "A", "B")]
        if defect == "cno_merge"
        else None
    )
    if defect == "normalization":
        rewrite(path, lambda t: t.assign(ca_nominal_normalized=0.9))
    elif defect == "duplicate":
        rewrite(path, lambda t: pd.concat([t, t.iloc[:1]]))
    elif defect == "cno_merge":

        def merge(t):
            t.loc[1, "electrode_a"] = "DIFFERENT"
            return t

        rewrite(path, merge)
    elif defect == "columns":
        rewrite(path, lambda t: t.drop(columns="source_step"))
    else:
        zarr = pytest.importorskip("zarr")
        store = zarr.open_group(str(path / "hotspots.zarr"), mode="a")
        if defect == "axes":
            store.attrs["source_steps"] = ["other"]
        elif defect == "shape":
            del store["nominal_failure"]
            store.create_array("nominal_failure", data=np.zeros((1, 2)))
        else:
            store["nominal_failure"][0, 0, 0, 0, 0] = 0
    result = report(path)
    assert code in codes(result)
    assert result.count("error") > 0
    assert checker.main([str(path)]) == 1


@pytest.mark.parametrize("vector", CONTRACT["sample_id"]["collision_vectors"])
def test_full_tuple_collisions(make_run, vector):
    tuples = vector["raw_tuples"]
    path = make_run(
        steps=tuple(dict.fromkeys(t[2] for t in tuples)),
        units=[
            (u, "electrode_pair", "lateral_short", "A", "B")
            for u in dict.fromkeys(t[3] for t in tuples)
        ],
    )
    result = report(path)
    assert "sample_id_collision" in codes(result)
    assert any(
        repr(tuple(tuples[0])) in f.message and repr(tuple(tuples[1])) in f.message
        for f in result.findings
        if f.code == "sample_id_collision"
    )


@pytest.mark.parametrize("vector", CONTRACT["applicability"]["vectors"])
def test_applicability(make_run, vector):
    path = make_run()
    rewrite(path, lambda t: t.assign(classification=vector["classifications"]))
    result = report(path)
    if vector["result"] == "error":
        assert "mixed_applicability" in codes(result)
    else:
        assert result.count("error") == 0
        assert len(result.bundles) == (vector["result"] == "export")


def test_mapping_overrides(make_run, tmp_path):
    path = make_run()
    mapping = tmp_path / "mapping.yaml"
    mapping.write_text(
        "- {unit_kind: electrode_pair, failure_mode: lateral_short, failure_type: impossible}"
    )
    assert checker.main([str(path), "--mapping", str(mapping)]) == 1
    mapping.write_text(
        "- {unit_kind: electrode_pair, failure_mode: lateral_short, failure_type: null}"
    )
    assert checker.main([str(path), "--mapping", str(mapping), "--strict"]) == 1
    assert (
        checker.FAILURE_MAPPING[("electrode_pair", "lateral_short")].failure_type
        == "lateral_short"
    )


def test_missing_store_and_input_immutability(make_run):
    import shutil

    path = make_run()
    before = {p.name: p.read_bytes() for p in path.iterdir() if p.is_file()}
    shutil.rmtree(path / "hotspots.zarr")
    result = report(path)
    assert "store_missing" in codes(result)
    assert result.count("error") == 0
    assert before == {p.name: p.read_bytes() for p in path.iterdir() if p.is_file()}


def test_malformed_parquet(make_run):
    path = make_run()
    (path / "ca.parquet").write_bytes(b"not parquet")
    assert checker.main([str(path)]) == 2


def test_vertical_layer_remains_unverified(make_run):
    path = make_run(units=[("U", "electrode_pair", "vertical_short", "A", "B")])
    result = report(path)
    assert result.count("error") == 0
    assert any(
        "vertical_short" in f.message and "layer_stack.csv" in f.message
        for f in result.findings
        if f.level == "warning"
    )


def test_malformed_store(make_run):
    path = make_run()
    (path / "hotspots.zarr/zarr.json").write_text("{")
    assert checker.main([str(path)]) == 2


def test_across_run_duplicate_warning(make_run, tmp_path):
    import shutil

    path = make_run()
    other = tmp_path / "other"
    shutil.copytree(path, other)
    args = checker.parse_args([str(path), str(other)])
    seen = {}
    first = checker.check_run(path, args, checker.FAILURE_MAPPING, None, seen)
    second = checker.check_run(other, args, checker.FAILURE_MAPPING, None, seen)
    assert first.count("error") == second.count("error") == 0
    assert "sample_id_across_runs" in codes(second)


def test_distinct_raw_tuples_collide_across_runs(make_run, tmp_path):
    import shutil

    path = make_run()
    other = tmp_path / "other"
    shutil.copytree(path, other)
    for run, product, version in [(path, "P-X", "V"), (other, "P", "X-V")]:
        manifest = json.loads((run / "run_manifest.json").read_text())
        manifest.update(product_id=product, design_version=version)
        (run / "run_manifest.json").write_text(json.dumps(manifest))
    args = checker.parse_args([str(path), str(other)])
    seen = {}
    checker.check_run(path, args, checker.FAILURE_MAPPING, None, seen)
    second = checker.check_run(other, args, checker.FAILURE_MAPPING, None, seen)
    assert "sample_id_collision" in codes(second)


@pytest.mark.parametrize("probability", [False, True])
def test_each_bundle_uses_original_step_and_unit_plane(make_run, probability):
    path = make_run(
        steps=("S0", "S1"),
        probability=probability,
        units=[
            ("cno:C:A__B", "contact", "contact_not_open", "A", "B"),
            ("U", "electrode_pair", "lateral_short", "A", "B"),
            ("O", "open_path", "lateral_open", "A", "N/A"),
        ],
    )
    result = report(path)
    assert result.count("error") == 0
    run = checker.load_run(path)
    store = checker.open_store(run.store_path)
    r = checker.RunReport(str(path))
    bundles = checker.build_bundles(
        run,
        r,
        checker.map_units(run, r, checker.FAILURE_MAPPING),
        store,
        None,
        checker.parse_args([str(path)]),
        {},
    )
    assert len(bundles) == 4
    array = store["probability" if probability else "nominal_failure"]
    for bundle in bundles:
        _, _, step, unit = bundle.sample_id.split("-")
        si = ["S0", "S1"].index(step)
        ui = ["cno:C:A__B", "U", "O"].index(unit)
        assert len(bundle.hotspots) == 2
        for di, hotspot in enumerate(bundle.hotspots):
            np.testing.assert_array_equal(hotspot.array, array[si, di, ui])
            assert hotspot.particle_size == di + 1
        rows = run.ca[(run.ca.source_step == step) & (run.ca.unit_id == unit)]
        np.testing.assert_allclose(bundle.ca.values, rows.ca_nominal_normalized)


@pytest.mark.parametrize("kind", ["open_path", "open_electrode"])
def test_excluded_open_warning_per_step_unit(make_run, kind):
    classifications = CONTRACT["excluded_open_warnings"]["classifications"]
    ids = [
        f"auto_open:A{i}" if kind == "open_electrode" else f"O{i}"
        for i in range(len(classifications))
    ]
    units = [(uid, kind, "lateral_open", f"A{i}", "N/A") for i, uid in enumerate(ids)]
    units.append(("P", "electrode_pair", "lateral_short", "A", "B"))
    path = make_run(steps=("S0", "S1"), units=units)

    def classify(table):
        for i, classification in enumerate(classifications):
            table.loc[table.failure_unit_id == ids[i], "classification"] = (
                classification
            )
        # The same classification on a short is not an excluded-open warning.
        table.loc[table.failure_unit_id == "P", "classification"] = classifications[0]
        return table

    rewrite(path, classify)
    result = report(path)
    assert result.count("error") == 0
    assert len(result.bundles) == 2 * len(units)
    warnings = [f for f in result.findings if f.code == "excluded_open"]
    assert len(warnings) == 2 * len(classifications)
    for step in ("S0", "S1"):
        for i, classification in enumerate(classifications):
            matches = [
                f
                for f in warnings
                if f.unit == ids[i] and f"step={step!r}" in f.message
            ]
            assert len(matches) == 1
            assert matches[0].level == "warning"
            assert f"classification={classification}" in matches[0].message
            assert (
                "CA 0 reflects an excluded baseline condition, not low risk"
                in matches[0].message
            )
    assert checker.main([str(path), "--strict"]) == 0


def vertical_config():
    return dict(
        product_id="P",
        design_version="V",
        product_family="F",
        design_lineage_id="L",
        fab_id="FAB",
        stack=[dict(layer_name=f"M{i}", process_order=i) for i in (1, 2, 3)],
        electrodes=[
            dict(electrode_id=e, layer_name=layer, electrode_role=e)
            for e, layer in [("A", "M1"), ("B", "M2"), ("C", "M3")]
        ],
    )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("version", ["1.0", "2.0"])
def test_config_vertical_assignments(make_run, reverse, version, monkeypatch):
    units = [
        (
            "U",
            "electrode_pair",
            "vertical_short",
            *(("B", "A") if reverse else ("A", "B")),
        ),
        (
            "W",
            "electrode_pair",
            "vertical_short",
            *(("C", "B") if reverse else ("B", "C")),
        ),
    ]
    path = make_run(units=units, version=version)
    if version == "2.0":

        def layers(t):
            lookup = {"A": "M1", "B": "M2", "C": "M3"}
            return t.assign(
                electrode_a_layer=t.electrode_a.map(lookup),
                electrode_b_layer=t.electrode_b.map(lookup),
            )

        rewrite(path, layers)
    run = checker.load_run(path)
    result = checker.RunReport(str(path))
    calls = []
    validate = checker.schema.validate_bundle

    def capture(bundle, mode):
        calls.append(mode)
        return validate(bundle, mode=mode)

    monkeypatch.setattr(checker.schema, "validate_bundle", capture)
    bundles = checker.build_bundles(
        run,
        result,
        checker.map_units(run, result, checker.FAILURE_MAPPING),
        checker.open_store(run.store_path),
        vertical_config(),
        checker.parse_args([str(path), "--mapping-contract-version", "1.1"]),
        {},
    )
    assert result.count("error") == 0
    assert [b.assigned_process_layer for b in bundles] == ["M2", "M3"]
    assert calls == ["real", "real"]
    if version == "2.0":
        warnings = [
            f.message for f in result.findings if f.code == "vertical_source_layer"
        ]
        assert warnings
        assert all("source step 'S' (fab layer 'M1')" in text for text in warnings)
        assert any("upper electrode layer 'M2'" in text for text in warnings)
    assert "vertical-layer-unverified" not in codes(result)


@pytest.mark.parametrize(
    "defect,code",
    [
        ("names", "config_stack"),
        ("orders", "config_stack"),
        ("noninteger", "config_stack"),
        ("empty", "config_stack"),
        ("missing_electrode", "config_electrode_layer"),
        ("missing_layer", "config_electrode_layer"),
        ("absent_layer", "config_electrode_layer"),
        ("tie", "config_layer_tie"),
        ("ca", "config_ca_layer"),
        ("product_id", "config_mismatch"),
        ("design_version", "config_mismatch"),
    ],
)
def test_vertical_config_errors(make_run, tmp_path, defect, code):
    import yaml

    path = make_run(units=[("U", "electrode_pair", "vertical_short", "A", "B")])
    config = vertical_config()
    if defect == "names":
        config["stack"][1]["layer_name"] = "M1"
    elif defect == "orders":
        config["stack"][1]["process_order"] = 1
    elif defect == "noninteger":
        config["stack"][1]["process_order"] = 2.0
    elif defect == "empty":
        config["stack"] = []
    elif defect == "missing_electrode":
        config["electrodes"].pop(0)
    elif defect == "missing_layer":
        config["electrodes"][0].pop("layer_name")
    elif defect == "absent_layer":
        config["electrodes"][0]["layer_name"] = "OTHER"
    elif defect == "tie":
        config["electrodes"][1]["layer_name"] = "M1"
        rewrite(path, lambda t: t.assign(electrode_b_layer="M1"))
    elif defect == "ca":
        rewrite(path, lambda t: t.assign(electrode_b_layer="M3"))
    else:
        config[defect] = "OTHER"
    config_path = tmp_path / "product.yaml"
    config_path.write_text(yaml.safe_dump(config))
    args = checker.parse_args(
        [
            str(path),
            "--mapping-contract-version",
            "1.1",
            "--product-config",
            str(config_path),
        ]
    )
    result = checker.check_run(path, args, checker.FAILURE_MAPPING, config, {})
    assert code in codes(result)
    assert result.count("error") > 0
    assert checker.main([str(path), "--product-config", str(config_path)]) == 1


@pytest.mark.parametrize(
    "defect",
    [None, "blank", "mixed", "nan", "missing_step", "duplicate_step", "mismatch"],
)
def test_f4_checker_source_contract(make_run, defect):
    path = make_run(units=[("U", "electrode_pair", "vertical_short", "A", "B")])
    rewrite(path, lambda t: t.assign(fab_distribution_layer="SOURCE"))
    config = dict(
        vertical_config(),
        particle_source_steps=[dict(step_id="S", fab_distribution_layer="SOURCE")],
    )
    if defect in ("blank", "mixed", "nan"):

        def change(t):
            t.loc[0, "fab_distribution_layer"] = {
                "blank": "",
                "mixed": "OTHER",
                "nan": None,
            }[defect]
            return t

        rewrite(path, change)
    elif defect == "missing_step":
        config["particle_source_steps"] = []
    elif defect == "duplicate_step":
        config["particle_source_steps"] *= 2
    elif defect == "mismatch":
        config["particle_source_steps"][0]["fab_distribution_layer"] = "OTHER"
    run = checker.load_run(path)
    result = checker.RunReport(str(path))
    args = checker.parse_args([str(path)])
    assert args.mapping_contract_version == "1.2"
    bundles = checker.build_bundles(
        run,
        result,
        checker.map_units(run, result, checker.FAILURE_MAPPING),
        checker.open_store(run.store_path),
        config,
        args,
        {},
    )
    if defect:
        assert result.count("error") and not bundles
    else:
        assert result.count("error") == 0
        assert bundles[0].assigned_process_layer == "SOURCE"
        assert bundles[0].fab_pair_key == "U:M1-M2"
        assert bundles[0].mapping_contract_version == "1.2"
        assert "vertical_source_layer" not in codes(result)
        assert any("upper physical layer M2" in f.message for f in result.findings)


def test_f4_no_config_does_not_hide_missing_source(make_run):
    path = make_run()
    rewrite(path, lambda t: t.assign(fab_distribution_layer=None))
    assert "source_fab_layer" in codes(report(path))


def test_f4_missing_source_label_hint(make_run):
    path = make_run(version="1.0")
    result = report(path, "--mapping-contract-version", "1.2")
    errors = [f for f in result.findings if f.code == "source_fab_layer"]
    assert errors
    assert all("--mapping-contract-version 1.1" in f.message for f in errors)
