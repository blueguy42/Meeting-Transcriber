import json
import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mt.audio import RECORDINGS
from mt.recording import wavtools
from mt.recording.clock import RecordingClock
from mt.recording.recorder import Recorder
from mt.transcription import segments

RATE = 16000


def write_ramp(path: Path, seconds: int) -> None:
    """One sample value per second (0, 1, 2, ...): easy to see which seconds survive a cut."""
    data = np.repeat(np.arange(seconds, dtype=np.int16), RATE)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data.tobytes())


def seconds_left(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as r:
        data = np.frombuffer(r.readframes(r.getnframes()), dtype=np.int16)
    return sorted({int(v) for v in data})


class CutWav(unittest.TestCase):
    def test_the_paused_stretches_are_removed_and_nothing_else(self):
        d = Path(tempfile.mkdtemp())
        p = d / "system.wav"
        write_ramp(p, 10)  # started at wall time 1000
        wavtools.cut_wav(p, 1000.0, [(1003.0, 1005.0), (1007.0, 1008.0)])
        self.assertEqual(seconds_left(p), [0, 1, 2, 5, 6, 8, 9])
        with wave.open(str(p), "rb") as r:
            self.assertEqual(r.getnframes(), 7 * RATE)

    def test_a_pause_that_runs_to_the_end_and_one_before_the_start(self):
        d = Path(tempfile.mkdtemp())
        p = d / "system.wav"
        write_ramp(p, 6)
        wavtools.cut_wav(p, 1000.0, [(990.0, 1001.0), (1004.0, 1100.0)])
        self.assertEqual(seconds_left(p), [1, 2, 3])


class Clock(unittest.TestCase):
    def clock(self):
        c = RecordingClock(100.0)
        c.pauses = [(110.0, 130.0)]
        return c

    def test_paused_time_is_not_counted(self):
        c = self.clock()
        self.assertEqual(c.active(100.0), 100.0)
        self.assertEqual(c.active(120.0), 110.0)  # in the middle of the pause: it stands still
        self.assertEqual(c.active(150.0), 130.0)  # 20 s paused before it
        self.assertEqual(c.rel(150.0, 100.0), 30.0)

    def test_an_open_pause_counts_until_now(self):
        c = self.clock()
        c.pause_start = 200.0
        self.assertTrue(c.paused)
        self.assertEqual(c.active(260.0), 260.0 - 20.0 - 60.0)

    def test_pause_and_resume(self):
        c = RecordingClock(0.0)
        c.pause(10.0)
        c.resume(25.0)
        c.resume(99.0)  # nothing to end: a no-op
        self.assertEqual((c.pauses, c.paused), ([(10.0, 25.0)], False))
        self.assertEqual(c.active(40.0), 25.0)


class Cancel(unittest.TestCase):
    def test_only_a_meeting_folder_inside_the_meetings_folder_is_deleted(self):
        root = Path(tempfile.mkdtemp())
        meeting = root / "2026-10-29_1000"
        (meeting / "shots").mkdir(parents=True)
        (meeting / "notes.txt").write_text("x")
        other = root / "2026-10-28_0900"
        other.mkdir()
        r = Recorder.__new__(Recorder)
        r.cfg = {"meetings_dir": root}
        r.dir = meeting
        r.clock = RecordingClock(0.0)
        r._close = lambda: None
        self.assertEqual(r.cancel(), meeting)
        self.assertFalse(meeting.exists())
        self.assertTrue(other.exists())  # older meetings are never touched
        self.assertIsNone(r.cancel())  # not recording any more
        # a folder that is not directly inside the meetings folder is left alone
        stray = Path(tempfile.mkdtemp())
        r.dir = stray
        r.cancel()
        self.assertTrue(stray.exists())
        shutil.rmtree(stray)


class Markers(unittest.TestCase):
    def test_pause_lines(self):
        ev = segments.pause_events([{"at": 130.0, "for": 90.0}], 290.0)
        self.assertEqual([e[1] for e in ev], ["[00:02:10] (Recording paused)", "[00:02:10] (Recording resumed)"])
        ev = segments.pause_events([{"at": 289.8, "for": 30.0}], 290.0)  # paused until the end: no resume
        self.assertEqual([e[1] for e in ev], ["[00:04:49] (Recording paused)"])


class StopMeta(unittest.TestCase):
    def test_a_recording_with_a_pause_has_recording_time_everywhere(self):
        import time as _t

        root = Path(tempfile.mkdtemp())
        d = root / "2026-10-29_1000"
        (d / RECORDINGS).mkdir(parents=True)
        r = Recorder.__new__(Recorder)
        r.cfg = {"meetings_dir": root}
        r.dir, r._close, r.warn = d, (lambda: None), print
        r._mic_events, r._system_events, r._mic_start_device, r._system_start_device = [], [], "M", "S"
        r._mute_start, r._mute_spans = {"mic": None, "system": None}, {"mic": [], "system": []}
        now = _t.time()
        r.clock = RecordingClock(now - 300)
        r.mic_first = now - 300
        r.sys_first = None
        r.clock.pauses = [(now - 170, now - 80)]  # 90 s paused, 130 s into the recording
        r.mic_muted = r.sys_muted = False
        r.stop()
        meta = json.loads((d / "meta.json").read_text())
        self.assertAlmostEqual(meta["duration"], 210.0, delta=1.0)  # 300 s of wall time, 90 of them paused
        self.assertEqual(meta["paused"][0]["for"], 90.0)
        self.assertAlmostEqual(meta["paused"][0]["at"], 130.0, delta=1.0)

    def test_stopping_while_paused_ends_the_pause(self):
        import time as _t

        root = Path(tempfile.mkdtemp())
        d = root / "m"
        (d / RECORDINGS).mkdir(parents=True)
        r = Recorder.__new__(Recorder)
        r.cfg = {"meetings_dir": root}
        r.dir, r._close, r.warn = d, (lambda: None), print
        r._mic_events, r._system_events, r._mic_start_device, r._system_start_device = [], [], None, None
        r._mute_start, r._mute_spans = {"mic": None, "system": None}, {"mic": [], "system": []}
        now = _t.time()
        r.clock = RecordingClock(now - 100)
        r.mic_first = now - 100
        r.sys_first = None
        r.clock.pause(now - 40)
        r.mic_muted = r.sys_muted = False
        r.stop()
        meta = json.loads((d / "meta.json").read_text())
        self.assertAlmostEqual(meta["duration"], 60.0, delta=1.0)
        self.assertFalse(r.paused)
        self.assertEqual(len(meta["paused"]), 1)


if __name__ == "__main__":
    unittest.main()
