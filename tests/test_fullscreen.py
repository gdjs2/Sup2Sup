"""Pixel-level regressions for full-screen clipping and its review-only status."""

import contextlib
import io
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sup2sup.cli import main
from sup2sup.edit.geometry import Crop, EditError, Transform, inspect_cue
from sup2sup.edit.project import Project, SubtitleTrack
from sup2sup.edit.session import Session
from sup2sup.pgs.parser import parse_sup
from sup2sup.pgs.rle import crop_rle, decode_rle
from sup2sup.pgs.segments import PGSError, Rect, SegmentType
from sup2sup.pgs.writer import export_sup
from sup2sup.progress import OperationCancelled

from tests.fixtures import end, fullscreen, ods, pcs, pds, rle_solid, simple, wds


def literal_rle(rows):
    return b"".join(b"".join(bytes([p]) if p else b"\0\1" for p in row) + b"\0\0"
                    for row in rows)


def patterned_source():
    rows = [bytes(1 + x + y * 16 for x in range(16)) for y in range(12)]
    return fullscreen(width=16, height=12, pixels=literal_rle(rows)), rows


class CropRLETests(unittest.TestCase):
    def test_literal_transparent_and_long_runs_match_pixel_slices(self):
        rng = random.Random(52)
        rows = [bytes(rng.randrange(4) for _ in range(150)) for _ in range(10)]
        for encoded in (literal_rle(rows), literal_rle(rows)[:-2], rle_solid(150, 10),
                        rle_solid(150, 10, 0)):
            original = decode_rle(encoded, 150, 10)
            for _ in range(30):
                x, y = rng.randrange(150), rng.randrange(10)
                rect = Rect(x, y, rng.randint(1, 150 - x), rng.randint(1, 10 - y))
                cropped = crop_rle(encoded, 150, 10, rect)
                expected = b"".join(original[row * 150 + x:row * 150 + rect.right]
                                    for row in range(y, rect.bottom))
                self.assertEqual(decode_rle(cropped, rect.width, rect.height), expected)

    def test_invalid_rectangles_and_malformed_input_are_rejected(self):
        for rect in (Rect(-1, 0, 1, 1), Rect(0, 0, 0, 1), Rect(0, 0, 11, 1)):
            with self.assertRaises(PGSError):
                crop_rle(rle_solid(10, 4), 10, 4, rect)
        with self.assertRaises(PGSError):
            crop_rle(b"\x01", 10, 4, Rect(0, 0, 1, 1))


class FullscreenTests(unittest.TestCase):
    def test_flag_does_not_block_export_and_fit_keeps_manual_offsets(self):
        session = Session(parse_sup(fullscreen()), "test.sup")
        session.set_crop(Crop(top=138, bottom=138))
        session.move([0], 0, -20)
        finding = session.findings()[0]
        self.assertEqual(finding.status, "fullscreen_cropped")
        self.assertTrue(finding.problem)
        self.assertFalse(finding.blocks_export)
        self.assertIn("review", finding.description)
        self.assertEqual(session.auto_fit([0], margin=20), [])
        self.assertEqual(session.transforms, {0: Transform(0, -20)})
        output, report = export_sup(session.document, session.crop, session.transforms)
        self.assertEqual(parse_sup(output).height, 804)
        self.assertEqual(report.cropped_fullscreen_cues, 1)
        self.assertFalse(report.bitmap_data_identical)
        self.assertTrue(report.palette_data_identical)
        self.assertTrue(report.presentation_timestamps_identical)

    def test_unchanged_fullscreen_roundtrip_and_status(self):
        source = fullscreen(fragmented=True)
        doc = parse_sup(source)
        self.assertEqual(inspect_cue(doc.cues[0], Crop()).status, "safe")
        output, report = export_sup(doc)
        self.assertEqual(output, source)
        self.assertEqual(report.cropped_fullscreen_cues, 0)
        self.assertTrue(report.bitmap_data_identical)

    def test_asymmetric_crop_and_moves_keep_exact_palette_indices(self):
        source, rows = patterned_source()
        crop = Crop(2, 2, 3, 1)
        for t in (Transform(), Transform(-1, -1), Transform(5, 1)):
            output, _ = export_sup(parse_sup(source), crop, {0: t})
            cue = parse_sup(output).cues[0]
            p = cue.placements[0]
            x, y = max(2, t.dx), max(2, t.dy)
            right, bottom = min(13, 16 + t.dx), min(11, 12 + t.dy)
            expected = b"".join(row[x - t.dx:right - t.dx]
                                for row in rows[y - t.dy:bottom - t.dy])
            self.assertEqual(p.bitmap.indices, expected)
            self.assertEqual(p.rect, Rect(x - 2, y - 2, right - x, bottom - y))

    def test_fragmented_fullscreen_and_native_4k_without_decoding_images(self):
        for width, height, margin in ((1920, 1080, 138), (3840, 2160, 276)):
            source = fullscreen(width=width, height=height, fragmented=True, flags=0x40)
            with patch("sup2sup.pgs.parser.decode_rle", side_effect=AssertionError("expanded image")):
                doc = parse_sup(source)
                output, report = export_sup(doc, Crop(top=margin, bottom=margin))
                check = parse_sup(output)
            self.assertEqual((check.width, check.height), (width, height - 2 * margin))
            self.assertEqual(check.cues[0].bounds, Rect(0, 0, width, height - 2 * margin))
            self.assertTrue(check.cues[0].forced)
            self.assertEqual(check.cues[0].end_pts, 270000)
            self.assertEqual(report.cropped_fullscreen_cues, 1)

    def test_reused_bitmap_with_fade_and_different_moves(self):
        source, rows = patterned_source()
        doc = parse_sup(source)
        source = b"".join(s.to_bytes() for s in doc.segments[:-2])
        for number in range(1, 4):
            source += pcs(((1, 0, 0, 0, 0, None),), width=16, height=12, state=0,
                          pts=(number + 1) * 90000, number=number, update=0x80)
            source += pds(pts=(number + 1) * 90000, version=number) + end((number + 1) * 90000)
        transforms = {1: Transform(0, -1)}
        crop = Crop(2, 2, 2, 2)
        output, report = export_sup(parse_sup(source), crop, transforms)
        check = parse_sup(output)
        for index, cue in enumerate(check.cues):
            start = 3 if index == 1 else 2
            self.assertEqual(cue.placements[0].bitmap.indices,
                             b"".join(row[2:14] for row in rows[start:start + 8]))
            self.assertEqual(cue.start_pts, (index + 1) * 90000)
        self.assertEqual(sum(s.kind == SegmentType.ODS for s in check.segments), 3)
        self.assertEqual([c.composition.palette_update for c in check.cues], [0, 0, 0, 0x80])
        self.assertEqual([c.placements[0].bitmap.version for c in check.cues], [0, 1, 2, 2])
        self.assertTrue(report.presentation_timestamps_identical)
        self.assertFalse(report.timestamps_identical)
        self.assertTrue(report.palette_data_identical)

    def test_object_redefinition_version_wrap_and_epoch_reset(self):
        source = fullscreen(width=16, height=12, clear=False, version=255)
        for number, state in ((1, 0), (2, 0x80)):
            pts = (number + 1) * 90000
            source += pcs(((1, 0, 0, 0, 0, None),), width=16, height=12, state=state, pts=pts)
            if state:
                source += wds(((0, 0, 0, 16, 12),), pts) + pds(pts=pts)
            source += ods(width=16, height=12, pixels=rle_solid(16, 12, number + 1),
                          version=number - 1, pts=pts) + end(pts)
        output, _ = export_sup(parse_sup(source), Crop(top=2, bottom=2))
        cues = parse_sup(output).cues
        self.assertEqual([c.placements[0].bitmap.version for c in cues], [255, 0, 1])
        for index, cue in enumerate(cues):
            self.assertEqual(cue.placements[0].bitmap.indices, bytes([index + 1]) * 128)

    def test_output_object_is_fragmented_when_payload_exceeds_64k(self):
        rows = [bytes(range(1, 251)) * 2 for _ in range(200)]
        source = fullscreen(width=500, height=200, pixels=literal_rle(rows), fragmented=True)
        output, _ = export_sup(parse_sup(source), Crop(top=10, bottom=10))
        check = parse_sup(output)
        packets = [s for s in check.segments if s.kind == SegmentType.ODS]
        self.assertGreater(len(packets), 1)
        self.assertTrue(all(len(s.payload) <= 65535 for s in packets))
        self.assertEqual(check.cues[0].placements[0].bitmap.indices, b"".join(rows[10:190]))

    def test_mixed_objects_keep_ordinary_clipping_as_a_blocking_error(self):
        source = (pcs(((1, 0, 0, 0, 0, None), (2, 0, 0, 500, 950, None)))
                  + wds() + pds() + ods(width=1920, height=1080) + ods(oid=2) + end())
        doc = parse_sup(source)
        session = Session(doc, "mixed.sup")
        session.set_crop(Crop(top=138, bottom=138))
        finding = session.findings()[0]
        self.assertTrue(finding.fullscreen_cropped)
        self.assertTrue(finding.blocks_export)
        with self.assertRaises(EditError):
            export_sup(doc, session.crop)
        self.assertEqual(session.auto_fit([0], margin=20), [])
        output, _ = export_sup(doc, session.crop, session.transforms)
        normal = parse_sup(output).cues[0].placements[1].bitmap
        self.assertEqual(normal.rle, doc.cues[0].placements[1].bitmap.rle)

    def test_ordinary_oversized_cue_and_fully_displaced_fullscreen_still_block(self):
        for doc, crop, t in ((parse_sup(simple(width=1500)), Crop(left=1000), Transform()),
                             (parse_sup(fullscreen()), Crop(top=138), Transform(1920, 0))):
            self.assertTrue(inspect_cue(doc.cues[0], crop, t).blocks_export)
            with self.assertRaises(EditError):
                export_sup(doc, crop, {0: t})

    def test_source_window_clipping_and_duplicate_object_references(self):
        source = (pcs(((1, 0, 0, 0, 0, None), (1, 1, 0x40, 0, 0, None)), width=16, height=12)
                  + wds(((0, 0, 0, 8, 12), (1, 8, 0, 8, 12))) + pds()
                  + ods(width=16, height=12) + end())
        output, _ = export_sup(parse_sup(source), Crop(top=2, bottom=2))
        placements = parse_sup(output).cues[0].placements
        self.assertNotEqual(placements[0].reference.object_id, placements[1].reference.object_id)
        self.assertEqual([p.rect for p in placements], [Rect(0, 0, 8, 8), Rect(8, 0, 8, 8)])
        self.assertTrue(placements[1].reference.forced)

    def test_cancellation_during_bitmap_crop_and_project_batch(self):
        project = Project()
        session = Session(parse_sup(fullscreen()), "source.sup")
        session.set_crop(Crop(top=138, bottom=138))
        project.tracks = [SubtitleTrack(session, "Full screen")]
        updates = []

        def cancel(update):
            if update.stage == "Cropping full-screen bitmaps":
                updates.append(update)
                if len(updates) == 3:
                    raise OperationCancelled()

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OperationCancelled):
                project.export_all(directory, progress=cancel)
            self.assertFalse(list(Path(directory).iterdir()))
            report = project.export_all(directory)[0]
            self.assertEqual(report["cropped_fullscreen_cues"], 1)
            self.assertEqual(len(list(Path(directory).glob("*.sup"))), 1)

    def test_cli_inspect_and_crop_without_fit(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "source.sup", Path(directory) / "output.sup"
            source.write_bytes(fullscreen())
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["inspect", str(source), "--crop", "0", "138", "0", "138",
                                       "--json"]), 0)
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["fullscreen_cropped"], 1)
            self.assertEqual(report["blocking"], 0)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["crop", str(source), str(output), "--crop",
                                       "0", "138", "0", "138"]), 0)
            self.assertEqual(parse_sup(output.read_bytes()).height, 804)
