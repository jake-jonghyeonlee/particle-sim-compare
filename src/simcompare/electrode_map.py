"""Full-mask 전극 수 지도와 검토 영역. 확률·온셋·단독 원인 지도가 아니다.

canonical 경로는 G7b 사용자 결정(2026-10-08)에 따라 short-only이며 기본은 영역 선택 없는 지도다.
DefectHotspot 경로는 기존 open(전극 하나) 의미를 보존하는 호환 API다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import hashlib
import json
from typing import Iterable

import numpy as np

from .measurement import measure_full_mask, measure_union
from .numeric_schema import (CaseIdentity, Frame, Measurement, NumericCase, NumericDataset,
    NumericContractError, NumericFrameError, PhysicalWindow, binary_mask,
    diameter_key, finite, require_same_frame, text)

DEFAULT_DIAMETER_UM = 5.0
SCOPE_DECISION = {'status': 'answered', 'value': 'short_only',
                  'authority': 'user', 'decided_on': '2026-10-08'}


def _threshold(value):
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < 1:
        raise NumericContractError('invalid_count_threshold')
    return int(value)


def _run_counts(active):
    """Horizontal runs of True per row (int64, one entry per row); O(width) scratch, nothing per cell."""
    counts = np.zeros(active.shape[0], np.int64)
    for r in range(active.shape[0]):
        row = active[r]
        counts[r] = int(row[0]) + int(np.count_nonzero(row[1:] > row[:-1])) if row.size else 0
    return counts


def _label_runs(active, counts):
    """4-neighbour connected components by run-length union-find: (rows, starts, ends, root), each int32 per run.

    Memory is 16 B per run (a run is a maximal horizontal stretch of True, so runs <= cells/2) plus O(width)
    per row and one temporary the size of the root array while the roots are flattened; no per-cell Python
    object is created. Runs are numbered row-major and a root is always the smallest run id of its component,
    so ascending roots enumerate the components in the row-major order of their first cell.
    """
    total = int(counts.sum())
    rows = np.empty(total, np.int32)
    starts = np.empty(total, np.int32)
    ends = np.empty(total, np.int32)
    parent = np.arange(total, dtype=np.int32)
    link = memoryview(parent)

    def find(x):
        while link[x] != x:
            link[x] = link[link[x]]
            x = link[x]
        return x

    offset, prev_s, prev_e, prev_first = 0, [], [], 0
    for r in range(active.shape[0]):
        edge = np.diff(active[r].view(np.int8), prepend=np.int8(0), append=np.int8(0))
        s, e = np.flatnonzero(edge == 1), np.flatnonzero(edge == -1)
        n = s.size
        rows[offset:offset+n] = r
        starts[offset:offset+n] = s
        ends[offset:offset+n] = e
        cur_s, cur_e = s.tolist(), e.tolist()
        i = j = 0
        while i < n and j < len(prev_s):
            if cur_s[i] < prev_e[j] and prev_s[j] < cur_e[i]:  # the two runs share a column
                a, b = find(offset+i), find(prev_first+j)
                if a != b:
                    if a < b:
                        link[b] = a
                    else:
                        link[a] = b
            if cur_e[i] < prev_e[j]:
                i += 1
            else:
                j += 1
        prev_s, prev_e, prev_first, offset = cur_s, cur_e, offset, offset+n
    del link
    while total:  # every parent id is smaller than its child, so pointer doubling terminates in log steps
        flat = parent[parent]
        if np.array_equal(flat, parent):
            break
        parent = flat
    return rows, starts, ends, parent


def _components(active, labelled=None):
    """행 우선의 안정적인 4-neighbour 연결 성분. scipy 의존성 없음.

    labelled = (_label_runs(...) result) when the caller already counted the components; each component is
    then built from its own runs, so only that component's full-frame mask is allocated.
    """
    rows, starts, ends, root = labelled if labelled is not None else _label_runs(active, _run_counts(active))
    if not root.size:
        return
    order = np.argsort(root, kind='stable')
    first = np.flatnonzero(np.diff(root[order])) + 1
    bounds = zip(np.r_[0, first].tolist(), np.r_[first, order.size].tolist())
    for lo, hi in bounds:
        members = order[lo:hi]
        inside = np.zeros(active.shape, bool)
        for k in members.tolist():
            inside[rows[k], starts[k]:ends[k]] = True
        yield inside, (int(rows[members].min()), int(starts[members].min()),
                       int(rows[members].max())+1, int(ends[members].max()))


def _component_sizes(active, labelled=None):
    """Cell counts (int64 array, one per component, in _components order) without allocating any full-frame mask."""
    rows, starts, ends, root = labelled if labelled is not None else _label_runs(active, _run_counts(active))
    if not root.size:
        return np.zeros(0, np.int64)
    sizes = np.bincount(root, weights=(ends - starts))
    return sizes[(root == np.arange(root.size, dtype=np.int32))[:sizes.size]].astype(np.int64)


def _component_run_counts(labelled):
    """Horizontal runs per component (int64 array, same order as _component_sizes); no full-frame mask."""
    root = labelled[3]
    if not root.size:
        return np.zeros(0, np.int64)
    runs = np.bincount(root)
    return runs[(root == np.arange(root.size, dtype=np.int32))[:runs.size]]


@dataclass(frozen=True)
class DefectHotspot:
    """호환 입력: open은 전극 하나, short는 전극 둘. 물리 frame은 제공하지 않음."""
    layer: str
    electrodes: tuple[str, ...]
    diameter_um: float
    mask: np.ndarray

    def __post_init__(self):
        text(self.layer, 'layer')
        finite(self.diameter_um, 'diameter_um', positive=True)
        if not self.electrodes:
            raise NumericContractError('empty_electrodes')
        for name in self.electrodes:
            text(name, 'electrode_id')
        object.__setattr__(self, 'mask', binary_mask(self.mask))


@dataclass(frozen=True)
class Region:
    layer: str
    label: int
    max_count: int
    area_px: int
    bbox: tuple[int, int, int, int]  # row0,col0,row1,col1 끝 제외
    electrodes: tuple[str, ...]


def electrode_count_maps(hotspots: Iterable[DefectHotspot], diameter_um=DEFAULT_DIAMETER_UM):
    """기존 frame 없는 입력의 layer별 distinct electrode 지도(open 의미 보존)."""
    diameter_key(diameter_um)
    per_layer = {}
    shape = None
    for h in hotspots:
        if diameter_key(h.diameter_um) != diameter_key(diameter_um):
            continue
        if shape is not None and h.mask.shape != shape:
            raise ValueError('hotspot 격자가 다르다')
        shape = h.mask.shape
        touched = per_layer.setdefault(h.layer, {})
        for name in h.electrodes:
            touched[name] = touched.get(name, np.zeros(shape, bool)) | h.mask
    return {layer: np.sum(list(names.values()), axis=0, dtype=np.int64)
            for layer, names in per_layer.items()}


def summary_map(maps):
    """호환 API: 물리 frame 호환성은 canonical compute_electrode_maps에서 검사."""
    if not maps:
        raise ValueError('대표 지름의 hotspot이 없다')
    return np.max(np.stack(list(maps.values())), axis=0)


def find_regions(hotspots, min_count=3, diameter_um=DEFAULT_DIAMETER_UM):
    threshold = _threshold(min_count)
    spots = list(hotspots)
    regions = []
    for layer, counts in electrode_count_maps(spots, diameter_um).items():
        for label, (inside, bbox) in enumerate(_components(counts >= threshold), 1):
            names = sorted({e for h in spots if h.layer == layer
                            and diameter_key(h.diameter_um) == diameter_key(diameter_um)
                            and (h.mask & inside).any() for e in h.electrodes})
            regions.append(Region(layer, label, int(counts[inside].max()),
                                  int(inside.sum()), bbox, tuple(names)))
    return sorted(regions, key=lambda r: (-r.max_count, -r.area_px, r.layer, r.label))


@dataclass(frozen=True, order=True)
class MapKey:
    product_id: str
    design_version: str
    source_id: str
    process_step_id: str
    exposure_id: str
    source_contract_version: str
    unit_id: str
    layer: str

    @property
    def summary_key(self):
        return self.product_id, self.design_version, self.unit_id


@dataclass(frozen=True)
class CaseLink:
    identity: dict
    failure_type: str
    endpoints: tuple[dict, ...]
    original_case_ca: Measurement
    case_ca_in_region: Measurement
    raw: dict
    source_metadata: dict


@dataclass(frozen=True, eq=False)
class ElectrodeRegion:
    region_id: str
    key: MapKey
    max_count: int
    area_px: int
    area_um2: float
    bbox_yx: tuple[int, int, int, int]
    region_bbox_um: tuple[float, float, float, float]
    center_xy_um: tuple[float, float]
    frame: Frame
    mask: np.ndarray
    electrodes: tuple[str, ...]
    cases: tuple[CaseLink, ...]
    coverage: dict
    provenance: dict
    review_roi_status: str = 'context_required'
    review_roi_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, eq=False)
class LayerMap:
    key: MapKey
    frame: Frame
    counts: np.ndarray | None
    status: str
    reasons: tuple[str, ...]
    coverage: dict
    regions: tuple[ElectrodeRegion, ...]


@dataclass(frozen=True, eq=False)
class ProductMap:
    key: tuple[str, ...]
    frame: Frame | None
    counts: np.ndarray | None
    status: str
    reasons: tuple[str, ...]
    coverage: dict
    provenance: dict


@dataclass(frozen=True)
class ElectrodeMaps:
    layers: tuple[LayerMap, ...]
    summaries: tuple[ProductMap, ...]
    representative_diameter_um: float
    provenance: dict

    def to_dict(self, *, include_arrays=True):
        """JSON용 수치/ROI provenance; 원본 layout asset 연결은 T6가 확인한다."""
        def encode(value):
            if isinstance(value, Frame):
                return {'frame_id': value.frame_id, 'length_unit': 'um',
                        'pixel_pitch_um': value.pixel_pitch_um, 'origin_xy_um': list(value.origin_xy_um),
                        'shape_yx': list(value.shape_yx), 'axis_order': value.axis_order,
                        'origin_convention': value.origin_convention, 'valid_hash': value.valid_hash,
                        'valid': value.valid.tolist() if include_arrays else None}
            if isinstance(value, np.ndarray):
                return value.tolist() if include_arrays else None
            if isinstance(value, np.generic):
                return value.item()
            if hasattr(value, '__dataclass_fields__'):
                return {name: encode(getattr(value, name)) for name in value.__dataclass_fields__}
            if isinstance(value, dict):
                return {name: encode(item) for name, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [encode(item) for item in value]
            return value
        return encode(self)


def _validate_case(case):
    text(case.analysis_layer, 'analysis_layer')
    if not case.endpoints:
        raise NumericContractError('empty_electrodes')
    for endpoint in case.endpoints:
        text(endpoint.electrode_id, 'electrode_id')
    keys = set()
    for diameter, record in case.records.items():
        key = diameter_key(diameter)
        if key in keys:
            raise NumericContractError('duplicate_case_diameter')
        keys.add(key)
        if key != diameter_key(record.diameter_um):
            raise NumericContractError('record_diameter_mismatch')
        if record.identity != case.identity or record.analysis_layer != case.analysis_layer:
            raise NumericContractError('record_case_binding_mismatch')
        if record.frame.frame_id != case.identity.frame_id:
            raise NumericFrameError('record_identity_frame_mismatch')
        if record.input_status == 'observed':
            binary_mask(record.mask)
        if record.input_status == 'not_applicable':
            height = case.metadata.get('height_um')
            evidence = case.metadata.get('height_evidence')
            raw = next((r for r in case.metadata.get('records', [])
                        if (diameter_key(r['diameter_um']) if 'diameter_um' in r else
                            diameter_key(r['diameter'],r['diameter_unit'])) == diameter_key(diameter)), {})
            applicability = raw.get('applicability_evidence', {})
            if (case.failure_type != 'vertical_short' or not isinstance(evidence, str)
                    or not evidence.strip() or not isinstance(applicability, dict)
                    or not isinstance(applicability.get('source'), str)
                    or not applicability['source'].strip()
                    or applicability.get('height_um') != height
                    or applicability.get('comparison') != 'diameter_um < height_um'
                    or 'below_height_vertical_short' not in record.reasons):
                raise NumericContractError('not_applicable_evidence_required')
            if not diameter < finite(height, 'height_um', positive=True) < 5:
                raise NumericContractError('invalid_not_applicable_height')


def _key(case):
    i = case.identity
    return MapKey(i.product_id, i.design_version, i.source_id, i.process_step_id,
                  i.exposure_id, i.source_contract_version, case.metadata.get('unit_id', ''),
                  case.analysis_layer)


def _region_windows(inside, frame):
    # 각 행의 연속 셀을 합쳐 정확한 성분을 유지하며 T2 창 수를 줄인다.
    x, y = frame.origin_xy_um
    pitch = frame.pixel_pitch_um
    windows = []
    for r, row in enumerate(inside):
        edges = np.diff(np.r_[False, row, False].astype(np.int8))
        for c0, c1 in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            windows.append(PhysicalWindow(x+int(c0)*pitch, y+r*pitch,
                                          x+int(c1)*pitch, y+(r+1)*pitch))
    return windows


def compute_electrode_maps(inputs: NumericDataset | Iterable[NumericCase], *,
                           diameter_um=DEFAULT_DIAMETER_UM, min_count=None) -> ElectrodeMaps:
    """T2 원본 binary/valid에서 계산. pair 표·density·unit divisor를 사용하지 않는다.

    counts는 관측된 short의 하한이며 coverage와 scope를 함께 읽어야 한다.
    source/process/exposure별 layer 지도를 보존하며 product/design/unit 요약은 최대값이다.
    """
    diameter = float(diameter_key(diameter_um))
    threshold = None if min_count is None else _threshold(min_count)
    dataset = inputs if isinstance(inputs, NumericDataset) else None
    cases = list(dataset.cases if dataset else inputs)
    if dataset is not None:
        from .adapters.real_hotspot import guard_stage_map
        guard_stage_map('electrode-map', dataset)  # Phase A: fail fast before per-electrode masks are allocated
    provenance = {'domain': 'full_mask_valid', 'defect_scope': 'short_only',
                  'scope_status': 'confirmed', 'count_semantics': 'observed_short_lower_bound',
                  'G7b': copy.deepcopy(SCOPE_DECISION),
                  'representative_diameter_um': diameter, 'threshold': threshold,
                  'connectivity': 4 if threshold is not None else None,
                  'regions_requested': threshold is not None,
                  'selection_policy_version': 'electrode_count_region_v1' if threshold is not None else None,
                  'source_manifest_hash': dataset.provenance.get('input_hash') if dataset else None,
                  'real_coverage_status': 'pending', 'real_coverage_reason': 'unit_area_basis_unconfirmed'}
    grouped, seen, excluded = {}, set(), []
    for case in cases:
        _validate_case(case)
        identity = tuple(asdict(case.identity).values())
        if identity in seen:
            raise NumericContractError('duplicate_source_case')
        seen.add(identity)
        if case.failure_type not in ('lateral_short', 'vertical_short'):
            raise NumericContractError('map_requires_short_only', case.failure_type)
        grouped.setdefault(_key(case), []).append(case)
    provenance['excluded_cases'] = sorted(excluded, key=lambda r: json.dumps(r, sort_keys=True))
    # Dataset manifest에 있는 case가 사라졌으면 전체 완전성도 보류한다.
    missing_manifest = []
    if dataset:
        identity_fields = tuple(CaseIdentity.__dataclass_fields__)
        actual = {tuple(getattr(c.identity, k) for k in identity_fields) for c in cases}
        for raw in dataset.provenance.get('cases', []):
            identity = tuple(raw.get(k) for k in identity_fields)
            if identity not in actual:
                missing_manifest.append(identity)
    provenance['missing_manifest_cases'] = missing_manifest
    layers = []
    region_total = 0
    for key, entries in sorted(grouped.items()):
        frames = [r.frame for c in entries for r in c.records.values()]
        if not frames:
            # frame 없이 위치를 추정하지 않는다.
            raise NumericFrameError('source_frame_required')
        frame = frames[0]
        for other in frames[1:]:
            require_same_frame(frame, other)
        coverage = {'observed': [], 'missing': [], 'not_applicable': [], 'measurement_errors': [],
                    'source_coverage_status': 'available', 'valid_area_um2': float(frame.valid.sum())*frame.pixel_pitch_um**2,
                    'full_area_um2': frame.valid.size*frame.pixel_pitch_um**2,
                    'source_coverage_summary': {'expected_case_inventory_status':
                        'unconfirmed' if any('expected_case_inventory_unprovided' in c.reasons for c in entries)
                        else 'confirmed'},
                    'valid_fraction': float(frame.valid.mean())}
        touched, observed = {}, []
        reasons = set()
        for case in sorted(entries, key=lambda c: c.identity.case_id):
            reasons.update(case.reasons)
            record = next((r for d, r in case.records.items() if diameter_key(d) == diameter_key(diameter)), None)
            identity = asdict(case.identity)
            if record is None or record.input_status == 'missing':
                coverage['missing'].append({'identity': identity, 'reasons': list(record.reasons) if record else ['representative_diameter_missing']})
                reasons.add('source_case_missing')
                continue
            coverage[record.input_status].append(identity)
            if record.input_status == 'not_applicable':
                continue
            measured = measure_full_mask(record)
            reasons.update((*record.reasons, *measured.reasons))
            if measured.status == 'partial':
                coverage['measurement_errors'].append({'identity': identity,
                    'reasons': tuple(dict.fromkeys((*record.reasons, *measured.reasons))),
                    'original_case_ca': asdict(measured)})
            if measured.status == 'unavailable':
                coverage['measurement_errors'].append({'identity': identity, 'reasons': measured.reasons, 'original_case_ca': asdict(measured)})
                reasons.update(measured.reasons)
                continue
            active = record.mask & frame.valid
            for endpoint in case.endpoints:
                name = endpoint.electrode_id
                touched[name] = touched.get(name, np.zeros(frame.shape_yx, bool)) | active
            observed.append((case, record, measured))
        inventory_unconfirmed = coverage['source_coverage_summary']['expected_case_inventory_status'] != 'confirmed'
        if coverage['missing'] or coverage['measurement_errors'] or missing_manifest or inventory_unconfirmed:
            coverage['source_coverage_status'] = 'partial' if observed else 'unavailable'
        coverage['source_coverage_summary'].update(
            observed_case_count=len(coverage['observed']),
            missing_case_count=len(coverage['missing']),
            measurement_error_count=len(coverage['measurement_errors']),
            missing_manifest_case_count=len(missing_manifest),
            source_coverage_status=coverage['source_coverage_status'])
        if missing_manifest:
            reasons.add('manifest_case_missing')
        if not frame.valid.all():
            reasons.add('valid_coverage_partial')
            if coverage['source_coverage_status'] == 'available':
                coverage['source_coverage_status'] = 'partial'
        counts = (np.sum(list(touched.values()), axis=0, dtype=np.int64) if touched
                  else np.zeros(frame.shape_yx, np.int64))
        counts.setflags(write=False)
        regions = []
        labelled = None
        if threshold is not None:
            active = (counts >= threshold) & frame.valid
            run_counts = _run_counts(active)
            if dataset is not None and isinstance(dataset.provenance.get('resource_estimate'), dict):
                # real datasets: price the count prepass, then the region construction, before allocating either
                from .adapters.real_hotspot import guard_stage_map_counting, guard_stage_map_regions
                guard_stage_map_counting('electrode-map', dataset, regions_done=region_total, runs=int(run_counts.sum()),
                                         layers_done=len(layers) + 1, layer_cases=len(entries), layer_electrodes=len(touched))
                labelled = _label_runs(active, run_counts)
                sizes = _component_sizes(active, labelled)
                region_total += int(sizes.size)
                component_runs = _component_run_counts(labelled)
                guard_stage_map_regions('electrode-map', dataset, regions=region_total, runs=int(run_counts.sum()),
                                        largest_area=int(sizes.max()) if sizes.size else 0,
                                        largest_runs=int(component_runs.max()) if component_runs.size else 0,
                                        layers_done=len(layers) + 1, layer_cases=len(entries),
                                        layer_electrodes=len(touched))
                del sizes, component_runs
            del run_counts
        for inside, bbox in (() if threshold is None else _components(active, labelled)):
            r0, c0, r1, c1 = bbox
            x, y = frame.origin_xy_um
            p = frame.pixel_pitch_um
            physical = (x+c0*p, y+r0*p, x+c1*p, y+r1*p)
            names = tuple(sorted(name for name, mask in touched.items() if (mask & inside).any()))
            windows = _region_windows(inside, frame)
            links = tuple(CaseLink(asdict(case.identity), case.failure_type,
                                  tuple(asdict(e) for e in case.endpoints), measured,
                                  measure_union(record, windows), copy.deepcopy(case.raw), copy.deepcopy(case.metadata))
                          for case, record, measured in observed if (record.mask & inside).any())
            windows = None  # one region's windows at a time: the guard prices the region with the most runs
            payload = [asdict(key), diameter, threshold, frame.frame_id, frame.pixel_pitch_um,
                       frame.origin_xy_um, frame.shape_yx, frame.valid_hash, np.argwhere(inside).tolist()]
            stable_id = 'region_' + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            inside.setflags(write=False)
            region_coverage = copy.deepcopy(coverage)
            region_coverage.update(region_valid_area_um2=float(inside.sum())*p*p,
                                   bbox_valid_area_um2=float(frame.valid[r0:r1, c0:c1].sum())*p*p,
                                   bbox_area_um2=(r1-r0)*(c1-c0)*p*p)
            regions.append(ElectrodeRegion(stable_id, key, int(counts[inside].max()), int(inside.sum()),
                           float(inside.sum())*p*p, bbox, physical,
                           ((physical[0]+physical[2])/2, (physical[1]+physical[3])/2),
                           frame, inside, names, links, region_coverage, copy.deepcopy(provenance)))
        labelled = active = None  # the run labels and thresholded mask are not kept past the regions
        regions.sort(key=lambda r: (-r.max_count, -r.area_um2, r.region_id))
        status = coverage['source_coverage_status'] if observed or coverage['not_applicable'] else 'unavailable'
        layers.append(LayerMap(key, frame, counts if status!='unavailable' else None, status, tuple(sorted(reasons)), coverage, tuple(regions)))
    summaries = []
    summary_groups = {}
    for layer in layers:
        summary_groups.setdefault(layer.key.summary_key, []).append(layer)
    for key, maps in sorted(summary_groups.items()):
        reasons = {reason for m in maps for reason in m.reasons}
        frame = maps[0].frame
        coverage = {'layer_maps': [{'key':asdict(m.key),'status':m.status,'coverage':copy.deepcopy(m.coverage)} for m in maps]}
        summary_provenance = {'combined_steps':sorted({m.key.process_step_id for m in maps}),
                              'combined_sources':sorted({m.key.source_id for m in maps}),
                              'combined_exposures':sorted({m.key.exposure_id for m in maps}),
                              'layer_inputs':[asdict(m.key) for m in maps],
                              'aggregation':'pixelwise_max_across_product_design_unit_layers'}
        try:
            for m in maps[1:]:
                require_same_frame(frame, m.frame)
        except NumericFrameError:
            summaries.append(ProductMap(key, None, None, 'unavailable',
                             tuple(sorted(reasons | {'incompatible_layer_frames'})), coverage, summary_provenance))
            continue
        available_counts = [m.counts for m in maps if m.counts is not None]
        counts = np.max(np.stack(available_counts),axis=0) if available_counts else None
        if counts is not None:counts.setflags(write=False)
        summaries.append(ProductMap(key, frame, counts,
                                    ('unavailable' if counts is None else
                                     'available' if all(m.status == 'available' for m in maps) else 'partial'),
                                    tuple(sorted(reasons)), coverage, summary_provenance))
    return ElectrodeMaps(tuple(layers), tuple(summaries), diameter, provenance)
