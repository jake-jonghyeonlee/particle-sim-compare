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

from collections import OrderedDict
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
import hashlib
import itertools
import json
import math
from pathlib import Path

import numpy as np

from .correspondence import BoundPair, BoundSide
from .linepixel import FAILURE_PIECES, AreaMetric, AreaResult, GroupArea
from .measurement import measure_union
from .publication import staged_file
from .numeric_schema import (
    DIAMETERS_UM, Frame, MaskRecord, NumericContractError, NumericDataset, PhysicalWindow, Status,
    diameter_key, finite,
)

METRICS = ('union', *FAILURE_PIECES)
# Both sides x every metric x 4 diameters. Unpacking costs ~0.06 ms per 1262^2 mask while a window measurement costs ~10 ms, so the cache only has
# to cover the 24 uses of a row's 8 masks (a row needs 8 unpacks instead of 24); the 256 entries that would keep every diameter of a layer
# bought another ~5 % of time for ~420 MB (T3_report.md), so the bound stays small. STAGE6_UNPACK_CACHE_ENTRIES in real_hotspot.py must match.
UNPACK_CACHE_ENTRIES = 2 * len(METRICS) * 4
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
    if (key.product_id, key.design_version, key.frame_id, key.analysis_layer) != (
            side.product_id, side.design_version, side.frame.frame_id, side.analysis_layer):
        return False
    if diameter_key(key.diameter_um) != diameter_key(diameter):
        return False
    return all(getattr(key, name) == value for name, value in side.filters.items())


def _count(kind, amount=1):
    from .adapters import real_hotspot
    if real_hotspot.IO_COUNTERS is not None:     # benchmark / test counters, off by default
        real_hotspot.IO_COUNTERS[kind] = real_hotspot.IO_COUNTERS.get(kind, 0) + amount


class MaskCache:
    """Bounded LRU of unpacked group masks for ONE invocation (it is dropped with compute_local_delta / the page writer).

    Entries are keyed by the PackedMask they came from and hold a strong reference to it, so a key can never be
    reused by another object while its entry is alive. The arrays are the same read-only arrays PackedMask.unpack
    returns: measuring them gives the same numbers as unpacking afresh for every use."""

    def __init__(self, max_entries=None):
        self.max_entries = UNPACK_CACHE_ENTRIES if max_entries is None else max_entries
        self._entries = OrderedDict()
        self.unpacks = self.hits = 0

    def get(self, packed):
        entry = self._entries.get(id(packed))
        if entry is not None and entry[0] is packed:
            self._entries.move_to_end(id(packed))
            self.hits += 1
            _count('mask_unpack_hits')
            return entry[1]
        array = packed.unpack()
        self.unpacks += 1
        _count('mask_unpacks')
        if self.max_entries > 0:
            self._entries[id(packed)] = (packed, array)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return array

    def __len__(self):
        return len(self._entries)

    def clear(self):
        self._entries.clear()


def group_mask(group, name: str, masks: MaskCache | None = None):
    """The union (name == 'union') or failure-piece mask of a group; through the cache when one is given."""
    if masks is None:
        return group.union_mask if name == 'union' else group.failure_piece_masks[name]
    return masks.get(group.union_packed if name == 'union' else group.failure_piece_packed[name])


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


def _exact_inside(frame: Frame, window: PhysicalWindow) -> bool:
    """Window inside the frame, decided on the declared decimal numbers (no float ulp at flush edges)."""
    x0, y0, x1, y1 = (Fraction(str(v)) for v in window.bounds_um())
    ox, oy = (Fraction(str(v)) for v in frame.origin_xy_um)
    pitch = Fraction(str(frame.pixel_pitch_um))
    h, w = frame.shape_yx
    return x0 >= ox and y0 >= oy and x1 <= ox + w * pitch and y1 <= oy + h * pitch


def _windows_touch_invalid(frame: Frame, windows) -> bool:
    """Does any window cover part of an invalid cell? Decided on exact decimal cell edges of the
    declared frame (half-open), so an edge flush with a cell boundary never touches the next cell."""
    invalid = ~frame.valid
    if not invalid.any():
        return False
    ox, oy = (Fraction(str(v)) for v in frame.origin_xy_um)
    pitch = Fraction(str(frame.pixel_pitch_um))
    h, w = frame.shape_yx
    for window in windows:
        x0, y0, x1, y1 = (Fraction(str(v)) for v in window.bounds_um())
        c0, c1 = max(0, math.floor((x0 - ox) / pitch)), min(w, math.ceil((x1 - ox) / pitch))
        r0, r1 = max(0, math.floor((y0 - oy) / pitch)), min(h, math.ceil((y1 - oy) / pitch))
        if c1 > c0 and r1 > r0 and invalid[r0:r1, c0:c1].any():
            return True
    return False


def _reconcile(measured, frame: Frame, crop_frame: Frame, windows):
    """Re-decide available/partial with exact frame bounds and name the cause:
    window_clipped (leaves the frame) and/or valid_incomplete (covers an invalid cell)."""
    if measured.status not in ('available', 'partial'):
        return measured
    inside = all(_exact_inside(frame, w) for w in windows)
    touches = _windows_touch_invalid(frame, windows)  # original frame: exact declared origin and pitch
    base = {r for r in measured.reasons if r != 'partial_observation'}
    if inside and not touches:
        observed = measured.observed_ca_um2
        return replace(measured, status='available', ca_um2=observed,
                       ca_per_valid_area=observed / measured.valid_area_um2 if measured.valid_area_um2 else None,
                       reasons=tuple(sorted(base)), partial_value=None, partial_ca_per_valid_area=None)
    causes = {'window_clipped'} if not inside else set()
    if touches:
        causes.add('valid_incomplete')
    observed = measured.observed_ca_um2
    return replace(measured, status='partial', ca_um2=None, ca_per_valid_area=None,
                   reasons=tuple(sorted(base | {'partial_observation'} | causes)), partial_value=observed,
                   partial_ca_per_valid_area=observed / measured.valid_area_um2 if measured.valid_area_um2 else None)


def _bounding_window(windows):
    bounds = [w.bounds_um() for w in windows]
    return PhysicalWindow(min(b[0] for b in bounds), min(b[1] for b in bounds),
                          max(b[2] for b in bounds), max(b[3] for b in bounds))


def _measure_cropped_windows(frame: Frame, mask, windows, *, diameter, analysis_layer, identity=None,
                             input_status='observed', reasons=()):
    """measure_union over the windows on a crop around their bounding box; unobserved records keep
    their status without a mask. Completeness is reconciled with exact frame bounds."""
    windows = list(windows)
    if input_status != 'observed':
        record = MaskRecord(float(diameter), frame, None, input_status, tuple(reasons), identity=identity,
                            analysis_layer=analysis_layer)
        return measure_union(record, windows)
    sub_frame, sub_mask = crop_to_window(frame, mask, _bounding_window(windows))
    if sub_frame is None:
        # windows entirely outside the frame: same outcome as the full-frame measurement (empty valid area)
        record = MaskRecord(float(diameter), frame, np.zeros(frame.shape_yx, bool), identity=identity, analysis_layer=analysis_layer)
        return measure_union(record, windows)
    record = MaskRecord(float(diameter), sub_frame, sub_mask, identity=identity, analysis_layer=analysis_layer)
    return _reconcile(measure_union(record, windows), frame, sub_frame, windows)


def _measure_cropped(frame: Frame, mask, window: PhysicalWindow, **kwargs):
    return _measure_cropped_windows(frame, mask, [window], **kwargs)


def _unavailable(window: PhysicalWindow, reasons) -> WindowMetric:
    return WindowMetric(None, None, 'unavailable', tuple(sorted(set(reasons))), _window_area(window), 0.0, None)


def measure_group_metric(group: GroupArea | None, name: str, diameter, window: PhysicalWindow,
                         reasons=(), masks: MaskCache | None = None) -> WindowMetric:
    if group is None:
        return _unavailable(window, reasons)
    metric = group.union if name == 'union' else group.failure_pieces[name]
    state, group_reasons = _group_state(metric)
    if state == 'not_applicable':
        return WindowMetric(None, None, 'not_applicable', tuple(sorted(group_reasons | set(reasons))),
                            _window_area(window), 0.0, None)
    mask = group_mask(group, name, masks)
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
        reasons = {'a_not_applicable', 'b_not_applicable'}
        for label, metric in (('a', a), ('b', b)):
            if {'all_vertical_sources_not_applicable', 'below_height_vertical_short'} & set(metric.reasons):
                reasons.add(f'below_height_vertical_short_{label}')
        return WindowDelta(None, 'not_applicable', tuple(sorted(reasons)), None)
    if a.status == 'available' and b.status == 'available':
        value = finite(b.value - a.value, 'delta')
        return WindowDelta(value, 'available', (), finite(b.fraction_of_window - a.fraction_of_window, 'fraction delta'))
    reasons = set()
    for label, metric in (('a', a), ('b', b)):
        if metric.status != 'available':
            reasons.add(f'{label}_{metric.status}')
            reasons.update(f'{label}:{r}' for r in metric.reasons)
            for cause in ('window_clipped', 'valid_incomplete'):
                if cause in metric.reasons:
                    reasons.add(f'{cause}_{label}')
            if metric.status == 'not_applicable' and {'all_vertical_sources_not_applicable', 'below_height_vertical_short'} & set(metric.reasons):
                reasons.add(f'below_height_vertical_short_{label}')
    return WindowDelta(None, 'unavailable', tuple(sorted(reasons)), None)


def _case_key(case):
    identity = case.identity
    return (identity.product_id, identity.design_version, identity.frame_id, case.analysis_layer,
            frozenset(e.electrode_id for e in case.endpoints))


def _side_key(side: BoundSide):
    return (side.product_id, side.design_version, side.frame.frame_id, side.analysis_layer,
            frozenset(e.electrode_id for e in side.endpoints))


def _named_filter(candidates, side: BoundSide):
    """The checks of _named_case after the identity / endpoint-set match, in the original order."""
    matches = []
    for case in candidates:
        identity = case.identity
        if side.failure_type is not None and case.failure_type != side.failure_type:
            continue
        ok = True
        for name, value in side.filters.items():
            actual = getattr(identity, name, None) if name != 'unit_id' else case.metadata.get('unit_id')
            ok = ok and actual == value
        if ok:
            matches.append(case)
    return matches


def _named_case(cases, side: BoundSide):
    key = _side_key(side)
    return _named_filter([case for case in cases if _case_key(case) == key], side)


class NamedCaseIndex:
    """dataset.cases bucketed once by (product, design, frame, layer, endpoint set), case order preserved, so
    matches(side) equals _named_case(dataset.cases, side) without scanning every case for every pair / size."""

    def __init__(self, cases):
        self._buckets = {}
        for case in cases:
            self._buckets.setdefault(_case_key(case), []).append(case)

    def matches(self, side: BoundSide):
        return _named_filter(self._buckets.get(_side_key(side), ()), side)


class _Run:
    """Caches of one compute_local_delta invocation; they die with it (nothing is process-wide)."""

    def __init__(self, reference, target):
        self.masks = MaskCache()
        self._datasets = {'a': reference, 'b': target}
        self._indexes = {}
        self._records = {}

    def named_matches(self, label, side):
        index = self._indexes.get(label)
        if index is None:
            index = self._indexes[label] = NamedCaseIndex(self._datasets[label].cases)
        return index.matches(side)

    def new_pair(self):
        self._records.clear()

    def record(self, label, case, diameter):
        """case.records[diameter] loaded once per pair (a lazy record reads its .npz on every access)."""
        key = (label, id(case), diameter)
        entry = self._records.get(key)
        if entry is None:
            entry = self._records[key] = (case, case.records[diameter])
        return entry[1]


def _compare(group_a, group_b, reasons_a, reasons_b, diameter, window_a, window_b, masks=None):
    a = {name: measure_group_metric(group_a, name, diameter, window_a, reasons_a, masks) for name in METRICS}
    b = {name: measure_group_metric(group_b, name, diameter, window_b, reasons_b, masks) for name in METRICS}
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

    def payload(self):
        """The JSON document with `rows` and the rest still as live objects (not converted by json_value)."""
        return {'schema_version': SCHEMA_VERSION, 'status': self.status, 'reasons': list(self.reasons),
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
                    'dl_scores': '표시 문맥일 뿐 어떤 수치에도 들어가지 않음'}}

    def to_dict(self):
        from .report import json_value
        return json_value(self.payload())


def _validate_sizes(window_sizes_um):
    if not isinstance(window_sizes_um, (list, tuple)) or not window_sizes_um:
        raise NumericContractError('window_sizes_required', '--window-sizes 에 창 한 변(µm)을 하나 이상 지정하세요')
    sizes = tuple(sorted({finite(v, 'window size', positive=True) for v in window_sizes_um}))
    if len(sizes) != len(window_sizes_um):
        raise NumericContractError('duplicate_window_size')
    return sizes


def compute_local_delta(pairs, reference: NumericDataset, target: NumericDataset, *, window_sizes_um,
                        jitter_um=None, sort_diameter_um=None, areas=None, provenance=None) -> LocalDeltaResult:
    """pairs: BoundPair list (side a → reference, side b → target).

    areas = (AreaResult a, AreaResult b) skips compute_areas (the caller owns their admission); anything with the
    groups / input_hash / is_synthetic of an AreaResult works, e.g. compare_masks.ReusedAreaResult."""
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
    if areas is None:
        from .adapters.real_hotspot import guard_stage_compare_windows
        guard_stage_compare_windows('compare-windows', reference, target, pairs=pairs, sizes=sizes,
                                    jitter_um=jitter_um, phase='compute')  # before both AreaResults exist
    # compare-windows reads masks and areas only: summary mode keeps one bucket per class (STAGE6_TOP_BUCKETS)
    from .adapters.real_hotspot import STAGE6_TOP_BUCKETS
    summary = {'bucket_detail': 'summary', 'top_buckets': STAGE6_TOP_BUCKETS}
    areas_a, areas_b = areas if areas is not None else (compute_areas(reference.cases if reference.provenance.get('_case_list') else reference, **summary),
                                                        compute_areas(target.cases if target.provenance.get('_case_list') else target, **summary))
    rows, reasons = [], set()
    run = _Run(reference, target)
    for pair in pairs:
        if pair.status != 'ready':
            reasons.add('blocked_pairs_present')
            continue
        run.new_pair()
        for size in sizes:
            window_a, window_b = _square(pair.a.center_xy_um, size), _square(pair.b.center_xy_um, size)
            for diameter in DIAMETERS_UM:
                group_a, reasons_a = find_group(areas_a, pair.a, diameter, 'a')
                group_b, reasons_b = find_group(areas_b, pair.b, diameter, 'b')
                window = _compare(group_a, group_b, reasons_a, reasons_b, diameter, window_a, window_b, run.masks)
                core_side = Fraction(str(size)) - Fraction(str(diameter))
                if core_side <= 0:
                    core = _not_applicable_comparison(None, 'window_smaller_than_diameter')
                else:
                    core = {**_compare(group_a, group_b, reasons_a, reasons_b, diameter,
                                       _square(pair.a.center_xy_um, core_side), _square(pair.b.center_xy_um, core_side),
                                       run.masks),
                            'side_um': float(core_side)}
                named = None
                if pair.a.endpoints is not None and pair.b.endpoints is not None:
                    named = _named_pair(reference, target, pair, diameter, window_a, window_b,
                                        window['a']['union'], window['b']['union'], run)
                jitter = None
                if jitter_um is not None:
                    jitter = _jitter(pair, group_a, group_b, reasons_a, reasons_b, diameter, size, jitter_um, run.masks)
                rows.append({'pair_id': pair.pair_id, 'window_size_um': size, 'diameter_um': float(diameter),
                             'status': window['delta']['union'].status,
                             'window': {**window, 'side_um': size}, 'attributable_core': core,
                             'named_pair': named, 'jitter': jitter,
                             'group_a': None if group_a is None else asdict(group_a.key),
                             'group_b': None if group_b is None else asdict(group_b.key)})
    run.new_pair()
    ready = [p for p in pairs if p.status == 'ready']
    for row in rows:
        if row['status'] not in ('available', 'not_applicable'):
            reasons.add('incomplete_local_delta')
    status = ('unavailable' if not ready else 'available' if not reasons else 'partial')
    ranking = _ranking(rows, sizes, sort_diameter_um)
    sensitivity = window_sensitivity(rows, sizes, sort_diameter_um)
    explained = explained_fraction(rows, ready, areas_a, areas_b, sizes, run.masks)
    run.masks.clear()   # the page writer builds its own small cache; the compute-phase one must not outlive the phase
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


def _named_pair(reference, target, pair, diameter, window_a, window_b, union_a=None, union_b=None, run=None):
    out = {'a': None, 'b': None, 'delta': None, 'cases': {'a': None, 'b': None}, 'reasons': []}
    metrics = {}
    unions = {'a': union_a, 'b': union_b}
    for label, dataset, side, window in (('a', reference, pair.a, window_a), ('b', target, pair.b, window_b)):
        matches = _named_case(dataset.cases, side) if run is None else run.named_matches(label, side)
        if len(matches) != 1:
            reason = f'named_pair_case_not_found_{label}' if not matches else f'named_pair_case_ambiguous_{label}'
            out['reasons'].append(reason)
            metrics[label] = _unavailable(window, (reason,))
            continue
        case = matches[0]
        out['cases'][label] = {'case_id': case.identity.case_id, 'failure_type': case.failure_type,
                               'pair_id': case.pair_id, 'classification': case.classification}
        record = case.records[diameter] if run is None else run.record(label, case, diameter)
        measured = _measure_cropped(record.frame, record.mask, window, diameter=diameter, analysis_layer=record.analysis_layer,
                                    identity=record.identity, input_status=record.input_status, reasons=record.reasons)
        metrics[label] = _from_measurement(measured, window, 'physical_window_single_case')
        union = unions[label]
        if (union is not None and union.status == 'available' and metrics[label].status == 'available'
                and metrics[label].value > union.value + 1e-9):
            reason = f'named_pair_exceeds_group_union_{label}'
            out['reasons'].append(reason)
            metrics[label] = replace(metrics[label], status='unavailable', value=None, partial_value=None,
                                     fraction_of_window=None, reasons=(*metrics[label].reasons, reason))
    out['a'], out['b'] = metrics['a'], metrics['b']
    out['delta'] = delta_of(metrics['a'], metrics['b'])
    return out


def _jitter(pair, group_a, group_b, reasons_a, reasons_b, diameter, size, jitter_um, masks=None):
    offsets = [(jitter_um, 0.0), (-jitter_um, 0.0), (0.0, jitter_um), (0.0, -jitter_um)]
    values = []
    for dx, dy in offsets:
        wa = _square((pair.a.center_xy_um[0] + dx, pair.a.center_xy_um[1] + dy), size)
        wb = _square((pair.b.center_xy_um[0] + dx, pair.b.center_xy_um[1] + dy), size)
        delta = delta_of(measure_group_metric(group_a, 'union', diameter, wa, reasons_a, masks),
                         measure_group_metric(group_b, 'union', diameter, wb, reasons_b, masks))
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


def _overlap_components(bounds):
    """Connected components (indices) of rectangles that overlap; half-open touching is no overlap."""
    parent = list(range(len(bounds)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(bounds)):
        for j in range(i + 1, len(bounds)):
            if _rectangles_overlap(bounds[i], bounds[j]):
                parent[find(i)] = find(j)
    groups = {}
    for i in range(len(bounds)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def explained_fraction(rows, ready_pairs, areas_a, areas_b, sizes, masks=None):
    """PLAN_v3 T3b: Σ(window union CA × repeats_per_frame) / frame_area per side, against the
    product-level fraction_of_frame delta of the referenced groups. repeats_per_frame counts the
    repeats of the structure inside the measured frame (the 2×2 domain on the real path), never a
    per-unit count. Windows of one group that overlap are measured once as a union when their
    repeats agree; otherwise that diameter is unavailable."""
    if not ready_pairs:
        return {'status': 'unavailable', 'reasons': ['no_ready_pairs'], 'by_window_size': {}}
    missing = [p.pair_id for p in ready_pairs if p.repeats_per_frame is None]
    if missing:
        return {'status': 'unavailable', 'reasons': ['repeats_per_frame_required'], 'pairs_without_repeats': missing,
                'by_window_size': {}}
    repeats = {p.pair_id: p.repeats_per_frame for p in ready_pairs}
    by_size, statuses = {}, []
    for size in sizes:
        per_diameter = {}
        for diameter in DIAMETERS_UM:
            selected = [r for r in rows if r['window_size_um'] == size and diameter_key(r['diameter_um']) == diameter_key(diameter)]
            if not selected:
                continue
            reasons, sums, actual, unions = set(), {'a': 0.0, 'b': 0.0}, {'a': 0.0, 'b': 0.0}, []
            for label, areas in (('a', areas_a), ('b', areas_b)):
                by_group = {}
                for row in selected:
                    key = row[f'group_{label}']
                    if key is None:
                        reasons.add(f'group_missing_{label}:{row["pair_id"]}')
                        continue
                    by_group.setdefault(tuple(sorted(key.items())), []).append(row)
                for key, group_rows in by_group.items():
                    group = next((g for k, g in areas.groups.items() if tuple(sorted(asdict(k).items())) == key), None)
                    if group is None:
                        reasons.add(f'group_missing_{label}')
                        continue
                    fraction = group.fractions['union']
                    if fraction.status != 'available':
                        reasons.add(f'{label}_fraction_{fraction.status}')
                        continue
                    actual[label] += fraction.value
                    frame_area = group.frame_area_um2
                    bounds = [r['window']['bounds_um'][label] for r in group_rows]
                    for component in _overlap_components(bounds):
                        component_rows = [group_rows[i] for i in component]
                        pair_ids = [r['pair_id'] for r in component_rows]
                        counts = {repeats[pid][label] for pid in pair_ids}
                        if len(component) == 1:
                            metric = component_rows[0]['window'][label]['union']
                            if metric.status != 'available':
                                reasons.add(f'{label}_{metric.status}:{pair_ids[0]}')
                                continue
                            sums[label] += metric.value * counts.pop() / frame_area
                            continue
                        if len(counts) != 1:
                            reasons.add(f'overlapping_windows_with_different_repeats_{label}:' + ','.join(sorted(pair_ids)))
                            continue
                        state, _ = _group_state(group.union)
                        measured = _measure_cropped_windows(group.frame, group_mask(group, 'union', masks),
                                                            [PhysicalWindow(*r['window']['bounds_um'][label]) for r in component_rows],
                                                            diameter=diameter, analysis_layer=group.key.analysis_layer)
                        if measured.status != 'available' or state != 'available':
                            reasons.add(f'{label}_union_{measured.status if measured.status != "available" else state}:' + ','.join(sorted(pair_ids)))
                            continue
                        unions.append({'side': label, 'pair_ids': sorted(pair_ids), 'union_ca_um2': measured.ca_um2,
                                       'repeats': counts.pop()})
                        sums[label] += measured.ca_um2 * unions[-1]['repeats'] / frame_area
            available = not reasons
            statuses.append(available)
            per_diameter[str(float(diameter))] = {
                'status': 'available' if available else 'unavailable', 'reasons': sorted(reasons),
                'explained_a': sums['a'] if available else None, 'explained_b': sums['b'] if available else None,
                'explained_delta': (sums['b'] - sums['a']) if available else None,
                'actual_delta_fraction_of_frame': (actual['b'] - actual['a']) if available else None,
                'residual': ((actual['b'] - actual['a']) - (sums['b'] - sums['a'])) if available else None,
                'overlapping_windows_measured_as_union': unions}
        by_size[str(size)] = per_diameter
    status = 'available' if statuses and all(statuses) else 'partial' if any(statuses) else 'unavailable'
    reasons = ([] if status == 'available' else ['some_diameters_unavailable'] if status == 'partial'
               else ['all_diameters_unavailable'] if statuses else ['no_rows'])
    return {'status': status, 'reasons': reasons,
            'status_semantics': 'available = every window size × diameter complete; partial = some complete; unavailable = none',
            'unit': '1', 'direction': 'b_minus_a',
            'definition': 'Σ(window union CA × repeats_per_frame) / frame_area per side; actual = Σ fraction_of_frame(union) of the referenced groups; repeats count structures inside the measured frame, not per unit',
            'by_window_size': by_size}


CSV_ROW_COLUMNS = ('pair_id', 'window_size_um', 'diameter_um', 'kind', 'side_um', 'metric',
                   'a_value_um2', 'a_partial_value_um2', 'a_status', 'a_fraction_of_window',
                   'b_value_um2', 'b_partial_value_um2', 'b_status', 'b_fraction_of_window',
                   'delta_um2', 'delta_fraction', 'delta_status', 'delta_reasons')


def _csv_rows(result: LocalDeltaResult):
    """local_delta_rows.csv rows one by one (every row has exactly CSV_ROW_COLUMNS, in this order)."""
    for row in result.rows:
        for kind in WINDOW_KINDS:
            comparison = row[kind]
            for name in METRICS:
                a, b, d = comparison['a'][name], comparison['b'][name], comparison['delta'][name]
                yield {'pair_id': row['pair_id'], 'window_size_um': row['window_size_um'], 'diameter_um': row['diameter_um'],
                       'kind': kind, 'side_um': comparison.get('side_um'), 'metric': name,
                       'a_value_um2': a.value, 'a_partial_value_um2': a.partial_value, 'a_status': a.status,
                       'a_fraction_of_window': a.fraction_of_window,
                       'b_value_um2': b.value, 'b_partial_value_um2': b.partial_value, 'b_status': b.status,
                       'b_fraction_of_window': b.fraction_of_window,
                       'delta_um2': d.value, 'delta_fraction': d.fraction_delta, 'delta_status': d.status,
                       'delta_reasons': list(d.reasons)}
        named = row['named_pair']
        if named is not None and named['delta'] is not None:
            yield {'pair_id': row['pair_id'], 'window_size_um': row['window_size_um'], 'diameter_um': row['diameter_um'],
                   'kind': 'window', 'side_um': row['window_size_um'], 'metric': 'named_pair',
                   'a_value_um2': named['a'].value, 'a_partial_value_um2': named['a'].partial_value, 'a_status': named['a'].status,
                   'a_fraction_of_window': named['a'].fraction_of_window,
                   'b_value_um2': named['b'].value, 'b_partial_value_um2': named['b'].partial_value, 'b_status': named['b'].status,
                   'b_fraction_of_window': named['b'].fraction_of_window,
                   'delta_um2': named['delta'].value, 'delta_fraction': named['delta'].fraction_delta,
                   'delta_status': named['delta'].status, 'delta_reasons': list(named['delta'].reasons)}


LAZY_LIST_MIN = 32
JSON_CHUNK_BYTES = 1 << 16


class _LazyList(list):
    """A list for json.JSONEncoder.iterencode that converts each item with json_value only when the encoder reaches it, so
    the converted copy of a long list never exists as a whole. The real items live in `_items`; this list object is empty,
    which is why it must only be encoded through iterencode() (the C encoder used by dumps() without indent reads the
    empty storage): _write_json does."""

    def __init__(self, items):
        super().__init__()
        self._items = items

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        from .report import json_value
        for item in self._items:
            yield json_value(item)


def _lazy_json(value):
    """json_value(value) with every long list left to _LazyList; dict keys become str, tuples lists, as json_value does."""
    from .report import json_value
    if isinstance(value, dict):
        return {str(k): _lazy_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)) and len(value) > LAZY_LIST_MIN:
        return _LazyList(value)
    return json_value(value)


def _write_json(path, payload):
    """json.dumps(json_value(payload), ensure_ascii=False, indent=2, allow_nan=False) + newline, written in 64 KiB pieces."""
    encoder = json.JSONEncoder(ensure_ascii=False, indent=2, allow_nan=False)
    with staged_file(path) as handle:
        pending, size = [], 0
        for chunk in encoder.iterencode(_lazy_json(payload)):  # not _one_shot: the Python encoder walks _LazyList
            pending.append(chunk)
            size += len(chunk)
            if size >= JSON_CHUNK_BYTES:
                handle.write(''.join(pending))
                pending, size = [], 0
        pending.append('\n')
        handle.write(''.join(pending))


def _csv_cell(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            if value is None or isinstance(value, (dict, list, bool)) else value)


def _write_csv(path, rows, columns=None):
    """Rows (json_value'd one at a time) as CSV. Without `columns` the header is the keys in first-seen order over all
    rows (the rows are then held; a row without some key leaves that cell empty); with `columns` the rows are streamed and
    each must have exactly these keys. An empty table gets the placeholder header."""
    import csv
    from .report import json_value
    rows = (json_value(row) for row in rows)
    first = ()
    fixed = columns is not None
    if not fixed:
        rows = list(rows)
        columns = list(dict.fromkeys(k for row in rows for k in row)) or ['value', 'status', 'reasons']
    else:
        first = list(itertools.islice(rows, 1))
        columns = list(columns) if first else ['value', 'status', 'reasons']
    with staged_file(path, newline='') as handle:
        writer = csv.DictWriter(handle, columns, lineterminator='\n')
        writer.writeheader()
        for row in itertools.chain(first, rows):
            if fixed and list(row) != columns:
                raise ValueError(f'{Path(path).name}: row keys differ from the table columns')
            writer.writerow({k: _csv_cell(v) for k, v in row.items()})


def write_local_delta(out, result: LocalDeltaResult, *, compact=False):
    """local_delta.json/csv and window_sensitivity.json/csv; the HTML page is a separate writer.

    compact=True leaves the per-row nested metrics out of the JSON (they stay in
    local_delta_rows.csv) so tens of pairs × several windows do not produce tens of MB.
    Rows are converted and written one at a time (no second copy of the result); each file appears whole or not at all.
    compare-windows calls this with a scratch directory and publishes the whole set afterwards (publication.py).
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for prefix in ('local_delta', 'window_sensitivity'):
        for old in out.glob(prefix + '*'):
            if old.is_file() and old.suffix in ('.json', '.csv', '.html'):
                old.unlink()
    payload = result.payload()
    if compact:
        payload = {**payload, 'rows': None, 'json_detail': 'compact', 'rows_csv': 'local_delta_rows.csv',
                   'n_rows': len(result.rows)}
    else:
        payload = {**payload, 'json_detail': 'full', 'rows_csv': 'local_delta_rows.csv', 'n_rows': len(result.rows)}
    paths = [out / 'local_delta.json', out / 'local_delta_rows.csv']
    _write_json(paths[0], payload)
    _write_csv(paths[1], _csv_rows(result), CSV_ROW_COLUMNS)
    sensitivity = result.sensitivity
    rows = [{'pair_id': e['pair_id'], 'diameter_um': e['diameter_um'], 'sign_consistent': e['sign_consistent'],
             'max_abs_delta': e['max_abs_delta'], 'min_abs_delta': e['min_abs_delta'],
             **{f'delta_union_{size}um': value for size, value in e['delta_union_by_size'].items()}}
            for e in sensitivity['per_pair_diameter']]
    paths += [out / 'window_sensitivity.json', out / 'window_sensitivity_rows.csv',
              out / 'window_sensitivity_rank_agreement.csv']
    _write_json(paths[2], sensitivity)
    _write_csv(paths[3], rows)
    _write_csv(paths[4], sensitivity['rank_agreement'])
    return [str(path) for path in paths]


def correspondence_hash(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
