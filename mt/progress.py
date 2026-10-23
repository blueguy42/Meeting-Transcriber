"""The progress messages the pipeline reports, in one place, so the floating bar can read them back.

transcribe.py and pipeline.py build their messages with these helpers; jobs.py turns a message into an
event with parse(). A message nobody here knows is simply shown as text (parse returns None).
"""

import re

SUMMARIZING = "Summarizing with Claude…"
DOCX = "Creating Word document…"


def transcribing(label: str, fraction: float | None = None, bursts: int | None = None) -> str:
    if fraction is None:
        return f"Transcribing {label} ({bursts} speech bursts)…"
    return f"Transcribing {label}… {fraction:.0%}"


def reading_documents(done: int, total: int) -> str:
    return f"Reading documents… {done} of {total}"


def diarizing(percent: int) -> str:
    return f"Telling speakers apart… {percent}%"


MATCHING_VOICES = "Telling speakers apart… matching voices"


def downloading_model(n: int) -> str:
    return f"Downloading speaker model {n} of 2…"


def speakers_found(label: str, count: int) -> str:
    return f"Found {count} {label} speaker(s)" if count > 1 else f"{label}: one speaker"


SPEAKERS_FAILED = "Speakers were not told apart: "  # followed by the reason


def parse(msg: str, labels: list[str]) -> dict | None:
    """-> {"kind": "transcribing", "label": ..., "fraction": 0..1 | None}, {"kind": "summarizing"}, {"kind": "docx"},
    {"kind": "diarizing", "fraction": 0..1 | None, "note": str}, {"kind": "diarized", "failed": bool} or None."""
    m = re.fullmatch(r"Reading documents… (\d+) of (\d+)", msg)
    if m:
        return {"kind": "readdocs", "done": int(m.group(1)), "total": int(m.group(2))}
    if msg.startswith(SPEAKERS_FAILED):
        return {"kind": "diarized", "failed": True}
    m = re.fullmatch(r"Telling speakers apart… (\d+)%", msg)
    if m:
        return {"kind": "diarizing", "fraction": int(m.group(1)) / 100, "note": ""}
    if msg == MATCHING_VOICES:
        return {"kind": "diarizing", "fraction": 0.99, "note": "Matching voices"}
    m = re.fullmatch(r"Downloading speaker model (\d) of 2…", msg)
    if m:
        return {"kind": "diarizing", "fraction": 0.0, "note": f"Downloading model {m.group(1)} of 2"}
    if len(labels) > 1 and (re.fullmatch(rf"Found \d+ {re.escape(labels[1])} speaker\(s\)", msg) or msg == f"{labels[1]}: one speaker"):
        return {"kind": "diarized", "failed": False}
    if msg == SUMMARIZING:
        return {"kind": "summarizing"}
    if msg == DOCX:
        return {"kind": "docx"}
    for label in labels:
        m = re.fullmatch(rf"Transcribing {re.escape(label)}… (\d+)%", msg)
        if m:
            return {"kind": "transcribing", "label": label, "fraction": int(m.group(1)) / 100}
        if re.fullmatch(rf"Transcribing {re.escape(label)} \(\d+ speech bursts\)…", msg):
            return {"kind": "transcribing", "label": label, "fraction": None}
    return None
