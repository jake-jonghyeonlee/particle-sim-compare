"""리뷰 반례의 frame·지름·결측·출력 크기 계약을 검사한다.

실제 정확도 검증은 하지 않는다. 반례 수치/크기는 아래 fixture를, 생산자 양성 gate는
test_particle_sim_export_positive.py를 수정한다. 파일 출력은 임시 디렉터리만 사용한다.
"""
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pytest

from fixtures.regional.builders import make_bundle, make_pair
from simcompare.adapters.regional import exported_particle_sim_sources, load_source_manifest
from simcompare.config import RegionalConfig
from simcompare.regional import analyze_regions
from simcompare.regional_comparison import compare_pair
from simcompare.regional_report import write_report
from simcompare.schema import DataContractError


@pytest.mark.parametrize("change", [{"origin_xy": (1., 0.)}, {"pixel_size": 2.}])
def test_image_source_frame_mismatch_blocks_roi(change):
    bundle = make_bundle("s", {1.: [[1, 1, 0, 1, 1], [1, 1, 0, 1, 1]]})
    source = bundle.ca_spatial_sources[0]
    bundle.ca_spatial_sources = [replace(source, frame=replace(source.frame, **change))]
    result = compare_pair(bundle, None, None, RegionalConfig())
    assert result.region_sets[0].regions
    for row in result.rows:
        if row.region_id:
            assert row.query.roi is None or row.query.roi.roi_ca_um2 is None
            assert row.query.roi_reason == "incompatible_source_frame"
    assert result.source_summaries[0].selected_union_ca_um2 is None


def test_nanometer_diameter_matches_single_um_key():
    bundle = make_bundle("s", {.7: [[1, 0, 1]]})
    bundle.hotspots[0].particle_size = 700.
    bundle.hotspots[0].particle_size_unit = "nm"
    bundle.ca_spatial_sources = [replace(bundle.ca_spatial_sources[0], diameter_um=.7000000000000001)]
    result = compare_pair(bundle, None, None, RegionalConfig())
    assert {row.diameter_um for row in result.rows} == {.7}
    assert all(row.query.roi.roi_ca_um2 == 1. for row in result.rows if row.region_id)
    assert result.source_summaries[0].diameter_um == .7


def test_source_shape_mismatch_blocks_roi():
    bundle = make_bundle("s", {1.: [[1, 1]]})
    source = bundle.ca_spatial_sources[0]
    bundle.ca_spatial_sources = [replace(source, density=np.array([[1., 1., 0.]]),
                                         valid_mask=np.ones((1, 3), bool))]
    result = compare_pair(bundle, None, None, RegionalConfig())
    row = next(row for row in result.rows if row.region_id)
    assert row.query.roi.roi_ca_um2 is None and row.query.roi_reason == "incompatible_source_frame"


def test_embedded_duplicate_diameter_keys_are_rejected():
    from simcompare.adapters.regional import source_for_diameter
    bundle = make_bundle("s", {.7: [[1, 0]]})
    source = bundle.ca_spatial_sources[0]
    bundle.ca_spatial_sources.append(replace(source, diameter_um=.7000000000000001))
    with pytest.raises(DataContractError, match="duplicate"):
        source_for_diameter(bundle, .7)


def test_manifest_rejects_mixed_unit_duplicate_diameter(tmp_path):
    from test_regional_sources import manifest
    bundle, path, row = manifest(tmp_path)
    bundle.ca.particle_sizes = np.array([.7])
    row.update(diameter=.7, diameter_unit="um")
    second = dict(row, diameter=700., diameter_unit="nm")
    path.write_text(json.dumps(dict(schema_version="1", sources=[row, second])))
    with pytest.raises(DataContractError, match="duplicate"):
        load_source_manifest(path, [bundle])


def test_nanometer_manifest_links_um_scalar(tmp_path):
    from test_regional_sources import manifest
    bundle, path, row = manifest(tmp_path)
    bundle.ca.particle_sizes = np.array([.7])
    row.update(diameter=700., diameter_unit="nm")
    path.write_text(json.dumps(dict(schema_version="1", sources=[row])))
    source = load_source_manifest(path, [bundle])[0]
    assert source.diameter_um == .7
    assert source.validation.status == "available" and source.validation.source_total_um2 == 8.


def test_missing_location_union_is_unknown_not_observed_empty():
    missing = make_bundle("s", {1.: [[1, 1]]})
    missing.hotspots[0].array = None
    empty = make_bundle("s", {1.: [[1, 1]]}, masks={1.: [[0, 0]]})
    unknown = compare_pair(missing, None, None, RegionalConfig()).source_summaries[0]
    known = compare_pair(empty, None, None, RegionalConfig()).source_summaries[0]
    assert unknown.selected_union_ca_um2 is None and unknown.outside_regions_ca_um2 is None
    assert unknown.reason == "location_unavailable"
    assert (known.selected_union_ca_um2, known.outside_regions_ca_um2, known.reason) == (0., 2., None)


def test_missing_and_empty_location_have_distinct_html(tmp_path):
    missing = make_bundle("missing", {1.: [[1, 1]]})
    missing.hotspots[0].array = None
    empty = make_bundle("empty", {1.: [[1, 1]]}, masks={1.: [[0, 0]]})
    cfg = RegionalConfig()
    write_report(analyze_regions([missing, empty], cfg), [missing, empty], tmp_path)
    html = (tmp_path / "regional_report.html").read_text()
    assert "위치 자료 미확인 · 선택 union/창 밖 잔여는 null" in html
    assert "관측된 빈 support · 선택 union은 0" in html


def test_support_status_doc_matches_invalid_value_behavior():
    from simcompare.regions import SupportStatus, collect_support
    bundle = make_bundle("s", {1.: [[1, 0]]})
    bundle.hotspots[0].array[0, 0] = np.nan
    status = collect_support(bundle)[1][0]
    assert status.status == "unavailable" and status.reason == "invalid_hotspot_values"
    assert "missing/invalid" not in SupportStatus.__doc__


def large_case(side: int, diameters: int):
    """원점 0 um/1 um 셀의 64개 분리된 성분과 지름별 약한 배경 없는 합성 maps."""
    rng = np.random.default_rng(42)
    maps = {}
    for d in range(1, diameters + 1):
        array = np.zeros((side, side))
        for y in range(10, side - 10, side // 8):
            for x in range(10, side - 10, side // 8):
                array[y:y + 3, x:x + 3] = rng.uniform(.01, 1., (3, 3))
        maps[float(d)] = array
    q, r = make_bundle("q", maps), make_bundle("r", maps)
    cfg = RegionalConfig()
    return [q, r], cfg, [make_pair(q, r)]


def test_moderate_report_has_bounded_inline_size(tmp_path):
    bundles, cfg, pairs = large_case(256, 8)
    analysis = analyze_regions(bundles, cfg, pairs)
    write_report(analysis, bundles, tmp_path)
    assert (tmp_path / "regional_report.html").stat().st_size < 3_000_000


def test_comparison_reuses_support_and_full_gate_per_source(monkeypatch):
    import simcompare.regional_comparison as module
    bundles, cfg, pairs = large_case(64, 4)
    calls = {"support": 0, "gate": 0}
    support, gate = module.collect_support, module.validate_source
    def counted_support(*args, **kwargs):
        calls["support"] += 1
        return support(*args, **kwargs)
    def counted_gate(*args, **kwargs):
        calls["gate"] += 1
        return gate(*args, **kwargs)
    monkeypatch.setattr(module, "collect_support", counted_support)
    monkeypatch.setattr(module, "validate_source", counted_gate)
    compare_pair(bundles[0], bundles[1], pairs[0], cfg)
    assert calls == {"support": 2, "gate": 8}


def test_report_builds_plane_images_once_and_global_curves_once(tmp_path, monkeypatch):
    import simcompare.regional_plots as module
    bundles, cfg, pairs = large_case(64, 2)
    original = module._plane_image
    seen = []
    def counted(array, color, intensity):
        seen.append((id(array), color, intensity))
        return original(array, color, intensity)
    monkeypatch.setattr(module, "_plane_image", counted)
    write_report(analyze_regions(bundles, cfg, pairs), bundles, tmp_path)
    assert len(seen) == len(set(seen))
    assert len(list((tmp_path / "figures/regional").glob("*-global-curves.svg"))) == 1


@pytest.mark.parametrize("field,value", [("calc_settings", "bad"), ("sample_id_mapping", ["bad"]),
                                        ("array_storage", {"formats": []})])
def test_malformed_export_provenance_is_contract_error(tmp_path, field, value):
    from test_regional_sources import bundle, payload
    b = bundle()
    data = payload(b)
    data[field] = value
    with pytest.raises(DataContractError):
        exported_particle_sim_sources(b, tmp_path, data)


def test_dataset_malformed_calc_settings_is_contract_error(tmp_path, demo_dir):
    import shutil
    from simcompare.adapters.example import load_dataset
    root = tmp_path / "data"
    shutil.copytree(demo_dir, root)
    path = root / "provenance.json"
    payload = json.loads(path.read_text())
    payload["calc_settings"] = "malformed settings"
    path.write_text(json.dumps(payload))
    with pytest.raises(DataContractError, match="calc_settings"):
        load_dataset(root, mode="demo")


def test_regional_source_style_has_bounded_lines():
    root = Path(__file__).parents[1]
    names = ("correspondence", "regions", "regional_comparison", "regional", "regional_plots", "regional_report",
             "regional_measurement", "regional_schema", "adapters/regional")
    for name in names:
        text = (root / "src/simcompare" / (name + ".py")).read_text()
        assert all(len(line) <= 120 for line in text.splitlines()), name
    text = (root / "scripts/regional_demo.py").read_text()
    assert all(len(line) <= 120 for line in text.splitlines()), "regional_demo"
    assert "dummy = SpatialCASource" not in (root / "src/simcompare/regions.py").read_text()
    assert "probe = SpatialCASource" not in (root / "src/simcompare/regional_plots.py").read_text()


def test_one_sided_source_has_distinct_global_reason():
    q = make_bundle("q", {1.: [[1, 0]]})
    r = make_bundle("r", {1.: [[1, 0]]})
    r.ca_spatial_sources = []
    result = compare_pair(q, r, make_pair(q, r), RegionalConfig())
    assert result.rows[0].total_reason == "one_sided_source_domain"


@pytest.mark.parametrize("relationship", ["confirmed_added", "confirmed_removed"])
def test_absent_structure_local_reason_is_relationship(relationship):
    bundle = make_bundle("s", {1.: [[1, 0]]})
    q, r = (bundle, None) if relationship == "confirmed_added" else (None, bundle)
    pair = make_pair(q, r, relationship=relationship, registration=False)
    result = compare_pair(q, r, pair, RegionalConfig())
    assert next(row for row in result.rows if row.region_id).local_reason == relationship


def test_demo_sources_are_explicit_synthetic_density():
    from scripts.regional_demo import build_demo
    rows = build_demo().source_manifest["sources"]
    assert all(row["kind"] == "explicit_ca_density" and row["semantics"] == "ca_density" for row in rows)


def test_svg_image_has_compatible_xlink_and_shared_crop_defs(tmp_path):
    import xml.etree.ElementTree as ET
    bundles, cfg, pairs = large_case(64, 2)
    write_report(analyze_regions(bundles, cfg, pairs), bundles, tmp_path)
    html = (tmp_path / "regional_report.html").read_text()
    assert '<use href="#plane-' in html
    for path in (tmp_path / "figures/regional").glob("*-crops.svg"):
        root = ET.fromstring(path.read_text())
        for node in root.iter():
            if node.tag.endswith("image"):
                assert node.attrib["href"] == node.attrib["{http://www.w3.org/1999/xlink}href"]


def test_tiny_positive_background_is_full_support():
    from simcompare.regions import build_regions
    bundle = make_bundle("s", {1.: np.ones((3, 5))}, masks={1.: np.full((3, 5), 1e-10)})
    region = build_regions([bundle], RegionalConfig()).regions[0]
    assert (region.bounds_um.x0, region.bounds_um.y0, region.bounds_um.x1, region.bounds_um.y1) == (0., 0., 5., 3.)


def test_shared_report_contract_has_no_function_level_import():
    import ast
    path = Path(__file__).parents[1] / "src/simcompare/regional.py"
    tree = ast.parse(path.read_text())
    for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
        assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(function))


def test_html_share_precision_reason_wrapping_and_absent_context(tmp_path):
    from scripts.regional_demo import build_demo, write_demo_inputs
    from simcompare.regional import write_regional_analysis
    inputs = build_demo()
    cfg = write_demo_inputs(inputs, tmp_path)
    write_regional_analysis(inputs.bundles, cfg, tmp_path)
    html = (tmp_path / "regional_report.html").read_text()
    assert "0.3750" in html
    assert "td{overflow-wrap:normal;word-break:normal}" in html
    assert "<pre>null</pre>" not in html
    import csv
    rows = list(csv.DictReader((tmp_path / "regional_measurements.csv").open()))
    assert any(row.get("contribution_share") == "0.375" for row in rows)


def test_manifest_frame_name_is_provenance_when_image_declares_no_frame(tmp_path):
    from test_regional_sources import manifest
    bundle, path, _ = manifest(tmp_path, frame_id="unit-frame")
    bundle.ca_spatial_sources = load_source_manifest(path, [bundle])
    result = analyze_regions([bundle], RegionalConfig())
    assert result.source_validations[0]["status"] == "available"
    assert result.source_validations[0]["validation"]["provenance"]["frame_id"] == "unit-frame"
    assert all(row.query.roi.roi_ca_um2 == 8 for row in result.comparisons[0].rows if row.region_id)


@pytest.mark.parametrize("change,reason", [({"pixel_size": (1., 2.)}, "unsupported_grid"),
                                         ({"origin_xy": None}, "missing_grid")])
def test_invalid_source_grid_keeps_specific_reason(change, reason):
    bundle = make_bundle("s", {1.: [[1, 0, 1]]})
    source = bundle.ca_spatial_sources[0]
    bundle.ca_spatial_sources = [replace(source, frame=replace(source.frame, **change))]
    result = analyze_regions([bundle], RegionalConfig())
    assert result.source_validations[0]["reason"] == reason
    assert all(row.query.roi_reason == reason for row in result.comparisons[0].rows if row.region_id)


def test_frame_blocked_source_is_unavailable_in_header_and_run_record(tmp_path):
    from simcompare.regional import write_regional_analysis
    bundle = make_bundle("s", {1.: [[1, 0, 1]]})
    source = bundle.ca_spatial_sources[0]
    bundle.ca_spatial_sources = [replace(source, frame=replace(source.frame, origin_xy=(1., 0.)))]
    output = write_regional_analysis([bundle], RegionalConfig(), tmp_path)
    assert output.run_record["source_unavailable"] == 1
    assert output.run_record["unavailable_reasons"] == {"incompatible_source_frame": 1}
    assert output.run_record["source_validations"][0]["reason"] == "incompatible_source_frame"
    html = (tmp_path / "regional_report.html").read_text()
    assert "정량 원천 available 0 · unavailable 1" in html
    assert "incompatible_source_frame" in html


def test_no_window_html_distinguishes_location_by_diameter(tmp_path):
    bundle = make_bundle("s", {1.: [[0, 0]], 2.: [[0, 0]]})
    bundle.hotspots[1].array = None
    cfg = RegionalConfig()
    write_report(analyze_regions([bundle], cfg), [bundle], tmp_path)
    html = (tmp_path / "regional_report.html").read_text()
    assert "s · 지름 1 µm: 관측된 빈 support · 선택 union은 0" in html
    assert "s · 지름 2 µm: 위치 자료 미확인 · 선택 union/창 밖 잔여는 null" in html


def test_bbox_rounding_matches_numpy_mask_for_seven_decimal_pitch():
    from simcompare.variants import _bbox_um, _bounds_um
    mask = np.zeros((128, 128), dtype=bool)
    mask[3:5, 3:5] = True
    expected = _bbox_um(mask, 0.1234567, 0.0)
    assert expected == [0.37037, 0.37037, 0.617284, 0.617284]
    assert _bounds_um((3, 3, 4, 4), mask.shape, 0.1234567, 0.0) == expected
