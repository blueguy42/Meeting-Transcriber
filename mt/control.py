"""Pausing and cancelling the processing of a meeting.

The worker thread that processes a meeting is bound to a JobControl. The pipeline reports progress all the time, and the
app's progress callback calls check(), which raises Stopped as soon as a pause or cancel was asked for. So a step stops
at its next progress report (after the speech burst that is running, at the next speaker-separation tick...), and the code
that knows how to keep its place (the transcription checkpoint) catches Stopped, saves, and lets it go on up. Code that
cannot report progress (the Claude call) calls check() itself while it waits.
"""

import threading


class Stopped(Exception):
    """The meeting's processing was paused ("pause") or cancelled ("cancel")."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class JobControl:
    def __init__(self):
        self.reason: str | None = None  # "pause" | "cancel" once asked for
        self.locked = False  # saving the files: too late to stop, it finishes

    def stop(self, reason: str) -> bool:
        """Ask the job to stop. False if it is too late (it is saving the files)."""
        if self.locked:
            return False
        self.reason = reason
        return True

    def check(self) -> None:
        if self.reason and not self.locked:
            raise Stopped(self.reason)


_local = threading.local()


def bind(ctrl: JobControl | None) -> None:
    _local.ctrl = ctrl


def check() -> None:
    """Raise Stopped if the job running on this thread was asked to stop. Does nothing outside a job."""
    ctrl = getattr(_local, "ctrl", None)
    if ctrl is not None:
        ctrl.check()
