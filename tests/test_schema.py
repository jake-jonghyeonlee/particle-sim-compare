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
    curve = CACurve(np.array([1.0, 2.0]), np.array([0.02, np.nan]), np.array([True, False]), "1")
    b = make_bundle(ca=curve)
    per_area, reason = b.ca_per_area()
    assert reason == "ok" and per_area[0] == pytest.approx(0.02 / 4000.0) and np.isnan(per_area[1])
    nb = make_bundle(unit_width=None, unit_height=None, physical_length_unit=None, ca=curve)
    assert nb.ca_per_area()[0] is None
    assert any("실제 크기 없음" in w for w in validate_bundle(nb, "real"))


def test_unobserved_must_be_nan_not_zero():
    bad = CACurve(np.array([1.0, 2.0]), np.array([0.02, 0.0]), np.array([True, False]), "1")
    with pytest.raises(DataContractError):
        validate_bundle(make_bundle(ca=bad), "real")


def test_group_size_records_keeps_missing_and_detects_duplicates():
    curves = group_size_records([
        {"sample_id": "S1", "particle_size": 2, "particle_size_unit": "um", "ca_value": "", "ca_unit": "1"},
        {"sample_id": "S1", "particle_size": 1, "particle_size_unit": "um", "ca_value": "0", "ca_unit": "1"},
    ])
    c = curves["S1"]
    assert list(c.particle_sizes) == [1.0, 2.0]
    assert list(c.observed) == [True, False] and c.values[0] == 0.0 and np.isnan(c.values[1])
    with pytest.raises(DataContractError):
        group_size_records([
            {"sample_id": "S1", "particle_size": 1, "particle_size_unit": "um", "ca_value": 0.1, "ca_unit": "1"},
            {"sample_id": "S1", "particle_size": 1, "particle_size_unit": "um", "ca_value": 0.2, "ca_unit": "1"},
        ])


def test_validate_fab_rules():
    d = FabDistribution("D1", "FAB_A", "F1", "M2", np.array([0.5, 1.5]), np.array([1.5, 2.5]),
                        np.array([0.1, np.nan]), np.array([True, False]), "probability", "1", "um")
    validate_fab(d)
    d.bin_upper = np.array([2.0, 2.5])
    with pytest.raises(DataContractError):
        validate_fab(d)


def test_f4_fixture_key_and_marker_conformance():
    import json
    from pathlib import Path
    import pytest
    from simcompare.schema import AnalysisBundle, DataContractError, make_fab_pair_key, validate_mapping_contract
    contract = json.loads((Path(__file__).parent / 'fixtures/particle_sim_mapping_v1.json').read_text())
    for vector in contract['fab_pair_key']['vectors']:
        assert make_fab_pair_key(*vector['raw_tuple'], {}) == vector['key']
    for tuples in contract['fab_pair_key']['collision_vectors']:
        registry = {}
        make_fab_pair_key(*tuples[0], registry)
        with pytest.raises(DataContractError, match='collision'):
            make_fab_pair_key(*tuples[1], registry)
    for value in contract['fab_pair_key']['invalid_components']:
        with pytest.raises(DataContractError):
            make_fab_pair_key(value, 'A', 'B', {})
    for vector in contract['version_marker']['vectors']:
        bundle = AnalysisBundle('S', 'P', 'F', 'V', 'L', 'a', 'b', 'A', 'B', 'lateral_short', 'FAB',
                                assigned_process_layer=vector.get('assigned_process_layer'),
                                mapping_contract_version=vector['marker'], fab_pair_key=vector.get('pair_key'))
        if vector['result'] == 'error':
            with pytest.raises(DataContractError):
                validate_mapping_contract(bundle)
        else:
            validate_mapping_contract(bundle)


@pytest.mark.parametrize("unit,value", [("um^2", 2.0), ("nm^2", 2e6), ("mm^2", 2e-6)])
def test_area_ca_normalizes_once(unit, value):
    curve = CACurve(np.array([1.0, 2.0]), np.array([value, np.nan]), np.array([True, False]), unit)
    large = make_bundle(ca=curve, unit_width=100.0, unit_height=100.0)
    small = make_bundle(ca=curve, unit_width=50.0, unit_height=50.0)
    validate_bundle(large)
    a = large.ca_per_area()[0]
    b = small.ca_per_area()[0]
    assert a[0] == pytest.approx(2.0 / 10000, rel=1e-12)
    assert b[0] / a[0] == pytest.approx(4.0, rel=1e-12)
    assert np.isnan(a[1])
    assert large.ca_area_unit() == "1"
    assert large.ca_per_area("nm")[0][0] == pytest.approx(a[0], rel=1e-12)


@pytest.mark.parametrize("unit", ["bad", "um", "1/um^2"])
def test_invalid_ca_unit(unit):
    curve = CACurve(np.array([1.0]), np.array([0.1]), np.array([True]), unit)
    with pytest.raises(DataContractError):
        validate_bundle(make_bundle(ca=curve))


def test_legacy_ca_area_unit():
    curve = CACurve(np.array([1.0]), np.array([0.1]), np.array([True]), "1")
    assert make_bundle(ca=curve).ca_area_unit() == "1/um^2"
