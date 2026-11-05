"""Opt-in condition_stats: integer cell counts per pair; default path unchanged."""
import hashlib
import json

import pytest

from simcompare.linepixel import compute_areas
from simcompare.numeric_schema import NumericContractError
from test_linepixel import group, make_case, oracle_cases

# sha256 of json.dumps(group.to_dict(), sort_keys=True) from baseline a05d19e (condition_stats absent)
GOLDEN_ORACLE_SHA256 = "adf033aed623f19a55dcb8dfe0a7c949508240aadd621e80e02cae915b2b3d64"


def digest(g):
    return hashlib.sha256(json.dumps(g.to_dict(), sort_keys=True).encode()).hexdigest()


def stats_of(cases, **kw):
    return group(compute_areas(cases, condition_stats=True, **kw)).condition_stats


def test_default_path_has_no_condition_stats_and_matches_baseline():
    g = group(compute_areas(oracle_cases(), chunk_cells=2))
    assert g.condition_stats is None and 'condition_stats' not in g.to_dict()
    if GOLDEN_ORACLE_SHA256 is not None:
        assert digest(g) == GOLDEN_ORACLE_SHA256
    on = group(compute_areas(oracle_cases(), chunk_cells=2, condition_stats=True)).to_dict()
    assert on.pop('condition_stats')
    assert on == g.to_dict()


@pytest.mark.parametrize('limits', [{'max_label_pattern_entries': 1}, {'max_bucket_output_entries': 1},
                                    {'max_bucket_output_entries': 3}])
def test_default_and_stats_paths_fail_or_succeed_alike(limits):
    outcome = []
    for flag in (False, True):
        try:
            compute_areas(oracle_cases(), condition_stats=flag, **limits)
            outcome.append(None)
        except NumericContractError as error:
            outcome.append(error.code)
    assert outcome[0] == outcome[1]


def test_conservation_and_hand_counts():
    # line A-B {0,1,2}, A-C {2,3}; pixel D-E {1,3,4}, E-F {4,5}; P&L = {1,3}, P_eff = {4,5}
    s = stats_of(oracle_cases(), chunk_cells=2)
    g = group(compute_areas(oracle_cases()))
    assert s['line'] == {'exclusive_cells': {'A-B': 2, 'A-C': 1}, 'shared_cells': 1}
    assert s['pixel'] == {'exclusive_cells': {'D-E': 0, 'E-F': 1}, 'shared_cells': 1}
    assert s['transfer_by_line_pair_cells'] == {'A-B': 1, 'A-C': 1}
    assert s['transfer_shared_cells'] == 0
    assert sum(s['line']['exclusive_cells'].values()) + s['line']['shared_cells'] == 4
    assert sum(s['pixel']['exclusive_cells'].values()) + s['pixel']['shared_cells'] == g.pixel.area.value
    assert sum(s['transfer_by_line_pair_cells'].values()) + s['transfer_shared_cells'] == g.transfer.value


@pytest.mark.parametrize('chunk', [1, 3, 8, 65536])
def test_chunk_size_does_not_change_counts(chunk):
    assert stats_of(oracle_cases(), chunk_cells=chunk) == stats_of(oracle_cases())


def test_line_fully_covers_pixel():
    cases = [make_case('l', 'A', 'B', {0, 1, 2, 3}), make_case('p', 'C', 'D', {1, 2}, 'pixel')]
    s = stats_of(cases)
    assert s['transfer_by_line_pair_cells'] == {'A-B': 2} and s['transfer_shared_cells'] == 0
    assert s['pixel'] == {'exclusive_cells': {'C-D': 0}, 'shared_cells': 0}
    assert s['line'] == {'exclusive_cells': {'A-B': 4}, 'shared_cells': 0}


def test_cell_in_two_line_and_two_pixel_pairs():
    cases = [make_case('l1', 'A', 'B', {2}), make_case('l2', 'A', 'C', {2}),
             make_case('p1', 'D', 'E', {2}, 'pixel'), make_case('p2', 'E', 'F', {2}, 'pixel')]
    s = stats_of(cases)
    assert s['line'] == {'exclusive_cells': {'A-B': 0, 'A-C': 0}, 'shared_cells': 1}
    assert s['transfer_by_line_pair_cells'] == {'A-B': 0, 'A-C': 0}
    assert s['transfer_shared_cells'] == 1
    assert s['pixel'] == {'exclusive_cells': {'D-E': 0, 'E-F': 0}, 'shared_cells': 0}


def test_summary_mode_keeps_every_pair():
    full = stats_of(oracle_cases())
    summary = group(compute_areas(oracle_cases(), bucket_detail='summary', top_buckets=1,
                                  condition_stats=True)).condition_stats
    assert summary == full


def test_class_without_pairs_is_empty():
    s = stats_of([make_case('l', 'A', 'B', {0, 1})])
    assert s['pixel'] == {'exclusive_cells': {}, 'shared_cells': 0}
    assert s['transfer_by_line_pair_cells'] == {'A-B': 0} and s['transfer_shared_cells'] == 0
    s = stats_of([make_case('p', 'A', 'B', {0, 1}, 'pixel')])
    assert s['line'] == {'exclusive_cells': {}, 'shared_cells': 0}
    assert s['pixel'] == {'exclusive_cells': {'A-B': 2}, 'shared_cells': 0}
    assert s['transfer_by_line_pair_cells'] == {} and s['transfer_shared_cells'] == 0
