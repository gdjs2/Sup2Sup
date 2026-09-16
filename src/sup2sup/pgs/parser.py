"""Retain every packet and build immutable presentation snapshots at END."""

from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
import struct

from sup2sup.progress import ProgressCallback, report_progress

from .palette import EMPTY_PALETTE, Palette, update_palette
from .rle import decode_rle, validate_rle
from .segments import Composition, CompositionObject, PGSError, Rect, Segment, SegmentType
from .segments import parse_windows

CLOCK = 90_000
MAX_SUP_BYTES = 512 * 1024 * 1024


@lru_cache(maxsize=4)
def _decode_bitmap(rle: bytes, width: int, height: int) -> bytes:
    # Shared across documents/tracks: at most four indexed images are retained.
    # The per-image limit in decode_rle bounds this cache to 33.75 MiB of pixels.
    return decode_rle(rle, width, height)


@dataclass(frozen=True)
class Bitmap:
    object_id: int
    version: int
    width: int
    height: int
    rle: bytes
    segment_indices: tuple[int, ...] = ()

    @property
    def indices(self) -> bytes:
        """Expand a validated bitmap only when a renderer requests its pixels."""
        return _decode_bitmap(self.rle, self.width, self.height)


@dataclass(frozen=True)
class Placement:
    reference: CompositionObject
    bitmap: Bitmap
    window: Rect
    window_segment: int

    @property
    def source_rect(self) -> Rect:
        return self.reference.crop or Rect(0, 0, self.bitmap.width, self.bitmap.height)

    @property
    def rect(self) -> Rect:
        source = self.source_rect
        return Rect(self.reference.x, self.reference.y, source.width, source.height)


@dataclass(frozen=True)
class Cue:
    index: int
    pcs_segment: int
    start_pts: int
    end_pts: int | None
    composition: Composition
    placements: tuple[Placement, ...]
    palette: Palette

    @property
    def bounds(self) -> Rect:
        result = self.placements[0].rect
        for placement in self.placements[1:]:
            result = result.union(placement.rect)
        return result

    @property
    def forced(self) -> bool:
        return any(p.reference.forced for p in self.placements)


@dataclass(frozen=True)
class DisplaySet:
    pcs_segment: int
    end_segment: int
    pts: int
    composition: Composition
    cue_index: int | None


@dataclass(frozen=True)
class Document:
    segments: tuple[Segment, ...]
    display_sets: tuple[DisplaySet, ...]
    cues: tuple[Cue, ...]
    width: int
    height: int
    warnings: tuple[str, ...] = ()

    def to_bytes(self) -> bytes:
        return b"".join(segment.to_bytes() for segment in self.segments)


def parse_segments(data: bytes, *, progress: ProgressCallback | None = None) -> tuple[Segment, ...]:
    if len(data) > MAX_SUP_BYTES:
        raise PGSError("SUP exceeds the 512 MiB input limit")
    segments = []
    offset = 0
    report_progress(progress, "Reading packets", 0, len(data), unit="bytes")
    while offset < len(data):
        if len(data) - offset < 13:
            raise PGSError(f"Truncated SUP header at byte {offset}")
        magic, pts, dts, kind, length = struct.unpack_from(">2sIIBH", data, offset)
        if magic != b"PG":
            raise PGSError(f"Missing PG signature at byte {offset}; open an extracted .sup file")
        end = offset + 13 + length
        if end > len(data):
            raise PGSError(f"Truncated segment at byte {offset}: expected {length} payload bytes")
        segments.append(Segment(pts, dts, kind, data[offset + 13:end]))
        offset = end
        if len(segments) % 256 == 0:
            report_progress(progress, "Reading packets", offset, len(data), unit="bytes")
    if not segments:
        raise PGSError("SUP file is empty")
    report_progress(progress, "Reading packets", len(data), len(data), unit="bytes")
    return tuple(segments)


@dataclass
class _Fragment:
    version: int
    width: int
    height: int
    expected: int
    data: bytearray
    segment_indices: list[int]


def parse_sup(data: bytes, *, progress: ProgressCallback | None = None) -> Document:
    segments = parse_segments(data, progress=progress)
    total_cues = sum(s.kind == SegmentType.PCS and len(s.payload) >= 11 and s.payload[10] > 0
                     for s in segments)
    objects: dict[int, Bitmap] = {}
    fragments: dict[int, _Fragment] = {}
    palettes: dict[int, Palette] = {}
    windows: dict[int, tuple[Rect, int]] = {}
    cues: list[Cue] = []
    displays = []
    warnings = set()
    composition = None
    pcs_index = -1
    current_pts = 0
    previous_raw_pts = None
    wrap = 0
    width = height = 0

    def loading_progress():
        report_progress(progress, "Loading cues", len(cues), total_cues)

    loading_progress()

    for index, segment in enumerate(segments):
        loading_progress()
        try:
            kind, payload = segment.kind, segment.payload
            if kind == SegmentType.PCS:
                if composition is not None:
                    raise PGSError("New PCS before previous display set END")
                composition = Composition.parse(payload)
                if width and (width, height) != (composition.width, composition.height):
                    raise PGSError("Changing canvas dimensions within one SUP is unsupported")
                width, height = composition.width, composition.height
                if previous_raw_pts is not None and segment.pts < previous_raw_pts:
                    if previous_raw_pts - segment.pts > 0x80000000:
                        wrap += 1 << 32
                    else:
                        raise PGSError("Presentation timestamps are out of order")
                previous_raw_pts = segment.pts
                current_pts = segment.pts + wrap
                pcs_index = index
                # Non-normal compositions are independent acquisition/epoch boundaries.
                if composition.state & 0xC0:
                    if fragments:
                        raise PGSError("Unfinished ODS at an epoch/acquisition boundary")
                    objects.clear()
                    palettes.clear()
                    windows.clear()
                if cues and cues[-1].end_pts is None:
                    cues[-1] = replace(cues[-1], end_pts=current_pts)
            elif kind in (SegmentType.PDS, SegmentType.ODS, SegmentType.WDS, SegmentType.END):
                if composition is None:
                    raise PGSError("Display-set segment without a preceding PCS")
                if kind == SegmentType.PDS:
                    old = palettes.get(payload[0], EMPTY_PALETTE) if payload else EMPTY_PALETTE
                    palette_id, palette = update_palette(payload, old, hd=height > 576)
                    palettes[palette_id] = palette
                elif kind == SegmentType.WDS:
                    for window in parse_windows(payload):
                        if not Rect(0, 0, width, height).contains(window.rect):
                            raise PGSError("Window extends beyond the source canvas")
                        windows[window.window_id] = (window.rect, index)
                elif kind == SegmentType.ODS:
                    if len(payload) < 4:
                        raise PGSError("Truncated ODS header")
                    oid = int.from_bytes(payload[:2], "big")
                    version, flags = payload[2:4]
                    if flags & 0x80:
                        if oid in fragments:
                            raise PGSError("ODS first fragment replaces an incomplete object")
                        if len(payload) < 11:
                            raise PGSError("Truncated first ODS fragment")
                        expected = int.from_bytes(payload[4:7], "big") - 4
                        bw, bh = struct.unpack_from(">HH", payload, 7)
                        if expected <= 0 or not (0 < bw <= width and 0 < bh <= height):
                            raise PGSError("Invalid ODS size or dimensions")
                        fragments[oid] = _Fragment(version, bw, bh, expected,
                                                   bytearray(payload[11:]), [index])
                    else:
                        if oid not in fragments or fragments[oid].version != version:
                            raise PGSError("ODS continuation has no matching first fragment/version")
                        fragments[oid].data.extend(payload[4:])
                        fragments[oid].segment_indices.append(index)
                    fragment = fragments[oid]
                    if len(fragment.data) > fragment.expected:
                        raise PGSError("ODS exceeds its declared object_data_length")
                    if flags & 0x40:
                        if len(fragment.data) != fragment.expected:
                            raise PGSError("Final ODS fragment is shorter than declared")
                        encoded = bytes(fragment.data)
                        # Keep the small RLE representation in cue snapshots. Validate all
                        # pixels' run structure now, including objects never previewed.
                        validate_rle(encoded, fragment.width, fragment.height,
                                     checkpoint=loading_progress if progress else None)
                        objects[oid] = Bitmap(oid, version, fragment.width, fragment.height, encoded,
                                              tuple(fragment.segment_indices))
                        del fragments[oid]
                else:
                    if payload:
                        raise PGSError("END segment must have an empty payload")
                    if fragments:
                        raise PGSError("Incomplete fragmented ODS at display-set END")
                    placements = []
                    for ref in composition.objects:
                        if ref.object_id not in objects:
                            raise PGSError(f"Undefined object {ref.object_id}")
                        if ref.window_id not in windows:
                            raise PGSError(f"Undefined window {ref.window_id}")
                        bitmap = objects[ref.object_id]
                        crop = ref.crop
                        if crop and not Rect(0, 0, bitmap.width, bitmap.height).contains(crop):
                            raise PGSError(f"Crop exceeds object {ref.object_id}")
                        window, window_index = windows[ref.window_id]
                        placement = Placement(ref, bitmap, window, window_index)
                        if not window.contains(placement.rect):
                            warnings.add("Some source windows clip objects; geometry export only "
                                         "supports this for auto-cropped full-screen cues.")
                        placements.append(placement)
                    cue_index = None
                    if placements:
                        if composition.palette_id not in palettes:
                            raise PGSError(f"Undefined palette {composition.palette_id}")
                        cue_index = len(cues)
                        cues.append(Cue(cue_index, pcs_index, current_pts, None, composition,
                                        tuple(placements), palettes[composition.palette_id]))
                    displays.append(DisplaySet(pcs_index, index, current_pts, composition, cue_index))
                    composition = None
            else:
                warnings.add(f"Unknown segment 0x{kind:02x} retained verbatim")
        except (PGSError, struct.error) as exc:
            raise PGSError(f"Segment {index + 1} (0x{segment.kind:02x}): {exc}") from exc
    if composition is not None:
        raise PGSError("File ends before display-set END")
    if not displays:
        raise PGSError("No presentation display sets in SUP")
    if cues and cues[-1].end_pts is None:
        warnings.add("Final cue has no clearing presentation; its end time remains open.")
    loading_progress()
    return Document(segments, tuple(displays), tuple(cues), width, height, tuple(sorted(warnings)))


def read_sup(path: str | Path, *, progress: ProgressCallback | None = None) -> Document:
    size = Path(path).stat().st_size
    if size > MAX_SUP_BYTES:
        raise PGSError("SUP exceeds the 512 MiB input limit")
    report_progress(progress, "Reading file", 0, size, unit="bytes")
    with Path(path).open("rb") as stream:
        data = bytearray()
        while chunk := stream.read(min(1024 * 1024, MAX_SUP_BYTES + 1 - len(data))):
            data.extend(chunk)
            report_progress(progress, "Reading file", len(data), size, unit="bytes")
            if len(data) > MAX_SUP_BYTES:
                raise PGSError("SUP exceeds the 512 MiB input limit")
    return parse_sup(bytes(data), progress=progress)
