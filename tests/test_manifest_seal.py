"""pasx/T4: load_real_manifest verify=full|seal. A matching seal skips the per-mask hash and decode, never changes the result."""
import copy
import json
import os
import shutil

import pytest

import test_cno_short_guard as tcs
import test_compare2 as tc
from simcompare.adapters import real_hotspot as rh
from simcompare.adapters.real_hotspot import load_real_manifest, make_mask_seal
from simcompare.numeric_schema import NumericContractError

M = {1: {0}, 2: {0, 1}, 3: {0}}
EXCL_CASES = [('lateral_short', 'A', 'L1', 'B', 'L1', M),
              ('vertical_short', 'A', 'SD2', 'C', 'L1', M),     # excluded
              ('vertical_short', 'J', 'L2', 'K', 'L1', M)]


def fixture_base(tmp_path):
    return tcs.build(tmp_path, 'base', include_cno=False)[1]


def fixture_cno(tmp_path):
    return tcs.build(tmp_path, 'cno', include_cno=True)[1]


def fixture_excluded(tmp_path):
    return tc.write_side(tmp_path, 'excl', EXCL_CASES, tc.PAIRS)[1]


def fixture_missing_records(tmp_path):
    return tc.write_side(tmp_path, 'miss', tc.base_cases(), tc.PAIRS, skip=(3,))[1]


FIXTURES = [fixture_base, fixture_cno, fixture_excluded, fixture_missing_records]


@pytest.fixture
def count_mask_reads(monkeypatch):
    calls = {'sha': [], 'load': 0}
    real_sha, real_load = rh._sha, rh._load_mask

    def sha(path):
        calls['sha'].append(str(path))
        return real_sha(path)

    def load(*args, **kwargs):
        calls['load'] += 1
        return real_load(*args, **kwargs)
    monkeypatch.setattr(rh, '_sha', sha)
    monkeypatch.setattr(rh, '_load_mask', load)
    return calls


def mask_files(folder):
    return sorted((folder / 'assets').rglob('*.np*'))


def outcome(folder, **kw):
    try:
        return ('ok', load_real_manifest(folder, **kw))
    except NumericContractError as exc:
        return ('error', exc.code if hasattr(exc, 'code') else str(exc))


@pytest.mark.parametrize('make', FIXTURES)
def test_seal_result_equals_full_and_reads_no_mask(make, tmp_path, count_mask_reads, capsys):
    folder = make(tmp_path)
    full = load_real_manifest(folder)
    assert count_mask_reads['load'] > 0
    seal = make_mask_seal(folder)
    assert seal['schema'] == 'mask_seal_v1' and seal['verification'] == 'full'
    assert [m['path'] for m in seal['masks']] == sorted(m['path'] for m in seal['masks'])
    assert seal['masks'] and set(seal['masks'][0]) == {'path', 'size', 'mtime_ns'}
    count_mask_reads['sha'].clear()
    count_mask_reads['load'] = 0
    capsys.readouterr()
    sealed = load_real_manifest(folder, verify='seal', seal=seal)
    assert count_mask_reads['load'] == 0
    assert not [p for p in count_mask_reads['sha'] if 'manifest.json' not in p and '/assets/' in p.replace(os.sep, '/')]
    assert 'mask_seal_mismatch' not in capsys.readouterr().err
    assert sealed == full
    assert json.dumps(sealed) == json.dumps(full)


def test_cno_fixture_seals_cno_masks(tmp_path):
    folder = fixture_cno(tmp_path)
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['cno_cases']
    cno_paths = {r['mask_path'] for c in manifest['cno_cases'] for r in c['records'] if r['input_status'] == 'observed'}
    assert cno_paths and cno_paths <= {m['path'] for m in make_mask_seal(folder)['masks']}


def test_seal_covers_excluded_cases_masks(tmp_path):
    folder = fixture_excluded(tmp_path)
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    all_paths = {r['mask_path'] for c in manifest['cases'] for r in c['records'] if r['input_status'] == 'observed'}
    assert {m['path'] for m in make_mask_seal(folder)['masks']} == all_paths


def check_fallback(folder, seal, capsys, reason):
    expected = outcome(folder)
    capsys.readouterr()
    got = outcome(folder, verify='seal', seal=seal)
    err = capsys.readouterr().err
    assert f'mask_seal_mismatch:{reason}' in err
    assert got == expected
    return got


def test_manifest_byte_change_falls_back_and_fails_on_hash(tmp_path, capsys):
    folder = fixture_base(tmp_path)
    seal = make_mask_seal(folder)
    path = folder / 'manifest.json'
    text = path.read_text(encoding='utf-8')
    path.write_text(text.replace('"is_synthetic": false', '"is_synthetic":  false'), encoding='utf-8')  # 1 byte, still valid JSON
    got = check_fallback(folder, seal, capsys, 'manifest_sha256')
    assert got[0] == 'ok'  # whitespace only: hash over canonical JSON still passes
    data = json.loads(text)
    data['source_files'] = dict(data['source_files'])
    data['diagnostics'] = data['diagnostics'] + [{'status': 'x'}]
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    got = check_fallback(folder, seal, capsys, 'manifest_sha256')
    assert got == ('error', 'real_manifest_hash_mismatch')


def test_mask_mtime_change_falls_back(tmp_path, capsys):
    folder = fixture_base(tmp_path)
    seal = make_mask_seal(folder)
    target = mask_files(folder)[0]
    st = target.stat()
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert check_fallback(folder, seal, capsys, 'mask_mtime')[0] == 'ok'


def test_mask_size_change_falls_back_and_full_error_is_kept(tmp_path, capsys):
    folder = fixture_base(tmp_path)
    seal = make_mask_seal(folder)
    target = mask_files(folder)[0]
    with open(target, 'ab') as handle:
        handle.write(b'\0')
    assert check_fallback(folder, seal, capsys, 'mask_size')[0] in ('ok', 'error')


def test_mask_content_change_with_size_and_mtime_changed_gives_the_full_error(tmp_path, capsys):
    folder = fixture_base(tmp_path)
    seal = make_mask_seal(folder)
    target = mask_files(folder)[0]
    data = bytearray(target.read_bytes())
    data[-5] ^= 0xFF
    target.write_bytes(bytes(data))
    st = target.stat()
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert check_fallback(folder, seal, capsys, 'mask_mtime')[0] == 'error'


def test_seal_without_one_mask_falls_back(tmp_path, capsys):
    folder = fixture_base(tmp_path)
    seal = make_mask_seal(folder)
    seal['masks'] = seal['masks'][1:]
    assert check_fallback(folder, seal, capsys, 'mask_set')[0] == 'ok'


def test_seal_with_extra_mask_falls_back(tmp_path, capsys):
    folder = fixture_base(tmp_path)
    seal = make_mask_seal(folder)
    extra = copy.deepcopy(seal['masks'][0])
    extra['path'] = 'assets/not_in_manifest.npz'
    seal['masks'].append(extra)
    assert check_fallback(folder, seal, capsys, 'mask_set')[0] == 'ok'


def test_seal_of_another_generation_falls_back(tmp_path, capsys):
    folder = fixture_base(tmp_path)
    other = tcs.build(tmp_path, 'other', include_cno=True)[1]
    foreign = make_mask_seal(other)
    assert check_fallback(folder, foreign, capsys, 'manifest_sha256')[0] == 'ok'
    copy_dir = tmp_path / 'copy'
    shutil.copytree(folder, copy_dir)  # a copy keeps the bytes but not the mtimes' identity guarantee: same manifest, new mtimes
    seal = make_mask_seal(folder)
    got = outcome(copy_dir, verify='seal', seal=seal)
    assert got == outcome(copy_dir)


@pytest.mark.parametrize('field,value,reason', [('schema', 'mask_seal_v0', 'schema'),
                                                 ('verification', 'seal', 'verification')])
def test_wrong_schema_or_verification_falls_back(field, value, reason, tmp_path, capsys):
    folder = fixture_base(tmp_path)
    seal = make_mask_seal(folder)
    seal[field] = value
    assert check_fallback(folder, seal, capsys, reason)[0] == 'ok'


def test_seal_mode_without_seal_falls_back(tmp_path, capsys):
    folder = fixture_base(tmp_path)
    full = load_real_manifest(folder)
    capsys.readouterr()
    assert load_real_manifest(folder, verify='seal') == full
    assert 'mask_seal_mismatch:seal_missing' in capsys.readouterr().err


def test_unknown_verify_value_is_refused(tmp_path):
    folder = fixture_base(tmp_path)
    with pytest.raises(ValueError):
        load_real_manifest(folder, verify='fast')


def test_make_seal_raises_when_full_verification_fails(tmp_path):
    folder = fixture_base(tmp_path)
    target = mask_files(folder)[0]
    data = bytearray(target.read_bytes())
    data[-5] ^= 0xFF
    target.write_bytes(bytes(data))
    with pytest.raises(NumericContractError):
        make_mask_seal(folder)


def test_pipeline_real_source_passes_verify_through(tmp_path, monkeypatch):
    from simcompare import pipeline
    seen = {}

    def fake(root, manifest_path, verify='full', seal=None):
        seen.update(verify=verify, seal=seal)
        return {}
    monkeypatch.setattr(rh, 'load_real_manifest', fake)
    folder = fixture_base(tmp_path)
    cfg = {'mode': 'real', 'base_dir': str(tmp_path)}
    section = {'data_dir': str(folder)}
    pipeline._real_source(cfg, section)
    assert seen == {'verify': 'full', 'seal': None}
    pipeline._real_source(cfg, section, verify='seal', seal={'x': 1})
    assert seen == {'verify': 'seal', 'seal': {'x': 1}}
