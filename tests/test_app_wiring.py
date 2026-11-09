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


if __name__ == "__main__":
    unittest.main()
