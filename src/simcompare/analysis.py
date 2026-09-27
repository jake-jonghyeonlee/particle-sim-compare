"""제품 간 비교 분석: 위험 표, 순위 비교, 군집화, 제품 제외 안정성.

이미 계산된 값을 비교할 뿐 원인을 판정하지 않는다. 군집 번호는 임의의 라벨이다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

from simcompare.caa import CAAError, compute_caa, interpret_fab, select_distribution
from simcompare.preprocess import align_curve
from simcompare.retrieval import candidate_mask, layer_key, role_key, search_all
from simcompare.schema import convert_length

CLUSTER_NOTE = "군집 번호는 임의의 라벨이며 물리적 의미가 없습니다"


def cluster_embeddings(emb: np.ndarray, train_pos, n_clusters: int, seed: int) -> dict:
    """학습 행으로 KMeans를 적합하고 전체 행에 군집 라벨을 붙인다.

    대표 사례는 각 중심에 가장 가까운 행, 반대 사례는 다른 군집에 속하면서
    그 중심에 가장 가까운 학습 행이다.
    """
    train_pos = np.asarray(train_pos, dtype=int)
    k = max(1, min(int(n_clusters), len(train_pos)))
    # 군집 중심은 학습 행으로만 적합한다.
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
    """선택된 분포(또는 선택 실패 사유)로 CAA 값, 사유, demo 여부를 계산한다. 실패하면 NaN과 사유를 반환한다."""
    if isinstance(dist_or_error, str):
        return np.nan, dist_or_error, None
    if bundle.ca is None:
        return np.nan, "CA 곡선 없음", None
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
    """분석 묶음별 위험 비교 표를 만든다.

    크기별 원 CA와 면적 대비 CA, 최대값, 실제 Fab 분포 CAA, 기준 분포 CAA와 각 사유를 담는다.
    계산할 수 없는 값은 NaN과 사유로 남기고 0으로 채우지 않는다.
    """
    grid = np.asarray(cfg.data.ca_grid, dtype=float)
    canonical_unit = cfg.data.canonical_length_unit
    ref_id = cfg.caa.reference_distribution_id
    rows = []
    for bundle in bundles:
        roles = "|".join(role_key(_as_row(bundle)))
        layers = "|".join(layer_key(_as_row(bundle)))
        row = {
            "sample_id": bundle.sample_id, "product_id": bundle.product_id,
            "product_family": bundle.product_family, "fab_id": bundle.fab_id,
            "failure_type": bundle.failure_type,
            "assigned_process_layer": bundle.assigned_process_layer,
            "fab_pair_key": bundle.fab_pair_key,
            "mapping_contract_version": bundle.mapping_contract_version,
            "role_key": roles,
            "layer_key": layers,
            "comparison_group_id": f"{roles}::{layers}",
            "has_physical_scale": bundle.has_physical_scale(),
            "ca_unit": "1",
            "ca_area_unit": f"1/{canonical_unit}^2",
        }
        raw = np.full(len(grid), np.nan)
        area = np.full(len(grid), np.nan)
        if bundle.ca is not None:
            ca_sizes = convert_length(
                bundle.ca.particle_sizes, bundle.ca.particle_size_unit, canonical_unit
            )
            raw, raw_mask = align_curve(ca_sizes, bundle.ca.values,
                                        bundle.ca.observed, grid, cfg.data.ca_alignment)
            raw = np.where(raw_mask, raw, np.nan)
            per_area, _ = bundle.ca_per_area(canonical_unit)
            if per_area is not None:
                area, area_mask = align_curve(ca_sizes, per_area,
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
        row["selected_fab_distribution_id"] = None if isinstance(actual, str) else actual.fab_distribution_id
        row["caa_actual"], row["caa_actual_reason"], demo_a = _caa(bundle, actual, cfg)
        if ref_id is None:
            reference = "기준 분포가 설정되지 않았습니다"
        elif ref_id not in distributions:
            reference = f"기준 분포 {ref_id!r}을(를) 찾을 수 없습니다"
        elif not distributions[ref_id].is_reference:
            reference = f"분포 {ref_id!r}은(는) is_reference=true가 아닙니다"
        elif bundle.mapping_contract_version == "1.2" and distributions[ref_id].fab_pair_key is not None:
            reference = "1.2 reference distributions cannot carry fab_pair_key"
        else:
            reference = distributions[ref_id]
        row["caa_reference"], row["caa_reference_reason"], demo_r = _caa(bundle, reference, cfg)
        row["caa_is_demo"] = bool(demo_a or demo_r)
        rows.append(row)
    return pd.DataFrame(rows)


def _as_row(bundle) -> dict:
    """retrieval의 role_key/layer_key에 넘길 최소 필드 dict를 만든다."""
    return {
        "failure_type": bundle.failure_type,
        "electrode_a_role": bundle.electrode_a_role, "electrode_b_role": bundle.electrode_b_role,
        "layer_a": bundle.layer_a, "layer_b": bundle.layer_b,
    }


def ranking_comparison(table: pd.DataFrame) -> pd.DataFrame:
    """같은 불량·전극 역할·layer 비교 그룹 안에서 네 위험 지표의 순위를 매긴다.

    rank_shift_fab은 기준 분포 적용 시 순위 변화, rank_shift_area는 면적 보정 시 순위 변화다.
    NaN 값은 순위를 매기지 않는다.
    """
    ranked = table.copy()
    for source, target in (
        ("ca_max", "rank_ca_max"), ("ca_area_max", "rank_ca_area_max"),
        ("caa_actual", "rank_caa_actual"), ("caa_reference", "rank_caa_reference"),
    ):
        ranked[target] = ranked.groupby("comparison_group_id")[source].rank(
            ascending=False, method="min", na_option="keep"
        )
    ranked["rank_shift_fab"] = ranked["rank_caa_reference"] - ranked["rank_caa_actual"]
    ranked["rank_shift_area"] = ranked["rank_ca_area_max"] - ranked["rank_ca_max"]
    return ranked


def _spearman(a: pd.Series, b: pd.Series) -> tuple[float | None, int]:
    """두 열이 모두 유한한 행으로 Spearman 상관을 구한다. 3행 미만이면 None."""
    both = a.notna() & b.notna()
    n = int(both.sum())
    if n < 3:
        return None, n
    value = a[both].corr(b[both], method="spearman")
    return (None if pd.isna(value) else float(value)), n


def rank_agreement(ranked: pd.DataFrame) -> list[dict]:
    """비교 그룹별 실제 Fab 대 기준 분포, 원 CA 대 면적 대비 CA의 순위 상관을 요약한다."""
    out = []
    for comparison_group_id, group in ranked.groupby("comparison_group_id", sort=True):
        fab, n_fab = _spearman(group["caa_actual"], group["caa_reference"])
        area, n_area = _spearman(group["ca_max"], group["ca_area_max"])
        out.append({
            "comparison_group_id": comparison_group_id,
            "failure_type": group["failure_type"].iloc[0],
            "role_key": group["role_key"].iloc[0],
            "layer_key": group["layer_key"].iloc[0],
            "n": n_fab, "n_area": n_area,
            "spearman_actual_vs_reference": fab, "spearman_raw_vs_area": area,
        })
    return out


def neighbor_sets(neighbors: pd.DataFrame) -> dict[str, set[str]]:
    """검색 결과 표를 질의별 이웃 ID 집합으로 바꾼다."""
    return {
        query: set(group["neighbor_id"]) for query, group in neighbors.groupby("query_id")
    }


def jaccard(a: set, b: set) -> float | None:
    """두 집합의 Jaccard 유사도. 둘 다 비어 있으면 None."""
    if not a and not b:
        return None
    return len(a & b) / len(a | b)


def _top_items(frame: pd.DataFrame, top_k: int) -> dict[str, set[str]]:
    """비교 그룹별 실제 Fab CAA 상위 top_k 샘플 ID 집합을 구한다."""
    out = {}
    for comparison_group_id, group in frame.groupby("comparison_group_id"):
        valid = group[group["caa_actual"].notna()]
        out[comparison_group_id] = set(valid.sort_values(["caa_actual", "sample_id"],
                                                         ascending=[False, True]).head(top_k)["sample_id"])
    return out


class InsufficientEmbeddingData(ValueError):
    """임베딩 재적합에 필요한 관측 자료가 부족함 (메시지는 기록할 reason 코드)."""


def leave_one_product_out(ranked, meta, embed_fn, *, k, top_k, search_kwargs=None,
                          train_pos=None) -> pd.DataFrame:
    """제품을 하나씩 제외했을 때 상위 항목과 유사 사례가 유지되는지 비교한다.

    - top_items_jaccard: 전체 순위 상위 항목(제외 제품 빼고)과 제외 후 재순위 상위 항목의 일치도
    - neighbor_jaccard: 전체 학습 행으로 만든 임베딩과 제외 후 다시 적합한 임베딩의 이웃 일치도
    embed_fn(fit_pos)는 fit_pos로 적합해 전체 행의 임베딩을 반환해야 한다.
    """
    search_kwargs = dict(search_kwargs or {})
    meta = meta.reset_index(drop=True)
    positions = np.arange(len(meta))
    train_pos = positions if train_pos is None else np.asarray(train_pos, dtype=int)
    full_top = _top_items(ranked, top_k)
    full_reason = None
    try:
        if len(train_pos) < 2:
            raise InsufficientEmbeddingData("insufficient_training_rows")
        emb_full = embed_fn(train_pos)
    except InsufficientEmbeddingData as exc:
        full_reason = str(exc)
    rows = []
    for product in sorted(meta["product_id"].unique()):
        keep = positions[(meta["product_id"] != product).to_numpy()]
        fit_pos = np.intersect1d(train_pos, keep)
        try:
            if full_reason:
                raise InsufficientEmbeddingData(full_reason)
            if len(fit_pos) < 2:
                raise InsufficientEmbeddingData("insufficient_training_rows")
            emb_loo = embed_fn(fit_pos)
        except InsufficientEmbeddingData as exc:
            rows.append({
                "held_out_product": product, "status": "skipped", "reason": str(exc),
                "n_training_rows": len(fit_pos), "top_items_jaccard": None,
                "neighbor_jaccard": None, "n_queries": 0,
            })
            continue
        keep_ids = set(meta.loc[keep, "sample_id"])
        loo_ranked = ranked[ranked["sample_id"].isin(keep_ids)]
        loo_top = _top_items(loo_ranked, top_k)
        # 전체 순위 상위 항목(제외 제품 제거)과 제외 후 다시 매긴 상위 항목을 비교해
        # 제외한 제품이 다른 사례의 순위를 밀어냈는지 확인한다.
        scores = [
            jaccard({s for s in full_top.get(ft, set()) if s in keep_ids}, loo_top.get(ft, set()))
            for ft in sorted(set(full_top) | set(loo_top))
        ]
        scores = [s for s in scores if s is not None]
        sub_meta = meta.iloc[keep].reset_index(drop=True)
        full_nb = search_all(emb_full[keep], sub_meta, k=k, space="full", **search_kwargs)
        loo_nb = search_all(emb_loo[keep], sub_meta, k=k, space="loo", **search_kwargs)
        full_sets, loo_sets = neighbor_sets(full_nb), neighbor_sets(loo_nb)
        nb_scores = [jaccard(full_sets.get(q, set()), loo_sets.get(q, set()))
                     for q in sorted(set(full_sets) | set(loo_sets))]
        nb_scores = [s for s in nb_scores if s is not None]
        rows.append({
            "held_out_product": product,
            "status": "computed", "reason": None, "n_training_rows": len(fit_pos),
            "top_items_jaccard": float(np.mean(scores)) if scores else None,
            "neighbor_jaccard": float(np.mean(nb_scores)) if nb_scores else None,
            "n_queries": len(nb_scores),
        })
    return pd.DataFrame(rows)


def held_out_product_evidence(
    embeddings: np.ndarray,
    meta: pd.DataFrame,
    ranked: pd.DataFrame,
    train_pos,
    test_pos,
    *,
    k: int,
    search_kwargs: dict | None = None,
) -> dict:
    """보지 않은 평가 제품을 query, 학습 제품만 candidate로 사용한 반복성 근거를 만든다.

    전문가 정답 라벨이 없으므로 정확도나 recall을 계산하지 않는다. 형상 전용 공간에서 찾은
    학습 이웃과 query의 면적 정규화 CA가 같은 비교 그룹 중앙값 방향인지 보고한다.
    """
    embeddings = np.asarray(embeddings, dtype=float)
    train_pos = np.asarray(train_pos, dtype=int)
    test_pos = np.asarray(test_pos, dtype=int)
    search_kwargs = dict(search_kwargs or {})
    train_set = set(int(p) for p in train_pos)
    risk = ranked.set_index("sample_id")
    rows = []
    for query_pos in test_pos:
        mask = candidate_mask(meta, int(query_pos), **search_kwargs)
        candidates = np.array([p for p in np.flatnonzero(mask) if int(p) in train_set], dtype=int)
        query_id = meta.iloc[query_pos]["sample_id"]
        if candidates.size == 0:
            rows.append({
                "query_id": query_id, "held_out_product": meta.iloc[query_pos]["product_id"],
                "comparison_group_id": risk.loc[query_id, "comparison_group_id"],
                "status": "no_train_candidates", "neighbors": [], "trend_agreement": None,
            })
            continue
        distances = np.linalg.norm(embeddings[candidates] - embeddings[query_pos], axis=1)
        ids = meta.iloc[candidates]["sample_id"].to_numpy()
        order = np.lexsort((ids, distances))[: min(k, len(candidates))]
        neighbor_ids = ids[order].tolist()
        group_id = risk.loc[query_id, "comparison_group_id"]
        train_group_ids = [
            meta.iloc[p]["sample_id"] for p in train_pos
            if meta.iloc[p]["sample_id"] in risk.index
            and risk.loc[meta.iloc[p]["sample_id"], "comparison_group_id"] == group_id
        ]
        group_values = pd.to_numeric(risk.loc[train_group_ids, "ca_area_max"], errors="coerce").dropna()
        query_value = risk.loc[query_id, "ca_area_max"]
        neighbor_values = pd.to_numeric(risk.loc[neighbor_ids, "ca_area_max"], errors="coerce").dropna()
        agreement = None
        if pd.notna(query_value) and not neighbor_values.empty and not group_values.empty:
            median = float(group_values.median())
            agreement = bool((float(query_value) >= median) == (float(neighbor_values.mean()) >= median))
        rows.append({
            "query_id": query_id,
            "held_out_product": meta.iloc[query_pos]["product_id"],
            "comparison_group_id": group_id,
            "status": "ok",
            "neighbors": neighbor_ids,
            "n_train_candidates": int(len(candidates)),
            "query_ca_per_area_max": None if pd.isna(query_value) else float(query_value),
            "neighbor_ca_per_area_mean": None if neighbor_values.empty else float(neighbor_values.mean()),
            "trend_agreement": agreement,
        })
    scored = [row["trend_agreement"] for row in rows if row.get("trend_agreement") is not None]
    return {
        "note": (
            "전문가 정답 라벨이 없어 정확도가 아니라 보지 않은 제품의 형상 이웃과 "
            "면적 정규화 CA 경향이 반복되는지 보는 근거입니다"
        ),
        "n_queries": len(rows),
        "n_scored": len(scored),
        "trend_agreement_rate": float(np.mean(scored)) if scored else None,
        "queries": rows,
    }
