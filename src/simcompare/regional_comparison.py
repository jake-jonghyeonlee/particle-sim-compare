"""확인된 구조의 지름별 global/own/local CA와 증감·사유를 계산한다.

IO, 렌더링, AE/geometry 점수, product/layer 합산은 하지 않는다. 자격 정책은
source_semantics_reason/_global_reason/_local_reason, 결과 조합은 compare_pair,
source별 union/창 밖 잔여는 summarize_source를 수정한다. delta는 query-reference다.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from typing import Sequence

import numpy as np

from .adapters.regional import scalar_observation, source_for_diameter, source_frame_reason
from .config import RegionalConfig
from .correspondence import CorrespondencePair, PairResolution, resolve_pair
from .regional_measurement import _measure_union_validated, diameter_key, frame_geometry_um, validate_source
from .regional_schema import PhysicalWindow, RegionMeasurement, SpatialCASource, SpatialFrame
from .regions import Region, RegionSet, SupportStatus, build_regions, collect_support, observed_diameters
from .schema import AnalysisBundle, DataContractError, convert_area, convert_length

# ---------------------------------------------------------------------------
# 결과 계약: global scalar와 공간 측정을 분리하고 부재/결측을 null로 보존한다
# ---------------------------------------------------------------------------
CALCULATION_SETTING_KEYS = ("assumption_set", "legacy_kernel", "post_step_policy")


@dataclass(frozen=True)
class SideObservation:
    """한쪽의 원 CA/단위와 선택적 지역 적분. global은 공간 원천이 없어도 보존한다."""
    sample_id: str | None
    global_ca_value: float | None
    global_ca_unit: str | None
    unit_total_ca_um2: float | None
    global_reason: str | None
    roi: RegionMeasurement | None
    roi_reason: str | None
    location_status: str
    source_kind: str | None
    source_mode: str | None
    provenance: dict


@dataclass(frozen=True)
class ComparisonRow:
    """동일 지름의 own/global/local 값. 비교 불가한 delta는 null+사유다."""
    comparison_id: str
    region_id: str | None
    diameter_um: float
    query: SideObservation
    reference: SideObservation
    total_reason: str | None
    local_reason: str | None
    delta_total_ca_um2: float | None
    delta_roi_ca_um2: float | None
    relative_delta_total: float | None
    relative_delta_roi: float | None
    reasons: tuple[str, ...]
    delta_direction: str = "query_minus_reference"


@dataclass(frozen=True)
class SourceSummary:
    """동일 source/map/지름의 union 값과 창 밖 잔여. 다른 pair와 더하지 않는다."""
    sample_id: str
    diameter_um: float
    unit_total_ca_um2: float | None
    selected_union_ca_um2: float | None
    outside_regions_ca_um2: float | None
    reason: str | None
    union_measurement: RegionMeasurement | None


@dataclass(frozen=True)
class RegionalComparison:
    """보고용 순수 결과. 원본 bundle/배열을 포함하지 않고 source별 요약을 유지한다."""
    comparison_id: str
    relationship: str
    query_sample_id: str | None
    reference_sample_id: str | None
    correspondence: CorrespondencePair | None
    region_sets: tuple[RegionSet, ...]
    rows: tuple[ComparisonRow, ...]
    source_summaries: tuple[SourceSummary, ...]
    identity_reason: str | None
    registration_reason: str | None


def _translated_source(source: SpatialCASource, translation: tuple[float, float], frame_id: str) -> SpatialCASource:
    pitch, x, y = frame_geometry_um(source)
    frame = SpatialFrame(pitch, (x + translation[0], y + translation[1]), "um", frame_id)
    provenance = copy.deepcopy(source.provenance)
    provenance["registration"] = {"translation_xy_um": translation, "original_frame_id": source.frame.frame_id,
                                  "original_origin_xy": source.frame.origin_xy}
    return replace(source, frame=frame, provenance=provenance, validation=None)


def _source(bundle: AnalysisBundle | None, diameter: float) -> SpatialCASource | None:
    return source_for_diameter(bundle, diameter)[0] if bundle else None


def source_semantics_reason(query: SpatialCASource, reference: SpatialCASource) -> str | None:
    """typed source의 계산 정의를 비교한다. 같은 지름/단위 source이며 IO·변경은 없다.

    kind/mode/semantics/계약/producer version과 의미를 바꾸는 설정이 같아야 한다.
    run_id/config_hash는 설계 변경 추적용이므로 동등성 조건에 넣지 않는다.
    설정 누락은 동일하다고 추정하지 않고 unconfirmed_source_definition을 반환한다.
    """
    fields = ("source_kind", "source_mode", "semantics", "ca_source", "calculation_contract_id")
    if any(getattr(query, key) != getattr(reference, key) for key in fields):
        return "incompatible_source_semantics"
    for key in ("producer", "producer_version"):
        a, b = query.provenance.get(key), reference.provenance.get(key)
        if a is None or b is None:
            return "unconfirmed_source_definition"
        if a != b:
            return "incompatible_source_semantics"
    a = query.provenance.get("settings", {})
    b = reference.provenance.get("settings", {})
    for key in CALCULATION_SETTING_KEYS:
        if a.get(key) is None or b.get(key) is None:
            return "unconfirmed_source_definition"
        if a[key] != b[key]:
            return "incompatible_source_semantics"
    return None


# ---------------------------------------------------------------------------
# global/domain/grid 자격: own 수치를 남긴 채 delta만 차단한다
# ---------------------------------------------------------------------------


def _scalar_definition(bundle: AnalysisBundle) -> tuple | None:
    settings = bundle.provenance.calc_settings | (bundle.ca.calc_settings if bundle.ca else {})
    version = bundle.provenance.calc_version
    if version is None or settings.get("ca_source") is None:
        return None
    if any(settings.get(key) is None for key in CALCULATION_SETTING_KEYS):
        return None
    return (version, settings["ca_source"], bundle.failure_type,
            *(settings[key] for key in CALCULATION_SETTING_KEYS))


def _domain_signature(bundle: AnalysisBundle, source: SpatialCASource | None) -> tuple | None:
    if source is not None and source.density is not None and source.valid_mask is not None:
        try:
            pitch, _, _ = frame_geometry_um(source)
        except ValueError:
            return None
        valid = source.valid_mask
        if valid.dtype != np.bool_ or valid.shape != source.density.shape:
            return None
        return (source.density.shape, pitch, valid.tobytes())
    if bundle.unit_width is None or bundle.unit_height is None or bundle.physical_length_unit is None:
        return None
    return (float(convert_length(bundle.unit_width, bundle.physical_length_unit)),
            float(convert_length(bundle.unit_height, bundle.physical_length_unit)))


def _global_reason(resolution: PairResolution, diameter: float) -> str | None:
    if resolution.identity_reason:
        return resolution.identity_reason
    query, reference = resolution.query, resolution.reference
    for bundle in (query, reference):
        reason = scalar_observation(bundle, diameter)[3]
        if reason:
            return reason
    a, b = _source(query, diameter), _source(reference, diameter)
    if (a is None) != (b is None):
        return "one_sided_source_domain"
    if a is not None and b is not None:
        reason = source_semantics_reason(a, b)
        if reason:
            return reason
    else:
        first, second = _scalar_definition(query), _scalar_definition(reference)
        if first is None or second is None:
            return "unconfirmed_source_definition"
        if first != second:
            return "incompatible_source_semantics"
    first, second = _domain_signature(query, a), _domain_signature(reference, b)
    if first is None or second is None:
        return "unconfirmed_domain"
    if first != second:
        return "incomparable_domain"
    return None


def _grid_reason(a: SpatialCASource, b: SpatialCASource, atol: float) -> str | None:
    try:
        pa, ax, ay = frame_geometry_um(a)
        pb, bx, by = frame_geometry_um(b)
    except ValueError as exc:
        return str(exc)
    if pa != pb:
        return "incompatible_comparison_grid"
    if any(abs(offset - round(offset / pa) * pa) > atol for offset in (ax - bx, ay - by)):
        return "incompatible_comparison_grid"
    return None


def _coverage(source: SpatialCASource, window: PhysicalWindow, anchor: tuple[float, float],
              pitch: float) -> set[tuple[int, int]]:
    _, ox, oy = frame_geometry_um(source)
    dx, dy = round((ox - anchor[0]) / pitch), round((oy - anchor[1]) / pitch)
    height, width = source.valid_mask.shape
    x0 = max(0, int(np.floor((window.x0 - ox) / pitch)))
    y0 = max(0, int(np.floor((window.y0 - oy) / pitch)))
    x1 = min(width, int(np.ceil((window.x1 - ox) / pitch)))
    y1 = min(height, int(np.ceil((window.y1 - oy) / pitch)))
    if x1 <= x0 or y1 <= y0:
        return set()
    # 같은 valid 셀 비교를 작은 창에서 수행한다. 전체 map을 매 지역마다 순회하지 않는다.
    yy, xx = np.nonzero(source.valid_mask[y0:y1, x0:x1])
    yy, xx = yy + y0, xx + x0
    active = ((ox + (xx + 1) * pitch > window.x0) & (ox + xx * pitch < window.x1) &
              (oy + (yy + 1) * pitch > window.y0) & (oy + yy * pitch < window.y1))
    return {(int(y) + dy, int(x) + dx) for y, x in zip(yy[active], xx[active])}


def _local_reason(resolution: PairResolution, a: SpatialCASource | None, b: SpatialCASource | None,
                  window: PhysicalWindow, cfg: RegionalConfig) -> str | None:
    if resolution.identity_reason:
        return resolution.identity_reason
    if resolution.registration_reason:
        return resolution.registration_reason
    if a is None or b is None:
        return "missing_source"
    reason = source_semantics_reason(a, b)
    if reason:
        return reason
    for source in (a, b):
        gate = source.validation or validate_source(source, conservation_rtol=cfg.conservation_rtol,
                               conservation_atol_um2=cfg.conservation_atol_um2)
        if gate.reason:
            return gate.reason
    reason = _grid_reason(a, b, cfg.coordinate_atol_um)
    if reason:
        return reason
    pitch, x, y = frame_geometry_um(a)
    # 같은 면적만으로 충분하지 않다. 실제 valid 셀 위치/holes를 공통 lattice에서 비교한다.
    if _coverage(a, window, (x, y), pitch) != _coverage(b, window, (x, y), pitch):
        return "incomparable_domain"
    return None


# ---------------------------------------------------------------------------
# own 측정·union 잔여·크기별 delta 및 정렬
# ---------------------------------------------------------------------------


def _observation(bundle: AnalysisBundle | None, diameter: float, window: PhysicalWindow | None,
                 source: SpatialCASource | None, cfg: RegionalConfig, absent_reason: str,
                 status: SupportStatus | None = None) -> SideObservation:
    raw, unit, total, reason = scalar_observation(bundle, diameter)
    if bundle is None:
        return SideObservation(None, None, None, None, absent_reason, None, absent_reason, "missing", None, None, {})
    if status is None:
        _, statuses = collect_support(bundle, [diameter], cfg=cfg)
        status = statuses[0]
    measurement = None
    roi_reason = source_for_diameter(bundle, diameter)[1]
    if source is not None and window is not None:
        gate = source.validation or validate_source(
            source, conservation_rtol=cfg.conservation_rtol, conservation_atol_um2=cfg.conservation_atol_um2)
        measurement = _measure_union_validated(source, (window,), gate, cfg.include_area_normalized)
        roi_reason = measurement.reason
    elif window is None:
        roi_reason = "not_selected" if source is not None else roi_reason
    provenance = copy.deepcopy(source.provenance if source else {
        "calc_version": bundle.provenance.calc_version, "settings": bundle.provenance.calc_settings,
        "source_files": bundle.provenance.source_files})
    return SideObservation(bundle.sample_id, raw, unit, total, reason, measurement, roi_reason, status.status,
                           source.source_kind if source else None, source.source_mode if source else None, provenance)


def summarize_source(source: SpatialCASource, windows: Sequence[PhysicalWindow], cfg: RegionalConfig,
                     *, location_reason: str | None = None, _cached: bool = False) -> SourceSummary:
    """동일 원본 [y,x] source의 창 union을 한 번 적분하고 밖의 CA(um²)를 반환한다.

    창은 source.frame의 좌상단 기준 half-open um이다. 보존 실패/유효 coverage 결측은
    null이며 input 변경/IO/다른 pair 합산은 없다. 완전히 빈 창 목록은 선택 CA=0이다.
    """
    if not isinstance(source, SpatialCASource):
        raise DataContractError("regional summary accepts one source, never a sum of pair sources")
    gate = source.validation if _cached else None
    gate = gate or validate_source(source, conservation_rtol=cfg.conservation_rtol,
                                  conservation_atol_um2=cfg.conservation_atol_um2)
    if location_reason:
        return SourceSummary(source.sample_id, source.diameter_um, gate.source_total_um2,
                             None, None, location_reason, None)
    if gate.reason:
        return SourceSummary(source.sample_id, source.diameter_um, gate.source_total_um2, None, None, gate.reason, None)
    if not windows:
        return SourceSummary(
            source.sample_id, source.diameter_um, gate.source_total_um2, 0., gate.source_total_um2, None, None,
        )
    # 여러 창의 비율이나 pair CA를 합치지 않는다. 같은 map의 union만 한 번 적분한다.
    union = _measure_union_validated(source, tuple(windows), gate, cfg.include_area_normalized)
    if union.roi_ca_um2 is None:
        return SourceSummary(
            source.sample_id, source.diameter_um, gate.source_total_um2, None, None, union.reason, union,
        )
    return SourceSummary(source.sample_id, source.diameter_um, gate.source_total_um2, union.roi_ca_um2,
                         gate.source_total_um2 - union.roi_ca_um2, None, union)


def _delta(query: float | None, reference: float | None, reason: str | None) -> tuple[float | None, float | None]:
    if reason is not None or query is None or reference is None:
        return None, None
    delta = query - reference
    # 기준 0의 상대율은 epsilon으로 만들어내지 않는다. 절대 증감은 그대로 보존한다.
    return delta, delta / reference if reference != 0 else None


def _row(cid: str, region: Region | None, diameter: float, resolution: PairResolution,
         query_source: SpatialCASource | None, reference_source: SpatialCASource | None,
         cfg: RegionalConfig, owner: str | None = None, forced_local_reason: str | None = None,
         statuses: dict[tuple[str, float], SupportStatus] | None = None) -> ComparisonRow:
    window = region.bounds_um if region else None
    query_window = window if owner in (None, "query") else None
    reference_window = window if owner in (None, "reference") else None
    statuses = statuses or {}
    query_status = statuses.get((resolution.query.sample_id, diameter)) if resolution.query else None
    reference_status = statuses.get((resolution.reference.sample_id, diameter)) if resolution.reference else None
    query = _observation(
        resolution.query, diameter, query_window, query_source, cfg, resolution.relationship, query_status,
    )
    reference = _observation(
        resolution.reference, diameter, reference_window, reference_source, cfg,
        resolution.relationship, reference_status,
    )
    total_reason = _global_reason(resolution, diameter)
    local_reason = "not_selected" if window is None else forced_local_reason
    if window is not None and local_reason is None:
        local_reason = _local_reason(resolution, query_source, reference_source, window, cfg)
        if local_reason is None:
            local_reason = query.roi_reason or reference.roi_reason
    dt, rt = _delta(query.unit_total_ca_um2, reference.unit_total_ca_um2, total_reason)
    qroi = query.roi.roi_ca_um2 if query.roi else None
    rroi = reference.roi.roi_ca_um2 if reference.roi else None
    dr, rr = _delta(qroi, rroi, local_reason)
    reasons = list(region.reasons) if region else []
    tag = f"@{diameter:g}um"
    if dt is not None and dt > cfg.delta_atol_um2:
        reasons.append("total_ca_increased" + tag)
    if dr is not None:
        if dr > cfg.delta_atol_um2:
            reasons.append("local_ca_increased" + tag)
        if rroi == 0 and qroi > cfg.delta_atol_um2:
            reasons.append("new_risk" + tag)
        if qroi == 0 and rroi > cfg.delta_atol_um2:
            reasons.append("risk_disappeared" + tag)
    query_unavailable = query_window is not None and query.sample_id is not None and query.roi_reason
    reference_unavailable = reference_window is not None and reference.sample_id is not None and reference.roi_reason
    if query_unavailable or reference_unavailable:
        reasons.append("source_unavailable")
    return ComparisonRow(cid, region.region_id if region else None, diameter, query, reference,
                         total_reason, local_reason, dt, dr, rt, rr, tuple(reasons))


def rank_rows(rows: Sequence[ComparisonRow], metric: str = "roi_level") -> list[ComparisonRow]:
    """지름별 CA level/delta로 모든 행을 정렬한다. overlap/peak/geometry는 쓰지 않는다.

    metric은 roi_level/roi_increase/total_level/total_increase다. um² 값 결측은 끝으로
    보내며 query own 값이 없으면 관측된 reference own 값을 사용한다. 상위-k 삭제,
    input 변경, IO는 없다. 서로 다른 지름을 max 하나로 축약하지 않는다.
    """
    if metric not in ("roi_level", "roi_increase", "total_level", "total_increase"):
        raise ValueError("unknown regional ordering metric")

    def key(row: ComparisonRow) -> tuple:
        value = ordering_value(row, metric)
        return (row.diameter_um, value is None, -value if value is not None else 0.,
                row.region_id or "", row.comparison_id)

    return sorted(rows, key=key)


def ordering_value(row: ComparisonRow, metric: str) -> float | None:
    """지름별 정렬에 쓰는 um² 원 값/증감을 반환한다. 결측은 null, IO/변경은 없다."""
    if metric == "roi_increase":
        return row.delta_roi_ca_um2
    if metric == "total_increase":
        return row.delta_total_ca_um2
    if metric == "roi_level":
        value = row.query.roi.roi_ca_um2 if row.query.roi else None
        return value if value is not None else row.reference.roi.roi_ca_um2 if row.reference.roi else None
    if metric == "total_level":
        value = row.query.unit_total_ca_um2
        return value if value is not None else row.reference.unit_total_ca_um2
    raise ValueError("unknown regional ordering metric")


def _context(pair: CorrespondencePair | None) -> dict:
    if pair is None:
        return {}
    return {"comparison_id": pair.comparison_id,
            "electrodes": sorted((e["canonical_electrode_id"], e["canonical_layer_id"]) for e in pair.electrodes),
            "process": pair.process["canonical_process_id"]}


def compare_pair(query: AnalysisBundle | None, reference: AnalysisBundle | None,
                 pair: CorrespondencePair | None, cfg: RegionalConfig) -> RegionalComparison:
    """명시 pair 또는 unmatched own의 모든 지름·창에 대해 순수 지역 CA 결과를 만든다.

    원본 배열은 [y,x], 원점은 셀 좌상단이다. 공통 창은 translation과 동일 lattice일
    때만 사용하며 미등록은 별도 local 패널/창으로 남긴다. scalar/ROI 결측은 null,
    confirmed_added/removed도 없는 쪽을 0으로 채우지 않는다. IO/입력 변경은 없다.
    """
    bundles = [b for b in (query, reference) if b is not None]
    if not bundles:
        raise ValueError("at least one existing sample is required")
    if any(b.failure_type not in ("lateral_open", "lateral_short", "vertical_short") for b in bundles):
        raise DataContractError("unsupported regional failure type")
    sizes = observed_diameters(bundles)
    supports = {b.sample_id: collect_support(b, sizes, cfg=cfg) for b in bundles}
    statuses = {(state.sample_id, state.diameter_um): state
                for _, states in supports.values() for state in states}
    resolution = resolve_pair(query, reference, pair)
    cid = pair.comparison_id if pair else "own:" + ":".join(sorted(b.sample_id for b in bundles))
    registration = pair.registration if pair else None
    common = resolution.identity_reason is None and resolution.registration_reason is None
    translations = {}
    sets, entries = [], []
    registration_reason = resolution.registration_reason
    if common:
        translations = {query.sample_id: registration.query_translation_xy_um,
                        reference.sample_id: registration.reference_translation_xy_um}
        shared = build_regions(bundles, cfg, translations_xy_um=translations,
                               frame_id=registration.frame_id, context=_context(pair), _supports=supports)
        if shared.reason == "incompatible_comparison_grid":
            common = False
            registration_reason = shared.reason
        else:
            sets.append(shared)
            entries.extend((region, None) for region in shared.regions)
    if not common:
        for side, bundle in (("query", query), ("reference", reference)):
            if bundle is None:
                continue
            own = build_regions([bundle], cfg, context=_context(pair), _supports=supports)
            sets.append(own)
            entries.extend((region, side) for region in own.regions)
    sizes = observed_diameters(bundles)
    rows, summaries = [], []
    for diameter in sizes:
        checked = []
        for bundle in (query, reference):
            source = _source(bundle, diameter)
            if source is not None:
                reason = source_frame_reason(bundle, source, cfg.coordinate_atol_um)
                source = replace(source, diameter_um=diameter_key(source.diameter_um),
                                 unavailable_reason=reason or source.unavailable_reason, validation=None)
            checked.append(source)
        a, b = checked
        if common:
            try:
                a = _translated_source(a, translations[query.sample_id], registration.frame_id) if a else None
                b = _translated_source(b, translations[reference.sample_id], registration.frame_id) if b else None
            except ValueError:
                # own gate는 해당 원천의 grid reason을 설명한다. 잘못된 등록으로 보정하지
                # 않는다.
                pass
        a = replace(a, validation=validate_source(a, conservation_rtol=cfg.conservation_rtol,
                    conservation_atol_um2=cfg.conservation_atol_um2)) if a else None
        b = replace(b, validation=validate_source(b, conservation_rtol=cfg.conservation_rtol,
                    conservation_atol_um2=cfg.conservation_atol_um2)) if b else None
        rows.append(_row(cid, None, diameter, resolution, a, b, cfg, statuses=statuses))
        for region, owner in entries:
            reason = None if common else resolution.identity_reason or registration_reason or "unregistered"
            rows.append(_row(cid, region, diameter, resolution, a, b, cfg, owner, reason, statuses))
        for side, source in (("query", a), ("reference", b)):
            if source is None:
                continue
            windows = [region.bounds_um for region, owner in entries if owner in (None, side)]
            owned_sets = [group for group in sets if group.common_frame or
                          any(state.sample_id == source.sample_id for state in group.support_statuses)]
            location_reason = next((group.reason for group in owned_sets if group.reason), None)
            if not windows:
                state = statuses[(source.sample_id, diameter)]
                if state.status in ("missing", "unavailable"):
                    location_reason = "location_unavailable"
            summaries.append(summarize_source(
                source, windows, cfg, location_reason=location_reason, _cached=True,
            ))
    return RegionalComparison(
        cid, resolution.relationship, query.sample_id if query else None,
        reference.sample_id if reference else None, pair, tuple(sets), tuple(rows), tuple(summaries),
        resolution.identity_reason, registration_reason,
    )
