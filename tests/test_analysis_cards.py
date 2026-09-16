import json

import numpy as np
import pandas as pd
import pytest

from simcompare.analysis import (
    cluster_embeddings, jaccard, leave_one_product_out, rank_agreement, ranking_comparison, risk_table,
)
from simcompare.cards import build_review_cards


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
    features = pd.DataFrame({"sample_id": ranked["sample_id"], "layout0_fill": np.linspace(0.1, 0.3, len(ranked))})
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
