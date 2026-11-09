"""The app's side of the floating bar (see bar/)."""

import logging
import subprocess

from ..desktop import permissions
from ..desktop.hotkeys import fmt_hotkey
from ..meetings import documents, notes
from ..recording.device_toast import TOAST_S, DeviceToasts
from ..settings import config

log = logging.getLogger(__name__)


class BarPresenter:
    """What the floating bar asks of the app: its state, tooltips, menus and the lists behind its popups, and what a click in them does.

    The Bar calls the `overlay_*` methods (see bar/host.py); everything else is the app's own work, done by its controllers."""

    def __init__(self, app):
        self.app = app
        self.rec = app.rec
        self.jobs = app.jobs
        self.toasts = DeviceToasts()

    def device_toast(self, track: str, device: str) -> None:
        """The microphone or the sound output moved to another device while recording: say so beside the bar."""
        bar = self.app.bar
        if bar is not None and self.rec.recording:
            bar.show_toast(self.toasts.message(track, device), TOAST_S)

    def state(self) -> dict:
        jobs = self.jobs.snapshot()
        # The ring follows the most relevant meeting: one running, else one waiting, else a paused one, else the latest tab (done,
        # failed or cancelled): whatever tabs are in the panel, the ring says so and opens it.
        show = next((j for j in jobs if j["state"] == "active"), None) or next((j for j in jobs if j["state"] == "queued"), None) or next((j for j in jobs if j["state"] == "paused"), None) or (jobs[-1] if jobs else None)
        return {
            "recording": self.rec.recording,
            "elapsed": int(self.rec.elapsed()),  # recording time: a pause is not counted
            "paused": bool(self.rec.paused),
            "notesLines": notes.line_count(self.app.recording.notes_text) if self.rec.recording else 0,
            "docsCount": documents.count(self.rec.meeting) if self.rec.recording else 0,
            "docsReady": sum(1 for d in documents.load(self.rec.meeting) if d.get("status") == "ready") if self.rec.recording else 0,
            "micMuted": bool(self.rec.mic_muted),
            "sysMuted": bool(self.rec.sys_muted),
            "busy": len(jobs),  # the badge counts the tabs in the processing panel (so a paused or finished one can still be reopened)
            "perm": bool(self.app.permissions.missing),
            "restart": self.app.permissions.restart_pending(),
            "job": {"stage": show["stage"], "progress": show["progress"]} if show else None,
        }

    def overlay_in_captures(self) -> bool:
        return bool(self.app.cfg.overlay_in_captures)

    def overlay_action(self, op):
        app, recording = self.app, self.app.recording
        {"toggle": recording.toggle, "shot": recording.shot, "notes": recording.toggle_notes, "docs_add": recording.add_documents,
         "mute_mic": recording.toggle_mic, "mute_system": recording.toggle_system, "pause": recording.toggle_pause,
         "cancel_rec": recording.cancel_recording, "perm": app.permissions.fix, "quit": app.confirm_quit}[op]()
        app.refresh_bar()

    def overlay_tip(self, tip_id):
        cfg, rec = self.app.cfg, self.rec
        busy = bool(self.jobs.snapshot()) and not rec.recording
        return {
            "rec": {"text": "Stop and summarize" if rec.recording else "Show processing status" if busy else "Start recording",
                    "hotkey": None if busy else fmt_hotkey(cfg.hotkey_record)},
            "shot": {"text": "Take screenshot" if rec.recording else "Take screenshot (while recording)",
                     "hotkey": fmt_hotkey(cfg.hotkey_screenshot) if rec.recording else None},
            "notes": {"text": "Take notes (while recording)" if not rec.recording
                      else f"Notes · {n} line{'s' if n != 1 else ''}" if (n := notes.line_count(self.app.recording.notes_text)) else "Take notes",
                      "hotkey": fmt_hotkey(cfg.hotkey_notes) if rec.recording else None},
            "docadd": {"text": "Add documents" if rec.recording else "Add documents (while recording)",
                       "hotkey": fmt_hotkey(cfg.hotkey_documents) if rec.recording else None},
            "doclist": {"text": self._doclist_tip(), "hotkey": None},
            "mic": {"text": "Unmute microphone" if rec.mic_muted else "Mute microphone", "hotkey": fmt_hotkey(cfg.hotkey_mute_mic)},
            "sys": {"text": "Unmute system audio" if rec.sys_muted else "Mute system audio", "hotkey": fmt_hotkey(cfg.hotkey_mute_system)},
            "perm": {"text": "Restart to apply the permission", "hotkey": None} if self.app.permissions.restart_pending()
                    else {"text": "Permissions needed", "items": [permissions.LABELS[m] for m in self.app.permissions.missing], "hotkey": None},
            "more": {"text": "More", "hotkey": None},
            "hide": {"text": "Hide bar", "hotkey": None},
            "quit": {"text": "Quit", "hotkey": None},
            "tab": {"text": "Show bar", "hotkey": None},
        }.get(tip_id)

    def _doclist_tip(self) -> str:
        if not self.rec.recording:
            return "Documents (while recording)"
        docs = documents.load(self.rec.meeting)
        ready = sum(1 for d in docs if d.get("status") == "ready")
        return "Documents" if not docs else f"Documents · {len(docs)}" if ready == len(docs) else f"Documents · {ready} of {len(docs)} ready"

    def overlay_docs(self) -> dict:
        """The list in the documents popup: what was added to this recording, and how far reading each one is."""
        out = []
        for d in sorted(documents.load(self.rec.meeting) if self.rec.recording else [], key=lambda d: d["added_at"]):
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
        if act == "delete" and self.rec.meeting is not None:
            documents.delete(self.rec.meeting, doc_id)
            self.app.refresh_bar()

    def overlay_notes_info(self) -> dict:
        return {"timeKey": self.app.cfg.hotkey_notes_time, "timeLabel": fmt_hotkey(self.app.cfg.hotkey_notes_time)}

    def overlay_notes_save(self, text: str) -> str | None:
        """The notes panel sends everything typed so far; it goes to notes.txt in the meeting's folder."""
        self.app.recording.notes_text = text[:notes.MAX_CHARS]
        meeting = self.app.recording.notes_meeting
        if meeting is None or not meeting.folder.exists():
            return None
        try:
            notes.save_notes(meeting, self.app.recording.notes_text)
        except OSError as e:
            return f"Couldn't save: {str(e)[:60]}"
        return None

    def overlay_menu(self):
        styles = [{"id": f"style:{n}", "label": n, "checked": n == self.app.cfg.default_prompt} for n in config.list_prompts()]
        label = "Restart to apply the permission" if self.app.permissions.restart_pending() else "⚠ Permissions needed…"
        warn = [{"id": "perm", "label": label}, {"divider": True}] if self.app.permissions.missing else []
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
            self.app.choose_prompt(item_id[len("style:"):])
        elif item_id == "perm":
            self.app.permissions.fix()
        elif item_id == "summarize":
            self.app.processing.summarize_chosen()
        elif item_id == "transcribe":
            self.app.processing.transcribe_chosen()
        elif item_id == "settings":
            self.app.open_settings()
        elif item_id == "folder":
            subprocess.run(["open", str(self.app.cfg.meetings_dir)])

    def overlay_status(self):
        return {"jobs": [j for j in self.jobs.snapshot()], "canRecord": not self.rec.recording}

    def overlay_status_action(self, act, job_id):
        job = next((j for j in self.jobs.snapshot() if j["id"] == job_id), None)
        if act == "summary" and job and job["out"]:
            subprocess.run(["open", job["out"]])  # (the tab stays: only Dismiss, or ten minutes after finishing, removes it)
        elif act == "folder" and job:
            subprocess.run(["open", job.get("folder") or str(self.app.cfg.meetings_dir)])
        elif act == "pause" and job and job["state"] == "active":
            self.app.processing.pause_job(job_id)
        elif act == "resume" and job:
            self.app.processing.resume_job(job_id)
        elif act == "cancel" and job:
            self.app.processing.cancel_job(job_id, job)
        elif act == "again" and job and job["state"] in ("cancelled", "failed"):  # (a finished transcript is kept)
            self.app.processing.process_again(job)
        elif act == "dismiss" and job:
            self.jobs.dismiss(job_id)
        elif act == "record" and not self.rec.recording:
            self.app.recording.toggle()
        elif act == "close":
            pass  # × only hides the panel; every tab is still there when it is opened again

    def open_status(self):
        bar = self.app.bar
        if bar is not None and not bar.hidden:
            bar.open_status()
