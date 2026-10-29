"""change-roi/T8: ``candidates-change`` 의 ``alignment.html`` — 정렬 결과와 변경 지도를 사람이 눈으로 확인하는 보고서.

한 파일, PNG 는 base64 data URI, ``<script>`` 와 외부 URL 이 없어 ``file://`` 로 열린다. 보고서만 만든다: 계산·순위·export 는 바꾸지 않는다.
numpy·표준 라이브러리만 쓴다(PNG 는 zlib 으로 직접 쓰고, 글자는 3x5 점 글꼴이라 Pillow·글꼴 버전에 따라 바이트가 달라지지 않는다).

change-roi-v2/T3a: 절 순서는 제목 줄 / 1 요약 / 2 CNT 정렬과 정렬 진단 / 3 변경 강조 지도 / 4 ROI 목록 / 5 ROI 상세 / 6 열지도 / 7 각주.

그림 (층 사이 hotspot 합집합은 만들지 않는다)
- 2a 전체 CNT 층 겹침(정렬 전 / 정렬 후): 모든 CNT 층을 한 장에. 층마다 고정 색표의 명도(최대 SHADE_LEVELS 단계, 넘으면 반복).
  정렬 전은 reference 파랑 계열 / target 주황 계열(겹치면 미리 섞은 색), 정렬 후는 같은 픽셀 = 회색~검정, 한쪽만 = 파랑·주황, 비교 영역 밖 = 회색.
- 2b 층별 CNT 겹침: CNT 층마다 정렬 전·후 두 장(접어 둠).
- 3 변경 강조 지도: 층별 hotspot 합집합 변화의 층 OR 을 빨강, 변경 없는 패턴 연회색, 비교 영역 밖 진회색, 선정 ROI 초록 사각형·순위.
- 5 ROI 상세: frame 전체에 변경 위치(연빨강)·사분면 경계선과 이 ROI 만 굵은 사각형인 위치 그림(한 변 <= POSITION_MAX_PX)과, 이 ROI 에 불량 조합이 있는 층마다 한 묶음
  (change-roi-v2/T3b): [비교, reference, target] 세 그림(한 변 <= CROP_MAX_PX). 바탕 DDDDDD = 그 모델의 그 층 전극 PNG 합집합(target 은 offset 으로
  reference 좌표에 옮김), 색 = 그 층 hotspot 합집합. 층이 MAX_BUNDLES 를 넘으면 창 안 변경 면적 합이 큰 층부터 MAX_BUNDLES 개. 전극 PNG 가 없으면 바탕 없이 그린다.
- 6 열지도: hotspot 층마다 합집합 변화(RU)와 조합별 잔차 합(count_map)(접어 둠).
모든 PNG 는 고정 색표와 미리 섞은 색만 쓴다(반투명 없음).

크기: 직렬화한 실제 바이트가 ``MAX_HTML_BYTES`` 를 넘으면 ROI crop 을 반 해상도로, 그래도 넘으면 순위 낮은 ROI 의 crop 부터 뺀다
(요약 표·전체 겹침·위치 그림·열지도는 빼지 않는다). 같은 입력은 같은 바이트다(시각·절대 경로 없음).
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
POSITION_MAX_PX = 250
SHADE_LEVELS = 6
MAX_BUNDLES = 4                                 # ROI 하나에 그리는 층 묶음 수 상한
HEAT_LAYERS_ASSUMED = 8                         # 메모리 추정에서 hotspot 층 수를 모를 때 쓰는 보수적 상수
SCHEMA = 'alignment_report_v1'

# 2a 고정 색표(밝음 → 어두움). 층 순서(이름 자연 정렬)대로 SHADE_LEVELS 단계, 넘으면 처음부터 반복한다.
REF_SHADES = ((190, 208, 246), (137, 168, 236), (80, 122, 220), (40, 90, 200), (24, 62, 150), (14, 38, 100))
TGT_SHADES = ((250, 214, 170), (245, 184, 108), (235, 150, 50), (205, 115, 15), (150, 80, 8), (100, 52, 4))
SAME_SHADES = ((150, 150, 150), (120, 120, 120), (90, 90, 90), (62, 62, 62), (36, 36, 36), (10, 10, 10))
OUTSIDE_GRAY = (200, 200, 200)                  # 비교 영역 밖(C8C8C8)
MAP_BG = (240, 240, 240)                        # 3절 바탕
MAP_PATTERN = (208, 208, 208)                   # 변경 없는 패턴
MAP_OUTSIDE = (160, 160, 160)                   # 비교 영역 밖
MAP_CHANGED = (224, 32, 32)                     # 변경(빨강)
POS_BG = (244, 244, 244)
POS_LINE = (150, 150, 150)                      # 위치 그림의 frame 사분면 경계선
POS_CHANGED = (246, 170, 170)                   # 위치 그림의 변경 위치(연빨강)
POS_RECT = (224, 32, 32)
POS_MIN_RECT_PX = 7                             # 위치 그림에서 ROI 사각형의 최소 한 변

REF_COLOUR = (40, 90, 220)
TGT_COLOUR = (235, 140, 20)
BOTH_COLOUR = (30, 30, 30)
GRAY = (205, 205, 205)
WHITE = (255, 255, 255)
BASE_COLOUR = (221, 221, 221)                   # 층 묶음의 전극 형상 바탕(DDDDDD)
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

CASE_FLAGS = ('new_electrode', 'removed_electrode')      # 기여 case 표의 전극 표시 칸에 싣는 flag

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


def _overlay(ref, tgt, gray, base=None):
    """base: 전극 형상(None 이면 바탕 없음). 그리는 순서: 흰색, 전극 형상, 비교 영역 밖 회색, hotspot 색."""
    rgb = np.empty(ref.shape + (3,), dtype=np.uint8)
    rgb[:] = WHITE
    if base is not None:
        rgb[base] = BASE_COLOUR
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

def estimate_report_bytes(ref_shape, tgt_shape, n_rois=0, n_layers=HEAT_LAYERS_ASSUMED, window_px=0):
    """그림 생성 중 동시에 보유하는 배열(바이트 수). 2a 는 층 하나씩 처리하므로 그 부분은 층 수와 무관하다.

    hr = H_ref*W_ref, ht = H_tgt*W_tgt, nb = 블록 축소한 그림의 칸 수(ceil(H/step)*ceil(W/step), step = _step_for(ref, OVERVIEW_MAX_PX)).
    - 2a 정렬 후 한 층: ref&비교 영역, 이동한 target, 이동한 target valid, 비교 영역, 같은 칸, reference 만, target 만 = 7*hr,
      블록 축소 중간 배열 2*hr 이하(reduceat 는 한 축만 줄인 (ny, W) 배열) → 9*hr, 정렬 전 target 한 장분 ht.
    - 2a 두 장의 층 인덱스·rgb 와 3절·6절 블록 지도(블록 단위 uint8/bool/uint32/rgb 합쳐 칸당 24 바이트 이하) → 24*nb.
    - ImageSource.heat 는 hotspot 층마다 합집합 변화(uint32)와 조합별 잔차 합(최대 uint32) 블록 지도를 놓는다 → n_layers * 8*nb.
      호출부(change_candidates)가 두 dataset 의 실제 hotspot 층 수를 넘긴다. 모를 때만 보수적 상수 HEAT_LAYERS_ASSUMED 를 쓴다.
    - ROI 위치 그림은 ROI 마다 따로 만들어 바이트로 바꾼 뒤 놓는다: 바탕 rgb 와 그림 하나, 블록 배열 → n_rois > 0 이면 4*POSITION_MAX_PX**2 (ROI 수와 무관).
    - ROI crop(window_px > 0 일 때): 상자 한 변 <= 2*window_px 로 ROI 마다 비교 영역 bool 1개 + hotspot 층마다 reference·target bool 2개 +
      층 묶음 바탕 최대 MAX_BUNDLES 층 x 2개 = (2*n_layers + 1 + 2*MAX_BUNDLES) 개를 잡는다.
    식: 9*hr + ht + 24*nb + n_layers*8*nb + (4*POSITION_MAX_PX**2 if n_rois) + n_rois * min(hr, (2*window_px)**2) * (2*n_layers + 1 + 2*MAX_BUNDLES).
    열지도 수집 중 uint32 지도의 reduceat 중간 배열은 4*hr 이하라 더 작다."""
    hr = int(ref_shape[0]) * int(ref_shape[1])
    ht = int(tgt_shape[0]) * int(tgt_shape[1])
    step = _step_for(ref_shape, OVERVIEW_MAX_PX)
    nb = int(math.ceil(ref_shape[0] / float(step))) * int(math.ceil(ref_shape[1] / float(step)))
    total = 9 * hr + ht + 24 * nb + int(n_layers) * 8 * nb
    if n_rois:
        total += 4 * POSITION_MAX_PX ** 2
        if window_px:
            total += int(n_rois) * min(hr, (2 * int(window_px)) ** 2) * (2 * int(n_layers) + 1 + 2 * MAX_BUNDLES)
    return total


def fits(ref_shape, tgt_shape, max_operation_bytes, reserved_bytes, n_rois=0, n_layers=HEAT_LAYERS_ASSUMED, window_px=0):
    return estimate_report_bytes(ref_shape, tgt_shape, n_rois, n_layers, window_px) <= max_operation_bytes - reserved_bytes


def electrode_read_bytes(ref_shape, tgt_shape):
    """층 묶음 바탕을 읽을 때 동시에 보유하는 최대 바이트(한 번에 (모델, 층) 하나씩 읽는다).

    DL1 전극 PNG 는 frame 보다 사방 500 px 큰 이미지다(fw = (H + 1000)*(W + 1000)). 디코드 정점은 16*fw 로 잡는다: RGBA 4 바이트,
    ``convert('RGBA')`` 사본 4 바이트, rgb 비교 중간 배열·검정 판정·marked 와 PNG 원본 바이트·중간 사본 여유. 여기에 frame 크기 bool
    합집합 하나와 crop 사본 둘(2*H*W)을 더한다. 두 모델 중 큰 쪽. ImageSource 가 이미 쥔 바이트는 ``electrode_fits`` 의 ``held_bytes`` 로 뺀다."""
    return max(16 * (int(s[0]) + 1000) * (int(s[1]) + 1000) + 2 * int(s[0]) * int(s[1]) for s in (ref_shape, tgt_shape))


def electrode_fits(ref_shape, tgt_shape, max_operation_bytes, reserved_bytes, held_bytes=0):
    """held_bytes: ImageSource 가 이미 보유한 바이트(``ImageSource.held_bytes()``: 열지도 블록, hotspot crop)."""
    return electrode_read_bytes(ref_shape, tgt_shape) <= max_operation_bytes - reserved_bytes - int(held_bytes)


class ImageSource:
    """그림에 필요한 배열 묶음. 큰 지도는 변경 지도 단계에서 ``collect`` 로 블록 축소·crop 만 남기고 놓는다."""

    def __init__(self, ref_layers, tgt_layers, ref_valid, tgt_valid, offset_px):
        self.ref_layers, self.tgt_layers = dict(ref_layers), dict(tgt_layers)
        self.ref_valid, self.tgt_valid = ref_valid, tgt_valid
        self.offset_px = (int(offset_px[0]), int(offset_px[1]))
        self.step = _step_for(ref_valid.shape, OVERVIEW_MAX_PX)
        self.heat = {}
        self.observed = None
        self.hot_any = None       # 모든 hotspot 층의 reference ∪ target 합집합 블록(3절의 연회색 패턴)
        self.hot = []             # ROI 순서: {'box', 'obs', 'layers': {층: {'ref', 'tgt'}}}
        self.base = {}            # (ROI 번호, 층, 'ref' | 'tgt') → 상자 크기 전극 형상 bool 또는 None (collect_bases)
        self.base_reason = {}     # (층, 'ref' | 'tgt') → 바탕이 없는 사유 문구(호출자가 채운다). 없으면 '파일 없음'
        self.base_errors = {}     # (층, 'ref' | 'tgt') → 전극 PNG 읽기 오류 코드 목록(호출자가 채운다; 국소 정렬 진단 전용)
        self.diag_wide = {}       # (ROI 번호, 층) → 상자를 사방 diag_margin 넓힌 target 전극 bool 또는 None (국소 정렬 진단 전용)

    def collect(self, maps, windows):
        """``change_maps`` 결과(maps)에서 열지도용 블록 값과 선정 창의 hotspot 합집합 crop 을 뽑는다. 호출 뒤 maps 를 놓아도 된다."""
        step = self.step
        self.observed = _block(np.asarray(maps['D'], dtype=bool), step, 'any')
        for layer in sorted(maps['maps']):
            self.heat[layer] = {'union': _block(np.asarray(maps['union_change'][layer], dtype=bool), step, 'sum'),
                                'count': _block(maps['maps'][layer], step, 'max')}
            if layer in maps['union_ref'] and layer in maps['union_tgt']:
                any_layer = _block(np.asarray(maps['union_ref'][layer], dtype=bool) |
                                   np.asarray(maps['union_tgt'][layer], dtype=bool), step, 'any')
                self.hot_any = any_layer if self.hot_any is None else (self.hot_any | any_layer)
        height, width = self.ref_valid.shape
        for window in windows:
            y, x, w = window['window_origin_px'][0], window['window_origin_px'][1], window['window_px']
            box = _crop_box(y, x, w, height, width)
            y0, y1, x0, x1 = box
            # 층 묶음에 쓸 층은 기여 계산 뒤에 정해지므로, 상자 안에 hotspot 이 있는 모든 층의 crop 을 모은다(상자 크기라 작다).
            layers = {}
            for layer in sorted(maps['union_ref']):
                if layer not in maps['union_tgt']:
                    continue
                ref = np.array(maps['union_ref'][layer][y0:y1, x0:x1], dtype=bool)
                tgt = np.array(maps['union_tgt'][layer][y0:y1, x0:x1], dtype=bool)
                if ref.any() or tgt.any() or layer == window['layer']:
                    layers[layer] = {'ref': ref, 'tgt': tgt}
            self.hot.append({'box': box, 'layers': layers, 'obs': np.array(maps['D'][y0:y1, x0:x1], dtype=bool)})

    def held_bytes(self):
        """``collect`` 가 보유 중인 배열 바이트(열지도 블록, 관측 블록, hotspot crop). 전극 PNG 읽기 admission 이 한도에서 뺀다."""
        total = sum(int(a.nbytes) for a in (self.observed, self.hot_any) if a is not None)
        for item in self.heat.values():
            total += sum(int(a.nbytes) for a in item.values())
        for hot in self.hot:
            total += int(hot['obs'].nbytes)
            for crops in hot['layers'].values():
                total += int(crops['ref'].nbytes) + int(crops['tgt'].nbytes)
        return total

    def collect_bases(self, wanted, load_reference, load_target, diag_margin=0):
        """층 묶음 바탕(전극 형상) crop. wanted: ``{층: [ROI 번호(0 부터)]}``, load_*(층) → frame 크기 bool 또는 None(전극 PNG 없음).

        diag_margin > 0 이면(국소 정렬 진단) 같은 frame 을 읽는 김에 target 쪽 상자를 사방 diag_margin px 넓힌 crop 도
        ``self.diag_wide[(ROI, 층)]`` 에 남긴다(추가 파일 읽기 없음). 바탕 그림에는 영향이 없다.

        (모델, 층) 하나씩 frame bool 을 읽어 필요한 모든 ROI 상자를 잘라 두고 바로 놓는다. target 은 hotspot 과 같은
        ``change_map._shift_crop`` 으로 reference 좌표에 옮긴다. 결과는 ``self.base[(ROI, 층, 'ref'|'tgt')]`` (없으면 None)."""
        dx, dy = self.offset_px
        for layer in sorted(wanted):
            for side, load in (('ref', load_reference), ('tgt', load_target)):
                frame = load(layer)
                for i in wanted[layer]:
                    y0, y1, x0, x1 = self.hot[i]['box']
                    if frame is None:
                        crop = None
                    elif side == 'ref':
                        crop = np.array(frame[y0:y1, x0:x1], dtype=bool)
                        if crop.shape != (y1 - y0, x1 - x0):          # frame 이 reference valid 보다 작으면 모자란 쪽은 형상 없음
                            full = np.zeros((y1 - y0, x1 - x0), dtype=bool)
                            full[:crop.shape[0], :crop.shape[1]] = crop
                            crop = full
                    else:
                        crop = change_map._shift_crop(frame, dy, dx, y0, y1, x0, x1)
                        if diag_margin:
                            m = int(diag_margin)
                            self.diag_wide[(i, layer)] = change_map._shift_crop(frame, dy, dx, y0 - m, y1 + m, x0 - m, x1 + m)
                    if side == 'tgt' and frame is None and diag_margin:
                        self.diag_wide[(i, layer)] = None
                    self.base[(i, layer, side)] = crop
                del frame


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


def natural_key(name):
    """층 이름 자연 정렬 키: CNT2 < CNT10."""
    import re
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', str(name))]


def _shade(ref_idx, tgt_idx, same_idx, gray):
    """층 인덱스 배열(없음 = -1) → rgb. 같은 칸은 SAME_SHADES, 한쪽만은 REF/TGT_SHADES.
    한 칸에 reference 만·target 만이 같이 있으면 정렬 전(same_idx 없음)은 미리 섞은 평균색, 정렬 후는 순수 target 색이다."""
    rgb = np.empty(ref_idx.shape + (3,), dtype=np.uint8)
    rgb[:] = WHITE
    rgb[gray] = OUTSIDE_GRAY
    ref_t, tgt_t, same_t = (np.array(t, dtype=np.uint8) for t in (REF_SHADES, TGT_SHADES, SAME_SHADES))
    if same_idx is not None:
        m = same_idx >= 0
        rgb[m] = same_t[same_idx[m]]
    r, t = ref_idx >= 0, tgt_idx >= 0
    rgb[r & ~t] = ref_t[ref_idx[r & ~t]]
    rgb[t & ~r] = tgt_t[tgt_idx[t & ~r]]
    both = r & t
    if both.any():
        if same_idx is not None:        # 정렬 후: reference 만·target 만 픽셀이 한 칸에 같이 있으면 평균 색(같음처럼 보임) 대신 순수 target 주황
            rgb[both] = tgt_t[tgt_idx[both]]
        else:                           # 정렬 전: 겹치면 미리 섞은 색
            rgb[both] = ((ref_t[ref_idx[both]].astype(np.uint16) + tgt_t[tgt_idx[both]]) // 2).astype(np.uint8)
    return rgb


def _overview_all(source, names):
    """모든 CNT 층을 한 장에 겹친 (정렬 전 rgb, 정렬 후 rgb, reference 패턴 블록). 층마다 한 번에 하나씩만 전체 해상도 배열을 쓴다.

    정렬 후는 비교 영역(reference valid ∩ 이동한 target valid) 안만 비교하고 밖은 회색이다. 한 칸에 변경(한쪽만)과 같은 모양이 같이 있으면 변경이 위에 온다."""
    dx, dy = source.offset_px
    height, width = source.ref_valid.shape
    step = source.step
    shape = (int(math.ceil(height / float(step))), int(math.ceil(width / float(step))))
    empty = np.zeros(source.ref_valid.shape, dtype=bool)
    valid_p = _plain(source.tgt_valid, source.ref_valid.shape)
    valid_s = change_map._shift_crop(source.tgt_valid, dy, dx, 0, height, 0, width)
    comp = source.ref_valid & valid_s
    gray_after = ~_block(comp, step, 'any')
    pattern = np.zeros(shape, dtype=bool)
    idx = {key: np.full(shape, -1, dtype=np.int8) for key in ('ref_b', 'tgt_b', 'ref_a', 'tgt_a', 'same_a')}
    for k, name in enumerate(names):
        level = k % SHADE_LEVELS
        ref = (np.asarray(source.ref_layers[name], dtype=bool) & source.ref_valid) if name in source.ref_layers else empty
        tgt_p = (_plain(np.asarray(source.tgt_layers[name], dtype=bool), source.ref_valid.shape) & valid_p
                 if name in source.tgt_layers else empty)
        ref_b, tgt_b = _block(ref, step, 'any'), _block(tgt_p, step, 'any')
        idx['ref_b'][ref_b] = level
        idx['tgt_b'][tgt_b] = level
        pattern |= ref_b
        del tgt_p
        tgt_s = (change_map._shift_crop(np.asarray(source.tgt_layers[name], dtype=bool), dy, dx, 0, height, 0, width) & comp
                 if name in source.tgt_layers else empty)
        ref_c = ref & comp
        idx['same_a'][_block(ref_c & tgt_s, step, 'any')] = level
        idx['ref_a'][_block(ref_c & ~tgt_s, step, 'any')] = level
        idx['tgt_a'][_block(tgt_s & ~ref_c, step, 'any')] = level
        del ref, ref_c, tgt_s
    none = np.zeros(shape, dtype=bool)
    before = _shade(idx['ref_b'], idx['tgt_b'], None, none)
    after = _shade(idx['ref_a'], idx['tgt_a'], idx['same_a'], gray_after)
    return before, after, pattern


def _change_map_image(source, windows, pattern):
    """3절: 층별 hotspot 합집합 변화의 층 OR = 빨강, 변경 없는 패턴 연회색, 비교 영역 밖 진회색, 선정 ROI 초록 사각형·순위."""
    step = source.step
    observed = source.observed
    shape = observed.shape
    changed = np.zeros(shape, dtype=bool)
    for layer in source.heat:
        changed |= source.heat[layer]['union'] > 0
    quiet = np.zeros(shape, dtype=bool)
    if pattern is not None:
        quiet |= pattern
    if source.hot_any is not None:
        quiet |= source.hot_any
    rgb = np.empty(shape + (3,), dtype=np.uint8)
    rgb[:] = MAP_BG
    rgb[quiet] = MAP_PATTERN
    rgb[~observed] = MAP_OUTSIDE
    rgb[changed & observed] = MAP_CHANGED
    for rank, window in enumerate(windows, start=1):
        y, x, w = window['window_origin_px'][0], window['window_origin_px'][1], window['window_px']
        y0, x0, y1, x1 = y // step, x // step, (y + w - 1) // step, (x + w - 1) // step
        _rect(rgb, y0, x0, y1, x1, RECT_COLOUR)
        _rect(rgb, y0 - 1, x0 - 1, y1 + 1, x1 + 1, RECT_COLOUR)
        _label(rgb, str(rank), y0 + 2, x0 + 2)
    return rgb


def _position_base(source):
    """ROI 위치 그림의 바탕(한 변 <= POSITION_MAX_PX) 과 총 축소 배수. 모든 ROI 가 같은 바탕을 쓴다.

    그리는 순서: 바탕색, 비교 영역 밖 회색, frame 가운데 사분면 경계선(1 px), 변경 위치(모든 층 hotspot 합집합 변화 OR, 비교 영역 안만).
    CNT 패턴은 그리지 않는다(조밀한 패턴이 그림 전체를 채워 회백색이 된다)."""
    observed = source.observed
    changed = np.zeros(observed.shape, dtype=bool)
    for layer in source.heat:
        changed |= source.heat[layer]['union'] > 0
    extra = _step_for(observed.shape, POSITION_MAX_PX)
    total = source.step * extra
    changed, obs = _block(changed, extra, 'any'), _block(observed, extra, 'any')
    rgb = np.empty(obs.shape + (3,), dtype=np.uint8)
    rgb[:] = POS_BG
    rgb[~obs] = OUTSIDE_GRAY
    height, width = source.ref_valid.shape
    rgb[min((height // 2) // total, obs.shape[0] - 1), :] = POS_LINE
    rgb[:, min((width // 2) // total, obs.shape[1] - 1)] = POS_LINE
    rgb[changed & obs] = POS_CHANGED
    return rgb, total


def _position_image(base, total_step, window):
    """frame 전체(base)에 이 ROI 창만 굵은(3 px) 진한 사각형. 축소 뒤 창이 POS_MIN_RECT_PX 보다 작으면 창 중심에 그 크기로 그린다."""
    rgb = np.array(base)
    y, x, w = window['window_origin_px'][0], window['window_origin_px'][1], window['window_px']
    y0, x0, y1, x1 = y // total_step, x // total_step, (y + w - 1) // total_step, (x + w - 1) // total_step
    if y1 - y0 + 1 < POS_MIN_RECT_PX:
        y0 = (y0 + y1) // 2 - POS_MIN_RECT_PX // 2
        y1 = y0 + POS_MIN_RECT_PX - 1
    if x1 - x0 + 1 < POS_MIN_RECT_PX:
        x0 = (x0 + x1) // 2 - POS_MIN_RECT_PX // 2
        x1 = x0 + POS_MIN_RECT_PX - 1
    for grow in (0, 1, 2):
        _rect(rgb, y0 - grow, x0 - grow, y1 + grow, x1 + grow, POS_RECT)
    return rgb


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


def _fit(rgb_inputs, limit, base=None):
    """crop 한 장을 한 변 <= limit 로 만든다. 크면 블록 any, 작으면 정수 배 확대. (ref, tgt, gray, rect) → rgb. base: 전극 형상 bool 또는 None."""
    ref, tgt, gray, rect = rgb_inputs
    size = max(ref.shape)
    y0, x0, y1, x1 = rect
    if size > limit:
        step = int(math.ceil(size / float(limit)))
        ref, tgt, gray = _block(ref, step, 'any'), _block(tgt, step, 'any'), _block(gray, step, 'any')
        base = _block(base, step, 'any') if base is not None else None
        rect = (y0 // step, x0 // step, y1 // step, x1 // step)
    else:
        scale = max(1, limit // size)
        if scale > 1:
            ref, tgt, gray = (np.repeat(np.repeat(a, scale, axis=0), scale, axis=1) for a in (ref, tgt, gray))
            base = np.repeat(np.repeat(base, scale, axis=0), scale, axis=1) if base is not None else None
            rect = (y0 * scale, x0 * scale, (y1 + 1) * scale - 1, (x1 + 1) * scale - 1)
    rgb = _overlay(ref, tgt, gray, base)
    _rect(rgb, *rect, RECT_COLOUR)
    return rgb


def layer_bundles(query, window):
    """ROI 의 층 묶음 계획: ``(보일 묶음 목록, 생략한 층 이름 목록)``.

    묶음 = ``{'layer', 'area', 'n', 'counts': (형상 변화, 새로 생김, 사라짐)}``. 층은 기여 case 의 ``layer``(merge_scope=all)이고
    기여 case 가 없는 ROI 는 묶음이 없다(빈 목록). 순서는 창 안 변경 면적(``residual_um2``) 합이 큰 순(같으면 이름 자연 정렬), MAX_BUNDLES 개까지 보이고 나머지는 생략한다."""
    stats = {}
    for case in query['contributing_cases']:
        item = stats.setdefault(case.get('layer', window['layer']), {'area': 0.0, 'n': 0, 'counts': [0, 0, 0]})
        item['area'] += float(case['residual_um2'])
        item['n'] += 1
        kind = case['change_kind']
        item['counts'][1 if kind == 'appeared' else 2 if kind == 'disappeared' else 0] += 1
    ordered = sorted(stats, key=lambda layer: (-stats[layer]['area'], natural_key(layer)))
    bundles = [{'layer': layer, 'area': stats[layer]['area'], 'n': stats[layer]['n'], 'counts': tuple(stats[layer]['counts'])}
               for layer in ordered]
    return bundles[:MAX_BUNDLES], sorted((b['layer'] for b in bundles[MAX_BUNDLES:]), key=natural_key)


def bundle_title(bundle):
    parts = [name + ' ' + str(count) for name, count in zip(('형상 변화', CHANGE_KIND_TEXT['appeared'], CHANGE_KIND_TEXT['disappeared']),
                                                          bundle['counts']) if count]
    return '층 %s — 불량 조합 %d개%s' % (bundle['layer'], bundle['n'], ' (' + ' · '.join(parts) + ')' if parts else '')


def _bundle_images(source, i, window, layer, limit):
    """ROI i 의 한 층 묶음 [(캡션, rgb)] = 비교 · reference · target. 바탕이 없는 쪽은 캡션에 '전극 형상 없음'을 적는다."""
    hot = source.hot[i]
    y0, y1, x0, x1 = hot['box']
    y, x, w = window['window_origin_px'][0], window['window_origin_px'][1], window['window_px']
    rect = (y - y0, x - x0, y + w - 1 - y0, x + w - 1 - x0)
    crops = hot['layers'].get(layer)
    empty = np.zeros((y1 - y0, x1 - x0), dtype=bool)
    ref, tgt = (crops['ref'], crops['tgt']) if crops is not None else (empty, empty)
    gray = ~hot['obs']
    base_ref, base_tgt = source.base.get((i, layer, 'ref')), source.base.get((i, layer, 'tgt'))
    both = base_ref if base_tgt is None else base_tgt if base_ref is None else (base_ref | base_tgt)
    reason = {side: source.base_reason.get((layer, side), '파일 없음') for side in ('ref', 'tgt')}
    missing = [(name, side) for name, side, base in (('reference', 'ref', base_ref), ('target', 'tgt', base_tgt)) if base is None]
    compare_note = ('' if not missing else ' · 전극 형상 없음 (' + ' · '.join(name + ': ' + reason[side] for name, side in missing) + ')')
    return [
        ('ROI 층 ' + layer + ' · 비교: 파랑 = reference 만 · 주황 = target 만 · 검정 = 둘 다' + compare_note,
         _fit((ref, tgt, gray, rect), limit, both)),
        ('ROI 층 ' + layer + ' · reference: 전극 형상 + hotspot' +
         ('' if base_ref is not None else ' · 전극 형상 없음 (' + reason['ref'] + ')'),
         _fit((ref, empty, gray, rect), limit, base_ref)),
        ('ROI 층 ' + layer + ' · target: 전극 형상 + hotspot' +
         ('' if base_tgt is not None else ' · 전극 형상 없음 (' + reason['tgt'] + ')'),
         _fit((empty, tgt, gray, rect), limit, base_tgt))]


# --------------------------------------------------------------------------- HTML

STYLE = (
    'body{font-family:system-ui,sans-serif;max-width:1400px;margin:1.5rem auto;padding:0 1rem;color:#222}'
    'table{border-collapse:collapse;margin:.5rem 0}td,th{border:1px solid #ccc;padding:3px 8px;text-align:left;vertical-align:top;font-size:13px}'
    'th{background:#f3f3f3}.warn{border:2px solid #c00;background:#fff0f0;padding:.6rem 1rem;font-weight:bold}'
    '.note{color:#555;font-size:13px}.figs{display:flex;flex-wrap:wrap;gap:12px}figure{margin:0}'
    'figure img{max-width:100%;border:1px solid #999;image-rendering:pixelated}figcaption{font-size:12px;color:#555}'
    '.bundle{border-left:3px solid #ddd;padding-left:8px;margin:.5rem 0}.bundle h4{margin:.2rem 0}'
    '.roi{border-top:2px solid #888;margin-top:1.2rem;padding-top:.4rem}.missing{border:1px dashed #999;color:#777;padding:.4rem;font-size:12px}'
    '.sw{display:inline-block;width:.9em;height:.9em;vertical-align:middle;border:1px solid #666;margin-right:2px}'
    '.badge{display:inline-block;padding:3px 12px;border-radius:12px;font-weight:bold;color:#fff;font-size:14px}'
    '.badge.ok{background:#1f8f4e}.badge.uncertain{background:#d9822b}.badge.failed{background:#c62828}'
    '.frame-warn{border:2px solid #c80;background:#fff7e6;padding:.5rem .8rem;font-size:13px}'
    'tr.app td{background:#e9f8ee}tr.dis td{background:#fdecec}td.hi{color:#c62828;font-weight:bold}td.num{text-align:right}'
    '.key{color:#888;font-size:11px}code{background:#f2f2f2;padding:0 3px;font-size:12px}'
    'details summary{cursor:pointer;color:#36c;font-size:13px}a{color:#1a56c4}')

BADGE = {'aligned': ('ok', '정렬됨'), 'aligned_uncertain': ('uncertain', '불확실'),
         'alignment_failed_identity_used': ('failed', '실패')}
CHANGE_KIND_TEXT = {'appeared': '창 안 새로 생김', 'disappeared': '창 안 사라짐', 'shifted': '형상 변화', 'reshaped': '형상 변화',
                    'mixed_undetermined': '형상 변화'}
PRESENT_TEXT = {'both': '둘 다', 'reference': 'reference 만', 'target': 'target 만'}
ELECTRODE_FLAG_TEXT = {'new_electrode': '새 전극', 'removed_electrode': '없어진 전극'}
CONTRIBUTION_TOP_ROWS = 10
QUADRANTS = (('top_left', '위', '왼쪽'), ('top_right', '위', '오른쪽'),
             ('bottom_left', '아래', '왼쪽'), ('bottom_right', '아래', '오른쪽'))


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


def _swatch(colour):
    return '<span class="sw" style="background:rgb%s"></span>' % (tuple(colour),)


def _legend_layers(names):
    """2a 범례: 층 이름 + 견본(reference / target / 같은 모양). 층 > SHADE_LEVELS 이면 명도 반복 경고."""
    parts = ['<div style="font-size:13px;max-width:360px"><b>범례</b><br>']
    for k, name in enumerate(names):
        level = k % SHADE_LEVELS
        parts.append(_swatch(REF_SHADES[level]) + _swatch(TGT_SHADES[level]) + _swatch(SAME_SHADES[level]) +
                     ' ' + escape(name) + '<br>')
    parts.append(_swatch(REF_SHADES[3]) + ' reference 파랑 · ' + _swatch(TGT_SHADES[3]) + ' target 주황 · ' +
                 _swatch(SAME_SHADES[3]) + ' 두 모델 같음(회색~검정) · ' + _swatch(OUTSIDE_GRAY) + ' 비교 안 함<br>')
    if len(names) > SHADE_LEVELS:
        parts.append('<span class="warn">층이 %d개라 명도 반복: %d번째 층부터 앞의 층과 같은 명도를 씁니다. 층별 그림(2b)으로 구분하십시오.</span>'
                     % (len(names), SHADE_LEVELS + 1))
    else:
        parts.append('<span class="note">층 순서는 이름 자연 정렬이다. 층마다 명도가 다르다(밝음 → 어두움).</span>')
    parts.append('</div>')
    return ''.join(parts)


def _fmt2(value):
    return '—' if value is None else '%.2f' % float(value)


def _pct(value):
    return '—' if value is None else '%.1f %%' % (float(value) * 100.0)


def _frame_shape(alignment, source=None):
    shape = alignment.get('frame_shape_reference_yx')
    if shape is None and source is not None:
        shape = list(source.ref_valid.shape)
    return shape


def position_text(centre_y_px, centre_x_px, shape):
    """frame 안 위치 문구: (왼쪽/가운데/오른쪽) × (위/가운데/아래) + x %, y %. shape = (높이, 너비) px. 경계는 1/3, 2/3 에서 아래쪽 칸에 든다."""
    if not shape or not shape[0] or not shape[1]:
        return '—'
    fy, fx = float(centre_y_px) / shape[0], float(centre_x_px) / shape[1]
    horizontal = ('왼쪽', '가운데', '오른쪽')[min(2, max(0, int(fx * 3)))]
    vertical = ('위', '가운데', '아래')[min(2, max(0, int(fy * 3)))]
    where = '가운데' if horizontal == vertical == '가운데' else horizontal + ' ' + vertical
    return '%s (x %d %%, y %d %%)' % (where, int(round(fx * 100)), int(round(fy * 100)))


def _window_position(window, shape):
    y, x, w = window['window_origin_px'][0], window['window_origin_px'][1], window['window_px']
    return position_text(y + w / 2.0, x + w / 2.0, shape)


def _badge_title(alignment):
    css, text = BADGE.get(alignment['status'], ('uncertain', alignment['status']))
    inputs = alignment.get('cnt_inputs') or {}
    ids = {side: (inputs.get(side) or {}).get('product_id', '—') for side in ('reference', 'target')}
    return ('<h1>CNT 정렬 · 변경점 ROI 보고서 <span class="badge %s">%s</span></h1>'
            '<p class="note">기준 모델(reference) <code>%s</code> · 비교 모델(target) <code>%s</code> · pitch %s µm</p>'
            % (css, escape(text), escape(str(ids['reference'])), escape(str(ids['target'])), escape(str(alignment.get('pitch_um')))))


def _summary(alignment, n_rois=0, local=None):
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
    rows = rows + _frame_rows(alignment)
    quadrant = _quadrant_table(alignment)
    if quadrant:
        rows.append(('사분면 offset (전역 대비 차이)', _Raw(quadrant)))
    rows.append(('ROI 수', _Raw('%d (<a href="#list">ROI 목록</a>)' % n_rois)))
    parts.append(_table(['항목', '값'], rows))
    if local is not None:
        parts.append(_local_summary_line(local))
    if alignment.get('frame_shape_reference_yx') != alignment.get('frame_shape_target_yx') and 'frame_shape_target_yx' in alignment:
        parts.append('<p class="frame-warn">unit size 차이로 frame 크기가 다릅니다. 비교는 두 frame 의 공통 영역만 합니다. resize 하지 않습니다.</p>')
    return ''.join(parts)


def _frame_rows(alignment):
    """frame 모양 두 줄과 비교 영역 한 줄. 키가 없는 이전 alignment.json 이면 줄을 더하지 않는다."""
    if 'frame_shape_reference_yx' not in alignment:
        return []
    domain = alignment.get('comparison_domain') or {}
    anchor = alignment.get('frame_anchor')
    return [('frame 모양 reference (y, x)', alignment['frame_shape_reference_yx']),
            ('frame 모양 target (y, x)', _Raw(escape(str(alignment['frame_shape_target_yx'])) + ' 차이 ' +
                                              escape(str(alignment['frame_shape_delta_px'])) + ' · 기준 <code>' +
                                              escape(str(anchor)) + '</code>')),
            ('비교 영역', _Raw('%s px (침식 전 %s px) · reference 의 %s · target 의 %s · bbox [y0, y1, x0, x1] %s' % (
                escape(str(domain.get('pixels'))), escape(str(domain.get('pixels_before_erosion'))),
                escape(str(domain.get('fraction_of_reference'))), escape(str(domain.get('fraction_of_target'))),
                escape(str(domain.get('bbox_px'))))))]


def _quadrant_table(alignment):
    """사분면 offset 2x2 표(안쪽 표). 칸마다 전역 offset 과의 거리(µm, 소수 2자리). ``align_layer_tolerance_um`` 초과 칸은 hi 클래스·⚠."""
    quadrants = alignment.get('quadrant_offsets_um') or {}
    if not quadrants:
        return ''
    tolerance = (alignment.get('args') or {}).get('align_layer_tolerance_um')
    glob = alignment.get('offset_xy_um')
    cells = {}
    for key, _, _ in QUADRANTS:
        value = quadrants.get(key)
        if value is None or glob is None:
            cells[key] = '<td>—</td>'
            continue
        diff = math.hypot(float(value[0]) - float(glob[0]), float(value[1]) - float(glob[1]))
        over = tolerance is not None and diff > float(tolerance) + 1e-9
        cells[key] = '<td%s>%s · 차이 %.2f µm%s</td>' % (' class="hi"' if over else '', escape(_json(value)), diff, ' ⚠' if over else '')
    return ('<table><tr><th></th><th>왼쪽</th><th>오른쪽</th></tr>'
            '<tr><th>위</th>%(top_left)s%(top_right)s</tr><tr><th>아래</th>%(bottom_left)s%(bottom_right)s</tr></table>' % cells)


def _alternatives(alignment):
    alts = alignment['alternatives']
    parts = ['<h3>2c. 대안 offset · 합집합에 포함한 조합</h3><h4>대안 offset</h4>']
    if 'peak_ambiguous' in alignment['reasons']:
        parts.append('<p class="warn">정렬이 반복 주기만큼 틀렸을 수 있습니다(peak_ambiguous): 아래 대안 offset 이 최적과 비슷한 점수입니다.</p>')
    if not alts:
        parts.append('<p class="note">최적과 2 µm 이상 떨어진 별개 peak 가 없습니다.</p>')
    else:
        parts.append(_table(['offset_xy_um', 'offset_xy_px', 'dice', 'at_search_edge'],
                            [(a['offset_xy_um'], a.get('offset_xy_px'), a['dice'], a['at_search_edge']) for a in alts]))
    return ''.join(parts)


def split_case_key(case_key):
    """case_key → (불량 유형, 전극 쌍, 층). ``change_map._key_text`` 형식 ``type|A|layerA|B|layerB|layer`` 가 아니면 (None, None, None)."""
    fields = str(case_key).split('|')
    if len(fields) != 6:
        return None, None, None
    failure, a, layer_a, b, layer_b, layer = fields
    if layer_a == layer_b == layer:
        pair = a + ' – ' + b
    else:
        pair = '%s (%s) – %s (%s)' % (a, layer_a, b, layer_b)
    return failure, pair, layer


def _case_layer(case, decomposed):
    return decomposed if decomposed is not None else case.get('layer')


def contribution_layers(query, window):
    """불량 조합이 있는 층: merge_scope=all 이면 기여 case 의 ``layer``, 아니면 대표 층. 순서는 자연 정렬."""
    layers = {c['layer'] for c in query['contributing_cases'] if 'layer' in c}
    return sorted(layers or {window['layer']}, key=natural_key)


def kind_counts(query):
    """(새로 생김, 사라짐, 형상 변화) 기여 조합 수."""
    counts = [0, 0, 0]
    for c in query['contributing_cases']:
        kind = c['change_kind']
        counts[0 if kind == 'appeared' else 1 if kind == 'disappeared' else 2] += 1
    return tuple(counts)


def _case_row(case):
    failure, pair, layer = split_case_key(case['case_key'])
    layer = _case_layer(case, layer)
    kind = case['change_kind']
    kind_text = CHANGE_KIND_TEXT.get(kind, kind)
    kind_cell = escape(kind_text) + ('' if kind == 'appeared' or kind == 'disappeared' else ' <code>' + escape(kind) + '</code>')
    flags = [f for f in case.get('flags', ()) if f in CASE_FLAGS]
    flag_cell = ', '.join(escape(ELECTRODE_FLAG_TEXT[f]) + ' <code>' + escape(f) + '</code>' for f in flags) or '—'
    if failure is None:
        first = '<span class="key">' + escape(case['case_key']) + '</span>'
    else:
        first = escape(failure) + ' <br><span class="key">' + escape(case['case_key']) + '</span>'
    cells = [first, escape(pair) if pair is not None else '—', escape(str(layer)) if layer is not None else '—', kind_cell,
             _fmt2(case['residual_um2']), _fmt2(case.get('area_reference_um2')),
             _fmt2(case.get('area_target_um2')), escape(PRESENT_TEXT.get(case.get('present_in', 'both'), str(case.get('present_in')))),
             '예' if case.get('layer_changed', False) else '아니오', flag_cell]
    css = ' class="app"' if kind == 'appeared' else ' class="dis"' if kind == 'disappeared' else ''
    numeric = (4, 5, 6)
    return '<tr%s>%s</tr>' % (css, ''.join('<td%s>%s</td>' % (' class="num"' if i in numeric else '', c) for i, c in enumerate(cells)))


CONTRIBUTION_HEADER = ('불량 유형', '전극 쌍', '층', '변경 종류', '창 안 변경 면적 µm²', 'reference 면적 µm²', 'target 면적 µm²',
                       '있는 모델', '층 바뀜', '전극 표시')


def _contribution_table(cases):
    """기여 조합 표(한글). 창 안 변경 면적 큰 순(입력 순서 그대로), 상위 CONTRIBUTION_TOP_ROWS 행 밖은 <details> 로 접는다."""
    head = '<tr>' + ''.join('<th>' + escape(h) + '</th>' for h in CONTRIBUTION_HEADER) + '</tr>'
    rows = [_case_row(c) for c in cases]
    out = '<h4>기여 조합 (창 안 변경 면적 큰 순)</h4><table>' + head + ''.join(rows[:CONTRIBUTION_TOP_ROWS]) + '</table>'
    if len(rows) > CONTRIBUTION_TOP_ROWS:
        out += ('<details><summary>나머지 %d행 보기 (상위 %d행 외)</summary><table>%s%s</table></details>'
                % (len(rows) - CONTRIBUTION_TOP_ROWS, CONTRIBUTION_TOP_ROWS, head, ''.join(rows[CONTRIBUTION_TOP_ROWS:])))
    return out


def _roi_list(queries, windows, shape):
    """4절 ROI 목록 표. 순위 칸은 5절 상세(``roi<순위>``)로 가는 링크."""
    header = ('순위', '대표 층', '불량 조합이 있는 층', '중심 (µm)', 'frame 안 위치', '점수 µm²', CHANGE_KIND_TEXT['appeared'],
              CHANGE_KIND_TEXT['disappeared'], '형상 변화', '상태')
    out = ['<h2 id="list">4. ROI 목록</h2>']
    if not queries:
        out.append('<p class="note">선정된 ROI 가 없습니다.</p>')
        return ''.join(out)
    out.append('<table><tr>' + ''.join('<th>' + escape(h) + '</th>' for h in header) + '</tr>')
    for rank, (query, window) in enumerate(zip(queries, windows), start=1):
        new, gone, shape_change = kind_counts(query)
        centre = '[%.2f, %.2f]' % (query['center_xy_um'][0], query['center_xy_um'][1])
        out.append('<tr><td><a href="#roi%d">%d</a></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class="num">%s</td>'
                   '<td>%d</td><td>%d</td><td>%d</td><td>%s</td></tr>'
                   % (rank, rank, escape(str(window['layer'])), escape(', '.join(contribution_layers(query, window))),
                      escape(centre), escape(_window_position(window, shape)), _fmt2(query['score_um2']), new, gone, shape_change,
                      escape(str(query.get('status', '—')))))
    out.append('</table>')
    return ''.join(out)


def _roi_text(rank, query, window, shape):
    flags = query['flags']
    head = [('score_um2 (합집합 변화, 순위 기준)', _fmt2(query['score_um2'])), ('score_layer', query['score_layer']),
            ('sum_residual_um2', _fmt2(query['sum_residual_um2'])), ('changed_case_count', query['changed_case_count']),
            ('union_delta_um2', _fmt2(query['union_delta_um2'])),
            ('union_area_reference_um2', _fmt2(query['union_area_reference_um2'])),
            ('union_area_target_um2', _fmt2(query['union_area_target_um2'])), ('observed_fraction', _pct(query['observed_fraction'])),
            ('flags', _Raw('<br>'.join('<code>' + escape(f) + '</code> ' + escape(FLAG_TEXT.get(f, '')) for f in flags) or '—')),
            ('cnt_change_um2_by_layer', query['cnt_change_um2_by_layer'])]
    title = ('<h3>순위 %d · 대표 층 %s · 불량 조합 층 %s · 중심 %s µm · 창 원점 %s px · frame %s · <code>%s</code></h3>'
             % (rank, escape(str(window['layer'])), escape(', '.join(contribution_layers(query, window))),
                escape('[%.2f, %.2f]' % (query['center_xy_um'][0], query['center_xy_um'][1])),
                escape(_json(list(window['window_origin_px']))), escape(_window_position(window, shape)),
                escape(str(query['region_id']))))
    return title, _table(['항목', '값'], head) + _contribution_table(query['contributing_cases'])


LOCAL_STATUS_TEXT = {
    'ok': '일치 확인', 'change_biased': '변경이 정렬을 끌어당김(링과 다름)', 'ring_inconclusive': '링 판정 불가', 'ambiguous': '모호',
    'low_score': '점수 낮음', 'insufficient_support': '지지 부족', 'search_bound_hit': '탐색 경계', 'skipped': '진단 안 함'}
LOCAL_SUMMARY_ORDER = (('ok', 'ok'), ('change_biased', '변경 편향'), ('ring_inconclusive', '링 판정 불가'), ('ambiguous', '모호'),
                       ('low_score', '점수 낮음'), ('insufficient_support', '지지 부족'), ('search_bound_hit', '탐색 경계'),
                       ('skipped', '진단 안 함'))


def _local_summary_line(local):
    """1절 요약 아래 한 줄: 상태별 ROI 수와 선형 적합(배율 추정) 또는 그 사유. local = roi_local_alignment.json 문서."""
    summary = local.get('summary') or {}
    counts = ' · '.join('%s %d' % (text, summary.get(key, 0)) for key, text in LOCAL_SUMMARY_ORDER)
    fit = local.get('linear_fit')
    if fit:
        scale = '배율 추정 %s ppm, %s ppm (%d개 ROI)' % (fit['scale_ppm_xy'][0], fit['scale_ppm_xy'][1], fit['n'])
    else:
        scale = '배율 추정 없음: ' + str(local.get('linear_fit_reason') or '—')
    return '<p class="note">ROI 국소 정렬 진단: ' + escape(counts) + ' · ' + escape(scale) + '</p>'


def _local_table(item, excluded):
    """5절 ROI 마다 국소 정렬 진단 표(작은 한글 표). item = roi_local_alignment.json 의 rois 항목, excluded = 문서의 excluded_layers."""
    status = item.get('status', 'skipped')
    rows = [('상태', escape(LOCAL_STATUS_TEXT.get(status, status)) + ' <code>' + escape(status) + '</code>', '')]
    if item.get('reason') and item.get('reason') != status:
        rows.append(('사유', '<code>' + escape(str(item['reason'])) + '</code>', ''))
    if item.get('local_offset_xy_px') is not None:
        rows.append(('국소 offset', escape('%s px = %s µm (x, y)' % (_json(item['local_offset_xy_px']), _json(item['local_offset_xy_um']))), ''))
        rows.append(('전역 대비 차이', escape('%s µm, 크기 %s µm' % (_json(item['delta_from_global_xy_um']), item['delta_from_global_um'])), ''))
    rows.append(('Dice 전역 / 국소', escape('%s / %s' % (_fmt3(item.get('dice_global')), _fmt3(item.get('dice_local')))), ''))
    if item.get('ring_delta_xy_px') is not None or item.get('ring_reason'):
        ring = '링 차이 %s px (x, y) · 링 Dice %s' % (_json(item['ring_delta_xy_px']) if item.get('ring_delta_xy_px') is not None else '—',
                                                    _fmt3(item.get('ring_dice')))
        if item.get('ring_reason'):
            ring += ' · 링 사유 ' + str(item['ring_reason'])
        rows.append(('링', escape(ring), ''))
    used = ', '.join('%s:%s' % (u['source'], u['layer']) for u in item.get('layers_used') or [])
    rows.append(('쓴 층', escape(used or '—'), ''))
    rows.append(('제외 층', escape(', '.join('%s (%s)' % (e['key'], e['reason']) for e in excluded) or '—'), ''))
    flags = item.get('flags') or []
    rows.append(('플래그', ('<code>' + '</code> <code>'.join(escape(f) for f in flags) + '</code>') if flags else '—', 'hi' if flags else ''))
    return ('<h4>국소 정렬 진단</h4><table>' +
            ''.join('<tr><th>' + escape(k) + '</th><td' + (' class="%s"' % c if c else '') + '>' + v + '</td></tr>' for k, v, c in rows) +
            '</table>')


def _fmt3(value):
    return '—' if value is None else '%.3f' % float(value)


def _position_caption(alignment, total_step):
    """5절 위치 그림 캡션. 변경 위치는 총 축소 배수 x pitch 크기 블록으로 칠해 실제 면적보다 크게 보일 수 있다."""
    pitch = alignment.get('pitch_um')
    block = ('약 %g µm' % round(float(total_step) * float(pitch), 2)) if isinstance(pitch, (int, float)) else '%d px' % total_step
    return ('위치: frame 전체 · 연빨강 = 변경 위치 · 회색 = 비교 영역 밖 · 가는 선 = 사분면 경계 · 굵은 사각형 = 이 ROI · 변경 위치는 ' + block +
            ' 블록 단위로 칠한다(실제 면적보다 크게 보일 수 있음)')


def _pairing_summary(stats):
    """합집합에 포함·제외한 조합 수(``pair_cases`` stats)."""
    keys = ('pairs', 'one_sided_included_reference', 'one_sided_included_target', 'layer_changed_included',
            'new_electrode_included', 'removed_electrode_included')
    rows = [(key, stats.get(key, 0)) for key in keys]
    rows.append(('excluded_by_reason', _Raw('<code>' + escape(_json(stats.get('excluded_by_reason', {}))) + '</code>')))
    return ('<h4>합집합에 포함한 조합</h4>'
            '<p class="note">한쪽 모델에만 있거나 층이 바뀐 조합은 그쪽 합집합에만 들어갑니다. '
            '전극이 한쪽 모델에만 있는 조합도 포함하며 new_electrode / removed_electrode 로 표시합니다.</p>'
            + _table(['항목', '값'], rows))


def _footnotes(local=None):
    notes = ('<h2>7. 각주</h2><ul class="note">'
            '<li>순위는 층별 합집합 변화(<code>score_um2</code>, 6회차 Δ_union 과 같은 기준)이다. 층 사이 합집합은 만들지 않는다.</li>'
            '<li>조합별 잔차 합(<code>sum_residual_um2</code>)과 변경 case 수(<code>changed_case_count</code>)는 순위가 아니라 속성이다.</li>'
            '<li>union 은 짝지어진 조합과 한쪽 조합으로 계산한다(<code>union_basis: paired_and_one_sided_cases</code>). '
            '한쪽 모델에만 있거나 층이 바뀐 조합(<code>present_in</code>, <code>layer_changed</code>)의 hotspot 은 그쪽 합집합에만 들어가고 '
            '변경 종류는 appeared / disappeared 이다. 전극이 한쪽 모델에만 있어도 비교 대상이며, 그 조합에는 flag '
            '<code>new_electrode</code>(target 에만 있는 전극) 또는 <code>removed_electrode</code>(reference 에만 있는 전극)가 붙는다.</li>'
            '<li>한 ROI 의 직접 쌍들은 중심을 공유한다(같은 창에 기여한 조합마다 쌍이 하나씩이다).</li>'
            '<li>열지도는 블록 축소한 값이다: 합집합 변화는 블록 안 변화 칸 수, 조합별 잔차 합은 블록 안 최댓값(겹친 조합 수).</li>'
            '<li>창 안 변경 면적(<code>residual_um2</code>)은 한 조합의 reference 와 target hotspot 을 정렬 offset 으로 맞춘 뒤 '
            '변경 허용 오차를 넘어 창 안에 남는 칸의 면적이다. 조합마다 따로 구하며 합집합 변화(<code>score_um2</code>)와 같은 값이 아니다.</li>'
            '<li>변경 종류는 창 단위다: 그 창 안에서 한 모델의 hotspot 이 없으면 새로 생김/사라짐이다. case 자체는 두 모델에 다 있을 수 있다(있는 모델 열).</li>'
            '<li>층 묶음(5절)은 그 ROI 에 불량 조합이 있는 층만 보인다. 바탕 회색은 그 모델의 그 층 전극 PNG 를 ROI 주변만 잘라 합친 형상이고, '
            'target 은 정렬 offset 으로 reference 좌표에 옮겼다. 층이 ' + str(MAX_BUNDLES) + '개를 넘으면 창 안 변경 면적 합이 큰 층부터 ' +
            str(MAX_BUNDLES) + '개만 그린다. 전극 PNG 가 없거나 읽을 수 없으면 바탕 없이 그리고 "전극 형상 없음"이라고 적는다.</li>'
            '<li>reference 면적 / target 면적은 창 안·비교 영역 안 그 조합의 hotspot 면적이다. 한쪽에만 있는 조합은 없는 쪽이 0 이다. '
            '두 면적은 frame 크기가 달라도 같은 창·같은 공통 비교 영역에서 세므로 바로 비교할 수 있다. '
            '공통 비교 영역은 두 frame 어느 쪽보다 작을 수 있다.</li>'
            '<li>frame 크기가 다르면 두 frame 의 공통 영역만 비교한다. resize 하지 않으며 비교 영역 밖은 회색이다.</li>'
            '<li><code>--merge-scope all</code> 에서는 창이 층을 넘어 병합되지만 순위·점수·위치는 대표 층 하나의 합집합 변화 기준이다. '
            '다른 층의 변경은 기여 조합 표(층 칸)로만 보이며 층 사이 합집합은 만들지 않는다.</li>'
            '<li><code>--suppress-overlap</code> 을 쓰면 순위가 앞선 대표 창과 겹침 비율이 기준 이상인 창은 층 구분 없이 목록에서 빠진다. '
            '빠진 창의 변경은 앞선 ROI 의 창에서 이미 보인다.</li>')
    if local is not None:
        notes += ('<li>ROI 국소 정렬 진단(<code>--local-align-um</code>) 상태: 일치 확인(ok) = 전체와 링(창 둘레 띠)의 국소 offset 이 같다 · '
                  '변경 편향(change_biased) = 링의 offset 이 전체와 다르다(변경이 정렬을 끌어당김) · 링 판정 불가(ring_inconclusive) = 링만으로는 정할 수 없다 · '
                  '모호(ambiguous) = 최적과 비슷한 점수의 다른 후보가 멀리 있다 · 점수 낮음(low_score) = 최적 Dice 가 --align-min-dice 보다 낮다 · '
                  '지지 부족(insufficient_support) = 비교할 전경 픽셀이 너무 적다 · 탐색 경계(search_bound_hit) = 최적이 탐색 반경 가장자리다 · '
                  '진단 안 함(skipped) = 입력이 없거나 메모리 때문에 건너뛰었다.</li>'
                  '<li>국소 정렬 진단은 계산(순위·기여 case·export)에 쓰이지 않는다. 참고 표시다.</li>')
    return notes + '</ul>'


def build_alignment_html(alignment, *, queries, windows, source=None, max_bytes=None, skipped_reason=None,
                         pairing_stats=None, local_alignment=None):
    """alignment: ``alignment.json`` 문서(dict). queries: reference_to_target 질의 목록(순위 순), windows: 같은 순서의 창 후보.

    source: ``ImageSource`` (``collect`` 를 마친 것) 또는 None(그림 없음). skipped_reason: 그림을 만들지 않은 이유(문장).
    local_alignment: ``roi_local_alignment.json`` 문서 또는 None(None 이면 국소 정렬 표·요약 줄·각주를 더하지 않는다).
    반환 ``(html 바이트, {'path', 'bytes', 'downscaled', 'crops_omitted', 'n_images'})``."""
    limit = MAX_HTML_BYTES if max_bytes is None else max_bytes
    n_images = 0
    shape = _frame_shape(alignment, source)
    prefix = [_badge_title(alignment), _summary(alignment, len(queries), local_alignment)]
    # 2. CNT 정렬과 정렬 진단
    prefix.append('<h2>2. CNT 정렬과 정렬 진단</h2><h3>2a. 전체 CNT 층 겹침 — 정렬 전 / 정렬 후</h3>')
    pattern = None
    names = sorted(set(source.ref_layers) | set(source.tgt_layers), key=natural_key) if source is not None else []
    if source is None:
        prefix.append('<p class="note">그림을 만들지 않았습니다: ' + escape(skipped_reason or '') + '</p>')
    elif not names:
        prefix.append('<p class="note">CNT 층 영상이 없어 겹침 그림이 없습니다.</p>')
    else:
        before, after, pattern = _overview_all(source, names)
        figs = []
        for caption, rgb in (('전체 CNT 층 · 정렬 전 (offset 0)', before),
                             ('전체 CNT 층 · 정렬 후 (offset ' + _json(alignment['offset_xy_px']) + ' px): 같은 모양은 회색~검정, 남은 색이 변경', after)):
            figs.append(_figure(_uri(_png(rgb)), caption))
            n_images += 1
        prefix.append('<div class="figs">' + ''.join(figs) + _legend_layers(names) + '</div>')
        del before, after
        # 2b. 층별 CNT 겹침 (접어 둠)
        prefix.append('<h3>2b. 층별 CNT 겹침 (정렬 전 / 정렬 후)</h3><details><summary>층별 그림 펼치기 (%d층)</summary>' % len(names))
        prefix.append(_legend())
        for name in names:
            before, after = _overview_pair(source, name)
            figs = []
            for caption, rgb in (('정렬 전 (offset 0)', before), ('정렬 후 (offset ' + _json(alignment['offset_xy_px']) + ' px)', after)):
                figs.append(_figure(_uri(_png(rgb)), name + ' · ' + caption))
                n_images += 1
            prefix.append('<h4>CNT ' + escape(name) + '</h4><div class="figs">' + ''.join(figs) + '</div>')
        prefix.append('</details>')
    prefix.append(_alternatives(alignment))
    if pairing_stats is not None:
        prefix.append(_pairing_summary(pairing_stats))
    # 3. 변경 강조 지도
    prefix.append('<h2>3. 변경 강조 지도</h2>')
    position_base = None
    if source is None:
        prefix.append('<p class="note">그림을 만들지 않았습니다.</p>')
    elif source.observed is None:
        prefix.append('<p class="note">변경 지도를 수집하지 않아 그림이 없습니다.</p>')
    else:
        rgb = _change_map_image(source, windows, pattern)
        prefix.append('<div class="figs">' + _figure(_uri(_png(rgb)),
                      '변경 강조 지도: 빨강 = 변경 · 연회색 = 변경 없는 패턴 · 진회색 = 비교 안 함 · 초록 사각형·번호 = ROI 순위') + '</div>')
        n_images += 1
        position_base = _position_base(source)
    del pattern
    prefix.append(_roi_list(queries, windows, shape))
    prefix.append('<h2>5. ROI 상세 (순위 순)</h2>')
    if not queries:
        prefix.append('<p class="note">선정된 ROI 가 없습니다.</p>')
    suffix = _heat_section(source, windows) + _footnotes(local_alignment)
    local_items = {item['rank']: item for item in local_alignment['rois']} if local_alignment is not None else {}
    n_images += _heat_count(source)

    def encode(text):
        return text.encode('utf-8')
    head = ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>alignment.html</title><style>' + STYLE +
            '</style></head><body>')
    tail = '</body></html>\n'
    fixed = encode(head + ''.join(prefix)) + encode(suffix + tail)

    sections = {}      # (rank, level) → bytes; level 0 full, 1 half, 2 none

    def section(i, level):
        if (i, level) in sections:
            return sections[(i, level)]
        title, text = _roi_text(i + 1, queries[i], windows[i], shape)
        figs = ''
        if source is None:
            figs = '<p class="missing">그림을 만들지 않았습니다.</p>'
        else:
            items = []
            if position_base is not None:
                rgb = _position_image(position_base[0], position_base[1], windows[i])
                items.append(_figure(_uri(_png(rgb)), _position_caption(alignment, position_base[1])))
            figs = '<div class="figs">' + ''.join(items) + '</div>'
            if level < 2 and i < len(source.hot):
                side = CROP_MAX_PX if level == 0 else CROP_HALF_PX
                shown, omitted_layers = layer_bundles(queries[i], windows[i])
                if not shown:
                    figs += '<p class="note">이 ROI 에 기여 조합이 없다.</p>'
                else:
                    figs += '<p class="note">이 ROI 에 불량 조합이 있는 층마다 한 묶음: 비교 · reference · target. 바탕 회색 = 그 층 전극 형상, 색 = 그 층 hotspot 합집합, 초록 = ROI 창.</p>'
                for bundle in shown:
                    pictures = _bundle_images(source, i, windows[i], bundle['layer'], side)
                    figs += ('<div class="bundle"><h4>' + escape(bundle_title(bundle)) + '</h4><div class="figs">' +
                             ''.join(_figure(_uri(_png(rgb)), caption) for caption, rgb in pictures) + '</div></div>')
                if omitted_layers:
                    figs += '<p class="note">생략 %d개 층 (창 안 변경 면적 합이 작은 층): %s</p>' % (len(omitted_layers), escape(', '.join(omitted_layers)))
            if level == 2:
                figs += '<p class="missing">그림 생략: 파일 크기 상한(%d MiB) 때문에 이 ROI 의 crop 그림을 뺐습니다.</p>' % (limit // (1024 * 1024))
        if i + 1 in local_items:
            figs += _local_table(local_items[i + 1], local_alignment.get('excluded_layers') or [])
        sections[(i, level)] = encode('<div class="roi" id="roi%d">' % (i + 1) + title + figs + text + '</div>')
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
        has_position = 1 if position_base is not None else 0
        n_images += (sum(section_images(source, i, queries[i], windows[i]) + has_position for i in range(keep)) +
                     has_position * (count - keep))
    prefix_bytes = encode(head + ''.join(prefix))
    data = prefix_bytes + b''.join(body) + encode(suffix + tail)
    return data, {'path': 'alignment.html', 'bytes': len(data), 'downscaled': bool(downscaled),
                  'crops_omitted': omitted, 'n_images': n_images}


def section_images(source, i, query, window):
    """ROI i 의 crop 그림 장수 = 층 묶음 수 x 3 (위치 그림은 따로 센다)."""
    return 3 * len(layer_bundles(query, window)[0]) if i < len(source.hot) else 0


def _heat_count(source):
    return 2 * len(source.heat) if source is not None and source.observed is not None else 0


def _heat_section(source, windows):
    """6절 변경 지도 열지도(참고, 접어 둠). 그림 수는 ``_heat_count``."""
    parts = ['<h2>6. 변경 지도 열지도 (참고)</h2>']
    if source is None:
        parts.append('<p class="note">그림을 만들지 않았습니다.</p>')
    elif not source.heat or source.observed is None:
        parts.append('<p class="note">짝지어진 case 가 없어 열지도가 없습니다.</p>')
    else:
        parts.append('<p class="note">흰색 = 변화 없음, 노랑→빨강 = 많음, 회색 = 비교 영역 밖. 초록 사각형과 숫자 = 선정 ROI 창과 순위.</p>'
                     '<details><summary>층별 열지도 펼치기 (%d층)</summary>' % len(source.heat))
        heat = _heat_images(source, windows)
        for layer in sorted(heat):
            figs = []
            for (rgb, vmax), caption in zip(heat[layer], ('합집합 변화 RU (블록당 변화 칸 수, 최대 %d)',
                                                          '조합별 잔차 합 count_map (블록 최대 겹침 수, 최대 %d)')):
                figs.append(_figure(_uri(_png(rgb)), '층 ' + layer + ' · ' + caption % vmax))
            parts.append('<h4>층 ' + escape(layer) + '</h4><div class="figs">' + ''.join(figs) + '</div>')
        parts.append('</details>')
    return ''.join(parts)
