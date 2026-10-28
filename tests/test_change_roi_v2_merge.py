"""change-roi-v2/T2: ``--merge-scope`` and ``--suppress-overlap`` of ``candidates-change``.

World: the test_change_candidates world with every case repeated on layer L2 at the same place (so two layers change at the
same window), plus one-sided cases that exist only in the target: AD (L1, big) and AD2 (L2, small) near CHANGE. L1 therefore
has the larger union change and is the representative layer of the merged window; L2 is the non-representative layer.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_change_candidates as tcc
from simcompare import change_map
from simcompare.cli import main

LAYER_STACK = [{'actual_layer': 'L1', 'stack_order': 1, 'density_process_layer': 'P1'},
               {'actual_layer': 'L2', 'stack_order': 2, 'density_process_layer': 'P2'}]
CASE_AD2 = 'lateral_short-A-L2-D-L2'


def two_layer_masks(original, ad_dx=40):
    def masks(side, extra_target_case=False):
        base = original(side, False)
        out = dict(base)
        for case, mask in base.items():
            out[case.replace('-L1', '-L2')] = mask
        if side == 'target':
            dy, dx = tcc.OFFSET[1], tcc.OFFSET[0]
            y, x = tcc.CHANGE[0].start+dy, tcc.CHANGE[1].start+dx+ad_dx
            out[tcc.CASE_AD] = tcc.rect(y, x, 20, 30)
            out[CASE_AD2] = tcc.rect(y+5, x+5, 10, 10)
        return out
    return masks


def build_world(tmp_path_factory, ad_dx=40):
    base = tmp_path_factory.mktemp('merge_world')
    mp = pytest.MonkeyPatch()
    mp.setattr(tcc, 'hotspot_masks', two_layer_masks(tcc.hotspot_masks, ad_dx))
    try:
        datas, assignments = {}, {}
        for side in ('reference', 'target'):
            datas[side], assignments[side] = tcc.build_product(base, side, layer_stack=LAYER_STACK)
        config = tcc.write_config(base, datas, assignments, map_spec={'diameter_um': 5})
    finally:
        mp.undo()
    return SimpleNamespace(base=base, config=config, dl1=base/'dl1')


@pytest.fixture(scope='module')
def two_layer_world(tmp_path_factory):
    return build_world(tmp_path_factory)


def run(world, out, *extra, **kw):
    args = tcc.cli_args(world.config, out, world.dl1, **kw)
    return main(args[:-2] + list(extra) + args[-2:]), Path(out)


def queries(payload, direction='reference_to_target'):
    return tcc.queries(payload, direction)


def make_run(world, tmp_path_factory, *extra, **kw):
    code, out = run(world, tmp_path_factory.mktemp('out')/'out', *extra, **kw)
    return SimpleNamespace(code=code, out=out, payload=tcc.load(out))


@pytest.fixture(scope='module')
def layer_run(two_layer_world, tmp_path_factory):
    return make_run(two_layer_world, tmp_path_factory)


@pytest.fixture(scope='module')
def all_run(two_layer_world, tmp_path_factory):
    return make_run(two_layer_world, tmp_path_factory, '--merge-scope', 'all')


def test_scope_layer_keeps_one_query_per_layer(layer_run):
    qs = queries(layer_run.payload)
    assert sorted(q['layers'] for q in qs) == [['L1'], ['L2']]
    assert all('layer' not in c for q in qs for c in q['contributing_cases'] + q['one_sided_cases'])
    assert layer_run.payload['policy']['merge'].startswith('per_layer_')
    assert 'merge_scope' not in layer_run.payload['change_args'] and 'suppress' not in layer_run.payload['policy']


def test_scope_all_merges_the_two_layers_into_one_query_with_both_layers_cases(all_run):
    payload = all_run.payload
    qs = queries(payload)
    assert len(qs) == 1
    q = qs[0]
    assert q['layers'] == ['L1', 'L2'] and q['score_layer'] == 'L1'
    cases = q['contributing_cases']
    assert {c['layer'] for c in cases} == {'L1', 'L2'}
    assert cases == sorted(cases, key=lambda c: (-c['residual_um2'], c['case_key']))
    assert payload['change_args']['merge_scope'] == 'all' and 'suppress_overlap' not in payload['change_args']
    assert payload['policy']['merge'] == 'all_layers_window_overlap_intersection_over_min_area_union_find_region_id_order'
    assert 'suppress' not in payload['policy']
    execution = tcc.load(all_run.out, 'execution.json')
    assert execution['args']['merge_scope'] == 'all' and 'suppress_overlap' not in execution['args']
    assert 'suppressed' not in [s['stage'] for s in payload['funnel']['directions'][0]['stages']]


def test_one_sided_case_on_the_non_representative_layer_reaches_manual_rows_and_direct_pairs(all_run):
    q = queries(all_run.payload)[0]
    one_sided = [c for c in q['contributing_cases'] if c['layer'] == 'L2' and c['present_in'] != 'both']
    assert one_sided, q['contributing_cases']
    assert [c for c in q['one_sided_cases'] if c['layer'] == 'L2'] == [
        {k: c[k] for k in ('case_key', 'present_in', 'layer_changed', 'layer')} for c in one_sided]
    rows = json.loads((all_run.out/'target_export'/'review_rois.json').read_text(encoding='utf-8'))['rois']
    assert any(r['analysis_layer'] == 'L2' and CASE_AD2 in json.dumps(r['source_identity']) for r in rows)
    sidecar = tcc.load(all_run.out, 'direct/direct_pairs_sidecar.json')
    correspondence = tcc.load(all_run.out, 'direct/roi_correspondence.json')
    pair_ids = {pid for pid, p in sidecar['pairs'].items() if p.get('layer') == 'L2' and p['one_sided_case']}
    assert pair_ids
    sides = [(p['a']['analysis_layer'], p['b']['analysis_layer']) for p in correspondence['pairs'] if p['pair_id'] in pair_ids]
    assert sides and all(s == ('L2', 'L2') for s in sides)
    assert all('layer' in p for p in sidecar['pairs'].values())


def test_scope_all_case_area_keys_are_on_every_layer_case_and_on_sidecar_pairs(all_run):
    q = queries(all_run.payload)[0]
    assert {c['layer'] for c in q['contributing_cases']} == {'L1', 'L2'}
    for c in q['contributing_cases']:
        assert c['area_reference_um2'] >= 0 and c['area_target_um2'] >= 0, c
        if c['present_in'] == 'reference':
            assert c['area_target_um2'] == 0 and c['area_reference_um2'] > 0
        if c['present_in'] == 'target':
            assert c['area_reference_um2'] == 0 and c['area_target_um2'] > 0
    sidecar = tcc.load(all_run.out, 'direct/direct_pairs_sidecar.json')
    l2 = [p for p in sidecar['pairs'].values() if p['layer'] == 'L2']
    assert l2 and all('area_reference_um2' in p and 'area_target_um2' in p for p in sidecar['pairs'].values())


def test_policy_attributes_key_only_for_scope_all(all_run, layer_run, two_layer_world, tmp_path_factory):
    expected = 'score_union_sum_residual_representative_layer_window_changed_case_count_all_member_layers'
    assert all_run.payload['policy']['attributes'] == expected
    assert 'attributes' not in layer_run.payload['policy']
    explicit = make_run(two_layer_world, tmp_path_factory, '--merge-scope', 'layer')
    assert 'attributes' not in explicit.payload['policy']


def test_chain_merge_rescores_only_the_representative_window(two_layer_world, tmp_path, monkeypatch):
    """Pinned limitation: members far from the representative window are not re-scored (only its origin, once per layer)."""
    seen = []
    original = change_map.case_contributions

    def spy(pairs, windows, **kw):
        seen.append(list(windows))
        return original(pairs, windows, **kw)

    monkeypatch.setattr(change_map, 'case_contributions', spy)
    code, out = run(two_layer_world, tmp_path/'out', '--merge-scope', 'all')
    q = queries(tcc.load(out))[0]
    assert len(q['merged_from']) > 2                       # a chain of many grid windows
    windows = seen[0]
    assert [w['layer'] for w in windows] == ['L1', 'L2']
    assert len({tuple(w['window_origin_px']) for w in windows}) == 1


def test_chain_merge_case_changed_only_in_a_far_member_window_is_not_a_contributing_case(all_run, tmp_path_factory, tmp_path):
    """Case-level view of the pinned limitation: electrode D cases (AD, AD2) change only far from the representative window."""
    near = {c['case_key'] for c in queries(all_run.payload)[0]['contributing_cases']}
    assert any('|D|' in key for key in near)               # near the representative window the D cases do contribute
    world = build_world(tmp_path_factory, ad_dx=130)
    code, out = run(world, tmp_path/'out', '--merge-scope', 'all')
    q = queries(tcc.load(out))[0]
    assert len(q['merged_from']) > 2
    keys = {c['case_key'] for c in q['contributing_cases']}
    assert keys and not any('|D|' in key for key in keys)  # the far windows' D cases are not re-scored


def _region(x, y, layer='L1'):
    return {'representative': SimpleNamespace(center_xy_um=(x, y), layer=layer), 'members': []}


@pytest.mark.parametrize('threshold, dx, kept', [
    (0.5, 5.0, 1),             # ratio == X: suppressed
    (0.5 + 1e-9, 5.0, 2),      # X just above the ratio: kept
    (0.5, 5.000001, 2),        # ratio just below X: kept
    (0.5, 4.0, 1),             # ratio above X: suppressed
])
def test_nms_boundary(threshold, dx, kept):
    items = [_region(0.0, 0.0, 'L1'), _region(dx, 0.0, 'L2')]      # ratio = (10-dx)*10/100, across layers
    assert len(change_map.suppress_overlap(items, 10.0, threshold)) == kept


def test_nms_keeps_rank_order_and_compares_against_kept_only():
    items = [_region(0.0, 0.0), _region(6.0, 0.0), _region(12.0, 0.0)]
    # 2nd overlaps 40 % with the 1st (kept at X=0.5); 3rd overlaps 40 % with the 2nd and 0 % with the 1st
    assert change_map.suppress_overlap(items, 10.0, 0.5) == items
    # at X=0.4 the 2nd is dropped; the 3rd does not touch the 1st (overlap 0) and is kept
    assert change_map.suppress_overlap(items, 10.0, 0.4) == [items[0], items[2]]
    assert change_map.suppress_overlap([], 10.0, 0.4) == []


def test_suppression_then_top_k_has_exact_funnel_counts(two_layer_world, tmp_path_factory):
    r = make_run(two_layer_world, tmp_path_factory, '--merge-scope', 'all', '--suppress-overlap', '0.5',
                 overlap=0.9, top_k=1)
    funnel = r.payload['funnel']
    stages = {s['stage']: s for s in funnel['directions'][0]['stages']}
    assert [s['stage'] for s in funnel['directions'][0]['stages']] == [
        'change_candidates', 'merged', 'suppressed', 'top_k', 'exported_ready']
    merged, suppressed, top_k = stages['merged'], stages['suppressed'], stages['top_k']
    assert merged['count'] > suppressed['count'] > top_k['count'] == 1
    assert suppressed['dropped'] == merged['count']-suppressed['count']
    assert suppressed['reasons'] == {'suppressed_overlap': suppressed['dropped']}
    assert top_k['dropped'] == suppressed['count']-1 and top_k['reasons'] == {'cut_by_top_k': top_k['dropped']}
    assert merged['dropped'] == stages['change_candidates']['count']-merged['count']      # `merged` is the pre-suppression count
    assert funnel['suppressed'] == suppressed['count'] and funnel['directions'][0]['suppressed'] == suppressed['count']
    csv_text = (r.out/'funnel.csv').read_text(encoding='utf-8-sig')
    assert f"reference_to_target,suppressed,{suppressed['count']},{suppressed['dropped']},suppressed_overlap:" in csv_text
    assert r.payload['policy']['suppress'] == 'greedy_score_order_window_overlap_ge_x_all_layers'
    assert r.payload['change_args']['suppress_overlap'] == 0.5
    assert tcc.load(r.out, 'execution.json')['args']['suppress_overlap'] == 0.5


def test_suppress_without_survivor_loss_records_empty_reasons(two_layer_world, tmp_path_factory):
    r = make_run(two_layer_world, tmp_path_factory, '--merge-scope', 'all', '--suppress-overlap', '1')
    stage = [s for s in r.payload['funnel']['directions'][0]['stages'] if s['stage'] == 'suppressed'][0]
    assert stage['dropped'] == 0 and stage['reasons'] == {}


def test_explicit_scope_layer_only_adds_the_args_key(layer_run, two_layer_world, tmp_path_factory):
    explicit = make_run(two_layer_world, tmp_path_factory, '--merge-scope', 'layer')
    a, b = dict(layer_run.payload), dict(explicit.payload)
    assert b['change_args'].pop('merge_scope') == 'layer'
    for payload in (a, b):
        payload.pop('alignment_report')
    assert a == b
    assert tcc.load(explicit.out, 'funnel.json') == tcc.load(layer_run.out, 'funnel.json')
    assert tcc.load(explicit.out, 'direct/direct_pairs_sidecar.json') == tcc.load(layer_run.out, 'direct/direct_pairs_sidecar.json')


@pytest.mark.parametrize('flag, value', [('--suppress-overlap', '0'), ('--suppress-overlap', '1.5'),
                                          ('--suppress-overlap', 'nan'), ('--suppress-overlap', '-0.2')])
def test_invalid_suppress_overlap_exits_2(two_layer_world, tmp_path, capsys, flag, value):
    code, out = run(two_layer_world, tmp_path/'out', flag, value)
    assert code == 2 and not out.exists()
    assert 'invalid_candidates_change_argument' in capsys.readouterr().err


def test_invalid_merge_scope_is_rejected_by_the_parser(two_layer_world, tmp_path, capsys):
    with pytest.raises(SystemExit) as raised:
        run(two_layer_world, tmp_path/'out', '--merge-scope', 'everything')
    assert raised.value.code == 2 and not (tmp_path/'out').exists()
