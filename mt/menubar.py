"""Menu bar app. Run via the double-clickable MeetingTranscriber.app (or `python -m mt.menubar`)."""

import re
import subprocess
import threading
import time
import traceback
from pathlib import Path

import rumps

from . import config, permissions
from .config import PROJECT_DIR
from .flash import flash_screen
from .hotkeys import Hotkeys
from .pipeline import process, summarize_existing
from .recorder import Recorder
from .transcribe import transcript_file

MENUBAR_ICON = PROJECT_DIR / "assets" / "menubar.png"
MAX_STATUS_CHARS = 45  # the menu is as wide as its longest item

# NSEventModifierFlag values, to show a global hotkey as a native key hint (e.g. ⌃⌥R) in the menu
MODIFIER_FLAGS = {"ctrl": 1 << 18, "alt": 1 << 19, "shift": 1 << 17, "cmd": 1 << 20}


def on_main(fn, *args):
    """AppKit objects must only be touched from the main thread (hotkeys/workers run elsewhere)."""
    from PyObjCTools import AppHelper

    AppHelper.callAfter(fn, *args)


def notify(title, subtitle, message=""):
    on_main(lambda: rumps.notification(title, subtitle, message))


def bring_to_front():
    from AppKit import NSApp

    NSApp.activateIgnoringOtherApps_(True)  # a menu bar app's dialogs would otherwise open behind other windows


def _hide_dock_icon():
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)


def show_hotkey(item: rumps.MenuItem, spec: str) -> None:
    """Display "<ctrl>+<alt>+r" as the menu item's key hint. The real shortcut is the global hotkey;
    the hint is only a label (and also triggers the item while the menu is open)."""
    m = re.fullmatch(r"((?:<(?:ctrl|alt|shift|cmd)>\+)+)([a-z0-9])", spec or "")
    if not m:  # F-keys etc. have no menu equivalent worth showing
        item._menuitem.setKeyEquivalent_("")
        return
    mask = 0
    for mod in re.findall(r"<(\w+)>", m.group(1)):
        mask |= MODIFIER_FLAGS[mod]
    item._menuitem.setKeyEquivalent_(m.group(2))
    item._menuitem.setKeyEquivalentModifierMask_(mask)


class App(rumps.App):
    def __init__(self):
        # the app icon in the menu bar; the emoji is only a fallback if the image is missing
        self._idle_title = None if MENUBAR_ICON.exists() else "🎙"
        super().__init__(
            "MT", title=self._idle_title, icon=str(MENUBAR_ICON) if MENUBAR_ICON.exists() else None,
            template=False, quit_button=None,
        )
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

        self.m_toggle = rumps.MenuItem("Start recording", callback=self.toggle)
        self.m_shot = rumps.MenuItem("Take screenshot", callback=self.shot)
        self.m_status = rumps.MenuItem("Idle")
        self.m_perm = rumps.MenuItem("⚠ Permissions needed…", callback=self.fix_permissions)
        self.m_style = rumps.MenuItem("Summary style")
        self.style_items = {}
        self._refresh_styles()
        self.menu = [
            self.m_perm,
            self.m_toggle,
            self.m_shot,
            None,
            self.m_style,
            rumps.MenuItem("Summarize a meeting…", callback=self.summarize_chosen),
            None,
            rumps.MenuItem("Settings…", callback=self.open_settings, key=","),
            rumps.MenuItem("Open meetings folder", callback=lambda _: subprocess.run(["open", str(self.cfg["meetings_dir"])])),
            None,
            self.m_status,
            rumps.MenuItem("Quit", callback=self.quit),
        ]
        self.m_shot.set_callback(None)  # disabled until recording
        self.m_perm._menuitem.setHidden_(not permissions.missing())
        self._apply_settings()
        self._timer = rumps.Timer(self._tick, 1)
        self._timer.start()
        self._welcome_timer = rumps.Timer(self._welcome, 1)  # once the app is up: first-launch permission step
        self._welcome_timer.start()

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
        """Make the menu and hotkeys follow the current settings."""
        self.prompt = self.cfg["default_prompt"]
        self.rec.cfg = self.cfg if not self.rec.recording else self.rec.cfg  # never change mid-recording
        self._refresh_styles()
        show_hotkey(self.m_toggle, self.cfg["hotkey_record"])
        show_hotkey(self.m_shot, self.cfg["hotkey_screenshot"])
        self._start_hotkeys()

    def _refresh_styles(self):
        names = config.list_prompts()
        for name in [n for n in self.style_items if n not in names]:
            del self.m_style[name]
            del self.style_items[name]
        for name in names:
            if name not in self.style_items:
                self.style_items[name] = rumps.MenuItem(name, callback=self._pick_prompt)
                self.m_style.add(self.style_items[name])
        for name, item in self.style_items.items():
            item.state = int(name == self.cfg["default_prompt"])

    def _pick_prompt(self, item):
        try:
            config.set_value("default_prompt", item.title)  # remembered for the next launch
        except Exception as e:
            notify("Meeting Transcriber", "Could not save setting", str(e)[:150])
            return
        self._on_settings_changed()

    def _status(self, text):
        text = str(text)
        if len(text) > MAX_STATUS_CHARS:  # a long meeting title would stretch the whole menu
            text = text[: MAX_STATUS_CHARS - 1].rstrip() + "…"
        on_main(setattr, self.m_status, "title", text)

    # ---- permissions
    def _welcome(self, timer):
        timer.stop()
        if permissions.welcomed():
            return
        if not permissions.missing():
            permissions.mark_welcomed()
            return
        bring_to_front()
        if not rumps.alert(
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
            notify("Meeting Transcriber", "Allow Screen & System Audio Recording",
                   "Switch it on in System Settings, then reopen the app.")

    def _explain_denied(self, which):
        bring_to_front()
        if rumps.alert(
            f"{permissions.LABELS[which]} is turned off",
            "macOS only asks once. Turn it on for Meeting Transcriber in System Settings → Privacy & Security.",
            ok="Open System Settings", cancel="Not now",
        ):
            permissions.open_settings(which)

    def fix_permissions(self, _):
        """Menu action: ask (if macOS hasn't yet) or point to the switch (if it was turned off)."""
        todo = permissions.missing()
        if "microphone" in todo:
            if permissions.mic_status() == "undetermined":
                bring_to_front()
                permissions.request_mic(lambda ok: on_main(self.fix_permissions, None))
            else:
                self._explain_denied("microphone")
        elif "screen" in todo:
            permissions.request_screen()
            permissions.open_settings("screen")

    def _tick(self, _):
        self.m_perm._menuitem.setHidden_(not permissions.missing())
        if self.rec.recording:
            s = int(time.time() - self.started)
            self.title = f"🔴 {s // 60:02d}:{s % 60:02d}"
        elif self.pending:
            self.title = "⏳"
        else:
            self.title = self._idle_title

    # ---- hotkeys
    def _start_hotkeys(self):
        """(Re)register the global hotkeys. Callbacks arrive on the main thread."""
        if self._hotkeys is None:
            return
        problems = self._hotkeys.set(
            {
                self.cfg["hotkey_screenshot"]: lambda: self.shot(None),
                self.cfg["hotkey_record"]: lambda: self.toggle(None),
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
                self.m_toggle.title = "Stop & summarize"
                self.m_shot.set_callback(self.shot)
                self._status("Recording…")
            else:
                self.m_toggle.title = "Start recording"
                self.m_shot.set_callback(None)
                d = self.rec.stop()
                self._run_pipeline(d)
        except Exception as e:
            notify("Meeting Transcriber", "Error", str(e))
            traceback.print_exc()

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

        def work():
            with self._work_lock:  # one meeting at a time; recording is never blocked
                try:
                    cfg = self._reload_cfg()
                    if summarize_only:
                        new_dir, out = summarize_existing(d, cfg, prompt, progress=self._status)
                    else:
                        new_dir, out = process(d, cfg, prompt, progress=self._status)
                    self._status(f"Done: {new_dir.name}")
                    notify("Meeting Transcriber", "Done", out.name)
                    subprocess.run(["open", "-R", str(out)])  # reveal the result in Finder
                except Exception as e:
                    traceback.print_exc()
                    self._status("Failed - see notification")
                    notify("Meeting Transcriber", "Processing failed", str(e)[:200])
                finally:
                    with self._pending_lock:
                        self.pending -= 1

        threading.Thread(target=work, daemon=True).start()

    def quit(self, _):
        if self.rec.recording:
            self.rec.stop()
        rumps.quit_application()


if __name__ == "__main__":
    _hide_dock_icon()
    App().run()
