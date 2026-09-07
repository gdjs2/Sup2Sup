"""Strict PGS run-length decoder, independent of image libraries."""

from collections.abc import Callable

from .segments import PGSError, Reader

MAX_BITMAP_PIXELS = 4096 * 2160


def decode_rle(data: bytes, width: int, height: int, *,
               checkpoint: Callable[[], None] | None = None) -> bytes:
    if width <= 0 or height <= 0 or width * height > MAX_BITMAP_PIXELS:
        raise PGSError(f"Invalid or excessive bitmap dimensions: {width}x{height}")
    r = Reader(data)
    pixels = bytearray(width * height)
    x = y = 0
    while r.offset < len(data):
        if y >= height:
            raise PGSError("RLE data continues after the final row")
        color = r.integer(1)
        run = 1
        if color == 0:
            flags = r.integer(1)
            if flags == 0:
                if x != width:
                    raise PGSError(f"RLE row {y} has {x} pixels, expected {width}")
                x = 0
                y += 1
                if checkpoint and y % 32 == 0:
                    checkpoint()
                continue
            run = flags & 0x3F
            if flags & 0x40:
                run = (run << 8) | r.integer(1)
            color = r.integer(1) if flags & 0x80 else 0
            if run == 0:
                raise PGSError("Zero-length RLE run")
        if x + run > width:
            raise PGSError(f"RLE run crosses row {y} boundary")
        offset = y * width + x
        pixels[offset:offset + run] = bytes([color]) * run
        x += run
    # Some encoders omit only the very last end-of-line marker.
    if not (y == height and x == 0 or y == height - 1 and x == width):
        raise PGSError("Incomplete RLE bitmap")
    return bytes(pixels)
