from dataclasses import dataclass

from sup2sup.pgs.parser import Cue, Placement
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
    fullscreen_cropped: bool = False

    @property
    def problem(self) -> bool:
        return self.status != "safe"

    @property
    def blocks_export(self) -> bool:
        return self.status in ("clipped", "outside")

    @property
    def description(self) -> str:
        if not self.problem:
            return "Safe"
        if self.status == "fullscreen_cropped":
            return "Full-screen cropped — review"
        edges = ", ".join(f"{name} {n}px" for name, n in
                          zip(("left", "top", "right", "bottom"), self.overflow) if n)
        review = "; full-screen cropped — review" if self.fullscreen_cropped else ""
        return f"{self.status.capitalize()}: {edges}{review}"


def is_fullscreen(cue: Cue, placement: Placement) -> bool:
    """Only a bitmap covering the entire original canvas may be auto-clipped."""
    canvas = Rect(0, 0, cue.composition.width, cue.composition.height)
    return (placement.rect == canvas
            and placement.source_rect == canvas
            and (placement.bitmap.width, placement.bitmap.height)
            == (canvas.width, canvas.height))


def retained_rect(cue: Cue, placement: Placement, canvas: Rect,
                  transform: Transform) -> Rect | None:
    """Visible source coordinates after movement, before changing the canvas origin."""
    moved = placement.rect.moved(transform.dx, transform.dy)
    if not is_fullscreen(cue, placement):
        return moved
    visible = moved.intersection(placement.window.moved(transform.dx, transform.dy))
    return visible.intersection(canvas) if visible else None


def inspect_cue(cue: Cue, crop: Crop, transform: Transform = Transform()) -> Finding:
    canvas = crop.rectangle(cue.composition.width, cue.composition.height)
    bounds = cue.bounds.moved(transform.dx, transform.dy)
    overflow = (max(0, canvas.x - bounds.x), max(0, canvas.y - bounds.y),
                max(0, bounds.right - canvas.right), max(0, bounds.bottom - canvas.bottom))
    retained = [retained_rect(cue, p, canvas, transform) for p in cue.placements]
    fullscreen_cropped = any(
        is_fullscreen(cue, p) and rect != p.rect.moved(transform.dx, transform.dy)
        for p, rect in zip(cue.placements, retained, strict=True)
    )
    if all(rect is not None and canvas.contains(rect) for rect in retained):
        status = "fullscreen_cropped" if fullscreen_cropped else "safe"
    elif not any(rect is not None and rect.intersection(canvas) for rect in retained):
        status = "outside"
    else:
        status = "clipped"
    return Finding(cue.index, status, bounds, overflow, fullscreen_cropped)


def fit_cue(cue: Cue, crop: Crop, transform: Transform = Transform(), margin: int = 0
            ) -> Transform:
    if type(margin) is not int or margin < 0:
        raise EditError("Fit margin must be a nonnegative whole number")
    canvas = crop.rectangle(cue.composition.width, cue.composition.height)
    target = Rect(canvas.x + margin, canvas.y + margin,
                  canvas.width - 2 * margin, canvas.height - 2 * margin)
    # Full-screen images are cropped at export; fitting their outer bitmap would
    # always fail, and would unexpectedly move text in otherwise correct cues.
    ordinary = [p.rect for p in cue.placements if not is_fullscreen(cue, p)]
    if not ordinary:
        if inspect_cue(cue, crop, transform).blocks_export:
            raise EditError(f"Cue {cue.index + 1}: move the full-screen image back into the crop")
        return transform
    bounds = ordinary[0]
    for rect in ordinary[1:]:
        bounds = bounds.union(rect)
    bounds = bounds.moved(transform.dx, transform.dy)
    if bounds.width > target.width or bounds.height > target.height:
        raise EditError(f"Cue {cue.index + 1} ({bounds.width}x{bounds.height}) cannot fit "
                        f"inside {target.width}x{target.height}; reduce crop or margin")
    dx = min(max(bounds.x, target.x), target.right - bounds.width) - bounds.x
    dy = min(max(bounds.y, target.y), target.bottom - bounds.height) - bounds.y
    return Transform(transform.dx + dx, transform.dy + dy)
