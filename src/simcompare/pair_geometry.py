"""전극 쌍 단위의 조정 가능한 치수를 layout 마스크에서 계산한다.

채움 비율 같은 요약 특징으로는 무엇을 얼마나 바꿀지 말할 수 없으므로, 회피안 검토에 쓰는
치수를 픽셀 중심 거리로 직접 잰다. 값은 픽셀 단위 근사(대략 ±1 픽셀)이며 실제 설계 치수를
대신하지 않는다.

- pair:min_gap: 두 전극 경계 사이 최소 간격. 평면 투영이 겹치면 0.
- pair:near_gap_len: 전극 a 경계 중 최소 간격 + near_margin 안에 드는 길이(좁은 간격이
  이어지는 평행 구간의 근사).
- pair:overlap_area: 평면 투영에서 두 전극이 겹치는 면적.
- pair:a_min_width, pair:b_min_width: 전극별 가장 좁은 선폭(거리 변환 능선의 최솟값).

길이는 설정의 canonical 길이 단위(권장 um), 면적은 그 제곱이다.
실제 크기(pixel_size와 physical_length_unit)가 없거나 전극이 비어 있으면 값을 추정하지 않고 NaN으로 둔다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.neighbors import KDTree

from simcompare.schema import AnalysisBundle, convert_length

PAIR_FEATURES = (
    "pair:min_gap",
    "pair:near_gap_len",
    "pair:overlap_area",
    "pair:a_min_width",
    "pair:b_min_width",
)

# 불량 종류별로 회피안 검토에 쓰는 치수. 해당하지 않는 치수는 비교 표에서 제외한다.
RELEVANT_FEATURES = {
    "lateral_short": ("pair:min_gap", "pair:near_gap_len", "pair:a_min_width",
                      "pair:b_min_width"),
    "vertical_short": ("pair:overlap_area", "pair:min_gap", "pair:a_min_width",
                       "pair:b_min_width"),
    "lateral_open": ("pair:a_min_width",),
}

_NEIGHBORS_4 = ((-1, 0), (1, 0), (0, -1), (0, 1))
_NEIGHBORS_8 = _NEIGHBORS_4 + ((-1, -1), (-1, 1), (1, -1), (1, 1))


def _shift(array: np.ndarray, dy: int, dx: int, fill) -> np.ndarray:
    """배열을 (dy, dx)만큼 옮기고 빈 자리를 fill로 채운다(가장자리 밖은 알 수 없는 값)."""
    out = np.full_like(array, fill)
    h, w = array.shape
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    out[yd, xd] = array[ys, xs]
    return out


def boundary(mask: np.ndarray) -> np.ndarray:
    """이미지 안의 배경과 4방향으로 맞닿은 전극 픽셀. 이미지 가장자리는 경계로 보지 않는다."""
    mask = np.asarray(mask, dtype=bool)
    touches = np.zeros_like(mask)
    for dy, dx in _NEIGHBORS_4:
        # 이미지 밖은 전극으로 간주해 가장자리를 경계로 만들지 않는다.
        touches |= ~_shift(mask, dy, dx, True)
    return mask & touches


def _coords(mask: np.ndarray) -> np.ndarray:
    return np.argwhere(mask).astype(np.float64)


_RUN_DIRECTIONS = ((0, 1, 1.0), (1, 0, 1.0), (1, 1, np.sqrt(2.0)), (1, -1, np.sqrt(2.0)))


def _run_length(mask: np.ndarray, y: int, x: int, dy: int, dx: int, cap: int) -> int | None:
    """(y, x)를 지나 ±(dy, dx) 방향으로 이어진 전극 픽셀 수.

    이미지 가장자리에서 끊긴 연속은 실제 폭을 알 수 없으므로 None이다.
    cap을 넘으면 더 세지 않고 cap+1을 돌려준다(최솟값 탐색용).
    """
    h, w = mask.shape
    count = 1
    for sign in (1, -1):
        cy, cx = y + sign * dy, x + sign * dx
        while mask[cy, cx] if (0 <= cy < h and 0 <= cx < w) else False:
            count += 1
            if count > cap:
                return count
            cy, cx = cy + sign * dy, cx + sign * dx
        if not (0 <= cy < h and 0 <= cx < w):
            return None
    return count


def min_width_px(mask: np.ndarray) -> float:
    """거리 변환 능선에서 잰 가장 좁은 선폭(픽셀). 배경이 없거나 전극이 비면 NaN.

    전극 픽셀마다 가장 가까운 인접 배경 픽셀 중심까지의 거리로 능선(8방향 이웃보다 작지
    않은 픽셀, 선의 중심선)을 찾고, 능선 픽셀을 지나는 가로·세로·두 대각선 방향의 연속 전극
    길이 중 최솟값을 그 위치의 선폭으로 본다. 가로·세로·45도 선에서는 픽셀 단위로 정확하고
    다른 각도에서는 조금 크게 잰다. 선 끝으로 갈수록 거리가 줄어드는 픽셀은 안쪽 이웃보다
    작아 능선에서 빠지므로 선 끝이 좁은 폭으로 잡히지 않는다.
    """
    mask = np.asarray(mask, dtype=bool)
    if not mask.any():
        return float("nan")
    outer = boundary(~mask)  # 전극과 맞닿은 배경 픽셀
    if not outer.any():
        return float("nan")
    dist, _ = KDTree(_coords(outer)).query(_coords(mask), k=1)
    field = np.zeros(mask.shape, dtype=np.float64)
    field[mask] = dist[:, 0]
    ridge = mask.copy()
    for dy, dx in _NEIGHBORS_8:
        ridge &= field >= _shift(field, dy, dx, 0.0)
    best = float("inf")
    for y, x in np.argwhere(ridge):
        for dy, dx, step in _RUN_DIRECTIONS:
            cap = int(best / step) + 1 if np.isfinite(best) else max(mask.shape) + 1
            run = _run_length(mask, int(y), int(x), dy, dx, cap=cap)
            if run is not None:
                best = min(best, run * step)
    return float(best) if np.isfinite(best) else float("nan")


def pair_metrics_px(a: np.ndarray, b: np.ndarray | None, near_margin_px: float) -> dict[str, float]:
    """픽셀 단위 치수. 길이는 픽셀, 면적은 픽셀 수."""
    a = np.asarray(a, dtype=bool)
    out = {name: float("nan") for name in PAIR_FEATURES}
    out["pair:a_min_width"] = min_width_px(a)
    if b is None:
        return out
    b = np.asarray(b, dtype=bool)
    out["pair:b_min_width"] = min_width_px(b)
    if not a.any() or not b.any():
        return out
    overlap = a & b
    out["pair:overlap_area"] = float(overlap.sum())
    if overlap.any():
        out["pair:min_gap"] = 0.0
        return out
    edge_a, edge_b = _coords(boundary(a)), _coords(boundary(b))
    if len(edge_a) == 0 or len(edge_b) == 0:
        return out
    dist, _ = KDTree(edge_b).query(edge_a, k=1)
    dist = dist[:, 0]
    nearest = float(dist.min())
    # 픽셀 중심 거리에서 한 픽셀을 빼면 사이에 비어 있는 폭이 된다.
    out["pair:min_gap"] = max(nearest - 1.0, 0.0)
    out["pair:near_gap_len"] = float(np.count_nonzero(dist <= nearest + near_margin_px))
    return out


def bundle_pixel_size(bundle: AnalysisBundle, length_unit: str = "um") -> float | None:
    """layout 픽셀 하나의 실제 길이(목표 단위). pixel_size나 길이 단위가 없거나 전극마다 다르면 None."""
    if not bundle.layouts or not bundle.physical_length_unit:
        return None
    sizes = {image.pixel_size for image in bundle.layouts}
    if len(sizes) != 1 or None in sizes:
        return None
    return float(convert_length(sizes.pop(), bundle.physical_length_unit, length_unit))


def bundle_pair_metrics(bundle: AnalysisBundle, *, length_unit: str = "um",
                        near_margin: float = 1.0) -> dict[str, float]:
    """묶음 하나의 전극 쌍 치수를 목표 길이 단위로 계산한다."""
    empty = {name: float("nan") for name in PAIR_FEATURES}
    pixel = bundle_pixel_size(bundle, length_unit)
    arrays = [image.array for image in bundle.layouts]
    if pixel is None or not arrays or any(a is None for a in arrays):
        return empty
    a = np.asarray(arrays[0]) > 0.5
    b = None if bundle.is_open or len(arrays) < 2 else np.asarray(arrays[1]) > 0.5
    if b is not None and a.shape != b.shape:
        return empty
    px = pair_metrics_px(a, b, near_margin_px=near_margin / pixel)
    return {
        name: (value * pixel * pixel if name == "pair:overlap_area" else value * pixel)
        for name, value in px.items()
    }


def pair_metrics_table(bundles: list[AnalysisBundle], *, length_unit: str = "um",
                       near_margin: float = 1.0) -> pd.DataFrame:
    """sample_id 열과 PAIR_FEATURES 열을 가진 표."""
    rows = [{"sample_id": b.sample_id,
             **bundle_pair_metrics(b, length_unit=length_unit, near_margin=near_margin)}
            for b in bundles]
    return pd.DataFrame(rows, columns=["sample_id", *PAIR_FEATURES])
