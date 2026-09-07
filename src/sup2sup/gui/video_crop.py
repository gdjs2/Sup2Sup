"""Convert a video's crop into subtitle pixels, with an explicit source-size assumption."""

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout,
)

from sup2sup.edit.geometry import Crop, EditError
from sup2sup.video import centered_crop, subtitle_crop_from_video, suggest_video_canvas


class VideoCropDialog(QDialog):
    def __init__(self, subtitle_width, subtitle_height, video_width, video_height, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Match video crop")
        self.setMinimumWidth(510)
        self.subtitle_size = subtitle_width, subtitle_height
        self.video_size = video_width, video_height
        self.subtitle_crop = None
        layout = QVBoxLayout(self)
        explanation = QLabel(
            f"Subtitle canvas: {subtitle_width} × {subtitle_height}\n"
            f"Opened video: {video_width} × {video_height}\n\n"
            "Confirm the video's size before cropping. The suggested margins assume a centered "
            "crop; adjust them if the removed borders were unequal. All inputs below use video pixels."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        form = QFormLayout()
        layout.addLayout(form)
        self.source_width, self.source_height = QSpinBox(), QSpinBox()
        for spin in (self.source_width, self.source_height):
            spin.setRange(1, 65535)
        width, height = suggest_video_canvas(subtitle_width, subtitle_height, video_width, video_height)
        self.source_width.setValue(width)
        self.source_height.setValue(height)
        form.addRow("Original video width", self.source_width)
        form.addRow("Original video height", self.source_height)
        self.margins = []
        for name in ("Remove left", "Remove top", "Remove right", "Remove bottom"):
            spin = QSpinBox()
            spin.setRange(0, 65534)
            self.margins.append(spin)
            form.addRow(name, spin)
        center = QPushButton("Center crop to the opened video's size")
        center.clicked.connect(self._center)
        layout.addWidget(center)
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply subtitle crop")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        for spin in (self.source_width, self.source_height, *self.margins):
            spin.valueChanged.connect(self._update)
        self._center()

    def _center(self):
        try:
            crop = centered_crop(self.source_width.value(), self.source_height.value(), *self.video_size)
        except EditError as exc:
            self._invalid(str(exc))
            return
        for spin, value in zip(self.margins, (crop.left, crop.top, crop.right, crop.bottom)):
            with QSignalBlocker(spin):
                spin.setValue(value)
        self._update()

    def _invalid(self, message):
        self.subtitle_crop = None
        self.result_label.setText(message)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def _update(self, *_args):
        try:
            width, height = self.source_width.value(), self.source_height.value()
            video_crop = Crop(*(spin.value() for spin in self.margins))
            region = video_crop.rectangle(width, height)
            if (region.width, region.height) != self.video_size:
                raise EditError(f"These margins produce {region.width} × {region.height}, but the "
                                f"opened video is {self.video_size[0]} × {self.video_size[1]}.")
            crop = subtitle_crop_from_video(*self.subtitle_size, width, height, video_crop)
            output = crop.rectangle(*self.subtitle_size)
        except EditError as exc:
            self._invalid(str(exc))
            return
        self.subtitle_crop = crop
        scale = width / self.subtitle_size[0]
        self.result_label.setText(
            f"Subtitle crop (L / T / R / B): {crop.left} / {crop.top} / {crop.right} / {crop.bottom}\n"
            f"Exported SUP: {output.width} × {output.height}\n"
            f"1 subtitle pixel = {scale:g} video pixels. Subtitle bitmaps retain their original resolution."
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)

    def accept(self):
        if self.subtitle_crop is not None:
            super().accept()
