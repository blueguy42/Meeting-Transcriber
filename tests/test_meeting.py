import tempfile
import time
import unittest
from pathlib import Path

from meeting_transcriber.meetings.meeting import Meeting


class Layout(unittest.TestCase):
    def meeting(self):
        return Meeting(Path(tempfile.mkdtemp()) / "2026-10-29_1000")

    def test_a_new_meeting_has_its_folders_and_a_name(self):
        m = self.meeting()
        m.create()
        self.assertTrue(m.shots_dir.is_dir() and m.recordings_dir.is_dir())
        self.assertEqual(m.name, "2026-10-29_1000")
        self.assertEqual(m.audio_dir, m.recordings_dir)
        with self.assertRaises(FileExistsError):
            m.create()  # a folder that exists is never reused

    def test_older_meetings_keep_the_audio_and_transcript_where_they_were(self):
        m = self.meeting()
        m.folder.mkdir(parents=True)
        self.assertEqual(m.audio_dir, m.folder)  # no recordings/ folder: the tracks are in the meeting folder
        (m.folder / "transcript").mkdir()
        (m.folder / "transcript" / "transcript.md").write_text("old")
        self.assertEqual(m.transcript_file, m.folder / "transcript" / "transcript.md")
        (m.folder / "transcript.md").write_text("new")
        self.assertEqual(m.transcript_file, m.folder / "transcript.md")  # the current location wins
        self.assertTrue(m.has_transcript)

    def test_meta_round_trip_and_has_recording(self):
        m = self.meeting()
        m.create()
        self.assertFalse(m.has_recording)
        m.write_meta({"started": "2026-10-29T10:00:00", "duration": 5.0})
        self.assertTrue(m.has_recording)
        self.assertEqual(m.read_meta()["duration"], 5.0)

    def test_summaries_newest_first_and_the_style_cannot_leave_the_folder(self):
        m = self.meeting()
        m.create()
        m.summary_file("default").write_text("a")
        time.sleep(0.02)
        m.summary_file("action-items-only").write_text("b")
        self.assertEqual([p.name for p in m.summaries()], ["summary-action-items-only.md", "summary-default.md"])
        self.assertEqual(m.summary_file("../../etc/x").parent, m.folder)

    def test_shots_are_named_by_the_second_and_listed_in_order(self):
        m = self.meeting()
        m.create()
        m.shot_file(754).write_bytes(b"x")
        m.shot_file(9).write_bytes(b"x")
        (m.shots_dir / "other.jpg").write_bytes(b"x")
        self.assertEqual([p.name for p in m.shot_files()], ["shot_00009.jpg", "shot_00754.jpg"])

    def test_two_meetings_for_the_same_folder_are_equal(self):
        self.assertEqual(Meeting("/m/a"), Meeting(Path("/m/a")))
        self.assertEqual(len({Meeting("/m/a"), Meeting("/m/a")}), 1)


if __name__ == "__main__":
    unittest.main()
