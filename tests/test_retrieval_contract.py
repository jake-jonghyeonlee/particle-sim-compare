"""Contract tests for retrieval (derived from the orchestrator smoke gate reproducing review findings)."""


def test_contract_gate():
    import sys
    import numpy as np, pandas as pd
    from simcompare.retrieval import candidate_mask, layer_key, role_key, search, search_all
    cols = ["sample_id", "product_id", "product_family", "fab_id", "failure_type", "electrode_a_role", "electrode_b_role", "layer_a", "layer_b", "unit_width", "unit_height"]
    rows = [("q", "P1", "F1", "A", "lateral_short", "data", "pix", "M2", "ITO", 50.0, 80.0),
            ("s1", "P2", "F1", "A", "lateral_short", "pix", "data", "ITO", "M2", 52.0, 82.0),
            ("s2", "P1", "F1", "A", "lateral_short", "data", "pix", "M2", "ITO", 50.0, 80.0),
            ("s3", "P3", "F2", "B", "lateral_short", "data", "pix", "M1", "ITO", 100.0, 160.0),
            ("o1", "P2", "F1", "A", "lateral_open", "data", "N/A", "M2", "N/A", np.nan, np.nan),
            ("s4", "P4", "F2", "B", "lateral_short", "data", "pix", "M2", "ITO", np.nan, np.nan)]
    meta = pd.DataFrame(rows, columns=cols)
    assert role_key(meta.iloc[0]) == role_key(meta.iloc[1]) == ("lateral_short", "data", "pix")
    assert role_key(meta.iloc[4]) == ("lateral_open", "data") and layer_key(meta.iloc[4]) == ("M2",)
    assert candidate_mask(meta, 0).tolist() == [False, True, True, False, False, True]
    assert candidate_mask(meta, 0, layer_policy="ignore").tolist() == [False, True, True, True, False, True]
    assert candidate_mask(meta, 0, other_products_only=True).tolist() == [False, True, False, False, False, True]
    assert candidate_mask(meta, 0, filters={"fab_id": ["B"]}, layer_policy="ignore").tolist() == [False, False, False, True, False, True]
    assert candidate_mask(meta, 0, size_tolerance=0.1).tolist() == [False, True, True, False, False, False]
    emb = np.array([[0, 0], [1, 0], [3, 0], [0.5, 0], [0, 0], [2, 0]], dtype=float)
    res = search(emb, meta, 0, k=2, space="shape_only")
    assert list(res.columns) == ["query_id", "neighbor_id", "rank", "distance", "space", "neighbor_product_id", "neighbor_fab_id", "n_candidates"]
    assert res["neighbor_id"].tolist() == ["s1", "s4"] and res["rank"].tolist() == [1, 2] and res["n_candidates"].tolist() == [3, 3]
    empty = search(emb, meta, 4, k=3, space="x")
    assert empty.empty and list(empty.columns) == list(res.columns)
    bad = emb.copy(); bad[1, 0] = np.nan
    try:
        search(bad, meta, 0, k=2, space="x"); raise AssertionError("nan accepted")
    except ValueError:
        pass
    try:
        candidate_mask(meta, 0, layer_policy="fuzzy"); raise AssertionError("bad policy accepted")
    except ValueError:
        pass
    assert len(search_all(emb[[0, 1, 2, 3, 5]], meta.drop(index=4).reset_index(drop=True), k=1, space="x")) == 4
