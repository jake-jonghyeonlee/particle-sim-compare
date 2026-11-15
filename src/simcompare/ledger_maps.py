"""ledger-maps: value tiles, membership tiles, unit window and numbered map PNGs of one ledger (`ledger_maps_v1`).

Input: compare2.json (02) + its compare2.yaml + ledger.json (08) [+ the 04 folder, existence only in T3].
The command reads every product's hotspot masks once (through `compare2._load_side(..., share_sink=...)`, the same code
path as 02) and keeps, per ledger item, the value it puts on each analysis tile (docs/plan/ledger_report/README.md
sections 16-23). Cards, lines and the range checks 3-5 are added by lmap/T4 at `_cards`.

Memory: one `_Budget` (max_operation_bytes) covers every large allocation of this command (README 18.2).
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

from .compare2 import SCHEMA_VERSION as COMPARE2_SCHEMA, _item_id, _json_text, _load_side, stale_reason
from .compare2_inputs import lambda_factor, load_compare2_config, load_density, size_key
from .compare2_report import _side_info
from .numeric_schema import NumericContractError

SCHEMA_VERSION = 'ledger_maps_v1'
LEDGER_SCHEMA = 'ledger_v1'
COMMAND = 'ledger-maps'
ROLES = ('reference', 'target')
KINDS = ('line', 'pixel')
OWNED_NAMES = {'ledger_maps.json', 'execution.json', 'error.json'}
PNG_NAME = re.compile(r'^map_(reference|target)_\d{3}_(el|it)\.png$')
TMP_PREFIX = '.ledger-maps-'
CHECK_REL_TOL = 1e-9
MAX_NUMBER = 0xFFFF
BIG_RANK = 2 ** 31 - 1
# temporaries of one sink call: share_idx, geom_idx, y/x, tile index, unique + inverse, weights (all int64/float64)
SINK_BYTES_PER_CELL = 64
REMEDY = 'raise max_operation_bytes, or use a larger --tile-um / smaller map (--max-map-px)'

OPTION_DEFAULTS = {
    'range_um': 10.0, 'tile_um': 1.0, 'segment_um': 70.0, 'max_rows': 6, 'linear_min_length_um': 50.0,
    'linear_min_aspect': 4.0, 'edge_repeat_min_match': 0.98, 'coord_offset_spread_um': 1.0,
    'coord_tolerance_um': 2.0, 'coord_min_overlap': 0.5, 'map_px_um': 0.2, 'max_map_px': 2000,
    'max_map_bytes': 12 * 1024 * 1024, 'top_pairs': 30, 'top_cards': 5, 'max_tile_entries': 40000000}
_INT_OPTIONS = {'max_rows', 'max_map_px', 'max_map_bytes', 'top_pairs', 'top_cards', 'max_tile_entries'}


def _invalid(detail):
    return NumericContractError('ledger_maps_option_invalid', detail)


def _missing(detail):
    return NumericContractError('ledger_maps_input_missing', detail)


# ---- options -------------------------------------------------------------------------------------------------------

def validate_options(given=None):
    """The 21-section option values (defaults filled in), or ledger_maps_option_invalid (exit 2)."""
    options = dict(OPTION_DEFAULTS)
    for name, value in (given or {}).items():
        if name not in OPTION_DEFAULTS:
            raise _invalid(f'unknown option {name}')
        if value is not None:
            options[name] = value
    for name, value in options.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise _invalid(f'--{name.replace("_", "-")} must be a finite number, got {value!r}')
        if name in _INT_OPTIONS:
            if int(value) != value:
                raise _invalid(f'--{name.replace("_", "-")} must be an integer, got {value!r}')
            options[name] = int(value)
        else:
            options[name] = float(value)
    checks = (('range_um', lambda v: v > 0, '> 0'), ('tile_um', lambda v: 0.5 <= v <= 2.0, 'in [0.5, 2.0]'),
              ('segment_um', lambda v: v > 0, '> 0'), ('max_rows', lambda v: v >= 1, '>= 1'),
              ('linear_min_length_um', lambda v: v > 0, '> 0'), ('linear_min_aspect', lambda v: v >= 1, '>= 1'),
              ('edge_repeat_min_match', lambda v: 0 < v <= 1, 'in (0, 1]'),
              ('coord_offset_spread_um', lambda v: v >= 0, '>= 0'), ('coord_tolerance_um', lambda v: v > 0, '> 0'),
              ('coord_min_overlap', lambda v: 0 < v <= 1, 'in (0, 1]'), ('map_px_um', lambda v: v > 0, '> 0'),
              ('max_map_px', lambda v: v >= 1, '>= 1'), ('max_map_bytes', lambda v: v >= 1, '>= 1'),
              ('top_pairs', lambda v: v >= 1, '>= 1'), ('top_cards', lambda v: v >= 1, '>= 1'),
              ('max_tile_entries', lambda v: v >= 1, '>= 1'))
    for name, ok, text in checks:
        if not ok(options[name]):
            raise _invalid(f'--{name.replace("_", "-")} must be {text}, got {options[name]!r}')
    return options


# ---- memory budget (README 18.2) -----------------------------------------------------------------------------------

class _Budget(object):
    """One accounting object for every large allocation of the command. reserve BEFORE allocating."""

    def __init__(self, limit):
        self.limit = int(limit)
        self.used = 0
        self.peak = 0

    def reserve(self, nbytes, what):
        nbytes = int(nbytes)
        if self.used + nbytes > self.limit:
            raise NumericContractError(
                'oversized_real_operation',
                f'ledger-maps: {what}: estimated {self.used + nbytes} bytes ({nbytes} more on top of {self.used} '
                f'in use) exceed the limit {self.limit} (max_operation_bytes); {REMEDY}')
        self.used += nbytes
        self.peak = max(self.peak, self.used)

    def release(self, nbytes, what):
        self.used = max(0, self.used - int(nbytes))


def guard_stage_ledger_maps(limit, estimates):
    """Before any mask is read: the dense arrays (tile grids, map canvases) must fit on their own.

    estimates: list of (what, bytes). The compute_areas estimate is reserved separately (it is a transient)."""
    total = sum(nbytes for _what, nbytes in estimates)
    if total > limit:
        detail = ', '.join(f'{what}={nbytes}' for what, nbytes in estimates)
        raise NumericContractError(
            'oversized_real_operation',
            f'guard_stage_ledger_maps: estimated {total} bytes ({detail}) exceed the limit {limit} '
            f'(max_operation_bytes); {REMEDY}')


# ---- input checks ----------------------------------------------------------------------------------------------------

def _sha256_file(raw):
    return hashlib.sha256(raw).hexdigest()


def _execution_ok(folder, label):
    folder = Path(folder)
    if (folder / 'error.json').exists():
        raise _missing(f'{label}: {folder / "error.json"} exists, the previous step did not finish')
    path = folder / 'execution.json'
    if not path.is_file():
        raise _missing(f'{label}: {path} is missing (run the step again)')
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise _missing(f'{label}: {path}: {exc}') from exc
    if not isinstance(record, dict) or record.get('status') == 'error' or record.get('exit_code') == 2:
        raise _missing(f'{label}: {path} reports an error')


def _read_json(path, label):
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise _missing(f'{label}: {path}: {exc}') from exc
    try:
        doc = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise _missing(f'{label}: {path}: {exc}') from exc
    if not isinstance(doc, dict):
        raise _missing(f'{label}: {path}: not a JSON object')
    return doc, _sha256_file(raw)


def _read_inputs(compare2_dir, ledger_dir, config, cfg2, candidates):
    """compare2.json, ledger.json and their hashes; every mismatch ends with exit 2 before any mask is read."""
    if not Path(config).is_file():
        raise _missing(f'config {config} is missing')
    _execution_ok(compare2_dir, 'compare2')
    _execution_ok(ledger_dir, 'ledger')
    c2_doc, c2_sha = _read_json(Path(compare2_dir) / 'compare2.json', 'compare2.json')
    if c2_doc.get('schema_version') != COMPARE2_SCHEMA:
        raise _missing(f'compare2.json schema_version must be {COMPARE2_SCHEMA}')
    stale = stale_reason(c2_doc)
    if stale is not None:
        raise NumericContractError(stale[0], stale[1])
    recorded = ((c2_doc.get('inputs') or {}).get('compare2_config') or {}).get('sha256')
    if recorded != cfg2.config_sha256:
        raise NumericContractError(
            'compare2_config_mismatch',
            f'compare2.json was made with config sha256 {recorded}, this config is {cfg2.config_sha256}')
    led_doc, led_sha = _read_json(Path(ledger_dir) / 'ledger.json', 'ledger.json')
    if led_doc.get('schema_version') != LEDGER_SCHEMA:
        raise _missing(f'ledger.json schema_version must be {LEDGER_SCHEMA}')
    led_inputs = led_doc.get('inputs') or {}
    if led_inputs.get('compare2_json_sha256') != c2_sha:
        raise NumericContractError(
            'ledger_maps_stale',
            f'ledger.json was made from compare2.json sha256 {led_inputs.get("compare2_json_sha256")}, '
            f'this compare2.json is {c2_sha}: 08(ledger)을 다시 실행')
    if led_inputs.get('config_sha256') != cfg2.config_sha256:
        raise NumericContractError(
            'ledger_maps_stale',
            f'ledger.json was made with config sha256 {led_inputs.get("config_sha256")}, this config is '
            f'{cfg2.config_sha256}: 08(ledger)을 다시 실행')
    cand = {'given': candidates is not None, 'path': None if candidates is None else str(candidates),
            'exists': None}
    if candidates is not None:
        cand['exists'] = Path(candidates).is_dir()
        if not cand['exists']:
            raise _missing(f'candidates folder {candidates} does not exist')
    return c2_doc, c2_sha, led_doc, led_sha, cand


# ---- one side -------------------------------------------------------------------------------------------------------

def _side_items(led_doc, role):
    """The ledger items that exist on this side, in ledger order, numbered 1..N."""
    key, present = ('a', ('both', 'gone')) if role == 'reference' else ('b', ('both', 'new'))
    items, by_id = [], {}
    for pair in led_doc.get('pairs') or []:
        for item in pair.get('items') or []:
            if item.get('presence') not in present:
                continue
            number = len(items) + 1
            if number > MAX_NUMBER:
                raise NumericContractError('oversized_real_operation',
                                           f'ledger-maps: more than {MAX_NUMBER} items on one side (PNG number is 16 bit)')
            rec = {'item_id': item['item_id'], 'pair_id': pair['pair_id'], 'kind': item['class'],
                   'layer': item['analysis_layer'], 'upper_layer': item.get('upper_layer'),
                   'lower_layer': item.get('lower_layer'), 'number': number, 'value': item.get(key),
                   'rank': (0 if item['class'] == 'line' else 1) * (MAX_NUMBER + 1) + number}
            items.append(rec)
            by_id[rec['item_id']] = rec
    return items, by_id


def _window(info):
    """README 18.3: a U_px square (per axis) at the centre of the frame, or the whole frame."""
    height, width = info['frame'].shape_yx
    pitch = info['pitch']
    size, origin = [], []
    for total, unit_um in ((width, info['unit_um'][0]), (height, info['unit_um'][1])):
        unit_px = int(round(unit_um / pitch))
        if unit_px >= total:
            size.append(total)
            origin.append(0)
        else:
            size.append(unit_px)
            origin.append((total - unit_px) // 2)
    ox, oy = info['frame'].origin_xy_um
    return {'origin_px': origin, 'size_px': size,
            'origin_um': [ox + origin[0] * pitch, oy + origin[1] * pitch],
            'unit_um': [info['unit_um'][0], info['unit_um'][1]],
            'unit_source': 'derived' if info['unit_derived'] else 'config',
            'tile_origin_um': [ox, oy]}


def _tile_px(info, tile_um):
    pitch = info['pitch']
    px = int(round(tile_um / pitch))
    if px < 1 or abs(px * pitch - tile_um) > 1e-9 * max(1.0, tile_um):
        raise _invalid(f'--tile-um {tile_um} is not a whole number of pixels of the {info["role"]} pitch {pitch}')
    return px


def _region(window, info, range_um):
    """The map region: the window plus range_um on every side, clipped to the frame (x0, y0, x1, y1 in frame px)."""
    height, width = info['frame'].shape_yx
    margin = int(math.ceil(range_um / info['pitch'] - 1e-9))
    x0, y0 = window['origin_px']
    w, h = window['size_px']
    return (max(0, x0 - margin), max(0, y0 - margin), min(width, x0 + w + margin), min(height, y0 + h + margin))


def _scale_factor(geoms, layer_counts, has_dl1, options):
    """Doubling count so every map fits --max-map-px and the raw RGB total fits --max-map-bytes (README 20)."""
    steps = []
    for step in range(0, 40):
        factor = 2 ** step
        total, widest, coarse = 0, 0, True
        for role, (rx0, ry0, rx1, ry1, block) in geoms.items():
            b = block * factor
            dh, dw = -(-(ry1 - ry0) // b), -(-(rx1 - rx0) // b)
            widest = max(widest, dh, dw)
            total += layer_counts[role] * (2 if has_dl1[role] else 1) * dh * dw * 3
            coarse = coarse and b >= max(rx1 - rx0, ry1 - ry0)
        if widest <= options['max_map_px'] and total <= options['max_map_bytes']:
            return factor, steps
        if coarse:
            return factor, steps
        reason = 'max_map_px' if widest > options['max_map_px'] else 'max_map_bytes'
        steps.append({'step': step + 1, 'reason': reason, 'widest_px': widest, 'raw_bytes': total})
    return 2 ** 39, steps


def _block_any(mask, y0, x0, y1, x1, block):
    """Crop a frame-sized bool array to the region and reduce it by blocks (a block is on if any cell is on)."""
    crop = mask[y0:y1, x0:x1]
    height, width = crop.shape
    dh, dw = -(-height // block), -(-width // block)
    if block == 1:
        return crop.copy()
    padded = np.zeros((dh * block, dw * block), dtype=bool)
    padded[:height, :width] = crop
    return padded.reshape(dh, block, dw, block).any(axis=(1, 3))


class _SideContext(object):
    """Everything one product keeps while it is read (the sparse parts are dropped after `_cards`)."""

    def __init__(self, role, info, window, tile_px, region, block, items, by_id, layers):
        self.role = role
        self.info = info
        self.window = window
        self.tile_px = tile_px
        height, width = info['frame'].shape_yx
        self.ntx, self.nty = -(-width // tile_px), -(-height // tile_px)
        self.region = region
        self.block = block
        self.dh = -(-(region[3] - region[1]) // block)
        self.dw = -(-(region[2] - region[0]) // block)
        self.items, self.by_id, self.layers = items, by_id, layers
        self.grids = {}                  # kind -> dense float64 tile grid
        self.canvas = {}                 # layer -> (num uint16, rank int32)
        self.chunks = {}                 # item_id -> ([tile idx], [values], [membership])
        self.chunk_bytes = {}            # item_id -> reserved bytes of its chunks
        self.entries = 0
        self.unlisted = 0
        self.incomplete = set()
        self.total_px = {}               # item_id -> display pixels inside the region
        self.sparse = {}                 # item_id -> {'tiles', 'values', 'member'} after finalize
        self.sparse_bytes = 0


def _tile_index(ctx, flat):
    width = ctx.info['frame'].shape_yx[1]
    y = flat // width
    x = flat - y * width
    return (y // ctx.tile_px) * ctx.ntx + (x // ctx.tile_px), y, x


def _make_sink(ctx, budget, report_diameter, slope, tables, factors, subset, entries_limit):
    pitch = ctx.info['pitch']
    pitch_area = pitch * pitch
    rx0, ry0, rx1, ry1 = ctx.region

    def sink(group_key, kind, item_key, item, frame, geom_idx, share_idx, share):
        item_id = _item_id(item_key[0], item_key[1], item['upper_layer'], item['lower_layer'], item_key[2])
        rec = ctx.by_id.get(item_id)
        if rec is None:
            ctx.unlisted += 1
            return
        temp = SINK_BYTES_PER_CELL * (len(share_idx) + len(geom_idx))
        budget.reserve(temp, f'sink temporaries {ctx.role} item {rec["number"]} ({len(share_idx)}+{len(geom_idx)} cells)')
        try:
            diameter = size_key(group_key.diameter_um)
            chunk = ctx.chunks.setdefault(item_id, ([], [], []))
            # value tiles (only the diameters of the ledger: lambda_diameters_um)
            if len(share_idx) and (subset is None or diameter in subset):
                density, _why = tables[kind].lookup(group_key.density_process_layer, diameter)
                factor = factors[kind]
                if slope[kind] is None or factor is None:
                    pass
                elif density is None:
                    ctx.incomplete.add(item_id)
                else:
                    tiles, _y, _x = _tile_index(ctx, share_idx)
                    unique, inverse = np.unique(tiles, return_inverse=True)
                    values = np.bincount(inverse, weights=share * (density * factor * pitch_area * slope[kind]),
                                         minlength=len(unique))
                    unique = unique.astype(np.int32)
                    grid = ctx.grids[kind]
                    grid[unique] += values
                    budget.reserve(unique.nbytes + values.nbytes, f'item sparse values {ctx.role} item {rec["number"]}')
                    ctx.chunk_bytes[item_id] = ctx.chunk_bytes.get(item_id, 0) + unique.nbytes + values.nbytes
                    ctx.entries += len(unique)
                    chunk[0].append(unique)
                    chunk[1].append(values)
            # membership tiles and the display pixels (only the map diameter)
            if len(geom_idx) and diameter == report_diameter:
                tiles, y, x = _tile_index(ctx, geom_idx)
                member = np.unique(tiles).astype(np.int32)
                budget.reserve(member.nbytes, f'item membership {ctx.role} item {rec["number"]}')
                ctx.chunk_bytes[item_id] = ctx.chunk_bytes.get(item_id, 0) + member.nbytes
                ctx.entries += len(member)
                chunk[2].append(member)
                canvas = ctx.canvas.get(rec['layer'])
                if canvas is not None:
                    inside = (y >= ry0) & (y < ry1) & (x >= rx0) & (x < rx1)
                    if inside.any():
                        flat = ((y[inside] - ry0) // ctx.block) * ctx.dw + (x[inside] - rx0) // ctx.block
                        flat = np.unique(flat)
                        num, rank = canvas
                        num_flat, rank_flat = num.reshape(-1), rank.reshape(-1)
                        win = rec['rank'] < rank_flat[flat]
                        rank_flat[flat[win]] = rec['rank']
                        num_flat[flat[win]] = rec['number']
                        ctx.total_px[item_id] = ctx.total_px.get(item_id, 0) + int(len(flat))
            if ctx.entries > entries_limit:
                raise NumericContractError(
                    'oversized_real_operation',
                    f'ledger-maps: item sparse entries {ctx.entries} exceed --max-tile-entries {entries_limit}; '
                    'raise --tile-um (or --max-tile-entries)')
        finally:
            budget.release(temp, 'sink temporaries')

    return sink


def _merge_sparse(chunks, budget, what):
    """Sorted unique tile index + summed values + membership of the chunks of one item (np.unique + np.bincount).

    The merged copy is reserved before it is built (the chunks are still held)."""
    total = sum(a.nbytes for part in chunks for a in part)
    budget.reserve(total, f'merge copy {what}')
    try:
        tiles = np.concatenate(chunks[0]) if chunks[0] else np.zeros(0, dtype=np.int32)
        values = np.concatenate(chunks[1]) if chunks[1] else np.zeros(0, dtype=np.float64)
        if len(chunks[0]) > 1:
            unique, inverse = np.unique(tiles, return_inverse=True)
            values = np.bincount(inverse, weights=values, minlength=len(unique))
            tiles = unique.astype(np.int32)
        member = np.concatenate(chunks[2]) if chunks[2] else np.zeros(0, dtype=np.int32)
        if len(chunks[2]) > 1:
            member = np.unique(member).astype(np.int32)
    finally:
        budget.release(total, 'merge copy')
    budget.reserve(tiles.nbytes + values.nbytes + member.nbytes, f'merged sparse {what}')
    return tiles, values, member


def _cards(ctx):
    """Cards of this side (lmap/T4). T3 returns no card and carries nothing to the other product.

    Returns (cards, carried_bytes): carried_bytes is what the caller keeps reserved while the next product is read
    (the card pairs' membership and sparse values, README 18.1); the caller releases it at the end."""
    return [], 0


def _encode_png(array):
    """RGB 8 bit, no ancillary chunk (bytes depend only on the pixels)."""
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format='PNG', optimize=False, compress_level=6)
    return buffer.getvalue()


def _number_png(num):
    rgb = np.zeros(num.shape + (3,), dtype=np.uint8)
    rgb[..., 0] = (num & 0xFF).astype(np.uint8)
    rgb[..., 1] = (num >> 8).astype(np.uint8)
    return _encode_png(rgb)


def _numbers_json(num):
    counts = np.bincount(num.reshape(-1))
    return {str(i): int(c) for i, c in enumerate(counts) if i and c}


def _electrode_maps(ctx, cfg2, budget, warnings, layers):
    """One DL1 electrode number picture per layer (README 18.1): the first name wins a shared pixel."""
    from .adapters.electrode_png import load_electrode_layer, scan_electrode_shapes
    role = ctx.role
    root, model = cfg2.report.dl1_root, getattr(cfg2, role).dl1_model
    if root is None or model is None:
        warnings.append({'code': 'maps_dl1_not_configured', 'role': role})
        return {}, [], []
    try:
        available = scan_electrode_shapes(root, model)
    except (NumericContractError, OSError, ValueError) as exc:
        warnings.append({'code': 'electrode_scan_failed', 'role': role, 'detail': str(exc)})
        return {}, [], []
    names = sorted(available)
    if len(names) > MAX_NUMBER:
        raise NumericContractError('oversized_real_operation',
                                   f'ledger-maps: more than {MAX_NUMBER} electrodes in {model} (PNG number is 16 bit)')
    number_of = {name: i + 1 for i, name in enumerate(names)}
    x0, y0, x1, y1 = ctx.region
    frame = ctx.info['frame']
    frame_cells = frame.shape_yx[0] * frame.shape_yx[1]
    table = {name: {'number': number_of[name], 'name': name, 'layers': {}} for name in names}
    files, maps = [], []
    for index, layer in enumerate(layers):
        num = np.zeros((ctx.dh, ctx.dw), dtype=np.uint16)
        budget.reserve(num.nbytes, f'electrode map canvas {role} {layer}')
        for name in names:
            fs_rel = available[name].get(layer)
            if fs_rel is None:
                continue
            budget.reserve(frame_cells * 3, f'electrode PNG decode {role} {name} {layer}')
            try:
                errors = []
                mask = load_electrode_layer(root, model, frame, [fs_rel], errors)
                if mask is None:
                    warnings.append({'code': 'electrode_shape_missing', 'role': role, 'layer': layer,
                                     'detail': f'{name}: unreadable ({", ".join(sorted(set(errors))) or "no file"})'})
                    continue
                disp = _block_any(mask, y0, x0, y1, x1, ctx.block)
                del mask
            except (NumericContractError, OSError, ValueError) as exc:
                warnings.append({'code': 'electrode_shape_missing', 'role': role, 'layer': layer, 'detail': str(exc)})
                continue
            finally:
                budget.release(frame_cells * 3, 'electrode PNG decode')
            overlap = int(np.count_nonzero(disp & (num != 0)))
            new = disp & (num == 0)
            num[new] = number_of[name]
            table[name]['layers'][layer] = {'px': int(np.count_nonzero(new)), 'overlap_px': overlap}
        file = f'map_{role}_{index + 1:03d}_el.png'
        data = _number_png(num)
        budget.reserve(len(data), f'encoded electrode map {role} {layer}')
        files.append((file, data))
        maps.append({'file': file, 'layer': layer, 'kind': 'el', 'px_um': ctx.block * ctx.info['pitch'],
                     'shape_yx': [ctx.dh, ctx.dw], 'origin_px': [x0, y0], 'block_px': ctx.block,
                     'numbers': _numbers_json(num)})
        budget.release(num.nbytes, 'electrode map canvas')
    used = [row for row in table.values() if row['layers']]
    return {'electrodes': sorted(used, key=lambda row: row['number'])}, files, maps


def _within(error, reference):
    return abs(error) <= CHECK_REL_TOL * max(abs(reference), 1e-12)


def _check_items(ctx, entries):
    """Check 1: the tile sum of every item = its ledger value (pending items are counted, not passed)."""
    failed, checked, pending = [], 0, 0
    for rec in ctx.items:
        entry = entries[rec['item_id']]
        if rec['value'] is None:
            pending += 1
            continue
        checked += 1
        if not _within(entry['tile_sum'] - rec['value'], rec['value']):
            failed.append({'item_id': rec['item_id'], 'value': rec['value'], 'tile_sum': entry['tile_sum']})
    status = 'failed' if failed else ('passed' if checked or not pending else 'not_evaluated')
    return {'status': status, 'checked': checked, 'not_evaluated': pending, 'failed': failed[:20],
            'failed_count': len(failed)}


def _check_kinds(ctx, led_doc, key):
    """Check 2: the dense tile grid of a kind = totals.<kind>.items.<a|b> (intercept, CRP and CNO are not in it)."""
    out = {}
    for kind in KINDS:
        total = (((led_doc.get('totals') or {}).get(kind) or {}).get('items') or {}).get(key)
        grid_sum = math.fsum(ctx.grids[kind].tolist()) if ctx.grids.get(kind) is not None else None
        if total is None or grid_sum is None:
            out[kind] = {'status': 'not_evaluated', 'sum': grid_sum, 'total': total}
        else:
            out[kind] = {'status': 'passed' if _within(grid_sum - total, total) else 'failed',
                         'sum': grid_sum, 'total': total}
    return out


def _process_side(role, info, cfg, cfg2, led_doc, doc_hash, options, budget, geom, report_diameter, warnings):
    """Read one product once and return (side json, files, window json, checks, ctx-free summary)."""
    from .pipeline import _configured_real
    product = getattr(cfg2, role)
    group = cfg2.model_groups[product.model_group]
    tables = load_density(cfg2.density_dir, product.model_group)
    factors = {kind: lambda_factor(product, group, kind) for kind in KINDS}
    slope = {kind: ((led_doc.get('kinds') or {}).get(kind) or {}).get('slope') for kind in KINDS}
    subset = None if cfg2.lambda_diameters_um is None else set(cfg2.lambda_diameters_um)
    items, by_id = _side_items(led_doc, role)
    layers = sorted({rec['layer'] for rec in items})
    window = geom['window']
    region, block = geom['region'], geom['block']
    ctx = _SideContext(role, info, window, geom['tile_px'], region, block, items, by_id, layers)
    key = 'a' if role == 'reference' else 'b'

    # estimate of the unchanged compute_areas holding (real_hotspot.estimate_area_bytes, via the dataset admission)
    dataset = _configured_real(cfg, info['section'], info['real'], {'bucket_detail': 'summary', 'top_buckets': geom['top']},
                               require_distance=True)
    if dataset is None:
        raise NumericContractError('compare2_condition_assignment_required', f'{role}: condition_assignment needed')
    provenance = getattr(dataset[1], 'provenance', None) or {}
    current_hash = provenance.get('input_hash')
    estimate = int(((provenance.get('resource_estimate') or {}).get('estimated_peak_bytes')) or 0)
    del dataset
    _check_input_hash(role, doc_hash, current_hash)
    budget.reserve(estimate, f'compute_areas holding ({role})')
    grid_bytes = ctx.ntx * ctx.nty * 8
    canvas_bytes = ctx.dh * ctx.dw * 6
    for kind in KINDS:
        budget.reserve(grid_bytes, f'dense tile grid {role} {kind}')
        ctx.grids[kind] = np.zeros(ctx.ntx * ctx.nty, dtype=np.float64)
    for layer in layers:
        budget.reserve(canvas_bytes, f'item map canvas {role} {layer}')
        ctx.canvas[layer] = (np.zeros((ctx.dh, ctx.dw), dtype=np.uint16),
                             np.full((ctx.dh, ctx.dw), BIG_RANK, dtype=np.int32))
    sink = _make_sink(ctx, budget, report_diameter, slope, tables, factors, subset, options['max_tile_entries'])
    _extracted, meta = _load_side(cfg, role, geom['top'], share_sink=sink)
    del _extracted
    _check_input_hash(role, doc_hash, meta.get('input_hash'))
    budget.release(estimate, 'compute_areas holding')   # the masks and the dataset are gone

    # finalize items: merge the chunks of one item (np.unique + np.bincount), then drop the chunk copies
    entries = {}
    for rec in items:
        chunks = ctx.chunks.pop(rec['item_id'], ([], [], []))
        chunk_bytes = ctx.chunk_bytes.pop(rec['item_id'], 0)
        tiles, values, member = _merge_sparse(chunks, budget, f'{role} item {rec["number"]}')
        budget.release(chunk_bytes, 'item chunks')
        del chunks
        merged = tiles.nbytes + values.nbytes + member.nbytes
        ctx.sparse[rec['item_id']] = {'tiles': tiles, 'values': values, 'member': member, 'bytes': merged}
        ctx.sparse_bytes += merged
        entries[rec['item_id']] = {
            'tile_sum': None if rec['value'] is None else math.fsum(values.tolist()), 'has_area': len(member) > 0,
            'total_px': ctx.total_px.get(rec['item_id'], 0), 'value_tiles': int(len(tiles)),
            'membership_tiles': int(len(member))}

    # numbered pictures (item maps first, then the DL1 electrode maps)
    files, maps, shown = [], [], {}
    for index, layer in enumerate(layers):
        num, _rank = ctx.canvas[layer]
        counts = np.bincount(num.reshape(-1), minlength=len(items) + 1)
        for rec in items:
            if rec['layer'] == layer:
                shown[rec['item_id']] = int(counts[rec['number']])
        file = f'map_{role}_{index + 1:03d}_it.png'
        data = _number_png(num)
        budget.reserve(len(data), f'encoded item map {role} {layer}')
        files.append((file, data))
        maps.append({'file': file, 'layer': layer, 'kind': 'it', 'px_um': ctx.block * info['pitch'],
                     'shape_yx': [ctx.dh, ctx.dw], 'origin_px': [region[0], region[1]], 'block_px': ctx.block,
                     'numbers': _numbers_json(num)})
    ctx.canvas.clear()
    budget.release(canvas_bytes * len(layers), 'item map canvases')
    extra, el_files, el_maps = _electrode_maps(ctx, cfg2, budget, warnings, layers)
    files += el_files
    maps += el_maps

    item_rows, hidden_px = [], 0
    for rec in items:
        entry = entries[rec['item_id']]
        hid = max(0, entry['total_px'] - shown.get(rec['item_id'], 0))
        hidden_px += hid
        status = 'pending' if rec['value'] is None else ('ok' if entry['has_area'] else 'no_map_area')
        item_rows.append({
            'item_id': rec['item_id'], 'pair_id': rec['pair_id'], 'kind': rec['kind'], 'layers': [rec['layer']],
            'upper_layer': rec['upper_layer'], 'lower_layer': rec['lower_layer'], 'number': rec['number'],
            'value': rec['value'], 'tile_sum': entry['tile_sum'], 'has_map_area': entry['has_area'],
            'total_px': entry['total_px'], 'shown_px': shown.get(rec['item_id'], 0), 'hidden_px': hid,
            'value_tiles': entry['value_tiles'], 'membership_tiles': entry['membership_tiles'], 'status': status})
    checks = {'item_sum': _check_items(ctx, entries), 'kind_sum': _check_kinds(ctx, led_doc, key)}
    cards, carried = _cards(ctx)
    budget.reserve(carried, f'cards carried to the next product ({role})')
    ctx.sparse.clear()                      # this product's sparse data is dropped after its cards and checks
    budget.release(ctx.sparse_bytes, 'item sparse values')
    ctx.grids.clear()
    budget.release(grid_bytes * len(KINDS), 'dense tile grids')
    pitch = info['pitch']
    side_json = {
        'frame': {'shape_yx': list(info['frame'].shape_yx), 'origin_um': list(info['frame'].origin_xy_um),
                  'pitch_um': pitch},
        'tiles': {'tile_px': ctx.tile_px, 'tile_um': ctx.tile_px * pitch, 'shape_yx': [ctx.nty, ctx.ntx],
                  'count': ctx.ntx * ctx.nty},
        'region': {'origin_px': [region[0], region[1]], 'size_px': [region[2] - region[0], region[3] - region[1]],
                   'block_px': ctx.block},
        'items': item_rows, 'maps': sorted(maps, key=lambda m: m['file']),
        'electrodes': extra.get('electrodes', []),
        'hidden': {'item_px': hidden_px,
                   'electrode_overlap_px': sum(layer['overlap_px'] for row in extra.get('electrodes', [])
                                               for layer in row['layers'].values())},
        'unlisted_items': ctx.unlisted, 'incomplete_items': sorted(ctx.incomplete)}
    return side_json, files, checks, cards, meta


def _check_input_hash(role, recorded, current):
    if recorded is not None and current is not None and current != recorded:
        raise NumericContractError(
            'compare2_input_hash_mismatch',
            f'{role}: compare2.json used input_hash {recorded}, the data and settings now give {current} '
            '(for example the thk_info file changed): real.yaml 의 condition_assignment 값(max_operation_bytes 포함)이나 '
            'thk·pair 파일을 바꾸면 02 부터 다시 실행')


# ---- output folder (same convention as ledger) -------------------------------------------------------------------

def _owned_output(out):
    """True for a missing/empty folder or one holding only files `ledger-maps` makes."""
    out = Path(out)
    if not out.exists():
        return True
    if not out.is_dir():
        return False
    for path in sorted(out.iterdir()):
        if path.name.startswith(TMP_PREFIX):
            continue
        if path.name not in OWNED_NAMES and not PNG_NAME.match(path.name):
            return False
        if path.name in ('execution.json', 'error.json'):
            try:
                if json.loads(path.read_text(encoding='utf-8')).get('command') != COMMAND:
                    return False
            except (OSError, ValueError, AttributeError):
                return False
    return True


def _remove_results(out):
    out = Path(out)
    if not out.is_dir():
        return
    for path in out.iterdir():
        if path.name == 'ledger_maps.json' or PNG_NAME.match(path.name):
            path.unlink()


def _publish(out, files):
    """Write each file into a temporary folder inside out, then os.replace them one by one (str or bytes content)."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=TMP_PREFIX, dir=str(out)))
    try:
        for name, content in files:
            if isinstance(content, bytes):
                with open(str(tmp / name), 'wb') as handle:
                    handle.write(content)
            else:
                with open(str(tmp / name), 'w', encoding='utf-8', newline='\n') as handle:
                    handle.write(content)
        for name, _content in files:
            os.replace(str(tmp / name), str(out / name))
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


# ---- run ----------------------------------------------------------------------------------------------------------------

def run_ledger_maps(compare2_dir, config, ledger_dir, out, options=None, candidates=None, max_operation_bytes=None):
    """Compute and publish the maps; returns the execution record (exit_code 0 or 3) or raises.

    max_operation_bytes overrides the limit of real.yaml (tests and tuning; the CLI leaves it out)."""
    started = time.time()
    output = Path(out)
    opts = validate_options(options)
    if not _owned_output(output):
        raise NumericContractError('ledger_maps_output_not_owned',
                                   f'{output}: use a new folder or a previous ledger-maps folder')
    if not Path(config).is_file():
        raise _missing(f'config {config} is missing')
    cfg2 = load_compare2_config(config)
    c2_doc, c2_sha, led_doc, led_sha, cand = _read_inputs(compare2_dir, ledger_dir, config, cfg2, candidates)
    from .pipeline import _compare_options, load_numeric_config
    cfg = load_numeric_config(cfg2.numeric_config)
    top = _compare_options(cfg)[1]
    infos = {role: _side_info(cfg, cfg2, role) for role in ROLES}
    limit = max_operation_bytes if max_operation_bytes is not None else min(info['limit'] for info in infos.values())
    budget = _Budget(limit)
    report_diameter = size_key(cfg2.report.diameter_um)
    recorded_hash = {role: ((c2_doc.get('inputs') or {}).get('manifests') or {}).get(role, {}).get('input_hash')
                     for role in ROLES}

    # geometry of both products (no mask is read here)
    geoms, side_layers, has_dl1 = {}, {}, {}
    for role in ROLES:
        info = infos[role]
        items, _by_id = _side_items(led_doc, role)
        side_layers[role] = sorted({rec['layer'] for rec in items})
        window = _window(info)
        has_dl1[role] = cfg2.report.dl1_root is not None and getattr(cfg2, role).dl1_model is not None
        geoms[role] = {'window': window, 'tile_px': _tile_px(info, opts['tile_um']), 'top': top,
                       'region': _region(window, info, opts['range_um']),
                       'block': max(1, int(round(opts['map_px_um'] / info['pitch'])))}
    factor, scale_steps = _scale_factor(
        {role: geoms[role]['region'] + (geoms[role]['block'],) for role in ROLES},
        {role: len(side_layers[role]) for role in ROLES}, has_dl1, opts)
    for role in ROLES:
        geoms[role]['block'] *= factor
    estimates = []
    for role in ROLES:
        g, info = geoms[role], infos[role]
        height, width = info['frame'].shape_yx
        tiles = (-(-width // g['tile_px'])) * (-(-height // g['tile_px']))
        rx0, ry0, rx1, ry1 = g['region']
        dh, dw = -(-(ry1 - ry0) // g['block']), -(-(rx1 - rx0) // g['block'])
        estimates.append((f'tile grids {role}', 2 * tiles * 8))
        estimates.append((f'item map canvases {role}', len(side_layers[role]) * dh * dw * 6))
    guard_stage_ledger_maps(limit, estimates)

    texts_files, sides_json, window_json, checks, cards, metas = [], {}, {}, {}, [], {}
    warnings, reasons = [], []
    for role in ROLES:
        side, files, side_checks, side_cards, meta = _process_side(
            role, infos[role], cfg, cfg2, led_doc, recorded_hash[role], opts, budget, geoms[role],
            report_diameter, warnings)
        sides_json[role] = side
        window_json[role] = geoms[role]['window']
        checks[role] = side_checks
        cards.extend(side_cards)
        metas[role] = meta
        texts_files.extend(files)
    failed = [role for role in ROLES
              if checks[role]['item_sum']['status'] == 'failed'
              or any(row['status'] == 'failed' for row in checks[role]['kind_sum'].values())]
    status = 'partial' if failed else 'ok'
    if failed:
        reasons.append('ledger_maps_value_check_failed')
    summary = {'status': 'failed' if failed else 'passed',
               'item_sum': {role: checks[role]['item_sum'] for role in ROLES},
               'kind_sum': {role: checks[role]['kind_sum'] for role in ROLES},
               'range_sum': None, 'pair_bound': None, 'partition': None,
               'not_evaluated': {'items': {role: checks[role]['item_sum']['not_evaluated'] for role in ROLES}}}
    document = {
        'schema_version': SCHEMA_VERSION, 'status': status, 'reasons': reasons,
        'inputs': {'compare2_json_sha256': c2_sha, 'ledger_json_sha256': led_sha, 'config_sha256': cfg2.config_sha256,
                   'candidates': cand,
                   'real_input_hash': {role: metas[role].get('input_hash') for role in ROLES}},
        'options': {name: opts[name] for name in sorted(opts)},
        'report': {'diameter_um': float(cfg2.report.diameter_um)},
        'window': window_json,
        'sides': sides_json,
        'cards': cards,
        'checks': summary,
        'scale_steps': scale_steps,
        'warnings': warnings, 'bands': []}
    exit_code = 0 if status == 'ok' else 3
    texts_files.sort(key=lambda item: item[0])
    record = {'command': COMMAND, 'status': status, 'reasons': reasons,
              'outputs': [str(output / 'ledger_maps.json')] + [str(output / name) for name, _data in texts_files],
              'compare2_dir': str(compare2_dir), 'ledger_dir': str(ledger_dir), 'config': str(cfg2.config_path),
              'max_reserved_bytes': budget.peak, 'max_operation_bytes': budget.limit,
              'started_at': datetime.fromtimestamp(started, timezone.utc).isoformat(),
              'finished_at': datetime.now(timezone.utc).isoformat(),
              'elapsed_seconds': round(time.time() - started, 3), 'exit_code': exit_code}
    files = texts_files + [('ledger_maps.json', _json_text(document)), ('execution.json', _json_text(record))]
    _remove_results(output)             # a previous run's pictures must not outlive this run
    _publish(output, files)
    error = output / 'error.json'
    if error.exists():
        error.unlink()
    return record


def main_ledger_maps(compare2_dir, config, ledger_dir, out, print_result, options=None, candidates=None,
                     error_stream=None, max_operation_bytes=None):
    """CLI body: prints the record, or leaves error.json + execution.json only and returns 2."""
    error_stream = error_stream or sys.stderr
    output = Path(out)
    try:
        record = run_ledger_maps(compare2_dir, config, ledger_dir, out, options=options, candidates=candidates,
                                 max_operation_bytes=max_operation_bytes)
    except (NumericContractError, OSError, ValueError, TypeError, KeyError) as exc:
        code = getattr(exc, 'code', 'input_or_config_error')
        print(f'오류: {exc}', file=error_stream)
        if code != 'ledger_maps_output_not_owned' and _owned_output(output):
            try:
                _remove_results(output)
                now = datetime.now(timezone.utc).isoformat()
                _publish(output, [
                    ('error.json', _json_text({'command': COMMAND, 'code': code, 'detail': str(exc), 'exit_code': 2})),
                    ('execution.json', _json_text({'command': COMMAND, 'status': 'error', 'reasons': [code],
                                                   'outputs': [], 'compare2_dir': str(compare2_dir),
                                                   'ledger_dir': str(ledger_dir), 'config': str(config),
                                                   'finished_at': now, 'exit_code': 2}))])
            except Exception as publish_error:  # still exit 2; say why error.json is missing
                print(f'오류: error.json 을 쓰지 못했습니다 (원래 오류 {code}): {publish_error}', file=error_stream)
        return 2
    print_result(record)
    return record['exit_code']
