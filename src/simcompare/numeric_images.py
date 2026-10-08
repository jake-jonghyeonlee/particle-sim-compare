"""Strict binary image IO with root-contained relative paths, no image transforms."""
from pathlib import Path, PureWindowsPath
import hashlib

import numpy as np
from PIL import Image

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


def read_binary(root: Path, relative_path: str, *, sha256: str | None = None,
                value_semantics: str = 'binary') -> np.ndarray:
    if value_semantics != 'binary':
        raise NumericContractError('unsupported_mask_semantics', value_semantics)
    path = resolve_path(root, relative_path)
    if sha256 is not None:
        check_hash(path, sha256)
    try:
        if path.suffix.lower() == '.npy':
            value = np.load(path, allow_pickle=False)
        elif path.suffix.lower() == '.png':
            with Image.open(path) as image:
                # Palette/RGB/alpha need a separate explicit contract; never convert/threshold.
                if image.mode not in ('1', 'L', 'I', 'I;16'):
                    raise NumericContractError('unsupported_png_mode', image.mode)
                value = np.asarray(image)
        else:
            raise NumericContractError('unsupported_image_format', path.suffix)
    except (OSError, ValueError, TypeError) as exc:
        if isinstance(exc, NumericContractError):
            raise
        raise NumericContractError('image_read_failed', relative_path) from exc
    return binary_mask(value)
