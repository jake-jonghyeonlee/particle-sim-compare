"""Contract tests for preprocess (derived from the orchestrator smoke gate reproducing review findings)."""


def test_contract_gate():
    import sys
    import numpy as np
    from simcompare.preprocess import Normalizer, align_curve, letterbox, legend_to_values
    x = np.array([[1.0, np.nan, 3.0], [3.0, 5.0, np.nan], [100.0, 100.0, 100.0]])
    m = np.array([[True, False, True], [True, True, False], [True, True, True]])
    n = Normalizer.fit(x, m, np.array([0, 1]))
    assert np.allclose(n.mean, [2.0, 5.0, 3.0]) and np.isfinite(n.std).all() and (n.std > 0).all()
    t = n.transform(x, m)
    assert np.isfinite(t).all() and t[0, 1] == 0.0 and np.isclose(t[1, 1], 0.0) and t[0, 0] < 0 and t[2, 0] > 0
    empty = Normalizer.fit(np.full((2, 2), np.nan), np.zeros((2, 2), bool), np.array([0, 1]))
    assert np.allclose(empty.mean, 0) and np.allclose(empty.std, 1)
    assert np.allclose(Normalizer.from_dict(n.to_dict()).mean, n.mean)
    v, k = align_curve(np.array([1.0, 2.0]), np.array([np.nan, np.nan]), np.array([False, False]), np.array([1.0, 1.5]), "loglinear_within_range")
    assert not k.any() and np.isnan(v).all()
    v, k = align_curve(np.array([1.0, 4.0]), np.array([1.0, 3.0]), np.array([True, True]), np.array([0.5, 2.0, 4.0, 8.0]), "loglinear_within_range")
    assert k.tolist() == [False, True, True, False] and np.isclose(v[1], 2.0) and np.isnan(v[0])
    v, k = align_curve(np.array([1.0, 4.0]), np.array([1.0, 0.0]), np.array([True, True]), np.array([2.0, 4.0]), "exact")
    assert k.tolist() == [False, True] and v[1] == 0.0
    out, valid, info = letterbox(np.ones((4, 8)), (8, 8), binary=True)
    assert info["offset_y"] == 2 and valid.sum() == 32 and out.shape == (8, 8)
    try:
        legend_to_values(np.zeros((2, 2, 3), np.uint8), None); raise AssertionError("legend None accepted")
    except ValueError:
        pass
