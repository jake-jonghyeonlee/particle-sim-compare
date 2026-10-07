"""layer별 line/pixel 값과 전극 쌍별 몫 — 손으로 검산할 수 있는 작은 예."""
import numpy as np
import pytest

from simcompare.linepixel import (
    ShortHotspot,
    compare_rates,
    compute_rates,
    normalize_pair_table,
    pair_of,
    unit_count,
)

TABLE = normalize_pair_table([("gate", "data", "line"), ("gate", "com", "line"), ("data", "pixel", "pixel"), ("com", "pixel", "pixel")])
GD, GC, DP, CP = pair_of("gate", "data"), pair_of("gate", "com"), pair_of("data", "pixel"), pair_of("com", "pixel")


def mask(*cols):
    """1 x 10 격자. cols에 든 열만 True."""
    m = np.zeros((1, 10), dtype=bool)
    m[0, list(cols)] = True
    return m


def hs(a, b, cols, layer="L1", d=5.0):
    return ShortHotspot(layer, a, b, d, mask(*cols))


def test_line_takes_the_overlap_and_pixel_is_what_remains():
    r = compute_rates([hs("gate", "data", range(0, 4)), hs("data", "pixel", range(2, 7))], TABLE, {("L1", 5.0): 1.0}, pixel_area_um2=1.0)
    layer = r.layers["L1"]
    assert layer.line.value == 4.0  # 열 0~3
    assert layer.pixel.value == 3.0  # 열 4~6 (2~3은 line으로)
    assert layer.moved_to_line == 2.0
    assert layer.line.exclusive == {GD: 4.0} and layer.pixel.exclusive == {DP: 3.0}


def test_union_counts_once_and_shares_add_up_to_the_total():
    r = compute_rates([hs("gate", "data", range(0, 5)), hs("gate", "com", range(3, 8))], TABLE, {("L1", 5.0): 1.0}, pixel_area_um2=1.0)
    line = r.layers["L1"].line
    assert line.value == 8.0  # 합집합 0~7, 겹친 3~4는 한 번
    assert line.exclusive == {GD: 3.0, GC: 3.0}
    assert line.shared == {frozenset({GD, GC}): 2.0}
    assert sum(line.exclusive.values()) + sum(line.shared.values()) == line.value


def test_each_diameter_is_weighted_by_its_density_and_pixel_area():
    spots = [hs("data", "pixel", range(0, 2), d=5.0), hs("data", "pixel", range(0, 6), d=7.0)]
    r = compute_rates(spots, TABLE, {("L1", 5.0): 0.5, ("L1", 7.0): 2.0}, pixel_area_um2=0.25)
    assert r.layers["L1"].pixel.value == pytest.approx(2 * 0.25 * 0.5 + 6 * 0.25 * 2.0)
    assert r.layers["L1"].area_um2[7.0]["pixel"] == pytest.approx(1.5)


def test_layers_are_separate_and_totals_add_across_layers():
    spots = [hs("gate", "data", range(0, 3), layer="L1"), hs("gate", "data", range(0, 3), layer="L2")]
    r = compute_rates(spots, TABLE, {("L1", 5.0): 1.0, ("L2", 5.0): 3.0}, pixel_area_um2=1.0)
    assert r.layers["L1"].line.value == 3.0 and r.layers["L2"].line.value == 9.0
    assert r.total("line") == 12.0 and r.total("pixel") == 0.0


def test_lateral_and_vertical_of_the_same_pair_merge_into_one_pair():
    r = compute_rates([hs("gate", "data", range(0, 3)), hs("data", "gate", range(2, 5))], TABLE, {("L1", 5.0): 1.0}, pixel_area_um2=1.0)
    assert r.layers["L1"].line.exclusive == {GD: 5.0} and r.layers["L1"].line.shared == {}


def test_pair_missing_from_the_table_is_reported_not_counted_as_zero():
    r = compute_rates([hs("gate", "data", range(0, 2)), hs("gate", "pixel", range(0, 9))], TABLE, {("L1", 5.0): 1.0}, pixel_area_um2=1.0)
    assert r.unmapped_pairs == (pair_of("gate", "pixel"),)
    assert r.layers["L1"].line.value == 2.0 and r.layers["L1"].pixel.value == 0.0


def test_two_by_two_mask_is_divided_by_the_units_it_holds():
    r = compute_rates([hs("gate", "data", range(0, 8))], TABLE, {("L1", 5.0): 1.0}, pixel_area_um2=1.0, units_in_mask=4)
    assert r.layers["L1"].line.value == 2.0


def test_missing_density_and_mismatched_grid_are_errors():
    with pytest.raises(ValueError, match="defect density"):
        compute_rates([hs("gate", "data", [0])], TABLE, {}, pixel_area_um2=1.0)
    other = ShortHotspot("L1", "gate", "com", 5.0, np.zeros((2, 10), dtype=bool))
    with pytest.raises(ValueError, match="격자"):
        compute_rates([hs("gate", "data", [0]), other], TABLE, {("L1", 5.0): 1.0}, pixel_area_um2=1.0)


def test_pair_table_rejects_conflicting_or_unknown_types():
    with pytest.raises(ValueError, match="유형이 둘"):
        normalize_pair_table([("a", "b", "line"), ("b", "a", "pixel")])
    with pytest.raises(ValueError, match="line 또는 pixel"):
        normalize_pair_table([("a", "b", "open")])


def test_unit_count_from_panel_and_unit_size():
    assert unit_count((1000.0, 500.0), (10.0, 10.0)) == 100 * 50
    assert unit_count((1005.0, 500.0), (10.0, 10.0)) == 100 * 50
    assert unit_count((1005.0, 500.0), (10.0, 10.0), rounding="round") == 101 * 50


def test_design_change_reads_as_pixel_up_line_down_with_pair_shares():
    density = {("L1", 5.0): 1.0}
    a = compute_rates([hs("gate", "data", range(0, 4)), hs("data", "pixel", range(6, 8))], TABLE, density, pixel_area_um2=1.0)
    b = compute_rates([hs("gate", "data", range(0, 2)), hs("data", "pixel", range(1, 8))], TABLE, density, pixel_area_um2=1.0)
    d = compare_rates(a, b)["L1"]
    assert d.line == -2.0 and d.pixel == 4.0  # line 4→2, pixel 2→6
    assert d.moved_to_line == 1.0  # B에서 열 1이 pixel 쌍에도 덮이지만 line
    assert d.line_by_pair == {GD: -2.0} and d.pixel_by_pair == {DP: 4.0}


def test_comparison_scales_each_product_by_its_own_unit_count():
    density = {("L1", 5.0): 1.0}
    a = compute_rates([hs("gate", "data", range(0, 2))], TABLE, density, pixel_area_um2=1.0)
    b = compute_rates([hs("gate", "data", range(0, 3))], TABLE, density, pixel_area_um2=1.0)
    d = compare_rates(a, b, scale_reference=100, scale_target=50)["L1"]
    assert d.line == 3.0 * 50 - 2.0 * 100
