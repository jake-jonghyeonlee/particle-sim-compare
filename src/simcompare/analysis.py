"""Cross-product comparison: risk tables, rankings, clustering and stability.

Cluster ids are arbitrary labels without physical meaning. Rankings compare
already computed values; they do not establish causes.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

from simcompare.caa import CAAError, compute_caa, interpret_fab, select_distribution
from simcompare.preprocess import align_curve
from simcompare.retrieval import layer_key, role_key, search_all

CLUSTER_NOTE = "cluster ids are arbitrary labels without physical meaning"


def cluster_embeddings(emb: np.ndarray, train_pos, n_clusters: int, seed: int) -> dict:
    train_pos = np.asarray(train_pos, dtype=int)
    k = max(1, min(int(n_clusters), len(train_pos)))
    model = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(emb[train_pos])
    labels = model.predict(emb)
    representatives: dict[int, int] = {}
    counter_examples: dict[int, int | None] = {}
    for c, centroid in enumerate(model.cluster_centers_):
        dist = np.linalg.norm(emb - centroid, axis=1)
        representatives[c] = int(np.argmin(dist))
        others = train_pos[labels[train_pos] != c]
        counter_examples[c] = int(others[np.argmin(dist[others])]) if others.size else None
    return {
        "labels": labels,
        "representatives": representatives,
        "counter_examples": counter_examples,
        "note": CLUSTER_NOTE,
    }


def _caa(bundle, dist_or_error, cfg) -> tuple[float, str, bool | None]:
    if isinstance(dist_or_error, str):
        return np.nan, dist_or_error, None
    if bundle.ca is None:
        return np.nan, "no CA curve", None
    try:
        weights = interpret_fab(dist_or_error, cfg.caa.rule, mode=cfg.mode, confirmed=cfg.caa.confirmed)
        result = compute_caa(bundle.ca, weights, require_full_coverage=cfg.caa.require_full_coverage)
    except CAAError as exc:
        return np.nan, str(exc), None
    value = np.nan if result.value is None else float(result.value)
    reason = result.reason or result.label
    if result.value is not None and result.reason:
        reason = f"{result.label}; {result.reason}"
    return value, reason, bool(result.is_demo)


def risk_table(bundles, distributions, cfg) -> pd.DataFrame:
    grid = np.asarray(cfg.data.ca_grid, dtype=float)
    ref_id = cfg.caa.reference_distribution_id
    rows = []
    for bundle in bundles:
        row = {
            "sample_id": bundle.sample_id, "product_id": bundle.product_id,
            "product_family": bundle.product_family, "fab_id": bundle.fab_id,
            "failure_type": bundle.failure_type,
            "role_key": "|".join(role_key(_as_row(bundle))),
            "layer_key": "|".join(layer_key(_as_row(bundle))),
            "has_physical_scale": bundle.has_physical_scale(),
        }
        raw = np.full(len(grid), np.nan)
        area = np.full(len(grid), np.nan)
        if bundle.ca is not None:
            raw, raw_mask = align_curve(bundle.ca.particle_sizes, bundle.ca.values,
                                        bundle.ca.observed, grid, cfg.data.ca_alignment)
            raw = np.where(raw_mask, raw, np.nan)
            per_area, _ = bundle.ca_per_area()
            if per_area is not None:
                area, area_mask = align_curve(bundle.ca.particle_sizes, per_area,
                                              bundle.ca.observed, grid, cfg.data.ca_alignment)
                area = np.where(area_mask, area, np.nan)
        for j, size in enumerate(grid):
            row[f"ca@{size:g}"] = raw[j]
            row[f"ca_area@{size:g}"] = area[j]
        row["ca_max"] = float(np.nanmax(raw)) if np.isfinite(raw).any() else np.nan
        row["ca_area_max"] = float(np.nanmax(area)) if np.isfinite(area).any() else np.nan
        try:
            actual = select_distribution(bundle, distributions, cfg.caa.process_layer_overrides)
        except CAAError as exc:
            actual = str(exc)
        row["caa_actual"], row["caa_actual_reason"], demo_a = _caa(bundle, actual, cfg)
        if ref_id is None:
            reference = "no reference distribution configured"
        elif ref_id not in distributions:
            reference = f"reference distribution {ref_id!r} not found"
        else:
            reference = distributions[ref_id]
        row["caa_reference"], row["caa_reference_reason"], demo_r = _caa(bundle, reference, cfg)
        row["caa_is_demo"] = bool(demo_a or demo_r)
        rows.append(row)
    return pd.DataFrame(rows)


def _as_row(bundle) -> dict:
    return {
        "failure_type": bundle.failure_type,
        "electrode_a_role": bundle.electrode_a_role, "electrode_b_role": bundle.electrode_b_role,
        "layer_a": bundle.layer_a, "layer_b": bundle.layer_b,
    }


def ranking_comparison(table: pd.DataFrame) -> pd.DataFrame:
    ranked = table.copy()
    for source, target in (
        ("ca_max", "rank_ca_max"), ("ca_area_max", "rank_ca_area_max"),
        ("caa_actual", "rank_caa_actual"), ("caa_reference", "rank_caa_reference"),
    ):
        ranked[target] = ranked.groupby("failure_type")[source].rank(
            ascending=False, method="min", na_option="keep"
        )
    ranked["rank_shift_fab"] = ranked["rank_caa_reference"] - ranked["rank_caa_actual"]
    ranked["rank_shift_area"] = ranked["rank_ca_area_max"] - ranked["rank_ca_max"]
    return ranked


def _spearman(a: pd.Series, b: pd.Series) -> tuple[float | None, int]:
    both = a.notna() & b.notna()
    n = int(both.sum())
    if n < 3:
        return None, n
    value = a[both].corr(b[both], method="spearman")
    return (None if pd.isna(value) else float(value)), n


def rank_agreement(ranked: pd.DataFrame) -> list[dict]:
    out = []
    for failure_type, group in ranked.groupby("failure_type", sort=True):
        fab, n_fab = _spearman(group["caa_actual"], group["caa_reference"])
        area, n_area = _spearman(group["ca_max"], group["ca_area_max"])
        out.append({
            "failure_type": failure_type, "n": n_fab, "n_area": n_area,
            "spearman_actual_vs_reference": fab, "spearman_raw_vs_area": area,
        })
    return out


def neighbor_sets(neighbors: pd.DataFrame) -> dict[str, set[str]]:
    return {
        query: set(group["neighbor_id"]) for query, group in neighbors.groupby("query_id")
    }


def jaccard(a: set, b: set) -> float | None:
    if not a and not b:
        return None
    return len(a & b) / len(a | b)


def _top_items(frame: pd.DataFrame, top_k: int) -> dict[str, set[str]]:
    out = {}
    for failure_type, group in frame.groupby("failure_type"):
        valid = group[group["caa_actual"].notna()]
        out[failure_type] = set(valid.sort_values(["caa_actual", "sample_id"],
                                                  ascending=[False, True]).head(top_k)["sample_id"])
    return out


def leave_one_product_out(ranked, meta, embed_fn, *, k, top_k, search_kwargs=None,
                          train_pos=None) -> pd.DataFrame:
    search_kwargs = dict(search_kwargs or {})
    meta = meta.reset_index(drop=True)
    positions = np.arange(len(meta))
    train_pos = positions if train_pos is None else np.asarray(train_pos, dtype=int)
    full_top = _top_items(ranked, top_k)
    emb_full = embed_fn(train_pos)
    rows = []
    for product in sorted(meta["product_id"].unique()):
        keep = positions[(meta["product_id"] != product).to_numpy()]
        keep_ids = set(meta.loc[keep, "sample_id"])
        loo_ranked = ranked[ranked["sample_id"].isin(keep_ids)]
        loo_top = _top_items(loo_ranked, top_k)
        # Full-data top items minus the held-out product vs top items re-ranked
        # without it: shows whether the held-out product displaced other cases.
        scores = [
            jaccard({s for s in full_top.get(ft, set()) if s in keep_ids}, loo_top.get(ft, set()))
            for ft in sorted(set(full_top) | set(loo_top))
        ]
        scores = [s for s in scores if s is not None]
        emb_loo = embed_fn(np.intersect1d(train_pos, keep))
        sub_meta = meta.iloc[keep].reset_index(drop=True)
        full_nb = search_all(emb_full[keep], sub_meta, k=k, space="full", **search_kwargs)
        loo_nb = search_all(emb_loo[keep], sub_meta, k=k, space="loo", **search_kwargs)
        full_sets, loo_sets = neighbor_sets(full_nb), neighbor_sets(loo_nb)
        nb_scores = [jaccard(full_sets.get(q, set()), loo_sets.get(q, set()))
                     for q in sorted(set(full_sets) | set(loo_sets))]
        nb_scores = [s for s in nb_scores if s is not None]
        rows.append({
            "held_out_product": product,
            "top_items_jaccard": float(np.mean(scores)) if scores else None,
            "neighbor_jaccard": float(np.mean(nb_scores)) if nb_scores else None,
            "n_queries": len(nb_scores),
        })
    return pd.DataFrame(rows)
