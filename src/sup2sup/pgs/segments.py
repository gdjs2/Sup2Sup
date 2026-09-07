"""PGS wire structures. All multibyte fields are big endian."""

from dataclasses import dataclass
from enum import IntEnum
import struct


class PGSError(ValueError):
    """Malformed or unsupported subtitle stream, with useful context."""


class SegmentType(IntEnum):
    PDS = 0x14
    ODS = 0x15
    PCS = 0x16
    WDS = 0x17
    END = 0x80


@dataclass(frozen=True)
class Segment:
    pts: int
    dts: int
    kind: int
    payload: bytes

    def to_bytes(self) -> bytes:
        return struct.pack(">2sIIBH", b"PG", self.pts, self.dts, self.kind, len(self.payload)) + self.payload


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def take(self, count: int) -> bytes:
        if self.offset + count > len(self.data):
            raise PGSError(f"Truncated payload at byte {self.offset}; need {count} bytes")
        value = self.data[self.offset:self.offset + count]
        self.offset += count
        return value

    def integer(self, count: int) -> int:
        return int.from_bytes(self.take(count), "big")

    def finish(self) -> None:
        if self.offset != len(self.data):
            raise PGSError(f"Unexpected {len(self.data) - self.offset} trailing payload bytes")


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def moved(self, dx: int, dy: int) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.width, self.height)

    def contains(self, other: "Rect") -> bool:
        return (self.x <= other.x and self.y <= other.y
                and self.right >= other.right and self.bottom >= other.bottom)

    def intersection(self, other: "Rect") -> "Rect | None":
        x, y = max(self.x, other.x), max(self.y, other.y)
        right, bottom = min(self.right, other.right), min(self.bottom, other.bottom)
        return Rect(x, y, right - x, bottom - y) if right > x and bottom > y else None

    def union(self, other: "Rect") -> "Rect":
        x, y = min(self.x, other.x), min(self.y, other.y)
        return Rect(x, y, max(self.right, other.right) - x, max(self.bottom, other.bottom) - y)


@dataclass(frozen=True)
class CompositionObject:
    object_id: int
    window_id: int
    flags: int
    x: int
    y: int
    crop: Rect | None = None

    @property
    def forced(self) -> bool:
        return bool(self.flags & 0x40)

    def to_bytes(self) -> bytes:
        result = struct.pack(">HBBHH", self.object_id, self.window_id, self.flags, self.x, self.y)
        if self.crop is not None:
            c = self.crop
            result += struct.pack(">HHHH", c.x, c.y, c.width, c.height)
        return result


@dataclass(frozen=True)
class Composition:
    width: int
    height: int
    frame_rate: int
    number: int
    state: int
    palette_update: int
    palette_id: int
    objects: tuple[CompositionObject, ...]

    @classmethod
    def parse(cls, data: bytes) -> "Composition":
        r = Reader(data)
        width, height = r.integer(2), r.integer(2)
        if not width or not height:
            raise PGSError("PCS canvas must have nonzero dimensions")
        rate, number, state = r.integer(1), r.integer(2), r.integer(1)
        update, palette, count = r.integer(1), r.integer(1), r.integer(1)
        if count > 2:
            raise PGSError("PGS supports at most two composition objects")
        objects = []
        for _ in range(count):
            oid, wid, flags = r.integer(2), r.integer(1), r.integer(1)
            x, y = r.integer(2), r.integer(2)
            crop = Rect(*(r.integer(2) for _ in range(4))) if flags & 0x80 else None
            if crop is not None and (not crop.width or not crop.height):
                raise PGSError("Object crop must have nonzero dimensions")
            objects.append(CompositionObject(oid, wid, flags, x, y, crop))
        r.finish()
        return cls(width, height, rate, number, state, update, palette, tuple(objects))

    def to_bytes(self) -> bytes:
        return struct.pack(
            ">HHBHBBBB", self.width, self.height, self.frame_rate, self.number,
            self.state, self.palette_update, self.palette_id, len(self.objects),
        ) + b"".join(obj.to_bytes() for obj in self.objects)


@dataclass(frozen=True)
class Window:
    window_id: int
    rect: Rect


def parse_windows(data: bytes) -> tuple[Window, ...]:
    r = Reader(data)
    count = r.integer(1)
    if count > 2:
        raise PGSError("PGS supports at most two windows")
    windows = tuple(Window(r.integer(1), Rect(*(r.integer(2) for _ in range(4))))
                    for _ in range(count))
    r.finish()
    if len({w.window_id for w in windows}) != len(windows):
        raise PGSError("Duplicate window ID in WDS")
    if any(w.rect.width == 0 or w.rect.height == 0 for w in windows):
        raise PGSError("Window must have nonzero dimensions")
    return windows


def encode_windows(windows: tuple[Window, ...]) -> bytes:
    return bytes([len(windows)]) + b"".join(
        struct.pack(">BHHHH", w.window_id, w.rect.x, w.rect.y, w.rect.width, w.rect.height)
        for w in windows
    )
