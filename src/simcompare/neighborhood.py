"""유사 제품 비교군 안에서 전극·layer별 위험을 비교하고 치수 차이를 회피안 검토 후보로 정리한다.

비교군은 한 공간(space)의 검색 결과에서 질의 사례와 그 이웃이다. 검색은 이미 같은 불량 종류·
전극 역할·layer 조건을 지키므로 비교군은 항상 같은 comparison_group_id 안에 있다.
위험 지표는 면적 보정 CA다. 사례마다 관측한 이물 크기가 다를 수 있으므로, 비교군 안에서
면적 보정 CA가 있는 사례들이 모두 관측한 크기만 골라 그 크기들의 최댓값으로 비교한다
(없는 크기를 채우거나 외삽하지 않는다).

치수 차이는 관찰된 차이일 뿐이다. 개선 효과는 재시뮬레이션으로 확인해야 하며,
다른 불량 증가 허용 여부와 설계 규칙 판단은 설계자에게 맡기므로 여기서 판정하지 않는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from simcompare.pair_geometry import RELEVANT_FEATURES

AREA_PREFIX = "ca_area@"

COMPARISON_COLUMNS = [
    "query_id", "product_id", "comparison_group_id", "failure_type", "space",
    "reuses_risk_inputs", "neighbor_ids", "n_neighborhood", "query_risk",
    "neighbor_risk_median", "risk_ratio_to_median", "query_rank", "compared_sizes", "reference_id",
    "reference_product_id", "reference_risk", "reference_distance", "status",
]
GEOMETRY_COLUMNS = [
    "query_id", "reference_id", "comparison_group_id", "failure_type", "feature",
    "query_value", "reference_value", "delta", "review_direction",
]


def _float(value) -> float:
    return float(value) if value is not None and pd.notna(value) else float("nan")


def _size_of(column: str) -> float:
    return float(column[len(AREA_PREFIX):])


def neighborhood_comparison(ranked: pd.DataFrame, neighbors: pd.DataFrame, *,
                            space: str) -> pd.DataFrame:
    """질의 사례마다 비교군(질의 + 이웃) 안의 면적 보정 CA 순위와 저위험 참고 사례를 구한다.

    - compared_sizes: 비교에 쓴 공통 관측 이물 크기(';'로 구분).
    - query_risk 등 위험 값: 공통 관측 크기에서의 면적 보정 CA 최댓값.
    - query_rank: 비교군에서 위험이 높은 순서(1 = 가장 위험). 값이 없으면 NaN.
    - reference_id: 이웃 중 위험이 가장 낮은 사례(동률은 거리, sample_id 순).
    - status: ok | no_neighbors | query_risk_missing | no_common_sizes | no_reference |
      query_is_lowest
    """
    columns = sorted((c for c in ranked.columns if c.startswith(AREA_PREFIX)), key=_size_of)
    table = ranked.set_index("sample_id")
    subset = neighbors[neighbors["space"] == space]
    rows = []
    for query_id, group in subset.groupby("query_id", sort=True):
        if query_id not in table.index:
            continue
        info = table.loc[query_id]
        group = group.sort_values(["rank", "neighbor_id"])
        ids = [n for n in group["neighbor_id"] if n in table.index]
        values = table.loc[[query_id, *ids], columns].apply(pd.to_numeric, errors="coerce")
        # 면적 보정 CA가 전혀 없는 사례(실제 크기 없음 등)는 공통 크기 계산에서 뺀다.
        members = values[values.notna().any(axis=1)]
        common = [c for c in columns if len(members) and members[c].notna().all()]
        risk = members[common].max(axis=1) if common else pd.Series(dtype=float)
        q = float(risk.get(query_id, np.nan))
        observed = risk.drop(query_id, errors="ignore").dropna()
        median = float(observed.median()) if len(observed) else np.nan
        distance = dict(zip(group["neighbor_id"], group["distance"]))
        reference = None
        if len(observed):
            reference = min(observed.index, key=lambda n: (observed[n], distance.get(n, np.inf), n))
        if not ids:
            status = "no_neighbors"
        elif query_id not in members.index:
            status = "query_risk_missing"
        elif not common:
            status = "no_common_sizes"
        elif reference is None:
            status = "no_reference"
        elif observed[reference] >= q:
            status = "query_is_lowest"
        else:
            status = "ok"
        rows.append({
            "query_id": query_id, "product_id": info["product_id"],
            "comparison_group_id": info["comparison_group_id"],
            "failure_type": info["failure_type"], "space": space,
            "reuses_risk_inputs": bool(group["reuses_risk_inputs"].iloc[0])
            if "reuses_risk_inputs" in group else None,
            "neighbor_ids": ";".join(ids), "n_neighborhood": len(ids) + 1,
            "query_risk": q, "neighbor_risk_median": median,
            "risk_ratio_to_median": q / median if np.isfinite(q) and np.isfinite(median) and median > 0
            else np.nan,
            "query_rank": (1 + int((observed > q).sum())) if np.isfinite(q) else np.nan,
            "compared_sizes": ";".join(f"{_size_of(c):g}" for c in common),
            "reference_id": reference,
            "reference_product_id": table.loc[reference, "product_id"] if reference else None,
            "reference_risk": float(observed[reference]) if reference else np.nan,
            "reference_distance": float(distance[reference]) if reference else np.nan,
            "status": status,
        })
    return pd.DataFrame(rows, columns=COMPARISON_COLUMNS)


def geometry_deltas(comparison: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    """status가 ok인 질의마다 불량 종류에 해당하는 전극 쌍 치수를 저위험 참고 사례와 비교한다.

    delta = reference_value - query_value. review_direction은 참고 사례 쪽으로 가는 방향이며
    (increase | decrease), 값이 같거나 한쪽이 없으면 행을 만들지 않는다.
    """
    values = features.set_index("sample_id")
    rows = []
    for _, item in comparison[comparison["status"] == "ok"].iterrows():
        query, reference = item["query_id"], item["reference_id"]
        if query not in values.index or reference not in values.index:
            continue
        for feature in RELEVANT_FEATURES.get(item["failure_type"], ()):
            if feature not in values.columns:
                continue
            q, r = _float(values.loc[query, feature]), _float(values.loc[reference, feature])
            if not (np.isfinite(q) and np.isfinite(r)) or abs(r - q) <= 1e-12:
                continue
            rows.append({
                "query_id": query, "reference_id": reference,
                "comparison_group_id": item["comparison_group_id"],
                "failure_type": item["failure_type"], "feature": feature,
                "query_value": q, "reference_value": r, "delta": r - q,
                "review_direction": "increase" if r > q else "decrease",
            })
    return pd.DataFrame(rows, columns=GEOMETRY_COLUMNS)
