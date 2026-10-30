"""Start, pause, resume and stop a Recorder with the audio devices left out (nothing is opened)."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mt.recording import Recorder


class Flow(unittest.TestCase):
    def recorder(self):
        root = Path(tempfile.mkdtemp())
        r = Recorder({"meetings_dir": root}, on_warning=lambda m: None)
        r._start_capture = lambda d: None  # no microphone, no system-audio helper
        r._close = lambda: None
        return r, root

    def test_the_clock_stops_while_paused_and_the_meta_says_so(self):
        r, root = self.recorder()
        d = r.start()
        self.assertTrue(r.recording and not r.paused)
        r.mic_first = r.clock.t0
        self.assertTrue(r.pause())
        self.assertFalse(r.pause())  # already paused
        self.assertTrue(r.paused)
        e1 = r.elapsed()
        time.sleep(0.3)
        self.assertAlmostEqual(r.elapsed(), e1, delta=0.05)  # standing still
        self.assertTrue(r.resume())
        self.assertFalse(r.resume())
        self.assertFalse(r.paused)
        out = r.stop()
        self.assertEqual(out, d)
        meta = json.loads((d / "meta.json").read_text())
        self.assertEqual(len(meta["paused"]), 1)
        self.assertGreaterEqual(meta["paused"][0]["for"], 0.2)
        self.assertLess(meta["duration"], 0.2 + meta["paused"][0]["for"] + 1.0)  # the paused time is not in the duration

    def test_cancel_deletes_the_folder_and_a_new_recording_starts_clean(self):
        r, root = self.recorder()
        d = r.start()
        r.pause()
        self.assertEqual(r.cancel(), d)
        self.assertFalse(d.exists())
        self.assertFalse(r.recording)
        r.start()
        self.assertFalse(r.paused)  # the clock was reset
        self.assertEqual(r.clock.pauses, [])


if __name__ == "__main__":
    unittest.main()
