"""Strict numeric PNG decoding from one immutable compressed snapshot.

Structure/scanline validation ported from psdl1/png_source.py at
DL1 commit e67a8b2a3719d7141472a32503d00f52bbdf625c (read-only reference).
The validator keeps bounded inflate blocks and two packed rows, never a full
inflated stream. Pillow expands pixels only after independent validation.
Modes 1/L retain the exact 0/1 or 0/255 binary_mask contract; other accepted
modes require opaque exact black/white pixels, with no threshold.
Modes I and I;16 from 16-bit PNG are deliberately no longer supported: depth
16 is rejected before Pillow can downconvert. NPY integer support is unchanged.
"""
from contextlib import contextmanager
from io import BytesIO
import hashlib
import struct
import threading
import warnings
import zlib

import numpy as np
from PIL import Image, ImageFile

from .numeric_schema import NumericContractError, binary_mask

_DECODER_LOCK = threading.RLock()


class _PNGError(ValueError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__(detail)


def _check_pixel_limit(width, height, limit):
    pixels = width * height
    if pixels > 2 * limit:
        raise _PNGError('png_pixel_limit_error', 'image exceeds twice installed Pillow pixel limit')
    if pixels > limit:
        raise _PNGError('png_pixel_limit_warning', 'image exceeds installed Pillow pixel limit')


@contextmanager
def decoder_session():
    """Keep installed pixel limit; disallow truncated loads; restore global state."""
    with _DECODER_LOCK:
        limit = Image.MAX_IMAGE_PIXELS
        truncated = ImageFile.LOAD_TRUNCATED_IMAGES
        if type(limit) is not int or limit <= 0:
            raise _PNGError('png_pixel_limit_disabled', 'installed Pillow pixel limit is disabled/invalid')
        try:
            ImageFile.LOAD_TRUNCATED_IMAGES = False
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                yield limit
        finally:
            ImageFile.LOAD_TRUNCATED_IMAGES = truncated


class _PNGScanlines:
    """Stream inflated bytes into scanlines; retain at most two packed rows.

    Palette rows are unfiltered in raw sample units before index validation.
    Other colour types need only a valid filter byte and exact scanline lengths.
    """
    def __init__(self, width, height, depth, colour, interlace):
        self.depth, self.colour = depth, colour
        channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
        geometry = ((0, 0, 1, 1),) if not interlace else (
            (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
            (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
        self.passes = []
        for x, y, dx, dy in geometry:
            w = max(0, (width - x + dx - 1) // dx)
            h = max(0, (height - y + dy - 1) // dy)
            if w and h:
                self.passes.append((w, h, (w * channels * depth + 7) // 8))
        self.expected = sum(h * (row_bytes + 1) for _, h, row_bytes in self.passes)
        self.received = self.pass_index = self.row_index = 0
        self.row = bytearray()
        self.previous = None
        self.palette_entries = None

    def feed(self, data):
        self.received += len(data)
        if self.received > self.expected:
            raise _PNGError('png_idat_length_mismatch', "IDAT scanline byte count is longer than IHDR requires")
        offset = 0
        while offset < len(data):
            width, height, size = self.passes[self.pass_index]
            take = min(size + 1 - len(self.row), len(data) - offset)
            self.row.extend(data[offset:offset + take])
            offset += take
            if len(self.row) == size + 1:
                filter_type = self.row[0]
                if filter_type > 4:
                    raise _PNGError('png_invalid_scanline_filter', "invalid PNG scanline filter type")
                if self.colour == 3 and self.palette_entries != 1 << self.depth:
                    packed = bytearray(self.row[1:])
                    prior = self.previous
                    for i in range(len(packed)) if filter_type else ():
                        value = packed[i]
                        left = packed[i - 1] if i else 0
                        above = prior[i] if prior is not None else 0
                        upper_left = prior[i - 1] if prior is not None and i else 0
                        if filter_type == 1:
                            value += left
                        elif filter_type == 2:
                            value += above
                        elif filter_type == 3:
                            value += (left + above) // 2
                        elif filter_type == 4:
                            predictor = left + above - upper_left
                            dl, da, du = abs(predictor - left), abs(predictor - above), abs(predictor - upper_left)
                            value += left if dl <= da and dl <= du else above if da <= du else upper_left
                        packed[i] = value & 255
                    values = np.frombuffer(packed, dtype=np.uint8)
                    if self.depth == 8:
                        invalid_index = bool(np.any(values >= self.palette_entries))
                    else:
                        shifts = np.arange(8 - self.depth, -1, -self.depth, dtype=np.uint8)
                        samples = ((values[:, None] >> shifts) & ((1 << self.depth) - 1)).ravel()[:width]
                        invalid_index = bool(np.any(samples >= self.palette_entries))
                    if invalid_index:
                        raise _PNGError('png_palette_index_out_of_range', "palette pixel index has no PLTE entry")
                    self.previous = packed
                self.row.clear()
                self.row_index += 1
                if self.row_index == height:
                    self.pass_index += 1
                    self.row_index = 0
                    self.previous = None

    def finish(self):
        if self.received != self.expected or self.row:
            raise _PNGError('png_idat_length_mismatch', f"IDAT scanline byte count is short: expected {self.expected}, received {self.received}")


def _validate_png_structure(data, *, pixel_limit):
    """Independent chunk/CRC/critical-order/zlib/sample validation, no Pillow internals.

    The compressed snapshot is shared with the decoder. Chunks and inflate
    output are consumed in bounded blocks; no full inflated image is retained.
    """
    seen_ihdr = seen_plte = seen_idat = ended_idat = seen_trns = False
    inflater = zlib.decompressobj()
    scanlines = None
    colour = depth = None

    def inflate(block):
        while block:
            if inflater.eof:
                raise _PNGError('png_invalid_idat_stream', "extra compressed bytes after IDAT zlib stream")
            output = inflater.decompress(block, 65536)
            scanlines.feed(output)
            if inflater.unused_data:
                raise _PNGError('png_invalid_idat_stream', "extra compressed bytes after IDAT zlib stream")
            block = inflater.unconsumed_tail

    with BytesIO(data) as handle:
        if handle.read(8) != b"\x89PNG\r\n\x1a\n":
            raise _PNGError('png_invalid_signature', "invalid PNG signature")
        while True:
            header = handle.read(8)
            if len(header) != 8:
                raise _PNGError('png_truncated_chunk', "PNG missing/truncated chunk or IEND")
            length, kind = struct.unpack(">I4s", header)
            if kind in {b"acTL", b"fcTL", b"fdAT"}:
                raise _PNGError('png_apng_unsupported', "APNG is unsupported")
            if length > 0x7fffffff or not all(65 <= c <= 90 or 97 <= c <= 122 for c in kind) or kind[2] & 32:
                raise _PNGError('png_invalid_chunk', "invalid PNG chunk length/type")
            if not seen_ihdr and kind != b"IHDR":
                raise _PNGError('png_invalid_chunk_order', "IHDR must be the first PNG chunk")
            if kind == b"IHDR" and (seen_ihdr or length != 13):
                raise _PNGError('png_invalid_chunk_order', "PNG requires exactly one 13-byte IHDR first")
            if kind == b"PLTE" and (seen_plte or seen_idat or colour in {0, 4} or
                                    not 0 < length <= 768 or length % 3):
                raise _PNGError('png_invalid_palette', "invalid PLTE length/order/colour type")
            if kind == b"tRNS":
                if colour != 3:
                    raise _PNGError('png_nonopaque_transparency', "non-palette tRNS is nonopaque/unsupported")
                if seen_trns or seen_idat or not seen_plte or not 0 < length <= scanlines.palette_entries:
                    raise _PNGError('png_invalid_transparency', "invalid palette tRNS length/order")
            if kind == b"IDAT":
                if colour == 3 and not seen_plte:
                    raise _PNGError('png_missing_palette', 'palette PNG missing PLTE')
                if ended_idat:
                    raise _PNGError('png_invalid_chunk_order', "IDAT order invalid or palette PNG missing PLTE")
                seen_idat = True
            elif seen_idat:
                ended_idat = True
            if kind == b"IEND" and (length or not seen_idat):
                raise _PNGError('png_invalid_chunk_order', "invalid IEND length/order or missing IDAT")
            if not kind[0] & 32 and kind not in {b"IHDR", b"PLTE", b"IDAT", b"IEND"}:
                raise _PNGError('png_unknown_critical_chunk', "unknown critical PNG chunk")
            crc = zlib.crc32(kind)
            payload = bytearray()  # only bounded IHDR/PLTE/tRNS metadata retained
            remaining = length
            while remaining:
                block = handle.read(min(remaining, 65536))
                if not block:
                    raise _PNGError('png_truncated_chunk', "truncated PNG chunk payload")
                remaining -= len(block)
                crc = zlib.crc32(block, crc)
                if kind == b"IDAT":
                    inflate(block)
                elif kind in {b"IHDR", b"PLTE", b"tRNS"}:
                    payload.extend(block)
            expected_crc = handle.read(4)
            if len(expected_crc) != 4 or struct.unpack(">I", expected_crc)[0] != crc:
                raise _PNGError('png_crc_mismatch', f"PNG {kind.decode()} CRC mismatch/truncation")
            if kind == b"IHDR":
                width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
                if depth == 16:
                    raise _PNGError('png_unsupported_bit_depth', '16-bit PNG is unsupported')
                allowed = {0: {1, 2, 4, 8}, 2: {8}, 3: {1, 2, 4, 8}, 4: {8}, 6: {8}}
                if not 0 < width <= 0x7fffffff or not 0 < height <= 0x7fffffff or depth not in allowed.get(colour, set()):
                    raise _PNGError('png_invalid_header', "invalid/unsupported IHDR dimensions/bit depth/colour type")
                if compression != 0 or filtering != 0 or interlace not in {0, 1}:
                    raise _PNGError('png_invalid_header', "unsupported IHDR compression/filter/interlace method")
                _check_pixel_limit(width, height, pixel_limit)
                scanlines = _PNGScanlines(width, height, depth, colour, interlace)
                seen_ihdr = True
            elif kind == b"PLTE":
                entries = length // 3
                if colour == 3 and entries > 1 << depth:
                    raise _PNGError('png_invalid_palette', "PLTE entries exceed palette bit depth")
                scanlines.palette_entries = entries
                seen_plte = True
            elif kind == b"tRNS":
                if any(alpha != 255 for alpha in payload):
                    raise _PNGError('png_nonopaque_transparency', "nonopaque palette tRNS is unsupported (including unused entries)")
                seen_trns = True
            elif kind == b"IEND":
                if not inflater.eof:
                    raise _PNGError('png_invalid_idat_stream', "truncated/incomplete IDAT zlib stream")
                scanlines.finish()
                if handle.read(1):
                    raise _PNGError('png_trailing_bytes', "trailing bytes after IEND")
                return

def read_png(path, relative_path, *, sha256=None):
    """Validate, hash and decode the same file buffer; return a white-marked mask."""
    try:
        data = path.read_bytes()
        if sha256 is not None and (not isinstance(sha256, str) or hashlib.sha256(data).hexdigest() != sha256):
            raise NumericContractError('input_hash_mismatch', relative_path)
        with decoder_session() as pixel_limit:
            _validate_png_structure(data, pixel_limit=pixel_limit)
            try:
                with Image.open(BytesIO(data)) as image:
                    if image.format != 'PNG' or image.mode not in {'1', 'L', 'RGB', 'LA', 'RGBA', 'P'}:
                        raise _PNGError('unsupported_png_mode', str(image.mode))
                    if image.getexif().get(274, 1) != 1:
                        raise _PNGError('png_nonidentity_orientation', 'nonidentity orientation metadata')
                    image.load()
                    if image.mode in {'1', 'L'}:
                        return binary_mask(np.asarray(image))
                    # Exact palette/alpha expansion only; no luminance conversion.
                    rgba = np.asarray(image.convert('RGBA'))
                    if not np.all(rgba[..., 3] == 255):
                        raise _PNGError('png_nonopaque_pixels', 'nonopaque pixels')
                    rgb = rgba[..., :3]
                    black = np.all(rgb == 0, axis=2)
                    white = np.all(rgb == 255, axis=2)
                    if not np.all(black | white):
                        raise _PNGError('nonbinary_nominal_mask', 'pixels must be exactly black or white')
                    white.setflags(write=False)
                    return white
            except (_PNGError, NumericContractError, Image.DecompressionBombError, Image.DecompressionBombWarning):
                raise
            except Exception as exc:
                # Third-party decode failures (including malformed ancillary metadata)
                # are input errors; process-control BaseExceptions must propagate.
                raise _PNGError('png_decode_failed', str(exc)) from exc
    except _PNGError as exc:
        raise NumericContractError(exc.code, relative_path+': '+str(exc)) from exc
    except NumericContractError as exc:
        if relative_path in str(exc):
            raise
        raise NumericContractError(exc.code, relative_path) from exc
    except Image.DecompressionBombError as exc:
        raise NumericContractError('png_pixel_limit_error', relative_path) from exc
    except Image.DecompressionBombWarning as exc:
        raise NumericContractError('png_pixel_limit_warning', relative_path) from exc
    except zlib.error as exc:
        raise NumericContractError('png_invalid_idat_stream', relative_path) from exc
    except (OSError, ValueError, TypeError, SyntaxError, struct.error) as exc:
        raise NumericContractError('png_decode_failed', relative_path) from exc


def read_gray_hotspot(path, relative_path, *, sha256=None, return_sha256=False):
    """Decode opaque grayscale hotspot occupancy from the same validated bytes.

    Unlike the legacy binary reader, every nonzero gray sample is occupied.
    RGB is accepted only when all three channels are identical; palette and alpha
    encodings are rejected rather than converted.
    """
    try:
        data = path.read_bytes()
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if sha256 is not None and (not isinstance(sha256, str) or actual_sha256 != sha256):
            raise NumericContractError('input_hash_mismatch', relative_path)
        with decoder_session() as pixel_limit:
            _validate_png_structure(data, pixel_limit=pixel_limit)
            try:
                with Image.open(BytesIO(data)) as image:
                    if image.format != 'PNG' or image.mode not in {'L', 'RGB'}:
                        raise _PNGError('png_hotspot_grayscale_required', str(image.mode))
                    if image.getexif().get(274, 1) != 1:
                        raise _PNGError('png_nonidentity_orientation', 'nonidentity orientation metadata')
                    image.load()
                    pixels = np.asarray(image)
                    if image.mode == 'RGB':
                        if not np.array_equal(pixels[..., 0], pixels[..., 1]) or not np.array_equal(pixels[..., 0], pixels[..., 2]):
                            raise _PNGError('png_hotspot_color_rejected', 'RGB channels differ')
                        pixels = pixels[..., 0]
                    mask = pixels != 0
                    mask.setflags(write=False)
                    return (mask, actual_sha256) if return_sha256 else mask
            except (_PNGError, NumericContractError, Image.DecompressionBombError, Image.DecompressionBombWarning):
                raise
            except Exception as exc:
                raise _PNGError('png_decode_failed', str(exc)) from exc
    except _PNGError as exc:
        raise NumericContractError(exc.code, relative_path+': '+str(exc)) from exc
    except NumericContractError as exc:
        if relative_path in str(exc):
            raise
        raise NumericContractError(exc.code, relative_path) from exc
    except Image.DecompressionBombError as exc:
        raise NumericContractError('png_pixel_limit_error', relative_path) from exc
    except Image.DecompressionBombWarning as exc:
        raise NumericContractError('png_pixel_limit_warning', relative_path) from exc
    except zlib.error as exc:
        raise NumericContractError('png_invalid_idat_stream', relative_path) from exc
    except (OSError, ValueError, TypeError, SyntaxError, struct.error) as exc:
        raise NumericContractError('png_decode_failed', relative_path) from exc
