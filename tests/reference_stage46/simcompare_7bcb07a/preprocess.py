"""전처리 도구: 종횡비 보존 letterbox, 범례 기반 색상 변환, 외삽 없는 곡선 정렬, 학습 행 전용 정규화.

NumPy와 Pillow만 사용한다(torch 미사용).
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
from PIL import Image


# --------------------------------------------------------------------------- #
# 종횡비 보존 리사이즈
# --------------------------------------------------------------------------- #
def letterbox(
    image: np.ndarray,
    out_hw: tuple[int, int],
    *,
    binary: bool,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """종횡비를 유지한 채 이미지를 out_hw 캔버스에 맞추고 가운데 배치한다.

    (canvas, valid_mask, info)를 반환한다. valid_mask는 이미지가 놓인 영역이 1, 여백이 0이다.
    binary이면 최근접 보간 후 {0, 1}로 이진화하고, 아니면 쌍선형 보간한다.
    info에는 scale, offset_y, offset_x, 원본 크기가 들어 있다.
    """
    if not isinstance(image, np.ndarray) or image.ndim != 2 or image.size == 0:
        raise ValueError("이미지는 비어 있지 않은 2차원 배열이어야 합니다")

    out_h, out_w = out_hw
    if out_h <= 0 or out_w <= 0:
        raise ValueError("out_hw는 양수 크기여야 합니다")

    h, w = image.shape
    scale = min(out_h / h, out_w / w)
    new_h = max(1, round(h * scale))
    new_w = max(1, round(w * scale))

    src = Image.fromarray(image.astype(np.float32, copy=False), mode="F")
    resample = Image.NEAREST if binary else Image.BILINEAR
    resized = src.resize((new_w, new_h), resample)
    resized_arr = np.asarray(resized, dtype=np.float32)

    canvas = np.zeros((out_h, out_w), dtype=np.float32)
    mask = np.zeros((out_h, out_w), dtype=np.float32)

    offset_y = (out_h - new_h) // 2
    offset_x = (out_w - new_w) // 2
    canvas[offset_y : offset_y + new_h, offset_x : offset_x + new_w] = resized_arr
    mask[offset_y : offset_y + new_h, offset_x : offset_x + new_w] = 1.0

    if binary:
        canvas = (canvas > 0.5).astype(np.float32)

    info = {
        "scale": float(scale),
        "offset_y": int(offset_y),
        "offset_x": int(offset_x),
        "src_hw": [int(h), int(w)],
    }
    return canvas, mask, info


# --------------------------------------------------------------------------- #
# 리사이즈 후 픽셀 크기
# --------------------------------------------------------------------------- #
def resized_pixel_size(pixel_size: float | None, info: dict) -> float | None:
    """리사이즈 후 픽셀 하나의 실제 길이(원래 pixel_size / scale). pixel_size가 없으면 None."""
    if pixel_size is None:
        return None
    scale = info["scale"]
    if scale == 0:
        raise ValueError("scale은 0이 아니어야 합니다")
    return float(pixel_size) / float(scale)


# --------------------------------------------------------------------------- #
# 범례 기반 색상 변환
# --------------------------------------------------------------------------- #
def legend_to_values(rgb: np.ndarray, legend: dict | None) -> np.ndarray:
    """명시적 범례로 RGB 이미지를 float32 값 배열로 변환한다.

    각 픽셀은 채널 최대 차이가 가장 작은 범례 색에 대응시키며, 그 차이가 tolerance를 넘으면
    대응 실패다. 대응되지 않은 픽셀이 하나라도 있으면 ValueError를 낸다.
    범례가 없으면 색을 확률로 바꾸지 않는다.
    """
    if not isinstance(rgb, np.ndarray) or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("rgb는 HxWx3 배열이어야 합니다")
    if legend is None or not isinstance(legend, dict):
        raise ValueError("범례(legend)가 필요합니다: 범례 없이 색상을 확률로 바꾸지 않습니다")
    entries = legend.get("entries")
    if not entries:
        raise ValueError("범례 항목이 비어 있습니다")

    tolerance = float(legend.get("tolerance", 0))
    table = np.array([e["rgb"] for e in entries], dtype=np.float64)
    values = np.array([e["value"] for e in entries], dtype=np.float64)

    px = rgb.astype(np.float64)
    diff = np.abs(px[..., None, :] - table[None, None, :, :])
    maxdiff = diff.max(axis=-1)  # (H, W, E)
    best = maxdiff.argmin(axis=-1)  # (H, W)
    best_diff = np.take_along_axis(maxdiff, best[..., None], axis=-1)[..., 0]

    out = np.full(px.shape[:2], np.nan, dtype=np.float32)
    valid = best_diff <= tolerance
    out[valid] = values[best[valid]].astype(np.float32)

    if not np.all(valid):
        count = int((~valid).sum())
        raise ValueError(f"{count}개 픽셀을 범례에 대응시킬 수 없습니다")

    return out


# --------------------------------------------------------------------------- #
# 크기 격자 정렬
# --------------------------------------------------------------------------- #
def align_curve(
    sizes,
    values,
    observed,
    grid,
    method: str = "exact",
) -> tuple[np.ndarray, np.ndarray]:
    """크기별 곡선을 목표 크기 격자에 정렬한다. 관측 범위 밖으로 외삽하지 않는다.

    - exact: 격자 크기와 정확히 일치(rtol=1e-6)하는 관측값만 사용
    - loglinear_within_range: 일치값이 없으면 양쪽에 관측점이 있을 때만 log(크기) 선형 보간
    (값 배열, 마스크)를 반환하며 채우지 못한 격자는 NaN과 False다. 0으로 채우지 않는다.
    """
    sizes = np.asarray(sizes, dtype=np.float64)
    values = np.asarray(values, dtype=np.float64)
    observed = np.asarray(observed, dtype=bool)
    grid = np.asarray(grid, dtype=np.float64)

    if sizes.shape != values.shape or sizes.ndim != 1:
        raise ValueError("크기와 값 배열은 길이가 같은 1차원이어야 합니다")
    if observed.shape != sizes.shape:
        raise ValueError("관측 여부 배열의 길이는 크기 배열과 같아야 합니다")
    if grid.ndim != 1:
        raise ValueError("격자는 1차원이어야 합니다")

    if method == "exact":
        out = np.full(grid.shape, np.nan, dtype=np.float64)
        mask = np.zeros(grid.shape, dtype=bool)
        obs_idx = np.where(observed)[0]
        for j, g in enumerate(grid):
            for i in obs_idx:
                if np.isclose(sizes[i], g, rtol=1e-6, atol=0.0):
                    out[j] = values[i]
                    mask[j] = True
                    break
        return out, mask

    if method == "loglinear_within_range":
        out = np.full(grid.shape, np.nan, dtype=np.float64)
        mask = np.zeros(grid.shape, dtype=bool)
        obs_idx = np.where(observed)[0]
        if obs_idx.size == 0:
            return out, mask
        obs_sizes = sizes[obs_idx]
        obs_values = values[obs_idx]
        order = np.argsort(obs_sizes)
        obs_sizes = obs_sizes[order]
        obs_values = obs_values[order]
        for j, g in enumerate(grid):
            exact = np.where(np.isclose(obs_sizes, g, rtol=1e-6, atol=0.0))[0]
            if exact.size:
                out[j] = obs_values[exact[0]]
                mask[j] = True
                continue
            if obs_idx.size < 2:
                continue  # 보간하려면 관측점이 2개 이상 필요하다
            if g <= obs_sizes[0] or g >= obs_sizes[-1]:
                continue  # 관측 범위 밖으로는 절대 외삽하지 않는다
            k = np.searchsorted(obs_sizes, g)
            s0, s1 = obs_sizes[k - 1], obs_sizes[k]
            if s0 <= g <= s1:
                t = (math.log(g) - math.log(s0)) / (math.log(s1) - math.log(s0))
                out[j] = obs_values[k - 1] + t * (obs_values[k] - obs_values[k - 1])
                mask[j] = True
        return out, mask

    raise ValueError(f"알 수 없는 정렬 방식: {method!r}")


# --------------------------------------------------------------------------- #
# 안전한 log10
# --------------------------------------------------------------------------- #
def safe_log10(values: np.ndarray, mask: np.ndarray, floor: float = 1e-12) -> np.ndarray:
    """마스크가 참인 위치는 log10(max(v, floor)), 나머지는 NaN을 반환한다."""
    values = np.asarray(values, dtype=np.float64)
    mask = np.asarray(mask, dtype=bool)
    out = np.full(values.shape, np.nan, dtype=np.float64)
    out[mask] = np.log10(np.maximum(values[mask], floor))
    return out


# --------------------------------------------------------------------------- #
# 학습 행 전용 정규화
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class Normalizer:
    """지정한 행(학습 행)만으로 적합하는 표준화기.

    통계는 rows 행 중 mask가 참인 값으로 열마다 계산한다. 관측값이 없는 열은 평균 0·표준편차 1,
    표준편차가 1e-8보다 작으면 1로 둔다.
    """

    mean: np.ndarray  # (D,)
    std: np.ndarray  # (D,)

    @classmethod
    def fit(cls, x, mask, rows) -> "Normalizer":
        """rows 행에서 열마다 관측된 값만 사용해 평균과 표준편차를 계산한다."""
        x = np.asarray(x, dtype=np.float64)
        mask = np.asarray(mask, dtype=bool)
        rows = np.asarray(rows, dtype=np.intp)

        if x.ndim != 2:
            raise ValueError("x는 2차원이어야 합니다")
        if mask.shape != x.shape:
            raise ValueError("mask의 모양이 x와 같아야 합니다")
        if rows.ndim != 1:
            raise ValueError("rows는 1차원이어야 합니다")

        n, d = x.shape
        if rows.size == 0:
            return cls(np.zeros(d, dtype=np.float64), np.ones(d, dtype=np.float64))

        sel = x[rows]
        sel_mask = mask[rows]

        mean = np.zeros(d, dtype=np.float64)
        std = np.ones(d, dtype=np.float64)
        for j in range(d):
            col = sel[:, j]
            m = sel_mask[:, j]
            if not np.any(m):
                continue
            vals = col[m]
            mu = float(np.mean(vals))
            sd = float(np.std(vals))
            mean[j] = mu
            std[j] = sd if sd >= 1e-8 else 1.0

        return cls(mean, std)

    def transform(self, x, mask) -> np.ndarray:
        """(x - 평균) / 표준편차를 계산한다. 마스크 밖은 0 자리표시값이며 NaN을 전파하지 않는다."""
        x = np.asarray(x, dtype=np.float64)
        mask = np.asarray(mask, dtype=bool)
        if x.ndim != 2:
            raise ValueError("x는 2차원이어야 합니다")
        if mask.shape != x.shape:
            raise ValueError("mask의 모양이 x와 같아야 합니다")

        out = np.where(mask, (np.where(mask, x, 0.0) - self.mean) / self.std, 0.0)
        return out.astype(np.float64, copy=False)

    def to_dict(self) -> dict:
        """체크포인트 저장용으로 평균·표준편차를 float 리스트 dict로 만든다."""
        return {
            "mean": [float(v) for v in np.asarray(self.mean).ravel()],
            "std": [float(v) for v in np.asarray(self.std).ravel()],
        }

    @classmethod
    def from_dict(cls, d) -> "Normalizer":
        """to_dict 결과에서 Normalizer를 복원한다."""
        return cls(
            np.asarray(d["mean"], dtype=np.float64),
            np.asarray(d["std"], dtype=np.float64),
        )
