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


def parse(msg: str, labels: list[str]) -> dict | None:
    """-> {"kind": "transcribing", "label": ..., "fraction": 0..1 | None}, {"kind": "summarizing"}, {"kind": "docx"} or None."""
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
