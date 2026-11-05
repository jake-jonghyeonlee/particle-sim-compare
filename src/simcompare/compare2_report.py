"""compare2-report: one self-contained HTML report for people outside the team, built from compare2.json.

No number is recomputed here: every vi/caa/share/removal value is read from compare2.json. This module only
(1) renders them, and (2) reads the 3 um hotspot masks of the top conditions to draw two maps per condition
(reference A and target B). Maps are not aligned: A is cut at the same relative position inside its unit.
Exit code: 0 complete, 3 compare2.json partial or a map/overlay warning, 2 error (error.json in a new --out).
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import sys
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from .compare2 import SCHEMA_VERSION as COMPARE2_SCHEMA, _json_text, _publish
from .compare2_inputs import VI_LINE_DIVISOR, VI_PIXEL_DIVISOR, load_compare2_config
from .numeric_schema import NumericContractError

REPORT_COMMAND = 'compare2-report'
REPORT_SCHEMA = 'compare2_report_v1'
MAX_MAPS = 200
RATIO_EPS = 1e-15
FIXED_BYTES = 64 * 1024 ** 2
LABEL_BYTES_PER_CELL = 10  # run-length labelling: 16 B per run (runs <= cells / 2) plus scratch, rounded up
FRAME_BYTES_PER_CELL = 2 + 1 + LABEL_BYTES_PER_CELL  # two bool masks, one layer PNG, labelling arrays
WINDOW_BYTES_PER_CELL = 4 * 2 + 3  # two RGBA images and the three cropped bool windows per condition
DEFAULT_MAX_OPERATION_BYTES = 8 * 1024 ** 3
REMEDY = 'reduce report.map_window_um, report.top_n or the number of conditions, or raise max_operation_bytes'
ROLES = ('reference', 'target')
ROLE_TEXT = {'reference': '기준 A', 'target': '대상 B'}
BORDER_PX = 500
UNIT_MARGIN_PX = 2 * BORDER_PX # the electrode PNG is the hotspot frame + 500 px on each side and holds 2 x 2 units
OVERLAY_GRAY = 128
OVERLAY_ALPHA = 0.35
MASK_ALPHA = 235
COLOR_COMMON = (141, 140, 133)
COLOR_NEW = (208, 59, 59)
COLOR_GONE = (42, 120, 214)
COLOR_BAR = (122, 121, 114)
SCALE_BAR_UM = 5.0


# ---- memory guard ------------------------------------------------------------------------------------------------

def guard_stage_compare2_report(stage, frame_cells, maps, window_cells, limit):
    """Refuse before any mask is read when the frame-sized arrays plus the per-map windows exceed the limit."""
    estimated = FIXED_BYTES + frame_cells * FRAME_BYTES_PER_CELL + maps * window_cells * WINDOW_BYTES_PER_CELL
    if estimated > limit:
        raise NumericContractError(
            'oversized_real_operation',
            f'{stage}: estimated {estimated} bytes exceed limit {limit} ({frame_cells} frame cells x '
            f'{FRAME_BYTES_PER_CELL} + {maps} maps x {window_cells} window cells x {WINDOW_BYTES_PER_CELL}); {REMEDY}')


# ---- text helpers ------------------------------------------------------------------------------------------------

def _e(value):
    return html.escape(str(value), quote=True)


def _f6(value):
    return '—' if value is None else f'{value:.6f}'


def _s6(value):
    return '—' if value is None else f'{value:+.6f}'


def _s4(value):
    """Signed value in 1e-4 units; the sign carries the direction, not only the colour."""
    if value is None:
        return '—'
    scaled = value * 1e4
    return '0.00' if abs(scaled) < 0.005 else f'{scaled:+.2f}'


def _pct_change(b, a):
    if a is None or b is None or abs(a) < RATIO_EPS:
        return '—'
    return f'{(b - a) / a * 100:+.1f}%'


def _share(value):
    return '—' if value is None else f'{int(round(value * 100))}%'


def _sign_class(value):
    if value is None or value == 0:
        return ''
    return 'up' if value > 0 else 'down'


def _num(value, digits=2):
    return f'{value:.{digits}f}'


def _span(css, text):
    return f'<span class="{css}">{text}</span>' if css else text


def _size_text(value):
    return f'{value:g}'


def _changed(item):
    return any(item.get(f'delta_vi_{kind}') not in (None, 0, 0.0) for kind in ('line', 'pixel'))


def _cond_score(item):
    """|share_line| + |share_pixel|; |Δvi_line| + |Δvi_pixel| when both shares are null."""
    if item.get('share_line') is None and item.get('share_pixel') is None:
        return abs(item.get('delta_vi_line') or 0.0) + abs(item.get('delta_vi_pixel') or 0.0)
    return abs(item.get('share_line') or 0.0) + abs(item.get('share_pixel') or 0.0)


def _changed_ranking(conds):
    """Indexes of the conditions that change vi, most influential first."""
    return sorted((i for i, c in enumerate(conds) if _changed(c)),
                  key=lambda i: (-_cond_score(conds[i]), conds[i]['layer'], conds[i]['pair_id']))


def _no_map_slot(item):
    return '' if not _changed(item) else '지도 생략(상한)'


def _pair_label(item):
    electrodes = item.get('electrodes')
    if electrodes and len(electrodes) == 2:
        return f'{electrodes[0]}–{electrodes[1]}'
    return str(item.get('pair_id'))


def _types(cls):
    return [part for part in str(cls or '').split('->') if part in ('line', 'pixel')] or ['pixel']


def _presence_badge(presence):
    return {'new': '<span class="badge b-new">신규</span>', 'gone': '<span class="badge b-gone">소멸</span>',
            'both': '<span class="badge b-both">양쪽</span>'}.get(presence, '')


# ---- compare2.json -------------------------------------------------------------------------------------------------

def _read_compare2(compare2_dir, cfg2):
    path = Path(compare2_dir) / 'compare2.json'
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise NumericContractError('compare2_json_missing', f'{path}: {exc}') from exc
    try:
        doc = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise NumericContractError('compare2_json_invalid', f'{path}: {exc}') from exc
    if not isinstance(doc, dict) or doc.get('schema_version') != COMPARE2_SCHEMA:
        raise NumericContractError('unsupported_compare2_json', f'{path}: schema_version must be {COMPARE2_SCHEMA}')
    recorded = ((doc.get('inputs') or {}).get('compare2_config') or {}).get('sha256')
    if recorded != cfg2.config_sha256:
        raise NumericContractError(
            'compare2_config_mismatch',
            f'compare2.json was made with config sha256 {recorded}, this config is {cfg2.config_sha256}')
    return doc, hashlib.sha256(raw).hexdigest()


def _inside(child, parent):
    child, parent = Path(child).resolve(), Path(parent).resolve()
    return child == parent or parent in child.parents


# ---- side geometry (manifest only, no mask is read) -----------------------------------------------------------------

class _FrameInfo(object):
    """The four Frame fields load_layer_png needs, without the frame-sized valid array."""

    def __init__(self, spec):
        self.frame_id = spec['frame_id']
        self.pixel_pitch_um = spec['pixel_pitch_um']
        self.origin_xy_um = tuple(spec['origin_xy_um'])
        self.shape_yx = tuple(spec['shape_yx'])


def _side_info(cfg, cfg2, role):
    from .pipeline import _real_source
    section = cfg.get(role)
    real = _real_source(cfg, section)
    if real is None:
        raise NumericContractError('compare2_real_source_required', f'{role}: real_hotspot_v1 data_dir needed')
    frame = _FrameInfo(real[1]['frame'])
    product = getattr(cfg2, role)
    pitch = float(frame.pixel_pitch_um)
    ny, nx = frame.shape_yx
    if product.unit_size_um is not None:
        unit_um, derived = tuple(product.unit_size_um), False
    else:
        unit_um, derived = ((nx + UNIT_MARGIN_PX) / 2 * pitch, (ny + UNIT_MARGIN_PX) / 2 * pitch), True
    assignment = (section.get('condition_assignment') if isinstance(section, dict) else None) or {}
    return {'role': role, 'section': section, 'real': real, 'frame': frame, 'pitch': pitch,
            'unit_um': unit_um, 'unit_derived': derived, 'unit_px': (unit_um[0] / pitch, unit_um[1] / pitch),
            'product_id': assignment.get('product_id'),
            'limit': assignment.get('max_operation_bytes', DEFAULT_MAX_OPERATION_BYTES)}


# ---- masks, components, windows -------------------------------------------------------------------------------------

def _components(mask):
    """(count, bbox of the largest 4-neighbour component) with bbox = (row0, col0, row1, col1), end exclusive."""
    from .electrode_map import _label_runs, _run_counts
    rows, starts, ends, root = _label_runs(mask, _run_counts(mask))
    if not root.size:
        return 0, None
    roots = np.flatnonzero(root == np.arange(root.size, dtype=root.dtype))
    sizes = np.bincount(root, weights=(ends - starts))[roots]
    best = roots[int(np.argmax(sizes))]
    members = root == best
    return int(roots.size), (int(rows[members].min()), int(starts[members].min()),
                             int(rows[members].max()) + 1, int(ends[members].max()))


def _origin(center, size):
    """Top-left (row, col) of a size x size window centred on center = (x, y) in cells."""
    return (int(math.floor(center[1] - size / 2.0 + 0.5)), int(math.floor(center[0] - size / 2.0 + 0.5)))


def _crop(array, y0, x0, size):
    """size x size window of a bool array; cells outside the frame stay empty."""
    out = np.zeros((size, size), dtype=bool)
    height, width = array.shape
    ys0, ys1, xs0, xs1 = max(y0, 0), min(y0 + size, height), max(x0, 0), min(x0 + size, width)
    if ys0 < ys1 and xs0 < xs1:
        out[ys0 - y0:ys1 - y0, xs0 - x0:xs1 - x0] = array[ys0:ys1, xs0:xs1]
    return out


def _find_diameter(records, diameter):
    for key in records:
        if abs(float(key) - diameter) < 1e-9:
            return key
    return None


def _new_state():
    return {'status': 'no_cases', 'cells': 0, 'components': 0, 'center': None, 'origin': None, 'crop': None,
            'overlay': None, 'overlay_status': None}


def _scan_side(cfg, info, top_buckets, keys, diameter, size, origin_for, states):
    """Open one side, OR the lineage masks of every condition key at diameter and crop the window; then release it."""
    from .pipeline import _configured_real
    configured = _configured_real(cfg, info['section'], info['real'],
                                  {'bucket_detail': 'summary', 'top_buckets': top_buckets})
    if configured is None:
        raise NumericContractError('compare2_condition_assignment_required', f'{info["role"]}: condition_assignment needed')
    dataset = configured[1]
    lineage = {}
    for case in dataset.cases:
        if case.pair_id is not None:
            lineage.setdefault((case.pair_id, case.analysis_layer), []).append(case)
    for key in keys:
        state = states[key] = _new_state()
        cases = lineage.get(key, [])
        if cases:
            state['status'] = 'no_record'
        acc = None
        for case in cases:
            found = _find_diameter(case.records, diameter)
            if found is None:
                continue
            record = case.records[found]
            if record.input_status != 'observed' or record.mask is None:
                continue
            state['status'] = 'ok'
            if acc is None:
                acc = np.array(record.mask, dtype=bool)
            else:
                acc |= record.mask
            del record
        if acc is None or not acc.any():
            del acc
            continue
        state['cells'] = int(acc.sum())
        state['components'], bbox = _components(acc)
        state['center'] = ((bbox[1] + bbox[3]) / 2.0, (bbox[0] + bbox[2]) / 2.0)
        origin = origin_for(key, state)
        if origin is not None:
            state['origin'] = origin
            state['crop'] = _crop(acc, origin[0], origin[1], size)
        del acc
    del configured, dataset, lineage


def _load_overlays(cfg2, info, keys, states, size, warnings):
    """Crop the Layer PNG of each analysis layer at every window; a missing or mismatching layer only drops the overlay."""
    from .adapters.electrode_png import load_layer_png, scan_layer_pngs
    role = info['role']
    product = getattr(cfg2, role)
    root, model = cfg2.report.dl1_root, product.dl1_model
    layers = sorted({key[1] for key in keys})

    def skip(layer_names, code, detail):
        for layer in layer_names:
            for key in keys:
                if key[1] == layer:
                    states[key]['overlay_status'] = code
        warnings.append({'code': code, 'role': role, 'layer': None if len(layer_names) != 1 else layer_names[0],
                         'detail': detail})

    if root is None or model is None:
        skip(layers, 'overlay_dl1_not_configured', 'report.dl1_root and products.%s.dl1_model are needed' % role)
        return
    try:
        available = scan_layer_pngs(root, model)
    except (NumericContractError, OSError, ValueError) as exc:
        skip(layers, 'overlay_scan_failed', str(exc))
        return
    for layer in layers:
        if layer not in available:
            skip([layer], 'overlay_layer_missing', f'{layer}: no Layer PNG in {model}')
            continue
        try:
            png = load_layer_png(root, model, info['frame'], available[layer])
        except (NumericContractError, OSError, ValueError) as exc:
            skip([layer], 'overlay_layer_unusable', f'{layer}: {getattr(exc, "code", "")} {exc}'.strip())
            continue
        for key in keys:
            if key[1] == layer:
                state = states[key]
                y0, x0 = state['origin']
                state['overlay'] = _crop(png, y0, x0, size)
                state['overlay_status'] = 'ok'
        del png


def _proportional(center, source, target):
    """Same relative position inside the unit; the frame sits BORDER_PX inside the 2 x 2 unit crop on each side."""
    return tuple((c + BORDER_PX) / su * tu - BORDER_PX
                 for c, su, tu in zip(center, source['unit_px'], target['unit_px']))


def _build_maps(cfg, cfg2, infos, keys, warnings):
    """{key: {role: state}} for the map conditions; target is read first, the reference follows its window."""
    diameter, window_um = cfg2.report.diameter_um, cfg2.report.map_window_um
    size = {role: max(1, int(math.floor(window_um / infos[role]['pitch'] + 0.5))) for role in ROLES}
    states = {role: {} for role in ROLES}
    from .pipeline import _compare_options
    top = _compare_options(cfg)[1]
    target, reference = infos['target'], infos['reference']
    for role in ROLES:  # both guards run before any dataset is opened or mask is read
        shape = infos[role]['frame'].shape_yx
        guard_stage_compare2_report(f'compare2-report ({role})', shape[0] * shape[1], len(keys), size[role] ** 2,
                                    infos[role]['limit'])
    _scan_side(cfg, target, top, keys, diameter, size['target'],
               lambda key, st: _origin(st['center'], size['target']), states['target'])

    def reference_origin(key, st):
        t_center = states['target'][key]['center']
        center = _proportional(t_center, target, reference) if t_center is not None else st['center']
        return _origin(center, size['reference'])

    _scan_side(cfg, reference, top, keys, diameter, size['reference'], reference_origin, states['reference'])
    for key in keys:  # windows of conditions whose masks are empty: derived from the other side, or the frame centre
        t, r = states['target'][key], states['reference'][key]
        if t['center'] is not None:
            centre_t = t['center']
        elif r['center'] is not None:
            centre_t = _proportional(r['center'], reference, target)
        else:
            centre_t = (target['frame'].shape_yx[1] / 2.0, target['frame'].shape_yx[0] / 2.0)
        centre_r = r['center'] if r['center'] is not None and t['center'] is None else _proportional(centre_t, target, reference)
        for role, state, centre in (('target', t, centre_t), ('reference', r, centre_r)):
            if state['origin'] is None:
                state['origin'] = _origin(centre, size[role])
            if state['crop'] is None:
                state['crop'] = np.zeros((size[role], size[role]), dtype=bool)
    for role in ROLES:
        _load_overlays(cfg2, infos[role], keys, states[role], size[role], warnings)
    return states


# ---- PNG ---------------------------------------------------------------------------------------------------------

def _compose(crop, overlay, pitch, diff=None):
    """RGBA image: coloured mask cells over a transparent background, layer pattern as ~35 % gray on top."""
    size = crop.shape[0]
    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    if diff is None:
        rgba[crop] = COLOR_COMMON + (MASK_ALPHA,)
    else:
        other = diff
        rgba[crop & other] = COLOR_COMMON + (MASK_ALPHA,)
        rgba[crop & ~other] = COLOR_NEW + (MASK_ALPHA,)
        rgba[~crop & other] = COLOR_GONE + (MASK_ALPHA,)
    if overlay is not None:
        shown = rgba[..., 3] > 0
        blend = rgba[..., :3].astype(np.float64) * (1 - OVERLAY_ALPHA) + OVERLAY_GRAY * OVERLAY_ALPHA
        both = overlay & shown
        rgba[both, :3] = np.rint(blend[both]).astype(np.uint8)
        only = overlay & ~shown
        rgba[only] = (OVERLAY_GRAY, OVERLAY_GRAY, OVERLAY_GRAY, int(round(255 * OVERLAY_ALPHA)))
    bar = int(round(SCALE_BAR_UM / pitch))
    if 0 < bar < size - 8 and size > 12:
        rgba[size - 8:size - 6, size - 6 - bar:size - 6] = COLOR_BAR + (255,)
    buffer = BytesIO()
    Image.fromarray(rgba).save(buffer, format='PNG')
    return buffer.getvalue()


def _png_uri(data):
    import base64
    return 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')


def _render_pair(states, key, infos):
    """(figure dicts per role, whether B shows the A difference colours)."""
    t, r = states['target'][key], states['reference'][key]
    same = (t['crop'].shape == r['crop'].shape and t['origin'] == r['origin']
            and infos['target']['pitch'] == infos['reference']['pitch'])
    figs = {}
    for role, state in (('reference', r), ('target', t)):
        diff = r['crop'] if role == 'target' and same else None
        png = _compose(state['crop'], state['overlay'], infos[role]['pitch'], diff)
        pitch = infos[role]['pitch']
        figs[role] = {'png': png, 'cells': state['cells'], 'area_um2': state['cells'] * pitch * pitch,
                      'components': state['components'], 'status': state['status'],
                      'overlay': state['overlay_status'], 'origin_yx': list(state['origin'])}
    return figs, same


# ---- HTML pieces -----------------------------------------------------------------------------------------------------

def _pending_reason(doc, role, kind):
    product = doc['products'][role]
    if kind == 'line' and product.get('glass_um2') is None:
        return 'glass 면적 대기'
    reasons = doc['sides'][role].get('reasons') or []
    shown = [why for why in reasons if kind in why or 'glass' in why or 'density' in why] or reasons
    return '입력 대기: ' + (', '.join(shown[:2]) if shown else '사유 미기록')


def _tiles(doc):
    delta = doc['delta']['indicators']
    ref, tgt = doc['sides']['reference'], doc['sides']['target']
    parts = []
    for kind in ('line', 'pixel'):
        name = f'vi_{kind}'
        a, b, d = ref[name], tgt[name], delta[f'delta_vi_{kind}']
        if a is None or b is None or d is None:
            reasons = [_pending_reason(doc, role, kind) for role in ROLES if doc['sides'][role][name] is None]
            body = ('<div class="v">대기</div><div class="ab">' + _e(' / '.join(dict.fromkeys(reasons)) or '입력 대기')
                    + '</div>')
        else:
            body = (f'<div class="v">{_f6(b)}</div><div class="ab">기준 {_f6(a)} · '
                    + _span(_sign_class(d), f'{_s6(d)} ({_pct_change(b, a)})') + '</div>')
        parts.append(f'<div class="tile"><div class="k"><span class="sw" style="background:var(--{kind})"></span>'
                     f'{name}</div>{body}</div>')
    return ''.join(parts)


def _headline(doc, conds):
    ind = doc['delta']['indicators']
    ref, tgt = doc['sides']['reference'], doc['sides']['target']
    texts = []
    for kind in ('line', 'pixel'):
        name = f'vi_{kind}'
        a, b = ref[name], tgt[name]
        if a is None or b is None or ind[f'delta_vi_{kind}'] is None:
            texts.append(f'{name}은 입력 대기 중입니다.')
        else:
            texts.append(f'{name}은 <b class="{_sign_class(ind["delta_vi_" + kind])}">{_pct_change(b, a)}</b> 변했습니다.')
    for kind in ('line', 'pixel'):
        field = f'delta_vi_{kind}'
        ranked = sorted((c for c in conds if c.get(field) is not None), key=lambda c: -abs(c[field]))
        if ranked and abs(ranked[0][field]) > 0:
            top = ranked[0]
            texts.append(f'|Δvi_{kind}| 가 가장 큰 조건은 <b>{_e(top["layer"])} {_e(_pair_label(top))}</b> '
                         f'(전체 변화의 {_share(top.get("share_" + kind))})입니다.')
    return ' '.join(texts)


def _product_table(doc, infos, unit_text):
    rows = []
    sizes = sorted({row['diameter_um'] for role in ROLES for layer in doc['sides'][role]['layers']
                    for row in layer.get('by_size', [])})
    size_range = '—' if not sizes else (f'{_size_text(sizes[0])}–{_size_text(sizes[-1])} µm' if len(sizes) > 1
                                          else f'{_size_text(sizes[0])} µm')

    def per_role(fn):
        return [fn(role) for role in ROLES]

    resolution = per_role(lambda role: ' × '.join(str(v) for v in doc['products'][role]['resolution']))
    layers = per_role(lambda role: ', '.join(sorted({layer['layer'] for layer in doc['sides'][role]['layers']})) or '—')
    area = []
    for role in ROLES:
        res, unit = doc['products'][role]['resolution'], infos[role]['unit_um']
        area.append(f'{res[0] * unit[0] / 1000:.2f} × {res[1] * unit[1] / 1000:.2f} mm')
    rows.append(('제품', per_role(lambda role: infos[role]['product_id'] or '—')))
    rows.append(('제품군', per_role(lambda role: doc['products'][role]['model_group'])))
    rows.append(('해상도 (unit)', resolution))
    rows.append(('unit 크기', unit_text))
    rows.append(('표시 영역', area))
    rows.append(('분석 레이어', layers))
    rows.append(('이물 크기 범위', [size_range, size_range]))
    head = '<tr><th class="l">항목</th><th class="l">기준 A</th><th class="l">대상 B</th></tr>'
    return head + ''.join(f'<tr><td class="l">{_e(name)}</td><td class="l">{_e(a)}</td><td class="l">{_e(b)}</td></tr>'
                          for name, (a, b) in rows)


def _removal_cell(removal, name):
    value = None if removal is None else removal.get(name)
    return f'<td class="{_sign_class(value)}">{_s4(value)}</td>'


def _sentences(cond, fig, side_conds, diameter):
    """Report sentences of one condition (HTML strings). fig is the render result or None."""
    items = []
    presence = cond['presence']
    status = {'new': '신규: 대상 B에만 있는 조건입니다.', 'gone': '소멸: 기준 A에만 있던 조건입니다.',
              'both': '양쪽: 기준 A와 대상 B 모두에 있는 조건입니다.'}[presence]
    items.append(f'<b>상태</b>: {status}')
    size = _size_text(diameter)
    if not _changed(cond):
        items.append('<b>영향</b>: 변화 없음. 이 조건은 vi 를 바꾸지 않아 지도를 만들지 않았습니다.')
        return items
    if fig is None:
        items.append(f'<b>불량 영역</b>: 지도 생략(상한 {MAX_MAPS}개). 영역 면적은 표시하지 않습니다.')
    else:
        a, b = fig['reference'], fig['target']
        notes = [f'{ROLE_TEXT[role]} {size} µm 입력 없음' for role, f in (('reference', a), ('target', b))
                 if f['status'] == 'no_record']
        verb = '늘었습니다' if b['area_um2'] > a['area_um2'] else ('줄었습니다' if b['area_um2'] < a['area_um2'] else '같습니다')
        text = (f'{size} µm 이물의 불량 영역이 {_num(a["area_um2"])} → {_num(b["area_um2"])} µm² 로 {verb} '
                f'(영역 {a["components"]} → {b["components"]} 개).')
        if notes:
            text += ' ' + ' · '.join(_e(n) for n in notes) + '.'
        items.append(f'<b>불량 영역</b>: {text}')
    parts = []
    for kind in ('line', 'pixel'):
        value = cond.get(f'delta_vi_{kind}')
        if value is None:
            continue
        if kind != 'line' and 'line' in _types(cond['class']) and 'pixel' not in _types(cond['class']) and value == 0:
            continue
        parts.append(f'vi_{kind} ' + _span(_sign_class(value), f'{_s4(value)}×10⁻⁴')
                     + f' (전체 변화의 {_share(cond.get("share_" + kind))})')
    items.append('<b>영향</b>: ' + ('; '.join(parts) if parts else '대기 또는 변화 없음') + '.')
    transfer = cond.get('delta_lambda_transfer')
    if 'line' in _types(cond['class']) and transfer is not None and abs(transfer) > 0:
        ref_area = (side_conds['reference'].get(cond['condition_id']) or {}).get('area_transfer_um2', 0.0)
        tgt_area = (side_conds['target'].get(cond['condition_id']) or {}).get('area_transfer_um2', 0.0)
        moved = abs(tgt_area - ref_area)
        if moved > 0:  # when only the lambda differs the moved area is unchanged: say nothing about a move
            move = 'line 으로 넘어갔습니다' if tgt_area > ref_area else 'pixel 로 돌아왔습니다'
            items.append(f'<b>함께 나타난 영향</b>: line 영역 변화로 pixel 영역 {_num(moved)} µm² 가 {move}.')
    removal = cond.get('removal_b')
    if removal is None:
        items.append('<b>B에서 제거하면</b>: 대상 B에 없는 조건이라 해당 없음.')
    else:
        items.append('<b>B에서 이 조건을 제거하면</b>: Δvi_line ' + _span(_sign_class(removal['delta_vi_line']),
                                                                      f'{_s4(removal["delta_vi_line"])}×10⁻⁴')
                     + ', Δvi_pixel ' + _span(_sign_class(removal['delta_vi_pixel']),
                                              f'{_s4(removal["delta_vi_pixel"])}×10⁻⁴') + '.')
    return items


def _mapset(index, figs, same, diameter, window_um):
    out = [f'<div class="mapset" id="m{index}">']
    for role in ROLES:
        fig = figs[role]
        overlay = {'ok': '레이어 패턴 표시', None: ''}.get(fig['overlay'], '레이어 패턴 생략')
        caption = (f'{ROLE_TEXT[role]} · {_num(fig["area_um2"])} µm² · 영역 {fig["components"]}개')
        if fig['status'] == 'no_record':
            caption += f' · {_size_text(diameter)} µm 입력 없음'
        elif fig['status'] == 'no_cases':
            caption += ' · 해당 조건 없음'
        if overlay:
            caption += f' · {overlay}'
        out.append(f'<figure><img alt="{_e(ROLE_TEXT[role])} {_size_text(diameter)} µm 지도" '
                   f'src="{_png_uri(fig["png"])}"><figcaption>{_e(caption)}</figcaption></figure>')
    note = ('A·B 같은 위치에 겹쳐 비교(공통 회색 · 신규 빨강 · 소멸 파랑).' if same
            else 'A 는 같은 unit 상대 위치(정렬 아님).')
    out.append(f'<div class="mapnote">창 한 변 {_size_text(window_um)} µm · {_e(note)} · 막대 = {_size_text(SCALE_BAR_UM)} µm</div></div>')
    return ''.join(out)


def _bars(rows, kind):
    width, row_h = 480, 30
    height = len(rows) * row_h + 28
    values = [abs(v) for _name, v in rows if v is not None]
    peak = (max(values) if values else 0.0) * 1.2 or 1.0
    cx, scale = width / 2 + 30, (width / 2 - 50) / peak
    title = f'vi_{kind}'
    out = [f'<b><span class="sw" style="background:var(--{kind})"></span> {title}</b>',
           f'<svg viewBox="0 0 {width + 30} {height}" width="100%" role="img" aria-label="{title} 레이어별 변화">',
           f'<line x1="{cx}" x2="{cx}" y1="4" y2="{height - 20}" stroke="var(--text-muted)"/>']
    for i, (name, value) in enumerate(rows):
        y = 6 + i * row_h
        out.append(f'<text x="4" y="{y + 15}">{_e(name)}</text>')
        if value is None:
            out.append(f'<text class="val" x="{cx + 4}" y="{y + 15}">대기</text>')
            continue
        bw = max(abs(value) * scale, 1)
        x = cx if value >= 0 else cx - bw
        color = 'var(--up)' if value >= 0 else 'var(--down)'
        out.append(f'<rect x="{x:.1f}" y="{y + 3}" width="{bw:.1f}" height="16" rx="3" fill="{color}">'
                   f'<title>{_e(name)} Δ{title} {_s4(value)}×10⁻⁴</title></rect>')
        anchor, tx = ('start', x + bw + 4) if value >= 0 else ('end', x - 4)
        out.append(f'<text class="val" x="{tx:.1f}" y="{y + 15}" text-anchor="{anchor}">{_s4(value)}</text>')
    out.append(f'<text x="{cx}" y="{height - 4}" text-anchor="middle">Δ (×10⁻⁴)</text></svg>')
    return ''.join(out)


def _heat(layers, kind, sizes):
    field = f'delta_vi_{kind}'
    cells = {(row['layer'], s['diameter_um']): s.get(field) for row in layers for s in row.get('by_size', [])}
    peak = max([abs(v) for v in cells.values() if v is not None] or [0.0]) or 1.0
    out = [f'<tr><th class="l">레이어 \\ 이물 크기</th>' + ''.join(f'<th>{_size_text(s)} µm</th>' for s in sizes) + '</tr>']
    for row in layers:
        tds = []
        for size in sizes:
            value = cells.get((row['layer'], size))
            if value is None:
                tds.append('<td>—</td>')
                continue
            q = min(abs(value) / peak, 1.0)
            color = '208,59,59' if value >= 0 else '42,120,214'
            tds.append(f'<td style="background:color-mix(in srgb, rgb({color}) {round(q * 85)}%, var(--mid))" '
                       f'title="{_e(row["layer"])} · {_size_text(size)} µm · {kind}: {_s4(value)}×10⁻⁴">{_s4(value)}</td>')
        out.append(f'<tr><td class="l">{_e(row["layer"])}</td>{"".join(tds)}</tr>')
    return ''.join(out)


def _caa_table(doc):
    ref, tgt, ind = doc['sides']['reference'], doc['sides']['target'], doc['delta']['indicators']
    out = ['<tr><th class="l">지표</th><th>기준 A</th><th>대상 B</th><th>Δ</th><th>Δ%</th></tr>']
    for kind in ('line', 'pixel'):
        a, b, d = ref[f'caa_{kind}'], tgt[f'caa_{kind}'], ind[f'delta_caa_{kind}']
        out.append(f'<tr><td class="l">caa_{kind}</td><td>{_f6(a)}</td><td>{_f6(b)}</td>'
                   f'<td class="{_sign_class(d)}">{_s6(d)}</td><td>{_pct_change(b, a)}</td></tr>')
    return ''.join(out)


# ---- page --------------------------------------------------------------------------------------------------------------

CSS = '''.viz-root{color-scheme:light;
  --surface-0:#f4f3f0;--surface-1:#fcfcfb;--border:#e2e0da;--grid:#ebe9e4;
  --text-primary:#0b0b0b;--text-secondary:#52514e;--text-muted:#7a7972;
  --line:#2a78d6;--pixel:#eb6834;--up:#d03b3b;--down:#2a78d6;--mid:#f0efec;--common:#8d8c85;
  --sel:#fff3d6;--badge-new:#fde7e7;--badge-gone:#e3eefc;--badge-both:#ecebe6;}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;
  --surface-0:#121211;--surface-1:#1a1a19;--border:#33332f;--grid:#2a2a27;
  --text-primary:#f0efec;--text-secondary:#c3c2b7;--text-muted:#94938a;
  --line:#3987e5;--pixel:#d95926;--up:#e66767;--down:#3987e5;--mid:#383835;--common:#77766f;
  --sel:#3a3018;--badge-new:#4a2323;--badge-gone:#1d2f4a;--badge-both:#2c2c29;}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;
  --surface-0:#121211;--surface-1:#1a1a19;--border:#33332f;--grid:#2a2a27;
  --text-primary:#f0efec;--text-secondary:#c3c2b7;--text-muted:#94938a;
  --line:#3987e5;--pixel:#d95926;--up:#e66767;--down:#3987e5;--mid:#383835;--common:#77766f;
  --sel:#3a3018;--badge-new:#4a2323;--badge-gone:#1d2f4a;--badge-both:#2c2c29;}
*{box-sizing:border-box} body{margin:0}
.viz-root{background:var(--surface-0);color:var(--text-primary);font:14px/1.55 -apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;min-height:100vh}
.wrap{max-width:1180px;margin:0 auto;padding:24px}
header{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}
h1{font-size:22px;margin:0 0 4px} h2{font-size:17px;margin:0 0 4px} h3{font-size:14px;margin:14px 0 6px}
.sub{color:var(--text-secondary);margin:0} .muted{color:var(--text-muted);font-size:12px}
section{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:18px 20px;margin:16px 0}
.lead{color:var(--text-secondary);margin:2px 0 12px;font-size:13px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:900px){.grid2{grid-template-columns:1fr}}
.tile{border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.tile .k{font-size:13px;color:var(--text-secondary);display:flex;align-items:center;gap:6px}
.tile .v{font-size:30px;font-weight:650;margin:2px 0;font-variant-numeric:tabular-nums}
.tile .ab{font-size:13px;color:var(--text-secondary);font-variant-numeric:tabular-nums}
.headline{font-size:15px;margin:14px 0 0;padding:10px 12px;border-left:3px solid var(--text-muted);background:var(--surface-0);border-radius:0 6px 6px 0}
.banner{border:1px solid var(--border);border-left:3px solid var(--pixel);background:var(--surface-1);border-radius:6px;padding:8px 12px;margin:12px 0;font-size:13px}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:6px 8px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap}
th{color:var(--text-secondary);font-weight:600;font-size:12px;position:sticky;top:0;background:var(--surface-1)}
td.l,th.l{text-align:left}
#conds tr[data-k]{cursor:pointer} #conds tr[data-k]:hover td{background:var(--surface-0)} #conds tr.on td{background:var(--sel)}
.up{color:var(--up)} .down{color:var(--down)}
.badge{font-size:11px;border-radius:4px;padding:1px 6px;margin-left:6px}
.b-new{background:var(--badge-new)} .b-gone{background:var(--badge-gone)} .b-both{background:var(--badge-both)}
.controls{display:flex;gap:8px;align-items:center;margin:0 0 10px;flex-wrap:wrap}
.controls button,.theme{border:1px solid var(--border);background:var(--surface-1);color:var(--text-primary);border-radius:6px;padding:4px 10px;cursor:pointer;font:inherit;font-size:12px}
.controls button[aria-pressed="true"]{background:var(--text-primary);color:var(--surface-1)}
.scroll{max-height:460px;overflow:auto;border:1px solid var(--grid);border-radius:6px}
svg text{fill:var(--text-secondary);font-size:11px} svg .val{fill:var(--text-primary)}
.story{display:grid;grid-template-columns:auto 1fr;gap:16px;border:1px solid var(--border);border-radius:10px;padding:14px;margin:12px 0;cursor:pointer}
.story:hover{border-color:var(--text-muted)}
.story .rank{font-size:12px;color:var(--text-muted)}
.story .title{font-size:15px;font-weight:600;margin:2px 0 8px}
.story ol,#detail ol{margin:0;padding-left:20px} .story li,#detail li{margin:4px 0}
.mapset{display:flex;gap:8px;flex-wrap:wrap}
.mapset figure{margin:0;width:190px} .mapset figcaption{font-size:11px;color:var(--text-muted);text-align:center}
.mapset img{width:190px;height:190px;border:1px solid var(--border);border-radius:4px;background:var(--surface-0);display:block}
.mapnote{flex-basis:100%;font-size:11px;color:var(--text-muted)}
#store{display:none}
.legend{display:flex;gap:14px;font-size:12px;color:var(--text-secondary);margin:6px 0;flex-wrap:wrap}
.legend span{display:inline-flex;align-items:center;gap:5px}
.explorer{display:grid;grid-template-columns:1fr 420px;gap:16px}
@media (max-width:1000px){.explorer{grid-template-columns:1fr}}
.hm td{text-align:center;min-width:56px}
.chip{display:inline-block;border:1px solid var(--border);border-radius:999px;padding:0 8px;font-size:12px;margin-right:4px}
'''

SCRIPT = '''(function(){
var $=function(id){return document.getElementById(id)};
function fill(scope){scope.querySelectorAll('.mapslot').forEach(function(s){var src=$('m'+s.getAttribute('data-m'));s.innerHTML='';
  if(src){var c=src.cloneNode(true);c.removeAttribute('id');s.appendChild(c)}})}
fill($('stories'));
function select(key){document.querySelectorAll('#conds tr[data-k]').forEach(function(tr){tr.classList.toggle('on',tr.getAttribute('data-k')===key)});
  var d=$('d'+key),det=$('detail');det.innerHTML=d?d.innerHTML:'';fill(det)}
$('conds').addEventListener('click',function(e){var tr=e.target.closest('tr[data-k]');if(tr)select(tr.getAttribute('data-k'))});
document.querySelectorAll('.story').forEach(function(card){card.addEventListener('click',function(){
  select(card.getAttribute('data-k'));$('conds').scrollIntoView({behavior:'smooth',block:'center'})})});
$('cCtl').addEventListener('click',function(e){var b=e.target.closest('button');if(!b)return;var f=b.getAttribute('data-f');
  $('cCtl').querySelectorAll('button').forEach(function(x){x.setAttribute('aria-pressed',x===b)});
  document.querySelectorAll('#conds tr[data-types]').forEach(function(tr){var t=tr.getAttribute('data-types');
    tr.style.display=(f==='all'||t==='all'||t.split(' ').indexOf(f)>=0)?'':'none'})});
$('hmCtl').addEventListener('click',function(e){var b=e.target.closest('button');if(!b)return;var t=b.getAttribute('data-t');
  $('hmCtl').querySelectorAll('button').forEach(function(x){x.setAttribute('aria-pressed',x===b)});
  $('heat-line').style.display=t==='line'?'':'none';$('heat-pixel').style.display=t==='pixel'?'':'none'});
$('themeBtn').addEventListener('click',function(){var r=document.documentElement,
  c=r.getAttribute('data-theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
  r.setAttribute('data-theme',c==='dark'?'light':'dark')});
var first=document.querySelector('#conds tr[data-k]');if(first)select(first.getAttribute('data-k'));
})();
'''


def _render_html(doc, infos, cfg2, maps, warnings, partial):
    """The whole page. maps = {condition index: (figs, same)}; condition rows use compare2.json values only."""
    delta = doc['delta']
    conds, cno = delta['conditions'], delta['cno']
    diameter, window_um, top_n = cfg2.report.diameter_um, cfg2.report.map_window_um, cfg2.report.top_n
    side_conds = {role: {c['condition_id']: c for c in doc['sides'][role]['conditions']} for role in ROLES}
    entries = [('c', i, _cond_score(c)) for i, c in enumerate(conds)] + [
        ('n', j, abs(c.get('share_pixel') or 0.0)) for j, c in enumerate(cno)]
    entries.sort(key=lambda e: (-e[2], e[0], (conds[e[1]]['layer'], conds[e[1]]['pair_id']) if e[0] == 'c'
                                else (cno[e[1]]['density_layer'] or '', cno[e[1]]['case_name'])))
    cond_rank = _changed_ranking(conds)
    unit_text = []
    for role in ROLES:
        info = infos[role]
        text = f'{info["unit_um"][0]:g} × {info["unit_um"][1]:g} µm'
        unit_text.append(text + (' (유도: (frame+1000 px)/2 × pitch)' if info['unit_derived'] else ''))
    ind = delta['indicators']
    out = ['<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
           '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
           f'<title>설계 변경 영향 보고서</title>\n<style>\n{CSS}</style>\n</head>\n<body>\n'
           '<div class="viz-root" id="root"><div class="wrap">\n']
    out.append('<header><div><h1>설계 변경 영향 보고서</h1>'
               f'<p class="sub">기준 {_e(infos["reference"]["product_id"] or "A")} 대비 대상 '
               f'{_e(infos["target"]["product_id"] or "B")} · compare2 결과 기반</p></div>'
               '<button class="theme" id="themeBtn" type="button">테마 전환</button></header>\n')
    if partial:
        reasons = sorted({why for role in ROLES for why in (doc['sides'][role].get('reasons') or [])})
        out.append('<div class="banner"><b>부분 결과</b>: 일부 입력이 비어 있어 값이 "대기"이거나 근거가 부족할 수 있습니다.'
                   + (' 사유: ' + _e(', '.join(reasons[:6])) + ('…' if len(reasons) > 6 else '') if reasons else '')
                   + '</div>\n')
    out.append(f'<section><h2>제품 정보</h2><table id="prod">{_product_table(doc, infos, unit_text)}</table></section>\n')
    out.append('<section><h2>1. 요약</h2><p class="lead">vi 지표는 불량 발생 비율입니다. 양품 수율이 아닙니다. '
               '값이 커지면 위험이 커집니다.</p>'
               f'<div class="grid2" id="tiles">{_tiles(doc)}</div>'
               f'<div class="headline" id="headline">{_headline(doc, conds)}</div></section>\n')
    # 2. top conditions
    out.append('<section><h2>2. 주요 설계 변경과 영향</h2>'
               f'<p class="lead">vi 변화에 가장 크게 기여한 조건입니다. 지도는 {_size_text(diameter)} µm 이물의 불량 영역이며 '
               '반투명 회색이 해당 레이어 패턴입니다. 기준 A 와 대상 B 는 정렬하지 않았습니다.</p>'
               '<div class="legend"><span><span class="sw" style="background:var(--common)"></span>불량 영역</span>'
               '<span><span class="sw" style="background:var(--up)"></span>B 에서 새로 생긴 영역(같은 위치 비교일 때)</span>'
               '<span><span class="sw" style="background:var(--down)"></span>B 에서 사라진 영역(같은 위치 비교일 때)</span>'
               '<span><span class="sw" style="background:rgba(128,128,128,.35)"></span>레이어 패턴</span></div>'
               '<div id="stories">')
    for rank, i in enumerate(cond_rank[:top_n], start=1):
        c = conds[i]
        slot = (f'<div class="maps"><div class="mapslot" data-m="{i}"></div></div>' if i in maps
                else f'<div class="maps muted">{_no_map_slot(c)}</div>')
        sentences = ''.join(f'<li>{s}</li>' for s in _sentences(c, maps[i][0] if i in maps else None, side_conds,
                                                                  diameter))
        out.append(f'<div class="story" data-k="c{i}">{slot}<div><div class="rank">{rank}위 · '
                   f'<span class="sw" style="background:var(--{_types(c["class"])[0]})"></span> {_e(c["class"])}</div>'
                   f'<div class="title">{_e(c["layer"])} · {_e(_pair_label(c))}{_presence_badge(c["presence"])}</div>'
                   f'<ol>{sentences}</ol></div></div>')
    if not cond_rank:
        out.append('<p class="muted">변화가 있는 조건이 없습니다.</p>')
    out.append('</div></section>\n')
    # 3. table
    head = ('<tr><th class="l">전극 쌍</th><th class="l">레이어</th><th class="l">유형</th><th class="l">상태</th>'
            '<th>Δvi_line</th><th>비율</th><th>Δvi_pixel</th><th>비율</th>'
            '<th>B 제거 시 Δvi_line</th><th>B 제거 시 Δvi_pixel</th></tr>')
    rows = []
    for kind, i, _score in entries:
        if kind == 'c':
            c = conds[i]
            types = ' '.join(_types(c['class']))
            rows.append(
                f'<tr data-k="c{i}" data-types="{types}"><td class="l">{_e(_pair_label(c))}</td><td class="l">{_e(c["layer"])}</td>'
                f'<td class="l"><span class="sw" style="background:var(--{_types(c["class"])[0]})"></span> {_e(c["class"])}</td>'
                f'<td class="l">{_presence_badge(c["presence"])}</td>'
                f'<td class="{_sign_class(c["delta_vi_line"])}">{_s4(c["delta_vi_line"])}</td><td>{_share(c["share_line"])}</td>'
                f'<td class="{_sign_class(c["delta_vi_pixel"])}">{_s4(c["delta_vi_pixel"])}</td><td>{_share(c["share_pixel"])}</td>'
                f'{_removal_cell(c.get("removal_b"), "delta_vi_line")}{_removal_cell(c.get("removal_b"), "delta_vi_pixel")}</tr>')
        else:
            c = cno[i]
            rows.append(
                f'<tr data-k="n{i}" data-types="pixel"><td class="l">{_e(c["electrode_id"] or c["case_name"])}'
                '<span class="badge b-both">CNO</span></td>'
                f'<td class="l">{_e(c["density_layer"] or "—")}</td><td class="l"><span class="sw" style="background:var(--pixel)"></span> pixel</td>'
                f'<td class="l">{_presence_badge(c["presence"])}</td><td>—</td><td>—</td>'
                f'<td class="{_sign_class(c["delta_vi_pixel"])}">{_s4(c["delta_vi_pixel"])}</td><td>{_share(c["share_pixel"])}</td>'
                f'{_removal_cell(c.get("removal_b"), "delta_vi_line")}{_removal_cell(c.get("removal_b"), "delta_vi_pixel")}</tr>')
    shared = delta['shared']
    rows.append('<tr data-types="all"><td class="l muted" colspan="4">공유 영역 변화 (여러 조건이 함께 덮는 영역)</td>'
                f'<td class="{_sign_class(shared["delta_vi_line"])}">{_s4(shared["delta_vi_line"])}</td><td>{_share(shared["share_line"])}</td>'
                f'<td class="{_sign_class(shared["delta_vi_pixel"])}">{_s4(shared["delta_vi_pixel"])}</td><td>{_share(shared["share_pixel"])}</td>'
                '<td>—</td><td>—</td></tr>')
    crp = delta.get('crp_difference')
    if crp is not None:
        rows.append('<tr data-types="all"><td class="l muted" colspan="4">제품군 보정 차이 (CRP)</td><td>—</td><td>—</td>'
                    f'<td class="{_sign_class(crp["delta_vi_pixel"])}">{_s4(crp["delta_vi_pixel"])}</td><td>{_share(crp.get("share_pixel"))}</td>'
                    '<td>—</td><td>—</td></tr>')

    def total_share(value):
        return '—' if value is None or abs(value) < RATIO_EPS else '100%'

    rows.append('<tr data-types="all"><td class="l" colspan="4"><b>합계</b></td>'
                f'<td class="{_sign_class(ind["delta_vi_line"])}"><b>{_s4(ind["delta_vi_line"])}</b></td><td>{total_share(ind["delta_vi_line"])}</td>'
                f'<td class="{_sign_class(ind["delta_vi_pixel"])}"><b>{_s4(ind["delta_vi_pixel"])}</b></td><td>{total_share(ind["delta_vi_pixel"])}</td>'
                '<td></td><td></td></tr>')
    out.append('<section><h2>3. 결함 조건별 영향</h2><p class="lead">조건 = 전극 쌍 × 레이어입니다. 행을 누르면 오른쪽에 해당 위치와 설명이 나옵니다. '
               '비율은 해당 지표 전체 변화 중 이 조건의 몫이며, 음수는 전체 변화와 반대 방향이라는 뜻입니다.</p>'
               '<div class="controls" id="cCtl"><span class="muted">유형</span>'
               '<button type="button" data-f="all" aria-pressed="true">전체</button>'
               '<button type="button" data-f="line" aria-pressed="false">line</button>'
               '<button type="button" data-f="pixel" aria-pressed="false">pixel</button></div>'
               '<div class="explorer"><div class="scroll"><table id="conds">' + head + ''.join(rows) + '</table></div>'
               '<div id="detail"></div></div>'
               '<p class="muted">Δvi 단위 ×10⁻⁴. 여러 조건이 함께 덮는 영역의 변화는 특정 조건에 나누지 않고 "공유 영역 변화"로 따로 보입니다. '
               '"B 제거 시" 는 대상 B 에서 그 조건 하나를 없앴을 때의 vi 변화입니다.</p></section>\n')
    # 4. layers
    layers = delta['layers']
    sizes = sorted({s['diameter_um'] for row in layers for s in row.get('by_size', [])})
    out.append('<section><h2>4. 레이어별 변화</h2><p class="lead">레이어마다 vi 변화량입니다. 아래 표는 이물 크기별 분포입니다 '
               '(빨강 증가 +, 파랑 감소 −).</p><div class="grid2">'
               f'<div>{_bars([(r["layer"], r["delta_vi_line"]) for r in layers], "line")}</div>'
               f'<div>{_bars([(r["layer"], r["delta_vi_pixel"]) for r in layers], "pixel")}</div></div>'
               '<div class="controls" id="hmCtl" style="margin-top:12px"><span class="muted">크기별 분포 (×10⁻⁴)</span>'
               '<button type="button" data-t="line" aria-pressed="true">line</button>'
               '<button type="button" data-t="pixel" aria-pressed="false">pixel</button></div>'
               f'<table class="hm" id="heat-line">{_heat(layers, "line", sizes)}</table>'
               f'<table class="hm" id="heat-pixel" style="display:none">{_heat(layers, "pixel", sizes)}</table></section>\n')
    # 5. caa
    out.append('<section><h2>5. 참고: caa 지표</h2><p class="lead">caa 는 보정 전 불량 발생 비율입니다. '
               f'vi = caa ÷ 보정 계수(line {VI_LINE_DIVISOR}, pixel {VI_PIXEL_DIVISOR}). '
               f'그래서 증감 방향과 비율은 vi 와 같습니다.</p><table id="caa">{_caa_table(doc)}</table></section>\n')
    if warnings:
        out.append('<section><h2>경고</h2><ul>' + ''.join(
            f'<li>{_e(w["code"])}{" · " + _e(ROLE_TEXT[w["role"]]) if w.get("role") else ""}'
            f'{" · " + _e(w["layer"]) if w.get("layer") else ""}: {_e(w["detail"])}</li>' for w in warnings)
            + '</ul></section>\n')
    out.append(f'<p class="muted">생성 {datetime.now(timezone.utc).strftime("%Y-%m-%d")} · 값은 compare2.json 에서 그대로 읽었습니다 '
               f'(스키마 {_e(doc["schema_version"])}).</p>\n')
    # store: every map once, every detail text once
    out.append('</div><div id="store">')
    for i, (figs, same) in sorted(maps.items()):
        out.append(_mapset(i, figs, same, diameter, window_um))
    for kind, i, _score in entries:
        if kind == 'c':
            c = conds[i]
            slot = f'<div class="mapslot" data-m="{i}"></div>' if i in maps else f'<div class="muted">{_no_map_slot(c)}</div>'
            items = ''.join(f'<li>{s}</li>' for s in _sentences(c, maps[i][0] if i in maps else None, side_conds,
                                                                 diameter))
            out.append(f'<div id="dc{i}"><div class="title" style="font-weight:600;margin-bottom:6px">{_e(c["layer"])} · '
                       f'{_e(_pair_label(c))}{_presence_badge(c["presence"])} <span class="chip">{_e(c["class"])}</span></div>'
                       f'{slot}<ol>{items}</ol></div>')
        else:
            c = cno[i]
            removal = c.get('removal_b')
            text = ('CNO(전극 접촉 열림) 항목입니다. 지도는 없습니다. Δvi_pixel '
                    + _span(_sign_class(c['delta_vi_pixel']), f'{_s4(c["delta_vi_pixel"])}×10⁻⁴')
                    + f' (전체 변화의 {_share(c["share_pixel"])}).')
            if removal is not None:
                text += (' B 에서 제거하면 Δvi_pixel ' + _span(_sign_class(removal['delta_vi_pixel']),
                                                           f'{_s4(removal["delta_vi_pixel"])}×10⁻⁴') + '.')
            out.append(f'<div id="dn{i}"><div class="title" style="font-weight:600;margin-bottom:6px">'
                       f'{_e(c["electrode_id"] or c["case_name"])} <span class="chip">CNO</span></div><p>{text}</p></div>')
    out.append(f'</div></div>\n<script>\n{SCRIPT}</script>\n</body>\n</html>\n')
    return ''.join(out)


# ---- orchestration -------------------------------------------------------------------------------------------------------

def build_report(compare2_dir, config_path, out):
    """Return (files, record): files = [(name, text)]; nothing is written."""
    from .pipeline import load_numeric_config
    started = time.time()
    cfg2 = load_compare2_config(config_path)
    out = Path(out)
    if out.exists():
        raise NumericContractError('compare2_report_output_exists', f'{out}: --out must be a new folder')
    if _inside(out, compare2_dir):
        raise NumericContractError('compare2_report_output_inside_input', f'{out}: --out must be outside {compare2_dir}')
    doc, json_sha = _read_compare2(compare2_dir, cfg2)
    cfg = load_numeric_config(cfg2.numeric_config)
    if cfg.get('mode') != 'real':
        raise NumericContractError('compare2_real_mode_required', 'numeric_config mode must be real')
    infos = {role: _side_info(cfg, cfg2, role) for role in ROLES}
    for role in ROLES:
        recorded = (((doc.get('inputs') or {}).get('manifests') or {}).get(role) or {}).get('source_payload_hash')
        loaded = infos[role]['real'][1].get('source_payload_hash')
        if recorded != loaded:
            raise NumericContractError(
                'compare2_input_mismatch',
                f'{role}: compare2.json used source_payload_hash {recorded}, the data now has {loaded}')
    conds = doc['delta']['conditions']
    warnings = []
    ranked = _changed_ranking(conds)
    mapped = ranked[:MAX_MAPS]
    if len(ranked) > MAX_MAPS:
        warnings.append({'code': 'maps_capped', 'role': None, 'layer': None,
                         'detail': f'{len(ranked) - MAX_MAPS} conditions have no map (limit {MAX_MAPS})'})
    keys = [(conds[i]['pair_id'], conds[i]['layer']) for i in mapped]
    maps, figures = {}, []
    if keys:
        states = _build_maps(cfg, cfg2, infos, keys, warnings)
        for i, key in zip(mapped, keys):
            figs, same = _render_pair(states, key, infos)
            maps[i] = (figs, same)
            for role in ROLES:
                if figs[role]['status'] == 'no_record':
                    warnings.append({'code': 'map_input_missing', 'role': role, 'layer': key[1],
                                     'detail': f'{conds[i]["condition_id"]}: {cfg2.report.diameter_um:g} µm 입력 없음'})
            figures.append({'condition_id': conds[i]['condition_id'], 'layer': key[1],
                            'diameter_um': cfg2.report.diameter_um, 'window_um': cfg2.report.map_window_um,
                            'same_position_diff_colours': same,
                            **{role: {k: v for k, v in figs[role].items() if k != 'png'} for role in ROLES},
                            'png_sha256': {role: hashlib.sha256(figs[role]['png']).hexdigest() for role in ROLES}})
    partial = (any(doc['sides'][role].get('status') != 'available' for role in ROLES)
               or doc['delta'].get('check', {}).get('status') != 'passed')
    page = _render_html(doc, infos, cfg2, maps, warnings, partial)
    exit_code = 3 if partial or warnings else 0
    record = {'command': REPORT_COMMAND, 'schema_version': REPORT_SCHEMA, 'status': 'partial' if exit_code else 'available',
              'compare2_status': 'partial' if partial else 'available', 'exit_code': exit_code,
              'compare2_json_sha256': json_sha, 'config': str(cfg2.config_path),
              'compare2_dir': str(Path(compare2_dir)), 'diameter_um': cfg2.report.diameter_um,
              'map_window_um': cfg2.report.map_window_um, 'top_n': cfg2.report.top_n,
              'conditions': len(conds), 'cno_rows': len(doc['delta']['cno']), 'maps': len(maps),
              'figures': figures, 'warnings': warnings,
              'outputs': [str(out / 'report.html'), str(out / 'report.json')],
              'started_at': datetime.fromtimestamp(started, timezone.utc).isoformat(),
              'finished_at': datetime.now(timezone.utc).isoformat(), 'elapsed_seconds': round(time.time() - started, 3)}
    return [('report.html', page), ('report.json', _json_text(record))], record


def run_compare2_report(compare2_dir, config_path, out):
    files, record = build_report(compare2_dir, config_path, out)
    _publish(Path(out), files)
    return {k: record[k] for k in ('command', 'status', 'compare2_status', 'conditions', 'maps', 'warnings', 'outputs',
                                   'elapsed_seconds', 'exit_code')}


def main_compare2_report(compare2_dir, config, out, print_result, error_stream=None):
    """CLI body: prints the result, or writes error.json into a new --out and returns 2."""
    error_stream = error_stream or sys.stderr
    output = Path(out)
    existed = output.exists()
    try:
        result = run_compare2_report(compare2_dir, config, out)
    except (NumericContractError, OSError, ValueError, TypeError, KeyError) as exc:
        code = getattr(exc, 'code', 'input_or_config_error')
        print(f'오류: {exc}', file=error_stream)
        if not existed and not _inside(output, compare2_dir):
            try:
                _publish(output, [('error.json', _json_text({'command': REPORT_COMMAND, 'code': code,
                                                            'detail': str(exc), 'exit_code': 2}))])
            except Exception as publish_error:
                print(f'오류: error.json 을 쓰지 못했습니다 (원래 오류 {code}): {publish_error}', file=error_stream)
        return 2
    print_result(result)
    return result['exit_code']
