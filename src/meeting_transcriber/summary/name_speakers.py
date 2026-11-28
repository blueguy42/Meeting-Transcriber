"""Names for the other speakers, from the transcript: Claude reads it (text only, no audio) and names the speakers it has proof for.

Proof means a line of the transcript that shows the name: a self-introduction, being addressed and answering, a roll call.
Whatever Claude returns is checked here before it is used: the proof must really be in the transcript, no two speakers may get the
same name (unless Claude says they are one person), and the label of the user's own microphone is never renamed.
"""

import json
import os
import re

from ..meetings import speakers
from ..meetings.meeting import Meeting
from ..settings import config
from ..settings.model import Settings
from ..transcription.segments import title_terms
from .summarize import _run_claude, notes_block

PROMPT = """You are given the transcript of a meeting. Lines look like `[hh:mm:ss] Speaker: text`.
"{mine}" is the person who recorded the meeting. The other people are labelled "{others} 1", "{others} 2" and so on by a program that
tells voices apart. Your job: work out the real name of a labelled speaker, but ONLY when the transcript itself proves it. Proof is
one of these:
- the speaker introduces themselves ("Budi here", "this is Dina from finance");
- someone addresses a name ("Dina, over to you", "thanks, Budi") and the very next speaker answers as that person;
- a roll call where each person says their name in turn.
Names that are only listed below (people who may be in the meeting) help you choose between similar spellings; they are never proof
on their own. If you are not sure, leave the speaker out. Never rename "{mine}". Never give two labels the same name unless they are
clearly the same person (then list them under "same").

Answer with ONLY this JSON, nothing else:
{{"speakers": [{{"label": "{others} 1", "name": "Budi", "evidence": "<a short piece copied exactly from one transcript line that proves it>"}}],
  "same": [["{others} 1", "{others} 4"]],
  "people": ["Budi", "Dina", "Rina"]}}
"people" lists every PERSON named in the transcript, spelled as in the transcript (not products, companies, places or other terms), including people who
are mentioned but not speaking. It may be empty.

"""


def _context(meeting: Meeting, cfg: Settings) -> str:
    terms = list(dict.fromkeys([*cfg.vocabulary, *title_terms(meeting)]))
    out = ""
    if terms:
        out += "People or terms that may come up in this meeting: " + ", ".join(terms) + "\n\n"
    out += notes_block(meeting, cfg)[:3000]
    return out


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def check(reply: str, meeting: Meeting, cfg: Settings) -> dict[str, dict]:
    """Claude's answer -> {raw label: {"name", "by": "claude", "evidence", "at"}} for the speakers that pass every check."""
    match = re.search(r"\{.*\}", reply, flags=re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group())
    except ValueError:
        return {}
    spoken = speakers.lines(meeting)
    labels = set(speakers.raw_labels(meeting, cfg))
    text_by_line = [(t, _norm(txt)) for t, _s, txt in spoken]
    found: dict[str, dict] = {}
    if not isinstance(data, dict) or not isinstance(data.get("speakers"), list):
        return {}
    for item in data["speakers"]:
        if not isinstance(item, dict):
            continue
        label, name, evidence = (str(item.get(k, "")).strip() for k in ("label", "name", "evidence"))
        try:
            name = speakers.clean_name(name, cfg)
        except ValueError:
            continue
        ev = _norm(evidence)
        at = next((t for t, line in text_by_line if len(ev) >= 8 and ev in line), None)
        if label not in labels or not name or at is None:
            continue  # unknown speaker, no name, or the proof is not in the transcript
        found[label] = {"name": name, "by": "claude", "evidence": evidence[:120], "at": f"{int(at) // 60:02d}:{int(at) % 60:02d}"}
    same = [[x for x in group if x in found] for group in (data.get("same") if isinstance(data.get("same"), list) else []) if isinstance(group, list)]
    merged = {x for group in same if len(group) > 1 and len({found[x]["name"].lower() for x in group}) == 1 for x in group}
    count: dict[str, int] = {}
    for e in found.values():
        count[e["name"].lower()] = count.get(e["name"].lower(), 0) + 1
    return {label: e for label, e in found.items() if count[e["name"].lower()] == 1 or label in merged}  # the same name twice: neither is used


def people(reply: str, meeting: Meeting, cfg: Settings) -> list[str]:
    """The names of people Claude saw mentioned, for suggestions: only ones that really appear in the transcript, at most 30."""
    match = re.search(r"\{.*\}", reply, flags=re.S)
    try:
        listed = json.loads(match.group()).get("people") if match else None
    except (ValueError, AttributeError):
        return []
    if not isinstance(listed, list):
        return []
    text = " " + _norm(" ".join(t for _t, _s, t in speakers.lines(meeting))) + " "
    out = []
    for raw in listed:
        try:
            name = speakers.clean_name(str(raw), cfg)
        except ValueError:
            continue
        if name and f" {_norm(name)} " in text and name not in out:
            out.append(name)
    return out[:30]


def name_speakers(meeting: Meeting, cfg: Settings) -> dict[str, dict]:
    """Ask Claude, keep what checks out, write it to meta.json and transcript.md. -> what was named. Raises if Claude cannot be run."""
    claude = config.find_claude(cfg)
    if not claude:
        raise RuntimeError("Claude Code was not found")
    labels = speakers.raw_labels(meeting, cfg)
    if not labels:
        return {}
    stdin = PROMPT.format(mine=cfg.my_label, others=cfg.others_label) + _context(meeting, cfg) + "Transcript:\n" + meeting.transcript_file.read_text()
    cmd = [claude, "-p"]  # text in, text out: no tool is allowed, so nothing in the transcript can make it read or run anything
    if cfg.claude_model:
        cmd += ["--model", cfg.claude_model]
    env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/opt/homebrew/bin:/usr/local/bin"}
    r = _run_claude(cmd, stdin, meeting.folder, env, timeout=600)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"claude failed ({r.returncode}): {(r.stderr.strip() or r.stdout.strip())[:120]}")
    named = check(r.stdout, meeting, cfg)
    if named:
        speakers.set_names(meeting, cfg, named)
    speakers.mark_checked(meeting, people(r.stdout, meeting, cfg))
    return named
