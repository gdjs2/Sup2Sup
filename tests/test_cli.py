from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from sup2sup.cli import main
from sup2sup.files import write_bytes
from sup2sup.pgs.parser import read_sup
from sup2sup.video import video_rectangle
from sup2sup.edit.geometry import Crop, EditError
from sup2sup.pgs.segments import Rect

from tests.fixtures import simple


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "movie.sup"
        self.output = Path(self.temp.name) / "cropped.sup"
        self.source.write_bytes(simple())

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = main([str(arg) for arg in args])
        return result, out.getvalue(), err.getvalue()

    def test_inspect_json(self):
        result, out, _ = self.invoke("inspect", self.source, "--crop", 0, 138, 0, 138, "--json")
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(out)["outside"], 1)

    def test_export_and_json_report(self):
        report = Path(self.temp.name) / "report.json"
        result, _, error = self.invoke("crop", self.source, self.output, "--crop", 0, 138, 0, 138,
                                      "--fit", "margin", "--report", report)
        self.assertEqual(result, 0, error)
        self.assertEqual(read_sup(self.output).height, 804)
        self.assertTrue(json.loads(report.read_text())["bitmap_data_identical"])
        self.assertEqual(self.source.read_bytes(), simple())

    def test_invalid_export_never_creates_output(self):
        result, _, error = self.invoke("crop", self.source, self.output, "--crop", 0, 138, 0, 138)
        self.assertEqual(result, 1)
        self.assertIn("Resolve", error)
        self.assertFalse(self.output.exists())

    def test_crop_using_4k_video_margins_preserves_bitmaps(self):
        result, out, error = self.invoke("crop", self.source, self.output,
                                        "--video-source", 3840, 2160, "--crop", 0, 276, 0, 276,
                                        "--fit", "margin", "--margin", 20)
        self.assertEqual(result, 0, error)
        report = json.loads(out)
        self.assertEqual(report["output_size"], [1920, 804])
        self.assertTrue(report["bitmap_data_identical"])
        cue = read_sup(self.output).cues[0]
        self.assertEqual(cue.bounds, Rect(500, 734, 400, 50))

    def test_inspect_4k_margins_and_reject_fractional_crop(self):
        result, out, error = self.invoke("inspect", self.source, "--video-source", 3840, 2160,
                                        "--crop", 0, 276, 0, 276, "--json")
        self.assertEqual(result, 0, error)
        self.assertEqual(json.loads(out)["output_size"], [1920, 804])
        result, _, error = self.invoke("crop", self.source, self.output,
                                      "--video-source", 3840, 2160, "--crop", 0, 277, 0, 277)
        self.assertEqual(result, 1)
        self.assertIn("whole pixels", error)
        self.assertFalse(self.output.exists())

    def test_protect_input_and_existing_output(self):
        result, _, _ = self.invoke("crop", self.source, self.source, "--overwrite")
        self.assertEqual(result, 1)
        self.output.write_bytes(b"keep")
        result, _, _ = self.invoke("crop", self.source, self.output)
        self.assertEqual(result, 1)
        self.assertEqual(self.output.read_bytes(), b"keep")
        result, _, _ = self.invoke("crop", self.source, self.output, "--overwrite")
        self.assertEqual(result, 0)

    def test_report_path_cannot_clobber_output(self):
        result, _, _ = self.invoke("crop", self.source, self.output, "--report", self.output)
        self.assertEqual(result, 1)
        self.assertFalse(self.output.exists())

    def test_atomic_write_no_overwrite(self):
        write_bytes(self.output, b"first")
        with self.assertRaises(FileExistsError):
            write_bytes(self.output, b"second")
        self.assertEqual(self.output.read_bytes(), b"first")
        self.assertFalse(list(Path(self.temp.name).glob("*.tmp")))


class VideoMappingTests(unittest.TestCase):
    def test_original_and_precropped_video(self):
        crop = Crop(top=138, bottom=138)
        self.assertEqual(video_rectangle(1920, 1080, crop, 1920, 1080), Rect(0, 0, 1920, 1080))
        self.assertEqual(video_rectangle(1920, 1080, crop, 1920, 804), Rect(0, 138, 1920, 804))

    def test_mismatched_dimensions_require_explicit_mapping(self):
        crop = Crop(top=138, bottom=138)
        with self.assertRaises(EditError):
            video_rectangle(1920, 1080, crop, 1280, 600)
        self.assertEqual(video_rectangle(1920, 1080, crop, 1280, 600, "cropped"), Rect(0, 138, 1920, 804))


class ProjectCLITests(unittest.TestCase):
    setUp = CLITests.setUp
    invoke = CLITests.invoke

    def test_create_inspect_export_multiple_tracks(self):
        project = Path(self.temp.name) / 'movie.json'
        directory = Path(self.temp.name) / 'exports'
        result, output, error = self.invoke(
            'project-create', project, '--subtitle', self.source, '--subtitle', self.source,
            '--crop', 0, 138, 0, 138, '--fit', '--margin', 20)
        self.assertEqual(result, 0, error)
        self.assertEqual(len(json.loads(output)['tracks']), 2)
        result, output, error = self.invoke('project-inspect', project)
        self.assertEqual(result, 0, error)
        self.assertEqual([t['problems'] for t in json.loads(output)['tracks']], [0, 0])
        result, output, error = self.invoke('project-export', project, directory)
        self.assertEqual(result, 0, error)
        self.assertEqual(len(json.loads(output)), 2)
        self.assertEqual(len(list(directory.glob('*.sup'))), 2)

    def test_detect_crop_requires_video(self):
        result, _, error = self.invoke('project-create', self.output, '--detect-crop')
        self.assertEqual(result, 1)
        self.assertIn('requires --video', error)
        self.assertFalse(self.output.exists())
