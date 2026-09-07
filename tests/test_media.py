"""Real container import through Python libraries with no FFmpeg executable."""

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import av

from sup2sup.edit.geometry import EditError
from sup2sup.edit.project import Project
from sup2sup.media import extract_pgs, probe_video
from sup2sup.pgs.parser import parse_sup
from sup2sup.progress import OperationCancelled
from tests.fixtures import simple


def make_container(path, *, size=(1920, 1080), tracks=2):
    with av.open(io.BytesIO(simple()), format="sup") as source:
        with av.open(str(path), "w") as output:
            video = output.add_stream("mpeg4", rate=1)
            video.width, video.height = size
            video.pix_fmt = "yuv420p"
            subtitles = [output.add_stream_from_template(source.streams[0]) for _ in range(tracks)]
            for index, stream in enumerate(subtitles):
                stream.metadata["language"] = ("eng", "jpn")[index % 2]
            for index in range(5):
                frame = av.VideoFrame(*size, "yuv420p")
                for plane in frame.planes:
                    plane.update(bytes(plane.buffer_size))
                frame.pts = index
                for packet in video.encode(frame):
                    output.mux(packet)
            for packet in video.encode():
                output.mux(packet)
            for packet in source.demux():
                if not packet.size:
                    continue
                for stream in subtitles:
                    copy = av.Packet(bytes(packet))
                    copy.pts, copy.dts, copy.time_base = packet.pts, packet.dts, packet.time_base
                    copy.stream = stream
                    output.mux(copy)


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "movie.mkv"
        make_container(self.path)

    def test_tracks_payloads_and_delayed_timing_without_executables(self):
        with patch.dict("os.environ", {"PATH": ""}):
            info = probe_video(self.path)
            self.assertEqual((info.width, info.height), (1920, 1080))
            self.assertEqual(len(info.subtitles), 2)
            self.assertEqual(info.subtitles[1]["tags"]["language"], "jpn")
            project = Project()
            project.import_video(self.path)
            self.assertEqual(len(project.tracks), 2)
            for track in project.tracks:
                cue = track.session.document.cues[0]
                self.assertEqual((cue.start_pts, cue.end_pts), (90000, 270000))
                # Matroska does not preserve SUP decode timestamps; encoded payloads do survive.
                self.assertEqual(
                    [(s.kind, s.payload) for s in track.session.document.segments],
                    [(s.kind, s.payload) for s in parse_sup(simple()).segments],
                )

    def test_cancel_extraction_and_reject_non_pgs(self):
        def cancel(_):
            raise OperationCancelled()

        with self.assertRaises(OperationCancelled):
            extract_pgs(self.path, 1, progress=cancel)
        with self.assertRaises(EditError):
            extract_pgs(self.path, 0)

    def test_video_without_subtitles(self):
        path = Path(self.temp.name) / "no-subs.mkv"
        make_container(path, tracks=0)
        project = Project()
        project.import_video(path)
        self.assertEqual(project.tracks, [])
        self.assertEqual(project.video, path)
