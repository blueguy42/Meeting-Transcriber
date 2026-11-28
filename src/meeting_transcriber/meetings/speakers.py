"""Who the other people in a meeting are: their names, kept in meta.json, and the transcript lines that carry them.

The transcript writes the speakers the voice separation found as "Others 1", "Others 2"... (the *raw labels*, numbered by how
much they talk). A name for one is kept in meta.json under "speakers":

    "speakers": {"Others 1": {"name": "Budi", "by": "claude", "evidence": "Budi here, I'll cover the budget", "at": "00:06"},
                 "Others 3": {"name": "Rina", "by": "user"}}

and transcript.md is written with the names. "by" says who chose it: Claude (from the transcript) or the user, whose choice a later
run never changes. A raw label with no entry is simply not named yet. meta.json also remembers "speakers_checked" (Claude looked) and
"summary_stale" (the names changed after the summary was written).
"""

import os
import re
from pathlib import Path

from ..settings.model import Settings
from .meeting import Meeting

LINE = re.compile(r"^\[(\d+):(\d\d):(\d\d)\] ([^:\n]+): (.*)$")
SAMPLE_SECONDS = 6.0  # how much of a voice one sample plays
SAMPLES = 3  # how many samples a speaker offers
MAX_NAME = 40


def _raw_pattern(cfg: Settings) -> re.Pattern:
    return re.compile(rf"^{re.escape(cfg.others_label)}( \d+)?$")


def parse_line(line: str) -> tuple[float, str, str] | None:
    """'[00:01:20] Others 3: Can I add…' -> (80.0, 'Others 3', 'Can I add…'), or None for any other line."""
    m = LINE.match(line)
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)), m.group(4), m.group(5)


def lines(meeting: Meeting) -> list[tuple[float, str, str]]:
    """The spoken lines of the transcript: (seconds, speaker, text)."""
    if not meeting.has_transcript:
        return []
    return [p for p in map(parse_line, meeting.transcript_file.read_text().splitlines()) if p]


def saved(meeting: Meeting) -> dict:
    """The names in meta.json: {raw label: {"name", "by", ...}}."""
    try:
        return dict(meeting.read_meta().get("speakers") or {})
    except (OSError, ValueError):
        return {}


def raw_labels(meeting: Meeting, cfg: Settings) -> list[str]:
    """The other people's raw labels in this transcript ("Others 1"...), in order, named or not."""
    names = saved(meeting)
    seen = {s for _, s, _ in lines(meeting)}
    pattern = _raw_pattern(cfg)
    out = [s for s in seen if pattern.match(s)] + [raw for raw, e in names.items() if e.get("name") in seen]
    return sorted(dict.fromkeys(out), key=lambda s: (int(re.search(r"\d+$", s).group()) if re.search(r"\d+$", s) else 0, s))


def shown_name(names: dict, raw: str) -> str:
    return (names.get(raw) or {}).get("name") or raw


def _rewrite(meeting: Meeting, change: dict[str, str]) -> None:
    """Rename speakers in transcript.md ({shown name: new shown name}), keeping every timestamp. The file is replaced in one step."""
    path = meeting.transcript_file
    out = []
    for line in path.read_text().splitlines():
        p = LINE.match(line)
        if p and p.group(4) in change:
            line = f"[{p.group(1)}:{p.group(2)}:{p.group(3)}] {change[p.group(4)]}: {p.group(5)}"
        out.append(line)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(out) + "\n")
    os.replace(tmp, path)


def clean_name(raw: str, cfg: Settings) -> str:
    name = re.sub(r"\s+", " ", raw or "").strip().strip(":")
    if len(name) > MAX_NAME:
        raise ValueError(f"A name can be at most {MAX_NAME} characters.")
    if name and name.lower() == cfg.my_label.lower():
        raise ValueError(f"“{name}” is the label of your own microphone. Choose another name.")
    return name


def set_names(meeting: Meeting, cfg: Settings, wanted: dict[str, dict]) -> None:
    """Give raw labels their names: {raw label: {"name", "by", "evidence"?, "at"?}}. An empty name takes the label's name away again.
    Names a user chose are not replaced by Claude's. Rewrites transcript.md, and notes that the summary is out of date."""
    meta = meeting.read_meta()
    names = dict(meta.get("speakers") or {})
    before = {raw: shown_name(names, raw) for raw in set(wanted) | set(names)}
    for raw, entry in wanted.items():
        name = clean_name(entry.get("name", ""), cfg)
        if entry.get("by") == "claude" and (names.get(raw) or {}).get("by") == "user":
            continue
        if name:
            names[raw] = {**{k: v for k, v in entry.items() if k != "name"}, "name": name}
        else:
            names.pop(raw, None)
    change = {before[raw]: shown_name(names, raw) for raw in before if before[raw] != shown_name(names, raw)}
    if change and meeting.has_transcript:
        _rewrite(meeting, change)
    meta["speakers"] = names
    if change and meeting.summaries():
        meta["summary_stale"] = True
    meeting.write_meta(meta)


def rename(meeting: Meeting, cfg: Settings, raw: str, name: str) -> None:
    """The user's choice for one speaker ('' = back to the raw label)."""
    set_names(meeting, cfg, {raw: {"name": name, "by": "user"}})


def mark_checked(meeting: Meeting, people: list[str] | None = None) -> None:
    """Claude has read this transcript. `people` are the names of people it saw mentioned: they are suggested when a speaker is named by hand."""
    meta = meeting.read_meta()
    meta["speakers_checked"] = True
    if people is not None:
        meta["people"] = people
    meeting.write_meta(meta)


def mark_summary_current(meeting: Meeting) -> None:
    """A summary was just written from the current transcript."""
    try:
        meta = meeting.read_meta()
    except (OSError, ValueError):
        return
    if meta.pop("summary_stale", None):
        meeting.write_meta(meta)


def apply_saved(meeting: Meeting, cfg: Settings) -> None:
    """After a transcript was written afresh (with raw labels): put back the names the user chose, for the speakers still there.
    Claude's names are dropped (it looks again); so is anything that no longer fits, e.g. fewer speakers than before."""
    try:
        meta = meeting.read_meta()
    except (OSError, ValueError):
        return
    old = meta.get("speakers") or {}
    present = {s for _, s, _ in lines(meeting)}
    keep = {raw: e for raw, e in old.items() if e.get("by") == "user" and raw in present}
    if old != keep or meta.get("speakers_checked"):
        meta["speakers"] = keep
        meta.pop("speakers_checked", None)
        meeting.write_meta(meta)
    change = {raw: e["name"] for raw, e in keep.items()}
    if change and meeting.has_transcript:
        _rewrite(meeting, change)


def roster(meeting: Meeting, cfg: Settings) -> list[str]:
    """Names to suggest when someone is named by hand: the people Claude saw mentioned in the transcript, and the names already used here.
    (Only people: the words of the meeting's title or the terms in Settings are not names.)"""
    try:
        seen = list(meeting.read_meta().get("people") or [])
    except (OSError, ValueError):
        seen = []
    used = [e["name"] for e in saved(meeting).values() if e.get("name")]
    return list(dict.fromkeys(w for w in [*used, *seen] if w and w.lower() != cfg.my_label.lower()))


# ---- the rows of the processing panel
def samples(meeting: Meeting, cfg: Settings, raw: str, limit: int = SAMPLES) -> list[dict]:
    """A few places to hear a speaker: the longer lines, where nobody else starts talking within the sample.
    [{"start", "end", "text", "at"}], times in recording seconds (the system audio track starts a little later: see track_time)."""
    names = saved(meeting)
    who = shown_name(names, raw)
    spoken = lines(meeting)
    found = []
    for i, (t, speaker, text) in enumerate(spoken):
        if speaker != who:
            continue
        nxt = next((u for u, s, _ in spoken[i + 1:] if s != who), None)
        room = SAMPLE_SECONDS if nxt is None else min(SAMPLE_SECONDS, nxt - t)
        found.append((len(text) if room >= 2.0 else 0, t, min(room, SAMPLE_SECONDS), text))
    found.sort(key=lambda x: (-x[0], x[1]))
    out = []
    for score, t, length, text in found[:limit]:
        if score == 0 and out:
            break
        text = text.strip()
        out.append({"start": t, "end": t + max(length, 1.0), "text": text if len(text) <= 90 else text[:87].rstrip() + "…",
                    "at": f"{int(t) // 60:02d}:{int(t) % 60:02d}"})
    return out


def track_time(meeting: Meeting, seconds: float) -> float:
    """Recording seconds -> seconds into the system audio track."""
    try:
        return max(seconds - float(meeting.read_meta().get("system_offset", 0.0)), 0.0)
    except (OSError, ValueError):
        return max(seconds, 0.0)


def audio_file(meeting: Meeting) -> Path | None:
    """The system audio track as it is kept (WAV, or compressed after transcribing), if it is still there."""
    for ext in ("wav", "m4a", "flac"):
        p = meeting.audio_dir / f"system.{ext}"
        if p.exists():
            return p
    return None


def rows(meeting: Meeting, cfg: Settings) -> list[dict]:
    """What the panel shows for a finished meeting: one row per other speaker (empty when the transcript has none)."""
    names = saved(meeting)
    spoken = lines(meeting)
    out = []
    for raw in raw_labels(meeting, cfg):
        shown = shown_name(names, raw)
        entry = names.get(raw) or {}
        out.append({"label": raw, "name": entry.get("name"), "by": entry.get("by"), "evidence": entry.get("evidence"),
                    "lines": sum(1 for _, s, _ in spoken if s == shown), "samples": samples(meeting, cfg, raw)})
    return out
