import json
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QItemSelection, QItemSelectionModel, Qt, QUrl, Slot
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from sup2sup.edit.geometry import Crop, EditError, Transform
from sup2sup.edit.project import Project
from sup2sup.edit.timeline import Timeline, format_pts
from sup2sup.files import same_path, write_bytes
from sup2sup.pgs.writer import export_sup
from sup2sup.progress import Progress
from sup2sup.video import suggest_video_canvas, video_rectangle

from .audio_controls import AudioControls
from .cue_table import CueTableModel
from .preview import Preview
from .seek_slider import SeekSlider
from .tasks import Task
from .video_crop import VideoCropDialog


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sup2Sup")
        self.resize(1400, 950)
        self.project = Project()
        self._track_results = {}
        self.session = None
        self.project_path = None
        self.video_path = None
        self.timeline = Timeline(())
        self.displayed_index = None
        self.task = None
        self._task_callback = None
        self._task_cancellable = True
        self._cancel_requested = False
        self._last_progress = None
        self._loading_ui = False
        self._pending_seek = None
        self._video_alignment_pending = False
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.7)
        self.audio.setMuted(False)
        self.player.setAudioOutput(self.audio)
        self._build_ui()
        self.player.setVideoOutput(self.preview.video_item)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._playback_changed)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.tracksChanged.connect(lambda: self.player.setActiveSubtitleTrack(-1))
        self.player.errorOccurred.connect(lambda _error, message: self._error(f"Video: {message}"))
        self.preview.video_item.nativeSizeChanged.connect(self._map_video)
        self.preview.cueMoved.connect(self._dragged)
        self.preview.dragStarted.connect(self.player.pause)
        self._set_edit_enabled(False)
        self.statusBar().showMessage(
            "New project — import a video or one or more PGS .sup tracks to begin."
        )

    def _action(self, text, callback, shortcut=None):
        action = QAction(text, self)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(shortcut)
        return action

    def _button(self, text, callback):
        button = QPushButton(text)
        button.clicked.connect(callback)
        return button

    def _build_ui(self):
        toolbar = QToolBar("Files", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self.new_action = self._action("New project", self._new_project, "Ctrl+N")
        self.export_all_action = self._action(
            "Export all tracks…", self._export_all, "Ctrl+Shift+E"
        )
        self.open_sup_action = self._action(
            "Import SUP files", self._open_sup, QKeySequence.StandardKey.Open
        )
        self.open_project_action = self._action("Open project", self._open_project)
        self.open_video_action = self._action("Import video + PGS tracks", self._open_video)
        self.save_action = self._action(
            "Save project", self._save_project, QKeySequence.StandardKey.Save
        )
        self.export_action = self._action("Export SUP", self._export, "Ctrl+E")
        self.undo_action = self._action("Undo", self._undo, QKeySequence.StandardKey.Undo)
        self.redo_action = self._action("Redo", self._redo, QKeySequence.StandardKey.Redo)
        for action in (
            self.new_action,
            self.open_sup_action,
            self.open_project_action,
            self.open_video_action,
            self.save_action,
            self.export_all_action,
            self.export_action,
            self.undo_action,
            self.redo_action,
        ):
            toolbar.addAction(action)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        self.file_label = QLabel("No subtitle loaded")
        self.file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        track_controls = QHBoxLayout()
        track_controls.addWidget(QLabel("Subtitle track"))
        self.track_selector = QComboBox()
        self.track_selector.currentIndexChanged.connect(self._switch_track)
        track_controls.addWidget(self.track_selector, 1)
        self.remove_track_button = self._button("Remove track", self._remove_track)
        track_controls.addWidget(self.remove_track_button)
        self.remove_video_button = self._button("Remove video", self._remove_video)
        track_controls.addWidget(self.remove_video_button)
        outer.addLayout(track_controls)
        outer.addWidget(self.file_label)
        self.progress_panel = QWidget()
        progress_layout = QHBoxLayout(self.progress_panel)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        self.progress_label = QLabel()
        self.progress_label.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(200)
        self.cancel_button = self._button("Cancel", self._cancel_task)
        progress_layout.addWidget(self.progress_label, 1)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.cancel_button)
        outer.addWidget(self.progress_panel)
        self.progress_panel.hide()
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(self.splitter, 1)
        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.splitter.addWidget(main)
        self.sidebar = QWidget()
        self.sidebar.setMinimumWidth(285)
        self.sidebar.setMaximumWidth(380)
        side = QVBoxLayout(self.sidebar)
        self.splitter.addWidget(self.sidebar)
        self.splitter.setStretchFactor(0, 1)

        preview_controls = QHBoxLayout()
        self.preview_mode = QComboBox()
        self.preview_mode.addItems(["Cropped / edited", "Original + crop mask"])
        self.preview_mode.currentIndexChanged.connect(self._refresh_preview)
        self.mapping = QComboBox()
        self.mapping.addItems(
            ["Auto video mapping", "Video is original canvas", "Video is already cropped"]
        )
        self.mapping.setToolTip(
            "Auto accepts matching aspect ratios, including 1080p subtitles on 4K video. "
            "Use Match video crop to convert video margins into subtitle pixels."
        )
        self.mapping.currentIndexChanged.connect(self._map_video)
        preview_controls.addWidget(self.preview_mode)
        preview_controls.addWidget(self.mapping)
        self.match_video_crop_button = self._button("Match video crop…", self._match_video_crop)
        preview_controls.addWidget(self.match_video_crop_button)
        self.detect_crop_button = self._button("Detect project crop…", self._detect_project_crop)
        self.detect_crop_button.setToolTip(
            "Suggest a centered crop from video dimensions against 1080p / 2160p. "
            "Does not scan black bars inside frames."
        )
        preview_controls.addWidget(self.detect_crop_button)
        main_layout.addLayout(preview_controls)
        self.vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        main_layout.addWidget(self.vertical_splitter, 1)
        self.preview = Preview()
        self.vertical_splitter.addWidget(self.preview)
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        self.vertical_splitter.addWidget(bottom)
        self.vertical_splitter.setSizes([500, 250])
        self.video_label = QLabel("Video optional — cue previews also work on a black canvas.")
        self.video_label.setWordWrap(True)
        bottom_layout.addWidget(self.video_label)
        transport = QHBoxLayout()
        self.play = self._button("Play", self._toggle_play)
        self.play.setEnabled(False)
        transport.addWidget(self.play)
        self.seek = SeekSlider()
        self.seek.setRange(0, 0)
        self.seek.seekRequested.connect(self._seek_to)
        self.seek.sliderMoved.connect(
            lambda position: self.time_label.setText(format_pts(position * 90))
        )
        transport.addWidget(self.seek, 1)
        self.time_label = QLabel("00:00:00.000")
        transport.addWidget(self.time_label)
        bottom_layout.addLayout(transport)
        self.audio_controls = AudioControls(self.player, self.audio, self)
        bottom_layout.addWidget(self.audio_controls)
        review = QHBoxLayout()
        self.problems_only = QCheckBox("Problems only")
        self.problems_only.toggled.connect(self._filter)
        review.addWidget(self.problems_only)
        review.addWidget(self._button("Previous cue", lambda: self._navigate(-1)))
        review.addWidget(self._button("Next cue", lambda: self._navigate(1)))
        review.addWidget(self._button("Next problem", lambda: self._navigate(1, True)))
        bottom_layout.addLayout(review)
        self.table = QTableView()
        self.cue_model = CueTableModel(self)
        self.table.setModel(self.cue_model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((55, 115, 115, 65, 60, 95)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.selectionModel().selectionChanged.connect(self._selected)
        bottom_layout.addWidget(self.table, 1)

        crop_box = QGroupBox("Crop margins (subtitle pixels)")
        crop_form = QFormLayout(crop_box)
        self.crop_spins = []
        for name in ("Left", "Top", "Right", "Bottom"):
            spin = QSpinBox()
            spin.setRange(0, 65534)
            self.crop_spins.append(spin)
            crop_form.addRow(name, spin)
        crop_form.addRow(self._button("Apply crop to this track", self._apply_crop))
        crop_form.addRow(self._button("Apply crop to all tracks", self._apply_project_crop))
        self.output_label = QLabel()
        crop_form.addRow(self.output_label)
        side.addWidget(crop_box)
        centered_box = QGroupBox("Centered SUP size (subtitle pixels)")
        centered_form = QFormLayout(centered_box)
        self.target_width, self.target_height = QSpinBox(), QSpinBox()
        for spin in (self.target_width, self.target_height):
            spin.setRange(1, 65535)
        centered_form.addRow("Width", self.target_width)
        centered_form.addRow("Height", self.target_height)
        centered_form.addRow(self._button("Center crop to this size", self._center_crop))
        side.addWidget(centered_box)

        fit_box = QGroupBox("Automatic placement")
        fit_form = QFormLayout(fit_box)
        self.margin = QSpinBox()
        self.margin.setRange(0, 2000)
        self.margin.setValue(20)
        self.margin.setToolTip(
            "Subtitle pixels; 20 equals 40 video pixels at 2× scale. "
            "Use 0 for minimum movement to the crop edge."
        )
        fit_form.addRow("Safe margin", self.margin)
        fit_form.addRow(self._button("Fit selected cues", self._fit_selected))
        fit_form.addRow(self._button("Fit problems in this track", self._fit_problems))
        fit_form.addRow(self._button("Fit problems in all tracks", self._fit_all_tracks))
        side.addWidget(fit_box)

        move_box = QGroupBox("Selected cues")
        move_layout = QVBoxLayout(move_box)
        self.cue_label = QLabel("Select a cue to inspect or move it.")
        self.cue_label.setWordWrap(True)
        move_layout.addWidget(self.cue_label)
        self.step = QSpinBox()
        self.step.setRange(1, 1000)
        self.step.setValue(5)
        self.step.setToolTip("Subtitle pixels; a step of 1 moves 2 video pixels at 2× scale.")
        move_form = QFormLayout()
        move_form.addRow("Nudge step", self.step)
        self.dx, self.dy = QSpinBox(), QSpinBox()
        for spin in (self.dx, self.dy):
            spin.setRange(-65535, 65535)
            spin.setToolTip(
                "Offset in the original subtitle's pixels, before subtracting crop margins."
            )
        move_form.addRow("Offset X", self.dx)
        move_form.addRow("Offset Y", self.dy)
        move_layout.addLayout(move_form)
        arrows = QGridLayout()
        for label, x, y, row, col in (
            ("↑", 0, -1, 0, 1),
            ("←", -1, 0, 1, 0),
            ("→", 1, 0, 1, 2),
            ("↓", 0, 1, 2, 1),
        ):
            arrows.addWidget(
                self._button(label, lambda _checked=False, a=x, b=y: self._nudge(a, b)), row, col
            )
        move_layout.addLayout(arrows)
        move_layout.addWidget(self._button("Set selected offsets", self._set_offsets))
        move_layout.addWidget(self._button("Reset selected offsets", self._reset_selected))
        side.addWidget(move_box)
        delay_form = QFormLayout()
        self.delay = QDoubleSpinBox()
        self.delay.setDecimals(3)
        self.delay.setRange(-86400, 86400)
        self.delay.setSingleStep(0.1)
        self.delay.setToolTip(
            "Preview only. Positive values show subtitles later. Export timing is unchanged."
        )
        self.delay.valueChanged.connect(lambda: self._position_changed(self.player.position()))
        delay_form.addRow("Preview delay (s)", self.delay)
        side.addLayout(delay_form)
        side.addStretch(1)

    def _set_edit_enabled(self, enabled):
        self.sidebar.setEnabled(enabled)
        self.match_video_crop_button.setEnabled(enabled and self._video_dimensions() is not None)
        self.save_action.setEnabled(not self.task)
        self.track_selector.setEnabled(not self.task)
        self.remove_track_button.setEnabled(enabled and not self.task)
        self.remove_video_button.setEnabled(self.video_path is not None and not self.task)
        self.detect_crop_button.setEnabled(self._video_dimensions() is not None and not self.task)
        for action in (
            self.export_all_action,
            self.export_action,
            self.undo_action,
            self.redo_action,
        ):
            action.setEnabled(enabled)

    def _error(self, message):
        QMessageBox.warning(self, "Sup2Sup", message)

    def _run_task(self, text, function, callback, *, cancellable=True, pause=True):
        if self.task:
            return
        if pause:
            self.player.pause()
        self.track_selector.setEnabled(False)
        self.remove_track_button.setEnabled(False)
        self.remove_video_button.setEnabled(False)
        self.splitter.setEnabled(False)
        for toolbar in self.findChildren(QToolBar):
            toolbar.setEnabled(False)
        self.statusBar().showMessage(text)
        self.progress_panel.show()
        self.cancel_button.setVisible(cancellable)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("Cancel")
        self._task_progress(Progress(text, 0, 0, unit=""))
        task = Task(function, self)
        self.task = task
        self._task_callback = callback
        self._task_cancellable = cancellable
        self._cancel_requested = False
        task.progress.connect(self._task_progress, Qt.ConnectionType.QueuedConnection)
        task.finished.connect(self._task_finished, Qt.ConnectionType.QueuedConnection)
        task.start()

    @Slot(object)
    def _task_progress(self, update):
        self._last_progress = update
        if update.total:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(min(1000, update.completed * 1000 // update.total))
            count = f"{update.completed:,} / {update.total:,} {update.unit}"
        elif update.unit == "cues":
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(1000)
            count = "0 / 0 cues"
        else:
            self.progress_bar.setRange(0, 0)
            count = ""
        self.progress_label.setText(
            f"{update.stage}: {count}".rstrip(": ")
            + (f" — {update.detail}" if update.detail else "")
        )

    def _cancel_task(self):
        if self.task and self._task_cancellable:
            self._cancel_requested = True
            self.task.requestInterruption()
            self.cancel_button.setEnabled(False)
            self.cancel_button.setText("Cancelling…")

    @Slot()
    def _task_finished(self):
        task, callback = self.task, self._task_callback
        self.task = None
        self._task_callback = None
        self.splitter.setEnabled(True)
        for toolbar in self.findChildren(QToolBar):
            toolbar.setEnabled(True)
        self._set_edit_enabled(self.session is not None)
        self.cancel_button.hide()
        self.progress_bar.setRange(0, 1000)
        try:
            if task.cancelled or self._cancel_requested:
                self.progress_bar.setValue(0)
                self.progress_label.setText("Cancelled — previous document and edits kept.")
                self._show_crop()
            elif task.error is not None:
                self.progress_bar.setValue(0)
                self.progress_label.setText("Operation failed — previous document and edits kept.")
                self._show_crop()
                self._error(task.error)
            else:
                self.progress_bar.setValue(1000)
                self.progress_label.setText("Finished — " + self.progress_label.text())
                callback(task.value)
        finally:
            task.deleteLater()
            self._set_edit_enabled(self.session is not None)
        self._summary()

    def _confirm_discard(self):
        if not self.project.dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved edits",
            "Save your project before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self._save_project()
        return answer == QMessageBox.StandardButton.Discard

    def _new_project(self):
        if self.task or not self._confirm_discard():
            return
        self.project = Project()
        self.project_path = None
        self._track_results.clear()
        self._set_video(None)
        self._update_tracks()
        self._activate_track()

    def _open_sup(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import PGS subtitles", "", "PGS subtitles (*.sup);;All files (*)"
        )
        if paths:
            self._import_subtitles(paths)

    def _open_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open project", "", "Sup2Sup projects (*.sup2sup.json);;JSON (*.json)"
        )
        if path:
            self.open_path(path)

    def open_path(self, path):
        if self.task:
            return
        path = Path(path)
        if path.suffix.lower() == ".sup":
            self._import_subtitles([path])
            return
        if path.suffix.lower() != ".json":
            self._import_video(path)
            return
        if not self._confirm_discard():
            return

        def load(progress):
            project = Project.load(path, progress=progress)
            return project, self._scan_tracks(project, progress)

        def loaded(result):
            self.project, self._track_results = result
            self.project_path = path
            self._set_video(self.project.video)
            self._update_tracks()
            self._activate_track()
            if self.project.video and not self.project.video.exists():
                self._error(
                    "Saved video is missing. Subtitle editing is available; import a video "
                    "to restore playback."
                )

        self._run_task("Opening project…", load, loaded)

    @staticmethod
    def _scan_tracks(project, progress):
        results = {}
        for track in project.tracks:
            findings = track.session.findings(progress=progress)
            results[id(track.session)] = (
                findings,
                tuple(f.cue_index for f in findings if f.problem),
            )
        return results

    def _import_subtitles(self, paths):
        def work(progress):
            candidate = self.project.fork()
            first = len(candidate.tracks)
            for path in paths:
                candidate.add_sup(path, progress=progress)
            candidate.active_index = first
            return candidate, self._scan_tracks(candidate, progress)

        def imported(result):
            self._project_edited(result)
            count = sum(len(t.session.document.cues) for t in self.project.tracks)
            self.progress_label.setText(
                f"Loaded {count:,} cues across {len(self.project.tracks)} subtitle tracks."
            )

        self._run_task("Importing subtitle tracks…", work, imported)

    def _project_edited(self, result):
        self.project, self._track_results = result[:2]
        self._update_tracks()
        self._activate_track()
        if len(result) > 2 and result[2]:
            self._error("\n".join(result[2][:10]))

    def _update_tracks(self):
        self.track_selector.blockSignals(True)
        self.track_selector.clear()
        self.track_selector.addItems([t.name for t in self.project.tracks])
        if self.project.tracks:
            self.track_selector.setCurrentIndex(self.project.active_index)
        self.track_selector.blockSignals(False)

    def _activate_track(self):
        self.displayed_index = None
        self.preview._tile_cue = None
        self.preview._pixmaps = []
        self.preview.set_cue(None)
        if not self.project.tracks:
            self.session = None
            self.timeline = Timeline(())
            self.cue_model.replace(None, (), ())
            self.file_label.setText(
                "No subtitle tracks — import SUP files or a video with PGS tracks."
            )
            self.preview.configure(1920, 1080, Crop(), False)
            self._map_video()
        else:
            track = self.project.tracks[self.project.active_index]
            session = track.session
            self.timeline = Timeline(session.document.cues)
            self.file_label.setText(f"{track.name} · {session.source}")
            self.target_width.setValue(session.document.width)
            self.target_height.setValue(session.document.height)
            findings, problems = self._track_results[id(session)]
            if not self.video_path:
                last = session.document.cues[-1] if session.document.cues else None
                duration = ((last.end_pts or last.start_pts + 450000) // 90) if last else 0
                self.seek.setMaximum(min(duration, 2_147_483_647))
            self._present(session, findings, problems, selected=[])
            position = self.player.position() if self.video_path else self.seek.value()
            self._position_changed(position)
            if not self.video_path and session.document.cues and self.displayed_index is None:
                self.table.selectRow(0)
            if session.document.warnings:
                self._error("\n".join(session.document.warnings))
        self._set_edit_enabled(self.session is not None)
        self._summary()

    def _switch_track(self, index):
        if self.task or not 0 <= index < len(self.project.tracks):
            return
        self.project.active_index = index
        self._activate_track()

    def _remove_track(self):
        if self.task or not self.project.tracks:
            return
        del self.project.tracks[self.project.active_index]
        self.project.active_index = min(
            self.project.active_index, max(0, len(self.project.tracks) - 1)
        )
        self._update_tracks()
        self._activate_track()

    def _open_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import video and PGS tracks",
            "",
            "Videos (*.mkv *.mp4 *.m2ts *.ts *.mov *.avi);;All files (*)",
        )
        if path:
            self._import_video(path)

    def _import_video(self, path):
        def work(progress):
            candidate = self.project.fork()
            info = candidate.import_video(path, progress=progress)
            return candidate, self._scan_tracks(candidate, progress), info.skipped_subtitles

        def imported(result):
            project, results, skipped = result
            self.project = project
            self._set_video(project.video)
            self._project_edited((project, results))
            self.progress_label.setText(
                f"Video imported · {len(project.tracks)} subtitle tracks in project. "
                + (f"Skipped {skipped} non-PGS subtitle tracks." if skipped else "")
            )

        self._run_task("Importing video and extracting PGS tracks…", work, imported)

    def _set_video(self, path):
        self.video_path = Path(path) if path else None
        self._pending_seek = 0 if path else None
        self._video_alignment_pending = False
        self.player.setSource(
            QUrl.fromLocalFile(str(self.video_path.resolve()))
            if path and self.video_path.exists()
            else QUrl()
        )
        self.player.setActiveSubtitleTrack(-1)
        self.player.pause()
        self.play.setEnabled(path is not None)
        self.preview.video_item.setVisible(path is not None)
        self.video_label.setText(f"Loading {self.video_path.name}…" if path else "Video optional")
        self.seek.setValue(0)
        self.time_label.setText(format_pts(0))

    def _remove_video(self):
        if self.task:
            return
        self.project.video = None
        self._set_video(None)
        self._activate_track()

    def _apply_project_crop(self):
        if not self.session:
            return
        doc = self.session.document
        self._set_project_crop((doc.width, doc.height), Crop(*(s.value() for s in self.crop_spins)))

    def _set_project_crop(self, canvas, crop):
        def work(progress):
            candidate = self.project.fork()
            candidate.set_crop(canvas, crop)
            return candidate, self._scan_tracks(candidate, progress)

        self._run_task("Applying crop to all subtitle tracks…", work, self._project_edited)

    def _detect_project_crop(self):
        size = self._video_dimensions()
        if self.task or size is None:
            return
        # This also works before subtitles are imported; subsequent imports inherit the preset.
        canvas = suggest_video_canvas(1920, 1080, *size)
        dialog = VideoCropDialog(*canvas, *size, self)
        dialog.setWindowTitle("Detect project crop against standard canvas")
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        crop = dialog.subtitle_crop
        dialog.deleteLater()
        if accepted and crop is not None:
            self._set_project_crop(canvas, crop)

    def _fit_all_tracks(self):
        margin = self.margin.value()

        def work(progress):
            candidate = self.project.fork()
            errors = candidate.fit_all(margin, progress=progress)
            return candidate, self._scan_tracks(candidate, progress), errors

        self._run_task("Fixing problem cues in all subtitle tracks…", work, self._project_edited)

    def _export_all(self):
        directory = QFileDialog.getExistingDirectory(self, "Export all subtitle tracks")
        if not directory:
            return
        project = self.project.fork()

        def work(progress):
            return project.export_all(directory, progress=progress)

        def exported(reports):
            QMessageBox.information(
                self, "Export complete", f"Exported {len(reports)} subtitle tracks to {directory}"
            )

        self._run_task(
            "Validating and exporting all subtitle tracks…", work, exported, cancellable=False
        )

    def _media_status(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            self.player.setActiveSubtitleTrack(-1)
            if self._pending_seek is not None:
                position = self._pending_seek
                self._pending_seek = None
                self.player.setPosition(position)
            self._map_video()

    def _video_dimensions(self):
        if not self.video_path:
            return None
        size = self.preview.video_item.nativeSize()
        if size.isEmpty():
            return None
        return round(size.width()), round(size.height())

    def _match_video_crop(self):
        size = self._video_dimensions()
        if not self.session or self.task or size is None:
            return
        doc = self.session.document
        dialog = VideoCropDialog(doc.width, doc.height, *size, self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        crop = dialog.subtitle_crop
        dialog.deleteLater()
        if not accepted or crop is None:
            return
        region = crop.rectangle(doc.width, doc.height)
        # Keep Auto for ordinary letterbox crops; an equal-aspect crop is ambiguous.
        mode = 2 if crop != Crop() and region.width * doc.height == region.height * doc.width else 0
        self._edit(
            "Matching video crop",
            lambda candidate, progress: candidate.set_crop(crop),
            on_applied=lambda: self.mapping.setCurrentIndex(mode),
        )

    def _map_video(self, *_args):
        size = self._video_dimensions()
        self.match_video_crop_button.setEnabled(self.session is not None and size is not None)
        self.detect_crop_button.setEnabled(size is not None and not self.task)
        if size is None:
            return
        width, height = size
        if self.session:
            doc, crop = self.session.document, self.session.crop
            sw, sh = doc.width, doc.height
        else:
            sw, sh, crop = width, height, Crop()
            self.preview.configure(sw, sh, crop, False)
        try:
            rect = video_rectangle(
                sw,
                sh,
                crop,
                width,
                height,
                ("auto", "source", "cropped")[self.mapping.currentIndex()],
            )
        except EditError as exc:
            # An unresolved subtitle mapping must not look like a video decoder failure.
            self._video_alignment_pending = True
            self.preview.configure(width, height, Crop(), False)
            self.preview.set_video_rect(Crop().rectangle(width, height))
            self.preview.video_item.show()
            if self.preview.group:
                self.preview.group.hide()
            self.match_video_crop_button.setText("Set subtitle alignment…")
            self.video_label.setText(
                f"{self.video_path.name} · {width} × {height} · Video only\n"
                "Use Set subtitle alignment to match the crop and show subtitles."
            )
            self.video_label.setToolTip(str(exc))
            return
        if self._video_alignment_pending:
            self.preview.configure(sw, sh, crop, self.preview_mode.currentIndex() == 1)
            self._video_alignment_pending = False
        if self.preview.group:
            self.preview.group.show()
        self.match_video_crop_button.setText("Match video crop…")
        self.video_label.setToolTip("")
        self.preview.set_video_rect(rect)
        self.preview.video_item.show()
        scale_x, scale_y = width / rect.width, height / rect.height
        scale = (
            f"{scale_x:g}×"
            if width * rect.height == height * rect.width
            else f"{scale_x:g}× horizontal / {scale_y:g}× vertical"
        )
        self.video_label.setText(
            f"{self.video_path.name} · {width} × {height} · "
            + (
                "mapped to active picture"
                if rect.width != sw or rect.height != sh
                else "mapped to original canvas"
            )
            + f" · Subtitle → video: {scale}"
        )

    def _save_project(self):
        suggestion = self.project_path or Path("Untitled.sup2sup.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save project", str(suggestion), "Sup2Sup projects (*.sup2sup.json)"
        )
        if not path:
            return False
        try:
            if self.video_path and same_path(path, self.video_path):
                raise EditError("Project cannot overwrite the preview video")
            self.project.save(path, overwrite=True)
        except (OSError, ValueError) as exc:
            self._error(str(exc))
            return False
        self.project_path = Path(path)
        self._summary()
        return True

    def _export(self):
        if not self.session:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export cropped SUP",
            str(self.session.source.with_name(self.session.source.stem + ".cropped.sup")),
            "PGS subtitles (*.sup)",
        )
        if not path:
            return
        protected = [*self.project.protected_paths(), self.project_path, self.video_path]
        if any(p and same_path(path, p) for p in protected):
            self._error("Choose an output path different from the source SUP, project, and video.")
            return
        document, crop, transforms = (
            self.session.document,
            self.session.crop,
            self.session.transforms.copy(),
        )

        def export(progress):
            progress(Progress("Validating and exporting SUP", 0, 0, unit=""))
            data, report = export_sup(document, crop, transforms)
            write_bytes(path, data, overwrite=True)
            return report

        def exported(report):
            box = QMessageBox(self)
            box.setWindowTitle("Export complete")
            box.setText(
                f"Saved {path}\n\nCanvas: {report.output_size[0]} × {report.output_size[1]}\n"
                f"Moved cues: {report.moved_cues}\n"
                f"PCS changed: {report.pcs_changed} · WDS changed: {report.wds_changed}\n"
                "Bitmap, palette, PTS and DTS data are unchanged."
            )
            report_json = json.dumps(asdict(report), indent=2) + "\n"
            box.setDetailedText(report_json)
            save = box.addButton("Save report…", QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Close)
            box.exec()
            if box.clickedButton() == save:
                report_path, _ = QFileDialog.getSaveFileName(
                    self, "Save export report", path + ".report.json", "JSON (*.json)"
                )
                if report_path:
                    try:
                        if any(p and same_path(report_path, p) for p in [*protected, path]):
                            raise EditError("Report must use a separate output path")
                        write_bytes(report_path, report_json.encode(), overwrite=True)
                    except (OSError, ValueError) as exc:
                        self._error(str(exc))

        self._run_task(
            "Validating and exporting subtitle geometry…", export, exported, cancellable=False
        )

    def _refresh(self):
        """Recheck an existing session without doing a full scan in the GUI thread."""
        if not self.session:
            return
        self._edit("Checking cues", lambda candidate, progress: None)

    def _show_crop(self):
        if not self.session:
            return
        crop = self.session.crop
        for spin, value in zip(
            self.crop_spins, (crop.left, crop.top, crop.right, crop.bottom), strict=True
        ):
            spin.setValue(value)
        region = crop.rectangle(self.session.document.width, self.session.document.height)
        self.output_label.setText(f"SUP output: {region.width} × {region.height}")

    def _present(self, session, findings, problems, *, selected=None):
        selected = self._indices() if selected is None else selected
        self.session = session
        if self.project.tracks:
            self.project.tracks[self.project.active_index].session = session
        self._track_results[id(session)] = (findings, problems)
        active_ids = {id(t.session) for t in self.project.tracks}
        self._track_results = {
            key: value for key, value in self._track_results.items() if key in active_ids
        }
        self._loading_ui = True
        try:
            self._show_crop()
            self.cue_model.replace(session, findings, problems)
            self._restore_selection(selected)
            self._refresh_preview()
            self._inspector()
            self._summary()
        finally:
            self._loading_ui = False

    def _restore_selection(self, indices):
        rows = [row for index in indices if (row := self.cue_model.row_for_cue(index)) is not None]
        selection = QItemSelection()
        # Submit contiguous ranges together, including when thousands of cues are selected.
        start = previous = None
        for row in rows:
            if start is not None and row != previous + 1:
                selection.select(self.cue_model.index(start, 0), self.cue_model.index(previous, 6))
                start = None
            if start is None:
                start = row
            previous = row
        if start is not None:
            selection.select(self.cue_model.index(start, 0), self.cue_model.index(previous, 6))
        self.table.selectionModel().select(
            selection, QItemSelectionModel.SelectionFlag.ClearAndSelect
        )
        if rows:
            self.table.selectionModel().setCurrentIndex(
                self.cue_model.index(rows[0], 0), QItemSelectionModel.SelectionFlag.NoUpdate
            )

    def _edit(self, title, operation, *, on_applied=None):
        if not self.session or self.task:
            return
        source = self.session

        def work(progress):
            candidate = source.fork()
            errors = operation(candidate, progress) or []
            findings = candidate.findings(progress=progress)
            problems = tuple(f.cue_index for f in findings if f.problem)
            moved = sum(
                candidate.transforms.get(cue.index, Transform())
                != source.transforms.get(cue.index, Transform())
                for cue in source.document.cues
            )
            return candidate, findings, problems, errors, moved

        def finished(result):
            candidate, findings, problems, errors, moved = result
            self._present(candidate, findings, problems)
            if on_applied is not None:
                on_applied()
            self.progress_label.setText(
                f"{title} finished: {len(findings):,} cues checked; "
                f"{moved:,} moved; {len(problems):,} need review."
                + (f" {len(errors):,} could not fit." if errors else "")
            )
            if errors:
                self._error("\n".join(errors[:10]))

        self._run_task(title, work, finished)

    def _summary(self):
        name = self.project_path.name if self.project_path else "Untitled project"
        self.setWindowTitle(f"{'* ' if self.project.dirty else ''}{name} — Sup2Sup")
        if not self.session:
            self.statusBar().showMessage("Import one or more subtitle tracks to start editing.")
            return
        findings = self.cue_model.findings
        problems = len(self.cue_model.problems)
        self.statusBar().showMessage(
            f"{len(findings)} cues · {len(findings) - problems} safe · {problems} need review"
            "   |   Drag in cropped preview. Ctrl+Z to undo."
        )

    def _filter(self):
        if self.task:
            return
        selected = self._indices()
        self._loading_ui = True
        try:
            self.cue_model.filter_problems(self.problems_only.isChecked())
            self._restore_selection(selected)
            self._inspector()
        finally:
            self._loading_ui = False

    def _indices(self):
        return sorted(
            self.cue_model.cue_index(index.row())
            for index in self.table.selectionModel().selectedRows()
        )

    def _selected(self, *_args):
        if self._loading_ui or not self.session:
            return
        indices = self._indices()
        if not indices:
            return
        row = self.cue_model.cue_index(self.table.currentIndex().row())
        if row not in indices:
            row = indices[0]
        cue = self.session.document.cues[row]
        self.displayed_index = row
        self.player.pause()
        # Round upward to avoid seeking just before a cue with sub-millisecond PTS.
        position = max(0, (cue.start_pts + 89) // 90 + round(self.delay.value() * 1000))
        if self.video_path:
            self.player.setPosition(position)
        else:
            self.seek.setValue(position)
            self.time_label.setText(format_pts(position * 90))
        self._refresh_preview()
        self._inspector()

    def _inspector(self):
        if not self.session:
            return
        indices = self._indices()
        if not indices:
            self.cue_label.setText("Select cues to inspect or move them.")
            return
        cue = self.session.document.cues[indices[0]]
        t = self.session.transforms.get(cue.index, Transform())
        self.dx.setValue(t.dx)
        self.dy.setValue(t.dy)
        b = cue.bounds
        self.cue_label.setText(
            f"{len(indices)} selected · Cue {cue.index + 1}\n"
            f"Original bounds: {b.x}, {b.y} · {b.width} × {b.height}\n"
            "Offsets move the whole cue, before subtracting crop margins."
        )

    def _refresh_preview(self, *_args):
        if not self.session:
            return
        doc = self.session.document
        self.preview.configure(
            doc.width, doc.height, self.session.crop, self.preview_mode.currentIndex() == 1
        )
        cue = doc.cues[self.displayed_index] if self.displayed_index is not None else None
        t = self.session.transforms.get(cue.index, Transform()) if cue else Transform()
        self.preview.set_cue(cue, t)
        self._map_video()

    def _apply_crop(self):
        try:
            crop = Crop(*(spin.value() for spin in self.crop_spins))
            crop.rectangle(self.session.document.width, self.session.document.height)
            self._edit("Checking crop", lambda candidate, progress: candidate.set_crop(crop))
        except EditError as exc:
            self._error(str(exc))

    def _center_crop(self):
        doc = self.session.document
        dw, dh = doc.width - self.target_width.value(), doc.height - self.target_height.value()
        if dw < 0 or dh < 0:
            self._error("Output dimensions must not exceed the subtitle canvas.")
            return
        crop = Crop(dw // 2, dh // 2, dw - dw // 2, dh - dh // 2)
        self._edit("Checking crop", lambda candidate, progress: candidate.set_crop(crop))

    def _fit(self, indices):
        if not indices:
            return
        margin = self.margin.value()
        self._edit(
            "Fixing cues",
            lambda candidate, progress: candidate.auto_fit(indices, margin, progress=progress),
        )

    def _fit_selected(self):
        self._fit(self._indices())

    def _fit_problems(self):
        self._fit(self.cue_model.problems)

    def _move(self, indices, dx, dy, *, absolute=False):
        if indices:
            self._edit(
                "Moving cues",
                lambda candidate, progress: candidate.move(indices, dx, dy, absolute=absolute),
            )

    def _nudge(self, x, y):
        self._move(self._indices(), x * self.step.value(), y * self.step.value())

    def _set_offsets(self):
        self._move(self._indices(), self.dx.value(), self.dy.value(), absolute=True)

    def _reset_selected(self):
        self._move(self._indices(), 0, 0, absolute=True)

    def _dragged(self, index, dx, dy):
        if self.session and not self.task:
            self._loading_ui = True
            row = self.cue_model.row_for_cue(index)
            if row is not None:
                self.table.selectRow(row)
            self._loading_ui = False
            # Restore the committed position while the worker validates the proposed move.
            self._refresh_preview()
            self._move([index], dx, dy)

    def _undo(self):
        self._edit("Undo", lambda candidate, progress: candidate.undo())

    def _redo(self):
        self._edit("Redo", lambda candidate, progress: candidate.redo())

    def _navigate(self, direction, problems=False):
        if not self.session:
            return
        allowed = set(self.cue_model.problems) if problems else None
        count = self.cue_model.rowCount()
        current = self.table.currentIndex().row()
        for step in range(1, count + 1):
            row = (current + direction * step) % count
            if allowed is None or self.cue_model.cue_index(row) in allowed:
                self.table.selectRow(row)
                self.table.scrollTo(self.cue_model.index(row, 0))
                break

    def _toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _playback_changed(self, state):
        self.play.setText("Pause" if state == QMediaPlayer.PlaybackState.PlayingState else "Play")

    def _duration_changed(self, duration):
        self.seek.setMaximum(min(duration, 2_147_483_647))

    def _seek_to(self, position):
        if self.video_path:
            self.player.setPosition(position)
        else:
            self._position_changed(position)

    def _position_changed(self, position):
        if self.task:
            return
        if not self.seek.isSliderDown():
            self.seek.setValue(position)
            self.time_label.setText(format_pts(position * 90))
        cue = self.timeline.at_milliseconds(position, round(self.delay.value() * 1000))
        index = cue.index if cue else None
        if index != self.displayed_index:
            self.displayed_index = index
            self._refresh_preview()
            if (
                index is not None
                and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            ):
                self._loading_ui = True
                row = self.cue_model.row_for_cue(index)
                if row is not None:
                    self.table.selectRow(row)
                self._loading_ui = False
                self._inspector()

    def closeEvent(self, event):
        if self.task:
            self.statusBar().showMessage(
                "A task is running. Cancel it or wait for it to finish before closing."
            )
            event.ignore()
            return
        if self._confirm_discard():
            self.player.stop()
            event.accept()
        else:
            event.ignore()
