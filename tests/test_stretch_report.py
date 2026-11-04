"""stretch-align/T5: alignment.html 의 stretch 표시와, stretch 가 아닌 실행의 HTML 이 바이트 불변임을 확인한다."""
import base64
import hashlib
import io
import re

from PIL import Image

from simcompare import alignment_report as ar

from test_stretch_cli import drift, make, queries, STRETCH, UNIT  # noqa: F401  (drift 는 fixture)

LOCAL = ('--apply-align', 'local', '--local-align-um', '1.5', '--unit-align-um', '2.5')
# 기준 커밋 acf2c58(T5 적용 전)에서 같은 drift fixture 로 각 실행을 돌려 얻은 alignment.html 의 sha256 과 바이트 수.
# Python 3.11 과 3.9 에서 같은 값이다. HTML 을 일부러 바꿀 때만 갱신한다.
# stretch-align/T8: PNG 의 zlib 바이트(Pillow·zlib 버전에 따라 달라짐)에 묶이지 않게, HTML 을 두 부분으로 나눠 고정한다.
# (1) 인라인 PNG 를 자리표시로 바꾼 텍스트의 sha256 과 바이트 수, (2) 디코드한 모든 PNG 의 (모드, 크기, 픽셀 바이트) 를 순서대로 해시한 값과 PNG 개수.
# 텍스트 전부와 픽셀 전부를 고정하므로 이전의 전체 바이트 해시보다 약하지 않다. 압축 방식만 달라진 경우는 통과한다.
BASELINE_HTML = {
    'plain': ((), '50dde277ff05aa1dd7a96071ddbb785cdc460ddf5b6c7432d39821bc1a613ae1', 125589,
              '0302656d009221f8f93c2b22d9f1d20fb67360a58b5eb1143c4372d5885c87af', 221),
    'unit': (UNIT, 'd22f42b875cb7b0003c90734c47fda0a30a267095e25d2362ffc46bb42f70024', 71460,
             '8c17567fc023139bd98f992b288c2fb291d212151926cbe5a71998c3508d6a19', 116),
    'local': (LOCAL, '9d18c30f4974bc4ce19ab9a18841bb51c348f55d301e5af98c6279689eaf870b', 25159,
              '7957a0b917b3c3bb654bbfcadb06629a0b546e4c7161eeadf1cc10a43e2e4575', 26),
    'fold': (UNIT + ('--unit-fold',), '2cfb4cd5fda54141a1cb1029340e69eb1353133f67b613147b7c0eb8dcac750e', 63605,
             '25f221d2712cc1e906f1247a69bd9293d58b866910d4432993313e9e68205c16', 101),
}
PNG_URI = re.compile(r'data:image/png;base64,([A-Za-z0-9+/=]+)')
PNG_SLOT = 'data:image/png;base64,@@PNG@@'


def html_fingerprint(text):
    """(자리표시 텍스트 sha256, 텍스트 바이트 수, 디코드 픽셀 sha256, PNG 개수)."""
    pixels, count = hashlib.sha256(), 0
    for m in PNG_URI.finditer(text):
        with Image.open(io.BytesIO(base64.b64decode(m.group(1)))) as im:
            im.load()
            pixels.update(repr((im.mode, im.size)).encode() + im.tobytes())
        count += 1
    slotted = PNG_URI.sub(PNG_SLOT, text).encode('utf-8')
    return hashlib.sha256(slotted).hexdigest(), len(slotted), pixels.hexdigest(), count


def html_of(run_):
    return (run_.out / 'alignment.html').read_text(encoding='utf-8')


def test_non_stretch_html_is_byte_identical_to_the_baseline_commit(drift):
    got = {name: html_fingerprint(html_of(make(drift, *spec[0]))) for name, spec in BASELINE_HTML.items()}
    assert got == {name: spec[1:] for name, spec in BASELINE_HTML.items()}


# --------------------------------------------------------------------------- stretch 모드의 HTML

def test_stretch_html_has_summary_map_list_detail_and_footnotes(drift):
    got = make(drift, *STRETCH)
    html = html_of(got)
    s = got.alignment['stretch']
    # 1절 요약
    assert '<code>--apply-align stretch</code>' in html and '타일 크기' in html and '탐색 반경' in html
    assert '%s µm (%s px)' % (s['tile_um'], s['tile_px']) in html and '이웃 타일 단차 상한 s_max' in html
    assert '적용 타일 k / n' in html and '%d / %d' % (s['n_applied'], s['n_tiles']) in html
    assert '타일 상태 수' in html and 'unit 별 늘어남 배율 (ppm)' in html and 'ppm' in html
    # 2c-2절 늘어남 지도: 표 + 인라인 PNG + 범례, 변경 점수에 쓰지 않는다는 문구
    start = html.index('<h3 id="stretch-map">2c-2. 늘어남 지도</h3>')
    end = html.index('<h2>3. 변경 강조 지도</h2>')
    section = html[start:end]
    assert '변경 점수·순위·ROI 선정에 쓰지 않는다' in section and 'max |δ| (x, y)' in section and '적용 타일 k / n' in section
    assert section.count('<img src="data:image/png;base64,') == 1 and '범례' in section and 'µm' in section
    # 4절 ROI 목록: 합의 열은 접기가 없으면 없다. 경계 열은 늘 있다
    listing = html[html.index('<h2 id="list">4. ROI 목록</h2>'):html.index('<h2>5. ROI 상세')]
    assert '<th>경계</th>' in listing and '합의 k/4' not in listing
    crossing = [q for q in queries(got) if q['crosses_tile_boundary']]
    assert ('<b>경계</b>' in listing) == bool(crossing)
    # 5절 ROI 상세
    detail = html[html.index('<h2>5. ROI 상세'):html.index('<h2>6. ')]
    for label in ('offset_source', '적용 offset (x, y px)', 'crosses_tile_boundary', 'seam_excluded_pixels',
                  'reference 측 unit 번호·unit 좌표 (µm)', 'target 측 중심 (µm)', 'target 측 unit 번호·unit 좌표 (µm)'):
        assert detail.count(label) == len(queries(got)), label
    first, target = queries(got)[0], queries(got, 'target_to_reference')[0]
    assert ar.escape(ar._json(target['center_xy_um'])) in detail and ar.escape(ar._json(first['offset_xy_px_applied'])) in detail
    # 각주: 모서리 한계
    notes = html[html.index('<h2>7. 각주</h2>'):]
    assert '<code>--apply-align stretch</code>' in notes and '최악 2·s_max, 일반적으로 ≤ 2r' in notes and '이음새 띠' in notes
    assert '늘어남 지도(2c-2절)는 참고 자료' in notes


def test_stretch_html_with_unit_fold_has_the_consensus_column_and_target_coordinates(drift):
    got = make(drift, *STRETCH, '--unit-fold', '--merge-scope', 'all')
    html = html_of(got)
    listing = html[html.index('<h2 id="list">4. ROI 목록</h2>'):html.index('<h2>5. ROI 상세')]
    assert '<th>합의 k/4</th>' in listing
    for q in queries(got):
        consensus = q['unit_fold']['consensus']
        assert '<td>%d/%d</td>' % (consensus['k'], consensus['n']) in listing
    detail = html[html.index('<h2>5. ROI 상세'):html.index('<h2>6. ')]
    assert detail.count('접기 합의') == len(queries(got)) and '대표)' in detail


def test_stretch_html_is_not_added_to_other_modes(drift):
    for extra in ((), UNIT, LOCAL, UNIT + ('--unit-fold',)):
        html = html_of(make(drift, *extra))
        assert 'stretch' not in html.lower() and '늘어남' not in html and '합의 k/4' not in html


def test_stretch_html_is_deterministic(drift):
    assert (make(drift, *STRETCH).out / 'alignment.html').read_bytes() == (make(drift, *STRETCH).out / 'alignment.html').read_bytes()


# --------------------------------------------------------------------------- 늘어남 지도 그림과 크기

def _grid_map(n, pitch=0.1, unit_tiles=8):
    """n x n 타일(타일 20 px)의 stretch_map 문서 조각. 타일마다 δ 가 조금씩 다르고 일부는 적용하지 않는다."""
    tiles = []
    for ty in range(n):
        for tx in range(n):
            tiles.append({'unit_index_xy': [tx // unit_tiles, ty // unit_tiles], 'tile_index_xy': [tx % unit_tiles, ty % unit_tiles],
                          'region_px': [ty * 20, ty * 20 + 20, tx * 20, tx * 20 + 20], 'delta_xy_px': [(tx + ty) % 4, (tx * ty) % 3],
                          'applied': (tx + ty) % 5 != 0})
    units = [{'unit_index_xy': [ux, uy], 'n_tiles': unit_tiles ** 2, 'n_applied': unit_tiles ** 2 - 10, 'status_counts': {'ok': 54},
              'max_abs_delta_xy_px': [3, 2], 'linear_fit': {'n': 54, 'scale_ppm_xy': [12.5, -3.0]}, 'linear_fit_reason': None}
             for uy in range(n // unit_tiles) for ux in range(n // unit_tiles)]
    return {'pitch_um': pitch, 'tiles': tiles, 'units': units}


def test_stretch_image_one_cell_per_tile_with_grey_for_unapplied_tiles():
    tiles = [{'region_px': [0, 10, 0, 10], 'delta_xy_px': [0, 0], 'applied': True},
             {'region_px': [0, 10, 10, 20], 'delta_xy_px': [3, 4], 'applied': True},
             {'region_px': [10, 20, 0, 10], 'delta_xy_px': [1, 0], 'applied': True},
             {'region_px': [10, 20, 10, 20], 'delta_xy_px': [9, 9], 'applied': False}]
    rgb, vmax = ar._stretch_image({'pitch_um': 0.1, 'tiles': tiles})
    cell = rgb.shape[0] // 2
    assert rgb.shape == (2 * cell, 2 * cell, 3) and abs(vmax - 0.5) < 1e-9          # δ 길이 5 px × 0.1 µm
    assert tuple(rgb[0, 0]) == ar.WHITE and tuple(rgb[0, cell]) == tuple(ar._LUT[255]) and tuple(rgb[cell, cell]) == ar.GRAY
    assert tuple(rgb[cell, 0]) not in (ar.WHITE, ar.GRAY) and (rgb[:cell, :cell] == rgb[0, 0]).all()


def test_html_with_256_tiles_stays_under_the_size_limit(drift, capsys):
    got = make(drift, *STRETCH)
    big = _grid_map(16)
    assert len(big['tiles']) == 256
    alignment = {**got.alignment, 'stretch': {**got.alignment['stretch'], 'n_tiles': 256, 'n_applied': 205}}
    data, info = ar.build_alignment_html(alignment, queries=[], windows=[], apply_mode='stretch', stretch_map=big)
    base, _ = ar.build_alignment_html(got.alignment, queries=[], windows=[], apply_mode='stretch', stretch_map=_grid_map(2, unit_tiles=1))
    assert info['bytes'] == len(data) <= ar.MAX_HTML_BYTES and len(data) - len(base) < 64 * 1024        # 256 타일 지도는 수 KB 다
    real = len((got.out / 'alignment.html').read_bytes())
    with capsys.disabled():
        print('\nstretch html bytes: 256 tiles (no ROI) = %d, fixture run (64 tiles, %d ROI) = %d' % (len(data), len(queries(got)), real))
    assert real <= ar.MAX_HTML_BYTES
