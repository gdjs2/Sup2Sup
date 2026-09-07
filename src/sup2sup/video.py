"""Coordinate mapping for previews, without a Qt dependency."""

from fractions import Fraction

from .edit.geometry import Crop, EditError
from .pgs.segments import Rect


def video_rectangle(source_width: int, source_height: int, crop: Crop,
                    video_width: int, video_height: int, mode: str = "auto") -> Rect:
    """Place video in subtitle coordinates, accepting a uniform scale in Auto mode."""
    _validate_sizes(source_width, source_height, video_width, video_height)
    source = Rect(0, 0, source_width, source_height)
    cropped = crop.rectangle(source_width, source_height)
    if mode == "source":
        return source
    if mode == "cropped":
        return cropped
    if mode != "auto":
        raise EditError("Unknown video mapping mode")
    if (video_width, video_height) == (source_width, source_height):
        return source
    if (video_width, video_height) == (cropped.width, cropped.height):
        return cropped
    matches_source = video_width * source.height == video_height * source.width
    matches_crop = video_width * cropped.height == video_height * cropped.width
    if matches_source and matches_crop and source != cropped:
        raise EditError("The original and cropped subtitle canvases have the same aspect ratio. "
                        "Choose whether the video is original or already cropped.")
    if matches_source:
        return source
    if matches_crop:
        return cropped
    raise EditError(f"Video is {video_width}x{video_height}; its aspect ratio does not match "
                    f"the original SUP ({source_width}x{source_height}) or cropped SUP "
                    f"({cropped.width}x{cropped.height}). Use Match video crop, "
                    "or choose a video mapping explicitly.")


def _validate_sizes(*dimensions: int) -> None:
    if any(type(value) is not int or value <= 0 for value in dimensions):
        raise EditError("Video and subtitle dimensions must be positive whole pixels")


def subtitle_crop_from_video(subtitle_width: int, subtitle_height: int,
                             video_source_width: int, video_source_height: int,
                             video_crop: Crop) -> Crop:
    """Convert video-space margins exactly, preserving the subtitle's native pixel grid."""
    _validate_sizes(subtitle_width, subtitle_height, video_source_width, video_source_height)
    video_crop.rectangle(video_source_width, video_source_height)
    scale = Fraction(video_source_width, subtitle_width)
    if scale != Fraction(video_source_height, subtitle_height):
        raise EditError("The uncropped video and subtitle canvas must have the same aspect ratio "
                        "for uniform scaling. Check the video's original dimensions.")
    margins = []
    for edge, margin in zip(("Left", "Top", "Right", "Bottom"),
                            (video_crop.left, video_crop.top, video_crop.right, video_crop.bottom)):
        value = margin / scale
        if value.denominator != 1:
            raise EditError(f"{edge} crop of {margin} video pixels maps to "
                            f"{float(value):g} subtitle pixels. SUP crop margins must be whole "
                            f"pixels; use video margins divisible by {scale.numerator}. "
                            "The crop has not been rounded.")
        margins.append(int(value))
    result = Crop(*margins)
    result.rectangle(subtitle_width, subtitle_height)
    return result


def centered_crop(source_width: int, source_height: int, width: int, height: int) -> Crop:
    _validate_sizes(source_width, source_height, width, height)
    dw, dh = source_width - width, source_height - height
    if dw < 0 or dh < 0:
        raise EditError("Cropped video dimensions must not exceed the original video dimensions")
    return Crop(dw // 2, dh // 2, dw - dw // 2, dh - dh // 2)


def suggest_video_canvas(subtitle_width: int, subtitle_height: int,
                         video_width: int, video_height: int) -> tuple[int, int]:
    """Offer an editable common source size; dimensions alone cannot prove the crop offset."""
    _validate_sizes(subtitle_width, subtitle_height, video_width, video_height)
    if video_width * subtitle_height == video_height * subtitle_width:
        return video_width, video_height
    for width, height in ((1280, 720), (1920, 1080), (2560, 1440), (3840, 2160), (7680, 4320)):
        if (width >= video_width and height >= video_height
                and width * subtitle_height == height * subtitle_width):
            return width, height
    # Custom aspect ratios: assume full width only if that gives an integral source height.
    height = Fraction(video_width * subtitle_height, subtitle_width)
    if height.denominator == 1 and height >= video_height:
        return video_width, int(height)
    return max(subtitle_width, video_width), max(subtitle_height, video_height)
