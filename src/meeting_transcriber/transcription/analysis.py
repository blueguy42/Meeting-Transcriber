"""Looking at the audio itself: loading a recording, its loudness envelope, where the speech is, and speaker echo on the mic."""

import struct
from pathlib import Path

import numpy as np


SR = 16000
SILENT_RMS = 0.003
FPS = 50  # envelope frames per second (20 ms)
FRAME = SR // FPS
MIN_GAP_S = 1.5  # a pause shorter than this stays inside one speech region
PAD_S = 0.3
MAX_REGION_S = 28.0  # Whisper decodes 30 s windows; longer speech is cut at its quietest point
ECHO_CORR = 0.6  # envelope correlation between mic and system audio that means "speaker bleed"


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


def voiced_fraction(env: np.ndarray, thr: float, start: float, end: float) -> float:
    """Share of the envelope frames between start and end (frames, possibly fractional) that are above thr."""
    a = max(int(start), 0)
    b = min(max(int(end), a + 1), len(env))
    return float((env[a:b] > thr).mean()) if b > a else 0.0


def unmuted(audio: np.ndarray, offset: float, spans: list) -> np.ndarray:
    """The audio outside the muted spans. Spans are in seconds from the recording origin; the track starts at `offset`."""
    keep = np.ones(len(audio), dtype=bool)
    for a, b in spans:
        keep[max(round((a - offset) * SR), 0) : max(round((b - offset) * SR), 0)] = False
    return audio[keep]
