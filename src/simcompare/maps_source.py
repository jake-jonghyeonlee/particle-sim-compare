"""Record and replay the `compute_areas` share-sink calls of one product (pasx/T8b, `maps_src_v2`).

ledger-maps reads every product through `_load_side(..., share_sink=sink)`: a full manifest check plus one
`compute_areas` per product and pair. The sink calls only depend on the product, so `compare2-side --record-maps`
writes them once (next to side.json, inside the side generation) and `ledger-maps --maps-src` plays them back into the
same sink: same calls, same order, same lengths, same budget reservations, no mask read.

Folder `maps_src/` (written in this order, `index.json` last):
  calls.bin     zlib streams of the stored arrays (level 1, 64 MiB pieces, original dtype), one stream per array
  groups.jsonl  one line per distinct GroupKey (first-seen order): g, group_key
  items.jsonl   one line per distinct (item_key, item) (first-seen order): i, item_key, item
  calls.jsonl   one line per sink call: seq, g, i (references into the two tables), kind, frame_ref, share_len,
                geom_len and one {offset, nbytes_stored, dtype, length, crc32} entry per stored array
  frames.jsonl  one line per distinct frame: frame_ref, frame_id, shape_yx, has_valid, the valid array entry, sha256
  index.json    status, call count, byte sums, file sizes, sha256 of every line file (written while recording, checked
                while replaying: a changed character that keeps the JSON valid is still a replay failure),
                recording conditions, estimate, input hash, side.json sha256
The line files count against the byte cap as well (`line_bytes`).

Which arrays are stored (what the sink reads): `share_idx` and `share` only for a diameter inside the recorded
`lambda_diameters_um`, `geom_idx` only for the report diameter. Everything else is a length (`__len__` only).

Failure isolation (PLAN-v2.1 E): the recorder never fails the side computation. `open`, `call`, `close` and `abort`
catch `Exception`, set the state `failed` (reason cap_exceeded | disk_full | write_error | finalize_failed) and do not
raise again. KeyboardInterrupt and SystemExit pass. Whether a recording may be used is decided by the side's
`complete.json` `maps_src.status` (from the recorder's memory state), never by files found in the folder.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import time
import zlib
from decimal import Decimal
from pathlib import Path

import numpy as np

from .compare2_inputs import size_key
from .numeric_schema import NumericContractError

SCHEMA = 'maps_src_v2'
MAPS_BASIS = 'maps_src_v2'          # raise it when the sink contract or the recorded content changes
DIR_NAME = 'maps_src'
INDEX_FILE = 'index.json'
CALLS_BIN = 'calls.bin'
CALLS_JSONL = 'calls.jsonl'
FRAMES_JSONL = 'frames.jsonl'
GROUPS_JSONL = 'groups.jsonl'
ITEMS_JSONL = 'items.jsonl'
LINE_FILES = (CALLS_JSONL, FRAMES_JSONL, GROUPS_JSONL, ITEMS_JSONL)
DATA_FILES = (CALLS_BIN,) + LINE_FILES
DETAIL_MAX = 300
DEFAULT_MAX_BYTES = 32 * 1024 ** 3
MAX_CALLS = 1000000
MAX_ARRAY_ELEMENTS = 2 ** 31
PIECE_BYTES = 64 * 1024 * 1024
ZLIB_LEVEL = 1
READ_BLOCK = 1 << 20
REASONS = ('cap_exceeded', 'disk_full', 'write_error', 'finalize_failed')
REPLAY_FAILED = 'maps_src_replay_failed'
_GROUP_FIELDS = ('product_id', 'design_version', 'unit_id', 'source_id', 'process_step_id', 'exposure_id',
                 'analysis_layer', 'density_process_layer', 'source_contract_version', 'frame_id', 'diameter_um')


class ReplayFailed(NumericContractError):
    """A recording that cannot be read back (bad crc, truncated file, malformed line): the run ends without output."""

    def __init__(self, detail):
        super().__init__(REPLAY_FAILED, detail)


class StubAccessError(NumericContractError):
    """The sink touched an array that was recorded as a length only (a contract breach, never silent)."""

    def __init__(self, detail):
        super().__init__('maps_src_stub_access', detail)


class _CapExceeded(Exception):
    pass


def conditions_for(cfg2, top):
    """The recording conditions of a compare2 config: what decides which arrays the sink reads."""
    listed = cfg2.lambda_diameters_um
    return {'maps_basis': MAPS_BASIS, 'report_diameter_um': str(size_key(cfg2.report.diameter_um)),
            'lambda_diameters_um': None if listed is None else sorted(str(size_key(item)) for item in listed),
            'top_buckets': top}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(str(path), 'rb') as handle:
        for block in iter(lambda: handle.read(READ_BLOCK), b''):
            digest.update(block)
    return digest.hexdigest()


def _json_line(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


# ---- recorder ---------------------------------------------------------------------------------------------------

class MapsRecorder(object):
    """The share sink that writes `maps_src/`. Use `.sink` as `share_sink`, then `close(...)` (or `abort()`)."""

    def __init__(self, folder, conditions, max_bytes=DEFAULT_MAX_BYTES, max_calls=MAX_CALLS,
                 max_array_elements=MAX_ARRAY_ELEMENTS):
        self.folder = Path(folder)
        self.conditions = dict(conditions)
        self.max_bytes, self.max_calls, self.max_array = int(max_bytes), int(max_calls), int(max_array_elements)
        self.report_diameter = size_key(Decimal(self.conditions['report_diameter_um']))
        listed = self.conditions['lambda_diameters_um']
        self.subset = None if listed is None else set(size_key(Decimal(item)) for item in listed)
        self.status = 'new'                       # new | recording | failed | complete
        self.reason = None
        self.detail = None
        self.cleanup_errors = []
        self.calls = 0
        self.raw_bytes = 0
        self.stored_bytes = 0
        self.line_bytes = 0                       # bytes of the line files (they count against max_bytes too)
        self._bin = self._calls = self._frames = self._groups = self._items = None
        self._bin_pos = 0
        self._frame_ids = {}                      # id(frame) -> (frame_ref, frame): the frames stay alive in the dataset
        self._group_ids = {}                      # sha256 of the encoded group key -> g
        self._item_ids = {}                       # sha256 of the encoded (item_key, item) -> i
        self._digests = {name: hashlib.sha256() for name in LINE_FILES}

    # state -----------------------------------------------------------------------------------------------------
    @property
    def recording(self):
        return self.status == 'recording'

    def summary(self):
        """What goes into complete.json `maps_src`."""
        if self.status == 'complete':
            return {'status': 'complete', 'reason': None, 'calls': self.calls, 'raw_bytes': self.raw_bytes,
                    'stored_bytes': self.stored_bytes, 'line_bytes': self.line_bytes,
                    'conditions': dict(self.conditions)}
        return {'status': 'failed', 'reason': self.reason or 'write_error', 'calls': self.calls,
                'detail': (self.detail or '')[:DETAIL_MAX]}

    def _fail(self, exc, reason=None):
        if self.status == 'failed':
            return
        if reason is None:
            if isinstance(exc, _CapExceeded):
                reason = 'cap_exceeded'
            elif isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
                reason = 'disk_full'
            else:
                reason = 'write_error'
        self.status = 'failed'
        self.reason = reason
        self.detail = '%s: %s' % (type(exc).__name__, exc)

    # writing (the single place the tests inject disk errors into) -----------------------------------------------
    def _write(self, handle, data):
        handle.write(data)

    # lifecycle --------------------------------------------------------------------------------------------------
    def open(self):
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            self._bin = open(str(self.folder / CALLS_BIN), 'wb')
            self._calls = open(str(self.folder / CALLS_JSONL), 'wb')
            self._frames = open(str(self.folder / FRAMES_JSONL), 'wb')
            self._groups = open(str(self.folder / GROUPS_JSONL), 'wb')
            self._items = open(str(self.folder / ITEMS_JSONL), 'wb')
            self.status = 'recording'
        except Exception as exc:
            self._fail(exc)
            self._close_handles()
            self._cleanup()
        return self.recording

    def sink(self, group_key, kind, item_key, item, frame, geom_idx, share_idx, share):
        if self.status != 'recording':
            return
        try:
            self._record(group_key, kind, item_key, item, frame, geom_idx, share_idx, share)
        except Exception as exc:
            self._fail(exc)

    def _record(self, group_key, kind, item_key, item, frame, geom_idx, share_idx, share):
        from .compare2_side import encode
        if self.calls >= self.max_calls:
            raise _CapExceeded('calls %d' % self.calls)
        diameter = size_key(group_key.diameter_um)
        keep_share = self.subset is None or diameter in self.subset
        keep_geom = diameter == self.report_diameter
        line = {'seq': self.calls, 'g': self._group_ref(group_key, encode), 'i': self._item_ref(item_key, item, encode),
                'kind': encode(kind), 'frame_ref': self._frame_ref(frame),
                'share_len': int(len(share_idx)), 'geom_len': int(len(geom_idx))}
        if keep_share:
            line['share_idx'] = self._write_array(share_idx)
            line['share'] = self._write_array(share)
        if keep_geom:
            line['geom_idx'] = self._write_array(geom_idx)
        self._write_line(self._calls, CALLS_JSONL, _json_line(line))
        self.calls += 1

    def _write_line(self, handle, name, data):
        """One line of a line file: counted against the byte cap and into the file's running sha256."""
        if self.raw_bytes + self.line_bytes + len(data) > self.max_bytes:
            raise _CapExceeded('%d + %d line bytes' % (self.raw_bytes + self.line_bytes, len(data)))
        self._write(handle, data)
        self._digests[name].update(data)
        self.line_bytes += len(data)

    def _group_ref(self, group_key, encode):
        fields = {name: encode(getattr(group_key, name)) for name in _GROUP_FIELDS}
        data = _json_line(fields)
        key = hashlib.sha256(data).digest()
        ref = self._group_ids.get(key)
        if ref is None:
            ref = len(self._group_ids)
            self._write_line(self._groups, GROUPS_JSONL, _json_line({'g': ref, 'group_key': fields}))
            self._group_ids[key] = ref
        return ref

    def _item_ref(self, item_key, item, encode):
        encoded_key, encoded_item = encode(item_key), encode(item)
        key = hashlib.sha256(_json_line([encoded_key, encoded_item])).digest()
        ref = self._item_ids.get(key)
        if ref is None:
            ref = len(self._item_ids)
            self._write_line(self._items, ITEMS_JSONL,
                             _json_line({'i': ref, 'item_key': encoded_key, 'item': encoded_item}))
            self._item_ids[key] = ref
        return ref

    def _frame_ref(self, frame):
        known = self._frame_ids.get(id(frame))
        if known is not None:
            return known[0]
        ref = len(self._frame_ids)
        valid = getattr(frame, 'valid', None)
        entry = {'frame_ref': ref, 'frame_id': getattr(frame, 'frame_id', None),
                 'shape_yx': [int(n) for n in frame.shape_yx], 'has_valid': valid is not None, 'valid': None,
                 'sha256': None}
        if valid is not None:
            digest = hashlib.sha256()
            entry['valid'] = self._write_array(valid.reshape(-1), digest)
            entry['sha256'] = digest.hexdigest()
        self._write_line(self._frames, FRAMES_JSONL, _json_line(entry))
        self._frame_ids[id(frame)] = (ref, frame)
        return ref

    def _write_array(self, array, digest=None):
        if not isinstance(array, np.ndarray) or array.ndim != 1 or array.dtype.hasobject or array.dtype.fields:
            raise TypeError('only flat numeric arrays are stored')
        length = int(array.shape[0])
        if length > self.max_array:
            raise _CapExceeded('array of %d elements' % length)
        flat = np.ascontiguousarray(array)
        raw = int(flat.nbytes)
        if self.raw_bytes + self.line_bytes + raw > self.max_bytes:
            raise _CapExceeded('%d + %d bytes' % (self.raw_bytes + self.line_bytes, raw))
        octets = flat.view(np.uint8) if raw else flat.astype(np.uint8)
        compressor = zlib.compressobj(ZLIB_LEVEL)
        offset, stored, crc = self._bin_pos, 0, 0
        for start in range(0, raw, PIECE_BYTES):
            piece = octets[start:start + PIECE_BYTES]
            crc = zlib.crc32(piece, crc)
            if digest is not None:
                digest.update(piece)
            data = compressor.compress(piece)
            if data:
                self._write(self._bin, data)
                stored += len(data)
        data = compressor.flush()
        if data:
            self._write(self._bin, data)
            stored += len(data)
        self._bin_pos += stored
        self.raw_bytes += raw
        self.stored_bytes += stored
        return {'offset': offset, 'nbytes_stored': stored, 'dtype': flat.dtype.str, 'length': length,
                'crc32': crc & 0xffffffff}

    def _close_handles(self):
        """Close every handle, one at a time. Returns the first exception (a close that fails fails the recording)."""
        first = None
        for name in ('_bin', '_calls', '_frames', '_groups', '_items'):
            handle = getattr(self, name)
            setattr(self, name, None)
            if handle is None:
                continue
            try:
                handle.close()
            except Exception as exc:
                first = first or exc
        return first

    def _flush_all(self):
        for handle in (self._bin, self._calls, self._frames, self._groups, self._items):
            handle.flush()
            os.fsync(handle.fileno())

    def close(self, finalize=None):
        """End of the recording. `finalize()` -> dict of index fields known only now (estimate, input hash, side sha).
        Returns `summary()`; never raises (KeyboardInterrupt and SystemExit excepted)."""
        if self.status == 'recording':
            try:
                self._flush_all()
            except Exception as exc:                  # a full disk while flushing is still a full disk (R8:5)
                self._fail(exc, 'disk_full' if isinstance(exc, OSError) and exc.errno == errno.ENOSPC
                           else 'finalize_failed')
        closed = self._close_handles()
        if closed is not None and self.status == 'recording':
            self._fail(closed, 'finalize_failed')
        if self.status == 'recording':
            try:
                extra = dict(finalize() if finalize is not None else {})
                self._write_index(extra)
                self.status = 'complete'
            except Exception as exc:
                self._fail(exc, 'finalize_failed')
        if self.status == 'failed':
            self._cleanup()
            self._write_failed_index()
        return self.summary()

    def abort(self):
        """The side computation ended with an error: close and clean up. Never raises."""
        try:
            if self.status in ('new', 'recording'):
                self._fail(RuntimeError('aborted'), 'write_error')
                self.detail = 'aborted'
            self._close_handles()
            if self.status != 'complete':        # a finished recording is left as it is (its staging is swept later)
                self._cleanup()
        except Exception as exc:
            self.cleanup_errors.append('abort: %s: %s' % (type(exc).__name__, exc))

    # index and cleanup -----------------------------------------------------------------------------------------
    def _write_index(self, extra):
        sizes = {name: (self.folder / name).stat().st_size for name in DATA_FILES}
        if sizes[CALLS_BIN] != self._bin_pos or sizes[CALLS_BIN] != self.stored_bytes:
            raise OSError('calls.bin is %d bytes, %d were written' % (sizes[CALLS_BIN], self._bin_pos))
        index = {'schema_version': SCHEMA, 'status': 'complete', 'calls': self.calls, 'frames': len(self._frame_ids),
                 'raw_bytes': self.raw_bytes, 'stored_bytes': self.stored_bytes, 'line_bytes': self.line_bytes,
                 'files': sizes, 'sha256': {name: self._digests[name].hexdigest() for name in LINE_FILES},
                 'groups': len(self._group_ids), 'items': len(self._item_ids), 'conditions': self.conditions}
        if sum(sizes[name] for name in LINE_FILES) != self.line_bytes:
            raise OSError('the line files are %d bytes, %d were written'
                          % (sum(sizes[name] for name in LINE_FILES), self.line_bytes))
        index.update(extra)
        self._publish_index(index)

    def _publish_index(self, index):
        target = self.folder / INDEX_FILE
        temp = self.folder / ('.%s.tmp' % INDEX_FILE)
        try:
            with open(str(temp), 'wb') as handle:
                self._write(handle, json.dumps(index, ensure_ascii=False, sort_keys=True, indent=1).encode('utf-8'))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temp), str(target))
        except BaseException:
            try:
                temp.unlink()
            except OSError:
                pass
            raise

    def _cleanup(self):
        """Empty the folder of recording files; every step is tried on its own, a failure is only noted."""
        for name in DATA_FILES + (INDEX_FILE, '.%s.tmp' % INDEX_FILE):
            try:
                (self.folder / name).unlink()
            except FileNotFoundError:
                pass
            except Exception as exc:
                self.cleanup_errors.append('%s: %s: %s' % (name, type(exc).__name__, exc))

    def _write_failed_index(self):
        try:
            self._publish_index({'schema_version': SCHEMA, 'status': 'failed', 'reason': self.reason,
                                 'detail': self.detail, 'calls': self.calls})
        except Exception as exc:
            self.cleanup_errors.append('index: %s: %s' % (type(exc).__name__, exc))


# ---- replay -----------------------------------------------------------------------------------------------------

class LengthOnly(object):
    """Stands in for an array that was recorded as a length: `len()` only, any other access is an error (F4)."""
    __slots__ = ('_n',)

    def __init__(self, n):
        object.__setattr__(self, '_n', int(n))

    def __len__(self):
        return self._n

    def __getattr__(self, name):
        raise StubAccessError('the recording holds only the length of this array; attribute %r was read' % name)

    def __getitem__(self, key):
        raise StubAccessError('the recording holds only the length of this array; it was indexed')

    def __iter__(self):
        raise StubAccessError('the recording holds only the length of this array; it was iterated')

    def __array__(self, *args, **kwargs):
        raise StubAccessError('the recording holds only the length of this array; it was converted to an array')


class _FrameStub(object):
    """A frame with `shape_yx` only (no valid mask recorded)."""

    def __init__(self, shape_yx):
        self.shape_yx = tuple(shape_yx)


class _FrameWithValid(_FrameStub):
    """A frame with `valid`, read from the recording each time it is asked for (the ledger-maps sink asks once)."""

    def __init__(self, shape_yx, loader):
        super().__init__(shape_yx)
        self._loader = loader

    @property
    def valid(self):
        return self._loader()


def _fail(detail):
    raise ReplayFailed(detail)


def _int(value, what, low=0):
    if type(value) is not int or value < low:
        _fail('%s is not an integer >= %d' % (what, low))
    return value


class MapsSource(object):
    """A structurally checked recording of one side generation."""

    def __init__(self, side_dir, index):
        self.side_dir = Path(side_dir)
        self.folder = self.side_dir / DIR_NAME
        self.index = index

    # conditions ------------------------------------------------------------------------------------------------
    def reason_against(self, conditions, input_hash):
        """None when the recording may serve the current run, else a short reason (the run then computes directly)."""
        recorded = self.index.get('conditions') or {}
        for key in sorted(set(recorded) | set(conditions)):
            if recorded.get(key) != conditions.get(key):
                return 'conditions_changed:%s' % key
        if input_hash is None or self.index.get('input_hash') != input_hash:
            return 'input_hash_changed'
        return None

    # replay ----------------------------------------------------------------------------------------------------
    def replay(self, sink):
        """Call `sink` for every recorded call, in order. Returns (meta, seconds spent reading). A read problem raises
        ReplayFailed; an exception of the sink itself passes through unchanged."""
        from .compare2_side import decode
        from .linepixel import GroupKey
        read_s = 0.0
        index = self.index
        total = _int(index.get('calls'), 'calls')
        try:
            binary = open(str(self.folder / CALLS_BIN), 'rb')
        except OSError as exc:
            _fail('%s: %s' % (CALLS_BIN, exc))
        with binary:
            t0 = time.perf_counter()
            frames = self._load_frames(binary)
            groups = self._load_table(GROUPS_JSONL, 'g', 'group_key')
            items = self._load_table(ITEMS_JSONL, 'i', None)
            expected = self._expected_hash(CALLS_JSONL)
            try:                                              # R8b:2: a damaged calls.jsonl is found before the sink sees a call
                if sha256_file(self.folder / CALLS_JSONL) != expected:
                    _fail('%s: sha256 mismatch' % CALLS_JSONL)
            except OSError as exc:
                _fail('%s: %s' % (CALLS_JSONL, exc))
            digest = hashlib.sha256()
            read_s += time.perf_counter() - t0
            try:
                lines = open(str(self.folder / CALLS_JSONL), 'rb')
            except OSError as exc:
                _fail('%s: %s' % (CALLS_JSONL, exc))
            with lines:
                done = 0
                while True:
                    t0 = time.perf_counter()
                    raw = lines.readline()
                    if not raw:
                        read_s += time.perf_counter() - t0
                        break
                    digest.update(raw)
                    try:
                        call = self._parse_call(raw, done, binary, frames, groups, items, decode, GroupKey)
                    except ReplayFailed:
                        raise
                    except Exception as exc:
                        _fail('call %d: %s: %s' % (done, type(exc).__name__, exc))
                    read_s += time.perf_counter() - t0
                    sink(**call)
                    done += 1
        if digest.hexdigest() != expected:
            _fail('%s: sha256 mismatch' % CALLS_JSONL)
        if done != total:
            _fail('%d calls were read, the index says %d' % (done, total))
        return {'input_hash': index.get('input_hash')}, read_s

    def _expected_hash(self, name):
        expected = (self.index.get('sha256') or {}).get(name)
        if not isinstance(expected, str):
            _fail('%s: the index holds no sha256' % name)
        return expected

    def _hashed_lines(self, name):
        """(number, raw line) of a line file; at its end the running sha256 must equal the index's."""
        expected = self._expected_hash(name)
        try:
            handle = open(str(self.folder / name), 'rb')
        except OSError as exc:
            _fail('%s: %s' % (name, exc))
        digest = hashlib.sha256()
        with handle:
            for number, raw in enumerate(handle):
                digest.update(raw)
                if not raw.endswith(b'\n'):
                    _fail('%s line %d is truncated' % (name, number))
                yield number, raw
        if digest.hexdigest() != expected:
            _fail('%s: sha256 mismatch' % name)

    def _load_table(self, name, ref_key, value_key):
        """The still-encoded rows of groups.jsonl (group_key fields) or items.jsonl ((item_key, item)), by reference."""
        rows = []
        for number, raw in self._hashed_lines(name):
            try:
                entry = json.loads(raw.decode('utf-8'))
                if entry[ref_key] != number:
                    _fail('%s line %d: reference mismatch' % (name, number))
                rows.append(entry[value_key] if value_key else (entry['item_key'], entry['item']))
            except ReplayFailed:
                raise
            except Exception as exc:
                _fail('%s line %d: %s: %s' % (name, number, type(exc).__name__, exc))
        return rows

    def _parse_call(self, raw, seq, binary, frames, groups, items, decode, group_key_type):
        if not raw.endswith(b'\n'):
            _fail('call %d: line is truncated' % seq)
        line = json.loads(raw.decode('utf-8'))
        if type(line) is not dict or line.get('seq') != seq:
            _fail('call %d: seq mismatch' % seq)
        g, i = _int(line['g'], 'g'), _int(line['i'], 'i')
        if g >= len(groups) or i >= len(items):
            _fail('call %d: group or item reference is unknown' % seq)
        fields = groups[g]
        group_key = group_key_type(**{name: decode(fields[name]) for name in _GROUP_FIELDS})
        item_key, item = items[i]
        ref = _int(line['frame_ref'], 'frame_ref')
        if ref >= len(frames):
            _fail('call %d: frame_ref %d is unknown' % (seq, ref))

        def column(name, length_key):
            length = _int(line[length_key], length_key)
            if name in line:
                array = self._read_array(binary, line[name])
                if len(array) != length:
                    _fail('call %d: %s has %d elements, %s says %d' % (seq, name, len(array), length_key, length))
                return array
            return LengthOnly(length)
        return {'group_key': group_key, 'kind': decode(line['kind']), 'item_key': decode(item_key),
                'item': decode(item), 'frame': frames[ref], 'geom_idx': column('geom_idx', 'geom_len'),
                'share_idx': column('share_idx', 'share_len'), 'share': column('share', 'share_len')}

    def _load_frames(self, binary):
        frames = []
        for number, raw in self._hashed_lines(FRAMES_JSONL):
            try:
                entry = json.loads(raw.decode('utf-8'))
                if entry['frame_ref'] != number:
                    _fail('frames line %d: frame_ref mismatch' % number)
                shape = tuple(_int(n, 'shape_yx', 1) for n in entry['shape_yx'])
                if entry['has_valid']:
                    frames.append(_FrameWithValid(shape, self._valid_loader(binary, entry, shape)))
                else:
                    frames.append(_FrameStub(shape))
            except ReplayFailed:
                raise
            except Exception as exc:
                _fail('frames line %d: %s: %s' % (number, type(exc).__name__, exc))
        return frames

    def _valid_loader(self, binary, entry, shape):
        def load():
            with open(str(self.folder / CALLS_BIN), 'rb') as handle:        # its own handle: the replay loop keeps moving
                array = self._read_array(handle, entry['valid'])
            if hashlib.sha256(array.view(np.uint8)).hexdigest() != entry['sha256']:
                _fail('frame %s: sha256 mismatch' % entry['frame_ref'])
            if array.dtype != np.dtype(bool) or int(array.shape[0]) != shape[0] * shape[1]:
                _fail('frame %s: valid has the wrong type or size' % entry['frame_ref'])
            return array.reshape(shape)
        return load

    def _read_array(self, binary, entry):
        """One stored array: stream-decompress into a fresh writable array, check length, size and crc32."""
        try:
            offset, stored = _int(entry['offset'], 'offset'), _int(entry['nbytes_stored'], 'nbytes_stored')
            length, crc = _int(entry['length'], 'length'), _int(entry['crc32'], 'crc32')
            dtype = np.dtype(entry['dtype'])
        except (KeyError, TypeError) as exc:
            _fail('array entry: %s: %s' % (type(exc).__name__, exc))
        if dtype.kind not in 'biuf' or dtype.itemsize > 8:
            _fail('array dtype %s is not allowed' % dtype.str)
        array = np.empty(length, dtype=dtype)
        target = array.view(np.uint8) if length else np.empty(0, dtype=np.uint8)
        size = int(target.shape[0])
        decompressor = zlib.decompressobj()
        position, running, remaining = 0, 0, stored
        binary.seek(offset)

        def emit(chunk):
            nonlocal position, running
            if not chunk:
                return
            if position + len(chunk) > size:
                _fail('array holds more bytes than its length')
            target[position:position + len(chunk)] = np.frombuffer(chunk, dtype=np.uint8)
            running = zlib.crc32(chunk, running)
            position += len(chunk)
        try:
            while remaining:
                block = binary.read(min(READ_BLOCK, remaining))
                if not block:
                    _fail('calls.bin is truncated')
                remaining -= len(block)
                pending = block
                while True:
                    out = decompressor.decompress(pending, PIECE_BYTES)
                    pending = decompressor.unconsumed_tail
                    emit(out)
                    if len(out) < PIECE_BYTES and not pending:
                        break
            while True:
                out = decompressor.decompress(b'', PIECE_BYTES)
                emit(out)
                if len(out) < PIECE_BYTES:
                    break
        except zlib.error as exc:
            _fail('zlib: %s' % exc)
        if not decompressor.eof or decompressor.unused_data or position != size:
            _fail('array stream is incomplete or has the wrong size')
        if (running & 0xffffffff) != crc:
            _fail('crc32 mismatch')
        return array


def load_source(side_dir):
    """(MapsSource, None) for a side generation whose `complete.json` says `maps_src.status == complete` and whose
    recording files match the index; else (None, reason). Reads small files and stats; the arrays are not touched."""
    side_dir = Path(side_dir)
    try:
        complete = json.loads((side_dir / 'complete.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None, 'complete_unreadable'
    if not isinstance(complete, dict):
        return None, 'complete_unreadable'
    status = (complete.get('maps_src') or {}).get('status') if isinstance(complete.get('maps_src'), dict) else None
    if status != 'complete':
        return None, 'not_recorded:%s' % status
    entries = {entry.get('path'): entry for entry in complete.get('files') or [] if isinstance(entry, dict)}
    listed = entries.get('%s/%s' % (DIR_NAME, INDEX_FILE))
    side_entry = entries.get('side.json')
    folder = side_dir / DIR_NAME
    try:
        if listed is None or side_entry is None:
            return None, 'index_not_listed'
        if sha256_file(folder / INDEX_FILE) != listed.get('sha256'):
            return None, 'index_changed'
        index = json.loads((folder / INDEX_FILE).read_text(encoding='utf-8'))
        if (not isinstance(index, dict) or index.get('schema_version') != SCHEMA or index.get('status') != 'complete'):
            return None, 'index_not_complete'
        if index.get('side_sha256') != side_entry.get('sha256') or sha256_file(side_dir / 'side.json') != side_entry.get('sha256'):
            return None, 'side_changed'
        sizes = index.get('files') or {}
        for name in DATA_FILES:
            if (folder / name).stat().st_size != sizes.get(name):
                return None, 'file_size_mismatch:%s' % name
        if sizes.get(CALLS_BIN) != index.get('stored_bytes'):
            return None, 'file_size_mismatch:stored_bytes'
    except (OSError, ValueError):
        return None, 'index_unreadable'
    return MapsSource(side_dir, index), None
