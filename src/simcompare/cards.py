"""Review cards: evidence bundles for expert review (no automatic factor judgement)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

DISCLAIMER = (
    "Evidence for expert review only. No causal importance, automatic factor ranking, "
    "or yield/defect-rate claim."
)
RETRIEVAL_NOTE = "with_risk neighbors reuse CA/hotspot inputs; not independent findings"


def _clean(value):
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
    return group["caa_actual"].where(group["caa_actual"].notna(), group["ca_max"])


def _cv(values: pd.Series) -> float | None:
    values = pd.to_numeric(values, errors="coerce").dropna()
    if len(values) < 2 or abs(values.mean()) <= 1e-12:
        return None
    return float(values.std(ddof=0) / abs(values.mean()))


def _repeated_features(group: pd.DataFrame, features: pd.DataFrame) -> list[str]:
    """Features that vary across the group but stay similar among higher-risk cases.

    Constant features (e.g. identical thickness everywhere) are skipped because
    their consistency says nothing about the higher-risk cases.
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


def build_review_cards(ranked: pd.DataFrame, neighbors: pd.DataFrame, clusters: dict,
                       features: pd.DataFrame, *, min_products: int = 2) -> list[dict]:
    reps = set(clusters.get("representatives", {}).values())
    counters = {v for v in clusters.get("counter_examples", {}).values() if v is not None}
    cards = []
    keys = ["failure_type", "role_key", "layer_key"]
    for n, (key, group) in enumerate(ranked.groupby(keys, sort=True), start=1):
        failure_type, roles, layers = key
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
            unavailable.append(f"no physical unit-pixel size (size-corrected comparison impossible): {no_scale}")
        for _, row in group[group["caa_actual"].isna()].iterrows():
            unavailable.append(f"CAA unavailable for {row['sample_id']}: {row['caa_actual_reason']}")
        if len(products) < min_products:
            unavailable.append("fewer comparable products than required")
        if group["fab_id"].nunique() < 2:
            unavailable.append("single Fab in this group; Fab effect cannot be compared")
        available = [f"{len(products)} product(s) share roles {roles} and layers {layers}"]
        if group["caa_reference"].notna().any():
            available.append("reference-distribution ranking available")
        if group["has_physical_scale"].astype(bool).sum() >= 2:
            available.append("CA per unit area comparable for products with physical size")

        missing_ca = sorted(
            group.loc[group.filter(like="ca@").isna().any(axis=1), "sample_id"]
        )
        review = [
            "confirm electrode role / layer correspondence across products",
            "confirm CAA weighting rule and Fab units",
        ]
        if missing_ca:
            review.append(f"recompute CA at missing sizes: {missing_ca}")
        if group["caa_is_demo"].astype(bool).any():
            review.append("CAA values use a DEMO rule; replace with a confirmed rule before interpretation")

        cards.append(_clean({
            "card_id": f"card-{n}",
            "failure_type": failure_type,
            "electrode_roles": roles,
            "layers": layers,
            "products": products,
            "n_comparable_products": len(products),
            "status": "comparable" if len(products) >= min_products else "insufficient_products",
            "representative_cases": representative,
            "counter_cases": counter,
            "repeated_shape_features": _repeated_features(group, features),
            "repeated_shape_features_note": "less variation among higher-risk cases than across the group; descriptive, not causal",
            "risk_comparison": group[[
                "sample_id", "product_id", "ca_max", "ca_area_max", "caa_actual", "caa_reference",
                "rank_shift_fab",
            ]].to_dict(orient="records"),
            "size_correction": group.loc[
                group["rank_shift_area"].fillna(0) != 0, ["sample_id", "rank_ca_max", "rank_ca_area_max"]
            ].to_dict(orient="records"),
            "retrieval": retrieval,
            "available_conditions": available,
            "unavailable_conditions": unavailable,
            "expert_review_required": review,
            "disclaimer": DISCLAIMER,
        }))
    return cards
