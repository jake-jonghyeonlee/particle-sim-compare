"""lmap/T2: the observe-only share sink of compute_areas / compare2._load_side.

Naming: test_bytes_* = byte/serialization identity with and without a sink; test_numeric_* = 1e-9 relative checks;
test_contract_* = what the sink is handed (shapes, order, call counts)."""
import dataclasses
import hashlib
import re
import json
import math

import numpy as np
import pytest

import test_compare2 as tc
import test_compare2_expected_zero as ez
from simcompare import compare2
from simcompare.linepixel import LINE, PIXEL, compute_areas
from test_linepixel_ledger_shares import ikey_of, make_case_nd
from test_linepixel import group


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)
        return 'ignored'


def valid_all(h, w):
    return [[1] * w for _ in range(h)]


def areas(cases, sink=None, **kw):
    return compute_areas(cases, condition_stats=True, share_sink=sink, **kw)


def call_for(rec, kind, pid, failure_type='lateral_short', diameter=5):
    found = [c for c in rec.calls if c['kind'] == kind and c['item_key'][:2] == (pid, failure_type)
             and c['group_key'].diameter_um == diameter]
    assert len(found) == 1
    return found[0]


# ---------- bytes identical ----------

def install_sink(monkeypatch, rec):
    original = compare2._load_side
    monkeypatch.setattr(compare2, '_load_side',
                        lambda cfg, role, top_buckets, share_sink=None: original(cfg, role, top_buckets, rec))


def digests(out, folder):
    result = {}
    for path in sorted(out.iterdir()):
        if path.name.startswith('compare2') and path.suffix in ('.json', '.csv'):  # execution.json has timestamps
            text = path.read_bytes().decode('utf-8').replace(str(folder), 'TMP')
            text = re.sub(r'[0-9a-f]{64}', 'H', text)
            result[path.name] = hashlib.sha256(text.encode('utf-8')).hexdigest()
    return result


@pytest.mark.parametrize('diameters', [None, [3]])
def test_bytes_compare2_outputs_equal_with_and_without_sink(tmp_path, monkeypatch, diameters):
    ref, tgt = tc.two_sides()
    kwargs = {} if diameters is None else {'diameters': diameters}
    base = tmp_path / 'base'
    base.mkdir()
    code, out = tc.run(base, ref, tgt, **kwargs)
    assert code == 0
    rec = Recorder()
    install_sink(monkeypatch, rec)
    with_sink = tmp_path / 'sink'
    with_sink.mkdir()
    code2, out2 = tc.run(with_sink, ref, tgt, **kwargs)
    assert code2 == 0
    assert rec.calls  # the sink was really used
    first, second = digests(out, base), digests(out2, with_sink)
    assert 'compare2.json' in first and len(first) >= 5
    assert first == second


def test_bytes_area_result_serialization_equal_with_and_without_sink():
    cases = [make_case_nd('c1', 'A', 'B', {0, 1, 2}, 'line', height=2, width=5, valid=valid_all(2, 5),
                          failure_type='lateral_short', layers=('M2', 'M2')),
             make_case_nd('c2', 'C', 'D', {2, 3, 7}, 'pixel', height=2, width=5, valid=valid_all(2, 5),
                          failure_type='lateral_short', layers=('M2', 'M2'))]
    plain, rec = areas(cases), Recorder()
    observed = areas(cases, rec)
    assert rec.calls
    dump = lambda result: json.dumps(result.to_dict(), sort_keys=True, default=str)
    assert dump(plain) == dump(observed)
    g1, g2 = group(plain), group(observed)
    assert json.dumps(g1.ledger_shares, sort_keys=True, default=str) == json.dumps(g2.ledger_shares, sort_keys=True, default=str)
    assert g1.condition_stats == g2.condition_stats


# ---------- numeric checks on counter-example fixtures ----------

def check_calls(rec):
    for c in rec.calls:
        geom, idx, share = c['geom_idx'], c['share_idx'], c['share']
        assert idx.dtype.kind == 'i' and geom.dtype.kind == 'i'
        assert share.dtype == np.float64 and len(share) == len(idx)
        assert np.all(np.diff(idx) > 0) and np.all(np.diff(geom) > 0)
        assert set(idx.tolist()) <= set(geom.tolist())


def test_numeric_share_sums_and_index_contract_on_random_like_fixture():
    h, w = 3, 11
    cases = [make_case_nd('a1', 'A', 'B', {0, 1, 2, 3, 12}, 'line', height=h, width=w, valid=valid_all(h, w),
                          failure_type='lateral_short', layers=('M2', 'M2')),
             make_case_nd('a2', 'C', 'D', {2, 3, 4, 20}, 'line', height=h, width=w, valid=valid_all(h, w),
                          failure_type='lateral_short', layers=('M2', 'M2')),
             make_case_nd('a3', 'E', 'F', {3, 4, 5, 6, 30}, 'pixel', height=h, width=w, valid=valid_all(h, w),
                          failure_type='lateral_short', layers=('M2', 'M2'))]
    rec = Recorder()
    result = areas(cases, rec)
    check_calls(rec)
    ledger = group(result).ledger_shares
    five = [c for c in rec.calls if c['group_key'].diameter_um == 5]
    assert len(five) == sum(len(ledger[k]['items']) for k in (LINE, PIXEL))
    for c in five:
        item = next(i for i in ledger[c['kind']]['items'] if ikey_of(i) == c['item_key'])
        assert math.fsum(c['share']) == pytest.approx(item['share_cells'], rel=1e-9)
        assert len(c['share_idx']) == item['own_cells']
        assert set(c['item']) == {'pair_id', 'failure_type', 'upper_layer', 'lower_layer', 'endpoints'}


def test_numeric_three_pairs_on_one_cell_share_one_third():
    h, w = 1, 6
    cases = [make_case_nd(f't{i}', a, b, {2, 3 + i}, 'line', height=h, width=w, valid=valid_all(h, w),
                          failure_type='lateral_short', layers=('M2', 'M2'))
             for i, (a, b) in enumerate([('A', 'B'), ('C', 'D'), ('E', 'F')])]
    rec = Recorder()
    areas(cases, rec)
    check_calls(rec)
    for i, pid in enumerate(['A-B', 'C-D', 'E-F']):
        c = call_for(rec, LINE, pid)
        assert c['share_idx'].tolist() == [2, 3 + i]
        assert c['share'].tolist() == pytest.approx([1 / 3, 1.0])


def test_numeric_two_items_of_one_pair_overlap_divides_by_item_count():
    h, w = 1, 6
    lateral = make_case_nd('l', 'A', 'B', {1, 2, 3}, 'line', height=h, width=w, valid=valid_all(h, w),
                           failure_type='lateral_short', layers=('M2', 'M2'))
    vertical = make_case_nd('v', 'A', 'B', {3, 4}, 'line', height=h, width=w, valid=valid_all(h, w),
                            failure_type='vertical_short', layers=('M2', 'M1'))
    rec = Recorder()
    areas([lateral, vertical], rec)
    check_calls(rec)
    lat = call_for(rec, LINE, 'A-B', 'lateral_short')
    ver = call_for(rec, LINE, 'A-B', 'vertical_short')
    assert lat['share_idx'].tolist() == [1, 2, 3] and lat['share'].tolist() == pytest.approx([1, 1, 0.5])
    assert ver['share_idx'].tolist() == [3, 4] and ver['share'].tolist() == pytest.approx([0.5, 1])


def test_numeric_line_covered_cell_is_geometry_but_not_share_of_pixel_item():
    h, w = 1, 6
    line = make_case_nd('l', 'A', 'B', {2, 3}, 'line', height=h, width=w, valid=valid_all(h, w),
                        failure_type='lateral_short', layers=('M2', 'M2'))
    pixel = make_case_nd('p', 'C', 'D', {3, 4}, 'pixel', height=h, width=w, valid=valid_all(h, w),
                         failure_type='lateral_short', layers=('M2', 'M2'))
    rec = Recorder()
    areas([line, pixel], rec)
    check_calls(rec)
    c = call_for(rec, PIXEL, 'C-D')
    assert c['geom_idx'].tolist() == [3, 4]
    assert c['share_idx'].tolist() == [4]
    assert c['share'].tolist() == [1.0]


def test_numeric_non_square_frame_flat_index_is_row_major():
    h, w = 2, 5
    cells = {1 * w + 3, 0 * w + 4, 1 * w + 0}  # (1,3), (0,4), (1,0)
    case = make_case_nd('n', 'A', 'B', cells, 'line', height=h, width=w, valid=valid_all(h, w),
                        failure_type='lateral_short', layers=('M2', 'M2'))
    rec = Recorder()
    areas([case], rec)
    c = call_for(rec, LINE, 'A-B')
    assert c['frame'].shape_yx == (h, w)
    yx = sorted((int(i) // c['frame'].shape_yx[1], int(i) % c['frame'].shape_yx[1]) for i in c['geom_idx'])
    assert yx == [(0, 4), (1, 0), (1, 3)]


def test_numeric_invalid_cells_are_left_out_of_geometry_and_share():
    h, w = 1, 5
    valid = [[1, 1, 0, 1, 1]]
    case = make_case_nd('n', 'A', 'B', {1, 2, 3}, 'line', height=h, width=w, valid=valid,
                        failure_type='lateral_short', layers=('M2', 'M2'))
    rec = Recorder()
    areas([case], rec)
    c = call_for(rec, LINE, 'A-B')
    assert c['geom_idx'].tolist() == [1, 3] and c['share_idx'].tolist() == [1, 3]


# ---------- contract: groups, calls, errors ----------

def test_contract_density_layers_split_the_calls_by_group_key():
    h, w = 1, 6
    one = make_case_nd('a', 'A', 'B', {1, 2}, 'line', height=h, width=w, valid=valid_all(h, w),
                       failure_type='lateral_short', layers=('M2', 'M2'))
    two = make_case_nd('b', 'C', 'D', {1, 2}, 'line', height=h, width=w, valid=valid_all(h, w),
                       failure_type='lateral_short', layers=('M2', 'M2'))
    two = dataclasses.replace(two, analysis_layer='M3', density_process_layer='density-M3',
                              records={d: dataclasses.replace(r, analysis_layer='M3') for d, r in two.records.items()})
    rec = Recorder()
    areas([one, two], rec)
    d5 = [c for c in rec.calls if c['group_key'].diameter_um == 5]
    assert {c['group_key'].density_process_layer for c in d5} == {'density-M2', 'density-M3'}
    assert len(d5) == 2
    assert {c['item_key'][0] for c in d5 if c['group_key'].density_process_layer == 'density-M3'} == {'C-D'}


def test_contract_sink_is_called_for_diameters_outside_lambda_diameters(tmp_path, monkeypatch):
    ref, tgt = tc.two_sides()
    rec = Recorder()
    install_sink(monkeypatch, rec)
    code, _ = tc.run(tmp_path, ref, tgt, diameters=[3])
    assert code == 0
    diameters = {c['group_key'].diameter_um for c in rec.calls}
    assert diameters > {3}  # compute_areas does not know the diameter filter: every analysed diameter is observed


def test_contract_absent_zero_inventory_runs_and_sink_gets_no_call_for_the_absent_case(tmp_path, monkeypatch):
    ref, tgt = ez.two_one_sided()
    rec = Recorder()
    install_sink(monkeypatch, rec)
    code, out, doc = ez.run_pair_in(tmp_path, monkeypatch, 'absent', ref, tgt)
    assert code == 0 and rec.calls
    assert doc['sides']['target']['absent_expected_cases']['count'] == 1
    check_calls(rec)


def test_contract_sink_exception_propagates():
    case = make_case_nd('c', 'A', 'B', {1}, 'line', height=1, width=3, valid=valid_all(1, 3),
                        failure_type='lateral_short', layers=('M2', 'M2'))

    def boom(**kw):
        raise RuntimeError('sink failed')

    with pytest.raises(RuntimeError, match='sink failed'):
        areas([case], boom)


def test_contract_share_sink_without_condition_stats_is_a_value_error():
    case = make_case_nd('c', 'A', 'B', {1}, 'line', height=1, width=3, valid=valid_all(1, 3),
                        failure_type='lateral_short', layers=('M2', 'M2'))
    with pytest.raises(ValueError):
        compute_areas([case], condition_stats=False, share_sink=Recorder())
