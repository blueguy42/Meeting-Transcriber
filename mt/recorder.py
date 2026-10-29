"""Records mic (sounddevice) + system audio (sck-audio helper) + screenshots into a meeting folder.

meeting folder layout:
    meta.json  shots/shot_<seconds>.jpg  recordings/mic.wav  recordings/system.wav
"""

import json
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

from . import config, permissions
from .audio import RECORDINGS

SR = 16000


def _mouse_position() -> tuple[float, float]:
    """Cursor position in points with a top-left origin: the same grid as Quartz display bounds."""
    try:
        from AppKit import NSEvent, NSScreen

        p = NSEvent.mouseLocation()
        return p.x, NSScreen.screens()[0].frame().size.height - p.y
    except Exception:
        return 0, 0


def _grab_display(x: float, y: float):
    """The display under (x, y) at its real pixel resolution (a Retina screen is 2x its point size)."""
    import Quartz
    from PIL import Image

    _, ids, _ = Quartz.CGGetActiveDisplayList(16, None, None)
    did = next((d for d in ids if Quartz.CGRectContainsPoint(Quartz.CGDisplayBounds(d), (x, y))), Quartz.CGMainDisplayID())
    cg = Quartz.CGDisplayCreateImage(did)
    if cg is None:
        raise RuntimeError("could not capture the screen")
    return cg_to_pil(cg)


def cg_to_pil(cg):
    """A Quartz picture (CGImage) as a Pillow image."""
    import Quartz
    from PIL import Image

    w, h, stride = Quartz.CGImageGetWidth(cg), Quartz.CGImageGetHeight(cg), Quartz.CGImageGetBytesPerRow(cg)
    data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(cg))
    return Image.frombytes("RGB", (w, h), bytes(data), "raw", "BGRX", stride, 1)


def _screen_access() -> bool:
    """macOS silently hands screenshot tools just the wallpaper when Screen Recording is not allowed."""
    if sys.platform != "darwin" or permissions.screen_granted():
        return True
    permissions.request_screen()  # shows the system prompt the first time
    return False


def _coreaudio():
    import ctypes

    return ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")


def _audio_prop(ca, dev: int, selector: int, out, scope: int = 0x676C6F62) -> bool:
    import ctypes

    class Addr(ctypes.Structure):
        _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("element", ctypes.c_uint32)]

    addr = Addr(selector, scope, 0)  # scope 'glob' = global, element = main
    size = ctypes.c_uint32(ctypes.sizeof(out))
    return ca.AudioObjectGetPropertyData(dev, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(out)) == 0


def _default_device_id(selector: int) -> int | None:
    try:
        import ctypes

        dev = ctypes.c_uint32(0)
        ok = _audio_prop(_coreaudio(), 1, selector, dev)  # 1 = the system object
        return dev.value if ok and dev.value else None
    except Exception:
        return None


def _default_input_id() -> int | None:
    """CoreAudio's current default input device id (cheap; PortAudio's own device list is a stale snapshot)."""
    return _default_device_id(0x64496E20)  # 'dIn '


def _default_output_id() -> int | None:
    """CoreAudio's current default output device id (what system audio is played through)."""
    return _default_device_id(0x644F7574)  # 'dOut'


def _device_name(dev: int | None) -> str | None:
    """Human-readable name of a CoreAudio device id."""
    if not dev:
        return None
    try:
        import ctypes

        ref = ctypes.c_void_p()
        if not _audio_prop(_coreaudio(), dev, 0x6C6E616D, ref):  # 'lnam' (kAudioObjectPropertyName)
            return None
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        buf = ctypes.create_string_buffer(256)
        ok = cf.CFStringGetCString(ref, buf, len(buf), 0x08000100)  # UTF-8
        cf.CFRelease(ref)
        return buf.value.decode() if ok else None
    except Exception:
        return None


def _make_resampler(src: int, dst: int):
    """Streaming linear resampler for mono int16 chunks (speech only, so no fancy filter)."""
    import numpy as np

    step = src / dst
    state = {"tail": np.zeros(0, dtype=np.float32), "pos": 0.0}

    def run(chunk):
        x = np.concatenate([state["tail"], chunk.astype(np.float32)])
        idx = np.arange(state["pos"], len(x) - 1, step)
        out = np.interp(idx, np.arange(len(x)), x)
        used = int(idx[-1] // 1) if len(idx) else 0
        next_pos = (idx[-1] + step if len(idx) else state["pos"]) - used
        state["tail"], state["pos"] = x[used:], next_pos
        return out.astype(np.int16)

    return run


def cut_wav(path: Path, first: float, spans: list) -> None:
    """Remove the paused stretches from a recording that kept running through them (the system audio helper cannot be
    told to stop writing). `first` is the wall-clock time of its first sample, `spans` the (start, end) wall-clock pauses."""
    with wave.open(str(path), "rb") as r:
        params, rate, total = r.getparams(), r.getframerate(), r.getnframes()
        cuts: list[list[int]] = []
        for a, b in sorted(spans):
            x, y = max(int(round((a - first) * rate)), 0), min(int(round((b - first) * rate)), total)
            if y > x:
                if cuts and x <= cuts[-1][1]:
                    cuts[-1][1] = max(cuts[-1][1], y)
                else:
                    cuts.append([x, y])
        if not cuts:
            return
        tmp = path.with_name(path.stem + ".cut.wav")
        with wave.open(str(tmp), "wb") as w:
            w.setparams(params)
            pos = 0
            for x, y in [*cuts, [total, total]]:
                r.setpos(pos)
                left = x - pos
                while left > 0:
                    chunk = r.readframes(min(left, 1 << 16))
                    if not chunk:
                        break
                    w.writeframes(chunk)
                    left -= len(chunk) // (params.sampwidth * params.nchannels)
                pos = y
    tmp.replace(path)


class Recorder:
    def __init__(self, cfg: dict, on_warning=print):
        self.cfg = cfg
        self.warn = on_warning
        self.dir: Path | None = None
        self.t0 = 0.0
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
        self.paused = False  # a real pause: nothing is recorded, and the time does not count
        self._pauses: list[tuple[float, float]] = []  # wall-clock spans of finished pauses
        self._pause_start: float | None = None

    @property
    def recording(self) -> bool:
        return self.dir is not None

    def start(self) -> Path:
        if self.recording:
            raise RuntimeError("already recording")
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        d = self.cfg["meetings_dir"] / stamp
        n = 2
        while d.exists():
            d = self.cfg["meetings_dir"] / f"{stamp}-{n}"
            n += 1
        (d / "shots").mkdir(parents=True)
        (d / RECORDINGS).mkdir()
        self.dir = d
        self.t0 = time.time()
        self.mic_first = self.sys_first = None
        self.shot_count = 0
        # mutes are set any time and carry over: one that is on at the start counts from the start
        self._mute_start = {"mic": self.t0 if self.mic_muted else None, "system": self.t0 if self.sys_muted else None}
        self._mute_spans = {"mic": [], "system": []}
        self.paused, self._pauses, self._pause_start = False, [], None
        try:
            self._start_capture(d)
        except Exception:
            # Don't leave a half-started recording (open files, a running helper) behind.
            self._close()
            self.dir = None
            shutil.rmtree(d, ignore_errors=True)
            raise
        return d

    def _start_capture(self, d: Path) -> None:
        import sounddevice as sd

        # system audio via ScreenCaptureKit helper
        helper = config.helper_path()
        if helper is None:
            self.warn("sck-audio helper not found - recording mic only")
        else:
            self._proc = subprocess.Popen(
                [str(helper), str(d / RECORDINGS / "system.wav"), *(["--muted"] if self.sys_muted else [])],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            threading.Thread(target=self._read_helper, daemon=True).start()

        # microphone
        self._wav = wave.open(str(d / RECORDINGS / "mic.wav"), "wb")
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
        self._mic_start_device = _device_name(_default_input_id())
        self._system_start_device = _device_name(_default_output_id())
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
            pcm = indata[:, 0] if resampler else indata  # fallback path may deliver several channels
            if resampler:
                pcm = resampler(pcm)
            if self._mic_resync:  # reopened after a device change: keep the file aligned with the clock
                self._mic_resync = False
                gap = int((self._active(now) - self._active(self.mic_first)) * SR) - self._mic_written - len(pcm)
                if gap > 0:
                    self._q.put(bytes(gap * 2))
                    self._mic_written += gap
            self._mic_written += len(pcm)
            # muted: keep the file running (so it stays aligned with the clock) but write silence
            self._q.put(bytes(pcm.nbytes) if self.mic_muted else bytes(pcm))

        self._mic_dev = _default_input_id()
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
                resampler = _make_resampler(rate, SR)
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
                cur = _default_input_id()
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
        cur = _default_output_id()
        while not self._mic_stop.wait(1.0):
            new = _default_output_id()
            if new is not None and new != cur:
                cur = new
                name = _device_name(new)
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
            now = time.time()
            if muted and self._mute_start[track] is None:
                self._mute_start[track] = now
            elif not muted and self._mute_start[track] is not None:
                self._mute_spans[track].append((self._mute_start[track], now))
                self._mute_start[track] = None
        return True

    # ---- pause: nothing is recorded, and the paused time is not counted (see _active)
    def _active(self, t: float) -> float:
        """The wall clock at t minus the time spent paused before it: the recording's own clock."""
        spans = list(self._pauses)
        if self._pause_start is not None:
            spans.append((self._pause_start, float("inf")))
        return t - sum(max(0.0, min(t, b) - a) for a, b in spans)

    def elapsed(self) -> float:
        """Seconds of recording so far, not counting pauses (what the timer, notes and screenshots use)."""
        return max(self._active(time.time()) - self.t0, 0.0) if self.recording else 0.0

    def pause(self) -> bool:
        """Stop recording until resume(). The microphone stops writing; the system audio helper writes silence that is cut
        out when the recording ends. False if it can't be done."""
        with self._mute_lock:
            if not self.recording or self.paused:
                return False
            if self._proc is not None and not self._send_helper("MUTE"):
                return False
            self._pause_start = time.time()
            self.paused = True
        return True

    def resume(self) -> bool:
        with self._mute_lock:
            if not self.recording or not self.paused:
                return False
            now = time.time()
            if self._proc is not None:
                self._send_helper("MUTE" if self.sys_muted else "UNMUTE")  # back to the mute you had set
            self._pauses.append((self._pause_start, now))
            self._pause_start, self.paused = None, False
            self._mic_resync = True  # the clock moved on without the file: no padding for the paused time
        return True

    def cancel(self) -> Path | None:
        """Stop and throw the recording away: the meeting folder is deleted. Returns the folder that was deleted."""
        if not self.recording:
            return None
        d = self.dir
        try:
            self._close()
        finally:
            self.dir, self.paused, self._pause_start = None, False, None
        root = Path(self.cfg["meetings_dir"]).resolve()
        if d.resolve().parent == root:  # only ever a meeting folder inside the meetings folder
            shutil.rmtree(d, ignore_errors=True)
        return d

    def _rel(self, t: float, origin: float) -> float:
        return max(self._active(t) - self._active(origin), 0.0)

    def _mute_meta(self, origin: float, end: float) -> dict:
        """Mute spans as seconds from the recording origin: the same clock as the transcript timestamps."""
        out = {}
        for track in ("mic", "system"):
            spans = list(self._mute_spans[track])
            if self._mute_start[track] is not None:  # still muted when recording stopped
                spans.append((self._mute_start[track], end))
            out[f"{track}_muted"] = [[round(self._rel(a, origin), 1), round(self._rel(b, origin), 1)] for a, b in spans]
        return out

    def _read_helper(self):
        for line in self._proc.stdout:
            line = line.strip()
            if line.startswith("FIRST "):
                self.sys_first = float(line.split()[1])
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
        x, y = _mouse_position()
        return self.save_shot(_grab_display(x, y), secs)

    @staticmethod
    def require_screen_access() -> None:
        if not _screen_access():
            raise RuntimeError("Screen Recording permission needed: allow Meeting Transcriber in System Settings, then restart it")

    def save_shot(self, img, secs: float) -> Path | None:
        """Save a picture (the whole screen or a chosen area) as shots/shot_<seconds>.jpg, scaled by screenshot_scale.
        `secs` is how far into the recording the picture was taken: the moment the screen froze."""
        if not self.recording:
            return None
        from PIL import Image

        scale = self.cfg["screenshot_scale"]
        if scale < 100:
            img = img.resize((round(img.width * scale / 100), round(img.height * scale / 100)), Image.LANCZOS)
        n = int(secs)
        while (self.dir / "shots" / f"shot_{n:05d}.jpg").exists():  # two shots in one second
            n += 1
        path = self.dir / "shots" / f"shot_{n:05d}.jpg"
        img.save(path, quality=80)
        self.shot_count += 1
        return path

    def _close(self) -> None:
        """Stop everything that may be running. Safe to call twice and after a partial start."""
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

    def _device_events(self, origin: float) -> list[dict]:
        """Mic and system device changes in one list, in seconds from the recording origin. A mic and a system
        switch to the same device within 2 s of each other (a headset plugged in moves both) become one "mic+system" entry."""
        evs = [{**e, "track": "mic"} for e in self._mic_events] + [{**e, "track": "system"} for e in self._system_events]
        evs.sort(key=lambda e: e["t"])
        out: list[dict] = []
        for e in evs:
            prev = out[-1] if out else None
            if (
                prev
                and {prev["track"], e["track"]} == {"mic", "system"}
                and prev["event"] == e["event"] == "switched"
                and prev.get("device") == e.get("device")
                and e["t"] - prev["t"] <= 2
            ):
                out[-1] = {**prev, "track": "mic+system"}
            else:
                out.append(e)
        return [
            {"at": round(self._rel(e["t"], origin), 1), **{k: v for k, v in e.items() if k != "t" and v is not None}}
            for e in out
        ]

    def stop(self) -> Path:
        if not self.recording:
            raise RuntimeError("not recording")
        d = self.dir
        try:
            end = time.time()
            if self.paused:  # stopped while paused: the pause ends here
                self._pauses.append((self._pause_start, end))
                self._pause_start, self.paused = None, False
            self._close()
            firsts = [t for t in (self.mic_first, self.sys_first) if t]
            origin = min(firsts) if firsts else self.t0
            if self.sys_first and self._pauses:  # the helper kept writing (silence) through the pauses: cut them out
                try:
                    cut_wav(d / RECORDINGS / "system.wav", self.sys_first, self._pauses)
                except Exception as e:
                    self.warn(f"could not remove the paused parts of the system audio: {e}")
            meta = {
                "started": datetime.fromtimestamp(self.t0).isoformat(timespec="seconds"),
                "duration": round(self._rel(end, origin), 1),
                "origin_delay": round(origin - self.t0, 3),  # screenshot clock starts at t0, audio at origin
                "mic_offset": round(self.mic_first - origin, 3) if self.mic_first else None,
                "system_offset": round(self.sys_first - origin, 3) if self.sys_first else None,
                **self._mute_meta(origin, end),
            }
            meta["mic_device"] = self._mic_start_device  # default input / output when recording started
            meta["system_device"] = self._system_start_device
            if self._pauses:  # "at": recording time of the pause, "for": seconds not recorded
                meta["paused"] = [{"at": round(self._rel(a, origin), 1), "for": round(b - a, 1)} for a, b in self._pauses]
            events = self._device_events(origin)
            if events:
                meta["device_events"] = events
            (d / "meta.json").write_text(json.dumps(meta, indent=2))
        finally:
            self.dir = None
        return d
