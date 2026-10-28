"""Logging for the app: one timestamped, levelled line per event, in ~/Library/Logs/MeetingTranscriber.log.

The launcher points the app's stdout/stderr at that file. setup() makes everything written there readable:
- our own messages go through the `logging` module (`log = logging.getLogger("mt.<module>")`);
- anything native code or a library writes to stderr (PortAudio, warnings, tracebacks) is read back through a pipe,
  stripped of known noise (progress bars, harmless OS warnings) and logged as `native`.
"""

import logging
import os
import re
import sys
import threading

# Set before any library is imported: they read these when they load.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")  # the "Fetching 4 files" bar, one per model load
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")  # plain HTTP downloads of the Whisper model resume reliably; xet ones stall silently
# The resource tracker is a separate process that warns about the semaphores of a model-loading pool at exit.
os.environ["PYTHONWARNINGS"] = ",".join(filter(None, [os.environ.get("PYTHONWARNINGS"), "ignore:resource_tracker:UserWarning"]))

# stderr lines that carry no information for the user (matched at the start of the line, after stripping)
_NOISE = re.compile(
    r"(Python\(\d+\) MallocStackLogging: can't turn off malloc stack logging"
    r"|Fetching \d+ files:"
    r"|\s+warnings\.warn\($"
    r"|.*resource_tracker\.py:\d+: UserWarning)"
)
# PortAudio prints "||PaMacCore (AUHAL)|| Error on line 1322: err='-10851', msg=..." for every device it cannot open
_PORTAUDIO = re.compile(r"\|\|PaMacCore \(AUHAL\)\|\| (Warning|Error) on line \d+: (.*)")

_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _pump(read_fd: int, logger: logging.Logger) -> None:
    """Turn what is written to the real stderr into log records (runs for the life of the app)."""
    tb: list[str] = []  # a Python traceback arrives as many lines: keep it as one record
    with os.fdopen(read_fd, "r", encoding="utf-8", errors="replace", newline="") as f:
        for raw in f:
            for line in re.split(r"[\r\n]+", raw):  # progress bars redraw with \r
                if not line.strip() or _NOISE.search(line):
                    continue
                if tb or line.startswith("Traceback"):
                    tb.append(line.rstrip())
                    # the unindented line after the frames ("ValueError: ...") ends it, unless a chained traceback follows
                    if not line.startswith((" ", "\t", "Traceback", "During handling", "The above exception")):
                        logger.error("\n".join(tb))
                        tb.clear()
                    continue
                m = _PORTAUDIO.search(line)
                if m:
                    logger.log(logging.ERROR if m.group(1) == "Error" else logging.DEBUG, "PortAudio: %s", m.group(2))
                else:
                    logger.warning(line.rstrip())


_done = False


def setup(level: int = logging.INFO) -> None:
    global _done
    if _done:
        return
    _done = True
    real = os.fdopen(os.dup(2), "w", encoding="utf-8", buffering=1)  # where our own records go: the file the launcher opened
    handler = logging.StreamHandler(real)
    handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)

    r, w = os.pipe()
    os.dup2(w, 2)  # native code and child processes now write into the pipe
    os.close(w)
    sys.stderr = os.fdopen(2, "w", encoding="utf-8", buffering=1, closefd=False)
    threading.Thread(target=_pump, args=(r, logging.getLogger("native")), name="stderr", daemon=True).start()

    logging.captureWarnings(True)  # Python warnings become records instead of bare stderr text
    for noisy in ("httpx", "httpcore", "urllib3", "huggingface_hub", "filelock", "numba"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    sys.excepthook = lambda t, v, tb: logging.getLogger("mt").critical("Uncaught exception", exc_info=(t, v, tb))
    threading.excepthook = lambda a: logging.getLogger("mt").critical(
        "Uncaught exception in thread %s", a.thread.name if a.thread else "?", exc_info=(a.exc_type, a.exc_value, a.exc_traceback)
    )
    logging.getLogger("mt").info("=" * 20 + " Meeting Transcriber started (pid %d, python %s) " + "=" * 20, os.getpid(), sys.version.split()[0])


class Throttle:
    """Pass through only the first message and then every `step` percent of a repeating "<what>… N%" progress line."""

    def __init__(self, step: int = 25):
        self.step, self.last = step, {}

    def __call__(self, text: str) -> bool:
        m = re.fullmatch(r"(.*?)\s*(\d+)%", text)
        if not m:
            return True
        key, pct = m.group(1), int(m.group(2))
        bucket = pct // self.step
        if pct in (0, 100) or self.last.get(key) != bucket:
            self.last[key] = bucket
            return True
        return False
