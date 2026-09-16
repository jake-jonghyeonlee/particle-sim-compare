"""유사 사례 검색.

같은 불량 종류와 같은 전극 역할 집합(순서 무관) 안에서만 후보를 찾는다.
layer 조건, 다른 제품만 보기, 열 필터, 실제 크기 허용 비율로 후보를 더 제한할 수 있다.
space 라벨로 형상 전용 공간과 위험 수치를 포함한 공간의 결과를 구분한다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from simcompare.schema import NOT_APPLICABLE, convert_length

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
    """(불량 종류, 해당 없음을 제외하고 정렬한 전극 역할...) 튜플. 전극 순서가 달라도 같은 키가 된다."""
    ft = row["failure_type"]
    roles = [row["electrode_a_role"], row["electrode_b_role"]]
    roles = [r for r in roles if r is not None and r != NOT_APPLICABLE]
    return (ft, *sorted(roles))


def layer_key(row: pd.Series) -> tuple:
    """해당 없음을 제외하고 정렬한 layer 튜플."""
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
    """query_pos 행에 대한 검색 후보 여부(bool 배열)를 반환한다.

    - 같은 role_key는 항상 필요, 질의 행 자신은 제외
    - layer_policy: exact면 layer_key 일치, ignore면 layer 조건 없음
    - other_products_only: 다른 제품만
    - filters: {열 이름: 허용 값 목록}
    - size_tolerance: 가로·세로 실제 크기 비율이 모두 허용 범위 안인 후보만(크기 미상은 제외)
    """
    if layer_policy not in ("exact", "ignore"):
        raise ValueError(
            f"layer_policy는 'exact' 또는 'ignore'여야 합니다(현재 {layer_policy!r})"
        )

    n = len(meta)
    mask = np.ones(n, dtype=bool)

    query_row = meta.iloc[query_pos]
    q_role = role_key(query_row)

    # 같은 불량 종류·전극 역할 조합은 항상 필요하다.
    for i in range(n):
        if role_key(meta.iloc[i]) != q_role:
            mask[i] = False

    # layer 조건.
    if layer_policy == "exact":
        q_layer = layer_key(query_row)
        for i in range(n):
            if mask[i] and layer_key(meta.iloc[i]) != q_layer:
                mask[i] = False

    # 질의 행 자신은 제외한다.
    mask[query_pos] = False

    # 다른 제품만 보기.
    if other_products_only:
        q_product = query_row["product_id"]
        for i in range(n):
            if mask[i] and meta.iloc[i]["product_id"] == q_product:
                mask[i] = False

    # 열 필터.
    if filters:
        for col, allowed in filters.items():
            if col not in meta.columns:
                raise ValueError(f"알 수 없는 필터 열: {col!r}")
            allowed_set = set(allowed)
            for i in range(n):
                if mask[i] and meta.iloc[i][col] not in allowed_set:
                    mask[i] = False

    # 실제 크기 허용 비율.
    if size_tolerance is not None:
        if size_tolerance < 0:
            raise ValueError(f"size_tolerance는 0 이상이어야 합니다(현재 {size_tolerance})")
        log_tol = np.log(1.0 + size_tolerance)
        def physical_size(row, axis: str):
            canonical = f"unit_{axis}_canonical"
            if canonical in meta.columns:
                return row[canonical]
            raw = row[f"unit_{axis}"]
            if "physical_length_unit" in meta.columns and pd.notna(row.get("physical_length_unit")):
                return convert_length(raw, row["physical_length_unit"], "um")
            return raw

        q_w = physical_size(query_row, "width")
        q_h = physical_size(query_row, "height")
        q_w_ok = q_w is not None and np.isfinite(q_w) and q_w > 0
        q_h_ok = q_h is not None and np.isfinite(q_h) and q_h > 0
        for i in range(n):
            if not mask[i]:
                continue
            w = physical_size(meta.iloc[i], "width")
            h = physical_size(meta.iloc[i], "height")
            w_ok = w is not None and np.isfinite(w) and w > 0
            h_ok = h is not None and np.isfinite(h) and h > 0
            if not (q_w_ok and q_h_ok and w_ok and h_ok):
                mask[i] = False
                continue
            if abs(np.log(w / q_w)) > log_tol or abs(np.log(h / q_h)) > log_tol:
                mask[i] = False

    return mask


def _check_embeddings(embeddings: np.ndarray, meta: pd.DataFrame) -> None:
    """특징 벡터 행 수와 메타데이터 행 수가 같은지 확인한다."""
    if len(embeddings) != len(meta):
        raise ValueError(
            f"특징 벡터 수({len(embeddings)})와 메타데이터 행 수({len(meta)})가 다릅니다"
        )


def _validate_finite(embeddings: np.ndarray, positions: np.ndarray) -> None:
    """사용할 행의 특징 벡터에 NaN/inf가 없는지 확인한다."""
    for p in positions:
        if not np.all(np.isfinite(embeddings[p])):
            raise ValueError(f"위치 {p}의 특징 벡터에 유한하지 않은 값이 있습니다")


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
    """질의 행의 가까운 이웃 k개를 찾아 표로 반환한다.

    metric은 euclidean 또는 cosine(1 - 코사인 유사도)이다. 거리 오름차순, 동률은 sample_id 순이다.
    후보가 없으면 같은 열 구성의 빈 표를 반환한다.
    """
    embeddings = np.asarray(embeddings, dtype=np.float64)
    _check_embeddings(embeddings, meta)

    mask = candidate_mask(meta, query_pos, **constraints)
    cand_idx = np.where(mask)[0]

    if len(cand_idx) == 0:
        return pd.DataFrame(columns=_COLUMNS)

    # 질의와 모든 후보의 특징 벡터가 유한한지 확인한다.
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
        raise ValueError(f"알 수 없는 거리 척도: {metric!r}")

    # 거리 오름차순, 동률은 sample_id 순으로 정렬한다.
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
    """positions(기본 전체 행)의 각 행을 질의로 search를 실행해 결과를 이어 붙인다."""
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
