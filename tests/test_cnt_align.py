"""change-roi/T4: CNT 정렬 모듈(합성 입력). 부호: p_target = p_reference + offset."""
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from simcompare import cnt_align
from simcompare.adapters.electrode_png import load_cnt_layers
from simcompare.cnt_align import align_products, estimate_alignment_bytes
from simcompare.numeric_schema import NumericContractError

SRC = str(Path(__file__).resolve().parents[1] / 'src')
BIG = 10 ** 11
KW = dict(pitch_um=0.1, search_um=10.0, min_dice=0.3, layer_tolerance_um=0.5, max_operation_bytes=BIG)

# 3.9 인터프리터 subprocess 에서도 그대로 쓰는 자기완결 사례 생성기(numpy 와 simcompare 만 쓴다).
CASE_SRC = '''
import numpy as np


def random_world(seed, shape=(900, 900), density=0.012, layers=3):
    rng = np.random.default_rng(seed)
    return [rng.random(shape) < density for _ in range(layers)]


def cut(world, y0, x0, h, w):
    return [layer[y0:y0 + h, x0:x0 + w].copy() for layer in world]


def identifiable_case():
    world = random_world(7)
    dx, dy = 13, -7
    ref0, shape_r, shape_t = (150, 160), (400, 360), (380, 420)
    tgt0 = (ref0[0] - dy, ref0[1] - dx)
    ref = {'layers': dict(zip(('M1', 'M2', 'V'), cut(world, ref0[0], ref0[1], *shape_r))),
           'valid': np.ones(shape_r, bool)}
    tgt = {'layers': dict(zip(('M1', 'M2', 'V'), cut(world, tgt0[0], tgt0[1], *shape_t))),
           'valid': np.ones(shape_t, bool)}
    return ref, tgt, dict(pitch_um=0.1, search_um=10.0, min_dice=0.3, layer_tolerance_um=0.5,
                          max_operation_bytes=10 ** 11), (dx, dy)


def ambiguous_case():
    ref_a = np.zeros((25, 25), bool)
    tgt_a = np.zeros((25, 25), bool)
    for y in (5, 15):
        for x in (8, 18):
            ref_a[y, x] = True
        for x in (4, 14, 24):
            tgt_a[y, x] = True
    ref = {'layers': {'M1': ref_a}, 'valid': np.ones((25, 25), bool)}
    tgt = {'layers': {'M1': tgt_a}, 'valid': np.ones((25, 25), bool)}
    return ref, tgt, dict(pitch_um=1.0, search_um=8.0, min_dice=0.0, layer_tolerance_um=0.5,
                          max_operation_bytes=10 ** 11), (6, 0)
'''
_ns = {}
exec(CASE_SRC, _ns)
random_world, cut = _ns['random_world'], _ns['cut']
identifiable_case, ambiguous_case = _ns['identifiable_case'], _ns['ambiguous_case']


def pair(world, ref0, tgt0, shape_r, shape_t, names=('M1', 'M2', 'V')):
    ref = {'layers': dict(zip(names, cut(world, ref0[0], ref0[1], *shape_r))), 'valid': np.ones(shape_r, bool)}
    tgt = {'layers': dict(zip(names, cut(world, tgt0[0], tgt0[1], *shape_t))), 'valid': np.ones(shape_t, bool)}
    return ref, tgt


def tgt_origin(ref0, dx, dy):
    """target = reference + offset 이므로 tgt 원점 = ref 원점 - offset."""
    return ref0[0] - dy, ref0[1] - dx


@pytest.mark.parametrize('dx,dy', [(13, -7), (0, 0), (-40, 25), (-98, 3), (5, 97), (99, 0), (-60, -60)])
def test_identifiable_shifts_are_recovered_exactly(dx, dy):
    world = random_world(11)
    ref0 = (200, 200)
    ref, tgt = pair(world, ref0, tgt_origin(ref0, dx, dy), (400, 360), (380, 420))
    out = align_products(ref, tgt, **KW)
    assert out['offset_xy_px'] == [dx, dy]
    assert out['offset_xy_um'] == [round(dx * 0.1, 6), round(dy * 0.1, 6)]
    assert out['status'] == 'aligned', out['reasons']
    assert out['sign'] == 'p_target = p_reference + offset'
    assert out['layers_used'] == ['M1', 'M2', 'V']
    assert 0.3 < out['dice_best'] < 1.0
    json.dumps(out, sort_keys=True, allow_nan=False)


def test_two_by_two_repeat_with_boundary_information_gives_true_offset_not_period():
    rng = np.random.default_rng(3)
    tile = rng.random((100, 100)) < 0.03
    world = np.tile(tile, (4, 4))
    world[60:66, 70:76] = True                 # 주기를 깨는 경계 정보
    world[230:238, 310:316] = True
    dx, dy = 20, -10
    ref0 = (50, 50)
    tgt0 = tgt_origin(ref0, dx, dy)
    ref = {'layers': {'M1': world[50:350, 50:350]}, 'valid': np.ones((300, 300), bool)}
    tgt = {'layers': {'M1': world[tgt0[0]:tgt0[0] + 300, tgt0[1]:tgt0[1] + 300]}, 'valid': np.ones((300, 300), bool)}
    out = align_products(ref, tgt, **{**KW, 'search_um': 15.0})
    assert out['offset_xy_px'] == [dx, dy]
    periods = [tuple(v['vector_xy_um']) for v in out['pattern_period_vectors_um']]
    assert (10.0, 0.0) in periods and (0.0, 10.0) in periods


def test_unidentifiable_pattern_is_deterministic_ambiguous_and_lists_truth():
    ref, tgt, kw, truth = ambiguous_case()
    first = align_products(ref, tgt, **kw)
    second = align_products(ref, tgt, **kw)
    assert first == second
    assert 'peak_ambiguous' in first['reasons'] and first['status'] == 'aligned_uncertain'
    assert first['peak_ratio'] == 1.0
    assert first['offset_xy_px'] == [-4, 0]          # 동점이면 |d| 최소
    assert list(truth) in [a['offset_xy_px'] for a in first['alternatives']]
    assert len(first['alternatives']) <= 5


def test_partial_cnt_change_keeps_true_offset_with_dice_below_one():
    world = random_world(5)
    rng = np.random.default_rng(99)
    dx, dy = -22, 17
    ref0 = (180, 190)
    ref, tgt = pair(world, ref0, tgt_origin(ref0, dx, dy), (400, 400), (400, 400))
    for name, layer in tgt['layers'].items():
        ys, xs = np.nonzero(layer)
        pick = rng.permutation(len(ys))[: int(0.2 * len(ys))]
        layer[ys[pick], xs[pick]] = False           # 삭제
        layer[rng.integers(0, 400, len(pick)), rng.integers(0, 400, len(pick))] = True   # 생성
    out = align_products(ref, tgt, **KW)
    assert out['offset_xy_px'] == [dx, dy]
    assert out['dice_best'] < 1.0


def test_layer_moving_alone_is_flagged_but_result_is_best_effort():
    world = random_world(21)
    dx, dy = 10, 6
    ref0 = (200, 200)
    ref, tgt = pair(world, ref0, tgt_origin(ref0, dx, dy), (400, 400), (400, 400))
    odd = tgt_origin(ref0, dx + 30, dy - 12)
    tgt['layers']['V'] = cut(world, odd[0], odd[1], 400, 400)[2]
    out = align_products(ref, tgt, **KW)
    assert out['offset_xy_px'] == [dx, dy]
    assert 'layer_offsets_disagree' in out['reasons']
    assert out['status'] == 'aligned_uncertain'
    assert out['per_layer']['V']['offset_xy_um'] == [round((dx + 30) * 0.1, 6), round((dy - 12) * 0.1, 6)]


def test_valid_hole_and_support_exclusion_and_search_bound():
    world = random_world(31)
    dx, dy = 8, -5
    ref0 = (200, 200)
    ref, tgt = pair(world, ref0, tgt_origin(ref0, dx, dy), (300, 300), (300, 300))
    ref['valid'][100:200, 100:200] = False
    tgt['valid'][50:120, 200:260] = False
    out = align_products(ref, tgt, **KW)
    assert out['offset_xy_px'] == [dx, dy]
    # 지지 면적: 40 % 만 겹치는 참 이동(60 px)은 후보에서 빠진다
    base = random_world(32, shape=(160, 260), layers=1)
    ref = {'layers': {'M1': base[0][:, 0:100]}, 'valid': np.ones((160, 100), bool)}
    tgt = {'layers': {'M1': base[0][:, 60:160]}, 'valid': np.ones((160, 100), bool)}
    wide = align_products(ref, tgt, pitch_um=1.0, search_um=80.0, min_dice=0.0, layer_tolerance_um=5.0,
                          max_operation_bytes=BIG)
    assert wide['offset_xy_px'] != [-60, 0]
    assert wide['overlap_area_um2'] >= 0.5 * 160 * 100
    # 탐색 경계
    ref0 = (300, 300)
    ref, tgt = pair(random_world(33), ref0, tgt_origin(ref0, 100, 0), (300, 300), (300, 300))
    edge = align_products(ref, tgt, **KW)
    assert edge['offset_xy_px'] == [100, 0]
    assert 'search_bound_hit' in edge['reasons']


def test_correlation_has_no_wrap_against_brute_force():
    rng = np.random.default_rng(4)
    a, b = rng.random((50, 60)) < 0.2, rng.random((40, 70)) < 0.2
    r = 80
    size = (cnt_align._fft_size(50, 40, r), cnt_align._fft_size(60, 70, r))
    win = cnt_align._lags(cnt_align._spectrum(a, size), cnt_align._spectrum(b, size), size, r)
    assert size[0] >= 50 + r and size[1] >= 70 + r
    for dy, dx in [(0, 0), (-40, 55), (30, -60), (-79, 79), (79, -79), (12, 12), (-10, 20), (r, 0), (0, -r)]:
        expect = 0
        for y in range(50):
            for x in range(60):
                if a[y, x] and 0 <= y + dy < 40 and 0 <= x + dx < 70:
                    expect += int(b[y + dy, x + dx])
        assert win[dy + r, dx + r] == expect, (dy, dx)


def test_reversing_direction_negates_the_offset_and_keeps_physical_correspondence():
    world = random_world(41)
    dx, dy = 31, -18
    ref0 = (200, 200)
    ref, tgt = pair(world, ref0, tgt_origin(ref0, dx, dy), (400, 360), (380, 420))
    forward = align_products(ref, tgt, **KW)
    backward = align_products(tgt, ref, **KW)
    assert forward['offset_xy_px'] == [dx, dy]
    assert backward['offset_xy_px'] == [-dx, -dy]
    assert backward['offset_xy_um'] == [-forward['offset_xy_um'][0], -forward['offset_xy_um'][1]]
    assert forward['dice_best'] == backward['dice_best']
    # 물리 대응: ref 의 한 점이 가리키는 target 점은 양방향에서 같은 세계 좌표다
    p = np.array([120, 90])
    q = p + np.array([forward['offset_xy_px'][1], forward['offset_xy_px'][0]])
    back = q + np.array([backward['offset_xy_px'][1], backward['offset_xy_px'][0]])
    assert tuple(back) == tuple(p)


def test_failures_return_identity_with_reasons():
    ones = np.ones((60, 60), bool)
    some = np.zeros((60, 60), bool)
    some[10:14, 10:14] = True
    good = {'layers': {'M1': some}, 'valid': ones}
    kw = {**KW, 'search_um': 3.0}
    out = align_products({'layers': {}, 'valid': ones}, good, **kw)
    assert out['status'] == 'alignment_failed_identity_used' and out['reasons'] == ['cnt_missing_reference']
    assert out['offset_xy_px'] == [0, 0] and out['offset_xy_um'] == [0.0, 0.0]
    out = align_products(good, {'layers': {}, 'valid': ones}, **kw)
    assert out['reasons'] == ['cnt_missing_target']
    out = align_products({'layers': {'M1': np.zeros_like(some)}, 'valid': ones}, good, **kw)
    assert out['status'] == 'alignment_failed_identity_used'
    assert 'cnt_empty_reference_M1' in out['reasons'] and 'no_usable_layers' in out['reasons']
    out = align_products(good, {'layers': {'M9': some}, 'valid': ones}, **kw)
    assert {'cnt_layer_set_mismatch', 'no_usable_layers'} <= set(out['reasons'])
    assert out['status'] == 'alignment_failed_identity_used'
    # 일부 층만 빠지면 사용 층으로 계속한다
    both = {'layers': {'M1': some, 'M2': some}, 'valid': ones}
    part = align_products(both, {'layers': {'M1': some}, 'valid': ones}, **kw)
    assert part['layers_used'] == ['M1'] and 'cnt_layer_set_mismatch' in part['reasons']
    assert part['status'] == 'aligned_uncertain'
    # 겹침 없음
    top, bottom = np.zeros((100, 100), bool), np.zeros((100, 100), bool)
    top[:10] = True
    bottom[90:] = True
    pts_top, pts_bottom = np.zeros((100, 100), bool), np.zeros((100, 100), bool)
    pts_top[3, 3:60:7] = True
    pts_bottom[95, 3:60:7] = True
    out = align_products({'layers': {'M1': pts_top}, 'valid': top}, {'layers': {'M1': pts_bottom}, 'valid': bottom},
                         **{**KW, 'search_um': 2.0})
    assert out['status'] == 'alignment_failed_identity_used' and 'no_overlap' in out['reasons']
    assert out['offset_xy_px'] == [0, 0]


def test_admission_passes_at_the_limit_and_refuses_one_byte_below():
    world = random_world(51, shape=(200, 200))
    ref, tgt = pair(world, (0, 0), (0, 0), (150, 150), (150, 150))
    need = estimate_alignment_bytes((150, 150), (150, 150), 100, 3)
    ok = align_products(ref, tgt, **{**KW, 'max_operation_bytes': need})
    assert ok['estimated_bytes'] == need and ok['offset_xy_px'] == [0, 0]
    with pytest.raises(NumericContractError) as caught:
        align_products(ref, tgt, **{**KW, 'max_operation_bytes': need - 1})
    assert caught.value.code == 'oversized_real_operation'
    assert str(need) in str(caught.value) and str(need - 1) in str(caught.value)
    assert estimate_alignment_bytes((2400, 2400), (2400, 2400), 300, 3) > 1e9 * 0.3


def _png(path, shape, pixels, extra=()):
    full = np.zeros(shape, dtype=bool)
    for y, x in tuple(pixels) + tuple(extra):
        full[y, x] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.where(full, 0, 255).astype(np.uint8)).save(path)


def test_load_cnt_layers_crops_each_layer_without_cross_layer_union(tmp_path):
    frame = SimpleNamespace(shape_yx=(40, 50), pixel_pitch_um=0.1, origin_xy_um=(50.0, 50.0), frame_id='f1')
    full = (40 + 1000, 50 + 1000)
    cno = tmp_path / 'PRODUCT' / 'cno'
    _png(cno / '1_M1.png', full, [(503, 504)])
    _png(cno / '2_V.png', full, [(510, 520)])
    _png(cno / '3_CNT.png', full, [(530, 540), (10, 10)])      # (10,10) 은 crop 밖
    _png(cno / '4_M1.png', full, [(505, 506)])                 # 같은 layer 이름 중복
    out = load_cnt_layers(tmp_path, 'PRODUCT', frame)
    assert sorted(out['layers']) == ['CNT', 'M1', 'V']
    assert all(arr.shape == (40, 50) and arr.dtype == bool for arr in out['layers'].values())
    assert {tuple(p) for p in np.argwhere(out['layers']['M1'])} == {(3, 4), (5, 6)}
    assert {tuple(p) for p in np.argwhere(out['layers']['V'])} == {(10, 20)}
    assert {tuple(p) for p in np.argwhere(out['layers']['CNT'])} == {(30, 40)}
    assert [w['status'] for w in out['warnings']] == ['duplicate_cnt_mask_same_name']
    assert out['warnings'][0]['layer'] == 'M1'
    assert len(out['files']) == 4 and all(len(f['sha256']) == 64 for f in out['files'])
    (tmp_path / 'EMPTY' / 'cno').mkdir(parents=True)
    empty = load_cnt_layers(tmp_path, 'EMPTY', frame)
    assert empty['layers'] == {} and empty['warnings'][0]['status'] == 'cnt_not_provided'
    missing = load_cnt_layers(tmp_path, 'NOPE', frame)
    assert missing['layers'] == {} and missing['warnings'][0]['status'] == 'product_folder_missing'


@pytest.mark.skipif(not os.environ.get('SIMCOMPARE_ETX_PY'), reason='SIMCOMPARE_ETX_PY not set')
@pytest.mark.parametrize('name', ['identifiable_case', 'ambiguous_case'])
def test_python39_result_is_identical(name):
    py = os.path.expanduser(os.environ['SIMCOMPARE_ETX_PY'])
    code = (CASE_SRC + '\nimport json, sys\nsys.path.insert(0, %r)\n' % SRC +
            'from simcompare.cnt_align import align_products\n'
            'ref, tgt, kw, _ = %s()\n' % name +
            'print(json.dumps(align_products(ref, tgt, **kw), sort_keys=True))\n')
    done = subprocess.run([py, '-c', code], capture_output=True, text=True, check=True, timeout=600)
    ref, tgt, kw, _ = {'identifiable_case': identifiable_case, 'ambiguous_case': ambiguous_case}[name]()
    here = json.dumps(align_products(ref, tgt, **kw), sort_keys=True)
    assert done.stdout.strip() == here
    assert math.isfinite(json.loads(here)['dice_best'])
