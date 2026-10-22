"""Side-by-side HTML for local ΔCA (PLAN_v3 T5).

One static page: for every window size and pair, the two layout crops (endpoint
electrodes of the named case, when original shape assets are available), the two
hotspot crops at the display diameter coloured by failure-type piece, and the
a / b / Δ table. PNGs are embedded as base64 so the file opens from file:// in the
closed network without any external reference. No registration is performed; the
optional IoU samples cell centres inside each product's own window and is labelled
as such. DL scores are shown as provenance text only.
"""
from __future__ import annotations

import base64
import hashlib
from html import escape
import io
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .local_delta import METRICS, LocalDeltaResult, _named_case, find_group
from .numeric_schema import DIAMETERS_UM, diameter_key

PIECE_COLOURS = {'lateral_only': (0, 90, 200), 'vertical_only': (0, 150, 80), 'simultaneous': (200, 30, 30)}
ELECTRODE_COLOURS = ((40, 90, 220), (235, 140, 20))
OVERLAP_COLOUR = (150, 60, 200)
INVALID_COLOUR = (200, 200, 200)
WINDOW_OUTLINE = (220, 0, 0)
CORE_OUTLINE = (0, 140, 0)
DEFAULT_DISPLAY_DIAMETER_UM = 5.0  # same convention as the electrode-count map
OVERVIEW_MAX_PX = 800
THUMBS_SCHEMA = 'local_delta_thumbs_v1'


def _crop_cells(frame, bounds, margin_um):
    x0, y0, x1, y1 = bounds
    ox, oy = frame.origin_xy_um
    pitch = frame.pixel_pitch_um
    h, w = frame.shape_yx
    c0 = max(0, int(math.floor((x0 - margin_um - ox) / pitch)))
    c1 = min(w, int(math.ceil((x1 + margin_um - ox) / pitch)))
    r0 = max(0, int(math.floor((y0 - margin_um - oy) / pitch)))
    r1 = min(h, int(math.ceil((y1 + margin_um - oy) / pitch)))
    if c1 <= c0 or r1 <= r0:
        return None
    return r0, r1, c0, c1


def _render(frame, crop, layers, rectangles, *, target_px=200, max_scale=40):
    """layers: [(bool mask, rgb)] drawn in order; rectangles: [(bounds_um, rgb)] outlines."""
    r0, r1, c0, c1 = crop
    rows, cols = r1 - r0, c1 - c0
    scale = max(1, min(max_scale, math.ceil(target_px / max(rows, cols))))
    rgb = np.full((rows, cols, 3), 255, dtype=np.uint8)
    rgb[~frame.valid[r0:r1, c0:c1]] = INVALID_COLOUR
    painted = np.zeros((rows, cols), dtype=bool)
    for mask, colour in layers:
        sub = np.asarray(mask)[r0:r1, c0:c1]
        overlap = sub & painted
        rgb[sub] = colour
        rgb[overlap] = OVERLAP_COLOUR if colour in ELECTRODE_COLOURS else colour
        painted |= sub
    image = Image.fromarray(rgb).resize((cols * scale, rows * scale), Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(image)
    ox, oy = frame.origin_xy_um
    pitch = frame.pixel_pitch_um
    for bounds, colour in rectangles:
        if bounds is None:
            continue
        x0, y0, x1, y1 = bounds
        px = [((x0 - ox) / pitch - c0) * scale, ((y0 - oy) / pitch - r0) * scale,
              ((x1 - ox) / pitch - c0) * scale - 1, ((y1 - oy) / pitch - r0) * scale - 1]
        draw.rectangle(px, outline=colour, width=2)
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii'), scale


def _img(uri, title):
    if uri is None:
        return '<div class="missing">' + escape(title) + '</div>'
    return f'<img src="{uri}" alt="{escape(title)}" title="{escape(title)}">'


def _png_file(out, prefix, uri_or_bytes):
    """Writes PNG bytes (or a data URI's bytes) as <prefix>_<short sha>.png and returns the file name."""
    data = base64.b64decode(uri_or_bytes.split(',', 1)[1]) if isinstance(uri_or_bytes, str) else uri_or_bytes
    name = f'{prefix}_{hashlib.sha256(data).hexdigest()[:12]}.png'
    (out / name).write_bytes(data)
    return name


def _crop_bounds_um(frame, crop):
    r0, r1, c0, c1 = crop
    ox, oy = frame.origin_xy_um
    pitch = frame.pixel_pitch_um
    return [ox + c0 * pitch, oy + r0 * pitch, ox + c1 * pitch, oy + r1 * pitch]


def _block_any(array, step):
    h, w = array.shape
    ph, pw = -h % step, -w % step
    padded = np.pad(array, ((0, ph), (0, pw)))
    return padded.reshape((h + ph) // step, step, (w + pw) // step, step).any(axis=(1, 3))


def _overview_png(frame, layouts):
    """Whole-frame original layout (same colours as the crops), long side <= OVERVIEW_MAX_PX; returns (bytes, step).

    Convention for a block of step x step cells: electrode occupancy is counted only on valid cells (layout shapes outside
    the observed domain never colour a block); a block is gray only if none of its cells is valid; a mixed block is
    treated as valid, so it shows an electrode colour when any of its valid cells holds that electrode, else white.
    """
    h, w = frame.shape_yx
    step = max(1, math.ceil(max(h, w) / OVERVIEW_MAX_PX))
    cell_valid = np.asarray(frame.valid, dtype=bool)
    valid = _block_any(cell_valid, step)
    rgb = np.full(valid.shape + (3,), 255, dtype=np.uint8)
    painted = np.zeros(valid.shape, dtype=bool)
    for mask, colour in zip(layouts, ELECTRODE_COLOURS):
        sub = _block_any(np.asarray(mask, dtype=bool) & cell_valid, step)
        rgb[sub] = colour
        rgb[sub & painted] = OVERLAP_COLOUR
        painted |= sub
    rgb[~valid] = INVALID_COLOUR  # applied last: the invalid domain is always gray
    buffer = io.BytesIO()
    Image.fromarray(rgb).save(buffer, format='PNG')
    return buffer.getvalue(), step


def _fmt(value, digits=6):
    if value is None:
        return '—'
    if isinstance(value, float):
        return f'{value:.{digits}g}'
    return str(value)


class _Assets:
    """Original layout masks per case, resolved through the review asset manifest when configured."""

    def __init__(self, cfg, side_name, dataset):
        self.masks = {}
        self.reason = None
        if cfg is None or not isinstance(cfg.get(side_name), dict) or dataset is None:
            self.reason = 'source_asset_manifest_required'
            return
        try:
            from .numeric_images import read_binary
            from .pipeline import _numeric_path
            from .roi_export import _identity, check_asset_manifest
            from dataclasses import asdict
            root = _numeric_path(cfg, cfg[side_name]['data_dir'])
            assets, reasons = check_asset_manifest(dataset, root, cfg[side_name].get('asset_manifest'))
            if reasons:
                self.reason = ','.join(reasons)
                return
            for case in dataset.cases:
                entry = assets.get(_identity(asdict(case.identity)))
                if not entry or entry['assets'] is None:
                    continue
                layouts = []
                for item in entry['assets']['layouts']:
                    ref = item['asset']
                    layouts.append(read_binary(root, ref['path'], sha256=ref['sha256'], value_semantics=ref['value_semantics']))
                self.masks[case.identity] = layouts
        except Exception as exc:  # the page must still be written; thumbnails become 'unavailable'
            self.reason = getattr(exc, 'code', type(exc).__name__)

    def layouts(self, case):
        return self.masks.get(case.identity)


def _iou_cell_centres(mask_a, frame_a, bounds_a, mask_b, frame_b, bounds_b):
    def sample(mask, frame, bounds):
        x0, y0, x1, y1 = bounds
        ox, oy = frame.origin_xy_um
        pitch = frame.pixel_pitch_um
        h, w = frame.shape_yx
        cols = [c for c in range(w) if x0 <= ox + (c + 0.5) * pitch < x1]
        rows = [r for r in range(h) if y0 <= oy + (r + 0.5) * pitch < y1]
        return np.asarray(mask)[np.ix_(rows, cols)] if rows and cols else None
    a, b = sample(mask_a, frame_a, bounds_a), sample(mask_b, frame_b, bounds_b)
    if a is None or b is None or a.shape != b.shape:
        return {'value': None, 'status': 'not_applicable', 'reason': 'grid_mismatch', 'sampling': 'cell_centres', 'registration': 'none'}
    union = int(np.count_nonzero(a | b))
    if union == 0:
        return {'value': None, 'status': 'not_applicable', 'reason': 'both_empty', 'sampling': 'cell_centres', 'registration': 'none'}
    return {'value': int(np.count_nonzero(a & b)) / union, 'status': 'available', 'reason': None,
            'sampling': 'cell_centres', 'registration': 'none'}


def _side_thumbnails(side, dataset, assets, areas, display_row, window_key, label):
    """Layout crop (named case electrodes) and hotspot crop (pieces) for one side."""
    bounds = display_row['window']['bounds_um'][label]
    core = display_row['attributable_core'].get('bounds_um')
    core_bounds = None if core is None else core[label]
    margin = (bounds[2] - bounds[0]) / 2
    crop = _crop_cells(side.frame, bounds, margin)
    out = {'layout': None, 'hotspot': None, 'layout_note': None, 'layout_masks': None, 'crop_bounds_um': None, 'hotspot_note': None}
    if crop is None:
        out['layout_note'] = 'window_outside_frame'
        return out
    out['crop_bounds_um'] = _crop_bounds_um(side.frame, crop)
    rectangles = [(bounds, WINDOW_OUTLINE), (core_bounds, CORE_OUTLINE)]
    layouts = None
    if side.endpoints is None:
        out['layout_note'] = '전극 지정 없음 (endpoints 없음)'
    else:
        matches = _named_case(dataset.cases, side)
        if len(matches) != 1:
            out['layout_note'] = 'named_pair_case_not_found' if not matches else 'named_pair_case_ambiguous'
        else:
            layouts = assets.layouts(matches[0])
            if layouts is None:
                out['layout_note'] = 'source_shape_required' + ('' if assets.reason is None else f' ({assets.reason})')
    if layouts is not None:
        out['layout'], _ = _render(side.frame, crop, list(zip(layouts, ELECTRODE_COLOURS)), rectangles)
        out['layout_masks'] = layouts
    diameter = display_row['diameter_um']
    group, _ = find_group(areas, side, diameter, label)
    if group is not None:
        base = [] if layouts is None else [(m, (225, 225, 225)) for m in layouts]
        pieces = [(group.failure_piece_masks[name], PIECE_COLOURS[name]) for name in PIECE_COLOURS]
        out['hotspot'], _ = _render(side.frame, crop, base + pieces, rectangles)
    else:
        out['hotspot_note'] = 'hotspot_group_not_found'
    return out


def _row_html(pair, size, rows_by_d, display_d, thumbs, iou):
    display = rows_by_d[display_d]
    window, core = display['window'], display['attributable_core']
    source = pair.source
    source_text = source.get('kind', '')
    if source.get('kind') == 'dl2_search':
        source_text += f" score={_fmt(source.get('score'))} rank={source.get('rank')} (표시 문맥, 수치에 미사용)"
    elif source.get('kind') == 'manual':
        source_text += f" {source.get('labelled_by', '')} {source.get('labelled_at', '')}"
    metric_rows = ''
    for name in METRICS:
        a, b, d = window['a'][name], window['b'][name], window['delta'][name]
        c = core['delta'][name]
        metric_rows += ('<tr><td>' + escape(name) + '</td><td>' + escape(_fmt(a.value if a.status == 'available' else a.partial_value)) +
                        ' <small>' + escape(a.status) + '</small></td><td>' + escape(_fmt(b.value if b.status == 'available' else b.partial_value)) +
                        ' <small>' + escape(b.status) + '</small></td><td class="delta">' + escape(_fmt(d.value)) +
                        ' <small>' + escape(d.status) + '</small></td><td>' + escape(_fmt(d.fraction_delta)) +
                        '</td><td>' + escape(_fmt(c.value)) + ' <small>' + escape(c.status) + '</small></td></tr>')
    named = display.get('named_pair')
    named_text = '—'
    if named is not None and named.get('delta') is not None:
        named_text = (f"a={_fmt(named['a'].value if named['a'].status == 'available' else named['a'].partial_value)} "
                      f"b={_fmt(named['b'].value if named['b'].status == 'available' else named['b'].partial_value)} "
                      f"Δ={_fmt(named['delta'].value)} ({named['delta'].status})")
    jitter = display.get('jitter')
    jitter_text = '—' if jitter is None else f"Δ_union ∈ [{_fmt(jitter['min'])}, {_fmt(jitter['max'])}] sign_stable={jitter['sign_stable']} ({jitter['status']})"
    by_d = ' '.join(f"{_fmt(d, 3)}:{_fmt(rows_by_d[d]['window']['delta']['union'].value, 4)}" for d in sorted(rows_by_d))
    reasons = sorted({r for d in rows_by_d.values() for r in d['window']['delta']['union'].reasons})
    iou_text = '—' if iou is None else ' / '.join(
        (f"{_fmt(v['value'], 3)}" if v['status'] == 'available' else f"n/a ({v['reason']})") for v in iou) + ' (cell_centres, registration none)'
    return ('<tr class="pair"><td class="meta"><b>' + escape(pair.pair_id) + '</b><br>창 ' + escape(_fmt(size)) + ' µm · 표시 지름 ' + escape(_fmt(display_d)) +
            ' µm<br><small>' + escape(source_text) + '</small><br><small>a ' + escape(pair.a.product_id) + ' ' + escape(pair.a.analysis_layer) +
            ' @ ' + escape(json.dumps(list(pair.a.center_xy_um))) + '<br>b ' + escape(pair.b.product_id) + ' ' + escape(pair.b.analysis_layer) +
            ' @ ' + escape(json.dumps(list(pair.b.center_xy_um))) + '</small><br><small>layout IoU: ' + escape(iou_text) + '</small></td>'
            '<td class="thumbs"><div class="pane"><span>a layout</span>' + _img(thumbs['a']['layout'], thumbs['a']['layout_note'] or 'a layout') +
            '</div><div class="pane"><span>a hotspot</span>' + _img(thumbs['a']['hotspot'], 'a hotspot unavailable') +
            '</div><div class="pane"><span>b layout</span>' + _img(thumbs['b']['layout'], thumbs['b']['layout_note'] or 'b layout') +
            '</div><div class="pane"><span>b hotspot</span>' + _img(thumbs['b']['hotspot'], 'b hotspot unavailable') + '</div></td>'
            '<td class="table"><table class="metrics"><tr><th>metric</th><th>a µm²</th><th>b µm²</th><th>Δ µm²</th><th>Δ fraction</th><th>core Δ</th></tr>' +
            metric_rows + '</table><small>named pair: ' + escape(named_text) + '<br>jitter: ' + escape(jitter_text) +
            '<br>Δ_union by diameter: ' + escape(by_d) + '<br>reasons: ' + escape(', '.join(reasons) or '—') + '</small></td></tr>')


def write_local_delta_html(out, result: LocalDeltaResult, *, cfg=None, reference=None, target=None, name='local_delta'):
    """Writes <out>/<name>.html plus the crop / overview PNG files and <name>_thumbs.json; returns all paths."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    display_d = result.sort_diameter_um if result.sort_diameter_um is not None else DEFAULT_DISPLAY_DIAMETER_UM
    display_d = next((float(d) for d in DIAMETERS_UM if diameter_key(d) == diameter_key(display_d)), float(display_d))
    areas_a, areas_b = result.areas if result.areas is not None else (None, None)
    if cfg is not None and reference is not None and target is not None:
        from .adapters.real_hotspot import guard_stage_compare_windows
        guard_stage_compare_windows('compare-windows report', reference, target, layouts_cfg=cfg)  # layouts of both models
    assets = {'a': _Assets(cfg, 'reference', reference), 'b': _Assets(cfg, 'target', target)}
    ready = {pair.pair_id: pair for pair in result.pairs if pair.status == 'ready'}
    blocked = [pair for pair in result.pairs if pair.status != 'ready']
    by_pair_size = {}
    for row in result.rows:
        by_pair_size.setdefault((row['pair_id'], row['window_size_um']), {})[row['diameter_um']] = row
    sections = ''
    crop_files, window_records, overview_layouts = {}, [], {}
    for size in result.window_sizes_um:
        ranking = result.ranking.get('by_window_size', {}).get(str(size))
        order = [e['pair_id'] for e in ranking] if ranking else list(ready)
        rows_html = ''
        for pair_id in order:
            pair = ready[pair_id]
            rows_by_d = by_pair_size.get((pair_id, size), {})
            if display_d not in rows_by_d:
                continue
            thumbs = {}
            for label, dataset, areas in (('a', reference, areas_a), ('b', target, areas_b)):
                side = pair.a if label == 'a' else pair.b
                if dataset is None or areas is None:
                    thumbs[label] = {'layout': None, 'hotspot': None, 'layout_note': 'dataset_not_supplied', 'layout_masks': None}
                else:
                    try:
                        thumbs[label] = _side_thumbnails(side, dataset, assets[label], areas, rows_by_d[display_d], size, label)
                    except Exception as exc:  # the numbers are already written; the page must still render
                        thumbs[label] = {'layout': None, 'hotspot': None, 'layout_masks': None,
                                         'layout_note': 'thumbnail_failed:' + getattr(exc, 'code', type(exc).__name__)}
            iou = None
            if thumbs['a']['layout_masks'] is not None and thumbs['b']['layout_masks'] is not None:
                bounds = rows_by_d[display_d]['window']['bounds_um']
                iou = [_iou_cell_centres(ma, pair.a.frame, bounds['a'], mb, pair.b.frame, bounds['b'])
                       for ma, mb in zip(thumbs['a']['layout_masks'], thumbs['b']['layout_masks'])]
            rows_html += _row_html(pair, size, rows_by_d, display_d, thumbs, iou)
            for label in ('a', 'b'):
                side = pair.a if label == 'a' else pair.b
                thumb = thumbs[label]
                bounds = rows_by_d[display_d]['window']['bounds_um'][label]
                core = rows_by_d[display_d]['attributable_core'].get('bounds_um')
                window_records.append({'pair_id': pair_id, 'window_size_um': size, 'side': label, 'window_bounds_um': list(bounds),
                                       'core_bounds_um': None if core is None else list(core[label])})
                for kind in ('layout', 'hotspot'):
                    note = thumb['layout_note'] if kind == 'layout' else (thumb.get('hotspot_note') or thumb['layout_note'])
                    crop_files[(pair_id, size, label, kind)] = {
                        'pair_id': pair_id, 'window_size_um': size, 'diameter_um': display_d, 'side': label, 'kind': kind,
                        'file': None if thumb[kind] is None else _png_file(out, name + '_thumb', thumb[kind]),
                        'crop_bounds_um': thumb.get('crop_bounds_um'), 'note': None if thumb[kind] is not None else note}
                if thumb['layout_masks'] is not None:
                    # The overview background shows only this first pair's two electrodes; the report names the pair
                    # (overview_source_pair_id) and says so. No full-layout composite is attempted.
                    overview_layouts.setdefault((label, side.frame.frame_id), (pair, side, thumb['layout_masks']))
        order_note = ('|Δ_union| 내림차순 (정렬 지름 ' + _fmt(result.sort_diameter_um) + ' µm)') if ranking else '대응 파일 순서 (정렬 지름 미지정)'
        sections += ('<h2>창 ' + escape(_fmt(size)) + ' µm</h2><p>순서: ' + escape(order_note) + '</p>'
                     '<table class="pairs"><tr><th>쌍</th><th>a layout · a hotspot · b layout · b hotspot (빨간 사각형 = 창, 초록 = attributable core)</th><th>a / b / Δ (b − a) @ 표시 지름</th></tr>' +
                     (rows_html or '<tr><td colspan="3">표시할 ready 쌍이 없습니다</td></tr>') + '</table>')
    blocked_html = ''
    if blocked:
        blocked_html = '<h2>blocked 쌍</h2><ul>' + ''.join('<li>' + escape(p.pair_id) + ': ' + escape(', '.join(p.reasons)) + '</li>' for p in blocked) + '</ul>'
    legend = ('<p class="legend">hotspot 색: <span style="color:rgb(0,90,200)">■ lateral_only</span> '
              '<span style="color:rgb(0,150,80)">■ vertical_only</span> <span style="color:rgb(200,30,30)">■ simultaneous</span>; '
              'layout 색: <span style="color:rgb(40,90,220)">■ 전극 1</span> <span style="color:rgb(235,140,20)">■ 전극 2</span> '
              '<span style="color:rgb(150,60,200)">■ 겹침</span>; 회색 = valid 밖. 두 제품의 좌표는 서로 겹쳐 그리지 않으며 각자 자기 frame의 창입니다.</p>')
    provenance = result.provenance
    header = ('<p>상태: ' + escape(result.status) + (' · 합성 동작 검증.' if provenance.get('is_synthetic') else ' · 입력 자료 결과.') +
              ' 방향 b − a. 창 크기 ' + escape(json.dumps(list(result.window_sizes_um))) + ' µm' +
              ('' if result.jitter_um is None else ', jitter ±' + escape(_fmt(result.jitter_um)) + ' µm') +
              '. a=' + escape(str(result.products['a']['product_id'])) + ', b=' + escape(str(result.products['b']['product_id'])) +
              '. 대응 파일 SHA256 ' + escape(str(provenance.get('correspondence_sha256', '—'))) + '.</p>'
              '<p>창 값은 창 안 이물 중심 위치의 정확한 국부 위험이고, core 값은 창 안 형상 탓으로 읽을 수 있는 부분입니다. '
              'fraction은 창 면적으로 나눈 단위 없는 값입니다. DL 점수는 출처 표시일 뿐 어떤 수치에도 들어가지 않습니다. '
              '숫자 전체는 local_delta.json / local_delta_rows.csv, 창 크기 민감도는 window_sensitivity.*에 있습니다.</p>')
    html = ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>' + escape(name) + '</title><style>'
            'body{font-family:system-ui;max-width:1500px;margin:1.5rem auto;padding:0 1rem}'
            'table.pairs{border-collapse:collapse;width:100%}table.pairs td,table.pairs th{border:1px solid #ccc;padding:6px;vertical-align:top}'
            'td.thumbs{white-space:nowrap}.pane{display:inline-block;margin:0 6px;text-align:center}.pane span{display:block;font-size:12px;color:#555}'
            '.pane img{max-width:220px;max-height:220px;border:1px solid #999;image-rendering:pixelated}'
            '.missing{width:160px;height:80px;border:1px dashed #999;color:#777;font-size:12px;display:flex;align-items:center;justify-content:center;white-space:normal}'
            'table.metrics{border-collapse:collapse;font-size:13px}table.metrics td,table.metrics th{border:1px solid #ddd;padding:2px 6px;text-align:right}'
            'td.delta{font-weight:bold}td.meta{min-width:220px}small{color:#555}</style>'
            '<h1>국부 ΔCA: 대응 위치의 같은 물리 창</h1>' + header + legend + sections + blocked_html + '</html>')
    path = out / (name + '.html')
    path.write_text(html, encoding='utf-8')
    sides = []
    for label, dataset in (('a', reference), ('b', target)):
        seen = set()
        for pair in ready.values():
            side = pair.a if label == 'a' else pair.b
            if side.frame.frame_id in seen:
                continue
            seen.add(side.frame.frame_id)
            frame = side.frame
            record = {'side': label, 'product_id': side.product_id, 'frame_id': frame.frame_id, 'origin_xy_um': list(frame.origin_xy_um),
                      'pixel_pitch_um': frame.pixel_pitch_um, 'shape_yx': list(frame.shape_yx),
                      'overview_file': None, 'overview_scale': None, 'overview_source_pair_id': None, 'note': None}
            found = overview_layouts.get((label, frame.frame_id))
            if found is None:
                record['note'] = assets[label].reason or 'layout_unavailable'
            else:
                try:
                    data, step = _overview_png(frame, found[2])
                    record.update(overview_file=_png_file(out, f'{name}_overview_{label}', data), overview_scale=step,
                                  overview_source_pair_id=found[0].pair_id)
                except Exception as exc:  # the page and numbers are already written
                    record['note'] = 'overview_failed:' + getattr(exc, 'code', type(exc).__name__)
            sides.append(record)
    thumbs_path = out / (name + '_thumbs.json')
    thumbs_path.write_text(json.dumps({'schema_version': THUMBS_SCHEMA, 'display_diameter_um': display_d,
                                       'crops': list(crop_files.values()), 'sides': sides, 'windows': window_records},
                                      indent=2, ensure_ascii=False), encoding='utf-8')
    files = sorted({str(out / r['file']) for r in crop_files.values() if r['file']} |
                   {str(out / r['overview_file']) for r in sides if r['overview_file']})
    return [str(path), str(thumbs_path)] + files
