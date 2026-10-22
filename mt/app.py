"""The app: records, transcribes and summarizes meetings, controlled from the floating bar (overlay.py).

Run via the double-clickable MeetingTranscriber.app (or `python -m mt.app`).
"""

import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from PyObjCTools import AppHelper

from . import config, permissions
from .flash import flash_screen
from .hotkeys import Hotkeys
from .jobs import Tracker
from .pipeline import process, summarize_existing
from .recorder import Recorder
from .transcribe import transcript_file

def on_main(fn, *args):
    """AppKit objects must only be touched from the main thread (hotkeys/workers run elsewhere)."""
    AppHelper.callAfter(fn, *args)


def notify(title, subtitle, message=""):
    def show():
        from Foundation import NSUserNotification, NSUserNotificationCenter

        n = NSUserNotification.alloc().init()
        n.setTitle_(title)
        n.setSubtitle_(subtitle)
        n.setInformativeText_(message)
        center = NSUserNotificationCenter.defaultUserNotificationCenter()
        if center is None:  # not running from an app bundle: the log is all we have
            print(f"{title}: {subtitle} {message}".strip(), flush=True)
            return
        center.deliverNotification_(n)

    on_main(show)


def alert(title: str, message: str, ok: str = "OK", cancel: str | None = None) -> bool:
    """A modal dialog. True if the first button (ok) was pressed."""
    from AppKit import NSAlert

    a = NSAlert.alloc().init()
    a.setMessageText_(title)
    a.setInformativeText_(message)
    a.addButtonWithTitle_(ok)
    if cancel:
        a.addButtonWithTitle_(cancel)
    return a.runModal() == 1000  # NSAlertFirstButtonReturn


_timers = []  # NSTimers are kept alive here


def every(seconds: float, fn) -> None:
    """Call fn() every `seconds` on the main thread, also while a dialog (open panel, alert) is showing."""
    from AppKit import NSRunLoop, NSRunLoopCommonModes
    from Foundation import NSTimer

    t = NSTimer.timerWithTimeInterval_repeats_block_(seconds, True, lambda _t: fn())
    NSRunLoop.mainRunLoop().addTimer_forMode_(t, NSRunLoopCommonModes)
    _timers.append(t)


def bring_to_front():
    from AppKit import NSApp

    NSApp.activateIgnoringOtherApps_(True)  # an app without a Dock icon opens its dialogs behind other windows otherwise


def _hide_dock_icon():
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)


def fmt_hotkey(spec: str) -> str | None:
    """"<ctrl>+<alt>+r" -> "⌃⌥R" (the way macOS writes a shortcut)."""
    if not spec:
        return None
    parts = spec.split("+")
    keys = {"ctrl": "⌃", "alt": "⌥", "shift": "⇧", "cmd": "⌘"}
    out = "".join(keys.get(p.strip("<>"), "") for p in parts[:-1])
    last = parts[-1]
    return out + (last.strip("<>").upper() if last.startswith("<") else last.upper())


class App:
    def __init__(self):
        config.ensure_settings()
        self.cfg = config.defaults()
        self._reload_cfg()
        self.cfg["meetings_dir"].mkdir(parents=True, exist_ok=True)
        self.rec = Recorder(self.cfg, on_warning=lambda m: notify("Meeting Transcriber", "Warning", m))
        self.prompt = self.cfg["default_prompt"]
        self.started = 0.0
        self.pending = 0  # meetings waiting for / in processing
        self._pending_lock = threading.Lock()
        self._work_lock = threading.Lock()  # process one meeting at a time (GPU), but never block recording
        try:
            self._hotkeys = Hotkeys()
        except OSError as e:
            self._hotkeys = None
            notify("Meeting Transcriber", "Global hotkeys are off", str(e)[:150])
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
        print(text, flush=True)  # the progress shown on the bar comes from jobs.py; this goes to the log

    # ---- permissions
    def _welcome(self):
        if permissions.welcomed():
            return
        if not permissions.missing():
            permissions.mark_welcomed()
            return
        bring_to_front()
        if not alert(
            "Welcome to Meeting Transcriber",
            "To record your meetings it needs two permissions:\n\n"
            "•  Microphone: to record your voice\n"
            "•  Screen & System Audio Recording: to capture other participants' audio and take screenshots "
            "(no video is recorded)\n\n"
            "macOS will now ask you for each one.",
            ok="Continue", cancel="Later",
        ):
            return  # asked again next launch
        permissions.mark_welcomed()
        self._request_next()

    def _request_next(self):
        """One system prompt at a time: microphone first, then screen & system audio."""
        if permissions.mic_status() == "undetermined":
            permissions.request_mic(lambda ok: on_main(self._request_next))
            return
        if permissions.mic_status() == "denied":
            self._explain_denied("microphone")
        if not permissions.screen_granted():
            permissions.request_screen()
            self._awaiting_restart = True
            notify("Meeting Transcriber", "Allow Screen & System Audio Recording",
                   "Switch it on in System Settings, then click the restart button on the bar.")

    def _explain_denied(self, which):
        bring_to_front()
        if alert(
            f"{permissions.LABELS[which]} is turned off",
            "macOS only asks once. Turn it on for Meeting Transcriber in System Settings → Privacy & Security.",
            ok="Open System Settings", cancel="Not now",
        ):
            permissions.open_settings(which)

    def fix_permissions(self, _):
        """Ask (if macOS hasn't yet) or point to the switch (if it was turned off)."""
        todo = permissions.missing()
        if "microphone" in todo:
            if permissions.mic_status() == "undetermined":
                bring_to_front()
                permissions.request_mic(lambda ok: on_main(self.fix_permissions, None))
            else:
                self._explain_denied("microphone")
        elif "screen" in todo:
            if self._awaiting_restart:  # you already switched it on: macOS applies it after a restart
                self.restart()
                return
            permissions.request_screen()
            permissions.open_settings("screen")
            self._awaiting_restart = True

    def _tick(self):
        self._push_overlay()

    # ---- floating bar (the Overlay asks us for content and tells us what you clicked: see overlay.py)
    def _overlay_state(self) -> dict:
        jobs = self.jobs.snapshot()
        show = next((j for j in jobs if j["state"] == "active"), None) or next((j for j in jobs if j["state"] == "queued"), None) or (jobs[-1] if jobs else None)
        return {
            "recording": self.rec.recording,
            "elapsed": int(time.time() - self.started) if self.rec.recording else 0,
            "micMuted": bool(self.rec.mic_muted),
            "sysMuted": bool(self.rec.sys_muted),
            "busy": self.jobs.busy,
            "perm": bool(self._missing),
            "restart": self._restart_pending(),
            "job": {"stage": show["stage"], "progress": show["progress"]} if show else None,
        }

    def _push_overlay(self):
        """Create the bar on first use and keep it showing the app's current state."""
        try:
            if self._overlay is None:
                from .overlay import Overlay

                self._overlay = Overlay(self)
                self._overlay.show()
            self._overlay.update(self._overlay_state())
        except Exception as e:
            traceback.print_exc()
            if self._overlay is None:  # without the bar there is no way to use the app
                bring_to_front()
                alert("The control bar could not start", f"{str(e)[:300]}\n\nSee ~/Library/Logs/MeetingTranscriber.log.")
                self.quit(None)

    def overlay_action(self, op):
        {"toggle": self.toggle, "shot": self.shot, "mute_mic": self.toggle_mic, "mute_system": self.toggle_system, "perm": self.fix_permissions, "quit": self.confirm_quit}[op](None)
        self._push_overlay()

    def overlay_tip(self, tip_id):
        cfg, rec = self.cfg, self.rec
        busy = bool(self.jobs.snapshot()) and not rec.recording
        return {
            "rec": {"text": "Stop and summarize" if rec.recording else "Show processing status" if busy else "Start recording",
                    "hotkey": None if busy else fmt_hotkey(cfg["hotkey_record"])},
            "shot": {"text": "Take screenshot" if rec.recording else "Take screenshot (while recording)",
                     "hotkey": fmt_hotkey(cfg["hotkey_screenshot"]) if rec.recording else None},
            "mic": {"text": "Unmute microphone" if rec.mic_muted else "Mute microphone", "hotkey": fmt_hotkey(cfg["hotkey_mute_mic"])},
            "sys": {"text": "Unmute system audio" if rec.sys_muted else "Mute system audio", "hotkey": fmt_hotkey(cfg["hotkey_mute_system"])},
            "perm": {"text": "Restart to apply the permission", "hotkey": None} if self._restart_pending()
                    else {"text": "Permissions needed", "items": [permissions.LABELS[m] for m in self._missing], "hotkey": None},
            "more": {"text": "More", "hotkey": None},
            "hide": {"text": "Hide bar", "hotkey": None},
            "quit": {"text": "Quit", "hotkey": None},
            "tab": {"text": "Show bar", "hotkey": None},
        }.get(tip_id)

    def overlay_menu(self):
        styles = [{"id": f"style:{n}", "label": n, "checked": n == self.cfg["default_prompt"]} for n in config.list_prompts()]
        label = "Restart to apply the permission" if self._restart_pending() else "⚠ Permissions needed…"
        warn = [{"id": "perm", "label": label}, {"divider": True}] if self._missing else []
        return warn + [
            {"id": "style", "label": "Summary style", "sub": styles},
            {"id": "summarize", "label": "Summarize a meeting…"},
            {"divider": True},
            {"id": "settings", "label": "Settings…", "hint": "⌘,"},
            {"id": "folder", "label": "Open meetings folder"},
        ]

    def overlay_menu_click(self, item_id):
        if item_id.startswith("style:"):
            self._choose_prompt(item_id[len("style:"):])
        elif item_id == "perm":
            self.fix_permissions(None)
        elif item_id == "summarize":
            self.summarize_chosen(None)
        elif item_id == "settings":
            self.open_settings(None)
        elif item_id == "folder":
            subprocess.run(["open", str(self.cfg["meetings_dir"])])

    def overlay_status(self):
        return {"jobs": [j for j in self.jobs.snapshot()], "canRecord": not self.rec.recording}

    def overlay_status_action(self, act, job_id):
        job = next((j for j in self.jobs.snapshot() if j["id"] == job_id), None)
        if act == "summary" and job and job["out"]:
            subprocess.run(["open", job["out"]])
            self.jobs.acknowledge(job_id)
        elif act == "folder" and job:
            subprocess.run(["open", job.get("folder") or str(self.cfg["meetings_dir"])])
            self.jobs.acknowledge(job_id)
        elif act == "record" and not self.rec.recording:
            self.toggle(None)
        elif act == "close":
            self.jobs.acknowledge()  # finished results are dismissed; running ones carry on

    def _open_status(self):
        if self._overlay is not None and not self._overlay.hidden:
            self._overlay.open_status()

    # ---- hotkeys
    def _start_hotkeys(self):
        """(Re)register the global hotkeys. Callbacks arrive on the main thread."""
        if self._hotkeys is None:
            return
        problems = self._hotkeys.set(
            {
                self.cfg["hotkey_screenshot"]: lambda: self.shot(None),
                self.cfg["hotkey_record"]: lambda: self.toggle(None),
                self.cfg["hotkey_mute_mic"]: lambda: self.toggle_mic(None),
                self.cfg["hotkey_mute_system"]: lambda: self.toggle_system(None),
            }
        )
        if problems:
            notify("Meeting Transcriber", "Hotkey problem", "; ".join(problems)[:200])

    # ---- actions
    def toggle(self, _):
        try:
            if not self.rec.recording:
                if permissions.mic_status() in ("denied", "undetermined"):
                    notify("Meeting Transcriber", "Microphone permission needed", "Recording was not started.")
                    self.fix_permissions(None)
                    return
                if not permissions.screen_granted():
                    notify("Meeting Transcriber", "Other participants won't be recorded",
                           "Allow Screen & System Audio Recording in System Settings, then reopen the app.")
                self.rec.cfg = self._reload_cfg()  # pick up edits to config.toml
                self.rec.start()
                self.started = time.time()
                self._status("Recording…")
            else:
                d = self.rec.stop()
                self._run_pipeline(d)
        except Exception as e:
            notify("Meeting Transcriber", "Error", str(e))
            traceback.print_exc()
        self._push_overlay()

    def _toggle_mute(self, track, label):
        muted = not (self.rec.mic_muted if track == "mic" else self.rec.sys_muted)
        if not self.rec.set_muted(track, muted):
            notify("Meeting Transcriber", f"Can't mute {label}", "The system audio helper isn't responding.")
            return
        if self.rec.recording:
            self._status(f"{label.capitalize()} muted - not being recorded" if muted else "Recording…")
        on_main(self._push_overlay)

    def toggle_mic(self, _):
        self._toggle_mute("mic", "microphone")

    def toggle_system(self, _):
        self._toggle_mute("system", "system audio")

    def shot(self, _):
        if not self.rec.recording:
            return
        try:
            self.rec.screenshot()
            on_main(flash_screen)
            self._status(f"Screenshots: {self.rec.shot_count}")
        except Exception as e:
            notify("Meeting Transcriber", "Screenshot failed", str(e))

    def summarize_chosen(self, _):
        """Pick a meeting folder to summarize or re-summarize (in the current Summary style)."""
        from AppKit import NSOpenPanel
        from Foundation import NSURL

        bring_to_front()
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setMessage_("Choose a meeting folder to summarize")
        panel.setPrompt_("Summarize")
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(self.cfg["meetings_dir"])))
        if panel.runModal() != 1:
            return
        d = Path(str(panel.URL().path()))
        if not ((d / "meta.json").exists() or transcript_file(d).exists()):
            notify("Meeting Transcriber", "Not a meeting folder", "It needs a transcript or a recording (meta.json).")
            return
        self._run_pipeline(d, summarize_only=True)

    def _run_pipeline(self, d, summarize_only=False):
        with self._pending_lock:
            self.pending += 1
        prompt = self.prompt
        cfg0 = self._reload_cfg()
        self.jobs.labels = (cfg0["my_label"], cfg0["others_label"])
        job = self.jobs.add(
            d,
            summarize=bool(cfg0["auto_summarize"] or summarize_only),
            transcribe=not (summarize_only and transcript_file(d).exists()),
            recorded=not summarize_only,
        )
        on_main(self._open_status)

        def say(text):
            self.jobs.feed(job, text)
            self._status(text)

        def work():
            with self._work_lock:  # one meeting at a time; recording is never blocked
                try:
                    cfg = self._reload_cfg()
                    self.jobs.begin(job)
                    if summarize_only:
                        new_dir, out = summarize_existing(d, cfg, prompt, progress=say)
                    else:
                        new_dir, out = process(d, cfg, prompt, progress=say)
                    self.jobs.finish(job, new_dir, out)
                    on_main(self._open_status)
                    self._status(f"Done: {new_dir.name}")
                    notify("Meeting Transcriber", "Done", out.name)
                    subprocess.run(["open", "-R", str(out)])  # reveal the result in Finder
                except Exception as e:
                    traceback.print_exc()
                    self.jobs.fail(job, str(e))
                    on_main(self._open_status)
                    self._status("Failed - see notification")
                    notify("Meeting Transcriber", "Processing failed", str(e)[:200])
                finally:
                    with self._pending_lock:
                        self.pending -= 1

        threading.Thread(target=work, daemon=True).start()

    def _watch_permissions(self):
        """Refresh the cached list of missing permissions every couple of seconds (off the main thread)."""
        while True:
            time.sleep(2)
            try:
                self._missing = permissions.missing()
            except Exception:
                traceback.print_exc()

    def _restart_pending(self) -> bool:
        """You switched on Screen Recording in System Settings, so the next step is a restart."""
        missing = self._missing
        return self._awaiting_restart and "screen" in missing and "microphone" not in missing

    def restart(self):
        """Quit and start again (macOS only lets a running app use Screen Recording after a restart)."""
        if self.rec.recording:
            notify("Meeting Transcriber", "Stop the recording first", "Then click the restart button again.")
            return
        helper = os.environ.get("MT_HELPER", "")  # <App>.app/Contents/Resources/sck-audio when run as the app
        bundle = Path(helper).parents[2] if helper.endswith(".app/Contents/Resources/sck-audio") else None
        again = ["/usr/bin/open", str(bundle)] if bundle else [sys.executable, "-m", "mt.app"]
        subprocess.Popen(["/bin/sh", "-c", 'sleep 1; exec "$@"', "sh", *again], start_new_session=True,
                         cwd=str(Path(__file__).resolve().parent.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.quit(None)

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
            self.rec.stop()
        from AppKit import NSApp

        NSApp.terminate_(None)


_activity = None  # keeps the "do not nap" assertion alive for the life of the app


def _stay_responsive():
    """An app that is never frontmost (this one has no Dock icon and its panels never take focus) is a prime
    candidate for App Nap: after a while macOS coalesces its timers and delays its events, which showed up as hover
    that got slower and slower. Ask not to be napped."""
    global _activity
    from Foundation import NSProcessInfo

    user_initiated, latency_critical = 0x00EFFFFF, 0xFF00000000  # NSActivityUserInitiatedAllowingIdleSystemSleep / NSActivityLatencyCritical
    _activity = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
        user_initiated | latency_critical, "The floating control bar has to react instantly"
    )


def main():
    _hide_dock_icon()
    _stay_responsive()
    app = App()  # keep a reference: the timers and the bar call back into it
    AppHelper.runEventLoop()
    return app


if __name__ == "__main__":
    main()
