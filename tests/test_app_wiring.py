"""The App puts its parts together: build it with the system pieces (hotkeys, microphone, threads, timers) stubbed out, and
check that what the bar asks for and what its buttons do reach the right part."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from meeting_transcriber.app import app as app_module
from meeting_transcriber.settings import config
from meeting_transcriber.settings.model import Settings


class Wiring(unittest.TestCase):
    def setUp(self):
        cfg = Settings(meetings_dir=Path(tempfile.mkdtemp()))
        patches = [
            mock.patch.object(config, "ensure_settings"),
            mock.patch.object(config, "load", return_value=cfg),
            mock.patch.object(app_module, "Hotkeys"),
            mock.patch.object(app_module, "every"),
            mock.patch.object(app_module, "on_main"),
            mock.patch.object(app_module, "AppHelper"),
            mock.patch.object(app_module, "permissions"),
            mock.patch("meeting_transcriber.app.permission_controller.permissions"),
            mock.patch("meeting_transcriber.app.permission_controller.threading"),
        ]
        mocks = []
        for p in patches:
            mocks.append(p.start())
            self.addCleanup(p.stop)
        app_module.permissions.missing.return_value = []
        mocks[7].missing.return_value = []  # the permission controller's own view of the permissions
        self.app = app_module.App()

    def test_the_controllers_share_what_the_app_owns(self):
        a = self.app
        self.assertIs(a.recording.rec, a.rec)
        self.assertIs(a.recording.docs, a.docs)
        self.assertIs(a.processing.jobs, a.jobs)
        self.assertIs(a.presenter.rec, a.rec)
        self.assertIs(a.recording.app, a)

    def test_the_bar_asks_whether_to_show_in_captures(self):
        self.assertIs(self.app.presenter.overlay_in_captures(), False)
        self.app.cfg.overlay_in_captures = True
        self.assertIs(self.app.presenter.overlay_in_captures(), True)

    def test_the_bar_state_is_built_from_every_part(self):
        state = self.app.presenter.state()
        self.assertEqual((state["recording"], state["paused"], state["busy"], state["perm"], state["restart"]), (False, False, 0, False, False))
        self.assertIsNone(state["job"])

    def test_tips_menu_and_status_come_from_the_presenter(self):
        p = self.app.presenter
        self.assertEqual(p.overlay_tip("rec")["text"], "Start recording")
        self.assertEqual(p.overlay_tip("notes")["text"], "Take notes (while recording)")
        self.assertEqual(p.overlay_status(), {"jobs": [], "canRecord": True})
        self.assertEqual(p.overlay_docs(), {"docs": []})
        self.assertIn("settings", [i.get("id") for i in p.overlay_menu()])

    def test_a_button_reaches_its_controller(self):
        p, a = self.app.presenter, self.app
        with mock.patch.object(a.recording, "toggle") as toggle, mock.patch.object(a, "refresh_bar") as refresh:
            p.overlay_action("toggle")
        toggle.assert_called_once_with()
        refresh.assert_called_once_with()
        with mock.patch.object(a.permissions, "fix") as fix:
            p.overlay_action("perm")
        fix.assert_called_once_with()

    def test_menu_clicks_and_job_buttons_reach_the_processing_controller(self):
        p, a = self.app.presenter, self.app
        with mock.patch.object(a.processing, "summarize_chosen") as summarize:
            p.overlay_menu_click("summarize")
        summarize.assert_called_once_with()
        with mock.patch.object(a, "choose_prompt") as choose:
            p.overlay_menu_click("style:action-items-only")
        choose.assert_called_once_with("action-items-only")
        job = a.jobs.add(Path("/m/2026-10-29_1000"), summarize=True)
        with mock.patch.object(a.processing, "pause_job") as pause:
            a.jobs.begin(job)
            p.overlay_status_action("pause", job)
        pause.assert_called_once_with(job)

    def test_notes_typed_in_the_panel_are_kept_by_the_recording_controller(self):
        self.assertIsNone(self.app.presenter.overlay_notes_save("[00:10] a note"))
        self.assertEqual(self.app.recording.notes_text, "[00:10] a note")

    def test_the_hotkeys_are_the_recording_controllers(self):
        mapping = self.app._hotkeys.set.call_args[0][0]
        self.assertIn(self.app.recording.toggle, mapping.values())
        self.assertIn(self.app.recording.shot, mapping.values())

    def make_meeting(self):
        from meeting_transcriber.meetings.meeting import Meeting

        m = Meeting(self.app.cfg.meetings_dir / "2026-10-29_1000 - Weekly sync")
        m.create()
        m.write_meta({"started": "2026-10-29T10:00:00", "duration": 100.0})
        m.new_transcript_file.write_text("# T\n\n[00:00:03] Me: hi\n[00:00:06] Others 1: Budi here, hello everyone.\n[00:00:20] Others 2: Thanks.\n")
        return m

    def test_a_finished_meeting_in_the_panel_lists_its_speakers(self):
        a, m = self.app, self.make_meeting()
        job = a.jobs.add(m.folder, summarize=False)
        a.jobs.finish(job, m.folder, m.transcript_file)
        done = a.presenter.overlay_status()["jobs"][0]
        self.assertEqual([r["label"] for r in done["speakers"]], ["Others 1", "Others 2"])
        self.assertFalse(done["canPlay"])  # no recording kept in this folder
        self.assertFalse(done["speakersChecked"])

    def test_naming_a_speaker_from_the_panel_asks_for_a_name_and_updates_the_transcript_and_the_rows(self):
        a, m = self.app, self.make_meeting()
        job = a.jobs.add(m.folder, summarize=False)
        a.jobs.finish(job, m.folder, m.transcript_file)
        with mock.patch("meeting_transcriber.app.speaker_controller.ask_name", return_value="Rina") as ask, mock.patch("meeting_transcriber.app.speaker_controller.bring_to_front"):
            a.presenter.overlay_status_action("rename", job, "Others 2")
        self.assertEqual(ask.call_args[0][0], "Who is Others 2?")
        self.assertIn("] Rina: Thanks.", m.transcript_file.read_text())
        rows = {r["label"]: r["name"] for r in a.presenter.overlay_status()["jobs"][0]["speakers"]}
        self.assertEqual(rows, {"Others 1": None, "Others 2": "Rina"})  # the panel's list is fresh, not cached

    def test_a_name_that_is_refused_asks_again(self):
        a, m = self.app, self.make_meeting()
        job = a.jobs.add(m.folder, summarize=False)
        a.jobs.finish(job, m.folder, m.transcript_file)
        with mock.patch("meeting_transcriber.app.speaker_controller.ask_name", side_effect=["Me", "Rina"]) as ask, \
                mock.patch("meeting_transcriber.app.speaker_controller.alert") as alert, mock.patch("meeting_transcriber.app.speaker_controller.bring_to_front"):
            a.presenter.overlay_status_action("rename", job, "Others 2")
        self.assertEqual(ask.call_count, 2)
        alert.assert_called_once()
        self.assertIn("] Rina: Thanks.", m.transcript_file.read_text())

    def test_the_summary_is_updated_from_a_stale_panel_through_the_processing_controller(self):
        a, m = self.app, self.make_meeting()
        job = a.jobs.add(m.folder, summarize=False)
        a.jobs.finish(job, m.folder, m.transcript_file)
        other = a.jobs.add(m.folder, summarize=False)  # (a button dismisses its tab, so the second action needs its own)
        a.jobs.finish(other, m.folder, m.transcript_file)
        with mock.patch.object(a.processing, "run_pipeline") as run:
            a.presenter.overlay_status_action("resummarize", job)
            a.presenter.overlay_status_action("namespeakers", other)
        self.assertEqual([c.kwargs for c in run.call_args_list], [{"summarize_only": True}, {"name_only": True}])

    def test_the_next_sample_stops_the_one_that_is_playing(self):
        a, m = self.app, self.make_meeting()
        job = a.jobs.add(m.folder, summarize=False)
        a.jobs.finish(job, m.folder, m.transcript_file)
        key = (str(m.folder), "Others 1")
        with mock.patch.object(a.speakers.player, "stop") as stop:
            a.speakers.playing = key
            a.presenter.overlay_status_action("nextsample", job, "Others 1")
        stop.assert_called_once_with()
        self.assertEqual(a.speakers.sample_at[key], 1)  # and the next press plays the next sample

    def test_the_sample_of_a_named_speaker_stays_on_show_once_you_have_asked_for_another(self):
        a, m = self.app, self.make_meeting()
        job = a.jobs.add(m.folder, summarize=False)
        a.jobs.finish(job, m.folder, m.transcript_file)
        from meeting_transcriber.meetings import speakers

        speakers.set_names(m, a.cfg, {"Others 1": {"name": "Budi", "by": "claude"}})
        rows = lambda: {r["label"]: r["showSample"] for r in a.presenter.overlay_status()["jobs"][0]["speakers"]}
        self.assertEqual(rows(), {"Others 1": False, "Others 2": False})  # a named speaker shows no sample until you ask
        a.presenter.overlay_status_action("nextsample", job, "Others 1")
        self.assertEqual(rows()["Others 1"], True)  # and it does not vanish when the one playing stops

    def test_the_menu_can_name_the_speakers_of_any_meeting(self):
        menu = self.app.presenter.overlay_menu()
        process = next(i for i in menu if i.get("id") == "process")
        self.assertEqual([o["id"] for o in process["sub"]], ["transcribe", "speakers", "summarize"])  # one submenu, the steps in order
        self.assertNotIn("summarize", [i.get("id") for i in menu])  # (no longer loose in the main menu)
        with mock.patch.object(self.app.processing, "name_chosen") as chosen:
            self.app.presenter.overlay_menu_click("speakers")
        chosen.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
