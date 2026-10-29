"""layer-align/T4b: ``candidates-change --apply-align local --local-align-um R [--refine-pool N]``, synthetic end to end.

World = the unit world of test_layer_align_cli (frame 1300 x 1300, 4 units of 1150 px, ΔU = 2, unit offsets C + 2·(ix, iy)) plus two features in the
CASE_AB hotspot masks:

* LP (local patch): inside the patch rectangle the target layout (CNT and Layer images) is moved a further LOCAL px relative to its unit offset.
  The hotspot LP moves with it and has 4 extra columns in the target (the one real change). Unit mode leaves a large false residual, local mode
  must find the injected move and leave only the real change.
* XB (boundary): a rectangle crossing the unit boundary x = 650. The two pieces sit at the offsets of their own units (exact field world), with a
  4x4 hole in the target (the one real change).
"""
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import numpy as np
import pytest

import test_change_candidates as tcc
import test_layer_align_cli as cli_t
from test_alignment_report import captions
from simcompare import change_candidates, change_map, layer_align, local_align
from simcompare.adapters.real_hotspot import import_hotspots
from simcompare.cli import main

PITCH = 0.1
U, C = cli_t.U, cli_t.C
LOCAL = (2, 1)                                   # (dx, dy) injected on top of the unit-(0, 0) offset
LP_REF = (200, 300, 20, 40)                      # frame (y, x, h, w)
XB_REF = (300, 630, 20, 40)
PATCH = (60, 340, 180, 480)                      # frame rows / cols of the moved layout (target)
BIG = dict(change_tol=0.0, window=5, top_k=12, layer_tol=0.5)
LOCAL_ARGS = ('--unit-align-um', '1.5', '--local-align-um', '1', '--apply-align', 'local')


NB_REF = (600, 600, 20, 15)                      # T4c adv:2 world only: a change 40 px left of the unit boundary x = 650 (window does not cross it)
NB = {'on': False}


def add_features(masks, side, extra):
    mask = masks[tcc.CASE_AB]
    if NB['on']:
        ny, nx, nh, nw = NB_REF
        if side == 'reference':
            mask[ny:ny + nh, nx:nx + nw] = True
        else:
            mask[ny + C[1]:ny + C[1] + nh, nx + C[0]:nx + C[0] + nw + 4] = True
    y, x, h, w = LP_REF
    by, bx, bh, bw = XB_REF
    if side == 'reference':
        mask[y:y + h, x:x + w] = True
        mask[by:by + bh, bx:bx + bw] = True
        return
    mask[y + C[1] + LOCAL[1]:y + C[1] + LOCAL[1] + h, x + C[0] + LOCAL[0]:x + C[0] + LOCAL[0] + w + 4] = True
    mask[by + C[1]:by + C[1] + bh, bx + C[0]:bx + C[0] + 20] = True            # left piece (unit (0, 0) offset)
    mask[by + C[1]:by + C[1] + bh, bx + 20 + C[0] + 2:bx + 20 + C[0] + 2 + 20] = True   # right piece (unit (0, 1) offset: +2 px)
    mask[by + C[1] + 8:by + C[1] + 12, bx + 20 + C[0] + 2 + 6:bx + 20 + C[0] + 2 + 10] = False   # the real change: a 4 x 4 hole in the right piece


def patched(image):
    """target Layer / CNT full image (frame + 500 px border): the layout inside PATCH moves LOCAL px further."""
    out = image.copy()
    y0, y1, x0, x1 = (v + 500 for v in PATCH)
    dx, dy = LOCAL
    out[y0:y1, x0:x1] = image[y0 - dy:y1 - dy, x0 - dx:x1 - dx]
    return out


def build_side(base, side, extra, patch=True):
    original_masks, original_image = cli_t.hotspot_masks, cli_t.full_image

    def masks(s, e):
        out = original_masks(s, e)
        add_features(out, s, e)
        return out

    def image(*args, **kw):
        out = original_image(*args, **kw)
        return patched(out) if (patch and side == 'target') else out
    cli_t.hotspot_masks, cli_t.full_image = masks, image
    try:
        return cli_t.build_side(base, side, extra)
    finally:
        cli_t.hotspot_masks, cli_t.full_image = original_masks, original_image


@pytest.fixture(scope='module')
def world(tmp_path_factory):
    base = tmp_path_factory.mktemp('local_world')
    datas, assignments = {}, {}
    for side in ('reference', 'target'):
        datas[side], assignments[side] = build_side(base, side, 2)
    config = tcc.write_config(base, datas, assignments, map_spec={'diameter_um': 5})
    return SimpleNamespace(base=base, config=config, dl1=base / 'dl1', tmp_path_factory=tmp_path_factory)


def make(world, *extra, **kw):
    kw = {**BIG, **kw}
    out = world.tmp_path_factory.mktemp('out') / 'out'
    args = tcc.cli_args(world.config, out, kw.pop('dl1', world.dl1), **kw)
    code = main(args[:-2] + list(extra) + args[-2:])
    return SimpleNamespace(code=code, out=out, payload=tcc.load(out), alignment=tcc.load(out, 'alignment.json'),
                           execution=tcc.load(out, 'execution.json'))


def local_doc(run):
    return tcc.load(run.out, 'roi_local_alignment.json')


def queries(run, direction='reference_to_target'):
    return tcc.queries(run.payload, direction)


def near(q, y, x, tol_px=40):
    return cli_t.near(q, y, x, tol_px)


def lp_queries(run):
    return [q for q in queries(run) if near(q, 210, 320, 45)]


def xb_queries(run):
    return [q for q in queries(run) if near(q, 310, 650, 35)]


@pytest.fixture(scope='module')
def unit_run(world):
    return make(world, '--unit-align-um', '1.5', '--apply-align', 'unit')


@pytest.fixture(scope='module')
def local_run(world):
    return make(world, *LOCAL_ARGS)


# --- (a) 국소 어긋남 ---------------------------------------------------------------------------------------------------

def test_a_unit_mode_leaves_a_false_residual_at_the_locally_moved_feature(unit_run):
    found = lp_queries(unit_run)
    assert found and max(q['score_um2'] for q in found) > 2.0         # 대부분이 거짓 잔차(진짜 변경은 0.8 µm²)


def test_a_local_mode_finds_the_injected_move_and_leaves_only_the_real_change(unit_run, local_run):
    found = lp_queries(local_run)
    assert found
    top = max(found, key=lambda q: q['score_um2'])
    want = [C[0] + LOCAL[0], C[1] + LOCAL[1]]
    assert top['offset_source'] == 'local' and top['offset_xy_px_applied'] == want
    assert top['score_um2'] == pytest.approx(4 * 20 * PITCH * PITCH)           # 진짜 변경 4 x 20 px 만
    assert top['score_um2'] < max(q['score_um2'] for q in lp_queries(unit_run)) / 2
    item = [i for i in local_doc(local_run)['rois'] if i['query_id'] == top['query_id']][0]
    assert item['status'] == 'ok' and item['applied_source'] == 'local' and item['applied_offset_xy_px'] == want
    assert item['start_offset_xy_px'] == [C[0], C[1]] and item['delta_from_start_xy_px'] == list(LOCAL)
    assert item['decided_by_layer'] is not None and item['layer_order'] and item['per_layer']
    assert top['offset_xy_px_applied'] == item['local_offset_xy_px']
    assert local_run.execution['args']['apply_align'] == 'local' and local_run.execution['args']['refine_pool'] == 120


def test_a_local_document_header_and_summary(local_run):
    doc = local_doc(local_run)
    assert doc['layers_basis'] == 'layer_png_per_layer_dice_first' and doc['schema'] == local_align.SCHEMA
    assert doc['search_px'] == 10 and doc['refine_pool'] == 120
    counts = doc['summary']['applied_source_counts']
    assert sorted(counts) == ['global', 'local', 'unit'] and sum(counts.values()) == len(doc['rois']) == len(queries(local_run))
    assert counts['local'] > 0
    assert doc['summary']['n_rois'] == len(doc['rois'])
    assert sorted(local_run.execution['timings_s']) == ['layer_read', 'local_layer_read', 'local_refine', 'unit_align']
    keys = {f'layer:{n}' for n in cli_t.LAYERS} | {f'cnt:{n}' for n in cli_t.CNTS}
    for item in doc['rois']:
        if item['status'] == 'ok':
            assert {u['source'] + ':' + u['layer'] for u in item['layers_used']} == keys
    assert any(s['stage'] == 'refine_pool' for s in local_run.payload['funnel']['directions'][0]['stages'])


def test_a_local_without_unit_align_starts_from_the_global_offset(world):
    got = make(world, '--local-align-um', '1', '--apply-align', 'local')
    assert 'unit_offsets' not in got.alignment and sorted(got.execution['timings_s']) == ['local_layer_read', 'local_refine']
    assert got.payload['inputs']['layers']['reference']['n_files'] == 3
    for name in ('reference', 'target'):      # layer-align/T8 adv:8: inputs.layers(정렬이 읽은 공통 층)는 layer_inputs(그림용 전체 Layer)의 부분집합이다
        used = got.payload['inputs']['layers'][name]['n_files']
        assert used <= len(got.alignment['layer_inputs'][name]['files'])
    doc = local_doc(got)
    g = got.alignment['offset_xy_px']
    assert all(item['start_offset_xy_px'] == g for item in doc['rois'])
    assert doc['summary']['applied_source_counts']['unit'] == 0


# --- (b) X3: 재점수 뒤에 병합·억제 -----------------------------------------------------------------------------------

def overlap_fraction(a, b):
    w = a['window_px']
    ox = w - abs(a['window_origin_px'][1] - b['window_origin_px'][1])
    oy = w - abs(a['window_origin_px'][0] - b['window_origin_px'][0])
    return ox * oy / float(w * w) if ox > 0 and oy > 0 else 0.0


def scripted(monkeypatch, recorded):
    """풀 재점수 패스에서 겹치는 두 창 A(첫째), B 의 합집합 변화를 0 / 80 칸으로 바꾼다(단위 점수에서는 A >= B).
    A 나 B 와 30% 이상 겹치는 다른 창도 0 으로 해서(국소 보정으로 거짓 변경이 사라진 모양) B 만 80 칸으로 남긴다."""
    real = change_map.case_contributions

    def fake(pairs, windows, **kw):
        out = real(pairs, windows, **kw)
        if kw.get('window_offsets') is None:
            return out
        windows = list(windows)
        a = windows[0]
        j = next((j for j, other in enumerate(windows[1:], start=1) if overlap_fraction(a, other) >= 0.5), None)
        recorded['A'], recorded['B'] = a, (None if j is None else windows[j])
        if j is None:
            return out
        b = windows[j]
        rows = out['windows']
        for k, other in enumerate(windows):
            if k != j and (overlap_fraction(a, other) >= 0.3 or overlap_fraction(b, other) >= 0.3):
                rows[k] = {**rows[k], 'union_change_cells': 0, 'sum_residual_cells': 0}
        rows[j] = {**rows[j], 'union_change_cells': 80}
        return out
    monkeypatch.setattr(change_map, 'case_contributions', fake)


def test_b_overlapping_window_survives_when_the_better_unit_window_loses_after_refine(world, monkeypatch):
    recorded = {}
    scripted(monkeypatch, recorded)
    got = make(world, *LOCAL_ARGS, '--suppress-overlap', '0.3', overlap=1.0)
    a, b = recorded['A'], recorded['B']
    assert b is not None, 'B must still be in the pool when the refine pass runs (suppression comes after the rescore)'
    assert a['score_um2'] >= b['score_um2'] and a['layer'] == b['layer'] and overlap_fraction(a, b) >= 0.5
    ids = {q['region_id'] for q in queries(got)}
    assert change_map.region_id_for(b['layer'], b['center_xy_um']) in ids
    assert change_map.region_id_for(a['layer'], a['center_xy_um']) not in ids
    top = [q for q in queries(got) if q['region_id'] == change_map.region_id_for(b['layer'], b['center_xy_um'])][0]
    assert top['score_um2'] == pytest.approx(80 * PITCH * PITCH)
    stages = {s['stage']: s for s in got.payload['funnel']['directions'][0]['stages']}
    assert stages['refine_pool']['reasons']['zero_after_refine'] >= 2 and stages['suppressed']['count'] <= stages['merged']['count']


def test_b_pool_is_the_top_n_raw_windows_and_the_funnel_keeps_the_counts(world, monkeypatch):
    seen = {}
    real = change_map.case_contributions

    def spy(pairs, windows, **kw):
        if kw.get('window_offsets') is not None:
            seen['pool'] = [(w['layer'], tuple(w['center_xy_um']), w['score_um2']) for w in windows]
        return real(pairs, windows, **kw)
    monkeypatch.setattr(change_map, 'case_contributions', spy)
    everything = make(world, *LOCAL_ARGS, '--refine-pool', '1000')
    full_pool = list(seen['pool'])
    scores = [s for _, _, s in full_pool]
    assert scores == sorted(scores, reverse=True) and len(full_pool) > 3
    small = make(world, *LOCAL_ARGS, '--refine-pool', '3')
    assert seen['pool'] == full_pool[:3]
    n_all = {d['direction']: {s['stage']: s for s in d['stages']} for d in everything.payload['funnel']['directions']}
    for direction in small.payload['funnel']['directions']:
        stages = {s['stage']: s for s in direction['stages']}
        n_candidates = stages['change_candidates']['count']
        assert n_candidates == n_all[direction['direction']]['change_candidates']['count'] == len(full_pool)
        pool = stages['refine_pool']
        assert pool['count'] + pool['dropped'] == n_candidates and pool['reasons']['outside_refine_pool'] == n_candidates - 3
        assert stages['merged']['count'] <= pool['count'] <= 3 and stages['top_k']['count'] <= 3
        assert direction['refine_pool'] == 3
    assert len(queries(small)) <= 3 < len(queries(everything))
    # 풀을 키우면 top_k 가 차고, 모자라면 경고에 --refine-pool 안내가 붙는다
    codes = {w['code']: w for w in small.payload['warnings']}
    assert small.code == 3 and 'top_k_not_filled' in codes and '--refine-pool' in codes['top_k_not_filled']['detail']
    assert 'top_k_not_filled' not in {w['code'] for w in everything.payload['warnings']} or everything.code == 3


def test_b_default_pool_is_ten_times_top_k(local_run):
    assert local_run.payload['change_args']['refine_pool'] == 10 * BIG['top_k']
    assert local_run.payload['funnel']['directions'][0]['refine_pool'] == 10 * BIG['top_k']


# --- (c) 경계를 넘는 창 -----------------------------------------------------------------------------------------------

def test_c_boundary_window_uses_the_field_and_keeps_only_the_real_change(local_run):
    found = xb_queries(local_run)
    assert found, 'the real 4 x 4 change must keep a window on the unit boundary'
    for q in found:
        assert q['offset_source'] == 'unit' and q['score_um2'] == pytest.approx(16 * PITCH * PITCH)
        assert sum(c['residual_um2'] for c in q['contributing_cases']) == pytest.approx(16 * PITCH * PITCH)
    doc = {i['query_id']: i for i in local_doc(local_run)['rois']}
    crossing = [doc[q['query_id']] for q in found if doc[q['query_id']]['flags'] == ['crosses_unit_boundary_field_used']]
    assert crossing, '창이 경계를 넘는 ROI 가 하나는 있어야 한다'
    for item in crossing:
        assert item['status'] == 'skipped' and item['reason'] == 'crosses_unit_boundary_field_used'
        assert item['applied_source'] == 'unit' and item['local_offset_xy_px'] is None


def test_c_boundary_roi_figures_use_the_unit_field_and_the_other_rois_the_scalar(local_run):
    """layer-align/T8 adv:4: 국소 미적용 + crop 이 unit 경계를 걸치는 ROI 는 target crop 캡션이 'unit 별 offset(경계 창)', 나머지는 스칼라 offset."""
    html = (local_run.out / 'alignment.html').read_text(encoding='utf-8')
    caps = [c for c in captions(html) if c.startswith('ROI 주변 · target Layer stack')]
    assert len(caps) == len(queries(local_run))
    assert any('unit 별 offset(경계 창)' in c for c in caps) and any('offset [' in c for c in caps)
    boundary = [q for q in queries(local_run) if q['offset_source'] == 'unit' and 'crosses_unit_boundary_field_used' in q['flags']]
    assert sum('unit 별 offset(경계 창)' in c for c in caps) >= len(boundary) > 0
    assert not any('unit 별 offset(경계 창)' in c and '출처 ROI 국소' in c for c in caps)


# --- (d) 상태별 적용·되돌림 ---------------------------------------------------------------------------------------------

def force_local_status(monkeypatch, status):
    real = layer_align.choose_dice_first

    def fake(scores, s_xy, radius, **kw):
        result = real(scores, s_xy, radius, **kw)
        if radius == 10:           # 국소 보정 호출만(unit 탐색은 --unit-align-um 1.5 → 15 px)
            result = {**result, 'status': status, 'apply': False}
            if status in ('ambiguous', 'low_score', 'insufficient_support'):
                result.update({'offset_xy_px': None, 'delta_xy_px': None, 'decided_by_layer': None})
        return result
    monkeypatch.setattr(layer_align, 'choose_dice_first', fake)


@pytest.mark.parametrize('status', ['ambiguous', 'low_score', 'search_bound_hit'])
def test_d_a_failed_local_choice_returns_to_the_unit_offset(world, monkeypatch, status):
    force_local_status(monkeypatch, status)
    got = make(world, *LOCAL_ARGS)
    offsets = {tuple(u['unit_index_xy'][::-1]): u['offset_xy_px'] for u in got.alignment['unit_offsets']}
    doc = {i['query_id']: i for i in local_doc(got)['rois']}
    assert queries(got)
    for q in queries(got):
        item = doc[q['query_id']]
        if item['status'] == 'skipped':           # 경계 창은 이 호출과 무관
            continue
        assert item['status'] == status and item['applied_source'] == 'unit'
        assert q['offset_source'] == 'unit' and q['offset_xy_px_applied'] == offsets[cli_t.unit_of(q)] == item['applied_offset_xy_px']
    assert local_doc(got)['summary']['applied_source_counts']['local'] == 0


def test_d_change_biased_is_applied_with_a_flag(world, monkeypatch):
    real = local_align.choose

    def far_ring(sum_a, inter, sum_b, r, **kw):
        out = real(sum_a, inter, sum_b, r, **kw)
        return {**out, 'status': 'ok', 'd': (-r + 2, r - 2)}
    monkeypatch.setattr(local_align, 'choose', far_ring)
    got = make(world, *LOCAL_ARGS)
    doc = local_doc(got)
    biased = [i for i in doc['rois'] if i['status'] == 'change_biased']
    assert biased and all('change_biased' in i['flags'] and i['applied_source'] == 'local' for i in biased)
    by_query = {q['query_id']: q for q in queries(got)}
    for item in biased:
        q = by_query[item['query_id']]
        assert q['offset_source'] == 'local' and q['offset_xy_px_applied'] == item['local_offset_xy_px']


def test_d_ring_inconclusive_returns_to_the_unit_offset(world, monkeypatch):
    real = local_align.choose

    def no_ring(sum_a, inter, sum_b, r, **kw):
        out = real(sum_a, inter, sum_b, r, **kw)
        return {**out, 'status': 'insufficient_support', 'd': None}
    monkeypatch.setattr(local_align, 'choose', no_ring)
    got = make(world, *LOCAL_ARGS)
    doc = local_doc(got)
    assert doc['summary']['applied_source_counts']['local'] == 0
    assert any(i['status'] == 'ring_inconclusive' and i['applied_source'] == 'unit' for i in doc['rois'])


# --- (e) A4: 연결 일치 --------------------------------------------------------------------------------------------------

def test_e_applied_offsets_agree_across_queries_both_directions_pairs_and_the_local_document(local_run):
    r2t, t2r = queries(local_run), queries(local_run, 'target_to_reference')
    assert len(r2t) == len(t2r) > 4
    doc = {i['query_id']: i for i in local_doc(local_run)['rois']}
    other = {q['region_id']: q for q in t2r}
    sources = set()
    for q in r2t:
        item = doc[q['query_id']]
        assert q['offset_xy_px_applied'] == item['applied_offset_xy_px'] and q['offset_source'] == item['applied_source']
        sources.add(q['offset_source'])
        t = other[q['region_id']]
        assert t['offset_xy_px_applied'] == q['offset_xy_px_applied'] and t['offset_source'] == q['offset_source']
        assert t['center_xy_um'][0] == pytest.approx(q['center_xy_um'][0] + q['offset_xy_px_applied'][0] * PITCH, abs=1e-6)
        assert t['center_xy_um'][1] == pytest.approx(q['center_xy_um'][1] + q['offset_xy_px_applied'][1] * PITCH, abs=1e-6)
        assert q['roi_ids'] and len(q['roi_ids']) == q['n_rois']
    assert 'local' in sources
    sidecar = tcc.load(local_run.out, 'direct/direct_pairs_sidecar.json')
    correspondence = tcc.load(local_run.out, 'direct/roi_correspondence.json')
    by_region = {q['region_id']: q for q in r2t}
    assert sidecar['pairs']
    for pair_id, record in sidecar['pairs'].items():
        q = by_region[record['region_id']]
        assert record['offset_xy_px_applied'] == q['offset_xy_px_applied'] and record['offset_source'] == q['offset_source']
    for pair in correspondence['pairs']:
        record = sidecar['pairs'][pair['pair_id']]
        applied = record['offset_xy_px_applied']
        dx = pair['b']['center_xy_um'][0] - pair['a']['center_xy_um'][0]
        dy = pair['b']['center_xy_um'][1] - pair['a']['center_xy_um'][1]
        assert (dx, dy) == pytest.approx((applied[0] * PITCH, applied[1] * PITCH), abs=1e-6)


def test_e_contributing_cases_have_no_duplicate_case_layer_pairs_and_cover_all_layers(local_run):
    for q in queries(local_run):
        keys = [(c['case_key'], c['layer']) for c in q['contributing_cases']]
        assert keys and len(keys) == len(set(keys))


def test_e_windows_with_the_unit_offset_keep_the_unit_mode_values(unit_run, local_run):
    """국소 보정이 unit offset 과 같은 이동을 고른 창은 unit 모드(field)와 같은 점수·기여를 낸다. 값이 달라지는 창은 오직 국소 이동을 쓴 창이다."""
    by_region = {q['region_id']: q for q in queries(unit_run)}
    same = 0
    for q in queries(local_run):
        assert q['union_delta_um2'] == pytest.approx(q['union_area_target_um2'] - q['union_area_reference_um2'], abs=1e-9)
        assert 0 < q['observed_fraction'] <= 1
        base = by_region.get(q['region_id'])
        if base is None or base['offset_xy_px_applied'] != q['offset_xy_px_applied']:
            continue
        same += 1
        for key in ('score_um2', 'sum_residual_um2', 'union_area_reference_um2', 'union_area_target_um2', 'observed_fraction'):
            assert q[key] == pytest.approx(base[key])
        assert [(c['case_key'], c['layer'], c['residual_um2']) for c in q['contributing_cases']] == \
               [(c['case_key'], c['layer'], c['residual_um2']) for c in base['contributing_cases']]
    assert same >= 4


def test_e_folded_replicas_stay_attached_to_the_representative(world):
    got = make(world, *LOCAL_ARGS, '--unit-fold', '--unit-fold-tolerance-um', str(cli_t.FOLD_UM))
    folded = [q for q in queries(got) if q.get('unit_fold', {}).get('n_replicas')]
    assert folded
    ids = {q['region_id'] for q in queries(got)}
    doc = {i['query_id']: i for i in local_doc(got)['rois']}
    for q in folded:
        fold = q['unit_fold']
        assert len(fold['replicas']) == fold['n_replicas'] and all(r['region_id'] not in ids for r in fold['replicas'])
        assert doc[q['query_id']]['applied_offset_xy_px'] == q['offset_xy_px_applied']
    stages = {s['stage']: s for s in got.payload['funnel']['directions'][0]['stages']}
    assert stages['unit_folded']['dropped'] > 0
    names = [s['stage'] for s in got.payload['funnel']['directions'][0]['stages']]
    assert names.index('refine_pool') == 1 and names.index('unit_folded') == names.index('top_k') - 1


# --- (f) 오류 코드 -----------------------------------------------------------------------------------------------------

def expect_error(world, tmp_path, capsys, code, *extra):
    out = tmp_path / 'out'
    args = tcc.cli_args(world.config, out, world.dl1, **BIG)
    assert main(args[:-2] + list(extra) + args[-2:]) == 2
    assert code in capsys.readouterr().err
    assert not out.exists()


def test_f_error_codes(world, tmp_path, capsys):
    expect_error(world, tmp_path, capsys, 'apply_align_requires_local_align', '--apply-align', 'local')
    expect_error(world, tmp_path, capsys, 'local_align_um_too_small_for_unit_delta', '--apply-align', 'local', '--local-align-um', '0.3')
    expect_error(world, tmp_path, capsys, 'refine_pool_requires_local', '--refine-pool', '5')
    expect_error(world, tmp_path, capsys, 'refine_pool_requires_local', '--apply-align', 'unit', '--unit-align-um', '1', '--refine-pool', '5')
    expect_error(world, tmp_path, capsys, 'invalid_candidates_change_argument', *LOCAL_ARGS, '--refine-pool', '0')
    expect_error(world, tmp_path, capsys, 'local_align_um_invalid', '--apply-align', 'local', '--local-align-um', '31')


def test_f_layer_folder_is_required_for_local(world, tmp_path, capsys):
    legacy = tmp_path / 'legacy'
    shutil.copytree(world.dl1, legacy)
    for product in tcc.PRODUCTS.values():
        shutil.rmtree(legacy / product / 'Layer')
    out = tmp_path / 'out'
    args = tcc.cli_args(world.config, out, legacy, **BIG)
    assert main(args[:-2] + ['--apply-align', 'local', '--local-align-um', '1'] + args[-2:]) == 2
    assert 'layer_folder_required' in capsys.readouterr().err and not out.exists()


def test_f_diagnostic_radius_rule_is_unchanged(world, tmp_path, capsys):
    expect_error(world, tmp_path, capsys, 'local_align_um_invalid', '--local-align-um', '3.5')      # 35 px > 30, 진단 경로


# --- (h) T4c: R2 권고 --------------------------------------------------------------------------------------------------

def test_h_adv1_radius_floor_depends_on_unit_alignment(world, tmp_path, capsys):
    """ΔU = 2: unit 정렬이 있으면 시작점이 unit offset 이라 |ΔU| + 2 = 4 px, 없으면 시작점이 전역 g 라 2·|ΔU| + 2 = 6 px."""
    expect_error(world, tmp_path, capsys, 'local_align_um_too_small_for_unit_delta', '--apply-align', 'local', '--local-align-um', '0.5')
    out = tmp_path / 'msg'
    args = tcc.cli_args(world.config, out, world.dl1, **BIG)
    assert main(args[:-2] + ['--apply-align', 'local', '--local-align-um', '0.5'] + args[-2:]) == 2
    err = capsys.readouterr().err
    assert '필요 6 px = 0.6 µm' in err and 'unit 정렬 없음' in err
    with_unit = make(world, '--unit-align-um', '1.5', '--local-align-um', '0.4', '--apply-align', 'local')       # 4 px: 경계값
    assert with_unit.code in (0, 3) and local_doc(with_unit)['search_px'] == 4
    expect_error(world, tmp_path, capsys, 'local_align_um_too_small_for_unit_delta',
                 '--unit-align-um', '1.5', '--apply-align', 'local', '--local-align-um', '0.3')
    without_unit = make(world, '--local-align-um', '0.6', '--apply-align', 'local')                               # 6 px: 경계값
    assert without_unit.code in (0, 3) and local_doc(without_unit)['search_px'] == 6


@pytest.fixture(scope='module')
def nb_world(tmp_path_factory):
    NB['on'] = True
    try:
        base = tmp_path_factory.mktemp('nb_world')
        datas, assignments = {}, {}
        for side in ('reference', 'target'):
            datas[side], assignments[side] = build_side(base, side, 2)
        config = tcc.write_config(base, datas, assignments, map_spec={'diameter_um': 5})
    finally:
        NB['on'] = False
    return SimpleNamespace(base=base, config=config, dl1=base / 'dl1', tmp_path_factory=tmp_path_factory)


def test_h_adv2_unit_interior_boundary_is_eroded_by_r(nb_world, monkeypatch):
    """국소 Ω 는 unit 내부 경계 쪽만 r px 침식한다(이웃 unit 형상이 x+s+δ 로 섞이지 않게). 침식을 끄면 이 테스트는 실패한다.
    경계 x = 650 의 왼쪽 40 px 에 변경이 있는 월드: 그 창의 상자는 경계에 닿고, 침식 띠는 정확히 r 열이다."""
    raw, seen, boxes = [], [], []
    real_domain, real_scores, real_box = local_align.valid_domain, layer_align.unit_window_scores, change_candidates.alignment_report._crop_box
    geometry = layer_align.UnitGeometry((1300, 1300))

    def crop_box(y, x, w, height, width):
        out = real_box(y, x, w, height, width)
        if geometry.unit_index((y, x)) == geometry.unit_index((y + w - 1, x + w - 1)):     # 경계를 넘는 창은 국소 보정을 하지 않는다
            boxes.append((out, geometry.unit_index((y + w // 2, x + w // 2))))
        return out

    def domain(*args, **kw):
        out = real_domain(*args, **kw)
        raw.append(out.copy())
        return out

    def scores(a, b, omega, *args, **kw):
        if len(raw) > len(seen):                    # 창마다 첫 호출(상자 전체 Ω)만 본다
            seen.append((raw[len(seen)], omega.copy()))
        return real_scores(a, b, omega, *args, **kw)
    monkeypatch.setattr(local_align, 'valid_domain', domain)
    monkeypatch.setattr(layer_align, 'unit_window_scores', scores)
    monkeypatch.setattr(change_candidates.alignment_report, '_crop_box', crop_box)
    got = make(nb_world, *LOCAL_ARGS)
    r = local_doc(got)['search_px']
    regions = geometry.unit_regions()
    assert seen and len(seen) <= len(boxes)
    eroded = 0
    for (before, after), ((y0, y1, x0, x1), idx) in zip(seen, boxes):
        ry0, ry1, rx0, rx1 = regions[idx]
        want = np.zeros(before.shape, dtype=bool)         # 기대: unit 내부 경계(frame 가장자리가 아닌 쪽)만 r 침식한 영역
        want[max(ry0 + (r if ry0 > 0 else 0) - y0, 0):max(ry1 - (r if ry1 < 1300 else 0) - y0, 0),
             max(rx0 + (r if rx0 > 0 else 0) - x0, 0):max(rx1 - (r if rx1 < 1300 else 0) - x0, 0)] = True
        assert (after == (before & want)).all()
        eroded += bool((before & ~after).any())
    assert eroded > 0                                      # 경계 근처 창이 실제로 있었다


def test_h_adv3_boundary_flag_is_in_queries_sidecar_and_local_document(local_run, unit_run):
    flag = change_candidates.CROSSES_BOUNDARY_FLAG
    doc = {i['query_id']: i for i in local_doc(local_run)['rois']}
    crossing = [q for q in queries(local_run) if flag in doc[q['query_id']]['flags']]
    assert crossing
    assert all(flag in q['flags'] for q in crossing)
    assert not any(flag in q['flags'] for q in queries(local_run) if flag not in doc[q['query_id']]['flags'])
    sidecar = tcc.load(local_run.out, 'direct/direct_pairs_sidecar.json')
    by_region = {q['region_id']: q for q in queries(local_run)}
    n_pairs = 0
    for record in sidecar['pairs'].values():
        q = by_region[record['region_id']]
        assert (flag in record['flags']) == (flag in q['flags'])
        n_pairs += flag in record['flags']
    assert n_pairs
    assert not any(flag in q['flags'] for q in queries(unit_run))                          # unit 모드에는 없다
    for name in ('reference_export', 'target_export'):
        keys = {tuple(sorted(r)) for r in json.loads((local_run.out / name / 'review_rois.json').read_text(encoding='utf-8'))['rois']}
        base = {tuple(sorted(r)) for r in json.loads((unit_run.out / name / 'review_rois.json').read_text(encoding='utf-8'))['rois']}
        assert keys == base


def test_h_adv4_attributes_policy_follows_the_applied_offsets(world):
    old = change_candidates.ATTRIBUTES_POLICY_ALL
    plain = make(world, '--merge-scope', 'all')
    unit = make(world, '--merge-scope', 'all', '--unit-align-um', '1.5', '--apply-align', 'unit')
    local = make(world, '--merge-scope', 'all', *LOCAL_ARGS)
    assert plain.payload['policy']['attributes'] == old
    for run in (unit, local):
        assert run.payload['policy']['attributes'] == 'contributing_cases_all_layers_window_offset' != old
    assert 'attributes' not in make(world, *LOCAL_ARGS).payload['policy']


def test_h_adv7_apply_and_diagnostic_radius_use_the_same_rounding(world):
    """0.45 µm / 0.1 µm = 4.5 px: 진단 경로(Python round)는 4 px. apply 경로도 같다(예전에는 .5 올림으로 5 px)."""
    assert local_align.search_radius_px(0.45, PITCH) == 4
    got = make(world, '--unit-align-um', '1.5', '--local-align-um', '0.45', '--apply-align', 'local')
    assert got.code in (0, 3) and local_doc(got)['search_px'] == local_align.search_radius_px(0.45, PITCH) == 4


# --- (g) 행 키 불변 ----------------------------------------------------------------------------------------------------

def test_g_review_rois_row_keys_do_not_change(world, unit_run, local_run):
    for name in ('reference_export', 'target_export'):
        base = json.loads((unit_run.out / name / 'review_rois.json').read_text(encoding='utf-8'))
        got = json.loads((local_run.out / name / 'review_rois.json').read_text(encoding='utf-8'))
        assert got['rois'] and {tuple(sorted(r)) for r in got['rois']} == {tuple(sorted(r)) for r in base['rois']}
