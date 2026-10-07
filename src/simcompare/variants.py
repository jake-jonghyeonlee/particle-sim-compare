"""회피안 편집 후보 생성과 변경본(재시뮬레이션 결과)의 원안 대비 재비교.

편집 후보는 particle-sim ``make-variant``가 읽는 편집 지정서(schema_version 1.0) 형식이다.
전역 CD bias나 overlay가 아니라 hotspot 부근 ROI 안의 국소 편집만 제안한다.

- lateral short: 참고 사례보다 간격이 좁으면 한쪽 전극을 ROI 안에서 후퇴(trim_near)시키는
  후보를 전극별·양(차이의 TRIM_FRACTIONS 배)별로 만든다.
- vertical short: 참고 사례보다 겹침이 크면 ROI 안의 겹침을 한쪽 전극에서
  제거(remove_overlap)한다.
- lateral open: 선폭을 넓히는 보강(추가) 편집은 아직 지원하지 않으므로 사유와 함께
  건너뛴다.

ROI는 질의 사례의 hotspot(비교에 쓴 가장 큰 이물 크기)에서 최댓값의 절반 이상인
연결 영역의 경계 상자에 그 이물 크기만큼 여유를 더한 것이다. hotspot이 없거나
편집 전극이 ROI 밖이면 두 전극이 가장 가까운(또는 겹치는) 구간의 경계 상자를 쓴다.

재비교는 parent_design_id가 있는 변경본마다 같은 제품의 원안에서 불량 종류·전극 역할·
layer·적용 공정 layer가 같은 사례를 찾아, 두 사례가 모두 관측한 크기의 면적 보정 CA
최댓값을 비교한다.
판정은 하지 않는다(다른 불량 증가 허용 여부는 설계자 판단).
"""
from __future__ import annotations

import re
from collections import deque

import numpy as np
import pandas as pd
from sklearn.neighbors import KDTree

from simcompare.neighborhood import AREA_PREFIX
from simcompare.pair_geometry import boundary, bundle_pixel_size
from simcompare.schema import AnalysisBundle, convert_length

TRIM_FRACTIONS = (0.5, 1.0)
SPEC_NOTE = (
    "simcompare가 유사 제품 비교군의 저위험 참고 사례와의 치수 차이로 만든 "
    "국소 편집 후보입니다. "
    "관찰된 차이에 근거한 검토 후보이며 효과는 particle-sim make-variant → simulate-nominal → "
    "export-simcompare 후 variant_comparison.csv로 확인합니다. 채택은 설계자가 판단합니다."
)
CANDIDATE_COLUMNS = [
    "candidate_id", "query_id", "reference_id", "failure_type", "op", "slot", "target_slot",
    "electrode_role", "layer", "amount_um", "roi_um", "roi_source", "basis_feature", "basis_delta",
    "status", "reason",
]
VARIANT_COLUMNS = [
    "variant_id", "parent_id", "product_id", "comparison_group_id", "failure_type",
    "assigned_process_layer", "change_description", "compared_sizes", "parent_ca_area",
    "variant_ca_area", "delta", "ratio", "direction", "status",
]


def _flood(mask: np.ndarray, start: tuple[int, int]) -> np.ndarray:
    """start를 포함한 4방향 연결 영역."""
    out = np.zeros_like(mask)
    queue = deque([start])
    out[start] = True
    h, w = mask.shape
    while queue:
        y, x = queue.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not out[ny, nx]:
                out[ny, nx] = True
                queue.append((ny, nx))
    return out


def _bounds_um(bounds: tuple[int, int, int, int], shape: tuple[int, int],
               pixel_um: float, pad_um: float) -> list[float]:
    """행/열 inclusive bbox를 기존 반올림·여백·격자 clipping으로 um 창으로 바꾼다."""
    # 기존 mask bbox의 NumPy float64 반올림을 Python int bbox에서도 동일하게 유지한다.
    row_min, col_min, row_max, col_max = map(np.float64, bounds)
    h, w = shape
    return [
        round(max(0.0, col_min * pixel_um - pad_um), 6),
        round(max(0.0, row_min * pixel_um - pad_um), 6),
        round(min(w * pixel_um, (col_max + 1) * pixel_um + pad_um), 6),
        round(min(h * pixel_um, (row_max + 1) * pixel_um + pad_um), 6),
    ]


def _bbox_um(region: np.ndarray, pixel_um: float, pad_um: float) -> list[float] | None:
    rows, cols = np.nonzero(region)
    if rows.size == 0:
        return None
    bounds = (rows.min(), cols.min(), rows.max(), cols.max())
    return _bounds_um(bounds, region.shape, pixel_um, pad_um)


def _component_bounds(mask: np.ndarray) -> list[tuple[int, tuple[int, int], tuple[int, int, int, int]]]:
    """2D support를 4방향으로 한 번 탐색하여 개수·첫 raster 픽셀·inclusive bbox만 반환한다."""
    visited = np.zeros_like(mask, dtype=bool)
    components = []
    h, w = mask.shape
    # 성분별 전체 마스크를 쌓으면 checkerboard에서 메모리가 픽셀 수의 제곱으로 커진다.
    for row in range(h):
        for col in range(w):
            if not mask[row, col] or visited[row, col]:
                continue
            first = (row, col)
            queue = deque([first])
            visited[first] = True
            count = 0
            row_min = row_max = row
            col_min = col_max = col
            while queue:
                y, x = queue.popleft()
                count += 1
                row_min, row_max = min(row_min, y), max(row_max, y)
                col_min, col_max = min(col_min, x), max(col_max, x)
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        queue.append((ny, nx))
            components.append((count, first, (row_min, col_min, row_max, col_max)))
    components.sort(key=lambda component: (-component[0], component[1]))
    return components


def _edited_prefix(mask: np.ndarray, pixel_um: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """편집 bool 격자의 누적합과 기존 판정과 동일한 um 픽셀 중심 축을 만든다.

    입력은 불변이다.
    """
    h, w = mask.shape
    prefix = np.zeros((h + 1, w + 1), dtype=np.int64)
    prefix[1:, 1:] = mask
    np.cumsum(prefix, axis=0, out=prefix)
    np.cumsum(prefix, axis=1, out=prefix)
    return prefix, (np.arange(w) + 0.5) * pixel_um, (np.arange(h) + 0.5) * pixel_um


def _contains_prefix(roi: list[float], prefix: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> bool:
    """um 닫힌 bbox 안의 편집 픽셀 유무를 기존 _contains와 동일하게 누적합으로 검사한다."""
    x0, y0, x1, y1 = roi
    # 나눗셈/floor 대신 동일한 중심 좌표를 비교해야 경계의 부동소수 판정이 달라지지
    # 않는다.
    left, right = np.searchsorted(xs, x0, side="left"), np.searchsorted(xs, x1, side="right")
    top, bottom = np.searchsorted(ys, y0, side="left"), np.searchsorted(ys, y1, side="right")
    if left >= right or top >= bottom:
        return False
    count = prefix[bottom, right] - prefix[top, right] - prefix[bottom, left] + prefix[top, left]
    return bool(count > 0)


def _contains(roi: list[float], mask: np.ndarray, pixel_um: float) -> bool:
    x0, y0, x1, y1 = roi
    h, w = mask.shape
    xs = (np.arange(w) + 0.5) * pixel_um
    ys = (np.arange(h) + 0.5) * pixel_um
    inside = ((ys >= y0) & (ys <= y1))[:, None] & ((xs >= x0) & (xs <= x1))[None, :]
    return bool((mask & inside).any())


def _closest_zone(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """두 전극이 겹치는 구간, 없으면 가장 가까운 경계 픽셀들(두 전극 모두)."""
    overlap = a & b
    if overlap.any():
        return overlap
    edge_a, edge_b = np.argwhere(boundary(a)), np.argwhere(boundary(b))
    if len(edge_a) == 0 or len(edge_b) == 0:
        return np.zeros_like(a)
    da, _ = KDTree(edge_b.astype(float)).query(edge_a.astype(float), k=1)
    db, _ = KDTree(edge_a.astype(float)).query(edge_b.astype(float), k=1)
    nearest = min(da.min(), db.min())
    zone = np.zeros_like(a)
    zone[tuple(edge_a[da[:, 0] <= nearest + 1.0].T)] = True
    zone[tuple(edge_b[db[:, 0] <= nearest + 1.0].T)] = True
    return zone


def _roi(bundle: AnalysisBundle, sizes: list[float], pixel_um: float,
         a: np.ndarray, b: np.ndarray, edited: np.ndarray) -> tuple[list[float] | None, str]:
    """hotspot 기반 ROI, 안 되면 최근접 구간 ROI(um)."""
    by_size = {h.particle_size: h for h in bundle.hotspots if h.array is not None}
    for size in sorted((s for s in sizes if s in by_size), reverse=True):
        heat = np.asarray(by_size[size].array, dtype=float)
        if heat.shape != a.shape or not np.isfinite(heat).any() or np.nanmax(heat) <= 0:
            continue
        peak = np.unravel_index(int(np.nanargmax(heat)), heat.shape)
        support = heat >= 0.5 * np.nanmax(heat)
        if np.all((heat == 0) | (heat == np.nanmax(heat))):
            components = _component_bounds(support)
            if len(components) > 1:
                # binary 동률은 큰 성분·첫 raster 순으로 검사하되 전체 mask 재탐색을
                # 반복하지 않는다.
                prefix, xs, ys = _edited_prefix(edited, pixel_um)
                for _, _, bounds in components:
                    roi = _bounds_um(bounds, support.shape, pixel_um, float(size))
                    if _contains_prefix(roi, prefix, xs, ys):
                        return roi, f"hotspot@{size:g}"
                continue
        # 단일 성분과 graded 입력은 기존 first-max 마스크·판정 경로를 그대로 유지한다.
        region = _flood(support, peak)
        roi = _bbox_um(region, pixel_um, float(size))
        if roi is not None and _contains(roi, edited, pixel_um):
            return roi, f"hotspot@{size:g}"
    roi = _bbox_um(_closest_zone(a, b), pixel_um, max(sizes) if sizes else 0.0)
    return roi, "closest_zone"


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", text)


def edit_candidates(comparison: pd.DataFrame, deltas: pd.DataFrame,
                    bundles: list[AnalysisBundle], *, length_unit: str = "um"
                    ) -> tuple[dict, pd.DataFrame]:
    """particle-sim 편집 지정서 dict와 후보·건너뜀 요약 표를 만든다."""
    by_id = {b.sample_id: b for b in bundles}
    counters: dict[str, int] = {}
    candidates, rows = [], []

    def skip(item, reason):
        rows.append({"query_id": item["query_id"], "reference_id": item["reference_id"],
                     "failure_type": item["failure_type"], "status": "skipped", "reason": reason})

    for _, item in comparison[comparison["status"] == "ok"].iterrows():
        query = by_id.get(item["query_id"])
        own = deltas[deltas["query_id"] == item["query_id"]].set_index("feature")
        failure = item["failure_type"]
        if failure == "lateral_open":
            skip(item, "선폭 보강(추가) 편집은 아직 지원하지 않습니다")
            continue
        if failure == "lateral_short":
            if "pair:min_gap" not in own.index or own.loc["pair:min_gap", "delta"] <= 0:
                skip(item, "참고 사례보다 간격이 좁지 않습니다")
                continue
            op, feature = "trim_near", "pair:min_gap"
        else:
            if "pair:overlap_area" not in own.index or own.loc["pair:overlap_area", "delta"] >= 0:
                skip(item, "참고 사례보다 겹침이 크지 않습니다")
                continue
            op, feature = "remove_overlap", "pair:overlap_area"
        pixel = None if query is None else bundle_pixel_size(query, "um")
        if pixel is None or len(query.layouts) < 2 or any(x.array is None for x in query.layouts):
            skip(item, "layout 배열이나 실제 픽셀 크기가 없어 ROI를 정할 수 없습니다")
            continue
        a, b = (np.asarray(x.array) > 0.5 for x in query.layouts[:2])
        delta = float(own.loc[feature, "delta"])
        delta_um = float(convert_length(delta, length_unit, "um")) if op == "trim_near" else None
        sizes = [float(s) for s in str(item["compared_sizes"]).split(";") if s]
        sizes_um = [float(convert_length(s, length_unit, "um")) for s in sizes]
        amounts = (sorted({round(delta_um * f, 3) for f in TRIM_FRACTIONS if delta_um * f > 0})
                   if op == "trim_near" else [0.0])
        for slot, target_slot in (("b", "a"), ("a", "b")):
            edited = b if slot == "b" else a
            roi, source = _roi(query, sizes_um, pixel, a, b, edited)
            if roi is None:
                skip(item, f"slot {slot}: ROI를 정할 수 없습니다")
                continue
            role = query.electrode_b_role if slot == "b" else query.electrode_a_role
            layer = query.layer_b if slot == "b" else query.layer_a
            for amount in amounts:
                counters[query.product_id] = counters.get(query.product_id, 0) + 1
                candidate_id = _safe(f"{query.sample_id}-e{counters[query.product_id]}")
                verb = (f"국소 후퇴 {amount:g} um" if op == "trim_near" else "겹침 국소 제거")
                basis = {"query_id": query.sample_id, "reference_id": item["reference_id"],
                         "feature": feature, "query_value": float(own.loc[feature, "query_value"]),
                         "reference_value": float(own.loc[feature, "reference_value"]),
                         "delta": delta, "length_unit": length_unit}
                candidates.append({
                    "candidate_id": candidate_id,
                    "sample_id": query.sample_id,
                    "change_description": f"{role}({layer}) 전극 {verb}, 참고 {item['reference_id']}",
                    "edits": [{"op": op, "slot": slot, "target_slot": target_slot,
                               "roi_um": roi, "amount_um": amount}],
                    "basis": basis,
                    "info": {"failure_type": failure, "roi_source": source,
                             "comparison_group_id": item["comparison_group_id"],
                             "status": "proposed", "decision": "designer_judgement"},
                })
                rows.append({"candidate_id": candidate_id, "query_id": query.sample_id,
                             "reference_id": item["reference_id"], "failure_type": failure,
                             "op": op, "slot": slot, "target_slot": target_slot,
                             "electrode_role": role, "layer": layer, "amount_um": amount,
                             "roi_um": ";".join(f"{v:g}" for v in roi), "roi_source": source,
                             "basis_feature": feature, "basis_delta": delta,
                             "status": "proposed", "reason": None})
    spec = {"schema_version": "1.0", "length_unit": "um", "note": SPEC_NOTE, "candidates": candidates}
    return spec, pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)


def variant_comparison(ranked: pd.DataFrame, bundles: list[AnalysisBundle]) -> pd.DataFrame:
    """parent_design_id가 있는 변경본을 원안의 같은 불량·전극·layer 사례와 비교한다."""
    columns = [c for c in ranked.columns if c.startswith(AREA_PREFIX)]
    table = ranked.set_index("sample_id")
    design_id = {b.sample_id: f"{b.product_id}-{b.design_version}" for b in bundles}
    rows = []
    for bundle in bundles:
        if not bundle.parent_design_id or bundle.sample_id not in table.index:
            continue
        row = table.loc[bundle.sample_id]
        key = (row["comparison_group_id"], row["assigned_process_layer"])
        parents = [s for s, d in design_id.items()
                   if d == bundle.parent_design_id and s in table.index
                   and (table.loc[s, "comparison_group_id"], table.loc[s, "assigned_process_layer"]) == key]
        out = {"variant_id": bundle.sample_id, "product_id": bundle.product_id,
               "comparison_group_id": row["comparison_group_id"], "failure_type": row["failure_type"],
               "assigned_process_layer": row["assigned_process_layer"],
               "change_description": bundle.change_description}
        if len(parents) != 1:
            rows.append({**out, "parent_id": ";".join(parents) or None,
                         "status": "no_parent_match" if not parents else "ambiguous_parent"})
            continue
        parent = parents[0]
        pair = table.loc[[parent, bundle.sample_id], columns].apply(pd.to_numeric, errors="coerce")
        common = [c for c in columns if pair[c].notna().all()]
        if not common:
            rows.append({**out, "parent_id": parent, "status": "no_common_sizes"})
            continue
        before, after = float(pair.loc[parent, common].max()), float(pair.loc[bundle.sample_id, common].max())
        delta = after - before
        rows.append({
            **out, "parent_id": parent,
            "compared_sizes": ";".join(c[len(AREA_PREFIX):] for c in common),
            "parent_ca_area": before, "variant_ca_area": after, "delta": delta,
            "ratio": after / before if before > 0 else np.nan,
            "direction": "decrease" if delta < 0 else ("increase" if delta > 0 else "same"),
            "status": "ok",
        })
    return pd.DataFrame(rows, columns=VARIANT_COLUMNS)
