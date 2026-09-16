"""Large subtitle tracks keep compressed objects and decode a bounded preview cache."""

import unittest
from unittest.mock import patch

from sup2sup.edit.geometry import Crop
from sup2sup.edit.session import Session
from sup2sup.pgs.parser import _decode_bitmap, parse_sup
from sup2sup.pgs.rle import decode_rle, validate_rle
from sup2sup.pgs.segments import PGSError
from sup2sup.pgs.writer import export_sup
from sup2sup.progress import OperationCancelled

from tests.fixtures import end, ods, pcs, pds, rle_solid, wds


def changing_bitmaps(count, width, height):
    """New object versions, including version wrap, retained by successive cues."""
    result = bytearray()
    for index in range(count):
        pts = (index + 1) * 90_000
        result.extend(pcs(((1, 0, 0, 0, 140, None),), pts=pts,
                          state=0x80 if index == 0 else 0, number=index))
        if index == 0:
            result.extend(wds() + pds())
        result.extend(ods(width=width, height=height, version=index % 256, pts=pts,
                          pixels=rle_solid(width, height, index % 250 + 1)))
        result.extend(end(pts))
    pts = (count + 1) * 90_000
    result.extend(pcs((), pts=pts, state=0, number=count) + end(pts))
    return bytes(result)


class BitmapMemoryTests(unittest.TestCase):
    def setUp(self):
        _decode_bitmap.cache_clear()
        self.addCleanup(_decode_bitmap.cache_clear)

    def test_more_than_512_mib_loads_checks_and_exports_without_expanding_bitmaps(self):
        source = changing_bitmaps(360, 1920, 800)
        with patch("sup2sup.pgs.parser.decode_rle",
                   side_effect=AssertionError("Import/export must not expand bitmap pixels")):
            document = parse_sup(source)
            bitmaps = [cue.placements[0].bitmap for cue in document.cues]
            self.assertGreater(sum(b.width * b.height for b in bitmaps), 512 * 1024 * 1024)
            self.assertLess(sum(len(b.rle) for b in bitmaps), 2 * 1024 * 1024)
            self.assertEqual(len(bitmaps), 360)
            self.assertEqual(document.to_bytes(), source)
            session = Session(document, "synthetic.sup")
            session.set_crop(Crop(top=138, bottom=138))
            self.assertFalse(any(f.problem for f in session.findings()))
            output, report = export_sup(document, session.crop)
            self.assertTrue(report.bitmap_data_identical)
            self.assertTrue(report.timestamps_identical)
            self.assertEqual(report.output_size, (1920, 804))
            exported = parse_sup(output)
            self.assertEqual(exported.cues[-1].bounds.y, 2)
        self.assertEqual(bitmaps[0].indices, b"\x01" * (1920 * 800))
        self.assertEqual(bitmaps[-1].indices, bytes([110]) * (1920 * 800))

    def test_preview_cache_evicts_and_redecodes_older_cues(self):
        document = parse_sup(changing_bitmaps(6, 32, 2))
        self.assertEqual(_decode_bitmap.cache_info().currsize, 0)
        for index, cue in enumerate(document.cues):
            self.assertEqual(cue.placements[0].bitmap.indices, bytes([index + 1]) * 64)
        self.assertEqual(_decode_bitmap.cache_info().currsize, 4)
        self.assertEqual(_decode_bitmap.cache_info().misses, 6)
        self.assertEqual(document.cues[0].placements[0].bitmap.indices, b"\x01" * 64)
        self.assertEqual(_decode_bitmap.cache_info().misses, 7)
        self.assertEqual(_decode_bitmap.cache_info().currsize, 4)

    def test_cache_is_shared_across_tracks(self):
        source = changing_bitmaps(1, 32, 2)
        first, second = parse_sup(source), parse_sup(source)
        self.assertIs(first.cues[0].placements[0].bitmap.indices,
                      second.cues[0].placements[0].bitmap.indices)
        self.assertEqual(_decode_bitmap.cache_info().currsize, 1)

    def test_corrupt_unused_bitmap_fails_during_import(self):
        source = pcs(()) + wds() + pds() + ods(width=3, height=1, pixels=b"\0\0") + end()
        with self.assertRaisesRegex(PGSError, r"Segment 4 \(0x15\): RLE row"):
            parse_sup(source)
        self.assertEqual(_decode_bitmap.cache_info().currsize, 0)

    def test_validation_checks_rows_without_pixel_allocation(self):
        encoded = rle_solid(1920, 1080)
        with patch("sup2sup.pgs.rle.bytearray", create=True) as allocate:
            validate_rle(encoded, 1920, 1080)
            allocate.assert_not_called()

    def test_validation_retains_cancellation_and_dimension_limits(self):
        def cancel():
            raise OperationCancelled()

        with self.assertRaises(OperationCancelled):
            validate_rle(rle_solid(1920, 100), 1920, 100, checkpoint=cancel)
        for dimensions in ((0, 1), (1, -1), (65535, 65535)):
            with self.subTest(dimensions=dimensions), self.assertRaises(PGSError):
                validate_rle(b"", *dimensions)

    def test_validation_and_decoding_reject_the_same_truncated_data(self):
        encoded = rle_solid(100, 3)
        for length in range(len(encoded) + 1):
            data = encoded[:length]
            try:
                decode_rle(data, 100, 3)
            except PGSError as error:
                with self.subTest(length=length), self.assertRaises(PGSError) as validation:
                    validate_rle(data, 100, 3)
                self.assertEqual(str(validation.exception), str(error))
            else:
                validate_rle(data, 100, 3)
