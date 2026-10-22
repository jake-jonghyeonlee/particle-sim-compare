"""Packed per-group masks of a compare run, reused by compare-windows (Phase B, Q3).

`compare` (real path) publishes ``comparison_masks.zip`` next to comparison.json/csv, inside the same publication
transaction (pipeline._ComparisonStream). It holds, per side and per layer x diameter group, exactly what
compare-windows reads from an AreaResult: the group key, the union and failure-piece AreaMetrics, the fractions and
the packed union / failure-piece masks (np.packbits, 1 bit per cell). Bucket lists, source cases and the line / pixel
masks are not stored.

compare-windows reuses the file only when everything it depends on matches (schema, engine tag, each model's
``input_hash``, ``is_synthetic`` and the frame it was computed on, every mask's length and sha256, no publication in
progress). In every other case (absent, stale, corrupt, half-published) it returns None and the caller recomputes
`compute_areas`, which gives the same numbers: reuse must never be the only way to get a result.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import zipfile

import numpy as np

from .linepixel import FAILURE_PIECES, AreaMetric, Coverage, FractionMetric, GroupKey, PackedMask, _PieceMasks

SCHEMA = 'compare_masks_v1'
# Bump when compute_areas changes any mask, AreaMetric or FractionMetric it produces: a file written by another engine
# version is then ignored (recomputed) instead of reused.
AREA_ENGINE_TAG = 'linepixel_area_v1/compare_masks_1'
ARTEFACT_NAME = 'comparison_masks.zip'
MAX_META_BYTES = 256 * 1024 * 1024
MASK_NAMES = ('union', *FAILURE_PIECES)
# files of the compare publication protocol: while one of them exists the directory may be mid-publication
_IN_PROGRESS = ('.comparison.publishing', '.comparison.publishing.partial', '.comparison.committed',
                '.comparison.committed.partial')


def _plain(value):
    """json default: numpy scalars become Python numbers; anything else is a bug, not something to stringify."""
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f'{type(value).__name__} is not JSON serialisable')


def frame_descriptor(frame) -> dict:
    return {'frame_id': frame.frame_id, 'pixel_pitch_um': frame.pixel_pitch_um, 'origin_xy_um': list(frame.origin_xy_um),
            'shape_yx': list(frame.shape_yx), 'axis_order': frame.axis_order,
            'origin_convention': frame.origin_convention, 'valid_hash': frame.valid_hash}


def _member(name):
    """Zip member with a fixed timestamp: writestr(name) would stamp the wall clock, so two identical compares could differ
    by a second in the archive bytes."""
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = 0o600 << 16
    return info


class MasksWriter:
    """Streams the packed masks of every group into a stored (uncompressed) zip; meta.json is the last member."""

    def __init__(self, handle):
        self._zip = zipfile.ZipFile(handle, 'w', zipfile.ZIP_STORED, allowZip64=True)
        self._sides = {}

    def add_side(self, name, result):
        if result is None:
            self._sides[name] = None
            return
        groups, frames = [], {}
        for number, (key, group) in enumerate(result.groups.items()):
            frames.setdefault(key.frame_id, frame_descriptor(group.frame))
            masks = {}
            for mask_name in MASK_NAMES:
                packed = group.union_packed if mask_name == 'union' else group.failure_piece_packed[mask_name]
                bits = np.ascontiguousarray(packed.bits, dtype=np.uint8).tobytes()
                self._zip.writestr(_member(f'masks/{name}/{number}/{mask_name}'), bits)
                masks[mask_name] = {'shape': list(packed.shape), 'bytes': len(bits), 'sha256': hashlib.sha256(bits).hexdigest()}
            groups.append({'key': asdict(key), 'union': asdict(group.union),
                           'failure_pieces': {n: asdict(group.failure_pieces[n]) for n in FAILURE_PIECES},
                           'fractions': {n: asdict(m) for n, m in group.fractions.items()}, 'masks': masks})
        self._sides[name] = {'input_hash': result.input_hash, 'is_synthetic': bool(result.is_synthetic),
                             'frames': frames, 'groups': groups}

    def abandon(self):
        """Drop an unfinished archive without writing its central directory (the caller closes and deletes the file)."""
        self._zip.fp = None

    def close(self):
        meta = {'schema_version': SCHEMA, 'engine': AREA_ENGINE_TAG, 'sides': self._sides}
        self._zip.writestr(_member('meta.json'), json.dumps(meta, ensure_ascii=False, allow_nan=False, separators=(',', ':'), default=_plain))
        self._zip.close()


@dataclass
class ReusedGroup:
    """The part of a GroupArea compare-windows and its page read; masks unpack to fresh read-only arrays."""
    key: GroupKey
    frame: object
    union: AreaMetric
    failure_pieces: dict
    fractions: dict
    union_packed: PackedMask
    failure_piece_packed: dict

    @property
    def union_mask(self):
        return self.union_packed.unpack()

    @property
    def failure_piece_masks(self):
        return _PieceMasks(self.failure_piece_packed)

    @property
    def frame_area_um2(self) -> float:
        return self.frame.valid.size * self.frame.pixel_pitch_um ** 2


@dataclass
class ReusedAreaResult:
    groups: dict
    input_hash: str | None
    is_synthetic: bool = False
    reused_from: str | None = None


def _metric(raw) -> AreaMetric:
    return AreaMetric(**{**raw, 'reasons': tuple(raw['reasons']), 'coverage': Coverage(**raw['coverage'])})


def _fraction(raw) -> FractionMetric:
    return FractionMetric(**{**raw, 'reasons': tuple(raw['reasons'])})


def publication_in_progress(output) -> bool:
    output = Path(output)
    return any(os.path.lexists(str(output / name)) for name in _IN_PROGRESS)


def _check_frame(dataset, frame_id, descriptor):
    frame = dataset.frames.get(frame_id)
    if frame is None or frame_descriptor(frame) != descriptor:
        raise ValueError('frame_mismatch')
    return frame


def _side_result(archive, name, side, dataset, bytes_limit):
    if side is None or dataset is None or not dataset.cases:
        raise ValueError('side_not_stored')
    expected_hash = dataset.provenance.get('input_hash')
    if expected_hash is None or side['input_hash'] != expected_hash:
        raise ValueError('input_hash_mismatch')
    if side['is_synthetic'] != bool(dataset.provenance.get('is_synthetic', False)):
        raise ValueError('synthetic_flag_mismatch')
    frames = {fid: _check_frame(dataset, fid, desc) for fid, desc in side['frames'].items()}
    groups, total = {}, 0
    for number, raw in enumerate(side['groups']):
        key = GroupKey(**raw['key'])
        frame = frames[key.frame_id]
        packed = {}
        for mask_name in MASK_NAMES:
            entry = raw['masks'][mask_name]
            shape = tuple(entry['shape'])
            if shape != frame.shape_yx:
                raise ValueError('mask_shape_mismatch')
            total += entry['bytes']
            if total > bytes_limit:
                raise ValueError('artefact_exceeds_budget')
            data = archive.read(f'masks/{name}/{number}/{mask_name}')   # zipfile verifies the member's CRC-32
            if len(data) != entry['bytes'] or len(data) != (frame.valid.size + 7) // 8 \
                    or hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise ValueError('mask_digest_mismatch')
            packed[mask_name] = PackedMask(shape, np.frombuffer(data, dtype=np.uint8))
        if key in groups:
            raise ValueError('duplicate_group')
        groups[key] = ReusedGroup(key, frame, _metric(raw['union']),
                                  {n: _metric(raw['failure_pieces'][n]) for n in FAILURE_PIECES},
                                  {n: _fraction(m) for n, m in raw['fractions'].items()},
                                  packed['union'], {n: packed[n] for n in FAILURE_PIECES})
    return ReusedAreaResult(groups, side['input_hash'], side['is_synthetic'], ARTEFACT_NAME)


def artefact_bytes(output) -> int | None:
    try:
        return os.stat(str(Path(output) / ARTEFACT_NAME)).st_size
    except OSError:
        return None


def load_compare_masks(output, reference, target, *, max_bytes=None):
    """(areas_reference, areas_target, None) when the artefact in ``output`` matches both datasets, else (None, None, reason).

    Never raises for an unusable file: a missing, stale, truncated, tampered or half-published artefact returns a short
    reason and the caller recomputes. ``max_bytes`` bounds what is read (the packed masks of both sides)."""
    output = Path(output)
    try:
        if publication_in_progress(output):
            return None, None, 'publication_in_progress'
        path = output / ARTEFACT_NAME
        if not path.is_file():
            return None, None, 'absent'
        limit = max_bytes if max_bytes is not None else os.stat(str(path)).st_size
        with zipfile.ZipFile(str(path), 'r') as archive:
            info = archive.getinfo('meta.json')
            if info.file_size > MAX_META_BYTES:
                raise ValueError('meta_too_large')
            meta = json.loads(archive.read('meta.json').decode('utf-8'))
            if meta.get('schema_version') != SCHEMA or meta.get('engine') != AREA_ENGINE_TAG:
                return None, None, 'version_mismatch'
            sides = meta['sides']
            results = [_side_result(archive, name, sides.get(name), dataset, limit)
                       for name, dataset in (('reference', reference), ('target', target))]
        return results[0], results[1], None
    except (OSError, ValueError, KeyError, TypeError, EOFError, zipfile.BadZipFile) as exc:
        return None, None, f'unusable:{type(exc).__name__}:{exc}'
