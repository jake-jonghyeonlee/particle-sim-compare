"""Phase B T3: stage-6 compute at scale (bounded caches, lazy layouts, admission) and the stage-2 mask artefact (Q3).

Equality with the frozen 7bcb07a package is the contract (tests/test_stage46_equivalence.py runs the whole CLI both ways);
this file pins the NEW machinery: the owner-scoped unpack cache, the named-case index and per-pair record cache, the lazy
`_Assets` (same failure order, bounded decodes), the phase-wise stage-6 guard (peak <= estimate, one byte below refuses before
any expensive call), and `comparison_masks.zip` (reuse == recompute, stale/corrupt -> recompute, transactional publication).
"""
import dataclasses
import errno
import hashlib
import json
from pathlib import Path
import shutil
import tracemalloc
import zipfile

import numpy as np
import pytest

import test_stage46_equivalence as eq
from test_comparison_v2 import _Died, _fault_injection, _recover_only
import simcompare
import simcompare.adapters.real_hotspot as real_hotspot
from simcompare import compare_masks, linepixel, local_delta, local_delta_report, pipeline
from simcompare.local_delta import MaskCache, NamedCaseIndex, _named_case, compute_local_delta
from simcompare.numeric_schema import NumericContractError

gen = eq.gen
PKG = eq.PKG
CLEAN = eq.CLEAN


@pytest.fixture(scope='module')
def stage46(tmp_path_factory):
    class Factory:
        def scenario(self, name):
            return gen.scenario(tmp_path_factory, name)

        def clone(self, name, label, dest=None):
            return gen.clone_scenario(tmp_path_factory, name, label, dest)

        def run_dir(self, label):
            return Path(tmp_path_factory.mktemp(label))
    return Factory()


@pytest.fixture(autouse=True)
def pinned_clock(monkeypatch):
    for prefix in eq.PREFIXES:
        monkeypatch.setattr(eq.module(prefix, 'confirm_pairs'), 'utc_now_iso', lambda: eq.FIXED_CLOCK)


def inputs(stage46, name, *, n_pairs=8, sizes=(0.3, 0.8), jitter=0.1, seed=3, prefix='simcompare', **corr):
    corr = {**dict(seed=seed, n_pairs=n_pairs, repeats='uniform'), **(corr or CLEAN)}
    cfg, correspondence = eq.make_windows_inputs(stage46, name, corr, {}, sizes, dict(jitter=jitter))
    _, reference, target, pairs = eq._load_inputs(prefix, cfg, correspondence)
    return cfg, correspondence, reference, target, pairs


def areas_of(prefix, dataset):
    return eq.module(prefix, 'linepixel').compute_areas(dataset, bucket_detail='summary', top_buckets=real_hotspot.STAGE6_TOP_BUCKETS)


def run(prefix, pairs, reference, target, sizes, **kwargs):
    return eq.module(prefix, 'local_delta').compute_local_delta(pairs, reference, target, window_sizes_um=list(sizes), **kwargs)


# --- 1. the unpack cache (B5) -------------------------------------------------------------------------------------

def packed(seed, shape=(7, 9)):
    return linepixel.PackedMask(shape, linepixel._pack(np.random.RandomState(seed).rand(*shape) < 0.5))


def test_mask_cache_is_lru_bounded_and_returns_the_fresh_unpack():
    masks = [packed(i) for i in range(5)]
    cache = MaskCache(max_entries=3)
    for m in masks:
        assert np.array_equal(cache.get(m), m.unpack())
    assert len(cache) == 3 and cache.unpacks == 5 and cache.hits == 0
    assert cache.get(masks[4]) is cache.get(masks[4]) and cache.hits == 2          # most recent are kept
    cache.get(masks[0])                                                            # evicted earlier: unpacked again
    assert cache.unpacks == 6 and len(cache) == 3
    cache.clear()
    assert len(cache) == 0
    assert MaskCache(max_entries=0).get(masks[0]) is not None and len(MaskCache(max_entries=0)) == 0


def test_mask_cache_keeps_its_owner_alive_so_an_id_is_never_reused():
    import gc
    import weakref
    cache = MaskCache(max_entries=4)
    owner = packed(1)
    ref = weakref.ref(owner)
    cache.get(owner)
    del owner
    gc.collect()
    assert ref() is not None                   # the entry holds the PackedMask: its id cannot be handed to another object
    cache.clear()
    gc.collect()
    assert ref() is None                       # and nothing outlives the invocation's cache


def test_cached_arrays_are_read_only_and_group_mask_defaults_to_a_fresh_unpack(stage46):
    _, _, reference, _, _ = inputs(stage46, 'shared')
    areas = areas_of('simcompare', reference)
    group = next(iter(areas.groups.values()))
    cache = MaskCache()
    for name in local_delta.METRICS:
        cached = local_delta.group_mask(group, name, cache)
        fresh = local_delta.group_mask(group, name)
        assert not cached.flags.writeable and np.array_equal(cached, fresh)
        assert local_delta.group_mask(group, name, cache) is cached


class Counting:
    """Count calls of a function/method by replacing it (restored by monkeypatch)."""

    def __init__(self, monkeypatch, owner, attr):
        self.calls = 0
        real = getattr(owner, attr)

        def wrapped(*args, **kwargs):
            self.calls += 1
            return real(*args, **kwargs)
        monkeypatch.setattr(owner, attr, wrapped)


RUN_SPECS = [('shared', 8, (0.3, 0.8)), ('components', 6, (0.4, 8.0)), ('multilayer', 6, (0.5, 1.0))]


@pytest.mark.parametrize('name,n_pairs,sizes', RUN_SPECS)
def test_unpacks_are_bounded_by_distinct_masks_and_far_below_the_frozen_count(stage46, monkeypatch, name, n_pairs, sizes):
    cfg_path, correspondence = eq.make_windows_inputs(stage46, name, dict(seed=3, n_pairs=n_pairs, repeats='uniform', **CLEAN),
                                                      {}, sizes, dict(jitter=0.1))
    counts, results = {}, {}
    for prefix in eq.PREFIXES:
        _, reference, target, pairs = eq._load_inputs(prefix, cfg_path, correspondence)
        areas = (areas_of(prefix, reference), areas_of(prefix, target))
        counter = Counting(monkeypatch, eq.module(prefix, 'linepixel').PackedMask, 'unpack')
        results[prefix] = run(prefix, pairs, reference, target, sizes, jitter_um=0.1, sort_diameter_um=1, areas=areas)
        counts[prefix] = counter.calls
        monkeypatch.undo()
    assert not eq.deep_diff(results['simcompare'], results[PKG])
    rows = sum(1 for row in results['simcompare'].rows if row['group_a'] is not None or row['group_b'] is not None)
    assert 0 < counts['simcompare'] <= 9 * rows, counts          # ~its own 8 masks (4 per side) per row (+ explained-union uses), never 24
    assert counts['simcompare'] < 0.6 * counts[PKG], counts      # the frozen code unpacks at every use (window, core, jitter)


def test_cache_constants_agree_between_the_runtime_and_the_guard():
    assert local_delta.UNPACK_CACHE_ENTRIES == real_hotspot.STAGE6_UNPACK_CACHE_ENTRIES
    assert local_delta_report.LAYOUT_CACHE_CASES == real_hotspot.STAGE6_LAYOUT_CACHE_CASES
    assert local_delta_report.PAGE_MASK_CACHE_ENTRIES == real_hotspot.STAGE6_PAGE_MASK_ENTRIES


def test_a_cache_covering_every_group_unpacks_each_mask_once(stage46, monkeypatch):
    cfg_path, correspondence = eq.make_windows_inputs(stage46, 'shared', dict(seed=3, n_pairs=8, repeats='uniform', **CLEAN),
                                                      {}, (0.3, 0.8), dict(jitter=0.1))
    monkeypatch.setattr(local_delta, 'UNPACK_CACHE_ENTRIES', 256)
    _, reference, target, pairs = eq._load_inputs('simcompare', cfg_path, correspondence)
    areas = (areas_of('simcompare', reference), areas_of('simcompare', target))
    counter = Counting(monkeypatch, linepixel.PackedMask, 'unpack')
    run('simcompare', pairs, reference, target, (0.3, 0.8), jitter_um=0.1, areas=areas)
    assert 0 < counter.calls <= sum(4 * len(a.groups) for a in areas)


@pytest.mark.parametrize('name,n_pairs,sizes', RUN_SPECS)
def test_eviction_does_not_change_a_single_number(stage46, monkeypatch, name, n_pairs, sizes):
    """A cache far smaller than the working set (3 entries) still gives the frozen 7bcb07a result exactly."""
    cfg_path, correspondence = eq.make_windows_inputs(stage46, name, dict(seed=4, n_pairs=n_pairs, repeats='some', **CLEAN),
                                                      {}, sizes, dict(jitter=0.1))
    results, unpacks = {}, {}
    for entries in (3, 256):
        with monkeypatch.context() as mp:
            mp.setattr(local_delta, 'UNPACK_CACHE_ENTRIES', entries)
            counter = Counting(mp, linepixel.PackedMask, 'unpack')
            _, reference, target, pairs = eq._load_inputs('simcompare', cfg_path, correspondence)
            results[entries] = run('simcompare', pairs, reference, target, sizes, jitter_um=0.1, sort_diameter_um=1)
            unpacks[entries] = counter.calls
    assert unpacks[3] > 3 * unpacks[256]                                         # the tiny cache really evicted (many more unpacks)
    _, reference, target, pairs = eq._load_inputs(PKG, cfg_path, correspondence)
    results[PKG] = run(PKG, pairs, reference, target, sizes, jitter_um=0.1, sort_diameter_um=1)
    results['simcompare'] = results[3]
    assert not eq.deep_diff(results['simcompare'], results[PKG])
    assert json.dumps(results['simcompare'].to_dict(), sort_keys=True) == json.dumps(results[PKG].to_dict(), sort_keys=True)


def test_unpack_counters_are_exposed_when_io_counters_are_on(stage46, monkeypatch):
    _, _, reference, target, pairs = inputs(stage46, 'shared')
    monkeypatch.setattr(real_hotspot, 'IO_COUNTERS', {'sha256_files': 0, 'sha256_bytes': 0, 'mask_decodes': 0})
    result = compute_local_delta(pairs, reference, target, window_sizes_um=[0.3, 0.8], jitter_um=0.1)
    counters = real_hotspot.IO_COUNTERS
    assert counters['mask_unpacks'] > 0 and counters['mask_unpack_hits'] > 0
    assert len(result.rows) == sum(p.status == 'ready' for p in pairs) * 2 * 32


# --- 2. named-case index and per-pair record cache -----------------------------------------------------------------

def side_variants(case, side_template):
    """BoundSide variants for one dataset case: exact, wrong frame/layer/product, wrong failure type, filters, no failure type."""
    replace = dataclasses.replace
    yield side_template
    yield replace(side_template, failure_type=None)
    yield replace(side_template, failure_type='vertical_short')
    yield replace(side_template, analysis_layer='NOPE')
    yield replace(side_template, product_id='OTHER')
    yield replace(side_template, filters={'unit_id': case.metadata.get('unit_id')})
    yield replace(side_template, filters={'unit_id': 'other'})
    yield replace(side_template, filters={'source_id': case.identity.source_id})
    yield replace(side_template, filters={'exposure_id': 'nope'})
    yield replace(side_template, endpoints=tuple(reversed(side_template.endpoints)))
    yield replace(side_template, endpoints=side_template.endpoints[:1] * 2)


@pytest.mark.parametrize('name', ['shared', 'distinct', 'multilayer', 'components'])
def test_named_case_index_matches_the_scan_in_the_same_order(stage46, name):
    _, _, reference, target, pairs = inputs(stage46, name, n_pairs=4)
    template = next(p for p in pairs if p.status == 'ready' and p.a.endpoints)
    checked = 0
    for dataset in (reference, target):
        index = NamedCaseIndex(dataset.cases)
        for case in dataset.cases:
            base = dataclasses.replace(template.a, product_id=case.identity.product_id, design_version=case.identity.design_version,
                                       analysis_layer=case.analysis_layer, endpoints=case.endpoints, failure_type=case.failure_type,
                                       frame=dataset.frames[case.identity.frame_id])
            for side in side_variants(case, base):
                scan = _named_case(dataset.cases, side)
                assert [id(c) for c in index.matches(side)] == [id(c) for c in scan]
                checked += 1
    assert checked > 50


def test_named_case_duplicates_and_missing_keep_the_scan_order(stage46):
    """Two cases with the same endpoint set: the index returns both in dataset order (ambiguous), a missing set returns none."""
    _, _, reference, _, pairs = inputs(stage46, 'shared', n_pairs=3)
    cases = list(reference.cases)
    twin = dataclasses.replace(cases[0], identity=dataclasses.replace(cases[0].identity, case_id='twin'))
    dataset_cases = [cases[0], *cases[1:3], twin]
    side = dataclasses.replace(next(p for p in pairs if p.status == 'ready').a, endpoints=cases[0].endpoints,
                               product_id=cases[0].identity.product_id, design_version=cases[0].identity.design_version,
                               analysis_layer=cases[0].analysis_layer, failure_type=None, filters={},
                               frame=reference.frames[cases[0].identity.frame_id])
    assert [c.identity.case_id for c in NamedCaseIndex(dataset_cases).matches(side)] == \
           [c.identity.case_id for c in _named_case(dataset_cases, side)] == [cases[0].identity.case_id, 'twin']


def count_record_loads(monkeypatch, prefix):
    cls = eq.module(prefix, 'adapters.real_hotspot').LazyRecords
    return Counting(monkeypatch, cls, '__getitem__')


def test_named_records_are_loaded_once_per_pair_and_diameter(stage46, monkeypatch):
    sizes = (0.3, 0.8, 1.2)
    cfg_path, correspondence = eq.make_windows_inputs(stage46, 'shared', dict(seed=3, n_pairs=6, repeats='uniform', **CLEAN),
                                                      {}, sizes, dict(jitter=0.1))
    loads, ready = {}, 0
    for prefix in eq.PREFIXES:
        _, reference, target, pairs = eq._load_inputs(prefix, cfg_path, correspondence)
        areas = (areas_of(prefix, reference), areas_of(prefix, target))          # compute_areas loads are not part of this count
        counter = count_record_loads(monkeypatch, prefix)
        run(prefix, pairs, reference, target, sizes, areas=areas)
        loads[prefix] = counter.calls
        ready = sum(p.status == 'ready' for p in pairs)
        monkeypatch.undo()
    assert loads['simcompare'] <= ready * 2 * 32                               # 2 sides x 32 diameters, whatever the number of sizes
    assert loads[PKG] == ready * len(sizes) * 2 * 32                          # 7bcb07a: reloaded for every window size
    assert loads['simcompare'] * len(sizes) == loads[PKG]


# --- 3. lazy, bounded layout assets (B5) -----------------------------------------------------------------------------

def page_assets(prefix, cfg_path, reference, target):
    cfg = eq.module(prefix, 'pipeline').load_numeric_config(cfg_path)
    report = eq.module(prefix, 'local_delta_report')
    return report._Assets(cfg, 'reference', reference), report._Assets(cfg, 'target', target), report


def layout_pairs(assets, dataset):
    return {case.identity.case_id: assets.layouts(case) for case in dataset.cases}


@pytest.mark.parametrize('name', ['shared', 'distinct'])
def test_lazy_assets_return_the_eagerly_decoded_arrays(stage46, name):
    cfg_path, _ = eq.make_windows_inputs(stage46, name, dict(seed=3, n_pairs=2, **CLEAN), {}, (0.5,), {})
    got = {}
    for prefix in eq.PREFIXES:
        _, reference, target, _ = eq._load_inputs(prefix, cfg_path, eq.make_windows_inputs(stage46, name, dict(seed=3, n_pairs=2, **CLEAN), {}, (0.5,), {})[1])
        a, b, _ = page_assets(prefix, cfg_path, reference, target)
        got[prefix] = (a.reason, b.reason, layout_pairs(a, reference), layout_pairs(b, target))
    assert got['simcompare'][:2] == got[PKG][:2] == (None, None)
    for side in (2, 3):
        assert got['simcompare'][side].keys() == got[PKG][side].keys()
        for case_id, layouts in got[PKG][side].items():
            ours = got['simcompare'][side][case_id]
            assert (ours is None) == (layouts is None)
            if layouts is not None:
                assert len(ours) == len(layouts) and all(np.array_equal(x, y) and x.dtype == y.dtype for x, y in zip(ours, layouts))


def test_layout_cache_is_bounded_and_authentication_reads_each_layout_once(stage46, monkeypatch):
    cfg_path, correspondence = eq.make_windows_inputs(stage46, 'components', dict(seed=3, n_pairs=2, **CLEAN), {}, (0.5,), {})
    _, reference, target, _ = eq._load_inputs('simcompare', cfg_path, correspondence)
    monkeypatch.setattr(real_hotspot, 'IO_COUNTERS', {'sha256_files': 0, 'sha256_bytes': 0, 'mask_decodes': 0})
    a, b, _ = page_assets('simcompare', cfg_path, reference, target)
    n_layouts = sum(len(refs) for refs in a.refs.values())
    assert a.reads == n_layouts and real_hotspot.IO_COUNTERS.get('layout_auth_reads', 0) >= n_layouts
    assert a.reason is None and len(reference.cases) > local_delta_report.LAYOUT_CACHE_CASES
    for case in reference.cases:
        assert a.layouts(case) is not None
        assert len(a._cache) <= local_delta_report.LAYOUT_CACHE_CASES
    assert a.reads == 2 * n_layouts                                            # every case decoded exactly once more, then evicted
    hits_before = a.reads
    last = reference.cases[-1]
    assert a.layouts(last) is a.layouts(last) and a.reads == hits_before       # a cached case is not decoded again


@pytest.mark.parametrize('scenario,case', [('shared', 1), ('distinct', 2)])
def test_asset_damaged_between_validation_and_authentication_keeps_the_eager_failure_order(stage46, scenario, case):
    """check_asset_manifest reads each layout once; the damage lands before the eager loader's (now authentication) read:
    earlier cases keep their layouts, the damaged and every later case have none, one global reason - as in 7bcb07a."""
    outcomes = {}
    work = stage46.run_dir('lazy_between')
    for prefix in eq.PREFIXES:
        out, ref, tgt = stage46.clone(scenario, 'lazy_between', dest=work / 'root')
        relative = eq._layout(eq._manifest(ref), case, 0)['path']
        cfg = gen.write_config(out / 'cfg.yaml', ref, tgt, diameter_um=1)
        corr = gen.correspondence(out / 'c.json', ref, tgt, seed=6, n_pairs=2, **CLEAN)
        _, reference, target, _ = eq._load_inputs(prefix, cfg, corr)
        with eq.patch_reads(prefix, eq.mutate_after_first_read(relative)):
            a, _, _ = page_assets(prefix, cfg, reference, target)
        outcomes[prefix] = (a.reason, [(c.identity.case_id, a.layouts(c) is not None) for c in reference.cases])
    assert outcomes['simcompare'] == outcomes[PKG]
    assert outcomes[PKG][0] == 'input_hash_mismatch' and not all(ok for _, ok in outcomes[PKG][1])


def test_layout_damaged_after_authentication_makes_only_that_case_unavailable(stage46):
    """Lazy decoding cannot reproduce a snapshot taken at construction: a layout damaged LATER fails its own case with the
    sha256 code and never an exception; the other cases stay available. (Documented difference, T3_report.md.)"""
    out, ref, tgt = stage46.clone('distinct', 'lazy_after')
    cfg = gen.write_config(out / 'cfg.yaml', ref, tgt, diameter_um=1)
    corr = gen.correspondence(out / 'c.json', ref, tgt, seed=6, n_pairs=2, **CLEAN)
    _, reference, target, _ = eq._load_inputs('simcompare', cfg, corr)
    a, _, _ = page_assets('simcompare', cfg, reference, target)
    assert a.reason is None
    entry = eq._manifest(ref)['cases'][2]
    victim = next(c for c in reference.cases if c.identity.case_id == entry['identity']['case_id'])
    path = ref.data_dir / entry['assets']['layouts'][0]['asset']['path']
    path.write_bytes(path.read_bytes()[:40])
    assert a.layouts(victim) is None and a.reason == 'input_hash_mismatch'
    others = [c for c in reference.cases if c is not victim]
    assert all(a.layouts(c) is not None for c in others) and a.layouts(victim) is None


# --- 4. the stage-6 guard (B3) -----------------------------------------------------------------------------------------

def make_fail(monkeypatch, loads=True):
    """Every expensive stage-6 step fails the test: no compute, no unpack, no layout decode, no record load.
    loads=False allows _load_mask: the CLI authenticates every mask while loading the datasets, before the guard (B6)."""
    def boom(what):
        return lambda *a, **k: pytest.fail(f'{what} ran before the stage-6 guard refused')
    monkeypatch.setattr(linepixel, 'compute_areas', boom('compute_areas'))
    monkeypatch.setattr(linepixel.PackedMask, 'unpack', boom('PackedMask.unpack'))
    if loads:
        monkeypatch.setattr(real_hotspot, '_load_mask', boom('_load_mask'))
    for module_name in ('roi_export', 'numeric_images'):
        monkeypatch.setattr(eq.module('simcompare', module_name), 'read_binary', boom('read_binary'))
    monkeypatch.setattr(compare_masks, 'load_compare_masks', boom('load_compare_masks'))


def guard_inputs(stage46, name, n_pairs=6, sizes=(0.3, 0.8), jitter=0.1, **corr):
    cfg_path, correspondence, reference, target, pairs = inputs(stage46, name, n_pairs=n_pairs, sizes=sizes, jitter=jitter, **corr)
    cfg = pipeline.load_numeric_config(cfg_path)
    return cfg, cfg_path, correspondence, reference, target, pairs


def set_limit(*datasets, limit):
    for dataset in datasets:
        dataset.provenance['resource_estimate']['max_operation_bytes'] = limit


@pytest.mark.parametrize('name', ['shared', 'distinct', 'multilayer'])
@pytest.mark.parametrize('phase', ['all', 'compute', 'report'])
def test_guard_boundary_exact_estimate_passes_one_byte_below_refuses_with_zero_expensive_calls(stage46, monkeypatch, name, phase):
    cfg, _, _, reference, target, pairs = guard_inputs(stage46, name)
    sizes = (0.3, 0.8)
    estimate, _ = real_hotspot.stage_compare_windows_estimate(reference, target, layouts_cfg=cfg, pairs=pairs, sizes=sizes,
                                                              jitter_um=0.1, phase=phase)
    peak = estimate['peak']
    assert peak > 0
    kwargs = dict(layouts_cfg=cfg, pairs=pairs, sizes=sizes, jitter_um=0.1, phase=phase)
    set_limit(reference, target, limit=peak)
    real_hotspot.guard_stage_compare_windows('compare-windows', reference, target, **kwargs)
    set_limit(reference, target, limit=peak - 1)
    make_fail(monkeypatch)
    with pytest.raises(NumericContractError) as caught:
        real_hotspot.guard_stage_compare_windows('compare-windows', reference, target, **kwargs)
    message = str(caught.value)
    ready = sum(p.status == 'ready' for p in pairs)
    assert caught.value.code == 'oversized_real_operation'
    assert f'estimated {peak} bytes exceed limit {peak - 1}' in message
    assert f'{ready} ready and {len(pairs) - ready} blocked pairs x 2 window sizes' in message
    assert real_hotspot.STAGE6_REMEDY in message


def test_the_cli_refuses_one_byte_below_before_any_compute_and_writes_nothing(stage46, monkeypatch, tmp_path):
    cfg, cfg_path, correspondence, reference, target, pairs = guard_inputs(stage46, 'shared', sizes=(0.3, 0.8))
    estimate, _ = real_hotspot.stage_compare_windows_estimate(reference, target, layouts_cfg=cfg, pairs=pairs, sizes=(0.3, 0.8),
                                                              jitter_um=0.1, phase='all')
    make_fail(monkeypatch, loads=False)
    real_load = real_hotspot.configured_dataset

    def tight(*args, **kwargs):                       # the datasets the CLI builds are admitted for exactly one byte less
        dataset = real_load(*args, **kwargs)
        set_limit(dataset, limit=estimate['peak'] - 1)
        return dataset
    monkeypatch.setattr(real_hotspot, 'configured_dataset', tight)
    out = tmp_path / 'refused'
    code, _, err = eq.run_cli('simcompare', eq.windows_argv(cfg_path, correspondence, out, (0.3, 0.8), jitter=0.1, sort=1))
    assert code == 2 and 'oversized_real_operation' in (out / 'error.json').read_text(encoding='utf-8')
    assert not (out / 'local_delta.json').exists() and not (out / 'local_delta.html').exists()


def test_guard_runs_after_the_correspondence_is_bound_and_counts_ready_and_blocked(stage46, monkeypatch):
    order = []
    cfg, cfg_path, correspondence, reference, target, pairs = guard_inputs(stage46, 'distinct', n_pairs=6, blocked=2, outside=0,
                                                                         edge=0, duplicate=0)
    assert any(p.status != 'ready' for p in pairs) and any(p.status == 'ready' for p in pairs)
    real_guard = real_hotspot.guard_stage_compare_windows
    real_bind = eq.module('simcompare', 'correspondence').bind_pairs

    def guard(*args, **kwargs):
        order.append(('guard', sum(p.status == 'ready' for p in kwargs['pairs']), len(kwargs['pairs'])))
        return real_guard(*args, **kwargs)
    monkeypatch.setattr(real_hotspot, 'guard_stage_compare_windows', guard)
    monkeypatch.setattr(eq.module('simcompare', 'correspondence'), 'bind_pairs',
                        lambda *a, **k: order.append('bound') or real_bind(*a, **k))
    code, _, _ = eq.run_cli('simcompare', eq.windows_argv(cfg_path, correspondence, stage46.run_dir('order'), (0.3, 0.8), jitter=0.1))
    assert code in (0, 3)
    assert order[0] == 'bound' and order[1][0] == 'guard' and order[1][2] == len(pairs)
    assert order[1][1] == sum(p.status == 'ready' for p in pairs)


def test_oversized_pairs_and_sizes_are_refused_with_the_numbers_behind_the_estimate(stage46, monkeypatch):
    cfg, _, _, reference, target, pairs = guard_inputs(stage46, 'shared', n_pairs=4)
    ready = [p for p in pairs if p.status == 'ready']
    many = [dataclasses.replace(ready[0], pair_id=f'P{i}') for i in range(200_000)] + [p for p in pairs if p.status != 'ready']
    sizes = tuple(float(i + 1) for i in range(5))
    estimate, limit = real_hotspot.stage_compare_windows_estimate(reference, target, layouts_cfg=cfg, pairs=many, sizes=sizes,
                                                                   jitter_um=0.1)
    assert estimate['rows_bytes'] == 200_000 * 5 * 32 * real_hotspot.STAGE6_ROW_BYTES + 200_000 * 32 * real_hotspot.STAGE6_SENSITIVITY_ENTRY_BYTES
    assert estimate['peak'] > limit
    make_fail(monkeypatch)
    with pytest.raises(NumericContractError) as caught:
        compute_local_delta(many, reference, target, window_sizes_um=list(sizes), jitter_um=0.1)
    assert f'{len(ready) and 200_000} ready and' in str(caught.value) and 'x 5 window sizes' in str(caught.value)


def test_guard_is_the_max_of_the_phases_not_their_sum(stage46):
    cfg, _, _, reference, target, pairs = guard_inputs(stage46, 'shared')
    est, _ = real_hotspot.stage_compare_windows_estimate(reference, target, layouts_cfg=cfg, pairs=pairs, sizes=(0.3, 0.8),
                                                         jitter_um=0.1)
    assert est['peak'] == max(est['areas'], est['rows'], est['report']) < est['areas'] + est['rows'] + est['report']
    for phase, key in (('compute', None), ('rows', 'rows'), ('report', 'report')):
        part, _ = real_hotspot.stage_compare_windows_estimate(reference, target, layouts_cfg=cfg, pairs=pairs, sizes=(0.3, 0.8),
                                                              jitter_um=0.1, phase=phase)
        assert part['peak'] <= est['peak']
        if key:
            assert part['peak'] == est[key]


def test_reuse_prices_packed_masks_not_area_results():
    """Real ETX scale (1,682 cases, 1262^2, one layer): the artefact holds 4 packed masks x 32 groups per model."""
    cells = 1262 * 1262
    profile = (cells, {'L1': 1682}, 1682 * 32, 8 * 1024 ** 3, 1_000_000, (1262, 1262), 24_000_000)
    frames = [[(cells, 0.1)], [(cells, 0.1)]]
    kwargs = dict(ready=200, blocked=0, sizes=(10.0, 20.0, 30.0), jitter_um=0.5, named=True, layouts=(True, True), phase='compute')
    recompute = real_hotspot.estimate_stage_compare_windows([profile, profile], frames, reuse=False, **kwargs)
    reused = real_hotspot.estimate_stage_compare_windows([profile, profile], frames, reuse=True, **kwargs)
    packed = (cells + 7) // 8
    dataset = 1682 * 32 * real_hotspot.STAGE6_DATASET_RECORD_BYTES + real_hotspot.STAGE6_FIXED_BYTES
    assert reused['held'] == 2 * (32 * (4 * packed + real_hotspot.STAGE6_REUSED_GROUP_BYTES) + dataset)
    assert reused['held'] < recompute['held']                       # packed masks instead of AreaResults (the dataset payload stays)
    assert reused['areas'] < recompute['areas'] / 10                # no label-partition / group transient at all


def traced(function):
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        value = function()
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    return value, peak, before


def phase_peaks(cfg_path, correspondence, sizes, jitter, tmp_path, sort=1):
    """Absolute traced peaks of the rows and report phases (py3.8 has no reset_peak, so three tracing sessions):
    1. datasets + both AreaResults -> `held`;  2. compute_local_delta -> held + its delta peak;  3. the page on top of held + result."""
    tracemalloc.start()
    try:
        cfg, reference, target, pairs = eq._load_inputs('simcompare', cfg_path, correspondence)
        areas = (areas_of('simcompare', reference), areas_of('simcompare', target))
        held = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
    result, rows_delta, _ = traced(lambda: compute_local_delta(pairs, reference, target, window_sizes_um=list(sizes), jitter_um=jitter,
                                                                sort_diameter_um=sort, areas=areas))
    _, page_delta, _ = traced(lambda: local_delta_report.write_local_delta_html(tmp_path / 'page', result, cfg=cfg, reference=reference,
                                                                               target=target))
    return dict(cfg=cfg, reference=reference, target=target, pairs=pairs, result=result, held=held, rows=held + rows_delta,
                page_delta=page_delta)


@pytest.mark.parametrize('name,n_pairs,sizes', [('shared', 10, (0.3, 0.8, 1.2)), ('distinct', 8, (0.5, 1.2)),
                                                ('components', 8, (0.4, 8.0)), ('multilayer', 8, (0.5, 1.0))])
def test_traced_peak_stays_below_the_estimate_in_every_phase(stage46, tmp_path, name, n_pairs, sizes):
    """Absolute traced peak (datasets + AreaResults + rows + caches) <= the rows phase; held <= the priced held; the page on top of
    held + rows <= the report phase. Asymmetric frames ('distinct', 'multilayer') are priced with the larger frame of the two."""
    cfg_path, correspondence = eq.make_windows_inputs(stage46, name, dict(seed=3, n_pairs=n_pairs, repeats='uniform', **CLEAN),
                                                      {}, sizes, dict(jitter=0.1))
    measured = phase_peaks(cfg_path, correspondence, sizes, 0.1, tmp_path)
    reference, target, pairs, result = (measured[k] for k in ('reference', 'target', 'pairs', 'result'))
    ready = sum(p.status == 'ready' for p in pairs)
    estimate, _ = real_hotspot.stage_compare_windows_estimate(reference, target, layouts_cfg=measured['cfg'], pairs=pairs, sizes=sizes,
                                                              jitter_um=0.1)
    assert len(result.rows) == ready * len(sizes) * 32
    assert measured['held'] <= estimate['held'], (measured['held'], estimate)
    assert measured['rows'] <= estimate['rows'], (measured['rows'], estimate)
    assert measured['held'] + estimate['rows_bytes'] + measured['page_delta'] <= estimate['report'], (measured, estimate)
    retained = len(json.dumps(result.to_dict())) // max(1, len(result.rows))
    assert retained < real_hotspot.STAGE6_ROW_BYTES                      # the per-row charge is an upper bound


def test_estimate_uses_the_largest_frame_of_both_models(stage46):
    cfg, _, _, reference, target, pairs = guard_inputs(stage46, 'distinct')
    big = max(reference.frames.values(), key=lambda f: f.valid.size)
    small = min(reference.frames.values(), key=lambda f: f.valid.size)
    cells = [next(iter(d.frames.values())).valid.size for d in (reference, target)]
    assert cells[0] != cells[1]
    est, _ = real_hotspot.stage_compare_windows_estimate(reference, target, layouts_cfg=cfg, pairs=pairs, sizes=(0.5,), jitter_um=0.1)
    swapped, _ = real_hotspot.stage_compare_windows_estimate(target, reference, layouts_cfg=cfg, pairs=pairs, sizes=(0.5,), jitter_um=0.1)
    assert est['caches'] == swapped['caches'] >= (min(real_hotspot.STAGE6_UNPACK_CACHE_ENTRIES, 8 * 32 * sum(p.status == 'ready' for p in pairs)) + 1) * max(cells)
    assert big is not None and small is not None


# --- 5. the compare artefact (Q3) ----------------------------------------------------------------------------------------

def compare_config(stage46, name, label, **extra):
    """A writable clone of a scenario, a config whose output_dir is inside it, and a correspondence."""
    out, ref, tgt = stage46.clone(name, label)
    cmp_dir = out / 'compare_out'
    cfg = gen.write_config(out / 'cfg.yaml', ref, tgt, diameter_um=1, extra={'output_dir': str(cmp_dir), **extra})
    corr = gen.correspondence(out / 'c.json', ref, tgt, seed=2, n_pairs=6, repeats='uniform', margin=0.65, **CLEAN)
    return out, cfg, corr, cmp_dir, ref, tgt


def run_compare(cfg, prefix='simcompare'):
    code, _, err = eq.run_cli(prefix, ['compare', '--config', cfg])
    assert code == 3, err
    return code


def windows(cfg, corr, out, prefix='simcompare', **kwargs):
    code, stdout, err = eq.run_cli(prefix, eq.windows_argv(cfg, corr, out, (0.3, 0.8), jitter=0.1, sort=1, **kwargs))
    assert code in (0, 3), err
    return code


def files(folder):
    return {p.name: p.read_bytes() for p in sorted(Path(folder).iterdir()) if p.is_file() and p.name != 'execution.json'}


def forbid_compute(monkeypatch):
    monkeypatch.setattr(linepixel, 'compute_areas', lambda *a, **k: pytest.fail('compute_areas ran although the artefact matches'))


@pytest.mark.parametrize('name', ['shared', 'multilayer', 'components', 'unicode'])
def test_compare_publishes_the_artefact_and_reuse_equals_recompute_byte_for_byte(stage46, monkeypatch, name):
    out, cfg, corr, cmp_dir, ref, tgt = compare_config(stage46, name, 'reuse')
    run_compare(cfg)
    assert sorted(p.name for p in cmp_dir.iterdir() if p.is_file()) == ['comparison.csv', 'comparison.json',
                                                                         'comparison_masks.zip', 'execution.json']
    # comparison.json / .csv are byte-equal to what 7bcb07a writes for the same inputs (the artefact is an extra file)
    frozen_out = out / 'frozen_compare'
    frozen_cfg = gen.write_config(out / 'cfg_frozen.yaml', ref, tgt, diameter_um=1, extra={'output_dir': str(frozen_out)})
    code, _, err = eq.run_cli(PKG, ['compare', '--config', frozen_cfg])
    assert code == 3 and not (frozen_out / 'comparison_masks.zip').exists()
    for name_ in ('comparison.json', 'comparison.csv'):
        assert (cmp_dir / name_).read_bytes() == (frozen_out / name_).read_bytes()
    with monkeypatch.context() as mp:
        forbid_compute(mp)
        windows(cfg, corr, out / 'reused')                                       # raises through the CLI if compute_areas runs
    reused = files(out / 'reused')
    (cmp_dir / 'comparison_masks.zip').rename(out / 'masks.hidden')
    calls = Counting(monkeypatch, linepixel, 'compute_areas')
    windows(cfg, corr, out / 'recomputed')
    assert calls.calls == 2                                                       # the fallback really recomputes both models
    assert files(out / 'recomputed') == reused
    windows(cfg, corr, out / 'frozen', prefix=PKG)                                # ... and both equal 7bcb07a
    assert files(out / 'frozen') == reused


def reuse_state(monkeypatch, cfg, corr, out):
    """True when compare-windows served the AreaResults from the artefact (compute_areas never called)."""
    calls = Counting(monkeypatch, linepixel, 'compute_areas')
    windows(cfg, corr, out)
    monkeypatch.undo()
    return calls.calls == 0


def rewrite_zip(path, edit):
    members = {}
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            members[info.filename] = archive.read(info.filename)
    edit(members)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_STORED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def corrupt_member(members):
    name = next(n for n in members if n.startswith('masks/'))
    data = bytearray(members[name])
    data[0] ^= 0xFF
    members[name] = bytes(data)


def edit_meta(change):
    def edit(members):
        meta = json.loads(members['meta.json'])
        change(meta)
        members['meta.json'] = json.dumps(meta).encode()
    return edit


def drop_member(members):
    del members[next(n for n in members if n.startswith('masks/'))]


STALE = {
    'engine_tag': edit_meta(lambda m: m.update(engine='other')),
    'schema': edit_meta(lambda m: m.update(schema_version='compare_masks_v0')),
    'input_hash': edit_meta(lambda m: m['sides']['reference'].update(input_hash='0' * 64)),
    'target_input_hash': edit_meta(lambda m: m['sides']['target'].update(input_hash='f' * 64)),
    'synthetic_flag': edit_meta(lambda m: m['sides']['reference'].update(is_synthetic=True)),
    'frame_valid_hash': edit_meta(lambda m: next(iter(m['sides']['reference']['frames'].values())).update(valid_hash='1' * 64)),
    'frame_pitch': edit_meta(lambda m: next(iter(m['sides']['target']['frames'].values())).update(pixel_pitch_um=0.5)),
    'mask_digest': edit_meta(lambda m: m['sides']['reference']['groups'][0]['masks']['union'].update(sha256='2' * 64)),
    'mask_shape': edit_meta(lambda m: m['sides']['reference']['groups'][0]['masks']['union'].update(shape=[1, 1])),
    'side_missing': edit_meta(lambda m: m['sides'].update(target=None)),
    'meta_garbage': lambda members: members.update({'meta.json': b'not json'}),
    'bit_flip': corrupt_member,
    'member_missing': drop_member,
}


@pytest.mark.parametrize('kind', sorted(STALE))
def test_stale_or_tampered_artefact_is_ignored_and_the_result_is_identical(stage46, monkeypatch, kind):
    out, cfg, corr, cmp_dir, _, _ = compare_config(stage46, 'shared', f'stale_{kind}')
    run_compare(cfg)
    assert reuse_state(monkeypatch, cfg, corr, out / 'good')                         # the untouched artefact is reused
    rewrite_zip(cmp_dir / 'comparison_masks.zip', STALE[kind])
    assert not reuse_state(monkeypatch, cfg, corr, out / 'bad')                      # damaged: recomputed ...
    assert files(out / 'bad') == files(out / 'good')                                 # ... with the same numbers and files


@pytest.mark.parametrize('damage', ['truncated', 'empty', 'not_a_zip', 'directory'])
def test_unreadable_artefact_file_is_recomputed(stage46, monkeypatch, damage):
    out, cfg, corr, cmp_dir, _, _ = compare_config(stage46, 'shared', f'unreadable_{damage}')
    run_compare(cfg)
    assert reuse_state(monkeypatch, cfg, corr, out / 'good')
    path = cmp_dir / 'comparison_masks.zip'
    if damage == 'truncated':
        path.write_bytes(path.read_bytes()[:-30])
    elif damage == 'empty':
        path.write_bytes(b'')
    elif damage == 'not_a_zip':
        path.write_bytes(b'PK nothing')
    else:
        path.unlink()
        path.mkdir()
    assert not reuse_state(monkeypatch, cfg, corr, out / 'bad')
    assert files(out / 'bad') == files(out / 'good')


def test_artefact_of_other_inputs_is_never_reused(stage46, monkeypatch):
    out, cfg, corr, cmp_dir, _, _ = compare_config(stage46, 'shared', 'other_a')
    run_compare(cfg)
    out2, cfg2, corr2, cmp_dir2, _, _ = compare_config(stage46, 'multilayer', 'other_b')
    cmp_dir2.mkdir(parents=True)
    shutil.copyfile(cmp_dir / 'comparison_masks.zip', cmp_dir2 / 'comparison_masks.zip')    # another model pair's masks
    assert not reuse_state(monkeypatch, cfg2, corr2, out2 / 'w')


@pytest.mark.parametrize('leftover', ['.comparison.publishing', '.comparison.committed', '.comparison.committed.partial',
                                      '.comparison.publishing.partial'])
def test_artefact_next_to_an_unfinished_publication_is_not_used(stage46, monkeypatch, leftover):
    out, cfg, corr, cmp_dir, _, _ = compare_config(stage46, 'shared', f'inflight_{leftover.strip(".")}')
    run_compare(cfg)
    (cmp_dir / leftover).write_text('{}')
    assert not reuse_state(monkeypatch, cfg, corr, out / 'w')
    assert (cmp_dir / leftover).exists()                                            # reading never repairs or deletes anything


def test_artefact_over_the_operation_budget_is_not_read(stage46, monkeypatch):
    out, cfg, corr, cmp_dir, _, _ = compare_config(stage46, 'shared', 'budget')
    run_compare(cfg)
    reads = []
    real = compare_masks.load_compare_masks
    monkeypatch.setattr(compare_masks, 'load_compare_masks', lambda *a, **k: reads.append(1) or real(*a, **k))
    real_load = real_hotspot.configured_dataset
    size = (cmp_dir / 'comparison_masks.zip').stat().st_size

    def tight(*args, **kwargs):
        dataset = real_load(*args, **kwargs)
        set_limit(dataset, limit=size - 1)
        return dataset
    monkeypatch.setattr(real_hotspot, 'configured_dataset', tight)
    eq.run_cli('simcompare', eq.windows_argv(cfg, corr, out / 'w', (0.3,)))
    assert reads == []                                                               # refused on size alone, nothing read


def test_a_second_compare_replaces_the_artefact_with_the_pair(stage46):
    out, cfg, corr, cmp_dir, _, _ = compare_config(stage46, 'shared', 'second')
    run_compare(cfg)
    first = (cmp_dir / 'comparison_masks.zip').read_bytes()
    run_compare(cfg)
    assert (cmp_dir / 'comparison_masks.zip').read_bytes() == first                    # deterministic bytes
    assert sorted(p.name for p in cmp_dir.iterdir()) == ['comparison.csv', 'comparison.json', 'comparison_masks.zip', 'execution.json']


def test_artefact_roundtrip_reproduces_every_field_stage_6_reads(stage46, tmp_path):
    """The reloaded groups give the same AreaMetrics / fractions / masks / keys / order as compute_areas."""
    out, cfg, corr, cmp_dir, ref, tgt = compare_config(stage46, 'multilayer', 'roundtrip')
    run_compare(cfg)
    config = pipeline.load_numeric_config(cfg)
    reference = pipeline._dataset_for(config, config['reference'], 'reference')
    target = pipeline._dataset_for(config, config['target'], 'target')
    a, b, reason = compare_masks.load_compare_masks(cmp_dir, reference, target)
    assert reason is None
    for loaded, dataset in ((a, reference), (b, target)):
        fresh = areas_of('simcompare', dataset)
        assert list(loaded.groups) == list(fresh.groups) and loaded.input_hash == fresh.input_hash
        assert loaded.is_synthetic == fresh.is_synthetic
        for key, group in fresh.groups.items():
            mine = loaded.groups[key]
            assert mine.union == group.union and mine.failure_pieces == group.failure_pieces
            assert mine.fractions == group.fractions and mine.frame is group.frame or mine.frame.valid_hash == group.frame.valid_hash
            assert np.array_equal(mine.union_mask, group.union_mask)
            assert all(np.array_equal(mine.failure_piece_masks[n], group.failure_piece_masks[n]) for n in linepixel.FAILURE_PIECES)
            assert mine.frame_area_um2 == group.frame_area_um2


def test_compute_local_delta_with_reused_areas_equals_the_recompute(stage46):
    out, cfg, corr, cmp_dir, ref, tgt = compare_config(stage46, 'components', 'lib')
    run_compare(cfg)
    config = pipeline.load_numeric_config(cfg)
    _, reference, target, pairs = eq._load_inputs('simcompare', cfg, corr)
    a, b, reason = compare_masks.load_compare_masks(cmp_dir, reference, target)
    assert reason is None
    sizes = (0.4, 8.0)
    reused = compute_local_delta(pairs, reference, target, window_sizes_um=list(sizes), jitter_um=0.1, sort_diameter_um=1, areas=(a, b))
    recomputed = compute_local_delta(pairs, reference, target, window_sizes_um=list(sizes), jitter_um=0.1, sort_diameter_um=1)
    for field in ('rows', 'ranking', 'sensitivity', 'products', 'provenance', 'status', 'reasons', 'explained', 'pairs'):
        assert not eq.deep_diff(getattr(reused, field), getattr(recomputed, field)), field
    assert json.dumps(reused.to_dict(), sort_keys=True) == json.dumps(recomputed.to_dict(), sort_keys=True)


# --- 6. the artefact inside the Phase A publication transaction ------------------------------------------------------------

def seed_triple(out):
    out.mkdir(parents=True, exist_ok=True)
    (out / 'comparison.json').write_text('{"previous":"report"}', encoding='utf-8')
    (out / 'comparison.csv').write_text('previous\n', encoding='utf-8')
    (out / compare_masks.ARTEFACT_NAME).write_bytes(b'previous-masks')


def publish_triple(out, with_masks=True):
    stream = pipeline._ComparisonStream(out, masks=with_masks)
    stream.begin()
    try:
        stream.side('reference', None)
        stream.side('target', None)
        stream.commit({'classified_comparison_status': 'done'}, [{'a': 1}])
    except BaseException:
        stream.abort()
        raise
    stream.abort()


def triple_state(out):
    """'old' | 'new' | 'empty' | 'torn' for comparison.json / comparison.csv / comparison_masks.zip together."""
    names = ('comparison.json', 'comparison.csv', compare_masks.ARTEFACT_NAME)
    present = [(out / n).exists() for n in names]
    if not any(present):
        return 'empty'
    if not all(present):
        return 'torn'
    old = [(out / 'comparison.json').read_text() == '{"previous":"report"}', (out / 'comparison.csv').read_text() == 'previous\n',
           (out / names[2]).read_bytes() == b'previous-masks']
    new = ['real_hotspot_comparison_v2' in (out / 'comparison.json').read_text(),
           (out / 'comparison.csv').read_bytes().startswith(b'\xef\xbb\xbfa'), zipfile.is_zipfile(out / names[2])]
    return 'old' if all(old) else 'new' if all(new) else 'torn'


def leftovers(out):
    return sorted(p.name for p in out.iterdir() if p.name not in ('comparison.json', 'comparison.csv', compare_masks.ARTEFACT_NAME))


def count_steps(tmp_path, previous, link):
    from _pytest.monkeypatch import MonkeyPatch
    out = tmp_path / 'count'
    if previous:
        seed_triple(out)
    with MonkeyPatch.context() as mp:
        state = _fault_injection(mp, -1, OSError(errno.EIO, 'x'), False, link)
        publish_triple(out)
    assert triple_state(out) == 'new' and leftovers(out) == []
    return state['n']


def recover(out):
    stream = pipeline._ComparisonStream(out)
    stream.begin()
    stream.abort()


@pytest.mark.parametrize('link', [True, False], ids=['hardlink', 'copy_fallback'])
@pytest.mark.parametrize('previous', [True, False], ids=['has_previous', 'first_publication'])
@pytest.mark.parametrize('kind', ['oserror', 'interrupt', 'died'])
def test_every_step_of_the_three_file_publication_leaves_a_consistent_set(tmp_path, kind, previous, link):
    from _pytest.monkeypatch import MonkeyPatch
    steps = count_steps(tmp_path, previous, link)
    assert steps >= 11                                         # the Phase A pair needs >= 8; the third file adds backup/replace/cleanup
    exc = {'oserror': OSError(errno.EIO, 'step failed'), 'interrupt': KeyboardInterrupt(), 'died': _Died()}[kind]
    dead = kind == 'died'
    committed_seen = []
    for at in range(steps):
        out = tmp_path / f'run{at}'
        if previous:
            seed_triple(out)
        with MonkeyPatch.context() as mp:
            _fault_injection(mp, at, exc, dead, link)
            try:
                publish_triple(out)
                raised = False
            except BaseException as caught:
                assert caught is exc
                raised = True
        committed = (out / '.comparison.committed').exists() or not raised
        if not dead:
            assert triple_state(out) in (('old' if previous else 'empty'), 'new'), (at, leftovers(out))
        if raised:
            committed_seen.append(committed)
        expected = 'new' if committed else ('old' if previous else 'empty')
        for kill in range(0, 60):                              # restart recovery, killed at each of its own steps
            copy = tmp_path / f'run{at}_k{kill}'
            shutil.copytree(str(out), str(copy))
            with MonkeyPatch.context() as mp:
                state = _fault_injection(mp, kill, _Died(), True, link)
                try:
                    recover(copy)
                except _Died:
                    pass
            fired = state['n'] > kill
            recover(copy)
            assert triple_state(copy) == expected, (at, kill, leftovers(copy))
            assert leftovers(copy) == [], (at, kill, leftovers(copy))
            if not fired:
                break
        else:
            pytest.fail('recovery never finished')
        recover(out)
        assert triple_state(out) == expected and leftovers(out) == []
        publish_triple(out)                                    # the directory is not wedged
        assert triple_state(out) == 'new' and leftovers(out) == []
    assert committed_seen == sorted(committed_seen) and not committed_seen[0]
    assert kind != 'died' or committed_seen[-1]


def test_a_pair_only_stream_never_touches_an_existing_artefact(tmp_path):
    """masks=False (library / older callers): publishes the pair exactly as Phase A did and leaves the artefact alone."""
    out = tmp_path / 'o'
    seed_triple(out)
    publish_triple(out, with_masks=False)
    assert (out / compare_masks.ARTEFACT_NAME).read_bytes() == b'previous-masks'
    assert 'real_hotspot_comparison_v2' in (out / 'comparison.json').read_text() and leftovers(out) == []


def test_recovery_of_an_old_two_name_journal_is_still_understood(tmp_path):
    """A journal written by the Phase A code (two names) is rolled back by the three-name recovery."""
    out = tmp_path / 'o'
    seed_triple(out)
    (out / '.comparison.publishing').write_text(json.dumps({'comparison.json': True, 'comparison.csv': True}))
    for name in ('comparison.json', 'comparison.csv'):
        shutil.copyfile(str(out / name), str(out / f'.{name}.prev'))
    (out / 'comparison.json').write_text('torn')
    recover(out)
    assert (out / 'comparison.json').read_text() == '{"previous":"report"}' and (out / 'comparison.csv').read_text() == 'previous\n'
    assert (out / compare_masks.ARTEFACT_NAME).read_bytes() == b'previous-masks' and leftovers(out) == []


@pytest.mark.parametrize('content', [{'comparison.json': True, 'comparison.csv': True, 'extra.bin': True},
                                     {'comparison.json': True, 'comparison.csv': True, compare_masks.ARTEFACT_NAME: 1}])
def test_a_journal_with_unknown_names_or_values_fails_closed(tmp_path, content):
    out = tmp_path / 'o'
    seed_triple(out)
    (out / '.comparison.publishing').write_text(json.dumps(content))
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    with pytest.raises(NumericContractError) as caught:
        recover(out)
    assert caught.value.code == 'comparison_recovery_unreadable'
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before


def test_abort_before_commit_leaves_the_published_triple_and_no_temp_files(tmp_path):
    out = tmp_path / 'o'
    seed_triple(out)
    stream = pipeline._ComparisonStream(out, masks=True)
    stream.begin()
    stream.side('reference', None)
    stream.abort()
    assert triple_state(out) == 'old' and leftovers(out) == []


def test_enospc_while_writing_the_artefact_keeps_the_old_set(stage46, tmp_path, monkeypatch):
    out = tmp_path / 'o'
    seed_triple(out)
    real = compare_masks.MasksWriter.close

    def full(self):
        raise OSError(errno.ENOSPC, 'No space left on device')
    monkeypatch.setattr(compare_masks.MasksWriter, 'close', full)
    with pytest.raises(OSError):
        publish_triple(out)
    monkeypatch.setattr(compare_masks.MasksWriter, 'close', real)
    assert triple_state(out) == 'old' and leftovers(out) == []


# --- 7. frames large enough for the caches to dominate (asymmetric, many components) ---------------------------------------

@pytest.fixture(scope='module')
def large_pair(tmp_path_factory):
    base = tmp_path_factory.mktemp('stage6_large')
    cases = lambda prefix, seed: [gen.CaseSpec(f'{prefix}{2 * i}', f'{prefix}{2 * i + 1}', kind=('many', 'blob', 'comb', 'dense')[i % 4],
                                               seed=seed + i) for i in range(8)]
    reference = gen.build_model(base, 'reference', gen.ModelSpec('PREF', (240, 300), cases('R', 100)))
    target = gen.build_model(base, 'target', gen.ModelSpec('PTGT', (300, 270), cases('T', 200)))
    cfg = gen.write_config(base / 'cfg.yaml', reference, target, diameter_um=1)
    corr = gen.correspondence(base / 'c.json', reference, target, seed=4, n_pairs=10, repeats='uniform', margin=4.4, **CLEAN)
    return base, cfg, corr


def test_traced_peak_below_estimate_where_the_unpack_cache_dominates(large_pair, tmp_path):
    base, cfg_path, corr = large_pair
    sizes = (4.0, 8.0)
    measured = phase_peaks(cfg_path, corr, sizes, 0.3, tmp_path, sort=5)
    reference, target, pairs = measured['reference'], measured['target'], measured['pairs']
    assert next(iter(reference.frames.values())).valid.size != next(iter(target.frames.values())).valid.size
    estimate, _ = real_hotspot.stage_compare_windows_estimate(reference, target, layouts_cfg=measured['cfg'], pairs=pairs, sizes=sizes,
                                                              jitter_um=0.3)
    assert estimate['caches'] >= 300 * 270 * 64                          # >= 64 live unpacked masks of the larger frame
    assert measured['held'] <= estimate['held']
    assert measured['rows'] <= estimate['rows']
    assert measured['rows'] > 0.3 * estimate['rows'], (measured['rows'], estimate)       # not vacuously huge
    assert measured['held'] + estimate['rows_bytes'] + measured['page_delta'] <= estimate['report']


def test_estimate_is_monotone_in_pairs_and_sizes_and_symmetric_in_the_models(large_pair):
    base, cfg_path, corr = large_pair
    cfg, reference, target, pairs = eq._load_inputs('simcompare', cfg_path, corr)

    def peak(count, sizes, a=reference, b=target):
        return real_hotspot.stage_compare_windows_estimate(a, b, layouts_cfg=cfg, pairs=pairs[:count], sizes=sizes, jitter_um=0.3,
                                                           phase='rows')[0]['peak']
    by_pairs = [peak(count, (4.0, 8.0)) for count in (1, 3, len(pairs))]
    by_sizes = [peak(len(pairs), sizes) for sizes in ((4.0,), (4.0, 8.0), (4.0, 8.0, 12.0))]
    assert by_pairs == sorted(by_pairs) and by_pairs[0] < by_pairs[-1]
    assert by_sizes == sorted(by_sizes) and by_sizes[0] < by_sizes[-1]
    assert peak(len(pairs), (4.0,), target, reference) == peak(len(pairs), (4.0,))


@pytest.mark.parametrize('where', ['add_side', 'close'])
def test_an_artefact_that_cannot_be_built_never_costs_the_compare(stage46, monkeypatch, where):
    """Not an I/O error (those fail the publication like any file): the pair is published, the old artefact is removed in the
    same transaction (RB-A4: one generation) and stage 6 simply recomputes."""
    out, cfg, corr, cmp_dir, _, _ = compare_config(stage46, 'shared', f'optional_{where}')
    cmp_dir.mkdir(parents=True)
    (cmp_dir / compare_masks.ARTEFACT_NAME).write_bytes(b'older-artefact')
    monkeypatch.setattr(compare_masks.MasksWriter, where, lambda self, *a, **k: (_ for _ in ()).throw(TypeError('numpy.float16')))
    code, _, err = eq.run_cli('simcompare', ['compare', '--config', cfg])
    assert code == 3 and 'comparison_masks.zip not written' in err
    assert sorted(p.name for p in cmp_dir.iterdir() if p.is_file()) == ['comparison.csv', 'comparison.json', 'execution.json']
    monkeypatch.undo()
    assert not reuse_state(monkeypatch, cfg, corr, out / 'w')              # no artefact: recompute


def test_numpy_scalars_in_a_metric_are_stored_as_numbers(tmp_path):
    out = tmp_path / 'o'
    stream = pipeline._ComparisonStream(out, masks=True)
    stream.begin()
    metric = linepixel.AreaMetric(np.float64(1.5), np.float64(2.0), 'available', (), linepixel.Coverage(*[np.int64(1)] * 9))
    frame = local_delta.Frame('f', 0.1, (0.0, 0.0), (4, 4), np.ones((4, 4), bool))
    packed_mask = linepixel.PackedMask((4, 4), linepixel._pack(np.ones((4, 4), bool)))
    group = linepixel.GroupArea(linepixel.GroupKey('p', 'd', 'u', 's', 'st', 'e', 'L', 'D', 'c', 'f', 1.0), frame, None, None, metric, metric,
                                (), {}, packed_mask, packed_mask, packed_mask, packed_mask, None,
                                {n: metric for n in linepixel.FAILURE_PIECES}, {n: packed_mask for n in linepixel.FAILURE_PIECES})
    stream._masks.add_side('reference', linepixel.AreaResult({group.key: group}, {}, 'h', None, None))
    stream._masks.add_side('target', None)
    stream._masks.close()
    stream.abort()
