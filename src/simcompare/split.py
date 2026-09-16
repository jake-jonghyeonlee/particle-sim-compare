"""Train/test splitting, leakage checks, and confounding diagnostics.

Implements the contract in ``docs/arch/split.md``.  All functions operate on
row *positions* (0..N-1) of a ``meta`` DataFrame that carries at least
``sample_id``, ``product_id`` and ``design_lineage_id`` (plus ``fab_id`` and
``product_family`` for the confounding report).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


class LeakageError(ValueError):
    """Raised when train/test positions or group labels leak across the split."""


# --------------------------------------------------------------------------- #
# Union-find helpers
# --------------------------------------------------------------------------- #
def _union_find(n: int) -> tuple[list[int], list[int]]:
    parent = list(range(n))
    rank = [0] * n

    def find(x: int) -> int:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if rank[ra] < rank[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        if rank[ra] == rank[rb]:
            rank[ra] += 1

    return parent, [find, union]


def _components(meta: pd.DataFrame) -> list[list[int]]:
    """Return the connected components of rows linked by shared product_id
    or design_lineage_id, as lists of row positions."""
    n = len(meta)
    if n == 0:
        return []

    parent, (find, union) = _union_find(n)

    def link_by(column: str) -> None:
        seen: dict = {}
        for pos in range(n):
            key = meta.iloc[pos][column]
            if isinstance(key, pd.Series):  # pragma: no cover - defensive
                key = key.iloc[0]
            if key is None or (isinstance(key, float) and math.isnan(key)):
                continue
            key = str(key)
            if key in seen:
                union(pos, seen[key])
            else:
                seen[key] = pos

    link_by("product_id")
    link_by("design_lineage_id")

    groups: dict[int, list[int]] = {}
    for pos in range(n):
        groups.setdefault(find(pos), []).append(pos)
    return list(groups.values())


def group_ids(meta: pd.DataFrame) -> np.ndarray:
    """Component label per row: the smallest ``design_lineage_id`` string in
    the component.  Returns an object-dtype array of length ``len(meta)``."""
    n = len(meta)
    labels = np.empty(n, dtype=object)
    if n == 0:
        return labels

    lineage = meta["design_lineage_id"].to_numpy()
    for comp in _components(meta):
        label = min(str(lineage[pos]) for pos in comp)
        for pos in comp:
            labels[pos] = label
    return labels


# --------------------------------------------------------------------------- #
# Splitting
# --------------------------------------------------------------------------- #
def group_split(
    meta: pd.DataFrame,
    *,
    test_fraction: float,
    seed: int,
    test_groups=(),
) -> tuple[np.ndarray, np.ndarray]:
    """Split row positions into ``(train_pos, test_pos)`` at component level.

    With ``test_groups`` the whole component of every row whose ``product_id``
    or ``design_lineage_id`` is listed goes to test.  Otherwise components are
    shuffled (sorted first) with ``np.random.default_rng(seed)`` and added to
    test until the test rows reach ``ceil(test_fraction * N)``, always keeping
    at least one component in train.
    """
    n = len(meta)
    if n == 0:
        raise ValueError("cannot split: fewer than 2 independent groups")
    comps = _components(meta)

    if test_groups:
        wanted = set(str(g) for g in test_groups)
        product_ids = set(str(v) for v in meta["product_id"])
        lineage_ids = set(str(v) for v in meta["design_lineage_id"])
        unknown = wanted - product_ids - lineage_ids
        if unknown:
            raise ValueError(f"unknown test_groups: {sorted(unknown)}")

        product_arr = meta["product_id"].to_numpy()
        lineage_arr = meta["design_lineage_id"].to_numpy()
        test_comp = set()
        for comp in comps:
            for pos in comp:
                if str(product_arr[pos]) in wanted or str(
                    lineage_arr[pos]
                ) in wanted:
                    test_comp.add(id(comp))
                    break

        test_pos = sorted(
            pos for comp in comps if id(comp) in test_comp for pos in comp
        )
        if len(test_pos) == n:
            raise ValueError("all rows assigned to test")
        test_set = set(test_pos)
        train_pos = [pos for pos in range(n) if pos not in test_set]
        return (
            np.array(sorted(train_pos), dtype=int),
            np.array(sorted(test_pos), dtype=int),
        )

    if len(comps) < 2:
        raise ValueError("cannot split: fewer than 2 independent groups")

    lineage_arr = meta["design_lineage_id"].to_numpy()
    order = sorted(
        comps, key=lambda c: min(str(lineage_arr[p]) for p in c)
    )
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(order))
    shuffled = [order[i] for i in perm]

    target = math.ceil(test_fraction * n)
    test_pos: list[int] = []
    for comp in shuffled:
        if len(test_pos) >= target:
            break
        test_pos.extend(comp)

    if len(test_pos) == n:  # keep at least one component in train
        last = shuffled[-1]
        test_pos = [p for p in test_pos if p not in set(last)]

    test_set = set(test_pos)
    train_pos = [pos for pos in range(n) if pos not in test_set]
    return (
        np.array(sorted(train_pos), dtype=int),
        np.array(sorted(test_pos), dtype=int),
    )


# --------------------------------------------------------------------------- #
# Leakage
# --------------------------------------------------------------------------- #
def assert_no_leakage(
    meta: pd.DataFrame, train_pos: np.ndarray, test_pos: np.ndarray
) -> None:
    """Raise :class:`LeakageError` if positions overlap or a ``product_id`` /
    ``design_lineage_id`` value occurs in both train and test."""
    train_set = set(int(p) for p in train_pos)
    test_set = set(int(p) for p in test_pos)
    if train_set & test_set:
        raise LeakageError("train and test positions overlap")

    for column in ("product_id", "design_lineage_id"):
        col = meta[column].to_numpy()
        train_vals = {str(col[p]) for p in train_set}
        test_vals = {str(col[p]) for p in test_set}
        overlap = train_vals & test_vals
        if overlap:
            raise LeakageError(
                f"{column} value(s) leak across split: {sorted(overlap)}"
            )


# --------------------------------------------------------------------------- #
# Leave-one-group-out
# --------------------------------------------------------------------------- #
def leave_one_group_out(meta: pd.DataFrame, column: str) -> list[dict]:
    """For each sorted unique value of ``column`` hold out the whole component
    of every row carrying that value; return per-value split records."""
    n = len(meta)
    comps = _components(meta)
    comp_of_pos: dict[int, int] = {}
    for idx, comp in enumerate(comps):
        for pos in comp:
            comp_of_pos[pos] = idx

    col = meta[column].to_numpy()
    results: list[dict] = []
    for v in sorted(set(str(x) for x in col)):
        target_comps = {
            comp_of_pos[pos]
            for pos in range(n)
            if str(col[pos]) == v
        }
        test_pos = sorted(
            pos for idx, comp in enumerate(comps) if idx in target_comps for pos in comp
        )
        test_set = set(test_pos)
        train_pos = [pos for pos in range(n) if pos not in test_set]
        status = "ok" if train_pos else "no_train_rows"
        results.append(
            {
                "held_out": v,
                "train_pos": np.array(sorted(train_pos), dtype=int),
                "test_pos": np.array(sorted(test_pos), dtype=int),
                "status": status,
            }
        )
    return results


# --------------------------------------------------------------------------- #
# Confounding
# --------------------------------------------------------------------------- #
def confounding_report(
    meta: pd.DataFrame, a: str = "fab_id", b: str = "product_family"
) -> dict:
    """Cross-tabulate ``a`` x ``b`` and classify the confounding status."""
    a_vals = [str(v) for v in meta[a]]
    b_vals = [str(v) for v in meta[b]]
    n_a = len(set(a_vals))
    n_b = len(set(b_vals))

    crosstab: dict = {}
    for av, bv in zip(a_vals, b_vals):
        crosstab.setdefault(av, {})
        crosstab[av][bv] = crosstab[av].get(bv, 0) + 1

    if n_a < 2 or n_b < 2:
        status = "insufficient_groups"
        detail = (
            "fewer than two levels in at least one factor; "
            "effects cannot be separated"
        )
    else:
        a_to_b = {av: set(bs) for av, bs in crosstab.items()}
        b_to_a = {
            bv: {av for av, bs in crosstab.items() if bv in bs} for bv in set(b_vals)
        }
        a_single = all(len(bs) == 1 for bs in a_to_b.values())
        b_single = all(len(as_) == 1 for as_ in b_to_a.values())
        if a_single and b_single:
            status = "fully_confounded"
            detail = (
                "a and b map one-to-one; their effects cannot be separated"
            )
        elif a_single or b_single:
            status = "partially_confounded"
            detail = (
                "one factor maps to a single level of the other; "
                "effects cannot be fully separated"
            )
        else:
            status = "ok"
            detail = "a and b are not confounded"

    return {
        "a": a,
        "b": b,
        "n_a": n_a,
        "n_b": n_b,
        "status": status,
        "detail": detail,
        "crosstab": crosstab,
    }


# --------------------------------------------------------------------------- #
# Product balance weights
# --------------------------------------------------------------------------- #
def product_balance_weights(meta: pd.DataFrame, train_pos: np.ndarray) -> np.ndarray:
    """Per-train-position weight ``1 / (train rows sharing the product_id)``,
    normalised to sum to 1."""
    train_pos = np.asarray(train_pos, dtype=int)
    if train_pos.size == 0:
        return np.array([], dtype=float)

    product_arr = meta["product_id"].to_numpy()
    counts: dict = {}
    for pos in train_pos:
        key = str(product_arr[pos])
        counts[key] = counts.get(key, 0) + 1

    weights = np.array(
        [1.0 / counts[str(product_arr[pos])] for pos in train_pos],
        dtype=float,
    )
    total = weights.sum()
    if total > 0:
        weights = weights / total
    return weights
