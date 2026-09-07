"""Offscreen GUI and event-loop regression tests; optional when Qt is unavailable."""

import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

QT_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed; run uv sync --extra gui")
class GUISmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from sup2sup.gui.main_window import MainWindow
        self.window = MainWindow()
        self.errors = []
        self.window._error = self.errors.append
        self.directory = tempfile.TemporaryDirectory()

    def tearDown(self):
        if self.window.task:
            self.window._cancel_task()
            self.wait_until(lambda: self.window.task is None)
        self.window.session = None
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def wait_until(self, predicate, timeout=10):
        from PySide6.QtTest import QTest
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(predicate(), "GUI task/event did not complete in time")

    def idle(self):
        self.wait_until(lambda: self.window.task is None)
        self.assertFalse(self.errors, self.errors)

    def load(self, count=1):
        from tests.fixtures import many_cues
        path = Path(self.directory.name) / "track.sup"
        path.write_bytes(many_cues(count))
        self.window.open_path(path)
        self.idle()

    def crop(self):
        self.window.crop_spins[1].setValue(138)
        self.window.crop_spins[3].setValue(138)
        self.window._apply_crop()
        self.idle()

    def test_window_load_edit_preview_and_undo(self):
        self.load()
        self.crop()
        window = self.window
        self.assertTrue(window.sidebar.isEnabled())
        window.table.selectRow(0)
        window._fit_selected()
        self.idle()
        self.assertEqual(window.session.transforms[0].dy, -78)
        self.assertEqual(window.preview.group.pos().y(), -78)
        window.preview_mode.setCurrentIndex(1)
        self.assertEqual(window.preview.group.pos().y(), 0)
        window._undo()
        self.idle()
        self.assertFalse(window.session.transforms)
        self.assertEqual(window.progress_bar.value(), 1000)

    def test_loading_progress_and_model_do_not_build_every_cell(self):
        from PySide6.QtCore import QTimer
        from sup2sup.gui.cue_table import CueTableModel
        from tests.fixtures import many_cues

        path = Path(self.directory.name) / "large.sup"
        path.write_bytes(many_cues(5000))
        heartbeats, progress = [], []
        timer = QTimer()
        timer.setInterval(5)
        timer.timeout.connect(lambda: heartbeats.append(time.monotonic()))
        self.window.show()
        self.app.processEvents()
        timer.start()
        try:
            with patch.object(CueTableModel, "data", autospec=True,
                              side_effect=CueTableModel.data) as cells:
                self.window.open_path(path)
                self.window.task.progress.connect(progress.append)
                self.idle()
                self.assertLess(cells.call_count, 5000)
        finally:
            timer.stop()
        self.assertGreater(len(heartbeats), 1)
        self.assertEqual(self.window.cue_model.rowCount(), 5000)
        self.assertTrue(any(u.stage == "Loading cues" and u.completed == 5000 for u in progress))
        self.assertIn("Loaded 5,000 cues", self.window.progress_label.text())

    def test_checking_runs_off_gui_thread_and_remains_cancellable(self):
        from PySide6.QtCore import QTimer
        from sup2sup.edit.geometry import inspect_cue

        self.load(30)
        gui_thread = threading.get_ident()
        threads, beats, updates = [], [], []
        timer = QTimer()
        timer.setInterval(5)
        timer.timeout.connect(lambda: beats.append(True))

        def slow_check(*args, **kwargs):
            threads.append(threading.get_ident())
            time.sleep(0.004)
            return inspect_cue(*args, **kwargs)

        timer.start()
        try:
            with patch("sup2sup.edit.session.inspect_cue", slow_check):
                self.window.crop_spins[1].setValue(138)
                self.window.crop_spins[3].setValue(138)
                self.window._apply_crop()
                self.window.task.progress.connect(updates.append)
                self.idle()
        finally:
            timer.stop()
        self.assertNotIn(gui_thread, threads)
        self.assertGreater(len(beats), 5)
        self.assertTrue(any(0 < u.completed < u.total for u in updates))
        self.assertEqual(len(self.window.cue_model.problems), 30)

    def test_cancelled_fit_keeps_offsets_and_undo_history(self):
        from sup2sup.edit.geometry import fit_cue

        self.load(40)
        self.crop()
        before = self.window.session
        calls = []

        def slow_fit(*args, **kwargs):
            calls.append(threading.get_ident())
            time.sleep(0.004)
            return fit_cue(*args, **kwargs)

        with patch("sup2sup.edit.session.fit_cue", slow_fit):
            self.window._fit_problems()
            self.wait_until(lambda: len(calls) >= 5)
            self.assertTrue(self.window.cancel_button.isEnabled())
            self.window.cancel_button.click()
            self.idle()
        self.assertIs(self.window.session, before)
        self.assertFalse(self.window.session.transforms)
        self.assertIn("Cancelled", self.window.progress_label.text())
        self.assertTrue(self.window.splitter.isEnabled())
        self.window._undo()
        self.idle()
        self.assertEqual(self.window.session.crop.top, 0)

    def test_cancelled_crop_keeps_prior_crop_and_findings(self):
        from sup2sup.edit.geometry import inspect_cue

        self.load(30)
        before = self.window.session
        calls = []

        def slow_check(*args, **kwargs):
            calls.append(True)
            time.sleep(0.004)
            return inspect_cue(*args, **kwargs)

        with patch("sup2sup.edit.session.inspect_cue", slow_check):
            self.window.crop_spins[1].setValue(138)
            self.window.crop_spins[3].setValue(138)
            self.window._apply_crop()
            self.wait_until(lambda: len(calls) >= 5)
            self.window._cancel_task()
            self.idle()
        self.assertIs(self.window.session, before)
        self.assertEqual(self.window.crop_spins[1].value(), 0)
        self.assertFalse(self.window.cue_model.problems)

    def test_fit_progress_filtered_row_mapping_and_batch_undo(self):
        self.load(4)
        self.crop()
        self.window.problems_only.setChecked(True)
        self.window.table.selectRow(0)
        self.window._fit_selected()
        self.idle()
        self.assertEqual(self.window.cue_model.rowCount(), 3)
        self.assertEqual(self.window.cue_model.cue_index(0), 1)
        self.window.table.selectRow(0)
        self.assertEqual(self.window._indices(), [1])
        self.window._fit_problems()
        self.idle()
        self.assertEqual(self.window.cue_model.rowCount(), 0)
        self.assertIn("3 moved", self.window.progress_label.text())
        self.window._undo()
        self.idle()
        self.assertEqual(self.window.cue_model.problems, (1, 2, 3))
        self.assertEqual(list(self.window.session.transforms), [0])

    def test_failed_load_keeps_previous_document_and_allows_retry(self):
        self.load()
        original = self.window.session
        bad = Path(self.directory.name) / "bad.sup"
        bad.write_bytes(b"invalid")
        self.window.open_path(bad)
        self.wait_until(lambda: self.window.task is None)
        self.assertTrue(self.errors)
        self.errors.clear()
        self.assertIs(self.window.session, original)
        self.assertTrue(self.window.open_sup_action.isEnabled())
        self.load(2)
        self.assertEqual(len(self.window.session.document.cues), 2)

    def test_preview_native_palette_matches_core_renderer(self):
        from PySide6.QtGui import QImage
        from sup2sup.pgs.parser import parse_sup
        from sup2sup.pgs.renderer import render_tiles
        from tests.fixtures import simple

        cue = parse_sup(simple(width=3, height=2)).cues[0]
        self.window.preview.set_cue(cue)
        image = self.window.preview._pixmaps[0][2].toImage().convertToFormat(
            QImage.Format.Format_RGBA8888)
        actual = bytes(image.constBits())
        self.assertEqual(actual, render_tiles(cue)[0].rgba)

    def video_frame(self, width=3840, height=1608):
        from PySide6.QtGui import QColor, QImage
        from PySide6.QtMultimedia import QVideoFrame

        self.window.video_path = Path("cropped-4k.mkv")
        frame = QImage(width, height, QImage.Format.Format_RGB32)
        frame.fill(QColor(20, 40, 60))
        self.window.preview.video_item.videoSink().setVideoFrame(QVideoFrame(frame))
        self.app.processEvents()
        self.assertEqual(self.window._video_dimensions(), (width, height))

    def test_video_crop_dialog_suggests_4k_and_checks_custom_margins(self):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        from sup2sup.edit.geometry import Crop
        from sup2sup.gui.video_crop import VideoCropDialog

        dialog = VideoCropDialog(1920, 1080, 3840, 1608, self.window)
        self.assertEqual((dialog.source_width.value(), dialog.source_height.value()), (3840, 2160))
        self.assertEqual(dialog.subtitle_crop, Crop(top=138, bottom=138))
        self.assertIn("1920 × 804", dialog.result_label.text())
        dialog.margins[1].setValue(200)
        self.assertIsNone(dialog.subtitle_crop)
        self.assertIn("opened video", dialog.result_label.text())
        dialog.margins[3].setValue(352)
        self.assertEqual(dialog.subtitle_crop, Crop(top=100, bottom=176))
        dialog.margins[1].setValue(201)
        dialog.margins[3].setValue(351)
        self.assertIsNone(dialog.subtitle_crop)
        self.assertFalse(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
        self.assertIn("whole pixels", dialog.result_label.text())
        dialog.accept()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_match_video_crop_checks_cues_and_supports_undo(self):
        from PySide6.QtWidgets import QDialog
        from sup2sup.edit.geometry import Crop
        from sup2sup.gui.video_crop import VideoCropDialog

        self.assertFalse(self.window.match_video_crop_button.isEnabled())
        self.load()
        self.assertFalse(self.window.match_video_crop_button.isEnabled())
        self.video_frame()
        self.assertTrue(self.window.match_video_crop_button.isEnabled())
        self.assertTrue(self.window.preview.video_item.isVisible())
        self.assertIn("Video only", self.window.video_label.text())
        self.assertFalse(self.window.preview.group.isVisible())
        with patch.object(VideoCropDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            self.window.match_video_crop_button.click()
        self.idle()
        self.assertEqual(self.window.session.crop, Crop(top=138, bottom=138))
        self.assertEqual(self.window.mapping.currentIndex(), 0)
        self.assertTrue(self.window.preview.video_item.isVisible())
        self.assertTrue(self.window.preview.group.isVisible())
        self.assertEqual(self.window.cue_model.problems, (0,))
        self.assertIn("Subtitle → video: 2×", self.window.video_label.text())
        self.window._undo()
        self.idle()
        self.assertEqual(self.window.session.crop, Crop())
        self.assertFalse(self.window.cue_model.problems)
        self.assertIn("Video only", self.window.video_label.text())

    def test_cancel_video_crop_keeps_session_and_mapping(self):
        from PySide6.QtWidgets import QDialog
        from sup2sup.edit.geometry import inspect_cue
        from sup2sup.gui.video_crop import VideoCropDialog

        self.load(30)
        self.video_frame()
        self.window.mapping.setCurrentIndex(1)
        before = self.window.session
        with patch.object(VideoCropDialog, "exec", return_value=QDialog.DialogCode.Rejected):
            self.window.match_video_crop_button.click()
        self.assertIs(self.window.session, before)
        self.assertEqual(self.window.mapping.currentIndex(), 1)
        calls = []

        def slow_check(*args, **kwargs):
            calls.append(True)
            time.sleep(0.004)
            return inspect_cue(*args, **kwargs)

        with patch.object(VideoCropDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
                patch("sup2sup.edit.session.inspect_cue", slow_check):
            self.window.match_video_crop_button.click()
            self.wait_until(lambda: len(calls) >= 5)
            self.window._cancel_task()
            self.idle()
        self.assertIs(self.window.session, before)
        self.assertEqual(self.window.mapping.currentIndex(), 1)

    def test_4k_preview_renders_scaled_cue_at_video_coordinates(self):
        from PySide6.QtCore import QPointF, QRectF, QSizeF
        from PySide6.QtGui import QImage, QPainter

        self.load()
        self.crop()
        self.video_frame()
        window, preview = self.window, self.window.preview
        self.assertTrue(preview.video_item.isVisible())
        self.assertEqual(preview.video_item.pos(), QPointF(0, 138))
        self.assertEqual(preview.video_item.size(), QSizeF(1920, 804))
        window._fit_problems()
        self.idle()
        # Render the actual scene at the video's resolution, including the video sink.
        image = QImage(3840, 1608, QImage.Format.Format_RGB32)
        image.fill(0)
        painter = QPainter(image)
        try:
            preview.scene().render(painter, QRectF(0, 0, 3840, 1608), preview.sceneRect())
        finally:
            painter.end()
        self.assertGreater(image.pixelColor(1010, 1478).red(), 230)
        self.assertLess(image.pixelColor(990, 1478).red(), 40)
        self.assertLess(image.pixelColor(1010, 1458).red(), 40)
        self.assertEqual(image.pixelColor(100, 100).blue(), 60)
        self.assertEqual(preview._pixmaps[0][2].size().width(), 400)
        # Source comparison still uses the original subtitle position and crop mask.
        window.preview_mode.setCurrentIndex(1)
        self.assertEqual(preview.sceneRect(), QRectF(0, 0, 1920, 1080))
        self.assertEqual(preview.video_item.pos(), QPointF(0, 138))
        self.assertEqual(preview.group.pos(), QPointF(0, 0))

    def test_unmatched_video_stays_visible_and_restores_original_crop_masks(self):
        from PySide6.QtCore import QRectF

        self.load()
        self.crop()
        self.window.preview_mode.setCurrentIndex(1)
        self.video_frame(3840, 1632)
        preview = self.window.preview
        self.assertTrue(preview.video_item.isVisible())
        self.assertFalse(preview.group.isVisible())
        self.assertFalse(preview.masks)
        self.assertEqual(preview.sceneRect(), QRectF(0, 0, 3840, 1632))
        self.assertIn("Set subtitle alignment", self.window.video_label.text())
        self.window.crop_spins[1].setValue(132)
        self.window.crop_spins[3].setValue(132)
        self.window._apply_crop()
        self.idle()
        self.assertTrue(preview.group.isVisible())
        self.assertTrue(preview.masks)
        self.assertEqual(preview.sceneRect(), QRectF(0, 0, 1920, 1080))
        self.assertIn("Subtitle → video: 2×", self.window.video_label.text())

    def test_click_seek_updates_subtitle_preview_without_video(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        self.load(5)
        self.window.show()
        self.app.processEvents()
        slider = self.window.seek
        QTest.mouseClick(slider, Qt.MouseButton.LeftButton,
                         pos=QPoint(slider.width() * 3 // 4, slider.height() // 2))
        self.assertEqual(self.window.displayed_index, 3)
        self.assertEqual(self.window.preview.group.cue_index, 3)

    def test_seek_requests_reach_player_without_feedback_from_playback(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        self.video_frame()
        self.window.seek.setMaximum(100_000)
        self.window.show()
        self.app.processEvents()
        slider = self.window.seek
        with patch.object(self.window.player, "setPosition") as seek:
            self.window._position_changed(20_000)
            seek.assert_not_called()
            QTest.mouseClick(slider, Qt.MouseButton.LeftButton,
                             pos=QPoint(slider.width() * 3 // 4, slider.height() // 2))
            seek.assert_called_once_with(slider.value())
            self.assertAlmostEqual(slider.value(), 75_000, delta=2000)
