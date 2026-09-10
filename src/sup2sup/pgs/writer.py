"""Rewrite geometry only, preserving segment order, PTS, DTS, ODS and PDS."""

from dataclasses import dataclass, replace
from hashlib import sha256

from sup2sup.edit.geometry import Crop, EditError, Transform, inspect_cue
from sup2sup.progress import Progress, ProgressCallback, report_progress

from .parser import Document, parse_sup
from .segments import Rect, Segment, SegmentType, Window, encode_windows, parse_windows

_DEFAULT_CROP = Crop()


@dataclass(frozen=True)
class ExportReport:
    source_size: tuple[int, int]
    output_size: tuple[int, int]
    cues: int
    moved_cues: int
    pcs_changed: int
    wds_changed: int
    timestamps_identical: bool
    bitmap_data_identical: bool
    palette_data_identical: bool
    ods_sha256: str
    pds_sha256: str
    input_sha256: str
    output_sha256: str


def _fingerprints(
    segments: tuple[Segment, ...], stage: str, progress: ProgressCallback | None
) -> tuple[str, str, str]:
    """Hash packets incrementally so preservation checks also report progress."""
    bitmap, palette, full = sha256(), sha256(), sha256()
    report_progress(progress, stage, 0, len(segments), unit="packets")
    for index, segment in enumerate(segments, 1):
        data = segment.to_bytes()
        full.update(data)
        if segment.kind == SegmentType.ODS:
            bitmap.update(data)
        elif segment.kind == SegmentType.PDS:
            palette.update(data)
        report_progress(progress, stage, index, len(segments), unit="packets")
    return bitmap.hexdigest(), palette.hexdigest(), full.hexdigest()


def export_sup(
    document: Document,
    crop: Crop = _DEFAULT_CROP,
    transforms: dict[int, Transform] | None = None,
    *,
    progress: ProgressCallback | None = None,
) -> tuple[bytes, ExportReport]:
    report_progress(progress, "Preparing subtitle export", 0, 0, unit="")
    transforms = transforms or {}
    if any(
        type(index) is not int or index < 0 or index >= len(document.cues) for index in transforms
    ):
        raise EditError("Transform refers to an unknown cue")
    region = crop.rectangle(document.width, document.height)
    changed = crop != Crop() or any(t != Transform() for t in transforms.values())
    rewritten = list(document.segments)
    if changed:
        known = {int(kind) for kind in SegmentType}
        if any(segment.kind not in known for segment in document.segments):
            raise EditError(
                "Geometry export is unavailable with unknown segment types; "
                "an unchanged round trip is still possible"
            )
        errors = []
        report_progress(progress, "Validating cue placement", 0, len(document.cues))
        for number, cue in enumerate(document.cues, 1):
            finding = inspect_cue(cue, crop, transforms.get(cue.index, Transform()))
            if finding.problem:
                errors.append(f"Cue {cue.index + 1}: {finding.description}")
            report_progress(progress, "Validating cue placement", number, len(document.cues))
        if errors:
            raise EditError(
                "Resolve out-of-canvas cues before export:\n"
                + "\n".join(errors[:12])
                + (f"\n...and {len(errors) - 12} more" if len(errors) > 12 else "")
            )

        # Key by defining WDS segment AND window ID, since windows can be reused for many cues.
        required: dict[tuple[int, int], Rect] = {}
        report_progress(progress, "Preparing subtitle windows", 0, len(document.cues))
        for number, cue in enumerate(document.cues, 1):
            t = transforms.get(cue.index, Transform())
            for p in cue.placements:
                if not p.window.contains(p.rect):
                    raise EditError(
                        f"Cue {cue.index + 1}: source window clips an object; "
                        "cannot expand that window without revealing hidden pixels"
                    )
                rect = p.rect.moved(t.dx - crop.left, t.dy - crop.top)
                key = (p.window_segment, p.reference.window_id)
                required[key] = required[key].union(rect) if key in required else rect
            report_progress(progress, "Preparing subtitle windows", number, len(document.cues))

        # A new WDS can accompany a clear. Include the previous visible rectangles so that
        # decoders that clear by the new window do not leave a moved subtitle behind.
        previous_rects: dict[int, Rect] = {}
        report_progress(
            progress,
            "Preparing subtitle clears",
            0,
            len(document.display_sets),
            unit="presentations",
        )
        for number, display in enumerate(document.display_sets, 1):
            for index in range(display.pcs_segment + 1, display.end_segment):
                segment = document.segments[index]
                if segment.kind != SegmentType.WDS:
                    continue
                for window in parse_windows(segment.payload):
                    old_rect = previous_rects.get(window.window_id)
                    if old_rect is not None:
                        key = (index, window.window_id)
                        required[key] = (
                            required[key].union(old_rect) if key in required else old_rect
                        )
            previous_rects = {}
            if display.cue_index is not None:
                cue = document.cues[display.cue_index]
                t = transforms.get(cue.index, Transform())
                for p in cue.placements:
                    rect = p.rect.moved(t.dx - crop.left, t.dy - crop.top)
                    wid = p.reference.window_id
                    previous_rects[wid] = (
                        previous_rects[wid].union(rect) if wid in previous_rects else rect
                    )
            report_progress(
                progress,
                "Preparing subtitle clears",
                number,
                len(document.display_sets),
                unit="presentations",
            )

        cue_for_pcs = {cue.pcs_segment: cue for cue in document.cues}
        compositions = {ds.pcs_segment: ds.composition for ds in document.display_sets}
        report_progress(
            progress, "Rewriting subtitle geometry", 0, len(document.segments), unit="packets"
        )
        for index, segment in enumerate(document.segments):
            if segment.kind == SegmentType.PCS:
                pcs = compositions[index]
                cue = cue_for_pcs.get(index)
                t = transforms.get(cue.index, Transform()) if cue else Transform()
                objects = tuple(
                    replace(obj, x=obj.x + t.dx - crop.left, y=obj.y + t.dy - crop.top)
                    for obj in pcs.objects
                )
                payload = replace(
                    pcs, width=region.width, height=region.height, objects=objects
                ).to_bytes()
                rewritten[index] = replace(segment, payload=payload)
            elif segment.kind == SegmentType.WDS:
                windows = []
                for window in parse_windows(segment.payload):
                    intersection = window.rect.intersection(region)
                    rect = intersection.moved(-crop.left, -crop.top) if intersection else None
                    need = required.get((index, window.window_id))
                    if need:
                        rect = rect.union(need) if rect else need
                    # Unused windows entirely inside removed bars still need legal dimensions.
                    windows.append(Window(window.window_id, rect or Rect(0, 0, 1, 1)))
                rewritten[index] = replace(segment, payload=encode_windows(tuple(windows)))
            elif segment.kind == SegmentType.ODS and segment.payload[3] & 0x80:
                bw = int.from_bytes(segment.payload[7:9], "big")
                bh = int.from_bytes(segment.payload[9:11], "big")
                if bw > region.width or bh > region.height:
                    raise EditError(
                        f"An encoded bitmap ({bw}x{bh}) exceeds the output canvas. "
                        "Reduce the crop; preserving ODS prevents resizing this bitmap."
                    )
            report_progress(
                progress,
                "Rewriting subtitle geometry",
                index + 1,
                len(document.segments),
                unit="packets",
            )

    output_segments = tuple(rewritten)
    report_progress(progress, "Encoding subtitle packets", 0, len(output_segments), unit="packets")

    def encoded_packets():
        for index, segment in enumerate(output_segments, 1):
            yield segment.to_bytes()
            report_progress(
                progress, "Encoding subtitle packets", index, len(output_segments), unit="packets"
            )

    output = b"".join(encoded_packets())
    # Independent semantic read-back checks references, dimensions and RLE on the final bytes.
    if changed:

        def verifying(update: Progress) -> None:
            if progress is not None:
                progress(replace(update, stage=f"Verifying export ({update.stage.lower()})"))

        check = parse_sup(output, progress=verifying if progress is not None else None)
        report_progress(progress, "Validating exported placement", 0, len(check.cues))
        for number, cue in enumerate(check.cues, 1):
            if inspect_cue(cue, Crop()).problem or any(
                not p.window.contains(p.rect) for p in cue.placements
            ):
                raise EditError("Export read-back validation failed")
            report_progress(progress, "Validating exported placement", number, len(check.cues))
    source = document.segments
    source_ods, source_pds, source_hash = _fingerprints(
        source, "Checking source checksums", progress
    )
    output_ods, output_pds, output_hash = _fingerprints(
        output_segments, "Checking export checksums", progress
    )
    report = ExportReport(
        (document.width, document.height),
        (region.width, region.height),
        len(document.cues),
        sum(t != Transform() for t in transforms.values()),
        sum(
            a.kind == SegmentType.PCS and a != b
            for a, b in zip(source, output_segments, strict=True)
        ),
        sum(
            a.kind == SegmentType.WDS and a != b
            for a, b in zip(source, output_segments, strict=True)
        ),
        all((a.pts, a.dts) == (b.pts, b.dts) for a, b in zip(source, output_segments, strict=True)),
        source_ods == output_ods,
        source_pds == output_pds,
        output_ods,
        output_pds,
        source_hash,
        output_hash,
    )
    return output, report
