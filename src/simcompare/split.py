"""학습/평가 분리, 누수 검사, 교락 진단.

모든 함수는 meta DataFrame의 행 위치(0..N-1)를 다룬다. product_id나 design_lineage_id를
공유하는 행은 하나의 그룹(연결 성분)으로 묶여 항상 같은 쪽에 배정된다.
따라서 같은 제품의 변경 설계나 크기별 기록이 학습과 평가에 섞이지 않는다.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


class LeakageError(ValueError):
    """학습/평가 위치가 겹치거나 제품·설계 계열 ID가 양쪽에 섞였을 때 발생하는 오류."""


# --------------------------------------------------------------------------- #
# union-find 도우미
# --------------------------------------------------------------------------- #
def _union_find(n: int) -> tuple[list[int], list[int]]:
    """경로 압축과 rank를 쓰는 union-find(find, union 함수 쌍)를 만든다."""
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
    """product_id 또는 design_lineage_id를 공유하는 행을 연결한 그룹 목록(행 위치 리스트)을 반환한다."""
    n = len(meta)
    if n == 0:
        return []

    parent, (find, union) = _union_find(n)

    def link_by(column: str) -> None:
        """같은 column 값을 가진 행들을 하나의 그룹으로 연결한다(빈 값은 건너뜀)."""
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
    """행마다 그룹 라벨을 반환한다. 라벨은 그룹 안에서 가장 작은 design_lineage_id 문자열이다."""
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
# 학습/평가 분리
# --------------------------------------------------------------------------- #
def group_split(
    meta: pd.DataFrame,
    *,
    test_fraction: float,
    seed: int,
    test_groups=(),
) -> tuple[np.ndarray, np.ndarray]:
    """그룹 단위로 행 위치를 (train_pos, test_pos)로 나눈다.

    test_groups를 주면 해당 제품·설계 계열이 속한 그룹 전체를 평가로 보낸다.
    주지 않으면 seed로 그룹 순서를 섞어 평가 행이 ceil(test_fraction × N) 이상이 될 때까지
    그룹을 평가에 넣되, 학습에 최소 한 그룹을 남긴다. 독립 그룹이 2개 미만이면 ValueError.
    """
    n = len(meta)
    comps = _components(meta) if n else []
    if len(comps) < 2:
        raise ValueError("분리 불가: 독립 그룹(제품·설계 계열)이 2개 미만입니다")

    if test_groups:
        wanted = set(str(g) for g in test_groups)
        product_ids = set(str(v) for v in meta["product_id"])
        lineage_ids = set(str(v) for v in meta["design_lineage_id"])
        unknown = wanted - product_ids - lineage_ids
        if unknown:
            raise ValueError(f"알 수 없는 test_groups: {sorted(unknown)}")

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
            raise ValueError("모든 행이 평가 쪽에 배정되어 학습 자료가 없습니다")
        test_set = set(test_pos)
        train_pos = [pos for pos in range(n) if pos not in test_set]
        return (
            np.array(sorted(train_pos), dtype=int),
            np.array(sorted(test_pos), dtype=int),
        )

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

    if len(test_pos) == n:  # 학습 쪽에 최소 한 그룹을 남긴다
        last = shuffled[-1]
        test_pos = [p for p in test_pos if p not in set(last)]

    test_set = set(test_pos)
    train_pos = [pos for pos in range(n) if pos not in test_set]
    return (
        np.array(sorted(train_pos), dtype=int),
        np.array(sorted(test_pos), dtype=int),
    )


# --------------------------------------------------------------------------- #
# 누수 검사
# --------------------------------------------------------------------------- #
def assert_no_leakage(
    meta: pd.DataFrame, train_pos: np.ndarray, test_pos: np.ndarray
) -> None:
    """학습/평가 위치가 겹치거나 product_id·design_lineage_id가 양쪽에 있으면 LeakageError를 낸다."""
    train_set = set(int(p) for p in train_pos)
    test_set = set(int(p) for p in test_pos)
    if train_set & test_set:
        raise LeakageError("학습과 평가 위치가 겹칩니다")

    for column in ("product_id", "design_lineage_id"):
        col = meta[column].to_numpy()
        train_vals = {str(col[p]) for p in train_set}
        test_vals = {str(col[p]) for p in test_set}
        overlap = train_vals & test_vals
        if overlap:
            raise LeakageError(
                f"{column} 값이 학습·평가 양쪽에 섞였습니다: {sorted(overlap)}"
            )


# --------------------------------------------------------------------------- #
# 그룹 제외 검증
# --------------------------------------------------------------------------- #
def leave_one_group_out(meta: pd.DataFrame, column: str) -> list[dict]:
    """column 값마다 해당 값을 가진 행의 그룹 전체를 평가로 빼는 분리 목록을 만든다.

    Fab이나 제품군 단위 제외 검증에 쓴다. 학습 행이 남지 않으면 status가 no_train_rows다.
    """
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
# 교락 진단
# --------------------------------------------------------------------------- #
def confounding_report(
    meta: pd.DataFrame, a: str = "fab_id", b: str = "product_family"
) -> dict:
    """두 요인(기본 fab_id × product_family)의 교차표와 교락 상태를 보고한다.

    - insufficient_groups: 한 요인이라도 값이 2개 미만(평가 불가)
    - fully_confounded: 두 요인이 일대일 대응(효과 분리 불가)
    - partially_confounded: 한쪽 방향으로만 고정 대응
    - ok: 교차 비교 가능(인과 분리를 뜻하지는 않음)
    """
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
            "적어도 한 요인의 그룹이 2개 미만이라 "
            "평가 불가: 효과를 분리할 수 없습니다"
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
                "두 요인이 일대일로 대응해 효과를 분리할 수 없습니다"
            )
        elif a_single or b_single:
            status = "partially_confounded"
            detail = (
                "한 요인의 각 값이 다른 요인의 한 값에만 대응해 "
                "효과를 완전히 분리할 수 없습니다"
            )
        else:
            status = "ok"
            detail = "두 요인이 서로 한쪽에 고정되지 않아 교차 비교가 가능합니다(인과 분리를 뜻하지는 않음)"

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
# 제품 균형 가중치
# --------------------------------------------------------------------------- #
def product_balance_weights(meta: pd.DataFrame, train_pos: np.ndarray) -> np.ndarray:
    """학습 행마다 1 / (같은 product_id의 학습 행 수)를 주고 합이 1이 되게 정규화한다."""
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
