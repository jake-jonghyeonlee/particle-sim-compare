"""통합 HTML 보고서 (simcompare report). 이미 만들어진 출력 폴더만 읽고 수치는 다시 계산하지 않는다.

compare / electrode-map / compare-windows / export-review-rois 출력을 읽어 report.html 한 파일(이미지·SVG 내장),
report.json(보고서에 쓴 값과 입력 SHA256), execution.json을 쓴다. 색: 악화(Δ>0) 빨강, 개선(Δ<0) 파랑, 그래프의 a 회색·b 보라.
"""
from __future__ import annotations

import base64
import csv
from datetime import datetime, timezone
from html import escape
import hashlib
import json
from pathlib import Path

from .numeric_schema import NumericContractError

SCHEMA = 'final_report_v1'
EXPECTED_SCHEMAS = {'comparison': 'numeric_comparison_report_v1', 'map': 'numeric_electrode_map_report_v2',
                    'local_delta': 'local_delta_v1', 'thumbs': 'local_delta_thumbs_v1', 'rois': 'review_roi_v1'}
COMPARE_FILES = ('comparison.json', 'comparison_fractions.csv', 'comparison_summary.csv', 'comparison_failure_pieces.csv')
WINDOW_FILES = ('local_delta.json', 'local_delta_rows.csv')
FRACTION_METRICS = ('line', 'pixel_effective', 'transfer', 'union', 'lateral_only', 'vertical_only', 'simultaneous')
PIECES = ('lateral_only', 'vertical_only', 'simultaneous')
UNION_METRICS = ('union', 'lateral_only', 'vertical_only', 'simultaneous')
WORSE, BETTER, OTHER = '#c62828', '#1565c0', '#757575'
SERIES_A, SERIES_B = '#8a8a8a', '#7b3fa0'
PROVISIONAL = '임시 기준값'
NO_SUM_NOTE = '겹친 쌍의 Δ를 단순 합산하지 마세요'
CSS = (
    'body{font-family:system-ui,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;max-width:1400px;margin:1.5rem auto;padding:0 1rem;color:#222}'
    'h1{margin-bottom:.2rem}h2{margin-top:2rem;border-bottom:2px solid #ddd;padding-bottom:.2rem}h3{margin:1.2rem 0 .4rem}'
    'table{border-collapse:collapse;margin:.4rem 0}th,td{border:1px solid #ccc;padding:3px 8px;text-align:right;vertical-align:top;font-size:13px}'
    'th{background:#f3f3f3}td.l,th.l{text-align:left}small,.note{color:#555;font-size:12px}.note{margin:.2rem 0}'
    '.worse{color:' + WORSE + ';font-weight:600}.better{color:' + BETTER + ';font-weight:600}.zero{color:#555}'
    '.cards{display:flex;flex-wrap:wrap;gap:10px}.card{border:1px solid #ccc;border-radius:6px;padding:8px 12px;min-width:230px;max-width:330px;background:#fafafa}'
    '.card .big{font-size:22px;font-weight:700}.card .what{font-size:12px;color:#444;margin:.2rem 0}.card .src{font-size:11px;color:#777}'
    '.badge{display:inline-block;border-radius:10px;padding:0 8px;font-size:11px;margin-right:4px;background:#eee}'
    '.badge.warn{background:#fff3cd}.badge.syn{background:#ffe0b2}.badge.worse{background:#fde7e7}.badge.better{background:#e3effd}'
    '.pair{border:1px solid #ccc;border-radius:6px;padding:8px 12px;margin:10px 0;display:flex;flex-wrap:wrap;gap:16px}'
    '.pair .crops img{max-width:200px;max-height:200px;border:1px solid #999;image-rendering:pixelated}'
    '.pane{display:inline-block;margin:0 5px;text-align:center;font-size:11px;color:#555}.pane span{display:block}'
    '.missing{width:150px;height:70px;border:1px dashed #999;color:#777;font-size:11px;display:flex;align-items:center;justify-content:center}'
    '.charts{display:flex;flex-wrap:wrap;gap:6px}.maps{display:flex;flex-wrap:wrap;gap:16px}.maps figure{margin:0}'
    'details{margin:.5rem 0}summary{cursor:pointer;font-weight:600}.box{background:#fff8e1;border:1px solid #f0d78c;padding:6px 10px;margin:.5rem 0;font-size:13px}')


class ReportInputError(NumericContractError):
    """report 입력 오류. 명령행에서는 exit 2, report.html은 남기지 않는다."""


class _Html(str):
    """이미 escape된(또는 우리가 만든) HTML 조각. 표 셀에서 다시 escape하지 않는다."""


def _cell(value):
    return value if isinstance(value, _Html) else escape('' if value is None else str(value))


def _num(text):
    """CSV 문자열 → float. 빈 값·null은 None."""
    if text is None or isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        return float(text)
    text = str(text).strip()
    if text in ('', 'null', 'None', 'nan'):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _finite(value, what):
    """HTML/SVG 속성에 그대로 들어가는 기하 값: 유한한 숫자만 허용하고 float로 돌려준다. 아니면 exit 2."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float('inf'), float('-inf')):
        raise ReportInputError('invalid_geometry_value', f'{what}: 유한한 숫자가 아닙니다 ({str(value)[:40]!r})')
    return float(value)


def _count(value, what):
    """개수 값: 0 이상의 정수만 허용(None은 값 없음). 아니면 exit 2."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReportInputError('invalid_count_value', f'{what}: 0 이상의 정수가 아닙니다 ({str(value)[:40]!r})')
    return value


def _fmt(value, digits=6):
    if value is None:
        return '—'
    if isinstance(value, float):
        return f'{value:.{digits}g}'
    return str(value)


def _delta_class(value):
    return 'zero' if value is None or value == 0 else 'worse' if value > 0 else 'better'


def _delta_html(value, digits=6):
    if value is None:
        return _Html('—')
    sign = '+' if value > 0 else ''
    return _Html(f'<span class="{_delta_class(value)}">{escape(sign + _fmt(float(value), digits))}</span>')


def _table(headers, rows, left=()):
    """headers: 열 제목, rows: 셀 목록의 목록, left: 왼쪽 정렬할 열 번호."""
    head = ''.join(f'<th class="{"l" if i in left else ""}">{escape(h)}</th>' for i, h in enumerate(headers))
    body = ''.join('<tr>' + ''.join(f'<td class="{"l" if i in left else ""}">{_cell(c)}</td>' for i, c in enumerate(row)) + '</tr>'
                   for row in rows)
    return _Html(f'<table><tr>{head}</tr>{body}</table>')


def _p(text, cls='note'):
    return f'<p class="{cls}">{escape(text)}</p>'


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _png_uri(data):
    return 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')


class _Inputs:
    """읽은 입력 파일을 한곳에서 읽고 SHA256을 기록한다."""

    def __init__(self):
        self.files = {}

    def path(self, folder, name, role):
        path = Path(folder) / name
        if not path.is_file():
            raise ReportInputError('missing_input_file', f'{role}: {path} 파일이 없습니다')
        self.files[str(path.resolve())] = {'role': role, 'sha256': _sha256(path)}
        return path

    def json(self, folder, name, role, schema=None):
        path = self.path(folder, name, role)
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise ReportInputError('unreadable_input_file', f'{path}: {exc}') from exc
        if schema is not None and (not isinstance(payload, dict) or payload.get('schema_version') != schema):
            found = payload.get('schema_version') if isinstance(payload, dict) else None
            raise ReportInputError('unexpected_schema_version', f'{path}: schema_version {found!r}, 기대값 {schema!r}')
        return payload

    def csv(self, folder, name, role):
        path = self.path(folder, name, role)
        try:
            with open(path, newline='', encoding='utf-8') as handle:
                return list(csv.DictReader(handle))
        except (OSError, ValueError, csv.Error) as exc:
            raise ReportInputError('unreadable_input_file', f'{path}: {exc}') from exc

    def png(self, folder, name, role):
        path = self.path(folder, name, role)
        return path.read_bytes()


def _json_list(text):
    try:
        value = json.loads(text) if text else []
    except ValueError:
        return [str(text)]
    return value if isinstance(value, list) else [value]


# ---- SVG 그래프 -------------------------------------------------------------------------------------------------

def _nice_ticks(low, high, count=4):
    if high == low:
        high = low + 1.0
    return [low + (high - low) * i / count for i in range(count + 1)]


def _chart_frame(title, width, height, body, y_ticks, y_of, left=58, bottom=26):
    lines = ''.join(f'<line x1="{left}" x2="{width - 8}" y1="{y_of(t):.1f}" y2="{y_of(t):.1f}" stroke="#e5e5e5"/>'
                    f'<text x="{left - 4}" y="{y_of(t) + 3:.1f}" font-size="9" text-anchor="end" fill="#555">{escape(_fmt(t, 3))}</text>'
                    for t in y_ticks)
    return (f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-label="{escape(title)}">'
            f'<text x="{left}" y="12" font-size="11" font-weight="600" fill="#222">{escape(title)}</text>{lines}{body}'
            f'<line x1="{left}" x2="{width - 8}" y1="{height - bottom}" y2="{height - bottom}" stroke="#888"/></svg>')


def _line_chart(title, xs, series, width=330, height=170):
    """series: [(이름, 색, [y 또는 None...])]. None은 선을 끊는다."""
    left, bottom, top = 58, 26, 20
    values = [v for _, _, ys in series for v in ys if v is not None]
    if not values or not xs:
        return _Html(f'<div class="missing">{escape(title)}: 값 없음</div>')
    low, high = min(0.0, min(values)), max(0.0, max(values))
    if high == low:
        high = low + 1.0
    x_low, x_high = min(xs), max(xs)
    x_of = lambda x: left + (width - 8 - left) * ((x - x_low) / (x_high - x_low) if x_high != x_low else 0.5)
    y_of = lambda y: height - bottom - (height - bottom - top) * (y - low) / (high - low)
    body = ''
    for name, colour, ys in series:
        run = []
        for x, y in zip(xs, ys):
            if y is None:
                if len(run) > 1:
                    body += f'<polyline fill="none" stroke="{colour}" stroke-width="1.6" points="{" ".join(run)}"/>'
                run = []
                continue
            run.append(f'{x_of(x):.1f},{y_of(y):.1f}')
            body += f'<circle cx="{x_of(x):.1f}" cy="{y_of(y):.1f}" r="2" fill="{colour}"/>'
        if len(run) > 1:
            body += f'<polyline fill="none" stroke="{colour}" stroke-width="1.6" points="{" ".join(run)}"/>'
    body += ''.join(f'<text x="{x_of(x):.1f}" y="{height - 14}" font-size="9" text-anchor="middle" fill="#555">{escape(_fmt(x, 3))}</text>'
                    for x in (x_low, x_high))
    body += f'<text x="{(left + width) / 2:.0f}" y="{height - 3}" font-size="9" text-anchor="middle" fill="#555">지름 µm</text>'
    body += ''.join(f'<text x="{width - 8}" y="{12 + 10 * i}" font-size="9" text-anchor="end" fill="{c}">■ {escape(n)}</text>'
                    for i, (n, c, _) in enumerate(series))
    return _Html(_chart_frame(title, width, height, body, _nice_ticks(low, high), y_of, left, bottom))


def _bar_chart(title, categories, series, width=330, height=190):
    """categories: [이름...], series: [(이름, 색, [값 또는 None...])]."""
    left, bottom, top = 58, 38, 20
    values = [v for _, _, ys in series for v in ys if v is not None]
    if not values:
        return _Html(f'<div class="missing">{escape(title)}: 값 없음</div>')
    low, high = min(0.0, min(values)), max(0.0, max(values))
    if high == low:
        high = low + 1.0
    y_of = lambda y: height - bottom - (height - bottom - top) * (y - low) / (high - low)
    slot = (width - 8 - left) / len(categories)
    bar = slot / (len(series) + 1)
    body = ''
    for ci, category in enumerate(categories):
        for si, (_, colour, ys) in enumerate(series):
            y = ys[ci]
            if y is None:
                continue
            x = left + ci * slot + bar * (si + 0.5)
            top_y, base_y = sorted((y_of(y), y_of(0.0)))
            body += f'<rect x="{x:.1f}" y="{top_y:.1f}" width="{bar:.1f}" height="{max(base_y - top_y, 0.5):.1f}" fill="{colour}"/>'
        body += (f'<text x="{left + (ci + 0.5) * slot:.1f}" y="{height - bottom + 12}" font-size="9" text-anchor="middle" fill="#555">'
                 f'{escape(category)}</text>')
    body += ''.join(f'<text x="{width - 8}" y="{12 + 10 * i}" font-size="9" text-anchor="end" fill="{c}">■ {escape(n)}</text>'
                    for i, (n, c, _) in enumerate(series))
    return _Html(_chart_frame(title, width, height, body, _nice_ticks(low, high), y_of, left, bottom))


# ---- 전체 frame 위치도 ------------------------------------------------------------------------------------------

def _png_size(data):
    return int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')


def _overlap_area(first, second):
    width = min(first[2], second[2]) - max(first[0], second[0])
    height = min(first[3], second[3]) - max(first[1], second[1])
    return width * height if width > 0 and height > 0 else 0.0


class _FrameMaps:
    """제품(side)·frame별 전체 위치도. overview PNG는 문서 앞쪽 <defs>에 한 번만 넣고 locator에서 <use>로 다시 쓴다."""

    def __init__(self):
        self.frames = {}   # (side, frame_id) → {'origin', 'pitch', 'shape', 'png', 'note', 'index'}

    def add(self, side, frame_id, geometry, png, note, source=None):
        self.frames[(side, frame_id)] = {**geometry, 'png': png, 'note': note, 'source': source, 'index': len(self.frames)}

    def defs(self):
        images = ''
        for info in self.frames.values():
            if info['png'] is None:
                continue
            ox, oy = info['origin']
            width, height = info['pitch'] * info['shape'][1], info['pitch'] * info['shape'][0]
            images += (f'<image id="ov{info["index"]}" x="{ox}" y="{oy}" width="{width}" height="{height}" '
                       f'preserveAspectRatio="none" href="{_png_uri(info["png"])}" style="image-rendering:pixelated"/>')
        return f'<svg width="0" height="0" style="position:absolute"><defs>{images}</defs></svg>'

    def svg(self, side, frame_id, rects, *, labels=True, max_width=560):
        """rects: [(pair_id, [x0,y0,x1,y1], 색)]. 전체 위치도는 모든 쌍을, 카드의 작은 위치도는 그 쌍 하나만 넘긴다(HTML 크기가 쌍 수에 비례)."""
        info = self.frames.get((side, frame_id))
        if info is None:
            return _Html('<div class="missing">frame 정보 없음</div>')
        ox, oy = info['origin']
        width, height = info['pitch'] * info['shape'][1], info['pitch'] * info['shape'][0]
        size = max(width, height)
        body = f'<rect x="{ox}" y="{oy}" width="{width}" height="{height}" fill="#f4f4f4"/>'
        if info['png'] is not None:
            body += f'<use href="#ov{info["index"]}"/>'
        body += (f'<rect x="{ox}" y="{oy}" width="{width}" height="{height}" fill="none" stroke="#444" '
                 'stroke-width="1" vector-effect="non-scaling-stroke"/>')
        for pair_id, bounds, colour in rects:
            cx, cy = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
            body += (f'<rect x="{bounds[0]}" y="{bounds[1]}" width="{bounds[2] - bounds[0]}" height="{bounds[3] - bounds[1]}" '
                     f'fill="{colour}" fill-opacity=".18" stroke="{colour}" stroke-width="2" vector-effect="non-scaling-stroke"/>'
                     f'<circle cx="{cx}" cy="{cy}" r="{size / 150}" fill="{colour}"/>')
            if labels:
                body += (f'<text x="{bounds[0]}" y="{bounds[1] - size / 200}" font-size="{size / 40}" fill="{colour}" '
                         f'font-weight="700">{escape(pair_id)}</text>')
        pad = size / 30
        return _Html(f'<svg viewBox="{ox - pad} {oy - pad} {width + 2 * pad} {height + 2 * pad}" '
                     f'style="width:100%;max-width:{max_width}px;height:auto;background:#fff;border:1px solid #bbb" '
                     f'role="img" aria-label="{escape(side)} 전체 frame 위치도">{body}</svg>')


# ---- 입력 읽기와 검증 -------------------------------------------------------------------------------------------

def _product_ids(compare_json):
    ids = {}
    for label in ('reference', 'target'):
        layers = (compare_json.get(label) or {}).get('layers') or []
        found = sorted({layer['key']['product_id'] for layer in layers if isinstance(layer.get('key'), dict) and 'product_id' in layer['key']})
        ids[label] = found
    return ids


def _check_args(diameter_um, rank_window_um, top_n):
    for name, value in (('--diameter-um', diameter_um), ('--rank-window-um', rank_window_um)):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not value > 0 or value != value or value == float('inf'):
            raise ReportInputError('invalid_argument', f'{name}는 0보다 큰 유한한 수여야 합니다')
    if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n < 1:
        raise ReportInputError('invalid_argument', '--top-n은 1 이상의 정수여야 합니다')


def _same_float(first, second):
    return abs(float(first) - float(second)) < 1e-9


def _load(inputs, compare_dir, map_dir, windows_dir, rois_dir, diameter_um, rank_window_um):
    """모든 입력을 읽고 폴더 사이 일관성을 검사한다. 오류는 ReportInputError(exit 2)."""
    loaded = {}
    for name in COMPARE_FILES:
        inputs.path(compare_dir, name, 'compare')
    comparison = inputs.json(compare_dir, 'comparison.json', 'compare', EXPECTED_SCHEMAS['comparison'])
    loaded['comparison'] = comparison
    loaded['fractions'] = inputs.csv(compare_dir, 'comparison_fractions.csv', 'compare')
    loaded['summary'] = inputs.csv(compare_dir, 'comparison_summary.csv', 'compare')
    loaded['pieces'] = inputs.csv(compare_dir, 'comparison_failure_pieces.csv', 'compare')
    ids = _product_ids(comparison)
    if len(ids['reference']) != 1 or len(ids['target']) != 1:
        raise ReportInputError('product_id_unreadable', 'comparison.json에서 reference/target product_id를 하나씩 읽을 수 없습니다')
    products = {'a': ids['reference'][0], 'b': ids['target'][0]}
    loaded['products'] = products
    synthetic = {'compare': comparison.get('is_synthetic')}

    loaded['map'] = loaded['rois'] = loaded['windows'] = loaded['thumbs'] = None
    if map_dir is not None:
        mapped = inputs.json(map_dir, 'electrode_map.json', 'map', EXPECTED_SCHEMAS['map'])
        loaded['map'] = mapped
        synthetic['map'] = mapped.get('is_synthetic')
        seen = {layer.get('key', {}).get('product_id') for layer in mapped.get('layers', []) if isinstance(layer.get('key'), dict)}
        stray = {p for p in seen if p is not None} - set(products.values())
        if stray:
            raise ReportInputError('product_id_mismatch', f'map-dir의 product_id {sorted(stray)}가 compare-dir {products}와 다릅니다')
    if rois_dir is not None:
        rois = inputs.json(rois_dir, 'review_rois.json', 'rois', EXPECTED_SCHEMAS['rois'])
        loaded['rois'] = rois
        stray = {r.get('product_id') for r in rois.get('rois', []) if isinstance(r, dict)} - {None} - set(products.values())
        if stray:
            raise ReportInputError('product_id_mismatch', f'rois-dir의 product_id {sorted(stray)}가 compare-dir {products}와 다릅니다')
    if windows_dir is not None:
        for name in WINDOW_FILES:
            inputs.path(windows_dir, name, 'windows')
        delta = inputs.json(windows_dir, 'local_delta.json', 'windows', EXPECTED_SCHEMAS['local_delta'])
        loaded['windows'] = delta
        loaded['window_rows'] = inputs.csv(windows_dir, 'local_delta_rows.csv', 'windows')
        if (Path(windows_dir) / 'local_delta_thumbs.json').is_file():
            loaded['thumbs'] = inputs.json(windows_dir, 'local_delta_thumbs.json', 'windows', EXPECTED_SCHEMAS['thumbs'])
        for name in ('window_sensitivity_rows.csv', 'window_sensitivity_rank_agreement.csv'):
            loaded[name] = inputs.csv(windows_dir, name, 'windows') if (Path(windows_dir) / name).is_file() else None
        got = {side: (delta.get('products', {}).get(side) or {}).get('product_id') for side in ('a', 'b')}
        if got != products:
            raise ReportInputError('product_id_mismatch', f'windows-dir의 제품 {got}가 compare-dir {products}와 다릅니다')
        synthetic['windows'] = (delta.get('provenance') or {}).get('is_synthetic')
        sizes = delta.get('window_sizes_um') or []
        if not any(_same_float(size, rank_window_um) for size in sizes):
            raise ReportInputError('rank_window_not_available',
                                   f'--rank-window-um {rank_window_um:g}은 local_delta.json의 window_sizes_um에 없습니다. '
                                   f'사용 가능한 창(µm): {", ".join(f"{s:g}" for s in sizes)}')
        provenance = delta.get('provenance') or {}
        for name in ('n_ready', 'n_blocked'):
            _count(provenance.get(name), f'local_delta.json provenance.{name}')
        diameters = sorted({_num(r.get('diameter_um')) for r in loaded['window_rows'] if _num(r.get('diameter_um')) is not None})
        # 행이 하나도 없는 결과(모든 쌍이 blocked)는 정당한 결과다. 지름 검사는 행이 있는데 그 지름이 없을 때만 한다.
        if loaded['window_rows'] and not any(_same_float(d, diameter_um) for d in diameters):
            raise ReportInputError('diameter_not_available',
                                   f'--diameter-um {diameter_um:g}은 local_delta_rows.csv에 없습니다. '
                                   f'사용 가능한 지름(µm): {", ".join(f"{d:g}" for d in diameters)}')
    flags = {k: v for k, v in synthetic.items() if v is not None}
    if len(set(flags.values())) > 1:
        raise ReportInputError('mixed_is_synthetic', f'입력 폴더의 is_synthetic이 섞여 있습니다: {flags}')
    loaded['is_synthetic'] = next(iter(flags.values()), None)
    return loaded


# ---- 섹션 조립 ---------------------------------------------------------------------------------------------------

class _Report:
    """섹션별 HTML, 섹션별 상태, report.json용 값을 모은다."""

    def __init__(self, settings):
        self.settings = settings
        self.html = {}
        self.data = {}
        self.status = {}
        self.notes = []
        self.kpi_cards, self.kpi, self.fraction_partial, self.problem_rows = [], [], [], 0

    def section(self, key, status, html, data=None, note=None):
        self.html[key] = html
        self.data[key] = data if data is not None else {}
        self.status[key] = status
        if note and status in ('partial', 'unavailable'):
            self.notes.append(f'{key}: {note}')


def _no_input(title, hint):
    return f'<h2>{escape(title)}</h2>' + _p(f'입력 없음: {hint}', 'box')


CONTEXT_FIELDS = ('process_step_id', 'source_id', 'exposure_id', 'density_process_layer', 'source_contract_version')


def _context_key(text, layer):
    """CSV의 comparison_key / context JSON → 비교 문맥 키 (layer, CONTEXT_FIELDS 값). 한쪽 제품 행의 context에서도 같은 키가 나온다."""
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return (layer, tuple(str(data.get(name, '')) for name in CONTEXT_FIELDS))


def _context_labels(keys):
    """문맥 키 → 읽을 수 있는 이름. layer에 문맥이 하나면 layer, 여럿이면 layer · process_step/exposure.
    그 둘이 같은 문맥이 있으면 서로 다른 나머지 값(source_id 등)도 덧붙인다."""
    by_layer = {}
    for key in set(keys):
        by_layer.setdefault(key[0], []).append(key)
    labels = {}
    for layer, group in by_layer.items():
        if len(group) == 1:
            labels[group[0]] = layer
            continue
        varying = {i for i in range(len(CONTEXT_FIELDS)) if len({k[1][i] for k in group}) > 1}
        shown = sorted({0, 2} | varying)   # process_step_id, exposure_id는 항상
        for key in group:
            labels[key] = f'{layer} · ' + '/'.join(key[1][i] or '?' for i in shown)
    return labels


def _fraction_groups(rows):
    """delta 행에서 (layer, 비교 문맥) 그룹별 지름 → 지표 → {a, b, delta, status}."""
    groups = {}
    for row in rows:
        if row.get('side') != 'delta':
            continue
        key = _context_key(row.get('comparison_key', ''), row.get('layer', ''))
        groups.setdefault(key, {}).setdefault(_num(row.get('diameter_um')), {})[row.get('metric')] = {
            'a': _num(row.get('reference_value')), 'b': _num(row.get('target_value')), 'delta': _num(row.get('value')),
            'status': row.get('status'), 'reasons': _json_list(row.get('reasons'))}
    return groups


def _panel_values(rows, extra=lambda row: True):
    """(side, 문맥 키, 항목) → 행. 같은 키가 두 번 나오면 조용히 덮어쓰지 않고 exit 2."""
    out = {}
    for row in rows:
        if row.get('domain') != 'panel' or not extra(row):
            continue
        name = row.get('piece') if row.get('axis') == 'failure_type' else row.get('class')
        key = (row.get('side'), _context_key(row.get('context', ''), row.get('layer', '')), name)
        if key in out:
            raise ReportInputError('duplicate_context', f'{key[0]} {key[1][0]} {name}: 같은 비교 문맥의 행이 둘 이상입니다')
        out[key] = row
    return out


def _gate_badge(comparison, gate):
    decision = (comparison.get('applied_gate_decisions') or {}).get(gate) or {}
    answered = decision.get('status') == 'answered'
    return ('' if answered else f'<span class="badge warn">{gate} 미확정</span>')


def _kpi_card(title, value_html, a, b, what, source, extra=''):
    return _Html('<div class="card">' + f'<div><b>{escape(title)}</b> {extra}</div><div class="big">{value_html}</div>'
                 f'<div class="src">a {escape(_fmt(a))} · b {escape(_fmt(b))}</div>'
                 f'<div class="what">{escape(what)}</div><div class="src">출처: {escape(source)}</div></div>')


READING_GUIDE = (
    ('Δ', '항상 b − a(target − reference)입니다. + 이면 b가 a보다 큰 값(빨강, 악화), − 이면 작은 값(파랑, 개선)입니다. 같은 값이면 색이 없습니다.'),
    ('union frame 비율', '지정 지름의 이물 중심이 놓였을 때 short가 되는 위치가 layer frame 전체 면적에서 차지하는 비율입니다. 단위가 없고 유닛 크기와 무관합니다.'),
    ('line / pixel', 'panel 기준 가중 short 지수의 b − a입니다. 밀도 계약(G1)과 한 unit 환산(G2)이 미확정이면 참고용입니다.'),
    ('창 ΔCA', '대응 위치 중심의 같은 물리 창 안 면적(µm²)입니다. 창 크기는 임시 기준값이며 제품마다 다를 수 있습니다.'),
    ('색', '그래프의 a는 회색, b는 보라입니다. 빨강·파랑은 Δ의 방향(악화·개선)에만 씁니다.'),
    ('겹친 창', '같은 layer에서 창이 겹치는 쌍은 같은 이물을 중복해서 셉니다. ' + NO_SUM_NOTE + '.'))


def _section_a1_b1_b2(report, loaded, diameter_um):
    comparison, fractions = loaded['comparison'], loaded['fractions']
    groups = _fraction_groups(fractions)
    summary = _panel_values(loaded['summary'])
    pieces = _panel_values(loaded['pieces'], lambda r: r.get('axis') == 'failure_type')
    labels = _context_labels([*groups, *(k[1] for k in summary), *(k[1] for k in pieces)])
    cards, kpi, partial = [], [], []
    for key, by_d in sorted(groups.items()):
        label = labels[key]
        entry = next((m for d, m in by_d.items() if d is not None and _same_float(d, diameter_um)), {}).get('union')
        if entry is None or entry['delta'] is None:
            partial.append(f'{label}: 지름 {diameter_um:g} µm의 union Δ 없음')
            cards.append(_kpi_card(f'{label} · union frame 비율 Δ', _Html('—'), None, None,
                                   '이 지름의 값이 없거나 계산되지 않았습니다.', 'comparison_fractions.csv',
                                   _Html(f'<span class="badge warn">{escape(str(entry["status"] if entry else "없음"))}</span>')))
        else:
            cards.append(_kpi_card(f'{label} · union frame 비율 Δ @{diameter_um:g} µm', _delta_html(entry['delta']), entry['a'], entry['b'],
                                   f'지름 {diameter_um:g} µm 이물의 short 위치가 frame 면적에서 차지하는 비율의 b − a. +이면 b가 더 나쁨(빨강), −이면 b가 더 좋음(파랑).',
                                   'comparison_fractions.csv (side=delta, metric=union)'))
        kpi.append({'group': label, 'diameter_um': diameter_um, 'metric': 'union', **(entry or {})})

    def side_value(side, key, name):
        row = summary.get((side, key, name))
        return _num(row['value']) if row else None
    for (side, key, name), row in sorted(summary.items()):
        if side != 'delta' or name not in ('line', 'pixel'):
            continue
        label = labels[key]
        value = _num(row['value'])
        gate = _gate_badge(comparison, 'G1')
        cards.append(_kpi_card(f'{label} · {name} Δ (panel)', _delta_html(value), side_value('reference', key, name),
                               side_value('target', key, name),
                               f'panel 기준 {name} 가중 short 지수의 b − a. +이면 b가 더 나쁨(빨강). 밀도 계약이 미확정이면 참고용.',
                               'comparison_summary.csv (side=delta, domain=panel)', _Html(gate)))
        kpi.append({'group': label, 'metric': name, 'domain': 'panel', 'delta': value, 'status': row.get('status'),
                    'a': side_value('reference', key, name), 'b': side_value('target', key, name)})
    report.kpi_cards = cards
    report.kpi = kpi
    report.fraction_partial = partial

    # B1: 지표별 지름 꺾은선과 지정 지름 표
    b1 = '<h2 id="b1">B1 frame 면적 비율</h2>' + _p(comparison.get('fraction_of_frame_note', ''))
    b1_data = []
    for key, by_d in sorted(groups.items()):
        label = labels[key]
        diameters = sorted(d for d in by_d if d is not None)
        charts = ''.join(_cell(_line_chart(
            f'{label} · {metric}', diameters,
            [('a', SERIES_A, [by_d[d].get(metric, {}).get('a') for d in diameters]),
             ('b', SERIES_B, [by_d[d].get(metric, {}).get('b') for d in diameters])])) for metric in FRACTION_METRICS)
        at_d = next((m for d, m in by_d.items() if d is not None and _same_float(d, diameter_um)), {})
        rows = [[m, _fmt(at_d.get(m, {}).get('a')), _fmt(at_d.get(m, {}).get('b')), _delta_html(at_d.get(m, {}).get('delta')),
                 at_d.get(m, {}).get('status', '—')] for m in FRACTION_METRICS]
        b1 += (f'<h3>{escape(label)}</h3><div class="charts">{charts}</div>' +
               _p(f'지름 {diameter_um:g} µm') + _cell(_table(['지표', 'a', 'b', 'Δ (b − a)', '상태'], rows, left=(0, 4))))
        b1_data.append({'group': label, 'at_diameter': {m: at_d.get(m) for m in FRACTION_METRICS},
                        'series': {m: [{'diameter_um': d, 'a': by_d[d].get(m, {}).get('a'), 'b': by_d[d].get(m, {}).get('b'),
                                        'delta': by_d[d].get(m, {}).get('delta')} for d in diameters] for m in FRACTION_METRICS}})
    status = 'partial' if partial or comparison.get('status') not in (None, 'available') else 'available'
    report.section('B1', status, b1 if groups else _no_input('B1 frame 면적 비율', 'delta 행이 없습니다'), b1_data,
                   '; '.join(partial) or f'comparison.json status={comparison.get("status")}')

    # B2: line/pixel/transfer 와 불량 유형 조각 (서로 다른 축, 더하지 않는다). 비교 문맥마다 따로 그린다.
    contexts = sorted({k[1] for k in (*summary, *pieces)})
    b2 = ('<h2 id="b2">B2 line / pixel / transfer와 불량 유형 조각</h2>' +
          _p(comparison.get('failure_piece_axis_note', '불량 유형 조각은 line / pixel과 다른 축입니다. 두 축의 값을 서로 더하거나 빼지 않습니다.')) +
          _p('panel 환산값입니다. 밀도 계약(G1)과 unit 면적(G2)이 미확정이면 참고용입니다.'))
    b2_data = []

    def piece_value(side, key, name):
        row = pieces.get((side, key, name))
        return _num(row['value']) if row else None
    for key in contexts:
        label = labels[key]
        classes = ('line', 'pixel', 'transfer')
        class_series = [(n, c, [side_value(s, key, k) for k in classes]) for n, s, c in (('a', 'reference', SERIES_A), ('b', 'target', SERIES_B))]
        piece_series = [(n, c, [piece_value(s, key, k) for k in PIECES]) for n, s, c in (('a', 'reference', SERIES_A), ('b', 'target', SERIES_B))]
        b2 += (f'<h3>{escape(label)}</h3><div class="charts">{_cell(_bar_chart(f"{label} · line / pixel / transfer (panel)", classes, class_series))}'
               f'{_cell(_bar_chart(f"{label} · 불량 유형 조각 (panel)", PIECES, piece_series))}</div>')
        b2_data.append({'group': label, 'layer': key[0], 'context': dict(zip(CONTEXT_FIELDS, key[1])),
                        'classes': {n: dict(zip(classes, ys)) for n, _, ys in class_series},
                        'pieces': {n: dict(zip(PIECES, ys)) for n, _, ys in piece_series}})
    report.section('B2', 'available' if contexts else 'partial', b2, b2_data, 'panel 행이 없습니다')


def _pair_rows(loaded, rank_window_um, diameter_um):
    """(pair_id) → {(kind, metric): 행} — 순위 창·지정 지름의 local_delta_rows.csv 행."""
    out = {}
    for row in loaded['window_rows']:
        if _num(row.get('window_size_um')) is None or not _same_float(_num(row['window_size_um']), rank_window_um):
            continue
        if _num(row.get('diameter_um')) is None or not _same_float(_num(row['diameter_um']), diameter_um):
            continue
        out.setdefault(row['pair_id'], {})[(row['kind'], row['metric'])] = row
    return out


def _classify(loaded, rank_window_um, diameter_um, top_n):
    delta = loaded['windows']
    pairs = {p['pair_id']: p for p in delta.get('pairs', [])}
    ready = [pid for pid, p in pairs.items() if p.get('status') == 'ready']
    blocked = [{'pair_id': pid, 'reasons': p.get('reasons', [])} for pid, p in pairs.items() if p.get('status') != 'ready']
    rows = _pair_rows(loaded, rank_window_um, diameter_um)
    entries = {}
    for pid in ready:
        union = rows.get(pid, {}).get(('window', 'union'))
        value = _num(union.get('delta_um2')) if union else None
        status = (union.get('delta_status') if union else None) or 'missing'
        entries[pid] = {'pair_id': pid, 'a': _num(union.get('a_value_um2')) if union else None,
                        'b': _num(union.get('b_value_um2')) if union else None, 'delta': value,
                        'fraction_delta': _num(union.get('delta_fraction')) if union else None, 'status': status,
                        'reasons': _json_list(union.get('delta_reasons')) if union else ['row_missing']}
    usable = [e for e in entries.values() if e['status'] == 'available' and e['delta'] is not None]
    worse = sorted((e for e in usable if e['delta'] > 0), key=lambda e: (-e['delta'], e['pair_id']))
    better = sorted((e for e in usable if e['delta'] < 0), key=lambda e: (e['delta'], e['pair_id']))
    zero = sorted((e for e in usable if e['delta'] == 0), key=lambda e: e['pair_id'])
    usable_ids = {e['pair_id'] for e in usable}
    unresolved = sorted((e for e in entries.values() if e['pair_id'] not in usable_ids), key=lambda e: e['pair_id'])
    shown = {e['pair_id'] for e in worse[:top_n] + better[:top_n]}
    # B3 카드 순서: 악화 상위, 개선 상위, 그 밖의 ready 쌍
    order = [e['pair_id'] for e in worse[:top_n] + better[:top_n]]
    order += [e['pair_id'] for e in worse[top_n:] + better[top_n:] + zero + unresolved if e['pair_id'] not in shown]
    return {'pairs': pairs, 'entries': entries, 'worse': worse, 'better': better, 'zero': zero, 'unresolved': unresolved,
            'blocked': blocked, 'order': order, 'rows': rows, 'top_n': top_n}


def _window_bounds(loaded, cls, rank_window_um):
    """(pair_id, side) → ([x0,y0,x1,y1], 출처). thumbs → 행 JSON → 중심 ± 창/2 순서."""
    bounds = {}
    thumbs = loaded.get('thumbs')
    for record in (thumbs or {}).get('windows', []):
        if _same_float(record['window_size_um'], rank_window_um):
            bounds[(record['pair_id'], record['side'])] = (_bounds4(record['window_bounds_um'], 'local_delta_thumbs.json window_bounds_um'),
                                                           'local_delta_thumbs.json')
    half = rank_window_um / 2
    for pid in cls['entries']:
        for side in ('a', 'b'):
            if (pid, side) not in bounds:
                centre = cls['pairs'][pid][side]['center_xy_um']
                if not isinstance(centre, (list, tuple)) or len(centre) != 2:
                    raise ReportInputError('invalid_geometry_value', f'local_delta.json {pid}/{side} center_xy_um 형식이 맞지 않습니다')
                cx, cy = _finite(centre[0], f'{pid}/{side} center_xy_um'), _finite(centre[1], f'{pid}/{side} center_xy_um')
                bounds[(pid, side)] = ([cx - half, cy - half, cx + half, cy + half], '중심 ± 창/2 (clipping 미반영)')
    return bounds


def _bounds4(value, what):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ReportInputError('invalid_geometry_value', f'{what}: 숫자 4개 목록이 아닙니다')
    return [_finite(v, what) for v in value]


def _overlaps(cls, bounds):
    """같은 side·product·frame·layer에서 창이 겹치는 쌍과 겹침 면적(µm²). 값은 바꾸지 않고 표시만 한다."""
    groups = {}
    for pid in cls['entries']:
        for side in ('a', 'b'):
            info = cls['pairs'][pid][side]
            groups.setdefault((side, info.get('product_id'), info.get('frame_id'), info.get('analysis_layer')), []).append(pid)
    found = []
    for (side, product, frame, layer), pids in sorted(groups.items(), key=lambda kv: str(kv[0])):
        pids = sorted(pids)
        for i, first in enumerate(pids):
            for second in pids[i + 1:]:
                area = _overlap_area(bounds[(first, side)][0], bounds[(second, side)][0])
                if area > 0:
                    found.append({'side': side, 'product_id': product, 'frame_id': frame, 'layer': layer,
                                  'pair_a': first, 'pair_b': second, 'overlap_um2': area})
    return found


def _centre(pair, side):
    x, y = pair[side]['center_xy_um']
    return f'({_fmt(_finite(x, "center_xy_um"), 8)}, {_fmt(_finite(y, "center_xy_um"), 8)})'


def _build_a2(report, cls, overlaps, anchors, rank_window_um, diameter_um):
    headers = ['#', '쌍', 'layer', 'a 중심 (x, y) µm', 'b 중심 (x, y) µm', 'a µm²', 'b µm²', 'Δ µm² (b − a)', 'Δ fraction']

    def table(entries):
        rows = []
        for rank, e in enumerate(entries[:cls['top_n']], 1):
            pair = cls['pairs'][e['pair_id']]
            rows.append([rank, _Html(f'<a href="#{anchors[e["pair_id"]]}">{escape(e["pair_id"])}</a>'), pair['a'].get('analysis_layer'),
                         _centre(pair, 'a'), _centre(pair, 'b'), _fmt(e['a']), _fmt(e['b']), _delta_html(e['delta']), _fmt(e['fraction_delta'])])
        return _cell(_table(headers, rows, left=(1, 2, 3, 4))) if rows else _p('해당하는 쌍이 없습니다.')

    html = (f'<h2 id="a2">A2 악화 상위 {cls["top_n"]} / 개선 상위 {cls["top_n"]}</h2>' +
            _p(f'기준: 창 {rank_window_um:g} µm ({PROVISIONAL}), 지름 {diameter_um:g} µm, window union의 Δ = b − a. '
               f'악화 {len(cls["worse"])}쌍 중 {min(cls["top_n"], len(cls["worse"]))}쌍, 개선 {len(cls["better"])}쌍 중 '
               f'{min(cls["top_n"], len(cls["better"]))}쌍을 표시합니다.') +
            '<h3 style="color:' + WORSE + '">악화 (Δ &gt; 0)</h3>' + table(cls['worse']) +
            '<h3 style="color:' + BETTER + '">개선 (Δ &lt; 0)</h3>' + table(cls['better']) +
            _p(f'Δ = 0인 쌍은 목록에 넣지 않고 개수만 셉니다: {len(cls["zero"])}쌍.'))
    if cls['unresolved'] or cls['blocked']:
        rows = [[e['pair_id'], e['status'], ', '.join(map(str, e['reasons']))] for e in cls['unresolved']]
        rows += [[b['pair_id'], 'blocked', ', '.join(map(str, b['reasons']))] for b in cls['blocked']]
        html += '<h3>값이 없는 쌍 (partial / unavailable / blocked)</h3>' + _cell(_table(['쌍', '상태', '사유'], rows, left=(0, 1, 2)))
    if overlaps:
        html += ('<h3>창이 겹치는 쌍</h3>' + _p(NO_SUM_NOTE + '. 같은 이물이 두 쌍에 중복해서 들어갑니다.', 'box') +
                 _cell(_table(['제품', 'layer', '쌍', '쌍', '겹침 µm²'], [[o['side'] + ':' + str(o['product_id']), o['layer'], o['pair_a'], o['pair_b'],
                                                                      _fmt(o['overlap_um2'])] for o in overlaps], left=(0, 1, 2, 3))))
    else:
        html += _p('창이 겹치는 쌍이 없습니다.')
    bad = bool(cls['unresolved'] or cls['blocked'])
    data = {'rank_window_um': rank_window_um, 'window_status': PROVISIONAL, 'diameter_um': diameter_um, 'top_n': cls['top_n'],
            'worse_total': len(cls['worse']), 'better_total': len(cls['better']), 'zero_count': len(cls['zero']),
            'worse': cls['worse'][:cls['top_n']], 'better': cls['better'][:cls['top_n']], 'unresolved': cls['unresolved'],
            'blocked': cls['blocked'], 'overlaps': overlaps}
    report.section('A2', 'partial' if bad else 'available', html, data, 'partial/unavailable/blocked 쌍이 있습니다')


def _frame_geometry(loaded, side, frame_id):
    frames = (loaded['windows']['products'].get(side) or {}).get('frames', {})
    info = frames.get(frame_id)
    if info is None:
        return None
    what = f'local_delta.json frames {side}/{frame_id}'
    origin, shape = info['origin_xy_um'], info['shape_yx']
    if not isinstance(origin, (list, tuple)) or len(origin) != 2 or not isinstance(shape, (list, tuple)) or len(shape) != 2:
        raise ReportInputError('invalid_geometry_value', f'{what}: origin_xy_um / shape_yx 형식이 맞지 않습니다')
    pitch = _finite(info['pixel_pitch_um'], what + ' pixel_pitch_um')
    if pitch <= 0 or any(isinstance(n, bool) or not isinstance(n, int) or n < 1 for n in shape):
        raise ReportInputError('invalid_geometry_value', f'{what}: pixel_pitch_um > 0, shape_yx 양의 정수여야 합니다')
    return {'origin': (_finite(origin[0], what), _finite(origin[1], what)), 'pitch': pitch, 'shape': (shape[0], shape[1])}


def _build_maps(loaded, inputs, windows_dir):
    maps = _FrameMaps()
    thumb_sides = {(s['side'], s['frame_id']): s for s in (loaded.get('thumbs') or {}).get('sides', [])}
    for side in ('a', 'b'):
        for frame_id in (loaded['windows']['products'].get(side) or {}).get('frames', {}):
            geometry = _frame_geometry(loaded, side, frame_id)
            record = thumb_sides.get((side, frame_id))
            png, note, source = None, None, None
            if record is None:
                note = 'local_delta_thumbs.json 없음'
            elif record.get('overview_file') is None:
                note = record.get('note') or 'overview 없음'
            else:
                png = inputs.png(windows_dir, record['overview_file'], 'windows')
                source = record.get('overview_source_pair_id')
            maps.add(side, frame_id, geometry, png, note, source)
    return maps


def _build_b3(report, loaded, inputs, windows_dir, cls, bounds, overlaps, anchors, rank_window_um, diameter_um):
    maps = _build_maps(loaded, inputs, windows_dir)
    colour = {}
    for e in cls['worse']:
        colour[e['pair_id']] = WORSE
    for e in cls['better']:
        colour[e['pair_id']] = BETTER
    kind_label = {}
    for e in cls['worse']:
        kind_label[e['pair_id']] = ('악화', 'worse')
    for e in cls['better']:
        kind_label[e['pair_id']] = ('개선', 'better')
    by_pair_overlap = {}
    for o in overlaps:
        by_pair_overlap.setdefault(o['pair_a'], []).append((o['pair_b'], o['overlap_um2']))
        by_pair_overlap.setdefault(o['pair_b'], []).append((o['pair_a'], o['overlap_um2']))
    thumbs = loaded.get('thumbs')
    crops = {(c['pair_id'], c['side'], c['kind']): c for c in (thumbs or {}).get('crops', []) if _same_float(c['window_size_um'], rank_window_um)}
    html = (f'<h2 id="b3">B3 대응 위치 상세</h2>' +
            _p(f'창 {rank_window_um:g} µm ({PROVISIONAL}), 지름 {diameter_um:g} µm. 순서: 악화 상위, 개선 상위, 그 밖의 ready 쌍.'))
    if thumbs is None:
        html += _p('local_delta_thumbs.json이 없어 crop 이미지와 전체 frame 그림을 넣지 못했습니다(T1 이전 compare-windows 출력).', 'box')
    elif _num(thumbs.get('display_diameter_um')) is not None and not _same_float(thumbs['display_diameter_um'], diameter_um):
        html += _p(f'crop 이미지는 지름 {thumbs["display_diameter_um"]:g} µm 기준으로 만들어졌고 표의 수치는 {diameter_um:g} µm입니다.', 'box')
    # F2: 제품별 전체 frame 위치도
    html += '<h3>전체 frame 위치도</h3>' + _p('사각형은 순위 기준 창이며 빨강 = 악화, 파랑 = 개선, 회색 = 그 밖(Δ = 0 또는 값 없음). 두 제품의 좌표는 서로 겹쳐 그리지 않습니다.')
    html += '<div class="maps">'
    for (side, frame_id), info in maps.frames.items():
        rects = [(pid, bounds[(pid, side)][0], colour.get(pid, OTHER)) for pid in cls['order'] if cls['pairs'][pid][side].get('frame_id') == frame_id]
        caption = f'{side} · {loaded["products"][side]} · {frame_id}'
        figure = _cell(maps.svg(side, frame_id, rects)) + f'<figcaption class="note">{escape(caption)}'
        if info['note']:
            figure += escape(f' — 전체 layout 그림 없음({info["note"]}), frame 윤곽만 표시')
        elif info['png'] is not None:
            figure += (f'<br>배경 그림: 쌍 {escape(str(info["source"]) if info["source"] else "—")}의 두 전극만 그린 것입니다. '
                       '다른 쌍의 전극은 배경에 나오지 않을 수 있으며, 창 사각형의 위치는 정확합니다.')
        html += f'<figure>{figure}</figcaption></figure>'
    html += '</div>'
    cards, card_data, missing_crops = '', [], []
    for pid in cls['order']:
        entry, pair = cls['entries'][pid], cls['pairs'][pid]
        label, css = kind_label.get(pid, ('그 밖', ''))
        if pid not in kind_label:
            label = 'Δ = 0' if entry in cls['zero'] else '값 없음' if entry in cls['unresolved'] else ('악화(상위 밖)' if entry['delta'] and entry['delta'] > 0 else '개선(상위 밖)')
        badges = f'<span class="badge {css}">{escape(label)}</span>'
        if pid in by_pair_overlap:
            badges += '<span class="badge warn">창 겹침: ' + escape(', '.join(f'{other}(겹침 {_fmt(area)} µm²)' for other, area in sorted(by_pair_overlap[pid]))) + '</span>'
        panes, pair_missing = '', []
        for side in ('a', 'b'):
            for kind in ('layout', 'hotspot'):
                crop = crops.get((pid, side, kind))
                if crop and crop.get('file'):
                    # 파일 이름이 있는데 파일이 없으면 입력이 서로 맞지 않는 것이므로 inputs.png가 exit 2로 알린다.
                    img = f'<img alt="{side} {kind}" src="{_png_uri(inputs.png(windows_dir, crop["file"], "windows"))}">'
                else:
                    reason = str((crop or {}).get('note') or ('crop 기록 없음' if thumbs is not None else 'crop 없음'))
                    img = f'<div class="missing">{escape(reason)}</div>'
                    if thumbs is not None:
                        pair_missing.append(f'{pid}/{side}/{kind}: {reason}')
                panes += f'<div class="pane"><span>{side} {kind}</span>{img}</div>'
        missing_crops += pair_missing
        locators = ''.join(f'<div class="pane"><span>{side} 위치</span>' +
                           _cell(maps.svg(side, pair[side].get('frame_id'), [(pid, bounds[(pid, side)][0], colour.get(pid, OTHER))],
                                          labels=False, max_width=170)) + '</div>' for side in ('a', 'b'))
        rows = []
        for kind in ('window', 'attributable_core'):
            for metric in UNION_METRICS:
                row = cls['rows'].get(pid, {}).get((kind, metric))
                if row is None:
                    continue
                rows.append([kind, metric, _fmt(_num(row['a_value_um2'])), _fmt(_num(row['b_value_um2'])), _delta_html(_num(row['delta_um2'])),
                             _fmt(_num(row['delta_fraction'])), row.get('delta_status', '')])
        table = _cell(_table(['구분', '지표', 'a µm²', 'b µm²', 'Δ µm²', 'Δ fraction', '상태'], rows, left=(0, 1, 6))) if rows else _p('이 창·지름의 행이 없습니다.')
        cards += (f'<div class="pair" id="{anchors[pid]}"><div><h3 style="margin-top:0">{escape(pid)}</h3>{badges}'
                  f'<div class="note">layer {escape(str(pair["a"].get("analysis_layer")))} · {escape(str(pair["a"].get("failure_type")))}<br>'
                  f'a 중심 {escape(_centre(pair, "a"))} · b 중심 {escape(_centre(pair, "b"))}</div>{table}</div>'
                  f'<div class="crops">{panes}</div><div>{locators}</div></div>')
        card_data.append({'pair_id': pid, 'class': label, 'entry': entry, 'overlaps': by_pair_overlap.get(pid, []),
                          'missing_crops': pair_missing,
                          'window_bounds_um': {s: bounds[(pid, s)][0] for s in ('a', 'b')},
                          'bounds_source': {s: bounds[(pid, s)][1] for s in ('a', 'b')},
                          'rows': {f'{k}/{m}': {key: row[key] for key in ('a_value_um2', 'b_value_um2', 'delta_um2', 'delta_fraction', 'delta_status')}
                                   for (k, m), row in cls['rows'].get(pid, {}).items()}})
    reasons = []
    if thumbs is None or any(info['png'] is None for info in maps.frames.values()):
        reasons.append('전체 frame 그림이 없습니다')
    if missing_crops:
        reasons.append(f'crop 이미지 {len(missing_crops)}개 없음 (' + ', '.join(missing_crops[:6]) + (' …' if len(missing_crops) > 6 else '') + ')')
    report.section('B3', 'partial' if reasons else 'available', maps.defs() + html + (cards or _p('ready 쌍이 없습니다.')),
                   card_data, '; '.join(reasons))


def _build_b4(report, loaded, diameter_um):
    html = '<h2 id="b4">B4 창 크기 민감도</h2>' + _p('창 크기에 따른 Δ_union의 부호와 크기 변화를 보여줄 뿐 추천 창은 계산하지 않습니다.')
    data = {}
    rows = loaded.get('window_sensitivity_rows.csv')
    if rows:
        shown = [r for r in rows if _num(r.get('diameter_um')) is not None and _same_float(_num(r['diameter_um']), diameter_um)]
        headers = list(rows[0].keys())
        html += _p(f'지름 {diameter_um:g} µm 행만 표시합니다(전체는 window_sensitivity_rows.csv).') + _cell(_table(headers, [[r[h] for h in headers] for r in shown], left=(0,)))
        data['rows'] = shown
    agreement = loaded.get('window_sensitivity_rank_agreement.csv')
    if agreement:
        headers = list(agreement[0].keys())
        html += '<h3>창 크기 쌍 간 순위 일치</h3>' + _cell(_table(headers, [[r[h] for h in headers] for r in agreement]))
        data['rank_agreement'] = agreement
    elif agreement is not None or rows is not None:
        html += _p('순위 일치 행이 없습니다(창 크기가 하나이거나 정렬 지름 미지정).')
    if rows is None and agreement is None:
        html += _p('window_sensitivity_*.csv가 windows-dir에 없습니다.', 'box')
    report.section('B4', 'available' if rows is not None else 'partial', html, data, 'window_sensitivity_rows.csv 없음')


def _build_b5(report, loaded, inputs, map_dir):
    if loaded['map'] is None:
        report.section('B5', 'no_input', _no_input('B5 취약 위치 지도', '--map-dir를 주지 않았습니다.'))
        return
    mapped = loaded['map']
    provenance = mapped.get('provenance') or {}
    html = ('<h2 id="b5">B5 취약 위치 지도</h2>' +
            _p(f'단위: {mapped.get("unit")} (hotspot에서 센 서로 다른 전극 수). 대표 지름 {_fmt(mapped.get("representative_diameter_um"))} µm. '
               f'범위: {provenance.get("defect_scope")}, 의미: {provenance.get("count_semantics")}.'))
    images, data = '', {'status': mapped.get('status'), 'reasons': mapped.get('reasons'), 'histograms': [], 'images': []}
    for heat in mapped.get('heatmaps', []):
        png = heat.get('png')
        if heat.get('status') != 'available' or not png:
            images += f'<figure><div class="missing">{escape(str(heat.get("status")))}</div></figure>'
            continue
        key = heat.get('key')
        caption = ' · '.join(map(str, key.values())) if isinstance(key, dict) else ' · '.join(map(str, key or ()))
        images += (f'<figure><img alt="{escape(heat["kind"])}" style="max-width:420px;image-rendering:pixelated;border:1px solid #999" '
                   f'src="{_png_uri(inputs.png(map_dir, png, "map"))}"><figcaption class="note">{escape(heat["kind"])} · {escape(caption)}</figcaption></figure>')
        data['images'].append({'kind': heat['kind'], 'png': png})
    html += f'<div class="maps">{images}</div>'
    for label, items in (('layer', mapped.get('layers', [])), ('summary', mapped.get('summaries', []))):
        for item in items:
            histogram = item.get('histogram') or {}
            key = item.get('key')
            name = ' · '.join(map(str, key.values())) if isinstance(key, dict) else ' · '.join(map(str, key or ()))
            html += (f'<h3>{label}: {escape(name)} ({escape(str(item.get("status")))})</h3>' +
                     _cell(_table(['전극 수', '셀 수'], [[k, v] for k, v in sorted(histogram.items(), key=lambda kv: float(kv[0]))])))
            data['histograms'].append({'kind': label, 'key': key, 'status': item.get('status'), 'histogram': histogram})
    bad = mapped.get('status') != 'available'
    report.section('B5', 'partial' if bad else 'available', html, data, f'electrode_map status={mapped.get("status")}')


def _build_b6(report, loaded):
    if loaded['rois'] is None:
        report.section('B6', 'no_input', _no_input('B6 검토 ROI', '--rois-dir를 주지 않았습니다.'))
        return
    rois = loaded['rois']
    rows = [[r.get('roi_id'), r.get('source_kind'), r.get('product_id'), r.get('case_id'), r.get('analysis_layer'), r.get('failure_type'),
             _fmt(r.get('center_xy_um')), r.get('status'), ', '.join(map(str, r.get('reasons', [])))] for r in rois.get('rois', [])]
    html = '<h2 id="b6">B6 검토 ROI</h2>' + _p(f'상태 {rois.get("status")} · producer_commit {rois.get("producer_commit")} · ROI {len(rows)}개')
    html += (_cell(_table(['roi_id', '출처', '제품', 'case', 'layer', '불량 유형', '중심 µm', '상태', '사유'], rows, left=(0, 1, 2, 3, 4, 5, 7, 8)))
             if rows else _p('ROI가 없습니다.'))
    excluded = rois.get('excluded_records', [])
    if excluded:
        html += '<h3>제외 기록</h3>' + _cell(_table(['roi_id', '출처', '사유'], [[e.get('roi_id', '—'), e.get('source_kind'), ', '.join(map(str, e.get('reasons', [])))] for e in excluded], left=(0, 1, 2)))
    else:
        html += _p('제외 기록이 없습니다.')
    report.section('B6', 'available' if rois.get('status') == 'available' else 'partial', html,
                   {'status': rois.get('status'), 'producer_commit': rois.get('producer_commit'), 'rois': rows, 'excluded_records': excluded},
                   f'review_rois status={rois.get("status")}')


def _status_groups(rows, source, status_key, reasons_key):
    counts = {}
    for row in rows:
        status = row.get(status_key)
        if status in (None, '', 'available'):
            continue
        key = (source, status, ', '.join(map(str, _json_list(row.get(reasons_key)))))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _build_b7(report, loaded, cls, overlaps):
    counts = {}
    for source, rows, status_key, reasons_key in (
            ('comparison_fractions.csv', loaded['fractions'], 'status', 'reasons'),
            ('comparison_summary.csv', loaded['summary'], 'status', 'reasons'),
            ('comparison_failure_pieces.csv', loaded['pieces'], 'status', 'reasons'),
            ('local_delta_rows.csv', loaded.get('window_rows') or [], 'delta_status', 'delta_reasons')):
        counts.update(_status_groups(rows, source, status_key, reasons_key))
    for source, items in (('electrode_map.json', (loaded['map'] or {}).get('layers', []) + (loaded['map'] or {}).get('summaries', [])),
                          ('review_rois.json', (loaded['rois'] or {}).get('rois', []))):
        for item in items:
            if item.get('status') not in (None, 'available'):
                key = (source, item['status'], ', '.join(map(str, item.get('reasons', []))))
                counts[key] = counts.get(key, 0) + 1
    rows = [[s, st, r, n] for (s, st, r), n in sorted(counts.items())]
    problem = sum(n for (_, st, _), n in counts.items() if st != 'not_applicable')
    report.problem_rows = problem
    html = ('<h2 id="b7">B7 상태·누락</h2>' + _p('status가 available이 아닌 행을 파일·상태·사유별로 묶어 센 표입니다. not_applicable은 해당 없음(예: 세로 short 없음)이며 문제 행 수에서 제외합니다.') +
            (_cell(_table(['파일', '상태', '사유', '행 수'], rows, left=(0, 1, 2))) if rows else _p('available이 아닌 행이 없습니다.')))
    top = [('comparison.json', loaded['comparison'].get('status')), ('electrode_map.json', (loaded['map'] or {}).get('status')),
           ('local_delta.json', (loaded['windows'] or {}).get('status')), ('review_rois.json', (loaded['rois'] or {}).get('status'))]
    html += '<h3>입력별 전체 상태</h3>' + _cell(_table(['파일', '상태'], [[n, s if s else '입력 없음'] for n, s in top], left=(0, 1)))
    if cls is not None:
        extra = [[e['pair_id'], e['status'], ', '.join(map(str, e['reasons']))] for e in cls['unresolved']] + \
                [[b['pair_id'], 'blocked', ', '.join(map(str, b['reasons']))] for b in cls['blocked']]
        if extra:
            html += '<h3>값이 없는 쌍</h3>' + _cell(_table(['쌍', '상태', '사유'], extra, left=(0, 1, 2)))
        html += '<h3>창 겹침</h3>' + (
            _p(NO_SUM_NOTE + '.', 'box') + _cell(_table(['제품', 'layer', '쌍', '쌍', '겹침 µm²'], [[o['side'] + ':' + str(o['product_id']), o['layer'], o['pair_a'], o['pair_b'],
                                                                                        _fmt(o['overlap_um2'])] for o in overlaps], left=(0, 1, 2, 3)))
            if overlaps else _p('창이 겹치는 쌍이 없습니다.'))
    report.section('B7', 'available', html, {'status_groups': rows, 'problem_rows': problem, 'top_level': dict(top)})


def _build_b8(report, loaded, inputs):
    rows = [[info['role'], path, info['sha256']] for path, info in sorted(inputs.files.items())]
    provenance = (loaded['windows'] or {}).get('provenance') or {}
    ids = [('config_sha256', provenance.get('config_sha256')), ('correspondence_sha256', provenance.get('correspondence_sha256')),
           ('reference_input_hash', provenance.get('reference_input_hash')), ('target_input_hash', provenance.get('target_input_hash')),
           ('source_manifest_hash (map)', ((loaded['map'] or {}).get('provenance') or {}).get('source_manifest_hash')),
           ('source_manifest_hash (rois)', (loaded['rois'] or {}).get('source_manifest_hash')),
           ('producer_commit (rois)', (loaded['rois'] or {}).get('producer_commit'))]
    html = ('<h2 id="b8">B8 출처</h2>' + _cell(_table(['폴더', '입력 파일', 'SHA256'], rows, left=(0, 1, 2))) +
            '<h3>실행 식별자</h3>' + _cell(_table(['항목', '값'], [[n, v if v else '—'] for n, v in ids], left=(0, 1))))
    report.section('B8', 'available', html, {'inputs': {p: i['sha256'] for p, i in inputs.files.items()}, 'identifiers': dict(ids)})


def _build_a0_a1_a3(report, loaded, cls, generated, rank_window_um, diameter_um, top_n):
    comparison, products = loaded['comparison'], loaded['products']
    synthetic = loaded['is_synthetic']
    commit = (loaded['rois'] or {}).get('producer_commit')
    badge = ('<span class="badge syn">합성 데이터 — 동작 검증용</span>' if synthetic else '<span class="badge">실자료</span>' if synthetic is False else '')
    a0 = (f'<h1>제품 비교 보고서</h1><p><b>a</b> {escape(products["a"])} (reference) → <b>b</b> {escape(products["b"])} (target) {badge}</p>'
          f'<p class="note">실행 일시 {escape(generated)} · producer_commit {escape(str(commit) if commit else "—")} · 방향 b − a (target − reference)</p>'
          f'<p>기준값: 지름 <b>{diameter_um:g} µm</b>, 순위 창 <b>{rank_window_um:g} µm</b> <span class="badge warn">{PROVISIONAL}</span>, 상위 N = <b>{top_n}</b>. '
          '수율 환산과 밀도 단위 환산은 포함하지 않습니다(보류).</p>')
    report.section('A0', 'available', a0, {'product_a': products['a'], 'product_b': products['b'], 'is_synthetic': synthetic,
                                           'generated_at': generated, 'producer_commit': commit, 'diameter_um': diameter_um,
                                           'rank_window_um': rank_window_um, 'rank_window_status': PROVISIONAL, 'top_n': top_n})
    cards = list(report.kpi_cards)
    if loaded['windows'] is None:
        cards.append(_kpi_card('대응 쌍', _Html('입력 없음'), None, None, '--windows-dir를 주지 않았습니다.', 'local_delta.json'))
    else:
        provenance = loaded['windows'].get('provenance') or {}
        counts = ' / '.join(escape(str(_count(provenance.get(name), name) if provenance.get(name) is not None else '—')) for name in ('n_ready', 'n_blocked'))
        cards.append(_kpi_card('대응 쌍 ready / blocked', _Html(counts), None, None,
                               '설계자가 짝지은 위치 중 계산 가능한 쌍과 막힌 쌍의 수.', 'local_delta.json provenance'))
    cards.append(_kpi_card('문제 행 수', _Html(str(report.problem_rows)), None, None,
                           'status가 available도 not_applicable도 아닌 입력 행의 수. 0이 아니면 B7 확인.', '모든 입력의 status 열'))
    guide = ''.join(f'<li><b>{escape(t)}</b>: {escape(d)}</li>' for t, d in READING_GUIDE)
    a1 = ('<h2 id="a1">A1 핵심 지표</h2><div class="cards">' + ''.join(_cell(c) for c in cards) + '</div>' +
          f'<details><summary>지표 읽는 법</summary><ul>{guide}</ul></details>')
    report.section('A1', 'partial' if report.fraction_partial else 'available', a1,
                   {'kpi': report.kpi, 'pairs': (loaded['windows'] or {}).get('provenance', {}).get('n_ready') if loaded['windows'] else None,
                    'problem_rows': report.problem_rows}, '; '.join(report.fraction_partial))
    gates = comparison.get('applied_gate_decisions') or {}
    gate_rows = [[g, d.get('status'), d.get('answer') if d.get('answer') is not None else '—', d.get('reason', '')] for g, d in sorted(gates.items())]
    a3 = ('<h2 id="a3">A3 판정 조건과 주의</h2>' + _cell(_table(['게이트', '상태', '답', '사유'], gate_rows, left=(0, 1, 2, 3))) +
          _p(comparison.get('failure_piece_axis_note', '')) + _p(comparison.get('fraction_of_frame_note', '')) +
          _p(f'순위 기준 창 {rank_window_um:g} µm는 {PROVISIONAL}이며 제품(모델)마다 다를 수 있습니다. 창 크기별 차이는 B4를 보세요.', 'box'))
    report.section('A3', 'available', a3, {'gates': gates, 'failure_piece_axis_note': comparison.get('failure_piece_axis_note'),
                                          'fraction_of_frame_note': comparison.get('fraction_of_frame_note')})


def _toc(report):
    names = {'A1': '핵심 지표', 'A2': '악화·개선 상위', 'A3': '판정 조건', 'B1': 'frame 면적 비율', 'B2': 'line/pixel/조각', 'B3': '대응 위치 상세',
             'B4': '창 민감도', 'B5': '취약 위치 지도', 'B6': '검토 ROI', 'B7': '상태·누락', 'B8': '출처'}
    label = {'available': '', 'partial': ' ⚠', 'no_input': ' (입력 없음)', 'unavailable': ' ⚠'}
    return '<p class="note">' + ' · '.join(f'<a href="#{k.lower()}">{k} {n}{label[report.status[k]]}</a>' for k, n in names.items()) + '</p>'


def build_report(compare_dir, *, map_dir=None, windows_dir=None, rois_dir=None, diameter_um=5.0, rank_window_um=20.0, top_n=10):
    """입력을 읽어 (html, report.json용 dict, 부분 여부 사유 목록)를 만든다. 쓰기는 하지 않는다."""
    _check_args(diameter_um, rank_window_um, top_n)
    inputs = _Inputs()
    loaded = _load(inputs, compare_dir, map_dir, windows_dir, rois_dir, diameter_um, rank_window_um)
    report = _Report({'diameter_um': diameter_um, 'rank_window_um': rank_window_um, 'top_n': top_n})
    generated = datetime.now(timezone.utc).isoformat(timespec='seconds')
    _section_a1_b1_b2(report, loaded, diameter_um)
    cls = overlaps = None
    if loaded['windows'] is not None:
        cls = _classify(loaded, rank_window_um, diameter_um, top_n)
        anchors = {pid: f'pair-{i}' for i, pid in enumerate(cls['entries'])}
        bounds = _window_bounds(loaded, cls, rank_window_um)
        overlaps = _overlaps(cls, bounds)
        _build_a2(report, cls, overlaps, anchors, rank_window_um, diameter_um)
        _build_b3(report, loaded, inputs, windows_dir, cls, bounds, overlaps, anchors, rank_window_um, diameter_um)
        _build_b4(report, loaded, diameter_um)
        windows_status = (loaded['windows'].get('status') or 'available')
        if windows_status != 'available':
            for key in ('A2', 'B3'):
                report.status[key] = 'partial'
            report.notes.append(f'windows: local_delta.json status={windows_status}')
    else:
        for key, title in (('A2', 'A2 악화 상위 / 개선 상위'), ('B3', 'B3 대응 위치 상세'), ('B4', 'B4 창 크기 민감도')):
            report.section(key, 'no_input', _no_input(title, '--windows-dir를 주지 않았습니다.'))
    _build_b5(report, loaded, inputs, map_dir)
    _build_b6(report, loaded)
    _build_b7(report, loaded, cls, overlaps or [])
    _build_a0_a1_a3(report, loaded, cls, generated, rank_window_um, diameter_um, top_n)
    _build_b8(report, loaded, inputs)
    order = ('A0', 'A1', 'A2', 'A3', 'B1', 'B2', 'B3', 'B4', 'B5', 'B6', 'B7', 'B8')
    head = report.html['A0'] + _toc(report)
    body = head + ''.join(report.html[k] for k in order[1:])
    html = (f'<!doctype html><html lang="ko"><meta charset="utf-8"><title>제품 비교 보고서</title><style>{CSS}</style><body>' + body + '</body></html>')
    reasons = sorted(report.notes)
    payload = {'schema_version': SCHEMA, 'generated_at': generated, 'settings': report.settings, 'is_synthetic': loaded['is_synthetic'],
               'products': loaded['products'], 'section_status': report.status, 'reasons': reasons,
               'inputs': {path: info for path, info in sorted(inputs.files.items())}, 'sections': report.data}
    return html, payload, reasons


def run_report(compare_dir, out, *, map_dir=None, windows_dir=None, rois_dir=None, diameter_um=5.0, rank_window_um=20.0, top_n=10):
    """report.html / report.json을 쓰고 execution 결과 dict를 돌려준다. 입력 오류는 쓰기 전에 ReportInputError."""
    out = Path(out)
    given = [Path(p).resolve() for p in (compare_dir, map_dir, windows_dir, rois_dir) if p is not None]
    if out.resolve() in given:
        raise ReportInputError('output_is_input', '--out은 입력 폴더와 달라야 합니다')
    html, payload, reasons = build_report(compare_dir, map_dir=map_dir, windows_dir=windows_dir, rois_dir=rois_dir,
                                          diameter_um=diameter_um, rank_window_um=rank_window_um, top_n=top_n)
    out.mkdir(parents=True, exist_ok=True)
    html_path, json_path = out / 'report.html', out / 'report.json'
    html_path.write_text(html, encoding='utf-8')
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    status = 'partial' if reasons else 'available'
    return {'status': status, 'reasons': reasons, 'outputs': [str(html_path), str(json_path)], 'exit_code': 3 if reasons else 0}

