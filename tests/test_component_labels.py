"""Stage-4 connected components: same regions as the original set-of-tuples flood fill, bounded memory."""
import tracemalloc

import numpy as np
import pytest

from simcompare import electrode_map
from simcompare.adapters import real_hotspot


def _flood_fill_reference(active):
    """The original (HEAD) implementation, kept as the oracle: row-first seeds, 4-neighbour flood fill."""
    seeds = [tuple(map(int, cell)) for cell in np.argwhere(active)]
    remaining = set(seeds)
    for seed in seeds:
        if seed not in remaining:
            continue
        remaining.remove(seed)
        cells, stack = [seed], [seed]
        while stack:
            r, c = stack.pop()
            for other in ((r-1, c), (r+1, c), (r, c-1), (r, c+1)):
                if other in remaining:
                    remaining.remove(other)
                    cells.append(other)
                    stack.append(other)
        inside = np.zeros(active.shape, bool)
        rows, cols = zip(*cells)
        inside[rows, cols] = True
        yield inside, (min(rows), min(cols), max(rows)+1, max(cols)+1)


def _masks():
    rng = np.random.RandomState(7)
    cases = [np.zeros((0, 5), bool), np.zeros((4, 0), bool), np.zeros((6, 7), bool), np.ones((6, 7), bool),
             np.eye(9, dtype=bool), np.eye(9, dtype=bool)[::-1],                       # diagonal contact is NOT connected
             (np.indices((10, 11)).sum(axis=0) % 2 == 0),                              # checkerboard: every cell alone
             np.array([[1, 0, 1], [1, 1, 1], [0, 0, 0], [1, 1, 0]], bool),              # a U joined through the bottom row
             np.array([[1, 1, 0, 1, 1], [0, 1, 1, 1, 0], [0, 0, 0, 0, 0], [1, 0, 1, 0, 1]], bool)]
    for density in (0.1, 0.3, 0.5, 0.7, 0.95):
        for _ in range(12):
            cases.append(rng.rand(rng.randint(1, 30), rng.randint(1, 30)) < density)
    return cases


@pytest.mark.parametrize('index', range(len(_masks())))
def test_components_and_sizes_match_the_flood_fill(index):
    active = _masks()[index]
    expected = list(_flood_fill_reference(active))
    got = list(electrode_map._components(active))
    assert len(got) == len(expected)
    for (inside, bbox), (inside_ref, bbox_ref) in zip(got, expected):
        assert bbox == bbox_ref and np.array_equal(inside, inside_ref) and inside.dtype == bool
    sizes = electrode_map._component_sizes(active)
    assert sizes.tolist() == [int(inside.sum()) for inside, _ in expected]
    labelled = electrode_map._label_runs(active, electrode_map._run_counts(active))   # the shared labelling gives the same
    assert electrode_map._component_sizes(active, labelled).tolist() == sizes.tolist()
    again = list(electrode_map._components(active, labelled))
    assert [b for _, b in again] == [b for _, b in expected]


def _traced_peak(function):
    tracemalloc.start()
    try:
        function()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_component_count_memory_is_bounded_by_the_run_count_not_the_cells():
    dense = np.ones((400, 400), bool)                                  # one region of 160,000 cells (old code: 37.9 MB)
    checker = np.indices((400, 400)).sum(axis=0) % 2 == 0              # 80,000 one-cell runs, the run-count worst case
    for active in (dense, checker):
        runs = int(electrode_map._run_counts(active).sum())
        peak = _traced_peak(lambda: electrode_map._component_sizes(active))
        assert peak <= real_hotspot.MAP_RUN_BYTES * runs + 2 * active.size, (runs, peak)
    assert _traced_peak(lambda: electrode_map._component_sizes(dense)) < 1 << 20
