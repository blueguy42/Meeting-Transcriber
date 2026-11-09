"""Meeting notes: a plain text file, notes.txt, in the meeting folder.

You type them in the notes panel while recording (see bar/notes.py / web/notes/). The summary reads them
together with the transcript (summarize.py). Times you insert look like [12:34] or [1:05:42]: how far into
the recording the note was made, so a note can be matched to the transcript.
"""

import os
import re
from .meeting import Meeting

MAX_CHARS = 20_000  # more than anyone types in a meeting; also keeps the summary request a sane size
TIME = re.compile(r"\[(?:\d+:)?\d{1,2}:\d{2}\]")


def read_notes(meeting: Meeting) -> str:
    try:
        return meeting.notes_file.read_text(encoding="utf-8")[:MAX_CHARS]
    except OSError:
        return ""


def save_notes(meeting: Meeting, text: str) -> None:
    """Write atomically. Empty notes remove the file, so a meeting with no notes has no notes.txt."""
    path = meeting.notes_file
    text = text[:MAX_CHARS]
    if not text.strip():
        path.unlink(missing_ok=True)
        return
    tmp = path.with_suffix(".txt.tmp")
    tmp.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    os.replace(tmp, path)


def line_count(text: str) -> int:
    """Lines with something on them (what the bar shows as 'Notes · 3 lines')."""
    return sum(1 for line in text.splitlines() if TIME.sub("", line).strip())


def stamp(seconds: float) -> str:
    """Seconds into the recording -> '[12:34]' (or '[1:05:42]' from the first hour on)."""
    s = max(0, int(seconds))
    h, rest = divmod(s, 3600)
    return f"[{h}:{rest // 60:02d}:{rest % 60:02d}]" if h else f"[{rest // 60:02d}:{rest % 60:02d}]"
