import tempfile
from pathlib import Path
import unittest

from sup2sup.edit.geometry import Crop, EditError, Transform, fit_cue, inspect_cue
from sup2sup.edit.session import Session
from sup2sup.pgs.parser import parse_sup
from sup2sup.pgs.segments import Rect, parse_windows
from sup2sup.pgs.writer import export_sup

from tests.fixtures import end, ods, packet, pcs, pds, simple, wds


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.doc = parse_sup(simple())
        self.cue = self.doc.cues[0]
        self.crop = Crop(0, 138, 0, 138)

    def test_outside_clipped_safe(self):
        self.assertEqual(inspect_cue(self.cue, self.crop).status, "outside")
        self.assertEqual(inspect_cue(self.cue, self.crop, Transform(0, -30)).status, "clipped")
        self.assertEqual(inspect_cue(self.cue, self.crop, Transform(0, -100)).status, "safe")
        self.assertEqual(inspect_cue(self.cue, self.crop).overflow, (0, 0, 0, 58))

    def test_minimal_fit_and_margin(self):
        self.assertEqual(fit_cue(self.cue, self.crop), Transform(0, -58))
        self.assertEqual(fit_cue(self.cue, self.crop, margin=20), Transform(0, -78))
        moved = fit_cue(self.cue, self.crop, Transform(10, -90), 20)
        self.assertEqual(moved, Transform(10, -90))

    def test_fit_all_edges_preserves_object_spacing(self):
        source = pcs(((1, 0, 0, 10, 10, None), (2, 0, 0, 90, 15, None)))
        source += wds() + pds() + ods(width=50, height=20) + ods(oid=2, width=50, height=20) + end()
        cue = parse_sup(source).cues[0]
        t = fit_cue(cue, Crop(30, 40, 0, 0), margin=5)
        self.assertEqual(t, Transform(25, 35))
        output, _ = export_sup(parse_sup(source), Crop(30, 40, 0, 0), {0: t})
        a, b = parse_sup(output).cues[0].placements
        self.assertEqual((b.rect.x - a.rect.x, b.rect.y - a.rect.y), (80, 5))

    def test_invalid_and_impossible_crop(self):
        for crop in (Crop(-1), Crop(top=1080), Crop(left=True)):
            with self.assertRaises(EditError):
                crop.rectangle(1920, 1080)
        with self.assertRaises(EditError):
            fit_cue(self.cue, Crop(left=1800))
        with self.assertRaises(EditError):
            fit_cue(self.cue, Crop(), margin=-1)

    def test_zero_origin_and_half_open_edges(self):
        cue = parse_sup(simple(x=0, y=0)).cues[0]
        self.assertEqual(fit_cue(cue, Crop()), Transform())
        self.assertIsNone(Rect(0, 0, 10, 10).intersection(Rect(10, 0, 2, 2)))


class ExportTests(unittest.TestCase):
    def test_no_op_is_byte_identical(self):
        source = packet(0x99, b"extension") + simple(fragmented=True)
        output, report = export_sup(parse_sup(source))
        self.assertEqual(source, output)
        self.assertEqual(report.pcs_changed, 0)
        self.assertEqual(report.wds_changed, 0)

    def test_only_geometry_changes(self):
        doc = parse_sup(simple(fragmented=True))
        output, report = export_sup(doc, Crop(0, 138, 0, 138), {0: Transform(0, -78)})
        exported = parse_sup(output)
        self.assertEqual((exported.width, exported.height), (1920, 804))
        self.assertEqual(exported.cues[0].bounds.y, 734)
        for before, after in zip(doc.segments, exported.segments):
            self.assertEqual((before.kind, before.pts, before.dts), (after.kind, after.pts, after.dts))
            if before.kind not in (0x16, 0x17):
                self.assertEqual(before.to_bytes(), after.to_bytes())
        self.assertTrue(report.bitmap_data_identical)
        self.assertTrue(report.palette_data_identical)
        self.assertTrue(report.timestamps_identical)
        self.assertEqual(report.pcs_changed, 2)  # Includes the clear PCS.

    def test_export_refuses_unresolved_and_unknown_layout(self):
        with self.assertRaises(EditError):
            export_sup(parse_sup(simple()), Crop(0, 138, 0, 138))
        with self.assertRaises(EditError):
            export_sup(parse_sup(packet(0x99) + simple()), transforms={0: Transform(0, -1)})

    def test_window_shared_by_separately_moved_cues(self):
        source = pcs() + wds(((0, 500, 950, 400, 50),)) + pds() + ods() + end()
        source += pcs(pts=180000, state=0, update=0x80) + pds(pts=180000) + end(180000)
        doc = parse_sup(source)
        output, _ = export_sup(doc, Crop(0, 138, 0, 138),
                               {0: Transform(0, -78), 1: Transform(20, -178)})
        check = parse_sup(output)
        for cue in check.cues:
            self.assertTrue(cue.placements[0].window.contains(cue.bounds))
        self.assertEqual(check.cues[0].placements[0].window, Rect(500, 634, 420, 150))

    def test_clear_window_covers_previous_moved_subtitle(self):
        source = simple(clear=False)
        source += pcs((), pts=270000, state=0) + wds(((0, 500, 950, 400, 50),), 270000) + end(270000)
        output, _ = export_sup(parse_sup(source), Crop(0, 138, 0, 138), {0: Transform(0, -100)})
        windows = [parse_windows(s.payload) for s in parse_sup(output).segments if s.kind == 0x17]
        self.assertTrue(windows[-1][0].rect.contains(Rect(500, 712, 400, 50)))

    def test_source_window_clipping_blocks_expansion(self):
        source = pcs() + wds(((0, 500, 960, 400, 20),)) + pds() + ods() + end()
        doc = parse_sup(source)
        self.assertTrue(doc.warnings)
        with self.assertRaises(EditError):
            export_sup(doc, transforms={0: Transform(1, 0)})

    def test_cropped_object_bitmap_larger_than_new_canvas(self):
        # A legal cropped object whose full bitmap would become too wide for the decoder.
        source = pcs(((1, 0, 0x80, 10, 10, (0, 0, 10, 1)),)) + wds() + pds() + ods(width=1920, height=1) + end()
        with self.assertRaises(EditError):
            export_sup(parse_sup(source), Crop(right=20))

    def test_uhd_canvas(self):
        source = pcs(((1, 0, 0, 1200, 2000, None),), width=3840, height=2160)
        source += wds(((0, 0, 0, 3840, 2160),)) + pds() + ods() + end()
        doc = parse_sup(source)
        crop = Crop(top=276, bottom=276)
        output, _ = export_sup(doc, crop, {0: fit_cue(doc.cues[0], crop, margin=40)})
        self.assertEqual(parse_sup(output).height, 1608)


class SessionTests(unittest.TestCase):
    def test_undo_redo_batch_and_dirty_state(self):
        session = Session(parse_sup(simple()), "input.sup")
        session.set_crop(Crop(top=138, bottom=138))
        session.auto_fit([0], margin=20)
        self.assertEqual(session.transforms[0], Transform(0, -78))
        session.undo()
        self.assertFalse(session.transforms)
        session.undo()
        self.assertFalse(session.dirty)
        session.redo()
        self.assertTrue(session.dirty)
        session.move([0], 10, 0)
        session.redo()
        self.assertEqual(session.transforms[0], Transform(10, 0))

    def test_project_roundtrip_and_source_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sup"
            project = Path(directory) / "edits.json"
            source.write_bytes(simple())
            session = Session(parse_sup(source.read_bytes()), source)
            session.move([0], -10, -100)
            session.save(project)
            self.assertFalse(session.dirty)
            loaded = Session.load(project)
            self.assertEqual(loaded.transforms, session.transforms)
            source.write_bytes(simple(x=501))
            with self.assertRaises(EditError):
                Session.load(project)
