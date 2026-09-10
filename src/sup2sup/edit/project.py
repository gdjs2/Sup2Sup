"""A project owns optional video, multiple independent edit sessions, and a shared crop preset."""

import base64
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from pathlib import Path

from sup2sup.files import same_path, write_bytes
from sup2sup.media import extract_pgs_tracks, probe_video
from sup2sup.pgs.parser import parse_sup, read_sup
from sup2sup.pgs.writer import export_sup
from sup2sup.progress import report_progress
from sup2sup.video import subtitle_crop_from_video

from .geometry import Crop, EditError, Transform
from .session import Session


@dataclass
class SubtitleTrack:
    session: Session
    name: str
    embedded: bool = False
    stream_index: int | None = None


class Project:
    def __init__(self):
        self.video: Path | None = None
        self.tracks: list[SubtitleTrack] = []
        self.active_index = 0
        self.crop_canvas: tuple[int, int] | None = None
        self.crop = Crop()
        self.path: Path | None = None
        self._saved = self._snapshot()

    def _snapshot(self):
        return (
            self.video,
            self.crop_canvas,
            self.crop,
            tuple(
                (
                    t.name,
                    t.embedded,
                    t.stream_index,
                    t.session.source,
                    id(t.session.document),
                    t.session._snapshot(),
                )
                for t in self.tracks
            ),
        )

    @property
    def dirty(self):
        return self._snapshot() != self._saved

    def fork(self):
        candidate = Project()
        candidate.video, candidate.path = self.video, self.path
        candidate.crop_canvas, candidate.crop = self.crop_canvas, self.crop
        candidate.active_index = self.active_index
        candidate.tracks = [replace(t, session=t.session.fork()) for t in self.tracks]
        candidate._saved = self._saved
        return candidate

    def add_sup(self, path, *, progress=None):
        session = Session(read_sup(path, progress=progress), path)
        self._add(SubtitleTrack(session, Path(path).stem))

    def _add(self, track):
        if self.crop_canvas:
            doc = track.session.document
            track.session.set_crop(
                subtitle_crop_from_video(doc.width, doc.height, *self.crop_canvas, self.crop)
            )
        self.tracks.append(track)

    def import_video(self, path, *, progress=None):
        """Build additions first so failed extraction never partially changes the project."""
        path = Path(path).resolve()
        info = probe_video(path, progress=progress)
        streams = []
        for stream in info.subtitles:
            index = stream["index"]
            if self.video == path and any(
                t.embedded and t.session.source == path and t.stream_index == index
                for t in self.tracks
            ):
                continue
            streams.append(stream)
        documents = (
            extract_pgs_tracks(path, [s["index"] for s in streams], progress=progress)
            if streams
            else {}
        )
        additions = []
        for stream in streams:
            index = stream["index"]
            tags = stream.get("tags", {})
            name = f"{path.stem}.s{index}.{tags.get('language', 'und')}"
            if tags.get("title"):
                name += f".{tags['title']}"
            doc = documents[index]
            additions.append(SubtitleTrack(Session(doc, path), name, True, index))
        candidate = self.fork()
        for track in additions:
            candidate._add(track)
        self.video, self.tracks = path, candidate.tracks
        return info

    def set_crop(self, canvas, crop):
        crop.rectangle(*canvas)
        converted = [
            subtitle_crop_from_video(
                t.session.document.width, t.session.document.height, *canvas, crop
            )
            for t in self.tracks
        ]
        for track, value in zip(self.tracks, converted, strict=True):
            track.session.set_crop(value)
        self.crop_canvas, self.crop = tuple(canvas), crop

    def fit_all(self, margin=0, *, progress=None):
        if type(margin) is not int or margin < 0:
            raise EditError("Fit margin must be a nonnegative whole number")
        failures = []
        for track in self.tracks:
            session = track.session
            indices = [f.cue_index for f in session.findings(progress=progress) if f.problem]
            failures.extend(
                f"{track.name}: {error}"
                for error in session.auto_fit(indices, margin, progress=progress)
            )
        return failures

    def protected_paths(self):
        return [p for p in [self.video, self.path, *(t.session.source for t in self.tracks)] if p]

    def save(self, path, *, overwrite=False):
        path = Path(path).resolve()
        if any(same_path(path, p) for p in self.protected_paths() if p != self.path):
            raise EditError("Project cannot overwrite a subtitle source or video")

        def relative(source):
            try:
                return os.path.relpath(source, path.parent)
            except ValueError:
                return str(source)

        tracks = []
        for track in self.tracks:
            session = track.session
            raw = session.document.to_bytes()
            item = dict(
                name=track.name,
                source=relative(session.source),
                source_sha256=sha256(raw).hexdigest(),
                embedded=track.embedded,
                stream_index=track.stream_index,
                crop=asdict(session.crop),
                transforms={str(i): asdict(t) for i, t in session.transforms.items()},
            )
            if track.embedded:
                item["sup_base64"] = base64.b64encode(raw).decode("ascii")
            tracks.append(item)
        data = dict(
            format="sup2sup-project",
            version=2,
            video=relative(self.video) if self.video else None,
            active_index=self.active_index,
            crop_canvas=self.crop_canvas,
            crop=asdict(self.crop),
            tracks=tracks,
        )
        write_bytes(path, (json.dumps(data, indent=2) + "\n").encode(), overwrite=overwrite)
        self.path = path
        for track in self.tracks:
            track.session._saved = track.session._snapshot()
        self._saved = self._snapshot()

    @classmethod
    def load(cls, path, *, progress=None):
        path = Path(path).resolve()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            project = cls()
            if data["format"] != "sup2sup-project":
                raise EditError("Unsupported project format")
            if data["version"] == 1:
                session = Session.load(path, progress=progress)
                project.tracks = [SubtitleTrack(session, session.source.stem)]
            elif data["version"] == 2:
                project.video = (path.parent / data["video"]).resolve() if data["video"] else None
                project.crop_canvas = tuple(data["crop_canvas"]) if data["crop_canvas"] else None
                project.crop = Crop(**data["crop"])
                if project.crop_canvas:
                    project.crop.rectangle(*project.crop_canvas)
                for item in data["tracks"]:
                    source = (path.parent / item["source"]).resolve()
                    embedded = item["embedded"]
                    doc = (
                        parse_sup(
                            base64.b64decode(item["sup_base64"], validate=True), progress=progress
                        )
                        if embedded
                        else read_sup(source, progress=progress)
                    )
                    if sha256(doc.to_bytes()).hexdigest() != item["source_sha256"]:
                        raise EditError(f"Source SUP has changed: {source}")
                    session = Session(doc, source)
                    session.set_crop(Crop(**item["crop"]))
                    session.transforms = {
                        int(index): Transform(**value)
                        for index, value in item["transforms"].items()
                    }
                    if any(i < 0 or i >= len(doc.cues) for i in session.transforms):
                        raise EditError("Project contains an unknown cue index")
                    session._undo.clear()
                    session._saved = session._snapshot()
                    project.tracks.append(
                        SubtitleTrack(
                            session, str(item["name"]), embedded, item.get("stream_index")
                        )
                    )
                project.active_index = data["active_index"]
                if (
                    type(project.active_index) is not int
                    or project.active_index < 0
                    or project.active_index >= max(1, len(project.tracks))
                ):
                    raise EditError("Invalid active subtitle track")
            else:
                raise EditError("Unsupported project format/version")
            project.path = path
            project._saved = project._snapshot()
            return project
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise EditError(f"Cannot load project: {exc}") from exc

    def export_all(self, directory, *, overwrite=False, progress=None):
        if not self.tracks:
            raise EditError("Import at least one subtitle track before exporting")
        directory = Path(directory).resolve()
        names = set()
        outputs = []
        for track in self.tracks:
            stem = re.sub(r"[^\w. -]", "_", track.name).strip(" .")[:120] or "subtitle"
            name = f"{stem}.cropped.sup"
            suffix = 2
            while name.casefold() in names:
                name = f"{stem}.{suffix}.cropped.sup"
                suffix += 1
            names.add(name.casefold())
            target = directory / name
            if any(same_path(target, source) for source in self.protected_paths()):
                raise EditError(f"Export cannot overwrite a project input: {target}")
            if target.exists() and not overwrite:
                raise FileExistsError(
                    f"{target} exists; choose another directory or use --overwrite"
                )
            outputs.append(target)
        # Stage every validated track on disk before publishing any output.
        directory.mkdir(parents=True, exist_ok=True)
        reports = []
        with tempfile.TemporaryDirectory(prefix=".sup2sup-", dir=directory) as staging:
            for index, (track, target) in enumerate(zip(self.tracks, outputs, strict=True)):
                report_progress(
                    progress,
                    "Validating subtitle tracks",
                    index,
                    len(outputs),
                    track.name,
                    unit="tracks",
                )
                try:
                    data, report = export_sup(
                        track.session.document, track.session.crop, track.session.transforms
                    )
                except ValueError as exc:
                    raise EditError(f"{track.name}: {exc}") from exc
                (Path(staging) / target.name).write_bytes(data)
                reports.append(dict(track=track.name, output=str(target), **asdict(report)))
            report_progress(progress, "Publishing subtitle tracks", 0, len(outputs), unit="tracks")
            for target in outputs:
                staged = Path(staging) / target.name
                if overwrite:
                    os.replace(staged, target)
                else:
                    os.link(staged, target)
        return reports
