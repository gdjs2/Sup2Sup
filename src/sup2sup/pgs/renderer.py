"""Render object-local RGBA tiles; the GUI performs the final alpha compositing."""

from dataclasses import dataclass

from .parser import Cue


@dataclass(frozen=True)
class Tile:
    x: int
    y: int
    width: int
    height: int
    rgba: bytes


def render_tiles(cue: Cue) -> tuple[Tile, ...]:
    colors = tuple(bytes(color) for color in cue.palette)
    tiles = []
    for placement in cue.placements:
        visible = placement.rect.intersection(placement.window)
        if visible is None:
            continue
        source, bitmap = placement.source_rect, placement.bitmap
        sx = source.x + visible.x - placement.rect.x
        sy = source.y + visible.y - placement.rect.y
        pixels = bytearray()
        for row in range(sy, sy + visible.height):
            begin = row * bitmap.width + sx
            pixels.extend(b"".join(colors[i] for i in bitmap.indices[begin:begin + visible.width]))
        tiles.append(Tile(visible.x, visible.y, visible.width, visible.height, bytes(pixels)))
    return tuple(tiles)
