"""ledger-maps: value tiles, membership tiles, unit window and numbered map PNGs of one ledger (`ledger_maps_v2`).

Input: compare2.json (02) + its compare2.yaml + ledger.json (08) [+ the 04 folder, existence only in T3].
The command reads every product's hotspot masks once (through `compare2._load_side(..., share_sink=...)`, the same code
path as 02) and keeps, per ledger item, the value it puts on each analysis tile (docs/plan/ledger_report/README.md
sections 16-23). Cards, lines and the range checks 3-5 are added by lmap/T4 at `_cards`.

Memory: one `_Budget` (max_operation_bytes) covers every large allocation of this command (README 18.2).
"""
from __future__ import annotations

import hashlib
import io
import itertools
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import numpy as np
from PIL import Image

from .compare2 import SCHEMA_VERSION as COMPARE2_SCHEMA, _item_id, _json_text, _load_side, stale_reason
from .compare2_inputs import lambda_factor, load_compare2_config, load_density, size_key
from .compare2_report import _side_info
from .electrode_map import _label_runs, _run_counts
from .numeric_schema import NumericContractError

SCHEMA_VERSION = 'ledger_maps_v2'
LEDGER_SCHEMA = 'ledger_v1'
# ledger item presence (both / new / gone) -> the card row wording (mrep E4): an item of A only is `a_only`
ROW_PRESENCE = {'both': 'both', 'gone': 'a_only', 'new': 'b_only'}
TABS = ('same', 'comp', 'all')
STAGES = ('read', 'merge', 'maps', 'cards')
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
# `_merge_sparse` peak per merged entry (measured 49 B with np.unique(return_inverse) on numpy 2.x, the rest is margin for
# older numpy): concatenated tiles (4) + values (8), argsort/inverse/cumsum temporaries of np.unique (about 33), bincount
# result (8) and the cast tile copy (4). The member phase (4 + sort copy + mask + cast) is smaller.
MERGE_BYTES_PER_ENTRY = 64
MERGE_MEMBER_BYTES_PER_ENTRY = 40
# arrays are summed with math.fsum in pieces of this many numbers (one Python list of <= 2 MB at a time, not reserved)
FSUM_CHUNK = 1 << 16
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

class _BudgetExceeded(NumericContractError):
    """`oversized_real_operation` raised by `_Budget.reserve` (the card step catches it to drop one card, README 28.4)."""


class _Hold(object):
    """The bytes of the budget one part (reference or target stage) of one card owns (README 28.4)."""

    def __init__(self):
        self.bytes = 0


class _Budget(object):
    """One accounting object for every large allocation of the command. reserve BEFORE allocating.

    mrep/T3 (README 28.4): while a card is processed (`scope` is its `_Hold`) every owned reserve/release is added to the
    hold, so a failed card gives back exactly what it holds (`free`). Outside a card, a failing reserve first evicts
    finished reference cards one by one (`evict` callback, worst rank first) and raises only when none is left."""

    def __init__(self, limit):
        self.limit = int(limit)
        self.used = 0
        self.peak = 0
        self.scope = None                # the _Hold of the card part being processed
        self.evictor = None              # () -> True when it freed one reference card, False when none is left
        self.stage = None                # (role, stage): the peak of `used` is kept per stage (execution.json)
        self.stage_peak = {}

    def set_stage(self, role, stage):
        self.stage = (role, stage)
        self.stage_peak[self.stage] = max(self.stage_peak.get(self.stage, 0), self.used)

    def reserve(self, nbytes, what, evict=None, owned=True):
        """evict: callback that frees one card (default `self.evictor`, False = never evict). owned=False keeps the
        bytes out of the running card's hold (a reservation that outlives the card, e.g. the tile electrode grids)."""
        nbytes = int(nbytes)
        if evict is None:
            evict = self.evictor
        while self.used + nbytes > self.limit:
            if self.scope is None and evict and evict():
                continue
            raise _BudgetExceeded(
                'oversized_real_operation',
                f'ledger-maps: {what}: estimated {self.used + nbytes} bytes ({nbytes} more on top of {self.used} '
                f'in use) exceed the limit {self.limit} (max_operation_bytes); {REMEDY}')
        self.used += nbytes
        self.peak = max(self.peak, self.used)
        if self.stage is not None:
            self.stage_peak[self.stage] = max(self.stage_peak.get(self.stage, 0), self.used)
        if self.scope is not None and owned:
            self.scope.bytes += nbytes

    def release(self, nbytes, what, owned=True):
        nbytes = int(nbytes)
        self.used = max(0, self.used - nbytes)
        if self.scope is not None and owned:
            self.scope.bytes -= nbytes

    def begin(self, hold):
        self.scope = hold

    def end(self):
        self.scope = None

    def free(self, hold):
        """Give back everything a hold still owns (a dropped or finished card part)."""
        self.used = max(0, self.used - hold.bytes)
        hold.bytes = 0


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
                   'presence': ROW_PRESENCE[item['presence']],
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
        self.run = None                  # the shared card state (set by run_ledger_maps, None = no cards)
        self.tile_el = {}                # layer -> tile-resolution DL1 electrode numbers (edge repeat only)
        self.el_bytes = 0


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


def _fsum_array(array):
    """math.fsum of a numpy array without one Python float per entry at once (same exact value as fsum(array.tolist()))."""
    return math.fsum(itertools.chain.from_iterable(
        array[start:start + FSUM_CHUNK].tolist() for start in range(0, len(array), FSUM_CHUNK)))


def _merge_sparse(chunks, budget, what):
    """Sorted unique tile index + summed values + membership of the chunks of one item (np.unique + np.bincount).

    The chunks are still held (the caller reserved them). Before anything is allocated the peak of each phase is
    reserved: phase 1 holds the concatenated tiles and values, the np.unique temporaries (argsort, inverse, ...) and the
    bincount result at the same time (MERGE_BYTES_PER_ENTRY per entry); phase 2 does the same for the membership
    (MERGE_MEMBER_BYTES_PER_ENTRY) while the merged tiles and values from phase 1 are alive."""
    tile_entries = sum(len(a) for a in chunks[0])
    member_entries = sum(len(a) for a in chunks[2])
    phase_one = MERGE_BYTES_PER_ENTRY * tile_entries
    budget.reserve(phase_one, f'merge copy {what} ({tile_entries} entries)')
    try:
        tiles = np.concatenate(chunks[0]) if chunks[0] else np.zeros(0, dtype=np.int32)
        values = np.concatenate(chunks[1]) if chunks[1] else np.zeros(0, dtype=np.float64)
        if len(chunks[0]) > 1:
            unique, inverse = np.unique(tiles, return_inverse=True)
            values = np.bincount(inverse, weights=values, minlength=len(unique))
            tiles = unique.astype(np.int32)
            del inverse, unique
    finally:
        budget.release(phase_one, 'merge copy')
    merged = tiles.nbytes + values.nbytes
    budget.reserve(merged, f'merged sparse {what}')
    phase_two = MERGE_MEMBER_BYTES_PER_ENTRY * member_entries
    try:
        budget.reserve(phase_two, f'merge membership copy {what} ({member_entries} entries)')
    except NumericContractError:
        budget.release(merged, 'merged sparse')
        raise
    try:
        member = np.concatenate(chunks[2]) if chunks[2] else np.zeros(0, dtype=np.int32)
        if len(chunks[2]) > 1:
            member = np.unique(member).astype(np.int32)
    finally:
        budget.release(phase_two, 'merge membership copy')
    budget.reserve(member.nbytes, f'merged membership {what}')
    return tiles, values, member


# ---- cards (lmap/T4): lines, tile ownership, edge repeat, A-B correspondence, checks 3-5 --------------------------------
# README 17 (checks 3-5), 18.3-18.4 (window, tile grid, lines, final tile set), 19 (correspondence). Everything below works
# on tile indices: id = ty * ntx + tx, boxes are (tx0, ty0, tx1, ty1) with the end excluded.

MIN_ROW_TILES = 1
TOP_OTHERS = 3
# temporaries of `member_components` per membership tile: 16 B per run (<= tiles) of `_label_runs`, its root copy, the
# argsort of the roots, the cut positions and the per-component id arrays (all int64/int32), with margin
COMPONENT_BYTES_PER_MEMBER = 64
# temporaries of one np.unique(np.concatenate(...)) / np.isin over int64 ids per id (concatenated copy, sort copy, mask)
IDS_UNIQUE_BYTES_PER_ID = 40
CAND_FILE, ALIGN_FILE, STRETCH_FILE = 'candidate_queries_v1.json', 'alignment.json', 'stretch_map.json'
APPLIED_MODES = ('unit', 'local', 'stretch')
COMPARABLE_CHANGE_KINDS = ('same_pair_change', 'no_change')
# mrep/T3: the kinds whose A-B correspondence is tried; a pair with a changed item set uses the items on both products
MATCHABLE_CHANGE_KINDS = COMPARABLE_CHANGE_KINDS + ('layer_change',)
_NO_IDS = np.zeros(0, dtype=np.int64)


def _ceil_tiles(um, tile_um):
    return max(1, int(math.ceil(um / tile_um - 1e-9)))


def _box_ids(box, ntx, nty):
    """Sorted ids of the tiles of a box inside the frame."""
    x0, y0, x1, y1 = max(0, box[0]), max(0, box[1]), min(ntx, box[2]), min(nty, box[3])
    if x1 <= x0 or y1 <= y0:
        return _NO_IDS
    ys = np.arange(y0, y1, dtype=np.int64)
    xs = np.arange(x0, x1, dtype=np.int64)
    return (ys[:, None] * ntx + xs[None, :]).ravel()


def _box_in_frame(box, ntx, nty):
    return box[0] >= 0 and box[1] >= 0 and box[2] <= ntx and box[3] <= nty and box[2] > box[0] and box[3] > box[1]


def _boxes_overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _clip_box(box, ntx, nty):
    return (max(0, box[0]), max(0, box[1]), min(ntx, box[2]), min(nty, box[3]))


def _shift_box(box, sx, sy):
    return (box[0] + sx, box[1] + sy, box[2] + sx, box[3] + sy)


def _box_values(vt, vv, boxes, ntx):
    """The exact (math.fsum) sum of the sparse values inside each box. vt = sorted unique tile ids, vv = their values.

    One np.searchsorted pair per box row (the ids of a box row are one id interval), so a box costs O(its height * log N
    + the tiles inside) and no array of the size of vt is built per box."""
    out = []
    last_row = int(vt[-1]) // ntx if len(vt) else -1
    for x0, y0, x1, y1 in boxes:
        xlo, xhi = max(0, x0), min(ntx, x1)
        ylo, yhi = max(0, y0), min(y1, last_row + 1)
        if not len(vt) or xhi <= xlo or yhi <= ylo:
            out.append(0.0)
            continue
        ys = np.arange(ylo, yhi, dtype=np.int64) * ntx
        lows = np.searchsorted(vt, ys + xlo, side='left')
        highs = np.searchsorted(vt, ys + xhi, side='left')
        parts = [vv[lo:hi] for lo, hi in zip(lows.tolist(), highs.tolist()) if hi > lo]
        out.append(_fsum_array(np.concatenate(parts)) if parts else 0.0)
    return out


def _box_value(vt, vv, box, ntx):
    """Sum of the sparse values (tile ids vt, values vv) inside a box."""
    return _box_values(vt, vv, [box], ntx)[0]


def member_components(member, ntx, win_box):
    """4-neighbour components of the membership tiles cut to the window box (README 18.4).

    Returns [{'tiles': sorted ids, 'bbox': (tx0, ty0, tx1, ty1)}] in the row-major order of the first tile."""
    x0, y0, x1, y1 = win_box
    width, height = x1 - x0, y1 - y0
    if width <= 0 or height <= 0 or not len(member):
        return []
    ty, tx = member // ntx, member % ntx
    keep = (tx >= x0) & (tx < x1) & (ty >= y0) & (ty < y1)
    if not keep.any():
        return []
    active = np.zeros((height, width), dtype=bool)
    active[ty[keep] - y0, tx[keep] - x0] = True
    rows, starts, ends, root = _label_runs(active, _run_counts(active))
    order = np.argsort(root, kind='stable')
    cut = np.flatnonzero(np.diff(root[order])) + 1
    comps = []
    for lo, hi in zip(np.r_[0, cut].tolist(), np.r_[cut, len(order)].tolist()):
        members = order[lo:hi]
        lens = (ends[members] - starts[members]).astype(np.int64)
        total = int(lens.sum())
        offsets = np.repeat(np.cumsum(lens) - lens, lens)
        xs = np.repeat(starts[members].astype(np.int64), lens) + (np.arange(total, dtype=np.int64) - offsets)
        ys = np.repeat(rows[members].astype(np.int64), lens)
        comps.append({'tiles': np.sort((ys + y0) * ntx + (xs + x0)),
                      'bbox': (int(xs.min()) + x0, int(ys.min()) + y0, int(xs.max()) + 1 + x0, int(ys.max()) + 1 + y0)})
    return comps


def _merge_overlapping(boxes):
    """Boxes whose interiors overlap become one location (bbox union); repeat until none overlaps."""
    groups = [(tuple(box), 1) for box in boxes]
    while True:
        n = len(groups)
        parent = list(range(n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        merged, active = False, []
        for i in sorted(range(n), key=lambda k: (groups[k][0][0], groups[k][0][1])):
            active = [j for j in active if groups[j][0][2] > groups[i][0][0]]
            for j in active:
                if _boxes_overlap(groups[i][0], groups[j][0]):
                    a, b = find(i), find(j)
                    if a != b:
                        parent[max(a, b)] = min(a, b)
                        merged = True
            active.append(i)
        if not merged:
            return groups
        joined = {}
        for i in range(n):
            root = find(i)
            box, count = groups[i]
            if root in joined:
                old, total = joined[root]
                box = (min(old[0], box[0]), min(old[1], box[1]), max(old[2], box[2]), max(old[3], box[3]))
                count += total
            joined[root] = (box, count)
        groups = [joined[root] for root in sorted(joined)]


def plan_rows(member, vt, vv, ntx, tile_um, win_box, opts):
    """README 18.4: the rows (lines) of one pair on one product, before tile ownership and the frame test.

    member = the pair's membership tile ids (sorted), vt/vv = its sparse value tiles, win_box = the window in tiles.
    Returns {'linear': bool, 'rows': [{'type', 'box', ...}], 'others_n', 'others_value', 'components'}; a row box is in
    tile units and may leave the frame (the edge rule decides)."""
    reach = _ceil_tiles(opts['range_um'], tile_um)
    comps = member_components(member, ntx, win_box)
    for comp in comps:
        bx0, by0, bx1, by1 = comp['bbox']
        long_t, short_t = max(bx1 - bx0, by1 - by0), min(bx1 - bx0, by1 - by0)
        comp['linear'] = (long_t * tile_um >= opts['linear_min_length_um'] - 1e-9
                          and long_t >= opts['linear_min_aspect'] * short_t - 1e-9)
    total_cells = sum(len(c['tiles']) for c in comps)
    linear_cells = sum(len(c['tiles']) for c in comps if c['linear'])
    pair_linear = bool(comps) and 2 * linear_cells >= total_cells
    max_rows = opts['max_rows']
    rows, others_boxes = [], []
    if pair_linear:
        for comp in comps:
            if not comp['linear']:
                continue
            bx0, by0, bx1, by1 = comp['bbox']
            along_x = (bx1 - bx0) >= (by1 - by0)
            lo, hi = (bx0, bx1) if along_x else (by0, by1)
            s_lo, s_hi = (by0, by1) if along_x else (bx0, bx1)
            length = hi - lo
            n = max(1, min(max_rows, int(math.ceil(length * tile_um / opts['segment_um'] - 1e-9))))
            cuts = [lo + (i * length) // n for i in range(n + 1)]
            for i in range(n):
                a0 = cuts[i] - (reach if i == 0 else 0)
                a1 = cuts[i + 1] + (reach if i == n - 1 else 0)
                box = (a0, s_lo - reach, a1, s_hi + reach) if along_x else (s_lo - reach, a0, s_hi + reach, a1)
                rows.append({'type': 'linear', 'box': box, 'segment': i + 1, 'segments': n})
        if len(rows) > max_rows:
            others_boxes.extend(row['box'] for row in rows[max_rows:])
            rows = rows[:max_rows]
    point_comps = [c for c in comps if not (pair_linear and c['linear'])]
    groups = _merge_overlapping([(c['bbox'][0] - reach, c['bbox'][1] - reach, c['bbox'][2] + reach, c['bbox'][3] + reach)
                                 for c in point_comps])
    # one pass over the sorted sparse values for every place (R2:4), not one pass per place
    sums = _box_values(vt, vv, [box for box, _count in groups], ntx)
    scored = sorted(((-total, box[1], box[0], box, count) for total, (box, count) in zip(sums, groups)))
    slots = max(0, max_rows - len(rows))
    for neg, _y, _x, box, count in scored[:slots]:
        rows.append({'type': 'point', 'box': box, 'components': count})
    others_boxes.extend(item[3] for item in scored[slots:])
    # others_value here is the plain sum over the boxes (the boxes can overlap a listed row); `_side_rows` replaces it with
    # the value on the tiles that no listed row owns (README 18.4)
    others_value = math.fsum(_box_values(vt, vv, others_boxes, ntx))
    return {'linear': pair_linear, 'rows': rows, 'others_n': len(others_boxes), 'others_value': others_value,
            'others_boxes': others_boxes, 'components': len(comps)}


def others_free_value(boxes, rows, vt, vv, ntx, nty):
    """README 18.4 "그 밖 k 곳": the pair's value on (union of the boxes not listed) minus (union of the tiles of the listed
    rows), after the rows are placed. Every tile counts once, a tile a listed row owns counts for that row only."""
    if not boxes or not len(vt):
        return 0.0
    ids = np.unique(np.concatenate([_box_ids(box, ntx, nty) for box in boxes]))
    owned = [row['tiles'] for row in rows if len(row['tiles'])]
    if owned and len(ids):
        ids = ids[~np.isin(ids, np.concatenate(owned))]
    if not len(ids):
        return 0.0
    sums, _hits = _row_sums((ids, np.zeros(len(ids), dtype=np.int64)), vt, vv, 1)
    return float(sums[0])


def _axis_segments(lo, hi, size, unit):
    """One axis of a row box [lo, hi) against the frame [0, size): the part left of the frame (it comes back by +unit),
    the part inside (shift 0) and the part right of it (it comes back by -unit). unit None = no period known (shift None).
    Each segment is (start, end, shift, side)."""
    segments = []
    if lo < 0:
        segments.append((lo, min(hi, 0), None if unit is None else unit, 'low'))
    if hi > 0 and lo < size:
        segments.append((max(lo, 0), min(hi, size), 0, 'mid'))
    if hi > size:
        segments.append((max(lo, size), hi, None if unit is None else -unit, 'high'))
    return segments


def _strip_range(segment, mid):
    """The frame tiles next to the edge that an outside segment of the box came from: as many tiles wide as that segment
    (README 18.4, decision 11), cut to the part of the box inside the frame (mid = its (start, end) on this axis).
    A segment inside the frame is its own strip (the strip keeps the whole extent along the other axis)."""
    start, end, _shift, side = segment
    depth = end - start
    lo, hi = mid
    if side == 'low':
        return lo, min(hi, lo + depth)
    if side == 'high':
        return max(lo, hi - depth), hi
    return start, end


def _edge_resolve(box, ntx, nty, edge):
    """README 18.4 (decision 11, tile periodic mapping): the tiles of `box` outside the frame are replaced by the tiles one
    period U away (tile units; +U for a tile left of / above the frame, -U right of / below it, both axes when both are
    outside). The row stays when every image tile is inside the frame and the neighbour strip next to the cut equals the
    strip one period further (same electrode on the layers of the pair, membership Dice >= `edge_repeat_min_match`)."""
    if not edge['enabled']:
        return {'edge': 'excluded', 'edge_reason': edge['reason']}
    clipped = _clip_box(box, ntx, nty)
    if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
        return {'edge': 'excluded', 'edge_reason': 'edge_no_repeat_position'}
    unit = edge['shift'] or (None, None)
    rounding = edge.get('rounding_um') or (0.0, 0.0)
    xs = _axis_segments(box[0], box[2], ntx, unit[0])
    ys = _axis_segments(box[1], box[3], nty, unit[1])
    mid_x = next(((s[0], s[1]) for s in xs if s[3] == 'mid'), None)
    mid_y = next(((s[0], s[1]) for s in ys if s[3] == 'mid'), None)
    groups, error = [], 0.0
    for xseg in xs:
        for yseg in ys:
            if xseg[3] == 'mid' and yseg[3] == 'mid':
                continue
            if xseg[2] is None or yseg[2] is None:
                return {'edge': 'excluded', 'edge_reason': 'edge_no_repeat_position'}
            vx, vy = xseg[2], yseg[2]
            image = (xseg[0] + vx, yseg[0] + vy, xseg[1] + vx, yseg[1] + vy)
            sx0, sx1 = _strip_range(xseg, mid_x)
            sy0, sy1 = _strip_range(yseg, mid_y)
            strip = (sx0, sy0, sx1, sy1)
            if (not _box_in_frame(image, ntx, nty) or not _box_in_frame(strip, ntx, nty)
                    or not _box_in_frame(_shift_box(strip, vx, vy), ntx, nty)):
                return {'edge': 'excluded', 'edge_reason': 'edge_no_repeat_position'}
            groups.append((vx, vy, image, strip))
            error = max(error, rounding[0] if vx else 0.0, rounding[1] if vy else 0.0)
    verdicts, unverifiable, mismatch = [], False, False
    for vx, vy, _image, strip in groups:
        verdict = edge['equiv'](strip, (vx, vy))
        if verdict is None:
            return {'edge': 'excluded', 'edge_reason': 'edge_no_dl1'}
        if verdict[0] is None or verdict[1] is None:
            unverifiable = True
        elif verdict[0] < edge['min_match'] - 1e-12 or verdict[1] < edge['min_match'] - 1e-12:
            mismatch = True
        verdicts.append(verdict)
    if unverifiable:
        return {'edge': 'excluded', 'edge_reason': 'edge_unverifiable'}
    if mismatch:
        return {'edge': 'excluded', 'edge_reason': 'edge_mismatch'}
    image_ids = np.unique(np.concatenate([_box_ids(group[2], ntx, nty) for group in groups]))
    boxes = [group[2] for group in groups]
    return {'edge': 'repeat_used', 'edge_reason': None, 'fbox': clipped, 'image_ids': image_ids,
            'image_tiles': int(len(image_ids)), 'shift_tiles': [groups[0][0], groups[0][1]],
            'image_shifts': [[g[0], g[1]] for g in groups],
            'image_bbox': (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                           max(b[3] for b in boxes)),
            'edge_match': min(v[0] for v in verdicts), 'edge_dice': min(v[1] for v in verdicts),
            'edge_rounding_um': error}


def place_rows(rows, ntx, nty, edge):
    """Edge repeat, then tile ownership: the first row keeps a tile (README 18.4). Returns the placed rows.

    edge = {'enabled', 'reason', 'shift': (ux, uy) | None, 'rounding_um', 'min_match',
            'equiv': f(strip_box, shift) -> (match, dice) | None}, where match/dice None = cannot be checked.
    Each row gets 'edge', 'fbox' (the box inside the frame that is counted), 'tiles' (sorted ids it owns: its own tiles
    plus the image tiles of a repeat, an image tile that is also an own tile counts once; first row wins a tile) and
    'lost' (tiles another row owns)."""
    placed, owned = [], _NO_IDS
    for row in rows:
        row = dict(row)
        if _box_in_frame(row['box'], ntx, nty):
            row.update({'edge': 'none', 'edge_reason': None, 'fbox': row['box']})
        else:
            row.update(_edge_resolve(row['box'], ntx, nty, edge))
        if row['edge'] == 'excluded':
            row.update({'tiles': _NO_IDS, 'lost': 0, 'fbox': row['box']})
        else:
            ids = _box_ids(row['fbox'], ntx, nty)
            image = row.pop('image_ids', None)
            if image is not None:
                ids = np.union1d(ids, image)
            free = ids[~np.isin(ids, owned, assume_unique=True)] if len(owned) else ids
            row.update({'tiles': free, 'lost': int(len(ids) - len(free))})
            owned = np.union1d(owned, free)
        placed.append(row)
    return placed


def _row_sums(row_tiles, item_tiles, item_values, nrows):
    """Per-row sum of an item's sparse values over the tiles each row owns (sorted ids; np.searchsorted)."""
    ids, owner = row_tiles
    if not len(ids) or not len(item_tiles):
        return np.zeros(nrows), np.zeros(nrows, dtype=np.int64)
    pos = np.searchsorted(ids, item_tiles)
    pos = np.minimum(pos, len(ids) - 1)
    hit = ids[pos] == item_tiles
    which = owner[pos[hit]]
    return (np.bincount(which, weights=item_values[hit], minlength=nrows),
            np.bincount(which, minlength=nrows))


def _owner_index(rows):
    parts = [(row['tiles'], np.full(len(row['tiles']), i, dtype=np.int64)) for i, row in enumerate(rows) if len(row['tiles'])]
    if not parts:
        return _NO_IDS, _NO_IDS
    ids = np.concatenate([p[0] for p in parts])
    owner = np.concatenate([p[1] for p in parts])
    order = np.argsort(ids, kind='stable')
    return ids[order], owner[order]


def _pair_arrays_reserved(ctx, budget, pair_id):
    """`_pair_arrays` with the budget (astra D1): the concurrent peak of the concatenate / np.unique / bincount / int64
    copies is reserved BEFORE any of them runs (the merge constants of `_merge_sparse`), then replaced by the returned
    arrays. Returns (member, tiles, values, held_bytes); the caller releases held_bytes when it drops the arrays."""
    recs = [rec for rec in ctx.items if rec['pair_id'] == pair_id]
    tile_entries = sum(len(ctx.sparse[rec['item_id']]['tiles']) for rec in recs)
    member_entries = sum(len(ctx.sparse[rec['item_id']]['member']) for rec in recs)
    peak = MERGE_BYTES_PER_ENTRY * tile_entries + MERGE_MEMBER_BYTES_PER_ENTRY * member_entries
    budget.reserve(peak, f'pair arrays {ctx.role} {pair_id} ({tile_entries}+{member_entries} entries)')
    try:
        member, vt, vv = _pair_arrays(ctx, pair_id)
    finally:
        budget.release(peak, 'pair arrays merge copy')
    held = member.nbytes + vt.nbytes + vv.nbytes
    budget.reserve(held, f'pair arrays held {ctx.role} {pair_id}')
    return member, vt, vv, held


def _pair_arrays(ctx, pair_id):
    """The pair's membership tiles (sorted unique) and its summed sparse values (tile ids, values)."""
    recs = [rec for rec in ctx.items if rec['pair_id'] == pair_id]
    members = [ctx.sparse[rec['item_id']]['member'] for rec in recs if len(ctx.sparse[rec['item_id']]['member'])]
    tiles = [ctx.sparse[rec['item_id']]['tiles'] for rec in recs if len(ctx.sparse[rec['item_id']]['tiles'])]
    values = [ctx.sparse[rec['item_id']]['values'] for rec in recs if len(ctx.sparse[rec['item_id']]['tiles'])]
    member = np.unique(np.concatenate(members)).astype(np.int64) if members else _NO_IDS
    if tiles:
        unique, inverse = np.unique(np.concatenate(tiles).astype(np.int64), return_inverse=True)
        vv = np.bincount(inverse, weights=np.concatenate(values), minlength=len(unique))
        return member, unique, vv
    return member, _NO_IDS, np.zeros(0)


def _window_box(ctx):
    ox, oy = ctx.window['origin_px']
    width, height = ctx.window['size_px']
    tp = ctx.tile_px
    return (ox // tp, oy // tp, -(-(ox + width) // tp), -(-(oy + height) // tp))


def _tile_electrodes(ctx, run, layer):
    """The DL1 electrode number of every tile (centre pixel) of one layer, frame wide (nty, ntx) uint16; None = no DL1."""
    cache = ctx.tile_el
    if layer in cache:
        return cache[layer]
    from .adapters.electrode_png import load_electrode_layer, scan_electrode_shapes
    cfg2, role = run.cfg2, ctx.role
    root, model = cfg2.report.dl1_root, getattr(cfg2, role).dl1_model
    if root is None or model is None:
        cache[layer] = None
        return None
    if '_names' not in cache:
        try:
            cache['_names'] = scan_electrode_shapes(root, model)
        except (NumericContractError, OSError, ValueError):
            cache['_names'] = None
    available = cache['_names']
    if available is None:
        cache[layer] = None
        return None
    frame = ctx.info['frame']
    height, width = frame.shape_yx
    tp = ctx.tile_px
    grid_bytes = ctx.ntx * ctx.nty * 2
    # not owned by the running card: the grid is cached for the later cards of this product and released after the last
    run.budget.reserve(grid_bytes, f'tile electrode grid {role} {layer}', owned=False)
    ctx.el_bytes += grid_bytes
    grid = np.zeros((ctx.nty, ctx.ntx), dtype=np.uint16)
    cy = np.minimum(np.arange(ctx.nty) * tp + tp // 2, height - 1)
    cx = np.minimum(np.arange(ctx.ntx) * tp + tp // 2, width - 1)
    for number, name in enumerate(sorted(available), start=1):
        fs_rel = available[name].get(layer)
        if fs_rel is None:
            continue
        run.budget.reserve(height * width * 3, f'electrode PNG decode {role} {name} {layer}')
        try:
            mask = load_electrode_layer(root, model, frame, [fs_rel], [])
            if mask is None:
                continue
            sample = mask[cy[:, None], cx[None, :]]
            del mask
        except (NumericContractError, OSError, ValueError):
            continue
        finally:
            run.budget.release(height * width * 3, 'electrode PNG decode')
        new = sample & (grid == 0)
        grid[new] = number
    cache[layer] = grid
    return grid


def _edge_env(ctx, run, member, layers):
    """The edge-repeat rules of one pair on one product (README 18.4, G4)."""
    opts = run.opts
    pitch = ctx.info['pitch']
    tile_px = ctx.tile_px
    tile_um = tile_px * pitch
    width, height = ctx.window['size_px']
    frame_h, frame_w = ctx.info['frame'].shape_yx
    ntx, nty = ctx.ntx, ctx.nty

    def period(unit_px, frame_px):
        """The unit in whole tiles (rounded half up) and the rounding error in um; None when the unit is not smaller than
        the frame (nothing repeats) or the error is above --coord-tolerance-um."""
        if unit_px >= frame_px:
            return None, 0.0
        tiles = (2 * unit_px + tile_px) // (2 * tile_px)
        error = abs(unit_px - tiles * tile_px) * pitch
        if tiles < 1 or error > opts['coord_tolerance_um'] + 1e-9:
            return None, error
        return tiles, error

    (ux, ex), (uy, ey) = period(width, frame_w), period(height, frame_h)

    def equiv(strip, shift_xy):
        """(electrode match, membership Dice) of a strip and the strip one period further; a value is None when nothing
        can be compared (no electrode on either strip, no membership tile on either strip); None = no DL1."""
        grids = [_tile_electrodes(ctx, run, layer) for layer in layers]
        if not grids or any(g is None for g in grids):
            return None
        sx, sy = shift_xy
        ids = _box_ids(strip, ntx, nty)
        ty, tx = ids // ntx, ids % ntx
        match = denom = 0
        for grid in grids:
            a, b = grid[ty, tx], grid[ty + sy, tx + sx]
            used = (a != 0) | (b != 0)
            denom += int(used.sum())
            match += int(((a == b) & used).sum())
        ratio = match / denom if denom else None
        here = ids[np.isin(ids, member)]
        moved = ((here // ntx + sy) * ntx + (here % ntx + sx))
        target_ids = ((ids // ntx + sy) * ntx + (ids % ntx + sx))
        there = target_ids[np.isin(target_ids, member)]
        both = int(np.isin(moved, there).sum())
        dice = None if not len(here) and not len(there) else 2.0 * both / (len(here) + len(there))
        return ratio, dice

    return {'enabled': tile_um <= opts['coord_tolerance_um'] + 1e-9, 'reason': 'coords_resolution_too_coarse',
            'shift': (ux, uy), 'rounding_um': (ex, ey), 'min_match': opts['edge_repeat_min_match'], 'equiv': equiv}


def _side_rows(ctx, run, plan):
    """Rows of one pair on this product (README 18.4 steps 1 and 4 before the A-B split)."""
    member, vt, vv, held = _pair_arrays_reserved(ctx, run.budget, plan['pair_id'])
    try:
        side = _side_rows_planned(ctx, run, plan, member, vt, vv)
    except BaseException:
        run.budget.release(held, 'pair arrays held')
        raise
    side['held'] = held
    return side


def _side_rows_planned(ctx, run, plan, member, vt, vv):
    tile_um = ctx.tile_px * ctx.info['pitch']
    win = _window_box(ctx)
    # the component labelling holds the window as a bool picture and 16 B per run plus the sort/cut temporaries of
    # `member_components`: reserved before it runs (R2:2)
    win_cells = max(0, win[2] - win[0]) * max(0, win[3] - win[1])
    label_bytes = win_cells + COMPONENT_BYTES_PER_MEMBER * len(member)
    run.budget.reserve(label_bytes, f'component labelling {ctx.role} {plan["pair_id"]} (window {win_cells} tiles)')
    try:
        planned = plan_rows(member, vt, vv, ctx.ntx, tile_um, win, run.opts)
    finally:
        run.budget.release(label_bytes, 'component labelling')
    cells = sum(max(0, box[2] - box[0]) * max(0, box[3] - box[1])
                for box in [row['box'] for row in planned['rows']] + planned['others_boxes'])
    temp = 32 * (cells + len(member) + len(vt))
    run.budget.reserve(temp, f'card tiles {ctx.role} {plan["pair_id"]}')
    try:
        layers = sorted({layer for rec in ctx.items if rec['pair_id'] == plan['pair_id']
                         for layer in (rec['layer'], rec['upper_layer'], rec['lower_layer']) if layer})
        edge = _edge_env(ctx, run, member, layers)
        rows = place_rows(planned['rows'], ctx.ntx, ctx.nty, edge)
        # R2:3: the value of the places that are not listed, on the tiles no listed row owns
        planned['others_value'] = others_free_value(planned['others_boxes'], rows, vt, vv, ctx.ntx, ctx.nty)
    finally:
        run.budget.release(temp, 'card tiles')
    return {'rows': rows, 'member': member, 'vt': vt, 'vv': vv, 'planned': planned}


def _fill_values(ctx, plan, rows):
    """Per row: this pair, the other pairs, the range total (README 18.4, check 3 inputs). Rows keep their tiles."""
    kind, pair_id = plan['kind'], plan['pair_id']
    owner = _owner_index(rows)
    nrows = len(rows)
    per_pair, hits, per_item = {}, {}, {}
    pair_recs = [rec for rec in ctx.items if rec['kind'] == kind and rec['pair_id'] == pair_id]
    for rec in ctx.items:
        if rec['kind'] != kind:
            continue
        sp = ctx.sparse[rec['item_id']]
        if not len(sp['tiles']):
            if rec['pair_id'] == pair_id:
                per_item[rec['item_id']] = np.zeros(nrows)
            continue
        sums, count = _row_sums(owner, sp['tiles'].astype(np.int64), sp['values'], nrows)
        per_pair[rec['pair_id']] = per_pair.get(rec['pair_id'], np.zeros(nrows)) + sums
        hits[rec['pair_id']] = hits.get(rec['pair_id'], np.zeros(nrows, dtype=np.int64)) + count
        if rec['pair_id'] == pair_id:
            per_item[rec['item_id']] = sums
    grid = ctx.grids[kind]
    for i, row in enumerate(rows):
        if row['edge'] == 'excluded':
            row.update({'pair': None, 'others': None, 'range_total': None, 'others_sum': None, 'items': None})
            continue
        # mrep/T3 (E4): the value of every item of this pair on the tiles the row owns; presence a_only / b_only / both
        row['items'] = [{'item_id': rec['item_id'], 'presence': rec['presence'],
                         'value': float(per_item[rec['item_id']][i])} for rec in pair_recs]
        this = float(per_pair[pair_id][i]) if pair_id in per_pair else 0.0
        others = sorted(((float(arr[i]), pid) for pid, arr in per_pair.items() if pid != pair_id and hits[pid][i] > 0),
                        key=lambda t: (-t[0], t[1]))
        rest = others[TOP_OTHERS:]
        row.update({'pair': this,
                    'others': {'top': [{'pair_id': pid, 'value': v} for v, pid in others[:TOP_OTHERS]],
                               'others_n': len(rest), 'others_value': math.fsum(v for v, _p in rest)},
                    'others_sum': math.fsum(v for v, _p in others),
                    'range_total': _fsum_array(grid[row['tiles']]) if len(row['tiles']) else 0.0})


def _pair_total(ctx, budget, pair_id, ids):
    """The pair's value on the tile set `ids` (sorted unique)."""
    _m, vt, vv, held = _pair_arrays_reserved(ctx, budget, pair_id)
    try:
        if not len(vt) or not len(ids):
            return 0.0
        sums, _c = _row_sums((ids, np.zeros(len(ids), dtype=np.int64)), vt, vv, 1)
        return float(sums[0])
    finally:
        budget.release(held, 'pair arrays held')


def _window_ids(ctx):
    return _box_ids(_window_box(ctx), ctx.ntx, ctx.nty)


def _tab_scores(pair):
    """The score of one pair on each card tab (mrep C2), None = the pair is not on that tab.

    same: pairs with an item on both products, |same_delta|. comp: composition change pairs, new_value + gone_value
    (both >= 0, so a new 100 and a gone 100 count 200 although the delta is 0). all: |delta|. A pair without a usable
    breakdown (waiting, no value) is not on same / comp."""
    brk = pair.get('breakdown') or {}
    items = pair.get('items') or []
    has_both = any(item.get('presence') == 'both' for item in items)
    scores = {'all': abs(pair['delta'])}
    if brk.get('same_delta') is not None and has_both:
        scores['same'] = abs(brk['same_delta'])
    if (pair.get('change_kind') == 'layer_change' and brk.get('new_value') is not None
            and brk.get('gone_value') is not None):
        scores['comp'] = brk['new_value'] + brk['gone_value']
    return scores


def _select_cards(led_doc, top_cards):
    """README 17/18.1/28: per kind and per tab (same / comp / all) the `top_cards` pairs with a value on both sides
    (pending pairs and CNO are not cards). A pair on several tabs is one card (`tabs`, `tab_ranks`); `rank` is the rank by
    |delta| among all candidates of the kind (the `all` tab). Plans of one kind are in that order."""
    plans = []
    for kind in KINDS:
        pairs = [p for p in led_doc.get('pairs') or [] if p.get('kind') == kind
                 and p.get('a') is not None and p.get('b') is not None and p.get('delta') is not None]
        scored = {p['pair_id']: _tab_scores(p) for p in pairs}
        full_rank, tab_ranks = {}, {}
        for tab in TABS:
            ordered = sorted((p for p in pairs if tab in scored[p['pair_id']]),
                             key=lambda p: (-scored[p['pair_id']][tab], p['pair_id']))
            for rank, pair in enumerate(ordered, start=1):
                if tab == 'all':
                    full_rank[pair['pair_id']] = rank
                if rank <= top_cards:
                    tab_ranks.setdefault(pair['pair_id'], {})[tab] = rank
        by_id = {p['pair_id']: p for p in pairs}
        for pair_id in sorted(tab_ranks, key=lambda pid: (full_rank[pid], pid)):
            pair = by_id[pair_id]
            items = pair.get('items') or []
            common = [item.get('item_id') for item in items if item.get('presence') == 'both']
            change_kind = pair.get('change_kind')
            both_sides = bool(pair.get('present_a') and pair.get('present_b'))
            comparable = (change_kind in COMPARABLE_CHANGE_KINDS and bool(items) and both_sides
                          and all(item.get('presence') == 'both' for item in items))
            if pair.get('present_a') and not pair.get('present_b'):
                match, reason = 'a_only', None
            elif pair.get('present_b') and not pair.get('present_a'):
                match, reason = 'b_only', None
            elif both_sides and change_kind in MATCHABLE_CHANGE_KINDS:
                # mrep/T3: a pair whose item set differs is matched on the items both products have
                match, reason = ('matched', None) if common else ('not_compared', 'no_common_item')
            else:
                match, reason = 'not_compared', change_kind or 'not_comparable'
            ranks = tab_ranks[pair_id]
            plans.append({'kind': kind, 'pair_id': pair_id, 'rank': full_rank[pair_id], 'delta': pair['delta'],
                          'a': pair['a'], 'b': pair['b'], 'change_kind': change_kind,
                          'tabs': [tab for tab in TABS if tab in ranks], 'tab_ranks': dict(ranks),
                          'comparable': bool(comparable), 'match': match, 'reason': reason,
                          'common_ids': common, 'n_items': len(items),
                          # the card with the largest key is dropped first: its best tab rank is the worst, then it
                          # sits on fewer tabs
                          'drop_key': (min(ranks.values()), -len(ranks), KINDS.index(kind), pair_id)})
    return plans


# ---- the 04 field (README 19) --------------------------------------------------------------------------------------------

class _Corr(object):
    """What the 04 folder allows: cells = [(y0, y1, x0, x1, dx, dy)] in reference px, or a band code and why not."""

    def __init__(self, band=None, detail=None, mode=None, cells=None, pitch=None):
        self.band, self.detail, self.mode, self.cells, self.pitch = band, detail, mode, cells, pitch

    @property
    def ok(self):
        return self.band is None and self.cells is not None


def _optional_json(path):
    try:
        doc = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _dig(doc, *keys):
    for key in keys:
        if not isinstance(doc, dict):
            return None
        doc = doc.get(key)
    return doc


INPUT_FINGERPRINT_KEYS = ('manifest_sha256', 'dataset_input_hash')       # candidate_queries_v1.json inputs.<role>.<key>
ASSET_FINGERPRINT_KEYS = ('asset_manifest', 'electrode_import_record')   # inputs.<role>.<key>.sha256 (when 04 recorded it)


def _current_input_fingerprints(info, cfg):
    """The fingerprints 04 stores in `candidate_queries_v1.json` `inputs.<role>` (candidates.prepare_side, candidates.py:
    manifest_sha256 = sha256 of the manifest file, dataset_input_hash = provenance input_hash), recomputed from the
    same files: the manifest of the hotspot folder the config points at and the same assignment/thk files. None = cannot."""
    from .adapters.real_hotspot import configured_input_hash
    from .numeric_images import resolve_path
    section = info['section'] if isinstance(info['section'], dict) else {}
    root, manifest = info['real']
    out = {key: None for key in INPUT_FINGERPRINT_KEYS}
    try:
        manifest_path = section.get('manifest_path', 'manifest.json')
        out['manifest_sha256'] = hashlib.sha256(resolve_path(Path(root), manifest_path).read_bytes()).hexdigest()
    except Exception:                                      # an unreadable file is "cannot recompute", never an error
        pass
    try:
        assignment = section.get('condition_assignment')
        if isinstance(assignment, dict):
            out['dataset_input_hash'] = configured_input_hash(root, manifest, assignment,
                                                               config_dir=cfg.get('_config_dir'))
    except Exception:
        pass
    return out


def _current_asset_fingerprints(info, cfg):
    """sha256 of the electrode asset manifest and import record that the config makes accepted (as 04 records them)."""
    from .pipeline import _electrode_evidence
    try:
        evidence = _electrode_evidence(info['real'][0], info['section'], cfg.get('_config_dir'))
        if evidence is None or evidence[0] != 'accepted':
            return {}
        (_record_file, record_sha), (_manifest_file, manifest_sha) = evidence[2]
        return {'asset_manifest': manifest_sha, 'electrode_import_record': record_sha}
    except Exception:
        return {}


def load_correspondence(candidates, infos, cfg, opts):
    """README 19.2-19.3: mode, fingerprint, pitch and the field of the 04 folder -> _Corr (never raises)."""
    if candidates is None:
        return _Corr('coords_no_candidates', 'no --candidates folder')
    queries = _optional_json(Path(candidates) / CAND_FILE)
    align = _optional_json(Path(candidates) / ALIGN_FILE)
    if queries is None or align is None:
        return _Corr('coords_unavailable', f'{CAND_FILE} or {ALIGN_FILE} is missing or unreadable in {candidates}')
    mode = _dig(queries, 'change_args', 'apply_align')
    if mode in (None, 'diagnostic'):
        return _Corr('coords_not_applied', f'change_args.apply_align = {mode!r}: 04 did not apply an offset field', mode)
    if mode not in APPLIED_MODES:
        return _Corr('coords_unavailable', f'unknown apply_align {mode!r}', mode)
    from .candidates import _canonical_sha
    stale, unknown = [], []

    def compare(what, recorded, current):
        if recorded is None:
            unknown.append(what)
        elif recorded != current:
            stale.append(f'{what}: 04 {recorded!r}, now {current!r}')

    compare('config_sha256', _dig(queries, 'inputs', 'config_sha256'), cfg.get('_config_sha256'))
    for role in ROLES:
        info = infos[role]
        assignment = (info['section'].get('condition_assignment') if isinstance(info['section'], dict) else None)
        compare(f'{role} condition_assignment_sha256', _dig(queries, 'inputs', role, 'condition_assignment_sha256'),
                None if assignment is None else _canonical_sha(assignment))
        compare(f'{role} product_id', _dig(queries, 'inputs', 'cnt', role, 'product_id'), info['product_id'])
        recorded_shape = align.get('frame_shape_reference_yx' if role == 'reference' else 'frame_shape_target_yx')
        compare(f'{role} frame shape', None if recorded_shape is None else list(recorded_shape),
                list(info['frame'].shape_yx))
        # the input files themselves (C2): 04 records fingerprints of what it read, recomputed here from the same files
        compared = 0
        current = _current_input_fingerprints(info, cfg)
        for key in INPUT_FINGERPRINT_KEYS:
            recorded = _dig(queries, 'inputs', role, key)
            if recorded is None:
                continue
            if current[key] is None:
                unknown.append(f'{role} {key} (cannot be recomputed now)')
            elif recorded != current[key]:
                stale.append(f'{role} {key}: 04 {recorded!r}, now {current[key]!r}')
            else:
                compared += 1
        recorded_assets = {key: _dig(queries, 'inputs', role, key, 'sha256') for key in ASSET_FINGERPRINT_KEYS}
        if any(sha is not None for sha in recorded_assets.values()):
            assets = _current_asset_fingerprints(info, cfg)
            for key, recorded in sorted(recorded_assets.items()):
                if recorded is None:
                    continue
                if assets.get(key) is None:
                    unknown.append(f'{role} {key}.sha256 (cannot be recomputed now)')
                elif recorded != assets[key]:
                    stale.append(f'{role} {key}.sha256: 04 {recorded!r}, now {assets[key]!r}')
                else:
                    compared += 1
        if not compared:
            unknown.append(f'{role} input fingerprint (' + ' / '.join(INPUT_FINGERPRINT_KEYS) + ')')
    pitch = align.get('pitch_um')
    compare('pitch_um', pitch, infos['reference']['pitch'])
    if pitch is not None and abs(float(pitch) - infos['reference']['pitch']) <= 1e-9 * max(1.0, abs(float(pitch))):
        stale = [s for s in stale if not s.startswith('pitch_um')]
    if stale:
        return _Corr('coords_stale', '; '.join(stale), mode)
    if unknown:
        return _Corr('coords_unavailable', 'not recorded by 04: ' + ', '.join(unknown), mode)
    if abs(infos['reference']['pitch'] - infos['target']['pitch']) > 1e-9 * max(1.0, infos['reference']['pitch']):
        return _Corr('coords_unavailable', 'reference and target pixel pitch differ', mode)
    unit_cells = [rec for rec in (align.get('unit_offsets') or []) if isinstance(rec, dict)]
    raw = None
    if mode == 'stretch':
        stretch = _optional_json(Path(candidates) / STRETCH_FILE)
        raw = [rec for rec in ((stretch or {}).get('tiles') or []) if isinstance(rec, dict)] or None
    elif mode == 'unit' or (mode == 'local' and unit_cells):
        raw = unit_cells or None
    else:                                           # local without a unit alignment: the global offset
        offset = align.get('offset_xy_px')
        height, width = infos['reference']['frame'].shape_yx
        if isinstance(offset, (list, tuple)) and len(offset) == 2:
            raw = [{'region_px': [0, height, 0, width], 'offset_xy_px': list(offset)}]
    if raw is None:
        return _Corr('coords_unavailable', f'no offset field for apply_align {mode!r}', mode)
    cells = []
    for rec in raw:
        region, offset = rec.get('region_px'), rec.get('offset_xy_px')
        if not (isinstance(region, (list, tuple)) and len(region) == 4 and isinstance(offset, (list, tuple))
                and len(offset) == 2):
            return _Corr('coords_unavailable', 'a field cell has no region_px / offset_xy_px', mode)
        cells.append((int(region[0]), int(region[1]), int(region[2]), int(region[3]), int(offset[0]), int(offset[1])))
    return _Corr(mode=mode, cells=cells, pitch=float(pitch))


def pick_offset(cells, box_px, spread_px):
    """README 19.4: the offset of the field cell with the largest overlap, if every overlapped cell agrees.

    box_px = (x0, y0, x1, y1) in reference px. Returns (offset (dx, dy), None) or (None, reason)."""
    x0, y0, x1, y1 = box_px
    hit = []
    for index, (cy0, cy1, cx0, cx1, dx, dy) in enumerate(cells):
        area = max(0, min(y1, cy1) - max(y0, cy0)) * max(0, min(x1, cx1) - max(x0, cx0))
        if area > 0:
            hit.append((-area, index, dx, dy))
    if not hit:
        return None, 'no_field_cell'
    for _a, _i, dx, dy in hit:
        for _b, _j, ex, ey in hit:
            if max(abs(dx - ex), abs(dy - ey)) > spread_px + 1e-9:
                return None, 'offset_spread'
    hit.sort()
    return (hit[0][2], hit[0][3]), None


def _round_half_away(value):
    """round() of a Fraction with an exact half going away from zero (2.5 -> 3, -2.5 -> -3)."""
    whole = (abs(value.numerator) * 2 + value.denominator) // (2 * value.denominator)
    return whole if value >= 0 else -whole


def _tile_shift(offset_px, info_a, info_b, tile_um):
    """G6: k_B = k_A + round((g_A + d - g_B) / tile) with d = _shift_um = (g_B - g_A) + offset_px * pitch (README 18.3).

    The frame origins cancel (g_A + (g_B - g_A) - g_B = 0), so the shift is round(offset_px / tile_px): it is computed
    exactly (Fraction of the pixel offset over the whole pixels of a tile; no float sum of origins), an exact half tile
    goes away from zero. The rounding error in um is (shift * tile_px - offset_px) * pitch, also exact in pixels."""
    pitch = info_a['pitch']
    tile_px = int(round(tile_um / pitch))
    if tile_px < 1 or abs(tile_px * pitch - tile_um) > 1e-9 * max(1.0, tile_um):
        raise _invalid(f'tile {tile_um} um is not a whole number of pixels of the pitch {pitch}')
    shifts, errors = [], []
    for axis in (0, 1):
        offset = Fraction(offset_px[axis])
        shift = _round_half_away(offset / tile_px)
        shifts.append(shift)
        errors.append(float(shift * tile_px - offset) * pitch)
    return shifts[0], shifts[1], (errors[0], errors[1])


def _ratio_overlap(a_tiles, b_member, ntx, nty, tol_tiles):
    """Share of the tiles a_tiles with a b_member tile within tol_tiles (Chebyshev)."""
    if not len(a_tiles) or not len(b_member):
        return 0.0
    ty, tx = a_tiles // ntx, a_tiles % ntx
    found = np.zeros(len(a_tiles), dtype=bool)
    for dy in range(-tol_tiles, tol_tiles + 1):
        for dx in range(-tol_tiles, tol_tiles + 1):
            ny, nx = ty + dy, tx + dx
            ok = (ny >= 0) & (ny < nty) & (nx >= 0) & (nx < ntx)
            ids = ny[ok] * ntx + nx[ok]
            pos = np.minimum(np.searchsorted(b_member, ids), len(b_member) - 1)
            found[np.flatnonzero(ok)[b_member[pos] == ids]] = True
    return float(found.sum()) / len(a_tiles)


CARD_CHECKS = ('range_sum', 'pair_bound', 'partition', 'row_items')


def _new_acc():
    """The checks one card part (reference or target) collects; they reach the run only when the whole card is done."""
    return {'stats': {name: 0 for name in CARD_CHECKS}, 'checks': {name: [] for name in CARD_CHECKS}}


class _Card(object):
    """One card across both products. `hold_a` = the bytes its reference part keeps reserved until the target part is
    done (README 28.4); `carried` = the reference part's rows / values / memberships."""

    def __init__(self, plan):
        self.plan = plan
        self.hold_a = _Hold()
        self.acc_a = _new_acc()
        self.carried = None


class _CardRun(object):
    """Shared state of the card step of one run (the two products share it through ctx.run)."""

    def __init__(self, led_doc, opts, budget, cfg2, infos, corr):
        self.led_doc, self.opts, self.budget, self.cfg2, self.infos, self.corr = led_doc, opts, budget, cfg2, infos, corr
        self.plans = _select_cards(led_doc, opts['top_cards'])
        self.carried = {}                # (pair_id, kind) -> _Card whose reference part is done (reserved, evictable)
        self.dropped = []                # cards left out because of the memory budget (README 28.4)
        self.checks = {name: [] for name in CARD_CHECKS}
        self.bands = []
        tile_um = None
        for role in ROLES:
            info = infos[role]
            tile_um = _tile_px(info, opts['tile_um']) * info['pitch']
        if corr.band is not None:
            self.bands.append({'code': corr.band, 'detail': corr.detail})
        if tile_um is not None and tile_um > opts['coord_tolerance_um'] + 1e-9:
            self.bands.append({'code': 'coords_resolution_too_coarse',
                               'detail': f'--tile-um {opts["tile_um"]} > --coord-tolerance-um {opts["coord_tolerance_um"]}'})
        self.coarse = any(b['code'] == 'coords_resolution_too_coarse' for b in self.bands)
        self.stats = {name: 0 for name in CARD_CHECKS}

    # ---- card lifecycle (E2, E3) ----
    def drop(self, card, reason, stage, detail=None):
        """Leave the card out: give back its reference hold, remember why (cards_dropped_for_budget)."""
        self.carried.pop((card.plan['pair_id'], card.plan['kind']), None)
        self.budget.free(card.hold_a)
        card.carried = None
        plan = card.plan
        self.dropped.append({'kind': plan['kind'], 'pair_id': plan['pair_id'], 'rank': plan['rank'],
                             'tabs': list(plan['tabs']), 'stage': stage, 'reason': reason,
                             'detail': None if detail is None else str(detail)})

    def evict_one(self):
        """Budget callback: drop the finished reference card that matters least. False = no card holds memory."""
        held = [card for card in self.carried.values() if card.hold_a.bytes > 0]
        if not held:
            return False
        worst = max(held, key=lambda card: card.plan['drop_key'])
        self.drop(worst, 'evicted_for_budget', 'reference_held')
        return True

    def finish(self, card, acc_b):
        """The card is done on both products: its checks and counts join the run totals."""
        for acc in (card.acc_a, acc_b):
            for name in CARD_CHECKS:
                self.stats[name] += acc['stats'][name]
                self.checks[name].extend(acc['checks'][name])


def _row_json(row, info, role, ident, tile_um):
    gx, gy = info['frame'].origin_xy_um
    box = row['fbox']
    return {'id': ident, 'side': role, 'type': row['type'],
            'rect': [round(gx + box[0] * tile_um, 9), round(gy + box[1] * tile_um, 9),
                     round(gx + box[2] * tile_um, 9), round(gy + box[3] * tile_um, 9)],
            'rect_tiles': [int(v) for v in box],
            'edge': row['edge'], 'edge_reason': row.get('edge_reason'),
            'shift_tiles': row.get('shift_tiles'), 'image_tiles': int(row.get('image_tiles', 0)),
            'image_shifts': row.get('image_shifts') or [], 'edge_rounding_um': row.get('edge_rounding_um', 0.0),
            'tiles': int(len(row['tiles'])), 'tiles_lost': int(row['lost']),
            'pair': row['pair'], 'items': row.get('items'), 'others': row['others'], 'range_total': row['range_total'],
            'match': row.get('match', {'status': 'only', 'reason': None})}


def _side_final(ctx, run, plan, rows, role, acc):
    """Values, observed area and checks 3-4 of the final rows of one pair on one product."""
    # reserved before the arrays exist (R2:2): the window ids + the rows' ids (concatenate / unique / isin), the pair's
    # summed sparse values and the searchsorted temporaries of one item
    win = _window_box(ctx)
    win_cells = max(0, win[2] - win[0]) * max(0, win[3] - win[1])
    pair_sparse = sum(len(ctx.sparse[rec['item_id']]['tiles']) for rec in ctx.items if rec['pair_id'] == plan['pair_id'])
    item_sparse = max([len(ctx.sparse[rec['item_id']]['tiles']) for rec in ctx.items] or [0])
    temp = (IDS_UNIQUE_BYTES_PER_ID * (win_cells + sum(len(row['tiles']) for row in rows))
            + 32 * (pair_sparse + item_sparse))
    run.budget.reserve(temp, f'card values {ctx.role} {plan["pair_id"]} (window {win_cells} tiles)')
    try:
        return _side_final_values(ctx, run, plan, rows, role, acc)
    finally:
        run.budget.release(temp, 'card values')


def _side_final_values(ctx, run, plan, rows, role, acc):
    """Checks 3-4 and 6 of one card part go to `acc` (the card's own record); the run totals take them when the whole
    card is finished (E3)."""
    stats, failed = acc['stats'], acc['checks']
    _fill_values(ctx, plan, rows)
    rid = f'{plan["kind"]}:{plan["pair_id"]}:{role}'
    live = [row for row in rows if row['edge'] != 'excluded']
    for i, row in enumerate(rows):
        if row['edge'] == 'excluded':
            continue
        stats['range_sum'] += 1
        total = row['pair'] + row['others_sum']
        if not _within(total - row['range_total'], row['range_total']):
            failed['range_sum'].append({'card': rid, 'row': i, 'range_total': row['range_total'],
                                        'pair_plus_others': total})
        # check 6: the item values of a row add up to the row's value of the pair
        stats['row_items'] += 1
        items_sum = math.fsum(item['value'] for item in row['items'])
        if not _within(items_sum - row['pair'], row['pair']):
            failed['row_items'].append({'card': rid, 'row': i, 'pair': row['pair'], 'items_sum': items_sum})
    ids = np.unique(np.concatenate([_window_ids(ctx)] + [row['tiles'] for row in live]))
    observed = _pair_total(ctx, run.budget, plan['pair_id'], ids)
    del ids                                 # the observed area is not kept: it is reserved only while it is built
    section4 = plan['a'] if role == 'reference' else plan['b']
    row_sum = math.fsum(row['pair'] for row in live)
    stats['pair_bound'] += 1
    slack = lambda y: CHECK_REL_TOL * max(abs(y), 1e-12)
    if row_sum > observed + slack(observed) or observed > section4 + slack(section4):
        failed['pair_bound'].append({'card': rid, 'rows_sum': row_sum, 'observed': observed, 'section4': section4})
    return {'observed': observed, 'section4': section4, 'rows_sum': row_sum}


def _cards(ctx):
    """Cards of this side (lmap/T4, mrep/T3).

    Returns (cards, carried_bytes): carried_bytes is memory the caller keeps reserved outside any card while the next
    product is read (always 0 now: the first product's card data is owned and reserved per card, README 28.4). The second
    product returns the cards that were finished on both products."""
    run = getattr(ctx, 'run', None)
    if run is None or not run.plans:
        return [], 0
    try:
        if ctx.role == 'reference':
            _cards_reference(ctx, run)
            return [], 0
        return _cards_target(ctx, run), 0
    finally:
        run.budget.release(ctx.el_bytes, 'tile electrode grids')
        ctx.el_bytes = 0
        ctx.tile_el.clear()


def _member_reserved(ctx, budget, pair_id, item_ids):
    """The membership tiles (sorted unique int64) of the pair's items listed in `item_ids`, reserved before they are built.
    Returns (member, held_bytes): the caller releases held_bytes when it drops the array."""
    recs = [rec for rec in ctx.items if rec['pair_id'] == pair_id and rec['item_id'] in item_ids]
    entries = sum(len(ctx.sparse[rec['item_id']]['member']) for rec in recs)
    peak = MERGE_MEMBER_BYTES_PER_ENTRY * entries
    budget.reserve(peak, f'common-item membership {ctx.role} {pair_id} ({entries} entries)')
    try:
        parts = [ctx.sparse[rec['item_id']]['member'] for rec in recs if len(ctx.sparse[rec['item_id']]['member'])]
        member = np.unique(np.concatenate(parts)).astype(np.int64) if parts else _NO_IDS
    finally:
        budget.release(peak, 'common-item membership copy')
    budget.reserve(member.nbytes, f'common-item membership held {ctx.role} {pair_id}')
    return member, member.nbytes


def _common_member(ctx, budget, plan, member):
    """README 28.2: the membership of the items both products have (the matching uses it). A pair whose items are all
    common keeps `member` as it is (the result is the same as before). Returns (array, held_bytes)."""
    common = set(plan['common_ids'])
    if all(rec['item_id'] in common for rec in ctx.items if rec['pair_id'] == plan['pair_id']):
        return member, 0
    return _member_reserved(ctx, budget, plan['pair_id'], common)


def _cards_reference(ctx, run):
    """Every card gets its reference part inside its own budget hold. A reserve that fails drops that card alone."""
    budget = run.budget
    for plan in run.plans:
        card = _Card(plan)
        budget.begin(card.hold_a)
        try:
            _card_reference(ctx, run, card)
        except _BudgetExceeded as exc:
            budget.end()
            run.drop(card, 'card_reserve_failed', 'reference', exc)
            continue
        finally:
            budget.end()
        run.carried[plan['pair_id'], plan['kind']] = card


def _card_reference(ctx, run, card):
    plan = card.plan
    side = _side_rows(ctx, run, plan)
    try:
        rows = side['rows']
        # what the card keeps for the second product: the rows' tiles (reserved right away) and the common membership
        run.budget.reserve(sum(row['tiles'].nbytes for row in rows), f'card rows carried {ctx.role} {plan["pair_id"]}')
        final = _side_final(ctx, run, plan, rows, 'reference', card.acc_a)
        a_member = [_NO_IDS for _row in rows]
        if plan['match'] == 'matched':
            common, held = _common_member(ctx, run.budget, plan, side['member'])
            try:
                # the intersect1d copies of the member tiles with each row (concatenate + sort, 24 B per id), reserved first
                temp = 24 * sum(len(common) + len(row['tiles']) for row in rows)
                run.budget.reserve(temp, f'card membership split {ctx.role} {plan["pair_id"]}')
                try:
                    a_member = [np.intersect1d(common, row['tiles'], assume_unique=True) for row in rows]
                finally:
                    run.budget.release(temp, 'card membership split')
            finally:
                run.budget.release(held, 'common-item membership held')
        run.budget.reserve(sum(a.nbytes for a in a_member), f'card membership carried {ctx.role} {plan["pair_id"]}')
    finally:
        run.budget.release(side['held'], 'pair arrays held')
    card.carried = {'rows': rows, 'final': final, 'a_member': a_member, 'planned': side['planned'], 'ntx': ctx.ntx}


def _cards_target(ctx, run):
    """The second part of every card that survived the first product, again inside its own hold: a reserve that fails drops
    the whole card (its reference hold too). A finished card hands its checks to the run (E3)."""
    budget = run.budget
    cards = []
    for plan in run.plans:
        card = run.carried.get((plan['pair_id'], plan['kind']))
        if card is None:                           # dropped on the first product, or evicted while it was read
            continue
        hold_b, acc_b = _Hold(), _new_acc()
        budget.begin(hold_b)
        try:
            cards.append(_card_target(ctx, run, card, acc_b))
        except _BudgetExceeded as exc:
            budget.end()
            budget.free(hold_b)
            run.drop(card, 'card_reserve_failed', 'target', exc)
            continue
        finally:
            budget.end()
        budget.free(hold_b)
        run.carried.pop((plan['pair_id'], plan['kind']), None)
        budget.free(card.hold_a)                   # the first product's part is no longer needed
        card.carried = None
        run.finish(card, acc_b)
    return cards


def _card_target(ctx, run, card, acc_b):
    """The target part of one card; returns its JSON. Everything it reserves is released before it returns."""
    plan, carried = card.plan, card.carried
    opts, corr = run.opts, run.corr
    info_a, info_b = run.infos['reference'], run.infos['target']
    tile_um = ctx.tile_px * info_b['pitch']
    tol_tiles = _ceil_tiles(opts['coord_tolerance_um'], tile_um)
    spread_px = opts['coord_offset_spread_um'] / info_a['pitch']
    side = _side_rows(ctx, run, plan)
    b_rows = side['rows']
    a_rows = carried['rows']
    ntx_a = carried['ntx']
    member_b, member_b_held = side['member'], 0
    if plan['match'] == 'matched':
        # the correspondence of a pair with a changed item set is measured on the items both products have (README 28.2)
        member_b, member_b_held = _common_member(ctx, run.budget, plan, side['member'])
    # the matching, the shifted copies, the partition check (concatenate / unique / isin / sort of the rows' tile ids)
    # hold up to 64 B per id of both products' rows and memberships at once: reserved before they run
    plan_temp = 64 * (sum(len(r['tiles']) for r in a_rows) + sum(len(r['tiles']) for r in b_rows) + len(member_b)
                      + sum(len(m) for m in carried['a_member']))
    run.budget.reserve(plan_temp, f'card matching {ctx.role} {plan["pair_id"]}')
    for arow in a_rows:
        arow['match'] = {'status': 'only', 'reason': arow['edge_reason']}
    confirmed = _NO_IDS
    moved_rows, rounding = [], []
    if plan['match'] != 'matched':
        row_reason = plan['reason'] or plan['match']
    elif not corr.ok:
        row_reason = corr.band
    elif run.coarse:
        row_reason = 'coords_resolution_too_coarse'
    else:
        row_reason = None
    for ia, arow in enumerate(a_rows):
        if arow['edge'] == 'excluded' or not len(arow['tiles']):
            continue
        if row_reason is not None:
            arow['match'] = {'status': 'only', 'reason': row_reason}
            continue
        x0, y0, x1, y1 = arow['fbox']
        if arow.get('image_bbox') is not None:          # a repeat row also stands on its image tiles (R2:1)
            ix0, iy0, ix1, iy1 = arow['image_bbox']
            x0, y0, x1, y1 = min(x0, ix0), min(y0, iy0), max(x1, ix1), max(y1, iy1)
        box_px = (x0 * ctx.tile_px, y0 * ctx.tile_px, x1 * ctx.tile_px, y1 * ctx.tile_px)
        offset, why = pick_offset(corr.cells, box_px, spread_px)
        if offset is None:
            arow['match'] = {'status': 'only', 'reason': why}
            continue
        sx, sy, err = _tile_shift(offset, info_a, info_b, tile_um)
        ty, tx = arow['tiles'] // ntx_a, arow['tiles'] % ntx_a
        ny, nx = ty + sy, tx + sx
        if (ny < 0).any() or (nx < 0).any() or (ny >= ctx.nty).any() or (nx >= ctx.ntx).any():
            arow['match'] = {'status': 'only', 'reason': 'outside_b_frame'}
            continue
        ids = np.sort(ny * ctx.ntx + nx)
        a_mem = carried['a_member'][ia]
        shifted = (a_mem // ntx_a + sy) * ctx.ntx + (a_mem % ntx_a + sx) if len(a_mem) else _NO_IDS
        keep = np.ones(len(shifted), dtype=bool)
        if len(shifted):
            sty, stx = shifted // ctx.ntx, shifted % ctx.ntx
            keep = (sty >= 0) & (sty < ctx.nty) & (stx >= 0) & (stx < ctx.ntx)
        ratio = _ratio_overlap(np.sort(shifted[keep]), member_b, ctx.ntx, ctx.nty, tol_tiles)
        if ratio < opts['coord_min_overlap'] - 1e-12:
            arow['match'] = {'status': 'only', 'reason': 'low_membership_overlap', 'overlap': ratio}
            continue
        if len(confirmed) and np.isin(ids, confirmed, assume_unique=True).any():
            arow['match'] = {'status': 'only', 'reason': 'b_conflict'}
            continue
        confirmed = np.union1d(confirmed, ids)
        arow['match'] = {'status': 'matched', 'reason': None, 'overlap': ratio, 'offset_px': list(offset),
                         'shift_tiles': [sx, sy], 'rounding_error_um': [err[0], err[1]]}
        rounding.append(max(abs(err[0]), abs(err[1])))
        moved_rows.append((ia, ids, (sx, sy)))
    # B rows: the matched copies first (A order), then the rest of the B lines without the matched part
    final_b, absorbed = [], []
    for ia, ids, shift in moved_rows:
        arow = a_rows[ia]
        box = _shift_box(arow['fbox'], shift[0], shift[1])
        final_b.append({'type': arow['type'], 'box': box, 'fbox': box, 'edge': 'none', 'edge_reason': None,
                        'tiles': ids, 'lost': 0, 'match': {'status': 'matched', 'reason': None, 'with_a': ia + 1}})
    n_matched = len(final_b)
    for jb, brow in enumerate(b_rows):
        if brow['edge'] == 'excluded':
            final_b.append(dict(brow, match={'status': 'only', 'reason': brow['edge_reason']}))
            continue
        rest = brow['tiles'][~np.isin(brow['tiles'], confirmed, assume_unique=True)] if len(confirmed) else brow['tiles']
        if not len(rest) and len(brow['tiles']):
            absorbed.append(jb + 1)
            continue
        final_b.append(dict(brow, tiles=rest, match={'status': 'only', 'reason': row_reason,
                                                     'partial_matched': len(rest) < len(brow['tiles'])}))
    final = _side_final(ctx, run, plan, final_b, 'target', acc_b)
    # check 5: the partition of both products
    acc_b['stats']['partition'] += 1
    t_ids = [r['tiles'] for r in final_b[:n_matched]]
    u_ids = [r['tiles'] for r in final_b[n_matched:]]
    s_b = [r['tiles'] for r in b_rows]
    all_b = np.concatenate(t_ids + u_ids) if (t_ids or u_ids) else _NO_IDS
    want_b = np.unique(np.concatenate(t_ids + s_b)) if (t_ids or s_b) else _NO_IDS
    a_live = [r['tiles'] for r in a_rows if r['edge'] != 'excluded']
    a_matched = [a_rows[ia]['tiles'] for ia, _i, _s in moved_rows]
    a_only = [r['tiles'] for k, r in enumerate(a_rows) if r['edge'] != 'excluded'
              and r['match']['status'] != 'matched']
    all_a = np.concatenate(a_matched + a_only) if (a_matched or a_only) else _NO_IDS
    want_a = np.concatenate(a_live) if a_live else _NO_IDS
    bad = []
    if len(np.unique(all_b)) != len(all_b) or not np.array_equal(np.unique(all_b), want_b):
        bad.append('target')
    if len(np.unique(all_a)) != len(all_a) or not np.array_equal(np.sort(all_a), np.sort(want_a)):
        bad.append('reference')
    if bad:
        acc_b['checks']['partition'].append({'card': f'{plan["kind"]}:{plan["pair_id"]}', 'sides': bad})
    out = _card_json(run, plan, carried, a_rows, final_b, final, side, absorbed, rounding, tile_um)
    run.budget.release(plan_temp, 'card matching')
    run.budget.release(member_b_held, 'common-item membership held')
    run.budget.release(side['held'], 'pair arrays held')
    return out


def _card_json(run, plan, carried, a_rows, final_b, final_side_b, side_b, absorbed, rounding, tile_um):
    """One card (README 22 `cards[]`): the rows of both products, the match state, the observed values."""
    info_a, info_b = run.infos['reference'], run.infos['target']
    rows = [_row_json(row, info_a, 'reference', f'A{i}', tile_um) for i, row in enumerate(a_rows, start=1)]
    for j, row in enumerate(final_b, start=1):
        rows.append(_row_json(row, info_b, 'target', f'B{j}', tile_um))
    b_of = {row['match']['with_a']: f'B{j}' for j, row in enumerate(final_b, start=1) if row['match'].get('with_a')}
    for row in rows:
        if row['side'] == 'reference' and row['match']['status'] == 'matched':
            row['match']['counterpart'] = b_of[int(row['id'][1:])]
        elif row['side'] == 'target' and row['match'].get('with_a'):
            row['match']['counterpart'] = f'A{row["match"]["with_a"]}'
    a_only_reasons = sorted({r['match']['reason'] for r in rows
                             if r['side'] == 'reference' and r['match']['status'] == 'only' and r['match']['reason']})
    n_matched = sum(1 for r in rows if r['side'] == 'reference' and r['match']['status'] == 'matched')
    status, reason = plan['match'], plan['reason']
    if status == 'matched' and not n_matched:
        status = 'not_compared'
        reason = run.corr.band or ('coords_resolution_too_coarse' if run.coarse else
                                   (','.join(a_only_reasons) or 'no_row_matched'))
    final_a = carried['final'] if carried else {'observed': 0.0, 'rows_sum': 0.0}
    others_a = carried['planned'] if carried else {'others_n': 0, 'others_value': 0.0}
    return {'kind': plan['kind'], 'pair_id': plan['pair_id'], 'rank': plan['rank'], 'delta': plan['delta'],
            'tabs': list(plan['tabs']), 'tab_ranks': dict(plan['tab_ranks']),
            'common_items': len(plan['common_ids']), 'items_total': plan['n_items'],
            'a': plan['a'], 'b': plan['b'], 'change_kind': plan['change_kind'], 'rows': rows,
            'match': {'status': status, 'reason': reason, 'matched_rows': n_matched,
                      'absorbed_b_rows': absorbed,
                      'not_listed': {'reference': {'n': others_a['others_n'], 'value': others_a['others_value']},
                                     'target': {'n': side_b['planned']['others_n'],
                                                'value': side_b['planned']['others_value']}}},
            'observed': {'reference': final_a['observed'], 'target': final_side_b['observed']},
            'section4_ref': {'reference': plan['a'], 'target': plan['b']},
            'rows_sum': {'reference': final_a['rows_sum'], 'target': final_side_b['rows_sum']},
            'rounding_error_um': max(rounding) if rounding else 0.0}


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
        grid_sum = _fsum_array(ctx.grids[kind]) if ctx.grids.get(kind) is not None else None
        if total is None or grid_sum is None:
            out[kind] = {'status': 'not_evaluated', 'sum': grid_sum, 'total': total}
        else:
            out[kind] = {'status': 'passed' if _within(grid_sum - total, total) else 'failed',
                         'sum': grid_sum, 'total': total}
    return out


def _process_side(role, info, cfg, cfg2, led_doc, doc_hash, options, budget, geom, report_diameter, warnings, run=None):
    """Read one product once and return (side json, files, window json, checks, ctx-free summary)."""
    from .pipeline import _configured_real
    budget.set_stage(role, 'read')
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
    ctx.run = run
    key = 'a' if role == 'reference' else 'b'

    # estimate of the unchanged compute_areas holding (real_hotspot.estimate_area_bytes, via the dataset admission)
    dataset = _configured_real(cfg, info['section'], info['real'], {'bucket_detail': 'summary', 'top_buckets': geom['top']},
                               require_distance=True)
    if dataset is None:
        raise NumericContractError('compare2_condition_assignment_required', f'{role}: condition_assignment needed')
    provenance = getattr(dataset[1], 'provenance', None) or {}
    current_hash = provenance.get('input_hash')
    estimate = int(((provenance.get('resource_estimate') or {}).get('estimated_peak_bytes')) or 0)
    if not estimate:
        # R2:6: without the estimate nothing is reserved for the compute_areas holding; the run goes on and says so
        warnings.append({'code': 'maps_resource_estimate_missing', 'role': role})
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
    budget.set_stage(role, 'merge')
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
            'tile_sum': None if rec['value'] is None else _fsum_array(values), 'has_area': len(member) > 0,
            'total_px': ctx.total_px.get(rec['item_id'], 0), 'value_tiles': int(len(tiles)),
            'membership_tiles': int(len(member))}

    # numbered pictures (item maps first, then the DL1 electrode maps)
    budget.set_stage(role, 'maps')
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
    budget.set_stage(role, 'cards')
    cards, carried = _cards(ctx)
    budget.reserve(carried, f'cards carried to the next product ({role})')   # not card-owned (0 unless a caller adds some)
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


def _card_check(run, name):
    """Checks 3 (range_sum), 4 (pair_bound), 5 (partition): how many rows/cards were checked and which failed."""
    failed, checked = run.checks[name], run.stats[name]
    status = 'failed' if failed else ('passed' if checked else 'not_evaluated')
    return {'status': status, 'checked': checked, 'failed': failed[:20], 'failed_count': len(failed)}


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

    corr = load_correspondence(candidates, infos, cfg, opts)
    run = _CardRun(led_doc, opts, budget, cfg2, infos, corr)
    budget.evictor = run.evict_one          # a reserve outside a card that does not fit first drops finished cards (E2)
    if cand.get('given'):
        cand.update({'apply_align': corr.mode, 'status': 'ok' if corr.ok else corr.band, 'detail': corr.detail})
    texts_files, sides_json, window_json, checks, cards, metas = [], {}, {}, {}, [], {}
    warnings, reasons = [], []
    for role in ROLES:
        side, files, side_checks, side_cards, meta = _process_side(
            role, infos[role], cfg, cfg2, led_doc, recorded_hash[role], opts, budget, geoms[role],
            report_diameter, warnings, run=run)
        sides_json[role] = side
        window_json[role] = geoms[role]['window']
        checks[role] = side_checks
        cards.extend(side_cards)
        metas[role] = meta
        texts_files.extend(files)
    failed = [role for role in ROLES
              if checks[role]['item_sum']['status'] == 'failed'
              or any(row['status'] == 'failed' for row in checks[role]['kind_sum'].values())]
    card_checks = {name: _card_check(run, name) for name in CARD_CHECKS}
    if any(row['status'] == 'failed' for row in card_checks.values()):
        failed.append('cards')
    dropped = sorted(run.dropped, key=lambda d: (KINDS.index(d['kind']), d['rank'], d['pair_id']))
    status = 'partial' if failed or dropped else 'ok'
    if failed:
        reasons.append('ledger_maps_value_check_failed')
    if dropped:
        # E2/E3: cards that did not fit are left out whole; maps, values and checks 1-2 stay (exit 3)
        reasons.append('cards_budget_reduced')
        run.bands.append({'code': 'cards_budget_reduced', 'count': len(dropped),
                          'detail': f'{len(dropped)} card(s) left out because of the memory budget '
                                    f'(max_operation_bytes {budget.limit}); see cards_dropped_for_budget'})
    summary = {'status': 'failed' if failed else 'passed',
               'item_sum': {role: checks[role]['item_sum'] for role in ROLES},
               'kind_sum': {role: checks[role]['kind_sum'] for role in ROLES},
               **card_checks,
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
        'cards_dropped_for_budget': dropped,
        'checks': summary,
        'scale_steps': scale_steps,
        'warnings': warnings, 'bands': run.bands}
    exit_code = 0 if status == 'ok' else 3
    texts_files.sort(key=lambda item: item[0])
    record = {'command': COMMAND, 'status': status, 'reasons': reasons,
              'outputs': [str(output / 'ledger_maps.json')] + [str(output / name) for name, _data in texts_files],
              'compare2_dir': str(compare2_dir), 'ledger_dir': str(ledger_dir), 'config': str(cfg2.config_path),
              'max_reserved_bytes': budget.peak, 'max_operation_bytes': budget.limit,
              # B3: the peak of the reservations in each stage of each product (where the 90% of a real run sits)
              'max_reserved_by_stage': {role: {stage: budget.stage_peak.get((role, stage), 0) for stage in STAGES}
                                        for role in ROLES},
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
