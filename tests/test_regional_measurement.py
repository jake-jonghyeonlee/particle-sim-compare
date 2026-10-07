"""원본 CA 적분의 보존·단위·결측 반례. IO/비교 정책은 별도 테스트한다."""
from dataclasses import asdict, replace

import numpy as np
import pytest

from simcompare.regional_schema import PhysicalWindow as W, SpatialCASource, SpatialFrame
from simcompare.regional_measurement import measure_window, measure_union, validate_source


def source(density=None, valid=None, **kwargs):
    density = np.array([[1.0, 0.5], [0.0, 1.0]]) if density is None else np.asarray(density)
    values = dict(sample_id="s", diameter_um=2.0, source_kind="particle_sim_mean",
                  source_mode="mean", semantics="probability", ca_source="ca_mean_um2",
                  calculation_contract_id="producer-v1:mean", density=density,
                  valid_mask=np.ones(density.shape, bool) if valid is None else valid,
                  frame=SpatialFrame(2.0, (10.0, 20.0)),
                  total_ca=float(density[(np.ones(density.shape, bool) if valid is None else valid)].sum()) * 4)
    values.update(kwargs)
    return SpatialCASource(**values)


@pytest.mark.parametrize("nominal", [False, True])
def test_full_valid_domain_matches_source_ca(nominal):
    s = source([[1., 0.], [0., 1.]], source_kind="particle_sim_nominal",
               source_mode="nominal", semantics="binary", ca_source="ca_nominal_um2") if nominal else source()
    m = measure_window(s, W(10, 20, 14, 24))
    assert m.roi_ca_um2 == s.total_ca
    assert m.validation.residual_um2 == 0
    assert m.validation.provenance["shape_yx"] == [2, 2]
    assert m.validation.conservation_rtol == 1e-6


def test_conservation_failure_is_unavailable_without_rescaling():
    s = source(total_ca=100)
    before = s.density.copy()
    m = measure_window(s, W(10, 20, 14, 24))
    assert m.reason == "conservation_failed" and m.roi_ca_um2 is None
    assert m.total_ca_um2 == 100 and m.validation.residual_um2 == -90
    np.testing.assert_array_equal(s.density, before)


def test_disjoint_partition_conserves_same_source():
    s = source(valid=np.array([[True, False], [True, True]]))
    rows = [measure_window(s, W(10, 20, 12, 24)), measure_window(s, W(12, 20, 14, 24))]
    assert sum(r.roi_ca_um2 for r in rows) == s.total_ca
    assert sum(r.valid_roi_area_um2 for r in rows) == 12


def test_overlapping_windows_integrate_union_once():
    s = source()
    a, b = W(10, 20, 13, 24), W(11, 21, 14, 24)
    union = measure_union(s, [a, b, a])
    assert union.roi_ca_um2 == pytest.approx(9.5)
    assert union.roi_ca_um2 < measure_window(s, a).roi_ca_um2 + measure_window(s, b).roi_ca_um2
    assert union.valid_roi_area_um2 == 15


def test_comparison_never_sums_pair_ca_into_product_total():
    # T1은 source별 측정만 반환한다. pair 간 product 집계 API/필드는 제공하지 않는다.
    a, b = source(), source(sample_id="other-pair")
    measurements = [measure_window(s, W(10, 20, 14, 24)) for s in (a, b)]
    assert [m.total_ca_um2 for m in measurements] == [10, 10]
    assert all("product_total_ca_um2" not in asdict(m) for m in measurements)
    from simcompare.schema import DataContractError
    with pytest.raises(DataContractError, match="one source"):
        measure_union([a, b], [W(10, 20, 14, 24)])


def test_nm_mm_area_and_nonzero_origin_conversion():
    s = source(frame=SpatialFrame(2000, (10000, 20000), "nm"), total_ca=1e7, total_ca_unit="nm^2")
    m = measure_window(s, W(.010, .020, .014, .024, "mm"))
    assert m.roi_ca_um2 == pytest.approx(10)
    assert m.total_ca_um2 == pytest.approx(10)
    assert m.requested_windows_um == (W(10, 20, 14, 24),)
    assert measure_window(replace(s, total_ca=1e-5, total_ca_unit="mm^2"), W(10, 20, 14, 24)).roi_ca_um2 == pytest.approx(10)


def test_fractional_boundary_crop_and_valid_mask():
    s = source(valid=np.array([[True, False], [True, True]]))
    m = measure_window(s, W(9, 19, 13, 23), include_area_normalized=True)
    assert m.roi_ca_um2 == 5  # 4×1 + 1×1, top-right valid hole excluded
    assert m.valid_roi_area_um2 == 7
    assert m.clipped_bounds_um == W(10, 20, 13, 23)
    assert m.roi_ca_per_valid_area == pytest.approx(5/7)


def test_outside_domain_is_not_risk_zero():
    assert measure_window(source(), W(14, 20, 15, 24)).reason == "empty_valid_area"
    assert measure_window(source(), W(14, 20, 15, 24)).roi_ca_um2 is None


def test_anisotropic_grid_is_explicitly_unsupported():
    assert validate_source(source(frame=SpatialFrame((1, 2), (0, 0)))).reason == "unsupported_grid"


def test_missing_origin_does_not_default_to_zero():
    assert validate_source(source(frame=SpatialFrame(2, None))).reason == "missing_grid"


@pytest.mark.parametrize("pitch", [-1, 0, float("inf"), float("nan")])
def test_invalid_pitch(pitch):
    assert validate_source(source(frame=SpatialFrame(pitch, (0, 0)))).reason == "invalid_grid"


@pytest.mark.parametrize("valid", [np.ones((1, 2), bool), np.ones((2, 2), float)])
def test_invalid_mask_shape_or_type(valid):
    assert validate_source(source(valid_mask=valid)).reason == "invalid_valid_mask"


def test_raw_share_and_density_have_distinct_denominators():
    m = measure_window(source(), W(10, 20, 12, 22), include_area_normalized=True)
    assert (m.roi_ca_um2, m.total_ca_um2, m.valid_roi_area_um2) == (4, 10, 4)
    assert m.contribution_share == .4 and m.roi_ca_per_valid_area == 1


def test_zero_total_share_and_empty_valid_area_are_null():
    s = source(np.zeros((2, 2)))
    m = measure_window(s, W(10, 20, 14, 24), include_area_normalized=True)
    assert m.roi_ca_um2 == 0 and m.contribution_share is None and m.share_reason == "zero_total"
    assert m.roi_ca_per_valid_area == 0
    empty = measure_window(replace(s, valid_mask=np.zeros((2, 2), bool)), W(10, 20, 14, 24), include_area_normalized=True)
    assert empty.roi_ca_um2 is None and empty.roi_ca_per_valid_area is None


def test_normalized_ratios_are_not_aggregated():
    s = source()
    windows = [W(10, 20, 12, 22), W(12, 20, 14, 24)]
    rows = [measure_window(s, w, include_area_normalized=True) for w in windows]
    union = measure_union(s, windows, include_area_normalized=True)
    assert union.roi_ca_per_valid_area == pytest.approx(10/12)
    assert union.roi_ca_per_valid_area != sum(m.roi_ca_per_valid_area for m in rows)


def test_measurement_preserves_arrays_and_provenance():
    s = source(provenance={"settings": {"nested": [1]}})
    density, valid = s.density.copy(), s.valid_mask.copy()
    m = measure_union(s, [W(10, 20, 13, 24), W(11, 20, 14, 23)])
    m.validation.provenance["settings"]["nested"].append(2)
    np.testing.assert_array_equal(s.density, density)
    np.testing.assert_array_equal(s.valid_mask, valid)
    assert s.provenance == {"settings": {"nested": [1]}}
