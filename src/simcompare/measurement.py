"""Original nominal CA and half-open physical window unions, without rescaling.

Cells have constant nominal occupancy. Fractional window clipping is physical
integration, not resizing, probability union, registration or causal attribution.
"""
from collections.abc import Sequence
from dataclasses import replace

import numpy as np

from .numeric_schema import (
    MaskRecord, Measurement, NumericContractError, PhysicalWindow, finite,
    require_same_frame,
)


def union_masks(records: Sequence[MaskRecord]) -> np.ndarray:
    """Validate compatible original sources before OR; callers retain all cases."""
    if not records:
        raise NumericContractError('empty_union')
    first = records[0]
    for record in records:
        require_same_frame(first.frame, record.frame)
        if record.analysis_layer != first.analysis_layer:
            raise NumericContractError('incompatible_analysis_layer')
        if record.input_status != 'observed' or record.diameter_um != first.diameter_um:
            raise NumericContractError('incompatible_union_record')
        if record.identity is not None and first.identity is not None:
            a, b = record.identity, first.identity
            if (a.product_id, a.design_version, a.source_id, a.process_step_id,
                a.exposure_id, a.source_contract_version) != (
                b.product_id, b.design_version, b.source_id, b.process_step_id,
                b.exposure_id, b.source_contract_version):
                raise NumericContractError('incompatible_source_conditions')
        elif record.identity != first.identity:
            raise NumericContractError('incompatible_source_identity')
    return np.logical_or.reduce([r.mask for r in records])


def _slabs(rectangles):
    cuts = sorted({x for x0, _, x1, _ in rectangles for x in (x0, x1)})
    for left, right in zip(cuts, cuts[1:]):
        intervals = sorted((y0, y1) for x0, y0, x1, y1 in rectangles if x0 < right and x1 > left)
        merged = []
        for low, high in intervals:
            if merged and low <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], high)
            else:
                merged.append([low, high])
        yield left, right, merged


def _cell_areas(frame, rectangles):
    h, w = frame.shape_yx
    pitch = frame.pixel_pitch_um
    ox, oy = frame.origin_xy_um
    xe, ye = ox + np.arange(w + 1) * pitch, oy + np.arange(h + 1) * pitch
    areas = np.zeros((h, w), dtype=float)
    clipped = [(max(x0, xe[0]), max(y0, ye[0]), min(x1, xe[-1]), min(y1, ye[-1]))
               for x0, y0, x1, y1 in rectangles
               if x1 > xe[0] and x0 < xe[-1] and y1 > ye[0] and y0 < ye[-1]]
    for left, right, intervals in _slabs(clipped):
        dx = np.maximum(0, np.minimum(xe[1:], right) - np.maximum(xe[:-1], left))
        dy = np.zeros(h)
        for low, high in intervals:
            dy += np.maximum(0, np.minimum(ye[1:], high) - np.maximum(ye[:-1], low))
        areas += dy[:, None] * dx[None, :]
    return areas


def _conservation(record, rtol, atol):
    finite(rtol, 'conservation rtol', nonnegative=True)
    finite(atol, 'conservation atol', nonnegative=True)
    source = record.source_ca
    if source is None:
        return None, None, ()
    if source.frame_id != record.frame.frame_id or source.valid_hash != record.frame.valid_hash:
        return None, source.value_um2, ('source_ca_domain_mismatch',)
    integrated = float(np.count_nonzero(record.mask & record.frame.valid) * record.frame.pixel_pitch_um ** 2)
    residual = integrated - source.value_um2
    reasons = ('conservation_failed',) if abs(residual) > atol + rtol * abs(source.value_um2) else ()
    return residual, source.value_um2, reasons


def measure_union(record: MaskRecord, windows: Sequence[PhysicalWindow], *,
                  conservation_rtol: float = 1e-9, conservation_atol_um2: float = 1e-9) -> Measurement:
    """Measure one original source over a union of explicit windows.

    ca_um2 is null for partial/unavailable observation; partial_value retains the known sum.
    observed_ca_um2 retains the geometric
    zero/known partial sum. A conservation failure reports residual, never scales.
    """
    if not isinstance(record, MaskRecord):
        raise NumericContractError('expected_nominal_source')
    rectangles = [window.bounds_um() for window in windows]
    if not rectangles:
        raise NumericContractError('empty_window_union')
    requested = sum((right - left) * sum(high - low for low, high in intervals)
                    for left, right, intervals in _slabs(rectangles))
    finite(requested, 'requested window area', positive=True)
    if record.input_status != 'observed':
        return Measurement(None, 0.0, requested, None,
                           'not_applicable' if record.input_status == 'not_applicable' else 'unavailable',
                           record.reasons, identity=record.identity, basis='physical_window_union')
    outside_valid = int(np.count_nonzero(record.mask & ~record.frame.valid))
    warnings = (f'active_cells_outside_valid:{outside_valid}',) if outside_valid else ()
    residual, source_ca, errors = _conservation(record, conservation_rtol, conservation_atol_um2)
    if errors:
        return Measurement(None, 0.0, requested, None, 'unavailable', (*errors,*warnings),
                           residual, source_ca, identity=record.identity, basis='physical_window_union')
    cell_areas = _cell_areas(record.frame, rectangles)
    areas = cell_areas * record.frame.valid
    valid_area = float(areas.sum())
    observed_ca = float(areas[record.mask].sum())
    if valid_area == 0:
        return Measurement(None, 0.0, requested, None, 'unavailable', ('empty_valid_area',*warnings),
                           residual, source_ca, 0.0, record.identity, 'physical_window_union')
    x0, y0, x1, y1 = record.frame.bbox_um
    complete = (all(a >= x0 and b >= y0 and c <= x1 and d <= y1 for a, b, c, d in rectangles)
                and not np.any(cell_areas[~record.frame.valid] > 0))
    return Measurement(observed_ca if complete else None, valid_area, requested,
                       observed_ca / valid_area if complete else None,
                       'available' if complete else 'partial', warnings if complete else ('partial_observation',*warnings),
                       residual, source_ca, observed_ca, record.identity, 'physical_window_union',
                       partial_value=None if complete else observed_ca,
                       partial_ca_per_valid_area=None if complete else observed_ca / valid_area)


def measure_window(record: MaskRecord, window: PhysicalWindow, **kwargs) -> Measurement:
    return measure_union(record, [window], **kwargs)


def measure_full_mask(record: MaskRecord, **kwargs) -> Measurement:
    result = measure_window(record, PhysicalWindow(*record.frame.bbox_um), **kwargs)
    return replace(result, basis='full_mask_valid')
