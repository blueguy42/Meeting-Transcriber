"""The speakers of a finished meeting, in the processing panel: what the rows show, hearing a sample, giving a name."""

import logging

from ..desktop.macos import alert, ask_name, bring_to_front
from ..desktop.player import SamplePlayer
from ..meetings import speakers
from ..meetings.meeting import Meeting
from ..settings import config as settings_config

log = logging.getLogger(__name__)


class SpeakerController:
    def __init__(self, app):
        self.app = app
        self.player = SamplePlayer()
        self.playing: tuple[str, str] | None = None  # (meeting folder, label) of the sample that is playing
        self.sample_at: dict[tuple[str, str], int] = {}  # which sample each speaker offers next
        self.sample_shown: set[tuple[str, str]] = set()  # speakers whose sample line stays on show (a named speaker shows it only once you have asked to hear it)
        self._cache: dict[str, tuple[tuple, dict]] = {}  # meeting folder -> (what the files looked like, the rows)

    # ---- what the panel shows
    def info(self, job: dict) -> dict:
        """The extra fields of a finished meeting's tab: its speakers (empty when there is nothing to name)."""
        meeting = Meeting(job["folder"])
        cfg = self.app.cfg
        try:
            stamp = (meeting.transcript_file.stat().st_mtime, meeting.meta_file.stat().st_mtime, tuple(cfg.vocabulary), cfg.others_label, cfg.my_label)
        except OSError:
            return {}
        hit = self._cache.get(job["folder"])
        if hit is None or hit[0] != stamp:
            rows = speakers.rows(meeting, cfg)
            meta = meeting.read_meta()
            hit = self._cache[job["folder"]] = (stamp, {"rows": rows, "stale": bool(meta.get("summary_stale")), "checked": bool(meta.get("speakers_checked"))})
        rows = [{**r, "sample": self.sample_at.get((job["folder"], r["label"]), 0), "playing": self.playing == (job["folder"], r["label"]),
                 "showSample": (job["folder"], r["label"]) in self.sample_shown} for r in hit[1]["rows"]]
        return {"speakers": rows, "speakersStale": hit[1]["stale"], "speakersChecked": hit[1]["checked"],
                "canPlay": speakers.audio_file(meeting) is not None, "canName": bool(self.app.cfg.name_speakers and self.app.cfg.diarize and settings_config.find_claude(self.app.cfg))}

    # ---- what the buttons do
    def act(self, act: str, job: dict, label: str | None) -> None:
        meeting = Meeting(job["folder"])
        if act == "play" and label:
            self.play(meeting, job["folder"], label)
        elif act == "nextsample" and label:
            key = (job["folder"], label)
            self.sample_at[key] = self.sample_at.get(key, 0) + 1
            self.sample_shown.add(key)  # (the line is what you are choosing between: it must not disappear when the sample stops)
            if self.playing is not None:  # a sample is playing: moving on stops it (its end refreshes the panel)
                self.player.stop()
            else:
                self.refresh()
        elif act == "rename" and label:
            self.rename(meeting, label)
        elif act == "resummarize":
            self.app.jobs.dismiss(job["id"])
            self.app.processing.run_pipeline(meeting, summarize_only=True)
        elif act == "namespeakers":
            self.app.jobs.dismiss(job["id"])
            self.app.processing.run_pipeline(meeting, name_only=True)

    def refresh(self) -> None:
        bar = self.app.bar
        if bar is not None and bar.status.wanted:
            bar.status.render_tabs()

    def play(self, meeting: Meeting, folder: str, label: str) -> None:
        key = (folder, label)
        self.sample_shown.add(key)
        if self.playing == key:  # pressed again while it plays: stop
            self.player.stop()
            return
        cfg = self.app.cfg
        found = speakers.samples(meeting, cfg, label)
        path = speakers.audio_file(meeting)
        if not found or path is None:
            return
        sample = found[self.sample_at.get(key, 0) % len(found)]

        def ended():
            if self.playing == key:
                self.playing = None
            self.refresh()

        if self.player.play(path, speakers.track_time(meeting, sample["start"]), sample["end"] - sample["start"], ended):
            self.playing = key
            self.refresh()

    def rename(self, meeting: Meeting, label: str) -> None:
        """Ask for a name; an empty one takes the name away again."""
        cfg = self.app.cfg
        names = speakers.saved(meeting)
        current = (names.get(label) or {}).get("name", "")
        bring_to_front()
        while True:
            answer = ask_name(f"Who is {label}?" if not current else f"Rename {current}",
                              "Type a name, or pick one from the list. Leave it empty to go back to the generic label.",
                              speakers.roster(meeting, cfg), current)
            if answer is None:
                return
            try:
                speakers.rename(meeting, cfg, label, answer)
                break
            except ValueError as e:
                alert("Choose another name", str(e))
                current = answer
        self._cache.pop(str(meeting.folder), None)
        self.refresh()
