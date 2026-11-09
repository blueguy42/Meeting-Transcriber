"""Records mic (sounddevice) + system audio (sck-audio helper) + screenshots into a meeting folder.

What a meeting folder holds is described in meetings/meeting.py."""

import queue
import shutil
import signal
import subprocess
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

from ..meetings.meeting import Meeting
from ..settings import config
from ..settings.model import Settings
from .clock import RecordingClock
from .coreaudio import default_input_id, default_output_id, device_name
from .device_events import merged_events
from .screen import grab_display, mouse_position, screen_access
from .wavtools import cut_wav, make_resampler


SR = 16000


class Recorder:
    def __init__(self, cfg: Settings, on_warning=print):
        self.cfg = cfg
        self.warn = on_warning
        self.meeting: Meeting | None = None
        self.clock = RecordingClock()  # the recording's own time: wall time minus pauses (see clock.py)
        self.mic_first: float | None = None
        self.sys_first: float | None = None
        self._stream = None
        self._wav = None
        self._q: queue.Queue = queue.Queue()
        self._writer: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self._mic_lock = threading.Lock()  # guards opening/closing the mic stream (watchdog vs. stop)
        self._mic_stop = threading.Event()
        self._mic_watcher: threading.Thread | None = None
        self._mic_dev: int | None = None  # default input device the stream was opened on
        self._mic_last = 0.0  # when the mic callback last ran
        self._mic_written = 0  # samples written to mic.wav (at SR)
        self._mic_resync = False  # stream was reopened: pad the gap with silence
        self._mic_lost = False
        self._mic_events: list[dict] = []  # device changes during the recording (wall clock)
        self._out_watcher: threading.Thread | None = None
        self.on_device_change = None  # ("mic" | "system", device name): called from a watcher thread when the device was switched
        self._mic_start_device: str | None = None  # default input / output when the recording started
        self._system_start_device: str | None = None
        self._system_events: list[dict] = []  # default output changes during the recording (wall clock)
        self.shot_count = 0
        self.mic_muted = False
        self.sys_muted = False
        self._mute_start: dict[str, float | None] = {"mic": None, "system": None}  # wall clock, while muted
        self._mute_spans: dict[str, list[tuple[float, float]]] = {"mic": [], "system": []}
        self._mute_lock = threading.Lock()
        # meta.json is saved while recording too, so a crash leaves a recording that can still be transcribed. One thread does it
        # (the audio callbacks only raise the flag), one lock keeps the final save from overlapping a running one.
        self._meta_lock = threading.Lock()
        self._meta_dirty = threading.Event()
        self._meta_stop = threading.Event()
        self._meta_thread: threading.Thread | None = None

    @property
    def recording(self) -> bool:
        return self.meeting is not None

    @property
    def t0(self) -> float:
        """When the recording started (wall clock)."""
        return self.clock.t0

    @property
    def paused(self) -> bool:
        """A real pause: nothing is recorded, and the time does not count."""
        return self.clock.paused

    def elapsed(self) -> float:
        """Seconds of recording so far, not counting pauses (what the timer, notes and screenshots use)."""
        return self.clock.elapsed() if self.recording else 0.0

    def start(self) -> Meeting:
        if self.recording:
            raise RuntimeError("already recording")
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        d = self.cfg.meetings_dir / stamp
        n = 2
        while d.exists():
            d = self.cfg.meetings_dir / f"{stamp}-{n}"
            n += 1
        meeting = Meeting(d)
        meeting.create()
        self.meeting = meeting
        self.clock.start(time.time())
        self.mic_first = self.sys_first = None
        self.shot_count = 0
        # mutes are set any time and carry over: one that is on at the start counts from the start
        self._mute_start = {"mic": self.t0 if self.mic_muted else None, "system": self.t0 if self.sys_muted else None}
        self._mute_spans = {"mic": [], "system": []}
        try:
            self._start_capture(meeting)
            self._start_meta_saver()
        except Exception:
            # Don't leave a half-started recording (open files, a running helper) behind.
            self._close()
            self.meeting = None
            shutil.rmtree(meeting.folder, ignore_errors=True)
            raise
        return meeting

    def _start_capture(self, meeting: Meeting) -> None:
        import sounddevice as sd

        # system audio via ScreenCaptureKit helper
        helper = config.helper_path()
        if helper is None:
            self.warn("sck-audio helper not found - recording mic only")
        else:
            self._proc = subprocess.Popen(
                [str(helper), str(meeting.recordings_dir / "system.wav"), *(["--muted"] if self.sys_muted else [])],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            threading.Thread(target=self._read_helper, daemon=True).start()

        # microphone
        self._wav = wave.open(str(meeting.recordings_dir / "mic.wav"), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(SR)
        self._writer = threading.Thread(target=self._write_loop, daemon=True)
        self._writer.start()

        self._mic_stop.clear()
        self._mic_written = 0
        self._mic_resync = False
        self._mic_lost = False
        self._mic_events = []
        self._system_events = []
        self._mic_start_device = device_name(default_input_id())
        self._system_start_device = device_name(default_output_id())
        if helper is not None:
            self._out_watcher = threading.Thread(target=self._watch_output, daemon=True)
            self._out_watcher.start()
        if self._open_mic():
            self._mic_watcher = threading.Thread(target=self._watch_mic, daemon=True)
            self._mic_watcher.start()

    def _open_mic(self) -> bool:
        """Open the default input at 16 kHz, or at its own rate with our own resampler. Warns and returns False on failure."""
        import sounddevice as sd

        resampler = None

        def cb(indata, frames, time_info, status):
            now = time.time()
            self._mic_last = now
            if self.paused:  # nothing is written while paused: the file just carries on afterwards
                return
            if self.mic_first is None:
                self.mic_first = now
                self._meta_dirty.set()  # (no disk work on the audio thread)
            pcm = indata[:, 0] if resampler else indata  # fallback path may deliver several channels
            if resampler:
                pcm = resampler(pcm)
            if self._mic_resync:  # reopened after a device change: keep the file aligned with the clock
                self._mic_resync = False
                gap = int(self.clock.rel(now, self.mic_first) * SR) - self._mic_written - len(pcm)
                if gap > 0:
                    self._q.put(bytes(gap * 2))
                    self._mic_written += gap
            self._mic_written += len(pcm)
            # muted: keep the file running (so it stays aligned with the clock) but write silence
            self._q.put(bytes(pcm.nbytes) if self.mic_muted else bytes(pcm))

        self._mic_dev = default_input_id()
        self._mic_last = time.time()
        try:
            try:
                self._stream = sd.InputStream(samplerate=SR, channels=1, dtype="int16", callback=cb)
                self._stream.start()
            except Exception as e:
                # CoreAudio (-10851 "Invalid Property Value") rejects 16 kHz on some inputs, e.g. Bluetooth
                # headsets and aggregate devices. Open it at its own rate and resample ourselves.
                self.warn(f"microphone rejected {SR} Hz ({e}); retrying at the device rate")
                if self._stream is not None:
                    self._stream.close()
                info = sd.query_devices(kind="input")
                rate = int(info["default_samplerate"])
                resampler = make_resampler(rate, SR)
                self._stream = sd.InputStream(samplerate=rate, channels=1, dtype="int16", callback=cb)
                self._stream.start()
            return True
        except Exception as e:  # no mic permission / no device
            self.warn(f"microphone unavailable: {e}")
            self._stream = None
            return False

    def _drop_mic(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _watch_mic(self) -> None:
        """Follow the default microphone: reopen when it changes, vanishes or stops delivering audio.

        The gap is filled with silence so mic.wav stays aligned with the clock and the system track."""
        import sounddevice as sd

        pending = None  # the switch / stall being handled, logged once the mic is open again
        while not self._mic_stop.wait(1.0):
            with self._mic_lock:
                if self._mic_stop.is_set():
                    return
                cur = default_input_id()
                if self._stream is None:  # lost earlier: retry until a device is back
                    reason = "retry"
                elif cur is not None and self._mic_dev is not None and cur != self._mic_dev:
                    reason = "changed"
                elif not self._stream.active or time.time() - self._mic_last > 3:
                    reason = "stalled"
                else:
                    continue
                if reason != "retry":
                    pending = {"t": time.time(), "event": "switched" if reason == "changed" else "stalled"}
                    self._drop_mic()
                    if not self._mic_lost:
                        self.warn("microphone changed or disconnected - reopening")
                try:  # PortAudio only sees devices that existed when it was initialised
                    sd._terminate()
                    sd._initialize()
                except Exception:
                    pass
                self._mic_resync = True
                if self._open_mic():
                    if self._mic_lost:
                        self.warn("microphone is back")
                    self._mic_lost = False
                    try:
                        name = sd.query_devices(kind="input")["name"]
                    except Exception:
                        name = None
                    if pending is None:  # back after a spell with no mic
                        pending = {"t": time.time(), "event": "restored"}
                    self._mic_events.append({**pending, "device": name})
                    if pending["event"] in ("switched", "restored"):
                        self._device_changed("mic", name)
                    pending = None
                else:
                    if not self._mic_lost:  # one entry per outage, not one per retry
                        self._mic_events.append({**pending, "event": "lost"})
                    pending = None
                    self._mic_lost = True

    def _device_changed(self, track: str, name: str | None) -> None:
        if name and self.on_device_change is not None:
            try:
                self.on_device_change(track, name)
            except Exception:
                pass

    def _watch_output(self) -> None:
        """Log changes of the default output device. System audio is captured from the whole display, so
        it keeps recording across a change; this only notes which device it was playing through."""
        cur = default_output_id()
        while not self._mic_stop.wait(1.0):
            new = default_output_id()
            if new is not None and new != cur:
                cur = new
                name = device_name(new)
                self._system_events.append({"t": time.time(), "event": "switched", "device": name})
                self._device_changed("system", name)

    # ---- mute: the audio is replaced by silence at the source, so what was said is never saved
    def _send_helper(self, command: str) -> bool:
        try:
            self._proc.stdin.write(command + "\n")
            self._proc.stdin.flush()
            return True
        except (AttributeError, OSError, ValueError):  # no helper, or it has exited
            return False

    def set_muted(self, track: str, muted: bool) -> bool:
        """Mute or unmute "mic" or "system", recording or not (it applies to the next recording too).
        Returns False if the system audio helper is running but can't be reached."""
        with self._mute_lock:
            if track == "system":
                if self.recording and not self.paused and not self._send_helper("MUTE" if muted else "UNMUTE"):  # paused: the helper stays silent until resume
                    return False
                self.sys_muted = muted
            else:
                self.mic_muted = muted
            if not self.recording:
                return True
            self._meta_dirty.set()
            now = time.time()
            if muted and self._mute_start[track] is None:
                self._mute_start[track] = now
            elif not muted and self._mute_start[track] is not None:
                self._mute_spans[track].append((self._mute_start[track], now))
                self._mute_start[track] = None
        return True

    # ---- pause: nothing is recorded, and the paused time is not counted (see clock.py)
    def pause(self) -> bool:
        """Stop recording until resume(). The microphone stops writing; the system audio helper writes silence that is cut
        out when the recording ends. False if it can't be done."""
        with self._mute_lock:
            if not self.recording or self.paused:
                return False
            if self._proc is not None and not self._send_helper("MUTE"):
                return False
            self.clock.pause()
            self._meta_dirty.set()
        return True

    def resume(self) -> bool:
        with self._mute_lock:
            if not self.recording or not self.paused:
                return False
            now = time.time()
            if self._proc is not None:
                self._send_helper("MUTE" if self.sys_muted else "UNMUTE")  # back to the mute you had set
            self.clock.resume(now)
            self._mic_resync = True  # the clock moved on without the file: no padding for the paused time
            self._meta_dirty.set()
        return True

    def cancel(self) -> Meeting | None:
        """Stop and throw the recording away: the meeting folder is deleted. Returns the meeting that was deleted."""
        if not self.recording:
            return None
        meeting = self.meeting
        try:
            self._close()
        finally:
            self.meeting = None
            self.clock.resume()
        root = Path(self.cfg.meetings_dir).resolve()
        if meeting.folder.resolve().parent == root:  # only ever a meeting folder inside the meetings folder
            shutil.rmtree(meeting.folder, ignore_errors=True)
        return meeting

    def _read_helper(self):
        for line in self._proc.stdout:
            line = line.strip()
            if line.startswith("FIRST "):
                self.sys_first = float(line.split()[1])
                self._meta_dirty.set()
            elif line.startswith("ERROR"):
                self.warn(f"system audio: {line[6:]} (check Screen Recording permission)")

    def _write_loop(self):
        while True:
            chunk = self._q.get()
            if chunk is None:
                return
            self._wav.writeframes(chunk)

    def screenshot(self) -> Path | None:
        """The whole screen under the mouse, saved at once (the command line uses this; the app lets you choose an area)."""
        if not self.recording:
            return None
        self.require_screen_access()
        secs = time.time() - self.t0
        x, y = mouse_position()
        return self.save_shot(grab_display(x, y), secs)

    @staticmethod
    def require_screen_access() -> None:
        if not screen_access():
            raise RuntimeError("Screen Recording permission needed: allow Meeting Transcriber in System Settings, then restart it")

    def save_shot(self, img, secs: float) -> Path | None:
        """Save a picture (the whole screen or a chosen area) as shots/shot_<seconds>.jpg, scaled by screenshot_scale.
        `secs` is how far into the recording the picture was taken: the moment the screen froze."""
        if not self.recording:
            return None
        from PIL import Image

        scale = self.cfg.screenshot_scale
        if scale < 100:
            img = img.resize((round(img.width * scale / 100), round(img.height * scale / 100)), Image.LANCZOS)
        n = int(secs)
        while self.meeting.shot_file(n).exists():  # two shots in one second
            n += 1
        path = self.meeting.shot_file(n)
        img.save(path, quality=80)
        self.shot_count += 1
        return path

    def _close(self) -> None:
        """Stop everything that may be running. Safe to call twice and after a partial start."""
        self._stop_meta_saver()  # first: nothing may write into the folder once it is being finished or deleted
        self._mic_stop.set()
        if self._mic_watcher is not None:
            self._mic_watcher.join(timeout=10)
            self._mic_watcher = None
        if self._out_watcher is not None:
            self._out_watcher.join(timeout=5)
            self._out_watcher = None
        with self._mic_lock:
            self._drop_mic()
        if self._writer is not None:
            self._q.put(None)
            self._writer.join(timeout=5)
            self._writer = None
        if self._wav is not None:
            try:
                self._wav.close()
            except Exception:
                pass
            self._wav = None
        if self._proc is not None:
            try:
                self._proc.send_signal(signal.SIGINT)
                self._proc.wait(timeout=8)
            except Exception:
                self._proc.kill()
            self._proc = None

    # ---- meta.json: what this recording knew, saved at the start, kept up to date, and finished when it stops
    def _start_meta_saver(self) -> None:
        self._meta_stop.clear()
        self._meta_dirty.clear()
        self._save_meta(time.time(), complete=False)  # from the first moment there is a recording to recover
        self._meta_thread = threading.Thread(target=self._meta_loop, daemon=True)
        self._meta_thread.start()

    def _stop_meta_saver(self) -> None:
        self._meta_stop.set()
        self._meta_dirty.set()
        if self._meta_thread is not None:
            self._meta_thread.join(timeout=5)
            self._meta_thread = None

    def _meta_loop(self) -> None:
        """Save when something worth saving happened (a track started, a pause, a mute), and every 10 seconds anyway
        (the length of the recording so far). A burst of changes is one save."""
        while not self._meta_stop.is_set():
            self._meta_dirty.wait(10)
            if self._meta_stop.wait(0.5):  # let a burst of changes finish
                return
            self._meta_dirty.clear()
            try:
                self._save_meta(time.time(), complete=False)
            except Exception:
                pass  # (the next save tries again; stopping saves the real one)

    def _save_meta(self, end: float, complete: bool) -> None:
        with self._meta_lock:
            meeting = self.meeting
            if meeting is not None and meeting.folder.exists():
                meeting.write_meta(self._build_meta(end, complete))

    def _build_meta(self, end: float, complete: bool) -> dict:
        with self._mute_lock:  # pause, resume and mute change these together
            pauses = list(self.clock.pauses) + ([(self.clock.pause_start, end)] if self.clock.pause_start is not None else [])
            mute_start, mute_spans = dict(self._mute_start), {k: list(v) for k, v in self._mute_spans.items()}
        mic_first, sys_first = self.mic_first, self.sys_first  # (set by other threads, once each)
        firsts = [t for t in (mic_first, sys_first) if t]
        origin = min(firsts) if firsts else self.t0
        rel = lambda t: self.clock.rel(t, origin)  # noqa: E731
        meta = {
            "started": datetime.fromtimestamp(self.t0).isoformat(timespec="seconds"),
            "duration": round(rel(end), 1),
            "origin_delay": round(origin - self.t0, 3),  # screenshot clock starts at t0, audio at origin
            "mic_offset": round(mic_first - origin, 3) if mic_first else None,
            "system_offset": round(sys_first - origin, 3) if sys_first else None,
        }
        for track in ("mic", "system"):
            spans = mute_spans[track] + ([(mute_start[track], end)] if mute_start[track] is not None else [])  # still muted now
            meta[f"{track}_muted"] = [[round(rel(a), 1), round(rel(b), 1)] for a, b in spans]
        meta["mic_device"] = self._mic_start_device  # default input / output when recording started
        meta["system_device"] = self._system_start_device
        if pauses:  # "at": recording time of the pause, "for": seconds not recorded
            meta["paused"] = [{"at": round(rel(a), 1), "for": round(b - a, 1)} for a, b in pauses]
        events = merged_events(list(self._mic_events), list(self._system_events), rel)
        if events:
            meta["device_events"] = events
        meta["complete"] = complete  # False: the app quit or crashed before the recording was stopped
        return meta

    def stop(self) -> Meeting:
        if not self.recording:
            raise RuntimeError("not recording")
        meeting = self.meeting
        try:
            end = time.time()
            self.clock.resume(end)  # stopped while paused: the pause ends here
            self._close()
            if self.sys_first and self.clock.pauses:  # the helper kept writing (silence) through the pauses: cut them out
                try:
                    cut_wav(meeting.recordings_dir / "system.wav", self.sys_first, self.clock.pauses)
                except Exception as e:
                    self.warn(f"could not remove the paused parts of the system audio: {e}")
            self._save_meta(end, complete=True)
        finally:
            self.meeting = None
        return meeting
