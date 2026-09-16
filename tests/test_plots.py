import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from simcompare.plots import ca_curves_svg, loo_stability_svg, rank_shift_svg, write_analysis_figures


def _ranked(n=4, group="lateral_open|data::M2"):
    rows = []
    for i in range(n):
        rows.append({
            "sample_id": f"P{i}-v1-OPEN", "comparison_group_id": group, "failure_type": "lateral_open",
            "role_key": "lateral_open|data", "layer_key": "M2",
            "ca@1": 0.001 * (i + 1), "ca@2": np.nan if i == 0 else 0.002 * (i + 1), "ca@4": 0.004 * (i + 1),
            "ca_max": 0.004 * (i + 1),
            "rank_ca_max": float(n - i), "rank_ca_area_max": float(i + 1) if i else np.nan,
            "rank_caa_reference": np.nan, "rank_caa_actual": np.nan,
        })
    return pd.DataFrame(rows)


def _texts(svg: str) -> str:
    root = ET.fromstring(svg)  # 올바른 XML이어야 한다
    return " ".join(t.text or "" for t in root.iter("{http://www.w3.org/2000/svg}text"))


def test_rank_shift_marks_missing_and_empty_panels():
    text = _texts(rank_shift_svg(_ranked()))
    assert "위험 순위 변화" in text
    assert "두 순위 모두 값이 없어 비교할 수 없습니다" in text  # CAA가 전부 비어 있는 경우
    assert "P0-v1-OPEN*" in text  # 한쪽 순위만 있는 사례
    assert "위→" in text  # 크게 바뀐 사례의 순위 변화 표시


def test_ca_curves_breaks_lines_at_missing_values_and_ignores_nonpositive():
    ranked = _ranked()
    ranked.loc[1, "ca@4"] = 0.0
    svg = ca_curves_svg(ranked)
    _texts(svg)
    root = ET.fromstring(svg)
    points = [len(p.get("points").split()) for p in root.iter("{http://www.w3.org/2000/svg}polyline")]
    assert points and max(points) <= 3 and min(points) >= 2


def test_ca_curves_without_size_columns():
    ranked = _ranked().drop(columns=["ca@1", "ca@2", "ca@4"])
    assert "CA 크기 열이 없어" in _texts(ca_curves_svg(ranked))


def test_long_ids_are_shortened_and_escaped():
    ranked = _ranked()
    ranked.loc[2, "sample_id"] = "VERY-LONG-PRODUCT-ID-<&>-WITH-MANY-PARTS-OPEN"
    text = _texts(rank_shift_svg(ranked)) + _texts(ca_curves_svg(ranked))
    assert "…" in text and "WITH-MANY-PARTS" not in text


def test_loo_and_write_all(tmp_path):
    loo = pd.DataFrame({"held_out_product": ["P1", "P2"], "top_items_jaccard": [1.0, np.nan],
                        "neighbor_jaccard": [0.5, 0.25], "n_queries": [3, 3]})
    assert "값 없음" in _texts(loo_stability_svg(loo))
    paths = write_analysis_figures(tmp_path, _ranked(), loo)
    assert [p.name for p in paths] == ["rank_shift.svg", "ca_curves.svg", "loo_stability.svg"]
    for path in paths:
        ET.fromstring(path.read_text(encoding="utf-8"))
