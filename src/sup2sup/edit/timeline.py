from bisect import bisect_right

from sup2sup.pgs.parser import CLOCK, Cue


class Timeline:
    def __init__(self, cues: tuple[Cue, ...]):
        self.cues = cues
        self.starts = tuple(cue.start_pts for cue in cues)

    def at_pts(self, pts: int) -> Cue | None:
        index = bisect_right(self.starts, pts) - 1
        if index < 0:
            return None
        cue = self.cues[index]
        return cue if cue.end_pts is None or pts < cue.end_pts else None

    def at_milliseconds(self, video_ms: int, subtitle_delay_ms: int = 0) -> Cue | None:
        return self.at_pts((video_ms - subtitle_delay_ms) * (CLOCK // 1000))


def format_pts(pts: int | None) -> str:
    if pts is None:
        return "open"
    milliseconds = pts // (CLOCK // 1000)
    seconds, ms = divmod(milliseconds, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02}:{minute:02}:{sec:02}.{ms:03}"
