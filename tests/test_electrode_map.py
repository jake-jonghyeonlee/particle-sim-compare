"""전극 수 지도 — 손으로 검산할 수 있는 작은 예."""
import numpy as np
import pytest

from simcompare.electrode_map import DefectHotspot, electrode_count_maps, find_regions, summary_map


def mask(*cols):
    m = np.zeros((1, 12), dtype=bool)
    m[0, list(cols)] = True
    return m


def spot(electrodes, cols, layer="L1", d=5.0):
    return DefectHotspot(layer, tuple(electrodes), d, mask(*cols))


SPOTS = [
    spot(["gate", "data"], range(0, 6)),       # short
    spot(["gate", "com"], range(4, 8)),        # short
    spot(["gate"], range(10, 12)),             # open
    spot(["data", "pixel"], range(4, 6), layer="L2"),
    spot(["gate", "pixel"], range(0, 12), d=7.0),  # 다른 지름 — 5 µm 지도에는 안 들어감
]


def test_counts_distinct_electrodes_not_defect_cases():
    counts = electrode_count_maps(SPOTS)["L1"]
    assert counts[0, :4].tolist() == [2, 2, 2, 2]      # gate, data
    assert counts[0, 4:6].tolist() == [3, 3]           # gate, data, com (gate는 한 번)
    assert counts[0, 6:8].tolist() == [2, 2]           # gate, com
    assert counts[0, 10:].tolist() == [1, 1]           # open은 전극 하나


def test_layers_are_separate_and_summary_takes_the_maximum():
    maps = electrode_count_maps(SPOTS)
    assert set(maps) == {"L1", "L2"}
    assert maps["L2"][0, 4:6].tolist() == [2, 2]
    assert summary_map(maps)[0, 4:6].tolist() == [3, 3]


def test_other_diameters_are_ignored():
    assert electrode_count_maps(SPOTS, diameter_um=7.0)["L1"].max() == 2


def test_regions_are_ranked_and_list_the_electrodes_involved():
    regions = find_regions(SPOTS, min_count=2)
    top = regions[0]
    assert (top.layer, top.max_count, top.area_px, top.bbox) == ("L1", 3, 8, (0, 0, 1, 8))
    assert top.electrodes == ("com", "data", "gate")
    assert [(r.layer, r.max_count) for r in regions] == [("L1", 3), ("L2", 2)]
    assert find_regions(SPOTS, min_count=3)[0].area_px == 2


def test_no_hotspot_at_the_diameter_and_mismatched_grid_are_errors():
    with pytest.raises(ValueError, match="hotspot이 없다"):
        summary_map(electrode_count_maps(SPOTS, diameter_um=3.0))
    bad = DefectHotspot("L1", ("a", "b"), 5.0, np.zeros((2, 12), dtype=bool))
    with pytest.raises(ValueError, match="격자"):
        electrode_count_maps(SPOTS + [bad])
