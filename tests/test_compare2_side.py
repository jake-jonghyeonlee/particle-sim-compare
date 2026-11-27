"""compare2-side / compare2-summary / compare2-compose (pasx/T2): golden equality, side.json round trip, binding."""
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

import compare2_golden_cases as gold
import test_compare2 as tc
from simcompare import compare2_side as cs
from simcompare.cli import main
from simcompare.numeric_schema import NumericContractError


# ---- helpers -------------------------------------------------------------------------------------------------------

def side_cmd(config, role, out):
    return main(['compare2-side', '--config', str(config), '--role', role, '--out', str(out)])


def compose_cmd(config, ref, tgt, out):
    return main(['compare2-compose', '--config', str(config), '--side-reference', str(ref),
                 '--side-target', str(tgt), '--out', str(out)])


def same_structure(a, b, path='$'):
    """Equal values with equal types, key order, defaultdict-ness (and factory), tuple/list and set/frozenset kinds."""
    assert type(a) is type(b), (path, type(a), type(b))
    if isinstance(a, defaultdict):
        assert a.default_factory is b.default_factory, path
    if isinstance(a, dict):
        assert list(a) == list(b), (path, 'key order')
        for key in a:
            same_structure(a[key], b[key], f'{path}[{key!r}]')
            assert type(key) is type(next(k for k in b if k == key)), (path, key)
        for ka, kb in zip(a, b):
            same_structure(ka, kb, f'{path}.key')
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b), path
        for index, (x, y) in enumerate(zip(a, b)):
            same_structure(x, y, f'{path}[{index}]')
    elif isinstance(a, Decimal):
        assert str(a) == str(b), path
    else:
        assert a == b, path


def roundtrip(value):
    return cs.decode(json.loads(cs.canonical(cs.encode(value))))


def check_golden(name, out, tmp):
    for file_name in gold.GOLDEN_FILES:
        text = gold.normalize((out / file_name).read_bytes().decode('utf-8'), tmp)
        expected = (gold.GOLDEN_DIR / name / file_name).read_bytes().decode('utf-8')
        assert text == expected, (name, file_name)


@pytest.fixture
def base(tmp_path, monkeypatch):
    code, out = gold.build('base', tmp_path, monkeypatch)
    assert code == 0
    return tmp_path, tmp_path / 'compare2.yaml'


# ---- E1: golden ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('name', sorted(gold.SCENARIOS))
def test_classic_compare2_equals_golden(tmp_path, monkeypatch, name):
    code, out = gold.build(name, tmp_path, monkeypatch)
    assert code == 0
    check_golden(name, out, tmp_path)


@pytest.mark.parametrize('name', sorted(gold.SCENARIOS))
def test_side_side_compose_equals_golden(tmp_path, monkeypatch, name):
    code, _classic = gold.build(name, tmp_path, monkeypatch)
    assert code == 0
    config = tmp_path / 'compare2.yaml'
    assert side_cmd(config, 'reference', tmp_path / 'sref') == 0
    assert side_cmd(config, 'target', tmp_path / 'stgt') == 0
    assert compose_cmd(config, tmp_path / 'sref', tmp_path / 'stgt', tmp_path / 'composed') == 0
    check_golden(name, tmp_path / 'composed', tmp_path)
    compose = json.loads((tmp_path / 'composed' / 'compose.json').read_text(encoding='utf-8'))
    assert compose['schema_version'] == cs.COMPOSE_SCHEMA
    assert compose['compare2_json_sha256'] == cs.sha256_bytes((tmp_path / 'composed' / 'compare2.json').read_bytes())
    assert compose['deps']['compose_basis'] == cs.COMPOSE_BASIS
    assert sorted(compose['deps']['side_sha256']) == ['reference', 'target']


# ---- E2: serialisation ---------------------------------------------------------------------------------------------

def test_encoder_round_trip_fixtures():
    ddict = defaultdict(float)
    ddict['x'] = 1.5
    ddict[('t', 1)] = 0.1
    values = [
        [], [[], [[1, 2], []], 'a'], ([1, [2, 3]], [(4, 5)], ()), {'$dict': 1}, {'$tuple': [1], 'b': {'$dec': 'x'}},
        {1: 'a', 'k': [None, True, False, 0, -1.25e-300, 1e22]}, set(), {3, 1, 2}, {('a', 1), ('b', 2)},
        {Decimal('3.0'): 1, Decimal('3'): 2, Decimal('1E+1'): 3}, ddict, {(): {}}, {'': ''},
        {('L1', 'P1', Decimal('1.0')): {'reasons': {'r2', 'r1'}, 'excl': defaultdict(float)}}]
    for value in values:
        same_structure(value, roundtrip(value))


def test_encoder_objects_are_always_one_key_tags():
    def walk(node):
        if isinstance(node, dict):
            assert len(node) == 1 and next(iter(node)) in cs.TAGS, node
            for item in node.values():
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(cs.encode({'$dict': [1, {'a': (1, [2, {3}])}], 'x': Decimal('1.5')}))


def test_set_order_is_canonical():
    assert cs.canonical(cs.encode({'b', 'a', 'c'})) == cs.canonical(cs.encode({'c', 'a', 'b'}))


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf'), Decimal('NaN'), Decimal('Infinity'),
                                 frozenset([1]), b'x', object(), defaultdict(list), defaultdict(), 1 + 2j, range(2)])
def test_encoder_refuses_unlisted_values(bad):
    with pytest.raises(NumericContractError) as caught:
        cs.encode({'k': [bad]})
    assert caught.value.code == 'compare2_side_unsupported_value'


def test_encoder_refuses_numpy_and_subclasses():
    import numpy as np
    from collections import OrderedDict
    for bad in (np.float64(1.0), np.int64(3), OrderedDict(a=1)):
        with pytest.raises(NumericContractError):
            cs.encode(bad)


@pytest.mark.parametrize('bad', [{'a': 1}, {'$dict': [], 'x': 1}, {'$nope': []}, {'$dict': [[1]]}, {'$dec': 3}, {'$set': 1}])
def test_decoder_refuses_malformed_tags(bad):
    with pytest.raises(NumericContractError) as caught:
        cs.decode(bad)
    assert caught.value.code == 'compare2_side_invalid'


def test_side_json_round_trips_the_measured_side(base):
    tmp, config = base
    from simcompare.compare2 import _load_side, load_pair_config
    cfg2, cfg, top = load_pair_config(config)
    assert side_cmd(config, 'reference', tmp / 'sref') == 0
    document, _sha = cs.read_side(tmp / 'sref')
    extracted, meta = _load_side(cfg, 'reference', top)
    same_structure(extracted, document['extracted'])
    same_structure(meta, document['meta'])
    assert any(isinstance(key[2], Decimal) for key in extracted['cells'])


# ---- side.json is independent of the role ----------------------------------------------------------------------

def test_side_json_does_not_depend_on_the_role(base):
    tmp, config = base
    numeric = yaml.safe_load((tmp / 'numeric.yaml').read_text(encoding='utf-8'))
    numeric['target'] = json.loads(json.dumps(numeric['reference']))  # the same product section under the other role
    (tmp / 'numeric.yaml').write_text(yaml.safe_dump(numeric), encoding='utf-8')
    assert side_cmd(config, 'reference', tmp / 'as_ref') == 0
    assert side_cmd(config, 'target', tmp / 'as_tgt') == 0
    assert (tmp / 'as_ref' / 'side.json').read_bytes() == (tmp / 'as_tgt' / 'side.json').read_bytes()
    assert b'"reference"' not in (tmp / 'as_ref' / 'side.json').read_bytes()
    assert side_cmd(config, 'reference', tmp / 'again') == 0  # deterministic
    assert (tmp / 'again' / 'side.json').read_bytes() == (tmp / 'as_ref' / 'side.json').read_bytes()


# ---- summary -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('name', ['base', 'subset', 'cno', 'absent'])
def test_summary_equals_the_pair_document(tmp_path, monkeypatch, name):
    code, out = gold.build(name, tmp_path, monkeypatch)
    assert code == 0
    config = tmp_path / 'compare2.yaml'
    document = json.loads((out / 'compare2.json').read_text(encoding='utf-8'))
    for role in ('reference', 'target'):
        assert side_cmd(config, role, tmp_path / f's_{role}') == 0
        assert main(['compare2-summary', '--config', str(config), '--role', role, '--side', str(tmp_path / f's_{role}'),
                     '--out', str(tmp_path / f'sum_{role}')]) == 0
        summary = json.loads((tmp_path / f'sum_{role}' / 'summary.json').read_text(encoding='utf-8'))
        side = document['sides'][role]
        for key in ('vi_line', 'vi_pixel', 'caa_line', 'caa_pixel', 'lambda', 'status', 'reasons'):
            assert summary[key] == side[key], (name, role, key)
        assert summary['conditions']['line'] + summary['conditions']['pixel'] == len(side['conditions'])
        assert summary['absent_expected_cases'] == side.get('absent_expected_cases', {}).get('count', 0)
        assert summary['deps']['summary_basis'] == cs.SUMMARY_BASIS
        assert summary['deps']['side_sha256'] == cs.sha256_bytes((tmp_path / f's_{role}' / 'side.json').read_bytes())


def test_density_change_moves_the_summary_deps_not_the_side(base):
    tmp, config = base
    assert side_cmd(config, 'reference', tmp / 'sref') == 0
    summary_cmd = ['compare2-summary', '--config', str(config), '--role', 'reference', '--side', str(tmp / 'sref')]
    assert main(summary_cmd + ['--out', str(tmp / 'sum1')]) == 0
    side_before = (tmp / 'sref' / 'side.json').read_bytes()
    density = tmp / 'density' / 'mobile_line.csv'
    density.write_text(density.read_text(encoding='utf-8').replace('5.0', '5.5'), encoding='utf-8')
    assert main(summary_cmd + ['--out', str(tmp / 'sum2')]) == 0
    first = json.loads((tmp / 'sum1' / 'summary.json').read_text(encoding='utf-8'))
    second = json.loads((tmp / 'sum2' / 'summary.json').read_text(encoding='utf-8'))
    assert first['deps']['density_sha256']['line'] != second['deps']['density_sha256']['line']
    assert first['deps']['density_sha256']['pixel'] == second['deps']['density_sha256']['pixel']
    assert first['deps']['side_sha256'] == second['deps']['side_sha256']
    assert (tmp / 'sref' / 'side.json').read_bytes() == side_before
    assert side_cmd(config, 'reference', tmp / 'sref_again') == 0  # a density change does not touch the side
    assert (tmp / 'sref_again' / 'side.json').read_bytes() == side_before


# ---- compose refuses a stale side --------------------------------------------------------------------------------

def stale_code(tmp, config):
    code = compose_cmd(config, tmp / 'sref', tmp / 'stgt', tmp / 'composed')
    error = json.loads((tmp / 'composed' / 'error.json').read_text(encoding='utf-8'))
    return code, error


def make_sides(tmp, config):
    assert side_cmd(config, 'reference', tmp / 'sref') == 0
    assert side_cmd(config, 'target', tmp / 'stgt') == 0


def test_compose_refuses_a_changed_pair_table(base):
    tmp, config = base
    make_sides(tmp, config)
    pairs = tmp / 'ref' / 'imp' / 'pairs.csv'
    pairs.write_text(pairs.read_text(encoding='utf-8') + 'ZZ,Y,Z,line,v1\n', encoding='utf-8')
    code, error = stale_code(tmp, config)
    assert code == 2 and error['code'] == 'compare2_side_stale' and 'pair_table_sha256' in error['detail']
    assert not (tmp / 'composed' / 'compare2.json').exists()


def test_compose_refuses_a_changed_manifest(base):
    tmp, config = base
    make_sides(tmp, config)
    manifest = tmp / 'tgt' / 'imp' / 'manifest.json'
    manifest.write_bytes(manifest.read_bytes() + b'\n')
    code, error = stale_code(tmp, config)
    assert code == 2 and error['code'] == 'compare2_side_stale' and 'manifest_sha256' in error['detail']


def test_compose_refuses_a_changed_section(base):
    tmp, config = base
    make_sides(tmp, config)
    numeric = yaml.safe_load((tmp / 'numeric.yaml').read_text(encoding='utf-8'))
    numeric['reference']['condition_assignment']['unit_id'] = 'U2'
    (tmp / 'numeric.yaml').write_text(yaml.safe_dump(numeric), encoding='utf-8')
    code, error = stale_code(tmp, config)
    assert code == 2 and error['code'] == 'compare2_side_stale' and 'section_sha256' in error['detail']


def test_compose_writes_compose_json_last_and_owns_it(base):
    tmp, config = base
    make_sides(tmp, config)
    assert compose_cmd(config, tmp / 'sref', tmp / 'stgt', tmp / 'composed') == 0
    assert (tmp / 'composed' / 'compose.json').exists()
    assert main(['compare2', '--config', str(config), '--out', str(tmp / 'composed')]) == 0  # a classic re-run owns it
    assert not (tmp / 'composed' / 'compose.json').exists()  # and drops the stale compose.json
    (tmp / 'composed' / 'foreign.txt').write_text('x', encoding='utf-8')
    assert compose_cmd(config, tmp / 'sref', tmp / 'stgt', tmp / 'composed') == 2
    assert not (tmp / 'composed' / 'error.json').exists()  # not owned: nothing written
