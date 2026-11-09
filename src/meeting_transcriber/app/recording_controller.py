"""Recording: start, stop, pause, cancel, mute, screenshots, notes, documents."""

import logging
import time
from pathlib import Path

from ..desktop import permissions
from ..desktop.macos import alert, bring_to_front, notify, on_main
from ..meetings import documents
from ..meetings.meeting import Meeting
from ..recording import screenshot
from ..recording.flash import flash_screen
from ..settings import config

log = logging.getLogger(__name__)


class RecordingController:
    """Everything you can do while recording: start, stop, pause, cancel, mute, screenshots, notes and documents."""

    def __init__(self, app):
        self.app = app
        self.rec = app.rec
        self.docs = app.docs
        self.started = 0.0
        self.selector = None  # the area chooser of a screenshot, while it is on screen
        self.notes_text = ""  # what is in the notes panel for the current (or last) recording
        self.notes_meeting: Meeting | None = None  # ...and the meeting it is saved in (kept after stopping, for the last keystrokes)

    def toggle(self):
        try:
            if not self.rec.recording:
                if permissions.mic_status() in ("denied", "undetermined"):
                    notify("Meeting Transcriber", "Microphone permission needed", "Recording was not started.")
                    self.app.permissions.fix()
                    return
                if not permissions.screen_granted():
                    notify("Meeting Transcriber", "Other participants won't be recorded",
                           "Allow Screen & System Audio Recording in System Settings, then reopen the app.")
                self.rec.cfg = self.app.reload_cfg()  # pick up edits to config.toml
                self.rec.start()
                log.info("Recording started: %s", self.rec.meeting)
                self.notes_text, self.notes_meeting = "", self.rec.meeting  # a fresh notes panel for this meeting
                if self.app.bar is not None:
                    self.app.bar.reset_notes()
                self.started = time.time()
                self.app.status("Recording…")
            else:
                log.info("Recording stopped after %ds: %s", time.time() - self.started, self.rec.meeting)
                if self.selector is not None:
                    self.selector.cancel()  # the recording is over: no more screenshots
                if self.app.bar is not None:
                    self.app.bar.close_notes()  # saves the last keystrokes (notes.txt is read when the summary is written)
                    self.app.bar.close_docs()
                self.app.processing.run_pipeline(self.rec.stop())
        except Exception as e:
            notify("Meeting Transcriber", "Error", str(e))
            log.exception("Record toggle failed")
        self.app.refresh_bar()

    def toggle_pause(self):
        """Pause the recording (nothing is recorded, and the time does not count) or resume it."""
        if not self.rec.recording:
            return
        if self.rec.paused:
            if self.rec.resume():
                log.info("Recording resumed")
                self.app.status("Recording…")
        else:
            if self.selector is not None:
                self.selector.cancel()
            if self.rec.pause():
                log.info("Recording paused")
                self.app.status("Paused")
            else:
                notify("Meeting Transcriber", "Can't pause", "The system audio helper isn't responding.")
        self.app.refresh_bar()

    def cancel_recording(self):
        """Ask, then throw the recording away: the meeting folder is deleted and nothing is summarized."""
        if not self.rec.recording:
            return
        bring_to_front()
        if not alert("Cancel this recording?", "The recording so far, with its notes, screenshots and documents, will be deleted from your Mac. "
                     "This cannot be undone.", ok="Keep recording", cancel="Delete recording", destructive_second=True) == "second":
            return
        if not self.rec.recording:  # it ended while the question was open
            return
        meeting = self.rec.meeting
        log.info("Recording cancelled and deleted: %s", meeting)
        if self.selector is not None:
            self.selector.cancel()
        if self.app.bar is not None:
            self.app.bar.close_popups()  # (the notes panel is closed without saving into the folder that is about to go)
            self.app.bar.reset_notes()
        documents.cancel_all(meeting)  # readers still at work stop instead of recreating files
        self.rec.cancel()
        self.notes_text, self.notes_meeting = "", None
        self.app.status("Recording cancelled")
        self.app.refresh_bar()

    def _toggle_mute(self, track, label):
        if self.rec.paused:  # greyed while paused
            return
        muted = not (self.rec.mic_muted if track == "mic" else self.rec.sys_muted)
        if not self.rec.set_muted(track, muted):
            notify("Meeting Transcriber", f"Can't mute {label}", "The system audio helper isn't responding.")
            return
        if self.rec.recording:
            self.app.status(f"{label.capitalize()} muted - not being recorded" if muted else "Recording…")
        on_main(self.app.refresh_bar)

    def toggle_mic(self):
        self._toggle_mute("mic", "microphone")

    def toggle_system(self):
        self._toggle_mute("system", "system audio")

    def shot(self):
        """Freeze the screen under the mouse, let you choose an area, and save only that area (see screenshot.py)."""
        if not self.rec.recording or self.rec.paused or self.selector is not None:  # no screenshots while paused
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
        if self.app.bar is not None:
            self.app.bar.close_popups()

        def done(box):
            self.selector = None
            if box is None or not self.rec.recording:  # cancelled, or the recording ended meanwhile
                return
            try:
                import Quartz

                from ..recording.screen import cg_to_pil

                x, y, w, h = box
                crop = Quartz.CGImageCreateWithImageInRect(frozen, ((x, y), (w, h)))
                self.rec.save_shot(cg_to_pil(crop), secs)
                on_main(flash_screen)
                self.app.status(f"Screenshots: {self.rec.shot_count}")
            except Exception as e:
                notify("Meeting Transcriber", "Screenshot failed", str(e))
            self.app.refresh_bar()

        self.selector = screenshot.AreaSelector(screen, frozen, done)
        self.selector.show()

    def toggle_notes(self):
        if not self.rec.recording or self.app.bar is None:
            return
        self.app.bar.toggle_notes()
        self.app.refresh_bar()

    def add_documents(self):
        """Pick files to give the summary more context. They are copied into the meeting folder and read in the background."""
        if not self.rec.recording or self.rec.meeting is None:
            return
        meeting = self.rec.meeting
        paths, ocr_on = self._choose_documents(bool(self.app.cfg.documents_ocr))
        if not paths:
            return
        if ocr_on != bool(self.app.cfg.documents_ocr):
            try:
                config.set_value("documents_ocr", ocr_on)  # the box starts the way you left it
                self.app.reload_cfg()
            except Exception:
                log.exception("Could not save the OCR setting")
        new, skipped = documents.add(meeting, [Path(p) for p in paths], int(self.rec.elapsed()), ocr_on)
        if skipped:
            notify("Meeting Transcriber", "Some documents were not added", "; ".join(skipped)[:220])
        if new:
            self.docs.submit(meeting, new)
        self.app.refresh_bar()

    def _choose_documents(self, ocr_default: bool) -> tuple[list[str], bool]:
        """The file chooser (several files at once) with a "Read text in images (OCR)" box under the list."""
        from AppKit import NSButton, NSColor, NSFont, NSOpenPanel, NSTextField, NSView
        from Foundation import NSMakeRect

        if self.app.bar is not None:
            self.app.bar.close_popups()  # the bar's popups float above the chooser and would block parts of it
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
