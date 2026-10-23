"""Phase B T2: stage-4 streaming map, slim ROI export, candidates without re-reads, asset memo, stage-5 read gate.

Equality with the frozen 7bcb07a package is proved in tests/test_stage46_equivalence.py (maps, whole candidates tree with the
documented review_rois expansion, confirm-pairs tree). This file checks the properties that equality cannot show: what is loaded,
what is kept, what is written once, and where the guards stop.
"""
import dataclasses
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

import numpy as np
import pytest
import yaml

import simcompare.adapters.real_hotspot as real_hotspot
from simcompare import confirm_pairs, roi_export
from simcompare.adapters.numeric import load_dataset
from simcompare.adapters.real_hotspot import configured_dataset, load_real_manifest
from simcompare.electrode_map import ElectrodeRegion, compute_electrode_maps
from simcompare.numeric_schema import NumericContractError
from simcompare.roi_export import AssetMemo, check_asset_manifest, expand_region_coverages, export_review_rois

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'make_compare_fixture.py'
NUMERIC = Path(__file__).parent / 'fixtures/review_roi_v1'


def _fixture(out, **kwargs):
    spec = importlib.util.spec_from_file_location('make_compare_fixture', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build(out, **kwargs)


def _dataset(config, **limits):
    cfg = yaml.safe_load(Path(config).read_text(encoding='utf-8'))
    assignment = {**cfg['reference']['condition_assignment'], **limits}
    root = Path(cfg['reference']['data_dir'])
    return configured_dataset(root, load_real_manifest(root), assignment)


@pytest.fixture(scope='module')
def small(tmp_path_factory):
    return _fixture(tmp_path_factory.mktemp('stage4_small'), cases=12, size=60, seed=3, shared=3)


# ---- what the map loads and keeps --------------------------------------------------------------------------------

def test_map_loads_one_representative_mask_per_case_and_no_other_record(small, monkeypatch):
    dataset = _dataset(small)
    loaded = []
    original = real_hotspot._load_mask
    monkeypatch.setattr(real_hotspot, '_load_mask',
                        lambda asset, record, *a, **k: loaded.append(record['diameter_um']) or original(asset, record, *a, **k))
    monkeypatch.setattr(real_hotspot, 'IO_COUNTERS', {'sha256_files': 0, 'sha256_bytes': 0, 'mask_decodes': 0})
    maps = compute_electrode_maps(dataset, diameter_um=5, min_count=2)
    assert loaded == [5.0] * len(dataset.cases)                       # 12 loads for 12 x 32 records
    assert real_hotspot.IO_COUNTERS['map_loads'] == len(dataset.cases)   # the map-only counter of the benchmark
    assert sum(len(layer.regions) for layer in maps.layers) > 0


def test_a_corrupt_non_representative_mask_is_not_read_by_the_map(small, monkeypatch):
    dataset = _dataset(small)
    original = real_hotspot._load_mask

    def only_the_representative(asset, record, *a, **k):
        if record['diameter_um'] != 5.0:
            pytest.fail(f'map read the {record["diameter_um"]} um mask')
        return original(asset, record, *a, **k)
    monkeypatch.setattr(real_hotspot, '_load_mask', only_the_representative)
    compute_electrode_maps(dataset, diameter_um=5, min_count=2)       # manifest authentication (stage 1/2 path) is unchanged


def test_header_only_validation_still_rejects_bad_metadata(small):
    dataset = _dataset(small)
    case = dataset.cases[0]
    wrong = dataclasses.replace(case, analysis_layer='')
    with pytest.raises(NumericContractError):
        compute_electrode_maps([wrong], diameter_um=5)


def test_regions_store_packed_masks_without_changing_the_public_field(small):
    dataset = _dataset(small)
    maps = compute_electrode_maps(dataset, diameter_um=5, min_count=2)
    regions = [r for layer in maps.layers for r in layer.regions]
    assert regions
    assert [f.name for f in dataclasses.fields(ElectrodeRegion)][:11] == [
        'region_id', 'key', 'max_count', 'area_px', 'area_um2', 'bbox_yx', 'region_bbox_um', 'center_xy_um', 'frame', 'mask',
        'electrodes']
    for region in regions:
        mask = region.mask
        assert mask.dtype == bool and mask.shape == region.frame.shape_yx and not mask.flags.writeable
        assert int(mask.sum()) == region.area_px
        assert mask is not region.mask and np.array_equal(mask, region.mask)   # a fresh read-only unpack on each access
        assert 'mask' not in vars(region)                                      # only the packed bits are stored
        packed = next(v for k, v in vars(region).items() if k.startswith('_packed'))
        assert packed.bits.nbytes == (mask.size + 7) // 8
    rebuilt = ElectrodeRegion(**{f.name: getattr(regions[0], f.name) for f in dataclasses.fields(ElectrodeRegion)})
    assert np.array_equal(rebuilt.mask, regions[0].mask)


def test_to_dict_has_no_new_fields_and_arrays_still_serialise(small):
    dataset = _dataset(small)
    maps = compute_electrode_maps(dataset, diameter_um=5, min_count=2)
    payload = maps.to_dict()
    region = next(r for layer in payload['layers'] for r in layer['regions'])
    assert list(region) == [f.name for f in dataclasses.fields(ElectrodeRegion)]
    assert isinstance(region['mask'], list) and len(region['mask']) == 60
    assert set(payload['layers'][0]) == {'key', 'frame', 'counts', 'status', 'reasons', 'coverage', 'regions'}
    assert not any('packed' in key for key in json.dumps(payload))


def test_links_share_one_read_only_copy_per_case_and_layer_coverage(small):
    dataset = _dataset(small)
    maps = compute_electrode_maps(dataset, diameter_um=5, min_count=1)
    regions = [r for layer in maps.layers for r in layer.regions]
    by_case = {}
    for region in regions:
        for link in region.cases:
            by_case.setdefault(link.identity['case_id'], []).append(link)
    shared = [links for links in by_case.values() if len(links) > 1]
    assert shared, 'the fixture must link a case into several regions'
    for links in shared:
        assert all(l.raw is links[0].raw and l.source_metadata is links[0].source_metadata and l.identity is links[0].identity
                   for l in links)
    source = {c.identity.case_id: c for c in dataset.cases}
    for case_id, links in by_case.items():
        assert links[0].raw == source[case_id].raw and links[0].raw is not source[case_id].raw       # a copy, not the dataset's
        assert links[0].source_metadata == source[case_id].metadata
    # the identity lists of a layer exist once; the three window scalars are the only per-region coverage values
    assert len({id(r.coverage['observed']) for r in regions}) <= len(maps.layers)
    assert len({id(r.provenance) for r in regions}) == 1


# ---- the slim ROI export --------------------------------------------------------------------------------------

def _numeric_export(tmp_path, root=NUMERIC):
    dataset = load_dataset(root)
    maps = compute_electrode_maps(dataset, min_count=3)
    reference = {'path': 'review_assets.json', 'sha256': hashlib.sha256((root/'review_assets.json').read_bytes()).hexdigest()}
    export_review_rois(dataset, root, tmp_path, maps=maps, selections={'map_region_ids': 'all', 'manual': []},
                       asset_manifest=reference, producer_commit='fixture')
    return json.loads((tmp_path/'review_rois.json').read_text(encoding='utf-8')), dataset, maps


def test_rows_keep_scalar_coverage_and_reference_the_identity_lists_once(tmp_path):
    payload, _, maps = _numeric_export(tmp_path)
    rows = [r for r in payload['rois'] if r['source_kind'] == 'electrode_count_region']
    assert len(rows) >= 2 and list(payload)[:6] == ['schema_version', 'producer_commit', 'producer_commit_reason',
                                                    'source_manifest_hash', 'assets_root', 'region_coverages']
    assert payload['schema_version'] == 'review_roi_v1'
    refs = {r['provenance']['coverage']['observed_ref'] for r in rows}
    assert refs == set(payload['region_coverages']) and len(refs) == 1
    region = next(r for layer in maps.layers for r in layer.regions)
    for row in rows:
        coverage = row['provenance']['coverage']
        assert not any(k in coverage for k in roi_export.COVERAGE_LISTS)
        assert coverage['source_coverage_status'] == region.coverage['source_coverage_status']
        assert coverage['source_coverage_summary'] == region.coverage['source_coverage_summary']
        assert coverage['region_valid_area_um2'] == region.coverage['region_valid_area_um2']
    shared = payload['region_coverages'][next(iter(refs))]
    assert list(shared) == list(roi_export.COVERAGE_LISTS) and shared['observed'] == json.loads(json.dumps(region.coverage['observed']))


def test_expansion_restores_the_identity_lists_in_their_original_position(tmp_path):
    payload, _, maps = _numeric_export(tmp_path)
    expanded = expand_region_coverages(payload)
    assert 'region_coverages' not in expanded and list(expanded) == [k for k in payload if k != 'region_coverages']
    region = next(r for layer in maps.layers for r in layer.regions)
    for row in expanded['rois']:
        if row['source_kind'] == 'electrode_count_region':
            assert list(row['provenance']['coverage']) == list(region.coverage)
            assert row['provenance']['coverage'] == json.loads(json.dumps(region.coverage))
    assert expand_region_coverages(expanded) == expanded            # exports without references are returned as they are


def test_manual_only_exports_are_byte_identical_to_the_old_format(tmp_path):
    dataset = load_dataset(NUMERIC)
    reference = {'path': 'review_assets.json', 'sha256': hashlib.sha256((NUMERIC/'review_assets.json').read_bytes()).hexdigest()}
    manual = {'manual': [{'product_id': 'PRODUCT', 'design_version': 'DESIGN', 'frame_id': 'FRAME', 'case_id': 'A',
                          'center_xy_um': [102.5, 201.5]}]}
    export_review_rois(dataset, NUMERIC, tmp_path, maps=None, selections=manual, asset_manifest=reference, producer_commit='x')
    payload = json.loads((tmp_path/'review_rois.json').read_text(encoding='utf-8'))
    assert 'region_coverages' not in payload
    assert (tmp_path/'review_rois.json').read_bytes() == (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
                                                          + '\n').encode('utf-8')


def test_streamed_json_equals_dumps_and_failures_leave_the_old_file(tmp_path, monkeypatch):
    payload = {'text': 'α β "q" \\ \n', 'rows': [{'x': i / 3, 'y': [i, None, True, {'z': 'é'}]} for i in range(50)], 'empty': [], 'e': {}}
    path = tmp_path/'out.json'
    roi_export._json_stream(path, payload)
    assert path.read_bytes() == (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    before = path.read_bytes()
    with pytest.raises(ValueError):
        roi_export._json_stream(path, {'rows': [1, float('nan')]})        # allow_nan=False fails mid-stream
    assert path.read_bytes() == before and [p.name for p in tmp_path.iterdir()] == ['out.json']   # no temp file is left


def test_file_sha256_streams_in_chunks(tmp_path):
    path = tmp_path/'f.bin'
    path.write_bytes(bytes(range(256)) * 1000)
    assert roi_export.file_sha256(path, chunk=7) == hashlib.sha256(path.read_bytes()).hexdigest()


# ---- the asset memo -----------------------------------------------------------------------------------------------

def _clone_numeric(tmp_path, name='root'):
    root = tmp_path/name
    shutil.copytree(NUMERIC, root)
    reference = {'path': 'review_assets.json', 'sha256': hashlib.sha256((root/'review_assets.json').read_bytes()).hexdigest()}
    return root, reference


def test_memo_serves_the_second_check_without_decoding_and_gives_the_same_answer(tmp_path, monkeypatch):
    root, reference = _clone_numeric(tmp_path)
    dataset = load_dataset(root)
    plain, plain_reasons = check_asset_manifest(dataset, root, reference)
    reads = []
    original = roi_export.read_binary
    monkeypatch.setattr(roi_export, 'read_binary', lambda *a, **k: reads.append(a[1]) or original(*a, **k))
    memo = AssetMemo()
    first = check_asset_manifest(dataset, root, reference, memo=memo)
    decoded = len(reads)
    second = check_asset_manifest(dataset, root, reference, memo=memo)
    assert first == second == (plain, plain_reasons) and decoded > 0
    assert len(reads) == decoded and memo.hits >= decoded and memo.decodes == decoded
    assert all(isinstance(v, tuple) and isinstance(v[0], tuple) and isinstance(v[1], str) for v in memo.entries.values())  # no arrays


def _reasons(result):
    return {key: value['reasons'] for key, value in result[0].items()}


def test_a_hit_still_authenticates_the_current_bytes(tmp_path):
    root, reference = _clone_numeric(tmp_path)
    dataset = load_dataset(root)
    memo = AssetMemo()
    assert all(not r for r in _reasons(check_asset_manifest(dataset, root, reference, memo=memo)).values())
    layout = root/'assets/layout_a.npy'
    layout.write_bytes(layout.read_bytes() + b'\0')                    # changed after it was validated and memoised
    with_memo = check_asset_manifest(dataset, root, reference, memo=memo)
    without = check_asset_manifest(dataset, root, reference)
    assert with_memo == without
    assert any('input_hash_mismatch' in r for r in _reasons(with_memo).values())
    layout.unlink()                                                    # missing after it was validated
    assert check_asset_manifest(dataset, root, reference, memo=memo) == check_asset_manifest(dataset, root, reference)


def test_same_relative_paths_in_different_roots_are_different_assets(tmp_path):
    root_a, ref_a = _clone_numeric(tmp_path, 'a')
    root_b, ref_b = _clone_numeric(tmp_path, 'b')
    other = np.load(root_b/'assets/layout_a.npy').copy()
    other[0, 0] = 1 - other[0, 0]
    np.save(root_b/'assets/layout_a.npy', other)                       # same path + new bytes; manifest sha stays -> mismatch
    manifest = json.loads((root_b/'review_assets.json').read_text())
    new_sha = hashlib.sha256((root_b/'assets/layout_a.npy').read_bytes()).hexdigest()
    for case in manifest['cases']:
        for item in case['assets']['layouts']:
            if item['asset']['path'] == 'assets/layout_a.npy':
                item['asset']['sha256'] = new_sha
    (root_b/'review_assets.json').write_text(json.dumps(manifest))
    ref_b = {'path': 'review_assets.json', 'sha256': hashlib.sha256((root_b/'review_assets.json').read_bytes()).hexdigest()}
    memo = AssetMemo()
    first = check_asset_manifest(load_dataset(root_a), root_a, ref_a, memo=memo)
    second = check_asset_manifest(load_dataset(root_b), root_b, ref_b, memo=memo)
    assert second == check_asset_manifest(load_dataset(root_b), root_b, ref_b)
    assert first == check_asset_manifest(load_dataset(root_a), root_a, ref_a)
    keys = {key[0] for key in memo.entries}
    assert any(str(root_a.resolve()) in k for k in keys) and any(str(root_b.resolve()) in k for k in keys)


def test_same_bytes_with_opposite_semantics_are_different_entries_and_valid_is_rechecked(tmp_path):
    root, reference = _clone_numeric(tmp_path)
    dataset = load_dataset(root)
    memo = AssetMemo()
    check_asset_manifest(dataset, root, reference, memo=memo)
    manifest = json.loads((root/'review_assets.json').read_text())
    for case in manifest['cases']:
        case['assets']['valid']['value_semantics'] = 'binary_zero_is_marked'   # the same bytes now decode to the complement
    (root/'review_assets.json').write_text(json.dumps(manifest))
    flipped = {'path': 'review_assets.json', 'sha256': hashlib.sha256((root/'review_assets.json').read_bytes()).hexdigest()}
    with_memo = check_asset_manifest(dataset, root, flipped, memo=memo)
    assert with_memo == check_asset_manifest(dataset, root, flipped)
    assert any('source_valid_mismatch' in r for r in _reasons(with_memo).values())      # the complement is not frame.valid
    assert len({key[2] for key in memo.entries}) == 2


def test_memo_is_bounded(tmp_path):
    root, reference = _clone_numeric(tmp_path)
    memo = AssetMemo(max_entries=2)
    result = check_asset_manifest(load_dataset(root), root, reference, memo=memo)
    assert len(memo.entries) == 2 and result == check_asset_manifest(load_dataset(root), root, reference)


# ---- candidates: one write per export, nothing re-read ---------------------------------------------------------

@pytest.fixture(scope='module')
def candidates_run(tmp_path_factory):
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import stage46_generators as gen
    base, ref, tgt = gen.scenario(tmp_path_factory, 'shared')
    cfg = gen.write_config(base/'stage4_streaming.yaml', ref, tgt, diameter_um=1)
    return gen, base, ref, tgt, cfg


def _run_candidates(cfg, out, k=2):
    from simcompare import cli
    return cli.main(['candidates', '--config', str(cfg), '--min-count', str(k), '--window-um', '1', '--merge-overlap', '0.5',
                     '--out', str(out)])


def test_candidates_never_reads_its_exports_back_and_hashes_them_by_streaming(candidates_run, tmp_path, monkeypatch):
    _, _, _, _, cfg = candidates_run
    reads = []
    for name in ('read_text', 'read_bytes'):
        original = getattr(Path, name)
        monkeypatch.setattr(Path, name, (lambda original: lambda self, *a, **k: reads.append(self.name) or original(self, *a, **k))(original))
    hashed = []
    original_hash = roi_export.file_sha256
    monkeypatch.setattr(roi_export, 'file_sha256', lambda path, *a, **k: hashed.append(Path(path).name) or original_hash(path, *a, **k))
    out = tmp_path/'cand'
    assert _run_candidates(cfg, out) in (0, 3)
    assert 'review_rois.json' not in reads                             # never parsed or hashed through read_text/read_bytes
    payload = json.loads((out/'candidate_queries_v1.json').read_text(encoding='utf-8'))
    for name in ('reference', 'target'):
        assert payload['inputs'][name]['export_sha256'] == hashlib.sha256((out/f'{name}_export/review_rois.json').read_bytes()).hexdigest()
        assert payload['inputs'][name]['index_export_sha256'] == hashlib.sha256(
            (out/f'{name}_index_export/review_rois.json').read_bytes()).hexdigest()
    assert hashed.count('review_rois.json') == 4


def test_candidates_writes_each_export_once(candidates_run, tmp_path, monkeypatch):
    writes = []
    original = roi_export._json_stream
    monkeypatch.setattr(roi_export, '_json_stream', lambda path, payload: writes.append(Path(path)) or original(path, payload))
    out = tmp_path/'cand'
    assert _run_candidates(candidates_run[4], out) in (0, 3)
    assert len(writes) == len(set(writes)) == 4                        # query + index export of each product, one write each


def test_second_product_is_admitted_with_the_first_ones_rows_reserved(candidates_run, tmp_path, monkeypatch):
    _, _, _, _, cfg = candidates_run
    seen = []
    import simcompare.electrode_map as electrode_map
    wrapped = electrode_map.compute_electrode_maps
    monkeypatch.setattr(electrode_map, 'compute_electrode_maps',
                        lambda *a, **k: seen.append(k.get('reserved_bytes')) or wrapped(*a, **k))
    assert _run_candidates(cfg, tmp_path/'cand') in (0, 3)
    assert seen[0] == 0 and seen[1] > 0                                # the target map is built with the reference rows held


def test_second_product_admission_counts_the_first_products_configured_dataset(candidates_run, tmp_path, monkeypatch):
    """RB-A7: the first product's configured dataset stays alive (index coverage) while the second one is admitted."""
    _, _, _, _, cfg = candidates_run
    seen, held = [], []
    import simcompare.electrode_map as electrode_map
    wrapped = electrode_map.compute_electrode_maps
    monkeypatch.setattr(electrode_map, 'compute_electrode_maps',
                        lambda dataset, *a, **k: (seen.append(k.get('reserved_bytes')), held.append(real_hotspot.dataset_held_bytes(dataset)))
                        and wrapped(dataset, *a, **k))
    assert _run_candidates(cfg, tmp_path/'cand') in (0, 3)
    assert held[0] >= real_hotspot.STAGE6_FIXED_BYTES
    assert seen[1] >= held[0] + real_hotspot.MAP_REGION_INFO_BYTES      # rows/regions AND the first dataset


def test_roles_gate_is_in_the_single_write(candidates_run, tmp_path):
    gen, base, ref, tgt, cfg = candidates_run
    out = tmp_path/'cand'
    _run_candidates(cfg, out)
    for folder in ('reference_export', 'target_export', 'reference_index_export', 'target_index_export'):
        payload = json.loads((out/folder/'review_rois.json').read_text(encoding='utf-8'))
        blocked = [r for r in payload['rois'] if 'electrode_role_required_for_matching' in r['reasons']]
        assert all(r['status'] == 'blocked' for r in blocked)
        if blocked:
            assert payload['status'] == 'partial'


# ---- stage 5: read gate ---------------------------------------------------------------------------------------

def test_stage5_estimate_arithmetic():
    est = real_hotspot.estimate_stage_confirm_read
    assert est([]) == 0
    assert est([100, 300]) == 400 * real_hotspot.STAGE5_PARSE_MULTIPLIER + 300 * real_hotspot.STAGE5_READ_TRANSIENT


def test_stage5_gate_boundary_and_real_only(small):
    dataset = _dataset(small, max_operation_bytes=1 << 30)
    sizes = [10_000, 20_000]
    needed = real_hotspot.estimate_stage_confirm_read(sizes)
    dataset.provenance['resource_estimate']['max_operation_bytes'] = needed
    real_hotspot.guard_stage_confirm_read('confirm-pairs', [dataset, dataset], sizes)          # exactly the estimate is enough
    dataset.provenance['resource_estimate']['max_operation_bytes'] = needed - 1
    with pytest.raises(NumericContractError) as caught:
        real_hotspot.guard_stage_confirm_read('confirm-pairs', [dataset, dataset], sizes)
    assert caught.value.code == 'oversized_real_operation' and f'estimated {needed} bytes exceed limit {needed - 1}' in str(caught.value)
    from simcompare.adapters.numeric import load_dataset as load_numeric
    real_hotspot.guard_stage_confirm_read('confirm-pairs', [load_numeric(NUMERIC)], [10 ** 15])   # numeric datasets carry no limit


def test_stage5_refuses_before_any_export_is_read(candidates_run, tmp_path, monkeypatch):
    gen, base, ref, tgt, cfg = candidates_run
    out = tmp_path/'cand'
    assert _run_candidates(cfg, out) in (0, 3)
    match_dir = tmp_path/'matches'
    match_dir.mkdir()
    matches = gen.fake_matches(out, {'reference': ref, 'target': tgt}, match_dir, seed=1)
    decisions = gen.decisions_file(tmp_path/'decisions.csv', matches, seed=1)
    sizes = [(out/folder/'review_rois.json').stat().st_size for folder in
             ('reference_export', 'target_export', 'reference_index_export', 'target_index_export')]
    needed = real_hotspot.estimate_stage_confirm_read(sizes)
    original = confirm_pairs._read_json_bytes

    def no_exports(path, problems, label):
        if str(path).endswith('review_rois.json'):
            pytest.fail(f'export read before the size gate: {path}')
        return original(path, problems, label)
    monkeypatch.setattr(confirm_pairs, '_read_json_bytes', no_exports)
    profile = real_hotspot._dataset_profile
    monkeypatch.setattr(real_hotspot, '_dataset_profile',
                        lambda dataset: None if profile(dataset) is None else (*profile(dataset)[:3], needed - 1, *profile(dataset)[4:]))
    from simcompare import pipeline
    cfg_dict = pipeline.load_numeric_config(cfg)
    with pytest.raises(NumericContractError) as caught:
        confirm_pairs.run_confirm_pairs(cfg_dict, tmp_path/'confirm', candidates_dir=out, matches=matches,
                                        decisions_path=decisions, labelled_by='tester', mutual_tolerance_um=0.5)
    assert caught.value.code == 'oversized_real_operation' and 'stage-5 export read' in str(caught.value)
    assert not (tmp_path/'confirm').exists()


def test_stage5_gate_also_prices_the_dl2_match_files(candidates_run, tmp_path, monkeypatch):
    """RB-A8: a limit that admits the four exports alone refuses once the --match JSONs are priced; nothing is read."""
    gen, base, ref, tgt, cfg = candidates_run
    out = tmp_path/'cand'
    assert _run_candidates(cfg, out) in (0, 3)
    match_dir = tmp_path/'matches'
    match_dir.mkdir()
    matches = gen.fake_matches(out, {'reference': ref, 'target': tgt}, match_dir, seed=1)
    decisions = gen.decisions_file(tmp_path/'decisions.csv', matches, seed=1)
    sizes = [(out/folder/'review_rois.json').stat().st_size for folder in
             ('reference_export', 'target_export', 'reference_index_export', 'target_index_export')]
    match_sizes = [Path(path).stat().st_size for path in matches.values()]
    assert all(match_sizes)
    exports_only = real_hotspot.estimate_stage_confirm_read(sizes)
    needed = real_hotspot.estimate_stage_confirm_read(sizes + match_sizes)
    assert needed > exports_only
    original = confirm_pairs._read_json_bytes
    guarded = {str(path) for path in matches.values()}

    def no_big_reads(path, problems, label):
        if str(path).endswith('review_rois.json') or str(path) in guarded:
            pytest.fail(f'read before the size gate: {path}')
        return original(path, problems, label)
    monkeypatch.setattr(confirm_pairs, '_read_json_bytes', no_big_reads)
    profile = real_hotspot._dataset_profile
    monkeypatch.setattr(real_hotspot, '_dataset_profile',
                        lambda dataset: None if profile(dataset) is None else (*profile(dataset)[:3], needed - 1, *profile(dataset)[4:]))
    from simcompare import pipeline
    with pytest.raises(NumericContractError) as caught:
        confirm_pairs.run_confirm_pairs(pipeline.load_numeric_config(cfg), tmp_path/'confirm', candidates_dir=out, matches=matches,
                                        decisions_path=decisions, labelled_by='tester', mutual_tolerance_um=0.5)
    assert caught.value.code == 'oversized_real_operation' and f'estimated {needed} bytes exceed limit {needed - 1}' in str(caught.value)


def test_stage5_accepts_slim_and_old_form_exports_alike(candidates_run, tmp_path):
    """The same decisions give the same pairs whether the exports carry observed_ref or the full identity lists."""
    gen, base, ref, tgt, cfg = candidates_run
    slim = tmp_path/'slim'
    assert _run_candidates(cfg, slim) in (0, 3)
    assert any('region_coverages' in (slim/f).read_text(encoding='utf-8') for f in
               ('reference_export/review_rois.json', 'target_export/review_rois.json'))
    rows = {}
    for folder in ('reference_export', 'target_export', 'reference_index_export', 'target_index_export'):
        payload = json.loads((slim/folder/'review_rois.json').read_text(encoding='utf-8'))
        rows[folder] = (payload, expand_region_coverages(payload))
    for label, pick in (('slim', 0), ('old', 1)):
        work = tmp_path/label
        shutil.copytree(slim, work/'cand')
        if pick:   # rewrite the exports in the pre-reference form and re-record their hashes
            payload_file = work/'cand'/'candidate_queries_v1.json'
            payload = json.loads(payload_file.read_text(encoding='utf-8'))
            for folder, side, key in (('reference_export', 'reference', 'export_sha256'), ('target_export', 'target', 'export_sha256'),
                                      ('reference_index_export', 'reference', 'index_export_sha256'),
                                      ('target_index_export', 'target', 'index_export_sha256')):
                path = work/'cand'/folder/'review_rois.json'
                path.write_text(json.dumps(rows[folder][1], ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
                payload['inputs'][side][key] = hashlib.sha256(path.read_bytes()).hexdigest()
            payload_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        matches = gen.fake_matches(work/'cand', {'reference': ref, 'target': tgt}, work, seed=2)
        decisions = gen.decisions_file(work/'decisions.csv', matches, seed=2, accept=0.8)
        from simcompare import cli
        argv = ['confirm-pairs', '--config', str(cfg), '--candidates', str(work/'cand'), '--decisions', str(decisions),
                '--labelled-by', 'tester', '--mutual-tolerance-um', '0.5', '--out', str(work/'confirm')]
        for direction, path in matches.items():
            argv += ['--match', f'{direction}={path}']
        assert cli.main(argv) in (0, 3)
    first = json.loads((tmp_path/'slim/confirm/roi_correspondence.json').read_text(encoding='utf-8'))
    second = json.loads((tmp_path/'old/confirm/roi_correspondence.json').read_text(encoding='utf-8'))
    assert first == second and first['pairs']


# ---- DL2 handoff -----------------------------------------------------------------------------------------------

def test_dl2_validator_accepts_the_slim_exports_under_review_roi_v1(candidates_run, tmp_path):
    """particle-sim-dl2 (sibling checkout, import only) reads schema_version and provenance.coverage.source_coverage_status
    of region rows and nothing else of the coverage: the slim form keeps both, so the version stays review_roi_v1."""
    import sys
    sibling = Path(__file__).resolve().parents[2]/'particle-sim-dl2'/'src'
    if not (sibling/'psdl2'/'data.py').is_file():
        pytest.skip('sibling particle-sim-dl2 checkout not available')
    sys.path.insert(0, str(sibling))
    try:
        data = pytest.importorskip('psdl2.data')
        out = tmp_path/'cand'
        assert _run_candidates(candidates_run[4], out) in (0, 3)
        assert data.SCHEMA_VERSION == 'review_roi_v1'
        for folder in ('reference_export', 'target_export', 'reference_index_export'):
            path = out/folder/'review_rois.json'
            slim = data.load_export(path)
            old = tmp_path/f'{folder}_old.json'
            old.write_text(json.dumps(expand_region_coverages(json.loads(path.read_text(encoding='utf-8'))),
                                      ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
            expanded = data.load_export(old)
            assert [r.row['roi_id'] for r in slim.ready] == [r.row['roi_id'] for r in expanded.ready] and slim.ready
            assert [r['reasons'] for r in slim.blocked] == [r['reasons'] for r in expanded.blocked]
    finally:
        sys.path.remove(str(sibling))
        for name in [n for n in sys.modules if n == 'psdl2' or n.startswith('psdl2.')]:
            del sys.modules[name]


def test_stage5_parse_multiplier_bounds_the_measured_retained_size(candidates_run, tmp_path):
    import gc
    import tracemalloc
    out = tmp_path/'cand'
    assert _run_candidates(candidates_run[4], out) in (0, 3)
    checked = 0
    for folder in ('reference_export', 'target_export', 'reference_index_export', 'target_index_export'):
        data = (out/folder/'review_rois.json').read_bytes()
        tracemalloc.start()
        before = tracemalloc.get_traced_memory()[0]
        parsed = json.loads(data.decode('utf-8'))
        gc.collect()
        retained = tracemalloc.get_traced_memory()[0] - before
        tracemalloc.stop()
        assert retained <= real_hotspot.STAGE5_PARSE_MULTIPLIER * len(data), (folder, retained, len(data))
        assert parsed['rois']
        checked += 1
    assert checked == 4


# ---- guards across the generator scenarios -------------------------------------------------------------------------

def _scenario_dataset(candidates_run, directory, name, limit=1 << 34):
    gen = candidates_run[0]
    base, ref, tgt = gen.scenario(_Factory(directory), name)
    manifest = real_hotspot.load_real_manifest(ref.data_dir)
    return configured_dataset(ref.data_dir, manifest, {**ref.assignment, 'max_operation_bytes': limit}), ref


class _Factory:
    """The generator caches scenarios per name, so the directory factory is only used when a scenario is not built yet."""
    def __init__(self, tmp):
        self.tmp = tmp

    def mktemp(self, name, numbered=True):
        path = Path(self.tmp)/name
        path.mkdir(parents=True, exist_ok=True)
        return path


SCENARIOS = ('shared', 'distinct', 'multilayer', 'components')


@pytest.mark.parametrize('name', SCENARIOS)
def test_every_phase_estimate_bounds_the_traced_peak_and_one_byte_below_refuses(candidates_run, name, tmp_path_factory, monkeypatch):
    import tracemalloc
    seen = {}
    dataset, _ = _scenario_dataset(candidates_run, tmp_path_factory.mktemp('guard_scn'), name)
    cells = next(iter(dataset.frames.values())).valid.size
    with monkeypatch.context() as patch:
        for guard, estimate in (('guard_stage_map_counting', real_hotspot.estimate_stage_map_counting),
                                ('guard_stage_map_regions', real_hotspot.estimate_stage_map_regions),
                                ('guard_stage_map_links', real_hotspot.estimate_stage_map_regions)):
            def spy(stage, data, _g=guard, _e=estimate, _o=getattr(real_hotspot, guard), **kwargs):
                seen[_g] = max(seen.get(_g, 0), _e(cells, **kwargs))
                return _o(stage, data, **kwargs)
            patch.setattr(real_hotspot, guard, spy)
        reference = compute_electrode_maps(dataset, diameter_um=1, min_count=2)
    layers = {}
    for case in dataset.cases:
        layers[case.analysis_layer] = layers.get(case.analysis_layer, 0) + 1
    seen['admission'] = real_hotspot.estimate_stage_map_admission(cells, list(layers.values()), len(layers))
    estimate = max(seen.values())
    assert sum(len(layer.regions) for layer in reference.layers) > 0
    dataset.provenance['resource_estimate']['max_operation_bytes'] = estimate
    tracemalloc.start()
    try:
        maps = compute_electrode_maps(dataset, diameter_um=1, min_count=2)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak <= estimate and len(maps.layers) == len(reference.layers)
    dataset.provenance['resource_estimate']['max_operation_bytes'] = estimate - 1
    with pytest.raises(NumericContractError) as caught:
        compute_electrode_maps(dataset, diameter_um=1, min_count=2)
    assert caught.value.code == 'oversized_real_operation' and f'estimated {estimate} bytes' in str(caught.value)


def test_reserved_bytes_are_added_to_the_admission_before_any_mask_is_loaded(small, monkeypatch):
    dataset = _dataset(small)
    cells = next(iter(dataset.frames.values())).valid.size
    base = real_hotspot.estimate_stage_map_admission(cells, [len(dataset.cases)], 1)
    dataset.provenance['resource_estimate']['max_operation_bytes'] = base + 999
    monkeypatch.setattr(real_hotspot, '_load_mask', lambda *a, **k: pytest.fail('mask loaded before the admission check'))
    with pytest.raises(NumericContractError) as caught:
        compute_electrode_maps(dataset, diameter_um=5, reserved_bytes=1000)
    assert f'estimated {base + 1000} bytes exceed limit {base + 999}' in str(caught.value)


def test_export_rows_are_priced_before_any_row_exists(candidates_run, tmp_path_factory, tmp_path, monkeypatch):
    dataset, model = _scenario_dataset(candidates_run, tmp_path_factory.mktemp('guard_export'), 'shared')
    maps = compute_electrode_maps(dataset, diameter_um=1, min_count=2)
    planned = sum(len(r.cases) for layer in maps.layers for r in layer.regions)
    assert planned > 0
    cells = next(iter(dataset.frames.values())).valid.size
    links = planned
    needed = real_hotspot.estimate_stage_map_export(cells, layers=len(maps.layers),
                                                    regions=sum(len(l.regions) for l in maps.layers), links=links,
                                                    rows=planned, cases=len(dataset.cases))
    reference = {'path': 'review_assets.json',
                 'sha256': hashlib.sha256((model.data_dir/'review_assets.json').read_bytes()).hexdigest()}
    selections = {'map_region_ids': 'all', 'manual': []}
    dataset.provenance['resource_estimate']['max_operation_bytes'] = needed
    export_review_rois(dataset, model.data_dir, tmp_path/'ok', maps=maps, selections=selections, asset_manifest=reference,
                       producer_commit='x')                                         # exactly the estimate is enough
    dataset.provenance['resource_estimate']['max_operation_bytes'] = needed - 1
    monkeypatch.setattr(roi_export, '_stable_id', lambda *a, **k: pytest.fail('a row was built before the bound'))
    with pytest.raises(NumericContractError) as caught:
        export_review_rois(dataset, model.data_dir, tmp_path/'refused', maps=maps, selections=selections,
                           asset_manifest=reference, producer_commit='x')
    assert caught.value.code == 'oversized_real_operation' and f'stage-4 export: {planned} rows' in str(caught.value)
    assert not (tmp_path/'refused'/'review_rois.json').exists()
