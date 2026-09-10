"""Project persistence and safe multi-track edits/exports."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sup2sup.edit.geometry import Crop, EditError
from sup2sup.edit.project import Project, SubtitleTrack
from sup2sup.edit.session import Session
from sup2sup.media import MediaInfo
from sup2sup.pgs.parser import parse_sup, read_sup
from tests.fixtures import simple


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "english.sup"
        self.source.write_bytes(simple())
        self.project = Project()
        self.project.add_sup(self.source)

    def test_roundtrip_multiple_tracks_and_embedded_source_without_video(self):
        video = self.root / "missing.mkv"
        self.project.video = video
        self.project.tracks.append(
            SubtitleTrack(Session(parse_sup(simple()), video), "Japanese", True, 3)
        )
        self.project.set_crop((1920, 1080), Crop(top=138, bottom=138))
        self.project.fit_all(20)
        self.project.active_index = 1
        path = self.root / "movie.sup2sup.json"
        self.project.save(path)
        self.assertFalse(self.project.dirty)
        loaded = Project.load(path)
        self.assertEqual(loaded.video, video)
        self.assertEqual(loaded.active_index, 1)
        self.assertEqual(len(loaded.tracks), 2)
        self.assertEqual(
            loaded.tracks[1].session.transforms, self.project.tracks[1].session.transforms
        )
        self.assertFalse(loaded.dirty)
        self.assertEqual(loaded.tracks[1].session.document.to_bytes(), simple())
        loaded.tracks[0].session.move([0], 1, 0)
        self.assertTrue(loaded.dirty)

    def test_changed_external_source_is_rejected_and_legacy_loads(self):
        path = self.root / "movie.json"
        self.project.tracks[0].session.save(path)
        legacy = Project.load(path)
        self.assertEqual(len(legacy.tracks), 1)
        legacy.save(path, overwrite=True)
        self.source.write_bytes(simple(x=600))
        with self.assertRaisesRegex(EditError, "changed"):
            Project.load(path)

    def test_shared_crop_is_inherited_and_fork_isolated(self):
        candidate = self.project.fork()
        candidate.set_crop((3840, 2160), Crop(top=276, bottom=276))
        candidate.add_sup(self.source)
        self.assertEqual([t.session.crop.top for t in candidate.tracks], [138, 138])
        self.assertEqual(self.project.tracks[0].session.crop, Crop())
        candidate.fit_all(20)
        self.assertFalse(self.project.tracks[0].session.transforms)
        self.assertTrue(
            all(not any(f.problem for f in t.session.findings()) for t in candidate.tracks)
        )

    def test_export_all_collision_names_and_preflight(self):
        self.project.add_sup(self.source)
        self.project.set_crop((1920, 1080), Crop(top=138, bottom=138))
        self.project.tracks[0].session.auto_fit([0], 20)
        output = self.root / "out"
        with self.assertRaises(EditError):
            self.project.export_all(output)
        self.assertFalse(list(output.iterdir()))
        self.project.fit_all(20)
        reports = self.project.export_all(output)
        self.assertEqual(len(reports), 2)
        self.assertEqual(len(list(output.glob("*.sup"))), 2)
        self.assertTrue(all(read_sup(r["output"]).height == 804 for r in reports))
        with self.assertRaises(FileExistsError):
            self.project.export_all(output)
        self.project.export_all(output, overwrite=True)

    def test_inputs_protected_even_with_overwrite(self):
        with self.assertRaises(EditError):
            self.project.save(self.source, overwrite=True)
        self.project.tracks[0].name = "english"
        protected = self.root / "english.cropped.sup"
        protected.write_bytes(simple())
        self.project.add_sup(protected)
        with self.assertRaises(EditError):
            self.project.export_all(self.root, overwrite=True)
        self.assertEqual(protected.read_bytes(), simple())

    def test_invalid_active_track_rejected(self):
        path = self.root / "project.json"
        self.project.save(path)
        data = json.loads(path.read_text())
        data["active_index"] = 4
        path.write_text(json.dumps(data))
        with self.assertRaises(EditError):
            Project.load(path)

    def test_empty_draft_preset_persists_and_cannot_export(self):
        project = Project()
        project.set_crop((1920, 1080), Crop(top=138, bottom=138))
        path = self.root / "draft.json"
        project.save(path)
        loaded = Project.load(path)
        with self.assertRaises(EditError):
            loaded.export_all(self.root / "empty")
        loaded.add_sup(self.source)
        self.assertEqual(loaded.tracks[0].session.crop.top, 138)

    def test_failed_video_import_preserves_project_and_success_deduplicates(self):
        info = MediaInfo(1920, 1080, ({"index": 1}, {"index": 2}))
        video = self.root / "movie.mkv"
        before = self.project._snapshot()
        with (
            patch("sup2sup.edit.project.probe_video", return_value=info),
            patch(
                "sup2sup.edit.project.extract_pgs_tracks",
                side_effect=EditError("broken"),
            ),
        ):
            with self.assertRaises(EditError):
                self.project.import_video(video)
        self.assertEqual(self.project._snapshot(), before)
        with (
            patch("sup2sup.edit.project.probe_video", return_value=info),
            patch(
                "sup2sup.edit.project.extract_pgs_tracks",
                return_value={1: parse_sup(simple()), 2: parse_sup(simple())},
            ) as extract,
        ):
            self.project.import_video(video)
            self.project.import_video(video)
            extract.assert_called_once_with(video, [1, 2], progress=None)
            self.assertEqual(len(self.project.tracks), 3)

    def test_mixed_hd_uhd_shared_crop_and_fractional_failure_are_atomic(self):
        from tests.fixtures import ods, packet, pcs, pds, wds

        native = self.root / "uhd.sup"
        native.write_bytes(
            pcs(width=3840, height=2160)
            + wds(windows=((0, 0, 0, 3840, 2160),))
            + pds()
            + ods()
            + packet(0x80)
            + pcs(objects=(), width=3840, height=2160, pts=270000, state=0, number=1)
            + packet(0x80, pts=270000)
        )
        self.project.add_sup(native)
        self.project.set_crop((3840, 2160), Crop(top=276, bottom=276))
        self.assertEqual([t.session.crop.top for t in self.project.tracks], [138, 276])
        before = self.project._snapshot()
        with self.assertRaisesRegex(EditError, "whole pixels"):
            self.project.set_crop((3840, 2160), Crop(top=277, bottom=277))
        self.assertEqual(self.project._snapshot(), before)

    def test_zero_shared_crop_accepts_different_subtitle_aspects(self):
        from sup2sup.pgs.writer import export_sup

        document = parse_sup(simple())
        session = Session(document, self.source)
        session.set_crop(Crop(top=138, bottom=138))
        session.auto_fit([0])
        cropped, _ = export_sup(document, session.crop, session.transforms)
        source = self.root / "cropped.sup"
        source.write_bytes(cropped)
        self.project.set_crop((1920, 1080), Crop())
        self.project.add_sup(source)
        self.project.set_crop((1920, 804), Crop())
        self.assertEqual([t.session.crop for t in self.project.tracks], [Crop(), Crop()])

    def test_export_subset_ignores_unselected_problems_and_keeps_stable_names(self):
        self.project.add_sup(self.source)
        self.project.set_crop((1920, 1080), Crop(top=138, bottom=138))
        self.project.tracks[1].session.auto_fit([0])
        before = self.project._snapshot()
        output = self.root / "selected"
        reports = self.project.export_tracks(output, [1])
        self.assertEqual(len(reports), 1)
        self.assertEqual(Path(reports[0]["output"]).name, "english.2.cropped.sup")
        self.assertEqual(len(list(output.iterdir())), 1)
        self.assertEqual(self.project._snapshot(), before)
        self.project.tracks[0].session.auto_fit([0])
        self.project.export_tracks(output, [0])
        self.assertEqual(
            {p.name for p in output.iterdir()}, {"english.cropped.sup", "english.2.cropped.sup"}
        )

    def test_selected_export_protects_unselected_sources(self):
        protected = self.root / "english.cropped.sup"
        protected.write_bytes(simple())
        self.project.add_sup(protected)
        with self.assertRaises(EditError):
            self.project.export_tracks(self.root, [0], overwrite=True)
        self.assertEqual(protected.read_bytes(), simple())

    def test_export_selection_must_be_nonempty_and_valid(self):
        output = self.root / "invalid"
        for indices in ([], [-1], [1], [True], ["0"]):
            with self.subTest(indices=indices), self.assertRaises(EditError):
                self.project.export_tracks(output, indices)
        self.assertFalse(output.exists())
