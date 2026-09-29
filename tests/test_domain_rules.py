import numpy as np
import pandas as pd
import pytest

from simcompare.adapters.example import (
    _apply_vertical_layer,
    _load_design_reviews,
    _load_processed_fab_measurements,
    _validate_image_registration,
)
from simcompare.analysis import held_out_product_evidence, ranking_comparison
from simcompare.caa import FabWeights, compute_caa, fab_shape_and_total
from simcompare.retrieval import candidate_mask
from simcompare.schema import (
    AnalysisBundle,
    CACurve,
    DataContractError,
    FabDistribution,
    ImageInput,
    convert_length,
    validate_curve,
)


def bundle(**changes):
    values = dict(
        sample_id="S", product_id="P", product_family="F", design_version="v1",
        design_lineage_id="L", electrode_a_role="gate", electrode_b_role="data",
        layer_a="M1", layer_b="M2", failure_type="vertical_short", fab_id="FAB",
        unit_width=0.08, unit_height=0.1, physical_length_unit="mm",
    )
    values.update(changes)
    return AnalysisBundle(**values)


def test_length_conversion_and_dimensionless_ca_area_normalization():
    assert convert_length(0.05, "mm", "um") == pytest.approx(50.0)
    curve = CACurve(np.array([0.001]), np.array([0.02]), np.array([True]), "1", "mm")
    item = bundle(ca=curve)
    per_area, reason = item.ca_per_area("um")
    assert reason == "ok"
    assert per_area[0] == pytest.approx(0.02 / (80.0 * 100.0))
    with pytest.raises(DataContractError, match="0 이상 1 이하"):
        validate_curve(CACurve(np.array([1.0]), np.array([1.01]), np.array([True]), "1"))


def test_ranking_is_within_role_and_layer_comparison_group():
    frame = pd.DataFrame([
        dict(sample_id="a", failure_type="lateral_short", role_key="lateral_short|a|b",
             layer_key="M1|M2", comparison_group_id="g1", ca_max=0.01, ca_area_max=0.001,
             caa_actual=1.0, caa_reference=1.0),
        dict(sample_id="b", failure_type="lateral_short", role_key="lateral_short|a|b",
             layer_key="M1|M2", comparison_group_id="g1", ca_max=0.02, ca_area_max=0.002,
             caa_actual=2.0, caa_reference=2.0),
        dict(sample_id="c", failure_type="lateral_short", role_key="lateral_short|x|y",
             layer_key="ITO|M3", comparison_group_id="g2", ca_max=0.5, ca_area_max=0.05,
             caa_actual=50.0, caa_reference=50.0),
    ])
    ranked = ranking_comparison(frame).set_index("sample_id")
    assert ranked.loc["b", "rank_ca_max"] == 1
    assert ranked.loc["a", "rank_ca_max"] == 2
    assert ranked.loc["c", "rank_ca_max"] == 1


def test_size_tolerance_converts_physical_units():
    meta = pd.DataFrame([
        dict(sample_id="q", product_id="P1", failure_type="lateral_open",
             electrode_a_role="data", electrode_b_role="N/A", layer_a="M2", layer_b="N/A",
             unit_width=50.0, unit_height=80.0, physical_length_unit="um"),
        dict(sample_id="n", product_id="P2", failure_type="lateral_open",
             electrode_a_role="data", electrode_b_role="N/A", layer_a="M2", layer_b="N/A",
             unit_width=0.05, unit_height=0.08, physical_length_unit="mm"),
    ])
    assert candidate_mask(meta, 0, size_tolerance=0.0).tolist() == [False, True]


def test_layout_hotspot_registration_is_strict_in_real_mode():
    item = bundle(failure_type="lateral_short")
    item.layouts = [ImageInput("gate", np.zeros((4, 4)), pixel_size=1.0, origin_xy=(0, 0))]
    item.hotspots = [ImageInput("hotspot", np.zeros((5, 4)), pixel_size=1.0,
                                origin_xy=(0, 0), particle_size=1, particle_size_unit="um")]
    with pytest.raises(DataContractError, match="픽셀 1:1 대응"):
        _validate_image_registration(item, "real")
    assert _validate_image_registration(item, "demo")


def test_vertical_short_uses_upper_layer_from_product_stack():
    item = bundle(assigned_process_layer=None)
    stack = {
        ("P", "*", "M1"): (1, "FAB_M1"),
        ("P", "*", "M2"): (2, "FAB_M2"),
    }
    assert _apply_vertical_layer(item, stack, "real") == []
    assert item.assigned_process_layer == "FAB_M2"


def test_average_count_per_glass_is_normalized_by_surveyed_area():
    dist = FabDistribution(
        "D", "FAB", "F", "M2", np.array([0.5, 1.5]), np.array([1.5, 2.5]),
        np.array([100.0, 100.0]), np.array([True, True]), "average_count_per_glass",
        "particles", "um", surveyed_area=1000.0, area_unit="um^2", period="2026-01",
        averaging_basis="per_glass",
    )
    shape, total, unit = fab_shape_and_total(dist)
    assert np.allclose(shape, [0.5, 0.5])
    assert total == pytest.approx(0.2)
    assert unit == "particles/glass/um^2"


def test_processed_fab_csv_and_external_review_inputs(tmp_path):
    fab = tmp_path / "fab_measurements.csv"
    header = (
        "source_id,fab_id,product_family,process_layer,particle_size_um,avg_detected_count,"
        "surveyed_area_um2,averaging_basis,period_start,period_end\n"
    )
    rows = [f"SRC,FAB,F,M2,{size},{100-size},1000,per_glass,2026-01-01,2026-01-31"
            for size in [*range(1, 31), 40, 50]]
    fab.write_text(header + "\n".join(rows) + "\n")
    loaded = _load_processed_fab_measurements(tmp_path, "real")
    assert loaded["SRC"].value_kind == "average_count_per_glass"
    assert loaded["SRC"].averaging_basis == "per_glass"
    assert len(loaded["SRC"].raw_values) == 32

    reviews = tmp_path / "design_review_status.csv"
    reviews.write_text(
        "candidate_id,comparison_group_id,hypothesis,resimulation_status,resistance_status,"
        "capacitance_status,coupling_status,signal_status,design_rule_status,short_open_status,"
        "external_result_ref,status\n"
        "C1,G1,간격 확대,pending,external_review_required,external_review_required,"
        "external_review_required,external_review_required,external_review_required,"
        "external_review_required,,pending\n"
    )
    parsed = _load_design_reviews(tmp_path)
    assert parsed[0]["candidate_id"] == "C1"


def test_caa_matches_particle_diameter_after_unit_conversion():
    ca = CACurve(np.array([0.001, 0.002]), np.array([0.01, 0.02]),
                 np.array([True, True]), "1", "mm")
    weights = FabWeights(np.array([1.0, 2.0]), np.array([0.25, 0.75]), "um",
                         "demo", "D", "probability/bin", True)
    result = compute_caa(ca, weights)
    assert result.value == pytest.approx(0.0175)
    assert result.unit == "relative_index"


def test_held_out_queries_only_use_training_candidates():
    meta = pd.DataFrame([
        dict(sample_id="train", product_id="P1", failure_type="lateral_open",
             electrode_a_role="data", electrode_b_role="N/A", layer_a="M2", layer_b="N/A",
             unit_width=50.0, unit_height=50.0),
        dict(sample_id="test", product_id="P2", failure_type="lateral_open",
             electrode_a_role="data", electrode_b_role="N/A", layer_a="M2", layer_b="N/A",
             unit_width=50.0, unit_height=50.0),
        dict(sample_id="other_test", product_id="P3", failure_type="lateral_open",
             electrode_a_role="data", electrode_b_role="N/A", layer_a="M2", layer_b="N/A",
             unit_width=50.0, unit_height=50.0),
    ])
    ranked = pd.DataFrame([
        dict(sample_id=s, comparison_group_id="g", ca_area_max=v)
        for s, v in (("train", 1.0), ("test", 1.1), ("other_test", 1.2))
    ])
    report = held_out_product_evidence(
        np.array([[0.0], [0.1], [0.11]]), meta, ranked, [0], [1, 2], k=2,
        search_kwargs={"other_products_only": True},
    )
    assert report["n_queries"] == 2
    assert all(row["neighbors"] == ["train"] for row in report["queries"])


@pytest.mark.parametrize("sizes,mode,valid", [
    ([*range(1, 31), 40, 50], "real", True),
    ([*range(1, 30), 40, 50], "real", False),
    ([*range(1, 31), 40], "real", False),
    ([*range(1, 31), 50], "real", False),
    (list(range(1, 51)), "real", False),
    (list(range(1, 51)), "demo", True),
    ([2, 4], "demo", True),
])
def test_processed_fab_size_grid(tmp_path, sizes, mode, valid):
    header = (
        "source_id,fab_id,product_family,process_layer,particle_size_um,avg_detected_count,"
        "surveyed_area_um2,averaging_basis,period_start,period_end\n"
    )
    rows = [f"SRC,FAB,F,M2,{size},1,1000,per_glass,2026-01-01,2026-01-31"
            for size in sizes]
    (tmp_path / "fab_measurements.csv").write_text(header + "\n".join(rows) + "\n")
    if valid:
        dist = _load_processed_fab_measurements(tmp_path, mode)["SRC"]
        np.testing.assert_array_equal((dist.bin_lower + dist.bin_upper) / 2, sizes)
    else:
        with pytest.raises(DataContractError, match="32개.*누락"):
            _load_processed_fab_measurements(tmp_path, mode)


@pytest.mark.parametrize('reverse', [False, True])
def test_f4_vertical_preserves_source_and_reports_geometry(reverse):
    item = bundle(assigned_process_layer='SOURCE', mapping_contract_version='1.2',
                  fab_pair_key='U:M2-M1' if reverse else 'U:M1-M2',
                  layer_a='M2' if reverse else 'M1', layer_b='M1' if reverse else 'M2')
    stack = {('P', '*', 'M1'): (1, 'FAB_LOW'), ('P', '*', 'M2'): (2, 'FAB_HIGH')}
    assert _apply_vertical_layer(item, stack, 'real') == []
    assert item.assigned_process_layer == 'SOURCE'
    assert item.provenance.calc_settings['upper_physical_layer'] == 'M2'
    with pytest.raises(DataContractError):
        _apply_vertical_layer(item, {}, 'real')
    assert _apply_vertical_layer(item, {}, 'demo')
    stack[('P', 'v1', 'M2')] = (1, 'OTHER')
    with pytest.raises(DataContractError):
        _apply_vertical_layer(item, stack, 'real')


def test_ranking_uses_sizes_observed_by_every_member_of_the_group():
    base = dict(failure_type="lateral_short", role_key="r", layer_key="l", comparison_group_id="g",
                caa_actual=np.nan, caa_reference=np.nan)
    frame = pd.DataFrame([
        # a는 16 um가 미관측이라 전체 범위 최댓값으로는 부당하게 낮아진다.
        {**base, "sample_id": "a", "ca@4": 0.5, "ca@16": np.nan, "ca_area@4": 0.05, "ca_area@16": np.nan,
         "ca_max": 0.5, "ca_area_max": 0.05},
        {**base, "sample_id": "b", "ca@4": 0.2, "ca@16": 0.9, "ca_area@4": 0.02, "ca_area@16": 0.09,
         "ca_max": 0.9, "ca_area_max": 0.09},
        # 실제 크기가 없어 면적 CA가 전혀 없는 사례는 면적 공통 크기 계산에서 빠진다.
        {**base, "sample_id": "c", "ca@4": 0.1, "ca@16": 0.3, "ca_area@4": np.nan, "ca_area@16": np.nan,
         "ca_max": 0.3, "ca_area_max": np.nan},
    ])
    ranked = ranking_comparison(frame).set_index("sample_id")
    assert ranked["rank_sizes_ca"].tolist() == ["4"] * 3
    assert ranked["rank_sizes_ca_area"].tolist() == ["4"] * 3
    assert ranked.loc["a", "rank_ca_max"] == 1 and ranked.loc["b", "rank_ca_max"] == 2
    assert ranked.loc["a", "rank_ca_area_max"] == 1 and np.isnan(ranked.loc["c", "rank_ca_area_max"])
    assert ranked.loc["b", "ca_area_max_common"] == pytest.approx(0.02)
    assert ranked.loc["b", "ca_area_max"] == pytest.approx(0.09)  # 전체 범위 값은 그대로 보존
