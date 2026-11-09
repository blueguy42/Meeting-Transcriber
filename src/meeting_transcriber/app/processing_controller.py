"""Processing meetings in the background, one at a time."""

import logging
import subprocess
import threading
import time

from ..desktop.macos import alert, bring_to_front, notify, on_main
from ..meetings import documents
from ..meetings.audio import has_audio
from ..meetings.meeting import Meeting
from ..processing import control, progress
from ..processing.pipeline import process, summarize_existing, transcribe_existing
from ..summary.summarize import notes_lines
from ..transcription import clear_checkpoint

log = logging.getLogger(__name__)


class ProcessingController:
    """Processing meetings: queueing a recording (or an existing meeting), running one at a time, and pausing, resuming, cancelling and repeating them."""

    def __init__(self, app):
        self.app = app
        self.jobs = app.jobs
        self.docs = app.docs
        self.controls: dict[int, control.JobControl] = {}  # pause / cancel switches of the meetings being processed (by job id)
        self.jobargs: dict[int, tuple] = {}  # what each job was started with, so Resume can run it again
        self.work_lock = threading.Lock()  # process one meeting at a time (GPU), but never block recording

    def _choose_meeting(self, message: str, button: str) -> Meeting | None:
        """Ask for a meeting folder (a folder picker starting in the meetings folder)."""
        from AppKit import NSOpenPanel
        from Foundation import NSURL

        bring_to_front()
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setMessage_(message)
        panel.setPrompt_(button)
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(self.app.cfg.meetings_dir)))
        if panel.runModal() != 1:
            return None
        return Meeting(str(panel.URL().path()))

    def summarize_chosen(self):
        """Pick a meeting folder to summarize or re-summarize (in the current Summary style)."""
        meeting = self._choose_meeting("Choose a meeting folder to summarize", "Summarize")
        if meeting is None:
            return
        if not (meeting.has_recording or meeting.has_transcript):
            notify("Meeting Transcriber", "Not a meeting folder", "It needs a transcript or a recording (meta.json).")
            return
        self.run_pipeline(meeting, summarize_only=True)

    def transcribe_chosen(self):
        """Pick a meeting folder and transcribe its recording (again), then summarize it, if that is on."""
        meeting = self._choose_meeting("Choose a meeting folder to transcribe", "Transcribe")
        if meeting is None:
            return
        if not meeting.has_recording or not has_audio(meeting):
            notify("Meeting Transcriber", "Nothing to transcribe", "That folder has no recording (meta.json and the audio files).")
            return
        self.run_pipeline(meeting, transcribe_only=True)

    def run_pipeline(self, meeting, summarize_only=False, transcribe_only=False):
        prompt = self.app.prompt
        cfg0 = self.app.reload_cfg()
        self.jobs.labels = (cfg0.my_label, cfg0.others_label)
        reading = not (summarize_only or transcribe_only) and self.docs.pending(meeting) > 0  # a recording that has just stopped
        n_docs = len([x for x in documents.load(meeting) if x.get("status") in ("ready", "queued", "reading")]) if cfg0.use_documents else 0
        job = self.jobs.add(
            meeting.folder,
            summarize=bool(cfg0.auto_summarize or summarize_only),  # (transcribing a meeting follows the auto-summarize setting)
            transcribe=not (summarize_only and meeting.has_transcript),
            recorded=not (summarize_only or transcribe_only),  # an existing meeting: no "Recorded" step
            diarize=bool(cfg0.diarize),  # "Telling speakers apart" is a part of Transcribing
            notes=notes_lines(meeting, cfg0),
            documents=n_docs,
            reading=reading,  # the summary also reads the notes you took (shown under "Summarizing")
            mode="summarize" if summarize_only else "transcribe" if transcribe_only else "full",
        )
        on_main(self.app.presenter.open_status)
        log.info("Job queued: %s (summarize=%s, transcribe_only=%s, summarize_only=%s)", meeting.name, bool(cfg0.auto_summarize or summarize_only), transcribe_only, summarize_only)

        self._start_job(job, meeting, summarize_only, transcribe_only, prompt, reading)

    def _start_job(self, job, meeting, summarize_only, transcribe_only, prompt, reading):
        """Run (or, after a pause, run again) one meeting's processing on its own thread, one meeting at a time."""
        ctrl = control.JobControl()
        self.controls[job] = ctrl
        self.jobargs[job] = (meeting, summarize_only, transcribe_only, prompt)

        def say(text):
            if text == progress.DOCX:
                ctrl.locked = True  # saving the files: too late to pause or cancel, it finishes
            self.jobs.feed(job, text)
            self.app.status(text)
            if not text.startswith("Audio compression"):  # (quick, and the transcript is already saved: a pause waits for the next step)
                ctrl.check()  # a pause or cancel stops the step here (see control.py)

        def work():
            with self.work_lock:  # one meeting at a time; recording is never blocked
                control.bind(ctrl)
                try:
                    if ctrl.reason:  # cancelled while waiting its turn (the row already says so)
                        return
                    cfg = self.app.reload_cfg()
                    self.jobs.begin(job)
                    t0 = time.time()
                    log.info("Job started: %s", meeting.name)
                    if reading:  # the summary needs the documents: wait for the ones still being read
                        self.docs.wait(meeting, lambda done, total: say(progress.reading_documents(done, total)))
                    if transcribe_only:
                        new_meeting, out = transcribe_existing(meeting, cfg, prompt, progress=say)
                    elif summarize_only:
                        new_meeting, out = summarize_existing(meeting, cfg, prompt, progress=say)
                    else:
                        new_meeting, out = process(meeting, cfg, prompt, progress=say)
                    self.jobs.finish(job, new_meeting.folder, out)
                    on_main(self.app.presenter.open_status)
                    log.info("Job done in %ds: %s", time.time() - t0, out)
                    notify("Meeting Transcriber", "Done", out.name)
                    subprocess.run(["open", "-R", str(out)])  # reveal the result in Finder
                except control.Stopped as stop:
                    if stop.reason == "cancel":
                        self.jobs.cancel(job)
                        clear_checkpoint(meeting)
                    else:
                        self.jobs.pause(job)
                    log.info("Job %s: %s", "cancelled" if stop.reason == "cancel" else "paused", meeting.name)
                    on_main(self.app.presenter.open_status)
                except Exception as e:
                    log.exception("Processing failed for %s", meeting.name)
                    self.jobs.fail(job, str(e))
                    on_main(self.app.presenter.open_status)
                    log.error("Job failed: %s: %s", meeting.name, e)
                    notify("Meeting Transcriber", "Processing failed", str(e)[:200])
                finally:
                    control.bind(None)

        threading.Thread(target=work, daemon=True).start()

    def resume_job(self, job_id: int) -> None:
        """Run a paused meeting again from the step it was paused at (transcribing carries on from its checkpoint)."""
        args = self.jobargs.get(job_id)
        info = self.jobs.job(job_id)
        if args is None or info is None or info["state"] != "paused":
            return
        meeting, summarize_only, transcribe_only, prompt = args
        at = info["paused_at"]
        if at == "summarize":  # the transcript is done: only the summary is asked for again
            summarize_only, transcribe_only = True, False
        self.jobs.resume(job_id)
        log.info("Job resumed at %s: %s", at, meeting.name)
        self._start_job(job_id, meeting, summarize_only, transcribe_only, prompt, reading=(at == "readdocs"))

    def pause_job(self, job_id: int) -> None:
        """Stop the step that is running (within a second or two); Resume carries on from there."""
        ctrl = self.controls.get(job_id)
        if ctrl is not None and ctrl.stop("pause"):
            self.jobs.stopping(job_id, "pause")

    def cancel_job(self, job_id: int, job: dict) -> None:
        """Stop a meeting's processing for good (the recording stays). Asks first when work would be thrown away."""
        state = job["state"]
        if state in ("active", "paused"):
            bring_to_front()
            if alert("Stop processing this meeting?", "The recording is kept and you can process it again later (Transcribe a meeting… or "
                     "Summarize a meeting…). Progress on the current step is lost.", ok="Keep going", cancel="Stop processing", destructive_second=True) != "second":
                return
        ctrl = self.controls.get(job_id)
        if state == "paused" or state == "queued":
            if ctrl is not None:
                ctrl.stop("cancel")  # (a waiting meeting leaves as soon as its turn comes)
            self.jobs.cancel(job_id)
            args = self.jobargs.get(job_id)
            if args is not None:
                clear_checkpoint(args[0])
        elif state == "active" and ctrl is not None and ctrl.stop("cancel"):
            self.jobs.stopping(job_id, "cancel")

    def process_again(self, job: dict) -> None:
        meeting, mode = Meeting(job["folder"]), job["mode"]
        self.jobs.dismiss(job["id"])
        if mode == "transcribe":
            self.run_pipeline(meeting, transcribe_only=True)
        elif mode == "summarize" or meeting.has_transcript:  # a finished transcript is kept
            self.run_pipeline(meeting, summarize_only=True)
        else:
            self.run_pipeline(meeting)
