"""numeric_v2 full-mask line/pixel unions and exact pair membership areas.

T3 area/membership is preserved independently from T4 synthetic weighting and
panel comparison. No default mask divisor or yield conversion is applied. G7a failure pieces are a separate axis of the same mapped-short union;
they do not change line/pixel priority, pair buckets or transfer.
"""
from __future__ import annotations
from .numeric_schema import current_gate_decisions

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
import copy
import hashlib
import json
from typing import Iterable, NamedTuple

import numpy as np

from .measurement import check_union_compatible, measure_full_mask
from .numeric_schema import (
    DEFAULT_MAX_BUCKET_OUTPUT_ENTRIES, DEFAULT_MAX_LABEL_PATTERN_ENTRIES, DEFAULT_TOP_BUCKETS,
    DIAMETERS_UM, CaseIdentity, Endpoint, Frame, MaskRecord, Measurement,
    NumericCase, NumericContractError, NumericDataset, Status,
    diameter_key, finite, require_same_frame, text,
)

LINE = 'line'
PIXEL = 'pixel'
FAILURE_PIECES = ('lateral_only', 'vertical_only', 'simultaneous')
# Budget for bucket output entries (pair ids + lineage keys + contributor keys) of one layer/diameter group, and
# for the live patterns (sum of member counts of all distinct pair-membership patterns) of the exact label
# partition of one group; stored by predecessor encoding, far cheaper per entry than emitted output.
# compute_areas(max_*=...) overrides them; real runs set them from condition_assignment. The defaults are defined
# once in numeric_schema (shared with the admission estimate of adapters.real_hotspot).
# Label history is compacted when it exceeds max(_COMPACT_MIN_HISTORY, _COMPACT_FACTOR * labels kept at the
# last compaction); module level so tests can shrink the threshold.
_COMPACT_MIN_HISTORY = 1 << 16
_COMPACT_FACTOR = 4
_LABEL_HISTORY_LIMIT = 1 << 30
_POPCOUNT = np.array([bin(i).count('1') for i in range(256)], dtype=np.uint8)
FAILURE_PIECE_POLICY = {'status':'implemented','value':list(FAILURE_PIECES),
                        'decided_on':'2026-10-08','axis':'failure_type','reasons':[]}


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
    partial_value: float | None = None
    unit: str = 'um^2'
    basis: str = 'full_mask_valid_observed_sources_line_priority'


@dataclass(frozen=True)
class FractionMetric:
    """area / frame area of the measured 2×2 domain: dimensionless, unit-size independent.

    The G2 one-unit divisor cancels in this ratio; the finite 2×2 edge effect does not.
    It is a comparison index, never a probability, yield or density-weighted value.
    """
    value: float | None
    partial_value: float | None
    status: Status
    reasons: tuple[str, ...]
    numerator_um2: float
    frame_area_um2: float
    valid_area_um2: float
    unit: str = '1'
    basis: str = 'area_over_frame_area'


FRACTION_NAMES = ('line', 'pixel_effective', 'transfer', 'union', *FAILURE_PIECES)


def fraction_of_frame(metric: AreaMetric, frame: Frame) -> FractionMetric:
    frame_area = frame.valid.size * frame.pixel_pitch_um ** 2
    valid_area = int(frame.valid.sum()) * frame.pixel_pitch_um ** 2
    if metric.status == 'available':
        return FractionMetric(finite(metric.value / frame_area, 'fraction', nonnegative=True), None, 'available',
                              metric.reasons, metric.value, frame_area, valid_area)
    partial = None
    if metric.status == 'partial' and metric.partial_value is not None:
        partial = finite(metric.partial_value / frame_area, 'partial fraction', nonnegative=True)
    return FractionMetric(None, partial, metric.status, metric.reasons, metric.observed_area_um2, frame_area, valid_area)


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
    pair_lineage_case_keys: tuple[str, ...]
    contributing_source_case_keys: tuple[str, ...]

    @property
    def source_case_keys(self):
        """호환 alias: pair lineage이며 실제 면적 기여자 목록이 아니다."""
        return self.pair_lineage_case_keys


@dataclass(frozen=True)
class ClassShare:
    area: AreaMetric
    exclusive: tuple[Bucket, ...]
    shared: tuple[Bucket, ...]


@dataclass(frozen=True)
class ClassShareSummary(ClassShare):
    """Summary-mode share: exclusive/shared are empty; only the top-N buckets by area carry lineage/contributors."""
    bucket_count_exclusive: int = 0
    bucket_count_shared: int = 0
    top_buckets: tuple[Bucket, ...] = ()


@dataclass(frozen=True)
class PackedMask:
    """Row-major np.packbits of a boolean mask (default bit order, so numpy >= 1.16 is enough)."""
    shape: tuple
    bits: np.ndarray

    def unpack(self) -> np.ndarray:
        size = int(np.prod(self.shape, dtype=np.int64))
        mask = np.unpackbits(self.bits)[:size].view(np.bool_).reshape(self.shape)
        mask.setflags(write=False)
        return mask

    def count(self) -> int:
        return int(_POPCOUNT[self.bits].sum(dtype=np.int64))


class _PieceMasks(Mapping):
    """Dict-style view of the packed failure pieces; each lookup returns a fresh read-only array."""

    def __init__(self, packed):
        self._packed = packed

    def __getitem__(self, name):
        return self._packed[name].unpack()

    def __iter__(self):
        return iter(self._packed)

    def __len__(self):
        return len(self._packed)


@dataclass
class GroupArea:
    """Retained masks are packed (1 bit/cell); the *_mask properties unpack to a fresh read-only array."""
    key: GroupKey
    frame: Frame
    line: ClassShare
    pixel: ClassShare  # effective pixel after line priority
    transfer: AreaMetric  # diagnostic, not added to totals
    union: AreaMetric
    source_cases: tuple[SourceCaseArea, ...]
    pair_membership: dict[str, tuple[str, str]]
    line_packed: PackedMask
    pixel_effective_packed: PackedMask
    transfer_packed: PackedMask
    union_packed: PackedMask
    pair_table_version: str | None
    failure_pieces: dict[str, AreaMetric]
    failure_piece_packed: dict[str, PackedMask]
    fractions: dict[str, FractionMetric] = field(default_factory=dict)

    @property
    def line_mask(self) -> np.ndarray:
        return self.line_packed.unpack()

    @property
    def pixel_effective_mask(self) -> np.ndarray:
        return self.pixel_effective_packed.unpack()

    @property
    def transfer_mask(self) -> np.ndarray:
        return self.transfer_packed.unpack()

    @property
    def union_mask(self) -> np.ndarray:
        return self.union_packed.unpack()

    @property
    def failure_piece_masks(self) -> Mapping:
        return _PieceMasks(self.failure_piece_packed)

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
            'line': _share_dict(self.line), 'pixel': _share_dict(self.pixel),
            'transfer': asdict(self.transfer), 'union': asdict(self.union),
            'source_cases': [asdict(source) for source in self.source_cases],
            'bucket_source_relationship': {'pair_lineage_case_keys':'pair_lineage',
                                           'contributing_source_case_keys':'observed_mask_intersects_bucket_at_diameter'},
            'failure_pieces': {name:asdict(metric) for name,metric in self.failure_pieces.items()},
            'failure_fragment_policy': copy.deepcopy(FAILURE_PIECE_POLICY),
            'frame_area_um2': self.frame_area_um2,
            'fraction_of_frame': {name:asdict(metric) for name,metric in self.fractions.items()},
        }


def _share_dict(share) -> dict:
    out = asdict(share)
    if isinstance(share, ClassShareSummary):
        del out['exclusive'], out['shared']
    return out


@dataclass
class AreaResult:
    groups: dict[GroupKey, GroupArea]
    gates: dict
    input_hash: str | None
    pair_table_version: str | None
    pair_table_hash: str | None
    is_synthetic: bool = False

    def to_dict(self) -> dict:
        return {'schema_version': 'linepixel_area_v1', 'basis': 'full_mask_valid',
                'is_synthetic':self.is_synthetic, 'input_hash': self.input_hash, 'pair_table_version': self.pair_table_version,
                'pair_table_hash': self.pair_table_hash, 'gates': current_gate_decisions() if self.gates else {},
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


def _pack(mask: np.ndarray) -> np.ndarray:
    return np.packbits(np.ascontiguousarray(mask).reshape(-1))


def _unpack_flat(bits: np.ndarray, size: int) -> np.ndarray:
    return np.unpackbits(bits)[:size].view(np.bool_)


def _oversized_buckets(key, entries: int, limit: int) -> NumericContractError:
    return NumericContractError('oversized_real_bucket_output',
                                f'{key.analysis_layer} d={key.diameter_um}: {entries} entries; limit {limit}')


def _oversized_label_patterns(key, entries: int, limit: int) -> NumericContractError:
    return NumericContractError('oversized_real_label_patterns',
                                f'{key.analysis_layer} d={key.diameter_um}: {entries} live label-pattern entries; '
                                f'limit {limit}; raise condition_assignment.max_label_pattern_entries (and '
                                'max_operation_bytes if needed) or split the cases across layers/groups')


def _partition_labels(pair_ids, select, size, *, live_limit=None, on_oversize=None, stats=None):
    """Exact membership partition by iterative refinement, O(pairs * cells) time.

    pair_ids: sorted pair ids; select(pid) -> flat indices of the cells that pair covers inside `keep`.
    A cell's label identifies the set of pairs covering it (0 = none). Patterns are stored by predecessor
    encoding (parent label, added pair): O(1) per new label, tuples are rebuilt only for final live labels.

    Memory: a label that stops being live has had all its cells moved to its single child, so every history
    entry is an ancestor of a final live label and the history never exceeds the sum of the live patterns'
    lengths (+1) -- exactly the pair_ids entries the buckets would emit. live_limit bounds that sum: it is
    checked whenever the history passes a threshold (max(_COMPACT_MIN_HISTORY, _COMPACT_FACTOR * history at the
    last check), but never above max(_COMPACT_MIN_HISTORY, live_limit)), raising on_oversize(n) before the history
    can grow past live_limit by more than one refinement step.
    The check also drops memo entries of dead parents. Labels stay int32 (int64 for >= 2**30 cells) and the
    history is capped at _LABEL_HISTORY_LIMIT.
    Returns (label array, {members tuple: cell count}, {members tuple: label id}).
    """
    dtype = np.int32 if size < _LABEL_HISTORY_LIMIT else np.int64
    label = np.zeros(size, dtype=dtype)
    count = len(pair_ids)
    parent, added, depth, child = [0], [0], [0], {}
    threshold = _COMPACT_MIN_HISTORY if live_limit is None else min(_COMPACT_MIN_HISTORY, live_limit)
    if on_oversize is None:
        def on_oversize(n):
            return NumericContractError('oversized_real_bucket_output', f'{n} label history entries')
    stats = {} if stats is None else stats
    stats.update(peak_history=1, compactions=0, final_live=0)

    def check():
        nonlocal child, threshold
        live = np.unique(label)
        emitted = int(np.asarray(depth, dtype=np.int64)[live].sum())
        if live_limit is not None and emitted > live_limit:
            raise on_oversize(emitted)
        alive = np.zeros(len(parent), dtype=bool)
        alive[live] = True
        child = {slot: found for slot, found in child.items() if alive[slot // count]}
        threshold = min(max(_COMPACT_MIN_HISTORY, _COMPACT_FACTOR * len(parent)), _LABEL_HISTORY_LIMIT)
        if live_limit is not None:
            # history <= live patterns + 1, so the history passing the limit is always checked, however small the
            # limit is. A check that passed at history = limit + 1 is not repeated at every following step.
            threshold = max(min(threshold, live_limit), len(parent))
        stats['compactions'] += 1

    def prospective(present, selected_counts):
        """Live pattern entries after the coming step: a fully moved label adds 1, a split one depth + 1."""
        frequency = np.bincount(label, minlength=len(parent))
        depths = np.asarray(depth, dtype=np.int64)
        split = frequency[present] > selected_counts
        return int(depths[frequency > 0].sum()) + int(np.where(split, depths[present] + 1, 1).sum())

    for index, pid in enumerate(pair_ids):
        if len(parent) > threshold:
            check()
        selected = select(pid)
        if not selected.size:
            continue
        old = label[selected]
        n = len(parent)
        dense = n <= max(1024, 2 * old.size)
        if dense:
            selected_counts = np.bincount(old, minlength=n)
            present = np.flatnonzero(selected_counts)
            selected_counts = selected_counts[present]
        else:
            present, inverse, selected_counts = np.unique(old, return_inverse=True, return_counts=True)
        if live_limit is not None and n + present.size > live_limit + 1:
            # The step adds one history node per present label and history <= live patterns + 1, so it can only
            # stay within the limit if the live patterns after it do: price it before the history grows.
            emitted = prospective(present, selected_counts)
            if emitted > live_limit:
                raise on_oversize(emitted)
        if dense:
            lookup = np.zeros(n, dtype=dtype)
            lookup[present] = _child_ids(present.tolist(), index, count, parent, added, depth, child)
            label[selected] = lookup[old]
        else:
            fresh = np.asarray(_child_ids(present.tolist(), index, count, parent, added, depth, child), dtype=dtype)
            label[selected] = fresh[inverse]
        if len(parent) >= _LABEL_HISTORY_LIMIT:
            raise on_oversize(len(parent))
        stats['peak_history'] = max(stats['peak_history'], len(parent))
    frequencies = np.bincount(label, minlength=len(parent))
    if live_limit is not None:
        emitted = int(np.asarray(depth, dtype=np.int64)[frequencies > 0].sum())
        if emitted > live_limit:
            raise on_oversize(emitted)
    counts, ids = {}, {}
    for label_id in np.flatnonzero(frequencies):
        label_id = int(label_id)
        if label_id == 0:
            continue
        members, node = [], label_id
        while node:
            members.append(pair_ids[added[node]])
            node = parent[node]
        members = tuple(reversed(members))
        counts[members] = counts.get(members, 0) + int(frequencies[label_id])
        ids[members] = label_id
    stats['final_live'] = len(counts)
    return label, counts, ids


def _child_ids(parents, index, count, parent, added, depth, child):
    out = []
    for old in parents:
        slot = old * count + index
        found = child.get(slot)
        if found is None:
            found = len(parent)
            parent.append(old)
            added.append(index)
            depth.append(depth[old] + 1)
            child[slot] = found
        out.append(found)
    return out


def _raw_record(case: NumericCase, diameter: float) -> dict:
    for raw_record in case.metadata.get('records', ()):
        if 'diameter_um' in raw_record:
            key = diameter_key(raw_record['diameter_um'])
        else:
            key = diameter_key(raw_record['diameter'], raw_record['diameter_unit'])
        if key == diameter_key(diameter):
            return raw_record
    return {}


def _record_heads(case: NumericCase):
    """(diameter, record-or-header) pairs; lazy records (LazyRecords) are read by header, never loaded."""
    header = getattr(case.records, 'header', None)
    if header is None:
        return list(case.records.items())
    return [(diameter, header(diameter)) for diameter in case.records]


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
    for diameter, record in _record_heads(case):
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


class _UnionMeta(NamedTuple):
    """What check_union_compatible needs from the first source of a pair; never its mask."""
    frame: Frame
    analysis_layer: str | None
    input_status: str
    diameter_um: float
    identity: CaseIdentity | None


def _group(key, entries, chunk_cells, table_version, *, raw_copies=None,
           max_bucket_entries=DEFAULT_MAX_BUCKET_OUTPUT_ENTRIES,
           max_label_entries=DEFAULT_MAX_LABEL_PATTERN_ENTRIES, top_buckets=None):
    """top_buckets=None keeps every bucket; an int N keeps lineage/contributors only for the N largest buckets per class."""
    entries = sorted(entries, key=lambda item: _json(asdict(item[0].identity)))
    frame = entries[0][1].frame
    for _, head in entries:
        require_same_frame(frame, head.frame)
    shape, size = frame.shape_yx, frame.valid.size
    valid_flat = frame.valid.reshape(-1)
    valid_bits = _pack(frame.valid)
    area_cache = {}
    sources, lineage, electrodes = [], {}, {}
    pair_bits = {LINE: {}, PIXEL: {}}   # pid -> packed OR of its sources (shares the source array if only one)
    pair_check = {LINE: {}, PIXEL: {}}  # pid -> [first-source metadata, first compatibility error]
    source_bits = {}                    # computable source_key -> packed raw mask
    type_bits = {name: np.zeros_like(valid_bits) for name in ('lateral_short', 'vertical_short')}
    observed = missing = na = unmapped = rejected = computable = 0
    reasons = set()
    for case, head in entries:
        # Lazy records: exactly one source mask is alive at a time (the previous one is released on rebind).
        record = head if isinstance(head, MaskRecord) else case.records[head.diameter_um]
        measured = measure_full_mask(record, area_cache=area_cache)
        source_key = _key('source_case_v1', {'identity': asdict(case.identity), 'diameter_um': key.diameter_um})
        raw_record = _raw_record(case, key.diameter_um)
        raw = copy.deepcopy(case.raw) if raw_copies is None else raw_copies[id(case)]
        sources.append(SourceCaseArea(source_key, case.identity, case.endpoints, case.failure_type,
                                      case.pair_id, case.classification, key.diameter_um, record.input_status,
                                      measured, raw, copy.deepcopy(raw_record.get('applicability_evidence')),
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
                kind = case.classification
                bits = _pack(record.mask)
                source_bits[source_key] = bits
                checks = pair_check[kind].get(pid)
                if checks is None:
                    checks = pair_check[kind][pid] = [_UnionMeta(record.frame, record.analysis_layer,
                                                                 record.input_status, record.diameter_um,
                                                                 record.identity), None]
                    pair_bits[kind][pid] = bits
                else:
                    pair_bits[kind][pid] = pair_bits[kind][pid] | bits
                if checks[1] is None:
                    try:
                        check_union_compatible(checks[0], record)
                    except NumericContractError as error:
                        checks[1] = error
                type_bits[case.failure_type] |= bits & valid_bits
    record = None  # release the last loaded mask
    coverage = Coverage(len(entries), observed, missing, na, unmapped, rejected, computable,
                        size, int(np.count_nonzero(valid_flat)))
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

    for kind in (LINE, PIXEL):  # same precedence as the union validation of the eager implementation
        for checks in pair_check[kind].values():
            if checks[1] is not None:
                raise checks[1]
    combined = {}
    for kind in (LINE, PIXEL):
        bits = np.zeros_like(valid_bits)
        for pair_mask in pair_bits[kind].values():
            np.bitwise_or(bits, pair_mask, out=bits)
        combined[kind] = bits & valid_bits
    line = combined[LINE]
    effective = combined[PIXEL] & ~line
    transfer = combined[PIXEL] & line
    union = line | combined[PIXEL]
    budget = [0]

    def shares(kind, keep):
        mask_bits = pair_bits[kind]
        pair_ids = sorted(mask_bits)
        label, counts, ids = _partition_labels(
            pair_ids, lambda pid: np.flatnonzero(_unpack_flat(mask_bits[pid], size) & keep), size,
            live_limit=max_label_entries, on_oversize=lambda n: _oversized_label_patterns(key, n, max_label_entries))
        ordered = sorted(counts.items())
        if top_buckets is not None:
            # largest area first; the stable sort keeps the member-tuple order among ties
            chosen = sorted(ordered, key=lambda item: -item[1])[:top_buckets]
            wanted = np.array(sorted(ids[members] for members, _ in chosen), dtype=label.dtype)
        else:
            chosen = ordered
        touched = {}
        for pid in pair_ids:
            for source in lineage[pid]:
                if source in source_bits:
                    found = np.unique(label[_unpack_flat(source_bits[source], size)])
                    if top_buckets is not None:
                        found = np.intersect1d(found, wanted, assume_unique=True)
                    touched[source] = frozenset(found.tolist())
        built = {}
        for members, count in chosen:
            pair_lineage = tuple(sorted(source for pid in members for source in lineage[pid]))
            budget[0] += len(members) + len(pair_lineage)
            if budget[0] > max_bucket_entries:
                raise _oversized_buckets(key, budget[0], max_bucket_entries)
            label_id = ids[members]
            contributors = tuple(source for source in pair_lineage
                                 if source in touched and label_id in touched[source])
            budget[0] += len(contributors)
            if budget[0] > max_bucket_entries:
                raise _oversized_buckets(key, budget[0], max_bucket_entries)
            built[members] = Bucket(bucket_key(key, kind, members, pair_table_version=table_version),
                                    'exclusive' if len(members) == 1 else 'shared', members,
                                    metric(count * pixel_area), pair_lineage, contributors)
        area = metric(sum(counts.values()) * pixel_area)
        if top_buckets is None:
            exclusive = tuple(b for b in built.values() if b.kind == 'exclusive')
            shared = tuple(b for b in built.values() if b.kind == 'shared')
            return ClassShare(area, exclusive, shared)
        return ClassShareSummary(area, (), (), sum(1 for members, _ in ordered if len(members) == 1),
                                 sum(1 for members, _ in ordered if len(members) > 1),
                                 tuple(built[members] for members, _ in chosen))

    lateral, vertical = type_bits['lateral_short'], type_bits['vertical_short']
    piece_bits = {'lateral_only': lateral & ~vertical,
                  'vertical_only': vertical & ~lateral, 'simultaneous': lateral & vertical}
    packed = lambda bits: PackedMask(shape, bits)
    pieces = {name: replace(metric(packed(bits).count() * pixel_area),
                            basis='full_mask_valid_observed_sources_failure_type_union')
              for name, bits in piece_bits.items()}
    vertical_entries = [head for case, head in entries if case.failure_type == 'vertical_short']
    if vertical_entries and all(head.input_status == 'not_applicable' for head in vertical_entries):
        for name in ('vertical_only', 'simultaneous'):
            pieces[name] = replace(pieces[name], value=None, partial_value=None, status='not_applicable',
                                   reasons=('all_vertical_sources_not_applicable',))
    line_share = shares(LINE, valid_flat)
    pixel_share = shares(PIXEL, valid_flat & ~_unpack_flat(line, size))
    group = GroupArea(key, frame, line_share, pixel_share,
                      metric(packed(transfer).count() * pixel_area), metric(packed(union).count() * pixel_area),
                      tuple(sources), electrodes, packed(line), packed(effective), packed(transfer), packed(union),
                      table_version, pieces, {name: packed(bits) for name, bits in piece_bits.items()})
    group.fractions = {'line': fraction_of_frame(group.line.area, frame),
                       'pixel_effective': fraction_of_frame(group.pixel.area, frame),
                       'transfer': fraction_of_frame(group.transfer, frame),
                       'union': fraction_of_frame(group.union, frame),
                       **{name: fraction_of_frame(pieces[name], frame) for name in FAILURE_PIECES}}
    return group


def compute_areas(inputs: NumericDataset | Iterable[NumericCase], *, chunk_cells: int = 65536,
                  max_bucket_output_entries: int | None = None, bucket_detail: str | None = None,
                  top_buckets: int | None = None, max_label_pattern_entries: int | None = None) -> AreaResult:
    """Per-source-condition/layer/diameter full-mask areas with conservative coverage.

    Incomplete groups expose observed-source partial areas, not lower bounds:
    an unknown line mask could remove part of the currently effective pixel area.
    No aggregation across diameter, source conditions, products, density or units.

    chunk_cells is accepted and validated for API compatibility; membership labelling no longer chunks.
    max_bucket_output_entries bounds, per layer/diameter group, the bucket pair ids + lineage keys +
    contributor keys that are produced (error 'oversized_real_bucket_output'); None takes the value of the
    dataset's condition_assignment (real runs) or DEFAULT_MAX_BUCKET_OUTPUT_ENTRIES.
    bucket_detail='summary' still computes every area and the bucket counts, but builds lineage/contributors only for
    the top_buckets largest buckets per class (ClassShareSummary); the emitted-entry budget counts only those.
    The label partition stays exact; its live-pattern bound is the separate max_label_pattern_entries (error
    'oversized_real_label_patterns'; None takes the dataset's condition_assignment or DEFAULT_MAX_LABEL_PATTERN_ENTRIES).
    bucket_detail / top_buckets default to what the dataset was admitted for (a real configured_dataset records it in
    provenance['admitted_bucket_detail'] and priced its resource estimate with it); any other input defaults to
    'full' with DEFAULT_TOP_BUCKETS. An explicit mode that keeps more than the admitted one (full after a summary
    admission, or a larger top_buckets) is refused with 'bucket_detail_exceeds_admission'.
    Lazy records (adapters.real_hotspot.LazyRecords) are read by header; at most one source mask is loaded at a time.
    SourceCaseArea.raw is one deep copy per case shared by that case's diameters; treat it as read-only.
    """
    if isinstance(chunk_cells, bool) or not isinstance(chunk_cells, int) or chunk_cells <= 0:
        raise NumericContractError('invalid_membership_chunk_size')
    admitted = getattr(inputs, 'provenance', {}).get('admitted_bucket_detail')
    if bucket_detail is None:
        bucket_detail = admitted['bucket_detail'] if admitted else 'full'
    if top_buckets is None:
        top_buckets = admitted.get('top_buckets', DEFAULT_TOP_BUCKETS) if admitted else DEFAULT_TOP_BUCKETS
    if bucket_detail not in ('full', 'summary'):
        raise NumericContractError('invalid_bucket_detail', str(bucket_detail))
    if bucket_detail == 'summary' and (type(top_buckets) is not int or top_buckets <= 0):
        raise NumericContractError('invalid_top_buckets', 'top_buckets must be a positive integer')
    if admitted and admitted['bucket_detail'] == 'summary' and (
            bucket_detail == 'full' or (type(top_buckets) is int and top_buckets > admitted['top_buckets'])):
        raise NumericContractError('bucket_detail_exceeds_admission',
                                   f"admitted for summary top_buckets={admitted['top_buckets']}; asked for "
                                   + ('full detail' if bucket_detail == 'full' else f'top_buckets={top_buckets}')
                                   + ' (load the dataset with configured_dataset(..., top_buckets=None or larger))')
    if max_bucket_output_entries is None:
        configured = getattr(inputs, 'provenance', {}).get('configured_assignments') or {}
        max_bucket_output_entries = configured.get('max_bucket_output_entries', DEFAULT_MAX_BUCKET_OUTPUT_ENTRIES)
    if (isinstance(max_bucket_output_entries, bool) or not isinstance(max_bucket_output_entries, int)
            or max_bucket_output_entries <= 0):
        raise NumericContractError('invalid_resource_limit', 'max_bucket_output_entries must be a positive integer')
    if max_label_pattern_entries is None:
        configured = getattr(inputs, 'provenance', {}).get('configured_assignments') or {}
        max_label_pattern_entries = configured.get('max_label_pattern_entries', DEFAULT_MAX_LABEL_PATTERN_ENTRIES)
    if (isinstance(max_label_pattern_entries, bool) or not isinstance(max_label_pattern_entries, int)
            or max_label_pattern_entries <= 0):
        raise NumericContractError('invalid_resource_limit', 'max_label_pattern_entries must be a positive integer')
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
        for diameter, record in _record_heads(case):
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
    raw_copies = {id(case): copy.deepcopy(case.raw) for case in cases}
    return AreaResult({key: _group(key, grouped[key], chunk_cells, table_version, raw_copies=raw_copies,
                                   max_bucket_entries=max_bucket_output_entries,
                                   max_label_entries=max_label_pattern_entries,
                                   top_buckets=top_buckets if bucket_detail == 'summary' else None)
                       for key in sorted(grouped)},
                      copy.deepcopy(dataset.gates) if dataset else {}, provenance.get('input_hash'),
                      table_version, provenance.get('pair_table_hash'), bool(provenance.get('is_synthetic',False)))


# T4: explicit synthetic density + one-unit basis; G1/G2 real decisions remain pending.
@dataclass(frozen=True)
class UnitAreaBasis:
    basis: str
    is_synthetic: bool
    frame_id: str
    unit_size_um: tuple[float, float]
    evidence: str

    def __post_init__(self):
        text(self.basis,'area basis')
        text(self.frame_id,'basis frame_id')
        text(self.evidence,'basis evidence')
        if type(self.is_synthetic) is not bool:
            raise NumericContractError('invalid_synthetic_marker')
        _size_um(self.unit_size_um,'um')


def _size_um(values, unit):
    from .numeric_schema import convert_length
    if not isinstance(values,(tuple,list)) or len(values)!=2:
        raise NumericContractError('invalid_dimension_length')
    for value in values: finite(value,'dimension',positive=True)
    converted=convert_length(values,unit)
    return tuple(finite(v,'converted dimension',positive=True) for v in converted)


@dataclass(frozen=True)
class PanelSpec:
    panel_size: tuple[float,float]
    unit_size: tuple[float,float]
    length_unit: str = 'um'
    rounding: str = 'floor'

    def __post_init__(self):
        _size_um(self.panel_size,self.length_unit)
        _size_um(self.unit_size,self.length_unit)
        if self.rounding not in ('floor','half_up_round'):
            raise NumericContractError('invalid_panel_rounding_policy')

    @property
    def metadata(self):
        return {'policy':self.rounding,'unit_count':unit_count(self.panel_size,self.unit_size,
                    length_unit=self.length_unit,rounding=self.rounding),
                'panel_size_um':_size_um(self.panel_size,self.length_unit),
                'unit_size_um':_size_um(self.unit_size,self.length_unit),'length_unit':'um',
                'declared_panel_size':tuple(self.panel_size),'declared_unit_size':tuple(self.unit_size),
                'declared_length_unit':self.length_unit}


def unit_count(panel_size, unit_size, *, length_unit='um', rounding='floor') -> int:
    """Round each axis then multiply. Decimal preserves declared half-up semantics."""
    from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
    # 동일 선언 단위에서 비율을 먼저 구한다. um float 변환 오차를 floor에 전달하지 않는다.
    from .numeric_schema import convert_length
    convert_length(1,length_unit)  # 지원 단위 확인; 입력 치수는 변환하지 않는다.
    for values in (panel_size,unit_size):
        if not isinstance(values,(tuple,list)) or len(values)!=2:
            raise NumericContractError('invalid_dimension_length')
        for value in values:finite(value,'dimension',positive=True)
    panel,unit=panel_size,unit_size
    if rounding not in ('floor','half_up_round'):
        raise NumericContractError('invalid_panel_rounding_policy')
    mode=ROUND_FLOOR if rounding=='floor' else ROUND_HALF_UP
    counts=[int((Decimal(str(p))/Decimal(str(u))).to_integral_value(rounding=mode)) for p,u in zip(panel,unit)]
    return counts[0]*counts[1]


@dataclass(frozen=True,order=True)
class LayerKey:
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

    @property
    def comparison_key(self):
        return ComparisonKey(self.analysis_layer,self.process_step_id,self.source_id,self.exposure_id,
                             self.density_process_layer,self.source_contract_version)


@dataclass(frozen=True,order=True)
class ComparisonKey:
    analysis_layer: str
    process_step_id: str
    source_id: str
    exposure_id: str
    density_process_layer: str
    source_contract_version: str


@dataclass(frozen=True)
class WeightedMetric:
    value: float | None
    partial_value: float | None
    status: Status
    reasons: tuple[str,...]
    distribution_ids: tuple[str,...]
    area_basis: dict
    rounding: dict
    coverage: tuple[dict,...]
    unit: str = '1'  # area * inverse area; weighted index, never yield/probability


@dataclass(frozen=True)
class WeightedBucket:
    bucket_id: str
    kind: str
    pair_ids: tuple[str,...]
    source_bucket_ids: tuple[str,...]
    unit: WeightedMetric
    panel: WeightedMetric


@dataclass(frozen=True)
class WeightedClass:
    unit: WeightedMetric
    panel: WeightedMetric
    exclusive: tuple[WeightedBucket,...]
    shared: tuple[WeightedBucket,...]


@dataclass(frozen=True)
class WeightedPiece:
    unit: WeightedMetric
    panel: WeightedMetric


@dataclass
class LayerRate:
    key: LayerKey
    line: WeightedClass
    pixel: WeightedClass
    transfer_unit: WeightedMetric
    transfer_panel: WeightedMetric
    distribution_contract: dict | None
    distribution_fingerprint: str | None
    failure_pieces: dict[str, WeightedPiece]


@dataclass
class RateResult:
    areas: AreaResult
    layers: dict[LayerKey,LayerRate]
    synthetic_contract_reasons: dict[str,tuple[str,...]]
    panels: dict[str,PanelSpec]
    densities: dict

    def to_dict(self):
        return {'schema_version':'linepixel_weighted_v1','gates':current_gate_decisions() if self.areas.gates else {},
                'areas':self.areas.to_dict(),'layers':[asdict(self.layers[k]) for k in sorted(self.layers)],
                'synthetic_contract_reasons':self.synthetic_contract_reasons,'metric_semantics':'weighted_short_index',
                'density_distributions':{name:dist.to_dict() for name,dist in sorted(self.densities.items())}}


def _weighted_layer(key, groups, distribution, basis, panel):
    groups=sorted(groups,key=lambda g:g.key.diameter_um)
    declared_basis={} if basis is None else asdict(basis)
    rounding={'policy':None,'unit_count':None} if panel is None else panel.metadata
    blockers=set(() if distribution is not None else ('density_contract_unconfirmed','missing_layer_density_distribution'))
    if distribution is not None:
        blockers.update(distribution.reasons)
        if distribution.process_layer!=key.density_process_layer:
            blockers.add('density_process_layer_mismatch')
    frame=groups[0].frame
    if basis is None or not basis.is_synthetic or basis.basis!='one_unit':
        blockers.add('unit_area_basis_unconfirmed')
    elif (basis.frame_id!=frame.frame_id or tuple(basis.unit_size_um)!=
          (frame.shape_yx[1]*frame.pixel_pitch_um,frame.shape_yx[0]*frame.pixel_pitch_um)):
        blockers.add('one_unit_domain_mismatch')
    if panel is not None and basis is not None and _size_um(panel.unit_size,panel.length_unit)!=tuple(basis.unit_size_um):
        blockers.add('panel_unit_basis_mismatch')
    coverage=[]; contributions={}; bucket_amounts={LINE:{},PIXEL:{}}; bucket_sources={LINE:{},PIXEL:{}}
    known=0; complete=True; reasons=set(blockers)
    for group in groups:
        d=group.key.diameter_um
        error=set(group.union.reasons)
        if group.union.status=='not_applicable':
            coverage.append({'diameter_um':d,'status':'not_applicable','source':asdict(group.coverage)})
            contributions[d]={LINE:0.0,PIXEL:0.0,'transfer':0.0,**{name:0.0 for name in FAILURE_PIECES}}
            continue
        density_bin=None if distribution is None else distribution.bins[d]
        if density_bin is not None:error.update(density_bin.reasons)
        if group.union.status!='available':complete=False
        if group.union.status not in ('available','partial') or density_bin is None or density_bin.value_per_um2 is None or blockers:
            complete=False
            error.update(blockers)
            reasons.update(error)
            coverage.append({'diameter_um':d,'status':'unavailable','reasons':sorted(error),'source':asdict(group.coverage),
                             'density_per_um2':None if density_bin is None else density_bin.value_per_um2})
            continue
        factor=density_bin.value_per_um2
        amounts={LINE:group.line.area.observed_area_um2*factor,PIXEL:group.pixel.area.observed_area_um2*factor,
                 'transfer':group.transfer.observed_area_um2*factor,
                 **{name:group.failure_pieces[name].observed_area_um2*factor for name in FAILURE_PIECES}}
        for value in amounts.values():finite(value,'weighted value',nonnegative=True)
        contributions[d]=amounts;known+=1
        reasons.update(error)
        coverage.append({'diameter_um':d,'status':group.union.status,'reasons':sorted(error),'source':asdict(group.coverage),
                         'density_per_um2':factor,'distribution_id':distribution.distribution_id})
        for kind,share in [(LINE,group.line),(PIXEL,group.pixel)]:
            for bucket in (*share.exclusive,*share.shared):
                members=bucket.pair_ids
                bucket_amounts[kind][members]=bucket_amounts[kind].get(members,0.0)+bucket.area.observed_area_um2*factor
                bucket_sources[kind].setdefault(members,[]).append(bucket.bucket_id)
    if len(groups)!=len(DIAMETERS_UM) or {g.key.diameter_um for g in groups}!=set(DIAMETERS_UM):
        complete=False;reasons.add('diameter_coverage_incomplete')
    status='available' if complete and not blockers else 'partial' if known and not blockers else 'unavailable'
    distribution_ids=() if distribution is None else (distribution.distribution_id,)
    coverage=tuple(coverage)

    def metric(amount,scale=1,panel_metric=False):
        errors=set(reasons)
        state=status
        if panel_metric and panel is None:
            errors.add('panel_dimensions_missing');state='unavailable'
        if panel_metric and panel is not None and rounding['unit_count']==0:
            errors.add('panel_has_no_units');state='unavailable'
        number=finite(amount*scale,'weighted aggregate',nonnegative=True)
        partial=number if known and not blockers and state!='available' and not (panel_metric and (panel is None or rounding['unit_count']==0)) else None
        return WeightedMetric(number if state=='available' else None,partial,state,tuple(sorted(errors)),
                              distribution_ids,declared_basis,rounding,coverage)

    count=1 if panel is None else rounding['unit_count']
    def share(kind):
        total=sum(values[kind] for values in contributions.values())
        exclusive=[];shared=[]
        for members,amount in sorted(bucket_amounts[kind].items()):
            bucket=WeightedBucket(_key('weighted_bucket_v1',{'layer':asdict(key),'class':kind,'pair_ids':members,
                                              'pair_table_version':groups[0].pair_table_version}),
                                  'exclusive' if len(members)==1 else 'shared',members,
                                  tuple(sorted(bucket_sources[kind][members])),metric(amount),metric(amount,count,True))
            (exclusive if len(members)==1 else shared).append(bucket)
        return WeightedClass(metric(total),metric(total,count,True),tuple(exclusive),tuple(shared))
    transfer=sum(values['transfer'] for values in contributions.values())
    pieces={name:WeightedPiece(metric(sum(values[name] for values in contributions.values())),
                              metric(sum(values[name] for values in contributions.values()),count,True))
            for name in FAILURE_PIECES}
    return LayerRate(key,share(LINE),share(PIXEL),metric(transfer),metric(transfer,count,True),
                     None if distribution is None or distribution.contract is None else asdict(distribution.contract),
                     None if distribution is None else _key('density_v1',distribution.to_dict()),pieces),tuple(sorted(blockers))


def compute_rates(areas: AreaResult, densities, *, unit_bases, panels=None) -> RateResult:
    """Sum diameters per layer/source condition only under explicit synthetic contracts.

    Densities are keyed by density_process_layer, bases by (product_id,frame_id),
    panels by product_id. T2 raw rows may be passed through adapt_density with no
    contract to produce blocked results. G1/G2 answers are never manufactured.
    """
    from .density import DensityDistribution
    if not isinstance(areas,AreaResult):raise NumericContractError('expected_area_result')
    grouped={}
    for group in areas.groups.values():
        data=asdict(group.key);data.pop('diameter_um');key=LayerKey(**data)
        grouped.setdefault(key,[]).append(group)
    result={};contract_reasons={};panels={} if panels is None else dict(panels)
    for key,groups in sorted(grouped.items()):
        distribution=densities.get(key.density_process_layer)
        if distribution is not None and not isinstance(distribution,DensityDistribution):
            raise NumericContractError('expected_density_distribution')
        basis=unit_bases.get((key.product_id,key.frame_id));panel=panels.get(key.product_id)
        if basis is not None and not isinstance(basis,UnitAreaBasis):raise NumericContractError('expected_unit_area_basis')
        if panel is not None and not isinstance(panel,PanelSpec):raise NumericContractError('expected_panel_spec')
        result[key],errors=_weighted_layer(key,groups,distribution,basis,panel)
        contract_reasons.setdefault(key.product_id,set()).update(errors)
    return RateResult(areas,result,{p:tuple(sorted(errors)) for p,errors in contract_reasons.items()},panels,
                      {name:dist for name,dist in densities.items() if isinstance(dist,DensityDistribution)})


@dataclass(frozen=True)
class LayerDelta:
    key: ComparisonKey
    line: WeightedMetric
    pixel: WeightedMetric
    transfer: WeightedMetric
    line_exclusive: dict[str,WeightedMetric]
    line_shared: dict[str,WeightedMetric]
    pixel_exclusive: dict[str,WeightedMetric]
    pixel_shared: dict[str,WeightedMetric]
    reference_product_id: str
    target_product_id: str
    comparison_basis: str
    failure_pieces: dict[str, WeightedMetric]

    def to_dict(self):return asdict(self)


def compare_rates(reference: RateResult,target: RateResult,*,reference_product_id: str,target_product_id: str,
                  reference_absent=None,target_absent=None) -> dict[ComparisonKey,LayerDelta]:
    """Panel target minus reference. Absence requires explicit observed evidence.

    Partial sums are preserved on each side in coverage, never subtracted into
    an improvement claim. Shared buckets are joined by the entire pair-ID tuple,
    not the product-scoped bucket hash, and are never allocated to individual pairs.
    """
    def index(result,product):
        out={}
        for key,layer in result.layers.items():
            if key.product_id==product:
                if key.comparison_key in out:raise NumericContractError('ambiguous_comparison_layer')
                out[key.comparison_key]=layer
        return out
    ra,ta=index(reference,reference_product_id),index(target,target_product_id)
    reference_absent={} if reference_absent is None else reference_absent
    target_absent={} if target_absent is None else target_absent
    for absent in (reference_absent,target_absent):
        for evidence in absent.values():text(evidence,'observed absence evidence')
    if set(ra)&set(reference_absent) or set(ta)&set(target_absent):raise NumericContractError('conflicting_absence_evidence')
    output={}
    for key in sorted(set(ra)|set(ta)):
        a,b=ra.get(key),ta.get(key)
        compatibility=set()
        if reference.areas.pair_table_version is None or target.areas.pair_table_version is None:
            compatibility.add('pair_table_version_unconfirmed')
        if reference.areas.pair_table_version!=target.areas.pair_table_version:
            compatibility.add('pair_table_version_mismatch')
        if a and b and a.distribution_contract and b.distribution_contract:
            for field in ('bin_semantics','basis_version'):
                if a.distribution_contract[field]!=b.distribution_contract[field]:compatibility.add('density_basis_mismatch')
        if a and b:
            def pair_definitions(result,layer):
                return {pid:electrodes for g in result.areas.groups.values()
                        if all(getattr(g.key,n)==v for n,v in asdict(layer.key).items())
                        for pid,electrodes in g.pair_membership.items()}
            ap,bp=pair_definitions(reference,a),pair_definitions(target,b)
            if any(ap[pid]!=bp[pid] for pid in set(ap)&set(bp)):
                compatibility.add('pair_identity_mismatch')
        same_density=a is not None and b is not None and a.distribution_fingerprint==b.distribution_fingerprint
        comparison_basis='same_density_basis' if same_density else 'different_or_absent_density_distributions'

        def delta(am,bm,members=None):
            from dataclasses import replace
            # An absent bucket uses its complete class observation metadata, not missing metadata.
            if members is not None:
                if am is None and a is not None:
                    cls=a.line if members[0]==LINE else a.pixel
                    if cls.panel.status=='available':am=replace(cls.panel,value=0.0,partial_value=None)
                if bm is None and b is not None:
                    cls=b.line if members[0]==LINE else b.pixel
                    if cls.panel.status=='available':bm=replace(cls.panel,value=0.0,partial_value=None)
            def absent_zero(result,product,evidence):
                metadata=result.panels[product].metadata
                empty=metadata['unit_count']==0
                return WeightedMetric(None if empty else 0.0,None,'unavailable' if empty else 'available',
                    ('panel_has_no_units',) if empty else (),(),
                    {'basis':'observed_absent','evidence':evidence},metadata,())
            if a is None and key in reference_absent and not reference.synthetic_contract_reasons.get(reference_product_id,('unconfirmed',)) and reference_product_id in reference.panels:
                am=absent_zero(reference,reference_product_id,reference_absent[key])
            if b is None and key in target_absent and not target.synthetic_contract_reasons.get(target_product_id,('unconfirmed',)) and target_product_id in target.panels:
                bm=absent_zero(target,target_product_id,target_absent[key])
            errors=set(compatibility);av=None if am is None else am.value;bv=None if bm is None else bm.value
            for value in (am,bm):
                if value is not None and value.status!='available':errors.update(value.reasons);errors.add('incomplete_comparison')
            for side,metric,layer,absent,result,product in [('reference',am,a,reference_absent,reference,reference_product_id),
                                                         ('target',bm,b,target_absent,target,target_product_id)]:
                if layer is None:
                    auth=result.synthetic_contract_reasons.get(product,('comparison_product_unconfirmed',))
                    if key not in absent or auth or product not in result.panels:
                        errors.update(auth);errors.add(side+'_layer_unknown')
                    elif side=='reference':av=0.0
                    else:bv=0.0
                elif metric is None:
                    # Bucket absence is observed only when both class totals are complete.
                    cls=layer.line if members[0]==LINE else layer.pixel
                    if cls.panel.status=='available':
                        if side=='reference':av=0.0
                        else:bv=0.0
                    else:errors.update(cls.panel.reasons);errors.add('bucket_coverage_incomplete')
            if av is None or bv is None:errors.add('incomplete_comparison')
            complete=not errors
            value=finite(bv-av,'delta') if complete else None
            distributions=tuple(sorted(set(pid for metric in (am,bm) if metric for pid in metric.distribution_ids)))
            basis={'reference':None if am is None else am.area_basis,'target':None if bm is None else bm.area_basis}
            rounding={'reference':None if am is None else am.rounding,'target':None if bm is None else bm.rounding}
            coverage=tuple({'side':side,'status':None if metric is None else metric.status,
                            'partial_value':None if metric is None else metric.partial_value,
                            'bins':() if metric is None else metric.coverage,
                            'observed_absence':absent.get(key)} for side,metric,absent in
                           [('reference',am,reference_absent),('target',bm,target_absent)])
            return WeightedMetric(value,None,'available' if complete else 'unavailable',tuple(sorted(errors)),distributions,basis,rounding,coverage)

        def buckets(kind,category):
            amap={} if a is None else {x.pair_ids:x.panel for x in getattr(getattr(a,kind),category)}
            bmap={} if b is None else {x.pair_ids:x.panel for x in getattr(getattr(b,kind),category)}
            return {_json(members):delta(amap.get(members),bmap.get(members),(kind,members)) for members in sorted(set(amap)|set(bmap))}
        output[key]=LayerDelta(key,delta(None if a is None else a.line.panel,None if b is None else b.line.panel),
                              delta(None if a is None else a.pixel.panel,None if b is None else b.pixel.panel),
                              delta(None if a is None else a.transfer_panel,None if b is None else b.transfer_panel),
                              buckets(LINE,'exclusive'),buckets(LINE,'shared'),buckets(PIXEL,'exclusive'),buckets(PIXEL,'shared'),
                              reference_product_id,target_product_id,comparison_basis,
                              {name:delta(None if a is None else a.failure_pieces[name].panel,
                                          None if b is None else b.failure_pieces[name].panel)
                               for name in FAILURE_PIECES})
    return output


@dataclass(frozen=True)
class FractionDelta:
    """target − reference of fraction_of_frame per comparison layer and diameter.

    Complete only when both sides are available; a layer missing on one side is
    unknown, never zero. No density, unit or panel scaling is involved.
    """
    value: float | None
    status: Status
    reasons: tuple[str, ...]
    reference_value: float | None
    target_value: float | None
    frame_area_um2: dict
    unit: str = '1'
    direction: str = 'target_minus_reference'


def compare_fractions(reference: AreaResult, target: AreaResult) -> dict:
    """{(ComparisonKey, diameter_um): {name: FractionDelta}} for every layer/diameter seen on either side."""
    if not isinstance(reference, AreaResult) or not isinstance(target, AreaResult):
        raise NumericContractError('expected_area_result')

    def index(result):
        out = {}
        for key, group in result.groups.items():
            data = asdict(key)
            diameter = data.pop('diameter_um')
            comparison = (LayerKey(**data).comparison_key, diameter)
            if comparison in out:
                raise NumericContractError('ambiguous_comparison_layer')
            out[comparison] = group
        return out
    ra, ta = index(reference), index(target)
    output = {}
    for key in sorted(set(ra) | set(ta)):
        a, b = ra.get(key), ta.get(key)
        metrics = {}
        for name in FRACTION_NAMES:
            am = None if a is None else a.fractions[name]
            bm = None if b is None else b.fractions[name]
            reasons = set()
            if am is None:
                reasons.add('reference_layer_unknown')
            if bm is None:
                reasons.add('target_layer_unknown')
            for side, metric in (('reference', am), ('target', bm)):
                if metric is not None and metric.status != 'available':
                    reasons.add(f'{side}_{metric.status}')
                    reasons.update(f'{side}:{r}' for r in metric.reasons)
            if am is not None and bm is not None and am.status == bm.status == 'not_applicable':
                status = 'not_applicable'
            elif reasons:
                status = 'unavailable'
            else:
                status = 'available'
            value = finite(bm.value - am.value, 'fraction delta') if status == 'available' else None
            metrics[name] = FractionDelta(value, status, tuple(sorted(reasons)),
                                          None if am is None else am.value, None if bm is None else bm.value,
                                          {'reference': None if am is None else am.frame_area_um2,
                                           'target': None if bm is None else bm.frame_area_um2})
        output[key] = metrics
    return output

