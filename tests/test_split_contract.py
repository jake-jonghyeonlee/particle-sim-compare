"""Contract tests for split (derived from the orchestrator smoke gate reproducing review findings)."""


def test_contract_gate():
    import sys
    import numpy as np, pandas as pd
    from simcompare.split import (LeakageError, assert_no_leakage, confounding_report, group_ids, group_split,
                                  leave_one_group_out, product_balance_weights)
    meta = pd.DataFrame({"sample_id": list("abcdef"), "product_id": ["P1", "P1", "P2", "P3", "P3", "P4"],
                         "design_lineage_id": ["L1", "L1", "L1", "L3", "L3", "L4"],
                         "fab_id": ["A", "A", "B", "B", "A", "B"], "product_family": ["F1", "F1", "F2", "F2", "F1", "F2"]},
                        index=[10, 11, 12, 13, 14, 15])
    g = group_ids(meta)
    assert g[0] == g[1] == g[2] and g[3] == g[4] and len(set(g)) == 3
    tr, te = group_split(meta, test_fraction=0.3, seed=0)
    assert_no_leakage(meta, tr, te)
    assert sorted(np.concatenate([tr, te]).tolist()) == list(range(6))
    tr, te = group_split(meta, test_fraction=0.3, seed=0, test_groups=("P2",))
    assert te.tolist() == [0, 1, 2]
    w = product_balance_weights(meta, np.array([0, 1, 3]))
    assert np.isclose(w.sum(), 1.0) and np.isclose(w[0], w[1]) and np.isclose(w[2], 2 * w[0])
    try:
        assert_no_leakage(meta, np.array([0]), np.array([1])); raise AssertionError("leak not detected")
    except LeakageError:
        pass
    for bad in (dict(meta=meta.iloc[:0]), dict(meta=meta.iloc[:2])):
        try:
            group_split(bad["meta"], test_fraction=0.5, seed=0); raise AssertionError("no ValueError")
        except ValueError:
            pass
    folds = leave_one_group_out(meta, "fab_id")
    assert [f["held_out"] for f in folds] == ["A", "B"]
    rep = confounding_report(meta)
    assert rep["status"] in {"ok", "partially_confounded", "fully_confounded", "insufficient_groups"}
    one = pd.DataFrame({"sample_id": list("ab"), "product_id": ["P1", "P2"], "design_lineage_id": ["L1", "L2"],
                        "fab_id": ["A", "B"], "product_family": ["F1", "F2"]})
    assert confounding_report(one)["status"] == "fully_confounded"
