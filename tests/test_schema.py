import numpy as np
import pytest

from simcompare.schema import (
    NOT_APPLICABLE, AnalysisBundle, CACurve, DataContractError, FabDistribution,
    group_size_records, validate_bundle, validate_fab,
)


def make_bundle(**kw):
    base = dict(
        sample_id="S1", product_id="P1", product_family="F1", design_version="v1",
        design_lineage_id="L1", electrode_a_role="data", electrode_b_role="pixel",
        layer_a="M2", layer_b="ITO", failure_type="lateral_short", fab_id="FAB_A",
        unit_width=50.0, unit_height=80.0, physical_length_unit="um",
    )
    base.update(kw)
    return AnalysisBundle(**base)


def test_open_uses_not_applicable_not_missing():
    b = make_bundle(failure_type="lateral_open", electrode_b_role=NOT_APPLICABLE, layer_b=NOT_APPLICABLE)
    validate_bundle(b, "real")
    assert b.role_pair() == ("data",)
    with pytest.raises(DataContractError):
        validate_bundle(make_bundle(failure_type="lateral_open"), "real")


def test_short_requires_second_electrode():
    with pytest.raises(DataContractError):
        validate_bundle(make_bundle(electrode_b_role=NOT_APPLICABLE), "real")


def test_vertical_short_layer_never_inferred():
    b = make_bundle(failure_type="vertical_short")
    with pytest.raises(DataContractError):
        validate_bundle(b, "real")
    assert any("assigned_process_layer" in w for w in validate_bundle(b, "demo"))


def test_physical_scale_and_ca_per_area():
    curve = CACurve(np.array([1.0, 2.0]), np.array([4.0, np.nan]), np.array([True, False]), "um^2")
    b = make_bundle(ca=curve)
    per_area, reason = b.ca_per_area()
    assert reason == "ok" and per_area[0] == pytest.approx(4.0 / 4000.0) and np.isnan(per_area[1])
    nb = make_bundle(unit_width=None, unit_height=None, physical_length_unit=None, ca=curve)
    assert nb.ca_per_area()[0] is None
    assert any("실제 크기 없음" in w for w in validate_bundle(nb, "real"))


def test_unobserved_must_be_nan_not_zero():
    bad = CACurve(np.array([1.0, 2.0]), np.array([4.0, 0.0]), np.array([True, False]), "um^2")
    with pytest.raises(DataContractError):
        validate_bundle(make_bundle(ca=bad), "real")


def test_group_size_records_keeps_missing_and_detects_duplicates():
    curves = group_size_records([
        {"sample_id": "S1", "particle_size": 2, "ca_value": "", "ca_unit": "um^2"},
        {"sample_id": "S1", "particle_size": 1, "ca_value": "0", "ca_unit": "um^2"},
    ])
    c = curves["S1"]
    assert list(c.particle_sizes) == [1.0, 2.0]
    assert list(c.observed) == [True, False] and c.values[0] == 0.0 and np.isnan(c.values[1])
    with pytest.raises(DataContractError):
        group_size_records([
            {"sample_id": "S1", "particle_size": 1, "ca_value": 1, "ca_unit": "um^2"},
            {"sample_id": "S1", "particle_size": 1, "ca_value": 2, "ca_unit": "um^2"},
        ])


def test_validate_fab_rules():
    d = FabDistribution("D1", "FAB_A", "F1", "M2", np.array([0.5, 1.5]), np.array([1.5, 2.5]),
                        np.array([0.1, np.nan]), np.array([True, False]), "probability", "1", "um")
    validate_fab(d)
    d.bin_upper = np.array([2.0, 2.5])
    with pytest.raises(DataContractError):
        validate_fab(d)
