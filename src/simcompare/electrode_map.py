"""전극 수 지도: 이물 하나가 동시에 건드리는 서로 다른 전극의 수.

취약 위치를 찾는 줄기(제품 하나만으로 계산)다. 방법은
docs/implementation_memory.json의 electrode_count_map_2026_10_07을 따른다.

- 대표 지름의 hotspot에서, 픽셀마다 그 자리를 덮는 모든 불량에 나오는 전극을 모아 센다.
- layer마다 한 장. vertical short는 짝의 위 layer에 넣는다.
- 제품 전체 요약은 layer별 지도에서 픽셀마다 가장 큰 값.
- 전극 수가 높다고 불량 확률이 가장 높은 것은 아니다. CA와 함께 읽는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
from scipy import ndimage

DEFAULT_DIAMETER_UM = 5.0


@dataclass(frozen=True)
class DefectHotspot:
    """불량 한 건의 hotspot. open은 전극 하나, short는 전극 둘."""

    layer: str
    electrodes: tuple[str, ...]
    diameter_um: float
    mask: np.ndarray


@dataclass(frozen=True)
class Region:
    layer: str
    label: int
    max_count: int
    area_px: int
    bbox: tuple[int, int, int, int]  # row0, col0, row1, col1 (끝 제외)
    electrodes: tuple[str, ...]


def electrode_count_maps(hotspots: Iterable[DefectHotspot], diameter_um: float = DEFAULT_DIAMETER_UM) -> dict[str, np.ndarray]:
    """layer → 전극 수 지도(int). 대표 지름의 hotspot만 쓴다."""
    per_layer: dict[str, dict[str, np.ndarray]] = {}
    shape = None
    for h in hotspots:
        if float(h.diameter_um) != float(diameter_um):
            continue
        mask = np.asarray(h.mask).astype(bool)
        if shape is None:
            shape = mask.shape
        elif mask.shape != shape:
            raise ValueError(f"hotspot 격자가 다르다: {mask.shape} != {shape}")
        touched = per_layer.setdefault(h.layer, {})
        for name in h.electrodes:
            touched[name] = touched[name] | mask if name in touched else mask
    return {layer: np.sum(np.stack(list(touched.values())), axis=0).astype(np.int32) for layer, touched in per_layer.items()}


def summary_map(maps: dict[str, np.ndarray]) -> np.ndarray:
    """layer별 지도에서 픽셀마다 가장 큰 값."""
    if not maps:
        raise ValueError("대표 지름의 hotspot이 없다")
    return np.max(np.stack(list(maps.values())), axis=0)


def find_regions(hotspots: Iterable[DefectHotspot], min_count: int = 3, diameter_um: float = DEFAULT_DIAMETER_UM) -> list[Region]:
    """전극 수가 min_count 이상인 픽셀이 이어진 덩어리. 전극 수, 면적 순으로 정렬."""
    spots = [h for h in hotspots if float(h.diameter_um) == float(diameter_um)]
    regions: list[Region] = []
    for layer, counts in electrode_count_maps(spots, diameter_um).items():
        labels, n = ndimage.label(counts >= min_count)
        for i, box in enumerate(ndimage.find_objects(labels), start=1):
            inside = labels == i
            names = sorted({e for h in spots if h.layer == layer and (np.asarray(h.mask).astype(bool) & inside).any() for e in h.electrodes})
            regions.append(Region(layer, i, int(counts[inside].max()), int(inside.sum()), (box[0].start, box[1].start, box[0].stop, box[1].stop), tuple(names)))
    return sorted(regions, key=lambda r: (-r.max_count, -r.area_px, r.layer, r.label))
