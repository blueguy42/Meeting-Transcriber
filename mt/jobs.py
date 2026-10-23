"""Tracks the meetings that are being processed, for the floating bar's progress ring and status panel.

The worker thread feeds it the pipeline's progress messages (see progress.py); the UI reads snapshot().
Thread-safe: feed() runs on the worker thread, snapshot() on the main thread.
"""

import itertools
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from . import progress

# share of the overall progress ring each step covers (with no summary, transcribing takes the summary's share too)
WEIGHTS = {"transcribe": 0.6, "summarize": 0.3, "save": 0.1}
FOLDER = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(?:-\d+)?(?: - (.+))?$")
KEEP_DONE_SECONDS = 600  # a finished meeting stays in the panel this long unless you dismiss it


def display_name(folder: str) -> str:
    """'2026-10-20_1402' -> 'Meeting, Oct 20 · 14:02'; once titled, just the title."""
    m = FOLDER.match(folder)
    if not m:
        return folder
    if m.group(6):
        return m.group(6)
    y, mo, d, h, mi = (int(m.group(i)) for i in range(1, 6))
    try:
        return f"Meeting, {datetime(y, mo, d, h, mi):%b} {d} · {h:02d}:{mi:02d}"
    except ValueError:
        return folder


def _step(key: str, label: str, state: str = "todo") -> dict:
    return {"key": key, "label": label, "state": state, "sub": "", "children": []}


class Tracker:
    def __init__(self, labels: tuple[str, str], on_change=lambda: None):
        self.labels = labels  # (microphone label, system audio label), e.g. ("Me", "Others")
        self.on_change = on_change
        self._jobs: list[dict] = []
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    # ---- worker side
    def add(self, folder: Path, summarize: bool, transcribe: bool = True, recorded: bool = True, notes: int = 0, diarize: bool = False) -> int:
        steps = []
        if recorded:
            steps.append(_step("recorded", "Recorded", "done"))
        if transcribe:
            steps.append(_step("transcribe", "Transcribing"))
        if summarize:
            steps.append(_step("summarize", "Summarizing"))
            if notes:  # the summary will use the notes you took: say so under the step
                steps[-1]["detail"] = f"with your notes ({notes} line{'s' if notes != 1 else ''})"
        steps.append(_step("save", "Saving files"))
        job = {"id": next(self._ids), "folder": str(folder), "name": display_name(folder.name), "state": "queued",
               "steps": steps, "diarize": bool(diarize and transcribe), "out": None, "error": "", "seen": False, "finished": 0.0}
        with self._lock:
            self._jobs.append(job)
        self.on_change()
        return job["id"]

    def begin(self, job_id: int) -> None:
        with self._lock:
            job = self._get(job_id)
            job["state"] = "active"
            self._advance(job, job["steps"][1]["key"] if job["steps"][0]["key"] == "recorded" else job["steps"][0]["key"])
        self.on_change()

    def feed(self, job_id: int, msg: str) -> None:
        ev = progress.parse(str(msg), list(self.labels))
        if ev is None:
            return
        with self._lock:
            job = self._get(job_id)
            if ev["kind"] == "transcribing":
                step = self._advance(job, "transcribe")
                if step is None:
                    return
                kids = step["children"]
                seen_it = False
                for c in kids:  # the tracks run one after the other: the ones before this label are finished
                    if c["key"] == ev["label"]:
                        seen_it = True
                        c["state"] = "now" if c.get("fraction", 0.0) < 1 else "done"
                        if ev["fraction"] is not None:
                            c["fraction"] = ev["fraction"]
                            c["sub"] = f"{round(ev['fraction'] * 100)}%"
                            if ev["fraction"] >= 1:
                                c.update(state="done", sub="")
                        elif c["state"] == "now":
                            c["sub"] = ""
                    elif not seen_it:
                        c.update(state="done", sub="")
            elif ev["kind"] in ("diarizing", "diarized"):
                step = self._advance(job, "transcribe")
                child = next((c for c in (step["children"] if step else []) if c["key"] == "diarize"), None)
                if child is None:
                    return
                for c in step["children"]:  # speakers are told apart once both tracks are transcribed
                    if c["key"] != "diarize":
                        c.update(state="done", sub="")
                if ev["kind"] == "diarized":
                    child.update(state="done", fraction=1.0, sub="Skipped" if ev["failed"] else "")
                else:
                    child.update(state="now", fraction=ev["fraction"], sub=ev["note"] or f"{round(ev['fraction'] * 100)}%")
            elif ev["kind"] == "summarizing":
                self._advance(job, "summarize")
            elif ev["kind"] == "docx":
                self._advance(job, "save")
        self.on_change()

    def finish(self, job_id: int, folder: Path, out: Path) -> None:
        with self._lock:
            job = self._get(job_id)
            for s in job["steps"]:
                s.update(state="done", sub="")
                for c in s["children"]:
                    c.update(state="done", sub="")
            job.update(state="done", folder=str(folder), out=str(out), name=display_name(folder.name), finished=time.time())
        self.on_change()

    def fail(self, job_id: int, message: str) -> None:
        with self._lock:
            job = self._get(job_id)
            job.update(state="failed", error=message[:200], finished=time.time())
        self.on_change()

    # ---- UI side
    def acknowledge(self, job_id: int | None = None) -> None:
        """You have seen this result (or dismissed it): it leaves the bar. None = all finished jobs."""
        with self._lock:
            for j in self._jobs:
                if j["state"] in ("done", "failed") and job_id in (None, j["id"]):
                    j["seen"] = True
        self.on_change()

    @property
    def busy(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs if j["state"] in ("queued", "active"))

    def snapshot(self) -> list[dict]:
        """Jobs worth showing: running or waiting ones, then unseen results; oldest first."""
        now = time.time()
        with self._lock:
            self._jobs = [j for j in self._jobs if not (j["seen"] or (j["finished"] and now - j["finished"] > KEEP_DONE_SECONDS))]
            out = []
            for j in self._jobs:
                c = {k: v for k, v in j.items() if k != "steps"}
                c["steps"] = [{**s, "children": [dict(k) for k in s["children"]]} for s in j["steps"]]
                c["progress"] = self._progress(j)
                c["stage"] = self._stage(j)
                out.append(c)
            return out

    # ---- internals
    def _get(self, job_id: int) -> dict:
        return next(j for j in self._jobs if j["id"] == job_id)

    def _track_name(self, label: str) -> str:
        return "Your mic" if label == self.labels[0] else "System audio"

    def _advance(self, job: dict, key: str) -> dict | None:
        """Make `key` the current step: everything before it is done. -> that step (None if the job has none)."""
        hit = None
        for s in job["steps"]:
            if s["key"] == key:
                s["state"] = "now"
                if key == "transcribe" and not s["children"]:  # show both tracks from the start; the second waits its turn
                    s["children"] = [
                        {"key": label, "label": self._track_name(label), "state": "now" if i == 0 else "todo",
                         "sub": "" if i == 0 else "Waiting", "fraction": 0.0}
                        for i, label in enumerate(self.labels)
                    ]
                    if job.get("diarize"):  # telling the other people apart is the last part of transcribing
                        s["children"].append({"key": "diarize", "label": "Speakers", "state": "todo", "sub": "Waiting", "fraction": 0.0})
                hit = s
                break
            s.update(state="done", sub="")
            for c in s["children"]:
                c.update(state="done", sub="")
        return hit

    def _weights(self, job: dict) -> dict:
        w = dict(WEIGHTS)
        if not any(s["key"] == "summarize" for s in job["steps"]):
            w["transcribe"] += w.pop("summarize")
        return w

    def _progress(self, job: dict) -> float:
        if job["state"] == "done":
            return 1.0
        w, total = self._weights(job), 0.0
        for s in job["steps"]:
            share = w.get(s["key"], 0.0)
            if s["state"] == "done":
                total += share
            elif s["state"] == "now":
                if s["key"] == "transcribe":
                    # a recording has two tracks; a track that never shows up leaves the ring at half until summarizing starts
                    part = min(1.0, sum(c.get("fraction", 0.0) if c["state"] != "done" else 1.0 for c in s["children"]) / max(len(s["children"]), 1))
                else:
                    part = 0.5
                total += share * part
        return round(min(total, 0.99), 3)

    def _stage(self, job: dict) -> str:
        if job["state"] in ("done", "failed", "queued"):
            return job["state"]
        now = next((s["key"] for s in job["steps"] if s["state"] == "now"), "transcribe")
        return {"transcribe": "transcribing", "summarize": "summarizing", "save": "saving"}.get(now, "transcribing")
