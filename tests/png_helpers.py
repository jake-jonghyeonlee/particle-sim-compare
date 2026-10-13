"""Test-only PNG byte builders, independent of the reader and Pillow encoder."""
import struct
import zlib


def png_chunk(kind, payload, *, bad_crc=False):
    crc = zlib.crc32(kind + payload) ^ int(bad_crc)
    return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', crc)


def raw_png(width, height, depth, colour, raw, *, interlace=0, compression=0,
            filtering=0, before=(), after=(), parts=None):
    header = struct.pack('>IIBBBBB', width, height, depth, colour, compression, filtering, interlace)
    parts = [zlib.compress(raw)] if parts is None else parts
    return (b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', header)
            + b''.join(png_chunk(k, p) for k, p in before)
            + b''.join(png_chunk(b'IDAT', p) for p in parts)
            + b''.join(png_chunk(k, p) for k, p in after) + png_chunk(b'IEND', b''))


def binary_rows(white, depth, colour, *, interlace=0):
    """Explicit pass coordinates and filter 0; True means a white source pixel."""
    height, width = len(white), len(white[0])
    passes = [(0, 0, 1, 1)] if not interlace else [
        (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
        (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
    raw = bytearray()
    for x0, y0, dx, dy in passes:
        xs = list(range(x0, width, dx))
        if not xs:
            continue
        for y in range(y0, height, dy):
            raw.append(0)
            samples = []
            for x in xs:
                value = (1 if colour == 3 else (1 << depth)-1) if white[y][x] else 0
                if colour in (0, 3):
                    samples.append(value)
                elif colour == 2:
                    samples.extend([value]*3)
                elif colour == 4:
                    samples.extend([value, 255])
                elif colour == 6:
                    samples.extend([value]*3 + [255])
            for offset in range(0, len(samples), 8//depth):
                packed = 0
                for i, value in enumerate(samples[offset:offset+8//depth]):
                    packed |= value << (8-depth*(i+1))
                raw.append(packed)
    return bytes(raw)
