"""Similar-case search for display particle-defect simulation results."""

from __future__ import annotations

import numpy as np
import pandas as pd

from simcompare.schema import NOT_APPLICABLE

_COLUMNS = [
    "query_id",
    "neighbor_id",
    "rank",
    "distance",
    "space",
    "neighbor_product_id",
    "neighbor_fab_id",
    "n_candidates",
]


def role_key(row: pd.Series) -> tuple:
    """Return (failure_type, *sorted roles excluding NOT_APPLICABLE)."""
    ft = row["failure_type"]
    roles = [row["electrode_a_role"], row["electrode_b_role"]]
    roles = [r for r in roles if r is not None and r != NOT_APPLICABLE]
    return (ft, *sorted(roles))


def layer_key(row: pd.Series) -> tuple:
    """Return sorted layers excluding NOT_APPLICABLE."""
    layers = [row["layer_a"], row["layer_b"]]
    layers = [l for l in layers if l is not None and l != NOT_APPLICABLE]
    return tuple(sorted(layers))


def candidate_mask(
    meta: pd.DataFrame,
    query_pos: int,
    *,
    layer_policy: str = "exact",
    other_products_only: bool = False,
    filters: dict | None = None,
    size_tolerance: float | None = None,
) -> np.ndarray:
    """Boolean mask of candidate rows for the query at *query_pos* (iloc)."""
    if layer_policy not in ("exact", "ignore"):
        raise ValueError(
            f"layer_policy must be 'exact' or 'ignore', got {layer_policy!r}"
        )

    n = len(meta)
    mask = np.ones(n, dtype=bool)

    query_row = meta.iloc[query_pos]
    q_role = role_key(query_row)

    # Same role_key always required.
    for i in range(n):
        if role_key(meta.iloc[i]) != q_role:
            mask[i] = False

    # Layer constraint.
    if layer_policy == "exact":
        q_layer = layer_key(query_row)
        for i in range(n):
            if mask[i] and layer_key(meta.iloc[i]) != q_layer:
                mask[i] = False

    # Exclude the query row itself.
    mask[query_pos] = False

    # Other products only.
    if other_products_only:
        q_product = query_row["product_id"]
        for i in range(n):
            if mask[i] and meta.iloc[i]["product_id"] == q_product:
                mask[i] = False

    # Filters.
    if filters:
        for col, allowed in filters.items():
            if col not in meta.columns:
                raise ValueError(f"Unknown filter column: {col!r}")
            allowed_set = set(allowed)
            for i in range(n):
                if mask[i] and meta.iloc[i][col] not in allowed_set:
                    mask[i] = False

    # Size tolerance.
    if size_tolerance is not None:
        if size_tolerance < 0:
            raise ValueError(f"size_tolerance must be >= 0, got {size_tolerance}")
        log_tol = np.log(1.0 + size_tolerance)
        q_w = query_row["unit_width"]
        q_h = query_row["unit_height"]
        q_w_ok = q_w is not None and np.isfinite(q_w) and q_w > 0
        q_h_ok = q_h is not None and np.isfinite(q_h) and q_h > 0
        for i in range(n):
            if not mask[i]:
                continue
            w = meta.iloc[i]["unit_width"]
            h = meta.iloc[i]["unit_height"]
            w_ok = w is not None and np.isfinite(w) and w > 0
            h_ok = h is not None and np.isfinite(h) and h > 0
            if not (q_w_ok and q_h_ok and w_ok and h_ok):
                mask[i] = False
                continue
            if abs(np.log(w / q_w)) > log_tol or abs(np.log(h / q_h)) > log_tol:
                mask[i] = False

    return mask


def _check_embeddings(embeddings: np.ndarray, meta: pd.DataFrame) -> None:
    if len(embeddings) != len(meta):
        raise ValueError(
            f"len(embeddings)={len(embeddings)} != len(meta)={len(meta)}"
        )


def _validate_finite(embeddings: np.ndarray, positions: np.ndarray) -> None:
    for p in positions:
        if not np.all(np.isfinite(embeddings[p])):
            raise ValueError(f"Non-finite embedding at position {p}")


def search(
    embeddings: np.ndarray,
    meta: pd.DataFrame,
    query_pos: int,
    *,
    k: int,
    space: str,
    metric: str = "euclidean",
    **constraints,
) -> pd.DataFrame:
    """Search for the *k* nearest neighbors of the query row."""
    embeddings = np.asarray(embeddings, dtype=np.float64)
    _check_embeddings(embeddings, meta)

    mask = candidate_mask(meta, query_pos, **constraints)
    cand_idx = np.where(mask)[0]

    if len(cand_idx) == 0:
        return pd.DataFrame(columns=_COLUMNS)

    # Validate finite embeddings for query and all candidates.
    _validate_finite(embeddings, np.array([query_pos]))
    _validate_finite(embeddings, cand_idx)

    q_vec = embeddings[query_pos]
    c_vecs = embeddings[cand_idx]

    if metric == "euclidean":
        dists = np.sqrt(np.sum((c_vecs - q_vec) ** 2, axis=1))
    elif metric == "cosine":
        q_norm = np.linalg.norm(q_vec)
        c_norms = np.linalg.norm(c_vecs, axis=1)
        denom = q_norm * c_norms
        with np.errstate(divide="ignore", invalid="ignore"):
            safe_denom = np.where(denom > 0, denom, 1.0)
            cos_sim = np.where(denom > 0, np.dot(c_vecs, q_vec) / safe_denom, 0.0)
        dists = 1.0 - cos_sim
    else:
        raise ValueError(f"Unknown metric: {metric!r}")

    # Sort by distance ascending; ties broken by sample_id.
    sample_ids = meta.iloc[cand_idx]["sample_id"].values
    sort_keys = np.lexsort((sample_ids, dists))
    ordered_cand = cand_idx[sort_keys]
    ordered_dists = dists[sort_keys]

    top_k = min(k, len(ordered_cand))
    sel_cand = ordered_cand[:top_k]
    sel_dists = ordered_dists[:top_k]

    query_id = meta.iloc[query_pos]["sample_id"]
    neighbor_ids = meta.iloc[sel_cand]["sample_id"].values
    neighbor_products = meta.iloc[sel_cand]["product_id"].values
    neighbor_fabs = meta.iloc[sel_cand]["fab_id"].values

    rows = []
    for rank, (nid, dist, npid, nfid) in enumerate(
        zip(neighbor_ids, sel_dists, neighbor_products, neighbor_fabs), start=1
    ):
        rows.append(
            {
                "query_id": query_id,
                "neighbor_id": nid,
                "rank": rank,
                "distance": float(dist),
                "space": space,
                "neighbor_product_id": npid,
                "neighbor_fab_id": nfid,
                "n_candidates": len(cand_idx),
            }
        )

    return pd.DataFrame(rows, columns=_COLUMNS)


def search_all(
    embeddings: np.ndarray,
    meta: pd.DataFrame,
    *,
    k: int,
    space: str,
    positions: list[int] | None = None,
    **constraints,
) -> pd.DataFrame:
    """Run search over *positions* (default: all rows) and concatenate."""
    embeddings = np.asarray(embeddings, dtype=np.float64)
    _check_embeddings(embeddings, meta)

    if positions is None:
        positions = list(range(len(meta)))

    frames = [
        search(embeddings, meta, pos, k=k, space=space, **constraints)
        for pos in positions
    ]
    if not frames:
        return pd.DataFrame(columns=_COLUMNS)
    return pd.concat(frames, ignore_index=True)
