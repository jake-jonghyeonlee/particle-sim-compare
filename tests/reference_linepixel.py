"""Test-only reference oracle: the pre-T1 (commit 01989de) linepixel/measurement code, copied verbatim.

Never imported by src. It keeps `bitorder=` (numpy >= 1.17); the equivalence suite skips under older numpy.
Only the classes whose layout did not change (Bucket, ClassShare, GroupKey, ...) are shared with src;
GroupArea is the old eager-mask version and compute_areas/_group/_membership_counts/measure_* are the old bodies.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
import copy
from typing import Iterable

import numpy as np

from simcompare.linepixel import (
    LINE, PIXEL, FAILURE_PIECES, FAILURE_PIECE_POLICY, AreaMetric, AreaResult, Bucket, ClassShare, Coverage,
    FractionMetric, GroupKey, SourceCaseArea, _json, _key, _raw_record, bucket_key,
    fraction_of_frame, pair_of,
)
from simcompare.numeric_schema import (
    DIAMETERS_UM, CaseIdentity, Frame, MaskRecord, Measurement, NumericCase, NumericContractError, NumericDataset,
    PhysicalWindow, diameter_key, finite, require_same_frame, text,
)


# Frozen copy of linepixel._validate_case at 01989de (eager records): the oracle must not follow later src edits.
def _validate_case(case: NumericCase) -> None:
    if not isinstance(case, NumericCase) or case.failure_type not in ('lateral_short', 'vertical_short'):
        raise NumericContractError('expected_canonical_short_case')
    if len(case.endpoints) != 2:
        raise NumericContractError('invalid_case_endpoints')
    if len({ep.electrode_id for ep in case.endpoints}) != 2:
        if case.pair_id is not None or case.classification is not None:
            raise NumericContractError('same_electrode_pair_cannot_be_classified')
    else:
        pair_of(*(ep.electrode_id for ep in case.endpoints))
    text(case.analysis_layer, 'analysis_layer')
    text(case.density_process_layer, 'density_process_layer')
    text(case.metadata.get('unit_id'), 'unit_id')
    if set(case.records) != set(DIAMETERS_UM):
        raise NumericContractError('incomplete_case_coverage', case.identity.case_id)
    if case.classification not in (LINE, PIXEL, None) or ((case.pair_id is None) != (case.classification is None)):
        raise NumericContractError('invalid_case_classification')
    if case.pair_id is not None:
        text(case.pair_id, 'pair_id')
    for diameter, record in case.records.items():
        if diameter_key(diameter) != diameter_key(record.diameter_um):
            raise NumericContractError('record_diameter_mismatch')
        if record.identity != case.identity or record.analysis_layer != case.analysis_layer:
            raise NumericContractError('record_case_binding_mismatch')
        if record.input_status == 'not_applicable':
            # A missing vertical record is never promoted by this computation.
            height = case.metadata.get('height_um')
            evidence = case.metadata.get('height_evidence')
            applicability = _raw_record(case, diameter).get('applicability_evidence')
            if (case.failure_type != 'vertical_short' or not isinstance(evidence, str) or not evidence.strip()
                    or not isinstance(applicability, dict) or not applicability.get('source')
                    or applicability.get('height_um') != height
                    or applicability.get('comparison') != 'diameter_um < height_um'
                    or 'below_height_vertical_short' not in record.reasons):
                raise NumericContractError('not_applicable_evidence_required')
            if not diameter < finite(height, 'height_um', positive=True) < 5:
                raise NumericContractError('invalid_not_applicable_height')


@dataclass
class GroupArea:
    key: GroupKey
    frame: Frame
    line: ClassShare
    pixel: ClassShare  # effective pixel after line priority
    transfer: AreaMetric  # diagnostic, not added to totals
    union: AreaMetric
    source_cases: tuple[SourceCaseArea, ...]
    pair_membership: dict[str, tuple[str, str]]
    line_mask: np.ndarray
    pixel_effective_mask: np.ndarray
    transfer_mask: np.ndarray
    union_mask: np.ndarray
    pair_table_version: str | None
    failure_pieces: dict[str, AreaMetric]
    failure_piece_masks: dict[str, np.ndarray]
    fractions: dict[str, FractionMetric] = field(default_factory=dict)

    @property
    def coverage(self) -> Coverage:
        return self.union.coverage

    @property
    def frame_area_um2(self) -> float:
        return self.frame.valid.size * self.frame.pixel_pitch_um ** 2

    def to_dict(self) -> dict:
        """JSON-ready provenance and area schema; binary arrays remain original assets."""
        return {
            'group': asdict(self.key),
            'frame': {'frame_id': self.frame.frame_id, 'pixel_pitch_um': self.frame.pixel_pitch_um,
                      'origin_xy_um': list(self.frame.origin_xy_um), 'shape_yx': list(self.frame.shape_yx),
                      'axis_order': self.frame.axis_order, 'origin_convention': self.frame.origin_convention,
                      'valid_hash': self.frame.valid_hash},
            'pair_table_version': self.pair_table_version,
            'pair_membership': {pid: list(members) for pid, members in sorted(self.pair_membership.items())},
            'line': asdict(self.line), 'pixel': asdict(self.pixel),
            'transfer': asdict(self.transfer), 'union': asdict(self.union),
            'source_cases': [asdict(source) for source in self.source_cases],
            'bucket_source_relationship': {'pair_lineage_case_keys':'pair_lineage',
                                           'contributing_source_case_keys':'observed_mask_intersects_bucket_at_diameter'},
            'failure_pieces': {name:asdict(metric) for name,metric in self.failure_pieces.items()},
            'failure_fragment_policy': copy.deepcopy(FAILURE_PIECE_POLICY),
            'frame_area_um2': self.frame_area_um2,
            'fraction_of_frame': {name:asdict(metric) for name,metric in self.fractions.items()},
        }


def _membership_counts(masks: dict[str, np.ndarray], keep: np.ndarray, chunk_cells: int) -> Counter:
    """Exact arbitrary-width bit patterns with O(pair_count * chunk_cells) workspace."""
    pairs = sorted(masks)
    counts = Counter()
    if not pairs:
        return counts
    flattened = [masks[pid].reshape(-1) for pid in pairs]
    flat_keep = keep.reshape(-1)
    for start in range(0, keep.size, chunk_cells):
        stop = min(start + chunk_cells, keep.size)
        membership = np.stack([mask[start:stop] for mask in flattened], axis=0)
        selected = flat_keep[start:stop] & membership.any(axis=0)
        if not selected.any():
            continue
        packed = np.packbits(membership[:, selected], axis=0, bitorder='little').T
        patterns, frequencies = np.unique(packed, axis=0, return_counts=True)
        for pattern, frequency in zip(patterns, frequencies):
            active = np.unpackbits(pattern, bitorder='little')[:len(pairs)]
            members = tuple(pairs[i] for i in np.flatnonzero(active))
            counts[members] += int(frequency)
    return counts


def _group(key, entries, chunk_cells, table_version):
    entries = sorted(entries, key=lambda item: _json(asdict(item[0].identity)))
    frame = entries[0][1].frame
    for _, record in entries:
        require_same_frame(frame, record.frame)
    sources, usable, lineage, electrodes = [], {LINE: {}, PIXEL: {}}, {}, {}
    contributor_records = {}
    type_masks = {name:np.zeros(frame.shape_yx, bool) for name in ('lateral_short','vertical_short')}
    observed = missing = na = unmapped = rejected = computable = 0
    reasons = set()
    for case, record in entries:
        measured = measure_full_mask(record)
        source_key = _key('source_case_v1', {'identity': asdict(case.identity), 'diameter_um': key.diameter_um})
        raw_record = _raw_record(case, key.diameter_um)
        sources.append(SourceCaseArea(source_key, case.identity, case.endpoints, case.failure_type,
                                      case.pair_id, case.classification, key.diameter_um, record.input_status,
                                      measured, copy.deepcopy(case.raw), copy.deepcopy(raw_record.get('applicability_evidence')),
                                      case.metadata.get('height_evidence')))
        observed += record.input_status == 'observed'
        missing += record.input_status == 'missing'
        na += record.input_status == 'not_applicable'
        if record.input_status == 'missing':
            reasons.update(record.reasons)
        if case.classification is None:
            unmapped += 1
            reasons.add('unmapped_pair')
        else:
            pid = case.pair_id
            pair = tuple(sorted(ep.electrode_id for ep in case.endpoints))
            if pid in electrodes and electrodes[pid] != pair:
                raise NumericContractError('pair_id_collision')
            electrodes[pid] = pair
            lineage.setdefault(pid, []).append(source_key)
        reasons.update(case.reasons)
        if record.input_status == 'observed':
            reasons.update(measured.reasons)
            if measured.status == 'unavailable':
                rejected += 1
                reasons.update(measured.reasons)
            elif case.classification is not None:
                computable += 1
                usable[case.classification].setdefault(case.pair_id, []).append(record)
                contributor_records[source_key] = record
                type_masks[case.failure_type] |= record.mask & frame.valid
    coverage = Coverage(len(entries), observed, missing, na, unmapped, rejected, computable,
                        frame.valid.size, int(frame.valid.sum()))
    if not frame.valid.all():
        reasons.add('partial_observation')
    if unmapped:
        reasons.add('classification_coverage_incomplete')
    if missing or rejected:
        reasons.add('source_coverage_incomplete')
    if na == len(entries) and not unmapped:
        status = 'not_applicable'
        reasons.add('all_sources_not_applicable')
    elif reasons:
        status = 'partial' if computable else 'unavailable'
    else:
        status = 'available'
    reason_tuple = tuple(sorted(reasons))
    pixel_area = frame.pixel_pitch_um ** 2

    def metric(area):
        return AreaMetric(area if status == 'available' else None,
                          area, status, reason_tuple, coverage,
                          partial_value=area if status == 'partial' else None)

    masks = {kind: {pid: union_masks(records) & frame.valid for pid, records in by_pair.items()}
             for kind, by_pair in usable.items()}
    combined = {kind: np.zeros(frame.shape_yx, dtype=bool) for kind in (LINE, PIXEL)}
    for kind in combined:
        for mask in masks[kind].values():
            combined[kind] |= mask
    line = combined[LINE]
    effective = combined[PIXEL] & ~line
    transfer = combined[PIXEL] & line
    union = line | combined[PIXEL]

    def shares(kind, keep):
        counts = _membership_counts(masks[kind], keep, chunk_cells)
        exclusive, shared = [], []
        for members, count in sorted(counts.items()):
            inside = keep.copy()
            for pid, mask in masks[kind].items():
                inside &= mask if pid in members else ~mask
            pair_lineage = tuple(sorted(source for pid in members for source in lineage[pid]))
            contributors = tuple(source for source in pair_lineage if source in contributor_records
                                 and np.any(contributor_records[source].mask & inside))
            bucket = Bucket(bucket_key(key, kind, members, pair_table_version=table_version),
                            'exclusive' if len(members) == 1 else 'shared', members, metric(count * pixel_area),
                            pair_lineage, contributors)
            (exclusive if len(members) == 1 else shared).append(bucket)
        return ClassShare(metric(sum(counts.values()) * pixel_area), tuple(exclusive), tuple(shared))

    lateral, vertical = type_masks['lateral_short'], type_masks['vertical_short']
    piece_masks = {'lateral_only': lateral & ~vertical,
                   'vertical_only': vertical & ~lateral, 'simultaneous': lateral & vertical}
    pieces = {name:replace(metric(int(mask.sum())*pixel_area),
                          basis='full_mask_valid_observed_sources_failure_type_union')
              for name,mask in piece_masks.items()}
    vertical_entries = [record for case,record in entries if case.failure_type=='vertical_short']
    if vertical_entries and all(record.input_status=='not_applicable' for record in vertical_entries):
        for name in ('vertical_only','simultaneous'):
            pieces[name]=replace(pieces[name],value=None,partial_value=None,status='not_applicable',
                                 reasons=('all_vertical_sources_not_applicable',))
    for mask in (line, effective, transfer, union, *piece_masks.values()):
        mask.setflags(write=False)
    group = GroupArea(key, frame, shares(LINE, frame.valid), shares(PIXEL, frame.valid & ~line),
                      metric(int(transfer.sum()) * pixel_area), metric(int(union.sum()) * pixel_area),
                      tuple(sources), electrodes, line, effective, transfer, union, table_version, pieces, piece_masks)
    group.fractions = {'line': fraction_of_frame(group.line.area, frame),
                       'pixel_effective': fraction_of_frame(group.pixel.area, frame),
                       'transfer': fraction_of_frame(group.transfer, frame),
                       'union': fraction_of_frame(group.union, frame),
                       **{name: fraction_of_frame(pieces[name], frame) for name in FAILURE_PIECES}}
    return group


def compute_areas(inputs: NumericDataset | Iterable[NumericCase], *, chunk_cells: int = 65536) -> AreaResult:
    """Per-source-condition/layer/diameter full-mask areas with conservative coverage.

    Incomplete groups expose observed-source partial areas, not lower bounds:
    an unknown line mask could remove part of the currently effective pixel area.
    No aggregation across diameter, source conditions, products, density or units.
    """
    if isinstance(chunk_cells, bool) or not isinstance(chunk_cells, int) or chunk_cells <= 0:
        raise NumericContractError('invalid_membership_chunk_size')
    dataset = inputs if isinstance(inputs, NumericDataset) else None
    cases = list(dataset.cases if dataset else inputs)
    grouped, seen, pair_definitions, electrode_definitions = {}, set(), {}, {}
    for case in cases:
        _validate_case(case)
        if case.identity in seen:
            raise NumericContractError('duplicate_case_identity')
        seen.add(case.identity)
        if case.pair_id is not None:
            definition = (pair_of(*(ep.electrode_id for ep in case.endpoints)), case.classification)
            namespace = (case.identity.product_id, case.pair_id)
            if namespace in pair_definitions and pair_definitions[namespace] != definition:
                code = ('pair_id_collision' if pair_definitions[namespace][0] != definition[0]
                        else 'conflicting_pair_definition')
                raise NumericContractError(code)
            pair_definitions[namespace] = definition
            electrode_key = (case.identity.product_id, definition[0])
            mapped = (case.pair_id, case.classification)
            if electrode_key in electrode_definitions and electrode_definitions[electrode_key] != mapped:
                raise NumericContractError('conflicting_pair_definition')
            electrode_definitions[electrode_key] = mapped
            if dataset is not None:
                pair = dataset.pairs.get(definition[0])
                if pair is None or pair.pair_id != case.pair_id or pair.classification != case.classification:
                    raise NumericContractError('case_pair_table_mismatch')
        for diameter, record in case.records.items():
            i = case.identity
            key = GroupKey(i.product_id, i.design_version, case.metadata['unit_id'], i.source_id,
                           i.process_step_id, i.exposure_id, case.analysis_layer, case.density_process_layer,
                           i.source_contract_version, i.frame_id, float(diameter))
            grouped.setdefault(key, []).append((case, record))
    if not cases:
        raise NumericContractError('missing_expected_cases')
    provenance = dataset.provenance if dataset else {}
    if dataset is not None:
        expected = {CaseIdentity(**{name: raw[name] for name in CaseIdentity.__dataclass_fields__})
                    for raw in provenance['cases']}
        if seen != expected:
            raise NumericContractError('expected_case_coverage_mismatch')
    table_version = provenance.get('pair_table_version')
    return AreaResult({key: _group(key, grouped[key], chunk_cells, table_version) for key in sorted(grouped)},
                      copy.deepcopy(dataset.gates) if dataset else {}, provenance.get('input_hash'),
                      table_version, provenance.get('pair_table_hash'), bool(provenance.get('is_synthetic',False)))


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
    result = np.zeros(first.frame.shape_yx, dtype=bool)
    for record in records:
        np.logical_or(result, record.mask, out=result)
    return result


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
    rtol = max(finite(rtol, 'conservation rtol', nonnegative=True), record.conservation_rtol)
    atol = max(finite(atol, 'conservation atol', nonnegative=True), record.conservation_atol_um2)
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
    warnings = tuple(record.reasons) + ((f'active_cells_outside_valid:{outside_valid}',) if outside_valid else ())
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
    complete = complete and not record.reasons
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
