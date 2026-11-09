"""compare2: an expected case whose folder is absent on one side counts as 0 area (confirmed inventory only).

The side builder of test_compare2 writes the expected-case list of its own cases; here the list is the union of both
sides, so a case that exists on one side only is an `expected_case_folder_missing` case on the other.
"""
import hashlib
import re

import numpy as np
import pytest
from PIL import Image

import test_compare2 as tc
from simcompare.adapters.real_hotspot import import_hotspots
from simcompare.numeric_schema import DIAMETERS_UM

NOTE = 'absent_expected_case_counted_zero'
BASE = {case[1] + case[3]: case for case in tc.base_cases()}  # 'AB' -> its lateral_short case tuple
UNION_SHA = {  # sha256 (masked like test_compare2.GOLDEN) of the outputs at 70ce93f for the union-inventory fixture
    'compare2.json': '656dcd639ca6512afd7ed6fa75f8ec420f46d1f02d35f8f5664dbad96cfb65b8',
    'compare2_summary.csv': '4306ab9e66b91f6fbb1a7a2ef2261966ed0845eebae0b1d139f8b1c392b38c27',
    'compare2_layers.csv': '01cf8566d35d359e9ac38583127eb6987cd9fd5f232e3960ef27ed9c049a30c9',
    'compare2_conditions.csv': '14aaab246c3c1b800375091fe6d5461e2612d969bde5c12650a7547897ea2a60',
    'compare2_cno.csv': '1a3fb0477c7fa69017b27a6fae93c3f42a24eb65b86b1949c3d36d4368655f6c'}


def pick(*pids):
    return [BASE[pid] for pid in pids]


def name_of(case):
    defect, ea, la, eb, lb, _masks = case
    return f'{defect}-{ea}-{la}-{eb}-{lb}'


def write_side_inventory(base, name, cases, pairs, expected, cno=None, skip=(), no_csv=()):
    """tc.write_side with an explicit expected-case list (None: no list, unconfirmed) and CSV-less cases."""
    root = base / name / 'src'
    for case in cases:
        defect, _ea, _la, _eb, _lb, masks = case
        folder = root / name_of(case)
        (folder / 'result').mkdir(parents=True)
        for electrode in ('1.png', '2.png'):
            Image.new('L', (1004, 1004), 0).save(folder / electrode)
        rows = ['defect_size(um),Area(um2)']
        for diameter in DIAMETERS_UM:
            if diameter in skip:
                continue
            cells = masks.get(diameter, ())
            array = np.zeros((4, 4), dtype=np.uint8)
            for cell in cells:
                array[cell // 4, cell % 4] = 255
            Image.fromarray(array).save(folder / f'hotspot_{defect}{diameter:02d}.png')
            rows.append(f'{diameter},{len(cells) * tc.CELL:.2f}')
        if name_of(case) not in no_csv:
            (folder / 'result' / f'{defect}.csv').write_text('\n'.join(rows) + '\n', encoding='utf-8')
    for cno_name, text in (cno or {}).items():
        folder = root / cno_name / 'result'
        folder.mkdir(parents=True)
        (folder / 'lateral_open.csv').write_text(text, encoding='utf-8')
    imported = base / name / 'imp'
    if expected is None:
        import_hotspots(root, imported)
    else:
        expected_file = base / name / 'expected.txt'
        expected_file.write_text('\n'.join(expected) + '\n', encoding='utf-8')
        import_hotspots(root, imported, expected_cases=expected_file)
    (imported / 'pairs.csv').write_text(
        'pair_id,electrode_a_id,electrode_b_id,class,pair_table_version\n'
        + ''.join(f'{pid},{a},{b},{cls},v1\n' for pid, a, b, cls in pairs), encoding='utf-8')
    return root, imported


def run_inventory(tmp, monkeypatch, ref, tgt, inventory='union', no_csv=(), skip_sides=None, extra_expected=(), **kwargs):
    """tc.run with the expected list: 'union' (both sides' cases), 'own' (each side's cases) or None (unconfirmed)."""
    union = sorted({name_of(c) for c in ref[0]} | {name_of(c) for c in tgt[0]} | set(extra_expected))
    expected = {'ref': union, 'tgt': union} if inventory == 'union' else (
        {'ref': [name_of(c) for c in ref[0]], 'tgt': [name_of(c) for c in tgt[0]]} if inventory == 'own' else {})

    def writer(base, name, cases, pairs, cno=None, skip=()):
        skip = (skip_sides or {}).get(name, skip)
        return write_side_inventory(base, name, cases, pairs, expected.get(name), cno=cno, skip=skip, no_csv=no_csv)

    monkeypatch.setattr(tc, 'write_side', writer)
    code, out = tc.run(tmp, ref, tgt, **kwargs)
    return code, out, tc.load(out)


def run_pair_in(tmp, monkeypatch, label, ref, tgt, **kwargs):
    folder = tmp / label
    folder.mkdir()
    return run_inventory(folder, monkeypatch, ref, tgt, **kwargs)


def two_one_sided():
    """reference has AC (L1, line) that the target lacks; target has GH (L2, line) that the reference lacks."""
    return (pick('AB', 'DE', 'AC'), tc.PAIRS), (pick('AB', 'DE', 'GH'), tc.PAIRS)


def with_zero_folders():
    """the same two sides, the absent case written as a real folder with zero hotspot area (+ CSV)."""
    ref, tgt = two_one_sided()
    return ((ref[0] + [('lateral_short', 'G', 'L2', 'H', 'L2', {})], tc.PAIRS),
            (tgt[0] + [('lateral_short', 'A', 'L1', 'C', 'L1', {})], tc.PAIRS))


def numbers(doc, role):
    side = doc['sides'][role]
    return side['lambda']['line'], side['lambda']['pixel'], side['vi_line'], side['vi_pixel']


def assert_same_numbers(doc_a, doc_b, roles=('reference', 'target')):
    for role in roles:
        for x, y in zip(numbers(doc_a, role), numbers(doc_b, role)):
            assert x is not None and y is not None and tc.close(x, y), (role, numbers(doc_a, role), numbers(doc_b, role))
    for key in ('delta_vi_line', 'delta_vi_pixel'):
        assert tc.close(doc_a['delta']['indicators'][key], doc_b['delta']['indicators'][key])


def notes_of(side):
    return {note for row in side['layers'] for size in row['by_size'] for note in size['reasons']}


# ---- a: one-sided cases count 0 -----------------------------------------------------------------------------------

def test_one_sided_case_counts_zero(tmp_path, monkeypatch):
    ref, tgt = two_one_sided()
    code, out, doc = run_pair_in(tmp_path, monkeypatch, 'absent', ref, tgt)
    assert code == 0, (doc['sides']['reference']['reasons'], doc['sides']['target']['reasons'])
    assert doc['delta']['check']['status'] == 'passed'
    for role in ('reference', 'target'):
        side = doc['sides'][role]
        assert side['vi_line'] is not None and side['vi_pixel'] is not None
        assert side['conservation_check']['status'] == 'passed'
        assert NOTE in notes_of(side) and NOTE not in side['reasons']
    # (i) each side expecting only its own cases gives the same numbers
    code_own, _out, own = run_pair_in(tmp_path, monkeypatch, 'own', ref, tgt, inventory='own')
    assert_same_numbers(doc, own)
    # (ii) a real zero-area folder on the absent side gives the same lambda and vi
    zero_ref, zero_tgt = with_zero_folders()
    code_zero, _out, zero = run_pair_in(tmp_path, monkeypatch, 'zero', zero_ref, zero_tgt)
    assert code_zero == 0
    assert_same_numbers(doc, zero)
    rows = tc.cond_by_id(doc['delta']['conditions'])
    assert rows['AC@L1']['presence'] == 'gone' and rows['GH@L2']['presence'] == 'new'
    assert 'GH@L2' not in tc.cond_by_id(doc['sides']['reference']['conditions'])
    assert 'AC@L1' not in tc.cond_by_id(doc['sides']['target']['conditions'])


def test_absent_case_same_pid_observed_elsewhere(tmp_path, monkeypatch):
    # the reference observes pair AB through A-B, the target through B-A (the same pair, another case name)
    reverse = ('lateral_short', 'B', 'L1', 'A', 'L1', {1: {4, 5}, 2: {5}})
    ref = (pick('AB', 'DE'), tc.PAIRS)
    tgt = ([reverse] + pick('DE'), tc.PAIRS)
    code, out, doc = run_pair_in(tmp_path, monkeypatch, 'both', ref, tgt)
    assert code == 0, (doc['sides']['reference']['reasons'], doc['sides']['target']['reasons'])
    assert tc.cond_by_id(doc['delta']['conditions'])['AB@L1']['presence'] == 'both'
    _code, _out, own = run_pair_in(tmp_path, monkeypatch, 'own', ref, tgt, inventory='own')
    assert_same_numbers(doc, own)
    assert 'AB@L1' in tc.cond_by_id(doc['sides']['reference']['conditions'])
    assert 'AB@L1' in tc.cond_by_id(doc['sides']['target']['conditions'])


# ---- b: a true input gap stays null ------------------------------------------------------------------------------

def test_missing_png_stays_null(tmp_path, monkeypatch):
    ref, tgt = two_one_sided()
    # the reference lacks the 2 um images of every case it has; AC (absent on the target) shares the group
    code, out, doc = run_pair_in(tmp_path, monkeypatch, 'gap', ref, tgt, skip_sides={'ref': (2,)})
    assert code != 0
    side = doc['sides']['reference']
    assert side['vi_line'] is None and side['vi_pixel'] is None and 'group_not_available' in side['reasons']
    assert doc['delta']['indicators']['delta_vi_line'] is None
    # the target (no missing image) is unaffected by the reference gap
    assert doc['sides']['target']['vi_line'] is not None


def test_csv_only_missing_unchanged(tmp_path, monkeypatch):
    ref, tgt = two_one_sided()
    no_csv = {name_of(BASE['AB'])}
    code, out, doc = run_pair_in(tmp_path, monkeypatch, 'absent', ref, tgt, no_csv=no_csv)
    assert code == 0, (doc['sides']['reference']['reasons'], doc['sides']['target']['reasons'])
    _code, _out, own = run_pair_in(tmp_path, monkeypatch, 'own', ref, tgt, inventory='own', no_csv=no_csv)
    assert_same_numbers(doc, own)


# ---- c: the switch is off without a confirmed inventory and in the default _extract ----------------------------

def test_without_union_inventory_unchanged(tmp_path, monkeypatch):
    ref, tgt = two_one_sided()
    code, out, doc = run_pair_in(tmp_path, monkeypatch, 'unconfirmed', ref, tgt, inventory=None)
    for role in ('reference', 'target'):
        side = doc['sides'][role]
        assert 'expected_case_inventory_unprovided' in side['reasons'] and NOTE not in notes_of(side)
    # the default _extract (absent_zero False) keeps the old behaviour on a confirmed union inventory
    from simcompare import compare2
    original = compare2._extract
    monkeypatch.setattr(compare2, '_extract', lambda areas, absent_zero=False: original(areas))
    code, out, old = run_pair_in(tmp_path, monkeypatch, 'old', ref, tgt)
    assert code != 0
    assert old['sides']['reference']['vi_line'] is None and old['sides']['target']['vi_line'] is None
    assert 'group_not_available' in old['sides']['reference']['reasons']


# ---- d: mixed reasons stay null ----------------------------------------------------------------------------------

def test_mixed_reasons_stay_null(tmp_path, monkeypatch):
    stray = ('lateral_short', 'X', 'L1', 'Y', 'L1', {1: {0}})  # no pair row for X-Y: unmapped_pair
    ref = (pick('AB', 'DE') + [stray], tc.PAIRS)
    tgt = (pick('AB', 'DE', 'AC'), tc.PAIRS)
    code, out, doc = run_pair_in(tmp_path, monkeypatch, 'unmapped', ref, tgt)
    side = doc['sides']['reference']
    assert side['vi_line'] is None and side['vi_pixel'] is None and 'group_not_available' in side['reasons']


def test_absent_case_outside_the_pair_table_stays_null(tmp_path, monkeypatch):
    ref = (pick('AB', 'DE'), tc.PAIRS)
    tgt = (pick('AB', 'DE'), tc.PAIRS)
    code, out, doc = run_pair_in(tmp_path, monkeypatch, 'nopair', ref, tgt,
                                 extra_expected=['lateral_short-K-L1-M-L1'])
    for role in ('reference', 'target'):
        side = doc['sides'][role]
        assert side['vi_line'] is None and 'group_not_available' in side['reasons']


# ---- e: no absent case, bytes unchanged ----------------------------------------------------------------------------

def test_union_inventory_without_absent_byte_identical(tmp_path, monkeypatch):
    ref, tgt = tc.two_sides()
    code, out, doc = run_pair_in(tmp_path, monkeypatch, 'same', ref, tgt)
    assert code == 0
    hashes = {}
    for name in UNION_SHA:
        text = (out / name).read_bytes().decode('utf-8').replace(str(tmp_path / 'same'), 'TMP')
        got = hashlib.sha256(re.sub(r'[0-9a-f]{64}', 'H', text).encode('utf-8')).hexdigest()
        hashes[name] = got
    assert hashes == UNION_SHA


# ---- f: a whole layer absent on one side --------------------------------------------------------------------------

def test_whole_layer_absent_unavailable_group(tmp_path, monkeypatch):
    ref, tgt = two_one_sided()  # the reference has no L2 case at all: every L2 group is unavailable there
    code, out, doc = run_pair_in(tmp_path, monkeypatch, 'layer', ref, tgt)
    assert code == 0
    side = doc['sides']['reference']
    l2 = [row for row in side['layers'] if row['layer'] == 'L2']
    assert l2 and all(size['status'] == 'available' for row in l2 for size in row['by_size'])
    assert all(size['lambda_line'] == 0.0 and size['lambda_pixel'] == 0.0 for row in l2 for size in row['by_size'])
    assert side['vi_line'] is not None and side['vi_pixel'] is not None


# ---- g: a vertical absent case beside a not_applicable small size -----------------------------------------------

def test_vertical_absent_with_not_applicable(tmp_path, monkeypatch):
    vertical = ('vertical_short', 'X', 'L1', 'Y', 'L2', {1: set(), 2: {3, 4}, 3: {3, 4, 5}})
    pairs = tc.PAIRS + [('XY', 'X', 'Y', 'line')]
    ref = (pick('AB', 'DE', 'GH'), pairs)
    tgt = (pick('AB', 'DE', 'GH') + [vertical], pairs)  # the 1 um record of the vertical case is not_applicable
    code, out, doc = run_pair_in(tmp_path, monkeypatch, 'vertical', ref, tgt)
    assert code == 0, (doc['sides']['reference']['reasons'], doc['sides']['target']['reasons'])
    assert doc['delta']['check']['status'] == 'passed'
    _code, _out, own = run_pair_in(tmp_path, monkeypatch, 'own', ref, tgt, inventory='own')
    assert_same_numbers(doc, own)
    assert tc.cond_by_id(doc['delta']['conditions'])['XY@L2']['presence'] == 'new'


# ---- g2: the diameter filter ----------------------------------------------------------------------------------------

@pytest.mark.parametrize('listed', [[1], [3], [1, 3]])
def test_diameter_filter_with_absent(tmp_path, monkeypatch, listed):
    # Expected: an absent case contributes 0 to every listed size and is never registered as a condition, so a
    # diameter filter changes neither the null state nor the "gone"/"new" presence; values equal the own-list run.
    ref, tgt = two_one_sided()
    code, out, doc = run_pair_in(tmp_path, monkeypatch, 'absent', ref, tgt, diameters=listed)
    assert code == 0, (doc['sides']['reference']['reasons'], doc['sides']['target']['reasons'])
    _code, _out, own = run_pair_in(tmp_path, monkeypatch, 'own', ref, tgt, inventory='own', diameters=listed)
    assert_same_numbers(doc, own)
    assert doc['delta']['check']['status'] == 'passed'


# ---- h: CNO folders beside absent cases -----------------------------------------------------------------------------

def test_cno_mixed(tmp_path, monkeypatch):
    ref, tgt = two_one_sided()
    cno = {tc.CNO_NAME: tc.CNO_CSV}
    code, out, doc = run_pair_in(tmp_path, monkeypatch, 'absent', ref, tgt, ref_cno=cno, tgt_cno=cno)
    assert code == 0, (doc['sides']['reference']['reasons'], doc['sides']['target']['reasons'])
    _code, _out, own = run_pair_in(tmp_path, monkeypatch, 'own', ref, tgt, inventory='own', ref_cno=cno, tgt_cno=cno)
    assert_same_numbers(doc, own)
    assert doc['sides']['reference']['lambda']['cno'] is not None
