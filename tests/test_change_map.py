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
    found = window_candidates(maps['maps'], maps['D'], pitch_um=0.1, window_um=5.0, max_operation_bytes=10 ** 12)
    top = found['candidates'][:6]
    contrib = case_contributions(pairs, top, max_operation_bytes=10 ** 12, **kw)
    return {'stats': maps['stats'], 'candidates': top, 'contributions': contrib['windows'],
            'sums': {k: int(v.sum()) for k, v in sorted(maps['maps'].items())}}
'''
_ns = {}
exec(CASE_SRC, _ns)
rect = _ns['rect']
representative_case = _ns['representative_case']


def kwargs(shape_ref, shape_tgt=None, offset=(0, 0), tolerance=0.2, ref_valid=None, tgt_valid=None):
    return dict(ref_valid=np.ones(shape_ref, bool) if ref_valid is None else ref_valid,
                tgt_valid=np.ones(shape_tgt or shape_ref, bool) if tgt_valid is None else tgt_valid,
                offset_px=offset, pitch_um=PITCH, tolerance_um=tolerance)


def run(pairs, window_um=5.0, **kw):
    maps = change_maps(pairs, max_operation_bytes=BIG, window_um=window_um, **kw)
    found = window_candidates(maps['maps'], maps['D'], pitch_um=PITCH, window_um=window_um, max_operation_bytes=BIG)
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
    result = case_contributions(pairs, list(chosen.values()), **kw)
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
    shifted = window_candidates(maps['maps'], maps['D'], pitch_um=PITCH, window_um=5.0, origin_xy_um=(50.0, 70.0))
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
    assert case_contributions(pairs, top, **kw) == case_contributions(pairs, top, **kw)
    json.dumps(case_contributions(pairs, top, **kw), sort_keys=True, allow_nan=False)
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
        got = case_contributions([pair], [cand], **kw)['windows'][0]['contributing_cases']
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
    assert window_candidates(ok['maps'], ok['D'], pitch_um=PITCH, window_um=5.0, max_operation_bytes=window_need)
    with pytest.raises(NumericContractError) as caught:
        window_candidates(ok['maps'], ok['D'], pitch_um=PITCH, window_um=5.0, max_operation_bytes=window_need - 1)
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
        window_candidates({'M1': np.zeros((50, 50), np.uint32)}, np.ones(shape, bool), pitch_um=PITCH, window_um=2.0)
    assert caught.value.code == 'invalid_change_map_input'
    with pytest.raises(NumericContractError) as caught:
        window_candidates({'M1': np.zeros(shape, np.uint32)}, np.ones(shape, bool), pitch_um=PITCH, window_um=10.0)
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
        window_candidates(out['maps'], out['D'], pitch_um=PITCH, window_um=1.0)
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


@pytest.mark.skipif(not os.environ.get('SIMCOMPARE_ETX_PY'), reason='SIMCOMPARE_ETX_PY not set')
def test_python39_result_is_identical():
    py = os.path.expanduser(os.environ['SIMCOMPARE_ETX_PY'])
    code = (f'import sys\nsys.path.insert(0, {SRC!r})\n' + CASE_SRC +
            '\nimport json\nprint(json.dumps(representative_case(), sort_keys=True))\n')
    done = subprocess.run([py, '-c', code], capture_output=True, text=True, check=True, timeout=600)
    assert done.stdout.strip() == json.dumps(representative_case(), sort_keys=True)
    assert json.loads(done.stdout)['candidates']
