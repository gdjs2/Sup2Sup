"""Container metadata and lossless PGS extraction through PyAV; no external executables."""

import struct
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av

from .edit.geometry import EditError
from .pgs.parser import MAX_SUP_BYTES, read_sup
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
    """Extract a single track using the same pipeline as a multi-track import."""
    return extract_pgs_tracks(path, [stream_index], progress=progress)[stream_index]


def extract_pgs_tracks(path, stream_indices, *, progress=None):
    """Demux selected PGS tracks together, then parse them in the requested order.

    Temporary SUP files keep raw tracks off the heap while scanning. All handles
    and files are cleaned up on success, cancellation, or failure. Timestamps use
    the container playback origin, never the first subtitle packet's timestamp.
    """
    indices = tuple(dict.fromkeys(stream_indices))
    if not indices:
        return {}
    with tempfile.TemporaryDirectory(prefix="sup2sup-pgs-") as directory:
        paths = {
            index: Path(directory) / f"track-{number}.sup" for number, index in enumerate(indices)
        }
        report_progress(progress, "Extracting PGS tracks", 0, 0, unit="bytes")
        with ExitStack() as stack:
            container = stack.enter_context(av.open(str(Path(path).resolve())))
            available = {s.index: s for s in container.streams.subtitles}
            for index in indices:
                if index not in available or available[index].codec_context.name != "pgssub":
                    raise EditError(f"Stream {index} is not a PGS subtitle track")
            outputs = {
                index: stack.enter_context(target.open("wb")) for index, target in paths.items()
            }
            origin = Fraction(container.start_time or 0, av.time_base)
            completed = 0
            for packet in container.demux(*(available[index] for index in indices)):
                if packet.size:
                    completed += _write_pgs_packet(outputs[packet.stream.index], packet, origin)
                report_progress(
                    progress,
                    "Extracting PGS tracks",
                    completed,
                    0,
                    f"{len(indices)} tracks",
                    unit="bytes",
                )
        # The video is closed and every SUP is complete before decoding any track.
        documents = {}
        for number, (index, target) in enumerate(paths.items(), 1):
            report_progress(
                progress,
                "Loading extracted PGS tracks",
                number - 1,
                len(indices),
                f"Stream {index}",
                unit="tracks",
            )
            documents[index] = read_sup(target, progress=progress)
        report_progress(
            progress, "Loading extracted PGS tracks", len(indices), len(indices), unit="tracks"
        )
        return documents


def _write_pgs_packet(output, packet, origin):
    """Wrap each encoded segment in a SUP header without re-encoding its payload."""
    if packet.pts is None:
        raise EditError(f"PGS stream {packet.stream.index} has a packet without a timestamp")
    pts = round((packet.pts * packet.time_base - origin) * 90000)
    dts = round((packet.dts * packet.time_base - origin) * 90000) if packet.dts is not None else pts
    if pts < 0:
        raise EditError("PGS presentation precedes the video playback origin")
    data = bytes(packet)
    offset = 0
    written = 0
    while offset < len(data):
        if offset + 3 > len(data):
            raise EditError("Truncated PGS segment header in container")
        _kind, length = struct.unpack_from(">BH", data, offset)
        end = offset + 3 + length
        if end > len(data):
            raise EditError("Truncated PGS segment in container")
        if output.tell() + 13 + length > MAX_SUP_BYTES:
            raise EditError("Extracted SUP exceeds the 512 MiB input limit")
        output.write(struct.pack(">2sII", b"PG", pts & 0xFFFFFFFF, max(0, dts) & 0xFFFFFFFF))
        output.write(data[offset:end])
        written += 13 + length
        offset = end
    return written
