"""The app's side of the floating bar (see overlay/)."""

import logging
import subprocess

from . import config, documents, notes, permissions
from .desktop import alert, bring_to_front
from .devicetoast import TOAST_S
from .hotkeys import fmt_hotkey

log = logging.getLogger("mt.overlay_host")


class OverlayHost:
    """What the floating bar asks of the app: its state, tooltips, menus and the lists behind its popups, and what a click in them does.

    Mixed into App; it uses self.cfg, self.rec, self.jobs, self.docs, self._overlay and the other parts of App."""

    # ---- floating bar (the Overlay asks us for content and tells us what you clicked: see mt/overlay/)
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
