"""change-roi/T8: ``candidates-change`` 의 ``alignment.html`` — 정렬 결과와 변경 지도를 사람이 눈으로 확인하는 보고서.

한 파일, PNG 는 base64 data URI, ``<script>`` 와 외부 URL 이 없어 ``file://`` 로 열린다. 보고서만 만든다: 계산·순위·export 는 바꾸지 않는다.
numpy·표준 라이브러리만 쓴다(PNG 는 zlib 으로 직접 쓰고, 글자는 3x5 점 글꼴이라 Pillow·글꼴 버전에 따라 바이트가 달라지지 않는다).

그림 (모두 층별이며 층 사이 합집합은 만들지 않는다)
- 층별 전체 겹침: CNT 층마다 정렬 전(offset 0)·정렬 후 두 장. reference 만 / target 만 / 둘 다 3색. 긴 변 <= OVERVIEW_MAX_PX 로 블록 any 축소.
- 변경 지도 열지도: hotspot 층마다 합집합 변화(RU, 블록당 변화 칸 수)와 조합별 잔차 합(count_map, 블록 최대)에 선정 ROI 창을 사각형·순위로 표시.
- ROI 상세: 창 + 50% 여백 crop(한 변 <= CROP_MAX_PX)의 CNT 층별 겹침과 hotspot 합집합 reference/target 겹침.

크기: 직렬화한 실제 바이트가 ``MAX_HTML_BYTES`` 를 넘으면 ROI crop 을 반 해상도로, 그래도 넘으면 순위 낮은 ROI 의 crop 부터 뺀다
(요약 표·전체 겹침·열지도는 빼지 않는다). 같은 입력은 같은 바이트다(시각·절대 경로 없음).
"""
from __future__ import annotations

from html import escape
import json
import math
import struct
import zlib

import numpy as np

from . import change_map

MAX_HTML_BYTES = 30 * 1024 * 1024
OVERVIEW_MAX_PX = 1000
CROP_MAX_PX = 400
CROP_HALF_PX = 200
SCHEMA = 'alignment_report_v1'

REF_COLOUR = (40, 90, 220)
TGT_COLOUR = (235, 140, 20)
BOTH_COLOUR = (30, 30, 30)
GRAY = (205, 205, 205)
WHITE = (255, 255, 255)
RECT_COLOUR = (0, 150, 70)
LABEL_COLOUR = (0, 0, 0)

STATUS_TEXT = {
    'aligned': '정렬 성공 (aligned)',
    'aligned_uncertain': '정렬했으나 불확실 (aligned_uncertain)',
    'alignment_failed_identity_used': '정렬 실패 — offset (0, 0) 을 그대로 썼습니다 (alignment_failed_identity_used)'}

REASON_TEXT = {
    'cnt_missing_reference': 'reference 의 CNT 층 영상이 없어 정렬할 수 없었습니다.',
    'cnt_missing_target': 'target 의 CNT 층 영상이 없어 정렬할 수 없었습니다.',
    'cnt_layer_set_mismatch': '한쪽에만 있는 CNT 층이 있어 그 층은 정렬에서 뺐습니다.',
    'no_usable_layers': '정렬에 쓸 수 있는 CNT 층이 하나도 없습니다.',
    'no_overlap': '탐색 반경 안에서 두 valid 영역이 충분히 겹치지 않습니다.',
    'peak_plateau': '최적 offset 주변에 같은 점수의 칸이 넓게 퍼져 있어(plateau) 위치가 한 점으로 정해지지 않습니다.',
    'peak_ambiguous': '최적과 비슷한 점수의 다른 peak 가 있어 정렬이 반복 주기만큼 틀렸을 수 있습니다.',
    'layer_offsets_disagree': 'CNT 층마다 가장 잘 맞는 offset 이 서로 다릅니다.',
    'residual_nonuniform': 'frame 의 사분면마다 가장 잘 맞는 offset 이 다릅니다(한 번의 이동으로 설명되지 않음).',
    'dice_below_min': '최적 offset 의 Dice 가 --align-min-dice 보다 낮습니다.',
    'search_bound_hit': '최적 offset 이 탐색 반경 가장자리에 닿았습니다.'}

FLAG_TEXT = {
    'window_partially_unobserved': '창의 일부가 비교 영역 밖입니다.',
    'union_unchanged_pair_changed': '합집합은 그대로인데 조합만 바뀐 곳입니다.',
    'window_clipped': '창이 frame 밖으로 나갑니다.',
    'merged_member_window_clipped': '병합된 창 중 일부가 frame 밖으로 나갑니다.',
    'cnt_change_unavailable': '한쪽 CNT 층이 비어 CNT 변경 면적을 구하지 못했습니다.',
    'roi_rows_partially_blocked': '일부 ROI 행이 blocked 입니다.'}


# --------------------------------------------------------------------------- 래스터 도구

def _png(rgb):
    """(H, W, 3) uint8 → PNG 바이트. Pillow 를 쓰지 않는다(버전과 무관하게 같은 바이트)."""
    height, width = rgb.shape[:2]
    raw = np.zeros((height, width * 3 + 1), dtype=np.uint8)
    raw[:, 1:] = rgb.reshape(height, width * 3)

    def chunk(kind, data):
        body = kind + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw.tobytes(), 6)) + chunk(b'IEND', b''))


def _uri(png):
    import base64
    return 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')


def _block(array, step, mode):
    """step x step 블록 축소. mode: any(bool) / sum(uint32) / max. 패딩 복사 없이 reduceat 으로 한다."""
    if mode == 'sum':
        array = array.astype(np.uint32) if step == 1 else array
    if step == 1:
        return np.asarray(array)
    iy, ix = np.arange(0, array.shape[0], step), np.arange(0, array.shape[1], step)
    if mode == 'any':
        return np.logical_or.reduceat(np.logical_or.reduceat(array, iy, axis=0), ix, axis=1)
    if mode == 'sum':
        part = np.add.reduceat(array, iy, axis=0, dtype=np.uint32)
        return np.add.reduceat(part, ix, axis=1, dtype=np.uint32)
    return np.maximum.reduceat(np.maximum.reduceat(array, iy, axis=0), ix, axis=1)


def _step_for(shape, limit):
    return max(1, int(math.ceil(max(shape) / float(limit))))


def _overlay(ref, tgt, gray):
    rgb = np.empty(ref.shape + (3,), dtype=np.uint8)
    rgb[:] = WHITE
    rgb[gray] = GRAY
    rgb[ref & ~tgt] = REF_COLOUR
    rgb[tgt & ~ref] = TGT_COLOUR
    rgb[ref & tgt] = BOTH_COLOUR
    return rgb


def _rect(rgb, y0, x0, y1, x1, colour):
    """(y0, x0)–(y1, x1) 포함 좌표의 1 px 테두리. 이미지 밖은 자른다."""
    h, w = rgb.shape[:2]
    y0, y1, x0, x1 = max(y0, 0), min(y1, h - 1), max(x0, 0), min(x1, w - 1)
    if y0 > y1 or x0 > x1:
        return
    rgb[y0, x0:x1 + 1] = colour
    rgb[y1, x0:x1 + 1] = colour
    rgb[y0:y1 + 1, x0] = colour
    rgb[y0:y1 + 1, x1] = colour


_DIGITS = {
    '0': ('111', '101', '101', '101', '111'), '1': ('010', '110', '010', '010', '111'),
    '2': ('111', '001', '111', '100', '111'), '3': ('111', '001', '111', '001', '111'),
    '4': ('101', '101', '111', '001', '001'), '5': ('111', '100', '111', '001', '111'),
    '6': ('111', '100', '111', '101', '111'), '7': ('111', '001', '001', '010', '010'),
    '8': ('111', '101', '111', '101', '111'), '9': ('111', '101', '111', '001', '111')}


def _label(rgb, text, y, x, scale=2):
    """흰 바탕 위에 3x5 점 글꼴 숫자. (y, x) 는 왼쪽 위."""
    h, w = rgb.shape[:2]
    width = (len(text) * 4 + 1) * scale
    height = 7 * scale
    y0, x0 = min(max(y, 0), max(h - height, 0)), min(max(x, 0), max(w - width, 0))
    rgb[y0:y0 + height, x0:x0 + width] = WHITE
    for k, char in enumerate(text):
        for r, line in enumerate(_DIGITS[char]):
            for c, bit in enumerate(line):
                if bit == '1':
                    py, px = y0 + (1 + r) * scale, x0 + (1 + k * 4 + c) * scale
                    rgb[py:py + scale, px:px + scale] = LABEL_COLOUR


def _heat_lut():
    anchors = ((1, (255, 240, 150)), (128, (255, 170, 0)), (255, (150, 0, 0)))
    lut = [WHITE] + [None] * 255
    for (p0, c0), (p1, c1) in zip(anchors, anchors[1:]):
        for i in range(p0, p1 + 1):
            lut[i] = tuple(a + (b - a) * (i - p0) // (p1 - p0) for a, b in zip(c0, c1))
    return np.array(lut, dtype=np.uint8)


_LUT = _heat_lut()


def _heat(values, observed):
    """(블록 값, 관측 여부) → rgb, 최댓값. 0 은 흰색, 관측 밖 블록은 회색."""
    vmax = int(values.max()) if values.size else 0
    index = np.zeros(values.shape, dtype=np.int64)
    if vmax:
        index = np.where(values > 0, 1 + (values.astype(np.int64) * 254) // vmax, 0)
    rgb = _LUT[index]
    rgb[~observed] = GRAY
    return rgb, vmax


# --------------------------------------------------------------------------- 메모리 추정·자료 수집

def estimate_report_bytes(ref_shape, tgt_shape):
    """그림 생성 중 동시에 보유하는 bool 배열(바이트 수). 층 하나씩 처리하므로 층 수와 무관하다.

    hr = H_ref*W_ref, ht = H_tgt*W_tgt: 겹침 그림 한 장 = ref&valid, 이동한 target, 이동한 target valid, 비교 영역, 정렬 전 target
    = 4*hr + ht, 블록 축소 중간 배열 2*hr 이하(reduceat 는 한 축만 줄인 (ny, W) 배열) → 6*hr + ht.
    열지도 수집 중 uint32 지도의 reduceat 중간 배열은 4*hr 이하라 더 작다."""
    hr = int(ref_shape[0]) * int(ref_shape[1])
    ht = int(tgt_shape[0]) * int(tgt_shape[1])
    return 6 * hr + ht


def fits(ref_shape, tgt_shape, max_operation_bytes, reserved_bytes):
    return estimate_report_bytes(ref_shape, tgt_shape) <= max_operation_bytes - reserved_bytes


class ImageSource:
    """그림에 필요한 배열 묶음. 큰 지도는 변경 지도 단계에서 ``collect`` 로 블록 축소·crop 만 남기고 놓는다."""

    def __init__(self, ref_layers, tgt_layers, ref_valid, tgt_valid, offset_px):
        self.ref_layers, self.tgt_layers = dict(ref_layers), dict(tgt_layers)
        self.ref_valid, self.tgt_valid = ref_valid, tgt_valid
        self.offset_px = (int(offset_px[0]), int(offset_px[1]))
        self.step = _step_for(ref_valid.shape, OVERVIEW_MAX_PX)
        self.heat = {}
        self.observed = None
        self.hot = []

    def collect(self, maps, windows):
        """``change_maps`` 결과(maps)에서 열지도용 블록 값과 선정 창의 hotspot 합집합 crop 을 뽑는다. 호출 뒤 maps 를 놓아도 된다."""
        step = self.step
        self.observed = _block(np.asarray(maps['D'], dtype=bool), step, 'any')
        for layer in sorted(maps['maps']):
            self.heat[layer] = {'union': _block(np.asarray(maps['union_change'][layer], dtype=bool), step, 'sum'),
                                'count': _block(maps['maps'][layer], step, 'max')}
        height, width = self.ref_valid.shape
        for window in windows:
            y, x, w = window['window_origin_px'][0], window['window_origin_px'][1], window['window_px']
            box = _crop_box(y, x, w, height, width)
            y0, y1, x0, x1 = box
            layer = window['layer']
            self.hot.append({'box': box,
                             'ref': np.array(maps['union_ref'][layer][y0:y1, x0:x1], dtype=bool),
                             'tgt': np.array(maps['union_tgt'][layer][y0:y1, x0:x1], dtype=bool),
                             'obs': np.array(maps['D'][y0:y1, x0:x1], dtype=bool)})


def _crop_box(y, x, w, height, width):
    """창 + 50% 여백(한 변 양쪽 w//2)을 frame 으로 자른 (y0, y1, x0, x1)."""
    m = w // 2
    return max(y - m, 0), min(y + w + m, height), max(x - m, 0), min(x + w + m, width)


# --------------------------------------------------------------------------- 그림 만들기

def _overview_pair(source, name):
    """CNT 층 하나의 (정렬 전, 정렬 후) rgb. 비교 영역 밖은 회색, 모양은 회색 위에도 그린다."""
    dx, dy = source.offset_px
    height, width = source.ref_valid.shape
    step = source.step
    ref = np.asarray(source.ref_layers[name], dtype=bool) & source.ref_valid if name in source.ref_layers else None
    tgt = np.asarray(source.tgt_layers[name], dtype=bool) if name in source.tgt_layers else None
    empty = np.zeros(source.ref_valid.shape, dtype=bool)
    ref_b = _block(ref if ref is not None else empty, step, 'any')
    out = []
    for shifted in (False, True):
        if shifted:
            valid_t = change_map._shift_crop(source.tgt_valid, dy, dx, 0, height, 0, width)
            layer_t = (change_map._shift_crop(tgt, dy, dx, 0, height, 0, width) & valid_t) if tgt is not None else empty
        else:
            valid_t = _plain(source.tgt_valid, source.ref_valid.shape)
            layer_t = (_plain(tgt, source.ref_valid.shape) & valid_t) if tgt is not None else empty
        gray = ~_block(source.ref_valid & valid_t, step, 'any')
        out.append(_overlay(ref_b, _block(layer_t, step, 'any'), gray))
        del valid_t, layer_t
    return out


def _plain(array, shape):
    """target 배열을 reference 격자에 offset 없이 얹는다(모양이 다르면 겹치는 부분만)."""
    out = np.zeros(shape, dtype=bool)
    h, w = min(shape[0], array.shape[0]), min(shape[1], array.shape[1])
    out[:h, :w] = array[:h, :w]
    return out


def _heat_images(source, windows):
    """층마다 (합집합 변화 rgb, 최댓값, 조합별 잔차 합 rgb, 최댓값). 선정 창은 같은 층의 열지도에만 사각형과 순위로 그린다."""
    out = {}
    step = source.step
    for layer in sorted(source.heat):
        pair = []
        for key in ('union', 'count'):
            rgb, vmax = _heat(source.heat[layer][key], source.observed)
            for rank, window in enumerate(windows, start=1):
                if window['layer'] != layer:
                    continue
                y, x, w = window['window_origin_px'][0], window['window_origin_px'][1], window['window_px']
                y0, x0, y1, x1 = y // step, x // step, (y + w - 1) // step, (x + w - 1) // step
                _rect(rgb, y0, x0, y1, x1, RECT_COLOUR)
                _label(rgb, str(rank), y0 + 1, x0 + 1)
            pair.append((rgb, vmax))
        out[layer] = pair
    return out


def _fit(rgb_inputs, limit):
    """crop 한 장을 한 변 <= limit 로 만든다. 크면 블록 any, 작으면 정수 배 확대. (ref, tgt, gray, rect) → (rgb, scale 정보)"""
    ref, tgt, gray, rect = rgb_inputs
    size = max(ref.shape)
    y0, x0, y1, x1 = rect
    if size > limit:
        step = int(math.ceil(size / float(limit)))
        ref, tgt, gray = _block(ref, step, 'any'), _block(tgt, step, 'any'), _block(gray, step, 'any')
        rect = (y0 // step, x0 // step, y1 // step, x1 // step)
    else:
        scale = max(1, limit // size)
        if scale > 1:
            ref, tgt, gray = (np.repeat(np.repeat(a, scale, axis=0), scale, axis=1) for a in (ref, tgt, gray))
            rect = (y0 * scale, x0 * scale, (y1 + 1) * scale - 1, (x1 + 1) * scale - 1)
    rgb = _overlay(ref, tgt, gray)
    _rect(rgb, *rect, RECT_COLOUR)
    return rgb


def _crop_images(source, window, hot, limit):
    """ROI 하나의 [(제목, rgb)]: CNT 층별 겹침(정렬 후) + hotspot 합집합 reference/target 겹침."""
    dx, dy = source.offset_px
    y0, y1, x0, x1 = hot['box']
    y, x, w = window['window_origin_px'][0], window['window_origin_px'][1], window['window_px']
    rect = (y - y0, x - x0, y + w - 1 - y0, x + w - 1 - x0)
    images = []
    names = sorted(set(source.ref_layers) & set(source.tgt_layers))
    if names:
        valid_t = change_map._shift_crop(source.tgt_valid, dy, dx, y0, y1, x0, x1)
        gray = ~(source.ref_valid[y0:y1, x0:x1] & valid_t)
        for name in names:
            ref = np.asarray(source.ref_layers[name][y0:y1, x0:x1], dtype=bool) & source.ref_valid[y0:y1, x0:x1]
            tgt = change_map._shift_crop(np.asarray(source.tgt_layers[name], dtype=bool), dy, dx, y0, y1, x0, x1) & valid_t
            images.append((name + ' (정렬 후)', _fit((ref, tgt, gray, rect), limit)))
    images.append(('hotspot 합집합 (' + window['layer'] + ')', _fit((hot['ref'], hot['tgt'], ~hot['obs'], rect), limit)))
    return images


# --------------------------------------------------------------------------- HTML

STYLE = (
    'body{font-family:system-ui,sans-serif;max-width:1400px;margin:1.5rem auto;padding:0 1rem;color:#222}'
    'table{border-collapse:collapse;margin:.5rem 0}td,th{border:1px solid #ccc;padding:3px 8px;text-align:left;vertical-align:top;font-size:13px}'
    'th{background:#f3f3f3}.warn{border:2px solid #c00;background:#fff0f0;padding:.6rem 1rem;font-weight:bold}'
    '.note{color:#555;font-size:13px}.figs{display:flex;flex-wrap:wrap;gap:12px}figure{margin:0}'
    'figure img{max-width:100%;border:1px solid #999;image-rendering:pixelated}figcaption{font-size:12px;color:#555}'
    '.roi{border-top:2px solid #888;margin-top:1.2rem;padding-top:.4rem}.missing{border:1px dashed #999;color:#777;padding:.4rem;font-size:12px}'
    '.sw{display:inline-block;width:.9em;height:.9em;vertical-align:middle;border:1px solid #666}')


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _cell(value):
    if isinstance(value, float):
        return escape(_json(value))
    if isinstance(value, (list, dict)):
        return escape(_json(value))
    return escape('—' if value is None else str(value))


def _table(header, rows):
    out = ['<table><tr>' + ''.join('<th>' + escape(h) + '</th>' for h in header) + '</tr>']
    for row in rows:
        out.append('<tr>' + ''.join('<td>' + (c if isinstance(c, _Raw) else _cell(c)) + '</td>' for c in row) + '</tr>')
    out.append('</table>')
    return ''.join(out)


class _Raw(str):
    """이미 escape 한 HTML 조각."""


def _figure(uri, caption):
    return '<figure><img src="' + uri + '" alt="' + escape(caption) + '"><figcaption>' + escape(caption) + '</figcaption></figure>'


def reason_text(code):
    if code in REASON_TEXT:
        return REASON_TEXT[code]
    for prefix, side in (('cnt_empty_reference_', 'reference'), ('cnt_empty_target_', 'target')):
        if code.startswith(prefix):
            return side + ' 의 CNT 층 ' + code[len(prefix):] + ' 이 비어 있어 그 층은 정렬에서 뺐습니다.'
    return code


def _legend():
    return ('<p class="note">색: <span class="sw" style="background:rgb%s"></span> reference 만 · '
            '<span class="sw" style="background:rgb%s"></span> target 만 · '
            '<span class="sw" style="background:rgb%s"></span> 둘 다 겹침 · '
            '<span class="sw" style="background:rgb%s"></span> 비교 영역 밖(회색). 초록 사각형 = 선정 ROI 창.</p>'
            % (REF_COLOUR, TGT_COLOUR, BOTH_COLOUR, GRAY))


def _summary(alignment):
    status = alignment['status']
    rows = [
        ('status', _Raw(escape(STATUS_TEXT.get(status, status)) + ' <code>' + escape(status) + '</code>')),
        ('reasons', _Raw('<br>'.join('<code>' + escape(c) + '</code> ' + escape(reason_text(c)) for c in alignment['reasons']) or '—')),
        ('offset_xy_um', alignment['offset_xy_um']), ('offset_xy_px', alignment['offset_xy_px']),
        ('부호', _Raw('<code>' + escape(alignment.get('sign') or 'p_target = p_reference + offset') + '</code>')),
        ('dice_best', alignment['dice_best']), ('dice_identity', alignment['dice_identity']),
        ('peak_ratio', alignment['peak_ratio']),
        ('best_plateau', {'best_plateau_px': alignment.get('best_plateau_px'),
                          'best_plateau_extent_px': alignment.get('best_plateau_extent_px')}),
        ('pattern_period_vectors_um', alignment['pattern_period_vectors_um']),
        ('layers_used', alignment['layers_used']), ('layers_excluded', alignment['layers_excluded']),
        ('overlap_area_um2', alignment['overlap_area_um2']), ('search_um', alignment['search_um']),
        ('pitch_um', alignment.get('pitch_um'))]
    parts = ['<h2>1. 요약</h2>']
    if status != 'aligned':
        parts.append('<p class="warn">경고: 정렬이 불확실합니다(alignment_uncertain). 상태는 ' + escape(STATUS_TEXT.get(status, status)) +
                     '입니다. 아래 변경 위치는 best-effort 이며 정렬이 틀렸으면 배치 이동 전체가 "변경"으로 잡힐 수 있습니다. '
                     '그림으로 정렬 후 겹침을 먼저 확인하십시오.</p>')
    parts.append(_table(['항목', '값'], rows))
    return ''.join(parts)


def _alternatives(alignment):
    alts = alignment['alternatives']
    parts = ['<h2>2. 대안 offset</h2>']
    if 'peak_ambiguous' in alignment['reasons']:
        parts.append('<p class="warn">정렬이 반복 주기만큼 틀렸을 수 있습니다(peak_ambiguous): 아래 대안 offset 이 최적과 비슷한 점수입니다.</p>')
    if not alts:
        parts.append('<p class="note">최적과 2 µm 이상 떨어진 별개 peak 가 없습니다.</p>')
    else:
        parts.append(_table(['offset_xy_um', 'offset_xy_px', 'dice', 'at_search_edge'],
                            [(a['offset_xy_um'], a.get('offset_xy_px'), a['dice'], a['at_search_edge']) for a in alts]))
    return ''.join(parts)


def _roi_text(rank, query, window):
    flags = query['flags']
    head = [('score_um2 (합집합 변화, 순위 기준)', query['score_um2']), ('score_layer', query['score_layer']),
            ('sum_residual_um2', query['sum_residual_um2']), ('changed_case_count', query['changed_case_count']),
            ('union_delta_um2', query['union_delta_um2']), ('union_area_reference_um2', query['union_area_reference_um2']),
            ('union_area_target_um2', query['union_area_target_um2']), ('observed_fraction', query['observed_fraction']),
            ('flags', _Raw('<br>'.join('<code>' + escape(f) + '</code> ' + escape(FLAG_TEXT.get(f, '')) for f in flags) or '—')),
            ('cnt_change_um2_by_layer', query['cnt_change_um2_by_layer'])]
    cases = [(c['case_key'], c['change_kind'], c['residual_um2'], c['iou_global'], c['iou_local']) for c in query['contributing_cases']]
    title = ('<h3>순위 %d · 층 %s · 중심 %s µm · <code>%s</code></h3>'
             % (rank, escape(str(window['layer'])), escape(_json(query['center_xy_um'])), escape(str(query['region_id']))))
    return title, _table(['항목', '값'], head) + _table(['기여 조합 case_key', 'change_kind', 'residual_um2', 'iou_global', 'iou_local'], cases)


def _footnotes():
    return ('<h2>6. 각주</h2><ul class="note">'
            '<li>순위는 층별 합집합 변화(<code>score_um2</code>, 6회차 Δ_union 과 같은 기준)이다. 층 사이 합집합은 만들지 않는다.</li>'
            '<li>조합별 잔차 합(<code>sum_residual_um2</code>)과 변경 case 수(<code>changed_case_count</code>)는 순위가 아니라 속성이다.</li>'
            '<li>union 은 짝지어진 조합만으로 계산한다(<code>union_basis: paired_cases_only</code>). 한쪽에만 있는 case 는 들어가지 않는다.</li>'
            '<li>한 ROI 의 직접 쌍들은 중심을 공유한다(같은 창에 기여한 조합마다 쌍이 하나씩이다).</li>'
            '<li>열지도는 블록 축소한 값이다: 합집합 변화는 블록 안 변화 칸 수, 조합별 잔차 합은 블록 안 최댓값(겹친 조합 수).</li>'
            '</ul>')


def build_alignment_html(alignment, *, queries, windows, source=None, max_bytes=None, skipped_reason=None):
    """alignment: ``alignment.json`` 문서(dict). queries: reference_to_target 질의 목록(순위 순), windows: 같은 순서의 창 후보.

    source: ``ImageSource`` (``collect`` 를 마친 것) 또는 None(그림 없음). skipped_reason: 그림을 만들지 않은 이유(문장).
    반환 ``(html 바이트, {'path', 'bytes', 'downscaled', 'crops_omitted', 'n_images'})``."""
    limit = MAX_HTML_BYTES if max_bytes is None else max_bytes
    n_images = 0
    prefix = [_summary(alignment), _alternatives(alignment)]
    # 3. 층별 전체 겹침
    prefix.append('<h2>3. 층별 전체 겹침 (정렬 전 / 정렬 후)</h2>')
    if source is None:
        prefix.append('<p class="note">그림을 만들지 않았습니다: ' + escape(skipped_reason or '') + '</p>')
    else:
        prefix.append(_legend())
        names = sorted(set(source.ref_layers) | set(source.tgt_layers))
        if not names:
            prefix.append('<p class="note">CNT 층 영상이 없어 겹침 그림이 없습니다.</p>')
        for name in names:
            before, after = _overview_pair(source, name)
            figs = []
            for caption, rgb in (('정렬 전 (offset 0)', before), ('정렬 후 (offset ' + _json(alignment['offset_xy_px']) + ' px)', after)):
                figs.append(_figure(_uri(_png(rgb)), name + ' · ' + caption))
                n_images += 1
            prefix.append('<h3>CNT ' + escape(name) + '</h3><div class="figs">' + ''.join(figs) + '</div>')
    # 4. 열지도
    prefix.append('<h2>4. 변경 지도 열지도 (hotspot 층별)</h2>')
    if source is None:
        prefix.append('<p class="note">그림을 만들지 않았습니다.</p>')
    elif not source.heat:
        prefix.append('<p class="note">짝지어진 case 가 없어 열지도가 없습니다.</p>')
    else:
        prefix.append('<p class="note">흰색 = 변화 없음, 노랑→빨강 = 많음, 회색 = 비교 영역 밖. 초록 사각형과 숫자 = 선정 ROI 창과 순위.</p>')
        heat = _heat_images(source, windows)
        for layer in sorted(heat):
            figs = []
            for (rgb, vmax), caption in zip(heat[layer], ('합집합 변화 RU (블록당 변화 칸 수, 최대 %d)', '조합별 잔차 합 count_map (블록 최대 겹침 수, 최대 %d)')):
                figs.append(_figure(_uri(_png(rgb)), '층 ' + layer + ' · ' + caption % vmax))
                n_images += 1
            prefix.append('<h3>층 ' + escape(layer) + '</h3><div class="figs">' + ''.join(figs) + '</div>')
    prefix.append('<h2>5. 선정 ROI 상세 (순위 순)</h2>')
    if not queries:
        prefix.append('<p class="note">선정된 ROI 가 없습니다.</p>')
    suffix = _footnotes()

    def encode(text):
        return text.encode('utf-8')
    head = ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>alignment.html</title><style>' + STYLE +
            '</style></head><body><h1>CNT 정렬과 변경 지도 확인 (alignment_report)</h1>'
            '<p class="note">정렬이 불확실해도 best-effort 로 진행한 결과를 눈으로 확인하는 보고서입니다. 용어는 출력 키(alignment.json, '
            'candidate_queries_v1.json) 그대로 병기합니다.</p>')
    tail = '</body></html>\n'
    fixed = encode(head + ''.join(prefix)) + encode(suffix + tail)

    sections = {}      # (rank, level) → bytes; level 0 full, 1 half, 2 none

    def section(i, level):
        if (i, level) in sections:
            return sections[(i, level)]
        title, text = _roi_text(i + 1, queries[i], windows[i])
        figs = ''
        if level < 2 and source is not None and i < len(source.hot):
            side = CROP_MAX_PX if level == 0 else CROP_HALF_PX
            items = _crop_images(source, windows[i], source.hot[i], side)
            figs = '<div class="figs">' + ''.join(_figure(_uri(_png(rgb)), caption) for caption, rgb in items) + '</div>'
        elif level == 2 and source is not None:
            figs = '<p class="missing">그림 생략: 파일 크기 상한(%d MiB) 때문에 이 ROI 의 crop 그림을 뺐습니다.</p>' % (limit // (1024 * 1024))
        elif source is None:
            figs = '<p class="missing">그림을 만들지 않았습니다.</p>'
        sections[(i, level)] = encode('<div class="roi">' + title + figs + text + '</div>')
        return sections[(i, level)]

    count = len(queries) if source is not None else 0
    if source is None:
        level, keep = 2, 0
        body = [section(i, 2) for i in range(len(queries))]
        downscaled, omitted = False, []
    else:
        downscaled = False
        total = len(fixed) + sum(len(section(i, 0)) for i in range(count))
        level, keep = 0, count
        if total > limit:
            downscaled, level = True, 1
            sizes = [len(section(i, 1)) for i in range(count)]
            none = [len(section(i, 2)) for i in range(count)]
            keep = count
            while keep > 0 and len(fixed) + sum(sizes[:keep]) + sum(none[keep:]) > limit:
                keep -= 1
        body = [section(i, level) for i in range(keep)] + [section(i, 2) for i in range(keep, count)]
        omitted = list(range(keep + 1, count + 1))
        n_images += sum(section_images(source, i) for i in range(keep))
    prefix_bytes = encode(head + ''.join(prefix))
    data = prefix_bytes + b''.join(body) + encode(suffix + tail)
    return data, {'path': 'alignment.html', 'bytes': len(data), 'downscaled': bool(downscaled),
                  'crops_omitted': omitted, 'n_images': n_images}


def section_images(source, i):
    """ROI i 의 crop 그림 장수 = CNT 공통 층 수 + hotspot 1."""
    return len(set(source.ref_layers) & set(source.tgt_layers)) + 1
