"""Choose subtitle tracks and a destination before exporting."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


class ExportTracksDialog(QDialog):
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export subtitle tracks")
        self.resize(600, 440)
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Choose the tracks to export. Only the active track is selected initially. "
            "Selected tracks must pass validation before any output is written."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.tracks = QListWidget()
        for index, track in enumerate(project.tracks):
            doc = track.session.document
            region = track.session.crop.rectangle(doc.width, doc.height)
            item = QListWidgetItem(
                f"{index + 1}. {track.name} · {region.width} × {region.height}"
                f" · {len(doc.cues):,} cues"
            )
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if index == project.active_index else Qt.CheckState.Unchecked
            )
            self.tracks.addItem(item)
        layout.addWidget(self.tracks, 1)
        selection = QHBoxLayout()
        for text, checked in (("Select all", True), ("Select none", False)):
            button = QPushButton(text)
            button.clicked.connect(lambda _=False, value=checked: self._check_all(value))
            selection.addWidget(button)
        layout.addLayout(selection)
        layout.addWidget(QLabel("Destination directory"))
        destination = QHBoxLayout()
        self.destination = QLineEdit()
        self.destination.setPlaceholderText("Choose where to save the selected SUP files")
        destination.addWidget(self.destination, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        destination.addWidget(browse)
        layout.addLayout(destination)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export selected tracks")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.tracks.itemChanged.connect(self._validate)
        self.destination.textChanged.connect(self._validate)
        self._validate()

    @property
    def selected_indices(self):
        return [
            self.tracks.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.tracks.count())
            if self.tracks.item(i).checkState() == Qt.CheckState.Checked
        ]

    @property
    def directory(self):
        return Path(self.destination.text().strip()).expanduser()

    def _check_all(self, checked):
        for index in range(self.tracks.count()):
            self.tracks.item(index).setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )

    def _browse(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Export destination", self.destination.text()
        )
        if directory:
            self.destination.setText(directory)

    def _validate(self, *_args):
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            bool(self.selected_indices) and bool(self.destination.text().strip())
        )

    def accept(self):
        if self.selected_indices and self.destination.text().strip():
            super().accept()
