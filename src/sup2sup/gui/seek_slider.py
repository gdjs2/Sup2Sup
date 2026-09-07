"""A playback slider with absolute clicks and a separate signal for user seeks."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider


class SeekSlider(QSlider):
    seekRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._drag_offset = 0
        self._last_mouse_x = None
        self.setSingleStep(5000)
        self.setPageStep(30000)
        self.setAccessibleName("Playback position")
        self.setToolTip("Click to jump or drag to seek. Arrow keys: 5 seconds; Page Up/Down: 30 seconds.")
        self.actionTriggered.connect(self._action_triggered)

    def _geometry(self):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        style = self.style()
        groove = style.subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                      QStyle.SubControl.SC_SliderGroove, self)
        handle = style.subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                      QStyle.SubControl.SC_SliderHandle, self)
        return option, groove, handle

    def _move_to(self, x):
        option, groove, handle = self._geometry()
        position = round(x - self._drag_offset - groove.x())
        span = max(0, groove.width() - handle.width())
        self.setSliderPosition(QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), position, span, option.upsideDown))

    def _drag_to(self, x):
        if x != self._last_mouse_x:
            self._move_to(x)
            self._last_mouse_x = x

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.minimum() == self.maximum():
            event.ignore()
            return
        _, _, handle = self._geometry()
        on_handle = handle.contains(event.position().toPoint())
        self._last_mouse_x = event.position().x()
        self._drag_offset = (event.position().x() - handle.x() if on_handle
                             else handle.width() / 2)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.setSliderDown(True)
        if not on_handle:
            self._move_to(event.position().x())
        event.accept()

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self._drag_to(event.position().x())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self._drag_to(event.position().x())
            position = self.sliderPosition()
            self.setSliderDown(False)
            self.seekRequested.emit(position)
            event.accept()
        else:
            event.ignore()

    def _action_triggered(self, _action):
        # Keyboard/wheel actions change sliderPosition before valueChanged is emitted.
        # Playback's setValue calls must never feed another seek back to the player.
        if not self.isSliderDown() and self.minimum() != self.maximum():
            self.seekRequested.emit(self.sliderPosition())
