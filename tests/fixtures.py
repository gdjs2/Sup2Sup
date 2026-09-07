"""Synthetic, redistributable SUP fixtures written independently from the production parser."""

import struct


def packet(kind, data=b"", pts=90000, dts=81000):
    return b"PG" + struct.pack(">IIBH", pts & 0xFFFFFFFF, dts & 0xFFFFFFFF, kind, len(data)) + data


def pcs(objects=((1, 0, 0, 500, 950, None),), *, pts=90000, state=0x80,
        width=1920, height=1080, number=0, update=0, palette=0):
    data = struct.pack(">HHBHBBBB", width, height, 0x10, number, state, update, palette, len(objects))
    for oid, wid, flags, x, y, crop in objects:
        data += struct.pack(">HBBHH", oid, wid, flags, x, y)
        if crop:
            data += struct.pack(">HHHH", *crop)
    return packet(0x16, data, pts)


def wds(windows=((0, 0, 0, 1920, 1080),), pts=90000):
    return packet(0x17, bytes([len(windows)]) + b"".join(struct.pack(">BHHHH", *w)
                                                        for w in windows), pts)


def pds(entries=((0, 16, 128, 128, 0), (1, 235, 128, 128, 255)), pts=90000, version=0):
    return packet(0x14, bytes([0, version]) + b"".join(bytes(entry) for entry in entries), pts)


def rle_solid(width, height, color=1):
    result = bytearray()
    for _ in range(height):
        remaining = width
        while remaining:
            run = min(remaining, 0x3FFF)
            if run < 64:
                result.extend((0, 0x80 | run, color))
            else:
                result.extend((0, 0xC0 | (run >> 8), run & 255, color))
            remaining -= run
        result.extend((0, 0))
    return bytes(result)


def ods(oid=1, width=400, height=50, *, pixels=None, fragmented=False, version=0, pts=90000):
    rle = pixels if pixels is not None else rle_solid(width, height)
    header = struct.pack(">HB", oid, version)
    size = (len(rle) + 4).to_bytes(3, "big") + struct.pack(">HH", width, height)
    if not fragmented:
        return packet(0x15, header + b"\xc0" + size + rle, pts)
    split = len(rle) // 2
    return (packet(0x15, header + b"\x80" + size + rle[:split], pts)
            + packet(0x15, header + b"\x00" + rle[split:split + 1], pts)
            + packet(0x15, header + b"\x40" + rle[split + 1:], pts))


def end(pts=90000):
    return packet(0x80, pts=pts)


def simple(*, x=500, y=950, width=400, height=50, fragmented=False, clear=True):
    result = (pcs(((1, 0, 0, x, y, None),)) + wds() + pds()
              + ods(width=width, height=height, fragmented=fragmented) + end())
    if clear:
        result += pcs((), pts=270000, state=0, number=1) + end(270000)
    return result


def many_cues(count=1000):
    """One cached bitmap and many presentations, like an animation-heavy subtitle track."""
    result = bytearray(simple(clear=False))
    for index in range(1, count):
        pts = 90000 + index * 90000
        result.extend(pcs(pts=pts, state=0, number=index % 65536))
        result.extend(end(pts))
    pts = 90000 + count * 90000
    result.extend(pcs((), pts=pts, state=0, number=count % 65536))
    result.extend(end(pts))
    return bytes(result)
