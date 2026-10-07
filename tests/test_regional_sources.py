"""생산자 승격과 명시 manifest를 검증한다. 실제 exporter gate는 integration에 둔다."""
from dataclasses import replace
import json

import numpy as np
from PIL import Image
import pytest

from simcompare import schema
from simcompare.adapters.regional import (
    exported_particle_sim_sources, load_source_manifest, particle_sim_sources, source_for_diameter,
)
from simcompare.regional_measurement import measure_window, validate_source
from simcompare.regional_schema import PhysicalWindow


def bundle(mode="nominal", array=None, total=None):
    array = np.array([[1, 0], [0, 1]], dtype=np.float32) if array is None else array
    return schema.AnalysisBundle(
        "s", "p", "family", "v", "lineage", "A", "B", "M1", "M1", "lateral_short", "fab",
        physical_length_unit="um",
        hotspots=[schema.ImageInput("hotspot", array, "map.npy", 2., (10., 20.), 2., "um", "processed_intensity")],
        ca=schema.CACurve(np.array([2.]), np.array([float(array.sum()*4) if total is None else total]),
                          np.array([True]), "um^2", calc_settings={"ca_source": f"ca_{mode}_um2"}),
        provenance=schema.Provenance("example", calc_version="particle-sim-2.0", calc_settings={
            "ca_mode": mode, "ca_source": f"ca_{mode}_um2", "run_id": "run1", "config_hash": "design1",
            "assumption_set": "v1", "legacy_kernel": False, "post_step_policy": "post",}),
    )


def payload(b):
    return {"calc_settings": b.provenance.calc_settings | {"hotspot_semantics": "nominal binary geometric contact risk"},
            "sample_id_mapping": [{"sample_id": "s", "raw_tuple": ["p", "v", "step1", "pair1"]}],
            "array_storage": {"format": "per-array", "formats": {"map.png":"png"},
                              "binary_encoding": "8-bit grayscale PNG, 0/255, compression level 9"}}


def test_particle_sim_nominal_export_preserves_raw_map_and_total(tmp_path):
    b = bundle()
    b.hotspots[0].path = "map.png"
    Image.fromarray((b.hotspots[0].array*255).astype(np.uint8)).save(tmp_path / "map.png")
    sources = exported_particle_sim_sources(b, tmp_path, payload(b))
    assert len(sources) == 1
    s = sources[0]
    np.testing.assert_array_equal(s.density, b.hotspots[0].array)
    assert s.validation.status == "available" and s.validation.integrated_total_um2 == 8
    assert s.frame.origin_xy == (10, 20) and len(s.provenance["source_files"]["map.png"]) == 64
    assert b.ca_spatial_sources == []  # factory does not mutate


def test_probability_map_matches_mean_ca_only():
    b = bundle("mean", np.array([[.1, .25], [.5, .75]], np.float32))
    s = particle_sim_sources(b, map_mode="mean", ca_mode="mean", source_step="step", unit_id="pair")[0]
    assert s.source_kind == "particle_sim_mean" and s.validation.status == "available"
    assert s.validation.integrated_total_um2 == pytest.approx(b.ca.values[0])
    wrong = particle_sim_sources(b, map_mode="mean", ca_mode="nominal", source_step="step", unit_id="pair")[0]
    assert wrong.validation.reason == "source_mode_mismatch"


def test_direct_producer_anisotropic_pitch_is_preserved_and_rejected():
    b = bundle()
    s = particle_sim_sources(b, map_mode="nominal", ca_mode="nominal", source_step="S", unit_id="U",
                             grid_pitch_xy=(2., 3.))[0]
    assert s.frame.pixel_size == (2., 3.)
    assert s.validation.reason == "unsupported_grid"


def test_equal_sums_do_not_override_source_mode_mismatch():
    b = bundle("mean")  # binary's integral happens to equal the mean scalar
    s = particle_sim_sources(b, map_mode="nominal", ca_mode="mean", source_step="S", unit_id="U")[0]
    assert s.validation.reason == "source_mode_mismatch"
    assert measure_window(s, PhysicalWindow(10, 20, 14, 24)).roi_ca_um2 is None


@pytest.mark.parametrize("semantics", ["processed_intensity", "probability"])
def test_untyped_footprint_and_legend_images_are_not_ca_sources(tmp_path, semantics):
    b = bundle()
    b.hotspots[0].value_semantics = semantics
    b.hotspots[0].legend_id = "known-legend"
    assert exported_particle_sim_sources(b, tmp_path, {}) == []
    p = payload(b)
    p["calc_settings"]["hotspot_semantics"] = "footprint trajectory union"
    assert exported_particle_sim_sources(b, tmp_path, p) == []


def test_legacy_dimensionless_ca_is_not_square_micrometers():
    b = bundle()
    b.ca.unit = "1"
    s = particle_sim_sources(b, map_mode="nominal", ca_mode="nominal", source_step="S", unit_id="U")[0]
    assert s.validation.reason == "legacy_ca_not_area"
    assert s.validation.source_total_um2 is None
    assert source_for_diameter(b, 2)[1] == "legacy_ca_not_area"
    assert b.ca.values[0] == 8


def test_missing_source_or_size_preserves_reason_and_global_ca():
    b = bundle()
    assert source_for_diameter(b, 2) == (None, "missing_source")
    assert source_for_diameter(b, 3) == (None, "missing_size")
    assert b.ca.values[0] == 8
    b.ca.observed[0] = False
    assert source_for_diameter(b, 2) == (None, "missing_total_ca")


def test_source_contract_settings_and_identity_are_preserved():
    b = bundle()
    s = particle_sim_sources(b, map_mode="nominal", ca_mode="nominal", source_step="S", unit_id="U")[0]
    assert s.provenance["source_step"] == "S" and s.provenance["unit_id"] == "U"
    assert s.provenance["producer_version"] == "particle-sim-2.0"
    assert s.validation.provenance["diameter_um"] == 2 and s.validation.provenance["sample_id"] == "s"
    assert s.provenance["settings"]["config_hash"] == "design1"
    s.provenance["settings"]["config_hash"] = "other"
    assert b.provenance.calc_settings["config_hash"] == "design1"


def manifest(tmp_path, b=None, **changes):
    b = bundle() if b is None else b
    np.save(tmp_path / "density.npy", b.hotspots[0].array)
    row = dict(sample_id="s", diameter=2000., diameter_unit="nm", kind="explicit_ca_density", mode="nominal",
               semantics="ca_density", ca_source="ca_nominal_um2", calculation_contract_id="declared-producer-v1",
               density_path="density.npy", origin_xy=[10000., 20000.], pixel_size=2000., length_unit="nm",
               frame_id="local:s", producer="test-producer", version="1", source_files=["density.npy"],
               evidence="Synthetic fixture: this is the actual CA-generating map", source_step="S", unit_id="U")
    row.update(changes)
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(dict(schema_version="1", sources=[row])))
    return b, path, row


def test_explicit_density_manifest_loads_verified_source(tmp_path):
    b, p, row = manifest(tmp_path)
    valid = np.array([[True, False], [True, True]])
    np.save(tmp_path / "valid.npy", valid)
    row["valid_mask_path"] = "valid.npy"
    p.write_text(json.dumps(dict(schema_version="1", sources=[row])))
    s = load_source_manifest(p, [b])[0]
    assert s.validation.status == "available"
    assert measure_window(s, PhysicalWindow(10, 20, 14, 24)).roi_ca_um2 == 8
    assert s.provenance["domain"] == "explicit_valid_mask"
    assert len(s.provenance["manifest_sha256"]) == 64


@pytest.mark.parametrize("case", ["duplicate", "outside", "absolute", "missing", "embedded", "symlink"])
def test_source_manifest_rejects_duplicate_and_outside_paths(tmp_path, case):
    b, p, row = manifest(tmp_path)
    rows = [row]
    if case == "duplicate":
        rows.append(dict(row))
    elif case == "outside":
        row["density_path"] = "../density.npy"
    elif case == "absolute":
        row["density_path"] = str((tmp_path / "density.npy").resolve())
    elif case == "missing":
        row["density_path"] = "missing.npy"
    elif case == "symlink":
        (tmp_path / "escape.npy").symlink_to(tmp_path.parent / "not-in-manifest.npy")
        row["density_path"] = "escape.npy"
    else:
        b.ca_spatial_sources = particle_sim_sources(b, map_mode="nominal", ca_mode="nominal", source_step="S", unit_id="U")
    p.write_text(json.dumps(dict(schema_version="1", sources=rows)))
    with pytest.raises(schema.DataContractError):
        load_source_manifest(p, [b])


@pytest.mark.parametrize("value", [float("inf"), float("nan"), -.01, 1.01])
def test_invalid_source_values_are_not_silently_clipped(tmp_path, value):
    b, p, _ = manifest(tmp_path)
    array = b.hotspots[0].array.copy()
    array[0, 0] = value
    np.save(tmp_path / "density.npy", array)
    with pytest.raises(schema.DataContractError, match="invalid_source_values"):
        load_source_manifest(p, [b])
    s = particle_sim_sources(b, map_mode="nominal", ca_mode="nominal", source_step="S", unit_id="U")[0]
    assert validate_source(replace(s, density=array)).reason == "invalid_source_values"


def test_manifest_does_not_inject_or_relabel_global_scalar(tmp_path):
    b, p, _ = manifest(tmp_path)
    b.ca.calc_settings["ca_source"] = "ca_mean_um2"
    b.provenance.calc_settings["ca_source"] = "ca_mean_um2"
    s = load_source_manifest(p, [b])[0]
    assert s.validation.reason == "source_mode_mismatch" and s.total_ca == 8


def test_manifest_conservation_failure_keeps_original_values(tmp_path):
    b, p, _ = manifest(tmp_path)
    b.ca.values[0] = 999
    s = load_source_manifest(p, [b])[0]
    assert s.validation.reason == "conservation_failed"
    assert s.validation.residual_um2 == -991
    assert s.density.sum() == 2


@pytest.mark.parametrize("probability,ca_mode,expected", [(False, "nominal", None), (True, "mean", None),
                                                        (True, "nominal", "source_mode_mismatch"),
                                                        (False, "mean", "source_mode_mismatch")])
def test_direct_run_adapter_attaches_source_for_exact_plane_without_optional_io(tmp_path, probability, ca_mode, expected):
    # 실제 build_bundles 경로를 NumPy store로 통과시켜 pyarrow/zarr skip과 독립 검증한다.
    import pandas as pd
    from simcompare.adapters import particle_sim_run as checker
    array = np.array([[.25, .5], [0, 1]], np.float32) if probability else np.eye(2, dtype=np.float32)
    table = pd.DataFrame([dict(source_step="S", unit_id="U", unit_kind="electrode_pair", particle_size_um=2.,
                               electrode_a="A", electrode_b="B", electrode_a_layer="M1", electrode_b_layer="M1",
                               classification="confirmed", ca_nominal_um2=float(array.sum()*4),
                               ca_mean_um2=float(array.sum()*4), fab_distribution_layer="M1")])
    run = checker.RunData(tmp_path, {"product_id":"P", "design_version":"V", "simulator_version":"test"},
                          table, "1.0", tmp_path/"raw.zarr", "probability" if probability else "nominal",
                          4., 2., 16., 4., 4.)
    class Store(dict):
        attrs = dict(source_steps=["S"], particle_sizes_um=[2.], unit_ids=["U"])
    store = Store({"probability" if probability else "nominal_failure":array[None, None, None]})
    args = checker.parse_args([str(tmp_path), "--mapping-contract-version", "1.1", "--ca", ca_mode])
    report = checker.RunReport(str(tmp_path))
    loaded = checker.build_bundles(run, report, {"U":checker.UnitMapping("lateral_short", "")}, store, None, args, {})
    assert len(loaded) == 1 and report.count("error") == 0
    s = loaded[0].ca_spatial_sources[0]
    np.testing.assert_array_equal(s.density, array)
    assert s.validation.reason == expected
    assert s.provenance["source_step"] == "S" and s.provenance["unit_id"] == "U"
    assert s.diameter_um == 2 and s.frame.origin_xy == (0, 0)


def test_example_adapter_loads_nominal_source_and_preserves_nonzero_origin(tmp_path):
    import csv
    from simcompare.adapters.example import load_dataset
    sid = "p-v-S-U"
    Image.fromarray(np.eye(2, dtype=np.uint8)*255).save(tmp_path/"map.png")
    rows = {
        "bundles.csv": [dict(sample_id=sid, product_id="p", product_family="f", design_version="v", design_lineage_id="l",
                              electrode_a_role="A", electrode_b_role="B", layer_a="M1", layer_b="M1",
                              failure_type="lateral_short", fab_id="fab", unit_width=4, unit_height=4, physical_length_unit="um")],
        "hotspots.csv": [dict(sample_id=sid, particle_size=2, particle_size_unit="um", path="map.png",
                               pixel_size=2, origin_x=10, origin_y=20)],
        "ca_records.csv": [dict(sample_id=sid, particle_size=2, particle_size_unit="um", ca_value=8, ca_unit="um^2")],
    }
    for name, entries in rows.items():
        with (tmp_path/name).open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(entries[0]))
            writer.writeheader()
            writer.writerows(entries)
    b = bundle()
    p = payload(b)
    p["sample_id_mapping"] = [{"sample_id":sid, "raw_tuple":["p", "v", "S", "U"]}]
    p["calc_version"] = "particle-sim-2.0"
    (tmp_path/"provenance.json").write_text(json.dumps(p))
    (tmp_path/"fab.json").write_text(json.dumps({"distributions":[dict(
        fab_distribution_id="SYNTHETIC_ONLY", fab_id="fab", process_layer="M1", bin_lower=[1.],
        bin_upper=[3.], raw_values=[1.], value_kind="density", value_unit="1/cm2", size_unit="um")]}))
    loaded = load_dataset(tmp_path, mode="demo").bundles[0]
    assert len(loaded.ca_spatial_sources) == 1
    s = loaded.ca_spatial_sources[0]
    assert s.validation.status == "available" and s.frame.origin_xy == (10, 20)
    assert measure_window(s, PhysicalWindow(10, 20, 12, 22)).roi_ca_um2 == 4
