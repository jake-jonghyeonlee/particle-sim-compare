"""numeric_v2 full-mask line/pixel unions and exact pair membership areas.

T3 owns only original area/membership: no density, unit divisor, panel count,
comparison or yield conversion. Source failure types remain provenance; G7a's
lateral-only/vertical-only/simultaneous decomposition is not implemented.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import copy
import hashlib
import json
from typing import Iterable

import numpy as np

from .measurement import measure_full_mask, union_masks
from .numeric_schema import (
    DIAMETERS_UM, CaseIdentity, Endpoint, Frame, MaskRecord, Measurement,
    NumericCase, NumericContractError, NumericDataset, Status,
    diameter_key, finite, require_same_frame, text,
)

LINE = 'line'
PIXEL = 'pixel'


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _key(namespace: str, payload) -> str:
    return namespace + ':' + hashlib.sha256(_json(payload).encode('utf-8')).hexdigest()


def pair_of(a: str, b: str) -> frozenset[str]:
    text(a, 'electrode_id')
    text(b, 'electrode_id')
    if a == b:
        raise NumericContractError('invalid_pair', 'two distinct electrodes required')
    return frozenset((a, b))


@dataclass(frozen=True, order=True)
class GroupKey:
    product_id: str
    design_version: str
    unit_id: str
    source_id: str
    process_step_id: str
    exposure_id: str
    analysis_layer: str
    density_process_layer: str
    source_contract_version: str
    frame_id: str
    diameter_um: float


@dataclass(frozen=True)
class Coverage:
    expected: int
    observed: int
    missing: int
    not_applicable: int
    unmapped: int
    rejected: int
    computable: int
    expected_cells: int
    observed_cells: int


@dataclass(frozen=True)
class AreaMetric:
    value: float | None
    observed_area_um2: float
    status: Status
    reasons: tuple[str, ...]
    coverage: Coverage
    unit: str = 'um^2'
    basis: str = 'full_mask_valid_observed_sources_line_priority'


@dataclass(frozen=True)
class SourceCaseArea:
    source_key: str
    identity: CaseIdentity
    endpoints: tuple[Endpoint, Endpoint]
    failure_type: str
    pair_id: str | None
    classification: str | None
    diameter_um: float
    input_status: str
    measurement: Measurement
    raw: dict
    applicability_evidence: dict | None
    height_evidence: str | None


@dataclass(frozen=True)
class Bucket:
    bucket_id: str
    kind: str  # exclusive or shared, never a per-failure-type metric
    pair_ids: tuple[str, ...]
    area: AreaMetric
    source_case_keys: tuple[str, ...]  # pair lineage, not causal apportionment


@dataclass(frozen=True)
class ClassShare:
    area: AreaMetric
    exclusive: tuple[Bucket, ...]
    shared: tuple[Bucket, ...]


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

    @property
    def coverage(self) -> Coverage:
        return self.union.coverage

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
            'bucket_source_relationship': 'pair_lineage',
            'failure_fragment_policy': {'status': 'pending', 'value': None,
                                        'reasons': ['failure_fragment_scope_unconfirmed']},
        }


@dataclass
class AreaResult:
    groups: dict[GroupKey, GroupArea]
    gates: dict
    input_hash: str | None
    pair_table_version: str | None
    pair_table_hash: str | None

    def to_dict(self) -> dict:
        return {'schema_version': 'linepixel_area_v1', 'basis': 'full_mask_valid',
                'input_hash': self.input_hash, 'pair_table_version': self.pair_table_version,
                'pair_table_hash': self.pair_table_hash, 'gates': copy.deepcopy(self.gates),
                'groups': [self.groups[key].to_dict() for key in sorted(self.groups)]}


def bucket_key(group: GroupKey, classification: str, pair_ids: Iterable[str], *,
               pair_table_version: str | None = None) -> str:
    members = tuple(sorted(set(pair_ids)))
    if classification not in (LINE, PIXEL) or not members:
        raise NumericContractError('invalid_bucket_membership')
    for member in members:
        text(member, 'pair_id')
    return _key('bucket_v1', {'group': asdict(group), 'class': classification,
                            'pair_table_version': pair_table_version,
                            'kind': 'exclusive' if len(members) == 1 else 'shared', 'pair_ids': members})


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


def _raw_record(case: NumericCase, diameter: float) -> dict:
    for raw_record in case.metadata.get('records', ()):
        if 'diameter_um' in raw_record:
            key = diameter_key(raw_record['diameter_um'])
        else:
            key = diameter_key(raw_record['diameter'], raw_record['diameter_unit'])
        if key == diameter_key(diameter):
            return raw_record
    return {}


def _validate_case(case: NumericCase) -> None:
    if not isinstance(case, NumericCase) or case.failure_type not in ('lateral_short', 'vertical_short'):
        raise NumericContractError('expected_canonical_short_case')
    if len(case.endpoints) != 2:
        raise NumericContractError('invalid_case_endpoints')
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


def _group(key, entries, chunk_cells, table_version):
    entries = sorted(entries, key=lambda item: _json(asdict(item[0].identity)))
    frame = entries[0][1].frame
    for _, record in entries:
        require_same_frame(frame, record.frame)
    sources, usable, lineage, electrodes = [], {LINE: {}, PIXEL: {}}, {}, {}
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
        if record.input_status == 'observed':
            if measured.status == 'unavailable':
                rejected += 1
                reasons.update(measured.reasons)
            elif case.classification is not None:
                computable += 1
                usable[case.classification].setdefault(case.pair_id, []).append(record)
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
        return AreaMetric(area if status in ('available', 'partial') else None,
                          area, status, reason_tuple, coverage)

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
            bucket = Bucket(bucket_key(key, kind, members, pair_table_version=table_version),
                            'exclusive' if len(members) == 1 else 'shared', members, metric(count * pixel_area),
                            tuple(sorted(source for pid in members for source in lineage[pid])))
            (exclusive if len(members) == 1 else shared).append(bucket)
        return ClassShare(metric(sum(counts.values()) * pixel_area), tuple(exclusive), tuple(shared))

    for mask in (line, effective, transfer, union):
        mask.setflags(write=False)
    return GroupArea(key, frame, shares(LINE, frame.valid), shares(PIXEL, frame.valid & ~line),
                     metric(int(transfer.sum()) * pixel_area), metric(int(union.sum()) * pixel_area),
                     tuple(sources), electrodes, line, effective, transfer, union, table_version)


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
                      table_version, provenance.get('pair_table_hash'))
