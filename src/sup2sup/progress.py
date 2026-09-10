"""Optional progress reporting for the core, with no GUI dependency."""

from collections.abc import Callable
from dataclasses import dataclass, replace


class OperationCancelled(Exception):
    """A caller requested that an operation stop at its next checkpoint."""


@dataclass(frozen=True)
class Progress:
    stage: str
    completed: int
    total: int
    detail: str = ""
    unit: str = "cues"
    track_number: int = 0
    track_total: int = 0
    track_name: str = ""


ProgressCallback = Callable[[Progress], None]


def track_progress(
    callback: ProgressCallback | None, number: int, total: int, name: str
) -> ProgressCallback | None:
    """Keep the track context while nested parsers report bytes, images, or cues."""
    if callback is None:
        return None

    def report(update: Progress) -> None:
        callback(replace(update, track_number=number, track_total=total, track_name=name))

    return report


def report_progress(
    callback: ProgressCallback | None,
    stage: str,
    completed: int,
    total: int,
    detail: str = "",
    unit: str = "cues",
) -> None:
    if callback is not None:
        callback(Progress(stage, completed, total, detail, unit))
