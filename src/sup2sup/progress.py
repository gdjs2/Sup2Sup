"""Optional progress reporting for the core, with no GUI dependency."""

from collections.abc import Callable
from dataclasses import dataclass


class OperationCancelled(Exception):
    """A caller requested that an operation stop at its next checkpoint."""


@dataclass(frozen=True)
class Progress:
    stage: str
    completed: int
    total: int
    detail: str = ""
    unit: str = "cues"


ProgressCallback = Callable[[Progress], None]


def report_progress(callback: ProgressCallback | None, stage: str,
                    completed: int, total: int, detail: str = "", unit: str = "cues") -> None:
    if callback is not None:
        callback(Progress(stage, completed, total, detail, unit))
