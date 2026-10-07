"""원본 정량 CA 격자의 보존 검사와 물리 창 적분을 수행한다.

IO, 그림, 대응/등록 추론, 배열 변경은 하지 않는다. 보존 정책은 validate_source,
셀 clipping/중첩 처리는 measure_union을 수정한다. 셀 내부 density는 상수다.
"""
from __future__ import annotations

import copy
from numbers import Real
from typing import Sequence

import numpy as np

from .regional_schema import (
    DEFAULT_CONSERVATION_ATOL_UM2, DEFAULT_CONSERVATION_RTOL,
    PhysicalWindow, RegionMeasurement, SourceValidation, SpatialCASource, SpatialFrame,
)
from .schema import DataContractError, convert_area, convert_length, is_dimensionless_unit

# ---------------------------------------------------------------------------
# 격자·단위와 전체-domain 보존 gate
# ---------------------------------------------------------------------------


def frame_geometry_um(source: SpatialCASource) -> tuple[float, float, float]:
    """원본 [y,x] 격자의 (pitch,x원점,y원점)을 um으로 반환한다.

    origin은 좌상단 셀 모서리다. 결측/anisotropy/잘못된 단위는 ValueError이며
    등록은 추론하지 않는다. 입력 변경이나 IO는 없다.
    """
    return geometry_um(source.frame)


def geometry_um(frame: SpatialFrame) -> tuple[float, float, float]:
    """등방 pitch와 좌상단 원점을 um로 변환한다. 결측은 ValueError, IO/변경은 없다."""
    if frame.pixel_size is None or frame.origin_xy is None:
        raise ValueError("missing_grid")
    if not isinstance(frame.pixel_size, Real) or isinstance(frame.pixel_size, bool):
        raise ValueError("unsupported_grid")
    try:
        pitch = float(convert_length(frame.pixel_size, frame.length_unit))
        origin = np.asarray(convert_length(frame.origin_xy, frame.length_unit))
    except (DataContractError, TypeError, ValueError):
        raise ValueError("invalid_grid") from None
    if pitch <= 0 or not np.isfinite(pitch) or origin.shape != (2,) or not np.isfinite(origin).all():
        raise ValueError("invalid_grid")
    return pitch, float(origin[0]), float(origin[1])


# 지름 키 1e-12 um 해상도는 단위 환산의 float 표현 차이만 제거한다. 보간은 아니다.
DIAMETER_KEY_DECIMALS = 12


def diameter_key(value: float, unit: str = "um") -> float:
    """지름 scalar를 um 12자리 키로 정규화한다. 배열/원점/CA는 변경하지 않는다.

    700 nm와 0.7 um의 표현 오차를 제거하며 서로 다른 키 간 보간은 없다.
    유효성 검사는 원 입력 계약에 맡기고 이 helper는 IO 없이 키만 반환한다.
    """
    return round(float(convert_length(value, unit)), DIAMETER_KEY_DECIMALS)


def validate_source(
    source: SpatialCASource, *, conservation_rtol: float = DEFAULT_CONSERVATION_RTOL,
    conservation_atol_um2: float = DEFAULT_CONSERVATION_ATOL_UM2,
) -> SourceValidation:
    """2D 원본 density와 bool valid domain의 합을 동일-domain CA(um²)와 검사한다.

    nominal/mean 의미를 먼저 검사하므로 우연히 합이 같아도 승격하지 않는다.
    원점은 명시 좌상단, pitch는 isotropic이다. 결측은 reason+None을 반환한다.
    결과에 residual/허용오차/추적 정보를 복사하며 입력 변경과 IO는 없다.
    """
    if not isinstance(source, SpatialCASource):
        raise DataContractError("regional measurement accepts one source, never a sum of pair sources")
    if any(not isinstance(v, Real) or isinstance(v, bool) or not np.isfinite(v) or v < 0
           for v in (conservation_rtol, conservation_atol_um2)):
        raise ValueError("conservation tolerances must be finite and nonnegative")
    total = integrated = residual = None
    provenance = copy.deepcopy(source.provenance)
    provenance.update(sample_id=source.sample_id, diameter_um=source.diameter_um,
                      source_kind=source.source_kind, source_mode=source.source_mode,
                      semantics=source.semantics, ca_source=source.ca_source,
                      calculation_contract_id=source.calculation_contract_id,
                      frame_id=source.frame.frame_id, length_unit=source.frame.length_unit,
                      pixel_size=source.frame.pixel_size, origin_xy=source.frame.origin_xy,
                      shape_yx=None if source.density is None else list(np.shape(source.density)),
                      valid_domain="explicit_bool_mask")

    def result(reason: str | None) -> SourceValidation:
        return SourceValidation("available" if reason is None else "unavailable", reason,
                                total, integrated, residual, conservation_rtol,
                                conservation_atol_um2, provenance)

    if is_dimensionless_unit(source.total_ca_unit):
        return result("legacy_ca_not_area")
    if source.total_ca is not None:
        if not isinstance(source.total_ca, Real) or isinstance(source.total_ca, bool):
            return result("invalid_total_ca")
        try:
            total = float(convert_area(source.total_ca, source.total_ca_unit))
        except (DataContractError, ValueError, TypeError):
            return result("invalid_total_ca")
        if not np.isfinite(total) or total < 0:
            total = None
            return result("invalid_total_ca")
    if source.unavailable_reason:
        return result(source.unavailable_reason)
    if total is None:
        return result("missing_total_ca")
    if source.source_kind not in ("particle_sim_nominal", "particle_sim_mean", "explicit_ca_density"):
        return result("unverified_source_semantics")
    if not source.calculation_contract_id:
        return result("unverified_source_semantics")
    expected = {"particle_sim_nominal": ("nominal", "binary", "ca_nominal_um2"),
                "particle_sim_mean": ("mean", "probability", "ca_mean_um2")}
    if source.source_kind in expected and (
        source.source_mode, source.semantics, source.ca_source
    ) != expected[source.source_kind]:
        return result("source_mode_mismatch")
    if source.source_kind == "explicit_ca_density" and (
        source.source_mode not in ("nominal", "mean") or source.semantics != "ca_density"
        or source.ca_source != f"ca_{source.source_mode}_um2"
    ):
        return result("source_mode_mismatch")
    try:
        pitch, _, _ = frame_geometry_um(source)
    except ValueError as exc:
        return result(str(exc))
    density, valid = source.density, source.valid_mask
    if density is None:
        return result("missing_map")
    if not isinstance(density, np.ndarray) or density.ndim != 2 or not density.size:
        return result("invalid_map_shape")
    if not isinstance(valid, np.ndarray) or valid.dtype != np.bool_ or valid.shape != density.shape:
        return result("invalid_valid_mask")
    if density.dtype.kind not in "bifu" or not np.isfinite(density).all() or np.any((density < 0) | (density > 1)):
        return result("invalid_source_values")
    if source.source_kind == "particle_sim_nominal" and np.any((density != 0) & (density != 1)):
        return result("invalid_nominal_values")
    if (not isinstance(source.diameter_um, Real) or isinstance(source.diameter_um, bool)
            or not np.isfinite(source.diameter_um) or source.diameter_um <= 0):
        return result("invalid_diameter")
    integrated = float(np.sum(density[valid], dtype=np.float64) * pitch**2)
    residual = integrated - total
    provenance["valid_cell_count"] = int(valid.sum())
    # 전체 보존은 원천 오류를 드러내는 gate다. CA에 맞추어 density를 rescale하지 않는다.
    if abs(residual) > conservation_atol_um2 + conservation_rtol * abs(total):
        return result("conservation_failed")
    return result(None)


# ---------------------------------------------------------------------------
# 물리 창 union: x slab의 y interval union으로 정확한 cell 교집합 면적 계산
# ---------------------------------------------------------------------------


def window_um(window: PhysicalWindow) -> PhysicalWindow:
    """half-open 창을 um으로 변환한다. 유한한 양의 폭/높이만 허용하며 IO는 없다."""
    coords = np.asarray(convert_length([window.x0, window.y0, window.x1, window.y1], window.length_unit))
    if not np.isfinite(coords).all() or coords[0] >= coords[2] or coords[1] >= coords[3]:
        raise ValueError("window must have finite increasing bounds")
    return PhysicalWindow(*map(float, coords))


def _union_cell_areas(
    source: SpatialCASource, windows: tuple[PhysicalWindow, ...],
) -> tuple[np.ndarray, PhysicalWindow | None]:
    pitch, ox, oy = frame_geometry_um(source)
    height, width = source.density.shape
    xe = ox + np.arange(width + 1) * pitch
    ye = oy + np.arange(height + 1) * pitch
    clipped = [PhysicalWindow(max(w.x0, xe[0]), max(w.y0, ye[0]), min(w.x1, xe[-1]), min(w.y1, ye[-1]))
               for w in windows if w.x1 > xe[0] and w.x0 < xe[-1] and w.y1 > ye[0] and w.y0 < ye[-1]]
    areas = np.zeros((height, width), dtype=np.float64)
    if not clipped:
        return areas, None
    bounds = PhysicalWindow(min(w.x0 for w in clipped), min(w.y0 for w in clipped),
                            max(w.x1 for w in clipped), max(w.y1 for w in clipped))
    cuts = sorted({x for w in clipped for x in (w.x0, w.x1)})
    for left, right in zip(cuts, cuts[1:]):
        intervals = sorted((w.y0, w.y1) for w in clipped if w.x0 < right and w.x1 > left)
        merged: list[list[float]] = []
        for low, high in intervals:
            if merged and low <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], high)
            else:
                merged.append([low, high])
        dx = np.maximum(0, np.minimum(xe[1:], right) - np.maximum(xe[:-1], left))
        dy = np.zeros(height)
        for low, high in merged:
            dy += np.maximum(0, np.minimum(ye[1:], high) - np.maximum(ye[:-1], low))
        # 겹친 ROI 값을 더하면 같은 CA를 두 번 센다. 셀별 union 면적만 적분한다.
        areas += dy[:, None] * dx[None, :]
    return areas, bounds


def measure_union(
    source: SpatialCASource, windows: Sequence[PhysicalWindow], *, include_area_normalized: bool = False,
    conservation_rtol: float = DEFAULT_CONSERVATION_RTOL,
    conservation_atol_um2: float = DEFAULT_CONSERVATION_ATOL_UM2,
) -> RegionMeasurement:
    """한 원본 [y,x] CA source의 물리 창 union을 fractional cell clipping으로 적분한다.

    창 단위는 um/nm/mm, 원점은 좌상단이다. 전체 gate를 매번 현재 배열에 검사한다.
    도메인 밖/valid hole만 있는 창은 미관측이며 0이 아니다. raw um², total에 대한
    share, 선택적 valid 면적당 무차원 density를 구분한다. 입력 변경/IO/팽창은 없다.
    """
    requested = tuple(window_um(w) for w in windows)
    gate = validate_source(source, conservation_rtol=conservation_rtol,
                           conservation_atol_um2=conservation_atol_um2)
    if gate.status != "available":
        return RegionMeasurement("unavailable", gate.reason, None, gate.source_total_um2,
                                 None, gate.reason, None, gate.reason, None, requested, None, gate)
    return _measure_union_validated(source, requested, gate, include_area_normalized)


def _measure_union_validated(
    source: SpatialCASource, requested: tuple[PhysicalWindow, ...], gate: SourceValidation,
    include_area_normalized: bool = False,
) -> RegionMeasurement:
    """동일 순수 분석 호출 안에서 방금 검증한 gate를 재사용한다.

    외부 cache는 신뢰하지 않는다.
    """
    if gate.status != "available":
        return RegionMeasurement("unavailable", gate.reason, None, gate.source_total_um2,
                                 None, gate.reason, None, gate.reason, None, requested, None, gate)
    areas, bounds = _union_cell_areas(source, requested)
    valid_areas = areas * source.valid_mask
    valid_area = float(valid_areas.sum())
    # 빈 영역과 관측된 CA=0은 다른 상태다. 창이 source 밖이면 위험 0으로 채우지 않는다.
    if valid_area == 0:
        return RegionMeasurement("unavailable", "empty_valid_area", None, gate.source_total_um2,
                                 None, "empty_valid_area", None, "empty_valid_area", 0.0, requested, bounds, gate)
    ca = float(np.sum(source.density * valid_areas, dtype=np.float64))
    total = gate.source_total_um2
    share = ca / total if total != 0 else None
    normalized = ca / valid_area if include_area_normalized else None
    return RegionMeasurement("available", None, ca, total, share, "zero_total" if total == 0 else None,
                             normalized, None if include_area_normalized else "not_requested",
                             valid_area, requested, bounds, gate)


def measure_window(
    source: SpatialCASource, window: PhysicalWindow, *, include_area_normalized: bool = False,
    conservation_rtol: float = DEFAULT_CONSERVATION_RTOL,
    conservation_atol_um2: float = DEFAULT_CONSERVATION_ATOL_UM2,
) -> RegionMeasurement:
    """하나의 half-open 물리 창을 measure_union과 같은 단위/결측 규칙으로 적분한다.

    source는 원본 2D density다. 옵션은 보존 허용오차/면적 정규화이며 IO는 없다.
    """
    return measure_union(source, (window,), include_area_normalized=include_area_normalized,
                         conservation_rtol=conservation_rtol, conservation_atol_um2=conservation_atol_um2)
