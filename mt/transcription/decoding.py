"""Turning speech bursts into text with mlx-whisper: language handling, hallucination filters, and the burst loop (with its checkpoint)."""

import re

import numpy as np

from ..control import Stopped
from .analysis import FPS, FRAME, SR, voiced_fraction
from .checkpoint import Checkpoint


SHORT_REGION_S = 4.0  # too short to detect the language reliably: reuse the track's last language
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


def transcribe_regions(audio, regions, cfg, offset, speaker, progress=lambda f: None, env=None, thr=None, ck: Checkpoint | None = None):
    import mlx_whisper

    fp = Checkpoint.fingerprint(cfg, audio, regions) if ck is not None else ""
    start, out, state = ck.resume(speaker, fp) if ck is not None else (0, [], {})
    for n, (a, b) in enumerate(regions):
        if n < start:
            continue  # done before the pause
        try:
            progress(n / len(regions))
        except Stopped:
            if ck is not None:
                ck.update(speaker, fp, n, out, state)
                ck.save()  # bursts before this one are done: Resume continues here
            raise
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
        if ck is not None:
            ck.update(speaker, fp, n + 1, out, state)
    progress(1.0)
    return out
