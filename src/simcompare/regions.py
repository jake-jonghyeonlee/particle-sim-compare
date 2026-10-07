"""양쪽·전 지름 원본 hotspot support에서 고정 물리 창을 선정한다.

CA 적분, 위험 점수, 이미지 IO/resize, 자동 대응은 하지 않는다. support 정책은
collect_support, 연결/병합은 build_regions/_merge_bounds, ID는 _region_id를 수정한다.
원점은 [y,x] 배열 첫 셀 좌상단이며 x 오른쪽/y 아래쪽, 창은 half-open um이다.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Mapping, Sequence

import numpy as np

from .config import RegionalConfig
from .regional_measurement import diameter_key, frame_geometry_um, geometry_um, validate_source
from .regional_schema import PhysicalWindow, SpatialFrame
from .schema import AnalysisBundle, convert_length

# ---------------------------------------------------------------------------
# 위치 근거 계약: 밝기 > 0 전체를 쓰며 CA 크기를 밝기에서 추론하지 않는다
# ---------------------------------------------------------------------------
REGION_ALGORITHM_VERSION = "support-window-v1"
NEIGHBORS_4 = ((-1, 0), (0, -1), (0, 1), (1, 0))
NEIGHBORS_8 = NEIGHBORS_4 + ((-1, -1), (-1, 1), (1, -1), (1, 1))


@dataclass(frozen=True)
class SupportPlane:
    """복사한 bool support [y,x]와 pitch/origin(um). 정량 density와 다른 위치 근거다."""
    sample_id: str
    diameter_um: float
    mask: np.ndarray
    pitch_um: float
    origin_xy_um: tuple[float, float]
    evidence: str


@dataclass(frozen=True)
class SupportStatus:
    """지름별 위치 관측 상태: observed/observed_empty/missing/unavailable."""
    sample_id: str
    diameter_um: float
    status: str
    reason: str | None


@dataclass(frozen=True)
class Region:
    """한 고정 정량 창과 별도 표시 여백. ID는 표시 여백이나 query 방향에 무관하다."""
    region_id: str
    frame_id: str
    member_sample_ids: tuple[str, ...]
    bounds_um: PhysicalWindow
    display_bounds_um: PhysicalWindow
    reasons: tuple[str, ...] = ("hotspot_support",)


@dataclass(frozen=True)
class RegionSet:
    """전 지름에 공통인 non-overlapping 창과 누락을 포함한 위치 상태 목록."""
    regions: tuple[Region, ...]
    support_statuses: tuple[SupportStatus, ...]
    reason: str | None
    common_frame: bool
    algorithm_version: str = REGION_ALGORITHM_VERSION


def observed_diameters(bundles: Sequence[AnalysisBundle]) -> tuple[float, ...]:
    """CA/hotspot/source의 지름 union(um)을 정렬한다. 미관측 CA 행도 삭제하지 않는다.

    지름만 읽으며 배열/원점/CA 값은 변경하지 않고 보간이나 IO를 하지 않는다.
    """
    sizes = set()
    for bundle in bundles:
        if bundle.ca is not None:
            sizes.update(diameter_key(v, bundle.ca.particle_size_unit) for v in bundle.ca.particle_sizes)
        sizes.update(diameter_key(s.diameter_um) for s in bundle.ca_spatial_sources)
        for image in bundle.hotspots:
            if image.particle_size is not None and image.particle_size_unit:
                sizes.add(diameter_key(image.particle_size, image.particle_size_unit))
    return tuple(sorted(sizes))


def _image_geometry(image, length_unit: str | None) -> tuple[float, float, float]:
    if length_unit is None:
        raise ValueError("missing_grid")
    return geometry_um(SpatialFrame(image.pixel_size, image.origin_xy, length_unit))


def collect_support(
    bundle: AnalysisBundle, diameters_um: Sequence[float] | None = None, *, cfg: RegionalConfig | None = None,
) -> tuple[list[SupportPlane], list[SupportStatus]]:
    """원 해상도 2D hotspot의 finite 양수 support와 지름별 결측 상태를 복사한다.

    hotspot은 이미 입자 전체 궤적이므로 반경으로 다시 팽창하지 않는다. 없을 때만
    검증된 CA 생성 map을 위치 근거로 쓴다. pitch/origin은 um 좌상단으로 변환하며
    원점 결측은 0으로 채우지 않는다. cfg는 fallback 보존 허용오차이며 기본 정책을
    생략할 수 있다. input 변경/IO/ROI CA 계산은 없다.
    """
    sizes = (
        observed_diameters([bundle]) if diameters_um is None
        else sorted({diameter_key(v) for v in diameters_um})
    )
    policy = cfg or RegionalConfig()
    planes, statuses = [], []
    for size in sizes:
        images = [image for image in bundle.hotspots if image.particle_size is not None
                  and image.particle_size_unit
                  and diameter_key(image.particle_size, image.particle_size_unit) == size]
        successful, failure, has_positive = [], None, False
        for image in images:
            if image.array is None:
                failure = "missing_map"
                continue
            array = image.array
            if array.ndim != 2 or not np.isfinite(array).all() or np.any(array < 0):
                failure = "invalid_hotspot_values"
                continue
            try:
                pitch, ox, oy = _image_geometry(image, bundle.physical_length_unit)
            except ValueError as exc:
                failure = str(exc)
                continue
            mask = array > 0
            has_positive |= bool(mask.any())
            successful.append(SupportPlane(bundle.sample_id, size, mask, pitch, (ox, oy), "hotspot_support"))
        if not images:
            source = next((s for s in bundle.ca_spatial_sources if diameter_key(s.diameter_um) == size), None)
            gate = validate_source(source, conservation_rtol=policy.conservation_rtol,
                                   conservation_atol_um2=policy.conservation_atol_um2) if source else None
            if gate is not None and gate.status == "available":
                pitch, ox, oy = frame_geometry_um(source)
                mask = (source.density > 0) & source.valid_mask
                has_positive = bool(mask.any())
                successful.append(SupportPlane(bundle.sample_id, size, mask, pitch, (ox, oy),
                    "verified_ca_map_support"))
        planes.extend(successful)
        if failure is not None:
            status = "missing" if failure == "missing_map" else "unavailable"
        elif successful:
            status = "observed" if has_positive else "observed_empty"
        else:
            status, failure = "missing", "location_unavailable"
        statuses.append(SupportStatus(bundle.sample_id, size, status, failure))
    return planes, statuses


# ---------------------------------------------------------------------------
# 명시 translation 공통 lattice 및 연결성분 (sparse cell set, resize 없음)
# ---------------------------------------------------------------------------


def _lattice_cells(planes: Sequence[SupportPlane], translations: Mapping[str, tuple[float, float]],
                   atol: float) -> tuple[set[tuple[int, int]], float, float, float, str | None]:
    pitch = planes[0].pitch_um
    origins = [(p.origin_xy_um[0] + translations[p.sample_id][0],
                p.origin_xy_um[1] + translations[p.sample_id][1]) for p in planes]
    ox, oy = min(x for x, _ in origins), min(y for _, y in origins)
    cells = set()
    for plane, (x, y) in zip(planes, origins):
        dx, dy = round((x - ox) / pitch), round((y - oy) / pitch)
        # pitch 자체는 동일해야 한다. 작은 차이도 먼 셀에서 누적되므로 원점 오차와
        # 섞지 않는다.
        compatible = (plane.pitch_um == pitch and
                      abs(x - ox - dx * pitch) <= atol and abs(y - oy - dy * pitch) <= atol)
        if not compatible:
            return set(), pitch, ox, oy, "incompatible_comparison_grid"
        yy, xx = np.nonzero(plane.mask)
        cells.update((int(row) + dy, int(col) + dx) for row, col in zip(yy, xx))
    return cells, pitch, ox, oy, None


def _components(cells: set[tuple[int, int]], connectivity: int) -> list[tuple[int, int, int, int]]:
    remaining = set(cells)
    neighbors = NEIGHBORS_4 if connectivity == 4 else NEIGHBORS_8
    bounds = []
    while remaining:
        first = min(remaining)
        remaining.remove(first)
        pending, component = [first], [first]
        while pending:
            y, x = pending.pop()
            for dy, dx in neighbors:
                neighbor = (y + dy, x + dx)
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    pending.append(neighbor)
                    component.append(neighbor)
        yy, xx = zip(*component)
        bounds.append((min(xx), min(yy), max(xx) + 1, max(yy) + 1))
    return sorted(bounds)


def _merge_bounds(windows: list[PhysicalWindow], gap: float) -> list[PhysicalWindow]:
    ordered = sorted(windows, key=lambda w: (w.x0, w.y0, w.x1, w.y1))
    changed = True
    while changed:
        changed = False
        for i, a in enumerate(ordered):
            for j in range(i + 1, len(ordered)):
                b = ordered[j]
                overlaps = max(a.x0, b.x0) < min(a.x1, b.x1) and max(a.y0, b.y0) < min(a.y1, b.y1)
                dx = max(0., a.x0 - b.x1, b.x0 - a.x1)
                dy = max(0., a.y0 - b.y1, b.y0 - a.y1)
                near = gap > 0 and math.hypot(dx, dy) <= gap
                if overlaps or near:
                    merged = PhysicalWindow(
                        min(a.x0, b.x0), min(a.y0, b.y0), max(a.x1, b.x1), max(a.y1, b.y1)
                    )
                    ordered = [w for k, w in enumerate(ordered) if k not in (i, j)] + [merged]
                    ordered.sort(key=lambda w: (w.x0, w.y0, w.x1, w.y1))
                    changed = True
                    break
            if changed:
                break
    return ordered


def _region_id(members: tuple[str, ...], frame_id: str, bounds: PhysicalWindow, context: dict) -> str:
    coords = [0.0 if value == 0 else value for value in (bounds.x0, bounds.y0, bounds.x1, bounds.y1)]
    payload = {"algorithm": REGION_ALGORITHM_VERSION, "members": members,
               "frame_id": frame_id, "bounds_um": coords, "context": context}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _location_reasons(window: PhysicalWindow, planes: Sequence[SupportPlane],
                      translations: Mapping[str, tuple[float, float]]) -> tuple[str, ...]:
    reasons = set()
    for plane in planes:
        dx, dy = translations[plane.sample_id]
        ox, oy = plane.origin_xy_um
        yy, xx = np.nonzero(plane.mask)
        x, y = ox + dx + xx * plane.pitch_um, oy + dy + yy * plane.pitch_um
        inside = (x < window.x1) & (x + plane.pitch_um > window.x0)
        inside &= (y < window.y1) & (y + plane.pitch_um > window.y0)
        if inside.any():
            reasons.add(plane.evidence)
    return tuple(sorted(reasons))


def build_regions(
    bundles: Sequence[AnalysisBundle], cfg: RegionalConfig, *,
    translations_xy_um: Mapping[str, tuple[float, float]] | None = None,
    frame_id: str | None = None, context: dict | None = None,
    _supports: Mapping[str, tuple[list[SupportPlane], list[SupportStatus]]] | None = None,
) -> RegionSet:
    """양쪽 모든 지름의 원본 support union에서 고정 non-overlapping 창(um)을 만든다.

    공통 등록은 명시 translations/frame_id가 있을 때만 가능하다. 서로 다른 pitch나
    subpixel lattice이면 reason을 반환하며 호출자가 양쪽 own 창을 유지해야 한다.
    창은 CA와 무관하게 셀 support 전체를 포함한다. 표시 margin은 ID/정량 범위를
    바꾸지 않고, input 변경/IO/반경 dilation/위험 상위-k 삭제는 하지 않는다.
    """
    members = tuple(sorted(b.sample_id for b in bundles))
    sizes = observed_diameters(bundles)
    planes, statuses = [], []
    for bundle in sorted(bundles, key=lambda b: b.sample_id):
        support, state = _supports[bundle.sample_id] if _supports else collect_support(bundle, sizes, cfg=cfg)
        planes.extend(support)
        statuses.extend(state)
    common = translations_xy_um is not None and frame_id is not None
    if len(bundles) > 1 and not common:
        return RegionSet((), tuple(statuses), "unregistered", False)
    translations = translations_xy_um or {sid: (0., 0.) for sid in members}
    if set(translations) != set(members):
        raise ValueError("translation must explicitly cover every member")
    if not planes:
        return RegionSet((), tuple(statuses), "location_unavailable", common)
    cells, pitch, ox, oy, reason = _lattice_cells(planes, translations, cfg.coordinate_atol_um)
    if reason:
        return RegionSet((), tuple(statuses), reason, common)
    # hotspot은 입자 크기를 이미 포함한다. 지름에 따른 pad/dilation을 추가하면 이중
    # 반영이다.
    bounds = [PhysicalWindow(ox + x0 * pitch, oy + y0 * pitch, ox + x1 * pitch, oy + y1 * pitch)
              for x0, y0, x1, y1 in _components(cells, cfg.connectivity)]
    final = _merge_bounds(bounds, cfg.merge_gap_um)
    frame = frame_id if common else f"local:{members[0]}"
    margin = cfg.display_context_margin_um
    regions = tuple(Region(_region_id(members, frame, w, context or {}), frame, members, w,
                           PhysicalWindow(w.x0 - margin, w.y0 - margin, w.x1 + margin, w.y1 + margin),
                           _location_reasons(w, planes, translations)) for w in final)
    return RegionSet(regions, tuple(statuses), None, common)
