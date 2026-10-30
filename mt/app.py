"""The app: records, transcribes and summarizes meetings, controlled from the floating bar (mt/overlay/).

Run via the double-clickable MeetingTranscriber.app (or `python -m mt.app`).
"""

import logging
import re
import threading
from pathlib import Path

from PyObjCTools import AppHelper

from . import config, control, documents, permissions
from . import log as mtlog  # (sets the environment that quiets the libraries the modules below import)
from .desktop import alert, bring_to_front, every, hide_dock_icon, notify, on_main, stay_responsive
from .devicetoast import DeviceToasts
from .hotkeys import Hotkeys
from .jobs import Tracker
from .overlay_host import OverlayHost
from .permission_flow import PermissionFlow
from .processing import Processing
from .recording import Recorder
from .recording_actions import RecordingActions

log = logging.getLogger("mt.app")


class App(OverlayHost, RecordingActions, Processing, PermissionFlow):
    """The app: its parts are the mixins above (what each concern does is in its own module); this class owns the shared state,
    the settings and the hotkeys, and quits."""

    def __init__(self):
        config.ensure_settings()
        self.cfg = config.defaults()
        self._reload_cfg()
        self.cfg["meetings_dir"].mkdir(parents=True, exist_ok=True)
        self._progress_throttle = mtlog.Throttle()
        log.info("Meetings folder: %s; permissions missing: %s", self.cfg["meetings_dir"], permissions.missing() or "none")
        self.rec = Recorder(self.cfg, on_warning=lambda m: notify("Meeting Transcriber", "Warning", m))
        self._toasts = DeviceToasts()
        self.rec.on_device_change = lambda track, device: on_main(self._device_toast, track, device)  # called from the recorder's watcher threads
        self.prompt = self.cfg["default_prompt"]
        self.started = 0.0
        self.pending = 0  # meetings waiting for / in processing
        self._pending_lock = threading.Lock()
        self._controls: dict[int, control.JobControl] = {}  # pause / cancel switches of the meetings being processed (by job id)
        self._jobargs: dict[int, tuple] = {}  # what each job was started with, so Resume can run it again
        self._work_lock = threading.Lock()  # process one meeting at a time (GPU), but never block recording
        try:
            self._hotkeys = Hotkeys()
        except OSError as e:
            self._hotkeys = None
            notify("Meeting Transcriber", "Global hotkeys are off", str(e)[:150])
        self.docs = documents.DocumentWorker(on_change=lambda: on_main(self._push_overlay))  # reads the documents you add, in the background
        self._selector = None  # the area chooser of a screenshot, while it is on screen
        self._notes_text = ""  # what is in the notes panel for the current (or last) recording
        self._notes_dir: Path | None = None  # ...and the meeting folder it is saved in (kept after stopping, for the last keystrokes)
        self._settings = None  # the Settings window, created on first use
        self._overlay = None  # the floating control bar, created once the app is running
        self._awaiting_restart = False  # you were sent to System Settings: macOS only applies Screen Recording after a restart
        # Asking macOS which permissions are missing takes ~20 ms, and the bar needs the answer every second and on every
        # hover: on the main thread that was the lag. A background thread keeps the answer fresh instead.
        self._missing = permissions.missing()
        threading.Thread(target=self._watch_permissions, daemon=True).start()
        self.jobs = Tracker((self.cfg["my_label"], self.cfg["others_label"]), on_change=lambda: on_main(self._push_overlay))

        self.rec.mic_muted = bool(self.cfg["start_muted_mic"])  # the "when the app opens" setting
        self.rec.sys_muted = bool(self.cfg["start_muted_system"])
        self._apply_settings()
        on_main(self._push_overlay)  # shows the bar as soon as the run loop starts
        every(1, self._tick)
        AppHelper.callLater(1, self._welcome)  # first-launch permission step, once the bar is up

    def _reload_cfg(self) -> dict:
        """Re-read config.toml. A typo in it must not crash the app: keep the last good settings."""
        try:
            self.cfg = config.load()
        except Exception as e:
            notify("Meeting Transcriber", "config.toml has an error - using previous settings", str(e)[:150])
        return self.cfg

    # ---- settings window
    def open_settings(self, _):
        if self._settings is None:
            from .settings_window import SettingsWindow

            self._settings = SettingsWindow(on_change=self._on_settings_changed)
        self._settings.show()

    def _on_settings_changed(self):
        self._reload_cfg()
        self._apply_settings()

    def _apply_settings(self):
        """Make the hotkeys follow the current settings."""
        self.prompt = self.cfg["default_prompt"]
        self.rec.cfg = self.cfg if not self.rec.recording else self.rec.cfg  # never change mid-recording
        if self._overlay is not None:
            self._overlay.set_capturable(bool(self.cfg["overlay_in_captures"]))
        self._start_hotkeys()

    def _choose_prompt(self, name):
        try:
            config.set_value("default_prompt", name)  # remembered for the next launch
        except Exception as e:
            notify("Meeting Transcriber", "Could not save setting", str(e)[:150])
            return
        self._on_settings_changed()

    def _status(self, text):
        # the progress shown on the bar comes from jobs.py; the log gets the steps, not every percent
        if self._progress_throttle(text):
            problem = re.search(r"silent|failed|not told apart|muted", text, re.I)
            log.log(logging.WARNING if problem else logging.INFO, text)

    def _tick(self):
        self._push_overlay()

    # ---- hotkeys
    def _start_hotkeys(self):
        """(Re)register the global hotkeys. Callbacks arrive on the main thread."""
        if self._hotkeys is None:
            return
        problems = self._hotkeys.set(
            {
                self.cfg["hotkey_screenshot"]: lambda: self.shot(None),
                self.cfg["hotkey_notes"]: lambda: self.toggle_notes(None),
                self.cfg["hotkey_documents"]: lambda: self.add_documents(None),
                self.cfg["hotkey_record"]: lambda: self.toggle(None),
                self.cfg["hotkey_mute_mic"]: lambda: self.toggle_mic(None),
                self.cfg["hotkey_mute_system"]: lambda: self.toggle_system(None),
            }
        )
        if problems:
            notify("Meeting Transcriber", "Hotkey problem", "; ".join(problems)[:200])

    def confirm_quit(self, _):
        """The quit button sits right on the bar, so ask before it throws away work in progress."""
        what = "A recording is in progress" if self.rec.recording else "A meeting is still being processed" if self.jobs.busy else None
        if what:
            bring_to_front()
            if not alert("Quit Meeting Transcriber?", f"{what}. " + ("Quitting stops it; the recording is kept and can be summarized later."
                         if self.rec.recording else "Quitting now stops it; you can summarize the meeting again later."), ok="Quit", cancel="Cancel"):
                return
        self.quit(None)

    def quit(self, _):
        if self.rec.recording:
            if self._overlay is not None:
                self._overlay.close_notes()  # the notes panel saves what is still unsaved...
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
    from .settings_window import install_edit_menu

    install_edit_menu()  # no menu bar: without a main menu Cmd+A / C / V / Z would not work in the notes panel or Settings
    stay_responsive()
    app = App()  # keep a reference: the timers and the bar call back into it
    AppHelper.runEventLoop()
    return app


if __name__ == "__main__":
    main()
