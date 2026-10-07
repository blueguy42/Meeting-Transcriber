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
    w, h, stride = Quartz.CGImageGetWidth(cg), Quartz.CGImageGetHeight(cg), Quartz.CGImageGetBytesPerRow(cg)
    data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(cg))
    return Image.frombytes("RGB", (w, h), bytes(data), "raw", "BGRX", stride, 1)


def _screen_access() -> bool:
    """macOS silently hands screenshot tools just the wallpaper when Screen Recording is not allowed."""
    if sys.platform != "darwin" or permissions.screen_granted():
        return True
    permissions.request_screen()  # shows the system prompt the first time
    return False


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
        self.shot_count = 0

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
                [str(helper), str(d / RECORDINGS / "system.wav")],
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

        def cb(indata, frames, time_info, status):
            if self.mic_first is None:
                self.mic_first = time.time()
            self._q.put(bytes(indata))

        try:
            self._stream = sd.InputStream(samplerate=SR, channels=1, dtype="int16", callback=cb)
            self._stream.start()
        except Exception as e:  # no mic permission / no device
            self.warn(f"microphone unavailable: {e}")
            self._stream = None

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
        if not self.recording:
            return None
        from PIL import Image

        if not _screen_access():
            raise RuntimeError("Screen Recording permission needed: allow Meeting Transcriber in System Settings, then restart it")
        secs = time.time() - self.t0
        x, y = _mouse_position()
        img = _grab_display(x, y)
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
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
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
            meta = {
                "started": datetime.fromtimestamp(self.t0).isoformat(timespec="seconds"),
                "duration": round(time.time() - origin, 1),
                "origin_delay": round(origin - self.t0, 3),  # screenshot clock starts at t0, audio at origin
                "mic_offset": round(self.mic_first - origin, 3) if self.mic_first else None,
                "system_offset": round(self.sys_first - origin, 3) if self.sys_first else None,
            }
            (d / "meta.json").write_text(json.dumps(meta, indent=2))
        finally:
            self.dir = None
        return d
