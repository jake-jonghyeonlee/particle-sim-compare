"""numeric_v2 full-mask line/pixel unions and exact pair membership areas.

T3 area/membership is preserved independently from T4 synthetic weighting and
panel comparison. No default mask divisor or yield conversion is applied. G7a failure pieces are a separate axis of the same mapped-short union;
they do not change line/pixel priority, pair buckets or transfer.
"""
from __future__ import annotations
from .numeric_schema import current_gate_decisions

from collections import Counter
from dataclasses import asdict, dataclass, replace
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
FAILURE_PIECES = ('lateral_only', 'vertical_only', 'simultaneous')
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
            'bucket_source_relationship': {'pair_lineage_case_keys':'pair_lineage',
                                           'contributing_source_case_keys':'observed_mask_intersects_bucket_at_diameter'},
            'failure_pieces': {name:asdict(metric) for name,metric in self.failure_pieces.items()},
            'failure_fragment_policy': copy.deepcopy(FAILURE_PIECE_POLICY),
        }


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
    return GroupArea(key, frame, shares(LINE, frame.valid), shares(PIXEL, frame.valid & ~line),
                     metric(int(transfer.sum()) * pixel_area), metric(int(union.sum()) * pixel_area),
                     tuple(sources), electrodes, line, effective, transfer, union, table_version, pieces, piece_masks)


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
