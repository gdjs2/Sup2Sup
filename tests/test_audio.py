"""Audio control tests and optional decoding through Qt's real media backend."""

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

QT_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")
class AudioTests(unittest.TestCase):
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
        self.controls = self.window.audio_controls

    def tearDown(self):
        from PySide6.QtCore import QUrl
        self.window.player.stop()
        self.window.player.setSource(QUrl())
        self.window._confirm_discard = lambda: True
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def wait_until(self, predicate):
        from PySide6.QtTest import QTest
        deadline = time.monotonic() + 10
        while not predicate() and not self.errors and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(self.errors, self.errors)
        self.assertTrue(predicate(), "Audio operation did not complete in time")

    def test_volume_mute_and_backend_state_stay_in_sync(self):
        self.assertIs(self.window.player.audioOutput(), self.window.audio)
        self.assertFalse(self.window.audio.isMuted())
        self.controls.volume.setValue(35)
        self.assertAlmostEqual(self.window.audio.volume(), 0.35, places=5)
        self.controls.mute.click()
        self.assertTrue(self.window.audio.isMuted())
        self.assertEqual(self.controls.mute.text(), "Unmute")
        self.controls.volume.setValue(55)
        self.assertTrue(self.window.audio.isMuted())
        self.controls.mute.click()
        self.assertFalse(self.window.audio.isMuted())
        self.assertAlmostEqual(self.window.audio.volume(), 0.55, places=5)
        self.window.audio.setVolume(0.2)
        self.assertEqual(self.controls.volume.value(), 20)
        self.assertEqual(self.controls.volume_label.text(), "20%")

    def test_no_media_and_missing_output_device_are_explicit(self):
        from PySide6.QtMultimedia import QAudioDevice
        self.assertFalse(self.controls.tracks.isEnabled())
        self.assertIn("Open a video", self.controls.tracks.currentText())
        with patch.object(self.controls.devices, "audioOutputs", return_value=[]), \
                patch.object(self.controls.devices, "defaultAudioOutput", return_value=QAudioDevice()):
            self.controls._refresh_outputs()
        self.assertFalse(self.controls.outputs.isEnabled())
        self.assertEqual(self.controls.outputs.currentText(), "No audio output device")

    def test_output_selection_survives_refresh_and_falls_back_if_removed(self):
        devices = self.controls.devices.audioOutputs()
        if not devices:
            self.skipTest("No audio output device available")
        device = devices[-1]
        self.controls.outputs.setCurrentIndex(self.controls.outputs.findData(bytes(device.id()).hex()))
        self.assertEqual(self.window.audio.device().id(), device.id())
        self.controls._refresh_outputs()
        self.assertEqual(self.controls.outputs.currentData(), bytes(device.id()).hex())
        with patch.object(self.controls.devices, "audioOutputs", return_value=[]):
            self.controls._refresh_outputs()
        self.assertIsNone(self.controls._device_id)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is not installed")
    def test_video_audio_decodes_and_track_choice_survives_pause_and_seek(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QAudioBufferOutput, QMediaPlayer
        from PySide6.QtWidgets import QFileDialog

        directory = self.enterContext(tempfile.TemporaryDirectory())
        video = Path(directory) / "audio-preview.mkv"
        result = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "lavfi", "-i", "color=c=black:s=320x180:r=10:d=4",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=4",
            "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "mpeg4",
            "-c:a", "aac", "-metadata:s:a:0", "title=Main dialogue",
            "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "title=Commentary",
            "-metadata:s:a:1", "language=jpn", str(video),
        ], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        # Decode real audio while keeping synthetic test tones inaudible.
        self.window.audio.setMuted(True)
        sink = QAudioBufferOutput(self.window)
        buffers = []
        sink.audioBufferReceived.connect(lambda b: buffers.append(b) if b.isValid() else None)
        self.window.player.setAudioBufferOutput(sink)
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(video), "")):
            self.window._open_video()
        self.wait_until(lambda: len(self.window.player.audioTracks()) == 2
                        and self.window.player.mediaStatus() in (
                            QMediaPlayer.MediaStatus.LoadedMedia,
                            QMediaPlayer.MediaStatus.BufferedMedia))
        self.assertEqual(self.controls.tracks.count(), 2)
        self.assertIn("Main dialogue", self.controls.tracks.itemText(0))
        self.assertIn("Commentary", self.controls.tracks.itemText(1))
        self.assertGreaterEqual(self.window.player.activeAudioTrack(), 0)
        self.window._toggle_play()
        self.wait_until(lambda: len(buffers) >= 3)
        self.assertTrue(any(b.frameCount() > 0 and any(bytes(b.constData())) for b in buffers))
        self.window._toggle_play()
        buffers.clear()
        self.controls.tracks.setCurrentIndex(1)
        self.assertEqual(self.window.player.activeAudioTrack(), 1)
        self.window._toggle_play()
        self.wait_until(lambda: len(buffers) >= 3)
        self.assertTrue(any(b.frameCount() > 0 and any(bytes(b.constData())) for b in buffers))
        self.window._toggle_play()
        self.assertEqual(self.window.player.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.window.player.setPosition(2000)
        self.controls._refresh_tracks()
        self.assertEqual(self.window.player.activeAudioTrack(), 1)
        self.assertEqual(self.controls.tracks.currentData(), 1)
        self.assertTrue(self.window.audio.isMuted())
        self.window.player.stop()
        self.window.player.setSource(QUrl())
        self.app.processEvents()
