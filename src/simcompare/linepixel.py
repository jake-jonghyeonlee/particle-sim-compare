"""layer별 line/pixel 불량값과 전극 쌍별 몫.

현재 수율 계산 방식을 그대로 따른다(docs/implementation_memory.json,
current_yield_practice_2026_10_07 · line_pixel_rate_extension_2026_10_07).

- short만 쓴다. open은 넣지 않는다.
- 전극 쌍 → line/pixel 표로 hotspot을 두 종류로 나눈다. 표는 전극 두 개만 본다.
- 같은 layer·같은 이물 지름끼리 합집합을 낸다. line과 pixel이 겹친 곳은 line이다.
- 지름별 면적에 그 layer·지름의 defect density를 곱해 더한다.

합친 면적만 남기지 않고 픽셀마다 어느 전극 쌍이 덮는지를 함께 세어,
쌍별 단독 몫과 공동 몫을 낸다. 단독 몫 + 공동 몫 = layer 값.
경계 처리와 수율 환산은 보류 상태라 여기서 다루지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

import numpy as np

LINE = "line"
PIXEL = "pixel"
Pair = frozenset  # 전극 이름 두 개


def pair_of(a: str, b: str) -> Pair:
    if a == b:
        raise ValueError(f"전극 쌍은 서로 다른 전극이어야 한다: {a!r}")
    return frozenset((a, b))


@dataclass(frozen=True)
class ShortHotspot:
    """short 한 건의 hotspot. mask는 이물 중심의 궤적(bool 2-D).

    layer는 그 불량이 속한 layer다. vertical short는 짝의 위 layer.
    """

    layer: str
    electrode_a: str
    electrode_b: str
    diameter_um: float
    mask: np.ndarray

    @property
    def pair(self) -> Pair:
        return pair_of(self.electrode_a, self.electrode_b)


@dataclass
class ClassShare:
    """line 또는 pixel 한 종류의 값과 쌍별 몫."""

    value: float = 0.0
    exclusive: dict[Pair, float] = field(default_factory=dict)
    shared: dict[frozenset, float] = field(default_factory=dict)  # 키: 함께 덮는 쌍들의 집합


@dataclass
class LayerRate:
    layer: str
    line: ClassShare = field(default_factory=ClassShare)
    pixel: ClassShare = field(default_factory=ClassShare)
    moved_to_line: float = 0.0  # pixel 쌍이 덮지만 line과 겹쳐 line으로 센 몫
    area_um2: dict[float, dict[str, float]] = field(default_factory=dict)  # 지름 → {line, pixel, moved_to_line}


@dataclass
class RateResult:
    layers: dict[str, LayerRate]
    unmapped_pairs: tuple[Pair, ...]  # 표에 없어 계산에서 뺀 쌍 (0으로 보지 않는다)

    def total(self, kind: str) -> float:
        return float(sum(getattr(r, kind).value for r in self.layers.values()))


def normalize_pair_table(rows: Iterable[tuple[str, str, str]]) -> dict[Pair, str]:
    """(전극명, 전극명, 불량유형) 행을 쌍 → 유형으로 바꾼다. 같은 쌍의 유형이 다르면 오류."""
    table: dict[Pair, str] = {}
    for a, b, kind in rows:
        kind = kind.strip().lower()
        if kind not in (LINE, PIXEL):
            raise ValueError(f"불량유형은 line 또는 pixel이어야 한다: {kind!r}")
        pair = pair_of(a.strip(), b.strip())
        if table.setdefault(pair, kind) != kind:
            raise ValueError(f"같은 전극 쌍에 유형이 둘이다: {sorted(pair)}")
    return table


def unit_count(panel_size_um: tuple[float, float], unit_size_um: tuple[float, float], rounding: str = "floor") -> int:
    """패널 크기와 unit pixel 크기로 unit 수를 낸다. 나누어떨어지지 않으면 버림 또는 반올림."""
    if rounding not in ("floor", "round"):
        raise ValueError(f"rounding은 floor 또는 round: {rounding!r}")
    counts = []
    for panel, unit in zip(panel_size_um, unit_size_um):
        if unit <= 0 or panel <= 0:
            raise ValueError("패널과 unit 크기는 0보다 커야 한다")
        ratio = panel / unit
        counts.append(int(np.floor(ratio + 1e-9)) if rounding == "floor" else int(np.floor(ratio + 0.5)))
    return counts[0] * counts[1]


def _shares(masks: dict[Pair, np.ndarray], keep: np.ndarray, weight: float, into: ClassShare) -> float:
    """keep 안에서 쌍별 단독·공동 면적을 세어 가중해 더한다. 합친 면적(가중 전 픽셀 수)을 돌려준다."""
    pairs = sorted(masks, key=sorted)
    if len(pairs) > 62:
        raise ValueError("한 layer·지름에서 같은 종류의 전극 쌍은 62개까지")
    code = np.zeros(keep.shape, dtype=np.uint64)
    for i, pair in enumerate(pairs):
        code |= (masks[pair] & keep).astype(np.uint64) << np.uint64(i)
    values, counts = np.unique(code[code > 0], return_counts=True)
    for v, n in zip(values.tolist(), counts.tolist()):
        members = frozenset(pairs[i] for i in range(len(pairs)) if v >> i & 1)
        if len(members) == 1:
            (only,) = members
            into.exclusive[only] = into.exclusive.get(only, 0.0) + n * weight
        else:
            into.shared[members] = into.shared.get(members, 0.0) + n * weight
    covered = int(counts.sum()) if len(counts) else 0
    into.value += covered * weight
    return float(covered)


def compute_rates(
    hotspots: Iterable[ShortHotspot],
    pair_table: Mapping[Pair, str],
    density: Mapping[tuple[str, float], float],
    pixel_area_um2: float,
    units_in_mask: int = 1,
) -> RateResult:
    """layer별 line/pixel 값(unit 하나 기준)을 낸다.

    density는 (layer, 지름) → 면적당 이물 수. 값의 단위는 호출하는 쪽의 fab 자료를 따른다.
    units_in_mask는 mask가 담은 unit 수(2x2면 4)로, 면적을 그 수로 나눈다.
    """
    if pixel_area_um2 <= 0 or units_in_mask < 1:
        raise ValueError("pixel_area_um2는 0보다 크고 units_in_mask는 1 이상")
    grouped: dict[tuple[str, float], dict[str, dict[Pair, np.ndarray]]] = {}
    unmapped: set[Pair] = set()
    shape = None
    for h in hotspots:
        mask = np.asarray(h.mask).astype(bool)
        if mask.ndim != 2:
            raise ValueError("hotspot mask는 2-D")
        if shape is None:
            shape = mask.shape
        elif mask.shape != shape:
            raise ValueError(f"hotspot 격자가 다르다: {mask.shape} != {shape}")
        kind = pair_table.get(h.pair)
        if kind is None:
            unmapped.add(h.pair)
            continue
        by_pair = grouped.setdefault((h.layer, float(h.diameter_um)), {LINE: {}, PIXEL: {}})[kind]
        # 같은 쌍의 lateral·vertical은 한 쌍으로 합친다 (표가 전극만 보므로)
        by_pair[h.pair] = by_pair[h.pair] | mask if h.pair in by_pair else mask

    layers: dict[str, LayerRate] = {}
    for (layer, diameter), kinds in sorted(grouped.items()):
        if (layer, diameter) not in density:
            raise ValueError(f"defect density가 없다: layer={layer!r}, 지름={diameter}")
        weight = density[(layer, diameter)] * pixel_area_um2 / units_in_mask
        rate = layers.setdefault(layer, LayerRate(layer))
        everywhere = np.ones(shape, dtype=bool)
        line_union = np.zeros(shape, dtype=bool)
        for m in kinds[LINE].values():
            line_union |= m
        pixel_union = np.zeros(shape, dtype=bool)
        for m in kinds[PIXEL].values():
            pixel_union |= m
        n_line = _shares(kinds[LINE], everywhere, weight, rate.line)
        n_pixel = _shares(kinds[PIXEL], ~line_union, weight, rate.pixel)
        n_moved = float((pixel_union & line_union).sum())
        rate.moved_to_line += n_moved * weight
        scale = pixel_area_um2 / units_in_mask
        rate.area_um2[diameter] = {LINE: n_line * scale, PIXEL: n_pixel * scale, "moved_to_line": n_moved * scale}
    return RateResult(layers, tuple(sorted(unmapped, key=sorted)))


@dataclass
class LayerDelta:
    layer: str
    line: float
    pixel: float
    moved_to_line: float
    line_by_pair: dict[Pair, float]
    pixel_by_pair: dict[Pair, float]
    line_shared: dict[frozenset, float]
    pixel_shared: dict[frozenset, float]


def _diff(a: Mapping, b: Mapping) -> dict:
    return {k: b.get(k, 0.0) - a.get(k, 0.0) for k in set(a) | set(b)}


def compare_rates(reference: RateResult, target: RateResult, scale_reference: float = 1.0, scale_target: float = 1.0) -> dict[str, LayerDelta]:
    """layer별 증감(대상 − 참고). scale은 unit 수처럼 제품마다 곱할 값."""
    out: dict[str, LayerDelta] = {}
    empty = LayerRate("")
    for layer in sorted(set(reference.layers) | set(target.layers)):
        ra = reference.layers.get(layer, empty)
        rb = target.layers.get(layer, empty)

        def scaled(m: Mapping, s: float) -> dict:
            return {k: v * s for k, v in m.items()}

        out[layer] = LayerDelta(
            layer,
            rb.line.value * scale_target - ra.line.value * scale_reference,
            rb.pixel.value * scale_target - ra.pixel.value * scale_reference,
            rb.moved_to_line * scale_target - ra.moved_to_line * scale_reference,
            _diff(scaled(ra.line.exclusive, scale_reference), scaled(rb.line.exclusive, scale_target)),
            _diff(scaled(ra.pixel.exclusive, scale_reference), scaled(rb.pixel.exclusive, scale_target)),
            _diff(scaled(ra.line.shared, scale_reference), scaled(rb.line.shared, scale_target)),
            _diff(scaled(ra.pixel.shared, scale_reference), scaled(rb.pixel.shared, scale_target)),
        )
    return out
