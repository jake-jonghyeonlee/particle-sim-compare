"""전 크기 support·고정 창·비팽창·결정성의 반례를 검증한다."""
import copy
from dataclasses import replace

import numpy as np
import pytest

from simcompare.config import RegionalConfig
from simcompare.regions import build_regions, collect_support
from simcompare.regional_measurement import measure_window
from fixtures.regional.builders import make_bundle


def common(bundles, cfg=None):
    return build_regions(bundles, cfg or RegionalConfig(), translations_xy_um={b.sample_id:(0., 0.) for b in bundles},
                         frame_id="common", context={"confirmed-pair":"synthetic"})


def test_support_bounds_do_not_expand_by_particle_radius():
    maps = {1:np.zeros((3, 5)), 100:np.zeros((3, 5))}
    for array in maps.values():
        array[1, 2] = 1
    b = make_bundle("s", maps, pitch=2., origin=(10., 20.))
    regions = build_regions([b], RegionalConfig()).regions
    assert len(regions) == 1
    w = regions[0].bounds_um
    assert (w.x0, w.y0, w.x1, w.y1) == (14, 22, 16, 24)


def test_display_margin_changes_only_plot_bounds():
    b = make_bundle("s", {1:[[0, 1, 0]]}, pitch=2.)
    cfg = RegionalConfig()
    a = build_regions([b], cfg).regions[0]
    c = build_regions([b], replace(cfg, display_context_margin_um=100)).regions[0]
    assert a.region_id == c.region_id and a.bounds_um == c.bounds_um
    assert c.display_bounds_um.x0 == a.bounds_um.x0-100
    assert measure_window(b.ca_spatial_sources[0], a.bounds_um) == measure_window(b.ca_spatial_sources[0], c.bounds_um)


def test_common_windows_include_both_sides_all_sizes_and_dim_components():
    q = make_bundle("q", {1:[[1, 0, 0, 0, 0, 0]], 2:[[0, 0, .001, 0, 0, 0]]})
    r = make_bundle("r", {1:[[0, 0, 0, 0, 0, .001]], 2:[[0, 0, 0, 0, 0, 0]]})
    result = common([q, r])
    assert [(w.bounds_um.x0, w.bounds_um.x1) for w in result.regions] == [(0, 1), (2, 3), (5, 6)]
    assert len(result.support_statuses) == 4


def test_moved_hotspot_keeps_old_and_new_locations():
    q = make_bundle("q", {2:[[0, 0, 0, 0, 1]]})
    r = make_bundle("r", {2:[[1, 0, 0, 0, 0]]})
    result = common([q, r])
    assert [(w.bounds_um.x0, w.bounds_um.x1) for w in result.regions] == [(0, 1), (4, 5)]


def test_common_window_is_fixed_across_diameters():
    q = make_bundle("q", {1:[[1, 0, 0, 0]], 2:[[0, 0, 0, 0]]})
    r = make_bundle("r", {1:[[0, 0, 0, 1]]})
    result = common([q, r])
    assert len(result.regions) == 2
    statuses = {(s.sample_id, s.diameter_um):s.status for s in result.support_statuses}
    assert statuses[("q", 2)] == "observed_empty"
    assert statuses[("r", 2)] == "missing"
    from simcompare.regional_comparison import compare_pair
    from fixtures.regional.builders import make_pair
    comparison = compare_pair(q, r, make_pair(q, r), RegionalConfig())
    by_size = {size: [(row.region_id, row.query.roi.requested_windows_um)
                     for row in comparison.rows if row.region_id and row.diameter_um == size]
               for size in (1., 2.)}
    assert by_size[1.] == by_size[2.]
    assert {row.query.roi.roi_ca_um2 for row in comparison.rows
            if row.region_id and row.diameter_um == 2.} == {0.}


def test_region_ids_are_stable_under_reordering_and_pair_reversal():
    q = make_bundle("q", {1:[[1, 0, 0, 1]], 2:[[0, 1, 0, 0]]})
    r = make_bundle("r", {1:[[0, 0, 1, 0]], 2:[[0, 0, 0, 0]]})
    baseline = common([q, r])
    q.hotspots.reverse()
    r.ca_spatial_sources.reverse()
    reverse = common([r, q])
    assert baseline.regions == reverse.regions
    assert all(len(region.region_id) == 64 for region in reverse.regions)


def test_regional_functions_do_not_mutate_inputs():
    q = make_bundle("q", {1:[[1, 0, 1]]})
    before = copy.deepcopy(q)
    common([q])
    collect_support(q)
    np.testing.assert_array_equal(q.hotspots[0].array, before.hotspots[0].array)
    np.testing.assert_array_equal(q.ca_spatial_sources[0].density, before.ca_spatial_sources[0].density)
    assert q.ca_spatial_sources[0].frame == before.ca_spatial_sources[0].frame


def test_grouping_transitive_closure_leaves_nonoverlapping_bounds():
    b = make_bundle("s", {1:[[1, 0, 1, 0, 1, 0, 0, 0, 1]]})
    result = build_regions([b], RegionalConfig(merge_gap_um=1))
    assert [(r.bounds_um.x0, r.bounds_um.x1) for r in result.regions] == [(0, 5), (8, 9)]


def test_component_connectivity_is_explicit():
    b = make_bundle("s", {1:[[1, 0], [0, 1]]})
    assert len(build_regions([b], RegionalConfig(connectivity=4)).regions) == 2
    assert len(build_regions([b], RegionalConfig(connectivity=8)).regions) == 1


def test_missing_origin_and_invalid_hotspot_do_not_create_regions():
    b = make_bundle("s", {1:[[1, 0]]})
    b.hotspots[0].origin_xy = None
    assert build_regions([b], RegionalConfig()).reason == "location_unavailable"
    b.hotspots[0].origin_xy = (0, 0)
    b.hotspots[0].array[0, 0] = np.nan
    assert collect_support(b)[1][0].reason == "invalid_hotspot_values"


def test_verified_source_support_fallback_requires_gate():
    b = make_bundle("s", {1:[[1, 0]]})
    b.hotspots = []
    planes, _ = collect_support(b)
    assert planes[0].evidence == "verified_ca_map_support"
    b.ca_spatial_sources[0] = replace(b.ca_spatial_sources[0], total_ca=999)
    assert collect_support(b)[1][0].status == "missing"


def test_source_support_fallback_uses_configured_conservation_and_truthful_reason():
    b = make_bundle("s", {1:[[1, 0]]})
    b.hotspots = []
    b.ca_spatial_sources[0] = replace(b.ca_spatial_sources[0], total_ca=1.01)
    cfg = RegionalConfig(conservation_atol_um2=.02)
    result = build_regions([b], cfg)
    assert len(result.regions) == 1
    assert result.regions[0].reasons == ("verified_ca_map_support",)
    assert result.support_statuses[0].status == "observed"
