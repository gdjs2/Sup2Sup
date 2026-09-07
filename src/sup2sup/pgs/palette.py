"""PGS palettes use limited-range Y, Cr, Cb and straight alpha."""

from .segments import PGSError

Palette = tuple[tuple[int, int, int, int], ...]
EMPTY_PALETTE: Palette = ((0, 0, 0, 0),) * 256


def rgba(y: int, cr: int, cb: int, alpha: int, *, hd: bool = True) -> tuple[int, ...]:
    luma, red, blue = 1.16438356 * (y - 16), cr - 128, cb - 128
    if hd:
        rgb = (luma + 1.79274107 * red,
               luma - 0.21324861 * blue - 0.53290933 * red,
               luma + 2.11240179 * blue)
    else:
        rgb = (luma + 1.59602678 * red,
               luma - 0.39176229 * blue - 0.81296765 * red,
               luma + 2.01723214 * blue)
    return (*(max(0, min(255, round(value))) for value in rgb), alpha)


def update_palette(data: bytes, previous: Palette = EMPTY_PALETTE, *, hd: bool = True
                   ) -> tuple[int, Palette]:
    if len(data) < 2 or (len(data) - 2) % 5:
        raise PGSError("Invalid PDS payload length")
    colors = list(previous)
    for offset in range(2, len(data), 5):
        index, y, cr, cb, alpha = data[offset:offset + 5]
        colors[index] = rgba(y, cr, cb, alpha, hd=hd)
    return data[0], tuple(colors)
