"""Local transcription with mlx-whisper (Apple Silicon). Mic = "Me", system audio = "Others"."""

import difflib
import json
import re
import struct
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from .audio import find_track
from .progress import SPEAKERS_FAILED, downloading_whisper, speakers_found, transcribing

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
MIN_VOICED = 0.3  # a segment whose audio is mostly below the speech threshold was made up from silence
MAX_COMPRESSION = 2.4  # above this the text is a repetition loop ("eh eh eh ...")
# What Whisper "hears" in silence (YouTube outros). Only dropped when the segment is also weak.
PHANTOM = re.compile(
    r"^(terima kasih( (telah|sudah) menonton)?|thank you( for watching)?|thanks for watching"
    r"|sampai jumpa( lagi| di video (selanjutnya|berikutnya))?|bye[- ]?bye|subtitles? by .*|subtitle oleh .*|amara\.org.*)[\s.!]*$",
    re.I,
)
LEADING_HAI = re.compile(r"^hai[,.]?\s+", re.I)  # weak Indonesian bursts often get a spurious "Hai" in front
TITLE_STOP = {"dan", "yang", "untuk", "dengan", "and", "the", "for", "with", "of", "to", "in"}
ECHO_WINDOW_S = 3.0  # how far around a mic segment to look for the same words on the system track
ECHO_COVER = 0.7  # share of a mic segment's content words that the system track said around then
MERGE_GAP_S = 1.0
MERGE_MAX_CHARS = 400
MERGE_MAX_S = 30.0
MODEL_STALL_S = 300  # no new bytes for this long = the download is stuck (the first bytes can take a minute)
MODEL_WEIGHTS = ("weights.safetensors", "weights.npz", "model.safetensors")
MIN_TURN_S = 0.5  # shorter diarization turns are too unreliable to vote on who spoke


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


def muted_frames(n: int, offset: float, spans: list) -> np.ndarray:
    """Boolean mask over envelope frames that fall inside the muted spans (seconds from the recording origin)."""
    mask = np.zeros(n, dtype=bool)
    for a, b in spans:
        mask[max(round((a - offset) * FPS), 0) : max(round((b - offset) * FPS), 0)] = True
    return mask


def speech_threshold(env: np.ndarray, muted: np.ndarray | None = None) -> float | None:
    """Loudness above digital silence / room noise, or None if there is nothing to judge by."""
    live = env if muted is None else env[~muted[: len(env)]]  # muted frames are exact zeros: they say nothing about room noise
    if live.size == 0:
        return None
    return max(0.004, 3 * float(np.percentile(live, 10)))


def speech_regions(env: np.ndarray, thr: float | None) -> list[tuple[int, int]]:
    """Bursts of speech as (start_frame, end_frame). Each is transcribed on its own so Whisper never
    has to track long silences (a meeting track is mostly silence): timestamps stay exact, nothing is
    lost at 30 s window edges, and the language is detected per burst."""
    if thr is None:
        return []
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
    sys_thr = speech_threshold(sys_env) or 1
    best, lag_max = 0.0, 15  # ±0.3 s for delay between the two capture paths
    for lag in range(-lag_max, lag_max + 1):
        lo, hi = a + lag, b + lag
        if lo < 0 or hi > len(sys_env):
            continue
        s = sys_env[lo:hi]
        if (s > sys_thr).mean() < 0.5 or s.std() < 1e-6:  # no system speech to bleed from
            continue
        best = max(best, float(np.corrcoef(m, s)[0, 1]))
    return best > ECHO_CORR


def _prompt(vocab: list[str]) -> str | None:
    """A natural code-switched sentence primes Whisper better than a bare list, and keeps its style."""
    if not vocab:
        return None
    terms = ""
    for v in vocab:
        if len(terms) + len(v) > 600:  # Whisper's prompt window is ~224 tokens
            break
        terms += (", " if terms else "") + v
    return f"Diskusi tim dalam bahasa Indonesia dan English. Nama dan istilah: {terms}."


def _run(mlx_whisper, audio, cfg, language):
    return mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=cfg["model"],
        language=language,
        initial_prompt=_prompt(cfg["vocabulary"]),
        condition_on_previous_text=False,
        verbose=None,
    )


def voiced_fraction(env: np.ndarray, thr: float, start: float, end: float) -> float:
    """Share of the envelope frames between start and end (frames, possibly fractional) that are above thr."""
    a = max(int(start), 0)
    b = min(max(int(end), a + 1), len(env))
    return float((env[a:b] > thr).mean()) if b > a else 0.0


def clean_segment(seg: dict, voiced: float = 1.0) -> str | None:
    """The text to keep for a Whisper segment, or None if it is a hallucination. `voiced` is how much of the
    segment's audio is actually above the speech threshold."""
    text = seg["text"].strip()
    if not text:
        return None
    lp, ns = seg.get("avg_logprob", 0), seg.get("no_speech_prob", 0)
    if ns > 0.6 and lp < -1.0:  # classic silence hallucination
        return None
    if seg.get("compression_ratio", 0) > MAX_COMPRESSION or voiced < MIN_VOICED:
        return None
    if PHANTOM.match(text) and (voiced < 0.6 or lp < -0.5 or ns > 0.3):
        return None
    if lp < -0.7:
        text = LEADING_HAI.sub("", text) or text
    return text


def _confidence(res: dict) -> float:
    lps = [s.get("avg_logprob", 0) for s in res["segments"]]
    return sum(lps) / len(lps) if lps else -9.0


def _dominant(votes: dict) -> str | None:
    """The language that clearly carries this track so far (else None)."""
    total = sum(votes.values())
    if total < 5:
        return None
    lang, n = max(votes.items(), key=lambda kv: kv[1])
    return lang if n / total >= 0.8 else None


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
            votes = state.setdefault("votes", {})
            main = _dominant(votes)
            votes[det] = votes.get(det, 0) + 1
            if main and det != main and _confidence(res) < -0.9:
                # a shaky "other language" guess on a track that is almost all one language: retry in that one
                retry = _run(mlx_whisper, piece, cfg, main)
                if _confidence(retry) > _confidence(res):
                    res = retry
    return res


def transcribe_regions(audio, regions, cfg, offset, speaker, progress=lambda f: None, env=None, thr=None):
    import mlx_whisper

    out, state = [], {}
    for n, (a, b) in enumerate(regions):
        progress(n / len(regions))
        t0 = a * FRAME
        res = _transcribe_region(mlx_whisper, audio[t0 : b * FRAME], cfg, state)
        for seg in res["segments"]:
            voiced = voiced_fraction(env, thr, a + seg["start"] * FPS, a + seg["end"] * FPS) if env is not None and thr else 1.0
            text = clean_segment(seg, voiced)
            if text:
                out.append(
                    {
                        "start": round(t0 / SR + seg["start"] + offset, 2),
                        "end": round(t0 / SR + seg["end"] + offset, 2),
                        "speaker": speaker,
                        "text": text,
                    }
                )
    progress(1.0)
    return out


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


def unmuted(audio: np.ndarray, offset: float, spans: list) -> np.ndarray:
    """The audio outside the muted spans. Spans are in seconds from the recording origin; the track starts at `offset`."""
    keep = np.ones(len(audio), dtype=bool)
    for a, b in spans:
        keep[max(round((a - offset) * SR), 0) : max(round((b - offset) * SR), 0)] = False
    return audio[keep]


def _has_weights(folder: Path) -> bool:
    return any((folder / w).is_file() and (folder / w).stat().st_size > 0 for w in MODEL_WEIGHTS)


def ensure_model(model: str, progress=lambda msg: None) -> None:
    """Check that the Whisper model is on this Mac and complete, downloading it if not. Without this a broken
    cache (a download that was interrupted, leaving empty weight files) makes the first transcription hang."""
    local = Path(model).expanduser()
    if local.exists():
        if not _has_weights(local):
            raise RuntimeError(f"The Whisper model folder {local} has no weights file ({', '.join(MODEL_WEIGHTS)}).")
        return
    from huggingface_hub import constants, snapshot_download

    try:
        if _has_weights(Path(snapshot_download(model, local_files_only=True))):
            return
    except Exception:
        pass  # not (completely) downloaded yet
    progress(downloading_whisper(0))
    result: dict = {}

    def fetch():
        try:
            result["path"] = snapshot_download(model)
        except Exception as e:
            result["error"] = e

    t = threading.Thread(target=fetch, name="model-download", daemon=True)
    t.start()
    blobs = Path(constants.HF_HUB_CACHE) / ("models--" + model.replace("/", "--")) / "blobs"
    last, moved = -1, time.monotonic()
    while t.is_alive():
        t.join(2)
        size = sum(f.stat().st_size for f in blobs.glob("*") if f.is_file()) if blobs.exists() else 0
        if size != last:
            last, moved = size, time.monotonic()
            progress(downloading_whisper(size // 2**20))
        elif time.monotonic() - moved > MODEL_STALL_S:
            raise RuntimeError(
                f"Downloading the Whisper model {model} is stuck at {size // 2**20} MB. Check your internet connection, "
                f"delete the folder {blobs.parent} and try again (or pick another model in Settings)."
            )
    if "error" in result:
        raise RuntimeError(f"Could not download the Whisper model {model} ({type(result['error']).__name__}): {str(result['error'])[:150]}")
    if not _has_weights(Path(result["path"])):
        raise RuntimeError(f"The Whisper model {model} downloaded without a weights file; it may not be an MLX Whisper model.")


def transcribe_meeting(meeting_dir: Path, cfg: dict, progress=lambda msg: None) -> Path:
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
        )
    segs[mine] = drop_echo(segs[mine], segs[others])
    if cfg.get("diarize") and others in tracks and segs[others]:
        try:
            from . import diarize

            if not diarize.available():
                raise RuntimeError("the sherpa-onnx package is not installed (pip install sherpa-onnx)")
            turns = diarize.diarize(tracks[others][0], cfg.get("diarize_speakers", 0), progress)
            segs[others] = label_speakers(segs[others], turns, tracks[others][1], others)
            count = len({s["speaker"] for s in segs[others]})
            progress(speakers_found(others, count))
        except Exception as e:  # never lose the transcript because speakers could not be separated
            msg = f"{SPEAKERS_FAILED}{str(e)[:150]}"
            notes.append(f"⚠ {msg}")
            progress(msg)
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
