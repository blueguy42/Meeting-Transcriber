"""Naming the other speakers: what is kept in meta.json, how the transcript is rewritten, what Claude's answer must pass,
the samples offered for listening, and the pipeline step that asks Claude."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from meeting_transcriber.meetings import speakers
from meeting_transcriber.meetings.meeting import Meeting
from meeting_transcriber.processing import pipeline
from meeting_transcriber.settings.model import Settings
from meeting_transcriber.summary import name_speakers

TRANSCRIPT = """# Transcript (Thursday 2026-10-29 10:00, 3 min)

[00:00:03] Me: Morning everyone, shall we start?
[00:00:06] Others 1: Morning. Budi here, I'll cover the budget first.
[00:00:41] Others 1: ...so that is the Q4 number. Dina, over to you.
[00:00:44] Others 2: Thanks. The campaign went live on the third, and the numbers are looking good so far.
[00:01:20] Others 3: Can I add something on the vendor side?
"""


def make(transcript=TRANSCRIPT, **meta):
    m = Meeting(Path(tempfile.mkdtemp()) / "2026-10-29_1000")
    m.create()
    m.write_meta({"started": "2026-10-29T10:00:00", "duration": 180.0, "system_offset": 0.25, **meta})
    if transcript:
        m.new_transcript_file.write_text(transcript)
    return m


class Names(unittest.TestCase):
    def setUp(self):
        self.cfg = Settings()

    def test_the_other_speakers_are_found_in_order(self):
        self.assertEqual(speakers.raw_labels(make(), self.cfg), ["Others 1", "Others 2", "Others 3"])

    def test_a_name_rewrites_every_line_of_that_speaker_and_keeps_the_timestamps(self):
        m = make()
        speakers.rename(m, self.cfg, "Others 3", "Rina")
        text = m.transcript_file.read_text()
        self.assertIn("[00:01:20] Rina: Can I add something on the vendor side?", text)
        self.assertIn("[00:00:06] Others 1: Morning.", text)  # the others are untouched
        self.assertEqual(m.read_meta()["speakers"]["Others 3"], {"name": "Rina", "by": "user"})
        self.assertEqual(speakers.raw_labels(m, self.cfg), ["Others 1", "Others 2", "Others 3"])  # still found under its name

    def test_changing_and_removing_a_name(self):
        m = make()
        speakers.rename(m, self.cfg, "Others 1", "Budi")
        speakers.rename(m, self.cfg, "Others 1", "Bimo")
        self.assertIn("[00:00:41] Bimo: ...so that", m.transcript_file.read_text())
        speakers.rename(m, self.cfg, "Others 1", "")  # back to the label
        self.assertIn("[00:00:41] Others 1: ...so that", m.transcript_file.read_text())
        self.assertNotIn("Others 1", m.read_meta()["speakers"])

    def test_your_own_label_cannot_be_used_and_long_names_are_refused(self):
        m = make()
        with self.assertRaises(ValueError):
            speakers.rename(m, self.cfg, "Others 1", "me")
        with self.assertRaises(ValueError):
            speakers.rename(m, self.cfg, "Others 1", "x" * 41)
        self.assertEqual(m.transcript_file.read_text(), TRANSCRIPT)

    def test_a_name_the_user_chose_is_never_replaced_by_claude(self):
        m = make()
        speakers.rename(m, self.cfg, "Others 1", "Bimo")
        speakers.set_names(m, self.cfg, {"Others 1": {"name": "Budi", "by": "claude"}, "Others 2": {"name": "Dina", "by": "claude"}})
        names = m.read_meta()["speakers"]
        self.assertEqual((names["Others 1"]["name"], names["Others 2"]["name"]), ("Bimo", "Dina"))

    def test_renaming_after_a_summary_exists_marks_it_out_of_date_until_a_new_one_is_written(self):
        m = make()
        (m.folder / "summary-default.md").write_text("# Weekly sync")
        speakers.rename(m, self.cfg, "Others 1", "Budi")
        self.assertTrue(m.read_meta()["summary_stale"])
        speakers.mark_summary_current(m)
        self.assertNotIn("summary_stale", m.read_meta())

    def test_a_new_transcript_keeps_the_users_names_and_forgets_claudes(self):
        m = make()
        speakers.set_names(m, self.cfg, {"Others 1": {"name": "Budi", "by": "claude"}})
        speakers.rename(m, self.cfg, "Others 3", "Rina")
        m.new_transcript_file.write_text(TRANSCRIPT)  # transcribed again: raw labels once more
        speakers.apply_saved(m, self.cfg)
        text = m.transcript_file.read_text()
        self.assertIn("] Rina: Can I add", text)
        self.assertIn("] Others 1: Morning", text)  # Claude looks again
        self.assertEqual(list(m.read_meta()["speakers"]), ["Others 3"])

    def test_a_meeting_without_other_speakers_has_nothing_to_name(self):
        m = make("# Transcript\n\n[00:00:03] Me: hello\n[00:00:09] Others: hi\n")
        self.assertEqual(speakers.raw_labels(m, self.cfg), ["Others"])  # one other voice is still a person
        self.assertEqual(speakers.raw_labels(make("# Transcript\n\n[00:00:03] Me: just me\n"), self.cfg), [])


class Samples(unittest.TestCase):
    def test_samples_are_the_longer_lines_with_room_to_play(self):
        m = make()
        cfg = Settings()
        found = speakers.samples(m, cfg, "Others 2")
        self.assertEqual(found[0]["at"], "00:44")
        self.assertTrue(found[0]["text"].startswith("Thanks. The campaign"))
        self.assertLessEqual(found[0]["end"] - found[0]["start"], speakers.SAMPLE_SECONDS)

    def test_the_system_track_starts_a_little_later_than_the_recording(self):
        self.assertEqual(speakers.track_time(make(), 80.0), 79.75)
        self.assertEqual(speakers.track_time(make(), 0.1), 0.0)

    def test_rows_list_each_speaker_with_the_name_and_where_it_came_from(self):
        m = make()
        speakers.set_names(m, Settings(), {"Others 1": {"name": "Budi", "by": "claude", "evidence": "Budi here", "at": "00:06"}})
        rows = speakers.rows(m, Settings())
        self.assertEqual([(r["label"], r["name"], r["by"]) for r in rows], [("Others 1", "Budi", "claude"), ("Others 2", None, None), ("Others 3", None, None)])
        self.assertEqual(rows[0]["lines"], 2)

    def test_audio_is_found_whichever_way_it_was_kept(self):
        m = make()
        self.assertIsNone(speakers.audio_file(m))
        (m.recordings_dir / "system.m4a").write_bytes(b"x")
        self.assertEqual(speakers.audio_file(m).name, "system.m4a")


REPLY = json.dumps({"speakers": [
    {"label": "Others 1", "name": "Budi", "evidence": "Budi here, I'll cover the budget"},
    {"label": "Others 2", "name": "Dina", "evidence": "Dina, over to you"},
    {"label": "Others 3", "name": "Andi", "evidence": "this line was never said"},  # no proof in the transcript
    {"label": "Others 9", "name": "Zed", "evidence": "Budi here, I'll cover the budget"},  # not a speaker
    {"label": "Me", "name": "Teguh", "evidence": "Morning everyone"},  # the user's own label is never renamed
], "people": ["Budi", "Dina", "Rina", "Vendor Co", "x" * 50, "Tokopedia"]})


class ClaudesAnswer(unittest.TestCase):
    def setUp(self):
        self.cfg = Settings()
        self.m = make()

    def test_only_what_is_proved_by_a_line_in_the_transcript_is_used(self):
        named = name_speakers.check(REPLY, self.m, self.cfg)
        self.assertEqual({k: v["name"] for k, v in named.items()}, {"Others 1": "Budi", "Others 2": "Dina"})
        self.assertEqual(named["Others 2"]["at"], "00:41")  # where the proof is

    def test_the_same_name_for_two_speakers_is_used_for_neither_unless_they_are_one_person(self):
        twice = json.dumps({"speakers": [{"label": "Others 1", "name": "Budi", "evidence": "Budi here, I'll cover"},
                                         {"label": "Others 3", "name": "Budi", "evidence": "Budi here, I'll cover"}]})
        self.assertEqual(name_speakers.check(twice, self.m, self.cfg), {})
        same = json.dumps({"speakers": [{"label": "Others 1", "name": "Budi", "evidence": "Budi here, I'll cover"},
                                        {"label": "Others 3", "name": "Budi", "evidence": "Budi here, I'll cover"}], "same": [["Others 1", "Others 3"]]})
        self.assertEqual(set(name_speakers.check(same, self.m, self.cfg)), {"Others 1", "Others 3"})

    def test_the_people_it_saw_mentioned_are_only_names_that_are_really_in_the_transcript(self):
        self.assertEqual(name_speakers.people(REPLY, self.m, self.cfg), ["Budi", "Dina"])  # "Rina" and the others are not said anywhere
        self.assertEqual(name_speakers.people("nonsense", self.m, self.cfg), [])

    def test_the_suggestions_for_a_name_are_people_not_title_words_or_terms(self):
        m = make()
        speakers.mark_checked(m, ["Budi", "Dina"])
        speakers.rename(m, self.cfg, "Others 3", "Rina")
        cfg = Settings(vocabulary=["Tokopedia"])
        self.assertEqual(speakers.roster(m, cfg), ["Rina", "Budi", "Dina"])  # no "Weekly", "sync", "Tokopedia"

    def test_an_answer_that_is_not_json_names_nobody(self):
        for reply in ("I could not find any names.", "", "{broken", '{"speakers": "none"}'):
            self.assertEqual(name_speakers.check(reply, self.m, self.cfg), {}, reply)

    def test_json_inside_a_code_fence_still_works(self):
        self.assertEqual(len(name_speakers.check("```json\n" + REPLY + "\n```", self.m, self.cfg)), 2)

    def test_asking_claude_writes_the_names_and_remembers_it_looked(self):
        done = mock.Mock(returncode=0, stdout=REPLY, stderr="")
        with mock.patch.object(name_speakers.config, "find_claude", return_value="/bin/claude"), mock.patch.object(name_speakers, "_run_claude", return_value=done) as run:
            named = name_speakers.name_speakers(self.m, self.cfg)
        self.assertEqual(set(named), {"Others 1", "Others 2"})
        self.assertIn("] Budi: Morning.", self.m.transcript_file.read_text())
        self.assertTrue(self.m.read_meta()["speakers_checked"])
        self.assertEqual(self.m.read_meta()["people"], ["Budi", "Dina"])
        self.assertIn(TRANSCRIPT.splitlines()[3], run.call_args[0][1])  # the transcript was sent as text
        self.assertNotIn("--allowedTools", run.call_args[0][0])  # and no tool is allowed

    def test_a_failure_of_claude_is_an_error_not_a_silent_success(self):
        failed = mock.Mock(returncode=1, stdout="", stderr="not signed in")
        with mock.patch.object(name_speakers.config, "find_claude", return_value="/bin/claude"), mock.patch.object(name_speakers, "_run_claude", return_value=failed):
            with self.assertRaises(RuntimeError):
                name_speakers.name_speakers(self.m, self.cfg)
        self.assertNotIn("speakers_checked", self.m.read_meta())


class PipelineStep(unittest.TestCase):
    def setUp(self):
        self.cfg = Settings()
        self.m = make()
        self.said = []

    def test_a_failure_of_naming_never_fails_the_meeting(self):
        with mock.patch.object(pipeline.config, "find_claude", return_value="/bin/claude"), \
                mock.patch.object(pipeline, "name_speakers", side_effect=RuntimeError("offline")):
            pipeline.name_the_speakers(self.m, self.cfg, self.said.append)
        self.assertEqual(self.said[0], "Naming speakers…")
        self.assertTrue(self.said[1].startswith("Speaker names skipped: offline"))
        self.assertEqual(self.m.transcript_file.read_text(), TRANSCRIPT)

    def test_it_is_skipped_when_off_or_without_claude_or_without_speaker_separation(self):
        for cfg, claude in ((self.cfg.replace(name_speakers=False), "/bin/claude"), (self.cfg.replace(diarize=False), "/bin/claude"), (self.cfg, None)):
            with mock.patch.object(pipeline.config, "find_claude", return_value=claude), mock.patch.object(pipeline, "name_speakers") as ask:
                pipeline.name_the_speakers(self.m, cfg, self.said.append)
            ask.assert_not_called()
        self.assertEqual(self.said, [])

    def test_a_pause_while_claude_is_working_goes_up(self):
        from meeting_transcriber.processing.control import Stopped

        with mock.patch.object(pipeline.config, "find_claude", return_value="/bin/claude"), \
                mock.patch.object(pipeline, "name_speakers", side_effect=Stopped("pause")):
            with self.assertRaises(Stopped):
                pipeline.name_the_speakers(self.m, self.cfg, self.said.append)


if __name__ == "__main__":
    unittest.main()
