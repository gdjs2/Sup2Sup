"""Replace full-screen objects with cropped RLE, including reused object versions."""

from dataclasses import replace

from sup2sup.edit.geometry import Crop, EditError, Transform, is_fullscreen, retained_rect
from sup2sup.progress import ProgressCallback, report_progress

from .parser import Document
from .rle import crop_rle
from .segments import Composition, Rect, Segment, SegmentType


def _object_packets(oid: int, version: int, region: Rect, encoded: bytes,
                    timing: Segment) -> list[Segment]:
    """Respect both the 24-bit object length and 16-bit segment payload length."""
    if len(encoded) + 4 > 0xFFFFFF:
        raise EditError("Cropped bitmap exceeds the PGS object size limit")
    prefix = oid.to_bytes(2, "big") + bytes([version])
    size = ((len(encoded) + 4).to_bytes(3, "big")
            + region.width.to_bytes(2, "big") + region.height.to_bytes(2, "big"))
    packets = []
    offset = 0
    while offset < len(encoded):
        first = offset == 0
        chunk = encoded[offset:offset + 0xFFFF - (11 if first else 4)]
        offset += len(chunk)
        flags = (0x80 if first else 0) | (0x40 if offset == len(encoded) else 0)
        payload = prefix + bytes([flags]) + (size if first else b"") + chunk
        packets.append(Segment(timing.pts, timing.dts, SegmentType.ODS, payload))
    return packets


def rewrite_fullscreen(document: Document, rewritten: list[Segment], crop: Crop,
                       transforms: dict[int, Transform], *,
                       progress: ProgressCallback | None = None) -> list[Segment]:
    """Keep ordinary ODS packets verbatim; emit cropped variants when a cue needs one.

    A reused source bitmap may need different pixels after individual cue movement.
    Track the active output variant per object ID, never caching all decoded images.
    """
    canvas = crop.rectangle(document.width, document.height)
    affected = {}
    for cue in document.cues:
        t = transforms.get(cue.index, Transform())
        for p in cue.placements:
            if (is_fullscreen(cue, p)
                    and retained_rect(cue, p, canvas, t) != p.rect.moved(t.dx, t.dy)):
                affected[id(p.bitmap)] = p.bitmap
    if not affected:
        return rewritten

    removed = {index for bitmap in affected.values() for index in bitmap.segment_indices}
    occupied = {int.from_bytes(s.payload[:2], "big") for s in document.segments
                if s.kind == SegmentType.ODS}
    aliases: dict[tuple[int, int], int] = {}
    assigned: set[int] = set()
    spare = 0
    for cue in document.cues:
        for p in cue.placements:
            if id(p.bitmap) not in affected:
                continue
            if not is_fullscreen(cue, p):
                raise EditError(
                    f"Cue {cue.index + 1}: a full-screen bitmap is also used as a cropped "
                    "composition object; this shared layout cannot be auto-cropped"
                )
            key = (p.reference.object_id, p.reference.window_id)
            if key in aliases:
                continue
            oid = p.reference.object_id
            if oid in assigned:
                while spare in occupied or spare in assigned:
                    spare += 1
                if spare > 0xFFFF:
                    raise EditError("No free PGS object ID for full-screen crop")
                oid = spare
            aliases[key] = oid
            assigned.add(oid)

    active: dict[int, tuple[int, Rect]] = {}
    versions: dict[int, int] = {}
    output = []
    cursor = 0
    total = len(document.display_sets)
    for completed, display in enumerate(document.display_sets):
        def checkpoint(completed=completed):
            report_progress(progress, "Cropping full-screen bitmaps", completed, total,
                            f"Presentation {completed + 1} of {total}", unit="presentations")

        checkpoint()
        output.extend(rewritten[cursor:display.pcs_segment])
        if display.composition.state & 0xC0:
            active.clear()
            versions.clear()
        packets = []
        for index in range(display.pcs_segment, display.end_segment):
            if index in removed:
                continue
            segment = rewritten[index]
            packets.append(segment)
            if segment.kind == SegmentType.ODS and segment.payload[3] & 0x80:
                oid = int.from_bytes(segment.payload[:2], "big")
                active.pop(oid, None)
                versions[oid] = segment.payload[2]
        pcs = Composition.parse(packets[0].payload)
        objects = list(pcs.objects)
        generated = []
        if display.cue_index is not None:
            cue = document.cues[display.cue_index]
            t = transforms.get(cue.index, Transform())
            for number, p in enumerate(cue.placements):
                if id(p.bitmap) not in affected:
                    continue
                rect = retained_rect(cue, p, canvas, t)
                assert rect is not None  # Placement validation runs before rewriting.
                source = rect.moved(-t.dx, -t.dy)
                oid = aliases[(p.reference.object_id, p.reference.window_id)]
                objects[number] = replace(objects[number], object_id=oid)
                variant = (id(p.bitmap), source)
                if active.get(oid) == variant:
                    continue
                bitmap = p.bitmap
                version = (versions[oid] + 1) & 0xFF if oid in versions else bitmap.version
                encoded = crop_rle(bitmap.rle, bitmap.width, bitmap.height, source,
                                   checkpoint=checkpoint)
                first = bitmap.segment_indices[0]
                timing = (document.segments[first]
                          if display.pcs_segment < first < display.end_segment else packets[0])
                generated.extend(_object_packets(oid, version, source, encoded, timing))
                active[oid], versions[oid] = variant, version
        # A new bitmap variant is an object update, even during a source palette-only fade.
        pcs = replace(pcs, objects=tuple(objects),
                      palette_update=0 if generated else pcs.palette_update)
        packets[0] = replace(packets[0], payload=pcs.to_bytes())
        output.extend(packets)
        output.extend(generated)
        output.append(rewritten[display.end_segment])
        cursor = display.end_segment + 1
        report_progress(progress, "Cropping full-screen bitmaps", completed + 1, total,
                        unit="presentations")
    output.extend(rewritten[cursor:])
    return output
