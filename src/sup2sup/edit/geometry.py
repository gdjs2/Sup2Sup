from dataclasses import dataclass

from sup2sup.pgs.parser import Cue
from sup2sup.pgs.segments import Rect


class EditError(ValueError):
    pass


@dataclass(frozen=True)
class Crop:
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    def rectangle(self, width: int, height: int) -> Rect:
        values = (self.left, self.top, self.right, self.bottom)
        if any(type(value) is not int or value < 0 for value in values):
            raise EditError("Crop margins must be nonnegative whole pixels")
        w, h = width - self.left - self.right, height - self.top - self.bottom
        if w <= 0 or h <= 0:
            raise EditError("Crop must leave a nonempty output canvas")
        return Rect(self.left, self.top, w, h)


@dataclass(frozen=True)
class Transform:
    dx: int = 0
    dy: int = 0

    def __post_init__(self):
        if type(self.dx) is not int or type(self.dy) is not int:
            raise EditError("Cue offsets must be whole pixels")


@dataclass(frozen=True)
class Finding:
    cue_index: int
    status: str
    bounds: Rect
    overflow: tuple[int, int, int, int]

    @property
    def problem(self) -> bool:
        return self.status != "safe"

    @property
    def description(self) -> str:
        if not self.problem:
            return "Safe"
        edges = ", ".join(f"{name} {n}px" for name, n in
                          zip(("left", "top", "right", "bottom"), self.overflow) if n)
        return f"{self.status.capitalize()}: {edges}"


def inspect_cue(cue: Cue, crop: Crop, transform: Transform = Transform()) -> Finding:
    canvas = crop.rectangle(cue.composition.width, cue.composition.height)
    bounds = cue.bounds.moved(transform.dx, transform.dy)
    overflow = (max(0, canvas.x - bounds.x), max(0, canvas.y - bounds.y),
                max(0, bounds.right - canvas.right), max(0, bounds.bottom - canvas.bottom))
    if not any(overflow):
        status = "safe"
    elif not any(p.rect.moved(transform.dx, transform.dy).intersection(canvas)
                 for p in cue.placements):
        status = "outside"
    else:
        status = "clipped"
    return Finding(cue.index, status, bounds, overflow)


def fit_cue(cue: Cue, crop: Crop, transform: Transform = Transform(), margin: int = 0
            ) -> Transform:
    if type(margin) is not int or margin < 0:
        raise EditError("Fit margin must be a nonnegative whole number")
    canvas = crop.rectangle(cue.composition.width, cue.composition.height)
    target = Rect(canvas.x + margin, canvas.y + margin,
                  canvas.width - 2 * margin, canvas.height - 2 * margin)
    bounds = cue.bounds.moved(transform.dx, transform.dy)
    if bounds.width > target.width or bounds.height > target.height:
        raise EditError(f"Cue {cue.index + 1} ({bounds.width}x{bounds.height}) cannot fit "
                        f"inside {target.width}x{target.height}; reduce crop or margin")
    dx = min(max(bounds.x, target.x), target.right - bounds.width) - bounds.x
    dy = min(max(bounds.y, target.y), target.bottom - bounds.height) - bounds.y
    return Transform(transform.dx + dx, transform.dy + dy)
