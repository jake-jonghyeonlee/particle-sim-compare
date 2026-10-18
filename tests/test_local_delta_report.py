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
