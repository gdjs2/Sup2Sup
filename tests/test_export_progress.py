"""Export progress covers work inside each track without changing output or preflight."""

import tempfile
import unittest
from collections import defaultdict
from hashlib import sha256
from pathlib import Path

from sup2sup.edit.geometry import Crop, EditError
from sup2sup.edit.project import Project, SubtitleTrack
from sup2sup.edit.session import Session
from sup2sup.pgs.parser import parse_sup
from sup2sup.pgs.segments import SegmentType
from sup2sup.pgs.writer import export_sup
from sup2sup.progress import OperationCancelled
from tests.fixtures import many_cues


class ExportProgressTests(unittest.TestCase):
    def session(self):
        session = Session(parse_sup(many_cues(12)), "source.sup")
        session.set_crop(Crop(top=138, bottom=138))
        session.auto_fit(list(range(12)), 20)
        return session

    def test_progress_covers_validation_readback_and_checksums(self):
        session = self.session()
        updates = []
        data, report = export_sup(
            session.document, session.crop, session.transforms, progress=updates.append
        )
        self.assertEqual(
            (data, report), export_sup(session.document, session.crop, session.transforms)
        )
        stages = defaultdict(list)
        for update in updates:
            stages[update.stage].append(update)
        for stage, total in (
            ("Validating cue placement", 12),
            ("Verifying export (reading packets)", len(data)),
            ("Verifying export (loading cues)", 12),
            ("Validating exported placement", 12),
            ("Checking source checksums", len(session.document.segments)),
            ("Checking export checksums", len(session.document.segments)),
        ):
            with self.subTest(stage=stage):
                values = stages[stage]
                self.assertTrue(values)
                counts = [u.completed for u in values]
                self.assertEqual((counts[0], counts[-1]), (0, total))
                self.assertEqual(counts, sorted(counts))
                self.assertEqual({u.total for u in values}, {total})
                if stage != "Verifying export (reading packets)":
                    self.assertTrue(any(0 < count < total for count in counts))
        self.assertEqual(report.input_sha256, sha256(session.document.to_bytes()).hexdigest())
        self.assertEqual(report.output_sha256, sha256(data).hexdigest())
        output = parse_sup(data)
        for kind, digest in (
            (SegmentType.ODS, report.ods_sha256),
            (SegmentType.PDS, report.pds_sha256),
        ):
            self.assertEqual(
                digest,
                sha256(
                    b"".join(s.to_bytes() for s in output.segments if s.kind == kind)
                ).hexdigest(),
            )
        self.assertTrue(report.bitmap_data_identical)
        self.assertTrue(report.palette_data_identical)
        self.assertTrue(report.timestamps_identical)

    def test_unchanged_export_also_reports_progress_and_preserves_bytes(self):
        original = many_cues(12)
        updates = []
        data, report = export_sup(parse_sup(original), progress=updates.append)
        self.assertEqual(data, original)
        self.assertEqual(report.input_sha256, report.output_sha256)
        self.assertTrue(any(0 < u.completed < u.total for u in updates))
        self.assertEqual(updates[-1].completed, updates[-1].total)

    def test_validation_errors_still_report_cue_progress(self):
        updates = []
        session = self.session()
        with self.assertRaisesRegex(EditError, "Resolve out-of-canvas cues"):
            export_sup(session.document, session.crop, progress=updates.append)
        self.assertEqual(updates[-1].stage, "Validating cue placement")
        self.assertEqual(updates[-1].completed, 12)
        self.assertTrue(any(0 < u.completed < u.total for u in updates))

    def test_selected_tracks_keep_context_through_nested_verification(self):
        project = Project()
        project.tracks = [SubtitleTrack(self.session(), name) for name in ("A", "B", "C")]
        updates = []
        with tempfile.TemporaryDirectory() as directory:

            def progress(update):
                updates.append(update)
                if update.stage != "Publishing subtitle tracks":
                    self.assertFalse(list(Path(directory).glob("*.sup")))

            reports = project.export_tracks(directory, [1, 2], progress=progress)
            self.assertEqual([r["track"] for r in reports], ["B", "C"])
            self.assertEqual(len(list(Path(directory).glob("*.sup"))), 2)
        for stage in (
            "Validating cue placement",
            "Verifying export (loading cues)",
            "Checking export checksums",
            "Staging subtitle file",
        ):
            values = [u for u in updates if u.stage == stage]
            self.assertEqual(
                {(u.track_number, u.track_total, u.track_name) for u in values},
                {(1, 2, "B"), (2, 2, "C")},
            )
            self.assertEqual(
                [u.track_number for u in values], sorted(u.track_number for u in values)
            )
        self.assertEqual(updates[-1].stage, "Publishing subtitle tracks")
        self.assertEqual((updates[-1].completed, updates[-1].total), (2, 2))

    def test_interrupting_readback_leaves_no_partial_batch(self):
        project = Project()
        project.tracks = [SubtitleTrack(self.session(), name) for name in ("A", "B")]
        before = project._snapshot()

        def cancel(update):
            if (
                update.track_number == 2
                and update.stage == "Verifying export (loading cues)"
                and update.completed >= 3
            ):
                raise OperationCancelled()

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OperationCancelled):
                project.export_all(directory, progress=cancel)
            self.assertFalse(list(Path(directory).iterdir()))
        self.assertEqual(project._snapshot(), before)
