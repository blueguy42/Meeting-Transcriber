"""The app: records, transcribes and summarizes meetings, controlled from the floating bar (overlay.py).

Run via the double-clickable MeetingTranscriber.app (or `python -m mt.app`).
"""

import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import log as mtlog  # (sets the environment that quiets the libraries below)
from PyObjCTools import AppHelper

from . import config, control, documents, notes, permissions, progress, screenshot
from .flash import flash_screen
from .hotkeys import Hotkeys
from .jobs import Tracker
from .audio import has_audio
from .pipeline import process, summarize_existing, transcribe_existing
from .devicetoast import DeviceToasts, TOAST_S
from .recorder import Recorder
from .summarize import notes_lines
from . import transcribe
from .transcribe import transcript_file

log = logging.getLogger("mt.app")

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
        icon = app_icon()
        if icon is not None:
            try:
                n.setValue_forKey_(icon, "_identityImage")  # the logo instead of Python's rocket (a private key; ignored if it goes away)
            except Exception:
                pass
        center = NSUserNotificationCenter.defaultUserNotificationCenter()
        log.info("Notification: %s", f"{title}: {subtitle} {message}".strip())
        if center is None:  # not running from an app bundle: the log is all we have
            return
        center.deliverNotification_(n)

    on_main(show)


def alert(title: str, message: str, ok: str = "OK", cancel: str | None = None, destructive_second: bool = False):
    """A modal dialog. True if the first button (ok) was pressed. With destructive_second the answer is "first" or "second"
    instead, and the second button is drawn as the dangerous one (the first stays the default)."""
    from AppKit import NSAlert

    a = NSAlert.alloc().init()
    a.setMessageText_(title)
    a.setInformativeText_(message)
    icon = app_icon()
    if icon is not None:
        a.setIcon_(icon)
    a.addButtonWithTitle_(ok)
    if cancel:
        a.addButtonWithTitle_(cancel)
    if destructive_second:
        try:
            a.buttons()[1].setHasDestructiveAction_(True)
        except Exception:
            pass
        return "first" if a.runModal() == 1000 else "second"
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


def app_icon():
    """The app's logo (assets/AppIcon.icns). The app runs inside Python, so without this its dialogs show Python's rocket."""
    from pathlib import Path

    from AppKit import NSImage

    path = Path(__file__).resolve().parent.parent / "assets" / "AppIcon.icns"
    return NSImage.alloc().initWithContentsOfFile_(str(path)) if path.exists() else None


def _hide_dock_icon():
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    icon = app_icon()
    if icon is not None:
        app.setApplicationIconImage_(icon)  # alerts, open panels and the app switcher use this


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
    def _device_toast(self, track: str, device: str) -> None:
        """The microphone or the sound output moved to another device while recording: say so beside the bar."""
        overlay = getattr(self, "_overlay", None)
        if overlay is not None and self.rec.recording:
            overlay.show_toast(self._toasts.message(track, device), TOAST_S)

    def _overlay_state(self) -> dict:
        jobs = self.jobs.snapshot()
        # The ring follows the most relevant meeting: one running, else one waiting, else a paused one, else the latest tab (done,
        # failed or cancelled): whatever tabs are in the panel, the ring says so and opens it.
        show = next((j for j in jobs if j["state"] == "active"), None) or next((j for j in jobs if j["state"] == "queued"), None) or next((j for j in jobs if j["state"] == "paused"), None) or (jobs[-1] if jobs else None)
        return {
            "recording": self.rec.recording,
            "elapsed": int(self.rec.elapsed()),  # recording time: a pause is not counted
            "paused": bool(self.rec.paused),
            "notesLines": notes.line_count(self._notes_text) if self.rec.recording else 0,
            "docsCount": documents.count(self.rec.dir) if self.rec.recording else 0,
            "docsReady": sum(1 for d in documents.load(self.rec.dir) if d.get("status") == "ready") if self.rec.recording else 0,
            "micMuted": bool(self.rec.mic_muted),
            "sysMuted": bool(self.rec.sys_muted),
            "busy": len(jobs),  # the badge counts the tabs in the processing panel (so a paused or finished one can still be reopened)
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
            log.exception("Control bar failed to start")
            if self._overlay is None:  # without the bar there is no way to use the app
                bring_to_front()
                alert("The control bar could not start", f"{str(e)[:300]}\n\nSee ~/Library/Logs/MeetingTranscriber.log.")
                self.quit(None)

    def overlay_action(self, op):
        {"toggle": self.toggle, "shot": self.shot, "notes": self.toggle_notes, "docs_add": self.add_documents, "mute_mic": self.toggle_mic, "mute_system": self.toggle_system, "perm": self.fix_permissions, "quit": self.confirm_quit,
         "pause": self.toggle_pause, "cancel_rec": self.cancel_recording}[op](None)
        self._push_overlay()

    def overlay_tip(self, tip_id):
        cfg, rec = self.cfg, self.rec
        busy = bool(self.jobs.snapshot()) and not rec.recording
        return {
            "rec": {"text": "Stop and summarize" if rec.recording else "Show processing status" if busy else "Start recording",
                    "hotkey": None if busy else fmt_hotkey(cfg["hotkey_record"])},
            "shot": {"text": "Take screenshot" if rec.recording else "Take screenshot (while recording)",
                     "hotkey": fmt_hotkey(cfg["hotkey_screenshot"]) if rec.recording else None},
            "notes": {"text": "Take notes (while recording)" if not rec.recording
                      else f"Notes · {n} line{'s' if n != 1 else ''}" if (n := notes.line_count(self._notes_text)) else "Take notes",
                      "hotkey": fmt_hotkey(cfg["hotkey_notes"]) if rec.recording else None},
            "docadd": {"text": "Add documents" if rec.recording else "Add documents (while recording)",
                       "hotkey": fmt_hotkey(cfg["hotkey_documents"]) if rec.recording else None},
            "doclist": {"text": self._doclist_tip(), "hotkey": None},
            "mic": {"text": "Unmute microphone" if rec.mic_muted else "Mute microphone", "hotkey": fmt_hotkey(cfg["hotkey_mute_mic"])},
            "sys": {"text": "Unmute system audio" if rec.sys_muted else "Mute system audio", "hotkey": fmt_hotkey(cfg["hotkey_mute_system"])},
            "perm": {"text": "Restart to apply the permission", "hotkey": None} if self._restart_pending()
                    else {"text": "Permissions needed", "items": [permissions.LABELS[m] for m in self._missing], "hotkey": None},
            "more": {"text": "More", "hotkey": None},
            "hide": {"text": "Hide bar", "hotkey": None},
            "quit": {"text": "Quit", "hotkey": None},
            "tab": {"text": "Show bar", "hotkey": None},
        }.get(tip_id)

    def _doclist_tip(self) -> str:
        if not self.rec.recording:
            return "Documents (while recording)"
        docs = documents.load(self.rec.dir)
        ready = sum(1 for d in docs if d.get("status") == "ready")
        return "Documents" if not docs else f"Documents · {len(docs)}" if ready == len(docs) else f"Documents · {ready} of {len(docs)} ready"

    def overlay_docs(self) -> dict:
        """The list in the documents popup: what was added to this recording, and how far reading each one is."""
        out = []
        for d in sorted(documents.load(self.rec.dir) if self.rec.recording else [], key=lambda d: d["added_at"]):
            s = d.get("status")
            status = ({"k": "wait", "text": "Waiting"} if s == "queued" else
                      {"k": "run", "text": f"Reading… {round(d.get('progress', 0) * 100)}%" if d.get("progress", 0) > 0 else "Reading…"} if s == "reading" else
                      {"k": "ok", "text": "Ready"} if s == "ready" else
                      {"k": "warn", "text": "No text found"} if s == "nolext" else {"k": "err", "text": "Couldn't read"})
            what = d.get("note") if s in ("failed", "nolext") and d.get("note") else d.get("detail") or documents.label(d)
            out.append({"id": d["id"], "name": d["stored"], "kind": d["kind"], "meta": f"{what} · added {notes.stamp(d['added_at'])[1:-1]}",
                        "status": status, "pct": d.get("progress", 0)})
        return {"docs": out}

    def overlay_docs_action(self, act: str, doc_id: str) -> None:
        if act == "delete" and self.rec.dir is not None:
            documents.delete(self.rec.dir, doc_id)
            self._push_overlay()

    def overlay_notes_info(self) -> dict:
        return {"timeKey": self.cfg["hotkey_notes_time"], "timeLabel": fmt_hotkey(self.cfg["hotkey_notes_time"])}

    def overlay_notes_save(self, text: str) -> str | None:
        """The notes panel sends everything typed so far; it goes to notes.txt in the meeting's folder."""
        self._notes_text = text[:notes.MAX_CHARS]
        d = self._notes_dir
        if d is None or not d.exists():
            return None
        try:
            notes.save_notes(d, self._notes_text)
        except OSError as e:
            return f"Couldn't save: {str(e)[:60]}"
        return None

    def overlay_menu(self):
        styles = [{"id": f"style:{n}", "label": n, "checked": n == self.cfg["default_prompt"]} for n in config.list_prompts()]
        label = "Restart to apply the permission" if self._restart_pending() else "⚠ Permissions needed…"
        warn = [{"id": "perm", "label": label}, {"divider": True}] if self._missing else []
        return warn + [
            {"id": "style", "label": "Summary style", "sub": styles},
            {"id": "transcribe", "label": "Transcribe a meeting…"},
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
        elif item_id == "transcribe":
            self.transcribe_chosen(None)
        elif item_id == "settings":
            self.open_settings(None)
        elif item_id == "folder":
            subprocess.run(["open", str(self.cfg["meetings_dir"])])

    def overlay_status(self):
        return {"jobs": [j for j in self.jobs.snapshot()], "canRecord": not self.rec.recording}

    def overlay_status_action(self, act, job_id):
        job = next((j for j in self.jobs.snapshot() if j["id"] == job_id), None)
        if act == "summary" and job and job["out"]:
            subprocess.run(["open", job["out"]])  # (the tab stays: only Dismiss, or ten minutes after finishing, removes it)
        elif act == "folder" and job:
            subprocess.run(["open", job.get("folder") or str(self.cfg["meetings_dir"])])
        elif act == "pause" and job and job["state"] == "active":
            ctrl = self._controls.get(job_id)
            if ctrl is not None and ctrl.stop("pause"):
                self.jobs.stopping(job_id, "pause")
        elif act == "resume" and job:
            self._resume_job(job_id)
        elif act == "cancel" and job:
            self._cancel_job(job_id, job)
        elif act == "again" and job and job["state"] in ("cancelled", "failed"):  # (a finished transcript is kept)
            self._process_again(job)
        elif act == "dismiss" and job:
            self.jobs.dismiss(job_id)
        elif act == "record" and not self.rec.recording:
            self.toggle(None)
        elif act == "close":
            pass  # × only hides the panel; every tab is still there when it is opened again

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
                self.cfg["hotkey_notes"]: lambda: self.toggle_notes(None),
                self.cfg["hotkey_documents"]: lambda: self.add_documents(None),
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
                log.info("Recording started: %s", self.rec.dir)
                self._notes_text, self._notes_dir = "", self.rec.dir  # a fresh notes panel for this meeting
                if self._overlay is not None:
                    self._overlay.reset_notes()
                self.started = time.time()
                self._status("Recording…")
            else:
                log.info("Recording stopped after %ds: %s", time.time() - self.started, self.rec.dir)
                if self._selector is not None:
                    self._selector.cancel()  # the recording is over: no more screenshots
                if self._overlay is not None:
                    self._overlay.close_notes()  # saves the last keystrokes (notes.txt is read when the summary is written)
                    self._overlay.close_docs()
                d = self.rec.stop()
                self._run_pipeline(d)
        except Exception as e:
            notify("Meeting Transcriber", "Error", str(e))
            log.exception("Record toggle failed")
        self._push_overlay()

    def toggle_pause(self, _):
        """Pause the recording (nothing is recorded, and the time does not count) or resume it."""
        if not self.rec.recording:
            return
        if self.rec.paused:
            if self.rec.resume():
                log.info("Recording resumed")
                self._status("Recording…")
        else:
            if self._selector is not None:
                self._selector.cancel()
            if self.rec.pause():
                log.info("Recording paused")
                self._status("Paused")
            else:
                notify("Meeting Transcriber", "Can't pause", "The system audio helper isn't responding.")
        self._push_overlay()

    def cancel_recording(self, _):
        """Ask, then throw the recording away: the meeting folder is deleted and nothing is summarized."""
        if not self.rec.recording:
            return
        bring_to_front()
        if not alert("Cancel this recording?", "The recording so far, with its notes, screenshots and documents, will be deleted from your Mac. "
                     "This cannot be undone.", ok="Keep recording", cancel="Delete recording", destructive_second=True) == "second":
            return
        if not self.rec.recording:  # it ended while the question was open
            return
        meeting = self.rec.dir
        log.info("Recording cancelled and deleted: %s", meeting)
        if self._selector is not None:
            self._selector.cancel()
        if self._overlay is not None:
            self._overlay.close_popups()  # (the notes panel is closed without saving into the folder that is about to go)
            self._overlay.reset_notes()
        documents.cancel_all(meeting)  # readers still at work stop instead of recreating files
        self.rec.cancel()
        self._notes_text, self._notes_dir = "", None
        self._status("Recording cancelled")
        self._push_overlay()

    def _toggle_mute(self, track, label):
        if self.rec.paused:  # greyed while paused
            return
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
        """Freeze the screen under the mouse, let you choose an area, and save only that area (see screenshot.py)."""
        if not self.rec.recording or self.rec.paused or self._selector is not None:  # no screenshots while paused
            return
        try:
            self.rec.require_screen_access()
            screen = screenshot.screen_under_mouse()
            frozen = screenshot.freeze(screen)
            if frozen is None:
                raise RuntimeError("could not capture the screen")
        except Exception as e:
            notify("Meeting Transcriber", "Screenshot failed", str(e))
            return
        secs = self.rec.elapsed()  # the moment the screen froze, in recording time: what the transcript marker says
        if self._overlay is not None:
            self._overlay.close_popups()

        def done(box):
            self._selector = None
            if box is None or not self.rec.recording:  # cancelled, or the recording ended meanwhile
                return
            try:
                import Quartz

                from .recorder import cg_to_pil

                x, y, w, h = box
                crop = Quartz.CGImageCreateWithImageInRect(frozen, ((x, y), (w, h)))
                self.rec.save_shot(cg_to_pil(crop), secs)
                on_main(flash_screen)
                self._status(f"Screenshots: {self.rec.shot_count}")
            except Exception as e:
                notify("Meeting Transcriber", "Screenshot failed", str(e))
            self._push_overlay()

        self._selector = screenshot.AreaSelector(screen, frozen, done)
        self._selector.show()

    def toggle_notes(self, _):
        if not self.rec.recording or self._overlay is None:
            return
        self._overlay.toggle_notes()
        self._push_overlay()

    def add_documents(self, _):
        """Pick files to give the summary more context. They are copied into the meeting folder and read in the background."""
        if not self.rec.recording or self.rec.dir is None:
            return
        meeting = self.rec.dir
        paths, ocr_on = self._choose_documents(bool(self.cfg["documents_ocr"]))
        if not paths:
            return
        if ocr_on != bool(self.cfg["documents_ocr"]):
            try:
                config.set_value("documents_ocr", ocr_on)  # the box starts the way you left it
                self._reload_cfg()
            except Exception:
                log.exception("Could not save the OCR setting")
        new, skipped = documents.add(meeting, [Path(p) for p in paths], int(self.rec.elapsed()), ocr_on)
        if skipped:
            notify("Meeting Transcriber", "Some documents were not added", "; ".join(skipped)[:220])
        if new:
            self.docs.submit(meeting, new)
        self._push_overlay()

    def _choose_documents(self, ocr_default: bool) -> tuple[list[str], bool]:
        """The file chooser (several files at once) with a "Read text in images (OCR)" box under the list."""
        from AppKit import NSButton, NSColor, NSFont, NSOpenPanel, NSTextField, NSView
        from Foundation import NSMakeRect

        if self._overlay is not None:
            self._overlay.close_popups()  # the bar's popups float above the chooser and would block parts of it
        bring_to_front()
        panel = NSOpenPanel.openPanel()
        panel.setLevel_(100)  # above the bar and its popups (they float at level 25 and 26)
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(False)
        panel.setAllowsMultipleSelection_(True)
        panel.setPrompt_("Add")
        panel.setAllowedFileTypes_(sorted(e.lstrip(".") for e in documents.SUPPORTED))
        box = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 640, 64))
        check = NSButton.checkboxWithTitle_target_action_("Read text in images (OCR)", None, None)
        check.setState_(1 if ocr_default else 0)
        check.setFrame_(NSMakeRect(20, 40, 600, 20))
        hint = NSTextField.wrappingLabelWithString_("Uses Apple's text recognition on your Mac. Good for scans and for pictures inside documents; slower for big PDFs.")
        hint.setFont_(NSFont.systemFontOfSize_(11))
        hint.setTextColor_(NSColor.secondaryLabelColor())
        hint.setFrame_(NSMakeRect(38, 6, 580, 32))
        box.addSubview_(check)
        box.addSubview_(hint)
        panel.setAccessoryView_(box)
        try:
            panel.setAccessoryViewDisclosed_(True)
        except Exception:
            pass
        if panel.runModal() != 1:
            return [], ocr_default
        return [str(u.path()) for u in panel.URLs()], check.state() == 1

    def _choose_meeting(self, message: str, button: str) -> Path | None:
        """Ask for a meeting folder (a folder picker starting in the meetings folder)."""
        from AppKit import NSOpenPanel
        from Foundation import NSURL

        bring_to_front()
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setMessage_(message)
        panel.setPrompt_(button)
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(self.cfg["meetings_dir"])))
        if panel.runModal() != 1:
            return None
        return Path(str(panel.URL().path()))

    def summarize_chosen(self, _):
        """Pick a meeting folder to summarize or re-summarize (in the current Summary style)."""
        d = self._choose_meeting("Choose a meeting folder to summarize", "Summarize")
        if d is None:
            return
        if not ((d / "meta.json").exists() or transcript_file(d).exists()):
            notify("Meeting Transcriber", "Not a meeting folder", "It needs a transcript or a recording (meta.json).")
            return
        self._run_pipeline(d, summarize_only=True)

    def transcribe_chosen(self, _):
        """Pick a meeting folder and transcribe its recording (again), then summarize it, if that is on."""
        d = self._choose_meeting("Choose a meeting folder to transcribe", "Transcribe")
        if d is None:
            return
        if not (d / "meta.json").exists() or not has_audio(d):
            notify("Meeting Transcriber", "Nothing to transcribe", "That folder has no recording (meta.json and the audio files).")
            return
        self._run_pipeline(d, transcribe_only=True)

    def _run_pipeline(self, d, summarize_only=False, transcribe_only=False):
        with self._pending_lock:
            self.pending += 1
        prompt = self.prompt
        cfg0 = self._reload_cfg()
        self.jobs.labels = (cfg0["my_label"], cfg0["others_label"])
        reading = not (summarize_only or transcribe_only) and self.docs.pending(d) > 0  # a recording that has just stopped
        n_docs = len([x for x in documents.load(d) if x.get("status") in ("ready", "queued", "reading")]) if cfg0["use_documents"] else 0
        job = self.jobs.add(
            d,
            summarize=bool(cfg0["auto_summarize"] or summarize_only),  # (transcribing a meeting follows the auto-summarize setting)
            transcribe=not (summarize_only and transcript_file(d).exists()),
            recorded=not (summarize_only or transcribe_only),  # an existing meeting: no "Recorded" step
            diarize=bool(cfg0["diarize"]),  # "Telling speakers apart" is a part of Transcribing
            notes=notes_lines(d, cfg0),
            documents=n_docs,
            reading=reading,  # the summary also reads the notes you took (shown under "Summarizing")
            mode="summarize" if summarize_only else "transcribe" if transcribe_only else "full",
        )
        on_main(self._open_status)
        log.info("Job queued: %s (summarize=%s, transcribe_only=%s, summarize_only=%s)", d.name, bool(cfg0["auto_summarize"] or summarize_only), transcribe_only, summarize_only)

        self._start_job(job, d, summarize_only, transcribe_only, prompt, reading)

    def _start_job(self, job, d, summarize_only, transcribe_only, prompt, reading):
        """Run (or, after a pause, run again) one meeting's processing on its own thread, one meeting at a time."""
        ctrl = control.JobControl()
        self._controls[job] = ctrl
        self._jobargs[job] = (d, summarize_only, transcribe_only, prompt)

        def say(text):
            if text == progress.DOCX:
                ctrl.locked = True  # saving the files: too late to pause or cancel, it finishes
            self.jobs.feed(job, text)
            self._status(text)
            if not text.startswith("Audio compression"):  # (quick, and the transcript is already saved: a pause waits for the next step)
                ctrl.check()  # a pause or cancel stops the step here (see control.py)

        def work():
            with self._work_lock:  # one meeting at a time; recording is never blocked
                control.bind(ctrl)
                try:
                    if ctrl.reason:  # cancelled while waiting its turn (the row already says so)
                        return
                    cfg = self._reload_cfg()
                    self.jobs.begin(job)
                    t0 = time.time()
                    log.info("Job started: %s", d.name)
                    if reading:  # the summary needs the documents: wait for the ones still being read
                        self.docs.wait(d, lambda done, total: say(progress.reading_documents(done, total)))
                    if transcribe_only:
                        new_dir, out = transcribe_existing(d, cfg, prompt, progress=say)
                    elif summarize_only:
                        new_dir, out = summarize_existing(d, cfg, prompt, progress=say)
                    else:
                        new_dir, out = process(d, cfg, prompt, progress=say)
                    self.jobs.finish(job, new_dir, out)
                    on_main(self._open_status)
                    log.info("Job done in %ds: %s", time.time() - t0, out)
                    notify("Meeting Transcriber", "Done", out.name)
                    subprocess.run(["open", "-R", str(out)])  # reveal the result in Finder
                except control.Stopped as stop:
                    if stop.reason == "cancel":
                        self.jobs.cancel(job)
                        transcribe.clear_checkpoint(d)
                    else:
                        self.jobs.pause(job)
                    log.info("Job %s: %s", "cancelled" if stop.reason == "cancel" else "paused", d.name)
                    on_main(self._open_status)
                except Exception as e:
                    log.exception("Processing failed for %s", d.name)
                    self.jobs.fail(job, str(e))
                    on_main(self._open_status)
                    log.error("Job failed: %s: %s", d.name, e)
                    notify("Meeting Transcriber", "Processing failed", str(e)[:200])
                finally:
                    control.bind(None)
                    with self._pending_lock:
                        self.pending -= 1

        threading.Thread(target=work, daemon=True).start()

    def _resume_job(self, job_id: int) -> None:
        """Run a paused meeting again from the step it was paused at (transcribing carries on from its checkpoint)."""
        args = self._jobargs.get(job_id)
        info = self.jobs.job(job_id)
        if args is None or info is None or info["state"] != "paused":
            return
        d, summarize_only, transcribe_only, prompt = args
        at = info["paused_at"]
        if at == "summarize":  # the transcript is done: only the summary is asked for again
            summarize_only, transcribe_only = True, False
        self.jobs.resume(job_id)
        with self._pending_lock:
            self.pending += 1
        log.info("Job resumed at %s: %s", at, d.name)
        self._start_job(job_id, d, summarize_only, transcribe_only, prompt, reading=(at == "readdocs"))

    def _cancel_job(self, job_id: int, job: dict) -> None:
        """Stop a meeting's processing for good (the recording stays). Asks first when work would be thrown away."""
        state = job["state"]
        if state in ("active", "paused"):
            bring_to_front()
            if alert("Stop processing this meeting?", "The recording is kept and you can process it again later (Transcribe a meeting… or "
                     "Summarize a meeting…). Progress on the current step is lost.", ok="Keep going", cancel="Stop processing", destructive_second=True) != "second":
                return
        ctrl = self._controls.get(job_id)
        if state == "paused" or state == "queued":
            if ctrl is not None:
                ctrl.stop("cancel")  # (a waiting meeting leaves as soon as its turn comes)
            self.jobs.cancel(job_id)
            args = self._jobargs.get(job_id)
            if args is not None:
                transcribe.clear_checkpoint(args[0])
        elif state == "active" and ctrl is not None and ctrl.stop("cancel"):
            self.jobs.stopping(job_id, "cancel")

    def _process_again(self, job: dict) -> None:
        d, mode = Path(job["folder"]), job["mode"]
        self.jobs.dismiss(job["id"])
        if mode == "transcribe":
            self._run_pipeline(d, transcribe_only=True)
        elif mode == "summarize" or transcript_file(d).exists():  # a finished transcript is kept
            self._run_pipeline(d, summarize_only=True)
        else:
            self._run_pipeline(d)

    def _watch_permissions(self):
        """Refresh the cached list of missing permissions every couple of seconds (off the main thread)."""
        while True:
            time.sleep(2)
            try:
                self._missing = permissions.missing()
            except Exception:
                log.exception("Permission check failed")

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
            if self._overlay is not None:
                self._overlay.close_notes()  # the notes panel saves what is still unsaved...
            self.rec.stop()
            AppHelper.callLater(0.4, self._terminate)  # ...a moment later, when that has arrived
            return
        self._terminate()

    def _terminate(self):
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
    mtlog.setup()
    _hide_dock_icon()
    from .settings_window import install_edit_menu

    install_edit_menu()  # no menu bar: without a main menu Cmd+A / C / V / Z would not work in the notes panel or Settings
    _stay_responsive()
    app = App()  # keep a reference: the timers and the bar call back into it
    AppHelper.runEventLoop()
    return app


if __name__ == "__main__":
    main()
