"""Exercise playback seeking with real mouse and keyboard events."""

import importlib.util
import os
import unittest

QT_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")
class SeekSliderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from sup2sup.gui.seek_slider import SeekSlider
        self.slider = SeekSlider()
        self.slider.resize(401, 30)
        self.slider.setRange(0, 6_400_000)
        self.requests = []
        self.slider.seekRequested.connect(self.requests.append)
        self.slider.show()
        self.app.processEvents()

    def tearDown(self):
        self.slider.close()
        self.slider.deleteLater()
        self.app.processEvents()

    def point(self, x):
        from PySide6.QtCore import QPoint
        return QPoint(x, self.slider.height() // 2)

    def test_click_jumps_to_clicked_time_once(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        QTest.mouseClick(self.slider, Qt.MouseButton.LeftButton, pos=self.point(300))
        self.assertEqual(self.requests, [self.slider.value()])
        self.assertAlmostEqual(self.slider.value() / self.slider.maximum(), 0.75, delta=0.02)
        self.assertFalse(self.slider.isSliderDown())
        self.requests.clear()
        QTest.mouseClick(self.slider, Qt.MouseButton.LeftButton, pos=self.point(100))
        self.assertEqual(self.requests, [self.slider.value()])
        self.assertAlmostEqual(self.slider.value() / self.slider.maximum(), 0.25, delta=0.02)

    def test_drag_keeps_grab_offset_and_commits_on_release(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QStyle, QStyleOptionSlider

        self.slider.setValue(3_200_000)
        option = QStyleOptionSlider()
        self.slider.initStyleOption(option)
        handle = self.slider.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                                   QStyle.SubControl.SC_SliderHandle, self.slider)
        start = handle.center()
        start.setX(start.x() + 3)
        QTest.mousePress(self.slider, Qt.MouseButton.LeftButton, pos=start)
        self.assertEqual(self.slider.value(), 3_200_000)
        self.assertTrue(self.slider.isSliderDown())
        QTest.mouseMove(self.slider, self.point(start.x() + 60))
        self.assertGreater(self.slider.value(), 3_200_000)
        self.assertFalse(self.requests)
        QTest.mouseRelease(self.slider, Qt.MouseButton.LeftButton, pos=self.point(start.x() + 60))
        self.assertEqual(self.requests, [self.slider.value()])
        self.assertFalse(self.slider.isSliderDown())

    def test_endpoints_inverted_and_right_to_left(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        for rtl, inverted in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(rtl=rtl, inverted=inverted):
                self.slider.setLayoutDirection(Qt.LayoutDirection.RightToLeft if rtl
                                               else Qt.LayoutDirection.LeftToRight)
                self.slider.setInvertedAppearance(inverted)
                self.slider.setValue(3_200_000)
                QTest.mouseClick(self.slider, Qt.MouseButton.LeftButton, pos=self.point(1))
                self.assertEqual(self.slider.value(), self.slider.maximum() if rtl != inverted else 0)
                QTest.mouseClick(self.slider, Qt.MouseButton.LeftButton, pos=self.point(399))
                self.assertEqual(self.slider.value(), 0 if rtl != inverted else self.slider.maximum())

    def test_clicking_handle_without_moving_preserves_precise_time(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QStyle, QStyleOptionSlider

        self.slider.setValue(60_500)
        option = QStyleOptionSlider()
        self.slider.initStyleOption(option)
        handle = self.slider.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                                   QStyle.SubControl.SC_SliderHandle, self.slider)
        QTest.mouseClick(self.slider, Qt.MouseButton.LeftButton, pos=handle.center())
        self.assertEqual(self.requests, [60_500])

    def test_playback_updates_do_not_seek_and_keyboard_does(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        self.slider.setValue(60_000)
        self.slider.setValue(60_500)
        self.assertFalse(self.requests)
        QTest.keyClick(self.slider, Qt.Key.Key_Right)
        self.assertEqual(self.requests, [65_500])
        QTest.keyClick(self.slider, Qt.Key.Key_PageDown)
        self.assertEqual(self.requests[-1], 35_500)
        QTest.keyClick(self.slider, Qt.Key.Key_Home)
        self.assertEqual(self.requests[-1], 0)
        QTest.keyClick(self.slider, Qt.Key.Key_End)
        self.assertEqual(self.requests[-1], self.slider.maximum())

    def test_empty_range_disabled_slider_and_other_buttons_do_not_seek(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        QTest.mouseClick(self.slider, Qt.MouseButton.RightButton, pos=self.point(300))
        self.slider.setEnabled(False)
        QTest.mouseClick(self.slider, Qt.MouseButton.LeftButton, pos=self.point(300))
        self.slider.setEnabled(True)
        self.slider.setRange(0, 0)
        QTest.mouseClick(self.slider, Qt.MouseButton.LeftButton, pos=self.point(300))
        self.assertFalse(self.requests)
        self.assertFalse(self.slider.isSliderDown())
