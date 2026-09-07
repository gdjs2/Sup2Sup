from pathlib import Path
import tempfile
import unittest

from sup2sup.edit.geometry import Crop, Transform
from sup2sup.edit.session import Session
from sup2sup.pgs.parser import parse_sup, read_sup
from sup2sup.pgs.rle import decode_rle
from sup2sup.progress import OperationCancelled

from tests.fixtures import many_cues, rle_solid


class ProgressTests(unittest.TestCase):
    def test_loading_reports_real_cue_counts_and_preserves_bytes(self):
        updates = []
        data = many_cues(25)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "track.sup"
            path.write_bytes(data)
            document = read_sup(path, progress=updates.append)
        self.assertEqual(document.to_bytes(), data)
        loading = [u for u in updates if u.stage == "Loading cues"]
        self.assertEqual((loading[0].completed, loading[-1].completed), (0, 25))
        self.assertEqual({u.total for u in loading}, {25})
        self.assertEqual([u.completed for u in loading], sorted(u.completed for u in loading))
        self.assertIn("Reading file", {u.stage for u in updates})

    def test_loading_can_be_cancelled(self):
        def cancel(update):
            if update.stage == "Loading cues" and update.completed >= 3:
                raise OperationCancelled()

        with self.assertRaises(OperationCancelled):
            parse_sup(many_cues(10), progress=cancel)

    def test_checks_and_fixes_report_counts_and_failures(self):
        session = Session(parse_sup(many_cues(6)), "source.sup")
        session.set_crop(Crop(top=138, bottom=138))
        updates = []
        findings = session.findings(progress=updates.append)
        self.assertEqual(sum(f.problem for f in findings), 6)
        self.assertEqual(updates[-1].detail, "6 problems")
        self.assertEqual(updates[-1].completed, 6)
        session.auto_fit(list(range(6)), 20, progress=updates.append)
        self.assertEqual(updates[-1].detail, "6 moved; 0 could not fit")
        self.assertEqual(updates[-1].total, 6)
        session.auto_fit(list(range(6)), 1000, progress=updates.append)
        self.assertEqual(updates[-1].detail, "0 moved; 6 could not fit")

    def test_cancelled_batch_does_not_commit_partial_offsets(self):
        session = Session(parse_sup(many_cues(8)), "source.sup")
        session.set_crop(Crop(top=138, bottom=138))
        before = session._snapshot()

        def cancel(update):
            if update.completed == 3:
                raise OperationCancelled()

        with self.assertRaises(OperationCancelled):
            session.auto_fit(list(range(8)), 20, progress=cancel)
        self.assertEqual(session._snapshot(), before)
        session.undo()
        self.assertEqual(session.crop, Crop())

    def test_fork_isolated_and_keeps_one_batch_undo(self):
        original = Session(parse_sup(many_cues(4)), "source.sup")
        original.set_crop(Crop(top=138, bottom=138))
        candidate = original.fork()
        self.assertIs(candidate.document, original.document)
        candidate.auto_fit(list(range(4)), 20)
        self.assertEqual(candidate.transforms[0], Transform(0, -78))
        self.assertFalse(original.transforms)
        candidate.undo()
        self.assertFalse(candidate.transforms)
        self.assertEqual(candidate.crop, original.crop)

    def test_large_bitmap_has_cancellation_checkpoints(self):
        def cancel():
            raise OperationCancelled()

        with self.assertRaises(OperationCancelled):
            decode_rle(rle_solid(1920, 100), 1920, 100, checkpoint=cancel)
