"""Tests for :mod:`simcompare.split` (contract in ``docs/arch/split.md``)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from simcompare.split import (
    LeakageError,
    assert_no_leakage,
    confounding_report,
    group_ids,
    group_split,
    leave_one_group_out,
    product_balance_weights,
)


def _meta(rows):
    return pd.DataFrame(rows)


def _assert_disjoint(train_pos, test_pos):
    assert set(train_pos).isdisjoint(set(test_pos))


def _assert_partition(train_pos, test_pos, n):
    assert sorted(list(train_pos) + list(test_pos)) == list(range(n))


# --------------------------------------------------------------------------- #
# group_ids
# --------------------------------------------------------------------------- #
def test_group_ids_labels_components():
    meta = _meta(
        [
            {"sample_id": "s0", "product_id": "A", "design_lineage_id": "L2"},
            {"sample_id": "s1", "product_id": "A", "design_lineage_id": "L1"},
            {"sample_id": "s2", "product_id": "B", "design_lineage_id": "L3"},
        ]
    )
    labels = group_ids(meta)
    assert labels.dtype == object
    # rows 0 and 1 share product A -> one component labelled by smallest lineage
    assert labels[0] == "L1"
    assert labels[1] == "L1"
    assert labels[2] == "L3"


# --------------------------------------------------------------------------- #
# group_split: component integrity
# --------------------------------------------------------------------------- #
def test_group_split_keeps_components_together():
    meta = _meta(
        [
            {"sample_id": "s0", "product_id": "A", "design_lineage_id": "L1"},
            {"sample_id": "s1", "product_id": "A", "design_lineage_id": "L1"},
            {"sample_id": "s2", "product_id": "B", "design_lineage_id": "L2"},
            {"sample_id": "s3", "product_id": "B", "design_lineage_id": "L2"},
            {"sample_id": "s4", "product_id": "C", "design_lineage_id": "L3"},
            {"sample_id": "s5", "product_id": "C", "design_lineage_id": "L3"},
        ]
    )
    train_pos, test_pos = group_split(meta, test_fraction=0.5, seed=0)
    _assert_disjoint(train_pos, test_pos)
    _assert_partition(train_pos, test_pos, 6)
    # each component {0,1}, {2,3}, {4,5} must stay on one side
    for comp in ((0, 1), (2, 3), (4, 5)):
        side = [p in set(test_pos) for p in comp]
        assert all(side) or not any(side), f"component {comp} split across sides"


def test_group_split_deterministic_for_seed():
    meta = _meta(
        [
            {"sample_id": f"s{i}", "product_id": f"P{i % 4}", "design_lineage_id": f"L{i % 4}"}
            for i in range(8)
        ]
    )
    a = group_split(meta, test_fraction=0.5, seed=7)
    b = group_split(meta, test_fraction=0.5, seed=7)
    assert np.array_equal(a[0], b[0])
    assert np.array_equal(a[1], b[1])


# --------------------------------------------------------------------------- #
# group_split: test_groups
# --------------------------------------------------------------------------- #
def test_group_split_test_groups_pulls_whole_component():
    meta = _meta(
        [
            {"sample_id": "s0", "product_id": "A", "design_lineage_id": "L1"},
            {"sample_id": "s1", "product_id": "A", "design_lineage_id": "L1"},
            {"sample_id": "s2", "product_id": "B", "design_lineage_id": "L2"},
            {"sample_id": "s3", "product_id": "C", "design_lineage_id": "L3"},
        ]
    )
    train_pos, test_pos = group_split(meta, test_fraction=0.5, seed=0, test_groups=("A",))
    # component {0,1} (product A) fully in test
    assert set(test_pos) == {0, 1}
    assert set(train_pos) == {2, 3}


def test_group_split_test_groups_via_lineage():
    meta = _meta(
        [
            {"sample_id": "s0", "product_id": "A", "design_lineage_id": "L1"},
            {"sample_id": "s1", "product_id": "B", "design_lineage_id": "L1"},
            {"sample_id": "s2", "product_id": "C", "design_lineage_id": "L2"},
        ]
    )
    # rows 0 and 1 share lineage L1 -> one component; requesting L1 pulls both
    train_pos, test_pos = group_split(meta, test_fraction=0.5, seed=0, test_groups=("L1",))
    assert set(test_pos) == {0, 1}
    assert set(train_pos) == {2}


@pytest.mark.parametrize(
    "groups",
    [
        ("NOPE",),
        ("A", "NOPE"),
    ],
)
def test_group_split_test_groups_unknown_raises(groups):
    meta = _meta(
        [
            {"sample_id": "s0", "product_id": "A", "design_lineage_id": "L1"},
            {"sample_id": "s1", "product_id": "B", "design_lineage_id": "L2"},
        ]
    )
    with pytest.raises(ValueError):
        group_split(meta, test_fraction=0.5, seed=0, test_groups=groups)


def test_group_split_test_groups_all_rows_raises():
    meta = _meta(
        [
            {"sample_id": "s0", "product_id": "A", "design_lineage_id": "L1"},
            {"sample_id": "s1", "product_id": "A", "design_lineage_id": "L1"},
        ]
    )
    with pytest.raises(ValueError):
        group_split(meta, test_fraction=0.5, seed=0, test_groups=("A",))


# --------------------------------------------------------------------------- #
# group_split: insufficient groups
# --------------------------------------------------------------------------- #
def test_group_split_single_component_raises():
    meta = _meta(
        [
            {"sample_id": "s0", "product_id": "A", "design_lineage_id": "L1"},
            {"sample_id": "s1", "product_id": "A", "design_lineage_id": "L1"},
        ]
    )
    with pytest.raises(ValueError, match="fewer than 2 independent groups"):
        group_split(meta, test_fraction=0.5, seed=0)


def test_group_split_empty_meta_raises():
    meta = _meta(
        [
            {"sample_id": "s0", "product_id": "A", "design_lineage_id": "L1"},
        ]
    ).iloc[0:0]
    with pytest.raises(ValueError, match="fewer than 2 independent groups"):
        group_split(meta, test_fraction=0.5, seed=0)


# --------------------------------------------------------------------------- #
# assert_no_leakage
# --------------------------------------------------------------------------- #
def test_assert_no_leakage_passes_on_clean_split():
    meta = _meta(
        [
            {"sample_id": "s0", "product_id": "A", "design_lineage_id": "L1"},
            {"sample_id": "s1", "product_id": "B", "design_lineage_id": "L2"},
        ]
    )
    assert_no_leakage(meta, np.array([0]), np.array([1]))


def test_assert_no_leakage_position_overlap():
    meta = _meta(
        [
            {"sample_id": "s0", "product_id": "A", "design_lineage_id": "L1"},
            {"sample_id": "s1", "product_id": "B", "design_lineage_id": "L2"},
        ]
    )
    with pytest.raises(LeakageError):
        assert_no_leakage(meta, np.array([0, 1]), np.array([1, 2]))


def test_assert_no_leakage_product_overlap():
    meta = _meta(
        [
            {"sample_id": "s0", "product_id": "A", "design_lineage_id": "L1"},
            {"sample_id": "s1", "product_id": "A", "design_lineage_id": "L2"},
        ]
    )
    with pytest.raises(LeakageError):
        assert_no_leakage(meta, np.array([0]), np.array([1]))


def test_assert_no_leakage_lineage_overlap():
    meta = _meta(
        [
            {"sample_id": "s0", "product_id": "A", "design_lineage_id": "L1"},
            {"sample_id": "s1", "product_id": "B", "design_lineage_id": "L1"},
        ]
    )
    with pytest.raises(LeakageError):
        assert_no_leakage(meta, np.array([0]), np.array([1]))


# --------------------------------------------------------------------------- #
# leave_one_group_out
# --------------------------------------------------------------------------- #
def test_leave_one_group_out_holds_whole_component():
    meta = _meta(
        [
            {"sample_id": "s0", "product_id": "A", "design_lineage_id": "L1"},
            {"sample_id": "s1", "product_id": "A", "design_lineage_id": "L1"},
            {"sample_id": "s2", "product_id": "B", "design_lineage_id": "L2"},
        ]
    )
    results = leave_one_group_out(meta, "product_id")
    by_held = {r["held_out"]: r for r in results}
    assert set(by_held) == {"A", "B"}
    # holding out A -> component {0,1} to test
    assert set(by_held["A"]["test_pos"]) == {0, 1}
    assert set(by_held["A"]["train_pos"]) == {2}
    assert by_held["A"]["status"] == "ok"
    # holding out B -> component {2} to test
    assert set(by_held["B"]["test_pos"]) == {2}
    assert set(by_held["B"]["train_pos"]) == {0, 1}
    assert by_held["B"]["status"] == "ok"


def test_leave_one_group_out_no_train_rows_status():
    meta = _meta(
        [
            {"sample_id": "s0", "product_id": "A", "design_lineage_id": "L1"},
            {"sample_id": "s1", "product_id": "A", "design_lineage_id": "L1"},
        ]
    )
    results = leave_one_group_out(meta, "product_id")
    assert len(results) == 1
    assert results[0]["held_out"] == "A"
    assert results[0]["status"] == "no_train_rows"
    assert results[0]["train_pos"].size == 0
    assert set(results[0]["test_pos"]) == {0, 1}


def test_leave_one_group_out_sorted_unique_values():
    meta = _meta(
        [
            {"sample_id": "s0", "product_id": "C", "design_lineage_id": "L1"},
            {"sample_id": "s1", "product_id": "A", "design_lineage_id": "L2"},
            {"sample_id": "s2", "product_id": "B", "design_lineage_id": "L3"},
        ]
    )
    results = leave_one_group_out(meta, "product_id")
    assert [r["held_out"] for r in results] == ["A", "B", "C"]


# --------------------------------------------------------------------------- #
# confounding_report
# --------------------------------------------------------------------------- #
def _confound_meta(rows):
    return _meta(rows)


def test_confounding_fully_confounded():
    meta = _confound_meta(
        [
            {"fab_id": "F1", "product_family": "X"},
            {"fab_id": "F1", "product_family": "X"},
            {"fab_id": "F2", "product_family": "Y"},
            {"fab_id": "F2", "product_family": "Y"},
        ]
    )
    rep = confounding_report(meta)
    assert rep["status"] == "fully_confounded"
    assert rep["n_a"] == 2
    assert rep["n_b"] == 2
    assert rep["crosstab"]["F1"] == {"X": 2}
    assert rep["crosstab"]["F2"] == {"Y": 2}


def test_confounding_partially_confounded():
    # Each fab maps to a single family (a_single True), but family X is shared
    # by two fabs (b_single False) -> partially confounded.
    meta = _confound_meta(
        [
            {"fab_id": "F1", "product_family": "X"},
            {"fab_id": "F2", "product_family": "X"},
            {"fab_id": "F3", "product_family": "Y"},
        ]
    )
    rep = confounding_report(meta)
    assert rep["status"] == "partially_confounded"


def test_confounding_ok():
    meta = _confound_meta(
        [
            {"fab_id": "F1", "product_family": "X"},
            {"fab_id": "F1", "product_family": "Y"},
            {"fab_id": "F2", "product_family": "X"},
            {"fab_id": "F2", "product_family": "Y"},
        ]
    )
    rep = confounding_report(meta)
    assert rep["status"] == "ok"


def test_confounding_insufficient_groups():
    meta = _confound_meta(
        [
            {"fab_id": "F1", "product_family": "X"},
            {"fab_id": "F1", "product_family": "X"},
        ]
    )
    rep = confounding_report(meta)
    assert rep["status"] == "insufficient_groups"
    assert rep["n_a"] == 1
    assert rep["n_b"] == 1


def test_confounding_report_keys():
    meta = _confound_meta(
        [
            {"fab_id": "F1", "product_family": "X"},
            {"fab_id": "F2", "product_family": "Y"},
        ]
    )
    rep = confounding_report(meta)
    assert set(rep) == {"a", "b", "n_a", "n_b", "status", "detail", "crosstab"}
    assert rep["a"] == "fab_id"
    assert rep["b"] == "product_family"


# --------------------------------------------------------------------------- #
# product_balance_weights
# --------------------------------------------------------------------------- #
def test_product_balance_weights_sum_to_one():
    meta = _meta(
        [
            {"sample_id": "s0", "product_id": "A"},
            {"sample_id": "s1", "product_id": "A"},
            {"sample_id": "s2", "product_id": "B"},
        ]
    )
    weights = product_balance_weights(meta, np.array([0, 1, 2]))
    assert weights.shape == (3,)
    assert weights.sum() == pytest.approx(1.0)
    # A has 2 train rows -> each raw weight 1/2; B has 1 -> raw weight 1.
    # raw sum = 1/2 + 1/2 + 1 = 2 -> normalised: 1/4, 1/4, 1/2.
    assert weights[0] == pytest.approx(0.25)
    assert weights[1] == pytest.approx(0.25)
    assert weights[2] == pytest.approx(0.5)


def test_product_balance_weights_empty_train():
    meta = _meta([{"sample_id": "s0", "product_id": "A"}])
    weights = product_balance_weights(meta, np.array([], dtype=int))
    assert weights.size == 0


def test_product_balance_weights_uses_train_only():
    meta = _meta(
        [
            {"sample_id": "s0", "product_id": "A"},
            {"sample_id": "s1", "product_id": "A"},
            {"sample_id": "s2", "product_id": "A"},
            {"sample_id": "s3", "product_id": "B"},
        ]
    )
    # only positions 0 and 3 in train -> A count 1, B count 1 -> equal weights
    weights = product_balance_weights(meta, np.array([0, 3]))
    assert weights.sum() == pytest.approx(1.0)
    assert weights[0] == pytest.approx(0.5)
    assert weights[1] == pytest.approx(0.5)
