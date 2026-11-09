"""The recording's own clock: wall time minus the time spent paused.

Everything a recording stamps (the timer, notes, screenshots, documents, mute spans, device changes, the pauses themselves)
uses this clock, so a pause leaves no gap and nothing needs shifting afterwards.
"""

import time


class RecordingClock:
    def __init__(self, t0: float = 0.0):
        self.start(t0)

    def start(self, t0: float) -> None:
        self.t0 = t0
        self.pauses: list[tuple[float, float]] = []  # wall-clock (start, end) of the pauses that are over
        self.pause_start: float | None = None  # wall clock of the pause that is going on

    @property
    def paused(self) -> bool:
        return self.pause_start is not None

    def pause(self, now: float | None = None) -> None:
        self.pause_start = time.time() if now is None else now

    def resume(self, now: float | None = None) -> None:
        """End the pause that is going on (a no-op if there is none)."""
        if self.pause_start is not None:
            self.pauses.append((self.pause_start, time.time() if now is None else now))
            self.pause_start = None

    def active(self, t: float) -> float:
        """The wall clock at t minus the time spent paused before it."""
        spans = list(self.pauses)
        if self.pause_start is not None:
            spans.append((self.pause_start, float("inf")))
        return t - sum(max(0.0, min(t, b) - a) for a, b in spans)

    def elapsed(self) -> float:
        """Seconds of recording so far, not counting pauses."""
        return max(self.active(time.time()) - self.t0, 0.0)

    def rel(self, t: float, origin: float) -> float:
        """Seconds of recording between the moment `origin` and the moment `t` (never negative)."""
        return max(self.active(t) - self.active(origin), 0.0)
