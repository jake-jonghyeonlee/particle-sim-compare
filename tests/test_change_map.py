"""change-roi/T5: 변경 지도·창 점수·기여 case 분류(합성 입력). 좌표: reference 픽셀 p ↔ target 픽셀 p + offset(dx, dy)."""
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from simcompare import change_map
from simcompare.candidates import merge_regions
from simcompare.change_map import (MaskPair, case_contributions, change_maps, estimate_change_map_bytes,
                                   pair_cases, region_id_for, to_region_infos, window_candidates)
from simcompare.numeric_schema import NumericContractError

SRC = str(Path(__file__).resolve().parents[1] / 'src')
BIG = 10 ** 12
PITCH = 0.1

# 3.9 인터프리터 subprocess 에서도 그대로 쓰는 자기완결 사례 생성기(numpy 와 simcompare 만 쓴다).
CASE_SRC = '''
import numpy as np
from simcompare.change_map import MaskPair, case_contributions, change_maps, window_candidates


def rect(shape, y, x, h, w):
    m = np.zeros(shape, bool)
    m[y:y + h, x:x + w] = True
    return m


def union_kw(maps):
    return {k: maps[k] for k in ('union_change', 'union_ref', 'union_tgt')}


def representative_case():
    shape = (300, 300)
    rng = np.random.default_rng(5)
    pairs = []
    for i in range(6):
        base = rng.random(shape) < 0.02
        pairs.append(MaskPair('c%d' % i, 'M1' if i % 2 else 'M2', base, base.copy()))
    pairs.append(MaskPair('shift', 'M1', rect(shape, 40, 40, 20, 10), rect(shape, 40, 48, 20, 10)))
    pairs.append(MaskPair('reshape', 'M1', rect(shape, 40, 200, 20, 20), rect(shape, 40, 200, 20, 36)))
    pairs.append(MaskPair('appear', 'M2', np.zeros(shape, bool), rect(shape, 200, 40, 15, 15)))
    pairs.append(MaskPair('vanish', 'M2', rect(shape, 200, 200, 15, 15), np.zeros(shape, bool)))
    ref_valid = np.ones(shape, bool)
    ref_valid[100:120, 100:130] = False
    tgt_valid = np.ones(shape, bool)
    kw = dict(ref_valid=ref_valid, tgt_valid=tgt_valid, offset_px=(0, 0), pitch_um=0.1, tolerance_um=0.2)
    maps = change_maps(pairs, max_operation_bytes=10 ** 12, window_um=5.0, **kw)
    found = window_candidates(maps['maps'], maps['D'], **union_kw(maps), pitch_um=0.1, window_um=5.0,
                              max_operation_bytes=10 ** 12)
    top = found['candidates'][:6]
    contrib = case_contributions(pairs, top, max_operation_bytes=10 ** 12, **kw)
    return {'stats': maps['stats'], 'candidates': top, 'contributions': contrib['windows'],
            'sums': {k: int(v.sum()) for k, v in sorted(maps['maps'].items())}}
'''
_ns = {}
exec(CASE_SRC, _ns)
rect = _ns['rect']
union_kw = _ns['union_kw']
representative_case = _ns['representative_case']


def kwargs(shape_ref, shape_tgt=None, offset=(0, 0), tolerance=0.2, ref_valid=None, tgt_valid=None):
    return dict(ref_valid=np.ones(shape_ref, bool) if ref_valid is None else ref_valid,
                tgt_valid=np.ones(shape_tgt or shape_ref, bool) if tgt_valid is None else tgt_valid,
                offset_px=offset, pitch_um=PITCH, tolerance_um=tolerance)


def run(pairs, window_um=5.0, **kw):
    maps = change_maps(pairs, max_operation_bytes=BIG, window_um=window_um, **kw)
    found = window_candidates(maps['maps'], maps['D'], **union_kw(maps), pitch_um=PITCH, window_um=window_um,
                              max_operation_bytes=BIG)
    return maps, found


def test_four_planted_changes_are_top_candidates_with_expected_classes():
    shape = (300, 300)
    rng = np.random.default_rng(1)
    pairs = []
    for i in range(5):
        same = rng.random(shape) < 0.03
        pairs.append(MaskPair(f'same{i}', 'M1', same, same.copy()))
    planted = {
        'shifted': (MaskPair('shift', 'M1', rect(shape, 40, 40, 20, 10), rect(shape, 40, 48, 20, 10)), (40, 40, 20, 18), (40, 40, 20, 18)),
        'reshaped': (MaskPair('reshape', 'M1', rect(shape, 40, 200, 20, 20), rect(shape, 40, 200, 20, 36)), (40, 200, 20, 36), (40, 222, 20, 14)),
        'appeared': (MaskPair('appear', 'M1', np.zeros(shape, bool), rect(shape, 200, 40, 15, 15)), (200, 40, 15, 15), (200, 40, 15, 15)),
        'disappeared': (MaskPair('vanish', 'M1', rect(shape, 200, 200, 15, 15), np.zeros(shape, bool)), (200, 200, 15, 15), (200, 200, 15, 15)),
    }
    pairs += [item[0] for item in planted.values()]
    kw = kwargs(shape)
    maps, found = run(pairs, **kw)
    assert maps['stats']['pairs'] == 9 and maps['stats']['pairs_with_change_by_layer'] == {'M1': 4}
    # 같은 변경의 겹치는 창은 merge_regions 로 하나로 합치고, 대표 4개가 심은 4곳의 잔차 위에 있어야 한다
    infos = to_region_infos(found['candidates'], 'P', 'v1', 'f')
    merged = merge_regions(infos, 5.0, 0.5, rank_key=change_map.region_rank_key)
    assert len(merged) == 4
    by_id = {region_id_for(c['layer'], c['center_xy_um']): c for c in found['candidates']}
    reps = [by_id[m['representative'].region_id] for m in merged]

    def covers(c, box):
        y, x, h, w = box
        wy, wx = c['window_origin_px']
        return wy <= y and wx <= x and y + h <= wy + c['window_px'] and x + w <= wx + c['window_px']

    for kind, (_, _, residual_box) in planted.items():
        assert any(covers(c, residual_box) for c in reps), kind
    chosen = {}
    for kind, (_, whole, _) in planted.items():
        inside = [c for c in found['candidates'] if covers(c, whole)]
        assert inside, kind
        chosen[kind] = inside[0]
    result = case_contributions(pairs, list(chosen.values()), max_operation_bytes=BIG, **kw)
    for index, kind in enumerate(chosen):
        window = result['windows'][index]
        names = [c['case_key'] for c in window['contributing_cases']]
        assert names == [planted[kind][0].case_key], (kind, names)
        assert window['contributing_cases'][0]['classification'] == kind, (kind, window['contributing_cases'][0])
    assert result['constants']['CLASS_IOU_DELTA'] == 0.2 and result['constants']['CLASS_IOU_MIN'] == 0.2
    shifted = result['windows'][0]['contributing_cases'][0]
    assert shifted['local_shift_um'] == [0.8, 0.0] and shifted['iou_local'] == 1.0 and shifted['iou_global'] < 0.5


def test_unchanged_pairs_score_zero_and_give_no_candidates():
    shape = (200, 200)
    rng = np.random.default_rng(2)
    pairs = []
    for i in range(4):
        same = rng.random(shape) < 0.05
        pairs.append(MaskPair(f'c{i}', 'M1', same, same.copy()))
    maps, found = run(pairs, **kwargs(shape))
    assert int(maps['maps']['M1'].sum()) == 0 and found['candidates'] == []
    assert maps['maps']['M1'].dtype == np.uint32


def test_zero_tolerance_equals_xor_inside_the_comparison_domain():
    ref_shape, tgt_shape, offset = (150, 160), (170, 140), (7, -5)
    rng = np.random.default_rng(3)
    a, b = rng.random(ref_shape) < 0.3, rng.random(tgt_shape) < 0.3
    ref_valid, tgt_valid = np.ones(ref_shape, bool), np.ones(tgt_shape, bool)
    ref_valid[20:40, 30:60] = False
    tgt_valid[100:130, 10:50] = False
    out = change_maps([MaskPair('c', 'M1', a, b)], **kwargs(ref_shape, tgt_shape, offset, 0.0, ref_valid, tgt_valid),
                      max_operation_bytes=BIG)
    dx, dy = offset
    expect = np.zeros(ref_shape, bool)
    expect_domain = np.zeros(ref_shape, bool)
    for y in range(ref_shape[0]):
        for x in range(ref_shape[1]):
            ty, tx = y + dy, x + dx
            if 0 <= ty < tgt_shape[0] and 0 <= tx < tgt_shape[1] and ref_valid[y, x] and tgt_valid[ty, tx]:
                expect_domain[y, x] = True
                expect[y, x] = a[y, x] != b[ty, tx]
    assert np.array_equal(out['D'], expect_domain)
    assert np.array_equal(out['maps']['M1'], expect.astype(np.uint32))
    assert out['stats']['tolerance_px'] == 0


def test_one_pixel_shift_is_suppressed_inside_tolerance_and_larger_change_survives():
    shape = (120, 120)
    rng = np.random.default_rng(4)
    base = np.zeros(shape, bool)
    for _ in range(12):
        y, x = rng.integers(10, 100, 2)
        base[y:y + 6, x:x + 3] = True
    moved = np.roll(base, 1, axis=1)
    kw = kwargs(shape)
    kw['ref_valid'][:, :3] = kw['tgt_valid'][:, :3] = False
    quiet = change_maps([MaskPair('c', 'M1', base, moved)], max_operation_bytes=BIG, **kw)
    assert int(quiet['maps']['M1'].sum()) == 0
    loud = change_maps([MaskPair('c', 'M1', base, np.roll(base, 5, axis=1))], max_operation_bytes=BIG, **kw)
    assert int(loud['maps']['M1'].sum()) > 0
    exact = change_maps([MaskPair('c', 'M1', base, moved)], max_operation_bytes=BIG, **{**kw, 'tolerance_um': 0.0})
    assert int(exact['maps']['M1'].sum()) > 0
    # 허용 폭 밖의 작은 변경(독립된 3x3 한 점)은 보존
    spot = base.copy()
    spot[110:113, 110:113] = True
    kept = change_maps([MaskPair('c', 'M1', base, spot)], max_operation_bytes=BIG, **kw)
    assert int(kept['maps']['M1'].sum()) == 9


def test_valid_hole_and_frame_size_difference_limit_the_domain():
    ref_shape, tgt_shape = (200, 200), (180, 220)
    ref_valid = np.ones(ref_shape, bool)
    ref_valid[50:80, 50:80] = False
    a = np.zeros(ref_shape, bool)
    b = np.zeros(tgt_shape, bool)
    b[60:70, 60:70] = True                    # 변경이 reference 의 valid 구멍 안(offset 0)
    b[150:160, 190:200] = True                # target 에만 있는 영역(reference 밖은 아님: x 190..199 < 200)
    out = change_maps([MaskPair('c', 'M1', a, b)], **kwargs(ref_shape, tgt_shape, (0, 0), 0.0, ref_valid),
                      max_operation_bytes=BIG)
    assert not out['D'][50:80, 50:80].any()
    assert int(out['maps']['M1'][60:70, 60:70].sum()) == 0
    assert not out['D'][180:, :].any()        # target 이 180 행까지라 아래쪽은 관측 불가
    assert int(out['maps']['M1'][150:160, 190:200].sum()) == 100
    assert out['stats']['domain_pixels'] < out['stats']['frame_pixels']


def test_reversing_direction_gives_the_same_physical_candidates():
    stride = 12                                # window 5 µm = 50 px
    world0 = np.zeros((420, 420), bool)
    rng = np.random.default_rng(7)
    for _ in range(60):
        y, x = rng.integers(0, 400, 2)
        world0[y:y + 8, x:x + 4] = True
    world1 = world0.copy()
    world1[200:215, 250:262] = ~world1[200:215, 250:262]
    world1[150:160, 140:170] = True
    ref0, dx, dy = (30, 40), 2 * stride, -2 * stride
    tgt0 = (ref0[0] - dy, ref0[1] - dx)       # p_target = p_reference + offset 이므로 target 원점 = reference 원점 - offset
    ref_shape, tgt_shape = (330, 320), (310, 340)
    a = world0[ref0[0]:ref0[0] + ref_shape[0], ref0[1]:ref0[1] + ref_shape[1]]
    b = world1[tgt0[0]:tgt0[0] + tgt_shape[0], tgt0[1]:tgt0[1] + tgt_shape[1]]

    def physical(found, origin):
        return {(c['layer'], round(c['center_xy_um'][0] + origin[1] * PITCH, 6),
                 round(c['center_xy_um'][1] + origin[0] * PITCH, 6), c['score_um2'])
                for c in found['candidates'] if not c['flags']}      # 관측이 완전한 창만(가장자리 격자는 방향마다 다르다)

    _, forward = run([MaskPair('c', 'M1', a, b)], **kwargs(ref_shape, tgt_shape, (dx, dy)))
    _, backward = run([MaskPair('c', 'M1', b, a)], **kwargs(tgt_shape, ref_shape, (-dx, -dy)))
    assert forward['candidates'] and physical(forward, ref0) == physical(backward, tgt0)


def test_candidate_fields_flags_and_order():
    shape = (200, 200)
    a = np.zeros(shape, bool)
    b = np.zeros(shape, bool)
    b[0:10, 0:10] = True                      # 왼쪽 위 모서리: 일부 창은 D 밖을 포함하지 않지만
    b[100:110, 100:110] = True
    b[100:110, 140:150] = True
    tgt_valid = np.ones(shape, bool)
    tgt_valid[:, 150:] = False                # 오른쪽 띠는 관측 불가
    maps, found = run([MaskPair('c', 'M1', a, b)], **kwargs(shape, tgt_valid=tgt_valid), window_um=5.0)
    cands = found['candidates']
    assert found['window_px'] == 50 and found['stride_px'] == 12
    assert all(set(c) >= {'layer', 'center_xy_um', 'score_um2', 'observed_fraction', 'flags'} for c in cands)
    assert [c['score_um2'] for c in cands] == sorted((c['score_um2'] for c in cands), reverse=True)
    assert all(c['score_um2'] > 0 for c in cands)
    top = cands[0]
    assert top['score_um2'] == pytest.approx(160 * PITCH * PITCH)   # 격자(12 px)상 x0=96 창: 사각형 100 칸 + 다른 사각형 60 칸
    partial = [c for c in cands if c['flags']]
    assert all(c['flags'] == ['window_partially_unobserved'] and c['observed_fraction'] < 1 for c in partial)
    assert all(c['observed_fraction'] == 1 for c in cands if not c['flags'])
    # 창 중심 = origin + (왼쪽 위 + W/2) * pitch
    wpx = found['window_px']
    y0, x0 = top['window_origin_px']
    assert top['center_xy_um'] == [round((x0 + wpx / 2) * PITCH, 6), round((y0 + wpx / 2) * PITCH, 6)]
    shifted = window_candidates(maps['maps'], maps['D'], **union_kw(maps), pitch_um=PITCH, window_um=5.0,
                                max_operation_bytes=BIG, origin_xy_um=(50.0, 70.0))
    assert shifted['candidates'][0]['center_xy_um'] == [round(50.0 + top['center_xy_um'][0], 6),
                                                         round(70.0 + top['center_xy_um'][1], 6)]


def test_results_are_deterministic():
    shape = (200, 200)
    rng = np.random.default_rng(9)
    pairs = []
    for i in range(4):
        pairs.append(MaskPair(f'c{i}', 'M1' if i % 2 else 'M2', rng.random(shape) < 0.05, rng.random(shape) < 0.05))
    kw = kwargs(shape, offset=(3, 2))
    first_maps, first = run(pairs, **kw)
    second_maps, second = run(pairs, **kw)
    assert first == second
    assert all(np.array_equal(first_maps['maps'][k], second_maps['maps'][k]) for k in first_maps['maps'])
    top = first['candidates'][:3]
    assert (case_contributions(pairs, top, max_operation_bytes=BIG, **kw)
            == case_contributions(pairs, top, max_operation_bytes=BIG, **kw))
    json.dumps(case_contributions(pairs, top, max_operation_bytes=BIG, **kw), sort_keys=True, allow_nan=False)
    json.dumps(first, sort_keys=True, allow_nan=False)


def test_crop_contribution_equals_whole_frame_residual():
    shape = (160, 170)
    rng = np.random.default_rng(10)
    a, b = rng.random(shape) < 0.2, rng.random(shape) < 0.2
    pair = MaskPair('c', 'M1', a, b)
    kw = kwargs(shape, offset=(2, -1))
    maps, found = run([pair], window_um=3.0, **kw)
    wpx = found['window_px']
    for cand in found['candidates'][:6]:
        y0, x0 = cand['window_origin_px']
        want = int(maps['maps']['M1'][y0:y0 + wpx, x0:x0 + wpx].sum())
        got = case_contributions([pair], [cand], max_operation_bytes=BIG, **kw)['windows'][0]['contributing_cases']
        assert [round(g['residual_um2'] / PITCH ** 2) for g in got] == [want]
        assert round(cand['score_um2'] / PITCH ** 2) == want


def test_pair_cases_matches_by_failure_electrodes_and_layers_and_never_loads_masks():
    loaded = []

    class Records(Mapping):
        def __init__(self, tag, status, reasons=(), mask=None):
            self.tag, self.status, self.reasons, self.mask = tag, status, reasons, mask

        def __iter__(self):
            return iter([5.0, 10.0])

        def __len__(self):
            return 2

        def header(self, diameter):
            return SimpleNamespace(input_status=self.status, reasons=tuple(self.reasons))

        def __getitem__(self, diameter):
            loaded.append(self.tag)
            return SimpleNamespace(mask=self.mask)

    def case(product, tag, failure, e1, l1, e2, l2, layer, status='observed', reasons=(), mask=True):
        records = Records(f'{product}:{tag}', status, reasons, np.zeros((4, 4), bool) if mask else None)
        return SimpleNamespace(
            identity=SimpleNamespace(product_id=product, design_version=product + '_v', case_id=tag),
            failure_type=failure,
            endpoints=(SimpleNamespace(electrode_id=e1, actual_layer=l1), SimpleNamespace(electrode_id=e2, actual_layer=l2)),
            analysis_layer=layer, records=records)

    ref = SimpleNamespace(cases=[
        case('R', 'a', 'lateral_short', 'E1', 'M1', 'E2', 'M1', 'M1'),
        case('R', 'b', 'lateral_short', 'E3', 'M1', 'E4', 'M1', 'M1', status='missing'),
        case('R', 'c', 'lateral_short', 'E5', 'M1', 'E6', 'M1', 'M1', status='missing', reasons=('case_geometry_unconfirmed',)),
        case('R', 'd', 'lateral_short', 'E7', 'M1', 'E8', 'M1', 'M1'),
        case('R', 'e', 'lateral_short', 'E9', 'M1', 'E10', 'M1', 'M1'),
        case('R', 'f', 'lateral_short', 'E11', 'M1', 'E12', 'M1', 'M2'),
        case('R', 'g', 'vertical_short', 'E13', 'M1', 'E14', 'M2', 'M1'),
        case('R', 'h', 'vertical_short', 'E13', 'M1', 'E14', 'M2', 'M1'),
        case('R', 'i', 'lateral_short', 'E15', 'M1', 'E16', 'M1', 'M1', status='not_applicable'),
    ])
    tgt = SimpleNamespace(cases=[
        case('T', 'a', 'lateral_short', 'E2', 'M1', 'E1', 'M1', 'M1'),               # 전극 순서가 달라도 같은 키
        case('T', 'b', 'lateral_short', 'E3', 'M1', 'E4', 'M1', 'M1'),
        case('T', 'c', 'lateral_short', 'E5', 'M1', 'E6', 'M1', 'M1'),
        case('T', 'd', 'lateral_short', 'E7', 'M1', 'E8', 'M1', 'M1', mask=True),   # 빈 mask 도 관측 → 쌍
        case('T', 'x', 'lateral_short', 'E20', 'M1', 'E21', 'M1', 'M1'),               # target 에만 있음
        case('T', 'f', 'lateral_short', 'E11', 'M1', 'E12', 'M1', 'M3'),             # 층 불일치
        case('T', 'g', 'vertical_short', 'E13', 'M1', 'E14', 'M2', 'M1'),
        case('T', 'i', 'lateral_short', 'E15', 'M1', 'E16', 'M1', 'M1'),
    ])
    out = pair_cases(ref, tgt, diameter_um=5.0)
    assert not loaded
    assert [p.case_key for p in out['pairs']] == ['lateral_short|E1|M1|E2|M1|M1', 'lateral_short|E7|M1|E8|M1|M1']
    reasons = {(e['case_key'], e['side'], e['reason']) for e in out['cases_excluded']}
    assert ('lateral_short|E3|M1|E4|M1|M1', 'reference', 'mask_missing') in reasons
    assert ('lateral_short|E5|M1|E6|M1|M1', 'reference', 'geometry_unconfirmed') in reasons
    assert ('lateral_short|E11|M1|E12|M1|M2', 'reference', 'case_layer_mismatch') in reasons
    assert ('lateral_short|E11|M1|E12|M1|M3', 'target', 'case_layer_mismatch') in reasons
    assert ('vertical_short|E13|M1|E14|M2|M1', 'reference', 'duplicate_case_key') in reasons
    assert ('lateral_short|E15|M1|E16|M1|M1', 'reference', 'not_applicable') in reasons
    assert all(e['product'] == ('R' if e['side'] == 'reference' else 'T') for e in out['cases_excluded'])
    assert [e['case_key'] for e in out['cases_only_in_reference']] == ['lateral_short|E10|M1|E9|M1|M1']
    assert [e['case_key'] for e in out['cases_only_in_target']] == ['lateral_short|E20|M1|E21|M1|M1']
    assert out['cases_only_in_reference'][0]['product'] == 'R'
    assert out['stats']['pairs'] == 2
    # 쌍을 읽으면 그때 mask 를 읽는다
    out['pairs'][0].load()
    assert loaded == ['R:a', 'T:a']
    # 지름이 없으면 제외
    none = pair_cases(ref, tgt, diameter_um=7.0)
    assert not none['pairs'] and 'representative_diameter_missing' in {e['reason'] for e in none['cases_excluded']}


def test_admission_passes_at_the_limit_and_refuses_one_byte_below():
    shape = (120, 130)
    pairs = [MaskPair('c', 'M1', np.zeros(shape, bool), np.ones(shape, bool))]
    kw = kwargs(shape)
    need = estimate_change_map_bytes(shape, shape, 1, 50, 2)
    ok = change_maps(pairs, max_operation_bytes=need, window_um=5.0, **kw)
    assert ok['stats']['estimated_bytes'] == need
    with pytest.raises(NumericContractError) as caught:
        change_maps(pairs, max_operation_bytes=need - 1, window_um=5.0, **kw)
    assert caught.value.code == 'oversized_real_operation'
    assert str(need) in str(caught.value) and str(need - 1) in str(caught.value)
    window_need = estimate_change_map_bytes(shape, shape, 1, 50, 0)
    assert window_candidates(ok['maps'], ok['D'], **union_kw(ok), pitch_um=PITCH, window_um=5.0,
                             max_operation_bytes=window_need)
    with pytest.raises(NumericContractError) as caught:
        window_candidates(ok['maps'], ok['D'], **union_kw(ok), pitch_um=PITCH, window_um=5.0,
                          max_operation_bytes=window_need - 1)
    assert caught.value.code == 'oversized_real_operation'
    assert estimate_change_map_bytes((2400, 2400), (2400, 2400), 2, 200, 2) > estimate_change_map_bytes(
        (2400, 2400), (2400, 2400), 2, 0, 2) > 0
    assert estimate_change_map_bytes((100, 100), (100, 100), 1, 0, 0) < estimate_change_map_bytes((100, 100), (100, 100), 1, 0, 2)


def test_admission_refuses_before_any_mask_is_loaded():
    class Boom:
        case_key, layer = 'c', 'M1'

        def load(self):
            raise AssertionError('mask loaded before admission')

    with pytest.raises(NumericContractError) as caught:
        change_maps([Boom()], max_operation_bytes=10, **kwargs((50, 50)))
    assert caught.value.code == 'oversized_real_operation'


@pytest.mark.parametrize('override', [
    {'pitch_um': 0.0}, {'pitch_um': -0.1}, {'tolerance_um': -0.1}, {'window_um': 0.05},
    {'offset_px': (1.5, 0)}, {'ref_valid': np.ones((10,), bool)}])
def test_invalid_inputs_raise_numeric_contract_error(override):
    shape = (60, 60)
    pairs = [MaskPair('c', 'M1', np.zeros(shape, bool), np.zeros(shape, bool))]
    kw = {**kwargs(shape), 'window_um': 1.0, **override}
    with pytest.raises(NumericContractError) as caught:
        change_maps(pairs, max_operation_bytes=BIG, **kw)
    assert caught.value.code == 'invalid_change_map_input'


def test_shape_mismatch_and_bad_windows_raise():
    shape = (60, 60)
    with pytest.raises(NumericContractError) as caught:
        change_maps([MaskPair('c', 'M1', np.zeros((59, 60), bool), np.zeros(shape, bool))], max_operation_bytes=BIG,
                    **kwargs(shape))
    assert caught.value.code == 'invalid_change_map_input'
    with pytest.raises(NumericContractError) as caught:
        window_candidates({'M1': np.zeros((50, 50), np.uint32)}, np.ones(shape, bool),
                          union_change={'M1': np.zeros((50, 50), bool)}, union_ref={'M1': np.zeros((50, 50), bool)},
                          union_tgt={'M1': np.zeros((50, 50), bool)}, pitch_um=PITCH, window_um=2.0,
                          max_operation_bytes=BIG)
    assert caught.value.code == 'invalid_change_map_input'
    with pytest.raises(NumericContractError) as caught:
        window_candidates({'M1': np.zeros(shape, np.uint32)}, np.ones(shape, bool),
                          union_change={'M1': np.zeros(shape, bool)}, union_ref={'M1': np.zeros(shape, bool)},
                          union_tgt={'M1': np.zeros(shape, bool)}, pitch_um=PITCH, window_um=10.0,
                          max_operation_bytes=BIG)
    assert caught.value.code == 'invalid_change_map_input'          # 창이 frame 보다 크다


def test_counter_and_integral_overflow_are_explicit(monkeypatch):
    shape = (40, 40)
    pairs = [MaskPair(f'c{i}', 'M1', np.zeros(shape, bool), np.ones(shape, bool)) for i in range(3)]
    monkeypatch.setattr(change_map, 'COUNTER_LIMIT', 3)
    with pytest.raises(NumericContractError) as caught:
        change_maps(pairs, max_operation_bytes=BIG, **kwargs(shape))
    assert caught.value.code == 'change_map_counter_overflow'
    monkeypatch.setattr(change_map, 'COUNTER_LIMIT', 4)
    out = change_maps(pairs, max_operation_bytes=BIG, **kwargs(shape))
    assert int(out['maps']['M1'].max()) == 3
    monkeypatch.setattr(change_map, 'INTEGRAL_LIMIT', 3 * 40 * 40)
    with pytest.raises(NumericContractError) as caught:
        window_candidates(out['maps'], out['D'], **union_kw(out), pitch_um=PITCH, window_um=1.0,
                          max_operation_bytes=BIG)
    assert caught.value.code == 'change_map_counter_overflow'


def test_to_region_infos_feed_merge_regions_with_scalar_scores():
    shape = (300, 300)
    a = np.zeros(shape, bool)
    b = np.zeros(shape, bool)
    b[40:60, 40:60] = True
    b[200:230, 200:230] = True
    maps, found = run([MaskPair('c', 'M1', a, b)], **kwargs(shape))
    infos = to_region_infos(found['candidates'], 'P', 'v1', 'frame1')
    assert infos and all(isinstance(i.score, float) and i.area_um2 == i.score for i in infos)
    assert all(i.region_id.startswith('chg_') and len(i.region_id) == 4 + 16 for i in infos)
    assert len({i.region_id for i in infos}) == len(infos)
    assert all(i.group == ('P', 'v1', 'frame1') for i in infos)
    merged = merge_regions(infos, 5.0, 0.5, rank_key=change_map.region_rank_key)
    best = merged[0]['representative']
    assert best.score == max(i.score for i in infos)
    assert len(merged) == 2


# --- change-roi/T6: T5 review follow-ups ----------------------------------------------------------------------------

def _full_frame_oracle(pairs, ref_valid, tgt_valid, offset, tolerance_px):
    """adv:7 oracle: the residual of every pair computed on the whole frame, no bounding-box shortcut."""
    dx, dy = offset
    height, width = ref_valid.shape
    domain = change_map._erode(change_map._comparison_domain(ref_valid, tgt_valid, dy, dx), tolerance_px)
    maps, union = {}, {}
    for pair in pairs:
        a, b = pair.load()
        shifted_b = change_map._shift_crop(b, dy, dx, 0, height, 0, width)
        res = change_map._residual(a, shifted_b, domain, tolerance_px)
        maps.setdefault(pair.layer, np.zeros(ref_valid.shape, np.uint32))
        maps[pair.layer] += res
        u_a, u_b = union.setdefault(pair.layer, [np.zeros(ref_valid.shape, bool), np.zeros(ref_valid.shape, bool)])
        u_a |= a
        u_b |= shifted_b
    union = {layer: (u_a, u_b, change_map._residual(u_a, u_b, domain, tolerance_px))
             for layer, (u_a, u_b) in union.items()}
    return domain, maps, union


@pytest.mark.parametrize('seed, offset, tolerance_um', [(1, (0, 0), 0.0), (2, (7, -5), 0.2), (3, (-9, 4), 0.3),
                                                        (4, (0, 3), 0.1), (5, (30, -25), 0.2)])
def test_bbox_limited_residual_is_bit_identical_to_the_full_frame_computation(seed, offset, tolerance_um):
    rng = np.random.default_rng(seed)
    ref_shape, tgt_shape = (150, 160), (170, 140)
    ref_valid, tgt_valid = np.ones(ref_shape, bool), np.ones(tgt_shape, bool)
    ref_valid[20:40, 30:60] = False
    tgt_valid[100:130, 10:50] = False
    pairs = []
    for i in range(7):
        a, b = np.zeros(ref_shape, bool), np.zeros(tgt_shape, bool)
        if i % 4 != 0:                                           # 어떤 쌍은 한쪽이 완전히 비어 있다
            a[rng.integers(0, 150, 40), rng.integers(0, 160, 40)] = True
            a[0:3, 0:3] = True                                   # frame 모서리에 닿는 형상
        if i % 4 != 1:
            b[rng.integers(0, 170, 40), rng.integers(0, 140, 40)] = True
            b[-3:, -3:] = True
        pairs.append(MaskPair(f'c{i}', 'M1' if i % 2 else 'M2', a, b))
    pairs.append(MaskPair('empty', 'M1', np.zeros(ref_shape, bool), np.zeros(tgt_shape, bool)))
    kw = kwargs(ref_shape, tgt_shape, offset, tolerance_um, ref_valid, tgt_valid)
    out = change_maps(pairs, max_operation_bytes=BIG, **kw)
    domain, want, want_union = _full_frame_oracle(pairs, ref_valid, tgt_valid, offset, out['stats']['tolerance_px'])
    assert np.array_equal(out['D'], domain)
    assert sorted(out['maps']) == sorted(want)
    for layer in want:
        assert out['maps'][layer].dtype == np.uint32 and np.array_equal(out['maps'][layer], want[layer])
        u_ref, u_tgt, u_change = want_union[layer]          # the union maps are bit-identical to the full-frame ones
        assert np.array_equal(out['union_ref'][layer], u_ref) and np.array_equal(out['union_tgt'][layer], u_tgt)
        assert np.array_equal(out['union_change'][layer], u_change)
        assert out['stats']['union_change_pixels_by_layer'][layer] == int(u_change.sum())
    assert out['stats']['residual_pixels'] == sum(int(m.sum()) for m in want.values())


def test_thin_shape_one_pixel_off_at_a_small_target_frame_border_scores_zero():
    shape = (100, 100)
    tgt_valid = np.ones(shape, bool)
    tgt_valid[:, 90:] = False                     # 작은 target frame: 열 90 부터는 관측 불가
    a, b = np.zeros(shape, bool), np.zeros(shape, bool)
    a[20:60, 89] = True                           # reference 의 얇은 선은 D 의 마지막 열에 있다
    # target 의 같은 선은 한 칸 어긋난 열 90(관측 밖)에 있어 target 마스크에는 보이지 않는다(b 는 비어 있다)
    kw = kwargs(shape, tgt_valid=tgt_valid)
    quiet = change_maps([MaskPair('c', 'M1', a, b)], max_operation_bytes=BIG, **kw)
    assert int(quiet['maps']['M1'].sum()) == 0     # erode(D, r) 가 D 경계 r px 띠의 거짓 잔차를 없앤다
    assert quiet['stats']['domain_pixels'] < quiet['stats']['domain_pixels_before_erosion']
    exact = change_maps([MaskPair('c', 'M1', a, b)], max_operation_bytes=BIG, **{**kw, 'tolerance_um': 0.0})
    assert int(exact['maps']['M1'].sum()) == 40    # r=0 이면 기존 계산과 같다
    # 경계에서 먼 진짜 변경은 그대로 남는다
    b2 = b.copy()
    b2[10:20, 10:20] = True
    kept = change_maps([MaskPair('c', 'M1', a, b2)], max_operation_bytes=BIG, **kw)
    assert int(kept['maps']['M1'].sum()) == 100
    assert quiet['D'].sum() == (quiet['stats']['domain_pixels'])
    found = window_candidates(kept['maps'], kept['D'], **union_kw(kept), pitch_um=PITCH, window_um=2.0,
                              max_operation_bytes=BIG)
    assert found['candidates'] and all(c['observed_fraction'] <= 1 for c in found['candidates'])


def _fake_dataset(product, entries, **context):
    """entries: (tag, failure, e1, e2, layer, extra) — a lazily-read dataset like pair_cases expects."""
    class Records(Mapping):
        def __init__(self, reasons, status):
            self.reasons, self.status = reasons, status

        def __iter__(self):
            return iter([5.0])

        def __len__(self):
            return 1

        def header(self, diameter):
            return SimpleNamespace(input_status=self.status, reasons=tuple(self.reasons))

        def __getitem__(self, diameter):
            return SimpleNamespace(mask=np.zeros((4, 4), bool))

    ids = dict(process_step_id='S', exposure_id='E', unit_id='U', source_contract_version='v1')
    ids.update(context)
    cases = []
    for tag, failure, e1, e2, layer, reasons in entries:
        cases.append(SimpleNamespace(
            identity=SimpleNamespace(product_id=product, design_version=product + '_v', case_id=tag, **ids),
            failure_type=failure,
            endpoints=(SimpleNamespace(electrode_id=e1, actual_layer='M1'), SimpleNamespace(electrode_id=e2, actual_layer='M1')),
            analysis_layer=layer, records=Records(reasons, 'observed')))
    return SimpleNamespace(cases=cases)


def test_pair_flags_other_side_duplicate_short_only_and_context_warning():
    ref = _fake_dataset('R', [('a', 'lateral_short', 'E1', 'E2', 'M1', ('csv_area_mismatch',)),
                             ('b', 'lateral_short', 'E3', 'E4', 'M1', ()),
                             ('c', 'open', 'E5', 'E6', 'M1', ()),
                             ('d', 'lateral_short', 'E7', 'E8', 'M1', ())])
    tgt = _fake_dataset('T', [('a', 'lateral_short', 'E1', 'E2', 'M1', ('csv_area_mismatch',)),
                             ('b', 'lateral_short', 'E3', 'E4', 'M1', ()), ('b2', 'lateral_short', 'E4', 'E3', 'M1', ()),
                             ('c', 'open', 'E5', 'E6', 'M1', ()),
                             ('d', 'lateral_short', 'E7', 'E8', 'M1', ())], exposure_id='E-other')
    out = pair_cases(ref, tgt, diameter_um=5.0)
    flags = {p.case_key: p.flags for p in out['pairs']}
    assert flags['lateral_short|E1|M1|E2|M1|M1'] == ('reference:csv_area_mismatch', 'target:csv_area_mismatch')
    assert flags['lateral_short|E7|M1|E8|M1|M1'] == ()
    assert 'lateral_short|E3|M1|E4|M1|M1' not in flags                       # 중복이 있는 키는 쌍이 아니다
    reasons = {(e['case_key'], e['side'], e['reason']) for e in out['cases_excluded']}
    assert ('lateral_short|E3|M1|E4|M1|M1', 'target', 'duplicate_case_key') in reasons
    assert ('lateral_short|E3|M1|E4|M1|M1', 'reference', 'duplicate_case_key_on_other_side') in reasons
    assert ('open|E5|M1|E6|M1|M1', 'reference', 'map_requires_short_only') in reasons
    assert ('open|E5|M1|E6|M1|M1', 'target', 'map_requires_short_only') in reasons
    assert out['warnings'] == ['change_map_dataset_context_differs']
    assert out['stats']['dataset_context']['target']['exposure_id'] == ['E-other']
    assert out['stats']['dataset_context']['reference']['exposure_id'] == ['E']
    same = pair_cases(ref, _fake_dataset('T', [('a', 'lateral_short', 'E1', 'E2', 'M1', ())]), diameter_um=5.0)
    assert same['warnings'] == []


def test_pair_flags_reach_the_contributing_cases():
    shape = (120, 120)
    a, b = np.zeros(shape, bool), np.zeros(shape, bool)
    b[40:50, 40:50] = True
    pair = MaskPair('c', 'M1', a, b, flags=('reference:csv_area_mismatch',))
    plain = MaskPair('d', 'M1', a.copy(), b.copy())
    kw = kwargs(shape)
    maps, found = run([pair, plain], **kw)
    got = case_contributions([pair, plain], found['candidates'][:1], max_operation_bytes=BIG, **kw)
    cases = {c['case_key']: c for c in got['windows'][0]['contributing_cases']}
    assert cases['c']['flags'] == ['reference:csv_area_mismatch'] and cases['d']['flags'] == []


def _only_window(pairs, **kw):
    maps, found = run(pairs, **kw)
    return case_contributions(pairs, found['candidates'][:1], max_operation_bytes=BIG, **kw)['windows'][0]['contributing_cases']


def test_case_areas_equal_classify_pixel_counts_times_pitch_squared():
    shape = (300, 300)
    kw = kwargs(shape)
    # shifted: ref 20x10 = 200 px, tgt 20x10 = 200 px  -> 200 * 0.1 * 0.1 = 2.0 um2 each
    shifted = MaskPair('shift', 'M1', rect(shape, 40, 40, 20, 10), rect(shape, 40, 48, 20, 10))
    # reshaped (other place): ref 20x20 = 400 px -> 4.0 um2, tgt 20x36 = 720 px -> 7.2 um2
    reshaped = MaskPair('reshape', 'M1', rect(shape, 40, 200, 20, 20), rect(shape, 40, 200, 20, 36))
    for pair, ref_um2, tgt_um2, classification in ((shifted, 2.0, 2.0, 'shifted'), (reshaped, 4.0, 7.2, 'reshaped')):
        got = _only_window([pair], **kw)
        assert len(got) == 1
        assert got[0]['classification'] == classification, got[0]
        assert got[0]['area_reference_um2'] == pytest.approx(ref_um2) and got[0]['area_target_um2'] == pytest.approx(tgt_um2)


def test_case_area_of_the_missing_side_is_zero_for_appeared_and_disappeared():
    shape = (200, 200)
    kw = kwargs(shape)
    zero = np.zeros(shape, bool)
    appeared = _only_window([MaskPair('a', 'M1', zero, rect(shape, 50, 50, 15, 15))], **kw)[0]
    gone = _only_window([MaskPair('v', 'M1', rect(shape, 50, 50, 15, 15), zero)], **kw)[0]
    assert appeared['classification'] == 'appeared' and gone['classification'] == 'disappeared'
    assert appeared['area_reference_um2'] == 0 and appeared['area_target_um2'] == pytest.approx(2.25)   # 225 px * 0.01
    assert gone['area_target_um2'] == 0 and gone['area_reference_um2'] == pytest.approx(2.25)
    assert isinstance(appeared['area_reference_um2'], (int, float)) and appeared['area_reference_um2'] is not None


def test_case_area_leaves_out_hotspot_outside_the_comparison_domain():
    shape = (300, 300)
    ref_valid = np.ones(shape, bool)
    ref_valid[:, :50] = False                    # the left part of the hotspot lies on invalid reference area
    kw = kwargs(shape, ref_valid=ref_valid)
    a = rect(shape, 40, 40, 20, 20) | rect(shape, 140, 120, 5, 5)
    b = rect(shape, 40, 40, 20, 20) | rect(shape, 140, 120, 5, 5) | rect(shape, 40, 70, 20, 10)
    got = _only_window([MaskPair('c', 'M1', a, b)], **kw)
    assert len(got) == 1
    full_um2 = 20 * 20 * PITCH * PITCH
    # a at columns 40..59, valid from column 50, eroded by r = 2 px -> columns 52..59 count: 8 * 20 px = 1.6 um2
    assert got[0]['area_reference_um2'] == pytest.approx(1.6) and got[0]['area_reference_um2'] < full_um2
    assert got[0]['area_target_um2'] > got[0]['area_reference_um2']


def test_case_areas_survive_a_different_target_frame_size():
    ref_shape, tgt_shape = (300, 300), (320, 310)
    kw = kwargs(ref_shape, tgt_shape, offset=(3, 2))
    a = np.zeros(ref_shape, bool)
    b = np.zeros(tgt_shape, bool)
    b[100:110, 100:110] = True
    got = _only_window([MaskPair('c', 'M1', a, b)], **kw)
    assert got[0]['area_reference_um2'] == 0 and got[0]['area_target_um2'] == pytest.approx(1.0)   # 100 px * 0.01


def test_case_contributions_admission_is_the_first_stage_only_and_does_not_load_masks():
    shape = (120, 130)
    kw = kwargs(shape)
    pair = MaskPair('c', 'M1', np.zeros(shape, bool), np.ones(shape, bool))
    maps, found = run([pair], **kw)
    window = found['candidates'][:1]
    need = estimate_change_map_bytes(shape, shape, 1, 0, 2)
    assert case_contributions([pair], window, max_operation_bytes=need, **kw)['stats']['estimated_bytes'] == need
    with pytest.raises(NumericContractError) as caught:
        case_contributions([pair], window, max_operation_bytes=need - 1, **kw)
    assert caught.value.code == 'oversized_real_operation'

    class Boom:
        case_key, layer = 'c', 'M1'

        def load(self):
            raise AssertionError('mask loaded before admission')

    with pytest.raises(NumericContractError):
        case_contributions([Boom()], window, max_operation_bytes=10, **kw)


def test_required_operation_limit_has_no_default():
    shape = (60, 60)
    maps = change_maps([MaskPair('c', 'M1', np.zeros(shape, bool), np.ones(shape, bool))], max_operation_bytes=BIG,
                       **kwargs(shape))
    with pytest.raises(TypeError):
        window_candidates(maps['maps'], maps['D'], pitch_um=PITCH, window_um=2.0)
    with pytest.raises(TypeError):
        case_contributions([], [], **kwargs(shape))


def test_previous_pair_arrays_are_released_before_the_next_pair_is_read():
    import gc
    import weakref
    shape = (80, 80)
    refs = []

    class Probe:
        layer = 'M1'

        def __init__(self, key):
            self.case_key = key

        def load(self):
            if refs:                              # 이전 쌍의 배열이 아직 살아 있으면 안 된다
                gc.collect()
                assert all(r() is None for r in refs), 'previous pair arrays still referenced'
            a, b = np.zeros(shape, bool), np.zeros(shape, bool)
            b[10:20, 10:20] = True
            refs.extend([weakref.ref(a), weakref.ref(b)])
            return a, b

    kw = kwargs(shape)
    pairs = [Probe('p0'), Probe('p1'), Probe('p2')]
    out = change_maps(pairs, max_operation_bytes=BIG, **kw)
    assert out['stats']['pairs_with_change_by_layer'] == {'M1': 3}
    refs.clear()
    found = window_candidates(out['maps'], out['D'], **union_kw(out), pitch_um=PITCH, window_um=2.0,
                              max_operation_bytes=BIG)
    got = case_contributions(pairs, found['candidates'][:1], max_operation_bytes=BIG, **kw)
    assert [c['case_key'] for c in got['windows'][0]['contributing_cases']] == ['p0', 'p1', 'p2']


# --- change-roi/T7b: the rank score is the per-layer union change -----------------------------------------------------

def _top_representative(found, window_um=5.0):
    infos = to_region_infos(found['candidates'], 'P', 'v1', 'f')
    merged = merge_regions(infos, window_um, 0.5, rank_key=change_map.region_rank_key)
    by_id = {region_id_for(c['layer'], c['center_xy_um']): c for c in found['candidates']}
    return [by_id[m['representative'].region_id] for m in merged]


def test_dense_centre_with_small_union_change_ranks_below_one_large_move_at_the_edge():
    shape = (300, 300)
    # centre: 10 pairs, every one pushed 3 px right (> tolerance 2 px): 10 x 40 residual cells, union change only 40 cells
    pairs = [MaskPair(f'centre{i}', 'M1', rect(shape, 140, 140, 20, 20), rect(shape, 140, 143, 20, 20))
             for i in range(10)]
    # edge: one pair moves a 10 x 10 block by 20 px: union change 200 cells, residual sum 200
    pairs.append(MaskPair('edge', 'M1', rect(shape, 60, 60, 10, 10), rect(shape, 60, 80, 10, 10)))
    maps, found = run(pairs, **kwargs(shape))
    ranked = _top_representative(found)
    assert len(ranked) == 2
    edge, centre = ranked
    assert edge['center_xy_um'][0] < 10.0 and centre['center_xy_um'][0] > 10.0       # the edge block ranks first
    assert edge['score_um2'] == pytest.approx(200 * PITCH ** 2) and edge['score_basis'] == 'union_change'
    assert centre['score_um2'] == pytest.approx(40 * PITCH ** 2)
    assert centre['sum_residual_um2'] == pytest.approx(400 * PITCH ** 2)
    assert centre['sum_residual_um2'] > edge['sum_residual_um2']
    assert edge['sum_residual_um2'] == pytest.approx(200 * PITCH ** 2)
    assert centre['flags'] == [] and edge['flags'] == []
    # the per-pair sum stays in the map: 10 pairs x 40 cells (+ the edge pair's 200)
    assert int(maps['maps']['M1'].sum()) == 10 * 40 + 200
    assert [c['score_um2'] for c in found['candidates']] == sorted((c['score_um2'] for c in found['candidates']),
                                                                   reverse=True)


def test_union_unchanged_while_a_pair_moved_scores_zero_keeps_flag_and_ranks_last():
    shape = (300, 300)
    p, q = (40, 40), (200, 200)
    pairs = [MaskPair('Z', 'M1', rect(shape, p[0], p[1], 15, 15), rect(shape, p[0], p[1], 15, 15)),    # covers P always
             MaskPair('Y', 'M1', rect(shape, q[0], q[1], 15, 15), rect(shape, q[0], q[1], 15, 15)),    # covers Q always
             MaskPair('X', 'M1', rect(shape, p[0], p[1], 15, 15), rect(shape, q[0], q[1], 15, 15)),    # P -> Q
             MaskPair('W', 'M2', np.zeros(shape, bool), rect(shape, 100, 100, 12, 12))]               # a real union change
    maps, found = run(pairs, **kwargs(shape))
    assert not maps['union_change']['M1'].any()
    ranked = _top_representative(found)
    assert [c['layer'] for c in ranked] == ['M2', 'M1', 'M1']
    assert ranked[0]['score_um2'] > 0 and ranked[0]['flags'] == []
    for item in ranked[1:]:
        assert item['score_um2'] == 0 and item['sum_residual_um2'] > 0
        assert item['flags'] == ['union_unchanged_pair_changed']
        assert item['union_delta_um2'] == 0 and item['union_area_reference_um2'] == item['union_area_target_um2'] > 0
    contrib = case_contributions(pairs, ranked[1:], max_operation_bytes=BIG, **kwargs(shape))['windows']
    kinds = [{c['case_key']: c['classification'] for c in w['contributing_cases']} for w in contrib]
    assert sorted(kinds, key=lambda k: sorted(k.values())) == [{'X': 'appeared'}, {'X': 'disappeared'}]


def test_union_delta_is_positive_when_the_target_gains_hotspots():
    shape = (200, 200)
    grow = [MaskPair('g', 'M1', np.zeros(shape, bool), rect(shape, 80, 80, 10, 10))]
    shrink = [MaskPair('s', 'M1', rect(shape, 80, 80, 10, 10), np.zeros(shape, bool))]
    gained = run(grow, **kwargs(shape))[1]['candidates'][0]
    lost = run(shrink, **kwargs(shape))[1]['candidates'][0]
    assert gained['union_delta_um2'] == pytest.approx(100 * PITCH ** 2) and gained['union_delta_um2'] > 0
    assert gained['union_area_reference_um2'] == 0 and gained['union_area_target_um2'] == pytest.approx(100 * PITCH ** 2)
    assert lost['union_delta_um2'] == pytest.approx(-100 * PITCH ** 2) and lost['union_delta_um2'] < 0


def test_zero_tolerance_union_change_is_the_xor_of_the_unions_inside_the_domain():
    rng = np.random.default_rng(11)
    shape = (120, 130)
    valid = np.ones(shape, bool)
    valid[30:50, 40:60] = False
    pairs = [MaskPair(f'c{i}', 'M1' if i % 2 else 'M2', rng.random(shape) < 0.05, rng.random(shape) < 0.05)
             for i in range(6)]
    out = change_maps(pairs, max_operation_bytes=BIG, **kwargs(shape, ref_valid=valid, tolerance=0.0))
    for layer in ('M1', 'M2'):
        u_a = np.any([p.a for p in pairs if p.layer == layer], axis=0)
        u_b = np.any([p.b for p in pairs if p.layer == layer], axis=0)
        assert np.array_equal(out['union_ref'][layer], u_a) and np.array_equal(out['union_tgt'][layer], u_b)
        assert np.array_equal(out['union_change'][layer], (u_a ^ u_b) & out['D'])
        assert out['union_change'][layer].dtype == bool


def test_window_candidates_rejects_union_maps_that_do_not_match_the_maps():
    shape = (60, 60)
    maps = {'M1': np.zeros(shape, np.uint32)}
    flat = {'M1': np.zeros(shape, bool)}
    with pytest.raises(NumericContractError):
        window_candidates(maps, np.ones(shape, bool), union_change={}, union_ref=flat, union_tgt=flat,
                          pitch_um=PITCH, window_um=2.0, max_operation_bytes=BIG)


# --- change-roi/T8b: combinations that only one model has go into that model's union only -------------------------------

def _one_sided_pair_sets():
    ref = _fake_dataset('R', [('a', 'lateral_short', 'E1', 'E2', 'M1', ()),
                             ('x', 'lateral_short', 'E3', 'E4', 'M1', ('csv_area_mismatch',)),      # only the reference has it
                             ('g', 'lateral_short', 'E9', 'E10', 'M1', ()),                           # layer M1 here, M2 in the target
                             ('d', 'lateral_short', 'E11', 'E12', 'M1', ()),                          # E12 is not in the target
                             ('u', 'lateral_short', 'E13', 'E14', 'M1', ()), ('u2', 'lateral_short', 'E14', 'E13', 'M1', ())])
    tgt = _fake_dataset('T', [('a', 'lateral_short', 'E1', 'E2', 'M1', ()),
                             ('y', 'lateral_short', 'E5', 'E6', 'M2', ()),                            # only the target has it
                             ('g', 'lateral_short', 'E9', 'E10', 'M2', ()),
                             ('n', 'lateral_short', 'E7', 'E99', 'M1', ())])                          # E99 is not in the reference
    electrodes = dict(ref_electrodes=['E1', 'E10', 'E11', 'E12', 'E13', 'E14', 'E2', 'E3', 'E4', 'E5', 'E6', 'E7', 'E9'],
                      tgt_electrodes=['E1', 'E10', 'E11', 'E13', 'E14', 'E2', 'E3', 'E4', 'E5', 'E6', 'E7', 'E9', 'E99'])
    return ref, tgt, electrodes


def test_pair_cases_includes_one_sided_layer_changed_and_new_or_removed_electrode_cases():
    """An electrode only one model has is still compared: the combination is a one-sided case with a flag, never excluded."""
    ref, tgt, electrodes = _one_sided_pair_sets()
    out = pair_cases(ref, tgt, diameter_um=5.0, **electrodes)
    got = {p.case_key: (p.present_in, p.layer, p.layer_changed, p.flags) for p in out['pairs']}
    assert got == {
        'lateral_short|E1|M1|E2|M1|M1': ('both', 'M1', False, ()),
        'lateral_short|E3|M1|E4|M1|M1': ('reference', 'M1', False, ('one_sided_case', 'reference:csv_area_mismatch')),
        'lateral_short|E5|M1|E6|M1|M2': ('target', 'M2', False, ('one_sided_case',)),
        'lateral_short|E10|M1|E9|M1|M1': ('reference', 'M1', True, ('layer_changed', 'one_sided_case')),
        'lateral_short|E10|M1|E9|M1|M2': ('target', 'M2', True, ('layer_changed', 'one_sided_case')),
        'lateral_short|E11|M1|E12|M1|M1': ('reference', 'M1', False, ('one_sided_case', 'removed_electrode')),   # E12 not in target
        'lateral_short|E7|M1|E99|M1|M1': ('target', 'M1', False, ('new_electrode', 'one_sided_case'))}          # E99 not in reference
    reasons = {(e['case_key'], e['side'], e['reason']) for e in out['cases_excluded']}
    assert ('lateral_short|E13|M1|E14|M1|M1', 'reference', 'duplicate_case_key') in reasons
    assert not any(e['reason'] in ('case_layer_mismatch', 'circuit_change_electrode_missing') for e in out['cases_excluded'])
    stats = out['stats']
    assert (stats['one_sided_included_reference'], stats['one_sided_included_target'], stats['layer_changed_included'],
            stats['new_electrode_included'], stats['removed_electrode_included']) == (3, 3, 2, 1, 1)
    assert stats['pairs'] == 7 and 'circuit_change_excluded' not in stats
    assert {e['case_key'] for e in out['cases_only_in_reference']} >= {'lateral_short|E3|M1|E4|M1|M1'}


def test_pair_cases_without_electrode_lists_keeps_the_previous_behaviour():
    ref, tgt, _ = _one_sided_pair_sets()
    out = pair_cases(ref, tgt, diameter_um=5.0)
    assert [p.case_key for p in out['pairs']] == ['lateral_short|E1|M1|E2|M1|M1']
    assert out['stats']['one_sided_included_reference'] == out['stats']['one_sided_included_target'] == 0
    assert ('lateral_short|E10|M1|E9|M1|M1', 'reference', 'case_layer_mismatch') in {
        (e['case_key'], e['side'], e['reason']) for e in out['cases_excluded']}


def test_one_sided_hotspot_enters_only_its_own_union_and_is_appeared_or_disappeared():
    shape_ref, shape_tgt = (260, 280), (270, 290)
    offset = (6, -3)                       # reference p <-> target p + (dx, dy)
    gone = rect(shape_ref, 60, 50, 14, 22)            # only the reference has it
    new = rect(shape_tgt, 150, 170, 16, 20)           # only the target has it (target pixels)
    same = rect(shape_ref, 200, 60, 10, 10)
    pairs = [MaskPair('gone', 'M1', gone, None, flags=('one_sided_case',)),
             MaskPair('new', 'M1', None, new, flags=('one_sided_case',)),
             MaskPair('both', 'M1', same, np.roll(np.pad(same, ((0, 10), (0, 10))), (-3, 6), axis=(0, 1))[:shape_tgt[0], :shape_tgt[1]])]
    kw = kwargs(shape_ref, shape_tgt, offset=offset)
    maps = change_maps(pairs, max_operation_bytes=BIG, window_um=5.0, **kw)
    r = change_map._round_px(0.2, PITCH)
    domain = maps['D']
    new_in_ref = change_map._shift_crop(new, offset[1], offset[0], 0, shape_ref[0], 0, shape_ref[1])
    # reference union holds `gone` (+ the paired case), target union holds `new` (+ the paired case); never the other way round
    assert maps['union_ref']['M1'][70, 60] and not maps['union_tgt']['M1'][70, 60]
    assert maps['union_tgt']['M1'][new_in_ref][0] and not (maps['union_ref']['M1'] & new_in_ref).any()
    # residual of a one-sided hotspot = hotspot ∩ erode(D, r): no dilation suppression because the other side is empty
    assert int(maps['maps']['M1'][gone & domain].min()) >= 1
    assert np.array_equal(maps['union_change']['M1'] & gone, gone & domain)
    assert np.array_equal(maps['union_change']['M1'] & new_in_ref, new_in_ref & domain)
    # bbox-local computation equals the whole-frame computation (explicit empty masks)
    full = change_maps([MaskPair('gone', 'M1', gone, np.zeros(shape_tgt, bool)), MaskPair('new', 'M1', np.zeros(shape_ref, bool), new),
                        pairs[2]], max_operation_bytes=BIG, window_um=5.0, **kw)
    assert np.array_equal(full['maps']['M1'], maps['maps']['M1']) and np.array_equal(full['union_change']['M1'], maps['union_change']['M1'])
    assert np.array_equal(full['union_ref']['M1'], maps['union_ref']['M1']) and np.array_equal(full['union_tgt']['M1'], maps['union_tgt']['M1'])
    assert maps['stats']['pairs'] == 3 and r == 2
    found = window_candidates(maps['maps'], maps['D'], **union_kw(maps), pitch_um=PITCH, window_um=5.0, max_operation_bytes=BIG)
    top = found['candidates']
    gains = [c for c in top if c['union_delta_um2'] > 0]
    losses = [c for c in top if c['union_delta_um2'] < 0]
    assert gains and losses and gains[0]['score_um2'] == pytest.approx(new_in_ref[domain].sum() * PITCH ** 2)
    assert losses[0]['union_delta_um2'] == pytest.approx(-gone[domain].sum() * PITCH ** 2)
    contrib = case_contributions(pairs, [gains[0], losses[0]], max_operation_bytes=BIG, **kw)['windows']
    kinds = [{c['case_key']: (c['classification'], c['flags']) for c in w['contributing_cases']} for w in contrib]
    assert kinds == [{'new': ('appeared', ['one_sided_case'])}, {'gone': ('disappeared', ['one_sided_case'])}]


def test_layer_changed_pair_is_a_disappeared_in_one_layer_and_an_appeared_in_the_other():
    shape = (200, 200)
    block = rect(shape, 80, 80, 12, 12)
    pairs = [MaskPair('k|L1', 'M1', block, None, flags=('layer_changed', 'one_sided_case'), layer_changed=True),
             MaskPair('k|L2', 'M2', None, block, flags=('layer_changed', 'one_sided_case'), layer_changed=True)]
    kw = kwargs(shape)
    maps, found = run(pairs, **kw)
    by_layer = {c['layer']: c for c in found['candidates']}
    assert set(by_layer) == {'M1', 'M2'}
    assert by_layer['M1']['union_delta_um2'] < 0 < by_layer['M2']['union_delta_um2']
    contrib = case_contributions(pairs, [by_layer['M1'], by_layer['M2']], max_operation_bytes=BIG, **kw)['windows']
    assert [(w['layer'], w['contributing_cases'][0]['classification'], w['contributing_cases'][0]['flags']) for w in contrib] == [
        ('M1', 'disappeared', ['layer_changed', 'one_sided_case']), ('M2', 'appeared', ['layer_changed', 'one_sided_case'])]
    assert pairs[0].present_in == 'reference' and pairs[1].present_in == 'target' and pairs[0].layer_changed


@pytest.mark.skipif(not os.environ.get('SIMCOMPARE_ETX_PY'), reason='SIMCOMPARE_ETX_PY not set')
def test_python39_result_is_identical():
    py = os.path.expanduser(os.environ['SIMCOMPARE_ETX_PY'])
    code = (f'import sys\nsys.path.insert(0, {SRC!r})\n' + CASE_SRC +
            '\nimport json\nprint(json.dumps(representative_case(), sort_keys=True))\n')
    done = subprocess.run([py, '-c', code], capture_output=True, text=True, check=True, timeout=600)
    assert done.stdout.strip() == json.dumps(representative_case(), sort_keys=True)
    assert json.loads(done.stdout)['candidates']
