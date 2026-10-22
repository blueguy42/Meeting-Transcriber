import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mt import audio, pipeline


def meeting(files):
    d = Path(tempfile.mkdtemp()) / "2026-10-20_1402"
    (d / "recordings").mkdir(parents=True)
    for name in files:
        (d / "recordings" / name).write_bytes(b"x")
    return d


class TranscribeExisting(unittest.TestCase):
    def test_has_audio(self):
        self.assertTrue(audio.has_audio(meeting(["mic.wav"])))
        self.assertTrue(audio.has_audio(meeting(["system.m4a"])))  # compressed after the first run
        self.assertFalse(audio.has_audio(meeting([])))

    def test_runs_the_whole_pipeline_and_leaves_the_old_transcript_alone_until_the_new_one_is_done(self):
        d = meeting(["mic.m4a"])
        (d / "transcript.md").write_text("old transcript")
        seen = []
        def fake_process(folder, cfg, prompt, progress):
            seen.append((folder / "transcript.md").read_text())  # still the old one while the work is under way
            return folder, folder / "summary.md"
        with mock.patch.object(pipeline, "process", side_effect=fake_process):
            out = pipeline.transcribe_existing(d, {"auto_summarize": True}, "default", progress=print)
        self.assertEqual(seen, ["old transcript"])
        self.assertFalse((d / "transcript.md.bak").exists())
        self.assertEqual(out, (d, d / "summary.md"))

    def test_refuses_a_meeting_without_a_recording(self):
        d = meeting([])
        with self.assertRaises(ValueError):
            pipeline.transcribe_existing(d, {}, None)


if __name__ == "__main__":
    unittest.main()
