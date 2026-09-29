"""검토 카드: 전문가 검토를 위한 근거 묶음.

중요 인자를 자동으로 판정하지 않고, 같은 불량 종류·전극 역할·layer 조합별로
대표/반대 사례, 반복되는 형상 특징, 위험 수치 비교, 면적 보정 전후 순위 변화,
적용 가능·불가 조건, 전문가 확인 항목을 모아 보여준다.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

DISCLAIMER = (
    "전문가 검토용 근거 요약입니다. 인과적 중요도, 자동 인자 순위, "
    "수율·불량률을 주장하지 않습니다."
)
RETRIEVAL_NOTE = "with_risk 공간의 유사 사례는 CA·hotspot 입력을 재사용하므로 독립적인 발견이 아닙니다"
NEIGHBORHOOD_NOTE = (
    "유사 제품 비교군 안의 면적 보정 CA 비교와 저위험 참고 사례와의 전극 쌍 치수 차이입니다. "
    "관찰된 차이이며 개선 효과는 재시뮬레이션으로 확인해야 하고, 다른 불량 증가 허용 여부와 "
    "설계 규칙은 설계자가 판단합니다"
)


def _clean(value):
    """NumPy 타입과 NaN을 JSON에 쓸 수 있는 파이썬 값(None 포함)으로 바꾼다."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if math.isnan(float(value)) else float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def _order_value(group: pd.DataFrame) -> pd.Series:
    """Use one metric: first complete, else most observed (ties keep priority)."""
    metrics = ["caa_actual", "ca_area_max", "ca_max"]
    counts = {name: group[name].notna().sum() for name in metrics}
    metric = next((name for name in metrics if counts[name] == len(group)),
                  max(metrics, key=lambda name: counts[name]))
    return group[metric]


def _cv(values: pd.Series) -> float | None:
    """변동계수(표준편차/|평균|). 값이 2개 미만이거나 평균이 0에 가까우면 None."""
    values = pd.to_numeric(values, errors="coerce").dropna()
    if len(values) < 2 or abs(values.mean()) <= 1e-12:
        return None
    return float(values.std(ddof=0) / abs(values.mean()))


def _repeated_features(group: pd.DataFrame, features: pd.DataFrame) -> list[str]:
    """그룹 전체보다 위험이 높은 사례들 사이에서 변동이 작은 형상 특징을 최대 3개 고른다.

    모든 사례에서 값이 같은 특징(예: 동일한 두께)은 의미가 없으므로 제외한다.
    기술적 요약일 뿐 인과를 뜻하지 않는다.
    """
    order = _order_value(group)
    upper = group[order >= order.median()] if order.notna().any() else group
    rows_all = features[features["sample_id"].isin(group["sample_id"])]
    rows_upper = features[features["sample_id"].isin(upper["sample_id"])]
    scores = {}
    for name in features.columns.drop("sample_id"):
        cv_all, cv_upper = _cv(rows_all[name]), _cv(rows_upper[name])
        if cv_all is None or cv_upper is None or cv_all < 1e-6:
            continue
        scores[name] = cv_upper / cv_all
    ranked = sorted(scores.items(), key=lambda kv: (kv[1], kv[0]))
    return [name for name, ratio in ranked if ratio < 1.0][:3]


def _feature_deltas(group: pd.DataFrame, features: pd.DataFrame) -> list[dict]:
    """고위험·저위험 절반의 형상 특징 중앙값 차이를 폭넓은 사람 검토 후보로 만든다."""
    order = _order_value(group)
    valid = group[order.notna()].copy()
    if len(valid) < 2:
        return []
    valid["_risk"] = order[order.notna()]
    valid = valid.sort_values(["_risk", "sample_id"])
    cut = max(1, len(valid) // 2)
    low_ids = set(valid.head(cut)["sample_id"])
    high_ids = set(valid.tail(cut)["sample_id"])
    candidates = []
    for name in features.columns.drop("sample_id"):
        # 적층 두께는 현재 모델의 기본 관심 대상이 아니고, unit-pixel 크기는 제품 간 CA를
        # 보정하기 위한 조건이지 변경 제안 대상이 아니다. layout 파생 특징과 명시적으로
        # 전달된 shape_metrics만 후보로 남긴다.
        if name.startswith(("geom:thk:", "geom:unit_width", "geom:unit_height")):
            continue
        low = pd.to_numeric(features.loc[features["sample_id"].isin(low_ids), name], errors="coerce").dropna()
        high = pd.to_numeric(features.loc[features["sample_id"].isin(high_ids), name], errors="coerce").dropna()
        if low.empty or high.empty:
            continue
        low_value, high_value = float(low.median()), float(high.median())
        delta = high_value - low_value
        if abs(delta) <= 1e-12:
            continue
        candidates.append({
            "feature": name,
            "high_risk_median": high_value,
            "lower_risk_median": low_value,
            "observed_delta": delta,
            "observed_geometry_delta": delta,
            "review_direction": "decrease" if delta > 0 else "increase",
            "supporting_high_risk_cases": sorted(high_ids),
            "lower_risk_comparison_cases": sorted(low_ids),
            "counter_examples": sorted(low_ids),
            "applicable_conditions": {
                "comparison_group_id": str(group["comparison_group_id"].iloc[0]),
                "products": sorted(str(value) for value in group["product_id"].unique()),
            },
            "statement": "관찰된 차이에 근거한 검토 후보이며 원인이나 개선 효과를 뜻하지 않습니다",
            "resimulation_status": "pending",
            "electrical_review_status": "external_review_required",
        })
    return candidates


def _particle_size_summary(group: pd.DataFrame) -> list[dict]:
    """샘플별 관측 CA 크기 범위와 최대 CA 크기를 요약한다."""
    ca_columns = [c for c in group.columns if c.startswith("ca@")]
    out = []
    for _, row in group.iterrows():
        observed = [(float(c.split("@", 1)[1]), row[c]) for c in ca_columns if pd.notna(row[c])]
        if not observed:
            out.append({"sample_id": row["sample_id"], "observed_range": None, "peak_size": None})
            continue
        peak = max(observed, key=lambda item: item[1])
        out.append({
            "sample_id": row["sample_id"],
            "observed_range": [min(x[0] for x in observed), max(x[0] for x in observed)],
            "peak_size": peak[0],
        })
    return out


def _neighborhood_entries(group_id: str, comparison: pd.DataFrame | None,
                          geometry: pd.DataFrame | None) -> list[dict]:
    """비교 그룹의 질의별 비교군 요약과 참고 사례와의 치수 차이."""
    if comparison is None or comparison.empty:
        return []
    rows = comparison[comparison["comparison_group_id"] == group_id]
    entries = []
    for _, row in rows.sort_values(["query_rank", "query_id"], na_position="last").iterrows():
        deltas = []
        if geometry is not None and not geometry.empty:
            deltas = geometry[geometry["query_id"] == row["query_id"]][[
                "feature", "query_value", "reference_value", "delta", "review_direction",
            ]].to_dict(orient="records")
        entries.append({
            "query_id": row["query_id"],
            "product_id": row["product_id"],
            "space": row["space"],
            "neighbors": [n for n in str(row["neighbor_ids"]).split(";") if n and n != "nan"],
            "compared_sizes": row["compared_sizes"],
            "query_ca_area": row["query_risk"],
            "rank_in_neighborhood": row["query_rank"],
            "n_neighborhood": row["n_neighborhood"],
            "ratio_to_neighbor_median": row["risk_ratio_to_median"],
            "reference_case": row["reference_id"],
            "reference_ca_area": row["reference_risk"],
            "status": row["status"],
            "geometry_vs_reference": deltas,
            "resimulation_status": "pending" if deltas else None,
            "decision": "designer_judgement" if deltas else None,
        })
    return entries


def build_review_cards(ranked: pd.DataFrame, neighbors: pd.DataFrame, clusters: dict,
                       features: pd.DataFrame, *, min_products: int = 2,
                       design_reviews: list[dict] | None = None,
                       neighborhood: pd.DataFrame | None = None,
                       neighborhood_geometry: pd.DataFrame | None = None) -> list[dict]:
    """불량 종류·전극 역할·layer 조합별 검토 카드 목록을 만든다.

    ranked는 순위 비교 표, neighbors는 공간별 검색 결과, clusters는 군집 라벨·대표·반대 사례,
    features는 sample_id 열이 있는 형상 특징 표다. 형상 전용 검색(shape_only)과
    위험 수치를 포함한 검색(with_risk)의 결과는 구분해 담는다.
    """
    reps = set(clusters.get("representatives", {}).values())
    design_reviews = list(design_reviews or [])
    counters = {v for v in clusters.get("counter_examples", {}).values() if v is not None}
    cards = []
    for n, (comparison_group_id, group) in enumerate(
        ranked.groupby("comparison_group_id", sort=True), start=1
    ):
        failure_type = group["failure_type"].iloc[0]
        roles = group["role_key"].iloc[0]
        layers = group["layer_key"].iloc[0]
        ids = list(group["sample_id"])
        products = sorted(group["product_id"].unique())
        order = _order_value(group)
        by_value = group.assign(_v=order).sort_values(["_v", "sample_id"], ascending=[False, True])
        valued = by_value[by_value["_v"].notna()]
        representative = [s for s in ids if s in reps] or list(valued["sample_id"].head(2))
        counter = list(valued["sample_id"].tail(2)[::-1]) + [s for s in ids if s in counters]
        counter = [s for s in dict.fromkeys(counter) if s not in representative]

        retrieval = {"note": RETRIEVAL_NOTE}
        for space in sorted(neighbors["space"].unique()) if len(neighbors) else []:
            subset = neighbors[(neighbors["space"] == space) & neighbors["query_id"].isin(ids)]
            retrieval[space] = {
                q: list(g.sort_values("rank")["neighbor_id"]) for q, g in subset.groupby("query_id")
            }

        unavailable = []
        no_scale = sorted(group.loc[~group["has_physical_scale"].astype(bool), "sample_id"])
        if no_scale:
            unavailable.append(f"unit-pixel 실제 크기 없음(면적 보정 비교 불가): {no_scale}")
        for _, row in group[group["caa_actual"].isna()].iterrows():
            unavailable.append(f"{row['sample_id']} CAA 계산 불가: {row['caa_actual_reason']}")
        if len(products) < min_products:
            unavailable.append("비교 가능한 제품 수가 부족합니다")
        if group["fab_id"].nunique() < 2:
            unavailable.append("이 그룹에는 Fab이 하나뿐이라 Fab 영향을 비교할 수 없습니다")
        available = [f"제품 {len(products)}개가 전극 역할 {roles}, layer {layers}를 공유합니다"]
        if group["caa_reference"].notna().any():
            available.append("기준 분포 적용 순위 비교 가능")
        if group["has_physical_scale"].astype(bool).sum() >= 2:
            available.append("실제 크기가 있는 제품끼리 면적 대비 CA 비교 가능")

        missing_ca = sorted(
            group.loc[group.filter(like="ca@").isna().any(axis=1), "sample_id"]
        )
        review = [
            "제품 간 전극 역할·layer 대응 확인",
            "CAA 가중 규칙과 Fab 자료 단위 확인",
        ]
        if missing_ca:
            review.append(f"누락된 이물 크기의 CA 재계산 필요: {missing_ca}")
        if group["caa_is_demo"].astype(bool).any():
            review.append("CAA 값은 DEMO 규칙으로 계산됨: 해석 전에 확정된 규칙으로 교체 필요")
        external_reviews = [
            item for item in design_reviews if item.get("comparison_group_id") == comparison_group_id
        ]

        cards.append(_clean({
            "card_id": f"card-{n}",
            "comparison_group_id": comparison_group_id,
            "failure_type": failure_type,
            "electrode_roles": roles,
            "layers": layers,
            "products": products,
            "fabs": sorted(group["fab_id"].unique()),
            "n_comparable_products": len(products),
            "status": "comparable" if len(products) >= min_products else "insufficient_products",
            "representative_cases": representative,
            "counter_cases": counter,
            "repeated_shape_features": _repeated_features(group, features),
            "repeated_shape_features_note": "CAA 또는 면적 보정 CA 기준 고위험 사례들 사이에서 변동이 작은 특징(기술적 요약이며 인과 아님)",
            "risk_comparison": group[[
                "sample_id", "product_id", "ca_max", "ca_area_max", "caa_actual", "caa_reference",
                "rank_shift_fab",
            ]].to_dict(orient="records"),
            "size_correction": group.loc[
                group["rank_shift_area"].fillna(0) != 0, ["sample_id", "rank_ca_max", "rank_ca_area_max"]
            ].to_dict(orient="records"),
            "particle_size_summary": _particle_size_summary(group),
            "improvement_candidates": _feature_deltas(group, features),
            "neighborhood": _neighborhood_entries(comparison_group_id, neighborhood,
                                                  neighborhood_geometry),
            "neighborhood_note": NEIGHBORHOOD_NOTE,
            "retrieval": retrieval,
            "available_conditions": available,
            "unavailable_conditions": unavailable,
            "expert_review_required": review,
            "verification_status": {
                "particle_resimulation": "pending",
                "resistance": "external_review_required",
                "capacitance": "external_review_required",
                "coupling": "external_review_required",
                "signal_delay_or_integrity": "external_review_required",
                "design_rule": "external_review_required",
                "other_short_open_risk": "external_review_required",
            },
            "external_design_reviews": external_reviews,
            "disclaimer": DISCLAIMER,
        }))
    return cards
