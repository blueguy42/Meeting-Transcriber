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


def _default_input_id() -> int | None:
    """CoreAudio's current default input device id (cheap; PortAudio's own device list is a stale snapshot)."""
    try:
        import ctypes

        ca = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")

        class Addr(ctypes.Structure):
            _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("element", ctypes.c_uint32)]

        addr = Addr(0x64496E20, 0x676C6F62, 0)  # 'dIn ' (default input), 'glob', main element
        dev, size = ctypes.c_uint32(0), ctypes.c_uint32(4)
        err = ca.AudioObjectGetPropertyData(1, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(dev))
        return dev.value if err == 0 and dev.value else None
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
        self.shot_count = 0
        self.mic_muted = False
        self.sys_muted = False
        self._mute_start: dict[str, float | None] = {"mic": None, "system": None}  # wall clock, while muted
        self._mute_spans: dict[str, list[tuple[float, float]]] = {"mic": [], "system": []}
        self._mute_lock = threading.Lock()

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
            if self.mic_first is None:
                self.mic_first = now
            pcm = indata[:, 0] if resampler else indata  # fallback path may deliver several channels
            if resampler:
                pcm = resampler(pcm)
            if self._mic_resync:  # reopened after a device change: keep the file aligned with the clock
                self._mic_resync = False
                gap = int((now - self.mic_first) * SR) - self._mic_written - len(pcm)
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
                    self._mic_events.append({"t": time.time(), "event": reason})  # changed / stalled
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
                    self._mic_events.append({"t": time.time(), "event": "reopened", "device": name})
                else:
                    self._mic_lost = True

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
                if self.recording and not self._send_helper("MUTE" if muted else "UNMUTE"):
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

    def _mute_meta(self, origin: float, end: float) -> dict:
        """Mute spans as seconds from the recording origin: the same clock as the transcript timestamps."""
        out = {}
        for track in ("mic", "system"):
            spans = list(self._mute_spans[track])
            if self._mute_start[track] is not None:  # still muted when recording stopped
                spans.append((self._mute_start[track], end))
            out[f"{track}_muted"] = [[round(max(a - origin, 0), 1), round(max(b - origin, 0), 1)] for a, b in spans]
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

    def stop(self) -> Path:
        if not self.recording:
            raise RuntimeError("not recording")
        d = self.dir
        try:
            self._close()
            firsts = [t for t in (self.mic_first, self.sys_first) if t]
            origin = min(firsts) if firsts else self.t0
            end = time.time()
            meta = {
                "started": datetime.fromtimestamp(self.t0).isoformat(timespec="seconds"),
                "duration": round(end - origin, 1),
                "origin_delay": round(origin - self.t0, 3),  # screenshot clock starts at t0, audio at origin
                "mic_offset": round(self.mic_first - origin, 3) if self.mic_first else None,
                "system_offset": round(self.sys_first - origin, 3) if self.sys_first else None,
                **self._mute_meta(origin, end),
            }
            if self._mic_events:  # seconds from the recording origin, like the mute spans
                meta["mic_device_events"] = [
                    {**{k: v for k, v in e.items() if k != "t"}, "at": round(max(e["t"] - origin, 0), 1)}
                    for e in self._mic_events
                ]
            (d / "meta.json").write_text(json.dumps(meta, indent=2))
        finally:
            self.dir = None
        return d
