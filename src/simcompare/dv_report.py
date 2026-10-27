"""vulnerability-report: candidates-dv 출력 폴더를 읽어 ΔV 가중 취약점 ROI 의 수치 근거를 한 장의 HTML 로 보인다.

설계: PLAN v2.3 > v2 "3. 명령 vulnerability-report", 형식은 목업(vulnerability_report_mockup.html).
표시만 한다: vulnerability_v1.json·candidate_queries_v1.json 의 값을 그대로 옮기며 다시 계산하지 않는다
(표시용 파생은 순위 변화 = 비가중 순위 − 등급 순위, 창 포함 % 반올림, 쌍 등급 개수뿐이다).
ROI 지도는 grade_maps/ 의 PNG(+sidecar 의 legend)에서 셀 값을 읽고, 전극 layout 은 candidates-dv 입력 기록의 asset manifest
(경로·sha256)로 review_assets 의 전극 PNG 를 읽는다. sha256 이 다르면 layout 없이 그리고 경고한다.
final_report.py 의 헬퍼(_table, _png_uri, _sha256, _p)만 import 한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import escape
import io
import json
import math
import os
from pathlib import Path
import shutil
import tempfile

import numpy as np
from PIL import Image

from . import candidates
from .final_report import _Html, _cell, _p, _png_uri, _sha256, _table
from .numeric_schema import NumericContractError

REPORT_SCHEMA = 'vulnerability_report_v1'
QUERIES_NAME, VULNERABILITY_NAME = 'candidate_queries_v1.json', 'vulnerability_v1.json'
SIDES = ('reference', 'target')
GRADE_ORDER = {'High': 0, 'Low': 1, 'None': 2}
GRADE_CODE_NAME = {0: 'ungraded', 1: 'None', 2: 'Low', 3: 'High'}
LAYER_SLOTS = 4           # CSS --l-1 … --l-4. 이보다 layer 가 많으면 layer 별 작은 지도로 나눈다(색을 만들지 않는다)
MAP_SIZE_PX = 310         # ROI 상세 지도 한 변(화면 px)
MAP_MAX_CELLS = 220       # 지도 한 변 최대 셀 수(넘으면 정수 배로 묶는다)
MAP_MAX_BYTES = 400_000   # ROI 지도 한 장(SVG)의 상한. 넘으면 더 묶는다
MAP_MARGIN = 0.25         # 창 한 변에 대한 둘레 여백 비율
THUMB_MAX_PX = 360
SEQ_RGB = ((134, 182, 239), (57, 135, 229), (28, 92, 171), (13, 54, 107), (8, 32, 66))
GRADE_RGB = {1: (195, 194, 183), 2: (250, 178, 25), 3: (208, 59, 59)}
FOOTNOTE_CA = ('* ROI 내 CA 는 병합 전 등급 영역(선정 성분) 기준 CA 입니다. 질의 창·병합 영역 기준이 아닙니다. '
               '여러 병합 영역에 함께 걸린 case 는 대표 영역 쪽 값을 보입니다.')

CSS = """
:root{--surface-0:#f4f4f1;--surface-1:#fcfcfb;--line:#e3e2dc;--grid:#ecebe6;--text-primary:#0b0b0b;--text-secondary:#52514e;--text-muted:#7a7974;
--seq-400:#3987e5;--g-uncovered:#f0efec;--g-none:#c3c2b7;--g-low:#fab219;--g-high:#d03b3b;--win:#0b0b0b;--sel:#e87ba4;
--l-1:#2a78d6;--l-2:#1baf7a;--l-3:#6250d6;--l-4:#a0703c}
@media (prefers-color-scheme: dark){:root{--surface-0:#121211;--surface-1:#1a1a19;--line:#2e2e2b;--grid:#262624;--text-primary:#f0efec;
--text-secondary:#c3c2b7;--text-muted:#8f8e88;--g-uncovered:#262624;--g-none:#56554f;--g-high:#e66767;--win:#f0efec;
--l-1:#3987e5;--l-2:#199e70;--l-3:#9085e9;--l-4:#c79a6b}}
*{box-sizing:border-box}
body{margin:0;background:var(--surface-0);color:var(--text-primary);font:14px/1.5 -apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 8px;padding-top:8px;border-top:1px solid var(--line)}h3{font-size:15px;margin:18px 0 6px}
.sub{color:var(--text-secondary);margin:0 0 12px}.note{color:var(--text-secondary);font-size:13px;margin:.2rem 0}
.card{background:var(--surface-1);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.grid{display:grid;gap:12px}.kpis{grid-template-columns:repeat(4,1fr)}.two{grid-template-columns:1fr 1fr}.roi{grid-template-columns:330px minmax(0,1fr)}
.kpi .v{font-size:28px;font-weight:700;font-variant-numeric:tabular-nums}.kpi .l{color:var(--text-secondary);font-size:13px}.kpi .m{color:var(--text-muted);font-size:12px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;margin:0}
th,td{padding:5px 8px;border:0;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap;background:none;font-size:14px;vertical-align:middle}
th{color:var(--text-secondary);font-weight:600;font-size:12px}th.l,td.l{text-align:left}
.badge{display:inline-flex;align-items:center;gap:4px;border-radius:10px;padding:0 8px;font-size:12px;font-weight:600;border:1px solid var(--line)}
.badge i,.legend i{width:10px;height:10px;border-radius:2px;display:inline-block}
.g-High{background:var(--g-high)}.g-Low{background:var(--g-low)}.g-None{background:var(--g-none)}.g-ungraded{background:var(--g-uncovered)}
.up{color:var(--text-primary)}.muted{color:var(--text-muted)}
.flag{font-size:11px;border:1px dashed var(--text-muted);border-radius:4px;padding:0 5px;color:var(--text-secondary);margin-left:3px}
.legend{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;color:var(--text-secondary);margin:6px 0}.legend span{display:inline-flex;align-items:center;gap:4px}
.legend i{width:12px;height:12px;border:1px solid var(--line)}
.maps{display:flex;gap:10px;flex-wrap:wrap}.maps figure{margin:0}.maps figcaption{font-size:12px;color:var(--text-secondary);max-width:330px}
.map{background:var(--surface-1);border:1px solid var(--line);border-radius:6px;display:block}
.thumb{position:relative;display:inline-block;background:var(--g-uncovered);border:1px solid var(--line);border-radius:4px}
.thumb img{display:block;image-rendering:pixelated;max-width:100%}.thumb svg{position:absolute;left:0;top:0;width:100%;height:100%}
.lf{fill-opacity:.22}.ls{fill:none;stroke-opacity:.9;stroke-width:1;vector-effect:non-scaling-stroke}
.l1{fill:var(--l-1);stroke:var(--l-1)}.l2{fill:var(--l-2);stroke:var(--l-2)}.l3{fill:var(--l-3);stroke:var(--l-3)}.l4{fill:var(--l-4);stroke:var(--l-4)}
.ls.l1,.ls.l2,.ls.l3,.ls.l4{fill:none}
.gc{fill-opacity:.55}.gc1{fill:var(--g-none)}.gc2{fill:var(--g-low)}.gc3{fill:var(--g-high)}
.win{fill:none;stroke:var(--win);stroke-width:2;vector-effect:non-scaling-stroke}.sel{fill:none;stroke:var(--sel);stroke-width:2;stroke-dasharray:4 3;vector-effect:non-scaling-stroke}
.bar{height:16px;border-radius:0 4px 4px 0;background:var(--seq-400)}
code{font-size:12px;color:var(--text-secondary)}
details summary{cursor:pointer;color:var(--text-secondary)}
.tabr{position:absolute;opacity:0}.tabs{display:flex;gap:6px;margin:8px 0}
.tabs label{border:1px solid var(--line);background:var(--surface-1);border-radius:6px;padding:3px 12px;cursor:pointer}
#tab-reference:checked~.tabs label[for=tab-reference],#tab-target:checked~.tabs label[for=tab-target]{background:var(--text-primary);color:var(--surface-1)}
.side{display:none}#tab-reference:checked~.sides #side-reference,#tab-target:checked~.sides #side-target{display:block}
.warn{color:#9a5b00}@media (prefers-color-scheme: dark){.warn{color:#f2b44f}}
@media print{.side{display:block}.tabs{display:none}}
"""


def _bad(code, detail=''):
    return NumericContractError(code, detail)


# ---- 입력 --------------------------------------------------------------------------------------------------------

def _read_json(path, role, schema):
    if not path.is_file():
        raise _bad('missing_input_file', f'{role}: {path} 파일이 없습니다')
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise _bad('unreadable_input_file', f'{path}: {exc}') from exc
    if not isinstance(payload, dict) or payload.get('schema_version') != schema:
        found = payload.get('schema_version') if isinstance(payload, dict) else None
        raise _bad('unexpected_schema_version', f'{path}: schema_version {found!r}, 기대값 {schema!r}')
    return payload


def _load_inputs(folder):
    folder = Path(folder)
    if not folder.is_dir():
        raise _bad('candidates_dv_dir_missing', str(folder))
    queries = _read_json(folder / QUERIES_NAME, 'candidates-dv 질의', 'candidate_queries_v1')
    if queries.get('roi_source') != 'candidates_dv_v1':
        raise _bad('not_candidates_dv_output', f'{folder / QUERIES_NAME}: roi_source {queries.get("roi_source")!r} (candidates-dv 출력이 아닙니다)')
    vulnerability = _read_json(folder / VULNERABILITY_NAME, 'candidates-dv 취약점', 'vulnerability_v1')
    products = vulnerability.get('products')
    if not isinstance(products, dict) or set(products) != set(SIDES):
        raise _bad('vulnerability_products_invalid', f'{folder / VULNERABILITY_NAME}: products 는 reference·target 이어야 합니다')
    for name in SIDES:
        for key in ('regions', 'unweighted_regions', 'pairs', 'cases', 'grade_maps', 'ungraded_cases', 'dv_provenance', 'product_id'):
            if key not in products[name]:
                raise _bad('vulnerability_products_invalid', f'{name}.{key} 가 없습니다')
    for key in ('min_count', 'window_um', 'merge_overlap', 'roi_grade'):
        if key not in (queries.get('args') or {}):
            raise _bad('queries_args_invalid', f'args.{key} 가 없습니다')
    return queries, vulnerability


# ---- 표시 값 -----------------------------------------------------------------------------------------------------

def _fmt_area(value):
    return '-' if value is None else f'{value:.2f}'


def _fmt_ca(item, key):
    value = item.get(key)
    status = item.get(key.replace('_um2', '_status'))
    if value is None or status not in (None, 'available'):
        return _Html(f'<span class="muted" title="{escape(str(status))}">-</span>')
    return f'{value:.3f}'


def _fmt_ratio(value):
    return '-' if value is None else f'{value * 100:.0f}%'


def _badge(grade):
    return _Html(f'<span class="badge"><i class="g-{escape(str(grade))}"></i>{escape(str(grade))}</span>')


def _flags(flags):
    return ''.join(f'<span class="flag">{escape(f)}</span>' for f in flags)


def _short_id(prefix, rank, region_id):
    return _Html(f'<b title="{escape(region_id)}">{prefix}-{rank:02d}</b>')


def _dv_sort(value):
    try:
        return -Decimal(str(value))
    except InvalidOperation:
        return Decimal(0)


def _queries_by_region(queries):
    """제품 이름 → {region_id: 질의}."""
    out = {name: {} for name in SIDES}
    for direction in queries.get('directions', []):
        side = direction.get('query_product')
        if side in out:
            for query in direction.get('queries', []):
                out[side][query['region_id']] = query
    return out


def _rank_rows(product, by_query, top_n):
    """등급 순위 표의 행(표시 값 dict 목록). 대표 영역만, 등급 순위 오름차순."""
    reps = [r for r in product['regions'] if r.get('is_representative')]
    reps.sort(key=lambda r: (r['rank_graded'], r['region_id']))
    rows = []
    for region in reps[:top_n]:
        query = by_query.get(region['region_id'])
        change = region['rank_unweighted'] - region['rank_graded']
        rows.append({
            'label': f'R-{region["rank_graded"]:02d}', 'region_id': region['region_id'], 'rank_graded': region['rank_graded'],
            'layer': region['map_key']['layer'], 'sel_max_count': region['sel_max_count'], 'grade_max': region['grade_max'],
            'max_count': region['max_count'], 'rank_unweighted': region['rank_unweighted'], 'change': change,
            'area_um2': region['area_um2'], 'window_ratio': query.get('sel_cells_in_window_ratio') if query else None,
            'state': query['status'] if query else 'not_exported', 'flags': list(query['flags']) if query else [],
            'reasons': list(query['reasons']) if query else []})
    return rows, len(reps)


def _dropped_rows(product, k, top_n):
    rows = [u for u in product['unweighted_regions'] if 'below_graded_threshold' in u.get('flags', [])]
    rows.sort(key=lambda u: (u['rank_count_all'], u['region_id']))
    shown = [{'label': f'U-{u["rank_count_all"]:02d}', 'region_id': u['region_id'], 'layer': u['map_key']['layer'],
              'max_count': u['max_count'], 'sel_max_count': u['sel_max_count'], 'grade_max': u['grade_max'],
              'reason': f'선정 전극 수 {u["sel_max_count"]} < K {k} (below_graded_threshold)'} for u in rows[:top_n]]
    return shown, len(rows)


def _pairs_for_roi(product, rep_region):
    """대표 영역과 거기에 병합된 영역이 덮는 case 행. case 하나는 먼저 만난 영역(대표 먼저)의 값을 쓴다."""
    pair_by_key = {p['pair_key']: p for p in product['pairs']}
    case_by_name = {c['case_name']: c for c in product['cases']}
    members = [r for r in product['regions'] if r['merged_into'] == rep_region['region_id']]
    members.sort(key=lambda r: (r['region_id'] != rep_region['region_id'], r['rank_graded'] is None, r['region_id']))
    rows, seen = [], set()
    for member in members:
        for pair in member['pairs']:
            record = pair_by_key[pair['pair_key']]
            for item in pair['cases']:
                if item['case_name'] in seen:
                    continue
                seen.add(item['case_name'])
                case = case_by_name.get(item['case_name'], {})
                rows.append({
                    'pair_key': pair['pair_key'], 'case_name': item['case_name'],
                    'electrode_a': record['electrode_a'], 'layer_a': case.get('layer_a'),
                    'electrode_b': record['electrode_b'], 'layer_b': case.get('layer_b'),
                    'failure_type': item['failure_type'], 'grade': record['grade'], 'dv_v': record['dv_v'],
                    'original_ca_um2': item['original_ca_um2'], 'original_ca_status': item['original_ca_status'],
                    'ca_in_region_um2': item['ca_in_region_um2'], 'ca_in_region_status': item['ca_in_region_status']})
    rows.sort(key=lambda r: (GRADE_ORDER.get(r['grade'], 9), _dv_sort(r['dv_v']), r['pair_key'], r['case_name']))
    return rows, [m['region_id'] for m in members]


def _ungraded_in(product, rep_region):
    names = []
    for member in product['regions']:
        if member['merged_into'] == rep_region['region_id']:
            names.extend(c['case_name'] for c in member['ungraded_cases'])
    return sorted(set(names))


# ---- 그리드·SVG 도우미 -----------------------------------------------------------------------------------------

def _pool(array, factor, fill):
    if factor == 1:
        return array
    h, w = array.shape
    H, W = -(-h // factor), -(-w // factor)
    padded = np.full((H * factor, W * factor), fill, dtype=array.dtype)
    padded[:h, :w] = array
    return padded.reshape(H, factor, W, factor).max(axis=(1, 3))


def _runs(vector):
    """1-D bool 벡터의 True 구간 (시작, 길이) 목록."""
    padded = np.concatenate(([False], vector, [False])).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    return [(int(a), int(b - a)) for a, b in zip(edges[::2], edges[1::2])]


def _rect_path(mask):
    """True 셀을 세로로 같은 구간끼리 합친 사각형들의 path d (셀 단위 좌표)."""
    parts, active = [], {}
    for y in range(mask.shape[0] + 1):
        current = set(_runs(mask[y])) if y < mask.shape[0] else set()
        for key in sorted(k for k in active if k not in current):
            y0 = active.pop(key)
            parts.append(f'M{key[0]} {y0}h{key[1]}v{y - y0}h-{key[1]}z')
        for key in sorted(current):
            active.setdefault(key, y)
    return ''.join(parts)


def _edge_path(mask):
    """True 영역의 바깥 경계 선분들의 path d."""
    h, w = mask.shape
    padded = np.zeros((h + 2, w + 2), dtype=bool)
    padded[1:-1, 1:-1] = mask
    horizontal = padded[:-1, 1:-1] != padded[1:, 1:-1]     # 행 경계 y = 0..h, 열 x = 0..w-1
    vertical = padded[1:-1, :-1] != padded[1:-1, 1:]       # 열 경계 x = 0..w, 행 y = 0..h-1
    parts = [f'M{x} {y}h{n}' for y in range(h + 1) for x, n in _runs(horizontal[y])]
    parts += [f'M{x} {y}v{n}' for x in range(w + 1) for y, n in _runs(vertical[:, x])]
    return ''.join(parts)


class _Grid:
    """grade_maps PNG 한 장의 셀 값 읽기(sidecar 의 frame·legend 로 해독)."""

    def __init__(self, root, ref):
        self.png = root / ref['png']
        self.sidecar = root / ref['sidecar']
        meta = json.loads(self.sidecar.read_text(encoding='utf-8'))
        frame = meta.get('frame')
        if meta.get('status') != 'available' or not frame or not meta.get('png'):
            raise ValueError(f'지도 {self.sidecar.name}: status {meta.get("status")}')
        self.origin = tuple(frame['origin_xy_um'])
        self.pitch = float(frame['pixel_pitch_um'])
        self.shape = tuple(frame['shape_yx'])
        self.scale = int(meta['rendering']['scale'])
        legend = meta['legend']
        colors = [tuple(item['color']) for item in legend]
        if len(set(colors)) != len(colors):
            raise ValueError(f'지도 {self.sidecar.name}: legend 색이 겹쳐 값을 읽을 수 없습니다')
        self.lookup = {(c[0] << 16) | (c[1] << 8) | c[2]: int(item['count']) for c, item in zip(colors, legend)}

    def read(self, box):
        """box = (r0, c0, r1, c1) 셀 범위(frame 안). 값 배열(int32), legend 에 없는 색(invalid)은 -1."""
        r0, c0, r1, c1 = box
        s = self.scale
        with Image.open(self.png) as image:
            crop = np.asarray(image.convert('RGB').crop((c0 * s, r0 * s, c1 * s, r1 * s)))
        centre = crop[s // 2::s, s // 2::s].astype(np.int32)
        packed = (centre[..., 0] << 16) | (centre[..., 1] << 8) | centre[..., 2]
        out = np.full(packed.shape, -1, dtype=np.int32)
        for color, count in self.lookup.items():
            out[packed == color] = count
        return out


def _layer_slots(layers):
    return {layer: (index + 1 if index < LAYER_SLOTS else None) for index, layer in enumerate(sorted(layers))}


# ---- 전극 layout -------------------------------------------------------------------------------------------------

class _Layouts:
    """한 제품의 review_assets: asset manifest 를 입력 기록의 경로·sha256 으로 확인한 뒤 case → 전극 layout 을 준다."""

    def __init__(self, side, record, warn):
        self.side, self.warn = side, warn
        self.index, self.root, self.cache = None, None, {}
        self.status = 'unavailable'
        ref = (record or {}).get('asset_manifest')
        data_dir = (record or {}).get('data_dir')
        if not isinstance(ref, dict) or not data_dir or 'path' not in ref or 'sha256' not in ref:
            warn('layout_asset_manifest_unrecorded', side, 'candidates-dv 입력 기록에 asset manifest 경로·sha256 이 없습니다')
            return
        self.root = Path(data_dir)
        path = self.root / ref['path']
        if not path.is_file():
            warn('layout_asset_manifest_missing', side, f'{path} 파일이 없습니다')
            return
        actual = _sha256(path)
        if actual != ref['sha256']:
            self.status = 'sha256_mismatch'
            warn('layout_asset_sha256_mismatch', side, f'{path}: 기록 {ref["sha256"][:12]}… ≠ 실제 {actual[:12]}… — 전극 layout 없이 그립니다')
            return
        try:
            manifest = json.loads(path.read_text(encoding='utf-8'))
            self.index = {entry['identity']['case_id']: entry for entry in manifest['cases']}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            warn('layout_asset_manifest_unreadable', side, f'{path}: {exc}')
            return
        self.status = 'verified'
        self.sha256 = actual

    def case_layouts(self, case_id):
        """[(전극, layer, bool 마스크, origin, pitch)] — 읽지 못한 것은 경고하고 뺀다."""
        if self.index is None:
            return []
        entry = self.index.get(case_id)
        if entry is None:
            self.warn('layout_case_missing', self.side, f'review_assets 에 case {case_id} 가 없습니다')
            return []
        frame = entry.get('frame') or {}
        out = []
        for layout in (entry.get('assets') or {}).get('layouts', []):
            endpoint, asset = layout['endpoint'], layout['asset']
            path = self.root / asset['path']
            key = str(path)
            if key not in self.cache:
                mask = None
                if not path.is_file():
                    self.warn('layout_asset_missing', self.side, f'{path} 파일이 없습니다')
                elif _sha256(path) != asset['sha256']:
                    self.warn('layout_asset_sha256_mismatch', self.side, f'{path}: 기록된 sha256 과 다릅니다 — 이 전극은 그리지 않습니다')
                else:
                    with Image.open(path) as image:
                        mask = np.asarray(image.convert('L')) == 0     # 0 이 표시(전극)
                self.cache[key] = mask
            mask = self.cache[key]
            if mask is not None:
                out.append((endpoint['electrode_id'], endpoint['actual_layer'], mask, tuple(frame['origin_xy_um']), float(frame['pixel_pitch_um'])))
        return out


# ---- ROI 상세 지도 -----------------------------------------------------------------------------------------------

def _crop_box(grid, bounds_list):
    """µm 범위들의 합집합(+둘레 여백)을 grid 셀 범위 (r0, c0, r1, c1)로. 지도 밖은 자른다."""
    x0 = min(b[0] for b in bounds_list)
    y0 = min(b[1] for b in bounds_list)
    x1 = max(b[2] for b in bounds_list)
    y1 = max(b[3] for b in bounds_list)
    margin = MAP_MARGIN * max(x1 - x0, y1 - y0)
    ox, oy = grid.origin
    h, w = grid.shape
    c0 = max(0, int(math.floor((x0 - margin - ox) / grid.pitch)))
    r0 = max(0, int(math.floor((y0 - margin - oy) / grid.pitch)))
    c1 = min(w, int(math.ceil((x1 + margin - ox) / grid.pitch)))
    r1 = min(h, int(math.ceil((y1 + margin - oy) / grid.pitch)))
    return (r0, c0, r1, c1) if r1 > r0 and c1 > c0 else None


def _paste_layouts(layouts, case_ids, grid, box, warn, side):
    """전극 (이름, layer) → 이 crop 위의 bool 마스크 (여러 case 의 같은 전극은 OR)."""
    r0, c0, r1, c1 = box
    canvas_shape = (r1 - r0, c1 - c0)
    shapes = {}
    for case_id in case_ids:
        for electrode, layer, mask, origin, pitch in layouts.case_layouts(case_id):
            if abs(pitch - grid.pitch) > 1e-9:
                warn('layout_frame_mismatch', side, f'case {case_id}: pitch {pitch} ≠ 지도 pitch {grid.pitch}')
                continue
            dc = (origin[0] - grid.origin[0]) / grid.pitch
            dr = (origin[1] - grid.origin[1]) / grid.pitch
            if abs(dc - round(dc)) > 1e-6 or abs(dr - round(dr)) > 1e-6:
                warn('layout_frame_mismatch', side, f'case {case_id}: origin 이 지도 격자와 맞지 않습니다')
                continue
            top, left = int(round(dr)) - r0, int(round(dc)) - c0     # case 셀 (0,0) 의 canvas 위치
            sr0, sc0 = max(0, -top), max(0, -left)
            sr1 = min(mask.shape[0], canvas_shape[0] - top)
            sc1 = min(mask.shape[1], canvas_shape[1] - left)
            if sr1 <= sr0 or sc1 <= sc0:
                continue
            target = shapes.setdefault((electrode, layer), np.zeros(canvas_shape, dtype=bool))
            target[top + sr0:top + sr1, left + sc0:left + sc1] |= mask[sr0:sr1, sc0:sc1]
    return {key: value for key, value in shapes.items() if value.any()}


def _map_svg(factor, grid, box, codes, selected, shapes, slots, window, caption_layers):
    """SVG 문자열. 셀 단위 viewBox. 아래부터 전극 layout(layer 색) → 최고 등급(반투명) → 선정 셀 경계(점선) → 질의 창."""
    r0, c0, r1, c1 = box
    codes = _pool(np.where(codes < 0, 0, codes), factor, 0)
    selected = _pool(selected, factor, False)
    H, W = codes.shape
    parts = [f'<svg class="map" viewBox="0 0 {W} {H}" width="{MAP_SIZE_PX}" height="{max(1, round(MAP_SIZE_PX * H / W))}" role="img" '
             f'aria-label="전극 layout, 최고 등급, 질의 창, 선정 셀" shape-rendering="crispEdges">']
    for (electrode, layer), mask in sorted(shapes.items(), key=lambda item: (item[0][1], item[0][0])):
        slot = slots.get(layer)
        if slot is None:
            continue
        pooled = _pool(mask, factor, False)
        if not pooled.any():
            continue
        parts.append(f'<path class="lf l{slot}" d="{_rect_path(pooled)}"><title>{escape(electrode)} ({escape(layer)})</title></path>'
                     f'<path class="ls l{slot}" d="{_edge_path(pooled)}"/>')
    for code in (1, 2, 3):
        mask = codes == code
        if mask.any():
            parts.append(f'<path class="gc gc{code}" d="{_rect_path(mask)}"/>')
    if selected.any():
        parts.append(f'<path class="sel" d="{_edge_path(selected)}"/>')
    unit = factor * grid.pitch
    ox = grid.origin[0] + c0 * grid.pitch
    oy = grid.origin[1] + r0 * grid.pitch
    a, b, c, d = window
    parts.append(f'<rect class="win" x="{(a - ox) / unit:.3f}" y="{(b - oy) / unit:.3f}" width="{(c - a) / unit:.3f}" height="{(d - b) / unit:.3f}" rx="2"/>')
    return ''.join(parts) + '</svg>'


def _roi_figures(root, product, region, query, layouts, args, warn, side, case_ids, layout_summary):
    """ROI 상세 지도 HTML 과 메모 dict. 지도를 그릴 수 없으면 사유를 담은 문구를 돌려준다."""
    entry = next((e for e in product['grade_maps'] if all(e['key'].get(k) == region['map_key'].get(k)
                                                          for k in ('source_id', 'process_step_id', 'exposure_id', 'layer', 'unit_id'))), None)
    if entry is None or query is None:
        return '<p class="note muted">이 영역의 등급 지도가 없어 위치 지도를 그리지 못했습니다.</p>', {}
    select_name = 'count_high' if args['roi_grade'] == 'high' else 'count_visible'
    try:
        grade, select = _Grid(root, entry['grade_max']), _Grid(root, entry[select_name])
    except (OSError, ValueError, KeyError) as exc:
        warn('grade_map_unreadable', side, f'{exc}')
        return '<p class="note muted">등급 지도를 읽지 못해 위치 지도를 그리지 못했습니다(경고 참고).</p>', {}
    window = candidates.window_bounds(tuple(query['center_xy_um']), query['window_um'])
    members = [r for r in product['regions'] if r['merged_into'] == region['region_id']]
    box = _crop_box(grade, [window] + [tuple(r['region_bbox_um']) for r in members])
    if box is None:
        return '<p class="note muted">질의 창이 지도 밖이라 위치 지도를 그리지 못했습니다.</p>', {}
    try:
        codes, counts = grade.read(box), select.read(box)
    except (OSError, ValueError) as exc:
        warn('grade_map_unreadable', side, f'{exc}')
        return '<p class="note muted">등급 지도를 읽지 못해 위치 지도를 그리지 못했습니다(경고 참고).</p>', {}
    selected = counts >= args['min_count']
    shapes = _paste_layouts(layouts, case_ids, grid=grade, box=box, warn=warn, side=side) if layouts.index is not None else {}
    layer_names = sorted({layer for _, layer in shapes})
    slots = _layer_slots(layer_names)
    split = len(layer_names) > LAYER_SLOTS
    height, width = codes.shape
    factor = max(1, math.ceil(max(height, width) / MAP_MAX_CELLS))
    groups = [(None, shapes)] if not split else [(layer, {k: v for k, v in shapes.items() if k[1] == layer}) for layer in layer_names]
    figures, bytes_used = [], 0
    for layer, group in groups:
        group_slots = slots if not split else {layer: 1}
        f = factor
        while True:
            svg = _map_svg(f, grade, box, codes, selected, group, group_slots, window, layer_names)
            if len(svg) <= MAP_MAX_BYTES or f >= 16:
                break
            f *= 2
        bytes_used += len(svg)
        used = sorted({l for _, l in group}) if group else []
        legend = ''.join(f'<span><i class="lf l{group_slots[l]}" style="border:1px solid"></i>{escape(l)}</span>' for l in used if group_slots.get(l))
        title = (f'{escape(layer)} 전극' if split else '전극 layout(layer 별 색)')
        figures.append(f'<figure>{svg}<figcaption>{title} · 최고 등급 · 질의 창 · 선정 셀'
                       f'{"<div class=legend>" + legend + "</div>" if legend else ""}'
                       f'<br>칸에 마우스를 올리면 전극 이름(layer)</figcaption></figure>')
    note = ''
    if layouts.index is None:
        note = '<p class="note warn">전극 layout 을 읽지 못해 layout 없이 그렸습니다(경고 참고).</p>'
    elif not shapes:
        note = '<p class="note muted">이 ROI 를 덮는 쌍의 전극 layout 이 창 주변에 없습니다.</p>'
    layout_summary.append({'region_id': region['region_id'], 'layers': layer_names, 'electrodes': len(shapes),
                           'split_by_layer': split, 'cells': [height, width], 'downsample': factor, 'svg_bytes': bytes_used})
    return f'<div class="maps">{"".join(figures)}</div>{note}', {'crop_box_cells': list(box)}


# ---- layer 별 지도 ------------------------------------------------------------------------------------------------

def _thumb(values, kind, windows, label):
    """셀 값 배열 → <div class=thumb>(img + 질의 창 SVG). kind: 'grade' | 'count'."""
    factor = max(1, math.ceil(max(values.shape) / THUMB_MAX_PX))
    pooled = _pool(values, factor, -1)
    H, W = pooled.shape
    rgba = np.zeros((H, W, 4), dtype=np.uint8)
    if kind == 'grade':
        for code, color in GRADE_RGB.items():
            rgba[pooled == code] = (*color, 255)
    else:
        top = int(pooled.max()) if pooled.size else 0
        for value in np.unique(pooled[pooled > 0]):
            step = min(len(SEQ_RGB) - 1, int(math.floor(value / top * len(SEQ_RGB) - 1e-9)))
            rgba[pooled == value] = (*SEQ_RGB[step], 255)
    k = max(1, THUMB_MAX_PX // max(H, W))
    image = Image.fromarray(rgba, 'RGBA')
    if k > 1:
        image = image.resize((W * k, H * k), Image.Resampling.NEAREST)
    buffer = io.BytesIO()
    image.save(buffer, format='PNG', optimize=False)
    boxes = ''.join(f'<rect class="win" x="{(a / factor):.2f}" y="{(b / factor):.2f}" width="{max((c - a) / factor, 1):.2f}" '
                    f'height="{max((d - b) / factor, 1):.2f}"/>' for a, b, c, d in windows)
    svg = f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" aria-hidden="true">{boxes}</svg>' if boxes else ''
    return (f'<figure><div class="thumb"><img src="{_png_uri(buffer.getvalue())}" width="{W * k}" height="{H * k}" alt="{escape(label)}">{svg}</div>'
            f'<figcaption>{escape(label)}</figcaption></figure>'), {'cells': [int(values.shape[0]), int(values.shape[1])], 'downsample': factor}


def _layer_maps(root, product, queries_of, args, warn, side):
    select_name = 'count_high' if args['roi_grade'] == 'high' else 'count_visible'
    select_label = 'High 전극 수(선정)' if args['roi_grade'] == 'high' else 'Low+High 전극 수(선정)'
    cards, notes = [], []
    for entry in product['grade_maps']:
        key = entry['key']
        title = key['layer']
        same = [e for e in product['grade_maps'] if e['key']['layer'] == key['layer']]
        detail = ' / '.join(str(key[k]) for k in ('source_id', 'process_step_id', 'exposure_id', 'unit_id'))
        windows = []
        for query in queries_of.values():
            if all(query['map_key'].get(k) == key.get(k) for k in ('source_id', 'process_step_id', 'exposure_id', 'layer', 'unit_id')):
                windows.append(candidates.window_bounds(tuple(query['center_xy_um']), query['window_um']))
        try:
            grade, select = _Grid(root, entry['grade_max']), _Grid(root, entry[select_name])
            shape_box = (0, 0) + grade.shape
            full = (0, 0, grade.shape[0], grade.shape[1])
            codes, counts = grade.read(full), select.read((0, 0, select.shape[0], select.shape[1]))
        except (OSError, ValueError, KeyError) as exc:
            warn('grade_map_unreadable', side, f'{exc}')
            cards.append(f'<div class="card"><b>{escape(title)}</b><p class="note muted">지도를 읽지 못했습니다(경고 참고).</p></div>')
            continue
        pix = [((a - grade.origin[0]) / grade.pitch, (b - grade.origin[1]) / grade.pitch,
                (c - grade.origin[0]) / grade.pitch, (d - grade.origin[1]) / grade.pitch) for a, b, c, d in windows]
        count_html, _ = _thumb(np.where(counts < 0, 0, counts), 'count', pix, f'{select_label} (최대 {int(counts.max()) if counts.size else 0})')
        grade_html, info = _thumb(np.where(codes < 0, 0, codes), 'grade', pix, '최고 등급 (실선: 질의 창)')
        notes.append({'layer': title, **info})
        sub = f'<div class="note" title="{escape(detail)}">{escape(detail)}</div>' if len(same) > 1 else ''
        cards.append(f'<div class="card"><b>{escape(title)}</b>{sub}<div class="maps">{count_html}{grade_html}</div></div>')
    return cards, notes


# ---- 구성 --------------------------------------------------------------------------------------------------------

def _settings(queries, vulnerability):
    args = queries['args']
    provenance = [vulnerability['products'][s]['dv_provenance'] for s in SIDES]
    thresholds = {p.get('threshold_v') for p in provenance}
    threshold = (f'{provenance[0].get("threshold_v")} V' if len(thresholds) == 1 and provenance[0].get('threshold_v') is not None
                 else ' / '.join(f'{s} {p.get("threshold_v")}' for s, p in zip(SIDES, provenance)))
    if len(thresholds) == 1 and provenance[0].get('threshold_v') is None:
        threshold = '없음(ΔV>0 이면 High)'
    grade_rules = sorted({p.get('grade_rule_version') or '-' for p in provenance})
    status = queries.get('status')
    row = lambda *cells: '<tr>' + ''.join(f'<th class="l">{escape(a)}</th><td class="l">{b}</td>' for a, b in cells) + '</tr>'
    grade_text = {'high': 'high (High 쌍만 센 지도)', 'low_or_high': 'low_or_high (Low·High 쌍을 센 지도)'}.get(args['roi_grade'], args['roi_grade'])
    return _Html(
        '<div class="card"><table>'
        + row(('K (최소 전극 수)', escape(str(args['min_count']))), ('선정 등급', escape(grade_text)), ('창 W', f'{escape(str(args["window_um"]))} µm'),
              ('병합 R', escape(str(args['merge_overlap']))))
        + row(('low–low 경계', escape(threshold)), ('등급 규칙', f'<code>{escape(", ".join(grade_rules))}</code>'),
              ('순위 규칙', f'<code>{escape(str(vulnerability.get("score_rule_version")))}</code>'),
              ('상태', escape(f'{status} (exit {0 if status == "available" else 3})')))
        + row(('max-candidates', escape(str(args.get('max_candidates')) if args.get('max_candidates') is not None else '없음'),),
              ('대표 지름', f'{escape(str(args.get("representative_diameter_um")))} µm'))
        + '</table></div>')


def _kpis(product):
    pairs = product['pairs']
    counts = {g: sum(1 for p in pairs if p['grade'] == g) for g in ('High', 'Low', 'None')}
    total = sum(counts.values()) or 1
    x, bars = 2.0, ''
    for grade in ('High', 'Low', 'None'):
        width = counts[grade] / total * 296
        bars += (f'<rect x="{x:.1f}" y="4" width="{max(width - 2, 1):.1f}" height="14" rx="3" class="g-{grade}"><title>{grade}: {counts[grade]} 쌍</title></rect>'
                 f'<text x="{x + 2:.1f}" y="31" font-size="11" fill="var(--text-secondary)">{grade} {counts[grade]}</text>')
        x += width
    ungraded = len(product['ungraded_cases'])
    reps = sum(1 for r in product['regions'] if r.get('is_representative'))
    return _Html(
        '<div class="grid kpis">'
        f'<div class="card kpi"><div class="v">{reps}</div><div class="l">등급 ROI (병합 후)</div><div class="m">선정 전극 수 ≥ K 인 영역 {len(product["regions"])}개를 병합한 질의</div></div>'
        f'<div class="card kpi"><div class="v">{len(product["unweighted_regions"])}</div><div class="l">비가중 영역 (비교용)</div><div class="m">전체 전극 수 ≥ K, export 안 함</div></div>'
        f'<div class="card kpi"><div class="l">쌍 등급 분포</div><svg width="100%" height="34" viewBox="0 0 300 34" role="img" aria-label="등급 분포">{bars}</svg></div>'
        f'<div class="card kpi"><div class="v">{ungraded}</div><div class="l">등급 없는 case</div><div class="m">{"dv-grade 에 없는 case — 경고 참고" if ungraded else "모두 등급 있음"}</div></div>'
        '</div>')


def _rank_table(rows, args):
    head = ['등급 순위', 'ROI', 'layer', ('High' if args['roi_grade'] == 'high' else 'Low+High') + '(선정) 전극 수', '최고 등급', '전체 전극 수',
            '비가중 순위', '변화', '면적 µm²', '창 포함', '상태']
    body = []
    for r in rows:
        change = (f'<span class="up">▲{r["change"]}</span>' if r['change'] > 0 else
                  f'<span class="muted">▼{-r["change"]}</span>' if r['change'] < 0 else '<span class="muted">–</span>')
        body.append([r['rank_graded'], _Html(f'<b title="{escape(r["region_id"])}">{r["label"]}</b>'), r['layer'], r['sel_max_count'], _badge(r['grade_max']),
                     r['max_count'], r['rank_unweighted'], _Html(change), _fmt_area(r['area_um2']), _fmt_ratio(r['window_ratio']),
                     _Html(escape(r['state']) + _flags(r['flags']))])
    return _table(head, body, left=(1, 2, 4, 7, 10))


def _dropped_table(rows):
    head = ['영역', 'layer', '전체 전극 수', '선정 전극 수', '최고 등급', '빠진 이유']
    body = [[_Html(f'<b title="{escape(r["region_id"])}">{r["label"]}</b>'), r['layer'], r['max_count'], r['sel_max_count'],
             _badge(r['grade_max']), _Html(f'<span class="muted">{escape(r["reason"])}</span>')] for r in rows]
    return _table(head, body, left=(0, 1, 4, 5))


def _pair_table(rows):
    head = ['쌍', '전극 A (layer)', '전극 B (layer)', '불량', '등급', 'ΔV max (V)', '원본 CA µm²', 'ROI 내 CA µm² *']
    body = [[_Html(f'<b title="{escape(r["case_name"])}">{escape(r["pair_key"])}</b>'), f'{r["electrode_a"]} ({r["layer_a"]})',
             f'{r["electrode_b"]} ({r["layer_b"]})', r['failure_type'], _badge(r['grade']), r['dv_v'],
             _fmt_ca(r, 'original_ca_um2'), _fmt_ca(r, 'ca_in_region_um2')] for r in rows]
    return _table(head, body, left=(0, 1, 2, 3, 4))


def _funnel(queries):
    out = ''
    for item in queries['funnel']['directions']:
        stages = item['stages']
        top = max([s['count'] for s in stages] + [1])
        rows = ''.join(
            f'<tr><td class="l">{escape(s["stage"])}</td><td>{s["count"]}</td><td>{s["dropped"]}</td>'
            f'<td class="l"><div class="bar" style="width:{s["count"] / top * 100:.1f}%" title="{escape(s["stage"])}: {s["count"]}"></div></td>'
            f'<td class="l muted">{escape(", ".join(f"{k}: {v}" for k, v in sorted(s["reasons"].items())))}</td></tr>' for s in stages)
        out += (f'<div class="card"><b>{escape(item["direction"])}</b><table><tr><th class="l">단계</th><th>수</th><th>단계 정의상 탈락</th><th class="l"></th><th class="l">사유</th></tr>'
                f'{rows}</table><p class="note">{escape(item.get("note", ""))}</p></div>')
    note = ('단계 수는 서로 다른 것을 센 값입니다(비가중 영역·선정 지도 영역·병합 후 질의). graded_regions 의 탈락 수는 앞 단계와의 차이가 아니라 '
            '선정 지도가 K 미만이라 영역이 되지 못한 count_all 영역 수입니다. 단계 사이의 보존 관계로 읽지 마세요.')
    return _Html(f'<p class="note">{escape(note)}</p><div class="grid two">{out}</div>')


def _warning_lines(queries, extra):
    lines = []
    for item in queries.get('warnings', []):
        detail = item.get('detail') or ''
        lines.append((item.get('code'), item.get('product'), item.get('severity', 'warning'), detail or json.dumps(
            {k: v for k, v in item.items() if k not in ('code', 'product', 'severity')}, ensure_ascii=False, sort_keys=True)[:300]))
    for item in queries.get('notes', []):
        lines.append((item.get('code'), item.get('product'), item.get('severity', 'info'), item.get('detail') or ''))
    for code, side, detail in extra:
        lines.append((code, side, 'warning', detail))
    return lines


def _warnings_html(lines):
    if not lines:
        return _Html('<p class="note">경고 없음.</p>')
    return _Html('<ul class="note">' + ''.join(
        f'<li><code>{escape(str(code))}</code> ({escape(str(product))}, {escape(str(sev))}) {escape(str(detail))}</li>' for code, product, sev, detail in lines) + '</ul>')


def _sources(rows):
    return _table(['항목', '경로', 'sha256'], [[a, _Html(f'<code>{escape(str(b))}</code>'), _Html(f'<code>{escape(str(c))}</code>')] for a, b, c in rows], left=(0, 1, 2))


def _source_rows(folder, queries, hashes):
    rows = [('candidate_queries_v1.json', str(folder / QUERIES_NAME), hashes[QUERIES_NAME]),
            ('vulnerability_v1.json', str(folder / VULNERABILITY_NAME), hashes[VULNERABILITY_NAME])]
    for side in SIDES:
        record = queries['inputs'][side]
        dv = record.get('dv_grades') or {}
        rows += [(f'dv-grade ({side}) manifest', dv.get('dir'), dv.get('manifest_sha256')), (f'전압 시트 ({side})', '(dv-grade manifest 의 sheet)', dv.get('sheet_sha256')),
                 (f'asset manifest ({side})', f'{record.get("data_dir")}/{(record.get("asset_manifest") or {}).get("path")}', (record.get('asset_manifest') or {}).get('sha256')),
                 (f'dataset manifest ({side})', f'{record.get("data_dir")}/{record.get("manifest_path")}', record.get('manifest_sha256')),
                 (f'export ({side})', f'{side}_export/review_rois.json', record.get('export_sha256'))]
    rows.append(('config', '(candidates-dv 실행 설정)', queries['inputs'].get('config_sha256')))
    return rows


# ---- 조립 --------------------------------------------------------------------------------------------------------

def build_report(candidates_dv_dir, top_n=10):
    """(html, report dict, warnings 목록(코드, 제품, 설명), 입력 status). 파일을 쓰지 않는다."""
    folder = Path(candidates_dv_dir)
    queries, vulnerability = _load_inputs(folder)
    hashes = {name: _sha256(folder / name) for name in (QUERIES_NAME, VULNERABILITY_NAME)}
    args = queries['args']
    warnings = []

    def warn(code, side, detail):
        item = (code, side, detail)
        if item not in warnings:
            warnings.append(item)

    by_query = _queries_by_region(queries)
    sections, report_products = {}, {}
    for side in SIDES:
        product = vulnerability['products'][side]
        record = queries['inputs'][side]
        layouts = _Layouts(side, record, warn)
        rank_rows, n_reps = _rank_rows(product, by_query[side], top_n)
        dropped, n_dropped = _dropped_rows(product, args['min_count'], top_n)
        rois, roi_report, layout_summary = '', [], []
        for row in rank_rows:
            region = next(r for r in product['regions'] if r['region_id'] == row['region_id'])
            query = by_query[side].get(region['region_id'])
            if query is None:
                continue          # export 하지 않은 질의(max-candidates 로 잘림)는 상세를 그리지 않는다
            pair_rows, members = _pairs_for_roi(product, region)
            figure, extra = _roi_figures(folder, product, region, query, layouts, args, warn, side, [r['case_name'] for r in pair_rows], layout_summary)
            ungraded = _ungraded_in(product, region)
            merged = [m for m in members if m != region['region_id']]
            info = _table(['항목', '값'], [
                ['중심', f'({region["center_xy_um"][0]:.2f}, {region["center_xy_um"][1]:.2f}) µm'],
                ['선정 셀 중심', f'({region["sel_centre_xy_um"][0]:.2f}, {region["sel_centre_xy_um"][1]:.2f}) µm'],
                ['창 포함 비율', f'{_fmt_ratio(query.get("sel_cells_in_window_ratio"))} ({query.get("sel_cells_in_window")} / {query.get("sel_cells_total")} 셀)'],
                ['병합', ', '.join(m[:15] for m in merged) if merged else '없음'],
                ['질의 창', f'{query["window_um"]} µm']], left=(0, 1))
            ungraded_html = (f'<p class="note warn">등급 없는 case {len(ungraded)}건(표에 없음): {escape(", ".join(ungraded[:5]))}{" …" if len(ungraded) > 5 else ""}</p>'
                             if ungraded else '')
            rois += (f'<div class="card" style="margin-bottom:12px"><h3 style="margin-top:0">{row["rank_graded"]}위 · {row["label"]} · {escape(row["layer"])} '
                     f'{_badge(row["grade_max"])} {_flags(row["flags"])}</h3>'
                     f'<div class="grid roi"><div>{figure}{info}</div>'
                     f'<div><b>이 ROI 를 덮는 쌍 ({len(pair_rows)})</b>'
                     f'<p class="note" style="margin:2px 0 6px">ΔV max 는 구동 중 두 전극 사이 최대 전압 차이입니다. 최대 타이밍·임피던스는 json 에만 남깁니다. 정렬: 등급 → ΔV.</p>'
                     f'<div style="overflow-x:auto">{_pair_table(pair_rows)}</div><p class="note">{escape(FOOTNOTE_CA)}</p>{ungraded_html}</div></div></div>')
            roi_report.append({'label': row['label'], 'region_id': region['region_id'], 'pairs': pair_rows, 'merged_members': members,
                               'ungraded_cases': ungraded, **extra})
        layer_cards, thumb_notes = _layer_maps(folder, product, by_query[side], args, warn, side)
        report_products[side] = {'product_id': product['product_id'], 'rank_rows': rank_rows, 'n_graded_rois': n_reps, 'dropped_rows': dropped,
                                 'n_dropped': n_dropped, 'rois': roi_report, 'layouts': {'status': layouts.status, 'maps': layout_summary},
                                 'layer_thumbnails': thumb_notes}
        sections[side] = (
            f'<div class="side" id="side-{side}">{_kpis(product)}'
            f'<h2>1. 등급 순위와 비가중 순위</h2><p class="sub">등급 ROI 끼리 비교합니다. 비가중 순위는 같은 ROI 를 전체 전극 수만으로 정렬한 순위입니다. ▲ 는 등급 반영으로 올라간 것입니다. '
            f'상위 {len(rank_rows)}개 / 전체 {n_reps}개.</p><div class="card" style="overflow-x:auto">{_cell(_rank_table(rank_rows, args))}</div>'
            f'<h3>1-1. 전극은 많지만 등급 때문에 빠진 영역 (비가중 영역, export 안 함)</h3>'
            f'<p class="note">전체 {n_dropped}개 중 전체 전극 수 순 상위 {len(dropped)}개.</p><div class="card" style="overflow-x:auto">{_cell(_dropped_table(dropped))}</div>'
            f'<h2>2. ROI 별 상세 (등급 순위 상위 {len(roi_report)})</h2>'
            f'<div class="legend"><span>전극 layout: layer 마다 다른 색(지도 아래 범례)</span><span style="margin-left:8px">등급:</span>'
            f'<span><i class="g-None"></i>None (ΔV 0)</span><span><i class="g-Low"></i>Low</span><span><i class="g-High"></i>High</span>'
            f'<span><i style="background:transparent;border:2px solid var(--win)"></i>질의 창 (W)</span>'
            f'<span><i style="background:transparent;border:2px dashed var(--sel)"></i>선정 셀 (선정 전극 수 ≥ K)</span></div>{rois or "<p class=note>상세를 그릴 ROI 가 없습니다.</p>"}'
            f'<h2>3. layer 별 지도</h2><p class="sub">전체 frame 입니다(실선: 질의 창). 전체 전극 수(count_all) 지도는 candidates-dv 가 쓰지 않아 여기에 없습니다.</p>'
            f'<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(380px,1fr))">{"".join(layer_cards)}</div></div>')
    extra_warnings = list(warnings)
    lines = _warning_lines(queries, extra_warnings)
    title = '취약점 ROI 보고서 — ΔV 등급 반영'
    products_text = ' · '.join(f'{s} <b>{escape(vulnerability["products"][s]["product_id"])}</b>' for s in SIDES)
    html = (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{escape(title)}</title><style>{CSS}</style></head><body><main>'
        f'<h1>{escape(title)}</h1><p class="sub">{products_text} · 대표 지름 {escape(str(args.get("representative_diameter_um")))} µm · '
        f'생성 <code>vulnerability-report</code> (입력: <code>candidates-dv</code> 출력 폴더)</p>'
        f'{_cell(_settings(queries, vulnerability))}'
        '<input class="tabr" type="radio" name="side" id="tab-reference" checked><input class="tabr" type="radio" name="side" id="tab-target">'
        '<div class="tabs" role="group" aria-label="제품 선택">'
        + ''.join(f'<label for="tab-{s}">{s} {escape(vulnerability["products"][s]["product_id"])}</label>' for s in SIDES) + '</div>'
        f'<div class="sides">{"".join(sections[s] for s in SIDES)}</div>'
        f'<h2>4. 깔때기와 경고</h2>{_cell(_funnel(queries))}<div class="card" style="margin-top:12px"><b>경고</b>{_cell(_warnings_html(lines))}</div>'
        f'<h2>5. 출처</h2><details open><summary>입력 파일과 해시</summary><div class="card" style="overflow-x:auto">{_cell(_sources(_source_rows(folder, queries, hashes)))}</div></details>'
        '<p class="note">보고서의 모든 수치는 <code>vulnerability_v1.json</code>·<code>candidate_queries_v1.json</code> 값을 그대로 옮긴 것이며 다시 계산하지 않습니다. '
        '표시용 파생값은 순위 변화(비가중 순위 − 등급 순위)와 창 포함 % 반올림뿐입니다.</p>'
        '</main></body></html>')
    report = {'schema_version': REPORT_SCHEMA, 'top_n': top_n, 'input_status': queries.get('status'),
              'inputs': {'candidates_dv_dir': str(folder.resolve()), 'sha256': hashes}, 'args': args, 'products': report_products,
              'warnings': [{'code': c, 'product': p, 'detail': d} for c, p, d in extra_warnings]}
    return html, report, extra_warnings, queries.get('status')


def _write(path, text):
    with open(str(path), 'w', encoding='utf-8', newline='\n') as handle:    # Path.write_text(newline=) 는 3.10 부터
        handle.write(text)


def run_vulnerability_report(candidates_dv_dir, out, top_n=10):
    """report.html, report.json, execution.json 을 쓴 새 폴더 `out` 을 만들고 결과 dict 를 돌려준다.
    입력 오류와 --out 오류(이미 있음, 입력 폴더 안)는 쓰기 전에 NumericContractError (종료 코드 2)."""
    if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n < 1:
        raise _bad('invalid_vulnerability_report_argument', f'--top-n 은 1 이상의 정수여야 합니다: {top_n!r}')
    folder, out = Path(candidates_dv_dir), Path(out)
    if out.exists() or out.is_symlink():
        raise _bad('output_dir_exists', str(out))
    resolved_in, resolved_out = folder.resolve(), out.resolve()
    if resolved_out == resolved_in or resolved_in in resolved_out.parents:
        raise _bad('output_is_input', '--out 은 candidates-dv 출력 폴더 밖의 새 폴더여야 합니다')
    html, report, warnings, status = build_report(folder, top_n)
    partial = status != 'available' or bool(warnings)
    reasons = sorted({c for c, _, _ in warnings} | ({'input_status_partial'} if status != 'available' else set()))
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f'.{out.name}.staging-', dir=str(out.parent)))
    try:
        _write(stage / 'report.html', html)
        _write(stage / 'report.json', json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        result = {'status': 'partial' if partial else 'available', 'reasons': reasons, 'outputs': ['execution.json', 'report.html', 'report.json'],
                  'exit_code': 3 if partial else 0, 'report_html_bytes': (stage / 'report.html').stat().st_size}
        execution = {'command': 'vulnerability-report', **result, 'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
        _write(stage / 'execution.json', json.dumps(execution, ensure_ascii=False, indent=2) + '\n')
        # mkdtemp 는 0700 으로 만든다. 다른 출력 폴더처럼 umask 기준 권한으로 맞춘 뒤 옮긴다.
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(str(stage), 0o777 & ~umask)
        os.rename(str(stage), str(out))
    except BaseException:
        shutil.rmtree(str(stage), ignore_errors=True)
        raise
    result['outputs'] = [str(out / name) for name in result['outputs']]
    return result
