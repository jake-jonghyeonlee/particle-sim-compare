"""compare2/T21: round 4 (`candidates-change`) takes CNO cases in; DL2 export leaves them out; roi_correspondence_v1 allows one CNO endpoint.

World = test_change_candidates' two-product world plus a CNO folder (layer GCNT) in each hotspot root. The target CNO shape
differs from the reference one (the layout shift removed), so the CNO layer has a change of its own.
"""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image  # noqa: F401  (kept for fixtures that write PNGs)

import stage46_generators as gen
import test_cno_short_guard as tcs
import test_change_candidates as tcc
from simcompare import change_map, correspondence
from simcompare.adapters.real_hotspot import load_real_manifest
from simcompare.cli import main
from simcompare.numeric_schema import NumericContractError

CNO_KEY = f'lateral_open|CNO|{tcc.CNO_LAYER}|{tcc.CNO_LAYER}'
L1_KEYS = {'lateral_short|A|L1|B|L1|L1', 'lateral_short|A|L1|C|L1|L1'}
DIRECTIONS = ('reference_to_target', 'target_to_reference')


def make_world(base, *, reference_cno, target_cno, include_cno=True, cno_name=('CNO', tcc.CNO_LAYER)):
    datas, assignments = {}, {}
    for side, cno in (('reference', reference_cno), ('target', target_cno)):
        datas[side], assignments[side] = tcc.build_product(base, side, cno=cno, include_cno=include_cno and cno is not None,
                                                           cno_name=cno_name)
    config = tcc.write_config(base, datas, assignments, map_spec={'diameter_um': 5})
    return SimpleNamespace(base=base, config=config, dl1=base/'dl1', datas=datas, assignments=assignments)


def run(world, out, *extra, **kw):
    return main(tcc.cli_args(world.config, out, world.dl1, **kw) + list(extra))


@pytest.fixture(scope='module')
def on(tmp_path_factory):
    base = tmp_path_factory.mktemp('cno_on')
    world = make_world(base, reference_cno='apart', target_cno='apart')
    out = base/'out'
    code = run(world, out, '--unit-fold')
    return SimpleNamespace(world=world, out=out, code=code, payload=tcc.load(out))


def cno_queries(payload):
    return {d: [q for q in tcc.queries(payload, d) if q['layers'] == [tcc.CNO_LAYER]] for d in DIRECTIONS}


# --- (a) end to end -----------------------------------------------------------------------------------------------------

def test_import_holds_one_confirmed_cno_case_per_product(on):
    for side in ('reference', 'target'):
        manifest = load_real_manifest(on.world.datas[side])
        (cno,) = manifest['cno_cases']
        assert cno['geometry_status'] == 'confirmed' and cno['endpoints'] == [
            {'electrode_id': 'CNO', 'actual_layer': tcc.CNO_LAYER, 'role': None}]


def test_cno_layer_has_roi_with_four_part_case_key_and_dl2_marker(on):
    payload = on.payload
    assert on.code in (0, 3), payload['warnings']
    found = cno_queries(payload)
    for direction in DIRECTIONS:
        assert found[direction], direction
        for query in found[direction]:
            assert [c['case_key'] for c in query['contributing_cases']] == [CNO_KEY]
            (case,) = query['contributing_cases']
            assert case['dl2_export'] == 'not_applicable_cno' and case['present_in'] == 'both' and case['residual_um2'] > 0
            assert query['status'] == 'blocked' and query['reasons'] == ['cno_only_query_no_dl2']
            assert query['roi_ids'] == [] and query['n_rois'] == query['n_rois_ready'] == 0
            assert query['score_layer'] == tcc.CNO_LAYER


def test_non_cno_queries_are_unchanged_by_the_cno_layer(on):
    for direction in DIRECTIONS:
        (query,) = [q for q in tcc.queries(on.payload, direction) if q['layers'] == ['L1']]
        assert {c['case_key'] for c in query['contributing_cases']} == L1_KEYS
        assert all('dl2_export' not in c for c in query['contributing_cases'])
        assert query['status'] == 'ready' and query['n_rois'] == query['n_rois_ready'] == 2


def test_dl2_exports_hold_no_cno_row_and_index_coverage_is_complete(on):
    for side in ('reference', 'target'):
        for folder in (f'{side}_export', f'{side}_index_export'):
            rows = tcc.load(on.out/folder, 'review_rois.json')['rois']
            assert rows and not any('CNO' in str(row.get('case_id')) for row in rows), folder
    coverage = on.payload['funnel']['index_coverage']
    assert coverage['reference']['complete'] and coverage['target']['complete']
    assert coverage['reference']['electrodes_covered'] == coverage['reference']['electrodes_total'] == 3


def test_direct_pairs_of_cno_have_one_endpoint_and_load(on):
    document = correspondence.load_correspondence(on.out/'direct/roi_correspondence.json')    # the loader accepts the file
    cno_pairs = [p for p in document['pairs'] if p['a']['analysis_layer'] == tcc.CNO_LAYER]
    assert cno_pairs and all(p['status'] == 'ready' and p['reasons'] == [] for p in cno_pairs)
    for pair in cno_pairs:
        for key in ('a', 'b'):
            assert pair[key]['failure_type'] == 'lateral_open'
            assert pair[key]['endpoints'] == [{'electrode_id': 'CNO', 'role': None, 'actual_layer': tcc.CNO_LAYER}]
    short = [p for p in document['pairs'] if p['a']['analysis_layer'] == 'L1']
    assert short and all(len(p[k]['endpoints']) == 2 for p in short for k in ('a', 'b'))
    sidecar = tcc.load(on.out, 'direct/direct_pairs_sidecar.json')['pairs']
    assert {sidecar[p['pair_id']]['status'] for p in cno_pairs} == {'ready'}


def test_confirm_pairs_skips_cno_queries(on, tmp_path, capsys):
    models = {side: SimpleNamespace(spec=SimpleNamespace(shape=tcc.SHAPE, product_id=tcc.PRODUCTS[side]))
              for side in ('reference', 'target')}
    matches = gen.fake_matches(on.out, models, tmp_path, seed=3)
    capsys.readouterr()
    code = main(['confirm-pairs', '--list', '--config', str(on.world.config), '--candidates', str(on.out),
                 *[a for d, p in matches.items() for a in ('--match', f'{d}={p}')]])
    assert code == 0
    listing = json.loads(capsys.readouterr().out)
    ready_ids = {r for d in on.payload['directions'] for q in d['queries'] if q['status'] == 'ready' for r in q['roi_ids']}
    assert ready_ids and {c['query_roi_id'] for c in listing['candidates']} == ready_ids
    assert listing['unavailable_queries'] == []
    cno_ids = {q['query_id'] for found in cno_queries(on.payload).values() for q in found}
    assert cno_ids and all(q['roi_ids'] == [] for found in cno_queries(on.payload).values() for q in found)


def test_same_cno_input_twice_gives_identical_bytes(on, tmp_path):
    out = tmp_path/'again'
    assert run(on.world, out, '--unit-fold') == on.code
    assert tcc.digests(out) == tcc.digests(on.out)


def test_cno_with_another_electrode_name_and_a_lowercase_layer_runs_end_to_end(tmp_path):
    """T21 review B1: the CNO electrode_id is the folder's middle part (CNO1 here) and the layer keeps its case."""
    world = make_world(tmp_path, reference_cno='apart', target_cno='apart', cno_name=('CNO1', 'gcnt'))
    out = tmp_path/'out'
    assert run(world, out) in (0, 3)
    payload = tcc.load(out)
    key = 'lateral_open|CNO1|gcnt|gcnt'
    keyed = [q for d in DIRECTIONS for q in tcc.queries(payload, d) if key in {c['case_key'] for c in q['contributing_cases']}]
    assert len(keyed) == 2 and all(q['reasons'] == ['cno_only_query_no_dl2'] for q in keyed)
    document_ = correspondence.load_correspondence(out/'direct/roi_correspondence.json')
    pairs = [p for p in document_['pairs'] if p['a']['analysis_layer'] == 'gcnt']
    assert pairs and all(p['status'] == 'ready' and p['a']['endpoints'][0]['electrode_id'] == 'CNO1' for p in pairs)
    assert [w['code'] for w in payload['warnings']] == ['top_k_not_filled'] * 2


def test_cno_only_blocked_queries_keep_the_run_complete(tmp_path):
    """T21 review ADV5: with a filled top_k the CNO-only blocked query alone does not make the run partial."""
    world = make_world(tmp_path, reference_cno='apart', target_cno='apart')
    out = tmp_path/'out'
    assert run(world, out, top_k=2) == 0
    payload = tcc.load(out)
    assert payload['status'] == 'available' and payload['warnings'] == []
    assert any(q['reasons'] == ['cno_only_query_no_dl2'] for d in DIRECTIONS for q in tcc.queries(payload, d))
    assert all(q['status'] == 'ready' or q['reasons'] == ['cno_only_query_no_dl2']
               for d in DIRECTIONS for q in tcc.queries(payload, d))


def test_a_direction_without_any_dl2_request_still_runs(tmp_path):
    """T21 review ADV5: top_k 1 keeps only the CNO query per direction, so no DL2 request is made at all."""
    world = make_world(tmp_path, reference_cno='apart', target_cno='apart')
    out = tmp_path/'out'
    assert run(world, out, top_k=1) == 0
    payload = tcc.load(out)
    for direction in DIRECTIONS:
        (query,) = tcc.queries(payload, direction)
        assert query['layers'] == [tcc.CNO_LAYER] and query['reasons'] == ['cno_only_query_no_dl2'] and query['roi_ids'] == []
    assert payload['status'] == 'available'


def test_alignment_html_shows_a_cno_case_row(on):
    """T21 review ADV4: the 4-part key is split and the cno_case flag has a label."""
    from simcompare import alignment_report
    assert alignment_report.split_case_key(CNO_KEY) == ('lateral_open', 'CNO', tcc.CNO_LAYER)
    assert alignment_report.split_case_key('lateral_open|CNO1|a|b') == ('lateral_open', 'CNO1 (a)', 'b')
    assert alignment_report.split_case_key('x|y|z') == (None, None, None)
    row = alignment_report._case_row({'case_key': CNO_KEY, 'change_kind': 'appeared', 'residual_um2': 1.0, 'flags': ['cno_case'],
                                      'present_in': 'target'})
    assert 'CNO 항목' in row and 'lateral_open' in row


# --- merge-scope all: a query that mixes a CNO case with short cases ----------------------------------------------------

def test_mixed_query_requests_only_the_short_cases(tmp_path):
    world = make_world(tmp_path, reference_cno='at_change', target_cno='at_change')
    out = tmp_path/'out'
    assert run(world, out, '--merge-scope', 'all') in (0, 3)
    payload = tcc.load(out)
    mixed = [q for d in DIRECTIONS for q in tcc.queries(payload, d)
             if CNO_KEY in {c['case_key'] for c in q['contributing_cases']}
             and L1_KEYS & {c['case_key'] for c in q['contributing_cases']}]
    assert mixed, [q['contributing_cases'] for d in DIRECTIONS for q in tcc.queries(payload, d)]
    for query in mixed:
        assert query['status'] == 'ready' and 'cno_only_query_no_dl2' not in query['reasons']
        assert query['n_rois'] == query['n_rois_ready'] == len(query['roi_ids']) == 2
        marked = {c['case_key']: c.get('dl2_export') for c in query['contributing_cases']}
        assert marked[CNO_KEY] == 'not_applicable_cno' and all(marked[k] is None for k in L1_KEYS & set(marked))


# --- (b) CNO folders on disk, import without --include-cno: no CNO anywhere -------------------------------------------------

def test_import_without_the_flag_leaves_no_cno_trace_in_round_four(tmp_path):
    world = make_world(tmp_path, reference_cno='apart', target_cno='apart', include_cno=False)
    out = tmp_path/'out'
    assert run(world, out) in (0, 3)
    payload = tcc.load(out)
    text = (out/'candidate_queries_v1.json').read_text(encoding='utf-8')
    assert 'dl2_export' not in text and 'cno_case' not in text and CNO_KEY not in text
    assert all(q['layers'] == ['L1'] for d in DIRECTIONS for q in tcc.queries(payload, d))
    assert 'cno_case_included' not in payload['change_map']['pairing']


# --- (d) one-sided CNO ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('only', ['reference', 'target'])
def test_one_sided_cno_gets_cno_case_flag_not_electrode_flags(tmp_path, only):
    world = make_world(tmp_path, reference_cno='apart' if only == 'reference' else None,
                       target_cno='apart' if only == 'target' else None)
    out = tmp_path/'out'
    assert run(world, out) in (0, 3)
    payload = tcc.load(out)
    keyed = [(q, c) for d in DIRECTIONS for q in tcc.queries(payload, d) for c in q['contributing_cases']
             if c['case_key'] == CNO_KEY]
    assert keyed
    for query, case in keyed:
        assert case['present_in'] == only and 'cno_case' in case['flags'] and 'one_sided_case' in case['flags']
        assert not {'new_electrode', 'removed_electrode'} & set(case['flags'])
        assert case['dl2_export'] == 'not_applicable_cno'
    stats = payload['change_map']['pairing']
    assert stats['cno_case_included'] == 1 and stats[f'one_sided_included_{only}'] >= 1
    assert stats['new_electrode_included'] == stats['removed_electrode_included'] == 0
    direct = correspondence.load_correspondence(out/'direct/roi_correspondence.json')
    one_sided = [p for p in direct['pairs'] if p['a']['analysis_layer'] == tcc.CNO_LAYER]
    assert one_sided and all(p['a'].get('endpoints') is None and p['b'].get('endpoints') is None for p in one_sided)
    marker = {'reference': 'a', 'target': 'b'}[only]       # T22: only the side holding the CNO case carries lateral_open
    other = 'b' if marker == 'a' else 'a'
    assert all(p[marker].get('failure_type') == 'lateral_open' and p[other].get('failure_type') is None for p in one_sided)


def test_pair_cases_includes_cno_and_still_excludes_other_open_cases(tmp_path):
    (_, on_imp) = tcs.build(tmp_path, 'on', include_cno=True)
    dataset = tcs.dataset_of(on_imp)
    result = change_map.pair_cases(dataset, dataset, diameter_um=1.0)
    keys = {p.case_key for p in result['pairs']}
    assert 'lateral_open|CNO|GCNT|GCNT' in keys
    assert not any(e['reason'] == 'map_requires_short_only' and 'CNO' in e['case_key'] for e in result['cases_excluded'])


# --- (c) roi_correspondence_v1: the one-endpoint exception is conditional ---------------------------------------------------

def side(**override):
    base = {'product_id': 'P', 'design_version': 'D', 'frame_id': 'F', 'center_xy_um': [1.0, 2.0], 'analysis_layer': 'L',
            'failure_type': 'lateral_short',
            'endpoints': [{'electrode_id': 'A', 'role': None, 'actual_layer': 'L'},
                          {'electrode_id': 'B', 'role': None, 'actual_layer': 'L'}]}
    base.update(override)
    return base


def cno_side(**override):
    return side(failure_type='lateral_open', endpoints=[{'electrode_id': 'CNO', 'role': None, 'actual_layer': 'L'}], **override)


def document(a, b):
    return {'schema_version': 'roi_correspondence_v1', 'producer': {'kind': 'template_match', 'version': '1'},
            'pairs': [{'pair_id': 'p1', 'a': a, 'b': b, 'source': {'kind': 'template_match', 'method': 'm', 'version': '1'},
                       'status': 'ready', 'reasons': []}]}


def code_of(a, b):
    with pytest.raises(NumericContractError) as info:
        correspondence.validate_correspondence(document(a, b))
    return info.value.code


def test_one_cno_endpoint_with_lateral_open_is_accepted():
    payload = correspondence.validate_correspondence(document(cno_side(), cno_side()))
    assert payload['pairs'][0]['a']['endpoints'] == [{'electrode_id': 'CNO', 'role': None, 'actual_layer': 'L'}]
    assert payload['pairs'][0]['a']['failure_type'] == 'lateral_open'


def test_one_cno_endpoint_is_refused_for_every_other_failure_type():
    for failure_type in ('lateral_short', 'vertical_short'):
        one = [{'electrode_id': 'CNO', 'role': None, 'actual_layer': 'L'}]
        assert code_of(side(failure_type=failure_type, endpoints=one), side()) == 'invalid_correspondence_endpoints'


def test_one_endpoint_of_lateral_open_is_accepted_whatever_the_electrode_name():
    for name in ('CNO1', 'cno', 'A'):
        one = [{'electrode_id': name, 'role': None, 'actual_layer': 'l'}]
        payload = correspondence.validate_correspondence(document(side(failure_type='lateral_open', endpoints=one),
                                                                  side(failure_type='lateral_open', endpoints=one)))
        assert payload['pairs'][0]['a']['endpoints'][0]['electrode_id'] == name


def test_one_endpoint_of_another_failure_type_is_refused():
    one = [{'electrode_id': 'A', 'role': None, 'actual_layer': 'L'}]
    assert code_of(side(endpoints=one), side()) == 'invalid_correspondence_endpoints'
    assert code_of(side(failure_type='vertical_short', endpoints=one), side()) == 'invalid_correspondence_endpoints'


def test_two_electrodes_rules_are_unchanged():
    same = [{'electrode_id': 'A', 'role': None, 'actual_layer': 'L'}] * 2
    assert code_of(side(endpoints=same), side()) == 'invalid_correspondence_endpoints'
    three = side()['endpoints'] + [{'electrode_id': 'C', 'role': None, 'actual_layer': 'L'}]
    assert code_of(side(endpoints=three), side()) == 'invalid_correspondence_endpoints'
    two = side()['endpoints']
    assert code_of(side(failure_type='lateral_open', endpoints=two), side()) == 'invalid_correspondence_failure_type'
    marked = correspondence.validate_correspondence(document(side(failure_type='lateral_open', endpoints=None), side()))    # T22: the one-sided CNO marker
    assert marked['pairs'][0]['a']['failure_type'] == 'lateral_open' and marked['pairs'][0]['a']['endpoints'] is None
    assert code_of(side(endpoints=[]), side()) == 'invalid_correspondence_endpoints'
    two_cno = [{'electrode_id': 'CNO', 'role': None, 'actual_layer': 'L'}] * 2
    assert code_of(side(failure_type='lateral_open', endpoints=two_cno), side()) == 'invalid_correspondence_failure_type'


def test_cno_endpoint_with_extra_keys_is_refused():
    bad = [{'electrode_id': 'CNO', 'role': None, 'actual_layer': 'L', 'extra': 1}]
    assert code_of(side(failure_type='lateral_open', endpoints=bad), side()) == 'invalid_correspondence_endpoints'


def test_bind_pairs_accepts_a_cno_only_layer_and_blocks_an_absent_one(tmp_path):
    """The layer set of bind_pairs holds the CNO layer; a one-endpoint side binds, and an absent layer blocks only its pair."""
    (_, imported) = tcs.build(tmp_path, 'on', include_cno=True)
    dataset = tcs.dataset_of(imported)
    frame_id, frame = next(iter(dataset.frames.items()))
    x0, y0, x1, y1 = frame.bbox_um
    centre = [(x0+x1)/2, (y0+y1)/2]
    ends = [{'electrode_id': 'CNO', 'role': None, 'actual_layer': 'GCNT'}]
    good = dict(cno_side(), product_id='P', design_version='D', frame_id=frame_id, center_xy_um=centre,
                analysis_layer='GCNT', endpoints=ends)
    absent = dict(good, analysis_layer='NOPE')
    payload = document(good, good)
    payload['pairs'].append({**copy.deepcopy(payload['pairs'][0]), 'pair_id': 'p2', 'a': absent, 'b': absent})
    bound = {p.pair_id: p for p in correspondence.bind_pairs(payload, dataset, dataset)}
    assert bound['p1'].status == 'ready' and bound['p1'].reasons == ()
    assert [e.electrode_id for e in bound['p1'].a.endpoints] == ['CNO']
    assert bound['p2'].status == 'blocked' and 'analysis_layer_not_in_source_a' in bound['p2'].reasons


def test_a_normal_two_electrode_file_validates_as_before():
    payload = correspondence.validate_correspondence(document(side(), side()))
    assert copy.deepcopy(payload['pairs'][0]['a']['endpoints']) == side()['endpoints']


# --- (e) T20 review adv1: a broken cno_cases value is one clear error ---------------------------------------------------------

@pytest.mark.parametrize('broken', [[1], 'text', {'a': 1}])
def test_load_real_manifest_checks_the_cno_cases_type_first(tmp_path, broken):
    (_, imported) = tcs.build(tmp_path, 'on', include_cno=True)
    path = imported/'manifest.json'
    manifest = json.loads(path.read_text(encoding='utf-8'))
    manifest['cno_cases'] = broken
    payload = {k: v for k, v in manifest.items() if k not in ('source_payload_hash', 'manifest_hash_recipe')}
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    manifest['source_payload_hash'] = hashlib.sha256(canonical.encode('utf-8')).hexdigest()     # reseal: the type check, not the hash, must answer
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    with pytest.raises(NumericContractError) as info:
        load_real_manifest(imported)
    assert info.value.code == 'invalid_real_cno_cases'
