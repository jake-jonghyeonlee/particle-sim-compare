import json

import numpy as np
import pandas as pd
import pytest

from simcompare.analysis import (
    InsufficientEmbeddingData, cluster_embeddings, jaccard, leave_one_product_out,
    rank_agreement, ranking_comparison, risk_table,
)
from simcompare.cards import _order_value, build_review_cards
from simcompare.baseline import BaselinePCA


@pytest.mark.parametrize("n_products", [2, 3])
def test_loo_pca_minimum_training_rows(ranked, demo_loaded, n_products):
    sample = ranked.drop_duplicates("product_id").head(n_products).copy()
    meta = sample.reset_index(drop=True)
    bundles = {b.sample_id: b for b in demo_loaded.bundles}
    for column in ("electrode_a_role", "electrode_b_role", "layer_a", "layer_b"):
        meta[column] = [getattr(bundles[s], column) for s in meta["sample_id"]]
    x = np.arange(n_products * 2, dtype=float).reshape(n_products, 2)
    mask = np.ones_like(x, dtype=bool)

    def embed_fn(fit_pos):
        return BaselinePCA(2).fit(x, mask, fit_pos).transform(x, mask)

    loo = leave_one_product_out(sample, meta, embed_fn, k=1, top_k=1)
    if n_products == 2:
        assert loo["status"].eq("skipped").all()
        assert loo["reason"].eq("insufficient_training_rows").all()
        assert loo[["top_items_jaccard", "neighbor_jaccard"]].isna().all().all()
        assert loo["n_queries"].eq(0).all()
    else:
        assert loo["status"].eq("computed").all()
        assert loo["reason"].isna().all()
        # P3 is the full top item; removing it leaves an empty full-top set.
        assert loo["top_items_jaccard"].tolist() == [1.0, 1.0, 0.0]
        assert loo["neighbor_jaccard"].eq(1).all()


def test_loo_records_missing_features_without_hiding_other_errors(ranked):
    meta = ranked.reset_index(drop=True)
    calls = []

    def embed_fn(fit_pos):
        calls.append(len(fit_pos))
        if len(fit_pos) < len(meta):
            raise InsufficientEmbeddingData("no_observed_features")
        return np.arange(len(meta), dtype=float)[:, None]

    loo = leave_one_product_out(ranked, meta, embed_fn, k=1, top_k=1)
    assert loo["status"].eq("skipped").all()
    assert loo["reason"].eq("no_observed_features").all()
    assert loo[["top_items_jaccard", "neighbor_jaccard"]].isna().all().all()
    assert len(calls) == len(loo) + 1

    def broken_embed(fit_pos):
        raise ValueError("unrelated error")

    with pytest.raises(ValueError, match="unrelated error"):
        leave_one_product_out(ranked, meta, broken_embed, k=1, top_k=1)


@pytest.fixture()
def ranked(demo_loaded, demo_cfg):
    return ranking_comparison(risk_table(demo_loaded.bundles, demo_loaded.distributions, demo_cfg))


def test_risk_table_caa_matches_manual_demo_sum(demo_loaded, ranked):
    row = ranked.set_index("sample_id").loc["P1-v1-LSHORT"]
    bundle = next(b for b in demo_loaded.bundles if b.sample_id == "P1-v1-LSHORT")
    dist = demo_loaded.distributions["FAB_A-F1-ITO"]
    expected = float(np.sum(np.asarray(dist.raw_values) * bundle.ca.values))
    assert row["caa_actual"] == pytest.approx(expected)
    assert bool(row["caa_is_demo"]) and "DEMO" in row["caa_actual_reason"]
    ref = float(np.sum(0.2 * bundle.ca.values))
    assert row["caa_reference"] == pytest.approx(ref)
    p6 = ranked.set_index("sample_id").loc["P6-v1-OPEN"]
    assert np.isnan(p6["ca_area_max"]) and np.isnan(p6["rank_ca_area_max"])


def test_real_mode_refuses_demo_rule(demo_loaded, demo_cfg):
    demo_cfg.mode = "real"
    table = risk_table(demo_loaded.bundles[:2], demo_loaded.distributions, demo_cfg)
    assert table["caa_actual"].isna().all()
    assert table["caa_actual_reason"].str.contains("demo").all()


def test_rankings_and_agreement(ranked):
    opens = ranked[ranked["failure_type"] == "lateral_open"]
    assert opens["rank_caa_actual"].min() == 1
    agreement = {row["failure_type"]: row for row in rank_agreement(ranked)}
    assert set(agreement) == {"lateral_open", "lateral_short", "vertical_short"}
    assert agreement["lateral_open"]["n"] >= 3


def test_clusters_fit_on_train_only():
    rng = np.random.default_rng(0)
    emb = np.vstack([rng.normal(0, 0.1, (6, 2)), rng.normal(5, 0.1, (6, 2)), [[100.0, 100.0]]])
    train = np.arange(12)
    info = cluster_embeddings(emb, train, 2, seed=0)
    assert len(set(info["labels"][:12])) == 2
    assert "물리적 의미가 없습니다" in info["note"]
    assert all(v is not None for v in info["counter_examples"].values())
    assert jaccard(set(), set()) is None and jaccard({"a"}, {"a", "b"}) == 0.5


def test_card_risk_order_prefers_area_normalized_ca_when_caa_is_unavailable():
    group = pd.DataFrame({
        "caa_actual": [np.nan, np.nan],
        "ca_area_max": [0.002, 0.001],
        "ca_max": [0.01, 0.02],
    })
    assert _order_value(group).tolist() == [0.002, 0.001]


def test_leave_one_product_out_and_cards(demo_loaded, ranked):
    from simcompare.dataset import encode_bundles  # noqa: F401  (import check)

    meta = pd.DataFrame([{
        "sample_id": b.sample_id, "product_id": b.product_id, "product_family": b.product_family,
        "fab_id": b.fab_id, "failure_type": b.failure_type, "electrode_a_role": b.electrode_a_role,
        "electrode_b_role": b.electrode_b_role, "layer_a": b.layer_a, "layer_b": b.layer_b,
        "unit_width": b.unit_width, "unit_height": b.unit_height,
    } for b in demo_loaded.bundles])
    rng = np.random.default_rng(1)
    base = rng.normal(size=(len(meta), 3))

    def embed_fn(fit_pos):
        return base - base[fit_pos].mean(axis=0)

    loo = leave_one_product_out(ranked, meta, embed_fn, k=2, top_k=2)
    assert list(loo["held_out_product"]) == sorted(meta["product_id"].unique())
    assert loo["neighbor_jaccard"].dropna().between(0, 1).all()

    neighbors = pd.DataFrame([
        {"query_id": "P1-v1-OPEN", "neighbor_id": "P2-v1-OPEN", "rank": 1, "distance": 0.1,
         "space": "shape_only", "neighbor_product_id": "P2", "neighbor_fab_id": "FAB_A", "n_candidates": 3},
    ])
    features = pd.DataFrame({
        "sample_id": ranked["sample_id"],
        "layout0_fill": np.linspace(0.1, 0.3, len(ranked)),
        "geom:shape:electrode_gap_um": np.linspace(2.0, 4.0, len(ranked)),
        "geom:thk:metal": np.linspace(0.2, 0.4, len(ranked)),
        "geom:unit_width": np.linspace(50.0, 100.0, len(ranked)),
    })
    clusters = {"labels": {}, "representatives": {0: "P1-v1-OPEN"}, "counter_examples": {0: None}}
    cards = build_review_cards(ranked, neighbors, clusters, features)
    assert len(cards) == 3
    text = json.dumps(cards, allow_nan=False, ensure_ascii=False)
    assert "인과적 중요도" in text
    open_card = next(c for c in cards if c["failure_type"] == "lateral_open")
    assert open_card["representative_cases"] == ["P1-v1-OPEN"]
    assert open_card["retrieval"]["shape_only"]["P1-v1-OPEN"] == ["P2-v1-OPEN"]
    assert any("실제 크기 없음" in s for s in open_card["unavailable_conditions"])
    assert any("DEMO" in s for s in open_card["expert_review_required"])
    candidate_names = {item["feature"] for card in cards for item in card["improvement_candidates"]}
    assert "geom:shape:electrode_gap_um" in candidate_names
    assert "geom:thk:metal" not in candidate_names
    assert "geom:unit_width" not in candidate_names
    candidates = [item for card in cards for item in card["improvement_candidates"]]
    assert all(item["applicable_conditions"]["comparison_group_id"] for item in candidates)
    assert all(item["counter_examples"] for item in candidates)
