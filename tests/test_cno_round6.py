"""compare2/T22: round 6 (`compare-windows`) measures a CNO pair on its CNO case mask only, never on a short group.

World = the T21 end-to-end world (two products, CNO folder in each hotspot root); candidates-change writes the direct
correspondence (CNO pairs have one lateral_open endpoint), compare-windows reads it. The hard case is a CNO layer with the
same name as the short layer (L1): the group lookup by layer would find the short group there.
"""
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import test_change_candidates as tcc
import test_cno_round4 as r4
from simcompare import correspondence, local_delta, window_cases
from simcompare.cli import main

SIZE = 6.0      # the planted CNO shapes lie 1.5-7 um from the frame edge: a wider window would be clipped
DIAMETER = 5.0
ONE_LAYER = [{'actual_layer': 'L1', 'stack_order': 1, 'density_process_layer': 'P1'}]


def make_world(base, *, layer, reference_cno='apart', target_cno='apart', include_cno=True):
    """layer = the CNO layer name; 'L1' is the short layer's own name."""
    datas, assignments = {}, {}
    stack = ONE_LAYER if layer == 'L1' else None
    for side, cno in (('reference', reference_cno), ('target', target_cno)):
        datas[side], assignments[side] = tcc.build_product(base, side, cno=cno, include_cno=include_cno and cno is not None,
                                                           cno_name=('CNO', layer), layer_stack=stack)
    config = tcc.write_config(base, datas, assignments, map_spec={'diameter_um': 5})
    return SimpleNamespace(base=base, config=config, dl1=base/'dl1', datas=datas, assignments=assignments, layer=layer)


def candidates(world, out):
    assert main(tcc.cli_args(world.config, out, world.dl1, top_k=5)) in (0, 3)
    return out/'direct/roi_correspondence.json'


def windows(world, corr, out, *extra):
    code = main(['compare-windows', '--config', str(world.config), '--correspondence', str(corr), '--window-sizes', str(int(SIZE)),
                 '--out', str(out), *extra])
    assert code in (0, 3), code
    return out


def csv_rows(out):
    with (Path(out)/'local_delta_rows.csv').open(newline='', encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


def split_pairs(corr):
    pairs = json.loads(Path(corr).read_text(encoding='utf-8'))['pairs']
    cno = [p for p in pairs if p['a'].get('endpoints') and len(p['a']['endpoints']) == 1]
    short = [p for p in pairs if p not in cno]
    return cno, short


def hand_area(mask, centre, size=SIZE):
    """Exact overlap area (um^2) of a frame mask (origin 50/50 um, 0.1 pitch) with the square window around centre."""
    pitch, x0, y0 = tcc.PITCH, centre[0]-size/2, centre[1]-size/2
    total = 0.0
    for r, c in zip(*np.nonzero(mask)):
        cx, cy = 50.0+c*pitch, 50.0+r*pitch
        ox = max(0.0, min(cx+pitch, x0+size)-max(cx, x0))
        oy = max(0.0, min(cy+pitch, y0+size)-max(cy, y0))
        total += ox*oy
    return total


def named_row(rows, pair_id, diameter=DIAMETER):
    (row,) = [r for r in rows if r['pair_id'] == pair_id and r['metric'] == 'named_pair' and float(r['diameter_um']) == diameter]
    return row


def metric_row(rows, pair_id, metric, kind='window', diameter=DIAMETER):
    (row,) = [r for r in rows if r['pair_id'] == pair_id and r['metric'] == metric and r['kind'] == kind
              and float(r['diameter_um']) == diameter]
    return row


@pytest.fixture(scope='module')
def same_layer(tmp_path_factory):
    base = tmp_path_factory.mktemp('c6_same')
    world = make_world(base, layer='L1')
    corr = candidates(world, base/'cand')
    return SimpleNamespace(world=world, corr=corr, out=windows(world, corr, base/'win'), base=base)


@pytest.fixture(scope='module')
def own_layer(tmp_path_factory):
    base = tmp_path_factory.mktemp('c6_own')
    world = make_world(base, layer=tcc.CNO_LAYER)
    corr = candidates(world, base/'cand')
    return SimpleNamespace(world=world, corr=corr, out=windows(world, corr, base/'win'), base=base)


# --- (a) the CNO layer has the short layer's name -------------------------------------------------------------------------

def test_cno_pair_of_a_short_layer_name_is_measured_on_the_cno_mask_only(same_layer):
    cno, short = split_pairs(same_layer.corr)
    assert cno and short and all(p['a']['analysis_layer'] == 'L1' for p in cno)
    rows = csv_rows(same_layer.out)
    for pair in cno:
        row = named_row(rows, pair['pair_id'])
        want_a = hand_area(tcc.cno_mask('reference', 'apart'), pair['a']['center_xy_um'])
        want_b = hand_area(tcc.cno_mask('target', 'apart'), pair['b']['center_xy_um'])
        assert want_a > 0 or want_b > 0
        assert row['a_status'] == row['b_status'] == 'available'
        assert float(row['a_value_um2']) == pytest.approx(want_a, abs=1e-6)
        assert float(row['b_value_um2']) == pytest.approx(want_b, abs=1e-6)
        union = metric_row(rows, pair['pair_id'], 'union')            # the window union IS the CNO case, not the short union
        assert float(union['a_value_um2']) == pytest.approx(want_a, abs=1e-6)
        assert float(union['b_value_um2']) == pytest.approx(want_b, abs=1e-6)
        assert float(union['delta_um2']) == pytest.approx(want_b-want_a, abs=1e-6)
        for piece in local_delta.FAILURE_PIECES:                      # no short piece leaks into a CNO pair
            cell = metric_row(rows, pair['pair_id'], piece)
            assert cell['a_status'] == cell['b_status'] == cell['delta_status'] == 'not_applicable'
            assert cell['a_value_um2'] == cell['b_value_um2'] == 'null'
        core = metric_row(rows, pair['pair_id'], 'union', kind='attributable_core')
        assert core['a_status'] == 'available' and core['b_status'] == 'available'


def test_short_pairs_next_to_a_same_name_cno_equal_the_world_without_cno(same_layer, tmp_path):
    """Acceptance 1(a): the CNO case is in no short group or union."""
    cno, short = split_pairs(same_layer.corr)
    bare = make_world(tmp_path, layer='L1', include_cno=False)
    filtered = tmp_path/'short_only.json'
    document = json.loads(same_layer.corr.read_text(encoding='utf-8'))
    document['pairs'] = short
    filtered.write_text(json.dumps(document), encoding='utf-8')
    off = windows(bare, filtered, tmp_path/'win_off')
    ids = {p['pair_id'] for p in short}
    on_rows = [r for r in csv_rows(same_layer.out) if r['pair_id'] in ids]
    assert on_rows and on_rows == csv_rows(off)


def test_cno_rows_carry_no_group_and_stay_out_of_the_short_fraction_explanation(same_layer):
    payload = json.loads((same_layer.out/'local_delta.json').read_text(encoding='utf-8'))
    cno, _ = split_pairs(same_layer.corr)
    ids = {p['pair_id'] for p in cno}
    rows = [r for r in payload['rows'] if r['pair_id'] in ids]
    assert rows and all(r['cno_case'] is True and r['group_a'] is None and r['group_b'] is None for r in rows)
    assert not any(r.get('cno_case') for r in payload['rows'] if r['pair_id'] not in ids)
    assert not any(reason.startswith('group_missing') for size in payload['explained_fraction']['by_window_size'].values()
                   for entry in size.values() for reason in entry['reasons'])


def test_an_overlapping_short_union_is_not_what_the_cno_pair_reports(same_layer):
    """The hard-case check made explicit: with the same layer name the group union differs from the CNO case, and the row follows the case."""
    cno, _ = split_pairs(same_layer.corr)
    rows = csv_rows(same_layer.out)
    pair = cno[0]
    # what the group path would report: the short-case union (hotspots of AB / AC) inside the same window
    short_union = hand_area(tcc.hotspot_masks('target')[tcc.CASE_AB] | tcc.hotspot_masks('target')[tcc.CASE_AC],
                            pair['b']['center_xy_um'])
    cno_area = hand_area(tcc.cno_mask('target', 'apart'), pair['b']['center_xy_um'])
    assert cno_area != pytest.approx(short_union, abs=1e-6)
    assert float(metric_row(rows, pair['pair_id'], 'union')['b_value_um2']) == pytest.approx(cno_area, abs=1e-6)


# --- (b) a CNO layer of its own ----------------------------------------------------------------------------------------------

def test_cno_pair_of_its_own_layer_is_available_with_a_named_pair_row(own_layer):
    cno, short = split_pairs(own_layer.corr)
    rows = csv_rows(own_layer.out)
    assert cno and all(p['a']['analysis_layer'] == tcc.CNO_LAYER for p in cno)
    for pair in cno:
        row = named_row(rows, pair['pair_id'])
        assert row['kind'] == 'window' and row['a_status'] == row['b_status'] == 'available'
        assert float(row['a_value_um2']) == pytest.approx(hand_area(tcc.cno_mask('reference', 'apart'), pair['a']['center_xy_um']), abs=1e-6)
        assert float(row['b_value_um2']) == pytest.approx(hand_area(tcc.cno_mask('target', 'apart'), pair['b']['center_xy_um']), abs=1e-6)
        assert float(metric_row(rows, pair['pair_id'], 'union')['delta_um2']) == pytest.approx(float(row['delta_um2']), abs=1e-9)
    ids = {p['pair_id'] for p in short}
    assert all(named_row(rows, i)['a_status'] for i in ids)          # short pairs still get their rows


def test_jitter_of_a_cno_pair_comes_from_the_cno_mask(same_layer, tmp_path):
    out = windows(same_layer.world, same_layer.corr, tmp_path/'jit', '--jitter-um', '0.5')
    payload = json.loads((out/'local_delta.json').read_text(encoding='utf-8'))
    cno, _ = split_pairs(same_layer.corr)
    ids = {p['pair_id'] for p in cno}
    rows = [r for r in payload['rows'] if r['pair_id'] in ids and r['diameter_um'] == DIAMETER]
    assert rows and all(r['jitter']['status'] in ('available', 'partial') and len(r['jitter']['delta_union_values']) == 4 for r in rows)
    pair = cno[0]
    row = next(r for r in rows if r['pair_id'] == pair['pair_id'])
    want = [hand_area(tcc.cno_mask('target', 'apart'), (pair['b']['center_xy_um'][0]+dx, pair['b']['center_xy_um'][1]+dy))
            - hand_area(tcc.cno_mask('reference', 'apart'), (pair['a']['center_xy_um'][0]+dx, pair['a']['center_xy_um'][1]+dy))
            for dx, dy in ((0.5, 0), (-0.5, 0), (0, 0.5), (0, -0.5))]
    got = row['jitter']['delta_union_values']
    assert all(g is None or g == pytest.approx(w, abs=1e-6) for g, w in zip(got, want))
    assert any(g is not None for g in got)


def test_ordering_is_deterministic(own_layer, tmp_path):
    again = windows(own_layer.world, own_layer.corr, tmp_path/'again')
    assert csv_rows(again) == csv_rows(own_layer.out)


# --- (c) one side only -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('only', ['reference', 'target'])
def test_one_sided_cno_pair_has_no_endpoints_and_is_blocked_by_the_existing_rule(tmp_path, only):
    """The layer exists on one side only: the pair is blocked (one_sided_layer_absent_*) and gets no row, as before T22."""
    world = make_world(tmp_path, layer=tcc.CNO_LAYER, reference_cno='apart' if only == 'reference' else None,
                       target_cno='apart' if only == 'target' else None)
    corr = candidates(world, tmp_path/'cand')
    pairs = [p for p in json.loads(corr.read_text(encoding='utf-8'))['pairs'] if p['a']['analysis_layer'] == tcc.CNO_LAYER]
    assert pairs and all(p['a'].get('endpoints') is None and p['status'] == 'blocked' for p in pairs)
    assert all(p['reasons'] == ['one_sided_layer_absent_' + ('b' if only == 'reference' else 'a')] for p in pairs)
    rows = csv_rows(windows(world, corr, tmp_path/'win'))
    assert not [r for r in rows if r['pair_id'] in {p['pair_id'] for p in pairs}]


@pytest.mark.parametrize('only', ['reference', 'target'])
def test_one_sided_cno_in_a_shared_layer_name_has_no_named_pair_row(tmp_path, only):
    """With the short layer's name the pair is ready but has no endpoints: no named_pair row (the group rule is unchanged)."""
    world = make_world(tmp_path, layer='L1', reference_cno='apart' if only == 'reference' else None,
                       target_cno='apart' if only == 'target' else None)
    corr = candidates(world, tmp_path/'cand')
    document = json.loads(corr.read_text(encoding='utf-8'))
    bare = [p for p in document['pairs'] if p['a'].get('endpoints') is None]
    rows = csv_rows(windows(world, corr, tmp_path/'win'))
    for pair in bare:
        assert not [r for r in rows if r['pair_id'] == pair['pair_id'] and r['metric'] == 'named_pair']


def test_a_pair_with_one_cno_side_and_one_short_side_names_the_missing_case(own_layer, tmp_path):
    cno, _ = split_pairs(own_layer.corr)
    document = json.loads(own_layer.corr.read_text(encoding='utf-8'))
    pair = json.loads(json.dumps(cno[0]))
    pair['pair_id'] = 'mixed'
    pair['b']['endpoints'] = [{'electrode_id': 'A', 'role': 'gate', 'actual_layer': tcc.CNO_LAYER},
                              {'electrode_id': 'B', 'role': 'data', 'actual_layer': tcc.CNO_LAYER}]
    pair['b']['failure_type'] = 'lateral_short'
    document['pairs'] = [pair]
    path = tmp_path/'mixed.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    rows = csv_rows(windows(own_layer.world, path, tmp_path/'win'))
    row = named_row(rows, 'mixed')
    assert row['b_status'] == 'unavailable' and 'named_pair_case_not_found_b' in row['delta_reasons']
    assert row['a_status'] == 'available'


# --- (d) the stage-2 mask artefact has no CNO group ------------------------------------------------------------------------

def test_a_stage2_artefact_does_not_change_cno_rows(own_layer, tmp_path):
    from simcompare.compare_masks import ARTEFACT_NAME
    assert main(['compare', '--config', str(own_layer.world.config), '--out', str(tmp_path/'cmp')]) in (0, 3)
    assert (tmp_path/'cmp'/ARTEFACT_NAME).is_file()
    meta = json.dumps([key for key in json.loads(__import__('zipfile').ZipFile(str(tmp_path/'cmp'/ARTEFACT_NAME)).read('meta.json'))['sides']['reference']['groups']])
    assert tcc.CNO_LAYER not in meta                                  # CNO is in no stored group (compute_areas leaves it out)
    reused = windows(own_layer.world, own_layer.corr, tmp_path/'cmp', )
    assert [r for r in csv_rows(reused) if r['metric'] == 'named_pair'] == [r for r in csv_rows(own_layer.out) if r['metric'] == 'named_pair']


# --- (e) models flow: window_cases -------------------------------------------------------------------------------------------

def test_window_cases_lists_the_cno_case_only_when_a_cno_exists(own_layer, same_layer, tmp_path):
    from simcompare.adapters.real_hotspot import load_real_manifest  # noqa: F401
    for world, corr, layer in ((own_layer.world, own_layer.corr, tcc.CNO_LAYER), (same_layer.world, same_layer.corr, 'L1')):
        bound = _bound(world, corr)
        reference, target = bound['reference'], bound['target']
        cno, _ = split_pairs(corr)
        payload = window_cases.build_window_cases(bound['pairs'], reference, target, window_um=SIZE, diameter_um=DIAMETER,
                                                  correspondence_sha256='x')
        by_pair = {p['pair_id']: p for p in payload['pairs']}
        for pair in cno:
            keys = {c['key'] for c in by_pair[pair['pair_id']]['cases']}
            assert 'lateral_open|CNO' in keys, (layer, keys)
    bare = make_world(tmp_path, layer='L1', include_cno=False)      # same layer name, CNO folders on disk but not imported
    short_only = tmp_path/'short_only.json'
    document = json.loads(same_layer.corr.read_text(encoding='utf-8'))
    document['pairs'] = split_pairs(same_layer.corr)[1]
    short_only.write_text(json.dumps(document), encoding='utf-8')
    bound = _bound(bare, short_only)
    payload = window_cases.build_window_cases(bound['pairs'], bound['reference'], bound['target'], window_um=SIZE,
                                              diameter_um=DIAMETER, correspondence_sha256='x')
    assert payload['pairs'] and not any(c['key'] == 'lateral_open|CNO' for p in payload['pairs'] for c in p['cases'])


def _bound(world, corr):
    from simcompare.adapters.real_hotspot import configured_dataset, load_real_manifest
    datasets = {}
    for side in ('reference', 'target'):
        data = world.datas[side]
        datasets[side] = configured_dataset(data, load_real_manifest(data), world.assignments[side])
    pairs = correspondence.bind_pairs(correspondence.load_correspondence(corr), datasets['reference'], datasets['target'])
    return {'reference': datasets['reference'], 'target': datasets['target'], 'pairs': pairs}


# --- (f) off: no CNO anywhere -> the round-6 output is the T21 code's output ----------------------------------------------------

def test_cno_folders_without_the_flag_leave_round_six_without_a_trace(tmp_path):
    world = make_world(tmp_path, layer=tcc.CNO_LAYER, include_cno=False)
    corr = candidates(world, tmp_path/'cand')
    cno, short = split_pairs(corr)
    assert not cno and short
    out = windows(world, corr, tmp_path/'win')
    text = (out/'local_delta.json').read_text(encoding='utf-8')
    assert 'cno_case' not in text and 'cno_case_has_no_failure_pieces' not in text
    rows = csv_rows(out)
    assert rows and all(r['delta_reasons'] != 'cno' for r in rows)
