import json

import pytest

from simcompare.adapters.real_hotspot import configured_dataset, import_hotspots, load_real_manifest
from simcompare.electrode_map import compute_electrode_maps
from simcompare.numeric_schema import NumericContractError

from test_real_hotspot import _assignment, _run, _source, _write_pairs

CASE_AB, CASE_AC = 'lateral_short-A-L1-B-L1', 'lateral_short-A-L1-C-L1'


def _roots(tmp_path):
    ref, tgt = tmp_path/'ref', tmp_path/'tgt'
    _source(ref, CASE_AB, shape=(1002, 1002)); _source(ref, CASE_AC, shape=(1002, 1002))
    _source(tgt, CASE_AB, shape=(1002, 1002))
    return ref, tgt


def test_union_marks_case_present_only_in_other_root_as_missing(tmp_path):
    ref, tgt = _roots(tmp_path)
    out = tmp_path/'imported_tgt'
    import_hotspots(tgt, out, expected_case_roots=[ref])
    manifest = load_real_manifest(out)
    assert manifest['expected_cases'] == [CASE_AB, CASE_AC]
    assert manifest['expected_case_coverage_status'] == 'confirmed'
    assert manifest['expected_case_source'] == {
        'kind': 'union_of_discovered_roots', 'roots': [ref.as_posix()],
        'limitation': 'cases absent from every root are not detected'}
    assert {'case': CASE_AC, 'status': 'expected_case_folder_missing'} in manifest['diagnostics']
    assert [c['case_name'] for c in manifest['cases']] == [CASE_AB]
    # the reference side sees the union too (nothing missing there)
    out_ref = tmp_path/'imported_ref'
    import_hotspots(ref, out_ref, expected_case_roots=[tgt])
    assert not [d for d in load_real_manifest(out_ref)['diagnostics'] if d.get('status') == 'expected_case_folder_missing']


def test_mutually_exclusive_with_expected_cases_file(tmp_path):
    ref, tgt = _roots(tmp_path)
    names = tmp_path/'names.txt'; names.write_text(CASE_AB+'\n')
    with pytest.raises(NumericContractError, match='expected_cases_sources_exclusive'):
        import_hotspots(tgt, tmp_path/'o', expected_cases=names, expected_case_roots=[ref])
    assert not (tmp_path/'o').exists()
    result = _run('import-hotspots', '--root', tgt, '--out', tmp_path/'o2', '--expected-cases', names,
                  '--expected-cases-from-roots', ref)
    assert result.returncode != 0 and '함께 쓸 수 없습니다' in result.stderr
    assert not (tmp_path/'o2').exists()


def test_cli_option_and_file_source_provenance(tmp_path):
    ref, tgt = _roots(tmp_path)
    out = tmp_path/'cli'
    result = _run('import-hotspots', '--root', tgt, '--out', out, '--expected-cases-from-roots', ref)
    assert result.returncode in (0, 3), result.stderr
    assert load_real_manifest(out)['expected_case_source']['kind'] == 'union_of_discovered_roots'
    names = tmp_path/'names.txt'; names.write_text(CASE_AB+'\n'+CASE_AC+'\n')
    filed = tmp_path/'filed'
    import_hotspots(tgt, filed, expected_cases=names)
    manifest = load_real_manifest(filed)
    assert manifest['expected_case_source'] == {'kind': 'file'}
    plain = tmp_path/'plain'
    import_hotspots(tgt, plain)
    assert load_real_manifest(plain)['expected_case_source'] is None


def _write_rehashed(path, manifest):
    import hashlib
    body = {k: v for k, v in manifest.items() if k not in ('source_payload_hash', 'manifest_hash_recipe')}
    canonical = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    path.write_text(json.dumps(dict(body, source_payload_hash=hashlib.sha256(canonical.encode('utf-8')).hexdigest(),
                                    manifest_hash_recipe=manifest['manifest_hash_recipe'])), encoding='utf-8')


def test_legacy_manifest_without_key_loads_and_bad_shape_rejected(tmp_path):
    ref, tgt = _roots(tmp_path)
    out = tmp_path/'m'
    import_hotspots(tgt, out, expected_case_roots=[ref])
    path = out/'manifest.json'
    manifest = json.loads(path.read_text(encoding='utf-8'))
    legacy = dict(manifest); del legacy['expected_case_source']
    _write_rehashed(path, legacy)
    assert 'expected_case_source' not in load_real_manifest(out)
    for bad in ({'kind': 'other'}, {'kind': 'file', 'x': 1}, {'kind': 'union_of_discovered_roots', 'roots': 'r', 'limitation': 'l'},
                {'kind': 'union_of_discovered_roots', 'roots': []}, 'file'):
        broken = dict(manifest, expected_case_source=bad)
        _write_rehashed(path, broken)
        with pytest.raises(NumericContractError, match='invalid_real_expected_case_source'):
            load_real_manifest(out)
    _write_rehashed(path, dict(manifest, expected_cases=None, expected_case_coverage_status='unconfirmed'))
    with pytest.raises(NumericContractError, match='invalid_real_expected_case_source'):
        load_real_manifest(out)


def test_manifest_hash_is_recomputable_and_equals_file_import_except_provenance(tmp_path):
    import hashlib
    ref, tgt = _roots(tmp_path)
    union, filed = tmp_path/'union', tmp_path/'filed'
    names = tmp_path/'names.txt'; names.write_text(CASE_AB+'\n'+CASE_AC+'\n')
    import_hotspots(tgt, union, expected_case_roots=[ref])
    import_hotspots(tgt, filed, expected_cases=names)
    mu, mf = (json.loads((d/'manifest.json').read_text(encoding='utf-8')) for d in (union, filed))
    recorded = mu.pop('source_payload_hash'); mu.pop('manifest_hash_recipe')
    canonical = json.dumps(mu, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    assert hashlib.sha256(canonical.encode('utf-8')).hexdigest() == recorded
    mf.pop('source_payload_hash'); mf.pop('manifest_hash_recipe')
    mu.pop('expected_case_source'); mf.pop('expected_case_source')
    assert mu == mf


def test_stage4_map_coverage_available_with_union_option_and_partial_for_missing(tmp_path):
    ref = tmp_path/'ref'; tgt = tmp_path/'tgt'
    _source(ref, CASE_AB, shape=(1002, 1002)); _source(tgt, CASE_AB, shape=(1002, 1002))
    both = tmp_path/'both'
    import_hotspots(tgt, both, expected_case_roots=[ref]); _write_pairs(both)
    layer = compute_electrode_maps(configured_dataset(both, load_real_manifest(both), _assignment(pair_table=False)),
                                   diameter_um=1).layers[0]
    assert layer.status == 'available' and layer.coverage['source_coverage_status'] == 'available'
    assert 'expected_case_inventory_unprovided' not in str(layer.coverage)
    _source(ref, CASE_AC, shape=(1002, 1002))
    gap = tmp_path/'gap'
    import_hotspots(tgt, gap, expected_case_roots=[ref]); _write_pairs(gap)
    layer = compute_electrode_maps(configured_dataset(gap, load_real_manifest(gap), _assignment(pair_table=False)),
                                   diameter_um=1).layers[0]
    assert layer.status == 'partial' and layer.coverage['missing']


def test_extra_root_uses_same_discovery_errors(tmp_path):
    ref, tgt = _roots(tmp_path)
    empty = tmp_path/'empty'; empty.mkdir()
    with pytest.raises(NumericContractError, match='no_real_hotspot_cases_found'):
        import_hotspots(tgt, tmp_path/'o', expected_case_roots=[empty])
    with pytest.raises(FileNotFoundError):
        import_hotspots(tgt, tmp_path/'o', expected_case_roots=[tmp_path/'nope'])
    assert not (tmp_path/'o').exists()
