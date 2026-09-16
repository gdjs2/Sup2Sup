"""Strict PGS run-length decoding, validation and cropping without image libraries."""

from collections.abc import Callable

from .segments import PGSError, Reader, Rect

MAX_BITMAP_PIXELS = 4096 * 2160


def decode_rle(data: bytes, width: int, height: int, *,
               checkpoint: Callable[[], None] | None = None) -> bytes:
    pixels = _read_rle(data, width, height, checkpoint=checkpoint, decode=True)
    assert pixels is not None
    return pixels


def validate_rle(data: bytes, width: int, height: int, *,
                 checkpoint: Callable[[], None] | None = None) -> None:
    """Check every run and row without allocating the expanded image."""
    _read_rle(data, width, height, checkpoint=checkpoint, decode=False)


def crop_rle(data: bytes, width: int, height: int, region: Rect, *,
             checkpoint: Callable[[], None] | None = None) -> bytes:
    """Crop indexed pixels directly from runs, without expanding a full-screen image."""
    if (region.width <= 0 or region.height <= 0
            or not Rect(0, 0, width, height).contains(region)):
        raise PGSError("Bitmap crop must be a nonempty rectangle inside the image")
    output = bytearray()

    def keep_run(x: int, y: int, run: int, color: int) -> None:
        if not region.y <= y < region.bottom:
            return
        count = min(x + run, region.right) - max(x, region.x)
        if count <= 0:
            return
        remaining = count
        while remaining:
            length = min(remaining, 0x3FFF)
            remaining -= length
            if color and length <= 2:
                output.extend(bytes([color]) * length)
            else:
                flags = 0x80 if color else 0
                if length < 64:
                    output.extend((0, flags | length))
                else:
                    output.extend((0, flags | 0x40 | (length >> 8), length & 0xFF))
                if color:
                    output.append(color)
        if x + run >= region.right:
            output.extend((0, 0))

    _read_rle(data, width, height, checkpoint=checkpoint, decode=False, on_run=keep_run)
    return bytes(output)


def _read_rle(data: bytes, width: int, height: int, *,
              checkpoint: Callable[[], None] | None, decode: bool,
              on_run: Callable[[int, int, int, int], None] | None = None) -> bytes | None:
    if width <= 0 or height <= 0 or width * height > MAX_BITMAP_PIXELS:
        raise PGSError(f"Invalid or excessive bitmap dimensions: {width}x{height}")
    r = Reader(data)
    pixels = bytearray(width * height) if decode else None
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
        if pixels is not None:
            offset = y * width + x
            pixels[offset:offset + run] = bytes([color]) * run
        if on_run is not None:
            on_run(x, y, run, color)
        x += run
    # Some encoders omit only the very last end-of-line marker.
    if not (y == height and x == 0 or y == height - 1 and x == width):
        raise PGSError("Incomplete RLE bitmap")
    return bytes(pixels) if pixels is not None else None
