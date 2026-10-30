"""Processing meetings in the background, one at a time."""

import logging
import subprocess
import threading
import time
from pathlib import Path

from . import control, documents, progress
from .audio import has_audio
from .desktop import alert, bring_to_front, notify, on_main
from .pipeline import process, summarize_existing, transcribe_existing
from .summarize import notes_lines
from .transcription import clear_checkpoint, transcript_file

log = logging.getLogger("mt.processing")


class Processing:
    """Processing meetings: queueing a recording (or an existing meeting), running one at a time, and pausing, resuming, cancelling and repeating them.

    Mixed into App; it uses self.jobs, self.docs, self.prompt, self._status and the other parts of App."""

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
                        clear_checkpoint(d)
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
                clear_checkpoint(args[0])
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
