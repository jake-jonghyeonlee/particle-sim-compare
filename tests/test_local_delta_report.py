"""local_delta.html: 정렬·내장 PNG·외부 참조 없음·자산 없는 쌍의 표기 (PLAN_v3 T5)."""
import base64
import io
import json
from pathlib import Path
import re

import numpy as np
import pytest
from PIL import Image

from simcompare.local_delta_report import _crop_cells, _iou_cell_centres
from simcompare.numeric_schema import Frame
from simcompare.pipeline import load_numeric_config, run_numeric_local_delta
from simcompare.synthetic import generate_numeric_demo
from test_local_delta import correspondence_file


def test_crop_cells_clip_to_frame_and_include_margin():
    frame = Frame('f', 0.5, (0, 0), (4, 4), np.ones((4, 4), bool))
    assert _crop_cells(frame, (0.5, 0.5, 1.5, 1.5), 0.5) == (0, 4, 0, 4)
    assert _crop_cells(frame, (0.5, 0.5, 1.5, 1.5), 0.0) == (1, 3, 1, 3)
    assert _crop_cells(frame, (5.0, 5.0, 6.0, 6.0), 0.0) is None


def test_iou_samples_cell_centres_without_registration():
    frame = Frame('f', 0.5, (0, 0), (4, 4), np.ones((4, 4), bool))
    a = np.zeros((4, 4), bool); a[1:3, 1:3] = True
    b = np.zeros((4, 4), bool); b[1:3, 1:2] = True
    result = _iou_cell_centres(a, frame, (0.5, 0.5, 1.5, 1.5), b, frame, (0.5, 0.5, 1.5, 1.5))
    assert result == {'value': 0.5, 'status': 'available', 'reason': None, 'sampling': 'cell_centres', 'registration': 'none'}
    other = Frame('g', 1.0, (0, 0), (2, 2), np.ones((2, 2), bool))
    mismatch = _iou_cell_centres(a, frame, (0.5, 0.5, 1.5, 1.5), np.ones((2, 2), bool), other, (0.0, 0.0, 1.0, 1.0))
    assert mismatch['status'] == 'not_applicable' and mismatch['reason'] == 'grid_mismatch'
    empty = _iou_cell_centres(np.zeros((4, 4), bool), frame, (0.5, 0.5, 1.5, 1.5), np.zeros((4, 4), bool), frame, (0.5, 0.5, 1.5, 1.5))
    assert empty['reason'] == 'both_empty'


def test_demo_page_embeds_thumbnails_sorted_without_external_references(tmp_path):
    generate_numeric_demo(tmp_path / 'demo')
    cfg = load_numeric_config(tmp_path / 'demo' / 'config.yaml')
    payload = json.loads(correspondence_file(tmp_path / 'c.json').read_text())
    second = json.loads(json.dumps(payload['pairs'][0]))
    second.update(pair_id='P2')
    for key in ('a', 'b'):
        second[key].pop('endpoints'); second[key].pop('failure_type')
    second['a']['center_xy_um'] = [104.5, 200.5]
    payload['pairs'].append(second)
    (tmp_path / 'c.json').write_text(json.dumps(payload), encoding='utf-8')
    result = run_numeric_local_delta(cfg, tmp_path / 'out', correspondence=tmp_path / 'c.json', window_sizes_um=[1.0],
                                     sort_diameter_um=5)
    html_path = tmp_path / 'out' / 'local_delta.html'
    assert str(html_path) in result['outputs'] and result['exit_code'] == 0
    html = html_path.read_text(encoding='utf-8')
    assert 'http://' not in html and 'https://' not in html and '<script' not in html
    assert html.count('<tr class="pair">') == 2
    assert html.index('<b>P1</b>') < html.index('<b>P2</b>')  # equal |Δ| → pair_id order
    images = re.findall(r'data:image/png;base64,([A-Za-z0-9+/=]+)', html)
    assert len(images) == 6  # P1: 2 layouts + 2 hotspots; P2: 2 hotspots only
    decoded = Image.open(io.BytesIO(base64.b64decode(images[0])))
    assert decoded.mode == 'RGB' and decoded.width >= 100
    assert '전극 지정 없음' in html  # P2 has no endpoints → no layout thumbnail, hotspot still drawn
    assert 'layout IoU' in html and 'registration none' in html
    assert 'DL 점수는 출처 표시일 뿐' in html and '합성 동작 검증' in html
    assert 'lateral_only' in html and 'vertical_only' in html and 'simultaneous' in html


def test_page_without_assets_or_sort_diameter_still_renders(tmp_path):
    generate_numeric_demo(tmp_path / 'demo')
    cfg = load_numeric_config(tmp_path / 'demo' / 'config.yaml')
    cfg['reference'].pop('asset_manifest')
    correspondence_file(tmp_path / 'c.json')
    result = run_numeric_local_delta(cfg, tmp_path / 'out', correspondence=tmp_path / 'c.json', window_sizes_um=[1.0])
    html = (tmp_path / 'out' / 'local_delta.html').read_text(encoding='utf-8')
    assert result['exit_code'] == 0 and '대응 파일 순서 (정렬 지름 미지정)' in html
    assert 'source_shape_required' in html  # reference layouts unavailable, page still written
    assert len(re.findall(r'data:image/png;base64,', html)) == 3  # a hotspot, b layout, b hotspot


def _png_ok(path):
    with Image.open(path) as image:
        image.verify()
    return Image.open(path)


def test_thumbs_json_and_png_files_match_the_embedded_page(tmp_path):
    generate_numeric_demo(tmp_path / 'demo')
    cfg = load_numeric_config(tmp_path / 'demo' / 'config.yaml')
    correspondence_file(tmp_path / 'c.json')
    result = run_numeric_local_delta(cfg, tmp_path / 'out', correspondence=tmp_path / 'c.json', window_sizes_um=[1.0],
                                     sort_diameter_um=5)
    out = tmp_path / 'out'
    thumbs_path = out / 'local_delta_thumbs.json'
    assert str(thumbs_path) in result['outputs']
    payload = json.loads(thumbs_path.read_text(encoding='utf-8'))
    assert payload['schema_version'] == 'local_delta_thumbs_v1'
    assert len(payload['crops']) == 4 and {c['kind'] for c in payload['crops']} == {'layout', 'hotspot'}
    html = (out / 'local_delta.html').read_text(encoding='utf-8')
    embedded = [base64.b64decode(x) for x in re.findall(r'data:image/png;base64,([A-Za-z0-9+/=]+)', html)]
    for crop in payload['crops']:
        assert {'pair_id', 'window_size_um', 'diameter_um', 'side', 'kind', 'file', 'note', 'crop_bounds_um'} <= set(crop)
        assert crop['file'].startswith('local_delta_thumb_') and str(out / crop['file']) in result['outputs']
        assert (out / crop['file']).read_bytes() in embedded  # same bytes as the page, rendered once
        _png_ok(out / crop['file'])
        x0, y0, x1, y1 = crop['crop_bounds_um']
        assert x1 > x0 and y1 > y0
    assert len(payload['windows']) == 2 and all(w['window_bounds_um'] for w in payload['windows'])
    assert {s['side'] for s in payload['sides']} == {'a', 'b'}
    for side in payload['sides']:
        assert {'product_id', 'frame_id', 'origin_xy_um', 'pixel_pitch_um', 'shape_yx', 'overview_file', 'overview_scale', 'note'} <= set(side)
        image = _png_ok(out / side['overview_file'])
        assert max(image.size) <= 800 and str(out / side['overview_file']) in result['outputs']
        assert side['overview_scale'] >= 1 and side['note'] is None


def test_overview_long_side_is_capped_and_keeps_thin_lines():
    from simcompare.local_delta_report import _overview_png, ELECTRODE_COLOURS
    frame = Frame('f', 0.1, (0, 0), (900, 2000), np.ones((900, 2000), bool))
    mask = np.zeros((900, 2000), bool); mask[5, :] = True  # one-cell-thin line
    data, step = _overview_png(frame, [mask, np.zeros_like(mask)])
    image = Image.open(io.BytesIO(data))
    assert step == 3 and max(image.size) <= 800
    assert tuple(np.asarray(image)[1, 10]) == ELECTRODE_COLOURS[0]


def test_without_asset_manifest_overview_is_null_with_reason(tmp_path):
    generate_numeric_demo(tmp_path / 'demo')
    cfg = load_numeric_config(tmp_path / 'demo' / 'config.yaml')
    cfg['reference'].pop('asset_manifest')
    cfg['target'].pop('asset_manifest')
    correspondence_file(tmp_path / 'c.json')
    result = run_numeric_local_delta(cfg, tmp_path / 'out', correspondence=tmp_path / 'c.json', window_sizes_um=[1.0])
    assert result['exit_code'] == 0
    payload = json.loads((tmp_path / 'out' / 'local_delta_thumbs.json').read_text(encoding='utf-8'))
    for side in payload['sides']:
        assert side['overview_file'] is None and side['overview_scale'] is None and side['note']
    layouts = [c for c in payload['crops'] if c['kind'] == 'layout']
    assert all(c['file'] is None and c['note'] for c in layouts)
    assert not list((tmp_path / 'out').glob('local_delta_overview_*'))
