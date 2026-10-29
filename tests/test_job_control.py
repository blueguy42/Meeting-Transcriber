import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mt import control, progress, summarize, transcribe
from mt.jobs import Tracker


class Control(unittest.TestCase):
    def test_check_raises_only_when_asked_and_not_while_saving(self):
        c = control.JobControl()
        control.bind(c)
        try:
            control.check()  # nothing asked
            self.assertTrue(c.stop("pause"))
            with self.assertRaises(control.Stopped) as cm:
                control.check()
            self.assertEqual(cm.exception.reason, "pause")
            c.locked = True  # saving the files
            control.check()
            c2 = control.JobControl()
            c2.locked = True
            self.assertFalse(c2.stop("cancel"))  # too late
        finally:
            control.bind(None)
        control.check()  # outside a job: nothing


class Checkpoint(unittest.TestCase):
    def setUp(self):
        self.calls = 0

        def fake_transcribe(audio, **kw):
            self.calls += 1
            return {"language": "en", "segments": [{"start": 0.0, "end": 1.0, "text": f" hello there number {self.calls}", "avg_logprob": -0.2, "no_speech_prob": 0.0, "compression_ratio": 1.0}]}

        sys.modules["mlx_whisper"] = SimpleNamespace(transcribe=fake_transcribe)
        self.addCleanup(lambda: sys.modules.pop("mlx_whisper", None))
        self.cfg = {"model": "m", "language": "en", "allowed_languages": ["en"], "vocabulary": []}
        self.audio = np.zeros(16000 * 60, dtype=np.float32)
        self.regions = [(i * 100, i * 100 + 50) for i in range(6)]  # six bursts (envelope frames)

    def test_a_pause_keeps_the_finished_bursts_and_resume_does_the_rest(self):
        d = Path(tempfile.mkdtemp())
        ck = transcribe.Checkpoint(d)

        def stop_at_three(f):
            if f >= 3 / 6:
                raise control.Stopped("pause")

        with self.assertRaises(control.Stopped):
            transcribe.transcribe_regions(self.audio, self.regions, self.cfg, 0.0, "Me", stop_at_three, ck=ck)
        self.assertEqual(self.calls, 3)
        self.assertTrue((d / transcribe.PARTIAL).exists())
        # the next run (a new Checkpoint object, as after a restart of the job) continues with burst 4
        out = transcribe.transcribe_regions(self.audio, self.regions, self.cfg, 0.0, "Me", ck=transcribe.Checkpoint(d))
        self.assertEqual(self.calls, 6)  # only three more bursts were transcribed
        self.assertEqual(len(out), 6)
        self.assertEqual([s["text"] for s in out][:3], ["hello there number 1", "hello there number 2", "hello there number 3"])

    def test_a_changed_setting_starts_over(self):
        d = Path(tempfile.mkdtemp())
        ck = transcribe.Checkpoint(d)

        def stop_at_two(f):
            if f >= 2 / 6:
                raise control.Stopped("pause")

        with self.assertRaises(control.Stopped):
            transcribe.transcribe_regions(self.audio, self.regions, self.cfg, 0.0, "Me", stop_at_two, ck=ck)
        before = self.calls
        transcribe.transcribe_regions(self.audio, self.regions, {**self.cfg, "model": "other"}, 0.0, "Me", ck=transcribe.Checkpoint(d))
        self.assertEqual(self.calls - before, 6)  # a different model: nothing is reused

    def test_clear_removes_the_file(self):
        d = Path(tempfile.mkdtemp())
        ck = transcribe.Checkpoint(d)
        ck.update("Me", "x", 1, [], {})
        ck.save()
        self.assertTrue((d / transcribe.PARTIAL).exists())
        transcribe.clear_checkpoint(d)
        self.assertFalse((d / transcribe.PARTIAL).exists())


class ClaudeProcess(unittest.TestCase):
    def test_a_cancel_kills_it_at_once(self):
        c = control.JobControl()
        control.bind(c)
        try:
            import threading

            threading.Timer(0.5, lambda: c.stop("cancel")).start()
            t0 = time.monotonic()
            with self.assertRaises(control.Stopped):
                summarize._run_claude(["sleep", "30"], "", tempfile.mkdtemp(), os.environ)
            self.assertLess(time.monotonic() - t0, 5)
        finally:
            control.bind(None)

    def test_it_still_returns_the_output(self):
        r = summarize._run_claude(["cat"], "hello", tempfile.mkdtemp(), os.environ)
        self.assertEqual((r.returncode, r.stdout), (0, "hello"))


class JobStates(unittest.TestCase):
    def tracker(self):
        t = Tracker(("Me", "Others"))
        i = t.add(Path("/m/2026-10-29_1500"), summarize=True, diarize=True)
        t.begin(i)
        return t, i

    def snap(self, t):
        return t.snapshot()[0]

    def test_pausing_while_transcribing_remembers_the_step_and_resume_carries_on(self):
        t, i = self.tracker()
        t.feed(i, progress.transcribing("Me", 1.0))
        t.feed(i, progress.transcribing("Others", 0.62))
        t.stopping(i, "pause")
        self.assertEqual(self.snap(t)["stopping"], "pause")
        t.pause(i)
        j = self.snap(t)
        self.assertEqual((j["state"], j["stage"], j["paused_at"], j["stopping"]), ("paused", "paused", "transcribe", ""))
        step = next(s for s in j["steps"] if s["key"] == "transcribe")
        self.assertEqual(step["state"], "paused")
        self.assertEqual([(c["state"], c["sub"]) for c in step["children"]][:2], [("done", ""), ("paused", "Paused · 62%")])
        self.assertIn("last speech burst", j["note"])
        self.assertGreater(j["progress"], 0.1)  # the ring keeps what was done
        t.resume(i)
        self.assertEqual(self.snap(t)["state"], "queued")
        t.begin(i)
        j = self.snap(t)
        self.assertEqual((j["state"], j["paused_at"]), ("active", None))
        self.assertEqual(next(s for s in j["steps"] if s["key"] == "transcribe")["state"], "now")

    def test_pausing_while_summarizing_says_the_step_starts_again(self):
        t, i = self.tracker()
        t.feed(i, progress.SUMMARIZING)
        t.pause(i)
        j = self.snap(t)
        self.assertEqual(j["paused_at"], "summarize")
        self.assertIn("starts this step again", j["note"])

    def test_speakers_start_again_but_the_transcript_is_kept(self):
        t, i = self.tracker()
        t.feed(i, progress.transcribing("Me", 1.0))
        t.feed(i, progress.transcribing("Others", 1.0))
        t.feed(i, progress.diarizing(40))
        t.pause(i)
        self.assertIn("The transcript is kept", self.snap(t)["note"])

    def test_cancel_marks_the_step_and_dismiss_removes_the_row(self):
        t, i = self.tracker()
        t.feed(i, progress.SUMMARIZING)
        t.cancel(i)
        j = self.snap(t)
        self.assertEqual((j["state"], j["stage"]), ("cancelled", "cancelled"))
        self.assertEqual(next(s for s in j["steps"] if s["key"] == "summarize")["state"], "cancelled")
        self.assertEqual(t.busy, 0)  # not counted as being processed
        t.dismiss(i)
        self.assertEqual(t.snapshot(), [])

    def test_a_paused_job_can_be_cancelled_and_a_waiting_one_too(self):
        t, i = self.tracker()
        t.pause(i)
        t.cancel(i)
        self.assertEqual(self.snap(t)["state"], "cancelled")
        q = t.add(Path("/m/2026-10-29_1600"), summarize=True)
        t.cancel(q)  # still waiting
        self.assertEqual({j["state"] for j in t.snapshot()}, {"cancelled"})


if __name__ == "__main__":
    unittest.main()
