"""Video/subtitle scaling, crop conversion, and ambiguous geometry checks without Qt."""

import unittest

from sup2sup.edit.geometry import Crop, EditError
from sup2sup.pgs.segments import Rect
from sup2sup.video import (
    centered_crop, subtitle_crop_from_video, suggest_video_canvas, video_rectangle,
)


class VideoScaleTests(unittest.TestCase):
    def test_1080p_subtitles_on_original_and_cropped_4k(self):
        crop = Crop(top=138, bottom=138)
        self.assertEqual(video_rectangle(1920, 1080, crop, 3840, 2160), Rect(0, 0, 1920, 1080))
        self.assertEqual(video_rectangle(1920, 1080, crop, 3840, 1608), Rect(0, 138, 1920, 804))
        self.assertEqual(video_rectangle(1920, 1080, crop, 1280, 536), Rect(0, 138, 1920, 804))

    def test_asymmetric_crop_and_already_cropped_sup(self):
        crop = subtitle_crop_from_video(1920, 1080, 3840, 2160, Crop(40, 200, 80, 352))
        self.assertEqual(crop, Crop(20, 100, 40, 176))
        self.assertEqual(video_rectangle(1920, 1080, crop, 3720, 1608), Rect(20, 100, 1860, 804))
        self.assertEqual(video_rectangle(1920, 804, Crop(), 3840, 1608), Rect(0, 0, 1920, 804))

    def test_native_4k_subtitles_remain_at_native_resolution(self):
        crop = subtitle_crop_from_video(3840, 2160, 3840, 2160, Crop(top=276, bottom=276))
        self.assertEqual(crop, Crop(top=276, bottom=276))
        self.assertEqual(video_rectangle(3840, 2160, crop, 3840, 1608), Rect(0, 276, 3840, 1608))

    def test_same_aspect_crop_is_ambiguous_when_scaled(self):
        crop = Crop(160, 90, 160, 90)
        with self.assertRaisesRegex(EditError, "same aspect ratio"):
            video_rectangle(1920, 1080, crop, 3840, 2160)
        self.assertEqual(video_rectangle(1920, 1080, crop, 3840, 2160, "source"),
                         Rect(0, 0, 1920, 1080))
        self.assertEqual(video_rectangle(1920, 1080, crop, 3200, 1800, "cropped"),
                         Rect(160, 90, 1600, 900))

    def test_video_dimensions_alone_do_not_apply_a_crop(self):
        with self.assertRaisesRegex(EditError, "Match video crop"):
            video_rectangle(1920, 1080, Crop(), 3840, 1608)

    def test_source_canvas_suggestions(self):
        for sw, sh, vw, vh, expected in (
            (1920, 1080, 3840, 1608, (3840, 2160)),
            (1920, 1080, 3800, 1600, (3840, 2160)),
            (1920, 1080, 1920, 804, (1920, 1080)),
            (1920, 804, 3840, 1608, (3840, 1608)),
            (1920, 1080, 3840, 2160, (3840, 2160)),
        ):
            with self.subTest(video=(vw, vh), subtitle=(sw, sh)):
                self.assertEqual(suggest_video_canvas(sw, sh, vw, vh), expected)

    def test_centered_4k_crop_converts_to_subtitle_pixels(self):
        margins = centered_crop(3840, 2160, 3840, 1608)
        self.assertEqual(margins, Crop(0, 276, 0, 276))
        self.assertEqual(subtitle_crop_from_video(1920, 1080, 3840, 2160, margins),
                         Crop(0, 138, 0, 138))

    def test_fractional_subtitle_pixels_are_never_silently_rounded(self):
        with self.assertRaisesRegex(EditError, "138.5 subtitle pixels"):
            subtitle_crop_from_video(1920, 1080, 3840, 2160, Crop(top=277, bottom=277))
        # The same output height is representable if the actual crop uses these offsets.
        self.assertEqual(subtitle_crop_from_video(1920, 1080, 3840, 2160,
                                                  Crop(top=276, bottom=278)),
                         Crop(top=138, bottom=139))

    def test_fractional_scale_uses_exact_arithmetic(self):
        self.assertEqual(subtitle_crop_from_video(1920, 1080, 1280, 720,
                                                  Crop(top=92, bottom=92)),
                         Crop(top=138, bottom=138))
        with self.assertRaisesRegex(EditError, "whole pixels"):
            subtitle_crop_from_video(1920, 1080, 1280, 720, Crop(top=91))

    def test_incompatible_aspects_and_invalid_dimensions(self):
        with self.assertRaisesRegex(EditError, "same aspect ratio"):
            subtitle_crop_from_video(1920, 1080, 4096, 2160, Crop(top=276))
        for size in (0, -1, 1920.5, True):
            with self.subTest(size=size), self.assertRaises(EditError):
                video_rectangle(1920, 1080, Crop(), size, 2160)
        with self.assertRaises(EditError):
            centered_crop(1920, 1080, 3840, 1608)
        with self.assertRaises(EditError):
            subtitle_crop_from_video(1920, 1080, 3840, 2160, Crop(top=2160))
