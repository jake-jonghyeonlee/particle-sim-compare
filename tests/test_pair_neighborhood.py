import copy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from simcompare.analysis import ranking_comparison, risk_table
from simcompare.cards import build_review_cards
from simcompare.config import ConfigError, config_from_dict
from simcompare.neighborhood import geometry_deltas, neighborhood_comparison
from simcompare.pair_geometry import (
    bundle_pair_metrics,
    min_width_px,
    pair_metrics_px,
    pair_metrics_table,
)


@pytest.mark.parametrize("width", [1, 2, 3, 4, 5])
def test_min_width_is_exact_for_straight_lines(width):
    vertical = np.zeros((30, 30), bool)
    vertical[:, 4:4 + width] = True
    horizontal = vertical.T.copy()
    assert min_width_px(vertical) == width
    assert min_width_px(horizontal) == width


def test_min_width_finds_neck_and_ignores_line_ends():
    mask = np.zeros((40, 40), bool)
    mask[5:35, 5:15] = True  # 10 px wide bar that ends inside the image
    mask[18:22, 5:15] = False
    mask[18:22, 8:11] = True  # 3 px neck
    assert min_width_px(mask) == 3
    assert np.isnan(min_width_px(np.zeros((5, 5), bool)))
    assert np.isnan(min_width_px(np.ones((5, 5), bool)))


def test_pair_metrics_gap_parallel_length_and_overlap():
    a = np.zeros((40, 40), bool)
    a[:, 5:9] = True
    b = np.zeros((40, 40), bool)
    b[5:35, 14:38] = True
    metrics = pair_metrics_px(a, b, near_margin_px=1.0)
    assert metrics["pair:min_gap"] == 5  # columns 9..13 are empty
    # a의 오른쪽 경계 중 b와 마주 보는 30행에, b 모서리까지 간격이 최소 간격 + 1 px 안인 위아래 3행씩이 더해진다.
    assert metrics["pair:near_gap_len"] == 36
    assert pair_metrics_px(a, b, near_margin_px=0.0)["pair:near_gap_len"] == 30
    assert metrics["pair:overlap_area"] == 0
    assert metrics["pair:a_min_width"] == 4

    c = np.zeros((40, 40), bool)
    c[18:21, :] = True
    crossing = pair_metrics_px(a, c, near_margin_px=1.0)
    assert crossing["pair:min_gap"] == 0 and crossing["pair:overlap_area"] == 12
    assert np.isnan(crossing["pair:near_gap_len"])

    single = pair_metrics_px(a, None, near_margin_px=1.0)
    assert single["pair:a_min_width"] == 4 and np.isnan(single["pair:min_gap"])


def test_bundle_metrics_use_physical_pixel_size(demo_loaded):
    bundles = {b.sample_id: b for b in demo_loaded.bundles}
    # 합성 P1: 세로 90 um / 72 px = 1.25 um, 간격 round(6 / 1.25) = 5 px, 선폭 round(4 / 1.25) = 3 px
    lshort = bundle_pair_metrics(bundles["P1-v1-LSHORT"])
    assert lshort["pair:min_gap"] == pytest.approx(6.25)
    assert lshort["pair:a_min_width"] == pytest.approx(3.75)
    vshort = bundle_pair_metrics(bundles["P1-v1-VSHORT"])
    assert vshort["pair:overlap_area"] == pytest.approx(3.75 ** 2)
    assert bundle_pair_metrics(bundles["P1-v1-LSHORT"], length_unit="nm")["pair:min_gap"] == pytest.approx(6250)
    # P6에는 실제 크기가 없으므로 추정하지 않는다.
    table = pair_metrics_table(demo_loaded.bundles)
    assert table.set_index("sample_id").loc["P6-v1-LSHORT"].isna().all()


def _ranked(rows):
    base = {"comparison_group_id": "g", "failure_type": "lateral_short"}
    return pd.DataFrame([{**base, **row} for row in rows])


def _neighbors(query, ids, space="with_risk_ae"):
    return pd.DataFrame([{"query_id": query, "neighbor_id": n, "rank": i + 1, "distance": float(i + 1),
                          "space": space, "reuses_risk_inputs": True} for i, n in enumerate(ids)])


def test_neighborhood_compares_only_common_observed_sizes():
    ranked = _ranked([
        {"sample_id": "X", "product_id": "PX", "ca_area@4": 0.5, "ca_area@16": 9.0},
        {"sample_id": "A", "product_id": "PA", "ca_area@4": 0.2, "ca_area@16": np.nan},
        {"sample_id": "B", "product_id": "PB", "ca_area@4": 0.3, "ca_area@16": 1.0},
        {"sample_id": "N", "product_id": "PN", "ca_area@4": np.nan, "ca_area@16": np.nan},
    ])
    out = neighborhood_comparison(ranked, _neighbors("X", ["B", "A", "N"]), space="with_risk_ae")
    row = out.iloc[0]
    # 16 um은 A가 관측하지 않았으므로 4 um만 비교한다. 실제 크기가 없는 N은 공통 크기 계산에서 빠진다.
    assert row["compared_sizes"] == "4"
    assert row["query_risk"] == 0.5 and row["query_rank"] == 1
    assert row["reference_id"] == "A" and row["reference_risk"] == 0.2
    assert row["neighbor_risk_median"] == pytest.approx(0.25)
    assert row["status"] == "ok" and row["n_neighborhood"] == 4
    other = neighborhood_comparison(ranked, _neighbors("X", ["B"], space="shape_only"),
                                    space="with_risk_ae")
    assert other.empty


def test_neighborhood_status_when_query_is_lowest_or_missing():
    ranked = _ranked([
        {"sample_id": "X", "product_id": "PX", "ca_area@4": 0.1},
        {"sample_id": "A", "product_id": "PA", "ca_area@4": 0.2},
        {"sample_id": "M", "product_id": "PM", "ca_area@4": np.nan},
    ])
    lowest = neighborhood_comparison(ranked, _neighbors("X", ["A"]), space="with_risk_ae")
    assert lowest.iloc[0]["status"] == "query_is_lowest"
    missing = neighborhood_comparison(ranked, _neighbors("M", ["A"]), space="with_risk_ae")
    assert missing.iloc[0]["status"] == "query_risk_missing" and np.isnan(missing.iloc[0]["query_rank"])


def test_geometry_deltas_use_features_relevant_to_failure_type():
    comparison = pd.DataFrame([{
        "query_id": "X", "reference_id": "A", "comparison_group_id": "g",
        "failure_type": "lateral_short", "status": "ok",
    }, {
        "query_id": "Y", "reference_id": "A", "comparison_group_id": "g",
        "failure_type": "lateral_short", "status": "query_is_lowest",
    }])
    features = pd.DataFrame([
        {"sample_id": "X", "pair:min_gap": 4.0, "pair:near_gap_len": 50.0, "pair:overlap_area": 1.0,
         "pair:a_min_width": 3.0, "pair:b_min_width": np.nan},
        {"sample_id": "A", "pair:min_gap": 6.0, "pair:near_gap_len": 20.0, "pair:overlap_area": 9.0,
         "pair:a_min_width": 3.0, "pair:b_min_width": 5.0},
        {"sample_id": "Y", "pair:min_gap": 1.0, "pair:near_gap_len": 1.0, "pair:overlap_area": 1.0,
         "pair:a_min_width": 1.0, "pair:b_min_width": 1.0},
    ])
    deltas = geometry_deltas(comparison, features).set_index("feature")
    # overlap_area는 lateral short 대상이 아니고, 같은 값과 한쪽 누락은 행을 만들지 않는다.
    assert set(deltas.index) == {"pair:min_gap", "pair:near_gap_len"}
    assert deltas.loc["pair:min_gap", "delta"] == 2.0
    assert deltas.loc["pair:min_gap", "review_direction"] == "increase"
    assert deltas.loc["pair:near_gap_len", "review_direction"] == "decrease"
    assert set(deltas["query_id"]) == {"X"}


@pytest.fixture()
def ranked(demo_loaded, demo_cfg):
    return ranking_comparison(risk_table(demo_loaded.bundles, demo_loaded.distributions, demo_cfg))


def test_cards_carry_neighborhood_without_automatic_decision(ranked):
    features = pd.DataFrame({"sample_id": ranked["sample_id"]})
    group_id = ranked["comparison_group_id"].iloc[0]
    query = ranked["sample_id"].iloc[0]
    comparison = pd.DataFrame([{
        "query_id": query, "product_id": "P", "comparison_group_id": group_id,
        "failure_type": "lateral_short", "space": "with_risk_ae", "reuses_risk_inputs": True,
        "neighbor_ids": "A;B", "n_neighborhood": 3, "query_risk": 1.0, "neighbor_risk_median": 0.5,
        "risk_ratio_to_median": 2.0, "query_rank": 1, "compared_sizes": "4;8", "reference_id": "A",
        "reference_product_id": "PA", "reference_risk": 0.4, "reference_distance": 1.0, "status": "ok",
    }])
    deltas = pd.DataFrame([{
        "query_id": query, "reference_id": "A", "comparison_group_id": group_id,
        "failure_type": "lateral_short", "feature": "pair:min_gap", "query_value": 4.0,
        "reference_value": 6.0, "delta": 2.0, "review_direction": "increase",
    }])
    cards = build_review_cards(ranked, pd.DataFrame(columns=["space", "query_id"]), {}, features,
                               neighborhood=comparison, neighborhood_geometry=deltas)
    card = next(c for c in cards if c["comparison_group_id"] == group_id)
    entry = card["neighborhood"][0]
    assert entry["neighbors"] == ["A", "B"] and entry["reference_case"] == "A"
    assert entry["geometry_vs_reference"][0]["delta"] == 2.0
    assert entry["decision"] == "designer_judgement" and entry["resimulation_status"] == "pending"
    assert "설계자" in card["neighborhood_note"]


def test_config_neighborhood_options():
    with open(Path(__file__).resolve().parents[1] / "configs" / "demo.yaml") as handle:
        base = yaml.safe_load(handle)
    cfg = config_from_dict(copy.deepcopy(base))
    assert cfg.analysis.neighborhood_space == "with_risk_ae" and cfg.analysis.pair_near_margin == 1.0
    raw = copy.deepcopy(base)
    raw["analysis"].update({"neighborhood_space": "shape_only", "pair_near_margin": 0.5})
    cfg = config_from_dict(raw)
    assert cfg.analysis.neighborhood_space == "shape_only" and cfg.analysis.pair_near_margin == 0.5
    for key, value in (("neighborhood_space", "latent"), ("pair_near_margin", -1.0),
                       ("pair_near_margin", float("nan")), ("pair_near_margin", None)):
        raw = copy.deepcopy(base)
        raw["analysis"][key] = value
        with pytest.raises(ConfigError):
            config_from_dict(raw)
