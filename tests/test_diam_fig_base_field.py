"""diam-fig/T1: 층 묶음 target 바탕은 같은 패널의 target hotspot 과 같은 변환(unit offset 장 또는 전역 offset)으로 옮긴다."""
import numpy as np
import pytest

from simcompare import alignment_report as ar
from simcompare import change_map
from test_alignment_report import captions
from test_layer_align_local import LOCAL_ARGS, local_run, make, unit_run, world  # noqa: F401  (module-scoped fixtures)


SHAPE = (60, 80)
# reference 격자의 두 영역: 왼쪽 (dx, dy) = (0, 0), 오른쪽 (3, 2) — 둘 다 전역 offset (1, 1) 과 다르다.
FIELD = ((0, 60, 0, 40, 0, 0), (0, 60, 40, 80, 3, 2))
GLOBAL = (1, 1)


def frame():
    rng = np.random.RandomState(7)
    return rng.rand(*SHAPE) > 0.5


def source_with(field):
    valid = np.ones(SHAPE, dtype=bool)
    source = ar.ImageSource({}, {}, valid, valid, GLOBAL)
    source.hot.append({'box': (5, 55, 5, 75), 'layers': {}, 'obs': np.ones((50, 70), dtype=bool)})
    source.map_field = field
    return source


def collect(source, target):
    source.collect_bases({'L1': [0]}, lambda layer: target, lambda layer: target)
    return source.base[(0, 'L1', 'tgt')]


def test_a_target_base_follows_the_unit_field_like_the_hotspot_crop():
    target = frame()
    got = collect(source_with(FIELD), target)
    expected = change_map._shift_crop_field(target, FIELD, 5, 55, 5, 75)
    assert np.array_equal(got, expected)
    assert not np.array_equal(got, change_map._shift_crop(target, GLOBAL[1], GLOBAL[0], 5, 55, 5, 75))   # 전역 offset 으로 옮기면 달라진다


def test_a_without_a_field_the_base_keeps_the_global_offset():
    target = frame()
    got = collect(source_with(None), target)
    assert np.array_equal(got, change_map._shift_crop(target, GLOBAL[1], GLOBAL[0], 5, 55, 5, 75))


def test_b_target_caption_names_the_unit_offset_only_when_the_field_is_used():
    target = frame()
    for field, expected in ((None, False), (FIELD, True)):
        source = source_with(field)
        collect(source, target)
        source.base[(0, 'L1', 'ref')] = source.base[(0, 'L1', 'tgt')]
        window = {'window_origin_px': (20, 20), 'window_px': 20, 'layer': 'L1'}
        caps = [c for c, _ in ar._bundle_images(source, 0, window, 'L1', 400)]
        assert ('unit 별 offset' in caps[2]) is expected
        assert not any('unit 별 offset' in c for c in caps[:2])


def test_c_unit_run_captions_name_the_unit_offset_on_target_bundle_panels(unit_run, local_run):   # noqa: F811
    for run in (unit_run, local_run):
        html = (run.out / 'alignment.html').read_text(encoding='utf-8')
        targets = [c for c in captions(html) if c.startswith('ROI 층 ') and ' · target: 전극 형상 + hotspot' in c]
        assert targets and all('바탕·hotspot: unit 별 offset' in c for c in targets)
    html = (unit_run.out / 'alignment.html').read_text(encoding='utf-8')
    assert not any('ROI 국소 offset' in c for c in captions(html))
