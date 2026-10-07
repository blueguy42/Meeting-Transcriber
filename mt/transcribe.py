"""Local transcription with mlx-whisper (Apple Silicon). Mic = "Me", system audio = "Others"."""

import difflib
import json
import re
import struct
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np

from .audio import find_track

SR = 16000
SILENT_RMS = 0.003
FPS = 50  # envelope frames per second (20 ms)
FRAME = SR // FPS
MIN_GAP_S = 1.5  # a pause shorter than this stays inside one speech region
PAD_S = 0.3
MAX_REGION_S = 28.0  # Whisper decodes 30 s windows; longer speech is cut at its quietest point
SHORT_REGION_S = 4.0  # too short to detect the language reliably: reuse the track's last language
ECHO_CORR = 0.6  # envelope correlation between mic and system audio that means "speaker bleed"
# Whisper often labels Indonesian as these close relatives.
LANG_MAP = {"ms": "id", "jw": "id", "su": "id"}


def transcript_file(meeting_dir: Path) -> Path:
    legacy = meeting_dir / "transcript" / "transcript.md"  # older meetings
    new = meeting_dir / "transcript.md"
    return legacy if legacy.exists() and not new.exists() else new


def load_wav(path: Path) -> np.ndarray:
    """Mono float32 @16 kHz. Parses the header by hand so recordings that were never
    finalised (app crash, force-quit) still load, and resamples if the source rate differs."""
    raw = path.read_bytes()
    pos, fmt, data = 12, None, b""
    while pos + 8 <= len(raw):
        cid, size = raw[pos : pos + 4], int.from_bytes(raw[pos + 4 : pos + 8], "little")
        body = pos + 8
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", raw[body : body + 16])  # tag, ch, rate, _, _, bits
        elif cid == b"data":
            end = body + size
            if size == 0 or end > len(raw):
                end = len(raw)  # unfinalised header: read to end of file
            data = raw[body:end]
            break
        pos = body + size + (size & 1)
    if fmt is None:
        raise ValueError(f"{path.name}: not a WAV file")
    tag, ch, rate, _, _, bits = fmt
    if bits == 16:
        x = np.frombuffer(data[: len(data) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
    elif bits == 32 and tag == 3:
        x = np.frombuffer(data[: len(data) // 4 * 4], dtype=np.float32).copy()
    else:
        raise ValueError(f"{path.name}: unsupported WAV format (tag={tag}, bits={bits})")
    if ch > 1:
        x = x[: len(x) // ch * ch].reshape(-1, ch).mean(axis=1)
    if rate != SR and len(x):
        n = int(len(x) * SR / rate)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    return x


def envelope(audio: np.ndarray) -> np.ndarray:
    n = len(audio) // FRAME
    return np.sqrt((audio[: n * FRAME].reshape(n, FRAME) ** 2).mean(axis=1))


def speech_regions(env: np.ndarray) -> list[tuple[int, int]]:
    """Bursts of speech as (start_frame, end_frame). Each is transcribed on its own so Whisper never
    has to track long silences (a meeting track is mostly silence): timestamps stay exact, nothing is
    lost at 30 s window edges, and the language is detected per burst."""
    if env.size == 0:
        return []
    thr = max(0.004, 3 * float(np.percentile(env, 10)))  # above digital silence / room noise
    idx = np.flatnonzero(env > thr)
    if idx.size == 0:
        return []
    runs, start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > MIN_GAP_S * FPS:
            runs.append((start, prev))
            start = i
        prev = i
    runs.append((start, prev))

    out = []
    for s, e in runs:
        if e - s + 1 < 0.3 * FPS:  # a click or cough
            continue
        a, b = max(0, s - int(PAD_S * FPS)), min(len(env), e + 1 + int(PAD_S * FPS))
        while b - a > MAX_REGION_S * FPS:
            lo, hi = a + int((MAX_REGION_S - 8) * FPS), a + int(MAX_REGION_S * FPS)
            cut = lo + int(np.argmin(env[lo:hi]))
            out.append((a, cut))
            a = cut
        out.append((a, b))
    return out


def align(env: np.ndarray, shift: int) -> np.ndarray:
    """env re-indexed so that out[i] == env[i + shift] (zero-filled where the other track has no audio)."""
    return np.concatenate([np.zeros(max(-shift, 0)), env[max(shift, 0) :]])


def is_echo(mic_env: np.ndarray, sys_env: np.ndarray, a: int, b: int) -> bool:
    """True if the mic burst just mirrors the system audio (speakers bleeding into the mic).
    Compares loudness envelopes, so it works whatever the room does to the sound and whatever
    language Whisper would have made of it. Real double-talk has an unrelated envelope."""
    m = mic_env[a:b]
    if len(m) < 10 or m.std() < 1e-6:
        return False
    sys_thr = max(0.004, 3 * float(np.percentile(sys_env, 10))) if sys_env.size else 1
    best, lag_max = 0.0, 15  # ±0.3 s for delay between the two capture paths
    for lag in range(-lag_max, lag_max + 1):
        lo, hi = a + lag, b + lag
        if lo < 0 or hi > len(sys_env):
            continue
        s = sys_env[lo:hi]
        if (s > sys_thr).mean() < 0.6 or s.std() < 1e-6:  # no system speech to bleed from
            continue
        best = max(best, float(np.corrcoef(m, s)[0, 1]))
    return best > ECHO_CORR


def _run(mlx_whisper, audio, cfg, language):
    vocab = cfg["vocabulary"]
    return mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=cfg["model"],
        language=language,
        initial_prompt=("Names and terms: " + ", ".join(vocab) + ".") if vocab else None,
        condition_on_previous_text=False,
        verbose=None,
    )


def _keep(seg) -> bool:
    text = seg["text"].strip()
    if not text:
        return False
    # classic silence hallucinations
    return not (seg.get("no_speech_prob", 0) > 0.6 and seg.get("avg_logprob", 0) < -1.0)


def _transcribe_region(mlx_whisper, piece: np.ndarray, cfg: dict, state: dict):
    lang, allowed = cfg["language"], cfg["allowed_languages"]
    if lang != "auto":
        return _run(mlx_whisper, piece, cfg, lang)
    short = len(piece) < SHORT_REGION_S * SR
    hint = state.get("lang") if short else None  # trust the previous burst over a 2-second guess
    res = _run(mlx_whisper, piece, cfg, hint)
    if hint is None:
        det = LANG_MAP.get(res.get("language"), res.get("language"))
        if det not in allowed or det != res.get("language"):
            det = det if det in allowed else (state.get("lang") or allowed[0])
            res = _run(mlx_whisper, piece, cfg, det)
        if not short:
            state["lang"] = det
    return res


def transcribe_regions(audio, regions, cfg, offset, speaker, progress=lambda f: None):
    import mlx_whisper

    out, state = [], {}
    for n, (a, b) in enumerate(regions):
        progress(n / len(regions))
        t0 = a * FRAME
        res = _transcribe_region(mlx_whisper, audio[t0 : b * FRAME], cfg, state)
        for seg in res["segments"]:
            if _keep(seg):
                out.append(
                    {
                        "start": round(t0 / SR + seg["start"] + offset, 2),
                        "end": round(t0 / SR + seg["end"] + offset, 2),
                        "speaker": speaker,
                        "text": seg["text"].strip(),
                    }
                )
    progress(1.0)
    return out


def drop_echo(mine: list[dict], others: list[dict]) -> list[dict]:
    """Without headphones the mic hears the speakers; drop mic segments that duplicate system audio."""
    kept = []
    for m in mine:
        dup = any(
            o["start"] < m["end"] + 1
            and o["end"] > m["start"] - 1
            and difflib.SequenceMatcher(None, m["text"].lower(), o["text"].lower()).ratio() > 0.6
            for o in others
        )
        if not dup:
            kept.append(m)
    return kept


def fmt_ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def transcribe_meeting(meeting_dir: Path, cfg: dict, progress=lambda msg: None) -> Path:
    meta = json.loads((meeting_dir / "meta.json").read_text())
    mine, others = cfg["my_label"], cfg["others_label"]
    tracks: dict[str, tuple[np.ndarray, float]] = {}
    warnings: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for fname, label, key in (("mic", mine, "mic_offset"), ("system", others, "system_offset")):
            p = find_track(meeting_dir, fname, Path(tmp)) if meta.get(key) is not None else None
            if p is None:
                continue
            audio = load_wav(p)
            if len(audio) == 0 or np.sqrt((audio**2).mean()) < SILENT_RMS:
                msg = f"{label} audio was silent - check the Microphone / Screen Recording permission"
                warnings.append(msg)
                progress(msg)
            tracks[label] = (audio, meta[key])

    envs = {label: envelope(audio) for label, (audio, _) in tracks.items()}
    regions = {label: speech_regions(env) for label, env in envs.items()}
    if mine in tracks and others in tracks:
        # each track starts at its own offset; put the system envelope on the mic's frame grid
        sys_env = align(envs[others], round((tracks[mine][1] - tracks[others][1]) * FPS))
        kept = [(a, b) for a, b in regions[mine] if not is_echo(envs[mine], sys_env, a, b)]
        if len(kept) < len(regions[mine]):
            progress(f"Ignored {len(regions[mine]) - len(kept)} mic burst(s) that were speaker echo")
        regions[mine] = kept

    segs: dict[str, list[dict]] = {mine: [], others: []}
    for label, (audio, offset) in tracks.items():
        progress(f"Transcribing {label} ({len(regions[label])} speech bursts)…")
        segs[label] = transcribe_regions(
            audio, regions[label], cfg, offset, label, lambda f, label=label: progress(f"Transcribing {label}… {f:.0%}")
        )
    segs[mine] = drop_echo(segs[mine], segs[others])
    merged = sorted(segs[mine] + segs[others], key=lambda s: s["start"])

    shots = []
    for p in sorted((meeting_dir / "shots").glob("shot_*.jpg")):
        m = re.match(r"shot_(\d+)", p.stem)
        shots.append({"start": max(int(m.group(1)) - meta.get("origin_delay", 0), 0), "shot": f"shots/{p.name}"})

    events = sorted(
        [(s["start"], f"[{fmt_ts(s['start'])}] {s['speaker']}: {s['text']}") for s in merged]
        + [(s["start"], f"[{fmt_ts(s['start'])}] (screenshot: {s['shot']})") for s in shots],
        key=lambda e: e[0],
    )
    out = meeting_dir / "transcript.md"
    notes = "".join(f"> ⚠ {w}\n" for w in warnings)
    out.write_text(notes + f"# Transcript ({datetime.fromisoformat(meta['started']):%A %Y-%m-%d %H:%M}, {meta['duration'] / 60:.0f} min)\n\n" + "\n".join(e[1] for e in events) + "\n")
    return out
