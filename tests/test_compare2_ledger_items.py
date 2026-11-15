"""ledger/T3: compare2 ledger_items / ledger_check / inputs.ledger_basis on the small real_hotspot_v1 fixtures."""
import hashlib
import json
import re

import pytest

from simcompare import compare2
from test_compare2 import PAIRS, STACK, base_cases, cond_by_id, load, run, two_sides

NEW_KEYS = ('ledger_items', 'ledger_check')


def strip_new_keys(doc):
    """The compare2 document without the three keys ledger/T3 added (sides.*.ledger_items, sides.*.ledger_check,
    inputs.ledger_basis)."""
    doc = json.loads(json.dumps(doc))
    for side in doc['sides'].values():
        for key in NEW_KEYS:
            side.pop(key, None)
    doc['inputs'].pop('ledger_basis', None)
    return doc


def masked_digest(out, name, tmp):
    """sha256 of an output file as test_compare2.GOLDEN masks it; compare2.json is written without the new keys."""
    data = (out / name).read_bytes().decode('utf-8')
    if name == 'compare2.json':
        data = compare2._json_text(strip_new_keys(json.loads(data)))
    text = data.replace(str(tmp), 'TMP')
    return hashlib.sha256(re.sub(r'[0-9a-f]{64}', 'H', text).encode('utf-8')).hexdigest()


OUTPUT_NAMES = ('compare2.json', 'compare2_summary.csv', 'compare2_layers.csv', 'compare2_conditions.csv',
                'compare2_cno.csv')

VERTICAL = ('vertical_short', 'X', 'L1', 'Y', 'L2', {1: set(), 2: {3, 4}, 3: {3, 4, 5}})
THK = 'layers\tdistance\nL1L2\t0.5\n'


def fixture_plain():
    ref, tgt = two_sides()
    return ref, tgt, {}


def fixture_filter():
    ref, tgt = two_sides()
    return ref, tgt, {'diameters': [3]}


def fixture_vertical():
    pairs = PAIRS + [('XY', 'X', 'Y', 'line')]
    return (base_cases() + [VERTICAL], pairs), (base_cases() + [VERTICAL], pairs), {'thk': THK}


FIXTURES = {'plain': fixture_plain, 'diameter_filter': fixture_filter, 'vertical_thk': fixture_vertical}

# sha256 of the five outputs taken from the code at 40234b4 (before ledger/T3), masked like test_compare2.GOLDEN
BASELINE = {
    'plain': {
        'compare2.json': 'b22968a547a603211d58cd4eb4f1601398c102ddd27f2ff81524a7a6cc3ee83c',
        'compare2_summary.csv': '4306ab9e66b91f6fbb1a7a2ef2261966ed0845eebae0b1d139f8b1c392b38c27',
        'compare2_layers.csv': '01cf8566d35d359e9ac38583127eb6987cd9fd5f232e3960ef27ed9c049a30c9',
        'compare2_conditions.csv': '14aaab246c3c1b800375091fe6d5461e2612d969bde5c12650a7547897ea2a60',
        'compare2_cno.csv': '1a3fb0477c7fa69017b27a6fae93c3f42a24eb65b86b1949c3d36d4368655f6c'},
    'diameter_filter': {
        'compare2.json': '284061ee2c0116c0a907fba096392de3c5a0b9b01f633fc8ca148ce51b7c410c',
        'compare2_summary.csv': '768210c72ffeea751991454df95cecca6a188846f29e93495897735269d4ed35',
        'compare2_layers.csv': 'd64d89b837a92a7164c0390a1cfb9bb1f0ec03baaf1abcd2a763bb200029312f',
        'compare2_conditions.csv': '70f592443397d3ac7a40e1279128ecf8727cf0b0c7a2e46e6c54ffe61737ffa9',
        'compare2_cno.csv': '1a3fb0477c7fa69017b27a6fae93c3f42a24eb65b86b1949c3d36d4368655f6c'},
    'vertical_thk': {
        'compare2.json': '38758d67ab9f1ee3669a2c6a0f44af4fd4cc20d86de82c5d259c35bbe8f0a230',
        'compare2_summary.csv': 'fcc6bf8f418ca6bb2a09aeb2ef52b9295d1319d8922ab1d96cdcb9bd13d2ca96',
        'compare2_layers.csv': '4fa46aa121184e3cfbf5da37159ba28545d8abf1a7dc1f0964c67bbdfead435b',
        'compare2_conditions.csv': 'f314f91367e08cfd8553d65e9df3d2f34bd60eff23949999550d70692f357467',
        'compare2_cno.csv': '1a3fb0477c7fa69017b27a6fae93c3f42a24eb65b86b1949c3d36d4368655f6c'}}


@pytest.mark.parametrize('name', sorted(FIXTURES))
def test_outputs_without_the_new_keys_equal_the_baseline(tmp_path, name):
    ref, tgt, kwargs = FIXTURES[name]()
    code, out = run(tmp_path, ref, tgt, **kwargs)
    assert code == 0, (out / 'compare2.json').read_text()
    got = {file: masked_digest(out, file, tmp_path) for file in OUTPUT_NAMES}
    assert got == BASELINE.get(name), name + '\n' + '\n'.join(f'    {k!r}: {v!r},' for k, v in got.items())


# ---- the new keys: invariants on the shared fixtures ---------------------------------------------------------------

def items_of(doc, role='reference'):
    return doc['sides'][role]['ledger_items']


def item_lambda(doc, role, kind):
    return sum(item['lambda'] for item in items_of(doc, role) if item['class'] == kind)


@pytest.mark.parametrize('name', sorted(FIXTURES))
def test_item_lambda_sums_equal_the_side_totals_and_the_shared_lambda(tmp_path, name):
    ref, tgt, kwargs = FIXTURES[name]()
    code, out = run(tmp_path, ref, tgt, **kwargs)
    doc = load(out)
    assert doc['inputs']['ledger_basis'] == 'pair_share_v1' and doc['schema_version'] == 'compare2_v1'
    for role in ('reference', 'target'):
        side = doc['sides'][role]
        check = side['ledger_check']
        assert check['status'] == 'passed', check
        assert check['integer_checks']['status'] == 'passed'
        for kind in ('line', 'pixel'):
            assert check[f'lambda_{kind}']['status'] == 'passed' and check[f'shared_{kind}']['status'] == 'passed'
            assert item_lambda(doc, role, kind) == pytest.approx(side['lambda'][kind], rel=1e-12, abs=1e-15)
            excl = sum(c['lambda_excl'] for c in side['conditions'] if c['class'] == kind)
            assert item_lambda(doc, role, kind) - excl == pytest.approx(side['shared'][f'lambda_{kind}'], abs=1e-12)
        keys = [(i['analysis_layer'], i['pair_id'], i['failure_type'],
                 tuple((e['electrode_id'], e['actual_layer']) for e in i['endpoints']), i['class'])
                for i in side['ledger_items']]
        assert keys == sorted(keys) and len({i['item_id'] for i in side['ledger_items']}) == len(keys)
        for item in side['ledger_items']:
            assert item['computable'] and item['lambda'] == pytest.approx(
                sum(row['lambda'] for row in item['by_size']), abs=1e-15)


# ---- hand calculation: shared 2 and 3 layers, transfer, lateral + vertical of one pair -----------------------------

HAND_PAIRS = [('AB', 'A', 'B', 'line'), ('AC', 'A', 'C', 'line'), ('AF', 'A', 'F', 'line'), ('DE', 'D', 'E', 'pixel'),
              ('EF', 'E', 'F', 'pixel'), ('XY', 'X', 'Y', 'line'), ('GH', 'G', 'H', 'line')]


def hand_cases():
    return [('lateral_short', 'A', 'L1', 'B', 'L1', {1: {0, 1, 2, 3}}),
            ('lateral_short', 'A', 'L1', 'C', 'L1', {1: {2, 3, 4}}),
            ('lateral_short', 'A', 'L1', 'F', 'L1', {1: {3, 5}}),
            ('lateral_short', 'D', 'L1', 'E', 'L1', {1: {1, 2, 6}}),
            ('lateral_short', 'E', 'L1', 'F', 'L1', {1: {6, 7}}),
            ('lateral_short', 'X', 'L2', 'Y', 'L2', {1: {0, 1, 2}}),
            ('vertical_short', 'X', 'L1', 'Y', 'L2', {1: {1, 2, 3}}),
            ('lateral_short', 'G', 'L2', 'H', 'L2', {1: {2, 3}})]


def test_hand_calculated_three_overlaps_transfer_and_split_pair(tmp_path):
    code, out = run(tmp_path, (hand_cases(), HAND_PAIRS), (hand_cases(), HAND_PAIRS), thk=THK)
    assert code == 0, (out / 'compare2.json').read_text()
    doc = load(out)
    got = {i['item_id']: i for i in items_of(doc)}
    cell = 0.01
    # L1 line (density 5.0): cell 0 AB; 1 AB; 2 AB+AC; 3 AB+AC+AF; 4 AC; 5 AF
    # L1 pixel (density 3.0): DE {1, 2} lie under line (transferred), {6} shared with EF; EF {6, 7}
    # L2 line (density 4.0): XY lateral {0,1,2} + vertical {1,2,3}, GH {2,3}: cell 2 and 3 shared by XY and GH
    expect = {
        'AB|lateral_short|L1|L1|A@L1,B@L1': (1 + 1 + 1 / 2 + 1 / 3, 5.0, 'line', 0.0),
        'AC|lateral_short|L1|L1|A@L1,C@L1': (1 / 2 + 1 / 3 + 1, 5.0, 'line', 0.0),
        'AF|lateral_short|L1|L1|A@L1,F@L1': (1 / 3 + 1, 5.0, 'line', 0.0),
        'DE|lateral_short|L1|L1|D@L1,E@L1': (1 / 2, 3.0, 'pixel', 2 * cell),
        'EF|lateral_short|L1|L1|E@L1,F@L1': (1 / 2 + 1, 3.0, 'pixel', 0.0),
        'XY|lateral_short|L2|L2|X@L2,Y@L2': (1 + 1 / 2 + 1 / 4, 4.0, 'line', 0.0),
        'XY|vertical_short|L2|L1|X@L1,Y@L2': (1 / 2 + 1 / 4 + 1 / 2, 4.0, 'line', 0.0),
        'GH|lateral_short|L2|L2|G@L2,H@L2': (1 / 2 + 1 / 2, 4.0, 'line', 0.0)}
    assert set(got) == set(expect)
    for item_id, (cells, density, kind, transferred) in expect.items():
        item = got[item_id]
        assert item['class'] == kind
        assert item['lambda'] == pytest.approx(cells * cell * density, abs=1e-15), item_id
        assert item['area_share_um2'] == pytest.approx(cells * cell, abs=1e-6)
        assert item['transferred_out_area_um2'] == pytest.approx(transferred, abs=1e-6)
        used = [r for r in item['by_size'] if r['area_share_um2'] > 0]   # the other sizes have zero-area rows
        assert [r['diameter_um'] for r in used] == ([1.0] if cells else []) and (
            {r['density_layer'] for r in item['by_size']} == {'P2' if item['analysis_layer'] == 'L2' else 'P1'})
    vertical = got['XY|vertical_short|L2|L1|X@L1,Y@L2']
    assert (vertical['upper_layer'], vertical['lower_layer'], vertical['analysis_layer']) == ('L2', 'L1', 'L2')
    assert vertical['endpoints'] == [{'electrode_id': 'X', 'actual_layer': 'L1'},
                                     {'electrode_id': 'Y', 'actual_layer': 'L2'}]
    assert vertical['electrodes'] == ['X', 'Y']
    side = doc['sides']['reference']
    assert side['ledger_check']['status'] == 'passed'
    assert side['ledger_check']['integer_checks']['line']['multi_item_overlap_cells'] == 2
    assert side['lambda']['line'] == pytest.approx((6 * 5.0 + 4 * 4.0) * cell)
    assert side['lambda']['pixel'] == pytest.approx(2 * 3.0 * cell)


# ---- counterexamples -------------------------------------------------------------------------------------------

def test_pair_on_one_side_only_has_items_on_that_side_only(tmp_path):
    tgt_cases = base_cases(AC=None) + [('lateral_short', 'K', 'L1', 'L', 'L1', {1: {8, 9}, 2: {10}})]
    code, out = run(tmp_path, (base_cases(), PAIRS), (tgt_cases, PAIRS + [('KL', 'K', 'L', 'line')]))
    assert code == 0
    doc = load(out)
    ref_pairs = {i['pair_id'] for i in items_of(doc, 'reference')}
    tgt_pairs = {i['pair_id'] for i in items_of(doc, 'target')}
    assert 'AC' in ref_pairs - tgt_pairs and 'KL' in tgt_pairs - ref_pairs
    for role in ('reference', 'target'):
        assert doc['sides'][role]['ledger_check']['status'] == 'passed'


def test_absent_expected_case_makes_no_item(tmp_path, monkeypatch):
    import test_compare2_expected_zero as ez
    ref, tgt = ez.two_one_sided()
    code, out, doc = ez.run_pair_in(tmp_path, monkeypatch, 'absent', ref, tgt)
    assert doc['sides']['target']['absent_expected_cases']['count'] == 1
    ref_pairs = {i['pair_id'] for i in items_of(doc, 'reference')}
    tgt_pairs = {i['pair_id'] for i in items_of(doc, 'target')}
    assert 'AC' in ref_pairs and 'AC' not in tgt_pairs and 'GH' in tgt_pairs and 'GH' not in ref_pairs
    for role in ('reference', 'target'):
        assert doc['sides'][role]['ledger_check']['status'] == 'passed'
        assert doc['sides'][role]['ledger_check']['lambda_line']['status'] == 'passed'


def test_density_missing_with_zero_area_keeps_a_zero_item(tmp_path):
    cases = base_cases(IJ={1: set()})
    pixel = {'P1': DENS_PIXEL_P1, 'gcnt': DENS_PIXEL_P1}
    code, out = run(tmp_path, (cases, PAIRS), (cases, PAIRS), pixel=pixel)
    assert code == 0, load(out)['sides']['reference']['reasons']
    side = load(out)['sides']['reference']
    (item,) = [i for i in side['ledger_items'] if i['pair_id'] == 'IJ']
    assert item['lambda'] == 0.0 and item['computable'] and item['area_share_um2'] == 0.0
    assert 'density_missing_zero_area:pixel' in item['reasons']
    assert side['ledger_check']['status'] == 'passed'


DENS_PIXEL_P1 = {1: 3.0, 2: 1.5, 3: 0.75}


def test_swapped_direction_pair_items_split_the_overlap(tmp_path):
    cases = [('vertical_short', 'X', 'L1', 'Y', 'L2', {1: {0, 1}}), ('vertical_short', 'Y', 'L1', 'X', 'L2', {1: {1, 2}})]
    pairs = [('XY', 'X', 'Y', 'line')]
    code, out = run(tmp_path, (cases, pairs), (cases, pairs), thk=THK)
    assert code == 0, (out / 'compare2.json').read_text()
    items = items_of(load(out))
    assert [i['item_id'] for i in items] == ['XY|vertical_short|L2|L1|X@L1,Y@L2', 'XY|vertical_short|L2|L1|X@L2,Y@L1']
    for item in items:   # pair covers 0,1,2 (n=1); cell 1 is in both items (m=2): 1 + 1/2
        assert item['lambda'] == pytest.approx(1.5 * 0.01 * 4.0, abs=1e-15)
    assert load(out)['sides']['reference']['ledger_check']['status'] == 'passed'


def test_items_of_the_diameter_filter(tmp_path):
    from decimal import Decimal
    from collections import defaultdict
    from simcompare.compare2_inputs import GroupConfig, ProductConfig

    class Table:
        def lookup(self, layer, diameter):
            return 2.0, None

    def cell(excl, ledger):
        item = compare2._new_cell()
        item['excl_l'] = defaultdict(float, excl)
        item['line'] = sum(excl.values())
        item['ledger'] = {('line', pid, 'lateral_short', ((pid[0], 'L1'), (pid[1], 'L1'))): {
            'upper': 'L1', 'lower': 'L1', 'share': area, 'own': area, 'out': 0.0,
            'endpoints': [{'electrode_id': pid[0], 'actual_layer': 'L1'}, {'electrode_id': pid[1], 'actual_layer': 'L1'}]}
            for pid, area in ledger.items()}
        return item

    # AB has cells only at 1 um, AC only at 3 um; the filter keeps 3 um
    cells = {('L1', 'P1', Decimal(1)): cell({'AB': 0.04}, {'AB': 0.04}),
             ('L1', 'P1', Decimal(3)): cell({'AC': 0.02}, {'AC': 0.02})}
    extracted = {'cells': cells, 'classes': {('AB', 'L1'): 'line', ('AC', 'L1'): 'line'},
                 'electrodes': {('AB', 'L1'): ['A', 'B'], ('AC', 'L1'): ['A', 'C']}}
    root = tmp_path / 'root'
    root.mkdir()
    product = ProductConfig('reference', 'mobile', (20, 10), root)
    group = GroupConfig('mobile', 50.0, 1.0, 1.0)
    meta = {'expected_case_coverage_status': 'confirmed', 'same_name_cases_unresolved': False}
    tables = {'line': Table(), 'pixel': Table()}
    side = compare2._compute_side('reference', product, group, tables, extracted, meta, diameters={Decimal(3)})
    by_pair = {i['pair_id']: i for i in side['ledger_items']}
    assert by_pair['AB']['lambda'] == 0.0 and by_pair['AB']['computable']
    assert by_pair['AB']['reasons'] == ['diameter_filter_removed_all'] and by_pair['AB']['by_size'] == []
    assert by_pair['AC']['lambda'] == pytest.approx(2.0 * 0.02 * 1.0) and len(by_pair['AC']['by_size']) == 1
    assert side['ledger_check']['status'] == 'passed'
    assert side['ledger_check']['lambda_line']['total'] == pytest.approx(0.04)
    full = compare2._compute_side('reference', product, group, tables, extracted, meta)
    assert {i['pair_id']: i['lambda'] for i in full['ledger_items']} == pytest.approx({'AB': 0.08, 'AC': 0.04})


def test_glass_pending_gives_not_evaluated_and_no_new_reason(tmp_path):
    code, out = run(tmp_path, (base_cases(), PAIRS), (base_cases(), PAIRS), glass=None)
    assert code == 3
    side = load(out)['sides']['reference']
    assert side['ledger_check']['status'] == 'not_evaluated'
    assert side['ledger_check']['lambda_line']['status'] == 'not_evaluated'
    assert side['ledger_check']['integer_checks']['status'] == 'passed'
    assert 'ledger_check_failed' not in side['reasons'] and 'glass_area_pending' in side['reasons']
    assert side['ledger_items'] and all(i['lambda'] is None and not i['computable'] for i in side['ledger_items'])


def test_failed_ledger_check_is_partial_with_exit_3(tmp_path, monkeypatch):
    original = compare2._extract_ledger

    def broken(cell, shares, pitch_area):
        original(cell, shares, pitch_area)
        for entry in cell['ledger'].values():
            entry['share'] *= 2          # item areas no longer add up to the group area

    monkeypatch.setattr(compare2, '_extract_ledger', broken)
    code, out = run(tmp_path, (base_cases(), PAIRS), (base_cases(), PAIRS))
    assert code == 3
    doc = load(out)
    execution = json.loads((out / 'execution.json').read_text())
    for role in ('reference', 'target'):
        side = doc['sides'][role]
        assert side['status'] == 'partial' and 'ledger_check_failed' in side['reasons']
        assert side['ledger_check']['status'] == 'failed' and side['ledger_check']['lambda_line']['status'] == 'failed'
        assert side['conservation_check']['status'] == 'passed' and 'conservation_check_failed' not in side['reasons']
        assert f'{role}:ledger_check_failed' in execution['reasons']
    assert execution['exit_code'] == 3 and execution['status'] == 'partial'
