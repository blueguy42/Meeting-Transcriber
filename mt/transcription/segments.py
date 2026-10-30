"""Working with transcript segments: dropping echo, merging, naming speakers, and the lines that mark mutes and pauses."""

import difflib
import re
from pathlib import Path


TITLE_STOP = {"dan", "yang", "untuk", "dengan", "and", "the", "for", "with", "of", "to", "in"}
ECHO_WINDOW_S = 3.0  # how far around a mic segment to look for the same words on the system track
ECHO_COVER = 0.7  # share of a mic segment's content words that the system track said around then
MERGE_GAP_S = 1.0
MERGE_MAX_CHARS = 400
MERGE_MAX_S = 30.0
MIN_TURN_S = 0.5  # shorter diarization turns are too unreliable to vote on who spoke


def _content_words(text: str) -> list[str]:
    return [w for w in re.findall(r"\w+", text.lower()) if len(w) > 3]  # short words (yang, kan, ya) match by chance


def drop_echo(mine: list[dict], others: list[dict]) -> list[dict]:
    """Without headphones the mic hears the speakers; drop mic segments that duplicate system audio.
    The bleed is quieter and delayed, so Whisper words it differently: compare against everything the
    system track said around then, not one segment against one segment. No system speech nearby = never an echo."""
    kept = []
    for m in mine:
        near = [o for o in others if o["start"] < m["end"] + ECHO_WINDOW_S and o["end"] > m["start"] - ECHO_WINDOW_S]
        if not near:
            kept.append(m)
            continue
        text = m["text"].lower()
        dup = any(difflib.SequenceMatcher(None, text, o["text"].lower()).ratio() > 0.6 for o in near)
        words = _content_words(text)
        if not dup and len(words) >= 3:
            heard = {w for o in near for w in _content_words(o["text"])}
            pool = list(heard)  # Whisper spells the same bleed differently each time (sebenernya / sebenarnya)
            dup = sum(w in heard or bool(difflib.get_close_matches(w, pool, 1, 0.8)) for w in words) / len(words) >= ECHO_COVER
        if not dup:
            kept.append(m)
    return kept


def merge_segments(segs: list[dict]) -> list[dict]:
    """Join a speaker's consecutive fragments ("Gimana?" / "Sehat?" / "Gais?") into one line.
    `segs` must be sorted by start; a line from another speaker in between keeps them apart."""
    out: list[dict] = []
    for s in segs:
        last = out[-1] if out else None
        if (
            last
            and last["speaker"] == s["speaker"]
            and s["start"] - last["end"] < MERGE_GAP_S
            and len(last["text"]) + len(s["text"]) < MERGE_MAX_CHARS
            and s["end"] - last["start"] < MERGE_MAX_S
        ):
            last["end"] = max(last["end"], s["end"])
            last["text"] += " " + s["text"]
        else:
            out.append(dict(s))
    return out


def title_terms(meeting_dir: Path) -> list[str]:
    """Words from the meeting's title (folder "2026-10-20_1030 - Progres & Brainstorming Konten AI"): they are
    usually what the meeting is about, so Whisper should spell them right."""
    _, sep, title = meeting_dir.name.partition(" - ")
    if not sep:
        return []
    return [w for w in re.findall(r"[^\W\d_][\w-]*", title) if len(w) > 2 and w.lower() not in TITLE_STOP]


def fmt_ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def label_speakers(segs: list[dict], turns: list, offset: float, base: str) -> list[dict]:
    """Rename `base` to "<base> 1", "<base> 2"... by who talks most during each segment. turns: (start, end, speaker)
    in track time (seconds from the start of the track); the segments' times also include the track's offset."""
    if len({s for _, _, s in turns}) < 2:
        return segs  # one voice: no point numbering it
    for seg in segs:
        a, b = seg["start"] - offset, seg["end"] - offset
        heard: dict[int, float] = {}
        for x, y, s in turns:
            if y - x < MIN_TURN_S:
                continue
            o = min(y, b) - max(x, a)
            if o > 0:
                heard[s] = heard.get(s, 0.0) + o
        if heard:
            who = max(heard, key=heard.get)
        else:  # the turn finder missed this one: take the nearest speaker if it is close, else leave it unnumbered
            near = min(turns, key=lambda t: max(t[0] - b, a - t[1], 0.0))
            who = near[2] if max(near[0] - b, a - near[1], 0.0) <= 2.0 else None
        if who is not None:
            seg["speaker"] = f"{base} {who + 1}"
    return segs


def mute_events(muted: dict, duration: float) -> list:
    """Transcript lines marking when a track was muted, so a silent stretch is not mistaken for a recording fault."""
    events = []
    for label, spans in muted.items():
        for a, b in spans:
            events.append((a, f"[{fmt_ts(a)}] ({label} muted)"))
            if b < duration - 0.5:  # a mute that lasted to the end has no unmute
                events.append((b, f"[{fmt_ts(b)}] ({label} unmuted)"))
    return events


def pause_events(paused: list, duration: float) -> list:
    """Transcript lines at each pause (the audio has no gap there: the time was not recorded)."""
    events = []
    for p in paused:
        at = float(p["at"])
        events.append((at, f"[{fmt_ts(at)}] (Recording paused)"))
        if at < duration - 0.5:  # a pause that lasted to the end has no resume
            events.append((at, f"[{fmt_ts(at)}] (Recording resumed)"))
    return events
