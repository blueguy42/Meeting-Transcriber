"""Transcribing a whole meeting: both tracks, echo, speakers, screenshots and the markdown transcript. Mic = "Me", system audio = "Others"."""

import json
import re
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np

from ..audio import find_track
from ..control import Stopped
from ..progress import SPEAKERS_FAILED, speakers_found, transcribing
from .analysis import FPS, SILENT_RMS, SR, align, envelope, is_echo, load_wav, muted_frames, speech_regions, speech_threshold, unmuted
from .checkpoint import Checkpoint
from .decoding import transcribe_regions
from .models import ensure_model
from .segments import drop_echo, fmt_ts, label_speakers, merge_segments, mute_events, pause_events, title_terms


def transcript_file(meeting_dir: Path) -> Path:
    legacy = meeting_dir / "transcript" / "transcript.md"  # older meetings
    new = meeting_dir / "transcript.md"
    return legacy if legacy.exists() and not new.exists() else new


def transcribe_meeting(meeting_dir: Path, cfg: dict, progress=lambda msg: None) -> Path:
    """Transcribe both tracks (and tell the speakers apart). A pause leaves a checkpoint (see Checkpoint) that the next
    call continues from; a finished transcription removes it."""
    ck = Checkpoint(meeting_dir)
    try:
        out = _transcribe_meeting(meeting_dir, cfg, progress, ck)
    except Stopped:
        ck.save()  # (the app deletes it when the job is cancelled rather than paused)
        raise
    ck.clear()
    return out


def tell_speakers_apart(segs: list, audio: np.ndarray, offset: float, others: str, cfg: dict, progress, notes: list) -> list:
    """Give each segment of the other people's track its speaker ("Others 1", "Others 2"...). A failure never loses the transcript:
    the segments come back unchanged and a note says why. A pause or cancel (Stopped) is not a failure: it goes on up."""
    try:
        from . import diarize

        if not diarize.available():
            raise RuntimeError("the sherpa-onnx package is not installed (pip install sherpa-onnx)")
        turns = diarize.diarize(audio, cfg.get("diarize_speakers", 0), progress)
        out = label_speakers(segs, turns, offset, others)
        progress(speakers_found(others, len({s["speaker"] for s in out})))
        return out
    except Stopped:
        raise
    except Exception as e:  # never lose the transcript because speakers could not be separated
        msg = f"{SPEAKERS_FAILED}{str(e)[:150]}"
        notes.append(f"⚠ {msg}")
        progress(msg)
        return segs


def _transcribe_meeting(meeting_dir: Path, cfg: dict, progress, ck: Checkpoint) -> Path:
    meta = json.loads((meeting_dir / "meta.json").read_text())
    mine, others = cfg["my_label"], cfg["others_label"]
    ensure_model(cfg["model"], progress)
    tracks: dict[str, tuple[np.ndarray, float]] = {}
    notes: list[str] = []
    muted = {mine: meta.get("mic_muted") or [], others: meta.get("system_muted") or []}
    with tempfile.TemporaryDirectory() as tmp:
        for fname, label, key in (("mic", mine, "mic_offset"), ("system", others, "system_offset")):
            p = find_track(meeting_dir, fname, Path(tmp)) if meta.get(key) is not None else None
            if p is None:
                continue
            audio = load_wav(p)
            heard = unmuted(audio, meta[key], muted[label])  # silence you chose is not a permission problem
            if muted[label] and len(heard) < SR:
                notes.append(f"ℹ {label} was muted for the whole recording")
            elif len(heard) == 0 or np.sqrt((heard**2).mean()) < SILENT_RMS:
                msg = f"{label} audio was silent - check the Microphone / Screen Recording permission"
                notes.append(f"⚠ {msg}")
                progress(msg)
            tracks[label] = (audio, meta[key])

    known = {v.lower() for v in cfg["vocabulary"]}
    cfg = {**cfg, "vocabulary": list(cfg["vocabulary"]) + [w for w in title_terms(meeting_dir) if w.lower() not in known]}

    envs = {label: envelope(audio) for label, (audio, _) in tracks.items()}
    thrs = {
        label: speech_threshold(env, muted_frames(len(env), tracks[label][1], muted[label]) if muted[label] else None)
        for label, env in envs.items()
    }
    regions = {label: speech_regions(env, thrs[label]) for label, env in envs.items()}
    if mine in tracks and others in tracks:
        # each track starts at its own offset; put the system envelope on the mic's frame grid
        sys_env = align(envs[others], round((tracks[mine][1] - tracks[others][1]) * FPS))
        kept = [(a, b) for a, b in regions[mine] if not is_echo(envs[mine], sys_env, a, b)]
        if len(kept) < len(regions[mine]):
            progress(f"Ignored {len(regions[mine]) - len(kept)} mic burst(s) that were speaker echo")
        regions[mine] = kept

    segs: dict[str, list[dict]] = {mine: [], others: []}
    for label, (audio, offset) in tracks.items():
        progress(transcribing(label, bursts=len(regions[label])))
        segs[label] = transcribe_regions(
            audio,
            regions[label],
            cfg,
            offset,
            label,
            lambda f, label=label: progress(transcribing(label, f)),
            env=envs[label],
            thr=thrs[label],
            ck=ck,
        )
    segs[mine] = drop_echo(segs[mine], segs[others])
    if cfg.get("diarize") and others in tracks and segs[others]:
        segs[others] = tell_speakers_apart(segs[others], tracks[others][0], tracks[others][1], others, cfg, progress, notes)
    merged = merge_segments(sorted(segs[mine] + segs[others], key=lambda s: s["start"]))

    shots = []
    for p in sorted((meeting_dir / "shots").glob("shot_*.jpg")):
        m = re.match(r"shot_(\d+)", p.stem)
        shots.append({"start": max(int(m.group(1)) - meta.get("origin_delay", 0), 0), "shot": f"shots/{p.name}"})

    events = sorted(
        [(s["start"], f"[{fmt_ts(s['start'])}] {s['speaker']}: {s['text']}") for s in merged]
        + [(s["start"], f"[{fmt_ts(s['start'])}] (screenshot: {s['shot']})") for s in shots]
        + mute_events(muted, meta["duration"])
        + pause_events(meta.get("paused") or [], meta["duration"]),
        key=lambda e: e[0],
    )
    out = meeting_dir / "transcript.md"
    header = "".join(f"> {n}\n" for n in notes)
    text = (header + f"# Transcript ({datetime.fromisoformat(meta['started']):%A %Y-%m-%d %H:%M}, {meta['duration'] / 60:.0f} min)\n\n" + "\n".join(e[1] for e in events) + "\n")
    tmp = out.with_suffix(".md.tmp")  # the old transcript, if any, is replaced in one step, only now that everything is done
    tmp.write_text(text)
    tmp.replace(out)
    return out
