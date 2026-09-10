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


def make_container(path, *, size=(1920, 1080), tracks=2, staggered=False, video_count=1):
    with av.open(io.BytesIO(simple()), format="sup") as source:
        with av.open(str(path), "w") as output:
            videos = [output.add_stream("mpeg4", rate=1) for _ in range(video_count)]
            for number, video in enumerate(videos):
                video.width, video.height = size if number == 0 else (640, 360)
                video.pix_fmt = "yuv420p"
                video.metadata["title"] = f"Angle {number + 1}"
            subtitles = [output.add_stream_from_template(source.streams[0]) for _ in range(tracks)]
            for index, stream in enumerate(subtitles):
                stream.metadata["language"] = ("eng", "jpn")[index % 2]
            for video in videos:
                for index in range(5):
                    frame = av.VideoFrame(video.width, video.height, "yuv420p")
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
                for index, stream in enumerate(subtitles):
                    copy = av.Packet(bytes(packet))
                    copy.pts, copy.dts, copy.time_base = packet.pts, packet.dts, packet.time_base
                    if staggered:
                        shift = round(index / copy.time_base)
                        copy.pts += shift
                        copy.dts += shift
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

    def test_project_import_scans_once_then_parses_each_track(self):
        from sup2sup.pgs.parser import read_sup

        make_container(self.path, staggered=True)
        real_open = av.open
        scans = []
        opened = []
        closed = []
        parsed = []

        class ObservedContainer:
            def __init__(self, *args, **kwargs):
                self.container = real_open(*args, **kwargs)
                self.scan_finished = False
                opened.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.container.close()
                closed.append(self)

            def __getattr__(self, name):
                return getattr(self.container, name)

            def demux(self, *streams):
                scans.append(tuple(s.index for s in streams))
                yield from self.container.demux(*streams)
                self.scan_finished = True

        def read_extracted(path, *, progress=None):
            self.assertEqual(scans, [(1, 2)])
            self.assertTrue(opened[-1].scan_finished)
            self.assertEqual(opened, closed)
            parsed.append(Path(path))
            return read_sup(path, progress=progress)

        project = Project()
        with (
            patch("sup2sup.media.av.open", side_effect=ObservedContainer),
            patch("sup2sup.media.read_sup", side_effect=read_extracted),
        ):
            project.import_video(self.path)
        self.assertEqual(len(opened), 2)  # Metadata probe, then a single extraction scan.
        self.assertEqual(len(parsed), 2)
        self.assertEqual(
            [t.session.document.cues[0].start_pts for t in project.tracks], [90000, 180000]
        )
        self.assertEqual(
            [t.session.document.cues[0].end_pts for t in project.tracks], [270000, 360000]
        )
        self.assertTrue(all(not p.parent.exists() for p in parsed))

    def test_cancel_during_scan_cleans_files_without_parsing(self):
        from sup2sup.media import extract_pgs_tracks

        real_temporary = tempfile.TemporaryDirectory
        directories = []

        def temporary(**kwargs):
            result = real_temporary(**kwargs)
            directories.append(Path(result.name))
            return result

        def cancel(update):
            if update.stage == "Extracting PGS tracks" and update.completed > 0:
                raise OperationCancelled()

        with (
            patch("sup2sup.media.tempfile.TemporaryDirectory", side_effect=temporary),
            patch("sup2sup.media.read_sup") as read,
        ):
            with self.assertRaises(OperationCancelled):
                extract_pgs_tracks(self.path, [1, 2], progress=cancel)
            read.assert_not_called()
        self.assertTrue(directories)
        self.assertTrue(all(not p.exists() for p in directories))

    def test_later_parse_failure_keeps_project_and_cleans_files(self):
        from sup2sup.pgs.parser import read_sup

        project = Project()
        source = Path(self.temp.name) / "external.sup"
        source.write_bytes(simple())
        project.add_sup(source)
        before = project._snapshot()
        paths = []

        def read_extracted(path, *, progress=None):
            paths.append(Path(path))
            if len(paths) == 2:
                raise EditError("Broken second track")
            return read_sup(path, progress=progress)

        with patch("sup2sup.media.read_sup", side_effect=read_extracted):
            with self.assertRaisesRegex(EditError, "Broken second track"):
                project.import_video(self.path)
        self.assertEqual(project._snapshot(), before)
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(not p.parent.exists() for p in paths))

    def test_empty_selection_does_not_open_video(self):
        from sup2sup.media import extract_pgs_tracks

        with patch("sup2sup.media.av.open") as opened:
            self.assertEqual(extract_pgs_tracks(self.path, []), {})
            opened.assert_not_called()

    def test_selected_video_and_subtitle_tracks_persist(self):
        make_container(self.path, video_count=2)
        info = probe_video(self.path)
        self.assertEqual([v["index"] for v in info.videos], [0, 1])
        self.assertEqual(info.videos[1]["playback_index"], 1)
        project = Project()
        project.import_video(self.path, video_stream_index=1, subtitle_indices=[3])
        self.assertEqual(project.video_stream_index, 1)
        self.assertEqual(project.video_track_index, 1)
        self.assertEqual([t.stream_index for t in project.tracks], [3])
        path = Path(self.temp.name) / "project.json"
        project.save(path)
        loaded = Project.load(path)
        self.assertEqual(loaded.video_stream_index, 1)
        self.assertEqual(loaded.video_track_index, 1)
        self.assertEqual(loaded.video, self.path)

    def test_subtitles_only_preserves_video_and_video_only_skips_extraction(self):
        project = Project()
        original_video = Path(self.temp.name) / "other.mkv"
        project.video = original_video
        project.video_stream_index, project.video_track_index = 4, 1
        project.import_video(self.path, video_stream_index=None, subtitle_indices=[2])
        self.assertEqual(project.video, original_video)
        self.assertEqual(project.video_stream_index, 4)
        self.assertEqual([t.stream_index for t in project.tracks], [2])
        with patch("sup2sup.edit.project.extract_pgs_tracks") as extract:
            project.import_video(self.path, video_stream_index=0, subtitle_indices=[])
            extract.assert_not_called()
        self.assertEqual(project.video, self.path)
        self.assertEqual(project.video_track_index, 0)
        self.assertEqual(len(project.tracks), 1)

    def test_invalid_selection_does_not_change_project(self):
        project = Project()
        before = project._snapshot()
        for selection in ({"video_stream_index": 42}, {"subtitle_indices": [0]}):
            with self.assertRaises(EditError):
                project.import_video(self.path, **selection)
            self.assertEqual(project._snapshot(), before)

    def test_track_context_survives_nested_parser_progress(self):
        from sup2sup.media import extract_pgs_tracks

        updates = []
        extract_pgs_tracks(self.path, [1, 2], progress=updates.append)
        parsing = [u for u in updates if u.stage == "Loading cues"]
        self.assertEqual({u.track_number for u in parsing}, {1, 2})
        self.assertTrue(all(u.track_total == 2 and u.track_name for u in parsing))
