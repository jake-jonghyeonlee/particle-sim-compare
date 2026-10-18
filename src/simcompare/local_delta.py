"""Local ΔCA inside the same physical window at corresponding positions (PLAN_v3 T2/T4).

For each labelled pair the mapped-short union and its failure-type pieces of one
product / analysis_layer / diameter group are measured inside an explicit square
window (µm) around each centre, for both products, and subtracted (b − a). Windows
are physical and the original masks are used as they are: no resize, no
registration, no pixel overlay across products. A delta exists only when both
sides are complete; clipped windows, valid holes and missing sources make it null
with reasons. The attributable core is the inner square of side (window − d):
hotspot cells inside the window can be caused by electrodes up to d/2 outside it,
so only the core may be read as "caused by in-window shape". DL scores never enter
these numbers.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .correspondence import BoundPair, BoundSide
from .linepixel import FAILURE_PIECES, AreaMetric, AreaResult, GroupArea
from .measurement import measure_union
from .numeric_schema import (
    DIAMETERS_UM, Frame, MaskRecord, NumericContractError, NumericDataset, PhysicalWindow, Status,
    diameter_key, finite,
)

METRICS = ('union', *FAILURE_PIECES)
SPATIAL_GROUP_REASONS = ('partial_observation',)
WINDOW_KINDS = ('window', 'attributable_core')
SCHEMA_VERSION = 'local_delta_v1'


@dataclass(frozen=True)
class WindowMetric:
    value: float | None
    partial_value: float | None
    status: Status
    reasons: tuple[str, ...]
    window_area_um2: float
    valid_area_um2: float
    fraction_of_window: float | None
    unit: str = 'um^2'
    basis: str = 'physical_window_mapped_short_union'


@dataclass(frozen=True)
class WindowDelta:
    value: float | None
    status: Status
    reasons: tuple[str, ...]
    fraction_delta: float | None
    unit: str = 'um^2'
    direction: str = 'b_minus_a'


def _square(center, side_um) -> PhysicalWindow:
    cx, cy, half = Fraction(str(center[0])), Fraction(str(center[1])), Fraction(str(side_um)) / 2
    return PhysicalWindow(float(cx - half), float(cy - half), float(cx + half), float(cy + half))


def _window_area(window: PhysicalWindow) -> float:
    """Exact decimal rectangle area of the declared bounds (not a float sum of cell slivers)."""
    x0, y0, x1, y1 = (Fraction(str(v)) for v in window.bounds_um())
    return float((x1 - x0) * (y1 - y0))


def _group_matches(key, side: BoundSide, diameter) -> bool:
    if (key.product_id, key.frame_id, key.analysis_layer) != (side.product_id, side.frame.frame_id, side.analysis_layer):
        return False
    if diameter_key(key.diameter_um) != diameter_key(diameter):
        return False
    return all(getattr(key, name) == value for name, value in side.filters.items())


def find_group(areas: AreaResult, side: BoundSide, diameter, which: str):
    matches = [group for key, group in areas.groups.items() if _group_matches(key, side, diameter)]
    if len(matches) == 1:
        return matches[0], ()
    return None, (f'no_source_group_{which}' if not matches else f'ambiguous_source_group_{which}',)


def _group_state(metric: AreaMetric) -> tuple[str, set[str]]:
    """Spatial incompleteness of the whole frame is re-evaluated inside the window;
    missing/unmapped/rejected sources are not spatial and keep blocking."""
    nonspatial = {r for r in metric.reasons if r not in SPATIAL_GROUP_REASONS
                  and not r.startswith('active_cells_outside_valid:')}
    if metric.status == 'not_applicable':
        return 'not_applicable', set(metric.reasons)
    if metric.status == 'unavailable':
        return 'unavailable', nonspatial
    return ('partial' if nonspatial else 'available'), nonspatial


def crop_to_window(frame: Frame, mask, window: PhysicalWindow, *, margin_cells: int = 1):
    """Sub-frame and sub-mask covering the window plus a one-cell margin.

    Cells outside the window contribute zero area, so measuring the crop gives the
    same value, valid area and completeness as measuring the whole frame; a window
    that leaves the original frame still leaves the crop (clipped at the same edge).
    Avoids copying and integrating a full 2000² mask for every small window.
    """
    x0, y0, x1, y1 = window.bounds_um()
    ox, oy = frame.origin_xy_um
    pitch = frame.pixel_pitch_um
    h, w = frame.shape_yx
    c0 = min(w, max(0, int(math.floor((x0 - ox) / pitch)) - margin_cells))
    c1 = max(c0, min(w, int(math.ceil((x1 - ox) / pitch)) + margin_cells))
    r0 = min(h, max(0, int(math.floor((y0 - oy) / pitch)) - margin_cells))
    r1 = max(r0, min(h, int(math.ceil((y1 - oy) / pitch)) + margin_cells))
    if c1 <= c0 or r1 <= r0:
        return None, None
    sub_frame = Frame(frame.frame_id, pitch, (ox + c0 * pitch, oy + r0 * pitch), (r1 - r0, c1 - c0),
                      frame.valid[r0:r1, c0:c1])
    return sub_frame, np.asarray(mask)[r0:r1, c0:c1]


def _measure_cropped(frame: Frame, mask, window: PhysicalWindow, *, diameter, analysis_layer, identity=None,
                     input_status='observed', reasons=()):
    """measure_union on the window crop; unobserved records keep their status without a mask."""
    if input_status != 'observed':
        record = MaskRecord(float(diameter), frame, None, input_status, tuple(reasons), identity=identity,
                            analysis_layer=analysis_layer)
        return measure_union(record, [window])
    sub_frame, sub_mask = crop_to_window(frame, mask, window)
    if sub_frame is None:
        # window entirely outside the frame: same outcome as the full-frame measurement
        record = MaskRecord(float(diameter), frame, np.zeros(frame.shape_yx, bool), identity=identity, analysis_layer=analysis_layer)
        return measure_union(record, [window])
    record = MaskRecord(float(diameter), sub_frame, sub_mask, identity=identity, analysis_layer=analysis_layer)
    return measure_union(record, [window])


def _unavailable(window: PhysicalWindow, reasons) -> WindowMetric:
    return WindowMetric(None, None, 'unavailable', tuple(sorted(set(reasons))), _window_area(window), 0.0, None)


def measure_group_metric(group: GroupArea | None, name: str, diameter, window: PhysicalWindow,
                         reasons=()) -> WindowMetric:
    if group is None:
        return _unavailable(window, reasons)
    metric = group.union if name == 'union' else group.failure_pieces[name]
    mask = group.union_mask if name == 'union' else group.failure_piece_masks[name]
    state, group_reasons = _group_state(metric)
    if state == 'not_applicable':
        return WindowMetric(None, None, 'not_applicable', tuple(sorted(group_reasons | set(reasons))),
                            _window_area(window), 0.0, None)
    measured = _measure_cropped(group.frame, mask, window, diameter=diameter, analysis_layer=group.key.analysis_layer)
    if measured.status == 'unavailable' or state == 'unavailable':
        status = 'unavailable'
    elif measured.status == 'partial' or state == 'partial':
        status = 'partial'
    else:
        status = 'available'
    all_reasons = tuple(sorted(group_reasons | set(measured.reasons) | set(reasons)))
    observed = measured.observed_ca_um2
    area = _window_area(window)
    return WindowMetric(observed if status == 'available' else None, observed if status == 'partial' else None,
                        status, all_reasons, area, measured.valid_area_um2,
                        observed / area if status == 'available' else None)


def _from_measurement(measured, window: PhysicalWindow, basis) -> WindowMetric:
    available = measured.status == 'available'
    area = _window_area(window)
    return WindowMetric(measured.ca_um2 if available else None, measured.partial_value, measured.status,
                        tuple(measured.reasons), area, measured.valid_area_um2,
                        measured.ca_um2 / area if available else None, basis=basis)


def delta_of(a: WindowMetric, b: WindowMetric) -> WindowDelta:
    if a.status == 'not_applicable' and b.status == 'not_applicable':
        return WindowDelta(None, 'not_applicable', ('a_not_applicable', 'b_not_applicable'), None)
    if a.status == 'available' and b.status == 'available':
        value = finite(b.value - a.value, 'delta')
        return WindowDelta(value, 'available', (), finite(b.fraction_of_window - a.fraction_of_window, 'fraction delta'))
    reasons = set()
    for label, metric in (('a', a), ('b', b)):
        if metric.status != 'available':
            reasons.add(f'{label}_{metric.status}')
            reasons.update(f'{label}:{r}' for r in metric.reasons)
    return WindowDelta(None, 'unavailable', tuple(sorted(reasons)), None)


def _named_case(cases, side: BoundSide):
    ids = frozenset(e.electrode_id for e in side.endpoints)
    matches = []
    for case in cases:
        identity = case.identity
        if (identity.product_id, identity.frame_id, case.analysis_layer) != (side.product_id, side.frame.frame_id, side.analysis_layer):
            continue
        if frozenset(e.electrode_id for e in case.endpoints) != ids:
            continue
        if side.failure_type is not None and case.failure_type != side.failure_type:
            continue
        ok = True
        for name, value in side.filters.items():
            actual = getattr(identity, name, None) if name != 'unit_id' else case.metadata.get('unit_id')
            ok = ok and actual == value
        if ok:
            matches.append(case)
    return matches


def _compare(group_a, group_b, reasons_a, reasons_b, diameter, window_a, window_b):
    a = {name: measure_group_metric(group_a, name, diameter, window_a, reasons_a) for name in METRICS}
    b = {name: measure_group_metric(group_b, name, diameter, window_b, reasons_b) for name in METRICS}
    return {'a': a, 'b': b, 'delta': {name: delta_of(a[name], b[name]) for name in METRICS},
            'bounds_um': {'a': list(window_a.bounds_um()), 'b': list(window_b.bounds_um())}}


def _not_applicable_comparison(side_um, reason):
    metric = WindowMetric(None, None, 'not_applicable', (reason,), 0.0, 0.0, None)
    delta = WindowDelta(None, 'not_applicable', (reason,), None)
    return {'a': {n: metric for n in METRICS}, 'b': {n: metric for n in METRICS},
            'delta': {n: delta for n in METRICS}, 'bounds_um': None, 'side_um': side_um}


def _ranks(values):
    order = np.argsort(values, kind='mergesort')
    ranks = np.empty(len(values), dtype=float)
    sorted_values = np.asarray(values)[order]
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and sorted_values[j + 1] == sorted_values[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(x, y):
    if len(x) != len(y) or len(x) < 3:
        return None
    rx, ry = _ranks(x), _ranks(y)
    if np.ptp(rx) == 0 or np.ptp(ry) == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


@dataclass
class LocalDeltaResult:
    pairs: list[BoundPair]
    window_sizes_um: tuple[float, ...]
    jitter_um: float | None
    sort_diameter_um: float | None
    rows: list[dict]
    ranking: dict
    sensitivity: dict
    products: dict
    provenance: dict
    status: str
    reasons: tuple[str, ...]
    areas: tuple | None = None  # (AreaResult a, AreaResult b); masks for the HTML page, never serialised
    explained: dict | None = None

    def to_dict(self):
        from .report import json_value
        return json_value({'schema_version': SCHEMA_VERSION, 'status': self.status, 'reasons': list(self.reasons),
                           'direction': 'b_minus_a', 'coordinate_policy': 'independent_product_frames_same_physical_window',
                           'window_sizes_um': list(self.window_sizes_um), 'jitter_um': self.jitter_um,
                           'sort_diameter_um': self.sort_diameter_um, 'metrics': list(METRICS),
                           'window_kinds': list(WINDOW_KINDS), 'products': self.products, 'provenance': self.provenance,
                           'pairs': [pair.to_dict() for pair in self.pairs], 'rows': self.rows,
                           'ranking': self.ranking, 'window_sensitivity': self.sensitivity,
                           'explained_fraction': self.explained,
                           'interpretation': {
                               'window': '창 안 이물 중심 위치의 면적. 창 밖 전극이 원인일 수 있는 정확한 국부 위험',
                               'attributable_core': '한 변 (창 − 지름)의 안쪽 정사각형. 창 안 형상 탓으로 읽을 수 있는 부분',
                               'fraction_of_window': '면적 / 창 면적. 단위 없음, 유닛 크기와 무관',
                               'dl_scores': '표시 문맥일 뿐 어떤 수치에도 들어가지 않음'}})


def _validate_sizes(window_sizes_um):
    if not isinstance(window_sizes_um, (list, tuple)) or not window_sizes_um:
        raise NumericContractError('window_sizes_required', '--window-sizes 에 창 한 변(µm)을 하나 이상 지정하세요')
    sizes = tuple(sorted({finite(v, 'window size', positive=True) for v in window_sizes_um}))
    if len(sizes) != len(window_sizes_um):
        raise NumericContractError('duplicate_window_size')
    return sizes


def compute_local_delta(pairs, reference: NumericDataset, target: NumericDataset, *, window_sizes_um,
                        jitter_um=None, sort_diameter_um=None, areas=None, provenance=None) -> LocalDeltaResult:
    """pairs: BoundPair list (side a → reference, side b → target)."""
    from .linepixel import compute_areas
    sizes = _validate_sizes(window_sizes_um)
    if jitter_um is not None:
        jitter_um = finite(jitter_um, 'jitter', positive=True)
    if sort_diameter_um is not None:
        sort_diameter_um = finite(sort_diameter_um, 'sort diameter', positive=True)
        if diameter_key(sort_diameter_um) not in {diameter_key(d) for d in DIAMETERS_UM}:
            raise NumericContractError('invalid_representative_diameter', '정렬 지름은 1..30, 40, 50 um 중 하나여야 합니다')
    if not isinstance(pairs, (list, tuple)) or any(not isinstance(p, BoundPair) for p in pairs):
        raise NumericContractError('bound_pairs_required')
    reference, target = _as_dataset(reference), _as_dataset(target)
    areas_a, areas_b = areas if areas is not None else (compute_areas(reference.cases if reference.provenance.get('_case_list') else reference),
                                                        compute_areas(target.cases if target.provenance.get('_case_list') else target))
    rows, reasons = [], set()
    for pair in pairs:
        if pair.status != 'ready':
            reasons.add('blocked_pairs_present')
            continue
        for size in sizes:
            window_a, window_b = _square(pair.a.center_xy_um, size), _square(pair.b.center_xy_um, size)
            for diameter in DIAMETERS_UM:
                group_a, reasons_a = find_group(areas_a, pair.a, diameter, 'a')
                group_b, reasons_b = find_group(areas_b, pair.b, diameter, 'b')
                window = _compare(group_a, group_b, reasons_a, reasons_b, diameter, window_a, window_b)
                core_side = Fraction(str(size)) - Fraction(str(diameter))
                if core_side <= 0:
                    core = _not_applicable_comparison(None, 'window_smaller_than_diameter')
                else:
                    core = {**_compare(group_a, group_b, reasons_a, reasons_b, diameter,
                                       _square(pair.a.center_xy_um, core_side), _square(pair.b.center_xy_um, core_side)),
                            'side_um': float(core_side)}
                named = None
                if pair.a.endpoints is not None and pair.b.endpoints is not None:
                    named = _named_pair(reference, target, pair, diameter, window_a, window_b)
                jitter = None
                if jitter_um is not None:
                    jitter = _jitter(pair, group_a, group_b, reasons_a, reasons_b, diameter, size, jitter_um)
                rows.append({'pair_id': pair.pair_id, 'window_size_um': size, 'diameter_um': float(diameter),
                             'status': window['delta']['union'].status,
                             'window': {**window, 'side_um': size}, 'attributable_core': core,
                             'named_pair': named, 'jitter': jitter,
                             'group_a': None if group_a is None else asdict(group_a.key),
                             'group_b': None if group_b is None else asdict(group_b.key)})
    ready = [p for p in pairs if p.status == 'ready']
    for row in rows:
        if row['status'] not in ('available', 'not_applicable'):
            reasons.add('incomplete_local_delta')
    status = ('unavailable' if not ready else 'available' if not reasons else 'partial')
    ranking = _ranking(rows, sizes, sort_diameter_um)
    sensitivity = window_sensitivity(rows, sizes, sort_diameter_um)
    explained = explained_fraction(rows, ready, areas_a, areas_b, sizes)
    products = {'a': {'product_id': _product(reference), 'frames': _frames(reference)},
                'b': {'product_id': _product(target), 'frames': _frames(target)}}
    provenance = {**(provenance or {}), 'reference_input_hash': areas_a.input_hash, 'target_input_hash': areas_b.input_hash,
                  'is_synthetic': bool(areas_a.is_synthetic and areas_b.is_synthetic),
                  'n_pairs': len(pairs), 'n_ready': len(ready), 'n_blocked': len(pairs) - len(ready)}
    return LocalDeltaResult(list(pairs), sizes, jitter_um, sort_diameter_um, rows, ranking, sensitivity, products,
                            provenance, status, tuple(sorted(reasons)), (areas_a, areas_b), explained)


def _as_dataset(source) -> NumericDataset:
    """Datasets are used as they are; a plain case iterable (tests, library use) is wrapped."""
    if isinstance(source, NumericDataset):
        return source
    cases = list(source)
    frames = {}
    for case in cases:
        for record in case.records.values():
            frames.setdefault(record.frame.frame_id, record.frame)
    return NumericDataset(cases, {}, frames, [], {}, {}, {}, {'_case_list': True})


def _product(dataset):
    products = {c.identity.product_id for c in dataset.cases}
    return next(iter(products)) if len(products) == 1 else None


def _frames(dataset):
    return {fid: {'pixel_pitch_um': f.pixel_pitch_um, 'origin_xy_um': list(f.origin_xy_um), 'shape_yx': list(f.shape_yx),
                  'frame_area_um2': f.shape_yx[0] * f.shape_yx[1] * f.pixel_pitch_um ** 2}
            for fid, f in sorted(dataset.frames.items())}


def _named_pair(reference, target, pair, diameter, window_a, window_b):
    out = {'a': None, 'b': None, 'delta': None, 'cases': {'a': None, 'b': None}, 'reasons': []}
    metrics = {}
    for label, dataset, side, window in (('a', reference, pair.a, window_a), ('b', target, pair.b, window_b)):
        matches = _named_case(dataset.cases, side)
        if len(matches) != 1:
            reason = f'named_pair_case_not_found_{label}' if not matches else f'named_pair_case_ambiguous_{label}'
            out['reasons'].append(reason)
            metrics[label] = _unavailable(window, (reason,))
            continue
        case = matches[0]
        out['cases'][label] = {'case_id': case.identity.case_id, 'failure_type': case.failure_type,
                               'pair_id': case.pair_id, 'classification': case.classification}
        record = case.records[diameter]
        measured = _measure_cropped(record.frame, record.mask, window, diameter=diameter, analysis_layer=record.analysis_layer,
                                    identity=record.identity, input_status=record.input_status, reasons=record.reasons)
        metrics[label] = _from_measurement(measured, window, 'physical_window_single_case')
    out['a'], out['b'] = metrics['a'], metrics['b']
    out['delta'] = delta_of(metrics['a'], metrics['b'])
    return out


def _jitter(pair, group_a, group_b, reasons_a, reasons_b, diameter, size, jitter_um):
    offsets = [(jitter_um, 0.0), (-jitter_um, 0.0), (0.0, jitter_um), (0.0, -jitter_um)]
    values = []
    for dx, dy in offsets:
        wa = _square((pair.a.center_xy_um[0] + dx, pair.a.center_xy_um[1] + dy), size)
        wb = _square((pair.b.center_xy_um[0] + dx, pair.b.center_xy_um[1] + dy), size)
        delta = delta_of(measure_group_metric(group_a, 'union', diameter, wa, reasons_a),
                         measure_group_metric(group_b, 'union', diameter, wb, reasons_b))
        values.append(delta.value)
    complete = all(v is not None for v in values)
    sign_stable = None
    if complete:
        sign_stable = all(v >= 0 for v in values) or all(v <= 0 for v in values)
    return {'jitter_um': jitter_um, 'offsets_xy_um': [list(o) for o in offsets], 'delta_union_values': values,
            'min': min(values) if complete else None, 'max': max(values) if complete else None,
            'sign_stable': sign_stable, 'status': 'available' if complete else 'partial'}


def _ranking(rows, sizes, sort_diameter_um):
    if sort_diameter_um is None:
        return {'status': 'not_applicable', 'reason': 'sort_diameter_not_specified', 'by_window_size': {}}
    by_size = {}
    for size in sizes:
        entries = []
        for row in rows:
            if row['window_size_um'] != size or diameter_key(row['diameter_um']) != diameter_key(sort_diameter_um):
                continue
            delta = row['window']['delta']['union']
            entries.append({'pair_id': row['pair_id'], 'delta_union_um2': delta.value, 'status': delta.status,
                            'abs_delta_um2': None if delta.value is None else abs(delta.value),
                            'fraction_delta': delta.fraction_delta})
        entries.sort(key=lambda e: (e['abs_delta_um2'] is None, -(e['abs_delta_um2'] or 0.0), e['pair_id']))
        for index, entry in enumerate(entries):
            entry['rank'] = index + 1 if entry['abs_delta_um2'] is not None else None
        by_size[str(size)] = entries
    return {'status': 'available', 'sort_diameter_um': sort_diameter_um, 'order': 'abs_delta_union_desc_then_pair_id',
            'by_window_size': by_size}


def window_sensitivity(rows, sizes, sort_diameter_um):
    """Per pair×diameter deltas across window sizes, sign consistency, rank agreement.
    Reports only; the user chooses the window size (G4)."""
    table = {}
    for row in rows:
        key = (row['pair_id'], row['diameter_um'])
        table.setdefault(key, {})[row['window_size_um']] = row['window']['delta']['union'].value
    per_pair = []
    for (pair_id, diameter), by_size in sorted(table.items()):
        values = [by_size.get(size) for size in sizes]
        complete = all(v is not None for v in values)
        per_pair.append({'pair_id': pair_id, 'diameter_um': diameter,
                         'delta_union_by_size': {str(size): by_size.get(size) for size in sizes},
                         'sign_consistent': (all(v >= 0 for v in values) or all(v <= 0 for v in values)) if complete else None,
                         'max_abs_delta': max(abs(v) for v in values) if complete else None,
                         'min_abs_delta': min(abs(v) for v in values) if complete else None})
    agreement = []
    for i, s1 in enumerate(sizes):
        for s2 in sizes[i + 1:]:
            if sort_diameter_um is None:
                agreement.append({'sizes_um': [s1, s2], 'spearman_abs_delta': None, 'n_pairs': 0,
                                  'status': 'not_applicable', 'reason': 'sort_diameter_not_specified'})
                continue
            xs, ys = [], []
            for (pair_id, diameter), by_size in table.items():
                if diameter_key(diameter) != diameter_key(sort_diameter_um):
                    continue
                if by_size.get(s1) is None or by_size.get(s2) is None:
                    continue
                xs.append(abs(by_size[s1]))
                ys.append(abs(by_size[s2]))
            rho = spearman(xs, ys)
            agreement.append({'sizes_um': [s1, s2], 'spearman_abs_delta': rho, 'n_pairs': len(xs),
                              'status': 'available' if rho is not None else 'not_applicable',
                              'reason': None if rho is not None else ('insufficient_pairs' if len(xs) < 3 else 'constant_ranks')})
    core = {str(size): [float(d) for d in DIAMETERS_UM if Fraction(str(size)) > Fraction(str(d))] for size in sizes}
    return {'schema_version': 'window_sensitivity_v1', 'window_sizes_um': list(sizes), 'sort_diameter_um': sort_diameter_um,
            'per_pair_diameter': per_pair, 'rank_agreement': agreement, 'core_applicable_diameters_um': core,
            'note': '추천값을 계산하지 않는다. 사용자가 표를 보고 창 크기(G4)를 정한다.'}


def _rectangles_overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def explained_fraction(rows, ready_pairs, areas_a, areas_b, sizes):
    """PLAN_v3 T3b: Σ(window CA × repeats_per_unit) / frame_area per side, against the
    product-level fraction_of_frame delta of the referenced groups. Available only when
    every ready pair declares repeats and the windows of one side do not overlap."""
    if not ready_pairs:
        return {'status': 'unavailable', 'reasons': ['no_ready_pairs'], 'by_window_size': {}}
    missing = [p.pair_id for p in ready_pairs if p.repeats_per_unit is None]
    if missing:
        return {'status': 'unavailable', 'reasons': ['repeats_per_unit_required'], 'pairs_without_repeats': missing,
                'by_window_size': {}}
    repeats = {p.pair_id: p.repeats_per_unit for p in ready_pairs}
    frame_area = {'a': {p.pair_id: p.a.frame.valid.size * p.a.frame.pixel_pitch_um ** 2 for p in ready_pairs},
                  'b': {p.pair_id: p.b.frame.valid.size * p.b.frame.pixel_pitch_um ** 2 for p in ready_pairs}}
    by_size = {}
    for size in sizes:
        per_diameter = {}
        for diameter in DIAMETERS_UM:
            selected = [r for r in rows if r['window_size_um'] == size and diameter_key(r['diameter_um']) == diameter_key(diameter)]
            if not selected:
                continue
            reasons = set()
            for label in ('a', 'b'):
                bounds = [r['window']['bounds_um'][label] for r in selected]
                if any(_rectangles_overlap(x, y) for i, x in enumerate(bounds) for y in bounds[i + 1:]):
                    reasons.add(f'overlapping_windows_{label}')
            sums = {'a': 0.0, 'b': 0.0}
            for row in selected:
                for label in ('a', 'b'):
                    metric = row['window'][label]['union']
                    if metric.status != 'available':
                        reasons.add(f'{label}_{metric.status}:{row["pair_id"]}')
                        continue
                    sums[label] += metric.value * repeats[row['pair_id']][label] / frame_area[label][row['pair_id']]
            actual = {'a': 0.0, 'b': 0.0}
            for label, areas in (('a', areas_a), ('b', areas_b)):
                keys = {tuple(sorted(row[f'group_{label}'].items())) for row in selected if row[f'group_{label}'] is not None}
                if len(keys) != len({row['pair_id'] for row in selected if row[f'group_{label}'] is not None}) and not keys:
                    reasons.add(f'group_missing_{label}')
                for key in keys:
                    group = next((g for k, g in areas.groups.items() if tuple(sorted(asdict(k).items())) == key), None)
                    fraction = None if group is None else group.fractions['union']
                    if fraction is None or fraction.status != 'available':
                        reasons.add(f'{label}_fraction_{"missing" if fraction is None else fraction.status}')
                        continue
                    actual[label] += fraction.value
            available = not reasons
            per_diameter[str(float(diameter))] = {
                'status': 'available' if available else 'unavailable', 'reasons': sorted(reasons),
                'explained_a': sums['a'] if available else None, 'explained_b': sums['b'] if available else None,
                'explained_delta': (sums['b'] - sums['a']) if available else None,
                'actual_delta_fraction_of_frame': (actual['b'] - actual['a']) if available else None,
                'residual': ((actual['b'] - actual['a']) - (sums['b'] - sums['a'])) if available else None}
        by_size[str(size)] = per_diameter
    return {'status': 'available', 'reasons': [], 'unit': '1', 'direction': 'b_minus_a',
            'definition': 'Σ(window union CA × repeats_per_unit) / frame_area per side; actual = Σ fraction_of_frame(union) of the referenced groups',
            'by_window_size': by_size}


def _csv_rows(result: LocalDeltaResult):
    out = []
    for row in result.rows:
        for kind in WINDOW_KINDS:
            comparison = row[kind]
            for name in METRICS:
                a, b, d = comparison['a'][name], comparison['b'][name], comparison['delta'][name]
                out.append({'pair_id': row['pair_id'], 'window_size_um': row['window_size_um'], 'diameter_um': row['diameter_um'],
                            'kind': kind, 'side_um': comparison.get('side_um'), 'metric': name,
                            'a_value_um2': a.value, 'a_partial_value_um2': a.partial_value, 'a_status': a.status,
                            'a_fraction_of_window': a.fraction_of_window,
                            'b_value_um2': b.value, 'b_partial_value_um2': b.partial_value, 'b_status': b.status,
                            'b_fraction_of_window': b.fraction_of_window,
                            'delta_um2': d.value, 'delta_fraction': d.fraction_delta, 'delta_status': d.status,
                            'delta_reasons': list(d.reasons)})
        named = row['named_pair']
        if named is not None and named['delta'] is not None:
            out.append({'pair_id': row['pair_id'], 'window_size_um': row['window_size_um'], 'diameter_um': row['diameter_um'],
                        'kind': 'window', 'side_um': row['window_size_um'], 'metric': 'named_pair',
                        'a_value_um2': named['a'].value, 'a_partial_value_um2': named['a'].partial_value, 'a_status': named['a'].status,
                        'a_fraction_of_window': named['a'].fraction_of_window,
                        'b_value_um2': named['b'].value, 'b_partial_value_um2': named['b'].partial_value, 'b_status': named['b'].status,
                        'b_fraction_of_window': named['b'].fraction_of_window,
                        'delta_um2': named['delta'].value, 'delta_fraction': named['delta'].fraction_delta,
                        'delta_status': named['delta'].status, 'delta_reasons': list(named['delta'].reasons)})
    return out


def _write_json_csv(out, name, payload, tables):
    import csv
    from .report import json_value
    payload = json_value(payload)
    paths = []
    json_path = out / (name + '.json')
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    paths.append(str(json_path))
    for table, rows in tables.items():
        rows = [json_value(row) for row in rows]
        path = out / (name + '_' + table + '.csv')
        columns = list(dict.fromkeys(k for row in rows for k in row)) or ['value', 'status', 'reasons']
        with path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, columns, lineterminator='\n')
            writer.writeheader()
            for row in rows:
                writer.writerow({k: json.dumps(v, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
                                 if v is None or isinstance(v, (dict, list, bool)) else v for k, v in row.items()})
        paths.append(str(path))
    return paths


def write_local_delta(out, result: LocalDeltaResult, *, compact=False):
    """local_delta.json/csv and window_sensitivity.json/csv; the HTML page is a separate writer.

    compact=True leaves the per-row nested metrics out of the JSON (they stay in
    local_delta_rows.csv) so tens of pairs × several windows do not produce tens of MB.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for prefix in ('local_delta', 'window_sensitivity'):
        for old in out.glob(prefix + '*'):
            if old.is_file() and old.suffix in ('.json', '.csv', '.html'):
                old.unlink()
    payload = result.to_dict()
    if compact:
        payload = {**payload, 'rows': None, 'json_detail': 'compact', 'rows_csv': 'local_delta_rows.csv',
                   'n_rows': len(result.rows)}
    else:
        payload = {**payload, 'json_detail': 'full', 'rows_csv': 'local_delta_rows.csv', 'n_rows': len(result.rows)}
    paths = _write_json_csv(out, 'local_delta', payload, {'rows': _csv_rows(result)})
    sensitivity = result.sensitivity
    rows = [{'pair_id': e['pair_id'], 'diameter_um': e['diameter_um'], 'sign_consistent': e['sign_consistent'],
             'max_abs_delta': e['max_abs_delta'], 'min_abs_delta': e['min_abs_delta'],
             **{f'delta_union_{size}um': value for size, value in e['delta_union_by_size'].items()}}
            for e in sensitivity['per_pair_diameter']]
    paths += _write_json_csv(out, 'window_sensitivity', sensitivity,
                             {'rows': rows, 'rank_agreement': sensitivity['rank_agreement']})
    return paths


def correspondence_hash(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
