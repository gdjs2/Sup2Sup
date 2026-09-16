"""An independent installed decoder validates exported pixels and cue clearing."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from sup2sup.edit.geometry import Crop, Transform, fit_cue
from sup2sup.pgs.parser import parse_sup
from sup2sup.pgs.writer import export_sup
from sup2sup.video import subtitle_crop_from_video

from tests.fixtures import end, fullscreen, pcs, pds, rle_solid, simple


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is not installed")
class FFmpegIntegrationTests(unittest.TestCase):
    def frame(self, sup, time, size=(1920, 804)):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sup"
            path.write_bytes(sup)
            width, height = size
            overlay = (f"[1:s]scale={width}:{height}:flags=neighbor[sub];"
                       "[0:v][sub]overlay=eof_action=pass[v]")
            command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                       "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r=1:d=5",
                       "-i", str(path), "-filter_complex", overlay,
                       "-map", "[v]", "-ss", str(time), "-frames:v", "1", "-pix_fmt", "gray",
                       "-f", "rawvideo", "pipe:1"]
            result = subprocess.run(command, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            self.assertEqual(len(result.stdout), width * height)
            return result.stdout

    def test_ffmpeg_renders_moved_bitmap_at_expected_coordinates(self):
        output, _ = export_sup(parse_sup(simple(fragmented=True)), Crop(top=138, bottom=138),
                               {0: Transform(0, -78)})
        pixels = self.frame(output, 2)
        mask = pixels.translate(bytes(255 if i > 128 else 0 for i in range(256)))
        expected = bytearray(1920 * 804)
        for y in range(734, 784):
            expected[y * 1920 + 500:y * 1920 + 900] = b"\xff" * 400
        self.assertEqual(mask, bytes(expected))

    def test_ffmpeg_clears_subtitle_at_original_end_timestamp(self):
        output, _ = export_sup(parse_sup(simple()), Crop(top=138, bottom=138), {0: Transform(0, -78)})
        self.assertLess(max(self.frame(output, 4)), 30)

    def test_1080p_export_scaled_onto_cropped_4k_video(self):
        document = parse_sup(simple(fragmented=True))
        crop = subtitle_crop_from_video(1920, 1080, 3840, 2160, Crop(top=276, bottom=276))
        transform = fit_cue(document.cues[0], crop, margin=20)
        output, report = export_sup(document, crop, {0: transform})
        self.assertTrue(report.bitmap_data_identical)
        pixels = self.frame(output, 2, size=(3840, 1608))
        mask = pixels.translate(bytes(255 if i > 128 else 0 for i in range(256)))
        expected = bytearray(3840 * 1608)
        for y in range(1468, 1568):
            expected[y * 3840 + 1000:y * 3840 + 1800] = b"\xff" * 800
        self.assertEqual(mask, bytes(expected))
        self.assertLess(max(self.frame(output, 4, size=(3840, 1608))), 30)

    def test_fullscreen_crop_discards_pixels_below_picture_and_preserves_clear(self):
        pixels = (rle_solid(1920, 900, 0)
                  + (rle_solid(500, 1, 0)[:-2] + rle_solid(400, 1)[:-2]
                     + rle_solid(1020, 1, 0)) * 100
                  + rle_solid(1920, 80, 0))
        source = fullscreen(pixels=pixels, fragmented=True)
        output, _ = export_sup(parse_sup(source), Crop(top=138, bottom=138))
        rendered = self.frame(output, 2)
        mask = rendered.translate(bytes(255 if i > 128 else 0 for i in range(256)))
        expected = bytearray(1920 * 804)
        for y in range(762, 804):
            expected[y * 1920 + 500:y * 1920 + 900] = b"\xff" * 400
        self.assertEqual(mask, bytes(expected))
        self.assertLess(max(self.frame(output, 4)), 30)

    def test_reused_fullscreen_with_individual_move_during_palette_update(self):
        pixels = (rle_solid(1920, 900, 0)
                  + (rle_solid(500, 1, 0)[:-2] + rle_solid(400, 1)[:-2]
                     + rle_solid(1020, 1, 0)) * 100
                  + rle_solid(1920, 80, 0))
        source = fullscreen(pixels=pixels, clear=False)
        source += (pcs(((1, 0, 0, 0, 0, None),), pts=180000, state=0, number=1, update=0x80)
                   + pds(pts=180000, version=1) + end(180000)
                   + pcs((), pts=360000, state=0, number=2) + end(360000))
        output, _ = export_sup(parse_sup(source), Crop(top=138, bottom=138),
                               {1: Transform(0, -100)})
        mask = self.frame(output, 3).translate(bytes(255 if i > 128 else 0 for i in range(256)))
        expected = bytearray(1920 * 804)
        for y in range(662, 762):
            expected[y * 1920 + 500:y * 1920 + 900] = b"\xff" * 400
        self.assertEqual(mask, bytes(expected))
        self.assertLess(max(self.frame(output, 4)), 30)
