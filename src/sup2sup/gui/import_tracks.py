"""Select the optional playback video and PGS tracks before extracting a container."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


class ImportTracksDialog(QDialog):
    def __init__(self, path, info, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import tracks")
        self.resize(600, 420)
        layout = QVBoxLayout(self)
        label = QLabel(str(path))
        label.setWordWrap(True)
        layout.addWidget(label)
        form = QFormLayout()
        self.video = QComboBox()
        self.video.addItem("No video — import subtitles only", None)
        for stream in info.videos:
            tags = stream.get("tags", {})
            self.video.addItem(
                f"Stream {stream['index']} · {stream['width']} × {stream['height']}"
                f" · {stream['codec']} · {tags.get('title', tags.get('language', ''))}",
                stream["index"],
            )
        if info.videos:
            self.video.setCurrentIndex(1)
        form.addRow("Video for playback", self.video)
        layout.addLayout(form)
        layout.addWidget(QLabel("PGS subtitle tracks"))
        self.subtitles = QListWidget()
        for stream in info.subtitles:
            tags = stream.get("tags", {})
            item = QListWidgetItem(
                f"Stream {stream['index']} · {tags.get('language', 'und')}"
                + (f" · {tags['title']}" if tags.get("title") else "")
            )
            item.setData(Qt.ItemDataRole.UserRole, stream["index"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.subtitles.addItem(item)
        layout.addWidget(self.subtitles, 1)
        row = QHBoxLayout()
        for text, checked in (("Select all", True), ("Select none", False)):
            button = QPushButton(text)
            button.clicked.connect(lambda _=False, value=checked: self._check_all(value))
            row.addWidget(button)
        layout.addLayout(row)
        note = QLabel(
            f"{info.skipped_subtitles} non-PGS subtitle tracks cannot be imported."
            if info.skipped_subtitles
            else "Choose one video or none, and any number of PGS tracks."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import selected tracks")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.video.currentIndexChanged.connect(self._validate)
        self.subtitles.itemChanged.connect(self._validate)
        self._validate()

    @property
    def video_stream_index(self):
        return self.video.currentData()

    @property
    def subtitle_indices(self):
        return [
            self.subtitles.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.subtitles.count())
            if self.subtitles.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _check_all(self, checked):
        for index in range(self.subtitles.count()):
            self.subtitles.item(index).setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )

    def _validate(self, *_args):
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            self.video_stream_index is not None or bool(self.subtitle_indices)
        )

    def accept(self):
        if self.video_stream_index is not None or self.subtitle_indices:
            super().accept()
