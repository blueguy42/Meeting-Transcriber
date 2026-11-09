"""The app: records, transcribes and summarizes meetings, controlled from the floating bar (bar/).

Run via the double-clickable MeetingTranscriber.app (or `python -m meeting_transcriber`).
"""

import logging
import re

from PyObjCTools import AppHelper

from .. import log as mtlog  # (sets the environment that quiets the libraries the modules below import)
from ..desktop import permissions
from ..desktop.hotkeys import Hotkeys
from ..desktop.macos import alert, bring_to_front, every, hide_dock_icon, notify, on_main, stay_responsive
from ..meetings import documents
from ..processing.jobs import Tracker
from ..recording import Recorder
from ..settings import config
from ..settings.model import Settings
from .bar_presenter import BarPresenter
from .permission_controller import PermissionController
from .processing_controller import ProcessingController
from .recording_controller import RecordingController

log = logging.getLogger(__name__)


class App:
    """The app. It owns what the parts share (the settings, the recorder, the job list, the document reader, the bar) and
    puts the parts together: `recording`, `processing`, `permissions` do the work and `presenter` tells the bar what to show.
    It also owns the hotkeys and quitting."""

    def __init__(self):
        config.ensure_settings()
        self.cfg = config.defaults()
        self.reload_cfg()
        self.cfg.meetings_dir.mkdir(parents=True, exist_ok=True)
        self._progress_throttle = mtlog.Throttle()
        log.info("Meetings folder: %s; permissions missing: %s", self.cfg.meetings_dir, permissions.missing() or "none")
        self.prompt = self.cfg.default_prompt  # the Summary style
        self.bar = None  # the floating control bar, created once the app is running
        self._settings = None  # the Settings window, created on first use
        self.rec = Recorder(self.cfg, on_warning=lambda m: notify("Meeting Transcriber", "Warning", m))
        self.jobs = Tracker((self.cfg.my_label, self.cfg.others_label), on_change=lambda: on_main(self.refresh_bar))
        self.docs = documents.DocumentWorker(on_change=lambda: on_main(self.refresh_bar))  # reads the documents you add, in the background
        self.permissions = PermissionController(self)
        self.recording = RecordingController(self)
        self.processing = ProcessingController(self)
        self.presenter = BarPresenter(self)
        self.rec.on_device_change = lambda track, device: on_main(self.presenter.device_toast, track, device)  # called from the recorder's watcher threads
        try:
            self._hotkeys = Hotkeys()
        except OSError as e:
            self._hotkeys = None
            notify("Meeting Transcriber", "Global hotkeys are off", str(e)[:150])

        self.rec.mic_muted = bool(self.cfg.start_muted_mic)  # the "when the app opens" setting
        self.rec.sys_muted = bool(self.cfg.start_muted_system)
        self._apply_settings()
        on_main(self.refresh_bar)  # shows the bar as soon as the run loop starts
        every(1, self.refresh_bar)
        AppHelper.callLater(1, self.permissions.welcome)  # first-launch permission step, once the bar is up

    def reload_cfg(self) -> Settings:
        """Re-read config.toml. A typo in it must not crash the app: keep the last good settings."""
        try:
            self.cfg = config.load()
        except Exception as e:
            notify("Meeting Transcriber", "config.toml has an error - using previous settings", str(e)[:150])
        return self.cfg

    # ---- settings window
    def open_settings(self):
        if self._settings is None:
            from ..settings.window import SettingsWindow

            self._settings = SettingsWindow(on_change=self._on_settings_changed)
        self._settings.show()

    def _on_settings_changed(self):
        self.reload_cfg()
        self._apply_settings()

    def _apply_settings(self):
        """Make the hotkeys follow the current settings."""
        self.prompt = self.cfg.default_prompt
        self.rec.cfg = self.cfg if not self.rec.recording else self.rec.cfg  # never change mid-recording
        if self.bar is not None:
            self.bar.set_capturable(bool(self.cfg.overlay_in_captures))
        self._start_hotkeys()

    def choose_prompt(self, name):
        try:
            config.set_value("default_prompt", name)  # remembered for the next launch
        except Exception as e:
            notify("Meeting Transcriber", "Could not save setting", str(e)[:150])
            return
        self._on_settings_changed()

    def status(self, text):
        # the progress shown on the bar comes from processing/jobs.py; the log gets the steps, not every percent
        if self._progress_throttle(text):
            problem = re.search(r"silent|failed|not told apart|muted", text, re.I)
            log.log(logging.WARNING if problem else logging.INFO, text)

    def refresh_bar(self):
        """Create the bar on first use and keep it showing the app's current state."""
        try:
            if self.bar is None:
                from ..bar import Bar

                self.bar = Bar(self.presenter)
                self.bar.show()
            self.bar.update(self.presenter.state())
        except Exception as e:
            log.exception("Control bar failed to start")
            if self.bar is None:  # without the bar there is no way to use the app
                bring_to_front()
                alert("The control bar could not start", f"{str(e)[:300]}\n\nSee ~/Library/Logs/MeetingTranscriber.log.")
                self.quit()

    # ---- hotkeys
    def _start_hotkeys(self):
        """(Re)register the global hotkeys. Callbacks arrive on the main thread."""
        if self._hotkeys is None:
            return
        problems = self._hotkeys.set(
            {
                self.cfg.hotkey_screenshot: self.recording.shot,
                self.cfg.hotkey_notes: self.recording.toggle_notes,
                self.cfg.hotkey_documents: self.recording.add_documents,
                self.cfg.hotkey_record: self.recording.toggle,
                self.cfg.hotkey_mute_mic: self.recording.toggle_mic,
                self.cfg.hotkey_mute_system: self.recording.toggle_system,
            }
        )
        if problems:
            notify("Meeting Transcriber", "Hotkey problem", "; ".join(problems)[:200])

    def confirm_quit(self):
        """The quit button sits right on the bar, so ask before it throws away work in progress."""
        what = "A recording is in progress" if self.rec.recording else "A meeting is still being processed" if self.jobs.busy else None
        if what:
            bring_to_front()
            if not alert("Quit Meeting Transcriber?", f"{what}. " + ("Quitting stops it; the recording is kept and can be summarized later."
                         if self.rec.recording else "Quitting now stops it; you can summarize the meeting again later."), ok="Quit", cancel="Cancel"):
                return
        self.quit()

    def quit(self):
        if self.rec.recording:
            if self.bar is not None:
                self.bar.close_notes()  # the notes panel saves what is still unsaved...
            self.rec.stop()
            AppHelper.callLater(0.4, self._terminate)  # ...a moment later, when that has arrived
            return
        self._terminate()

    def _terminate(self):
        from AppKit import NSApp

        NSApp.terminate_(None)


def main():
    mtlog.setup()
    hide_dock_icon()
    from ..settings.window import install_edit_menu

    install_edit_menu()  # no menu bar: without a main menu Cmd+A / C / V / Z would not work in the notes panel or Settings
    stay_responsive()
    app = App()  # keep a reference: the timers and the bar call back into it
    AppHelper.runEventLoop()
    return app


if __name__ == "__main__":
    main()
