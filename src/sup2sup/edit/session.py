"""Small undoable edit model, also usable without the GUI."""

from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path

from sup2sup.files import same_path, write_bytes
from sup2sup.pgs.parser import Document, read_sup
from sup2sup.progress import ProgressCallback, report_progress

from .geometry import Crop, EditError, Transform, fit_cue, inspect_cue


class Session:
    def __init__(self, document: Document, source: str | Path):
        self.document = document
        self.source = Path(source).resolve()
        self.crop = Crop()
        self.transforms: dict[int, Transform] = {}
        self._undo: list[tuple[Crop, dict[int, Transform]]] = []
        self._redo: list[tuple[Crop, dict[int, Transform]]] = []
        self._saved = self._snapshot()

    def _snapshot(self):
        return self.crop, self.transforms.copy()

    def fork(self) -> "Session":
        """An isolated edit candidate; immutable document pixels are shared with the original."""
        candidate = Session(self.document, self.source)
        candidate.crop, candidate.transforms = self._snapshot()
        candidate._undo = self._undo.copy()
        candidate._redo = self._redo.copy()
        candidate._saved = self._saved
        return candidate

    @property
    def dirty(self) -> bool:
        return self._snapshot() != self._saved

    def _commit(self, crop: Crop, transforms: dict[int, Transform]) -> None:
        crop.rectangle(self.document.width, self.document.height)
        if (crop, transforms) == self._snapshot():
            return
        self._undo.append(self._snapshot())
        self._undo = self._undo[-100:]
        self._redo.clear()
        self.crop, self.transforms = crop, transforms.copy()

    def set_crop(self, crop: Crop) -> None:
        self._commit(crop, self.transforms)

    def move(self, indices: list[int], dx: int, dy: int, *, absolute: bool = False) -> None:
        updated = self.transforms.copy()
        for index in indices:
            if not 0 <= index < len(self.document.cues):
                raise EditError("Unknown cue index")
            old = Transform() if absolute else updated.get(index, Transform())
            new = Transform(old.dx + dx, old.dy + dy)
            if new == Transform():
                updated.pop(index, None)
            else:
                updated[index] = new
        self._commit(self.crop, updated)

    def auto_fit(self, indices: list[int], margin: int = 0, *,
                 progress: ProgressCallback | None = None) -> list[str]:
        updated = self.transforms.copy()
        failures = []
        moved = 0
        report_progress(progress, "Fixing cues", 0, len(indices), "0 moved; 0 could not fit")
        for completed, index in enumerate(indices, 1):
            try:
                t = fit_cue(self.document.cues[index], self.crop,
                            updated.get(index, Transform()), margin)
                moved += t != updated.get(index, Transform())
                if t == Transform():
                    updated.pop(index, None)
                else:
                    updated[index] = t
            except EditError as exc:
                failures.append(str(exc))
            report_progress(progress, "Fixing cues", completed, len(indices),
                            f"{moved} moved; {len(failures)} could not fit")
        self._commit(self.crop, updated)
        return failures

    def findings(self, *, progress: ProgressCallback | None = None):
        results = []
        problems = 0
        total = len(self.document.cues)
        report_progress(progress, "Checking cues", 0, total, "0 problems")
        for completed, cue in enumerate(self.document.cues, 1):
            finding = inspect_cue(cue, self.crop, self.transforms.get(cue.index, Transform()))
            results.append(finding)
            problems += finding.problem
            report_progress(progress, "Checking cues", completed, total, f"{problems} problems")
        return tuple(results)

    def undo(self) -> None:
        if self._undo:
            self._redo.append(self._snapshot())
            self.crop, self.transforms = self._undo.pop()

    def redo(self) -> None:
        if self._redo:
            self._undo.append(self._snapshot())
            self.crop, self.transforms = self._redo.pop()

    def save(self, path: str | Path, *, overwrite: bool = False) -> None:
        if same_path(path, self.source):
            raise EditError("Project file cannot overwrite the source SUP")
        try:
            source = os.path.relpath(self.source, Path(path).resolve().parent)
        except ValueError:  # Different Windows drives.
            source = str(self.source)
        data = {
            "format": "sup2sup-project", "version": 1, "source": source,
            "source_sha256": sha256(self.document.to_bytes()).hexdigest(),
            "crop": asdict(self.crop),
            "transforms": {str(index): asdict(t) for index, t in self.transforms.items()},
        }
        write_bytes(path, (json.dumps(data, indent=2) + "\n").encode(), overwrite=overwrite)
        self._saved = self._snapshot()

    @classmethod
    def load(cls, path: str | Path, *, progress: ProgressCallback | None = None) -> "Session":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if data["format"] != "sup2sup-project" or data["version"] != 1:
                raise EditError("Unsupported project format/version")
            source = (Path(path).resolve().parent / data["source"]).resolve()
            document = read_sup(source, progress=progress)
            report_progress(progress, "Verifying project source", 0, 0, unit="")
            if sha256(document.to_bytes()).hexdigest() != data["source_sha256"]:
                raise EditError("Source SUP has changed since this project was saved")
            session = cls(document, source)
            session.crop = Crop(**data["crop"])
            session.crop.rectangle(document.width, document.height)
            session.transforms = {int(index): Transform(**value)
                                  for index, value in data["transforms"].items()}
            if any(index < 0 or index >= len(document.cues) for index in session.transforms):
                raise EditError("Project contains an unknown cue index")
            session._saved = session._snapshot()
            return session
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise EditError(f"Cannot load project: {exc}") from exc
