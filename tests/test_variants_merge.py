import csv
import json

import numpy as np
import pandas as pd
import pytest

from simcompare.analysis import ranking_comparison, risk_table
from simcompare.cli import main
from simcompare.merge import merge_exports
from simcompare.schema import AnalysisBundle, DataContractError, ImageInput
from simcompare.variants import edit_candidates, variant_comparison

SPEC_KEYS = {"schema_version", "length_unit", "note", "candidates"}
CANDIDATE_KEYS = {"candidate_id", "sample_id", "change_description", "edits", "basis", "info"}
EDIT_KEYS = {"op", "slot", "target_slot", "roi_um", "amount_um"}


def _bundle(sid, failure, a, b, hotspot=None):
    layouts = [ImageInput("a", a.astype(np.float32), pixel_size=0.5),
               ImageInput("b", b.astype(np.float32), pixel_size=0.5)]
    hotspots = [] if hotspot is None else [
        ImageInput("hot", hotspot.astype(np.float32), pixel_size=0.5, particle_size=4.0,
                   particle_size_unit="um", value_semantics="processed_intensity")]
    return AnalysisBundle(
        sample_id=sid, product_id="PX", product_family="F", design_version="v1",
        design_lineage_id="L", electrode_a_role="data", electrode_b_role="pixel", layer_a="M2",
        layer_b="ITO", failure_type=failure, fab_id="FAB", unit_width=20.0, unit_height=20.0,
        physical_length_unit="um", layouts=layouts, hotspots=hotspots)


def _rows(query, failure, feature, qv, rv):
    comparison = pd.DataFrame([{"query_id": query, "reference_id": "REF", "failure_type": failure,
                                "status": "ok", "comparison_group_id": "g", "compared_sizes": "4"}])
    deltas = pd.DataFrame([{"query_id": query, "reference_id": "REF", "comparison_group_id": "g",
                            "failure_type": failure, "feature": feature, "query_value": qv,
                            "reference_value": rv, "delta": rv - qv,
                            "review_direction": "increase" if rv > qv else "decrease"}])
    return comparison, deltas


def test_lateral_short_candidates_follow_particle_sim_spec():
    a = np.zeros((40, 40), bool)
    a[:, 5:9] = True
    b = np.zeros((40, 40), bool)
    b[:, 12:20] = True
    heat = np.zeros((40, 40))
    heat[18:22, 9:12] = 1.0  # 간격 가운데 좁은 hotspot
    bundle = _bundle("PX-v1-S-A__B", "lateral_short", a, b, heat)
    spec, table = edit_candidates(*_rows(bundle.sample_id, "lateral_short", "pair:min_gap", 1.5, 3.5),
                                  [bundle])
    assert set(spec) == SPEC_KEYS and spec["schema_version"] == "1.0" and spec["length_unit"] == "um"
    candidates = spec["candidates"]
    # 전극 2개 x 양 2개(차이의 0.5배, 1배)
    assert len(candidates) == 4
    assert [c["candidate_id"] for c in candidates] == [f"PX-v1-S-A__B-e{i}" for i in (1, 2, 3, 4)]
    for candidate in candidates:
        assert set(candidate) == CANDIDATE_KEYS and set(candidate["edits"][0]) == EDIT_KEYS
        assert candidate["info"]["decision"] == "designer_judgement"
    edits = [c["edits"][0] for c in candidates]
    assert [(e["slot"], e["target_slot"], e["amount_um"]) for e in edits] == [
        ("b", "a", 1.0), ("b", "a", 2.0), ("a", "b", 1.0), ("a", "b", 2.0)]
    # hotspot 영역(열 9..11, 행 18..21)에 이물 크기 4 um 여유를 더한 ROI
    assert edits[0]["roi_um"] == [0.5, 5.0, 10.0, 15.0]
    assert set(table["roi_source"]) == {"hotspot@4"}


def test_roi_falls_back_to_closest_zone_and_skips_are_reported():
    a = np.zeros((40, 40), bool)
    a[10:14, :] = True
    b = np.zeros((40, 40), bool)
    b[:, 20:24] = True
    vertical = _bundle("PX-v1-S-G__P", "vertical_short", a, b)
    spec, table = edit_candidates(*_rows(vertical.sample_id, "vertical_short", "pair:overlap_area", 4.0, 1.0),
                                  [vertical])
    edits = [c["edits"][0] for c in spec["candidates"]]
    assert [e["op"] for e in edits] == ["remove_overlap", "remove_overlap"]
    assert all(e["amount_um"] == 0.0 for e in edits)
    assert edits[0]["roi_um"] == [6.0, 1.0, 16.0, 11.0]  # 겹침(행 10..13, 열 20..23) + 4 um
    assert set(table["roi_source"]) == {"closest_zone"}

    comparison = pd.concat([
        _rows("PX-v1-S-OPEN", "lateral_open", "pair:a_min_width", 2.0, 3.0)[0],
        _rows(vertical.sample_id, "vertical_short", "pair:overlap_area", 1.0, 4.0)[0],
    ], ignore_index=True)
    deltas = _rows(vertical.sample_id, "vertical_short", "pair:overlap_area", 1.0, 4.0)[1]
    spec, table = edit_candidates(comparison, deltas, [vertical])
    assert spec["candidates"] == []
    reasons = table.set_index("query_id")["reason"]
    assert "보강" in reasons["PX-v1-S-OPEN"] and "겹침이 크지 않습니다" in reasons[vertical.sample_id]


def test_variant_comparison_on_demo_lineage(demo_loaded, demo_cfg):
    ranked = ranking_comparison(risk_table(demo_loaded.bundles, demo_loaded.distributions, demo_cfg))
    table = variant_comparison(ranked, demo_loaded.bundles).set_index("variant_id")
    # 합성 P1-v2(간격 확대)는 원안 P1-v1과 불량 종류별로 짝지어진다.
    assert set(table.index) == {"P1-v2-OPEN", "P1-v2-LSHORT", "P1-v2-VSHORT"}
    row = table.loc["P1-v2-LSHORT"]
    assert row["parent_id"] == "P1-v1-LSHORT" and row["status"] == "ok"
    assert row["direction"] == "decrease" and row["delta"] == pytest.approx(
        row["variant_ca_area"] - row["parent_ca_area"])
    orphan = next(b for b in demo_loaded.bundles if b.sample_id == "P1-v2-OPEN")
    orphan.parent_design_id = "P9-v1"
    try:
        again = variant_comparison(ranked, demo_loaded.bundles).set_index("variant_id")
        assert again.loc["P1-v2-OPEN", "status"] == "no_parent_match"
    finally:
        orphan.parent_design_id = "P1-v1"


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


def test_binary_roi_considers_large_component_after_small_raster_first_component():
    from simcompare.variants import _roi

    heat = np.zeros((80, 80), dtype=np.float32)
    heat[1:3, 1:3] = 1
    heat[50:54, 50:55] = 1
    edited = np.zeros_like(heat, dtype=bool)
    edited[51, 52] = True
    other = np.zeros_like(edited)
    other[51, 60] = True
    bundle = _bundle("binary", "lateral_short", edited, other, heat)
    roi, source = _roi(bundle, [4.0], 0.5, edited, other, edited)
    assert source == "hotspot@4"
    assert roi == [21.0, 21.0, 31.5, 31.0]


def test_graded_roi_keeps_first_max_component_and_legacy_fallback():
    from simcompare.variants import _roi, _bbox_um, _closest_zone

    heat = np.zeros((80, 80), dtype=np.float32)
    heat[1:3, 1:3] = 1
    heat[50:54, 50:55] = 0.75
    edited = np.zeros_like(heat, dtype=bool)
    edited[51, 52] = True
    other = np.zeros_like(edited)
    other[51, 60] = True
    bundle = _bundle("graded", "lateral_short", edited, other, heat)
    roi, source = _roi(bundle, [4.0], 0.5, edited, other, edited)
    assert source == "closest_zone"
    assert roi == _bbox_um(_closest_zone(edited, other), 0.5, 4.0)


def test_binary_roi_checkerboard_has_linear_memory():
    import tracemalloc
    from simcompare.variants import _roi

    rows, cols = np.indices((128, 128))
    heat = ((rows + cols) % 2 == 0).astype(np.float32)
    edited = np.zeros_like(heat, dtype=bool)
    edited[0, 0] = True
    bundle = _bundle("checkerboard", "lateral_short", edited, edited, heat)
    tracemalloc.start()
    try:
        result = _roi(bundle, [4.0], 0.5, edited, edited, edited)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result == ([0.0, 0.0, 4.5, 4.5], "hotspot@4")
    # 8192개 성분의 전체 마스크만 128 MiB다. 16 MiB 제한은 메타데이터·누적합에는 충분하다.
    assert peak < 16 * 1024 * 1024, peak


def _reference_binary_roi(bundle, edited):
    from simcompare.variants import _bbox_um, _contains, _flood, _closest_zone

    remaining = bundle.hotspots[0].array > 0
    components = []
    while remaining.any():
        first = tuple(np.argwhere(remaining)[0])
        component = _flood(remaining, first)
        components.append(component)
        remaining[component] = False
    components.sort(key=lambda component: (-int(component.sum()), tuple(np.argwhere(component)[0])))
    for component in components:
        roi = _bbox_um(component, 0.5, 4.0)
        if _contains(roi, edited, 0.5):
            return roi, "hotspot@4"
    return _bbox_um(_closest_zone(edited, edited), 0.5, 4.0), "closest_zone"


@pytest.mark.parametrize("seed", range(8))
def test_binary_roi_matches_small_random_reference_with_area_ties(seed):
    from simcompare.variants import _roi

    rng = np.random.default_rng(seed)
    heat = (rng.random((32, 32)) < 0.12).astype(np.float32)
    edited = rng.random(heat.shape) < (0.02 if seed % 2 else 0)
    bundle = _bundle("random", "lateral_short", edited, edited, heat)
    assert _roi(bundle, [4.0], 0.5, edited, edited, edited) == _reference_binary_roi(bundle, edited)


@pytest.mark.parametrize("pixel_um", [0.1, 0.5, 1.3])
def test_prefix_contains_matches_legacy_at_random_and_exact_center_bounds(pixel_um):
    from simcompare.variants import _contains, _contains_prefix, _edited_prefix

    rng = np.random.default_rng(715)
    edited = rng.random((17, 23)) < 0.08
    prefix, xs, ys = _edited_prefix(edited, pixel_um)
    bounds = []
    for _ in range(100):
        x0, x1 = sorted(rng.uniform(-pixel_um, 25 * pixel_um, 2))
        y0, y1 = sorted(rng.uniform(-pixel_um, 19 * pixel_um, 2))
        bounds.append([x0, y0, x1, y1])
    # 닫힌 경계가 픽셀 중심에 정확히 겹쳐도 기존 포함 판정과 같아야 한다.
    for y, x in ((0, 0), (8, 11), (16, 22)):
        bounds.extend([[xs[x], ys[y], xs[x], ys[y]], [xs[0], ys[0], xs[x], ys[y]]])
    for roi in bounds:
        assert _contains_prefix(roi, prefix, xs, ys) == _contains(roi, edited, pixel_um)
