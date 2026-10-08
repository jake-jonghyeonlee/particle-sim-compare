import numpy as np
import pytest

from simcompare.adapters.example import (
    _apply_vertical_layer,
    _load_design_reviews,
    _load_processed_fab_measurements,
    _validate_image_registration,
)
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
