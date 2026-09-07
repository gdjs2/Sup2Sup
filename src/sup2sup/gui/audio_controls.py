"""Controls for the video's audio, sharing the media player's playback clock."""

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtMultimedia import QAudioDevice, QMediaDevices, QMediaMetaData, QMediaPlayer
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider, QWidget


def track_label(index, metadata):
    details = [metadata.stringValue(key) for key in
               (QMediaMetaData.Key.Title, QMediaMetaData.Key.Language,
                QMediaMetaData.Key.AudioCodec)]
    return f"Track {index + 1}" + "".join(f" · {value}" for value in details if value)


class AudioControls(QWidget):
    def __init__(self, player, output, parent=None):
        super().__init__(parent)
        self.player, self.output = player, output
        self.devices = QMediaDevices(self)
        self._device_id = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.mute = QPushButton("Mute")
        self.mute.setCheckable(True)
        self.mute.setToolTip("Mute or unmute the video's audio")
        layout.addWidget(self.mute)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(100)
        self.volume.setAccessibleName("Audio volume")
        layout.addWidget(self.volume)
        self.volume_label = QLabel()
        self.volume_label.setMinimumWidth(38)
        layout.addWidget(self.volume_label)

        layout.addWidget(QLabel("Audio"))
        self.tracks = QComboBox()
        self.tracks.setMinimumContentsLength(14)
        self.tracks.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.tracks.setToolTip("Choose an audio track from the opened video")
        layout.addWidget(self.tracks, 1)

        layout.addWidget(QLabel("Output"))
        self.outputs = QComboBox()
        self.outputs.setMinimumContentsLength(14)
        self.outputs.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.outputs.setToolTip("Choose speakers or headphones for preview playback")
        layout.addWidget(self.outputs, 1)

        self.volume.valueChanged.connect(lambda value: self.output.setVolume(value / 100))
        self.mute.toggled.connect(self.output.setMuted)
        self.output.volumeChanged.connect(self._sync_volume)
        self.output.mutedChanged.connect(self._sync_mute)
        self.tracks.currentIndexChanged.connect(self._select_track)
        self.outputs.currentIndexChanged.connect(self._select_output)
        self.player.tracksChanged.connect(self._refresh_tracks)
        self.player.activeTracksChanged.connect(self._sync_track)
        self.player.sourceChanged.connect(self._refresh_tracks)
        self.player.mediaStatusChanged.connect(self._refresh_tracks)
        self.devices.audioOutputsChanged.connect(self._refresh_outputs)
        self._sync_volume(self.output.volume())
        self._sync_mute(self.output.isMuted())
        self._refresh_tracks()
        self._refresh_outputs()

    def _sync_volume(self, value):
        percent = round(value * 100)
        with QSignalBlocker(self.volume):
            self.volume.setValue(percent)
        self.volume_label.setText(f"{percent}%")

    def _sync_mute(self, muted):
        with QSignalBlocker(self.mute):
            self.mute.setChecked(muted)
        self.mute.setText("Unmute" if muted else "Mute")

    def _refresh_tracks(self, *_args):
        tracks = self.player.audioTracks()
        with QSignalBlocker(self.tracks):
            self.tracks.clear()
            for index, metadata in enumerate(tracks):
                self.tracks.addItem(track_label(index, metadata), index)
            if not tracks:
                status = self.player.mediaStatus()
                text = ("Open a video for audio" if status == QMediaPlayer.MediaStatus.NoMedia
                        else "Loading audio…" if status == QMediaPlayer.MediaStatus.LoadingMedia
                        else "No audio track")
                self.tracks.addItem(text, None)
            self.tracks.setEnabled(bool(tracks))
        if tracks and not 0 <= self.player.activeAudioTrack() < len(tracks):
            self.player.setActiveAudioTrack(0)
        self._sync_track()

    def _sync_track(self):
        index = self.tracks.findData(self.player.activeAudioTrack())
        if index >= 0:
            with QSignalBlocker(self.tracks):
                self.tracks.setCurrentIndex(index)
        self.tracks.setToolTip(self.tracks.currentText())

    def _select_track(self, index):
        track = self.tracks.itemData(index)
        if track is not None and track != self.player.activeAudioTrack():
            position = self.player.position()
            self.player.setActiveAudioTrack(track)
            # Refill the new stream at the current clock position. In particular, Qt's
            # FFmpeg backend can otherwise retain an empty audio queue after a paused switch.
            if self.player.isSeekable():
                self.player.setPosition(position)
            self.tracks.setToolTip(self.tracks.currentText())

    def _refresh_outputs(self):
        devices = self.devices.audioOutputs()
        default = self.devices.defaultAudioOutput()
        with QSignalBlocker(self.outputs):
            self.outputs.clear()
            self.outputs.addItem(f"System default · {default.description()}" if devices
                                 else "No audio output device", None)
            for device in devices:
                self.outputs.addItem(device.description(), bytes(device.id()).hex())
            selected = self.outputs.findData(self._device_id) if self._device_id is not None else 0
            self.outputs.setCurrentIndex(max(0, selected))
            self.outputs.setEnabled(bool(devices))
        # A disconnected selected device falls back to the current system default.
        self._select_output(self.outputs.currentIndex())

    def _select_output(self, index):
        self._device_id = self.outputs.itemData(index)
        selected = next((device for device in self.devices.audioOutputs()
                         if bytes(device.id()).hex() == self._device_id), QAudioDevice())
        self.output.setDevice(selected)
        self.outputs.setToolTip(self.outputs.currentText())
