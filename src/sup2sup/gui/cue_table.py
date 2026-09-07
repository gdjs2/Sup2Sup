"""A table that reads only visible cells, instead of constructing seven widgets per cue."""

from bisect import bisect_left

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from sup2sup.edit.geometry import Transform
from sup2sup.edit.timeline import format_pts


class CueTableModel(QAbstractTableModel):
    HEADERS = ("Cue", "Start", "End", "Objects", "Forced", "dx / dy", "Crop status")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = None
        self.findings = ()
        self.problems = ()
        self.rows = range(0)
        self.problems_only = False

    def replace(self, session, findings, problems):
        self.beginResetModel()
        self.session, self.findings, self.problems = session, findings, problems
        self.rows = problems if self.problems_only else range(len(findings))
        self.endResetModel()

    def filter_problems(self, enabled):
        if enabled != self.problems_only:
            self.beginResetModel()
            self.problems_only = enabled
            self.rows = self.problems if enabled else range(len(self.findings))
            self.endResetModel()

    def cue_index(self, row):
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def row_for_cue(self, cue_index):
        row = bisect_left(self.rows, cue_index)
        return row if row < len(self.rows) and self.rows[row] == cue_index else None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self.session is None:
            return None
        cue_index = self.rows[index.row()]
        finding = self.findings[cue_index]
        if role == Qt.ItemDataRole.ForegroundRole:
            return QColor("#cf5142") if finding.problem else None
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        cue = self.session.document.cues[cue_index]
        column = index.column()
        if column == 0:
            return str(cue.index + 1)
        if column == 1:
            return format_pts(cue.start_pts)
        if column == 2:
            return format_pts(cue.end_pts)
        if column == 3:
            return str(len(cue.placements))
        if column == 4:
            return "Yes" if cue.forced else ""
        if column == 5:
            t = self.session.transforms.get(cue.index, Transform())
            return f"{t.dx} / {t.dy}"
        return finding.description
