"""Strict binary image IO with root-contained relative paths, no image transforms.

PNG supports opaque exact 1/L/RGB/LA/RGBA/P after structural validation; 8-bit
L keeps exact 0/1 as well as 0/255. I/I;16 (16-bit PNG) support is intentionally
removed: png_strict rejects depth 16 before decoding. NPY reading is unchanged.
"""
from __future__ import annotations

from pathlib import Path, PureWindowsPath
import hashlib

import numpy as np

from .numeric_schema import NumericContractError, NumericPathError, binary_mask


def resolve_path(root: Path, relative_path: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise NumericPathError('invalid_asset_path')
    path = Path(relative_path)
    windows = PureWindowsPath(relative_path)
    if path.is_absolute() or windows.is_absolute() or windows.drive or '\\' in relative_path:
        raise NumericPathError('absolute_or_nonportable_path', relative_path)
    if '..' in path.parts:
        raise NumericPathError('relative_path_escape', relative_path)
    try:
        resolved = (Path(root) / path).resolve(strict=True)
        resolved.relative_to(Path(root).resolve(strict=True))
    except (ValueError, OSError, RuntimeError) as exc:
        raise NumericPathError('missing_or_escaping_path', relative_path) from exc
    if not resolved.is_file():
        raise NumericPathError('asset_not_file', relative_path)
    return resolved


def check_hash(path: Path, expected: str) -> None:
    if not isinstance(expected, str) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise NumericContractError('input_hash_mismatch', path.name)


# 'binary': nonzero is the marked region. 'binary_zero_is_marked': the source draws the
# marked region black. Real hotspot PNGs are black background / white pattern, so they use
# 'binary' (user, 2026-10-13; supersedes the 2026-10-09 note that the hotspot is black).
MASK_SEMANTICS = ('binary', 'binary_zero_is_marked')


def read_binary(root: Path, relative_path: str, *, sha256: str | None = None,
                value_semantics: str | None = None, synthetic: bool = True) -> np.ndarray:
    png_detail = relative_path if isinstance(relative_path, str) and Path(relative_path).suffix.lower() == '.png' else ''
    if value_semantics is None:
        if not synthetic:
            raise NumericContractError('explicit_value_semantics_required', png_detail)
        value_semantics = 'binary'
    if value_semantics not in MASK_SEMANTICS:
        raise NumericContractError('unsupported_mask_semantics', png_detail+': '+str(value_semantics) if png_detail else value_semantics)
    path = resolve_path(root, relative_path)
    if sha256 is not None and path.suffix.lower() != '.png':
        check_hash(path, sha256)
    try:
        if path.suffix.lower() == '.npy':
            value = np.load(path, allow_pickle=False)
        elif path.suffix.lower() == '.png':
            from .png_strict import read_png
            value = read_png(path, relative_path, sha256=sha256)
        else:
            raise NumericContractError('unsupported_image_format', path.suffix)
    except (OSError, ValueError, TypeError) as exc:
        if isinstance(exc, NumericContractError):
            raise
        raise NumericContractError('image_read_failed', relative_path) from exc
    mask = binary_mask(value)
    if value_semantics == 'binary_zero_is_marked':
        mask = ~mask
        mask.setflags(write=False)
    return mask
