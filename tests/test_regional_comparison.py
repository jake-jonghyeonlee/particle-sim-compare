"""크기별 증감·missing/zero·등록·domain·source 정의와 union 잔여를 검증한다."""
import copy
from dataclasses import replace

import numpy as np
import pytest

from simcompare.config import RegionalConfig
from simcompare.schema import DataContractError
from simcompare.regional_comparison import compare_pair, rank_rows, summarize_source
from simcompare.regional_measurement import measure_window
from simcompare.regions import build_regions
from simcompare.correspondence import parse_correspondence
from fixtures.regional.builders import make_bundle, make_pair, pair_payload


def compare(q, r, **kwargs):
    return compare_pair(q, r, make_pair(q, r, **kwargs), RegionalConfig())


def locals_(result):
    return [row for row in result.rows if row.region_id is not None]


def first_local(result):
    first_id = result.region_sets[0].regions[0].region_id
    return next(row for row in result.rows if row.region_id == first_id)


def test_local_worsening_survives_total_improvement():
    q = make_bundle("q", {2:[[.3, 0, 0, 0, .5]]}, pitch=10)
    r = make_bundle("r", {2:[[.2, 0, 0, 0, .8]]}, pitch=10)
    result = compare(q, r)
    row = first_local(result)
    assert (row.query.unit_total_ca_um2, row.reference.unit_total_ca_um2) == (80, 100)
    assert (row.query.roi.roi_ca_um2, row.reference.roi.roi_ca_um2) == (30, 20)
    assert row.delta_total_ca_um2 == -20 and row.delta_roi_ca_um2 == 10
    assert "local_ca_increased@2um" in row.reasons
    assert "total_ca_increased@2um" not in row.reasons


def test_crossing_diameter_curves_retain_each_change():
    q = make_bundle("q", {1:[[.1, 0, 0, .4]], 2:[[.4, 0, 0, .1]]}, pitch=10)
    r = make_bundle("r", {1:[[.2, 0, 0, .3]], 2:[[.2, 0, 0, .3]]}, pitch=10)
    result = compare(q, r)
    rid = result.region_sets[0].regions[0].region_id
    rows = [row for row in result.rows if row.region_id == rid]
    assert [row.diameter_um for row in rows] == [1, 2]
    assert [row.delta_roi_ca_um2 for row in rows] == [-10, 20]
    assert len(result.rows) == 6  # two local regions + global, at both sizes


def test_ca_levels_and_deltas_drive_order_not_overlap_or_peak():
    q = make_bundle("q", {1:[[.1, 0, 0, .4]]}, pitch=10, masks={1:[[1, 0, 0, .001]]})
    r = make_bundle("r", {1:[[.05, 0, 0, .2]]}, pitch=10)
    result = compare(q, r)
    rows = locals_(result)
    ranked = rank_rows(rows, "roi_level")
    assert [row.query.roi.roi_ca_um2 for row in ranked] == [40, 10]
    assert [row.delta_roi_ca_um2 for row in rank_rows(rows, "roi_increase")] == [20, 5]
    q.hotspots[0].array *= np.array([[.001, 1, 1, 1000]])
    q.geometry = None
    reranked = rank_rows(locals_(compare(q, r)), "roi_level")
    assert [row.region_id for row in ranked] == [row.region_id for row in reranked]


@pytest.mark.parametrize("missing", ["size", "map", "reference"])
def test_missing_reference_size_or_map_never_becomes_zero(missing):
    q = make_bundle("q", {1:[[1, 0]], 2:[[1, 0]]})
    r = make_bundle("r", {1:[[1, 0]], 2:[[1, 0]]})
    if missing == "size":
        r.ca.particle_sizes, r.ca.values, r.ca.observed = np.array([1.]), np.array([1.]), np.array([True])
        r.ca_spatial_sources = r.ca_spatial_sources[:1]
        r.hotspots = r.hotspots[:1]
    elif missing == "map":
        r.ca_spatial_sources = []
    if missing == "reference":
        result = compare_pair(q, None, None, RegionalConfig())
    else:
        result = compare(q, r)
    row = next(row for row in locals_(result) if row.diameter_um == 2)
    assert row.reference.roi is None and row.delta_roi_ca_um2 is None
    assert row.query.roi.roi_ca_um2 == 1
    if missing != "map":
        assert row.reference.unit_total_ca_um2 is None
    else:
        assert row.reference.unit_total_ca_um2 == 1


def test_observed_zero_and_empty_hotspot_differ_from_missing():
    q = make_bundle("q", {1:[[1, 0]]})
    r = make_bundle("r", {1:[[0, 0]]})
    row = first_local(compare(q, r))
    assert row.reference.location_status == "observed_empty"
    assert row.reference.roi.roi_ca_um2 == 0 and row.reference.unit_total_ca_um2 == 0
    r.ca.observed[0] = False
    r.ca_spatial_sources = []
    missing = first_local(compare(q, r))
    assert missing.reference.unit_total_ca_um2 is None and missing.reference.roi is None


def test_zero_reference_has_absolute_delta_without_infinity():
    q = make_bundle("q", {1:[[1, 0]]})
    r = make_bundle("r", {1:[[0, 0]]})
    row = first_local(compare(q, r))
    assert row.delta_roi_ca_um2 == 1 and row.relative_delta_roi is None
    assert row.delta_total_ca_um2 == 1 and row.relative_delta_total is None
    assert "new_risk@1um" in row.reasons


@pytest.mark.parametrize("relationship", ["confirmed_added", "confirmed_removed"])
def test_confirmed_structure_addition_retains_own_risk_without_fake_baseline(relationship):
    b = make_bundle("existing", {1:[[1, 0]]})
    q, r = (b, None) if relationship == "confirmed_added" else (None, b)
    result = compare(q, r, relationship=relationship)
    row = locals_(result)[0]
    present = row.query if q else row.reference
    absent = row.reference if q else row.query
    assert present.roi.roi_ca_um2 == 1 and present.unit_total_ca_um2 == 1
    assert absent.unit_total_ca_um2 is None and absent.roi is None
    assert row.delta_roi_ca_um2 is None and row.delta_total_ca_um2 is None
    assert row.total_reason == relationship
    context = result.correspondence.reference_context if q else result.correspondence.query_context
    assert context["product_id"].startswith("declared-")
    assert not any("new_risk" in reason or "risk_disappeared" in reason for reason in row.reasons)


def test_risk_disappearance_requires_observed_zero_in_same_structure():
    q = make_bundle("q", {1:[[0, 0]]})
    r = make_bundle("r", {1:[[1, 0]]})
    row = first_local(compare(q, r))
    assert row.delta_roi_ca_um2 == -1 and "risk_disappeared@1um" in row.reasons
    unknown = compare(q, r, relationship="unknown")
    assert all(not any("risk_disappeared" in reason for reason in row.reasons) for row in unknown.rows)
    assert all(row.delta_roi_ca_um2 is None for row in unknown.rows)


def test_equal_shapes_do_not_imply_registration():
    q = make_bundle("q", {1:[[1, 0]]})
    r = make_bundle("r", {1:[[.5, 0]]})
    result = compare(q, r, registration=False)
    assert result.registration_reason == "unregistered" and len(result.region_sets) == 2
    assert result.rows[0].delta_total_ca_um2 == .5
    assert all(row.delta_roi_ca_um2 is None for row in result.rows)
    assert all("source_unavailable" not in row.reasons for row in result.rows)


def test_translation_with_nonzero_origin_aligns_common_window():
    q = make_bundle("q", {1:[[0, 1]]}, origin=(10, 20))
    r = make_bundle("r", {1:[[0, .5]]}, origin=(30, 40))
    result = compare(q, r, reference_translation=(-20, -20))
    assert len(result.region_sets) == 1
    region = result.region_sets[0].regions[0]
    assert (region.bounds_um.x0, region.bounds_um.y0) == (11, 20)
    row = first_local(result)
    assert row.delta_roi_ca_um2 == .5 and row.local_reason is None
    assert q.ca_spatial_sources[0].frame.origin_xy == (10, 20)
    assert r.ca_spatial_sources[0].frame.origin_xy == (30, 40)
    reversed_result = compare(r, q, query_translation=(-20, -20))
    assert result.region_sets[0].regions == reversed_result.region_sets[0].regions
    assert first_local(reversed_result).delta_roi_ca_um2 == -.5


@pytest.mark.parametrize("case", ["subpixel", "pitch", "near_pitch"])
def test_subpixel_or_different_pitch_blocks_local_delta(case):
    q = make_bundle("q", {1:[[1, 0]]})
    pitches = {"pitch":2, "near_pitch":1+1e-10, "subpixel":1}
    pitch = pitches[case]
    r = make_bundle("r", {1:[[1, 0]]}, pitch=pitch,
                    origin=(.25, 0) if case == "subpixel" else (0, 0))
    result = compare(q, r)
    assert result.registration_reason == "incompatible_comparison_grid"
    assert len(result.region_sets) == 2 and all(row.delta_roi_ca_um2 is None for row in result.rows)
    assert all(row.query.roi or row.reference.roi for row in locals_(result))


def test_different_valid_coverage_blocks_local_delta_without_dropping_hotspot():
    q = make_bundle("q", {1:[[0, 1, 0]]})
    r = make_bundle("r", {1:[[0, 1, 0]]}, valid=[[True, False, True]])
    result = compare(q, r)
    row = first_local(result)
    assert row.local_reason == "incomparable_domain" and row.delta_roi_ca_um2 is None
    assert row.query.roi.roi_ca_um2 == 1 and row.reference.roi.roi_ca_um2 is None
    assert result.region_sets[0].regions[0].bounds_um.x0 == 1


def test_identical_boundary_clipping_remains_comparable():
    # 동일 shape의 valid 경계에서 clip한다. 다른 shape 이미지/source는 새 frame gate가 거부한다.
    q = make_bundle("q", {1:[[0, 1, 0]]}, masks={1:[[0, 1, 1]]}, valid=[[True, True, False]])
    r = make_bundle("r", {1:[[0, .5, 0]]}, masks={1:[[0, 1, 1]]}, valid=[[True, True, False]])
    row = first_local(compare(q, r))
    assert row.local_reason is None and row.delta_roi_ca_um2 == .5
    assert row.query.roi.requested_windows_um[0].x1 == 3
    assert row.query.roi.clipped_bounds_um.x1 == 3 and row.query.roi.valid_roi_area_um2 == 1


def test_equal_valid_area_with_different_hole_locations_is_incomparable():
    q = make_bundle("q", {1:[[1, 0, 1]]}, valid=[[True, False, True]], masks={1:[[1, 1, 1]]})
    r = make_bundle("r", {1:[[0, 1, 1]]}, valid=[[False, True, True]], masks={1:[[1, 1, 1]]})
    row = first_local(compare(q, r))
    assert row.query.roi.valid_roi_area_um2 == row.reference.roi.valid_roi_area_um2 == 2
    assert row.local_reason == "incomparable_domain" and row.delta_roi_ca_um2 is None


@pytest.mark.parametrize("case", ["mode", "contract", "version", "assumptions", "missing_setting"])
def test_incompatible_source_semantics_blocks_delta_even_when_numbers_match(case):
    q = make_bundle("q", {1:[[1, 0]]})
    r = make_bundle("r", {1:[[1, 0]]})
    s = r.ca_spatial_sources[0]
    if case == "mode":
        s = replace(s, source_mode="mean", ca_source="ca_mean_um2")
    elif case == "contract":
        s = replace(s, calculation_contract_id="other")
    elif case == "version":
        s.provenance["producer_version"] = "other"
    elif case == "assumptions":
        s.provenance["settings"]["assumption_set"] = "other"
    else:
        del s.provenance["settings"]["post_step_policy"]
    r.ca_spatial_sources[0] = s
    row = first_local(compare(q, r))
    assert row.query.roi.roi_ca_um2 == row.reference.roi.roi_ca_um2 == 1
    assert row.delta_roi_ca_um2 is None and row.delta_total_ca_um2 is None
    assert row.local_reason in ("incompatible_source_semantics", "unconfirmed_source_definition")


def test_design_config_hash_change_alone_does_not_block_comparison():
    q = make_bundle("q", {1:[[1, 0]]})
    r = make_bundle("r", {1:[[.5, 0]]})
    assert q.ca_spatial_sources[0].provenance["settings"]["config_hash"] != r.ca_spatial_sources[0].provenance["settings"]["config_hash"]
    row = first_local(compare(q, r))
    assert row.total_reason is None and row.local_reason is None and row.delta_roi_ca_um2 == .5


def test_disjoint_final_windows_plus_outside_residual_equal_full_domain_total():
    b = make_bundle("s", {1:[[.2, 0, 0, .8]]}, pitch=10)
    cfg = RegionalConfig()
    regions = build_regions([b], cfg).regions
    windows = [r.bounds_um for r in regions]
    summary = summarize_source(b.ca_spatial_sources[0], windows, cfg)
    assert sum(measure_window(b.ca_spatial_sources[0], w).roi_ca_um2 for w in windows)+summary.outside_regions_ca_um2 == 100
    assert summary.outside_regions_ca_um2 == 0
    # 소스에는 CA가 있으나 위치 근거에 누락되면 잔여를 숨기지 않는다.
    b.hotspots[0].array[0, 3] = 0
    own = compare_pair(b, None, None, cfg)
    summary = own.source_summaries[0]
    assert summary.selected_union_ca_um2 == 20 and summary.outside_regions_ca_um2 == 80


def test_comparison_never_sums_pair_ca_into_product_total():
    q = make_bundle("q", {1:[[1, 0]]})
    r = make_bundle("r", {1:[[1, 0]]})
    result = compare(q, r)
    assert len(result.source_summaries) == 2
    assert [s.unit_total_ca_um2 for s in result.source_summaries] == [1, 1]
    assert not hasattr(result, "product_total_ca_um2")
    with pytest.raises(DataContractError, match="one source"):
        summarize_source(q.ca_spatial_sources + r.ca_spatial_sources,
                         [region.bounds_um for region in result.region_sets[0].regions], RegionalConfig())


def test_pair_reversal_keeps_ids_and_reverses_deltas_without_mutation():
    q = make_bundle("q", {1:[[1, 0]]})
    r = make_bundle("r", {1:[[.5, 0]]})
    before = copy.deepcopy((q, r))
    a, b = compare(q, r), compare(r, q)
    assert a.region_sets[0].regions == b.region_sets[0].regions
    assert first_local(a).delta_roi_ca_um2 == -first_local(b).delta_roi_ca_um2
    for original, snapshot in zip((q, r), before):
        np.testing.assert_array_equal(original.ca.values, snapshot.ca.values)
        np.testing.assert_array_equal(original.ca_spatial_sources[0].density, snapshot.ca_spatial_sources[0].density)
        assert original.ca_spatial_sources[0].provenance == snapshot.ca_spatial_sources[0].provenance


def test_own_regional_path_keeps_cno_excluded():
    from simcompare.schema import DataContractError
    b = make_bundle("s", {1:[[1, 0]]})
    b.failure_type = "contact_not_open"
    with pytest.raises(DataContractError, match="unsupported"):
        compare_pair(b, None, None, RegionalConfig())


def test_removed_structure_keeps_observed_reference_in_level_ordering():
    b = make_bundle("s", {1:[[1, 0, .5]]})
    result = compare(None, b, relationship="confirmed_removed")
    ranked = rank_rows(locals_(result), "roi_level")
    assert [row.reference.roi.roi_ca_um2 for row in ranked] == [1, .5]


def test_legacy_global_units_stay_raw_when_spatial_ca_is_unavailable():
    b = make_bundle("s", {1:[[1, 0]]})
    b.ca.unit = "1"
    b.ca_spatial_sources = []
    result = compare_pair(b, None, None, RegionalConfig())
    row = result.rows[0]
    assert row.query.global_ca_value == 1 and row.query.global_ca_unit == "1"
    assert row.query.unit_total_ca_um2 is None and row.query.global_reason == "legacy_ca_not_area"
    assert row.delta_total_ca_um2 is None
