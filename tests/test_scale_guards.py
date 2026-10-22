"""T3: lazy real records, streaming estimator arithmetic, bucket-output wiring, stage-4/6 admission guards."""
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import weakref

import numpy as np
import pytest
import yaml

import test_real_hotspot as real_tests
from simcompare import linepixel
import simcompare.adapters.real_hotspot as real_hotspot
from simcompare.adapters.real_hotspot import (LazyRecords, configured_dataset, estimate_area_bytes,
                                              guard_stage_compare_windows, guard_stage_map, load_real_manifest)
from simcompare.electrode_map import compute_electrode_maps
from simcompare.local_delta import compute_local_delta
from simcompare.numeric_schema import DIAMETERS_UM, NumericContractError, RecordHeader

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'make_compare_fixture.py'


def _fixture(out, **kwargs):
    spec = importlib.util.spec_from_file_location('make_compare_fixture', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build(out, **kwargs)


def _assignment_of(config_path, side='reference', **limits):
    cfg = yaml.safe_load(Path(config_path).read_text(encoding='utf-8'))
    assignment = dict(cfg[side]['condition_assignment'])
    assignment.update(limits)
    return Path(cfg[side]['data_dir']), assignment


def _dataset(config_path, **limits):
    root, assignment = _assignment_of(config_path, **limits)
    return configured_dataset(root, load_real_manifest(root), assignment)


@pytest.fixture(scope='module')
def small(tmp_path_factory):
    return _fixture(tmp_path_factory.mktemp('guard_small'), cases=4, size=24, seed=5, shared=2)


@pytest.fixture(scope='module')
def crowded(tmp_path_factory):
    # 16 cases x 2 electrodes on a 400 x 400 frame: 5 full masks per case make the stage-4 footprint exceed the area
    # footprint (the stage-4 estimate no longer carries a fixed region allowance).
    return _fixture(tmp_path_factory.mktemp('guard_crowded'), cases=16, size=400, seed=2, shared=4)


@pytest.fixture(scope='module')
def wide(tmp_path_factory):
    # 6 cases x 12 electrodes on a 400 x 400 frame: the stage-4 footprint clearly exceeds the area footprint.
    return _fixture(tmp_path_factory.mktemp('guard_wide'), cases=6, size=400, seed=2, shared=3)


# ---- LazyRecords ----------------------------------------------------------------------------------

def test_configured_records_are_lazy_headers_do_not_load(small, monkeypatch):
    calls = []
    original = real_hotspot._load_mask
    root, assignment = _assignment_of(small)
    manifest = load_real_manifest(root)      # authenticates (and reads) every mask once; not part of this check
    monkeypatch.setattr(real_hotspot, '_load_mask', lambda *a, **k: calls.append(a[0]) or original(*a, **k))
    dataset = configured_dataset(root, manifest, assignment)
    case = dataset.cases[0]
    assert isinstance(case.records, LazyRecords) and len(case.records) == len(DIAMETERS_UM)
    assert set(case.records) == set(float(d) for d in DIAMETERS_UM) and 5.0 in case.records
    head = case.records.header(5.0)
    assert isinstance(head, RecordHeader) and head.input_status == 'observed'
    assert head.identity == case.identity and head.analysis_layer == case.analysis_layer
    assert calls == []
    first, second = case.records[5.0], case.records[5.0]
    assert len(calls) == 2 and first.mask is not second.mask  # no cache: every access reads the file again
    assert first.mask.dtype == bool and np.array_equal(first.mask, second.mask)
    assert first.diameter_um == head.diameter_um and first.frame is head.frame


def test_lazy_shape_mismatch_keeps_error_code(small, monkeypatch):
    dataset = _dataset(small)
    monkeypatch.setattr(real_hotspot, '_load_mask', lambda *a, **k: np.zeros((3, 3), bool))
    with pytest.raises(NumericContractError, match='real_mask_shape_mismatch'):
        dataset.cases[0].records[1.0]


def test_unconfirmed_geometry_records_are_missing_and_never_read(tmp_path, monkeypatch):
    root, out = tmp_path / 'source', tmp_path / 'imported'
    first = real_tests._source(root, shape=(1002, 1002))
    (first / '2.png').unlink()
    real_tests._source(root, case='lateral_short-A-L1-C-L1', shape=(1002, 1002))
    real_hotspot.import_hotspots(root, out)
    real_tests._write_pairs(out)
    manifest = load_real_manifest(out)
    monkeypatch.setattr(real_hotspot, '_load_mask', lambda *a, **k: pytest.fail('mask of unconfirmed case read'))
    dataset = configured_dataset(out, manifest, real_tests._assignment())
    case = next(c for c in dataset.cases if c.identity.case_id.endswith('B-L1'))
    record = case.records[1.0]
    assert record.input_status == 'missing' and record.mask is None and record.source_ca is None
    assert 'case_geometry_unconfirmed' in record.reasons
    assert case.records.header(1.0).input_status == 'missing'


def test_compute_areas_loads_masks_one_at_a_time(small, monkeypatch):
    dataset = _dataset(small)
    original = real_hotspot._load_mask
    alive, peak, loads = [], [0], [0]

    class Tracked(np.ndarray):
        pass

    def tracked(*args, **kwargs):
        mask = np.asarray(original(*args, **kwargs)).view(Tracked)
        gc.collect()
        alive[:] = [ref for ref in alive if ref() is not None]
        alive.append(weakref.ref(mask))
        peak[0] = max(peak[0], len(alive))
        loads[0] += 1
        return mask

    monkeypatch.setattr(real_hotspot, '_load_mask', tracked)
    result = linepixel.compute_areas(dataset)
    observed = sum(1 for c in dataset.cases for d in DIAMETERS_UM if c.records.header(float(d)).input_status == 'observed')
    assert loads[0] == observed          # each observed mask is read exactly once per compare
    assert peak[0] <= 2                  # the previous record is released when the next one is loaded
    assert result.groups


def test_lazy_dataset_equals_eager_dict_records(small):
    dataset = _dataset(small)
    lazy = linepixel.compute_areas(dataset).to_dict() if hasattr(linepixel.AreaResult, 'to_dict') else None
    for case in dataset.cases:
        case.records = {d: case.records[d] for d in case.records}   # eager MaskRecords, same objects as before T3
    eager = linepixel.compute_areas(dataset).to_dict()
    assert lazy == eager


def test_legacy_npy_import_loads_and_computes_identically(tmp_path):
    config = _fixture(tmp_path / 'fx', cases=4, size=24, seed=7, shared=2)
    root, assignment = _assignment_of(config)
    current = linepixel.compute_areas(configured_dataset(root, load_real_manifest(root), assignment)).to_dict()
    manifest = json.loads((root / 'manifest.json').read_text())
    for case in manifest['cases']:
        for rec in case['records']:
            if rec['input_status'] != 'observed':
                continue
            old = root / rec['mask_path']
            with np.load(old) as archive:
                mask = archive['mask']
            new = old.with_suffix('.npy')
            np.save(new, mask)
            old.unlink()
            rec['mask_path'] = new.relative_to(root).as_posix()
            rec['mask_sha256'] = hashlib.sha256(new.read_bytes()).hexdigest()
            del rec['mask_encoding']
    (root / 'manifest.json').write_text(json.dumps(manifest))
    real_tests._reseal_real_manifest(root, lambda m: None)
    legacy = configured_dataset(root, load_real_manifest(root), assignment)
    assert all(rec['mask_path'].endswith('.npy')
               for c in legacy.cases for rec in c.metadata['source_case_metadata']['records']
               if rec['input_status'] == 'observed')
    def numbers(result):   # raw source records legitimately differ (mask_path / sha / encoding)
        return [{**group, 'source_cases': [{k: v for k, v in source.items() if k != 'raw'}
                                           for source in group['source_cases']]} for group in result['groups']]
    assert numbers(linepixel.compute_areas(legacy).to_dict()) == numbers(current)


# ---- estimator arithmetic -------------------------------------------------------------------------

def test_estimator_arithmetic_at_real_scale():
    """1,682 cases, one layer, 32 diameters, frame 1262 x 1262, default budgets.

    masks   = 1 layer x 32 x 7 x ceil(1,592,644 / 8)                        (the AreaResult, packed)
    output  = 32 groups x emitted entries x 100 B                           (RETAINED by every group)
    group   = 1,682 x 2 x 199,081 B + 64 x 1,592,644 B                      (sources+pair unions, then 64 B/cell)
    history = 24,000,000 x 256 B (label-pattern budget) + emitted x 200 B   (one group serialized at a time)
    payload = 1,682 x 32 x 3,072 B
    emitted = 1,000,000 (full) or min(1,000,000, 2 x 20 x 3 x 1,682 = 201,840) (summary, top 20).
    """
    cells = 1262 * 1262
    packed = (cells + 7) // 8
    group = 1682 * 2 * packed + 64 * cells
    full = estimate_area_bytes(cells, {'L1': 1682}, 1682 * 32, 1_000_000)
    assert full['output'] == 32 * 1_000_000 * 100
    assert full['retained'] == 32 * 7 * packed + full['output']
    assert full['payload'] == 1682 * 32 * 3 * 1024
    assert full['transient'] == group + 1_000_000 * 200 + 24_000_000 * 256
    assert full['total'] == full['retained'] + full['payload'] + full['transient']
    summary = estimate_area_bytes(cells, {'L1': 1682}, 1682 * 32, 1_000_000, 20)
    assert summary['output'] == 32 * 201_840 * 100
    assert summary['transient'] == group + 201_840 * 200 + 24_000_000 * 256
    # the retained bucket output of all 32 groups is what keeps full detail out of the default 8 GiB
    assert summary['total'] < 8 * 1024 ** 3 < full['total']
    assert 53_824 * cells > 20e9          # what the old eager estimate compared against max_materialized_source_cells


def test_estimator_scales_with_layers_and_bucket_budget():
    base = estimate_area_bytes(1000, {'A': 10}, 320, 1000)
    two = estimate_area_bytes(1000, {'A': 10, 'B': 10}, 640, 1000)
    assert two['retained'] == 2 * base['retained'] and two['payload'] == 2 * base['payload']
    assert two['output'] == 2 * base['output']        # retained output is charged per layer x diameter group
    assert two['transient'] == base['transient']      # one group is live at a time
    bigger = estimate_area_bytes(1000, {'A': 10}, 320, 2000)
    assert bigger['retained'] - base['retained'] == 32 * 1000 * 100          # every group keeps its output
    assert bigger['transient'] - base['transient'] == 1000 * 200             # one group is serialized at a time
    wider = estimate_area_bytes(1000, {'A': 10}, 320, 1000, None, 3000)
    assert wider['transient'] - estimate_area_bytes(1000, {'A': 10}, 320, 1000, None, 1000)['transient'] == 2000 * 256


def test_structural_caps_keep_small_datasets_inside_default_limits():
    """A group cannot hold more patterns than min(cells, 2^cases): tiny inputs do not pay the 6 GB default label budget."""
    tiny = estimate_area_bytes(100, {'L': 4}, 128, 1_000_000)
    assert tiny['total'] < 2 << 20
    assert estimate_area_bytes(100, {'L': 4}, 128, 1_000_000, None, 10**9)['total'] == tiny['total']


def test_provenance_records_the_streaming_estimate(small):
    estimate = _dataset(small).provenance['resource_estimate']
    assert estimate['estimated_peak_bytes'] == estimate['components']['total']
    assert 'streaming' in estimate['method']


def test_operation_budget_uses_the_new_estimate(small):
    estimate = _dataset(small).provenance['resource_estimate']['estimated_peak_bytes']
    _dataset(small, max_operation_bytes=estimate)                       # equal passes
    with pytest.raises(NumericContractError, match='oversized_real_operation'):
        _dataset(small, max_operation_bytes=estimate - 1)


def test_materialization_limit_applies_to_the_widest_group(small):
    # 4 cases x 24 x 24 = 2,304 cells in the only layer group
    _dataset(small, max_materialized_source_cells=2304)
    with pytest.raises(NumericContractError, match='oversized_real_materialization'):
        _dataset(small, max_materialized_source_cells=2303)


# ---- max_bucket_output_entries ---------------------------------------------------------------------

@pytest.mark.parametrize('value', [0, -1, True, 1.5, '10'])
def test_bucket_output_limit_is_validated(small, value):
    with pytest.raises(NumericContractError, match='invalid_resource_limit'):
        _dataset(small, max_bucket_output_entries=value)


def test_bucket_output_limit_is_wired_from_assignment(small):
    dataset = _dataset(small, max_bucket_output_entries=1)
    assert dataset.provenance['configured_assignments']['max_bucket_output_entries'] == 1
    with pytest.raises(NumericContractError, match='oversized_real_bucket_output'):
        linepixel.compute_areas(dataset)
    assert linepixel.compute_areas(_dataset(small, max_bucket_output_entries=1_000_000)).groups
    # an explicit argument still wins over the dataset value
    assert linepixel.compute_areas(dataset, max_bucket_output_entries=1_000_000).groups


# ---- max_label_pattern_entries ---------------------------------------------------------------------

@pytest.mark.parametrize('value', [0, -1, True, 1.5, '10'])
def test_label_pattern_limit_is_validated(small, value):
    with pytest.raises(NumericContractError, match='invalid_resource_limit'):
        _dataset(small, max_label_pattern_entries=value)
    with pytest.raises(NumericContractError, match='invalid_resource_limit'):
        linepixel.compute_areas(_dataset(small), max_label_pattern_entries=value)


def test_label_pattern_limit_is_wired_and_separate_from_output_budget(small):
    dataset = _dataset(small, max_label_pattern_entries=1)
    assert dataset.provenance['configured_assignments']['max_label_pattern_entries'] == 1
    with pytest.raises(NumericContractError) as error:
        linepixel.compute_areas(dataset)
    assert error.value.code == 'oversized_real_label_patterns'
    message = str(error.value)
    assert ' d=' in message and 'live label-pattern entries' in message and 'limit 1;' in message
    assert 'max_label_pattern_entries' in message
    # an explicit argument wins; the output budget no longer bounds the partition
    assert linepixel.compute_areas(dataset, max_label_pattern_entries=1_000_000).groups
    reference = linepixel.compute_areas(_dataset(small)).to_dict()['groups']
    assert linepixel.compute_areas(_dataset(small, max_label_pattern_entries=1_000_000)).to_dict()['groups'] == reference
    with pytest.raises(NumericContractError) as error:
        linepixel.compute_areas(_dataset(small, max_bucket_output_entries=1))
    assert error.value.code == 'oversized_real_bucket_output'


def test_label_pattern_budget_enters_the_estimate():
    one = estimate_area_bytes(1000, {'A': 10}, 320, 1000, None, 1000)
    two = estimate_area_bytes(1000, {'A': 10}, 320, 1000, None, 2000)
    assert two['transient'] - one['transient'] == 1000 * 256


# ---- stage 4 / stage 6 admission guards ------------------------------------------------------------

def _stage4_estimate(dataset):
    cells = next(iter(dataset.frames.values())).valid.size
    layers = {}
    for case in dataset.cases:
        layers.setdefault(case.analysis_layer, []).append(case)
    peak = 0
    for cases in layers.values():
        electrodes = {ep.electrode_id for c in cases for ep in c.endpoints}
        peak = max(peak, (len(cases) + 2 * len(electrodes)) * cells + 16 * cells)
    return peak + 2 * len(layers) * 8 * cells


def test_stage4_guard_fails_fast_before_masks_are_loaded(crowded, monkeypatch):
    area = _dataset(crowded, max_operation_bytes=1 << 30, max_bucket_output_entries=100, max_label_pattern_entries=100)
    limit = area.provenance['resource_estimate']['estimated_peak_bytes']
    dataset = _dataset(crowded, max_operation_bytes=limit, max_bucket_output_entries=100, max_label_pattern_entries=100)   # stage 2 fits exactly
    expected = _stage4_estimate(dataset)
    assert expected > limit            # 16 cases x 160,000 cells: the map needs more than compare
    monkeypatch.setattr(real_hotspot, '_load_mask', lambda *a, **k: pytest.fail('stage 4 guard must precede loading'))
    with pytest.raises(NumericContractError) as caught:
        compute_electrode_maps(dataset, diameter_um=5)
    assert caught.value.code == 'oversized_real_operation'
    assert str(caught.value) == (f'oversized_real_operation: electrode-map: estimated {expected} bytes exceed limit '
                                 f'{limit}; {real_hotspot.OPERATION_REMEDY}')
    assert 'streaming support for this stage is pending' in str(caught.value)


def test_stage4_guard_passes_within_limit(crowded):
    dataset = _dataset(crowded, max_operation_bytes=_stage4_estimate(_dataset(crowded)) , max_bucket_output_entries=100, max_label_pattern_entries=100)
    guard_stage_map('electrode-map', dataset)
    assert compute_electrode_maps(dataset, diameter_um=5).layers


def _stage6_parts(dataset):
    profile = real_hotspot._dataset_profile(dataset)
    return estimate_area_bytes(profile[0], profile[1], profile[2], profile[4], real_hotspot.STAGE6_TOP_BUCKETS, profile[6])


def test_stage6_guard_counts_both_models(wide, monkeypatch):
    one = _dataset(wide, max_operation_bytes=1 << 30, max_bucket_output_entries=100, max_label_pattern_entries=100)
    limit = one.provenance['resource_estimate']['components']['total']     # one model (stage 2) fits exactly ...
    dataset = _dataset(wide, max_operation_bytes=limit, max_bucket_output_entries=100, max_label_pattern_entries=100)
    single = _stage6_parts(dataset)
    expected = 2 * (single['retained'] + single['payload']) + single['transient']
    assert expected > limit                                       # ... two models together do not
    monkeypatch.setattr(real_hotspot, '_load_mask', lambda *a, **k: pytest.fail('stage 6 guard must precede loading'))
    with pytest.raises(NumericContractError) as caught:
        compute_local_delta([], dataset, dataset, window_sizes_um=[10])
    assert str(caught.value) == (f'oversized_real_operation: compare-windows: estimated {expected} bytes exceed '
                                 f'limit {limit}; {real_hotspot.OPERATION_REMEDY}')


def test_stage6_guard_includes_layout_masks(wide):
    probe = _dataset(wide, max_operation_bytes=1 << 30, max_bucket_output_entries=100, max_label_pattern_entries=100)
    parts = _stage6_parts(probe)
    areas_only = 2 * (parts['retained'] + parts['payload']) + parts['transient']
    dataset = _dataset(wide, max_operation_bytes=areas_only, max_bucket_output_entries=100, max_label_pattern_entries=100)
    guard_stage_compare_windows('compare-windows', dataset, dataset)                         # areas alone fit
    cfg = {'reference': {'asset_manifest': {'path': 'x'}}, 'target': {'asset_manifest': {'path': 'x'}}}
    layouts = len(dataset.cases) * 2 * 1400 * 1400 * 2
    with pytest.raises(NumericContractError) as caught:
        guard_stage_compare_windows('compare-windows', dataset, dataset, layouts_cfg=cfg)
    message = str(caught.value)
    assert message.startswith('oversized_real_operation: compare-windows: estimated ')
    assert f'exceed limit {areas_only};' in message
    estimated = int(message.split('estimated ')[1].split(' bytes')[0])
    held = 2 * (parts['retained'] + parts['payload'])
    assert estimated == held + layouts + 20 * 1400 * 1400
    # sides without an asset_manifest hold no layouts
    guard_stage_compare_windows('compare-windows', dataset, dataset, layouts_cfg={'reference': {}, 'target': {}})


# ---- R3: retained bucket output of every group (and both stage-6 models) is accounted --------------

def test_stage6_models_retain_only_what_the_guard_charged(small, monkeypatch):
    """compare-windows never reads buckets: its AreaResults keep STAGE6_TOP_BUCKETS per class, as the guard assumes."""
    dataset = _dataset(small)
    seen = []
    real = linepixel.compute_areas
    monkeypatch.setattr(linepixel, 'compute_areas', lambda inputs, **kw: seen.append(kw) or real(inputs, **kw))
    compute_local_delta([], dataset, dataset, window_sizes_um=[10])
    assert seen == [{'bucket_detail': 'summary', 'top_buckets': real_hotspot.STAGE6_TOP_BUCKETS}] * 2


def test_stage6_guard_charges_retained_output_of_both_models():
    """Reviewer scenario: 32 groups x 1M output entries per model used to be charged once, as a transient."""
    cells = 1262 * 1262
    full = estimate_area_bytes(cells, {'L1': 1682}, 1682 * 32, 1_000_000)
    top1 = estimate_area_bytes(cells, {'L1': 1682}, 1682 * 32, 1_000_000, real_hotspot.STAGE6_TOP_BUCKETS)
    assert full['retained'] - top1['retained'] == 32 * (1_000_000 - 2 * 3 * 1682) * 100
    # both models: the retained part (masks + output) is held twice, the transient once
    both = 2 * (top1['retained'] + top1['payload']) + top1['transient']
    assert both - (top1['retained'] + top1['payload'] + top1['transient']) == top1['retained'] + top1['payload']
    assert top1['output'] == 32 * 2 * 3 * 1682 * 100       # summary mode retains only the top-N lists
    # a group list of entries that is retained for 32 groups must show up in `retained`, not in `transient`
    assert full['retained'] >= 32 * 1_000_000 * 100 and full['transient'] < 8 * 1024 ** 3


def test_guards_ignore_non_real_datasets():
    from simcompare.adapters.numeric import load_dataset
    dataset = load_dataset(Path(__file__).parent / 'fixtures/numeric_v2')
    guard_stage_map('electrode-map', dataset)
    guard_stage_compare_windows('compare-windows', dataset, dataset, layouts_cfg={})


# ---- R1: one default for the label budget (admission == execution == provenance == docs) -------------
def test_default_label_budget_is_one_value_everywhere(small, monkeypatch):
    dataset = _dataset(small)                      # no max_label_pattern_entries / max_bucket_output_entries configured
    seen = {}
    real_group = linepixel._group

    def spy(key, entries, chunk_cells, table_version, **kwargs):
        seen['label'], seen['bucket'] = kwargs['max_label_entries'], kwargs['max_bucket_entries']
        return real_group(key, entries, chunk_cells, table_version, **kwargs)
    monkeypatch.setattr(linepixel, '_group', spy)
    linepixel.compute_areas(dataset)
    execution = seen['label']
    estimate = dataset.provenance['resource_estimate']
    profile = real_hotspot._dataset_profile(dataset)
    admission = profile[6]
    assert execution == admission == estimate['max_label_pattern_entries'] == 24_000_000
    assert linepixel.DEFAULT_MAX_LABEL_PATTERN_ENTRIES == real_hotspot.DEFAULT_MAX_LABEL_PATTERN_ENTRIES == execution
    assert seen['bucket'] == profile[4] == estimate['max_bucket_output_entries'] == 1_000_000
    # the admission estimate prices exactly the entries execution may keep live
    default = estimate_area_bytes(1000, {'A': 10}, 320, 1000)
    explicit = estimate_area_bytes(1000, {'A': 10}, 320, 1000, None, execution)
    assert default == explicit
    docs = (Path(__file__).resolve().parents[1]/'docs/usage.md').read_text(encoding='utf-8')
    assert '24,000,000' in docs and '50,000,000' not in docs


# ---- R2: stage-4 admission bounds the regions that are actually allocated -------------------------------
@pytest.fixture(scope='module')
def fragmented(tmp_path_factory):
    # 40 independent cases (no shared clusters) on a 100 x 100 frame: far more than 32 connected regions at count >= 1.
    return _fixture(tmp_path_factory.mktemp('guard_fragmented'), cases=40, size=100, seed=4, shared=1)


def _components_count(dataset):
    maps = compute_electrode_maps(dataset, diameter_um=5, min_count=1)
    return sum(len(layer.regions) for layer in maps.layers)


def test_stage4_region_count_is_bounded_by_what_is_allocated(fragmented, monkeypatch):
    big = _dataset(fragmented, max_operation_bytes=1 << 30, max_bucket_output_entries=100, max_label_pattern_entries=100)
    regions = _components_count(big)
    assert regions > 32                                       # the old fixed allowance of 32 regions is exceeded
    cells = next(iter(big.frames.values())).valid.size
    admission = _stage4_estimate(big)                         # what the admission guard prices (no region guess)
    dataset = _dataset(fragmented, max_operation_bytes=1 << 30, max_bucket_output_entries=100, max_label_pattern_entries=100)
    dataset.provenance['resource_estimate']['max_operation_bytes'] = admission   # stage 2 was admitted with a larger limit
    guard_stage_map('electrode-map', dataset)                 # admission passes ...
    import simcompare.electrode_map as electrode_map
    monkeypatch.setattr(electrode_map, '_components',
                        lambda *a, **k: pytest.fail('no full-frame region mask may be allocated before the bound is checked'))
    with pytest.raises(NumericContractError) as caught:       # ... but the real region count does not fit
        compute_electrode_maps(dataset, diameter_um=5, min_count=1)
    assert caught.value.code == 'oversized_real_operation'
    message = str(caught.value)
    assert 'stage-4 regions' in message and f'{regions} regions' in message and f'of {cells} cells' in message
    assert f'exceed limit {admission};' in message
    # without region selection (min_count=None) nothing is allocated and the map still computes
    assert compute_electrode_maps(dataset, diameter_um=5).layers


def test_stage4_region_bound_passes_when_the_regions_fit(fragmented):
    dataset = _dataset(fragmented, max_operation_bytes=1 << 30, max_bucket_output_entries=100, max_label_pattern_entries=100)
    assert _components_count(dataset) > 32


# ---- B2: the component count and the region construction are priced before they allocate -----------------
@pytest.fixture(scope='module')
def dense(tmp_path_factory):
    # 1 case x 2 electrodes on a 400 x 400 frame, every cell set at 5 um: one region of 160,000 cells at count >= 1.
    return _fixture(tmp_path_factory.mktemp('guard_dense'), cases=1, size=400, seed=2, shared=1)


def _dense_run(config, limit, monkeypatch, mask=None):
    """compute_electrode_maps on the all-True (or the given) 5 um mask: (result or NumericContractError, traced peak)."""
    import tracemalloc
    dataset = _dataset(config, max_operation_bytes=1 << 30, max_bucket_output_entries=100, max_label_pattern_entries=100)
    dataset.provenance['resource_estimate']['max_operation_bytes'] = limit    # stage 2 was admitted with a larger limit
    cells = next(iter(dataset.frames.values())).valid.size
    mask = np.ones((400, 400), bool) if mask is None else mask
    assert cells == mask.size
    monkeypatch.setattr(real_hotspot, '_load_mask', lambda *a, **k: mask.copy())
    tracemalloc.start()
    try:
        try:
            outcome = compute_electrode_maps(dataset, diameter_um=5, min_count=1)
        except NumericContractError as error:
            outcome = error
        return outcome, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def _dense_estimates(cells=160_000):
    kwargs = dict(layers_done=1, layer_cases=1, layer_electrodes=2)
    counting = real_hotspot.estimate_stage_map_counting(cells, regions_done=0, runs=400, **kwargs)
    regions = real_hotspot.estimate_stage_map_regions(cells, regions=1, runs=400, largest_area=cells, largest_runs=400, **kwargs)
    return counting, regions


def test_stage4_dense_region_fails_fast_below_the_true_peak(dense, monkeypatch):
    counting, regions = _dense_estimates()
    admission = _stage4_estimate(_dataset(dense, max_operation_bytes=1 << 30))
    assert admission < counting < regions                      # the old admission (5.9 MB) knew nothing of the region build
    limit = 16 * 1024 * 1024                                   # reviewer's budget: old code peaked at 39.8 MB before failing
    assert admission < limit < regions
    outcome, peak = _dense_run(dense, limit, monkeypatch)
    assert isinstance(outcome, NumericContractError) and outcome.code == 'oversized_real_operation'
    assert 'stage-4 regions: 1 regions of 160000 cells' in str(outcome) and f'estimated {regions} bytes' in str(outcome)
    assert peak <= limit                                       # the 22 MB id payload / region mask was never allocated


def test_stage4_count_prepass_is_priced_before_the_labels_exist(dense, monkeypatch):
    counting, regions = _dense_estimates()
    limit = counting - 1                                       # admission and the id payload are not the reason to refuse
    assert _stage4_estimate(_dataset(dense, max_operation_bytes=1 << 30)) <= limit
    import simcompare.electrode_map as electrode_map
    monkeypatch.setattr(electrode_map, '_label_runs', lambda *a, **k: pytest.fail('labels allocated before the prepass was priced'))
    outcome, peak = _dense_run(dense, limit, monkeypatch)
    assert isinstance(outcome, NumericContractError) and outcome.code == 'oversized_real_operation'
    assert 'stage-4 component count: 400 runs of 160000 cells' in str(outcome) and f'estimated {counting} bytes' in str(outcome)
    assert peak <= limit


def test_stage4_dense_region_succeeds_above_the_estimate_within_it(dense, monkeypatch):
    counting, regions = _dense_estimates()
    outcome, peak = _dense_run(dense, max(counting, regions), monkeypatch)      # exactly the estimate is enough
    assert not isinstance(outcome, Exception)
    assert [len(layer.regions) for layer in outcome.layers] == [1] and outcome.layers[0].regions[0].area_px == 160_000
    assert peak <= max(counting, regions)                      # the estimate is a true upper bound of the traced peak


# ---- RA3 B2: a connected comb has few cells per run; the window objects of its one region are priced too --------
@pytest.fixture(scope='module')
def comb_fixture(tmp_path_factory):
    return _fixture(tmp_path_factory.mktemp('guard_comb'), cases=1, size=200, seed=2, shared=1)


def _comb_mask():
    active = np.zeros((200, 200), bool)
    active[:, ::2] = True
    active[0, :] = True                                        # joins the 100 columns into ONE region of 19,901 runs
    return active


def _comb_estimates():
    kwargs = dict(layers_done=1, layer_cases=1, layer_electrodes=2)
    counting = real_hotspot.estimate_stage_map_counting(40_000, regions_done=0, runs=19_901, **kwargs)
    regions = real_hotspot.estimate_stage_map_regions(40_000, regions=1, runs=19_901, largest_area=20_100,
                                                      largest_runs=19_901, **kwargs)
    return counting, regions


def test_comb_mask_is_one_region_with_one_run_per_cell_pair():
    from simcompare.electrode_map import _component_run_counts, _component_sizes, _label_runs, _run_counts
    active = _comb_mask()
    labelled = _label_runs(active, _run_counts(active))
    assert _component_sizes(active, labelled).tolist() == [20_100]
    assert _component_run_counts(labelled).tolist() == [19_901]


def test_stage4_comb_region_succeeds_at_the_estimate_within_it(comb_fixture, monkeypatch):
    counting, regions = _comb_estimates()
    admission = _stage4_estimate(_dataset(comb_fixture, max_operation_bytes=1 << 30))
    assert admission < counting < regions
    assert regions > 6_528_016                                 # the estimate RA3 measured at 11.25 MB of traced peak
    outcome, peak = _dense_run(comb_fixture, regions, monkeypatch, _comb_mask())
    assert not isinstance(outcome, Exception)
    assert [len(layer.regions) for layer in outcome.layers] == [1] and outcome.layers[0].regions[0].area_px == 20_100
    assert 6_528_016 < peak <= regions                         # the old price was below the true peak; the new one covers it


def test_stage4_comb_region_one_byte_below_fails_before_any_window_is_built(comb_fixture, monkeypatch):
    counting, regions = _comb_estimates()
    import simcompare.electrode_map as electrode_map
    monkeypatch.setattr(electrode_map, '_region_windows', lambda *a, **k: pytest.fail('window objects built before the bound'))
    monkeypatch.setattr(electrode_map, 'measure_union', lambda *a, **k: pytest.fail('measure_union ran before the bound'))
    outcome, peak = _dense_run(comb_fixture, regions - 1, monkeypatch, _comb_mask())
    assert isinstance(outcome, NumericContractError) and outcome.code == 'oversized_real_operation'
    assert 'stage-4 regions: 1 regions of 40000 cells' in str(outcome) and f'estimated {regions} bytes' in str(outcome)
    assert peak <= regions - 1


# ---- A4: env-gated hash / decode counters used by the benchmark ----------------------------------------
def test_io_counters_count_hashes_and_decodes_when_enabled(small, monkeypatch):
    assert real_hotspot.IO_COUNTERS is None                       # off by default: no cost, no state
    monkeypatch.setattr(real_hotspot, 'IO_COUNTERS', {'sha256_files': 0, 'sha256_bytes': 0, 'mask_decodes': 0})
    root, assignment = _assignment_of(small)
    manifest = load_real_manifest(root)
    counters = real_hotspot.IO_COUNTERS
    observed = sum(1 for c in manifest['cases'] for r in c['records'] if r['input_status'] == 'observed')
    assert counters['mask_decodes'] == observed and counters['sha256_files'] >= observed and counters['sha256_bytes'] > 0
    before = counters['mask_decodes']
    linepixel.compute_areas(configured_dataset(root, manifest, assignment))
    assert counters['mask_decodes'] - before == observed          # compare decodes every observed mask once


# ---- R3 follow-up: stages that keep no bucket output are not refused for compare's full-detail output --------
@pytest.fixture(scope='module')
def bushy(tmp_path_factory):
    # 12 cases on a 24 x 24 frame: a full-detail group may emit far more entries than the top-20 summary keeps.
    return _fixture(tmp_path_factory.mktemp('guard_bushy'), cases=12, size=24, seed=5, shared=2)


def _estimate_of(config, **kwargs):
    root, assignment = _assignment_of(config)
    manifest = load_real_manifest(root)
    return configured_dataset(root, manifest, assignment, **kwargs).provenance['resource_estimate']['estimated_peak_bytes']


def _between_full_and_summary(config):
    full, summary = _estimate_of(config, top_buckets=None), _estimate_of(config)
    assert full > summary
    return (full + summary) // 2


def test_configured_dataset_admits_summary_by_default_and_full_only_when_asked(bushy):
    limit = _between_full_and_summary(bushy)
    root, assignment = _assignment_of(bushy, max_operation_bytes=limit)
    manifest = load_real_manifest(root)
    assert configured_dataset(root, manifest, assignment).cases                    # default = summary: fits
    with pytest.raises(NumericContractError) as caught:
        configured_dataset(root, manifest, assignment, top_buckets=None)           # full detail keeps every group's output
    assert caught.value.code == 'oversized_real_operation'


def test_compute_areas_default_follows_the_admitted_bucket_detail(bushy, monkeypatch):
    """Direct API: configured_dataset(...); compute_areas(dataset) used to admit top-20 summary and execute full."""
    root, assignment = _assignment_of(bushy)
    manifest = load_real_manifest(root)
    seen = []
    real_group = linepixel._group

    def spy(key, entries, chunk_cells, table_version, **kwargs):
        seen.append(kwargs['top_buckets'])
        return real_group(key, entries, chunk_cells, table_version, **kwargs)
    monkeypatch.setattr(linepixel, '_group', spy)

    summary = configured_dataset(root, manifest, assignment)                          # default admission: top-20 summary
    assert summary.provenance['admitted_bucket_detail'] == {'bucket_detail': 'summary', 'top_buckets': 20}
    default_result = linepixel.compute_areas(summary)
    assert set(seen) == {20} and default_result.groups                                 # ... and the default execution is that
    seen.clear()
    linepixel.compute_areas(summary, bucket_detail='summary', top_buckets=5)           # cheaper than admitted: fine
    assert set(seen) == {5}
    for kwargs in ({'bucket_detail': 'full'}, {'bucket_detail': 'summary', 'top_buckets': 21}):
        with pytest.raises(NumericContractError) as caught:                            # costlier than admitted: refused
            linepixel.compute_areas(summary, **kwargs)
        assert caught.value.code == 'bucket_detail_exceeds_admission'

    seen.clear()
    full = configured_dataset(root, manifest, assignment, top_buckets=None)           # explicit full admission
    assert full.provenance['admitted_bucket_detail'] == {'bucket_detail': 'full'}
    linepixel.compute_areas(full)
    assert set(seen) == {None}                                                         # default execution is full
    seen.clear()
    linepixel.compute_areas(full, bucket_detail='summary', top_buckets=7)              # a cheaper mode is allowed
    assert set(seen) == {7}
    larger = configured_dataset(root, manifest, assignment, top_buckets=50)
    linepixel.compute_areas(larger, bucket_detail='summary', top_buckets=50)
    with pytest.raises(NumericContractError):
        linepixel.compute_areas(larger, bucket_detail='summary', top_buckets=51)


def test_compute_areas_default_without_admission_stays_full(small, monkeypatch):
    """Datasets that never went through an admission estimate (numeric/synthetic) keep the old full default."""
    seen = []
    real_group = linepixel._group
    monkeypatch.setattr(linepixel, '_group', lambda key, entries, chunk_cells, table_version, **kw:
                        seen.append(kw['top_buckets']) or real_group(key, entries, chunk_cells, table_version, **kw))
    dataset = _dataset(small)
    del dataset.provenance['admitted_bucket_detail']
    linepixel.compute_areas(dataset)
    assert set(seen) == {None}


def test_validate_data_admits_what_the_summary_compare_keeps(bushy, tmp_path):
    """Reviewer-style default-limit run: validate-data used to price compare's FULL output and report invalid_configured_input."""
    from simcompare.pipeline import run_numeric_validate
    limit = _between_full_and_summary(bushy)
    cfg = yaml.safe_load(Path(bushy).read_text(encoding='utf-8'))
    for side in ('reference', 'target'):
        cfg[side]['condition_assignment']['max_operation_bytes'] = limit
    result = run_numeric_validate(cfg, tmp_path / 'summary')
    assert 'invalid_configured_input' not in result['reasons']
    cfg['compare'] = {'full_bucket_detail': True}                                  # the same limit is too small for full detail
    result = run_numeric_validate(cfg, tmp_path / 'full')
    assert 'invalid_configured_input' in result['reasons']
