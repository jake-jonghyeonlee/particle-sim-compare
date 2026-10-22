"""T1 oracle: the single-pass labelling implementation equals the pre-T1 code (tests/reference_linepixel.py).

Equality means AreaResult.to_dict() JSON, every retained mask, the pipeline's per-source CSV rows and the
NumericContractError code on invalid inputs. The reference keeps `bitorder=` (numpy >= 1.17) and is skipped below that.
"""
from dataclasses import replace
import itertools
import json
import math
from pathlib import Path
import random

import numpy as np
import pytest

import reference_linepixel as reference
from simcompare import linepixel
from simcompare.adapters.numeric import load_dataset
from simcompare.linepixel import FAILURE_PIECES, compute_areas
from simcompare.measurement import measure_full_mask
from simcompare.numeric_schema import (
    DIAMETERS_UM, CaseIdentity, Endpoint, Frame, MaskRecord, NumericCase, NumericContractError, SourceCA,
)
from test_linepixel import make_case, oracle_cases

pytestmark = pytest.mark.skipif(tuple(int(p) for p in np.__version__.split('.')[:2]) < (1, 17),
                                reason='reference oracle uses np.packbits(bitorder=), numpy >= 1.17')

MASKS = ('line_mask', 'pixel_effective_mask', 'transfer_mask', 'union_mask')


def _outcome(function, cases, **kwargs):
    try:
        return function(cases, **kwargs), None
    except NumericContractError as error:
        return None, (error.code, str(error))


def _rows(result):
    """The per-source rows of pipeline.run_numeric_compare's real path (fields taken from the area groups)."""
    rows = []
    for group in result.groups.values():
        for source in group.source_cases:
            raw_records = source.raw.get('records', []) if isinstance(source.raw, dict) else []
            raw_record = next((r for r in raw_records if r.get('diameter_um') == source.diameter_um), {})
            rows.append({'group': group.key.__dict__, 'status': group.union.status, 'union_um2': group.union.value,
                         'partial_union_um2': group.union.partial_value, 'line_um2': group.line.area.value,
                         'pixel_effective_um2': group.pixel.area.value, 'transfer_um2': group.transfer.value,
                         'unmapped_pair_count': group.coverage.unmapped, 'missing_count': group.coverage.missing,
                         'case_id': source.identity.case_id, 'case_area_um2': source.measurement.ca_um2,
                         'case_partial_area_um2': source.measurement.partial_value,
                         'csv_status': raw_record.get('csv_status'), 'reasons': list(source.measurement.reasons)})
    return json.dumps(rows, sort_keys=True)


def assert_equivalent(cases, **kwargs):
    """Run both implementations; assert identical output or identical error. Returns the new result (or None)."""
    cases = cases if hasattr(cases, 'cases') else list(cases)
    expected, expected_error = _outcome(reference.compute_areas, cases, **kwargs)
    actual, actual_error = _outcome(compute_areas, cases, **kwargs)
    assert actual_error == expected_error
    if expected is None:
        return None
    assert json.dumps(actual.to_dict(), sort_keys=True) == json.dumps(expected.to_dict(), sort_keys=True)
    assert actual.to_dict() == expected.to_dict()
    assert _rows(actual) == _rows(expected)
    assert set(actual.groups) == set(expected.groups)
    for key, old in expected.groups.items():
        new = actual.groups[key]
        for name in MASKS:
            got = getattr(new, name)
            assert got.dtype == bool and not got.flags.writeable
            assert got.shape == old.frame.shape_yx
            assert np.array_equal(got, getattr(old, name)), name
        assert list(new.failure_piece_masks) == list(old.failure_piece_masks)
        for name in FAILURE_PIECES:
            assert np.array_equal(new.failure_piece_masks[name], old.failure_piece_masks[name]), name
            assert not new.failure_piece_masks[name].flags.writeable
    return actual


# ---------------------------------------------------------------- hand-written and fixture cases

def test_oracle_cases_all_permutations_and_chunk_sizes():
    cases = oracle_cases()
    for chunk in (1, 2, 65536):
        for order in itertools.permutations(cases):
            assert_equivalent(order, chunk_cells=chunk)


def test_numeric_v2_fixture():
    path = Path(__file__).parent / 'fixtures/numeric_v2'
    assert assert_equivalent(load_dataset(path)) is not None


def test_failure_piece_hand_written_mix():
    cases = [make_case('lat_AB', 'A', 'B', {0, 1, 2}, width=7), make_case('lat_AC', 'A', 'C', {2, 3}, width=7),
             make_case('vert_AD', 'A', 'D', {3, 4}, 'pixel', width=7, failure_type='vertical_short'),
             make_case('vert_EF', 'E', 'F', {6}, 'pixel', width=7, failure_type='vertical_short')]
    assert_equivalent(cases)
    assert_equivalent([make_case('lat', 'A', 'B', {0}, width=1), make_case('vert', 'B', 'A', {0}, width=1,
                                                                            failure_type='vertical_short')])


def test_identical_nested_and_duplicate_pairs():
    cases = [make_case('ab', 'A', 'B', {0, 1, 2, 3}), make_case('cd', 'C', 'D', {0, 1, 2, 3}),   # identical, dead labels
             make_case('ef', 'E', 'F', {1, 2}), make_case('ab2', 'B', 'A', {5}),                  # nested + duplicate pair
             make_case('gh', 'G', 'H', {0, 1, 2, 3, 4, 5}, 'pixel'), make_case('ij', 'I', 'J', {2, 4}, 'pixel')]
    result = assert_equivalent(cases)
    g = [v for k, v in result.groups.items() if k.diameter_um == 5][0]
    assert any(len(b.pair_ids) == 3 for b in g.line.shared)


def test_all_false_and_all_invalid_frames():
    assert_equivalent([make_case('a', 'A', 'B', set()), make_case('b', 'C', 'D', set(), 'pixel')])
    assert_equivalent([make_case('a', 'A', 'B', {0, 1}, valid=[[0] * 6]), make_case('b', 'C', 'D', {1}, 'pixel',
                                                                                   valid=[[0] * 6])])


def test_line_fully_covers_pixel_and_masks_outside_valid():
    cases = [make_case('l', 'A', 'B', {0, 1, 2, 3, 4, 5}), make_case('p', 'C', 'D', {1, 2}, 'pixel'),
             make_case('q', 'D', 'E', {2, 3}, 'pixel')]
    result = assert_equivalent(cases)
    g = [v for k, v in result.groups.items() if k.diameter_um == 5][0]
    assert g.pixel.area.value == 0 and not g.pixel.exclusive and not g.pixel.shared
    valid = [[1, 0, 1, 0, 1, 1]]
    assert_equivalent([make_case('a', 'A', 'B', {0, 1, 2, 3}, valid=valid), make_case('b', 'B', 'C', {1, 3, 4}, valid=valid),
                       make_case('c', 'C', 'D', {0, 1}, 'pixel', valid=valid)])


@pytest.mark.parametrize('width', [1, 7, 8, 9, 15, 17, 63, 65])
def test_non_byte_aligned_widths(width):
    rng = random.Random(width)
    cases = [make_case(f'c{i}', f'A{i}', f'B{i}', {c for c in range(width) if rng.random() < .4},
                       'line' if i % 2 else 'pixel', width=width) for i in range(5)]
    assert_equivalent(cases)


def test_more_than_64_pairs():
    rng = random.Random(64)
    width = 90
    cases = [make_case(f'c{i}', f'A{i}', f'B{i}', {c for c in range(width) if rng.random() < .15},
                       'line' if i % 5 else 'pixel', width=width) for i in range(70)]
    result = assert_equivalent(cases)
    g = [v for k, v in result.groups.items() if k.diameter_um == 5][0]
    assert max(len(b.pair_ids) for b in g.line.shared) > 2


def test_missing_only_and_unmapped_inventories():
    missing = make_case('m', 'A', 'B', {0})
    missing.records = {d: replace(r, mask=None, input_status='missing', reasons=('asset_missing',))
                       for d, r in missing.records.items()}
    assert_equivalent([missing])
    assert_equivalent([missing, make_case('u', 'C', 'D', {1}, kind=None), make_case('o', 'E', 'F', {2})])


def test_wrong_source_ca_is_rejected_identically():
    case = make_case('a', 'A', 'B', {0})
    r = case.records[5]
    case.records[5] = replace(r, source_ca=SourceCA(2, r.frame.frame_id, r.frame.valid_hash))
    assert_equivalent([case, make_case('b', 'B', 'C', {1, 2})])


@pytest.mark.parametrize('cases', [
    lambda: [make_case('a', 'A', 'B', {0}, pair_id='same'), make_case('b', 'C', 'D', {1}, pair_id='same')],
    lambda: [make_case('a', 'A', 'B', {0}), make_case('b', 'B', 'A', {0}, kind='pixel')],
    lambda: [make_case('a', 'A', 'B', {0}), make_case('b', 'A', 'C', {1}, pitch=0.5)],
    lambda: [make_case('a', 'A', 'B', {0}), make_case('b', 'A', 'C', {1}, valid=[[1, 0, 1, 1, 1, 1]])],
])
def test_error_codes_match(cases):
    got = assert_equivalent(cases())
    assert got is None


# ---------------------------------------------------------------- seeded randomized generator

def _frame(frame_id, shape, pitch, origin, valid):
    return Frame(frame_id, pitch, origin, shape, valid.copy())


def random_cases(rng):
    """Small 2-D frames with holes, fractional pitch/origin, distinct-but-equal Frame objects per record."""
    h, w = rng.randint(1, 7), rng.randint(1, 9)
    pitch = rng.choice([1, 0.5, 0.37])
    origin = rng.choice([(0, 0), (1.3, -2.1)])
    hole = rng.choice([0, 0, .25, 1.0])
    valid = np.array([[rng.random() >= hole for _ in range(w)] for _ in range(h)])
    electrodes = [f'E{i}' for i in range(rng.randint(3, 7))]
    classes, cases = {}, []
    layer = 'M2'

    def random_mask():
        mode = rng.random()
        mask = np.zeros((h, w), bool)
        if mode < .15:
            return mask
        if mode < .35:
            return np.array([[rng.random() < .5 for _ in range(w)] for _ in range(h)])
        for _ in range(rng.randint(1, 2)):
            y0, x0 = rng.randrange(h), rng.randrange(w)
            mask[y0:rng.randint(y0 + 1, h), x0:rng.randint(x0 + 1, w)] = True
        return mask

    def build(index, a, b, pair_id, classification, failure_type, shift_valid=False):
        identity = CaseIdentity('product', 'design', 'source', 'step', 'exposure', f'case{index}', 'frame',
                                'synthetic_v2')
        endpoints = (Endpoint(a, 'gate', 'M1' if failure_type == 'vertical_short' else layer), Endpoint(b, 'data', layer))
        raw = {'case_id': f'case{index}', 'fab_pair_key': f'RAW:{a}-{b}', 'records': []}
        metadata = {'unit_id': 'unit', 'height_um': 3 if failure_type == 'vertical_short' else None,
                    'height_evidence': 'synthetic height' if failure_type == 'vertical_short' else None, 'records': []}
        active = set(rng.sample(DIAMETERS_UM, rng.randint(1, 3)))
        records = {}
        for d in DIAMETERS_UM:
            local = valid.copy()
            if shift_valid:
                local[0, 0] = not local[0, 0]
            frame = _frame('frame', (h, w), pitch, origin, local)
            na = failure_type == 'vertical_short' and d < 3
            if na:
                records[d] = MaskRecord(d, frame, None, 'not_applicable', ('below_height_vertical_short',),
                                        identity=identity, analysis_layer=layer)
            elif d in active and rng.random() < .1:
                records[d] = MaskRecord(d, frame, None, 'missing', ('asset_missing',), identity=identity,
                                        analysis_layer=layer)
            else:
                mask = random_mask() if d in active else np.zeros((h, w), bool)
                source_ca = None
                if d in active and rng.random() < .3:
                    value = float(np.count_nonzero(mask & local)) * pitch ** 2
                    source_ca = SourceCA(value + (1 if rng.random() < .2 else 0), 'frame', frame.valid_hash)
                records[d] = MaskRecord(d, frame, mask, 'observed', (), source_ca=source_ca, identity=identity,
                                        analysis_layer=layer)
            metadata['records'].append({'diameter_um': d, 'applicability_evidence': {
                'height_um': 3, 'comparison': 'diameter_um < height_um', 'source': 'synthetic'} if na else None})
            raw['records'].append({'diameter_um': d, 'csv_status': 'ok'})
        return NumericCase(identity, endpoints, failure_type, pair_id, classification, layer, 'density-' + layer,
                           records, raw, metadata)

    index = 0
    for _ in range(rng.randint(1, 8)):
        a, b = rng.sample(electrodes, 2)
        pair = tuple(sorted((a, b)))
        classes.setdefault(pair, rng.choice(['line', 'line', 'pixel']))
        for _ in range(rng.randint(1, 3)):
            ends = (a, b) if rng.random() < .5 else (b, a)
            cases.append(build(index, *ends, '-'.join(pair), classes[pair], rng.choice(['lateral_short', 'vertical_short'])))
            index += 1
    for _ in range(rng.choice([0, 0, 1, 2])):   # unmapped and same-net cases
        a, b = rng.choice([(electrodes[0], electrodes[1]), (electrodes[0], electrodes[0])])
        cases.append(build(index, a, b, None, None, rng.choice(['lateral_short', 'vertical_short'])))
        index += 1
    invalid = rng.random()
    if invalid < .06:
        cases.append(build(index, 'X1', 'X2', cases[0].pair_id or 'P', 'line', 'lateral_short'))   # pair id collision
    elif invalid < .12:
        cases.append(build(index, 'X1', 'X2', 'X1-X2', 'line', 'lateral_short', shift_valid=True))  # incompatible frame
    rng.shuffle(cases)
    return cases


def test_seeded_randomized_equivalence_300_iterations():
    outcomes = {'ok': 0, 'error': 0}
    for seed in range(300):
        cases = random_cases(random.Random(seed))
        try:
            result = assert_equivalent(cases)
        except AssertionError:
            raise AssertionError(f'seed {seed}')
        outcomes['ok' if result is not None else 'error'] += 1
    assert outcomes['ok'] > 200 and outcomes['error'] > 0


def test_randomized_equivalence_with_forced_label_compaction(monkeypatch):
    monkeypatch.setattr(linepixel, '_COMPACT_MIN_HISTORY', 2)
    monkeypatch.setattr(linepixel, '_COMPACT_FACTOR', 1)
    for seed in range(1000, 1060):
        assert_equivalent(random_cases(random.Random(seed)))


# ---------------------------------------------------------------- B2: bounded labels and output

def _brute_force_counts(masks, keep):
    ids = sorted(masks)
    stacked = np.stack([masks[pid].reshape(-1) for pid in ids])
    keep = keep.reshape(-1)
    cells = np.flatnonzero(keep & stacked.any(axis=0))
    counts = {}
    for cell in cells:
        members = tuple(ids[i] for i in np.flatnonzero(stacked[:, cell]))
        counts[members] = counts.get(members, 0) + 1
    return counts


def _partition(masks, keep, stats=None, **kwargs):
    ids = sorted(masks)
    flat = keep.reshape(-1)
    return linepixel._partition_labels(
        ids, lambda pid: np.flatnonzero(masks[pid].reshape(-1) & flat), flat.size, stats=stats, **kwargs)


def test_partition_checkerboards_and_independent_random_masks(monkeypatch):
    monkeypatch.setattr(linepixel, '_COMPACT_MIN_HISTORY', 64)
    monkeypatch.setattr(linepixel, '_COMPACT_FACTOR', 2)
    rng = np.random.RandomState(7)
    yy, xx = np.mgrid[0:64, 0:64]
    masks = {f'p{i:03d}': rng.rand(64, 64) < .5 for i in range(100)}
    masks['p000'] = (yy + xx) % 2 == 0
    masks['p001'] = (yy + xx) % 2 == 1
    keep = np.ones((64, 64), bool)
    keep[::7, ::5] = False
    stats = {}
    label, counts, ids = _partition(masks, keep, stats)
    assert counts == _brute_force_counts(masks, keep)
    assert label.dtype == np.int32 and stats['compactions'] > 0
    assert stats['final_live'] > 1000          # almost every cell has its own pattern
    # every history entry is an ancestor of a live label: history <= sum of live pattern lengths + 1
    assert stats['peak_history'] <= sum(len(m) for m in counts) + 1
    assert sum(counts.values()) == int((keep & np.any(list(masks.values()), axis=0)).sum())


def test_partition_nested_identical_and_dead_labels():
    base = np.zeros((6, 6), bool)
    base[1:5, 1:5] = True
    masks = {'a': base.copy(), 'b': base.copy(), 'c': base.copy(), 'd': np.zeros((6, 6), bool)}
    masks['c'][2:4, 2:4] = False
    stats = {}
    label, counts, ids = _partition(masks, np.ones((6, 6), bool), stats)
    assert counts == {('a', 'b', 'c'): 12, ('a', 'b'): 4}
    assert counts == _brute_force_counts(masks, np.ones((6, 6), bool))
    assert set(counts) == set(ids)           # only live, non-zero labels are reported
    assert () not in counts and 'd' not in {p for m in counts for p in m}


def test_partition_checks_are_threshold_independent_and_int32(monkeypatch):
    rng = np.random.RandomState(3)
    masks = {f'p{i:02d}': rng.rand(20, 20) < .4 for i in range(40)}
    keep = np.ones((20, 20), bool)
    results = []
    for threshold in (1, 8, 1 << 16):
        monkeypatch.setattr(linepixel, '_COMPACT_MIN_HISTORY', threshold)
        monkeypatch.setattr(linepixel, '_COMPACT_FACTOR', 1)
        stats = {}
        label, counts, _ = _partition(masks, keep, stats)
        assert label.dtype == np.int32
        results.append((counts, stats))
    assert results[0][0] == results[1][0] == results[2][0] == _brute_force_counts(masks, keep)
    assert results[0][1]['compactions'] > results[2][1]['compactions'] == 0


def test_partition_identical_masks_history_equals_live_pattern_length(monkeypatch):
    monkeypatch.setattr(linepixel, '_COMPACT_MIN_HISTORY', 4)
    masks = {f'p{i:03d}': np.ones((8, 8), bool) for i in range(200)}   # every step moves all cells to a new label
    stats = {}
    label, counts, _ = _partition(masks, np.ones((8, 8), bool), stats)
    assert counts == {tuple(sorted(masks)): 64} and label.dtype == np.int32
    assert stats['peak_history'] == 201 == sum(len(m) for m in counts) + 1


def test_partition_live_limit_stops_before_unbounded_growth(monkeypatch):
    monkeypatch.setattr(linepixel, '_COMPACT_MIN_HISTORY', 4)
    rng = np.random.RandomState(1)
    masks = {f'p{i:02d}': rng.rand(30, 30) < .5 for i in range(30)}

    def oversize(n):
        return NumericContractError('oversized_real_bucket_output', str(n))
    stats = {}
    with pytest.raises(NumericContractError, match='oversized_real_bucket_output'):
        _partition(masks, np.ones((30, 30), bool), stats, live_limit=500, on_oversize=oversize)
    assert stats['peak_history'] < 5 * 500


def test_partition_live_limit_is_exact_and_bounds_history(monkeypatch):
    rng = np.random.RandomState(2)
    masks = {f'p{i:02d}': rng.rand(30, 30) < .5 for i in range(30)}
    keep = np.ones((30, 30), bool)
    _, counts, _ = _partition(masks, keep)
    live = sum(len(members) for members in counts)
    oversize = lambda n: NumericContractError('oversized_real_label_patterns', str(n))
    _partition(masks, keep, live_limit=live, on_oversize=oversize)           # exactly at the limit passes
    with pytest.raises(NumericContractError, match='oversized_real_label_patterns'):
        _partition(masks, keep, live_limit=live - 1, on_oversize=oversize)   # checked even below the history threshold
    monkeypatch.setattr(linepixel, '_COMPACT_MIN_HISTORY', 4)
    stats = {}
    with pytest.raises(NumericContractError):
        _partition(masks, keep, stats, live_limit=500, on_oversize=oversize)
    assert stats['peak_history'] < 500 + 900     # limit plus one refinement step (<= 30 x 30 labels)


def _entry_count(group):
    return sum(len(b.pair_ids) + len(b.pair_lineage_case_keys) + len(b.contributing_source_case_keys)
               for share in (group.line, group.pixel) for b in (*share.exclusive, *share.shared))


def test_bucket_output_budget_error_and_exact_limit():
    cases = oracle_cases()
    result = compute_areas(cases)
    g = [v for k, v in result.groups.items() if k.diameter_um == 5][0]
    entries = _entry_count(g)
    assert entries > 0
    assert compute_areas(cases, max_bucket_output_entries=entries).to_dict() == result.to_dict()
    with pytest.raises(NumericContractError) as error:
        compute_areas(cases, max_bucket_output_entries=entries - 1)
    assert error.value.code == 'oversized_real_bucket_output'
    assert 'M2 d=5.0: ' in str(error.value) and f'limit {entries - 1}' in str(error.value)
    n = int(str(error.value).split('d=5.0: ')[1].split(' entries')[0])
    assert entries - 1 < n <= entries


@pytest.mark.parametrize('limit', [0, -1, True, 1.5, '5'])
def test_bucket_output_limit_validation(limit):
    with pytest.raises(NumericContractError) as error:
        compute_areas(oracle_cases(), max_bucket_output_entries=limit)
    assert error.value.code == 'invalid_resource_limit'


def test_default_budget_is_documented_constant():
    assert linepixel.DEFAULT_MAX_BUCKET_OUTPUT_ENTRIES == 1_000_000


# ---------------------------------------------------------------- packed retention, raw sharing, measurement cache

def test_retained_masks_are_packed_fresh_and_read_only():
    case = make_case('a', 'A', 'B', {0, 1, 2}, width=13)
    g = [v for k, v in compute_areas([case]).groups.items() if k.diameter_um == 5][0]
    assert g.line_packed.bits.nbytes == math.ceil(13 / 8)
    first, second = g.line_mask, g.line_mask
    assert first is not second and np.array_equal(first, second) and not first.flags.writeable
    with pytest.raises(ValueError):
        first[0, 0] = False
    assert not hasattr(g, 'bitorder')


def test_raw_is_copied_once_per_case_and_shared_by_its_diameters():
    case = make_case('a', 'A', 'B', {0, 1})
    result = compute_areas([case])
    raws = [s.raw for g in result.groups.values() for s in g.source_cases]
    assert len(raws) == len(DIAMETERS_UM) and all(r is raws[0] for r in raws)
    assert raws[0] == case.raw and raws[0] is not case.raw
    case.raw['case_id'] = 'changed'
    assert raws[0]['case_id'] == 'a'


def test_measurement_cache_is_bit_identical_with_fractional_geometry_and_equal_frames():
    rng = np.random.RandomState(11)
    h, w = 9, 11
    valid = rng.rand(h, w) > .2
    pitch, origin = 0.37, (1.3, -2.1)
    cache = {}
    for _ in range(20):
        frame = Frame('f', pitch, origin, (h, w), valid.copy())    # distinct but equal Frame objects
        record = MaskRecord(5, frame, rng.rand(h, w) > .5)
        plain = measure_full_mask(record)
        cached = measure_full_mask(record, area_cache=cache)
        assert plain == cached
        assert [plain.ca_um2, plain.observed_ca_um2, plain.valid_area_um2] == \
            [cached.ca_um2, cached.observed_ca_um2, cached.valid_area_um2]
    assert len(cache) == 1


def test_measurement_cache_is_bound_to_the_frame():
    """A1: a cache shared by records of different frames (same window bounds) must not reuse the other's cell areas."""
    rng = np.random.RandomState(5)
    h, w = 8, 8
    mask = rng.rand(h, w) > .3
    cache = {}
    results = []
    for index, pitch in enumerate((0.5, 0.5, 0.25)):
        valid = np.ones((h, w), bool)
        if index == 1:
            valid[:, :3] = False           # same geometry and window bounds as frame 0, different valid area
        frame = Frame('f', pitch, (0.0, 0.0), (h, w), valid)
        record = MaskRecord(5, frame, mask)
        results.append((measure_full_mask(record, area_cache=cache), measure_full_mask(record)))
    for cached, plain in results:
        assert cached == plain
    assert results[0][0].valid_area_um2 != results[1][0].valid_area_um2


# ---------------------------------------------------------------- real_hotspot temporary dataset

def test_real_hotspot_temp_dataset(tmp_path):
    from simcompare.adapters.real_hotspot import configured_dataset, import_hotspots, load_real_manifest
    from test_real_hotspot import _assignment, _source, _write_pairs
    root = tmp_path / 'src'
    _source(root, csv_area='0.014')
    imported = tmp_path / 'imported'
    import_hotspots(root, imported)
    _write_pairs(imported)
    folder, manifest = imported, load_real_manifest(imported)
    # the oracle computes full bucket detail, which is also compute_areas' default only for a dataset admitted as full
    dataset = configured_dataset(folder, manifest, _assignment(), top_buckets=None)
    assert_equivalent(dataset)
    dataset = configured_dataset(folder, manifest, _assignment(pair_table=False), top_buckets=None)
    assert_equivalent(dataset)


# ---------------------------------------------------------------- R5: small limits with the production thresholds

@pytest.mark.parametrize('limit', [1, 50, 500, 5000])
def test_small_live_limit_bounds_history_with_production_threshold(limit):
    """No monkeypatch: _COMPACT_MIN_HISTORY is 65,536 and must not hide a smaller live limit."""
    assert linepixel._COMPACT_MIN_HISTORY == 1 << 16
    rng = np.random.RandomState(11)
    masks = {f'p{i:02d}': rng.rand(64, 64) < .5 for i in range(40)}      # ~4096 labels after a few steps
    reported = []
    oversize = lambda n: reported.append(n) or NumericContractError('oversized_real_label_patterns', str(n))
    stats = {}
    with pytest.raises(NumericContractError, match='oversized_real_label_patterns'):
        _partition(masks, np.ones((64, 64), bool), stats, live_limit=limit, on_oversize=oversize)
    assert stats['peak_history'] <= limit + 1          # the step that would pass the limit is priced before it grows
    assert len(reported) == 1 and reported[0] > limit


def test_small_live_limit_exact_boundary_passes_with_production_threshold():
    rng = np.random.RandomState(12)
    masks = {f'p{i:02d}': rng.rand(16, 16) < .5 for i in range(12)}
    keep = np.ones((16, 16), bool)
    _, counts, _ = _partition(masks, keep)
    live = sum(len(members) for members in counts)
    oversize = lambda n: NumericContractError('oversized_real_label_patterns', str(n))
    stats = {}
    _, again, _ = _partition(masks, keep, stats, live_limit=live, on_oversize=oversize)
    assert again == counts and stats['peak_history'] <= live + 1
    with pytest.raises(NumericContractError):
        _partition(masks, keep, live_limit=live - 1, on_oversize=oversize)


def test_oracle_keeps_its_own_frozen_case_validation():
    """A2: the oracle must not follow later edits of the production _validate_case."""
    assert reference._validate_case is not linepixel._validate_case
    assert reference._validate_case.__module__ == reference.__name__
