"""Start, pause, resume and stop a Recorder with the audio devices left out (nothing is opened)."""

import tempfile
import time
import unittest
from pathlib import Path

from meeting_transcriber.recording import Recorder
from meeting_transcriber.settings.model import Settings


class Flow(unittest.TestCase):
    def recorder(self):
        root = Path(tempfile.mkdtemp())
        r = Recorder(Settings(meetings_dir=root), on_warning=lambda m: None)
        r._start_capture = lambda d: None  # no microphone, no system-audio helper
        r._close = lambda: None
        return r, root

    def test_the_clock_stops_while_paused_and_the_meta_says_so(self):
        r, root = self.recorder()
        meeting = r.start()
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
        self.assertEqual(out, meeting)
        meta = meeting.read_meta()
        self.assertEqual(len(meta["paused"]), 1)
        self.assertGreaterEqual(meta["paused"][0]["for"], 0.2)
        self.assertLess(meta["duration"], 0.2 + meta["paused"][0]["for"] + 1.0)  # the paused time is not in the duration

    def test_cancel_deletes_the_folder_and_a_new_recording_starts_clean(self):
        r, root = self.recorder()
        meeting = r.start()
        r.pause()
        self.assertEqual(r.cancel(), meeting)
        self.assertFalse(meeting.folder.exists())
        self.assertFalse(r.recording)
        r.start()
        self.assertFalse(r.paused)  # the clock was reset
        self.assertEqual(r.clock.pauses, [])


class MetaWhileRecording(unittest.TestCase):
    def recorder(self):
        r = Recorder(Settings(meetings_dir=Path(tempfile.mkdtemp())), on_warning=lambda m: None)
        r._start_capture = lambda m: None
        return r

    def test_a_recording_can_be_recovered_after_a_crash(self):
        r = self.recorder()
        meeting = r.start()
        meta = meeting.read_meta()  # there from the first moment, and says it is not finished
        self.assertEqual(meta["complete"], False)
        r.mic_first = r.clock.t0 + 0.04
        r.pause()
        r._save_meta(time.time(), complete=False)  # what the background saver does a moment after a change
        meta = meeting.read_meta()
        self.assertEqual(len(meta["paused"]), 1)
        self.assertAlmostEqual(meta["mic_offset"], 0.0, places=3)
        r.stop()
        self.assertTrue(meeting.read_meta()["complete"])

    def test_saves_from_many_threads_never_leave_a_half_written_file_and_stop_has_the_last_word(self):
        import threading

        r = self.recorder()
        meeting = r.start()
        stop = threading.Event()
        problems = []

        def reader():
            while not stop.is_set():
                try:
                    meeting.read_meta()
                except FileNotFoundError:
                    problems.append("missing")
                except ValueError:
                    problems.append("half written")

        def writer():
            while not stop.is_set():
                r._save_meta(time.time(), complete=False)
                r.set_muted("mic", True)
                r.set_muted("mic", False)

        threads = [threading.Thread(target=f) for f in (reader, reader, writer, writer)]
        for t in threads:
            t.start()
        time.sleep(0.6)
        r.stop()  # while the others are still going
        stop.set()
        for t in threads:
            t.join()
        self.assertEqual(problems, [])
        self.assertTrue(meeting.read_meta()["complete"])
        self.assertEqual(list(meeting.folder.glob("*.tmp")), [])

    def test_cancelling_leaves_nothing_behind_for_the_saver_to_recreate(self):
        r = self.recorder()
        meeting = r.start()
        r.cancel()
        time.sleep(0.8)
        self.assertFalse(meeting.folder.exists())


class Threads(unittest.TestCase):
    """The recorder's background loops (they are started by _start_capture, which needs a microphone, so they are run here by hand)."""

    def recorder(self):
        return Recorder(Settings(meetings_dir=Path(tempfile.mkdtemp())), on_warning=lambda m: warnings.append(m))

    def test_the_helper_reader_notes_when_system_audio_started_and_passes_errors_on(self):
        from types import SimpleNamespace

        global warnings
        warnings = []
        r = self.recorder()
        r._proc = SimpleNamespace(stdout=iter(["FIRST 1234.5\n", "ERROR no permission\n"]))
        r._read_helper()
        self.assertEqual(r.sys_first, 1234.5)
        self.assertTrue(r._meta_dirty.is_set())  # the saver is told
        self.assertIn("no permission", warnings[0])

    def test_the_writer_loop_writes_what_it_is_given_until_told_to_stop(self):
        r = self.recorder()
        written = []
        r._wav = SimpleNamespace_writer = type("W", (), {"writeframes": lambda s, c: written.append(c)})()
        r._q.put(b"ab")
        r._q.put(b"cd")
        r._q.put(None)
        r._write_loop()
        self.assertEqual(written, [b"ab", b"cd"])

    def test_every_loop_the_recorder_starts_exists(self):
        for name in ("_read_helper", "_write_loop", "_watch_mic", "_watch_output", "_meta_loop"):
            self.assertTrue(callable(getattr(Recorder, name)), name)


if __name__ == "__main__":
    unittest.main()
