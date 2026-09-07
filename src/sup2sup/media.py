"""Container metadata and lossless PGS extraction through PyAV; no external executables."""

import struct
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av

from .edit.geometry import EditError
from .pgs.parser import MAX_SUP_BYTES, parse_sup
from .progress import report_progress


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    subtitles: tuple[dict, ...]
    skipped_subtitles: int = 0


def probe_video(path, *, progress=None):
    report_progress(progress, "Reading media metadata", 0, 0, unit="")
    with av.open(str(Path(path).resolve())) as container:
        videos = [
            s
            for s in container.streams.video
            if not s.disposition & av.stream.Disposition.attached_pic
        ]
        if not videos:
            raise EditError("The file contains no video stream")
        video = videos[0]
        subtitles = tuple(
            dict(index=s.index, tags=dict(s.metadata))
            for s in container.streams.subtitles
            if s.codec_context.name == "pgssub"
        )
        return MediaInfo(
            video.codec_context.width,
            video.codec_context.height,
            subtitles,
            len(container.streams.subtitles) - len(subtitles),
        )


def extract_pgs(path, stream_index, *, progress=None):
    """Wrap demuxed PGS segments in SUP headers, preserving their encoded payloads.

    Timestamps are relative to the container start (the player's origin), never the
    first subtitle packet. No bitmap decoding/re-encoding occurs during extraction.
    """
    raw = bytearray()
    with av.open(str(Path(path).resolve())) as container:
        stream = next((s for s in container.streams.subtitles if s.index == stream_index), None)
        if stream is None or stream.codec_context.name != "pgssub":
            raise EditError(f"Stream {stream_index} is not a PGS subtitle track")
        origin = Fraction(container.start_time or 0, av.time_base)
        for packet in container.demux(stream):
            report_progress(
                progress, f"Extracting PGS stream {stream_index}", len(raw), 0, unit="bytes"
            )
            if not packet.size:
                continue
            if packet.pts is None:
                raise EditError(f"PGS stream {stream_index} has a packet without a timestamp")
            pts = round((packet.pts * packet.time_base - origin) * 90000)
            dts = (
                round((packet.dts * packet.time_base - origin) * 90000)
                if packet.dts is not None
                else pts
            )
            if pts < 0:
                raise EditError("PGS presentation precedes the video playback origin")
            data = bytes(packet)
            offset = 0
            while offset < len(data):
                if offset + 3 > len(data):
                    raise EditError("Truncated PGS segment header in container")
                kind, length = struct.unpack_from(">BH", data, offset)
                end = offset + 3 + length
                if end > len(data):
                    raise EditError("Truncated PGS segment in container")
                if len(raw) + 13 + length > MAX_SUP_BYTES:
                    raise EditError("Extracted SUP exceeds the 512 MiB input limit")
                raw.extend(struct.pack(">2sII", b"PG", pts & 0xFFFFFFFF, max(0, dts) & 0xFFFFFFFF))
                raw.extend(data[offset:end])
                offset = end
    return parse_sup(bytes(raw), progress=progress)
