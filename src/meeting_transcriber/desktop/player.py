"""Plays a short stretch of an audio file (a sample of one speaker's voice), through the Mac's speakers. Main thread only."""

import logging

from Foundation import NSURL

log = logging.getLogger(__name__)


class SamplePlayer:
    """One sample at a time: starting another stops the first."""

    def __init__(self):
        self._player = None
        self._timer = None
        self._on_end = None

    @property
    def playing(self) -> bool:
        return self._player is not None

    def play(self, path, start: float, seconds: float, on_end=lambda: None) -> bool:
        """Play `seconds` of the file from `start`; on_end() is called when it stops (also when stopped early). -> False if it cannot be played."""
        from AVFoundation import AVAudioPlayer
        from Foundation import NSRunLoop, NSRunLoopCommonModes, NSTimer

        self.stop()
        player, error = AVAudioPlayer.alloc().initWithContentsOfURL_error_(NSURL.fileURLWithPath_(str(path)), None)
        if player is None:
            log.warning("Could not open %s for playback: %s", path, error)
            return False
        player.setCurrentTime_(max(0.0, min(start, player.duration() - 0.1)))
        if not player.play():
            return False
        self._player, self._on_end = player, on_end
        # (a timer rather than the player's delegate: the sample ends before the file does)
        self._timer = NSTimer.timerWithTimeInterval_repeats_block_(seconds, False, lambda _t: self.stop())
        NSRunLoop.mainRunLoop().addTimer_forMode_(self._timer, NSRunLoopCommonModes)
        return True

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        player, on_end, self._player, self._on_end = self._player, self._on_end, None, None
        if player is not None:
            player.stop()
            if on_end:
                on_end()
