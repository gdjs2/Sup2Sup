import unittest

from sup2sup.edit.timeline import Timeline
from sup2sup.pgs.palette import rgba
from sup2sup.pgs.parser import parse_segments, parse_sup
from sup2sup.pgs.renderer import render_tiles
from sup2sup.pgs.rle import decode_rle
from sup2sup.pgs.segments import PGSError

from tests.fixtures import end, ods, packet, pcs, pds, rle_solid, simple, wds


class RLETests(unittest.TestCase):
    def test_all_run_encodings(self):
        data = bytes([3, 0, 2, 0, 0x82, 4, 0, 0x40, 65, 0, 0xC0, 66, 7, 0, 0])
        self.assertEqual(decode_rle(data, 136, 1), b"\x03" + bytes(2) + b"\x04" * 2
                         + bytes(65) + b"\x07" * 66)

    def test_rows_and_omitted_final_eol(self):
        self.assertEqual(decode_rle(b"\x01\x02\0\0\x03\x04", 2, 2), b"\x01\x02\x03\x04")
        self.assertEqual(decode_rle(rle_solid(400, 20), 400, 20), b"\1" * 8000)

    def test_malformed_runs(self):
        for data in (b"", b"\0", b"\0\x80\1", b"\0\0", b"\0\x82\1", b"\1\0\0\1"):
            with self.subTest(data=data), self.assertRaises(PGSError):
                decode_rle(data, 1, 1)

    def test_dimensions_bound_allocation(self):
        for size in ((0, 1), (1, -1), (65535, 65535)):
            with self.assertRaises(PGSError):
                decode_rle(b"", *size)


class ParserTests(unittest.TestCase):
    def test_round_trip_and_fragmentation(self):
        for fragmented in (False, True):
            source = simple(fragmented=fragmented)
            doc = parse_sup(source)
            self.assertEqual(doc.to_bytes(), source)
            self.assertEqual((doc.width, doc.height), (1920, 1080))
            self.assertEqual((doc.cues[0].start_pts, doc.cues[0].end_pts), (90000, 270000))
            self.assertEqual(doc.cues[0].placements[0].bitmap.indices, b"\1" * 20000)

    def test_palette_snapshots_object_reuse_and_clear(self):
        source = simple(clear=False)
        source += pcs(pts=180000, state=0, number=1, update=0x80)
        source += pds(((1, 81, 240, 90, 128),), pts=180000, version=1) + end(180000)
        source += pcs((), pts=270000, state=0, number=2) + end(270000)
        doc = parse_sup(source)
        self.assertEqual(len(doc.cues), 2)
        self.assertEqual(doc.cues[0].end_pts, 180000)
        self.assertEqual(doc.cues[0].palette[1], (255, 255, 255, 255))
        self.assertEqual(doc.cues[1].palette[1][3], 128)
        self.assertEqual(doc.cues[1].palette[0], (0, 0, 0, 0))
        self.assertIs(doc.cues[0].placements[0].bitmap, doc.cues[1].placements[0].bitmap)

    def test_timeline_boundaries_delay_and_gaps(self):
        doc = parse_sup(simple())
        timeline = Timeline(doc.cues)
        self.assertIsNone(timeline.at_pts(89999))
        self.assertEqual(timeline.at_pts(90000), doc.cues[0])
        self.assertIsNotNone(timeline.at_pts(269999))
        self.assertIsNone(timeline.at_pts(270000))
        self.assertIsNone(timeline.at_milliseconds(1000, 500))
        self.assertIsNotNone(timeline.at_milliseconds(1500, 500))

    def test_open_ended_cue(self):
        doc = parse_sup(simple(clear=False))
        self.assertIsNone(doc.cues[0].end_pts)
        self.assertTrue(doc.warnings)
        self.assertIsNotNone(Timeline(doc.cues).at_pts(1000000))

    def test_pts_wrap_and_equal_timestamps(self):
        source = pcs(pts=0xFFFFFF00) + wds() + pds() + ods() + end()
        source += pcs((), pts=100, state=0) + end(100)
        doc = parse_sup(source)
        self.assertEqual(doc.cues[0].end_pts, (1 << 32) + 100)
        source = simple(clear=False) + pcs((), state=0) + end()
        self.assertIsNone(Timeline(parse_sup(source).cues).at_pts(90000))

    def test_forced_object_crop_and_multiple_windows(self):
        source = pcs(((1, 0, 0xC0, 40, 50, (1, 0, 2, 1)), (2, 1, 0, 70, 50, None)))
        source += wds(((0, 0, 0, 60, 100), (1, 60, 0, 100, 100))) + pds()
        source += ods(width=4, height=1, pixels=b"\0\x01\1\1\0\x01\0\0")
        source += ods(oid=2, width=4, height=1) + end()
        cue = parse_sup(source).cues[0]
        self.assertTrue(cue.forced)
        self.assertEqual(cue.bounds.width, 34)
        tiles = render_tiles(cue)
        self.assertEqual((tiles[0].width, tiles[0].height), (2, 1))
        self.assertEqual(tiles[0].rgba, b"\xff" * 8)

    def test_unknown_segments_are_retained(self):
        source = packet(0x99, b"opaque") + simple()
        doc = parse_sup(source)
        self.assertEqual(doc.to_bytes(), source)
        self.assertTrue(doc.warnings)

    def test_bad_framing(self):
        for source in (b"", b"PG", simple()[:-1], b"XX" + simple()[2:], packet(0x80)):
            with self.subTest(source=source[:20]), self.assertRaises(PGSError):
                parse_sup(source)

    def test_bad_references_and_incomplete_objects(self):
        invalid = [
            pcs() + wds() + pds() + end(),
            pcs() + wds() + ods() + end(),
            pcs() + pds() + ods() + end(),
            pcs() + pcs(),
            pcs() + wds() + pds() + ods()[:-13],
            pcs() + packet(0x15, b"\0\1\0\x40abc") + end(),
            simple(clear=False) + pcs(state=0x80) + end(),
            simple(clear=False) + pcs(pts=1, state=0) + end(),
            simple(clear=False) + pcs(width=1280, state=0) + end(),
        ]
        for source in invalid:
            with self.subTest(source=source[-30:]), self.assertRaises(PGSError):
                parse_sup(source)

    def test_all_truncated_prefixes_reject_or_form_complete_documents(self):
        source = simple(fragmented=True)
        for end_offset in range(len(source)):
            prefix = source[:end_offset]
            try:
                document = parse_sup(prefix)
            except PGSError:
                continue
            self.assertEqual(document.to_bytes(), prefix)
            self.assertEqual(parse_segments(prefix)[-1].kind, 0x80)

    def test_palette_colors_and_alpha(self):
        self.assertEqual(rgba(16, 128, 128, 0), (0, 0, 0, 0))
        self.assertEqual(rgba(235, 128, 128, 255), (255, 255, 255, 255))
        self.assertNotEqual(rgba(100, 200, 80, 64), rgba(100, 200, 80, 64, hd=False))
