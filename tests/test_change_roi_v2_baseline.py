"""change-roi-v2 기준선(T0): candidates-change 기본 실행의 산출물 해시를 0717947 에서 고정한다.

뒤 Task 가 기본 실행의 바이트를 바꾸지 않는지 확인하는 회귀 시험이다. 입력은 test_change_candidates 의 first_run 과
같은 world 이다. producer_commit 은 git HEAD 라 commit 마다 바뀌므로 고정 값으로 pin 한다.
정규화: alignment_report(HTML 경로·bytes·downscaled·crops_omitted)는 HTML 이 바뀔 때마다 달라지므로 candidate_queries_v1.json
에서 최상위 `alignment_report` 객체 전체를 지우고 다시 쓴 텍스트를 해시한다. alignment.json·alignment.html·execution.json 은 제외한다.
"""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from simcompare import roi_export
from test_change_candidates import (build_product, load, run_change, write_config)

PINNED_COMMIT = '0' * 40

# 입력 폴더의 절대 경로가 여러 산출물에 들어간다. 해시 전에 world 루트 경로(와 resolve() 한 /private 형)를 <WORLD> 로 바꾼다.
# 경로가 든 파일의 해시를 값으로 가진 키는 경로가 바뀌면 달라지므로 비교에서 뺀다(그 파일들은 치환 뒤 직접 해시한다):
#   config_sha256 (config yaml 해시), export_sha256, index_export_sha256 (review_rois.json 해시) — sidecar 와 candidate_queries 의 어느 깊이든.
DROP_KEYS = ('config_sha256', 'export_sha256', 'index_export_sha256')
PLACEHOLDER = '<WORLD>'

# 뒤 Task 가 정규화 키를 늘릴 수 있게 한 곳에 둔다(T4b: contributing_cases·sidecar pairs 의 area 키 제외 예정).
CANDIDATE_DROP_KEYS = ('alignment_report',)

RAW_FILES = ('funnel.json', 'funnel.csv', 'direct/roi_correspondence.json', 'direct/direct_pairs_sidecar.json')
RAW_GLOBS = ('*/review_rois.json', '*/mask_derivation.json')

EXPECTED = {   # recorded at 0717947
    'funnel.json': '457ca1d93d71e51adca415ee7441ae37794e73078d61986fea78412abf466dec',
    'funnel.csv': 'dc760c1db9ac06d9bb4e0426771fbe0b4f231250c129d37f37ae5f435154dd3c',
    'direct/roi_correspondence.json': '3ff874f13acae437d1221bd23561bf81fb27235c045faf4d589fca026600d107',
    'direct/direct_pairs_sidecar.json': '2ac712f758f81d8a6d0cd1665cc576331bf5c31b9788b38f4e01eaa26fb13488',
    'reference_export/review_rois.json': 'b4e0ba95b258f884f4134a9980c8df64df881aac35c909a0b0daf5ca112ce2a7',
    'reference_index_export/review_rois.json': '69badb1aec4b2868099c4b7fbcd809722f835e9e24313ee331a6d5d3e9cd6c20',
    'target_export/review_rois.json': '7040ffdd2931962409b20e4ef9c1ec9d18e25e24d59f227540f2e8e427f5c94b',
    'target_index_export/review_rois.json': 'c28212cc8948d06ee078635cf30dfa20b284bfd48572ec0d97cc4a8895bc631c',
    'reference_export/mask_derivation.json': '15bf297534cb28e7767c0058ade9141b1a645d629273a65c2533261223942d29',
    'reference_index_export/mask_derivation.json': '15bf297534cb28e7767c0058ade9141b1a645d629273a65c2533261223942d29',
    'target_export/mask_derivation.json': '15bf297534cb28e7767c0058ade9141b1a645d629273a65c2533261223942d29',
    'target_index_export/mask_derivation.json': '15bf297534cb28e7767c0058ade9141b1a645d629273a65c2533261223942d29',
    'candidate_queries_v1.json(normalised)': '365b57882e655980f1aa890073f1e4261a305ebd114eb9a0f2f751359b74c373',
}


def _sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _drop(value):
    if isinstance(value, dict):
        return {k: _drop(v) for k, v in value.items() if k not in DROP_KEYS}
    if isinstance(value, list):
        return [_drop(v) for v in value]
    return value


def _text(path, roots, *, drop_keys, drop_top=()):
    """파일 텍스트에서 world 루트 경로를 <WORLD> 로 바꾼다. drop_keys 이면 JSON 으로 읽어 DROP_KEYS(와 drop_top)를 빼고 다시 쓴다."""
    text = Path(path).read_text(encoding='utf-8')
    for root in sorted(set(roots), key=len, reverse=True):
        text = text.replace(root, PLACEHOLDER)
    if drop_keys:
        payload = json.loads(text)
        for key in drop_top:
            payload.pop(key, None)
        text = json.dumps(_drop(payload), indent=2, ensure_ascii=False)
    return text


def baseline_hashes(out, roots):
    out = Path(out)
    files = [out/name for name in RAW_FILES]
    for pattern in RAW_GLOBS:
        files.extend(sorted(out.glob(pattern)))
    hashes = {}
    for p in files:
        name = p.relative_to(out).as_posix()
        hashes[name] = _sha_bytes(_text(p, roots, drop_keys=name == 'direct/direct_pairs_sidecar.json').encode('utf-8'))
    hashes['candidate_queries_v1.json(normalised)'] = _sha_bytes(
        _text(out/'candidate_queries_v1.json', roots, drop_keys=True, drop_top=CANDIDATE_DROP_KEYS).encode('utf-8'))
    return hashes


@pytest.fixture(scope='module')
def pinned_run(tmp_path_factory):
    base = tmp_path_factory.mktemp('baseline_world')
    datas, assignments = {}, {}
    for side in ('reference', 'target'):
        datas[side], assignments[side] = build_product(base, side)
    config = write_config(base, datas, assignments, map_spec={'diameter_um': 5})
    world = SimpleNamespace(base=base, config=config, dl1=base/'dl1')
    original = roi_export.export_review_rois
    mp = pytest.MonkeyPatch()

    def pinned(*args, **kwargs):
        kwargs['producer_commit'] = PINNED_COMMIT
        return original(*args, **kwargs)

    mp.setattr(roi_export, 'export_review_rois', pinned)
    try:
        out = tmp_path_factory.mktemp('baseline_out')/'out'
        code = run_change(world, out)
    finally:
        mp.undo()
    return SimpleNamespace(out=out, code=code, payload=load(out), roots=[str(base), str(base.resolve())])


def test_exit_code_and_status_match_the_baseline(pinned_run):
    assert pinned_run.code == 3                      # top_k (3) is not filled: only one place changed
    assert pinned_run.payload['producer_commit'] == PINNED_COMMIT


def test_fixture_has_no_images_skipped_warning(pinned_run):
    execution = json.loads((pinned_run.out/'execution.json').read_text(encoding='utf-8'))
    codes = [w.get('code') if isinstance(w, dict) else w for w in pinned_run.payload.get('warnings', [])]
    codes += [w.get('code') if isinstance(w, dict) else w for w in execution.get('warnings', [])]
    assert 'alignment_report_images_skipped' not in codes


def test_default_run_hashes_are_pinned(pinned_run):
    assert baseline_hashes(pinned_run.out, pinned_run.roots) == EXPECTED


# --- T1: frame 크기 차이와 비교 영역 기록 ------------------------------------------------------------------------------
import test_change_candidates as tcc
from simcompare import change_map


def _run_with_target_shape(tmp_path, delta):
    """target hotspot mask 를 (dy, dx) 만큼 크게(양수, 아래·오른쪽 0 채움) 또는 작게(음수, 아래·오른쪽 자름) 만든 world 를 돌린다."""
    original = tcc.hotspot_masks
    mp = pytest.MonkeyPatch()

    def resized(side, extra_target_case=False):
        masks = original(side, extra_target_case)
        if side != 'target' or delta == (0, 0):
            return masks
        dy, dx = delta
        out = {}
        for case, mask in masks.items():
            if dy >= 0 and dx >= 0:
                big = np.zeros((mask.shape[0]+dy, mask.shape[1]+dx), bool)
                big[:mask.shape[0], :mask.shape[1]] = mask
                out[case] = big
            else:
                out[case] = mask[:mask.shape[0]+dy, :mask.shape[1]+dx]
        return out

    mp.setattr(tcc, 'hotspot_masks', resized)
    try:
        base = tmp_path/'world'
        base.mkdir()
        datas, assignments = {}, {}
        for side in ('reference', 'target'):
            if side == 'target':      # electrode PNGs and CNT layers of the target are 1000 px larger than its frame
                mp.setattr(tcc, 'FULL', (tcc.FULL[0]+delta[0], tcc.FULL[1]+delta[1]))
            datas[side], assignments[side] = tcc.build_product(base, side)
        mp.undo()
        mp.setattr(tcc, 'hotspot_masks', resized)
        config = tcc.write_config(base, datas, assignments, map_spec={'diameter_um': 5})
        world = SimpleNamespace(base=base, config=config, dl1=base/'dl1')
        out = tmp_path/'out'
        code = tcc.run_change(world, out)
    finally:
        mp.undo()
    return code, out


def _notes(out):
    return [n for n in load(out)['notes'] if n.get('code') == 'frame_shape_differs']


@pytest.mark.parametrize('delta', [(10, 6), (-10, -6)])
def test_unequal_frames_are_recorded_and_noted_without_changing_the_exit_code(tmp_path, delta):
    code, out = _run_with_target_shape(tmp_path, delta)
    alignment = load(out, 'alignment.json')
    ref, tgt = alignment['frame_shape_reference_yx'], alignment['frame_shape_target_yx']
    assert [t-r for r, t in zip(ref, tgt)] == list(delta) == alignment['frame_shape_delta_px']
    assert alignment['frame_anchor'] == 'top_left_index'
    domain = alignment['comparison_domain']
    assert domain['reference_frame_pixels'] == ref[0]*ref[1] and domain['target_frame_pixels'] == tgt[0]*tgt[1]
    assert 0 < domain['pixels'] <= domain['pixels_before_erosion'] <= min(ref[0]*ref[1], tgt[0]*tgt[1])
    assert domain['fraction_of_reference'] == round(domain['pixels_before_erosion']/(ref[0]*ref[1]), 6)
    assert domain['fraction_of_target'] == round(domain['pixels_before_erosion']/(tgt[0]*tgt[1]), 6)
    y0, y1, x0, x1 = domain['bbox_px']
    assert 0 <= y0 < y1 <= ref[0] and 0 <= x0 < x1 <= ref[1]
    note = _notes(out)
    assert note == [{'code': 'frame_shape_differs', 'severity': 'info', 'reference_yx': ref, 'target_yx': tgt,
                     'delta_px': list(delta)}]
    execution = json.loads((out/'execution.json').read_text(encoding='utf-8'))
    assert [n for n in execution['notes'] if n.get('code') == 'frame_shape_differs'] == note
    assert not any(w.get('code') == 'frame_shape_differs' for w in load(out)['warnings'])
    assert code == (3 if load(out)['warnings'] else 0)
    html = (out/'alignment.html').read_text(encoding='utf-8')
    assert 'resize 하지 않습니다' in html


def test_equal_frames_record_the_shape_and_add_no_note(pinned_run):
    alignment = load(pinned_run.out, 'alignment.json')
    assert alignment['frame_shape_reference_yx'] == alignment['frame_shape_target_yx']
    assert alignment['frame_shape_delta_px'] == [0, 0]
    assert _notes(pinned_run.out) == []
    assert 'resize 하지 않습니다' not in (pinned_run.out/'alignment.html').read_text(encoding='utf-8')


class _Pair:
    layer = 'L1'
    case_key = 'k'

    def load(self):
        return None, None


def _maps(ref_valid, tgt_valid, offset=(0, 0)):
    return change_map.change_maps([_Pair()], ref_valid=ref_valid, tgt_valid=tgt_valid, offset_px=offset, pitch_um=0.1,
                                  tolerance_um=0.1, max_operation_bytes=1 << 30)


def test_domain_bbox_and_counts_for_a_domain_with_a_hole():
    ref = np.ones((30, 40), bool)
    ref[10:14, 10:16] = False                          # hole
    tgt = np.ones((34, 38), bool)                      # taller, narrower: common area is 30 x 38
    result = _maps(ref, tgt)
    assert result['domain_bbox_px'] == [0, 30, 0, 38]
    stats = result['stats']
    assert stats['domain_pixels_before_erosion'] == 30*38 - 24
    assert stats['domain_pixels'] < stats['domain_pixels_before_erosion']
    assert stats['frame_pixels'] == 30*40


def test_empty_domain_has_no_bbox():
    ref = np.zeros((20, 20), bool)
    tgt = np.ones((25, 15), bool)
    result = _maps(ref, tgt)
    assert result['domain_bbox_px'] is None
    assert result['stats']['domain_pixels_before_erosion'] == 0 and result['stats']['domain_pixels'] == 0


def test_bbox_starts_after_an_invalid_margin():
    ref = np.ones((30, 40), bool)
    ref[:5, :] = False                                 # top strip
    ref[:, :7] = False                                 # left strip
    result = _maps(ref, np.ones((30, 40), bool))
    assert result['domain_bbox_px'] == [5, 30, 7, 40]


def test_bbox_shrinks_by_a_nonzero_offset():
    full = np.ones((30, 40), bool)
    y0, y1, x0, x1 = _maps(full, full, offset=(3, 0))['domain_bbox_px']
    assert (y0, y1) == (0, 30) and (x0, x1) in ((3, 40), (0, 37))


def test_alignment_record_writes_a_null_bbox_for_an_empty_domain():
    from simcompare import change_candidates
    stats = {'domain_pixels_before_erosion': 0, 'domain_pixels': 0}
    record = change_candidates._frame_record((20, 20), (25, 15), stats, None)
    assert record['comparison_domain']['bbox_px'] is None
    assert json.loads(json.dumps(record))['comparison_domain']['bbox_px'] is None
